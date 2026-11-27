"""
fx-exports (fix wave F3b): Needs from others, save() (no fixer owns it). Not collected by the suite (no test_ prefix):
run it with  python3 -m pytest -q plans/progress/fx-exports_needs_save.py  once the change below is made.

The change: save() hands an item's work to its heir (the heir keys an item records in self._heirs, and via_bill) only
when the heir is new in that run (`prev is None`). Apply the heir keys also to an existing heir that has no work of its
own (save's worked() is false for it): start it from the yielding item as via_bill does (Started if someone was working
it, with the notes; the faxes and history through carry_to_heirs). Then, in detect._exports_yield_to_newer_bills, take
out the branch counted as 'export_kept_for_its_work' when the heir has no work of its own (keep it when both items were
worked: both stay listed and point to each other). fx-exports-r1-2 and r2-5.

Invented data only (tests/test_fx_exports.py's builders).
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests.test_837_sends import KINDS  # noqa: E402
from tests.test_fx_exports import _rebill_world, ema_env  # noqa: E402,F401


@pytest.mark.parametrize("kind", KINDS)
def test_work_on_an_export_item_follows_the_newer_bill_listed_before(tmp_path, kind, ema_env):
    """The first run lists both bills (nothing names their patient yet); a biller starts the exports' item with a note.
    EMA's reports naming the patient arrive; the second run finds one visit. The newer bill's item, listed before and
    worked by no one, carries the visit and the biller's Started and note; the exports' item is no longer listed."""
    from tests import ema_fixtures as F
    s, found = _rebill_world(tmp_path, kind, named=True, worked=True, two_runs=True)
    assert F.claim_id(31) not in found, sorted(found)
    y = found[F.claim_id(32)]
    assert y["status"] == "in_progress" and "no record of it" in (y["notes"] or ""), (y["status"], y["notes"])
    s.close()
