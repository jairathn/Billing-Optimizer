"""
A second payer a person billed through RemitWatch, and that then paid, is never left as hidden work (a month-by-month
replay of real data, 2026-11-28, class "second payer billed by a person and paid: 'mark it Paid' prompt never shown":
184 items, $5,959.00, every one at the last cutoff Sent, stale, work_state work_now, rule person_billed_paid).

The page's Mark billed sets a SECONDARY_NOT_BILLED item to Sent ("Billed: <second payer>") beside its own 'billed' line.
states._billed_second words what comes next: when the second payer pays, "the person's status stands, the item says to
mark it Paid" (rule person_billed_paid, Work now; tests/test_story.py's practice-manager H4 checks the words on a row put
in by hand). But detect.run_secondary_not_billed lists a line only while the second payer has NOT answered: the run in
which EMA (or an 835) shows its payment no longer produces the item, Detector.save keeps the worked row as stale (on no
list: every list asks for live=1), and states.state_of decides a person's Sent before it looks at `stale`, so the row is
stored as Work now, "mark it Paid", where nobody sees it. The person's Sent stays Sent for good, the recovery never shows
as Paid (Today's "What came back" and the weekly report count status Paid only), and the Results tab's open book
(results._closure reads the stored work_state) counts the item as still open, Work now, while no Work now list holds it.

Each case runs one daily cycle with the item listed, records Mark billed as the page does, lets the second payer pay
(EMA's posting; with '835_too' its remittance as second payer as well), and runs the next daily cycle. What must hold:
  1. the item is either still on the list people work from with its "mark it Paid" step, or closed as Paid by the run
     with its payment named; never a stale row whose stored state is Work now or Waiting;
  2. every item the Results open book counts as still open is on a list people see (counts equal lists).
The control ('unworked'): nobody billed it; the run drops it when the second payer pays, as it always has.

Every name, claim number, check number, amount and date here is invented (tests/ema_fixtures.py). Each case runs on the
server's store file and on the compact in-memory store the serverless cycle builds.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch import actions, ema_checks as ec, ema_ledger as el, ema_reports as er, results, states  # noqa: E402
from remitwatch import selfcheck as sc  # noqa: E402
from remitwatch.detect import Detector  # noqa: E402
from remitwatch.store import Store  # noqa: E402
from tests import ema_fixtures as F  # noqa: E402
from tests.test_ema_wiring import adj, charge, denial_world, load, pay, remit  # noqa: E402

D = dt.date
P = F.PAYERS
SVC = D(2026, 3, 2)
S = SVC.isoformat()
SALT_HEX = "ab" * 32
KINDS = ["file", "compact"]
DAY1 = "2026-05-03"                 # the daily run that lists the item (EMA's Production reaches May 1)
BILLED_AT = "2026-05-04T14:00:00+00:00"
DAY2 = "2026-05-14"                 # the next daily run after the second payer's payment


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(er, "CLAIM_HOST_SUFFIX", F.HOST_SUFFIX)
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    monkeypatch.setenv("REMITWATCH_EMA_STRICT", "1")
    monkeypatch.setenv("REMITWATCH_TZ", "America/New_York")
    for k in ("VERCEL", "REMITWATCH_EMA_REPORTS", "REMITWATCH_EMA_CLAIM_URL", "REMITWATCH_EMA_PAYER_ALIASES", "REMITWATCH_EMA_LEDGER",
              "REMITWATCH_EMA_REPORT_KEEP", "REMITWATCH_EMA_CLOSES", "REMITWATCH_STATE_DIR"):
        monkeypatch.delenv(k, raising=False)
    ec._LAST_POST.clear()
    states._RESPONSE_CACHE.clear()
    states._RECOVERY_CACHE.clear()


def _store(tmp_path, kind, monkeypatch):
    if kind == "file":
        s = Store(str(tmp_path / "store.sqlite"))
    else:
        # the store a serverless run works on: in memory, compact schema, EMA's reports read in full
        monkeypatch.setenv("REMITWATCH_MODE", "serverless")
        monkeypatch.setenv("REMITWATCH_EMA_REPORTS", "all")
        s = Store(":memory:", compact=True)
    er.ensure_schema(s.conn)
    s.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('ema:key_salt', ?)", (SALT_HEX,))
    s.conn.commit()
    actions.ensure_fax_tables(s.conn)
    return s, el.Ledger.open(str(tmp_path / f"ema_ledger_{kind}.sqlite"))


def _run(s, today, n):
    det = Detector(s, run_id=f"r{n}", today=today)
    det.run()
    assert det.ema_errors == [], det.ema_errors
    results.refresh(s.conn, today=today, force=True)       # the daily cycle's Results refresh, as both hosts run it
    return det


def _snb(s) -> list[dict]:
    s.conn.row_factory = None
    cols = [r[1] for r in s.conn.execute("PRAGMA table_info(findings)")]
    return [dict(zip(cols, r)) for r in s.conn.execute("SELECT * FROM findings WHERE signal='SECONDARY_NOT_BILLED'")]


def _world(s, lg, tmp_path, *, worked: bool, how: str) -> dict:
    """Sample Delta's 99213 of March 2 ($150): Blue Cross paid $90 on April 1, CO-45 $30 and $30 coinsurance (PR-2). EMA
    has Testcare Health on file as the second payer and $30 open. The first daily run lists 'bill Testcare Health'. A
    person presses Mark billed the next day. Testcare Health pays the $30 on May 12 (EMA posts it; with '835_too' its
    remittance as second payer, claim status 2, is read as well). The next daily run follows."""
    remit(s, trace="777000222", paid_on="2026-04-01",
          claims=[{"pcn": F.claim_id(8), "last": "SAMPLE", "first": "DELTA",
                   "lines": [(S, "99213", 150, 90, [("CO", "45", 30), ("PR", "2", 30)], {"allowed_cents": 12000})]}])
    kw = dict(patient=2, secondary=P["commercial"])
    rows = [charge(F.link_no(8), **kw), pay(F.link_no(8), "777000222", 90, posted=D(2026, 4, 3), **kw),
            adj(F.link_no(8), "CO-45", 30, posted=D(2026, 4, 3), contractual=True, **kw)]
    ar = [F.ar_row(claim=F.claim_id(8), link=F.link_no(8), status="Payment Pending", current=30, aging=D(2026, 4, 3), svc=SVC,
                   patient=2, secondary=P["commercial"])]
    denial_world(s, lg, tmp_path, rows, ar_rows=ar)
    _run(s, DAY1, 1)
    listed = [f for f in _snb(s) if not f["stale"]]
    assert len(listed) == 1 and listed[0]["work_state"] == "work_now", [(f["status"], f["work_state"]) for f in _snb(s)]
    fid = listed[0]["finding_id"]
    if worked:
        # what the page's Mark billed records: its own 'billed' line, and the status Sent with "Billed: <second payer>"
        second = json.loads(listed[0]["evidence"])["second_payer"]
        assert actions.apply_event(s.conn, {"finding_id": fid, "event": "billed", "note": f"Billed: {second}, May 4",
                                            "actor": "Biller One", "at": BILLED_AT})
        assert actions.apply_status(s.conn, fid, "appealed", f"Billed: {second}, May 4", None, "Biller One", at=BILLED_AT)
        s.conn.commit()
    if how == "835_too":
        remit(s, trace="777000333", paid_on="2026-05-12", payer="TESTCARE HEALTH", status="2",
              claims=[{"pcn": F.claim_id(8), "last": "SAMPLE", "first": "DELTA",
                       "lines": [(S, "99213", 150, 30, [("CO", "45", 30), ("OA", "23", 90)])]}])
    load(s, lg, tmp_path, [pay(F.link_no(8), "PAPER-8", 30, posted=D(2026, 5, 12), payer=P["commercial"], **kw),
                           charge(F.link_no(98), cpt="99212", patient=3, svc=D(2026, 5, 4), posted=D(2026, 5, 13))])
    _run(s, DAY2, 2)
    return {"fid": fid}


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("how", ["ema", "835_too"])
def test_a_second_payer_a_person_billed_and_that_paid_is_shown_or_closed_never_hidden_as_work(tmp_path, monkeypatch, kind, how):
    s, lg = _store(tmp_path, kind, monkeypatch)
    try:
        w = _world(s, lg, tmp_path, worked=True, how=how)
        got = [f for f in _snb(s) if f["finding_id"] == w["fid"]]
        assert len(got) == 1, "the worked item must stay on record"
        f = got[0]
        # the person's work and the second payer's payment are both on file: the item says so where a person sees it
        shown = (not f["stale"] and f["status"] == "appealed" and f["work_state"] == "work_now"
                 and f.get("state_rule") == "person_billed_paid" and "mark it Paid" in str(f.get("state_basis") or ""))
        ev = json.loads(f["evidence"]) if f.get("evidence") else {}
        closed = (not f["stale"] and f["status"] == "resolved" and bool(f.get("auto_note")) and bool(sc.has_proof(ev))
                  and int(f.get("recovered_cents") or 0) > 0)
        assert shown or closed, (
            "Testcare Health paid the $30 a person billed it for, yet the item is kept only as a stale row (on no list) whose "
            f"stored state is still work to do: stale={f['stale']} status={f['status']} work_state={f['work_state']} "
            f"rule={f.get('state_rule')} (the 'mark it Paid' step is never shown, and the Sent is never closed)")
    finally:
        lg.close()
        s.close()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("how", ["ema", "835_too"])
def test_the_results_open_book_never_counts_a_hidden_row_as_still_open(tmp_path, monkeypatch, kind, how):
    s, lg = _store(tmp_path, kind, monkeypatch)
    try:
        w = _world(s, lg, tmp_path, worked=True, how=how)
        d = results.dashboard(s.conn)
        o = d["outcomes"]
        still = {e[0] for r in o["open_by"] for e in results.list_entries(s.conn, f"book:open:{r['key']}", DAY2)}
        hidden = {r[0] for r in s.conn.execute("SELECT finding_id FROM findings WHERE COALESCE(stale,0)=1")}
        assert w["fid"] in {e[0] for e in results.list_entries(s.conn, "book", DAY2)}, "the item was in Work now: it is in the open book"
        assert not (still & hidden), (
            f"The Results tab's open book counts {len(still & hidden)} item(s) as still open "
            f"({[(r['key'], r['n']) for r in o['open_by']]}) that no list shows (kept stale after the second payer paid): "
            f"closed {o['closed']} of {o['book']}")
    finally:
        lg.close()
        s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_control_an_unbilled_item_the_second_payer_paid_leaves_the_list(tmp_path, monkeypatch, kind):
    """Nobody pressed Mark billed: the run drops the item when the second payer pays (it was never a person's work)."""
    s, lg = _store(tmp_path, kind, monkeypatch)
    try:
        w = _world(s, lg, tmp_path, worked=False, how="ema")
        assert [f for f in _snb(s) if not f["stale"]] == []
        o = results.dashboard(s.conn)["outcomes"]
        still = {e[0] for r in o["open_by"] for e in results.list_entries(s.conn, f"book:open:{r['key']}", DAY2)}
        closed = {e[0] for e in results.list_entries(s.conn, "closed:any", DAY2)}
        assert w["fid"] not in still and w["fid"] in closed, o
    finally:
        lg.close()
        s.close()


# ------------------------------------------------------------------ what the run does with each answer (replay-fix-2a A4)
import sqlite3  # noqa: E402

KINDS3 = ["file", "compact"] + (["image"] if hasattr(sqlite3.Connection, "serialize") else [])
KW = dict(patient=2, secondary=P["commercial"])


class _W:
    """The store of a kind and its ledger; 'image' opens the compact store's serialized image again before every run (the
    serverless host's next invocation)."""

    def __init__(self, tmp_path, kind: str, monkeypatch):
        self.tmp, self.kind = tmp_path, kind
        self.s, self.lg = _store(tmp_path, "file" if kind == "file" else "compact", monkeypatch)

    def run(self, today: str, n: int):
        if self.kind == "image":
            data = self.s.to_bytes()
            self.s.close()
            self.s = Store.from_bytes(data)
            er.ensure_schema(self.s.conn)
        return _run(self.s, today, n)

    def item(self, fid: str) -> dict:
        got = [f for f in _snb(self.s) if f["finding_id"] == fid]
        assert len(got) == 1, got
        return got[0]

    def close(self) -> None:
        self.lg.close()
        self.s.close()


def _billed(w: _W, *, status: str = "appealed") -> str:
    """Day 1 lists 'bill Testcare Health' for Sample Delta's $30 coinsurance (as _world); a person presses Mark billed the
    next day (status Sent), or sets Started."""
    remit(w.s, trace="777000222", paid_on="2026-04-01",
          claims=[{"pcn": F.claim_id(8), "last": "SAMPLE", "first": "DELTA",
                   "lines": [(S, "99213", 150, 90, [("CO", "45", 30), ("PR", "2", 30)], {"allowed_cents": 12000})]}])
    rows = [charge(F.link_no(8), **KW), pay(F.link_no(8), "777000222", 90, posted=D(2026, 4, 3), **KW),
            adj(F.link_no(8), "CO-45", 30, posted=D(2026, 4, 3), contractual=True, **KW)]
    ar = [F.ar_row(claim=F.claim_id(8), link=F.link_no(8), status="Payment Pending", current=30, aging=D(2026, 4, 3), svc=SVC,
                   patient=2, secondary=P["commercial"])]
    denial_world(w.s, w.lg, w.tmp, rows, ar_rows=ar)
    w.run(DAY1, 1)
    listed = [f for f in _snb(w.s) if not f["stale"]]
    assert len(listed) == 1 and listed[0]["work_state"] == "work_now"
    fid = listed[0]["finding_id"]
    assert actions.apply_event(w.s.conn, {"finding_id": fid, "event": "billed", "note": "Billed: Testcare Health, May 4",
                                          "actor": "Biller One", "at": BILLED_AT})
    assert actions.apply_status(w.s.conn, fid, status, "Billed: Testcare Health, May 4", None, "Biller One", at=BILLED_AT)
    w.s.conn.commit()
    return fid


def _ema(w: _W, *rows) -> None:
    """EMA's next Production postings, with a later charge that carries its as_of past them."""
    load(w.s, w.lg, w.tmp, list(rows) + [charge(F.link_no(98), cpt="99212", patient=3, svc=D(2026, 5, 4), posted=D(2026, 5, 13))])


def _second_paid(cents_dollars: float, posted=D(2026, 5, 12)):
    return pay(F.link_no(8), "PAPER-8", cents_dollars, posted=posted, payer=P["commercial"], **KW)


def _second_835(w: _W, paid: float, adjs: list, *, allowed: float | None = None, trace="777000333", day="2026-05-12", status="2",
                charge_: float = 150) -> None:
    extra = {"allowed_cents": round(allowed * 100)} if allowed is not None else {}
    remit(w.s, trace=trace, paid_on=day, payer="TESTCARE HEALTH", status=status,
          claims=[{"pcn": F.claim_id(8), "last": "SAMPLE", "first": "DELTA", "lines": [(S, "99213", charge_, paid, adjs, extra)]}])


def _decisions(w: _W, fid: str, run_id: str) -> list[tuple]:
    return [tuple(r) for r in w.s.conn.execute("SELECT kind, rule FROM item_decisions WHERE finding_id=? AND run_id=?", (fid, run_id))]


@pytest.mark.parametrize("kind", KINDS3)
def test_a_second_payer_that_paid_part_of_the_share_stays_listed_to_work_its_answer(tmp_path, monkeypatch, kind):
    """Testcare Health pays $20 of the $30 billed to it: the $10 left stays on the list, Sent, with 'work its answer'; never
    closed as Paid, never hidden."""
    w = _W(tmp_path, kind, monkeypatch)
    try:
        fid = _billed(w)
        _ema(w, _second_paid(20))
        w.run(DAY2, 2)
        f = w.item(fid)
        ev = json.loads(f["evidence"])
        assert not f["stale"] and f["status"] == "appealed" and f["work_state"] == "work_now", (f["stale"], f["status"], f["work_state"])
        assert f["state_rule"] == "work_answered" and "work its answer" in f["state_basis"], f["state_basis"]
        assert "$10.00" in f["state_basis"] and "paid $20.00" in f["state_basis"], f["state_basis"]
        assert f["shortfall_cents"] == 1000 and not f["auto_note"] and not f["recovered_cents"]
        sa = ev["second_answer"]
        assert (sa["paid_cents"], sa["owed_cents"], sa["left_cents"], sa["billed_cents"]) == (2000, 3000, 1000, 3000), sa
        assert ev["billed_share_cents"] == 3000
        # the next run sizes it from what was billed, never from what was left the run before
        w.run("2026-05-20", 3)
        f = w.item(fid)
        assert f["shortfall_cents"] == 1000 and not f["stale"] and f["state_rule"] == "work_answered"
    finally:
        w.close()


@pytest.mark.parametrize("kind", KINDS3)
def test_a_second_payer_that_answered_without_paying_stays_listed_to_work_its_answer(tmp_path, monkeypatch, kind):
    """Testcare Health processes the claim as second payer and pays nothing (CO-96): the whole share stays listed, Sent, with
    its answer to work."""
    w = _W(tmp_path, kind, monkeypatch)
    try:
        fid = _billed(w)
        _second_835(w, 0, [("CO", "96", 30), ("OA", "23", 120)])
        _ema(w)
        w.run(DAY2, 2)
        f = w.item(fid)
        ev = json.loads(f["evidence"])
        assert not f["stale"] and f["status"] == "appealed" and f["state_rule"] == "work_answered", (f["stale"], f["status"], f["state_rule"])
        assert "CO-96" in f["state_basis"] and "work its answer" in f["state_basis"], f["state_basis"]
        assert f["shortfall_cents"] == 3000 and ev["second_answer"]["paid_cents"] == 0 and "CO-96" in ev["second_answer"]["codes"]
    finally:
        w.close()


@pytest.mark.parametrize("kind", KINDS3)
def test_the_patient_paying_the_share_closes_it_with_the_patients_payment_as_its_proof(tmp_path, monkeypatch, kind):
    """The patient pays the $30 coinsurance billed to Testcare Health: closed in the run as paid by the patient, EMA's
    figure its proof, no payer's recovery, a row in the decision log; the open book counts it closed."""
    w = _W(tmp_path, kind, monkeypatch)
    try:
        fid = _billed(w)
        _ema(w, F.production_row(link=F.link_no(8), cpt="99213", pat_paid=30, patient=2, svc=SVC, posted=D(2026, 5, 12),
                                 payer=P["bcbs"], primary=P["bcbs"], secondary=P["commercial"]))
        w.run(DAY2, 2)
        f = w.item(fid)
        ev = json.loads(f["evidence"])
        assert not f["stale"] and f["status"] == "resolved" and f["work_state"] == "done", (f["stale"], f["status"], f["work_state"])
        assert str(f["auto_note"]).startswith("Paid by the patient") and sc.has_proof(ev)
        assert int(f["recovered_cents"] or 0) == 0 and ev["paid_later"]["how"] == "settled by the patient"
        assert [r["kind"] for r in ev["paid_later"]["proof"]] == ["patient_payment"]
        assert ev["paid_later"]["proof"][0]["amount_cents"] == 3000 and ev["paid_later"]["proof"][0]["date"]
        assert ("closed", "paid_later:settled_by_the_patient") in _decisions(w, fid, "r2")
        closed = {e[0] for e in results.list_entries(w.s.conn, "closed:patient_paid", DAY2)}
        assert fid in closed
    finally:
        w.close()


@pytest.mark.parametrize("kind", KINDS3)
@pytest.mark.parametrize("how", ["ema", "835"])
def test_a_paid_second_payer_closes_as_paid_with_its_proof_and_a_decision_log_row(tmp_path, monkeypatch, kind, how):
    """Testcare Health pays the $30 (EMA's posting, or its 835 as second payer): closed in the run as Paid, the payment its
    proof, recovered $30 (what arrived as secondary), a 'closed' row in the decision log naming the rule. The next run
    keeps it closed and counts the recovery once."""
    w = _W(tmp_path, kind, monkeypatch)
    try:
        fid = _billed(w)
        if how == "835":
            _second_835(w, 30, [("CO", "45", 30), ("OA", "23", 90)])
            _ema(w)
        else:
            _ema(w, _second_paid(30))
        w.run(DAY2, 2)
        f = w.item(fid)
        ev = json.loads(f["evidence"])
        assert not f["stale"] and f["status"] == "resolved" and f["prev_status"] == "appealed", (f["stale"], f["status"])
        assert f["work_state"] == "done" and str(f["auto_note"]).startswith("Paid by Testcare Health as second payer"), f["auto_note"]
        assert f["recovered_cents"] == 3000 and sc.has_proof(ev) and ev["paid_later"]["how"] == "paid as second payer"
        assert sc.proof_money(ev["paid_later"]["proof"]) == 3000
        assert ("closed", "paid_later:paid_as_second_payer") in _decisions(w, fid, "r2")
        w.run("2026-05-20", 3)
        f = w.item(fid)
        assert not f["stale"] and f["status"] == "resolved" and f["recovered_cents"] == 3000
        n = w.s.conn.execute("SELECT COUNT(*) FROM finding_events WHERE finding_id=? AND kind='paid_on_later_remit'", (fid,)).fetchone()[0]
        assert n == 1
    finally:
        w.close()


@pytest.mark.parametrize("kind", KINDS3)
def test_a_person_started_billing_counts_as_billed(tmp_path, monkeypatch, kind):
    """A person's own Started (not Sent) on the item: the second payer's payment closes it as Paid the same way."""
    w = _W(tmp_path, kind, monkeypatch)
    try:
        fid = _billed(w, status="in_progress")
        _ema(w, _second_paid(30))
        w.run(DAY2, 2)
        f = w.item(fid)
        assert not f["stale"] and f["status"] == "resolved" and f["prev_status"] == "in_progress" and f["recovered_cents"] == 3000
    finally:
        w.close()


@pytest.mark.parametrize("kind", KINDS3)
def test_the_second_payers_takeback_reopens_the_closure_as_the_persons_sent(tmp_path, monkeypatch, kind):
    """Testcare Health pays $30 on its 835 (closed as Paid), then takes it back (claim status 22): the next run reopens the
    item to the person's Sent, listed, with the answer to work; nothing stays counted as recovered."""
    w = _W(tmp_path, kind, monkeypatch)
    try:
        fid = _billed(w)
        _second_835(w, 30, [("CO", "45", 30), ("OA", "23", 90)])
        _ema(w)
        w.run(DAY2, 2)
        assert w.item(fid)["status"] == "resolved"
        _second_835(w, -30, [("CO", "45", -30), ("OA", "23", -90)], trace="777000444", day="2026-05-19", status="22", charge_=-150)
        w.run("2026-05-21", 3)
        f = w.item(fid)
        assert not f["stale"] and f["status"] == "appealed" and not f["auto_note"] and not f["recovered_cents"], \
            (f["stale"], f["status"], f["auto_note"], f["recovered_cents"])
        assert f["work_state"] == "work_now" and f["state_rule"] == "work_answered", (f["work_state"], f["state_rule"])
        assert ("reopened" in {k for k, _r in _decisions(w, fid, "r3")}), _decisions(w, fid, "r3")
    finally:
        w.close()


@pytest.mark.parametrize("kind", KINDS3)
def test_a_person_who_moved_it_off_the_closure_keeps_their_status(tmp_path, monkeypatch, kind):
    """The run closed it as Paid; a person sets it back to Sent ('that payment was for another visit'). The next run leaves
    the person's Sent in place, listed, and says a payment was found: a person's decision is never overwritten."""
    w = _W(tmp_path, kind, monkeypatch)
    try:
        fid = _billed(w)
        _ema(w, _second_paid(30))
        w.run(DAY2, 2)
        assert w.item(fid)["status"] == "resolved"
        # recorded at the moment of the press, after the run that closed it (the decision is the person's from then on)
        assert actions.apply_status(w.s.conn, fid, "appealed", "That payment was for another visit", None, "Biller One")
        w.s.conn.commit()
        w.run("2026-05-20", 3)
        f = w.item(fid)
        assert not f["stale"] and f["status"] == "appealed" and not f["auto_note"], (f["stale"], f["status"], f["auto_note"])
        assert "kept as" in str(f["hint"] or ""), f["hint"]
    finally:
        w.close()


@pytest.mark.parametrize("kind", ["file", "compact"])
@pytest.mark.parametrize("paid,closed", [(10, True), (5, False)], ids=["paid_what_it_owes", "paid_less_than_it_owes"])
def test_what_the_second_payer_owes_is_its_own_allowed_amount_where_its_835_gives_it(tmp_path, monkeypatch, kind, paid, closed):
    """Testcare Health allows $100 (less than Blue Cross's $120): as second payer it owes $10 (its allowed less the $90 Blue
    Cross paid), not the $30 share (the owner's decision 3). Paying $10 settles it (closed, $10 recovered); paying $5
    leaves $5 to work."""
    w = _W(tmp_path, kind, monkeypatch)
    try:
        fid = _billed(w)
        _second_835(w, paid, [("CO", "45", 50), ("OA", "23", 90), ("CO", "45", 10 - paid)] if paid < 10 else
                    [("CO", "45", 50), ("OA", "23", 90)], allowed=100)
        _ema(w)
        w.run(DAY2, 2)
        f = w.item(fid)
        ev = json.loads(f["evidence"])
        if closed:
            assert f["status"] == "resolved" and f["recovered_cents"] == 1000 and "$10.00 it owes as second payer" in f["auto_note"], \
                (f["status"], f["auto_note"])
        else:
            assert f["status"] == "appealed" and not f["stale"] and f["shortfall_cents"] == 500, (f["status"], f["shortfall_cents"])
            assert ev["second_answer"]["owed_cents"] == 1000 and ev["second_answer"]["left_cents"] == 500
    finally:
        w.close()


def test_results_counts_a_stale_row_as_no_longer_listed_never_still_open():
    """results._closure: a row the run keeps stale (on no list) is closed, as paid when money came back on it, else gone,
    whatever state it was stored with; a live row in an open state stays open."""
    for ws in ("work_now", "waiting", "cant_check", None):
        assert results._closure({}, {"work_state": ws, "status": "appealed", "stale": 1}, 0) == "gone"
        assert results._closure({}, {"work_state": ws, "status": "appealed", "stale": 1}, 2500) == "paid"
        assert results._closure({}, {"work_state": ws, "status": "appealed", "stale": 0}, 0) is None


@pytest.mark.parametrize("kind", ["file", "compact"])
def test_a_started_the_run_handed_on_is_no_persons_billing(tmp_path, monkeypatch, kind):
    """The item's Started came from the run's hand-off (a claim's chase a person had worked; only a system event set it),
    not from a person billing the second payer (states.person_started, replay-fix-2a A2): when the second payer pays, the
    rule judges it as To do and takes it off the list, as it does an unworked one; it is never closed as a person's bill."""
    w = _W(tmp_path, kind, monkeypatch)
    try:
        fid = _billed(w, status="in_progress")
        c = w.s.conn
        c.execute("DELETE FROM finding_events WHERE finding_id=? AND kind IN ('manual', 'billed')", (fid,))
        c.execute("INSERT INTO finding_events (finding_id, at, kind, from_status, to_status, amount_cents, actor, note, run_id) "
                  "VALUES (?,?,?,?,?,?,?,?,?)", (fid, "2026-05-04T14:00:00+00:00", "signal_changed", "appealed", "in_progress", 3000,
                                                  "system", "was NO_ANSWER on claim ZZ-INVENTED (appealed), now SECONDARY_NOT_BILLED", "r1"))
        c.commit()
        _ema(w, _second_paid(30))
        w.run(DAY2, 2)
        f = w.item(fid)
        assert f["stale"] == 1 and f["status"] == "in_progress" and not f["auto_note"], (f["stale"], f["status"], f["auto_note"])
        assert f["work_state"] == "done", (f["work_state"], f["state_basis"])
        closed = {e[0] for e in results.list_entries(c, "closed:any", DAY2)}
        still = {e[0] for r in results.dashboard(c)["outcomes"]["open_by"] for e in results.list_entries(c, f"book:open:{r['key']}", DAY2)}
        assert fid in closed and fid not in still
    finally:
        w.close()


def test_the_open_book_counts_a_worked_chase_taken_off_the_list_as_no_longer_listed(tmp_path):
    """Any row the run keeps stale is on no list: a chase a person marked Sent, taken off the list when the payer's answer
    came (it is listed again as the answer's own item), is closed in the open book, and never 'worked and waiting with no
    money yet'."""
    from tests.test_decision_log_keeps_stale_after_a_persons_status import _chase_sent_then_answered
    fid, w = _chase_sent_then_answered(tmp_path, "file", run_between=False)
    try:
        day_ = "2026-11-30"
        results.refresh(w.conn, today=day_, force=True)
        f = next(x for x in w.items() if x["finding_id"] == fid)
        assert f["stale"] == 1 and f["status"] == "appealed" and f["work_state"] in ("work_now", "waiting"), \
            (f["stale"], f["status"], f["work_state"])
        d = results.dashboard(w.conn)
        book = {e[0] for e in results.list_entries(w.conn, "book", day_)}
        still = {e[0] for r in d["outcomes"]["open_by"] for e in results.list_entries(w.conn, f"book:open:{r['key']}", day_)}
        waiting = {e[0] for e in results.list_entries(w.conn, "waiting_no_money", day_)}
        assert fid in book, "the chase was Work now: it is in the open book"
        assert fid not in still and fid not in waiting, (fid in still, fid in waiting)
        assert fid in {e[0] for e in results.list_entries(w.conn, "closed:any", day_)}
    finally:
        w.store.close()


def _listed_for_service(w: _W) -> list[dict]:
    return [f for f in _snb(w.s) if not f["stale"]]


@pytest.mark.parametrize("kind", KINDS3)
def test_the_first_payer_paying_the_line_again_keeps_the_persons_item_on_its_own_line(tmp_path, monkeypatch, kind):
    """After the person billed Testcare Health, Blue Cross takes back its April payment and pays the line again on May 20
    (a new line of its remittance), while Testcare Health's $30 was posted in EMA on May 12. The rule's candidate for the
    service is now Blue Cross's new line; the item the person billed keeps its own line and closes as Paid on Testcare's
    payment. No second item is listed beside it."""
    w = _W(tmp_path, kind, monkeypatch)
    try:
        fid = _billed(w)
        remit(w.s, trace="777000555", paid_on="2026-05-20",
              claims=[{"pcn": F.claim_id(8), "last": "SAMPLE", "first": "DELTA", "status": "22",
                       "lines": [(S, "99213", -150, -90, [("CO", "45", -30), ("PR", "2", -30)], {"allowed_cents": -12000})]},
                      {"pcn": F.claim_id(8), "last": "SAMPLE", "first": "DELTA", "status": "1",
                       "lines": [(S, "99213", 150, 90, [("CO", "45", 30), ("PR", "2", 30)], {"allowed_cents": 12000})]}])
        _ema(w, _second_paid(30))
        w.run("2026-05-25", 2)
        f = w.item(fid)
        assert not f["stale"] and f["status"] == "resolved" and f["recovered_cents"] == 3000, (f["stale"], f["status"], f["recovered_cents"])
        assert [x["finding_id"] for x in _listed_for_service(w)] == [fid], [(x["finding_id"] == fid, x["status"]) for x in _listed_for_service(w)]
    finally:
        w.close()


@pytest.mark.parametrize("kind", KINDS3)
def test_a_second_payers_payment_posted_with_an_earlier_date_still_closes_it(tmp_path, monkeypatch, kind):
    """EMA's posting of Testcare Health's $30 arrives after the person billed it, dated March 31 (a posting entered with an
    earlier date, before Blue Cross's own payment): it is still the second payer's answer to the bill, as the rule itself
    reads another payer's money on the line, whatever its date. Closed as Paid."""
    w = _W(tmp_path, kind, monkeypatch)
    try:
        fid = _billed(w)
        _ema(w, _second_paid(30, posted=D(2026, 3, 31)))
        w.run(DAY2, 2)
        f = w.item(fid)
        assert not f["stale"] and f["status"] == "resolved" and f["recovered_cents"] == 3000, (f["stale"], f["status"], f["recovered_cents"])
    finally:
        w.close()


@pytest.mark.parametrize("kind", KINDS3)
def test_the_items_own_ema_claim_still_shows_its_answer_when_the_link_map_lets_go_of_the_bill(tmp_path, monkeypatch, kind):
    """The link map stops pairing the bill with its EMA claim (a vote split marks the pair a conflict) after the person
    billed Testcare Health, whose $30 EMA then posts: the item reads its own EMA claim's line and closes as Paid, never
    dropped for want of the map."""
    w = _W(tmp_path, kind, monkeypatch)
    try:
        fid = _billed(w)
        _ema(w, _second_paid(30))
        w.s.conn.execute("UPDATE ema_link_map SET conflict = 1 WHERE link = ?", (F.link_no(8),))
        w.s.conn.commit()
        w.run(DAY2, 2)
        assert w.s.conn.execute("SELECT conflict FROM ema_link_map WHERE link = ?", (F.link_no(8),)).fetchone()[0] == 1, \
            "the run mapped the claim again: this case no longer tests the fallback"
        f = w.item(fid)
        assert not f["stale"] and f["status"] == "resolved" and f["recovered_cents"] == 3000, (f["stale"], f["status"], f["recovered_cents"])
    finally:
        w.close()


@pytest.mark.parametrize("kind", KINDS3)
def test_a_takeback_by_the_first_payer_leaves_the_persons_item_closed_on_the_second_payers_payment(tmp_path, monkeypatch, kind):
    """After the person billed Testcare Health, Blue Cross takes back its own payment of the line (claim status 22) and
    pays nothing again, so no line of the service is the rule's candidate any more; Testcare Health pays its $30. The
    item the person billed is still read on its own line and closes as Paid, never left stale with the person's Sent."""
    w = _W(tmp_path, kind, monkeypatch)
    try:
        fid = _billed(w)
        remit(w.s, trace="777000666", paid_on="2026-05-10",
              claims=[{"pcn": F.claim_id(8), "last": "SAMPLE", "first": "DELTA", "status": "22",
                       "lines": [(S, "99213", -150, -90, [("CO", "45", -30), ("PR", "2", -30)], {"allowed_cents": -12000})]}])
        _ema(w, _second_paid(30))
        w.run(DAY2, 2)
        f = w.item(fid)
        assert not f["stale"] and f["status"] == "resolved" and f["recovered_cents"] == 3000, (f["stale"], f["status"], f["recovered_cents"])
        ev = json.loads(f["evidence"])
        assert sc.has_proof(ev) and sc.proof_money(ev["paid_later"]["proof"]) == 3000
        assert ("closed", "paid_later:paid_as_second_payer") in _decisions(w, fid, "r2")
    finally:
        w.close()


@pytest.mark.parametrize("kind", KINDS3)
def test_a_takeback_with_no_answer_from_the_second_payer_changes_nothing_of_the_persons_item(tmp_path, monkeypatch, kind):
    """Control: Blue Cross takes its payment back after the person billed Testcare Health, and Testcare has not answered.
    No answer, no automatic change: the person's Sent stays as it was, no closure, no decision row for the item."""
    w = _W(tmp_path, kind, monkeypatch)
    try:
        fid = _billed(w)
        remit(w.s, trace="777000667", paid_on="2026-05-10",
              claims=[{"pcn": F.claim_id(8), "last": "SAMPLE", "first": "DELTA", "status": "22",
                       "lines": [(S, "99213", -150, -90, [("CO", "45", -30), ("PR", "2", -30)], {"allowed_cents": -12000})]}])
        w.run(DAY2, 2)
        f = w.item(fid)
        assert f["status"] == "appealed" and not f.get("auto_note") and not f["recovered_cents"], (f["status"], f["recovered_cents"])
        assert not [d for d in _decisions(w, fid, "r2") if d[0] == "closed"], _decisions(w, fid, "r2")
    finally:
        w.close()


# ------------------------------------------------------------------ what counts as the second payer's answer (review r1)
@pytest.mark.parametrize("kind", KINDS3)
@pytest.mark.parametrize("who", ["namesake", "same_patient"])
def test_another_patients_payment_on_another_ema_claim_never_closes_the_persons_billed_item(tmp_path, monkeypatch, kind, who):
    """Sample Delta's $30 coinsurance is billed to Testcare Health by a person. Another patient with the same name and
    another date of birth (a son seen the same day, say) has his own EMA claim for a 99213 that day, and his own payer,
    Optum, pays it. That is not Testcare's answer to this bill: the person's Sent stands, nothing is recovered, no closure
    is logged. The control ('same_patient'): the same patient's second EMA claim for the service (a claim EMA keeps apart)
    carries Testcare's payment, and closes the item."""
    w = _W(tmp_path, kind, monkeypatch)
    try:
        fid = _billed(w)
        name, dob, _mrn = F.PATIENTS[2]
        if who == "namesake":
            kw = dict(patient=name, dob=D(2001, 5, 6), primary=P["optum"])
            rows = [charge(F.link_no(9), posted=D(2026, 5, 10), **kw),
                    pay(F.link_no(9), "777000555", 80, posted=D(2026, 5, 12), payer=P["optum"], **kw)]
        else:
            kw = dict(patient=name, dob=dob, primary=P["commercial"])
            rows = [charge(F.link_no(9), posted=D(2026, 5, 10), **kw),
                    pay(F.link_no(9), "777000555", 30, posted=D(2026, 5, 12), payer=P["commercial"], **kw)]
        _ema(w, *rows)
        w.run(DAY2, 2)
        f = w.item(fid)
        if who == "same_patient":
            assert f["status"] == "resolved" and f["recovered_cents"] == 3000, (f["status"], f["auto_note"], f["recovered_cents"])
            return
        assert f["status"] == "appealed" and not f["auto_note"] and not f["recovered_cents"], (
            f"another patient's payment (same name, another date of birth, his own EMA claim and payer) closed the person's "
            f"billed item as Paid: {f['status']} / {f['auto_note']} / recovered {f['recovered_cents']}")
        assert not [d for d in _decisions(w, fid, "r2") if d[0] == "closed"], _decisions(w, fid, "r2")
    finally:
        w.close()


@pytest.mark.parametrize("kind", KINDS3)
def test_the_first_payers_own_payment_under_another_payer_name_never_closes_it_as_the_second_payers(tmp_path, monkeypatch, kind):
    """Blue Cross paid the visit and left $30 coinsurance; a person billed Testcare Health. Blue Cross then pays the visit
    again on a remittance that names it 'BLUE CROSS TEST PLAN' (the same payer group, another payer name: a reprocessing).
    Testcare has not answered: that is the first payer's money, never the second payer's payment of the share."""
    w = _W(tmp_path, kind, monkeypatch)
    try:
        fid = _billed(w)
        remit(w.s, trace="777000666", paid_on="2026-05-12", payer="BLUE CROSS TEST PLAN", status="1",
              claims=[{"pcn": F.claim_id(8), "last": "SAMPLE", "first": "DELTA",
                       "lines": [(S, "99213", 150, 90, [("CO", "45", 30), ("PR", "2", 30)])]}])
        _ema(w)
        w.run(DAY2, 2)
        f = w.item(fid)
        assert f["status"] == "appealed" and not f["auto_note"] and not f["recovered_cents"], (
            f"the first payer's own second payment closed the person's bill to Testcare as 'paid as second payer': "
            f"{f['status']} / {f['auto_note']} / recovered {f['recovered_cents']}")
        assert "second_answer" not in json.loads(f["evidence"] or "{}"), "the first payer's payment was read as the second payer's answer"
    finally:
        w.close()


@pytest.mark.parametrize("kind", KINDS3)
def test_the_runs_work_its_answer_change_is_in_the_decision_log_after_a_persons_status(tmp_path, monkeypatch, kind):
    """A person billed Testcare Health (Sent) and the next run moved the item to Waiting. A week later the person records
    that they billed it again (Sent again, their own history line). Testcare then pays $20 of the $30: the run moves the
    item to Work now, 'work its answer', at the $10 left. That change is the run's (the person's Sent was already in the
    run's 'before'): it is in the decision log with its rule, as every automatic change is."""
    import time
    w = _W(tmp_path, kind, monkeypatch)
    try:
        fid = _billed(w)
        w.run("2026-05-06", 2)                        # the person's Sent is judged: Waiting on Testcare
        assert w.item(fid)["work_state"] == "waiting", w.item(fid)["work_state"]
        time.sleep(1.1)                               # the log's times are to the second: the press comes after that run
        assert actions.apply_status(w.s.conn, fid, "appealed", "Billed again (invented)", None, "Biller One")
        w.s.conn.commit()
        _ema(w, _second_paid(20))
        w.run(DAY2, 3)
        f = w.item(fid)
        assert (f["work_state"], f["state_rule"], f["shortfall_cents"]) == ("work_now", "work_answered", 1000), \
            (f["work_state"], f["state_rule"], f["shortfall_cents"])
        rows = _decisions(w, fid, "r3")
        assert ("state", "states:work_answered") in rows, (
            f"the run moved the billed item from Waiting to Work now ('work its answer', $30.00 to $10.00) and the decision "
            f"log has no row for it: {rows}")
    finally:
        w.close()
