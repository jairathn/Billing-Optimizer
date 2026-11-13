"""A reversal whose own payer has no earlier payment in the archive (paid before 2024-01-01, or in a missing file) falls
back to any payer's payment on the bill, code and date. Invented data only."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from r3helpers import add, base, by_key, show, visit_paid, visit_reversed  # noqa: E402

from remitwatch.detect import Detector  # noqa: E402

DOS = "20251110"


def denied_whole(pcn, carc="22", dos=DOS):
    from r3helpers import clm, sv
    return clm(pcn, "4", "223.65", "0.00", sv("99214", "223.65", "0.00", 1, f"CAS*CO*{carc}*223.65~", dos), dos=dos)


def test_one_payers_reversal_does_not_cancel_another_payers_payment(tmp_path):
    """Anthem paid bill 300 (C017) in November 2025, before the archive starts. The patient's coverage had moved: Humana
    is billed under C024 and pays it as primary on 2026-02-15. On 2026-05-01 Anthem takes its old payment back (status 22)
    and re-adjudicates C017 as denied (CO-22, covered by another payer). The visit is paid, by Humana."""
    base(tmp_path, pay_date="20260415").close()
    add(tmp_path, [visit_paid("CB0000300C024", dos=DOS, payer_ctl="HUM300")], "20260215", payer="HUMANA", payer_id="HUMNA").close()
    store = add(tmp_path, [visit_reversed("CB0000300C017", dos=DOS, payer_ctl="ANT300"), denied_whole("CB0000300C017")], "20260501")
    det = Detector(store, run_id="t")
    det.run()
    humana = [l["line_key"] for l in det.lines if l["patient_control_number"] == "CB0000300C024"]
    standing = getattr(det, "standing", None)
    if standing is not None:
        print("Humana's payment still standing:", {k: standing.get(k) for k in humana})
    rows = by_key(store)
    show(rows.values())
    assert "CB0000300C017::CLAIM_DENIED_UNPAID" not in rows or rows["CB0000300C017::CLAIM_DENIED_UNPAID"]["status"] != "open", \
        "Anthem's denial is listed as never paid although Humana paid the visit"
    if standing is not None:
        assert all(standing.get(k) for k in humana), "Anthem's reversal of its own pre-archive payment cancelled Humana's payment"
    store.close()


def test_control_the_same_reversal_with_anthems_payment_in_the_archive(tmp_path):
    """The same story with Anthem's November payment in the archive: the reversal cancels Anthem's own payment, Humana's
    stands, nothing is listed."""
    base(tmp_path, pay_date="20260415").close()
    add(tmp_path, [visit_paid("CB0000300C017", dos=DOS, payer_ctl="ANT300")], "20251130").close()
    add(tmp_path, [visit_paid("CB0000300C024", dos=DOS, payer_ctl="HUM300")], "20260215", payer="HUMANA", payer_id="HUMNA").close()
    store = add(tmp_path, [visit_reversed("CB0000300C017", dos=DOS, payer_ctl="ANT300"), denied_whole("CB0000300C017")], "20260501")
    det = Detector(store, run_id="t")
    det.run()
    rows = by_key(store)
    show(rows.values())
    assert "CB0000300C017::CLAIM_DENIED_UNPAID" not in rows
    store.close()


def test_a_line_denial_paid_by_the_new_payer_is_reopened_by_the_old_payers_reversal(tmp_path):
    """Humana first denies C024 for missing information (2026-01-20), then pays the corrected C031 (2026-02-15): the
    denial closes. Anthem's later takeback of its own pre-archive payment on C017 must not reopen it."""
    from r3helpers import visit_denied_line
    base(tmp_path, pay_date="20260415").close()
    add(tmp_path, [visit_denied_line("CB0000300C024", dos=DOS, payer_ctl="HUM300")], "20260120", payer="HUMANA", payer_id="HUMNA").close()
    add(tmp_path, [visit_paid("CB0000300C031", dos=DOS, payer_ctl="HUM301")], "20260215", payer="HUMANA", payer_id="HUMNA").close()
    store = add(tmp_path, [visit_reversed("CB0000300C017", dos=DOS, payer_ctl="ANT300"), denied_whole("CB0000300C017")], "20260501")
    Detector(store, run_id="t").run()
    rows = by_key(store)
    show(rows.values())
    r = rows["CB0000300C024:99214:DENIED_MISSING_INFO"]
    assert r["status"] == "resolved", "Humana's denial was paid by Humana on C031, but shows open after Anthem's takeback"
    store.close()


def test_control_the_line_denial_closes_without_anthems_takeback(tmp_path):
    from r3helpers import visit_denied_line
    base(tmp_path, pay_date="20260415").close()
    add(tmp_path, [visit_denied_line("CB0000300C024", dos=DOS, payer_ctl="HUM300")], "20260120", payer="HUMANA", payer_id="HUMNA").close()
    store = add(tmp_path, [visit_paid("CB0000300C031", dos=DOS, payer_ctl="HUM301")], "20260215", payer="HUMANA", payer_id="HUMNA")
    Detector(store, run_id="t").run()
    assert by_key(store)["CB0000300C024:99214:DENIED_MISSING_INFO"]["status"] == "resolved"
    store.close()
