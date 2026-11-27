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
