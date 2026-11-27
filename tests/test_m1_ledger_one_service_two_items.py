"""m1-fx-ledger review (invented data, both store kinds): fx-ledger-r1-1 keeps the payer's own denial of a service as the
one item that carries the takeback of the payment that later answered it ('the service is one item, that denial, the
takeback named on it') and gives it the takeback's window. An underpayment item on that same payment line (the payment
was under the payer's own rate) stays listed beside it, though the payment it measures was taken back in full: one
service on two Work-now items whose dollars do not add up to what is missing."""
from __future__ import annotations

import pytest

from tests.m1_ledger_helpers import OTHER, World, brief
from tests.review3.r3helpers import clm, sv, visit_denied_line, visit_paid
from tests.test_features import clean_visits

PCN = "CB0000300C017"
HUM = {"payer": "HUMANA", "payer_id": "61101"}        # an 18-month dispute window


@pytest.fixture(params=["file", "compact"])
def kind(request):
    return request.param


def test_a_payment_taken_back_in_full_is_not_also_worked_as_an_underpayment(tmp_path, kind):
    """Humana's usual 99214 rate is $117.60 (25 visits). It denies C017's 99214 (CO-50, March 2, 2026), pays it later at
    $80.00 (April 15: under its rate), and takes the $80.00 back by a WO naming C017 (June 1). On Sept 15, 2027 the
    denial's own 18 months have run out and the takeback's are open: fx-ledger-r1-1 puts the denial, which carries the
    takeback, on Work now. Nothing of the payment stands, so the underpayment of it must not be work beside it (at
    2331bdc the denial was expired and the underpayment alone was work; at 8cb51f6 both are)."""
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99214", "223.65", "117.60"), "20260201", **HUM)
    w.remit([visit_denied_line(PCN, carc="50", payer_ctl="PC1")], "20260302", **HUM)
    w.remit([visit_paid(PCN, paid="80.00", payer_ctl="PC2")], "20260415", **HUM)
    w.remit([visit_paid("CB0000900C017", who=OTHER, payer_ctl="PC900")], "20260601",
            plb=f"PLB*1777777772*20261231*WO:87654321 {PCN}*80.00~", **HUM)
    w.run("r1", today="2027-09-15")
    work = [r for r in w.rows("COALESCE(stale, 0) = 0 AND work_state = 'work_now'") if r["patient_control_number"] == PCN]
    print(brief(work))
    cats = sorted(r["category"] for r in work)
    assert not ("denial" in cats and "underpayment" in cats), (
        f"one service on two Work-now items: the denial carrying the takeback of the $80.00, and an underpayment of that same "
        f"$80.00, which no longer stands ({sum(r['shortfall_cents'] or 0 for r in work) / 100:.2f} claimed for a $117.60 service): {brief(work)}")
    w.store.close()
