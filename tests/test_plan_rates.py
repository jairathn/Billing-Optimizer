"""The payer's own rate where one payer has several (fix7c: the hand-check of the 151 underpayments the 60 percent rule
stopped listing).

Plans: a payer group can hold employer plans (the 835's group number, REF*1L) and products (its contract class, REF*CE)
that pay firm schedules of their own. Where one plan's amounts differ from its payer's other plans on two codes or more,
its lines are measured by its own rate (the 60 percent share and the backing rules apply within the plan), the payer's
rate is read on the other plans' lines, and a line of such a plan for a code the plan has no rate of its own for is not
called short against the other plans' rate. A payer that pays PAs and NPs 85 percent in one state's offices and in full
in the other's is held to the rate of the office the visit was in. A surgical site the 835 does not name (no CLP08) is
known from the 837's place of service, and a line of no known setting paid exactly the payer's facility rate was priced at
the facility.
Multiple procedures on separate claims: the payer's own rate of each procedure in its own setting ranks the day; the
highest-valued procedure paid at half (both halved, or the lower-valued one paid in full) is owed its missing half, less
what a correct ranking takes back from the others.
Rate changes by date of service: services dated on or after the payer's new rate took effect and paid on its earlier
schedule are owed the new rate, listed as one repricing request per payer and year; services dated before it are the
earlier rate, raised since.

Everything here is invented: names, member numbers, NPIs, claim ids, group numbers, plan names, payer claim numbers,
traces and amounts. Each test runs on both kinds of store: the server's file and the serverless image held in memory."""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch import story  # noqa: E402
from remitwatch.detect import Detector  # noqa: E402
from remitwatch.x12_837 import DDL as DDL_837  # noqa: E402
from tests.test_sizes_and_rates import DOC, NP, World, sv  # noqa: E402

DOC2 = "1555555553"         # another invented physician
NP2 = "1666666663"          # another invented nurse practitioner
DR, NR, NR2 = "1*DOCTOR*ONE", "1*NURSE*PRAC", "1*NURSE*TWO"


@pytest.fixture(params=["file", "compact"])
def kind(request):
    return request.param


def claim(i: int, lines: str, charge: str, paid: str, *, pat: int | None = None, npi: str = DOC, prov: str = DR, cfi: str = "12",
          dos: str = "20260401", pos: str | None = "11", group: str | None = None, cls: str | None = None) -> str:
    """Claim number `i` of patient `pat` (each patient their own member number), with an employer plan's group number
    (REF*1L), a product (REF*CE) and the place of service (CLP08; None leaves it out, as some payers do)."""
    pat = i if pat is None else pat
    refs = (f"REF*1L*{group}~" if group else "") + (f"REF*CE*{cls}~" if cls else "")
    return (f"CLP*CB{i:07d}C017*1*{charge}*{paid}*0.00*{cfi}*PC{i:07d}" + (f"*{pos}" if pos else "") + "~"
            f"NM1*QC*1*PATIENT*P{pat:05d}****MI*M{pat:08d}~NM1*82*{prov}****XX*{npi}~{refs}DTM*232*{dos}~{lines}")


def visit(i: int, code: str, charge: str, allowed: str, *, mods: str = "", dos: str = "20260401", **kw) -> str:
    """A claim of one line paid in full at `allowed` (the rest a contractual adjustment)."""
    co = f"{float(charge) - float(allowed):.2f}"
    return claim(i, sv(code, charge, allowed, allowed, "1", f"CAS*CO*45*{co}~", dos, mods), charge, allowed, dos=dos, **kw)


def many(start: int, n: int, code: str, charge: str, allowed: str, **kw) -> list[str]:
    return [visit(start + k, code, charge, allowed, **kw) for k in range(n)]


def medicare(start: int, n: int, npi: str, allowed: str, prov: str, dos: str = "20260401") -> list[str]:
    """Medicare visits that set the clinician's type (a PA or NP is paid 85 percent of a physician's amount)."""
    return many(start, n, "99214", "223.65", allowed, npi=npi, prov=prov, cfi="MB", dos=dos)


def types(w: World, *pairs: tuple[str, str, str, int]) -> None:
    """Medicare KY visits of each (npi, allowed, prov, first claim number)."""
    claims = []
    for npi, allowed, prov, start in pairs:
        claims += medicare(start, 25, npi, allowed, prov)
    w.remit(claims, "20260301", payer="KY MEDICARE", payer_id="15102")


def by_pcn(w: World, where: str) -> dict[str, dict]:
    return {r["patient_control_number"]: r for r in w.rows(where)}


def pcn(i: int) -> str:
    return f"CB{i:07d}C017"


# =============================================================================================== plans of one payer
def test_an_employer_plan_with_a_schedule_of_its_own_is_measured_by_its_own_rate(tmp_path, kind):
    """UMR-like: most employer plans pay one network rate (a biopsy $112.74, a visit $76.15, a destruction $73.45, to
    physicians and NPs alike); one large plan pays its own schedule ($91.72 and $94.00 to physicians, 85.7 percent of
    that to NPs). Its lines at its own rates are not short and make no routine-rate review: a physician's biopsy it
    paid $80.00 is short against $91.72; a network plan's biopsy paid $100.00 is short against $112.74 (read on the other
    plans' lines, it is firm); two destructions of the large plan, which has no rate of its own for that code, are not
    called short against the network's."""
    w = World(tmp_path, kind)
    types(w, (DOC, "127.80", DR, 1), (NP, "108.63", NR, 40))
    net = []
    for k in range(6):          # six small employer plans paying the network rate
        g = f"NET{k:03d}"
        net += many(100 + 10 * k, 4, "11102", "150.00", "112.74", group=g)
        net += many(104 + 10 * k, 4, "11102", "150.00", "112.74", group=g, npi=NP, prov=NR)
        net += many(1000 + 10 * k, 5, "99213", "156.66", "76.15", group=g)
        net += many(2000 + 10 * k, 4, "17000", "90.00", "73.45", group=g)
    big = (many(300, 8, "11102", "150.00", "91.72", group="BIG001")
           + many(310, 14, "11102", "150.00", "78.62", group="BIG001", npi=NP, prov=NR)
           + many(330, 8, "99213", "156.66", "94.00", group="BIG001")
           + many(340, 14, "99213", "156.66", "80.57", group="BIG001", npi=NP, prov=NR)
           + many(360, 2, "17000", "90.00", "64.40", group="BIG001"))
    w.remit(net + big, "20260415", payer="UMR", payer_id="UMRTEST")
    w.remit([visit(900, "11102", "150.00", "80.00", group="BIG001"), visit(901, "11102", "150.00", "100.00", group="NET002")],
            "20260520", payer="UMR", payer_id="UMRTEST")
    w.run(today="2026-06-15")
    assert not w.rows("signal='SECOND_RATE'"), [(r["title"], r["ev"].get("lines")) for r in w.rows("signal='SECOND_RATE'")]
    got = {k: (r["signal"], r["expected_cents"], r["shortfall_cents"]) for k, r in by_pcn(w, "category='underpayment'").items()}
    assert got == {pcn(900): ("UNDERPAID_VS_OWN_RATE", 9172, 1172), pcn(901): ("UNDERPAID_VS_OWN_RATE", 11274, 1274)}, got
    assert "plan" in w.one(f"patient_control_number='{pcn(900)}'")["ev"]["basis"]
    w.store.close()


def test_a_plans_rate_of_another_year_is_never_the_yardstick(tmp_path, kind):
    """A plan's own schedule is read for the line's own year only: the large plan's 2026 biopsy rate ($91.72) says nothing
    about what it paid in 2025, a year with too few of its lines to read a rate (its amounts change from year to year). Its
    2025 biopsy paid $80.00 is sized by the payer's other plans but not called short; a 2026 one paid $80.00 is."""
    w = World(tmp_path, kind)
    types(w, (DOC, "127.80", DR, 1), (NP, "108.63", NR, 40))
    net = []
    for k in range(6):
        g = f"NET{k:03d}"
        net += many(100 + 10 * k, 4, "11102", "150.00", "112.74", group=g) + many(1000 + 10 * k, 5, "99213", "156.66", "76.15", group=g)
    big = (many(300, 8, "11102", "150.00", "91.72", group="BIG001")
           + many(310, 14, "11102", "150.00", "78.62", group="BIG001", npi=NP, prov=NR)
           + many(330, 8, "99213", "156.66", "94.00", group="BIG001")
           + many(340, 14, "99213", "156.66", "80.57", group="BIG001", npi=NP, prov=NR))
    w.remit(net + big, "20260415", payer="UMR", payer_id="UMRTEST")
    w.remit([visit(900, "11102", "150.00", "80.00", group="BIG001", dos="20250610"),
             visit(901, "11102", "150.00", "80.00", group="BIG001")], "20260520", payer="UMR", payer_id="UMRTEST")
    w.run(today="2026-06-15")
    got = {k: (r["signal"], r["expected_cents"], r["shortfall_cents"]) for k, r in by_pcn(w, "category='underpayment'").items()}
    assert got == {pcn(901): ("UNDERPAID_VS_OWN_RATE", 9172, 1172)}, got
    w.store.close()


def test_a_product_with_a_schedule_of_its_own_is_measured_by_its_own_rate(tmp_path, kind):
    """Anthem-like products (the 835's contract class): one product pays pathology $98.00 and a visit $60.00; another
    pays $91.31 and $76.58. Twenty reads at $91.31 are that product's rate, not a routine lower amount of the payer; one
    of its reads paid $80.00 is short against $91.31."""
    w = World(tmp_path, kind)
    types(w, (DOC, "127.80", DR, 1))
    w.remit(many(100, 40, "88305", "150.00", "98.00", cls="PRODUCT A") + many(200, 25, "99213", "156.66", "60.00", cls="PRODUCT A")
            + many(300, 20, "88305", "150.00", "91.31", cls="PRODUCT B") + many(400, 6, "99213", "156.66", "76.58", cls="PRODUCT B"),
            "20260415")
    w.remit([visit(900, "88305", "150.00", "80.00", cls="PRODUCT B")], "20260520")
    w.run(today="2026-06-15")
    assert not w.rows("signal='SECOND_RATE'"), [(r["title"], r["ev"].get("lines")) for r in w.rows("signal='SECOND_RATE'")]
    got = {k: (r["signal"], r["expected_cents"]) for k, r in by_pcn(w, "category='underpayment'").items()}
    assert got == {pcn(900): ("UNDERPAID_VS_OWN_RATE", 9131)}, got
    w.store.close()


def test_a_plan_that_pays_the_network_rate_is_not_a_schedule_of_its_own(tmp_path, kind):
    """A guard: a plan whose lines are at the network rate, one of them paid $90.00, is measured by the network rate."""
    w = World(tmp_path, kind)
    types(w, (DOC, "127.80", DR, 1))
    w.remit(many(100, 25, "11102", "150.00", "112.74", group="NET001") + many(200, 25, "99213", "156.66", "76.15", group="NET001")
            + many(300, 12, "11102", "150.00", "112.74", group="NET002") + many(400, 12, "99213", "156.66", "76.15", group="NET002"),
            "20260415", payer="UMR", payer_id="UMRTEST")
    w.remit([visit(900, "11102", "150.00", "90.00", group="NET002")], "20260520", payer="UMR", payer_id="UMRTEST")
    w.run(today="2026-06-15")
    got = {k: (r["signal"], r["expected_cents"]) for k, r in by_pcn(w, "category='underpayment'").items()}
    assert got == {pcn(900): ("UNDERPAID_VS_OWN_RATE", 11274)}, got
    w.store.close()


# =============================================================================================== the office's state
def test_a_pa_np_rate_that_follows_the_offices_state_is_read_by_the_state_of_the_visit(tmp_path, kind):
    """UnitedHealthcare-like: physicians $110.43 in both states; NPs in full in Kentucky's offices and 85 percent ($93.87)
    in Indiana's (the state of the day's Medicare carrier for the clinician). An NP's Indiana visits at $93.87 are not
    short and make no review; a Kentucky visit paid $93.87 is short against $110.43; a visit of no known state paid $93.87
    is not called short."""
    w = World(tmp_path, kind)
    ky = [f"202604{d:02d}" for d in range(1, 21)]
    ind = [f"202605{d:02d}" for d in range(1, 21)]
    med_ky = medicare(1, 25, DOC, "127.80", DR) + [visit(40 + k, "99214", "223.65", "108.63", npi=NP, prov=NR, cfi="MB", dos=d)
                                                    for k, d in enumerate(ky)]
    w.remit(med_ky, "20260601", payer="KY MEDICARE", payer_id="15102")
    w.remit([visit(70 + k, "99214", "223.65", "108.63", npi=NP, prov=NR, cfi="MB", dos=d) for k, d in enumerate(ind)],
            "20260601", payer="IN MEDICARE", payer_id="00630")
    uhc = (many(100, 20, "99214", "223.65", "110.43", dos="20260410")
           + [visit(200 + k, "99214", "223.65", "110.43", npi=NP, prov=NR, dos=d) for k, d in enumerate(ky[:15])]
           + [visit(300 + k, "99214", "223.65", "93.87", npi=NP, prov=NR, dos=d) for k, d in enumerate(ind[:12])])
    w.remit(uhc, "20260610", payer="UNITEDHEALTHCARE", payer_id="UHCTEST")
    w.remit([visit(900, "99214", "223.65", "93.87", npi=NP, prov=NR, dos=ky[7]),
             visit(901, "99214", "223.65", "93.87", npi=NP, prov=NR, dos="20260615")], "20260701", payer="UNITEDHEALTHCARE", payer_id="UHCTEST")
    w.run(today="2026-07-15")
    assert not w.rows("signal='SECOND_RATE'"), [(r["title"], r["ev"].get("lines")) for r in w.rows("signal='SECOND_RATE'")]
    got = {k: (r["signal"], r["expected_cents"]) for k, r in by_pcn(w, "category='underpayment'").items()}
    assert got == {pcn(900): ("UNDERPAID_VS_OWN_RATE", 11043)}, got
    w.store.close()


# =============================================================================================== a separate surgical site
def _site_837(w: World, rows: list[tuple[int, str]]) -> None:
    """The 837s of these claims: the place of service each was billed with."""
    w.store.conn.executescript(DDL_837)
    for i, pos in rows:
        w.store.conn.execute("INSERT INTO claims_837 (claim_837_key, patient_control_number, place_of_service, service_date) "
                             "VALUES (?, ?, ?, '2026-04-01')", (f"k837-{i}", pcn(i), pos))
    w.store.conn.commit()


def test_a_surgical_sites_schedule_is_its_own_where_the_835_does_not_name_the_site(tmp_path, kind):
    """Anthem-like: a repair is $774.00 at the office; the surgical site's claims (the 837 says place of service 24; the
    835 leaves CLP08 out) are paid $630.79, its own facility schedule. Those, and three of no known setting paid exactly
    $630.79, are not short and make no review; a site claim paid $500.00 is short against $630.79."""
    w = World(tmp_path, kind)
    types(w, (DOC, "127.80", DR, 1))
    w.remit(many(100, 60, "14040", "1100.00", "774.00") + many(200, 20, "14040", "1100.00", "630.79", pos=None)
            + many(170, 3, "14040", "1100.00", "630.79", pos=None) + [visit(900, "14040", "1100.00", "500.00", pos=None)], "20260415")
    _site_837(w, [(200 + k, "24") for k in range(20)] + [(900, "24")] + [(100 + k, "11") for k in range(5)])
    w.run(today="2026-06-15")
    assert not w.rows("signal='SECOND_RATE'"), [(r["title"], r["ev"].get("lines")) for r in w.rows("signal='SECOND_RATE'")]
    got = {k: (r["signal"], r["expected_cents"]) for k, r in by_pcn(w, "category='underpayment'").items()}
    assert got == {pcn(900): ("UNDERPAID_VS_OWN_RATE", 63079)}, got
    w.store.close()


# =============================================================================================== procedures on separate claims
def _split_days(w: World, days: list[tuple[str, str, str, str]]) -> None:
    """Humana Medicare Advantage-like: 17311 is $585.12 at the office; at the surgery center (POS 24) a 14040 is $491.64,
    a 13132 $228.96, a 14041 $589.45 (each paid alone on many claims). Then split days: the Mohs on an office claim and a
    repair on a surgery-center claim, same patient, clinician and day, each (code, office amount, repair code, repair
    amount)."""
    types(w, (DOC, "127.80", DR, 1))
    hum = dict(payer="HUMANA", payer_id="HUMTEST")
    w.remit(many(100, 25, "17311", "900.00", "585.12", cfi="16") + many(200, 6, "14040", "1500.00", "491.64", cfi="16", pos="24")
            + many(220, 6, "13132", "900.00", "228.96", cfi="16", pos="24") + many(240, 6, "14041", "1800.00", "589.45", cfi="16", pos="24"),
            "20260301", **hum)
    claims = []
    for k, (code, amount, repair, ramount) in enumerate(days):
        dos = f"202603{k + 2:02d}"
        claims.append(visit(800 + 2 * k, code, "900.00", amount, cfi="16", pat=800 + k, dos=dos))
        claims.append(visit(801 + 2 * k, repair, "1800.00", ramount, cfi="16", pat=800 + k, dos=dos, pos="24"))
    w.remit(claims, "20260410", **hum)


def test_the_higher_valued_procedure_halved_on_a_split_day_is_owed_its_half_net(tmp_path, kind):
    """Humana MA ranks a split day by processing order. Both halved (the Mohs $292.56 and the surgery center's 14040
    $245.82): the Mohs is the higher-valued ($585.12 against $491.64), $292.56 owed. Reversed (the Mohs halved, the 13132
    paid in full $228.96): $292.56 less the $114.48 a correct ranking takes back, $178.08. Where the surgery center's
    14041 ($589.45) is the higher-valued, a half Mohs is right: no item. None of them is a routine-rate review."""
    w = World(tmp_path, kind)
    _split_days(w, [("17311", "292.56", "14040", "245.82"), ("17311", "292.56", "14040", "245.82"),
                    ("17311", "292.56", "13132", "228.96"), ("17311", "292.56", "13132", "228.96"),
                    ("17311", "292.56", "14041", "589.45"), ("17311", "292.56", "14041", "589.45")])
    w.run(today="2026-06-15")
    assert not w.rows("signal='SECOND_RATE'"), [(r["title"], r["ev"].get("lines")) for r in w.rows("signal='SECOND_RATE'")]
    got = {k: (r["signal"], r["expected_cents"], r["shortfall_cents"]) for k, r in by_pcn(w, "category='underpayment'").items()}
    assert got == {pcn(800): ("UNDERPAID_VS_OWN_RATE", 58512, 29256), pcn(802): ("UNDERPAID_VS_OWN_RATE", 58512, 29256),
                   pcn(804): ("UNDERPAID_VS_OWN_RATE", 58512, 17808), pcn(806): ("UNDERPAID_VS_OWN_RATE", 58512, 17808)}, got
    f = w.one(f"patient_control_number='{pcn(804)}'")
    assert f["ev"]["highest_valued"] is True and "highest-valued" in f["title"]
    day = {d["code"]: d for d in f["ev"]["same_day"]}
    assert day["13132"]["allowed_cents"] == 22896 and day["13132"]["rate_cents"] == 22896 and day["13132"]["claim"] == pcn(805)
    assert "$178.08" in f["ev"]["note"] and "$114.48" in f["ev"]["note"]
    w.store.close()


def _sent_837(w: World, rows: list[tuple[int, int, str, str, str]]) -> None:
    """837 claims of these split days whose answers are not on file: (claim number, patient, date of service, code, place of
    service), billed by the same clinician to the same member number as the patient's 835 claims."""
    w.store.conn.executescript(DDL_837)
    for i, pat, dos, code, pos in rows:
        d = f"{dos[:4]}-{dos[4:6]}-{dos[6:]}"
        w.store.conn.execute("INSERT INTO claims_837 (claim_837_key, patient_control_number, place_of_service, service_date, "
                             "subscriber_id, rendering_npi, payer_name) VALUES (?, ?, ?, ?, ?, ?, 'HUMANA')",
                             (f"k837-{i}", pcn(i), pos, d, f"M{pat:08d}", DOC))
        w.store.conn.execute("INSERT INTO lines_837 (line_837_key, claim_837_key, patient_control_number, line_seq, procedure_code, "
                             "charge_cents, units, service_date, rendering_npi) VALUES (?, ?, ?, 1, ?, 180000, 1, ?, ?)",
                             (f"l837-{i}", f"k837-{i}", pcn(i), code, d, DOC))
    w.store.conn.commit()


def test_a_split_day_whose_other_claim_has_no_answer_on_file_is_ranked_by_the_837(tmp_path, kind):
    """The Mohs claims are answered, the surgery center's are not on file (the 837s show them sent). A Mohs halved beside a
    14040 the 837 shows billed at the surgery center was the highest-valued procedure of the day: owed its half, less what a
    correct ranking takes back from the 14040, counted as paid in full since its answer is not on file ($292.56 - $245.82 =
    $46.74, the least owed). A Mohs halved beside a 14301 (worth more at the surgery center than the Mohs at the office) was
    rightly the second procedure: no item."""
    w = World(tmp_path, kind)
    types(w, (DOC, "127.80", DR, 1))
    hum = dict(payer="HUMANA", payer_id="HUMTEST")
    w.remit(many(100, 25, "17311", "900.00", "585.12", cfi="16") + many(200, 6, "14040", "1500.00", "491.64", cfi="16", pos="24")
            + many(220, 6, "14301", "2500.00", "943.80", cfi="16", pos="24"), "20260301", **hum)
    w.remit([visit(800, "17311", "900.00", "292.56", cfi="16", pat=800, dos="20260302"),
             visit(802, "17311", "900.00", "292.56", cfi="16", pat=801, dos="20260303")], "20260410", **hum)
    _sent_837(w, [(801, 800, "20260302", "14040", "24"), (803, 801, "20260303", "14301", "24")])
    w.run(today="2026-06-15")
    got = {k: (r["signal"], r["expected_cents"], r["shortfall_cents"]) for k, r in by_pcn(w, "category='underpayment'").items()}
    assert got == {pcn(800): ("UNDERPAID_VS_OWN_RATE", 58512, 4674)}, got
    f = w.one(f"patient_control_number='{pcn(800)}'")
    assert f["ev"]["highest_valued"] is True
    day = {d["code"]: d for d in f["ev"]["same_day"]}
    assert day["14040"]["claim"] == pcn(801) and day["14040"]["rate_cents"] == 49164 and day["14040"]["allowed_cents"] is None
    assert "not on file" in f["ev"]["note"] and "$46.74" in f["ev"]["note"], f["ev"]["note"]
    w.store.close()


def test_the_payers_rate_for_a_code_paid_only_at_a_facility_is_its_facility_rate(tmp_path, kind):
    """The rate a claim with no remittance is sized by (an EMA-only denial, Detector._rate) is the office setting's; for a
    code the payer has paid only at the surgery center it is the facility rate, never none (a 14040 paid $491.64 there)."""
    w = World(tmp_path, kind)
    types(w, (DOC, "127.80", DR, 1))
    w.remit(many(200, 6, "14040", "1500.00", "491.64", cfi="16", pos="24") + many(100, 6, "17311", "900.00", "585.12", cfi="16"),
            "20260301", payer="HUMANA", payer_id="HUMTEST")
    det = Detector(w.store, run_id="r", today="2026-06-15")
    det.load()
    det.build_baselines()
    assert det._rate("Humana Medicare Advantage", "17311", "2026") == 58512
    assert det._rate("Humana Medicare Advantage", "14040", "2026") == 49164
    w.store.close()


def test_separate_claims_that_bring_more_in_total_owe_nothing(tmp_path, kind):
    """A guard: the Mohs paid in full at the office and the surgery center's repair paid in full the same day (the payer
    did not reduce either): nothing is owed on either line."""
    w = World(tmp_path, kind)
    _split_days(w, [("17311", "585.12", "14040", "491.64"), ("17311", "585.12", "13132", "228.96")])
    w.run(today="2026-06-15")
    assert not w.rows("category='underpayment'"), [(r["signal"], r["title"]) for r in w.rows("category='underpayment'")]
    w.store.close()


# =============================================================================================== rate changes by date of service
def _schedules(w: World, payer: dict, cfi: str = "16") -> None:
    """A payer's 2025 schedule (99213 $76.64, 99214 $108.19; its NPs 85 percent) and its 2026 schedule (99213 $82.36,
    99214 $117.58), the 2026 one paid on visits from the first week of January (processed from March)."""
    types(w, (DOC, "127.80", DR, 1), (NP, "108.63", NR, 40))
    w.remit(many(100, 25, "99213", "156.66", "76.64", cfi=cfi, dos="20250610") + many(130, 25, "99214", "223.65", "108.19", cfi=cfi, dos="20250610")
            + many(160, 12, "99213", "156.66", "65.14", cfi=cfi, dos="20250610", npi=NP, prov=NR), "20250701", **payer)
    w.remit(many(200, 25, "99213", "156.66", "82.36", cfi=cfi, dos="20260105") + many(230, 25, "99214", "223.65", "117.58", cfi=cfi, dos="20260105")
            + many(260, 12, "99213", "156.66", "70.01", cfi=cfi, dos="20260105", npi=NP, prov=NR), "20260310", **payer)


def test_new_year_services_paid_on_the_old_schedule_are_one_repricing_request(tmp_path, kind):
    """UnitedHealthcare MA-like: January visits processed in February on the 2025 schedule and never reprocessed: six
    99213 at $76.64, two 99214 at $108.19, two NP 99213 at $65.14 (85 percent of the 2025 amount). Each is owed the 2026
    rate: one repricing item for the payer and 2026 with the ten claims, not routine-rate reviews or ten appeals."""
    w = World(tmp_path, kind)
    payer = dict(payer="UNITEDHEALTHCARE", payer_id="UHCTEST")
    _schedules(w, payer)
    w.remit(many(900, 6, "99213", "156.66", "76.64", cfi="16", dos="20260112") + many(910, 2, "99214", "223.65", "108.19", cfi="16", dos="20260113")
            + many(920, 2, "99213", "156.66", "65.14", cfi="16", dos="20260114", npi=NP, prov=NR), "20260215", **payer)
    w.run(today="2026-06-15")
    assert not w.rows("signal IN ('SECOND_RATE','UNDERPAID_VS_OWN_RATE')"), \
        [(r["signal"], r["title"]) for r in w.rows("signal IN ('SECOND_RATE','UNDERPAID_VS_OWN_RATE')")]
    item = w.one("signal='OLD_FEE_SCHEDULE'")
    assert item["category"] == "underpayment" and item["payer_group"] == "UnitedHealthcare Medicare Advantage" and item["year"] == "2026"
    assert item["ev"]["lines"] == 10 and item["shortfall_cents"] == 6 * (8236 - 7664) + 2 * (11758 - 10819) + 2 * (7001 - 6514), item["shortfall_cents"]
    assert sorted(c[0] for c in item["ev"]["claims"]) == sorted(pcn(i) for i in list(range(900, 906)) + [910, 911, 920, 921])
    assert item["work_state"] == "work_now" and item["appeal_block"] == "batch", (item["work_state"], item["appeal_block"])
    step = story.next_step(w.store.conn, item["finding_id"], today="2026-06-15")
    assert step["route"] == "repricing" and "repricing request" in step["action"] and step["who"] == "Biller", step
    w.store.close()


def test_an_earlier_rate_raised_from_a_date_is_not_owed_before_that_date(tmp_path, kind):
    """A guard: a commercial payer paid 99213 $100.00 through June 2026 (its 2025 rate) and $110.00 on visits from July:
    the first half's visits are its earlier rate, raised since (a routine-rate review, as before), not a repricing."""
    w = World(tmp_path, kind)
    types(w, (DOC, "127.80", DR, 1))
    aet = dict(payer="AETNA", payer_id="AETTEST")
    w.remit(many(100, 25, "99213", "156.66", "100.00", dos="20250610"), "20250701", **aet)
    w.remit(many(200, 8, "99213", "156.66", "100.00", dos="20260310") + many(300, 25, "99213", "156.66", "110.00", dos="20260710"),
            "20260801", **aet)
    w.run(today="2026-09-15")
    assert not w.rows("signal IN ('OLD_FEE_SCHEDULE','UNDERPAID_VS_OWN_RATE')"), \
        [(r["signal"], r["title"]) for r in w.rows("signal IN ('OLD_FEE_SCHEDULE','UNDERPAID_VS_OWN_RATE')")]
    item = w.one("signal='SECOND_RATE'")
    assert "raised since" in item["ev"]["note"], item["ev"]["note"]
    w.store.close()


def test_the_raised_since_reason_is_dated_by_the_payers_first_visit_at_the_new_rate(tmp_path, kind):
    """T-N4's reason reads the date the payer's new rate took effect from any visit paid at it, not from one clinician's
    own: an NP's January visits on the 2025 schedule come after the payer's first visit paid at the 2026 rate, even
    though that NP's own first visit at it came later."""
    w = World(tmp_path, kind)
    payer = dict(payer="UNITEDHEALTHCARE", payer_id="UHCTEST")
    types(w, (DOC, "127.80", DR, 1), (NP, "108.63", NR, 40), (NP2, "108.63", NR2, 70))
    w.remit(many(100, 25, "99213", "156.66", "76.64", cfi="16", dos="20250610")
            + many(160, 12, "99213", "156.66", "65.14", cfi="16", dos="20250610", npi=NP2, prov=NR2), "20250701", **payer)
    w.remit(many(200, 25, "99213", "156.66", "82.36", cfi="16", dos="20260105")
            + many(260, 12, "99213", "156.66", "70.01", cfi="16", dos="20260126", npi=NP2, prov=NR2), "20260310", **payer)
    w.remit(many(920, 5, "99213", "156.66", "65.14", cfi="16", dos="20260114", npi=NP2, prov=NR2), "20260215", **payer)
    w.run(today="2026-06-15")
    assert not w.rows("signal='SECOND_RATE'"), [(r["signal"], r["ev"].get("note")) for r in w.rows("signal='SECOND_RATE'")]
    assert w.one("signal='OLD_FEE_SCHEDULE'")["ev"]["lines"] == 5
    w.store.close()
