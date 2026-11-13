"""
Review of 418bad3, fourth batch: the same bill's other suffixes (later_payment, paid_events, bill_of).
Invented data only. """
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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


def test_one_unit_paid_on_the_corrected_claim_does_not_close_five_units_denied(tmp_path):
    """The units rule holds for another bill (tests/test_resolution.py); here the one unit is paid on the same bill's
    corrected claim C024. Four units were never paid."""
    who = "BIRCH*ANN****MI*W55500301"
    five = (f"CLP*CB0000300C017*1*125.00*0.00*0.00*12*PC300*11~NM1*QC*1*{who}~NM1*82*2*TEST PROVIDER*****XX*{NPI}~DTM*232*20260401~"
            "SVC*HC:17003*125.00*0.00**5~DTM*472*20260401~CAS*CO*16*125.00~")
    store = build_store(tmp_path, clean_visits(1, 25, "17003", "25.00", "6.00") + [five], pay_date="20260415")
    store.close()
    one = (f"CLP*CB0000300C024*1*25.00*6.00*0.00*12*PC301*11~NM1*QC*1*{who}~NM1*82*2*TEST PROVIDER*****XX*{NPI}~DTM*232*20260401~"
           "SVC*HC:17003*25.00*6.00**1~DTM*472*20260401~CAS*CO*45*19.00~AMT*B6*6.00~")
    store = build_store(tmp_path, [one], pay_date="20260520")
    Detector(store, run_id="t").run()
    r = denials(store)["CB0000300C017:17003:DENIED_MISSING_INFO"]
    print("five-unit denial:", r["status"], r["recovered_cents"], r["auto_note"])
    assert r["status"] == "open", f"five units denied, one paid on the corrected claim, closed: {r['auto_note']}"
    store.close()


def test_a_secondary_on_the_corrected_claim_does_not_keep_its_reversed_primary_payment(tmp_path):
    """Same bill: C017's line is denied; C024 is paid by the primary and by the secondary (same claim id); the primary
    then takes its payment back. C017's denial must not stay closed on that payment."""
    store = build_store(tmp_path, base_visits() + [visit(300, WHO, paid="0.00")], pay_date="20260415")
    store.close()
    c024 = lambda **kw: visit(300, WHO, **kw).replace("CB0000300C017", "CB0000300C024")
    store = build_store(tmp_path, [c024()], pay_date="20260520")
    store.close()
    store = build_store(tmp_path, [c024(paid="23.52", allowed="117.60", status="2")], pay_date="20260530", payer="HUMANA")
    store.close()
    store = build_store(tmp_path, [reversal(300, WHO, "C024"), c024(paid="0.00", cas="CAS*CO*22*223.65~", status="4")], pay_date="20260610")
    Detector(store, run_id="t").run()
    r = denials(store)["CB0000300C017:99214:DENIED_MISSING_INFO"]
    print("C017 denial:", r["status"], r["auto_note"])
    assert r["status"] == "open", f"closed on a primary payment that was taken back: {r['auto_note']}"
    store.close()


def test_two_patients_whose_claim_ids_share_nine_characters(tmp_path):
    """Claim ids that are not EMA's (for example a date and a sequence, 20260401001 and 20260401002) share their first
    nine characters, which bill_of() reads as one bill. Patient A's whole-claim denial must not be hidden, and A's line
    denial must not be closed, by patient B's payment."""
    a_whole = visit(1, "ASH*RO****MI*W55500304", paid="0.00", cas="CAS*CO*197*223.65~", status="4").replace("CB0000001C017", "20260401001")
    a_line = visit(2, "FIR*JO****MI*W55500305", paid="0.00").replace("CB0000002C017", "20260401003")
    store = build_store(tmp_path, clean_visits(100, 25, "99214", "223.65", "117.60") + [a_whole, a_line], pay_date="20260415")
    store.close()
    b_paid = visit(3, "YEW*MO****MI*W55500306").replace("CB0000003C017", "20260401002")
    store = build_store(tmp_path, [b_paid], pay_date="20260520")
    Detector(store, run_id="t").run()
    claims = [r["patient_control_number"] for r in store.query("SELECT patient_control_number FROM findings WHERE signal='CLAIM_DENIED_UNPAID'")]
    line = denials(store)["20260401003:99214:DENIED_MISSING_INFO"]
    print("CLAIM_DENIED_UNPAID listed for:", claims, "| A's line denial:", line["status"], line["auto_note"])
    assert claims == ["20260401001"] and line["status"] == "open", "another patient's payment hid or closed these"
    store.close()


def units_claim(i, who, units, paid, cas, status="1", suffix="C017", dos="20260401"):
    allowed = f"AMT*B6*{paid}~" if float(paid) > 0 else ""
    sign = "-" if status == "22" else ""
    return (f"CLP*CB{i:07d}{suffix}*{status}*{sign}{25 * units:.2f}*{sign}{paid}*0.00*12*PC{i}*11~NM1*QC*1*{who}~"
            f"NM1*82*2*TEST PROVIDER*****XX*{NPI}~DTM*232*{dos}~"
            f"SVC*HC:17003*{sign}{25 * units:.2f}*{sign}{paid}**{sign}{units}~DTM*472*{dos}~{cas}{allowed if status != '22' else ''}")


def test_a_reversed_and_repaid_claim_does_not_count_its_units_twice(tmp_path):
    """Bill 300 has two units of 17003 denied. Bill 700 (same patient, date, code) is paid for one unit; the payer then
    reverses it and pays the same one unit again (a re-adjudication, such as a fee-schedule mass adjustment). One unit
    stands: bill 300's two-unit denial must stay open."""
    who = "BIRCH*ANN****MI*W55500301"
    store = build_store(tmp_path, clean_visits(1, 25, "17003", "25.00", "6.00") + [units_claim(300, who, 2, "0.00", "CAS*CO*16*50.00~")],
                        pay_date="20260415")
    store.close()
    store = build_store(tmp_path, [units_claim(700, who, 1, "6.00", "CAS*CO*45*19.00~")], pay_date="20260501")
    store.close()
    store = build_store(tmp_path, [units_claim(700, who, 1, "6.00", "CAS*CO*45*-19.00~", status="22"),
                                   units_claim(700, who, 1, "6.00", "CAS*CO*45*19.00~")], pay_date="20260601")
    Detector(store, run_id="t").run()
    r = denials(store)["CB0000300C017:17003:DENIED_MISSING_INFO"]
    print("two-unit denial:", r["status"], r["auto_note"])
    assert r["status"] == "open", f"one unit paid (then reversed and paid again) closed a two-unit denial: {r['auto_note']}"
    store.close()


def test_whole_claim_skip_on_a_primary_payment_readjudicated_as_secondary(tmp_path):
    """C017 paid as primary; the resubmission C024 is denied as a duplicate (skipped: paid on C017). Then the payer
    finds other insurance, reverses C017 and re-pays it as secondary ($23.52). The primary money for the visit is not
    paid anywhere: C024's denial (or C017's) must be listed."""
    store = build_store(tmp_path, base_visits() + [visit(300, WHO)], pay_date="20260115")
    store.close()
    dup = visit(300, WHO, paid="0.00", cas="CAS*CO*18*223.65~", status="4").replace("C017*4*", "C024*4*")
    store = build_store(tmp_path, [dup], pay_date="20260215")
    store.close()
    store = build_store(tmp_path, [reversal(300, WHO), visit(300, WHO, paid="23.52", allowed="117.60", status="2")], pay_date="20260315")
    Detector(store, run_id="t").run()
    listed = [r["patient_control_number"] for r in store.query("SELECT patient_control_number FROM findings WHERE category='denial' AND status='open'")]
    print("open denials:", listed)
    assert listed, "the primary's money is gone and nothing on bill 300 is listed"
    store.close()


def test_two_patients_sharing_nine_characters_whole_claim_skip(tmp_path):
    """As above, but patient B's claim was paid BEFORE patient A's whole-claim denial: the 418bad3 skip hides A's denial."""
    b_paid = visit(3, "YEW*MO****MI*W55500306").replace("CB0000003C017", "20260401002")
    store = build_store(tmp_path, clean_visits(100, 25, "99214", "223.65", "117.60") + [b_paid], pay_date="20260410")
    store.close()
    a_whole = visit(1, "ASH*RO****MI*W55500304", paid="0.00", cas="CAS*CO*197*223.65~", status="4").replace("CB0000001C017", "20260401001")
    store = build_store(tmp_path, [a_whole], pay_date="20260415")
    Detector(store, run_id="t").run()
    claims = [r["patient_control_number"] for r in store.query("SELECT patient_control_number FROM findings WHERE signal='CLAIM_DENIED_UNPAID'")]
    print("CLAIM_DENIED_UNPAID listed for:", claims)
    assert claims == ["20260401001"], "patient B's earlier payment hides patient A's whole-claim denial"
    store.close()
