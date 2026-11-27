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


# --------------------------------------------------------------------------- safe mode and the new items of a held run

@pytest.fixture(params=["file", "compact"])
def sc_store(request, tmp_path, monkeypatch):
    """A store of either kind, as tests/test_selfcheck.py builds it (the self-check runs on both hosts)."""
    from remitwatch import ema_reports as er
    from remitwatch import selfcheck as sc
    from tests import ema_fixtures as F
    from tests.test_selfcheck import _new
    monkeypatch.setattr(er, "CLAIM_HOST_SUFFIX", F.HOST_SUFFIX)
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    for k in ("VERCEL", "REMITWATCH_TODAY", "REMITWATCH_MEMORY_BUDGET_MB", "REMITWATCH_MAX_SECONDS", "REMITWATCH_CYCLE_BUDGET_SECONDS"):
        monkeypatch.delenv(k, raising=False)
    sc._GROUPS.clear()
    s = _new(request.param, tmp_path)
    yield s
    s.close()


def _ledger_off(c) -> None:
    """The posting ledger and the store's copy of it disagree by $5 (the hook's fault run: rows gone from the ledger)."""
    from remitwatch import selfcheck as sc
    sc.meta_set(c, sc.K_MONEY, {"checks": [sc._result("ledger_store", [("cell-1", 500, None)])]})


def _ledger_on(c) -> None:
    from remitwatch import selfcheck as sc
    sc.meta_set(c, sc.K_MONEY, {"checks": [sc._result("ledger_store", [])]})


def test_a_run_held_over_the_data_it_read_holds_its_new_items_with_it_and_says_so_with_their_dollars(sc_store):
    """The failing check is about the input (the posting ledger against the store's copy of it): the run's new items rest
    on data that check distrusts, so they are held with the run, counted on the banner and the Data tab with their
    dollars and why; the next run that passes lists them."""
    from remitwatch import selfcheck as sc
    from tests.test_selfcheck import _listed, add_item, run
    c = sc_store.conn
    add_item(c, "F-1")
    add_item(c, "F-2")
    _ledger_on(c)
    run(c, None, "r1")

    def found(cn):
        add_item(cn, "F-new", line_key="Lnew", shortfall_cents=7500, appeal_deadline="2026-12-02")
    _ledger_off(c)
    out = run(c, found, "r2")
    assert out["held"] and out["hold_checks"] == ["ledger_store"], out
    assert "F-new" not in _listed(c), "a new item resting on distrusted data is held with the run"
    safe = sc.safe_mode(c)
    assert (safe["new_items"], safe["new_items_held"], safe["new_items_held_cents"], safe["new_items_why"]) == (0, 1, 7500, "input")
    assert "held the 1 new item to work ($75.00)" in safe["text"] and "rest on data a failing check distrusts" in safe["text"]
    banner = sc.banner(c)["text"]
    assert "holds back the 1 new item ($75.00)" in banner and "distrusts" in banner, banner
    _ledger_on(c)                                            # the ledger is right again: the next run passes
    out = run(c, found, "r3")
    assert not out["held"] and "F-new" in _listed(c)
    assert not sc.safe_mode(c)["on"] and sc.banner(c) is None


def test_a_run_held_over_its_own_closures_still_lists_its_new_items_and_says_why(sc_store):
    """The failing check is about what the run decided (a closure without its proof), not the data it read: the current
    rule stands (the new items to work are listed), and the banner says so and why, with their dollars."""
    from remitwatch import selfcheck as sc
    from tests.test_selfcheck import _listed, add_item, bad_closure, run
    c = sc_store.conn
    add_item(c, "F-1")
    add_item(c, "F-2")
    run(c, None, "r1")

    def found(cn):
        bad_closure(cn)
        add_item(cn, "F-new", line_key="Lnew", shortfall_cents=7500, appeal_deadline="2026-12-02")
    out = run(c, found, "r2")
    assert out["held"] and out["hold_checks"] == ["closure_proof"], out
    assert "F-new" in _listed(c)
    safe = sc.safe_mode(c)
    assert (safe["new_items"], safe["new_items_cents"], safe["new_items_held"], safe["new_items_why"]) == (1, 7500, 0, None)
    assert "still lists the 1 new item to work ($75.00)" in safe["text"] and "not the data it read" in safe["text"]
    banner = sc.banner(c)["text"]
    assert "lists the 1 new item ($75.00)" in banner and "not the data it read" in banner, banner


def test_a_run_held_over_both_its_data_and_its_closures_holds_its_new_items(sc_store):
    """Both kinds of check fail: the data is distrusted, so the new items are held (the reason names the data check)."""
    from remitwatch import selfcheck as sc
    from tests.test_selfcheck import _listed, add_item, bad_closure, run
    c = sc_store.conn
    add_item(c, "F-1")
    add_item(c, "F-2")
    _ledger_on(c)
    run(c, None, "r1")

    def found(cn):
        bad_closure(cn)
        add_item(cn, "F-new", line_key="Lnew", shortfall_cents=7500)
    _ledger_off(c)
    out = run(c, found, "r2")
    assert out["held"] and sorted(out["hold_checks"]) == ["closure_proof", "ledger_store"], out
    assert "F-new" not in _listed(c)
    safe = sc.safe_mode(c)
    assert safe["new_items_why"] == "input" and safe["new_items_held"] == 1
    assert "the store's EMA money equals the posting ledger's" in safe["text"], safe["text"]


def test_a_flood_held_with_a_run_is_counted_with_its_dollars(sc_store):
    """The flood rule stands (more new work than a run brings is held with it) and the banner counts it in dollars."""
    from remitwatch import selfcheck as sc
    from tests.test_selfcheck import _listed, add_item, bad_closure, run
    c = sc_store.conn
    add_item(c, "F-1")
    add_item(c, "F-2")
    run(c, None, "r1")

    def found(cn):
        bad_closure(cn)
        for i in range(30):
            add_item(cn, f"F-new{i:02d}", line_key=f"Lnew{i}", shortfall_cents=1000)
    out = run(c, found, "r2")
    assert out["held"] and not any(f.startswith("F-new") for f in _listed(c))
    safe = sc.safe_mode(c)
    assert (safe["new_items_held"], safe["new_items_held_cents"], safe["new_items_why"]) == (30, 30000, "flood")
    assert "held the 30 new items ($300.00)" in safe["text"] and "more than one run usually brings" in safe["text"]
    assert "holds back the 30 new items ($300.00)" in sc.banner(c)["text"]


# --------------------------------------------------------------------------- Vercel reads EMA's A/R only (decision 6)
from tests.test_cloud import box, site  # noqa: E402,F401  (the Vercel site over an invented state folder)

AR_ONLY = "This site reads EMA's A/R report only."


def test_the_vercel_data_tab_and_setup_page_say_the_site_reads_the_ar_only_so_its_lists_can_differ(site, monkeypatch):
    """The integrator's decision 6: a serverless host reads EMA's A/R report only (ema_reports.switch 'ar'), so its lists
    can differ from the server's, which reads Production as well. The Data tab's analysis panel and the setup page say
    so; with the switch at 'all' (both reports read here too) they do not."""
    from remitwatch import cloud
    tc, auth, _api, _state = site
    monkeypatch.delenv("REMITWATCH_EMA_REPORTS", raising=False)
    an = tc.get("/api/ops/panels", headers=auth).json()["analysis"]
    assert an["press_only"] is True and an["ema"].startswith(AR_ONLY) and "Production" in an["ema"], an
    assert "lists can differ" in an["ema"] and "—" not in an["ema"]
    assert cloud.press_only_text() == cloud.PRESS_ONLY_TEXT + " " + an["ema"]
    monkeypatch.setenv("REMITWATCH_EMA_REPORTS", "all")
    assert tc.get("/api/ops/panels", headers=auth).json()["analysis"]["ema"] == ""
    assert cloud.press_only_text() == cloud.PRESS_ONLY_TEXT
    monkeypatch.setenv("REMITWATCH_EMA_REPORTS", "off")
    assert "neither of EMA's reports" in tc.get("/api/ops/panels", headers=auth).json()["analysis"]["ema"]


def test_the_setup_page_status_carries_the_ar_only_sentence_on_vercel(monkeypatch):
    """setup_status's press_text (the setup page) is the press-only sentence and what the site reads of EMA's reports."""
    from remitwatch import cloud
    monkeypatch.setenv("REMITWATCH_MODE", "serverless")
    monkeypatch.delenv("REMITWATCH_EMA_REPORTS", raising=False)
    for k in ("DROPBOX_APP_KEY", "DROPBOX_APP_SECRET", "DROPBOX_REFRESH_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    st = cloud.setup_status(probe=False)
    assert st.get("press_only") and AR_ONLY in st.get("press_text", ""), st.get("press_text")


@pytest.mark.skipif(not __import__("shutil").which("node"), reason="needs node")
def test_the_data_tab_panel_draws_the_ar_only_line(tmp_path):
    """static/ops.js draws the panel's line for what the site reads of EMA's reports (and nothing when it reads both)."""
    import json
    import subprocess
    script = tmp_path / "draw.js"
    script.write_text(r"""
const fs = require('fs'), vm = require('vm');
const ctx = {console, document: {}, window: {}, state: {}, esc: s => String(s), money: c => '$' + c, opPl: (n, w) => n + ' ' + w};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), ctx);
const a = {press_only: true, text: 'press', analysed: {at: '2026-11-20T10:00:00Z'}};
console.log(JSON.stringify({with: ctx.opAnalysisHtml(Object.assign({ema: process.argv[3]}, a)), without: ctx.opAnalysisHtml(Object.assign({ema: ''}, a))}));
""")
    ops = os.path.join(ROOT, "remitwatch", "static", "ops.js")
    got = subprocess.run(["node", str(script), ops, AR_ONLY], capture_output=True, text=True, timeout=60)
    assert got.returncode == 0, got.stderr[-1500:]
    out = json.loads(got.stdout.strip().splitlines()[-1])
    assert AR_ONLY in out["with"] and "data-op-ema" in out["with"]
    assert "data-op-ema" not in out["without"]


# --------------------------------------------------------------------------- the self-regulation counts every run once

@pytest.mark.parametrize("mode", ["serverless", "server"])
def test_the_regulation_counts_each_run_once_even_two_that_started_in_the_same_second(sc_store, mode):
    """Runs of a chain start back to back: a press that hands its analysis on in a fraction of a second, then the run
    that analyses. Two runs that started within the same second are two runs: the second's pressure (200 s of a 240 s
    budget) steps the level too, and each run's status is counted once whatever arrives again (the third flaky test of
    the merges: on a fast machine the analysing run's pressure was taken for the run before it and never counted, so
    the chain's last run rebuilt the provider tables on a fast machine and shed them on a loaded one)."""
    from remitwatch import selfcheck as sc
    c = sc_store.conn
    same = "2026-11-27T02:31:13+00:00"
    calm = {"started": same, "finished": same, "seconds": 0.4, "budget_seconds": 240, "trigger": "press", "run_id": "run-20261127T023113Z-aaaa"}
    pressured = {"started": same, "finished": "2026-11-27T02:34:35+00:00", "seconds": 202.7, "budget_seconds": 240, "trigger": "continue",
                 "run_id": "run-20261127T023113Z-bbbb"}
    assert sc.plan_next(c, calm, mode=mode, budget_s=240)["level"] == 0
    assert sc.plan_next(c, pressured, mode=mode, budget_s=240)["level"] == 1, "the analysing run's pressure is counted"
    assert sc.plan_next(c, pressured, mode=mode, budget_s=240)["level"] == 1, "a status already counted is not counted again"
    again = dict(pressured, run_id="run-20261127T023113Z-cccc")
    assert sc.plan_next(c, again, mode=mode, budget_s=240)["level"] == 2


def test_the_regulation_reads_a_status_from_before_run_ids_once(sc_store):
    """A regulation state written by the release before (its last status known by its start alone) does not count that
    status a second time when it comes back with its run id."""
    from remitwatch import selfcheck as sc
    c = sc_store.conn
    st = {"started": "2026-11-27T02:31:13+00:00", "finished": "2026-11-27T02:34:35+00:00", "seconds": 202.7, "budget_seconds": 240}
    assert sc.plan_next(c, st, mode="serverless", budget_s=240)["level"] == 1
    reg = sc.meta_get(c, sc.K_REGULATE, {})
    reg.pop("last_run_key", None)                   # as the release before left it
    sc.meta_set(c, sc.K_REGULATE, reg)
    assert sc.plan_next(c, dict(st, run_id="run-20261127T023113Z-bbbb"), mode="serverless", budget_s=240)["level"] == 1
