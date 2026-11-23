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
    """One claim: the claim id and its lines; optional status ('1'), freq ('1'), cfi ('CI'), icn, rem, adj (claim-level
    adjustments) and claim_paid (the claim's own payment, CLP04, in dollars, when it differs from its lines' sum)."""
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
    def claim_paid(k):
        return cents(k["claim_paid"]) if "claim_paid" in k else sum(cents(x.get("paid") or 0) for x in k["lines"])
    total = sum(claim_paid(k) for k in claims)
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
                  (ck, tk, k["pcn"], st, sum(cents(x["charge"]) for x in lines), claim_paid(k),
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
    """Paid $60 twice for a line the payer allows at $60: by the payer's own reckoning it owes $60 once and overpaid $60,
    which is what it will take back (the expected $100 is the practice's figure, not the payer's)."""
    lk = remit(store, "2026-03-20", [cl("CB0000402C017", [ln("17110", 177.56, 60.00, 60.00, [("CO", "45", 117.56)], lcn="1")])])[0]
    remit(store, "2026-04-20", [cl("CB0000402C017", [ln("17110", 177.56, 60.00, 60.00, [("CO", "45", 117.56)], lcn="1")])])
    f = item(store, lk, "UNDERPAID_VS_OWN_RATE", "underpayment", expected=10000, shortfall=4000)
    a = only(ar.short_pay_topped_up(store.conn, [f]))
    assert (a["reclass"], a["shortfall_cents"], a["possible_overpayment_cents"]) == ("overpaid", 0, 6000)


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


# =========================================================================== fixes from the two reviews of unit B2
# Each test names the finding it guards. Invented data only.

def _det(*items):
    """The part of a Detector apply_actions uses for reclassifications and folds: its items."""
    class _D:
        def __init__(self):
            self.findings = {f["finding_id"]: dict(f) for f in items}
    return _D()


def _wire(monkeypatch):
    """needs_B2.md section 1: the three hook bodies, patched into the real Detector."""
    monkeypatch.setattr(Detector, "hook_expected", lambda self: ar.hook_expected(self))
    monkeypatch.setattr(Detector, "hook_archive_rules", lambda self: ar.hook_archive_rules(self))
    monkeypatch.setattr(Detector, "hook_fold", lambda self: ar.hook_fold(self))


# ---- a remittance that restates an earlier payment (review money, high)
def test_rule4_a_reprocessing_that_restates_the_earlier_payment_is_not_a_second_payment(store):
    """The payer reprocesses the claim 56 days later: the line lists $81.08 again, but the claim pays $0 and takes the
    amount off at the claim level (OA-23). A restatement, not a second payment: no 'possible overpayment, refund it'."""
    first = remit(store, "2026-04-01", [cl("CB0002001C017", [ln("11102", 89.47, 81.08, 82.42, [("CO", "45", 8.39)])])], payer="TEST FUND",
                  payer_id="TFUND")
    remit(store, "2026-05-27", [cl("CB0002001C017", [ln("11102", 89.47, 81.08, 82.42, [("CO", "45", 8.39)])], claim_paid=0.0,
                                   adj=[("OA", "23", 81.08)])], payer="TEST FUND", payer_id="TFUND")
    f = item(store, first[0], "UNDERPAID_VS_OWN_RATE", "underpayment", expected=9095, shortfall=853)
    res = ar.evaluate(store.conn, [f], rules=[ar.R_TOPPED_UP, ar.R_PAID_BEFORE], hide_rules=[])
    assert not [a for a in res["actions"] if a.get("possible_overpayment_cents")], res["actions"]


def test_rule4_a_restating_remittance_that_pays_a_top_up_closes_the_short_payment(store):
    """The reprocessing restates the earlier $81.08 on the line, allows it at the expected amount, and its claim pays only
    the difference ($9.87): a top-up to the expected amount, closed with that remittance as proof, for $9.87."""
    first = remit(store, "2026-04-01", [cl("CB0002002C017", [ln("11102", 99.47, 81.08, 81.08, [("CO", "45", 18.39)], lcn="1")])])
    remit(store, "2026-05-27", [cl("CB0002002C017", [ln("11102", 99.47, 90.95, 90.95, [("CO", "45", 8.52)], lcn="1")], claim_paid=9.87,
                                   adj=[("OA", "23", 81.08)])])
    f = item(store, first[0], "UNDERPAID_VS_OWN_RATE", "underpayment", expected=9095, shortfall=987)
    a = only(ar.short_pay_topped_up(store.conn, [f]))
    assert (a["action"], a["paid_cents"]) == ("close_with_proof", 987)
    assert any("a restatement, not a new payment" in p["text"] for p in a["proof"])


def test_rule1_does_not_call_a_restated_line_paid_twice(store):
    """The short payment is on a remittance whose claim paid $0 (claim-level CO-B13 takes the line's $76.11 off): it repeats
    the $89.54 paid before. Already paid (restated), done, never 'paid twice'."""
    remit(store, "2026-04-01", [cl("CB0002003C017", [ln("17110", 99.31, 89.54, 91.37, [("CO", "45", 7.94), ("CO", "253", 1.83)], lcn="1")])])
    again = remit(store, "2026-06-20", [cl("CB0002003C017", [ln("17110", 99.31, 76.11, 77.66, [("CO", "172", 21.65), ("CO", "253", 1.55)], lcn="2")],
                                           claim_paid=0.0, adj=[("CO", "B13", 76.11)])])
    f = item(store, again[0], "UNDERPAID_VS_OWN_RATE", "underpayment", expected=9137, shortfall=1371)
    a = only(ar.same_payer_paid_before(store.conn, [f]))
    assert a.get("possible_overpayment_cents") is None
    assert (a["reclass"], a["label"], a["state_hint"], a["shortfall_cents"]) == ("paid_before", "Already paid by this payer (restated)", "done", 0)
    assert "it is not a second one" in a["hint"]


def test_rule1_a_restated_payment_whose_original_was_taken_back_proves_nothing(store):
    """The only 'payment' before the denial is a restatement (its claim paid $0, PI-B13), and the real payment it repeats was
    taken back later: the line is unpaid today, so the denial stays to work."""
    paid = ln("17311", 1078.33, 699.49, 735.00, [("CO", "45", 343.33), ("PR", "3", 35.51)], lcn="1")
    remit(store, "2026-02-01", [cl("CB0002004C017", [paid])])
    remit(store, "2026-02-20", [cl("CB0002004C017", [ln("17311", 1078.33, 201.28, 201.28, [("CO", "45", 877.05)], lcn="1")], claim_paid=0.0,
                                   adj=[("PI", "B13", 201.28)])])
    lk = remit(store, "2026-03-01", [cl("CB0002004C018", [ln("17311", 1078.33, 0, None, [("CO", "50", 1078.33)], lcn="1")])])[0]
    remit(store, "2026-04-18", [cl("CB0002004C017", [neg(paid)], status="22")])
    assert ar.same_payer_paid_before(store.conn, [item(store, lk, "DENIED_MEDICAL_NECESSITY", expected=73500)]) == []


def test_a_restated_claim_nets_its_lines_to_what_it_paid(store):
    """The money is the claim's: a claim that lists $175.54 on two lines and pays $5.35 (claim-level OA-23 $170.19) leaves
    each line its share of the $5.35; a claim whose lines add up to its payment keeps them as they are."""
    keys = remit(store, "2026-04-01", [cl("CB0002005C017", [ln("11102", 100.00, 81.08, 81.08, lcn="1"), ln("11103", 100.00, 94.46, 94.46, lcn="2")],
                                          claim_paid=5.35, adj=[("OA", "23", 170.19)])])
    plain = remit(store, "2026-04-02", [cl("CB0002006C017", [ln("11102", 100.00, 81.08, 81.08, lcn="1")])])
    arch = ar.load_archive(store.conn, [item(store, keys[0], "X"), item(store, plain[0], "X")])
    a, b, c = arch.lines[keys[0]], arch.lines[keys[1]], arch.lines[plain[0]]
    assert a.restated and b.restated and not c.restated
    assert (a.standing + b.standing, c.standing) == (535, 8108)
    assert abs(a.standing - round(8108 * 535 / 17554)) <= 1


# ---- one company's two plans (review money, medium)
BIG_MA = {"payer": "BIGCO HEALTH", "payer_id": "BIG01"}


def test_rule2_a_medicaid_plans_denial_after_the_same_companys_advantage_plan_paid_is_not_closed(store):
    """The company's Medicare Advantage plan paid 11103 and left $16.54 coinsurance; its Medicaid plan denied the line the
    same day (CO-16/N257). Two plans: not 'paid the same day on another remittance', but another payer paid first, and
    the Medicaid plan's denial is about the $16.54 the Advantage plan left (never the member's)."""
    remit(store, "2026-05-04", [cl("CB0002101C017", [ln("11103", 87.08, 64.86, 82.72, [("CO", "45", 4.36), ("CO", "253", 1.32), ("PR", "2", 16.54)],
                                                        lcn="1")], cfi="16")], trace="TRCMA1", **BIG_MA)
    den = remit(store, "2026-05-04", [cl("CB0002101C017", [ln("11103", 87.08, 0, None, [("CO", "16", 87.08)], lcn="1", rem=["N257"])], cfi="MC")],
                trace="TRCMC1", **BIG_MA)
    f = item(store, den[0], "DENIED_MISSING_INFO", expected=8272)
    acts = [a for a in ar.evaluate(store.conn, [f], hide_rules=[])["actions"] if a.get("finding_id") == f["finding_id"]]
    assert not any(x["action"] == "close_with_proof" for x in acts), acts
    a = only(acts)
    assert (a["reclass"], a["shortfall_cents"], a["state_hint"]) == ("other_payer_paid", 1654, "work_now")


def test_rule1_a_medicaid_plans_later_denial_is_not_paid_before_by_the_same_payer(store):
    remit(store, "2026-05-04", [cl("CB0002102C017", [ln("99213", 85.14, 44.96, 80.88, [("CO", "45", 4.26), ("CO", "253", 0.92), ("PR", "3", 35.00)],
                                                        lcn="1")], cfi="16")], **BIG_MA)
    den = remit(store, "2026-05-29", [cl("CB0002102C017", [ln("99213", 85.14, 0, None, [("CO", "299", 85.14)], lcn="1", rem=["N767"])],
                                         cfi="MC")], **BIG_MA)
    f = item(store, den[0], "DENIED_NONCOVERED", expected=8514)
    acts = [a for a in ar.evaluate(store.conn, [f], hide_rules=[])["actions"] if a.get("finding_id") == f["finding_id"]]
    assert not any(x.get("reclass") == "paid_before" for x in acts), acts
    assert only(acts)["shortfall_cents"] == 3500           # the copay the Advantage plan left is the Medicaid plan's to pay


# ---- rule 5: lines the practice dropped or recoded (review money medium; review regression medium)
def test_rule5_a_claim_reanswered_without_the_line_is_not_waiting_on_the_payer(store):
    """The payer reversed the claim and answered the claim id again with the Mohs line only, its charge lower by exactly
    the visit's charge: the practice resubmitted without the visit. Dropped, done; not 'waiting on the payer'."""
    em = ln("99204", 269.12, 0, None, [("CO", "97", 269.12)], lcn="1", mods=("57",))
    mohs = ln("17311", 1078.33, 625.00, 660.00, [("CO", "45", 418.33), ("PR", "3", 35.00)], lcn="2")
    first = remit(store, "2026-04-01", [cl("CB0002201C017", [em, mohs], icn="PCN-A")])
    remit(store, "2026-06-24", [cl("CB0002201C017", [neg(em), neg(mohs)], icn="PCN-A", status="22"),
                                cl("CB0002201C017", [ln("17311", 1078.33, 812.49, 847.49, [("CO", "45", 230.84), ("PR", "3", 35.00)], lcn="1")],
                                   icn="PCN-B")])
    a = only(ar.same_day_answers(store.conn, [item(store, first[0], "EM_BUNDLED_MOD57_MINOR", "bundling", expected=12177)]))
    assert (a["reclass"], a["state_hint"], a["shortfall_cents"]) == ("dropped_on_correction", "done", 0)


def test_rule5_a_reanswer_that_drops_the_second_line_of_a_code_is_not_a_unit_to_work(store):
    l1 = ln("77401", 63.88, 0, None, [("CO", "222", 63.88)], lcn="1", rem=["N640"])
    l2 = ln("77401", 63.88, 0, None, [("CO", "222", 63.88)], lcn="2", mods=("59",), rem=["N640"])
    other = ln("77280", 238.79, 186.00, 186.00, [("CO", "45", 52.79)], lcn="3")
    first = remit(store, "2026-04-01", [cl("CB0002202C017", [l1, l2, other], icn="PCN-C")])
    remit(store, "2026-05-20", [cl("CB0002202C017", [ln("77401", 63.88, 21.37, 21.37, [("CO", "45", 42.51)], lcn="1"), other], icn="PCN-C"),
                                cl("CB0002202C017", [neg(l1), neg(l2), neg(other)], icn="PCN-C", status="22")])
    a = only(ar.same_day_answers(store.conn, [item(store, first[1], "DENIED_FREQUENCY", expected=2137)]))
    assert (a["reclass"], a["state_hint"], a["shortfall_cents"]) == ("dropped_on_correction", "done", 0)


def test_rule5_a_line_the_practices_adjustment_request_dropped_is_done(store):
    """Two fractions of 77401 denied over the daily maximum (CO-222/N640). The practice's adjustment request, which the payer
    answers CO-129/N770 with no frequency code, bills one fraction; the payer reverses both denials and pays that one.
    The second fraction was dropped by the practice: done, not 'the payer paid the other line, work this one'."""
    first = ln("77401", 63.88, 0, None, [("CO", "222", 63.88)], lcn="14", rem=["N640"])
    second = ln("77401", 63.88, 0, None, [("CO", "222", 63.88)], mods=("59",), lcn="13", rem=["N640"])
    lks = remit(store, "2026-03-20", [cl("CB0002203C017", [first, second], freq=None)])
    remit(store, "2026-05-10", [cl("CB0002203C017", [ln("77401", 63.88, 0, None, [("CO", "129", 63.88)], lcn="14", units=0, billed=1,
                                                        rem=["N770"])], status="4", freq=None)])
    remit(store, "2026-05-17", [cl("CB0002203C017", [neg(first), neg(second)], status="22", freq=None),
                                cl("CB0002203C017", [ln("77401", 63.88, 21.37, 21.37, [("CO", "45", 42.51)], lcn="14")], freq=None)])
    a = only(ar.same_day_answers(store.conn, [item(store, lks[1], "DENIED_FREQUENCY", expected=2137)]))
    assert (a["reclass"], a["state_hint"], a["shortfall_cents"]) == ("dropped_on_correction", "done", 0)


def test_rule5_an_adjustment_request_marks_the_corrected_claim_without_a_reversal(store):
    """The same practice correction where the payer sends no reversal: after its CO-129/N770 note, its answer of the claim
    id bills only the first fraction. The second fraction was dropped on the corrected claim (5f)."""
    lks = remit(store, "2026-03-20", [cl("CB0002204C017", [ln("77401", 63.88, 0, None, [("CO", "222", 63.88)], lcn="14"),
                                                           ln("77401", 63.88, 0, None, [("CO", "222", 63.88)], mods=("59",), lcn="13")],
                                         freq=None)])
    remit(store, "2026-05-10", [cl("CB0002204C017", [ln("77401", 63.88, 0, None, [("CO", "129", 63.88)], lcn="14", rem=["N770"])],
                                   status="4", freq=None)])
    remit(store, "2026-05-17", [cl("CB0002204C017", [ln("77401", 63.88, 21.37, 21.37, [("CO", "45", 42.51)], lcn="14")], freq=None)])
    a = only(ar.same_day_answers(store.conn, [item(store, lks[1], "DENIED_FREQUENCY", expected=2137)]))
    assert (a["reclass"], a["state_hint"]) == ("dropped_on_correction", "done")


def test_rule5_a_line_billed_again_under_another_code_is_done(store):
    """The payer reversed the claim and answered it again: the visit is billed 99213 now, not 99203, and paid. The 99203
    line was billed again under the new code: done, with the new code's answer as proof."""
    denial = ln("99203", 180.00, 0, None, [("CO", "151", 180.00)], mods=("25",), lcn="1")
    proc = ln("11102", 120.00, 90.00, 90.00, [("CO", "45", 30.00)], lcn="2")
    lk = remit(store, "2026-03-20", [cl("CB0002205C017", [denial, proc])])[0]
    remit(store, "2026-05-01", [cl("CB0002205C017", [neg(denial), neg(proc)], status="22"),
                                cl("CB0002205C017", [ln("99213", 140.00, 70.00, 70.00, [("CO", "45", 70.00)], mods=("25",), lcn="1"), proc])])
    a = only(ar.same_day_answers(store.conn, [item(store, lk, "DENIED_FREQUENCY", expected=11000)]))
    assert (a["reclass"], a["state_hint"], a["shortfall_cents"]) == ("recoded", "done", 0)
    assert "99213" in a["hint"] and any(p["code"] == "99213" and p["decisive"] for p in a["proof"])


def test_rule5_a_reversal_with_no_answer_of_the_claim_id_still_waits(store):
    """No answer of the claim id since the reversal: the payer is reprocessing, waiting (the fate check needs an answer)."""
    denial = ln("17110", 177.56, 0, None, [("CO", "16", 177.56)], lcn="1")
    lk = remit(store, "2026-03-20", [cl("CB0002206C017", [denial])])[0]
    remit(store, "2026-04-20", [cl("CB0002206C017", [neg(denial)], status="22")])
    a = only(ar.same_day_answers(store.conn, [item(store, lk, "DENIED_MISSING_INFO", expected=10000)]))
    assert (a["reclass"], a["state_hint"]) == ("reversed_no_answer", "waiting")


# ---- rule 5: a later answer onto the deductible, no reversal (review money, medium)
def test_rule5_a_later_answer_that_moves_the_line_to_the_deductible_settles_the_denial(store):
    den = remit(store, "2026-04-01", [cl("CB0002301C017", [ln("97597", 87.94, 0, None, [("CO", "16", 87.94)], lcn="1", rem=["MA130"])], icn="PCN-D")])
    remit(store, "2026-04-08", [cl("CB0002301C017", [ln("97597", 87.94, 0, 87.94, [("PR", "1", 87.94)], lcn="1")], icn="PCN-E")])
    a = only(ar.same_day_answers(store.conn, [item(store, den[0], "DENIED_MISSING_INFO", expected=8794)]))
    assert (a["reclass"], a["state_hint"], a["shortfall_cents"], a["patient_share_cents"]) == ("moved_to_patient", "done", 0, 8794)


def test_rule5_a_later_answer_that_denies_again_is_no_settlement(store):
    den = remit(store, "2026-04-01", [cl("CB0002302C017", [ln("97597", 87.94, 0, None, [("CO", "16", 87.94)], lcn="1")])])
    remit(store, "2026-04-08", [cl("CB0002302C017", [ln("97597", 87.94, 0, 87.94, [("PR", "1", 87.94)], lcn="1")])])
    remit(store, "2026-04-20", [cl("CB0002302C017", [ln("97597", 87.94, 0, None, [("CO", "50", 87.94)], lcn="1")])])
    assert ar.same_day_answers(store.conn, [item(store, den[0], "DENIED_MISSING_INFO", expected=8794)]) == []


def test_rule3_a_line_the_payer_later_put_on_the_deductible_is_no_unit_to_work(store):
    lks = remit(store, "2026-03-20", [cl("CB0002303C017", [ln("11312", 156.00, 90.00, 110.99, [("CO", "45", 45.01), ("PR", "2", 20.99)], lcn="1"),
                                                           ln("11312", 156.00, 0, None, [("CO", "151", 156.00)], mods=("59",), lcn="2")])])
    remit(store, "2026-04-20", [cl("CB0002303C017", [ln("11312", 156.00, 0, 110.99, [("CO", "45", 45.01), ("PR", "1", 110.99)], mods=("59",),
                                                        lcn="2")])])
    f = item(store, lks[1], "DENIED_FREQUENCY", expected=11099)
    assert ar.units_split(store.conn, [f]) == []
    assert only(ar.same_day_answers(store.conn, [f]))["reclass"] == "moved_to_patient"


def test_rule5_a_reversal_onto_the_deductible_with_the_cut_coded_co16_is_done(store):
    """The payer put the whole allowed $330 on the deductible and coded the cut above it CO-16 (where CO-45 belongs): the
    line went to the patient; nothing to work."""
    denial = ln("J7345", 400.00, 0, None, [("CO", "16", 400.00)], units=200, lcn="1")
    lk = remit(store, "2026-03-20", [cl("CB0002304C017", [denial])])[0]
    remit(store, "2026-04-20", [cl("CB0002304C017", [neg(denial)], status="22"),
                                cl("CB0002304C017", [ln("J7345", 400.00, 0, 330.00, [("CO", "16", 70.00), ("PR", "1", 330.00)], units=200, lcn="1")])])
    a = only(ar.same_day_answers(store.conn, [item(store, lk, "DENIED_MISSING_INFO", expected=33000)]))
    assert (a["reclass"], a["state_hint"]) == ("moved_to_patient", "done")


# ---- rule 5: a short payment taken back with no new answer (review money, low)
def test_rule5_a_short_payment_taken_back_with_no_new_answer_is_the_whole_line(store):
    paid = ln("17000", 120.00, 52.48, 52.48, [("CO", "45", 67.52)], lcn="1")
    lk = remit(store, "2026-03-20", [cl("CB0002401C017", [paid])])[0]
    remit(store, "2026-06-20", [cl("CB0002401C017", [neg(paid)], status="22")])
    f = item(store, lk, "UNDERPAID_VS_OWN_RATE", "underpayment", expected=5604, shortfall=356)
    a = only(ar.same_day_answers(store.conn, [f]))
    assert (a["reclass"], a["state_hint"], a["shortfall_cents"], a["taken_back_cents"]) == ("reversed_no_answer", "waiting", 5604, 5248)
    assert "took this payment back" in a["hint"] and a["proof"][1]["kind"] == "reversal" and a["proof"][1]["date"] == "2026-06-20"


# ---- rule 2 (review money, lows)
def test_rule2_share_is_not_halved_when_the_denied_line_reports_no_units(store):
    remit(store, "2026-04-01", [cl("CB0002501C017", [ln("11103", 89.88, 64.85, 76.40, [("PR", "2", 15.28), ("CO", "45", 13.48)], units=2, lcn="1")],
                                   status="19", cfi="MB")], payer="STATE MEDICARE", payer_id="MCR01")
    den = remit(store, "2026-04-08", [cl("CB0002501C017", [ln("11103", 89.88, 0, None, [("OA", "A1", 89.88)], units=0, rem=["N130"])], cfi="15")],
                payer="TEST MEDIGAP CO", payer_id="GAP01")
    a = only(ar.other_payer_paid_first(store.conn, [item(store, den[0], "DENIED_MEDICAL_NECESSITY", expected=4354)]))
    assert a["shortfall_cents"] == 1528


def test_rule2_a_line_that_billed_fewer_units_gets_its_part_of_the_share(store):
    """The primary paid two lines of 11103 (one unit each, $15.28 coinsurance in all); the secondary denied one of them at
    half the charge: its part of the share is half."""
    remit(store, "2026-04-01", [cl("CB0002502C017", [ln("11103", 44.94, 32.42, 38.20, [("PR", "2", 7.64), ("CO", "45", 6.74)], lcn="1"),
                                                     ln("11103", 44.94, 32.43, 38.20, [("PR", "2", 7.64), ("CO", "45", 6.74)], lcn="2")],
                                   status="19", cfi="MB")], payer="STATE MEDICARE", payer_id="MCR01")
    den = remit(store, "2026-04-08", [cl("CB0002502C017", [ln("11103", 44.94, 0, None, [("OA", "A1", 44.94)], units=1, lcn="2")], cfi="15")],
                payer="TEST MEDIGAP CO", payer_id="GAP01")
    a = only(ar.other_payer_paid_first(store.conn, [item(store, den[0], "DENIED_MEDICAL_NECESSITY", expected=3820)]))
    assert a["shortfall_cents"] == 764


def test_rule2_not_its_to_pay_with_no_other_plan_leaves_the_share_to_the_patient(store):
    """The plan's delegated payer paid and left a $55 copay; the plan itself answered B11/N418 (not its to process). No
    other plan answered: no payer owes the copay; 'done', size 0, the share kept as information. Once another plan has
    answered the service, the share is that plan's as a secondary claim."""
    remit(store, "2026-03-20", [cl("CB0002503C017", [ln("99213", 156.66, 21.15, 76.15, [("CO", "45", 80.51), ("PR", "3", 55.00)])])], **BETA)
    lk = remit(store, "2026-04-20", [cl("CB0002503C017", [ln("99213", 156.66, 0, None, [("CO", "B11", 156.66)], rem=["N418"])])])[0]
    a = only(ar.other_payer_paid_first(store.conn, [item(store, lk, "DENIED_COB", expected=7615)]))
    assert (a["state_hint"], a["shortfall_cents"], a["patient_share_left_cents"]) == ("done", 0, 5500)
    remit(store, "2026-04-25", [cl("CB0002503C017", [ln("99213", 156.66, 0, None, [("CO", "16", 156.66)])], status="2")],
          payer="GAMMA MUTUAL", payer_id="GAMMA1")
    b = only(ar.other_payer_paid_first(store.conn, [item(store, lk, "DENIED_COB", expected=7615)]))
    assert (b["state_hint"], b["shortfall_cents"]) == ("work_now", 5500) and "GAMMA MUTUAL" in b["hint"]


def test_rule2_two_medicare_contractors_are_not_primary_and_secondary(store):
    """One Medicare contractor paid, the other denied: no secondary claim to send. The item keeps its size, 'likely', with
    the jurisdiction to check while the window to dispute the denial is open."""
    remit(store, "2026-03-20", [cl("CB0002504C017", [ln("17311", 969.13, 600.00, 750.00, [("CO", "45", 219.13), ("PR", "2", 150.00)])],
                                   cfi="MB")], payer="MEDICARE OF KENTUCKY", payer_id="MCRKY")
    lk = remit(store, "2026-04-20", [cl("CB0002504C017", [ln("17311", 969.13, 0, None, [("CO", "B12", 969.13)])], cfi="MB")],
               payer="MEDICARE OF INDIANA", payer_id="MCRIN")[0]
    a = only(ar.other_payer_paid_first(store.conn, [item(store, lk, "DENIED_NONCOVERED", expected=75000)]))
    assert (a["reclass"], a["strength"], a["shortfall_cents"], a["state_hint"]) == ("other_medicare_contractor", "likely", None, "work_now")
    assert "secondary claim" not in a["hint"] and "jurisdiction" in a["hint"]


# ---- rule 1: a B13 answered under another code (review money, low)
def test_rule1_a_b13_answered_under_another_code_is_not_the_items_payment(store):
    lk = remit(store, "2026-03-20", [cl("CB0002601C017", [ln("77401", 63.88, 0, None, [("CO", "96", 63.88)], lcn="1")])])[0]
    remit(store, "2026-04-20", [cl("CB0002601C018", [ln("77280", 222.31, 0, None, [("CO", "B13", 222.31)], orig="77401", lcn="1")])])
    assert ar.same_payer_paid_before(store.conn, [item(store, lk, "DENIED_NONCOVERED", expected=6388)]) == []
    # the payer's B13 under the item's own code still speaks for it
    lk2 = remit(store, "2026-03-20", [cl("CB0002602C017", [ln("77401", 63.88, 0, None, [("CO", "96", 63.88)], lcn="1")])])[0]
    remit(store, "2026-04-20", [cl("CB0002602C018", [ln("77401", 63.88, 0, None, [("CO", "B13", 63.88)], lcn="1")])])
    assert only(ar.same_payer_paid_before(store.conn, [item(store, lk2, "DENIED_NONCOVERED", expected=6388)]))["reclass"] == "paid_before"


# ---- folds never remove a person's work (review regression, high)
def test_rule5_never_folds_an_item_a_person_worked(store):
    """The payer reversed a CO-16 denial and denied the line again (CO-50), which has its own item. A person had already
    appealed the CO-16 item: it stays listed ('replaced', naming the new item), never folded away."""
    ensure_schema(store.conn)
    denial = ln("17110", 177.56, 0, None, [("CO", "16", 177.56)], lcn="1")
    lk1 = remit(store, "2026-03-20", [cl("CB0002701C017", [denial])])[0]
    keys = remit(store, "2026-04-20", [cl("CB0002701C017", [neg(denial)], status="22"),
                                       cl("CB0002701C017", [ln("17110", 177.56, 0, None, [("CO", "50", 177.56)], lcn="1")])])
    f1 = item(store, lk1, "DENIED_MISSING_INFO", expected=10000)
    f2 = item(store, keys[1], "DENIED_MEDICAL_NECESSITY", expected=10000)
    unworked = ar.evaluate(store.conn, [f1, f2], hide_rules=[])
    assert [(a["finding_id"], a["into"]) for a in unworked["actions"] if a["action"] == "fold_into"] == [(f1["finding_id"], f2["finding_id"])]
    store.conn.execute("INSERT INTO findings (finding_id, status, notes) VALUES (?, 'appealed', 'appeal faxed')", (f1["finding_id"],))
    res = ar.evaluate(store.conn, [f1, f2], hide_rules=[])
    assert [a for a in res["actions"] if a["action"] == "fold_into"] == []
    a = only([a for a in res["actions"] if a.get("finding_id") == f1["finding_id"]])
    assert (a["action"], a["reclass"], a["new_item"], a["shortfall_cents"]) == ("reclassify", "replaced", f2["finding_id"], None)


@pytest.mark.parametrize("kind", ["file", "compact"])
def test_wired_rule5_fold_does_not_drop_a_worked_item_from_the_worklist(tmp_path, monkeypatch, kind):
    """The same through the real Detector with the hooks wired: after a person appealed the first denial, the next run keeps
    it listed (not stale) with its status."""
    s = Store(str(tmp_path / "s.sqlite")) if kind == "file" else Store(":memory:", compact=True)
    denial = ln("17110", 177.56, 0, None, [("CO", "16", 177.56)], lcn="1")
    lk1 = remit(s, "2026-03-20", [cl("CB0002702C017", [denial])])[0]
    remit(s, "2026-04-20", [cl("CB0002702C017", [neg(denial)], status="22"),
                            cl("CB0002702C017", [ln("17110", 177.56, 0, None, [("CO", "50", 177.56)], lcn="1")])])
    Detector(s, run_id="r1", today="2026-05-01").run()                     # unwired: both denials listed
    fid = s.conn.execute("SELECT finding_id FROM findings WHERE line_key=?", (lk1,)).fetchone()[0]
    s.conn.execute("UPDATE findings SET status='appealed', notes='appeal faxed', appealed_at='2026-05-02' WHERE finding_id=?", (fid,))
    s.conn.commit()
    _wire(monkeypatch)
    Detector(s, run_id="r2", today="2026-05-03").run()
    row = s.conn.execute("SELECT status, COALESCE(stale, 0), evidence FROM findings WHERE finding_id=?", (fid,)).fetchone()
    s.close()
    assert row is not None and tuple(row[:2]) == ("appealed", 0), row
    assert json.loads(row[2])["archive_rule"]["reclass"] == "replaced"


def test_hook_fold_reads_a_persons_work_again_before_folding(store):
    """A status set while the run was going counts: hook_fold checks the findings table again."""
    ensure_schema(store.conn)
    f1, f2 = {"finding_id": "F1", "evidence": "{}", "signal": "S"}, {"finding_id": "F2", "evidence": "{}", "signal": "S"}
    det = _det(f1, f2)
    det.conn = store.conn
    det.archive_folds = [{"action": "fold_into", "rule": ar.R_FOLD, "finding_id": "F1", "into": "F2", "proof": [], "note": "n"}]
    store.conn.execute("INSERT INTO findings (finding_id, status) VALUES ('F1', 'in_progress')")
    assert ar.hook_fold(det) == {} and set(det.findings) == {"F1", "F2"}


# ---- the first decision's dispute deadline (review regression, high; practice-manager case C26)
def test_rule7_fold_keeps_the_first_decisions_dispute_deadline_on_the_listed_item(store):
    """The payer denied the line, then carried the denial unchanged through its own reprocessing under the same payer claim
    number. The dispute window runs from the first decision: the item that stays shows the earlier deadline, with the
    basis saying so. A resubmission under a new claim number keeps its own deadline."""
    lk1 = remit(store, "2025-01-21", [cl("CB0002801C017", [ln("17311", 969.13, 0, None, [("CO", "97", 969.13)], lcn="1", rem=["M144"])],
                                         icn="PAYERCLAIM01")])[0]
    lk2 = remit(store, "2025-05-21", [cl("CB0002801C017", [ln("17311", 969.13, 0, None, [("CO", "97", 969.13)], lcn="1", rem=["M144"])],
                                         icn="PAYERCLAIM01")])[0]
    f1 = item(store, lk1, "PROCEDURE_BUNDLED", "bundling", expected=57499, appeal_deadline="2026-07-21", legal_deadline="2026-08-21")
    f2 = item(store, lk2, "PROCEDURE_BUNDLED", "bundling", expected=57499, appeal_deadline="2026-11-21", legal_deadline="2026-12-21",
              deadline_basis="18 months from the remittance")
    acts = ar.fold_repeat_denials(store.conn, [f1, f2], worked=set())
    assert [(a["finding_id"], a["into"]) for a in acts] == [(f1["finding_id"], f2["finding_id"])]
    det = _det(f1, f2)
    ar.apply_actions(det, acts)
    kept = det.findings[f2["finding_id"]]
    assert (kept["appeal_deadline"], kept["legal_deadline"]) == ("2026-07-21", "2026-08-21")
    assert "first decision of 2025-01-21" in kept["deadline_basis"] and kept["deadline_basis"].startswith("18 months")
    # another claim number: a new determination with its own window
    lk3 = remit(store, "2025-01-21", [cl("CB0002802C017", [ln("17311", 969.13, 0, None, [("CO", "97", 969.13)], lcn="1")], icn="PAYERCLAIM02")])[0]
    lk4 = remit(store, "2025-05-21", [cl("CB0002802C018", [ln("17311", 969.13, 0, None, [("CO", "97", 969.13)], lcn="1")], icn="PAYERCLAIM03")])[0]
    g1 = item(store, lk3, "PROCEDURE_BUNDLED", "bundling", expected=57499, appeal_deadline="2026-07-21")
    g2 = item(store, lk4, "PROCEDURE_BUNDLED", "bundling", expected=57499, appeal_deadline="2026-11-21")
    det2 = _det(g1, g2)
    ar.apply_actions(det2, ar.fold_repeat_denials(store.conn, [g1, g2], worked=set()))
    assert det2.findings[g2["finding_id"]]["appeal_deadline"] == "2026-11-21"


# ---- a 'likely' reading changes no dollars (review regression, medium)
def test_a_likely_reading_leaves_the_items_dollars_until_a_person_confirms(store):
    """A B13 'previously paid' with no payment of the line on file is 'likely': apply_actions keeps the item's size and
    keeps the proposed one beside it for a person to confirm. A 'proven' reading sets the size and records the old one."""
    lk = remit(store, "2026-04-20", [cl("CB0002901C018", [ln("17313", 548.44, 0, None, [("CO", "B13", 548.44)], lcn="1")])])[0]
    f = item(store, lk, "DENIED_DUPLICATE_UNPAID", expected=54844)
    a = only(ar.same_payer_paid_before(store.conn, [f]))
    assert (a["strength"], a["shortfall_cents"]) == ("likely", 0)
    det = _det(f)
    ar.apply_actions(det, [a])
    got = det.findings[f["finding_id"]]
    rule = json.loads(got["evidence"])["archive_rule"]
    assert got["shortfall_cents"] == 54844 and rule["proposed_shortfall_cents"] == 0 and "size_before_cents" not in rule
    proven = dict(a, strength="proven")
    det2 = _det(f)
    ar.apply_actions(det2, [proven])
    rule2 = json.loads(det2.findings[f["finding_id"]]["evidence"])["archive_rule"]
    assert det2.findings[f["finding_id"]]["shortfall_cents"] == 0 and rule2["size_before_cents"] == 54844


def test_a_likely_closure_never_closes_an_item():
    """Orchestrator decision 4: a 'likely' done never closes an item. The item stays open with the reading as its hint."""
    f = {"finding_id": "F1", "evidence": "{}", "auto_status": None, "shortfall_cents": 500}
    det = _det(f)

    def boom(*_a, **_k):
        raise AssertionError("closed on a likely reading")
    det._mark_paid = boom
    act = {"action": "close_with_proof", "rule": ar.R_SAME_DAY, "finding_id": "F1", "strength": "likely", "proof": [], "note": "probably paid",
           "paid_date": "2026-01-01", "paid_cents": 500, "detail": {}}
    assert ar.apply_actions(det, [act]) == {"likely_close_kept_open": 1}
    got = det.findings["F1"]
    assert got["auto_status"] is None and got["hint"] == "probably paid" and got["shortfall_cents"] == 500
    assert json.loads(got["evidence"])["archive_rule"]["state_hint"] == "done"


# ---- rule 8: one listing per service, coverage after another plan, sizes (review regression mediums; money low)
def test_rule8_does_not_list_one_service_twice(store):
    """C14's shape: the payer denied the visit for missing information (an item lists it), then answered the refiled claim
    with a missing referral (PR-288). One service, one listing: the raised item carries the older one, folded into it."""
    from remitwatch.detect import _fid
    lk1 = remit(store, "2026-03-20", [cl("CB0003001C017", [ln("99214", 205.80, 0, None, [("CO", "16", 205.80)], rem=["N255"])])])[0]
    remit(store, "2026-09-20", [cl("CB0003001C017", [ln("99214", 205.80, 0, None, [("PR", "288", 205.80)], lcn="7")])])
    f1 = item(store, lk1, "DENIED_MISSING_INFO", expected=9996)
    res = ar.evaluate(store.conn, [f1], rate=lambda g, c, y: 5032, hide_rules=[])
    raised = only([a for a in res["actions"] if a["action"] == "raise_item"])
    fold = only([a for a in res["actions"] if a["action"] == "fold_into"])
    assert fold["finding_id"] == f1["finding_id"] and fold["into"] == _fid(raised["signal"], raised["line_key"])
    assert raised["evidence"]["replaces"] == [f1["finding_id"]]


def test_rule8_a_medicaid_plans_refiled_claim_does_not_list_the_service_twice(store):
    old = remit(store, "2026-01-01", [cl("CB0003002C017", [ln("99214", 205.80, 0, None, [("CO", "16", 205.80)], rem=["N255"])], cfi="MC")],
                **BIG_MA)
    remit(store, "2026-07-20", [cl("CB0003002C017", [ln("99214", 205.80, 0, None, [("PR", "288", 205.80)], lcn="1")], cfi="MC", icn="PCN-G")],
          **BIG_MA)
    f = item(store, old[0], "DENIED_MISSING_INFO", expected=9996)
    res = ar.evaluate(store.conn, [f], rate=lambda g, c, y: 5032, hide_rules=[])
    assert [a["action"] for a in res["actions"]].count("raise_item") == 1
    assert only([a for a in res["actions"] if a.get("finding_id") == f["finding_id"]])["action"] == "fold_into"


def test_rule8_a_worked_older_listing_stays_the_one_listing(store):
    """A person worked the older listing: nothing is raised beside it; it stays the listing, with the payer's new answer
    (the missing referral, who should pay, what to fix) as its reading."""
    ensure_schema(store.conn)
    lk1 = remit(store, "2026-03-20", [cl("CB0003003C017", [ln("99214", 205.80, 0, None, [("CO", "16", 205.80)])])])[0]
    remit(store, "2026-09-20", [cl("CB0003003C017", [ln("99214", 205.80, 0, None, [("PR", "288", 205.80)], lcn="7")])])
    f1 = item(store, lk1, "DENIED_MISSING_INFO", expected=9996)
    store.conn.execute("INSERT INTO findings (finding_id, status, notes) VALUES (?, 'in_progress', 'called the plan')", (f1["finding_id"],))
    res = ar.evaluate(store.conn, [f1], rate=lambda g, c, y: 5032, hide_rules=[])
    assert not [a for a in res["actions"] if a["action"] in ("raise_item", "fold_into")]
    a = only([a for a in res["actions"] if a.get("finding_id") == f1["finding_id"]])
    assert (a["action"], a["rule"], a["reclass"], a["shortfall_cents"]) == ("reclassify", ar.R_PR, "replaced", None)
    assert "referral" in a["hint"] and a["new_reason"] == "PR-288"


def test_rule8_no_coverage_item_once_another_plan_answered_the_service(store):
    """ACME said its coverage had not started (PR-26). BETA, the plan that covered the day, answered the service since (an
    authorization denial, listed): the coverage item's fix is done already."""
    remit(store, "2026-03-20", [cl("CB0003004C017", [ln("G6001", 152.98, 0, None, [("PR", "26", 152.98)], mods=("59",), rem=["N30"])])], **ACME)
    lk2 = remit(store, "2026-04-15", [cl("CB0003004C024", [ln("G6001", 152.98, 0, None, [("CO", "197", 152.98)], mods=("59",), lcn="3")])],
                **BETA)[0]
    res = ar.evaluate(store.conn, [item(store, lk2, "DENIED_AUTH", expected=10709)], rate=lambda g, c, y: 15298, hide_rules=[])
    assert [a for a in res["actions"] if a["action"] == "raise_item"] == []
    # with no other plan's answer, the coverage item is raised
    remit(store, "2026-03-20", [cl("CB0003005C017", [ln("G6001", 152.98, 0, None, [("PR", "26", 152.98)], mods=("59",))])], **ACME)
    assert [a["signal"] for a in ar.pr_group_denials(store.conn, [], rate=lambda g, c, y: 15298)
            if a["action"] == "raise_item" and a["claim_key"].startswith("TX")] == ["DENIED_COVERAGE_DATES"]


def test_rule8_size_counts_the_units_and_never_tops_the_charge(store):
    """As detect.py sizes a denied line: the usual rate times the units billed, and never above the charge."""
    remit(store, "2026-03-20", [cl("CB0003006C017", [ln("17003", 41.86, 0, None, [("PR", "204", 41.86)], units=7)])])
    a = only(ar.pr_group_denials(store.conn, [], rate=lambda g, c, y: 675))
    assert a["shortfall_cents"] == 4186 and "x 7 units billed" in a["evidence"]["basis"] and "capped" in a["evidence"]["basis"]
    remit(store, "2026-03-21", [cl("CB0003007C017", [ln("96372", 13.04, 0, None, [("PR", "204", 13.04)])])])
    b = only([x for x in ar.pr_group_denials(store.conn, [], rate=lambda g, c, y: 1326) if x["procedure_code"] == "96372"])
    assert b["shortfall_cents"] == 1304


def test_raise_item_evidence_names_the_rule_as_a_dict_like_every_other_action(store):
    remit(store, "2026-03-20", [cl("CB0003008C017", [ln("11900", 93.75, 0, None, [("PR", "204", 93.75)])])])
    a = only(ar.pr_group_denials(store.conn, [], rate=lambda g, c, y: 3767))
    assert a["evidence"]["archive_rule"] == {"rule": ar.R_PR, "action": "raise_item", "strength": "proven"}


class _RateDet:
    """The parts of a Detector _det_rate reads: its lines, usual_rate (the payer's own rate record, or a fallback with
    none), expected_allowed and billed_units."""
    def __init__(self, line, own=None, fallback=None):
        self.lines_by_claim = {line["claim_key"]: [line]}
        self.own, self.fallback = own, fallback

    def usual_rate(self, l, code):
        if self.own:
            return self.own, f"{l['payer_group']} usual {l['year']} rate for {code} ({l['provider_tier']}, n=11)", {"n": 11}
        if self.fallback:
            return self.fallback, f"Medicare {l['year']} rate for {code} (physician)", None
        return None, "no baseline", None

    def expected_allowed(self, l, code):
        cents, basis, _r = self.usual_rate(l, code)
        return cents, basis

    @staticmethod
    def billed_units(l):
        return 1


def test_rule8_a_medicaid_plan_is_sized_from_its_own_rates_only():
    """C14: the Detector's reading of the line (its tier: a nonphysician's rate) sizes the raised item; a Medicaid plan with
    no rate of its own is not sized from Medicare's fee schedule."""
    line = {"claim_key": "C1", "line_key": "L1", "payer_group": "Bigco Health Medicaid", "payer_kind": "medicaid", "year": "2026",
            "provider_tier": "nonphysician", "pos_class": "office"}
    own = ar._det_rate(_RateDet(line, own=5032)).by_line("C1", "L1", "99214")
    assert own[0] == 5032 and "nonphysician" in own[1]
    none = ar._det_rate(_RateDet(line, fallback=11760)).by_line("C1", "L1", "99214")
    assert none[0] is None and "Medicaid plan" in none[1]
    commercial = dict(line, payer_kind="commercial", payer_group="Acme Health Plan")
    assert ar._det_rate(_RateDet(commercial, fallback=11760)).by_line("C1", "L1", "99214")[0] == 11760
    assert ar._det_rate(_RateDet(line, own=5032)).by_line("C1", "NOPE", "99214") is None


@pytest.mark.parametrize("kind", ["file", "compact"])
def test_wired_rule8_raises_one_listing_of_the_service(tmp_path, monkeypatch, kind):
    """Through the real Detector: the CO-16 item folds into the raised referral item, which lists it."""
    s = Store(str(tmp_path / "s.sqlite")) if kind == "file" else Store(":memory:", compact=True)
    remit(s, "2026-03-20", [cl("CB0003009C017", [ln("99214", 205.80, 0, None, [("CO", "16", 205.80)], rem=["N255"])])])
    remit(s, "2026-09-20", [cl("CB0003009C017", [ln("99214", 205.80, 0, None, [("PR", "288", 205.80)], lcn="7")])])
    _wire(monkeypatch)
    Detector(s, run_id="r1", today="2026-10-01").run()
    rows = [dict(zip(("signal", "stale", "evidence"), r)) for r in
            s.conn.execute("SELECT signal, COALESCE(stale, 0), evidence FROM findings WHERE patient_control_number = 'CB0003009C017'")]
    s.close()
    live = [r for r in rows if not r["stale"]]
    assert [r["signal"] for r in live] == ["DENIED_REFERRAL"], rows
    ev = json.loads(live[0]["evidence"])
    assert [x["rule"] for x in ev["folded"]] == [ar.R_PR] and ev["archive_rule"]["action"] == "raise_item"


# ---- rule 9: the SQL form and hide_match agree on a line answered under another code (review regression, low)
def test_rule9_a_code_family_is_the_answered_codes_in_both_forms(store):
    ensure_schema(store.conn)
    rows = [("R1", "Acme Health Plan", "DENIED_NONCOVERED", "70621010101", "J7345", "other"),      # answered as an NDC, billed J7345
            ("R2", "Acme Health Plan", "DENIED_NONCOVERED", "J7345", None, "drug")]
    store.conn.executemany("INSERT INTO findings (finding_id, payer_group, signal, procedure_code, original_procedure_code, code_category) "
                           "VALUES (?,?,?,?,?,?)", rows)
    rule = ar.hide_rule("Acme Health Plan", code="@drug")
    cond, params = ar.hide_sql(rule, "f")
    got = {r[0] for r in store.conn.execute(f"SELECT f.finding_id FROM findings f WHERE {cond}", params)}
    want = {r[0] for r in rows if ar.hide_match(rule, {"payer_group": r[1], "signal": r[2], "procedure_code": r[3],
                                                      "original_procedure_code": r[4], "billed_code": r[4] or r[3]})}
    assert got == want == {"R2"}


# ---- rule 10: one value per service (review regression, medium)
def test_rule10_gives_no_amount_to_a_duplicate_copys_denial(store):
    """A copy of the drug line was denied as a duplicate (OA-18); the line itself was paid, the payment taken back and the
    line denied (CO-50). The CO-50 item carries the line's value; the copy's denial does not carry it again."""
    _paid_j7345(store, 3, 1.79)
    dup = remit(store, "2026-03-05", [cl("CB0003101C017", [ln("J7345", 358.00, 0, None, [("OA", "18", 358.00)], units=200, lcn="2")])])[0]
    paid = ln("J7345", 358.00, 358.00, 358.00, units=200, lcn="2")
    remit(store, "2026-03-20", [cl("CB0003101C017", [paid])])
    keys = remit(store, "2026-09-20", [cl("CB0003101C017", [neg(paid)], status="22"),
                                       cl("CB0003101C017", [ln("J7345", 358.00, 0, None, [("CO", "50", 358.00)], units=200, lcn="LX#08")])])
    f_dup, f_den = item(store, dup, "DENIED_DUPLICATE_UNPAID"), item(store, keys[1], "DENIED_MEDICAL_NECESSITY")
    valued = {a["finding_id"] for a in ar.drug_expected(store.conn, [f_dup, f_den])}
    assert valued == {f_den["finding_id"]}


def test_rule10_gives_no_amount_to_a_wrong_payers_denial_of_a_service_another_payer_answered(store):
    _paid_j7345(store, 3, 1.79)
    wrong = remit(store, "2026-03-05", [cl("CB0003102C017", [ln("J7345", 358.00, 0, None, [("OA", "109", 358.00)], units=200)])],
                  payer="MEDICARE OF KENTUCKY", payer_id="MCRKY")[0]
    right = remit(store, "2026-03-20", [cl("CB0003102C017", [ln("J7345", 358.00, 0, None, [("CO", "16", 358.00)], units=200)])])[0]
    f_wrong, f_right = item(store, wrong, "DENIED_COB"), item(store, right, "DENIED_MISSING_INFO")
    assert {a["finding_id"] for a in ar.drug_expected(store.conn, [f_wrong, f_right])} == {f_right["finding_id"]}
    # with no other payer's answer, a 'not ours' denial is still the only listing of the service and gets its value
    alone = remit(store, "2026-03-05", [cl("CB0003103C017", [ln("J7345", 358.00, 0, None, [("OA", "109", 358.00)], units=200)])],
                  payer="MEDICARE OF KENTUCKY", payer_id="MCRKY")[0]
    assert len(ar.drug_expected(store.conn, [item(store, alone, "DENIED_COB")])) == 1


def test_rule10_values_one_listing_of_a_billed_line(store):
    """Two open denials of one billed line (CO-16, then CO-50 on the resubmission): only the newest is valued."""
    _paid_j7345(store, 3, 1.79)
    a1 = remit(store, "2026-03-05", [cl("CB0003104C017", [ln("J7345", 358.00, 0, None, [("CO", "16", 358.00)], units=200, lcn="1")])])[0]
    a2 = remit(store, "2026-04-05", [cl("CB0003104C018", [ln("J7345", 358.00, 0, None, [("CO", "50", 358.00)], units=200, lcn="1")])])[0]
    f1, f2 = item(store, a1, "DENIED_MISSING_INFO"), item(store, a2, "DENIED_MEDICAL_NECESSITY")
    assert {a["finding_id"] for a in ar.drug_expected(store.conn, [f1, f2])} == {f2["finding_id"]}


# ---- memory: the archive is released before the hooks return (review regression, medium)
def test_evaluate_and_the_drug_rule_release_the_archive(store):
    """The archive's answers and claims refer to each other; left alone they wait for the cycle collector while the
    Detector's run goes on. evaluate() and drug_expected() release them: none is alive after the call, even with the cycle
    collector off."""
    import gc
    _paid_j7345(store, 3, 1.79)
    items = []
    for i in range(4):
        lks = remit(store, "2026-03-20", [cl(f"CB00032{i:02d}C017", [ln("77401", 36.50, 25.04, 25.55, [("CO", "45", 10.95)], lcn="1"),
                                                                    ln("77401", 36.50, 0, None, [("CO", "151", 36.50)], mods=("59",), lcn="2"),
                                                                    ln("J7345", 358.00, 0, None, [("CO", "114", 358.00)], units=200, lcn="3")])])
        items += [item(store, lks[1], "DENIED_FREQUENCY", expected=3650), item(store, lks[2], "DENIED_NONCOVERED")]
    gc.collect()
    alive = lambda: sum(1 for o in gc.get_objects() if type(o) in (ar.Ans, ar.Claim))  # noqa: E731
    before = alive()
    gc.disable()
    try:
        res = ar.evaluate(store.conn, items, hide_rules=[])
        mid = alive()
        drugs = ar.drug_expected(store.conn, items)
        after = alive()
    finally:
        gc.enable()
    assert res["actions"] and drugs and (mid, after) == (before, before)
