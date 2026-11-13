"""
Review of 418bad3, third batch: line dates given as a period, near misses, people's statuses across reversals.
Invented data only. """
from __future__ import annotations

import json
import sys

import os  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch.actions import apply_status  # noqa: E402
from remitwatch.detect import Detector  # noqa: E402
from tests.test_features import NPI, build_store, clean_visits  # noqa: E402
from tests.test_resolution import reversal, visit  # noqa: E402

WHO = "ELM*BO****MI*W55500302"


def base_visits():
    return clean_visits(1, 25, "99214", "223.65", "117.60")


def denials(store):
    """Denial findings by claim id, code and signal (SELECT *: an older copy has no hint column)."""
    return {f"{r['patient_control_number']}:{r['procedure_code'] or ''}:{r['signal']}": dict(r, hint=dict(r).get("hint"))
            for r in store.query("SELECT * FROM findings WHERE category='denial'")}


def period_claim(i, who, dos, paid, cas, status="1"):
    """Line dates sent as a period (DTM*150 / DTM*151), one day long, instead of DTM*472."""
    allowed = f"AMT*B6*{paid}~" if float(paid) > 0 else ""
    return (f"CLP*CB{i:07d}C017*{status}*223.65*{paid}*0.00*12*PC{i}*11~NM1*QC*1*{who}~NM1*82*2*TEST PROVIDER*****XX*{NPI}~DTM*232*{dos}~"
            f"SVC*HC:99214*223.65*{paid}**1~DTM*150*{dos}~DTM*151*{dos}~{cas}{allowed}")


def test_a_visit_on_another_date_does_not_close_a_denial_when_lines_carry_a_period(tmp_path):
    store = build_store(tmp_path, base_visits() + [period_claim(300, WHO, "20260110", "0.00", "CAS*CO*16*223.65~")], pay_date="20260120")
    store.close()
    store = build_store(tmp_path, [period_claim(700, WHO, "20260305", "117.60", "CAS*CO*45*106.05~")], pay_date="20260320")
    Detector(store, run_id="t").run()
    r = denials(store)["CB0000300C017:99214:DENIED_MISSING_INFO"]
    print("January denial:", r["status"], r["service_date"], r["auto_note"])
    assert r["status"] == "open", f"the March visit's payment closed the January denial: {r['auto_note']}"
    store.close()


def test_father_and_son_with_one_name_and_member_number_do_not_close_each_other(tmp_path):
    """NM1*QC carries the suffix in NM107 (JR / SR); the parser drops it, so both are 'SMITH JOHN W11100001'."""
    sr = visit(300, "SMITH*JOHN*A**SR*MI*W11100001", paid="0.00")
    store = build_store(tmp_path, base_visits() + [sr], pay_date="20260415")
    store.close()
    store = build_store(tmp_path, [visit(700, "SMITH*JOHN*B**JR*MI*W11100001")], pay_date="20260520")
    Detector(store, run_id="t").run()
    r = denials(store)["CB0000300C017:99214:DENIED_MISSING_INFO"]
    print("father's denial:", r["status"], r["auto_note"])
    assert r["status"] == "open", f"the son's payment closed the father's denial: {r['auto_note']}"
    store.close()


def test_a_corrected_claim_paid_before_the_originals_denial_leaves_a_hint(tmp_path):
    """The corrected claim (bill 700) is paid May 1; the original (bill 300) comes back denied for missing information
    on May 8. It is a near miss (the payment is before the denial): it should stay open WITH a hint, not silently."""
    store = build_store(tmp_path, base_visits() + [visit(700, WHO)], pay_date="20260501")
    store.close()
    store = build_store(tmp_path, [visit(300, WHO, paid="0.00")], pay_date="20260508")
    Detector(store, run_id="t").run()
    r = denials(store)["CB0000300C017:99214:DENIED_MISSING_INFO"]
    print("bill 300:", r["status"], r["hint"])
    assert r["status"] == "open" and r["hint"], "no hint: the billers will rework a visit already paid on claim 700"
    store.close()


def test_a_corrected_claim_adjudicated_to_the_deductible_leaves_a_hint(tmp_path):
    """The corrected claim is adjudicated: allowed $117.60, all of it the patient's deductible (PR-1), paid $0. The
    payer has decided the service; the original's denial should at least say so."""
    store = build_store(tmp_path, base_visits() + [visit(300, WHO, paid="0.00")], pay_date="20260115")
    store.close()
    ded = (f"CLP*CB0000700C017*1*223.65*0.00*117.60*12*PC700*11~NM1*QC*1*{WHO}~NM1*82*2*TEST PROVIDER*****XX*{NPI}~DTM*232*20260401~"
           "SVC*HC:99214*223.65*0.00**1~DTM*472*20260401~CAS*CO*45*106.05~CAS*PR*1*117.60~AMT*B6*117.60~")
    store = build_store(tmp_path, [ded], pay_date="20260205")
    Detector(store, run_id="t").run()
    r = denials(store)["CB0000300C017:99214:DENIED_MISSING_INFO"]
    print("bill 300:", r["status"], r["hint"])
    assert r["status"] != "open" or r["hint"], "open with no word that the payer adjudicated the service to the deductible"
    store.close()


def test_two_denied_submissions_of_one_bill_are_one_unpaid_service(tmp_path):
    """C017 denied (CO-16), corrected C024 denied (CO-50): no payment anywhere. One visit, $117.60 at stake."""
    store = build_store(tmp_path, base_visits() + [visit(300, WHO, paid="0.00", cas="CAS*CO*16*223.65~", status="4")], pay_date="20260415")
    store.close()
    store = build_store(tmp_path, [visit(300, WHO, paid="0.00", cas="CAS*CO*50*223.65~", status="4").replace("C017*4*", "C024*4*")], pay_date="20260520")
    Detector(store, run_id="t").run()
    rows = store.query("SELECT patient_control_number, shortfall_cents FROM findings WHERE signal='CLAIM_DENIED_UNPAID' AND status='open'")
    print("open CLAIM_DENIED_UNPAID:", [tuple(r) for r in rows])
    assert len(rows) == 1, f"one visit counted {len(rows)} times (${sum(r['shortfall_cents'] for r in rows)/100:.2f})"
    store.close()


# --------------------------------------------------------------------------- statuses across a closure and its reversal
def _setup_denial_then_payment(tmp_path):
    store = build_store(tmp_path, base_visits() + [visit(300, WHO, paid="0.00")], pay_date="20260415")
    store.close()
    return build_store(tmp_path, [visit(700, WHO)], pay_date="20260520")


def test_control_appealed_before_the_closure_comes_back_after_the_reversal(tmp_path):
    store = build_store(tmp_path, base_visits() + [visit(300, WHO, paid="0.00")], pay_date="20260415")
    Detector(store, run_id="t1").run()
    fid = denials(store)["CB0000300C017:99214:DENIED_MISSING_INFO"]["finding_id"]
    apply_status(store.conn, fid, "appealed", "Appeal faxed", actor="biller")
    store.conn.commit()
    store.close()
    store = build_store(tmp_path, [visit(700, WHO)], pay_date="20260520")
    Detector(store, run_id="t2").run()
    assert denials(store)["CB0000300C017:99214:DENIED_MISSING_INFO"]["status"] == "resolved"
    store.close()
    store = build_store(tmp_path, [reversal(700, WHO), visit(700, WHO, paid="0.00", cas="CAS*CO*22*223.65~", status="4")], pay_date="20260610")
    Detector(store, run_id="t3").run()
    r = denials(store)["CB0000300C017:99214:DENIED_MISSING_INFO"]
    assert r["status"] == "appealed" and r["notes"] == "Appeal faxed"
    store.close()


def test_an_automatic_note_does_not_stay_on_a_finding_that_is_no_longer_paid(tmp_path):
    """Closed automatically, then the biller reopens it (claim 700 was the son's visit), then claim 700's payment is
    taken back. The finding is open, and must not still say 'Paid on a later remittance under claim 700'."""
    store = _setup_denial_then_payment(tmp_path)
    Detector(store, run_id="t1").run()
    fid = denials(store)["CB0000300C017:99214:DENIED_MISSING_INFO"]["finding_id"]
    apply_status(store.conn, fid, "open", "Claim 700 was another visit", actor="biller")
    store.conn.commit()
    store.close()
    store = build_store(tmp_path, [reversal(700, WHO), visit(700, WHO, paid="0.00", cas="CAS*CO*22*223.65~", status="4")], pay_date="20260610")
    Detector(store, run_id="t2").run()
    r = denials(store)["CB0000300C017:99214:DENIED_MISSING_INFO"]
    print("after the takeback:", r["status"], "| notes:", r["notes"], "| auto_note:", r["auto_note"])
    assert r["status"] == "open" and not r["auto_note"], "an open finding still carries 'Paid on a later remittance ...'"
    store.close()


def test_a_procedure_bundled_away_and_paid_on_a_corrected_bill_leaves_a_hint(tmp_path):
    """A biopsy (11102) bundled away (CO-97) on bill 300, corrected and paid on bill 700. Non-visit bundling is left
    out of resolve_elsewhere, which is safe, but the near miss should still carry a hint."""
    who = "OAK*KIM****MI*W55500399"
    bundled = (f"CLP*CB0000300C017*1*401.65*117.60*0.00*12*PC300*11~NM1*QC*1*{who}~NM1*82*2*TEST PROVIDER*****XX*{NPI}~DTM*232*20260401~"
               "SVC*HC:99214*223.65*117.60**1~DTM*472*20260401~CAS*CO*45*106.05~AMT*B6*117.60~"
               "SVC*HC:11102*178.00*0.00**1~DTM*472*20260401~CAS*CO*97*178.00~")
    store = build_store(tmp_path, base_visits() + clean_visits(101, 25, "11102", "178.00", "90.00") + [bundled], pay_date="20260415")
    store.close()
    fixed = (f"CLP*CB0000700C017*1*178.00*90.00*0.00*12*PC700*11~NM1*QC*1*{who}~NM1*82*2*TEST PROVIDER*****XX*{NPI}~DTM*232*20260401~"
             "SVC*HC:11102:59*178.00*90.00**1~DTM*472*20260401~CAS*CO*45*88.00~AMT*B6*90.00~")
    store = build_store(tmp_path, [fixed], pay_date="20260520")
    Detector(store, run_id="t").run()
    rows = store.query("SELECT signal, status, hint FROM findings WHERE patient_control_number='CB0000300C017' AND category='bundling'")
    print("bundling findings on bill 300:", [tuple(r) for r in rows])
    assert rows and all(r["status"] != "open" or r["hint"] for r in rows), "open with no word of the payment on bill 700"
    store.close()


def test_control_claim_level_denial_with_a_code_never_paid_is_not_skipped(tmp_path):
    who = "OAK*KIM****MI*W55500399"
    paid = (f"CLP*CB0000300C017*1*223.65*117.60*0.00*12*PC300*11~NM1*QC*1*{who}~NM1*82*2*TEST PROVIDER*****XX*{NPI}~DTM*232*20260401~"
            "SVC*HC:99214*223.65*117.60**1~DTM*472*20260401~CAS*CO*45*106.05~AMT*B6*117.60~")
    store = build_store(tmp_path, base_visits() + clean_visits(101, 25, "17000", "150.00", "60.00") + [paid], pay_date="20260415")
    store.close()
    whole = (f"CLP*CB0000300C024*4*373.65*0.00*0.00*12*PC301*11~NM1*QC*1*{who}~NM1*82*2*TEST PROVIDER*****XX*{NPI}~DTM*232*20260401~"
             "SVC*HC:99214*223.65*0.00**1~DTM*472*20260401~CAS*CO*18*223.65~SVC*HC:17000*150.00*0.00**1~DTM*472*20260401~CAS*CO*16*150.00~")
    store = build_store(tmp_path, [whole], pay_date="20260520")
    Detector(store, run_id="t").run()
    assert [r[0] for r in store.query("SELECT patient_control_number FROM findings WHERE signal='CLAIM_DENIED_UNPAID'")] == ["CB0000300C024"]
    store.close()


def test_a_whole_claim_duplicate_of_a_service_paid_on_another_bill(tmp_path):
    """The visit was paid on bill 300 in April. Charges entered twice made bill 700; the payer denies all of bill 700 as a
    duplicate (CLP02 = 4, CO-18) in May. A line-level duplicate closes as 'already paid'; the whole-claim one is listed
    as 'denied and never paid', with no hint, although the service was paid."""
    store = build_store(tmp_path, base_visits() + [visit(300, WHO)], pay_date="20260415")
    store.close()
    store = build_store(tmp_path, [visit(700, WHO, paid="0.00", cas="CAS*CO*18*223.65~", status="4")], pay_date="20260520")
    Detector(store, run_id="t").run()
    r = denials(store)["CB0000700C017::CLAIM_DENIED_UNPAID"]
    print("bill 700:", r["status"], r["title"], "| hint:", r["hint"])
    assert r["status"] != "open" or r["hint"], "listed as never paid, with no word that bill 300 paid it"
    store.close()
