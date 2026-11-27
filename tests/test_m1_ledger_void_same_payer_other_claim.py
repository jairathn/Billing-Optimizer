"""m1-fx-ledger review (invented data, both store kinds): fx-ledger-r2-2 counts a void only for the paid claim, but also
for any send of the bill 'to the same payer' (detect._voided_services: `or (grp and g == grp)`). A void of another claim
of the bill to that payer, billing another service, then drops the recoupment of a payment the practice never voided:
the money taken back is on no item."""
from __future__ import annotations

import pytest

from tests.m1_ledger_helpers import BILL, OTHER, World, brief, left_on
from tests.review3.r3helpers import visit_paid
from tests.test_features import clean_visits


@pytest.fixture(params=["file", "compact"])
def kind(request):
    return request.param


def test_a_void_of_another_claim_to_the_same_payer_does_not_drop_the_takeback(tmp_path, kind):
    """Anthem pays C017's 99214 $117.60 on May 20 (C017 sent April 2). On June 1 the practice sends C024 to Anthem, a
    claim of the same bill for another service (17110), and voids C024 on June 15; nothing is sent later. In August
    Anthem withholds the $117.60 naming C017. C017 was never voided: the recoupment is a takeback to work, and no item
    lists it at 8cb51f6."""
    from tests.test_837_sends import clm837, send
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99214", "223.65", "117.60"), "20260301")
    send(w.store, tmp_path, [clm837(f"{BILL}C017", [("99214", "223.65", "20260401")])], "20260402")
    w.remit([visit_paid(f"{BILL}C017", payer_ctl="PC300B")], "20260520")
    other = [("17110", "150.00", "20260401")]
    send(w.store, tmp_path, [clm837(f"{BILL}C024", other)], "20260601")
    send(w.store, tmp_path, [clm837(f"{BILL}C024", other, freq="8")], "20260615")
    w.remit([visit_paid("CB0000900C017", who=OTHER, payer_ctl="PC900")], "20260803",
            plb=f"PLB*1777777772*20261231*WO:87654321 {BILL}C017*117.60~")
    det = w.run("r1", today="2026-09-20")
    assert left_on(det, f"{BILL}C017") == 0, "the ledger took Anthem's payment back"
    tb = [r for r in w.rows("COALESCE(stale, 0) = 0") if r["signal"] == "TAKEBACK"]
    print(brief(tb))
    assert sum(r["shortfall_cents"] or 0 for r in tb) == 11760, (
        f"Anthem took back C017's $117.60; only C024 (another service) was voided; no item lists the takeback: {brief(tb)}")
    w.store.close()
