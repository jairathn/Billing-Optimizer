"""The Data tab's list of service weeks that the EMA charge exports miss (remitwatch.link.ema_coverage)."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch import link  # noqa: E402
from remitwatch.detect import run_detection  # noqa: E402
from remitwatch.store import Store  # noqa: E402
from tests.test_activity import PW, clp, ema_row, svc, visit, write_files  # noqa: E402

WEEKS = (("20260303", range(1, 41)), ("20260310", range(41, 81)), ("20260317", range(81, 121)), ("20260324", range(121, 131)))


def files():
    claims = [visit(n, d) for d, numbers in WEEKS for n in numbers]
    # the second payer for visit 1 writes the bill's next submission suffix: the export holds it under the first
    claims.append(clp(1, svc("99213", "150.00", "10.00", None, "20260303"), status="2", suffix="C024"))
    # a reversal of a visit no other remittance mentions is not a claim of that week
    claims.append(clp(900, svc("99213", "-150.00", "-72.00", None, "20260317"), status="22"))
    # a claim with services in two weeks belongs to the week of its first one
    claims.append(clp(131, svc("17000", "110.00", "48.00", "60.00", "20260310") + svc("17003", "40.00", "16.00", "20.00", "20260317")))
    return [("20260401", "ANTHEM", claims)]


def rows():
    out, i = [], 0
    for n in range(1, 41):                                   # the week of 2 March, pulled late enough: every visit
        out.append(ema_row(i := i + 1, "Encounter full 3-2.xlsx", "2026-03-03", "Alpha, Ann", "99213", patient=f"P{n}", bill_no=n, created="2026-03-06"))
    for n in range(41, 51):                                  # the week of 9 March, pulled as it ended: a quarter of it
        out.append(ema_row(i := i + 1, "Encounter full 3-9.xlsx", "2026-03-10", "Alpha, Ann", "99213", patient=f"P{n}", bill_no=n, created="2026-03-13"))
    # one stray old row does not move the start of the exports back a year
    out.append(ema_row(i := i + 1, "Encounter full 3-9.xlsx", "2025-01-07", "Alpha, Ann", "99213", patient="P5000", bill_no=5000, created="2025-01-08"))
    return out


def test_weeks_the_exports_miss_are_measured_on_the_full_store_and_served_from_the_run(tmp_path, monkeypatch):
    db = str(tmp_path / "s.sqlite")
    store = Store(db)
    store.ingest_835_paths(write_files(tmp_path, files()))
    assert link.stored_ema_coverage(store.conn)["weeks"] == []          # nothing measured before a run
    store.ingest_ema_rows(rows())
    store.close()
    run_detection(db)

    store = Store(db)
    cov = link.stored_ema_coverage(store.conn)
    assert cov["first_week"] == "2026-03-02" and cov["last_ema_service"] == "2026-03-10"
    assert cov["share"] == link.EMA_WEEK_SHARE and cov["min_claims"] == link.EMA_WEEK_MIN_CLAIMS
    assert cov["weeks"] == [{"week": "2026-03-02", "claims": 41, "in_ema": 41},
                            {"week": "2026-03-09", "claims": 41, "in_ema": 10},
                            {"week": "2026-03-16", "claims": 40, "in_ema": 0},
                            {"week": "2026-03-23", "claims": 10, "in_ema": 0}]
    measured = cov.pop("measured_at")
    assert link.ema_coverage(store.conn) == cov                        # what the run saved is what the store holds
    assert measured[:2] == "20" and "T" in measured                      # and says when it was measured
    store.close()

    from fastapi.testclient import TestClient

    from remitwatch import api
    monkeypatch.setattr(api, "DB_PATH", db)
    monkeypatch.setattr(api, "PASSWORD", PW)
    monkeypatch.setattr(api, "USERNAME", "oda")
    monkeypatch.delenv("REMITWATCH_MODE", raising=False)
    tc, auth = TestClient(api.app), ("oda", PW)
    assert tc.get("/api/coverage").status_code == 401
    served = tc.get("/api/coverage", auth=auth).json()
    assert served["ema"]["weeks"] == cov["weeks"] and served["ema"]["min_claims"] == 30

    page = open(os.path.join(os.path.dirname(link.__file__), "static", "index.html"), encoding="utf-8").read()
    assert "EMA charge exports that miss visits" in page and "w.in_ema < (ew.share||0.8) * w.claims" in page


def test_a_store_without_exports_or_with_a_broken_record_shows_nothing(tmp_path):
    store = Store(str(tmp_path / "s.sqlite"))
    store.ingest_835_paths(write_files(tmp_path, files()))
    assert link.ema_coverage(store.conn)["weeks"] == []
    store.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, 'not json')", (link.EMA_COVERAGE_KEY,))
    assert link.stored_ema_coverage(store.conn)["weeks"] == []
    store.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, '[1, 2]')", (link.EMA_COVERAGE_KEY,))
    assert link.stored_ema_coverage(store.conn)["weeks"] == []
    store.close()
