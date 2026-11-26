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


@pytest.mark.parametrize("kind", KINDS)
def test_a_visit_the_exports_chase_yields_to_a_newer_bill_only_the_837s_know(tmp_path, kind):
    """(answers-1, the exports' side; the reviewer's and the verifier's test) EMA's weekly exports list bill CB0000M31
    (C017, sent September 15, its 837 on file for member WDUP000002). No payer answers it; a person starts working it.
    The practice then makes the bill again as CB0000M32 and sends it September 25; only the 837s know it yet. Neither is
    answered (remittances to November 10). One visit of one patient (the same day, code and part) is one unpaid service:
    only the newer bill carries it, and the person's work on the older item follows it.

    The patient is the same person by name, not by the subscriber's id alone: an earlier visit's remittance names the
    member's patient (the review's identity: the member id plus the 835 name key for the member). The reviewer's own
    scenario had no name anywhere; two people on one subscriber id are two services (the next test), so with nothing
    naming the patient both items stay (see plans/progress/fx-exports.md: the 837's own patient loop, read by F3a, would
    name it)."""
    from remitwatch.actions import apply_status
    from remitwatch.detect import _fid
    from tests.test_workable import ema
    s = _store(tmp_path, kind)
    _salted(s)
    member = "WDUP000002"
    _remits(s, tmp_path, _visits() + [_member_visit("CB0000M30C017", member)], "20261110")
    ema(s.conn, "CB0000M31C017")
    ema(s.conn, "CB0000M31C017", family="claim_status", claim_status="Submitted", claim_submission_date="2026-09-15")
    s.conn.commit()
    send(s, tmp_path, [clm837("CB0000M31C017", V, member=member)], "20260915")
    found = _chased(s, "t1")
    assert set(found) == {"CB0000M31C017"}, sorted(found)
    apply_status(s.conn, found["CB0000M31C017"]["finding_id"], "in_progress", "Called Anthem: no record of it", actor="biller")
    s.conn.commit()
    send(s, tmp_path, [clm837("CB0000M32C017", V, member=member)], "20260925")
    found = _chased(s, "t2")
    assert set(found) == {"CB0000M32C017"}, (
        f"listed {sorted(found)}: the same patient's one visit (day, code) sent under two bills is one unpaid service")
    f = found["CB0000M32C017"]
    assert f["finding_id"] == _fid("837_OPEN_CLAIM", "CB0000M32C017")
    assert f["status"] == "in_progress" and "no record of it" in (f["notes"] or ""), (f["status"], f["notes"])
    assert "CB0000M31C017" in (f["hint"] or ""), f["hint"]
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
def test_an_export_claim_keeps_the_services_the_newer_bill_does_not_carry(tmp_path, kind):
    """(answers-1) The exports' bill CB0000M51 (sent September 15) carries a 99214 and a 17110 of one visit; the newer bill
    CB0000M52 (September 25, only the 837s know it) carries the 99214 alone. The 99214 is chased under the newer bill; the
    17110 stays on the exports' item, sized at Anthem's $80 for it."""
    from tests.test_features import claim, svc
    from tests.test_workable import ema
    s = _store(tmp_path, kind)
    _salted(s)
    member = "WDUP000005"
    rates = [claim(600 + i, svc("17110", "200.00", "80.00", "80.00", "CAS*CO*45*120.00~")) for i in range(25)]
    _remits(s, tmp_path, rates + _visits() + [_member_visit("CB0000M50C017", member)], "20261110")
    ema(s.conn, "CB0000M51C017")
    ema(s.conn, "CB0000M51C017", cpt="17110", charges_cents=20000)
    ema(s.conn, "CB0000M51C017", family="claim_status", claim_status="Submitted", claim_submission_date="2026-09-15")
    s.conn.commit()
    send(s, tmp_path, [clm837("CB0000M51C017", V + [("17110", "200.00", "20260914")], member=member)], "20260915")
    send(s, tmp_path, [clm837("CB0000M52C017", V, member=member)], "20260925")
    found = _chased(s, "t1")
    assert set(found) == {"CB0000M51C017", "CB0000M52C017"}, sorted(found)
    m51 = found["CB0000M51C017"]
    assert [ln[0] for ln in json.loads(m51["evidence"])["lines"]] == ["17110"] and m51["shortfall_cents"] == 8000, (
        json.loads(m51["evidence"])["lines"], m51["shortfall_cents"])
    assert [ln[0] for ln in json.loads(found["CB0000M52C017"]["evidence"])["lines"]] == ["99214"]
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
    send(s, tmp_path, [clm837("CB0000L02C017", v1), clm837("CB0000L03C017", v1)], "20260801")
    _remits(s, tmp_path, [answer("CB0000L02C017", dos="20260730"), answer("CB0000L03C017", dos="20260730")], "20260820")
    send(s, tmp_path, [clm837("CB0000L02C024", v2, freq="7"), clm837("CB0000L03C024", v2, freq="7")], "20260901")
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
    # L02's item is due from the newer send (the payer's answer to the bill is due from September 20, not September 1) and
    # names the submission the 837s show, so the biller asks the payer about it
    f = dict(s.query("SELECT title, hint, evidence FROM findings WHERE patient_control_number='CB0000L02C024'")[0])
    ev = json.loads(f["evidence"])
    assert ev["sent"] == "2026-09-20" and ev["bill_sent_again_837"] == {"day": "2026-09-20", "claim_id": "CB0000L02C031"}, ev
    assert "CB0000L02C031" in (f["hint"] or "") and "2026-09-20" in f["title"], (f["title"], f["hint"])
    s.close()


# ----------------------------------------------------------------------------------------------- answers-7
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("token", [False, True])
def test_a_claim_charged_days_before_its_837_went_out_is_listed_once(tmp_path, kind, ema_env, token):
    """(answers-7; the reviewer's test) Only the 837s know claim F.claim_id(91): a 99214 at $200, visit March 2, sent to
    Blue Cross on March 20; the practice posted the charge in EMA on March 3 and held the claim before sending it. No 835
    answers it; the remittances reach May 1 (due April 19). EMA's Production report holds it as EMA claim 91 (patient 1),
    nothing posted on it, and the A/R does not pair it with a claim id. Another patient's EMA claim 92 has the same day,
    code and charge and was paid April 10. EMA claim 91 is the only claim of those lines that no payer answered, charged
    before the send: it is this claim's, and the claim is listed once (the 837 pass's item, which carries EMA claim 91).
    token: each claim also carries a J3590 billed at a token $0.01, nothing owed but part of its content."""
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
    bc = ("BLUE CROSS TEST", "BCT01")
    send(s, tmp_path, [clm837(F.claim_id(91), [("99214", "200.00", "20260302")] + ([("J3590", "0.01", "20260302")] if token else []),
                              payer=bc, member="MEMBER000091")], "20260320")
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
    sent April 25 (not due yet), that no EMA claim is tied to."""
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
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("token", [False, True])
def test_send_day_twins_of_two_patients_are_told_apart_by_the_patients_name(tmp_path, kind, ema_env, token):
    """(fix7b residual: the send-day twins) Two patients' claims of the same content (a 99214 at $200, visit March 2) went
    to Blue Cross the same day (March 20), and only the 837s know them. EMA charged each the send day: Annie's EMA claim
    61 was paid April 10, Bert's EMA claim 62 was not. Each member's earlier remittance names its patient. The twin rule
    alone cannot tell which twin is whose (both claims stayed listed, Annie's paid claim with them); the patient's name
    tells it: Annie's claim is answered by EMA claim 61, and Bert's alone is listed, with EMA claim 62. token: each claim
    also carries a J3590 billed at a token $0.01, nothing owed but part of its content."""
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import charge, denial_world, pay, remit as raw_remit
    s, lg = _ema_store(tmp_path, kind)
    rows = [charge(F.link_no(61), cpt="99214", amount=200, patient="Twintest, Annie", posted=D(2026, 3, 20)),
            charge(F.link_no(62), cpt="99214", amount=200, patient="Twintest, Bert", posted=D(2026, 3, 20)),
            pay(F.link_no(61), "PAPER-61", 110, cpt="99214", patient="Twintest, Annie", posted=D(2026, 4, 10))]
    if token:
        rows += [charge(F.link_no(n), cpt="J3590", amount=0.01, patient=f"Twintest, {who}", posted=D(2026, 3, 20))
                 for n, who in ((61, "Annie"), (62, "Bert"))]
    denial_world(s, lg, tmp_path, rows)
    for n in range(25):
        raw_remit(s, trace=f"BC{n:05d}", paid_on="2026-05-01", payer="BLUE CROSS TEST",
                  claims=[{"pcn": F.claim_id(200 + n), "status": "1", "member": f"MB{n:08d}", "last": "VOLUME", "first": f"B{n}",
                           "lines": [("2026-04-20", "99214", 200, 110, [("CO", "45", 90)])]}])
    for n, first in ((61, "ANNIE"), (62, "BERT")):
        raw_remit(s, trace=f"BCM{n}", paid_on="2026-02-20", payer="BLUE CROSS TEST",
                  claims=[{"pcn": F.claim_id(160 + n), "status": "1", "member": f"MEMBER0000{n}", "last": "TWINTEST", "first": first,
                           "lines": [("2026-02-02", "99213", 150, 80, [("CO", "45", 70)])]}])
    bc = ("BLUE CROSS TEST", "BCT01")
    lines = [("99214", "200.00", "20260302")] + ([("J3590", "0.01", "20260302")] if token else [])
    send(s, tmp_path, [clm837(F.claim_id(n), lines, payer=bc, member=f"MEMBER0000{n}") for n in (61, 62)], "20260320")
    det = Detector(s, run_id="r1", today="2026-05-03")
    det.run()
    assert det.ema_errors == [], det.ema_errors
    lg.close()
    got = {r["patient_control_number"] or f"link:{r['ema_link']}": r["ema_link"] for r in s.query(
        "SELECT patient_control_number, ema_link FROM findings WHERE signal='NO_ANSWER' AND COALESCE(stale,0)=0 "
        "AND (patient_control_number IN (?, ?) OR ema_link IN (?, ?))", (F.claim_id(61), F.claim_id(62), F.link_no(61), F.link_no(62)))}
    assert got == {F.claim_id(62): F.link_no(62)}, got
    s.close()


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
@pytest.mark.parametrize("kind", KINDS)
def test_one_person_under_two_ema_patient_ids_is_one_patient_for_the_one_visit_rule(tmp_path, kind):
    """(fix7b residual) The reading of a biopsy (88305-26, visit September 14) was billed on CB0000V71 (sent September 15)
    and again on CB0000V72 (sent September 25) under a second chart EMA opened for the same person: the two bills carry two
    EMA patient ids. Neither is answered. The remittances name the member's patient (one name), and both bills' 837s name
    that member: one person, one visit, chased once under the newer bill (before: matched by the EMA patient id alone,
    the two bills stayed two items). Control: a second pair (V73, V74) whose members' remittances name two different
    people stays two items."""
    from tests.test_workable import ema
    s = _store(tmp_path, kind)
    _salted(s)
    _remits(s, tmp_path, _visits() + [_member_visit("CB0000V70C017", "WCHART0001"),
                                      _member_visit("CB0000V75C017", "WCHART0003", first="OTHER"),
                                      _member_visit("CB0000V76C017", "WCHART0004", first="ANOTHER")], "20261110")
    v26 = [("88305", "90.00", "20260914", "26")]
    for bill, pid, sent, member in (("CB0000V71", "EMA071A", "2026-09-15", "WCHART0001"), ("CB0000V72", "EMA071B", "2026-09-25", "WCHART0001"),
                                    ("CB0000V73", "EMA073A", "2026-09-15", "WCHART0003"), ("CB0000V74", "EMA073B", "2026-09-25", "WCHART0004")):
        ema(s.conn, bill + "C017", cpt="88305", modifiers='["26"]', charges_cents=9000, patient_id=pid)
        ema(s.conn, bill + "C017", family="claim_status", claim_status="Submitted", claim_submission_date=sent, patient_id=pid)
        s.conn.commit()
        send(s, tmp_path, [clm837(bill + "C017", v26, member=member)], sent.replace("-", ""))
    found = _chased(s, "t1")
    assert {k for k in found if k.startswith("CB0000V7")} == {"CB0000V72C017", "CB0000V73C017", "CB0000V74C017"}, sorted(found)
    s.close()
