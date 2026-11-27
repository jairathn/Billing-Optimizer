"""
Fix wave F4 (plans/briefs/2026-11-26_fixwave.md, "F4 the EMA side and the bare $0 rule everywhere"): the failing tests,
written from the residuals the section names (fix7e-c and rule 4.2's measure; no reviewer test reached these functions).

1. A bare $0 row is no answer anywhere: a $0 row EMA posts under a check is a payer's $0 answer only with its proof (an
   allowed amount, or the payer's reason that day: ema_checks.zero_answer_sql, the never-answered rule's predicate). The
   other readers of an EMA line's answers use the same predicate: selfcheck's blind-charge count and self-audit, the
   payers' answer times (ema_checks.turnaround), the ledger's own status class and SECONDARY_NOT_BILLED.
2. C5's patient, day and code check against the 835s reads the part of the service (26, TC or the whole).
3. A company's plan EMA names into the Medicaid catch-all ("Medicaid (other)") is that company's, never the state's
   Medicaid, in C6's same-payer reading and C5's same-day reading (fix6's reading, states.MEDICAID_CATCH_ALL).
4. The weekly self-audit dates a line as the product dates its item: the plan facts (codes.rule_facts), a second payer by
   its own group and clock (codes.second_claim_window), Kentucky Medicaid's corrected claim where it takes no appeal.

Every patient, claim, member number, check, date and amount here is invented (tests/ema_fixtures.py and the builders of
tests/test_ema_wiring.py, tests/test_837_sends.py). Each test runs on the server's store file and on the compact in-memory
store the serverless cycle builds and analyses.
"""
from __future__ import annotations

import datetime as dt
import itertools
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch import codes, ema_checks as ec, ema_ledger as el, ema_reports as er, selfcheck  # noqa: E402
from remitwatch.detect import Detector  # noqa: E402
from remitwatch.store import Store  # noqa: E402
from tests import ema_fixtures as F  # noqa: E402
from tests.test_837_sends import clm837, send  # noqa: E402
from tests.test_ema_wiring import adj, charge, denial_world, load, pay, remit  # noqa: E402

KINDS = ["file", "compact"]
D = dt.date
P = F.PAYERS
SVC = D(2026, 3, 2)
S = SVC.isoformat()
TODAY = "2026-05-03"
_N = itertools.count(1)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(er, "CLAIM_HOST_SUFFIX", F.HOST_SUFFIX)
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    monkeypatch.setenv("REMITWATCH_EMA_STRICT", "1")
    for k in ("VERCEL", "REMITWATCH_EMA_REPORTS", "REMITWATCH_EMA_CLAIM_URL", "REMITWATCH_EMA_PAYER_ALIASES",
              "REMITWATCH_EMA_LEDGER", "REMITWATCH_EMA_REPORT_KEEP", "REMITWATCH_EMA_CLOSES", "REMITWATCH_STALE_DAYS"):
        monkeypatch.delenv(k, raising=False)
    ec._LAST_POST.clear()


def _world(tmp_path, kind):
    s = Store(str(tmp_path / "s.sqlite")) if kind == "file" else Store(":memory:", compact=True)
    er.ensure_schema(s.conn)
    s.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('ema:key_salt', ?)", ("ab" * 32,))
    s.conn.commit()
    return s, el.Ledger.open(str(tmp_path / f"ema_ledger{next(_N)}.sqlite"))


def _name(n: int) -> str:
    """An invented patient for claim n."""
    return f"Feffour, Pat{chr(65 + n % 26)}{chr(65 + n // 26 % 26)}"


def _last_first(n: int) -> tuple[str, str]:
    last, first = _name(n).split(", ")
    return last.upper(), first.upper()


def _run(s, today=TODAY):
    det = Detector(s, run_id=f"r{next(_N)}", today=today)
    det.run()
    assert det.ema_errors == [], det.ema_errors
    return det


def _items(s, where="1=1"):
    s.conn.row_factory = None
    cols = ["finding_id", "signal", "patient_control_number", "ema_link", "shortfall_cents", "status", "work_state", "evidence"]
    return [dict(zip(cols, r)) for r in s.conn.execute(
        f"SELECT {', '.join(cols)} FROM findings WHERE COALESCE(stale,0)=0 AND {where} ORDER BY finding_id")]


# =================================================================================== 1. a bare $0 row is no answer
def _bare_rows(base: int, svc: dt.date, charged: dt.date, posted: dt.date) -> list[dict]:
    """Three claims of one patient each, charged `charged` for services on `svc`:
    base+1: its only insurance row is a bare $0 row on a batch check (no reason, no allowed amount, no adjustment);
    base+2: $0 on the batch check with the allowed amount ($90) put on the patient and CO-45 that day: a real $0 answer;
    base+3: paid $80 on the batch check."""
    rows = []
    for k in (1, 2, 3):
        n = base + k
        rows.append(charge(F.link_no(n), cpt="99214", amount=180, patient=_name(n), svc=svc, posted=charged, status="Payment Pending"))
    rows.append(pay(F.link_no(base + 1), f"BATCH{base}", 0, cpt="99214", patient=_name(base + 1), svc=svc, posted=posted,
                    status="Payment Pending"))
    rows.append(pay(F.link_no(base + 2), f"BATCH{base}", 0, cpt="99214", patient=_name(base + 2), svc=svc, posted=posted, allowed=90))
    rows.append(adj(F.link_no(base + 2), "CO-45", 90, cpt="99214", patient=_name(base + 2), svc=svc, posted=posted, contractual=True))
    rows.append(pay(F.link_no(base + 3), f"BATCH{base}", 80, cpt="99214", patient=_name(base + 3), svc=svc, posted=posted))
    return rows


@pytest.mark.parametrize("kind", KINDS)
def test_a_bare_zero_row_leaves_its_charge_in_the_blind_dollars(tmp_path, kind):
    """The newest remittance is of March 28. Three services of April 6 were charged on April 7; on April 20 a batch check
    lists 201 at $0 with nothing else (a bare row), answers 202 with $0 and its proof, and pays 203. The dollars billed
    since the remittances stopped with no payer's answer on file are 201's $180 (before: its bare row counted as an
    answer, so the blindness banner showed $0 while the claim was still pending in EMA)."""
    s, lg = _world(tmp_path, kind)
    rows = _bare_rows(200, D(2026, 4, 6), D(2026, 4, 7), D(2026, 4, 20))
    denial_world(s, lg, tmp_path, rows)
    lg.close()
    cents, about = selfcheck._charges_since(s.conn, "2026-03-28", "2026-06-10")
    assert (cents, about) == (18000, False)


@pytest.mark.parametrize("kind", KINDS)
def test_the_self_audit_counts_a_line_answered_only_by_a_bare_zero_row_as_unanswered(tmp_path, kind):
    """The weekly self-audit's coverage rule for unanswered claims (answer key R7): 201's only insurance row is a bare $0
    row, and no item lists it, so it is a gap (unanswered, inside the first-filing limit); 202 (a $0 answer with its proof)
    and 203 (paid) are not. Before: the bare row counted as an answer and the claim was in no list at all."""
    s, lg = _world(tmp_path, kind)
    rows = _bare_rows(200, D(2026, 4, 6), D(2026, 4, 7), D(2026, 4, 20))
    denial_world(s, lg, tmp_path, rows)
    lg.close()
    gaps = _audit_gaps(s, "2026-06-10")
    unanswered = [g["key"] for g in gaps if g["category"] == "unanswered_in_filing_limit" and g["key"].endswith("|2026-04-06|99214")]
    assert len(unanswered) == 1 and unanswered[0].startswith(f"link:{F.link_no(201)}|"), gaps


@pytest.mark.parametrize("kind", KINDS)
def test_a_payers_answer_time_counts_from_its_first_real_answer_not_a_bare_zero_row(tmp_path, kind):
    """21 claims of one payer, each charged on its day: a batch check lists each at $0 with nothing else five days later
    (a bare row) and the payer pays it forty days after the charge. The payer's answer time (charge to first answer, 95th
    percentile) is 40 days, whether read with the ledger or from the lines alone (before: 5, the bare row's day, so a claim
    the payer never answered was chased weeks early and one it answered late looked on time)."""
    s, lg = _world(tmp_path, kind)
    rows = []
    for i in range(21):
        n = 300 + i
        svc = D(2026, 1, 5) + dt.timedelta(days=i)
        ch = svc + dt.timedelta(days=1)
        rows += [charge(F.link_no(n), cpt="99213", amount=150, patient=_name(n), svc=svc, posted=ch, status="Payment Pending"),
                 pay(F.link_no(n), f"BATCH{i}", 0, patient=_name(n), svc=svc, posted=ch + dt.timedelta(days=5), status="Payment Pending"),
                 pay(F.link_no(n), f"PAID{i}", 80, patient=_name(n), svc=svc, posted=ch + dt.timedelta(days=40))]
    load(s, lg, tmp_path, rows)
    with_ledger = ec.turnaround(s.conn, ledger=lg)
    lg.close()
    got = {g: v for g, v in with_ledger.items() if v.get("n_charge", 0) >= 20}
    assert got and all(v["charge_p95"] == 40 for v in got.values()), with_ledger
    s.conn.execute("DELETE FROM meta WHERE key = ?", (ec.TURNAROUND_KEY,))
    alone = ec.turnaround(s.conn)
    got = {g: v for g, v in alone.items() if v.get("n_charge", 0) >= 20}
    assert got and all(v["charge_p95"] == 40 for v in got.values()), alone


@pytest.mark.parametrize("kind", KINDS)
def test_the_ledgers_status_class_reads_a_bare_zero_row_as_no_answer(tmp_path, kind):
    """EMA closed two claims 'Resolved/Other'. 401's only insurance row is a bare $0 row: nobody answered it, so it was
    given up (gave_up); 402 has a $0 answer with its allowed amount: settled. Before: the bare row made 401 'settled', as if
    the payer had answered."""
    s, lg = _world(tmp_path, kind)
    rows = [charge(F.link_no(401), cpt="99214", amount=180, patient=_name(401), posted=D(2026, 3, 3), status="Payment Pending"),
            pay(F.link_no(401), "BATCH40", 0, cpt="99214", patient=_name(401), posted=D(2026, 4, 10), status="Resolved/Other"),
            charge(F.link_no(402), cpt="99214", amount=180, patient=_name(402), posted=D(2026, 3, 3), status="Payment Pending"),
            pay(F.link_no(402), "BATCH40", 0, cpt="99214", patient=_name(402), posted=D(2026, 4, 10), allowed=90,
                status="Resolved/Other")]
    load(s, lg, tmp_path, rows)
    lg.close()
    got = dict(s.conn.execute("SELECT link, status_class FROM ema_lines WHERE link IN (?, ?)", (F.link_no(401), F.link_no(402))))
    assert got == {F.link_no(401): "gave_up", F.link_no(402): "settled"}


@pytest.mark.parametrize("kind", KINDS)
def test_a_second_payers_bare_zero_row_is_not_its_answer(tmp_path, kind):
    """Blue Cross paid claim 8 and left $30 coinsurance; a commercial second payer is on file in EMA. On April 20 a batch
    check of the second payer lists the line at $0 with nothing else. That is no answer: the share was never billed, so
    the item is listed (before: any $0 row from the second payer read as its answer and the share was on no list). A $0
    answer with its proof (the second payer's CO-23 that day) is an answer."""
    s, lg = _world(tmp_path, kind)
    remit(s, trace="777000222", paid_on="2026-04-01",
          claims=[{"pcn": F.claim_id(8), "last": "SAMPLE", "first": "DELTA",
                   "lines": [(S, "99213", 150, 90, [("CO", "45", 30), ("PR", "2", 30)], {"allowed_cents": 12000})]}])
    rows = [charge(F.link_no(8), patient=2, secondary=P["commercial"]),
            pay(F.link_no(8), "777000222", 90, patient=2, posted=D(2026, 4, 3), secondary=P["commercial"]),
            adj(F.link_no(8), "CO-45", 30, patient=2, posted=D(2026, 4, 3), contractual=True, secondary=P["commercial"]),
            pay(F.link_no(8), "BATCH-SEC", 0, patient=2, posted=D(2026, 4, 20), payer=P["commercial"], secondary=P["commercial"])]
    ar = [F.ar_row(claim=F.claim_id(8), link=F.link_no(8), status="Payment Pending", current=30, aging=D(2026, 4, 3), svc=SVC,
                   patient=2, secondary=P["commercial"])]
    denial_world(s, lg, tmp_path, rows, ar_rows=ar)
    _run(s)
    got = _items(s, "signal='SECONDARY_NOT_BILLED'")
    assert len(got) == 1 and got[0]["shortfall_cents"] == 3000, got
    # the second payer's real $0 answer (its reason that day): answered, nothing to bill
    load(s, lg, tmp_path, [adj(F.link_no(8), "CO-23", 30, patient=2, posted=D(2026, 4, 20), payer=P["commercial"],
                               secondary=P["commercial"]),
                           charge(F.link_no(98), cpt="99212", patient=3, svc=D(2026, 4, 10), posted=D(2026, 5, 2))])
    lg.close()
    _run(s)
    assert _items(s, "signal='SECONDARY_NOT_BILLED'") == []


# ========================================================================== 2. C5 reads the part of the service
BC = ("BLUE CROSS TEST", "BCT01")


@pytest.mark.parametrize("kind", KINDS)
def test_an_ema_only_denial_of_the_reading_is_not_covered_by_an_835_for_the_technical_part(tmp_path, kind):
    """Patient 51's 88305 reading (the 837 says 88305-26) was denied in EMA (CO-197) and no remittance answers its bill.
    An 835 on another bill paid the same patient's 88305-TC that day: the technical part, not the reading. C5 lists the
    denial (before: the 835 line for the patient, date and code counted as coverage whatever its part, and the denial was
    on no list). Patient 53's reading denied the same way, with an 835 paying 88305-26 on another bill, is covered."""
    s, lg = _world(tmp_path, kind)
    claims = []
    for n in (51, 53):
        claims.append(clm837(F.claim_id(n), [("88305", 200, "20260302", "26")], payer=BC))
    send(s, tmp_path, claims, "20260303")
    for n, part in ((52, "TC"), (54, "26")):
        last, first = _last_first(n - 1)
        remit(s, trace=f"83500{n}", paid_on="2026-03-20",
              claims=[{"pcn": F.claim_id(n), "last": last, "first": first,
                       "lines": [(S, "88305", 120, 60, [("CO", "45", 60)], {"modifiers": json.dumps([part])})]}])
    rows, ar = [], []
    for n in (51, 53):
        rows += [charge(F.link_no(n), cpt="88305", amount=200, patient=_name(n), status="Denied"),
                 pay(F.link_no(n), f"EMAONLY{n}", 0, cpt="88305", patient=_name(n), posted=D(2026, 3, 25), status="Denied"),
                 adj(F.link_no(n), "CO-197", 200, cpt="88305", patient=_name(n), posted=D(2026, 3, 25), status="Denied")]
        ar.append(F.ar_row(claim=F.claim_id(n), link=F.link_no(n), status="Denied", denial=200, current=200, svc=SVC,
                           patient=_name(n), cpt="88305"))
    denial_world(s, lg, tmp_path, rows, ar_rows=ar)
    lg.close()
    _run(s)
    denied = {r["ema_link"] for r in _items(s, "signal='EMA_DENIED'")}
    assert F.link_no(51) in denied, denied
    assert F.link_no(53) not in denied, denied


# ============================================================================ 3. the Medicaid catch-all, read as fix6 reads it
@pytest.mark.parametrize("kind", KINDS)
def test_a_company_plan_in_the_medicaid_catch_all_is_never_the_states_medicaid(tmp_path, kind):
    """C6's same-payer reading (ema_checks._payer_matcher) and C5's same-day reading (_same_payer_named): a payment EMA
    posted under '<company> Medicaid Community Plan' (the payer-name rules put it in 'Medicaid (other)') is that company's,
    so it never pays a Kentucky Medicaid denial as Medicaid's own; a payment under 'Medicaid of Kentucky' is the state's."""
    s, lg = _world(tmp_path, kind)
    lg.close()
    same = ec._payer_matcher(s.conn, {})
    ky = {"payer_group": "Medicaid KY", "payer_name": "KENTUCKY MEDICAID"}
    company = "UNITEDHEALTHCARE COMMUNITY PLAN MEDICAID"
    state = "MEDICAID OF KENTUCKY"
    # the company's plan is in the catch-all; the state's own name may be read as the catch-all or, by the newer name
    # rules, as Kentucky's own group: either way the groups passed below decide
    assert codes.normalize_payer(company)[0] == "Medicaid (other)"
    assert same("Medicaid (other)", ky, company) is False
    assert same("Medicaid (other)", ky, state) is True
    assert same("Medicaid (other)", {"payer_group": "UnitedHealthcare Medicaid"}, company) is True
    assert ec._same_payer_named("Medicaid (other)", company, "Medicaid KY") is False
    assert ec._same_payer_named("Medicaid (other)", state, "Medicaid KY") is True
    assert ec._same_payer_named("Anthem / BCBS", "ANTHEM BLUE CROSS", "Anthem / BCBS") is True


# =========================================================== 4. the self-audit dates a line as the product dates its item
def _audit_gaps(s, today):
    from remitwatch.detect import ensure_schema
    ensure_schema(s.conn)
    selfcheck.self_audit(s.conn, today=today, force=True)
    return [dict(zip(("key", "category", "deadline", "cents"), r)) for r in s.conn.execute(
        "SELECT key, category, deadline, cents FROM selfcheck_audit_items WHERE list='gap' ORDER BY key")]


@pytest.mark.parametrize("kind", KINDS)
def test_the_self_audit_dates_a_kentucky_medicaid_denial_by_its_corrected_claim_window(tmp_path, kind):
    """Kentucky Medicaid (fee-for-service) takes no appeal of an ordinary claim denial: the way is a corrected or new claim
    within 12 months of the service (907 KAR 1:673), as the product dates the item. A denial answered 60 days ago is still
    work, and with no item it is a gap (before: the self-audit counted 35 days, the dispute's legal limit, from the answer,
    and set the line aside as past its window)."""
    s, lg = _world(tmp_path, kind)
    lg.close()
    remit(s, trace="555000111", paid_on="2026-04-01", payer="KENTUCKY MEDICAID",
          claims=[{"pcn": F.claim_id(61), "lines": [("2026-03-20", "99213", 150, 0, [("CO", "16", 150)])]}])
    gaps = [g for g in _audit_gaps(s, "2026-05-31") if g["category"] == "denial_in_window"]
    assert len(gaps) == 1 and gaps[0]["key"].endswith("|2026-03-20|99213"), gaps
    rule = codes.corrected_rule("Medicaid KY", "medicaid")
    assert gaps[0]["deadline"] == codes.window_end("2026-03-20", rule)


@pytest.mark.parametrize("kind", KINDS)
def test_the_self_audit_reads_a_second_payer_by_its_own_group_and_clock(tmp_path, kind):
    """A second payer never billed (answer key R5): its window is the second payer's own first-filing rule as it counts a
    claim it gets second (codes.second_claim_window, the product's), from the payer group its name gives. Before: the name
    was taken for the group and 'supplement' for the kind, so the window was a year's default the product never uses."""
    s, lg = _world(tmp_path, kind)
    SECOND = "AETNA"            # Aetna's own limit counts 90 days from the service: never a 'supplement' default of a year
    remit(s, trace="777000333", paid_on="2026-04-01",
          claims=[{"pcn": F.claim_id(71), "last": "SAMPLE", "first": "DELTA",
                   "lines": [(S, "99213", 150, 90, [("CO", "45", 30), ("PR", "2", 30)], {"allowed_cents": 12000})]}])
    rows = [charge(F.link_no(71), patient=2, secondary=SECOND),
            pay(F.link_no(71), "777000333", 90, patient=2, posted=D(2026, 4, 3), secondary=SECOND, status="Payment Pending"),
            adj(F.link_no(71), "CO-45", 30, patient=2, posted=D(2026, 4, 3), contractual=True, secondary=SECOND,
                status="Payment Pending")]
    ar = [F.ar_row(claim=F.claim_id(71), link=F.link_no(71), status="Payment Pending", current=30, aging=D(2026, 4, 3), svc=SVC,
                   patient=2, secondary=SECOND)]
    denial_world(s, lg, tmp_path, rows, ar_rows=ar)
    lg.close()
    gaps = [g for g in _audit_gaps(s, "2026-05-20") if g["category"] in ("secondary_not_billed", "open_balance_in_window")]
    assert len(gaps) == 1, gaps
    sgrp, skind = er.payer_group_of(s.conn, SECOND)
    rule = codes.filing_rule(sgrp, skind, payer_name=SECOND)
    safe, last, _how = codes.second_claim_window(rule, S, "2026-04-03", first_payer="Anthem / BCBS")
    assert gaps[0]["deadline"] == (last or safe), (gaps, safe, last, sgrp, skind)


# ============================================ 5. inside-the-window counts: the plan facts, and the item's own last day
def test_written_off_money_is_inside_the_window_the_payers_plan_gives():
    """C7 and C9 count written-off dollars still inside a window that can recover them. Anthem's Medicaid plans have one
    window in Kentucky (120 days for a denial since KRS 205.534, April 14, 2026) and another where the remittance does not
    say the state (60 days): EMA's payer name names the state, so the count reads the plan the product reads (before: no
    facts, so the shorter window, and the money counted as lost). A line an item covers is counted by the item's own date."""
    today = "2026-11-27"
    first, svc = "2026-08-19", "2026-08-01"
    name = "ANTHEM BLUE CROSS KENTUCKY MEDICAID"
    assert ec._still_recoverable("Anthem", "medicaid", first, svc, {}, today, payer=name) is True
    plain = codes.appeal_rule("Anthem", "medicaid")
    assert (ec._add(ec._add(first, -ec.DEFAULT_LAG_P95), plain["days"]) or "") < today      # what the count used to read
    assert ec._still_recoverable("Anthem", "medicaid", "2026-01-05", svc, {}, today, payer=name) is False
    assert ec._still_recoverable("Anthem", "medicaid", "2026-01-05", svc, {}, today, payer=name, own_deadline="2026-12-31") is True


# =============================================================== 6. rule 4.2: a code a payer almost never pays, per service
@pytest.mark.parametrize("kind", KINDS)
def test_a_code_the_payer_pays_in_the_end_is_not_measured_as_rarely_paid(tmp_path, kind):
    """Kentucky Medicaid denied 22 services of 11102 on its remittances (CO-16, one answer each) and then paid 20 of them
    on paper: EMA shows its insurance money on each, no 835 carries it. Rule 4.2's measure counts services by their final
    outcome, EMA's postings included: 20 paid of 22 (before: 0 of 22 answers, so a payable code looked like one the payer
    never pays, and the research's 'Medicaid (other)' rule reached its denials). One service denied and then paid on a
    second remittance is one paid service, never one paid and one unpaid answer."""
    s, lg = _world(tmp_path, kind)
    rows, ar = [], []
    for i in range(22):
        n = 500 + i
        last, first = _last_first(n)
        svc = (D(2026, 2, 2) + dt.timedelta(days=i)).isoformat()
        remit(s, trace=f"84000{n}", paid_on="2026-04-01", payer="KENTUCKY MEDICAID",
              claims=[{"pcn": F.claim_id(n), "last": last, "first": first, "lines": [(svc, "11102", 150, 0, [("CO", "16", 150)])]}])
        rows.append(charge(F.link_no(n), cpt="11102", amount=150, patient=_name(n), svc=D.fromisoformat(svc), posted=D.fromisoformat(svc)))
        if i < 20:
            rows.append(pay(F.link_no(n), f"PAPER{n}", 60, cpt="11102", patient=_name(n), svc=D.fromisoformat(svc), posted=D(2026, 5, 1),
                            payer="MEDICAID OF KENTUCKY", primary="MEDICAID OF KENTUCKY"))
        ar.append(F.ar_row(claim=F.claim_id(n), link=F.link_no(n), status="Payment Pending", current=0, svc=D.fromisoformat(svc),
                           patient=_name(n), cpt="11102"))
    # one more service: denied on one remittance, paid on the next
    last, first = _last_first(530)
    remit(s, trace="840000530", paid_on="2026-03-01", payer="KENTUCKY MEDICAID",
          claims=[{"pcn": F.claim_id(530), "last": last, "first": first, "lines": [("2026-02-25", "11102", 150, 0, [("CO", "16", 150)])]}])
    remit(s, trace="840000531", paid_on="2026-03-20", payer="KENTUCKY MEDICAID",
          claims=[{"pcn": F.claim_id(530), "last": last, "first": first, "lines": [("2026-02-25", "11102", 150, 60, [("CO", "45", 90)])]}])
    denial_world(s, lg, tmp_path, rows, ar_rows=ar)
    lg.close()
    det = _run(s)        # the run measures the pair for its denials (fold_payer_policy), on the lines it read
    assert det._paid_share("Medicaid KY", "11102") == (21, 23), det.__dict__.get("_share")


@pytest.mark.parametrize("kind", KINDS)
def test_a_payer_policy_rows_count_is_the_items_listed(tmp_path, kind):
    """The owner's table counts each code a payer almost never pays by the items the Worklist lists for it after every rule
    of the run (before: the count taken at the fold, 26 where 9 items were listed)."""
    from remitwatch import api_ema, rarely_paid as rp
    from remitwatch.detect import ensure_schema
    s, lg = _world(tmp_path, kind)
    lg.close()
    ensure_schema(s.conn)
    grp, code = "Medicare KY (CGS J15)", "J3490"
    key = rp.pattern_key(grp, code, "2026-03-02")
    s.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('ema:payer_policy', ?)",
                   (json.dumps({"at": "2026-05-03T10:00:00", "patterns": {key: {"group": grp, "code": code, "paid": 0, "answered": 30,
                                                                                "n": 26, "cents": 260000, "decision": "pursue",
                                                                                "default": True}}}),))
    for i in range(26):
        listed = i < 9
        s.conn.execute("""INSERT INTO findings (finding_id, signal, category, payer_group, procedure_code, service_date, paid_cents,
                          line_key, shortfall_cents, status, work_state, stale, evidence) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (f"pp{i:02d}", "DENIED_NONCOVERED", "denial", grp, code, "2026-03-02", 0, f"L{i}", 10000,
                        "open" if listed else "resolved", "work_now" if listed else "done", 0, "{}"))
    s.conn.commit()
    row = next(p for p in api_ema.policy_table(s.conn)["patterns"] if p["pattern"] == key)
    assert (row["items"], row["cents"]) == (9, 90000), row


# ========================================================================= 7. C6 counts the patient's copay
@pytest.mark.parametrize("kind", KINDS)
def test_a_later_payment_with_the_patients_copay_pays_the_item(tmp_path, kind):
    """Blue Cross denied claim 81's 99212 (CO-16, its usual rate $55.50). EMA then shows the payer's $15.50 on a paper check
    (no 835 carries it) and the patient's $40 copay on the line: the service brought $55.50, so C6 closes the item as
    paid (before: $15.50 was under half of $55.50 and the item stayed in Work now as 'partly paid'). With no copay on the
    line the same payment is a partial payment."""
    s, lg = _world(tmp_path, kind)
    for n in (81, 82, 83, 84):
        last, first = _last_first(n)
        remit(s, trace=f"91000{n}", paid_on="2026-03-28",
              claims=[{"pcn": F.claim_id(n), "last": last, "first": first, "lines": [(S, "99212", 90, 0, [("CO", "16", 90)])]}])
    rows, ar = [], []
    for n in (81, 82, 83, 84):
        rows += [charge(F.link_no(n), cpt="99212", amount=90, patient=_name(n)),
                 pay(F.link_no(n), f"91000{n}", 0, cpt="99212", patient=_name(n), posted=D(2026, 3, 31)),
                 adj(F.link_no(n), "CO-16", 90, cpt="99212", patient=_name(n), posted=D(2026, 3, 31))]
        if n in (81, 82):
            rows.append(pay(F.link_no(n), f"PAPER{n}", 15.50, cpt="99212", patient=_name(n), posted=D(2026, 4, 20)))
        elif n == 83:
            # no check: a posting that moved $15.50 of another line's payment onto this one, not a payment
            rows += [pay(F.link_no(n), None, 15.50, cpt="99212", patient=_name(n), posted=D(2026, 4, 20)),
                     pay(F.link_no(n), None, -15.50, cpt="99213", patient=_name(n), posted=D(2026, 4, 20))]
        else:
            # another payer's payment (a second payer), the patient's copay beside it
            rows.append(pay(F.link_no(n), f"PAPER{n}", 15.50, cpt="99212", patient=_name(n), posted=D(2026, 4, 20), payer=P["commercial"]))
        ar.append(F.ar_row(claim=F.claim_id(n), link=F.link_no(n), status="Payment Pending", current=0, svc=SVC, patient=_name(n),
                           cpt="99212"))
    for n in (81, 83, 84):
        rows.append(F.production_row(link=F.link_no(n), cpt="99212", pat_paid=40, patient=_name(n), svc=SVC, posted=SVC, payer="Patient"))
    denial_world(s, lg, tmp_path, rows, ar_rows=ar)
    lg.close()
    findings = [{"finding_id": "c6-81", "signal": "DENIED_MISSING_INFO", "category": "denial", "payer_group": "Anthem / BCBS",
                 "payer_name": "BLUE CROSS TEST", "patient_control_number": F.claim_id(81), "service_date": S, "procedure_code": "99212",
                 "line_key": "LK81", "expected_cents": 5550, "shortfall_cents": 5550, "paid_cents": 0, "payment_date": "2026-03-28"},
                {"finding_id": "c6-82", "signal": "DENIED_MISSING_INFO", "category": "denial", "payer_group": "Anthem / BCBS",
                 "payer_name": "BLUE CROSS TEST", "patient_control_number": F.claim_id(82), "service_date": S, "procedure_code": "99212",
                 "line_key": "LK82", "expected_cents": 5550, "shortfall_cents": 5550, "paid_cents": 0, "payment_date": "2026-03-28"}]
    findings += [dict(findings[1], finding_id=f"c6-{n}", patient_control_number=F.claim_id(n), line_key=f"LK{n}") for n in (83, 84)]
    # a second payer's EMA-only denial valued at the balance the first payer left: the patient's money on the line is the
    # first payer's share (a deductible), never this payer's copay beside its payment
    findings.append({"finding_id": "c6-sec", "signal": "EMA_DENIED", "category": "denial", "payer_group": "Anthem / BCBS",
                     "payer_name": "BLUE CROSS TEST", "patient_control_number": F.claim_id(81), "service_date": S, "procedure_code": "99212",
                     "expected_cents": 5550, "shortfall_cents": 5550, "payment_date": "2026-03-31", "ema_link": F.link_no(81),
                     "evidence": json.dumps({"ema_link": F.link_no(81), "lines": [["99212", S, 9000]], "first_denial_posted": "2026-03-31",
                                             "expected_basis": ["the balance left when this payer denied it, after the first payer's payment"]})})
    outcomes = ec.line_outcomes(s.conn, findings, code_once=lambda f: True, today=TODAY)
    got = ec.paid_in_ema(s.conn, findings, outcomes)
    assert outcomes["c6-81"]["outcome"] == "paid_after" and got["c6-81"]["action"] == "close", (outcomes["c6-81"], got.get("c6-81"))
    assert got["c6-81"]["paid_cents"] == 1550
    assert got["c6-82"]["action"] == "hint_partial", got.get("c6-82")
    # no check (a payment moved from another line), or another payer's payment: the copay does not make it paid
    assert outcomes["c6-83"]["outcome"] != "paid_after" and (got.get("c6-83") or {}).get("action") != "close", got.get("c6-83")
    assert (got.get("c6-sec") or {}).get("action") != "close", (outcomes.get("c6-sec"), got.get("c6-sec"))
    assert outcomes["c6-84"]["outcome"] != "paid_after" and (got.get("c6-84") or {}).get("action") not in ("close", "hint_other_payer"), got.get("c6-84")


# ======================================================================== 8. the review round (fx-ema-r1 and fx-ema-r2)
# The reviewers' failing tests (invented data), ported here, each on both store kinds. A payer spelling the name rules put
# in the Medicaid catch-all is one that stays there once fix-wave F8 reads EMA's 'Medicaid of Kentucky' as Kentucky's own.

def _uhc_share_world(s, *, g6001_last: bool):
    """UnitedHealthcare as primary: one 99213 denial (claim 61), 25 services of 99213 it paid, and 22 services of G6001 it
    denied (CO-197), one per invented patient. The 835 lines are read in that order when g6001_last, else the G6001
    denials come first."""
    def g6001():
        for n in range(22):
            last, first = _last_first(800 + n)
            remit(s, trace=f"7776{n:05d}", paid_on="2026-02-10", payer="UNITEDHEALTHCARE TEST",
                  claims=[{"pcn": F.claim_id(800 + n), "last": last, "first": first, "member": f"J{n:011d}",
                           "lines": [("2026-02-02", "G6001", 260, 0, [("CO", "197", 260)])]}])
    if not g6001_last:
        g6001()
    remit(s, trace=F.check_no(61), paid_on="2026-03-28", payer="UNITEDHEALTHCARE TEST",
          claims=[{"pcn": F.claim_id(61), "lines": [(S, "99213", 150, 0, [("CO", "197", 150)])]}])
    for n in range(25):
        last, first = _last_first(300 + n)
        remit(s, trace=f"7775{n:05d}", paid_on="2026-02-10", payer="UNITEDHEALTHCARE TEST",
              claims=[{"pcn": F.claim_id(300 + n), "last": last, "first": first, "member": f"P{n:011d}",
                       "lines": [("2026-02-03", "99213", 150, 80, [("CO", "45", 70)])]}])
    if g6001_last:
        g6001()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("g6001_last,asked,want", [(True, "99213", (25, 26)), (False, "G6001", (0, 22))])
def test_the_first_paid_share_of_a_run_answers_for_the_code_asked(tmp_path, kind, g6001_last, asked, want):
    """fx-ema-r1-1, fx-ema-r2-1 (rule 4.2's measure, Detector._paid_share): the call that builds the run's cache answers for
    the code asked, never for the last service's code its loop read (the first question of a run, before: G6001's (0, 22)
    for 99213, a code the payer pays, or None for G6001 when a 99213 line was read last)."""
    s, lg = _world(tmp_path, kind)
    lg.close()
    _uhc_share_world(s, g6001_last=g6001_last)
    det = _run(s)
    det.__dict__.pop("_share", None)            # a fresh run's cache: the next question is the first one again
    first = det._paid_share("UnitedHealthcare", asked)
    second = det._paid_share("UnitedHealthcare", asked)
    assert (first, second) == (want, want), (first, second)


@pytest.mark.parametrize("kind", KINDS)
def test_a_code_the_payer_pays_never_lands_in_the_owners_payer_policy_table(tmp_path, kind):
    """fx-ema-r2-1: the run's first denial item is UnitedHealthcare's 99213 (claim 61) and the last 835 lines read are its
    G6001 denials. The owner's table (meta ema:payer_policy) holds G6001's pattern and never one for 99213, which the payer
    paid on 25 of the 26 services it answered (before: '...|99213||payer_policy', paid 0 of 22, which an Accept would have
    taken off Work now)."""
    s, lg = _world(tmp_path, kind)
    _uhc_share_world(s, g6001_last=True)
    load(s, lg, tmp_path, [charge(F.link_no(99), cpt="99212", patient=3, svc=D(2026, 3, 20), posted=D(2026, 5, 1),
                                  status="Payment Pending")])
    lg.close()
    _run(s)
    pol = json.loads(s.conn.execute("SELECT value FROM meta WHERE key='ema:payer_policy'").fetchone()[0])["patterns"]
    wrong = {k: (v.get("paid"), v.get("answered"), v.get("n")) for k, v in pol.items() if "|99213|" in k}
    assert wrong == {}, wrong
    assert "UnitedHealthcare|G6001||payer_policy" in pol, sorted(pol)


@pytest.mark.parametrize("kind", KINDS)
def test_an_accepted_never_paid_pattern_takes_every_one_of_its_items_off_the_list(tmp_path, kind):
    """fx-ema-r1-1: the owner accepts UnitedHealthcare's G6001 pattern (22 denials as primary, none paid; a 99213 it paid is
    the last 835 line read). All 22 items leave the Worklist, the first one the fold reads too, and the run counts 22."""
    s, lg = _world(tmp_path, kind)
    for n in range(22):
        last, first = _last_first(700 + n)
        remit(s, trace=f"7886{n:05d}", paid_on="2026-02-10", payer="UNITEDHEALTHCARE TEST",
              claims=[{"pcn": F.claim_id(700 + n), "last": last, "first": first, "member": f"Q{n:011d}",
                       "lines": [("2026-02-02", "G6001", 260, 0, [("CO", "197", 260)])]}])
    last, first = _last_first(799)
    remit(s, trace="788799999", paid_on="2026-02-20", payer="UNITEDHEALTHCARE TEST",
          claims=[{"pcn": F.claim_id(799), "last": last, "first": first, "member": "Q99999999999",
                   "lines": [("2026-02-03", "99213", 150, 90, [("CO", "45", 60)])]}])
    denial_world(s, lg, tmp_path)               # EMA's reports on file: the owner's table is built
    lg.close()
    _run(s)
    ec.set_policy(s.conn, "UnitedHealthcare|G6001||payer_policy", "accept", "owner")
    det = _run(s)
    left = [f["patient_control_number"] for f in _items(s, "procedure_code='G6001' AND status='open'")]
    assert left == [], left
    assert det.ema_counts["payer_policy_835"] == 22, det.ema_counts["payer_policy_835"]


def _ky_11102_denials(s, rows, ar, base, paid_rows):
    """22 services of 11102 Kentucky Medicaid denied as primary on its remittances (CO-16), one per invented patient;
    paid_rows(n, svc) gives the EMA rows that follow for service n (none for the last two)."""
    for i in range(22):
        n = base + i
        last, first = _last_first(n)
        svc = D(2026, 2, 2) + dt.timedelta(days=i)
        remit(s, trace=f"84100{n}", paid_on="2026-04-01", payer="KENTUCKY MEDICAID",
              claims=[{"pcn": F.claim_id(n), "last": last, "first": first,
                       "lines": [(svc.isoformat(), "11102", 150, 0, [("CO", "16", 150)])]}])
        rows.append(charge(F.link_no(n), cpt="11102", amount=150, patient=_name(n), svc=svc, posted=svc,
                           primary="KENTUCKY MEDICAID"))
        if i < 20:
            rows += paid_rows(n, svc)
        ar.append(F.ar_row(claim=F.claim_id(n), link=F.link_no(n), status="Payment Pending", current=0, svc=svc,
                           patient=_name(n), cpt="11102", primary="KENTUCKY MEDICAID"))


@pytest.mark.parametrize("kind", KINDS)
def test_a_payment_ema_shows_taken_back_is_not_a_paid_service(tmp_path, kind):
    """fx-ema-r2-2 (rule 4.2 per service, _share_ema_paid): Kentucky Medicaid denied 22 services of 11102; EMA shows a $60
    paper payment on 20 of them, then the payer took each back in full on a later posting (-$60): no service was paid in
    the end, so the measure says 0 of 22 (before: the positive rows alone, 20 of 22)."""
    s, lg = _world(tmp_path, kind)
    rows, ar = [], []

    def paid_then_taken(n, svc):
        return [pay(F.link_no(n), f"PAPER{n}", 60, cpt="11102", patient=_name(n), svc=svc, posted=D(2026, 5, 1),
                    payer="KENTUCKY MEDICAID", primary="KENTUCKY MEDICAID"),
                pay(F.link_no(n), f"RECOUP{n}", -60, cpt="11102", patient=_name(n), svc=svc, posted=D(2026, 5, 20),
                    payer="KENTUCKY MEDICAID", primary="KENTUCKY MEDICAID")]
    _ky_11102_denials(s, rows, ar, 600, paid_then_taken)
    denial_world(s, lg, tmp_path, rows, ar_rows=ar)
    lg.close()
    net = dict(s.conn.execute("""SELECT l.link, SUM(y.ins_paid) FROM ema_line_payments y JOIN ema_lines l
                                 ON l.link = y.link AND l.svc = y.svc AND l.cpt = y.cpt WHERE l.cpt = '11102' GROUP BY l.link"""))
    assert net and all(v == 0 for v in net.values()), net          # each line nets to $0 of insurance money in EMA
    det = _run(s, today="2026-06-10")
    assert det._paid_share("Medicaid KY", "11102") == (0, 22), det.__dict__.get("_share")


@pytest.mark.parametrize("kind", KINDS)
def test_a_company_plan_in_the_medicaid_catch_all_never_pays_for_the_states_medicaid(tmp_path, kind):
    """fx-ema-r2-3 (rule 4.2 per service, read as fix6 reads the catch-all): Kentucky Medicaid denied 22 services of 11102;
    EMA shows 20 of them paid by a company's Medicaid plan ('UNITEDHEALTHCARE COMMUNITY PLAN MEDICAID', which the name
    rules put in 'Medicaid (other)'). That money is the company's, never Kentucky Medicaid's: 0 of 22 (before: 20)."""
    s, lg = _world(tmp_path, kind)
    rows, ar = [], []
    company = "UNITEDHEALTHCARE COMMUNITY PLAN MEDICAID"
    assert codes.normalize_payer(company)[0] == "Medicaid (other)"

    def company_paid(n, svc):
        return [pay(F.link_no(n), f"UHCCP{n}", 60, cpt="11102", patient=_name(n), svc=svc, posted=D(2026, 5, 1),
                    payer=company, primary="KENTUCKY MEDICAID")]
    _ky_11102_denials(s, rows, ar, 700, company_paid)
    denial_world(s, lg, tmp_path, rows, ar_rows=ar)
    lg.close()
    groups = {g for (g,) in s.conn.execute("SELECT DISTINCT payer_group FROM ema_line_payments WHERE cpt = '11102'")}
    assert groups == {"Medicaid (other)"}, groups
    det = _run(s, today="2026-06-10")
    assert det._paid_share("Medicaid KY", "11102") == (0, 22), det.__dict__.get("_share")


@pytest.mark.parametrize("kind", KINDS)
def test_the_medicaid_catch_alls_research_speaks_only_for_a_code_it_rarely_pays(tmp_path, kind):
    """fx-ema-r1-5 (rule 4.2, rarely_paid.applies): the research's entries for 'Medicaid (other)' name the catch-all, which
    holds many payers (a state's program under a spelling the name rules do not place, the companies' Medicaid plans).
    A catch-all payer denied 22 services of 11102 on its remittances and EMA shows it paid 20 of them on paper: a code it
    pays, so no payer-policy pattern for it in the owner's table (before: the research put one there whatever the
    measure said, which an Accept would turn into write-offs of payable services). The same payer's 22 denials of 88305,
    none paid, are a code it rarely pays: the pattern is there, with the research's verdict and the measure (0 of 22)."""
    s, lg = _world(tmp_path, kind)
    payer = "MEDICAID OF OHIO"                  # a state's own program the name rules leave in the catch-all
    assert codes.normalize_payer(payer)[0] == "Medicaid (other)"
    rows, ar = [], []
    for code, base, paid in (("11102", 600, 20), ("88305", 640, 0)):
        for i in range(22):
            n = base + i
            last, first = _last_first(n)
            svc = D(2026, 2, 2) + dt.timedelta(days=i)
            remit(s, trace=f"85000{n}", paid_on="2026-03-01", payer=payer,
                  claims=[{"pcn": F.claim_id(n), "last": last, "first": first,
                           "lines": [(svc.isoformat(), code, 150, 0, [("CO", "16", 150)])]}])
            rows.append(charge(F.link_no(n), cpt=code, amount=150, patient=_name(n), svc=svc, posted=svc, primary=payer))
            if i < paid:            # paid in the end on paper: EMA shows the payer's money on the service
                rows.append(pay(F.link_no(n), f"PAPER{n}", 60, cpt=code, patient=_name(n), svc=svc, posted=D(2026, 4, 1),
                                payer=payer, primary=payer))
            ar.append(F.ar_row(claim=F.claim_id(n), link=F.link_no(n), status="Payment Pending", current=0, svc=svc,
                               patient=_name(n), cpt=code))
    denial_world(s, lg, tmp_path, rows, ar_rows=ar)
    lg.close()
    det = _run(s)
    assert det._paid_share("Medicaid (other)", "11102") == (20, 22)
    pol = json.loads((s.conn.execute("SELECT value FROM meta WHERE key='ema:payer_policy'").fetchone() or ["{}"])[0] or "{}")
    pats = pol.get("patterns") or {}
    assert not [k for k in pats if k.startswith("Medicaid (other)|11102")], {k: v for k, v in pats.items() if "|11102|" in k}
    got = pats.get("Medicaid (other)|88305||payer_policy") or {}
    assert (got.get("paid"), got.get("answered"), got.get("researched")) == (0, 22, "claim"), got



@pytest.mark.parametrize("kind", KINDS)
def test_the_owners_table_shows_the_measure_of_a_researched_pattern_the_payer_otherwise_pays(tmp_path, kind):
    """fx-ema-r1-5, its second part (C5, ema_checks.ema_denials): a researched pattern of the owner's table says how often
    the payer paid the code whenever the measure is known, rare or not. Blue Cross (Anthem) denied a 2026 77600 that only
    EMA saw (CO-97; the research's rule makes 77600 not payable from 2026); EMA shows it paid 22 services of 77600 in 2025.
    The pattern 'Anthem / BCBS|77600|from 2026-01-01|payer_policy' carries paid 22 of 23 answered (before: blank, since
    the pair is not rare)."""
    s, lg = _world(tmp_path, kind)
    rows = []
    for i in range(22):
        n = 500 + i
        svc = D(2025, 3, 3) + dt.timedelta(days=i)
        rows += [charge(F.link_no(n), cpt="77600", amount=400, patient=_name(n), svc=svc, posted=svc),
                 pay(F.link_no(n), f"PAID{n}", 150, cpt="77600", patient=_name(n), svc=svc, posted=svc + dt.timedelta(days=20))]
    n, svc = 530, D(2026, 2, 2)
    rows += [charge(F.link_no(n), cpt="77600", amount=400, patient=_name(n), svc=svc, posted=svc),
             pay(F.link_no(n), "EMAONLY530", 0, cpt="77600", patient=_name(n), svc=svc, posted=D(2026, 3, 2)),
             adj(F.link_no(n), "CO-97", 400, cpt="77600", patient=_name(n), svc=svc, posted=D(2026, 3, 2))]
    denial_world(s, lg, tmp_path, rows)
    lg.close()
    _run(s)
    lo = json.loads(s.conn.execute("SELECT value FROM meta WHERE key = ?", (ec.C5_LEFT_OUT_KEY,)).fetchone()[0])
    got = ((lo.get("payer_policy") or {}).get("patterns") or {}).get("Anthem / BCBS|77600|from 2026-01-01|payer_policy") or {}
    assert (got.get("paid"), got.get("answered"), got.get("researched")) == (22, 23, "never_payable"), lo

def _c6_copay_case(s, lg, tmp_path, n, *, expected_usd, copay_usd, paid_usd):
    """Blue Cross denied claim n's 99213 (CO-16) and no 835 pays it; EMA shows the patient's copay paid at the visit and
    Blue Cross's later payment on a paper check (no allowed amount, nothing put on the patient). Returns the item."""
    last, first = _last_first(n)
    remit(s, trace=f"92000{n}", paid_on="2026-03-28",
          claims=[{"pcn": F.claim_id(n), "last": last, "first": first, "lines": [(S, "99213", 150, 0, [("CO", "16", 150)])]}])
    rows = [charge(F.link_no(n), cpt="99213", amount=150, patient=_name(n)),
            pay(F.link_no(n), f"92000{n}", 0, cpt="99213", patient=_name(n), posted=D(2026, 3, 31)),
            adj(F.link_no(n), "CO-16", 150, cpt="99213", patient=_name(n), posted=D(2026, 3, 31)),
            pay(F.link_no(n), f"PAPER{n}", paid_usd, cpt="99213", patient=_name(n), posted=D(2026, 4, 20))]
    if copay_usd:
        rows.append(F.production_row(link=F.link_no(n), cpt="99213", pat_paid=copay_usd, patient=_name(n), svc=SVC, posted=SVC,
                                     payer="Patient"))
    ar = [F.ar_row(claim=F.claim_id(n), link=F.link_no(n), status="Payment Pending", current=0, svc=SVC, patient=_name(n),
                   cpt="99213")]
    denial_world(s, lg, tmp_path, rows, ar_rows=ar)
    cents = int(round(expected_usd * 100))
    return {"finding_id": f"cp-{n}", "signal": "DENIED_MISSING_INFO", "category": "denial", "payer_group": "Anthem / BCBS",
            "payer_name": "BLUE CROSS TEST", "patient_control_number": F.claim_id(n), "service_date": S, "procedure_code": "99213",
            "line_key": f"LK{n}", "expected_cents": cents, "shortfall_cents": cents, "paid_cents": 0, "payment_date": "2026-03-28",
            "evidence": json.dumps({"basis": "the payer's usual rate"})}


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("copay_usd,paid_usd", [(40, 12.00), (60, 1.00)])
def test_a_small_payment_beside_the_copay_is_a_partial_payment(tmp_path, kind, copay_usd, paid_usd):
    """fx-ema-r1-2, fx-ema-r2-6 (C6 and the copay): the service brings $100; the patient paid a $40 copay (or $60), so
    Blue Cross owes $60 (or $40), and it paid $12 (or a token $1). The item is partly paid and stays listed: the payer's
    payment is judged against the share it owes, half of it at least, as a payment with no copay is judged against half of
    the whole (before: payer plus patient against half of the whole, so $12 + $40 and $1 + $60 closed it as paid)."""
    s, lg = _world(tmp_path, kind)
    f = _c6_copay_case(s, lg, tmp_path, 71, expected_usd=100, copay_usd=copay_usd, paid_usd=paid_usd)
    lg.close()
    outcomes = ec.line_outcomes(s.conn, [f], code_once=lambda f_: True, today=TODAY)
    o = outcomes[f["finding_id"]]
    assert (o["paid_after_same"], o.get("patient_paid")) == (int(paid_usd * 100), copay_usd * 100), o
    got = ec.paid_in_ema(s.conn, [f], outcomes)
    assert o["outcome"] != "paid_after", {k: o.get(k) for k in ("outcome", "paid_after_same", "patient_cover")}
    assert (got.get(f["finding_id"]) or {}).get("action") == "hint_partial", got.get(f["finding_id"])


@pytest.mark.parametrize("kind", KINDS)
def test_control_a_token_payment_with_no_copay_is_partial(tmp_path, kind):
    """Control (fx-ema-r2-6): the same $1.00 with no copay on the line is a partial payment."""
    s, lg = _world(tmp_path, kind)
    f = _c6_copay_case(s, lg, tmp_path, 72, expected_usd=100, copay_usd=0, paid_usd=1.00)
    lg.close()
    outcomes = ec.line_outcomes(s.conn, [f], code_once=lambda f_: True, today=TODAY)
    got = ec.paid_in_ema(s.conn, [f], outcomes)
    assert (got.get(f["finding_id"]) or {}).get("action") == "hint_partial", got.get(f["finding_id"])


@pytest.mark.parametrize("kind", KINDS)
def test_a_closure_the_patients_copay_decided_names_it_among_its_proof(tmp_path, kind):
    """fx-ema-r1-3 (C6's copay closure, detect._close_from_ema): Blue Cross denied claim 81's 99212-like visit, valued $55.50;
    EMA shows the payer's $15.50 on a paper check and the patient's $40.00 on the line. C6 closes it as paid on the two
    together, so its proof rows show both and add up to the $55.50 it calls paid (before: the payer's $15.50 alone, the
    patient's money in the note's words only)."""
    s, lg = _world(tmp_path, kind)
    f = _c6_copay_case(s, lg, tmp_path, 81, expected_usd=55.50, copay_usd=40, paid_usd=15.50)
    lg.close()
    outcomes = ec.line_outcomes(s.conn, [f], code_once=lambda f_: True, today=TODAY)
    r = ec.paid_in_ema(s.conn, [f], outcomes).get(f["finding_id"]) or {}
    assert r.get("action") == "close" and r.get("patient_cents") == 4000, r
    det = Detector(s, run_id="r-proof", today=TODAY)
    # the state a run holds when run_ema_outcomes closes an item (the rest of the run is not needed for one closure)
    det.used_payments, det.used_units, det.lines_by_claim, det.partly_paid = set(), {}, {}, {}
    det.findings = {f["finding_id"]: f}
    det._close_from_ema(f, r, outcomes[f["finding_id"]], "EMA shows it paid", r.get("note") or "")
    proof = (json.loads(f["evidence"]).get("paid_later") or {}).get("proof") or []
    assert sorted((p.get("kind"), p.get("amount_cents")) for p in proof) == [("patient_payment", 4000), ("payment", 1550)], proof
    assert sum(int(p["amount_cents"]) for p in proof) == 5550


@pytest.mark.parametrize("kind", KINDS)
def test_a_second_payers_zero_on_the_check_that_paid_the_claims_other_line_is_its_answer(tmp_path, kind):
    """fx-ema-r1-4 (SECONDARY_NOT_BILLED, detect._ema_lines_for): Blue Cross paid claim 8's two lines and left $30 on 99213
    and $40 on 17000; a commercial second payer is on file. Its own paper check (no 835) pays 17000's $40 and lists 99213 at
    $0: the second payer was billed and adjudicated the claim on that check, so 99213 is no share 'never billed' (before:
    the $0 row with no reason of its own was read as no answer and the line was listed). A $0 row alone on a check that
    paid nothing else of the claim stays no answer (the fixer's test above)."""
    s, lg = _world(tmp_path, kind)
    remit(s, trace="777000333", paid_on="2026-04-01",
          claims=[{"pcn": F.claim_id(8), "last": "SAMPLE", "first": "DELTA",
                   "lines": [(S, "99213", 150, 90, [("CO", "45", 30), ("PR", "2", 30)], {"allowed_cents": 12000}),
                             (S, "17000", 120, 60, [("CO", "45", 20), ("PR", "2", 40)], {"allowed_cents": 10000})]}])
    sec = P["commercial"]
    rows = [charge(F.link_no(8), patient=2, secondary=sec),
            charge(F.link_no(8), cpt="17000", amount=120, patient=2, secondary=sec),
            pay(F.link_no(8), "777000333", 90, patient=2, posted=D(2026, 4, 3), secondary=sec),
            adj(F.link_no(8), "CO-45", 30, patient=2, posted=D(2026, 4, 3), contractual=True, secondary=sec),
            pay(F.link_no(8), "777000333", 60, cpt="17000", patient=2, posted=D(2026, 4, 3), secondary=sec),
            adj(F.link_no(8), "CO-45", 20, cpt="17000", patient=2, posted=D(2026, 4, 3), contractual=True, secondary=sec),
            pay(F.link_no(8), "SECPAPER1", 40, cpt="17000", patient=2, posted=D(2026, 4, 20), payer=sec, secondary=sec),
            pay(F.link_no(8), "SECPAPER1", 0, patient=2, posted=D(2026, 4, 20), payer=sec, secondary=sec)]
    ar = [F.ar_row(claim=F.claim_id(8), link=F.link_no(8), status="Payment Pending", current=30, aging=D(2026, 4, 3), svc=SVC,
                   patient=2, secondary=sec)]
    denial_world(s, lg, tmp_path, rows, ar_rows=ar)
    lg.close()
    det = _run(s)
    assert _items(s, "signal='SECONDARY_NOT_BILLED'") == []
    assert det.ema_counts["snb_second_answered_ema"] >= 1


def _second_payer_world(s, lg, tmp_path, *, bare: bool):
    """Blue Cross paid claim 71's 99213 ($90) and left $30; Aetna is the second payer on file in EMA and was never billed.
    With bare=True a batch check of Aetna's lists the line at $0 on April 20 with nothing else: a bare $0 row."""
    second = "AETNA"
    remit(s, trace="777000444", paid_on="2026-04-01",
          claims=[{"pcn": F.claim_id(71), "last": "SAMPLE", "first": "DELTA",
                   "lines": [(S, "99213", 150, 90, [("CO", "45", 30), ("PR", "2", 30)], {"allowed_cents": 12000})]}])
    rows = [charge(F.link_no(71), patient=2, secondary=second),
            pay(F.link_no(71), "777000444", 90, patient=2, posted=D(2026, 4, 3), secondary=second, status="Payment Pending"),
            adj(F.link_no(71), "CO-45", 30, patient=2, posted=D(2026, 4, 3), contractual=True, secondary=second,
                status="Payment Pending")]
    if bare:
        rows.append(pay(F.link_no(71), "BATCH-AET", 0, patient=2, posted=D(2026, 4, 20), payer=second, secondary=second,
                        status="Payment Pending"))
    ar = [F.ar_row(claim=F.claim_id(71), link=F.link_no(71), status="Payment Pending", current=30, aging=D(2026, 4, 3), svc=SVC,
                   patient=2, secondary=second)]
    denial_world(s, lg, tmp_path, rows, ar_rows=ar)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("bare", [True, False])
def test_the_self_audit_reads_a_second_payers_bare_zero_row_as_no_answer(tmp_path, kind, bare):
    """fx-ema-r2-4 (the brief's A1, the self-audit's second-payer rule, selfcheck._cov_secondary): Aetna's only row on the
    line is a bare $0 row (no allowed amount, no reason that day). The audit reads it as no answer, as the product does,
    and names the $30 share as a gap whether the row is there or not (before: any row of a payer other than the primary
    hid the share from the audit)."""
    s, lg = _world(tmp_path, kind)
    _second_payer_world(s, lg, tmp_path, bare=bare)
    lg.close()
    if bare:
        rows = [tuple(r) for r in s.conn.execute(f"""SELECT y.role, y.ins_paid, y.allowed, {ec.zero_answer_sql('y')}
                                                     FROM ema_line_payments y WHERE y.link = ? AND y.payer = 'AETNA'""",
                                                  (F.link_no(71),))]
        assert rows == [("secondary", 0, 0, 0)], rows
    gaps = [g for g in _audit_gaps(s, "2026-05-20") if g["category"] in ("secondary_not_billed", "open_balance_in_window")]
    assert len(gaps) == 1 and gaps[0]["cents"] == 3000, gaps


def _plan_denial(s, *, trace, payer, cfi, plan, svc, paid_on, carc, pcn_n):
    remit(s, trace=trace, paid_on=paid_on, payer=payer,
          claims=[{"pcn": F.claim_id(pcn_n), "lines": [(svc, "99213", 150, 0, [("CO", carc, 150)])]}])
    s.conn.execute("UPDATE claims SET claim_filing_indicator = ?, contract_class = ? WHERE patient_control_number = ?",
                   (cfi, plan, F.claim_id(pcn_n)))
    s.conn.commit()


def _product_end(grp, kind, answered, svc, f):
    """The last day the product gives the line (states.windows: codes.rule_facts of the item), read as the self-audit's
    _appeal_end reads a rule."""
    facts = {k: v for k, v in codes.rule_facts(f).items() if v is not None}
    return selfcheck._appeal_end(grp, kind, answered, svc, **facts)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("payer,plan,want", [("ANTHEM BLUE CROSS TEST", "KENTUCKY MEDICAID TEST PLAN", "2026-10-04"),
                                             ("ANTHEM BLUE CROSS KENTUCKY TEST", None, "2026-10-04")])
def test_the_self_audit_reads_an_anthem_medicaid_plans_state_from_its_contract_class(tmp_path, kind, payer, plan, want):
    """fx-ema-r2-5 (the brief's D1, codes.rule_facts in the self-audit): an Anthem Medicaid denial whose payer's name names
    no state but whose plan (the 835's contract class) is Kentucky's. The product reads the plan and gives Kentucky's 90 days
    with KRS 205.534's limit; so does the audit, and on August 20 the unlisted denial is a gap dated as the product dates
    it (before: Anthem Medicaid with no state, 60 days, so the audit set it aside as past its window). The second case is
    the control: the payer's name says Kentucky."""
    s, lg = _world(tmp_path, kind)
    lg.close()
    cfi = "MC"
    _plan_denial(s, trace="555000777", payer=payer, cfi=cfi, plan=plan, svc="2026-05-20", paid_on="2026-06-01", carc="16",
                 pcn_n=77)
    grp, gkind = selfcheck._group_kind(payer, cfi)
    got_end = _product_end(grp, gkind, "2026-06-01", "2026-05-20", {"payer_name": payer, "claim_filing_indicator": cfi,
                                                                  "contract_class": plan, "carc": "16", "paid_cents": 0})
    assert got_end == want, got_end
    gaps = [g for g in _audit_gaps(s, "2026-08-20") if g["category"] == "denial_in_window"]
    assert len(gaps) == 1 and gaps[0]["deadline"] == want, gaps


@pytest.mark.parametrize("kind", KINDS)
def test_the_self_audit_dates_a_federal_employee_program_clinical_denial_as_the_product_does(tmp_path, kind):
    """fx-ema-r2-5, the other way: a Federal Employee Program clinical denial (CO-50) billed through Blue Cross. The product
    reads the plan and dates it by the FEP's reconsideration, six months from the notice (2026-07-10); on September 1 it is
    past its window, so the audit counts no gap (before: Anthem's commercial two years, a gap dated 2028-01-10)."""
    s, lg = _world(tmp_path, kind)
    lg.close()
    payer, cfi, plan = "BLUE CROSS TEST", "BL", "FEDERAL EMPLOYEE PROGRAM TEST"
    _plan_denial(s, trace="555000888", payer=payer, cfi=cfi, plan=plan, svc="2026-01-02", paid_on="2026-01-10", carc="50",
                 pcn_n=78)
    grp, gkind = selfcheck._group_kind(payer, cfi)
    want = _product_end(grp, gkind, "2026-01-10", "2026-01-02", {"payer_name": payer, "claim_filing_indicator": cfi,
                                                                "contract_class": plan, "carc": "50", "paid_cents": 0})
    assert want == "2026-07-10", want
    gaps = [g for g in _audit_gaps(s, "2026-09-01") if g["category"] == "denial_in_window"]
    assert gaps == [], gaps


def _timely_world(s, lg, tmp_path, *, second_item: bool):
    """Claim 41's 99213 (January 5): Medicare denied it for timely filing; EMA wrote the $150 off under CO-29 on February 3.
    Medicare's own item for the line is on file with its last day, June 1 (passed by August 1). With second_item, a second
    payer's item on the same bill, date and code is on file too, with a later last day (January 31, 2027)."""
    from remitwatch.detect import ensure_schema
    remit(s, trace="555000041", paid_on="2026-02-01", payer="MEDICARE TEST REGION",
          claims=[{"pcn": F.claim_id(41), "lines": [("2026-01-05", "99213", 150, 0, [("CO", "29", 150)])]}])
    svc = D(2026, 1, 5)
    rows = [charge(F.link_no(41), patient=1, svc=svc, posted=svc, primary=P["medicare"]),
            pay(F.link_no(41), "555000041", 0, patient=1, svc=svc, posted=D(2026, 2, 3), payer=P["medicare"], primary=P["medicare"]),
            adj(F.link_no(41), "CO-29", 150, patient=1, svc=svc, posted=D(2026, 2, 3), payer=P["medicare"], primary=P["medicare"])]
    ar = [F.ar_row(claim=F.claim_id(41), link=F.link_no(41), status="Denied", denial=150, current=0, svc=svc, patient=1,
                   primary=P["medicare"])]
    load(s, lg, tmp_path, rows, ar_rows=ar)
    ensure_schema(s.conn)
    items = [("r2-med", "Medicare KY (CGS J15)", "2026-06-01")] + ([("r2-sec", "Testcare Health", "2027-01-31")] if second_item else [])
    for fid, grp, last in items:
        s.conn.execute("""INSERT INTO findings (finding_id, signal, category, payer_group, patient_control_number, service_date,
                          procedure_code, appeal_deadline, status, stale, evidence) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                       (fid, "DENIED_TIMELY", "denial", grp, F.claim_id(41), "2026-01-05", "99213", last, "open", 0, "{}"))
    s.conn.commit()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("second_item", [True, False])
def test_a_second_payers_later_item_never_makes_the_first_payers_write_off_recoverable(tmp_path, kind, second_item):
    """fx-ema-r2-7 (C7 and C9 count by the item's own deadline): Medicare's timely-filing write-off is dated by Medicare's
    own item for the line (June 1, passed by August 1), never by a second payer's item on the same bill, date and code
    (before: the latest item of any payer, January 31, 2027, so the written-off money was counted as recoverable). The
    case with Medicare's item alone is the control."""
    s, lg = _world(tmp_path, kind)
    _timely_world(s, lg, tmp_path, second_item=second_item)
    lg.close()
    got = ec.timely_writeoffs(s.conn, today="2026-08-01")
    assert got["cents"] == 15000 and got["inside_cents"] == 0, got


@pytest.mark.parametrize("kind", KINDS)
def test_an_item_with_an_empty_deadline_never_stops_the_write_off_counts(tmp_path, kind):
    """fx-ema-r2-8 (malformed input): an item on file whose appeal_deadline is an empty string (not NULL) is read as no
    date; C9 (timely_writeoffs) and C7 (denial_as_contractual) still count (before: max() of nothing raised ValueError
    and stopped both for the whole store)."""
    s, lg = _world(tmp_path, kind)
    _timely_world(s, lg, tmp_path, second_item=False)
    lg.close()
    s.conn.execute("UPDATE findings SET appeal_deadline = '' WHERE finding_id = 'r2-med'")
    s.conn.commit()
    got = ec.timely_writeoffs(s.conn, today="2026-08-01")
    assert got["cents"] == 15000, got
    assert isinstance(ec.denial_as_contractual(s.conn, today="2026-08-01"), list)


# ============================================ 9. routed from fix-wave F8: the EMA summaries read the payer-name rules of now
@pytest.mark.parametrize("kind", KINDS)
def test_an_ema_denial_from_the_va_network_named_after_its_administrator_is_the_vas(tmp_path, kind):
    """F8's need (ema_ledger.refresh learns the aliases): EMA names the VA's community care network after its administrator,
    and its weekly exports carry the network's payer id (VACCN) on that name's rows. A denial EMA posted for such a claim,
    with no 835, is the VA's: group 'VA Community Care' and its windows (before: nothing called learn_payer_aliases, so the
    item was the administrator's commercial group with its 12 months)."""
    from tests import test_ema_checks as TC
    from tests.test_workable import ema
    s = Store(str(tmp_path / "s.sqlite")) if kind == "file" else Store(":memory:", compact=True)
    er.ensure_schema(s.conn)
    s.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('ema:key_salt', ?)", (TC.SALT_HEX,))
    s.conn.commit()
    lg = el.Ledger.open(str(tmp_path / "ema_ledger.sqlite"))
    try:
        name = F.PAYERS["optum"]
        for i in range(25):                 # the weekly exports' rows of that name carry the network's payer id
            ema(s.conn, f"CB0000V{i:02d}C017", payer=name, payer_id="VACCN", claim_status="Resolved/Paid")
        s.conn.commit()
        TC.denial_world(s, lg, tmp_path, TC.c5_rows(link=2, reason="CO-16", payer=name))
        got = [i for i in ec.ema_denials(s.conn, today=TC.TODAY) if i["claim"]["payer_name"] == name]
        assert len(got) == 1, got
        assert (got[0]["claim"]["payer_group"], got[0]["claim"]["payer_kind"]) == ("VA Community Care", "va"), got[0]["claim"]
    finally:
        lg.close()
        s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_the_ema_summaries_take_a_changed_name_rule_without_a_new_report(tmp_path, kind, monkeypatch):
    """F8's need (ema_ledger.refresh re-groups on a changed name rule): the EMA summaries keep the payer group the name rules
    gave when they were last built, and Part A runs only when the posting ledger changed. A release that changes the name
    rules (here one that reads EMA's 'Medicaid of Kentucky' as Kentucky's own program, as F8 does) re-groups the stored
    lines and the A/R's rows at the next refresh, without waiting for the next Production report (before: the old group,
    and its windows, until then)."""
    from tests import test_ema_checks as TC
    name = "Medicaid of Kentucky"
    old_rules = [r for r in codes.PAYER_RULES if r[1] != "Medicaid KY"]       # the release before: the catch-all
    new_rules = [(r"MEDICAID OF KENTUCKY", "Medicaid KY", "medicaid")] + list(codes.PAYER_RULES)
    s = Store(str(tmp_path / "s.sqlite")) if kind == "file" else Store(":memory:", compact=True)
    er.ensure_schema(s.conn)
    s.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('ema:key_salt', ?)", (TC.SALT_HEX,))
    s.conn.commit()
    lg = el.Ledger.open(str(tmp_path / "ema_ledger.sqlite"))
    try:
        monkeypatch.setattr(codes, "PAYER_RULES", old_rules)
        er._GROUPERS.clear()
        ar = [F.ar_row(claim=F.claim_id(2), link=F.link_no(2), status="Denied", denial=150, current=150, svc=SVC, patient=1,
                       primary=name)]
        TC.load(s, lg, tmp_path, TC.c5_rows(link=2, reason="CO-16", payer=name), ar_rows=ar)
        lines = {g for (g,) in s.conn.execute("SELECT DISTINCT payer_group FROM ema_lines WHERE primary_payer = ?", (name,))}
        ars = {g for (g,) in s.conn.execute("SELECT DISTINCT payer_group FROM ema_ar WHERE primary_payer = ?", (name,))}
        assert (lines, ars) == ({"Medicaid (other)"}, {"Medicaid (other)"}), (lines, ars)    # what the earlier release stored
        monkeypatch.setattr(codes, "PAYER_RULES", new_rules)            # the release with the new rule starts
        er._GROUPERS.clear()
        el.refresh(s, lg, parts="auto", log=TC.Log())
        lines = {g for (g,) in s.conn.execute("SELECT DISTINCT payer_group FROM ema_lines WHERE primary_payer = ?", (name,))}
        ars = {g for (g,) in s.conn.execute("SELECT DISTINCT payer_group FROM ema_ar WHERE primary_payer = ?", (name,))}
        assert (lines, ars) == ({"Medicaid KY"}, {"Medicaid KY"}), (lines, ars)
        assert el.refresh(s, lg, parts="auto", log=TC.Log())["part_b"] is None          # once: nothing left to re-group
    finally:
        lg.close()
        s.close()
