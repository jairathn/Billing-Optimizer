"""
The hook-in unit (push 2's phase 3b): the self-running modules (remitwatch.archive, selfcheck, runlog, repair, diagnose)
wired into both daily cycles, and the store held in memory without SQLite's 1,024 MB ceiling. Invented data only.
"""
from __future__ import annotations

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch.store import Store, ddl  # noqa: E402


# ======================================================================================================================
# 1. A store held in memory has no 1,024 MB ceiling (Store.from_bytes), and saving it costs no third copy (Store.image)
# ======================================================================================================================
def _image(rows: int = 50, page_size: int | None = None) -> bytes:
    """A small store image with `rows` invented meta rows, as a serverless run saves it."""
    c = sqlite3.connect(":memory:")
    if page_size:
        c.execute(f"PRAGMA page_size={page_size}")
    c.executescript(ddl(True))
    c.executemany("INSERT INTO meta (key, value) VALUES (?, ?)", [(f"k{i}", "v" * 100) for i in range(rows)])
    c.commit()
    data = c.serialize()
    c.close()
    return data


def _meta_rows(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM meta WHERE key LIKE 'k%'").fetchone()[0]


def test_a_store_loaded_from_its_image_is_copied_into_a_plain_in_memory_database():
    """SQLite holds a deserialized database in one block it caps at 1,024 MB (SQLITE_MEMDB_DEFAULT_MAXSIZE, which Python
    cannot raise): a run whose store grew past it failed at detection's save with "database or disk is full". The image
    is now deserialized into a source connection and copied, page by page, into a plain in-memory database (no ceiling),
    after the image's own buffer is let go; the source is closed."""
    data = bytearray(_image())
    seen: dict = {}

    def copy(src: sqlite3.Connection, dst: sqlite3.Connection) -> None:
        seen.update(src=src, dst=dst, src_rows=_meta_rows(src), image_left=len(data),
                    dst_tables=dst.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0])
        src.backup(dst)

    store = Store.from_bytes(data, copy=copy)
    assert seen["src_rows"] == 50                   # the step copies the deserialized image
    assert seen["image_left"] == 0                  # whose buffer went first: the peak stays about twice the image
    assert seen["dst_tables"] == 0                  # into a fresh database, so the image's page size is kept
    assert seen["dst"] is store.conn                # the store is the plain copy
    with pytest.raises(sqlite3.ProgrammingError):
        seen["src"].execute("SELECT 1")             # and the deserialized source is closed
    assert _meta_rows(store.conn) == 50
    store.conn.execute("INSERT INTO meta (key, value) VALUES ('grown', ?)", ("x" * 5000,))
    store.conn.commit()
    assert store.conn.execute("SELECT LENGTH(value) FROM meta WHERE key='grown'").fetchone()[0] == 5000
    store.close()


def test_an_image_with_another_page_size_loads_whole():
    """The copy goes into a database with no page yet: an image written with 8 KB pages keeps them (a plain in-memory
    destination of another page size refuses the copy)."""
    store = Store.from_bytes(bytearray(_image(page_size=8192)))
    assert store.conn.execute("PRAGMA page_size").fetchone()[0] == 8192
    assert _meta_rows(store.conn) == 50
    store.close()


def test_an_empty_image_and_bytes_load_as_before():
    """No image (a first load) is an empty store; an image given as bytes (it cannot be let go) loads all the same."""
    blank = Store.from_bytes(b"")
    assert blank.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0
    blank.close()
    store = Store.from_bytes(_image(rows=7))
    assert _meta_rows(store.conn) == 7
    store.close()


def test_saving_a_store_held_in_memory_hands_out_one_block_and_keeps_the_store():
    """Python's serialize copies a plain in-memory database twice (three times the image at the save's peak: 2,286 MB on
    a 742 MB real image against 1,487 MB before). Store.image() copies the store's pages into one deserialized block, lets
    the store's own pages go while the block's single copy is out, then fills the store again from the block. The store's
    connection is the same object throughout, whole again after."""
    store = Store.from_bytes(bytearray(_image()))
    conn = store.conn
    steps: list = []

    def copy(src: sqlite3.Connection, dst: sqlite3.Connection) -> None:
        steps.append(src is conn)
        src.backup(dst)

    with store.image(copy=copy) as view:
        data = bytes(view)
        # while the image is out the store's own pages are let go: only one copy of the store is held beside the image
        assert conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] == 0
    assert steps == [True]
    assert store.conn is conn and _meta_rows(conn) == 50
    again = Store.from_bytes(bytearray(data))
    assert _meta_rows(again.conn) == 50
    again.close()
    store.close()


def test_a_store_past_the_blocks_ceiling_is_saved_the_plain_way_and_never_emptied():
    """A store whose pages do not fit one deserialized block (past 1,024 MB SQLite answers "database or disk is full") is
    serialized the plain way: the same image, and the store is never emptied."""
    store = Store.from_bytes(bytearray(_image()))
    want = store.to_bytes()

    def full(src: sqlite3.Connection, dst: sqlite3.Connection) -> None:
        raise sqlite3.OperationalError("database or disk is full")

    with store.image(copy=full) as view:
        assert bytes(view) == want
        assert _meta_rows(store.conn) == 50
    assert _meta_rows(store.conn) == 50
    store.close()


def test_a_failure_while_the_image_is_out_leaves_the_store_whole():
    """An upload that fails while the image is out raises as before, and the store is whole again for the rest of the run
    (the dashboard's copy is never built from an emptied store)."""
    store = Store.from_bytes(bytearray(_image()))
    with pytest.raises(RuntimeError):
        with store.image():
            raise RuntimeError("the upload failed")
    assert _meta_rows(store.conn) == 50
    store.close()


def test_a_store_on_disk_is_serialized_as_before(tmp_path):
    store = Store(str(tmp_path / "s.sqlite"))
    store.conn.execute("INSERT INTO meta (key, value) VALUES ('k1', 'v')")
    store.conn.commit()
    with store.image() as view:
        data = bytes(view)
        assert _meta_rows(store.conn) == 1          # a store on disk is never emptied
    assert data[:16] == b"SQLite format 3\x00" and data == store.to_bytes()
    assert _meta_rows(store.conn) == 1
    store.close()


def test_the_serverless_save_goes_through_the_one_block_image(tmp_path, monkeypatch):
    """cloud.save_raw saves the store through Store.image (not to_bytes, which would copy a plain store twice)."""
    from remitwatch import cloud
    from remitwatch.state import LocalState
    state = LocalState(str(tmp_path / "state"))
    store = Store.from_bytes(bytearray(_image()))
    used = []
    real = Store.image

    def spy(self, **kw):
        used.append(True)
        return real(self, **kw)
    monkeypatch.setattr(Store, "image", spy)
    monkeypatch.setattr(Store, "to_bytes", lambda self: (_ for _ in ()).throw(AssertionError("to_bytes at the save")))
    meta = cloud.save_raw(state, store, log=lambda *a: None)
    assert used == [True] and meta[cloud.IMAGE_KEY]["size"] > 0
    monkeypatch.undo()
    loaded, existed = cloud.load_raw(state)
    assert existed and _meta_rows(loaded.conn) == 50
    loaded.close()
    store.close()


def test_the_image_near_the_one_block_ceiling_counts_as_pressure_and_says_what_would_stop(tmp_path):
    """The serverless budgets count the store image against the 1,024 MB block its save goes through (a bigger one is
    saved with a third copy: past Vercel's memory), as well as memory, time and /tmp: an image at 80% of it makes the
    next run act (archive at the tighter end: E's horizon tightens), and the warning says, in dollars and items, what
    would stop being checked if it went on."""
    from remitwatch import alerts, selfcheck as sc
    from remitwatch.detect import ensure_schema
    s = Store(str(tmp_path / "s.sqlite"))
    ensure_schema(s.conn)
    for i in range(3):
        s.conn.execute("INSERT INTO findings (finding_id, signal, category, status, work_state, shortfall_cents, stale) "
                       "VALUES (?, 'DENIED_AUTH', 'denial', 'open', 'work_now', 125000, 0)", (f"F-{i}",))
    s.conn.commit()
    status = {"started": "2026-11-24T10:17:00+00:00", "finished": "2026-11-24T10:20:00+00:00", "mode": "serverless",
              "seconds": 180, "budget_seconds": 240, "memory_mb": {"peak": 1500}, "store": {"image_mb": 860.0}}
    p = sc.pressure_of(status, sc.budgets("serverless"))
    assert p["pressured"] and p["worst"] == "image" and p["image"] >= 0.8 and "1,024 MB" in p["text"]
    plan = sc.plan_next(s.conn, status, mode="serverless")
    assert plan["level"] == 1 and sc.archive_budget_state(plan)["share"] >= 0.8       # E's horizon takes six months more
    alerts.run_alerts(s.conn, today="2026-11-24")
    near = s.conn.execute("SELECT severity, title FROM alerts WHERE kind='NEAR_CEILING' AND status!='cleared'").fetchone()
    assert near and "860 MB" in near[1] and "3 open items" in near[1] and "$3,750" in near[1], near
    assert any(i["key"] == "selfcheck_ceiling" and not i["ok"] for i in sc.data_health(s.conn))
    calm = dict(status, started="2026-11-24T11:17:00+00:00", store={"image_mb": 500.0})
    sc.plan_next(s.conn, calm, mode="serverless")
    alerts.run_alerts(s.conn, today="2026-11-24")
    assert not s.conn.execute("SELECT 1 FROM alerts WHERE kind='NEAR_CEILING' AND status!='cleared'").fetchone()
    s.close()


def test_the_archive_counts_a_serverless_store_against_the_ceiling(tmp_path, monkeypatch):
    """E's own measure and budget: on a serverless host the store's size against the one-block ceiling is a share too,
    and the larger share decides (the horizon tightens, Data health warns)."""
    from remitwatch import archive, store as store_mod
    monkeypatch.setattr(store_mod, "MEMDB_CEILING_MB", 1)               # a 1 MB block: this small store is past 80% of it
    s = Store.from_bytes(bytearray(_image(rows=8000)))
    m = archive.measure(s.conn, peak=100, mode="serverless", budget=2188)
    assert m["ceiling_share"] >= 0.8 and m["share"] == m["ceiling_share"] and m["level"] in ("warn", "over")
    archive.record_run(s.conn, run_id="r1", measured=m)
    assert archive.budget_state(s.conn)["share"] >= 0.8
    assert archive.effective_horizon(s.conn, budget_state=archive.budget_state(s.conn))["months"] == archive.DEFAULT_HORIZON_MONTHS - 6
    item = next(i for i in archive.data_health(s.conn) if i["key"] == "store_memory")
    assert not item["ok"] and "1 MB" in item["title"]
    server = archive.measure(s.conn, peak=100, mode="server", budget=2188)
    assert server.get("ceiling_share") is None and server["share"] < 0.8   # a server's store is a file: no ceiling
    s.close()


# ======================================================================================================================
# 2. Detection (both hosts: Detector.run serves the server's cycle and the serverless one)
# ======================================================================================================================
@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    for k in ("DROPBOX_APP_KEY", "DROPBOX_APP_SECRET", "DROPBOX_REFRESH_TOKEN", "SMTP_HOST", "REPORT_TO", "ALERT_WEBHOOK_URL",
              "TRIZETTO_SFTP_USER", "SRFAX_ACCESS_ID", "VERCEL", "VERCEL_ENV", "REMITWATCH_STATE_DIR", "REMITWATCH_EMA_REPORTS",
              "REMITWATCH_EMA_LEDGER", "ANTHROPIC_API_KEY", "REMITWATCH_MEMORY_BUDGET_MB", "REMITWATCH_TODAY", "CRON_SECRET"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    monkeypatch.setenv("REMITWATCH_TMP", str(tmp_path / "scratch"))
    from remitwatch import runlog
    saved = set(runlog.SCRUB.names)
    yield
    runlog.SCRUB.names = saved
    runlog.set_clock(None)
    runlog._CURRENT.set(None)


@pytest.fixture(params=["file", "image"])
def kind(request):
    return request.param


def _store(kind: str, tmp_path) -> Store:
    """The server's file store, or the serverless store as cloud.load_raw makes it (an image held in memory)."""
    from remitwatch import cloud
    from remitwatch.alerts import ALERTS_DDL
    from remitwatch.detect import ensure_schema
    if kind == "file":
        s = Store(str(tmp_path / "data" / "remitwatch.sqlite"))
    else:
        blank = Store(":memory:", compact=True)
        s = Store.from_bytes(bytearray(blank.to_bytes()))
        blank.close()
    ensure_schema(s.conn)
    s.conn.executescript(ALERTS_DDL + cloud.CLOUD_DDL)
    return s


def _denials(store: Store, tmp_path, paid_later: bool = False) -> list[str]:
    from tests.test_runlog import denial_files
    paths = denial_files(tmp_path / "feed", paid_later=paid_later)
    store.ingest_835_paths(paths[:1])
    return paths


def test_a_saved_item_carries_its_state_rule_and_its_ranking(kind, tmp_path):
    """needs_G section 0: each item keeps the rule states.py decided its state by, and its dollars x its pattern's
    recovery rate with the basis (the worklist sorts by it)."""
    from remitwatch.detect import Detector
    s = _store(kind, tmp_path)
    _denials(s, tmp_path)
    Detector(s, "run-20261101T000000Z-aaaa", today="2026-11-01").run()
    rows = s.conn.execute("SELECT state_rule, expected_value_cents, recovery_rate, recovery_basis, work_state, shortfall_cents "
                          "FROM findings WHERE COALESCE(stale,0)=0").fetchall()
    assert rows and all(r[0] for r in rows), rows
    open_rows = [r for r in rows if r[4] in ("work_now", "waiting")]
    assert open_rows and all(r[1] is not None and r[3] for r in open_rows), open_rows
    s.close()


def test_a_self_check_that_crashes_leaves_the_items_and_says_so(kind, tmp_path, monkeypatch):
    """A code bug in the checks themselves: the items detection saved stand, and the Data tab and the alerts say the
    guard is off (a critical check that could not run), with the exception's type only."""
    from remitwatch import selfcheck
    from remitwatch.alerts import run_alerts
    from remitwatch.detect import Detector

    def boom(*a, **k):
        raise TypeError("a bug in the checks for QUILLWORTHA")
    monkeypatch.setattr(selfcheck, "run_checks", boom)
    s = _store(kind, tmp_path)
    _denials(s, tmp_path)
    det = Detector(s, "run-20261101T000000Z-aaaa", today="2026-11-01")
    det.run()
    assert det.selfcheck["crashed"] and s.conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0] > 0
    last = selfcheck.meta_get(s.conn, selfcheck.K_LAST)
    assert last["checks"][0]["status"] == "error" and last["checks"][0]["info"] == {"error": "TypeError"}
    run_alerts(s.conn, today="2026-11-01")
    titles = [r[0] for r in s.conn.execute("SELECT title FROM alerts WHERE kind='CHECK_FAILED' AND status!='cleared'")]
    assert titles and "could not run" in titles[0] and "QUILLWORTHA" not in " ".join(titles)
    s.close()


def test_a_contained_ema_step_is_logged_in_the_run(kind, tmp_path, monkeypatch):
    """needs_H D5: an EMA step that fails is contained as before (the 835 items still come out) and logged with its
    stack in the run in progress."""
    from remitwatch import runlog, states
    from remitwatch.detect import Detector

    def boom(*a, **k):
        raise ValueError("answer times failed")
    monkeypatch.setattr(states, "save_response_days", boom)
    s = _store(kind, tmp_path)
    _denials(s, tmp_path)
    rl = runlog.start_run("cycle", trigger="test", logs_dir=str(tmp_path / "logs"), host="server")
    det = Detector(s, rl.run_id, today="2026-11-01")
    det.run()
    errs = [e for e in rl.events if e["level"] == "error"]
    assert any(e["step"] == "ema:response_days" and e["error"]["type"] == "ValueError" for e in errs), errs
    assert {"step": "response_days", "error": "ValueError: answer times failed"} in det.ema_errors
    rl.finish()
    s.close()


def test_a_locked_years_aggregates_are_carried_by_the_detector_itself(tmp_path):
    """needs_E 1b, wired: with no test wrapper around save, a year the archive took keeps its aggregate items as measured
    on the whole year (Detector.run calls archive.carry_locked_aggregates before the save)."""
    from remitwatch import archive
    from tests.test_archive import NEW, TODAY, _sequestered, detect, ingest, sink_of, visits
    store = Store(":memory:", compact=True)
    ingest(store, tmp_path, [(_sequestered(1, 12, "20230310"), "20230415"), (visits(100, 4, NEW), "20260415")])
    detect(store)
    seq = {r[0]: r[1] for r in store.conn.execute("SELECT finding_id, shortfall_cents FROM findings WHERE signal='SEQUESTRATION'")}
    assert list(seq.values()) == [12 * 235]
    res = archive.run(store.conn, sink=sink_of(tmp_path, "compact"), today=TODAY, horizon=24)
    assert res["bills"] == 12
    detect(store, "r2")
    assert {r[0]: r[1] for r in store.conn.execute("SELECT finding_id, shortfall_cents FROM findings WHERE signal='SEQUESTRATION'")} == seq
    store.close()


def test_a_held_run_logs_no_change_people_never_saw(tmp_path, monkeypatch):
    """needs_G section 1 and needs_H D4: the self-check runs before the decision log. A run held for a closure with no
    proof puts the last good worklist back, and the decision log records no closure for it."""
    from remitwatch import runlog, selfcheck
    from remitwatch.detect import Detector
    s = _store("file", tmp_path)
    _denials(s, tmp_path)
    Detector(s, "run-20261101T000000Z-aaaa", today="2026-11-01").run()             # the baseline
    fid = s.conn.execute("SELECT finding_id FROM findings WHERE work_state='work_now'").fetchone()[0]
    real_hide = Detector.hook_hide

    def hide_then_a_bug(self):
        real_hide(self)
        self.conn.execute("UPDATE findings SET status='resolved', work_state='done', auto_note='Paid', evidence='{}' "
                          "WHERE finding_id=?", (fid,))
        self.conn.commit()
    monkeypatch.setattr(Detector, "hook_hide", hide_then_a_bug)
    det = Detector(s, "run-20261102T000000Z-bbbb", today="2026-11-02")
    det.run()
    assert det.selfcheck["held"] and selfcheck.safe_mode(s.conn)["on"]
    assert s.conn.execute("SELECT status FROM findings WHERE finding_id=?", (fid,)).fetchone()[0] == "open"
    kinds = [x["kind"] for x in runlog.decisions_for(s.conn, fid)["items"] if x["run_id"] == "run-20261102T000000Z-bbbb"]
    assert "closed" not in kinds, kinds
    s.close()


# ======================================================================================================================
# 3. The server's cycle (remitwatch.pipeline.run_cycle)
# ======================================================================================================================
def _db(tmp_path) -> str:
    return str(tmp_path / "data" / "remitwatch.sqlite")


def _feed(tmp_path, paid_later: bool = False) -> str:
    from tests.test_runlog import denial_files
    folder = tmp_path / "feed"
    denial_files(folder, paid_later=paid_later)
    return str(folder)


def _last_manifest(db: str) -> dict:
    from remitwatch import runlog
    sink = runlog.DirSink(runlog.logs_dir_for(db))
    m, _events = runlog.read_run(sink, runlog.read_index(sink)[-1]["run_id"])
    return m


def test_every_server_cycle_writes_its_manifest_events_and_decisions(tmp_path):
    """needs_H W1: the cycle starts its own run log: the logs folder beside the store holds the run's manifest (outcome
    ok, its steps, the decision log's summary, the self-check's invariants and the plan) and its events, and the store's
    run_log has the run."""
    import glob
    import sqlite3 as sq

    from remitwatch import pipeline
    db = _db(tmp_path)
    lines: list = []
    out = pipeline.run_cycle(db, sync=False, local=_feed(tmp_path), email=False, report=False, log=lines.append)
    assert "detect" in out and out["plan"]["level"] == 0
    m = _last_manifest(db)
    assert m["outcome"] == "ok" and {"detect", "alerts"} <= {s["step"] for s in m["steps"]}
    assert m["sections"]["decisions"]["by_kind"].get("raised", 0) >= 1
    assert any(c["id"] == "closure_proof" for c in m["invariants"]) and m["self_regulation"]["level"] == 0
    logs = os.path.dirname(db) + "/logs"
    assert glob.glob(logs + "/runs-*.jsonl") and any("run_start" in open(p).read() for p in glob.glob(logs + "/events-*.jsonl"))
    c = sq.connect(db)
    assert c.execute("SELECT outcome FROM run_log WHERE run_id=?", (m["run_id"],)).fetchone()[0] == "ok"
    c.close()


def test_a_dropbox_listing_that_fails_twice_is_tried_again_and_read(tmp_path, monkeypatch):
    """needs_H W8: the server's Dropbox client goes through the self-repair registry: a listing dropped twice by the
    network is tried again and the files are read in the same cycle; repair_log has two 'retrying' rows and one
    'repaired'."""
    import dataclasses
    import sqlite3 as sq

    import requests

    from remitwatch import dropbox_sync, pipeline, repair
    from tests.test_remitwatch import remit
    from tests.test_sync import FakeClient
    from tests.test_features import clean_visits

    class Flaky(FakeClient):
        def __init__(self, files, fail):
            super().__init__(files)
            self.fail = fail

        def list_folder(self, path, recursive=True):
            if self.fail:
                self.fail -= 1
                raise requests.ConnectionError("Connection reset by peer")
            yield from super().list_folder(path, recursive)
    files = {"/Trizetto Data/Trizetto/r900.RMT": remit("".join(clean_visits(900, 3, "99213", "150.00", "80.00")), "240.00",
                                                       seg_count=99, isa_ctrl="000000900", pay_date="20261113")}
    client = Flaky(files, fail=2)
    for k, v in {"DROPBOX_APP_KEY": "k", "DROPBOX_APP_SECRET": "s", "DROPBOX_REFRESH_TOKEN": "r", "DROPBOX_ROOT_PATH": "/Trizetto Data",
                 "REMITWATCH_DATA_DIR": str(tmp_path / "mirror")}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(dropbox_sync, "DropboxClient", lambda *a, **kw: client)
    monkeypatch.setitem(repair.REMEDIES, "network", dataclasses.replace(repair.REMEDIES["network"], delays=(0, 0, 0)))
    db = _db(tmp_path)
    out = pipeline.run_cycle(db, sync=True, email=False, report=False, log=lambda *a: None)
    assert "error" not in out.get("sync", {}) and out["ingest"]["remits"] == 1 and client.fail == 0
    c = sq.connect(db)
    rows = [r[0] for r in c.execute("SELECT outcome FROM repair_log WHERE target='dropbox' ORDER BY id")]
    c.close()
    assert rows == ["retrying", "retrying", "repaired"], rows


def test_a_trizetto_pull_is_tried_again_and_a_bug_in_its_ingest_is_raised_at_once(tmp_path, monkeypatch):
    """needs_H P4: a whole pull is safe to repeat (files read are skipped), so a dropped connection is tried again; what
    the ingest callback raises (a code bug) is never retried (repair.not_retried) and the cycle carries on without it."""
    import dataclasses
    import sqlite3 as sq

    import requests

    from remitwatch import pipeline, repair, trizetto
    monkeypatch.setitem(repair.REMEDIES, "network", dataclasses.replace(repair.REMEDIES["network"], delays=(0, 0, 0)))
    monkeypatch.setattr(trizetto, "configured", lambda: True)
    calls = {"n": 0}

    def flaky_pull(conn, handle, deadline, settings=None, log=print):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise requests.ConnectionError("Connection reset by peer")
        return {"listed": 0, "remits": 0, "ema": 0, "claims_837": 0}
    monkeypatch.setattr(trizetto, "pull", flaky_pull)
    db = _db(tmp_path)
    out = pipeline.run_cycle(db, sync=False, local=_feed(tmp_path), email=False, report=False, log=lambda *a: None)
    assert calls["n"] == 3 and "error" not in out["trizetto"]
    c = sq.connect(db)
    assert [r[0] for r in c.execute("SELECT outcome FROM repair_log WHERE target='trizetto' ORDER BY id")] == ["retrying", "retrying", "repaired"]
    c.close()

    def buggy_pull(conn, handle, deadline, settings=None, log=print):
        calls["n"] += 1
        return handle([("r1.RMT", b"x", 0.0)])          # the ingest raises: a code bug, whatever its words
    from remitwatch import cloud

    def buggy_ingest(store, items, log):
        raise RuntimeError("the reader timed out 503")  # a code bug whose words read like a network error
    monkeypatch.setattr(cloud, "_ingest_batch", buggy_ingest)
    calls["n"] = 0
    monkeypatch.setattr(trizetto, "pull", buggy_pull)
    out = pipeline.run_cycle(db, sync=False, email=False, report=False, log=lambda *a: None)
    assert calls["n"] == 1 and "timed out" in out["trizetto"]["error"] and "finished" in out      # raised at once, never retried


def test_ledger_summaries_that_stop_matching_the_ledger_are_rebuilt_once_in_the_cycle(tmp_path, monkeypatch):
    """needs_H section 8 and CLAUDE.md: a derived table that fails its check is rebuilt once. The store's EMA summaries
    off by $50 against the posting ledger (selfcheck's ledger_store check) are rebuilt from the ledger in the cycle
    (repair.rebuild_if_failing), checked again, and the repair is logged; no safe mode."""
    import sqlite3 as sq

    from remitwatch import ema_ledger as el, ema_reports as er, pipeline
    from tests import ema_fixtures as F
    from tests.test_selfcheck import SALT_HEX, prod_rows
    monkeypatch.setattr(er, "CLAIM_HOST_SUFFIX", F.HOST_SUFFIX)
    monkeypatch.setenv("REMITWATCH_EMA_REPORTS", "all")
    db = _db(tmp_path)
    s = Store(db)
    from remitwatch.detect import ensure_schema
    ensure_schema(s.conn)
    s.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('ema:key_salt', ?)", (SALT_HEX,))
    s.conn.commit()
    lg = el.open_for_store(db)
    res = er.ingest_file(s, F.write(str(tmp_path / "prod.csv"), F.production_csv(prod_rows())), via="test", ledger=lg, log=lambda *a: None)
    assert res["status"] == "current", res
    el.refresh(s, lg, log=lambda *a: None)
    lg.close()
    s.conn.execute("UPDATE ema_months SET cents = cents + 5000 WHERE measure = 'ins_paid'")      # the summaries drift
    s.conn.commit()
    s.close()
    out = pipeline.run_cycle(db, sync=False, email=False, report=False, log=lambda *a: None)
    assert out["ledger_rebuilt"] == {"ok": True} and out["money_checks"]["ledger_store"] == "ok", (out.get("ledger_rebuilt"), out.get("money_checks"))
    c = sq.connect(db)
    rows = [r[0] for r in c.execute("SELECT outcome FROM repair_log WHERE remedy='derived_table' AND target='ledger_summaries'")]
    assert rows == ["repaired"] and not c.execute("SELECT value FROM meta WHERE key='selfcheck:safe_mode' AND value LIKE '%\"on\": true%'").fetchone()
    c.close()


def test_a_scheduled_cycle_that_dies_before_its_lock_is_visible_on_the_panel(tmp_path, monkeypatch):
    """needs_H W10 (P0a): a store that cannot be opened makes the cycle fail before its lock; with no run log yet, the
    failure is still written as a failed run, and the panel shows it."""
    import sqlite3 as sq
    from datetime import timedelta

    from remitwatch import pipeline, runlog
    db = _db(tmp_path)
    store = Store(db)
    yesterday = runlog.utcnow() - timedelta(days=1)
    rl = runlog.start_run("cycle", trigger="schedule", db_path=db, conn=store.conn, host="server", now=yesterday)
    rl.finish(conn=store.conn, now=yesterday + timedelta(minutes=2))      # yesterday's good run
    store.close()

    def broken_store(path, *a, **kw):
        raise sq.OperationalError("duplicate column name: work_state")     # a migration bug
    monkeypatch.setattr(pipeline, "Store", broken_store)
    with pytest.raises(sq.OperationalError):
        pipeline.run_cycle(db, sync=False, email=False, report=False)
    c = sq.connect(db)
    p = runlog.panel(c, runlog.logs_dir_for(db))
    c.close()
    assert p["last_run"]["outcome"] == "failed" and p["last_run"]["first_error"]["type"] == "OperationalError"


def test_the_schedulers_child_that_ends_badly_is_noted_once(tmp_path, monkeypatch):
    """needs_H W11 (P0b): the scheduler's child exit code is noted as a failed run (its own log has the detail); a child
    that ended well adds nothing."""
    from remitwatch import pipeline, runlog
    db = _db(tmp_path)
    Store(db).close()
    sched = pipeline.Scheduler()
    sched.db_path = db
    monkeypatch.setattr(sched, "argv", lambda _db: [sys.executable, "-c", "import sys; sys.exit(3)"])
    assert sched.run_child("schedule") == 3
    sink = runlog.DirSink(runlog.logs_dir_for(db))
    last = runlog.read_index(sink)[-1]
    assert last["outcome"] == "failed" and last.get("exit_code") == 3
    n = len(runlog.read_index(sink))
    monkeypatch.setattr(sched, "argv", lambda _db: [sys.executable, "-c", "import sys; sys.exit(0)"])
    assert sched.run_child("schedule") == 0 and len(runlog.read_index(sink)) == n


def test_a_cycle_past_its_memory_budget_makes_the_next_ones_spread_then_shed(tmp_path, monkeypatch):
    """needs_G section 2: with a 1 MB budget every cycle passes 80% of it. The next cycle's plan steps up one level a
    run (spread, archive, shed); at level 3 the provider tables, the report and the self-audit are left for a calm cycle,
    and the plan, the regulation and what was shed are recorded."""
    from datetime import datetime, timedelta, timezone

    from remitwatch import pipeline, selfcheck
    monkeypatch.setenv("REMITWATCH_MEMORY_BUDGET_MB", "1")
    monkeypatch.setenv("REMITWATCH_REPORT_EVERY_DAYS", "0")             # the weekly report due at every cycle
    db = _db(tmp_path)
    feed = _feed(tmp_path)
    levels, outs = [], []
    for n in range(4):                  # cycles minutes apart, as on a server (a status is counted once, by its start)
        monkeypatch.setattr(pipeline, "_now", lambda n=n: (datetime.now(timezone.utc) - timedelta(minutes=10 * (4 - n))).isoformat(timespec="seconds"))
        out = pipeline.run_cycle(db, sync=False, local=feed, force_detect=True, email=False, report=None,
                                 report_dir=str(tmp_path / "reports"), log=lambda *a: None)
        levels.append(out["plan"]["level"])
        outs.append(out)
    assert levels == [0, 1, 2, 3], levels
    assert all("report" in o for o in outs[:3])
    last = outs[-1]
    assert "activity" in last["plan"]["skip"] and "activity" not in last and "self_audit" not in last and "report" not in last
    s = Store(db)
    reg = selfcheck.meta_get(s.conn, selfcheck.K_REGULATE)
    assert reg["plan"]["level"] == 3 and set(reg["did"]["shed"]) >= {"activity", "report", "audit"}
    from remitwatch import activity
    assert activity.stale(s.conn)                  # the provider tables wait for a calm cycle, and say so
    s.close()


def test_old_settled_bills_go_to_the_archive_in_the_cycle_and_a_reopening_comes_back(tmp_path):
    """needs_E section 3: retention runs after detection in the server's cycle: the settled bills of three years ago
    leave for the archive folder beside the store (a second cycle takes nothing more), the run's size is recorded, and a
    reopening asked in the app is back, with its status, after the next cycle."""
    import sqlite3 as sq

    from remitwatch import archive, pipeline
    from tests.test_archive import NEW, OLD, ingest, visits
    db = _db(tmp_path)
    s = Store(db)
    ingest(s, tmp_path, [(visits(1, 8, OLD), "20220415"), (visits(100, 8, NEW), "20260415")])
    s.close()
    out = pipeline.run_cycle(db, sync=False, email=False, report=False, log=lambda *a: None)
    assert out["archive"]["bills"] == 8, out.get("archive")
    c = sq.connect(db)
    assert c.execute("SELECT COUNT(*) FROM archive_bills").fetchone()[0] == 8
    assert len(archive.history(c)) == 1 and os.listdir(os.path.join(os.path.dirname(db), "archive"))
    bill = c.execute("SELECT bill FROM archive_bills LIMIT 1").fetchone()[0]
    c.close()
    again = pipeline.run_cycle(db, sync=False, email=False, report=False, log=lambda *a: None)
    assert "archive" not in again                                       # nothing more to take
    c = sq.connect(db)
    assert archive.request_restore(c, bill, actor="biller@example.test", reason="a late payment")["queued"]
    c.close()
    back = pipeline.run_cycle(db, sync=False, email=False, report=False, log=lambda *a: None)
    assert back["archive_back"]["asked"] == 1
    c = sq.connect(db)
    assert c.execute("SELECT COUNT(*) FROM archive_bills WHERE bill=?", (bill,)).fetchone()[0] == 0
    c.close()


# ======================================================================================================================
# 4. The serverless cycle (remitwatch.cloud.run_cloud_cycle): a fake Dropbox, the state folder inside it
# ======================================================================================================================
from tests.test_cloud import DATA, box, raw_conn, visits_with_downcodes, write_remits  # noqa: E402,F401


def _cloud(state, client, **kw):
    from remitwatch import cloud
    kw.setdefault("budget_s", 600)
    return cloud.run_cloud_cycle(state, client, email=False, **kw)


def test_every_serverless_run_logs_to_the_state_folder_and_prints_one_line(box, capsys):
    """needs_H W2: the run's manifest and events go to the state folder's logs/ (with its index); Vercel's function log
    gets exactly one line, the run id and its outcome, and nothing else (the cycle's own lines go to the run log)."""
    import re

    from remitwatch import runlog
    root, client, state = box
    write_remits(root / DATA, visits_with_downcodes())
    capsys.readouterr()
    out = _cloud(state, client, log=print)
    printed = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    assert "detect" in out and len(printed) == 1, printed
    assert re.match(r"^RemitWatch run run-\d{8}T\d{6}Z-[0-9a-f]{4}: ok \(", printed[0]), printed
    names = [m["name"] for m in state.list("logs")]
    assert "logs/index.json" in names and any(n.startswith("logs/runs/") and n.endswith(".jsonl.gz") for n in names), names
    sink = runlog.sink_for(state=state)
    m, events = runlog.read_run(sink, runlog.read_index(sink)[-1]["run_id"])
    assert m["outcome"] == "ok" and {"load", "detect", "save_store", "save_serving"} <= {s["step"] for s in m["steps"]}
    assert m["sections"]["decisions"]["by_kind"].get("raised") and m["invariants"] and m["self_regulation"]["level"] == 0
    assert out["store"]["image_mb"] > 0                 # the store's size in memory: its share of the one-block ceiling


def test_a_status_over_the_memory_mark_makes_the_next_serverless_run_spread(box, monkeypatch):
    """needs_G section 3: the last run's status.json past 80% of the 2,188 MB budget (a peak of 1,800 MB) makes the next
    run spread: a run that read something hands detection to a fresh invocation (more). A run at level 3 sheds the
    provider tables and says what it shed."""
    import json

    from remitwatch import cloud, selfcheck
    root, client, state = box
    write_remits(root / DATA, visits_with_downcodes()[:30])
    first = _cloud(state, client)
    assert "detect" in first and first["plan"]["level"] == 0
    st = json.loads(state.get(cloud.STATUS))
    st["started"], st["memory_mb"] = "2026-11-24T00:00:00+00:00", dict(st.get("memory_mb") or {}, peak=1800)
    state.put(cloud.STATUS, json.dumps(st).encode())
    write_remits(root / DATA, visits_with_downcodes()[30:], prefix="b", start_ctrl=40)
    second = _cloud(state, client)
    assert second["plan"]["level"] == 1 and second["ingest"]["remits"] >= 1 and second.get("deferred") == "detection"
    assert second["more"] is True and "detect" not in second
    third = _cloud(state, client)                       # the fresh invocation analyses
    assert "detect" in third
    conn = raw_conn(state)
    selfcheck.meta_set(conn, selfcheck.K_REGULATE, dict(selfcheck.meta_get(conn, selfcheck.K_REGULATE), level=3, calm=0))
    conn.commit()
    store, _e = cloud.load_raw(state)
    store.conn.execute("UPDATE meta SET value=? WHERE key=?", (json.dumps(selfcheck.meta_get(conn, selfcheck.K_REGULATE)), selfcheck.K_REGULATE))
    from remitwatch import activity
    activity.mark_pending(store.conn)
    store.conn.commit()
    cloud.save_raw(state, store)
    store.close()
    shed = _cloud(state, client)
    assert shed["plan"]["level"] >= 3 and "activity" in shed["shed"] and "activity" not in shed


def test_idle_serverless_runs_carry_the_self_regulation_to_the_next_run(box):
    """An idle Vercel run saves no store, so what self-regulation counted in it would be lost: each idle run started from
    the level the last saving run left, a site under memory pressure never reached the alert, and calm runs never brought
    the level down (found on the real image: four idle runs read past 80% and the level stayed one step up). The run's
    regulation rides in its status.json and the next run adopts it."""
    import json

    from remitwatch import cloud, selfcheck
    root, client, state = box
    write_remits(root / DATA, visits_with_downcodes()[:30])
    assert _cloud(state, client)["plan"]["level"] == 0

    hour = iter(range(1, 24))

    def reading(peak):
        # the run before, read as it ended (runs here start within a second; on a site they are hours apart)
        st = json.loads(state.get(cloud.STATUS))
        st["started"], st["memory_mb"] = f"2026-11-24T{next(hour):02d}:00:00+00:00", dict(st.get("memory_mb") or {}, peak=peak)
        state.put(cloud.STATUS, json.dumps(st).encode())
    levels = []
    for i in range(4):                  # four runs, each after a reading past 80% of the 2,188 MB budget
        reading(2100)
        got = _cloud(state, client)
        assert i == 3 or not got.get("store"), (i, got.get("store"))      # idle (the fourth saves its new alert)
        levels.append(got["plan"]["level"])
    assert levels == [1, 2, 3, 4], levels
    alerts = {r[0] for r in raw_conn_of_status(state)}
    assert "NEAR_LIMIT" in alerts, alerts
    calm = []
    for _ in range(3):
        reading(900)
        calm.append(_cloud(state, client)["plan"]["level"])
    assert calm[-1] == 3, calm              # three calm runs bring it down one step
    assert json.loads(state.get(cloud.STATUS))["regulate"]["level"] == 3


def raw_conn_of_status(state):
    """The alert kinds the last run's status names (an idle run does not save the store; its alerts are in its status)."""
    import json

    from remitwatch import cloud
    st = json.loads(state.get(cloud.STATUS))
    return [(k,) for k in ((st.get("alerts") or {}).get("by_kind") or {})]


def test_a_serverless_run_held_by_the_self_check_keeps_the_copy_as_it_was(box, monkeypatch):
    """needs_G section 3: a run whose detection closes an item with no proof is held: meta selfcheck:safe_mode is on in the
    saved store, and the dashboard's copy shows the item open (the last good worklist), with the alert."""
    from remitwatch import cloud, selfcheck
    from remitwatch.detect import Detector
    root, client, state = box
    write_remits(root / DATA, visits_with_downcodes())
    _cloud(state, client)
    fid = raw_conn(state).execute("SELECT finding_id FROM findings WHERE status='open' AND COALESCE(stale,0)=0 LIMIT 1").fetchone()[0]
    real_hide = Detector.hook_hide

    def hide_then_a_bug(self):
        real_hide(self)
        self.conn.execute("UPDATE findings SET status='resolved', work_state='done', auto_note='Paid', evidence='{}' "
                          "WHERE finding_id=?", (fid,))
        self.conn.commit()
    monkeypatch.setattr(Detector, "hook_hide", hide_then_a_bug)
    out = _cloud(state, client, force_detect=True)
    assert "detect" in out and "serving" in out
    conn = raw_conn(state)
    assert selfcheck.safe_mode(conn)["on"] and conn.execute("SELECT status FROM findings WHERE finding_id=?", (fid,)).fetchone()[0] == "open"
    monkeypatch.setenv("REMITWATCH_STATE_DIR", str(root / "RemitWatch-state"))
    copy = cloud.ServingCopy().connection()
    assert copy.execute("SELECT status FROM findings WHERE finding_id=?", (fid,)).fetchone()[0] == "open"
    assert copy.execute("SELECT COUNT(*) FROM alerts WHERE kind='SAFE_MODE' AND status!='cleared'").fetchone()[0] == 1
    # the copy carries the runs' logs and the decision log (the Data tab and the drawer read them there)
    assert copy.execute("SELECT COUNT(*) FROM run_log").fetchone()[0] >= 1
    assert copy.execute("SELECT COUNT(*) FROM item_decisions").fetchone()[0] >= 1


def test_old_settled_bills_leave_in_a_serverless_run_that_did_not_analyse_and_a_reopening_comes_back(box, monkeypatch):
    """needs_E section 4: retention runs in a run that did not analyse (its memory is free): the settled bills of three
    years ago go to the state folder's archive/ and the image is compacted; the next idle run takes nothing; a reopening
    the dashboard journals is taken by the next run and the bill is back. The dashboard's copy holds the archive's
    index (Find a claim opens archived bills)."""
    from remitwatch import archive, cloud
    from tests.test_archive import NEW, OLD, visits
    from tests.test_remitwatch import remit
    root, client, state = box
    folder = root / DATA
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "old.RMT").write_bytes(remit("".join(visits(1, 8, OLD)), "100.00", seg_count=99, isa_ctrl="000000001", pay_date="20220415"))
    (folder / "new.RMT").write_bytes(remit("".join(visits(100, 8, NEW)), "100.00", seg_count=99, isa_ctrl="000000002", pay_date="20260415"))
    first = _cloud(state, client)
    assert "detect" in first and "archive" not in first           # a run that analysed leaves the archive for the next
    idle = _cloud(state, client)
    assert idle["archive"]["bills"] == 8 and "archive_compact" in idle and "store" in idle
    assert [m for m in state.list("archive") if m["name"].endswith(".jsonl.gz")]
    conn = raw_conn(state)
    bill = conn.execute("SELECT bill FROM archive_bills LIMIT 1").fetchone()[0]
    assert conn.execute("SELECT COUNT(*) FROM archive_bills").fetchone()[0] == 8
    monkeypatch.setenv("REMITWATCH_STATE_DIR", str(root / "RemitWatch-state"))
    copy = cloud.ServingCopy().connection()             # the dashboard's copy holds the archive's index
    assert copy.execute("SELECT COUNT(*) FROM archive_bills").fetchone()[0] == 8
    assert "archive" not in _cloud(state, client)
    cloud.record_action(state, archive.journal_entry(bill, actor="biller@example.test", reason="a late payment"))
    back = _cloud(state, client)
    assert back["archive_back"]["asked"] == 1
    assert raw_conn(state).execute("SELECT COUNT(*) FROM archive_bills WHERE bill=?", (bill,)).fetchone()[0] == 0


def test_the_weekly_self_audit_takes_the_first_idle_serverless_run_and_a_journaled_request_runs_it(box, monkeypatch):
    """needs_G section 3: the self-audit runs in a run that did not analyse and saves its lists with the store; the next
    idle run uploads nothing; a request from the Data tab (journaled on Vercel) runs it again in the next run."""
    from remitwatch import cloud, selfcheck
    monkeypatch.undo()                                  # the box fixture leaves the audit out: here it is the subject
    root, client, state = box
    write_remits(root / DATA, visits_with_downcodes())
    assert "self_audit" not in _cloud(state, client)    # it analysed: the audit waits
    audit = _cloud(state, client)
    assert "self_audit" in audit and "store" in audit
    assert selfcheck.meta_get(raw_conn(state), selfcheck.K_AUDIT)["latest"]
    idle = _cloud(state, client)
    assert "self_audit" not in idle and "store" not in idle
    n0 = raw_conn(state).execute("SELECT COUNT(*) FROM selfcheck_audits").fetchone()[0]
    cloud.record_action(state, {"kind": "selfcheck_request", "what": "audit", "user": "owner@example.test"})
    asked = _cloud(state, client)
    assert asked["actions_folded"] == 1
    assert raw_conn(state).execute("SELECT COUNT(*) FROM selfcheck_audits").fetchone()[0] == n0 + 1


# ======================================================================================================================
# 5. The routes (remitwatch.api_ops, and the archive in Find a claim and an item's panel), on both hosts
# ======================================================================================================================
from tests.test_cloud import PW, site  # noqa: E402,F401

XHR = {"X-Requested-With": "remitwatch"}


def _server_client(monkeypatch, db):
    """The API of a server reading the store at db, with a password."""
    import base64

    from fastapi.testclient import TestClient

    import remitwatch.api as api
    api.ensure_server_schema(db)
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    monkeypatch.setattr(api, "DB_PATH", db)
    monkeypatch.setattr(api, "PASSWORD", PW)
    monkeypatch.setattr(api, "USERNAME", "oda")
    monkeypatch.setattr(api, "SHOW_PHI", False)
    monkeypatch.setattr(api, "_FAILED", {})
    auth = {"Authorization": "Basic " + base64.b64encode(f"oda:{PW}".encode()).decode()}
    return TestClient(api.app), auth


def test_the_ops_routes_answer_on_a_server_and_the_bundle_holds_no_patient_data(tmp_path, monkeypatch):
    """needs_H W6, needs_G section 6: the decision log of an item closed by a later payment, the three panels, the
    self-check's lists, the bundle (a zip with runs and events and no planted name), the banner in /api/meta. Every route
    needs the sign-in."""
    import shutil

    from remitwatch import diagnose, pipeline, runlog
    from tests.test_runlog import LAST, MEMBER, denial_files
    db = _db(tmp_path)
    p1, p2 = denial_files(tmp_path / "f", paid_later=True)
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two.mkdir()
    shutil.copy(p1, one / "r001.RMT")
    shutil.copy(p2, two / "r002.RMT")
    pipeline.run_cycle(db, sync=False, local=str(one), email=False, report=False, log=lambda *a: None)
    s = Store(db)
    fid = s.conn.execute("SELECT finding_id FROM findings WHERE patient_control_number LIKE 'CB0000300%'").fetchone()[0]
    s.close()
    pipeline.run_cycle(db, sync=False, local=str(two), email=False, report=False, log=lambda *a: None)
    tc, auth = _server_client(monkeypatch, db)
    got = tc.get(f"/api/findings/{fid}/decisions", headers=auth).json()["items"]
    assert [x["kind"] for x in got][:1] == ["closed"] and got[0]["rule"] == "paid_later:later_payment"
    panels = tc.get("/api/ops/panels", headers=auth).json()
    assert {"selfcheck", "archive", "logs"} <= set(panels) and panels["logs"]["runs"] and panels["logs"]["bundle"]["what"]
    assert panels["selfcheck"]["lines"] and panels["archive"]["lines"]
    meta = tc.get("/api/meta", headers=auth).json()
    assert "banner" in meta and meta["decisions"] is True
    assert tc.get("/api/ops/selfcheck/audit/gap", headers=auth).json()["list"] == "gap"
    assert tc.get("/api/ops/selfcheck/offenders/vanished", headers=auth).status_code == 200
    r = tc.get("/api/ops/diagnostic-bundle?days=2", headers=auth)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store" and r.headers["content-type"] == "application/zip"
    files = diagnose.unpack(r.content)
    assert any(n.startswith("runs/") for n in files) and not any(LAST.encode() in b or MEMBER.encode() in b for b in files.values())
    assert [m["name"] for m in runlog.DirSink(runlog.logs_dir_for(db)).list("downloads")]      # who and when, kept
    audit = tc.post("/api/ops/selfcheck/audit", headers=dict(auth, **XHR)).json()
    assert audit["ran"] is True
    for path in ("/api/ops/panels", "/api/ops/logs", "/api/ops/diagnostic-bundle", f"/api/findings/{fid}/decisions", "/api/ops/archive"):
        assert tc.get(path).status_code == 401, path
    assert tc.post("/api/ops/selfcheck/audit", headers=auth).status_code in (400, 403)      # no cross-site request


def test_an_item_kept_from_an_earlier_release_opens_with_an_empty_change_log(tmp_path, monkeypatch):
    """The item panel asks every item for its "Why did this change?" log. An item kept from an earlier release can carry
    an id of another shape (found by the page check in tests/test_review_rates.py: the route refused it, a red line in
    the browser's console): the answer is an empty log, never an error."""
    db = _db(tmp_path)
    s = Store(db)
    s.close()
    tc, auth = _server_client(monkeypatch, db)
    r = tc.get("/api/findings/legacy00000000000000aaaa/decisions", headers=auth)
    assert r.status_code == 200 and r.json()["items"] == [], r.text
    assert tc.get("/api/findings/legacy00000000000000aaaa/decisions").status_code == 401


def test_the_ops_routes_read_what_the_run_stored_on_vercel(site):
    """The same routes on a serverless host read the dashboard's copy and the state folder (never worked out again on
    the copy), and the Data tab's requests are journaled for the next run."""
    from remitwatch import cloud, diagnose
    tc, auth, _api, state = site
    fid = tc.get("/api/findings?status=all&limit=5", headers=auth).json()["items"][0]["finding_id"]
    assert tc.get(f"/api/findings/{fid}/decisions", headers=auth).json()["items"][0]["kind"] == "raised"
    panels = tc.get("/api/ops/panels", headers=auth).json()
    assert panels["logs"]["runs"] and panels["selfcheck"]["lines"] is not None
    files = diagnose.unpack(tc.get("/api/ops/diagnostic-bundle?days=2", headers=auth).content)
    assert any(n.startswith("runs/") for n in files)
    asked = tc.post("/api/ops/selfcheck/audit", headers=dict(auth, **XHR)).json()
    assert asked["asked"] is True
    assert any(a.get("kind") == "selfcheck_request" for _n, a in cloud.read_actions(state))


def test_find_a_claim_and_an_items_panel_open_what_the_archive_took_and_a_reopening_brings_it_back(tmp_path, monkeypatch):
    """needs_E section 5: Find a claim finds an archived bill by its claim id (archived: true); an item the archive took
    opens by its id with its history ending in why it left; a reopening asked from the page is queued, and the next
    cycle brings the bill and its item back."""
    from remitwatch import pipeline
    from tests.test_archive import NEW, OLD, denial, ingest, visits
    db = _db(tmp_path)
    s = Store(db)
    ingest(s, tmp_path, [(visits(1, 8, OLD) + [denial(50, OLD)], "20220415"), (visits(100, 8, NEW), "20260415")])
    s.close()
    out = pipeline.run_cycle(db, sync=False, email=False, report=False, log=lambda *a: None)
    assert out["archive"]["bills"] >= 8 and out["archive"]["findings"] >= 1
    s = Store(db)
    fid, bill = s.conn.execute("SELECT finding_id, bill FROM archive_findings LIMIT 1").fetchone()
    claim_id = s.conn.execute("SELECT id FROM archive_ids WHERE bill=? AND kind='pcn' LIMIT 1", (bill,)).fetchone()[0]
    s.close()
    tc, auth = _server_client(monkeypatch, db)
    found = tc.get("/api/claims/search", headers=dict(auth, **{"X-Search": claim_id})).json()["items"]
    assert found and found[0]["archived"] is True and found[0]["bill"] == bill
    item = tc.get(f"/api/findings/{fid}", headers=auth).json()
    assert item["archived"] is True and item["finding"]["finding_id"] == fid and item["events"][-1]["kind"] == "archived"
    r = tc.post("/api/archive/reopen", json={"finding_id": fid, "reason": "a late payment"}, headers=dict(auth, **XHR)).json()
    assert r["queued"] is True
    back = pipeline.run_cycle(db, sync=False, email=False, report=False, log=lambda *a: None)
    assert back["archive_back"]["asked"] == 1
    assert tc.get(f"/api/findings/{fid}", headers=auth).json().get("archived") is not True


def test_an_archived_claim_found_by_find_a_claim_opens_whole_in_the_claim_view(tmp_path, monkeypatch):
    """needs_E section 5: Find a claim finds an archived bill and the claim view opens it whole from the archive's record
    (every remittance with its lines, the item the archive took), saying it is archived and since when. Found by the
    wiring check: the search found the bill but the view answered 404 ("No remittance in the files"), and the search's
    row lacked the fields the page's table draws (its payers, its remittances). A claim nothing holds is still a 404."""
    from remitwatch import pipeline
    from tests.test_archive import NEW, OLD, denial, ingest, visits
    db = _db(tmp_path)
    s = Store(db)
    ingest(s, tmp_path, [(visits(1, 8, OLD) + [denial(50, OLD)], "20220415"), (visits(100, 8, NEW), "20260415")])
    s.close()
    pipeline.run_cycle(db, sync=False, email=False, report=False, log=lambda *a: None)
    s = Store(db)
    fid, bill = s.conn.execute("SELECT finding_id, bill FROM archive_findings LIMIT 1").fetchone()
    assert not s.conn.execute("SELECT 1 FROM findings WHERE finding_id=?", (fid,)).fetchone()        # it left the store
    s.close()
    tc, auth = _server_client(monkeypatch, db)
    row = tc.get("/api/claims/search", headers=dict(auth, **{"X-Search": bill})).json()["items"][0]
    assert row["archived"] is True and row["bill"] == bill
    assert row["payers"] == [] and row["remits"] >= 1 and "last_payment" in row and row["open_findings"] == 0
    r = tc.get("/api/claims/view", headers=dict(auth, **{"X-Claim": bill}))
    assert r.status_code == 200, r.text
    c = r.json()
    assert c["archived"] is True and c["archived_at"] and c["bill"] == bill and c["reopen"]
    assert c["remits"] and all(x["lines"] for x in c["remits"]) and c["remits"][0]["payer_name"]
    assert fid in {f["finding_id"] for f in c["findings"]}
    assert tc.get("/api/claims/view", headers=dict(auth, **{"X-Claim": "ZZQ999999"})).status_code == 404
    # the store is untouched: the bill is still archived, and the request's own copy is gone
    s = Store(db)
    assert s.conn.execute("SELECT COUNT(*) FROM archive_bills WHERE bill=?", (bill,)).fetchone()[0] == 1
    assert not s.conn.execute("SELECT 1 FROM claims WHERE patient_control_number LIKE ?", (bill + "%",)).fetchone()
    s.close()


def _browser_ready() -> bool:
    import shutil
    import subprocess
    return bool(shutil.which("node")) and subprocess.run(["node", "-e", "require('playwright')"], capture_output=True).returncode == 0


@pytest.mark.skipif(not _browser_ready(), reason="needs node and Playwright for node")
def test_the_data_tabs_three_panels_draw_in_plain_words_at_a_desk_and_on_a_phone(tmp_path):
    """static/ops.js in a browser over an invented store after one cycle: "Running on its own", "Logs and diagnostics" and
    "Store size and memory" draw with no page error, in plain words (no em dash), the bundle button says what it is for,
    and at a phone's width nothing runs off the side."""
    import json
    import socket
    import subprocess
    import time
    import urllib.request

    from remitwatch import pipeline
    from tests.test_archive import NEW, OLD, denial, ingest, visits
    db = _db(tmp_path)
    (tmp_path / "arc").mkdir()
    st = Store(db)          # older settled claims the cycle moves to the archive: they open from it on the page
    ingest(st, tmp_path / "arc", [(visits(1, 8, OLD) + [denial(50, OLD)], "20220415"), (visits(100, 8, NEW), "20260415")])
    st.close()
    pipeline.run_cycle(db, sync=False, local=_feed(tmp_path), email=False, report=False, log=lambda *a: None)
    st = Store(db)
    fid, bill = st.conn.execute("SELECT finding_id, bill FROM archive_findings LIMIT 1").fetchone()
    st.close()
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DROPBOX_", "TRIZETTO_", "SRFAX_", "ANTHROPIC_", "SMTP_", "MODMED_"))
           and k not in ("REPORT_TO", "ALERT_WEBHOOK_URL", "REMITWATCH_MODE", "REMITWATCH_OWNER_PASSWORD", "REMITWATCH_SHOW_PHI",
                         "REMITWATCH_SYNC_INTERVAL_MINUTES", "REMITWATCH_ALLOW_OPEN", "REMITWATCH_STATE_DIR")}
    env.update(REMITWATCH_PASSWORD=PW, REMITWATCH_USER="oda", REMITWATCH_DB=db, PYTHONDONTWRITEBYTECODE="1")
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    srv = subprocess.Popen([sys.executable, "-B", "-m", "uvicorn", "remitwatch.api:app", "--host", "127.0.0.1", "--port", str(port),
                            "--log-level", "warning"], cwd=repo, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for _ in range(150):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        got = subprocess.run(["node", os.path.join(repo, "tests", "ops_page.js"), str(port), "oda", PW, str(tmp_path), bill, fid],
                             capture_output=True, text=True, timeout=240)
    finally:
        srv.terminate()
        srv.wait(timeout=10)
    assert got.returncode == 0, got.stderr[-2000:]
    out = json.loads(got.stdout.strip().splitlines()[-1])
    assert not out["errors"] and not [f for f in out["failed"] if "/api/ops/" in f], out
    for width, w in out["widths"].items():
        assert w["running"].startswith("Running on its own") and "check" in w["running"], (width, w["running"][:300])
        assert w["logs"].startswith("Logs and diagnostics") and "Give this file and the repository to Claude" in w["logs"], width
        assert w["store"].startswith("Store size and memory") and w["bundle"], width
        assert "—" not in w["running"] + w["logs"] + w["store"]
    assert out["widths"]["390"]["overflow"] <= 1, out["widths"]["390"]["overflow"]
    # an archived claim (Find a claim) and an archived item open from the archive, say so, and offer to bring them back
    for width, w in out["widths"].items():
        assert w["claim"].startswith("Claim ") and "In the archive since" in w["claim"] and "Bring it back to the lists" in w["claim"], (width, w["claim"][:400])
        assert "In the archive since" in w["item"] and "See the whole claim" in w["item"], (width, w["item"][:400])
        assert "—" not in w["claim"] + w["item"]
    assert "next analysis" in out["reopened"] or "next run" in out["reopened"], out["reopened"]


# ======================================================================================================================
# 6. The command line (remitwatch.cli): diagnose, self-audit, archive, archive-restore
# ======================================================================================================================
class _FlakyLine:
    """A fax line whose first status check loses its connection, and whose send always does (it may have gone out)."""
    name = "srfax"

    def __init__(self):
        self.calls = {"status": 0, "send": 0}

    def status(self, ref):
        import requests
        self.calls["status"] += 1
        if self.calls["status"] == 1:
            raise requests.ConnectionError("connection dropped")
        return {"state": "sent"}

    def send(self, *a, **kw):
        import requests
        self.calls["send"] += 1
        raise requests.ConnectionError("connection dropped")


def _fake_fax_run(monkeypatch, line):
    """The fax run's own work replaced by one status check and one send through the provider it was handed."""
    import requests

    from remitwatch import api, fax, repair
    seen = {}

    def refresh(prov, budget_s=0, lock=None, **kw):
        seen["status"] = prov.status("F1")
        try:
            prov.send("cover", "fax number")
        except requests.ConnectionError:
            seen["send_failed"] = True
        return {}
    monkeypatch.setattr(fax, "provider", lambda env=None: line)
    monkeypatch.setattr(api, "_fax_refresh", refresh)
    monkeypatch.setattr(api, "_send_followups", lambda prov, actor, budget_s=0, lock=None, **kw: {})
    real = repair.resilient
    monkeypatch.setattr(repair, "resilient", lambda c, t, **kw: real(c, t, **dict(kw, sleep=lambda s: None)))
    return seen


@pytest.mark.parametrize("entry", ["cycle", "cron"])
def test_the_daily_fax_runs_reads_are_tried_again_and_a_send_never_is(entry, tmp_path, monkeypatch):
    """needs_H L2 on both of the fax line's daily entries: the server cycle's fax run (cli._fax_run, which `cycle` and
    `fax-cycle` call) and the fax line's own daily call (/api/cron/faxes). A status check that lost its connection is
    tried again (remitwatch.repair); a send that lost it is never sent again (it may have gone out)."""
    from remitwatch import cli
    db = _db(tmp_path)
    Store(db).close()
    line = _FlakyLine()
    seen = _fake_fax_run(monkeypatch, line)
    if entry == "cycle":
        cli._fax_run(db, budget_s=60)
    else:
        tc, auth = _server_client(monkeypatch, db)
        r = tc.post("/api/cron/faxes", headers=dict(auth, **XHR))
        assert r.status_code == 200, r.text
    assert seen["status"] == {"state": "sent"} and line.calls["status"] == 2         # tried again after the dropped line
    assert seen.get("send_failed") and line.calls["send"] == 1                      # a send goes once


def test_the_command_line_writes_the_bundle_runs_the_self_audit_and_moves_old_claims(tmp_path, capsys):
    """needs_H W7 and the brief's commands: `diagnose` writes the zip, `self-audit` runs it now, `archive --dry-run` says what
    would move (counts only, never a claim number), `archive --run` moves it, `archive-restore` brings a bill back."""
    import json

    from remitwatch import cli, diagnose, pipeline
    from remitwatch.detect import Detector
    from tests.test_archive import NEW, OLD, ingest, visits
    db = _db(tmp_path)
    s = Store(db)
    ingest(s, tmp_path, [(visits(1, 8, OLD), "20220415"), (visits(100, 8, NEW), "20260415")])
    Detector(s, "run-20261101T000000Z-aaaa").run()                 # the year baselines the archive waits for
    s.close()
    capsys.readouterr()
    assert cli.main(["--db", db, "archive", "--dry-run"]) == 0
    said = capsys.readouterr().out
    assert json.loads(said.strip().splitlines()[-1])["bills"] == 8 and "CB00000" not in said     # counted, never named
    assert cli.main(["--db", db, "archive", "--run"]) == 0
    s = Store(db)
    bill = s.conn.execute("SELECT bill FROM archive_bills LIMIT 1").fetchone()[0]
    assert s.conn.execute("SELECT COUNT(*) FROM archive_bills").fetchone()[0] == 8
    s.close()
    assert cli.main(["--db", db, "archive-restore", bill]) == 0
    s = Store(db)
    assert s.conn.execute("SELECT COUNT(*) FROM archive_bills WHERE bill=?", (bill,)).fetchone()[0] == 0
    s.close()
    pipeline.run_cycle(db, sync=False, email=False, report=False, log=lambda *a: None)       # a run log for the bundle
    z = tmp_path / "bundle.zip"
    assert cli.main(["--db", db, "diagnose", "--days", "2", "--out", str(z)]) == 0 and z.exists()
    assert any(n.startswith("runs/") for n in diagnose.unpack(z.read_bytes()))
    capsys.readouterr()
    assert cli.main(["--db", db, "self-audit"]) == 0
    assert set(json.loads(capsys.readouterr().out.strip().splitlines()[-1])) >= {"gaps_n", "wasted_n"}
