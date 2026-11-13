"""The release's API and weekly report on a store the live release (3f8855b) analysed and people worked, before the
release re-analyses it (on a serverless host no migration runs when the site starts; the dashboard reads the copy the
last run built). Every route the page calls must answer. Invented data only."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lvn import LIVE, REL, act, add, base, clean_visits, clm, detect, run_tree, sv  # noqa: E402

XHR = {"X-Requested-With": "remitwatch"}


def live_store(tmp_path):
    under = [c.replace("117.60", "100.00").replace("106.05", "123.65") for c in clean_visits(501, 6, "99214", "223.65", "117.60")]
    base(tmp_path, extra=under)
    add(tmp_path, [clm("CB0000920C017", "4", "223.65", "0.00", sv("99214", "223.65", "0.00", cas="CAS*CO*16*223.65~"), payer_ctl="A920"),
                   clm("CB0000921C017", "1", "223.65", "0.00", sv("99214", "223.65", "0.00", cas="CAS*CO*197*223.65~"), payer_ctl="A921"),
                   clm("CB0000922C017", "1", "223.65", "76.15", sv("99213", "223.65", "76.15", cas="CAS*CO*45*147.50~", allowed="76.15", orig="99214"),
                       payer_ctl="A922")],
        "20260415", plb="PLB*999999999*20261231*WO:CB0000001C017*40.00~")
    add(tmp_path, [clm("CB0000921C024", "1", "223.65", "117.60", sv("99214", "223.65", "117.60", cas="CAS*CO*45*106.05~", allowed="117.60"),
                       payer_ctl="A921B")], "20260520")
    db = str(tmp_path / "s.sqlite")
    rows = detect(LIVE, db, "live-1", "2026-05-25T09:00:00+00:00")
    run_tree(LIVE, db, """
        from remitwatch.alerts import run_alerts
        run_alerts(s.conn)
        result = 1
    """)
    act(LIVE, db, "CB0000920C017", "CLAIM_DENIED_UNPAID", "appealed", at="2026-05-26T15:00:00+00:00")
    act(LIVE, db, "CB0000922C017", "DOWNCODE_SVC06", "lost", "Payer upheld", at="2026-05-26T15:01:00+00:00")
    return db, rows


def test_every_route_answers_on_a_store_the_live_release_left(tmp_path, monkeypatch):
    db, rows = live_store(tmp_path)
    print(sorted({r["signal"] for r in rows}))
    from fastapi.testclient import TestClient
    import remitwatch.api as api_mod
    monkeypatch.setattr(api_mod, "DB_PATH", db)
    monkeypatch.setattr(api_mod, "PASSWORD", "")
    monkeypatch.setattr(api_mod, "SHOW_PHI", False)
    c = TestClient(api_mod.app)
    failures = []

    def get(url):
        r = c.get(url)
        if r.status_code != 200:
            failures.append((url, r.status_code, r.text[:300]))
        return r

    for url in ["/api/meta", "/api/summary", "/api/summary?status=all", "/api/findings", "/api/findings?deadline=due",
                "/api/findings?deadline=expired", "/api/findings?deadline=valid", "/api/findings?category=underpayment",
                "/api/findings?sort=deadline", "/api/findings.csv", "/api/progress", "/api/alerts", "/api/rates", "/api/trends",
                "/api/payers", "/api/providers", "/api/codes", "/api/volume", "/api/coverage",
                "/api/breakdown?dim=payer&family=denial", "/api/breakdown?dim=payer&dim2=billed_code&family=underpayment",
                "/api/breakdown?dim=month&family=downcoding", "/api/rates/matrix?year=2026&tier=physician"]:
        get(url)
    for r in rows:
        get(f"/api/findings/{r['finding_id']}")
        if r.get("claim_key") or r["signal"] in ("TAKEBACK", "FEE_SKIM"):
            get(f"/api/appeal/{r['finding_id']}")
    one = next(r for r in rows if r["signal"] == "DENIED_AUTH" or r["category"] == "denial")
    p = c.post(f"/api/findings/{one['finding_id']}/status", json={"status": "in_progress"}, headers=XHR)
    if p.status_code != 200:
        failures.append(("status", p.status_code, p.text[:300]))
    report = run_tree(REL, db, """
        from remitwatch.report import build_report
        result = build_report(s, detail=True)[:200]
    """)
    print(report)
    # then the release's first analysis and alerts on that store, and every route again
    from lvn import detect
    rows2 = detect(REL, db, "rel-1", "2026-06-02T09:00:00+00:00")
    run_tree(REL, db, """
        from remitwatch.alerts import run_alerts
        run_alerts(s.conn)
        from remitwatch.report import build_report
        result = build_report(s, detail=True)[:50]
    """)
    for url in ["/api/summary", "/api/findings", "/api/findings?deadline=due", "/api/findings.csv", "/api/progress", "/api/alerts",
                "/api/breakdown?dim=payer&family=all", "/api/breakdown?dim=signal&family=underpayment"]:
        get(url)
    for r in rows2:
        get(f"/api/findings/{r['finding_id']}")
    print("release first run:", sorted({(r["signal"], r["status"], r["stale"]) for r in rows2}))
    assert not failures, failures
