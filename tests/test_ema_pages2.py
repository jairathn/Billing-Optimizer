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
import shutil
import sqlite3
import subprocess

import pytest

from remitwatch import cloud, ema_checks as ec, ema_ledger as el, ema_reports as er
from remitwatch.detect import Detector
from remitwatch.state import LocalState
from remitwatch.store import Store
from tests import ema_fixtures as F

P = F.PAYERS
SALT_HEX = "cd" * 32
XHR = {"X-Requested-With": "remitwatch"}
BCBS = "Anthem / BCBS"
T = dt.datetime.now(dt.timezone.utc).date()          # the site's day: the lists count windows from it (SQL date('now'))
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
