"""run_claim_rules' rebilled note: 'Billed again on claim X without <codes>; check whether it was dropped on purpose' is
put on any listed whole-claim denial whose bill has a claim processed after it, whatever that claim carried. Invented
data only. Run from the repository root."""
from __future__ import annotations

import sys

import os  # noqa: E402
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from tests.review4.r4h import WHO, NPI, add, base, brief, rows, run  # noqa: E402


def one(pcn, status, units, paid, cas, ctl):
    charge = f"{25 * units:.2f}"
    amt = f"AMT*B6*{paid}~" if float(paid) > 0 else ""
    return (f"CLP*{pcn}*{status}*{charge}*{paid}*0.00*12*{ctl}*11~NM1*QC*1*{WHO}~NM1*82*2*TEST PROVIDER*****XX*{NPI}~DTM*232*20260401~"
            f"SVC*HC:17003*{charge}*{paid}**{units}~DTM*472*20260401~{cas}{amt}")


def test_a_corrected_claim_with_fewer_units_is_called_a_claim_without_the_code(tmp_path):
    """C017: 17003 x6 denied whole (CO-16, 04-15). The corrected C024 bills 17003 x5 and is paid (05-20). One unit is
    unpaid and listed; the note says C024 was billed 'without 17003', and the 'Partly paid' note promised for a corrected
    claim with fewer units is not there."""
    base(tmp_path, "17003", "25.00", "6.00").close()
    add(tmp_path, [one("CB0000310C017", "4", 6, "0.00", "CAS*CO*16*150.00~", "A310A")], "20260415").close()
    store = add(tmp_path, [one("CB0000310C024", "1", 5, "30.00", "CAS*CO*45*95.00~", "A310B")], "20260520")
    run(store, "t", "2026-05-21T09:00:00+00:00")
    rs = [r for r in rows(store, "category='denial'") if r["status"] == "open"]
    brief(rs, cols=("patient_control_number", "signal", "status", "shortfall_cents", "hint"))
    print([r["evidence"].get("units_unpaid") for r in rs])
    assert rs, "precondition: the unpaid unit is listed"
    assert not any("without 17003" in (r["hint"] or "") for r in rs), f"the note says C024 left 17003 off: {rs[0]['hint']!r}"
    store.close()
