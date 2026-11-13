"""Lines a payer sends without their own date (no DTM*472) on a claim whose statement period covers several days
(DTM*232 to DTM*233). The live release (3f8855b) matched such lines to each other on the bill and code; the release
leaves them undated and never matches them to a payment. Invented data only."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lvn import PROV, WHO, add, base, both, money, show  # noqa: E402


def undated(pcn, status, code, charge, paid, cas, allowed=None, payer_ctl="PCU"):
    line = f"SVC*HC:{code}*{charge}*{paid}**1~{cas}" + (f"AMT*B6*{allowed}~" if allowed else "")
    return (f"CLP*{pcn}*{status}*{charge}*{paid}*0.00*12*{payer_ctl}*11~NM1*QC*1*{WHO}~{PROV}"
            f"DTM*232*20260401~DTM*233*20260408~{line}")


def test_undated_denial_paid_on_the_corrected_claim(tmp_path):
    base(tmp_path)
    add(tmp_path, [undated("CB0000950C017", "1", "99214", "223.65", "0.00", "CAS*CO*16*223.65~", payer_ctl="A950A")], "20260415")
    add(tmp_path, [undated("CB0000950C024", "1", "99214", "223.65", "117.60", "CAS*CO*45*106.05~", "117.60", payer_ctl="A950B")], "20260520")
    add(tmp_path, [undated("CB0000950C031", "1", "99214", "223.65", "0.00", "CAS*OA*18*223.65~", payer_ctl="A950C")], "20260601")
    live, rel = both(tmp_path)
    show("live", live)
    show("release", rel)
    print("live owed:", money(live), " release owed:", money(rel))
    assert money(rel) == money(live), "the release lists an undated denial and an undated duplicate of a service paid on the bill"
