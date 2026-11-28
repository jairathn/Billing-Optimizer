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
import re
import sqlite3
from urllib.parse import quote

import pytest

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


# ---------------------------------------------------------------------------------- a first name the code uses as a word
# The person card says "First name is enough". A first name that is also a word of RemitWatch's own (Grace, Mark, Pat) or a
# short English word (An) has no pair for the scrubber to learn, so it is kept as written, with its capital, and masked
# wherever it is written so (Scrubber.people_exact). The states write "Marked Paid by <name> on <day>" into the state basis
# and the decision log keeps it, scrubbed; a failing self-check's bundle carries the decision logs. The review found such
# a name kept whole on both hosts (finding e-2).
CODE_WORD_NAMES = ["Grace", "Mark", "Pat", "An"]


def _word_hits(files: dict[str, bytes], name: str) -> list[str]:
    """Files that hold the name as a whole word, as written (the code's own lowercase "grace_days" or "mark" is no hit)."""
    rx = re.compile(rb"(?<![A-Za-z])" + name.encode() + rb"(?![A-Za-z])")
    return sorted(k for k, b in files.items() if rx.search(b))


@pytest.mark.parametrize("name", CODE_WORD_NAMES + [SOLO])
def test_a_one_word_staff_name_the_code_also_uses_is_masked_as_written(name):
    rl = runlog.Scrubber()
    conn = sqlite3.connect(":memory:")
    conn.executescript("""CREATE TABLE finding_events (id INTEGER PRIMARY KEY, finding_id TEXT, at TEXT, kind TEXT, actor TEXT, note TEXT);
                          CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);""")
    conn.execute("INSERT INTO finding_events (finding_id, kind, actor) VALUES ('f', 'manual', ?)", (name,))
    rl.learn(conn)
    out = rl.text(f"Marked Paid by {name} on Nov 28, 2026 (recovered $1.00)")
    assert not re.search(rf"(?<![A-Za-z]){name}(?![A-Za-z])", out), out
    assert out.startswith("Marked Paid by") and "$1.00" in out
    # the code's own words stay readable where they are not written as the name is
    kept = rl.text("mark as sent; an item in grace_days; Marked; Patient billed")
    assert kept == "mark as sent; an item in grace_days; Marked; Patient billed", kept


def _status_changes(c, fids, name):
    from urllib.parse import quote as _q
    h = {"X-Requested-With": "remitwatch", "X-RemitWatch-Person": _q(name)}
    for fid, st in zip(fids, ("resolved", "dismissed", "in_progress")):
        body = {"status": st, "notes": "invented note"}
        if st == "resolved":
            body["recovered_cents"] = 100
        r = c.post(f"/api/findings/{fid}/status", json=body, headers=h)
        assert r.status_code == 200, r.text


@pytest.mark.parametrize("name", ["Grace", "Pat"])
def test_a_code_word_first_name_never_reaches_the_decision_log_or_a_bundle_on_a_server(name, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import remitwatch.api as api
    db = str(tmp_path / "data" / "remitwatch.sqlite")
    feed = tmp_path / "feed"
    denial_files(feed)
    pipeline.run_cycle(db, sync=False, local=str(feed), email=False, report=False, log=lambda *a: None)
    monkeypatch.setattr(api, "DB_PATH", db)
    monkeypatch.setattr(api, "PASSWORD", "")
    c = TestClient(api.app)
    fids = [r[0] for r in sqlite3.connect(db).execute("SELECT finding_id FROM findings WHERE status='open' ORDER BY finding_id")]
    assert fids
    _status_changes(c, fids, name)
    # the next analysis takes the person's statuses into the states and writes the decision log
    pipeline.run_cycle(db, sync=False, local=str(feed), email=False, report=False, log=lambda *a: None, force_detect=True)
    store = Store(db)
    try:
        basis = [r[0] for r in store.conn.execute("SELECT state_basis FROM findings WHERE finding_id=?", (fids[0],))]
        assert any(f"by {name} on" in (b or "") for b in basis), basis       # the store keeps the name with the item
        files = log_tables(store.conn)
        assert b"Marked Paid by" in files["item_decisions"]                  # the decision was logged, name masked
    finally:
        store.close()
    res = diagnose.build_bundle(db_path=db, days=2, known=PLANTED)
    files.update({f"bundle:{n}": b for n, b in diagnose.unpack(res["bytes"]).items()})
    files.update(all_log_bytes(runlog.logs_dir_for(db)))
    assert not _word_hits(files, name)


def test_a_code_word_first_name_never_reaches_the_decision_log_or_a_bundle_on_a_serverless_host(tmp_path, monkeypatch):
    name = "Grace"
    root = tmp_path / "dbx"
    client = FakeDropbox(str(root))
    state = DropboxState(client, "/RemitWatch-state")
    denial_files(root / DATA)
    for _ in range(6):
        if not cloud.run_cloud_cycle(state, client, budget_s=600, email=False).get("more"):
            break
    store, _existed = cloud.load_raw(state)
    try:
        fids = [r[0] for r in store.conn.execute("SELECT finding_id FROM findings WHERE status='open' ORDER BY finding_id")]
    finally:
        store.close()
    assert fids
    # the people's changes as the dashboard journals them, folded by the next run (a fresh process: nothing learned yet)
    runlog.SCRUB.people_exact.discard(name)
    cloud.record_action(state, {"kind": "status", "finding_id": fids[0], "status": "resolved", "recovered_cents": 100,
                                "notes": "invented note", "actor": name, "seen": "open"})
    # a change that fails while the journal is folded, its error naming the person: logged before learn_people runs
    cloud.record_action(state, {"kind": "event", "finding_id": fids[0], "event": "asked", "note": "invented", "actor": name})
    real_apply = cloud.actions_mod.apply

    def failing_apply(conn, a):
        if a.get("kind") == "event":
            raise ValueError(f"the note {a.get('actor')} wrote could not be read")
        return real_apply(conn, a)
    monkeypatch.setattr(cloud.actions_mod, "apply", failing_apply)
    for _ in range(4):
        if not cloud.run_cloud_cycle(state, client, budget_s=600, email=False, force_detect=True).get("more"):
            break
    store, _existed = cloud.load_raw(state)
    try:
        held = {a for (a,) in store.conn.execute("SELECT actor FROM finding_events WHERE finding_id=?", (fids[0],))}
        assert name in held
        files = all_log_bytes(str(root / "RemitWatch-state" / "logs"))
        files.update(log_tables(store.conn))
        assert b"Marked Paid by" in files["item_decisions"]
        res = diagnose.build_bundle(conn=store.conn, state=state, days=2, known=PLANTED)
    finally:
        store.close()
    files.update({f"bundle:{n}": b for n, b in diagnose.unpack(res["bytes"]).items()})
    assert not _word_hits(files, name)
