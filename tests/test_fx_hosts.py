"""
Fix wave F6 (hosts): the review of 2026-11-26's hosts findings (plans/review_2026-11-26.md, hosts-1, hosts-2, hosts-3),
the flaky tests and the hosts' own follow-ups (plans/briefs/2026-11-26_fixwave.md, section F6).

Every claim, number, amount and date here is invented (tests/test_hotfix2d.py's builders, tests/test_features.claim,
tests/test_remitwatch.remit). The reviewer's tests (since-hosts-tests) and the verifier's (since-hosts-verify-tests) are
ported here; a test runs on the server's store file and, where the code runs on both hosts, on the compact in-memory
store the serverless cycle builds, or through that cycle itself.
"""
from __future__ import annotations

import os
import sqlite3
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from remitwatch import cloud, detect  # noqa: E402
from remitwatch.store import Store  # noqa: E402
from tests.test_features import claim, svc  # noqa: E402
from tests.test_hotfix2d import (_cloud_box, _cycle, _era, _live, _server_cycle, _server_world,  # noqa: E402
                                 _two_runs_folder, _visit)
from tests.test_remitwatch import remit  # noqa: E402

PAID_CLAIM = "CB0000001C017"        # denied on a.RMT, paid on b.RMT
OTHER_CLAIM = "CB0000003C017"       # denied on a.RMT, never paid: the item a person works


def _denied(i: int) -> str:
    """One invented claim denied in full (CO-50, nothing paid)."""
    c = claim(i, svc("99213", "500.00", "0.00", None, "CAS*CO*50*500.00~"), status="4")
    return c.replace("*500.00*100.00*0.00*", "*500.00*0.00*0.00*", 1)


def _open(db: str, pcn: str) -> list[str]:
    s = Store(db)
    try:
        return [r[0] for r in s.conn.execute("""SELECT finding_id FROM findings WHERE patient_control_number=?
                                               AND status IN ('open','in_progress') AND COALESCE(stale,0)=0""", (pcn,))]
    finally:
        s.close()


def _coverage(db: str) -> tuple[int, int, int]:
    """(lines the newest analysis read, lines in the store, analyses run)."""
    s = Store(db)
    try:
        newest = s.conn.execute("SELECT lines FROM detection_runs ORDER BY finished_at DESC, rowid DESC LIMIT 1").fetchone()[0]
        return newest, s.conn.execute("SELECT COUNT(*) FROM lines").fetchone()[0], \
            s.conn.execute("SELECT COUNT(*) FROM detection_runs").fetchone()[0]
    finally:
        s.close()


def _world(tmp_path, monkeypatch) -> str:
    """A server store whose first cycle read a.RMT: two denials (claims 1 and 3) listed as work, claim 2 paid."""
    db, _ = _server_world(tmp_path, monkeypatch)
    first = tmp_path / "first"
    first.mkdir()
    (first / "a.RMT").write_bytes(remit(_denied(1) + _visit(2) + _denied(3), "100.00", seg_count=99, isa_ctrl="000000501",
                                        pay_date="20261020"))
    out = _server_cycle(db, str(first))
    assert "error" not in out and "detect" in out, out
    assert _open(db, PAID_CLAIM) and _open(db, OTHER_CLAIM), "the fixture's two denials are listed as work"
    return db


def _second(tmp_path) -> str:
    """b.RMT: the payer pays claim 1 ($100.00)."""
    second = tmp_path / "second"
    second.mkdir(exist_ok=True)
    (second / "b.RMT").write_bytes(remit(_visit(1), "100.00", seg_count=99, isa_ctrl="000000502", pay_date="20261027"))
    return str(second)


def _paid_cents(db: str, pcn: str) -> int:
    s = Store(db)
    try:
        return s.conn.execute("""SELECT COALESCE(SUM(c.paid_cents),0) FROM claims c JOIN transactions t ON t.transaction_key=c.transaction_key
                                 WHERE c.patient_control_number=? AND COALESCE(t.superseded,0)=0""", (pcn,)).fetchone()[0]
    finally:
        s.close()


def _stop_inside_detection(monkeypatch, where: str, exc: BaseException) -> None:
    def stopped(self, *_a, **_k):
        raise exc
    monkeypatch.setattr(detect.Detector, where, stopped)


# --------------------------------------------------------------------------- hosts-1: an analysis that stopped is owed

def test_server_new_remittances_are_analysed_after_a_cycle_whose_analysis_stopped(tmp_path, monkeypatch):
    """(reviewer) Cycle 1 reads a.RMT and analyses it. Cycle 2 reads b.RMT and its analysis stops (a code bug, or the
    container is stopped by a deploy while detection runs). Cycle 3 has no new file: the remittance read by cycle 2 must
    still be analysed (Vercel keeps cloud:detect_pending for this)."""
    db, local = _server_world(tmp_path, monkeypatch)
    assert "error" not in _server_cycle(db, local)
    second = tmp_path / "second"
    second.mkdir()
    (second / "b.RMT").write_bytes(_era([_visit(5, "40.00")], "40.00", "000000391"))

    def stopped(*_a, **_k):
        raise MemoryError("the analysis stopped part-way")
    with monkeypatch.context() as m:
        m.setattr(detect, "run_detection", stopped)
        with pytest.raises(MemoryError):
            _server_cycle(db, str(second))
    out = _server_cycle(db)                                  # nothing new arrives
    newest, in_store, runs = _coverage(db)
    assert newest == in_store, (f"the remittance read by the stopped cycle was never analysed: the newest analysis read {newest} "
                                f"of the store's {in_store} lines; analyses run: {runs}; the cycle after it did {sorted(out)}")
    assert "detect" in out


def test_server_an_item_paid_by_a_remittance_read_in_a_stopped_cycle_is_not_left_on_the_list(tmp_path, monkeypatch):
    """(reviewer) The money view of the case above: the denial of claim 1 is an open item; the payer pays claim 1 on a
    later remittance, read by a cycle whose analysis stops; the cycles after it read nothing new. The item must not stay
    on the list as work while the store holds the payment."""
    db = _world(tmp_path, monkeypatch)

    def stopped(*_a, **_k):
        raise MemoryError("the analysis stopped part-way")
    with monkeypatch.context() as m:
        m.setattr(detect, "run_detection", stopped)
        with pytest.raises(MemoryError):
            _server_cycle(db, _second(tmp_path))
    _server_cycle(db)                                        # nothing new arrives
    _server_cycle(db)                                        # nor the next day
    assert _paid_cents(db, PAID_CLAIM) == 10000
    still = _open(db, PAID_CLAIM)
    assert still == [], f"the store holds the payer's $100.00 for claim 1, but {len(still)} item(s) for it are still listed as work"


STOPS = {
    # a transient failure of the save (the web process held the write lock past the busy timeout)
    "save_locked": ("save", sqlite3.OperationalError("database is locked")),
    # the process runs short of memory in the middle of the rules
    "rules_memory": ("run_claim_rules", MemoryError("the analysis ran out of memory")),
}


@pytest.mark.parametrize("where", sorted(STOPS))
def test_server_items_paid_by_a_remittance_read_in_a_cycle_whose_analysis_stopped(tmp_path, monkeypatch, where):
    """(verifier) The analysis of the cycle that read b.RMT stops inside detection (Detector.run_claim_rules or its save);
    the next two cycles see nothing new and nothing a person did. b.RMT's payment must reach the items by then."""
    meth, exc = STOPS[where]
    db = _world(tmp_path, monkeypatch)
    with monkeypatch.context() as m:
        _stop_inside_detection(m, meth, exc)
        with pytest.raises(type(exc)):
            _server_cycle(db, _second(tmp_path))
    from remitwatch.pipeline import status
    failed = (status(db).get("last_cycle") or {}).get("error")
    assert failed, "the stopped cycle's error is on the Data tab"
    outs = [_server_cycle(db), _server_cycle(db)]           # nothing new arrives, nobody acts
    newest, in_store, runs = _coverage(db)
    assert _paid_cents(db, PAID_CLAIM) == 10000, "b.RMT's payment is in the store"
    still = _open(db, PAID_CLAIM)
    assert newest == in_store and still == [], \
        (f"the payer's $100.00 for claim 1 is in the store but {len(still)} item(s) for it are still listed as work; the later "
         f"cycles ran detection {['detect' in o for o in outs]}; the newest analysis read {newest} of {in_store} lines ({runs})")
    assert "detect" in outs[0] and "detect" not in outs[1], "the owed analysis runs once, then the cycles are quiet again"
    assert _open(db, OTHER_CLAIM), "the denial nobody paid stays listed"


KILLED_CHILD = r'''
import os, signal, sys
sys.path.insert(0, sys.argv[4])
from remitwatch import detect
from remitwatch.pipeline import run_cycle

def killed(self, *_a, **_k):        # the system's memory limit (or a stop) ends the process in the middle of the rules
    os.kill(os.getpid(), signal.SIGKILL)
detect.Detector.run_claim_rules = killed
run_cycle(sys.argv[1], sync=False, local=sys.argv[2], email=False, report=False, report_dir=sys.argv[3], log=lambda *a: None)
'''


def test_server_cycle_killed_during_its_analysis_as_the_scheduler_sees_it(tmp_path, monkeypatch):
    """(verifier) The cycle runs in a child process (pipeline.Scheduler.run_child); the child is killed (SIGKILL: the memory
    limit, a stop) in the middle of the rules, after the cycle committed b.RMT. The scheduler removes its lock; the next
    cycles see nothing new. b.RMT's payment must reach the items."""
    import signal
    import subprocess
    import time
    from remitwatch.pipeline import clear_lock_of_pid
    db = _world(tmp_path, monkeypatch)
    script = tmp_path / "killed_child.py"
    script.write_text(KILLED_CHILD)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    started = time.time()
    proc = subprocess.Popen([sys.executable, "-I", "-B", str(script), db, _second(tmp_path), str(tmp_path / "rep"), ROOT], env=env)
    rc = proc.wait(timeout=600)
    assert rc == -signal.SIGKILL, rc
    assert clear_lock_of_pid(db, proc.pid, since=started), "the killed cycle's lock was left behind and removed"
    outs = [_server_cycle(db), _server_cycle(db)]
    newest, in_store, runs = _coverage(db)
    still = _open(db, PAID_CLAIM)
    assert in_store == 4, "b.RMT's line is in the store (the killed cycle committed it before its analysis)"
    assert newest == in_store and still == [], \
        (f"after the killed cycle the later cycles ran detection {['detect' in o for o in outs]}; the newest analysis read "
         f"{newest} of {in_store} lines ({runs} analyses); {len(still)} item(s) for the paid claim are still listed as work")


@pytest.mark.parametrize("step", ["archive_requests", "ema_waiting"])
def test_server_a_cycle_stopped_after_reading_and_before_its_analysis_leaves_the_analysis_owed(tmp_path, monkeypatch, step):
    """The container is stopped (SystemExit: a deploy) after the cycle committed b.RMT but before the analysis started:
    in the step that brings archived bills back, or in the one that reads EMA's waiting reports again. The next cycle,
    with nothing new, must analyse b.RMT all the same."""
    from remitwatch import archive, pipeline
    db = _world(tmp_path, monkeypatch)

    def stopped(*_a, **_k):
        raise SystemExit("container stopped")
    with monkeypatch.context() as m:
        if step == "archive_requests":
            m.setattr(archive, "process_requests", stopped)
        else:
            m.setattr(pipeline, "reread_waiting", stopped)
        with pytest.raises(SystemExit):
            _server_cycle(db, _second(tmp_path))
    assert _paid_cents(db, PAID_CLAIM) == 10000, "the stopped cycle committed b.RMT"
    out = _server_cycle(db)
    assert "detect" in out, out
    assert _open(db, PAID_CLAIM) == []


def test_server_quiet_cycles_do_not_analyse_again_and_leave_no_analysis_owed(tmp_path, monkeypatch):
    """The mark costs nothing on a quiet day: a cycle that reads nothing new analyses nothing and leaves no analysis owed,
    and a cycle that reads a file analyses it once."""
    from remitwatch.pipeline import DETECT_PENDING
    db = _world(tmp_path, monkeypatch)
    for _ in range(2):
        out = _server_cycle(db)
        assert "detect" not in out and "error" not in out, out
    s = Store(db)
    try:
        assert s.conn.execute("SELECT 1 FROM meta WHERE key=?", (DETECT_PENDING,)).fetchone() is None
    finally:
        s.close()
    assert "detect" in _server_cycle(db, _second(tmp_path))
    assert "detect" not in _server_cycle(db)
    assert _open(db, PAID_CLAIM) == []


def test_server_an_analysis_that_keeps_failing_is_tried_again_and_said_every_cycle(tmp_path, monkeypatch):
    """A code bug in the analysis: every cycle while it is owed tries again, and each says so on the Data tab, instead of
    the first failure being followed by quiet cycles whose lists miss what was read."""
    from remitwatch.pipeline import status
    db = _world(tmp_path, monkeypatch)
    with monkeypatch.context() as m:
        _stop_inside_detection(m, "run_claim_rules", ZeroDivisionError("a code bug"))
        for local in (_second(tmp_path), None):
            with pytest.raises(ZeroDivisionError):
                _server_cycle(db, local)
            assert "ZeroDivisionError" in ((status(db).get("last_cycle") or {}).get("error") or "")
    out = _server_cycle(db)                 # the fix deployed: the owed analysis runs
    assert "detect" in out and "error" not in out, out
    assert _open(db, PAID_CLAIM) == []


def test_server_imported_store_whose_analysis_was_owed_on_vercel_is_analysed_by_the_first_cycle(tmp_path, monkeypatch):
    """A store moved from the Vercel site (import-cloud) whose last run read files and handed the analysis on
    (cloud:detect_pending): the server's first cycle analyses it, with no new file."""
    db = _world(tmp_path, monkeypatch)
    s = Store(db)
    s.ingest_835_paths([os.path.join(_second(tmp_path), "b.RMT")])
    s.mark_superseded()
    s.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('cloud:detect_pending', '1')")
    s.conn.commit()
    s.close()
    out = _server_cycle(db)
    assert "detect" in out, out
    assert _open(db, PAID_CLAIM) == []


@pytest.mark.parametrize("stopped", [False, True], ids=["control", "analysis_stopped"])
def test_server_no_chase_fax_for_an_appeal_paid_on_a_remittance_read_in_a_cycle_whose_analysis_stopped(tmp_path, monkeypatch, stopped):
    """(verifier) Claim 1's denial was appealed by fax, and a cycle analysed after that (it saw the person's Sent). The
    payer pays claim 1 on b.RMT. Control: the cycle that reads b.RMT analyses it and the payment closes the item, which ends
    the follow-ups. Stopped: the analysis of that cycle stops; the next cycle sees nothing new and nothing a person did,
    and analyses what was read: the follow-ups (hotfix 2d D4's guard) must not fax the payer about a claim the store shows
    paid, in either case."""
    from remitwatch import actions, fax
    chase_day = "2026-12-28"            # 67 days after the appeal went out: past every payer's time to answer
    faxed_at = "2026-10-22T10:00:00+00:00"
    db = _world(tmp_path, monkeypatch)
    fid = _open(db, PAID_CLAIM)[0]
    s = Store(db)
    try:
        actions.apply_status(s.conn, fid, "appealed", "Faxed to the payer (invented).", actor="biller", at=faxed_at)
        actions.apply_fax(s.conn, {"fax_key": "out:fake:1", "direction": "out", "finding_id": fid, "packet_id": "pk1",
                                   "payer_group": "Anthem / BCBS", "number": "8005550142", "purpose": "appeal", "level": 1,
                                   "provider": "fake", "provider_id": "F1", "status": "sent", "created_at": faxed_at,
                                   "sent_at": faxed_at})
        s.conn.commit()
    finally:
        s.close()

    def chase() -> list[tuple]:
        s = Store(db)
        try:
            return [(d["action"], bool(d.get("paid"))) for d in fax.followups(s.conn, today=chase_day)]
        finally:
            s.close()
    out = _server_cycle(db)                     # the person's Sent is seen by an analysis (people_changed)
    assert out.get("people_changed") and "detect" in out, out
    assert chase() == [("second_request", False)], "before the payment the appeal is chased"
    if stopped:
        with monkeypatch.context() as m:
            _stop_inside_detection(m, "run_claim_rules", MemoryError("the analysis ran out of memory"))
            with pytest.raises(MemoryError):
                _server_cycle(db, _second(tmp_path))
        later = [_server_cycle(db), _server_cycle(db)]
        assert "detect" in later[0], "the analysis the stopped cycle owed runs in the next one"
    else:
        assert "detect" in _server_cycle(db, _second(tmp_path))
    faxed = [a for a, paid in chase() if a in ("second_request", "next_level", "records", "payment") and not paid]
    assert faxed == [], (f"the store holds the payer's $100.00 for claim 1 (b.RMT), yet the follow-ups would fax the payer "
                         f"{faxed} for it: {chase()}")


def test_scope_a_persons_status_change_after_the_stopped_cycle_makes_the_next_cycle_analyse(tmp_path, monkeypatch):
    """(verifier, scope) Any status a person sets after the stopped cycle makes the next cycle analyse, and the paid item
    leaves the list then."""
    from remitwatch.actions import apply_status
    db = _world(tmp_path, monkeypatch)
    with monkeypatch.context() as m:
        _stop_inside_detection(m, "save", sqlite3.OperationalError("database is locked"))
        with pytest.raises(sqlite3.OperationalError):
            _server_cycle(db, _second(tmp_path))
    s = Store(db)
    assert apply_status(s.conn, _open(db, OTHER_CLAIM)[0], "in_progress", actor="biller")
    s.conn.commit()
    s.close()
    out = _server_cycle(db)
    assert out.get("people_changed") and "detect" in out, out
    newest, in_store, _ = _coverage(db)
    assert newest == in_store and _open(db, PAID_CLAIM) == []


def test_scope_force_detect_recovers(tmp_path, monkeypatch):
    """(verifier, scope) `cycle --force-detect` analyses the remittance the stopped cycle read."""
    from remitwatch.pipeline import run_cycle
    db = _world(tmp_path, monkeypatch)
    with monkeypatch.context() as m:
        _stop_inside_detection(m, "run_claim_rules", MemoryError("the analysis ran out of memory"))
        with pytest.raises(MemoryError):
            _server_cycle(db, _second(tmp_path))
    out = run_cycle(db, sync=False, email=False, report=False, force_detect=True,
                    report_dir=os.path.join(os.path.dirname(db), "rep"), log=lambda *a: None)
    assert "detect" in out
    assert _open(db, PAID_CLAIM) == []


def test_parity_serverless_recovers_by_itself(tmp_path, monkeypatch):
    """(verifier, host parity) The same sequence on the serverless cycle. Run 2's analysis stops; its image is not saved;
    run 3 (nothing new in the folder) still analyses b.RMT and the item leaves the list."""
    root, client, state = _cloud_box(tmp_path, monkeypatch)
    folder = root / "Trizetto Data"
    folder.mkdir(parents=True)
    (folder / "a.RMT").write_bytes(remit(_denied(1) + _visit(2) + _denied(3), "100.00", seg_count=99, isa_ctrl="000000501",
                                         pay_date="20261020"))
    os.utime(folder / "a.RMT", (1_790_000_000, 1_790_000_000))
    out = _cycle(state, client)
    assert "error" not in out, out

    def open_in_image():
        store, existed = cloud.load_raw(state)
        try:
            assert existed
            rows = [r[0] for r in store.conn.execute("""SELECT finding_id FROM findings WHERE patient_control_number=?
                                                       AND status IN ('open','in_progress') AND COALESCE(stale,0)=0""", (PAID_CLAIM,))]
            lines = store.conn.execute("SELECT COUNT(*) FROM lines").fetchone()[0]
            newest = store.conn.execute("SELECT lines FROM detection_runs ORDER BY finished_at DESC, rowid DESC LIMIT 1").fetchone()[0]
            return rows, newest, lines
        finally:
            store.close()
    assert open_in_image()[0], "the denial of claim 1 is listed as work"
    (folder / "b.RMT").write_bytes(remit(_visit(1), "100.00", seg_count=99, isa_ctrl="000000502", pay_date="20261027"))
    os.utime(folder / "b.RMT", (1_790_100_000, 1_790_100_000))
    calls = []
    with monkeypatch.context() as m:
        def stopped(self, *_a, **_k):
            calls.append(1)
            raise MemoryError("the analysis ran out of memory")
        m.setattr(detect.Detector, "run_claim_rules", stopped)
        try:
            got = _cycle(state, client)
        except MemoryError:
            got = {"raised": True}
    assert calls, f"run 2 never reached the analysis: {got}"
    assert got.get("raised") or got.get("error"), got
    for _ in range(2):
        later = _cycle(state, client)
        assert "error" not in later, later
    rows, newest, lines = open_in_image()
    assert newest == lines and rows == [], f"serverless: still listed {rows}, analysis read {newest} of {lines}; stopped run: {got}"


# --------------------------------------------------------------------------- hosts-2: supersession decided after a stop

def test_server_supersession_is_decided_after_a_cycle_stopped_between_reading_and_deciding(tmp_path, monkeypatch):
    """(reviewer) Cycle 1 reads a.RMT. Cycle 2 reads b.RMT, a later complete version of the same remittance (same trace,
    payer and date, one claim added), and stops before supersession is decided (dropbox_sync.ingest_new_files commits the
    file, then calls Store.mark_superseded). Cycle 3 has no new file: the two versions must not both be counted."""
    db, local = _server_world(tmp_path, monkeypatch)
    assert "error" not in _server_cycle(db, local)
    s = Store(db)
    once = _live(s)
    s.close()
    second = tmp_path / "second"
    second.mkdir()
    (second / "b.RMT").write_bytes(_era([_visit(1), _visit(2), _visit(3)], "300.00", "000000392", trace_ctrl="000000381"))

    def stopped(self):
        raise SystemExit("container stopped")
    with monkeypatch.context() as m:
        m.setattr(Store, "mark_superseded", stopped)
        with pytest.raises(SystemExit):
            _server_cycle(db, str(second))
    out = _server_cycle(db)                                  # nothing new arrives
    s = Store(db)
    after = _live(s)
    s.close()
    assert once == (2, 20000)
    assert after == (3, 30000), f"two versions of one remittance both counted after the stopped cycle: claims and cents {after}"
    assert "detect" in out, "the analysis counts the remittance once"
    newest, _in_store, _ = _coverage(db)
    s = Store(db)
    standing = s.conn.execute("""SELECT COUNT(*) FROM lines l JOIN transactions t ON t.transaction_key = l.transaction_key
                                 WHERE COALESCE(t.superseded, 0) = 0""").fetchone()[0]
    s.close()
    assert newest == standing == 3, "the analysis read the standing version's lines only"


@pytest.fixture(params=["file", "compact"])
def kind(request):
    return request.param


def _store(tmp_path, kind: str) -> Store:
    return Store(str(tmp_path / "s.sqlite")) if kind == "file" else Store(":memory:", compact=True)


def test_supersession_is_decided_again_whenever_a_remittance_was_read_after_the_last_decision(tmp_path, kind):
    """On both store kinds: a remittance read with nothing deciding supersession after it (a stop in between, or a reader
    that does not decide) is decided by the next recheck, whatever the rule's version says; with nothing read since, the
    recheck decides nothing."""
    from remitwatch import dropbox_sync
    s = _store(tmp_path, kind)
    try:
        d = tmp_path / "in"
        d.mkdir()
        a, b = d / "a.RMT", d / "b.RMT"
        a.write_bytes(_era([_visit(1), _visit(2)], "200.00", "000000381"))
        b.write_bytes(_era([_visit(1), _visit(2), _visit(3)], "300.00", "000000392", trace_ctrl="000000381"))
        os.utime(a, (1_790_000_000, 1_790_000_000))
        os.utime(b, (1_790_100_000, 1_790_100_000))
        dropbox_sync.ingest_new_files(s, [str(a)], log=lambda *x: None)
        assert _live(s) == (2, 20000)
        assert s.recheck_supersession() is False, "nothing read since the decision"
        s.ingest_835_paths([str(b)])                 # read, and stopped before the decision
        assert _live(s) == (5, 50000)
        assert s.recheck_supersession() is True
        assert _live(s) == (3, 30000)
        assert s.recheck_supersession() is False, "decided: the next recheck has nothing to do"
    finally:
        s.close()


def test_supersession_recheck_on_a_store_decided_by_a_release_that_recorded_no_position(tmp_path, kind):
    """A store whose supersession was decided by the release before this one (the rule's version and no record of what it
    saw) is decided once more, and the flags it gets are the same."""
    from remitwatch import dropbox_sync
    from remitwatch.store import SUPERSEDE_SEEN
    s = _store(tmp_path, kind)
    try:
        d = tmp_path / "in"
        d.mkdir()
        for i, (name, data) in enumerate((("a.RMT", _era([_visit(1), _visit(2)], "200.00", "000000381")),
                                          ("b.RMT", _era([_visit(1), _visit(2), _visit(3)], "300.00", "000000392",
                                                         trace_ctrl="000000381")))):
            (d / name).write_bytes(data)
            os.utime(d / name, (1_790_000_000 + i, 1_790_000_000 + i))
        dropbox_sync.ingest_new_files(s, [str(d / "a.RMT"), str(d / "b.RMT")], log=lambda *x: None)
        assert _live(s) == (3, 30000)
        s.conn.execute("DELETE FROM meta WHERE key=?", (SUPERSEDE_SEEN,))
        s.conn.commit()
        assert s.recheck_supersession() is False, "decided again, and no flag changed"
        assert s.conn.execute("SELECT 1 FROM meta WHERE key=?", (SUPERSEDE_SEEN,)).fetchone() is not None
        assert _live(s) == (3, 30000)
    finally:
        s.close()


# --------------------------------------------------------------------------- hosts-3: tools that read a copy

def _stopped_over_its_image(tmp_path, monkeypatch):
    """A serverless state folder whose run stopped because the store image in it is not the one the last run saved."""
    root, client, state = _cloud_box(tmp_path, monkeypatch)
    folder = _two_runs_folder(root)
    assert "error" not in _cycle(state, client)
    older = state.get(cloud.RAW)
    (folder / "b.RMT").write_bytes(_era([_visit(3)], "100.00", "000000372"))
    assert "error" not in _cycle(state, client)
    state.put(cloud.RAW, older)                         # an older copy put back: the run stops (IMAGE_OTHER)
    out = _cycle(state, client)
    assert "not the one the last run saved" in (out.get("error") or "")
    return root, client, state


def test_diagnose_builds_a_bundle_for_a_state_folder_whose_run_stopped_over_its_image(tmp_path, monkeypatch):
    """(reviewer) The operator copies the state folder and asks for the bundle, as docs/TROUBLESHOOTING.md says: the
    bundle is made, and it says that the image is not the one the last run saved."""
    import zipfile

    from remitwatch import diagnose
    root, _client, _state = _stopped_over_its_image(tmp_path, monkeypatch)
    bundle = tmp_path / "bundle.zip"
    rc = diagnose.main(["--state-dir", str(root / "RemitWatch-state"), "--out", str(bundle), "--days", "7"])
    assert rc == 0 and bundle.exists()
    with zipfile.ZipFile(bundle) as z:
        text = "\n".join(z.read(n).decode("utf-8", "replace") for n in z.namelist() if n.endswith((".md", ".json", ".txt")))
    assert "not the one the last run saved" in text


def test_compare_cloud_reads_a_state_folder_whose_run_stopped_over_its_image(tmp_path, monkeypatch, capsys):
    """(reviewer) The same state folder, read by the move's proof (compare-cloud): it compares, and says in a sentence
    that the image is not the one the last run saved, instead of stopping on an exception."""
    from remitwatch.cli import main
    root, _client, _state = _stopped_over_its_image(tmp_path, monkeypatch)
    db = str(tmp_path / "server" / "remitwatch.sqlite")
    os.makedirs(os.path.dirname(db))
    Store(db).close()
    monkeypatch.setenv("REMITWATCH_STATE_DIR", str(root / "RemitWatch-state"))
    rc = main(["--db", db, "compare-cloud"])
    assert rc in (0, 1)
    assert "not the one the last run saved" in capsys.readouterr().out


def test_import_cloud_refuses_an_image_the_last_run_did_not_save_with_its_sentence(tmp_path, monkeypatch, capsys):
    """import-cloud on that state folder moves nothing: it prints the image check's sentence and exits 1, with no
    traceback and no store written."""
    from remitwatch.cli import main
    root, _client, _state = _stopped_over_its_image(tmp_path, monkeypatch)
    db = str(tmp_path / "server" / "remitwatch.sqlite")
    monkeypatch.setenv("REMITWATCH_STATE_DIR", str(root / "RemitWatch-state"))
    rc = main(["--db", db, "import-cloud"])
    assert rc == 1
    said = capsys.readouterr().out
    assert "not the one the last run saved" in said and "Traceback" not in said
    assert not os.path.exists(db)
