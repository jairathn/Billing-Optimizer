"""Dates: a payment in the same remittance as the denial, and lines a payer leaves undated on a claim that spans days.
Invented data only."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from r3helpers import NPI, WHO, add, base, by_key, show, visit_denied_line, visit_paid  # noqa: E402

from remitwatch.detect import Detector  # noqa: E402


def test_a_corrected_claim_paid_in_the_same_remittance_as_the_denial(tmp_path):
    """The biller sent the corrected claim C024 before C017 was processed. Anthem's 2026-04-15 remittance denies C017's
    visit (missing information) and pays C024 for the same visit. The visit is paid: the denial should close, or at least
    say where it was paid."""
    base(tmp_path).close()
    store = add(tmp_path, [visit_denied_line("CB0000300C017"), visit_paid("CB0000300C024")], "20260415")
    Detector(store, run_id="t").run()
    r = by_key(store)["CB0000300C017:99214:DENIED_MISSING_INFO"]
    show([r])
    assert r["status"] != "open" or r["hint"], "open with no word of the payment on C024 in the same remittance"
    store.close()


def span_claim(pcn, status, paid, cas):
    """A claim dated 2026-04-01 to 2026-04-03 whose lines carry no date of their own (no DTM*472)."""
    allowed = f"AMT*B6*{paid}~" if float(paid) > 0 else ""
    return (f"CLP*{pcn}*{status}*223.65*{paid}*0.00*12*PC300*11~NM1*QC*1*{WHO}~NM1*82*2*TEST PROVIDER*****XX*{NPI}~"
            f"DTM*232*20260401~DTM*233*20260403~SVC*HC:99214*223.65*{paid}**1~{cas}{allowed}")


def test_a_whole_claim_denial_on_undated_lines_paid_later_on_the_bill_is_not_listed(tmp_path):
    """C017 (lines undated, claim 04-01 to 04-03) is denied whole; the corrected C024 with the same lines is paid. 418bad3
    left it out (a later payment on the bill); now an undated line never matches a payment, so it is listed as never paid."""
    base(tmp_path).close()
    add(tmp_path, [span_claim("CB0000300C017", "4", "0.00", "CAS*CO*16*223.65~")], "20260415").close()
    store = add(tmp_path, [span_claim("CB0000300C024", "1", "117.60", "CAS*CO*45*106.05~")], "20260520")
    Detector(store, run_id="t").run()
    rows = by_key(store)
    show(rows.values())
    assert "CB0000300C017::CLAIM_DENIED_UNPAID" not in rows, "paid on C024, listed as denied and never paid"
    store.close()
