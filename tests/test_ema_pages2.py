"""
The EMA pages (unit C2): remitwatch/api_ema.py and the page scripts it feeds (static/today.js, static/data.js,
static/policy.js). Today's counts by state, EMA's automatic write-offs still appealable, the items nobody has worked and
the blind spot; the Data tab's catalog of EMA's reports, the payments with no remittance and their re-send requests, the
A/R accounted for, credits and the CSVs; the owner's write-off policy table, its decisions on both hosts, and the items
of a pattern.

Every row is invented (tests/ema_fixtures.py). The world is built the way the cycle builds it: 835 payments written into
the store's remittance tables, EMA's A/R and Production reports read by the real readers into the store and a posting
ledger, the summaries refreshed, the Detector run. Dates are counted back from today, so the filing windows and the
reports' ages mean the same whenever the suite runs. Both hosts: the server reads the store; the serverless host reads
the dashboard's copy (cloud.build_serving) through cloud.ServingCopy, and a decision goes through the journal.
"""
from __future__ import annotations

import base64
import datetime as dt
import itertools
import json
import os
import re
import shutil
import sqlite3
import subprocess

import pytest

from remitwatch import cloud, ema_checks as ec, ema_ledger as el, ema_reports as er, runtime
from remitwatch.detect import Detector
from remitwatch.state import LocalState
from remitwatch.store import Store
from tests import ema_fixtures as F

P = F.PAYERS
SALT_HEX = "cd" * 32
XHR = {"X-Requested-With": "remitwatch"}
BCBS = "Anthem / BCBS"
T = dt.date.fromisoformat(runtime.practice_today())  # the site's day: the practice's, as every list counts windows
TODAY = T.isoformat()
_N = itertools.count(1)
_TX = itertools.count(1)


def ago(n: int) -> dt.date:
    return T - dt.timedelta(days=n)


def iso(n: int) -> str:
    return ago(n).isoformat()


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(er, "CLAIM_HOST_SUFFIX", F.HOST_SUFFIX)
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    monkeypatch.setenv("REMITWATCH_EMA_STRICT", "1")
    for k in ("VERCEL", "VERCEL_ENV", "REMITWATCH_EMA_REPORTS", "REMITWATCH_EMA_CLAIM_URL", "REMITWATCH_EMA_PAYER_ALIASES",
              "REMITWATCH_EMA_LEDGER", "REMITWATCH_EMA_REPORT_KEEP", "REMITWATCH_EMA_CLOSES", "REMITWATCH_STATE_DIR",
              "DROPBOX_APP_KEY", "DROPBOX_APP_SECRET", "DROPBOX_REFRESH_TOKEN", "CRON_SECRET"):
        monkeypatch.delenv(k, raising=False)
    ec._LAST_POST.clear()


class Log(list):
    def __call__(self, *a):
        self.append(" ".join(str(x) for x in a))


# --------------------------------------------------------------------------------------------------- the world
def remit(store, *, trace, paid_on, claims, payer="BLUE CROSS TEST"):
    """One 835 payment written straight into the store (as tests/test_ema_wiring.py does). claims: dicts with pcn and
    lines [(svc, code, charge $, paid $, [(group, reason, $), ...], {more line columns})]."""
    c = store.conn
    tk = f"TX{next(_TX):05d}"
    total = sum(round(ln[3] * 100) for cl in claims for ln in cl["lines"])
    c.execute("INSERT INTO transactions (transaction_key, trace_number, payment_date, payer_name, superseded, "
              "claim_paid_total_cents, payment_amount_cents) VALUES (?,?,?,?,0,?,?)", (tk, trace, paid_on, payer, total, total))
    for i, cl in enumerate(claims):
        ck = f"{tk}-{i}"
        c.execute("INSERT INTO claims (claim_key, transaction_key, patient_control_number, patient_last, patient_first, patient_id, "
                  "payment_date, trace_number, statement_from, claim_status_code, charge_cents, paid_cents, payer_name) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (ck, tk, cl["pcn"], cl.get("last", "TESTPATIENT"), cl.get("first", "ALPHA"), "MEMBER000001", paid_on, trace,
                   cl["lines"][0][0], cl.get("status", "1"),
                   sum(round(ln[2] * 100) for ln in cl["lines"]), sum(round(ln[3] * 100) for ln in cl["lines"]), payer))
        for j, ln in enumerate(cl["lines"]):
            svc, code, chg, paid = ln[:4]
            lk = f"{ck}-{j}"
            cols = {"line_key": lk, "claim_key": ck, "transaction_key": tk, "procedure_code": code, "service_date": svc,
                    "charge_cents": round(chg * 100), "paid_cents": round(paid * 100), "patient_control_number": cl["pcn"],
                    "payment_date": paid_on, "payer_name": payer, "claim_status_code": cl.get("status", "1")}
            cols.update(ln[5] if len(ln) > 5 else {})
            c.execute(f"INSERT INTO lines ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", tuple(cols.values()))
            for g, r, amt in (ln[4] if len(ln) > 4 else []):
                c.execute("INSERT INTO adjustments (transaction_key, claim_key, line_key, level, group_code, reason_code, amount_cents) "
                          "VALUES (?,?,?,?,?,?,?)", (tk, ck, lk, "line", g, r, round(amt * 100)))
    c.commit()
    return tk


def charge(link, cpt="99213", amount=150, svc=None, posted=None, **kw):
    svc = svc or ago(70)
    return F.production_row(link=link, cpt=cpt, charges=amount, svc=svc, posted=posted or svc + dt.timedelta(days=2), **kw)


def pay(link, check, amount, cpt="99213", svc=None, posted=None, payer=P["bcbs"], **kw):
    return F.production_row(link=link, cpt=cpt, ins_paid=amount, check=check, svc=svc or ago(70), posted=posted or ago(58), payer=payer, **kw)


def adj(link, reason, amount, cpt="99213", svc=None, posted=None, payer=P["bcbs"], **kw):
    return F.production_row(link=link, cpt=cpt, reason=reason, noncontractual=amount, svc=svc or ago(70), posted=posted or ago(58),
                            payer=payer, **kw)


def build(path, *, fresh: bool = True) -> str:
    """The invented practice. Twelve Blue Cross visits paid long ago give the payer's usual rates (99213 $90, 99214 $120).
    Three Blue Cross claims answered on one remittance 60 days ago:
      1. 99213 denied for no authorization (CO-197); EMA wrote the denial off the day it posted: an automatic write-off;
      2. 99214 denied for missing information (CO-16); EMA wrote it off the day it posted, while the payer waited for
         what it asked for;
      3. 99213 denied for no authorization; EMA posted $90 from the payer 30 days ago on a check no 835 carries: paid.
    A remittance 25 days ago that EMA never posted, a credit EMA holds on another claim, a check EMA posted five days ago
    with no remittance (Testcare Health), a charge two days ago (Production's as of), and an A/R pulled three days ago with
    the first denial, an open claim and a Medicare credit over 60 days. `fresh`: a remittance three days ago keeps the 835
    feed current; without it the newest remittance is 25 days old."""
    os.makedirs(path, exist_ok=True)
    db = os.path.join(path, "store.sqlite")
    s = Store(db)
    er.ensure_schema(s.conn)
    s.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('ema:key_salt', ?)", (SALT_HEX,))
    s.conn.commit()
    svc = iso(70)
    remit(s, trace=F.check_no(10), paid_on=iso(200), claims=[
        {"pcn": F.claim_id(100 + i), "lines": [(iso(210), "99213", 150, 90, [("CO", "45", 60)], {"allowed_cents": 9000}),
                                               (iso(210), "99214", 200, 120, [("CO", "45", 80)], {"allowed_cents": 12000})]}
        for i in range(12)])
    remit(s, trace=F.check_no(1), paid_on=iso(60), claims=[
        {"pcn": F.claim_id(1), "lines": [(svc, "99213", 150, 0, [("CO", "197", 150)])]},
        {"pcn": F.claim_id(2), "last": "SAMPLE", "first": "BRAVO", "lines": [(svc, "99214", 200, 0, [("CO", "16", 200)])]},
        {"pcn": F.claim_id(3), "last": "EXAMPLE", "first": "CHARLIE", "lines": [(svc, "99213", 150, 0, [("CO", "197", 150)])]}])
    remit(s, trace=F.check_no(8), paid_on=iso(25), claims=[
        {"pcn": F.claim_id(8), "lines": [(iso(45), "99213", 150, 85, [("CO", "45", 65)], {"allowed_cents": 8500})]}])
    if fresh:
        remit(s, trace=F.check_no(4), paid_on=iso(3), claims=[
            {"pcn": F.claim_id(4), "lines": [(iso(20), "99212", 100, 70, [("CO", "45", 30)], {"allowed_cents": 7000})]}])
    rows = [charge(F.link_no(1)), pay(F.link_no(1), F.check_no(1), 0), adj(F.link_no(1), "CO-197", 150),
            charge(F.link_no(2), cpt="99214", amount=200, patient=1), pay(F.link_no(2), F.check_no(1), 0, cpt="99214", patient=1),
            adj(F.link_no(2), "CO-16", 200, cpt="99214", patient=1),
            charge(F.link_no(3), patient=2), pay(F.link_no(3), F.check_no(1), 0, patient=2),
            pay(F.link_no(3), "PAPER-3", 90, posted=ago(30), patient=2),
            F.production_row(link=F.link_no(3), reason="CO-45", contractual=60, svc=ago(70), posted=ago(30), payer=P["bcbs"], patient=2),
            charge(F.link_no(8), svc=ago(45), patient=1),
            charge(F.link_no(7), amount=100, patient=2, svc=ago(50)),
            pay(F.link_no(7), "PAPER-71", 100, patient=2, svc=ago(50), posted=ago(35)),
            pay(F.link_no(7), "PAPER-72", 60, patient=2, svc=ago(50), posted=ago(28)),
            charge(F.link_no(5), cpt="99213", amount=80, patient=1, svc=ago(40), status="Payment Pending"),
            pay(F.link_no(5), "777000555", 80, patient=1, svc=ago(40), posted=ago(5), payer=P["commercial"], primary=P["commercial"]),
            charge(F.link_no(99), cpt="99212", patient=3, svc=ago(10), posted=ago(2), status="Payment Pending")]
    if fresh:
        rows += [charge(F.link_no(4), cpt="99212", amount=100, patient=2, svc=ago(20)),
                 pay(F.link_no(4), F.check_no(4), 70, cpt="99212", patient=2, svc=ago(20), posted=ago(2))]
    ar = [F.ar_row(claim=F.claim_id(1), link=F.link_no(1), status="Denied", denial=150, current=0, aging=ago(58), svc=ago(70)),
          F.ar_row(claim=F.claim_id(5), link=F.link_no(5), status="Payment Pending", current=80, aging=ago(8), svc=ago(40), patient=1),
          F.ar_row(claim=F.claim_id(6), link=F.link_no(6), status="Payment Pending", current=-25, aging=ago(120), svc=ago(130),
                   patient=3, primary=P["medicare"])]
    lg = el.Ledger.open(os.path.join(path, "ema_ledger.sqlite"))
    try:
        p = F.write(os.path.join(path, "ar.xlsx"), F.ar_xlsx(ar, created=dt.datetime.combine(ago(3), dt.time(8, 0))))
        r = er.ingest_file(s, p, via="test", log=Log())
        assert r["status"] == "current", r
        p = F.write(os.path.join(path, "prod.csv"), F.production_csv(rows))
        r = er.ingest_file(s, p, via="test", ledger=lg, log=Log())
        assert r["status"] == "current", r
        el.refresh(s, lg, parts="AB", log=Log())
    finally:
        lg.close()
    det = Detector(s, run_id=f"r{next(_N)}", today=TODAY)
    det.run()
    assert det.ema_errors == [], det.ema_errors
    s.close()
    return db


# ------------------------------------------------------------------------------------------------ the two hosts
@pytest.fixture(scope="module")
def worlds(tmp_path_factory):
    """Built once for the module (each build runs the readers, the ledger and the Detector): a current world, and one
    whose newest remittance is 25 days old. A test that changes a world works on its own copy (copied)."""
    out = {}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(er, "CLAIM_HOST_SUFFIX", F.HOST_SUFFIX)
        mp.setenv("REMITWATCH_MODE", "server")
        mp.setenv("REMITWATCH_EMA_STRICT", "1")
        for k in ("REMITWATCH_EMA_REPORTS", "REMITWATCH_EMA_LEDGER", "REMITWATCH_EMA_CLOSES"):
            mp.delenv(k, raising=False)
        for name, fresh in (("fresh", True), ("stale", False)):
            ec._LAST_POST.clear()
            out[name] = build(str(tmp_path_factory.mktemp(name)), fresh=fresh)
    return out


def copied(worlds, tmp_path, name="fresh") -> str:
    d = tmp_path / f"w_{name}"
    d.mkdir()
    for f in ("store.sqlite", "ema_ledger.sqlite"):
        src = os.path.join(os.path.dirname(worlds[name]), f)
        if os.path.exists(src):
            shutil.copyfile(src, d / f)
    return str(d / "store.sqlite")


def _site(monkeypatch, db, *, serverless=False, tmp=None):
    """The API on this store: a server reading the store, or a serverless host reading the dashboard's copy of it, built
    as the run builds it (cloud.save_serving) into a state folder, through a fresh ServingCopy."""
    from fastapi.testclient import TestClient

    import remitwatch.api as api
    api.ensure_server_schema(db)
    monkeypatch.setattr(api, "PASSWORD", "")
    monkeypatch.setattr(api, "SHOW_PHI", False)
    monkeypatch.setattr(api, "_FAILED", {})
    monkeypatch.setattr(api, "DB_PATH", db)
    if serverless:
        # the copy is built in this test's own folder, never in the shared one another process may be building in
        monkeypatch.setenv("REMITWATCH_TMP", str(tmp / "scratch"))
        st = LocalState(str(tmp / "state"))
        conn = sqlite3.connect(db)
        try:
            cloud.save_serving(st, conn)
        finally:
            conn.close()
        monkeypatch.setenv("REMITWATCH_STATE_DIR", str(tmp / "state"))
        monkeypatch.setenv("REMITWATCH_MODE", "serverless")
        monkeypatch.setattr(cloud, "SERVING_COPY", cloud.ServingCopy())
    else:
        monkeypatch.setenv("REMITWATCH_MODE", "server")
    return TestClient(api.app)


@pytest.fixture
def server(worlds, tmp_path, monkeypatch):
    db = copied(worlds, tmp_path)
    return type("Site", (), {"c": _site(monkeypatch, db), "db": db, "tmp": tmp_path})


def rows(db, sql, *args):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def fid(db, signal, outcome=None):
    got = rows(db, "SELECT finding_id FROM findings WHERE signal=? AND (? IS NULL OR ema_outcome=?) ORDER BY finding_id", signal, outcome, outcome)
    assert len(got) == 1, got
    return got[0][0]


def rerun(db, today=TODAY):
    """The next detection run on the store (what the daily run does after a decision or a status change)."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(er, "CLAIM_HOST_SUFFIX", F.HOST_SUFFIX)
        s = Store(db)
        try:
            det = Detector(s, run_id=f"r{next(_N)}", today=today)
            det.run()
            assert det.ema_errors == [], det.ema_errors
        finally:
            s.close()


MONEY = ("denial", "bundling", "downcoding", "underpayment")


def sql_count(db, where, *args):
    """The independent count: the Worklist's scope (the four claim pages, live items) and the condition, straight from
    the findings table."""
    n, cents = rows(db, f"""SELECT COUNT(*), COALESCE(SUM(shortfall_cents),0) FROM findings WHERE COALESCE(stale,0)=0
                            AND category IN ('denial','bundling','downcoding','underpayment') AND ({where})""", *args)[0]
    return n, cents


def _list_total(c, go):
    """The count line of the Worklist this filter opens (desk.js deskTab: the four claim pages, live items), when the
    list takes the filter (api._filters names state= and ema=); None when it does not yet."""
    import inspect

    import remitwatch.api as api
    takes = inspect.signature(api._filters).parameters
    if any(k in go and k not in takes for k in ("state", "ema")):
        return None
    params = {"category": ",".join(MONEY), "live": 1, "status": go.get("status", "open,in_progress"), "deadline": go.get("deadline", "valid"),
              "limit": 1}
    for k in ("state", "ema", "signal", "kind", "payer", "handling"):
        if go.get(k):
            params[k] = go[k]
    r = c.get("/api/findings", params=params).json()
    return r["total"], r["shortfall_cents"] or 0


# --------------------------------------------------------------------------------------------------------- Today
def test_today_counts_each_state_and_each_count_is_the_list_it_opens(server):
    c, db = server.c, server.db
    t = c.get("/api/ema/today").json()
    assert t["ready"] is True and t["today"] == TODAY
    st = t["states"]
    assert st["todo"]["n"] == 2 and st["todo"]["cents"] == 9000 + 12000                 # the two written-off denials
    assert st["done"]["n"] == 1                                                          # paid on a check no 835 carries
    assert st["waiting"]["n"] == 0 and st["followup_due"]["n"] == 0
    assert {k: v["label"] for k, v in st.items()} == {"todo": "Work now", "followup_due": "Follow-ups due", "waiting": "Waiting",
                                                        "done": "Done", "accepted": "Accepted", "expired": "Expired",
                                                        "cant_check": "Can't be checked"}
    # every count is the count of the list it opens: the same filter, counted again straight from the table
    for key in ("waiting", "done", "accepted", "expired", "cant_check"):
        assert st[key]["go"] == {"state": key, "status": "all", "deadline": ""}
        assert (st[key]["n"], st[key]["cents"]) == sql_count(db, "work_state = ?", key), key
    due = "follow_up_on IS NOT NULL AND follow_up_on <= date('now')"
    assert (st["todo"]["n"], st["todo"]["cents"]) == sql_count(db, f"work_state='work_now' OR (work_state='waiting' AND {due})")
    assert (st["followup_due"]["n"], st["followup_due"]["cents"]) == sql_count(db, f"{due} AND work_state IN ('work_now','waiting')")
    aw = t["auto_written_off"]
    assert (aw["n"], aw["cents"]) == (1, 9000) and aw["go"] == {"ema": "auto_written_off", "status": "open,in_progress", "deadline": "valid",
                                                                 "sort": "shortfall"}
    assert (aw["n"], aw["cents"]) == sql_count(db, "ema_outcome='auto_written_off' AND status IN ('open','in_progress') AND appeal_deadline >= date('now')")
    un = t["unworked"]
    stored = json.loads(rows(db, "SELECT value FROM meta WHERE key='ema:unworked'")[0][0])
    assert (un["n"], un["cents"], un["data_through"]) == (stored["n"], stored["cents"], stored["data_through"]) == (2, 21000, iso(2))
    rec = json.loads(rows(db, "SELECT value FROM meta WHERE key='ema:reconcile'")[0][0])
    assert t["remits_missing"] == rec["remits_missing"] and t["remits_missing"]["ok"] is True       # the run's own judgment
    assert t["other"]["post"]["n"] == 1 and t["other"]["post"]["cents"] == 8500           # the 835 payment EMA never posted
    assert t["other"]["refund"]["n"] == 1 and t["other"]["refund"]["cents"] == 6000       # the credit EMA holds
    # and the Worklist, once it takes these filters (unit C1), lists exactly what was counted
    for x in list(st.values()) + [aw, un]:
        got = _list_total(c, x["go"])
        assert got is None or got == (x["n"], x["cents"]), (x["go"], got)


def test_the_counts_hold_with_a_list_filter_that_does_not_take_state_or_ema_yet(server, monkeypatch):
    """Before the Worklist's filter takes state= and ema= (an older api.py), Today counts with the same rule itself
    (api_ema.condition, the SQL needs_C2.md gave unit C1): the same numbers."""
    import remitwatch.api as api
    c, db = server.c, server.db
    # one item waits on the payer and its follow-up day has come: Work now holds it, Waiting holds it too
    conn = sqlite3.connect(db)
    conn.execute("UPDATE findings SET work_state='waiting', follow_up_on=? WHERE signal='DENIED_MISSING_INFO'", (iso(1),))
    conn.commit()
    conn.close()
    want = c.get("/api/ema/today").json()
    assert (want["states"]["todo"]["n"], want["states"]["followup_due"]["n"], want["states"]["waiting"]["n"]) == (2, 1, 1)
    real = api._filters

    def older(signal, category, payer, year, status, severity, actionable, q, provider, code, deadline, handling=None, extra=None,
              patient_of=None, kind=None, live=False, paid_days=0, min_dollars=0):
        return real(signal, category, payer, year, status, severity, actionable, q, provider, code, deadline, handling, extra,
                    patient_of, kind, live, paid_days, min_dollars)
    monkeypatch.setattr(api, "_filters", older)
    got = c.get("/api/ema/today").json()
    for key in want["states"]:
        assert (got["states"][key]["n"], got["states"][key]["cents"]) == (want["states"][key]["n"], want["states"][key]["cents"]), key
    for key in ("auto_written_off", "unworked"):
        assert (got[key]["n"], got[key]["cents"]) == (want[key]["n"], want[key]["cents"]), key


def test_today_follows_the_state_a_person_and_the_calendar_give(server):
    c, db = server.c, server.db
    from remitwatch.actions import apply_status
    f = fid(db, "DENIED_MISSING_INFO")
    conn = sqlite3.connect(db)
    apply_status(conn, f, "appealed", "corrected claim sent", None, "biller")
    conn.commit()
    conn.close()
    rerun(db)
    st = c.get("/api/ema/today").json()["states"]
    assert st["waiting"]["n"] == 1 and st["todo"]["n"] == 1 and st["followup_due"]["n"] == 0
    follow = rows(db, "SELECT follow_up_on FROM findings WHERE finding_id=?", f)[0][0]
    assert follow and follow >= TODAY
    # the follow-up day comes before the next run: the item is due today, and counted so, on the list it opens
    conn = sqlite3.connect(db)
    conn.execute("UPDATE findings SET follow_up_on=? WHERE finding_id=?", (iso(1), f))
    conn.commit()
    conn.close()
    st = c.get("/api/ema/today").json()["states"]
    assert st["followup_due"]["n"] == 1 and st["followup_due"]["cents"] == 12000
    assert st["todo"]["n"] == 2 and st["todo"]["cents"] == 21000      # Work now holds the follow-up due with the new work
    assert st["waiting"]["n"] == 1                     # its state stays Waiting until the next run moves it


def test_an_auto_write_off_past_its_window_is_not_still_appealable(server):
    c, db = server.c, server.db
    conn = sqlite3.connect(db)
    conn.execute("UPDATE findings SET appeal_deadline=? WHERE ema_outcome='auto_written_off'", (iso(1),))
    conn.commit()
    conn.close()
    aw = c.get("/api/ema/today").json()["auto_written_off"]
    assert (aw["n"], aw["cents"]) == (0, 0)


def test_counts_leave_out_items_the_rules_no_longer_find(server):
    c, db = server.c, server.db
    conn = sqlite3.connect(db)
    conn.execute("UPDATE findings SET stale=1 WHERE ema_outcome='auto_written_off'")
    conn.commit()
    conn.close()
    t = c.get("/api/ema/today").json()
    assert t["states"]["todo"]["n"] == 1 and t["auto_written_off"]["n"] == 0


def test_an_upper_bound_amount_is_counted_and_said_apart(server):
    """An item EMA wrote off on a line no rate prices has no shortfall; what EMA wrote off on it is capped at the charge
    less what was paid (ema_checks C2, basis charge_less_paid): an upper bound. The count keeps the item (it equals the
    list), its dollars stay out of every total, and the bound is said apart (needs_B 1.6)."""
    c, db = server.c, server.db
    f = fid(db, "DENIED_AUTH", "auto_written_off")
    conn = sqlite3.connect(db)
    ev = json.loads(conn.execute("SELECT evidence FROM findings WHERE finding_id=?", (f,)).fetchone()[0])
    ev["written_off_basis"] = "charge_less_paid"
    # as the run leaves such an item: no price, so no shortfall; the write-off capped at the charge less what was paid
    conn.execute("UPDATE findings SET evidence=?, shortfall_cents=NULL, written_off_cents=15000 WHERE finding_id=?", (json.dumps(ev), f))
    conn.commit()
    conn.close()
    t = c.get("/api/ema/today").json()
    a = t["auto_written_off"]
    assert (a["n"], a["cents"], a["upper_n"], a["upper_cents"]) == (1, 0, 1, 15000)
    assert _list_total(c, a["go"]) in (None, (1, 0))           # the list still shows the item, with no dollars
    todo = t["states"]["todo"]
    assert (todo["n"], todo["cents"], todo["upper_n"], todo["upper_cents"]) == (2, 12000, 1, 15000)
    assert _list_total(c, todo["go"]) in (None, (2, 12000))
    d = c.get("/api/ema/data").json()
    assert d["upper_bound"] == {"n": 1, "cents": 15000}
    pats = c.get("/api/ema/policy").json()
    pat = next(p for p in pats["patterns"] if p["code"] == "99213" and p["upper_n"])
    assert (pat["upper_n"], pat["upper_cents"]) == (1, 15000)
    assert pat["cents"] == sum(r["shortfall_cents"] or 0 for r in _all_items(c, pat["pattern"]))
    item = next(r for r in _all_items(c, pat["pattern"]) if r["finding_id"] == f)
    assert item["shortfall_cents"] is None and item["written_off_basis"] == "charge_less_paid" and item["written_off_cents"] == 15000


def _all_items(c, pattern):
    """Every item of one write-off pattern, page after page of GET /api/ema/policy/items."""
    out, page = [], 1
    while True:
        r = c.get("/api/ema/policy/items", params={"pattern": pattern, "page": page}).json()
        out += r["rows"]
        if page * r["size"] >= r["count"]:
            return out
        page += 1


def test_a_store_before_the_states_has_no_state_counts_and_no_error(server, monkeypatch):
    c, db = server.c, server.db
    conn = sqlite3.connect(db)
    conn.execute("ALTER TABLE findings DROP COLUMN work_state")
    conn.commit()
    conn.close()
    t = c.get("/api/ema/today").json()
    assert t["ready"] is False and t["states"] == {}


# ------------------------------------------------------------------------------------------------ the blind spot
def test_no_blind_spot_while_the_feeds_are_current(server):
    b = server.c.get("/api/ema/blind").json()
    assert b["blind"] is False and b["cents"] == 0 and b["lines"] == []
    assert [f["feed"] for f in b["feeds"]] == ["835", "ar", "production"] and not any(f["stale"] for f in b["feeds"])


def test_a_stale_remittance_feed_says_in_dollars_what_cannot_be_checked(worlds, tmp_path, monkeypatch):
    db = copied(worlds, tmp_path, "stale")
    c = _site(monkeypatch, db)
    b = c.get("/api/ema/blind").json()
    assert b["blind"] is True and b["since"] == iso(25) and b["source"] == "run"
    feed = next(f for f in b["feeds"] if f["feed"] == "835")
    assert feed == {"feed": "835", "label": "Remittances (835)", "since": iso(25), "days": 25, "stale": True}
    # the dollars: what EMA posted after the newest remittance that no 835 explains (the run's figure, meta ema:reconcile)
    rec = json.loads(rows(db, "SELECT value FROM meta WHERE key='ema:reconcile'")[0][0])
    assert rec["since_feed_stopped"] == {"checks": 1, "cents": 8000}
    assert b["cents"] == 8000 and "$80" in b["lines"][0] and "1 check" in b["lines"][0] and "cannot see" in b["lines"][0]
    assert c.get("/api/ema/today").json()["blind"]["blind"] is True


def test_a_stale_ema_report_counts_the_work_now_it_may_hide(server):
    c, db = server.c, server.db
    conn = sqlite3.connect(db)
    conn.execute("UPDATE ema_report_files SET as_of=? WHERE report='ar_report'", (iso(20),))
    conn.execute("UPDATE ema_ar SET as_of=?", (iso(20),))
    conn.commit()
    conn.close()
    b = c.get("/api/ema/blind").json()
    ar = next(f for f in b["feeds"] if f["feed"] == "ar")
    assert ar["stale"] is True and ar["days"] == 20 and b["blind"] is True
    assert b["cents"] == 21000 and "2 items" in b["lines"][0] and "may already be worked" in b["lines"][0]


def test_the_runs_own_blind_figure_wins_when_it_is_stored(server):
    c, db = server.c, server.db
    conn = sqlite3.connect(db)
    mine = {"at": "2026-01-01T00:00:00+00:00", "blind": True, "cents": 123456, "basis": "Charges with no payer answer since the feed stopped.",
            "feeds": [{"feed": "835", "since": iso(30), "days": 30, "stale": True}]}
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('selfcheck:blind', ?)", (json.dumps(mine),))
    conn.commit()
    conn.close()
    b = c.get("/api/ema/blind").json()
    assert b["source"] == "selfcheck" and b["cents"] == 123456 and b["basis"].startswith("Charges")


# ------------------------------------------------------------------------------------------------- the Data tab
def test_the_data_tab_gets_the_runs_figures_in_one_call(server):
    c, db = server.c, server.db
    d = c.get("/api/ema/data").json()
    assert d["ready"] is True and d["blind"]["blind"] is False
    ar_as_of = rows(db, "SELECT MAX(as_of) FROM ema_report_files WHERE report='ar_report'")[0][0]
    assert d["fresh"]["ar_as_of"] == ar_as_of and d["fresh"]["ar_days"] == (T - dt.date.fromisoformat(ar_as_of)).days <= 8
    assert d["fresh"]["production_as_of"] == iso(2) and d["fresh"]["newest_835"] == iso(3)
    files = d["catalog"]["files"]
    assert sorted(f["report"] for f in files) == ["ar_report", "production"] and all(f["status"] == "current" for f in files)
    assert all("origin" not in f for f in files) and {f["from"] for f in files} == {"Read on the server"}
    assert d["catalog"]["by_status"] == {"current": 2}
    # the reconcile summary is what the run stored, as it stored it
    stored = json.loads(rows(db, "SELECT value FROM meta WHERE key='ema:reconcile'")[0][0])
    assert d["reconcile"]["stored"] is True and d["reconcile"]["c1"] == stored["c1"] and d["reconcile"]["w4"] == stored["w4"]
    # the remittances someone can ask for: by payer, month and cause, with what to ask
    assert d["resend"] == [{"payer_group": "Testcare Health", "month": iso(5)[:7], "cause": "no_era", "checks": 1, "cents": 8000,
                            "why": "This payer sends no electronic remittance here",
                            "ask": "Enroll this payer's electronic remittances (ERA) with TriZetto."}]
    assert d["wiring"]["errors"] == [] and d["wiring"]["on"] is True
    assert d["policy"]["patterns"] == 2 and d["policy"]["appealable_cents"] == 21000 and d["policy"]["accepted"] == 0
    assert d["upper_bound"] == {"n": 0, "cents": 0}
    assert set(d["cause_words"]) >= {"feed_gap", "after_feed", "no_era", "no_era_va", "separate_payee", "misnumbered", "other"}


def test_checks_posted_after_the_newest_remittance_are_not_asked_for_one_by_one(server):
    """A check posted after the newest remittance is there because the feed is behind: the fix is the newest downloads,
    not a request per payer, so it is not a re-send row. It stays in the checks list and its CSV."""
    c, db = server.c, server.db
    before = c.get("/api/ema/data").json()["resend"]
    assert [r["cause"] for r in before] == ["no_era"]
    conn = sqlite3.connect(db)
    conn.execute("UPDATE ema_checks SET cause='after_feed' WHERE match='none' AND cause='no_era'")
    conn.commit()
    conn.close()
    assert c.get("/api/ema/data").json()["resend"] == []
    r = c.get("/api/ema/checks", params={"cause": "after_feed"}).json()
    assert r["count"] == 1 and r["rows"][0]["check_number"] == "777000555"
    assert "777000555" in c.get("/api/ema/checks.csv").text


def test_the_data_tab_counts_the_write_off_patterns_as_the_policy_page_does(server):
    """The Data tab's line about EMA's write-offs gives the policy page's own figures, counted now and over every
    pattern (the run stores only its 500 largest)."""
    c, db = server.c, server.db
    tab = c.get("/api/ema/policy").json()
    want = (len(tab["patterns"]), tab["totals"]["cents"], tab["totals"]["appealable_cents"], tab["totals"]["accepted"])
    pol = c.get("/api/ema/data").json()["policy"]
    assert (pol["patterns"], pol["cents"], pol["appealable_cents"], pol["accepted"]) == want == (2, 21000, 21000, 0)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE meta SET value=? WHERE key='ema:policy_summary'", (json.dumps({"at": TODAY + "T00:00:00+00:00", "patterns": []}),))
    conn.commit()
    conn.close()
    pol = c.get("/api/ema/data").json()["policy"]
    assert (pol["patterns"], pol["cents"], pol["appealable_cents"], pol["accepted"]) == want


def test_a_report_that_waits_or_is_held_says_so_on_today_with_what_to_do(server):
    c, db = server.c, server.db
    assert (c.get("/api/ema/today").json()["reports_waiting"], c.get("/api/ema/today").json()["reports_held"]) == ([], 0)
    conn = sqlite3.connect(db)
    reason = "Too big to read on this site: upload it on the Upload page (it compresses it), or put a zipped copy in the same folder."
    conn.execute("""INSERT INTO ema_report_files (file_id, report, source_name, via, status, status_reason, received_at)
                    VALUES ('waitfile', 'production', '2026 Production.csv', 'dropbox', 'waiting', ?, ?)""", (reason, iso(1)))
    conn.execute("""INSERT INTO ema_report_files (file_id, report, source_name, via, status, status_reason, received_at)
                    VALUES ('heldfile', 'ar_report', 'AR.xlsx', 'upload', 'held', 'Fewer lines than the one on file.', ?)""", (iso(1),))
    conn.commit()
    conn.close()
    t = c.get("/api/ema/today").json()
    assert t["reports_held"] == 1
    assert t["reports_waiting"] == [{"report": "production", "source_name": "2026 Production.csv", "status": "waiting", "reason": reason}]
    d = c.get("/api/ema/data").json()
    assert [f["status_reason"] for f in d["catalog"]["files"] if f["status"] == "waiting"] == [reason]


def test_a_failed_run_step_reaches_the_data_tab(server):
    c, db = server.c, server.db
    conn = sqlite3.connect(db)
    w = json.loads(conn.execute("SELECT value FROM meta WHERE key='ema:wiring'").fetchone()[0])
    w["errors"] = [{"step": "credits", "error": "OperationalError: database is locked"}]
    conn.execute("UPDATE meta SET value=? WHERE key='ema:wiring'", (json.dumps(w),))
    conn.commit()
    conn.close()
    assert c.get("/api/ema/data").json()["wiring"]["errors"][0]["step"] == "credits"
    assert c.get("/api/ema/today").json()["wiring_errors"] == 1


def test_the_catalog_shows_holds_decisions_replacements_and_files_not_read(server):
    c, db = server.c, server.db
    conn = sqlite3.connect(db)
    cur = conn.execute("SELECT file_id FROM ema_report_files WHERE report='ar_report'").fetchone()[0]
    conn.execute("""INSERT INTO ema_report_files (file_id, report, source_name, via, status, status_reason, as_of, received_at, origin)
                    VALUES ('heldfile1', 'ar_report', 'AR 2026 again.xlsx', 'upload', 'held', 'This A/R lists far fewer lines than the one on file.',
                            ?, ?, '{"path": "/data/incoming/done/x"}')""", (iso(1), iso(1)))
    conn.execute("""INSERT INTO ema_report_files (file_id, report, source_name, via, status, status_reason, decision, decided_by, decided_at, as_of, received_at)
                    VALUES ('heldfile2', 'production', 'Production week.csv', 'dropbox', 'held', 'Rows do not line up with the header.', 'apply',
                            'biller', ?, ?, ?)""", (TODAY + "T09:00:00+00:00", iso(1), iso(1)))
    conn.execute("""INSERT INTO ema_report_files (file_id, report, source_name, via, status, replaced_by, as_of, received_at)
                    VALUES ('oldfile', 'ar_report', 'AR older.xlsx', 'dropbox', 'replaced', ?, ?, ?)""", (cur, iso(40), iso(40)))
    conn.execute("INSERT INTO files (sha256, kind, source_name, ingested_at, status) VALUES ('x1', 'ema:unknown', 'notes.xlsx', ?, 'not_read')", (iso(4),))
    conn.commit()
    conn.close()
    cat = c.get("/api/ema/reports").json()
    held = {f["file_id"]: f for f in cat["held"]}
    assert set(held) == {"heldfile1", "heldfile2"} and "origin" not in held["heldfile1"]
    assert held["heldfile1"]["from"] == "Uploaded here" and held["heldfile1"]["status_reason"].startswith("This A/R")
    assert held["heldfile2"]["decided_by"] == "biller" and held["heldfile2"]["decision"] == "apply"
    by = {f["file_id"]: f for f in cat["files"]}
    assert [r["file_id"] for r in by[cur]["replaced"]] == ["oldfile"]
    assert cat["not_read"] == [{"kind": "ema:unknown", "ingested_at": iso(4), "status": "not_read", "source_name": "notes.xlsx"}]
    assert cat["by_status"] == {"current": 2, "held": 2, "replaced": 1}


def test_each_ar_bucket_lists_exactly_the_rows_it_counts(server):
    c, db = server.c, server.db
    w4 = c.get("/api/ema/data").json()["reconcile"]["w4"]
    assert w4["adds_up"] is True and w4["total"]["n"] == 3
    seen = 0
    for bucket, x in w4["buckets"].items():
        r = c.get("/api/ema/ar", params={"bucket": bucket}).json()
        assert (r["count"], r["cents"]) == (x["n"], x["cents"]), bucket
        assert len(r["rows"]) == x["n"] and sum(row["balance_cents"] for row in r["rows"]) == x["cents"], bucket
        assert r["claim_url"] == F.CLAIM_PREFIX + "{claim}"
        seen += len(r["rows"])
    assert seen == w4["total"]["n"]
    denied = c.get("/api/ema/ar", params={"bucket": "denied_todo"}).json()["rows"][0]
    assert denied["claim_id"] == F.claim_id(1) and denied["link"] == F.link_no(1) and "patient" not in json.dumps(denied).lower()
    assert c.get("/api/ema/ar", params={"bucket": "nope"}).status_code == 400
    # the A/R list never writes: the meta and the buckets are what they were
    before = rows(db, "SELECT key, value FROM meta ORDER BY key")
    c.get("/api/ema/ar", params={"bucket": "credits"})
    assert rows(db, "SELECT key, value FROM meta ORDER BY key") == before


def test_the_checks_list_adds_up_to_the_summary_and_makes_the_request(server):
    c, db = server.c, server.db
    rec = c.get("/api/ema/reconcile").json()
    total = 0
    for cause, x in rec["c1"]["causes"].items():
        r = c.get("/api/ema/checks", params={"cause": cause}).json()
        assert (r["count"], r["cents"]) == (x["checks"], x["cents"]), cause
        total += r["cents"]
    assert total == rec["c1"]["unmatched_cents"]
    every = c.get("/api/ema/checks").json()                   # the checks no 835 carries, and only those
    assert every["count"] == sum(x["checks"] for x in rec["c1"]["causes"].values()) == 4 and every["cents"] == total
    r = c.get("/api/ema/checks", params={"cause": "no_era", "month": iso(5)[:7], "payer": "Testcare Health"}).json()
    assert r["rows"] == [{"check_number": "777000555", "first_posted": iso(5), "payer": "Testcare Health", "payer_group": "Testcare Health",
                          "cents": 8000, "cause": "no_era", "claims": 1}]
    assert r["ask"].startswith("Enroll")
    assert c.get("/api/ema/checks", params={"cause": "nope"}).status_code == 400
    assert c.get("/api/ema/checks", params={"month": "2026-1"}).status_code == 400


def test_the_csvs_are_downloads_with_no_patient_names(server):
    c = server.c
    for path, header, n in (("/api/ema/checks.csv", "check_number,first_posted,last_posted,ema_payer,payer_group,amount,cause,claims", 4),
                            ("/api/ema/unposted.csv", "trace_ending,payment_date,payer,payer_group,claims,amount,kind", 1),
                            ("/api/ema/credits.csv", "claim_id,initials,service_date,code,amount,age_days,party,coverage,payer_group", 1)):
        r = c.get(path)
        assert r.status_code == 200 and r.headers["cache-control"] == "no-store", path
        assert r.headers["content-type"].startswith("text/csv") and "attachment" in r.headers["content-disposition"]
        lines = r.text.strip().splitlines()
        assert lines[0] == header and len(lines) == n + 1, (path, lines)
        for secret in F.SECRET_STRINGS:
            assert secret not in r.text, (path, secret)
    unposted = c.get("/api/ema/unposted.csv").text.splitlines()[1].split(",")
    assert unposted[1] == iso(25) and unposted[5] == "85.00" and unposted[6] == "not_posted"
    # filtered by cause and dates, as the Data tab asks for one payer's month
    r = c.get("/api/ema/checks.csv", params={"cause": "no_era", "from": iso(10), "to": TODAY})
    assert len(r.text.strip().splitlines()) == 2
    assert c.get("/api/ema/checks.csv", params={"from": "yesterday"}).status_code == 400


def test_the_unposted_file_lists_what_the_data_tab_counts(server):
    """The Data tab counts the payments EMA never posted from what the run stored; the file lists those very rows, on
    either host, even when the code that finds them changed since the run. A run that kept no rows: the live list."""
    c, db = server.c, server.db
    stored = json.loads(rows(db, "SELECT value FROM meta WHERE key='ema:unposted'")[0][0])
    assert len(stored["rows"]) == stored["n"] + stored["short_n"] == 1
    live = c.get("/api/ema/unposted.csv").text
    # a run that found one more (as the run writes it: the list, and the same figures in its reconcile summary)
    stored["rows"].append(["9999", iso(30), "Other Payer", "Other Payer", 1, "12.00", "short"])
    stored["short_n"] += 1
    rec = json.loads(rows(db, "SELECT value FROM meta WHERE key='ema:reconcile'")[0][0])
    rec["c4"] = {k: v for k, v in stored.items() if k != "rows"}
    conn = sqlite3.connect(db)
    conn.execute("UPDATE meta SET value=? WHERE key='ema:unposted'", (json.dumps(stored),))
    conn.execute("UPDATE meta SET value=? WHERE key='ema:reconcile'", (json.dumps(rec),))
    conn.commit()
    conn.close()
    lines = c.get("/api/ema/unposted.csv").text.strip().splitlines()
    assert len(lines) - 1 == stored["n"] + stored["short_n"] == 2 and lines[-1] == f"9999,{iso(30)},Other Payer,Other Payer,1,12.00,short"
    c4 = c.get("/api/ema/data").json()["reconcile"]["c4"]                # what the Data tab counts
    assert c4["n"] + c4["short_n"] + (c4.get("unfunded_n") or 0) == len(lines) - 1
    del stored["rows"]
    conn = sqlite3.connect(db)
    conn.execute("UPDATE meta SET value=? WHERE key='ema:unposted'", (json.dumps(stored),))
    conn.commit()
    conn.close()
    assert c.get("/api/ema/unposted.csv").text == live


def test_a_csv_cell_that_could_start_a_formula_is_quoted(server):
    c, db = server.c, server.db
    conn = sqlite3.connect(db)
    conn.execute("UPDATE ema_checks SET payer='=HYPERLINK(\"x\")' WHERE check_no='777000555'")
    conn.commit()
    conn.close()
    assert "'=HYPERLINK" in c.get("/api/ema/checks.csv").text


def test_the_pages_answer_before_any_ema_report_arrived(tmp_path, monkeypatch):
    """A new site: no EMA table yet. Every route answers, says it has nothing, and writes nothing."""
    from remitwatch.store import Store as S
    db = str(tmp_path / "empty.sqlite")
    S(db).close()
    c = _site(monkeypatch, db)
    assert c.get("/api/ema/data").json() == {"ready": False}
    t = c.get("/api/ema/today").json()
    assert t["blind"]["blind"] is False and t["remits_missing"] is None
    assert c.get("/api/ema/policy").json()["patterns"] == []
    assert c.get("/api/ema/reports").json()["files"] == []
    assert c.get("/api/ema/checks").json()["count"] == 0
    assert c.get("/api/ema/ar", params={"bucket": "waiting"}).json()["count"] == 0


# ------------------------------------------------------------------------------------- the owner's write-off policy
P_AUTH, P_INFO = f"{BCBS}|99213||CO-197", f"{BCBS}|99214||CO-16"


def test_the_policy_table_counts_each_pattern_now_and_lists_its_items(server):
    c, db = server.c, server.db
    t = c.get("/api/ema/policy").json()
    pats = {p["pattern"]: p for p in t["patterns"]}
    assert [p["pattern"] for p in t["patterns"]] == [P_INFO, P_AUTH]        # most still appealable first
    a = pats[P_AUTH]
    assert (a["payer_group"], a["code"], a["modifier"], a["reason"]) == (BCBS, "99213", "", "CO-197")
    assert (a["items"], a["cents"], a["appealable"], a["appealable_cents"], a["auto"], a["at_posting"]) == (1, 9000, 1, 9000, 1, 1)
    assert a["decision"] == "pursue" and a["decided_by"] is None and a["pending"] is False
    # the archive's later-paid share is the run's (meta ema:policy_summary): of the two zero-paid 99213 denials, one paid later
    stored = {x["pattern"]: x for x in json.loads(rows(db, "SELECT value FROM meta WHERE key='ema:policy_summary'")[0][0])["patterns"]}
    assert a["later_paid_share"] == stored[P_AUTH]["later_paid_share"] == 0.5
    assert "authorization" in a["reason_text"].lower()
    i = pats[P_INFO]
    assert (i["items"], i["cents"], i["auto"], i["at_posting"]) == (1, 12000, 0, 1)
    assert t["totals"] == {"items": 2, "cents": 21000, "appealable": 2, "appealable_cents": 21000, "legal_only": 0, "legal_only_cents": 0,
                           "accepted": 0, "decide": {"patterns": 0, "items": 0, "cents": 0},
                           "contractual": {"patterns": 0, "items": 0, "written_off_cents": 0}}
    assert {p["kind"] for p in t["patterns"]} == {"write_off"} and all(p["basis"] for p in t["patterns"])
    # a pattern's count is its list
    for p in t["patterns"]:
        r = c.get("/api/ema/policy/items", params={"pattern": p["pattern"]}).json()
        assert (r["count"], r["cents"]) == (p["items"], p["cents"]) and len(r["rows"]) == p["items"]
        for row in r["rows"]:
            assert json.loads(rows(db, "SELECT evidence FROM findings WHERE finding_id=?", row["finding_id"])[0][0])["writeoff_pattern"] == p["pattern"]
            assert row["state_text"] == "Work now" and row["outcome_text"]
            assert not ({"patient", "patient_last", "member_id"} & set(row))
    assert c.get("/api/ema/policy/items", params={"pattern": "nobody|00000||CO-1"}).json()["count"] == 0


def test_accepting_a_pattern_takes_its_items_off_the_queue_at_the_next_run_and_pursue_brings_them_back(server):
    c, db = server.c, server.db
    r = c.post("/api/ema/policy", json={"pattern": P_AUTH, "decision": "accept", "name": "  Owner   Test "}, headers=XHR)
    assert r.status_code == 200 and r.json()["decision"] == "accept" and r.json()["by"] == "Owner Test (anonymous)"
    pol = json.loads(rows(db, "SELECT value FROM meta WHERE key='ema:writeoff_policy'")[0][0])
    assert pol[P_AUTH]["decision"] == "accept" and pol[P_AUTH]["by"] == "Owner Test (anonymous)" and pol[P_AUTH]["at"]
    p = next(x for x in c.get("/api/ema/policy").json()["patterns"] if x["pattern"] == P_AUTH)
    assert p["decision"] == "accept" and p["pending"] is True and p["decided_by"] == "Owner Test (anonymous)"
    assert c.get("/api/ema/today").json()["auto_written_off"]["n"] == 1             # the run moves the items, not the click
    rerun(db)
    f = fid(db, "DENIED_AUTH", "written_off_by_policy")
    assert rows(db, "SELECT work_state, status FROM findings WHERE finding_id=?", f)[0] == ("accepted", "open")
    t = c.get("/api/ema/today").json()
    assert t["auto_written_off"]["n"] == 0 and t["states"]["accepted"]["n"] == 1 and t["states"]["todo"]["n"] == 1
    p = next(x for x in c.get("/api/ema/policy").json()["patterns"] if x["pattern"] == P_AUTH)
    assert (p["items"], p["by_policy"], p["decision"], p["pending"]) == (1, 1, "accept", False)     # still findable, still listed
    assert c.get("/api/ema/policy/items", params={"pattern": P_AUTH}).json()["rows"][0]["state_text"] == "Accepted"
    c.post("/api/ema/policy", json={"pattern": P_AUTH, "decision": "pursue"}, headers=XHR)
    rerun(db)
    assert fid(db, "DENIED_AUTH", "auto_written_off")
    assert c.get("/api/ema/today").json()["auto_written_off"]["n"] == 1


def test_a_policy_decision_is_refused_when_it_cannot_be_recorded(server):
    c = server.c
    assert c.post("/api/ema/policy", json={"pattern": P_AUTH, "decision": "accept"}).status_code == 400          # no XHR header
    assert c.post("/api/ema/policy", json={"pattern": P_AUTH, "decision": "maybe"}, headers=XHR).status_code == 400
    assert c.post("/api/ema/policy", json={"pattern": "not a pattern", "decision": "accept"}, headers=XHR).status_code == 400
    assert c.post("/api/ema/policy", json={"pattern": "x" * 300, "decision": "accept"}, headers=XHR).status_code == 400
    r = c.post("/api/ema/policy", json={"pattern": "Nobody|00000||CO-1", "decision": "accept"}, headers=XHR)
    assert r.status_code == 404 and "No item" in r.json()["detail"]
    assert rows(server.db, "SELECT COUNT(*) FROM meta WHERE key='ema:writeoff_policy'")[0][0] == 0


def test_a_pattern_decided_before_keeps_its_row_when_its_items_are_gone(server):
    c, db = server.c, server.db
    conn = sqlite3.connect(db)
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('ema:writeoff_policy', ?)",
                 (json.dumps({"Old Payer|11102||CO-50": {"decision": "accept", "by": "owner", "at": "2026-01-02T00:00:00+00:00"}}),))
    conn.commit()
    conn.close()
    p = next(x for x in c.get("/api/ema/policy").json()["patterns"] if x["pattern"] == "Old Payer|11102||CO-50")
    assert (p["items"], p["decision"], p["decided_by"]) == (0, "accept", "owner")
    r = c.post("/api/ema/policy", json={"pattern": "Old Payer|11102||CO-50", "decision": "pursue"}, headers=XHR)
    assert r.status_code == 200


# ------------------------------------------------------------------------------------------- both hosts the same
def _same(a, b, path=""):
    """Both hosts' answers, field by field, but for the few that say which host answered."""
    if isinstance(a, dict) and isinstance(b, dict):
        assert set(a) == set(b), (path, set(a) ^ set(b))
        for k in a:
            if k in ("at", "built", "switch", "reads"):       # when, and which reports this host reads (its own setting)
                continue
            _same(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, list) and isinstance(b, list):
        assert len(a) == len(b), path
        for i, (x, y) in enumerate(zip(a, b)):
            _same(x, y, f"{path}[{i}]")
    else:
        assert a == b, (path, a, b)


GETS = ["/api/ema/today", "/api/ema/blind", "/api/ema/data", "/api/ema/reconcile", "/api/ema/reports", "/api/ema/policy",
        "/api/ema/checks", "/api/ema/checks?cause=no_era", f"/api/ema/policy/items?pattern={P_AUTH}"]


def test_the_serverless_host_shows_what_the_server_shows(worlds, tmp_path, monkeypatch):
    db = copied(worlds, tmp_path)
    srv = _site(monkeypatch, db)
    got = {p: srv.get(p).json() for p in GETS}
    buckets = [b for b, x in got["/api/ema/data"]["reconcile"]["w4"]["buckets"].items() if x["n"]]
    got.update({f"/api/ema/ar?bucket={b}": srv.get("/api/ema/ar", params={"bucket": b}).json() for b in buckets})
    csvs = {p: srv.get(p).text for p in ("/api/ema/checks.csv", "/api/ema/unposted.csv", "/api/ema/credits.csv")}
    (tmp_path / "vercel").mkdir()
    cloud_c = _site(monkeypatch, db, serverless=True, tmp=tmp_path / "vercel")
    for p, want in got.items():
        r = cloud_c.get(p)
        assert r.status_code == 200, p
        _same(want, r.json(), p)
    for p, want in csvs.items():
        assert cloud_c.get(p).text == want, p
    # the copy is read, never written: the same bytes before and after every page
    before = open(cloud.SERVING_COPY.path, "rb").read()
    for p in got:
        cloud_c.get(p)
    assert open(cloud.SERVING_COPY.path, "rb").read() == before


def test_a_decision_on_the_serverless_host_goes_through_the_journal_and_reaches_the_store(worlds, tmp_path, monkeypatch):
    db = copied(worlds, tmp_path)
    c = _site(monkeypatch, db, serverless=True, tmp=tmp_path)
    r = c.post("/api/ema/policy", json={"pattern": P_AUTH, "decision": "accept", "name": "Owner"}, headers=XHR)
    assert r.status_code == 200 and r.json()["applies"] == "next run"
    st = LocalState(str(tmp_path / "state"))
    entries = cloud.read_actions(st)
    assert len(entries) == 1
    a = entries[0][1]
    assert {k: a[k] for k in ("kind", "pattern", "decision", "actor")} == {"kind": "ema_policy", "pattern": P_AUTH, "decision": "accept",
                                                                          "actor": "Owner (anonymous)"} and a["at"]
    # the page shows the decision at once (the journal is laid over this instance's copy) ...
    p = next(x for x in c.get("/api/ema/policy").json()["patterns"] if x["pattern"] == P_AUTH)
    assert (p["decision"], p["decided_by"], p["pending"]) == ("accept", "Owner (anonymous)", True)
    # ... and the next run folds it into the store, where the run reads it
    conn = sqlite3.connect(db)
    done, left, _wrote = cloud.apply_actions(conn, entries)
    pol = json.loads(conn.execute("SELECT value FROM meta WHERE key='ema:writeoff_policy'").fetchone()[0])
    conn.close()
    assert done and not left and pol[P_AUTH]["decision"] == "accept" and pol[P_AUTH]["at"] == a["at"]


# ------------------------------------------------------------------------------------- the page scripts, in node
# The page's own code (index.html's helpers, features.js, today.js, desk.js, data.js, policy.js) in node, on a small
# stand-in for the page, fed with what the real routes answered on the invented world: what each page draws, and what a
# click opens or sends. Usage: node <this file> <static dir> <answers.json>.
PAGES_JS = r"""
const fs = require('fs'), vm = require('vm'), path = require('path');
const read = f => fs.readFileSync(path.join(process.argv[2], f), 'utf8');
const answers = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const html = read('index.html');
const grab = (src, re) => { const m = src.match(re); if (!m) throw new Error('not found: ' + re); return m[0]; };
const helpers = [/const money = [^\n]*\n/, /const money2 = [^\n]*\n/, /const esc = [^\n]*\n/, /function qs\([^\n]*\n/].map(re => grab(html, re)).join('\n');
// A stand-in page. A root element holds its HTML as text; the elements in it are its tags, each with the range it spans,
// matched by tag, #id, .class, [attr] and [attr="value"], with descendant selectors. Setting an element's innerHTML (or
// removing it) rewrites that range of its root's HTML: the elements outside the range stay the same objects, as in a
// browser, so their handlers stay bound, and the root's innerHTML shows what every slot was filled with.
const VOID = new Set(['input', 'br', 'img', 'hr', 'meta', 'link', 'col', 'source', 'wbr', 'area', 'base', 'embed', 'param', 'track']);
const scrolled = [];
function closeOf(h, tag, from) {
  const re = new RegExp('<(/?)' + tag + '(?=[\\s>/])[^>]*>', 'gi'); re.lastIndex = from; let depth = 1, m;
  while ((m = re.exec(h))) { if (m[1]) { if (--depth === 0) return { index: m.index, len: m[0].length }; } else if (!/\/>$/.test(m[0])) depth++; }
  return null;
}
class El {
  constructor(tag, attrs, root) {
    this.tag = tag; this.attrs = attrs || {}; this.root = root || null; this.dataset = {};
    for (const [k, v] of Object.entries(this.attrs)) if (k.startsWith('data-')) this.dataset[k.slice(5).replace(/-([a-z])/g, (m, c) => c.toUpperCase())] = v;
    this.id = this.attrs.id || ''; this.cls = (this.attrs.class || '').split(/\s+/).filter(Boolean); this.hidden = 'hidden' in this.attrs;
    this.disabled = 'disabled' in this.attrs; this.value = this.attrs.value === undefined ? '' : this.attrs.value; this.textContent = '';
    this._html = ''; this.kids = root ? null : []; this.start = this.innerStart = this.innerEnd = this.end = 0;
    this.classList = { contains: c => this.cls.includes(c) };
  }
  get top() { return this.root || this; }
  _kids() { const t = this.top; if (t.kids === null) t.kids = t._scan(0, t._html.length); return t.kids; }
  _scan(from, to) {
    const h = this.top._html, re = /<([a-zA-Z][\w-]*)((?:\s+[\w-]+(?:="[^"]*")?)*)\s*\/?>/g, out = []; re.lastIndex = from; let m;
    while ((m = re.exec(h)) && m.index < to) {
      const attrs = {}; for (const a of m[2].matchAll(/([\w-]+)(?:="([^"]*)")?/g)) attrs[a[1]] = a[2] === undefined ? '' : a[2];
      const e = new El(m[1].toLowerCase(), attrs, this.top); e.start = m.index; e.innerStart = m.index + m[0].length;
      const c = VOID.has(e.tag) || m[0].endsWith('/>') ? null : closeOf(h, e.tag, e.innerStart);
      e.innerEnd = c ? c.index : e.innerStart; e.end = c ? c.index + c.len : e.innerStart; out.push(e);
    }
    return out;
  }
  _shift(a, b, d) {   // the range [a, b) of the root's HTML became d characters longer: move what is after it, stretch what holds it
    return this._kids().filter(e => !(e.start >= a && e.start < b)).map(e => {
      if (e.start >= b) { e.start += d; e.innerStart += d; e.innerEnd += d; e.end += d; } else if (e.end > a) { e.innerEnd += d; e.end += d; }
      return e; });
  }
  set innerHTML(h) {
    h = String(h);
    if (!this.root) { this._html = h; this.kids = null; return; }
    const t = this.root; if (!this._kids().includes(this)) return;       // no longer on the page
    const a = this.innerStart, b = this.innerEnd, keep = this._shift(a, b, h.length - (b - a));
    t._html = t._html.slice(0, a) + h + t._html.slice(b);
    t.kids = keep.concat(t._scan(a, a + h.length)).sort((x, y) => x.start - y.start);
  }
  get innerHTML() { return this.root ? this.root._html.slice(this.innerStart, this.innerEnd) : this._html; }
  remove() {
    if (!this.root || !this._kids().includes(this)) return;
    const t = this.root, a = this.start, b = this.end, keep = this._shift(a, b, -(b - a));
    t._html = t._html.slice(0, a) + t._html.slice(b); t.kids = keep;
  }
  get isConnected() { return !this.root || this._kids().includes(this); }
  get firstElementChild() { return this._kids().find(e => e !== this && e.start >= (this.root ? this.innerStart : 0) && e.end <= (this.root ? this.innerEnd : this._html.length)) || null; }
  _ancestors() { return this._kids().filter(e => e !== this && e.start < this.start && e.end >= this.end && e.innerEnd >= this.end).sort((x, y) => y.start - x.start); }
  matches(sel) {
    const bare = sel.replace(/\[[^\]]*\]/g, ''), tag = (bare.match(/^[a-zA-Z][\w-]*/) || [''])[0].toLowerCase(), id = (bare.match(/#([\w-]+)/) || [])[1];
    const cls = [...bare.matchAll(/\.([\w-]+)/g)].map(x => x[1]), at = [...sel.matchAll(/\[([\w-]+)(?:="([^"]*)")?\]/g)];
    return (!tag || tag === this.tag) && (!id || id === this.id) && cls.every(c => this.cls.includes(c))
      && at.every(([, k, v]) => k in this.attrs && (v === undefined || this.attrs[k] === v));
  }
  _chain(chain) {
    if (!this.matches(chain[chain.length - 1])) return false;
    let i = chain.length - 2;
    for (const a of this._ancestors()) { if (i < 0) break; if (a.matches(chain[i])) i--; }
    return i < 0;
  }
  querySelectorAll(sel) {
    const lo = this.root ? this.innerStart : 0, hi = this.root ? this.innerEnd : this._html.length;
    const chains = sel.split(',').map(x => x.trim().split(/\s*>\s*|\s+/).filter(Boolean));
    return this._kids().filter(e => e !== this && e.start >= lo && e.end <= hi && chains.some(c => e._chain(c)));
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  closest(sel) { if (this.matches(sel)) return this; return this._ancestors().find(a => a.matches(sel)) || null; }
  scrollIntoView() { scrolled.push(this.id || this.tag); }
  focus() {}
}
const calls = [], went = [], dialogs = [];
const later = ms => new Promise(r => setTimeout(r, ms));
const answer = async p => {
  // a key 'prefix|part|part' matches a path that starts with the prefix and holds each part
  const keys = Object.keys(answers).filter(k => { const [pre, ...need] = k.split('|');
    return (p === pre || p.startsWith(pre.endsWith('?') ? pre : pre + '?')) && need.every(x => p.includes(x)); })
    .sort((a, b) => b.length - a.length);
  const key = keys[0];
  const slow = Object.entries(answers.__delay || {}).find(([k]) => p.startsWith(k));
  if (slow) await later(slow[1]);
  if ((answers.__fail || []).some(k => p.startsWith(k))) { const e = new Error('Internal Server Error'); e.status = 500; throw e; }
  if (key === undefined) { const e = new Error('no answer for ' + p); e.status = 404; throw e; }
  return JSON.parse(JSON.stringify(answers[key]));
};
const ctx = { console, Date, Math, JSON, Promise, Set, Map, URLSearchParams, setTimeout: f => 0, Number, String, Object, Array, RegExp, encodeURIComponent,
  state: { tab: 'today', filt: {}, page: {}, meta: answers['/api/meta'] }, location: { hash: '' },
  api: async (p, o) => { calls.push({ path: p, body: o && o.body ? JSON.parse(o.body) : null, method: o && o.method || 'GET' }); return answer(p); },
  render: async () => { went.push({ tab: ctx.state.tab, hash: ctx.location.hash, anchor: ctx.state.dataAnchor || null, filt: ctx.state.filt.worklist ? JSON.parse(JSON.stringify(ctx.state.filt.worklist)) : null }); },
  openDetail: id => went.push({ open: id, worklist: ctx.state.worklist ? ctx.state.worklist.slice() : null, total: ctx.state.wlTotal == null ? null : ctx.state.wlTotal }),
  localStorage: { getItem: () => '1', setItem() {}, removeItem() {} },
  confirm: m => { dialogs.push(['confirm', m]); return answers.__confirm !== false; }, prompt: (q, d) => { dialogs.push(['prompt', q]); return 'Owner Test'; },
  alert: m => calls.push({ alert: m }), navigator: {},
  document: { querySelector: () => null, querySelectorAll: () => [] }, window: answers.__narrow ? { innerWidth: 390 } : {} };
vm.createContext(ctx);
for (const src of [helpers, read('features.js'), read('today.js'), read('desk.js'), read('data.js'), read('policy.js')]) vm.runInContext(src, ctx);
const checksAt = [];
ctx.dataChecks = async el => { checksAt.push(Date.now()); if (el) el.innerHTML = '<div class="panel"><h2>Can the numbers be trusted?</h2></div>'; };
ctx.feedPanel = async () => {};
const out = {};
const click = async el => { if (!el) throw new Error('nothing to click'); await el.onclick({ preventDefault() {} }); await new Promise(r => setImmediate(r)); };
const ids = (v, sel) => v.querySelectorAll(sel + ' [data-id]').map(e => e.dataset.id);
(async () => {
  out.kind_styles = JSON.parse(JSON.stringify(vm.runInContext('KIND_STYLE', ctx)));
  let v = new El('main');
  const t0 = Date.now(), tdRun = ctx.todayTab(v);
  if (answers.__delay) { await later(150); out.today_early = v.innerHTML; out.today_early_ms = Date.now() - t0; }
  await tdRun;
  out.today_html = v.innerHTML;
  out.today_tiles = v.querySelectorAll('[data-td]').map(e => e.dataset.td);
  out.today_score = v.querySelectorAll('[data-score]').map(e => e.dataset.score);
  out.today_titles = Object.fromEntries(v.querySelectorAll('[data-td]').map(e => [e.dataset.td, e.attrs.title || '']));
  out.today_done = v.querySelectorAll('.step.is-done').length;
  const clicks = {};
  for (const key of out.today_tiles) { went.length = 0; ctx.state.dataAnchor = null; await click(v.querySelector(`[data-td="${key}"]`)); clicks[key] = went.slice(); }
  out.today_clicks = clicks;
  out.today_links = v.querySelectorAll('.go-data').map(e => e.dataset.anchor || null);
  // Results' top: the money scoreboard and the items by state (Today's tiles until the premium pass: static/today.js tdBoard)
  const bd = new El('div'); await ctx.tdBoard(bd);
  out.board_html = bd.innerHTML;
  out.board_tiles = bd.querySelectorAll('[data-td]').map(e => e.dataset.td);
  out.board_score = bd.querySelectorAll('[data-score]').map(e => e.dataset.score);
  out.board_titles = Object.fromEntries(bd.querySelectorAll('[data-td]').map(e => [e.dataset.td, e.attrs.title || '']));
  const bclicks = {};
  for (const key of out.board_tiles) { went.length = 0; ctx.state.dataAnchor = null; await click(bd.querySelector(`[data-td="${key}"]`)); bclicks[key] = went.slice(); }
  out.board_clicks = bclicks;
  went.length = 0; await click(bd.querySelector('[data-score="lost"]')); out.score_click = went.slice(); out.results_open = ctx.state.resultsOpen;
  ctx.state.dataAnchor = null;
  v = new El('main'); calls.length = 0; dialogs.length = 0;
  const d0 = Date.now(), dRun = ctx.dataTab(v);
  if (answers.__delay) { await later(150); out.data_early = v.innerHTML; out.checks_ms = checksAt.length ? checksAt[checksAt.length - 1] - d0 : null; }
  await dRun;
  out.data_html = v.innerHTML;
  out.data_calls = calls.map(c => c.path);
  out.data_links = v.querySelectorAll('#dtEmaLine .go-data').map(e => e.dataset.anchor || null);
  out.buckets = v.querySelectorAll('.dtBucket').map(e => e.dataset.bucket);
  went.length = 0; calls.length = 0;
  await click(v.querySelector('.dtBucket'));
  out.bucket_calls = calls.map(c => c.path);
  out.ar_rows_html = (v.querySelector('#dtArRows') || {}).innerHTML || '';
  out.request = await ctx.dtRequestText(answers['/api/ema/data'].resend[0]);
  calls.length = 0;
  const held = v.querySelector('[data-decide="apply"]');
  out.decide_button = held ? held.dataset.file : null;
  out.decide_primary = held ? held.cls.includes('primary') : null;
  if (held) { dialogs.length = 0; calls.length = 0; await click(held); out.apply_dialogs = dialogs.map(d => d[0]); out.apply_words = dialogs.map(d => d[1]).join(' ');
    out.apply_calls = calls.filter(c => c.method === 'POST').map(c => c.path); }
  out.file_rows = v.querySelector('tbody#dtFiles') ? 'table' : (v.querySelector('div#dtFiles') ? 'list' : null);
  out.file_items = v.querySelectorAll('#dtFiles div.check').length;
  out.ask_buttons = v.querySelectorAll('.dtAsk').length;
  // a link from Today lands on its list: the list opens, the page scrolls to it
  out.landing = {};
  for (const [anchor, key] of [['dtPostList', 'post'], ['dtRefundList', 'refund']]) {
    v = new El('main'); scrolled.length = 0; went.length = 0; ctx.state.dataAnchor = anchor;
    await ctx.dataTab(v);
    const row = v.querySelector(`#${anchor} .row[data-id]`);
    if (row) row.onclick();
    out.landing[key] = { ids: ids(v, '#' + anchor), scrolled: scrolled.slice(), html: (v.querySelector('#' + anchor) || {}).innerHTML || '', opened: went.slice() };
  }
  ctx.state.dataAnchor = null;
  v = new El('main'); calls.length = 0; dialogs.length = 0;
  await ctx.policyTab(v);
  out.policy_html = v.innerHTML;
  out.policy_buttons = v.querySelectorAll('[data-dec]').map(e => [e.dataset.i, e.dataset.dec, e.attrs['aria-pressed']]);
  out.policy_cards = v.querySelectorAll('.lab-card').length;
  out.policy_find_f = (v.querySelector('#polFind') || { attrs: {} }).attrs['data-f'] || null;
  calls.length = 0; went.length = 0; dialogs.length = 0;
  const nameBox = v.querySelector('#polName'); if (nameBox) nameBox.value = 'Owner Test';
  const f0 = v.querySelector('#polFind'); if (f0) { f0.value = answers.__find || ''; f0.oninput(); }
  out.policy_shown_before = v.querySelectorAll('#polTable [data-find]').filter(e => !e.hidden).length;
  await click(v.querySelector('[data-dec="accept"][data-i="1"]'));
  out.accept_calls = calls.filter(c => c.method === 'POST');
  out.accept_went = went.slice();
  out.accept_dialogs = dialogs.map(d => d[0]);
  out.policy_after = v.innerHTML;
  out.policy_find_after = (v.querySelector('#polFind') || {}).value;
  out.policy_shown_after = v.querySelectorAll('#polTable [data-find]').filter(e => !e.hidden).length;
  const pur = v.querySelector('[data-dec="pursue"][data-i="1"]');
  dialogs.length = 0; calls.length = 0;
  if (pur) { await click(pur); out.pursue_dialogs = dialogs.map(d => d[0]); out.pursue_calls = calls.filter(c => c.method === 'POST').length; }
  calls.length = 0;
  const cell = new El('td');
  await ctx.polItems(cell, answers['/api/ema/policy'].patterns[1], 1);
  out.items_html = cell.innerHTML;
  went.length = 0;
  const row = cell.querySelector('.row[data-id]'); if (row) row.onclick(); out.items_open = went.slice().map(w => ({ open: w.open }));
  out.items_walk = went.length ? { worklist: went[0].worklist, total: went[0].total } : null;
  if (answers.__caps) {             // the long lists: the first rows, the rest on request, the filter over all of them
    const caps = out.caps = {};
    v = new El('main');
    await ctx.dataTab(v);
    caps.asks = v.querySelectorAll('.dtAsk').length;
    await click(v.querySelector('#dtAllAsk'));
    caps.asks_after = (v.querySelector('#dtAskList').innerHTML.match(/class="small dtAsk"/g) || []).length;
    v = new El('main');
    await ctx.policyTab(v);
    const shown = () => v.querySelectorAll('[data-find]').filter(e => !e.hidden).length;
    caps.pol_shown = shown();
    const f = v.querySelector('#polFind');
    f.value = 'payer 44'; f.oninput(); caps.pol_found = shown();
    f.value = ''; f.oninput(); caps.pol_cleared = shown();
    await click(v.querySelector('#polAll')); caps.pol_all = shown();
  }
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@pytest.fixture
def page_run(worlds, tmp_path, monkeypatch):
    """What the page scripts draw and do on the answers the real routes give on the invented world (a held report and a
    scoreboard from unit R's route shape are added to those answers)."""
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    db = copied(worlds, tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("""INSERT INTO ema_report_files (file_id, report, source_name, via, status, status_reason, as_of, received_at)
                    VALUES ('heldfile1', 'ar_report', 'AR again.xlsx', 'upload', 'held', 'This A/R lists far fewer lines than the one on file.', ?, ?)""",
                 (iso(1), iso(1)))
    conn.commit()
    conn.close()
    c = _site(monkeypatch, db)

    def run(extra=None):
        if callable(extra):                 # answers worked out on this world's own site
            extra = extra(c)
        answers = {p: c.get(p).json() for p in ("/api/meta", "/api/brief", "/api/ema/today", "/api/ema/exports", "/api/ema/data",
                                                "/api/ema/policy", "/api/coverage", "/api/sync/status", "/api/alerts")}
        answers["/api/results/scoreboard"] = {"recovered_cents": 123400, "lost_cents": None, "at_risk_cents": 9000, "as_of": TODAY, "weeks": []}
        d = answers["/api/ema/data"]
        r0 = d["resend"][0]
        answers["/api/ema/checks?"] = c.get("/api/ema/checks", params={"payer": r0["payer_group"], "month": r0["month"], "cause": r0["cause"]}).json()
        answers["/api/ema/ar?"] = c.get("/api/ema/ar", params={"bucket": next(b for b, x in d["reconcile"]["w4"]["buckets"].items() if x["n"])}).json()
        answers["/api/ema/policy/items?"] = c.get("/api/ema/policy/items", params={"pattern": answers["/api/ema/policy"]["patterns"][1]["pattern"]}).json()
        answers["/api/ema/policy"] = answers["/api/ema/policy"]
        # the two lists the Data tab opens (the payments to post, the credits to refund), as the real route answers them
        for key, item in (d.get("items") or {}).items():
            answers[f"/api/findings?|signal={item['go']['signal']}"] = c.get("/api/findings", params=dict(item["go"], live=1, limit=25)).json()
        answers.update(extra or {})
        f = tmp_path / "answers.json"
        f.write_text(json.dumps(answers), encoding="utf-8")
        script = tmp_path / "pages.js"
        script.write_text(PAGES_JS, encoding="utf-8")
        static = os.path.join(os.path.dirname(cloud.__file__), "static")
        res = subprocess.run(["node", str(script), static, str(f)], capture_output=True, text=True, timeout=120)
        assert res.returncode == 0, res.stderr[-3000:]
        return json.loads(res.stdout), answers
    return run


def test_today_draws_the_counts_the_routes_give_and_each_opens_its_list(page_run):
    """The counts by state and the scoreboard are Results' board now (the premium pass moved Today's nine tiles there
    without losing a figure): each tile shows its route's number and opens the list it was counted with. Today keeps
    what is not on today's list, the payments to post and the credits to refund."""
    out, ans = page_run()
    t = ans["/api/ema/today"]
    assert out["board_tiles"][:5] == ["todo", "followup_due", "waiting", "auto_written_off", "unworked"]
    b, h = out["board_html"], out["today_html"]
    # each tile shows its route's number, and opens the Worklist with the very filter that number was counted with
    for key in ("todo", "followup_due", "waiting"):
        assert out["board_clicks"][key][-1]["tab"] == "worklist"
        # no order forced: the Worklist's own (worth most first), so Item 1 is the same item whichever door opened it
        want = dict({"status": "open,in_progress", "deadline": "valid"}, **t["states"][key]["go"])
        assert out["board_clicks"][key][-1]["filt"] == want, key
        assert out["today_clicks"].get(key, [{}])[-1].get("filt") in (None, want), key   # Today's strip opens the same lists
    aw = t["auto_written_off"]
    assert out["board_clicks"]["auto_written_off"][-1]["filt"] == dict({"status": "open,in_progress", "deadline": "valid"}, **aw["go"])
    assert 'data-td="todo"' in b and ">2<small>$210 est.</small>" in b.replace("\n", "")
    assert "Auto write-offs still appealable" in b and "Nobody has worked these" in b
    assert 'class="tile' not in h and 'data-score' not in h                     # none of the nine is left on Today
    assert "Not on today's list" in h and ">1 done</a>" in h
    assert "1 payment</a>" not in h and "1 payment to post in EMA" in h and "1 credit to refund" in h
    assert [(out["today_clicks"][k][-1]["tab"], out["today_clicks"][k][-1]["anchor"]) for k in ("post", "refund")] == \
        [("data", "dtPostList"), ("data", "dtRefundList")]
    # the scoreboard: a figure the route gives, and a dash (never a zero) for one it does not
    assert out["board_score"] == ["recovered", "lost", "at_risk", "given_up"]
    assert "$1,234" in b and b.count(">–</span>") >= 2
    assert out["score_click"][-1]["tab"] == "results" and out["results_open"] == "lost"
    assert "Blind spot" not in h                       # the feeds are current
    # every work type has its style, the two unit B added included (the stylesheet, static/rw.css, holds the classes
    # since the pages' inline <style> blocks moved there: the same guarantee, read where the styles live now)
    rw_css = open(os.path.join(os.path.dirname(cloud.__file__), "static", "rw.css"), encoding="utf-8").read()
    assert {k: out["kind_styles"].get(k) for k in ("bill", "post")} == {"bill": "k-bill", "post": "k-post"}
    assert all(re.search(rf"\.{re.escape(c)}\s*\{{", rw_css) for c in out["kind_styles"].values())
    assert "—" not in h and "false positive" not in h.lower()


def test_today_says_the_blind_spot_in_dollars_and_says_no_all_clear(page_run):
    blind = {"blind": True, "cents": 8000, "since": iso(25), "source": "run", "feeds": [],
             "lines": ["No remittance after Oct 28, 2026 (25 days). Since then EMA posted $80 of insurance payments on 1 check that no remittance on file explains."]}
    out, ans = page_run()
    today = dict(ans["/api/ema/today"], blind=blind)
    brief = json.loads(json.dumps(ans["/api/brief"]))
    brief["work"]["total"]["due7_n"] = 0
    brief["health"] = [{"key": "feed", "ok": False, "title": "No payment newer than 2026-10-28 (25 days ago)", "detail": "", "who": ""}] + brief["health"]
    out, _ = page_run({"/api/ema/today": today, "/api/brief": brief})
    h = out["today_html"]
    assert "Blind spot: $80 the site cannot check." in re.sub(r"<[^>]+>", "", h) and "No remittance after" in h
    assert "Nothing closes this week in the payments on file" in h and "is-done" not in h
    assert "No payment newer than" not in h          # the health banner does not say the blind spot a second time


def test_today_gives_no_all_clear_while_the_self_check_holds(page_run):
    """Nothing closes this week and the feeds are current: step 1 is done. While the self-check's line is up (safe mode
    while it checks its last run, given in /api/meta as banner: the key the route publishes, review hook-r2-2), Today does
    not say all clear."""
    out, ans = page_run()
    brief = json.loads(json.dumps(ans["/api/brief"]))
    brief["work"]["total"]["due7_n"] = 0
    out, _ = page_run({"/api/brief": brief})
    assert "step is-done" in out["today_html"] and "Nothing closes this week<" in out["today_html"]
    meta = dict(ans["/api/meta"], banner={"level": "safe_mode", "text": "Safe mode: the worklist is the one from before the last run.",
                                         "safe_mode": "Safe mode: the worklist is the one from before the last run."})
    out, _ = page_run({"/api/brief": brief, "/api/meta": meta})
    assert "is-done" not in out["today_html"] and "Nothing closes this week<" in out["today_html"]


def test_today_gives_no_all_clear_while_the_real_meta_says_safe_mode(page_run):
    """The same, on the real /api/meta answer (review hook-r2-2): safe mode in the store, nothing closing this week. The
    route publishes the banner under the key Today reads, so step 1 is not ticked."""
    import remitwatch.api as api
    got = {}

    def answers(c):
        conn = sqlite3.connect(api.DB_PATH)
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('selfcheck:safe_mode', ?)",
                     (json.dumps({"on": True, "text": "Safe mode: the run of today is held."}),))
        conn.commit()
        conn.close()
        meta = c.get("/api/meta").json()
        brief = c.get("/api/brief").json()
        brief["work"]["total"]["due7_n"] = 0           # nothing closes this week: step 1 would be ticked done
        got["banner"] = meta.get("banner")
        return {"/api/brief": brief, "/api/meta": meta}
    out, _ = page_run(answers)
    assert got["banner"] and got["banner"]["level"] == "safe_mode", got
    assert "is-done" not in out["today_html"] and "Nothing closes this week<" in out["today_html"]


def test_the_data_tab_draws_each_panel_and_its_lists(page_run):
    out, ans = page_run()
    h = out["data_html"]
    for title in ("EMA's reports", "Payments with no remittance", "Payments the payer sent that EMA never posted", "Credits",
                  "The A/R accounted for", "Denials written off in EMA"):
        assert title in h, title
    assert '<section id="ops-panels"></section>' in h
    assert out["decide_button"] == "heldfile1" and "This A/R lists far fewer lines" in h
    assert "Compliance: 1 Medicare primary credit over 60 days on EMA's A/R, $25." in h
    assert "1 payment</b> not posted, $85" in h
    assert out["buckets"] == [b for b in ["waiting", "chase", "paid_elsewhere", "denied_todo", "denied_started", "denied_sent", "denied_worked",
                                         "denied_no_item", "not_sent", "settled", "facility", "unbilled", "other", "credits"]
                              if ans["/api/ema/data"]["reconcile"]["w4"]["buckets"][b]["n"]]
    assert out["bucket_calls"] and out["bucket_calls"][0].startswith("/api/ema/ar?bucket=")
    assert out["request"].startswith("Please set up electronic remittances (835) for Testcare Health.")
    assert "posted after the newest remittance" not in h                     # nothing came after the newest remittance here
    assert "- check 777000555, posted " + iso(5) + ", Testcare Health, $80.00" in out["request"]
    for path in ("/api/ema/checks.csv", "/api/ema/unposted.csv", "/api/ema/credits.csv"):
        assert path in h
    assert "—" not in h and "false positive" not in h.lower()


def test_the_data_tab_says_how_to_close_the_gap_after_the_newest_remittance(page_run):
    out, ans = page_run()
    data = json.loads(json.dumps(ans["/api/ema/data"]))
    data["reconcile"]["since_feed_stopped"] = {"cents": 8000, "checks": 1}
    out, _ = page_run({"/api/ema/data": data})
    h = out["data_html"]
    assert "<b>$80</b> of it was posted after the newest remittance (1 check). The remittance feed is behind: get the newest TriZetto" in h
    assert "Those checks match by themselves once their remittances are in, so they are not among the requests below." in h


def test_the_policy_page_draws_each_pattern_and_records_a_decision(page_run):
    out, ans = page_run()
    h = out["policy_html"]
    pats = ans["/api/ema/policy"]["patterns"]
    assert "Write-off policy" in h and "2 patterns" in h and "$210</b> still appealable" in h
    assert [b for b in out["policy_buttons"] if b[2] == "true"] == [["0", "pursue", "true"], ["1", "pursue", "true"]]
    assert out["accept_calls"] == [{"path": "/api/ema/policy", "body": {"pattern": pats[1]["pattern"], "decision": "accept", "name": "Owner Test"},
                                    "method": "POST"}]
    # the row changes in place (no redraw: the filter and the place on the page stay), with who and that the next run applies it
    assert out["accept_went"] == [] and "Accepted by Owner Test on" in out["policy_after"] and "the next run applies it" in out["policy_after"]
    assert "1 item</b>, $90" in out["items_html"] and out["items_open"] == [{"open": ans["/api/ema/policy/items?"]["rows"][0]["finding_id"]}]
    assert "—" not in h + out["items_html"]


def test_an_unpriced_write_off_is_said_apart_on_each_page(page_run):
    """An item no rate prices has no dollars of its own; what EMA wrote off on it (the charge less what was paid) is
    said beside the figure as an upper bound, never inside it (needs_B 1.6)."""
    out, ans = page_run()
    copy = lambda k: json.loads(json.dumps(ans[k]))
    pol, items, today, data = copy("/api/ema/policy"), copy("/api/ema/policy/items?"), copy("/api/ema/today"), copy("/api/ema/data")
    pol["patterns"][1].update(upper_n=1, upper_cents=15000)
    items["rows"][0].update(shortfall_cents=None, written_off_basis="charge_less_paid", written_off_cents=15000)
    today["states"]["todo"].update(upper_n=1, upper_cents=15000)
    data["upper_bound"] = {"n": 1, "cents": 15000}
    answers = {"/api/ema/policy": pol, "/api/ema/policy/items?": items, "/api/ema/today": today, "/api/ema/data": data}
    out, _ = page_run(answers)
    assert "+ up to $150 not priced" in out["policy_html"] and "up to $150.00" in out["items_html"]
    assert "Not in this total: up to $150 EMA wrote off on 1 item no rate prices" in out["board_html"]
    assert "1 open item has no price" in out["data_html"] and "EMA wrote off up to $150 on them" in out["data_html"]
    narrow, _ = page_run(dict(answers, __narrow=True))
    assert "(+ up to $150 not priced)" in narrow["policy_html"] and "up to $150.00" in narrow["items_html"]


def test_long_lists_show_the_first_rows_and_the_rest_on_request(page_run):
    """On the real archive the Data tab holds hundreds of re-send requests and the policy page hundreds of patterns: the
    first ones show (newest month and most dollars; most dollars still inside a window), the rest are one click away,
    and the filter finds a pattern that is not shown yet."""
    out, ans = page_run()
    data = json.loads(json.dumps(ans["/api/ema/data"]))
    r0 = data["resend"][0]
    data["resend"] = [dict(r0, month=f"2025-{m:02d}", checks=1, cents=1000 + m) for m in range(12, 0, -1)] + \
                     [dict(r0, month=f"2024-{m:02d}", checks=1, cents=1000 + m) for m in range(12, 4, -1)]
    pol = json.loads(json.dumps(ans["/api/ema/policy"]))
    p0 = pol["patterns"][0]
    pol["patterns"] = [dict(p0, pattern=f"Payer {i:02d}|99213||CO-197", payer_group=f"Payer {i:02d}") for i in range(45)]
    out, _ = page_run({"/api/ema/data": data, "/api/ema/policy": pol, "__caps": True})
    caps = out["caps"]
    assert (caps["asks"], caps["asks_after"]) == (12, 20) and "Show all 20 requests" in out["data_html"]
    assert (caps["pol_shown"], caps["pol_found"], caps["pol_cleared"], caps["pol_all"]) == (40, 1, 40, 45)
    assert "Show all 45 patterns" in out["policy_html"] and "45 patterns" in out["policy_html"]


def test_at_phone_width_each_main_action_stays_on_screen(page_run):
    """At 390 px the wide tables become lists and cards: the request button, the report decision and the pattern
    decision are in each row's own block, not off to the right of a table that scrolls."""
    out, ans = page_run({"__narrow": True})
    assert out["file_rows"] == "list" and out["ask_buttons"] == len(ans["/api/ema/data"]["resend"])
    assert out["file_items"] == len(ans["/api/ema/data"]["catalog"]["files"]) == 3
    assert out["policy_cards"] == len(ans["/api/ema/policy"]["patterns"]) == 2
    assert [b[1] for b in out["policy_buttons"]] == ["pursue", "accept", "pursue", "accept"]
    assert "1 item</b>, $90" in out["items_html"] and out["items_open"]          # the items as a list, each one opens
    wide, _ = page_run()
    assert wide["file_rows"] == "table" and wide["policy_cards"] == 0


def test_today_says_which_report_waits_and_what_to_do(page_run):
    out, ans = page_run()
    reason = "Too big to read on this site: upload it on the Upload page (it compresses it), or put a zipped copy in the same folder."
    today = dict(ans["/api/ema/today"], reports_held=1,
                 reports_waiting=[{"report": "production", "source_name": "x.csv", "status": "waiting", "reason": reason}],
                 remits_missing={"key": "remits_missing", "ok": False, "month": "2026-10", "cents": 1234500,
                                 "title": "Payments without a remittance in 2026-10: $12,345"})
    out, _ = page_run({"/api/ema/today": today})
    h = out["data_html"]                        # EMA's exports line is on the Data tab (it was Today's)
    assert "A Production report waits: " + reason.replace("'", "&#39;") in h
    # the run's judgment of last month, with the month by name
    assert "1 EMA report waits for a decision</a>" in h and "EMA posted $12,345 of insurance payments in October 2026 with no remittance</a>" in h
    assert "2026-10" not in h


# ================================================================================ after the two reviews (C2's fixer)
# Each test below failed on the code before its fix.
PP_KEY = "VA Community Care|77600||payer_policy"
C7_KEY = f"{BCBS}|||CO-97"


def wiring_world(tmp_path, build) -> str:
    """A store built the way tests/test_ema_wiring.py builds one (835 rows written into the store, EMA's reports through
    the real readers and the posting ledger), then one detection run on that file's day."""
    from tests import test_ema_wiring as W
    db = str(tmp_path / "store.sqlite")
    store = W._new_store(db)
    lg = el.Ledger.open(str(tmp_path / "ema_ledger.sqlite"))
    try:
        build(store, lg, tmp_path)
        W.run(store)
    finally:
        lg.close()
        store.close()
    return db


def rerun_wiring(db):
    from tests import test_ema_wiring as W
    s = Store(db)
    try:
        W.run(s)
    finally:
        s.close()


def _c7_world(store, lg, tmp):
    """Ten G2211 lines Blue Cross denied CO-97, each posted in EMA as a contractual write-off: one
    EMA_DENIAL_AS_CONTRACTUAL item (C7), $1,100 written off."""
    from tests import test_ema_wiring as W
    more = []
    for i in range(10):
        more += [W.charge(F.link_no(10 + i), cpt="G2211", amount=110, patient=i % 4),
                 W.adj(F.link_no(10 + i), "CO-97", 110, cpt="G2211", patient=i % 4, contractual=True)]
    W.denial_world(store, lg, tmp, more)


def test_a_code_the_payer_never_pays_is_pursued_until_the_owner_accepts_it_on_the_policy_page(tmp_path, monkeypatch):
    """PLAN decision 14 (nothing is accepted by default) as the owner's decision 16 settled it: a code a payer almost never
    pays is one pattern 'group|code||payer_policy' on the policy page, with the payer's paid share, apart from the write-off
    totals, and its denials stay in the work (appealed in a batch per payer) until the owner accepts the pattern; Accept
    is recorded and the next run takes them off the work. Both hosts."""
    from tests import test_ema_wiring as W
    db = wiring_world(tmp_path, W._never_paid_world)
    stored = json.loads(rows(db, "SELECT value FROM meta WHERE key='ema:payer_policy'")[0][0])["patterns"][PP_KEY]
    assert stored["n"] == 21 and stored["cents"] > 0 and stored["decision"] == "pursue"
    assert rows(db, "SELECT COUNT(*) FROM findings WHERE signal='EMA_DENIED' AND COALESCE(stale,0)=0")[0][0] == 21
    c = _site(monkeypatch, db)
    t = c.get("/api/ema/policy").json()
    p = next(x for x in t["patterns"] if x["pattern"] == PP_KEY)
    assert (p["kind"], p["items"], p["cents"], p["decision"], p["decided_by"]) == ("payer_policy", 21, stored["cents"], "pursue", None)
    assert (p["payer_group"], p["code"], p["paid"], p["answered"], p["appealable"]) == ("VA Community Care", "77600", 0, 21, None)
    assert t["totals"]["decide"]["patterns"] == 0                     # nothing waits: pursued until the owner accepts
    assert t["totals"]["cents"] == sum(x["cents"] for x in t["patterns"] if x["kind"] == "write_off")
    r = c.post("/api/ema/policy", json={"pattern": PP_KEY, "decision": "accept", "name": "Owner"}, headers=XHR)
    assert r.status_code == 200, r.text
    p = next(x for x in c.get("/api/ema/policy").json()["patterns"] if x["pattern"] == PP_KEY)
    assert (p["decision"], p["pending"]) == ("accept", True) and p["decided_by"].startswith("Owner")
    rerun_wiring(db)
    assert rows(db, "SELECT COUNT(*) FROM findings WHERE signal='EMA_DENIED' AND COALESCE(stale,0)=0")[0][0] == 0
    t = c.get("/api/ema/policy").json()
    p = next(x for x in t["patterns"] if x["pattern"] == PP_KEY)
    assert (p["decision"], p["pending"], p["items"]) == ("accept", False, 21)
    (tmp_path / "vercel").mkdir()
    cl = _site(monkeypatch, db, serverless=True, tmp=tmp_path / "vercel")
    _same(t, cl.get("/api/ema/policy").json(), "/api/ema/policy")


def test_the_contractual_pattern_of_decision_3_is_on_the_policy_page_and_the_drawers_accept_records_it(tmp_path, monkeypatch):
    """PLAN decision 3 and review 1's second high finding: the owner's key for an EMA_DENIAL_AS_CONTRACTUAL item is
    'payer group|||reason'. The policy page lists it (its written-off dollars said apart, outside every money total),
    lists its items, and records the drawer's Accept this pattern; the next run marks the item accepted. Both hosts."""
    from remitwatch import story
    from tests import test_ema_wiring as W
    db = wiring_world(tmp_path, _c7_world)
    got = rows(db, "SELECT finding_id, evidence FROM findings WHERE signal='EMA_DENIAL_AS_CONTRACTUAL'")
    assert len(got) == 1
    fid, ev = got[0][0], json.loads(got[0][1])
    conn = sqlite3.connect(db)
    try:
        nx = story.next_step(conn, fid, today=W.TODAY)
    finally:
        conn.close()
    assert "accept_pattern" in nx["buttons"] and nx["pattern"] == C7_KEY
    c = _site(monkeypatch, db)
    t = c.get("/api/ema/policy").json()
    p = next(x for x in t["patterns"] if x["pattern"] == C7_KEY)
    assert (p["kind"], p["items"], p["payer_group"], p["code"], p["reason"]) == ("contractual", 1, BCBS, "", "CO-97")
    assert (p["written_off_cents"], p["cents"], p["decision"]) == (ev["written_off_cents"], 0, "decide") and ev["written_off_cents"] == 110000
    assert t["totals"]["cents"] == sum(x["cents"] for x in t["patterns"] if x["kind"] == "write_off")
    assert t["totals"]["contractual"] == {"patterns": 1, "items": 1, "written_off_cents": 110000}
    it = c.get("/api/ema/policy/items", params={"pattern": C7_KEY}).json()
    assert (it["count"], [r["finding_id"] for r in it["rows"]], it["rows"][0]["written_off_cents"]) == (1, [fid], 110000)
    r = c.post("/api/ema/policy", json={"pattern": nx["pattern"], "decision": "accept"}, headers=XHR)
    assert r.status_code == 200, r.text
    rerun_wiring(db)
    assert rows(db, "SELECT work_state FROM findings WHERE finding_id=?", fid)[0][0] == "accepted"
    t = c.get("/api/ema/policy").json()
    p = next(x for x in t["patterns"] if x["pattern"] == C7_KEY)
    assert (p["decision"], p["pending"], p["items"], p["decided_by"]) == ("accept", False, 1, "anonymous")
    (tmp_path / "vercel").mkdir()
    cl = _site(monkeypatch, db, serverless=True, tmp=tmp_path / "vercel")
    _same(t, cl.get("/api/ema/policy").json(), "/api/ema/policy")
    _same(c.get("/api/ema/policy/items", params={"pattern": C7_KEY}).json(),
          cl.get("/api/ema/policy/items", params={"pattern": C7_KEY}).json(), "items")


def test_two_people_deciding_two_patterns_at_once_on_a_server_keep_both_decisions(server, monkeypatch):
    """The plan's 'two people acting at once': the first decision is read and written back a moment later; the second,
    sent in that moment, waits for the first to be written and then reads it (one write lock around the read and the
    write), so both are kept."""
    import threading
    import time

    import remitwatch.api_ema as AE
    real = ec._meta
    reading = threading.Event()

    def slow_meta(conn, key, default=None):
        got = real(conn, key, default)
        if key == ec.POLICY_KEY and not reading.is_set():
            reading.set()
            time.sleep(0.4)
        return got
    monkeypatch.setattr(ec, "_meta", slow_meta)
    errors = []

    def decide(p, who):
        try:
            AE.policy_set(body={"pattern": p, "decision": "accept", "name": who}, user="anonymous")
        except Exception as exc:        # noqa: BLE001
            errors.append(repr(exc))
    a = threading.Thread(target=decide, args=(P_AUTH, "Owner"))
    a.start()
    assert reading.wait(10)
    b = threading.Thread(target=decide, args=(P_INFO, "Biller"))
    b.start()
    a.join(30)
    b.join(30)
    assert errors == []
    kept = json.loads(rows(server.db, "SELECT value FROM meta WHERE key=?", ec.POLICY_KEY)[0][0])
    assert set(kept) == {P_AUTH, P_INFO}, sorted(kept)


def test_nobody_has_worked_these_counts_only_what_is_in_work_now(server):
    """Review 2's high finding: Today's "Nobody has worked these" sits beside Work now and opens a list to work, so it
    counts inside Work now: an item the run put in Done, Expired or Accepted is not on it, and its count is its list."""
    c, db = server.c, server.db
    un = c.get("/api/ema/today").json()["unworked"]
    assert un["go"] == {"state": "todo", "ema": "unworked", "status": "all", "deadline": ""} and (un["n"], un["cents"]) == (2, 21000)
    assert c.post("/api/ema/policy", json={"pattern": P_AUTH, "decision": "accept", "name": "Owner"}, headers=XHR).status_code == 200
    rerun(db)
    t = c.get("/api/ema/today").json()
    assert t["states"]["accepted"]["n"] == 1 and (t["unworked"]["n"], t["unworked"]["cents"]) == (1, 12000)
    assert _list_total(c, t["unworked"]["go"]) in (None, (1, 12000))
    conn = sqlite3.connect(db)
    conn.execute("UPDATE findings SET work_state='done' WHERE signal='DENIED_MISSING_INFO'")
    conn.commit()
    conn.close()
    t = c.get("/api/ema/today").json()
    assert t["states"]["todo"]["n"] == 0 and (t["unworked"]["n"], t["unworked"]["cents"]) == (0, 0)


def test_the_blind_spot_counts_work_now_as_the_tile_beside_it_and_gives_each_part_its_dollars(worlds, tmp_path, monkeypatch):
    """Review 1: the banner's "N items worth $X in Work now" is the Work now tile under it (work now and the follow-ups
    due), and with two feeds behind each part says its own dollars and what they rest on, never one sum alone."""
    db = copied(worlds, tmp_path, "stale")
    conn = sqlite3.connect(db)
    conn.execute("UPDATE findings SET work_state='waiting', follow_up_on=? WHERE signal='DENIED_MISSING_INFO'", (iso(1),))
    conn.execute("UPDATE ema_report_files SET as_of=? WHERE report='ar_report'", (iso(20),))
    conn.execute("UPDATE ema_ar SET as_of=?", (iso(20),))
    conn.commit()
    conn.close()
    c = _site(monkeypatch, db)
    t = c.get("/api/ema/today").json()
    tile, b = t["states"]["todo"], t["blind"]
    assert (tile["n"], tile["cents"]) == (2, 21000)
    assert "the 2 items worth $210 in Work now" in next(x for x in b["lines"] if "Work now" in x)
    assert [(p["feed"], p["cents"]) for p in b["parts"]] == [("835", 8000), ("ema", 21000)] and b["cents"] == 29000
    assert all(p["words"] and p["basis"] for p in b["parts"])
    assert c.get("/api/ema/blind").json()["parts"] == b["parts"]


def test_a_held_report_waits_for_a_decision_only_until_someone_decides(server):
    """Review 1: Today's "waits for a decision" counts a held report nobody decided, or one a run held again after an
    Apply; one whose Apply came after the newest run waits for the next run instead. The Data tab says which."""
    c, db = server.c, server.db
    run_at = dt.datetime.fromisoformat(json.loads(rows(db, "SELECT value FROM meta WHERE key='ema:wiring'")[0][0])["at"])
    after, before = (run_at + dt.timedelta(minutes=5)).isoformat(), (run_at - dt.timedelta(days=1)).isoformat()
    conn = sqlite3.connect(db)
    for fid, dec, by, at in (("held1", None, None, None), ("held2", "apply", "biller", after), ("held3", "apply", "biller", before)):
        conn.execute("""INSERT INTO ema_report_files (file_id, report, source_name, via, status, status_reason, decision, decided_by,
                        decided_at, received_at) VALUES (?, 'production', ?, 'dropbox', 'held', 'Rows do not line up.', ?, ?, ?, ?)""",
                     (fid, fid + ".csv", dec, by, at, iso(1)))
    conn.commit()
    conn.close()
    t = c.get("/api/ema/today").json()
    assert (t["reports_held"], t["reports_pending"]) == (2, 1)
    held = {f["file_id"]: f for f in c.get("/api/ema/reports").json()["held"]}
    assert [held[k]["decision_pending"] for k in ("held1", "held2", "held3")] == [False, True, False]


def test_the_request_for_checks_posted_with_no_payer_name_lists_those_checks(server):
    """Review 1: a check EMA posted with no payer name is grouped as Unknown; its re-send request lists that check."""
    c, db = server.c, server.db
    for group in ("Unknown", ""):
        conn = sqlite3.connect(db)
        conn.execute("UPDATE ema_checks SET payer='', payer_group=? WHERE check_no='777000555'", (group,))
        conn.commit()
        conn.close()
        row = next(r for r in c.get("/api/ema/data").json()["resend"] if r["payer_group"] == "Unknown")
        got = c.get("/api/ema/checks", params={"payer": row["payer_group"], "month": row["month"], "cause": row["cause"]}).json()
        assert (got["count"], got["cents"]) == (row["checks"], row["cents"]) == (1, 8000), group


def test_the_credits_file_names_no_patient_while_names_are_off(worlds, tmp_path, monkeypatch):
    """Review 1: the names switch governs patient details. With names off the credits file carries the claim ID and no
    initials (as findings.csv); with names on, the initials too. Both hosts give the same file."""
    import remitwatch.api as api
    db = copied(worlds, tmp_path)
    c = _site(monkeypatch, db)
    lines = c.get("/api/ema/credits.csv").text.strip().splitlines()
    assert lines[0] == "claim_id,initials,service_date,code,amount,age_days,party,coverage,payer_group"
    body = [x.split(",") for x in lines[1:]]
    assert [(r[0], r[1]) for r in body] == [(F.claim_id(6), "")]
    monkeypatch.setattr(api, "SHOW_PHI", True)
    assert [x.split(",")[1] for x in c.get("/api/ema/credits.csv").text.strip().splitlines()[1:]] != [""]
    monkeypatch.setattr(api, "SHOW_PHI", False)
    (tmp_path / "vercel").mkdir()
    cl = _site(monkeypatch, db, serverless=True, tmp=tmp_path / "vercel")
    assert cl.get("/api/ema/credits.csv").text.strip().splitlines() == lines


def test_still_appealable_on_the_policy_page_counts_the_payers_own_limit(server):
    """Review 1 and orchestrator decision 2: an item past its safe date but inside the payer's own limit is still
    appealed, one at a time. The policy page (and the Data tab's line) count it as still appealable and say that part
    apart; Today's tile stays on the safe date, as bulk appeal does."""
    c, db = server.c, server.db
    conn = sqlite3.connect(db)
    conn.execute("UPDATE findings SET appeal_deadline=?, legal_deadline=? WHERE ema_outcome='auto_written_off'",
                 (iso(5), (T + dt.timedelta(days=120)).isoformat()))
    conn.commit()
    conn.close()
    t = c.get("/api/ema/policy").json()
    p = next(x for x in t["patterns"] if x["pattern"] == P_AUTH)
    assert (p["appealable"], p["appealable_cents"], p["legal_only"], p["legal_only_cents"]) == (1, 9000, 1, 9000)
    assert (t["totals"]["appealable_cents"], t["totals"]["legal_only_cents"]) == (21000, 9000)
    d = c.get("/api/ema/data").json()["policy"]
    assert (d["appealable_cents"], d["legal_only_cents"]) == (21000, 9000)
    assert c.get("/api/ema/today").json()["auto_written_off"]["n"] == 0


def test_the_payments_to_post_and_the_credits_to_refund_open_lists_that_hold_them(server):
    """Both reviews: Today's "N payments to post in EMA" and "N credits to refund" opened pages that list neither. The
    Data tab now carries the same two counts, each with the filter of the list it opens; that list is exactly the items
    counted."""
    c, db = server.c, server.db
    t = c.get("/api/ema/today").json()["other"]
    d = c.get("/api/ema/data").json()["items"]
    for key, signal in (("post", "REMIT_NOT_POSTED"), ("refund", "CREDIT_BALANCE")):
        assert d[key] == t[key] and t[key]["n"] == 1, key
        want = {r[0] for r in rows(db, "SELECT finding_id FROM findings WHERE signal=? AND COALESCE(stale,0)=0 "
                                       "AND status IN ('open','in_progress')", signal)}
        got = c.get("/api/findings", params=dict(d[key]["go"], live=1, limit=200)).json()
        assert (got["total"], got["shortfall_cents"]) == (t[key]["n"], t[key]["cents"]), key
        assert {r["finding_id"] for r in got["items"]} == want, key


def test_an_ar_row_in_a_bucket_with_an_item_names_the_item(server):
    """Review 2: a row in "Denied, with an item To do" opens that item."""
    c, db = server.c, server.db
    r = c.get("/api/ema/ar", params={"bucket": "denied_todo"}).json()
    row = r["rows"][0]
    want = rows(db, "SELECT finding_id FROM findings WHERE ema_link=? AND COALESCE(stale,0)=0 AND status IN ('open','in_progress')", row["link"])
    assert want and row["finding_id"] == want[0][0]
    assert all("finding_id" not in x for x in c.get("/api/ema/ar", params={"bucket": "credits"}).json()["rows"])


# ------------------------------------------------------------------------- the pages, after the two reviews (node)
def test_today_and_the_data_tab_draw_at_once_and_fill_each_place_when_its_answer_comes(page_run):
    """Review 2: nothing blocks while loading. Today's plan comes from the brief and shows before EMA's counts, the
    scoreboard and the exports' ages answer; the Data tab's checklist shows before EMA's panels."""
    slow = {k: 700 for k in ("/api/ema/today", "/api/results/scoreboard", "/api/ema/exports", "/api/ema/data")}
    out, _ = page_run({"__delay": slow})
    early, late = out["today_early"], out["today_html"]
    assert 'id="tdPlan"' in early and 'class="title"' in early and 'id="tdStates"' not in early and 'id="tdScore"' not in early
    assert 'id="tdStates"' in late and late.index('id="tdStates"') < late.index('id="tdPlan"')
    assert 'id="tdScore"' not in late and 'id="tdScore"' in out["board_html"]     # the scoreboard is Results' board now
    assert "Can the numbers be trusted?" in out["data_early"] and 'id="dtReports"' not in out["data_early"]
    assert out["checks_ms"] is not None and out["checks_ms"] < 500 and 'id="dtReports"' in out["data_html"]


def _dollars(c: int) -> str:
    return "$" + f"{int(abs(c) / 100 + 0.5):,}"           # static/index.html money(): whole dollars, half up


def test_today_s_strip_adds_up_and_each_part_opens_its_list(page_run):
    """CRITIQUE_premium change 5: one figure that adds up. Work now is the whole; what is new and the follow-ups due are
    its parts and add up to it; what closes within 7 days and is not sent yet is inside it, counted by the route with
    the Worklist's own filter (state todo, the to-do statuses, deadline week: the list step 1 opens), so each figure
    opens the very list it counts. Beside it, the week's progress and what deadlines took in the last 30 days."""
    out, ans = page_run()
    t, b = ans["/api/ema/today"], ans["/api/brief"]
    whole, fu = t["states"]["todo"], t["states"]["followup_due"]
    due = whole["due7"]
    assert due["go"] == {"state": "todo", "status": "open,in_progress", "deadline": "week", "sort": "deadline"}
    assert 0 <= due["n"] <= whole["n"] and 0 <= due["cents"] <= whole["cents"] and fu["n"] <= whole["n"]
    h = out["today_html"]
    strip = h.split('id="tdStates"')[1].split('id="tdMoreSlot"')[0]
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", strip))
    # the parts as shown add up to the whole as shown: the new part is the whole's dollars less the follow-ups' dollars
    whole_d, fu_d = int(_dollars(whole["cents"])[1:].replace(",", "")), int(_dollars(fu["cents"])[1:].replace(",", ""))
    assert f"Work now {_dollars(whole['cents'])} {whole['n']} item" in text
    assert f"New {_dollars((whole_d - fu_d) * 100)} ({whole['n'] - fu['n']})" in text and f"Follow-ups due {_dollars(fu['cents'])} ({fu['n']})" in text
    if due["n"]:
        assert f"Of which closes in 7 days: {_dollars(due['cents'])} ({due['n']})" in text and 'class="td-due"' in strip
    done, missed = b.get("done") or {}, b.get("missed") or {}
    assert f"Sent or paid this week {_dollars(done.get('week_cents') or 0)}" in text
    assert f"Lost to deadlines, last {missed.get('days') or 30} days {_dollars(missed.get('cents') or 0)}" in text
    for key, go in (("todo", whole["go"]), ("followup_due", fu["go"])):
        assert out["today_clicks"][key][-1]["filt"] == dict({"status": "open,in_progress", "deadline": "valid"}, **go), key
    if due["n"]:
        assert out["today_clicks"]["todo_week"][-1]["filt"] == dict({"status": "open,in_progress", "deadline": "valid"}, **due["go"])
    # with follow-ups due, the new part is the whole less them (the two parts add up to the whole)
    t2 = json.loads(json.dumps(t))
    t2["states"]["followup_due"].update(n=1, cents=min(5000, whole["cents"]))
    out2, _ = page_run({"/api/ema/today": t2})
    text2 = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", out2["today_html"].split('id="tdStates"')[1].split('id="tdMoreSlot"')[0]))
    fu2 = t2["states"]["followup_due"]["cents"]
    fu2_d = int(_dollars(fu2)[1:].replace(",", ""))
    assert f"New {_dollars((whole_d - fu2_d) * 100)} ({whole['n'] - 1})" in text2 and f"Follow-ups due {_dollars(fu2)} (1)" in text2
    assert "Nothing in it closes within 7 days." in text2 and 'class="td-due"' not in out2["today_html"]
    # something closes within 7 days: the red part says it inside the whole and opens its own list, soonest first
    t3 = json.loads(json.dumps(t))
    cents3 = min(4000, whole["cents"])
    t3["states"]["todo"]["due7"].update(n=1, cents=cents3)
    out3, _ = page_run({"/api/ema/today": t3})
    strip3 = out3["today_html"].split('id="tdStates"')[1].split('id="tdMoreSlot"')[0]
    text3 = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", strip3))
    assert f"Of which closes in 7 days: {_dollars(cents3)} (1)" in text3 and 'class="td-due"' in strip3
    assert out3["today_clicks"]["todo_week"][-1]["filt"] == dict({"status": "open,in_progress", "deadline": "valid"}, **due["go"])
    # the strip's whole and its 7-day part are drawn when the counts come; until then a dash, never a zero
    out, _ = page_run({"__fail": ["/api/ema/today"]})
    assert 'id="tdStates"' not in out["today_html"] and "Work now" in out["today_html"] and ">–<" in out["today_html"]


def test_today_shows_a_banner_only_for_a_late_feed_and_leaves_the_self_checks_to_data(page_run):
    """DESIGN P4: Today shows one banner only when a feed is late (safe mode has its own bar above every page); the
    other self-checks are on the Data tab, with a dot on Data in the sidebar (static/shell.js)."""
    out, ans = page_run()
    brief = json.loads(json.dumps(ans["/api/brief"]))
    brief["health"] = [{"key": "ema_wiring", "ok": False, "title": "A check that is not a feed failed", "detail": "", "who": ""}] + brief["health"]
    out, _ = page_run({"/api/brief": brief})
    assert "Before you start" not in out["today_html"] and "A check that is not a feed failed" not in out["today_html"]
    brief["health"] = [{"key": "feed", "ok": False, "title": "The remittances reach 2026-09-01 for most payers", "detail": "", "who": ""}] + brief["health"]
    out, _ = page_run({"/api/brief": brief})
    assert "<b>Before you start:</b> The remittances reach 2026-09-01 for most payers." in out["today_html"]
    assert "dtMissing" in out["today_links"]                      # How to fix lands on the payments with no remittance


def test_the_route_counts_what_closes_in_7_days_inside_work_now(worlds, tmp_path, monkeypatch):
    """/api/ema/today's Work now carries what in it closes within 7 days and is not sent yet: one definition with Today's
    step 1, the red numbers on the tabs and the count in each page's title (/api/brief work.total due7), counted with
    the Worklist's own filter, so the strip shows it as a part of the whole and a click opens exactly that list. A
    follow-up due on an item already sent is in Work now, but its filing window no longer closes on it. The window is
    today and the next 6 days, as the Worklist counts it. The same answer on both hosts (the dashboard's copy holds
    every column the count reads)."""
    db = copied(worlds, tmp_path)
    conn = sqlite3.connect(db)
    # one denial was sent and its follow-up is due (in Work now), its window's last day today; the other is not sent
    # yet and its window's last day is the 6th day from today, the last day inside the 7
    conn.execute("UPDATE findings SET status='appealed', work_state='waiting', follow_up_on=?, appeal_deadline=? "
                 "WHERE signal='DENIED_MISSING_INFO'", (iso(1), TODAY))
    conn.execute("UPDATE findings SET appeal_deadline=? WHERE signal='DENIED_AUTH'", (iso(-6),))
    conn.commit()
    conn.close()
    want = (1, 9000)
    todo = "(work_state='work_now' OR (work_state='waiting' AND follow_up_on <= ?)) AND status IN ('open','in_progress')"
    assert sql_count(db, f"{todo} AND appeal_deadline >= ? AND appeal_deadline < ?", TODAY, TODAY, iso(-7)) == want
    srv = _site(monkeypatch, db)
    st = srv.get("/api/ema/today").json()["states"]
    whole, due = st["todo"], st["todo"]["due7"]
    assert (whole["n"], whole["cents"]) == (2, 21000) and (st["followup_due"]["n"], st["followup_due"]["cents"]) == (1, 12000)
    assert (due["n"], due["cents"]) == want                                   # a part of the whole
    assert due["go"] == {"state": "todo", "status": "open,in_progress", "deadline": "week", "sort": "deadline"}
    assert _list_total(srv, due["go"]) == want                               # the list a click opens, its count line
    total = srv.get("/api/brief").json()["work"]["total"]
    assert (total["due7_n"], total["due7_cents"]) == want                    # step 1, the tabs' red numbers, the title
    # the 7th day from today is outside the window, here as on the list
    conn = sqlite3.connect(db)
    conn.execute("UPDATE findings SET appeal_deadline=? WHERE signal='DENIED_AUTH'", (iso(-7),))
    conn.commit()
    edge = srv.get("/api/ema/today").json()["states"]["todo"]["due7"]
    assert (edge["n"], edge["cents"]) == (0, 0) == _list_total(srv, edge["go"])
    conn.execute("UPDATE findings SET appeal_deadline=? WHERE signal='DENIED_AUTH'", (iso(-6),))
    conn.commit()
    conn.close()
    (tmp_path / "vercel").mkdir()
    cloud_c = _site(monkeypatch, db, serverless=True, tmp=tmp_path / "vercel")
    got = cloud_c.get("/api/ema/today").json()["states"]["todo"]
    assert got["due7"] == due and (got["n"], got["cents"]) == (whole["n"], whole["cents"])
    assert _list_total(cloud_c, due["go"]) == want
    cloud_total = cloud_c.get("/api/brief").json()["work"]["total"]
    assert (cloud_total["due7_n"], cloud_total["due7_cents"]) == want


def test_today_stays_blind_and_ticks_nothing_when_its_counts_do_not_come_back(page_run):
    """Review 2: when /api/ema/today fails, Today asks the blind spot on its own (as every other page does), says the
    counts did not come back, and never ticks step 1 as an all clear while a feed is behind."""
    out, ans = page_run()
    brief = json.loads(json.dumps(ans["/api/brief"]))
    brief["work"]["total"]["due7_n"] = 0
    blind = {"blind": True, "cents": 8000, "source": "run", "parts": [], "feeds": [{"feed": "835", "stale": True}],
             "lines": ["No remittance after Oct 28, 2026 (25 days)."]}
    out, _ = page_run({"/api/brief": brief, "/api/ema/blind": blind, "__fail": ["/api/ema/today"]})
    h = out["today_html"]
    assert "Today's EMA counts did not come back" in h and "Blind spot" in h and out["today_done"] == 0
    assert "dtMissing" in out["today_links"]                   # What to do lands on the payments with no remittance
    out, _ = page_run({"/api/brief": brief, "/api/ema/blind": dict(blind, blind=False), "__fail": ["/api/ema/today"]})
    assert "Blind spot" not in out["today_html"] and out["today_done"] == 1


def test_the_payments_to_post_and_the_credits_to_refund_land_on_lists_of_exactly_those_items(page_run, worlds):
    """Both reviews: Today's "1 payment to post in EMA" and "1 credit to refund" open the Data tab on a list of exactly
    the items counted (the count line says Today's number), and an item opened there walks that list."""
    out, ans = page_run()
    db = worlds["fresh"]
    for key, signal, line in (("post", "REMIT_NOT_POSTED", "1 payment to post in EMA, $85"),
                              ("refund", "CREDIT_BALANCE", "1 credit to refund or take back, $60")):
        want = [r[0] for r in rows(db, "SELECT finding_id FROM findings WHERE signal=? AND COALESCE(stale,0)=0", signal)]
        got = out["landing"][key]
        anchor = {"post": "dtPostList", "refund": "dtRefundList"}[key]
        assert got["ids"] == want and anchor in got["scrolled"] and line in got["html"], (key, got)
        assert got["opened"] == [{"open": want[0], "worklist": want, "total": 1}]
    assert "Fees &amp; takebacks" not in out["data_html"]
    assert "On EMA's A/R of the newest pull" in out["data_html"]        # the A/R's own credits, said apart


def test_a_held_report_is_decided_safely_and_offered_again_when_a_run_left_it_held(page_run):
    """Review 2: Apply is never the obvious button and asks first, saying what it replaces. Review 1: a report someone
    chose to apply that a run since left held is offered again; one chosen since the newest run waits for that run."""
    out, ans = page_run()
    assert out["decide_button"] == "heldfile1" and out["decide_primary"] is False
    assert out["apply_dialogs"] == ["confirm"] and "replaces what is on file" in out["apply_words"]
    assert out["apply_calls"] == ["/api/ema/reports/heldfile1/decision"]
    data = json.loads(json.dumps(ans["/api/ema/data"]))
    f = next(x for x in data["catalog"]["files"] if x["file_id"] == "heldfile1")
    f.update(decision="apply", decided_by="biller", decided_at=iso(9) + "T09:00:00+00:00", decision_pending=False)
    out, _ = page_run({"/api/ema/data": data})
    assert out["decide_button"] == "heldfile1" and "still held: choose again" in out["data_html"]
    assert "the next run reads it" not in out["data_html"]
    f.update(decided_at=TODAY + "T23:00:00+00:00", decision_pending=True)
    out, _ = page_run({"/api/ema/data": data})
    assert out["decide_button"] is None and "the next run reads it" in out["data_html"] and "Discard it instead" in out["data_html"]


def test_the_policy_page_keeps_the_owners_place_asks_at_most_once_and_walks_a_patterns_items(page_run):
    """Review 2: a decision changes its row in place (the filter and what it shows stay), Accept asks once and Pursue
    not at all (the name is a remembered field, not a question), "/" searches the pattern filter, and an item opened
    from a pattern's list walks that list with Next."""
    out, ans = page_run({"__find": "99213"})
    assert out["policy_find_f"] == "q"
    assert (out["policy_shown_before"], out["policy_find_after"], out["policy_shown_after"]) == (1, "99213", 1)
    assert out["accept_dialogs"] == ["confirm"] and out["pursue_dialogs"] == [] and out["pursue_calls"] == 1
    items = ans["/api/ema/policy/items?"]
    assert out["items_walk"] == {"worklist": [r["finding_id"] for r in items["rows"]], "total": items["count"]}


def test_the_policy_page_shows_what_waits_for_the_owners_decision(page_run):
    """PLAN decision 14 and review 1's highs, on the page: a code a payer almost never pays and a reason EMA posts as
    contractual have no button pressed until the owner decides, say so, and show their dollars apart; Today says how
    many wait, and opens the policy page."""
    out, ans = page_run()
    pol = json.loads(json.dumps(ans["/api/ema/policy"]))
    p0 = pol["patterns"][0]
    pp = dict(p0, pattern="VA Community Care|77600||payer_policy", payer_group="VA Community Care", code="77600", modifier="", reason="",
              kind="payer_policy", items=21, cents=630000, appealable=None, appealable_cents=None, auto=None, decision="decide",
              decided_by=None, decided_at=None, pending=False, paid=0, answered=21, later_paid_share=0.0,
              reason_text="The payer almost never pays this code")
    c7 = dict(p0, pattern=f"{BCBS}|||CO-97", code="", modifier="", reason="CO-97", kind="contractual", items=1, cents=0, appealable=None,
              appealable_cents=None, auto=None, decision="decide", decided_by=None, decided_at=None, pending=False, written_off_cents=110000,
              inside_cents=0)
    pol["patterns"] = [pp, c7] + pol["patterns"]
    pol["totals"]["decide"] = {"patterns": 1, "items": 21, "cents": 630000}
    pol["totals"]["contractual"] = {"patterns": 1, "items": 1, "written_off_cents": 110000}
    today = dict(ans["/api/ema/today"], decide={"patterns": 1, "items": 21, "cents": 630000})
    out, _ = page_run({"/api/ema/policy": pol, "/api/ema/today": today})
    h = out["policy_html"]
    pressed = {(b[0], b[1]): b[2] for b in out["policy_buttons"]}
    assert [pressed[(i, d)] for i in ("0", "1") for d in ("pursue", "accept")] == ["false"] * 4
    assert h.count("Waits for your decision") == 2 and "out of Work now until then" in h
    assert "1 code a payer almost never pays waits for your decision" in h
    assert "written off, in no total" in h and "0 of 21 lines" in h and 'class="polItems" data-i="0"' not in h
    assert "1 write-off pattern worth $6,300 (est.) waits for the owner's decision" in out["today_html"]


def test_today_words_dollars_and_phone(page_run):
    """Review 2: the Work now tile does not say "this week"; review 1 and PLAN decision 5: each dollar figure carries its
    basis on hover and estimates are labelled; decision 9: no nudge to leave small items; review 2: at phone width
    the figures are two compact lines, so the plan's first step is on the first screen."""
    out, ans = page_run()
    h = out["board_html"]
    assert "this week, follow-ups too" not in h and "to work, follow-ups due included" in h
    assert "of Work now, nothing done in 21 days" in h and "worth less than" not in h and "worth less than" not in out["today_html"]
    assert "Estimate" in out["board_titles"]["todo"] and "est.</small>" in h
    assert "Estimate" in out["today_titles"]["todo"]                            # Today's Work now says its basis too
    assert "safe date" in out["board_titles"]["auto_written_off"]
    assert "Estimate" in out["policy_html"] and "Dollars (est.)" in out["policy_html"]
    assert "postings: insurance payments posted on checks" in out["data_html"] and "The remittances (835)" in out["data_html"]
    narrow, _ = page_run({"__narrow": True})
    nh = narrow["today_html"]
    assert narrow["board_html"].count("td-compact") == 2 and 'class="tile' not in nh.split('id="tdPlan"')[0]
    assert set(narrow["board_tiles"][:5]) == {"todo", "followup_due", "waiting", "auto_written_off", "unworked"}


def test_the_blind_spot_gives_each_part_its_dollars_and_basis(page_run):
    """Review 1: with two feeds behind, the banner's headline gives each part its own dollars (and its basis on hover),
    never one sum alone."""
    out, ans = page_run()
    blind = {"blind": True, "cents": 29000, "source": "run", "feeds": [{"feed": "835", "stale": True}, {"feed": "ar", "stale": True}],
             "parts": [{"feed": "835", "cents": 8000, "words": "of insurance payments no remittance explains", "basis": "EMA's postings"},
                       {"feed": "ema", "cents": 21000, "words": "in Work now that may already be worked", "basis": "Estimate"}],
             "lines": ["No remittance after Oct 28, 2026.", "EMA's A/R report (20 days old)."]}
    out, _ = page_run({"/api/ema/today": dict(ans["/api/ema/today"], blind=blind)})
    text = re.sub(r"<[^>]+>", "", out["today_html"])
    assert "Blind spot: $80 of insurance payments no remittance explains and $210 in Work now that may already be worked." in text
    assert "$290" not in text
    # on a phone the headline and its dollars stay; the explanation folds under Why, so the plan stays on the first screen
    narrow, _ = page_run({"/api/ema/today": dict(ans["/api/ema/today"], blind=blind), "__narrow": True})
    banner = narrow["today_html"].split('id="tdBlind"')[1].split('id="tdHealth"')[0]
    assert "$80" in banner and "What to do" in banner and "<details" in banner and "<summary>Why</summary>" in banner
    assert banner.index("What to do") < banner.index("No remittance after")


def test_on_a_phone_step_one_names_the_three_biggest_kinds_and_how_many_more(page_run):
    """Real data: five kinds of work closing this week wrapped step 1's labels over five lines on a phone, and its Start
    button fell under the first screen. On a phone step 1 names the three kinds worth most and says how many more; at
    full width it names them all."""
    out, ans = page_run()
    brief = json.loads(json.dumps(ans["/api/brief"]))
    types = brief["work_types"][:5]
    assert len(types) == 5
    cents = [500, 900, 700, 600, 800]                       # the three worth most: the 2nd, the 5th and the 3rd
    brief["work"]["total"].update(due7_n=15, due7_cents=sum(cents) * 100)
    brief["work"]["types"] = [{"key": t["key"], "due7_n": 3, "due7_cents": c * 100} for t, c in zip(types, cents)]
    step1 = lambda h: h.split('id="tdPlan"')[1].split('class="step')[1]
    wide, _ = page_run({"/api/brief": brief})
    assert all(f">{t['label']}</span>" in step1(wide["today_html"]) for t in types)
    narrow, _ = page_run({"/api/brief": brief, "__narrow": True})
    s1 = step1(narrow["today_html"])
    shown = [t["label"] for t in types if f">{t['label']}</span>" in s1]
    assert shown == [types[i]["label"] for i in (1, 2, 4)], shown
    assert s1.index(f">{types[1]['label']}</span>") < s1.index(f">{types[4]['label']}</span>") < s1.index(f">{types[2]['label']}</span>")
    assert "and 2 more" in s1 and "Start (15)" in s1


def test_the_data_tab_lands_on_what_a_link_names_says_remittance_and_shows_an_ar_rows_item(page_run):
    """Review 2: Today's links name where they land on the Data tab; the Data tab says "remittance", not "835", in its
    headings; an A/R row in a bucket with an item opens it, and at phone width its actions come first."""
    out, ans = page_run()
    today = dict(ans["/api/ema/today"], reports_held=1, reports_pending=1)
    out, _ = page_run({"/api/ema/today": today})
    assert {"dtReports"} <= set(out["data_links"]) and "1 EMA report waits for the next run" in out["data_html"]
    heads = re.findall(r"<h2>([^<]*)</h2>", out["data_html"])
    assert heads and not [x for x in heads if "835" in x] and "no 835 on file" not in out["data_html"]
    narrow, ans = page_run(lambda c: {"/api/ema/ar?": c.get("/api/ema/ar", params={"bucket": "denied_todo"}).json(), "__narrow": True})
    denied = ans["/api/ema/ar?"]
    fid = denied["rows"][0]["finding_id"]
    rh = narrow["ar_rows_html"]
    assert f'data-id="{fid}"' in rh and rh.index("Open the item") < rh.index(denied["rows"][0]["claim_id"])


def test_an_export_held_or_not_read_yet_is_not_said_never_to_have_arrived(page_run):
    """needs_A2 F.5: an export's age is that of what RemitWatch read, so an overdue row with no age may be a file that
    arrived and is held or not read yet: Today says which, never "never arrived" for it."""
    out, ans = page_run()
    rows_ = [{"key": "ar", "title": "A/R report", "overdue": True, "age_days": None, "held": True, "unread": "held", "arrived": iso(2)},
             {"key": "prod", "title": "Production report", "overdue": True, "age_days": None, "held": False, "unread": "waiting", "arrived": iso(3)},
             {"key": "w1", "title": "Weekly export", "overdue": True, "age_days": None, "held": False, "unread": None, "arrived": None}]
    out, _ = page_run({"/api/ema/exports": {"exports": rows_}})
    h = out["data_html"]                        # EMA's exports line is on the Data tab (it was Today's)
    assert "A/R report: the newest file is held" in h and "Production report: none read yet" in h and "Weekly export: never arrived" in h


def test_no_page_tells_a_person_to_leave_an_item_for_its_size():
    """PLAN decision 9: no dollar amount is too small. Nothing on Today, the Data tab or the policy page nudges a person to
    hide small items or to let one go as not worth pursuing."""
    static = os.path.join(os.path.dirname(cloud.__file__), "static")
    for name in ("today.js", "data.js", "policy.js"):
        src = open(os.path.join(static, name), encoding="utf-8").read()
        assert "not worth pursuing" not in src and "worth less than" not in src and "amount filter hides" not in src, name


def test_the_guides_first_item_opens_work_now():
    """Review C1-2 (aimed at C2's today.js): the first-visit guide's "Show me the first item" opens the first item of
    Work now, never one that waits on the payer or a question for the owner."""
    src = open(os.path.join(os.path.dirname(cloud.__file__), "static", "today.js"), encoding="utf-8").read()
    assert "goWork({state:'todo', deadline:'valid', sort:'deadline', first:'1'})" in src


def test_the_unposted_line_counts_every_row_of_its_file(page_run):
    """Real-data acceptance (C2 fixer): the run's list of payments EMA never posted also holds payments a remittance
    reported but whose money never arrived (kind never_arrived, counted as unfunded_n); the Data tab's line counts them
    too, so its numbers add up to the rows of the file it links (and to Today's count of items to post)."""
    out, ans = page_run()
    data = json.loads(json.dumps(ans["/api/ema/data"]))
    data["reconcile"]["c4"].update(unfunded_n=1, unfunded_cents=52360)
    out, _ = page_run({"/api/ema/data": data})
    text = re.sub(r"<[^>]+>", "", out["data_html"])
    assert "1 payment the payer reported but never sent, $524 (ask the payer for it)" in text


def test_today_and_the_pattern_counts_take_the_practices_day_not_utcs(server, monkeypatch):
    """The Today page's day and the write-off patterns' "still appealable" counts are the practice's day
    (runtime.practice_today), as every list is. SQL's date('now') is UTC, a day off the practice's for hours each evening in
    the US: an item whose last day is the practice's today was counted lost, and one whose last day passed there was
    counted appealable. The zone below puts the practice a day off UTC whatever the hour the test runs."""
    behind = dt.datetime.now(dt.timezone.utc).hour < 12
    monkeypatch.setenv("REMITWATCH_TZ", "Etc/GMT+12" if behind else "Etc/GMT-14")     # UTC-12 before noon UTC, UTC+14 after
    day = runtime.practice_today()
    assert day != dt.datetime.now(dt.timezone.utc).date().isoformat()
    c, db = server.c, server.db
    f = fid(db, "DENIED_AUTH", "auto_written_off")
    # behind UTC: the last day is the practice's today, still appealable; ahead of UTC: the last day was yesterday there, lost
    last = day if behind else runtime.practice_today(-1)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE findings SET appeal_deadline=?, legal_deadline=NULL WHERE finding_id=?", (last, f))
    conn.commit()
    conn.close()
    assert c.get("/api/ema/today").json()["today"] == day
    pol = c.get("/api/ema/policy").json()
    a = {p["pattern"]: p for p in pol["patterns"]}[P_AUTH]
    assert (a["appealable"], a["appealable_cents"]) == ((1, 9000) if behind else (0, 0)), (behind, a)
    assert pol["today"] == day


def test_the_data_tab_and_today_count_the_reports_ages_to_the_practices_day_on_both_hosts(worlds, tmp_path, monkeypatch):
    """The reports' ages on the Data tab and Today (fresh: ar_days, production_days) count to the practice's day, as the
    blind spot beside them and every list do. They counted to the machine's day (ema_checks' default, UTC on both hosts):
    from 7 PM Eastern an A/R pulled eight days ago read nine, past the eight that still counts as fresh. The zone puts the
    practice a day off UTC whatever the hour. Both hosts: the server's store, and the serverless host's dashboard copy."""
    from tests.test_practice_day import pin_zone
    pin_zone(monkeypatch)
    day = dt.date.fromisoformat(runtime.practice_today())
    db = copied(worlds, tmp_path)
    ar_as_of = rows(db, "SELECT MAX(as_of) FROM ema_report_files WHERE report='ar_report'")[0][0]
    prod_as_of = rows(db, "SELECT MAX(as_of) FROM ema_report_files WHERE report='production'")[0][0]
    want = {"today": day.isoformat(), "ar_days": (day - dt.date.fromisoformat(ar_as_of)).days,
            "production_days": (day - dt.date.fromisoformat(prod_as_of)).days}

    def check(c, host):
        for path in ("/api/ema/data", "/api/ema/today"):
            fr = c.get(path).json()["fresh"]
            assert {k: fr[k] for k in want} == want, (host, path, fr)
        feeds = {f["feed"]: f["days"] for f in c.get("/api/ema/data").json()["blind"]["feeds"]}
        assert (feeds["ar"], feeds["production"]) == (want["ar_days"], want["production_days"]), (host, feeds)
    check(_site(monkeypatch, db), "server")           # asked before the serverless site switches the mode
    (tmp_path / "vercel").mkdir()
    check(_site(monkeypatch, db, serverless=True, tmp=tmp_path / "vercel"), "serverless")


# ------------------------------------------------------------------------------- Work (unit ui-work, the list's fields)
def _items(c, **params):
    base = {"category": ",".join(MONEY), "live": 1, "state": "todo", "status": "all", "limit": 200}
    return c.get("/api/findings", params=dict(base, **params)).json()


def test_last_chance_closes_on_the_payers_limit_counts_in_7_days_and_sorts_first(worlds, tmp_path, monkeypatch):
    """CRITIQUE_biller change 3 (unit ui-work): an item past our target date whose payer's own limit is still ahead is the
    most urgent money, never "passed" in gray. It closes on the payer's limit (api._last_day): inside 7 days it counts in
    Work now's 7-day part, Today's step 1 and the title (one definition), and its list shows it first, soonest first; it
    is not among the closed windows. Once the payer's limit passes too, it is closed and counts nowhere as due. The same
    on both hosts (legal_deadline is in the dashboard's copy)."""
    db = copied(worlds, tmp_path)
    conn = sqlite3.connect(db)
    # our target passed 12 days ago; the payer's own limit is 3 days ahead. The other item is due in 20 days
    conn.execute("UPDATE findings SET appeal_deadline=?, legal_deadline=? WHERE signal='DENIED_AUTH'", (iso(12), iso(-3)))
    conn.execute("UPDATE findings SET appeal_deadline=?, legal_deadline=NULL WHERE signal='DENIED_MISSING_INFO'", (iso(-20),))
    conn.commit()
    conn.close()
    want = (1, 9000)
    (tmp_path / "vercel").mkdir()
    for c in (_site(monkeypatch, db), _site(monkeypatch, db, serverless=True, tmp=tmp_path / "vercel")):
        due = c.get("/api/ema/today").json()["states"]["todo"]["due7"]
        assert (due["n"], due["cents"]) == want and _list_total(c, due["go"]) == want
        total = c.get("/api/brief").json()["work"]["total"]
        assert (total["due7_n"], total["due7_cents"]) == want
        soonest = [r["signal"] for r in _items(c, sort="deadline")["items"]]
        assert soonest.index("DENIED_AUTH") < soonest.index("DENIED_MISSING_INFO")
        closed = [r["signal"] for r in _items(c, state="", status="open,in_progress", deadline="expired")["items"]]
        assert "DENIED_AUTH" not in closed
        # "Window still open" keeps our target date (orchestrator decision 2: bulk appeal and Today's write-off count)
        assert "DENIED_AUTH" not in [r["signal"] for r in _items(c, state="", status="open,in_progress", deadline="valid")["items"]]
    # past the payer's own limit too: the window is closed
    conn = sqlite3.connect(db)
    conn.execute("UPDATE findings SET legal_deadline=? WHERE signal='DENIED_AUTH'", (iso(1),))
    conn.commit()
    conn.close()
    srv = _site(monkeypatch, db)
    assert "DENIED_AUTH" in [r["signal"] for r in _items(srv, state="", status="open,in_progress", deadline="expired")["items"]]
    due = srv.get("/api/ema/today").json()["states"]["todo"]["due7"]
    assert (due["n"], due["cents"]) == (0, 0) == _list_total(srv, due["go"])


def test_one_payer_in_one_sitting_and_the_owners_work_after_a_billers(worlds, tmp_path, monkeypatch):
    """CRITIQUE_biller changes 8 and 19 (unit ui-work). "By payer, most money first" lists each payer's items together,
    the payers by the money the list holds for each, and gives each payer's count and dollars in the whole list (not the
    rows shown), which add up to the count line. On the Worklist (owner_last) a rate pattern, the owner's contract
    question, comes after every claim a biller works, whatever the order, and the list says how many there are and their
    dollars; the count line still counts them (one definition with Today). The same on both hosts."""
    db = copied(worlds, tmp_path)
    conn = sqlite3.connect(db)
    top = conn.execute("SELECT finding_id FROM findings WHERE category IN ('denial','bundling','downcoding','underpayment') "
                       "AND work_state='work_now' ORDER BY shortfall_cents DESC, finding_id LIMIT 1").fetchone()[0]
    conn.execute("UPDATE findings SET signal='SECOND_RATE', category='underpayment' WHERE finding_id=?", (top,))
    conn.commit()
    conn.close()
    (tmp_path / "vercel").mkdir()
    for c in (_site(monkeypatch, db), _site(monkeypatch, db, serverless=True, tmp=tmp_path / "vercel")):
        got = _items(c, sort="payer_money")
        payers = [r["payer_group"] or "" for r in got["items"]]
        runs = [p for i, p in enumerate(payers) if i == 0 or p != payers[i - 1]]
        assert len(runs) == len(set(runs)), "each payer's items come together"
        groups = got["groups"]
        assert [g["cents"] for g in groups] == sorted((g["cents"] for g in groups), reverse=True)
        assert runs == [g["payer_group"] for g in groups if g["payer_group"] in set(payers)]
        assert sum(g["n"] for g in groups) == got["total"] and sum(g["cents"] for g in groups) == got["shortfall_cents"]
        plain = _items(c, sort="value")
        assert plain["items"][0]["finding_id"] == top and "owner_work" not in plain
        owner = _items(c, sort="value", owner_last=1)
        assert owner["items"][-1]["finding_id"] == top and owner["total"] == plain["total"]
        assert owner["owner_work"]["n"] == 1 and owner["owner_work"]["cents"] == plain["items"][0]["shortfall_cents"]
