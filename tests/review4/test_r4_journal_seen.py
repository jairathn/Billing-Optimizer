"""A journal entry carrying 'seen', replayed twice (two cycles fold the same entry in). Invented data only."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from r4h import WHO, add, base, events, rows, run, visit_denied_line, visit_paid  # noqa: E402


def test_a_seen_entry_replayed_twice_writes_once_and_does_not_undo_a_later_closure(tmp_path):
    from remitwatch.cloud import apply_actions
    base(tmp_path).close()
    store = add(tmp_path, [visit_denied_line("CB0000300C017", who=WHO)], "20260415")
    run(store, "t1", "2026-04-16T09:00:00+00:00")
    [f] = rows(store, "category='denial'")
    entry = {"kind": "status", "finding_id": f["finding_id"], "status": "appealed", "notes": "Appeal faxed", "actor": "biller",
             "at": "2026-04-20T15:00:00+00:00", "seen": "open"}
    print(apply_actions(store.conn, [("actions/a.json", entry)]))
    store.close()
    store = add(tmp_path, [visit_paid("CB0000300C024", who=WHO)], "20260520")
    run(store, "t2", "2026-05-21T09:00:00+00:00")
    print(apply_actions(store.conn, [("actions/a.json", entry)]))        # the same entry again, next cycle
    run(store, "t3", "2026-05-28T09:00:00+00:00")
    [f] = rows(store, "category='denial'")
    ev = events(store, f["finding_id"])
    print(ev)
    assert sum(1 for e in ev if e[0] == "manual") == 1
    assert f["status"] == "resolved"
    store.close()
