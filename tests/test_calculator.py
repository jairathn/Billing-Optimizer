"""
Tests for the collections calculator: the payload remitwatch.calculator builds from the clinician tables, the page's
arithmetic (remitwatch/static/calc_model.js, run with node), the Medicare fee schedule table, and the owner gate on the
API. Every clinician, payer amount and volume here is made up.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from remitwatch import activity, calculator  # noqa: E402
from remitwatch.detect import ensure_schema  # noqa: E402
from remitwatch.mpfs import CONVERSION_FACTOR, MPFS, medicare_amount  # noqa: E402

DOC, PA, IDLE, GEN, RAD = "1000000001", "1000000002", "1000000003", "1000000004", "1000000005"
PW, OWNER = "correct-horse-battery-7", "owner-staple-orbit-42"
NODE = shutil.which("node")
MONTHS = ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"]

# units a month for the physician, and the allowed per unit they came in at
DOC_MONTH = {"17311": (20, 30000), "17313": (10, 27000), "17312": (12, 37000), "17314": (3, 34000), "14040": (8, 70000), "15260": (2, 90000),
             "13132": (5, 20000), "11642": (4, 12000), "12032": (3, 13000), "99213": (60, 8500), "99214": (30, 12000), "99203": (10, 10500),
             "G2211": (6, 1600), "11102": (25, 8000), "11103": (15, 4200), "17000": (20, 5000), "17003": (60, 580), "17004": (2, 14000),
             "17110": (5, 9000), "77600": (8, 40000), "88305": (10, 6500)}
PA_MONTH = {"99213": (150, 7500), "99214": (80, 10500), "11102": (40, 7000), "11103": (12, 3800), "17000": (40, 4500), "17003": (90, 500),
            "17110": (20, 8000), "11402": (3, 9000), "12032": (2, 11000)}


def _db(tmp_path) -> str:
    path = str(tmp_path / "calc.sqlite")
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    ensure_schema(c)
    activity.ensure_schema(c)
    c.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (activity.INFO_KEY, json.dumps({"version": activity.ACTIVITY_VERSION, "built_at": "2026-07-01T00:00:00+00:00"})))
    c.executemany("INSERT INTO provider_roster (provider_id, name, kind, tier, role) VALUES (?,?,?,?,?)",
                  [(DOC, "BRAVO BOB", "person", "physician", "Doctor"), (PA, "ALPHA ANN", "person", "nonphysician", "Physician Assistant"),
                   (IDLE, "CHARLIE CAL", "person", "physician", "Doctor"), (GEN, "DELTA DEE", "person", "physician", "Doctor"),
                   (RAD, "ECHO ED", "person", "physician", "Doctor")])
    # clinic days: the physician 16 days a month in KY, the PA 18 days (4 of them in IN); the third clinician two days
    days = []
    for m in MONTHS:
        for dd in range(1, 17):
            days.append((DOC, f"{m}-{dd:02d}", 20, "KY"))
        for dd in range(1, 19):
            days.append((PA, f"{m}-{dd:02d}", 22, "IN" if dd <= 4 else "KY"))
    days += [(IDLE, "2026-03-02", 6, "KY"), (IDLE, "2026-03-03", 6, "KY")]
    # a general dermatologist (no Mohs) and a physician whose work is nearly all radiation, both with plenty of days
    for m in MONTHS:
        for dd in range(1, 13):
            days.append((GEN, f"{m}-{dd:02d}", 25, "KY"))
            days.append((RAD, f"{m}-{dd:02d}", 8, "KY"))
    c.executemany("INSERT INTO provider_days (provider_id, service_date, patients, biopsy_lesions, path_generated_cents, place) VALUES (?,?,?,?,?,?)",
                  [(p, d, n, 2.0 if p == DOC else 1.0, 6000 if p == DOC else 0, pl) for p, d, n, pl in days])
    rows = []
    for who, plan in ((DOC, DOC_MONTH), (PA, PA_MONTH), (GEN, {"99213": (200, 8500), "11102": (30, 8000)}),
                      (RAD, {"77600": (100, 40000), "99213": (5, 8500)})):
        for m in MONTHS:
            for code, (u, per) in plan.items():
                rows.append((who, m, code, activity.service_family(code), u, u, 0, u * per * 2, u * per, int(u * per * 0.85)))
    c.executemany("INSERT INTO provider_services VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    c.executemany("INSERT INTO provider_payers (provider_id, month, payer_kind, encounters, charge_cents, allowed_cents, paid_cents) VALUES (?,?,?,?,?,?,?)",
                  [(who, m, k, 50, 0, al, pd) for who in (DOC, PA) for m in MONTHS
                   for k, al, pd in (("medicare", 1_000_000, 940_000), ("commercial", 1_000_000, 650_000))])
    # who pays: Medicare KY 60%, Plan A (commercial) 40% of every service line
    exp = []
    for who in (DOC, PA):
        for m in MONTHS:
            for code in set(DOC_MONTH) | set(PA_MONTH):
                exp.append((m, "Medicare KY (CGS J15)", "medicare", who, "physician" if who == DOC else "nonphysician", code, "office", "", 60, 0, 0, 0, 0))
                exp.append((m, "Plan A", "commercial", who, "physician" if who == DOC else "nonphysician", code, "office", "", 40, 0, 0, 0, 0))
    c.executemany("INSERT INTO exposure VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", exp)
    # usual rates: Medicare KY has its own for a few codes; Plan A pays 90% of Medicare on visits it has rates for
    pr = []
    for code, cents in (("17311", 61592), ("99213", 8952), ("99214", 12780), ("11102", 8735)):
        pr.append(("Medicare KY (CGS J15)", "medicare", "2026", code, "physician", cents, cents, cents, cents, 50, 1.0, cents, 1.0, "office"))
    for code, cents in (("99213", 8057), ("99214", 11502), ("99203", 9953), ("17311", 52000), ("17000", 5520)):
        pr.append(("Plan A", "commercial", "2026", code, "physician", cents, cents, cents, cents, 30, 1.0, None, None, "office"))
    for code in [f"9{n}" for n in range(9000, 9100)]:           # filler so 2026 counts as a full year of rates
        pr.append(("Plan B", "commercial", "2026", code, "physician", 100, 100, 100, 100, 5, 1.0, None, None, "office"))
    c.executemany("INSERT INTO payer_rates VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", pr)
    c.commit()
    c.close()
    return path


@pytest.fixture
def db(tmp_path):
    return _db(tmp_path)


def _calc(db, who, **kw):
    c = sqlite3.connect(db)
    try:
        return calculator.calculator(c, who, "2026-01", "2026-06", **kw)
    finally:
        c.close()


def test_levers_describe_the_schedule(db):
    d = _calc(db, DOC)
    L, days = d["levers"], d["schedule"]["clinic_days"]
    assert days == 96 and d["schedule"]["days_per_week"] > 3
    assert L["mohs"]["cases"] == pytest.approx(30 * 6 / days)
    assert L["mohs"]["head_neck_share"] == pytest.approx(20 / 30)
    assert L["mohs"]["stages"] == pytest.approx(1 + 15 / 30)
    assert L["flap_graft"]["share"] == pytest.approx(10 / 30) and L["flap_graft"]["mix"] == {"14040": pytest.approx(0.8), "15260": pytest.approx(0.2)}
    # layered closures are shared between Mohs cases and excisions with the practice weights, the clinician's total kept
    wm, we = calculator.LAYERED_WEIGHT
    lay = (L["layered"]["mohs_share"] * 30 + L["layered"]["excision_share"] * 4) * 6
    assert lay == pytest.approx(8 * 6) and L["layered"]["mohs_share"] / L["layered"]["excision_share"] == pytest.approx(wm / we)
    assert L["biopsy"]["per_patient"] == pytest.approx(40 / 25)
    assert L["ak"]["per_patient"] == pytest.approx(1 + 60 / 20) and L["ak"]["share_15_plus"] == pytest.approx(2 / 22)
    assert L["other"]["groups"] == {"77600": "radiation", "88305": "path", "17110": "rest"} or L["other"]["groups"]["77600"] == "radiation"
    assert "17110" not in L["other"]["codes"]      # benign destruction is its own lever


def test_rates_use_the_payers_own_then_its_share_of_medicare(db):
    d = _calc(db, DOC)
    r = d["rates"]
    assert r["17311"]["Plan A"]["2026"] == 52000 and r["17311"]["Plan A"]["how"] == "own"
    # Medicare KY with no observed rate for a code: CMS's Kentucky amount
    assert r["14040"]["Medicare KY (CGS J15)"]["2026"] == MPFS["2026"]["KY"]["14040"][0]
    assert r["14040"]["Medicare KY (CGS J15)"]["how"] == "medicare_schedule"
    # Plan A pays visits at 90% of Medicare: a visit code it has no rate for is priced the same way
    pct = r["99203"]["Plan A"]["2026"] / MPFS["2026"]["KY"]["99203"][0]
    assert pct == pytest.approx(0.9, abs=0.01)
    v = r["99215"]["Plan A"] if "99215" in r else None
    assert v is None or v["how"] == "payer_pct_of_medicare"
    # a procedure Plan A has no rate for: its procedure share of Medicare (17311 and 17000 here)
    assert r["14040"]["Plan A"]["how"] == "payer_pct_of_medicare"


def test_medicare_reference_follows_the_states_and_the_tier(db):
    doc, pa = _calc(db, DOC), _calc(db, PA)
    assert doc["states"] == {"KY": 1.0}
    assert pa["states"]["IN"] == pytest.approx(4 / 18, abs=1e-3)
    ky, ind = MPFS["2026"]["KY"]["99213"][0], MPFS["2026"]["IN"]["99213"][0]
    want = round((pa["states"]["KY"] * ky + pa["states"]["IN"] * ind) * calculator.APP_MEDICARE_SHARE)
    assert abs(pa["medicare"]["99213"]["2026"] - want) <= 1
    assert doc["medicare"]["17311"]["2027"] == MPFS["2027"]["KY"]["17311"][0]


def test_role_averages_and_clinician_list(db):
    d = _calc(db, "mohs")
    # the Mohs surgeons' average: only the physician with Mohs on most days; not the one with two clinic days, not the
    # general dermatologist, not the physician whose work is nearly all radiation
    assert d["who"]["clinicians"] == 1 and d["who"]["members"] == ["BRAVO BOB"]
    assert d["schedule"]["days_per_week"] == _calc(db, DOC)["schedule"]["days_per_week"]
    g = _calc(db, "general")
    assert g["who"]["members"] == ["DELTA DEE"]
    assert d["roles"] == {"mohs": 1, "general": 1, "apps": 1}
    assert _calc(db, "physicians")["who"]["id"] == "mohs"          # the earlier name still works
    ids = {c["provider_id"] for c in d["clinicians"]}
    assert DOC in ids and PA in ids and IDLE not in ids
    assert _calc(db, "nobody") is None


def test_paid_shares(db):
    d = _calc(db, DOC)
    assert d["paid_share"]["medicare"] == pytest.approx(0.94) and d["paid_share"]["commercial"] == pytest.approx(0.65)
    assert set(d["paid_share_class"]) <= {"visit", "mohs", "procedure", "pathology"} and d["paid_share_all"] == pytest.approx(0.85, abs=0.01)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_page_arithmetic():
    out = subprocess.run([NODE, os.path.join(ROOT, "tests", "calc_model_check.js")], capture_output=True, text=True, check=True, cwd=ROOT)
    res = json.loads(out.stdout)
    failed = {k: v[1] for k, v in res.items() if not v[0]}
    assert len(res) >= 20 and not failed, failed


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_engine_levers_give_back_history_in_the_page(db, tmp_path):
    """The engine's levers, turned into units by the page, give back every code billed; the model times k gives back
    what was allowed."""
    for who in (DOC, PA, "physicians"):
        path = tmp_path / f"{who}.json"
        path.write_text(json.dumps(_calc(db, who)))
        js = (
            "const M=require(process.argv[1]);const d=JSON.parse(require('fs').readFileSync(process.argv[2]));"
            "const x=M.defaults(d),u=M.units(d,x),days=d.schedule.clinic_days;const bad=[];"
            "for(const [c,cc] of Object.entries(d.codes)){const got=(u[c]||0)*days;if(Math.abs(got-cc.units)>1e-6*Math.max(1,cc.units))bad.push([c,cc.units,got]);}"
            "const cal=M.calibrate(d);let m=0,a=0;for(const i of Object.values(cal.info)){m+=i.modeled*i.k;a+=i.actual;}"
            "process.stdout.write(JSON.stringify({bad,ratio:m/a}));")
        out = subprocess.run([NODE, "-e", js, os.path.join(ROOT, "remitwatch", "static", "calc_model.js"), str(path)],
                             capture_output=True, text=True, check=True)
        res = json.loads(out.stdout)
        assert res["bad"] == [], (who, res["bad"])
        assert res["ratio"] == pytest.approx(1.0, abs=0.005), who


def test_medicare_table_matches_cms():
    # CMS's 2026 locality file, Kentucky (carrier 15102, locality 00), non-facility and facility: 17311 $615.92 / $278.01
    assert MPFS["2026"]["KY"]["17311"] == (61592, 27801)
    assert medicare_amount("2026", "IN", "17311") == 62438
    assert CONVERSION_FACTOR["2027"] == 32.6286
    # 2027 Kentucky, from RVU27A (17311: work 6.05, non-facility PE 12.69, MP 0.58) and the 2027 GPCIs (0.963, 0.900, 0.917):
    # [(6.05 x 0.963) + (12.69 x 0.900) + (0.58 x 0.917)] x 32.6286 = $580.10
    assert MPFS["2027"]["KY"]["17311"][0] == round(((6.05 * 0.963) + (12.69 * 0.900) + (0.58 * 0.917)) * 32.6286 * 100) == 58010
    assert "G2211" not in MPFS["2027"]["KY"]      # replaced by modifier B1 in 2027


def test_calculator_is_for_the_owner(db, monkeypatch):
    from fastapi.testclient import TestClient
    from remitwatch import api
    monkeypatch.setattr(api, "DB_PATH", db)
    monkeypatch.setattr(api, "PASSWORD", PW)
    monkeypatch.setattr(api, "USERNAME", "oda")
    monkeypatch.setattr(api, "OWNER_PASSWORD", OWNER)
    monkeypatch.delenv("REMITWATCH_MODE", raising=False)
    tc, auth = TestClient(api.app), ("oda", PW)
    assert tc.get("/api/activity/calculator?provider=" + DOC, auth=auth).status_code == 403
    r = tc.get("/api/activity/calculator?provider=" + DOC + "&from=2026-01&to=2026-06", auth=auth, headers={"X-Owner-Password": OWNER})
    assert r.status_code == 200 and r.json()["ready"] and r.json()["who"]["id"] == DOC
    assert tc.get("/api/activity/calculator?provider=" + DOC, auth=("oda", "wrong-password-x")).status_code == 401
    assert tc.get("/api/activity/calculator?provider=nobody", auth=auth, headers={"X-Owner-Password": OWNER}).status_code == 404
    assert tc.get("/api/activity/calculator?provider=../x", auth=auth, headers={"X-Owner-Password": OWNER}).status_code == 422
