"""
A payer's answer to a claim a person was chasing starts Started (Detector.save's hand-off, via_bill: "a new item is a new
matter: it starts To do (Started if someone was working the claim)", docs/OPERATIONS.md). Nobody started that new item:
the only line in its history is the run's own signal_changed (actor 'system'). states._decide read every in_progress
status as a person's ("a person's status wins": work now, rule person_started) before any rule, so the hand-off decided the
item's state for good: past every window of the payer it stayed Work now instead of expiring; while a person worked it in
EMA (a follow-up date) it stayed Work now instead of waiting; paid before the denial it stayed Work now instead of done. Its
basis read "Started by a person on " with no person, no date and no evidence row (replay-fix-2a A2, from a month-by-month
replay of real data: 31 items carried a Started no person set, 9 of them, $1,328.52, in the wrong state).

What holds now:
  1. The answer's item, which no person touched, takes the state the rules give its facts (past the payer's last window it
     is expired, the same state it gets as To do); its label stays Started (its status), and its basis says the Started came
     with the claim's chase, who marked the chase and when.
  2. A person who presses Started on that item themselves keeps it Work now past the window, named and dated (a person's
     decision is never overwritten by an automatic one); a person's lines that move with a whole hand-off do the same.
  3. The self-check past_window looks at such a Started too.
  4. A person's own Started (or Sent, with its faxes) carried to the line's new id by the run (a new signal on the same
     line, or the same claim under its other key: detect.carry_to_heirs) decides the heir's state in the very run that
     carries it, as it did on the old id: Detector.save moves the person's lines and the item's faxes to the heir before
     the states are decided (review r1 of replay-fix-2a: they moved only after, so the heir was judged as To do for one
     run, with a basis naming a hand-off from the claim's chase).

Every claim, member, amount and date is invented (the builders of tests/test_features.py, tests/test_remitwatch.py and
tests/test_workable.py). Each case runs on the server's store file, on the compact in-memory store the serverless cycle
builds, and on that store's image serialized and opened again between runs (cloud.load_raw).
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch import actions, selfcheck, states  # noqa: E402
from remitwatch.detect import Detector, ensure_schema  # noqa: E402
from remitwatch.store import Store  # noqa: E402
from tests.test_features import claim, clean_visits, svc  # noqa: E402
from tests.test_remitwatch import remit  # noqa: E402
from tests.test_workable import ema  # noqa: E402

PCN = "CB0000L71C017"                   # an invented claim id: Anthem, a 99214 of Sep 14, 2026, $223.65
SENT_NOTE = "Not on file at the payer: sent again (invented)"
CAN_SERIALIZE = hasattr(sqlite3.Connection, "serialize")
FIRST, ANSWER, LATER = "2026-11-20", "2026-11-30", "2029-06-01"     # the chase, the answer's run, past every window


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    for k in ("VERCEL", "REMITWATCH_TODAY", "REMITWATCH_EMA_REPORTS", "REMITWATCH_EMA_LEDGER"):
        monkeypatch.delenv(k, raising=False)


class World:
    """One store of a kind, fed remittance files one at a time; `image` builds it in a compact store and opens its serialized
    image again before every run (the serverless host's next invocation)."""

    def __init__(self, tmp_path, kind: str):
        self.tmp, self.kind, self.n = tmp_path, kind, 0
        self.store = Store(str(tmp_path / "s.sqlite")) if kind == "file" else Store(":memory:", compact=True)

    @property
    def conn(self) -> sqlite3.Connection:
        return self.store.conn

    def remit(self, claims: list[str], pay_date: str, total: str = "100.00") -> None:
        self.n += 1
        p = self.tmp / f"r{self.n:04d}.RMT"
        p.write_bytes(remit("".join(claims), total, payer="ANTHEM", seg_count=99, isa_ctrl=f"{self.n:09d}", pay_date=pay_date))
        self.store.ingest_835_paths([str(p)])

    def reopen(self) -> None:
        if self.kind == "image":
            data = self.store.to_bytes()
            self.store.close()
            self.store = Store.from_bytes(data)
            ensure_schema(self.store.conn)

    def run(self, run_id: str, today: str) -> None:
        self.conn.commit()
        self.reopen()
        Detector(self.store, run_id=run_id, today=today).run()

    def items(self) -> list[dict]:
        self.conn.row_factory = sqlite3.Row
        return [dict(r) for r in self.conn.execute("SELECT * FROM findings WHERE patient_control_number=? ORDER BY finding_id", (PCN,))]

    def answer(self) -> dict:
        got = [r for r in self.items() if r["signal"] != "NO_ANSWER" and not r.get("stale")]
        assert len(got) == 1, [(r["signal"], r["status"], r.get("stale")) for r in self.items()]
        return got[0]


@pytest.fixture(params=["file", "compact", "image"])
def kind(request):
    if request.param == "image" and not CAN_SERIALIZE:
        pytest.skip("this SQLite build cannot serialize a store image")
    return request.param


def _chased_then_answered(tmp_path, kind: str, chase_status: str = "appealed") -> World:
    """Anthem answers 25 visits on Nov 10, 2026 (its usual answer time). EMA billed the 99214 of Sep 14 on Sep 15 and no 835
    answers it: on Nov 20 it is chased (NO_ANSWER). A biller marks the chase Sent on Nov 21 ("sent again"). Anthem's answer
    arrives on Nov 25: CO-50, $0.00 paid. The run of Nov 30 lists the denial: the payer's answer, a new matter."""
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 20, "99214", "223.65", "117.60"), "20261110")
    w.remit(clean_visits(21, 5, "99214", "223.65", "117.60"), "20261110")
    ensure_schema(w.conn)
    ema(w.conn, PCN)
    w.run("r1", FIRST)
    chase = [r for r in w.items() if r["signal"] == "NO_ANSWER" and r["status"] == "open"]
    assert len(chase) == 1, [(r["signal"], r["status"]) for r in w.items()]
    assert actions.apply_status(w.conn, chase[0]["finding_id"], chase_status, SENT_NOTE, None, "Biller One (invented)",
                                "2026-11-21T15:00:00+00:00", "open")
    w.conn.commit()
    den = claim(0, svc("99214", "223.65", "0.00", None, "CAS*CO*50*223.65~", dos="20260914"), dos="20260914").replace("CB0000000C017", PCN)
    w.remit([den], "20261125", total="0.00")
    w.run("r2", ANSWER)
    a = w.answer()
    # the hand-off happened: the denial's item is new and carries the chase's notes (what the replay saw)
    assert a["signal"] == "DENIED_MEDICAL_NECESSITY" and SENT_NOTE in (a.get("notes") or ""), (a["signal"], a["status"])
    manual = w.conn.execute("SELECT COUNT(*) FROM finding_events WHERE finding_id=? AND kind='manual'", (a["finding_id"],)).fetchone()[0]
    assert manual == 0, "precondition: no person has acted on the denial's own item"
    return w


def test_an_answer_nobody_started_expires_past_the_payers_last_window(tmp_path, kind):
    w = _chased_then_answered(tmp_path, kind)
    w.run("r3", LATER)
    a = w.answer()
    rules = states.state_of(w.conn, dict(a, status="open"), today=LATER)
    # the control on the rules: the same facts with no status of anyone's are past every window by then
    assert (rules["work_state"], rules["rule"]) == ("expired", "expired"), (rules["work_state"], rules["rule"], rules["state_basis"])
    basis = a.get("state_basis") or ""
    assert a["state_rule"] != "person_started" and a["work_state"] == "expired", (
        f"nobody started the payer's answer (its only history is the run's own hand-off from the chase a person marked Sent), "
        f"yet the run stored it {a['status']} / {a['work_state']} / {a['state_rule']} ('{basis[:60]}') past every window of the "
        f"payer, where the rules say {rules['work_state']} ('{(rules['state_basis'] or '')[:80]}')")
    assert not re.match(r"Started by a person on\s*(\.|$)", basis), f"the basis names no person and no day: '{basis[:60]}'"
    w.store.close()


def test_control_a_person_who_starts_the_answer_keeps_it_work_now_past_the_window(tmp_path, kind):
    w = _chased_then_answered(tmp_path, kind)
    a = w.answer()
    if a["status"] == "in_progress":        # pressing Started on an item already Started: move it to To do and back, as a page does
        assert actions.apply_status(w.conn, a["finding_id"], "open", None, None, "Biller Two (invented)", "2026-12-01T15:00:00+00:00",
                                    "in_progress")
    assert actions.apply_status(w.conn, a["finding_id"], "in_progress", "Records requested (invented)", None, "Biller Two (invented)",
                                "2026-12-01T15:05:00+00:00", "open")
    w.conn.commit()
    w.run("r3", LATER)
    a = w.answer()
    assert (a["status"], a["work_state"], a["state_rule"]) == ("in_progress", "work_now", "person_started"), \
        (a["status"], a["work_state"], a["state_rule"], (a.get("state_basis") or "")[:80])
    assert (a.get("state_basis") or "").startswith("Started by Biller Two (invented) on Dec 1, 2026"), (a.get("state_basis") or "")[:80]
    w.store.close()


HANDED = "Its Started came with the claim's chase, which Biller One (invented) marked {} on Nov 21, 2026: the chase's notes are here"


@pytest.mark.parametrize("chase_status,word", [("appealed", "Sent"), ("in_progress", "Started")])
def test_the_answers_label_stays_started_and_its_basis_names_the_handoff(tmp_path, kind, chase_status, word):
    w = _chased_then_answered(tmp_path, kind, chase_status)
    a = w.answer()
    assert a["status"] == "in_progress"                         # the label: Started (test_workable's hand-off)
    rules = states.state_of(w.conn, dict(a, status="open"), today=ANSWER)
    assert (a["work_state"], a["state_rule"]) == (rules["work_state"], rules["rule"]) and a["state_rule"] != "person_started"
    assert (a.get("state_basis") or "").endswith(HANDED.format(word)), a.get("state_basis")
    st = states.state_of(w.conn, a, today=ANSWER)
    assert st["person"] is False and any(e.get("kind") == "handoff" and e["text"] == HANDED.format(word) for e in st["evidence"])
    w.store.close()


def test_the_self_check_looks_at_a_handed_on_started(tmp_path, kind):
    """past_window reads a Started no person set as To do: one left Work now past every window, as the old rule stored it
    (person_started), is caught; a person's own Started is not."""
    w = _chased_then_answered(tmp_path, kind)
    w.run("r3", LATER)
    a = w.answer()
    assert selfcheck.check_past_window(w.conn, LATER)["status"] == "ok"
    w.conn.execute("UPDATE findings SET work_state='work_now', state_rule='person_started', state_basis='Started by a person on ' "
                   "WHERE finding_id=?", (a["finding_id"],))
    w.conn.commit()
    r = selfcheck.check_past_window(w.conn, LATER)
    assert r["status"] == "fail" and a["finding_id"] in r["examples"], r
    # a person's own Started on the item (the newest status line a person set): theirs, never caught
    assert actions.apply_status(w.conn, a["finding_id"], "open", None, None, "Biller Two (invented)", "2029-06-01T10:00:00+00:00",
                                "in_progress")
    assert actions.apply_status(w.conn, a["finding_id"], "in_progress", None, None, "Biller Two (invented)", "2029-06-01T10:05:00+00:00",
                                "open")
    w.conn.execute("UPDATE findings SET work_state='work_now', state_rule='person_started' WHERE finding_id=?", (a["finding_id"],))
    w.conn.commit()
    assert a["finding_id"] not in selfcheck.check_past_window(w.conn, LATER)["examples"]
    w.store.close()


def test_a_person_started_is_the_newest_status_line_a_person_set():
    """states.person_started: a person's own Started on the item (their newest status line), the lines a whole hand-off
    carried to an heir included; never the run's own lines."""
    hand = {"kind": "signal_changed", "from": "appealed", "to": "in_progress", "actor": "system", "at": "2026-11-30T09:00:00"}
    mine = {"kind": "manual", "from": "open", "to": "in_progress", "actor": "Biller One (invented)", "at": "2026-11-21T15:00:00"}
    later = {"kind": "manual", "from": "in_progress", "to": "open", "actor": "Biller One (invented)", "at": "2026-11-22T15:00:00"}
    assert not states.person_started([])
    assert not states.person_started([hand])
    assert states.person_started([mine])
    assert states.person_started([mine, hand])          # carried with a whole hand-off, then the run's own line: still theirs
    assert not states.person_started([mine, later])      # they set it back to To do since


# --------------------------------------------------------------------------- review r1: a person's Started moves to an heir
def _reopened(s: Store, kind: str) -> Store:
    """The serverless host's next invocation: the store image serialized and opened again ('image'); the others go on."""
    if kind != "image":
        return s
    data = s.to_bytes()
    s.close()
    s = Store.from_bytes(data)
    ensure_schema(s.conn)
    return s


def test_a_persons_started_carried_to_the_lines_new_id_decides_its_state_in_the_run_that_carries_it(tmp_path, kind, monkeypatch):
    """A person marks a downcoded visit Started themselves, past every window of the payer (they take it up anyway). New
    claim files then prove the billed level, so the run gives the line's money a new id (DOWNCODE_VS_BILLED) and carries the
    person's Started and history to it (detect.carry_to_heirs). In the very run that carries it, the person's Started
    decides the heir's state, as it did on the old id: Work now, rule person_started, named and dated. It is never judged as
    To do for one run (expired) with a basis that calls it a hand-off from the claim's chase (review r1 of replay-fix-2a:
    the states were decided while the person's lines still sat on the old id)."""
    from tests.test_hotfix2d import _claim_files_arrive, _downcode_store, _first_downcode
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    s = _reopened(_downcode_store(tmp_path, kind), kind)
    c = s.conn
    day = "2028-06-01"                  # past every window of the April 15, 2026 remittance (the heir's too)
    old = _first_downcode(c)
    assert actions.apply_status(c, old, "in_progress", "Taking it up with the payer (invented)", None, "Biller One (invented)",
                                "2028-05-20T15:00:00+00:00")
    c.commit()
    s = _reopened(s, kind)
    Detector(s, run_id="r1b", today=day).run()
    c = s.conn
    c.row_factory = sqlite3.Row
    got = dict(c.execute("SELECT status, work_state, state_rule FROM findings WHERE finding_id=?", (old,)).fetchone())
    assert (got["status"], got["work_state"], got["state_rule"]) == ("in_progress", "work_now", "person_started"), got
    _claim_files_arrive(s)
    s = _reopened(s, kind)
    Detector(s, run_id="r2", today=day).run()
    c = s.conn
    c.row_factory = sqlite3.Row
    heirs = [dict(r) for r in c.execute("SELECT finding_id, status, work_state, state_rule, state_basis FROM findings "
                                        "WHERE signal='DOWNCODE_VS_BILLED' AND status='in_progress'")]
    assert len(heirs) == 1, heirs
    h = heirs[0]
    assert h["finding_id"] != old
    assert c.execute("SELECT COUNT(*) FROM finding_events WHERE finding_id=? AND kind='manual' AND to_status='in_progress'",
                     (h["finding_id"],)).fetchone()[0] == 1, "the person's Started moved to the heir"
    assert (h["work_state"], h["state_rule"]) == ("work_now", "person_started"), (
        f"the person's own Started, carried to the line's new id, was judged by the automatic rules in the run that carried "
        f"it: {h['work_state']} / {h['state_rule']}: {h['state_basis']}")
    assert (h["state_basis"] or "").startswith("Started by Biller One (invented) on May 20, 2028"), h["state_basis"]
    assert "chase" not in (h["state_basis"] or ""), h["state_basis"]
    # the decision log holds no automatic state change for the heir in that run, and none the run after
    Detector(s, run_id="r3", today=day).run()
    rows = c.execute("SELECT run_id, kind, rule FROM item_decisions WHERE finding_id=? AND kind='state'", (h["finding_id"],)).fetchall() \
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='item_decisions'").fetchone() else []
    assert not [tuple(r) for r in rows], [tuple(r) for r in rows]
    s.close()


@pytest.mark.parametrize("store_kind", ["file", "compact"])
def test_a_persons_started_on_a_claim_listed_under_its_ema_claim_number_stays_theirs_when_the_837s_name_it(tmp_path, store_kind,
                                                                                                           monkeypatch):
    """The other heir path (gapfix G8's same claim under two identities): a person marks the chase of EMA's claim 74
    Started themselves; the 837s then name its claim id and the 837 pass lists it under that id, taking the old item whole
    (status, notes, history). In the run that carries it, the person's own Started decides the item: Work now by their
    status, named and dated, never To do with a hand-off's words."""
    from tests import ema_fixtures as F
    from tests.test_gap_one_claim import _listed, _run, _send, _visit, _work, _world
    s, lg = _world(tmp_path, store_kind, monkeypatch, _visit())
    try:
        _run(s, "m1")
        first = _listed(s)
        assert [(r["signal"], r["patient_control_number"], r["ema_link"]) for r in first] == [("NO_ANSWER", None, F.link_no(74))], first
        old = first[0]
        _work(s, old["finding_id"], status="in_progress", note="Called Blue Cross about the claim (invented)")
        _send(s, tmp_path, "20260309")
        _run(s, "m2")
        now = _listed(s)
        assert len(now) == 1 and now[0]["finding_id"] != old["finding_id"], [(r["finding_id"], r["status"]) for r in now]
        it = now[0]
        assert it["status"] == "in_progress" and s.conn.execute(
            "SELECT COUNT(*) FROM finding_events WHERE finding_id=? AND kind='manual' AND to_status='in_progress'",
            (it["finding_id"],)).fetchone()[0] == 1, "the person's Started went with the claim to its new id"
        assert (it["work_state"], it["state_rule"]) == ("work_now", "person_started"), (
            f"the person's own Started, carried to the claim's new id, was judged by the automatic rules in the run that "
            f"carried it: {it['work_state']} / {it['state_rule']}: {it['state_basis']}")
        assert (it["state_basis"] or "").startswith("Started by biller on Apr 25, 2026"), it["state_basis"]
    finally:
        lg.close()
        s.close()


def test_a_faxed_appeals_heir_is_judged_on_its_newest_fax_in_the_run_that_carries_it(tmp_path, kind, monkeypatch):
    """A person faxed the appeal of a downcoded visit (Sent, Oct 1) and faxed it again on Nov 10 (a fax line only). The
    run of Nov 20 waits on the payer until Dec 25. New claim files then give the line's money a new id: in the run that
    carries it, the heir is judged on its faxes as the old id was (Waiting, follow-up Dec 25), never on the first send
    alone (follow-up due since Nov 15) because its faxes still sat on the old id."""
    from tests.test_hotfix2d import _claim_files_arrive, _downcode_store, _fax_record, _fax_the_appeal, _first_downcode, _heir
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    s = _reopened(_downcode_store(tmp_path, kind), kind)
    c = s.conn
    old = _first_downcode(c)
    _fax_the_appeal(c, old)
    actions.apply_fax(c, _fax_record(old, key="out:fake:2", at="2026-11-10T15:00:00+00:00"))
    c.commit()
    s = _reopened(s, kind)
    Detector(s, run_id="r1b", today="2026-11-20").run()
    c = s.conn
    c.row_factory = sqlite3.Row
    got = dict(c.execute("SELECT work_state, state_rule, follow_up_on FROM findings WHERE finding_id=?", (old,)).fetchone())
    assert (got["work_state"], got["follow_up_on"]) == ("waiting", "2026-12-25"), got
    _claim_files_arrive(s)
    s = _reopened(s, kind)
    Detector(s, run_id="r2", today="2026-11-20").run()
    c = s.conn
    c.row_factory = sqlite3.Row
    h = _heir(c)
    got = dict(c.execute("SELECT work_state, state_rule, follow_up_on, state_basis FROM findings WHERE finding_id=?", (h,)).fetchone())
    assert (got["work_state"], got["follow_up_on"]) == ("waiting", "2026-12-25"), (
        f"the heir was judged without its newest fax in the run that carried it: {got}")
    assert c.execute("SELECT COUNT(*) FROM faxes WHERE finding_id=?", (h,)).fetchone()[0] == 2
    s.close()
