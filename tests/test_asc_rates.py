"""
Tests for the ASC rates built from ASCA's Medicare ASC final rule rate calculator (scripts/build_ascfs.py): the rate lookup
(remitwatch.ascrates), which must agree with the workbook to the cent; the payload of the Calculator's ASC view
(remitwatch.calculator.asc_rule) and its arithmetic (static/asc_model.js, run with node); and the owner gate on the routes.

The expected amounts below are the workbook's own: its Calculator sheet recalculated by LibreOffice for Warrick County,
Indiana and for San Francisco (the build was checked that way against every code in six places, territories included).
Every clinician, case count and payer amount is made up.
"""
from __future__ import annotations

import json
import os
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

NODE = shutil.which("node")
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
    assert len(t.codes) == 8224 and len(t.places) == 3224                    # 8,186 codes in 2027 and 38 only in 2026
    assert sum(1 for c in t.codes if t.rates[c]["2027"]) == 8186
    m = t.meta
    assert m["labor_share"] == 0.5 and m["sequestration"] == 0.02 and m["coinsurance"] == 0.2
    assert m["not_wage_adjusted"] == ["H2", "J7", "K2", "K5", "K7", "L6"]
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
    assert everything["total"] == 8224 and len(everything["rows"]) == 50 and everything["offset"] == 100
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
    assert d["labels"] == {"2026": "October 2026", "2027": "the CY 2027 final rule"} and d["source"].endswith(".xlsx")
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


# --------------------------------------------------------------------------- the routes
PW, OWNER = "correct-horse-battery-7", "owner-staple-orbit-42"


def test_the_routes_are_the_owners(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from remitwatch import api
    monkeypatch.setattr(api, "DB_PATH", _store(tmp_path))
    monkeypatch.setattr(api, "PASSWORD", PW)
    monkeypatch.setattr(api, "USERNAME", "oda")
    monkeypatch.setattr(api, "OWNER_PASSWORD", OWNER)
    tc = TestClient(api.app)
    auth = ("oda", PW)
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
