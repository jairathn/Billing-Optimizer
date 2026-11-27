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
    assert codes.normalize_payer(company)[0] == codes.normalize_payer(state)[0] == "Medicaid (other)"
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
    outcomes = ec.line_outcomes(s.conn, findings, code_once=lambda f: True, today=TODAY)
    got = ec.paid_in_ema(s.conn, findings, outcomes)
    assert outcomes["c6-81"]["outcome"] == "paid_after" and got["c6-81"]["action"] == "close", (outcomes["c6-81"], got.get("c6-81"))
    assert got["c6-81"]["paid_cents"] == 1550
    assert got["c6-82"]["action"] == "hint_partial", got.get("c6-82")
    # no check (a payment moved from another line), or another payer's payment: the copay does not make it paid
    assert outcomes["c6-83"]["outcome"] != "paid_after" and (got.get("c6-83") or {}).get("action") != "close", got.get("c6-83")
    assert outcomes["c6-84"]["outcome"] != "paid_after" and (got.get("c6-84") or {}).get("action") not in ("close", "hint_other_payer"), got.get("c6-84")
