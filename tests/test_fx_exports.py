"""
Fix wave F3b (plans/briefs/2026-11-26_fixwave.md): the never-answered claims of EMA's weekly exports, their send-day
twins and Production's never-answered check (G8), against the review of 2026-11-26 (plans/review_2026-11-26.md,
answers-1, 2, 3, 5 and 7) and the residuals of fix7e (plans/next_fixes_2026-11-24.md sections 5 and 7).

- answers-1: a visit the exports chase under one bill and the practice billed again under a newer bill that only the
  837s know is chased once, under the newer bill; the older item's work follows it. Two people on one subscriber id
  stay two services.
- answers-2: an export claim due only by EMA's postings whose EMA claim cannot be found waits, counted on the Data tab,
  whatever stray remittance is newer than the feed date; the count names the feed date.
- answers-3: a line billed at $0 (or a token charge under $1) is nothing owed on an export claim; a claim with no line
  left is not listed.
- answers-5: an export claim whose bill went out again (the 837s show it) stays listed when nothing else chases the newer
  submission.
- answers-7: a claim the 837 pass chases is not listed again by Production's never-answered check when EMA charged it
  days before the send.

Every claim id, member number, name, amount and date here is invented (the repository's builders: tests/test_837_sends,
tests/test_workable, tests/test_features, tests/test_ema_wiring, tests/ema_fixtures). Each test runs on the server's
store file and on the compact in-memory store the serverless cycle builds and analyses.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch.detect import Detector  # noqa: E402
from tests.test_837_sends import KINDS, V, _chased, _remits, _store, _visits, answer, clm837, send  # noqa: E402

D = dt.date


@pytest.fixture
def ema_env(monkeypatch):
    """EMA's reports read as on a server, a failing EMA step failing the test (REMITWATCH_EMA_STRICT)."""
    from remitwatch import ema_checks as ec, ema_reports as er
    from tests import ema_fixtures as F
    monkeypatch.setattr(er, "CLAIM_HOST_SUFFIX", F.HOST_SUFFIX)
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    monkeypatch.setenv("REMITWATCH_EMA_STRICT", "1")
    for k in ("VERCEL", "REMITWATCH_EMA_REPORTS", "REMITWATCH_EMA_CLAIM_URL", "REMITWATCH_EMA_PAYER_ALIASES", "REMITWATCH_EMA_LEDGER",
              "REMITWATCH_EMA_REPORT_KEEP", "REMITWATCH_EMA_CLOSES"):
        monkeypatch.delenv(k, raising=False)
    ec._LAST_POST.clear()
    yield


def _ema_store(tmp_path, kind):
    from remitwatch import ema_ledger as el, ema_reports as er
    s = _store(tmp_path, kind)
    er.ensure_schema(s.conn)
    s.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('ema:key_salt', ?)", ("ab" * 32,))
    s.conn.commit()
    return s, el.Ledger.open(str(tmp_path / "ema_ledger.sqlite"))


def _open(s, like: str) -> list[dict]:
    return [dict(r) for r in s.query("SELECT signal, patient_control_number, procedure_code, status, work_state, shortfall_cents, "
                                     "evidence, finding_id FROM findings WHERE patient_control_number LIKE ? AND status='open' "
                                     "AND COALESCE(stale,0)=0", (like,))]


# ----------------------------------------------------------------------------------------------- answers-1
def _salted(s) -> None:
    """The store's key salt, as EMA's readers make it: patients' names are compared by their keyed hash only."""
    from remitwatch import ema_reports as er
    er.ensure_schema(s.conn)
    s.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('ema:key_salt', ?)", ("cd" * 32,))
    s.conn.commit()


def _member_visit(claim_id: str, member: str, last: str = "SAMPLE", first: str = "PAT", dos: str = "20260801") -> str:
    """Anthem's answer to an earlier visit of the member (a 99214 paid in full): the remittance names the patient
    (NM1*QC) with the member id, which is how a member id is tied to one person's name."""
    from tests.test_features import NPI, svc
    return (f"CLP*{claim_id}*1*223.65*117.60*0.00*12*P{claim_id[4:9]}*11~NM1*QC*1*{last}*{first}****MI*{member}~"
            f"NM1*82*2*TEST PROVIDER*****XX*{NPI}~DTM*232*{dos}~"
            + svc("99214", "223.65", "117.60", "117.60", "CAS*CO*45*106.05~", dos=dos))


def _dependent_837(claim_id: str, lines: list[tuple], member: str, first: str) -> str:
    """One dependent's 837P claim: the subscriber loop names the parent's member id (NM1*IL), the patient loop (HL 23,
    NM1*QC) names the child. The 837 tables keep the subscriber's id only."""
    out = (f"HL*2*1*22*1~SBR*P**GRP1******CI~NM1*IL*1*SAMPLE*PARENT****MI*{member}~NM1*PR*2*ANTHEM*****PI*00660~"
           f"HL*3*2*23*0~PAT*19~NM1*QC*1*SAMPLE*{first}~"
           f"CLM*{claim_id}*{sum(float(ln[1]) for ln in lines):.2f}***11:B:1*Y*A*Y*Y~")
    for n, (code, charge, dos) in enumerate(lines, 1):
        out += f"LX*{n}~SV1*HC:{code}*{charge}*UN*1***1~DTP*472*D8*{dos}~"
    return out


def _tie(n: int, who, dob=None):
    """EMA's reports naming a patient: an earlier visit's claim F.claim_id(n) (a 99212 of February 2, paid February 21) that
    the A/R pairs with EMA claim n, whose lines name `who` (born `dob`). Returns (Production rows, A/R rows); _tie_claim
    puts the claim's own rows in the store."""
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import charge, pay
    kw = {"dob": dob} if dob else {}
    feb = D(2026, 2, 2)
    rows = [charge(F.link_no(n), cpt="99212", amount=100, patient=who, svc=feb, posted=D(2026, 2, 3), **kw),
            pay(F.link_no(n), f"7771{n:04d}", 60, cpt="99212", patient=who, svc=feb, posted=D(2026, 2, 21), **kw)]
    ar = [F.ar_row(claim=F.claim_id(n), link=F.link_no(n), status="Payment Pending", current=0, svc=feb, patient=who, cpt="99212")]
    return rows, ar


def _tie_claim(s, n: int, pid: str) -> None:
    """The earlier visit's claim of _tie: Blue Cross's remittance for it, and its row in EMA's weekly exports under EMA's
    patient `pid` (which is how EMA's patient on another claim is named)."""
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import remit as raw_remit
    from tests.test_workable import ema
    raw_remit(s, trace=f"7771{n:04d}", paid_on="2026-02-20", claims=[{
        "pcn": F.claim_id(n), "status": "1", "member": f"MTIE{n:06d}", "last": "TIE", "first": f"N{n}",
        "lines": [("2026-02-02", "99212", 100, 60, [("CO", "45", 40)])]}])
    ema(s.conn, F.claim_id(n), cpt="99212", service_date="2026-02-02", bill_created="2026-02-03", charges_cents=10000,
        patient_id=pid, payer=F.PAYERS["bcbs"], payer_id="BCT01")


def _volume(s) -> None:
    """Blue Cross's remittances that reach May 1 (its usual 99214 at $110 and 17110 at $80)."""
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import remit as raw_remit
    for n in range(25):
        raw_remit(s, trace=f"BC{n:05d}", paid_on="2026-05-01", payer="BLUE CROSS TEST",
                  claims=[{"pcn": F.claim_id(200 + n), "status": "1", "member": f"MB{n:08d}", "last": "VOLUME", "first": f"B{n}",
                           "lines": [("2026-04-20", "99214", 200, 110, [("CO", "45", 90)], {"allowed_cents": 11000})]},
                          {"pcn": F.claim_id(300 + n), "status": "1", "member": f"MC{n:08d}", "last": "VOLUME", "first": f"C{n}",
                           "lines": [("2026-04-20", "17110", 150, 80, [("CO", "45", 70)], {"allowed_cents": 8000})]}])


BC = ("BLUE CROSS TEST", "BCT01")


def _read_before_v2(s, *claim_ids: str) -> None:
    """These claims' 837s as the reader kept them before x12_837.READ_VERSION 2: no patient (as every 837 on the real copy of
    2026-11-24 until the hosts read them again). A world whose premise is that nothing, or only a remittance, names the
    patient needs it: an 837 read since names the patient itself, and the clm837 builder names every one SAMPLE PAT
    (the 837's own patient: tests/test_fx_passes.py)."""
    q = ",".join("?" * len(claim_ids))
    s.conn.execute(f"UPDATE claims_837 SET patient_key=NULL, person_key=NULL, first_key=NULL, read_version=0 "
                   f"WHERE patient_control_number IN ({q})", claim_ids)
    s.conn.commit()


def _rebill_world(tmp_path, kind, *, named: bool, extra_17110: bool = False, worked: bool = False, two_runs: bool = False,
                  worked_newer: bool | str = False, old_837: bool | None = None):
    """(answers-1) Sample's visit of March 2 (a 99214 at $200; extra_17110: and a 17110 at $150) went to Blue Cross on claim
    F.claim_id(31), which EMA's weekly exports list (submitted March 3; its 837 names member MEMBER000031). No payer answered
    it. The practice made the bill again as F.claim_id(32) (the 99214 alone), sent March 13; only the 837s know it (member
    MEMBER000031 too). Remittances reach May 1; today May 3.
    named: EMA's reports name the patient of both claims: the A/R pairs F.claim_id(32) with EMA claim 32 ('Sample, Bravo
    Q'), and EMA's patient on the exports' claim has another claim the A/R pairs with an EMA claim of the same name. Not
    named: nothing but the member id's one remitted name ties them (an earlier visit's remittance names the member's
    patient), which never makes two claims one person.
    worked, two_runs: a first run before the names arrive lists both; a biller starts the exports' item ('Called Blue Cross:
    no record of it'); EMA's reports naming both arrive before the second run. worked_newer: another biller also starts the
    newer bill's item ('Claim resent by fax'); 'touched': sets it Started and back to To do, recording nothing.
    old_837: the two 837s were read before x12_837.READ_VERSION 2, so they name no patient (as every 837 on the real copy
    of 2026-11-24 until the hosts read them again); by default when the world's premise is that nothing names the patient
    (not named, or the first of two runs): an 837 read since names the patient itself (tests/test_fx_passes.py)."""
    from remitwatch.actions import apply_status
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import charge, denial_world, load, remit as raw_remit
    from tests.test_workable import ema
    s, lg = _ema_store(tmp_path, kind)
    bravo = F.PATIENTS[1][0]
    tie_rows, tie_ar = _tie(33, bravo)
    # the newer bill's own EMA claim (made again at a corrected $210): the exports' claim at $200 has other content
    own_rows = [charge(F.link_no(32), cpt="99214", amount=210, patient=bravo, posted=D(2026, 3, 13), status="Payment Pending")]
    own_ar = [F.ar_row(claim=F.claim_id(32), link=F.link_no(32), status="Payment Pending", current=210, svc=SVC_, patient=bravo,
                       cpt="99214")]
    later = named and two_runs
    denial_world(s, lg, tmp_path, tie_rows + own_rows, ar_rows=tie_ar + own_ar if (named and not later) else [])
    _volume(s)
    raw_remit(s, trace="BCMEM31", paid_on="2026-02-20", payer="BLUE CROSS TEST",
              claims=[{"pcn": F.claim_id(30), "status": "1", "member": "MEMBER000031", "last": "SAMPLE", "first": "BRAVO",
                       "lines": [("2026-02-02", "99213", 150, 80, [("CO", "45", 70)])]}])
    _tie_claim(s, 33, "EMA031")
    bc = dict(payer=F.PAYERS["bcbs"], payer_id="BCT01", patient_id="EMA031")
    ema(s.conn, F.claim_id(31), service_date="2026-03-02", bill_created="2026-03-03", charges_cents=20000, **bc)
    if extra_17110:
        ema(s.conn, F.claim_id(31), cpt="17110", service_date="2026-03-02", bill_created="2026-03-03", charges_cents=15000, **bc)
    ema(s.conn, F.claim_id(31), family="claim_status", claim_status="Submitted", claim_submission_date="2026-03-03",
        service_date="2026-03-02", **bc)
    s.conn.commit()
    lines31 = [("99214", "200.00", "20260302")] + ([("17110", "150.00", "20260302")] if extra_17110 else [])
    send(s, tmp_path, [clm837(F.claim_id(31), lines31, payer=BC, member="MEMBER000031")], "20260303")
    send(s, tmp_path, [clm837(F.claim_id(32), [("99214", "210.00", "20260302")], payer=BC, member="MEMBER000031")], "20260313")
    if old_837 if old_837 is not None else (two_runs or not named):
        _read_before_v2(s, F.claim_id(31), F.claim_id(32))
    if two_runs:
        found = _chased(s, "t1", today="2026-05-03")
        assert {F.claim_id(31), F.claim_id(32)} <= set(found), sorted(found)
        if worked:
            apply_status(s.conn, found[F.claim_id(31)]["finding_id"], "in_progress", "Called Blue Cross: no record of it",
                         actor="biller")
            s.conn.commit()
        if worked_newer == "touched":
            # a person set the newer bill's item Started and back to To do: it was touched, though nothing is recorded on it
            apply_status(s.conn, found[F.claim_id(32)]["finding_id"], "in_progress", None, actor="biller2")
            apply_status(s.conn, found[F.claim_id(32)]["finding_id"], "open", None, actor="biller2")
            s.conn.commit()
        elif worked_newer:
            apply_status(s.conn, found[F.claim_id(32)]["finding_id"], "in_progress", "Claim resent by fax", actor="biller2")
            s.conn.commit()
        if later:
            load(s, lg, tmp_path, [], tie_ar + own_ar)      # the A/R that names both claims' patient arrives
    found = _chased(s, "t2", today="2026-05-03")
    lg.close()
    return s, found


SVC_ = D(2026, 3, 2)


@pytest.mark.parametrize("kind", KINDS)
def test_a_visit_the_exports_chase_yields_to_a_newer_bill_only_the_837s_know(tmp_path, kind, ema_env):
    """(answers-1, the exports' side) One visit of one patient (the same day, code and part) billed on the exports' claim and
    again on a newer bill only the 837s know is one unpaid service: only the newer bill carries it. The patient is one
    person by the claims' own evidence: EMA's reports name both claims' patient, under the same member id (as the 837 pass
    matches people: fx-exports-r2-3, r2-4)."""
    from tests import ema_fixtures as F
    s, found = _rebill_world(tmp_path, kind, named=True)
    mine = {k for k in found if k in (F.claim_id(31), F.claim_id(32))}
    assert mine == {F.claim_id(32)}, f"listed {sorted(mine)}: the same patient's one visit sent under two bills is one service"
    assert F.claim_id(31) in (found[F.claim_id(32)]["hint"] or ""), found[F.claim_id(32)]["hint"]
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_a_member_ids_one_remitted_name_never_makes_two_bills_one_visit(tmp_path, kind, ema_env):
    """(answers-1; fx-exports-r2-3) The same two bills, but nothing names their patient except the member id's one remitted
    name (an earlier visit's remittance under MEMBER000031). A subscriber's id covers the family: a child never remitted
    takes that name there. Both bills stay chased (the reviewer's scenario for answers-1 had no name at all)."""
    from tests import ema_fixtures as F
    s, found = _rebill_world(tmp_path, kind, named=False)
    assert {F.claim_id(31), F.claim_id(32)} <= set(found), sorted(found)
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_an_export_claim_keeps_the_services_the_newer_bill_does_not_carry(tmp_path, kind, ema_env):
    """(answers-1) The exports' bill carries a 99214 and a 17110 of the visit; the newer bill carries the 99214 alone. The
    99214 is chased under the newer bill; the 17110 stays on the exports' item, sized at Blue Cross's $80 for it."""
    from tests import ema_fixtures as F
    s, found = _rebill_world(tmp_path, kind, named=True, extra_17110=True)
    assert {F.claim_id(31), F.claim_id(32)} <= set(found), sorted(found)
    x = found[F.claim_id(31)]
    assert [ln[0] for ln in json.loads(x["evidence"])["lines"]] == ["17110"] and x["shortfall_cents"] == 8000, (
        json.loads(x["evidence"])["lines"], x["shortfall_cents"])
    assert [ln[0] for ln in json.loads(found[F.claim_id(32)]["evidence"])["lines"]] == ["99214"]
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_work_on_an_export_item_stays_on_a_listed_item_when_the_patients_name_arrives_later(tmp_path, kind, ema_env):
    """(fx-exports-r1-2, r2-5; restated by F3c for both items worked) The first run lists both bills (nothing names their
    patient yet); one biller starts the exports' item ('no record of it'), another the newer bill's ('resent by fax'). EMA's
    reports naming the patient arrive; the second run finds the one visit. Neither person's work is moved onto the other's:
    the exports' item stays listed with its Started and its note, pointing to the newer bill, and the newer bill's item keeps
    its own and points back, so a person checks which is the payer's claim and sets the other Done. (One worked by nobody
    takes the work: tests/test_fx_passes.py.)"""
    from tests import ema_fixtures as F
    s, found = _rebill_world(tmp_path, kind, named=True, worked=True, two_runs=True, worked_newer=True)
    assert {F.claim_id(31), F.claim_id(32)} <= set(found), sorted(found)
    x, y = found[F.claim_id(31)], found[F.claim_id(32)]
    assert x["status"] == "in_progress" and "no record of it" in (x["notes"] or ""), (x["status"], x["notes"])
    assert y["status"] == "in_progress" and "resent by fax" in (y["notes"] or ""), (y["status"], y["notes"])
    assert F.claim_id(32) in (x["hint"] or "") and F.claim_id(31) in (y["hint"] or ""), (x["hint"], y["hint"])
    assert "both items were worked" in x["hint"], x["hint"]
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_an_unworked_export_item_yields_when_the_patients_name_arrives_later(tmp_path, kind, ema_env):
    """(answers-1, with r1-2's run order) The same two runs with no work recorded on the exports' item: once the patient is
    named it yields, and only the newer bill is chased."""
    from tests import ema_fixtures as F
    s, found = _rebill_world(tmp_path, kind, named=True, worked=False, two_runs=True)
    assert {k for k in found if k in (F.claim_id(31), F.claim_id(32))} == {F.claim_id(32)}, sorted(found)
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_two_people_on_one_subscriber_id_seen_the_same_day_stay_two_services(tmp_path, kind):
    """(answers-1, the verifier's family case) Two children on one parent's plan (subscriber id WFAM000002) were both seen
    September 14 for a 99214. Child A's bill CB0000F41 is in EMA's weekly exports (sent September 15); child B's bill
    CB0000F42 went out September 25 and only the 837s know it. The member's remittances name two people, so the member
    id does not tell whose visit each bill is: both claims stay chased (never merged on the subscriber's id alone)."""
    from tests.test_workable import ema
    s = _store(tmp_path, kind)
    _salted(s)
    member = "WFAM000002"
    _remits(s, tmp_path, _visits() + [_member_visit("CB0000F30C017", member, first="CHILDA"),
                                      _member_visit("CB0000F31C017", member, first="CHILDB", dos="20260802")], "20261110")
    ema(s.conn, "CB0000F41C017")
    ema(s.conn, "CB0000F41C017", family="claim_status", claim_status="Submitted", claim_submission_date="2026-09-15")
    s.conn.commit()
    send(s, tmp_path, [_dependent_837("CB0000F41C017", [("99214", "223.65", "20260914")], member, "CHILDA")], "20260915")
    send(s, tmp_path, [_dependent_837("CB0000F42C017", [("99214", "223.65", "20260914")], member, "CHILDB")], "20260925")
    found = _chased(s, "t1")
    assert set(found) == {"CB0000F41C017", "CB0000F42C017"}, f"listed {sorted(found)}: child A's visit dropped for child B's"
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_a_child_whose_sibling_was_never_remitted_keeps_its_own_visit(tmp_path, kind):
    """(fx-exports-r2-3; the reviewer's test) Two children on one parent's plan (subscriber id WFAM000009) were both seen
    September 14 for a 99214. Child A's bill CB0000F51 is in EMA's weekly exports (sent September 15); child B's bill
    CB0000F52 went out September 25 and only the 837s know it. Only child A was ever on a remittance (an August visit), so
    the member id's one remitted name is child A's. Nothing ties child B's claim to child A but the parent's subscriber
    id: both visits are unpaid, both claims stay chased."""
    from tests.test_workable import ema
    s = _store(tmp_path, kind)
    _salted(s)
    member = "WFAM000009"
    _remits(s, tmp_path, _visits() + [_member_visit("CB0000F50C017", member, first="CHILDA")], "20261110")
    ema(s.conn, "CB0000F51C017")
    ema(s.conn, "CB0000F51C017", family="claim_status", claim_status="Submitted", claim_submission_date="2026-09-15")
    s.conn.commit()
    send(s, tmp_path, [_dependent_837("CB0000F51C017", [("99214", "223.65", "20260914")], member, "CHILDA")], "20260915")
    send(s, tmp_path, [_dependent_837("CB0000F52C017", [("99214", "223.65", "20260914")], member, "CHILDB")], "20260925")
    found = _chased(s, "t1")
    assert {"CB0000F51C017", "CB0000F52C017"} <= set(found), (
        f"listed {sorted(found)}: child A's unpaid visit was handed to child B's bill on the parent's subscriber id")
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_two_parts_of_a_service_on_two_bills_are_two_services(tmp_path, kind):
    """(answers-1) The exports' bill CB0000M61 (September 15) bills the reading of a biopsy (88305-26); the newer bill
    CB0000M62 (September 25, only the 837s know it) bills its technical part (88305-TC) for the same patient and day. The
    reading and the technical part are two services: both stay chased."""
    from tests.test_workable import ema
    s = _store(tmp_path, kind)
    _salted(s)
    member = "WDUP000006"
    _remits(s, tmp_path, _visits() + [_member_visit("CB0000M60C017", member)], "20261110")
    ema(s.conn, "CB0000M61C017", cpt="88305", modifiers='["26"]', charges_cents=9000)
    ema(s.conn, "CB0000M61C017", family="claim_status", claim_status="Submitted", claim_submission_date="2026-09-15")
    s.conn.commit()
    send(s, tmp_path, [clm837("CB0000M61C017", [("88305", "90.00", "20260914", "26")], member=member)], "20260915")
    send(s, tmp_path, [clm837("CB0000M62C017", [("88305", "120.00", "20260914", "TC")], member=member)], "20260925")
    found = _chased(s, "t1")
    assert set(found) == {"CB0000M61C017", "CB0000M62C017"}, f"listed {sorted(found)}: the reading and the technical part merged"
    s.close()


# ----------------------------------------------------------------------------------------------- answers-2
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("stray", [True, False])
def test_an_export_claim_no_source_can_check_waits_whatever_stray_remittance_is_newer(tmp_path, kind, ema_env, stray):
    """(answers-2; the reviewer's test) Anthem's and Humana's remittances reach March 28; one small payer's single file is
    dated April 25 (stray=False: March 28 too). The feed date is March 28. EMA's Production report is fresh (as of May 1)
    and shows Anthem posting through April 20, so Anthem's answers are known through April 23 by EMA's postings.
    EMA's weekly exports list claim CB0000Q01C017 to Anthem: submitted March 3, due April 2. No 835 answers it and EMA's
    own claim for it cannot be found: no source can show whether Anthem answered it after March 28. It waits, counted
    with its dollars on the Data tab (meta ema:unanswered_unseen), and that count names the feed date as the newest 835,
    not the stray file's date."""
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import charge, denial_world, pay, remit as raw_remit
    from tests.test_workable import ema
    s, lg = _ema_store(tmp_path, kind)
    rows = [charge(F.link_no(70), cpt="99213", amount=150, patient=2, svc=D(2026, 3, 9), posted=D(2026, 3, 10)),
            pay(F.link_no(70), "PAPER-70", 90, cpt="99213", patient=2, svc=D(2026, 3, 9), posted=D(2026, 4, 20))]
    denial_world(s, lg, tmp_path, rows)
    for n in range(30):
        raw_remit(s, trace=f"BC{n:05d}", paid_on="2026-03-28", payer="BLUE CROSS TEST",
                  claims=[{"pcn": F.claim_id(200 + n), "status": "1", "member": f"MB{n:08d}", "last": "VOLUME", "first": f"B{n}",
                           "lines": [("2026-03-01", "99212", 100, 60, [("CO", "45", 40)])]}])
        raw_remit(s, trace=f"HU{n:05d}", paid_on="2026-03-28", payer="HUMANA",
                  claims=[{"pcn": F.claim_id(300 + n), "status": "1", "member": f"MH{n:08d}", "last": "VOLUME", "first": f"H{n}",
                           "lines": [("2026-03-01", "99212", 100, 60, [("CO", "45", 40)])]}])
    raw_remit(s, trace="UH00001", paid_on="2026-04-25" if stray else "2026-03-28", payer="UNITEDHEALTHCARE",
              claims=[{"pcn": F.claim_id(400), "status": "1", "member": "MU00000001", "last": "STRAY", "first": "U",
                       "lines": [("2026-04-01" if stray else "2026-03-01", "99212", 100, 60, [("CO", "45", 40)])]}])
    from remitwatch.detect import feed_date
    assert feed_date(s.conn) == "2026-03-28"
    ema(s.conn, "CB0000Q01C017", service_date="2026-03-02", bill_created="2026-03-03")
    ema(s.conn, "CB0000Q01C017", family="claim_status", claim_status="Submitted", claim_submission_date="2026-03-03",
        service_date="2026-03-02")
    s.conn.commit()
    det = Detector(s, run_id="r1", today="2026-05-03")
    det.run()
    assert det.ema_errors == [], det.ema_errors
    lg.close()
    got = {r["patient_control_number"] for r in s.query(
        "SELECT patient_control_number FROM findings WHERE signal='NO_ANSWER' AND COALESCE(stale,0)=0")}
    unseen = json.loads(s.conn.execute("SELECT value FROM meta WHERE key='ema:unanswered_unseen'").fetchone()[0])
    assert "CB0000Q01C017" not in got, (
        "listed as never answered, though its due day (April 2) is after the day the remittances reach (March 28) and EMA's "
        "own claim for it was not found: no source can show whether Anthem answered it")
    assert unseen["n"] == 1 and unseen["billed_cents"] == 22365, unseen
    assert unseen["newest_835"] == "2026-03-28", f"the count names {unseen['newest_835']} as the newest remittance, not the feed date"
    s.close()


# ----------------------------------------------------------------------------------------------- answers-3
@pytest.mark.parametrize("kind", KINDS)
def test_a_line_billed_at_no_charge_is_nothing_owed_on_an_export_claim(tmp_path, kind):
    """(answers-3, the exports' half; the reviewer's test) Anthem pays 99214 at $117.60 and 99213 at $80. EMA's weekly
    exports list claim CB0000Z01C017 to Anthem, never answered: a 99214 charged $223.65 and a 99213 billed at no charge.
    The 837 pass leaves such a line out (a line billed at $0, or a token charge under $1, is nothing owed): so do the
    exports. Claim CB0000Z02C017's only line is a 99213 at $0, and Z03's a 99213 at a token $0.01: nothing is owed on
    either, and neither is listed. Claim CB0000Z04C017 is known from EMA's claim status export alone (no charge row, so
    its charge is not known): it stays listed, at the payer's rate."""
    from tests.test_features import claim, svc
    from tests.test_workable import ema
    s = _store(tmp_path, kind)
    rates = [claim(500 + i, svc("99213", "150.00", "80.00", "80.00", "CAS*CO*45*70.00~")) for i in range(25)]
    _remits(s, tmp_path, rates + _visits(), "20261110")
    ema(s.conn, "CB0000Z01C017")
    ema(s.conn, "CB0000Z01C017", cpt="99213", charges_cents=0)
    ema(s.conn, "CB0000Z02C017", cpt="99213", charges_cents=0)
    ema(s.conn, "CB0000Z03C017", cpt="99213", charges_cents=1)
    ema(s.conn, "CB0000Z04C017", family="claim_status", cpt="99213", claim_status="Submitted", claim_submission_date="2026-09-15")
    s.conn.commit()
    found = _chased(s, "t1")
    z1 = found["CB0000Z01C017"]
    lines = json.loads(z1["evidence"])["lines"]
    assert z1["shortfall_cents"] == 11760 and [ln[0] for ln in lines] == ["99214"], (
        f"sized at ${z1['shortfall_cents'] / 100:.2f} with lines {lines}: the 99213 billed at $0 was valued at Anthem's rate")
    assert "CB0000Z02C017" not in found, "a claim billed $0 is listed"
    assert "CB0000Z03C017" not in found, "a claim billed a token $0.01 is listed"
    assert found["CB0000Z04C017"]["shortfall_cents"] == 8000, "a claim whose charge is not known is still chased"
    s.close()


def _token_world(tmp_path, kind, source):
    """EMA's claims 96 and 97 (patients 1 and 2): a 99214, a 96372 and a J3590 billed at a token $0.01 (the line EMA bills
    so a drug's code goes out), visit March 2, charges posted March 3. Blue Cross paid EMA claim 96 on April 10; EMA claim
    97 is still open. Claims F.claim_id(96) and F.claim_id(97) carry the same lines as their EMA claims (96: 99214 at
    $223.65; 97: 99214 at $200), sent to Blue Cross on March 20: known from the 837s only (source '837') or from EMA's
    weekly exports only (source 'ema'). No 835 answers either; the A/R pairs neither; the remittances reach May 1.
    Returns the open never-answered items of the two claims and of their EMA claims."""
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import charge, denial_world, pay, remit as raw_remit
    from tests.test_workable import ema
    s, lg = _ema_store(tmp_path, kind)
    lines = {96: [("99214", 223.65), ("96372", 25.03), ("J3590", 0.01)], 97: [("99214", 200.00), ("96372", 25.03), ("J3590", 0.01)]}
    rows = [charge(F.link_no(n), cpt=code, amount=amt, patient=n - 95, posted=D(2026, 3, 3)) for n, ls in lines.items() for code, amt in ls]
    rows.append(pay(F.link_no(96), "PAPER-96", 117.60, cpt="99214", patient=1, posted=D(2026, 4, 10)))
    denial_world(s, lg, tmp_path, rows)
    for n in range(25):
        raw_remit(s, trace=f"BC{n:05d}", paid_on="2026-05-01", payer="BLUE CROSS TEST",
                  claims=[{"pcn": F.claim_id(200 + n), "status": "1", "member": f"MB{n:08d}", "last": "VOLUME", "first": f"B{n}",
                           "lines": [("2026-04-20", "99214", 200, 110, [("CO", "45", 90)])]}])
    bc = ("BLUE CROSS TEST", "BCT01")
    if source == "837":
        send(s, tmp_path, [clm837(F.claim_id(n), [(code, f"{amt:.2f}", "20260302") for code, amt in ls], payer=bc,
                                  member=f"MEMBER0000{n}") for n, ls in lines.items()], "20260320")
    else:
        for n, ls in lines.items():
            for code, amt in ls:
                ema(s.conn, F.claim_id(n), cpt=code, charges_cents=round(amt * 100), service_date="2026-03-02",
                    bill_created="2026-03-03", payer=bc[0], payer_id=bc[1])
            ema(s.conn, F.claim_id(n), family="claim_status", cpt="99214", claim_status="Submitted",
                claim_submission_date="2026-03-20", service_date="2026-03-02", payer=bc[0], payer_id=bc[1])
        s.conn.commit()
    det = Detector(s, run_id="r1", today="2026-05-03")
    det.run()
    assert det.ema_errors == [], det.ema_errors
    lg.close()
    got = [dict(r) for r in s.query("SELECT patient_control_number, ema_link, source, evidence FROM findings "
                                    "WHERE signal IN ('NO_ANSWER','REJECTED_PRE_PAYER') AND status='open' AND COALESCE(stale,0)=0")]
    s.close()
    return got


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("source", ["ema", "837"])
def test_a_line_billed_at_a_token_charge_still_tells_which_ema_claim_is_the_claims(tmp_path, kind, ema_env, source):
    """(answers-3, what it must not break) A line billed at a token $0.01 is nothing owed, so it is never listed or priced;
    but EMA's claim carries it as a charge line, and a claim's content is all its charged lines. Left out of the content, the
    claim matched no EMA claim: F.claim_id(96), which Blue Cross paid in EMA after its send, was listed as never answered
    (the measured world: 2 export claims and 15 claims of the 837s, all paid). Claim 96 is not listed; claim 97 is, once,
    for its two lines that are owed, with its EMA claim (so Production's never-answered check leaves EMA claim 97)."""
    from tests import ema_fixtures as F
    got = _token_world(tmp_path, kind, source)
    paid = [g for g in got if g["patient_control_number"] == F.claim_id(96) or g["ema_link"] == F.link_no(96)]
    assert not paid, f"a claim EMA shows paid after its send is listed: {[(g['source'], g['ema_link']) for g in paid]}"
    open_ = [g for g in got if g["patient_control_number"] == F.claim_id(97) or g["ema_link"] == F.link_no(97)]
    assert len(open_) == 1, f"claim 97 is listed {len(open_)} times: {[(g['source'], g['ema_link']) for g in open_]}"
    assert open_[0]["source"] == source and open_[0]["ema_link"] == F.link_no(97), open_
    assert sorted(ln[0] for ln in json.loads(open_[0]["evidence"])["lines"]) == ["96372", "99214"], open_[0]["evidence"]


@pytest.mark.parametrize("kind", KINDS)
def test_a_claims_content_counts_each_token_line_once(tmp_path, kind):
    """The content a claim is matched by: its listed lines and its token lines, each once. The 837 pass's lines leave the
    token line out (it is read from the claim's 837 send); a pass that keeps it among the claim's lines is not given it a
    second time (EMA keeps one line per date and code, so a doubled $0.01 would match no EMA claim); EMA's weekly exports
    give their own token lines."""
    s = _store(tmp_path, kind)
    send(s, tmp_path, [clm837("CB0000T01C017", [("99214", "200.00", "20260302"), ("J3590", "0.01", "20260302")])], "20260320")
    key = s.query("SELECT claim_837_key FROM claims_837 WHERE patient_control_number='CB0000T01C017'")[0][0]
    det = Detector(s, run_id="t1")
    r8 = ["2026-03-20", "2026-03-20", "1", None, "00660", "ANTHEM", 20001, key, "M1", 1]
    visit = ("99214", "2026-03-02", 20000, 1, None, "")
    token = ("J3590", "2026-03-02", 1, 1, None, "")
    got = det._claim_content([{"pcn": "CB0000T01C017", "items": [visit], "s837": r8},
                              {"pcn": "CB0000T02C017", "items": [visit, token], "s837": r8},
                              {"pcn": "CB0000T03C017", "items": [visit], "s837": None, "token_lines": [token[:3]]}])
    for cid in ("CB0000T01C017", "CB0000T02C017", "CB0000T03C017"):
        assert sorted(got[cid]) == [("99214", "2026-03-02", 20000), ("J3590", "2026-03-02", 1)], (cid, got[cid])
    s.close()


# ----------------------------------------------------------------------------------------------- answers-5
@pytest.mark.parametrize("kind", KINDS)
def test_an_export_claim_whose_bill_went_out_again_stays_listed_when_nothing_else_chases_it(tmp_path, kind):
    """(answers-5, the exports' half; from the reviewer's test) Anthem pays 99214 at $117.60 and 17110 at $80. Two bills
    were first sent with a 99214 (August 1) that Anthem paid in full as primary (August 20). The practice then found a
    forgotten 17110 of the same visit and sent each bill again with both lines (a replacement, September 1); EMA's weekly
    exports list that submission (C024). Nothing answered it by November 10.
    - L02: the 837s show the bill sent once more as C031 (September 20). The 837 pass does not chase a bill a payer
      answered as primary, so C031 is chased by nothing: the exports' C024 stays listed, its 17110 chased.
    - L03 (control): the same without C031: the exports' pass chases C024."""
    from tests.test_features import claim, svc
    from tests.test_workable import ema
    s = _store(tmp_path, kind)
    rates = [claim(600 + i, svc("17110", "200.00", "80.00", "80.00", "CAS*CO*45*120.00~")) for i in range(25)]
    v1 = [("99214", "223.65", "20260730")]
    v2 = v1 + [("17110", "200.00", "20260730")]
    other = lambda x: x.replace("*SAMPLE*PAT*", "*OTHERTEST*PERSON*")      # L03 is another patient's bill (fx-exports-r2-2)
    send(s, tmp_path, [clm837("CB0000L02C017", v1), other(clm837("CB0000L03C017", v1))], "20260801")
    _remits(s, tmp_path, [answer("CB0000L02C017", dos="20260730"), other(answer("CB0000L03C017", dos="20260730"))], "20260820")
    send(s, tmp_path, [clm837("CB0000L02C024", v2, freq="7"), other(clm837("CB0000L03C024", v2, freq="7"))], "20260901")
    send(s, tmp_path, [clm837("CB0000L02C031", v2, freq="7")], "20260920")
    for b in ("CB0000L02", "CB0000L03"):
        ema(s.conn, b + "C024", service_date="2026-07-30", bill_created="2026-07-31")
        ema(s.conn, b + "C024", cpt="17110", charges_cents=20000, service_date="2026-07-30", bill_created="2026-07-31")
        ema(s.conn, b + "C024", family="claim_status", claim_status="Submitted", claim_submission_date="2026-09-01",
            service_date="2026-07-30")
    s.conn.commit()
    _remits(s, tmp_path, rates + _visits(), "20261110")
    Detector(s, run_id="t1").run()
    for bill in ("CB0000L03", "CB0000L02"):
        got = [r for r in _open(s, bill + "%") if r["signal"] == "NO_ANSWER"]
        assert any("17110" in [ln[0] for ln in json.loads(r["evidence"])["lines"]] for r in got), (
            f"{bill}: the 17110 sent in the replacement was never answered, and no item chases it: {got}")
    l02 = [r["patient_control_number"] for r in _open(s, "CB0000L02%") if r["signal"] == "NO_ANSWER"]
    assert len(l02) == 1, f"one bill's one resend is chased {len(l02)} times: {l02}"
    if l02 == ["CB0000L02C031"]:
        s.close()       # the 837 pass chases the bill's newest resend itself (the fix wave's F3a): the exports' item yields
        return
    # L02's item is due from the newer send (the payer's answer to the bill is due from September 20, not September 1) and
    # names the submission the 837s show, so the biller asks the payer about it
    f = dict(s.query("SELECT title, hint, evidence FROM findings WHERE patient_control_number='CB0000L02C024'")[0])
    ev = json.loads(f["evidence"])
    assert ev["sent"] == "2026-09-20" and ev["bill_sent_again_837"] == {"day": "2026-09-20", "claim_id": "CB0000L02C031"}, ev
    assert "CB0000L02C031" in (f["hint"] or "") and "2026-09-20" in f["title"], (f["title"], f["hint"])
    # what it chases is what no remittance of the bill answered: the 17110 the resend added (the 99214 was paid on C017), as
    # the 837 pass sizes a resend (fx-exports-r2-2)
    assert [ln[0] for ln in ev["lines"]] == ["17110"], ev["lines"]
    s.close()


def _resend_world(tmp_path, kind):
    """(fx-exports-r2-2; the reviewer's world) Anthem paid bill L02's C017 (a 99214) in full on August 20. The practice sent
    the bill again with a forgotten 17110 (C024, September 1, which EMA's weekly exports list) and once more as C031
    (September 20, only the 837s know it). Nothing answered either resend by November 10."""
    from tests.test_features import claim, svc
    from tests.test_workable import ema
    s = _store(tmp_path, kind)
    rates = [claim(600 + i, svc("17110", "200.00", "80.00", "80.00", "CAS*CO*45*120.00~")) for i in range(25)]
    v1 = [("99214", "223.65", "20260730")]
    v2 = v1 + [("17110", "200.00", "20260730")]
    send(s, tmp_path, [clm837("CB0000L02C017", v1)], "20260801")
    _remits(s, tmp_path, [answer("CB0000L02C017", dos="20260730")], "20260820")
    send(s, tmp_path, [clm837("CB0000L02C024", v2, freq="7")], "20260901")
    send(s, tmp_path, [clm837("CB0000L02C031", v2, freq="7")], "20260920")
    ema(s.conn, "CB0000L02C024", service_date="2026-07-30", bill_created="2026-07-31")
    ema(s.conn, "CB0000L02C024", cpt="17110", charges_cents=20000, service_date="2026-07-30", bill_created="2026-07-31")
    ema(s.conn, "CB0000L02C024", family="claim_status", claim_status="Submitted", claim_submission_date="2026-09-01",
        service_date="2026-07-30")
    s.conn.commit()
    _remits(s, tmp_path, rates + _visits(), "20261110")
    return s


def _resend_items(s) -> list[dict]:
    return [dict(r) for r in s.query(
        "SELECT patient_control_number, signal, source, shortfall_cents, evidence, hint FROM findings "
        "WHERE patient_control_number LIKE 'CB0000L02%' AND signal IN ('NO_ANSWER','REJECTED_PRE_PAYER') "
        "AND status='open' AND COALESCE(stale,0)=0")]


@pytest.mark.parametrize("kind", KINDS)
def test_a_bill_sent_again_after_its_primary_answer_is_chased_once(tmp_path, kind):
    """(fx-exports-r2-2; the reviewer's test) One bill, one unanswered resend: one item, for the service no remittance of
    the bill answered (the 17110 at Anthem's $80; the 99214 was paid on C017)."""
    s = _resend_world(tmp_path, kind)
    Detector(s, run_id="t1").run()
    got = _resend_items(s)
    s.close()
    assert len(got) == 1, [(g["patient_control_number"], g["source"], g["shortfall_cents"]) for g in got]
    assert [ln[0] for ln in json.loads(got[0]["evidence"])["lines"]] == ["17110"] and got[0]["shortfall_cents"] == 8000, got[0]


@pytest.mark.parametrize("kind", KINDS)
def test_an_export_resend_yields_to_the_837_passs_item_for_the_same_bill(tmp_path, kind, monkeypatch):
    """(fx-exports-r2-2) The 837 pass chases a bill's newest resend when a payer answered an earlier submission as primary
    (the fix wave's F3a, _837_resends; simulated here: its item for C031 is listed, sized at the 17110). Then the exports'
    item kept for that resend (C024) is not listed beside it: one bill, one resend, one item, the 837 pass's, which says
    the bill went out before as C024."""
    from remitwatch import detect as Dm
    orig = Dm.Detector._exports_yield_to_newer_bills

    def with_resend(self):
        final, newest = self.__dict__["_export_final"]
        cd = next(cd for cd, _items in final if cd["pcn"] == "CB0000L02C024")
        items = [x for x in next(it for c, it in final if c is cd)]
        self._emit_open_claim(dict(cd, pcn="CB0000L02C031", id_signal=Dm.OPEN_837_ID, source="837", bill_resent_837=None),
                              items, newest)
        return orig(self)
    monkeypatch.setattr(Dm.Detector, "_exports_yield_to_newer_bills", with_resend)
    s = _resend_world(tmp_path, kind)
    Detector(s, run_id="t1").run()
    got = _resend_items(s)
    s.close()
    assert [(g["patient_control_number"], g["source"]) for g in got] == [("CB0000L02C031", "837")], got
    assert "CB0000L02C024" in (got[0]["hint"] or ""), got[0]["hint"]


# ----------------------------------------------------------------------------------------------- answers-7
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("token", [False, True])
def test_a_claim_charged_days_before_its_837_went_out_is_listed_once(tmp_path, kind, ema_env, token):
    """(answers-7; the reviewer's test, with the patient named) Only the 837s know claim F.claim_id(91): a 99214 at $200,
    visit March 2, sent to Blue Cross on March 20; the practice posted the charge in EMA on March 3 and held the claim
    before sending it. No 835 answers it; the remittances reach May 1 (due April 19). EMA's Production report holds it as
    EMA claim 91 (patient 1, 'Sample, Bravo Q'), nothing posted on it, and the A/R does not pair it with a claim id. Another
    patient's EMA claim 92 has the same day, code and charge and was paid April 10. The member's earlier remittance names
    the claim's patient Bravo Sample. EMA claim 91 is the only claim of those lines that no payer answered, charged before
    the send, and it names that patient: it is this claim's, and the claim is listed once (the 837 pass's item, which
    carries EMA claim 91). With nothing naming the patient it cannot be told (the next tests: fx-exports-r1-4, r2-6).
    token: each claim also carries a J3590 billed at a token $0.01, nothing owed but part of its content. (The 837 was read
    before the reader kept the patient: with it, the 837 names the patient itself, tests/test_fx_passes.py.)"""
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import charge, denial_world, pay, remit as raw_remit
    s, lg = _ema_store(tmp_path, kind)
    rows = [charge(F.link_no(91), cpt="99214", amount=200, patient=1, posted=D(2026, 3, 3)),
            charge(F.link_no(92), cpt="99214", amount=200, patient=2, posted=D(2026, 3, 3)),
            pay(F.link_no(92), "PAPER-92", 110, cpt="99214", patient=2, posted=D(2026, 4, 10))]
    if token:
        rows += [charge(F.link_no(n), cpt="J3590", amount=0.01, patient=n - 90, posted=D(2026, 3, 3)) for n in (91, 92)]
    denial_world(s, lg, tmp_path, rows)
    for n in range(25):
        raw_remit(s, trace=f"BC{n:05d}", paid_on="2026-05-01", payer="BLUE CROSS TEST",
                  claims=[{"pcn": F.claim_id(200 + n), "status": "1", "member": f"MB{n:08d}", "last": "VOLUME", "first": f"B{n}",
                           "lines": [("2026-04-20", "99214", 200, 110, [("CO", "45", 90)])]}])
    raw_remit(s, trace="BCMEM91", paid_on="2026-02-20", payer="BLUE CROSS TEST",
              claims=[{"pcn": F.claim_id(95), "status": "1", "member": "MEMBER000091", "last": "SAMPLE", "first": "BRAVO",
                       "lines": [("2026-02-02", "99213", 150, 80, [("CO", "45", 70)])]}])
    bc = ("BLUE CROSS TEST", "BCT01")
    send(s, tmp_path, [clm837(F.claim_id(91), [("99214", "200.00", "20260302")] + ([("J3590", "0.01", "20260302")] if token else []),
                              payer=bc, member="MEMBER000091")], "20260320")
    _read_before_v2(s, F.claim_id(91))
    det = Detector(s, run_id="r1", today="2026-05-03")
    det.run()
    assert det.ema_errors == [], det.ema_errors
    lg.close()
    got = [dict(r) for r in s.query("SELECT finding_id, patient_control_number, ema_link, source, signal, title, shortfall_cents "
                                    "FROM findings WHERE signal='NO_ANSWER' AND COALESCE(stale,0)=0")]
    mine = [g for g in got if g["patient_control_number"] == F.claim_id(91) or g["ema_link"] == F.link_no(91)]
    assert len(mine) == 1, f"the one claim is listed {len(mine)} times: {[(g['source'], g['ema_link']) for g in mine]}"
    assert mine[0]["source"] == "837" and mine[0]["ema_link"] == F.link_no(91), mine
    s.close()


def _g8_world(tmp_path, kind, *, second_unanswered=False, member_name=None, other_bill=False):
    """The answers-7 world: an 837-only claim (F.claim_id(91), 99214 at $200, visit March 2, sent March 20 to Blue Cross,
    member MEMBER000091) that no 835 answers, EMA claim 91 charged March 3 with its lines (patient 'Sample, Bravo Q'), and
    another patient's EMA claim 92 of the same lines, paid April 10 (or, second_unanswered, not paid). member_name: an
    earlier visit's remittance naming the member's patient. other_bill: another bill with the same lines to Blue Cross,
    sent April 25 (not due yet), that no EMA claim is tied to. The 837s were read before the reader kept the patient (the
    837's own patient: tests/test_fx_passes.py)."""
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import charge, denial_world, pay, remit as raw_remit
    s, lg = _ema_store(tmp_path, kind)
    rows = [charge(F.link_no(91), cpt="99214", amount=200, patient=1, posted=D(2026, 3, 3)),
            charge(F.link_no(92), cpt="99214", amount=200, patient=2, posted=D(2026, 3, 3))]
    if not second_unanswered:
        rows.append(pay(F.link_no(92), "PAPER-92", 110, cpt="99214", patient=2, posted=D(2026, 4, 10)))
    else:       # Blue Cross still posts in EMA (Production's check needs a posting of the payer's in its last 30 days)
        rows += [charge(F.link_no(94), cpt="99212", amount=100, patient=3, svc=D(2026, 3, 9), posted=D(2026, 3, 10)),
                 pay(F.link_no(94), "PAPER-94", 60, cpt="99212", patient=3, svc=D(2026, 3, 9), posted=D(2026, 4, 20))]
    denial_world(s, lg, tmp_path, rows)
    for n in range(25):
        raw_remit(s, trace=f"BC{n:05d}", paid_on="2026-05-01", payer="BLUE CROSS TEST",
                  claims=[{"pcn": F.claim_id(200 + n), "status": "1", "member": f"MB{n:08d}", "last": "VOLUME", "first": f"B{n}",
                           "lines": [("2026-04-20", "99214", 200, 110, [("CO", "45", 90)])]}])
    if member_name:
        raw_remit(s, trace="BCMEM01", paid_on="2026-02-20", payer="BLUE CROSS TEST",
                  claims=[{"pcn": F.claim_id(95), "status": "1", "member": "MEMBER000091", "last": member_name[0], "first": member_name[1],
                           "lines": [("2026-02-02", "99213", 150, 80, [("CO", "45", 70)])]}])
    bc = ("BLUE CROSS TEST", "BCT01")
    send(s, tmp_path, [clm837(F.claim_id(91), [("99214", "200.00", "20260302")], payer=bc, member="MEMBER000091")], "20260320")
    if other_bill:
        send(s, tmp_path, [clm837(F.claim_id(93), [("99214", "200.00", "20260302")], payer=bc, member="MEMBER000093")], "20260425")
    _read_before_v2(s, F.claim_id(91), F.claim_id(93))
    det = Detector(s, run_id="r1", today="2026-05-03")
    det.run()
    assert det.ema_errors == [], det.ema_errors
    lg.close()
    got = [dict(r) for r in s.query("SELECT patient_control_number, ema_link, source FROM findings WHERE signal='NO_ANSWER' "
                                    "AND COALESCE(stale,0)=0")]
    s.close()
    own = [g for g in got if g["patient_control_number"] == F.claim_id(91)]
    g8 = [g for g in got if g["ema_link"] == F.link_no(91) and g["source"] != "837"]
    return own, g8


@pytest.mark.parametrize("kind", KINDS)
def test_a_claim_two_unanswered_ema_claims_could_be_takes_neither(tmp_path, kind, ema_env):
    """(answers-7, one to one) As above, but another patient's EMA claim 92 of the same lines was not paid either: EMA's
    claim for F.claim_id(91) cannot be told, so the claim keeps no EMA claim (never another patient's), and Production's
    check lists EMA claim 91 on its own as before."""
    from tests import ema_fixtures as F
    own, g8 = _g8_world(tmp_path, kind, second_unanswered=True)
    assert len(own) == 1 and own[0]["ema_link"] is None, own
    assert len(g8) == 1, g8
    assert F.link_no(91)


@pytest.mark.parametrize("kind", KINDS)
def test_a_claim_with_another_bill_of_the_same_lines_on_file_takes_no_ema_claim(tmp_path, kind, ema_env):
    """(answers-7, one to one) Another bill with the same lines to Blue Cross is on file (sent April 25, not due yet) and
    no EMA claim is tied to it: EMA claim 91 may be that bill's, so F.claim_id(91) keeps no EMA claim."""
    own, g8 = _g8_world(tmp_path, kind, other_bill=True)
    assert len(own) == 1 and own[0]["ema_link"] is None, own
    assert len(g8) == 1, g8


@pytest.mark.parametrize("kind", KINDS)
def test_a_claim_whose_patient_is_named_takes_only_that_patients_ema_claim(tmp_path, kind, ema_env):
    """(answers-7, the patient) The member's earlier remittance names its patient. When that is EMA claim 91's patient
    ('Sample, Bravo Q'), the claim takes EMA claim 91 and is listed once; when it names someone else, EMA claim 91 is not
    this claim's and both stay listed (the claim with no EMA claim, Production's check with EMA claim 91)."""
    from tests import ema_fixtures as F
    own, g8 = _g8_world(tmp_path / "same", kind, member_name=("SAMPLE", "BRAVO"))
    assert len(own) == 1 and own[0]["ema_link"] == F.link_no(91) and not g8, (own, g8)
    own, g8 = _g8_world(tmp_path / "other", kind, member_name=("OTHERPERSON", "ZED"))
    assert len(own) == 1 and own[0]["ema_link"] is None and len(g8) == 1, (own, g8)


def _paid_own_world(tmp_path, kind, *, ninety_four: bool):
    """(fx-exports-r1-4, r2-6) The mirror of answers-7: the claim's own EMA claim 91 (patient 1) was charged March 3 and Blue
    Cross paid it April 10 on a paper check (no 835 for it); nothing ties it to F.claim_id(91) (no A/R pair, no bill map,
    and the content is not one claim's). Patient 2's visit of the same day, code and charge (EMA claim 92), charged March
    3 on a paper claim no 837 or weekly export lists, was never answered. Nothing names claim F.claim_id(91)'s patient.
    ninety_four: Blue Cross still posts in EMA (Production's check needs a posting of the payer's in its last 30 days)."""
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import charge, denial_world, pay
    s, lg = _ema_store(tmp_path, kind)
    rows = [charge(F.link_no(91), cpt="99214", amount=200, patient=1, posted=D(2026, 3, 3)),
            pay(F.link_no(91), "PAPER-91", 110, cpt="99214", patient=1, posted=D(2026, 4, 10)),
            charge(F.link_no(92), cpt="99214", amount=200, patient=2, posted=D(2026, 3, 3))]
    if ninety_four:
        rows += [charge(F.link_no(94), cpt="99212", amount=100, patient=3, svc=D(2026, 3, 9), posted=D(2026, 3, 10)),
                 pay(F.link_no(94), "PAPER-94", 60, cpt="99212", patient=3, svc=D(2026, 3, 9), posted=D(2026, 4, 20))]
    denial_world(s, lg, tmp_path, rows)
    _volume(s)
    send(s, tmp_path, [clm837(F.claim_id(91), [("99214", "200.00", "20260302")], payer=BC, member="MEMBER000091")], "20260320")
    _read_before_v2(s, F.claim_id(91))         # nothing names the patient: the 837 too was read before it kept one
    det = Detector(s, run_id="r1", today="2026-05-03")
    det.run()
    assert det.ema_errors == [], det.ema_errors
    lg.close()
    got = [dict(r) for r in s.query("SELECT patient_control_number, ema_link, source FROM findings WHERE signal='NO_ANSWER' "
                                    "AND COALESCE(stale,0)=0")]
    s.close()
    carried = [g for g in got if g["patient_control_number"] == F.claim_id(91)]
    g8 = [g for g in got if g["ema_link"] == F.link_no(92) and g["source"] != "837"]
    return carried, g8, got


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("ninety_four", [True, False])
def test_the_before_send_bridge_never_takes_another_patients_claim_when_the_claims_own_was_answered(tmp_path, kind, ema_env,
                                                                                                   ninety_four):
    """(fx-exports-r1-4, r2-6; the reviewers' tests) EMA claim 92 is the only unanswered EMA claim of the claim's lines
    charged before its send, but it is patient 2's: the claim's own EMA claim is no candidate because it WAS answered.
    With nothing naming the claim's patient, the bridge takes nothing: the claim keeps no EMA claim, and Production's check
    keeps listing patient 2's EMA claim 92 on its own (before: the claim carried it, and Production's check left it)."""
    from tests import ema_fixtures as F
    carried, g8, got = _paid_own_world(tmp_path, kind, ninety_four=ninety_four)
    assert not any(g["ema_link"] == F.link_no(92) for g in carried), f"the claim carries patient 2's EMA claim 92: {carried}"
    assert len(g8) == 1, f"patient 2's never-answered EMA claim 92 is not listed on its own: {got}"


@pytest.mark.parametrize("kind", KINDS)
def test_an_answered_ema_claim_of_the_patient_with_the_claims_lines_blocks_the_bridge(tmp_path, kind, ema_env):
    """(fx-exports-r1-4) The member's earlier remittance names the claim's patient (patient 1, 'Sample, Bravo Q'). EMA holds
    two claims of patient 1 with the claim's lines charged before its send: EMA claim 91, answered by Blue Cross with $0
    and its reason (CO-16, information missing), and EMA claim 93, never answered. Either may be the claim's own: the
    bridge takes none, and Production's check lists EMA claim 93 on its own."""
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import adj, charge, denial_world, pay, remit as raw_remit
    s, lg = _ema_store(tmp_path, kind)
    rows = [charge(F.link_no(91), cpt="99214", amount=200, patient=1, posted=D(2026, 3, 3)),
            pay(F.link_no(91), "PAPER-91", 0, cpt="99214", patient=1, posted=D(2026, 4, 10)),
            adj(F.link_no(91), "CO-16", 200, cpt="99214", patient=1, posted=D(2026, 4, 10)),
            charge(F.link_no(93), cpt="99214", amount=200, patient=1, posted=D(2026, 3, 4)),
            charge(F.link_no(94), cpt="99212", amount=100, patient=3, svc=D(2026, 3, 9), posted=D(2026, 3, 10)),
            pay(F.link_no(94), "PAPER-94", 60, cpt="99212", patient=3, svc=D(2026, 3, 9), posted=D(2026, 4, 20))]
    denial_world(s, lg, tmp_path, rows)
    _volume(s)
    raw_remit(s, trace="BCMEM91", paid_on="2026-02-20", payer="BLUE CROSS TEST",
              claims=[{"pcn": F.claim_id(95), "status": "1", "member": "MEMBER000091", "last": "SAMPLE", "first": "BRAVO",
                       "lines": [("2026-02-02", "99213", 150, 80, [("CO", "45", 70)])]}])
    send(s, tmp_path, [clm837(F.claim_id(91), [("99214", "200.00", "20260302")], payer=BC, member="MEMBER000091")], "20260320")
    _read_before_v2(s, F.claim_id(91))         # nothing names the patient: the 837 too was read before it kept one
    det = Detector(s, run_id="r1", today="2026-05-03")
    det.run()
    assert det.ema_errors == [], det.ema_errors
    lg.close()
    got = [dict(r) for r in s.query("SELECT patient_control_number, ema_link, source FROM findings WHERE signal='NO_ANSWER' "
                                    "AND COALESCE(stale,0)=0")]
    s.close()
    own = [g for g in got if g["patient_control_number"] == F.claim_id(91)]
    assert own and own[0]["ema_link"] is None, f"the claim took one of two EMA claims of its patient that either may be: {own}"


# ----------------------------------------------------------------------------------------------- G8 (fix7b residuals)
@pytest.mark.parametrize("kind", KINDS)
def test_production_lists_one_service_once_and_prices_its_parts_at_their_own_rates(tmp_path, kind, ema_env):
    """(fix7b residuals: G8) EMA's Production report holds one patient's biopsy (88305, visit March 2) three times, none
    answered: EMA claim 81 charged March 3 for the whole service ($120), then EMA claims 82 and 83 charged March 10 for its
    reading (26, $45) and its technical part (TC, $75); EMA's weekly exports name the parts of 82 and 83 (submitted again
    April 25, not due yet there). Blue Cross pays 88305 at $80 whole, $30 for the 26 and $50 for the TC.
    One service is one item per part: the newer charges (the two parts) carry the whole service, so EMA claim 81 is not
    listed; the 26 is priced at $30 and the TC at $50 (before: three items, each part priced at the whole service's $80,
    $240 in all)."""
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import charge, denial_world, pay, remit as raw_remit
    from tests.test_workable import ema
    s, lg = _ema_store(tmp_path, kind)
    v = D(2026, 3, 2)
    rows = [charge(F.link_no(81), cpt="88305", amount=120, patient=1, svc=v, posted=D(2026, 3, 3)),
            charge(F.link_no(82), cpt="88305", amount=45, patient=1, svc=v, posted=D(2026, 3, 10)),
            charge(F.link_no(83), cpt="88305", amount=75, patient=1, svc=v, posted=D(2026, 3, 10)),
            charge(F.link_no(94), cpt="99212", amount=100, patient=3, svc=D(2026, 3, 9), posted=D(2026, 3, 10)),
            pay(F.link_no(94), "PAPER-94", 60, cpt="99212", patient=3, svc=D(2026, 3, 9), posted=D(2026, 4, 20))]
    ar = [F.ar_row(claim=F.claim_id(n), link=F.link_no(n), status="Payment Pending", current=amt, svc=v, patient=1, cpt="88305")
          for n, amt in ((82, 45), (83, 75))]
    denial_world(s, lg, tmp_path, rows, ar_rows=ar)
    from tests.test_features import claim, svc
    rates = [claim(700 + 3 * i + j, svc("88305", f"{chg:.2f}", f"{paid:.2f}", f"{paid:.2f}", f"CAS*CO*45*{chg - paid:.2f}~",
                                         dos="20260420", mods=part), dos="20260420")
             for i in range(25) for j, (part, chg, paid) in enumerate((("", 120, 80), ("26", 45, 30), ("TC", 75, 50)))]
    _remits(s, tmp_path, rates, "20260501", payer="BLUE CROSS TEST", payer_id="BCT01")
    for n, part, cents in ((82, "26", 4500), (83, "TC", 7500)):
        ema(s.conn, F.claim_id(n), cpt="88305", modifiers=json.dumps([part]), service_date=v.isoformat(), bill_created="2026-03-10",
            charges_cents=cents, payer=F.PAYERS["bcbs"], payer_id="BCT01", patient_id="EMA081")
        ema(s.conn, F.claim_id(n), family="claim_status", claim_status="Submitted", claim_submission_date="2026-04-25",
            service_date=v.isoformat(), payer=F.PAYERS["bcbs"], payer_id="BCT01", patient_id="EMA081")
    s.conn.commit()
    det = Detector(s, run_id="r1", today="2026-05-03")
    det.run()
    assert det.ema_errors == [], det.ema_errors
    lg.close()
    got = {(r["patient_control_number"] or f"link:{r['ema_link']}"): dict(r) for r in s.query(
        "SELECT patient_control_number, ema_link, shortfall_cents, evidence FROM findings WHERE signal='NO_ANSWER' "
        "AND COALESCE(stale,0)=0 AND ema_link IN (?, ?, ?)", (F.link_no(81), F.link_no(82), F.link_no(83)))}
    sizes = {k: v["shortfall_cents"] for k, v in got.items()}
    assert sizes == {F.claim_id(82): 3000, F.claim_id(83): 5000}, sizes
    s.close()


# ----------------------------------------------------------------------------------------------- send-day twins (fix7b)
def _twins_world(tmp_path, kind, *, token=False, exports=True, ben_answer=None, ben_billed_under=None):
    """(the residual of fix7b: the send-day twins) Two patients' claims of the same content (a 99214 at $200, visit March 2)
    went to Blue Cross the same day (March 20). EMA charged each the send day: Annie's EMA claim 61 was paid April 10,
    Bert's EMA claim 62 was not answered. token: each claim also carries a J3590 billed at a token $0.01.
    exports True: EMA's weekly exports list both claims (F.claim_id(61), F.claim_id(62), submitted March 20) under EMA's
    patients EMA061 and EMA062, whom EMA's reports name (another claim of each, paired by the A/R): the claims' own
    evidence. exports False: only the 837s know them, and only each member's earlier remittance names its patient."""
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import charge, denial_world, pay, remit as raw_remit
    from tests.test_workable import ema
    s, lg = _ema_store(tmp_path, kind)
    rows = [charge(F.link_no(61), cpt="99214", amount=200, patient="Twintest, Annie", posted=D(2026, 3, 20)),
            charge(F.link_no(62), cpt="99214", amount=200, patient="Twintest, Bert", posted=D(2026, 3, 20)),
            pay(F.link_no(61), "PAPER-61", 110, cpt="99214", patient="Twintest, Annie", posted=D(2026, 4, 10))]
    if token:
        rows += [charge(F.link_no(n), cpt="J3590", amount=0.01, patient=f"Twintest, {who}", posted=D(2026, 3, 20))
                 for n, who in ((61, "Annie"), (62, "Bert"))]
    ar = []
    if exports:
        for n, who in ((161, "Twintest, Annie"), (162, "Twintest, Bert")):
            r, a_ = _tie(n, who)
            rows, ar = rows + r, ar + a_
    denial_world(s, lg, tmp_path, rows, ar_rows=ar)
    _volume(s)
    lines = [("99214", "200.00", "20260302")] + ([("J3590", "0.01", "20260302")] if token else [])
    if exports:
        for n, pid in ((161, "EMA061"), (162, "EMA062")):
            _tie_claim(s, n, pid)
        for n, pid in ((61, "EMA061"), (62, "EMA062")):
            bc = dict(payer=F.PAYERS["bcbs"], payer_id="BCT01", patient_id=pid)
            ema(s.conn, F.claim_id(n), service_date="2026-03-02", bill_created="2026-03-20", charges_cents=20000, **bc)
            if token:
                ema(s.conn, F.claim_id(n), cpt="J3590", service_date="2026-03-02", bill_created="2026-03-20", charges_cents=1, **bc)
            ema(s.conn, F.claim_id(n), family="claim_status", claim_status="Submitted", claim_submission_date="2026-03-20",
                service_date="2026-03-02", **bc)
        s.conn.commit()
    else:
        for n, first in ((61, "ANNIE"), (62, "BERT")):
            raw_remit(s, trace=f"BCM{n}", paid_on="2026-02-20", payer="BLUE CROSS TEST",
                      claims=[{"pcn": F.claim_id(160 + n), "status": "1", "member": f"MEMBER0000{n}", "last": "TWINTEST", "first": first,
                               "lines": [("2026-02-02", "99213", 150, 80, [("CO", "45", 70)])]}])
        send(s, tmp_path, [clm837(F.claim_id(n), lines, payer=BC, member=f"MEMBER0000{n}") for n in (61, 62)], "20260320")
    det = Detector(s, run_id="r1", today="2026-05-03")
    det.run()
    assert det.ema_errors == [], det.ema_errors
    lg.close()
    got = {r["patient_control_number"] or f"link:{r['ema_link']}": r["ema_link"] for r in s.query(
        "SELECT patient_control_number, ema_link FROM findings WHERE signal='NO_ANSWER' AND COALESCE(stale,0)=0 "
        "AND (patient_control_number IN (?, ?) OR ema_link IN (?, ?))", (F.claim_id(61), F.claim_id(62), F.link_no(61), F.link_no(62)))}
    s.close()
    return got


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("token", [False, True])
def test_send_day_twins_of_two_patients_are_told_apart_by_the_patients_name(tmp_path, kind, ema_env, token):
    """(the residual of fix7b) The twin rule alone cannot tell which twin is whose (both claims stayed listed, Annie's paid
    claim with them); the patient's name, from the claims' own evidence (EMA's patients, named by EMA's reports), tells it:
    Annie's claim is answered, and Bert's alone is listed, with his EMA claim 62."""
    from tests import ema_fixtures as F
    got = _twins_world(tmp_path, kind, token=token, exports=True)
    assert got == {F.claim_id(62): F.link_no(62)}, got


@pytest.mark.parametrize("kind", KINDS)
def test_a_member_ids_one_remitted_name_never_chooses_a_send_day_twin(tmp_path, kind, ema_env):
    """(fx-exports-r1-5) The same two claims known only from the 837s, each member's earlier remittance naming its patient.
    A member id's one remitted name is no evidence of whose a claim is (a dependent's 837 carries the subscriber's id): it
    never chooses a twin: Bert's claim carries neither EMA claim (each twin is one of these claims, so the Production check
    leaves both to them). Annie's claim is answered all the same, by the answered rule: a payment by its own payer that
    still stands on her visit (_ema_by_patient, as before this branch)."""
    from tests import ema_fixtures as F
    got = _twins_world(tmp_path, kind, exports=False)
    assert got == {F.claim_id(62): None}, got


@pytest.mark.parametrize("kind", KINDS)
def test_a_childs_claim_never_takes_its_parents_send_day_twin_by_the_subscribers_name(tmp_path, kind, ema_env):
    """(fx-exports-r1-5, high; the reviewer's test) Annie and her son Ben are both on Annie's Blue Cross plan (subscriber id
    MEMBER000071). Both were seen March 2 for a 99214 at $200, and both bills went out March 20 (only the 837s know them;
    each 837 names the subscriber, Annie's id). EMA charged both the send day: Blue Cross answered Annie's EMA claim 61 April
    10 ($0 paid, $110 allowed to her deductible), Ben's EMA claim 62 was never answered. Only Annie had an earlier visit, so
    the remittances give her member id one name, Annie's. Ben's claim is not answered: it stays listed, never carrying his
    mother's EMA claim (before: both claims took EMA claim 61 by the subscriber's one name)."""
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import adj, charge, denial_world, pay, remit as raw_remit
    s, lg = _ema_store(tmp_path, kind)
    rows = [charge(F.link_no(61), cpt="99214", amount=200, patient="Twintest, Annie", posted=D(2026, 3, 20)),
            charge(F.link_no(62), cpt="99214", amount=200, patient="Twintest, Ben", posted=D(2026, 3, 20)),
            pay(F.link_no(61), "PAPER-61", 0, cpt="99214", patient="Twintest, Annie", posted=D(2026, 4, 10), allowed=110),
            adj(F.link_no(61), "CO-45", 90, cpt="99214", patient="Twintest, Annie", posted=D(2026, 4, 10), contractual=True)]
    denial_world(s, lg, tmp_path, rows)
    _volume(s)
    raw_remit(s, trace="BCM71", paid_on="2026-02-20", payer="BLUE CROSS TEST",
              claims=[{"pcn": F.claim_id(231), "status": "1", "member": "MEMBER000071", "last": "TWINTEST", "first": "ANNIE",
                       "lines": [("2026-02-02", "99213", 150, 80, [("CO", "45", 70)])]}])
    send(s, tmp_path, [clm837(F.claim_id(n), [("99214", "200.00", "20260302")], payer=BC, member="MEMBER000071") for n in (61, 62)],
         "20260320")
    det = Detector(s, run_id="r1", today="2026-05-03")
    det.run()
    assert det.ema_errors == [], det.ema_errors
    lg.close()
    got = {r["patient_control_number"] or f"link:{r['ema_link']}": r["ema_link"] for r in s.query(
        "SELECT patient_control_number, ema_link FROM findings WHERE signal='NO_ANSWER' AND COALESCE(stale,0)=0 "
        "AND (patient_control_number IN (?, ?) OR ema_link IN (?, ?))", (F.claim_id(61), F.claim_id(62), F.link_no(61), F.link_no(62)))}
    s.close()
    assert F.claim_id(62) in got, f"Ben's never-answered claim is not listed (answered by his mother's EMA claim): {got}"
    assert got[F.claim_id(62)] != F.link_no(61), f"Ben's claim carries his mother's EMA claim: {got}"


@pytest.mark.parametrize("kind", KINDS)
def test_two_claims_whose_names_choose_the_same_twin_take_neither(tmp_path, kind, ema_env):
    """(fx-exports-r1-5: a twin a name chose is used one to one) A father and his son of one name (Robert Testjones and
    Robert Testjones Jr: JR is dropped from the keyed name) each had a 99214 at $200 on March 2; EMA's weekly exports list
    both bills (F.claim_id(81), F.claim_id(82)), submitted March 20, under their own charts (EMA081S, EMA081J), whom EMA's
    reports name. EMA charged two claims of that content on March 20: the father's EMA claim 81, which Blue Cross answered
    with $0 (all to his deductible), and another patient's EMA claim 83, never answered; the son's own EMA claim was charged
    on another day. Both bills' names choose EMA claim 81, which can be only one of them: neither takes it, and both stay
    listed (before: both took it, and the son's unanswered bill was answered by his father's)."""
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import adj, charge, denial_world, pay
    from tests.test_workable import ema
    s, lg = _ema_store(tmp_path, kind)
    rows = [charge(F.link_no(81), cpt="99214", amount=200, patient="Testjones, Robert", posted=D(2026, 3, 20), dob=D(1950, 5, 5)),
            pay(F.link_no(81), "PAPER-81", 0, cpt="99214", patient="Testjones, Robert", posted=D(2026, 4, 10), allowed=110,
                dob=D(1950, 5, 5)),
            adj(F.link_no(81), "CO-45", 90, cpt="99214", patient="Testjones, Robert", posted=D(2026, 4, 10), contractual=True,
                dob=D(1950, 5, 5)),
            charge(F.link_no(83), cpt="99214", amount=200, patient="Othertest, Carl", posted=D(2026, 3, 20)),
            charge(F.link_no(84), cpt="99214", amount=200, patient="Testjones, Robert Jr", posted=D(2026, 3, 15),
                   dob=D(1980, 6, 6))]
    ar = []
    for n, who, dob in ((181, "Testjones, Robert", D(1950, 5, 5)), (182, "Testjones, Robert Jr", D(1980, 6, 6))):
        r, a_ = _tie(n, who, dob=dob)
        rows, ar = rows + r, ar + a_
    denial_world(s, lg, tmp_path, rows, ar_rows=ar)
    _volume(s)
    for n, pid in ((181, "EMA081S"), (182, "EMA081J")):
        _tie_claim(s, n, pid)
    for n, pid in ((81, "EMA081S"), (82, "EMA081J")):
        bc = dict(payer=F.PAYERS["bcbs"], payer_id="BCT01", patient_id=pid)
        ema(s.conn, F.claim_id(n), service_date="2026-03-02", bill_created="2026-03-20", charges_cents=20000, **bc)
        ema(s.conn, F.claim_id(n), family="claim_status", claim_status="Submitted", claim_submission_date="2026-03-20",
            service_date="2026-03-02", **bc)
    s.conn.commit()
    det = Detector(s, run_id="r1", today="2026-05-03")
    det.run()
    assert det.ema_errors == [], det.ema_errors
    lg.close()
    got = {r["patient_control_number"]: r["ema_link"] for r in s.query(
        "SELECT patient_control_number, ema_link FROM findings WHERE signal='NO_ANSWER' AND COALESCE(stale,0)=0 "
        "AND patient_control_number IN (?, ?)", (F.claim_id(81), F.claim_id(82)))}
    s.close()
    # the son's bill is never answered by his father's EMA claim. The father's own bill is answered by it: the birth date
    # with the name (EMA's patient for the father's bill) names EMA claim 81 his, which the keyed name alone, the same for
    # both, could not (the content bridge's patient choice, the release's August 2026 hand test)
    assert F.claim_id(82) in got, f"listed {got}: the son's bill was answered by his father's EMA claim"
    assert F.claim_id(81) not in got, f"listed {got}: the father's bill, answered on his own EMA claim 81, is still chased"
    assert F.link_no(81) not in got.values(), got


def _lone_twin_world(tmp_path, kind, *, ben_tied: bool = False):
    """(fx-exports-r1-3) Annie's claim F.claim_id(61) (a 99214 at $200, visit March 2) went to Blue Cross on March 20; only the
    837s know it, and her member's earlier remittance names her (MEMBER000061). No payer answered it, and EMA's Production
    report does not hold her charge. Bert's visit of the same day, code and charge was charged in EMA on March 20 too (EMA
    claim 62), on a paper claim no 837 or weekly export lists, and Blue Cross paid it April 10. Carl's visit of the same
    content was charged March 3 (EMA claim 63), so no content bridge can tell these claims apart.
    ben_tied: Bert is billed under Annie's member id elsewhere (his earlier claim's 837 names MEMBER000061, and the A/R pairs
    that claim with an EMA claim of his): he is on her plan, so her claim may be his (a dependent's 837 carries the
    subscriber's id), and his EMA claim may be its own."""
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import charge, denial_world, pay, remit as raw_remit
    s, lg = _ema_store(tmp_path, kind)
    rows = [charge(F.link_no(62), cpt="99214", amount=200, patient="Twintest, Bert", posted=D(2026, 3, 20)),
            pay(F.link_no(62), "PAPER-62", 110, cpt="99214", patient="Twintest, Bert", posted=D(2026, 4, 10)),
            charge(F.link_no(63), cpt="99214", amount=200, patient="Othertest, Carl", posted=D(2026, 3, 3))]
    ar = []
    if ben_tied:
        r, ar = _tie(64, "Twintest, Bert")
        rows += r
    denial_world(s, lg, tmp_path, rows, ar_rows=ar)
    _volume(s)
    raw_remit(s, trace="BCM61", paid_on="2026-02-20", payer="BLUE CROSS TEST",
              claims=[{"pcn": F.claim_id(221), "status": "1", "member": "MEMBER000061", "last": "TWINTEST", "first": "ANNIE",
                       "lines": [("2026-02-02", "99213", 150, 80, [("CO", "45", 70)])]}])
    if ben_tied:
        send(s, tmp_path, [clm837(F.claim_id(64), [("99212", "100.00", "20260202")], payer=BC, member="MEMBER000061")], "20260203")
    send(s, tmp_path, [clm837(F.claim_id(61), [("99214", "200.00", "20260302")], payer=BC, member="MEMBER000061")], "20260320")
    det = Detector(s, run_id="r1", today="2026-05-03")
    det.run()
    assert det.ema_errors == [], det.ema_errors
    lg.close()
    got = {r["patient_control_number"] or f"link:{r['ema_link']}": r["ema_link"] for r in s.query(
        "SELECT patient_control_number, ema_link FROM findings WHERE signal='NO_ANSWER' AND COALESCE(stale,0)=0 "
        "AND (patient_control_number=? OR ema_link=?)", (F.claim_id(61), F.link_no(62)))}
    counts = {k: v for k, v in det.ema_counts.items() if k.startswith("837")}
    s.close()
    return got, counts


@pytest.mark.parametrize("kind", KINDS)
def test_a_lone_send_day_twin_of_another_named_patient_does_not_answer_the_claim(tmp_path, kind, ema_env):
    """(fx-exports-r1-3; the reviewer's test) EMA claim 62 is Annie's claim's only send-day twin, and its patient is Bert,
    whom nothing ties to Annie's member id: it is not her claim's, so Bert's payment does not answer her claim. Annie's
    claim stays listed (no payer answered it), carrying no EMA claim."""
    from tests import ema_fixtures as F
    got, counts = _lone_twin_world(tmp_path, kind)
    assert F.claim_id(61) in got, f"Annie's never-answered claim is not listed: Bert's EMA claim answered it ({counts})"
    assert got.get(F.claim_id(61)) != F.link_no(62), "Annie's claim carries Bert's EMA claim"


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("other", ["untied", "held"])
def test_a_lone_send_day_twin_of_another_patient_never_answers_a_claim_emas_reports_name(tmp_path, kind, ema_env, other):
    """(fx-exports-r1-3, own evidence) Annie's claim F.claim_id(61) is in EMA's weekly exports (submitted March 20) under
    EMA's patient EMA061, whom EMA's reports name (another claim of hers, paired by the A/R). Her own EMA claim is not in
    Production's dates. Bert's EMA claim 62 of the same content, charged March 20 and paid April 10, is the claim's only
    send-day twin: EMA itself names him, not Annie, so it is not her claim's. Her claim stays listed. other: the third
    EMA claim of the content (which keeps a content bridge from finding hers) is Carl's, held by no bill, or Dora's, held by
    her own bill (then EMA's name alone tells Bert's claim from Annie's)."""
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import charge, denial_world, pay
    from tests.test_workable import ema
    s, lg = _ema_store(tmp_path, kind)
    rows = [charge(F.link_no(62), cpt="99214", amount=200, patient="Twintest, Bert", posted=D(2026, 3, 20)),
            pay(F.link_no(62), "PAPER-62", 110, cpt="99214", patient="Twintest, Bert", posted=D(2026, 4, 10))]
    tie_rows, tie_ar = _tie(161, "Twintest, Annie")
    if other == "untied":
        rows.append(charge(F.link_no(63), cpt="99214", amount=200, patient="Othertest, Carl", posted=D(2026, 3, 3)))
    else:
        rows += [charge(F.link_no(64), cpt="99214", amount=200, patient="Othertest, Dora", posted=D(2026, 3, 3)),
                 pay(F.link_no(64), "PAPER-64", 110, cpt="99214", patient="Othertest, Dora", posted=D(2026, 3, 25))]
        tie_ar = tie_ar + [F.ar_row(claim=F.claim_id(164), link=F.link_no(64), status="Payment Pending", current=0,
                                    svc=D(2026, 3, 2), patient="Othertest, Dora", cpt="99214")]
    denial_world(s, lg, tmp_path, rows + tie_rows, ar_rows=tie_ar)
    _volume(s)
    _tie_claim(s, 161, "EMA061")
    bc = dict(payer=F.PAYERS["bcbs"], payer_id="BCT01", patient_id="EMA061")
    ema(s.conn, F.claim_id(61), service_date="2026-03-02", bill_created="2026-03-20", charges_cents=20000, **bc)
    ema(s.conn, F.claim_id(61), family="claim_status", claim_status="Submitted", claim_submission_date="2026-03-20",
        service_date="2026-03-02", **bc)
    s.conn.commit()
    det = Detector(s, run_id="r1", today="2026-05-03")
    det.run()
    assert det.ema_errors == [], det.ema_errors
    lg.close()
    got = {r["patient_control_number"]: r["ema_link"] for r in s.query(
        "SELECT patient_control_number, ema_link FROM findings WHERE signal='NO_ANSWER' AND COALESCE(stale,0)=0 "
        "AND patient_control_number=?", (F.claim_id(61),))}
    s.close()
    assert got == {F.claim_id(61): None}, f"Annie's claim was answered by Bert's EMA claim, or carries it: {got}"


@pytest.mark.parametrize("kind", KINDS)
def test_a_lone_send_day_twin_of_a_patient_billed_under_the_claims_member_id_still_answers_it(tmp_path, kind, ema_env):
    """(fx-exports-r1-3, the other side) The same, with Bert billed under Annie's member id elsewhere: he is on her plan, so
    the member id's one remitted name (Annie's) says nothing against EMA claim 62 being the claim's own. The twins rule
    decides as before: the lone twin is the claim's EMA claim, paid, and the claim is not listed."""
    from tests import ema_fixtures as F
    got, counts = _lone_twin_world(tmp_path, kind, ben_tied=True)
    assert F.claim_id(61) not in got, f"listed though its own EMA claim (a dependent's) was paid: {got} ({counts})"


def _spelled_twin_world(tmp_path, kind, *, twin="Twintest, Annie Marie", decoy=False, bert_elsewhere=False, kin=False,
                        own_remit=False):
    """(the measurement of the fix round: a remittance and EMA spell one patient's name otherwise) Annie's claim
    F.claim_id(61) (a 99214 at $200, visit March 2) went to Blue Cross on March 20; only the 837s know it, and her member's
    earlier remittance names her TWINTEST, ANNIE (MEMBER000061). EMA charged one claim of that content on March 20 (EMA claim
    62, its patient `twin`), and Blue Cross paid it April 10. Dora's visit of the same content (EMA claim 64, charged March 3,
    paid) is held by her own bill (the A/R pairs it with F.claim_id(164)), so no content bridge finds Annie's claim: only
    the send-day twin rule does.
    decoy: Carl's visit of the same content, charged March 3 (EMA claim 63), that no bill holds: another EMA claim that may
    be Annie's own. bert_elsewhere: the twin's patient (Bert) has another visit whose bill (F.claim_id(65), paired by the A/R
    with his EMA claim 65) went out under another member id (MEMBER000099). kin: the twin's patient has another visit (EMA
    claim 66, paired with F.claim_id(66)) that Blue Cross answered naming TWINTEST, ANNIE under MEMBER000061. own_remit: the
    claim's own bill has a remittance naming the patient TWINTEST, ANNIE (a second payer's answer)."""
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import charge, denial_world, pay, remit as raw_remit
    s, lg = _ema_store(tmp_path, kind)
    rows = [charge(F.link_no(62), cpt="99214", amount=200, patient=twin, posted=D(2026, 3, 20)),
            pay(F.link_no(62), "PAPER-62", 110, cpt="99214", patient=twin, posted=D(2026, 4, 10)),
            charge(F.link_no(64), cpt="99214", amount=200, patient="Othertest, Dora", posted=D(2026, 3, 3)),
            pay(F.link_no(64), "PAPER-64", 110, cpt="99214", patient="Othertest, Dora", posted=D(2026, 3, 25))]
    ar = [F.ar_row(claim=F.claim_id(164), link=F.link_no(64), status="Payment Pending", current=0, svc=D(2026, 3, 2),
                   patient="Othertest, Dora", cpt="99214")]
    if decoy:
        rows.append(charge(F.link_no(63), cpt="99214", amount=200, patient="Othertest, Carl", posted=D(2026, 3, 3)))
    if bert_elsewhere:
        r, a_ = _tie(65, twin)
        rows, ar = rows + r, ar + a_
    if kin:
        r, a_ = _tie(66, twin)
        rows, ar = rows + r, ar + a_
    denial_world(s, lg, tmp_path, rows, ar_rows=ar)
    _volume(s)
    raw_remit(s, trace="BCM61", paid_on="2026-02-20", payer="BLUE CROSS TEST",
              claims=[{"pcn": F.claim_id(221), "status": "1", "member": "MEMBER000061", "last": "TWINTEST", "first": "ANNIE",
                       "lines": [("2026-02-01", "99213", 150, 80, [("CO", "45", 70)])]}])
    if kin:
        raw_remit(s, trace="BCM66", paid_on="2026-02-20", payer="BLUE CROSS TEST",
                  claims=[{"pcn": F.claim_id(66), "status": "1", "member": "MEMBER000061", "last": "TWINTEST", "first": "ANNIE",
                           "lines": [("2026-02-02", "99212", 100, 60, [("CO", "45", 40)])]}])
    if own_remit:
        raw_remit(s, trace="SUP61", paid_on="2026-04-20", payer="SUPPLEMENT TEST",
                  claims=[{"pcn": F.claim_id(61), "status": "2", "member": "SUPP0061", "last": "TWINTEST", "first": "ANNIE",
                           "lines": [("2026-03-02", "99214", 200, 0, [("OA", "23", 200)])]}])
    if bert_elsewhere:
        send(s, tmp_path, [clm837(F.claim_id(65), [("99212", "100.00", "20260202")], payer=BC, member="MEMBER000099")], "20260203")
    send(s, tmp_path, [clm837(F.claim_id(61), [("99214", "200.00", "20260302")], payer=BC, member="MEMBER000061")], "20260320")
    det = Detector(s, run_id="r1", today="2026-05-03")
    det.run()
    assert det.ema_errors == [], det.ema_errors
    lg.close()
    got = {r["patient_control_number"] or f"link:{r['ema_link']}": r["ema_link"] for r in s.query(
        "SELECT patient_control_number, ema_link FROM findings WHERE signal='NO_ANSWER' AND COALESCE(stale,0)=0 "
        "AND (patient_control_number=? OR ema_link=?)", (F.claim_id(61), F.link_no(62)))}
    counts = {k: v for k, v in det.ema_counts.items() if k.startswith("837")}
    s.close()
    return got, counts


@pytest.mark.parametrize("kind", KINDS)
def test_a_lone_twin_whose_ema_name_differs_from_the_remitted_name_is_the_claims_when_no_other_ema_claim_can_be(
        tmp_path, kind, ema_env):
    """(the fix round's measurement: six claims EMA shows paid were listed) A remittance and EMA can spell one person's name
    otherwise (here EMA keeps her middle name), and a dependent's claim carries the subscriber's member id: a name that
    differs proves nothing. EMA claim 62 is the only EMA claim of the claim's content that no bill holds (Dora's is her own
    bill's), so if EMA holds Annie's claim at all, it is this one: it answers her claim, and the paid claim is not listed
    (before: set aside for its name, the claim was listed as never answered)."""
    from tests import ema_fixtures as F
    got, counts = _spelled_twin_world(tmp_path, kind)
    assert F.claim_id(61) not in got, f"listed though its only possible EMA claim was paid: {got} ({counts})"


@pytest.mark.parametrize("kind", KINDS)
def test_a_lone_twin_of_a_patient_whose_bills_are_another_members_never_answers_the_claim(tmp_path, kind, ema_env):
    """(fx-exports-r1-3 on the bills on file) EMA claim 62 is Bert's, and Bert's other visit went out under another member
    id than Annie's claim: he is another patient, whatever else EMA holds. His payment does not answer her claim, which
    stays listed without his EMA claim."""
    from tests import ema_fixtures as F
    got, counts = _spelled_twin_world(tmp_path, kind, twin="Twintest, Bert", bert_elsewhere=True)
    assert F.claim_id(61) in got, f"Annie's claim was answered by another patient's EMA claim ({counts})"
    assert got[F.claim_id(61)] != F.link_no(62), got


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("own_remit", [False, True])
def test_a_twin_whose_patients_remittances_name_the_claims_patient_answers_it_though_ema_spells_the_name_otherwise(
        tmp_path, kind, ema_env, own_remit):
    """(the fix round's measurement: a Medicare claim whose secondary's remittance named the patient, listed though EMA
    showed it paid) EMA spells the twin's patient Annie Marie, the remittances Annie. Her other visit's remittance (its EMA
    claim paired with its bill) names TWINTEST, ANNIE under the claim's member id: remittances compared with remittances,
    the twin's patient is the claim's. Carl's EMA claim of the same content that no bill holds changes nothing: the twin
    answers the claim, which is not listed (before: EMA's spelling set the twin aside)."""
    from tests import ema_fixtures as F
    got, counts = _spelled_twin_world(tmp_path, kind, decoy=True, kin=True, own_remit=own_remit)
    assert F.claim_id(61) not in got, f"listed though its EMA claim (the same patient's) was paid: {got} ({counts})"


@pytest.mark.parametrize("kind", KINDS)
def test_a_print_batch_duplicate_made_after_the_visit_was_paid_is_not_listed(tmp_path, kind, ema_env):
    """(fix7b residual: the print-batch duplicate) Carla's visit of March 2 (a 99214 at $200) was billed, sent and paid by
    Blue Cross on March 28 (EMA claim 70). On April 20 EMA made the bill again (claim F.claim_id(71), EMA claim 71) and left
    it in its print batch, never sent. The duplicate is no money owed: the visit's payment still stands, whatever day it
    came (before: a claim still in the print batch was answered only by a posting after the day its bill was made, so the
    duplicate stayed listed as 'never sent'). The A/R does not list the duplicate; EMA's claim for it is its send-day twin
    (charged the day the bill was made), whose patient's other EMA claim shows the visit paid."""
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import charge, denial_world, pay
    from tests.test_workable import ema
    s, lg = _ema_store(tmp_path, kind)
    who = "Dupe, Carla"
    rows = [charge(F.link_no(70), cpt="99214", amount=200, patient=who, posted=D(2026, 3, 3)),
            pay(F.link_no(70), "PAPER-70", 110, cpt="99214", patient=who, posted=D(2026, 3, 28)),
            charge(F.link_no(71), cpt="99214", amount=200, patient=who, posted=D(2026, 4, 20), status="Batched for Printing")]
    denial_world(s, lg, tmp_path, rows)
    ema(s.conn, F.claim_id(71), service_date="2026-03-02", bill_created="2026-04-20", charges_cents=20000,
        payer=F.PAYERS["bcbs"], payer_id="BCT01", patient_id="EMA071")
    ema(s.conn, F.claim_id(71), family="claim_status", claim_status="Batched for Printing", service_date="2026-03-02",
        payer=F.PAYERS["bcbs"], payer_id="BCT01", patient_id="EMA071")
    # the weekly exports reach May 24 (another patient's claim made that day): the print batch has waited 34 days
    ema(s.conn, F.claim_id(75), service_date="2026-05-22", bill_created="2026-05-24", payer=F.PAYERS["bcbs"], payer_id="BCT01",
        patient_id="EMA075")
    s.conn.commit()
    det = Detector(s, run_id="r1", today="2026-05-25")
    det.run()
    assert det.ema_errors == [], det.ema_errors
    lg.close()
    got = [(r["signal"], r["patient_control_number"] or f"link:{r['ema_link']}") for r in s.query(
        "SELECT signal, patient_control_number, ema_link FROM findings WHERE signal IN ('NO_ANSWER','REJECTED_PRE_PAYER') "
        "AND COALESCE(stale,0)=0 AND (patient_control_number=? OR ema_link IN (?, ?))", (F.claim_id(71), F.link_no(70), F.link_no(71)))]
    assert got == [], f"the duplicate of a paid visit is listed: {got}"
    s.close()


# ----------------------------------------------------------------------------------------------- one visit, two charts (fix7b)
def _charts_world(tmp_path, kind, people, *, cross_pass=False):
    """The reading of a biopsy (88305-26 at $90, visit March 2) billed twice to Blue Cross: on F.claim_id(71), which EMA's
    weekly exports list (submitted March 3, EMA's patient EMA071A), and on F.claim_id(72), sent March 13 (cross_pass False:
    EMA's exports list it too, under EMA's patient EMA071B; True: only the 837s know it, and the A/R pairs it with EMA claim
    72). EMA charged each bill the day it went out (EMA claims 71 and 72). Neither is answered. people: for each of the two
    bills, (name, birth date, member id) of the patient EMA's reports name (each chart's other claim, and each bill's own EMA
    claim) and the member id its 837 names."""
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import charge, denial_world
    from tests.test_workable import ema
    s, lg = _ema_store(tmp_path, kind)
    (n1, d1, m1), (n2, d2, m2) = people
    rows, ar = _tie(171, n1, dob=d1)
    # each bill's own EMA claim, charged the day it went out (its send-day twin)
    rows += [charge(F.link_no(71), cpt="88305", amount=90, patient=n1, posted=D(2026, 3, 3), dob=d1, status="Payment Pending"),
             charge(F.link_no(72), cpt="88305", amount=90, patient=n2, posted=D(2026, 3, 13), dob=d2, status="Payment Pending")]
    if cross_pass:
        ar += [F.ar_row(claim=F.claim_id(72), link=F.link_no(72), status="Payment Pending", current=90, svc=SVC_, patient=n2,
                        cpt="88305")]
    else:
        r2, a2 = _tie(172, n2, dob=d2)
        rows, ar = rows + r2, ar + a2
    denial_world(s, lg, tmp_path, rows, ar_rows=ar)
    _volume(s)
    _tie_claim(s, 171, "EMA071A")
    if not cross_pass:
        _tie_claim(s, 172, "EMA071B")
    bills = [(71, "EMA071A", "2026-03-03", m1)] + ([] if cross_pass else [(72, "EMA071B", "2026-03-13", m2)])
    for n, pid, sent, member in bills:
        bc = dict(payer=F.PAYERS["bcbs"], payer_id="BCT01", patient_id=pid)
        ema(s.conn, F.claim_id(n), cpt="88305", modifiers='["26"]', service_date="2026-03-02", bill_created=sent,
            charges_cents=9000, **bc)
        ema(s.conn, F.claim_id(n), family="claim_status", claim_status="Submitted", claim_submission_date=sent,
            service_date="2026-03-02", **bc)
    s.conn.commit()
    send(s, tmp_path, [clm837(F.claim_id(71), [("88305", "90.00", "20260302", "26")], payer=BC, member=m1)], "20260303")
    send(s, tmp_path, [clm837(F.claim_id(72), [("88305", "90.00", "20260302", "26")], payer=BC, member=m2)], "20260313")
    found = _chased(s, "t1", today="2026-05-03")
    lg.close()
    s.close()
    return {k for k in found if k in (F.claim_id(71), F.claim_id(72))}


@pytest.mark.parametrize("kind", KINDS)
def test_one_person_under_two_ema_patient_ids_is_one_patient_for_the_one_visit_rule(tmp_path, kind, ema_env):
    """(the residual of fix7b) The second bill went under a second chart EMA opened for the same person: the two bills
    carry two EMA patient ids, and EMA's reports name both charts' patient with the same name and birth date. One person,
    one visit: chased once, under the newer bill (before: matched by the EMA patient id alone, the bills stayed two
    items). The same name under the same member id joins them too."""
    from tests import ema_fixtures as F
    bravo, dob = F.PATIENTS[1][0], F.PATIENTS[1][1]
    assert _charts_world(tmp_path / "dob", kind, [(bravo, dob, "MEMBER000071"), (bravo, dob, "MEMBER000072")]) == {F.claim_id(72)}
    assert _charts_world(tmp_path / "mem", kind, [(bravo, None, "MEMBER000071"), (bravo, None, "MEMBER000071")]) == {F.claim_id(72)}


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("cross_pass", [False, True])
def test_a_father_and_son_of_one_name_are_two_patients_for_the_one_visit_rules(tmp_path, kind, ema_env, cross_pass):
    """(fx-exports-r2-4) A father and his son, both named Robert Testjones (the son is 'Jr', which the keyed name drops),
    each on his own plan and with his own chart; EMA's reports name each with his own birth date. Both had the reading on
    March 2. Two men, two visits: both bills stay chased, whether EMA's exports list both (the exports' one-visit rule) or
    only the first (the yield to a newer bill only the 837s know). Never the keyed name alone."""
    from tests import ema_fixtures as F
    got = _charts_world(tmp_path, kind, [("Testjones, Robert", D(1950, 5, 5), "WSRJR00001"),
                                         ("Testjones, Robert Jr", D(1980, 6, 6), "WSRJR00002")], cross_pass=cross_pass)
    assert got == {F.claim_id(71), F.claim_id(72)}, f"listed {sorted(got)}: the father's visit was handed to his son's bill"


def _father_and_son(s, tmp_path) -> None:
    """(fx-exports-r2-4; the reviewer's world) Each man's earlier visit was remitted under his own member id (WSRJR00001,
    WSRJR00002); the keyed name of 'TESTJONES ROBERT' and 'TESTJONES ROBERT JR' is one."""
    _salted(s)
    _remits(s, tmp_path, _visits() + [_member_visit("CB0000J90C017", "WSRJR00001", "TESTJONES", "ROBERT"),
                                      _member_visit("CB0000J95C017", "WSRJR00002", "TESTJONES", "ROBERT JR")], "20261110")


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("cross_pass", [False, True])
def test_a_father_and_son_named_only_by_their_members_remittances_stay_two_patients(tmp_path, kind, cross_pass):
    """(fx-exports-r2-4; the reviewer's tests) The father's bill CB0000J91 (EMA's exports, chart EMA091S, sent September 15)
    and the son's CB0000J92, sent September 25: in EMA's exports too (chart EMA091J), or (cross_pass) only in the 837s.
    Neither answered. Two men: both stay chased."""
    from tests.test_workable import ema
    s = _store(tmp_path, kind)
    _father_and_son(s, tmp_path)
    v26 = [("88305", "90.00", "20260914", "26")]
    bills = [("CB0000J91", "EMA091S", "2026-09-15", "WSRJR00001")] + ([] if cross_pass else [("CB0000J92", "EMA091J", "2026-09-25", "WSRJR00002")])
    for bill, pid, sent, member in bills:
        ema(s.conn, bill + "C017", cpt="88305", modifiers='["26"]', charges_cents=9000, patient_id=pid)
        ema(s.conn, bill + "C017", family="claim_status", claim_status="Submitted", claim_submission_date=sent, patient_id=pid)
        s.conn.commit()
        send(s, tmp_path, [clm837(bill + "C017", v26, member=member)], sent.replace("-", ""))
    if cross_pass:
        send(s, tmp_path, [clm837("CB0000J92C017", v26, member="WSRJR00002")], "20260925")
    found = _chased(s, "t1")
    assert {"CB0000J91C017", "CB0000J92C017"} <= set(found), (
        f"listed {sorted(k for k in found if k.startswith('CB0000J9'))}: the father's visit was handed to his son's bill")
    s.close()


# =============================================================================================== the reviews' round (r1, r2)
# The two reviews of this branch (fx-exports-r1 and r2) found these; each test is the reviewer's, ported with invented data.

# ----------------------------------------------------------------------------------------------- the print batch (r1-1, r2-1, r2-7)
def _print_batch_rebill(tmp_path, kind, *, earlier: str):
    """Carla's visit of March 2 (a 99214 at $200) was billed on EMA claim 70 and answered by Blue Cross on its 835 of March
    28. On April 20 the practice made the bill again (claim F.claim_id(71), EMA claim 71) and EMA left it in its print batch:
    it never went out. EMA's weekly exports reach May 24 (another patient's claim made that day), so it has waited 34 days.
    earlier: 'denied' (the 835 pays $0 with CO-16, EMA posts the $0 answer with its reason), 'reversed' (Blue Cross paid
    $110 and took it back, status 22, before the rebill; EMA shows the payment and its takeback), or 'paid' (paid $110 and
    it stands). Returns the never-answered items of the visit and the run's counts."""
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import adj, charge, denial_world, pay, remit as raw_remit
    from tests.test_workable import ema
    s, lg = _ema_store(tmp_path, kind)
    who = "Dupe, Carla"
    paid = 0 if earlier == "denied" else 110
    raw_remit(s, trace=F.check_no(70), paid_on="2026-03-28",
              claims=[{"pcn": F.claim_id(70), "last": "DUPE", "first": "CARLA", "member": "MEMBERCARLA1",
                       "lines": [("2026-03-02", "99214", 200, paid, [("CO", "16", 200)] if earlier == "denied" else [("CO", "45", 90)])]}])
    if earlier == "reversed":
        raw_remit(s, trace=F.check_no(170), paid_on="2026-04-10", status="22",
                  claims=[{"pcn": F.claim_id(70), "last": "DUPE", "first": "CARLA", "member": "MEMBERCARLA1", "status": "22",
                           "lines": [("2026-03-02", "99214", -200, -110, [("CO", "45", -90)])]}])
    rows = [charge(F.link_no(70), cpt="99214", amount=200, patient=who, posted=D(2026, 3, 3)),
            pay(F.link_no(70), F.check_no(70), paid, cpt="99214", patient=who, posted=D(2026, 3, 31)),
            charge(F.link_no(71), cpt="99214", amount=200, patient=who, posted=D(2026, 4, 20), status="Batched for Printing")]
    if earlier == "denied":
        rows.append(adj(F.link_no(70), "CO-16", 200, cpt="99214", patient=who, posted=D(2026, 3, 31)))
    if earlier == "reversed":
        rows.append(pay(F.link_no(70), F.check_no(170), -110, cpt="99214", patient=who, posted=D(2026, 4, 12)))
    denial_world(s, lg, tmp_path, rows)
    ema(s.conn, F.claim_id(71), service_date="2026-03-02", bill_created="2026-04-20", charges_cents=20000,
        payer=F.PAYERS["bcbs"], payer_id="BCT01", patient_id="EMA071")
    ema(s.conn, F.claim_id(71), family="claim_status", claim_status="Batched for Printing", service_date="2026-03-02",
        payer=F.PAYERS["bcbs"], payer_id="BCT01", patient_id="EMA071")
    ema(s.conn, F.claim_id(75), service_date="2026-05-22", bill_created="2026-05-24", payer=F.PAYERS["bcbs"], payer_id="BCT01",
        patient_id="EMA075")
    s.conn.commit()
    det = Detector(s, run_id="r1", today="2026-05-25")
    det.run()
    assert det.ema_errors == [], det.ema_errors
    lg.close()
    got = [(r["signal"], r["patient_control_number"] or f"link:{r['ema_link']}") for r in s.query(
        "SELECT signal, patient_control_number, ema_link FROM findings WHERE signal IN ('NO_ANSWER','REJECTED_PRE_PAYER') "
        "AND COALESCE(stale,0)=0 AND (patient_control_number=? OR ema_link IN (?, ?))", (F.claim_id(71), F.link_no(70), F.link_no(71)))]
    counts = {k: v for k, v in det.ema_counts.items() if k.startswith("export")}
    s.close()
    return got, counts


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("earlier", ["denied", "reversed"])
def test_a_print_batch_rebill_of_a_visit_the_payer_denied_is_still_listed_to_be_sent(tmp_path, kind, ema_env, earlier):
    """(fx-exports-r1-1, high) The visit was never paid: the earlier bill was denied (or its payment was taken back). The
    corrected bill is money owed that only goes out if someone prints and mails it: it stays listed (REJECTED_PRE_PAYER,
    'In EMA's print batch, never sent'). Before the fix, a claim still in the print batch had no 'after' day, so the
    earlier denial on another bill (a primary answer) answered it, and nothing listed it."""
    from tests import ema_fixtures as F
    got, counts = _print_batch_rebill(tmp_path, kind, earlier=earlier)
    assert ("REJECTED_PRE_PAYER", F.claim_id(71)) in got, (
        f"the corrected bill of a visit never paid, still in EMA's print batch, is not listed: {got} (counts {counts})")


@pytest.mark.parametrize("kind", KINDS)
def test_a_print_batch_rebill_of_a_visit_paid_on_its_835_is_a_duplicate(tmp_path, kind, ema_env):
    """(the residual of fix7b, kept) The same, with Blue Cross's payment of $110 standing on the earlier bill: the bill made
    again is a duplicate of a paid visit and is not listed."""
    got, counts = _print_batch_rebill(tmp_path, kind, earlier="paid")
    assert got == [], f"the duplicate of a paid visit is listed: {got} (counts {counts})"
    assert counts.get("export_print_batch_paid") == 1, counts


def _corrected_in_print_batch(tmp_path, kind, *, denial_on_other_bill: bool):
    """(fx-exports-r2-1) Dana's visit of March 2 (a 99214 at $200, EMA claim 70) was billed to Blue Cross and sent March 5.
    - denial_on_other_bill False: claim C001 of bill 70 was denied by Blue Cross March 28 (835: CO-16; EMA posted the $0
      answer with its reason the same day). The biller corrected the claim; EMA made it as submission C002 of the same bill
      and left it in its print batch. Never sent.
    - denial_on_other_bill True: the denied claim was bill 69's; the corrected bill 70 C001 waits in the print batch.
    Today May 25; EMA's weekly exports reach May 24."""
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import adj, charge, denial_world, pay, remit as raw_remit
    from tests.test_workable import ema
    s, lg = _ema_store(tmp_path, kind)
    who = "Fixit, Dana"
    rows = [charge(F.link_no(70), cpt="99214", amount=200, patient=who, posted=D(2026, 3, 3), status="Denied"),
            pay(F.link_no(70), "PAPER-70", 0, cpt="99214", patient=who, posted=D(2026, 3, 28), status="Denied"),
            adj(F.link_no(70), "CO-16", 200, cpt="99214", patient=who, posted=D(2026, 3, 28), status="Denied")]
    denial_world(s, lg, tmp_path, rows)
    denied = F.claim_id(69) if denial_on_other_bill else F.claim_id(70)
    raw_remit(s, trace="BCDEN0070", paid_on="2026-03-28", payer="BLUE CROSS TEST",
              claims=[{"pcn": denied, "status": "4", "member": "MEMBER000070", "last": "FIXIT", "first": "DANA",
                       "lines": [("2026-03-02", "99214", 200, 0, [("CO", "16", 200)])]}])
    bc = dict(payer=F.PAYERS["bcbs"], payer_id="BCT01", patient_id="EMA070")
    ema(s.conn, denied, service_date="2026-03-02", bill_created="2026-03-03", charges_cents=20000, **bc)
    ema(s.conn, denied, family="claim_status", claim_status="Denied", claim_submission_date="2026-03-05", service_date="2026-03-02", **bc)
    corrected = F.claim_id(70) if denial_on_other_bill else F.claim_id(70, 2)
    ema(s.conn, corrected, service_date="2026-03-02", bill_created="2026-04-02" if denial_on_other_bill else "2026-03-03",
        charges_cents=20000, **bc)
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
    counts = dict(det.ema_counts)
    s.close()
    return got, counts, corrected


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("other_bill", [False, True])
def test_a_corrected_claim_left_in_the_print_batch_after_its_denial_is_still_listed(tmp_path, kind, ema_env, other_bill):
    """(fx-exports-r2-1) The corrected claim never went out: the denial of March 28 answered the earlier submission (or
    another bill), not this one, and no payment stands on the visit. It stays listed, to be sent."""
    got, counts, corrected = _corrected_in_print_batch(tmp_path, kind, denial_on_other_bill=other_bill)
    assert got, f"the corrected claim {corrected}, never sent, is on no list (export_answered_in_ema={counts.get('export_answered_in_ema')})"


@pytest.mark.parametrize("kind", KINDS)
def test_a_claim_never_sent_is_listed_to_be_sent_whatever_stray_remittance_is_newer(tmp_path, kind, ema_env):
    """(fx-exports-r2-7) Blue Cross's and Humana's remittances reach April 30 (the feed date); one small payer's single file
    is dated May 22. EMA made bill CB0000P01 on April 20 and left it in its print batch: never sent. EMA's weekly exports
    reach May 24, so it has waited 34 days to be sent (due May 20). Nothing in EMA's reports has its lines. It is the
    practice's own work: listed, never held back as an answer no source can show."""
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import denial_world, remit as raw_remit
    from tests.test_workable import ema
    s, lg = _ema_store(tmp_path, kind)
    denial_world(s, lg, tmp_path, [])
    for n in range(30):
        raw_remit(s, trace=f"BC{n:05d}", paid_on="2026-04-30", payer="BLUE CROSS TEST",
                  claims=[{"pcn": F.claim_id(200 + n), "status": "1", "member": f"MB{n:08d}", "last": "VOLUME", "first": f"B{n}",
                           "lines": [("2026-04-01", "99212", 100, 60, [("CO", "45", 40)])]}])
        raw_remit(s, trace=f"HU{n:05d}", paid_on="2026-04-30", payer="HUMANA",
                  claims=[{"pcn": F.claim_id(300 + n), "status": "1", "member": f"MH{n:08d}", "last": "VOLUME", "first": f"H{n}",
                           "lines": [("2026-04-01", "99212", 100, 60, [("CO", "45", 40)])]}])
    raw_remit(s, trace="UH00001", paid_on="2026-05-22", payer="UNITEDHEALTHCARE",
              claims=[{"pcn": F.claim_id(400), "status": "1", "member": "MU00000001", "last": "STRAY", "first": "U",
                       "lines": [("2026-05-01", "99212", 100, 60, [("CO", "45", 40)])]}])
    from remitwatch.detect import feed_date
    assert feed_date(s.conn) == "2026-04-30"
    bc = dict(payer=F.PAYERS["bcbs"], payer_id="BCT01", patient_id="EMAP01")
    ema(s.conn, "CB0000P01C017", service_date="2026-04-17", bill_created="2026-04-20", charges_cents=22365, **bc)
    ema(s.conn, "CB0000P01C017", family="claim_status", claim_status="Batched for Printing", service_date="2026-04-17", **bc)
    ema(s.conn, F.claim_id(75), service_date="2026-05-22", bill_created="2026-05-24", payer=F.PAYERS["bcbs"], payer_id="BCT01",
        patient_id="EMA075")
    s.conn.commit()
    det = Detector(s, run_id="r1", today="2026-05-25")
    det.run()
    assert det.ema_errors == [], det.ema_errors
    lg.close()
    got = [(r["signal"], json.loads(r["evidence"]).get("not_sent")) for r in s.query(
        "SELECT signal, evidence FROM findings WHERE patient_control_number='CB0000P01C017' AND COALESCE(stale,0)=0")]
    unseen = s.conn.execute("SELECT value FROM meta WHERE key='ema:unanswered_unseen'").fetchone()
    s.close()
    assert got == [("REJECTED_PRE_PAYER", True)], (
        f"the claim never sent is listed as {got}; it waits as an answer no source can check: {json.loads(unseen[0]) if unseen else None}")


# ----------------------------------------------------------------------------------------------- the waiting view (F3a's need)
@pytest.mark.parametrize("kind", KINDS)
def test_an_exports_claim_not_due_yet_is_noted_for_the_waiting_view(tmp_path, kind, monkeypatch):
    """(fx-837's need for F3b) Bill CB0000B92, which EMA's weekly exports list as Submitted November 1, is not due before
    Anthem's answer days run out, so no list chases it yet. It is noted for the waiting view the 837 pass keeps
    (Detector._note_waiting, the fix wave's F3a; stood in for here, where that view is not built), with its group, send
    day, due day and billed dollars, as the 837 pass notes its own claims."""
    from remitwatch import detect as Dm
    from tests.test_workable import ema
    noted = []
    monkeypatch.setattr(Dm.Detector, "_note_waiting", lambda self, *a: noted.append(a), raising=False)
    s = _store(tmp_path, kind)
    _remits(s, tmp_path, _visits(), "20261110")
    ema(s.conn, "CB0000B92C017", service_date="2026-10-30", bill_created="2026-11-01")
    ema(s.conn, "CB0000B92C017", family="claim_status", claim_status="Submitted", claim_submission_date="2026-11-01",
        service_date="2026-10-30")
    s.conn.commit()
    Detector(s, run_id="t1").run()
    s.close()
    assert [(a[0], a[2], a[4]) for a in noted] == [("CB0000B92C017", "2026-11-01", 22365)] and noted[0][3] > "2026-11-10", noted


# ----------------------------------------------------------------------------------------------- a fold the self-check reads
@pytest.mark.parametrize("kind", KINDS)
def test_an_export_claim_listed_before_gives_its_visit_to_a_newer_bill_and_is_not_kept_by_the_self_check(tmp_path, kind):
    """(the exports' one-visit rule, with the self-check that keeps an item no rule explains) EMA's weekly exports list bill
    CB0000N81 (a 99214 of September 14, sent September 15, EMA patient P0000N81), never answered: listed. The practice
    makes the bill again as CB0000N82 for the same patient and visit (sent September 25), which the next exports list. The
    next run chases the visit under the newer bill only, and records where the older claim's chase went (the newer item's
    evidence, 'folded'), so the self-check does not keep the older item listed as unexplained."""
    from remitwatch.detect import _fid
    from tests.test_workable import ema
    s = _store(tmp_path, kind)
    _remits(s, tmp_path, _visits(), "20261110")
    ema(s.conn, "CB0000N81C017", patient_id="P0000N81")
    ema(s.conn, "CB0000N81C017", family="claim_status", claim_status="Submitted", claim_submission_date="2026-09-15",
        patient_id="P0000N81")
    s.conn.commit()
    found = _chased(s, "t1")
    assert "CB0000N81C017" in found, sorted(found)
    ema(s.conn, "CB0000N82C017", patient_id="P0000N81", bill_created="2026-09-25")
    ema(s.conn, "CB0000N82C017", family="claim_status", claim_status="Submitted", claim_submission_date="2026-09-25",
        patient_id="P0000N81")
    s.conn.commit()
    found = _chased(s, "t2")
    mine = sorted(k for k in found if k.startswith("CB0000N8"))
    assert mine == ["CB0000N82C017"], f"listed {mine}: {[(k, (found[k]['hint'] or '')[:80]) for k in mine]}"
    folded = json.loads(found["CB0000N82C017"]["evidence"]).get("folded") or []
    assert [x["finding_id"] for x in folded] == [_fid("EMA_OPEN_CLAIM", "CB0000N81C017")], folded
    s.close()


# ----------------------------------------------------------------------------------------------- one name, one method
def test_no_method_of_the_detector_is_defined_twice():
    """(found on the world copy) A method defined twice in one class silently replaces the first: this branch's patient
    matcher once took the name of the MPPR rule's own `_same_person`, and the detection run failed on the real data."""
    import ast
    import collections
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "remitwatch", "detect.py")
    tree = ast.parse(open(path).read())
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            names = collections.Counter(n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))
            assert not [n for n, c in names.items() if c > 1], (node.name, [n for n, c in names.items() if c > 1])
