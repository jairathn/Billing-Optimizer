"""
Review of 418bad3: payments the payer took back, and closures from another claim. Invented data only (names, member
numbers, claim ids and amounts are made up). Each test states what the billers should see; a failing assertion is the finding.
"""
from __future__ import annotations

import json
import sys

import os  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch.actions import apply_status  # noqa: E402
from remitwatch.detect import Detector  # noqa: E402
from tests.test_features import NPI, build_store, clean_visits, svc  # noqa: E402
from tests.test_resolution import reversal, visit  # noqa: E402

WHO = "ELM*BO****MI*W55500302"


def base_visits():
    return clean_visits(1, 25, "99214", "223.65", "117.60")


def denials(store):
    """Denial findings by claim id, code and signal (SELECT *: an older copy has no hint column)."""
    return {f"{r['patient_control_number']}:{r['procedure_code'] or ''}:{r['signal']}": dict(r, hint=dict(r).get("hint"))
            for r in store.query("SELECT * FROM findings WHERE category='denial'")}


# --------------------------------------------------------------------------- a secondary's payment on the same claim id
def test_a_secondarys_share_does_not_keep_a_reversed_primary_payment_alive(tmp_path):
    """Bill 300 is denied. Bill 700 (same patient, date, code) is paid by the primary, the secondary pays its 20 percent
    on the same claim id (a crossover keeps CLP01), then the primary takes its whole payment back and denies bill 700.
    Only the secondary's $23.52 is left: bill 300's denial must stay open."""
    store = build_store(tmp_path, base_visits() + [visit(300, WHO, paid="0.00")], pay_date="20260415")
    store.close()
    store = build_store(tmp_path, [visit(700, WHO)], pay_date="20260520")
    store.close()
    store = build_store(tmp_path, [visit(700, WHO, paid="23.52", allowed="117.60", status="2")], pay_date="20260530", payer="HUMANA")
    store.close()
    store = build_store(tmp_path, [reversal(700, WHO), visit(700, WHO, paid="0.00", cas="CAS*CO*22*223.65~", status="4")], pay_date="20260610")
    Detector(store, run_id="t").run()
    r = denials(store)["CB0000300C017:99214:DENIED_MISSING_INFO"]
    print("bill 300:", r["status"], r["auto_note"], r["recovered_cents"])
    assert r["status"] == "open", f"closed by the primary payment the payer took back: {r['auto_note']}"
    store.close()


def test_the_primarys_takeback_is_listed_when_a_secondary_paid_the_same_claim_id(tmp_path):
    """The same claim id paid by the primary and by the secondary (crossover), then the primary takes its payment back
    and denies the claim. The primary's money is gone: the whole-claim denial must be listed."""
    store = build_store(tmp_path, base_visits() + [visit(300, WHO)], pay_date="20250901")
    store.close()
    store = build_store(tmp_path, [visit(300, WHO, paid="23.52", allowed="117.60", status="2")], pay_date="20250920", payer="HUMANA")
    store.close()
    store = build_store(tmp_path, [reversal(300, WHO), visit(300, WHO, paid="0.00", cas="CAS*CO*22*223.65~", status="4")], pay_date="20260410")
    Detector(store, run_id="t").run()
    listed = [r["patient_control_number"] for r in store.query("SELECT patient_control_number FROM findings WHERE signal='CLAIM_DENIED_UNPAID'")]
    print("CLAIM_DENIED_UNPAID listed for:", listed)
    assert listed == ["CB0000300C017"], "the primary's takeback is hidden because the secondary paid the same claim id"
    store.close()


# --------------------------------------------------------------------------- a reversal under another suffix of the bill
def test_a_reversal_written_under_the_corrected_claims_suffix(tmp_path):
    """Bill 700 C017 is paid; the practice sends a corrected claim C024 and the payer writes the reversal of the C017
    payment under C024 (the claim that triggered it) and denies C024. Nothing is paid on bill 700 any more."""
    store = build_store(tmp_path, base_visits() + [visit(300, WHO, paid="0.00")], pay_date="20260415")
    store.close()
    store = build_store(tmp_path, [visit(700, WHO)], pay_date="20260520")
    store.close()
    redo = visit(700, WHO, paid="0.00", cas="CAS*CO*16*223.65~", status="4").replace("C017*4*", "C024*4*")
    store = build_store(tmp_path, [reversal(700, WHO, "C024"), redo], pay_date="20260610")
    Detector(store, run_id="t").run()
    rows = denials(store)
    r = rows["CB0000300C017:99214:DENIED_MISSING_INFO"]
    print("bill 300:", r["status"], r["auto_note"])
    print("listed:", sorted(rows))
    assert r["status"] == "open", f"closed by a payment that was taken back under another suffix: {r['auto_note']}"
    assert "CB0000700C024::CLAIM_DENIED_UNPAID" in rows, "bill 700's denial is hidden by the payment that was taken back"
    store.close()


def test_a_partial_repayment_records_the_reversed_amount_as_recovered(tmp_path):
    """Bill 700 is paid $117.60, the payer takes it back and pays it again at $40.00 (re-adjudicated). Bill 300's denial
    closes (fine: the service was paid), but it must not record $117.60 recovered from a payment that no longer stands."""
    store = build_store(tmp_path, base_visits() + [visit(300, WHO, paid="0.00")], pay_date="20260415")
    store.close()
    store = build_store(tmp_path, [visit(700, WHO)], pay_date="20260520")
    store.close()
    store = build_store(tmp_path, [reversal(700, WHO), visit(700, WHO, paid="40.00", allowed="40.00")], pay_date="20260610")
    Detector(store, run_id="t").run()
    r = denials(store)["CB0000300C017:99214:DENIED_MISSING_INFO"]
    print("bill 300:", r["status"], r["recovered_cents"], r["auto_note"])
    assert r["status"] != "resolved" or r["recovered_cents"] <= 4000, f"recorded {r['recovered_cents']} recovered; only $40.00 stands"
    store.close()


# --------------------------------------------------------------------------- the whole-claim skip
def test_whole_claim_skip_does_not_cover_more_units_than_were_paid(tmp_path):
    """C017 paid one unit of 17003. The corrected claim C024 bills five units and is denied as a duplicate (CO-18):
    four units were never paid, so the denial must be listed."""
    who = "BIRCH*ANN****MI*W55500301"
    one = (f"CLP*CB0000300C017*1*25.00*6.00*0.00*12*PC300*11~NM1*QC*1*{who}~NM1*82*2*TEST PROVIDER*****XX*{NPI}~DTM*232*20260401~"
           "SVC*HC:17003*25.00*6.00**1~DTM*472*20260401~CAS*CO*45*19.00~AMT*B6*6.00~")
    store = build_store(tmp_path, clean_visits(1, 25, "17003", "25.00", "6.00") + [one], pay_date="20260415")
    store.close()
    five = (f"CLP*CB0000300C024*4*125.00*0.00*0.00*12*PC301*11~NM1*QC*1*{who}~NM1*82*2*TEST PROVIDER*****XX*{NPI}~DTM*232*20260401~"
            "SVC*HC:17003*125.00*0.00**5~DTM*472*20260401~CAS*CO*18*125.00~")
    store = build_store(tmp_path, [five], pay_date="20260520")
    Detector(store, run_id="t").run()
    listed = [r["patient_control_number"] for r in store.query("SELECT patient_control_number FROM findings WHERE signal='CLAIM_DENIED_UNPAID'")]
    print("CLAIM_DENIED_UNPAID listed for:", listed)
    assert "CB0000300C024" in listed, "five units denied, one paid: the four unpaid units are hidden"
    store.close()


def test_whole_claim_skip_does_not_cover_a_second_line_of_the_same_code(tmp_path):
    """C017 paid one specimen (88305). The corrected claim C024 adds the second specimen (88305-59) and is denied as a
    duplicate: the second specimen was never paid, so the denial must be listed."""
    who = "BIRCH*ANN****MI*W55500301"
    one = (f"CLP*CB0000300C017*1*150.00*70.00*0.00*12*PC300*11~NM1*QC*1*{who}~NM1*82*2*TEST PROVIDER*****XX*{NPI}~DTM*232*20260401~"
           + svc("88305", "150.00", "70.00", "70.00", "CAS*CO*45*80.00~"))
    store = build_store(tmp_path, clean_visits(1, 25, "88305", "150.00", "70.00") + [one], pay_date="20260415")
    store.close()
    two = (f"CLP*CB0000300C024*4*300.00*0.00*0.00*12*PC301*11~NM1*QC*1*{who}~NM1*82*2*TEST PROVIDER*****XX*{NPI}~DTM*232*20260401~"
           + svc("88305", "150.00", "0.00", None, "CAS*CO*18*150.00~") + svc("88305", "150.00", "0.00", None, "CAS*CO*18*150.00~", mods="59"))
    store = build_store(tmp_path, [two], pay_date="20260520")
    Detector(store, run_id="t").run()
    listed = [r["patient_control_number"] for r in store.query("SELECT patient_control_number FROM findings WHERE signal='CLAIM_DENIED_UNPAID'")]
    print("CLAIM_DENIED_UNPAID listed for:", listed)
    assert "CB0000300C024" in listed, "the second specimen was never paid but the denial is hidden"
    store.close()


def test_two_whole_claim_denials_of_one_bill_are_not_both_listed(tmp_path):
    """Bill 300: C017 denied (CO-16), C024 paid, then C024's payment taken back and C024 denied. One service, one
    unpaid amount: the billers should see it once, not as two denials adding up to twice the money."""
    store = build_store(tmp_path, base_visits() + [visit(300, WHO, paid="0.00", cas="CAS*CO*16*223.65~", status="4")], pay_date="20260415")
    store.close()
    store = build_store(tmp_path, [visit(300, WHO).replace("C017*1*", "C024*1*")], pay_date="20260520")
    store.close()
    store = build_store(tmp_path, [reversal(300, WHO, "C024"), visit(300, WHO, paid="0.00", cas="CAS*CO*50*223.65~", status="4").replace("C017*4*", "C024*4*")],
                        pay_date="20260610")
    Detector(store, run_id="t").run()
    rows = store.query("SELECT patient_control_number, expected_cents, shortfall_cents FROM findings WHERE signal='CLAIM_DENIED_UNPAID' AND status='open'")
    print("open CLAIM_DENIED_UNPAID:", [tuple(r) for r in rows])
    assert len(rows) == 1, f"one unpaid service listed {len(rows)} times, ${sum(r['shortfall_cents'] or 0 for r in rows)/100:.2f} in total"
    store.close()


# --------------------------------------------------------------------------- what people recorded
def _closed_then(tmp_path, status, note):
    store = build_store(tmp_path, base_visits() + [visit(300, WHO, paid="0.00")], pay_date="20260415")
    store.close()
    store = build_store(tmp_path, [visit(700, WHO)], pay_date="20260520")
    Detector(store, run_id="t1").run()
    r = denials(store)["CB0000300C017:99214:DENIED_MISSING_INFO"]
    assert r["status"] == "resolved" and r["auto_note"]
    apply_status(store.conn, r["finding_id"], status, note, actor="biller")
    store.conn.commit()
    Detector(store, run_id="t2").run()
    return store, denials(store)["CB0000300C017:99214:DENIED_MISSING_INFO"]


def test_a_biller_reopening_an_automatic_closure_is_not_closed_again(tmp_path):
    store, r = _closed_then(tmp_path, "open", "Claim 700 is the son's visit (same name and member number), not this one")
    print("after re-run:", r["status"], r["notes"], r["auto_note"])
    assert r["status"] == "open", "the biller's reopening was replaced by the automatic closure on the next run"
    store.close()


def test_a_biller_marking_an_automatic_closure_lost_keeps_lost(tmp_path):
    store, r = _closed_then(tmp_path, "lost", "Payer recouped claim 700 by offset; upheld on appeal")
    print("after re-run:", r["status"], r["notes"], r["auto_note"])
    assert r["status"] == "lost", "the biller's 'lost' was replaced by the automatic closure on the next run"
    store.close()


def test_a_biller_marking_an_automatic_closure_appealed_keeps_appealed(tmp_path):
    store, r = _closed_then(tmp_path, "appealed", "Appeal faxed: claim 700 was another lesion")
    print("after re-run:", r["status"], r["notes"], r["auto_note"])
    assert r["status"] == "appealed", "the biller's 'appealed' was replaced by the automatic closure on the next run"
    store.close()
