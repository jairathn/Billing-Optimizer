"""
Review m1 of the fx-exports fix round (fix wave F3b, commits a2120cd..39fbd06, merged at 8cb51f6): failing tests with
invented data only (the repository's builders: tests/ema_fixtures, tests/test_ema_wiring, tests/test_837_sends,
tests/test_fx_exports). No real name, member id, claim id or date of any real claim.

Run against a tree:  REVIEW_WT=<worktree at 8cb51f6> python3 -m pytest -q <this file>
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

os.environ.setdefault("REMITWATCH_ALLOW_OPEN", "1")

import pytest  # noqa: E402

from remitwatch.detect import Detector  # noqa: E402
from tests.test_837_sends import KINDS, clm837, send  # noqa: E402
from tests.test_fx_exports import BC, _ema_store, _volume, ema_env  # noqa: E402,F401

D = dt.date


# ------------------------------------------------------------------------------------------------------------------------
# 1. The before-send bridge (_unanswered_before_send) takes a family member's open EMA claim through the member id's one
#    remitted name ('weak'), when the claim's own EMA claim was answered.
def _family_bridge_world(tmp_path, kind, *, parent_remitted: bool):
    """Kiddo Sample's visit of March 2 (a 99214 at $200) was billed on an 837 only (claim F.claim_id(91), sent March 20 to
    Blue Cross under the subscriber id MEMBER000091, which is her parent Bravo Sample's: a dependent's 837 carries the
    subscriber's id). Her own EMA claim 91 was charged March 3 and Blue Cross paid it April 10 on a paper check (no 835
    for it; nothing ties it to F.claim_id(91): no A/R pair, no bill map, and the content is not one claim's).
    Her parent was seen the same day for the same code and charge: EMA claim 92, charged March 3 on a paper claim that no
    837 or weekly export lists, never answered, $200 open. parent_remitted: Blue Cross's remittance of an earlier visit of
    the parent (February) names him under MEMBER000091, so that member id has one remitted name: the parent's."""
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import charge, denial_world, pay, remit as raw_remit
    s, lg = _ema_store(tmp_path, kind)
    kid = "Sample, Kiddo"
    rows = [charge(F.link_no(91), cpt="99214", amount=200, patient=kid, dob=D(2015, 6, 1), posted=D(2026, 3, 3)),
            pay(F.link_no(91), "PAPER-91", 110, cpt="99214", patient=kid, dob=D(2015, 6, 1), posted=D(2026, 4, 10)),
            charge(F.link_no(92), cpt="99214", amount=200, patient=1, posted=D(2026, 3, 3)),
            # Blue Cross still posts in EMA (Production's never-answered check needs a posting of the payer's lately)
            charge(F.link_no(94), cpt="99212", amount=100, patient=3, svc=D(2026, 3, 9), posted=D(2026, 3, 10)),
            pay(F.link_no(94), "PAPER-94", 60, cpt="99212", patient=3, svc=D(2026, 3, 9), posted=D(2026, 4, 20))]
    denial_world(s, lg, tmp_path, rows)
    _volume(s)
    if parent_remitted:
        raw_remit(s, trace="BCMEM91", paid_on="2026-02-20", payer="BLUE CROSS TEST",
                  claims=[{"pcn": F.claim_id(95), "status": "1", "member": "MEMBER000091", "last": "SAMPLE", "first": "BRAVO",
                           "lines": [("2026-02-02", "99213", 150, 80, [("CO", "45", 70)])]}])
    send(s, tmp_path, [clm837(F.claim_id(91), [("99214", "200.00", "20260302")], payer=BC, member="MEMBER000091")], "20260320")
    det = Detector(s, run_id="r1", today="2026-05-03")
    det.run()
    assert det.ema_errors == [], det.ema_errors
    lg.close()
    got = [dict(r) for r in s.query("SELECT patient_control_number, ema_link, source, evidence FROM findings "
                                    "WHERE signal='NO_ANSWER' AND COALESCE(stale,0)=0")]
    s.close()
    carried = [g for g in got if g["patient_control_number"] == F.claim_id(91)]
    g8 = [g for g in got if g["ema_link"] == F.link_no(92) and g["source"] != "837"]
    return carried, g8, got


@pytest.mark.parametrize("kind", KINDS)
def test_a_dependents_claim_never_takes_the_subscribers_open_ema_claim_by_the_member_ids_remitted_name(tmp_path, kind, ema_env):
    """The claim's patient is named by nothing of its own: the one name the remittances give MEMBER000091 is the parent's
    (a subscriber's id covers the family). The bridge must not take the parent's open EMA claim 92 for the child's claim
    (the reviewers' r1-4 and r2-6: with nothing naming the claim's patient, its own EMA claim may be an answered one, and
    the one left another patient's). Production's check keeps listing the parent's EMA claim 92 on its own.
    On 8cb51f6 the member id's one remitted name ('weak') 'confirms' the lone candidate: the child's claim carries the
    parent's EMA claim 92 (bridge unanswered_before_send) and Production's check leaves it."""
    from tests import ema_fixtures as F
    carried, g8, got = _family_bridge_world(tmp_path, kind, parent_remitted=True)
    assert not any(g["ema_link"] == F.link_no(92) for g in carried), (
        f"the child's claim carries the parent's open EMA claim 92: {[(g['patient_control_number'], g['ema_link']) for g in carried]}")
    assert len(g8) == 1, f"the parent's never-answered EMA claim 92 is not listed on its own: {[(g['source'], g['ema_link']) for g in got]}"


@pytest.mark.parametrize("kind", KINDS)
def test_control_with_no_remitted_name_the_bridge_takes_nothing(tmp_path, kind, ema_env):
    """Control (passes on 8cb51f6): the same world without the parent's earlier remittance. Nothing names the claim's
    patient, so the bridge takes nothing and Production's check lists EMA claim 92 on its own."""
    from tests import ema_fixtures as F
    carried, g8, got = _family_bridge_world(tmp_path, kind, parent_remitted=False)
    assert not any(g["ema_link"] == F.link_no(92) for g in carried), carried
    assert len(g8) == 1, [(g["source"], g["ema_link"]) for g in got]


# ------------------------------------------------------------------------------------------------------------------------
# 2. A corrected submission left in EMA's print batch is answered by the very denial it corrects when EMA posts that
#    denial a few days after the remittance's payment date (r1-1 / r2-1 with EMA's usual posting lag).
def _corrected_lag_world(tmp_path, kind, *, ema_posted):
    """Dana's visit of March 2 (a 99214 at $200, EMA claim 70) was billed to Blue Cross as claim C001 of bill 70, sent March 5.
    Blue Cross denied it on its 835 of March 28 (CO-16, information missing); EMA posted that $0 answer with its reason on
    `ema_posted` (EMA posts an ERA when it is received and worked: often days after the payment date). The biller corrected
    the claim; EMA made it as submission C002 of the same bill and left it in its print batch: never sent. Today May 25;
    EMA's weekly exports reach May 24. (tests/test_fx_exports.py's _corrected_in_print_batch, with the posting lag.)"""
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import adj, charge, denial_world, pay, remit as raw_remit
    from tests.test_workable import ema
    s, lg = _ema_store(tmp_path, kind)
    who = "Fixit, Dana"
    rows = [charge(F.link_no(70), cpt="99214", amount=200, patient=who, posted=D(2026, 3, 3), status="Denied"),
            pay(F.link_no(70), "PAPER-70", 0, cpt="99214", patient=who, posted=ema_posted, status="Denied"),
            adj(F.link_no(70), "CO-16", 200, cpt="99214", patient=who, posted=ema_posted, status="Denied")]
    denial_world(s, lg, tmp_path, rows)
    raw_remit(s, trace="BCDEN0070", paid_on="2026-03-28", payer="BLUE CROSS TEST",
              claims=[{"pcn": F.claim_id(70), "status": "4", "member": "MEMBER000070", "last": "FIXIT", "first": "DANA",
                       "lines": [("2026-03-02", "99214", 200, 0, [("CO", "16", 200)])]}])
    bc = dict(payer=F.PAYERS["bcbs"], payer_id="BCT01", patient_id="EMA070")
    ema(s.conn, F.claim_id(70), service_date="2026-03-02", bill_created="2026-03-03", charges_cents=20000, **bc)
    ema(s.conn, F.claim_id(70), family="claim_status", claim_status="Denied", claim_submission_date="2026-03-05",
        service_date="2026-03-02", **bc)
    corrected = F.claim_id(70, 2)
    ema(s.conn, corrected, service_date="2026-03-02", bill_created="2026-03-03", charges_cents=20000, **bc)
    ema(s.conn, corrected, family="claim_status", claim_status="Batched for Printing", service_date="2026-03-02", **bc)
    ema(s.conn, F.claim_id(75), service_date="2026-05-22", bill_created="2026-05-24", payer=F.PAYERS["bcbs"], payer_id="BCT01",
        patient_id="EMA075")
    s.conn.commit()
    det = Detector(s, run_id="r1", today="2026-05-25")
    det.run()
    assert det.ema_errors == [], det.ema_errors
    lg.close()
    got = [(r["signal"], r["patient_control_number"] or f"link:{r['ema_link']}") for r in s.query(
        "SELECT signal, patient_control_number, ema_link FROM findings WHERE COALESCE(stale,0)=0 "
        "AND signal IN ('NO_ANSWER','REJECTED_PRE_PAYER','NOT_SENT') AND (patient_control_number=? OR ema_link=?)",
        (corrected, F.link_no(70)))]
    counts = {k: v for k, v in det.ema_counts.items() if k.startswith("export")}
    s.close()
    return got, counts


@pytest.mark.parametrize("kind", KINDS)
def test_a_corrected_claim_in_the_print_batch_is_listed_though_ema_posted_its_denial_days_after_the_835(tmp_path, kind, ema_env):
    """The corrected claim C002 never went out: the denial answered C001, not it. On 8cb51f6 a print-batch claim keeps the
    'after' day (fx-exports-r1-1, r2-1), but for a later submission with no submission date that day is the bill's last
    answer on the 835s plus one (SENT_AGAIN: March 29); EMA's posting of the same denial on March 31 is 'after' it, so
    the denial answers the correction it asked for, and nothing lists the claim (export_answered_in_ema)."""
    got, counts = _corrected_lag_world(tmp_path, kind, ema_posted=D(2026, 3, 31))
    assert got, f"the corrected claim, never sent, is on no list: {counts}"


@pytest.mark.parametrize("kind", KINDS)
def test_control_the_same_corrected_claim_is_listed_when_ema_posts_the_denial_on_the_835s_day(tmp_path, kind, ema_env):
    """Control (passes on 8cb51f6, the fixer's own test): EMA posts the denial the day of the 835."""
    got, counts = _corrected_lag_world(tmp_path, kind, ema_posted=D(2026, 3, 28))
    assert got, f"the corrected claim, never sent, is on no list: {counts}"


# ------------------------------------------------------------------------------------------------------------------------
# 3. _fold_into: a fold recorded on an item that then yields is lost (A folded into B by the exports' one-visit rule, B
#    then yields to the 837 pass's C): the self-check that keeps an item no rule explains keeps A listed beside C.
def _chain_world(tmp_path, kind):
    """Sample Bravo's visit of March 2 (a 99214 at $200) billed three times to Blue Cross, under member MEMBER000031:
    A = F.claim_id(31), in EMA's weekly exports (EMA's patient EMA031, submitted March 3, its 837 sent March 3);
    B = F.claim_id(34), made again: in EMA's exports too (the same EMA patient EMA031, submitted March 8, 837 March 8);
    C = F.claim_id(32), made again once more: only the 837s know it (sent March 13), and the A/R pairs it with EMA claim 32
    ('Sample, Bravo Q', charged $210 on March 13). No payer answered any of them. EMA's reports name EMA031's patient
    'Sample, Bravo Q' (another claim of hers, paired by the A/R). Run t1 knows A only (listed); B and C arrive before t2."""
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import charge, denial_world, load, remit as raw_remit
    from tests.test_fx_exports import _tie, _tie_claim
    from tests.test_workable import ema
    s, lg = _ema_store(tmp_path, kind)
    bravo = F.PATIENTS[1][0]
    tie_rows, tie_ar = _tie(33, bravo)
    own_rows = [charge(F.link_no(32), cpt="99214", amount=210, patient=bravo, posted=D(2026, 3, 13), status="Payment Pending")]
    own_ar = [F.ar_row(claim=F.claim_id(32), link=F.link_no(32), status="Payment Pending", current=210, svc=D(2026, 3, 2),
                       patient=bravo, cpt="99214")]
    denial_world(s, lg, tmp_path, tie_rows + own_rows, ar_rows=tie_ar)
    _volume(s)
    raw_remit(s, trace="BCMEM31", paid_on="2026-02-20", payer="BLUE CROSS TEST",
              claims=[{"pcn": F.claim_id(30), "status": "1", "member": "MEMBER000031", "last": "SAMPLE", "first": "BRAVO",
                       "lines": [("2026-02-02", "99213", 150, 80, [("CO", "45", 70)])]}])
    _tie_claim(s, 33, "EMA031")
    bc = dict(payer=F.PAYERS["bcbs"], payer_id="BCT01", patient_id="EMA031")
    ema(s.conn, F.claim_id(31), service_date="2026-03-02", bill_created="2026-03-03", charges_cents=20000, **bc)
    ema(s.conn, F.claim_id(31), family="claim_status", claim_status="Submitted", claim_submission_date="2026-03-03",
        service_date="2026-03-02", **bc)
    s.conn.commit()
    send(s, tmp_path, [clm837(F.claim_id(31), [("99214", "200.00", "20260302")], payer=BC, member="MEMBER000031")], "20260303")
    from tests.test_837_sends import _chased
    t1 = _chased(s, "t1", today="2026-05-03")
    # B and C arrive: the exports list B; the 837s show B and C sent; the A/R names C's patient
    ema(s.conn, F.claim_id(34), service_date="2026-03-02", bill_created="2026-03-08", charges_cents=20000, **bc)
    ema(s.conn, F.claim_id(34), family="claim_status", claim_status="Submitted", claim_submission_date="2026-03-08",
        service_date="2026-03-02", **bc)
    s.conn.commit()
    send(s, tmp_path, [clm837(F.claim_id(34), [("99214", "200.00", "20260302")], payer=BC, member="MEMBER000031")], "20260308")
    send(s, tmp_path, [clm837(F.claim_id(32), [("99214", "210.00", "20260302")], payer=BC, member="MEMBER000031")], "20260313")
    load(s, lg, tmp_path, [], tie_ar + own_ar)
    t2 = _chased(s, "t2", today="2026-05-03")
    lg.close()
    return s, t1, t2


@pytest.mark.parametrize("kind", KINDS)
def test_a_visit_billed_three_times_is_chased_once_after_its_first_bill_was_listed(tmp_path, kind, ema_env):
    """Run t1 lists A. In run t2 the exports' one-visit rule hands A's visit to B (recorded as a fold on B's item), and the
    exports' yield then hands B's to C (B's item is dropped and folded into C). A's fold went with B's item: no item this
    run lists A as folded, so the self-check that keeps an item no rule explains (selfcheck.check_vanished) keeps A listed:
    the visit is chased twice (A and C). Expected: only C is listed."""
    from tests import ema_fixtures as F
    s, t1, t2 = _chain_world(tmp_path, kind)
    assert F.claim_id(31) in t1, sorted(t1)
    mine = sorted(k for k in t2 if k in (F.claim_id(31), F.claim_id(34), F.claim_id(32)))
    folded = json.loads(t2[F.claim_id(32)]["evidence"]).get("folded") if F.claim_id(32) in t2 else None
    s.close()
    assert mine == [F.claim_id(32)], f"listed {mine} (C's folded list: {folded})"


# ------------------------------------------------------------------------------------------------------------------------
# 4. _twins_by_name narrows a claim's send-day twins to one; the twins rule's per-day count (_sent_that_day) then refuses
#    the one left, so a claim EMA shows answered is listed (on the real copy: the 4 claims A17 calls paid, $311).
def _two_bills_one_day_world(tmp_path, kind, *, bert_elsewhere=True):
    """Two patients' visits of March 2 with the same content (a 99214 at $200) went to Blue Cross the same day, March 20,
    each on its own 837: Annie's F.claim_id(61) (member MEMBER000061; her earlier remittance names her TWINTEST, ANNIE) and
    Bert's F.claim_id(62) (member MEMBER000062), which Blue Cross answered on its 835 of April 12 (paid $110).
    EMA charged both on the send day: EMA claim 61 (EMA spells her 'Twintest, Annie Marie'), paid April 10, and EMA claim 62
    (Bert), paid April 12. Bert's other visit went out under member MEMBER000099 (the A/R pairs it with his EMA claim 65).
    Dora's EMA claim 64 of the same content (charged March 3, paid) is held by her own bill. Today May 3."""
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import charge, denial_world, pay, remit as raw_remit
    from tests.test_fx_exports import _tie
    s, lg = _ema_store(tmp_path, kind)
    annie, bert = "Twintest, Annie Marie", "Twintest, Bert"
    rows = [charge(F.link_no(61), cpt="99214", amount=200, patient=annie, posted=D(2026, 3, 20)),
            pay(F.link_no(61), "PAPER-61", 110, cpt="99214", patient=annie, posted=D(2026, 4, 10)),
            charge(F.link_no(62), cpt="99214", amount=200, patient=bert, posted=D(2026, 3, 20)),
            pay(F.link_no(62), "BC-62", 110, cpt="99214", patient=bert, posted=D(2026, 4, 12)),
            charge(F.link_no(64), cpt="99214", amount=200, patient="Othertest, Dora", posted=D(2026, 3, 3)),
            pay(F.link_no(64), "PAPER-64", 110, cpt="99214", patient="Othertest, Dora", posted=D(2026, 3, 25))]
    ar = [F.ar_row(claim=F.claim_id(164), link=F.link_no(64), status="Payment Pending", current=0, svc=D(2026, 3, 2),
                   patient="Othertest, Dora", cpt="99214")]
    if bert_elsewhere:
        r, a_ = _tie(65, bert)
        rows, ar = rows + r, ar + a_
    denial_world(s, lg, tmp_path, rows, ar_rows=ar)
    _volume(s)
    raw_remit(s, trace="BCM61", paid_on="2026-02-20", payer="BLUE CROSS TEST",
              claims=[{"pcn": F.claim_id(221), "status": "1", "member": "MEMBER000061", "last": "TWINTEST", "first": "ANNIE",
                       "lines": [("2026-02-01", "99213", 150, 80, [("CO", "45", 70)])]}])
    raw_remit(s, trace="BC0062", paid_on="2026-04-12", payer="BLUE CROSS TEST",
              claims=[{"pcn": F.claim_id(62), "status": "1", "member": "MEMBER000062", "last": "TWINTEST", "first": "BERT",
                       "lines": [("2026-03-02", "99214", 200, 110, [("CO", "45", 90)])]}])
    if bert_elsewhere:
        send(s, tmp_path, [clm837(F.claim_id(65), [("99212", "100.00", "20260202")], payer=BC, member="MEMBER000099")], "20260203")
    send(s, tmp_path, [clm837(F.claim_id(61), [("99214", "200.00", "20260302")], payer=BC, member="MEMBER000061"),
                       clm837(F.claim_id(62), [("99214", "200.00", "20260302")], payer=BC, member="MEMBER000062")], "20260320")
    det = Detector(s, run_id="r1", today="2026-05-03")
    det.run()
    assert det.ema_errors == [], det.ema_errors
    lg.close()
    got = {r["patient_control_number"]: r["ema_link"] for r in s.query(
        "SELECT patient_control_number, ema_link FROM findings WHERE signal='NO_ANSWER' AND COALESCE(stale,0)=0 "
        "AND patient_control_number IN (?, ?)", (F.claim_id(61), F.claim_id(62)))}
    counts = {k: v for k, v in det.ema_counts.items() if k.startswith("837")}
    s.close()
    return got, counts


@pytest.mark.parametrize("kind", KINDS)
def test_a_claim_whose_send_day_twins_were_all_answered_is_not_listed_when_its_patient_narrows_them(tmp_path, kind, ema_env):
    """Two bills of this content went out on March 20, and EMA charged two claims of it that day; both were answered. So
    whichever of them is Annie's, her claim was answered (the twins rule: 'several, all answered, answer it whichever is
    its own'). On 8cb51f6 _twins_by_name sets Bert's EMA claim aside for Annie's claim (his bills carry another member id)
    and keeps hers alone; _ema_claims_found then compares that one twin with the two bills sent that day
    (_sent_that_day: 2 > 1) and refuses it, so the claim EMA shows paid is listed as never answered."""
    from tests import ema_fixtures as F
    got, counts = _two_bills_one_day_world(tmp_path, kind)
    assert F.claim_id(61) not in got, f"Annie's claim is listed though both EMA claims of its send day were paid: {got} ({counts})"


@pytest.mark.parametrize("kind", KINDS)
def test_control_without_bert_elsewhere_both_twins_stay_and_answer_the_claim(tmp_path, kind, ema_env):
    """Control (passes on 8cb51f6): nothing says Bert is another member's patient, so both twins stay; two bills, two
    twins, both answered: Annie's claim is answered whichever twin is hers, and is not listed."""
    from tests import ema_fixtures as F
    got, counts = _two_bills_one_day_world(tmp_path, kind, bert_elsewhere=False)
    assert F.claim_id(61) not in got, (got, counts)


# ------------------------------------------------------------------------------------------------------------------------
# 5. _print_batch_paid: a payment on the claim's own EMA claim counts for every part of the service (_OWN), whatever its
#    day: the reading's (26) payment makes the technical part's (TC) corrected claim, never paid, a 'duplicate'.
def _part_rebill_world(tmp_path, kind, *, tc_paid: bool):
    """Erin's biopsy of March 2 was billed to Blue Cross on bill 70, claim C001 (sent March 5): the reading 88305-26 at $90
    and the technical part 88305-TC at $60. Blue Cross's 835 of March 28 paid the reading $40 (CO-45 $50) and denied the
    technical part (CO-16, information missing; tc_paid: paid it $25 too). EMA posted the same that day on its claim 70
    (EMA's lines carry no modifiers: one line, 88305, $150), which the A/R pairs with C001. The biller corrected the
    technical part as submission C002 of the bill (88305-TC at $60), which EMA left in its print batch: never sent. EMA's
    weekly exports reach May 24; today May 25."""
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import adj, charge, denial_world, pay, remit as raw_remit
    from tests.test_workable import ema
    s, lg = _ema_store(tmp_path, kind)
    who = "Partly, Erin"
    rows = [charge(F.link_no(70), cpt="88305", amount=90, patient=who, posted=D(2026, 3, 3)),
            charge(F.link_no(70), cpt="88305", amount=60, patient=who, posted=D(2026, 3, 3)),
            pay(F.link_no(70), "BC-70", 65 if tc_paid else 40, cpt="88305", patient=who, posted=D(2026, 3, 28)),
            adj(F.link_no(70), "CO-45", 50, cpt="88305", patient=who, posted=D(2026, 3, 28), contractual=True)]
    if not tc_paid:
        rows.append(adj(F.link_no(70), "CO-16", 60, cpt="88305", patient=who, posted=D(2026, 3, 28)))
    ar = [F.ar_row(claim=F.claim_id(70), link=F.link_no(70), status="Payment Pending", current=0, svc=D(2026, 3, 2),
                   patient=who, cpt="88305")]
    denial_world(s, lg, tmp_path, rows, ar_rows=ar)
    tc_line = ("2026-03-02", "88305", 60, 25 if tc_paid else 0, [("CO", "45", 35)] if tc_paid else [("CO", "16", 60)], {"modifiers": '["TC"]'})
    raw_remit(s, trace="BC-70", paid_on="2026-03-28", payer="BLUE CROSS TEST",
              claims=[{"pcn": F.claim_id(70), "status": "1", "member": "MEMBER000070", "last": "PARTLY", "first": "ERIN",
                       "lines": [("2026-03-02", "88305", 90, 40, [("CO", "45", 50)], {"modifiers": '["26"]'}), tc_line]}])
    bc = dict(payer=F.PAYERS["bcbs"], payer_id="BCT01", patient_id="EMA070")
    ema(s.conn, F.claim_id(70), cpt="88305", modifiers='["26"]', service_date="2026-03-02", bill_created="2026-03-03", charges_cents=9000, **bc)
    ema(s.conn, F.claim_id(70), cpt="88305", modifiers='["TC"]', service_date="2026-03-02", bill_created="2026-03-03", charges_cents=6000, **bc)
    ema(s.conn, F.claim_id(70), family="claim_status", claim_status="Submitted", claim_submission_date="2026-03-05",
        service_date="2026-03-02", cpt="88305", **bc)
    corrected = F.claim_id(70, 2)
    ema(s.conn, corrected, cpt="88305", modifiers='["TC"]', service_date="2026-03-02", bill_created="2026-03-03", charges_cents=6000, **bc)
    ema(s.conn, corrected, family="claim_status", claim_status="Batched for Printing", service_date="2026-03-02", cpt="88305", **bc)
    ema(s.conn, F.claim_id(75), service_date="2026-05-22", bill_created="2026-05-24", payer=F.PAYERS["bcbs"], payer_id="BCT01",
        patient_id="EMA075")
    s.conn.commit()
    det = Detector(s, run_id="r1", today="2026-05-25")
    det.run()
    assert det.ema_errors == [], det.ema_errors
    lg.close()
    got = [(r["signal"], r["patient_control_number"]) for r in s.query(
        "SELECT signal, patient_control_number FROM findings WHERE COALESCE(stale,0)=0 "
        "AND signal IN ('NO_ANSWER','REJECTED_PRE_PAYER','NOT_SENT') AND patient_control_number=?", (corrected,))]
    counts = {k: v for k, v in det.ema_counts.items() if k.startswith("export")}
    s.close()
    return got, counts


@pytest.mark.parametrize("kind", KINDS)
def test_a_print_batch_rebill_of_the_technical_part_is_not_a_duplicate_of_the_readings_payment(tmp_path, kind, ema_env):
    """The technical part was never paid: its corrected claim is money owed that goes out only if someone sends it. The
    reading's $40 on EMA claim 70 is no payment of the technical part (the fix round's own rule: 'a payment that still
    stands ... for the service's own part'). On 8cb51f6 _print_batch_paid counts any payment on the claim's own EMA claim
    for every part (_OWN), whatever its day, and drops the corrected claim as a duplicate (export_print_batch_paid)."""
    got, counts = _part_rebill_world(tmp_path, kind, tc_paid=False)
    assert got, f"the technical part's corrected claim, never paid and never sent, is on no list: {counts}"


@pytest.mark.parametrize("kind", KINDS)
def test_control_a_print_batch_rebill_of_a_paid_technical_part_is_a_duplicate(tmp_path, kind, ema_env):
    """Control (passes on 8cb51f6): Blue Cross paid the technical part too: the print-batch claim is a duplicate."""
    got, counts = _part_rebill_world(tmp_path, kind, tc_paid=True)
    assert not got, (got, counts)


def _ma_pair_world(tmp_path, kind):
    """(the real copy's pattern: 27 of the fix round's 37 newly listed claims) Two Medicare Advantage members, each with
    one name on the remittances under their own member id, were seen March 2 for the same service (a 99214 at $200), and
    both 837s went out March 20 (F.claim_id(61), member MEMBER000061, Annie; F.claim_id(62), member MEMBER000062, Bert),
    each a 99214 at $200 and a 17110 at $150. EMA charged both on the send day: Annie's EMA claim 61 and Bert's EMA claim
    62, and the payer answered both April 3: the 99214 paid $110 (CO-45 $90), the 17110 denied (CO-197, no authorization;
    EMA wrote it off); no 835 of either reached the store (a feed gap). Each patient's earlier visit (EMA claims 65 and
    66, paired by the A/R with F.claim_id(65) and F.claim_id(66)) went out under that patient's own member id."""
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import charge, denial_world, pay, remit as raw_remit
    from tests.test_fx_exports import _tie
    s, lg = _ema_store(tmp_path, kind)
    annie, bert = "Twintest, Annie", "Twintest, Bert"
    from tests.test_ema_wiring import adj
    rows = []
    for n, who in ((61, annie), (62, bert)):
        rows += [charge(F.link_no(n), cpt="99214", amount=200, patient=who, posted=D(2026, 3, 20)),
                 charge(F.link_no(n), cpt="17110", amount=150, patient=who, posted=D(2026, 3, 20)),
                 pay(F.link_no(n), f"MA-{n}", 110, cpt="99214", patient=who, posted=D(2026, 4, 3)),
                 adj(F.link_no(n), "CO-45", 90, cpt="99214", patient=who, posted=D(2026, 4, 3), contractual=True),
                 pay(F.link_no(n), f"MA-{n}", 0, cpt="17110", patient=who, posted=D(2026, 4, 3)),
                 adj(F.link_no(n), "CO-197", 150, cpt="17110", patient=who, posted=D(2026, 4, 3))]
    ar = []
    for n, who in ((65, annie), (66, bert)):
        r, a_ = _tie(n, who)
        rows, ar = rows + r, ar + a_
    denial_world(s, lg, tmp_path, rows, ar_rows=ar)
    _volume(s)
    for n, first in ((61, "ANNIE"), (62, "BERT")):
        raw_remit(s, trace=f"BCM{n}", paid_on="2026-02-20", payer="BLUE CROSS TEST",
                  claims=[{"pcn": F.claim_id(160 + n), "status": "1", "member": f"MEMBER0000{n}", "last": "TWINTEST", "first": first,
                           "lines": [("2026-02-01", "99213", 150, 80, [("CO", "45", 70)])]}])
    send(s, tmp_path, [clm837(F.claim_id(65), [("99212", "100.00", "20260202")], payer=BC, member="MEMBER000061"),
                       clm837(F.claim_id(66), [("99212", "100.00", "20260202")], payer=BC, member="MEMBER000062")], "20260203")
    two = [("99214", "200.00", "20260302"), ("17110", "150.00", "20260302")]
    send(s, tmp_path, [clm837(F.claim_id(61), two, payer=BC, member="MEMBER000061"),
                       clm837(F.claim_id(62), two, payer=BC, member="MEMBER000062")], "20260320")
    det = Detector(s, run_id="r1", today="2026-05-03")
    det.run()
    assert det.ema_errors == [], det.ema_errors
    lg.close()
    got = {r["patient_control_number"]: [ln[0] for ln in json.loads(r["evidence"]).get("lines") or []] for r in s.query(
        "SELECT patient_control_number, evidence FROM findings WHERE signal='NO_ANSWER' AND COALESCE(stale,0)=0 "
        "AND patient_control_number IN (?, ?)", (F.claim_id(61), F.claim_id(62)))}
    denied = sorted((r["procedure_code"], r["ema_link"]) for r in s.query(
        "SELECT procedure_code, ema_link FROM findings WHERE signal='EMA_DENIED' AND COALESCE(stale,0)=0 AND status='open'"))
    counts = {k: v for k, v in det.ema_counts.items() if k.startswith("837")}
    s.close()
    return got, counts, denied


@pytest.mark.parametrize("kind", KINDS)
def test_two_members_claims_sent_the_same_day_are_answered_by_their_own_paid_ema_claims(tmp_path, kind, ema_env):
    """Each claim's own EMA claim is plain: its patient's other bills went out under the claim's member id, and the other
    twin's under another member id. Two bills, two twins, one each, both paid: neither claim is unanswered. On 8cb51f6
    each claim keeps its own twin alone (_twins_by_name) and the per-day count (two bills sent that day, one twin) then
    refuses it, so both paid claims are listed as never answered."""
    got, counts, denied = _ma_pair_world(tmp_path, kind)
    assert got == {}, (f"claims whose own EMA claims the payer answered are listed as never answered: {got} ({counts}); "
                       f"EMA's denial items: {denied}")


# ------------------------------------------------------------------------------------------------------------------------
# 6. _spare_ema_claims counts an EMA claim charged AFTER the claim went out as one that 'may be the claim's own', and so
#    sets the claim's real send-day twin aside (on the real copy: 0ae7a771e27022193105b292, A17 undecided, listed though
#    its lone twin and both 'spare' claims were answered).
def _late_decoy_world(tmp_path, kind, *, decoy_day):
    """Annie's claim F.claim_id(61) (a 99214 at $200, visit March 2) went to Blue Cross March 20; only the 837s know it, and
    her member's earlier remittance names her TWINTEST, ANNIE. EMA charged one claim of that content on the send day (EMA
    claim 62, which EMA spells 'Twintest, Annie Marie'), paid April 10. Dora's claim of the same content (EMA claim 64,
    charged March 3, paid) is held by her own bill. Carl's visit of the same content was charged on `decoy_day` (EMA claim
    63), held by no bill (the same world as tests/test_fx_exports.py's _spelled_twin_world with decoy=True, but the decoy
    charged after the send)."""
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import charge, denial_world, pay, remit as raw_remit
    s, lg = _ema_store(tmp_path, kind)
    twin = "Twintest, Annie Marie"
    rows = [charge(F.link_no(62), cpt="99214", amount=200, patient=twin, posted=D(2026, 3, 20)),
            pay(F.link_no(62), "PAPER-62", 110, cpt="99214", patient=twin, posted=D(2026, 4, 10)),
            charge(F.link_no(64), cpt="99214", amount=200, patient="Othertest, Dora", posted=D(2026, 3, 3)),
            pay(F.link_no(64), "PAPER-64", 110, cpt="99214", patient="Othertest, Dora", posted=D(2026, 3, 25)),
            charge(F.link_no(63), cpt="99214", amount=200, patient="Othertest, Carl", posted=decoy_day)]
    ar = [F.ar_row(claim=F.claim_id(164), link=F.link_no(64), status="Payment Pending", current=0, svc=D(2026, 3, 2),
                   patient="Othertest, Dora", cpt="99214")]
    denial_world(s, lg, tmp_path, rows, ar_rows=ar)
    _volume(s)
    raw_remit(s, trace="BCM61", paid_on="2026-02-20", payer="BLUE CROSS TEST",
              claims=[{"pcn": F.claim_id(221), "status": "1", "member": "MEMBER000061", "last": "TWINTEST", "first": "ANNIE",
                       "lines": [("2026-02-01", "99213", 150, 80, [("CO", "45", 70)])]}])
    send(s, tmp_path, [clm837(F.claim_id(61), [("99214", "200.00", "20260302")], payer=BC, member="MEMBER000061")], "20260320")
    det = Detector(s, run_id="r1", today="2026-05-03")
    det.run()
    assert det.ema_errors == [], det.ema_errors
    lg.close()
    got = {r["patient_control_number"] for r in s.query(
        "SELECT patient_control_number FROM findings WHERE signal='NO_ANSWER' AND COALESCE(stale,0)=0 AND patient_control_number=?",
        (F.claim_id(61),))}
    counts = {k: v for k, v in det.ema_counts.items() if k.startswith("837")}
    s.close()
    return got, counts


@pytest.mark.parametrize("kind", KINDS)
def test_an_ema_claim_charged_after_the_send_never_sets_the_claims_send_day_twin_aside(tmp_path, kind, ema_env):
    """EMA posts a claim's charges before it sends it: Carl's EMA claim, charged two days after Annie's claim went out,
    cannot be the EMA claim of her bill. EMA claim 62 is the only EMA claim that can be hers, and it was paid: her claim
    is not listed. On 8cb51f6 _spare_ema_claims counts Carl's claim (no day limit), so _twins_by_name sets EMA claim 62
    aside for its spelling and the paid claim is listed as never answered."""
    from tests import ema_fixtures as F
    got, counts = _late_decoy_world(tmp_path, kind, decoy_day=D(2026, 3, 22))
    assert F.claim_id(61) not in got, f"listed though its only possible EMA claim was paid: {counts}"


@pytest.mark.parametrize("kind", KINDS)
def test_control_a_decoy_charged_before_the_send_still_sets_the_twin_aside(tmp_path, kind, ema_env):
    """Control (passes on 8cb51f6; the fix round's own rule): Carl's claim charged March 3, before the send, may be Annie's
    own, so the differently spelled twin is set aside and the claim stays listed."""
    from tests import ema_fixtures as F
    got, counts = _late_decoy_world(tmp_path, kind, decoy_day=D(2026, 3, 3))
    assert F.claim_id(61) in got, counts


# ------------------------------------------------------------------------------------------------------------------------
# 7. _print_batch_paid joins the claim's patient to another EMA claim by the keyed name alone: a son's bill still in the
#    print batch is dropped as a 'duplicate' of his father's paid visit (the same name: JR is dropped from the key).
def _namesake_world(tmp_path, kind, *, son_dob=D(1980, 6, 6), son_name="Testjones, Robert Jr"):
    """Robert Testjones (born 1950, EMA's patient EMA081S) and his son Robert Testjones Jr (born 1980, EMA081J) were both
    seen March 2 for a 99214 at $200, both with Blue Cross. The father's visit was billed and paid ($110, EMA claim 81,
    posted March 28). The son's visit was billed on F.claim_id(82), made April 20 (EMA claim 82, charged that day), and
    EMA left it in its print batch: it never went out. EMA's reports name each patient (another claim of each, paired by the A/R). Today May 25; EMA's weekly
    exports reach May 24."""
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import charge, denial_world, pay
    from tests.test_fx_exports import _tie, _tie_claim
    from tests.test_workable import ema
    s, lg = _ema_store(tmp_path, kind)
    father, son = "Testjones, Robert", son_name
    rows = [charge(F.link_no(81), cpt="99214", amount=200, patient=father, dob=D(1950, 5, 5), posted=D(2026, 3, 3)),
            pay(F.link_no(81), "BC-81", 110, cpt="99214", patient=father, dob=D(1950, 5, 5), posted=D(2026, 3, 28)),
            # the son's own EMA claim, charged the day his bill was made (EMA posts the charges, then batches the bill)
            charge(F.link_no(82), cpt="99214", amount=200, patient=son, dob=son_dob, posted=D(2026, 4, 20),
                   status="Batched for Printing")]
    ar = []
    for n, who, dob in ((181, father, D(1950, 5, 5)), (182, son, son_dob)):
        r, a_ = _tie(n, who, dob=dob)
        rows, ar = rows + r, ar + a_
    denial_world(s, lg, tmp_path, rows, ar_rows=ar)
    for n, pid in ((181, "EMA081S"), (182, "EMA081J")):
        _tie_claim(s, n, pid)
    bc = dict(payer=F.PAYERS["bcbs"], payer_id="BCT01", patient_id="EMA081J")
    ema(s.conn, F.claim_id(82), service_date="2026-03-02", bill_created="2026-04-20", charges_cents=20000, **bc)
    ema(s.conn, F.claim_id(82), family="claim_status", claim_status="Batched for Printing", service_date="2026-03-02", **bc)
    ema(s.conn, F.claim_id(75), service_date="2026-05-22", bill_created="2026-05-24", payer=F.PAYERS["bcbs"], payer_id="BCT01",
        patient_id="EMA075")
    s.conn.commit()
    det = Detector(s, run_id="r1", today="2026-05-25")
    det.run()
    assert det.ema_errors == [], det.ema_errors
    lg.close()
    got = [r["signal"] for r in s.query(
        "SELECT signal FROM findings WHERE COALESCE(stale,0)=0 AND signal IN ('NO_ANSWER','REJECTED_PRE_PAYER','NOT_SENT') "
        "AND patient_control_number=?", (F.claim_id(82),))]
    counts = {k: v for k, v in det.ema_counts.items() if k.startswith("export")}
    s.close()
    return got, counts


@pytest.mark.parametrize("kind", KINDS)
def test_a_sons_unsent_bill_is_no_duplicate_of_his_fathers_paid_visit(tmp_path, kind, ema_env):
    """The son's visit was never sent and never paid: his bill in the print batch is money owed that goes out only if
    someone sends it. On 8cb51f6 _print_batch_paid finds 'the patient's other EMA claim' by the keyed name alone (the
    father's, JR dropped), counts the father's $110 as a payment that stands on the son's visit, and drops the son's bill
    (export_print_batch_paid). The fix round's own one-visit rules refuse a name alone (r2-4: a name and birth date, or a
    name under one member id)."""
    got, counts = _namesake_world(tmp_path, kind)
    assert got, f"the son's unsent, unpaid bill is on no list: {counts}"


@pytest.mark.parametrize("kind", KINDS)
def test_control_a_son_of_another_name_keeps_his_unsent_bill_listed(tmp_path, kind, ema_env):
    """Control (passes on 8cb51f6): the same world with the son named Bobby: his unsent bill stays listed."""
    got, counts = _namesake_world(tmp_path, kind, son_name="Testjones, Bobby")
    assert got, counts
