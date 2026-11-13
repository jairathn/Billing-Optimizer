"""
Review of 418bad3, second batch: what counts as paid on a bill, line dates, and amounts people recorded.
Invented data only. """
from __future__ import annotations

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


def claim_denials(store):
    return [r["patient_control_number"] for r in store.query("SELECT patient_control_number FROM findings WHERE signal='CLAIM_DENIED_UNPAID'")]


# --------------------------------------------------------------------------- what counts as paid on the bill
def test_a_secondarys_share_after_a_whole_claim_denial_does_not_hide_it(tmp_path):
    """The primary denies the whole claim (CO-197, no authorization). The secondary pays its 20 percent ($23.52) on the
    same claim id. The primary's $94.08 is unpaid: the whole-claim denial must be listed."""
    store = build_store(tmp_path, base_visits() + [visit(300, WHO, paid="0.00", cas="CAS*CO*197*223.65~", status="4")], pay_date="20260415")
    store.close()
    store = build_store(tmp_path, [visit(300, WHO, paid="23.52", allowed="117.60", status="2")], pay_date="20260501", payer="HUMANA")
    Detector(store, run_id="t").run()
    print("CLAIM_DENIED_UNPAID listed for:", claim_denials(store))
    assert claim_denials(store) == ["CB0000300C017"], "a 20 percent secondary payment hides the primary's whole-claim denial"
    store.close()


def test_a_later_payment_of_another_code_does_not_hide_a_whole_claim_denial(tmp_path):
    """C017 (visit 99214 and destruction 17000) is denied whole (CO-197). The resubmission C024 carries only the 17000 and
    is paid. The visit was never paid: the claim's denial (or the visit's) must still be listed."""
    who = "OAK*KIM****MI*W55500399"
    whole = (f"CLP*CB0000300C017*4*373.65*0.00*0.00*12*PC300*11~NM1*QC*1*{who}~NM1*82*2*TEST PROVIDER*****XX*{NPI}~DTM*232*20260401~"
             "CAS*CO*197*373.65~" + svc("99214", "223.65", "0.00", None) + svc("17000", "150.00", "0.00", None))
    store = build_store(tmp_path, base_visits() + clean_visits(101, 25, "17000", "150.00", "60.00") + [whole], pay_date="20260415")
    store.close()
    only = (f"CLP*CB0000300C024*1*150.00*60.00*0.00*12*PC301*11~NM1*QC*1*{who}~NM1*82*2*TEST PROVIDER*****XX*{NPI}~DTM*232*20260401~"
            + svc("17000", "150.00", "60.00", "60.00", "CAS*CO*45*90.00~"))
    store = build_store(tmp_path, [only], pay_date="20260520")
    Detector(store, run_id="t").run()
    rows = store.query("SELECT patient_control_number, procedure_code, signal, status FROM findings WHERE patient_control_number LIKE 'CB0000300%'")
    print("findings on bill 300:", [tuple(r) for r in rows])
    assert any(r["status"] == "open" for r in rows), "the unpaid visit on a whole-claim denial is hidden by a later payment of another code"
    store.close()


# --------------------------------------------------------------------------- line dates
def nodate_claim(i, who, dos, paid, cas, status="1"):
    """A payer that puts the date of service on the claim (DTM*232) and not on each line (no DTM*472)."""
    allowed = f"AMT*B6*{paid}~" if float(paid) > 0 else ""
    return (f"CLP*CB{i:07d}C017*{status}*223.65*{paid}*0.00*12*PC{i}*11~NM1*QC*1*{who}~NM1*82*2*TEST PROVIDER*****XX*{NPI}~"
            f"DTM*232*{dos}~DTM*233*{dos}~SVC*HC:99214*223.65*{paid}**1~{cas}{allowed}")


def test_a_visit_on_another_date_does_not_close_a_denial_when_lines_carry_no_date(tmp_path):
    """January visit denied, March visit paid, same patient and code; the payer dates the claims, not the lines.
    They are two visits: the January denial must stay open."""
    store = build_store(tmp_path, base_visits() + [nodate_claim(300, WHO, "20260110", "0.00", "CAS*CO*16*223.65~")], pay_date="20260120")
    store.close()
    store = build_store(tmp_path, [nodate_claim(700, WHO, "20260305", "117.60", "CAS*CO*45*106.05~")], pay_date="20260320")
    Detector(store, run_id="t").run()
    r = denials(store)["CB0000300C017:99214:DENIED_MISSING_INFO"]
    print("January denial:", r["status"], r["service_date"], r["auto_note"])
    assert r["status"] == "open", f"the March visit's payment closed the January denial: {r['auto_note']}"
    store.close()


# --------------------------------------------------------------------------- PLB recoupment
def test_a_payment_recouped_by_offset_does_not_close(tmp_path):
    """Bill 700's payment is recouped on a later remittance by a provider-level offset (PLB WO with the claim id), the
    way many payers take money back instead of a status 22 reversal. Bill 300's denial must stay open."""
    from tests.test_remitwatch import remit
    store = build_store(tmp_path, base_visits() + [visit(300, WHO, paid="0.00")], pay_date="20260415")
    store.close()
    store = build_store(tmp_path, [visit(700, WHO)], pay_date="20260520")
    p = tmp_path / "r9999.RMT"
    p.write_bytes(remit(visit(800, "ASH*RO****MI*W55500304"), "0.00", isa_ctrl="000000099", seg_count=99, pay_date="20260610",
                        plb="PLB*1777777772*20261231*WO:CB0000700C017*117.60~"))
    store.ingest_835_paths([str(p)])
    print("plb rows:", [tuple(r) for r in store.query("SELECT reason_code, reference, amount_cents FROM plb")])
    Detector(store, run_id="t").run()
    r = denials(store)["CB0000300C017:99214:DENIED_MISSING_INFO"]
    print("bill 300:", r["status"], r["auto_note"])
    assert r["status"] == "open", f"closed by a payment the payer recouped: {r['auto_note']}"
    store.close()


# --------------------------------------------------------------------------- the same payer re-adjudicating as secondary
def test_a_primary_payment_readjudicated_as_secondary_does_not_close_as_primary(tmp_path):
    """Bill 700 is paid as primary, then the payer finds other insurance: it reverses the payment and re-pays the claim
    as secondary ($23.52, status 2). Bill 300's denial must not close on the primary payment that was reversed."""
    store = build_store(tmp_path, base_visits() + [visit(300, WHO, paid="0.00")], pay_date="20260415")
    store.close()
    store = build_store(tmp_path, [visit(700, WHO)], pay_date="20260520")
    store.close()
    store = build_store(tmp_path, [reversal(700, WHO), visit(700, WHO, paid="23.52", allowed="117.60", status="2")], pay_date="20260610")
    Detector(store, run_id="t").run()
    r = denials(store)["CB0000300C017:99214:DENIED_MISSING_INFO"]
    print("bill 300:", r["status"], r["auto_note"])
    assert r["status"] == "open", f"closed as primary-paid by a primary payment that was reversed: {r['auto_note']}"
    store.close()


# --------------------------------------------------------------------------- amounts and statuses people recorded
def test_an_amount_a_biller_recorded_on_an_automatic_closure_is_kept(tmp_path):
    """Bill 300's denial is closed automatically ($117.60). The biller checks the EOB and records what really came in,
    $50.00. The next run must keep the biller's amount."""
    store = build_store(tmp_path, base_visits() + [visit(300, WHO, paid="0.00")], pay_date="20260415")
    store.close()
    store = build_store(tmp_path, [visit(700, WHO)], pay_date="20260520")
    Detector(store, run_id="t1").run()
    r = denials(store)["CB0000300C017:99214:DENIED_MISSING_INFO"]
    assert r["status"] == "resolved"
    apply_status(store.conn, r["finding_id"], "resolved", "Checked the EOB: $50 after the offset", recovered_cents=5000, actor="biller")
    store.conn.commit()
    Detector(store, run_id="t2").run()
    r = denials(store)["CB0000300C017:99214:DENIED_MISSING_INFO"]
    print("after re-run:", r["status"], r["recovered_cents"], r["notes"])
    assert r["recovered_cents"] == 5000, f"the biller's $50.00 was replaced by {r['recovered_cents']}"
    store.close()


def test_lost_recorded_before_a_payment_on_another_claim_is_not_replaced(tmp_path):
    """The task's rule: whatever a person recorded (lost ...) is never replaced by an automatic closure.
    (docs/OPERATIONS.md says the opposite for a payment that arrives later; this test records what happens.)"""
    store = build_store(tmp_path, base_visits() + [visit(300, WHO, paid="0.00")], pay_date="20260415")
    Detector(store, run_id="t1").run()
    r = denials(store)["CB0000300C017:99214:DENIED_MISSING_INFO"]
    apply_status(store.conn, r["finding_id"], "lost", "Upheld on second-level appeal; written off", actor="biller")
    store.conn.commit()
    store.close()
    store = build_store(tmp_path, [visit(700, WHO)], pay_date="20260520")
    Detector(store, run_id="t2").run()
    r = denials(store)["CB0000300C017:99214:DENIED_MISSING_INFO"]
    print("after re-run:", r["status"], r["prev_status"], r["notes"], r["auto_note"])
    assert r["status"] == "lost"
    store.close()


# --------------------------------------------------------------------------- controls: these should hold
def test_control_reversal_in_a_later_file_than_the_repayment(tmp_path):
    store = build_store(tmp_path, base_visits() + [visit(300, WHO, paid="0.00")], pay_date="20260415")
    store.close()
    store = build_store(tmp_path, [visit(700, WHO)], pay_date="20260520")
    store.close()
    store = build_store(tmp_path, [visit(700, WHO)], pay_date="20260601")           # paid again
    store.close()
    store = build_store(tmp_path, [reversal(700, WHO)], pay_date="20260615")         # the first payment taken back later
    Detector(store, run_id="t").run()
    assert denials(store)["CB0000300C017:99214:DENIED_MISSING_INFO"]["status"] == "resolved"
    store.close()


def test_control_reversed_then_paid_by_another_payer_on_another_suffix(tmp_path):
    store = build_store(tmp_path, base_visits() + [visit(300, WHO, paid="0.00")], pay_date="20260415")
    store.close()
    store = build_store(tmp_path, [visit(700, WHO)], pay_date="20260520")
    store.close()
    store = build_store(tmp_path, [reversal(700, WHO), visit(700, WHO, paid="0.00", cas="CAS*CO*22*223.65~", status="4")], pay_date="20260610")
    store.close()
    store = build_store(tmp_path, [visit(700, WHO).replace("C017*1*", "C024*1*")], pay_date="20260701", payer="HUMANA")
    Detector(store, run_id="t").run()
    r = denials(store)["CB0000300C017:99214:DENIED_MISSING_INFO"]
    assert r["status"] == "resolved" and "C024" in r["auto_note"]
    assert claim_denials(store) == []
    store.close()


def test_control_whole_claim_skip_needs_primary_and_same_date(tmp_path):
    who = "OAK*KIM****MI*W55500399"
    store = build_store(tmp_path, base_visits() + [visit(300, who, paid="23.52", allowed="117.60", status="2"),
                                                   visit(301, who, dos="20260402")], pay_date="20260415")
    store.close()
    dup300 = visit(300, who, paid="0.00", cas="CAS*CO*18*223.65~", status="4").replace("C017*4*", "C024*4*")
    dup301 = visit(301, who, paid="0.00", cas="CAS*CO*18*223.65~", status="4").replace("C017*4*", "C024*4*")
    store = build_store(tmp_path, [dup300, dup301.replace("DTM*232*20260402", "DTM*232*20260401").replace("DTM*472*20260402", "DTM*472*20260401")],
                        pay_date="20260520")
    Detector(store, run_id="t").run()
    assert sorted(claim_denials(store)) == ["CB0000300C024", "CB0000301C024"]
    store.close()


def test_control_duplicate_whose_original_was_taken_back_stays_open(tmp_path):
    store = build_store(tmp_path, base_visits() + [visit(300, WHO)], pay_date="20260415")
    store.close()
    store = build_store(tmp_path, [visit(700, WHO, paid="0.00", cas="CAS*OA*18*223.65~")], pay_date="20260520")
    store.close()
    store = build_store(tmp_path, [reversal(300, WHO), visit(300, WHO, paid="0.00", cas="CAS*CO*50*223.65~", status="4")], pay_date="20260601")
    Detector(store, run_id="t").run()
    r = denials(store)["CB0000700C017:99214:DENIED_DUPLICATE_UNPAID"]
    assert r["status"] == "open"
    store.close()
