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
