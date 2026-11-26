"""
The decisions unit's review round of 2026-11-26: the reviewers' failing tests (rounds r1 and r2), ported into the
repository with the fixes they hold. Invented data only: made-up claim ids, payers, members, amounts and dates. Each case
runs on both kinds of store where the code runs on both (the server's file store and the compact in-memory store of the
serverless host); the API cases run on the server, and on the serverless host where the reviewer found the same.
"""
from __future__ import annotations

import json

import pytest

from tests.test_decisions import (TODAY2, _crossed_over, _ema_patient, _patient_denial, _production_covers, _state,  # noqa: F401
                                  sstore)


# =============================================================================================================
# r1-1: a patient-group denial RemitWatch cannot see the patient's side of is a can't-check, never billed blind
# =============================================================================================================
def _old_visit(s, *, svc="2024-03-02", paid_on="2024-03-28"):
    """A 2024 visit the payer put on the patient (PR-204, the whole $150.00)."""
    from tests import ema_fixtures as F
    from tests.test_states import item, ln, remit
    tk = remit(s, trace=F.check_no(1), paid_on=paid_on,
               claims=[{"pcn": F.claim_id(1), "received": "2024-03-05", "lines": [ln("99213", 150, 0, [("PR", "204", 150)], svc=svc)]}])
    item(s, signal="DENIED_PLAN_EXCLUSION", carc="204", line_key=f"{tk}-0-0", claim_key=f"{tk}-0", transaction_key=tk,
         service_date=svc, payment_date=paid_on, year=svc[:4], expected_cents=15000, shortfall_cents=15000,
         appeal_deadline="2024-09-24", evidence={"carc_group": "PR"})
    return tk


def test_r1_1_a_patient_denial_ema_cannot_see_is_cant_check_not_billed_blind(sstore):
    """EMA's Production report on file covers 2026 only, and the visit is of 2024 with no EMA line: RemitWatch cannot see
    whether the patient paid, was billed or had the share written off. The item is a can't-check with its $150.00 (the
    owner's rule 3), past every payer window or not (the payer's windows never close a patient's share; an age limit is
    the owner's call), and its next step checks the patient's account in EMA first: it never says "Bill the patient"."""
    from remitwatch import story
    from tests.test_states import state
    _production_covers(sstore, "2026-01-05", "2026-11-20")
    _old_visit(sstore)
    st = state(sstore, today="2026-11-24")
    assert (st["work_state"], st["rule"]) == ("cant_check", "cant_check_patient"), (st["work_state"], st["rule"], st["state_basis"])
    assert "EMA's Production reports do not cover this date of service" in st["state_basis"] and "$150.00" in st["state_basis"]
    n = story.next_step(sstore.conn, "F1", today="2026-11-24")
    assert not n["action"].startswith("Bill the patient"), n["action"]
    assert n["action"].startswith("Check the patient's account in EMA before billing $150.00"), n["action"]
    assert n["expected_cents"] == 15000 and "cannot see" in n["expected_basis"], (n["expected_cents"], n["expected_basis"])
    assert {"billed_patient", "mark_done"} <= set(n["buttons"]) and "build_packet" not in n["buttons"], n["buttons"]
    assert n["appeal_ok"] is False


def test_r1_1_with_no_production_report_on_file_it_says_so(sstore):
    """No EMA Production report on file at all (the serverless site reads the A/R only): the same can't-check, saying so."""
    from tests.test_states import state
    _old_visit(sstore, svc="2026-03-02", paid_on="2026-03-28")
    st = state(sstore, today=TODAY2)
    assert (st["work_state"], st["rule"]) == ("cant_check", "cant_check_patient"), st["state_basis"]
    assert "no EMA Production report is on file" in st["state_basis"], st["state_basis"]


def test_r1_1_an_ema_line_for_the_service_shows_the_patients_side_whatever_the_coverage(sstore):
    """EMA shows the visit's line (its patient's money is there to read) though no box covers the date: billed as decided,
    the rest after what EMA shows the patient paid."""
    from remitwatch import story
    from tests import ema_fixtures as F
    from tests.test_states import state
    _production_covers(sstore, "2026-01-05", "2026-11-20")
    _old_visit(sstore)
    bill = F.claim_id(1)[:9]                 # EMA's line for the 2024 visit, $150.00 open
    sstore.conn.execute("""INSERT INTO ema_lines (link, svc, cpt, bill, payer_group, charges, balance, pat_paid_joined, noncontractual,
                                                  pat_adj_joined) VALUES (7001, '2024-03-02', '99213', ?, 'Anthem / BCBS', 15000, 15000, 0, 0, 0)""",
                        (bill,))
    sstore.conn.execute("INSERT INTO ema_link_map (link, bill, claim_id, method, conflict, facility) VALUES (7001, ?, ?, 'test', 0, 0)",
                        (bill, F.claim_id(1)))
    sstore.conn.commit()
    st = state(sstore, today="2026-11-24")
    assert (st["work_state"], st["rule"]) == ("work_now", "bill_patient_denial"), st["state_basis"]
    assert story.next_step(sstore.conn, "F1", today="2026-11-24")["action"].startswith("Bill the patient $150.00:")


def test_r1_1_billed_the_patient_on_a_visit_ema_cannot_show_is_never_followed_by_a_bill_on_a_guess(sstore):
    """A person checked EMA by hand and pressed Billed the patient on the visit RemitWatch cannot see: it waits 60 days,
    then is work again, but never says "nothing paid by the patient" nor sends another statement on a guess: the
    patient's account in EMA is checked first."""
    from remitwatch import actions, story
    from tests.test_states import state
    _production_covers(sstore, "2026-01-05", "2026-11-20")
    _old_visit(sstore)
    actions.apply_status(sstore.conn, "F1", "appealed", "Billed the patient", actor="Biller One", at="2026-11-24T15:00:00+00:00")
    assert (state(sstore, today="2026-11-24")["rule"]) == "billed_patient"
    late = state(sstore, today="2027-01-24")
    assert (late["work_state"], late["rule"]) == ("work_now", "patient_unpaid"), late["state_basis"]
    assert "nothing paid by the patient" not in late["state_basis"] and "cannot be seen" in late["state_basis"], late["state_basis"]
    n = story.next_step(sstore.conn, "F1", today="2027-01-24")
    assert n["action"].startswith("Check the patient's account in EMA, then send the patient another statement for $150.00"), n["action"]


def test_r1_1_a_covered_visit_is_billed_as_decided(sstore):
    """The visit's date is covered by EMA's Production report on file: billed as decided (the unit's own case)."""
    _patient_denial(sstore, "204", signal="DENIED_PLAN_EXCLUSION")
    st = _state(sstore)
    assert (st["work_state"], st["rule"]) == ("work_now", "bill_patient_denial"), st["state_basis"]
