"""A later answer whose payment and negative line of the same service net to nothing pays nothing (the release's
real-data run of 2026-11-27: items closed as 'Paid on a later remittance (..., $0.00)', the self-check closure_proof's
offenders). Invented data, both store kinds."""
from __future__ import annotations

import pytest

from tests.m1_ledger_helpers import World
from tests.review3.r3helpers import clm, sv, visit_denied_line

PCN = "CB0000700C017"


@pytest.fixture(params=["file", "compact"])
def kind(request):
    return request.param


def _world(tmp_path, kind, *, net_zero: bool):
    """Anthem denies the 99214 of April 1 (CO-16, March 31 answer... here April 10). On May 20 its answer to the corrected
    claim C018 carries the 99214 paid $117.60 and, on a second line of the same code and date, -$117.60 (the payer's own
    correction inside a processed claim): net, nothing. The control pays $117.60 with no negative line."""
    w = World(tmp_path, kind)
    w.remit([visit_denied_line(PCN, carc="16", payer_ctl="PC1")], "20260410")
    lines = sv("99214", "223.65", "117.60", 1, "CAS*CO*45*106.05~", "20260401", allowed="117.60")
    if net_zero:
        lines += sv("99214", "-223.65", "-117.60", -1, "CAS*CO*45*-106.05~", "20260401")
    w.remit([clm("CB0000700C018", "1", "0.00" if net_zero else "223.65", "0.00" if net_zero else "117.60", lines,
                 payer_ctl="PC2")], "20260520")
    w.run("r1", today="2026-06-01")
    return w


def test_a_payment_and_its_negative_line_netting_to_nothing_do_not_close_the_denial(tmp_path, kind):
    w = _world(tmp_path, kind, net_zero=True)
    rows = [r for r in w.rows("COALESCE(stale, 0) = 0") if r["patient_control_number"] == PCN]
    closed = [r for r in rows if r["status"] == "resolved" and (r.get("auto_note") or "").startswith("Paid on")]
    assert not closed, f"the payer's answer netted to $0.00 for the 99214, yet the denial was closed as paid: {[r['auto_note'] for r in closed]}"
    assert any(r["status"] != "resolved" for r in rows), "the denial of the 99214 is on no open item"
    w.store.close()


def test_control_a_later_payment_that_stands_closes_the_denial(tmp_path, kind):
    w = _world(tmp_path, kind, net_zero=False)
    rows = [r for r in w.rows("COALESCE(stale, 0) = 0") if r["patient_control_number"] == PCN]
    assert rows and all(r["status"] == "resolved" for r in rows), [(r["signal"], r["status"]) for r in rows]
    w.store.close()
