"""One finding per bill for whole-claim denials: which submission carries it, and what happens to work people recorded.
Invented data only."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from r3helpers import WHO, add, base, clm, findings, show, sv, visit_paid, visit_reversed  # noqa: E402

from remitwatch.actions import apply_status  # noqa: E402
from remitwatch.detect import Detector  # noqa: E402


def denied(pcn, carc="16"):
    return clm(pcn, "4", "223.65", "0.00", sv("99214", "223.65", "0.00", 1, f"CAS*CO*{carc}*223.65~"))


def claim_rows(store):
    return findings(store, "signal='CLAIM_DENIED_UNPAID'")


def run(store, run_id, now=None):
    d = Detector(store, run_id=run_id)
    if now:
        d.now = now
    d.run()
    return d


def test_an_appeal_on_the_first_submission_is_not_hidden_when_the_resubmission_is_worked(tmp_path):
    """C017 is denied (2026-04-15); the biller appeals it on 2026-05-01. The resubmission C024 is denied on 2026-05-20, after
    the appeal, so it is listed on its own; the biller works it too (in progress, 2026-06-01). On the next run the appeal on
    C017 must still be there, appealed, counted in the totals (not stale)."""
    base(tmp_path).close()
    store = add(tmp_path, [denied("CB0000300C017")], "20260415")
    run(store, "t1", "2026-04-16T09:00:00+00:00")
    [f1] = claim_rows(store)
    apply_status(store.conn, f1["finding_id"], "appealed", "Appeal faxed with notes", actor="biller", at="2026-05-01T15:00:00+00:00")
    store.conn.commit()
    store.close()
    store = add(tmp_path, [denied("CB0000300C024", "50")], "20260520")
    run(store, "t2", "2026-05-21T09:00:00+00:00")
    rows = {r["patient_control_number"]: r for r in claim_rows(store)}
    show(rows.values())
    assert set(rows) == {"CB0000300C017", "CB0000300C024"}, "precondition: the denial after the appeal is listed on its own"
    apply_status(store.conn, rows["CB0000300C024"]["finding_id"], "in_progress", "Called Anthem; records requested", actor="biller",
                 at="2026-06-01T15:00:00+00:00")
    store.conn.commit()
    run(store, "t3", "2026-06-02T09:00:00+00:00")
    rows = {r["patient_control_number"]: r for r in claim_rows(store)}
    show(rows.values())
    a = rows["CB0000300C017"]
    assert a["status"] == "appealed" and not a["stale"], "the appealed denial went stale: out of every total and every work list"
    store.close()


def test_an_automatic_closure_is_not_a_persons_decision(tmp_path):
    """C017 is denied whole (2026-04-15). Another bill for the same patient, date and code (bill 700) is paid on 2026-05-20
    and the analysis of 2026-05-21 closes C017's finding. On 2026-06-10 the payer takes bill 700's payment back, and the
    resubmission C024 of bill 300 is denied on 2026-06-20. Nobody has touched either finding: one unpaid visit, one open
    finding."""
    base(tmp_path).close()
    store = add(tmp_path, [denied("CB0000300C017")], "20260415")
    run(store, "t1", "2026-04-16T09:00:00+00:00")
    store.close()
    store = add(tmp_path, [visit_paid("CB0000700C017", payer_ctl="PC700")], "20260520")
    run(store, "t2", "2026-05-21T09:00:00+00:00")
    show(claim_rows(store))
    assert [r["status"] for r in claim_rows(store)] == ["resolved"], "precondition: closed by bill 700's payment"
    store.close()
    add(tmp_path, [visit_reversed("CB0000700C017", payer_ctl="PC700"), denied("CB0000700C017", "22")], "20260610").close()
    store = add(tmp_path, [denied("CB0000300C024", "50")], "20260620")
    run(store, "t3", "2026-06-21T09:00:00+00:00")
    rows = [r for r in claim_rows(store) if r["patient_control_number"].startswith("CB0000300") and r["status"] == "open" and not r["stale"]]
    show(rows)
    assert len(rows) == 1, f"one unpaid visit listed {len(rows)} times (${sum(r['shortfall_cents'] or 0 for r in rows)/100:.2f})"
    store.close()


def test_an_appealed_whole_claim_denial_paid_on_the_same_bill_is_recorded_as_recovered(tmp_path):
    """C017 is denied whole and appealed. The appeal is won: the payer pays C024 (same bill) on 2026-06-15. Line findings
    close as 'resolved' with the amount recovered; this one must not vanish into a stale 'appealed' row."""
    base(tmp_path).close()
    store = add(tmp_path, [denied("CB0000300C017")], "20260415")
    run(store, "t1", "2026-04-16T09:00:00+00:00")
    [f1] = claim_rows(store)
    apply_status(store.conn, f1["finding_id"], "appealed", "Appeal faxed", actor="biller", at="2026-05-01T15:00:00+00:00")
    store.conn.commit()
    store.close()
    store = add(tmp_path, [visit_paid("CB0000300C024")], "20260615")
    run(store, "t2", "2026-06-16T09:00:00+00:00")
    [r] = [x for x in claim_rows(store) if x["finding_id"] == f1["finding_id"]]
    show([r])
    assert r["status"] == "resolved" and not r["stale"], f"won appeal recorded as status={r['status']} stale={r['stale']} recovered={r['recovered_cents']}"
    store.close()
