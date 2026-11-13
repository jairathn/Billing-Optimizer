"""The paid-event tuple's new eighth element (service date, None for an undated line). run_claim_rules' undated branch
collects every standing primary payment of the code on the bill, dated or not, into `pays`, and a listed denial since
paid is closed by `min(pays)`. Two payments that agree on the first seven elements (same claim, remittance, amount,
units, status, payer) and differ only in date-or-None make min() compare None with a string. Invented data only.
Run from the repository root."""
from __future__ import annotations

import sys

import os  # noqa: E402
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from tests.review4.r4h import NPI, WHO, add, base, brief, rows, run  # noqa: E402


def span(pcn, status, lines, charge, paid, payer_ctl):
    """A claim dated 2026-04-01 to 2026-04-03 (DTM*232/233)."""
    return (f"CLP*{pcn}*{status}*{charge}*{paid}*0.00*12*{payer_ctl}*11~NM1*QC*1*{WHO}~NM1*82*2*TEST PROVIDER*****XX*{NPI}~"
            f"DTM*232*20260401~DTM*233*20260403~{lines}")


def test_a_listed_undated_denial_paid_on_two_equal_lines_one_dated(tmp_path):
    """C017 (span 04-01 to 04-03): 17003 x2 on one line with no date of its own, denied whole (CO-16) on 04-15 and listed by
    the run of 04-16. The corrected C024 (05-20) bills the two lesions as two lines of 17003 at $6.00, one dated 04-02 and
    one without a date, both paid. The run of 05-21 must close the listed denial, not crash."""
    base(tmp_path, "17003", "25.00", "6.00").close()
    store = add(tmp_path, [span("CB0000990C017", "4", "SVC*HC:17003*50.00*0.00**2~CAS*CO*16*50.00~", "50.00", "0.00", "ANT990A")], "20260415")
    run(store, "r1", "2026-04-16T09:00:00+00:00")
    brief(rows(store, "signal='CLAIM_DENIED_UNPAID'"))
    assert [r["status"] for r in rows(store, "signal='CLAIM_DENIED_UNPAID'")] == ["open"], "precondition: listed"
    store.close()
    paid = ("SVC*HC:17003*25.00*6.00**1~DTM*472*20260402~CAS*CO*45*19.00~AMT*B6*6.00~"
            "SVC*HC:17003*25.00*6.00**1~CAS*CO*45*19.00~AMT*B6*6.00~")
    store = add(tmp_path, [span("CB0000990C024", "1", paid, "50.00", "12.00", "ANT990B")], "20260520")
    run(store, "r2", "2026-05-21T09:00:00+00:00")         # TypeError here: '<' between NoneType and str
    rs = rows(store, "signal='CLAIM_DENIED_UNPAID'")
    brief(rs)
    assert [r["status"] for r in rs] == ["resolved"]
    store.close()
