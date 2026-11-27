"""The failing tests of fx-people's needs from other fixers (plans/progress/fx-people.md, "Needs from others"). Invented
data only. Not collected by the suite (this file's name is not test_*.py): each fails on wip/fx-people until the fixer
named in its docstring makes the change; then move it into that fixer's tests. Run from the repository root:
    python3 -m pytest -q plans/progress/fx-people_needs_tests.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from remitwatch import codes  # noqa: E402


def test_a_humana_plan_filed_as_medicaid_is_one_group_whatever_its_835_name():
    """F8 need (codes.py, the payer-name rules): Humana's Medicaid plan reaches the practice under two 835 names. With the
    claim filing indicator MC (Medicaid) the plain company name is grouped "Humana Medicaid", but the name the rules read as
    Humana's Medicare Advantage brand ("HUMANA CARE PLAN", PAYER_RULES) becomes "Humana Medicare Advantage Medicaid": one
    plan split into two payer groups (its piles, patterns and rates), and a step that tells the biller the member "is on a
    Medicaid plan (Humana Medicare Advantage Medicaid)". On the world copy of 11-24, 20 of the 22 open missing-referral
    denials of its members (PR-288) carry the second name and 2 the first. The kind is right (medicaid) and so are
    the windows; the group name is not."""
    assert codes.payer_group_kind("HUMANA", "MC") == ("Humana Medicaid", "medicaid")
    assert codes.payer_group_kind("HUMANA CARE PLAN", "MC") == ("Humana Medicaid", "medicaid")


def test_a_second_payers_takeback_title_says_bill_the_patient_only_when_the_first_payer_left_it_on_the_patient(tmp_path):
    """F1 need (detect.py `_emit_taken_back`, the TAKEBACK item's title): a second payer's payment taken back in full is
    titled "<code>: <payer> paid its share as second payer, then took it back on <day>: bill the patient, or dispute the
    takeback" for every claim status 2 or 3. F5's state (states._decide, the FEES branch routed to F5) bills the patient
    only as far as the first payer's standing answer left the amount on the patient: where the first payer denied the
    service as the practice's (CO-50, nothing on the patient) or its answer is not on file, the basis says find out why,
    then dispute or bill the right payer, but the title still says bill the patient. On the world copy of 11-24, 2 of
    the 11 second-payer takebacks ($88.14): one whose first payer's standing answer is a CO-150 denial, one whose first
    payer's answer is not on file. The title should follow the same rule (states.first_payer_answer gives the first
    payer's answer)."""
    from tests.review3.r3helpers import clm, sv, visit_paid
    from tests.test_features import clean_visits
    from tests.test_fx_ledger import GAP, KY, OTHER, World
    w = World(tmp_path, "file")
    pcn = "CB0000302C017"
    w.remit(clean_visits(1, 25, "99214", "223.65", "80.00"), "20260301", **KY)
    w.remit([clm(pcn, "1", "223.65", "0.00", sv("99214", "223.65", "0.00", 1, "CAS*CO*50*223.65~"), payer_ctl="MC1")], "20260415", **KY)
    w.remit([clm(pcn, "2", "223.65", "16.00", sv("99214", "223.65", "16.00", 1, "CAS*OA*23*207.65~"), payer_ctl="GP1")], "20260425", **GAP)
    w.remit([visit_paid("CB0000900C017", who=OTHER, payer_ctl="GP9", paid="16.00")], "20260803",
            plb=f"PLB*1777777772*20261231*WO:5550001 {pcn}*16.00~", **GAP)
    w.run("r1", today="2026-09-20")
    (r,) = w.rows("signal='TAKEBACK' AND COALESCE(stale, 0) = 0")
    assert "bill the patient" not in (r["state_basis"] or ""), r["state_basis"]       # F5's part: passes
    assert "bill the patient" not in r["title"], r["title"]                            # F1's part: fails until the title follows
    w.store.close()
