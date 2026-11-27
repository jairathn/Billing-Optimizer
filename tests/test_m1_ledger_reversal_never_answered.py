"""m1-fx-ledger review (invented data, both store kinds): a payment the payer reversed and never answered again, reported by
a recoupment (WO) naming the claim, used to be on the recoupment's own listing. fx-ledger-r2-1 sizes that listing at
nothing (the WO 'withheld nothing of its own: the service's items carry the reversal'), but no item of the service
carries a reversal that no new answer followed: the money taken back is then on no item and in no Data-tab count."""
from __future__ import annotations

import pytest

from tests.m1_ledger_helpers import BILL, OTHER, World, brief, left_on
from tests.review3.r3helpers import visit_paid, visit_reversed
from tests.test_features import clean_visits

PCN = f"{BILL}C024"


@pytest.fixture(params=["file", "compact"])
def kind(request):
    return request.param


def _paid_600(w: World) -> None:
    w.remit(clean_visits(1, 25, "17311", "1400.00", "600.00"), "20260301")
    w.remit([visit_paid(PCN, paid="600.00", code="17311", charge="1400.00", payer_ctl="PC300B")], "20260520")


def _carried(w: World, det) -> int:
    """Cents of the $600.00 some listed item carries (any state, not stale), or the Data tab counts."""
    rows = w.rows("COALESCE(stale, 0) = 0")
    on_item = sum(r["shortfall_cents"] or 0 for r in rows
                  if r["patient_control_number"] == PCN or (r["signal"] == "TAKEBACK" and not r["line_key"]))
    return on_item + w.not_on_file_cents()


@pytest.mark.parametrize("how", ["same_day_wo", "carried_then_recovered"])
def test_a_payment_reversed_and_never_answered_again_stays_on_an_item(tmp_path, kind, how):
    """May 20: Anthem pays C024's 17311 $600.00. Then Anthem reverses that payment and never answers the claim again:
    - same_day_wo: the reversal and a WO of $600.00 naming C024 in one remittance (Aug 3);
    - carried_then_recovered: the reversal with the claim's -$600.00 carried forward under its own id (FB naming C024,
      June 10), recovered by a WO of $600.00 naming C024 (Aug 3).
    The payer took the $600.00 back and nothing on file says why: it must be on an item (a TAKEBACK of the line, or the
    recoupment's own listing), or counted on the Data tab. At 2331bdc the recoupment's listing carried it; at 8cb51f6
    nothing does."""
    w = World(tmp_path, kind)
    _paid_600(w)
    if how == "same_day_wo":
        w.remit([visit_reversed(PCN, paid="600.00", code="17311", charge="1400.00", payer_ctl="PC300B"),
                 visit_paid("CB0000900C017", who=OTHER, payer_ctl="PC900", paid="1200.00")], "20260803",
                plb=f"PLB*1777777772*20261231*WO:87654321 {PCN}*600.00~")
    else:
        w.remit([visit_reversed(PCN, paid="600.00", code="17311", charge="1400.00", payer_ctl="PC300B"),
                 visit_paid("CB0000901C017", who=OTHER, payer_ctl="PC901", paid="10.00")], "20260610",
                plb=f"PLB*1777777772*20261231*FB:{PCN}*-600.00~")
        w.remit([visit_paid("CB0000900C017", who=OTHER, payer_ctl="PC900", paid="1200.00")], "20260803",
                plb=f"PLB*1777777772*20261231*WO:87654321 {PCN}*600.00~")
    det = w.run("r1", today="2026-09-20")
    rows = w.rows("COALESCE(stale, 0) = 0")
    print("standing", left_on(det, PCN), "| items:", brief([r for r in rows if r["patient_control_number"] == PCN
                                                            or r["signal"] == "TAKEBACK"]), "| data tab cents", w.not_on_file_cents())
    assert left_on(det, PCN) == 0, "the reversal took the payment back"
    assert _carried(w, det) >= 60000, "the $600.00 the payer took back is on no item and counted nowhere"
    w.store.close()


def test_control_a_reversal_answered_by_a_denial_is_carried_by_the_denial(tmp_path, kind):
    """Control (fx-ledger-r2-1's own case): the reversal comes with a new answer denying the service (CO-50); the denial
    carries the $600.00 and the recoupment is not listed a second time."""
    from tests.review3.r3helpers import visit_denied_line
    w = World(tmp_path, kind)
    _paid_600(w)
    w.remit([visit_reversed(PCN, paid="600.00", code="17311", charge="1400.00", payer_ctl="PC300B"),
             visit_denied_line(PCN, carc="50", code="17311", charge="1400.00", payer_ctl="PC300C")], "20260610",
            plb=f"PLB*1777777772*20261231*FB:{PCN}*-600.00~")
    w.remit([visit_paid("CB0000900C017", who=OTHER, payer_ctl="PC900", paid="1200.00")], "20260803",
            plb=f"PLB*1777777772*20261231*WO:87654321 {PCN}*600.00~")
    det = w.run("r1", today="2026-09-20")
    assert _carried(w, det) >= 60000
    assert not [r for r in w.rows("COALESCE(stale, 0) = 0") if r["signal"] == "TAKEBACK" and not r["line_key"]]
    w.store.close()
