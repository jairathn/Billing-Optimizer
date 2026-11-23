"""
The one place an item's state is decided (remitwatch.states, amendment 13.1): work now, waiting with its follow-up day,
done with its proof, accepted, expired, can't be checked; a person's status winning over the automatic state; the payer's
answer time from the archive; the dated work on an item; what an A/R billing note says.

Every row is invented (tests/ema_fixtures.py for EMA's side). The 835 side is written straight into the store's
remittance tables; the EMA side goes through the real readers, the posting ledger and its refresh. tests/test_story.py
imports the builders below.
"""
from __future__ import annotations

import datetime as dt
import itertools
import json

import pytest

from remitwatch import detect, ema_checks as ec, ema_ledger as el, ema_reports as er, states
from remitwatch.store import Store
from tests import ema_fixtures as F

D = dt.date
P = F.PAYERS
SVC = D(2026, 3, 2)
S = SVC.isoformat()
SALT_HEX = "ab" * 32
BCBS = "Anthem / BCBS"          # the group of the 835 payer BLUE CROSS TEST and of EMA's P["bcbs"]
TODAY = "2026-05-03"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(er, "CLAIM_HOST_SUFFIX", F.HOST_SUFFIX)
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    for k in ("VERCEL", "REMITWATCH_EMA_REPORTS", "REMITWATCH_EMA_CLAIM_URL", "REMITWATCH_EMA_PAYER_ALIASES",
              "REMITWATCH_EMA_LEDGER", "REMITWATCH_EMA_REPORT_KEEP"):
        monkeypatch.delenv(k, raising=False)
    ec._LAST_POST.clear()
    states._RESPONSE_CACHE.clear()
    states._RECOVERY_CACHE.clear()


class Log(list):
    def __call__(self, *a):
        self.append(" ".join(str(x) for x in a))


def new_store(path: str) -> Store:
    s = Store(path)
    er.ensure_schema(s.conn)
    detect.ensure_schema(s.conn)
    s.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('ema:key_salt', ?)", (SALT_HEX,))
    s.conn.commit()
    return s


@pytest.fixture
def store(tmp_path):
    s = new_store(str(tmp_path / "store.sqlite"))
    yield s
    s.close()


@pytest.fixture
def ledger(tmp_path):
    lg = el.Ledger.open(str(tmp_path / "ema_ledger.sqlite"))
    yield lg
    lg.close()


_N = itertools.count(1)
_TX = itertools.count(1)


# ------------------------------------------------------------------------------------------------------- builders
def ln(code="99213", charge=150, paid=0, adj=(), *, svc=S, allowed=None, units=1, ounits=None, lcn=None, ocode=None, rmk=(),
       mods=None) -> dict:
    """One 835 service line: money in dollars, adjustments as (group, reason, dollars)."""
    return {"svc": svc, "code": code, "charge": charge, "paid": paid, "adj": list(adj), "allowed": allowed, "units": units,
            "ounits": ounits, "lcn": lcn, "ocode": ocode, "rmk": list(rmk), "mods": mods}


def remit(store, *, trace, paid_on, claims, payer="BLUE CROSS TEST", method="ACH", plb=()) -> str:
    """One 835 payment written straight into the store. claims: dicts with pcn, lines (ln(...)), and optionally status,
    cfi, pccn, received, freq, crossover, patient_id. plb: (reason, reference, dollars). Returns the payment's key."""
    c = store.conn
    tk = f"TX{next(_TX):05d}"
    total = sum(round(x["paid"] * 100) for cl in claims for x in cl["lines"])
    c.execute("INSERT INTO transactions (transaction_key, trace_number, payment_date, payer_name, superseded, payment_method, "
              "claim_paid_total_cents, payment_amount_cents) VALUES (?,?,?,?,0,?,?,?)", (tk, trace, paid_on, payer, method, total, total))
    for i, cl in enumerate(claims):
        ck = f"{tk}-{i}"
        lines = cl["lines"]
        c.execute("""INSERT INTO claims (claim_key, transaction_key, patient_control_number, patient_last, patient_first, payment_date,
                     trace_number, statement_from, claim_status_code, charge_cents, paid_cents, payer_name, claim_filing_indicator,
                     payer_claim_control_number, received_date, claim_frequency_code, crossover_carrier_name, patient_id)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (ck, tk, cl["pcn"], "TESTPATIENT", "ALPHA", paid_on, trace, lines[0]["svc"], cl.get("status", "1"),
                   sum(round(x["charge"] * 100) for x in lines), sum(round(x["paid"] * 100) for x in lines), payer,
                   cl.get("cfi", "12"), cl.get("pccn"), cl.get("received"), cl.get("freq", "1"), cl.get("crossover"),
                   cl.get("patient_id", "MEMBER-TEST-1")))
        for j, x in enumerate(lines):
            lk = f"{ck}-{j}"
            c.execute("""INSERT INTO lines (line_key, claim_key, transaction_key, procedure_code, original_procedure_code, modifiers,
                         service_date, charge_cents, paid_cents, allowed_cents, paid_units, original_units, line_control_number,
                         patient_control_number, payment_date, payer_name, claim_status_code) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (lk, ck, tk, x["code"], x["ocode"], x["mods"], x["svc"], round(x["charge"] * 100), round(x["paid"] * 100),
                       None if x["allowed"] is None else round(x["allowed"] * 100), x["units"], x["ounits"], x["lcn"], cl["pcn"],
                       paid_on, payer, cl.get("status", "1")))
            for g, r, amt in x["adj"]:
                c.execute("INSERT INTO adjustments (transaction_key, claim_key, line_key, level, group_code, reason_code, amount_cents) "
                          "VALUES (?,?,?,?,?,?,?)", (tk, ck, lk, "line", g, r, round(amt * 100)))
            for code in x["rmk"]:
                c.execute("INSERT INTO remarks (transaction_key, claim_key, line_key, level, qualifier, code) VALUES (?,?,?,?,?,?)",
                          (tk, ck, lk, "line", "HE", code))
    for reason, ref, amt in plb:
        c.execute("INSERT INTO plb (transaction_key, reason_code, reference, amount_cents, payer_name, payment_date, trace_number) "
                  "VALUES (?,?,?,?,?,?,?)", (tk, reason, ref, round(amt * 100), payer, paid_on, trace))
    c.commit()
    return tk


def line_key(store, tk, i=0, j=0) -> str:
    return f"{tk}-{i}-{j}"


def item(store, **kw) -> dict:
    """An item in the findings table as the detection run saves it (a denial of 99213 by Blue Cross by default)."""
    f = {"finding_id": "F1", "signal": "DENIED_AUTH", "category": "denial", "severity": "medium", "actionable": "appeal",
         "title": "Denied: no authorization", "line_key": None, "claim_key": None, "transaction_key": None,
         "patient_control_number": F.claim_id(1), "payer_name": "BLUE CROSS TEST", "payer_group": BCBS, "payer_kind": "commercial",
         "service_date": S, "payment_date": "2026-03-28", "year": "2026", "procedure_code": "99213", "modifiers": None,
         "original_procedure_code": None, "billed_code": "99213", "charge_cents": 15000, "allowed_cents": None, "paid_cents": 0,
         "expected_cents": 9000, "shortfall_cents": 9000, "carc": "197", "rarc": None, "evidence": "{}", "status": "open",
         "stale": 0, "appeal_deadline": "2027-12-31", "deadline_basis": "the payer's window", "first_seen": "2026-04-01"}
    f.update(kw)
    if isinstance(f.get("evidence"), dict):
        f["evidence"] = json.dumps(f["evidence"])
    cols = [r[1] for r in store.conn.execute("PRAGMA table_info(findings)")]
    use = [k for k in f if k in cols]
    store.conn.execute(f"INSERT OR REPLACE INTO findings ({', '.join(use)}) VALUES ({', '.join('?' * len(use))})", [f[k] for k in use])
    store.conn.commit()
    return f


def event(store, fid, kind, *, at, to=None, frm=None, actor="Biller One", note=None, cents=None):
    store.conn.execute("INSERT INTO finding_events (finding_id, at, kind, from_status, to_status, amount_cents, actor, note) "
                       "VALUES (?,?,?,?,?,?,?,?)", (fid, at, kind, frm, to, cents, actor, note))
    store.conn.commit()


def charge(link, cpt="99213", amount=150, patient=0, svc=SVC, posted=None, **kw):
    return F.production_row(link=link, cpt=cpt, charges=amount, patient=patient, svc=svc, posted=posted or svc, **kw)


def pay(link, check, amount, cpt="99213", patient=0, svc=SVC, posted=D(2026, 3, 31), payer=P["bcbs"], **kw):
    return F.production_row(link=link, cpt=cpt, ins_paid=amount, check=check, patient=patient, svc=svc, posted=posted, payer=payer, **kw)


def adj(link, reason, amount, cpt="99213", patient=0, svc=SVC, posted=D(2026, 3, 31), contractual=False, payer=P["bcbs"], **kw):
    money = {"contractual": amount} if contractual else {"noncontractual": amount}
    return F.production_row(link=link, cpt=cpt, reason=reason, patient=patient, svc=svc, posted=posted, payer=payer, **money, **kw)


def patient_pay(amount, cpt="99213", patient=0, svc=SVC, posted=D(2026, 4, 15)):
    return F.production_row(payer="Patient", link=None, cpt=cpt, patient=patient, svc=svc, posted=posted, pat_paid=amount)


def later(link=99, posted=D(2026, 5, 1)):
    """A charge on another claim that carries Production's as_of to `posted` (EMA's report reaches that day)."""
    return charge(F.link_no(link), cpt="99212", patient=3, svc=D(2026, 3, 20), posted=posted, status="Payment Pending")


def load(store, ledger, tmp_path, rows, ar_rows=None):
    """Read an A/R (when given) and a Production file through the real readers, refresh the summaries (Parts A and B) and
    classify the checks, as a cycle does."""
    if ar_rows is not None:
        p = F.write(str(tmp_path / f"ar{next(_N)}.xlsx"), F.ar_xlsx(ar_rows))
        r = er.ingest_file(store, p, via="test", log=Log())
        assert r["status"] == "current", r
    if rows:
        p = F.write(str(tmp_path / f"prod{next(_N)}.csv"), F.production_csv(rows))
        r = er.ingest_file(store, p, via="test", ledger=ledger, log=Log())
        assert r["status"] == "current", r
    el.refresh(store, ledger, parts="AB", log=Log())
    ec.classify_checks(store.conn)


def denied(store, *, trace=None, paid_on="2026-03-28", code="99213", charge_=150, reason=("CO", "197"), pcn=None, lcn="1",
           received="2026-03-05", payer="BLUE CROSS TEST", cfi="12", pccn="PCN-A", status="1") -> str:
    """The item's own denial: an 835 answering the claim's line with $0 and one reason."""
    return remit(store, trace=trace or F.check_no(1), paid_on=paid_on, payer=payer,
                 claims=[{"pcn": pcn or F.claim_id(1), "status": status, "cfi": cfi, "pccn": pccn, "received": received,
                          "lines": [ln(code, charge_, 0, [(reason[0], reason[1], charge_)], lcn=lcn)]}])


def state(store, fid="F1", today=TODAY, **kw) -> dict:
    f = dict(store.conn.execute("SELECT * FROM findings WHERE finding_id=?", (fid,)).fetchone())
    return states.state_of(store.conn, f, today=today, **kw)


# ================================================================================================ the billing note
def test_classify_note_reads_kinds_dates_and_allow_days_without_keeping_words():
    r = states.classify_note("10/14 spk w/ rep ref# 123, reconsideration overturned, allow 30 days for new EOB", as_of="2026-11-20")
    assert {"call", "appeal", "reprocess", "follow_up"} <= set(r["kinds"])
    assert r["date"] == "2026-10-14" and r["wait_days"] == 30
    # a date with no year that would fall after the pull is last year's; a year written is kept; weeks count as days
    assert states.classify_note("12/20 refiled corrected claim, allow 2 weeks", as_of="2026-03-01")["date"] == "2025-12-20"
    assert states.classify_note("01/05/2026 resubmitted", as_of="2026-03-01") == {"kinds": ["resubmitted"], "date": "2026-01-05",
                                                                                    "wait_days": None}
    assert states.classify_note("allow 2 weeks", as_of="2026-03-01")["wait_days"] == 14
    # a decision not to appeal drops the appeal kind; an empty note says nothing
    r = states.classify_note("reviewed, decided not to appeal: correct per contract", as_of="2026-03-01")
    assert "appeal" not in r["kinds"] and "no_appeal" in r["kinds"]
    assert states.classify_note("", as_of="2026-03-01") == {"kinds": [], "date": None, "wait_days": None}
    assert states.classify_note(None) == {"kinds": [], "date": None, "wait_days": None}


def test_a_sentence_ends_at_its_period_never_inside_an_abbreviation_or_a_number():
    """The first sentence of a basis or an argument (states.sentence_end): never cut at 'ch. 12 sec. 40.2', 'e.g.' or a
    period before a lower-case word; a semicolon ends a clause only when asked."""
    t = "Modifier 57 (CMS Claims Processing Manual ch. 12 sec. 40.2). If a biopsy visit made the decision, let it go."
    assert t[:states.sentence_end(t)] == "Modifier 57 (CMS Claims Processing Manual ch. 12 sec. 40.2)"
    t = "Send the note, e.g. The operative note. Then wait."
    assert t[:states.sentence_end(t)] == "Send the note, e.g. The operative note"
    t = "Medicare Part B: redetermination within 120 days (42 CFR 405.942); a reopening takes no documents. More."
    assert t[:states.sentence_end(t)] == "Medicare Part B: redetermination within 120 days (42 CFR 405.942)"
    assert t[:states.sentence_end(t, semicolon=False)] == "Medicare Part B: redetermination within 120 days (42 CFR 405.942); a reopening takes no documents"
    assert states.sentence_end("One sentence with no end") is None
    t = "Billed under version 2. lower-case words after a period go on. Then the end."
    assert t[:states.sentence_end(t)] == "Billed under version 2. lower-case words after a period go on"
    assert states._short_label("Commercial plans: 180 days is the common default. Check the contract.") == "Commercial plans: 180 days is the common default"


def test_action_kind_reads_emas_claim_actions():
    assert states.action_kind("Initial Appeal Electronic") == "appeal"
    assert states.action_kind("Submitted Corrected Claim Portal") == "corrected"
    assert states.action_kind("Sent Medical Records Fax") == "records"
    assert states.action_kind("Claim Reprocessing") == "reprocess"
    assert states.action_kind("Claim Billed To Secondary Tertiary Insurance") == "secondary"
    assert states.action_kind("Claim In Process") == "call"
    assert states.action_kind("None") is None and states.action_kind("") is None and states.action_kind("Other") is None


# ================================================================================================ reversal pairs
def _rec(key, date, status, paid, adj, *, received="2026-03-05", charge=150, code="99213", payer="BLUE CROSS TEST"):
    return {"line_key": key, "claim_key": key, "pcn": F.claim_id(1), "status": status, "payer": payer, "date": date, "code": code,
            "charge": charge if status != "22" else -charge, "paid": paid, "adj": adj, "pccn": "P1", "received": received, "cfi": "12"}


def test_a_reversal_pairs_with_the_answer_it_reverses_not_the_new_answer_on_the_same_remittance():
    orig = _rec("A", "2026-03-28", "1", 0, [("CO", "97", 150)])
    deny = _rec("B", "2026-05-01", "4", 0, [("CO", "129", 150)])
    again = _rec("C", "2026-06-01", "1", 0, [("PR", "1", 60), ("CO", "45", 90)])
    rev = _rec("R", "2026-06-01", "22", 0, [("CO", "97", -150)])
    recs = [orig, deny, again, rev]
    states._pair_reversals(recs)
    assert rev["reverses"] == "A" and orig["reversed_by"] == "R"
    assert again["reversed_by"] is None and deny["reversed_by"] is None
    assert [x["line_key"] for x in states.standing_rows(recs)] == ["B", "C"]


def test_a_reversal_under_another_product_of_the_same_payer_pairs_with_the_answer_it_cancels():
    """needs_R section 8: the payer moved the claim to its Medicare Advantage product: the reversal carries the new filing
    indicator (16) and comes with the new answer on its own remittance. It cancels the first answer (the same payer by
    name, the reasons it negates exactly), never the new one, as Results pairs it."""
    first = dict(_rec("A", "2026-04-01", "1", 5000, [("CO", "45", 10000)]), cfi="12")
    rev = dict(_rec("R", "2026-05-20", "22", -5000, [("CO", "45", -10000)]), cfi="16")
    new = dict(_rec("B", "2026-05-20", "1", 6000, [("CO", "45", 9000)]), cfi="16", pcn=F.claim_id(1, sub=3))
    assert states.row_group(first)[0] != states.row_group(rev)[0]
    recs = [first, rev, new]
    states._pair_reversals(recs)
    assert rev["reverses"] == "A" and first["reversed_by"] == "R" and new["reversed_by"] is None


def test_a_reversal_of_a_claim_the_archive_lacks_stays_unpaired():
    """The reversal repeats the received date of the claim it reverses; a line received after it is a new claim."""
    deny = _rec("A", "2026-03-28", "1", 0, [("CO", "226", 150)], received="2026-03-20")
    paid = _rec("B", "2026-04-10", "2", 3000, [("OA", "23", 12000)], received="2026-04-02")
    rev = _rec("R", "2026-04-10", "22", -3000, [("OA", "23", -12000)], received="2026-01-15")
    recs = [deny, paid, rev]
    states._pair_reversals(recs)
    assert rev["reverses"] is None and paid["reversed_by"] is None and deny["reversed_by"] is None


# ================================================================================================ the payer's answer time
def test_response_days_is_the_payers_90th_percentile_from_receipt_to_remittance(store):
    # 25 Blue Cross answers 10..34 days after the payer received the claim: p90 is 32 days
    for i in range(25):
        remit(store, trace=f"T{i:03d}", paid_on=(D(2026, 2, 1) + dt.timedelta(days=10 + i)).isoformat(),
              claims=[{"pcn": F.claim_id(100 + i), "received": "2026-02-01", "lines": [ln("99213", 100, 50)]}])
    # 3 answers from another payer: too few, so its items use the all-payer figure
    for i in range(3):
        remit(store, trace=f"U{i}", paid_on="2026-03-30", payer="TESTCARE HEALTH",
              claims=[{"pcn": F.claim_id(200 + i), "received": "2026-03-01", "lines": [ln("99213", 100, 50)]}])
    t = states.save_response_days(store.conn)
    assert t["groups"][BCBS] == {"p50": 22, "p90": 32, "n": 25}
    assert states.response_days(store.conn, BCBS) == 32
    assert states.response_days(store.conn, "Testcare Health") == t["all"]["p90"]
    # by the kind of work: a week more for a claim to reach the payer; an appeal's answer at least the payer's own decision time
    assert states.response_days(store.conn, BCBS, "corrected") == 39
    assert states.response_days(store.conn, BCBS, "appeal", "commercial") == 32
    assert states.response_days(store.conn, "Medicare (other)", "appeal", "medicare") == 60
    assert states.response_days(store.conn, BCBS, "call") == 32 and states.response_days(store.conn, BCBS, "forwarded") == 45
    # kept in meta for the dashboard's copy
    assert json.loads(store.conn.execute("SELECT value FROM meta WHERE key='states:response_days'").fetchone()[0])["groups"][BCBS]["p90"] == 32


def test_response_days_with_no_archive_is_the_45_day_default(store):
    assert states.response_days(store.conn, BCBS) == states.DEFAULT_RESPONSE_DAYS == 45
    assert states.response_days(store.conn, BCBS, "call") == 45


# ================================================================================================ work now and done
def test_an_unworked_denial_inside_its_window_is_work_now(store):
    tk = denied(store, reason=("CO", "50"))
    item(store, signal="DENIED_MEDICAL_NECESSITY", carc="50", line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    st = state(store)
    assert st["work_state"] == "work_now" and st["rule"] == "open" and st["label"] == "Work now"
    assert "nobody has worked it" in st["state_basis"] and st["follow_up_on"] is None and st["appeal_block"] is None
    assert st["route"] == "appeal"


def test_an_authorization_denial_is_checked_first_and_kept_out_of_a_bulk_appeal(store):
    """M10: a blind appeal of an authorization denial is almost never paid (amendment 8.2): its route is to find the
    authorization first, so it carries an appeal_block and never goes out in bulk."""
    tk = denied(store)
    item(store, line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    st = state(store)
    assert (st["work_state"], st["rule"], st["route"], st["appeal_block"]) == ("work_now", "open", "decide", "check_first")


def test_a_later_payment_of_the_same_line_on_another_submission_is_done(store):
    tk = denied(store)
    remit(store, trace="T2", paid_on="2026-04-20", claims=[{"pcn": F.claim_id(1, sub=2), "received": "2026-04-05", "pccn": "PCN-B",
                                                          "lines": [ln("99213", 150, 85, [("CO", "45", 65)], lcn="1")]}])
    item(store, line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    st = state(store)
    assert (st["work_state"], st["rule"]) == ("done", "paid_later_835")
    assert st["proof"]["cents"] == 8500 and st["appeal_block"] == "paid" and "Paid on a later remittance: $85.00" in st["state_basis"]


def test_a_later_payment_below_half_does_not_close_a_denial(store):
    tk = denied(store)
    remit(store, trace="T2", paid_on="2026-04-20", claims=[{"pcn": F.claim_id(1, sub=2), "received": "2026-04-05", "pccn": "PCN-B",
                                                          "lines": [ln("99213", 150, 20, [("CO", "45", 130)], lcn="1")]}])
    item(store, line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    assert state(store)["work_state"] == "work_now"


def test_reversed_and_reprocessed_to_the_deductible_is_moved_to_the_patient(store):
    """C11: a status-22 reversal of the denial and a new answer to the patient's share on the same claim."""
    tk = denied(store, reason=("CO", "B7"))
    remit(store, trace="T2", paid_on="2026-04-25",
          claims=[{"pcn": F.claim_id(1), "status": "22", "pccn": "PCN-A", "received": "2026-03-05",
                   "lines": [ln("99213", -150, 0, [("CO", "B7", -150)], lcn="1")]},
                  {"pcn": F.claim_id(1), "status": "1", "pccn": "PCN-A", "received": "2026-03-05",
                   "lines": [ln("99213", 150, 0, [("PR", "1", 95), ("CO", "45", 55)], lcn="1", allowed=95)]}])
    item(store, signal="DENIED_PROVIDER", carc="B7", line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    st = state(store)
    assert (st["work_state"], st["rule"]) == ("done", "moved_to_patient")
    assert st["proof"]["patient_cents"] == 9500 and "$95.00 to the patient's deductible" in st["state_basis"]


def test_reprocessed_to_the_patient_with_the_balance_open_in_ema_is_bill_the_patient(store, ledger, tmp_path):
    tk = denied(store, reason=("CO", "B7"))
    remit(store, trace=F.check_no(2), paid_on="2026-04-25",
          claims=[{"pcn": F.claim_id(1), "status": "22", "pccn": "PCN-A", "received": "2026-03-05",
                   "lines": [ln("99213", -150, 0, [("CO", "B7", -150)], lcn="1")]},
                  {"pcn": F.claim_id(1), "status": "1", "pccn": "PCN-A", "received": "2026-03-05",
                   "lines": [ln("99213", 150, 0, [("PR", "1", 95), ("CO", "45", 55)], lcn="1")]}])
    item(store, signal="DENIED_PROVIDER", carc="B7", line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    load(store, ledger, tmp_path, [charge(F.link_no(1), status="Payment Pending"), pay(F.link_no(1), F.check_no(1), 0),
                                   pay(F.link_no(1), F.check_no(2), 0, posted=D(2026, 4, 28)),
                                   adj(F.link_no(1), "CO-45", 55, contractual=True, posted=D(2026, 4, 28)), later()])
    st = state(store)
    assert (st["work_state"], st["rule"]) == ("work_now", "bill_patient")
    assert st["appeal_block"] == "reprocessed" and "bill the patient" in st["state_basis"]


def test_paid_before_by_the_plan_that_pays_first_closes_another_plans_denial(store):
    """C09: the primary paid; a plan that is not the patient's denied it later as if it were primary."""
    remit(store, trace="T0", paid_on="2026-03-20", payer="MEDICARE PART B TEST", claims=[
        {"pcn": F.claim_id(1), "cfi": "MB", "received": "2026-03-04", "status": "1",
         "lines": [ln("99213", 150, 92, [("CO", "45", 30), ("PR", "2", 28)], lcn="1")]}])
    tk = denied(store, reason=("CO", "96"), paid_on="2026-04-10")
    item(store, signal="DENIED_NONCOVERED", carc="96", payment_date="2026-04-10", line_key=line_key(store, tk), claim_key=f"{tk}-0",
         transaction_key=tk)
    st = state(store)
    assert (st["work_state"], st["rule"]) == ("done", "paid_before_other_payer") and st["proof"]["cents"] == 9200


def test_paid_before_by_the_same_payer_on_the_same_line_closes_a_later_denial_of_another_copy(store):
    """C62: the corrected claim was paid; the payer closed the superseded original later with another reason. The line
    control number ties the two (the re-adjudication carries none: the original's earlier answer gives it)."""
    remit(store, trace="T0", paid_on="2026-03-12", claims=[{"pcn": F.claim_id(1), "pccn": "PCN-A", "received": "2026-03-03",
                                                          "lines": [ln("99213", 150, 0, [("CO", "97", 150)], lcn="1")]}])
    remit(store, trace="T1", paid_on="2026-04-02", claims=[{"pcn": F.claim_id(1), "pccn": "PCN-B", "received": "2026-03-25",
                                                          "lines": [ln("99213", 150, 110, [("CO", "45", 40)], lcn="1")]}])
    tk = remit(store, trace="T2", paid_on="2026-04-28", claims=[{"pcn": F.claim_id(1), "pccn": "PCN-A", "received": "2026-03-03",
                                                               "lines": [ln("99213", 150, 0, [("CO", "222", 150)])]}])
    item(store, signal="DENIED_FREQUENCY", carc="222", payment_date="2026-04-28", line_key=line_key(store, tk), claim_key=f"{tk}-0",
         transaction_key=tk)
    st = state(store)
    assert (st["work_state"], st["rule"]) == ("done", "paid_before_same_payer")
    assert st["proof"]["watch"] and "watch for a takeback" in st["state_basis"]


def test_a_second_unit_on_its_own_line_is_never_closed_by_the_first_units_payment(store):
    """The first lesion's line was paid; the second, on its own line (its own line control number), was denied for
    frequency on a later answer: the first payment never closes it."""
    remit(store, trace="T1", paid_on="2026-03-20", claims=[{"pcn": F.claim_id(1), "pccn": "PCN-A", "received": "2026-03-03",
                                                          "lines": [ln("17003", 60, 50, [("CO", "45", 10)], lcn="1")]}])
    tk = remit(store, trace="T2", paid_on="2026-04-10", claims=[{"pcn": F.claim_id(1, sub=2), "pccn": "PCN-B", "received": "2026-04-01",
                                                               "lines": [ln("17003", 60, 0, [("CO", "151", 60)], lcn="2")]}])
    item(store, signal="DENIED_FREQUENCY", carc="151", procedure_code="17003", billed_code="17003", payment_date="2026-04-10",
         patient_control_number=F.claim_id(1, sub=2), line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk,
         expected_cents=5000, shortfall_cents=5000)
    assert state(store)["work_state"] == "work_now"


def test_a_denied_replacement_while_the_original_payment_stands_is_done_with_a_watch(store):
    """C10: the payer paid the original claim and denied the frequency-7 replacement."""
    remit(store, trace="T1", paid_on="2026-03-20", claims=[{"pcn": F.claim_id(1), "pccn": "PCN-A", "received": "2026-03-03",
                                                          "lines": [ln("99213", 150, 48, [("CO", "45", 57), ("PR", "3", 45)], lcn="1")]}])
    tk = remit(store, trace="T2", paid_on="2026-04-15", claims=[{"pcn": F.claim_id(1, sub=2), "pccn": "PCN-B", "freq": "7",
                                                               "received": "2026-04-01",
                                                               "lines": [ln("99213", 150, 0, [("CO", "197", 150)], lcn="1")]}])
    item(store, patient_control_number=F.claim_id(1, sub=2), payment_date="2026-04-15", line_key=line_key(store, tk),
         claim_key=f"{tk}-0", transaction_key=tk)
    st = state(store)
    assert (st["work_state"], st["rule"]) == ("done", "replacement_denied_paid") and st["proof"]["watch"]


def test_paid_as_the_second_payer_closes_the_payers_denial_as_primary(store):
    """C64: the same payer denied the claim sent to it as primary, then paid its second-payer part after the primary."""
    tk = denied(store, reason=("CO", "226"), paid_on="2026-03-28")
    remit(store, trace="T2", paid_on="2026-04-10", claims=[{"pcn": F.claim_id(1), "status": "2", "pccn": "PCN-C", "received": "2026-04-02",
                                                          "lines": [ln("99213", 150, 15.28, [("OA", "23", 134.72)], lcn="1")]}])
    item(store, signal="DENIED_MISSING_INFO", carc="226", line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    st = state(store)
    assert (st["work_state"], st["rule"]) == ("done", "paid_as_secondary") and st["proof"]["cents"] == 1528


def test_a_claim_forwarded_to_another_carrier_waits_45_days_then_is_work_now(store):
    """C08: CO-B11 names the carrier the claim went to; expect its payment, chase it after 45 days."""
    tk = remit(store, trace=F.check_no(1), paid_on="2026-03-28", claims=[
        {"pcn": F.claim_id(1), "status": "19", "crossover": "DELEGATE TEST", "received": "2026-03-05",
         "lines": [ln("99214", 128, 0, [("CO", "B11", 128)], lcn="1", rmk=["N418"])]}])
    item(store, signal="DENIED_NONCOVERED", carc="B11", procedure_code="99214", billed_code="99214", line_key=line_key(store, tk),
         claim_key=f"{tk}-0", transaction_key=tk)
    st = state(store, today="2026-04-20")
    assert (st["work_state"], st["rule"], st["follow_up_on"]) == ("waiting", "forwarded", "2026-05-12")
    assert st["appeal_block"] == "waiting" and "DELEGATE TEST" in st["state_basis"]
    # past the 45 days with no source that could show the carrier's money (no 835 from it, no EMA report): can't be checked
    st = state(store, today="2026-05-20")
    assert (st["work_state"], st["rule"]) == ("cant_check", "cant_check_carrier") and "no 835 from DELEGATE TEST" in st["state_basis"]


def test_a_forwarded_claim_ema_covers_is_chased_after_45_days(store, ledger, tmp_path):
    tk = remit(store, trace=F.check_no(1), paid_on="2026-03-28", claims=[
        {"pcn": F.claim_id(1), "status": "19", "crossover": "DELEGATE TEST", "received": "2026-03-05",
         "lines": [ln("99214", 128, 0, [("CO", "B11", 128)], lcn="1", rmk=["N418"])]}])
    item(store, signal="DENIED_NONCOVERED", carc="B11", procedure_code="99214", billed_code="99214", line_key=line_key(store, tk),
         claim_key=f"{tk}-0", transaction_key=tk)
    load(store, ledger, tmp_path, [charge(F.link_no(1), cpt="99214", amount=128), pay(F.link_no(1), F.check_no(1), 0, cpt="99214"), later()])
    st = state(store, today="2026-05-20")
    assert (st["work_state"], st["rule"]) == ("work_now", "forwarded_late") and "ask DELEGATE TEST for it" in st["state_basis"]


def test_a_forwarded_claim_ema_shows_paid_and_settled_is_done(store, ledger, tmp_path):
    tk = remit(store, trace=F.check_no(1), paid_on="2026-03-28", claims=[
        {"pcn": F.claim_id(1), "status": "19", "crossover": "DELEGATE TEST", "received": "2026-03-05",
         "lines": [ln("99213", 150, 0, [("CO", "B11", 150)], lcn="1")]}])
    item(store, signal="DENIED_NONCOVERED", carc="B11", line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    load(store, ledger, tmp_path, [charge(F.link_no(1)), pay(F.link_no(1), F.check_no(1), 0, posted=D(2026, 3, 31)),
                                   pay(F.link_no(1), "DLG0001", 105, posted=D(2026, 3, 26)),
                                   adj(F.link_no(1), "CO-45", 45, contractual=True, posted=D(2026, 3, 26)), later()])
    st = state(store)
    assert (st["work_state"], st["rule"]) == ("done", "forwarded_paid") and st["proof"]["cents"] == 10500


def test_paid_later_on_a_check_with_no_835_is_done_from_ema(store, ledger, tmp_path):
    """C01: EMA posted the payer's payment on a check no 835 carries."""
    tk = denied(store)
    item(store, line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    load(store, ledger, tmp_path, [charge(F.link_no(1), status="Resolved/Paid"), pay(F.link_no(1), F.check_no(1), 0),
                                   pay(F.link_no(1), "NOERA01", 90, posted=D(2026, 4, 20), status="Resolved/Paid"),
                                   adj(F.link_no(1), "CO-45", 60, contractual=True, posted=D(2026, 4, 20), status="Resolved/Paid"), later()])
    st = state(store)
    assert (st["work_state"], st["rule"]) == ("done", "paid_later_ema")
    assert st["proof"]["cents"] == 9000 and st["proof"]["check_tail"] == "RA01" and "no 835" in st["state_basis"]


def test_a_duplicate_denial_restating_a_payment_ema_posted_before_it_is_done(store, ledger, tmp_path):
    """C07: the payer paid on a check with no 835, then answered a copy with B13 lines that restate the payment, the
    patient's copay and the reductions; together they add up to the charge."""
    tk = remit(store, trace=F.check_no(2), paid_on="2026-04-20", claims=[{"pcn": F.claim_id(1), "pccn": "PCN-A", "received": "2026-03-05",
        "lines": [ln("17313", 120, 0, [("CO", "B13", 120)], lcn="1"), ln("17313", 25, 0, [("PR", "3", 25)], lcn="2"),
                  ln("17313", 5, 0, [("CO", "45", 5)], lcn="3")]}])
    item(store, signal="DENIED_DUPLICATE_UNPAID", carc="B13", procedure_code="17313", billed_code="17313", payment_date="2026-04-20",
         charge_cents=12000, line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    load(store, ledger, tmp_path, [charge(F.link_no(1), cpt="17313"), pay(F.link_no(1), "NOERA02", 120, cpt="17313", posted=D(2026, 3, 20)),
                                   patient_pay(25, cpt="17313", posted=D(2026, 3, 20)),
                                   adj(F.link_no(1), "CO-45", 5, cpt="17313", contractual=True, posted=D(2026, 3, 20)),
                                   pay(F.link_no(1), F.check_no(2), 0, cpt="17313", posted=D(2026, 4, 22)), later()])
    st = state(store)
    assert (st["work_state"], st["rule"]) == ("done", "paid_before_duplicate") and st["proof"]["cents"] == 12000


def test_a_credit_on_the_line_is_a_refund_to_decide_never_an_appeal(store, ledger, tmp_path):
    """C05: insurance paid more than the line was owed."""
    tk = remit(store, trace=F.check_no(1), paid_on="2026-03-28", claims=[{"pcn": F.claim_id(1), "pccn": "PCN-A", "received": "2026-03-05",
        "lines": [ln("99204", 290, 100, [("CO", "45", 125), ("PR", "2", 65)], lcn="1", allowed=165)]}])
    item(store, signal="UNDERPAID_VS_OWN_RATE", category="underpayment", carc=None, procedure_code="99204", billed_code="99204",
         paid_cents=10000, allowed_cents=16500, expected_cents=18000, shortfall_cents=1500, line_key=line_key(store, tk),
         claim_key=f"{tk}-0", transaction_key=tk)
    load(store, ledger, tmp_path, [charge(F.link_no(1), cpt="99204", amount=290), pay(F.link_no(1), F.check_no(1), 100, cpt="99204"),
                                   adj(F.link_no(1), "CO-45", 125, cpt="99204", contractual=True),
                                   pay(F.link_no(1), "SECOND01", 165, cpt="99204", posted=D(2026, 4, 20)), later()])
    st = state(store)
    assert (st["work_state"], st["rule"]) == ("work_now", "credit_refund")
    assert st["credit_cents"] == 10000 and st["appeal_block"] == "credit" and "refund or recoupment" in st["state_basis"]


# ================================================================================================ waiting and follow-up
def test_a_note_dated_and_allowing_days_waits_then_turns_back_to_work_now(store, ledger, tmp_path):
    """C38: an A/R note about a call and a reprocessing, with its own date and 'allow 30 days'."""
    tk = denied(store, reason=("CO", "B10"))
    item(store, signal="PROCEDURE_BUNDLED", category="bundling", carc="B10", line_key=line_key(store, tk), claim_key=f"{tk}-0",
         transaction_key=tk)
    ar = [F.ar_row(claim=F.claim_id(1), link=F.link_no(1), svc=SVC, cpt="99213", current=150, denial=150, status="Denied",
                   note="04/20 spoke with rep, reprocessing request, allow 30 days", aging=D(2026, 4, 28))]
    load(store, ledger, tmp_path, [charge(F.link_no(1), status="Denied"), pay(F.link_no(1), F.check_no(1), 0), later()], ar_rows=ar)
    # what the A/R reader will store with each note (needs_D.md): its kinds, date and stated wait
    for col, decl in (("note_kind", "TEXT"), ("note_date", "TEXT"), ("note_wait", "INTEGER")):
        if col not in {r[1] for r in store.conn.execute("PRAGMA table_info(ema_ar)")}:
            store.conn.execute(f"ALTER TABLE ema_ar ADD COLUMN {col} {decl}")
    c = states.classify_note("04/20 spoke with rep, reprocessing request, allow 30 days", as_of="2026-04-28")
    store.conn.execute("UPDATE ema_ar SET note_kind=?, note_date=?, note_wait=?", (",".join(c["kinds"]), c["date"], c["wait_days"]))
    store.conn.commit()
    st = state(store, today="2026-05-03")
    assert (st["work_state"], st["rule"], st["follow_up_on"]) == ("waiting", "work_waiting", "2026-05-20")
    assert st["appeal_block"] == "waiting" and "allow 30 days" in st["state_basis"]
    st = state(store, today="2026-05-25")
    assert (st["work_state"], st["rule"], st["follow_up_on"]) == ("work_now", "follow_up_due", "2026-05-20")
    assert "no answer from the payer by" in st["state_basis"]


def test_a_person_marking_sent_waits_for_the_payers_appeal_time(store):
    tk = denied(store)
    item(store, status="appealed", appealed_at="2026-04-10T15:00:00", status_changed_at="2026-04-10T15:00:00",
         line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    event(store, "F1", "manual", at="2026-04-10T15:00:00", frm="open", to="appealed", actor="Biller One")
    st = state(store, today="2026-04-20")
    # no answers on file to measure: the 45-day default, and an appeal waits at least 30 days for a commercial plan
    assert (st["work_state"], st["rule"], st["follow_up_on"]) == ("waiting", "work_waiting", "2026-05-25")
    assert st["person"] and "Marked Sent by Biller One" in st["state_basis"]


def test_the_payers_answer_after_the_work_is_work_now(store):
    tk = denied(store)
    item(store, status="appealed", appealed_at="2026-04-10", line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    event(store, "F1", "manual", at="2026-04-10T15:00:00", frm="open", to="appealed")
    remit(store, trace="T3", paid_on="2026-04-25", claims=[{"pcn": F.claim_id(1), "pccn": "PCN-A", "received": "2026-03-05",
                                                          "lines": [ln("99213", 150, 0, [("CO", "197", 150)], lcn="1")]}])
    st = state(store, today="2026-04-28")
    assert (st["work_state"], st["rule"]) == ("work_now", "work_answered")
    assert st["answered"]["date"] == "2026-04-25" and "answered CO-197" in st["state_basis"]


def test_undated_work_the_payer_answered_again_the_same_way_reads_as_upheld(store, ledger, tmp_path):
    """C26: EMA's claim action 'Appeal In Process' was already on the first report; the payer answered the same claim
    again with the same reason before it: the appeal was most likely upheld."""
    remit(store, trace=F.check_no(1), paid_on="2026-03-28", claims=[{"pcn": F.claim_id(1), "pccn": "PCN-A", "received": "2026-03-05",
                                                                    "lines": [ln("17311", 600, 0, [("CO", "97", 600)], lcn="1")]}])
    tk = remit(store, trace=F.check_no(2), paid_on="2026-04-20", claims=[{"pcn": F.claim_id(1), "pccn": "PCN-A", "received": "2026-03-05",
                                                                         "lines": [ln("17311", 600, 0, [("CO", "97", 600)])]}])
    item(store, signal="PROCEDURE_BUNDLED", category="bundling", carc="97", procedure_code="17311", billed_code="17311",
         payment_date="2026-04-20", line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    load(store, ledger, tmp_path, [charge(F.link_no(1), cpt="17311", amount=600, action="Appeal In Process", status="Denied"),
                                   pay(F.link_no(1), F.check_no(1), 0, cpt="17311"), later()])
    st = state(store)
    assert (st["work_state"], st["rule"]) == ("work_now", "work_answered")
    assert st["answered"]["inferred"] and "most likely its answer to that work, upheld" in st["state_basis"]


# ================================================================================================ accepted, expired, can't check
def test_a_person_letting_it_go_or_marking_it_lost_is_accepted(store):
    tk = denied(store)
    item(store, status="dismissed", status_changed_at="2026-04-02", notes="correct per contract", line_key=line_key(store, tk),
         claim_key=f"{tk}-0", transaction_key=tk)
    event(store, "F1", "manual", at="2026-04-02T10:00:00", frm="open", to="dismissed", actor="Biller Two", note="correct per contract")
    st = state(store)
    assert (st["work_state"], st["rule"], st["person"]) == ("accepted", "person_let_go", True)
    assert st["state_basis"] == "Let go by Biller Two on Apr 2, 2026: correct per contract"
    store.conn.execute("UPDATE findings SET status='lost'")
    store.conn.commit()
    assert state(store)["rule"] == "person_lost"


def test_an_item_a_hide_rule_let_go_is_accepted_and_says_so(store):
    tk = denied(store)
    item(store, status="dismissed", line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    event(store, "F1", "hidden", at="2026-04-02T10:00:00", note="hide rule: Blue Cross, 99213, CO-197")
    st = state(store)
    assert (st["work_state"], st["rule"]) == ("accepted", "hidden_rule") and "hide rule" in st["state_basis"]


def test_information_only_signals_are_accepted_and_contract_items_go_to_the_owner(store):
    item(store, finding_id="F2", signal="SEQUESTRATION", category="info", carc="253")
    item(store, finding_id="F3", signal="CONTRACT_RATE_GAP", category="contract", carc=None)
    assert state(store, "F2")["work_state"] == "accepted"
    st = state(store, "F3")
    assert (st["work_state"], st["rule"]) == ("work_now", "owner_decision")


def short_window_item(store, **kw):
    """A denial by a payer whose only window is 180 days from the remittance (a commercial plan the rules do not know):
    remittance Oct 1, 2025, window to Mar 30, 2026."""
    tk = denied(store, reason=("CO", "50"), paid_on="2025-10-01", received="2025-09-20", payer="TESTCARE HEALTH")
    args = dict(signal="DENIED_MEDICAL_NECESSITY", carc="50", payer_name="TESTCARE HEALTH", payer_group="Testcare Health",
                payer_kind="commercial", payment_date="2025-10-01", appeal_deadline="2026-03-30",
                deadline_basis="Commercial plans: 180 days is the common default", line_key=line_key(store, tk), claim_key=f"{tk}-0",
                transaction_key=tk)
    args.update(kw)
    return item(store, **args), tk


def test_past_every_window_is_expired_the_next_day_with_no_grace(store):
    """Orchestrator decision 1 (H5): EXPIRED starts the day after the last window closes; no 30-day grace in the work queue."""
    short_window_item(store)
    assert state(store, today="2026-03-30")["work_state"] == "work_now"
    st = state(store, today="2026-03-31")
    assert (st["work_state"], st["rule"], st["closed_on"]) == ("expired", "expired", "2026-03-30")
    assert st["state_basis"] == ("The last window closed on Mar 30, 2026 (Commercial plans: 180 days is the common default), with no work "
                                 "pending")
    assert states.EXPIRE_GRACE_DAYS == 0 and states.ARCHIVE_GRACE_DAYS == 30


def test_undated_work_does_not_hold_an_item_past_every_window_unless_ema_still_shows_it_pending(store, ledger, tmp_path):
    """C59: EMA's claim action was already on the first report, so its own date is unknown. While EMA's current claim
    action still shows the corrected claim and no answer to it is on file, it may be pending: follow it up (M1, related).
    An action that no longer shows any work at the payer proves nothing: past every window, the item expires."""
    short_window_item(store)
    load(store, ledger, tmp_path, [charge(F.link_no(1), action="Submitted Corrected Claim Portal", status="Denied"),
                                   pay(F.link_no(1), F.check_no(1), 0, posted=D(2025, 10, 3)), later()])
    st = state(store, today="2026-05-10")
    assert st["work_state"] == "work_now" and st["rule"] in ("work_stale", "follow_up_due"), st["state_basis"]
    assert "EMA's claim action still shows 'Submitted Corrected Claim Portal'" in st["state_basis"]
    store.conn.execute("UPDATE ema_lines SET action='Pending Patient'")
    store.conn.commit()
    st = state(store, today="2026-05-10")
    assert (st["work_state"], st["rule"]) == ("expired", "expired") and "Submitted Corrected Claim Portal" in st["state_basis"]
    assert "nothing shows that work was done in time or is still pending" in st["state_basis"]


def test_rebilled_to_the_right_payer_whose_denial_window_closed_is_expired(store):
    """C60: the plan said the patient was in hospice (B9); the claim went to Medicare, which denied it, and Medicare's
    window to dispute that answer closed more than 30 days ago (the plan's own window has not)."""
    tk = denied(store, reason=("CO", "B9"), paid_on="2026-01-10", received="2026-01-05", payer="HUMANA", cfi="16")
    remit(store, trace="T2", paid_on="2026-01-30", payer="MEDICARE PART B TEST", claims=[
        {"pcn": F.claim_id(1, sub=2), "cfi": "MB", "received": "2026-01-15", "lines": [ln("99213", 150, 0, [("CO", "97", 150)])]}])
    item(store, signal="DENIED_NONCOVERED", carc="B9", payer_name="HUMANA", payer_group="Humana Medicare Advantage",
         payer_kind="medicare_advantage", payment_date="2026-01-10", appeal_deadline="2027-07-10", line_key=line_key(store, tk),
         claim_key=f"{tk}-0", transaction_key=tk)
    st = state(store, today="2026-07-15")
    assert (st["work_state"], st["rule"]) == ("expired", "expired")
    # Medicare's window to dispute its answer: 120 days, its own limit 125 (42 CFR 405.942), so it closed on Jun 4, 2026
    assert "Rebilled to Medicare (other)" in st["state_basis"] and st["closed_on"] == "2026-06-04"


def test_an_answer_a_corrected_claim_can_fix_keeps_the_corrected_claims_clock_after_its_dispute_window(store):
    """C14: the refiled claim came back from the plan's other product name with PR-288 (referral absent). Its 120-day dispute
    window closed, but a corrected claim with the referral number can still go until the plan's 365 days from the date of
    service: never expired before that day, and expired the day after."""
    tk = denied(store, reason=("CO", "16"), paid_on="2026-03-15", received="2026-03-05", payer="HUMANA TEST", cfi="MC")
    remit(store, trace="T2", paid_on="2026-09-18", payer="HUMANA CARE PLAN TEST", claims=[
        {"pcn": F.claim_id(1, sub=2), "cfi": "MC", "received": "2026-09-10", "lines": [ln("99214", 205.80, 0, [("PR", "288", 205.80)], lcn="1")]}])
    item(store, signal="DENIED_MISSING_INFO", carc="16", procedure_code="99214", billed_code="99214", payer_name="HUMANA TEST",
         payer_group="Humana Medicaid", payer_kind="medicaid", payment_date="2026-03-15", appeal_deadline="2026-07-13",
         line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    st = state(store, today="2027-01-20")
    assert (st["work_state"], st["rule"]) == ("work_now", "work_answered"), st["state_basis"]
    assert st["answer_window"]["date"] == "2027-03-02" and st["answer_window"]["what"].startswith("the window to send")
    after = state(store, today="2027-03-03")
    assert (after["work_state"], after["rule"]) == ("expired", "expired") and after["closed_on"] == "2027-03-02"


def test_an_unanswered_claim_no_source_can_show_is_cant_check(store):
    item(store, signal="NO_ANSWER", category="denial", carc=None, line_key=None, claim_key=None, payment_date=None,
         evidence={"sent": "2026-03-03", "answer_due": "2026-04-02", "lines": [["99213", S]]})
    st = state(store, today="2026-05-03")
    assert (st["work_state"], st["rule"]) == ("cant_check", "cant_check_no_source")
    assert "No source can show the payer's answer" in st["state_basis"] and "no 835 from Anthem / BCBS on file" in st["state_basis"]


# ================================================================================================ a person's status wins
def test_a_persons_started_status_wins_over_proof_of_payment_and_says_so(store):
    tk = denied(store)
    remit(store, trace="T2", paid_on="2026-04-20", claims=[{"pcn": F.claim_id(1, sub=2), "received": "2026-04-05", "pccn": "PCN-B",
                                                          "lines": [ln("99213", 150, 85, [("CO", "45", 65)], lcn="1")]}])
    item(store, status="in_progress", status_changed_at="2026-04-22", line_key=line_key(store, tk), claim_key=f"{tk}-0",
         transaction_key=tk)
    event(store, "F1", "manual", at="2026-04-22T09:00:00", frm="open", to="in_progress", actor="Biller One")
    st = state(store)
    assert (st["work_state"], st["rule"], st["person"]) == ("work_now", "person_started", True)
    assert "Started by Biller One on Apr 22, 2026" in st["state_basis"] and "mark it Paid" in st["state_basis"]


def test_a_person_who_reopens_an_automatic_closure_keeps_it_open_until_a_newer_payment(store):
    """needs_B 0a.2: RemitWatch closed the item on a later 835 payment; a person reopened it (that payment is not this
    line's). The same payment never closes it again: work now, the payment named, never kept out as paid. Detection may
    also hand the rejected EMA payment over as the outcome's rejected_payment. A payment after the reopen closes it."""
    tk = denied(store, reason=("CO", "50"))
    remit(store, trace=F.check_no(2), paid_on="2026-04-10", claims=[{"pcn": F.claim_id(1, sub=2), "pccn": "PCN-B", "received": "2026-04-01",
          "lines": [ln("99213", 150, 90, [("CO", "45", 60)], lcn="1")]}])
    item(store, signal="DENIED_MEDICAL_NECESSITY", carc="50", line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk,
         status="open", status_changed_at="2026-04-20")
    assert state(store)["work_state"] == "done"                       # no person yet: the payment closes it
    event(store, "F1", "manual", at="2026-04-20T09:00:00", frm="resolved", to="open", actor="Biller One", note="not this line")
    st = state(store)
    assert (st["work_state"], st["rule"], st["appeal_block"]) == ("work_now", "person_reopened", None), st["state_basis"]
    assert st["state_basis"].startswith("Reopened by Biller One on Apr 20, 2026 after RemitWatch closed it: Paid on a later remittance: "
                                        "$90.00 by BLUE CROSS TEST on Apr 10, 2026")
    assert st["state_basis"].endswith("the person said it does not pay this line, so it is worked as open")
    f = dict(store.conn.execute("SELECT * FROM findings").fetchone())
    oc = {"F1": {"outcome": "open", "person_reopened": True, "paid_after_same": 0, "paid_after_other": 0,
                 "rejected_payment": {"date": "2026-04-18", "payer": "Testcare Health", "cents": 4000, "check_tail": "0007"}}}
    st = states.states_of(store.conn, [f], outcomes=oc, today=TODAY)["F1"]
    assert st["rule"] == "person_reopened" and "EMA shows $40.00 by Testcare Health on Apr 18, 2026" in st["state_basis"]
    remit(store, trace=F.check_no(3), paid_on="2026-04-28", claims=[{"pcn": F.claim_id(1, sub=3), "pccn": "PCN-C", "received": "2026-04-22",
          "lines": [ln("99213", 150, 90, [("CO", "45", 60)], lcn="1")]}])
    assert state(store)["work_state"] == "done"                       # a payment after the reopen


def test_a_person_marking_paid_is_done_and_a_stale_item_is_done(store):
    item(store, status="resolved", recovered_cents=9000, status_changed_at="2026-04-30")
    event(store, "F1", "manual", at="2026-04-30T09:00:00", frm="open", to="resolved", actor="Biller One")
    st = state(store)
    assert (st["work_state"], st["rule"]) == ("done", "person_paid") and "recovered $90.00" in st["state_basis"]
    item(store, finding_id="F2", stale=1)
    assert state(store, "F2")["rule"] == "stale"


def test_a_persons_reopen_cancels_their_older_marks(store):
    tk = denied(store)
    item(store, line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    event(store, "F1", "manual", at="2026-04-05T09:00:00", frm="open", to="appealed")
    event(store, "F1", "manual", at="2026-04-12T09:00:00", frm="appealed", to="open")
    assert state(store)["rule"] == "open"


# ================================================================================================ the batch, the record, the copy
def test_states_of_answers_like_state_of_for_every_item_and_works_on_a_compact_store(store, tmp_path):
    tk = denied(store)
    item(store, line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    item(store, finding_id="F2", signal="SEQUESTRATION", category="info")
    item(store, finding_id="F3", status="dismissed")
    rows = [dict(r) for r in store.conn.execute("SELECT * FROM findings")]
    batch = states.states_of(store.conn, rows, today=TODAY)
    for r in rows:
        assert batch[r["finding_id"]]["work_state"] == states.state_of(store.conn, r, today=TODAY)["work_state"]
    # the dashboard's in-memory copy (no ledger, fewer indexes) gives the same states
    mem = Store(":memory:", compact=True)
    store.conn.commit()
    store.conn.backup(mem.conn)
    rows2 = [dict(r) for r in mem.conn.execute("SELECT * FROM findings")]
    assert {k: v["work_state"] for k, v in states.states_of(mem.conn, rows2, today=TODAY).items()} == \
           {k: v["work_state"] for k, v in batch.items()}
    mem.close()


def test_the_stored_state_is_read_back_as_stored():
    assert states.stored_state({"work_state": "waiting", "follow_up_on": "2026-05-20", "state_basis": "x"}) == \
           {"work_state": "waiting", "follow_up_on": "2026-05-20", "state_basis": "x", "label": "Waiting"}
    assert states.stored_state({"work_state": None}) is None and states.stored_state({}) is None


def test_the_payers_record_for_a_pattern_leaves_the_item_out(store):
    for i in range(6):
        item(store, finding_id=f"P{i}", status="resolved" if i < 3 else "open", auto_note="Paid on a later remittance" if i < 3 else None)
    f = item(store, finding_id="P9")
    r = states.recovery_of(store.conn, f)
    assert (r["n"], r["paid"], r["rate"]) == (6, 3, 0.5) and "3 of the 6 earlier items like this" in r["basis"]
    assert states.recovery_of(store.conn, dict(f, procedure_code="11102"))["rate"] is None


def test_work_evidence_lists_dated_work_oldest_first(store):
    tk = denied(store)
    item(store, line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    event(store, "F1", "manual", at="2026-04-02T09:00:00", frm="open", to="appealed", actor="Biller One")
    remit(store, trace="T2", paid_on="2026-04-25", claims=[{"pcn": F.claim_id(1, sub=2), "pccn": "PCN-B", "received": "2026-04-15",
                                                          "freq": "7", "lines": [ln("99213", 150, 0, [("CO", "16", 150)], lcn="1")]}])
    f = dict(store.conn.execute("SELECT * FROM findings WHERE finding_id='F1'").fetchone())
    ev = states.work_evidence(store.conn, f, today=TODAY)
    assert [(e["kind"], e["dated_by"]) for e in ev] == [("sent", "event"), ("resubmitted", "payer")]
    assert ev[1]["date"] == "2026-04-15" and "received a corrected claim" in ev[1]["text"]


# ================================================================================================ every EMA outcome
def _written_off_world(store, ledger, tmp_path):
    """The item's denial written off in EMA the day the remittance posted (the line settled at $0)."""
    tk = denied(store)
    item(store, line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    load(store, ledger, tmp_path, [charge(F.link_no(1)), pay(F.link_no(1), F.check_no(1), 0), adj(F.link_no(1), "CO-197", 150), later()])
    return dict(store.conn.execute("SELECT * FROM findings WHERE finding_id='F1'").fetchone())


OUTCOME_STATES = [
    ("paid_after", {"paid_after_same": 9000, "first_paid_after": {"date": "2026-04-20", "payer": "Blue Cross", "check_tail": "0042"}},
     ("done", "paid_later_ema"), "Paid later: $90.00 by Blue Cross on a check with no 835 (Apr 20, 2026, check ending 0042)"),
    ("paid_after_other", {"paid_after_other": 9000, "first_paid_after": {"date": "2026-04-20", "payer": "Testcare", "check_tail": "0043"}},
     ("done", "rebilled_paid"), "Rebilled to Testcare, paid: $90.00 on Apr 20, 2026 (check ending 0043, no 835); EMA shows the line settled"),
    ("patient", {"patient_after": 15000, "outcome_on": "2026-04-15"}, ("done", "moved_to_patient"),
     "Settled by the patient: EMA shows $150.00 paid by the patient after the denial and the line closed"),
    ("written_off_by_policy", {"pattern": "Anthem / BCBS|99213||CO-197"}, ("accepted", "policy"),
     "The owner's write-off policy accepts Anthem / BCBS's CO-197 denials of 99213"),
    ("auto_written_off", {}, ("work_now", "open"), "; EMA's posting rule wrote it off the day the remittance posted; nobody worked it;"),
    ("written_off_while_pending", {}, ("work_now", "open"), "; EMA wrote it off the day it posted, while the payer was still waiting for information;"),
    ("lost_to_timely_filing", {}, ("work_now", "open"), "; written off for late filing after an earlier denial;"),
    ("written_off_later", {}, ("work_now", "open"), "; a person wrote it off in EMA later, on a day with no remittance;"),
    ("paid_after_partial", {"paid_after_same": 2000}, ("work_now", "open"), "; EMA shows part of it paid since;"),
    ("paid_before", {"paid_before": 0}, ("work_now", "open"), "; EMA shows the line paid before this denial: check whether the denial still matters;"),
    ("action", {}, ("work_now", "open"), "; EMA shows a claim action on it;"),
    ("open", {}, ("work_now", "open"), "; EMA shows it open;"),
    ("not_covered", {}, ("work_now", "open"), "Not paid and nobody has worked it; act by "),
]


@pytest.mark.parametrize("oc, extra, want, basis", OUTCOME_STATES, ids=[c[0] for c in OUTCOME_STATES])
def test_each_ema_outcome_gives_its_state(store, ledger, tmp_path, oc, extra, want, basis):
    f = _written_off_world(store, ledger, tmp_path)
    base = ec.line_outcomes(store.conn, [f], code_once=lambda x: True, today=TODAY)[f["finding_id"]]
    st = states.states_of(store.conn, [f], outcomes={"F1": dict(base, outcome=oc, **extra)}, today=TODAY)["F1"]
    assert (st["work_state"], st["rule"]) == want
    if want[0] == "work_now":
        assert st["state_basis"].startswith("Not paid and nobody has worked it") and basis in st["state_basis"], st["state_basis"]
    else:
        assert st["state_basis"].startswith(basis), st["state_basis"]


# ================================================================================================ the payer's own limit, the new signals, the archive's hints
def test_the_payers_own_limit_keeps_an_item_open_past_its_safe_date(store):
    """Detection keeps the safe date in appeal_deadline and the payer's own later limit in legal_deadline: an item
    expires only past the later of the two (and past every other window)."""
    short_window_item(store, appeal_deadline="2026-03-01", legal_deadline="2026-06-30")
    st = state(store, today="2026-05-10")
    assert (st["work_state"], st["rule"], st["legal_deadline"]) == ("work_now", "open", "2026-06-30")
    st = state(store, today="2026-07-01")
    assert (st["work_state"], st["closed_on"]) == ("expired", "2026-06-30")
    assert st["closed_basis"] == "the payer's own limit for the same request"
    assert st["state_basis"].startswith("The last window closed on Jun 30, 2026 (the payer's own limit for the same request)")


def test_a_credit_balance_is_a_refund_to_decide_and_never_expires(store):
    item(store, signal="CREDIT_BALANCE", category="refund", payer_group="Medicare Part B", payer_kind="medicare", carc=None,
         expected_cents=4500, shortfall_cents=4500, appeal_deadline="2026-05-30", deadline_basis="Medicare expects it back within 60 days",
         evidence={"credit_cents": 4500, "overpaid_by": "Medicare Part B"})
    for today in ("2026-05-03", "2027-01-15"):
        st = state(store, today=today)
        assert (st["work_state"], st["rule"], st["appeal_block"], st["credit_cents"]) == ("work_now", "credit_refund", "credit", 4500)
    assert st["state_basis"] == ("EMA shows a credit of $45.00 on this claim: Medicare Part B paid more than it was owed; decide refund or "
                                 "recoupment (owner). Medicare expects it back by May 30, 2026.")


def _secondary_world(store, ledger, tmp_path, *, paid_by_second=False, crossover=False, answered_again=False):
    """Blue Cross paid $80 on 99213 and left $20 of coinsurance; the second plan on file (Testcare) was never billed, and EMA
    wrote the $20 off. paid_by_second: Testcare's $20 posted later. answered_again: Blue Cross sent the same answer again on
    a later remittance (its own money, never the second payer's)."""
    tk = remit(store, trace=F.check_no(1), paid_on="2026-03-28", claims=[{"pcn": F.claim_id(1), "pccn": "PCN-A", "received": "2026-03-05",
        "lines": [ln("99213", 150, 80, [("CO", "45", 50), ("PR", "2", 20)], lcn="1")]}])
    if answered_again:
        remit(store, trace=F.check_no(3), paid_on="2026-04-15", claims=[{"pcn": F.claim_id(1, sub=2), "pccn": "PCN-B", "received": "2026-04-01",
            "lines": [ln("99213", 150, 80, [("CO", "45", 50), ("PR", "2", 20)], lcn="1")]}])
    second = er.payer_group_of(store.conn, P["commercial"])[0]
    item(store, signal="SECONDARY_NOT_BILLED", category="denial", actionable="review", carc=None, paid_cents=8000, expected_cents=2000,
         shortfall_cents=2000, appeal_deadline="2026-09-24", deadline_basis=f"{second}'s filing limit", line_key=line_key(store, tk),
         claim_key=f"{tk}-0", transaction_key=tk,
         evidence={"first_payer": BCBS, "second_payer": second, "share": "coinsurance", "crossover": crossover, "ema_wrote_off_share": True})
    rows = [charge(F.link_no(1)), pay(F.link_no(1), F.check_no(1), 80), adj(F.link_no(1), "CO-45", 50, contractual=True),
            adj(F.link_no(1), "OA-23", 20)]
    if paid_by_second:
        rows.append(pay(F.link_no(1), "SEC01", 20, posted=D(2026, 4, 20), payer=P["commercial"]))
    load(store, ledger, tmp_path, rows + [later()])
    return second


def test_a_second_payer_never_billed_is_work_now_until_it_pays_and_the_first_payers_money_never_closes_it(store, ledger, tmp_path):
    second = _secondary_world(store, ledger, tmp_path, answered_again=True)
    st = state(store)
    # EMA's line is settled at $0 (the share written off) and the first payer paid more than the $20: no denial rule closes it
    assert (st["work_state"], st["rule"]) == ("work_now", "open") and st["proof"] is None
    assert st["state_basis"].startswith(f"Nothing from {second} on file: bill it for the share the first payer left;")
    assert st["state_basis"].endswith("; act by Sep 24, 2026")


def test_a_second_payer_that_paid_closes_it(store, ledger, tmp_path):
    second = _secondary_world(store, ledger, tmp_path, paid_by_second=True)
    st = state(store)
    assert (st["work_state"], st["rule"]) == ("done", "second_payer_paid")
    assert st["state_basis"] == (f"Paid by {second}: EMA shows $20.00 from it from Apr 20, 2026 (check ending EC01), against the $20.00 the "
                                 "first payer left")


def test_contractual_denials_the_owner_accepted_are_accepted(store):
    item(store, signal="EMA_DENIAL_AS_CONTRACTUAL", category="contract", carc="97", expected_cents=None, shortfall_cents=None,
         evidence={"reason": "CO-97", "top_codes": [["17311", 120000], ["17312", 30000]], "written_off_cents": 150000})
    assert (state(store)["work_state"], state(store)["rule"]) == ("work_now", "owner_decision")
    pol = {f"{BCBS}|17311||CO-97": {"decision": "accept", "by": "Owner One", "at": "2026-04-02T10:00:00"}}
    store.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('ema:writeoff_policy', ?)", (json.dumps(pol),))
    store.conn.commit()
    assert state(store)["rule"] == "owner_decision"                  # one of its two codes accepted: still the owner's
    pol[f"{BCBS}|||CO-97"] = {"decision": "accept", "by": "Owner One", "at": "2026-04-03T10:00:00"}
    store.conn.execute("UPDATE meta SET value=? WHERE key='ema:writeoff_policy'", (json.dumps(pol),))
    store.conn.commit()
    st = state(store)
    assert (st["work_state"], st["rule"]) == ("accepted", "policy")
    assert st["state_basis"] == f"The owner's write-off policy accepts {BCBS}'s CO-97 denials on every code (set by Owner One on Apr 3, 2026)"
    assert "|" not in st["state_basis"]


def test_the_archive_rules_hints_decide_a_proven_done_and_a_waiting_and_never_close_a_likely_one(store):
    tk = denied(store)
    proof = [{"date": "2026-03-28", "source": "835", "kind": "payment", "text": "Paid $90.00 on the claim's other copy", "decisive": True}]
    hint = {"rule": "same_day_answers", "reclass": "answered_same_day", "label": "Answered on another claim the same day",
            "hint": "The same remittance paid this line on the claim's other copy.", "state_hint": "done", "strength": "proven", "proof": proof}
    item(store, line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk, evidence={"archive_rule": hint})
    st = state(store)
    assert (st["work_state"], st["rule"]) == ("done", "archive_answered_same_day")
    assert st["state_basis"] == "Answered on another claim the same day: the same remittance paid this line on the claim's other copy"
    assert st["evidence"][0]["text"] == "Paid $90.00 on the claim's other copy"
    item(store, line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk, evidence={"archive_rule": dict(hint, strength="likely")})
    st = state(store)
    assert (st["work_state"], st["rule"], st["appeal_block"], st["route"]) == ("work_now", "check_first", "likely_paid", "decide")
    assert st["state_basis"] == "Answered on another claim the same day: the same remittance paid this line on the claim's other copy"
    rev = {"rule": "same_day_answers", "reclass": "reversed_no_answer", "label": "Reversed by the payer, no new answer yet",
           "hint": "Blue Cross reversed this payment on 2026-04-10 and no new answer is on file.", "state_hint": "waiting", "strength": "proven",
           "proof": [{"date": "2026-04-10", "source": "835", "kind": "reversal", "text": "reversal", "decisive": True}]}
    item(store, line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk, evidence={"archive_rule": rev})
    st = state(store, today="2026-04-20")
    # no answers on file to measure: the 45-day default from the reversal
    assert (st["work_state"], st["rule"], st["follow_up_on"], st["appeal_block"]) == ("waiting", "archive_waiting", "2026-05-25", "waiting")
    st = state(store, today="2026-06-01")
    assert (st["work_state"], st["rule"], st["follow_up_on"]) == ("work_now", "follow_up_due", "2026-05-25")


def test_work_first_seen_on_a_later_production_report_waits_for_the_payers_answer(store, ledger, tmp_path):
    """Two pulls: the first Production report has no claim action on the line; the next one, three weeks later, shows
    'Initial Appeal Electronic'. The appeal is dated by the report that first showed it, so the item waits for the payer's
    answer, then turns back to work now."""
    tk = denied(store)
    item(store, line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    load(store, ledger, tmp_path, [charge(F.link_no(1), status="Denied"), pay(F.link_no(1), F.check_no(1), 0, status="Denied"), later()])
    load(store, ledger, tmp_path, [charge(F.link_no(1), status="Denied", action="Initial Appeal Electronic"),
                                   pay(F.link_no(1), F.check_no(1), 0, status="Denied", action="Initial Appeal Electronic"),
                                   later(posted=D(2026, 5, 20))])
    st = state(store, today="2026-05-25")
    work = [e for e in states.work_evidence(store.conn, dict(store.conn.execute("SELECT * FROM findings").fetchone())) if e["kind"] == "appeal"]
    assert work and work[0]["dated_by"] == "first_seen", work
    assert (st["work_state"], st["rule"]) == ("waiting", "work_waiting"), st["state_basis"]
    assert st["follow_up_on"] > "2026-05-25" and "first seen on the Production report of" in st["state_basis"]
    st = state(store, today=states._add(st["follow_up_on"], 1))
    assert (st["work_state"], st["rule"]) == ("work_now", "follow_up_due")
