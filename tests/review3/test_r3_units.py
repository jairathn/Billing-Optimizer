"""Units on a bill: the primary's and the secondary's payments for the same units are one payment of those units.
Invented data only."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from r3helpers import add, base, by_key, clm, show, sv  # noqa: E402

from remitwatch.detect import Detector  # noqa: E402


def five_units(pcn, status):
    return clm(pcn, status, "125.00", "0.00", sv("17003", "125.00", "0.00", 5, "CAS*CO*16*125.00~"))


def three_units_paid(pcn, status, paid, extra=""):
    return clm(pcn, status, "75.00", paid, sv("17003", "75.00", paid, 3, "CAS*CO*45*57.00~" + extra, allowed="18.00"))


def _story(tmp_path, denied_status):
    base(tmp_path, "17003", "25.00", "6.00").close()
    add(tmp_path, [five_units("CB0000300C017", denied_status)], "20260415").close()
    add(tmp_path, [three_units_paid("CB0000300C024", "1", "18.00")], "20260520").close()                  # primary: 3 units
    return add(tmp_path, [three_units_paid("CB0000300C024", "2", "3.60", "CAS*PR*2*14.40~")], "20260530",  # secondary: the same 3 units
               payer="HUMANA", payer_id="HUMNA")


def test_a_whole_claim_denial_of_five_units_with_three_paid_is_listed(tmp_path):
    """C017 (17003 x 5) is denied whole. The corrected C024 bills 3 units: the primary pays them, then the secondary pays
    its share of the same 3 units. Two units were never paid."""
    store = _story(tmp_path, "4")
    Detector(store, run_id="t").run()
    rows = by_key(store)
    show(rows.values())
    assert "CB0000300C017::CLAIM_DENIED_UNPAID" in rows, "3 units paid by the primary and the same 3 by the secondary count as 6: hidden"
    store.close()


def test_a_line_denial_of_five_units_with_three_paid_stays_open(tmp_path):
    """The same with the denial on the line (claim processed, line paid $0): 3 of 5 units paid is a hint, not a closure."""
    store = _story(tmp_path, "1")
    Detector(store, run_id="t").run()
    rows = by_key(store)
    show(rows.values())
    r = rows["CB0000300C017:17003:DENIED_MISSING_INFO"]
    assert r["status"] == "open", f"closed as paid: {r['auto_note']}"
    store.close()


def test_control_without_the_secondary(tmp_path):
    base(tmp_path, "17003", "25.00", "6.00").close()
    add(tmp_path, [five_units("CB0000300C017", "1")], "20260415").close()
    store = add(tmp_path, [three_units_paid("CB0000300C024", "1", "18.00")], "20260520")
    Detector(store, run_id="t").run()
    r = by_key(store)["CB0000300C017:17003:DENIED_MISSING_INFO"]
    show([r])
    assert r["status"] == "open" and r["hint"] and "3 of 5 units" in r["hint"]
    store.close()
