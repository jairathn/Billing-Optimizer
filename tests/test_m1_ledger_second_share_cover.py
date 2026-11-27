"""m1-fx-ledger review (invented data, both store kinds): fx-ledger-r2-5 lets any other second payer's standing payment of
the service cover a second payer's share that was taken back, whatever that payment's amount (units_paid counts units,
never money). A second payer that paid a few cents of the coinsurance then hides the whole share taken back: the money
the patient or a payer now owes is on no item."""
from __future__ import annotations

import pytest

from tests.m1_ledger_helpers import BILL, OTHER, World, brief
from tests.review3.r3helpers import clm, sv, visit_paid
from tests.test_features import clean_visits

KY = {"payer": "KY MEDICARE", "payer_id": "MCRKY"}
GAP = {"payer": "TESTGAP SUPPLEMENT", "payer_id": "TGAP1"}
AARP = {"payer": "AARP MEDICARE SUPPLEMENT", "payer_id": "AARP1"}
PCN = f"{BILL}C017"


@pytest.fixture(params=["file", "compact"])
def kind(request):
    return request.param


def _world(tmp_path, kind, other_paid: str, other_day: str = "20260418") -> World:
    """Medicare pays C017's 99214 $64.00 (coinsurance $16.00, April 15). Another supplement pays `other_paid` of the share
    (`other_day`), the test supplement pays the $16.00 share (April 20); in August the test supplement recoups its $16.00
    naming C017."""
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99214", "223.65", "80.00"), "20260301", **KY)
    w.remit([clm(PCN, "1", "223.65", "64.00", sv("99214", "223.65", "64.00", 1, "CAS*CO*45*143.65~CAS*PR*2*16.00~", allowed="80.00"),
                 payer_ctl="MC1")], "20260415", **KY)
    rest = f"{223.65 - float(other_paid):.2f}"
    other = [clm(PCN, "2", "223.65", other_paid, sv("99214", "223.65", other_paid, 1, f"CAS*OA*23*{rest}~"), payer_ctl="AA1")]
    if other_day < "20260420":
        w.remit(other, other_day, **AARP)
    w.remit([clm(PCN, "2", "223.65", "16.00", sv("99214", "223.65", "16.00", 1, "CAS*OA*23*207.65~"), payer_ctl="GP1")], "20260420", **GAP)
    if other_day > "20260420":
        w.remit(other, other_day, **AARP)
    w.remit([visit_paid("CB0000900C017", who=OTHER, payer_ctl="GP9", paid="16.00")], "20260803",
            plb=f"PLB*1777777772*20261231*WO:5550001 {PCN}*16.00~", **GAP)
    return w


def test_a_few_cents_from_another_second_payer_do_not_pay_the_share_taken_back(tmp_path, kind):
    """The other supplement paid $0.40 of the $16.00 coinsurance (April 18, before the test supplement). The test
    supplement took its $16.00 back: $15.60 of the coinsurance is open again (bill the patient, or dispute the takeback).
    At 8cb51f6 nothing lists it: the $0.40 counts as the whole unit paid again (fx-ledger-r2-5's cover counts units, never
    money). At 316b9d8 (before the fix round) the share was listed."""
    w = _world(tmp_path, kind, "0.40")
    w.run("r1", today="2026-09-20")
    tb = [r for r in w.rows("COALESCE(stale, 0) = 0") if r["signal"] == "TAKEBACK"]
    print(brief(tb))
    assert sum(r["shortfall_cents"] or 0 for r in tb) >= 1560, f"$15.60 of the coinsurance taken back is on no item: {brief(tb)}"
    w.store.close()


def test_control_the_other_supplement_paying_the_whole_share_is_a_double_payment(tmp_path, kind):
    """Control (fx-ledger-r2-5's own case): the other supplement paid the whole $16.00; the recoupment recovers a double
    payment and nothing is listed."""
    w = _world(tmp_path, kind, "16.00")
    w.run("r1", today="2026-09-20")
    assert [r for r in w.rows("COALESCE(stale, 0) = 0") if r["signal"] == "TAKEBACK"] == []
    w.store.close()
