"""Third review, area: what people recorded. Invented data only (names, member numbers, claims, amounts)."""
from __future__ import annotations

import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from remitwatch.actions import apply_status  # noqa: E402
from remitwatch.detect import Detector  # noqa: E402
from remitwatch.x12_837 import DDL as DDL_837  # noqa: E402
from tests.test_features import NPI, build_store, claim, clean_visits, svc  # noqa: E402
from tests.test_resolution import reversal, visit  # noqa: E402

WHO = "ELM*BO****MI*W55500302"


def downcoding(store):
    return [dict(r) for r in store.query("""SELECT finding_id, signal, status, notes, hint, auto_note, recovered_cents, stale
                                            FROM findings WHERE category='downcoding' AND patient_control_number LIKE 'CB0000200%'
                                            ORDER BY stale, signal""")]


def _charge_downcode_paid_on_corrected_claim(tmp_path):
    """Claim CB0000200C017: a 99213 paid $76.15 with the practice's 99214 charge ($223.65): DOWNCODE_CHARGE. The corrected
    claim CB0000200C024 (same bill, same date) is paid as 99214 a month later, which closes the finding automatically."""
    claims = clean_visits(1, 25, "99214", "223.65", "117.60") + clean_visits(100, 25, "99213", "156.66", "76.15")
    claims.append(claim(200, svc("99213", "223.65", "76.15", "76.15", "CAS*CO*45*147.50~")))
    store = build_store(tmp_path, claims, pay_date="20260415")
    store.close()
    c024 = claim(200, svc("99214", "223.65", "117.60", "117.60", "CAS*CO*45*106.05~")).replace("CB0000200C017", "CB0000200C024")
    store = build_store(tmp_path, [c024], pay_date="20260520")
    # June: the payer recoups that payment by a provider-level offset under its own overpayment number (not a claim id),
    # which the ledger cannot tie to C024; its letter tells the biller. The C024 payment is gone; the remits still show it.
    from tests.test_remitwatch import remit
    p = tmp_path / "r9999.RMT"
    p.write_bytes(remit(claim(900, svc("99214", "223.65", "117.60", "117.60", "CAS*CO*45*106.05~")), "0.00", isa_ctrl="000000099", seg_count=99,
                        pay_date="20260610", plb="PLB*1777777772*20261231*WO:OVP-2026-0042*117.60~"))
    store.ingest_835_paths([str(p)])
    return store


def _claim_file_arrives(store):
    """The 837 for the original claim arrives and proves 99214 was billed: the line's signal becomes DOWNCODE_VS_BILLED."""
    store.conn.executescript(DDL_837)
    store.conn.execute("INSERT INTO lines_837 (line_837_key, claim_837_key, patient_control_number, line_seq, procedure_code, charge_cents, units, "
                       "service_date) VALUES ('k1','c1','CB0000200C017',1,'99214',22365,1,'2026-04-01')")
    store.conn.commit()


def test_a_reopen_from_the_page_is_lost_when_the_signal_changes(tmp_path):
    """The biller reopens the automatic closure with the page's 'open' button (no note), because the payer's letter says
    the C024 payment was recouped (an offset under the payer's own reference, which the remits cannot tie to the claim):
    the person's decision must stand. Then the claim file arrives and the line's signal changes; the new finding is
    closed again on the recouped payment, in the same run, and nothing open is left for the unpaid $41.45."""
    store = _charge_downcode_paid_on_corrected_claim(tmp_path)
    Detector(store, run_id="r1").run()
    [f] = downcoding(store)
    assert (f["signal"], f["status"]) == ("DOWNCODE_CHARGE", "resolved")
    apply_status(store.conn, f["finding_id"], "open", None, actor="biller")      # what the drawer's 'open' button sends
    store.conn.commit()
    Detector(store, run_id="r2").run()
    [f2] = downcoding(store)
    print("after r2:", f2)
    assert f2["status"] == "open" and f2["hint"].startswith("A later payment was found")      # control: the person's decision stands
    _claim_file_arrives(store)
    Detector(store, run_id="r3").run()
    rows = downcoding(store)
    print("after the claim file:", rows)
    assert [r["status"] for r in rows if not r["stale"]] == ["open"], \
        "the biller reopened the closure; once the claim file arrives the finding is closed again on the payment the biller rejected"
    store.close()


def test_a_reopen_with_a_note_survives_one_run_after_the_signal_changes_and_not_two(tmp_path):
    store = _charge_downcode_paid_on_corrected_claim(tmp_path)
    Detector(store, run_id="r1").run()
    [f] = downcoding(store)
    apply_status(store.conn, f["finding_id"], "in_progress", "C024 paid at the 99213 rate; calling the payer", actor="biller")
    store.conn.commit()
    _claim_file_arrives(store)
    Detector(store, run_id="r2").run()
    rows = downcoding(store)
    print("run after the claim file:", rows)
    assert [(r["signal"], r["status"]) for r in rows] == [("DOWNCODE_VS_BILLED", "in_progress")]      # inherited, kept
    Detector(store, run_id="r3").run()          # the next routine run, nothing new
    rows = downcoding(store)
    ev = store.query("SELECT finding_id, kind, from_status, to_status FROM finding_events ORDER BY id")
    print("next run:", rows)
    print("events:", [tuple(e) for e in ev])
    assert [(r["signal"], r["status"]) for r in rows] == [("DOWNCODE_VS_BILLED", "in_progress")], \
        "the biller's in-progress decision held for one run after the signal changed, then the next run closed it on the payment they rejected"
    store.close()


def test_a_recovery_the_biller_confirmed_stays_recovered_after_the_payer_takes_the_payment_back(tmp_path):
    """Bill 300's denial is closed automatically by bill 700's payment. The biller checks the EOB and records the amount
    (reopen, then 'recovered' with $50.00, the page's way). The payer then takes bill 700's payment back and denies it.
    Before this change the takeback reopened the finding; now it stays recovered with no word of the takeback."""
    store = build_store(tmp_path, clean_visits(1, 25, "99214", "223.65", "117.60") + [visit(300, WHO, paid="0.00")], pay_date="20260415")
    store.close()
    store = build_store(tmp_path, [visit(700, WHO)], pay_date="20260520")
    Detector(store, run_id="t1").run()
    [r] = store.query("SELECT finding_id, status FROM findings WHERE patient_control_number='CB0000300C017' AND category='denial'")
    assert r["status"] == "resolved"
    apply_status(store.conn, r["finding_id"], "open", None, actor="biller")
    apply_status(store.conn, r["finding_id"], "resolved", None, recovered_cents=5000, actor="biller")
    store.conn.commit()
    Detector(store, run_id="t2").run()
    store.close()
    store = build_store(tmp_path, [reversal(700, WHO), visit(700, WHO, paid="0.00", cas="CAS*CO*22*223.65~", status="4")], pay_date="20260610")
    Detector(store, run_id="t3").run()
    [r] = store.query("SELECT status, recovered_cents, hint, auto_note, evidence FROM findings WHERE patient_control_number='CB0000300C017' AND category='denial'")
    ev = json.loads(r["evidence"])
    print("after the takeback:", r["status"], r["recovered_cents"], r["hint"], r["auto_note"], sorted(ev))
    assert r["status"] != "resolved" or r["hint"], \
        "the only payment for this service was taken back; the finding still reads recovered $50.00, with nothing to say so"
    store.close()


def test_a_biller_who_undoes_a_mistaken_recovered_makes_the_finding_immune_to_closure(tmp_path):
    """The biller marks the wrong finding recovered and puts it back to open a minute later. The analysis never closed it.
    When the service is later paid on another claim, the finding is not closed: the person 'moved it away from resolved'
    after the analysis last closed it (never), by the rule's COALESCE."""
    store = build_store(tmp_path, clean_visits(1, 25, "99214", "223.65", "117.60") + [visit(300, WHO, paid="0.00")], pay_date="20260415")
    Detector(store, run_id="t1").run()
    [r] = store.query("SELECT finding_id FROM findings WHERE patient_control_number='CB0000300C017' AND category='denial'")
    apply_status(store.conn, r["finding_id"], "resolved", None, actor="biller", at="2026-04-20T15:00:00+00:00")
    apply_status(store.conn, r["finding_id"], "open", None, actor="biller", at="2026-04-20T15:01:00+00:00")
    store.conn.commit()
    store.close()
    store = build_store(tmp_path, [visit(700, WHO)], pay_date="20260520")
    Detector(store, run_id="t2").run()
    [r] = store.query("SELECT status, hint FROM findings WHERE patient_control_number='CB0000300C017' AND category='denial'")
    print("after the payment:", r["status"], r["hint"])
    assert r["status"] == "resolved", "a finding the analysis never closed stays open with a hint after it is paid, because of an undone click"
    store.close()


def test_a_status_set_on_the_dashboards_older_copy_is_replayed_as_an_override(tmp_path):
    """Serverless: a cycle's analysis closes bill 300's denial (payment found) at T. Before the dashboard's copy is
    refreshed (it is saved after detection, activity and the uploads), the biller, still seeing it open, marks it
    appealed at T + 20 s. The next cycle replays the journal entry onto the store, where the finding is resolved: it is
    recorded as a person moving it away from 'resolved' after the closure, and the paid finding stays appealed."""
    from datetime import datetime, timedelta
    from remitwatch.cloud import apply_actions
    store = build_store(tmp_path, clean_visits(1, 25, "99214", "223.65", "117.60") + [visit(300, WHO, paid="0.00")], pay_date="20260415")
    Detector(store, run_id="t1").run()
    [r] = store.query("SELECT finding_id, status FROM findings WHERE patient_control_number='CB0000300C017' AND category='denial'")
    store.close()
    store = build_store(tmp_path, [visit(700, WHO)], pay_date="20260520")
    Detector(store, run_id="t2").run()            # the cycle that closes it
    [(closed_at,)] = store.query("SELECT at FROM finding_events WHERE finding_id=? AND kind='paid_on_later_remit'", (r["finding_id"],))
    at = (datetime.fromisoformat(closed_at) + timedelta(seconds=20)).isoformat(timespec="seconds")
    # the dashboard records the status the person was looking at (remitwatch.api._record_status)
    entry = {"kind": "status", "finding_id": r["finding_id"], "status": "appealed", "notes": "Appeal faxed", "actor": "biller", "at": at, "seen": "open"}
    print(apply_actions(store.conn, [("actions/x.json", entry)]))      # the next cycle folds the journal in first
    ev = store.query("SELECT kind, from_status, to_status, at FROM finding_events WHERE finding_id=? ORDER BY id", (r["finding_id"],))
    print([tuple(e) for e in ev])
    Detector(store, run_id="t3").run()
    [r] = store.query("SELECT status, hint FROM findings WHERE patient_control_number='CB0000300C017' AND category='denial'")
    print("after the next cycle:", r["status"], r["hint"])
    assert r["status"] == "resolved", "a status set on a copy that still showed the finding open is treated as overriding a closure the biller never saw"
    store.close()


def test_an_automatic_closure_counts_as_someone_working_a_whole_claim_denial(tmp_path):
    """Bill 300 (C017) is denied whole in April; the service is paid on bill 700 in May and the analysis run of May 21
    closes the denial. The practice resubmits bill 300 (C024), denied again in June. prev_claims treats the automatic
    closure as 'worked' and its time as when 'someone' last acted, so C024 is listed on its own, open, for a service paid
    in May (a hint only), instead of being one more denied submission of the bill the closed finding carries."""
    store = build_store(tmp_path, clean_visits(1, 25, "99214", "223.65", "117.60")
                        + [visit(300, WHO, paid="0.00", cas="CAS*CO*16*223.65~", status="4")], pay_date="20260415")
    store.close()
    store = build_store(tmp_path, [visit(700, WHO)], pay_date="20260520")
    Detector(store, run_id="t1").run()
    [r] = store.query("SELECT finding_id, status FROM findings WHERE signal='CLAIM_DENIED_UNPAID'")
    assert r["status"] == "resolved"
    store.conn.execute("UPDATE findings SET status_changed_at='2026-05-21T10:00:00+00:00' WHERE finding_id=?", (r["finding_id"],))   # that run was on May 21
    store.conn.commit()
    store.close()
    store = build_store(tmp_path, [visit(300, WHO, paid="0.00", cas="CAS*CO*50*223.65~", status="4").replace("C017*4*", "C024*4*")], pay_date="20260610")
    Detector(store, run_id="t2").run()
    rows = [tuple(x) for x in store.query("SELECT patient_control_number, status, hint FROM findings WHERE signal='CLAIM_DENIED_UNPAID' ORDER BY 1")]
    print(rows)
    assert [x for x in rows if x[1] == "open"] == [], "a resubmission of a bill whose service was paid is listed as an open denial"
    store.close()
