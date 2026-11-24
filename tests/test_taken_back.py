"""Money a payer took back: a service the takeback left unpaid is listed, whatever the amount; a reversal sent under
another of the payer's ids cancels the payment it reverses; Medicare's J1 entries are read.

- A recoupment (PLB WO or FB) naming a claim, which the ledger applies since the money-accounting batch, could leave a
  service with no payment at all and no item: TAKEBACK listed only PLB WO entries of $500 or more. The owner's rule is no
  dollar floor: such a service is listed as work (paid, then taken back: find out why, then dispute or bill the right
  payer), with the payer's dispute window counted from the remittance that took the money back. A recoupment that leaves
  the service paid by another payment (a double payment recovered) is not work, and a recoupment naming no claim keeps
  its listing of $500 or more.
- The ledger paired a reversal (CLP02 22) with the payment it cancels by payer id: a payer that reverses under another
  of its ids left the payment standing. The claim's payer is any id and name seen together on that claim.
- Medicare's J1 entries (non-reimbursable) were not read. Medicare sends one beside its reversal and new payment of a
  claim (a 13-digit number, spaces, the claim id), withholding what the new payment adds to the one it reverses, or the
  whole new payment. Read where it names a claim, as the ledger reads WO and FB.

Everything here is invented: names, member numbers, claim ids, payer claim numbers, traces and amounts. Each test runs on
both kinds of store: the server's file and the serverless image held in memory."""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch.codes import appeal_rule, window_end  # noqa: E402
from remitwatch.detect import Detector  # noqa: E402
from tests.review3.r3helpers import clm, sv, visit_denied_line, visit_paid, visit_reversed  # noqa: E402
from tests.test_features import clean_visits  # noqa: E402
from tests.test_takebacks_and_recoveries import OTHER, World, kind, standing  # noqa: E402,F401

KY = {"payer": "KY MEDICARE", "payer_id": "MCRKY"}
BILL = "CB0000300"
TODAY = "2026-09-20"            # the run's day (pinned: the dispute windows below are open on it)


def run(w: World, run_id: str) -> Detector:
    det = Detector(w.store, run_id=run_id, today=TODAY)
    det.run()
    return det


def open_items(w: World, pcn_like: str, code: str) -> list[dict]:
    """The items still listed (open, not stale) for a bill's code."""
    return [r for r in w.rows(f"patient_control_number LIKE '{pcn_like}%' AND procedure_code='{code}' AND status='open' "
                              "AND COALESCE(stale,0)=0")]


def taken_back(w: World) -> list[dict]:
    """The items listing a service a takeback left unpaid (TAKEBACK on a line)."""
    return w.rows("signal='TAKEBACK' AND line_key IS NOT NULL")


def paid_then_recouped(w: World, cents: str = "117.60", payer: str = "ANTHEM", payer_id: str = "ANTHM") -> None:
    """C024 is paid $117.60 on 2026-05-20; on 2026-08-03 the payer recoups `cents` naming C024 from a check that pays
    another patient."""
    w.remit(clean_visits(1, 25, "99214", "223.65", "117.60"), "20260301", payer=payer, payer_id=payer_id)
    w.remit([visit_paid(f"{BILL}C024", payer_ctl="PC300B")], "20260520", payer=payer, payer_id=payer_id)
    w.remit([visit_paid("CB0000900C017", who=OTHER, payer_ctl="PC900")], "20260803", payer=payer, payer_id=payer_id,
            plb=f"PLB*1777777772*20261231*WO:87654321 {BILL}C024*{cents}~")


# --------------------------------------------------------------------------- 1. a service a takeback left unpaid
def test_a_service_a_small_recoupment_left_unpaid_is_listed_as_work_with_the_dispute_window(tmp_path, kind):
    w = World(tmp_path, kind)
    paid_then_recouped(w)
    det = run(w, "r1")
    assert standing(det, f"{BILL}C024") == 0
    got = taken_back(w)
    assert len(got) == 1, f"a service a $117.60 recoupment left unpaid has no item: {[r['signal'] for r in w.rows()]}"
    r = got[0]
    assert (r["patient_control_number"], r["procedure_code"], r["category"]) == (f"{BILL}C024", "99214", "fees")
    assert r["status"] == "open" and r["work_state"] == "work_now", (r["work_state"], r["state_basis"])
    assert r["shortfall_cents"] == 11760 and r["payment_date"] == "2026-08-03"
    assert "taken back by" in r["title"] and "2026-08-03" in r["title"], r["title"]
    assert "find out why, then dispute or bill the right payer" in r["title"], r["title"]
    rule = appeal_rule(r["payer_group"], r["payer_kind"], None)
    assert r["appeal_deadline"] == window_end("2026-08-03", rule), (r["appeal_deadline"], r["deadline_basis"])
    assert "Counted from the remittance that took the money back" in (r["deadline_basis"] or "")
    assert "taken back by" in (r["state_basis"] or ""), r["state_basis"]
    ev = json.loads(r["evidence"])
    assert ev["taken_back"]["cents"] == 11760 and ev["taken_back"]["date"] == "2026-08-03"
    assert ev["taken_back"]["paid"]["date"] == "2026-05-20" and ev["taken_back"]["paid"]["cents"] == 11760
    assert "C024" in ev["reference"]
    w.store.close()


def test_a_g2211_payment_taken_back_is_not_listed(tmp_path, kind):
    """The payer pays G2211 beside the visit and recoups that $12.00 later: G2211 is never pursued (as its denials are
    not), so the recoupment makes no item."""
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99213", "146.00", "60.00"), "20260301")
    w.remit([clm(f"{BILL}C024", "1", "161.00", "72.00", sv("99213", "146.00", "60.00", 1, "CAS*CO*45*86.00~", allowed="60.00", mods="25")
                 + sv("G2211", "15.00", "12.00", 1, "CAS*CO*45*3.00~", allowed="12.00"), payer_ctl="PC1")], "20260520")
    w.remit([visit_paid("CB0000900C017", who=OTHER, payer_ctl="PC900")], "20260803", plb=f"PLB*1777777772*20261231*WO:87654321 {BILL}C024*12.00~")
    run(w, "r1")
    assert taken_back(w) == []
    w.store.close()


def test_a_double_payment_recovered_is_not_listed(tmp_path, kind):
    """C024 and C031 each pay the visit ($117.60, 2026-05-20 and 2026-06-10); the payer recoups C031's payment on
    2026-08-03. The visit stays paid by C024: nothing is owed, nothing is listed."""
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99214", "223.65", "117.60"), "20260301")
    w.remit([visit_paid(f"{BILL}C024", payer_ctl="PC300B")], "20260520")
    w.remit([visit_paid(f"{BILL}C031", payer_ctl="PC300C")], "20260610")
    w.remit([visit_paid("CB0000900C017", who=OTHER, payer_ctl="PC900")], "20260803",
            plb=f"PLB*1777777772*20261231*WO:87654321 {BILL}C031*117.60~")
    det = run(w, "r1")
    assert standing(det, f"{BILL}C031") == 0 and standing(det, f"{BILL}C024") == 11760
    assert taken_back(w) == [] and w.rows("signal='TAKEBACK'") == []
    w.store.close()


def test_a_recoupment_of_an_extra_payment_beside_a_restatement_is_not_listed(tmp_path, kind):
    """C017's visit is paid $70.00 (2026-03-10). On 2026-05-15 the payer answers C017 again: it restates the earlier payment
    in pieces paid nothing (previously paid B13, its cuts and the coinsurance, each a line of the code) and pays $56.00 more
    on a line of its own; on 2026-07-27 it recoups that $56.00 naming C017. The visit stays paid by the first payment: the
    restatement's pieces are no units of their own, and nothing is listed."""
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99213", "90.00", "75.00"), "20260301")
    w.remit([clm(f"{BILL}C017", "1", "90.00", "70.00", sv("99213", "90.00", "70.00", 1, "CAS*CO*253*1.50~CAS*CO*45*15.00~CAS*PR*2*3.50~",
                                                            allowed="75.00", mods="25"), payer_ctl="PC1")], "20260310")
    pieces = "".join(sv("99213", amt, "0.00", 1, f"CAS*{grp}*{rc}*{amt}~", mods="25")
                     for grp, rc, amt in (("CO", "B13", "10.00"), ("CO", "253", "1.50"), ("PR", "2", "3.50"), ("CO", "45", "15.00")))
    w.remit([clm(f"{BILL}C017", "1", "90.00", "56.00", sv("99213", "60.00", "56.00", 1, "CAS*CO*253*1.20~CAS*PR*2*2.80~", allowed="60.00", mods="25")
                 + pieces, payer_ctl="PC1")], "20260515")
    w.remit([visit_paid("CB0000900C017", who=OTHER, payer_ctl="PC900")], "20260727", plb=f"PLB*1777777772*20261231*WO:Q{BILL}C017/123456789012345*56.00~")
    det = run(w, "r1")
    assert standing(det, f"{BILL}C017") == 7000
    assert taken_back(w) == [], [(r["title"], r["shortfall_cents"]) for r in taken_back(w)]
    w.store.close()


def test_a_recoupment_after_the_service_was_billed_again_on_another_claim_is_not_listed(tmp_path, kind):
    """C024 pays the pathology (88305) $65.00; the practice voids it and bills the service again on another bill as its two
    parts (26 and TC), which the payer denies; then the payer recoups the $65.00. The new claim's answer is the work: no
    item for the recoupment."""
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "88305", "66.00", "65.00"), "20260301")
    w.remit([clm(f"{BILL}C024", "1", "66.00", "65.00", sv("88305", "66.00", "65.00", 1, "CAS*CO*45*1.00~", allowed="65.00"), payer_ctl="PC1")],
            "20260420")
    w.remit([clm("CB0000301C017", "1", "66.00", "0.00", sv("88305", "32.00", "0.00", 1, "CAS*CO*234*32.00~", mods="TC")
                 + sv("88305", "34.00", "0.00", 1, "CAS*CO*234*34.00~", mods="26"), payer_ctl="PC2")], "20260515")
    w.remit([visit_paid("CB0000900C017", who=OTHER, payer_ctl="PC900")], "20260522", plb=f"PLB*1777777772*20261231*WO:Q{BILL}C024/123456789012345*65.00~")
    det = run(w, "r1")
    assert standing(det, f"{BILL}C024") == 0
    assert taken_back(w) == [], [(r["title"], r["shortfall_cents"]) for r in taken_back(w)]
    w.store.close()


def test_a_recoupment_after_the_payer_answered_the_service_again_is_listed_by_that_answer_only(tmp_path, kind):
    """C024 is paid $117.60 (2026-05-20); on 2026-06-15 the payer answers the visit again on C031 and denies it (no
    reversal sent); on 2026-08-03 it recoups the $117.60. The denial is the item to work; the recoupment adds no second
    item."""
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99214", "223.65", "117.60"), "20260301")
    w.remit([visit_paid(f"{BILL}C024", payer_ctl="PC300B")], "20260520")
    w.remit([visit_denied_line(f"{BILL}C031", payer_ctl="PC300D")], "20260615")
    w.remit([visit_paid("CB0000900C017", who=OTHER, payer_ctl="PC900")], "20260803", plb=f"PLB*1777777772*20261231*WO:87654321 {BILL}C024*117.60~")
    run(w, "r1")
    assert taken_back(w) == []
    assert [r["signal"] for r in open_items(w, BILL, "99214")] == ["DENIED_MISSING_INFO"]
    w.store.close()


def test_a_denial_paid_then_taken_back_is_one_item_the_denial_named_with_the_takeback(tmp_path, kind):
    """C017 is denied for authorization (2026-04-15); the corrected claim C024 is paid $117.60 (2026-05-20); on 2026-08-03
    the payer recoups that payment naming C024. One item: the denial, listed as work again (the payment taken back pays
    nothing), the takeback named on it; no second item for the takeback."""
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99214", "223.65", "117.60"), "20260301")
    w.remit([visit_denied_line(f"{BILL}C017", carc="197")], "20260415")
    w.remit([visit_paid(f"{BILL}C024", payer_ctl="PC300B")], "20260520")
    w.remit([visit_paid("CB0000900C017", who=OTHER, payer_ctl="PC900")], "20260803", plb=f"PLB*1777777772*20261231*WO:87654321 {BILL}C024*117.60~")
    run(w, "r1")
    assert taken_back(w) == []
    rows = open_items(w, BILL, "99214")
    assert [r["signal"] for r in rows] == ["DENIED_AUTH"], [(r["signal"], r["status"]) for r in rows]
    r = rows[0]
    assert r["work_state"] == "work_now", (r["work_state"], r["state_basis"])
    assert "then taken back by" in (r["hint"] or "") and json.loads(r["evidence"])["taken_back"]["date"] == "2026-08-03", r["hint"]
    w.store.close()


def test_a_claim_a_recoupment_took_partly_keeps_its_recoupments_own_listing_alone(tmp_path, kind):
    """C024 pays the visit ($117.60) and the procedure ($520.00); the payer recoups $600.00 naming C024: the visit's whole
    payment and $482.40 of the procedure's (a repricing of the claim). The recoupment keeps its own listing, once; the visit
    gets no item of its own beside it."""
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99214", "223.65", "117.60") + clean_visits(101, 25, "17311", "900.00", "520.00"), "20260301")
    w.remit([clm(f"{BILL}C024", "1", "1123.65", "637.60", sv("99214", "223.65", "117.60", 1, "CAS*CO*45*106.05~", allowed="117.60")
                 + sv("17311", "900.00", "520.00", 1, "CAS*CO*45*380.00~", allowed="520.00"), payer_ctl="PC300B")], "20260520")
    w.remit([visit_paid("CB0000900C017", who=OTHER, payer_ctl="PC900")], "20260803", plb=f"PLB*1777777772*20261231*WO:87654321 {BILL}C024*600.00~")
    run(w, "r1")
    rows = w.rows("signal='TAKEBACK'")
    assert [(r["line_key"] is None, r["shortfall_cents"]) for r in rows] == [(True, 60000)], [(r["title"], r["shortfall_cents"]) for r in rows]
    w.store.close()


def test_a_large_recoupment_naming_a_claim_is_listed_once_by_its_service(tmp_path, kind):
    """A $600.00 recoupment naming C024 (paid $600.00) leaves the service unpaid: one item, on the service; not a second
    one for the recoupment itself."""
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "17311", "900.00", "600.00"), "20260301")
    w.remit([clm(f"{BILL}C024", "1", "900.00", "600.00", sv("17311", "900.00", "600.00", 1, "CAS*CO*45*300.00~", allowed="600.00"),
                 payer_ctl="PC300B")], "20260520")
    w.remit([visit_paid("CB0000900C017", who=OTHER, payer_ctl="PC900")], "20260803",
            plb=f"PLB*1777777772*20261231*WO:87654321 {BILL}C024*600.00~")
    run(w, "r1")
    rows = w.rows("signal='TAKEBACK'")
    assert [(r["line_key"] is not None, r["shortfall_cents"]) for r in rows] == [(True, 60000)], \
        [(r["title"], r["shortfall_cents"]) for r in rows]
    w.store.close()


def test_a_large_recoupment_naming_no_claim_keeps_its_listing(tmp_path, kind):
    w = World(tmp_path, kind)
    paid_then_recouped(w)
    w.remit([visit_paid("CB0000901C017", who=OTHER, payer_ctl="PC901")], "20260810", plb="PLB*1777777772*20261231*WO:90000000000017*640.00~")
    run(w, "r1")
    rows = [r for r in w.rows("signal='TAKEBACK' AND line_key IS NULL")]
    assert [(r["shortfall_cents"], r["payment_date"]) for r in rows] == [(64000, "2026-08-10")]
    w.store.close()


def test_a_service_paid_again_after_the_takeback_is_closed_with_what_came(tmp_path, kind):
    """Listed after the recoupment; the corrected claim C031 is paid $117.60 on 2026-09-14: the item closes as paid, the
    payment its recovery."""
    w = World(tmp_path, kind)
    paid_then_recouped(w)
    run(w, "r1")
    fid = taken_back(w)[0]["finding_id"]
    w.remit([visit_paid(f"{BILL}C031", payer_ctl="PC300D")], "20260914")
    run(w, "r2")
    r = w.rows(f"finding_id='{fid}'")[0]
    assert r["status"] == "resolved" and r["recovered_cents"] == 11760 and r["recovered_date"] == "2026-09-14", \
        (r["status"], r["recovered_cents"], r["auto_note"], r["stale"])
    w.store.close()


def test_a_takeback_the_payer_answered_again_is_listed_by_that_answer_only(tmp_path, kind):
    """After the recoupment the payer answers the visit again on C031 and denies it (2026-09-14): the denial is the item
    to work, listed once; the takeback adds no second item."""
    w = World(tmp_path, kind)
    paid_then_recouped(w)
    w.remit([visit_denied_line(f"{BILL}C031", payer_ctl="PC300D")], "20260914")
    run(w, "r1")
    assert taken_back(w) == []
    assert [r["signal"] for r in open_items(w, BILL, "99214")] == ["DENIED_MISSING_INFO"]
    w.store.close()


# --------------------------------------------------------------------------- 2. a reversal under another of the payer's ids
def test_a_reversal_under_another_of_the_payers_ids_takes_the_payment_back_and_reopens_its_closure(tmp_path, kind):
    """C017 is denied on 2026-04-15 and C024 paid $117.60 on 2026-05-20 (ANTHEM, payer id ANTHM), which closes the denial.
    On 2026-06-10 ANTHEM reverses C024's payment under another of its payer ids (00660): the payment is taken back and the
    denial reopens."""
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99214", "223.65", "117.60"), "20260301")
    w.remit([visit_denied_line(f"{BILL}C017")], "20260415")
    w.remit([visit_paid(f"{BILL}C024", payer_ctl="PC300B")], "20260520")
    run(w, "r1")
    assert w.items()[f"{BILL}C017:99214:DENIED_MISSING_INFO"]["status"] == "resolved"
    w.remit([visit_reversed(f"{BILL}C024", payer_ctl="PC300B")], "20260610", payer_id="00660")
    det = run(w, "r2")
    assert standing(det, f"{BILL}C024") == 0, "the reversal under the payer's other id left the payment standing"
    item = w.items()[f"{BILL}C017:99214:DENIED_MISSING_INFO"]
    assert item["status"] == "open" and item["recovered_cents"] is None, (item["status"], item["auto_note"])
    assert det.not_on_file["lines"] == 0
    w.store.close()


def test_another_payers_reversal_of_the_same_amount_takes_nothing(tmp_path, kind):
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99214", "223.65", "117.60"), "20260301")
    w.remit([visit_paid(f"{BILL}C024", payer_ctl="PC300B")], "20260520")
    w.remit([visit_reversed(f"{BILL}C024", payer_ctl="PC300X")], "20260610", payer="BRIGHTPATH HEALTH", payer_id="BRGHT")
    det = run(w, "r1")
    assert standing(det, f"{BILL}C024") == 11760
    w.store.close()


# --------------------------------------------------------------------------- 4. Medicare's J1 entries
J1_REF = "1234567890123      CB0000400C017"          # the layout on file: a 13-digit number, six spaces, the claim id


def medicare_paid_reversed_denied(w: World) -> None:
    """Medicare pays C017's visit $60.00 (2026-05-01); on 2026-06-10 it reverses that payment and denies the visit
    (CO-97), carrying the $60.00 forward (FB, negative), which it recovers on 2026-06-28 (WO)."""
    w.remit(clean_visits(1, 25, "99213", "90.00", "77.00"), "20260301", **KY)
    w.remit([clm("CB0000400C017", "1", "90.00", "60.00", sv("99213", "90.00", "60.00", 1, "CAS*CO*45*13.00~CAS*PR*2*15.00~CAS*CO*253*2.00~",
                                                             allowed="77.00"), payer_ctl="MC400A")], "20260501", **KY)
    w.remit([clm("CB0000400C017", "22", "-90.00", "-60.00", sv("99213", "-90.00", "-60.00", -1, "CAS*CO*45*-13.00~CAS*PR*2*-15.00~CAS*CO*253*-2.00~"),
                 payer_ctl="MC400A"),
             clm("CB0000400C017", "1", "90.00", "0.00", sv("99213", "90.00", "0.00", 1, "CAS*CO*97*90.00~"), payer_ctl="MC400B")],
            "20260610", plb=f"PLB*1777777772*20261231*FB:{J1_REF}*-60.00~", **KY)
    w.remit([visit_paid("CB0000900C017", who=OTHER, payer_ctl="PC900", code="99213", charge="90.00", paid="60.00")], "20260628",
            plb="PLB*1777777772*20261231*WO:123456789012345    CB0000400C017*60.00~", **KY)


def test_a_j1_withholding_the_whole_new_payment_leaves_the_visit_unpaid_and_listed(tmp_path, kind):
    """2026-07-26: Medicare reverses its denial and pays the visit $61.00, and withholds the whole $61.00 (J1). Nothing
    stands paid by Medicare: the visit is listed once, as paid then taken back."""
    w = World(tmp_path, kind)
    medicare_paid_reversed_denied(w)
    w.remit([clm("CB0000400C017", "22", "-90.00", "0.00", sv("99213", "-90.00", "0.00", -1, "CAS*CO*97*-90.00~"), payer_ctl="MC400B"),
             clm("CB0000400C017", "1", "90.00", "61.00", sv("99213", "90.00", "61.00", 1, "CAS*CO*45*12.00~CAS*PR*2*15.00~CAS*CO*253*2.00~",
                                                            allowed="78.00"), payer_ctl="MC400C")],
            "20260726", plb=f"PLB*1777777772*20261231*J1:{J1_REF}*61.00~", **KY)
    det = run(w, "r1")
    assert standing(det, "CB0000400C017") == 0, "Medicare's J1 was not read: the payment it withheld stands"
    got = taken_back(w)
    assert len(got) == 1 and got[0]["shortfall_cents"] == 6100 and got[0]["payment_date"] == "2026-07-26", \
        [(r["title"], r["shortfall_cents"]) for r in got]
    assert [r["signal"] for r in open_items(w, "CB0000400", "99213")] == ["TAKEBACK"], \
        [(r["signal"], r["status"]) for r in w.rows("procedure_code='99213' AND patient_control_number LIKE 'CB0000400%'")]
    w.store.close()


def test_a_j1_withholding_what_the_new_payment_adds_takes_each_lines_increase(tmp_path, kind):
    """2026-07-26: Medicare reverses C017 (99214 $90.00, 17000 $50.00) and pays it again (99214 $117.60, 17000 $60.00),
    withholding the $37.60 the new payment adds (J1). Each line keeps what it had: 99214 $90.00, 17000 $50.00. The visit
    and the procedure stay paid: nothing is listed."""
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99214", "223.65", "117.60") + clean_visits(101, 25, "17000", "150.00", "60.00"), "20260301", **KY)

    def two(status, s, paid_v, paid_p, ctl):
        sign = "-" if s < 0 else ""
        u = -1 if s < 0 else 1
        return clm("CB0000400C017", status, f"{sign}373.65", f"{sign}{paid_v + paid_p:.2f}",
                   sv("99214", f"{sign}223.65", f"{sign}{paid_v:.2f}", u, f"CAS*CO*45*{sign}{223.65 - paid_v:.2f}~")
                   + sv("17000", f"{sign}150.00", f"{sign}{paid_p:.2f}", u, f"CAS*CO*45*{sign}{150 - paid_p:.2f}~"), payer_ctl=ctl)
    w.remit([two("1", 1, 90.00, 50.00, "MC400A")], "20260501", **KY)
    w.remit([two("22", -1, 90.00, 50.00, "MC400A"), two("1", 1, 117.60, 60.00, "MC400B")], "20260726",
            plb=f"PLB*1777777772*20261231*J1:{J1_REF}*37.60~", **KY)
    det = run(w, "r1")
    by_code = {l["procedure_code"]: det.standing.get(l["line_key"], 0) for l in det.lines
               if l["patient_control_number"] == "CB0000400C017" and l.get("claim_status_code") == "1" and l.get("payment_date") == "2026-07-26"}
    assert by_code == {"99214": 9000, "17000": 5000}, by_code
    assert taken_back(w) == []
    w.store.close()


def test_a_j1_recovering_a_balance_carried_forward_takes_nothing_more(tmp_path, kind):
    """2026-06-10: Medicare reverses C017 ($117.60) and denies it, carrying the $117.60 forward (FB, negative), never
    recovered by a WO. 2026-07-26: it reverses the denial and pays $117.60 again, withholding $117.60 (J1): the carried
    balance recovered. The visit stands paid once ($117.60), and nothing is listed."""
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99214", "223.65", "117.60"), "20260301", **KY)
    w.remit([visit_paid("CB0000400C017", payer_ctl="MC400A")], "20260501", **KY)
    w.remit([visit_reversed("CB0000400C017", payer_ctl="MC400A"), visit_denied_line("CB0000400C017", carc="97", payer_ctl="MC400B")],
            "20260610", plb=f"PLB*1777777772*20261231*FB:{J1_REF}*-117.60~", **KY)
    w.remit([clm("CB0000400C017", "22", "-223.65", "0.00", sv("99214", "-223.65", "0.00", -1, "CAS*CO*97*-223.65~"), payer_ctl="MC400B"),
             visit_paid("CB0000400C017", payer_ctl="MC400C")], "20260726", plb=f"PLB*1777777772*20261231*J1:{J1_REF}*117.60~", **KY)
    det = run(w, "r1")
    assert standing(det, "CB0000400C017") == 11760
    assert taken_back(w) == []
    w.store.close()
