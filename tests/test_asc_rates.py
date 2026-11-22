"""
Tests for the Medicare ASC rates built by scripts/build_ascfs.py from CMS's own addenda and county crosswalk (or from
ASCA's "Medicare ASC final rule rate calculator" workbook, checked against CMS's files): the build itself, on small
invented files; the rate lookup (remitwatch.ascrates), worked out as the workbook works it out, to the cent; the payload
of the Calculator's ASC view (remitwatch.calculator.asc_rule), its arithmetic (static/asc_model.js) and the page
(static/asc.js), run with node; and the owner gate on the routes.

The expected amounts in WARRICK and SAN_FRANCISCO are the workbook's own: its Calculator sheet recalculated by LibreOffice
for Warrick County, Indiana and for San Francisco (the build was checked that way against every code in six places,
territories included); CMS's update of 11/10/2026 left these codes as they were. Every clinician, case count, payer amount
and every amount in the build's files here is made up; the code numbers and CMS's published rates are public.
"""
from __future__ import annotations

import csv
import glob
import importlib.util
import json
import os
import re
import runpy
import shutil
import sqlite3
import subprocess
import sys
from decimal import Decimal

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from remitwatch import ascrates, calculator  # noqa: E402
from remitwatch.ascfs import ASC, OFFICE_COUNTY, WAGE_INDEX, asc_amount  # noqa: E402
from remitwatch.mpfs import MPFS  # noqa: E402


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_ascfs", os.path.join(ROOT, "scripts", "build_ascfs.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


B = _load_builder()
NODE = shutil.which("node")
STATIC = os.path.join(ROOT, "remitwatch", "static")
KEYS = ("total", "total_after", "medicare", "seq", "medicare_after", "copay")

# the workbook's Calculator sheet, columns E..P in cents: national (total, total after sequestration, Medicare, the 2%,
# Medicare after it, copayment) and the same for the county
WARRICK = {
    "14040": ((105608, 103918, 84486, 1690, 82796, 21122), (103258, 101606, 82606, 1652, 80954, 20652)),
    "11604": ((37478, 36878, 29982, 600, 29382, 7496), (36644, 36058, 29315, 586, 28729, 7329)),
    "0101T": ((12443, 12244, 9954, 199, 9755, 2489), (12167, 11972, 9733, 195, 9538, 2434)),
    # a preventive service: no copayment ('**')
    "G0105": ((50061, 49060, 50061, 1001, 49060, 0), (48947, 47968, 48947, 979, 47968, 0)),
    # a drug with an inflation-adjusted copayment, paid as it stands (K2: no wage adjustment)
    "J0121": ((429, 422, 345, 7, 338, 84), (429, 422, 345, 7, 338, 84)),
    # a non-opioid alternative ('***'): keeps its 20% copayment
    "C9818": ((31, 30, 25, 1, 24, 6), (31, 30, 25, 1, 24, 6)),
}
SAN_FRANCISCO = {
    "14040": (148997, 146613, 119197, 2384, 116813, 29800),
    "0101T": (17556, 17275, 14044, 281, 13763, 3512),
}


def test_the_tables_hold_both_years_every_county_and_the_offices():
    t = ascrates.tables()
    assert t.years == ["2026", "2027"] and ascrates.rule_year() == "2027" and ascrates.prior_year() == "2026"
    assert len(t.codes) == 8225 and len(t.places) == 3224                    # 8,187 codes in 2027 and 38 only in 2026
    assert sum(1 for c in t.codes if t.rates[c]["2027"]) == 8187
    m = t.meta
    assert m["labor_share"] == 0.5 and m["sequestration"] == 0.02 and m["coinsurance"] == 0.2
    # the indicators the calculator leaves unadjusted, and skin substitute supplies, which CMS pays without the wage index
    assert m["not_wage_adjusted"] == ["H2", "J7", "K2", "K5", "K7", "L6", "S2"]
    assert m["pi"]["N1"].startswith("Packaged") and "S2" in m["pi"]
    # the offices' counties and wage indexes, both years
    assert ascrates.office_place("newburgh")["county"] == "WARRICK" and OFFICE_COUNTY["newburgh"] == "18173"
    assert ascrates.place("IN", "warrick")["cbsa"] == "21780" and ascrates.place(fips="21059")["county"] == "DAVIESS"
    assert ascrates.place("KY", "HENDERSON")["cbsa_name"] == "RURAL KENTUCKY"
    assert ascrates.office_wage_index("newburgh", "2026") == Decimal("0.9727") and ascrates.office_wage_index("newburgh", "2027") == Decimal("0.9555")
    assert [float(ascrates.place(*k)["wi"]) for k in (("IN", "WARRICK"), ("KY", "DAVIESS"), ("KY", "HENDERSON"))] == \
        [WAGE_INDEX["2027"]["newburgh"], WAGE_INDEX["2027"]["owensboro"], WAGE_INDEX["2027"]["henderson"]]
    # Guam and the Virgin Islands have no county in the crosswalk: wage-indexed by name, as the workbook does
    assert ascrates.place("GU", "GUAM")["wi"] == Decimal("0.9611") and ascrates.place("VI", "VIRGIN ISLANDS")["wi"] == Decimal("0.5784")
    p = ascrates.places()
    assert len(p["states"]) == 54 and ["KY", "KENTUCKY"] in p["states"] and len(p["counties"]["KY"]) == 120


def test_the_rule_year_is_cms_posting_of_november_10():
    """CMS posted the CY 2027 addenda again on 11/10/2026, after ASCA's workbook (version 77) was set up: the tables
    are that posting, and say so."""
    t = ascrates.tables()
    m = t.meta
    assert m["cms_release"] == "2026-11-10" and m["checked_against_cms"] is True
    assert m["source"] == "CMS's CY 2027 final rule addenda as posted on November 10, 2026"
    r = lambda c: t.rates[c]["2027"]
    # finalized for separate payment in an ASC in CY 2027 (CMS-1850-FC): no longer packaged
    assert (r("G0681")["pi"], r("G0681")["rate"], r("G0681")["mpd"]) == ("G2", 39148, "Y")
    assert (r("G0683")["pi"], r("G0683")["rate"]) == ("G2", 105608) and t.rates["G0683"]["2026"]["pi"] == "N1"
    assert (r("C8015")["pi"], r("C8015")["rate"], r("C8015")["ci"]) == ("J8", 2322538, "NI") and r("27417")["rate"] == 990705
    assert r("90385")["rate"] == 8000 and r("A9608")["rate"] == 29270
    # the final CY 2027 crosswalk with its 5% cap: a transition CBSA of its own where the cap holds a county up
    for fips, cbsa, wi in (("13171", "50002", "0.8629"), ("16077", "50004", "0.8092"), ("42103", "50020", "0.9679"),
                           ("51113", "50021", "0.8782"), ("37167", "34", "0.8561")):
        p = ascrates.place(fips=fips)
        assert (p["cbsa"], p["wi"]) == (cbsa, Decimal(wi)), fips


def test_skin_substitutes_are_paid_without_the_wage_index():
    """S2, a skin substitute supply: paid at the OPPS rate and not wage-adjusted (CY 2026 final rule, 90 FR 53887-53888,
    kept for 2027), with the 20% copayment. The same amount in every county."""
    n = ascrates.row("Q4101")["national"]
    assert (n["total"], n["medicare"], n["copay"]) == (12714, 10171, 2543)
    for where in (ascrates.place("IN", "WARRICK"), ascrates.place("CA", "SAN FRANCISCO"), ascrates.place("GU", "GUAM")):
        r = ascrates.row("Q4101", where, Decimal("0.9727"))
        assert r["pi"] == "S2" and r["adjusted"] is False and r["local"] == n and r["prior"]["local_total"] == 12714


# CMS's own files are not in the repository. Point REMITWATCH_ASC_CMS at a folder holding them (the rule year's Addenda
# AA, BB, DD1 and FF, the year before's AA and BB for the quarter the tables name, and the county crosswalk) to compare.
CMS_DIR = os.environ.get("REMITWATCH_ASC_CMS")


def _cms_files_in(folder: str, meta: dict) -> dict:
    """CMS's CSVs in a folder that the tables are from: each addendum by its title and the release its headers name."""
    ry, py = meta["rule_year"], meta["prior_year"]
    found: dict = {}
    for path in sorted(glob.glob(os.path.join(folder, "**", "*.csv"), recursive=True)):
        grid = B.rows(path)
        title = " ".join((grid[0][0] if grid and grid[0] else "").split())
        heads = next(([" ".join(h.split()).lower() for h in r] for r in grid[:40] if r and r[0].strip() in ("HCPCS Code", "FIPS Code")), [])
        kind = next((k for k in ("AA", "BB", "DD1", "FF") if title.startswith(f"Addendum {k} ")), None)
        if kind in ("AA", "BB") and heads:
            year, release = B._period(heads, path)
            if B._label(year, release) == meta["years"].get(year):
                found.setdefault((kind, year), path)
        elif kind in ("DD1", "FF") and f"CY {ry}" in title and "Final" in title:
            found.setdefault(kind, path)
        elif heads and heads[0] == "fips code" and f"final cy {ry} asc wage index with 5% cap" in heads:
            found.setdefault("counties", path)
    need = [("AA", ry), ("BB", ry), ("AA", py), ("BB", py), "DD1", "FF", "counties"]
    if any(k not in found for k in need):
        pytest.skip(f"CMS's files for these tables are not all in {folder}: {[k for k in need if k not in found]}")
    return {"years": {y: (found[("AA", y)], found[("BB", y)]) for y in (ry, py)}, "counties": found["counties"],
            "dd1": found["DD1"], "ff": found["FF"], "addendum_b": None, "released": meta.get("cms_release")}


@pytest.mark.skipif(not CMS_DIR, reason="CMS's files are not here (set REMITWATCH_ASC_CMS)")
def test_the_tables_equal_cms_own_files():
    """Every code (indicator, rate, copayment, discounting, comment indicator), county (CBSA, wage index), indicator
    definition and Addendum FF APC in the tables, and ascfs.py, against CMS's CSVs read with the build's own check."""
    meta = ascrates.tables().meta
    cms = _cms_files_in(CMS_DIR, meta)
    assert B.check_against_cms(os.path.join(ROOT, "remitwatch", "ascfs.py"), str(ascrates.DATA), cms) == []


def test_amounts_agree_with_the_workbook_to_the_cent():
    where = ascrates.place("IN", "WARRICK")
    for code, (nat, loc) in WARRICK.items():
        r = ascrates.row(code, where)
        assert tuple(r["national"][k] for k in KEYS) == nat, code
        assert tuple(r["local"][k] for k in KEYS) == loc, code
    sf = ascrates.place("CA", "SAN FRANCISCO")
    assert sf["wi"] == Decimal("1.8217")
    for code, loc in SAN_FRANCISCO.items():
        assert tuple(ascrates.row(code, sf)["local"][k] for k in KEYS) == loc, code
    # packaged: no amount of its own; the drug and the non-opioid are not wage-adjusted
    r = ascrates.row("17312", where)
    assert r["pi"] == "N1" and r["national"] is None and r["local"] is None
    assert not ascrates.row("J0121", where)["adjusted"] and ascrates.row("14040", where)["adjusted"]


def test_the_year_before_and_what_is_new():
    where = ascrates.place("IN", "WARRICK")
    r = ascrates.row("14040", where, ascrates.office_wage_index("newburgh", "2026"))
    assert r["prior"] == {"pi": "A2", "rate": 112857, "local_total": 111317} and r["change"] == 105608 - 112857
    assert r["change_pct"] == round(105608 / 112857 - 1, 4)
    # as the workbook marks them: a new code's comment indicator, a revised one's; a code dropped from the 2027 addenda
    assert ascrates.status("C9818") == "New" and ascrates.status("0102T") == "Revised" and ascrates.status("14040") == ""
    gone = ascrates.row("0967T", where)
    assert gone["listed"] is False and gone["prior"]["pi"] == "J8" and "local" not in gone


def test_search_takes_codes_starts_and_ranges():
    find = lambda q, **kw: [r["code"] for r in ascrates.lookup(q, "IN", "WARRICK", **kw)["rows"]]
    assert find("14040") == ["14040"] and find("j0121, 14040") == ["14040", "J0121"]
    assert find("1404") == ["14040", "14041"]
    assert find("11600-11606") == ["11600", "11601", "11602", "11603", "11604", "11606"]
    assert set(find("116-117", limit=500)) >= {"11600", "11646", "11719", "11770"} and not any(c.startswith("118") for c in find("116-117", limit=500))
    assert find("not-a-code ;;") == [] and find("99999") == []
    everything = ascrates.lookup("", limit=50, offset=100)
    assert everything["total"] == 8225 and len(everything["rows"]) == 50 and everything["offset"] == 100
    assert ascrates.lookup("1404")["place"] is None and ascrates.lookup("1404")["rows"][0]["local"] is None   # no county: national only
    out = ascrates.lookup("14040", "IN", "WARRICK")
    assert out["place"]["wi"] == "0.9555" and out["place"]["prior_wi"] == "0.9727"
    assert ascrates.lookup("14040", "CA", "SAN FRANCISCO")["place"]["prior_wi"] is None       # not an office: no 2026 index


def test_the_lab_table_agrees_with_the_full_table():
    """ascfs.py (the lab's dermatology codes) and the full table come out of one build; every code agrees."""
    t = ascrates.tables()
    n = 0
    for y, codes in ASC.items():
        for c, (pi, rate, mpd) in codes.items():
            r = t.rates[c][y]
            assert (r["pi"], r["rate"], r["mpd"] == "Y") == (pi, rate, mpd), (y, c)
            n += 1
    assert n == 407
    # the lab prices the ASC the way Medicare's pricer does (the whole rate wage-adjusted): within a cent of the workbook
    where = ascrates.place("IN", "WARRICK")
    for c in ASC["2027"]:
        a, r = asc_amount("2027", "newburgh", c), ascrates.row(c, where)
        if a is not None:
            assert abs(a - r["local"]["total"]) <= 1, c


def test_the_lookup_does_not_need_ascfs_office_county(tmp_path, monkeypatch):
    """The offices' counties come from the tables' own asc_meta.json: an ascfs.py written without OFFICE_COUNTY (as the
    older --aa route wrote it) leaves the lookup, the picker's home county and the view's payload working."""
    from remitwatch import ascfs
    monkeypatch.delattr(ascfs, "OFFICE_COUNTY")
    assert ascrates.offices() == B.OFFICES and ascrates.office_place("newburgh")["county"] == "WARRICK"
    assert ascrates.lookup("14040", "IN", "WARRICK")["place"]["prior_wi"] == "0.9727"
    c = sqlite3.connect(_store(tmp_path))
    try:
        assert calculator.asc_rule(c, 6, "2026-01", "2026-06")["site"]["county"] == "WARRICK"
    finally:
        c.close()


# --------------------------------------------------------------------------- the build, on small invented files
# One rule year (2027) and the year before (October 2026), laid out as CMS lays its addenda out. Every amount is made up.
AA = {  # code, marker, subject to discounting, comment indicator, payment indicator, rate
    "2027": [("14040", "", "Y", "", "A2", "$1,000.00"), ("17311", "", "N", "", "P3", "$300.00"), ("17312", "", "N", "", "N1", ""),
             ("11643", "", "Y", "", "P3", "$250.50"), ("G0105", "**", "Y", "", "A2", "$500.00"), ("27417", "", "Y", "NC", "J8", "$9,000.00"),
             ("C1234", "", "Y", "", "G2", "$400.00")],
    "2026": [("14040", "", "Y", "", "A2", "$1,100.00"), ("17311", "", "N", "", "P3", "$290.00"), ("17312", "", "N", "", "N1", ""),
             ("11643", "", "Y", "", "P3", "$240.00"), ("15756", "", "Y", "", "A2", "$800.00"), ("G0105", "**", "Y", "", "A2", "$490.00")],
}
BB = {  # code, marker, comment indicator, payment indicator, rate, inflation-adjusted copayment
    "2027": [("J0121", "", "", "K2", "$4.29", "$0.84"), ("C9818", "***", "NI", "K2", "$0.31", ""), ("Q4101", "", "", "S2", "$100.00", ""),
             ("C1234", "", "", "K2", "$5.00", "")],
    "2026": [("J0121", "", "", "K2", "$4.10", "$0.80"), ("Q4101", "", "", "S2", "$100.00", "")],
}
OFF = list(B.OFFICES.items())
COUNTIES = [  # FIPS, mark, county, state, CBSA and name before, final CBSA and name, wage index before, unadjusted, final
    (OFF[0][1], "", "NORTHWOOD", "IN", "21780", "Somewhere, IN", "21780", "Somewhere, IN", "0.9700", "0.9500", "0.9500"),
    (OFF[1][1], "", "RIVERBEND", "KY", "36980", "Elsewhere, KY", "36980", "Elsewhere, KY", "0.9000", "0.8600", "0.8600"),
    (OFF[2][1], "**", "HILLCREST", "KY", "50007", "Hillcrest County", "18", "KENTUCKY", "0.8300", "0.8100", "0.8100"),
    ("13901", "**", "LAKESIDE", "GA", "50002", "Lakeside County", "50002", "Lakeside County", "0.9100", "0.7700", "0.8600"),
    ("18990", "", "STATEWIDE", "IN", "15", "INDIANA", "15", "INDIANA", "0.8500", "0.8400", "0.8400"),
    ("66990", "", "STATEWIDE", "GU", "65", "GUAM", "65", "GUAM", "0.9600", "0.9600", "0.9600"),
]
DD1 = [("A2", "Surgical procedure on ASC list"), ("G2", "Non office-based surgical procedure"), ("J8", "Device-intensive procedure"),
       ("K2", "Drugs and biologicals paid separately"), ("N1", "Packaged service/item; no separate payment made."),
       ("P3", "Office-based surgical procedure"), ("S2", "Skin substitute supply group")]
FF = [("27417", "J8", "5115"), ("14040", "A2", "5054")]


def _lab(y: str) -> str:
    return f"Final CY {y}" if y == "2027" else f"October {y}"


def _csv(path, grid) -> str:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(grid)
    return str(path)


def _cms_files(tmp, counties=COUNTIES) -> dict:
    """CMS's CSVs, as CMS writes them: title lines, the header, the codes, notes at the bottom."""
    f = {}
    for y in ("2027", "2026"):
        ci = y == "2027"                                  # a quarter's files have no comment indicator
        head = ["HCPCS Code", "", "Short Descriptor", "Subject to Multiple Procedure Discounting"] + ([f"{_lab(y)} Comment Indicator"] if ci else []) \
            + [f"{_lab(y)}  Payment Indicator", f"{_lab(y)}  Payment Weight  ", f"{_lab(y)} Payment Rate "]
        body = [[c, m, "x", mpd] + ([k] if ci else []) + [pi, "", rate] for c, m, mpd, k, pi, rate in AA[y]]
        f[("AA", y)] = _csv(tmp / f"AA{y}.csv", [[f"Addendum AA -- invented, {y}"], ["code numbers and amounts only"], []] + [head] + body + [["NOTE 1: an invented note"]])
        head = ["HCPCS Code", "", "Short Descriptor"] + ([f"CY {y} Comment Indicator"] if ci else []) + \
            ["Drug and Device Pass-Through Expiration during Calendar Year", f"{_lab(y)} Payment Indicator", f"{_lab(y)} Payment Weight  ",
             f"{_lab(y)} Payment Rate ", f"{_lab(y)} IRA-22 Adjusted Copayment 1"]
        body = [[c, m, "x"] + ([k] if ci else []) + ["", pi, "", rate, ira] for c, m, k, pi, rate, ira in BB[y]]
        f[("BB", y)] = _csv(tmp / f"BB{y}.csv", [[f"Addendum BB -- invented, {y}"], ["code numbers and amounts only"], []] + [head] + body + [["NOTE 4: (***) invented"]])
    head = ["FIPS Code", "", "County Name", "State", "CY 2026 CBSA", "CY 2026 CBSA Name", "Final CY 2027 CBSA", "Final CY 2027 CBSA Name",
            "CY 2026 ASC Wage Index", "Unadjusted CY 2027 ASC Wage Index", "Final CY 2027 ASC Wage Index with 5% Cap"]
    f["counties"] = _csv(tmp / "crosswalk.csv", [head] + [list(r) for r in counties] + [[], ["Notes: (**) a transition CBSA, invented"]])
    f["dd1"] = _csv(tmp / "DD1.csv", [["Addendum DD1 -- invented"], ["Indicator", "Payment Indicator Definition"]] + [list(r) for r in DD1])
    f["ff"] = _csv(tmp / "FF.csv", [["Addendum FF -- invented"], [], [], ["HCPCS Code", "", "Short Descriptor", "Final CY 2027  Payment Indicator",
                                                                          "Final CY 2027 APC", "Final CY 2027 APC Device Offset Percentage"]]
                   + [[c, "", "x", pi, apc, ""] for c, pi, apc in FF])
    return f


def _cms_args(f: dict) -> list[str]:
    return ["--cms", f"2027={f[('AA', '2027')]},{f[('BB', '2027')]}", "--cms", f"2026={f[('AA', '2026')]},{f[('BB', '2026')]}",
            "--counties", f["counties"], "--dd1", f["dd1"], "--ff", f["ff"], "--released", "2026-11-10"]


def _cms_dict(f: dict) -> dict:
    return {"years": {y: (f[("AA", y)], f[("BB", y)]) for y in ("2027", "2026")}, "counties": f["counties"], "dd1": f["dd1"],
            "ff": f["ff"], "addendum_b": None, "released": "2026-11-10"}


def _read(path) -> dict:
    with open(path, newline="", encoding="utf-8") as fh:
        return {r["code"]: r for r in csv.DictReader(fh)} if str(path).endswith("asc_rates.csv") else \
            {(r["state"], r["county"]): r for r in csv.DictReader(fh)}


def _snapshot(*paths) -> dict:
    return {str(p): p.read_bytes() for p in paths}


def test_the_build_from_cms_files(tmp_path):
    f = _cms_files(tmp_path)
    out, data = tmp_path / "ascfs.py", tmp_path / "cms"
    B.main(_cms_args(f) + ["--out", str(out), "--data-dir", str(data)])
    rates = _read(data / "asc_rates.csv")
    # Addendum BB first, then AA on top: a code on both is AA's
    assert (rates["C1234"]["add_2027"], rates["C1234"]["pi_2027"], rates["C1234"]["rate_2027"]) == ("AA", "G2", "40000")
    # the copayment: 20% of the rate to the cent, none for a '**' preventive service, 20% for a '***' non-opioid, a drug's
    # inflation-adjusted copayment where Addendum BB gives one
    assert (rates["14040"]["copay_2027"], rates["11643"]["copay_2027"], rates["G0105"]["copay_2027"]) == ("20000", "5010", "0")
    assert (rates["C9818"]["copay_2027"], rates["J0121"]["copay_2027"], rates["J0121"]["copay_2026"]) == ("6", "84", "80")
    assert rates["17312"]["rate_2027"] == "" and rates["15756"]["pi_2027"] == "" and rates["15756"]["rate_2026"] == "80000"
    assert (rates["27417"]["apc"], rates["27417"]["ci_2027"], rates["14040"]["apc"]) == ("5115", "NC", "5054")     # Addendum FF
    counties = _read(data / "asc_counties.csv")
    # the final wage index after the 5% cap, with the transition CBSA that carries it; a rural area as 'RURAL <STATE>'
    assert (counties[("GA", "LAKESIDE")]["cbsa"], counties[("GA", "LAKESIDE")]["wi_2027"]) == ("50002", "0.8600")
    assert counties[("KY", "HILLCREST")]["cbsa_name"] == "RURAL KENTUCKY" and counties[("KY", "HILLCREST")]["cbsa"] == "18"
    # a state with no county of its own, by its name; a state's rural area is not a county
    assert counties[("GU", "GUAM")]["fips"] == "" and counties[("GU", "GUAM")]["wi_2027"] == "0.9600"
    assert ("IN", "STATEWIDE") not in counties and ("IN", "INDIANA") not in counties and len(counties) == 5
    meta = json.loads((data / "asc_meta.json").read_text(encoding="utf-8"))
    assert (meta["cms_release"], meta["checked_against_cms"]) == ("2026-11-10", True)
    assert meta["source"] == "CMS's CY 2027 final rule addenda as posted on November 10, 2026"
    assert meta["years"] == {"2026": "October 2026", "2027": "the CY 2027 final rule"} and "S2" in meta["not_wage_adjusted"]
    assert meta["files"]["2027"] == ["AA2027.csv", "BB2027.csv"] and meta["pi"]["S2"] == "Skin substitute supply group"
    # the offices' wage index both years, from CMS's crosswalk (the year before's column too)
    assert {o: v["wage_index"] for o, v in meta["offices"].items()} == {
        OFF[0][0]: {"2026": 0.97, "2027": 0.95}, OFF[1][0]: {"2026": 0.9, "2027": 0.86}, OFF[2][0]: {"2026": 0.83, "2027": 0.81}}
    mod = runpy.run_path(str(out))
    assert mod["OFFICE_COUNTY"] == B.OFFICES and mod["EXCLUDED"] == {"2026": [], "2027": ["15756"]}
    assert mod["ASC"]["2027"]["14040"] == ("A2", 100000, True) and "G0105" not in mod["ASC"]["2027"]
    assert mod["asc_amount"]("2027", OFF[0][0], "14040") == 97500 and mod["asc_amount"]("2027", OFF[0][0], "17312") is None
    # the day CMS posted the addenda has to be given, as a date
    with pytest.raises(SystemExit, match="--released"):
        B.main(_cms_args(f)[:-2] + ["--out", str(tmp_path / "x.py"), "--data-dir", str(tmp_path / "x")])
    assert not (tmp_path / "x.py").exists()


def test_the_check_reads_cms_files_again_and_names_each_difference(tmp_path, monkeypatch):
    f = _cms_files(tmp_path)
    out, data = tmp_path / "ascfs.py", tmp_path / "cms"
    B.main(_cms_args(f) + ["--out", str(out), "--data-dir", str(data)])
    cms = _cms_dict(f)
    assert B.check_against_cms(str(out), str(data), cms) == []
    kept = _snapshot(out, *(data / n for n in B.TABLES))
    # a reading that does not agree with CMS's files: the build stops and nothing is replaced
    monkeypatch.setattr(B, "copay_of", lambda rate, marker, ira: None if rate is None else rate // 4)
    with pytest.raises(SystemExit, match="differ from CMS's own files") as e:
        B.main(_cms_args(f) + ["--out", str(out), "--data-dir", str(data)])
    assert "2027 14040: copay 25000 (CMS: 20000)" in str(e.value) and "nothing was written" in str(e.value)
    assert _snapshot(out, *(data / n for n in B.TABLES)) == kept
    monkeypatch.undo()
    # the tables changed after the build: a rate, a county's wage index, a code dropped
    grid = lambda p: list(csv.reader(p.read_text(encoding="utf-8").splitlines()))
    rows = grid(data / "asc_rates.csv")
    for r in rows:
        if r[0] == "14040":
            r[rows[0].index("rate_2027")] = "99999"
    _csv(data / "asc_rates.csv", [r for r in rows if r[0] != "C9818"])
    _csv(data / "asc_counties.csv", [r[:5] + ["0.7700"] if r[1] == "LAKESIDE" else r for r in grid(data / "asc_counties.csv")])
    diffs = B.check_against_cms(str(out), str(data), cms)
    assert "2027 14040: rate 99999 (CMS: 100000)" in diffs and "2027 C9818: on CMS's Addendum BB (K2), not in the tables" in diffs
    assert "county 13901 LAKESIDE, GA: wage index 0.7700 (CMS: 0.8600)" in diffs
    assert any(d.startswith("ascfs.py 2027 14040:") for d in diffs)


def _workbook(path, counties=COUNTIES, aa=AA, formula='=IF(OR(C4 = "H2", C4 = "K2", C4 = "Z9"), "Geographic Wage Adjustment Not Applied", "")'):
    """ASCA's workbook as version 77 lays it out (sheet names, header rows, its own helper columns among CMS's), small."""
    openpyxl = pytest.importorskip("openpyxl")
    num = lambda s: float(s.replace("$", "").replace(",", "")) if s else None
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for y, tag in (("2027", "CY 2027"), ("2026", "Oct 2026")):
        ci = y == "2027"
        ws = wb.create_sheet(f"{tag} ASC AA")
        for r in ([f"Addendum AA -- invented, {y}"], ["amounts only"], []):
            ws.append(r)
        # the workbook's own columns ('for lookup', 'Check', 'Length') sit before and among CMS's, with other values in them
        ws.append(["HCPCS Code", None, "Short Descriptor", "Payment Rate for lookup", "Subject to Multiple Procedure Discounting for lookup",
                   "Subject to Multiple Procedure Discounting"] + ([f"{_lab(y)} Comment Indicator"] if ci else [])
                  + [f"{_lab(y)}  Payment Indicator", f"{_lab(y)}  Payment Weight  ", f"{_lab(y)} Payment Rate ", "Check HCPCS Code for duplicates", "Length of HCPCS Code"])
        for c, m, mpd, k, pi, rate in aa[y]:
            ws.append([c, m or None, "x", 1.0, "N" if mpd == "Y" else "Y", mpd] + ([k or None] if ci else []) + [pi, None, num(rate), 1, 5])
        ws = wb.create_sheet(f"{tag} ASC BB")
        for r in ([f"Addendum BB -- invented, {y}"], ["amounts only"], []):
            ws.append(r)
        ws.append(["HCPCS Code", None, "Short Descriptor"] + ([f"CY {y} Comment Indicator"] if ci else [])
                  + ["Drug and Device Pass-Through Expiration during Calendar Year", f"{_lab(y)} Payment Indicator", f"{_lab(y)} Payment Weight  ",
                     f"{_lab(y)} Payment Rate ", f"{_lab(y)} IRA-22 Adjusted Copayment 1", "Check if code is on Addendum AA", "IRA-22 Adjusted Copayment"])
        for c, m, k, pi, rate, ira in BB[y]:
            ws.append([c, m or None, "x"] + ([k or None] if ci else []) + [None, pi, None, num(rate), num(ira), 1, 9.99])
    ws = wb.create_sheet("CY 2027 ASC DD1")
    for r in [["Addendum DD1 -- invented"], ["Indicator", "Payment Indicator Definition"]] + [list(r) for r in DD1]:
        ws.append(r)
    ws = wb.create_sheet("2027_ASC_Wage_Index")
    ws.append(["CBSA", "CBSA Name", "WI27"])
    for cb, name, wi in sorted({(r[6], r[7], float(r[10])) for r in counties}):
        ws.append([cb, name, wi])
    ws = wb.create_sheet("2027_FR_Cnty_to_CBSA_Xwalk")
    for r in ([None, None, None, None, "<-- from the crosswalk --|", "|-- added by ECG -->"], [], ["FY 2027 FR Crosswalk"]):
        ws.append(r)
    ws.append(["FIPS County Code", "County Name", "State", "FY 2025 CBSA ", "FY 2025 CBSA Name ", "State Name (spaces removed)", "Check for missing Counties",
               "County_State_1", "County_State_2", "CBSA for calculator", "CBSA Name for calculator", "CY 2027 Final Rule ASC Wage Index for calculator"])
    for fips, _, county, st, _, _, cb, name, _, _, wi in counties:
        if county != "STATEWIDE":
            ws.append([fips, county, st, None, None, st, 0, f"{county}, {st}", f"{county}, {st}", cb, f"RURAL {name}" if len(cb) <= 2 else name, float(wi)])
    ws = wb.create_sheet("2027 FR Addendum B")
    for i in range(6):
        ws.append([None, f"note {i + 1}"])
    ws.append(["HCPCS Code", "Short Descriptor", " CI", " SI", " APC ", "Relative Weight", "APC for lookup"])
    for c, apc in (("14040", "5054"), ("Q4101", "6000"), ("J0121", "9999")):
        ws.append([c, "x", None, "T", apc, None, "0000"])
    ws = wb.create_sheet("States")
    ws.append(["State Name", "State Name (spaces removed)", "State Abbreviation"])
    for st, name in (("GA", "GEORGIA"), ("GU", "GUAM"), ("IN", "INDIANA"), ("KY", "KENTUCKY"), ("VI", "VIRGIN ISLANDS")):
        ws.append([name, name.replace(" ", "_"), st])
    ws = wb.create_sheet("Pay_Rates_2027")
    for r in ([], ["HCPCS"], ["code"], ["0095T"] + [None] * 11 + [formula]):
        ws.append(r)
    wb.save(path)
    return str(path)


def test_the_workbook_reader(tmp_path):
    w = B.read_workbook(_workbook(tmp_path / "calculator.xlsx"))
    assert (w["year"], w["prior"], w["releases"]) == ("2027", "2026", {"2027": "final rule", "2026": "Oct"})
    t, p = w["tables"]["2027"], w["tables"]["2026"]
    # CMS's columns by their headers, never the workbook's own ('for lookup' holds 1.00 and the opposite discounting)
    assert (t["14040"]["rate"], t["14040"]["mpd"], t["11643"]["rate"], p["14040"]["rate"]) == (100000, "Y", 25050, 110000)
    assert (t["27417"]["ci"], t["17312"]["rate"], t["17312"]["copay"]) == ("NC", None, None)
    # '**' exactly waives the copayment; '***' (a non-opioid alternative) keeps it; a drug's adjusted copayment from BB
    assert (t["G0105"]["copay"], t["C9818"]["copay"], t["J0121"]["copay"]) == (0, 6, 84)
    # Addendum BB first, then AA on top
    assert (t["C1234"]["add"], t["C1234"]["pi"], t["C1234"]["rate"]) == ("AA", "G2", 40000)
    # Guam has no county in the crosswalk: wage-indexed by its name, as the workbook does; the Virgin Islands have no
    # area of that name here, so no row
    assert ("GU", "GUAM", "", "65", "GUAM", 0.96) in w["counties"] and not any(c[0] == "VI" for c in w["counties"])
    assert ("GA", "LAKESIDE", "13901", "50002", "Lakeside County", 0.86) in w["counties"]
    # the indicators left unadjusted, read from the workbook's own formula; the APCs from its copy of Addendum B
    assert w["no_wage"] == ("H2", "K2", "Z9") and w["apc"] == {"14040": "5054", "Q4101": "6000", "J0121": "9999"}
    assert w["pi"]["N1"].startswith("Packaged") and dict(w["states"])["GU"] == "GUAM"
    # with no formula to read, the list the calculator is known to use
    assert B.read_workbook(_workbook(tmp_path / "plain.xlsx", formula="none"))["no_wage"] == B.NOT_WAGE_ADJUSTED


def test_the_workbook_is_held_against_cms_files(tmp_path):
    f = _cms_files(tmp_path)
    out, data = tmp_path / "ascfs.py", tmp_path / "cms"
    B.main(["--asca", _workbook(tmp_path / "current.xlsx")] + _cms_args(f) + ["--out", str(out), "--data-dir", str(data)])
    meta = json.loads((data / "asc_meta.json").read_text(encoding="utf-8"))
    assert meta["checked_against_cms"] is True and meta["source"] == \
        "ASCA's rate calculator, current.xlsx, equal to CMS's CY 2027 final rule addenda as posted on November 10, 2026"
    assert meta["not_wage_adjusted"] == ["H2", "K2", "S2", "Z9"]           # the workbook's own list, and S2 as CMS pays it
    # the same tables as from CMS's files alone (the APCs apart: the workbook's Addendum B gives more of them)
    from_cms = tmp_path / "cms_only"
    B.main(_cms_args(f) + ["--out", str(tmp_path / "ascfs_cms.py"), "--data-dir", str(from_cms)])
    strip = lambda d: {k: {c: v for c, v in r.items() if c != "apc"} for k, r in d.items()}
    assert strip(_read(data / "asc_rates.csv")) == strip(_read(from_cms / "asc_rates.csv"))
    assert _read(data / "asc_counties.csv") == _read(from_cms / "asc_counties.csv")
    # a workbook behind CMS's posting (as version 77 was): a code CMS has since priced, a county the cap holds up
    kept = _snapshot(out, *(data / n for n in B.TABLES))
    stale_aa = {"2027": [r if r[0] != "27417" else ("27417", "", "Y", "NC", "J8", "") for r in AA["2027"]], "2026": AA["2026"]}
    stale_xw = [r if r[2] != "LAKESIDE" else r[:6] + ("11", "GEORGIA", r[8], r[9], "0.7700") for r in COUNTIES]
    with pytest.raises(SystemExit) as e:
        B.main(["--asca", _workbook(tmp_path / "v77.xlsx", counties=stale_xw, aa=stale_aa)] + _cms_args(f) + ["--out", str(out), "--data-dir", str(data)])
    msg = str(e.value)
    assert "differ from CMS's own files in 2 places; nothing was written" in msg and "ASCA's workbook is not CMS's latest posting" in msg
    assert "2027 27417: rate None (CMS: 900000), copay None (CMS: 180000)" in msg
    assert "county 13901 LAKESIDE, GA: cbsa 11 (CMS: 50002), wage index 0.7700 (CMS: 0.8600)" in msg
    assert _snapshot(out, *(data / n for n in B.TABLES)) == kept
    # compared unless --unchecked is said, and then the tables say so
    with pytest.raises(SystemExit, match="--unchecked"):
        B.main(["--asca", str(tmp_path / "current.xlsx"), "--out", str(out), "--data-dir", str(data)])
    B.main(["--asca", str(tmp_path / "v77.xlsx"), "--wage", "2026:" + ",".join(f"{o}=0.9" for o in B.OFFICES), "--unchecked",
            "--out", str(tmp_path / "u.py"), "--data-dir", str(tmp_path / "u")])
    meta = json.loads((tmp_path / "u" / "asc_meta.json").read_text(encoding="utf-8"))
    assert meta["checked_against_cms"] is False and meta["source"] == "ASCA's rate calculator, v77.xlsx, not compared with CMS's own files"


def test_the_older_aa_route_writes_office_county(tmp_path):
    """--aa YEAR=AA.csv[,EE.csv] writes ascfs.py alone: with the offices' counties (OFFICE_COUNTY), and only for the years
    the rate lookup's tables hold, so that ascfs.py and remitwatch/cms cannot drift apart."""
    f = _cms_files(tmp_path)
    ee = {y: _csv(tmp_path / f"EE{y}.csv", [["Addendum EE -- invented"], [], [], ["HCPCS Code", "Short Descriptor"], ["15760", "x"], ["00176", "x"]])
          for y in ("2026", "2027")}
    data = tmp_path / "cms"
    data.mkdir()
    (data / "asc_meta.json").write_text(json.dumps({"prior_year": "2026", "rule_year": "2027"}), encoding="utf-8")
    wage = lambda y, w: ["--wage", f"{y}:" + ",".join(f"{o}={w}" for o in B.OFFICES)]
    out = tmp_path / "ascfs_aa.py"
    B.main(["--aa", f"2026={f[('AA', '2026')]},{ee['2026']}", "--aa", f"2027={f[('AA', '2027')]},{ee['2027']}", *wage("2026", 0.97),
            *wage("2027", 0.95), "--out", str(out), "--data-dir", str(data)])
    mod = runpy.run_path(str(out))
    assert mod["OFFICE_COUNTY"] == B.OFFICES
    assert mod["ASC"]["2027"]["14040"] == ("A2", 100000, True) and mod["ASC"]["2026"]["15756"] == ("A2", 80000, True)
    assert mod["EXCLUDED"] == {"2026": ["15760"], "2027": ["15760"]} and mod["asc_amount"]("2027", OFF[0][0], "14040") == 97500
    # one year only, or other years than the tables': refused, nothing written
    with pytest.raises(SystemExit, match="build both from CMS's files"):
        B.main(["--aa", f"2027={f[('AA', '2027')]}", *wage("2027", 0.95), "--out", str(tmp_path / "x.py"), "--data-dir", str(data)])
    with pytest.raises(SystemExit, match="--wage"):
        B.main(["--aa", f"2026={f[('AA', '2026')]}", "--aa", f"2027={f[('AA', '2027')]}", *wage("2027", 0.95),
                "--out", str(tmp_path / "x.py"), "--data-dir", str(data)])
    assert not (tmp_path / "x.py").exists()


# --------------------------------------------------------------------------- the Calculator's ASC view
def _store(tmp_path):
    import test_calculator as T
    path = T._db(tmp_path)
    c = sqlite3.connect(path)
    cases = {"medicare": {"mohs": [["IN", "asc", "14040 17311", 3, 1.5], ["IN", "clinic", "13132 17311", 2, 0], ["IN", "both", "13132 14040 17311", 1, 0]],
                          "excision": [["IN", "asc", "11643 13132", 1]]},
             "commercial": {"mohs": [["IN", "asc", "15260 17311", 2, 0]], "excision": []}}
    for kind, v in cases.items():
        c.execute("UPDATE provider_payers SET asc_cases=? WHERE provider_id=? AND month='2026-03' AND payer_kind=?", (json.dumps(v), T.DOC, kind))
    c.execute("UPDATE provider_payers SET asc_cases=? WHERE provider_id=? AND month='2026-04' AND payer_kind='medicare'",
              (json.dumps({"mohs": [["IN", "asc", "14040 17311", 1, 0]], "excision": []}), T.PA))
    # what the commercial plan pays the surgeons against Medicare for these codes: 120%
    c.executemany("INSERT OR REPLACE INTO payer_rates VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  [("Plan A", "commercial", "2026", code, "physician", round(med * 1.2), 0, 0, 0, 30, 1.0, med, 1.2, "office")
                   for code, med in (("14040", 76000), ("17311", 61592), ("15260", 88000))])
    c.commit()
    c.close()
    return path


def test_asc_rule_payload(tmp_path):
    c = sqlite3.connect(_store(tmp_path))
    try:
        d = calculator.asc_rule(c, 6, "2026-01", "2026-06")
    finally:
        c.close()
    assert (d["from"], d["to"], d["months"], d["per_year"]) == ("2026-01", "2026-06", 6, 2.0)
    assert d["years"] == ["2026", "2027"] and d["prior_year"] == "2026" and d["rule_year"] == "2027"
    assert d["labels"] == {"2026": "October 2026", "2027": "the CY 2027 final rule"}
    assert d["source"] == "CMS's CY 2027 final rule addenda as posted on November 10, 2026"
    s = d["site"]
    assert (s["office"], s["county"], s["cbsa"], s["wage_index"]) == ("newburgh", "WARRICK", "21780", {"2026": 0.9727, "2027": 0.9555})
    # every clinician's cases pooled (the physician's and the APP's), by codes, site, kind of payer and state
    C = d["cases"]
    assert C["from"] == "provider_payers"
    assert C["mohs"] == [["13132 14040 17311", "both", "medicare", "IN", 1, 0], ["13132 17311", "clinic", "medicare", "IN", 2, 0],
                         ["14040 17311", "asc", "medicare", "IN", 4, 1.5], ["15260 17311", "asc", "commercial", "IN", 2, 0]]
    assert C["excision"] == [["11643 13132", "asc", "medicare", "IN", 1, 1]]
    # what the commercial plan pays the surgeons against Medicare, measured on these codes
    assert d["kinds"]["commercial"] == {"ratio": 1.2, "lines": 90, "year": "2026", "basis": "codes"}
    # both years' ASC rates at the ASC's office, and Medicare's physician amounts to price the surgeons' side
    A = d["asc"]
    assert A["2027"]["14040"] == ["A2", True, {o: asc_amount("2027", o, "14040") for o in WAGE_INDEX["2027"]}]
    assert A["medicare"]["17311"]["2027"] == list(MPFS["2027"]["IN"]["17311"]) and "11643" in A["mppr"] and "17312" in A["add_on"]
    assert d["codes"]["14040"] == {"2026": {"pi": "A2", "mpd": True, "national": 112857, "local": 111317},
                                   "2027": {"pi": "A2", "mpd": True, "national": 105608, "local": 103258}}
    assert d["codes"]["17312"]["2027"] == {"pi": "N1", "mpd": False, "national": None, "local": None}
    assert set(d["pi"]) >= {"A2", "P3", "N1"} and json.dumps(d)


def _node_eval(d, settings):
    js = ("const AM=require(process.argv[1]);const d=JSON.parse(process.argv[2]);const s=JSON.parse(process.argv[3]);"
          "const merge=x=>{if(x===null)return null;const s0=AM.defaults(d);for(const k of Object.keys(x.kinds||{}))Object.assign(s0.kinds[k],x.kinds[k]);"
          "for(const k of ['own','ascqr'])if(k in x)s0[k]=x[k];return s0;};"
          "const out=s.map(x=>{const v=AM.evaluate(d,merge(x));return {total:v.total,kinds:v.kinds,"
          "medicare:v.medicare,own:v.own,pro:v.pro,types:v.types,codes:v.codes,split:v.split,unpriced:v.unpriced,missing:v.missing,n:v.n};});"
          "process.stdout.write(JSON.stringify({out, defaults: AM.defaults(d, {own: 0.4})}));")
    res = subprocess.run([NODE, "-e", js, os.path.join(ROOT, "remitwatch", "static", "asc_model.js"), json.dumps(d), json.dumps(settings)],
                         capture_output=True, text=True, check=True, cwd=ROOT)
    return json.loads(res.stdout)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_view_prices_the_cases_as_medicare_does(tmp_path):
    """The view's arithmetic against figures worked out here from the tables alone: the ASC's claim (the highest
    procedure in full, every other one subject to discounting at half), the surgeon's (the 50% rule across the clinic's
    and the ASC's claims, the extra stages in full), a plan's share and whether it follows Medicare's rates."""
    c = sqlite3.connect(_store(tmp_path))
    try:
        d = calculator.asc_rule(c, 6, "2026-01", "2026-06")
    finally:
        c.close()
    half = lambda v: int(v * 0.5 + 0.5)                     # JS Math.round on a positive half
    fee = lambda y, c: asc_amount(y, "newburgh", c)
    flap = {y: fee(y, "14040") for y in d["years"]}                                         # the closure alone on the ASC's claim
    exc = {y: max(fee(y, "11643"), fee(y, "13132")) + half(min(fee(y, "11643"), fee(y, "13132"))) for y in d["years"]}
    graft = {y: fee(y, "15260") for y in d["years"]}

    def surgeon(y, closure, stages):
        m, f, s = MPFS[y]["IN"]["17311"][0], MPFS[y]["IN"][closure][1], MPFS[y]["IN"]["17312"][0]
        return max(m, f) + half(min(m, f)) + stages * s

    out = _node_eval(d, [None, {"kinds": {"commercial": {"share": 1.5, "follows": True}}},
                         {"kinds": {"commercial": {"share": 1.5, "follows": False}}}, {"ascqr": True}])
    base, share, fixed, qr = out["out"]
    # a year at the window's pace: 4 Medicare flap cases (two clinicians), 1 excision, 2 commercial grafts, x2
    K = base["kinds"]
    assert K["medicare"]["n"] == 10 and K["commercial"]["n"] == 4 and base["split"] == 2 and base["unpriced"] == 0
    for y in d["years"]:
        assert K["medicare"]["fee"][y] == pytest.approx(8 * flap[y] + 2 * exc[y])
        # the plan's share starts at what it pays the surgeons against Medicare (120%)
        assert K["commercial"]["fee"][y] == pytest.approx(4 * graft[y] * 1.2)
        assert base["total"][y] == pytest.approx(8 * flap[y] + 2 * exc[y] + 4 * graft[y] * 1.2)
        assert base["medicare"]["paid"][y] == pytest.approx(K["medicare"]["fee"][y] * 0.8 * 0.98)
        assert share["kinds"]["commercial"]["fee"][y] == pytest.approx(4 * graft[y] * 1.5)
        # the surgeons' fees for the Medicare cases: 0.75 extra stages a flap case (1.5 over 2 cases in March, none in April)
        assert K["medicare"]["pro"][y] == pytest.approx(2 * (3 * surgeon(y, "14040", 0.5) + surgeon(y, "14040", 0))
                                                        + 2 * (max(MPFS[y]["IN"]["11643"][1], MPFS[y]["IN"]["13132"][1])
                                                               + half(min(MPFS[y]["IN"]["11643"][1], MPFS[y]["IN"]["13132"][1]))))
    # a plan whose contract does not follow Medicare's yearly rates is paid in 2027 what it was paid in 2026
    assert fixed["kinds"]["commercial"]["fee"]["2027"] == pytest.approx(fixed["kinds"]["commercial"]["fee"]["2026"])
    # the ASC failing quality reporting: A2 rates lower (14040, 15260, 13132), P3 unchanged (11643)
    f27 = calculator.ASCQR_FACTOR["2027"]
    rnd = lambda x: int(x + 0.5)                            # JS Math.round on a positive amount
    assert qr["kinds"]["medicare"]["fee"]["2027"] == pytest.approx(8 * rnd(flap["2027"] * f27) + 2 * (rnd(fee("2027", "13132") * f27) + half(fee("2027", "11643"))))
    # per type and per code at Medicare's rates, whoever paid; units a year by code (Mohs codes are not on the ASC's claim)
    assert base["types"]["flap"]["fee"]["2027"] == pytest.approx(8 * flap["2027"]) and base["types"]["graft"]["n"] == 4
    assert base["codes"] == {"14040": 8, "11643": 2, "13132": 2, "15260": 4}
    # the lab's figure for the practice's share starts the view's; the surgeons' share of Medicare is CMS's
    assert out["defaults"]["own"] == 0.4 and out["defaults"]["kinds"]["medicare"]["share"] == 1
    assert out["defaults"]["kinds"]["commercial"]["from"] == "measured" and out["defaults"]["kinds"]["medicaid"]["follows"] is False


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_case_that_cannot_be_priced_is_set_apart():
    d = {"years": ["2026", "2027"], "prior_year": "2026", "rule_year": "2027", "per_year": 1,
         "cases": {"mohs": [["99999 17311", "asc", "medicare", "IN", 3, 0]], "excision": []},
         "asc": {"2026": {}, "2027": {}, "site": {"office": "newburgh", "state": "IN"}, "medicare": {}, "medicare_ky": {}, "mppr": [], "add_on": []},
         "constants": {"mppr_share": 0.5}, "kinds": {}}
    out = _node_eval(d, [None])["out"][0]
    assert out["unpriced"] == 3 and out["total"] == {"2026": 0, "2027": 0} and "99999" in out["missing"]


def _node(js: str, data=None):
    """A snippet of JS run with AM (static/asc_model.js) and DATA (the JSON given); it prints one JSON value."""
    pre = f"const AM=require({json.dumps(os.path.join(STATIC, 'asc_model.js'))});const fs=require('fs');const DATA=JSON.parse(fs.readFileSync(0,'utf8'));"
    res = subprocess.run([NODE, "-e", pre + js], input=json.dumps(data), capture_output=True, text=True, cwd=ROOT, timeout=60)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def _rule(tmp_path, name: str = "counted", counted: bool = True) -> dict:
    """The ASC view's payload on the made-up store (_store), in a folder of its own; counted=False: on a copy of the
    figures built before the provider tables kept the ASC's cases (no provider_payers.asc_cases)."""
    folder = tmp_path / name
    folder.mkdir(exist_ok=True)
    c = sqlite3.connect(_store(folder))
    try:
        if not counted:
            c.execute("ALTER TABLE provider_payers DROP COLUMN asc_cases")
            c.commit()
        return calculator.asc_rule(c, 6, "2026-01", "2026-06")
    finally:
        c.close()


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_cases_not_counted_are_not_read_as_no_case(tmp_path):
    d = _rule(tmp_path, "old", counted=False)
    assert d["cases"] == {"from": None, "mohs": [], "excision": []}
    counted = _rule(tmp_path)
    lines = dict(counted, cases=dict(counted["cases"], **{"from": "lines"}))
    assert _node("process.stdout.write(JSON.stringify(DATA.map(AM.counted)))", [d, counted, lines, {}]) == [False, True, True, False]


def test_kind_ratios_fall_back_to_the_service_families_and_take_the_newest_full_year():
    """What a kind of payer pays the surgeons against Medicare: on the cases' own codes where they have 20 lines or more,
    else on every code of the same service families; in the newest year with 100 rates or more, else the newest."""
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE payer_rates (payer_group TEXT, payer_kind TEXT, year TEXT, code TEXT, tier TEXT, mode_cents INTEGER, "
              "medicare_cents INTEGER, n INTEGER)")
    rows = [("Plan C", "commercial", "2025", "14040", "physician", 150, 100, 5), ("Plan C", "commercial", "2025", "17311", "physician", 120, 100, 5),
            ("Plan C", "commercial", "2025", "13132", "physician", 110, 100, 40),       # a repair, as 14040 is
            ("Plan C", "commercial", "2025", "17312", "physician", 130, 100, 10),       # a Mohs code, as 17311 is
            ("Plan C", "commercial", "2025", "99213", "physician", 200, 100, 50),       # a visit: another family, never counted
            ("State plan", "medicaid", "2025", "14040", "physician", 70, 100, 25),
            ("Plan C", "commercial", "2026", "14040", "physician", 300, 100, 30)]       # a newer year, but only a few rates
    rows += [("Filler", "commercial", "2025", f"9{i:04d}", "nonphysician", 100, 100, 1) for i in range(100)]
    c.executemany("INSERT INTO payer_rates VALUES (?,?,?,?,?,?,?,?)", rows)
    got = calculator._kind_ratios(c, {"14040", "17311"})
    assert got == {"commercial": {"ratio": round(7050 / 6000, 4), "lines": 60, "year": "2025", "basis": "families"},
                   "medicaid": {"ratio": 0.7, "lines": 25, "year": "2025", "basis": "codes"}}
    c.execute("DELETE FROM payer_rates WHERE payer_group = 'Filler'")                # no year with 100 rates: the newest
    assert calculator._kind_ratios(c, {"14040", "17311"}) == {"commercial": {"ratio": 3.0, "lines": 30, "year": "2026", "basis": "codes"}}
    c.close()


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_medicare_advantage_surgeon_fees_follow_the_calculators_ticks(tmp_path):
    """A Medicare Advantage plan unticked on the Calculator ('moves with Medicare in 2027') keeps its 2026 surgeon fees,
    by its share of the Medicare Advantage money; the ticks are read from the Calculator's plan."""
    shares = _node("""process.stdout.write(JSON.stringify([
        AM.maFollowing(DATA.payers, {}), AM.maFollowing(DATA.payers, {'MA Two': false}), AM.maFollowing(DATA.payers, {'MA One': false, 'MA Two': false}),
        AM.maFollowing(DATA.even, {}), AM.maFollowing(DATA.even, {'MA Four': true}), AM.maFollowing({'Plan C': DATA.payers['Plan C']}, {}),
        AM.maFollowing(null, null)]))""",
                   {"payers": {"MA One": {"kind": "medicare_advantage", "share": 0.3, "indexed": True},
                               "MA Two": {"kind": "medicare_advantage", "share": 0.1, "indexed": True},
                               "Plan C": {"kind": "commercial", "share": 0.6, "indexed": False}},
                    "even": {"MA Three": {"kind": "medicare_advantage", "indexed": True}, "MA Four": {"kind": "medicare_advantage", "indexed": False}}})
    assert shares == [1, pytest.approx(0.75), 0, 0.5, 1, None, None]
    c = sqlite3.connect(_store(tmp_path))
    try:
        d = calculator.asc_rule(c, 6, "2026-01", "2026-06")
    finally:
        c.close()
    d["cases"]["mohs"].append(["14040 17311", "asc", "medicare_advantage", "IN", 2, 0])
    moves, held, part, med = [o["kinds"] for o in _node_eval(d, [None, {"kinds": {"medicare_advantage": {"pro_follows": 0}}},
                                                                 {"kinds": {"medicare_advantage": {"pro_follows": 0.25}}},
                                                                 {"kinds": {"medicare": {"pro_follows": 0}}}])["out"]]
    P = lambda k: k["medicare_advantage"]["pro"]
    assert P(moves)["2027"] != pytest.approx(P(moves)["2026"])                 # Medicare's 2027 schedule moves them
    assert P(held)["2027"] == pytest.approx(P(moves)["2026"]) and P(held)["2026"] == pytest.approx(P(moves)["2026"])
    assert P(part)["2027"] == pytest.approx(0.25 * P(moves)["2027"] + 0.75 * P(moves)["2026"])
    # Medicare's own always moves with its schedule; the ASC's facility fees are another contract, untouched
    assert med["medicare"]["pro"] == moves["medicare"]["pro"] and held["medicare_advantage"]["fee"] == moves["medicare_advantage"]["fee"]


def _codes(n: int) -> list[str]:
    """n real codes of five digits from the tables (sorted), none of them a Mohs code."""
    t = ascrates.tables()
    out = [c for c in t.codes if c.isdigit() and len(c) == 5 and not c.startswith("1731")][:n]
    assert len(out) == n
    return out


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_long_search_is_cut_at_whole_codes():
    """67 five-digit codes are 401 characters: cut at 400 the old way, the last became the start of a code and brought in
    codes nobody asked for. Now the search ends at the last whole code and says how many were left out."""
    codes = _codes(250)
    q67 = " ".join(codes[:67])
    assert len(q67) == 401 and q67[:400].split()[-1] == codes[66][:4]
    fits = _node("""process.stdout.write(JSON.stringify([AM.fitQuery(DATA.q, 400, 200), AM.fitQuery(DATA.q, 2400, 200),
        AM.fitQuery(DATA.many, 2400, 200), AM.fitQuery('14040,14041  ,, 11600-11646', 400, 200), AM.fitQuery('', 400, 200),
        AM.fitQuery(DATA.long + ' 14040', 400, 200), AM.fitQuery(null, 400, 200)]))""", {"q": q67, "many": " ".join(codes), "long": "9" * 500})
    narrow, wide, many, seps, empty, long, none = fits
    assert narrow == {"q": " ".join(codes[:66]), "left": 1} and wide == {"q": q67, "left": 0}
    assert many == {"q": " ".join(codes[:200]), "left": 50}
    assert seps == {"q": "14040 14041 11600-11646", "left": 0} and empty == none == {"q": "", "left": 0}
    assert long == {"q": "9" * 400, "left": 1}                    # a first word too long to be a code finds nothing, not everything
    # what the lookup then answers: exactly the whole codes sent
    assert [r["code"] for r in ascrates.lookup(narrow["q"], limit=500)["rows"]] == codes[:66]
    assert ascrates.lookup(long["q"])["total"] == 0


PAGE = r"""
const vm = require('vm');
const calls = [];
const el = () => { const kids = {}; return { innerHTML: '', value: '', isConnected: true, style: {}, dataset: {},
  querySelector(s) { return kids[s] || (kids[s] = el()); }, querySelectorAll() { return []; }, scrollIntoView() {}, addEventListener() {} }; };
// the server, stubbed: the ASC view's payload, the picker, and the rate lookup taking at most DATA.limit characters
const api = async p => {
  const u = new URL(p, 'http://page'), q = u.searchParams.get('q') || '';
  calls.push([u.pathname, q]);
  if (u.pathname === '/api/activity/asc_rule') return Object.assign({ ready: true }, DATA.payload);      // as api._activity adds
  if (u.pathname === '/api/activity/asc_places') return { states: [['IN', 'INDIANA']], counties: { IN: [['WARRICK', '00000']] },
    home: { state: 'IN', county: 'WARRICK' }, pi: {}, rule_year: '2027', prior_year: '2026' };
  if (q.length > DATA.limit) { const e = new Error('String should have at most ' + DATA.limit + ' characters'); e.status = 422; throw e; }
  return { rows: [], total: q.split(' ').filter(Boolean).length, offset: 0, place: null, rule_year: '2027', prior_year: '2026', source: 'CMS' };
};
const ctx = { console, URL, AscModel: AM, api, esc: s => String(s == null ? '' : s), money: c => String(c), money2: c => String(c),
  qs: o => Object.entries(o).filter(([k, v]) => v !== '' && v != null).map(([k, v]) => k + '=' + encodeURIComponent(v)).join('&'),
  document: { getElementById: () => null, createElement: () => ({}), head: { appendChild() {} } },
  requestAnimationFrame: f => f(), cancelAnimationFrame() {},
  state: { tab: 'calculator', calc: { months: 6, view: 'asc', data: { payers: DATA.payers }, x: { indexed: DATA.indexed } } } };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(DATA.page, 'utf8'), ctx);
const v = el();
ctx.ascDraw(v).then(() => process.stdout.write(JSON.stringify({ body: v.querySelector('#calcBody').innerHTML,
  out: v.querySelector('#ascLookOut').innerHTML, calls, ma: ctx.state.calc.asc.s.kinds.medicare_advantage.pro_follows })));
"""
MA_PLANS = {"MA One": {"kind": "medicare_advantage", "share": 0.3, "indexed": True}, "MA Two": {"kind": "medicare_advantage", "share": 0.1, "indexed": True}}


def _page(payload: dict, limit: int, indexed: dict | None = None) -> dict:
    return _node(PAGE, {"page": os.path.join(STATIC, "asc.js"), "payload": payload, "limit": limit, "payers": MA_PLANS, "indexed": indexed or {}})


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_page_says_when_the_cases_are_not_counted(tmp_path):
    p = _page(_rule(tmp_path, "old", counted=False), 400)
    assert "not in these figures yet" in p["body"] and "Sync and analyse now" in p["body"]
    # no tiles of $0, no table of cases or codes that would read as no case at the ASC; the rate calculator still answers
    assert 'id="ascTiles"' not in p["body"] and 'id="ascKinds"' not in p["body"] and 'id="ascCodes"' not in p["body"]
    assert [c[0] for c in p["calls"]] == ["/api/activity/asc_rule", "/api/activity/asc_places", "/api/activity/asc_rates"]
    p = _page(_rule(tmp_path), 400, {"MA Two": False})
    assert 'id="ascTiles"' in p["body"] and "not in these figures yet" not in p["body"]
    # the Calculator's ticks on the Medicare Advantage plans, read at the visit
    assert p["ma"] == pytest.approx(0.75) and "25% of its Medicare Advantage money at plans unticked" in p["body"]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_page_sends_whole_codes_and_falls_back_to_the_shorter_search(tmp_path):
    """The rate calculator starts from the codes of the window's cases: 67 of them make 401 characters (as 12 months did
    on the archive copy)."""
    d = _rule(tmp_path)
    own = sorted(k for k in d["codes"] if not k.startswith("1731"))
    want = sorted(own + [k for k in _codes(120) if k not in own][:67 - len(own)])
    d["codes"] = {**{k: d["codes"].get(k, {}) for k in want}, **{k: v for k, v in d["codes"].items() if k.startswith("1731")}}
    assert len(want) == 67 and len(" ".join(want)) == 401
    # a route that takes 400 characters: the whole search answers 422, then the 66 whole codes that fit are sent
    p = _page(d, 400)
    sent = [q for path, q in p["calls"] if path == "/api/activity/asc_rates"]
    assert sent == [" ".join(want), " ".join(want[:66])]
    assert "the last code in it was left out" in p["out"] and "66 codes" in p["out"]
    # a route that takes the longer search: one request, nothing left out
    p = _page(d, 2400)
    assert [q for path, q in p["calls"] if path == "/api/activity/asc_rates"] == [" ".join(want)] and "left out" not in p["out"]


# --------------------------------------------------------------------------- the routes
PW, OWNER = "correct-horse-battery-7", "owner-staple-orbit-42"


def _client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from remitwatch import api
    monkeypatch.setattr(api, "DB_PATH", _store(tmp_path))
    monkeypatch.setattr(api, "PASSWORD", PW)
    monkeypatch.setattr(api, "USERNAME", "oda")
    monkeypatch.setattr(api, "OWNER_PASSWORD", OWNER)
    return TestClient(api.app), ("oda", PW)


def test_the_page_search_fits_the_route(tmp_path, monkeypatch):
    """static/asc.js sends whole codes: at most ASC_Q_CODES of them (as many as ascrates._match reads) in ASC_Q_WIDE
    characters, or in ASC_Q_MAX after a 422. The route takes one of the two lengths and answers 422 past it."""
    src = open(os.path.join(STATIC, "asc.js"), encoding="utf-8").read()
    codes, wide, narrow = map(int, re.search(r"const ASC_Q_CODES = (\d+), ASC_Q_WIDE = (\d+), ASC_Q_MAX = (\d+);", src).groups())
    assert "e.status !== 422" in src and narrow < wide
    assert ascrates._match(" ".join(["14040"] * codes + ["14041"])) == ["14040"]
    tc, auth = _client(tmp_path, monkeypatch)
    status = lambda n: tc.get("/api/activity/asc_rates", params={"q": "1" * n}, auth=auth, headers={"X-Owner-Password": OWNER}).status_code
    if status(narrow + 1) == 422:
        assert status(narrow) == 200                     # the route as it stands: the page falls back to the shorter search
    else:
        assert status(wide) == 200 and status(wide + 1) == 422


def test_the_routes_are_the_owners(tmp_path, monkeypatch):
    tc, auth = _client(tmp_path, monkeypatch)
    for url in ("/api/activity/asc_rule", "/api/activity/asc_rates?q=14040", "/api/activity/asc_places"):
        assert tc.get(url, auth=auth).status_code == 403, url
        assert tc.get(url).status_code == 401, url
    h = {"X-Owner-Password": OWNER}
    r = tc.get("/api/activity/asc_rule", params={"from": "2026-01", "to": "2026-06"}, auth=auth, headers=h).json()
    assert r["ready"] and r["months"] == 6 and r["cases"]["mohs"]
    r = tc.get("/api/activity/asc_rates", params={"q": "14040 G0105", "state": "IN", "county": "WARRICK"}, auth=auth, headers=h).json()
    assert [x["code"] for x in r["rows"]] == ["14040", "G0105"] and r["rows"][0]["local"]["total"] == 103258
    p = tc.get("/api/activity/asc_places", auth=auth, headers=h).json()
    assert p["home"] == {"state": "IN", "county": "WARRICK"} and p["rule_year"] == "2027" and "WARRICK" in [c for c, _ in p["counties"]["IN"]]
    assert tc.get("/api/activity/asc_rates", params={"state": "Indiana"}, auth=auth, headers=h).status_code == 422
    assert tc.get("/api/activity/asc_rates", params={"limit": 501}, auth=auth, headers=h).status_code == 422
