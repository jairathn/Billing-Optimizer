"""The duplicate rule's paid-sibling count (run_line_rules, DUP_CARCS). Invented data only."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from r4h import add, base, brief, clm, rows, run, sv  # noqa: E402


def test_a_visit_keyed_twice_on_one_claim_is_not_listed_as_unpaid(tmp_path):
    """The visit (99214) was keyed twice on C017 by mistake. Anthem pays the first line $117.60 and denies the second as an
    exact duplicate (CO-18). One visit, paid once: nothing is owed. 99214 is paid at most once per patient and date here
    (25 patient-dates, never twice)."""
    base(tmp_path).close()
    paid = sv("99214", "223.65", "117.60", 1, "CAS*CO*45*106.05~", allowed="117.60")
    dup = sv("99214", "223.65", "0.00", 1, "CAS*CO*18*223.65~")
    store = add(tmp_path, [clm("CB0000300C017", "1", "447.30", "117.60", paid + dup)], "20260415")
    run(store, "t", "2026-04-16T09:00:00+00:00")
    rs = rows(store, "category='denial'")
    brief(rs)
    assert not [r for r in rs if r["status"] == "open"], \
        f"a visit keyed twice and paid once is listed as denied and never paid (${sum(r['shortfall_cents'] or 0 for r in rs)/100:.2f})"
    store.close()


def test_a_corrected_claim_that_repeats_the_paid_visit_is_not_listed(tmp_path):
    """C017's visit is paid $117.60 (2026-04-15). The corrected claim C024 replaces it: Anthem reverses C017's payment and
    processes C024, paying its 99214 line and denying the same visit keyed a second time on C024 (CO-18). The visit is
    paid once, on C024."""
    base(tmp_path).close()
    add(tmp_path, [clm("CB0000300C017", "1", "223.65", "117.60", sv("99214", "223.65", "117.60", 1, "CAS*CO*45*106.05~", allowed="117.60"))],
        "20260415").close()
    reversal = clm("CB0000300C017", "22", "-223.65", "-117.60", sv("99214", "-223.65", "-117.60", -1, "CAS*CO*45*-106.05~"))
    paid = sv("99214", "223.65", "117.60", 1, "CAS*CO*45*106.05~", allowed="117.60")
    dup = sv("99214", "223.65", "0.00", 1, "CAS*CO*18*223.65~")
    store = add(tmp_path, [reversal, clm("CB0000300C024", "1", "447.30", "117.60", paid + dup)], "20260520")
    run(store, "t", "2026-05-21T09:00:00+00:00")
    rs = rows(store, "category='denial'")
    brief(rs)
    assert not [r for r in rs if r["status"] == "open"], "the visit is paid on C024; its keyed-twice copy is listed as never paid"
    store.close()
