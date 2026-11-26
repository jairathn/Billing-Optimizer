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
@pytest.mark.parametrize("kind", KINDS)
def test_a_visit_the_exports_chase_yields_to_a_newer_bill_only_the_837s_know(tmp_path, kind):
    """(answers-1, the exports' side; the reviewer's and verifier's test) EMA's weekly exports list bill CB0000M31 (C017,
    sent September 15, its 837 on file for member WDUP000002). The practice made the bill again as CB0000M32 and sent it
    September 25; only the 837s know it yet. Neither is answered (remittances to November 10). One visit (the same
    patient, day and code) is one unpaid service: only the newer bill carries it."""
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
    assert set(found) == {"CB0000M32C017"}, (
        f"listed {sorted(found)}: the same patient's one visit (member, day, code) sent under two bills is one unpaid service")
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
def test_a_claim_charged_days_before_its_837_went_out_is_listed_once(tmp_path, kind, ema_env):
    """(answers-7; the reviewer's test) Only the 837s know claim F.claim_id(91): a 99214 at $200, visit March 2, sent to
    Blue Cross on March 20; the practice posted the charge in EMA on March 3 and held the claim before sending it. No 835
    answers it; the remittances reach May 1 (due April 19). EMA's Production report holds it as EMA claim 91 (patient 1),
    nothing posted on it, and the A/R does not pair it with a claim id. Another patient's EMA claim 92 has the same day,
    code and charge and was paid April 10. EMA claim 91 is the only claim of those lines that no payer answered, charged
    before the send: it is this claim's, and the claim is listed once (the 837 pass's item, which carries EMA claim 91)."""
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import charge, denial_world, pay, remit as raw_remit
    s, lg = _ema_store(tmp_path, kind)
    rows = [charge(F.link_no(91), cpt="99214", amount=200, patient=1, posted=D(2026, 3, 3)),
            charge(F.link_no(92), cpt="99214", amount=200, patient=2, posted=D(2026, 3, 3)),
            pay(F.link_no(92), "PAPER-92", 110, cpt="99214", patient=2, posted=D(2026, 4, 10))]
    denial_world(s, lg, tmp_path, rows)
    for n in range(25):
        raw_remit(s, trace=f"BC{n:05d}", paid_on="2026-05-01", payer="BLUE CROSS TEST",
                  claims=[{"pcn": F.claim_id(200 + n), "status": "1", "member": f"MB{n:08d}", "last": "VOLUME", "first": f"B{n}",
                           "lines": [("2026-04-20", "99214", 200, 110, [("CO", "45", 90)])]}])
    bc = ("BLUE CROSS TEST", "BCT01")
    send(s, tmp_path, [clm837(F.claim_id(91), [("99214", "200.00", "20260302")], payer=bc, member="MEMBER000091")], "20260320")
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
