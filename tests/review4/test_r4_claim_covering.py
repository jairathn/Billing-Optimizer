"""Whole-claim denials someone worked and the bill has since paid ('covering' in run_claim_rules). Invented data only."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from r4h import add, apply_status, base, brief, clm, events, rows, run, sv, visit_paid  # noqa: E402


def denied(pcn, carc="16"):
    return clm(pcn, "4", "223.65", "0.00", sv("99214", "223.65", "0.00", 1, f"CAS*CO*{carc}*223.65~"))


def claim_rows(store):
    return rows(store, "signal='CLAIM_DENIED_UNPAID'")


def test_a_whole_claim_denial_set_in_progress_and_then_paid_is_kept_as_recovered_on_the_next_run(tmp_path):
    """C017 is denied whole (CO-16) on 2026-04-15. The biller presses 'in progress' on the page (no note) on 2026-04-20.
    The corrected C024 is paid $117.60 on 2026-06-01: the run of 2026-06-02 closes the finding as recovered. The run of
    2026-06-09 has nothing new. The recovered finding must still be there."""
    base(tmp_path).close()
    store = add(tmp_path, [denied("CB0000300C017")], "20260415")
    run(store, "t1", "2026-04-16T09:00:00+00:00")
    [f] = claim_rows(store)
    apply_status(store.conn, f["finding_id"], "in_progress", None, actor="biller", at="2026-04-20T15:00:00+00:00")
    store.conn.commit()
    store.close()
    store = add(tmp_path, [visit_paid("CB0000300C024")], "20260601")
    run(store, "t2", "2026-06-02T09:00:00+00:00")
    after_payment = claim_rows(store)
    brief(after_payment)
    assert [(r["finding_id"], r["status"]) for r in after_payment] == [(f["finding_id"], "resolved")], "precondition: closed by C024's payment"
    assert after_payment[0]["recovered_cents"] == 11760
    run(store, "t3", "2026-06-09T09:00:00+00:00")       # the next routine run: nothing new
    later = claim_rows(store)
    brief(later)
    print("events:", events(store, f["finding_id"]))
    print("progress snapshots:", [tuple(r) for r in store.query("SELECT run_id, category, status, n, cents FROM progress_snapshots WHERE category='denial' ORDER BY run_id")])
    assert [(r["finding_id"], r["status"], r["recovered_cents"]) for r in later] == [(f["finding_id"], "resolved", 11760)], \
        "the recovered whole-claim denial the biller had worked vanished on the next run (row deleted, recovery gone from every total)"
    store.close()


def test_control_the_same_with_a_note_stays(tmp_path):
    """The same with a note on the in-progress status: notes keep it 'worked', so the closure is kept."""
    base(tmp_path).close()
    store = add(tmp_path, [denied("CB0000300C017")], "20260415")
    run(store, "t1", "2026-04-16T09:00:00+00:00")
    [f] = claim_rows(store)
    apply_status(store.conn, f["finding_id"], "in_progress", "Called Anthem", actor="biller", at="2026-04-20T15:00:00+00:00")
    store.conn.commit()
    store.close()
    store = add(tmp_path, [visit_paid("CB0000300C024")], "20260601")
    run(store, "t2", "2026-06-02T09:00:00+00:00")
    run(store, "t3", "2026-06-09T09:00:00+00:00")
    later = claim_rows(store)
    brief(later)
    assert [(r["finding_id"], r["status"], r["recovered_cents"]) for r in later] == [(f["finding_id"], "resolved", 11760)]
    store.close()


def test_the_row_the_biller_worked_still_exists_after_two_runs(tmp_path):
    """Weaker form, to compare with the live release (RW_LIVE=1): whatever its status, the row the biller set in progress
    must still be in the findings table two runs after the payment. Live keeps it (stale, in progress); the release deletes it."""
    base(tmp_path).close()
    store = add(tmp_path, [denied("CB0000300C017")], "20260415")
    run(store, "t1", "2026-04-16T09:00:00+00:00")
    [f] = claim_rows(store)
    apply_status(store.conn, f["finding_id"], "in_progress", None, actor="biller", at="2026-04-20T15:00:00+00:00")
    store.conn.commit()
    store.close()
    store = add(tmp_path, [visit_paid("CB0000300C024")], "20260601")
    run(store, "t2", "2026-06-02T09:00:00+00:00")
    run(store, "t3", "2026-06-09T09:00:00+00:00")
    later = claim_rows(store)
    brief(later)
    assert [r["finding_id"] for r in later] == [f["finding_id"]], "the finding the biller worked is gone from the findings table"
    store.close()


def test_repeat_denials_after_the_worked_carrier_was_paid_and_taken_back_are_one_finding(tmp_path):
    """C017 is denied whole (2026-04-15) and appealed (2026-05-01, with a note). The corrected C024 is paid (2026-06-01) and
    the appeal closes as recovered. On 2026-07-01 Anthem reverses C024 and denies it; the practice resubmits as C031,
    denied on 2026-08-01. One visit is unpaid: the appeal reopens (appealed), and the repeat denials are new work, once."""
    from r4h import visit_reversed
    base(tmp_path).close()
    store = add(tmp_path, [denied("CB0000300C017")], "20260415")
    run(store, "t1", "2026-04-16T09:00:00+00:00")
    [f] = claim_rows(store)
    apply_status(store.conn, f["finding_id"], "appealed", "Appeal faxed", actor="biller", at="2026-05-01T15:00:00+00:00")
    store.conn.commit()
    store.close()
    store = add(tmp_path, [visit_paid("CB0000300C024")], "20260601")
    run(store, "t2", "2026-06-02T09:00:00+00:00")
    assert [r["status"] for r in claim_rows(store)] == ["resolved"]
    store.close()
    add(tmp_path, [visit_reversed("CB0000300C024"), denied("CB0000300C024", "50")], "20260701").close()
    store = add(tmp_path, [denied("CB0000300C031", "50")], "20260801")
    run(store, "t3", "2026-08-02T09:00:00+00:00")
    rs = [r for r in claim_rows(store) if not r["stale"]]
    brief(rs)
    assert [r["status"] for r in rs if r["finding_id"] == f["finding_id"]] == ["appealed"]
    new_open = [r for r in rs if r["status"] == "open"]
    assert len(new_open) <= 1, f"one unpaid visit listed as {len(new_open)} new open denials (${sum(r['shortfall_cents'] or 0 for r in new_open)/100:.2f}) besides the appeal"
    store.close()
