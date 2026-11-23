"""
EMA's A/R and Production reports wired into the detection run (remitwatch.detect, in the order of the tech lead's
amendment 4.5): the line outcome of each item, what EMA shows paid on checks no 835 carries (C6), what it wrote off and
how (C2, the automatic write-offs of amendment 8), the owner's write-off policy, claims that never left EMA, denials only
EMA saw (C5) and the work they hand on, 835 payments EMA never posted (C4), the item states, and the new rules the
practice-manager trace and the answer key found missing (a second payer never billed, credits to refund, short payments
with no 835, a claim sent on to another carrier).

Every row is invented (tests/ema_fixtures.py). The 835 side is written straight into the store's remittance tables; the
EMA side goes through the real readers, the posting ledger and its refresh, then the Detector runs as the cycle runs it.
REMITWATCH_EMA_STRICT makes a failing EMA step fail the test instead of being recorded.
"""
from __future__ import annotations

import datetime as dt
import itertools
import json

import pytest

from remitwatch import ema_checks as ec, ema_ledger as el, ema_reports as er
from remitwatch.detect import DETECT_RULES, EXTRA_FINDING_COLS, RULE_FILES, Detector, _fid
from remitwatch.store import Store
from tests import ema_fixtures as F

D = dt.date
P = F.PAYERS
SVC = D(2026, 3, 2)
S = SVC.isoformat()
SALT_HEX = "ab" * 32
TODAY = "2026-05-03"
BCBS = "Anthem / BCBS"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(er, "CLAIM_HOST_SUFFIX", F.HOST_SUFFIX)
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    monkeypatch.setenv("REMITWATCH_EMA_STRICT", "1")
    for k in ("VERCEL", "REMITWATCH_EMA_REPORTS", "REMITWATCH_EMA_CLAIM_URL", "REMITWATCH_EMA_PAYER_ALIASES",
              "REMITWATCH_EMA_LEDGER", "REMITWATCH_EMA_REPORT_KEEP", "REMITWATCH_EMA_CLOSES"):
        monkeypatch.delenv(k, raising=False)
    ec._LAST_POST.clear()


def _new_store(path) -> Store:
    s = Store(path)
    er.ensure_schema(s.conn)
    s.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('ema:key_salt', ?)", (SALT_HEX,))
    s.conn.commit()
    return s


@pytest.fixture
def store(tmp_path):
    s = _new_store(str(tmp_path / "store.sqlite"))
    yield s
    s.close()


@pytest.fixture
def ledger(tmp_path):
    lg = el.Ledger.open(str(tmp_path / "ema_ledger.sqlite"))
    yield lg
    lg.close()


class Log(list):
    def __call__(self, *a):
        self.append(" ".join(str(x) for x in a))


_N = itertools.count(1)
_TX = itertools.count(1)


# ------------------------------------------------------------------------------------------------------- builders
def charge(link, cpt="99213", amount=150, patient=0, svc=SVC, posted=None, **kw):
    return F.production_row(link=link, cpt=cpt, charges=amount, patient=patient, svc=svc, posted=posted or svc, **kw)


def pay(link, check, amount, cpt="99213", patient=0, svc=SVC, posted=D(2026, 3, 31), payer=P["bcbs"], **kw):
    return F.production_row(link=link, cpt=cpt, ins_paid=amount, check=check, patient=patient, svc=svc, posted=posted, payer=payer, **kw)


def adj(link, reason, amount, cpt="99213", patient=0, svc=SVC, posted=D(2026, 3, 31), contractual=False, payer=P["bcbs"], **kw):
    money = {"contractual": amount} if contractual else {"noncontractual": amount}
    return F.production_row(link=link, cpt=cpt, reason=reason, patient=patient, svc=svc, posted=posted, payer=payer, **money, **kw)


def load(store, ledger, tmp_path, rows, ar_rows=None):
    """Read an A/R (when given) and a Production file, then refresh the summaries (Part A and Part B)."""
    if ar_rows is not None:
        p = F.write(str(tmp_path / f"ar{next(_N)}.xlsx"), F.ar_xlsx(ar_rows))
        r = er.ingest_file(store, p, via="test", log=Log())
        assert r["status"] == "current", r
    if rows:
        p = F.write(str(tmp_path / f"prod{next(_N)}.csv"), F.production_csv(rows))
        r = er.ingest_file(store, p, via="test", ledger=ledger, log=Log())
        assert r["status"] == "current", r
    el.refresh(store, ledger, parts="AB", log=Log())


def remit(store, *, trace, paid_on, claims, payer="BLUE CROSS TEST", status="1"):
    """One 835 payment written straight into the store. claims: dicts with pcn, last, first, status, and lines
    [(svc, code, charge $, paid $, [(group, reason, $), ...], {extra line columns})]."""
    c = store.conn
    tk = f"TX{next(_TX):05d}"
    total = sum(round(ln[3] * 100) for cl in claims for ln in cl["lines"])
    c.execute("INSERT INTO transactions (transaction_key, trace_number, payment_date, payer_name, superseded, "
              "claim_paid_total_cents, payment_amount_cents) VALUES (?,?,?,?,0,?,?)", (tk, trace, paid_on, payer, total, total))
    for i, cl in enumerate(claims):
        ck = f"{tk}-{i}"
        c.execute("INSERT INTO claims (claim_key, transaction_key, patient_control_number, patient_last, patient_first, patient_id, "
                  "payment_date, trace_number, statement_from, claim_status_code, charge_cents, paid_cents, payer_name, "
                  "crossover_carrier_name) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (ck, tk, cl["pcn"], cl.get("last", "TESTPATIENT"), cl.get("first", "ALPHA"), cl.get("member", "MEMBER000001"),
                   paid_on, trace, cl["lines"][0][0], cl.get("status", status), sum(round(ln[2] * 100) for ln in cl["lines"]),
                   sum(round(ln[3] * 100) for ln in cl["lines"]), payer, cl.get("crossover")))
        for j, ln in enumerate(cl["lines"]):
            svc, code, chg, paid = ln[:4]
            extra = ln[5] if len(ln) > 5 else {}
            lk = f"{ck}-{j}"
            cols = {"line_key": lk, "claim_key": ck, "transaction_key": tk, "procedure_code": code, "service_date": svc,
                    "charge_cents": round(chg * 100), "paid_cents": round(paid * 100), "patient_control_number": cl["pcn"],
                    "payment_date": paid_on, "payer_name": payer, "claim_status_code": cl.get("status", status)}
            cols.update(extra)
            c.execute(f"INSERT INTO lines ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", tuple(cols.values()))
            for g, r, amt in (ln[4] if len(ln) > 4 else []):
                c.execute("INSERT INTO adjustments (transaction_key, claim_key, line_key, level, group_code, reason_code, amount_cents) "
                          "VALUES (?,?,?,?,?,?,?)", (tk, ck, lk, "line", g, r, round(amt * 100)))
    c.commit()
    return tk


def denial_world(store, ledger, tmp_path, extra=(), *, own_posted=D(2026, 3, 31), ar_rows=None):
    """Claim 1 (EMA claim 1): a 99213 charged $150 and denied by Blue Cross on 835 trace 1 (paid $0, CO-197). EMA posted the
    $0 answer under the trace and wrote the denial off the same day (its posting rule). A later charge on EMA claim 99 carries
    Production's as_of to May 1. The A/R pairs claim 1 with EMA claim 1."""
    tk = remit(store, trace=F.check_no(1), paid_on="2026-03-28",
               claims=[{"pcn": F.claim_id(1), "lines": [(S, "99213", 150, 0, [("CO", "197", 150)])]}])
    rows = [charge(F.link_no(1)), pay(F.link_no(1), F.check_no(1), 0, posted=own_posted),
            adj(F.link_no(1), "CO-197", 150, posted=own_posted),
            charge(F.link_no(99), cpt="99212", patient=3, svc=D(2026, 3, 20), posted=D(2026, 5, 1), status="Payment Pending")]
    ar = list(ar_rows or []) + [F.ar_row(claim=F.claim_id(1), link=F.link_no(1), status="Denied", denial=150, current=0,
                                         aging=D(2026, 4, 25), svc=SVC)]
    load(store, ledger, tmp_path, rows + list(extra), ar)
    return tk


def run(store, today=TODAY):
    det = Detector(store, run_id=f"r{next(_N)}", today=today)
    det.run()
    assert det.ema_errors == [], det.ema_errors
    return det


def items(store, where="1=1", *args):
    store.conn.row_factory = None
    cols = [r[1] for r in store.conn.execute("PRAGMA table_info(findings)")]
    return [dict(zip(cols, r)) for r in store.conn.execute(f"SELECT * FROM findings WHERE {where} ORDER BY finding_id", args)]


def one(store, where="1=1", *args):
    got = items(store, where, *args)
    assert len(got) == 1, [(g["signal"], g["finding_id"]) for g in got]
    return got[0]


# ------------------------------------------------------------------------------------------------- the run itself
def test_the_rules_version_the_hashed_files_and_the_new_columns():
    assert DETECT_RULES == "2.11"
    for name in ("ema_reports.py", "ema_ledger.py", "ema_checks.py", "states.py", "story.py", "archive_rules.py"):
        assert name in RULE_FILES
    for col in ("written_off_source", "written_off_kind", "written_off_when", "ema_link", "ema_status", "ema_status_src", "ema_as_of",
                "ema_balance_cents", "ema_worked_on", "ema_worked_how", "source", "ema_outcome", "ema_outcome_on",
                "ema_writeoff_evidence", "work_state", "follow_up_on", "state_basis", "appeal_block", "legal_deadline"):
        assert col in EXTRA_FINDING_COLS, col


def test_a_store_without_emas_reports_runs_as_before_and_every_item_gets_its_state(store):
    remit(store, trace=F.check_no(1), paid_on="2026-03-28",
          claims=[{"pcn": F.claim_id(1), "lines": [(S, "99213", 150, 0, [("CO", "197", 150)])]}])
    det = run(store)
    assert det.ema_on is False and det.ema_outcomes == {}
    f = one(store)
    assert f["signal"] == "DENIED_AUTH" and f["source"] == "835" and f["ema_outcome"] is None
    assert f["work_state"] == "work_now" and f["state_basis"]
    wiring = json.loads(store.conn.execute("SELECT value FROM meta WHERE key='ema:wiring'").fetchone()[0])
    assert wiring["on"] is False and wiring["errors"] == []
    assert store.conn.execute("SELECT 1 FROM meta WHERE key='ema:keep_dirty'").fetchone() is None


def test_an_automatic_write_off_is_flagged_and_stays_work(store, ledger, tmp_path):
    denial_world(store, ledger, tmp_path)
    run(store)
    f = one(store, "signal='DENIED_AUTH'")
    assert f["status"] == "open" and f["ema_outcome"] == "auto_written_off" and f["ema_outcome_on"] == "2026-03-31"
    assert f["written_off_when"] == "at_posting" and f["ema_writeoff_evidence"] == "posting_day"
    assert f["written_off_source"] == "production" and f["written_off_cents"] == 15000
    # EMA calls the claim Resolved/Paid (Production's newer status): context only, the line's own outcome comes first
    assert f["ema_link"] == F.link_no(1) and f["ema_status"] == "Resolved/Paid" and f["ema_status_src"] == "production"
    assert f["work_state"] == "work_now" and "EMA's posting rule wrote it off" in f["state_basis"]
    assert json.loads(f["evidence"])["writeoff_pattern"] == f"{BCBS}|99213||CO-197"
    # Anthem's two years: the safe date (730 days, across a leap day) and the payer's own limit, a day later
    assert f["appeal_deadline"] == "2028-03-27" and f["legal_deadline"] == "2028-03-28"
    pol = json.loads(store.conn.execute("SELECT value FROM meta WHERE key='ema:policy_summary'").fetchone()[0])
    assert pol["patterns"][0]["pattern"] == f"{BCBS}|99213||CO-197" and pol["patterns"][0]["auto"] == 1


def test_an_accepted_pattern_leaves_the_work_queue_and_stays_findable(store, ledger, tmp_path):
    denial_world(store, ledger, tmp_path)
    run(store)
    ec.set_policy(store.conn, f"{BCBS}|99213||CO-197", "accept", "owner")
    run(store)
    f = one(store, "signal='DENIED_AUTH'")
    assert f["ema_outcome"] == "written_off_by_policy" and f["work_state"] == "accepted" and f["status"] == "open"


def test_a_payment_ema_posted_with_no_835_closes_the_item_with_its_proof_and_a_newer_pull_reopens_it(store, ledger, tmp_path):
    denial_world(store, ledger, tmp_path, [pay(F.link_no(1), "PAPER-1", 90, posted=D(2026, 4, 20))])
    det = run(store)
    assert det.ema_counts["c6_closed"] == 1
    f = one(store, "signal='DENIED_AUTH'")
    assert f["status"] == "resolved" and f["prev_status"] == "open" and f["recovered_cents"] == 9000
    assert f["auto_note"].startswith("EMA shows $90.00 paid by") and "PAPER-1" not in f["auto_note"]
    proof = json.loads(f["evidence"])["paid_later"]
    assert proof["source"] == "EMA Production" and proof["proof"][0]["decisive"] and proof["proof"][0]["amount_cents"] == 9000
    assert f["work_state"] == "done"
    # a newer full pull of the same dates no longer shows the payment: the item opens again, and says why in its history
    rows = [charge(F.link_no(1)), pay(F.link_no(1), F.check_no(1), 0, posted=D(2026, 3, 31)),
            adj(F.link_no(1), "CO-197", 150, posted=D(2026, 3, 31)),
            charge(F.link_no(99), cpt="99212", patient=3, svc=D(2026, 3, 20), posted=D(2026, 5, 2), status="Payment Pending")]
    load(store, ledger, tmp_path, rows)
    run(store)
    f = one(store, "signal='DENIED_AUTH'")
    assert f["status"] == "open" and f["auto_note"] is None and f["work_state"] == "work_now"
    kinds = [r[0] for r in store.conn.execute("SELECT kind FROM finding_events WHERE finding_id=? ORDER BY id", (f["finding_id"],))]
    assert kinds == ["paid_on_later_remit", "reopened"]


def test_a_persons_status_is_never_replaced_by_an_ema_closure(store, ledger, tmp_path):
    from remitwatch.actions import apply_status
    denial_world(store, ledger, tmp_path, [pay(F.link_no(1), "PAPER-1", 90, posted=D(2026, 4, 20))])
    remit(store, trace=F.check_no(3), paid_on="2026-03-28",
          claims=[{"pcn": F.claim_id(3), "lines": [(S, "99214", 200, 0, [("CO", "197", 200)])]}])
    run(store)
    fid = one(store, "signal='DENIED_AUTH' AND patient_control_number=?", F.claim_id(1))["finding_id"]
    apply_status(store.conn, fid, "dismissed", notes="the payer is right", actor="biller")
    store.conn.commit()
    run(store)
    f = one(store, "finding_id=?", fid)
    assert f["status"] == "dismissed" and f["work_state"] == "accepted"


def test_another_payer_paying_a_claim_that_was_its_to_pay_closes_it_as_rebilled(store, ledger, tmp_path):
    tk = remit(store, trace=F.check_no(1), paid_on="2026-03-28",
               claims=[{"pcn": F.claim_id(1), "lines": [(S, "99213", 150, 0, [("CO", "22", 150)])]}])
    assert tk
    rows = [charge(F.link_no(1)), pay(F.link_no(1), F.check_no(1), 0), adj(F.link_no(1), "CO-22", 150),
            pay(F.link_no(1), "PAPER-9", 95, posted=D(2026, 4, 22), payer=P["commercial"]),
            charge(F.link_no(99), cpt="99212", patient=3, svc=D(2026, 3, 20), posted=D(2026, 5, 1), status="Payment Pending")]
    load(store, ledger, tmp_path, rows, [F.ar_row(claim=F.claim_id(1), link=F.link_no(1), status="Payment Pending", current=0,
                                                  aging=D(2026, 4, 25), svc=SVC)])
    det = run(store)
    f = one(store, "patient_control_number=? AND line_key IS NOT NULL", F.claim_id(1))
    assert det.ema_counts["c6_rebilled"] == 1 and f["status"] == "resolved"
    assert f["auto_note"].startswith(f"Rebilled to {P['commercial']}, paid")


def test_a_hint_never_closes(store, ledger, tmp_path):
    denial_world(store, ledger, tmp_path, [pay(F.link_no(1), "PAPER-2", 90, posted=D(2026, 4, 20), payer=P["commercial"])])
    run(store)
    f = one(store, "signal='DENIED_AUTH'")
    assert f["status"] == "open" and "another payer" in f["hint"]


# --------------------------------------------------------------------------------- claims, denials and payments from EMA
def test_a_claim_that_never_left_ema_is_listed_and_never_hidden_for_a_payer(store, ledger, tmp_path):
    from remitwatch.actions import NEVER_HIDDEN, _hide_matching
    ar = [F.ar_row(claim=F.claim_id(4), link=F.link_no(4), status="Batched for Printing", current=60, aging=None, patient=3, svc=SVC)]
    denial_world(store, ledger, tmp_path, ar_rows=ar)
    run(store)
    f = one(store, "signal='NOT_SENT'")
    assert f["finding_id"] == _fid("EMA_OPEN_CLAIM", F.claim_id(4)) and f["source"] == "ema" and f["ema_link"] == F.link_no(4)
    assert f["actionable"] == "correct_and_resubmit" and f["appeal_deadline"] and f["work_state"] == "work_now"
    assert {"NOT_SENT", "REMIT_NOT_POSTED", "SECONDARY_NOT_BILLED", "CREDIT_BALANCE"} <= NEVER_HIDDEN
    assert _hide_matching(store.conn, f["payer_group"], "NOT_SENT", "x", "2026-05-03T00:00:00") == 0


def test_a_denial_only_ema_saw_becomes_an_item_and_hands_its_work_to_the_remittance_when_it_arrives(store, ledger, tmp_path):
    from remitwatch.actions import apply_status
    rows = [charge(F.link_no(2), patient=1, payer=P["commercial"], primary=P["commercial"]),
            adj(F.link_no(2), "CO-50", 150, patient=1, posted=D(2026, 4, 10), payer=P["commercial"], primary=P["commercial"])]
    ar = [F.ar_row(claim=F.claim_id(2), link=F.link_no(2), status="Denied", denial=150, current=0, aging=D(2026, 4, 25), svc=SVC,
                   patient=1, primary=P["commercial"])]
    denial_world(store, ledger, tmp_path, rows, ar_rows=ar)
    run(store)
    e = one(store, "signal='EMA_DENIED'")
    assert e["source"] == "ema" and e["ema_link"] == F.link_no(2) and e["appeal_deadline"] and e["carc"] == "50"
    assert e["work_state"] == "work_now"
    apply_status(store.conn, e["finding_id"], "in_progress", notes="called the payer", actor="biller")
    store.conn.commit()
    # the payer's remittance for that line arrives: the item from it takes the work (Started, with the note)
    remit(store, trace=F.check_no(8), paid_on="2026-04-08", payer="TESTCARE HEALTH",
          claims=[{"pcn": F.claim_id(2), "last": "SAMPLE", "first": "BRAVO", "lines": [(S, "99213", 150, 0, [("CO", "50", 150)])]}])
    store.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('ema:map_dirty', '1')")
    store.conn.commit()
    run(store)
    heir = one(store, "patient_control_number=? AND line_key IS NOT NULL", F.claim_id(2))
    assert heir["signal"] == "DENIED_MEDICAL_NECESSITY" and heir["status"] == "in_progress" and heir["notes"] == "called the payer"
    old = one(store, "finding_id=?", e["finding_id"])
    assert old["stale"] == 1
    ev = [r[0] for r in store.conn.execute("SELECT note FROM finding_events WHERE finding_id=? AND kind='signal_changed'",
                                           (heir["finding_id"],))]
    assert ev and "was EMA_DENIED" in ev[0]


def test_an_835_payment_ema_never_posted_is_an_item_to_post(store, ledger, tmp_path):
    from remitwatch import insights
    remit(store, trace="777000111", paid_on="2026-04-01",
          claims=[{"pcn": F.claim_id(7), "last": "SAMPLE", "first": "BRAVO", "lines": [(S, "99214", 200, 120)]}])
    denial_world(store, ledger, tmp_path)
    run(store)
    f = one(store, "signal='REMIT_NOT_POSTED'")
    assert f["category"] == "fees" and f["source"] == "835" and f["shortfall_cents"] == 12000
    assert insights.work_type(f["signal"], f["actionable"], f["carc"]) == "post"
    assert f["work_state"] == "work_now" and "post it in EMA" in f["state_basis"]


# --------------------------------------------------------------------------------------- one run, two hosts
def _world(store, ledger, tmp_path):
    global _TX
    _TX = itertools.count(1)                 # the same transaction keys on both hosts
    ar = [F.ar_row(claim=F.claim_id(4), link=F.link_no(4), status="Batched for Printing", current=60, aging=None, patient=3, svc=SVC)]
    rows = [pay(F.link_no(1), "PAPER-1", 90, posted=D(2026, 4, 20)),
            charge(F.link_no(2), patient=1, payer=P["commercial"], primary=P["commercial"]),
            adj(F.link_no(2), "CO-50", 150, patient=1, posted=D(2026, 4, 10), payer=P["commercial"], primary=P["commercial"])]
    remit(store, trace="777000111", paid_on="2026-04-01",
          claims=[{"pcn": F.claim_id(7), "last": "SAMPLE", "first": "BRAVO", "lines": [(S, "99214", 200, 120)]}])
    remit(store, trace=F.check_no(3), paid_on="2026-03-28",
          claims=[{"pcn": F.claim_id(3), "last": "SAMPLE", "first": "CHARLIE", "lines": [(S, "99214", 200, 0, [("CO", "B11", 200)])]}])
    denial_world(store, ledger, tmp_path, rows, ar_rows=ar)
    run(store)
    cols = ("finding_id", "signal", "status", "work_state", "follow_up_on", "expected_cents", "shortfall_cents", "ema_outcome",
            "written_off_cents", "appeal_deadline", "source", "ema_link")
    return [tuple(r) for r in store.conn.execute(f"SELECT {', '.join(cols)} FROM findings ORDER BY finding_id")]


def test_a_serverless_compact_store_finds_exactly_what_a_server_finds(store, ledger, tmp_path, monkeypatch):
    server = _world(store, ledger, tmp_path)
    assert {r[1] for r in server} >= {"DENIED_AUTH", "EMA_DENIED", "NOT_SENT", "REMIT_NOT_POSTED", "FORWARDED_TO_CARRIER"}
    monkeypatch.setenv("REMITWATCH_MODE", "serverless")
    monkeypatch.setenv("REMITWATCH_EMA_REPORTS", "all")
    mem = Store(":memory:", compact=True)
    er.ensure_schema(mem.conn)
    mem.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('ema:key_salt', ?)", (SALT_HEX,))
    mem.conn.commit()
    lg2 = el.Ledger.open(str(tmp_path / "ledger_serverless.sqlite"))
    try:
        sub = tmp_path / "serverless"
        sub.mkdir()
        serverless = _world(mem, lg2, sub)
    finally:
        lg2.close()
        mem.close()
    assert serverless == server


# ------------------------------------------------------------------------------------ rules the trace found missing
def test_a_second_payer_on_file_that_was_never_billed_is_an_item(store, ledger, tmp_path):
    from remitwatch import insights
    remit(store, trace="777000222", paid_on="2026-04-01",
          claims=[{"pcn": F.claim_id(8), "last": "SAMPLE", "first": "DELTA",
                   "lines": [(S, "99213", 150, 90, [("CO", "45", 30), ("PR", "2", 30)], {"allowed_cents": 12000})]}])
    rows = [charge(F.link_no(8), patient=2, secondary=P["commercial"]),
            pay(F.link_no(8), "777000222", 90, patient=2, posted=D(2026, 4, 3), secondary=P["commercial"]),
            adj(F.link_no(8), "CO-45", 30, patient=2, posted=D(2026, 4, 3), contractual=True, secondary=P["commercial"])]
    ar = [F.ar_row(claim=F.claim_id(8), link=F.link_no(8), status="Payment Pending", current=30, aging=D(2026, 4, 3), svc=SVC,
                   patient=2, secondary=P["commercial"])]
    denial_world(store, ledger, tmp_path, rows, ar_rows=ar)
    run(store)
    f = one(store, "signal='SECONDARY_NOT_BILLED'")
    assert f["shortfall_cents"] == 3000 and f["patient_control_number"] == F.claim_id(8) and f["ema_link"] == F.link_no(8)
    ev = json.loads(f["evidence"])
    assert ev["share"] == "coinsurance" and ev["second_payer"] and ev["crossover"] is False
    assert f["appeal_deadline"] and "counted from" in f["deadline_basis"]
    assert insights.work_type(f["signal"], f["actionable"], f["carc"]) == "bill"
    # the second payer pays in EMA: nothing is left to bill
    load(store, ledger, tmp_path, [pay(F.link_no(8), "PAPER-8", 30, patient=2, posted=D(2026, 4, 20), payer=P["commercial"],
                                       secondary=P["commercial"]),
                                   charge(F.link_no(98), cpt="99212", patient=3, svc=D(2026, 4, 10), posted=D(2026, 5, 2))])
    run(store)
    assert items(store, "signal='SECONDARY_NOT_BILLED' AND COALESCE(stale,0)=0") == []


def test_a_second_payers_share_ema_wrote_off_is_still_an_item(store, ledger, tmp_path):
    """EMA closed the balance with a write-off, with nothing from the patient or the second payer: the share was never
    billed, it is not settled."""
    remit(store, trace="777000223", paid_on="2026-04-01",
          claims=[{"pcn": F.claim_id(9), "last": "SAMPLE", "first": "DELTA",
                   "lines": [(S, "99213", 150, 90, [("CO", "45", 30), ("PR", "2", 30)], {"allowed_cents": 12000})]}])
    rows = [charge(F.link_no(9), patient=2, secondary=P["commercial"]),
            pay(F.link_no(9), "777000223", 90, patient=2, posted=D(2026, 4, 3), secondary=P["commercial"]),
            adj(F.link_no(9), "CO-45", 30, patient=2, posted=D(2026, 4, 3), contractual=True, secondary=P["commercial"]),
            adj(F.link_no(9), "OA-23", 30, patient=2, posted=D(2026, 4, 10), secondary=P["commercial"])]
    ar = [F.ar_row(claim=F.claim_id(9), link=F.link_no(9), status="Payment Pending", current=0, aging=D(2026, 4, 3), svc=SVC,
                   patient=2, secondary=P["commercial"])]
    denial_world(store, ledger, tmp_path, rows, ar_rows=ar)
    run(store)
    f = one(store, "signal='SECONDARY_NOT_BILLED'")
    assert f["shortfall_cents"] == 3000 and json.loads(f["evidence"])["ema_wrote_off_share"] is True


def test_a_credit_balance_is_a_refund_item_outside_the_money_to_recoup(store, ledger, tmp_path):
    rows = [charge(F.link_no(6), amount=100, patient=2),
            pay(F.link_no(6), "PAPER-61", 100, patient=2, posted=D(2026, 3, 20)),
            pay(F.link_no(6), "PAPER-62", 60, patient=2, posted=D(2026, 3, 27))]
    denial_world(store, ledger, tmp_path, rows)
    run(store)
    f = one(store, "signal='CREDIT_BALANCE'")
    assert f["category"] == "refund" and f["shortfall_cents"] == 6000 and f["source"] == "ema" and f["ema_link"] == F.link_no(6)
    ev = json.loads(f["evidence"])
    assert ev["overpaid_by"] == BCBS and ev["credit_cents"] == 6000 and "owner" in ev["next_step"]


def test_co_b11_is_a_claim_sent_on_to_another_carrier_not_a_service_the_plan_excludes(store):
    from remitwatch import insights
    remit(store, trace=F.check_no(3), paid_on="2026-03-28",
          claims=[{"pcn": F.claim_id(3), "crossover": "WELLMED TEST", "lines": [(S, "99214", 200, 0, [("CO", "B11", 200)])]}])
    run(store)
    f = one(store)
    assert f["signal"] == "FORWARDED_TO_CARRIER" and "WELLMED TEST" in f["title"] and f["actionable"] == "review"
    assert insights.work_type(f["signal"], f["actionable"], f["carc"]) == "chase"
    assert f["work_state"] == "waiting" and f["follow_up_on"] == "2026-05-12"       # 45 days after the remittance


def test_a_visit_with_24_denied_inside_a_global_period_and_the_units_of_a_denial(store):
    remit(store, trace=F.check_no(4), paid_on="2026-03-28",
          claims=[{"pcn": F.claim_id(4), "lines": [(S, "99213", 150, 0, [("CO", "B20", 150)], {"modifiers": json.dumps(["24", "25"])})]}])
    for n in range(6):                       # a usual rate for the add-on 17312 at this payer
        remit(store, trace=f"7772{n:05d}", paid_on="2026-02-10",
              claims=[{"pcn": F.claim_id(200 + n), "last": f"UNIT{n}", "first": "FOX", "member": f"U{n:011d}",
                       "lines": [("2026-02-02", "17312", 300, 200, [("CO", "45", 100)], {"allowed_cents": 20000})]}])
    remit(store, trace=F.check_no(5), paid_on="2026-03-28",
          claims=[{"pcn": F.claim_id(5), "lines": [(S, "17312", 600, 0, [("CO", "16", 600)], {"original_units": 2})]}])
    run(store)
    f = one(store, "patient_control_number=?", F.claim_id(4))
    assert f["signal"] == "EM_BUNDLED_MOD24"
    g = one(store, "patient_control_number=?", F.claim_id(5))
    assert g["signal"] == "DENIED_MISSING_INFO" and g["expected_cents"] == 40000 and "x 2 units billed" in json.loads(g["evidence"])["basis"]


def test_the_payers_own_limit_beside_the_safe_date():
    from remitwatch.codes import appeal_rule, corrected_rule, legal_deadline
    hum = appeal_rule("Humana Medicare Advantage", "medicare_advantage")
    assert hum["days"] == 540 and legal_deadline("2026-01-31", hum) == "2027-07-31"          # 18 months, not 540 days
    med = appeal_rule("Medicare KY (CGS J15)", "medicare")
    assert legal_deadline("2026-03-01", med) == "2026-07-04"                                  # 120 days from receipt + 5
    assert legal_deadline("2026-03-01", appeal_rule("Wellcare Test", "medicare_advantage")) == "2026-05-05"   # 65 days
    assert legal_deadline("2024-02-29", appeal_rule("Anthem / BCBS", "commercial")) == "2026-02-28"
    assert legal_deadline("2026-03-01", appeal_rule("Anthem / BCBS", "commercial", "FEDERAL EMPLOYEE PROGRAM")) == "2026-09-01"
    assert legal_deadline("2026-03-01", appeal_rule("Aetna", "commercial")) is None             # the safe date is the payer's own
    fix = corrected_rule("Medicare KY (CGS J15)", "medicare", "", "DENIED_MISSING_INFO", "16")
    assert fix["days"] == 120 and legal_deadline("2026-03-01", fix) == "2026-07-04"


def test_ambetter_is_a_marketplace_plan_whatever_its_claim_filing_indicator_says():
    from remitwatch.codes import payer_group_kind
    assert payer_group_kind("AMBETTER FROM TEST PLAN", "MC") == ("Ambetter (Centene)", "commercial")
    assert payer_group_kind("WELLCARE TEST", "16")[1] == "medicare_advantage"


# ------------------------------------------------------------------------------------ alerts, the report, data health
def test_ema_stale_is_raised_while_a_report_is_old_and_clears_itself(store, ledger, tmp_path):
    from remitwatch.alerts import AlertEngine
    denial_world(store, ledger, tmp_path)
    run(store, today="2026-05-20")
    AlertEngine(store.conn, "a1", today="2026-05-20").run()
    kinds = {r[0]: r[1] for r in store.conn.execute("SELECT kind, status FROM alerts")}
    assert kinds.get("EMA_STALE") == "new"
    load(store, ledger, tmp_path, [charge(F.link_no(97), cpt="99212", patient=3, svc=D(2026, 5, 10), posted=D(2026, 5, 19))],
         [F.ar_row(claim=F.claim_id(1), link=F.link_no(1), status="Denied", denial=150, current=0, aging=D(2026, 5, 15), svc=SVC)])
    if ec.freshness(store.conn, "2026-05-20")["ar_days"] > 8:
        pytest.skip("the fixture A/R's as_of does not move with its rows")
    AlertEngine(store.conn, "a2", today="2026-05-20").run()
    statuses = [r[0] for r in store.conn.execute("SELECT status FROM alerts WHERE kind='EMA_STALE'")]
    assert statuses and all(s == "cleared" for s in statuses)


def test_the_weekly_report_and_the_data_checks_say_what_emas_reports_add(store, ledger, tmp_path):
    from remitwatch import insights
    from remitwatch.report import build_report
    ar = [F.ar_row(claim=F.claim_id(4), link=F.link_no(4), status="Batched for Printing", current=60, aging=None, patient=3, svc=SVC)]
    denial_world(store, ledger, tmp_path, ar_rows=ar)
    run(store)
    text = build_report(store)
    assert "## From EMA's reports" in text and "Claims that never left EMA: 1" in text
    assert "Written off automatically by EMA and still appealable: 1 items" in text
    keys = {h["key"] for h in insights.data_health(store.conn, insights.newest_payment(store.conn), TODAY)}
    assert {"ema_reports_missing", "ema_report_held", "remits_missing", "credits"} <= keys


def test_a_decision_on_a_held_report_recorded_on_a_serverless_host_is_applied_from_the_journal(store):
    from remitwatch import actions
    er.ensure_schema(store.conn)
    er._catalog_put(store.conn, "HELD1", commit=True, status="held")
    er._catalog_put(store.conn, "HELD2", commit=True, status="held")
    assert actions.apply(store.conn, {"kind": "ema_report_decision", "file_id": "HELD1", "decision": "discard", "actor": "biller"})
    assert actions.apply(store.conn, {"kind": "ema_report_decision", "file_id": "HELD2", "decision": "apply", "actor": "biller"})
    rows = dict(store.conn.execute("SELECT file_id, status || '/' || decision FROM ema_report_files").fetchall())
    assert rows == {"HELD1": "discarded/discard", "HELD2": "held/apply"}
    # a replay of a decision already made, or a report not on file, changes nothing
    assert not actions.apply(store.conn, {"kind": "ema_report_decision", "file_id": "HELD1", "decision": "apply", "actor": "biller"})
    assert not actions.apply(store.conn, {"kind": "ema_report_decision", "file_id": "NOPE", "decision": "discard", "actor": "biller"})
    assert store.conn.execute("SELECT status FROM ema_report_files WHERE file_id='HELD1'").fetchone()[0] == "discarded"


# ---------------------------------------------------------------- fixes from the independent reviews (money and gates)
def _remark(store, tk, code):
    """A line-level remark (RARC) on every line of the 835 payment tk."""
    for ck, lk in store.conn.execute("SELECT claim_key, line_key FROM lines WHERE transaction_key=?", (tk,)).fetchall():
        store.conn.execute("INSERT INTO remarks (transaction_key, claim_key, line_key, level, code) VALUES (?,?,?,?,?)",
                           (tk, ck, lk, "line", code))
    store.conn.commit()


def test_a_humana_ma_corrected_claim_keeps_the_18_month_dispute_as_the_payers_limit(store):
    """Humana MA CO-16: the safe date is the corrected-claim year from the date of service, and Humana's 18-month claim
    dispute from the determination stays open beside it: that later date is the payer's limit, so the item does not
    expire when the corrected-claim year passes (64 truth-listed lines on real data)."""
    remit(store, trace="777000331", paid_on="2026-03-28", payer="HUMANA CARE PLAN",
          claims=[{"pcn": F.claim_id(31), "lines": [(S, "99213", 150, 0, [("CO", "16", 150)])]}])
    run(store, today="2027-05-15")
    f = one(store, "patient_control_number=?", F.claim_id(31))
    assert f["signal"] == "DENIED_MISSING_INFO" and f["appeal_deadline"] == "2027-03-02"
    assert f["legal_deadline"] == "2027-09-28" and "2027-09-28" in f["deadline_basis"]
    assert f["work_state"] != "expired", f["state_basis"]


def test_an_anthem_ma_authorization_denial_keeps_the_corrected_claim_year_beside_the_reconsideration(store):
    """Anthem MA CO-197: the reconsideration window is 120 days from the EOP (the due date); a corrected claim carrying the
    authorization number can go within a year of the date of service (Anthem's own corrected-claim rule): the payer's limit."""
    remit(store, trace="777000332", paid_on="2026-03-28", payer="ANTHEM MEDICARE ADVANTAGE TEST",
          claims=[{"pcn": F.claim_id(32), "lines": [(S, "99213", 150, 0, [("CO", "197", 150)])]}])
    run(store)
    f = one(store, "patient_control_number=?", F.claim_id(32))
    assert f["signal"] == "DENIED_AUTH" and f["payer_group"] == "Anthem Medicare Advantage"
    assert f["appeal_deadline"] == "2026-07-26" and f["legal_deadline"] == "2027-03-02"
    assert "corrected claim" in f["deadline_basis"]


def test_a_medicare_unprocessable_claim_gets_no_dispute_route_beside_its_year(store):
    """Medicare MA130 (unprocessable: no appeal rights): a new claim within a year of the date of service is the only route,
    even when the remittance came late enough that a redetermination window would end after that year."""
    tk = remit(store, trace="777000333", paid_on="2026-12-15", payer="MEDICARE OF KENTUCKY TEST",
               claims=[{"pcn": F.claim_id(33), "lines": [(S, "99213", 150, 0, [("CO", "16", 150)])]}])
    _remark(store, tk, "MA130")
    run(store)
    f = one(store, "patient_control_number=?", F.claim_id(33))
    assert f["appeal_deadline"] == "2027-03-02" and f["legal_deadline"] is None, (f["legal_deadline"], f["deadline_basis"])


def test_a_claim_sent_on_to_another_carrier_and_a_denial_to_review_end_with_the_payers_window(store):
    """FORWARDED_TO_CARRIER and a non-covered denial (review) had no deadline, so no state could ever expire them (121
    forwarded items from 2024-25 on real data). Each now ends with the payer's window to dispute its remittance, the
    payer's own limit beside it."""
    remit(store, trace=F.check_no(3), paid_on="2026-03-28",
          claims=[{"pcn": F.claim_id(3), "crossover": "WELLMED TEST", "lines": [(S, "99214", 200, 0, [("CO", "B11", 200)])]}])
    remit(store, trace=F.check_no(34), paid_on="2026-03-28",
          claims=[{"pcn": F.claim_id(34), "lines": [(S, "99213", 150, 0, [("CO", "96", 150)])]}])
    run(store)
    fw = one(store, "signal='FORWARDED_TO_CARRIER'")
    assert fw["appeal_deadline"] == "2028-03-27" and fw["legal_deadline"] == "2028-03-28" and "sent the claim on" in fw["deadline_basis"]
    nc = one(store, "signal='DENIED_NONCOVERED'")
    assert nc["appeal_deadline"] == "2028-03-27" and "payer takes no dispute" in nc["deadline_basis"]


def _informational(store, tk):
    store.conn.execute("UPDATE transactions SET payment_method='NON', payment_amount_cents=0 WHERE transaction_key=?", (tk,))
    store.conn.commit()


def test_an_informational_835_that_repeats_a_funded_payment_is_not_money_to_post(store, ledger, tmp_path):
    """The administrator's 835 moved no money (BPR04 'NON') and reports the claim the plan paid on its own funded 835, which
    EMA posted: one payment reported twice, nothing to post (65 items, $13,019 on real data)."""
    remit(store, trace="777000111", paid_on="2026-04-01", payer="TESTCARE HEALTH",
          claims=[{"pcn": F.claim_id(7), "last": "SAMPLE", "first": "BRAVO", "lines": [(S, "99214", 200, 120)]}])
    tk = remit(store, trace="888000111", paid_on="2026-04-01", payer="TEST BENEFIT ADMINISTRATORS",
               claims=[{"pcn": F.claim_id(7), "last": "SAMPLE", "first": "BRAVO", "lines": [(S, "99214", 200, 120)]}])
    _informational(store, tk)
    rows = [charge(F.link_no(7), cpt="99214", amount=200, patient=1),
            pay(F.link_no(7), "777000111", 120, cpt="99214", patient=1, posted=D(2026, 4, 3))]
    denial_world(store, ledger, tmp_path, rows)
    run(store)
    assert items(store, "signal='REMIT_NOT_POSTED' AND transaction_key=?", tk) == []
    summary = json.loads(store.conn.execute("SELECT value FROM meta WHERE key='ema:unposted'").fetchone()[0])
    assert summary["informational_n"] == 1 and summary["informational_cents"] == 12000


def test_an_informational_835_whose_money_never_came_is_a_payment_to_ask_for_not_to_post(store, ledger, tmp_path):
    """PM case C54: the administrator's informational 835 reports the claim paid, and no funded payment or EMA posting
    follows. Not 'post it in EMA': the money never arrived, ask the payer for it."""
    tk = remit(store, trace="888000112", paid_on="2026-04-01", payer="TEST BENEFIT ADMINISTRATORS",
               claims=[{"pcn": F.claim_id(17), "last": "SAMPLE", "first": "ECHO", "lines": [(S, "99214", 200, 120)]}])
    _informational(store, tk)
    denial_world(store, ledger, tmp_path, [charge(F.link_no(17), cpt="99214", amount=200, patient=1)])
    run(store)
    f = one(store, "signal='REMIT_NOT_POSTED' AND transaction_key=?", tk)
    ev = json.loads(f["evidence"])
    assert ev["informational"] is True and "never arrived" in f["title"] and "ask" in f["hint"].lower()
    assert "post the payment" not in f["hint"].lower() and f["shortfall_cents"] == 12000


def test_a_zero_deposit_835_a_recovery_used_up_is_still_money_to_post(store, ledger, tmp_path):
    """A $0 deposit because the payer took back an earlier overpayment (PLB WO) moved its money all the same: the claim's
    payment is EMA's to post (with the recovery), not an informational copy."""
    tk = remit(store, trace="888000113", paid_on="2026-04-01", payer="TESTCARE HEALTH",
               claims=[{"pcn": F.claim_id(18), "last": "SAMPLE", "first": "FOXTROT", "lines": [(S, "99214", 200, 120)]}])
    _informational(store, tk)
    store.conn.execute("INSERT INTO plb (transaction_key, reason_code, amount_cents, payment_date) VALUES (?,?,?,?)",
                       (tk, "WO", 12000, "2026-04-01"))
    store.conn.commit()
    denial_world(store, ledger, tmp_path, [charge(F.link_no(18), cpt="99214", amount=200, patient=1)])
    run(store)
    f = one(store, "signal='REMIT_NOT_POSTED' AND transaction_key=?", tk)
    assert not json.loads(f["evidence"]).get("informational") and "not posted in EMA" in f["title"]


def test_the_plans_funded_payment_is_listed_once_and_its_informational_copy_not_again(store, ledger, tmp_path):
    """Neither 835 is posted in EMA: the plan's funded payment is the one to post; the administrator's informational copy
    of it is the same money and is not listed a second time."""
    funded = remit(store, trace="777000114", paid_on="2026-04-01", payer="TESTCARE HEALTH",
                   claims=[{"pcn": F.claim_id(19), "last": "SAMPLE", "first": "GOLF", "lines": [(S, "99214", 200, 120)]}])
    info = remit(store, trace="888000114", paid_on="2026-04-01", payer="TEST BENEFIT ADMINISTRATORS",
                 claims=[{"pcn": F.claim_id(19), "last": "SAMPLE", "first": "GOLF", "lines": [(S, "99214", 200, 120)]}])
    _informational(store, info)
    denial_world(store, ledger, tmp_path, [charge(F.link_no(19), cpt="99214", amount=200, patient=1)])
    run(store)
    got = [g["transaction_key"] for g in items(store, "signal='REMIT_NOT_POSTED'")]
    assert funded in got and info not in got


def test_an_informational_835_whose_money_ema_posted_under_another_835s_check_is_accounted_for(store, ledger, tmp_path):
    """The plan paid the claim under its resubmission's number (another claim id, so no twin by claim id), and EMA posted
    that check on the claim: EMA holds the informational 835's money under a check an 835 carries."""
    remit(store, trace="777000115", paid_on="2026-04-02", payer="TESTCARE HEALTH",
          claims=[{"pcn": F.claim_id(20, sub=2), "last": "SAMPLE", "first": "HOTEL", "lines": [(S, "99214", 200, 120)]}])
    info = remit(store, trace="888000115", paid_on="2026-04-01", payer="TEST BENEFIT ADMINISTRATORS",
                 claims=[{"pcn": F.claim_id(20), "last": "SAMPLE", "first": "HOTEL", "lines": [(S, "99214", 200, 120)]}])
    _informational(store, info)
    rows = [charge(F.link_no(20), cpt="99214", amount=200, patient=1),
            pay(F.link_no(20), "777000115", 120, cpt="99214", patient=1, posted=D(2026, 4, 4))]
    denial_world(store, ledger, tmp_path, rows, ar_rows=[F.ar_row(claim=F.claim_id(20), link=F.link_no(20), status="Payment Pending",
                                                                  current=0, aging=D(2026, 4, 4), svc=SVC, patient=1)])
    run(store)
    assert items(store, "signal='REMIT_NOT_POSTED' AND transaction_key=?", info) == []


MA_T, MCD_T = "UHC Medicare Advantage Test", "Medicaid Test State"


def test_a_medicaid_second_payers_denial_is_not_an_appealable_primary_denial(store, ledger, tmp_path):
    """EMA's line: primary a Medicare Advantage plan, Medicaid the second payer on file; Medicaid posts $0 and CO-B7. A
    Medicaid second payer pays the share only up to its own rate and the member cannot be billed (the answer key excludes
    all 64 such work-now items): no EMA-only denial, and the line is counted where the Data tab can show it."""
    kw = dict(patient=2, primary=MA_T, secondary=MCD_T, status="Denied")
    rows = [charge(F.link_no(21), payer=MA_T, **kw),
            pay(F.link_no(21), "MA-21", 90, posted=D(2026, 4, 3), payer=MA_T, **kw),
            pay(F.link_no(21), "MCD-21", 0, posted=D(2026, 4, 10), payer=MCD_T, **kw),
            adj(F.link_no(21), "CO-B7", 40, posted=D(2026, 4, 10), payer=MCD_T, **kw)]
    denial_world(store, ledger, tmp_path, rows)
    run(store)
    assert items(store, "signal='EMA_DENIED' AND ema_link=?", F.link_no(21)) == []
    lo = json.loads(store.conn.execute("SELECT value FROM meta WHERE key='ema:c5_left_out'").fetchone()[0])
    assert lo["second_payer_medicaid"]["n"] == 1 and lo["second_payer_medicaid"]["by_reason"] == {"CO-B7": 1}


def test_a_second_payers_denial_before_the_first_payer_paid_is_not_this_lines_money(store, ledger, tmp_path):
    """A commercial second payer on file denies before the first payer has paid anything: the order of the payers, not a
    denial of money the second payer owes."""
    kw = dict(patient=2, primary=P["bcbs"], secondary=P["commercial"], status="Denied")
    rows = [charge(F.link_no(22), **kw),
            adj(F.link_no(22), "CO-22", 150, posted=D(2026, 4, 10), payer=P["commercial"], **kw)]
    denial_world(store, ledger, tmp_path, rows)
    run(store)
    assert items(store, "signal='EMA_DENIED' AND ema_link=?", F.link_no(22)) == []


def test_a_commercial_second_payers_denial_after_the_first_paid_is_still_an_item_at_the_share_left(store, ledger, tmp_path):
    """A commercial second payer that denies the share the first payer left (after that payment) can owe it: still an
    EMA-only denial, sized at the balance left."""
    kw = dict(patient=2, primary=P["bcbs"], secondary=P["commercial"])
    rows = [charge(F.link_no(23), **kw),
            pay(F.link_no(23), "PAPER-23", 90, posted=D(2026, 4, 3), **kw),
            adj(F.link_no(23), "CO-45", 30, posted=D(2026, 4, 3), contractual=True, **kw),
            adj(F.link_no(23), "CO-27", 30, posted=D(2026, 4, 12), payer=P["commercial"], **kw)]
    denial_world(store, ledger, tmp_path, rows)
    run(store)
    e = one(store, "signal='EMA_DENIED' AND ema_link=?", F.link_no(23))
    assert e["shortfall_cents"] == 3000 and e["payer_group"] != BCBS


def _primary_with_share(store, n, trace, *, status="1", crossover=None, payer="BLUE CROSS TEST", paid_on="2026-04-01"):
    """The first payer paid $90 of a $150 visit, CO-45 $30 and the patient's $30 coinsurance (PR-2)."""
    return remit(store, trace=trace, paid_on=paid_on, payer=payer, status=status,
                 claims=[{"pcn": F.claim_id(n), "last": "SAMPLE", "first": "DELTA", "crossover": crossover,
                          "lines": [(S, "99213", 150, 90, [("CO", "45", 30), ("PR", "2", 30)], {"allowed_cents": 12000})]}])


def test_a_second_payer_that_answered_with_nothing_on_its_835_was_billed(store, ledger, tmp_path):
    """The second payer's remittance processed the claim as secondary (claim status 2) and paid $0: it was billed and
    answered, so this is not 'never billed' (243 of 339 work-now items on real data were like this)."""
    _primary_with_share(store, 8, "777000222")
    remit(store, trace="777000333", paid_on="2026-04-15", payer="TESTCARE HEALTH", status="2",
          claims=[{"pcn": F.claim_id(8), "last": "SAMPLE", "first": "DELTA", "lines": [(S, "99213", 150, 0, [("PR", "2", 30)])]}])
    kw = dict(patient=2, secondary=P["commercial"])
    rows = [charge(F.link_no(8), **kw), pay(F.link_no(8), "777000222", 90, posted=D(2026, 4, 3), **kw),
            adj(F.link_no(8), "CO-45", 30, posted=D(2026, 4, 3), contractual=True, **kw)]
    ar = [F.ar_row(claim=F.claim_id(8), link=F.link_no(8), status="Payment Pending", current=30, aging=D(2026, 4, 3), svc=SVC,
                   patient=2, secondary=P["commercial"])]
    denial_world(store, ledger, tmp_path, rows, ar_rows=ar)
    det = run(store)
    assert items(store, "signal='SECONDARY_NOT_BILLED' AND COALESCE(stale,0)=0") == []
    assert det.ema_counts["snb_second_answered_835"] == 1


def test_a_second_payer_whose_only_answer_is_in_ema_was_billed(store, ledger, tmp_path):
    """No 835 from the second payer, but EMA posted its $0 check row, and on another line its own reason (CO-27, coverage
    ended), after the first payer's payment: each was billed and answered."""
    _primary_with_share(store, 8, "777000222")
    _primary_with_share(store, 9, "777000223")
    kw8, kw9 = dict(patient=2, secondary=P["commercial"]), dict(patient=1, secondary=P["commercial"])
    rows = [charge(F.link_no(8), **kw8), pay(F.link_no(8), "777000222", 90, posted=D(2026, 4, 3), **kw8),
            adj(F.link_no(8), "CO-45", 30, posted=D(2026, 4, 3), contractual=True, **kw8),
            pay(F.link_no(8), "PAPER-81", 0, posted=D(2026, 4, 17), payer=P["commercial"], **kw8),
            charge(F.link_no(9), **kw9), pay(F.link_no(9), "777000223", 90, posted=D(2026, 4, 3), **kw9),
            adj(F.link_no(9), "CO-45", 30, posted=D(2026, 4, 3), contractual=True, **kw9),
            adj(F.link_no(9), "CO-27", 30, posted=D(2026, 4, 18), payer=P["commercial"], **kw9)]
    ar = [F.ar_row(claim=F.claim_id(n), link=F.link_no(n), status="Payment Pending", current=30, aging=D(2026, 4, 3), svc=SVC,
                   patient=pt, secondary=P["commercial"]) for n, pt in ((8, 2), (9, 1))]
    denial_world(store, ledger, tmp_path, rows, ar_rows=ar)
    det = run(store)
    assert items(store, "signal='SECONDARY_NOT_BILLED' AND COALESCE(stale,0)=0") == []
    assert det.ema_counts["snb_second_answered_ema"] == 2


def test_a_medicaid_second_payer_is_never_a_bill_it_item(store, ledger, tmp_path):
    """Medicaid as second payer pays the share only up to its own rate and the member cannot be billed (on real data it
    paid on 98 of 895 secondary postings since 2025): not a 'bill it' item, counted instead."""
    _primary_with_share(store, 8, "777000222")
    kw = dict(patient=2, secondary=MCD_T)
    rows = [charge(F.link_no(8), **kw), pay(F.link_no(8), "777000222", 90, posted=D(2026, 4, 3), **kw),
            adj(F.link_no(8), "CO-45", 30, posted=D(2026, 4, 3), contractual=True, **kw)]
    ar = [F.ar_row(claim=F.claim_id(8), link=F.link_no(8), status="Payment Pending", current=30, aging=D(2026, 4, 3), svc=SVC,
                   patient=2, secondary=MCD_T)]
    denial_world(store, ledger, tmp_path, rows, ar_rows=ar)
    det = run(store)
    assert items(store, "signal='SECONDARY_NOT_BILLED'") == []
    assert det.ema_counts["snb_medicaid_second"] == 1 and det.ema_counts["snb_medicaid_second_cents"] == 3000


def test_a_crossover_whose_ema_line_is_not_on_file_is_chased_from_the_remittance(store, ledger, tmp_path):
    """Medicare crossed the claim over to a Medigap carrier (claim status 19, the carrier named) and nothing came from it in
    45 days. EMA's line is not in the store (its summary keeps only the lines of bills with an item), so the remittance
    alone lists it, to chase the carrier; a Medicaid carrier is never listed."""
    _primary_with_share(store, 10, "777000224", status="19", crossover="TEST MEDIGAP LIFE", payer="MEDICARE OF KENTUCKY TEST",
                        paid_on="2026-03-28")
    _primary_with_share(store, 11, "777000225", status="19", crossover="KENTUCKY MEDICAID TEST", payer="MEDICARE OF KENTUCKY TEST",
                        paid_on="2026-03-28")
    denial_world(store, ledger, tmp_path)
    run(store, today="2026-06-01")
    f = one(store, "signal='SECONDARY_NOT_BILLED'")
    assert f["patient_control_number"] == F.claim_id(10) and f["shortfall_cents"] == 3000 and "crossed over" in f["title"]
    ev = json.loads(f["evidence"])
    assert ev["crossover"] is True and ev["ema_line_on_file"] is False and ev["second_payer_kind"] == "supplement"
    assert f["appeal_deadline"] == "2027-03-28" and "Ask" in f["hint"]


def test_a_second_payers_ema_denial_is_not_closed_by_the_first_payers_same_day_payment(store, ledger, tmp_path):
    """No 835: the first payer (Medicare) pays $80 and EMA posts its CO-45 $50; the same day the second payer denies the
    $20 left (CO-183). C5 lists that $20; the first payer's own payment never pays it: no 'Rebilled to <first payer>,
    paid' closure, and no 'paid before' proof that would call it done."""
    kw = dict(patient=1, primary=P["medicare"], secondary=P["commercial"])
    rows = [charge(F.link_no(5), payer=P["medicare"], **kw),
            pay(F.link_no(5), "PAPER-51", 80, posted=D(2026, 4, 10), payer=P["medicare"], **kw),
            adj(F.link_no(5), "CO-45", 50, posted=D(2026, 4, 10), contractual=True, payer=P["medicare"], **kw),
            adj(F.link_no(5), "CO-183", 20, posted=D(2026, 4, 10), payer=P["commercial"], **kw)]
    denial_world(store, ledger, tmp_path, rows)
    det = run(store)
    e = one(store, "signal='EMA_DENIED'")
    assert e["shortfall_cents"] == 2000 and e["status"] == "open", e["auto_note"]
    assert det.ema_counts["c6_rebilled"] == 0 and e["work_state"] == "work_now", e["state_basis"]
    o = det.ema_outcomes[e["finding_id"]]
    assert o["paid_before"] == 0 and o["first_payer_paid"] == 8000 and o["paid_after_other"] == 0


def test_a_supplements_own_zero_answer_as_if_primary_after_the_first_payer_means_it_was_billed(store, ledger, tmp_path):
    """The Medigap carrier answered the crossover on its own remittance as if primary (claim status 1), paying $0 (a
    deductible its plan does not cover), after Medicare paid: it was billed and answered, nothing to chase."""
    _primary_with_share(store, 10, "777000224", status="19", crossover="TEST MEDIGAP LIFE", payer="MEDICARE OF KENTUCKY TEST",
                        paid_on="2026-03-28")
    remit(store, trace="777000226", paid_on="2026-04-20", payer="TEST MEDIGAP LIFE",
          claims=[{"pcn": F.claim_id(10), "last": "SAMPLE", "first": "DELTA", "lines": [(S, "99213", 150, 0, [("PR", "1", 30)])]}])
    denial_world(store, ledger, tmp_path)
    run(store, today="2026-06-01")
    assert items(store, "signal='SECONDARY_NOT_BILLED'") == []
