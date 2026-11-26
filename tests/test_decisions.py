"""
The owner's decisions of 2026-11-26 (plans/HANDOFF.md section 3b), as the code holds them. Invented data only: made-up
claim ids, payers, members and amounts. Each case runs on both kinds of store where the code runs on both: the server's
file store and the compact in-memory store of the serverless host.

Decision 3, the recovery when a payer denies as primary and later pays as secondary: the recovery is the amount that
actually arrived, in both places that record it (archive rule 3 and the settled-as-secondary closure), and the Results
tab attributes it like any recovery. What the payer still owes for the service in that role (its allowed amount less the
other payers' payments and the patient's share) stays listed, with its proof, whatever its size.

Decision 2, rule 8's patient-group denials that are not the patient's deductible, coinsurance or copay (PR-288, PR-27,
PR-26, PR-200, PR-49, PR-204): the item stays on Work now with the next step "Bill the patient $X"; a person records
"Billed the patient" (it waits for the patient's payment in EMA) or "Done", and no run overwrites either.
"""
from __future__ import annotations

import json

import pytest

from remitwatch import archive_rules as ar
from remitwatch.store import Store
from tests.test_archive_rules import ACME, BETA, cl, item, ln, only, remit


@pytest.fixture(params=["file", "compact"])
def store(request, tmp_path):
    s = Store(str(tmp_path / "s.sqlite")) if request.param == "file" else Store(":memory:", compact=True)
    yield s
    s.close()


# =============================================================================================================
# Decision 3: archive rule 3, a payment in the other role
# =============================================================================================================
def _denied_as_primary(store, pcn: str, paid_on: str = "2026-03-20") -> str:
    """ACME's answer as the primary payer: nothing paid, another payer covers it first (CO-22)."""
    return remit(store, paid_on, [cl(pcn, [ln("99213", 150.00, 0, None, [("CO", "22", 150.00)], lcn="1")])])[0]


def test_d3_a_payment_as_secondary_after_a_denial_as_primary_is_recovered_at_what_arrived(store):
    """ACME denied the visit as primary (CO-22), then processed it as secondary and paid $20.00 of its $100.00 allowed
    amount, the prior payer's answer taking $80.00 (OA-23): it owed $20.00 in that role, all of it arrived. The item
    closes, and the $20.00 is the recovery (it used to count $0)."""
    lk = _denied_as_primary(store, "CB0000901C017")
    remit(store, "2026-04-20", [cl("CB0000901C017", [ln("99213", 150.00, 20.00, 100.00, [("OA", "23", 80.00), ("CO", "45", 50.00)], lcn="1")],
                                   status="2")])
    a = only(ar.units_split(store.conn, [item(store, lk, "DENIED_MISSING_PRIMARY", expected=9000)]))
    assert (a["action"], a["paid_cents"], a["counts_as_recovery"], a["paid_date"]) == ("close_with_proof", 2000, True, "2026-04-20")
    assert "as secondary" in a["note"]


def test_d3_what_the_payer_still_owes_as_secondary_stays_listed_with_its_proof(store):
    """The same, but ACME paid $12.00 and took $8.00 off for a reason of its own (CO-16): of the $20.00 it owed in that
    role, $8.00 is left. The item stays listed at $8.00 (no dollar floor), the answer that paid as its proof."""
    lk = _denied_as_primary(store, "CB0000902C017")
    remit(store, "2026-04-20", [cl("CB0000902C017", [ln("99213", 150.00, 12.00, 100.00,
                                                        [("OA", "23", 80.00), ("CO", "45", 50.00), ("CO", "16", 8.00)], lcn="1")],
                                   status="2")])
    a = only(ar.units_split(store.conn, [item(store, lk, "DENIED_MISSING_PRIMARY", expected=9000)]))
    assert (a["action"], a["reclass"], a["shortfall_cents"], a["state_hint"], a["strength"]) == (
        "reclassify", "second_role_short", 800, "work_now", "proven")
    due = a["second_role"]
    assert (due["owed_cents"], due["paid_cents"], due["left_cents"], due["prior_cents"], due["prior_from"]) == (2000, 1200, 800, 8000, "stated")
    assert "$12.00" in a["hint"] and "$8.00" in a["hint"] and "$20.00" in a["hint"]
    assert any(r["decisive"] and r["amount_cents"] == 1200 for r in a["proof"])


def test_d3_the_primarys_remittance_on_file_is_what_the_other_payer_paid(store):
    """BETA paid $70.00 as primary; ACME, which had denied as primary, paid $25.00 as secondary of its $100.00 allowed:
    it owed $100.00 less BETA's $70.00, so $5.00 is left (BETA's remittance on file decides, not ACME's OA-23)."""
    lk = _denied_as_primary(store, "CB0000903C017")
    remit(store, "2026-04-01", [cl("CB0000903C017", [ln("99213", 150.00, 70.00, 100.00, [("CO", "45", 50.00), ("PR", "2", 30.00)], lcn="1")])],
          **BETA)
    remit(store, "2026-04-20", [cl("CB0000903C017", [ln("99213", 150.00, 25.00, 100.00, [("OA", "23", 75.00), ("CO", "45", 50.00)], lcn="1")],
                                   status="2")])
    a = only(ar.units_split(store.conn, [item(store, lk, "DENIED_MISSING_PRIMARY", expected=9000)]))
    assert (a["action"], a["shortfall_cents"]) == ("reclassify", 500)
    assert (a["second_role"]["prior_cents"], a["second_role"]["prior_from"]) == (7000, "on_file")


def test_d3_nothing_tells_what_the_primary_paid_so_no_remainder_is_guessed(store):
    """ACME paid $19.27 as secondary with no allowed amount, no OA-23 and no primary remittance on file: what it owed in
    that role cannot be told. The item closes on what arrived (a recovery); no remainder is sized from a guess."""
    lk = _denied_as_primary(store, "CB0000904C017")
    remit(store, "2026-04-20", [cl("CB0000904C017", [ln("99213", 150.00, 19.27, None, [("CO", "45", 130.73)], lcn="1")], status="2")])
    a = only(ar.units_split(store.conn, [item(store, lk, "DENIED_MISSING_PRIMARY", expected=9000)]))
    assert (a["action"], a["paid_cents"], a["counts_as_recovery"]) == ("close_with_proof", 1927, True)


def test_d3_a_payment_as_secondary_the_same_day_as_the_denial_came_with_the_answer(store):
    """The denial as primary and the payment as secondary on one day (two remittances): the money came with the answer,
    not after it. No recovery, as before."""
    lk = _denied_as_primary(store, "CB0000905C017")
    remit(store, "2026-03-20", [cl("CB0000905C017", [ln("99213", 150.00, 20.00, 100.00, [("OA", "23", 80.00), ("CO", "45", 50.00)], lcn="1")],
                                   status="2")])
    a = only(ar.units_split(store.conn, [item(store, lk, "DENIED_MISSING_PRIMARY", expected=9000)]))
    assert (a["action"], a["counts_as_recovery"]) == ("close_with_proof", False)


def _readjudicated(store, pcn: str, again: float):
    """ACME denied the visit as primary as a duplicate (OA-18) on March 20. Its remittance of April 10 takes back $9.43 it
    had paid for the service before, a payment not on file (a reversal, claim status 22), and pays it again as secondary,
    `again` dollars: it processed again a claim it had already paid."""
    lk = remit(store, "2026-03-20", [cl(pcn, [ln("99213", 150.00, 0, None, [("OA", "18", 150.00)], lcn="1")])])[0]
    rest = round(150.00 - 60.00 - again, 2)
    remit(store, "2026-04-10", [cl(pcn, [ln("99213", -150.00, -9.43, None, [("OA", "23", -60.00), ("CO", "45", -80.57)], lcn="1")], status="22"),
                                cl(pcn, [ln("99213", 150.00, again, round(150.00 - rest, 2), [("OA", "23", 60.00), ("CO", "45", rest)], lcn="1")],
                                   status="2")])
    return lk


def test_d3_a_payment_that_came_with_a_takeback_of_an_earlier_one_recovers_only_what_arrived(store):
    """ACME's payment as secondary ($9.26) came in the same remittance as its takeback of the $9.43 it had paid before (a
    payment not on file): nothing new arrived after the denial (the two net to a $0.17 takeback). The item closes, paid on
    its line, with no recovery, and the note says what was taken back. Paid again at $20.00 instead, $10.57 arrived: that
    is the recovery, never the whole $20.00."""
    lk = _readjudicated(store, "CB0000911C017", 9.26)
    a = only(ar.units_split(store.conn, [item(store, lk, "DENIED_DUPLICATE_UNPAID", expected=9000)]))
    assert (a["action"], a["paid_cents"], a["counts_as_recovery"]) == ("close_with_proof", 926, False), a
    assert "took back $9.43" in a["note"] and "nothing new arrived" in a["note"], a["note"]
    lk = _readjudicated(store, "CB0000912C017", 20.00)
    a = only(ar.units_split(store.conn, [item(store, lk, "DENIED_DUPLICATE_UNPAID", expected=9000)]))
    assert (a["action"], a["paid_cents"], a["counts_as_recovery"], a["arrived_cents"]) == ("close_with_proof", 2000, True, 1057), a
    assert "took back $9.43" in a["note"] and "$10.57 arrived" in a["note"], a["note"]


@pytest.mark.parametrize("kind", ["file", "compact"])
def test_d3_the_detection_run_records_only_what_arrived_after_a_takeback(tmp_path, kind):
    """The same through a detection run, on both kinds of store: Anthem denies a visit as a duplicate (OA-18), then one
    remittance takes back $9.43 it had paid before (not on file) and pays $20.00 as secondary. The item closes with
    $10.57 recovered, what arrived; a twin whose new payment ($9.26) is less than the takeback recovers nothing."""
    from remitwatch.detect import Detector
    from tests.test_settled_elsewhere import _add, _clp, _line, _store, _usual
    s = _store(tmp_path, kind)
    dup = "CAS*OA*18*223.65~"
    first = [_clp(f"CB0000R0{i}C017", "1", _line("0.00", dup)) for i in (1, 2)]
    _add(s, tmp_path, _usual("12", "100.00") + first, "20261001")
    Detector(s, run_id="t1").run()
    second = []
    for i, again in ((1, "20.00"), (2, "9.26")):
        rest = f"{223.65 - 60.00 - float(again):.2f}"
        second += [_clp(f"CB0000R0{i}C017", "22", _line("9.43", "CAS*OA*23*-60.00~CAS*CO*45*-154.22~", sign="-"), paid="9.43"),
                   _clp(f"CB0000R0{i}C017", "2", _line(again, f"CAS*OA*23*60.00~CAS*CO*45*{rest}~", f"{60.00 + float(again):.2f}"), paid=again)]
    _add(s, tmp_path, second, "20261110")
    Detector(s, run_id="t2", today="2026-11-20").run()
    rows = {r["patient_control_number"]: dict(r) for r in s.query(
        "SELECT * FROM findings WHERE procedure_code='99214' AND category='denial' AND COALESCE(stale,0)=0")}
    got = {k[:9]: (v["status"], v["recovered_cents"]) for k, v in rows.items()}
    assert got == {"CB0000R01": ("resolved", 1057), "CB0000R02": ("resolved", 0)}, (got, {k: v["auto_note"] for k, v in rows.items()})
    s.close()


def test_d3_owed_in_second_role_sizes_from_the_answer(store):
    """The rule itself: the allowed amount (else the item's expected), never above the charge, less the other payers'
    payments on file (else the payer's own OA-23), less the patient's share; None when neither tells what they paid."""
    f = ar.owed_in_second_role
    line = {"allowed": 10000, "charge": 15000, "paid": 2000, "adj": [("OA", "23", 8000), ("CO", "45", 5000)]}
    assert f([line], 0, 9000)["left_cents"] == 0
    assert f([line], 7000, 9000)["owed_cents"] == 3000 and f([line], 7000, 9000)["left_cents"] == 1000
    assert f([dict(line, allowed=None)], 0, 9000)["owed_cents"] == 1000          # the item's expected, less OA-23
    assert f([dict(line, allowed=20000)], 0, 9000)["owed_cents"] == 7000         # never above the charge
    assert f([dict(line, adj=[("CO", "45", 5000), ("PR", "1", 1000)])], 0, 9000) is None
    pr = f([dict(line, adj=[("OA", "23", 8000), ("PR", "2", 500)])], 0, 9000)
    assert (pr["patient_cents"], pr["owed_cents"], pr["left_cents"]) == (500, 1500, 0)


# =============================================================================================================
# Decision 3: the settled-as-secondary closure (detect.py), the item's state and next step, both kinds of store
# =============================================================================================================
@pytest.mark.parametrize("kind", ["file", "compact"])
def test_d3_a_secondary_answer_short_of_what_it_owes_keeps_the_item_listed_at_the_rest(tmp_path, kind):
    """Kentucky Medicaid pays 17110 on a claim and denies its 99214-25 line because another payer covers it first (CO-22
    with N36), then processes that line as secondary: its allowed $70.00, the primary's answer applied (OA-23 $50.00),
    and $10.00 more taken off for missing information (CO-16): it paid $10.00 of the $20.00 it owes in that role. The
    item stays listed at $10.00 with that answer as its proof (the closure used to close it, whatever the amount), its
    state work now, its next step asking Medicaid for the rest. A twin line paid in full as secondary closes, the
    payment recovered as before."""
    from remitwatch import story
    from remitwatch.detect import Detector
    from tests.test_settled_elsewhere import COB, DOS, MEDICAID, _add, _clp, _line, _store, _usual
    s = _store(tmp_path, kind)
    first = [_clp(f"CB0000Q0{i}C017", "1", _line("91.31", "CAS*CO*45*86.25~", "91.31", code="17110", charge="177.56")
                  + _line("0.00", COB, mods="25"), paid="91.31", cfi="MC") for i in (1, 2)]
    _add(s, tmp_path, _usual("MC", "70.00") + first, "20261001", **MEDICAID)
    Detector(s, run_id="t1").run()
    second = [_clp("CB0000Q01C017", "2", _line("10.00", "CAS*OA*23*50.00~CAS*CO*45*153.65~CAS*CO*16*10.00~", "70.00", mods="25"),
                   paid="10.00", cfi="MC"),
              _clp("CB0000Q02C017", "2", _line("20.00", "CAS*OA*23*50.00~CAS*CO*45*153.65~", "70.00", mods="25"), paid="20.00", cfi="MC")]
    _add(s, tmp_path, second, "20261110", **MEDICAID)
    Detector(s, run_id="t2", today="2026-11-20").run()
    rows = {r["patient_control_number"]: dict(r) for r in s.query(
        "SELECT * FROM findings WHERE procedure_code='99214' AND category IN ('denial','bundling') AND COALESCE(stale,0)=0")}
    short, full = rows["CB0000Q01C017"], rows["CB0000Q02C017"]
    assert (full["status"], full["recovered_cents"]) == ("resolved", 2000), (full["status"], full["recovered_cents"], full["auto_note"])
    assert (short["status"], short["shortfall_cents"], short["work_state"]) == ("open", 1000, "work_now"), (
        short["status"], short["shortfall_cents"], short["work_state"], short["state_basis"])
    due = json.loads(short["evidence"])["second_role"]
    assert (due["owed_cents"], due["paid_cents"], due["left_cents"], due["date"]) == (2000, 1000, 1000, "2026-11-10")
    assert "$10.00 is left" in short["state_basis"], short["state_basis"]
    n = story.next_step(s.conn, short["finding_id"], today="2026-11-20")
    assert n["route"] == "call" and n["action"].startswith("Ask Medicaid KY for the rest"), n["action"]
    assert n["deadline"] and n["deadline"] > "2026-11-20"
    s.close()


# =============================================================================================================
# Decision 3: the Results tab attributes what arrived as secondary like any recovery
# =============================================================================================================
@pytest.fixture(params=["file", "compact"])
def rstore(request, tmp_path):
    from remitwatch import actions
    from tests.test_results import compact_store
    from tests.test_states import new_store
    s = new_store(str(tmp_path / "store.sqlite")) if request.param == "file" else compact_store()
    actions.ensure_fax_tables(s.conn)
    yield s
    s.close()


def test_d3_results_count_what_the_payer_paid_as_secondary_after_the_flag(rstore, monkeypatch):
    """Blue Cross denied the visit as primary (CO-22) and RemitWatch flagged it on April 1; on April 20 Blue Cross processed
    it as secondary and paid $20.00. The Results tab counts the $20.00 as money returned on a flagged item (no work
    recorded: bucket c), and after a fax sent through RemitWatch it is money after RemitWatch work (bucket a)."""
    from remitwatch import ema_reports as er, results, states
    from tests import ema_fixtures as F
    from tests.test_results import dash, fax, money_of, run
    from tests.test_states import item, ln, remit
    monkeypatch.setattr(er, "CLAIM_HOST_SUFFIX", F.HOST_SUFFIX)
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    states._RESPONSE_CACHE.clear()
    states._RECOVERY_CACHE.clear()
    tk = remit(rstore, trace=F.check_no(100), paid_on="2026-03-28",
               claims=[{"pcn": F.claim_id(1), "received": "2026-03-05", "lines": [ln("99213", 150, 0, [("CO", "22", 150)])]}])
    item(rstore, finding_id="F1", signal="DENIED_MISSING_PRIMARY", carc="22", line_key=f"{tk}-0-0", claim_key=f"{tk}-0", transaction_key=tk,
         first_seen="2026-04-01T08:00:00+00:00", work_state="work_now", appeal_deadline="2026-12-31")
    remit(rstore, trace=F.check_no(102), paid_on="2026-04-20",
          claims=[{"pcn": F.claim_id(1, sub=2), "status": "2", "received": "2026-04-02",
                   "lines": [ln("99213", 150, 20, [("OA", "23", 80), ("CO", "45", 50)], allowed=100)]}])
    run(rstore)
    m = dash(rstore)["money"]
    assert (m["returned_cents"], m["c_cents"]) == (2000, 2000), m
    ev = money_of(rstore, "F1")["events"][0]
    assert ev["cents"] == 2000 and ev["bucket"] == "c"
    fax(rstore, "F1", "2026-04-05")
    run(rstore)
    assert dash(rstore)["money"]["a_cents"] == 2000


# =============================================================================================================
# Decision 2: rule 8's patient-group denials are billed to the patient
# =============================================================================================================
TODAY2 = "2026-11-20"


@pytest.fixture(params=["file", "compact"])
def sstore(request, tmp_path, monkeypatch):
    """A store as test_states builds it (EMA's tables, the key salt), on either kind."""
    from remitwatch import ema_reports as er, states
    from tests import ema_fixtures as F
    from tests.test_results import compact_store
    from tests.test_states import new_store
    monkeypatch.setattr(er, "CLAIM_HOST_SUFFIX", F.HOST_SUFFIX)
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    states._RESPONSE_CACHE.clear()
    states._RECOVERY_CACHE.clear()
    s = new_store(str(tmp_path / "store.sqlite")) if request.param == "file" else compact_store()
    yield s
    s.close()


def _production_covers(s, lo="2026-01-01", hi="2026-11-20"):
    """EMA's Production report on file covers services of these dates (a box as ema_reports.coverage reads it), so
    RemitWatch can see the patient's side of a service of those dates: decision 2 bills the patient only then."""
    s.conn.execute("""INSERT OR REPLACE INTO ema_report_files (file_id, report, status, scope, svc_lo, svc_hi, posted_lo, posted_hi, as_of)
                      VALUES (?, 'production', 'current', 'service', ?, ?, ?, ?, ?)""", (f"P{lo}", lo, hi, lo, hi, hi))
    s.conn.commit()


def _patient_denial(s, reason="27", *, charge=150, signal="DENIED_COVERAGE_DATES", other_paid=0, group="PR", fid="F1",
                    deadline="2026-09-24", payer_kind="commercial", covered=True):
    """A patient-group denial as archive rule 8 raises it: the payer put the whole charge on the patient (PR-<reason>)
    on March 28; another payer paid `other_paid` dollars for the same service the week before, when given. EMA's
    Production report on file covers the visit's date unless `covered` is False."""
    from tests import ema_fixtures as F
    from tests.test_states import item, ln, remit
    if covered:
        _production_covers(s)
    if other_paid:
        remit(s, trace=F.check_no(900), paid_on="2026-03-20", payer="TESTCARE OTHER PLAN",
              claims=[{"pcn": F.claim_id(1, sub=9), "received": "2026-03-05",
                       "lines": [ln("99213", charge, other_paid, [("CO", "45", charge - other_paid)])]}])
    tk = remit(s, trace=F.check_no(1), paid_on="2026-03-28",
               claims=[{"pcn": F.claim_id(1), "received": "2026-03-05", "lines": [ln("99213", charge, 0, [(group, reason, charge)])]}])
    item(s, finding_id=fid, signal=signal, carc=reason, line_key=f"{tk}-0-0", claim_key=f"{tk}-0", transaction_key=tk,
         expected_cents=charge * 100, shortfall_cents=charge * 100, appeal_deadline=deadline, payer_kind=payer_kind,
         evidence={"carc_group": group, "archive_rule": {"rule": "pr_group_denials", "action": "raise_item", "strength": "proven"}})
    return tk


def _ema_patient(s, *, balance, pat_paid=0, noncontractual=0, charges=15000, pat_adj=0):
    """EMA's line for the visit (written straight into its summary tables): what is open, what the patient paid, what EMA
    took off the patient's balance (a patient adjustment)."""
    from tests import ema_fixtures as F
    bill = F.claim_id(1)[:9]
    s.conn.execute("""INSERT OR REPLACE INTO ema_lines (link, svc, cpt, bill, payer_group, charges, balance, pat_paid_joined, noncontractual,
                                                        pat_adj_joined)
                      VALUES (?,?,?,?,?,?,?,?,?,?)""", (7001, "2026-03-02", "99213", bill, "Anthem / BCBS", charges, balance, pat_paid,
                                                        noncontractual, pat_adj))
    s.conn.execute("INSERT OR REPLACE INTO ema_link_map (link, bill, claim_id, method, conflict, facility) VALUES (?,?,?,?,0,0)",
                   (7001, bill, F.claim_id(1), "test"))
    s.conn.commit()


def _state(s, today=TODAY2, fid="F1"):
    from tests.test_states import state
    return state(s, fid, today=today)


def test_d2_a_coverage_date_denial_is_billed_to_the_patient_past_the_payers_windows(sstore):
    """PR-27 (the coverage had ended) put the visit's $150.00 on the patient. Past every payer window (Sep 24) it is still
    Work now: billing the patient is the work, and no payer's window closes it. The next step bills the patient $150.00
    and asks for the patient's current insurance; its buttons are Billed the patient and Done (and Mark sent, for the claim
    sent to a plan that covered the day)."""
    from remitwatch import story
    _patient_denial(sstore, "27")
    st = _state(sstore)
    assert (st["work_state"], st["rule"], st["route"]) == ("work_now", "bill_patient_denial", "bill_patient"), st["state_basis"]
    n = story.next_step(sstore.conn, "F1", today=TODAY2)
    assert n["action"] == "Bill the patient $150.00: Anthem / BCBS put this service on the patient (PR-27 $150.00: the coverage had ended).", n["action"]
    assert any("current insurance" in x for x in n["steps"]) and n["expected_cents"] == 15000 and n["appeal_ok"] is False
    assert {"billed_patient", "mark_done", "mark_sent", "let_go"} <= set(n["buttons"]) and "build_packet" not in n["buttons"]


def test_d2_the_amount_is_what_the_payer_put_on_the_patient_never_more_than_the_charge_less_what_any_payer_paid(sstore):
    """The payer priced the excluded service at $100.00 (CO-45 $50.00) and put that on the patient (PR-204): the patient is
    billed $100.00, not the charge. And what any payer paid for the service comes off: $150.00 on the patient with $40.00
    paid by another plan is $110.00."""
    from remitwatch import states, story
    from tests import ema_fixtures as F
    from tests.test_states import item, ln, remit
    _production_covers(sstore)
    tk = remit(sstore, trace=F.check_no(1), paid_on="2026-03-28",
               claims=[{"pcn": F.claim_id(1), "received": "2026-03-05", "lines": [ln("99213", 150, 0, [("PR", "204", 100), ("CO", "45", 50)])]}])
    item(sstore, finding_id="F1", signal="DENIED_PLAN_EXCLUSION", carc="204", line_key=f"{tk}-0-0", claim_key=f"{tk}-0", transaction_key=tk,
         expected_cents=10000, shortfall_cents=10000, appeal_deadline="2026-09-24", evidence={"carc_group": "PR"})
    n = story.next_step(sstore.conn, "F1", today=TODAY2)
    assert n["action"].startswith("Bill the patient $100.00:") and n["expected_cents"] == 10000, n["action"]
    own = {"line_key": "L1", "status": "1", "charge": 15000, "paid": 0, "adj": [("PR", "204", 15000)]}
    other = {"line_key": "L2", "status": "1", "charge": 15000, "paid": 4000, "adj": [("CO", "45", 11000)]}
    fx = {"item": {"line_key": "L1", "carc": "204"}, "r835": {"rows": [own, other]}}
    assert states.patient_share(fx)["cents"] == 11000


def test_d2_a_missing_referral_offers_a_retroactive_one_and_medicaid_follows_the_same_rule(sstore):
    from remitwatch import story
    _patient_denial(sstore, "288", signal="DENIED_REFERRAL", deadline="2027-01-31", payer_kind="medicaid")
    n = story.next_step(sstore.conn, "F1", today=TODAY2)
    assert n["action"].startswith("Bill the patient $150.00:") and "no referral on file" in n["action"], n["action"]
    assert any("retroactive referral" in x and "Jan 31, 2027" in x for x in n["steps"]), n["steps"]


def test_d2_co147_is_a_contractual_reason_never_billed_to_the_patient(sstore):
    from remitwatch import story
    _patient_denial(sstore, "147", signal="DENIED_CONTRACT_RATE", group="CO", deadline="2027-01-31")
    n = story.next_step(sstore.conn, "F1", today=TODAY2)
    assert n["route"] != "bill_patient" and "billed_patient" not in n["buttons"] and "Bill the patient" not in n["action"]


def test_d2_billed_the_patient_waits_for_the_patients_payment_and_closes_when_ema_shows_it(sstore):
    """A person presses Billed the patient on November 20: the item leaves Work now and waits for the patient's payment,
    checked again 60 days later; EMA showing the patient's $150.00 closes it as paid by the patient; with nothing paid by
    the follow-up day it is work again (another statement, or Done). The person's status stays Sent throughout."""
    from remitwatch import actions, story
    _patient_denial(sstore, "204", signal="DENIED_PLAN_EXCLUSION")
    _ema_patient(sstore, balance=15000)
    actions.apply_status(sstore.conn, "F1", "appealed", "Billed the patient: statement of Nov 20", actor="Biller One",
                         at="2026-11-20T15:00:00+00:00")
    st = _state(sstore)
    assert (st["work_state"], st["rule"], st["follow_up_on"]) == ("waiting", "billed_patient", "2027-01-19"), st["state_basis"]
    assert st["state_basis"].startswith("Patient billed by Biller One on Nov 20, 2026: statement of Nov 20")
    n = story.next_step(sstore.conn, "F1", today=TODAY2)
    assert n["route"] == "wait" and n["action"].startswith("Wait for the patient's payment") and "mark_done" in n["buttons"]
    late = _state(sstore, today="2027-01-20")
    assert (late["work_state"], late["rule"]) == ("work_now", "patient_unpaid"), late["state_basis"]
    assert story.next_step(sstore.conn, "F1", today="2027-01-20")["action"].startswith("Send the patient another statement for $150.00")
    _ema_patient(sstore, balance=0, pat_paid=15000)
    done = _state(sstore, today="2027-01-20")
    assert (done["work_state"], done["rule"]) == ("done", "patient_paid") and done["state_basis"].startswith("Paid by the patient: EMA shows $150.00")
    assert sstore.conn.execute("SELECT status FROM findings WHERE finding_id='F1'").fetchone()[0] == "appealed"


def test_d2_a_patient_share_ema_settled_on_the_patients_side_is_done_not_billed_again(sstore):
    """PR-204 put the visit's $150.00 on the patient. EMA shows the patient paid $40.00 and $110.00 taken off the
    patient's balance, and the line closed: the patient was billed and the practice settled the rest on the patient's
    account, so the item is done (settled by the patient), never "Bill the patient $150.00" again. The same $40.00 with the
    rest written off as non-contractual is not the patient's side: the share was written off, so it is still work, for the
    $110.00 the patient has not paid, after reversing the write-off."""
    from remitwatch import story
    _patient_denial(sstore, "204", signal="DENIED_PLAN_EXCLUSION")
    _ema_patient(sstore, balance=0, pat_paid=4000, pat_adj=11000)
    st = _state(sstore)
    assert (st["work_state"], st["rule"]) == ("done", "moved_to_patient"), st["state_basis"]
    assert "$40.00 paid by the patient" in st["state_basis"] and "$110.00 taken off the patient's balance" in st["state_basis"], st["state_basis"]
    _ema_patient(sstore, balance=0, pat_paid=4000, noncontractual=11000)
    st = _state(sstore)
    assert (st["work_state"], st["rule"]) == ("work_now", "bill_patient_denial"), st["state_basis"]
    n = story.next_step(sstore.conn, "F1", today=TODAY2)
    assert n["action"].startswith("Bill the patient $110.00 (the rest: EMA shows $40.00 of $150.00 paid by the patient):"), n["action"]
    assert n["expected_cents"] == 11000 and any(x.startswith("EMA wrote $110.00 off on this line: reverse that write-off") for x in n["steps"]), n


def test_d2_a_patient_who_paid_part_is_billed_the_rest_and_one_who_paid_it_all_is_done(sstore):
    """The patient paid $90.00 of the $150.00 and EMA shows $60.00 open: the next step bills the $60.00 left, not the
    $150.00. Once EMA shows the patient paid all $150.00 the item is done, paid by the patient, even while EMA still shows
    money open on the line for another reason."""
    from remitwatch import story
    _patient_denial(sstore, "27")
    _ema_patient(sstore, balance=6000, pat_paid=9000)
    st = _state(sstore)
    assert (st["work_state"], st["rule"]) == ("work_now", "bill_patient_denial"), st["state_basis"]
    assert st["state_basis"].endswith("bill the patient the $60.00 left (EMA shows $90.00 of $150.00 paid by the patient)"), st["state_basis"]
    n = story.next_step(sstore.conn, "F1", today=TODAY2)
    assert n["action"].startswith("Bill the patient $60.00 (the rest: EMA shows $90.00 of $150.00 paid by the patient):"), n["action"]
    assert n["expected_cents"] == 6000 and "EMA shows $60.00 open on this line: put $60.00 on the patient's next statement." in n["steps"]
    _ema_patient(sstore, balance=2000, pat_paid=15000, charges=17000)
    st = _state(sstore)
    assert (st["work_state"], st["rule"]) == ("done", "patient_paid"), st["state_basis"]
    assert st["state_basis"] == "Paid by the patient: EMA shows $150.00 paid by the patient", st["state_basis"]


def test_d2_billed_the_patient_is_not_closed_by_a_write_off_of_the_patients_share(sstore):
    """After Billed the patient, EMA closes the line by writing $130.00 off as non-contractual with only the $20.00 the
    patient paid: that is a write-off, not the patient's payment, so the item keeps waiting (and is work again at the
    follow-up day) for a person to decide; it is never closed as paid by the patient."""
    from remitwatch import actions
    _patient_denial(sstore, "204", signal="DENIED_PLAN_EXCLUSION")
    actions.apply_status(sstore.conn, "F1", "appealed", "Billed the patient", actor="Biller One", at="2026-11-20T15:00:00+00:00")
    _ema_patient(sstore, balance=0, pat_paid=2000, noncontractual=13000)
    st = _state(sstore)
    assert (st["work_state"], st["rule"]) == ("waiting", "billed_patient"), st["state_basis"]
    assert st["state_basis"].endswith("EMA shows $20.00 of $150.00 paid so far"), st["state_basis"]
    late = _state(sstore, today="2027-01-20")
    assert (late["work_state"], late["rule"]) == ("work_now", "patient_unpaid"), late["state_basis"]
    _ema_patient(sstore, balance=0, pat_paid=2000, pat_adj=13000)
    assert (_state(sstore)["work_state"], _state(sstore)["rule"]) == ("done", "patient_paid")


def _crossed_over(s, later_status, payer, cfi, adj):
    """Medicare put a routine visit on the patient (PR-49 $150.00) and sent the claim on to the next payer (status 19), which
    answered it on April 6 with nothing paid: `adj` its adjustment on the whole $150.00."""
    from tests import ema_fixtures as F
    from tests.test_states import item, ln, remit
    _production_covers(s)
    tk = remit(s, trace=F.check_no(1), paid_on="2026-03-28", payer="MEDICARE TEST",
               claims=[{"pcn": F.claim_id(1), "received": "2026-03-05", "status": "19", "cfi": "MB",
                        "lines": [ln("99213", 150, 0, [("PR", "49", 150)])]}])
    item(s, finding_id="F1", signal="DENIED_PLAN_EXCLUSION", carc="49", line_key=f"{tk}-0-0", claim_key=f"{tk}-0", transaction_key=tk,
         expected_cents=15000, shortfall_cents=15000, appeal_deadline="2026-09-24", payer_kind="medicare", evidence={"carc_group": "PR"})
    remit(s, trace=F.check_no(2), paid_on="2026-04-06", payer=payer,
          claims=[{"pcn": F.claim_id(1), "status": later_status, "cfi": cfi, "lines": [ln("99213", 150, 0, [(adj[0], adj[1], 150)])]}])


def test_d2_a_later_payer_that_put_the_service_on_the_practice_is_named_before_billing(sstore):
    """Medicare put a routine visit on the patient (PR-49 $150.00) and sent it on; the patient's Medicaid plan answered
    after it and put it on the practice (CO-45, nothing paid). The next step still bills the patient $150.00 (the owner's
    decision), but says first that the later payer put it on the practice, so a person checks that payer's terms before
    billing (a Medicaid member is not billed for what Medicaid puts on the practice) and presses Done when they forbid it."""
    from remitwatch import story
    _crossed_over(sstore, "4", "KENTUCKY MEDICAID", "MC", ("CO", "45"))
    n = story.next_step(sstore.conn, "F1", today=TODAY2)
    assert n["action"].startswith("Bill the patient $150.00:"), n["action"]
    first = n["steps"][0]
    assert first.startswith("Before billing: Medicaid KY answered after it and put this service on the practice (CO-45), with nothing paid"), first
    assert "a Medicaid member is not billed for what Medicaid puts on the practice" in first and "press Done" in first, first


def test_d2_a_later_payer_that_put_the_service_on_the_patient_too_adds_no_caution(sstore):
    """The same visit, the next payer (a supplement) put it on the patient too (PR-96): no caution, the patient is billed."""
    from remitwatch import story
    _crossed_over(sstore, "2", "TESTCARE SUPPLEMENT", "CI", ("PR", "96"))
    n = story.next_step(sstore.conn, "F1", today=TODAY2)
    assert n["action"].startswith("Bill the patient $150.00:") and not any(x.startswith("Before billing") for x in n["steps"]), n


def test_d2_done_is_a_persons_decision_with_who_and_when(sstore):
    from remitwatch import actions
    _patient_denial(sstore, "49", signal="DENIED_PLAN_EXCLUSION")
    actions.apply_status(sstore.conn, "F1", "resolved", "Done: the patient paid at the front desk", 0, actor="Biller One",
                         at="2026-11-20T15:00:00+00:00")
    st = _state(sstore)
    assert (st["work_state"], st["rule"]) == ("done", "person_done")
    assert st["state_basis"] == "Marked done by Biller One on Nov 20, 2026: the patient paid at the front desk"


# --------------------------------------------------------------------------- the two actions through the API, both hosts
XHR = {"X-Requested-With": "remitwatch"}


@pytest.fixture
def api_site(tmp_path, monkeypatch):
    """The server's API over a store holding a patient-group denial (F1, PR-27) and an authorization denial (F2)."""
    from fastapi.testclient import TestClient

    import remitwatch.api as api
    from remitwatch import actions, ema_reports as er, states
    from tests import ema_fixtures as F
    from tests.test_states import item, new_store
    monkeypatch.setattr(er, "CLAIM_HOST_SUFFIX", F.HOST_SUFFIX)
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    states._RESPONSE_CACHE.clear()
    db = str(tmp_path / "store.sqlite")
    s = new_store(db)
    actions.ensure_fax_tables(s.conn)
    _patient_denial(s, "27")
    item(s, finding_id="F2", patient_control_number=F.claim_id(2))
    s.close()
    monkeypatch.setattr(api, "DB_PATH", db)
    monkeypatch.setattr(api, "PASSWORD", "")
    return type("Site", (), {"c": TestClient(api.app), "db": db})


def _post(c, fid, **body):
    return c.post(f"/api/findings/{fid}/status", json=body, headers=XHR)


def _row(db, fid):
    import sqlite3
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        f = dict(conn.execute("SELECT * FROM findings WHERE finding_id=?", (fid,)).fetchone())
        ev = [dict(r) for r in conn.execute("SELECT * FROM finding_events WHERE finding_id=? ORDER BY id", (fid,))]
    finally:
        conn.close()
    return f, ev


def test_d2_done_through_the_api_is_strict_bound_to_its_item_and_checked_against_the_page(api_site):
    c, db = api_site.c, api_site.db
    # an override is strict JSON true: a string or a number is refused, and nothing changes
    from remitwatch import api as rw_api
    for bad in ("true", 1, "yes", False):
        r = _post(c, "F1", status="resolved", done=bad, recovered_cents=0, seen_status="open")
        assert r.status_code == 400 and r.json().get("detail") == rw_api.DONE_STRICT, (bad, r.text)
    # Done is for a patient-group denial only; $0 Paid without it stays refused
    r = _post(c, "F2", status="resolved", done=True, seen_status="open")
    assert r.status_code == 400 and r.json().get("code") == "done", r.text
    assert _post(c, "F1", status="resolved", recovered_cents=0, seen_status="open").status_code == 400
    # a page drawn when the item had another status cannot mark it done
    assert _post(c, "F1", status="resolved", done=True, seen_status="appealed").status_code == 409
    assert _row(db, "F1")[0]["status"] == "open"
    r = _post(c, "F1", status="resolved", done=True, seen_status="open", notes="the patient paid at the front desk")
    assert r.status_code == 200, r.text
    f, ev = _row(db, "F1")
    assert (f["status"], f["recovered_cents"], f["notes"]) == ("resolved", 0, "Done: the patient paid at the front desk")
    assert [(e["kind"], e["to_status"], e["note"]) for e in ev][-1] == ("manual", "resolved", "Done: the patient paid at the front desk")


def test_d2_billed_the_patient_through_the_api_is_one_change(api_site):
    from remitwatch import states
    c, db = api_site.c, api_site.db
    r = _post(c, "F1", status="appealed", notes="Billed the patient: statement sent", seen_status="open")
    assert r.status_code == 200, r.text
    f, ev = _row(db, "F1")
    assert f["status"] == "appealed" and [e["note"] for e in ev if e["kind"] == "manual"] == ["Billed the patient: statement sent"]
    import sqlite3
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        st = states.state_of(conn, dict(conn.execute("SELECT * FROM findings WHERE finding_id='F1'").fetchone()))
    finally:
        conn.close()
    assert (st["work_state"], st["rule"]) == ("waiting", "billed_patient"), st["state_basis"]


@pytest.mark.parametrize("kind", ["file", "compact"])
def test_d2_a_run_never_undoes_billed_the_patient_or_done(tmp_path, kind):
    """Anthem put three visits on the patients (PR-204, the plan's exclusion). The run lists each to bill the patient at
    the $223.65 the payer put on them. A biller marks one Billed the patient and another Done; the next runs keep both (the
    first waits for the patient's payment, then is work again when nothing came; the second stays done with $0), and the
    third is still Work now."""
    from remitwatch import actions, ema_reports as er
    from remitwatch.detect import Detector
    from tests.test_settled_elsewhere import ANTHEM, _add, _clp, _line, _store, _usual
    s = _store(tmp_path, kind)
    er.ensure_schema(s.conn)
    _production_covers(s, "2026-01-01", "2026-10-15")       # EMA's Production report covers the visits' dates
    pcns = [f"CB0000R0{i}C017" for i in (1, 2, 3)]
    _add(s, tmp_path, _usual("12", "117.60") + [_clp(p, "1", _line("0.00", "CAS*PR*204*223.65~")) for p in pcns], "20261001", **ANTHEM)
    Detector(s, run_id="t1", today="2026-10-20").run()

    def items():
        return {r["patient_control_number"]: dict(r) for r in s.query(
            "SELECT * FROM findings WHERE signal='DENIED_PLAN_EXCLUSION' AND COALESCE(stale,0)=0")}
    first = items()
    assert set(first) == set(pcns), sorted(first)
    assert all((r["work_state"], r["shortfall_cents"], r["status"]) == ("work_now", 22365, "open") for r in first.values()), [
        (r["work_state"], r["shortfall_cents"], r["state_basis"]) for r in first.values()]
    actions.apply_status(s.conn, first[pcns[0]]["finding_id"], "appealed", "Billed the patient", actor="Biller One", at="2026-10-21T15:00:00+00:00")
    actions.apply_status(s.conn, first[pcns[1]]["finding_id"], "resolved", "Done: on a payment plan", 0, actor="Biller One",
                         at="2026-10-21T15:05:00+00:00")
    s.conn.commit()
    Detector(s, run_id="t2", today="2026-10-22").run()
    now = items()
    assert (now[pcns[0]]["status"], now[pcns[0]]["work_state"], now[pcns[0]]["follow_up_on"]) == ("appealed", "waiting", "2026-12-20")
    assert (now[pcns[1]]["status"], now[pcns[1]]["recovered_cents"], now[pcns[1]]["work_state"]) == ("resolved", 0, "done")
    assert now[pcns[1]]["state_basis"].startswith("Marked done by Biller One")
    assert (now[pcns[2]]["status"], now[pcns[2]]["work_state"]) == ("open", "work_now")
    Detector(s, run_id="t3", today="2026-12-21").run()
    later = items()
    assert (later[pcns[0]]["status"], later[pcns[0]]["work_state"]) == ("appealed", "work_now")
    assert later[pcns[0]]["state_basis"].startswith("Patient billed by Biller One") and "send another statement" in later[pcns[0]]["state_basis"]
    assert (later[pcns[1]]["status"], later[pcns[1]]["work_state"]) == ("resolved", "done")
    s.close()


from tests.test_cloud import box  # noqa: E402,F401  (a fixture: the serverless state folder)
from tests.test_hotfix2 import sl  # noqa: E402,F401  (a fixture: the serverless site)


def test_d2_done_on_the_serverless_host_is_checked_on_its_copy_and_journaled_as_a_status(sl):
    """On Vercel the check runs against the dashboard's copy and the change goes to the journal as a status: Paid, $0,
    the note Done, the status the page showed. An item that is no patient-group denial is refused there too."""
    from remitwatch import cloud
    c, h, api = sl.c, sl.h, sl.api
    fid, other = sl.ids[0], sl.ids[1]
    conn = api.db()
    conn.execute("UPDATE findings SET signal='DENIED_COVERAGE_DATES', carc='27', category='denial', evidence=? WHERE finding_id=?",
                 (json.dumps({"carc_group": "PR"}), fid))
    conn.commit()
    conn.close()
    n = len(list(sl.state.list(cloud.ACTIONS)))
    r = c.post(f"/api/findings/{other}/status", json={"status": "resolved", "done": True, "seen_status": "open"}, headers=h)
    assert r.status_code == 400 and len(list(sl.state.list(cloud.ACTIONS))) == n, r.text
    r = c.post(f"/api/findings/{fid}/status", json={"status": "resolved", "done": "true", "seen_status": "open"}, headers=h)
    assert r.status_code == 400
    r = c.post(f"/api/findings/{fid}/status", json={"status": "resolved", "done": True, "seen_status": "open", "notes": "patient paid"},
               headers=h)
    assert r.status_code == 200, r.text
    entries = [json.loads(sl.state.get(m["name"])) for m in sl.state.list(cloud.ACTIONS)]
    e = [x for x in entries if x.get("finding_id") == fid][-1]
    assert (e["kind"], e["status"], e["recovered_cents"], e["notes"], e["seen"]) == ("status", "resolved", 0, "Done: patient paid", "open")
