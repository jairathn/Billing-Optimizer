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
