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
          dos: str = "20260401", pos: str | None = "11", group: str | None = None, cls: str | None = None, status: str = "1",
          insured: int | None = None) -> str:
    """Claim number `i` of patient `pat` (each patient their own member number), with an employer plan's group number
    (REF*1L), a product (REF*CE) and the place of service (CLP08; None leaves it out, as some payers do). `status` is
    CLP02 (22: a reversal); `insured` names a subscriber other than the patient (NM1*IL, a family member's number)."""
    pat = i if pat is None else pat
    refs = (f"REF*1L*{group}~" if group else "") + (f"REF*CE*{cls}~" if cls else "")
    il = f"NM1*IL*1*SUBSCRIBER*S{insured:05d}****MI*M{insured:08d}~" if insured is not None else ""
    return (f"CLP*CB{i:07d}C017*{status}*{charge}*{paid}*0.00*{cfi}*PC{i:07d}" + (f"*{pos}" if pos else "") + "~"
            f"NM1*QC*1*PATIENT*P{pat:05d}****MI*M{pat:08d}~{il}NM1*82*{prov}****XX*{npi}~{refs}DTM*232*{dos}~{lines}")


def visit(i: int, code: str, charge: str, allowed: str, *, mods: str = "", dos: str = "20260401", **kw) -> str:
    """A claim of one line paid in full at `allowed` (the rest a contractual adjustment)."""
    co = f"{float(charge) - float(allowed):.2f}"
    return claim(i, sv(code, charge, allowed, allowed, "1", f"CAS*CO*45*{co}~", dos, mods), charge, allowed, dos=dos, **kw)


def many(start: int, n: int, code: str, charge: str, allowed: str, **kw) -> list[str]:
    return [visit(start + k, code, charge, allowed, **kw) for k in range(n)]


def reversal(i: int, code: str, charge: str, allowed: str, *, dos: str = "20260401", **kw) -> str:
    """The payer's reversal (CLP02 22) of claim i's payment at `allowed`."""
    co = f"{float(charge) - float(allowed):.2f}"
    line = f"SVC*HC:{code}*-{charge}*-{allowed}**-1~DTM*472*{dos}~CAS*CO*45*-{co}~"
    return claim(i, line, f"-{charge}", f"-{allowed}", dos=dos, status="22", **kw)


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


def unproven(w: World) -> list[str]:
    """Automatic closures with no proof of the payment that closed them (selfcheck.has_proof: the self-check that holds a
    run in safe mode)."""
    from remitwatch.selfcheck import has_proof
    return [r["finding_id"] for r in w.rows("status='resolved' AND auto_note IS NOT NULL") if not has_proof(r["ev"])]


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


def test_a_split_day_on_which_the_payer_cut_every_procedure_is_owed_the_least_missing_half(tmp_path, kind):
    """Humana MA paid both procedures of a split day as second ones: the Mohs $292.56 of $585.12 at the office, and the
    surgery center's 14041 $294.73 of $589.45 (a facility rate a few lines show, too few to stand as a rate of its own, so
    the 14041 is no item of its own). The 14041 is the higher-valued, so the Mohs is rightly the second, but nothing that day
    was paid as the first procedure: one of the two is owed its missing half, at the least the Mohs's $292.56. Guards:
    where the 14041 was paid in full, or above a second procedure's share (75%), the half Mohs is right (no item)."""
    w = World(tmp_path, kind)
    _split_days(w, [("17311", "292.56", "14041", "294.73"), ("17311", "292.56", "14041", "589.45"),
                    ("17311", "292.56", "14041", "442.09")])
    w.run(today="2026-06-15")
    got = {k: (r["signal"], r["expected_cents"], r["shortfall_cents"]) for k, r in by_pcn(w, "category='underpayment'").items()}
    assert got == {pcn(800): ("UNDERPAID_VS_OWN_RATE", 58512, 29256)}, got
    f = w.one(f"patient_control_number='{pcn(800)}'")
    assert f["ev"]["every_cut"] is True and "every procedure of the day" in f["title"]
    day = {d["code"]: d for d in f["ev"]["same_day"]}
    assert day["14041"]["allowed_cents"] == 29473 and day["14041"]["rate_cents"] == 58945 and day["14041"]["claim"] == pcn(801)
    assert "$292.56" in f["ev"]["note"] and "$294.73" in f["ev"]["note"] and "$294.72" in f["ev"]["note"], f["ev"]["note"]
    w.store.close()


def test_a_cut_day_names_the_year_of_a_repair_rate_from_another_year(tmp_path, kind):
    """The surgery center's 14301 has a facility rate on file only for the year before ($757.52 in 2025; 2026 facility rates
    fell): the day is still listed at the least missing half (the Mohs's $292.56, on its firm 2026 rate), and the note says
    the 14301's figure is its 2025 rate, never passing it off as this year's."""
    w = World(tmp_path, kind)
    types(w, (DOC, "127.80", DR, 1))
    hum = dict(payer="HUMANA", payer_id="HUMTEST")
    w.remit(many(200, 5, "14301", "1800.00", "757.52", cfi="16", pos="24", dos="20250601"), "20250701", **hum)
    w.remit(many(100, 25, "17311", "900.00", "585.12", cfi="16"), "20260301", **hum)
    w.remit([visit(800, "17311", "900.00", "292.56", cfi="16", pat=800, dos="20260302"),
             visit(801, "14301", "1800.00", "346.96", cfi="16", pat=800, dos="20260302", pos="24")], "20260410", **hum)
    w.run(today="2026-06-15")
    got = {k: (r["signal"], r["shortfall_cents"]) for k, r in by_pcn(w, "category='underpayment'").items()}
    assert got == {pcn(800): ("UNDERPAID_VS_OWN_RATE", 29256)}, got
    note = w.one(f"patient_control_number='{pcn(800)}'")["ev"]["note"]
    assert "2025 rate" in note and "$757.52" in note, note
    w.store.close()


def test_a_split_day_whose_higher_valued_procedure_is_an_item_of_its_own_is_listed_once(tmp_path, kind):
    """Where the payer's facility rate for the surgery center's 14041 is firm (20 lines and more), the 14041 halved beside a
    halved Mohs is the day's item (its missing half, $294.72, on its own line), and the Mohs is not listed as well."""
    w = World(tmp_path, kind)
    w.remit(many(300, 20, "14041", "1800.00", "589.45", cfi="16", pos="24"), "20260302", payer="HUMANA", payer_id="HUMTEST")
    _split_days(w, [("17311", "292.56", "14041", "294.73")])
    w.run(today="2026-06-15")
    got = {k: (r["signal"], r["expected_cents"], r["shortfall_cents"]) for k, r in by_pcn(w, "category='underpayment'").items()}
    assert got == {pcn(801): ("UNDERPAID_VS_OWN_RATE", 58945, 29472)}, got
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


def test_a_split_day_reads_the_other_claims_answer_where_the_patient_match_misses_it(tmp_path, kind):
    """The surgery center's claims are answered, but under the patient's name spelled another way (the same member number),
    so the day's patient match misses them; the 837s tie them to the day. A Mohs halved beside a 14301 the payer allowed
    $943.80 was rightly the second procedure: no item. A Mohs halved beside a 14040 it allowed $491.64 in full was the
    highest-valued: $292.56 less the $245.82 a correct ranking takes back from that payment, $46.74."""
    w = World(tmp_path, kind)
    types(w, (DOC, "127.80", DR, 1))
    hum = dict(payer="HUMANA", payer_id="HUMTEST")
    w.remit(many(100, 25, "17311", "900.00", "585.12", cfi="16") + many(200, 6, "14040", "1500.00", "491.64", cfi="16", pos="24")
            + many(220, 6, "14301", "2500.00", "943.80", cfi="16", pos="24"), "20260301", **hum)

    def other_name(c: str) -> str:
        return c.replace("NM1*QC*1*PATIENT*", "NM1*QC*1*PATIENTE*")
    w.remit([visit(810, "17311", "900.00", "292.56", cfi="16", pat=810, dos="20260302"),
             other_name(visit(811, "14301", "2500.00", "943.80", cfi="16", pat=810, dos="20260302", pos="24")),
             visit(812, "17311", "900.00", "292.56", cfi="16", pat=812, dos="20260303"),
             other_name(visit(813, "14040", "1500.00", "491.64", cfi="16", pat=812, dos="20260303", pos="24"))], "20260410", **hum)
    _sent_837(w, [(811, 810, "20260302", "14301", "24"), (813, 812, "20260303", "14040", "24")])
    w.run(today="2026-06-15")
    got = {k: (r["signal"], r["expected_cents"], r["shortfall_cents"]) for k, r in by_pcn(w, "category='underpayment'").items()}
    assert got == {pcn(812): ("UNDERPAID_VS_OWN_RATE", 58512, 4674)}, got
    f = w.one(f"patient_control_number='{pcn(812)}'")
    day = {d["code"]: d for d in f["ev"]["same_day"]}
    assert day["14040"]["allowed_cents"] == 49164 and "answer" not in day["14040"], day
    w.store.close()


def test_a_procedure_of_the_day_the_payer_denied_does_not_make_the_mohs_the_second(tmp_path, kind):
    """A guard: the surgery center's 14301 (the 837 shows it billed that day) was denied, so nothing of it was paid and it
    ranks nothing; the Mohs halved beside it is owed its whole half, as for a denied procedure on an answered claim."""
    w = World(tmp_path, kind)
    types(w, (DOC, "127.80", DR, 1))
    hum = dict(payer="HUMANA", payer_id="HUMTEST")
    w.remit(many(100, 25, "17311", "900.00", "585.12", cfi="16") + many(220, 6, "14301", "2500.00", "943.80", cfi="16", pos="24"),
            "20260301", **hum)
    denied = claim(821, sv("14301", "2500.00", "0", None, "1", "CAS*CO*197*2500.00~", "20260302"), "2500.00", "0", pat=820, cfi="16",
                   dos="20260302", pos="24")
    w.remit([visit(820, "17311", "900.00", "292.56", cfi="16", pat=820, dos="20260302"), denied], "20260410", **hum)
    _sent_837(w, [(821, 820, "20260302", "14301", "24")])
    w.run(today="2026-06-15")
    got = {k: (r["signal"], r["expected_cents"], r["shortfall_cents"]) for k, r in by_pcn(w, "signal='UNDERPAID_VS_OWN_RATE'").items()}
    assert got == {pcn(820): ("UNDERPAID_VS_OWN_RATE", 58512, 29256)}, got
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


def test_a_codes_typical_amount_across_settings_sizes_a_claim_no_setting_can(tmp_path, kind):
    """The last resort for a claim with no remittance (Detector._rate): the typical amount of the payer's product class for
    the code, read across settings when neither the office's nor the facility's lines are enough alone (6 and 6 of 10 needed,
    commercial plans with no rate of their own): never none, which would leave the claim sized at its charge."""
    w = World(tmp_path, kind)
    types(w, (DOC, "127.80", DR, 1))
    w.remit(many(100, 4, "14001", "1900.00", "700.00"), "20260301", payer="CIGNA", payer_id="CIGTEST")
    w.remit(many(110, 2, "14001", "1900.00", "700.00"), "20260301", payer="AETNA", payer_id="AETTEST")
    w.remit(many(120, 4, "14001", "1900.00", "600.00", pos="24"), "20260301", payer="UNITEDHEALTHCARE", payer_id="UHCTEST")
    w.remit(many(130, 2, "14001", "1900.00", "600.00", pos="24"), "20260301", payer="HUMANA", payer_id="HUMTEST")
    det = Detector(w.store, run_id="r", today="2026-06-15")
    det.load()
    det.build_baselines()
    assert det._rate("UMR", "14001", "2026") in (60000, 70000)
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


# =============================================================================================== the review's findings (fix7c)
# Each test below is a reviewer's failing case (review fix7c-r1 and fix7c-r2, 2026-11-26), with invented data.

# ----------------------------------------------------------------------------- the new-year rule (repricing requests)
def test_a_repriced_one_off_closes_as_recovered_as_the_base_closed_it(tmp_path, kind):
    """fix7c-r2-1. One January 2026 visit paid on the 2025 schedule ($76.64 of $82.36), then reprocessed by the payer (the
    payment reversed, CLP02 22, and paid again at $82.36). The repriced claim is recovered ($5.72): nothing stays owed
    and the recovery is counted, as it was when the line was a one-off."""
    w = World(tmp_path, kind)
    types(w, (DOC, "127.80", DR, 1))
    payer = dict(payer="UNITEDHEALTHCARE", payer_id="UHCTEST")
    w.remit(many(100, 25, "99213", "156.66", "76.64", cfi="16", dos="20250610"), "20250701", **payer)
    w.remit(many(200, 25, "99213", "156.66", "82.36", cfi="16", dos="20260105"), "20260310", **payer)
    w.remit([visit(900, "99213", "156.66", "76.64", cfi="16", dos="20260112")], "20260215", **payer)
    w.remit([reversal(900, "99213", "156.66", "76.64", cfi="16", dos="20260112"),
             visit(900, "99213", "156.66", "82.36", cfi="16", dos="20260112")], "20260501", **payer)
    w.run(today="2026-06-15")
    still_owed = [(r["signal"], r["status"], r["work_state"], r["shortfall_cents"], r["recovered_cents"])
                  for r in w.rows("category='underpayment' AND status='open'")]
    assert not still_owed, still_owed
    w.store.close()


def test_a_repriced_claim_leaves_the_repricing_request(tmp_path, kind):
    """fix7c-r2-1. Ten January 2026 visits paid on the 2025 schedule; the payer then reprocessed four of them (each payment
    reversed and paid again at $82.36). The repricing request lists the six still at the old amount; the four repriced
    claims are recovered (each closed on its own line, as a one-off is) and named on the item, not still owed. Once every
    claim is repriced the item is resolved."""
    w = World(tmp_path, kind)
    types(w, (DOC, "127.80", DR, 1))
    payer = dict(payer="UNITEDHEALTHCARE", payer_id="UHCTEST")
    w.remit(many(100, 25, "99213", "156.66", "76.64", cfi="16", dos="20250610"), "20250701", **payer)
    w.remit(many(200, 25, "99213", "156.66", "82.36", cfi="16", dos="20260105"), "20260310", **payer)
    w.remit(many(900, 10, "99213", "156.66", "76.64", cfi="16", dos="20260112"), "20260215", **payer)
    w.run(today="2026-04-15")
    fid = w.one("signal='OLD_FEE_SCHEDULE'")["finding_id"]
    w.remit([reversal(900 + k, "99213", "156.66", "76.64", cfi="16", dos="20260112") for k in range(4)]
            + many(900, 4, "99213", "156.66", "82.36", cfi="16", dos="20260112"), "20260501", **payer)
    w.run(today="2026-06-15")
    item = w.one("signal='OLD_FEE_SCHEDULE'")
    listed = sorted(c[0] for c in item["ev"]["claims"])
    assert item["finding_id"] == fid
    assert listed == sorted(pcn(i) for i in range(904, 910)) and item["shortfall_cents"] == 6 * (8236 - 7664), \
        (listed, item["shortfall_cents"], item["ev"]["lines"])
    assert sorted(c[0] for c in item["ev"]["repriced"]) == sorted(pcn(i) for i in range(900, 904)), item["ev"].get("repriced")
    # each repriced claim is closed as recovered on its own line, as a one-off is (its $5.72 counted once)
    back = {r["patient_control_number"]: (r["status"], r["recovered_cents"]) for r in w.rows("signal='UNDERPAID_VS_OWN_RATE'")}
    assert back == {pcn(i): ("resolved", 572) for i in range(900, 904)}, back
    w.remit([reversal(904 + k, "99213", "156.66", "76.64", cfi="16", dos="20260112") for k in range(6)]
            + many(904, 6, "99213", "156.66", "82.36", cfi="16", dos="20260112"), "20260620", **payer)
    w.run(today="2026-07-15")
    row = w.store.conn.execute("SELECT status, stale FROM findings WHERE finding_id=?", (fid,)).fetchone()
    assert row is not None and row["status"] == "resolved" and not row["stale"], dict(row) if row else None
    assert not w.rows("category='underpayment' AND status='open'")
    got = w.store.conn.execute("SELECT SUM(recovered_cents) FROM findings WHERE category='underpayment' AND status='resolved' "
                               "AND COALESCE(stale,0)=0").fetchone()[0]
    assert got == 10 * (8236 - 7664), got
    assert not unproven(w), unproven(w)
    w.store.close()


def test_a_repricing_requests_claims_are_counted_by_their_own_windows(tmp_path, kind):
    """fix7c-r2-2. Six January 2026 visits paid on the 2025 schedule, three processed in February (their 12-month windows
    close 2027-02-10) and three in May (closing 2027-05-20). On 2027-05-01 the first three are past their window and the
    last three close within 30 days. The deadline views count a repricing request's claims by their own windows, as they
    do a routine-rate review's (detect.pattern_windows): the gone ones as expired ($17.16), the others as due within 30 days."""
    from remitwatch import insights
    w = World(tmp_path, kind)
    types(w, (DOC, "127.80", DR, 1))
    payer = dict(payer="UNITEDHEALTHCARE", payer_id="UHCTEST")
    w.remit(many(100, 25, "99213", "156.66", "76.64", cfi="16", dos="20250610"), "20250701", **payer)
    w.remit(many(200, 25, "99213", "156.66", "82.36", cfi="16", dos="20260105"), "20260310", **payer)
    w.remit(many(900, 3, "99213", "156.66", "76.64", cfi="16", dos="20260112"), "20260210", **payer)
    w.remit(many(910, 3, "99213", "156.66", "76.64", cfi="16", dos="20260113"), "20260520", **payer)
    w.run(today="2026-06-15")
    w.run(today="2027-05-01")
    got = insights.work(w.store.conn, "2027-05-01")
    assert got["expired_cents"] >= 3 * 572 and got["total"]["due30_cents"] >= 3 * 572, \
        (got["expired_n"], got["expired_cents"], got["total"]["due30_n"], got["total"]["due30_cents"],
         [(r["signal"], r["shortfall_cents"], r["ev"].get("lines"), r["ev"].get("lines_open")) for r in w.rows("category='underpayment'")])
    w.store.close()


def test_every_deadline_view_counts_a_repricing_requests_claims_once_by_their_own_windows(tmp_path, monkeypatch):
    """fix7c-r2-2, the views: nine January visits paid on the 2025 schedule ($200.00 short each), three processed in
    February (closed by May 2027) and six in May (closing 2027-05-20). On 2027-05-01 the deadline alert, the weekly
    report's due list, the summary's deadline buckets and the worklist's 30-day filter each count the six claims due
    ($1,200.00) once, by their own windows: never the item's row as well (its last date, its every claim)."""
    from datetime import datetime, timezone
    from remitwatch import report, runtime
    from remitwatch.alerts import AlertEngine
    from tests.test_review_rates import api_client
    monkeypatch.setattr(runtime, "_utc_now", lambda: datetime(2027, 5, 1, 15, 0, tzinfo=timezone.utc))
    w = World(tmp_path, "file")
    types(w, (DOC, "127.80", DR, 1))
    payer = dict(payer="UNITEDHEALTHCARE", payer_id="UHCTEST")
    w.remit(many(100, 25, "99215", "800.00", "400.00", cfi="16", dos="20250610"), "20250701", **payer)
    w.remit(many(200, 25, "99215", "800.00", "600.00", cfi="16", dos="20260105"), "20260310", **payer)
    w.remit(many(900, 3, "99215", "800.00", "400.00", cfi="16", dos="20260112"), "20260210", **payer)
    w.remit(many(910, 6, "99215", "800.00", "400.00", cfi="16", dos="20260113"), "20260520", **payer)
    w.run(today="2027-05-01")
    item = w.one("signal='OLD_FEE_SCHEDULE'")
    assert item["shortfall_cents"] == 9 * 20000 and item["ev"]["lines_open"] == 6, (item["shortfall_cents"], item["ev"]["lines_open"])
    eng = AlertEngine(w.store.conn, run_id="a", today="2027-05-01")
    eng.deadline_rule()
    [alert] = [a for a in eng.active.values() if a["kind"] == "DEADLINE"]
    assert alert["cents_recent"] == 6 * 20000 and alert["n_recent"] == 1, (alert["cents_recent"], alert["n_recent"], alert["title"])
    text = report.build_report(w.store, detail=True)
    due = text.split("## Appeals due in the next 45 days")[1].split("##")[0]
    assert due.count("|") and "6 claims paid on the payer's earlier fee schedule" in due and "$1,200" in due, due
    assert "$1,800" not in due and due.count(report.say_what("OLD_FEE_SCHEDULE")) == 1 and "OLD_FEE_SCHEDULE" not in due, due
    w.store.close()
    c = api_client(tmp_path, monkeypatch)
    buckets = {d["bucket"]: d["shortfall_cents"] for d in c.get("/api/summary").json()["deadlines"]}
    assert buckets.get("due_30d") == 6 * 20000 and buckets.get("expired") == 3 * 20000, buckets
    month = c.get("/api/findings?category=underpayment&deadline=month").json()
    assert month["shortfall_cents"] == 6 * 20000, (month.get("total"), month.get("shortfall_cents"))


def test_a_sent_repricing_request_keeps_the_claims_it_was_sent_with(tmp_path, kind):
    """fix7c-r2-3. Ten January 2026 visits on the 2025 schedule are one repricing request; the biller sends it and marks it
    appealed. A late remittance then brings three more January visits at the old amounts. The request sent keeps the ten
    claims it listed ($57.20), as a worked routine-rate review keeps its lines; the three new claims are listed apart,
    since they were never in the request the payer has."""
    from remitwatch.actions import apply_status
    w = World(tmp_path, kind)
    types(w, (DOC, "127.80", DR, 1))
    payer = dict(payer="UNITEDHEALTHCARE", payer_id="UHCTEST")
    w.remit(many(100, 25, "99213", "156.66", "76.64", cfi="16", dos="20250610"), "20250701", **payer)
    w.remit(many(200, 25, "99213", "156.66", "82.36", cfi="16", dos="20260105"), "20260310", **payer)
    w.remit(many(900, 10, "99213", "156.66", "76.64", cfi="16", dos="20260112"), "20260215", **payer)
    w.run(today="2026-06-15")
    first = [r for r in w.rows("category='underpayment'")]
    assert len(first) == 1 and first[0]["ev"]["lines"] == 10, [(r["signal"], r["ev"].get("lines")) for r in first]
    sent = first[0]
    apply_status(w.store.conn, sent["finding_id"], "appealed", "Repricing request sent", actor="biller", at="2026-06-16T15:00:00+00:00")
    w.store.conn.commit()
    w.remit(many(950, 3, "99213", "156.66", "76.64", cfi="16", dos="20260114"), "20260620", **payer)
    w.run(today="2026-06-25")
    after = w.one(f"finding_id='{sent['finding_id']}'")
    assert after["status"] == "appealed"
    assert after["ev"]["lines"] == 10 and after["shortfall_cents"] == sent["shortfall_cents"], \
        (after["ev"]["lines"], after["shortfall_cents"], sent["shortfall_cents"])
    new = [r for r in w.rows("signal='OLD_FEE_SCHEDULE'") if r["finding_id"] != sent["finding_id"]]
    assert len(new) == 1 and new[0]["ev"]["lines"] == 3 and new[0]["shortfall_cents"] == 3 * 572, \
        [(r["ev"].get("lines"), r["shortfall_cents"]) for r in new]
    assert "not in the repricing request" in new[0]["ev"]["note"], new[0]["ev"]["note"]
    w.store.close()


def test_a_code_raised_mid_year_is_not_owed_its_new_rate_from_another_codes_new_year_date(tmp_path, kind):
    """fix7c-r2-4. A commercial payer's 2025 rates: 99213 $100.00, 99214 $150.00. In 2026 it paid 99214 at its new $160.00
    from the first week of January, but kept 99213 at $100.00 until July and paid $110.00 on visits from July 10: a
    mid-year raise of that one code. Eight 99213 visits of March paid $100.00 are the payer's earlier 99213 rate, raised
    since (the routine-rate review), not a repricing request that says the 2026 99213 rate was already in effect."""
    w = World(tmp_path, kind)
    types(w, (DOC, "127.80", DR, 1))
    aet = dict(payer="AETNA", payer_id="AETTEST")
    w.remit(many(100, 25, "99213", "156.66", "100.00", dos="20250610") + many(130, 25, "99214", "223.65", "150.00", dos="20250610"),
            "20250701", **aet)
    w.remit(many(400, 25, "99214", "223.65", "160.00", dos="20260105"), "20260301", **aet)
    w.remit(many(200, 8, "99213", "156.66", "100.00", dos="20260310"), "20260415", **aet)
    w.remit(many(300, 25, "99213", "156.66", "110.00", dos="20260710"), "20260801", **aet)
    w.run(today="2026-09-15")
    old = w.rows("signal='OLD_FEE_SCHEDULE'")
    assert not old, [(r["title"], r["shortfall_cents"], r["ev"].get("effective")) for r in old]
    item = w.one("signal='SECOND_RATE'")
    assert "raised since" in item["ev"]["note"], item["ev"]["note"]
    w.store.close()


def test_the_repricing_request_lists_every_service_paid_on_the_old_schedule(tmp_path, kind):
    """fix7c-r1-3. The payer's 2025 schedule pays a destruction 17000 $55.79, its 2026 schedule $56.42 (a small rise, 1.1
    percent). It paid the January 2026 visits on the 2025 schedule (six 99213 at $76.64) and, the same month, two
    destructions at $55.79: on the 2025 schedule too. The one repricing request lists the two 17000 claims as well ($0.63
    each more): each alone is far below what an appeal is worth, which is why they belong in the one request."""
    w = World(tmp_path, kind)
    payer = dict(payer="UNITEDHEALTHCARE", payer_id="UHCTEST")
    _schedules(w, payer)
    w.remit(many(300, 25, "17000", "120.00", "55.79", cfi="16", dos="20250610"), "20250701", **payer)
    w.remit(many(330, 25, "17000", "120.00", "56.42", cfi="16", dos="20260105"), "20260310", **payer)
    w.remit(many(900, 6, "99213", "156.66", "76.64", cfi="16", dos="20260112")
            + many(950, 2, "17000", "120.00", "55.79", cfi="16", dos="20260115"), "20260215", **payer)
    w.run(today="2026-06-15")
    item = w.one("signal='OLD_FEE_SCHEDULE'")
    assert item["ev"]["lines"] == 8 and item["shortfall_cents"] == 6 * (8236 - 7664) + 2 * (5642 - 5579), \
        (item["ev"]["lines"], item["shortfall_cents"], item["ev"]["codes"])
    w.store.close()


# ----------------------------------------------------------------------------- rates: settings, plans and states
def test_a_repair_the_payer_mostly_cuts_is_ranked_at_its_full_facility_rate(tmp_path, kind):
    """fix7c-r1-1. Humana MA-like, 2025: the Mohs 17313 is $539.37 at the office. At the surgery center the payer paid the
    repair 14021 $617.13 on the one day it ranked it first, and $308.57 (exactly half) on nine: the surgery center's repairs
    are nearly all split days, so the half is the common amount. The 14021's facility rate is $617.13. On a split day the
    payer halved both, the Mohs ($269.69) and the 14021 ($308.57): the 14021 is the higher-valued and no procedure was paid
    as the first, so the day is owed one missing half, at the least the Mohs's $269.68. Ranked at its half, the 14021 would
    look cheaper than the Mohs and 'paid in full' ($115.40 listed)."""
    w = World(tmp_path, kind)
    types(w, (DOC, "127.80", DR, 1))
    hum = dict(payer="HUMANA", payer_id="HUMTEST")
    w.remit(many(100, 25, "17313", "900.00", "539.37", cfi="16", dos="20250310")
            + many(200, 9, "14021", "1800.00", "308.57", cfi="16", pos="24", dos="20250310")
            + many(220, 1, "14021", "1800.00", "617.13", cfi="16", pos="24", dos="20250310"), "20250401", **hum)
    w.remit([visit(800, "17313", "900.00", "269.69", cfi="16", pat=800, dos="20250602"),
             visit(801, "14021", "1800.00", "308.57", cfi="16", pat=800, dos="20250602", pos="24")], "20250620", **hum)
    w.run(today="2025-09-15")
    got = {k: (r["signal"], r["shortfall_cents"], r["ev"].get("note") or "") for k, r in by_pcn(w, "category='underpayment'").items()}
    day = sum(v[1] or 0 for v in got.values())
    assert 26968 <= day <= 30856, got
    assert not any("14021 (paid in full" in v[2] for v in got.values()), got
    w.store.close()


def test_a_denial_keeps_its_size_when_the_837_moves_some_of_the_class_lines_to_the_surgery_center(tmp_path, kind):
    """fix7c-r1-2. Commercial plans paid the complex repair 13132 on ten claims ($250.00); the 835s name no place of
    service on three of them, which the 837 shows were billed from the surgery center (POS 24). A payer with no rate of its
    own for 13132 denies one at the office (CO-197): it is sized at the commercial plans' typical amount, read across
    settings when the office alone has too few lines (seven). An item on the list is never left at no amount."""
    w = World(tmp_path, kind)
    types(w, (DOC, "127.80", DR, 1))
    w.remit(many(100, 4, "13132", "700.00", "250.00", cfi="CI"), "20260310", payer="UNITEDHEALTHCARE", payer_id="UHCTEST")
    w.remit(many(110, 3, "13132", "700.00", "250.00", cfi="CI"), "20260310", payer="AETNA", payer_id="AETTEST")
    w.remit(many(120, 3, "13132", "700.00", "250.00", cfi="CI", pos=None), "20260310", payer="HUMANA", payer_id="HUMTEST")
    _site_837(w, [(120, "24"), (121, "24"), (122, "24")])
    denied = claim(900, sv("13132", "700.00", "0", None, "1", "CAS*CO*197*700.00~", "20260402"), "700.00", "0", cfi="CI",
                   dos="20260402")
    w.remit([denied], "20260420", payer="CIGNA", payer_id="CIGTEST")
    w.run(today="2026-06-15")
    got = {k: (r["signal"], r["expected_cents"], r["shortfall_cents"], (r["ev"].get("basis") or "")[:80])
           for k, r in by_pcn(w, f"patient_control_number='{pcn(900)}'").items()}
    assert got and all((v[1] or 0) > 0 for v in got.values()), got
    w.store.close()


def test_a_surgery_center_denial_is_never_sized_below_what_the_payer_pays_for_the_code(tmp_path, kind):
    """fix7c-r1-4. Anthem-like, 2024: the payer paid the adjacent tissue transfer 14301 $1,103.00 at the office (four
    claims) and $932.12 on its three surgery-center claims (the 835 names no place of service; the 837 says POS 24). It
    denied a whole surgery-center claim for a 14301: worth $932.12, what it pays for the code there. Neither setting holds
    the five lines a rate of the payer's own needs, but its own amounts agree: never sized by its share of charges below
    anything it has paid for the code."""
    w = World(tmp_path, kind)
    types(w, (DOC, "127.80", DR, 1))
    ant = dict(payer="ANTHEM", payer_id="ANTHM")
    w.remit(many(300, 200, "99213", "157.66", "60.00", dos="20240305"), "20240401", **ant)
    w.remit(many(100, 4, "14301", "1600.00", "1103.00", dos="20240310"), "20240410", **ant)
    w.remit(many(200, 3, "14301", "1600.00", "932.12", pos=None, dos="20240510"), "20240610", **ant)
    denied = claim(900, sv("14301", "1600.00", "0", None, "1", "CAS*CO*197*1600.00~", "20240802"), "1600.00", "0", pos=None,
                   dos="20240802").replace("C017*1*", "C017*4*", 1)        # the whole claim denied (CLP02 4)
    w.remit([denied], "20240901", **ant)
    _site_837(w, [(200, "24"), (201, "24"), (202, "24"), (900, "24")])
    w.run(today="2024-10-15")
    got = {k: (r["signal"], r["expected_cents"], (r["ev"].get("basis") or "")[:120]) for k, r in by_pcn(w, f"patient_control_number='{pcn(900)}'").items()}
    sizes = [v[1] for v in got.values()]
    assert sizes and all((s or 0) >= 0.9 * 93212 for s in sizes), got
    w.store.close()


def test_a_payers_own_few_amounts_in_a_setting_size_its_denials_before_another_payers(tmp_path, kind):
    """fix7c-r1-4, both rules apart. At the surgery center (POS 24) one payer paid a 14301 $932.12 twice and $900.00 once:
    too few lines for a rate of its own, but they agree, so its denial of a 14301 there is worth $932.12. Another payer paid
    two 99214 visits there ($85.58 and $87.78: no agreement), while a third payer's twelve visits there ($61.26) are most of
    the commercial plans' lines: its denial of a 99214 there is never sized below the least it has paid there itself,
    $85.58 (another payer's amounts are not its own)."""
    w = World(tmp_path, kind)
    types(w, (DOC, "127.80", DR, 1))
    ant, aet, cig = dict(payer="ANTHEM", payer_id="ANTHM"), dict(payer="AETNA", payer_id="AETTEST"), dict(payer="CIGNA", payer_id="CIGTEST")
    w.remit(many(300, 30, "99213", "157.66", "60.00") + many(100, 2, "14301", "1600.00", "932.12", pos="24")
            + many(110, 1, "14301", "1600.00", "900.00", pos="24"), "20260310", **ant)
    w.remit(many(400, 30, "99213", "157.66", "70.00") + many(200, 12, "99214", "223.65", "61.26", pos="24"), "20260310", **cig)
    w.remit(many(500, 30, "99213", "157.66", "75.00") + [visit(210, "99214", "223.65", "85.58", pos="24"),
                                                         visit(211, "99214", "223.65", "87.78", pos="24")], "20260310", **aet)
    w.remit([claim(900, sv("14301", "1600.00", "0", None, "1", "CAS*CO*197*1600.00~", "20260402"), "1600.00", "0", pos="24",
                   dos="20260402")], "20260420", **ant)
    w.remit([claim(901, sv("99214", "223.65", "0", None, "1", "CAS*CO*197*223.65~", "20260402"), "223.65", "0", pos="24",
                   dos="20260402")], "20260420", **aet)
    w.run(today="2026-06-15")
    got = {k: (r["signal"], r["expected_cents"]) for k, r in by_pcn(w, f"patient_control_number IN ('{pcn(900)}','{pcn(901)}')").items()}
    assert got.get(pcn(900), (None, None))[1] == 93212, got
    assert got.get(pcn(901), (None, None))[1] == 8558, got
    w.store.close()


def test_a_denial_no_rate_sizes_is_never_below_the_payers_own_amount_there(tmp_path, kind):
    """fix7c-r1-4, the last resort: the payer paid one surgery-center 14301 ($932.12) and no other payer has enough lines for
    a typical amount, so the denial is sized by the payer's share of charges (about 38 percent of $1,600.00, some $600):
    below the one amount it has paid for the code there. The size is never below that amount."""
    w = World(tmp_path, kind)
    types(w, (DOC, "127.80", DR, 1))
    ant = dict(payer="ANTHEM", payer_id="ANTHM")
    w.remit(many(300, 200, "99213", "157.66", "60.00", dos="20240305") + many(100, 1, "14301", "1600.00", "932.12", pos="24", dos="20240310"),
            "20240401", **ant)
    w.remit([claim(900, sv("14301", "1600.00", "0", None, "1", "CAS*CO*197*1600.00~", "20240802"), "1600.00", "0", pos="24",
                   dos="20240802")], "20240901", **ant)
    w.run(today="2024-10-15")
    got = {k: (r["signal"], r["expected_cents"], (r["ev"].get("basis") or "")[:90]) for k, r in by_pcn(w, f"patient_control_number='{pcn(900)}'").items()}
    assert got and all(v[1] == 93212 for v in got.values()), got
    w.store.close()


def test_a_line_priced_off_the_clinicians_contract_is_never_the_payers_own_amount(tmp_path, kind):
    """A payer priced a clinician's 13132s off the contract (CO-147, $229.20 where the commercial plans pay $250.00), on
    three claims of one procedure each. Those lines agree with each other, but they are what the item disputes, not the
    payer's rate: the denial-at-the-contract-rate item is sized by the commercial plans' typical amount, $20.80 short, never
    by the off-contract amounts read as the payer's own few (fix7c-r1-4's rule would leave it nothing to ask for and drop
    it)."""
    w = World(tmp_path, kind)
    types(w, (DOC, "127.80", DR, 1))
    w.remit(many(100, 4, "13132", "700.00", "250.00"), "20260310", payer="UNITEDHEALTHCARE", payer_id="UHCTEST")
    w.remit(many(110, 4, "13132", "700.00", "250.00"), "20260310", payer="AETNA", payer_id="AETTEST")
    w.remit(many(120, 3, "13132", "700.00", "250.00"), "20260310", payer="HUMANA", payer_id="HUMTEST")
    off = [claim(900 + k, sv("13132", "700.00", "229.20", "229.20", "1", "CAS*CO*147*470.80~", "20260402"), "700.00", "229.20",
                 dos="20260402") for k in range(3)]
    w.remit(off, "20260420", payer="CIGNA", payer_id="CIGTEST")
    w.run(today="2026-06-15")
    got = {k: (r["signal"], r["expected_cents"], r["shortfall_cents"]) for k, r in by_pcn(w, f"patient_control_number='{pcn(900)}'").items()}
    assert got == {pcn(900): ("DENIED_CONTRACT_RATE", 25000, 2080)}, got
    w.store.close()


def test_an_office_line_is_sized_by_the_offices_few_lines_before_every_settings_together(tmp_path, kind):
    """The world run of the fix round (r1-2's fix): commercial plans paid a 13132 at the office on 6 claims ($580.00) and at
    the surgery center on 8 ($236.69). Read across settings, the typical amount is the surgery center's (the median of 14),
    so an office 13132 a payer priced off the clinician's contract (CO-147, $494.24) looked paid in full and its item was
    dropped. The office's own few lines size it: $580.00, $85.76 short."""
    w = World(tmp_path, kind)
    types(w, (DOC, "127.80", DR, 1))
    w.remit(many(100, 4, "13132", "900.00", "580.00"), "20260310", payer="UNITEDHEALTHCARE", payer_id="UHCTEST")
    w.remit(many(110, 2, "13132", "900.00", "580.00"), "20260310", payer="AETNA", payer_id="AETTEST")
    w.remit(many(120, 8, "13132", "900.00", "236.69", pos="24"), "20260310", payer="HUMANA", payer_id="HUMTEST")
    off = claim(900, sv("13132", "900.00", "494.24", "494.24", "1", "CAS*CO*147*405.76~", "20260402"), "900.00", "494.24",
                dos="20260402")
    w.remit([off], "20260420", payer="CIGNA", payer_id="CIGTEST")
    w.run(today="2026-06-15")
    got = {k: (r["signal"], r["expected_cents"], r["shortfall_cents"]) for k, r in by_pcn(w, f"patient_control_number='{pcn(900)}'").items()}
    assert got == {pcn(900): ("DENIED_CONTRACT_RATE", 58000, 8576)}, got
    w.store.close()


def test_a_facility_line_takes_the_payers_office_figure_at_the_fee_schedules_share_before_the_classs_few_facility_lines(tmp_path, kind):
    """r1-2's fallbacks are read last at a facility: a payer with an office rate of its own ($800.00 for a 14020) and no
    facility lines sizes a surgery center's line at that rate times the fee schedule's facility share, as before the fix
    round, never at other payers' few facility lines ($531.97 on 4 claims). A payer with no office figure either takes those
    few facility lines (the class has 10 lines across settings, too few in each), and an office line the class's 6 office
    lines."""
    from remitwatch.mpfs import medicare_amount
    w = World(tmp_path, kind)
    types(w, (DOC, "127.80", DR, 1))
    w.remit(many(100, 6, "14020", "1800.00", "800.00"), "20260301", payer="AETNA", payer_id="AETTEST")
    w.remit(many(200, 4, "14020", "1800.00", "531.97", pos="24"), "20260301", payer="UNITEDHEALTHCARE", payer_id="UHCTEST")
    det = Detector(w.store, run_id="r", today="2026-06-15")
    det.load()
    det.build_baselines()

    def size(group, pos):
        return det.usual_rate({"payer_group": group, "year": "2026", "provider_tier": "physician", "pos_class": pos}, "14020")[:2]
    share = medicare_amount("2026", "KY", "14020", facility=True) / medicare_amount("2026", "KY", "14020")
    rate, basis = size("Aetna", "facility")
    assert rate == int(round(80000 * share)) and "facility share" in basis, (rate, basis)
    rate, basis = size("Humana", "facility")
    assert rate == 53197 and "few lines" in basis, (rate, basis)
    rate, basis = size("Humana", "office")
    assert rate == 80000 and "few lines" in basis, (rate, basis)
    w.store.close()


def test_a_class_too_small_in_every_setting_leaves_the_payers_own_level_to_size_a_denial(tmp_path, kind):
    """The class's few lines of a setting are read only in r1-2's case (lines enough across settings, too few in each): where
    the class has fewer than 10 lines in all (7 office 17110s at $150.00), a denial of a code the payer has no rate for is
    sized as before the fix round, by the payer's own level against the fee schedule (its 99213s at 120 percent of
    Medicare's), not by those 7 lines."""
    from remitwatch.mpfs import medicare_amount
    w = World(tmp_path, kind)
    types(w, (DOC, "127.80", DR, 1))
    w.remit(many(100, 20, "99213", "200.00", "107.42"), "20260301", payer="CIGNA", payer_id="CIGTEST")
    w.remit(many(200, 4, "17110", "300.00", "150.00"), "20260301", payer="UNITEDHEALTHCARE", payer_id="UHCTEST")
    w.remit(many(210, 3, "17110", "300.00", "150.00"), "20260301", payer="AETNA", payer_id="AETTEST")
    denied = claim(900, sv("17110", "300.00", "0", None, "1", "CAS*CO*197*300.00~", "20260402"), "300.00", "0", dos="20260402")
    w.remit([denied], "20260420", payer="CIGNA", payer_id="CIGTEST")
    w.run(today="2026-06-15")
    level = 10742 / medicare_amount("2026", "KY", "99213")
    got = {k: (r["expected_cents"], (r["ev"].get("basis") or "")[:60]) for k, r in by_pcn(w, f"patient_control_number='{pcn(900)}'").items()}
    assert got and all(v[0] == int(round(level * medicare_amount("2026", "KY", "17110"))) for v in got.values()), got
    w.store.close()


def test_a_payers_own_facility_amount_sizes_a_claim_with_no_remittance_before_the_class_across_settings(tmp_path, kind):
    """A claim with no remittance (an EMA-only denial, Detector._rate) is sized setting by setting: for a code the payer has
    paid only at the surgery center ($491.64), that amount, never its class's typical amount across settings (another
    Medicare Advantage plan's office lines at $700.00 make it $700.00), which the office pass took first once r1-2's fix
    let a line of too few class lines in its setting read every setting together."""
    w = World(tmp_path, kind)
    types(w, (DOC, "127.80", DR, 1))
    w.remit(many(200, 6, "14040", "1500.00", "491.64", cfi="16", pos="24"), "20260301", payer="HUMANA", payer_id="HUMTEST")
    w.remit(many(300, 6, "14040", "1500.00", "700.00", cfi="16"), "20260301", payer="UNITEDHEALTHCARE", payer_id="UHCTEST")
    det = Detector(w.store, run_id="r", today="2026-06-15")
    det.load()
    det.build_baselines()
    assert det._rate("Humana Medicare Advantage", "14040", "2026") == 49164
    w.store.close()


def test_a_plan_apart_keeps_its_payers_pa_np_rate_where_it_shows_none_of_its_own(tmp_path, kind):
    """fix7c-r1-5. UMR-like: the payer pays NPs 85 percent of a physician's amount on its network plans (30 NP lines). One
    large employer plan pays a schedule of its own ($130.00 for a 99214 to physicians, 22 lines) and has only two NP lines
    of its own: too few for a PA/NP rate of the plan's own. Five NP 99214 lines of that plan paid $68.90 are short of the
    PA/NP rate the payer pays (85 percent of the plan's $130.00, $110.50): $208.00 for the five, not $305.50 against the
    plan's full physician rate."""
    w = World(tmp_path, kind)
    types(w, (DOC, "127.80", DR, 1), (NP, "108.63", NR, 40))
    net = []
    for k in range(6):          # six network employer plans
        g = f"NET{k:03d}"
        net += many(100 + 20 * k, 5, "99213", "156.66", "76.15", group=g)
        net += many(105 + 20 * k, 5, "99214", "223.65", "110.00", group=g)
        net += many(110 + 20 * k, 4, "11102", "150.00", "112.74", group=g)
        net += many(1000 + 10 * k, 3, "99213", "156.66", "64.73", group=g, npi=NP, prov=NR)
        net += many(1003 + 10 * k, 2, "99214", "223.65", "93.50", group=g, npi=NP, prov=NR)
    big = (many(300, 8, "99213", "156.66", "94.00", group="BIG001")
           + many(400, 22, "99214", "223.65", "130.00", group="BIG001")
           + many(320, 8, "11102", "150.00", "91.72", group="BIG001")
           + many(330, 2, "99213", "156.66", "80.56", group="BIG001", npi=NP, prov=NR))
    w.remit(net + big, "20260415", payer="UMR", payer_id="UMRTEST")
    w.remit(many(900, 5, "99214", "223.65", "68.90", group="BIG001", npi=NP, prov=NR, dos="20260505"), "20260520",
            payer="UMR", payer_id="UMRTEST")
    w.run(today="2026-06-15")
    short = sum(r["shortfall_cents"] or 0 for r in w.rows("category='underpayment'"))
    got = sorted((r["signal"], r["expected_cents"], r["shortfall_cents"]) for r in w.rows("category='underpayment'"))
    assert short == 20800, got
    w.store.close()


def test_a_plan_renumbered_mid_year_keeps_its_own_schedule(tmp_path, kind):
    """fix7c-r2-13. UMR-like, as the first plan test: six employer plans pay the network rate (biopsy $112.74); one large
    plan (BIG001) pays its own schedule (biopsy $91.72 to physicians) and is read apart. In July the same employer's plan
    comes back under a new group number (BIG002) and pays exactly BIG001's amounts: six biopsies at $91.72. They are the
    plan's own schedule, not underpayments: no routine-rate review and no one-off."""
    w = World(tmp_path, kind)
    types(w, (DOC, "127.80", DR, 1), (NP, "108.63", NR, 40))
    net = []
    for k in range(6):
        g = f"NET{k:03d}"
        net += many(100 + 10 * k, 4, "11102", "150.00", "112.74", group=g)
        net += many(104 + 10 * k, 4, "11102", "150.00", "112.74", group=g, npi=NP, prov=NR)
        net += many(1000 + 10 * k, 5, "99213", "156.66", "76.15", group=g)
    big = (many(300, 8, "11102", "150.00", "91.72", group="BIG001")
           + many(310, 14, "11102", "150.00", "78.62", group="BIG001", npi=NP, prov=NR)
           + many(330, 8, "99213", "156.66", "94.00", group="BIG001")
           + many(340, 14, "99213", "156.66", "80.57", group="BIG001", npi=NP, prov=NR))
    w.remit(net + big, "20260415", payer="UMR", payer_id="UMRTEST")
    w.remit(many(700, 6, "11102", "150.00", "91.72", group="BIG002", dos="20260710"), "20260801", payer="UMR", payer_id="UMRTEST")
    w.run(today="2026-09-15")
    got = [(r["signal"], r["patient_control_number"], r["shortfall_cents"], (r["ev"].get("lines"))) for r in w.rows("category='underpayment'")]
    assert not got, got
    w.store.close()


@pytest.mark.parametrize("first", ["KY", "IN"])
def test_a_day_the_clinician_billed_both_states_is_a_visit_of_no_known_state_whatever_the_file_order(tmp_path, kind, first):
    """fix7c-r2-5. NPs paid in full in Kentucky's offices ($110.43) and 85 percent in Indiana's ($93.87), the state of the
    day's Medicare carrier for the clinician. On one day the NP's Medicare claims went to both carriers (a morning in each
    office). A visit of that day paid $93.87 is a visit whose state is not known: held to the lower rate, not short,
    whichever Medicare file was read last."""
    w = World(tmp_path, kind)
    ky = [f"202604{d:02d}" for d in range(1, 21)]
    ind = [f"202605{d:02d}" for d in range(1, 21)]
    both = "20260615"
    med_ky = medicare(1, 25, DOC, "127.80", DR) + [visit(40 + k, "99214", "223.65", "108.63", npi=NP, prov=NR, cfi="MB", dos=d)
                                                    for k, d in enumerate(ky)]
    med_ky.append(visit(60, "99214", "223.65", "108.63", npi=NP, prov=NR, cfi="MB", dos=both))
    med_in = [visit(70 + k, "99214", "223.65", "108.63", npi=NP, prov=NR, cfi="MB", dos=d) for k, d in enumerate(ind)]
    med_in.append(visit(95, "99214", "223.65", "108.63", npi=NP, prov=NR, cfi="MB", dos=both))
    files = {"KY": (med_ky, dict(payer="KY MEDICARE", payer_id="15102")), "IN": (med_in, dict(payer="IN MEDICARE", payer_id="00630"))}
    for st in (first, "IN" if first == "KY" else "KY"):
        claims, payer = files[st]
        w.remit(claims, "20260701", **payer)
    uhc = (many(100, 20, "99214", "223.65", "110.43", dos="20260410")
           + [visit(200 + k, "99214", "223.65", "110.43", npi=NP, prov=NR, dos=d) for k, d in enumerate(ky[:15])]
           + [visit(300 + k, "99214", "223.65", "93.87", npi=NP, prov=NR, dos=d) for k, d in enumerate(ind[:12])])
    w.remit(uhc, "20260710", payer="UNITEDHEALTHCARE", payer_id="UHCTEST")
    w.remit([visit(900, "99214", "223.65", "93.87", npi=NP, prov=NR, dos=both)], "20260720", payer="UNITEDHEALTHCARE", payer_id="UHCTEST")
    w.run(today="2026-08-15")
    got = {k: (r["signal"], r["expected_cents"]) for k, r in by_pcn(w, "category='underpayment'").items()}
    assert got == {}, got
    w.store.close()


def test_a_corrected_837_names_the_setting_not_the_first_one_sent(tmp_path, kind):
    """fix7c-r2-10. A repair is $774.00 at the office and $630.79 at the surgical site, whose claims the 835 sends without
    CLP08. One site claim was first sent with place of service 11 by mistake and corrected (frequency 7, place of service
    24, the same claim id): it is a site claim, paid exactly the site's rate, not short."""
    w = World(tmp_path, kind)
    types(w, (DOC, "127.80", DR, 1))
    w.remit(many(100, 60, "14040", "1100.00", "774.00") + many(200, 20, "14040", "1100.00", "630.79", pos=None)
            + [visit(900, "14040", "1100.00", "630.79", pos=None)], "20260415")
    w.store.conn.executescript(DDL_837)
    rows = [(f"k837-{200 + k}", pcn(200 + k), "24", "1", "20260402") for k in range(20)]
    rows += [("k837-900a", pcn(900), "11", "1", "20260402"), ("k837-900b", pcn(900), "24", "7", "20260409")]
    for key, p, pos, freq, bht in rows:
        w.store.conn.execute("INSERT INTO claims_837 (claim_837_key, patient_control_number, place_of_service, frequency_code, "
                             "bht_created, service_date) VALUES (?, ?, ?, ?, ?, '2026-04-01')", (key, p, pos, freq, bht))
    w.store.conn.commit()
    w.run(today="2026-06-15")
    got = {k: (r["signal"], r["expected_cents"], r["shortfall_cents"]) for k, r in by_pcn(w, "category='underpayment'").items()}
    assert got == {}, got
    w.store.close()


# ----------------------------------------------------------------------------- split days (the cross-claim ranking)
def test_two_procedures_of_equal_worth_on_separate_claims_both_cut_owe_one_missing_half(tmp_path, kind):
    """fix7c-r2-12. 17311 is $585.12 at the office. One patient's two Mohs of the day were billed on two claims and the
    payer paid each at $292.56. One of them is the first procedure (owed in full), the other rightly the second: the day
    is owed one missing half, $292.56, listed once."""
    w = World(tmp_path, kind)
    types(w, (DOC, "127.80", DR, 1))
    hum = dict(payer="HUMANA", payer_id="HUMTEST")
    w.remit(many(100, 25, "17311", "900.00", "585.12", cfi="16"), "20260301", **hum)
    w.remit([visit(800, "17311", "900.00", "292.56", cfi="16", pat=800, dos="20260302"),
             visit(801, "17311", "900.00", "292.56", cfi="16", pat=800, dos="20260302")], "20260410", **hum)
    w.run(today="2026-06-15")
    got = [(r["patient_control_number"], r["signal"], r["shortfall_cents"], r["title"]) for r in w.rows("category='underpayment'")]
    assert sum(g[2] or 0 for g in got) == 29256 and len(got) == 1, got
    w.store.close()


def test_a_claim_the_payer_split_in_two_answers_ranks_its_halves_together(tmp_path, kind):
    """A guard for fix7c-r2-7's own-answer rule: the payer answered one claim id in two parts on one remittance (two payer
    claim numbers), a 17311 halved in each ($292.56 of $585.12). They are two procedures of the day, not one service
    answered again: ranked together, the day owes one missing half, $292.56, listed once."""
    w = World(tmp_path, kind)
    types(w, (DOC, "127.80", DR, 1))
    hum = dict(payer="HUMANA", payer_id="HUMTEST")
    w.remit(many(100, 25, "17311", "900.00", "585.12", cfi="16"), "20260301", **hum)
    w.remit([visit(800, "17311", "900.00", "292.56", cfi="16", pat=800, dos="20260302"),
             visit(801, "17311", "900.00", "292.56", cfi="16", pat=800, dos="20260302").replace(pcn(801), pcn(800))], "20260410", **hum)
    w.run(today="2026-06-15")
    got = [(r["signal"], r["shortfall_cents"]) for r in w.rows("category='underpayment'")]
    assert got == [("UNDERPAID_VS_OWN_RATE", 29256)], got
    w.store.close()


def test_a_family_members_procedure_on_the_same_subscriber_number_does_not_rank_the_patients_day(tmp_path, kind):
    """fix7c-r2-8. The subscriber had a Mohs at the office, paid $292.56 of $585.12, and nothing else that day. The spouse
    (another patient, under the subscriber's number on the 835's NM1*IL and as the 837's subscriber) had a 14301 at the
    surgery center the same day with the same doctor, paid in full. The spouse's repair is no part of the subscriber's
    day: the Mohs is owed its half, $292.56."""
    w = World(tmp_path, kind)
    types(w, (DOC, "127.80", DR, 1))
    w.remit(many(100, 25, "17311", "900.00", "585.12") + many(220, 6, "14301", "2500.00", "943.80", pos="24"), "20260301")
    w.remit([visit(800, "17311", "900.00", "292.56", pat=800, dos="20260302"),
             visit(801, "14301", "2500.00", "943.80", pat=900, insured=800, dos="20260302", pos="24")], "20260410")
    _sent_837(w, [(800, 800, "20260302", "17311", "11"), (801, 800, "20260302", "14301", "24")])
    w.run(today="2026-06-15")
    got = {k: (r["signal"], r["shortfall_cents"]) for k, r in by_pcn(w, "category='underpayment'").items()}
    assert got == {pcn(800): ("UNDERPAID_VS_OWN_RATE", 29256)}, got
    w.store.close()


def test_a_dependants_day_is_not_ranked_by_the_subscribers_unanswered_837(tmp_path, kind):
    """fix7c-r2-8, the other half: the dependant (member number of their own on the 835's NM1*QC, the subscriber's on
    NM1*IL) had a Mohs at the office, paid $292.56 of $585.12. The subscriber's 837 for a 14301 at the surgery center that
    day, same doctor, is not answered on file. It is matched by the subscriber's number only, which the family shares, so it
    is no part of the dependant's day: the Mohs is owed its half."""
    w = World(tmp_path, kind)
    types(w, (DOC, "127.80", DR, 1))
    w.remit(many(100, 25, "17311", "900.00", "585.12") + many(220, 6, "14301", "2500.00", "943.80", pos="24"), "20260301")
    w.remit([visit(800, "17311", "900.00", "292.56", pat=900, insured=800, dos="20260302")], "20260410")
    _sent_837(w, [(801, 800, "20260302", "14301", "24")])
    w.run(today="2026-06-15")
    got = {k: (r["signal"], r["shortfall_cents"]) for k, r in by_pcn(w, "category='underpayment'").items()}
    assert got == {pcn(800): ("UNDERPAID_VS_OWN_RATE", 29256)}, got
    w.store.close()


def test_a_cut_day_with_two_lower_valued_procedures_is_owed_one_missing_half_once(tmp_path, kind):
    """fix7c-r2-6. A split day on three claims: the surgery center's 14041 ($589.45 facility rate, a few lines: no item of
    its own) paid $294.73, the office Mohs 17311 ($585.12) paid $292.56, and an office biopsy 11102 ($112.74) on a third
    claim paid $56.37. The day is owed one missing half: listed once, at the least ($292.56 on the Mohs), not the biopsy's
    half as well."""
    w = World(tmp_path, kind)
    types(w, (DOC, "127.80", DR, 1))
    hum = dict(payer="HUMANA", payer_id="HUMTEST")
    w.remit(many(100, 25, "17311", "900.00", "585.12", cfi="16") + many(240, 6, "14041", "1800.00", "589.45", cfi="16", pos="24")
            + many(300, 25, "11102", "150.00", "112.74", cfi="16"), "20260301", **hum)
    w.remit([visit(800, "17311", "900.00", "292.56", cfi="16", pat=800, dos="20260302"),
             visit(801, "14041", "1800.00", "294.73", cfi="16", pat=800, dos="20260302", pos="24"),
             visit(802, "11102", "150.00", "56.37", cfi="16", pat=800, dos="20260302")], "20260410", **hum)
    w.run(today="2026-06-15")
    got = {k: (r["signal"], r["shortfall_cents"], bool(r["ev"].get("every_cut"))) for k, r in by_pcn(w, "category='underpayment'").items()}
    assert got == {pcn(800): ("UNDERPAID_VS_OWN_RATE", 29256, True)}, got
    w.store.close()


@pytest.mark.parametrize("worked", [False, True])
def test_a_cut_day_the_payer_fixes_as_asked_is_counted_recovered(tmp_path, kind, worked):
    """fix7c-r2-7. An every-procedure-cut day: the office Mohs paid $292.56 of $585.12 and the surgery center's 14041
    $294.73 of $589.45. The item, on the Mohs line, says the 14041 is the higher-valued and was owed in full. The payer then
    does exactly that: it reverses the 14041's payment and pays it $589.45. The day's missing half came in: the item is
    closed with its recovery counted, whether or not a person had worked it."""
    from remitwatch.actions import apply_status
    w = World(tmp_path, kind)
    types(w, (DOC, "127.80", DR, 1))
    hum = dict(payer="HUMANA", payer_id="HUMTEST")
    w.remit(many(100, 25, "17311", "900.00", "585.12", cfi="16") + many(240, 6, "14041", "1800.00", "589.45", cfi="16", pos="24"),
            "20260301", **hum)
    w.remit([visit(800, "17311", "900.00", "292.56", cfi="16", pat=800, dos="20260302"),
             visit(801, "14041", "1800.00", "294.73", cfi="16", pat=800, dos="20260302", pos="24")], "20260410", **hum)
    w.run(today="2026-06-15")
    f = w.one("category='underpayment'")
    assert f["ev"].get("every_cut") is True and f["shortfall_cents"] == 29256
    if worked:
        apply_status(w.store.conn, f["finding_id"], "appealed", "Asked the payer to reprocess the day", actor="biller",
                     at="2026-06-16T15:00:00+00:00")
        w.store.conn.commit()
    w.remit([reversal(801, "14041", "1800.00", "294.73", cfi="16", pat=800, dos="20260302", pos="24"),
             visit(801, "14041", "1800.00", "589.45", cfi="16", pat=800, dos="20260302", pos="24")], "20260701", **hum)
    w.run(today="2026-07-15")
    row = w.store.conn.execute("SELECT status, stale, work_state, shortfall_cents, recovered_cents FROM findings WHERE finding_id=?",
                               (f["finding_id"],)).fetchone()
    assert row is not None and (row["recovered_cents"] or 0) > 0, dict(row) if row else None
    assert row["status"] == "resolved" and not row["stale"], dict(row)
    assert not unproven(w), unproven(w)
    w.store.close()


def test_a_split_day_the_payer_fixes_by_paying_the_mohs_again_is_counted_recovered(tmp_path, kind):
    """fix7c-r2-7, the other way round: a Mohs halved ($292.56) beside the surgery center's 14040 paid in full ($491.64) is
    owed $46.74 net. The payer then reverses the Mohs and pays it $585.12 (the same claim id answered again): that is the
    Mohs's own later answer, never another procedure of its day (which would make the Mohs look rightly second), and the
    item closes with the payment as its recovery."""
    w = World(tmp_path, kind)
    _split_days(w, [("17311", "292.56", "14040", "491.64")])
    w.run(today="2026-06-15")
    f = w.one("category='underpayment'")
    assert f["patient_control_number"] == pcn(800) and f["shortfall_cents"] == 4674, (f["patient_control_number"], f["shortfall_cents"])
    w.remit([reversal(800, "17311", "900.00", "292.56", cfi="16", pat=800, dos="20260302"),
             visit(800, "17311", "900.00", "585.12", cfi="16", pat=800, dos="20260302")], "20260701", payer="HUMANA", payer_id="HUMTEST")
    w.run(today="2026-07-15")
    row = w.store.conn.execute("SELECT status, recovered_cents FROM findings WHERE finding_id=?", (f["finding_id"],)).fetchone()
    assert row is not None and row["status"] == "resolved" and (row["recovered_cents"] or 0) >= 4674, dict(row) if row else None
    assert not w.rows("category='underpayment' AND status='open'"), [(r["signal"], r["title"]) for r in w.rows("category='underpayment' AND status='open'")]
    assert not unproven(w), unproven(w)
    w.store.close()


def _units_line(i: int, code: str, charge: str, allowed: str, units: int, *, dos: str, pos: str, pat: int, cfi: str = "16") -> str:
    co = f"{float(charge) - float(allowed):.2f}"
    return claim(i, sv(code, charge, allowed, allowed, str(units), f"CAS*CO*45*{co}~", dos), charge, allowed, pat=pat, cfi=cfi, dos=dos, pos=pos)


@pytest.mark.parametrize("units,allowed,owed", [(3, "500.00", 16756), (4, "625.00", 16756)])
def test_a_line_of_several_units_on_the_other_claim_is_ranked_unit_by_unit(tmp_path, kind, units, allowed, owed):
    """fix7c-r2-11. 17311 is $585.12 at the office; 11602 is $250.00 a lesion at the surgery center. A split day: the Mohs
    paid $292.56 (as a second procedure) and, on the surgery center's claim, 11602 for 3 (or 4) lesions on one line, allowed
    $500.00 ($250 + $125 + $125) or $625.00. Each excision is worth $250, less than the Mohs: the Mohs is the highest-valued
    and is owed in full, less what a correct ranking takes back from the excisions (each at half: $375 or $500 due, $125
    paid above it): $167.56 both times."""
    w = World(tmp_path, kind)
    types(w, (DOC, "127.80", DR, 1))
    hum = dict(payer="HUMANA", payer_id="HUMTEST")
    w.remit(many(100, 25, "17311", "900.00", "585.12", cfi="16") + many(240, 6, "11602", "600.00", "250.00", cfi="16", pos="24"),
            "20260301", **hum)
    w.remit([visit(800, "17311", "900.00", "292.56", cfi="16", pat=800, dos="20260302"),
             _units_line(801, "11602", f"{600 * units}.00", allowed, units, dos="20260302", pos="24", pat=800)], "20260410", **hum)
    w.run(today="2026-06-15")
    got = {k: (r["signal"], r["shortfall_cents"]) for k, r in by_pcn(w, "category='underpayment'").items()}
    assert got == {pcn(800): ("UNDERPAID_VS_OWN_RATE", owed)}, got
    w.store.close()


def test_a_site_claim_with_no_setting_named_is_ranked_at_the_facility_rate_it_was_paid(tmp_path, kind):
    """fix7c-r2-9. A 14040 is $720.00 at the office and $491.64 at the surgery center (both firm). A split day: the Mohs paid
    $292.56 (as a second procedure), and the surgery center's 14040 paid $491.64 in full on a claim whose 835 leaves CLP08
    out, with no 837 or EMA location to name the setting. usual_rate holds such a line to the facility rate it was paid;
    the ranking values it the same way: $491.64 < $585.12, so the Mohs is the highest-valued and is owed $292.56 -
    ($491.64 - $245.82) = $46.74."""
    w = World(tmp_path, kind)
    types(w, (DOC, "127.80", DR, 1))
    hum = dict(payer="HUMANA", payer_id="HUMTEST")
    w.remit(many(100, 25, "17311", "900.00", "585.12", cfi="16") + many(200, 25, "14040", "1500.00", "720.00", cfi="16")
            + many(300, 25, "14040", "1500.00", "491.64", cfi="16", pos="24"), "20260301", **hum)
    w.remit([visit(800, "17311", "900.00", "292.56", cfi="16", pat=800, dos="20260302"),
             visit(801, "14040", "1500.00", "491.64", cfi="16", pat=800, dos="20260302", pos=None)], "20260410", **hum)
    w.run(today="2026-06-15")
    got = {k: (r["signal"], r["shortfall_cents"]) for k, r in by_pcn(w, "category='underpayment'").items()}
    assert got == {pcn(800): ("UNDERPAID_VS_OWN_RATE", 4674)}, got
    w.store.close()
