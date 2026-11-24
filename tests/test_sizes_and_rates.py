"""What an item is worth: the payer's own rate per unit, for the units and the component billed, never above the charge
(trace findings T-C7, T-N7, T-N9, T-N4; the owner's rules on rate evidence, plans/audit_calibration_2026-11-24.md section 3
fixes 2 and 5 and section 4.1).

Sizes: an item's expected amount is the payer's own rate per unit times the item's units (SVC05, or the units billed when
SVC05 is empty and the line's adjustments account for the whole charge), at the component's rate for a 26 or TC line (the
payer's own, else its global rate split as the fee schedule splits it), and never above the charge billed; a $0.00 line with
no adjustments is no unit billed.
Rates: the payer's own (same payer group, product, setting and year), read on full-rate lines only (an amount about half of
another that holds a fifth of the lines is a multiple-procedure reduction), ties to the full amount, never another product
class's; a provider's own rate only as a recognised tier (10 payments, 60 percent at one amount, 100 percent of the payer's
rate or the PA/NP 85 percent for a PA or NP); any other provider-level discount is one contract or credentialing review per
payer and provider, with its dollars; no rate for an unlisted code; allowed is the charge less the payer's adjustments when
AMT*B6 repeats the payment beside a patient share.
T-N4: a usual rate needs a clear majority (60 percent); an amount seen on 5 lines or more with no known reason is one
routine-rate review item; no new rate item whose window has already closed.

Everything here is invented: names, member numbers, NPIs, claim ids, payer claim numbers, traces and amounts. Each test runs
on both kinds of store: the server's file and the serverless image held in memory."""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch.detect import Detector  # noqa: E402
from remitwatch.store import Store  # noqa: E402
from remitwatch.x12_837 import DDL as DDL_837  # noqa: E402
from tests.test_remitwatch import remit  # noqa: E402

DOC = "1555555551"          # an invented physician's NPI
DOC2 = "1555555552"         # another invented physician
NP = "1666666661"           # an invented nurse practitioner


@pytest.fixture(params=["file", "compact"])
def kind(request):
    return request.param


class World:
    """One test store of either kind, fed remittance files one at a time (the compact kind is the serverless image held in
    memory)."""

    def __init__(self, tmp_path, kind: str):
        self.tmp, self.n = tmp_path, 0
        self.store = Store(str(tmp_path / "s.sqlite")) if kind == "file" else Store(":memory:", compact=True)

    def remit(self, claims: list[str], pay_date: str, payer: str = "ANTHEM", payer_id: str = "ANTHM") -> None:
        for i in range(0, len(claims), 40):
            self.n += 1
            p = self.tmp / f"r{self.n:04d}.RMT"
            p.write_bytes(remit("".join(claims[i:i + 40]), "100.00", payer=payer, payer_id=payer_id, seg_count=99,
                                isa_ctrl=f"{self.n:09d}", pay_date=pay_date))
            self.store.ingest_835_paths([str(p)])

    def run(self, today: str = "2026-11-24") -> Detector:
        det = Detector(self.store, run_id=f"run{self.n}", today=today)
        det.run()
        return det

    def rows(self, where: str = "1=1") -> list[dict]:
        out = []
        for r in self.store.query(f"SELECT * FROM findings WHERE COALESCE(stale,0)=0 AND ({where})"):
            r = dict(r)
            r["ev"] = json.loads(r["evidence"]) if r.get("evidence") else {}
            out.append(r)
        return out

    def one(self, where: str) -> dict:
        got = self.rows(where)
        assert len(got) == 1, [(r["signal"], r["patient_control_number"], r["expected_cents"]) for r in got]
        return got[0]


def sv(code, charge, paid, allowed=None, units="1", cas="", dos="20260401", mods="", billed_units=""):
    """One service line: SVC05 is `units` ("" leaves it empty), SVC07 `billed_units` when given."""
    head = f"SVC*HC:{code}{':' + mods if mods else ''}*{charge}*{paid}**{units}" + (f"**{billed_units}" if billed_units else "")
    return head + f"~DTM*472*{dos}~{cas}" + (f"AMT*B6*{allowed}~" if allowed else "")


def clm(pcn, status, charge, paid, lines, *, i, npi=DOC, prov="1*DOCTOR*ONE", cfi="12", dos="20260401", pr="0.00"):
    """One claim of patient number `i` (each patient their own member number)."""
    return (f"CLP*{pcn}*{status}*{charge}*{paid}*{pr}*{cfi}*PC{i:06d}*11~NM1*QC*1*PATIENT*P{i:05d}****MI*M{i:08d}~"
            f"NM1*82*{prov}****XX*{npi}~DTM*232*{dos}~{lines}")


def paid(i, code, charge, allowed, *, mods="", units="1", npi=DOC, prov="1*DOCTOR*ONE", cfi="12", dos="20260401"):
    """A claim with one line paid in full at `allowed` (the rest a contractual adjustment)."""
    co = f"{float(charge) - float(allowed):.2f}"
    return clm(f"CB{i:07d}C017", "1", charge, allowed, sv(code, charge, allowed, allowed, units, f"CAS*CO*45*{co}~", dos, mods),
               i=i, npi=npi, prov=prov, cfi=cfi, dos=dos)


def clean(start, n, code, charge, allowed, **kw):
    return [paid(start + k, code, charge, allowed, **kw) for k in range(n)]


def denied(i, code, charge, *, carc="16", mods="", units="1", npi=DOC, cfi="12", cas=None, billed_units="", status="1"):
    """A claim whose one line was paid nothing (CO-`carc` for the whole charge unless `cas` says otherwise)."""
    cas = f"CAS*CO*{carc}*{charge}~" if cas is None else cas
    return clm(f"CB{i:07d}C017", status, charge, "0.00", sv(code, charge, "0.00", None, units, cas, mods=mods, billed_units=billed_units),
               i=i, npi=npi, cfi=cfi)


def medicare_em(start, n, npi, allowed, prov):
    """Medicare office visits that set the clinician's type (a PA or NP is paid 85 percent of a physician's amount)."""
    return clean(start, n, "99214", "223.65", allowed, npi=npi, prov=prov, cfi="MB")


# =============================================================================================== sizes
def test_a_whole_claim_denial_is_sized_by_the_units_each_line_billed(tmp_path, kind):
    """T-C7: a whole claim denied (CLP02 4) with a biopsy and four destructions on one line is worth one biopsy and four
    destructions at the payer's rate, not one unit of each."""
    w = World(tmp_path, kind)
    w.remit(clean(100, 6, "11102", "150.00", "90.00") + clean(200, 6, "17003", "20.00", "15.00"), "20260301")
    w.remit([clm("CB0000900C017", "4", "230.00", "0.00",
                 sv("11102", "150.00", "0.00", None, "1", "CAS*CO*16*150.00~") + sv("17003", "80.00", "0.00", None, "4", "CAS*CO*16*80.00~"),
                 i=900)], "20260415")
    w.run()
    item = w.one("signal='CLAIM_DENIED_UNPAID'")
    assert item["expected_cents"] == 9000 + 4 * 1500 and item["shortfall_cents"] == 15000, \
        f"the four destructions are counted as one: {item['expected_cents']}"
    w.store.close()


def test_a_line_with_no_units_on_the_remittance_is_sized_by_the_units_billed(tmp_path, kind):
    """Fix 2: SVC05 empty and the adjustments account for the whole charge: the payer answered every unit billed, and the
    837 says four. Where the adjustments leave part of the charge unexplained, the units stay unknown (one)."""
    w = World(tmp_path, kind)
    w.remit(clean(200, 6, "17003", "20.00", "15.00"), "20260301")
    w.remit([denied(901, "17003", "80.00", units=""), denied(902, "17003", "80.00", units="", cas="CAS*CO*16*40.00~")], "20260415")
    w.store.conn.executescript(DDL_837)
    for i in (901, 902):
        w.store.conn.execute("INSERT INTO lines_837 (line_837_key, claim_837_key, patient_control_number, line_seq, procedure_code, "
                             "charge_cents, units, service_date) VALUES (?, ?, ?, 1, '17003', 8000, 4, '2026-04-01')",
                             (f"k{i}", f"c{i}", f"CB{i:07d}C017"))
    w.store.conn.commit()
    w.run()
    full = w.one("patient_control_number='CB0000901C017'")
    assert full["expected_cents"] == 4 * 1500 and "x 4 units billed" in full["ev"]["basis"], (full["expected_cents"], full["ev"]["basis"])
    part = w.one("patient_control_number='CB0000902C017'")
    assert part["expected_cents"] == 1500, "units taken from the 837 though the remittance leaves part of the charge unexplained"
    w.store.close()


def test_a_component_line_is_sized_at_the_payers_component_rate(tmp_path, kind):
    """T-C7: an 88305-26 denial is worth the payer's own 26 rate, not its global one; with no TC payments of the payer's
    own, a TC denial is worth its global rate split as the fee schedule splits it (Medicare's own component payments)."""
    w = World(tmp_path, kind)
    w.remit(clean(300, 6, "88305", "190.00", "98.00") + clean(310, 6, "88305", "60.00", "40.00", mods="26"), "20260301")
    w.remit(clean(400, 6, "88305", "65.00", "65.00", cfi="MB") + clean(410, 6, "88305", "38.00", "38.00", mods="26", cfi="MB")
            + clean(420, 6, "88305", "27.00", "27.00", mods="TC", cfi="MB"), "20260301", payer="KY MEDICARE", payer_id="15102")
    w.remit([denied(500, "88305", "60.00", mods="26"), denied(501, "88305", "60.00", mods="TC")], "20260415")
    w.run()
    pc = w.one("patient_control_number='CB0000500C017'")
    assert pc["expected_cents"] == 4000, f"the 26 line is worth the global rate: {pc['expected_cents']}"
    tc = w.one("patient_control_number='CB0000501C017'")
    assert tc["expected_cents"] == round(9800 * 2700 / 6500), f"the TC line is not split as the fee schedule splits it: {tc['expected_cents']}"
    w.store.close()


def test_an_item_is_never_worth_more_than_the_charge_billed(tmp_path, kind):
    """A line billed below the payer's rate: the payer pays the lesser of the two, so the denial is worth the charge."""
    w = World(tmp_path, kind)
    w.remit(clean(300, 6, "88305", "190.00", "98.00"), "20260301")
    w.remit([denied(502, "88305", "50.00")], "20260415")
    w.run()
    item = w.one("patient_control_number='CB0000502C017'")
    assert item["expected_cents"] == 5000 and item["shortfall_cents"] == 5000, f"valued above its charge: {item['expected_cents']}"
    w.store.close()


def test_a_zero_charge_line_with_no_adjustments_is_not_a_unit_billed(tmp_path, kind):
    """T-N7: a Mohs stage denied beside a $0.00 line of the same code that carries no adjustments; the stage is paid on the
    corrected claim. One unit was billed, one was paid: the denial is closed by that payment, and the detector's own count
    of the units the claim billed (its units rule) is one, not two."""
    w = World(tmp_path, kind)
    w.remit(clean(100, 6, "17311", "900.00", "600.00"), "20260301")
    w.remit([clm("CB0000600C017", "1", "900.00", "0.00", sv("17311", "900.00", "0.00", None, "1", "CAS*CO*16*900.00~")
                 + sv("17311", "0.00", "0.00", None, "1", ""), i=600)], "20260415")
    w.remit([clm("CB0000600C024", "1", "900.00", "600.00", sv("17311", "900.00", "600.00", "600.00", "1", "CAS*CO*45*300.00~"), i=600)],
            "20260520")
    det = w.run()
    ck = next(l["claim_key"] for l in det.lines if l["patient_control_number"] == "CB0000600C017")
    assert det.units_billed(ck, "17311", "2026-04-01") == 1, "the $0.00 line with no adjustments counted as a unit billed"
    item = w.one("patient_control_number='CB0000600C017' AND signal='DENIED_MISSING_INFO'")
    assert item["status"] == "resolved" and item["recovered_cents"] == 60000, \
        f"the $0.00 line counted as a second unit billed: {item['status']}, {item['hint']}"
    w.store.close()


def test_a_never_answered_claim_prices_each_line_by_its_component_and_its_charge(tmp_path, kind):
    """T-N9: a claim EMA shows sent and no payer answered, with an 88305-26 and a visit billed below the payer's rate: the
    first is worth the payer's 26 rate, the second its charge."""
    w = World(tmp_path, kind)
    w.remit(clean(300, 6, "88305", "190.00", "98.00", dos="20260905") + clean(310, 6, "88305", "60.00", "40.00", mods="26", dos="20260905")
            + clean(320, 25, "99214", "223.65", "117.60", dos="20260905"), "20261110")
    c = w.store.conn
    for fp, cpt, mods, cents in (("a", "88305", '["26"]', 6000), ("b", "99214", None, 9000)):
        c.execute("""INSERT INTO ema_rows (row_fingerprint, family, source_name, row_index, claim_id, activity_type, bill_item_id, cpt,
                     modifiers, units, service_date, bill_created, charges_cents, insurance_payments_cents, payer, payer_id, patient_id,
                     provider) VALUES (?, 'charge_lines', 'Encounter & Charge Lines.xlsx', 0, 'CB0000950C017', 'Charge', ?, ?, ?, 1,
                     '2026-09-14', '2026-09-15', ?, 0, 'Anthem Blue Cross', '00660', 'P00950', 'Provider, Test')""",
                  (fp, fp, cpt, mods, cents))
    c.commit()
    w.run(today="2026-11-11")
    item = w.one("signal='NO_ANSWER'")
    assert item["expected_cents"] == 4000 + 9000, f"priced by code alone, above a line's charge: {item['expected_cents']}"
    w.store.close()


# =============================================================================================== rates
def test_the_payers_rate_is_read_on_full_rate_lines(tmp_path, kind):
    """Fix 5: the payer paid a Mohs stage $600 on 8 claims and half that on 12 (each the second procedure of a day billed on
    a claim of its own). The half amount is a multiple-procedure reduction, not the rate: a denied stage is worth $600."""
    w = World(tmp_path, kind)
    w.remit(clean(100, 8, "17311", "900.00", "600.00") + clean(200, 12, "17311", "900.00", "300.00"), "20260301")
    w.remit([denied(700, "17311", "900.00")], "20260415")
    w.run()
    item = w.one("patient_control_number='CB0000700C017'")
    assert item["expected_cents"] == 60000, f"sized at the multiple-procedure half: {item['expected_cents']}"
    w.store.close()


def test_no_rate_is_taken_from_another_product_class(tmp_path, kind):
    """Fix 5: a commercial plan with no rate of its own for a biopsy is not sized at Medicare's, nor at a typical amount
    most of whose lines are Medicare's; another commercial plan's typical amount is the nearest evidence. A Medicaid plan
    with none is sized from no other class either."""
    w = World(tmp_path, kind)
    w.remit(medicare_em(1, 25, DOC, "127.80", "1*DOCTOR*ONE") + clean(100, 25, "11102", "150.00", "87.35", cfi="MB"), "20260301",
            payer="KY MEDICARE", payer_id="15102")
    w.remit(clean(200, 12, "11102", "150.00", "112.74"), "20260301")
    w.remit(clean(300, 12, "11102", "150.00", "101.00"), "20260301", payer="CIGNA", payer_id="62308")
    w.remit([denied(800, "11102", "150.00")], "20260415", payer="AETNA", payer_id="60054")
    w.remit([denied(801, "11102", "150.00", cfi="MC")], "20260415", payer="HUMANA", payer_id="61101")
    w.run()
    aetna = w.one("patient_control_number='CB0000800C017'")
    assert aetna["expected_cents"] in (10100, 11274, (10100 + 11274) // 2) and "Medicare" not in aetna["ev"]["basis"], \
        (aetna["expected_cents"], aetna["ev"]["basis"])
    medicaid = w.one("patient_control_number='CB0000801C017'")
    assert not medicaid["expected_cents"] or "Medicare" not in medicaid["ev"]["basis"], (medicaid["expected_cents"], medicaid["ev"]["basis"])
    w.store.close()


def test_a_pa_or_np_is_sized_at_the_pa_np_rate_where_the_payer_pays_it(tmp_path, kind):
    """Section 4.1: Medicare pays a PA or NP 85 percent of the fee schedule. An NP's denied service of a code Medicare has
    paid only to physicians is worth 85 percent of that amount, not 100."""
    w = World(tmp_path, kind)
    w.remit(medicare_em(1, 25, DOC, "127.80", "1*DOCTOR*ONE") + medicare_em(40, 25, NP, "108.63", "1*NURSE*PRAC")
            + clean(100, 10, "96904", "75.00", "58.80", cfi="MB"), "20260301", payer="KY MEDICARE", payer_id="15102")
    w.remit([denied(900, "96904", "75.00", npi=NP, cfi="MB")], "20260415", payer="KY MEDICARE", payer_id="15102")
    w.run()
    item = w.one("patient_control_number='CB0000900C017'")
    assert item["expected_cents"] == round(5880 * 0.85), f"the NP's service sized at the physician rate: {item['expected_cents']}"
    w.store.close()


def test_a_physicians_service_is_sized_at_the_full_rate_where_pas_and_nps_bill_most_of_a_code(tmp_path, kind):
    """A guard on the payer's full rate: a payer pays physicians $75.16 for a visit and its NPs $63.89 (85 percent), and the
    NPs bill most of its visits. A physician's denied visit is worth $75.16, an NP's $63.89."""
    w = World(tmp_path, kind)
    w.remit(medicare_em(1, 25, DOC, "127.80", "1*DOCTOR*ONE") + medicare_em(40, 25, NP, "108.63", "1*NURSE*PRAC"),
            "20260301", payer="KY MEDICARE", payer_id="15102")
    w.remit(clean(100, 8, "99213", "156.66", "75.16") + clean(200, 30, "99213", "156.66", "63.89", npi=NP, prov="1*NURSE*PRAC"), "20260415")
    w.remit([denied(900, "99213", "156.66"), denied(901, "99213", "156.66", npi=NP)], "20260520")
    w.run()
    assert w.one("patient_control_number='CB0000900C017'")["expected_cents"] == 7516, "a physician's visit sized at the NPs' rate"
    assert w.one("patient_control_number='CB0000901C017'")["expected_cents"] == 6389
    w.store.close()


def test_a_payer_priced_on_medicares_fee_schedule_is_sized_at_medicares_rate(tmp_path, kind):
    """A guard on the product classes: the VA's community care pays Medicare's rate. A claim EMA shows sent to it and never
    answered (the VA sends few remittances) is worth Medicare's amount for its codes, not nothing."""
    w = World(tmp_path, kind)
    w.remit(clean(100, 12, "99214", "223.65", "127.80", cfi="MB", dos="20260905"), "20261110", payer="KY MEDICARE", payer_id="15102")
    w.remit(clean(200, 25, "99214", "223.65", "117.60", dos="20260905"), "20261110")
    w.store.conn.execute("""INSERT INTO ema_rows (row_fingerprint, family, source_name, row_index, claim_id, activity_type, bill_item_id, cpt,
                            units, service_date, bill_created, charges_cents, insurance_payments_cents, payer, payer_id, patient_id,
                            provider) VALUES ('v1', 'charge_lines', 'Encounter & Charge Lines.xlsx', 0, 'CB0000960C017', 'Charge', 'v1',
                            '99214', 1, '2026-09-14', '2026-09-15', 22365, 0, 'Optum VA CCN', 'VACCN', 'P00960', 'Provider, Test')""")
    w.store.conn.commit()
    w.run(today="2026-11-11")
    item = w.one("patient_control_number='CB0000960C017'")
    assert item["payer_group"] == "VA Community Care" and item["expected_cents"] == 12780, (item["payer_group"], item["expected_cents"])
    w.store.close()


def test_a_provider_level_discount_is_one_contract_review_not_an_appeal_per_line(tmp_path, kind):
    """Section 4.1: the payer pays a biopsy $112.74 to one physician (25 claims) and $90.99 to another on all 12 of his:
    80 percent, no recognised tier. It is listed once, for the payer and that physician, with its dollars; his lines are
    not one-off appeals and not absorbed as his rate."""
    w = World(tmp_path, kind)
    w.remit(medicare_em(1, 25, DOC, "127.80", "1*DOCTOR*ONE") + medicare_em(40, 25, DOC2, "127.80", "1*DOCTOR*TWO"),
            "20260301", payer="KY MEDICARE", payer_id="15102")
    w.remit(clean(100, 25, "11102", "150.00", "112.74") + clean(200, 12, "11102", "150.00", "90.99", npi=DOC2, prov="1*DOCTOR*TWO"),
            "20260601")
    w.run()
    assert not w.rows("signal='UNDERPAID_VS_OWN_RATE'"), "the second physician's discount is twelve one-off appeals"
    gap = w.one("signal='PROVIDER_RATE_GAP'")
    assert gap["category"] == "contract" and gap["rendering_provider_npi"] == DOC2 and gap["shortfall_cents"] == 12 * (11274 - 9099)
    assert gap["ev"]["lines"] == 12 and gap["ev"]["usual_cents"] == 11274 and gap["ev"]["amount_cents"] == 9099
    w.store.close()


def test_a_clinician_paid_in_full_is_short_when_paid_85_percent(tmp_path, kind):
    """Section 4.1: paid 85 percent for a clinician whose own payments sit at 100 percent is short, not the PA/NP rate: six
    such visits are one routine-rate review item with their dollars, with no PA/NP reason."""
    w = World(tmp_path, kind)
    w.remit(medicare_em(1, 25, DOC, "127.80", "1*DOCTOR*ONE"), "20260301", payer="KY MEDICARE", payer_id="15102")
    w.remit(clean(100, 25, "99214", "223.65", "117.60") + clean(200, 6, "99214", "223.65", "100.00"), "20260601")
    w.run()
    item = w.one("signal='SECOND_RATE'")
    assert item["ev"]["lines"] == 6 and item["shortfall_cents"] == 6 * 1760 and "nurse practitioner" not in item["ev"]["note"]
    assert not w.rows("signal='UNDERPAID_VS_OWN_RATE'")
    w.store.close()


def test_an_unlisted_code_has_no_rate(tmp_path, kind):
    """Fix 5: an unclassified drug (J3490) can be any drug: what the payer allowed on other J3490 lines is no rate for this
    one (only the payer's own price for this very line could size it, archive rule 10); an unlisted procedure (17999) with no
    rate is worth its charge at most."""
    w = World(tmp_path, kind)
    w.remit(clean(100, 8, "J3490", "40.00", "25.00") + clean(200, 8, "17999", "400.00", "250.00"), "20260301")
    w.remit([denied(700, "J3490", "300.00"), denied(701, "17999", "300.00")], "20260415")
    w.run()
    item = w.one("patient_control_number='CB0000700C017'")
    assert item["expected_cents"] != 2500 and "usual" not in (item["ev"].get("basis") or ""), (item["expected_cents"], item["ev"].get("basis"))
    unlisted = w.one("patient_control_number='CB0000701C017'")
    assert unlisted["expected_cents"] == 30000, "an unlisted code's denial is sized at the amounts allowed for other claims of it"
    w.store.close()


def test_allowed_is_the_charge_less_the_payers_adjustments_when_b6_repeats_the_payment(tmp_path, kind):
    """Fix 5: the payer reports AMT*B6 equal to what it paid though the patient owes a coinsurance: the allowed amount is
    the charge less the payer's own adjustments, and the line is paid at the usual rate."""
    w = World(tmp_path, kind)
    w.remit(clean(100, 25, "99214", "223.65", "117.60"), "20260301")
    line = sv("99214", "223.65", "94.08", "94.08", "1", "CAS*CO*45*106.05~CAS*PR*2*23.52~")
    w.remit([clm("CB0000700C017", "1", "223.65", "94.08", line, i=700, pr="23.52")], "20260415")
    w.run()
    assert not w.rows("patient_control_number='CB0000700C017'"), \
        [(r["signal"], r["shortfall_cents"]) for r in w.rows("patient_control_number='CB0000700C017'")]
    w.store.close()


# =============================================================================================== T-N4
def test_a_usual_rate_needs_a_clear_majority(tmp_path, kind):
    """T-N4: 12 visits at $100.00 and 9 at $95.00 (57 percent): no amount is the usual rate, so a visit paid $90.00 is not
    called underpaid against it."""
    w = World(tmp_path, kind)
    w.remit(clean(100, 12, "99213", "156.66", "100.00") + clean(200, 9, "99213", "156.66", "95.00")
            + clean(300, 1, "99213", "156.66", "90.00"), "20260301")
    w.run()
    assert not w.rows("signal IN ('UNDERPAID_VS_OWN_RATE','SECOND_RATE')"), \
        [(r["signal"], r["shortfall_cents"]) for r in w.rows("signal IN ('UNDERPAID_VS_OWN_RATE','SECOND_RATE')")]
    w.store.close()


def test_a_full_rate_read_on_a_few_physicians_lines_is_backed_by_every_line_paid_at_it(tmp_path, kind):
    """T-N4's evidence: where PAs and NPs bill most of a code, the full rate is read on the physicians' lines, here only 8.
    The NPs' 30 lines paid at the same amount (99213) or at its PA/NP 85 percent (99214) back it all the same: 38 of 39
    lines. A visit paid below it for no reason is short, by the physician's or the NP's rate."""
    w = World(tmp_path, kind)
    w.remit(medicare_em(1, 25, DOC, "127.80", "1*DOCTOR*ONE") + medicare_em(40, 25, NP, "108.63", "1*NURSE*PRAC"),
            "20260301", payer="KY MEDICARE", payer_id="15102")
    w.remit(clean(100, 8, "99213", "156.66", "76.15") + clean(200, 30, "99213", "156.66", "76.15", npi=NP, prov="1*NURSE*PRAC")
            + clean(300, 8, "99214", "223.65", "75.16") + clean(400, 30, "99214", "223.65", "63.89", npi=NP, prov="1*NURSE*PRAC"),
            "20260415")
    w.remit(clean(500, 1, "99213", "156.66", "60.00", npi=NP, prov="1*NURSE*PRAC") + clean(501, 1, "99214", "223.65", "60.00")
            + clean(502, 1, "99214", "223.65", "55.00", npi=NP, prov="1*NURSE*PRAC"), "20260520")
    w.run(today="2026-06-15")
    got = {r["patient_control_number"]: r["shortfall_cents"] for r in w.rows("signal='UNDERPAID_VS_OWN_RATE'")}
    assert got == {"CB0000500C017": 7615 - 6000, "CB0000501C017": 7516 - 6000, "CB0000502C017": 6389 - 5500}, got
    w.store.close()


def test_a_pa_or_np_paid_85_percent_where_the_payers_pa_np_rate_is_unknown_is_no_one_off_appeal(tmp_path, kind):
    """Section 4.1: two NP visits paid $85.00 against a payer's $100.00, where neither the NP's own payments nor the
    payer's other lines say what it pays PAs and NPs: the PA/NP 85 percent many plans pay, not two appeals. Where the
    payer pays its PAs and NPs in full (another payer: an NP's 12 visits at $100.00), the same $85.00 is short."""
    np2 = "1666666662"      # another invented nurse practitioner
    w = World(tmp_path, kind)
    w.remit(medicare_em(1, 25, DOC, "127.80", "1*DOCTOR*ONE") + medicare_em(40, 25, NP, "108.63", "1*NURSE*PRAC")
            + medicare_em(70, 25, np2, "108.63", "1*NURSE*TWO"), "20260301", payer="KY MEDICARE", payer_id="15102")
    w.remit(clean(100, 25, "99213", "156.66", "100.00") + clean(200, 2, "99213", "156.66", "85.00", npi=NP, prov="1*NURSE*PRAC"),
            "20260415")
    w.remit(clean(300, 25, "99213", "156.66", "100.00") + clean(400, 12, "99213", "156.66", "100.00", npi=np2, prov="1*NURSE*TWO")
            + clean(500, 1, "99213", "156.66", "85.00", npi=np2, prov="1*NURSE*TWO"), "20260415", payer="AETNA", payer_id="60054")
    w.run(today="2026-06-15")
    got = {r["patient_control_number"]: r["shortfall_cents"] for r in w.rows("signal IN ('UNDERPAID_VS_OWN_RATE','SECOND_RATE')")}
    assert got == {"CB0000500C017": 1500}, got
    w.store.close()


def test_a_routine_amount_with_no_known_reason_is_one_review_item(tmp_path, kind):
    """T-N4: seven visits by the same named physician paid $105.00 against the payer's usual $117.60, with no reason the
    remittances support: one routine-rate review item with the seven claims and their dollars, not seven appeals."""
    w = World(tmp_path, kind)
    w.remit(medicare_em(1, 25, DOC, "127.80", "1*DOCTOR*ONE"), "20260301", payer="KY MEDICARE", payer_id="15102")
    w.remit(clean(100, 25, "99214", "223.65", "117.60") + clean(200, 7, "99214", "223.65", "105.00"), "20260601")
    w.run()
    assert not w.rows("signal='UNDERPAID_VS_OWN_RATE'")
    item = w.one("signal='SECOND_RATE'")
    assert item["ev"]["lines"] == 7 and item["shortfall_cents"] == 7 * 1260 and item["actionable"] == "review"
    w.store.close()


def test_no_new_rate_item_whose_window_has_already_closed(tmp_path, kind):
    """T-N4: a visit paid short on a remittance whose appeal window (Aetna, 180 days) closed before this run: no new item."""
    w = World(tmp_path, kind)
    w.remit(clean(100, 25, "99214", "223.65", "117.60", dos="20250901"), "20250915", payer="AETNA", payer_id="60054")
    w.remit(clean(300, 1, "99214", "223.65", "90.00", dos="20250901"), "20250915", payer="AETNA", payer_id="60054")
    w.remit(clean(400, 1, "99214", "223.65", "90.00", dos="20261001"), "20261015", payer="AETNA", payer_id="60054")
    w.run(today="2026-11-24")
    got = {r["patient_control_number"] for r in w.rows("signal='UNDERPAID_VS_OWN_RATE'")}
    assert "CB0000300C017" not in got, "a new item born past its window"
    w.store.close()
