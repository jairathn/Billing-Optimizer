"""Needs from others (F1 -> the closures' owner, F2 or the integrator): a failing test, kept out of the suite (the file
name does not start with test_) until the function that owns the rule is changed. Copy it into tests/ to run it.

On the real copy (tip 993f440, A17 W view, in scope) 13 Work-now items are denials of 88305's professional or technical
part (modifier 26 or TC), or a never-answered claim for one, whose whole service (88305 with no modifier) the same payer
paid on another bill of the same patient before the part's denial. A17 calls them already done (paid_as_global,
paid_on_other_bill); the item routes to 'note' (an EMA billing note) and a biller reads it for nothing.
detect.resolve_elsewhere closes a part's denial on a payment of the whole service, but only for a payment made after the
denial (or a duplicate CARC); the part billed apart after the whole service was paid is a duplicate whatever its CARC.

Invented data only, both store kinds."""
from __future__ import annotations

import pytest

from tests.test_fx_ledger import OTHER, World, brief  # noqa: F401
from tests.review3.r3helpers import clm, sv, visit_paid  # noqa: F401
from tests.test_features import clean_visits


@pytest.fixture(params=["file", "compact"])
def kind(request):
    return request.param


def test_a_part_denied_after_the_whole_service_was_paid_on_another_bill_is_not_work(tmp_path, kind):
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "88305", "120.00", "68.00"), "20260301")
    # bill 400: the whole 88305 paid $68.00 on April 20; bill 401 (the same patient, the same day): the 26 billed apart,
    # denied CO-96 on May 15
    w.remit([clm("CB0000400C017", "1", "120.00", "68.00", sv("88305", "120.00", "68.00", 1, "CAS*CO*45*52.00~", allowed="68.00"))],
            "20260420")
    w.remit([clm("CB0000401C017", "1", "60.00", "0.00", sv("88305", "60.00", "0.00", 1, "CAS*CO*96*60.00~", mods="26"))], "20260515")
    w.run("r1", today="2026-09-20")
    rows = [r for r in w.rows("patient_control_number = 'CB0000401C017' AND COALESCE(stale, 0) = 0")]
    assert all(r["work_state"] != "work_now" for r in rows), brief(rows)
    w.store.close()
