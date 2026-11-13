"""The release's (752c700) API on a store the live release (3f8855b) analysed and people worked, with whole-claim denials
listed twice, a supplement's denial and corrected claims: before the release's first analysis, after it and a week
later. Every route the page calls must answer. Invented data only."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from frh import LIVE, REL, act, add_to, base, clean_visits, clm, detect, run_tree, sv  # noqa: E402

XHR = {"X-Requested-With": "remitwatch"}


def store(tmp_path):
    base(tmp_path)
    db = str(tmp_path / "s.sqlite")
    med = [c.replace("*12*PC", "*MB*PC").replace("SAMPLE*PAT", "MEDI*PAT") for c in clean_visits(601, 25, "99213", "156.66", "76.15")]
    add_to(tmp_path, db, med, "20260105", payer="KY MEDICARE", payer_id="MCRKY")
    d = lambda pcn, ctl, who="ELM*BO****MI*W55500302": clm(pcn, "4", "223.65", "0.00", sv("99214", "223.65", "0.00", cas="CAS*CO*16*223.65~"),  # noqa: E731
                                                           payer_ctl=ctl, who=who)
    add_to(tmp_path, db, [clm("CB0000990C017", "19", "156.66", "0.00", sv("99213", "156.66", "0.00", cas="CAS*PR*1*76.15~CAS*CO*45*80.51~", allowed="76.15"),
                              payer_ctl="MCR990", pr="76.15", cfi="MB", who="SUP*PA****MI*W55500990")], "20260120", payer="KY MEDICARE", payer_id="MCRKY")
    add_to(tmp_path, db, [clm("CB0000990C017", "4", "156.66", "0.00", sv("99213", "156.66", "0.00", cas="CAS*CO*31*156.66~"), payer_ctl="SUP990", cfi="CI",
                              who="SUP*PA****MI*W55500990")], "20260204", payer="AARP MEDICARE SUPPLEMENT", payer_id="AARPS")
    add_to(tmp_path, db, [d("CB0000980C017", "A980A"), d("CB0000985C017", "A985A", "DUO*LI****MI*W55500985")], "20260415")
    detect(LIVE, db, "live-1", "2026-04-16T09:00:00+00:00")
    add_to(tmp_path, db, [d("CB0000980C024", "A980B"),
                          clm("CB0000985C024", "1", "223.65", "0.00", sv("99214", "223.65", "0.00", cas="CAS*CO*16*223.65~"), payer_ctl="A985B",
                              who="DUO*LI****MI*W55500985")], "20260506")
    rows = detect(LIVE, db, "live-2", "2026-05-07T09:00:00+00:00")
    act(LIVE, db, "CB0000980C017", "CLAIM_DENIED_UNPAID", "dismissed", at="2026-05-12T15:00:00+00:00")
    act(LIVE, db, "CB0000985C017", "CLAIM_DENIED_UNPAID", "in_progress", at="2026-05-12T15:01:00+00:00")
    act(LIVE, db, "CB0000985C024", "DENIED_MISSING_INFO", "dismissed", at="2026-05-12T15:02:00+00:00")
    act(LIVE, db, "CB0000990C017", "CLAIM_DENIED_UNPAID", "in_progress", at="2026-05-12T15:03:00+00:00")
    return db, rows


def check(c, rows, failures):
    def get(url):
        r = c.get(url)
        if r.status_code != 200:
            failures.append((url, r.status_code, r.text[:300]))
        return r
    for url in ["/api/meta", "/api/summary", "/api/summary?status=all", "/api/summary?status=in_progress", "/api/findings",
                "/api/findings?status=all", "/api/findings?status=in_progress", "/api/findings?status=dismissed", "/api/findings?deadline=due",
                "/api/findings?category=denial&status=all", "/api/findings.csv", "/api/findings.csv?status=all", "/api/progress", "/api/alerts",
                "/api/breakdown?dim=payer&family=denial", "/api/breakdown?dim=signal&family=all"]:
        get(url)
    for r in rows:
        get(f"/api/findings/{r['finding_id']}")
        if r.get("claim_key"):
            get(f"/api/appeal/{r['finding_id']}")


def test_every_route_answers_through_the_first_runs(tmp_path, monkeypatch):
    db, live_rows = store(tmp_path)
    from fastapi.testclient import TestClient
    import remitwatch.api as api_mod
    monkeypatch.setattr(api_mod, "DB_PATH", db)
    monkeypatch.setattr(api_mod, "PASSWORD", "")
    monkeypatch.setattr(api_mod, "SHOW_PHI", False)
    c = TestClient(api_mod.app)
    failures = []
    check(c, live_rows, failures)
    rel = detect(REL, db, "rel-1", "2026-05-19T09:00:00+00:00")
    run_tree(REL, db, """
        from remitwatch.alerts import run_alerts
        run_alerts(s.conn)
        from remitwatch.report import build_report
        result = build_report(s, detail=True)[:50]
    """)
    check(c, rel, failures)
    stale = [r for r in rel if r.get("stale")]
    print("stale after the first run:", [(r["patient_control_number"], r["signal"], r["status"]) for r in stale])
    rel2 = detect(REL, db, "rel-2", "2026-05-26T09:00:00+00:00")
    check(c, rel2, failures)
    one = next(r for r in rel2 if r["patient_control_number"] == "CB0000985C017")
    p = c.post(f"/api/findings/{one['finding_id']}/status", json={"status": "appealed"}, headers=XHR)
    if p.status_code != 200:
        failures.append(("status", p.status_code, p.text[:300]))
    assert not failures, failures
