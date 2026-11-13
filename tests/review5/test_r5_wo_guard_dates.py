"""_build_ledger's WO guard, now keyed by (reference, date): a WO is skipped only when a line reversal on the same claim
and the same day took as much. A delayed recovery (TR3 'overpayment recovery': the reversal's remittance carries the
negative balance with a negative WO, a later remittance recovers it with a positive WO) is netted per reference; when
the two WOs name the claim differently (payer claim number, then claim id), the later WO is no longer recognised as
the reversal's money and takes back the re-payment made in between. Invented data only. Run from the repository root."""
from __future__ import annotations

import sys

import os  # noqa: E402
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from tests.review4.r4h import Detector, add, base, brief, by_key, visit_denied_line, visit_paid, visit_reversed  # noqa: E402

OTHER = "ASH*RO****MI*W55500304"


def test_delayed_recovery_with_mixed_references_takes_back_the_repayment(tmp_path):
    """C017 denied (04-15); C024 paid $117.60 (05-20, payer claim number PC1). 06-10: Anthem reverses C024 alone; the
    remittance carries the negative balance (PLB WO:PC1 -117.60). 06-20: after the appeal Anthem pays C024 again. 07-01:
    Anthem recovers the carried balance (PLB WO:CB0000300C024 117.60) from another patient's payment. The visit is paid
    (the 06-20 payment); the 07-01 WO is the 06-10 reversal's money."""
    base(tmp_path, pay_date="20260301").close()
    add(tmp_path, [visit_denied_line("CB0000300C017")], "20260415").close()
    add(tmp_path, [visit_paid("CB0000300C024", payer_ctl="PC300B")], "20260520").close()     # a payer claim number names one claim
    add(tmp_path, [visit_reversed("CB0000300C024", payer_ctl="PC300B")], "20260610", plb="PLB*1777777772*20261231*WO:PC300B*-117.60~").close()
    add(tmp_path, [visit_paid("CB0000300C024")], "20260620").close()
    store = add(tmp_path, [visit_paid("CB0000900C017", who=OTHER, payer_ctl="PC900")], "20260701",
                plb="PLB*1777777772*20261231*WO:CB0000300C024*117.60~")
    det = Detector(store, run_id="t")
    det.run()
    standing = sum(det.standing.get(l["line_key"], 0) for l in det.lines if l["patient_control_number"] == "CB0000300C024")
    r = by_key(store)["CB0000300C017:99214:DENIED_MISSING_INFO"]
    brief([r])
    print("C024 standing:", standing)
    assert standing == 11760, f"the 06-20 re-payment is taken back by the recovery of the 06-10 reversal's balance (standing ${standing/100:.2f})"
    store.close()


def test_control_same_reference_nets_to_nothing(tmp_path):
    """The TR3 pattern with one reference: the two WOs net to zero and the re-payment stands."""
    base(tmp_path, pay_date="20260301").close()
    add(tmp_path, [visit_denied_line("CB0000300C017")], "20260415").close()
    add(tmp_path, [visit_paid("CB0000300C024")], "20260520").close()
    add(tmp_path, [visit_reversed("CB0000300C024")], "20260610", plb="PLB*1777777772*20261231*WO:CB0000300C024*-117.60~").close()
    add(tmp_path, [visit_paid("CB0000300C024")], "20260620").close()
    store = add(tmp_path, [visit_paid("CB0000900C017", who=OTHER, payer_ctl="PC900")], "20260701",
                plb="PLB*1777777772*20261231*WO:CB0000300C024*117.60~")
    det = Detector(store, run_id="t")
    det.run()
    standing = sum(det.standing.get(l["line_key"], 0) for l in det.lines if l["patient_control_number"] == "CB0000300C024")
    print("C024 standing:", standing)
    assert standing == 11760
    store.close()
