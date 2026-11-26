"""
The decisions unit's review round of 2026-11-26: the reviewers' failing tests (rounds r1 and r2), ported into the
repository with the fixes they hold. Invented data only: made-up claim ids, payers, members, amounts and dates. Each case
runs on both kinds of store where the code runs on both (the server's file store and the compact in-memory store of the
serverless host); the API cases run on the server, and on the serverless host where the reviewer found the same.
"""
from __future__ import annotations

import json

import pytest

from tests.test_decisions import (TODAY2, _crossed_over, _ema_patient, _patient_denial, _production_covers, _state,  # noqa: F401
                                  sstore)


# =============================================================================================================
# r1-1: a patient-group denial RemitWatch cannot see the patient's side of is a can't-check, never billed blind
# =============================================================================================================
def _old_visit(s, *, svc="2024-03-02", paid_on="2024-03-28"):
    """A 2024 visit the payer put on the patient (PR-204, the whole $150.00)."""
    from tests import ema_fixtures as F
    from tests.test_states import item, ln, remit
    tk = remit(s, trace=F.check_no(1), paid_on=paid_on,
               claims=[{"pcn": F.claim_id(1), "received": "2024-03-05", "lines": [ln("99213", 150, 0, [("PR", "204", 150)], svc=svc)]}])
    item(s, signal="DENIED_PLAN_EXCLUSION", carc="204", line_key=f"{tk}-0-0", claim_key=f"{tk}-0", transaction_key=tk,
         service_date=svc, payment_date=paid_on, year=svc[:4], expected_cents=15000, shortfall_cents=15000,
         appeal_deadline="2024-09-24", evidence={"carc_group": "PR"})
    return tk


def test_r1_1_a_patient_denial_ema_cannot_see_is_cant_check_not_billed_blind(sstore):
    """EMA's Production report on file covers 2026 only, and the visit is of 2024 with no EMA line: RemitWatch cannot see
    whether the patient paid, was billed or had the share written off. The item is a can't-check with its $150.00 (the
    owner's rule 3), past every payer window or not (the payer's windows never close a patient's share; an age limit is
    the owner's call), and its next step checks the patient's account in EMA first: it never says "Bill the patient"."""
    from remitwatch import story
    from tests.test_states import state
    _production_covers(sstore, "2026-01-05", "2026-11-20")
    _old_visit(sstore)
    st = state(sstore, today="2026-11-24")
    assert (st["work_state"], st["rule"]) == ("cant_check", "cant_check_patient"), (st["work_state"], st["rule"], st["state_basis"])
    assert "EMA's Production reports do not cover this date of service" in st["state_basis"] and "$150.00" in st["state_basis"]
    n = story.next_step(sstore.conn, "F1", today="2026-11-24")
    assert not n["action"].startswith("Bill the patient"), n["action"]
    assert n["action"].startswith("Check the patient's account in EMA before billing $150.00"), n["action"]
    assert n["expected_cents"] == 15000 and "cannot see" in n["expected_basis"], (n["expected_cents"], n["expected_basis"])
    assert {"billed_patient", "mark_done"} <= set(n["buttons"]) and "build_packet" not in n["buttons"], n["buttons"]
    assert n["appeal_ok"] is False


def test_r1_1_with_no_production_report_on_file_it_says_so(sstore):
    """No EMA Production report on file at all (the serverless site reads the A/R only): the same can't-check, saying so."""
    from tests.test_states import state
    _old_visit(sstore, svc="2026-03-02", paid_on="2026-03-28")
    st = state(sstore, today=TODAY2)
    assert (st["work_state"], st["rule"]) == ("cant_check", "cant_check_patient"), st["state_basis"]
    assert "no EMA Production report is on file" in st["state_basis"], st["state_basis"]


def test_r1_1_an_ema_line_for_the_service_shows_the_patients_side_whatever_the_coverage(sstore):
    """EMA shows the visit's line (its patient's money is there to read) though no box covers the date: billed as decided,
    the rest after what EMA shows the patient paid."""
    from remitwatch import story
    from tests import ema_fixtures as F
    from tests.test_states import state
    _production_covers(sstore, "2026-01-05", "2026-11-20")
    _old_visit(sstore)
    bill = F.claim_id(1)[:9]                 # EMA's line for the 2024 visit, $150.00 open
    sstore.conn.execute("""INSERT INTO ema_lines (link, svc, cpt, bill, payer_group, charges, balance, pat_paid_joined, noncontractual,
                                                  pat_adj_joined) VALUES (7001, '2024-03-02', '99213', ?, 'Anthem / BCBS', 15000, 15000, 0, 0, 0)""",
                        (bill,))
    sstore.conn.execute("INSERT INTO ema_link_map (link, bill, claim_id, method, conflict, facility) VALUES (7001, ?, ?, 'test', 0, 0)",
                        (bill, F.claim_id(1)))
    sstore.conn.commit()
    st = state(sstore, today="2026-11-24")
    assert (st["work_state"], st["rule"]) == ("work_now", "bill_patient_denial"), st["state_basis"]
    assert story.next_step(sstore.conn, "F1", today="2026-11-24")["action"].startswith("Bill the patient $150.00:")


def test_r1_1_billed_the_patient_on_a_visit_ema_cannot_show_is_never_followed_by_a_bill_on_a_guess(sstore):
    """A person checked EMA by hand and pressed Billed the patient on the visit RemitWatch cannot see: it waits 60 days,
    then is work again, but never says "nothing paid by the patient" nor sends another statement on a guess: the
    patient's account in EMA is checked first."""
    from remitwatch import actions, story
    from tests.test_states import state
    _production_covers(sstore, "2026-01-05", "2026-11-20")
    _old_visit(sstore)
    actions.apply_status(sstore.conn, "F1", "appealed", "Billed the patient", actor="Biller One", at="2026-11-24T15:00:00+00:00")
    assert (state(sstore, today="2026-11-24")["rule"]) == "billed_patient"
    late = state(sstore, today="2027-01-24")
    assert (late["work_state"], late["rule"]) == ("work_now", "patient_unpaid"), late["state_basis"]
    assert "nothing paid by the patient" not in late["state_basis"] and "cannot be seen" in late["state_basis"], late["state_basis"]
    n = story.next_step(sstore.conn, "F1", today="2027-01-24")
    assert n["action"].startswith("Check the patient's account in EMA, then send the patient another statement for $150.00"), n["action"]


def test_r1_1_a_covered_visit_is_billed_as_decided(sstore):
    """The visit's date is covered by EMA's Production report on file: billed as decided (the unit's own case)."""
    _patient_denial(sstore, "204", signal="DENIED_PLAN_EXCLUSION")
    st = _state(sstore)
    assert (st["work_state"], st["rule"]) == ("work_now", "bill_patient_denial"), st["state_basis"]


# =============================================================================================================
# r1-2: an owner's decision made under the key with no period (before decision 4) still holds for each period
# =============================================================================================================
from tests.test_payer_code_rules import dt_days, ema_env, eledger, estore  # noqa: E402,F401  (fixtures)

S25 = "2025-10-02"
WHOLE = "UnitedHealthcare|G6001||payer_policy"
TO25 = "UnitedHealthcare|G6001|to 2025-12-31|payer_policy"


def _g6001_world(estore, eledger, tmp_path):
    """22 denials of G6001 as primary in 2025 (CO-197), and 6 payments of it as the second payer: a pair the research
    splits by period (to 2025-12-31). Invented members and claims."""
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import denial_world, remit
    for n in range(22):
        remit(estore, trace=f"7776{n:05d}", paid_on="2025-11-10", payer="UNITEDHEALTHCARE TEST",
              claims=[{"pcn": F.claim_id(800 + n), "last": f"RAD{n}", "first": "JULIET", "member": f"J{n:011d}",
                       "lines": [(S25, "G6001", 260, 0, [("CO", "197", 260)])]}])
    for n in range(6):
        remit(estore, trace=f"7777{n:05d}", paid_on="2025-11-10", payer="UNITEDHEALTHCARE TEST", status="2",
              claims=[{"pcn": F.claim_id(850 + n), "last": f"SEC{n}", "first": "KILO", "member": f"K{n:011d}",
                       "lines": [(S25, "G6001", 260, 30, [("PR", "2", 0)])]}])
    denial_world(estore, eledger, tmp_path)


def test_r1_2_an_owners_accept_under_the_key_with_no_period_still_holds(estore, eledger, tmp_path):
    """The owner accepted UnitedHealthcare G6001 on the policy page before decision 4 named periods (key
    'UnitedHealthcare|G6001||payer_policy'). After the release the run keys the 2025 services 'to 2025-12-31': the
    owner's accept still holds there (its 22 denials stay off Work now), and the policy page shows that row accepted,
    by whom and when, made for the code at every date."""
    from remitwatch import api_ema, ema_checks as ec
    from tests.test_ema_wiring import items, run
    _g6001_world(estore, eledger, tmp_path)
    ec.set_policy(estore.conn, WHOLE, "accept", "Owner One")
    estore.conn.commit()
    run(estore)
    left = items(estore, "payer_group='UnitedHealthcare' AND procedure_code='G6001' AND status='open'")
    assert left == [], f"{len(left)} G6001 denials back on the list although the owner accepted the pattern"
    rows = {p["pattern"]: p for p in api_ema.policy_table(estore.conn)["patterns"]}
    assert TO25 in rows, sorted(rows)
    row = rows[TO25]
    assert (row["decision"], row["decided_by"], row["decided_on"], row["pending"]) == ("accept", "Owner One", WHOLE, False), row


def test_r1_2_a_later_decision_on_the_period_row_wins_and_a_later_one_on_the_whole_row_wins_back():
    """The newer decision of the two rows holds: a period decided later is decided there; the row for every date changed
    later covers every period again. A key with no period never reads another row."""
    from remitwatch import rarely_paid as rp
    pol = {WHOLE: {"decision": "accept", "by": "Owner One", "at": "2026-10-01T10:00:00+00:00"}}
    assert rp.decision_for(pol, TO25)["decision"] == "accept"
    pol[TO25] = {"decision": "pursue", "by": "Owner One", "at": "2026-12-02T10:00:00+00:00"}
    assert (rp.decision_for(pol, TO25)["decision"], rp.decision_for(pol, TO25)["from_key"]) == ("pursue", TO25)
    pol[WHOLE] = {"decision": "accept", "by": "Owner One", "at": "2026-12-05T10:00:00+00:00"}
    assert (rp.decision_for(pol, TO25)["decision"], rp.decision_for(pol, TO25)["from_key"]) == ("accept", WHOLE)
    assert rp.decision_for(pol, "Aetna|G2211||payer_policy") is None
    assert rp.decision_for({}, TO25) is None and rp.decision_for(None, TO25) is None
    assert rp.whole_key("Anthem / BCBS|77600|from 2026-01-01|payer_policy") == "Anthem / BCBS|77600||payer_policy"
    assert rp.whole_key("Anthem / BCBS|99213|25|CO-45") is None


# =============================================================================================================
# r1-3: a denial only EMA saw, of a pair the research calls never payable, is led by the rule, not told to appeal
# =============================================================================================================
def test_r1_3_an_ema_only_denial_of_a_never_payable_pair_is_led_by_the_rule(estore, eledger, tmp_path):
    """Three 2026 Optum 77600 denials only EMA saw (CO-96, no remittance on file): their next step leads with "Not payable
    under Optum (UHC)'s published rule", the EOB check kept after it as the only conditional way on; no appeal is
    offered."""
    from remitwatch import story
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import D, P, adj, charge, denial_world, items, run
    optum, rows = P["optum"], []
    for n in range(3):
        kw = dict(patient=n % 4, payer=optum, primary=optum, svc=D(2026, 2, 2) + dt_days(n))
        rows += [charge(F.link_no(800 + n), cpt="77600", amount=300, **kw),
                 adj(F.link_no(800 + n), "CO-96", 300, cpt="77600", posted=D(2026, 3, 10) + dt_days(n), **kw)]
    denial_world(estore, eledger, tmp_path, rows)
    run(estore)
    got = items(estore, "signal='EMA_DENIED'")
    assert len(got) == 3
    x = story.explain(estore.conn, got[0]["finding_id"], today="2026-05-03")
    assert [b["verdict"] for b in x["why_unpaid"]] == ["never_payable"]
    nx = x["next_step"]
    assert (nx["route"], nx["payer_rule"]) == ("eob_first", "never_payable")
    assert nx["action"].startswith("Not payable under Optum (UHC)'s published rule"), nx["action"]
    assert any(s.startswith("Only if the check finds what the rule allows: Get the EOB") for s in nx["steps"]), nx["steps"]
    assert nx["appeal_ok"] is False and not {"build_packet", "fax_payer"} & set(nx["buttons"])
    assert got[0]["appeal_block"] in ("eob_first", "never_payable")      # a stored block: no bulk appeal takes it


# =============================================================================================================
# r2-1: a never-payable verdict closes every appeal path (bulk list, bulk send, the panel's main button) and the
# checklist goes with the packet to where the fax goes out
# =============================================================================================================
XHR = {"X-Requested-With": "remitwatch"}
ANTHEM26 = "payer_group='Anthem / BCBS' AND COALESCE(stale,0)=0 AND procedure_code='77600' AND service_date LIKE '2026%'"


def test_r2_1_a_never_payable_item_is_left_out_of_the_bulk_appeal_and_its_send_held(estore, eledger, tmp_path, monkeypatch):
    """Anthem's 2026 77600 denials (its rule never pays 77600 with the 2026 surface radiation codes): the run stores the
    block, so "Appeal everything appealable" leaves them out and names them on its Left out line, and the bulk send's own
    check of each item holds it. The panel offers no packet or fax button and its main button is Copy the steps."""
    from fastapi.testclient import TestClient

    import remitwatch.api as api
    from remitwatch import story
    from tests.test_ema_wiring import items, run
    from tests.test_payer_code_rules import _anthem_with_ema
    _anthem_with_ema(estore, eledger, tmp_path)
    run(estore)
    got = items(estore, ANTHEM26)
    assert got and {f["appeal_block"] for f in got} == {"never_payable"}, [(f["signal"], f["appeal_block"]) for f in got]
    x = story.explain(estore.conn, got[0]["finding_id"], today="2026-05-03")
    nx = x["next_step"]
    assert nx["payer_rule"] == "never_payable" and "no note changes that" in nx["action"]
    assert nx["appeal_ok"] is False and nx["appeal_block"] == "never_payable"
    assert not {"build_packet", "fax_payer"} & set(nx["buttons"]) and "copy_instructions" in nx["buttons"], nx["buttons"]
    if estore.db_path == ":memory:":
        return                                  # the API reads a store file: the compact kind is checked through story above
    estore.conn.commit()
    monkeypatch.setattr(api, "DB_PATH", estore.db_path)
    monkeypatch.setattr(api, "PASSWORD", "")
    monkeypatch.setenv("REMITWATCH_BULK_APPEAL", "1")
    c = TestClient(api.app)
    body = c.get("/api/appeals/appealable", headers=XHR).json()
    listed = {i["finding_id"] for i in body["items"]}
    never = {f["finding_id"] for f in got}
    assert not (never & listed), sorted(never & listed)
    left = body["left_out"]
    assert left["never_payable"] == len(never) and "never payable under the payer's rule" in left["line"], left["line"]
    assert {i["reason"] for i in left["items"] if i["finding_id"] in never} == {"never_payable"}
    ok, why = api._appeal_ok_now(got[0]["finding_id"])
    assert ok is False and "published rule does not pay this code" in why, why


def test_r2_1_the_fax_window_and_a_stored_packet_carry_the_before_you_send_it_checklist(estore, eledger, tmp_path, monkeypatch):
    """A letter-only packet keeps its checklist (returned, stored, read back) and the fax window shows it above Send the
    fax; the PDF never carries it."""
    import os
    import sqlite3

    import remitwatch.api as api
    from remitwatch import notes
    from tests.test_ema_wiring import items, run
    from tests.test_payer_code_rules import _anthem_with_ema
    _anthem_with_ema(estore, eledger, tmp_path)
    run(estore)
    if estore.db_path == ":memory:":
        return                                  # the packet routes read a store file
    estore.conn.commit()
    monkeypatch.setattr(api, "DB_PATH", estore.db_path)
    fid = items(estore, ANTHEM26)[0]["finding_id"]
    detail = api._with_payer_rules(api.finding_detail(fid, "ok"))
    conn = sqlite3.connect(estore.db_path)
    try:
        built = notes.build_letter_only(conn, detail, None)
        stored = notes.get_packet(conn, built["packet_id"])
        pdf = conn.execute("SELECT pdf FROM appeal_packets WHERE packet_id=?", (built["packet_id"],)).fetchone()[0]
    finally:
        conn.close()
    assert built["checklist"] and stored["checklist"] == built["checklist"], (built.get("checklist"), stored.get("checklist"))
    assert any("77600" in c for c in stored["checklist"])
    assert b"Before you send it" not in pdf
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(here, "remitwatch", "static", "features.js"), encoding="utf-8").read()
    i = src.index("async function xpFaxPreview")
    preview = src[i:src.index("\nasync function ", i + 10) if "\nasync function " in src[i + 10:] else i + 4000]
    assert "got.checklist" in preview and "Before you send it" in preview
    assert preview.index("Before you send it") < preview.index("Send the fax")


# =============================================================================================================
# r1-4: a patient-group denial whose work is with the payer is worth the payer's rate, not the patient's share
# =============================================================================================================
def test_r1_4_a_referral_denial_sent_back_to_the_payer_is_worth_the_payers_rate(sstore):
    """PR-288 put $150.00 on the patient; the run kept the payer's usual rate, $60.00, in the evidence. The biller got a
    retroactive referral and sent a corrected claim (Mark sent): the item waits on the payer, and is worth $60.00 on the
    payer's basis, never the patient's $150.00 under "the payer's usual rate"."""
    from remitwatch import actions, story
    _patient_denial(sstore, "288", signal="DENIED_REFERRAL", deadline="2027-01-31")
    ev = json.loads(sstore.conn.execute("SELECT evidence FROM findings WHERE finding_id='F1'").fetchone()[0])
    ev.update(payer_rate_cents=6000, payer_rate_basis="Anthem / BCBS usual 2026 rate for 99213")
    sstore.conn.execute("UPDATE findings SET evidence=? WHERE finding_id='F1'", (json.dumps(ev),))
    sstore.conn.commit()
    before = story.next_step(sstore.conn, "F1", today=TODAY2)
    assert (before["route"], before["expected_cents"]) == ("bill_patient", 15000), before          # the patient's side
    actions.apply_status(sstore.conn, "F1", "appealed", "Corrected claim sent with the retroactive referral", actor="Biller One",
                         at="2026-11-20T15:00:00+00:00")
    n = story.next_step(sstore.conn, "F1", today=TODAY2)
    assert n["route"] != "bill_patient", n["action"]
    assert (n["expected_cents"], n["expected_basis"]) == (6000, "Anthem / BCBS usual 2026 rate for 99213"), (n["expected_cents"], n["expected_basis"])


@pytest.mark.parametrize("kind", ["file", "compact"])
def test_r1_4_the_run_stores_payer_work_at_the_payers_rate(tmp_path, kind):
    """Through a detection run: Anthem put two visits on the patients (PR-204) beside 25 paid at its rate ($117.60). One
    is billed to the patient (the patient's share, $223.65, stays its size); for the other a corrected claim went to the
    payer (Mark sent): the next run stores it at Anthem's rate, so Work now's dollars count what that work can bring."""
    from remitwatch import actions, ema_reports as er
    from remitwatch.detect import Detector
    from tests.test_settled_elsewhere import ANTHEM, _add, _clp, _line, _store, _usual
    s = _store(tmp_path, kind)
    er.ensure_schema(s.conn)
    _production_covers(s, "2026-01-01", "2026-10-15")
    pcns = ["CB0000P01C017", "CB0000P02C017"]
    _add(s, tmp_path, _usual("12", "117.60") + [_clp(p, "1", _line("0.00", "CAS*PR*204*223.65~")) for p in pcns], "20261001", **ANTHEM)
    Detector(s, run_id="t1", today="2026-10-20").run()

    def items():
        return {r["patient_control_number"]: dict(r) for r in s.query(
            "SELECT * FROM findings WHERE signal='DENIED_PLAN_EXCLUSION' AND COALESCE(stale,0)=0")}
    first = items()
    assert {p: r["shortfall_cents"] for p, r in first.items()} == {pcns[0]: 22365, pcns[1]: 22365}
    rate = json.loads(first[pcns[1]]["evidence"]).get("payer_rate_cents")
    assert rate and 0 < rate < 22365, rate
    actions.apply_status(s.conn, first[pcns[1]]["finding_id"], "appealed", "Corrected claim sent with the right diagnosis",
                         actor="Biller One", at="2026-10-21T15:00:00+00:00")
    s.conn.commit()
    Detector(s, run_id="t2", today="2026-10-22").run()
    now = items()
    assert (now[pcns[0]]["shortfall_cents"], now[pcns[0]]["work_state"]) == (22365, "work_now")
    assert (now[pcns[1]]["shortfall_cents"], now[pcns[1]]["work_state"]) == (rate, "waiting"), (now[pcns[1]]["shortfall_cents"], rate)
    s.close()


# =============================================================================================================
# r1-5: the next payer's own answer caps what the patient is billed, and its contractual part is said first
# =============================================================================================================
def _supplement_answer(s, *, co=30, pr=120, allowed=120, paid=0):
    """Medicare put a routine visit on the patient (PR-49 $150.00) and sent it on; the patient's supplement then answered:
    `co` off as contractual (CO-45), `pr` on the patient's deductible (PR-1), `paid` paid."""
    _crossed_over(s, "2", "TESTCARE SUPPLEMENT", "CI", ("PR", "1"))
    s.conn.execute("DELETE FROM adjustments WHERE line_key IN (SELECT line_key FROM lines WHERE payer_name='TESTCARE SUPPLEMENT')")
    lk = s.conn.execute("SELECT line_key, claim_key, transaction_key FROM lines WHERE payer_name='TESTCARE SUPPLEMENT'").fetchone()
    rows = [(lk[2], lk[1], lk[0], "line", "CO", "45", co * 100)] + ([(lk[2], lk[1], lk[0], "line", "PR", "1", pr * 100)] if pr else [])
    s.conn.executemany("INSERT INTO adjustments (transaction_key, claim_key, line_key, level, group_code, reason_code, amount_cents) "
                       "VALUES (?,?,?,?,?,?,?)", rows)
    s.conn.execute("UPDATE lines SET allowed_cents=?, paid_cents=? WHERE line_key=?", (allowed * 100, paid * 100, lk[0]))
    s.conn.commit()


def test_r1_5_the_next_payers_patient_share_caps_the_bill(sstore):
    """The supplement allowed $120.00, took $30.00 off as contractual (CO-45) and put $120.00 on the patient (PR-1): the
    patient owes $120.00, never Medicare's whole $150.00, and the step says first that the $30.00 is not the patient's."""
    from remitwatch import story
    _supplement_answer(sstore)
    st = _state(sstore)
    assert (st["work_state"], st["rule"]) == ("work_now", "bill_patient_denial"), st["state_basis"]
    n = story.next_step(sstore.conn, "F1", today=TODAY2)
    assert n["action"].startswith("Bill the patient $120.00:") and n["expected_cents"] == 12000, (n["action"], n["expected_cents"])
    first = n["steps"][0]
    assert first == ("Before billing: Medicare Supplement carriers answered after it: it took $30.00 off as contractual (CO-45), which is "
                     "not the patient's to pay, and put $120.00 on the patient (PR-1): bill no more than that."), first


def test_r1_5_a_next_payer_that_put_nothing_on_the_patient_keeps_its_caution(sstore):
    """The unit's own case stays: a next payer that answered with a contractual adjustment only (nothing paid, nothing on
    the patient) is said before billing, and the bill is not capped to $0 (the owner's decision bills by default)."""
    from remitwatch import story
    _supplement_answer(sstore, co=150, pr=0, allowed=0)
    n = story.next_step(sstore.conn, "F1", today=TODAY2)
    assert n["action"].startswith("Bill the patient $150.00:"), n["action"]
    assert n["steps"][0].startswith("Before billing:") and "put this service on the practice (CO-45), with nothing paid" in n["steps"][0]


def test_r1_5_a_next_payer_that_paid_and_put_nothing_on_the_patient_is_said_plainly(sstore):
    """A next payer that paid part ($100.00), took the rest off as contractual and put nothing on the patient: the step says
    what it paid and took off, and that the patient may owe nothing (never "bill no more than that" with no amount)."""
    from remitwatch import story
    _supplement_answer(sstore, co=50, pr=0, allowed=100, paid=100)
    n = story.next_step(sstore.conn, "F1", today=TODAY2)
    first = n["steps"][0]
    assert first == ("Before billing: Medicare Supplement carriers answered after it: it paid $100.00, took $50.00 off as contractual "
                     "(CO-45), which is not the patient's to pay, and put nothing on the patient. If its answer settled the service, "
                     "do not bill: press Done."), first


def test_r1_5_archive_rule_8_sizes_the_item_at_the_next_payers_share(store):
    """Archive rule 8 itself: Medicare's PR-49 $150.00, then the supplement's CO-45 $30.00 and PR-1 $120.00 with nothing
    paid: the item it raises is sized $120.00."""
    from remitwatch import archive_rules as ar
    from tests.test_archive_rules import cl, ln, remit
    remit(store, "2026-03-28", [cl("CB0000951C017", [ln("99213", 150.00, 0, None, [("PR", "49", 150.00)], lcn="1")], status="19")],
          payer="MEDICARE TEST", payer_id="MCARE")
    remit(store, "2026-04-06", [cl("CB0000951C017", [ln("99213", 150.00, 0, 120.00, [("CO", "45", 30.00), ("PR", "1", 120.00)], lcn="1")],
                                   status="2")], payer="TESTCARE SUPPLEMENT", payer_id="TSUPP")
    raised = [a for a in ar.pr_group_denials(store.conn, []) if a["action"] == "raise_item"]
    assert [(a["carc"], a["shortfall_cents"]) for a in raised] == [("49", 12000)], [(a["carc"], a["shortfall_cents"]) for a in raised]


from tests.test_decisions import store  # noqa: E402,F401  (a fixture: both store kinds)


# =============================================================================================================
# r1-6: Billed the patient is never closed as paid by the patient over a non-contractual write-off
# =============================================================================================================
def test_r1_6_billed_patient_is_not_closed_as_paid_when_ema_wrote_off_the_rest(sstore):
    """Billed the patient for $150.00; EMA then shows the patient paid $80.00 and the other $70.00 written off as
    non-contractual, the line closed. Not "paid by the patient": a write-off with no owner, on Work now at the $70.00 left
    (reverse it and bill, or Done with the reason)."""
    from remitwatch import actions, story
    _patient_denial(sstore, "204", signal="DENIED_PLAN_EXCLUSION")
    actions.apply_status(sstore.conn, "F1", "appealed", "Billed the patient", actor="Biller One", at="2026-11-20T15:00:00+00:00")
    _ema_patient(sstore, balance=0, pat_paid=8000, noncontractual=7000)
    st = _state(sstore)
    assert (st["work_state"], st["rule"]) == ("work_now", "patient_unpaid"), st["state_basis"]
    assert "$70.00 written off on this line with no decision recorded and $80.00 paid by the patient" in st["state_basis"], st["state_basis"]
    n = story.next_step(sstore.conn, "F1", today=TODAY2)
    assert n["expected_cents"] == 7000 and n["action"].startswith("Send the patient another statement for $70.00"), (n["expected_cents"], n["action"])
    assert any(x.startswith("EMA wrote $70.00 off on this line: reverse that write-off") for x in n["steps"]), n["steps"]
    # the patient's payments and what EMA took off the patient's balance covering it still close it, and so does paying it all
    _ema_patient(sstore, balance=0, pat_paid=8000, pat_adj=7000)
    assert (_state(sstore)["work_state"], _state(sstore)["rule"]) == ("done", "patient_paid")
    _ema_patient(sstore, balance=0, pat_paid=15000)
    assert (_state(sstore)["work_state"], _state(sstore)["rule"]) == ("done", "patient_paid")


# =============================================================================================================
# r1-7: what a payer owes as secondary is never more than the primary left on the line
# =============================================================================================================
def test_r1_7_a_secondary_that_paid_all_the_primary_left_owes_nothing_more(store):
    """BETA paid as primary: allowed $100.00, paid $80.00, CO-45 $50.00, PR-2 $20.00. ACME, which had denied as primary
    (CO-22), paid the $20.00 BETA left as secondary, with its own allowed amount of $120.00. Nothing more is owed: the
    item closes with proof and the $20.00 is the recovery (its own allowed amount less BETA's payment, $40.00, is no debt)."""
    from tests.test_archive_rules import BETA, cl, item, ln, only, remit
    lk = remit(store, "2026-03-20", [cl("CB0000951C017", [ln("99213", 150.00, 0, None, [("CO", "22", 150.00)], lcn="1")])])[0]
    remit(store, "2026-04-01", [cl("CB0000951C017", [ln("99213", 150.00, 80.00, 100.00, [("CO", "45", 50.00), ("PR", "2", 20.00)], lcn="1")])],
          **BETA)
    remit(store, "2026-04-20", [cl("CB0000951C017", [ln("99213", 150.00, 20.00, 120.00, [("OA", "23", 100.00), ("CO", "45", 30.00)], lcn="1")],
                                   status="2")])
    a = only(ar.units_split(store.conn, [item(store, lk, "DENIED_MISSING_PRIMARY", expected=9000)]))
    assert a["action"] == "close_with_proof", (a["action"], a.get("reclass"), a.get("shortfall_cents"), a.get("hint"))
    assert (a["paid_cents"], a["counts_as_recovery"]) == (2000, True)


def test_r1_7_the_cap_is_what_the_others_left_and_only_with_their_remittances_on_file():
    f = ar.owed_in_second_role
    line = {"allowed": 12000, "charge": 15000, "paid": 1000, "adj": [("OA", "23", 10000), ("CO", "45", 3000)]}
    # BETA's remittance on file: paid $80.00, left $20.00 (its PR): ACME owes at most $20.00 of its $40.00
    due = f([line], 8000, 9000, 2000)
    assert (due["owed_cents"], due["left_cents"], due["left_by_others_cents"]) == (2000, 1000, 2000)
    assert "at most the $20.00 the other payers left on the line" in ar.second_role_words(due, "ACME", "as secondary", "2026-04-20")
    # the others left more than ACME owes: no cap
    assert f([line], 8000, 9000, 6000)["owed_cents"] == 4000 and f([line], 8000, 9000, 6000)["left_by_others_cents"] is None
    # no remittance of the others on file (only the payer's own OA-23): nothing tells what they left, no cap
    assert f([line], 0, 9000, None)["owed_cents"] == 2000 and f([line], 0, 9000, 0)["left_by_others_cents"] is None


# =============================================================================================================
# r1-8: the part that arrived as secondary counts as money returned while the rest stays listed
# =============================================================================================================
@pytest.mark.parametrize("kind", ["file", "compact"])
def test_r1_8_the_part_that_arrived_counts_while_the_rest_stays_listed(tmp_path, kind):
    """The unit's Medicaid case: $10.00 of the $20.00 Medicaid owes as secondary arrived. The rest stays listed ($10.00,
    open, work now); the payment that came is the item's proof, dated, and the Results tab counts it as money returned on
    the item (the owner's scoreboard), so it is never lost if the rest never comes. recovered_cents stays a closure's
    amount: the save keeps it on resolved items only."""
    from remitwatch import actions, results
    from remitwatch.detect import Detector
    from tests.test_settled_elsewhere import COB, MEDICAID, _add, _clp, _line, _store, _usual
    s = _store(tmp_path, kind)
    first = [_clp(f"CB0000Q0{i}C017", "1", _line("91.31", "CAS*CO*45*86.25~", "91.31", code="17110", charge="177.56")
                  + _line("0.00", COB, mods="25"), paid="91.31", cfi="MC") for i in (1, 2)]
    _add(s, tmp_path, _usual("MC", "70.00") + first, "20261001", **MEDICAID)
    Detector(s, run_id="t1").run()
    second = [_clp("CB0000Q01C017", "2", _line("10.00", "CAS*OA*23*50.00~CAS*CO*45*153.65~CAS*CO*16*10.00~", "70.00", mods="25"),
                   paid="10.00", cfi="MC")]
    _add(s, tmp_path, second, "20261110", **MEDICAID)
    Detector(s, run_id="t2", today="2026-11-20").run()
    short = dict(next(iter(s.query("SELECT * FROM findings WHERE patient_control_number='CB0000Q01C017' AND procedure_code='99214' "
                                   "AND category IN ('denial','bundling') AND COALESCE(stale,0)=0"))))
    assert (short["status"], short["shortfall_cents"], short["work_state"]) == ("open", 1000, "work_now"), short["state_basis"]
    due = json.loads(short["evidence"])["second_role"]
    assert (due["paid_cents"], due["left_cents"], due["date"]) == (1000, 1000, "2026-11-10"), due
    # flagged on October 2 (the run's own clock is today's): the payment of November 10 came after the flag
    s.conn.execute("UPDATE findings SET first_seen='2026-10-02T08:00:00+00:00' WHERE finding_id=?", (short["finding_id"],))
    s.conn.commit()
    actions.ensure_fax_tables(s.conn)
    results.refresh(s.conn, today="2026-11-20", force=True)
    detail = results._meta(s.conn, results.LISTS_KEY, {})["detail"].get(short["finding_id"]) or {}
    assert detail.get("returned") == 1000 and [e["cents"] for e in detail.get("events") or []] == [1000], detail
    s.close()


def test_r1_8_archive_rule_3_keeps_what_arrived_with_the_item_it_keeps_listed(store):
    """Archive rule 3's own case (ACME paid $12.00 of the $20.00 it owes as secondary): the reclassification keeps the
    $8.00 listed and carries what arrived after the denial ($12.00, dated) as its proof."""
    from tests.test_archive_rules import cl, item, ln, only, remit
    lk = remit(store, "2026-03-20", [cl("CB0000902C017", [ln("99213", 150.00, 0, None, [("CO", "22", 150.00)], lcn="1")])])[0]
    remit(store, "2026-04-20", [cl("CB0000902C017", [ln("99213", 150.00, 12.00, 100.00,
                                                        [("OA", "23", 80.00), ("CO", "45", 50.00), ("CO", "16", 8.00)], lcn="1")],
                                   status="2")])
    a = only(ar.units_split(store.conn, [item(store, lk, "DENIED_MISSING_PRIMARY", expected=9000)]))
    assert (a["reclass"], a["shortfall_cents"]) == ("second_role_short", 800)
    assert (a["second_role"]["arrived_cents"], a["second_role"]["date"]) == (1200, "2026-04-20")


from remitwatch import archive_rules as ar  # noqa: E402
