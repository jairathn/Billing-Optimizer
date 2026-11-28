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
