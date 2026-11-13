"""Second-round attack on SECOND_RATE (a payer's routine lower rate) versus one-off underpayments.
Everything here is invented: names, member numbers, NPIs, claims and amounts. The page checks need node, and the browser check needs Playwright with Chromium; without them they are skipped.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from datetime import date, timedelta

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from remitwatch import detect as det_mod  # noqa: E402
from remitwatch.actions import apply_status  # noqa: E402
from remitwatch.alerts import AlertEngine  # noqa: E402
from remitwatch.detect import Detector  # noqa: E402
from tests.test_features import NPI, build_store, svc  # noqa: E402
from tests.test_resolution import cl  # noqa: E402

DOC_NPI = "1555555551"        # an invented individual NPI
PATH_NPI = "1666666661"       # an invented dermatopathologist


def claim(i, code, charge, allowed, *, npi=DOC_NPI, cfi="12", dos="20260401", plan="PPO", status="1", pcn=None,
          who=None, prov="1*DOCTOR*ALEX", paid=None):
    """One claim with one line. `prov` is NM102*NM103*NM104 of the rendering provider (1 = a person)."""
    pcn = pcn or f"CB{i:07d}C017"
    who = who or f"SAMPLE*PAT****MI*M{i:08d}"
    paid = allowed if paid is None else paid
    co = f"{float(charge) - float(allowed):.2f}"
    pr = float(allowed) - float(paid)
    cas = f"CAS*CO*45*{co}~" + (f"CAS*PR*1*{pr:.2f}~" if pr > 0 else "")
    return (f"CLP*{pcn}*{status}*{charge}*{paid}*{pr:.2f}*{cfi}*PC{i}*11~NM1*QC*1*{who}~NM1*82*{prov}****XX*{npi}~REF*CE*{plan}~"
            f"DTM*232*{dos}~" + svc(code, charge, paid, allowed, cas, dos=dos))


def findings(store, where="category IN ('underpayment','contract') AND COALESCE(stale,0)=0"):
    out = []
    for r in store.query(f"SELECT * FROM findings WHERE {where} ORDER BY signal, patient_control_number"):
        r = dict(r)
        r["evidence"] = json.loads(r["evidence"]) if r.get("evidence") else {}
        out.append(r)
    return out


def api_client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import remitwatch.api as api_mod
    monkeypatch.setattr(api_mod, "DB_PATH", str(tmp_path / "s.sqlite"))
    monkeypatch.setattr(api_mod, "PASSWORD", "")
    monkeypatch.setattr(api_mod, "SHOW_PHI", False)
    return TestClient(api_mod.app)


# ======================================================================= reasons that fire on data where they are wrong
def test_R1_the_same_named_clinician_paid_both_amounts_is_called_the_pa_rate(tmp_path):
    """One named physician (NM1*82*1 with an individual NPI) who has no Medicare visits in the files yet (a new hire, or
    one who sees no Medicare patients), so the tier is 'unknown'. The payer pays her 99214 at $117.60 on 25 visits and
    at $100.00 (85%) on 6. A PA rate cannot explain one clinician being paid two amounts."""
    claims = [claim(100 + i, "99214", "223.65", "117.60") for i in range(25)] + [claim(200 + i, "99214", "223.65", "100.00") for i in range(6)]
    store = build_store(tmp_path, claims)
    Detector(store, run_id="t").run()
    rows = findings(store)
    print([(r["signal"], r["provider_tier"], r["shortfall_cents"]) for r in rows])
    for r in rows:
        if r["signal"] == "SECOND_RATE":
            print("NOTE:", r["evidence"]["note"])
    npis = {r[0] for r in store.query("SELECT DISTINCT c.rendering_provider_npi FROM claims c")}
    names = {r[0] for r in store.query("SELECT DISTINCT c.rendering_provider_name FROM claims c")}
    print("rendering NPIs on every claim:", npis, names)
    assert npis == {DOC_NPI} and names == {"DOCTOR ALEX"}
    assert [r["signal"] for r in rows] == ["UNDERPAID_VS_OWN_RATE"] * 6, \
        "the clinician paid $117.60 on 25 visits is grouped as 'the rate for a PA or NP' on the other 6"
    store.close()


def test_R1b_a_pathologist_paid_85_percent_is_called_the_pa_rate(tmp_path):
    """Dermatopathology (88305) read by one pathologist, who never bills Medicare visits (tier unknown)."""
    claims = [claim(100 + i, "88305", "150.00", "70.00", npi=PATH_NPI, prov="1*SLIDE*PAT") for i in range(25)]
    claims += [claim(200 + i, "88305", "150.00", "59.50", npi=PATH_NPI, prov="1*SLIDE*PAT") for i in range(6)]
    store = build_store(tmp_path, claims)
    Detector(store, run_id="t").run()
    rows = findings(store)
    sr = [r for r in rows if r["signal"] == "SECOND_RATE"]
    for r in sr:
        print("TITLE:", r["title"], "| NOTE:", r["evidence"]["note"])
    assert not any("physician assistant or nurse practitioner" in r["evidence"]["note"] for r in sr), \
        "a pathology read paid at 85% by one pathologist is explained as the PA/NP rate"
    store.close()


def test_R2_a_lone_biopsy_billed_to_a_secondary_payer_is_called_the_multiple_procedure_rule(tmp_path):
    """Each 11102 is the only procedure of its visit. The six paid at half also went to a Medicare supplement as
    secondary (same claim ID, same date, same code). The secondary's copy of the line is counted as 'another procedure
    for the patient that day'."""
    usual = [claim(100 + i, "11102", "180.00", "90.00") for i in range(25)]
    half = [claim(200 + i, "11102", "180.00", "45.00") for i in range(6)]
    store = build_store(tmp_path, usual + half, payer="AETNA")
    store.close()
    secondary = [claim(200 + i, "11102", "180.00", "45.00", status="2", paid="9.00") for i in range(6)]
    store = build_store(tmp_path, secondary, pay_date="20260501", payer="MUTUAL OF OMAHA")
    Detector(store, run_id="t").run()
    rows = findings(store, "signal IN ('SECOND_RATE','UNDERPAID_VS_OWN_RATE') AND payer_group='Aetna' AND COALESCE(stale,0)=0")
    print([(r["signal"], r["shortfall_cents"]) for r in rows])
    for r in rows:
        if r["signal"] == "SECOND_RATE":
            print("NOTE:", r["evidence"]["note"])
    per_claim = Counter(r[0] for r in store.query("SELECT patient_control_number FROM lines WHERE procedure_code='11102' AND patient_control_number >= 'CB0000200'"))
    print("lines per claim id:", dict(per_claim))
    assert [r["signal"] for r in rows] == ["UNDERPAID_VS_OWN_RATE"] * 6, \
        "a lone procedure paid at half is grouped as 'the multiple-procedure rule' because the secondary payer's copy counts as another procedure"
    store.close()


def test_R2b_without_the_secondary_copy_the_same_lines_stay_one_offs(tmp_path):
    """Control for R2: the same primary lines with no secondary claim stay one-offs."""
    usual = [claim(100 + i, "11102", "180.00", "90.00") for i in range(25)]
    half = [claim(200 + i, "11102", "180.00", "45.00") for i in range(6)]
    store = build_store(tmp_path, usual + half, payer="AETNA")
    Detector(store, run_id="t").run()
    rows = findings(store)
    assert [r["signal"] for r in rows] == ["UNDERPAID_VS_OWN_RATE"] * 6
    store.close()


def test_R3_one_plan_on_three_of_ten_lines_explains_all_ten(tmp_path):
    """Ten visits paid at $90.00 against the usual $117.60: three under a plan the usual-rate visits never carry, seven
    under the same PPO plan as every usual-rate visit. The plan explains three lines at most."""
    med = [claim(i, "99214", "223.65", "127.80", cfi="MB") for i in range(1, 26)]        # makes the clinician a physician
    store = build_store(tmp_path, med, payer="KY MEDICARE")
    store.close()
    claims = [claim(100 + i, "99214", "223.65", "117.60") for i in range(25)]
    claims += [claim(200 + i, "99214", "223.65", "90.00", plan="HMO GOLD") for i in range(3)]
    claims += [claim(300 + i, "99214", "223.65", "90.00", plan="PPO") for i in range(7)]
    store = build_store(tmp_path, claims)
    Detector(store, run_id="t").run()
    rows = findings(store, "payer_group='Anthem / BCBS' AND category IN ('underpayment','contract') AND COALESCE(stale,0)=0")
    print([(r["signal"], r["shortfall_cents"]) for r in rows])
    sr = [r for r in rows if r["signal"] == "SECOND_RATE"]
    for r in sr:
        print("NOTE:", r["evidence"]["note"], "| by plan:", r["evidence"]["by_plan"])
    ppo_one_offs = [r for r in rows if r["signal"] == "UNDERPAID_VS_OWN_RATE" and r["plan"] == "PPO"]
    assert len(ppo_one_offs) == 7, "seven PPO visits underpaid with no reason are folded under a plan reason that covers three other visits"
    store.close()


# ======================================================================= patterns that grow after someone worked them
def _plan_pattern(tmp_path, first_ids, dos="20260401", pay="20260415"):
    med = [claim(i, "99214", "223.65", "127.80", cfi="MB") for i in range(1, 26)]
    store = build_store(tmp_path, med, payer="KY MEDICARE")
    store.close()
    claims = [claim(100 + i, "99214", "223.65", "117.60") for i in range(25)]
    claims += [claim(i, "99214", "223.65", "90.00", plan="HMO GOLD", dos=dos) for i in first_ids]
    return build_store(tmp_path, claims, pay_date=pay)


def test_G1_lines_paid_after_a_pattern_was_marked_recovered_are_hidden_inside_it(tmp_path):
    store = _plan_pattern(tmp_path, range(200, 206))
    Detector(store, run_id="one").run()
    [p] = findings(store, "signal='SECOND_RATE'")
    assert p["evidence"]["lines"] == 6
    # the payer agreed and repaid the six claims; the biller records it
    apply_status(store.conn, p["finding_id"], "resolved", "Payer reprocessed the six HMO GOLD claims", recovered_cents=6 * 2760, actor="biller")
    store.conn.commit()
    store.close()
    # months later the payer is still paying new HMO GOLD visits at $90.00
    store = build_store(tmp_path, [claim(300 + i, "99214", "223.65", "90.00", plan="HMO GOLD", dos="20260901") for i in range(4)], pay_date="20260920")
    Detector(store, run_id="two").run()
    allrows = findings(store, "category IN ('underpayment','contract')")
    for r in allrows:
        print((r["signal"], r["status"], r["stale"], r["shortfall_cents"], r["recovered_cents"], r["evidence"].get("lines")))
    new_ids = {f"CB{300 + i:07d}C017" for i in range(4)}
    open_cover = [r for r in allrows if r["status"] == "open" and not r["stale"]
                  and (r["patient_control_number"] in new_ids or new_ids & {c[0] for c in r["evidence"].get("claims", [])})]
    assert open_cover, "four new underpaid claims ($110.40) are only inside a pattern marked recovered: nothing open shows them"
    store.close()


def test_G2_lines_paid_after_a_pattern_was_appealed_join_the_appealed_finding(tmp_path, monkeypatch):
    store = _plan_pattern(tmp_path, range(200, 206))
    Detector(store, run_id="one").run()
    [p] = findings(store, "signal='SECOND_RATE'")
    apply_status(store.conn, p["finding_id"], "appealed", "Sent the six claims to the HMO GOLD rep", actor="biller")
    store.conn.commit()
    store.close()
    store = build_store(tmp_path, [claim(300 + i, "99214", "223.65", "90.00", plan="HMO GOLD", dos="20260901") for i in range(4)], pay_date="20260920")
    Detector(store, run_id="two").run()
    store.close()
    c = api_client(tmp_path, monkeypatch)
    open_items = c.get("/api/findings?category=underpayment").json()["items"]          # the default status filter: open
    print("open:", [(i["signal"], i["patient_control_number"], i["shortfall_cents"]) for i in open_items])
    appealed = c.get("/api/findings?category=underpayment&status=appealed").json()["items"]
    print("appealed:", [(i["signal"], i["title"], i["shortfall_cents"]) for i in appealed])
    assert open_items, "four claims paid after the appeal was sent are not in the open work: they ride on the appealed pattern"


# ======================================================================= deadlines of the pattern's claims
def _deadline_store(tmp_path, today: date):
    """Aetna (180-day window). Usual $700.00 for Mohs 17311 on 25 visits in January, then $400.00 on 8 visits: four paid
    170 days before `today` (window closes in 10 days) and four paid 30 days before (closes in 150)."""
    early_pay, late_pay = today - timedelta(days=170), today - timedelta(days=30)
    early_dos, late_dos = early_pay - timedelta(days=14), late_pay - timedelta(days=14)
    assert early_dos.year == late_dos.year, "pick another day to run this"
    jan = date(early_dos.year, 1, 2)
    f = lambda d: d.strftime("%Y%m%d")
    med = [claim(i, "99214", "223.65", "127.80", cfi="MB", dos=f(jan)) for i in range(1, 26)]
    store = build_store(tmp_path, med, payer="KY MEDICARE", pay_date=f(jan + timedelta(days=14)))
    store.close()
    store = build_store(tmp_path, [claim(100 + i, "17311", "1200.00", "700.00", dos=f(jan)) for i in range(25)], payer="AETNA",
                        pay_date=f(jan + timedelta(days=14)))
    store.close()
    store = build_store(tmp_path, [claim(200 + i, "17311", "1200.00", "400.00", dos=f(early_dos)) for i in range(4)], payer="AETNA", pay_date=f(early_pay))
    store.close()
    return build_store(tmp_path, [claim(300 + i, "17311", "1200.00", "400.00", dos=f(late_dos)) for i in range(4)], payer="AETNA", pay_date=f(late_pay))


def test_D1_claims_whose_window_closes_in_ten_days_leave_every_deadline_view(tmp_path, monkeypatch):
    today = date.today()
    store = _deadline_store(tmp_path, today)
    # as one-offs (what the same lines were before grouping): they are due, and every deadline view says so
    monkeypatch.setattr(det_mod, "SECOND_RATE_MIN_LINES", 10 ** 6)
    Detector(store, run_id="one_offs").run()
    c = api_client(tmp_path, monkeypatch)
    s0 = {d["bucket"]: (d["n"], d["shortfall_cents"]) for d in c.get("/api/summary").json()["deadlines"]}
    due0 = c.get("/api/findings?category=underpayment&deadline=due").json()
    # grouped: one SECOND_RATE
    monkeypatch.setattr(det_mod, "SECOND_RATE_MIN_LINES", 5)
    Detector(store, run_id="grouped").run()
    [p] = findings(store, "signal='SECOND_RATE' AND COALESCE(stale,0)=0")
    print("pattern deadline:", p["appeal_deadline"], "| hint:", p["hint"], "| deadlines:", p["evidence"]["deadlines"], "| reason:", p["evidence"]["note"])
    s1 = {d["bucket"]: (d["n"], d["shortfall_cents"]) for d in c.get("/api/summary").json()["deadlines"]}
    due1 = c.get("/api/findings?category=underpayment&deadline=due").json()
    print("summary deadline buckets as one-offs:", s0, "| grouped:", s1)
    print("Due in 45 days as one-offs:", due0["total"], due0["shortfall_cents"], "| grouped:", due1["total"], due1["shortfall_cents"])
    soon = min(d for d, _n, _c in p["evidence"]["deadlines"])
    assert today.isoformat() <= soon <= (today + timedelta(days=30)).isoformat()
    assert s0.get("due_30d", (0, 0))[1] == 4 * 30000                     # the control: $1,200 due within 30 days
    assert due1["total"] >= 1 and s1.get("due_30d", (0, 0))[1] >= 4 * 30000, \
        "$1,200 whose filing windows close in 10 days is in neither the 'Due within 30 days' figure nor the 'Due in 45 days' filter"
    store.close()


def test_D2_no_deadline_alert_for_a_pattern_whose_first_windows_close_this_month(tmp_path, monkeypatch):
    today = date.today()
    store = _deadline_store(tmp_path, today)
    monkeypatch.setattr(det_mod, "SECOND_RATE_MIN_LINES", 10 ** 6)
    Detector(store, run_id="one_offs").run()
    e0 = AlertEngine(store.conn, run_id="a0", today=today.isoformat())
    e0.deadline_rule()
    a0 = [a["title"] for a in e0.active.values() if a["kind"] == "DEADLINE"]
    monkeypatch.setattr(det_mod, "SECOND_RATE_MIN_LINES", 5)
    Detector(store, run_id="grouped").run()
    e1 = AlertEngine(store.conn, run_id="a1", today=today.isoformat())
    e1.deadline_rule()
    a1 = [a["title"] for a in e1.active.values() if a["kind"] == "DEADLINE"]
    print("DEADLINE alert as one-offs:", a0)
    print("DEADLINE alert grouped:    ", a1)
    assert a0, "control: the one-offs raise a DEADLINE alert"
    assert a1, "the same $1,200 grouped into a pattern raises no DEADLINE alert"
    store.close()


def test_D3_progress_counts_a_pattern_as_still_appealable_after_most_windows_closed(tmp_path, monkeypatch):
    """Progress: 'live' is open money still inside its window. A pattern with seven claims whose windows closed and one
    still open counts all eight as live."""
    today = date.today()
    f = lambda d: d.strftime("%Y%m%d")
    old_pay, new_pay = today - timedelta(days=200), today - timedelta(days=20)
    old_dos, new_dos = old_pay - timedelta(days=14), new_pay - timedelta(days=14)
    assert old_dos.year == new_dos.year
    jan = date(old_dos.year, 1, 2)
    med = [claim(i, "99214", "223.65", "127.80", cfi="MB", dos=f(jan)) for i in range(1, 26)]
    store = build_store(tmp_path, med, payer="KY MEDICARE", pay_date=f(jan + timedelta(days=14)))
    store.close()
    store = build_store(tmp_path, [claim(100 + i, "17311", "1200.00", "700.00", dos=f(jan)) for i in range(25)], payer="AETNA", pay_date=f(jan + timedelta(days=14)))
    store.close()
    store = build_store(tmp_path, [claim(200 + i, "17311", "1200.00", "400.00", dos=f(old_dos)) for i in range(7)], payer="AETNA", pay_date=f(old_pay))
    store.close()
    store = build_store(tmp_path, [claim(300, "17311", "1200.00", "400.00", dos=f(new_dos))], payer="AETNA", pay_date=f(new_pay))
    Detector(store, run_id="t").run()
    [p] = findings(store, "signal='SECOND_RATE'")
    print("pattern:", p["appeal_deadline"], p["hint"], p["evidence"]["deadlines"])
    store.close()
    c = api_client(tmp_path, monkeypatch)
    prog = c.get("/api/progress").json()
    print("progress live:", prog["live"], "expired:", prog["expired"])
    assert prog["live"]["cents"] <= 1 * 30000, "Progress counts $2,100 whose filing windows have closed as still appealable"


# ======================================================================= patterns that dissolve after someone worked them
def test_S1_a_recovered_rate_change_pattern_comes_back_as_open_appeals_once_the_payer_fixes_its_rate(tmp_path, monkeypatch):
    """The payer paid 99214 at $100.00 from May (a rate-change pattern). The biller disputes it, the payer repays the six
    claims and pays new visits at the usual $117.60 again. The new usual-rate visits are dated after the pattern, so the
    'rate change' reason no longer holds: the recovered pattern goes stale and its six claims return as open appeals."""
    med = [claim(i, "99214", "223.65", "127.80", cfi="MB", dos="20260105") for i in range(1, 26)]
    store = build_store(tmp_path, med, payer="KY MEDICARE", pay_date="20260120")
    store.close()
    store = build_store(tmp_path, [claim(100 + i, "99214", "223.65", "117.60", dos="20260105") for i in range(25)], pay_date="20260120")
    store.close()
    store = build_store(tmp_path, [claim(200 + i, "99214", "223.65", "100.00", dos="20260505") for i in range(6)], pay_date="20260520")
    Detector(store, run_id="one").run()
    [p] = findings(store, "signal='SECOND_RATE'")
    print("run one:", p["title"], "|", p["evidence"]["note"][:120])
    apply_status(store.conn, p["finding_id"], "resolved", "Humana-style dispute won; six claims repaid", recovered_cents=6 * 1760, actor="biller")
    store.conn.commit()
    store.close()
    c = api_client(tmp_path, monkeypatch)
    rec0 = sum(r["cents"] for r in c.get("/api/progress").json()["recovered_by_month"])
    store = build_store(tmp_path, [claim(300 + i, "99214", "223.65", "117.60", dos="20260901") for i in range(5)], pay_date="20260915")
    Detector(store, run_id="two").run()
    rows = findings(store, "category IN ('underpayment','contract')")
    for r in rows:
        print("run two:", (r["signal"], r["status"], r["stale"], r["patient_control_number"], r["shortfall_cents"]))
    rec1 = sum(r["cents"] for r in c.get("/api/progress").json()["recovered_by_month"])
    print("Progress recovered before:", rec0, "after:", rec1)
    reopened = [r for r in rows if r["signal"] == "UNDERPAID_VS_OWN_RATE" and r["status"] == "open" and not r["stale"]]
    assert not reopened and rec1 == rec0, \
        f"{len(reopened)} claims already recovered on the pattern are open appeals again, and Progress lost ${(rec0 - rec1) / 100:.2f} of recoveries"
    store.close()


# ======================================================================= the API on a store saved before the hint column
def test_A1_every_findings_route_works_on_a_store_without_the_hint_column(tmp_path, monkeypatch):
    med = [claim(i, "99214", "223.65", "127.80", cfi="MB") for i in range(1, 26)]
    store = build_store(tmp_path, med, payer="KY MEDICARE")
    store.close()
    claims = [claim(100 + i, "99214", "223.65", "117.60") for i in range(25)]
    claims += [claim(200 + i, "99214", "223.65", "90.00", plan="HMO GOLD") for i in range(6)]
    claims += [claim(300, "99214", "223.65", "95.00"), claim(301, "99214", "223.65", "0.00", paid="0.00").replace("CAS*CO*45*223.65~", "CAS*CO*16*223.65~")]
    store = build_store(tmp_path, claims)
    Detector(store, run_id="t").run()
    fids = {r[0]: r[1] for r in store.query("SELECT signal, finding_id FROM findings")}
    store.conn.execute("ALTER TABLE findings DROP COLUMN hint")
    store.conn.commit()
    store.close()
    c = api_client(tmp_path, monkeypatch)
    routes = ["/api/meta", "/api/summary", "/api/summary?status=all", "/api/findings", "/api/findings?status=all&category=underpayment",
              "/api/findings?sort=deadline&deadline=valid&category=downcoding,bundling,denial,underpayment", "/api/findings.csv?status=all",
              "/api/findings.csv?category=underpayment", "/api/progress", "/api/trends", "/api/payers", "/api/providers", "/api/codes",
              "/api/rates", "/api/rates/matrix?year=2026", "/api/volume", "/api/coverage", "/api/alerts",
              "/api/breakdown?dim=provider&family=underpayment", "/api/breakdown?dim=plan&family=all&status=active",
              "/api/breakdown?dim=signal&dim2=payer&family=all"]
    routes += [f"/api/findings/{fid}" for fid in fids.values()]
    routes += [f"/api/appeal/{fids[s]}" for s in ("UNDERPAID_VS_OWN_RATE",) if s in fids]
    bad = {}
    for r in routes:
        res = c.get(r)
        if res.status_code != 200:
            bad[r] = (res.status_code, res.text[:200])
    print("signals:", sorted(fids))
    print("failures:", bad)
    assert not bad


# ======================================================================= the drawer's filing windows (the page's own code, in node)
HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "review_assets")


def _drawer(instant, cases, tz="America/New_York"):
    import shutil
    import subprocess
    if not shutil.which("node"):
        import pytest
        pytest.skip("node is not installed")
    env = dict(os.environ, TZ=tz)
    res = subprocess.run(["node", os.path.join(HERE, "drawer_eval.js"), instant, json.dumps(cases)], capture_output=True, text=True, env=env, timeout=60)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def _strip(html):
    import re
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def test_P1_on_the_last_day_of_a_window_the_drawer_says_passed_from_7pm_in_kentucky():
    """A claim's window closes on 2026-11-13. At 8:30 pm Eastern on 2026-11-13 (01:30 UTC on the 14th) the biller opens
    the pattern: the page counts days in UTC and reports that window as passed."""
    ev = {"lines": 2, "note": "a rate change", "deadlines": [["2026-11-13", 1, 1760], ["2027-03-01", 1, 1760]],
          "claims": [["CB0000200C017", "2026-05-05", 10000, "2026-11-13"], ["CB0000201C017", "2026-09-01", 10000, "2027-03-01"]]}
    f = {"signal": "SECOND_RATE", "evidence": ev, "appeal_deadline": "2027-03-01", "deadline_basis": "the last of its 2 claims"}
    got = _drawer("2026-11-14T01:30:00Z", [{"f": f}])
    text = _strip(got["out"][0]["html"])
    print("local date:", got["local_date"])
    print("drawer:", text[:400])
    assert got["local_date"] == "2026-11-13"
    assert "2 of its 2 lines" in text and "(passed)" not in text, \
        "on the window's last day, in the evening in Kentucky, the drawer says that claim's window has passed"


def test_P2_the_drawer_does_not_throw_when_evidence_fields_are_missing():
    cases = [{"f": {"signal": "SECOND_RATE", "evidence": {}}},
             {"f": {"signal": "SECOND_RATE"}},
             {"f": {"signal": "SECOND_RATE", "evidence": {"lines": 3, "claims": [["CB1", "2026-05-05", 10000]]}}},     # an earlier release's evidence
             {"f": {"signal": "SECOND_RATE", "evidence": {"lines": 3, "deadlines": [["2027-01-01", 3, None]], "claims": []}}},
             {"f": {"signal": "SECOND_RATE", "evidence": {"deadlines": [], "claims": None, "note": None}}},
             {"f": {"signal": "DENIED_MISSING_INFO", "claim_key": "k", "evidence": {"maybe_paid_later": {}}}, "r": {"packets": None, "events": None}},
             {"f": {"signal": "DENIED_MISSING_INFO", "evidence": {"ema_shows_paid": {}}}}]
    got = _drawer("2026-11-13T15:00:00Z", cases)
    for c, o in zip(cases, got["out"]):
        print(c["f"].get("evidence"), "->", o if not o["ok"] else _strip(o["html"])[:160])
    assert all(o["ok"] for o in got["out"]), [o for o in got["out"] if not o["ok"]]


def test_P3_lines_without_a_payment_date_are_reported_as_past_their_windows():
    """A pattern of 6 lines where 2 carry no payment date (no deadline): when the 4 dated windows have passed the drawer
    says every line's window has passed."""
    ev = {"lines": 6, "note": "x", "deadlines": [["2026-06-01", 4, 7040]],
          "claims": [["CB1", "2026-01-05", 10000, "2026-06-01"]] * 4 + [["CB2", "2026-01-05", 10000, None]] * 2}
    got = _drawer("2026-11-13T15:00:00Z", [{"f": {"signal": "SECOND_RATE", "evidence": ev}}])
    text = _strip(got["out"][0]["html"])
    print(text[:300])
    assert "Every line's window has passed" not in text, "two lines with no known deadline are reported as past their windows"


def test_P3b_detection_writes_a_pattern_whose_deadlines_cover_fewer_lines_than_it_has(tmp_path):
    """Two of the pattern's remittances carry no payment date (BPR16 empty): their lines get no deadline, and the
    evidence the drawer reads counts 4 dated lines of 6."""
    med = [claim(i, "99214", "223.65", "127.80", cfi="MB", dos="20260105") for i in range(1, 26)]
    store = build_store(tmp_path, med, payer="KY MEDICARE", pay_date="20260120")
    store.close()
    store = build_store(tmp_path, [claim(100 + i, "99214", "223.65", "117.60", dos="20260105") for i in range(25)], pay_date="20260120")
    store.close()
    store = build_store(tmp_path, [claim(200 + i, "99214", "223.65", "100.00", dos="20260505") for i in range(4)], pay_date="20260520")
    store.close()
    store = build_store(tmp_path, [claim(300 + i, "99214", "223.65", "100.00", dos="20260506") for i in range(2)], pay_date="")
    Detector(store, run_id="t").run()
    print("transactions without a payment date:", store.query("SELECT COUNT(*) FROM transactions WHERE payment_date IS NULL")[0][0])
    [p] = findings(store, "signal='SECOND_RATE'")
    ev = p["evidence"]
    print("lines:", ev["lines"], "| deadlines:", ev["deadlines"], "| claims without a date:", [c for c in ev["claims"] if c[3] is None])
    assert sum(n for _d, n, _c in ev["deadlines"]) == ev["lines"], "the pattern's deadlines cover fewer lines than it has"
    store.close()


# ======================================================================= the page in a browser, on invented data
import pytest  # noqa: E402


@pytest.fixture(scope="module")
def page_run(tmp_path_factory):
    """Payer name, plan name, a claim ID and a biller's note carrying HTML; a pattern with one window passed; a pattern
    kept from an earlier release (no deadlines); a takeback and a payment fee. Runs the real page in Chromium against a
    local server started on that invented store with no password."""
    import socket
    import subprocess
    import time
    import urllib.request
    import shutil
    if not shutil.which("node") or subprocess.run(["node", "-e", "require('playwright')"], capture_output=True, cwd=HERE).returncode != 0:
        pytest.skip("the browser check needs node with Playwright")
    tmp = tmp_path_factory.mktemp("page")
    work = tmp / "work"
    built = subprocess.run([sys.executable, os.path.join(HERE, "page_store.py"), str(work)], capture_output=True, text=True, cwd=REPO, timeout=300)
    assert built.returncode == 0, built.stderr[-2000:]
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    env = dict(os.environ, REMITWATCH_DB=str(work / "s.sqlite"), REMITWATCH_PASSWORD="", REMITWATCH_SHOW_PHI="0")
    env.pop("VERCEL", None)
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "remitwatch.api:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
                           cwd=REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1)
                break
            except Exception:
                time.sleep(0.2)
        res = subprocess.run(["node", os.path.join(HERE, "page_check.js"), str(port), str(tmp)], capture_output=True, text=True, timeout=240)
    finally:
        srv.terminate()
        srv.wait(timeout=10)
    assert res.returncode == 0, res.stderr[-3000:]
    return json.loads(res.stdout.strip().splitlines()[-1])


def test_P4_the_page_escapes_file_text_and_survives_old_evidence(page_run):
    got = page_run
    out = got["out"]
    print(json.dumps({k: out[k] for k in ("injected", "hint_chips", "denial_hints")}, indent=1))
    print("plan pattern drawer:", out["plan_pattern"])
    print("legacy pattern drawer:", out["legacy_pattern"])
    print("errors:", got["errors"], "dialogs:", got["dialogs"], "http>=400:", got["failed"])
    assert all(not v["ids"] and not v["imgs"] for v in out["injected"].values()), out["injected"]
    assert all(not out[k]["injected"]["ids"] and not out[k]["injected"]["imgs"] for k in ("plan_pattern", "aetna_pattern", "legacy_pattern"))
    assert not got["errors"] and not got["dialogs"], (got["errors"], got["dialogs"])
    assert not out["plan_pattern"]["appeal_button"] and not out["legacy_pattern"]["appeal_button"]


def test_P5_the_takeback_dispute_and_fee_opt_out_letters_are_gone_from_the_page(page_run):
    """TAKEBACK and FEE_SKIM belong to a remittance, not a claim (claim_key is empty). The server still writes their
    dedicated letters (/api/appeal), but the drawer now shows the letter button only when there is a claim."""
    out = page_run["out"]
    print("fee rows:", out["fees_rows"])
    print("letters the server writes:", out["fee_letters"])
    print("drawers:", out["fees_drawers"])
    assert out["fee_letters"].get("TAKEBACK", "").startswith("Dispute of overpayment recoupment")
    assert out["fee_letters"].get("FEE_SKIM", "").startswith("Request for standard ACH")
    assert out["fees_drawers"]["TAKEBACK"]["appeal_button"] and out["fees_drawers"]["FEE_SKIM"]["appeal_button"], \
        "the recoupment dispute letter and the fee opt-out letter can no longer be opened from the finding"


def test_G3_next_years_underpaid_lines_join_last_years_dismissed_pattern(tmp_path, monkeypatch):
    """2026: a plan pattern the manager dismissed ("that plan's 2026 contract rate"). January 2027: this payer has too few
    clean 2027 lines for a 2027 baseline, so 2027 lines are measured against 2026's, and three 2027 visits underpaid at
    the same amount are grouped into the dismissed 2026 pattern: not open, and not under the 2027 year filter."""
    store = _plan_pattern(tmp_path, range(200, 206))
    Detector(store, run_id="one").run()
    [p] = findings(store, "signal='SECOND_RATE'")
    apply_status(store.conn, p["finding_id"], "dismissed", "HMO GOLD 2026 contract rate", actor="manager")
    store.conn.commit()
    store.close()
    store = build_store(tmp_path, [claim(700 + i, "99214", "223.65", "90.00", plan="HMO GOLD", dos="20270110") for i in range(3)], pay_date="20270125")
    Detector(store, run_id="two").run()
    rows = findings(store, "category IN ('underpayment','contract')")
    for r in rows:
        print((r["signal"], r["year"], r["status"], r["stale"], r["shortfall_cents"], r["evidence"].get("lines"), r["evidence"].get("last_visit")))
    store.close()
    c = api_client(tmp_path, monkeypatch)
    y27 = c.get("/api/findings?year=2027&status=all&category=underpayment").json()
    print("2027, any status:", y27["total"], y27["shortfall_cents"])
    assert y27["total"] >= 1, "three 2027 visits underpaid by $82.80 are inside the dismissed 2026 pattern: nothing for 2027 shows them"


def test_R4_three_indiana_visits_explain_ten_lines(tmp_path):
    """The office is known only on days the clinician also saw a Medicare patient. Of ten visits paid at $90.00, three
    fall on Indiana days and seven on days with no Medicare visit; of the usual-rate visits, three fall on Kentucky days.
    'Indiana visits' then explains all ten."""
    ky_days = ["20260302", "20260303", "20260304"]
    in_days = ["20260310", "20260311", "20260312"]
    med = [claim(1 + i, "99214", "223.65", "127.80", cfi="MB", dos=d) for i, d in enumerate(ky_days)]
    med += [claim(10 + i, "99214", "223.65", "127.80", cfi="MB", dos="20260105") for i in range(5)]
    store = build_store(tmp_path, med, payer="KY MEDICARE")
    store.close()
    store = build_store(tmp_path, [claim(20 + i, "99214", "223.65", "121.00", cfi="MB", dos=d) for i, d in enumerate(in_days)], payer="IN MEDICARE")
    store.close()
    claims = [claim(100 + i, "99214", "223.65", "117.60", dos=ky_days[i]) for i in range(3)]
    claims += [claim(103 + i, "99214", "223.65", "117.60", dos="20260320") for i in range(22)]
    claims += [claim(200 + i, "99214", "223.65", "90.00", dos=in_days[i]) for i in range(3)]
    claims += [claim(203 + i, "99214", "223.65", "90.00", dos="20260320") for i in range(7)]
    store = build_store(tmp_path, claims)
    Detector(store, run_id="t").run()
    rows = findings(store, "payer_group='Anthem / BCBS' AND category IN ('underpayment','contract') AND COALESCE(stale,0)=0")
    print([(r["signal"], r["provider_tier"], r["shortfall_cents"]) for r in rows])
    for r in rows:
        if r["signal"] == "SECOND_RATE":
            print("NOTE:", r["evidence"]["note"], "| by office:", r["evidence"]["by_office_state"])
    unexplained = [r for r in rows if r["signal"] == "UNDERPAID_VS_OWN_RATE"]
    assert len(unexplained) >= 7, "seven visits with no known office are folded under 'Indiana visits' on the strength of three"
    store.close()


def test_X1_a_dismissed_pattern_returns_as_new_when_the_clinicians_tier_becomes_known(tmp_path):
    """A plan pattern for a clinician the files could not yet classify (no Medicare visits): the manager dismisses it.
    Her first Medicare remittance arrives, her tier becomes 'physician', the pattern's identity changes and it is back as
    a new open finding with the same six claims."""
    claims = [claim(100 + i, "99214", "223.65", "117.60") for i in range(25)]
    claims += [claim(200 + i, "99214", "223.65", "90.00", plan="HMO GOLD") for i in range(6)]
    store = build_store(tmp_path, claims)
    Detector(store, run_id="one").run()
    [p] = findings(store, "signal='SECOND_RATE'")
    print("one:", p["provider_tier"], p["title"])
    apply_status(store.conn, p["finding_id"], "dismissed", "HMO GOLD contract rate, checked", actor="manager")
    store.conn.commit()
    store.close()
    store = build_store(tmp_path, [claim(i, "99214", "223.65", "127.80", cfi="MB") for i in range(1, 6)], payer="KY MEDICARE", pay_date="20260601")
    Detector(store, run_id="two").run()
    rows = findings(store, "signal='SECOND_RATE'")
    for r in rows:
        print("two:", (r["provider_tier"], r["status"], r["stale"], r["evidence"].get("lines")))
    assert not [r for r in rows if r["status"] == "open" and not r["stale"]], "the dismissed pattern is back as a new open finding"
    store.close()


def test_R2c_a_lone_biopsy_resubmitted_after_a_denial_is_called_the_multiple_procedure_rule(tmp_path):
    """Each 11102 is the only procedure of its visit. The first submission was denied for missing information and the
    resubmission (same bill, suffix C024) was paid at half: the denied line is counted as another procedure that day."""
    usual = [claim(100 + i, "11102", "180.00", "90.00") for i in range(25)]
    denied = [claim(200 + i, "11102", "180.00", "0.00", paid="0.00").replace("CAS*CO*45*180.00~", "CAS*CO*16*180.00~") for i in range(6)]
    store = build_store(tmp_path, usual + denied, payer="AETNA", pay_date="20260415")
    store.close()
    half = [claim(200 + i, "11102", "180.00", "45.00", pcn=f"CB{200 + i:07d}C024") for i in range(6)]
    store = build_store(tmp_path, half, payer="AETNA", pay_date="20260520")
    Detector(store, run_id="t").run()
    rows = findings(store, "signal IN ('SECOND_RATE','UNDERPAID_VS_OWN_RATE') AND COALESCE(stale,0)=0")
    print([(r["signal"], r["shortfall_cents"]) for r in rows])
    for r in rows:
        if r["signal"] == "SECOND_RATE":
            print("NOTE:", r["evidence"]["note"][:160])
    assert [r["signal"] for r in rows] == ["UNDERPAID_VS_OWN_RATE"] * 6, \
        "a lone procedure paid at half on a resubmission is grouped as 'the multiple-procedure rule' because its own denied first submission counts as another procedure"
    store.close()


# ======================================================================= checks that hold (kept as evidence for the verdict)
def test_V1_two_deadline_rules_in_one_pattern_keep_each_claims_own_date(tmp_path):
    """Anthem commercial (two years) and a Federal Employee Program plan (180 days) in one pattern, paid in two years."""
    med = [claim(i, "99214", "223.65", "127.80", cfi="MB", dos="20260105") for i in range(1, 26)]
    store = build_store(tmp_path, med, payer="KY MEDICARE", pay_date="20260120")
    store.close()
    store = build_store(tmp_path, [claim(100 + i, "99214", "223.65", "117.60", dos="20260105") for i in range(25)], pay_date="20260120")
    store.close()
    store = build_store(tmp_path, [claim(200 + i, "99214", "223.65", "100.00", dos="20261201", plan="FEDERAL EMPLOYEE PROGRAM") for i in range(3)], pay_date="20261215")
    store.close()
    store = build_store(tmp_path, [claim(300 + i, "99214", "223.65", "100.00", dos="20261202") for i in range(3)], pay_date="20270110")
    Detector(store, run_id="t").run()
    [p] = findings(store, "signal='SECOND_RATE'")
    ev = p["evidence"]
    print(p["appeal_deadline"], "|", p["hint"], "|", ev["deadlines"], "|", p["deadline_basis"][:140])
    assert [d for d, _n, _c in ev["deadlines"]] == ["2027-06-13", "2029-01-09"]
    assert p["appeal_deadline"] == "2029-01-09" and p["hint"] == "Filing windows close 2027-06-13 to 2029-01-09"
    assert sum(n for _d, n, _c in ev["deadlines"]) == 6 and sum(c for _d, _n, c in ev["deadlines"]) == p["shortfall_cents"] == 6 * 1760
    assert sorted(c[3] for c in ev["claims"]) == ["2027-06-13"] * 3 + ["2029-01-09"] * 3
    store.close()


def test_V2_grouping_keeps_the_dollars_of_the_one_offs_it_replaces(tmp_path, monkeypatch):
    claims = [claim(100 + i, "99214", "223.65", "117.60") for i in range(25)]
    claims += [claim(200 + i, "99214", "223.65", "90.00", plan="HMO GOLD") for i in range(6)] + [claim(300, "99214", "223.65", "95.00")]
    store = build_store(tmp_path, claims)
    monkeypatch.setattr(det_mod, "SECOND_RATE_MIN_LINES", 10 ** 6)
    Detector(store, run_id="a").run()
    q = "SELECT COUNT(*), SUM(shortfall_cents), SUM(expected_cents) FROM findings WHERE category='underpayment' AND COALESCE(stale,0)=0"
    before = tuple(store.conn.execute(q).fetchone())
    monkeypatch.setattr(det_mod, "SECOND_RATE_MIN_LINES", 5)
    Detector(store, run_id="b").run()
    after = tuple(store.conn.execute(q).fetchone())
    [p] = findings(store, "signal='SECOND_RATE'")
    print("one-offs:", before, "| grouped:", after, "| pattern lines:", p["evidence"]["lines"])
    assert before[1:] == after[1:] and before[0] == 7 and after[0] == 2 and p["evidence"]["lines"] == 6
    store.close()


def test_C1_the_csv_of_the_work_queue_lists_a_pattern_without_its_claims(tmp_path, monkeypatch):
    store = _plan_pattern(tmp_path, range(200, 206))
    Detector(store, run_id="t").run()
    store.close()
    c = api_client(tmp_path, monkeypatch)
    text = c.get("/api/findings.csv?category=downcoding,bundling,denial,underpayment").text
    import csv as _csv
    import io
    rows = list(_csv.DictReader(io.StringIO(text)))
    sr = [r for r in rows if r["signal"] == "SECOND_RATE"]
    print("pattern row:", {k: sr[0][k] for k in ("title", "patient_control_number", "service_date", "appeal_deadline", "hint", "shortfall_cents")})
    claim_ids = [f"CB{200 + i:07d}C017" for i in range(6)]
    assert any(cid in text for cid in claim_ids), "the export carries the pattern's dollars and last deadline but none of its six claim IDs or their dates"
