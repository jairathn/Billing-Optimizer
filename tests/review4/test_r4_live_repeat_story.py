"""The repeat-denial story of test_claim_covering.py without preconditions, to print what each release lists."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from r4h import add, apply_status, base, brief, clm, rows, run, sv, visit_paid, visit_reversed  # noqa: E402


def denied(pcn, carc="16"):
    return clm(pcn, "4", "223.65", "0.00", sv("99214", "223.65", "0.00", 1, f"CAS*CO*{carc}*223.65~"))


def test_print_the_repeat_denial_story(tmp_path):
    base(tmp_path).close()
    store = add(tmp_path, [denied("CB0000300C017")], "20260415")
    run(store, "t1", "2026-04-16T09:00:00+00:00")
    [f] = rows(store, "signal='CLAIM_DENIED_UNPAID'")
    apply_status(store.conn, f["finding_id"], "appealed", "Appeal faxed", actor="biller", at="2026-05-01T15:00:00+00:00")
    store.conn.commit()
    store.close()
    store = add(tmp_path, [visit_paid("CB0000300C024")], "20260601")
    run(store, "t2", "2026-06-02T09:00:00+00:00")
    store.close()
    add(tmp_path, [visit_reversed("CB0000300C024"), denied("CB0000300C024", "50")], "20260701").close()
    store = add(tmp_path, [denied("CB0000300C031", "50")], "20260801")
    run(store, "t3", "2026-08-02T09:00:00+00:00")
    brief(rows(store, "signal='CLAIM_DENIED_UNPAID'"))
    store.close()
