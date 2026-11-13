"""The rule: open, in-progress and appealed findings close automatically on a later payment, unless a person moved the
finding away from 'resolved' at or after the analysis last closed it. Invented data only."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from r3helpers import add, base, by_key, show, visit_denied_line, visit_paid  # noqa: E402

from remitwatch.actions import apply_status  # noqa: E402
from remitwatch.detect import Detector  # noqa: E402


def test_a_slip_undone_before_any_automatic_closure_does_not_block_closures_for_ever(tmp_path):
    """The biller marks C017's denial resolved by mistake and sets it back to open a minute later. The analysis never
    closed it. When the corrected claim C024 is paid in May, the finding should close like any other open finding."""
    base(tmp_path).close()
    store = add(tmp_path, [visit_denied_line("CB0000300C017")], "20260415")
    Detector(store, run_id="t1").run()
    f = by_key(store)["CB0000300C017:99214:DENIED_MISSING_INFO"]
    apply_status(store.conn, f["finding_id"], "resolved", None, actor="biller", at="2026-04-20T15:00:00+00:00")
    apply_status(store.conn, f["finding_id"], "open", "Clicked the wrong row", actor="biller", at="2026-04-20T15:01:00+00:00")
    store.conn.commit()
    store.close()
    store = add(tmp_path, [visit_paid("CB0000300C024")], "20260520")
    Detector(store, run_id="t2").run()
    r = by_key(store)["CB0000300C017:99214:DENIED_MISSING_INFO"]
    show([r])
    assert r["status"] == "resolved", f"kept open: {r['hint']}"
    store.close()
