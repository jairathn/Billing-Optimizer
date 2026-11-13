"""
When a finding may close because the service was paid elsewhere, and when a payer's routine lower rate is grouped.
The cases come from an independent review: each one is a way to mark an unpaid service as paid, or to hide a real
underpayment, that the rules must not allow. Everything here is invented: names, member numbers, claims and amounts.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch import detect as det_mod  # noqa: E402
from remitwatch.actions import apply_status  # noqa: E402
from remitwatch.detect import Detector  # noqa: E402
from tests.test_features import NPI, build_store, clean_visits, svc  # noqa: E402

PA_NPI = "1999999993"


def visit(i, who, code="99214", charge="223.65", paid="117.60", allowed="117.60", cas=None, status="1", dos="20260401", mods=""):
    if cas is None:
        cas = f"CAS*CO*45*{float(charge) - float(paid):.2f}~" if float(paid) > 0 else f"CAS*CO*16*{charge}~"
    return (f"CLP*CB{i:07d}C017*{status}*{charge}*{paid}*0.00*12*PC{i}*11~NM1*QC*1*{who}~NM1*82*2*TEST PROVIDER*****XX*{NPI}~DTM*232*{dos}~"
            + svc(code, charge, paid, allowed if float(paid) > 0 else None, cas, dos=dos, mods=mods))


def reversal(i, who, suffix="C017"):
    return (f"CLP*CB{i:07d}{suffix}*22*-223.65*-117.60*0.00*12*PC{i}*11~NM1*QC*1*{who}~NM1*82*2*TEST PROVIDER*****XX*{NPI}~DTM*232*20260401~"
            "SVC*HC:99214*-223.65*-117.60**-1~DTM*472*20260401~CAS*CO*45*-106.05~")


def denials(store):
    return {f"{r['patient_control_number']}:{r['procedure_code'] or ''}:{r['signal']}": r for r in store.query(
        "SELECT patient_control_number, procedure_code, signal, status, auto_note, recovered_cents, hint, evidence FROM findings WHERE category='denial'")}


def base_visits():
    return clean_visits(1, 25, "99214", "223.65", "117.60")


def ema(store, rows):
    store.conn.executemany("""INSERT INTO ema_rows (row_fingerprint, family, source_name, row_index, service_date, claim_id, cpt, units, charges_cents,
                              activity_type, insurance_payments_cents, payments_cents, transaction_date, patient_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
    store.conn.commit()


# --------------------------------------------------------------------------- who the patient is
def test_the_same_name_on_another_member_number_does_not_close(tmp_path):
    store = build_store(tmp_path, base_visits() + [visit(300, "SMITH*JOHN****MI*W11100001", paid="0.00")], pay_date="20260415")
    store.close()
    store = build_store(tmp_path, [visit(700, "SMITH*JOHN****MI*W11100002")], pay_date="20260520")      # a son with his father's name
    Detector(store, run_id="t").run()
    r = denials(store)["CB0000300C017:99214:DENIED_MISSING_INFO"]
    assert r["status"] == "open" and r["hint"] == "Possibly paid on claim CB0000700C017 (2026-05-20)"
    assert "nothing ties the two claims" in json.loads(r["evidence"])["maybe_paid_later"]["why_not_closed"]
    store.close()


def test_a_family_on_one_member_number_does_not_close_each_other(tmp_path):
    store = build_store(tmp_path, base_visits() + [visit(300, "ORCHARD*LEE****MI*W55500300", paid="0.00")], pay_date="20260415")
    store.close()
    store = build_store(tmp_path, [visit(700, "ORCHARD*MAY****MI*W55500300")], pay_date="20260520")
    Detector(store, run_id="t").run()
    r = denials(store)["CB0000300C017:99214:DENIED_MISSING_INFO"]
    assert r["status"] == "open" and r["hint"] is None
    store.close()


def test_work_people_recorded_is_not_replaced_by_someone_elses_payment(tmp_path):
    store = build_store(tmp_path, base_visits() + [visit(300, "SMITH*JOHN****MI*W11100001", paid="0.00"),
                                                   visit(301, "SMITH*JANE****MI*W11100003", paid="0.00")], pay_date="20260415")
    Detector(store, run_id="t1").run()
    ids = {r["patient_control_number"]: r["finding_id"] for r in store.query("SELECT finding_id, patient_control_number FROM findings WHERE category='denial'")}
    apply_status(store.conn, ids["CB0000300C017"], "lost", "Payer upheld on appeal", actor="biller")
    apply_status(store.conn, ids["CB0000301C017"], "dismissed", "Self-pay visit", actor="biller")
    store.conn.commit()
    store.close()
    store = build_store(tmp_path, [visit(700, "SMITH*JOHN****MI*W11100002"), visit(701, "SMITH*JANE****MI*W11100004")], pay_date="20260601")
    Detector(store, run_id="t2").run()
    rows = denials(store)
    assert rows["CB0000300C017:99214:DENIED_MISSING_INFO"]["status"] == "lost"
    assert rows["CB0000301C017:99214:DENIED_MISSING_INFO"]["status"] == "dismissed"
    store.close()


# --------------------------------------------------------------------------- what counts as paid
def test_codes_paid_twice_on_a_date_are_not_closed_by_another_claim(tmp_path):
    two = [(f"CLP*CB{900 + i:07d}C017*1*300.00*140.00*0.00*12*PC{900 + i}*11~NM1*QC*1*SAMPLE*PAT****MI*M{900 + i:08d}~NM1*82*2*TEST PROVIDER*****XX*{NPI}~"
            "DTM*232*20260401~" + svc("88305", "150.00", "70.00", "70.00", "CAS*CO*45*80.00~") + svc("88305", "150.00", "70.00", "70.00", "CAS*CO*45*80.00~", mods="59"))
           for i in range(5)]
    store = build_store(tmp_path, clean_visits(1, 25, "88305", "150.00", "70.00") + two
                        + [visit(300, "BIRCH*ANN****MI*W55500301", "88305", "150.00", "0.00")], pay_date="20260415")
    store.close()
    store = build_store(tmp_path, [visit(700, "BIRCH*ANN****MI*W55500301", "88305", "150.00", "70.00", "70.00")], pay_date="20260520")
    Detector(store, run_id="t").run()
    r = denials(store)["CB0000300C017:88305:DENIED_MISSING_INFO"]
    assert r["status"] == "open" and "more than once" in json.loads(r["evidence"])["maybe_paid_later"]["why_not_closed"]
    store.close()


def twin_claim(i, who):
    return (f"CLP*CB{i:07d}C017*1*300.00*70.00*0.00*12*PC{i}*11~NM1*QC*1*{who}~NM1*82*2*TEST PROVIDER*****XX*{NPI}~DTM*232*20260401~"
            + svc("88305", "150.00", "70.00", "70.00", "CAS*CO*45*80.00~") + svc("88305", "150.00", "0.00", None, "CAS*CO*16*150.00~", mods="59"))


def test_emas_figures_for_a_twin_line_neither_close_nor_hint(tmp_path):
    store = build_store(tmp_path, clean_visits(1, 25, "88305", "150.00", "70.00") + [twin_claim(300, "BIRCH*ANN****MI*W55500301")], pay_date="20260415")
    ema(store, [("f1", "charge_lines", "Encounter & Charge Lines.xlsx", 1, "2026-04-01", "CB0000300C017", "88305", 1, 15000, "Charge", 7000, None, None, None),
                ("e1", "era_lines", "ERA & Payment Lines.xlsx", 1, "2026-04-01", "CB0000300C017", "88305", 1, 15000, None, None, 7000, "2026-04-17", None)])
    Detector(store, run_id="t").run()
    r = denials(store)["CB0000300C017:88305:DENIED_MISSING_INFO"]
    assert r["status"] == "open" and r["hint"] is None
    store.close()


def test_a_claim_level_denial_needs_every_code_paid_again(tmp_path):
    base = base_visits() + clean_visits(101, 25, "17000", "150.00", "60.00") + clean_visits(201, 25, "11102", "180.00", "90.00")
    whole = (f"CLP*CB0000300C017*4*553.65*0.00*0.00*12*PC300*11~NM1*QC*1*OAK*KIM****MI*W55500399~NM1*82*2*TEST PROVIDER*****XX*{NPI}~"
             "DTM*232*20260401~CAS*CO*27*553.65~" + svc("99214", "223.65", "0.00", None) + svc("17000", "150.00", "0.00", None) + svc("11102", "180.00", "0.00", None))
    store = build_store(tmp_path, base + [whole], pay_date="20260415")
    store.close()
    store = build_store(tmp_path, [visit(700, "OAK*KIM****MI*W55500399")], pay_date="20260521")
    Detector(store, run_id="t").run()
    r = denials(store)["CB0000300C017::CLAIM_DENIED_UNPAID"]
    assert r["status"] == "open" and "not every code" in json.loads(r["evidence"])["maybe_paid_later"]["why_not_closed"]
    store.close()


def test_a_payment_the_payer_took_back_does_not_close(tmp_path):
    who = "ELM*BO****MI*W55500302"
    store = build_store(tmp_path, base_visits() + [visit(300, who, paid="0.00")], pay_date="20260415")
    store.close()
    store = build_store(tmp_path, [visit(700, who)], pay_date="20260520")
    store.close()
    store = build_store(tmp_path, [reversal(700, who), visit(700, who, paid="0.00", cas="CAS*CO*22*223.65~", status="4")], pay_date="20260610")
    Detector(store, run_id="t").run()
    assert denials(store)["CB0000300C017:99214:DENIED_MISSING_INFO"]["status"] == "open"
    store.close()


def test_a_payment_taken_back_on_the_same_bill_does_not_close_either(tmp_path):
    who = "ELM*BO****MI*W55500302"
    store = build_store(tmp_path, base_visits() + [visit(300, who, paid="0.00")], pay_date="20260415")
    store.close()
    store = build_store(tmp_path, [visit(300, who).replace("C017*1*", "C024*1*")], pay_date="20260520")
    store.close()
    store = build_store(tmp_path, [reversal(300, who, "C024")], pay_date="20260610")
    Detector(store, run_id="t").run()
    assert denials(store)["CB0000300C017:99214:DENIED_MISSING_INFO"]["status"] == "open"
    store.close()


def test_ema_alone_never_closes_and_reads_the_newest_export(tmp_path):
    who = "PINE*AL****MI*W55500303"
    store = build_store(tmp_path, base_visits() + [visit(300, who)], pay_date="20260415")
    store.close()
    store = build_store(tmp_path, [reversal(300, who), visit(300, who, paid="0.00", cas="CAS*CO*50*223.65~")], pay_date="20260520")
    # an export from before the takeback still carries the April payment; the next one shows nothing
    store.conn.executemany("INSERT INTO ema_sources (source_name, sha256, family, created_at) VALUES (?,?,?,?)",
                           [("Encounter & Charge Lines 2026-05-01.xlsx", "s1", "charge_lines", "2026-05-01T10:00:00Z"),
                            ("Encounter & Charge Lines 2026-06-01.xlsx", "s2", "charge_lines", "2026-06-01T10:00:00Z")])
    ema(store, [("f1", "charge_lines", "Encounter & Charge Lines 2026-05-01.xlsx", 1, "2026-04-01", "CB0000300C017", "99214", 1, 22365, "Charge", 11760, None, None, None)])
    store.conn.execute("UPDATE ema_rows SET source_sha256='s1' WHERE row_fingerprint='f1'")
    store.conn.commit()
    Detector(store, run_id="t").run()
    r = denials(store)["CB0000300C017:99214:DENIED_MEDICAL_NECESSITY"]
    assert r["status"] == "open" and r["hint"].startswith("EMA shows $117.60 paid by insurance")
    ema(store, [("f2", "charge_lines", "Encounter & Charge Lines 2026-06-01.xlsx", 1, "2026-04-01", "CB0000300C017", "99214", 1, 22365, "Charge", 0, None, None, None)])
    store.conn.execute("UPDATE ema_rows SET source_sha256='s2' WHERE row_fingerprint='f2'")
    store.conn.commit()
    Detector(store, run_id="t2").run()
    r = denials(store)["CB0000300C017:99214:DENIED_MEDICAL_NECESSITY"]
    assert r["status"] == "open" and r["hint"] is None
    store.close()


def test_earlier_secondary_or_superseded_payments_do_not_close(tmp_path):
    store = build_store(tmp_path, base_visits() + [visit(700, "ASH*RO****MI*W55500304")], pay_date="20260410")      # paid before the denial
    store.close()
    store = build_store(tmp_path, [visit(300, "ASH*RO****MI*W55500304", paid="0.00"), visit(301, "FIR*JO****MI*W55500305", paid="0.00"),
                                   visit(302, "YEW*MO****MI*W55500306", paid="0.00")], pay_date="20260415")
    store.close()
    store = build_store(tmp_path, [visit(701, "FIR*JO****MI*W55500305", paid="23.52", allowed="117.60", status="2")], pay_date="20260520", payer="HUMANA")
    store.close()
    store = build_store(tmp_path, [visit(702, "YEW*MO****MI*W55500306")], pay_date="20260521")
    store.conn.execute("UPDATE transactions SET superseded=1 WHERE payment_date='2026-05-21'")
    store.conn.commit()
    Detector(store, run_id="t").run()
    rows = denials(store)
    assert [rows[f"CB000030{i}C017:99214:DENIED_MISSING_INFO"]["status"] for i in range(3)] == ["open"] * 3
    store.close()


def test_one_unit_paid_does_not_close_five_units_denied(tmp_path):
    five = (f"CLP*CB0000300C017*1*125.00*0.00*0.00*12*PC300*11~NM1*QC*1*BIRCH*ANN****MI*W55500301~NM1*82*2*TEST PROVIDER*****XX*{NPI}~DTM*232*20260401~"
            "SVC*HC:17003*125.00*0.00**5~DTM*472*20260401~CAS*CO*16*125.00~")
    store = build_store(tmp_path, clean_visits(1, 25, "17003", "25.00", "6.00") + [five], pay_date="20260415")
    store.close()
    one = (f"CLP*CB0000700C017*1*25.00*6.00*0.00*12*PC700*11~NM1*QC*1*BIRCH*ANN****MI*W55500301~NM1*82*2*TEST PROVIDER*****XX*{NPI}~DTM*232*20260401~"
           "SVC*HC:17003*25.00*6.00**1~DTM*472*20260401~CAS*CO*45*19.00~AMT*B6*6.00~")
    store = build_store(tmp_path, [one], pay_date="20260520")
    Detector(store, run_id="t").run()
    r = denials(store)["CB0000300C017:17003:DENIED_MISSING_INFO"]
    assert r["status"] == "open" and "units" in json.loads(r["evidence"])["maybe_paid_later"]["why_not_closed"]
    store.close()



def claims_denied(store):
    return store.query("SELECT patient_control_number, status FROM findings WHERE signal='CLAIM_DENIED_UNPAID' AND status='open'")


def test_a_recouped_original_denied_after_its_resubmission_was_paid_is_not_unpaid(tmp_path):
    who = "ELM*BO****MI*W55500302"
    store = build_store(tmp_path, base_visits() + [visit(300, who)], pay_date="20250401")                       # the original, paid
    store.close()
    store = build_store(tmp_path, [visit(300, who).replace("C017*1*", "C024*1*")], pay_date="20260320")         # paid again on a resubmission
    store.close()
    store = build_store(tmp_path, [reversal(300, who), visit(300, who, paid="0.00", cas="CAS*CO*18*223.65~", status="4")], pay_date="20260410")
    Detector(store, run_id="t").run()
    assert claims_denied(store) == []
    store.close()


def test_a_primary_takeback_with_only_a_secondary_payment_left_is_still_a_denial(tmp_path):
    who = "ELM*BO****MI*W55500302"
    store = build_store(tmp_path, base_visits() + [visit(300, who)], pay_date="20250901")
    store.close()
    store = build_store(tmp_path, [visit(300, who, paid="23.52", allowed="117.60", status="2").replace("C017*2*", "C024*2*")], pay_date="20250920", payer="HUMANA")
    store.close()
    store = build_store(tmp_path, [reversal(300, who), visit(300, who, paid="0.00", cas="CAS*CO*22*223.65~", status="4")], pay_date="20260410")
    Detector(store, run_id="t").run()
    assert [r[0] for r in claims_denied(store)] == ["CB0000300C017"]
    store.close()



def test_a_bills_finding_stays_on_its_submission_and_a_denial_after_someone_acted_is_seen(tmp_path):
    who = "ELM*BO****MI*W55500302"
    store = build_store(tmp_path, base_visits() + [visit(300, who, paid="0.00", cas="CAS*CO*16*223.65~", status="4")], pay_date="20260415")
    Detector(store, run_id="t1").run()
    [(fid,)] = store.query("SELECT finding_id FROM findings WHERE signal='CLAIM_DENIED_UNPAID'")
    store.close()
    # the corrected claim is denied too: still one finding, on the submission that had it, listing the other
    store = build_store(tmp_path, [visit(300, who, paid="0.00", cas="CAS*CO*50*223.65~", status="4").replace("C017*4*", "C024*4*")], pay_date="20260520")
    Detector(store, run_id="t2").run()
    rows = store.query("SELECT finding_id, evidence FROM findings WHERE signal='CLAIM_DENIED_UNPAID' AND status='open'")
    assert [r["finding_id"] for r in rows] == [fid] and json.loads(rows[0]["evidence"])["earlier_denied_submissions"] == ["CB0000300C024"]
    # the biller appeals on June 1; a third submission is denied on June 20: that one is seen on its own
    apply_status(store.conn, fid, "appealed", "Appeal faxed", actor="biller", at="2026-06-01T15:00:00+00:00")
    store.conn.commit()
    store.close()
    store = build_store(tmp_path, [visit(300, who, paid="0.00", cas="CAS*CO*50*223.65~", status="4").replace("C017*4*", "C031*4*")], pay_date="20260620")
    Detector(store, run_id="t3").run()
    rows = sorted((r["patient_control_number"], r["status"]) for r in store.query(
        "SELECT patient_control_number, status FROM findings WHERE signal='CLAIM_DENIED_UNPAID' AND COALESCE(stale,0)=0"))
    assert rows == [("CB0000300C017", "appealed"), ("CB0000300C031", "open")]
    store.close()

# --------------------------------------------------------------------------- what the practice manager expected to close
def test_a_duplicates_denial_closes_when_the_service_was_already_paid_on_another_claim(tmp_path):
    store = build_store(tmp_path, base_visits() + [visit(300, "ASH*RO****MI*W55500304")], pay_date="20260415")
    store.close()
    store = build_store(tmp_path, [visit(700, "ASH*RO****MI*W55500304", paid="0.00", cas="CAS*OA*18*223.65~")], pay_date="20260520")
    Detector(store, run_id="t").run()
    r = denials(store)["CB0000700C017:99214:DENIED_DUPLICATE_UNPAID"]
    assert r["status"] == "resolved" and r["auto_note"].startswith("Already paid under claim CB0000300C017")
    store.close()


def test_a_bundled_visit_corrected_with_modifier_25_on_a_new_bill_closes(tmp_path):
    bundled = (f"CLP*CB0000300C017*1*401.21*91.31*0.00*12*PC300*11~NM1*QC*1*OAK*KIM****MI*W55500399~NM1*82*2*TEST PROVIDER*****XX*{NPI}~DTM*232*20260401~"
               "SVC*HC:99214*223.65*0.00**1~DTM*472*20260401~CAS*CO*97*223.65~SVC*HC:17110*177.56*91.31**1~DTM*472*20260401~CAS*CO*45*86.25~AMT*B6*91.31~")
    store = build_store(tmp_path, base_visits() + [bundled], pay_date="20260415")
    store.close()
    fixed = (f"CLP*CB0000700C017*1*223.65*117.60*0.00*12*PC700*11~NM1*QC*1*OAK*KIM****MI*W55500399~NM1*82*2*TEST PROVIDER*****XX*{NPI}~DTM*232*20260401~"
             + svc("99214", "223.65", "117.60", "117.60", "CAS*CO*45*106.05~", mods="25"))
    store = build_store(tmp_path, [fixed], pay_date="20260520")
    Detector(store, run_id="t").run()
    rows = store.query("SELECT signal, status FROM findings WHERE patient_control_number='CB0000300C017' AND category IN ('bundling','denial')")
    assert rows and all(r["status"] == "resolved" for r in rows)
    store.close()


# --------------------------------------------------------------------------- a payer's routine lower rate
GROUP_NPI = "1777777773"


def cl(i, code, charge, allowed, npi=NPI, cfi="12", dos="20260401", group=False):
    """A cleanly paid claim. group=True: the remittance names only the practice (NM1*82*2 with the group's NPI), not who saw
    the patient, as some payers' do."""
    co = f"{float(charge) - float(allowed):.2f}"
    who = f"NM1*82*2*TEST GROUP*****XX*{GROUP_NPI}~" if group else f"NM1*82*1*CLINICIAN*ONE****XX*{npi}~"
    return (f"CLP*CB{i:07d}C017*1*{charge}*{allowed}*0.00*{cfi}*PC{i}*11~NM1*QC*1*SAMPLE*PAT****MI*M{i:08d}~{who}REF*CE*PPO~"
            f"DTM*232*{dos}~" + svc(code, charge, allowed, allowed, f"CAS*CO*45*{co}~", dos=dos))


def underpaid(store):
    return store.query("""SELECT signal, actionable, appeal_deadline, shortfall_cents, status, evidence FROM findings
                          WHERE category IN ('underpayment','contract') AND COALESCE(stale,0)=0 ORDER BY signal""")


def test_physician_lines_at_85_percent_stay_appeals_with_deadlines(tmp_path):
    store = build_store(tmp_path, [cl(i, "99214", "223.65", "127.80", cfi="MB") for i in range(1, 26)], payer="KY MEDICARE")
    store.close()
    store = build_store(tmp_path, [cl(100 + i, "99214", "223.65", "117.60") for i in range(25)] + [cl(200 + i, "99214", "223.65", "100.00") for i in range(6)])
    Detector(store, run_id="t").run()
    rows = underpaid(store)
    assert [r["signal"] for r in rows] == ["UNDERPAID_VS_OWN_RATE"] * 6 and all(r["appeal_deadline"] and r["actionable"] == "appeal" for r in rows)
    store.close()


def test_a_cut_on_the_pa_rate_is_not_explained_as_the_pa_rate(tmp_path):
    med = [cl(i, "99214", "223.65", "127.80", cfi="MB") for i in range(1, 26)] + [cl(30 + i, "99214", "223.65", "108.63", npi=PA_NPI, cfi="MB") for i in range(25)]
    store = build_store(tmp_path, med, payer="KY MEDICARE")
    store.close()
    store = build_store(tmp_path, [cl(100 + i, "99214", "223.65", "100.00", npi=PA_NPI) for i in range(25)]
                        + [cl(200 + i, "99214", "223.65", "85.00", npi=PA_NPI) for i in range(6)])
    Detector(store, run_id="t").run()
    assert [r["signal"] for r in underpaid(store)] == ["UNDERPAID_VS_OWN_RATE"] * 6
    store.close()


def test_a_payer_error_from_a_date_is_one_finding_that_keeps_its_deadline(tmp_path):
    store = build_store(tmp_path, [cl(i, "99214", "223.65", "127.80", cfi="MB") for i in range(1, 26)], payer="KY MEDICARE")
    store.close()
    store = build_store(tmp_path, [cl(100 + i, "99214", "223.65", "117.60", cfi="16") for i in range(25)], payer="HUMANA", pay_date="20260415")
    store.close()
    store = build_store(tmp_path, [cl(200 + i, "99214", "223.65", "100.00", cfi="16", dos="20260801") for i in range(6)], payer="HUMANA", pay_date="20260901")
    Detector(store, run_id="t").run()
    [p] = underpaid(store)
    ev = json.loads(p["evidence"])
    assert (p["signal"], p["actionable"], p["shortfall_cents"]) == ("SECOND_RATE", "review", 6 * 1760) and p["appeal_deadline"]
    assert "rate change" in ev["note"] and "physician assistant" not in ev["note"]
    store.close()



def test_a_pattern_paid_over_months_keeps_every_claims_deadline(tmp_path):
    store = build_store(tmp_path, [cl(i, "99214", "223.65", "127.80", cfi="MB") for i in range(1, 26)], payer="KY MEDICARE")
    store.close()
    store = build_store(tmp_path, [cl(100 + i, "99214", "223.65", "117.60", cfi="16") for i in range(25)], payer="HUMANA", pay_date="20260415")
    store.close()
    store = build_store(tmp_path, [cl(200 + i, "99214", "223.65", "100.00", cfi="16", dos="20260801") for i in range(3)], payer="HUMANA", pay_date="20260901")
    store.close()
    store = build_store(tmp_path, [cl(300 + i, "99214", "223.65", "100.00", cfi="16", dos="20261001") for i in range(3)], payer="HUMANA", pay_date="20261101")
    Detector(store, run_id="t").run()
    [p] = store.query("SELECT appeal_deadline, deadline_basis, hint, evidence FROM findings WHERE signal='SECOND_RATE'")
    ev = json.loads(p["evidence"])
    dates = [d for d, _n, _c in ev["deadlines"]]
    assert len(dates) == 2 and p["appeal_deadline"] == dates[-1] and p["hint"] == f"Filing windows close {dates[0]} to {dates[-1]}"
    assert sum(n for _d, n, _c in ev["deadlines"]) == 6 and sum(c for _d, _n, c in ev["deadlines"]) == 6 * 1760
    assert sorted({c[3] for c in ev["claims"]}) == dates and dates[0] in p["deadline_basis"]
    store.close()

def test_amounts_close_together_are_not_a_routine_rate(tmp_path):
    claims = [cl(100 + i, "99214", "223.65", "117.60") for i in range(25)]
    claims += [cl(200 + i, "99214", "223.65", a) for i, a in enumerate(["100.00", "100.50", "101.00", "101.50", "102.00"])]
    store = build_store(tmp_path, claims)
    Detector(store, run_id="t").run()
    assert [r["signal"] for r in underpaid(store)] == ["UNDERPAID_VS_OWN_RATE"] * 5
    store.close()


def test_a_visit_at_half_rate_is_not_called_the_multiple_procedure_rule(tmp_path):
    store = build_store(tmp_path, [cl(100 + i, "99214", "223.65", "117.60") for i in range(25)] + [cl(200 + i, "99214", "223.65", "58.80") for i in range(6)])
    Detector(store, run_id="t").run()
    rows = underpaid(store)
    assert [r["signal"] for r in rows] == ["UNDERPAID_VS_OWN_RATE"] * 6
    store.close()


def test_one_offs_people_worked_stay_their_own_findings(tmp_path, monkeypatch):
    store = build_store(tmp_path, [cl(100 + i, "99214", "223.65", "117.60") for i in range(25)] + [cl(200 + i, "99214", "223.65", "100.00", group=True) for i in range(8)])
    monkeypatch.setattr(det_mod, "SECOND_RATE_MIN_LINES", 10 ** 6)          # as an earlier release found them: eight one-offs
    Detector(store, run_id="before").run()
    ids = {r["patient_control_number"]: r["finding_id"] for r in store.query("SELECT finding_id, patient_control_number FROM findings WHERE signal='UNDERPAID_VS_OWN_RATE'")}
    apply_status(store.conn, ids["CB0000200C017"], "appealed", "Appeal sent by fax", actor="biller")
    apply_status(store.conn, ids["CB0000201C017"], "lost", "Payer upheld: PA rate per contract", actor="biller")
    store.conn.commit()
    monkeypatch.setattr(det_mod, "SECOND_RATE_MIN_LINES", 5)
    Detector(store, run_id="after").run()
    rows = underpaid(store)
    pattern = [r for r in rows if r["signal"] == "SECOND_RATE"]
    kept = sorted((r["status"]) for r in rows if r["signal"] == "UNDERPAID_VS_OWN_RATE")
    assert len(pattern) == 1 and pattern[0]["shortfall_cents"] == 6 * 1760 and json.loads(pattern[0]["evidence"])["lines"] == 6
    assert kept == ["appealed", "lost"]
    # the appeal is won and recorded on the finding the biller worked: it counts once, there
    apply_status(store.conn, ids["CB0000200C017"], "resolved", "Paid on appeal", recovered_cents=1760, actor="biller")
    store.conn.commit()
    Detector(store, run_id="again").run()
    assert store.query("SELECT status, recovered_cents FROM findings WHERE finding_id=?", (ids["CB0000200C017"],))[0][:] == ("resolved", 1760)
    store.close()


def test_a_dismissed_pattern_stays_dismissed_when_other_amounts_arrive(tmp_path):
    store = build_store(tmp_path, [cl(100 + i, "99214", "223.65", "117.60") for i in range(25)] + [cl(200 + i, "99214", "223.65", "100.00", group=True) for i in range(5)]
                        + [cl(300 + i, "99214", "223.65", "100.50", group=True) for i in range(4)])
    Detector(store, run_id="one").run()
    [p] = store.query("SELECT finding_id FROM findings WHERE signal='SECOND_RATE'")
    apply_status(store.conn, p[0], "dismissed", "PA rate per contract", actor="manager")
    store.conn.commit()
    store.close()
    store = build_store(tmp_path, [cl(400 + i, "99214", "223.65", "100.50", group=True) for i in range(2)], pay_date="20260501")
    Detector(store, run_id="two").run()
    assert store.query("SELECT status FROM findings WHERE finding_id=?", (p[0],))[0][0] == "dismissed"
    store.close()


# --------------------------------------------------------------------------- what the dashboard serves
def api_client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import remitwatch.api as api_mod
    monkeypatch.setattr(api_mod, "DB_PATH", str(tmp_path / "s.sqlite"))
    monkeypatch.setattr(api_mod, "PASSWORD", "")
    monkeypatch.setattr(api_mod, "SHOW_PHI", False)
    return TestClient(api_mod.app)


def test_a_store_saved_before_the_hint_column_still_lists_findings(tmp_path, monkeypatch):
    store = build_store(tmp_path, base_visits() + [visit(300, "SMITH*JOHN****MI*W11100001", paid="0.00")], pay_date="20260415")
    Detector(store, run_id="t").run()
    store.conn.execute("ALTER TABLE findings DROP COLUMN hint")      # as the live copy is until the next analysis
    store.conn.commit()
    store.close()
    c = api_client(tmp_path, monkeypatch)
    res = c.get("/api/findings?category=denial")
    assert res.status_code == 200 and res.json()["items"] and res.json()["items"][0]["hint"] is None
    assert c.get("/api/findings.csv?category=denial").status_code == 200


def test_a_pattern_finding_shows_its_history_and_no_claim_sections(tmp_path, monkeypatch):
    store = build_store(tmp_path, [cl(i, "99214", "223.65", "127.80", cfi="MB") for i in range(1, 26)], payer="KY MEDICARE")
    store.close()
    store = build_store(tmp_path, [cl(100 + i, "99214", "223.65", "117.60", cfi="16") for i in range(25)], payer="HUMANA", pay_date="20260415")
    store.close()
    store = build_store(tmp_path, [cl(200 + i, "99214", "223.65", "100.00", cfi="16", dos="20260801") for i in range(6)], payer="HUMANA", pay_date="20260901")
    Detector(store, run_id="t").run()
    [(fid,)] = store.query("SELECT finding_id FROM findings WHERE signal='SECOND_RATE'")
    apply_status(store.conn, fid, "in_progress", "Asked the Humana rep", actor="manager")
    store.conn.commit()
    store.close()
    c = api_client(tmp_path, monkeypatch)
    listed = c.get("/api/findings?category=underpayment&status=all").json()["items"]
    assert [r["signal"] for r in listed] == ["SECOND_RATE"] and listed[0]["appeal_deadline"]
    detail = c.get(f"/api/findings/{fid}").json()
    assert "claim" not in detail and [e["to_status"] for e in detail["events"]] == ["in_progress"] and detail["packets"] == []
    assert len(detail["finding"]["evidence"]["claims"]) == 6
