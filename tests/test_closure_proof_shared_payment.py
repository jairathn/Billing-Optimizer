"""A closure by a later payment whose units another item already counted names that payment with its own amount (the
release's real-data run of 2026-11-27: 33 forwarded-to-another-carrier items closed as 'Paid on a later remittance (...,
$0.00)', failing the self-check closure_proof). The recovery stays counted once, on the other item."""
from __future__ import annotations

from remitwatch import selfcheck
from remitwatch.detect import Detector


def _event(day="2026-05-20", trace="TRACE000000002", amount=14201, status="1", claim="CB0000700C017"):
    # (payment date, trace, amount standing, claim status, claim id, units, payer, date of service, line, modifiers, part)
    return (day, trace, amount, status, claim, 1.0, "PAYER", "2026-04-01", "LINEKEY1", "", "")


def test_a_payment_whose_recovery_another_item_counted_is_named_with_its_own_amount():
    e = _event()
    note, detail = Detector._paid_words([(e, 1.0, 0.0)], e)
    assert "$0.00" not in note, note
    assert "$142.01" in note and "counted on another item" in note, note
    assert detail["paid_cents"] == 14201 and detail.get("recovered_cents") == 0 and detail.get("recovery_counted_elsewhere")
    assert selfcheck.has_proof({"paid_later": detail}), detail


def test_control_a_payment_that_paid_this_item_names_what_it_paid():
    e = _event()
    note, detail = Detector._paid_words([(e, 1.0, 9000.0)], e)
    assert "$90.00" in note and "another item" not in note, note
    assert detail["paid_cents"] == 9000 and "recovery_counted_elsewhere" not in detail
