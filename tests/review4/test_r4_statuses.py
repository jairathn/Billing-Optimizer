"""save(): overridden and confirmed findings, carried through signal changes and takebacks. Invented data only."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from r4h import NPI, add, apply_status, base, brief, clm, events, rows, run, sv, visit_denied_line, visit_paid, visit_reversed  # noqa: E402

WHO = "ELM*BO****MI*W55500302"


def line_row(store, pcn="CB0000300C017"):
    rs = rows(store, f"patient_control_number='{pcn}' AND category='denial' AND COALESCE(stale,0)=0")
    assert len(rs) == 1, rs
    return rs[0]


def test_a_confirmed_recovery_reopened_by_a_takeback_closes_again_when_the_service_is_paid_again(tmp_path):
    """C017's visit is denied (missing information) on 2026-04-15. Bill 700 (same patient, date and code) is paid on
    2026-05-20 and the run of 2026-05-21 closes the denial. The biller checks the EOB and records the amount the page's way
    on 2026-05-22: 'open', then 'recovered' $117.60. On 2026-06-10 the payer takes bill 700's payment back; the run of
    2026-06-11 reopens the finding with 'The payment this was recovered on was taken back'. On 2026-07-15 the payer pays
    bill 700 again ($117.60, after the practice's appeal). The visit is paid: the finding should close again."""
    base(tmp_path).close()
    store = add(tmp_path, [visit_denied_line("CB0000300C017", who=WHO)], "20260415")
    run(store, "t1", "2026-04-16T09:00:00+00:00")
    store.close()
    store = add(tmp_path, [visit_paid("CB0000700C017", who=WHO, payer_ctl="PC700")], "20260520")
    run(store, "t2", "2026-05-21T09:00:00+00:00")
    f = line_row(store)
    assert f["status"] == "resolved", "precondition: closed by bill 700's payment"
    apply_status(store.conn, f["finding_id"], "open", None, actor="biller", at="2026-05-22T15:00:00+00:00")
    apply_status(store.conn, f["finding_id"], "resolved", None, recovered_cents=11760, actor="biller", at="2026-05-22T15:01:00+00:00")
    store.conn.commit()
    store.close()
    store = add(tmp_path, [visit_reversed("CB0000700C017", who=WHO, payer_ctl="PC700")], "20260610")
    run(store, "t3", "2026-06-11T09:00:00+00:00")
    f = line_row(store)
    brief([f])
    assert f["status"] == "open" and "taken back" in (f["hint"] or ""), "precondition: reopened by the takeback"
    store.close()
    store = add(tmp_path, [visit_paid("CB0000700C017", who=WHO, payer_ctl="PC700")], "20260715")
    run(store, "t4", "2026-07-16T09:00:00+00:00")
    f = line_row(store)
    brief([f])
    print("events:", events(store, f["finding_id"]))
    assert f["status"] == "resolved", \
        f"the service is paid again but the finding stays {f['status']} with the hint {f['hint']!r}: nobody set it open, the analysis did"
    store.close()


def test_control_without_the_confirmation_the_same_story_closes_again(tmp_path):
    """The same story with nobody touching the finding: closed, reopened by the takeback, closed again."""
    base(tmp_path).close()
    store = add(tmp_path, [visit_denied_line("CB0000300C017", who=WHO)], "20260415")
    run(store, "t1", "2026-04-16T09:00:00+00:00")
    store.close()
    store = add(tmp_path, [visit_paid("CB0000700C017", who=WHO, payer_ctl="PC700")], "20260520")
    run(store, "t2", "2026-05-21T09:00:00+00:00")
    store.close()
    store = add(tmp_path, [visit_reversed("CB0000700C017", who=WHO, payer_ctl="PC700")], "20260610")
    run(store, "t3", "2026-06-11T09:00:00+00:00")
    assert line_row(store)["status"] == "open"
    store.close()
    store = add(tmp_path, [visit_paid("CB0000700C017", who=WHO, payer_ctl="PC700")], "20260715")
    run(store, "t4", "2026-07-16T09:00:00+00:00")
    f = line_row(store)
    brief([f])
    assert f["status"] == "resolved"
    store.close()


def test_same_bill_a_confirmed_recovery_taken_back_and_paid_again_closes(tmp_path):
    """The same story on one bill, so the live release (RW_LIVE=1) can play it too: C017 denied (2026-04-15), the corrected
    C024 paid (2026-05-20) closes it, the biller records the recovery the page's way (open, then recovered), the payer
    reverses C024 (2026-06-10), and the next corrected claim C031 is paid (2026-07-15). The visit is paid."""
    base(tmp_path).close()
    store = add(tmp_path, [visit_denied_line("CB0000300C017", who=WHO)], "20260415")
    run(store, "t1", "2026-04-16T09:00:00+00:00")
    store.close()
    store = add(tmp_path, [visit_paid("CB0000300C024", who=WHO)], "20260520")
    run(store, "t2", "2026-05-21T09:00:00+00:00")
    f = line_row(store)
    assert f["status"] == "resolved", "precondition: closed by C024's payment"
    apply_status(store.conn, f["finding_id"], "open", None, actor="biller", at="2026-05-22T15:00:00+00:00")
    apply_status(store.conn, f["finding_id"], "resolved", None, recovered_cents=11760, actor="biller", at="2026-05-22T15:01:00+00:00")
    store.conn.commit()
    store.close()
    store = add(tmp_path, [visit_reversed("CB0000300C024", who=WHO)], "20260610")
    run(store, "t3", "2026-06-11T09:00:00+00:00")
    brief([line_row(store)])
    store.close()
    store = add(tmp_path, [visit_paid("CB0000300C031", who=WHO, payer_ctl="PC31")], "20260715")
    run(store, "t4", "2026-07-16T09:00:00+00:00")
    f = line_row(store)
    brief([f])
    assert f["status"] == "resolved", f"paid again on C031 but {f['status']}: {f['hint']!r}"
    store.close()


def test_a_recovery_recorded_after_the_takeback_is_not_reopened(tmp_path):
    """C017's visit is denied (2026-04-15); the corrected C024 is paid (2026-05-20) and the run of 2026-05-21 closes the
    denial. The payer reverses C024 (2026-06-10) and the run of 2026-06-11 reopens the finding, as it should. The biller
    works it and the payer settles it by a paper check: on 2026-06-20 the biller marks it recovered, $117.60. The run of
    2026-06-21 has nothing new. The biller's recovery was recorded after the takeback; it rests on nothing the analysis saw."""
    base(tmp_path).close()
    store = add(tmp_path, [visit_denied_line("CB0000300C017", who=WHO)], "20260415")
    run(store, "t1", "2026-04-16T09:00:00+00:00")
    store.close()
    store = add(tmp_path, [visit_paid("CB0000300C024", who=WHO)], "20260520")
    run(store, "t2", "2026-05-21T09:00:00+00:00")
    assert line_row(store)["status"] == "resolved", "precondition: closed by C024's payment"
    store.close()
    store = add(tmp_path, [visit_reversed("CB0000300C024", who=WHO)], "20260610")
    run(store, "t3", "2026-06-11T09:00:00+00:00")
    f = line_row(store)
    assert f["status"] == "open", "precondition: reopened by the takeback"
    apply_status(store.conn, f["finding_id"], "resolved", "Paper check 4471 from Anthem", recovered_cents=11760, actor="biller",
                 at="2026-06-20T15:00:00+00:00")
    store.conn.commit()
    run(store, "t4", "2026-06-21T09:00:00+00:00")
    f = line_row(store)
    brief([f])
    print("events:", events(store, f["finding_id"]))
    if f["status"] != "resolved":
        # the biller records it again: the run after reopens it again, every time
        apply_status(store.conn, f["finding_id"], "resolved", "Paper check 4471 from Anthem (again)", recovered_cents=11760, actor="biller",
                     at="2026-06-22T15:00:00+00:00")
        store.conn.commit()
        run(store, "t5", "2026-06-23T09:00:00+00:00")
        print("after recording it a second time:", line_row(store)["status"], line_row(store)["hint"])
    assert (f["status"], f["recovered_cents"]) == ("resolved", 11760), \
        f"the biller's recovery, recorded after the takeback, was reopened by the next run: {f['status']} / {f['hint']!r}"
    store.close()


def test_a_recovery_recorded_after_a_superseded_payment_is_not_reopened(tmp_path):
    """The same with a takeback the live release also understands: the remittance that paid C024 is delivered again with
    C024 denied (same trace number, newer file), the run of 2026-06-11 reopens the finding, and on 2026-06-20 the biller
    marks it recovered from a paper check. The next run must leave the biller's recovery alone."""
    import time
    from tests.test_remitwatch import remit
    from remitwatch.store import Store
    base(tmp_path).close()
    store = add(tmp_path, [visit_denied_line("CB0000300C017", who=WHO)], "20260415")
    run(store, "t1", "2026-04-16T09:00:00+00:00")
    store.close()
    n = len(list(tmp_path.glob("*.RMT")))
    store = add(tmp_path, [visit_paid("CB0000300C024", who=WHO)], "20260520")
    run(store, "t2", "2026-05-21T09:00:00+00:00")
    assert line_row(store)["status"] == "resolved", "precondition: closed by C024's payment"
    store.close()
    again = tmp_path / "zz_redelivered.RMT"
    again.write_bytes(remit(visit_denied_line("CB0000300C024", who=WHO), "0.00", seg_count=99, isa_ctrl=f"{n + 1:09d}", pay_date="20260520"))
    os.utime(again, (time.time() + 3600, time.time() + 3600))
    store = Store(str(tmp_path / "s.sqlite"))
    store.ingest_835_paths([str(again)])
    print("superseded:", store.mark_superseded())
    run(store, "t3", "2026-06-11T09:00:00+00:00")
    f = line_row(store)
    brief([f])
    assert f["status"] == "open", "precondition: reopened once the payment no longer stands"
    apply_status(store.conn, f["finding_id"], "resolved", "Paper check 4471 from Anthem", recovered_cents=11760, actor="biller",
                 at="2026-06-20T15:00:00+00:00")
    store.conn.commit()
    run(store, "t4", "2026-06-21T09:00:00+00:00")
    f = line_row(store)
    brief([f])
    assert (f["status"], f["recovered_cents"]) == ("resolved", 11760), \
        f"the biller's recovery, recorded after the takeback, was reopened by the next run: {f['status']} / {f['hint']!r}"
    store.close()


def test_a_reopen_survives_two_signal_changes(tmp_path):
    """tests/review3/test_r3_people.py's story (a reopen from the page, then the claim file arrives), then the claim file's
    line is withdrawn so the signal changes back. The biller's decision must hold through both changes and a later run."""
    from tests.review3.test_r3_people import _charge_downcode_paid_on_corrected_claim, _claim_file_arrives, downcoding
    store = _charge_downcode_paid_on_corrected_claim(tmp_path)
    run(store, "r1")
    [f] = downcoding(store)
    assert (f["signal"], f["status"]) == ("DOWNCODE_CHARGE", "resolved")
    apply_status(store.conn, f["finding_id"], "open", None, actor="biller")
    store.conn.commit()
    _claim_file_arrives(store)
    run(store, "r2")
    print("after the claim file:", downcoding(store))
    store.conn.execute("DELETE FROM lines_837")
    store.conn.commit()
    run(store, "r3")
    run(store, "r4")
    rows_ = downcoding(store)
    print("after the claim file is withdrawn:", rows_)
    assert [(r["signal"], r["status"]) for r in rows_ if not r["stale"]] == [("DOWNCODE_CHARGE", "open")], rows_
    store.close()
