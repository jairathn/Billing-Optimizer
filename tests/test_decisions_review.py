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


# =============================================================================================================
# r1-2: an owner's decision made under the key with no period (before decision 4) still holds for each period
# =============================================================================================================
from tests.test_payer_code_rules import dt_days, ema_env, eledger, estore  # noqa: E402,F401  (fixtures)

S25 = "2025-10-02"
WHOLE = "UnitedHealthcare|G6001||payer_policy"
TO25 = "UnitedHealthcare|G6001|to 2025-12-31|payer_policy"


def _g6001_world(estore, eledger, tmp_path):
    """22 denials of G6001 as primary in 2025 (CO-197), and 6 payments of it as the second payer: a pair the research
    splits by period (to 2025-12-31). Invented members and claims."""
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import denial_world, remit
    for n in range(22):
        remit(estore, trace=f"7776{n:05d}", paid_on="2025-11-10", payer="UNITEDHEALTHCARE TEST",
              claims=[{"pcn": F.claim_id(800 + n), "last": f"RAD{n}", "first": "JULIET", "member": f"J{n:011d}",
                       "lines": [(S25, "G6001", 260, 0, [("CO", "197", 260)])]}])
    for n in range(6):
        remit(estore, trace=f"7777{n:05d}", paid_on="2025-11-10", payer="UNITEDHEALTHCARE TEST", status="2",
              claims=[{"pcn": F.claim_id(850 + n), "last": f"SEC{n}", "first": "KILO", "member": f"K{n:011d}",
                       "lines": [(S25, "G6001", 260, 30, [("PR", "2", 0)])]}])
    denial_world(estore, eledger, tmp_path)


def test_r1_2_an_owners_accept_under_the_key_with_no_period_still_holds(estore, eledger, tmp_path):
    """The owner accepted UnitedHealthcare G6001 on the policy page before decision 4 named periods (key
    'UnitedHealthcare|G6001||payer_policy'). After the release the run keys the 2025 services 'to 2025-12-31': the
    owner's accept still holds there (its 22 denials stay off Work now), and the policy page shows that row accepted,
    by whom and when, made for the code at every date."""
    from remitwatch import api_ema, ema_checks as ec
    from tests.test_ema_wiring import items, run
    _g6001_world(estore, eledger, tmp_path)
    ec.set_policy(estore.conn, WHOLE, "accept", "Owner One")
    estore.conn.commit()
    run(estore)
    left = items(estore, "payer_group='UnitedHealthcare' AND procedure_code='G6001' AND status='open'")
    assert left == [], f"{len(left)} G6001 denials back on the list although the owner accepted the pattern"
    rows = {p["pattern"]: p for p in api_ema.policy_table(estore.conn)["patterns"]}
    assert TO25 in rows, sorted(rows)
    row = rows[TO25]
    assert (row["decision"], row["decided_by"], row["decided_on"], row["pending"]) == ("accept", "Owner One", WHOLE, False), row


def test_r1_2_a_later_decision_on_the_period_row_wins_and_a_later_one_on_the_whole_row_wins_back():
    """The newer decision of the two rows holds: a period decided later is decided there; the row for every date changed
    later covers every period again. A key with no period never reads another row."""
    from remitwatch import rarely_paid as rp
    pol = {WHOLE: {"decision": "accept", "by": "Owner One", "at": "2026-10-01T10:00:00+00:00"}}
    assert rp.decision_for(pol, TO25)["decision"] == "accept"
    pol[TO25] = {"decision": "pursue", "by": "Owner One", "at": "2026-12-02T10:00:00+00:00"}
    assert (rp.decision_for(pol, TO25)["decision"], rp.decision_for(pol, TO25)["from_key"]) == ("pursue", TO25)
    pol[WHOLE] = {"decision": "accept", "by": "Owner One", "at": "2026-12-05T10:00:00+00:00"}
    assert (rp.decision_for(pol, TO25)["decision"], rp.decision_for(pol, TO25)["from_key"]) == ("accept", WHOLE)
    assert rp.decision_for(pol, "Aetna|G2211||payer_policy") is None
    assert rp.decision_for({}, TO25) is None and rp.decision_for(None, TO25) is None
    assert rp.whole_key("Anthem / BCBS|77600|from 2026-01-01|payer_policy") == "Anthem / BCBS|77600||payer_policy"
    assert rp.whole_key("Anthem / BCBS|99213|25|CO-45") is None
