"""Provider-level recoupments (PLB WO) and claims sent without service lines. Invented data only."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from r3helpers import add, base, by_key, clm, show, visit_denied_line, visit_paid, visit_reversed  # noqa: E402

from remitwatch.detect import Detector  # noqa: E402

OTHER = "ASH*RO****MI*W55500304"


def test_a_balance_carried_with_wo_is_not_taken_back_twice(tmp_path):
    """C017's visit is denied (missing information) on 2026-04-15. The corrected C024 is paid $117.60 on 2026-05-20, which
    closes the denial. On 2026-06-10 Anthem re-adjudicates C024 at $50.00: reversal -117.60, corrected claim +50.00. The
    remittance has nothing else in it, so the net -67.60 is carried forward with a negative WO adjustment (PLB WO -67.60)
    and recovered on 2026-07-01 (PLB WO +67.60). The $50.00 was paid and stands; the payer took back $67.60 once."""
    base(tmp_path, pay_date="20260301").close()
    add(tmp_path, [visit_denied_line("CB0000300C017")], "20260415").close()
    add(tmp_path, [visit_paid("CB0000300C024")], "20260520").close()
    add(tmp_path, [visit_reversed("CB0000300C024"), visit_paid("CB0000300C024", paid="50.00")], "20260610",
        plb="PLB*1777777772*20261231*WO:CB0000300C024*-67.60~").close()
    store = add(tmp_path, [visit_paid("CB0000800C017", who=OTHER, payer_ctl="PC800")], "20260701",
                plb="PLB*1777777772*20261231*WO:CB0000300C024*67.60~")
    det = Detector(store, run_id="t")
    det.run()
    print("plb:", [tuple(r) for r in store.query("SELECT reference, amount_cents, payment_date FROM plb")])
    corrected = [l for l in det.lines if l["patient_control_number"] == "CB0000300C024" and l.get("paid_cents") == 5000]
    print("the corrected $50.00 still standing:", [getattr(det, "standing", {}).get(l["line_key"]) for l in corrected])
    r = by_key(store)["CB0000300C017:99214:DENIED_MISSING_INFO"]
    show([r])
    assert r["status"] == "resolved", "the $50.00 Anthem paid on C024 is counted as taken back: the denial is open again"
    store.close()


def test_control_without_the_carried_balance(tmp_path):
    """The same re-adjudication in a remittance with other money in it (no PLB): the $50.00 stands and the denial stays closed."""
    base(tmp_path, pay_date="20260301").close()
    add(tmp_path, [visit_denied_line("CB0000300C017")], "20260415").close()
    add(tmp_path, [visit_paid("CB0000300C024")], "20260520").close()
    store = add(tmp_path, [visit_reversed("CB0000300C024"), visit_paid("CB0000300C024", paid="50.00"),
                           visit_paid("CB0000800C017", who=OTHER, payer_ctl="PC800")], "20260610")
    Detector(store, run_id="t").run()
    r = by_key(store)["CB0000300C017:99214:DENIED_MISSING_INFO"]
    show([r])
    assert r["status"] == "resolved"
    store.close()


# --------------------------------------------------------------------------- claims with no service lines
def test_a_reversal_sent_without_service_lines_still_takes_the_payment_back(tmp_path):
    """C024 was paid with its service line on 2026-05-20, closing C017's denial. On 2026-06-10 the payer reverses C024 at
    the claim level only (CLP02 = 22 with no SVC loop) and denies the claim (CLP02 = 4, claim-level CO-16, no SVC loop).
    Nothing is paid on the visit any more."""
    base(tmp_path, pay_date="20260301").close()
    add(tmp_path, [visit_denied_line("CB0000300C017")], "20260415").close()
    add(tmp_path, [visit_paid("CB0000300C024")], "20260520").close()
    store = add(tmp_path, [clm("CB0000300C024", "22", "-223.65", "-117.60", claim_cas="CAS*CO*45*-106.05~"),
                           clm("CB0000300C024", "4", "223.65", "0.00", claim_cas="CAS*CO*16*223.65~")], "20260610")
    Detector(store, run_id="t").run()
    rows = by_key(store)
    show(rows.values())
    r = rows["CB0000300C017:99214:DENIED_MISSING_INFO"]
    assert r["status"] == "open", f"closed by a payment the payer reversed: {r['auto_note']}"
    store.close()


def test_a_claim_level_denial_paid_later_at_the_claim_level_is_not_listed(tmp_path):
    """A payer that sends claims without service lines: C017 denied (CLP02 = 4, claim-level CO-16) on 2026-04-15, the
    corrected C024 paid $117.60 (CLP02 = 1) on 2026-05-20, both with no SVC loop. run_claim_rules says a claim with no
    service lines counts unless a primary payment on the bill came after it."""
    base(tmp_path, pay_date="20260301").close()
    add(tmp_path, [clm("CB0000300C017", "4", "223.65", "0.00", claim_cas="CAS*CO*16*223.65~")], "20260415").close()
    store = add(tmp_path, [clm("CB0000300C024", "1", "223.65", "117.60", claim_cas="CAS*CO*45*106.05~")], "20260520")
    Detector(store, run_id="t").run()
    rows = by_key(store)
    show(rows.values())
    assert "CB0000300C017::CLAIM_DENIED_UNPAID" not in rows, "listed as never paid although a primary payment on the bill came after it"
    store.close()
