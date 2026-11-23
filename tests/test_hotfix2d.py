"""
Hotfix 2d: paid claims hidden as copies of another remittance (D1), a missing store taken for a first load (D2), an
appeal's fax left behind when its item's signal changes (D3), and follow-ups faxed for a claim the store shows paid (D4).

Every claim, number, amount and date here is invented (tests/test_remitwatch.remit, tests/test_features.claim). Each
test runs on the server's store file and, where the code runs on both hosts, on the compact in-memory store the
serverless cycle builds, or through that cycle itself (tests/test_cloud.py's fake Dropbox).
"""
from __future__ import annotations

import gzip
import json
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch import cloud, dropbox_sync, insights  # noqa: E402
from remitwatch.store import Store  # noqa: E402
from tests.test_features import claim, svc  # noqa: E402
from tests.test_remitwatch import remit  # noqa: E402

PAY = "20261020"


def _visit(i: int, paid: str = "100.00") -> str:
    """One invented claim: charge $500.00, CLP04 `paid` (the tests below make each remittance balance on CLP04)."""
    c = claim(i, svc("99213", "500.00", paid, paid, f"CAS*CO*45*{500 - float(paid):.2f}~"))
    return c.replace("*500.00*100.00*0.00*", f"*500.00*{paid}*0.00*", 1)


def _era(claims: list[str], bpr: str, ctrl: str, trace_ctrl: str | None = None) -> bytes:
    """A remittance; trace_ctrl makes it carry another file's trace number (a payer that reused one)."""
    data = remit("".join(claims), bpr, seg_count=99, isa_ctrl=ctrl, pay_date=PAY)
    if trace_ctrl:
        data = data.replace(f"TRN*1*TRACE{ctrl}*".encode(), f"TRN*1*TRACE{trace_ctrl}*".encode())
    return data


def _read(store: Store, tmp_path, name: str, data: bytes, mtime: int) -> None:
    """Through the router every remittance goes through (Dropbox, TriZetto, uploads), which decides supersession."""
    d = tmp_path / f"in-{name.replace(' ', '_')}"
    d.mkdir(exist_ok=True)
    p = d / name
    p.write_bytes(data)
    os.utime(p, (mtime, mtime))
    dropbox_sync.ingest_new_files(store, [str(p)], log=lambda *a: None)


@pytest.fixture(params=["file", "compact"])
def kind(request):
    return request.param


def _store(tmp_path, kind: str) -> Store:
    return Store(str(tmp_path / "s.sqlite")) if kind == "file" else Store(":memory:", compact=True)


def _live(store: Store) -> tuple[int, int]:
    """Claims counted (on a transaction that is not superseded) and what they paid, in cents."""
    r = store.conn.execute("""SELECT COUNT(*), COALESCE(SUM(c.paid_cents), 0) FROM claims c JOIN transactions t
                              ON t.transaction_key = c.transaction_key WHERE COALESCE(t.superseded, 0) = 0""").fetchone()
    return tuple(r)


# --------------------------------------------------------------------------- D1: what supersedes what

def test_two_different_remittances_that_share_a_trace_and_a_date_are_both_counted(tmp_path, kind):
    """L2-3: a payer reused a trace number on another day's remittance for other claims. Both are balanced and hold no
    claim in common: two payments, not two copies of one."""
    s = _store(tmp_path, kind)
    _read(s, tmp_path, "a.RMT", _era([_visit(1), _visit(2)], "200.00", "000000311"), 1_790_000_000)
    _read(s, tmp_path, "b.RMT", _era([_visit(3, "60.00")], "60.00", "000000312", trace_ctrl="000000311"), 1_790_100_000)
    assert _live(s) == (3, 26000), "a remittance was hidden as a copy of another that holds none of its claims"
    # two payments, not two versions of one: nothing for a person to settle
    assert s.conn.execute("SELECT value FROM meta WHERE key='supersede:differ'").fetchone() is None


def test_a_claimless_copy_that_does_not_balance_never_hides_the_complete_one(tmp_path, kind):
    """L2-2 as the live store has it: the full remittance (three claims, balanced) and, read later, a copy with the same
    payment and trace that holds no claim and does not add up. The copy must not hide the remittance's claims, and the
    payment must not count twice."""
    s = _store(tmp_path, kind)
    full = _era([_visit(1), _visit(2), _visit(3)], "300.00", "000000321")
    _read(s, tmp_path, "full.RMT", full, 1_790_000_000)
    _read(s, tmp_path, "full (1).RMT", _era([], "300.00", "000000322", trace_ctrl="000000321"), 1_790_100_000)
    assert _live(s) == (3, 30000)
    assert s.conn.execute("SELECT COUNT(*) FROM transactions WHERE COALESCE(superseded,0)=0").fetchone()[0] == 1


def test_a_copy_cut_short_never_supersedes_the_complete_one(tmp_path, kind):
    """A copy cut off in the middle of its third claim (no SE, GE or IEA), read after the whole remittance with a later
    file time: the whole one stands, with all five claims."""
    s = _store(tmp_path, kind)
    whole = _era([_visit(i) for i in range(1, 6)], "500.00", "000000331")
    cut = whole[: whole.index(b"CLP*CB0000003C017") + 40]
    assert b"SE*" not in cut
    _read(s, tmp_path, "era.RMT", whole, 1_790_000_000)
    _read(s, tmp_path, "era (1).RMT", cut, 1_790_100_000)
    assert _live(s) == (5, 50000), "a copy cut short replaced the complete remittance"


def test_a_version_that_holds_everything_another_holds_stands_whatever_order_they_came_in(tmp_path, kind):
    """Two balanced versions of one remittance, one with every claim of the other and one more: the fuller one stands,
    whichever came later, so no claim is hidden and none is counted twice."""
    s = _store(tmp_path, kind)
    small = _era([_visit(1), _visit(2)], "200.00", "000000341")
    big = _era([_visit(1), _visit(2), _visit(3)], "300.00", "000000342", trace_ctrl="000000341")
    _read(s, tmp_path, "big.RMT", big, 1_790_000_000)
    _read(s, tmp_path, "small.RMT", small, 1_790_100_000)          # the smaller one is the newer file
    assert _live(s) == (3, 30000)


def test_two_balanced_versions_that_differ_both_stay_and_a_person_is_told(tmp_path, kind):
    """Two balanced versions of one remittance (claim 1 on both), each holding something the other does not: no rule
    can say which stands. Both are counted and the Data tab's checks say so, with the payer, the trace and the dollars."""
    s = _store(tmp_path, kind)
    _read(s, tmp_path, "v1.RMT", _era([_visit(1), _visit(2)], "200.00", "000000351"), 1_790_000_000)
    _read(s, tmp_path, "v2.RMT", _era([_visit(1, "80.00"), _visit(3)], "180.00", "000000352", trace_ctrl="000000351"), 1_790_100_000)
    assert _live(s) == (4, 38000)
    from remitwatch.detect import ensure_schema
    ensure_schema(s.conn)                   # the home screen asks for its checks once the analysis has made its tables
    health = insights.data_health(s.conn, "2026-10-20", "2026-10-21")
    item = next((h for h in health if h.get("key") == "remit_versions"), None)
    assert item and not item["ok"] and "Two versions of one remittance differ" in item["title"]
    assert "TRACE000000351" in item["detail"] and "ANTHEM" in item["detail"]


def test_a_broken_copy_holding_a_claim_the_complete_one_does_not_is_shown_to_a_person(tmp_path, kind):
    """A complete, balanced remittance with claims 1 and 2, then a later version with claim 3 added that was cut short in
    transfer (it ends before its SE). The complete one stands (a broken copy never supersedes it), so claim 3, only on
    the broken copy, is not counted: the Data tab's checks say so, with what it paid, instead of dropping it silently."""
    s = _store(tmp_path, kind)
    _read(s, tmp_path, "v1.RMT", _era([_visit(1), _visit(2)], "200.00", "000000371"), 1_790_000_000)
    later = _era([_visit(1), _visit(2), _visit(3, "70.00")], "270.00", "000000372", trace_ctrl="000000371")
    _read(s, tmp_path, "v2.RMT", later[: later.index(b"SE*")], 1_790_100_000)
    assert s.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 2      # the broken copy was read
    assert _live(s) == (2, 20000)
    from remitwatch.detect import ensure_schema
    ensure_schema(s.conn)
    item = next((h for h in insights.data_health(s.conn, "2026-10-20", "2026-10-21") if h.get("key") == "remit_copy_lost"), None)
    assert item and not item["ok"] and item["claims"] == 1 and item["cents"] == 7000, item
    assert "TRACE000000371" in item["detail"] and "$70.00" in item["detail"]
    assert not any(h.get("key") == "remit_versions" for h in insights.data_health(s.conn, "2026-10-20", "2026-10-21"))


def test_copies_of_one_remittance_still_keep_only_the_newest(tmp_path, kind):
    """The rule that was right stays right: the same remittance delivered twice (identical claims and payment, another
    envelope) counts once, the newer copy standing."""
    s = _store(tmp_path, kind)
    first = _era([_visit(1), _visit(2)], "200.00", "000000361")
    again = first.replace(b"*000000361*0*P*", b"*000000362*0*P*").replace(b"IEA*1*000000361~", b"IEA*1*000000362~")
    again = again.replace(b"DTM*405*20251120~", b"DTM*405*20251121~")          # a production date: another fingerprint
    _read(s, tmp_path, "one.RMT", first, 1_790_000_000)
    _read(s, tmp_path, "two.RMT", again, 1_790_100_000)
    assert _live(s) == (2, 20000)
    newest = s.conn.execute("SELECT source_name FROM transactions WHERE COALESCE(superseded,0)=0").fetchall()
    assert [r[0] for r in newest] == ["two.RMT"]


def _old_flags(conn: sqlite3.Connection) -> None:
    """What the release before this one left in a store: the newest copy flagged as the only one, whatever it held, and
    no record of which supersession rule decided it."""
    conn.execute("UPDATE transactions SET superseded=0")
    conn.execute("""UPDATE transactions SET superseded=1 WHERE transaction_key IN (SELECT transaction_key FROM transactions
                    WHERE trace_number='TRACE000000311' ORDER BY source_name LIMIT 1)""")
    conn.execute("DELETE FROM meta WHERE key LIKE 'supersede:%'")
    conn.commit()


def test_a_store_decided_by_the_old_rule_is_decided_again_once_on_the_server(tmp_path, monkeypatch):
    for k in ("DROPBOX_APP_KEY", "DROPBOX_REFRESH_TOKEN", "TRIZETTO_HOST", "SMTP_HOST", "REPORT_TO", "ALERT_WEBHOOK_URL"):
        monkeypatch.delenv(k, raising=False)
    from remitwatch.pipeline import run_cycle
    db = str(tmp_path / "srv" / "remitwatch.sqlite")
    s = Store(db)
    _read(s, tmp_path, "a.RMT", _era([_visit(1), _visit(2)], "200.00", "000000311"), 1_790_000_000)
    _read(s, tmp_path, "b.RMT", _era([_visit(3, "60.00")], "60.00", "000000312", trace_ctrl="000000311"), 1_790_100_000)
    _old_flags(s.conn)
    assert _live(s) != (3, 26000)
    s.close()
    out = run_cycle(db, sync=False, email=False, report=False, report_dir=str(tmp_path / "rep"), log=lambda *a: None)
    assert "error" not in out and "detect" in out, out
    s = Store(db)
    assert _live(s) == (3, 26000)
    # once: a later run with nothing new leaves the flags as they are
    s.conn.execute("UPDATE transactions SET superseded=1 WHERE source_name='b.RMT'")
    s.conn.commit()
    s.close()
    run_cycle(db, sync=False, email=False, report=False, report_dir=str(tmp_path / "rep"), log=lambda *a: None)
    s = Store(db)
    assert s.conn.execute("SELECT superseded FROM transactions WHERE source_name='b.RMT'").fetchone()[0] == 1
    s.close()


def _cloud_box(tmp_path, monkeypatch):
    from remitwatch.state import DropboxState
    from tests.fakes import FakeDropbox
    for k in ("DROPBOX_APP_KEY", "DROPBOX_APP_SECRET", "DROPBOX_REFRESH_TOKEN", "SMTP_HOST", "ALERT_WEBHOOK_URL", "REPORT_TO", "CRON_SECRET",
              "REMITWATCH_STATE_DIR", "ANTHROPIC_API_KEY", "VERCEL", "VERCEL_ENV", "VERCEL_PROJECT_PRODUCTION_URL", "TRIZETTO_HOST"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("REMITWATCH_TMP", str(tmp_path / "scratch"))
    monkeypatch.setenv("DROPBOX_ROOT_PATH", "/Trizetto Data")
    root = tmp_path / "dbx"
    client = FakeDropbox(str(root))
    return root, client, DropboxState(client, "/RemitWatch-state")


def _cycle(state, client, **kw):
    kw.setdefault("budget_s", 600)
    return cloud.run_cloud_cycle(state, client, email=False, log=lambda *a: None, **kw)


def _serving(state, tmp_path) -> sqlite3.Connection:
    p = tmp_path / "serving-copy.sqlite"
    p.write_bytes(gzip.decompress(state.get(cloud.SERVING)))
    return sqlite3.connect(str(p))


def test_serverless_counts_both_remittances_and_tells_of_versions_that_differ(tmp_path, monkeypatch):
    root, client, state = _cloud_box(tmp_path, monkeypatch)
    folder = root / "Trizetto Data"
    folder.mkdir(parents=True)
    files = {"a.RMT": _era([_visit(1), _visit(2)], "200.00", "000000311"),
             "b.RMT": _era([_visit(3, "60.00")], "60.00", "000000312", trace_ctrl="000000311"),
             "v1.RMT": _era([_visit(11), _visit(12)], "200.00", "000000351"),
             "v2.RMT": _era([_visit(11, "80.00"), _visit(13)], "180.00", "000000352", trace_ctrl="000000351")}
    for n, (name, data) in enumerate(files.items()):
        (folder / name).write_bytes(data)
        os.utime(folder / name, (1_790_000_000 + n, 1_790_000_000 + n))
    out = _cycle(state, client)
    assert "error" not in out, out
    store, existed = cloud.load_raw(state)
    assert existed and _live(store) == (7, 64000)
    serving = _serving(state, tmp_path)
    item = next((h for h in insights.data_health(serving, "2026-10-20", "2026-10-21") if h.get("key") == "remit_versions"), None)
    assert item and "TRACE000000351" in item["detail"]


def test_serverless_store_decided_by_the_old_rule_is_decided_again_once(tmp_path, monkeypatch):
    root, client, state = _cloud_box(tmp_path, monkeypatch)
    folder = root / "Trizetto Data"
    folder.mkdir(parents=True)
    (folder / "a.RMT").write_bytes(_era([_visit(1), _visit(2)], "200.00", "000000311"))
    (folder / "b.RMT").write_bytes(_era([_visit(3, "60.00")], "60.00", "000000312", trace_ctrl="000000311"))
    assert "error" not in _cycle(state, client)
    store, _ = cloud.load_raw(state)
    _old_flags(store.conn)
    cloud.save_raw(state, store)
    store.close()
    status = json.loads(state.get(cloud.STATUS))
    status.pop(cloud.IMAGE_KEY, None)        # saved by the release before this one, which recorded no image
    state.put(cloud.STATUS, json.dumps(status).encode())
    out = _cycle(state, client)              # no new file: the rule changed, so this run decides again, and analyses
    assert "error" not in out and "detect" in out, out
    store, _ = cloud.load_raw(state)
    assert _live(store) == (3, 26000)


# --------------------------------------------------------------------------- D2: a missing store is a stop, not a first load

def _two_runs_folder(root):
    folder = root / "Trizetto Data"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "a.RMT").write_bytes(_era([_visit(1), _visit(2)], "200.00", "000000371"))
    return folder


def test_serverless_a_missing_store_image_stops_the_run_and_changes_nothing(tmp_path, monkeypatch):
    """L2-19: the store image is moved out of the state folder while status.json and cursor.json show earlier runs. The
    next run must not start from an empty store and save it as the practice's."""
    root, client, state = _cloud_box(tmp_path, monkeypatch)
    _two_runs_folder(root)
    assert "error" not in _cycle(state, client)
    before = {n: state.get(n) for n in (cloud.SERVING, cloud.CURSOR)}
    moved = state.get(cloud.RAW)
    state.delete(cloud.RAW)                                   # dragged to another folder by mistake
    out = _cycle(state, client)
    assert out.get("error") == ("The store image is missing from the state folder. Nothing was changed. Put it back, or "
                                "restore it from Dropbox's version history.")
    assert state.get(cloud.RAW) is None, "an empty store was saved as the practice's"
    assert {n: state.get(n) for n in (cloud.SERVING, cloud.CURSOR)} == before
    assert json.loads(state.get(cloud.STATUS))["error"] == out["error"] and state.meta(cloud.LOCK) is None
    with pytest.raises(cloud.StoreImageProblem):
        cloud.load_raw(state)
    state.put(cloud.RAW, moved)                               # put back: the next run goes on as before
    assert "error" not in _cycle(state, client)


def test_serverless_a_first_load_needs_both_status_and_cursor_absent(tmp_path, monkeypatch):
    root, client, state = _cloud_box(tmp_path, monkeypatch)
    _two_runs_folder(root)
    state.put(cloud.CURSOR, json.dumps({"cursor": "AAAinvented", "root": "/Trizetto Data"}).encode())
    assert "missing" in (_cycle(state, client).get("error") or "")      # a cursor alone is an earlier run
    state.delete(cloud.CURSOR)
    state.delete(cloud.STATUS)
    out = _cycle(state, client)                                          # nothing at all: a first load
    assert "error" not in out and out["ingest"]["remits"] == 1


def test_serverless_runs_that_never_saved_a_store_still_start_from_empty(tmp_path, monkeypatch):
    """A new site whose first runs had nothing to read leaves status.json behind but no store image and no cursor: no store
    ever existed there, so the next run is still a first load. Refusing it would stop that site for ever."""
    root, client, state = _cloud_box(tmp_path, monkeypatch)
    state.put(cloud.STATUS, json.dumps({"started": "2026-11-20T10:17:00+00:00", "ingest": {"remits": 0}}).encode())
    _two_runs_folder(root)
    out = _cycle(state, client)
    assert "error" not in out and out["ingest"]["remits"] == 1


def test_serverless_a_status_written_before_the_image_record_proves_a_store_until_it_is_back(tmp_path, monkeypatch):
    """The live site's status.json was written by a release that recorded no image, only the size of the store it saved.
    With the image gone that is a stop, and it stays one run after run (the refusal keeps the proof in status.json) until
    the image is put back. An unreadable status.json is a stop as well: nobody can tell that no store existed."""
    root, client, state = _cloud_box(tmp_path, monkeypatch)
    _two_runs_folder(root)
    state.put(cloud.STATUS, json.dumps({"store": {"gz_bytes": 106831319, "save_seconds": 11.0},
                                        "totals": {"remit_files": 32718}}).encode())
    for _ in range(2):
        out = _cycle(state, client)
        assert "missing" in (out.get("error") or ""), out
        assert state.get(cloud.RAW) is None
    state.put(cloud.STATUS, b"{not json")
    for _ in range(2):
        assert "missing" in (_cycle(state, client).get("error") or "")
        assert state.get(cloud.RAW) is None


def test_serverless_records_the_image_it_saved_and_refuses_another(tmp_path, monkeypatch):
    """Each save records the image's size and sha256 in status.json (at once, so a run killed after the save still leaves
    it right). An image that is not the one last saved (an older copy put back) is refused until a person accepts it."""
    import hashlib
    root, client, state = _cloud_box(tmp_path, monkeypatch)
    folder = _two_runs_folder(root)
    assert "error" not in _cycle(state, client)
    older = state.get(cloud.RAW)
    (folder / "b.RMT").write_bytes(_era([_visit(3)], "100.00", "000000372"))
    monkeypatch.setattr(cloud, "write_status", lambda *a, **k: None)     # the run is killed before its last word
    _cycle(state, client)
    monkeypatch.undo()
    monkeypatch.setenv("REMITWATCH_TMP", str(tmp_path / "scratch"))
    monkeypatch.setenv("DROPBOX_ROOT_PATH", "/Trizetto Data")
    image = state.get(cloud.RAW)
    rec = json.loads(state.get(cloud.STATUS))["image"]
    assert rec["size"] == len(image) and rec["sha256"] == hashlib.sha256(image).hexdigest()
    assert "error" not in _cycle(state, client)                           # the newest image, as recorded: it runs
    assert json.loads(state.get(cloud.STATUS))["image"]["sha256"] == hashlib.sha256(state.get(cloud.RAW)).hexdigest()
    state.put(cloud.RAW, older)                                           # an older copy put back
    out = _cycle(state, client)
    assert "not the one the last run saved" in (out.get("error") or "")
    assert state.get(cloud.RAW) == older
    # the documented recovery: a person accepts the image now in the folder
    monkeypatch.setenv("REMITWATCH_STATE_DIR", str(root / "RemitWatch-state"))
    from remitwatch.cli import main
    assert main(["accept-store", "--state-folder"]) == 0
    assert "error" not in _cycle(state, client)


def _server_world(tmp_path, monkeypatch):
    for k in ("DROPBOX_APP_KEY", "DROPBOX_REFRESH_TOKEN", "TRIZETTO_HOST", "SMTP_HOST", "REPORT_TO", "ALERT_WEBHOOK_URL", "SRFAX_ACCESS_ID"):
        monkeypatch.delenv(k, raising=False)
    data = tmp_path / "data"
    files = tmp_path / "incoming"
    files.mkdir()
    (files / "a.RMT").write_bytes(_era([_visit(1), _visit(2)], "200.00", "000000381"))
    return str(data / "remitwatch.sqlite"), str(files)


def _server_cycle(db, local=None):
    from remitwatch.pipeline import run_cycle
    return run_cycle(db, sync=False, local=local, email=False, report=False, report_dir=os.path.join(os.path.dirname(db), "rep"),
                     log=lambda *a: None)


def test_server_a_missing_store_file_is_a_stop_not_a_new_empty_store(tmp_path, monkeypatch):
    """A store file that is missing where a store was made (a volume not mounted, a file moved) is a stop: no new empty
    store is made, the Data tab says so, and no backup of an empty store pushes the good ones out."""
    db, local = _server_world(tmp_path, monkeypatch)
    assert "error" not in _server_cycle(db, local)
    assert os.path.exists(db + ".marker")
    os.rename(db, db + ".moved")
    out = _server_cycle(db)
    assert out.get("error") and "store file is missing" in out["error"]
    assert not os.path.exists(db) or os.path.getsize(db) == 0, "a new empty store was made"
    from remitwatch.pipeline import status
    st = status(db)
    assert "store file is missing" in (st.get("last_cycle") or {}).get("error", "")
    assert not os.path.exists(db) or os.path.getsize(db) == 0
    from remitwatch.cli import backup_store
    with pytest.raises(RuntimeError):
        backup_store(db, str(tmp_path / "backups"))
    assert not os.listdir(tmp_path / "backups") if os.path.exists(tmp_path / "backups") else True
    os.rename(db + ".moved", db)
    assert "error" not in _server_cycle(db)


def test_server_a_file_made_again_without_the_practice_s_data_is_a_stop(tmp_path, monkeypatch):
    """Another part of the site opening the path makes a new file at once (the fax lock writes a table into it): a store
    that held remittances and now holds none is a stop too, until a person says otherwise."""
    db, local = _server_world(tmp_path, monkeypatch)
    assert "error" not in _server_cycle(db, local)
    os.remove(db)
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    c.commit()
    c.close()
    out = _server_cycle(db)
    assert out.get("error") and "holds no remittance" in out["error"]
    # the person decides: start again from an empty store
    from remitwatch.cli import main
    assert main(["--db", db, "accept-store"]) == 0
    assert "error" not in _server_cycle(db, local)


def test_server_first_start_makes_the_store_and_its_marker(tmp_path, monkeypatch):
    db, local = _server_world(tmp_path, monkeypatch)
    assert not os.path.exists(db) and not os.path.exists(db + ".marker")
    out = _server_cycle(db, local)
    assert "error" not in out and os.path.exists(db) and os.path.exists(db + ".marker")
    assert json.loads(open(db + ".marker").read())["held_data"] is True


def test_server_web_start_does_not_make_an_empty_store_where_one_was(tmp_path, monkeypatch):
    db, local = _server_world(tmp_path, monkeypatch)
    assert "error" not in _server_cycle(db, local)
    os.rename(db, db + ".moved")
    from remitwatch import api
    api.ensure_server_schema(db)
    assert not os.path.exists(db) or os.path.getsize(db) == 0


# --------------------------------------------------------------------------- D3: an appeal's fax follows its item's heir

DOWN_BILLS = [f"CB{200 + i:07d}C017" for i in range(3)]
XHR = {"X-Requested-With": "remitwatch"}
SENT_AT = "2026-10-01T10:00:00+00:00"


def _downcode_claims() -> list[str]:
    """Anthem visits: enough clean 99214 and 99213 for a baseline, and three billed at the 99214 fee and paid as 99213
    (DOWNCODE_CHARGE: the higher level known only from the charge)."""
    from tests.test_features import clean_visits
    claims = clean_visits(1, 25, "99214", "223.65", "117.60") + clean_visits(100, 25, "99213", "156.66", "76.15")
    return claims + [claim(200 + i, svc("99213", "223.65", "76.15", "76.15", "CAS*CO*45*147.50~")) for i in range(3)]


def _write_remits(folder, claims: list[str], prefix: str, ctrl: int) -> list[str]:
    os.makedirs(folder, exist_ok=True)
    paths = []
    for n, i in enumerate(range(0, len(claims), 20)):
        p = os.path.join(str(folder), f"{prefix}{n:03d}.RMT")
        with open(p, "wb") as fh:
            fh.write(remit("".join(claims[i:i + 20]), "100.00", seg_count=99, isa_ctrl=f"{ctrl + n:09d}", pay_date="20260415"))
        paths.append(p)
    return paths


def _downcode_store(tmp_path, kind) -> Store:
    from remitwatch.detect import Detector
    s = _store(tmp_path, kind)
    s.ingest_835_paths(_write_remits(tmp_path / "remits", _downcode_claims(), "d", 900))
    assert Detector(s, run_id="r1").run().get("DOWNCODE_CHARGE") == 3
    return s


def _claim_files_arrive(store: Store) -> None:
    """The claim files prove 99214 was billed: the same lines become DOWNCODE_VS_BILLED, each with a new item id."""
    from remitwatch.x12_837 import DDL as DDL_837
    store.conn.executescript(DDL_837)
    store.conn.executemany("""INSERT INTO lines_837 (line_837_key, claim_837_key, patient_control_number, line_seq, procedure_code,
                              charge_cents, units, service_date) VALUES (?,?,?,?,?,?,?,?)""",
                           [(f"k{i}", f"c{i}", b, 1, "99214", 22365, 1, "2026-04-01") for i, b in enumerate(DOWN_BILLS)])
    store.conn.commit()


def _fax_record(fid: str, key: str = "out:fake:1", purpose: str = "appeal", at: str = SENT_AT) -> dict:
    return {"fax_key": key, "direction": "out", "finding_id": fid, "packet_id": "pk1", "payer_group": "Anthem / BCBS", "number": "8005550142",
            "purpose": purpose, "level": 1, "provider": "fake", "provider_id": "F1" if key.endswith("1") else "F2", "status": "sent",
            "created_at": at, "sent_at": at}


def _fax_the_appeal(conn, fid: str) -> None:
    from remitwatch import actions
    actions.apply_status(conn, fid, "appealed", "Faxed to Anthem / BCBS at (800) 555-0142 (fax F1, 6 pages).", actor="biller", at=SENT_AT)
    actions.apply_fax(conn, _fax_record(fid))
    conn.commit()


def _first_downcode(conn) -> str:
    return conn.execute("SELECT finding_id FROM findings WHERE signal='DOWNCODE_CHARGE' ORDER BY patient_control_number").fetchone()[0]


def _heir(conn) -> str:
    rows = conn.execute("SELECT finding_id FROM findings WHERE signal='DOWNCODE_VS_BILLED' AND status='appealed'").fetchall()
    assert len(rows) == 1, rows
    return rows[0][0]


def test_a_faxed_appeal_keeps_its_fax_its_packet_its_history_and_its_chase_when_its_signal_changes(tmp_path, kind):
    """0c-1: new claim files prove the billed level, so detection gives the item's money a new id. What was recorded on
    the old id follows the heir: its fax (so the follow-ups keep coming), its packet and its history."""
    from remitwatch import fax
    from remitwatch.detect import Detector
    s = _downcode_store(tmp_path, kind)
    c = s.conn
    old = _first_downcode(c)
    _fax_the_appeal(c, old)
    if kind == "file":                 # packets are rows on a server (on Vercel they are files named by the packet id)
        from remitwatch.notes import ensure_packets
        ensure_packets(c)
        c.execute("INSERT INTO appeal_packets (packet_id, finding_id, created_at, mode, subject) VALUES ('pk1', ?, ?, 'template', 's')", (old, SENT_AT))
        c.commit()
    assert [d["action"] for d in fax.followups(c, today="2026-11-20")] == ["second_request"]          # the chase, before
    _claim_files_arrive(s)
    Detector(s, run_id="r2").run()
    heir = _heir(c)
    assert heir != old
    assert c.execute("SELECT COUNT(*) FROM faxes WHERE finding_id=?", (heir,)).fetchone()[0] == 1, "the item lost its fax"
    assert c.execute("SELECT COUNT(*) FROM faxes WHERE finding_id=?", (old,)).fetchone()[0] == 0
    assert [(d["finding_id"], d["action"]) for d in fax.followups(c, today="2026-11-20")] == [(heir, "second_request")], \
        "the appeal is no longer chased"
    history = [(r[0], r[1]) for r in c.execute("SELECT kind, to_status FROM finding_events WHERE finding_id=? ORDER BY id", (heir,))]
    assert ("manual", "appealed") in history and ("signal_changed", "appealed") in history, history
    if kind == "file":
        assert c.execute("SELECT finding_id FROM appeal_packets WHERE packet_id='pk1'").fetchone()[0] == heir
    Detector(s, run_id="r3").run()           # a later run with nothing new keeps all of it on the heir
    assert c.execute("SELECT COUNT(*) FROM faxes WHERE finding_id=?", (heir,)).fetchone()[0] == 1


def test_what_is_recorded_for_the_old_id_after_the_change_lands_on_the_heir(tmp_path, kind):
    """A page opened before the run: its fax and its history line, written for the old id once the run gave the money a
    new one, land on the heir (on Vercel they arrive through the journal)."""
    from remitwatch import actions
    from remitwatch.detect import Detector
    s = _downcode_store(tmp_path, kind)
    c = s.conn
    old = _first_downcode(c)
    actions.apply_status(c, old, "appealed", "Sent", actor="biller", at=SENT_AT)
    c.commit()
    _claim_files_arrive(s)
    Detector(s, run_id="r2").run()
    heir = _heir(c)
    assert actions.apply(c, {"kind": "fax", "record": _fax_record(old)})
    assert actions.apply(c, {"kind": "event", "finding_id": old, "note": "Faxed to the payer", "actor": "biller", "at": "2026-10-02T10:00:00+00:00"})
    c.commit()
    assert c.execute("SELECT finding_id FROM faxes WHERE fax_key='out:fake:1'").fetchone()[0] == heir
    assert c.execute("SELECT COUNT(*) FROM finding_events WHERE finding_id=? AND note='Faxed to the payer'", (heir,)).fetchone()[0] == 1


def _site(tmp_path, monkeypatch, db: str):
    """The API on a server store, with a fake fax line and a practice whose details are filled in."""
    from fastapi.testclient import TestClient

    import remitwatch.api as api_mod
    from remitwatch import appeals, fax
    from tests.fake_fax import FakeLine
    monkeypatch.setattr(api_mod, "DB_PATH", db)
    monkeypatch.setattr(api_mod, "PASSWORD", "")
    for k, v in {"name": "Test Dermatology", "address": "1 Main St, Anytown KY", "tin": "61-0000000", "npi": "1999999999",
                 "phone": "(270) 555-0100", "fax": "(270) 555-0101", "email": "billing@example.org"}.items():
        monkeypatch.setitem(appeals.PRACTICE, k, v)
    for k in ("ANTHROPIC_API_KEY", "MODMED_CLIENT_ID", "SRFAX_ACCESS_ID"):
        monkeypatch.delenv(k, raising=False)
    line = FakeLine()
    monkeypatch.setattr(fax, "provider", lambda env=None: line)
    monkeypatch.setattr(fax, "configured", lambda env=None: True)
    return TestClient(api_mod.app), line, api_mod


def test_the_heir_s_panel_shows_the_fax_and_a_second_appeal_is_not_faxed(tmp_path, monkeypatch):
    """0c-1 step 6: the skeptic sets the heir back to To do and presses Appeal by fax: the appeal already went out for
    this claim and level, so nothing is faxed again."""
    from remitwatch import actions
    from remitwatch.detect import Detector
    s = _downcode_store(tmp_path, "file")
    c = s.conn
    old = _first_downcode(c)
    _fax_the_appeal(c, old)
    actions.apply_payer_fax(c, {"payer_group": "Anthem / BCBS", "number": "8005550142", "actor": "owner", "at": "2026-09-01T00:00:00+00:00"})
    c.commit()
    _claim_files_arrive(s)
    Detector(s, run_id="r2").run()
    heir = _heir(c)
    s.close()
    client, line, _api = _site(tmp_path, monkeypatch, str(tmp_path / "s.sqlite"))
    d = client.get(f"/api/findings/{heir}").json()
    assert [x["direction"] for x in d["faxes"]] == ["out"] and d["finding"]["faxed_at"], "the heir's panel shows no fax"
    assert any(e["kind"] == "manual" and e["to_status"] == "appealed" for e in d["events"])
    assert client.post(f"/api/findings/{heir}/status", json={"status": "open", "notes": "check again", "seen_status": "appealed"},
                       headers=XHR).status_code == 200
    r = client.post("/api/appeals/send", json={"finding_id": heir, "stale_ok": True}, headers=XHR).json()
    assert r["status"] == "skipped" and "already faxed" in r["reason"], r
    assert line.sent == {}


def test_a_second_appeal_for_the_same_claim_line_from_another_item_is_not_reserved(tmp_path, monkeypatch):
    """The last check before a send, in the step that reserves it: an appeal at the same level for the same claim line,
    already faxed from another item, stops it (whatever id the first one sits under now)."""
    s = _downcode_store(tmp_path, "file")
    c = s.conn
    first = _first_downcode(c)
    _fax_the_appeal(c, first)
    row = dict(c.execute("SELECT * FROM findings WHERE finding_id=?", (first,)).fetchone())
    other = "f" * 24
    c.execute("INSERT INTO findings (finding_id, signal, category, line_key, claim_key, status, payer_group) VALUES (?,?,?,?,?,?,?)",
              (other, "UNDERPAID_VS_OWN_RATE", "underpayment", row["line_key"], row["claim_key"], "open", row["payer_group"]))
    c.commit()
    s.close()
    _client, line, api = _site(tmp_path, monkeypatch, str(tmp_path / "s.sqlite"))
    with pytest.raises(api.SendBlocked) as e:
        api._reserve(line, dict(row, finding_id=other), "8005550142", packet_id=None, purpose="appeal", level=1, actor="t",
                     subject=None, extra=None)
    assert "already faxed" in str(e.value)
    # the item that has the appeal still gets its follow-ups reserved
    rec = api._reserve(line, row, "8005550142", packet_id=None, purpose="second_request", level=1, actor="t", subject=None, extra=None)
    assert rec["status"] == "sending"


def test_serverless_a_faxed_appeal_follows_its_heir(tmp_path, monkeypatch):
    """The same on the serverless host: the status and the fax come through the journal, the claim files reach the store,
    and the dashboard's copy shows the fax on the heir, which the follow-ups chase. A fax journaled for the old id after
    that lands on the heir too."""
    from remitwatch import fax
    root, client, state = _cloud_box(tmp_path, monkeypatch)
    _write_remits(root / "Trizetto Data", _downcode_claims(), "d", 900)
    assert "error" not in _cycle(state, client)
    store, _ = cloud.load_raw(state)
    old = _first_downcode(store.conn)
    store.close()
    cloud.record_action(state, {"kind": "status", "finding_id": old, "status": "appealed", "notes": "Faxed", "actor": "biller", "at": SENT_AT})
    cloud.record_action(state, {"kind": "fax", "record": _fax_record(old)})
    store, _ = cloud.load_raw(state)
    _claim_files_arrive(store)
    cloud.save_raw(state, store)
    store.close()
    out = _cycle(state, client, force_detect=True)
    assert "error" not in out and "detect" in out, out
    serving = _serving(state, tmp_path)
    heir = _heir(serving)
    assert serving.execute("SELECT COUNT(*) FROM faxes WHERE finding_id=?", (heir,)).fetchone()[0] == 1
    assert [(d["finding_id"], d["action"]) for d in fax.followups(serving, today="2026-11-20")] == [(heir, "second_request")]
    serving.close()
    cloud.record_action(state, {"kind": "fax", "record": _fax_record(old, key="out:fake:2", purpose="second_request",
                                                                     at="2026-11-20T10:00:00+00:00")})
    assert "error" not in _cycle(state, client)
    store, _ = cloud.load_raw(state)
    assert store.conn.execute("SELECT finding_id FROM faxes WHERE fax_key='out:fake:2'").fetchone()[0] == heir


# --------------------------------------------------------------------------- D4: no chase for a claim the store shows paid

def _denial_world(tmp_path, kind) -> tuple[Store, str]:
    """0c-2: an invented Anthem medical-necessity denial (claim 210) appealed by fax on 2026-10-12; a later remittance pays it
    on 2026-10-19 and the run closes it as Paid; the skeptic sets it back to To do, then Sent again; the next run keeps
    Sent (a person set it), with the later payment shown."""
    from remitwatch import actions
    from remitwatch.detect import Detector
    from tests.test_features import clean_visits
    s = _store(tmp_path, kind)
    first = clean_visits(1, 25, "99214", "223.65", "117.60") + [claim(210, svc("99214", "223.65", "0.00", None, "CAS*CO*50*223.65~"))]
    paths = []
    for n, i in enumerate(range(0, len(first), 20)):
        p = tmp_path / f"r{n:03d}.RMT"
        p.write_bytes(remit("".join(first[i:i + 20]), "100.00", seg_count=99, isa_ctrl=f"{800 + n:09d}", pay_date="20261010"))
        paths.append(str(p))
    s.ingest_835_paths(paths)
    Detector(s, run_id="r1").run()
    c = s.conn
    fid = c.execute("SELECT finding_id FROM findings WHERE signal='DENIED_MEDICAL_NECESSITY'").fetchone()[0]
    actions.apply_status(c, fid, "appealed", "Faxed", actor="biller", at="2026-10-12T10:00:00+00:00")
    actions.apply_fax(c, dict(_fax_record(fid, at="2026-10-12T10:00:00+00:00"), packet_id=None))
    c.commit()
    later = tmp_path / "later.RMT"
    later.write_bytes(remit(claim(210, svc("99214", "223.65", "117.60", "117.60", "CAS*CO*45*106.05~")), "117.60", seg_count=30,
                            isa_ctrl="000000777", pay_date="20261019"))
    s.ingest_835_paths([str(later)])
    Detector(s, run_id="r2").run()
    assert c.execute("SELECT status FROM findings WHERE finding_id=?", (fid,)).fetchone()[0] == "resolved"
    actions.apply_status(c, fid, "open", "not in EMA yet", actor="biller")
    actions.apply_status(c, fid, "appealed", None, actor="biller")
    c.commit()
    Detector(s, run_id="r3").run()
    assert c.execute("SELECT status FROM findings WHERE finding_id=?", (fid,)).fetchone()[0] == "appealed"
    return s, fid


PAID_WHY = "You marked this Sent, but a remittance shows it paid on 2026-10-19. Check it before RemitWatch chases it."


def test_no_second_request_is_faxed_for_a_claim_the_store_shows_paid(tmp_path, kind):
    """0c-2: whatever the item's status says, the follow-up engine sends no second request, next-level appeal or payment
    request for money the store shows paid; it lists the item for a person, with why."""
    from remitwatch import fax
    s, fid = _denial_world(tmp_path, kind)
    due = [d for d in fax.followups(s.conn, today="2026-11-20") if d["finding_id"] == fid]
    assert due and all(d["action"] == "person" for d in due), due
    assert due[0]["why"] == PAID_WHY and not due[0].get("resend")


def test_the_follow_up_run_holds_a_paid_claim_and_says_so_once_in_its_history(tmp_path, monkeypatch):
    from remitwatch import actions
    s, fid = _denial_world(tmp_path, "file")
    actions.apply_payer_fax(s.conn, {"payer_group": "Anthem / BCBS", "number": "8005550142", "actor": "owner", "at": "2026-09-01T00:00:00+00:00"})
    s.conn.commit()
    s.close()
    _client, line, api = _site(tmp_path, monkeypatch, str(tmp_path / "s.sqlite"))
    for n in range(2):          # the hourly run, twice, an hour apart: one line in the history
        monkeypatch.setattr(api, "_now_iso", lambda n=n: f"2026-11-20T1{n}:00:00+00:00")
        res = api._send_followups(line, "daily run")
        mine = [r for r in res["results"] if r["finding_id"] == fid]
        assert mine and mine[0]["status"] == "held" and mine[0]["reason"] == PAID_WHY, res
    assert line.sent == {}
    s = Store(str(tmp_path / "s.sqlite"))
    assert s.conn.execute("SELECT COUNT(*) FROM finding_events WHERE finding_id=? AND note=?", (fid, PAID_WHY)).fetchone()[0] == 1
    s.close()


def test_serverless_the_follow_up_run_holds_a_paid_claim_and_says_so_once(tmp_path, monkeypatch):
    """The same on the serverless host: the statuses, the fax and the payer's number come through the journal, the runs
    close the claim as paid and keep the person's Sent, and the follow-up run (an instance with the dashboard's copy)
    faxes nothing, holds the item for a person, and journals one line for its history."""
    from remitwatch import api, fax
    from tests.fake_fax import FakeLine
    from tests.test_features import clean_visits
    root, client, state = _cloud_box(tmp_path, monkeypatch)
    folder = root / "Trizetto Data"
    folder.mkdir(parents=True)
    first = clean_visits(1, 25, "99214", "223.65", "117.60") + [claim(210, svc("99214", "223.65", "0.00", None, "CAS*CO*50*223.65~"))]
    for n, i in enumerate(range(0, len(first), 20)):
        (folder / f"r{n:03d}.RMT").write_bytes(remit("".join(first[i:i + 20]), "100.00", seg_count=99, isa_ctrl=f"{800 + n:09d}",
                                                     pay_date="20261010"))
    assert "error" not in _cycle(state, client)
    store, _ = cloud.load_raw(state)
    fid = store.conn.execute("SELECT finding_id FROM findings WHERE signal='DENIED_MEDICAL_NECESSITY'").fetchone()[0]
    store.close()
    for a in ({"kind": "status", "finding_id": fid, "status": "appealed", "notes": "Faxed", "actor": "biller", "at": "2026-10-12T10:00:00+00:00"},
              {"kind": "fax", "record": dict(_fax_record(fid, at="2026-10-12T10:00:00+00:00"), packet_id=None)},
              {"kind": "payer_fax", "payer_group": "Anthem / BCBS", "number": "8005550142", "actor": "owner", "at": "2026-09-01T00:00:00+00:00"}):
        cloud.record_action(state, a)
    assert "error" not in _cycle(state, client)
    (folder / "later.RMT").write_bytes(remit(claim(210, svc("99214", "223.65", "117.60", "117.60", "CAS*CO*45*106.05~")), "117.60",
                                             seg_count=30, isa_ctrl="000000777", pay_date="20261019"))
    assert "error" not in _cycle(state, client)
    store, _ = cloud.load_raw(state)
    assert store.conn.execute("SELECT status FROM findings WHERE finding_id=?", (fid,)).fetchone()[0] == "resolved"
    store.close()
    # the skeptic, after the run closed it: To do, then Sent again (now, as on the server)
    cloud.record_action(state, {"kind": "status", "finding_id": fid, "status": "open", "notes": "not in EMA yet", "actor": "biller"})
    cloud.record_action(state, {"kind": "status", "finding_id": fid, "status": "appealed", "actor": "biller"})
    assert "error" not in _cycle(state, client, force_detect=True)
    store, _ = cloud.load_raw(state)
    assert store.conn.execute("SELECT status FROM findings WHERE finding_id=?", (fid,)).fetchone()[0] == "appealed"
    store.close()
    monkeypatch.setenv("REMITWATCH_MODE", "serverless")
    monkeypatch.setenv("REMITWATCH_STATE_DIR", str(root / "RemitWatch-state"))
    monkeypatch.setattr(cloud, "SERVING_COPY", cloud.ServingCopy())
    line = FakeLine()
    monkeypatch.setattr(fax, "provider", lambda env=None: line)
    monkeypatch.setattr(fax, "configured", lambda env=None: True)
    for n in range(2):          # the daily fax run, twice, an hour apart: one line for the history
        monkeypatch.setattr(api, "_now_iso", lambda n=n: f"2026-11-20T1{n}:00:00+00:00")
        res = api._send_followups(line, "daily run")
        mine = [r for r in res["results"] if r["finding_id"] == fid]
        assert mine and mine[0]["status"] == "held" and mine[0]["reason"] == PAID_WHY, res
    assert line.sent == {}
    journal = [json.loads(state.get(m["name"])) for m in state.list(cloud.ACTIONS)]
    assert sum(1 for a in journal if a.get("kind") == "event" and a.get("note") == PAID_WHY) == 1


# --------------------------------------------------------------------------- D4 in the fax run's retry (review mr-3)

BUSY_KEY = "out:fake:9"


def _busy_fax(fid: str, purpose: str) -> dict:
    """A fax for the item that the payer's line did not take (busy), an hour ago, tried once: what the hourly fax run tries
    again (api._fax_refresh, api._retry)."""
    from datetime import datetime, timedelta, timezone
    at = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="microseconds")
    return dict(_fax_record(fid, key=BUSY_KEY, purpose=purpose, at=at), level=2 if purpose == "next_level" else 1, provider_id="F9",
                status="failed", error="Busy", doc=json.dumps({"attempts": 1, "subject": "Second request", "front": ["Second request", ["It is unpaid."]]}))


def _packets_on_hand(api, monkeypatch) -> None:
    """The packet that was faxed is there and has nothing wrong with it, so only the rule under test can stop the retry."""
    from tests.fake_fax import pdf_of
    monkeypatch.setattr(api, "_stored_packet", lambda pid: {"packet_id": pid, "pdf": pdf_of("Invented appeal packet"), "subject": "Appeal"})
    monkeypatch.setattr(api, "_packet_problem", lambda *a, **k: None)


@pytest.mark.parametrize("purpose", ["second_request", "next_level", "payment", "records", "appeal"])
def test_a_fax_the_line_did_not_take_is_not_tried_again_for_a_claim_the_store_shows_paid(tmp_path, monkeypatch, purpose):
    """Review mr-3: a person set Sent again over the payment the run found (0c-2), and a fax for the item did not get through
    (a busy line). The hourly fax run must not send it again: like the follow-ups, a fax of any kind for money the store
    shows paid is a person's call. It is held with why, listed for a person, and the item's history says so once."""
    from remitwatch import actions, fax
    s, fid = _denial_world(tmp_path, "file")
    actions.apply_payer_fax(s.conn, {"payer_group": "Anthem / BCBS", "number": "8005550142", "actor": "owner", "at": "2026-09-01T00:00:00+00:00"})
    actions.apply_fax(s.conn, _busy_fax(fid, purpose))
    s.conn.commit()
    s.close()
    _client, line, api = _site(tmp_path, monkeypatch, str(tmp_path / "s.sqlite"))
    _packets_on_hand(api, monkeypatch)
    for _ in range(2):          # the hourly run, twice
        out = api._fax_refresh(line, use_ai=False)
        assert out["retried"] == 0 and out["errors"] == [], out
    assert line.sent == {}, "a fax went to the payer for a claim the store shows paid"
    s = Store(str(tmp_path / "s.sqlite"))
    c = s.conn
    assert json.loads(c.execute("SELECT doc FROM faxes WHERE fax_key=?", (BUSY_KEY,)).fetchone()[0]).get("held") == PAID_WHY
    assert c.execute("SELECT COUNT(*) FROM finding_events WHERE finding_id=? AND note=?", (fid, PAID_WHY)).fetchone()[0] == 1
    due = [d for d in fax.followups(c) if d["finding_id"] == fid]
    assert [(d["action"], d.get("paid"), d.get("resend"), d["why"]) for d in due] == [("person", True, None, PAID_WHY)]
    s.close()


def test_serverless_a_fax_the_line_did_not_take_is_not_tried_again_for_a_claim_the_store_shows_paid(tmp_path, monkeypatch):
    """The same on the serverless host: the world comes through the runs and the journal, the fax run reads the dashboard's
    copy, and what it decides (the hold on the fax, one line for the item's history) goes into the journal."""
    from remitwatch import api, fax
    from tests.fake_fax import FakeLine
    from tests.test_features import clean_visits
    root, client, state = _cloud_box(tmp_path, monkeypatch)
    folder = root / "Trizetto Data"
    folder.mkdir(parents=True)
    first = clean_visits(1, 25, "99214", "223.65", "117.60") + [claim(210, svc("99214", "223.65", "0.00", None, "CAS*CO*50*223.65~"))]
    for n, i in enumerate(range(0, len(first), 20)):
        (folder / f"r{n:03d}.RMT").write_bytes(remit("".join(first[i:i + 20]), "100.00", seg_count=99, isa_ctrl=f"{800 + n:09d}",
                                                     pay_date="20261010"))
    assert "error" not in _cycle(state, client)
    store, _ = cloud.load_raw(state)
    fid = store.conn.execute("SELECT finding_id FROM findings WHERE signal='DENIED_MEDICAL_NECESSITY'").fetchone()[0]
    store.close()
    for a in ({"kind": "status", "finding_id": fid, "status": "appealed", "notes": "Faxed", "actor": "biller", "at": "2026-10-12T10:00:00+00:00"},
              {"kind": "fax", "record": dict(_fax_record(fid, at="2026-10-12T10:00:00+00:00"), packet_id=None)},
              {"kind": "payer_fax", "payer_group": "Anthem / BCBS", "number": "8005550142", "actor": "owner", "at": "2026-09-01T00:00:00+00:00"}):
        cloud.record_action(state, a)
    assert "error" not in _cycle(state, client)
    (folder / "later.RMT").write_bytes(remit(claim(210, svc("99214", "223.65", "117.60", "117.60", "CAS*CO*45*106.05~")), "117.60",
                                             seg_count=30, isa_ctrl="000000777", pay_date="20261019"))
    assert "error" not in _cycle(state, client)
    cloud.record_action(state, {"kind": "status", "finding_id": fid, "status": "open", "notes": "not in EMA yet", "actor": "biller"})
    cloud.record_action(state, {"kind": "status", "finding_id": fid, "status": "appealed", "actor": "biller"})
    assert "error" not in _cycle(state, client, force_detect=True)
    cloud.record_action(state, {"kind": "fax", "record": _busy_fax(fid, "second_request")})
    monkeypatch.setenv("REMITWATCH_MODE", "serverless")
    monkeypatch.setenv("REMITWATCH_STATE_DIR", str(root / "RemitWatch-state"))
    monkeypatch.setattr(cloud, "SERVING_COPY", cloud.ServingCopy())
    line = FakeLine()
    monkeypatch.setattr(fax, "provider", lambda env=None: line)
    monkeypatch.setattr(fax, "configured", lambda env=None: True)
    _packets_on_hand(api, monkeypatch)
    for _ in range(2):          # the fax run, twice
        out = api._fax_refresh(line, use_ai=False)
        assert out["retried"] == 0 and out["errors"] == [], out
    assert line.sent == {}, "a fax went to the payer for a claim the store shows paid"
    journal = [json.loads(state.get(m["name"])) for m in state.list(cloud.ACTIONS)]
    assert sum(1 for a in journal if a.get("kind") == "event" and a.get("note") == PAID_WHY) == 1
    held = [a["record"]["doc"] for a in journal if a.get("kind") == "fax" and a["record"].get("fax_key") == BUSY_KEY and "held" in str(a["record"].get("doc"))]
    assert held and all((d if isinstance(d, dict) else json.loads(d)).get("held") == PAID_WHY for d in held), held


# --------------------------------------------------------------------------- a status written for an item's old id (review mr-9)

def test_a_status_written_for_the_old_id_after_the_change_lands_on_the_heir(tmp_path, kind):
    """Review mr-9: two Started items get new ids in a run. The Sent a person pressed on a page opened before it reaches
    each heir (on Vercel through the journal: one entry for one item, a bulk entry for the other), with the status the
    person saw logged as the one they changed it from. The same entries applied again change nothing."""
    from remitwatch import actions
    from remitwatch.detect import Detector
    s = _downcode_store(tmp_path, kind)
    c = s.conn
    olds = [r[0] for r in c.execute("SELECT finding_id FROM findings WHERE signal='DOWNCODE_CHARGE' ORDER BY patient_control_number")][:2]
    for fid in olds:
        actions.apply_status(c, fid, "in_progress", "calling the payer", actor="biller", at="2026-11-19T15:00:00+00:00")
    c.commit()
    _claim_files_arrive(s)
    Detector(s, run_id="r2").run()
    heirs = [c.execute("SELECT new_id FROM finding_heirs WHERE old_id=?", (o,)).fetchone()[0] for o in olds]
    assert all(c.execute("SELECT status FROM findings WHERE finding_id=?", (h,)).fetchone()[0] == "in_progress" for h in heirs)
    entries = [("actions/20261120T150000000000-aaaaaa.json",
                {"kind": "status", "finding_id": olds[0], "status": "appealed", "notes": "Sent on the portal", "actor": "biller",
                 "at": "2026-11-20T15:00:00+00:00", "seen": "in_progress", "entry": "e1"}),
               ("actions/20261120T150001000000-bbbbbb.json",
                {"kind": "bulk_status", "ids": [olds[1]], "status": "appealed", "notes": None, "actor": "biller",
                 "at": "2026-11-20T15:00:01+00:00", "seen": {olds[1]: "in_progress"}, "entry": "e2"})]
    done, left, wrote = cloud.apply_actions(c, entries)
    assert left == [] and len(done) == 2 and wrote, "a person's Sent waits for an item that no longer exists, and is dropped after a week"
    assert [c.execute("SELECT status FROM findings WHERE finding_id=?", (h,)).fetchone()[0] for h in heirs] == ["appealed", "appealed"]
    def logged(h: str) -> list[tuple]:
        return [tuple(r) for r in c.execute("""SELECT from_status, to_status, actor FROM finding_events WHERE finding_id=? AND kind='manual'
                                              AND at LIKE '2026-11-20%'""", (h,))]
    assert logged(heirs[0]) == [("in_progress", "appealed", "biller")] and logged(heirs[1]) == [("in_progress", "appealed", "biller")]
    assert cloud.apply_actions(c, entries)[2] is False                  # folded again (another instance): nothing written
    assert len(logged(heirs[0])) == 1 and len(logged(heirs[1])) == 1
    # on a server: the Sent a send records for the id it reserved under (api._record_status) reaches the heir too
    assert actions.apply_status(c, olds[0], "appealed", "Faxed", actor="biller") is True


def test_serverless_a_status_journaled_for_the_old_id_lands_on_the_heir(tmp_path, monkeypatch):
    """The same through the serverless runs: a Started item gets a new id in one run; the Sent a person pressed on the
    dashboard's copy from before it, journaled for the old id, reaches the heir in the next run (and its copy) instead of
    waiting a week for an item that will not come back."""
    root, client, state = _cloud_box(tmp_path, monkeypatch)
    _write_remits(root / "Trizetto Data", _downcode_claims(), "d", 900)
    assert "error" not in _cycle(state, client)
    store, _ = cloud.load_raw(state)
    old = _first_downcode(store.conn)
    store.close()
    cloud.record_action(state, {"kind": "status", "finding_id": old, "status": "in_progress", "notes": "calling the payer", "actor": "biller",
                                "at": SENT_AT})
    store, _ = cloud.load_raw(state)
    _claim_files_arrive(store)
    cloud.save_raw(state, store)
    store.close()
    assert "error" not in _cycle(state, client, force_detect=True)          # folds the Started, then gives the money a new id
    store, _ = cloud.load_raw(state)
    heir = store.conn.execute("SELECT new_id FROM finding_heirs WHERE old_id=?", (old,)).fetchone()[0]
    store.close()
    cloud.record_action(state, {"kind": "status", "finding_id": old, "status": "appealed", "notes": "Sent on the portal", "actor": "biller",
                                "seen": "in_progress"})
    out = _cycle(state, client)
    assert "error" not in out and not out.get("actions_waiting"), out
    assert state.list(cloud.ACTIONS) == []
    store, _ = cloud.load_raw(state)
    assert store.conn.execute("SELECT status FROM findings WHERE finding_id=?", (heir,)).fetchone()[0] == "appealed"
    store.close()
    serving = _serving(state, tmp_path)
    assert serving.execute("SELECT status FROM findings WHERE finding_id=?", (heir,)).fetchone()[0] == "appealed"
    serving.close()


# --------------------------------------------------------------------------- an appeal in flight when its item gets a new id (review mr-4)

def _reserved(fid: str, key: str = "out:fake:5") -> dict:
    """What api._reserve writes before a send: going out, no fax id from the line yet. The item is still To do: it becomes
    Sent only once the line takes the fax."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    return dict(_fax_record(fid, key=key), provider_id=None, status="sending", created_at=now, updated_at=now, sent_at=None)


def _later(days: int = 60) -> str:
    from datetime import date, timedelta
    return (date.today() + timedelta(days=days)).isoformat()


def test_an_appeal_being_faxed_when_its_item_gets_a_new_id_follows_the_heir(tmp_path, kind):
    """Review mr-4: a person pressed Appeal by fax on a To do item, and the send is reserved, when a run gives the item's
    money a new id. The fax follows the heir, the send's Sent (recorded for the id it was reserved under) reaches it, and
    the follow-ups chase the heir, run after run."""
    from remitwatch import actions, fax
    from remitwatch.detect import Detector
    s = _downcode_store(tmp_path, kind)
    c = s.conn
    old = _first_downcode(c)
    line_key = c.execute("SELECT line_key FROM findings WHERE finding_id=?", (old,)).fetchone()[0]
    actions.apply_fax(c, _reserved(old))
    c.commit()
    _claim_files_arrive(s)
    Detector(s, run_id="r2").run()
    heir = c.execute("SELECT finding_id FROM findings WHERE line_key=? AND COALESCE(stale,0)=0", (line_key,)).fetchone()[0]
    assert heir != old
    assert (c.execute("SELECT new_id FROM finding_heirs WHERE old_id=?", (old,)).fetchone() or [None])[0] == heir, "no heir was recorded"
    assert c.execute("SELECT finding_id FROM faxes WHERE fax_key='out:fake:5'").fetchone()[0] == heir, "the fax stayed on an id no item has"
    assert c.execute("SELECT status FROM findings WHERE finding_id=?", (heir,)).fetchone()[0] == "open"
    # the line takes it: the send settles the fax by its key and records Sent for the id it reserved under
    actions.apply_fax(c, {"fax_key": "out:fake:5", "provider_id": "F5", "status": "queued"})
    assert actions.apply_status(c, old, "appealed", "Faxed to Anthem / BCBS at (800) 555-0142 (fax F5, 6 pages).", actor="biller")
    c.commit()
    assert c.execute("SELECT status FROM findings WHERE finding_id=?", (heir,)).fetchone()[0] == "appealed"
    assert [(d["finding_id"], d["action"]) for d in fax.followups(c, today=_later())] == [(heir, "second_request")]
    Detector(s, run_id="r3").run()           # a later run with nothing new keeps it all on the heir
    assert c.execute("SELECT finding_id FROM faxes WHERE fax_key='out:fake:5'").fetchone()[0] == heir
    assert c.execute("SELECT status FROM findings WHERE finding_id=?", (heir,)).fetchone()[0] == "appealed"


def test_the_heir_of_an_item_being_faxed_waits_for_the_fax_too(tmp_path, monkeypatch):
    """Review mr-4 on the server's pages: while the fax is going out, the heir's panel shows it, and a second Appeal by fax
    on the heir, or a person's status change, waits for it (409 in_flight), as on the item it was pressed on."""
    from remitwatch import actions
    from remitwatch.detect import Detector
    s = _downcode_store(tmp_path, "file")
    c = s.conn
    old = _first_downcode(c)
    line_key = c.execute("SELECT line_key FROM findings WHERE finding_id=?", (old,)).fetchone()[0]
    actions.apply_payer_fax(c, {"payer_group": "Anthem / BCBS", "number": "8005550142", "actor": "owner", "at": "2026-09-01T00:00:00+00:00"})
    actions.apply_fax(c, _reserved(old))
    c.commit()
    _claim_files_arrive(s)
    Detector(s, run_id="r2").run()
    heir = c.execute("SELECT finding_id FROM findings WHERE line_key=? AND COALESCE(stale,0)=0", (line_key,)).fetchone()[0]
    s.close()
    client, line, _api = _site(tmp_path, monkeypatch, str(tmp_path / "s.sqlite"))
    d = client.get(f"/api/findings/{heir}").json()
    assert [x["direction"] for x in d["faxes"]] == ["out"], "the heir's panel shows no fax"
    r = client.post("/api/appeals/send", json={"finding_id": heir, "stale_ok": True}, headers=XHR)
    assert r.status_code == 409 and r.json()["code"] == "in_flight", r.json()
    r = client.post(f"/api/findings/{heir}/status", json={"status": "dismissed", "notes": "not worth it", "seen_status": "open"}, headers=XHR)
    assert r.status_code == 409 and r.json()["code"] == "in_flight", r.json()
    assert line.sent == {}


def test_serverless_an_appeal_being_faxed_follows_its_heir(tmp_path, monkeypatch):
    """The same on the serverless host: the send's reservation is journaled and folded in by the run that gives the money a
    new id; then the line takes the fax. Its update and the Sent journaled for the old id reach the heir in the next run,
    nothing waits in the journal, and the dashboard's copy shows the heir Sent with its fax, chased by the follow-ups."""
    from datetime import datetime, timezone

    from remitwatch import fax
    root, client, state = _cloud_box(tmp_path, monkeypatch)
    _write_remits(root / "Trizetto Data", _downcode_claims(), "d", 900)
    assert "error" not in _cycle(state, client)
    store, _ = cloud.load_raw(state)
    old = _first_downcode(store.conn)
    line_key = store.conn.execute("SELECT line_key FROM findings WHERE finding_id=?", (old,)).fetchone()[0]
    _claim_files_arrive(store)
    cloud.save_raw(state, store)
    store.close()
    cloud.record_action(state, {"kind": "fax", "record": _reserved(old)})
    assert "error" not in _cycle(state, client, force_detect=True)      # folds the reservation, then gives the money a new id
    now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    cloud.record_action(state, {"kind": "fax", "record": {"fax_key": "out:fake:5", "provider_id": "F5", "status": "queued", "updated_at": now}})
    cloud.record_action(state, {"kind": "status", "finding_id": old, "status": "appealed", "notes": "Faxed (fax F5)", "actor": "biller", "seen": "open"})
    out = _cycle(state, client)
    assert "error" not in out and not out.get("actions_waiting"), out
    serving = _serving(state, tmp_path)
    heir = serving.execute("SELECT finding_id FROM findings WHERE line_key=? AND COALESCE(stale,0)=0", (line_key,)).fetchone()[0]
    assert heir != old
    assert serving.execute("SELECT status FROM findings WHERE finding_id=?", (heir,)).fetchone()[0] == "appealed"
    assert [tuple(r) for r in serving.execute("SELECT finding_id, provider_id FROM faxes WHERE fax_key='out:fake:5'")] == [(heir, "F5")]
    assert [(d["finding_id"], d["action"]) for d in fax.followups(serving, today=_later())] == [(heir, "second_request")]
    serving.close()


def test_an_appeal_being_prepared_when_its_item_gets_a_new_id_gives_it_an_heir(tmp_path):
    """Review mr-4: on a server, an appeal being prepared (the note looked up and reviewed: api._prep_take's marker in the
    store) is work on the item too. When a run gives the money a new id meanwhile, the send stops at its last check (the
    item it was pressed on is gone), and what was written for the old id meanwhile (the note check) follows the heir."""
    from remitwatch import actions
    from remitwatch.api import PREP_KEY
    from remitwatch.detect import Detector
    s = _downcode_store(tmp_path, "file")
    c = s.conn
    old = _first_downcode(c)
    c.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (PREP_KEY + old, json.dumps({"token": "t1", "at": "2026-11-20T15:00:00+00:00"})))
    c.commit()
    _claim_files_arrive(s)
    Detector(s, run_id="r2").run()
    pair = c.execute("SELECT new_id FROM finding_heirs WHERE old_id=?", (old,)).fetchone()
    assert pair, "no heir was recorded for an item whose appeal was being prepared"
    assert actions.apply_event(c, {"finding_id": old, "event": "note_check", "note": "The biller opened the note.", "actor": "biller",
                                   "at": "2026-11-20T15:00:05+00:00"})
    assert c.execute("SELECT COUNT(*) FROM finding_events WHERE finding_id=? AND kind='note_check'", (pair[0],)).fetchone()[0] == 1


# --------------------------------------------------------------------------- a store image saved whose answer was lost (review mr-5)

def _finish_answer_lost(client, commit: bool = True) -> None:
    """The fake Dropbox's next upload of the store image ends as a lost answer: the finish call raises (retried after a 5xx,
    or a timeout), after Dropbox wrote the file (commit) or before anything was written."""
    real, lose = client.upload_chunks, [1]

    def upload_chunks(path, chunks, part=32 << 20):
        if lose[0] and path.endswith("/" + cloud.RAW):
            lose[0] = 0
            if commit:
                real(path, chunks, part)
            else:
                b"".join(chunks)            # every part went up; the finish fails before the file is written
            raise RuntimeError("Dropbox files/upload_session/finish: no answer")
        return real(path, chunks, part)
    client.upload_chunks = upload_chunks


def test_serverless_an_image_whose_save_lost_its_answer_is_taken_by_the_next_run(tmp_path, monkeypatch):
    """Review mr-5: Dropbox commits the store image but the answer is lost. The run that saved it fails, but status.json
    already names the image it was writing (a pending record, written before the commit), so the next run takes that
    image and records it as the last save, instead of every run stopping until a person accepts it. An older copy put back
    afterwards is still refused."""
    import hashlib
    root, client, state = _cloud_box(tmp_path, monkeypatch)
    folder = _two_runs_folder(root)
    assert "error" not in _cycle(state, client)
    before = state.get(cloud.RAW)
    (folder / "b.RMT").write_bytes(_era([_visit(3)], "100.00", "000000372"))
    _finish_answer_lost(client)
    with pytest.raises(RuntimeError):
        _cycle(state, client)
    written = state.get(cloud.RAW)
    assert written != before                                        # Dropbox has the new image
    rec = json.loads(state.get(cloud.STATUS))[cloud.IMAGE_KEY]
    assert rec["sha256"] == hashlib.sha256(before).hexdigest()      # the last save confirmed
    assert (rec.get("pending") or {}).get("sha256") == hashlib.sha256(written).hexdigest(), rec
    out = _cycle(state, client)
    assert "error" not in out, out
    rec = json.loads(state.get(cloud.STATUS))[cloud.IMAGE_KEY]
    assert rec["sha256"] == hashlib.sha256(state.get(cloud.RAW)).hexdigest() and "pending" not in rec, rec
    assert "error" not in _cycle(state, client)
    state.put(cloud.RAW, before)                                    # an older copy put back: refused, as before
    assert "not the one the last run saved" in (_cycle(state, client).get("error") or "")


def test_serverless_an_image_upload_that_failed_before_its_commit_leaves_the_last_one(tmp_path, monkeypatch):
    """The other side of review mr-5: the finish fails before Dropbox writes anything. The image in the folder is still the
    last one confirmed, and the next run loads it. And on a new site's first save, whose answer is lost, the next run takes
    the image it finds, as before (no record of an earlier image to hold it against)."""
    root, client, state = _cloud_box(tmp_path, monkeypatch)
    folder = _two_runs_folder(root)
    assert "error" not in _cycle(state, client)
    before = state.get(cloud.RAW)
    (folder / "b.RMT").write_bytes(_era([_visit(3)], "100.00", "000000372"))
    _finish_answer_lost(client, commit=False)
    with pytest.raises(RuntimeError):
        _cycle(state, client)
    assert state.get(cloud.RAW) == before
    assert "error" not in _cycle(state, client)
    root2, client2, state2 = _cloud_box(tmp_path / "new-site", monkeypatch)
    _two_runs_folder(root2)
    _finish_answer_lost(client2)
    with pytest.raises(RuntimeError):
        _cycle(state2, client2)
    assert state2.get(cloud.RAW) is not None and "error" not in _cycle(state2, client2)
