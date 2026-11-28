"""
One claim, one item (the never-answered gap, unit gapfix G8): what a person did on a claim never comes back as new work.

A claim no payer answered is listed by one of three passes: the weekly exports' (keyed by the claim id), the 837s' (keyed
by the claim id, under its own id signal) and the report checks' (EMA's A/R and Production: keyed by the claim id when the
A/R pairs it, else by EMA's claim number, 'link:<n>'). A month-by-month replay of the real data (private) found the same
claim under two identities: 28 items a simulated biller had worked came back as new Work-now work for the same claim, and a
claim the exports' or the 837 pass held on the waiting view was listed again by the report checks under its EMA claim
number, with Production's own clock. These tests hold the rule both ways:
- within one run, the report checks never list an EMA claim another pass holds, on its list or on its waiting view, and
  the waiting view says EMA's report would have listed it;
- across runs, when a claim's identity changes anyway (its EMA claim number to its claim id, or back), the new item takes
  the old one whole: the person's status, notes, history and faxes, as an heir does (HOTFIX 2d D3). Never a new matter.

Every name, claim id, EMA claim number, amount and date here is invented (tests/ema_fixtures.py, the 837 builder of
tests/test_837_sends.py). Each test runs on the server's store file and on the compact in-memory store the serverless
cycle builds.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch.detect import OPEN_837_ID, WAITING_837_META, Detector, _fid  # noqa: E402
from tests.test_837_sends import KINDS, _store, clm837, send  # noqa: E402

BC = ("BLUE CROSS TEST", "BCT01")
D = dt.date
TODAY = "2026-05-03"
N = 74                      # the invented claim of these tests: claim id F.claim_id(74), EMA claim F.link_no(74)


def _world(tmp_path, kind, monkeypatch, rows=(), ar=None):
    """EMA's reports as the server reads them (tests.test_ema_wiring.denial_world: Blue Cross's 835 of March 28, so the
    remittances on file reach that day; Production as of May 1) plus these Production and A/R rows, and a Blue Cross
    payment posted in EMA on April 20 on another visit (its answers in EMA reach past the claims' due days)."""
    from remitwatch import ema_checks as ec, ema_ledger as el, ema_reports as er
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import charge, denial_world, pay
    monkeypatch.setattr(er, "CLAIM_HOST_SUFFIX", F.HOST_SUFFIX)
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    monkeypatch.setenv("REMITWATCH_EMA_STRICT", "1")
    for k in ("VERCEL", "REMITWATCH_EMA_REPORTS", "REMITWATCH_EMA_CLAIM_URL", "REMITWATCH_EMA_PAYER_ALIASES", "REMITWATCH_EMA_LEDGER",
              "REMITWATCH_EMA_REPORT_KEEP", "REMITWATCH_EMA_CLOSES"):
        monkeypatch.delenv(k, raising=False)
    ec._LAST_POST.clear()
    s = _store(tmp_path, kind)
    er.ensure_schema(s.conn)
    s.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('ema:key_salt', ?)", ("ab" * 32,))
    s.conn.commit()
    lg = el.Ledger.open(str(tmp_path / "ema_ledger.sqlite"))
    rows = list(rows) + [charge(F.link_no(58), cpt="99212", amount=120, patient=0, svc=D(2026, 3, 2), posted=D(2026, 3, 3)),
                         pay(F.link_no(58), "PAPER-58", 60, cpt="99212", patient=0, svc=D(2026, 3, 2), posted=D(2026, 4, 20))]
    denial_world(s, lg, tmp_path, rows, ar_rows=ar or [])
    return s, lg


def _visit():
    """EMA's claim 74: Sample's 11102 of March 5 at $86.66, charged that day, billed to Blue Cross, no answer posted."""
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import charge
    return [charge(F.link_no(N), cpt="11102", amount=86.66, patient=1, svc=D(2026, 3, 5), posted=D(2026, 3, 5),
                   status="Payment Pending")]


def _send(s, tmp_path, day: str) -> None:
    """The 837s show claim 74 (the same visit) sent to Blue Cross on `day` (CCYYMMDD)."""
    from tests import ema_fixtures as F
    send(s, tmp_path, [clm837(F.claim_id(N), [("11102", "86.66", "20260305")], payer=BC, member="MEMBER000074")], day)


def _run(s, run: str, today: str = TODAY) -> Detector:
    det = Detector(s, run_id=run, today=today)
    det.run()
    assert det.ema_errors == [], det.ema_errors
    return det


def _claim_items(s) -> list[dict]:
    """Every item of claim 74, under its claim id or its EMA claim number, listed or kept."""
    from tests import ema_fixtures as F
    s.conn.row_factory = None
    cols = [r[1] for r in s.conn.execute("PRAGMA table_info(findings)")]
    rows = s.conn.execute("""SELECT * FROM findings WHERE signal IN ('NO_ANSWER','REJECTED_PRE_PAYER','NOT_SENT')
                             AND (patient_control_number = ? OR ema_link = ?) ORDER BY finding_id""",
                          (F.claim_id(N), F.link_no(N))).fetchall()
    return [dict(zip(cols, r)) for r in rows]


def _listed(s) -> list[dict]:
    return [r for r in _claim_items(s) if not r.get("stale")]


def _work(s, fid: str, status: str = "appealed", note: str = "Faxed Blue Cross a status request") -> None:
    """A person records Sent (appealed) with a note on the item, as the panel's status action records it."""
    at = "2026-04-25T15:00:00+00:00"
    s.conn.execute("UPDATE findings SET status=?, notes=?, appealed_at=?, status_changed_at=? WHERE finding_id=?",
                   (status, note, at if status == "appealed" else None, at, fid))
    s.conn.execute("""INSERT INTO finding_events (finding_id, at, kind, from_status, to_status, actor, note)
                      VALUES (?, ?, 'manual', 'open', ?, 'biller', ?)""", (fid, at, status, note))
    s.conn.commit()


def _waiting_row(s, claim_id: str):
    w = json.loads((s.conn.execute("SELECT value FROM meta WHERE key=?", (WAITING_837_META,)).fetchone() or ["{}"])[0])
    return next((r for r in w.get("claims") or [] if r and r[0] == claim_id), None), w


# ------------------------------------------------------------------- across runs: the identity changes, the work stays
@pytest.mark.parametrize("kind", KINDS)
def test_a_claim_worked_under_its_ema_claim_number_is_not_new_work_once_the_837s_name_its_claim_id(tmp_path, kind, monkeypatch):
    """Month 1: no 837 and no weekly export knows claim 74, and EMA's A/R does not pair it with a claim id; Production shows
    EMA's claim 74 charged March 5 with no answer, so the report checks list it under its EMA claim number. A biller faxes
    Blue Cross a status request and records Sent, with a note. Month 2: the 837 files arrive and show claim 74 sent March 9
    to Blue Cross; the 837 pass lists it under its claim id and finds EMA's claim 74 by its content. It is the same claim:
    its item keeps the biller's Sent and note and the day it was first listed, and no other item of it is listed.
    Before: the 837 pass's item took the old one's work as a new matter (Started, first listed today): back as new work
    (the replay's commonest class, 20 of its 28 pairs)."""
    from tests import ema_fixtures as F
    s, lg = _world(tmp_path, kind, monkeypatch, _visit())
    _run(s, "m1")
    first = _listed(s)
    assert [(r["signal"], r["patient_control_number"], r["ema_link"]) for r in first] == [("NO_ANSWER", None, F.link_no(N))], first
    old = first[0]
    _work(s, old["finding_id"])
    _send(s, tmp_path, "20260309")
    _run(s, "m2")
    now = _listed(s)
    assert len(now) == 1, [(r["signal"], r["patient_control_number"], r["finding_id"]) for r in now]
    it = now[0]
    assert it["finding_id"] == _fid(OPEN_837_ID, F.claim_id(N)) and it["ema_link"] == F.link_no(N), it
    assert (it["status"], it["notes"], it["first_seen"]) == ("appealed", old["notes"] or "Faxed Blue Cross a status request",
                                                              old["first_seen"]), (
        f"the claim the biller worked came back as {it['status']} work first listed {it['first_seen']}: the same claim, a new matter")
    assert it["work_state"] != "work_now" or it["state_rule"] != "open", it["state_rule"]
    # the old id's history went with it: the biller's Sent is on the heir's record
    kinds = [r[0] for r in s.conn.execute("SELECT kind FROM finding_events WHERE finding_id=? ORDER BY id", (it["finding_id"],))]
    assert "manual" in kinds, kinds
    assert not [r for r in _claim_items(s) if r["finding_id"] == old["finding_id"]], "the old id stays beside its heir"
    lg.close()
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_a_claim_worked_under_its_ema_claim_number_is_not_new_work_once_the_ar_pairs_its_claim_id(tmp_path, kind, monkeypatch):
    """Month 1: Production lists EMA's claim 74 with no answer, and nothing pairs it with a claim id: the report checks list
    it under its EMA claim number, and a biller records Sent with a note. Month 2: EMA's A/R lists the claim, pairing claim id
    74 with EMA claim 74; the report checks now key it by its claim id. The same claim: its item keeps the biller's Sent and
    note. Before: a new matter (Started, first listed today), the replay's second class (4 of its 28 pairs)."""
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import SVC, load
    s, lg = _world(tmp_path, kind, monkeypatch, _visit())
    _run(s, "m1")
    first = _listed(s)
    assert [(r["patient_control_number"], r["ema_link"]) for r in first] == [(None, F.link_no(N))], first
    old = first[0]
    _work(s, old["finding_id"])
    # the newer A/R (May 1) lists claim 1 as the older one did, and now claim 74 under its claim id
    load(s, lg, tmp_path, [], [F.ar_row(claim=F.claim_id(1), link=F.link_no(1), status="Denied", denial=150, current=0,
                                        aging=D(2026, 4, 25), svc=SVC),
                               F.ar_row(claim=F.claim_id(N), link=F.link_no(N), cpt="11102", current=86.66, svc=D(2026, 3, 5),
                                        aging=D(2026, 5, 1), patient=1, status="Payment Pending")])
    _run(s, "m2")
    now = _listed(s)
    assert len(now) == 1, [(r["signal"], r["patient_control_number"], r["finding_id"]) for r in now]
    it = now[0]
    assert it["patient_control_number"] == F.claim_id(N), it
    assert (it["status"], it["first_seen"]) == ("appealed", old["first_seen"]), (
        f"the claim the biller worked came back as {it['status']} work first listed {it['first_seen']}")
    lg.close()
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_a_claim_worked_under_its_claim_id_is_not_new_work_under_its_ema_claim_number(tmp_path, kind, monkeypatch):
    """The other way. Month 1: the 837s show claim 74 sent March 9 to Blue Cross and no answer: the 837 pass lists it under
    its claim id (EMA's claim 74 found by its content), and a biller records Sent with a note. Month 2: the 837 files are
    gone from the store (the archive retired them), so only EMA's reports still show the claim, under its EMA claim number:
    the report checks list it there. The same claim: its item keeps the biller's Sent, note and first day.
    Before: Production's item for EMA claim 74 was new work beside the worked one (the replay's other direction)."""
    from tests import ema_fixtures as F
    s, lg = _world(tmp_path, kind, monkeypatch, _visit())
    _send(s, tmp_path, "20260309")
    _run(s, "m1")
    first = _listed(s)
    assert [(r["finding_id"], r["ema_link"]) for r in first] == [(_fid(OPEN_837_ID, F.claim_id(N)), F.link_no(N))], first
    old = first[0]
    _work(s, old["finding_id"])
    s.conn.execute("DELETE FROM lines_837")
    s.conn.execute("DELETE FROM claims_837")
    s.conn.commit()
    _run(s, "m2")
    now = _listed(s)
    assert len(now) == 1, [(r["signal"], r["patient_control_number"], r["finding_id"]) for r in now]
    it = now[0]
    assert it["ema_link"] == F.link_no(N), it
    assert (it["status"], it["notes"], it["first_seen"]) == ("appealed", "Faxed Blue Cross a status request", old["first_seen"]), (
        f"the claim the biller worked came back as {it['status']} work first listed {it['first_seen']}")
    lg.close()
    s.close()


# ----------------------------------------------------------------- within a run: a claim on the waiting view is held there
@pytest.mark.parametrize("kind", KINDS)
def test_a_claim_the_837_pass_holds_on_the_waiting_view_is_not_listed_again_under_its_ema_claim_number(tmp_path, kind, monkeypatch):
    """Month 1: the 837s show claim 74 sent March 9 and no answer: listed under its claim id, and a biller records Sent.
    Month 2: the 837s show it sent again May 1, so its answer is due May 31: it waits, on the waiting view with its due day.
    Production still shows EMA's claim 74 charged March 5 with no answer, which by Production's own clock is overdue: the
    report checks must leave it to the 837 pass (one claim, one item), and the waiting view says EMA's report would list it.
    Month 3 (June 15, Production of June 10 with Blue Cross's postings through June 5): the resend is due and still
    unanswered, so the claim comes back under its own id with the biller's work.
    Before: in month 2 Production's item for EMA claim 74 was listed as new work beside the waiting claim."""
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import charge, load, pay
    s, lg = _world(tmp_path, kind, monkeypatch, _visit())
    _send(s, tmp_path, "20260309")
    _run(s, "m1")
    first = _listed(s)
    assert [r["finding_id"] for r in first] == [_fid(OPEN_837_ID, F.claim_id(N))], first
    _work(s, first[0]["finding_id"])
    _send(s, tmp_path, "20260501")
    _run(s, "m2")
    # the claim's own item may stay on record with the biller's Sent (the self-check keeps a worked item that left the
    # list): what must not happen is a second item for it, new work under EMA's claim number
    others = [r for r in _claim_items(s) if r["finding_id"] != first[0]["finding_id"]]
    assert not others, [(r["signal"], r["patient_control_number"], r["ema_link"], r["status"], r["stale"]) for r in others]
    kept = [r for r in _claim_items(s) if r["finding_id"] == first[0]["finding_id"]]
    assert kept and kept[0]["status"] == "appealed", "the biller's work is kept with the claim while it waits"
    row, w = _waiting_row(s, F.claim_id(N))
    assert row is not None and row[3] == "2026-05-31", (row, w.get("n"))
    note = row[5] if len(row) > 5 else None
    assert isinstance(note, dict) and note.get("ema_link") == F.link_no(N) and note.get("check") == "no_answer", (
        f"the waiting view does not say EMA's report would list the claim: {row}")
    # month 3: the resend is due and unanswered
    load(s, lg, tmp_path, [charge(F.link_no(59), cpt="99212", amount=120, patient=2, svc=D(2026, 5, 20), posted=D(2026, 5, 21)),
                           pay(F.link_no(59), "PAPER-59", 60, cpt="99212", patient=2, svc=D(2026, 5, 20), posted=D(2026, 6, 5)),
                           charge(F.link_no(98), cpt="99212", patient=3, svc=D(2026, 6, 1), posted=D(2026, 6, 10),
                                  status="Payment Pending")])
    _run(s, "m3", today="2026-06-15")
    now = _listed(s)
    assert [(r["finding_id"], r["status"]) for r in now] == [(first[0]["finding_id"], "appealed")], (
        [(r["signal"], r["patient_control_number"], r["ema_link"], r["status"]) for r in now])
    lg.close()
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_a_claim_the_exports_hold_on_the_waiting_view_is_not_listed_again_by_the_report_checks(tmp_path, kind, monkeypatch):
    """EMA's weekly exports list claim 74 to Blue Cross, Rejected (submitted March 9); the 837s show it sent again May 1, after
    that status, so it waits for Blue Cross's answer to the resend (due May 31) on the waiting view. EMA's Production report
    of May 1 still calls EMA's claim 74 Rejected: the report checks must leave the claim to the exports' pass (one claim, one
    item), and its waiting entry names the report's status. Before: the report checks listed EMA's claim 74 as Rejected,
    new work with its own clock, beside the claim waiting under its claim id (15 such claims on the real data, $3,372.62)."""
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import charge
    from tests.test_workable import ema
    rows = [charge(F.link_no(N), cpt="11102", amount=86.66, patient=1, svc=D(2026, 3, 5), posted=D(2026, 3, 5), status="Rejected")]
    s, lg = _world(tmp_path, kind, monkeypatch, rows)
    bc = dict(payer=F.PAYERS["bcbs"], payer_id="BCT01", patient_id="EMA074")
    ema(s.conn, F.claim_id(N), cpt="11102", service_date="2026-03-05", bill_created="2026-03-05", charges_cents=8666, **bc)
    ema(s.conn, F.claim_id(N), family="claim_status", claim_status="Rejected", cpt="11102", service_date="2026-03-05",
        claim_submission_date="2026-03-09", **bc)
    s.conn.commit()
    _send(s, tmp_path, "20260501")
    _run(s, "m1")
    assert not _listed(s), [(r["signal"], r["patient_control_number"], r["ema_link"], r["title"]) for r in _listed(s)]
    row, w = _waiting_row(s, F.claim_id(N))
    assert row is not None and row[3] == "2026-05-31", (row, w.get("n"))
    note = row[5] if len(row) > 5 else None
    assert isinstance(note, dict) and note.get("ema_link") == F.link_no(N) and note.get("status") == "Rejected", row
    lg.close()
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_an_item_listed_before_its_claim_began_waiting_stays_listed_until_the_claims_own_item_takes_it_over(tmp_path, kind,
                                                                                                         monkeypatch):
    """Month 1: no 837 knows claim 74, and Production shows EMA's claim 74 charged March 5 with no answer: the report checks
    list it under its EMA claim number (nobody has worked it yet). Month 2: the 837s show claim 74 sent May 1, so the 837
    pass holds it on the waiting view until May 31. The item already listed stays listed as it was: holding a claim on the
    waiting view keeps new work from being made early, and never takes work off the list in silence. Month 3 (June 15):
    the send is due and unanswered; the 837 pass lists the claim under its claim id, and that item takes the old one over
    (the day it was first listed, and the fold the self-check reads), so the claim is one item.
    Before: in month 2 the listed item vanished (on the real data the self-check put back 12 such items, every run)."""
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import charge, load, pay
    s, lg = _world(tmp_path, kind, monkeypatch, _visit())
    _run(s, "m1")
    first = _listed(s)
    assert [(r["signal"], r["patient_control_number"], r["ema_link"]) for r in first] == [("NO_ANSWER", None, F.link_no(N))], first
    old = first[0]
    _send(s, tmp_path, "20260501")
    det = _run(s, "m2")
    now = _listed(s)
    assert [(r["finding_id"], r["status"]) for r in now] == [(old["finding_id"], "open")], (
        "the item listed before its claim began waiting left the list: "
        + repr([(r["signal"], r["patient_control_number"], r["ema_link"], r["stale"]) for r in _claim_items(s)]))
    assert det.ema_counts.get("unanswered_claims_kept_while_waiting") == 1, dict(det.ema_counts)
    kept = s.conn.execute("SELECT COUNT(*) FROM finding_events WHERE finding_id=? AND kind='kept_by_selfcheck'",
                          (old["finding_id"],)).fetchone()[0]
    assert kept == 0, "the analysis dropped the listed item, and only the self-check put it back"
    row, _w = _waiting_row(s, F.claim_id(N))
    assert row is not None and row[3] == "2026-05-31" and len(row) == 5, row
    # month 3: the send is due and unanswered
    load(s, lg, tmp_path, [charge(F.link_no(59), cpt="99212", amount=120, patient=2, svc=D(2026, 5, 20), posted=D(2026, 5, 21)),
                           pay(F.link_no(59), "PAPER-59", 60, cpt="99212", patient=2, svc=D(2026, 5, 20), posted=D(2026, 6, 5)),
                           charge(F.link_no(98), cpt="99212", patient=3, svc=D(2026, 6, 1), posted=D(2026, 6, 10),
                                  status="Payment Pending")])
    _run(s, "m3", today="2026-06-15")
    now = _listed(s)
    assert [r["finding_id"] for r in now] == [_fid(OPEN_837_ID, F.claim_id(N))], (
        [(r["signal"], r["patient_control_number"], r["ema_link"], r["stale"]) for r in now])
    it = now[0]
    assert it["first_seen"] == old["first_seen"], (it["first_seen"], old["first_seen"])
    folded = json.loads(it["evidence"] or "{}").get("folded") or []
    assert old["finding_id"] in {x.get("finding_id") if isinstance(x, dict) else x for x in folded}, folded
    lg.close()
    s.close()


# ------------------------------------------------------------- the orders the first tests left out, and a bill sent again
def _exports(s, sub: int = 1, submitted: str = "2026-03-09") -> None:
    """EMA's weekly exports list claim 74 (submission `sub`) to Blue Cross: its 11102 of March 5 at $86.66, Submitted."""
    from tests import ema_fixtures as F
    from tests.test_workable import ema
    bc = dict(payer=F.PAYERS["bcbs"], payer_id="BCT01", patient_id="EMA074")
    cid = F.claim_id(N, sub=sub)
    ema(s.conn, cid, cpt="11102", service_date="2026-03-05", bill_created="2026-03-05", charges_cents=8666, **bc)
    ema(s.conn, cid, family="claim_status", claim_status="Submitted", cpt="11102", service_date="2026-03-05",
        claim_submission_date=submitted, **bc)
    s.conn.commit()


def _all_subs(s) -> list[dict]:
    """Every item of the visit, under any submission of claim 74 or under EMA claim 74."""
    from tests import ema_fixtures as F
    s.conn.row_factory = None
    cols = [r[1] for r in s.conn.execute("PRAGMA table_info(findings)")]
    rows = s.conn.execute("""SELECT * FROM findings WHERE signal IN ('NO_ANSWER','REJECTED_PRE_PAYER','NOT_SENT')
                             AND (patient_control_number LIKE ? OR ema_link = ?) ORDER BY finding_id""",
                          (F.claim_id(N)[:-3] + "%", F.link_no(N))).fetchall()
    return [dict(zip(cols, r)) for r in rows]


@pytest.mark.parametrize("kind", KINDS)
def test_a_claim_worked_under_its_ema_claim_number_is_not_new_work_once_the_weekly_exports_list_it(tmp_path, kind, monkeypatch):
    """Month 1: only Production shows EMA's claim 74 (no answer): listed under its EMA claim number, and a biller records
    Sent with a note. Month 2: EMA's weekly exports list claim 74 (Submitted March 9), and the exports' pass lists it under
    its claim id. One claim: one item, with the biller's Sent and the day it was first listed (review gapfix-r2, an order
    the first tests did not cover)."""
    s, lg = _world(tmp_path, kind, monkeypatch, _visit())
    _run(s, "m1")
    first = _listed(s)
    assert len(first) == 1 and first[0]["patient_control_number"] is None, first
    old = first[0]
    _work(s, old["finding_id"])
    _exports(s)
    _run(s, "m2")
    now = [r for r in _all_subs(s) if not r.get("stale")]
    assert len(now) == 1, [(r["signal"], r["patient_control_number"], r["ema_link"], r["status"]) for r in now]
    assert (now[0]["status"], now[0]["first_seen"]) == ("appealed", old["first_seen"]), (now[0]["status"], now[0]["first_seen"])
    lg.close()
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_a_claim_worked_under_its_claim_id_by_the_exports_is_not_new_work_once_only_production_shows_it(tmp_path, kind,
                                                                                                        monkeypatch):
    """Month 1: EMA's weekly exports list claim 74 (Submitted March 9), no answer: listed under its claim id, and a biller
    records Sent with a note. Month 2: the weekly exports no longer carry the claim (their window moved on); Production
    still shows EMA's claim 74 with no answer, under its EMA claim number. One claim: one item, with the biller's Sent
    (review gapfix-r2, the other order)."""
    s, lg = _world(tmp_path, kind, monkeypatch, _visit())
    _exports(s)
    _run(s, "m1")
    first = [r for r in _all_subs(s) if not r.get("stale")]
    assert len(first) == 1, [(r["signal"], r["patient_control_number"], r["ema_link"]) for r in first]
    old = first[0]
    _work(s, old["finding_id"])
    s.conn.execute("DELETE FROM ema_rows")
    s.conn.commit()
    _run(s, "m2")
    now = [r for r in _all_subs(s) if not r.get("stale")]
    assert len(now) == 1, [(r["signal"], r["patient_control_number"], r["ema_link"], r["status"]) for r in now]
    assert (now[0]["status"], now[0]["first_seen"]) == ("appealed", old["first_seen"]), (now[0]["status"], now[0]["first_seen"])
    lg.close()
    s.close()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("decision", ["dismissed", "resolved"])
def test_a_bill_sent_again_after_a_decision_on_its_ema_claim_is_a_new_matter(tmp_path, kind, monkeypatch, decision):
    """Month 1: only Production shows EMA's claim 74 (no answer): listed under its EMA claim number, and a biller decides it
    on April 25: Let go (dismissed) or Paid (resolved). Month 2: the practice sends the bill again under a new claim id
    (submission 2, May 1, to Blue Cross: the 837s show it), and by June 15 no payer answered the resend. The resend is a
    new matter, as the house rule for a bill sent again says ('it starts To do ... never Sent, Paid or Let go from the
    earlier item'): its item is work, To do, with the notes handed on (review gapfix-r2-1).
    Before: the same-EMA-claim heir handed the month-1 decision to the resend whole: born Let go (or Paid), on no work
    list, though no payer ever answered it."""
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import charge, load, pay
    s, lg = _world(tmp_path, kind, monkeypatch, _visit())
    _run(s, "m1")
    first = _listed(s)
    assert len(first) == 1 and first[0]["patient_control_number"] is None, first
    old = first[0]
    _work(s, old["finding_id"], status=decision, note="Decided on the first claim")
    sub2 = F.claim_id(N, sub=2)
    send(s, tmp_path, [clm837(sub2, [("11102", "86.66", "20260305")], payer=BC, member="MEMBER000074")], "20260501")
    load(s, lg, tmp_path, [charge(F.link_no(59), cpt="99212", amount=120, patient=2, svc=D(2026, 5, 20), posted=D(2026, 5, 21)),
                           pay(F.link_no(59), "PAPER-59", 60, cpt="99212", patient=2, svc=D(2026, 5, 20), posted=D(2026, 6, 5)),
                           charge(F.link_no(98), cpt="99212", patient=3, svc=D(2026, 6, 1), posted=D(2026, 6, 10),
                                  status="Payment Pending")])
    _run(s, "m2", today="2026-06-15")
    resend = [r for r in _all_subs(s) if r["patient_control_number"] == sub2]
    assert resend, [(r["signal"], r["patient_control_number"], r["ema_link"], r["status"], r["stale"]) for r in _all_subs(s)]
    it = resend[0]
    assert it["finding_id"] == _fid(OPEN_837_ID, sub2), it["finding_id"]
    assert it["status"] in ("open", "in_progress") and not it["stale"], (
        f"the resend (submission 2, never answered) was born '{it['status']}', the decision a person made on the first "
        f"claim's EMA claim number (folded: {'same_ema_claim' in (it['evidence'] or '')})")
    assert "Decided on the first claim" in (it["notes"] or ""), it["notes"]
    lg.close()
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_a_claim_sent_before_a_decision_on_its_ema_claim_keeps_that_decision(tmp_path, kind, monkeypatch):
    """The other side of the rule. Month 1: only Production shows EMA's claim 74, listed under its EMA claim number, and a
    biller lets it go on April 25. Month 2: the 837 files arrive and show claim 74 itself sent March 9, before that
    decision: the claim the biller decided, under its claim id at last. It keeps the decision (one claim, one item)."""
    from tests import ema_fixtures as F
    s, lg = _world(tmp_path, kind, monkeypatch, _visit())
    _run(s, "m1")
    first = _listed(s)
    assert len(first) == 1 and first[0]["patient_control_number"] is None, first
    old = first[0]
    _work(s, old["finding_id"], status="dismissed", note="Let go: the payer has no record")
    _send(s, tmp_path, "20260309")
    _run(s, "m2")
    mine = [r for r in _claim_items(s) if r["patient_control_number"] == F.claim_id(N)]
    assert len(mine) == 1 and mine[0]["status"] == "dismissed", [(r["finding_id"], r["status"], r["stale"]) for r in _claim_items(s)]
    lg.close()
    s.close()
