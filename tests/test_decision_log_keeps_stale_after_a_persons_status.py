"""
The decision log ("Why did this change?") keeps the change a run makes to an item, also when a person worked the item since
the run before (replay-fix-2a A3, from a month-by-month replay of real data: 393 of the 726 items the monthly runs took off
the list had no row for that run, every one of them an item a person had worked since the run before; the 333 nobody had
touched since had theirs).

runlog.record_decisions left out a state, follow-up, status, hide, stale or reopened change on any item with a person's
event since the PREVIOUS run ended (Snapshot.prev_run_at), so that a person's own change is not written up as the run's.
But the run's 'before' is taken when this run starts (Detector.run calls runlog.snapshot_items first): a status a person set
before that is already in it, so what differs between before and after is the run's own doing. A stale mark is never a
person's: Detector.save sets it when no rule produces a worked item any more (rule detect:no_longer_listed). The item then
left every list, and its drawer had no row saying when, by which run and by which rule.

What holds now:
  1. A stale mark the run sets is in the decision log, whenever the person acted before the run began, with the rule
     detect:no_longer_listed.
  2. The control: the same change, with one detection run between the person's status and the answer, has its row too.
  3. The guard: a status a person sets while the run runs, after its 'before' was taken, stays the person's and is not
     written up as the run's, also when the same run sets the stale mark: the row carries the stale mark (kind 'stale',
     rule detect:no_longer_listed), never the person's Let go or reopen as a hide rule or a payment that no longer stands.
  4. Who changed what is decided by the history line's id against the snapshot's newest (Snapshot.max_event), never by a
     time stored to the second: a person's line written before the run's 'before' is already in it, so the run's own
     change on that item is logged (review r1 of replay-fix-2a).

Every name, claim number, check number, amount and date is invented (tests/ema_fixtures.py and the builders of
tests/test_ema_wiring.py, tests/test_features.py and tests/test_workable.py). The unit cases run on the server's store
file and on the compact in-memory store the serverless cycle builds; the cycle's case on those and on that store's image
serialized and opened again between runs.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch import actions, ema_checks as ec, ema_reports as er, runlog, states  # noqa: E402
from remitwatch.detect import ensure_schema  # noqa: E402
from remitwatch.store import Store  # noqa: E402
from tests import ema_fixtures as F  # noqa: E402

KINDS = ["file", "compact"]
FID = "5e1fc0ffee5e1fc0ffee0001"          # an invented item id (24 hex)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(er, "CLAIM_HOST_SUFFIX", F.HOST_SUFFIX)
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    monkeypatch.setenv("REMITWATCH_EMA_STRICT", "1")
    monkeypatch.setenv("REMITWATCH_TZ", "America/New_York")
    for k in ("VERCEL", "REMITWATCH_EMA_REPORTS", "REMITWATCH_EMA_CLAIM_URL", "REMITWATCH_EMA_PAYER_ALIASES", "REMITWATCH_EMA_LEDGER",
              "REMITWATCH_EMA_REPORT_KEEP", "REMITWATCH_EMA_CLOSES", "REMITWATCH_STATE_DIR"):
        monkeypatch.delenv(k, raising=False)
    for cache in (getattr(ec, "_LAST_POST", None), getattr(states, "_RESPONSE_CACHE", None), getattr(states, "_RECOVERY_CACHE", None)):
        if cache is not None:
            cache.clear()


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat(timespec="seconds")


def _stale_rows(conn, fid: str, run_id: str) -> list[tuple]:
    """The run's decision rows on the item whose change carries the stale mark set (0 -> 1)."""
    out = []
    for kind, rule, change in conn.execute("SELECT kind, rule, change FROM item_decisions WHERE finding_id=? AND run_id=?", (fid, run_id)):
        ch = json.loads(change) if change else {}
        if ch.get("stale") == [0, 1]:
            out.append((kind, rule, ch))
    return out


# ------------------------------------------------------------------ the decision log itself, as Detector.run calls it
def _plain_store(tmp_path, kind: str) -> Store:
    s = Store(str(tmp_path / "s.sqlite")) if kind == "file" else Store(":memory:", compact=True)
    ensure_schema(s.conn)
    runlog.ensure_schema(s.conn)
    return s


def _worked_item(conn, now: datetime, status: str = "open", auto_note: str | None = None) -> None:
    """A second payer never billed, listed by the run that ended an hour ago (invented); with auto_note, closed by that run."""
    row = dict(finding_id=FID, signal="SECONDARY_NOT_BILLED", category="denial", status=status, work_state="work_now",
               payer_group="Testcare Health", line_key="0f0f0f0f0f0f0f0f:1:0001:1:1", claim_key="0f0f0f0f0f0f0f0f:1:0001:1",
               shortfall_cents=3000, appeal_deadline="2027-03-02", follow_up_on=None, stale=0, evidence="{}",
               first_seen=_iso(now - timedelta(hours=2)), last_seen=_iso(now - timedelta(hours=2)))
    if auto_note:
        row.update(auto_note=auto_note, prev_status="open")
    cols = list(row)
    conn.execute(f"INSERT INTO findings ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [row[c] for c in cols])
    conn.execute("INSERT INTO detection_runs (run_id, started_at, finished_at, version) VALUES ('run-1', ?, ?, 'x')",
                 (_iso(now - timedelta(hours=2)), _iso(now - timedelta(hours=1, minutes=55))))
    conn.commit()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("person_acted", ["since_the_run_before", "before_the_run_before"])
def test_a_stale_mark_the_run_sets_is_logged_whenever_the_person_acted(tmp_path, kind, person_acted):
    """A person marks the item billed (Sent), as the server's API records it, either after the run before ended or before
    it did. This run starts (its 'before' holds the person's Sent), finds no rule producing the item any more (the second
    payer paid) and keeps the worked row, stale. The stale mark is the run's in both cases; today it is logged only in the
    second."""
    s = _plain_store(tmp_path, kind)
    try:
        c = s.conn
        now = datetime.now(timezone.utc)
        _worked_item(c, now)
        at = now - (timedelta(hours=1) if person_acted == "since_the_run_before" else timedelta(hours=3))
        assert actions.apply_status(c, FID, "appealed", "Billed the second payer", None, "Biller One", _iso(at))
        c.commit()
        before = runlog.snapshot_items(c)                       # Detector.run: the items as they are when this run starts
        assert before.get(FID, "status") == "appealed", "the person's status is already in the run's 'before'"
        c.execute("UPDATE findings SET stale=1 WHERE finding_id=?", (FID,))     # Detector.save: a worked item no rule produces
        c.commit()
        res = runlog.record_decisions(c, "run-2", before, rule_version="x")
        got = _stale_rows(c, FID, "run-2")
        assert all(rule == "detect:no_longer_listed" for _k, rule, _ch in got), got
        assert got, (
            f"run-2 took the item off every list (stale 0 -> 1, rule detect:no_longer_listed) and the decision log has no row "
            f"for it: the person's Sent of {person_acted.replace('_', ' ')} was already in the run's 'before', yet the change "
            f"was counted as theirs ({res['by_kind']})")
    finally:
        s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_guard_a_status_a_person_sets_while_the_run_runs_stays_theirs(tmp_path, kind):
    """The person presses Let go after the run took its 'before' (the server's web process writes while the cycle runs) and
    the run keeps their status (Detector.save keeps people's work). That change is the person's: it is in their own history
    and must not be written up as the run's (this holds today; a fix must keep it)."""
    s = _plain_store(tmp_path, kind)
    try:
        c = s.conn
        now = datetime.now(timezone.utc)
        _worked_item(c, now)
        before = runlog.snapshot_items(c)
        assert actions.apply_status(c, FID, "dismissed", "Let go: the patient paid the share at the desk", None, "Biller One")
        c.commit()
        runlog.record_decisions(c, "run-2", before, rule_version="x")
        rows = c.execute("SELECT kind, rule FROM item_decisions WHERE finding_id=? AND run_id='run-2'", (FID,)).fetchall()
        assert rows == [], f"a person's Let go, pressed while the run ran, was written up as the run's: {rows}"
    finally:
        s.close()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("pressed", ["dismissed", "open"], ids=["let_go", "reopened"])
def test_a_status_pressed_while_the_run_runs_is_never_written_up_as_a_rule_when_the_run_also_sets_stale(tmp_path, kind, pressed):
    """The guard above with the run's stale mark in the same change (review r1 of replay-fix-2a): the person presses Let go
    (or reopens an item the run before had closed) after this run took its 'before', and the same run keeps the worked row,
    stale. The stale mark is the run's and is logged (rule detect:no_longer_listed); the Let go or the reopen is the
    person's, in their own history, and is never written up as a hide rule's or as 'the payment no longer stands'."""
    s = _plain_store(tmp_path, kind)
    try:
        c = s.conn
        now = datetime.now(timezone.utc)
        if pressed == "open":
            _worked_item(c, now, status="resolved", auto_note="Paid on a later remittance (invented)")
        else:
            _worked_item(c, now)
        before = runlog.snapshot_items(c)
        assert actions.apply_status(c, FID, pressed, "Pressed while the run ran (invented)", None, "Biller One")
        c.execute("UPDATE findings SET stale=1 WHERE finding_id=?", (FID,))      # Detector.save: no rule produces it any more
        c.commit()
        runlog.record_decisions(c, "run-2", before, rule_version="x")
        rows = [(k, r, json.loads(ch)) for k, r, ch in c.execute(
            "SELECT kind, rule, change FROM item_decisions WHERE finding_id=? AND run_id='run-2'", (FID,))]
        wrong = [(k, r) for k, r, ch in rows if r.startswith("hide_rule:") or r == "detect:payment_no_longer_stands"
                 or "status" in ch or "hidden" in ch]
        assert not wrong, f"the person's {pressed} was written up as the run's: {rows}"
        assert [(k, r) for k, r, ch in rows if ch.get("stale") == [0, 1]] == [("stale", "detect:no_longer_listed")], (
            f"the run's stale mark is not logged as its own: {rows}")
    finally:
        s.close()


# ------------------------------------------------------------------ the same through the daily cycle's detection runs
from tests.test_handoff_started_is_judged_by_the_rules import ANSWER, FIRST, PCN, World as ChaseWorld  # noqa: E402
from tests.test_features import claim, clean_visits, svc  # noqa: E402
from tests.test_workable import ema  # noqa: E402

CYCLE_KINDS = ["file", "compact"] + (["image"] if hasattr(__import__("sqlite3").Connection, "serialize") else [])


def _chase_sent_then_answered(tmp_path, kind: str, *, run_between: bool) -> tuple[str, ChaseWorld]:
    """Anthem answers 25 visits on Nov 10, 2026. EMA billed a 99214 of Sep 14 on Sep 15 and no 835 answers it: the run of
    Nov 20 lists its chase (NO_ANSWER). A biller marks it Sent, recorded at the moment of the press, as the API does. With
    run_between, the next cycle analyses (a person changed something) before anything else arrives. Anthem's answer arrives
    (CO-50, $0.00): the run of Nov 30 lists the answer and keeps the worked chase, stale (no rule produces it any more)."""
    w = ChaseWorld(tmp_path, kind)
    w.remit(clean_visits(1, 20, "99214", "223.65", "117.60"), "20261110")
    w.remit(clean_visits(21, 5, "99214", "223.65", "117.60"), "20261110")
    ensure_schema(w.conn)
    ema(w.conn, PCN)
    w.run("r1", FIRST)
    chase = [r for r in w.items() if r["signal"] == "NO_ANSWER" and r["status"] == "open"]
    assert len(chase) == 1, [(r["signal"], r["status"]) for r in w.items()]
    fid = chase[0]["finding_id"]
    time.sleep(1.1)                 # the log's times are to the second: the press comes after the run that listed it
    assert actions.apply_status(w.conn, fid, "appealed", "Sent again (invented)", None, "Biller One (invented)")
    w.conn.commit()
    if run_between:
        time.sleep(1.1)
        w.run("r-between", "2026-11-21")
        time.sleep(1.1)
    den = claim(0, svc("99214", "223.65", "0.00", None, "CAS*CO*50*223.65~", dos="20260914"), dos="20260914").replace("CB0000000C017", PCN)
    w.remit([den], "20261125", total="0.00")
    w.run("r2", ANSWER)
    return fid, w


@pytest.mark.parametrize("kind", CYCLE_KINDS)
@pytest.mark.parametrize("run_between", [False, True], ids=["next_run_after_the_press", "control_a_run_between"])
def test_the_run_that_takes_a_worked_chase_off_the_list_says_so_in_its_log(tmp_path, kind, run_between):
    fid, w = _chase_sent_then_answered(tmp_path, kind, run_between=run_between)
    try:
        f = next(x for x in w.items() if x["finding_id"] == fid)
        assert f["stale"] == 1 and f["status"] == "appealed", (f["stale"], f["status"])     # the person's Sent stands; off the lists
        got = _stale_rows(w.conn, fid, "r2")
        assert got, ("the run of Nov 30 took the worked chase off every list (stale 0 -> 1) and its drawer's 'Why did this "
                     "change?' has no row for it: the person's Sent came before the run began, yet the change was counted as "
                     "theirs")
        assert {rule for _k, rule, _ch in got} == {"detect:no_longer_listed"}, got
    finally:
        w.store.close()
