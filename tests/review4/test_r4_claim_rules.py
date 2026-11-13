"""run_claim_rules: claim-level payments in bill_primary, undated lines, the fold. Invented data only."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from r4h import NPI, WHO, add, base, brief, by_key, clm, rows, run, sv, visit_paid  # noqa: E402


def test_a_claim_level_payment_the_payer_reversed_does_not_hide_a_claim_level_denial(tmp_path):
    """A payer that sends claims without service lines: C017 denied (CLP02 = 4, claim-level CO-16) on 2026-04-15; the
    corrected C024 paid $117.60 at the claim level on 2026-05-20; on 2026-06-10 the payer reverses C024 at the claim level
    (CLP02 = 22, -117.60, no SVC loop) and denies it (CLP02 = 4, CO-16). Nothing stands paid on the bill: C017's denial (or
    C024's) must be listed."""
    base(tmp_path).close()
    add(tmp_path, [clm("CB0000300C017", "4", "223.65", "0.00", claim_cas="CAS*CO*16*223.65~")], "20260415").close()
    add(tmp_path, [clm("CB0000300C024", "1", "223.65", "117.60", claim_cas="CAS*CO*45*106.05~")], "20260520").close()
    store = add(tmp_path, [clm("CB0000300C024", "22", "-223.65", "-117.60", claim_cas="CAS*CO*45*-106.05~"),
                           clm("CB0000300C024", "4", "223.65", "0.00", claim_cas="CAS*CO*16*223.65~")], "20260610")
    run(store, "t", "2026-06-11T09:00:00+00:00")
    rs = rows(store, "category='denial'")
    brief(rs)
    assert [r for r in rs if r["status"] == "open"], "the bill's only payment was reversed, yet no denial of it is listed"
    store.close()


def test_control_the_claim_level_payment_standing_hides_the_denial(tmp_path):
    """Without the reversal (tests/review3/test_r3_wo_claimlevel.py): the payment stands and nothing is listed."""
    base(tmp_path).close()
    add(tmp_path, [clm("CB0000300C017", "4", "223.65", "0.00", claim_cas="CAS*CO*16*223.65~")], "20260415").close()
    store = add(tmp_path, [clm("CB0000300C024", "1", "223.65", "117.60", claim_cas="CAS*CO*45*106.05~")], "20260520")
    run(store, "t", "2026-06-11T09:00:00+00:00")
    rs = rows(store, "category='denial'")
    brief(rs)
    assert not [r for r in rs if r["status"] == "open"]
    store.close()


def span_claim(pcn, status, paid, units, cas):
    """A claim dated 2026-04-01 to 2026-04-03 whose 17003 line carries no date of its own (no DTM*472)."""
    charge = f"{25 * units:.2f}"
    allowed = f"AMT*B6*{paid}~" if float(paid) > 0 else ""
    return (f"CLP*{pcn}*{status}*{charge}*{paid}*0.00*12*PC300*11~NM1*QC*1*{WHO}~NM1*82*2*TEST PROVIDER*****XX*{NPI}~"
            f"DTM*232*20260401~DTM*233*20260403~SVC*HC:17003*{charge}*{paid}**{units}~{cas}{allowed}")


def test_an_undated_line_of_five_units_is_not_covered_by_a_payment_of_three(tmp_path):
    """C017 (17003 x 5, line undated, claim 04-01 to 04-03) is denied whole. The corrected C024 bills 3 units and the payer
    pays them. Two units were never paid: with dated lines this is listed (tests/review3/test_r3_units.py)."""
    base(tmp_path, "17003", "25.00", "6.00").close()
    add(tmp_path, [span_claim("CB0000300C017", "4", "0.00", 5, "CAS*CO*16*125.00~")], "20260415").close()
    store = add(tmp_path, [span_claim("CB0000300C024", "1", "18.00", 3, "CAS*CO*45*57.00~")], "20260520")
    run(store, "t", "2026-05-21T09:00:00+00:00")
    rs = rows(store, "category='denial'")
    brief(rs)
    assert [r for r in rs if r["status"] == "open"], "3 units paid count as all 5: the 2 unpaid units are not listed"
    store.close()


def test_a_claim_level_reversal_alone_reopens_the_claim_level_denial(tmp_path):
    """The same without a new denial in the reversal's remittance: the payer takes C024's claim-level payment back
    (CLP02 = 22, no SVC loop) because another plan is primary, and sends nothing else for the bill. Nothing stands paid,
    so C017's claim-level denial must be listed again."""
    base(tmp_path).close()
    add(tmp_path, [clm("CB0000300C017", "4", "223.65", "0.00", claim_cas="CAS*CO*16*223.65~")], "20260415").close()
    add(tmp_path, [clm("CB0000300C024", "1", "223.65", "117.60", claim_cas="CAS*CO*45*106.05~")], "20260520").close()
    store = add(tmp_path, [clm("CB0000300C024", "22", "-223.65", "-117.60", claim_cas="CAS*CO*45*-106.05~"),
                           visit_paid("CB0000800C017", who="ASH*RO****MI*W55500304", payer_ctl="PC800")], "20260610")
    run(store, "t", "2026-06-11T09:00:00+00:00")
    rs = rows(store, "category='denial'")
    brief(rs)
    assert [r for r in rs if r["status"] == "open" and r["patient_control_number"].startswith("CB0000300")], \
        "C024's claim-level payment was reversed, but it still counts as paying the bill: C017's denial is hidden"
    store.close()
