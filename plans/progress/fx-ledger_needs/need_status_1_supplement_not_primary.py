"""Needs from others (F1 -> F2, whose residual this is: "a supplement that answers with claim status 1 is counted as a
primary payment"; archive_rules.other_payer_paid_first is in no fixer's row): a failing test, kept out of the suite (the
file name does not start with test_) until the archive rule reads such an answer as a second payer's. Copy it into
tests/ to run it. Found while fixing fx-ledger-r1-3.

fx-ledger-r1-3 (F1, fixed): states.paid_proof's paid_before_other_payer and detect._taken_back_items' cover no longer read
a status-1 answer carrying CARC 22 or 23 (processed after another payer: OA-23, the prior payer's adjudication) as
another plan's payment as primary. The archive rule other_payer_paid_first (archive_rules.py) still does: its `prim`
list takes any standing payment with x.status in PRIMARY_STATUSES. Medicare paid the 99214, the supplement paid the
$29.40 coinsurance with claim status 1 and OA-23, then Medicare reversed its payment and denied the service (CO-97): the
rule says "Paid by TESTGAP SUPPLEMENT as primary on 2026-04-25 ($29.40): this denial by KY MEDICARE is another payer's
answer, not a denial to appeal. Nothing is left to collect from a payer", proven, done, sized $0, and _decide's
_archive_done closes Medicare's denial on the supplement's coinsurance.

The change: in other_payer_paid_first, leave out of `prim` a payment whose line or claim carries CARC 22 or 23 by the
payer (states._after_other_payer and detect.Detector._after_other_payer read it the same way), or, with F2's residual,
any payment of a payer the 835 or the 837 names as the second payer. Real copy (reviewers): 820 paid supplement answers
report claim status 1, 342 of them with OA-22 or OA-23.

Invented data only, both store kinds."""
from __future__ import annotations

from tests.review3.r3helpers import clm, sv
from tests.test_features import clean_visits
from tests.test_fx_ledger import BILL, GAP, KY, World, kind  # noqa: F401  (kind: the both-store-kinds fixture)


def test_medicares_denial_is_not_closed_on_the_supplements_status_1_coinsurance(tmp_path, kind):  # noqa: F811
    w = World(tmp_path, kind)
    pcn = f"{BILL}C017"
    w.remit(clean_visits(1, 25, "99214", "223.65", "147.00"), "20260301", **KY)
    w.remit([clm(pcn, "19", "223.65", "117.60", sv("99214", "223.65", "117.60", 1, "CAS*CO*45*76.65~CAS*PR*2*29.40~", allowed="147.00"),
                 payer_ctl="MC1")], "20260415", **KY)
    w.remit([clm(pcn, "1", "223.65", "29.40", sv("99214", "223.65", "29.40", 1, "CAS*CO*45*76.65~CAS*OA*23*117.60~", allowed="147.00"),
                 payer_ctl="GP1")], "20260425", **GAP)
    w.remit([clm(pcn, "22", "-223.65", "-117.60", sv("99214", "-223.65", "-117.60", -1, "CAS*CO*45*-76.65~CAS*PR*2*-29.40~",
                                                     allowed="-147.00"), payer_ctl="MC1"),
             clm(pcn, "1", "223.65", "0.00", sv("99214", "223.65", "0.00", 1, "CAS*CO*97*223.65~"), payer_ctl="MC2")], "20260520", **KY)
    w.run("r1", today="2026-09-20")
    den = [r for r in w.rows(f"patient_control_number LIKE '{BILL}%' AND COALESCE(stale, 0) = 0") if "Supplement" not in (r["payer_group"] or "")]
    assert den, "Medicare's denial is an item"
    for r in den:
        assert not (r["work_state"] == "done" and "as primary" in (r["state_basis"] or "")), (
            f"Medicare's denial is closed on the supplement's $29.40 coinsurance (claim status 1, OA-23) as if it paid as primary: "
            f"{r['state_basis']}")
    w.store.close()
