"""
The archive-only rules (remitwatch.archive_rules): what the 835 archive proves about an item that the item itself does
not show. Invented data only: made-up claim ids, payers, members and amounts. Each rule has a case where it must act and
a case where it must not, on both kinds of store: the server's file store and the compact in-memory store of the
serverless host.
"""
from __future__ import annotations

import itertools
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch import archive_rules as ar  # noqa: E402
from remitwatch.codes import payer_group_kind  # noqa: E402
from remitwatch.detect import Detector, ensure_schema  # noqa: E402
from remitwatch.detect import bill_of as detect_bill_of  # noqa: E402
from remitwatch.store import Store  # noqa: E402
from tests.test_efficiency import rescans  # noqa: E402

DOS = "2026-03-02"
_TX = itertools.count(1)
ACME = {"payer": "ACME HEALTH PLAN", "payer_id": "ACME1"}
BETA = {"payer": "BETA CARE", "payer_id": "BETA1"}


@pytest.fixture(params=["file", "compact"])
def store(request, tmp_path):
    s = Store(str(tmp_path / "s.sqlite")) if request.param == "file" else Store(":memory:", compact=True)
    yield s
    s.close()


def ln(code, charge, paid=0.0, allowed=None, adj=(), **kw):
    """One service line: code, charge, paid and allowed in dollars, adjustments [(group, reason, dollars)]; optional
    mods, units (paid), billed (original units), lcn, orig (the code billed), dos and rem (remark codes)."""
    return dict(code=code, charge=charge, paid=paid, allowed=allowed, adj=list(adj), **kw)


def cl(pcn, lines, **kw):
    """One claim: the claim id and its lines; optional status ('1'), freq ('1'), cfi ('CI'), icn, rem and adj."""
    return dict(pcn=pcn, lines=lines, **kw)


def neg(line):
    """The reversal's restatement of a line: every amount negated."""
    out = dict(line)
    out.update(charge=-line["charge"], paid=-(line.get("paid") or 0), allowed=None if line.get("allowed") is None else -line["allowed"],
               adj=[(g, r, -a) for g, r, a in line.get("adj", [])])
    return out


def remit(store, paid_on, claims, payer="ACME HEALTH PLAN", payer_id="ACME1", trace=None):
    """One remittance written straight into the store's 835 tables. Returns the line keys in order."""
    c = store.conn
    n = next(_TX)
    tk = f"TX{n:05d}"
    trace = trace or f"TRC{n:05d}"

    def cents(v):
        return None if v is None else int(round(v * 100))
    total = sum(cents(x.get("paid") or 0) for k in claims for x in k["lines"])
    c.execute("INSERT INTO transactions (transaction_key, trace_number, payment_date, payer_name, payer_id, superseded, "
              "claim_paid_total_cents, payment_amount_cents) VALUES (?,?,?,?,?,0,?,?)", (tk, trace, paid_on, payer, payer_id, total, total))
    keys = []
    for i, k in enumerate(claims):
        ck = f"{tk}-C{i}"
        lines = k["lines"]
        dos = lines[0].get("dos", DOS) if lines else DOS
        st = k.get("status", "1")
        c.execute("""INSERT INTO claims (claim_key, transaction_key, patient_control_number, claim_status_code, charge_cents, paid_cents,
                     claim_filing_indicator, payer_claim_control_number, claim_frequency_code, patient_last, patient_first, patient_id,
                     payer_name, payer_id, payment_date, trace_number, statement_from, statement_to)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (ck, tk, k["pcn"], st, sum(cents(x["charge"]) for x in lines), sum(cents(x.get("paid") or 0) for x in lines),
                   k.get("cfi", "CI"), k.get("icn", f"ICN{n:05d}{i}"), k.get("freq", "1"), "TESTPATIENT", "ALPHA", "M00000001",
                   payer, payer_id, paid_on, trace, dos, dos))
        for g, r, a in k.get("adj", []):
            c.execute("INSERT INTO adjustments (transaction_key, claim_key, line_key, level, group_code, reason_code, amount_cents) "
                      "VALUES (?,?,?,?,?,?,?)", (tk, ck, None, "claim", g, r, cents(a)))
        for code in k.get("rem", []):
            c.execute("INSERT INTO remarks (transaction_key, claim_key, line_key, level, qualifier, code) VALUES (?,?,?,?,?,?)",
                      (tk, ck, None, "claim", "MOA", code))
        for j, x in enumerate(lines):
            lk = f"{ck}-L{j}"
            c.execute("""INSERT INTO lines (line_key, claim_key, transaction_key, procedure_code, original_procedure_code, modifiers,
                         charge_cents, paid_cents, allowed_cents, paid_units, original_units, service_date, line_control_number,
                         patient_control_number, claim_status_code, payer_name, payer_id, payment_date)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (lk, ck, tk, x["code"], x.get("orig"), json.dumps(list(x["mods"])) if x.get("mods") else None,
                       cents(x["charge"]), cents(x.get("paid") or 0), cents(x.get("allowed")), x.get("units", 1), x.get("billed"),
                       x.get("dos", DOS), x.get("lcn"), k["pcn"], st, payer, payer_id, paid_on))
            for g, r, a in x.get("adj", []):
                c.execute("INSERT INTO adjustments (transaction_key, claim_key, line_key, level, group_code, reason_code, amount_cents) "
                          "VALUES (?,?,?,?,?,?,?)", (tk, ck, lk, "line", g, r, cents(a)))
            for code in x.get("rem", []):
                c.execute("INSERT INTO remarks (transaction_key, claim_key, line_key, level, qualifier, code) VALUES (?,?,?,?,?,?)",
                          (tk, ck, lk, "line", "LQ", code))
            keys.append(lk)
    c.commit()
    return keys


def item(store, lk, signal, category="denial", expected=None, shortfall=None, **kw):
    """A run item (the Detector's finding dict) on an 835 line, as the rules receive it."""
    cur = store.conn.execute("SELECT l.*, c.claim_filing_indicator AS cfi FROM lines l JOIN claims c ON c.claim_key = l.claim_key "
                             "WHERE l.line_key = ?", (lk,))
    r = dict(zip([d[0] for d in cur.description], cur.fetchone()))
    carc = next((row[0] for row in store.conn.execute(
        "SELECT reason_code FROM adjustments WHERE line_key = ? AND amount_cents > 0 AND reason_code NOT IN ('45', '253', '1', '2', '3') "
        "ORDER BY amount_cents DESC", (lk,))), None)
    mods = json.loads(r["modifiers"]) if r["modifiers"] else []
    out = {"finding_id": f"F-{lk}-{signal}", "signal": signal, "category": category, "line_key": lk, "claim_key": r["claim_key"],
           "patient_control_number": r["patient_control_number"], "procedure_code": r["procedure_code"],
           "original_procedure_code": r["original_procedure_code"], "billed_code": r["original_procedure_code"] or r["procedure_code"],
           "modifiers": ",".join(mods), "paid_cents": r["paid_cents"], "allowed_cents": r["allowed_cents"],
           "charge_cents": r["charge_cents"], "service_date": r["service_date"], "payment_date": r["payment_date"],
           "payer_group": payer_group_kind(r["payer_name"], r["cfi"])[0], "carc": carc, "expected_cents": expected,
           "shortfall_cents": expected if shortfall is None else shortfall, "auto_status": None, "evidence": "{}",
           "appeal_deadline": None}
    out.update(kw)
    return out


def only(acts, fid=None):
    acts = [a for a in acts if fid is None or a.get("finding_id") == fid]
    assert len(acts) == 1, acts
    return acts[0]


# =========================================================================== 1. the same payer paid before
def test_rule1_a_later_denial_of_a_line_the_payer_paid_is_reclassified_with_proof_never_closed(store):
    """The payer paid bill 101's office visit, then denied a second submission of it as a duplicate. The denial is no
    work: it is reclassified 'paid before' at $0 with the payment as proof, and stays an item (not closed)."""
    remit(store, "2026-03-20", [cl("CB0000101C017", [ln("99213", 156.66, 76.15, 76.15, [("CO", "45", 80.51)], lcn="1")])])
    lk = remit(store, "2026-04-20", [cl("CB0000101C018", [ln("99213", 156.66, 0, None, [("CO", "18", 156.66)], lcn="1")])])[0]
    f = item(store, lk, "DENIED_DUPLICATE_UNPAID", expected=7615)
    a = only(ar.same_payer_paid_before(store.conn, [f]))
    assert (a["action"], a["reclass"], a["shortfall_cents"], a["state_hint"], a["strength"]) == ("reclassify", "paid_before", 0, "done", "proven")
    assert any(p["decisive"] and p["kind"] == "payment" and p["amount_cents"] == 7615 for p in a["proof"])
    assert "Already paid by ACME HEALTH PLAN" in a["hint"]


def test_rule1_a_payment_the_payer_took_back_is_no_proof(store):
    """The same, but the payer reversed its payment before the denial: nothing stands, the denial is real."""
    paid = ln("99213", 156.66, 76.15, 76.15, [("CO", "45", 80.51)], lcn="1")
    remit(store, "2026-03-20", [cl("CB0000102C017", [paid])])
    remit(store, "2026-04-01", [cl("CB0000102C017", [neg(paid)], status="22")])
    lk = remit(store, "2026-04-20", [cl("CB0000102C018", [ln("99213", 156.66, 0, None, [("CO", "18", 156.66)], lcn="1")])])[0]
    f = item(store, lk, "DENIED_DUPLICATE_UNPAID", expected=7615)
    assert ar.same_payer_paid_before(store.conn, [f]) == []


def test_rule1_a_short_payment_paid_twice_is_a_possible_overpayment(store):
    """A short payment on a resubmission after the payer already paid the line in full: paid twice, size 0, to settle."""
    remit(store, "2026-03-20", [cl("CB0000103C017", [ln("17110", 177.56, 120.00, 120.00, [("CO", "45", 57.56)], lcn="1")])])
    lk = remit(store, "2026-04-20", [cl("CB0000103C018", [ln("17110", 177.56, 90.00, 90.00, [("CO", "45", 87.56)], lcn="1")])])[0]
    f = item(store, lk, "UNDERPAID_VS_OWN_RATE", "underpayment", expected=12000, shortfall=3000)
    a = only(ar.same_payer_paid_before(store.conn, [f]))
    assert (a["reclass"], a["shortfall_cents"], a["state_hint"]) == ("paid_before", 0, "work_now")
    assert a["possible_overpayment_cents"] == 9000


# =========================================================================== 2. another payer paid first
def test_rule2_a_denial_after_another_payer_paid_as_primary_says_who_paid(store):
    """BETA CARE paid the visit as primary and left $15.23 coinsurance; ACME then denied it as not its to pay (CO-22).
    Not recoverable from ACME: the item says BETA paid, and is sized at the share BETA left."""
    remit(store, "2026-03-20", [cl("CB0000201C017", [ln("99213", 156.66, 60.92, 76.15, [("CO", "45", 80.51), ("PR", "2", 15.23)])])], **BETA)
    lk = remit(store, "2026-04-20", [cl("CB0000201C017", [ln("99213", 156.66, 0, None, [("CO", "22", 156.66)])])])[0]
    f = item(store, lk, "DENIED_COB", expected=7615)
    a = only(ar.other_payer_paid_first(store.conn, [f]))
    assert (a["reclass"], a["paid_by"], a["patient_share_left_cents"], a["shortfall_cents"]) == ("other_payer_paid", "BETA CARE", 1523, 1523)
    assert "Paid by BETA CARE as primary" in a["hint"] and "not its to pay" in a["hint"]


def test_rule2_a_secondary_that_paid_the_share_leaves_nothing(store):
    remit(store, "2026-03-20", [cl("CB0000202C017", [ln("99213", 156.66, 60.92, 76.15, [("CO", "45", 80.51), ("PR", "2", 15.23)])])], **BETA)
    remit(store, "2026-03-30", [cl("CB0000202C017", [ln("99213", 156.66, 15.23, 15.23, [("OA", "23", 141.43)])], status="2")],
          payer="GAMMA MUTUAL", payer_id="GAMMA1")
    lk = remit(store, "2026-04-20", [cl("CB0000202C017", [ln("99213", 156.66, 0, None, [("CO", "22", 156.66)])])])[0]
    a = only(ar.other_payer_paid_first(store.conn, [item(store, lk, "DENIED_COB", expected=7615)]))
    assert (a["shortfall_cents"], a["state_hint"]) == (0, "done")


def test_rule2_no_standing_payment_of_another_payer_no_action(store):
    """BETA's payment was taken back, and a payment another payer made only as secondary says nothing about who is primary."""
    paid = ln("99213", 156.66, 60.92, 76.15, [("CO", "45", 80.51), ("PR", "2", 15.23)])
    remit(store, "2026-03-20", [cl("CB0000203C017", [paid])], **BETA)
    remit(store, "2026-03-25", [cl("CB0000203C017", [neg(paid)], status="22")], **BETA)
    remit(store, "2026-03-27", [cl("CB0000203C017", [ln("99213", 156.66, 10.00, 10.00, [("OA", "23", 146.66)])], status="2")],
          payer="GAMMA MUTUAL", payer_id="GAMMA1")
    lk = remit(store, "2026-04-20", [cl("CB0000203C017", [ln("99213", 156.66, 0, None, [("CO", "22", 156.66)])])])[0]
    assert ar.other_payer_paid_first(store.conn, [item(store, lk, "DENIED_COB", expected=7615)]) == []


# =========================================================================== 3. units split across lines of one code
def test_rule3_the_unpaid_line_of_two_is_the_unit_denied_at_the_payers_rate(store):
    """Two fractions of 77401 on one claim; the payer paid one line and denied the other: the item is that one unit,
    sized at the payer's own allowed amount per unit."""
    lks = remit(store, "2026-03-20", [cl("CB0000301C017", [ln("77401", 36.50, 25.04, 25.55, [("CO", "45", 10.95), ("CO", "253", 0.51)], lcn="1"),
                                                           ln("77401", 36.50, 0, None, [("CO", "151", 36.50)], mods=("59",), lcn="2")])])
    a = only(ar.units_split(store.conn, [item(store, lks[1], "DENIED_FREQUENCY", expected=3650)]))
    assert (a["reclass"], a["units_billed"], a["units_paid"], a["shortfall_cents"]) == ("unit_denied", 2, 1, 2555)


def test_rule3_the_items_own_line_paid_later_closes_it(store):
    """The payer denied the second line, then paid that very line (its line number) on a later remittance."""
    lks = remit(store, "2026-03-20", [cl("CB0000302C017", [ln("77401", 36.50, 25.04, 25.55, [("CO", "45", 10.95), ("CO", "253", 0.51)], lcn="1"),
                                                           ln("77401", 36.50, 0, None, [("CO", "151", 36.50)], mods=("59",), lcn="2")])])
    remit(store, "2026-04-25", [cl("CB0000302C017", [ln("77401", 36.50, 25.04, 25.55, [("CO", "45", 10.95), ("CO", "253", 0.51)],
                                                        mods=("59",), lcn="2")])])
    a = only(ar.units_split(store.conn, [item(store, lks[1], "DENIED_FREQUENCY", expected=3650)]))
    assert (a["action"], a["paid_cents"], a["counts_as_recovery"]) == ("close_with_proof", 2504, True)
    assert a["paid_date"] == "2026-04-25"


def test_rule3_one_line_nothing_paid_no_action(store):
    lk = remit(store, "2026-03-20", [cl("CB0000303C017", [ln("77401", 36.50, 0, None, [("CO", "151", 36.50)], lcn="1")])])[0]
    assert ar.units_split(store.conn, [item(store, lk, "DENIED_FREQUENCY", expected=3650)]) == []


# =========================================================================== 4. a later payment tops up a short one
def test_rule4_a_supplemental_payment_to_the_expected_amount_closes_the_short_payment(store):
    lk = remit(store, "2026-03-20", [cl("CB0000401C017", [ln("17110", 177.56, 60.00, 60.00, [("CO", "45", 117.56)], lcn="1")])])[0]
    remit(store, "2026-04-20", [cl("CB0000401C017", [ln("17110", 40.00, 40.00, 40.00, lcn="1")])])
    f = item(store, lk, "UNDERPAID_VS_OWN_RATE", "underpayment", expected=10000, shortfall=4000)
    a = only(ar.short_pay_topped_up(store.conn, [f]))
    assert (a["action"], a["paid_cents"], a["counts_as_recovery"], a["paid_date"]) == ("close_with_proof", 4000, True, "2026-04-20")


def test_rule4_a_second_full_payment_is_a_possible_overpayment(store):
    lk = remit(store, "2026-03-20", [cl("CB0000402C017", [ln("17110", 177.56, 60.00, 60.00, [("CO", "45", 117.56)], lcn="1")])])[0]
    remit(store, "2026-04-20", [cl("CB0000402C017", [ln("17110", 177.56, 60.00, 60.00, [("CO", "45", 117.56)], lcn="1")])])
    f = item(store, lk, "UNDERPAID_VS_OWN_RATE", "underpayment", expected=10000, shortfall=4000)
    a = only(ar.short_pay_topped_up(store.conn, [f]))
    assert (a["reclass"], a["shortfall_cents"], a["possible_overpayment_cents"]) == ("overpaid", 0, 2000)


def test_rule4_another_payers_later_payment_is_no_top_up(store):
    lk = remit(store, "2026-03-20", [cl("CB0000403C017", [ln("17110", 177.56, 60.00, 60.00, [("CO", "45", 117.56)], lcn="1")])])[0]
    remit(store, "2026-04-20", [cl("CB0000403C017", [ln("17110", 177.56, 40.00, 40.00, [("OA", "23", 137.56)], lcn="1")], status="2")], **BETA)
    f = item(store, lk, "UNDERPAID_VS_OWN_RATE", "underpayment", expected=10000, shortfall=4000)
    assert ar.short_pay_topped_up(store.conn, [f]) == []


# =========================================================================== 5. answers on the same day, reversals
def test_rule5_the_payer_reversed_a_denial_and_paid_the_line(store):
    denial = ln("17110", 177.56, 0, None, [("CO", "16", 177.56)], lcn="1", rem=["M51"])
    lk = remit(store, "2026-03-20", [cl("CB0000501C017", [denial])])[0]
    remit(store, "2026-04-20", [cl("CB0000501C017", [neg(denial)], status="22"),
                                cl("CB0000501C017", [ln("17110", 177.56, 100.00, 100.00, [("CO", "45", 77.56)], lcn="1")])],
          trace="TRCREV501")
    a = only(ar.same_day_answers(store.conn, [item(store, lk, "DENIED_MISSING_INFO", expected=10000)]))
    assert (a["action"], a["paid_cents"], a["counts_as_recovery"]) == ("close_with_proof", 10000, True)


def test_rule5_a_denial_reversed_onto_the_patients_deductible_is_done(store):
    denial = ln("99213", 156.66, 0, None, [("CO", "97", 156.66)], lcn="1")
    lk = remit(store, "2026-03-20", [cl("CB0000502C017", [denial])])[0]
    remit(store, "2026-04-20", [cl("CB0000502C017", [neg(denial)], status="22"),
                                cl("CB0000502C017", [ln("99213", 156.66, 0, 76.15, [("CO", "45", 80.51), ("PR", "1", 76.15)], lcn="1")])])
    a = only(ar.same_day_answers(store.conn, [item(store, lk, "EM_BUNDLED_MOD25", "bundling", expected=7615)]))
    assert (a["reclass"], a["shortfall_cents"], a["state_hint"], a["patient_share_cents"]) == ("moved_to_patient", 0, "done", 7615)


def test_rule5_a_split_claim_measured_across_its_halves(store):
    """The payer split the claim in two on one remittance (MA15) and answered the line on both halves: together it
    allowed the expected amount, so the half that looks short is paid."""
    lks = remit(store, "2026-03-20", [cl("CB0000503C017", [ln("77600", 819.53, 150.00, 150.00, [("CO", "45", 669.53)], lcn="1")], rem=["MA15"]),
                                      cl("CB0000503C017", [ln("77600", 819.53, 200.00, 200.00, [("CO", "45", 619.53)], lcn="1")], rem=["MA15"])],
                trace="TRCSPLIT")
    f = item(store, lks[0], "UNDERPAID_VS_OWN_RATE", "underpayment", expected=35000, shortfall=20000)
    a = only(ar.same_day_answers(store.conn, [f]))
    assert (a["action"], a["counts_as_recovery"], a["paid_cents"]) == ("close_with_proof", False, 0)


def test_rule5_a_line_dropped_on_the_practices_corrected_claim_is_done(store):
    lks = remit(store, "2026-03-20", [cl("CB0000504C017", [ln("77401", 36.50, 0, None, [("CO", "151", 36.50)], lcn="1"),
                                                           ln("77401", 36.50, 0, None, [("CO", "151", 36.50)], mods=("59",), lcn="2")])])
    remit(store, "2026-04-20", [cl("CB0000504C018", [ln("77401", 36.50, 25.04, 25.55, [("CO", "45", 10.95), ("CO", "253", 0.51)], lcn="1")],
                                   freq="7")])
    a = only(ar.same_day_answers(store.conn, [item(store, lks[1], "DENIED_FREQUENCY", expected=3650)]))
    assert (a["reclass"], a["state_hint"], a["shortfall_cents"]) == ("dropped_on_correction", "done", 0)


def test_rule5_a_whole_claim_denial_answered_on_another_claim_the_same_day(store):
    remit(store, "2026-03-20", [cl("CB0000505C017", [ln("99213", 156.66, 0, None, [("CO", "18", 156.66)])], status="4"),
                                cl("CB0000505C018", [ln("99213", 156.66, 76.15, 76.15, [("CO", "45", 80.51)])])], trace="TRCDUP")
    ck = store.conn.execute("SELECT claim_key FROM claims WHERE patient_control_number = 'CB0000505C017'").fetchone()[0]
    f = {"finding_id": "F-claim-505", "signal": "CLAIM_DENIED_UNPAID", "category": "denial", "claim_key": ck, "line_key": None,
         "patient_control_number": "CB0000505C017", "payer_group": "Acme Health Plan", "auto_status": None,
         "evidence": json.dumps({"unpaid": [["99213", DOS]]}), "paid_cents": 0, "expected_cents": 15666, "shortfall_cents": 15666}
    a = only(ar.same_day_answers(store.conn, [f]))
    assert (a["action"], a["counts_as_recovery"], a["paid_cents"]) == ("close_with_proof", False, 7615)


def test_rule5_no_reversal_no_other_answer_no_action(store):
    lk = remit(store, "2026-03-20", [cl("CB0000506C017", [ln("17110", 177.56, 0, None, [("CO", "16", 177.56)], lcn="1")])])[0]
    remit(store, "2026-04-20", [cl("CB0000506C017", [ln("17110", 177.56, 100.00, 100.00, [("CO", "45", 77.56)], lcn="1")], status="2")], **BETA)
    assert ar.same_day_answers(store.conn, [item(store, lk, "DENIED_MISSING_INFO", expected=10000)]) == []


def test_rule5_a_later_duplicate_denial_is_not_the_new_answer(store):
    """Reversed onto the deductible, then a resubmitted copy denied as a duplicate: the copy's denial is about the copy;
    the payer's answer to the service is the deductible (C11, C12, C57)."""
    denial = ln("99213", 156.66, 0, None, [("CO", "97", 156.66)], lcn="1")
    lk = remit(store, "2026-03-20", [cl("CB0000507C017", [denial])])[0]
    remit(store, "2026-04-20", [cl("CB0000507C017", [neg(denial)], status="22"),
                                cl("CB0000507C017", [ln("99213", 156.66, 0, 76.15, [("CO", "45", 80.51), ("PR", "1", 76.15)], lcn="1")])])
    remit(store, "2026-05-20", [cl("CB0000507C018", [ln("99213", 156.66, 0, None, [("OA", "18", 156.66)], lcn="1")])])
    a = only(ar.same_day_answers(store.conn, [item(store, lk, "EM_BUNDLED_MOD25", "bundling", expected=7615)]))
    assert a["reclass"] == "moved_to_patient"


# =========================================================================== 6. a second payer's share
def test_rule6_a_secondary_that_saw_a_primary_payment_this_archive_lacks(store):
    lk = remit(store, "2026-03-20", [cl("CB0000601C017", [ln("99213", 85.14, 0, None, [("CO", "97", 85.14)])])])[0]
    remit(store, "2026-05-02", [cl("CB0000601C017", [ln("99213", 85.14, 6.02, 50.12, [("OA", "23", 79.12)])], status="2")], **BETA)
    a = only(ar.second_payer_share(store.conn, [item(store, lk, "EM_BUNDLED_MOD25", "bundling", expected=8088)]))
    assert (a["reclass"], a["strength"], a["shortfall_cents"], a["prior_payer_cents"]) == ("secondary_paid", "likely", None, 7912)


def test_rule6_a_primary_that_took_its_payment_back_makes_the_denial_real(store):
    """The primary's reversal of its payment is on file (the payment itself is older than the archive): the secondary
    paid on the answer that was taken back, and the denial stands."""
    remit(store, "2026-03-20", [cl("CB0000602C017", [ln("99213", -85.14, -60.00, -76.00, [("PR", "2", -16.00), ("CO", "45", -9.14)])],
                                   status="22"),
                                cl("CB0000602C017", [ln("99213", 85.14, 0, None, [("CO", "150", 85.14)])], status="19")], trace="TRC602")
    lk = store.conn.execute("SELECT line_key FROM lines WHERE patient_control_number = 'CB0000602C017' AND claim_status_code = '19'").fetchone()[0]
    remit(store, "2026-05-02", [cl("CB0000602C017", [ln("99213", 85.14, 16.00, 76.00, [("OA", "23", 69.14)])], status="2")], **BETA)
    assert ar.second_payer_share(store.conn, [item(store, lk, "DENIED_LEVEL_OF_SERVICE", expected=6000)]) == []


# =========================================================================== 7. repeat denials of one service
def test_rule7_repeat_denials_of_one_service_fold_into_the_newest(store):
    lk1 = remit(store, "2026-03-20", [cl("CB0000701C017", [ln("J7345", 358.00, 0, None, [("CO", "114", 358.00)], units=200, lcn="1")])])[0]
    lk2 = remit(store, "2026-04-20", [cl("CB0000701C018", [ln("J7345", 358.00, 0, None, [("CO", "114", 358.00)], units=200, lcn="1")])])[0]
    f1, f2 = item(store, lk1, "DENIED_NONCOVERED"), item(store, lk2, "DENIED_NONCOVERED")
    a = only(ar.fold_repeat_denials(store.conn, [f1, f2], worked=set()))
    assert (a["finding_id"], a["into"]) == (f1["finding_id"], f2["finding_id"])


def test_rule7_two_units_of_one_code_and_worked_items_never_fold(store):
    lks = remit(store, "2026-03-20", [cl("CB0000702C017", [ln("77401", 36.50, 0, None, [("CO", "151", 36.50)], lcn="1"),
                                                           ln("77401", 36.50, 0, None, [("CO", "151", 36.50)], mods=("59",), lcn="2")])])
    later = remit(store, "2026-04-20", [cl("CB0000702C017", [ln("77401", 36.50, 0, None, [("CO", "151", 36.50)], mods=("59",), lcn="2")])])
    f1, f2, f3 = (item(store, lks[0], "DENIED_FREQUENCY"), item(store, lks[1], "DENIED_FREQUENCY"), item(store, later[0], "DENIED_FREQUENCY"))
    acts = ar.fold_repeat_denials(store.conn, [f1, f2, f3], worked=set())
    assert [(a["finding_id"], a["into"]) for a in acts] == [(f2["finding_id"], f3["finding_id"])]      # line 1 is another unit
    assert ar.fold_repeat_denials(store.conn, [f1, f2, f3], worked={f2["finding_id"]}) == []          # a person worked it


# =========================================================================== 8. patient-group denials and CO-147
def test_rule8_a_plan_exclusion_raises_an_item_saying_who_pays_and_what_to_fix(store):
    remit(store, "2026-03-20", [cl("CB0000801C017", [ln("11900", 93.75, 0, 93.75, [("PR", "204", 93.75)])])])
    a = only(ar.pr_group_denials(store.conn, [], rate=lambda g, c, y: 3767 if c == "11900" else None))
    assert (a["action"], a["signal"], a["carc"], a["carc_group"], a["expected_cents"], a["shortfall_cents"]) == \
        ("raise_item", "DENIED_PLAN_EXCLUSION", "204", "PR", 3767, 3767)
    assert a["who_should_pay"].startswith("the patient") and "Who should pay" in a["hint"] and "What to fix" in a["hint"]
    assert a["evidence"]["basis"] == "Acme Health Plan's usual 2026 rate for 11900"
    # a fallback rate says where it came from (the Detector's usual_rate names Medicare's fee schedule, say)
    b = only(ar.pr_group_denials(store.conn, [], rate=lambda g, c, y: (4100, "Medicare 2026 rate for 11900 (physician)")))
    assert (b["shortfall_cents"], b["evidence"]["basis"]) == (4100, "Medicare 2026 rate for 11900 (physician)")
    # with no usual rate, the whole charge the payer put down as 'allowed' is no price: no size
    assert only(ar.pr_group_denials(store.conn, []))["shortfall_cents"] is None


def test_rule8_a_medicaid_members_missing_referral_is_the_payers_to_pay(store):
    remit(store, "2026-03-20", [cl("CB0000802C017", [ln("99204", 293.16, 0, None, [("PR", "288", 293.16)])], cfi="MC")],
          payer="STATE MEDICAID PLAN", payer_id="MCD1")
    a = only(ar.pr_group_denials(store.conn, []))
    assert a["signal"] == "DENIED_REFERRAL" and "Medicaid member cannot be billed" in a["who_should_pay"]


def test_rule8_cost_share_listed_lines_and_paid_services_raise_nothing(store):
    remit(store, "2026-03-20", [cl("CB0000803C017", [ln("99213", 156.66, 0, 76.15, [("PR", "1", 76.15), ("CO", "45", 80.51)])])])
    lk = remit(store, "2026-03-20", [cl("CB0000804C017", [ln("11900", 93.75, 0, None, [("PR", "204", 93.75)])])])[0]
    remit(store, "2026-03-20", [cl("CB0000805C017", [ln("11900", 93.75, 0, None, [("PR", "27", 93.75)])])])
    remit(store, "2026-04-20", [cl("CB0000805C017", [ln("11900", 93.75, 60.00, 60.00, [("CO", "45", 33.75)])])], **BETA)
    listed = item(store, lk, "DENIED_NONCOVERED")
    assert ar.pr_group_denials(store.conn, [listed]) == []


def test_rule8_co147_is_sized_by_the_gap_to_the_usual_rate(store):
    remit(store, "2026-03-20", [cl("CB0000806C017", [ln("99204", 292.64, 0, 93.49, [("CO", "147", 199.15), ("PR", "1", 93.49)])]),
                                cl("CB0000807C017", [ln("99204", 292.64, 0, 186.98, [("CO", "147", 105.66), ("PR", "1", 186.98)])])])
    a = only(ar.pr_group_denials(store.conn, [], rate=lambda g, c, y: 14090))
    assert (a["signal"], a["expected_cents"], a["shortfall_cents"]) == ("DENIED_CONTRACT_RATE", 14090, 14090 - 9349)


# =========================================================================== 9. hide rules at payer x code x modifier x reason
def test_rule9_hide_rules_match_at_payer_code_modifier_and_reason_grain():
    f = {"payer_group": "Acme Health Plan", "signal": "EM_BUNDLED_MOD25", "procedure_code": "99213", "modifiers": "25",
         "carc": "97", "evidence": json.dumps({"carc_group": "CO"})}
    assert ar.hide_match(ar.hide_rule("Acme Health Plan", code="99213", modifier="25", reason="CO-97"), f)
    assert ar.hide_match(ar.hide_rule("Acme Health Plan", code="@em"), f)
    assert ar.hide_match(ar.hide_rule("Acme Health Plan", signal="EM_BUNDLED_MOD25"), f)         # the old payer x signal rule
    assert not ar.hide_match(ar.hide_rule("Acme Health Plan", code="99213", modifier="none"), f)
    assert not ar.hide_match(ar.hide_rule("Acme Health Plan", reason="PR-97"), f)
    assert not ar.hide_match(ar.hide_rule("Acme Health Plan", code="99214"), f)
    assert not ar.hide_match(ar.hide_rule("Beta Care", code="99213"), f)
    assert not ar.hide_match(ar.hide_rule("Acme Health Plan"), dict(f, signal="NO_ANSWER"))    # money nobody asked for yet
    assert ar.hide_key(ar.hide_rule("Acme Health Plan", code="99213", reason="97")) == "Acme Health Plan|*|99213|*|97"


def test_rule9_the_sql_form_covers_the_same_rows(store):
    ensure_schema(store.conn)
    rows = [("H1", "Acme Health Plan", "EM_BUNDLED_MOD25", "99213", None, "25", "97", "em"),
            ("H2", "Acme Health Plan", "EM_BUNDLED_MOD25", "99213", None, None, "97", "em"),
            ("H3", "Acme Health Plan", "DENIED_NONCOVERED", "J7345", None, None, "114", "drug"),
            ("H4", "Beta Care", "EM_BUNDLED_MOD25", "99213", None, "25", "97", "em")]
    store.conn.executemany("INSERT INTO findings (finding_id, payer_group, signal, procedure_code, original_procedure_code, modifiers, carc, "
                           "code_category) VALUES (?,?,?,?,?,?,?,?)", rows)
    for rule in (ar.hide_rule("Acme Health Plan", code="99213", modifier="25", reason="CO-97"), ar.hide_rule("Acme Health Plan", code="@drug"),
                 ar.hide_rule("Acme Health Plan", modifier="none"), ar.hide_rule("Acme Health Plan", signal="EM_BUNDLED_MOD25")):
        cond, params = ar.hide_sql(rule, "f")
        got = {r[0] for r in store.conn.execute(f"SELECT f.finding_id FROM findings f WHERE {cond}", params)}
        want = {r[0] for r in rows if ar.hide_match(rule, {"payer_group": r[1], "signal": r[2], "procedure_code": r[3],
                                                          "modifiers": r[5] or "", "carc": r[6]})}
        assert got == want, (rule, got, want)
    acts = ar.hide_matches(store.conn, [{"finding_id": "X", "payer_group": "Acme Health Plan", "signal": "EM_BUNDLED_MOD25",
                                         "procedure_code": "99213", "modifiers": "25", "carc": "97"}],
                           rules=[ar.hide_rule("Acme Health Plan", code="99213")])
    assert [(a["action"], a["finding_id"]) for a in acts] == [("hide_match", "X")]


# =========================================================================== 10. drugs and skin substitutes
def _paid_j7345(store, n, per_unit, start=10):
    for i in range(n):
        remit(store, "2026-02-20", [cl(f"CB00009{start + i:02d}C017", [ln("J7345", 358.00, per_unit * 200, per_unit * 200,
                                                                          [("CO", "45", 358.00 - per_unit * 200)], units=200)])])


def test_rule10_a_drug_denial_gets_the_payers_own_price_per_unit(store):
    _paid_j7345(store, 3, 1.75)
    lk = remit(store, "2026-03-20", [cl("CB0000901C017", [ln("J7345", 358.00, 0, None, [("CO", "114", 358.00)], units=200)])])[0]
    a = only(ar.drug_expected(store.conn, [item(store, lk, "DENIED_NONCOVERED")]))
    assert (a["expected_cents"], a["shortfall_cents"]) == (35000, 35000)
    assert "median allowed for J7345 in 2026 quarter 1" in a["basis"] and "3 paid lines" in a["basis"]


def test_rule10_the_payers_own_price_for_the_very_line_comes_first(store):
    """The payer paid this line at $1.79 a unit, took the payment back and denied it: its own price for the line is the
    expected amount, whatever its other lines say."""
    _paid_j7345(store, 3, 1.50)
    paid = ln("J7345", 358.00, 358.00, 358.00, units=200, lcn="1")
    remit(store, "2026-03-10", [cl("CB0000902C017", [paid])])
    lk = remit(store, "2026-04-20", [cl("CB0000902C017", [neg(paid)], status="22"),
                                     cl("CB0000902C017", [ln("J7345", 358.00, 0, None, [("CO", "50", 358.00)], units=200, lcn="1")])])[1]
    a = only(ar.drug_expected(store.conn, [item(store, lk, "DENIED_MEDICAL_NECESSITY")]))
    assert a["expected_cents"] == 35800 and "own allowed amount for this line" in a["basis"] and "taken back since" in a["basis"]


def test_rule10_too_few_paid_lines_or_a_restatement_piece_get_no_amount(store):
    _paid_j7345(store, 2, 1.75)
    lk = remit(store, "2026-03-20", [cl("CB0000903C017", [ln("J7345", 358.00, 0, None, [("CO", "114", 358.00)], units=200)])])[0]
    assert ar.drug_expected(store.conn, [item(store, lk, "DENIED_NONCOVERED")]) == []
    _paid_j7345(store, 3, 1.75, start=40)
    piece = remit(store, "2026-03-20", [cl("CB0000904C017", [ln("J7345", 257.40, 0, None, [("CO", "B13", 257.40)], units=200)])])[0]
    assert ar.drug_expected(store.conn, [item(store, piece, "DENIED_DUPLICATE_UNPAID")]) == []


# =========================================================================== the combined plan and the Detector
def test_evaluate_settles_one_action_per_item_closures_first(store):
    """An item two rules speak for: the closure the archive proves wins over a reclassification."""
    denial = ln("17110", 177.56, 0, None, [("CO", "16", 177.56)], lcn="1")
    lk = remit(store, "2026-03-20", [cl("CB0000951C017", [denial])])[0]
    remit(store, "2026-04-20", [cl("CB0000951C017", [neg(denial)], status="22"),
                                cl("CB0000951C017", [ln("17110", 177.56, 100.00, 100.00, [("CO", "45", 77.56)], lcn="1")])])
    f = item(store, lk, "DENIED_MISSING_INFO", expected=10000)
    res = ar.evaluate(store.conn, [f], hide_rules=[])
    mine = [a for a in res["actions"] if a.get("finding_id") == f["finding_id"]]
    assert [a["action"] for a in mine] == ["close_with_proof"] and mine[0]["rule"] == ar.R_SAME_DAY
    assert res["summary"]["applied"] == {"same_day_answers:close_with_proof": 1}


def test_bill_of_agrees_with_detect():
    for pcn in ("CB0000300C017", "CB0000300C", "AB12CD3E4FC1", "XY99ABCDEC023", "NOTACLAIMID", "", None):
        assert ar.bill_of(pcn) == detect_bill_of(pcn), pcn


def test_no_rule_rescans_a_table_for_every_row_on_the_compact_store(tmp_path):
    store = Store(":memory:", compact=True)
    ensure_schema(store.conn)
    items = []
    for i in range(12):
        pcn = f"CB00{i:05d}C017"
        lks = remit(store, "2026-03-20", [cl(pcn, [ln("77401", 36.50, 25.04, 25.55, [("CO", "45", 10.95)], lcn="1"),
                                                   ln("77401", 36.50, 0, None, [("CO", "151", 36.50)], mods=("59",), lcn="2"),
                                                   ln("J7345", 358.00, 0, None, [("CO", "114", 358.00)], units=200, lcn="3"),
                                                   ln("11900", 93.75, 0, None, [("PR", "204", 93.75)], lcn="4")])])
        items += [item(store, lks[1], "DENIED_FREQUENCY", expected=3650), item(store, lks[2], "DENIED_NONCOVERED")]
    seen: list[str] = []
    store.conn.set_trace_callback(seen.append)
    res = ar.evaluate(store.conn, items, hide_rules=[ar.hide_rule("Acme Health Plan", code="@drug")])
    store.conn.set_trace_callback(None)
    assert res["actions"]
    problems = {}
    for sql in dict.fromkeys(seen):
        if sql.lstrip().upper().startswith(("SELECT", "WITH", "INSERT", "UPDATE", "DELETE")):
            bad = rescans(store.conn, sql)
            if bad:
                problems[" ".join(sql.split())[:160]] = bad
    assert not problems, problems
    store.close()


def _detector_world(tmp_path, kind):
    """A small archive the Detector reads: 25 paid visits for its rates, a plan exclusion no rule lists (PR-204), a drug
    the payer paid three times and then denied, and one service denied twice for the same reason."""
    from tests.test_features import claim, svc
    from tests.test_remitwatch import remit as envelope
    claims = [claim(1 + i, svc("99214", "223.65", "117.60", "117.60", "CAS*CO*45*106.05~")) for i in range(25)]
    claims += [claim(100 + i, svc("J7345", "358.00", "350.00", "350.00", "CAS*CO*45*8.00~").replace("**1~", "**200~")) for i in range(3)]
    claims.append(claim(200, svc("11900", "93.75", "0.00", None, "CAS*PR*204*93.75~")))
    claims.append(claim(300, svc("J7345", "358.00", "0.00", None, "CAS*CO*114*358.00~").replace("**1~", "**200~")))
    later = [claim(300, svc("J7345", "358.00", "0.00", None, "CAS*CO*114*358.00~").replace("**1~", "**200~")).replace("C017*", "C018*")]
    store = Store(str(tmp_path / "s.sqlite")) if kind == "file" else Store(":memory:", compact=True)
    paths = []
    for n, (batch, day) in enumerate(((claims, "20260415"), (later, "20260515"))):
        p = tmp_path / f"r{n:04d}.RMT"
        p.write_bytes(envelope("".join(batch), "100.00", payer="ANTHEM", seg_count=99, isa_ctrl=f"{n + 1:09d}", pay_date=day))
        paths.append(str(p))
    store.ingest_835_paths(paths)
    return store


@pytest.mark.parametrize("kind", ["file", "compact"])
def test_the_detector_hooks_apply_the_rules_before_save(tmp_path, monkeypatch, kind):
    monkeypatch.setattr(Detector, "hook_expected", lambda self: ar.hook_expected(self))
    monkeypatch.setattr(Detector, "hook_archive_rules", lambda self: ar.hook_archive_rules(self))
    monkeypatch.setattr(Detector, "hook_fold", lambda self: ar.hook_fold(self))
    store = _detector_world(tmp_path, kind)
    Detector(store, run_id="t").run()
    rows = {(r["patient_control_number"], r["procedure_code"], r["signal"]): dict(r) for r in
            store.query("SELECT patient_control_number, procedure_code, signal, expected_cents, shortfall_cents, hint, evidence FROM findings")}
    raised = rows.get(("CB0000200C017", "11900", "DENIED_PLAN_EXCLUSION"))
    assert raised is not None and "Who should pay" in (raised["hint"] or ""), sorted(rows)
    j = [r for k, r in rows.items() if k[0].startswith("CB0000300") and k[1] == "J7345"]
    assert len(j) == 1, j                                       # the earlier denial folded into the newer one
    ev = json.loads(j[0]["evidence"] or "{}")
    assert j[0]["expected_cents"] == 35000 and "expected_basis" in ev
    assert [x["rule"] for x in ev.get("folded", [])] == [ar.R_FOLD]
    store.close()


def test_without_the_hooks_nothing_changes(tmp_path):
    """The Detector's hook points are no-ops until wired: the same archive raises no plan exclusion item, and lists the
    drug denial twice with no expected amount."""
    store = _detector_world(tmp_path, "file")
    Detector(store, run_id="t").run()
    assert not store.query("SELECT 1 FROM findings WHERE signal = 'DENIED_PLAN_EXCLUSION'")
    j = store.query("SELECT expected_cents FROM findings WHERE patient_control_number LIKE 'CB0000300%' AND procedure_code = 'J7345'")
    assert [r["expected_cents"] for r in j] == [None, None]
    store.close()
