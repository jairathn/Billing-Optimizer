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
