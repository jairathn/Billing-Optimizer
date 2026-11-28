"""
A claim still in EMA's print batch is never on no list (unit gapfix G1).

EMA's weekly exports show a claim in EMA's print batch, never sent: no submission date, no 837. By the exports' own rule
(run_ema_open_claims) it is due 30 days after the visit, since EMA gives no date the bill was made. EMA's A/R, newer than
the exports' status row, still calls it Batched for Printing, so the exports' pass hands it to ema_checks.not_sent, whose
clock runs 14 days from the claim's first charge posting to the report's own date. A bill made late (9 days before the
A/R) was then listed by neither, and no count showed it: two VA claims ($260.32) on the real data. Now each claim the
passes hand to not_sent and not_sent does not list is dealt with once: on the waiting view with its due day (the first
charge posting plus 14 days) while it has been in the print batch fewer than 14 days by the newest sign it is still
unsent (the report's date or the day EMA's exports reach, the later: never work made early), else listed as never sent.
A claim the 837s show sent is listed as the 837 pass lists it, with the report's status as context.

Every name, claim id, EMA claim number, amount and date here is invented (tests/ema_fixtures.py and the repository's
builders). Each test runs on the server's store file and on the compact in-memory store the serverless cycle builds.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch.detect import WAITING_837_META, Detector  # noqa: E402
from tests.test_837_sends import KINDS, clm837, send  # noqa: E402
from tests.test_fx_exports import _ema_store, ema_env  # noqa: E402,F401  (ema_env is a fixture)

D = dt.date
VISIT = D(2026, 4, 21)
AR_DAY = D(2026, 5, 21)
TODAY = "2026-05-25"


def _print_batch_world(tmp_path, kind, *, first_charge: dt.date):
    """Erin's visit of April 21 (an 88305 at $180, Blue Cross): EMA made the bill on `first_charge` and left it in its print
    batch. It never went out: the weekly exports list it Batched for Printing with no submission date and no bill date, and
    no 837 shows it. The exports reach May 24 (another patient's bill made that day). EMA's A/R of May 21 still calls the
    claim Batched for Printing, and Production shows its charge first posted on `first_charge`. Blue Cross's remittances
    on file reach May 20 (another patient's paid visit). Today May 25. Returns the claim's never-answered items, as
    (signal, claim id or 'link:<EMA claim number>'), its waiting row (or None) and the run's counts."""
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import charge, load, remit as raw_remit
    from tests.test_workable import ema
    s, lg = _ema_store(tmp_path, kind)
    who = "Batchtest, Erin"
    raw_remit(s, trace=F.check_no(80), paid_on="2026-05-20",
              claims=[{"pcn": F.claim_id(80), "last": "OTHERTEST", "first": "OLIVE", "member": "MEMBEROLIVE1",
                       "lines": [("2026-04-28", "99213", 150, 90, [("CO", "45", 60)])]}])
    rows = [charge(F.link_no(81), cpt="88305", amount=180, patient=who, svc=VISIT, posted=first_charge, status="Batched for Printing"),
            charge(F.link_no(80), cpt="99213", amount=150, patient="Othertest, Olive", svc=D(2026, 4, 28), posted=AR_DAY)]
    ar = [F.ar_row(claim=F.claim_id(81), link=F.link_no(81), status="Batched for Printing", current=180.0, cpt="88305",
                   svc=VISIT, aging=AR_DAY, patient=who)]
    load(s, lg, tmp_path, rows, ar)
    bc = dict(payer=F.PAYERS["bcbs"], payer_id="BCT01", patient_id="EMA081")
    ema(s.conn, F.claim_id(81), cpt="88305", service_date=VISIT.isoformat(), bill_created=None, charges_cents=18000, **bc)
    ema(s.conn, F.claim_id(81), family="claim_status", claim_status="Batched for Printing", cpt="88305",
        service_date=VISIT.isoformat(), **bc)
    ema(s.conn, F.claim_id(82), service_date="2026-05-22", bill_created="2026-05-24", payer=F.PAYERS["bcbs"], payer_id="BCT01",
        patient_id="EMA082")
    s.conn.commit()
    det = Detector(s, run_id="q1", today=TODAY)
    det.run()
    assert det.ema_errors == [], det.ema_errors
    lg.close()
    got = [(r[0], r[1] or f"link:{r[2]}") for r in s.conn.execute(
        "SELECT signal, patient_control_number, ema_link FROM findings WHERE signal IN ('NO_ANSWER','REJECTED_PRE_PAYER','NOT_SENT') "
        "AND COALESCE(stale,0)=0 AND (patient_control_number=? OR ema_link=?)", (F.claim_id(81), F.link_no(81)))]
    w = json.loads((s.conn.execute("SELECT value FROM meta WHERE key=?", (WAITING_837_META,)).fetchone() or ["{}"])[0])
    row = next((r for r in w.get("claims") or [] if r and r[0] == F.claim_id(81)), None)
    counts = {k: v for k, v in det.ema_counts.items() if k.startswith(("export", "not_sent", "NOT_SENT", "REJECTED"))}
    s.close()
    return got, row, counts


@pytest.mark.parametrize("kind", KINDS)
def test_a_print_batch_claim_charged_9_days_before_the_ar_waits_on_the_waiting_view_with_its_due_day(tmp_path, kind, ema_env):
    """The bill was made May 12, 9 days before the A/R's date (12 before the day EMA's exports reach): not_sent waits for 14
    and nothing is late yet. The claim is on the waiting view, named, due May 26 (the first charge plus 14 days), with the
    reason (EMA's print batch, since May 12); never on no list, and never work before its day.
    Before: on no list and in no count (the exports' pass left it to not_sent, which does not list it yet)."""
    from tests import ema_fixtures as F
    got, row, counts = _print_batch_world(tmp_path, kind, first_charge=D(2026, 5, 12))
    assert not got, f"listed before its day: {got}"
    assert row is not None, f"a claim in EMA's print batch, due by the exports' own rule, is on no list and in no count ({counts})"
    assert row[3] == "2026-05-26" and row[4] == 18000, row
    note = row[5] if len(row) > 5 else {}
    assert note.get("check") == "print_batch" and note.get("since") == "2026-05-12" and note.get("ema_link") == F.link_no(81), row


@pytest.mark.parametrize("kind", KINDS)
def test_a_print_batch_claim_still_unsent_15_days_after_its_charge_on_newer_exports_is_listed(tmp_path, kind, ema_env):
    """The bill was made May 9: 12 days before the A/R's date, so not_sent does not list it, but EMA's exports reach May 24,
    15 days after the charge, and nothing newer says it went out. It is listed (once) as never sent: in EMA's print batch.
    Before: on no list (the exports' pass left it to not_sent, which counts only to the A/R's own date)."""
    from tests import ema_fixtures as F
    got, row, counts = _print_batch_world(tmp_path, kind, first_charge=D(2026, 5, 9))
    assert got == [("REJECTED_PRE_PAYER", F.claim_id(81))], (got, counts)
    assert row is None, f"listed and on the waiting view at once: {row}"


@pytest.mark.parametrize("kind", KINDS)
def test_control_the_same_claim_billed_earlier_is_listed_once_by_not_sent(tmp_path, kind, ema_env):
    """Control (passes before and after): the bill was made May 5, 16 days before the A/R's date, so not_sent lists it;
    one item for the claim, nothing on the waiting view."""
    got, row, counts = _print_batch_world(tmp_path, kind, first_charge=D(2026, 5, 5))
    assert len(got) == 1, f"expected one item for the claim, got {got} (counts {counts})"
    assert got[0][0] in ("NOT_SENT", "REJECTED_PRE_PAYER"), got
    assert row is None, row


@pytest.mark.parametrize("kind", KINDS)
def test_a_claim_the_837s_show_sent_that_ema_calls_never_sent_is_still_listed(tmp_path, kind, monkeypatch):
    """Claim 77: a visit March 5 (11102 at $86.66) sent March 9 to Blue Cross (the 837s show it); no answer, and due by that
    send. EMA's A/R of May 1, newer than the send, calls the claim Batched for Printing, and Production shows its charge
    first posted April 25 (6 days before the A/R): not_sent does not list it. The 837 pass lists it as it lists any claim
    due and unanswered, saying what EMA's report calls it. Before: left to not_sent, on no list and in no count."""
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import SVC, charge
    from tests.test_gap_one_claim import _world
    rows = [charge(F.link_no(77), cpt="11102", amount=86.66, patient=1, svc=D(2026, 3, 5), posted=D(2026, 4, 25),
                   status="Batched for Printing")]
    ar = [F.ar_row(claim=F.claim_id(77), link=F.link_no(77), status="Batched for Printing", current=86.66, cpt="11102",
                   svc=D(2026, 3, 5), aging=D(2026, 5, 1), patient=1),
          F.ar_row(claim=F.claim_id(1), link=F.link_no(1), status="Denied", denial=150, current=0, aging=D(2026, 4, 25), svc=SVC)]
    s, lg = _world(tmp_path, kind, monkeypatch, rows, ar)
    send(s, tmp_path, [clm837(F.claim_id(77), [("11102", "86.66", "20260305")], payer=("BLUE CROSS TEST", "BCT01"),
                              member="MEMBER000077")], "20260309")
    det = Detector(s, run_id="n1", today="2026-05-03")
    det.run()
    assert det.ema_errors == [], det.ema_errors
    lg.close()
    got = [dict(r) for r in s.query("SELECT signal, patient_control_number, hint FROM findings WHERE COALESCE(stale,0)=0 "
                                    "AND signal IN ('NO_ANSWER','REJECTED_PRE_PAYER','NOT_SENT') AND (patient_control_number=? OR ema_link=?)",
                                    (F.claim_id(77), F.link_no(77)))]
    assert len(got) == 1 and got[0]["signal"] == "NO_ANSWER", got
    assert "calls the claim Batched for Printing" in (got[0]["hint"] or ""), got[0]["hint"]
    s.close()


# ----------------------------------------------------------------------- G7: the waiting view names every claim it counts
@pytest.mark.parametrize("kind", KINDS)
def test_the_waiting_view_counts_the_claims_it_does_not_name_with_their_dollars(tmp_path, kind, monkeypatch):
    """Three claims to Anthem went out November 1 and are not due yet: the waiting view counts all three. With room to name
    two (the list's cap, here 2; 2,000 on a real store), the third is counted with its dollars and due day, and the Data
    tab's line says so. Before: 289 of 2,289 waiting claims on the real data were counted and named nowhere, their dollars
    in no line of their own."""
    from remitwatch import detect as Dm, insights
    from tests.test_837_sends import V, _remits, _store, _visits
    monkeypatch.setattr(Dm, "WAITING_LIST_MAX", 2)
    s = _store(tmp_path, kind)
    _remits(s, tmp_path, _visits(), "20261110")
    send(s, tmp_path, [clm837(f"CB0000W{i:02d}C017", V) for i in range(3)], "20261101")
    Detector(s, run_id="w", today="2026-11-12").run()
    w = json.loads(s.conn.execute("SELECT value FROM meta WHERE key=?", (WAITING_837_META,)).fetchone()[0])
    assert w["n"] == 3 and len(w["claims"]) == 2, w
    assert w["unnamed"]["n"] == 1 and w["unnamed"]["billed_cents"] == 22365 and w["unnamed"]["first_due"], w["unnamed"]
    h = next(x for x in insights.data_health(s.conn, "2026-11-10", "2026-11-12") if x.get("key") == "waiting_837")
    assert "The list names the first 2 by due day; 1 more ($223.65" in h["detail"], h["detail"]
    s.close()
