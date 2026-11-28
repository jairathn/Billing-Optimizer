"""
Staff names stay in the store (unit ui-words, CRITIQUE_biller change 5). A person's own name is kept with what they did, in
the item's history, and never reaches a log line, a run manifest, an exception or a diagnostic bundle: runlog's scrubber
learns the names the store holds (Scrubber.learn_people, on both hosts, and again once a serverless run has folded the
journal), as it learns patients' names. The test the owner's privacy rule asks for, like tests/test_runlog.py's: a full
cycle on each host with invented staff names planted in the items' histories and in a failing step's log line and error,
after which no byte of any log, manifest or bundle holds them. Every name is invented.
"""
from __future__ import annotations

import os
import sqlite3
from urllib.parse import quote

from remitwatch import cloud, diagnose, pipeline, runlog
from remitwatch.state import DropboxState
from remitwatch.store import Store
from tests.fakes import FakeDropbox
from tests.test_runlog import DATA, PLANTED, _env, all_log_bytes, assert_clean, denial_files, log_tables  # noqa: F401  (_env: autouse)

# invented staff names: two words, and one word alone (a capitalized word the scrubber masks only because the store holds it)
STAFF = "Quorvalda Tisbenwick"
SOLO = "Zephyrinne"
STAFF_WORDS = ["Quorvalda", "Tisbenwick", SOLO]


def _leaky(*_a, log=print, **_kw):
    """A step that fails with the staff names in its log line and in its error message."""
    log(f"  EMA ERROR the report {SOLO} uploaded for {STAFF} could not be read")
    raise ValueError(f"bad row checked by {STAFF} and {SOLO}")


def test_a_scrubber_learns_the_staff_names_the_store_holds_and_masks_them_alone_or_together():
    rl = runlog.Scrubber()
    conn = sqlite3.connect(":memory:")
    conn.executescript("""CREATE TABLE finding_events (id INTEGER PRIMARY KEY, finding_id TEXT, at TEXT, kind TEXT, actor TEXT, note TEXT);
                          CREATE TABLE hide_rules (payer_group TEXT, actor TEXT);
                          CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);""")
    conn.executemany("INSERT INTO finding_events (finding_id, kind, actor) VALUES ('f', 'manual', ?)",
                     [(STAFF,), ("retry",), ("hide rule",), ("system",)])
    conn.execute("INSERT INTO hide_rules VALUES ('Aetna', ?)", (f"{SOLO} (owner's password)",))
    conn.execute("""INSERT INTO meta VALUES ('ema:writeoff_policy', '{"a|b|c|d": {"decision": "accept", "by": "Wrennolyx Dabb (oda)"}}')""")
    rl.learn(conn)
    assert {"quorvalda", "tisbenwick", SOLO.lower(), "wrennolyx", "dabb"} <= rl.names
    assert not {"retry", "hide", "rule", "system", "owner", "password"} & rl.names
    out = rl.text(f"ERROR fax for {SOLO} failed; {STAFF} and WRENNOLYX checked it; retry at noon")
    assert not any(w.lower() in out.lower() for w in STAFF_WORDS + ["Wrennolyx"]), out
    assert "retry at noon" in out and out.startswith("ERROR fax for")


def test_no_staff_name_reaches_any_log_byte_on_a_server(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import remitwatch.api as api
    db = str(tmp_path / "data" / "remitwatch.sqlite")
    feed = tmp_path / "feed"
    denial_files(feed)
    pipeline.run_cycle(db, sync=False, local=str(feed), email=False, report=False, log=lambda *a: None)
    # two people work the items through the app: a status, a line written down, a note that names a colleague
    monkeypatch.setattr(api, "DB_PATH", db)
    monkeypatch.setattr(api, "PASSWORD", "")
    c = TestClient(api.app)
    fids = [r[0] for r in sqlite3.connect(db).execute("SELECT finding_id FROM findings WHERE status='open' ORDER BY finding_id")]
    assert fids
    hdr = {"X-Requested-With": "remitwatch"}
    r = c.post(f"/api/findings/{fids[0]}/status", json={"status": "appealed", "notes": f"faxed; {SOLO} has the copy"},
               headers=dict(hdr, **{"X-RemitWatch-Person": quote(STAFF)}))
    assert r.status_code == 200, r.text
    r = c.post(f"/api/findings/{fids[0]}/event", json={"event": "asked", "note": "on hold"}, headers=dict(hdr, **{"X-RemitWatch-Person": SOLO}))
    assert r.status_code == 200, r.text
    store = Store(db)
    try:
        held = {a for (a,) in store.conn.execute("SELECT actor FROM finding_events WHERE finding_id=?", (fids[0],))}
        assert {STAFF, SOLO} <= held                    # the names live in the store, with the item's history
    finally:
        store.close()
    # the next cycle, with a step that fails with the names in its line and its error
    monkeypatch.setattr(pipeline, "reread_waiting", _leaky)
    printed: list = []
    pipeline.run_cycle(db, sync=False, local=str(feed), email=False, report=False, log=printed.append)
    sink = runlog.DirSink(runlog.logs_dir_for(db))
    m, _events = runlog.read_run(sink, runlog.read_index(sink)[-1]["run_id"])
    assert any(e["type"] == "ValueError" for e in m["errors"])          # the failing step was logged, scrubbed
    assert any("EMA ERROR" in str(x) for x in printed) and not any(any(w in str(x) for w in STAFF_WORDS) for x in printed)
    store = Store(db)
    try:
        rl = runlog.start_run("cycle", trigger="test", db_path=db, host="server", conn=store.conn)
        with rl.step("broken", contain=True):
            raise RuntimeError(f"{STAFF} {SOLO} let it go")
        rl.finish(conn=store.conn)
        files = all_log_bytes(runlog.logs_dir_for(db))
        assert any(n.endswith(".jsonl") for n in files)
        files.update(log_tables(store.conn))
    finally:
        store.close()
    # the bundle is told the patients planted in the data, never the staff names: it must find those in the store itself
    res = diagnose.build_bundle(db_path=db, days=2, known=PLANTED)
    files.update({f"bundle:{n}": b for n, b in diagnose.unpack(res["bytes"]).items()})
    files["bundle.zip"] = res["bytes"]
    assert_clean(files, STAFF_WORDS)


def test_no_staff_name_reaches_any_log_byte_on_a_serverless_host(tmp_path, monkeypatch):
    root = tmp_path / "dbx"
    client = FakeDropbox(str(root))
    state = DropboxState(client, "/RemitWatch-state")
    denial_files(root / DATA)
    outs = []
    for _ in range(6):
        out = cloud.run_cloud_cycle(state, client, budget_s=600, email=False)
        outs.append(out)
        if not out.get("more"):
            break
    assert any("detect" in o for o in outs)
    store, _existed = cloud.load_raw(state)
    try:
        fids = [r[0] for r in store.conn.execute("SELECT finding_id FROM findings WHERE status='open' ORDER BY finding_id")]
    finally:
        store.close()
    assert fids
    # the people's changes as the dashboard journals them (api.acting: the name the page sent), to be folded by the next run
    cloud.record_action(state, {"kind": "status", "finding_id": fids[0], "status": "appealed", "notes": f"{SOLO} has the copy",
                                "actor": STAFF, "seen": "open"})
    cloud.record_action(state, {"kind": "event", "finding_id": fids[0], "event": "asked", "note": "Asked the payer", "actor": SOLO})
    # the run that folds the journal fails, after the fold, with the names in its line and its error (the names are new to
    # this run: the scrubber learns them as the journal comes in); the run after it fails again before the fold
    from remitwatch import intake

    def leaky_intake(*a, log=print, **kw):
        _leaky(log=log)
    monkeypatch.setattr(intake, "process_state", leaky_intake)
    cloud.run_cloud_cycle(state, client, budget_s=600, email=False)

    def leaky_repair(store, state, log=print):
        try:
            _leaky(log=log)
        except ValueError as exc:
            runlog.caught(exc, "ema_repair")
            raise
    monkeypatch.setattr(cloud, "repair_ledger", leaky_repair)
    for _ in range(3):
        if not cloud.run_cloud_cycle(state, client, budget_s=600, email=False).get("more"):
            break
    store, _existed = cloud.load_raw(state)
    try:
        held = {a for (a,) in store.conn.execute("SELECT actor FROM finding_events WHERE finding_id=?", (fids[0],))}
        assert {STAFF, SOLO} <= held                    # folded into the store, with the item's history
        files = all_log_bytes(str(root / "RemitWatch-state" / "logs"))
        assert any(n.endswith(".jsonl.gz") for n in files)
        files.update(log_tables(store.conn))
        res = diagnose.build_bundle(conn=store.conn, state=state, days=2, known=PLANTED)
    finally:
        store.close()
    files.update({f"bundle:{n}": b for n, b in diagnose.unpack(res["bytes"]).items()})
    assert any(b"EMA ERROR" in b for b in files.values())                 # the failing line itself was logged
    assert_clean(files, STAFF_WORDS)
    assert os.path.exists(str(root / "RemitWatch-state"))
