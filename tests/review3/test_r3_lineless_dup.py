"""A duplicate submission denied at the claim level, with no service lines, after the claim was paid. Invented data only."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from r3helpers import add, base, by_key, clm, show, visit_paid  # noqa: E402

from remitwatch.detect import Detector  # noqa: E402


def test_a_lineless_duplicate_denial_of_a_paid_claim_is_not_listed_as_never_paid(tmp_path):
    """C017 is paid $117.60 on 2026-04-15. The claim is sent again by mistake and the payer denies the copy on 2026-05-20
    as an exact duplicate (CLP02 = 4, claim-level CO-18) without service lines. Nothing is unpaid."""
    base(tmp_path).close()
    add(tmp_path, [visit_paid("CB0000300C017")], "20260415").close()
    store = add(tmp_path, [clm("CB0000300C017", "4", "223.65", "0.00", claim_cas="CAS*CO*18*223.65~")], "20260520")
    Detector(store, run_id="t").run()
    rows = by_key(store)
    show(rows.values(), ("patient_control_number", "signal", "title", "status", "hint"))
    assert not [r for r in rows.values() if r["status"] == "open"], "a duplicate of a paid claim is listed as denied and never paid"
    store.close()


def test_the_same_for_a_resubmission_under_the_next_suffix(tmp_path):
    base(tmp_path).close()
    add(tmp_path, [visit_paid("CB0000300C017")], "20260415").close()
    store = add(tmp_path, [clm("CB0000300C024", "4", "223.65", "0.00", claim_cas="CAS*CO*18*223.65~")], "20260520")
    Detector(store, run_id="t").run()
    rows = by_key(store)
    show(rows.values(), ("patient_control_number", "signal", "title", "status", "hint"))
    assert not [r for r in rows.values() if r["status"] == "open"], "a duplicate of a paid claim is listed as denied and never paid"
    store.close()
