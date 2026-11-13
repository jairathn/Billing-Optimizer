"""run_claim_rules' settled(): bill_lines holds every processed line of the bill (status 1, 19, 2, 3) whatever came
after it, so a processing to the patient that its payer has since reversed still 'settles' another payer's denial of
the service ('a primary payer's processing counts at any date'). Invented data only. Run from the repository root."""
from __future__ import annotations

import sys

import os  # noqa: E402
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from tests.review4.lvn import add, base, both, clm, money, show, sv  # noqa: E402


def test_a_reversed_deductible_processing_hides_the_new_payers_denial(tmp_path):
    """January: plan A (primary at the time) processes C017's visit to the deductible (status 1, paid $0, PR-1 $117.60).
    March: plan A's coverage is ended retroactively; it reverses the claim (status 22, PR-1 -117.60). April: the practice
    bills the patient's new plan B (C024), which denies the claim whole (CO-16, missing information). Nobody has paid or
    applied anything to the visit: plan B's denial is the money to recover. Live lists it; the release takes plan A's
    reversed January processing as 'processed to the patient' and lists nothing."""
    base(tmp_path)
    add(tmp_path, [clm("CB0000966C017", "1", "223.65", "0.00", sv("99214", "223.65", "0.00", cas="CAS*PR*1*117.60~CAS*CO*45*106.05~", allowed="117.60"),
                       payer_ctl="PLA966", pr="117.60")], "20260120", payer="PLAN A HEALTH", payer_id="PLANA")
    add(tmp_path, [clm("CB0000966C017", "22", "-223.65", "0.00", sv("99214", "-223.65", "0.00", units=-1, cas="CAS*PR*1*-117.60~CAS*CO*45*-106.05~"),
                       payer_ctl="PLA966", pr="-117.60")], "20260301", payer="PLAN A HEALTH", payer_id="PLANA")
    add(tmp_path, [clm("CB0000966C024", "4", "223.65", "0.00", sv("99214", "223.65", "0.00", cas="CAS*CO*16*223.65~"), payer_ctl="ANT966")],
        "20260415")
    live, rel = both(tmp_path)
    show("live", live)
    show("release", rel)
    mine = lambda r: (r.get("patient_control_number") or "").startswith("CB0000966")       # noqa: E731
    print("live owed:", money(live, mine), " release owed:", money(rel, mine))
    assert money(live, mine)[0] == 1, "precondition: live lists plan B's denial"
    assert money(rel, mine) == money(live, mine), "plan B's denial is hidden by plan A's reversed processing"
