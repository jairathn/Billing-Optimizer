"""The payment ledger: PLB WO recoupments next to line reversals (_build_ledger's reversed_on guard). Invented data only."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from r4h import Detector, add, base, brief, by_key, visit_denied_line, visit_paid, visit_reversed  # noqa: E402

OTHER = "ASH*RO****MI*W55500304"


def standing_of(det, pcn):
    return [(l["line_key"], l.get("payment_date"), l.get("paid_cents"), det.standing.get(l["line_key"]))
            for l in det.lines if l["patient_control_number"] == pcn and (l.get("paid_cents") or 0) > 0]


def test_a_recoupment_after_a_same_amount_readjudication_is_applied(tmp_path):
    """C017's visit is denied (missing information) on 2026-04-15; the corrected C024 is paid $117.60 on 2026-05-20, which
    closes the denial. On 2026-06-10 Anthem re-adjudicates C024 at the same amount (reversal -117.60 and +117.60 in one
    remittance, with other money in it). On 2026-08-03 Anthem recoups the whole payment as an overpayment (eligibility
    ended): PLB WO:CB0000300C024 117.60, in a remittance that pays another patient. Nothing stands paid on the visit."""
    base(tmp_path, pay_date="20260301").close()
    add(tmp_path, [visit_denied_line("CB0000300C017")], "20260415").close()
    add(tmp_path, [visit_paid("CB0000300C024")], "20260520").close()
    add(tmp_path, [visit_reversed("CB0000300C024"), visit_paid("CB0000300C024"), visit_paid("CB0000800C017", who=OTHER, payer_ctl="PC800")],
        "20260610").close()
    store = add(tmp_path, [visit_paid("CB0000900C017", who=OTHER, payer_ctl="PC900")], "20260803",
                plb="PLB*1777777772*20261231*WO:CB0000300C024*117.60~")
    det = Detector(store, run_id="t")
    det.run()
    print("C024 payments and what stands of them:", standing_of(det, "CB0000300C024"))
    r = by_key(store)["CB0000300C017:99214:DENIED_MISSING_INFO"]
    brief([r])
    assert sum(det.standing.get(l["line_key"], 0) for l in det.lines if l["patient_control_number"] == "CB0000300C024") == 0, \
        "the August recoupment is skipped because the June re-adjudication 'already took as much back'"
    assert r["status"] == "open", f"closed as paid by a payment Anthem recouped in August: {r['auto_note']}"
    store.close()


def test_control_the_same_recoupment_without_the_readjudication(tmp_path):
    """Without the June re-adjudication the WO takes the payment back and the denial is open."""
    base(tmp_path, pay_date="20260301").close()
    add(tmp_path, [visit_denied_line("CB0000300C017")], "20260415").close()
    add(tmp_path, [visit_paid("CB0000300C024")], "20260520").close()
    store = add(tmp_path, [visit_paid("CB0000900C017", who=OTHER, payer_ctl="PC900")], "20260803",
                plb="PLB*1777777772*20261231*WO:CB0000300C024*117.60~")
    det = Detector(store, run_id="t")
    det.run()
    print("C024 payments and what stands of them:", standing_of(det, "CB0000300C024"))
    r = by_key(store)["CB0000300C017:99214:DENIED_MISSING_INFO"]
    brief([r])
    assert r["status"] == "open"
    store.close()


def test_a_recoupment_is_not_skipped_because_a_later_reversal_took_a_different_payment(tmp_path):
    """C017 denied (2026-04-15); C024 paid $117.60 (2026-05-20) closes it. Anthem recoups that payment on 2026-06-01 (PLB
    WO:CB0000300C024 117.60). After the practice's appeal Anthem pays C024 again (2026-07-01, $117.60), then on 2026-08-03
    reverses that second payment and denies C024 (CO-22: another plan is primary). Nothing stands paid on the visit: the
    June recoupment took the first payment, the August reversal the second."""
    from r4h import clm, sv
    base(tmp_path, pay_date="20260301").close()
    add(tmp_path, [visit_denied_line("CB0000300C017")], "20260415").close()
    add(tmp_path, [visit_paid("CB0000300C024")], "20260520").close()
    add(tmp_path, [visit_paid("CB0000800C017", who=OTHER, payer_ctl="PC800")], "20260601",
        plb="PLB*1777777772*20261231*WO:CB0000300C024*117.60~").close()
    add(tmp_path, [visit_paid("CB0000300C024")], "20260701").close()
    store = add(tmp_path, [visit_reversed("CB0000300C024"),
                           clm("CB0000300C024", "4", "223.65", "0.00", sv("99214", "223.65", "0.00", 1, "CAS*CO*22*223.65~")),
                           visit_paid("CB0000900C017", who=OTHER, payer_ctl="PC900")], "20260803")
    det = Detector(store, run_id="t")
    det.run()
    print("C024 payments and what stands of them:", standing_of(det, "CB0000300C024"))
    rows_ = by_key(store)
    brief(rows_.values())
    standing = sum(det.standing.get(l["line_key"], 0) for l in det.lines if l["patient_control_number"] == "CB0000300C024")
    assert standing == 0, f"${standing/100:.2f} still counted as paid: the June recoupment is skipped because the August reversal 'already took as much'"
    store.close()
