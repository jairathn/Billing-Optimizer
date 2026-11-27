"""
Fix wave F3c (plans/briefs/2026-11-26_fixwave.md, section "F3c after F3a and F3b merge"): the two never-answered passes
together, once both are in the integration branch. F3a (the 837 pass) and F3b (EMA's weekly exports) each left needs on
the other's functions: plans/progress/fx-837_needs_tests.py, plans/progress/fx-exports_needs_save.py and the Needs lists
of plans/progress/fx-837.md and fx-exports.md. Their tests are ported here, with the review's no-name answers-1 test
(since-answers-tests/test_answers_review.py), which the 837's own patient makes decidable.

- An exports' charge row whose charge EMA does not give is priced at the payer's rate (the charge is unknown, not $0).
- An exports' claim not due yet is on the waiting view (meta feed:waiting_837), as the 837 pass's own claims are.
- One visit under two bills across the passes is chased once, the patient told by each claim's own evidence, the 837's
  own patient (READ_VERSION 2) included; family members, and a father and son of one name, stay apart.
- Work on an exports' item that yields follows to the newer bill's item when nobody worked that one; both stay listed,
  pointing to each other, when both were worked.
- An older exports' bill stays given way once the newer bill of the visit is answered as primary.

Every claim id, member number, name, amount and date here is invented (the repository's builders: tests/test_837_sends,
tests/test_fx_837, tests/test_fx_exports, tests/test_workable, tests/test_ema_wiring, tests/ema_fixtures). Each test that
runs detection runs on the server's store file and on the compact in-memory store the serverless cycle builds.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch.detect import WAITING_837_META, Detector  # noqa: E402
from tests.test_837_sends import KINDS, V, _chased, _remits, _store, _visits, answer, send  # noqa: E402
from tests.test_fx_837 import clm837  # noqa: E402
from tests.test_fx_exports import _ema_store, _rebill_world, ema_env  # noqa: E402,F401

D = dt.date


def _open(s, like: str) -> dict[str, dict]:
    return {r["patient_control_number"]: dict(r) for r in s.query(
        "SELECT * FROM findings WHERE signal IN ('NO_ANSWER','REJECTED_PRE_PAYER') AND COALESCE(stale,0)=0 "
        "AND patient_control_number LIKE ?", (like,))}


# ======================================================================================= the exports' unknown charge
@pytest.mark.parametrize("kind", KINDS)
def test_an_exports_charge_row_with_no_charge_given_is_priced_at_the_payers_rate(tmp_path, kind):
    """(F3a's need of F3b: live_items keeps None) EMA's charge export gives no charge on bill CB0000B91's 99214 row (the cell
    is blank, not $0.00). The charge is unknown, not nothing: the claim is chased at Anthem's rate for a 99214 ($117.60),
    its charge shown as not known."""
    from tests.test_workable import ema
    s = _store(tmp_path, kind)
    _remits(s, tmp_path, _visits(), "20261110")
    ema(s.conn, "CB0000B91C017", charges_cents=None)
    ema(s.conn, "CB0000B91C017", family="claim_status", claim_status="Submitted", claim_submission_date="2026-09-16")
    s.conn.commit()
    Detector(s, run_id="t1").run()
    got = [dict(r) for r in s.query("SELECT signal, charge_cents, shortfall_cents FROM findings "
                                    "WHERE patient_control_number='CB0000B91C017'")]
    assert got == [{"signal": "NO_ANSWER", "charge_cents": None, "shortfall_cents": 11760}], got
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_an_exports_claim_known_from_the_status_report_alone_keeps_its_unknown_charge(tmp_path, kind):
    """(The stopgap _emit_open_claim kept for status-only claims, now that live_items gives None) Bill CB0000B94 is in EMA's
    claim-status export alone, with no charge; bill CB0000B95 is there too, its status export giving the 99214's charge,
    $0.00. B94 is chased at Anthem's rate ($117.60, its charge not known); B95 was billed nothing and is not listed."""
    from tests.test_workable import ema
    s = _store(tmp_path, kind)
    _remits(s, tmp_path, _visits(), "20261110")
    ema(s.conn, "CB0000B94C017", family="claim_status", claim_status="Submitted", claim_submission_date="2026-09-16")
    ema(s.conn, "CB0000B95C017", family="claim_status", claim_status="Submitted", claim_submission_date="2026-09-16",
        charges_cents=0)
    s.conn.commit()
    det = Detector(s, run_id="t1")
    det.run()
    got = {r["patient_control_number"]: (r["charge_cents"], r["shortfall_cents"]) for r in s.query(
        "SELECT patient_control_number, charge_cents, shortfall_cents FROM findings WHERE patient_control_number LIKE 'CB0000B9%'")}
    assert got == {"CB0000B94C017": (None, 11760)}, got
    assert det.ema_counts["export_billed_nothing"] == 1, dict(det.ema_counts)
    s.close()


# ======================================================================================= the waiting view
@pytest.mark.parametrize("kind", KINDS)
def test_an_exports_claim_not_due_yet_is_on_the_waiting_view(tmp_path, kind):
    """(F3a's need of F3b: _note_waiting in run_ema_open_claims) Bill CB0000B92, which EMA's weekly exports list as Submitted
    November 1, is not due before Anthem's answer days run out, so no list chases it yet. The waiting view (meta
    feed:waiting_837, rows [claim id, payer group, sent, due, billed cents]) shows it with its due day, as it shows the 837
    pass's own claims."""
    from tests.test_workable import ema
    s = _store(tmp_path, kind)
    _remits(s, tmp_path, _visits(), "20261110")
    ema(s.conn, "CB0000B92C017", service_date="2026-10-30", bill_created="2026-11-01")
    ema(s.conn, "CB0000B92C017", family="claim_status", claim_status="Submitted", claim_submission_date="2026-11-01",
        service_date="2026-10-30")
    s.conn.commit()
    Detector(s, run_id="t1").run()
    w = json.loads(s.conn.execute("SELECT value FROM meta WHERE key=?", (WAITING_837_META,)).fetchone()[0] or "{}")
    assert [c[0] for c in w.get("claims") or []] == ["CB0000B92C017"], w
    assert w["claims"][0][1:] == ["Anthem / BCBS", "2026-11-01", "2026-12-01", 22365], w["claims"]
    assert not _open(s, "CB0000B92%")
    s.close()


# ======================================================================================= one visit across the passes
@pytest.mark.parametrize("kind", KINDS)
def test_one_visit_in_the_exports_and_again_under_a_new_bill_only_the_837s_know_is_chased_once(tmp_path, kind):
    """(answers-1 across the passes; the review's test, unchanged) EMA's weekly exports list bill CB0000M31 (C017, sent
    September 15, its 837 on file for member WDUP000002); the practice made the bill again as CB0000M32 and sent it September
    25 (only the 837s know it). Neither is answered (remittances to November 10). Both 837s name the patient (the subscriber,
    no patient loop): one person, one visit, chased once under the newer bill, which says the older bill went out before."""
    from tests.test_workable import ema
    s = _store(tmp_path, kind)
    member = "WDUP000002"
    _remits(s, tmp_path, _visits(), "20261110")
    ema(s.conn, "CB0000M31C017")
    ema(s.conn, "CB0000M31C017", family="claim_status", claim_status="Submitted", claim_submission_date="2026-09-15")
    s.conn.commit()
    send(s, tmp_path, [clm837("CB0000M31C017", V, member=member)], "20260915")
    send(s, tmp_path, [clm837("CB0000M32C017", V, member=member)], "20260925")
    found = _chased(s, "t1")
    mine = {k: v for k, v in found.items() if k.startswith("CB0000M3")}
    assert set(mine) == {"CB0000M32C017"}, sorted(mine)
    assert "CB0000M31C017" in (mine["CB0000M32C017"]["hint"] or ""), mine["CB0000M32C017"]["hint"]
    s.close()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("who", ["dependent", "father_and_son"])
def test_two_people_under_one_member_id_stay_two_visits_across_the_passes(tmp_path, kind, who):
    """(answers-1's guard, with the 837's own patient) The same two bills of one day's 99214 under member WDUP000003: the
    exports' bill CB0000M41 (sent September 15) is the subscriber's own visit (no patient loop). The 837-only bill CB0000M42
    (September 25) is another person's: a child (dependent: its patient loop names SAMPLE KID), or the subscriber's son of
    the same name (father_and_son: SAMPLE ROBERT both, the father born 1960 as subscriber, the son born 1990 in the patient
    loop: the keyed name agrees, the birth date does not). Two people's visits: both stay chased."""
    from tests.test_workable import ema
    s = _store(tmp_path, kind)
    member = "WDUP000003"
    _remits(s, tmp_path, _visits(), "20261110")
    ema(s.conn, "CB0000M41C017")
    ema(s.conn, "CB0000M41C017", family="claim_status", claim_status="Submitted", claim_submission_date="2026-09-15")
    s.conn.commit()
    if who == "dependent":
        sub, pat = ("SAMPLE", "PAT", None), ("SAMPLE", "KID", None)
    else:
        sub, pat = ("SAMPLE", "ROBERT", "19600101"), ("SAMPLE", "ROBERT", "19900101")
    send(s, tmp_path, [clm837("CB0000M41C017", V, member=member, subscriber=sub)], "20260915")
    send(s, tmp_path, [clm837("CB0000M42C017", V, member=member, subscriber=sub, patient=pat)], "20260925")
    found = _chased(s, "t1")
    assert {"CB0000M41C017", "CB0000M42C017"} <= set(found), sorted(found)
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_another_lesions_service_on_the_newer_bill_is_not_the_exports_service(tmp_path, kind):
    """(answers-1 across the passes: the visit key is detect._visit_line) The exports' bill CB0000M51 (sent September 15)
    bills a 17110 for one lesion; the practice's newer bill CB0000M52 (September 25, only the 837s know it) bills a 17110-59,
    another lesion's service of the same day. One person (both 837s name the subscriber), two services: the exports' item
    keeps its 17110 and the newer bill is chased for its own."""
    from tests.test_workable import ema
    s = _store(tmp_path, kind)
    member = "WDUP000005"
    _remits(s, tmp_path, _visits(), "20261110")
    ema(s.conn, "CB0000M51C017", cpt="17110", charges_cents=15000)
    ema(s.conn, "CB0000M51C017", family="claim_status", cpt="17110", claim_status="Submitted", claim_submission_date="2026-09-15")
    s.conn.commit()
    send(s, tmp_path, [clm837("CB0000M51C017", [("17110", "150.00", "20260914")], member=member)], "20260915")
    send(s, tmp_path, [clm837("CB0000M52C017", [("17110", "150.00", "20260914", "59")], member=member)], "20260925")
    found = _chased(s, "t1")
    assert {"CB0000M51C017", "CB0000M52C017"} <= set(found), sorted(found)
    assert [ln[0] for ln in json.loads(found["CB0000M51C017"]["evidence"])["lines"]] == ["17110"]
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_an_older_bill_only_the_837s_know_gives_way_to_the_exports_newer_bill_of_the_same_patient(tmp_path, kind):
    """(The other direction, F3a's _837_export_carriers, with the 837's own patient) The 837-only bill CB0000M61 went out
    September 15; the practice made the bill again as CB0000M62, which EMA's weekly exports list (sent September 25). Both
    837s name the subscriber under member WDUP000006: the exports' newer bill carries the visit, chased once."""
    from tests.test_workable import ema
    s = _store(tmp_path, kind)
    member = "WDUP000006"
    _remits(s, tmp_path, _visits(), "20261110")
    ema(s.conn, "CB0000M62C017")
    ema(s.conn, "CB0000M62C017", family="claim_status", claim_status="Submitted", claim_submission_date="2026-09-25")
    s.conn.commit()
    send(s, tmp_path, [clm837("CB0000M61C017", V, member=member)], "20260915")
    send(s, tmp_path, [clm837("CB0000M62C017", V, member=member)], "20260925")
    found = _chased(s, "t1")
    assert {k for k in found if k.startswith("CB0000M6")} == {"CB0000M62C017"}, sorted(found)
    s.close()


# ======================================================================================= work follows the visit
@pytest.mark.parametrize("kind", KINDS)
def test_work_on_an_export_item_follows_the_newer_bill_listed_before(tmp_path, kind, ema_env):
    """(fx-exports-r1-2, r2-5; plans/progress/fx-exports_needs_save.py) The first run lists both bills (nothing names their
    patient yet); a biller starts the exports' item with a note. EMA's reports naming the patient arrive; the second run finds
    one visit. The newer bill's item, listed before and worked by no one, carries the visit and the biller's Started and
    note; the exports' item is no longer listed."""
    from tests import ema_fixtures as F
    s, found = _rebill_world(tmp_path, kind, named=True, worked=True, two_runs=True)
    assert F.claim_id(31) not in found, sorted(found)
    y = found[F.claim_id(32)]
    assert y["status"] == "in_progress" and "no record of it" in (y["notes"] or ""), (y["status"], y["notes"])
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_work_on_an_export_item_stays_listed_when_the_newer_items_status_was_changed_and_set_back(tmp_path, kind, ema_env):
    """(The keep-for-work branch, narrowed) As above, but a second biller set the newer bill's item Started and back to To
    do, recording nothing. save() takes such an item for touched and hands it no one's work, so the exports' item keeps its
    Started and note, listed, pointing to the newer bill: the work is never left on an item no list shows."""
    from tests import ema_fixtures as F
    s, found = _rebill_world(tmp_path, kind, named=True, worked=True, two_runs=True, worked_newer="touched")
    assert {F.claim_id(31), F.claim_id(32)} <= set(found), sorted(found)
    x = found[F.claim_id(31)]
    assert x["status"] == "in_progress" and "no record of it" in (x["notes"] or ""), (x["status"], x["notes"])
    assert F.claim_id(32) in (x["hint"] or ""), x["hint"]
    s.close()


# ======================================================================================= the newer bill answered
@pytest.mark.parametrize("kind", KINDS)
def test_an_older_exports_bill_stays_given_way_once_the_newer_bill_of_the_visit_is_answered(tmp_path, kind):
    """(F3a's need of F3b: run_ema_open_claims' newest_for, the exports' side of fx-837-r2-6) One patient's visit of
    September 14 (99214) is on two bills EMA's weekly exports list: CB0000E81 (Submitted September 15, no answer) and the
    bill made again, CB0000E82 (Submitted September 25). Anthem denies E82 (CO-16, October 30). The visit's answer is on the
    newest bill: E81, the bill the practice replaced, is not chased as never answered beside E82's denial."""
    from tests.test_workable import ema
    s = _store(tmp_path, kind)
    for cid, day in (("CB0000E81C017", "2026-09-15"), ("CB0000E82C017", "2026-09-25")):
        ema(s.conn, cid, bill_created=day, patient_id="P0000E81")
        ema(s.conn, cid, family="claim_status", claim_status="Submitted", claim_submission_date=day, patient_id="P0000E81")
    s.conn.commit()
    _remits(s, tmp_path, [answer("CB0000E82C017", status="1", paid="0.00")], "20261030")
    _remits(s, tmp_path, _visits(), "20261110")
    Detector(s, run_id="t1").run()
    rows = [dict(r) for r in s.query("SELECT patient_control_number, signal FROM findings WHERE COALESCE(stale,0)=0 "
                                     "AND patient_control_number LIKE 'CB0000E8%'")]
    assert any(r["patient_control_number"] == "CB0000E82C017" for r in rows), "control: E82's denial is listed"
    assert not any(r["patient_control_number"] == "CB0000E81C017" and r["signal"] == "NO_ANSWER" for r in rows), rows
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_an_older_exports_bill_is_still_chased_when_the_newer_bill_answered_is_another_patients(tmp_path, kind):
    """(The guard of the test above) Two of EMA's patients each have a 99214 of September 14 on their own bill: CB0000E83
    (patient P0000E83, sent September 15, no answer) and CB0000E84 (patient P0000E84, sent September 25), which Anthem
    denied. Another patient's answer is no answer to E83: it stays chased."""
    from tests.test_workable import ema
    s = _store(tmp_path, kind)
    for cid, day, pid in (("CB0000E83C017", "2026-09-15", "P0000E83"), ("CB0000E84C017", "2026-09-25", "P0000E84")):
        ema(s.conn, cid, bill_created=day, patient_id=pid)
        ema(s.conn, cid, family="claim_status", claim_status="Submitted", claim_submission_date=day, patient_id=pid)
    s.conn.commit()
    _remits(s, tmp_path, [answer("CB0000E84C017", status="1", paid="0.00")], "20261030")
    _remits(s, tmp_path, _visits(), "20261110")
    found = _chased(s, "t1")
    assert "CB0000E83C017" in found, sorted(found)
    s.close()


# ======================================================================================= the 837's own patient
def _bc_volume(s) -> None:
    """Blue Cross's remittances that reach May 1 (its usual 99214 at $110)."""
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import remit as raw_remit
    for n in range(25):
        raw_remit(s, trace=f"BC{n:05d}", paid_on="2026-05-01", payer="BLUE CROSS TEST",
                  claims=[{"pcn": F.claim_id(200 + n), "status": "1", "member": f"MB{n:08d}", "last": "VOLUME", "first": f"B{n}",
                           "lines": [("2026-04-20", "99214", 200, 110, [("CO", "45", 90)])]}])


BC = ("BLUE CROSS TEST", "BCT01")


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("named", ["same", "other"])
def test_an_837_that_names_its_patient_takes_the_ema_claim_charged_before_its_send(tmp_path, kind, ema_env, named):
    """(answers-7, the review's test made decidable by the 837's own patient) Only the 837s know claim F.claim_id(91): a
    99214 at $200, visit March 2, sent to Blue Cross March 20; EMA charged it March 3 as EMA claim 91 (patient 'Sample,
    Bravo Q', born July 14, 1961), nothing posted, the A/R pairs no claim id with it. Another patient's EMA claim 92 of the
    same day, code and charge was paid April 10, so EMA's claim cannot be told by content alone. No remittance names the
    patient; the 837 does (its subscriber loop, no patient loop): SAMPLE BRAVO born 1961-07-14 ('same'), and the claim keeps
    EMA claim 91: one item. Naming another person ('other'), EMA claim 91 is not this claim's: both stay listed."""
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import charge, denial_world, pay
    s, lg = _ema_store(tmp_path, kind)
    rows = [charge(F.link_no(91), cpt="99214", amount=200, patient=1, posted=D(2026, 3, 3)),
            charge(F.link_no(92), cpt="99214", amount=200, patient=2, posted=D(2026, 3, 3)),
            pay(F.link_no(92), "PAPER-92", 110, cpt="99214", patient=2, posted=D(2026, 4, 10))]
    denial_world(s, lg, tmp_path, rows)
    _bc_volume(s)
    sub = ("SAMPLE", "BRAVO", "19610714") if named == "same" else ("OTHERPERSON", "ZED", "19700101")
    send(s, tmp_path, [clm837(F.claim_id(91), [("99214", "200.00", "20260302")], payer=BC, member="MEMBER000091",
                              subscriber=sub)], "20260320")
    det = Detector(s, run_id="r1", today="2026-05-03")
    det.run()
    assert det.ema_errors == [], det.ema_errors
    lg.close()
    got = [dict(r) for r in s.query("SELECT patient_control_number, ema_link, source FROM findings WHERE signal='NO_ANSWER' "
                                    "AND COALESCE(stale,0)=0")]
    own = [g for g in got if g["patient_control_number"] == F.claim_id(91)]
    g8 = [g for g in got if g["ema_link"] == F.link_no(91) and g["source"] != "837"]
    if named == "same":
        assert len(own) == 1 and own[0]["ema_link"] == F.link_no(91) and not g8, (own, g8)
    else:
        assert len(own) == 1 and own[0]["ema_link"] is None and len(g8) == 1, (own, g8)
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_send_day_twins_of_two_patients_are_told_apart_by_their_837s(tmp_path, kind, ema_env):
    """(The send-day twins, with the 837's own patient) Claims F.claim_id(67) and F.claim_id(68), two patients' 99204 at $250
    for March 2 visits, went to Blue Cross March 4; only the 837s know them. EMA holds two claims of that day, code, charge
    and payer, charged March 4: patient 1's (Sample, Bravo Q), paid April 12, and patient 2's (Example, Charlie Jr), with
    nothing posted. Without a name the two cannot be told apart and both stay listed (tests/test_837_sends.py). Each 837
    names its patient (SAMPLE BRAVO; EXAMPLE CHARLIE): claim 67 is answered by its own EMA claim, and claim 68 is listed
    once, with its own EMA claim (Production's check does not list that claim again)."""
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import charge, denial_world, pay
    s, lg = _ema_store(tmp_path, kind)
    sent = D(2026, 3, 4)
    rows = [charge(F.link_no(67), cpt="99204", amount=250, patient=1, posted=sent),
            pay(F.link_no(67), "PAPER-67", 130, cpt="99204", patient=1, posted=D(2026, 4, 12)),
            charge(F.link_no(68), cpt="99204", amount=250, patient=2, posted=sent)]
    denial_world(s, lg, tmp_path, rows)
    _bc_volume(s)
    send(s, tmp_path, [clm837(F.claim_id(67), [("99204", "250.00", "20260302")], payer=BC, member="MEMBER000067",
                              subscriber=("SAMPLE", "BRAVO", None)),
                       clm837(F.claim_id(68), [("99204", "250.00", "20260302")], payer=BC, member="MEMBER000068",
                              subscriber=("EXAMPLE", "CHARLIE", None))], "20260304")
    det = Detector(s, run_id="r1", today="2026-05-03")
    det.run()
    assert det.ema_errors == [], det.ema_errors
    lg.close()
    got = [dict(r) for r in s.query("SELECT patient_control_number, ema_link, source FROM findings WHERE signal='NO_ANSWER' "
                                    "AND COALESCE(stale,0)=0")]
    assert not [g for g in got if g["patient_control_number"] == F.claim_id(67) or g["ema_link"] == F.link_no(67)], got
    mine = [g for g in got if g["patient_control_number"] == F.claim_id(68) or g["ema_link"] == F.link_no(68)]
    assert len(mine) == 1 and mine[0]["source"] == "837" and mine[0]["ema_link"] == F.link_no(68), mine
    s.close()


# ======================================================================================= F8's observations
@pytest.mark.parametrize("kind", KINDS)
def test_a_send_to_a_medicaid_plan_sharing_a_payer_id_is_that_medicaid_plans(tmp_path, kind):
    """(Observed by F8 on the 11-24 copy) Payer id TST99 is a company's: three claims sent under it named SAMPLE HEALTH PLAN
    were answered as primary by its commercial plan (AMBETTER TEST PLAN). Its Indiana Medicaid managed-care plan shares
    the id: claim CB0000P02 went to INDIANA MEDICAID TEST PLAN under TST99 and no payer answered it. The send is the
    Medicaid plan's (its windows apply), not the id's commercial majority; a send named SAMPLE HEALTH PLAN under the same id
    (CB0000P03, the control) stays the commercial plan's."""
    s = _store(tmp_path, kind)
    plan = ("SAMPLE HEALTH PLAN", "TST99")
    send(s, tmp_path, [clm837(f"CB0000P1{n}C017", V, payer=plan) for n in range(3)]
         + [clm837("CB0000P02C017", V, payer=("INDIANA MEDICAID TEST PLAN", "TST99")), clm837("CB0000P03C017", V, payer=plan)],
         "20260915")
    _remits(s, tmp_path, [answer(f"CB0000P1{n}C017") for n in range(3)], "20261110", payer="AMBETTER TEST PLAN", payer_id="TST99")
    _remits(s, tmp_path, _visits(), "20261110")
    found = _chased(s, "t1")
    assert found["CB0000P02C017"]["payer_group"] == "Medicaid IN", found["CB0000P02C017"]["payer_group"]
    assert found["CB0000P03C017"]["payer_group"] == "Ambetter (Centene)", found["CB0000P03C017"]["payer_group"]
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_a_claim_no_line_of_which_can_be_priced_says_its_size_is_not_known(tmp_path, kind):
    """(Observed by F8 on the 11-24 copy: a G2211 claim to a Medicaid plan, listed at '$0') Claim CB0000G01 bills a G2211 at
    $30 to SAMPLE COMMUNITY PLAN, sent September 15; no payer answered it. The plan's remittances reach November 10, but it
    has paid nothing on file (three answers, each a denial), so nothing prices the claim. It is listed all the same (its
    answer decides what is owed and whether the patient may be billed; decision 4 keeps a never-paid code pursued), and the
    item says its size is not known rather than showing it owes nothing."""
    s = _store(tmp_path, kind)
    plan = ("SAMPLE COMMUNITY PLAN", "TST98")
    send(s, tmp_path, [clm837("CB0000G01C017", [("G2211", "30.00", "20260914")], payer=plan)], "20260915")
    _remits(s, tmp_path, [answer(f"CB0000G1{n}C017", paid="0.00") for n in range(3)], "20261110",
            payer="SAMPLE COMMUNITY PLAN", payer_id="TST98")
    found = _chased(s, "t1")
    f = found["CB0000G01C017"]
    assert f["signal"] == "NO_ANSWER" and f["shortfall_cents"] is None, (f["signal"], f["shortfall_cents"])
    assert "Its size is not known" in (f["hint"] or "") and "G2211" in f["hint"], f["hint"]
    s.close()
