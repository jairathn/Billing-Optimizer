"""What holds in this area (each test here passes on b8c3146): the cloud cycle measures the EMA exports on the full store
and the serverless site serves the record; the home screen's data is the same on the dashboard's copy as on the full
store; a store saved by the live release (6dcc450) answers every new route and shows no EMA panel until the first
analysis by this release; empty stores; no patient name, member number or claim id in /api/brief or /api/coverage; a kind
for every signal the detector emits as work, including those set through a variable. Invented data only."""
from __future__ import annotations

import base64
import json
import os
import re
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from r6cov_h import PW, REPO, client, clm, identifiers, live_tree, pcn, rich_store, run_tree, sv  # noqa: E402

from remitwatch import cloud, insights, link  # noqa: E402
from remitwatch.detect import ensure_schema, run_detection  # noqa: E402
from remitwatch.store import Store  # noqa: E402
from tests.test_cloud import DATA, box, cycle, raw_conn  # noqa: E402,F401  (box is a fixture)
from tests.test_ema_coverage import files as cov_files  # noqa: E402
from tests.test_ema_coverage import rows as cov_rows  # noqa: E402
from tests.test_activity import write_files  # noqa: E402
from tests.test_remitwatch import remit  # noqa: E402

TODAY = "2026-11-13"
FLAGGED = [{"week": "2026-03-09", "claims": 41, "in_ema": 10}, {"week": "2026-03-16", "claims": 40, "in_ema": 0}]


def flagged(ema: dict) -> list[dict]:
    """The weeks the Data tab lists (index.html and insights.data_health apply the same test)."""
    return [w for w in ema.get("weeks") or [] if w["claims"] >= (ema.get("min_claims") or 30) and w["in_ema"] < (ema.get("share") or 0.8) * w["claims"]]


def write_export(path, rows):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Service Date", "Primary Provider", "Primary Provider Role", "Claim ID", "CPT/Product", "Units", "Charges", "Activity Type", "Location"])
    for d, cid in rows:
        ws.append([d, "Doctor, Alex", "Doctor", cid, "99213", 1, 150.0, "Charge", "North Office"])
    wb.save(path)


def test_the_cloud_cycle_measures_the_exports_and_the_serverless_site_serves_them(box, monkeypatch):  # noqa: F811
    from fastapi.testclient import TestClient

    from remitwatch import api
    root, dbx, state = box
    folder = root / DATA
    os.makedirs(folder, exist_ok=True)
    weeks = (("20260303", "2026-03-03", 40), ("20260310", "2026-03-10", 10), ("20260317", "2026-03-17", 40))
    claims = [clm(pcn(100 * k + j), "1", sv("99213", "150.00", "72.00", "90.00", "CAS*CO*45*60.00~", dos=d8), dos=d8, charge="150.00", paid="72.00")
              for k, (d8, _d, _n) in enumerate(weeks) for j in range(40)]
    for i in range(0, len(claims), 40):
        (folder / f"r{i:04d}.RMT").write_bytes(remit("".join(claims[i:i + 40]), "100.00", seg_count=99, isa_ctrl=f"{i + 1:09d}", pay_date="20260415"))
    write_export(folder / "Encounter Charge Lines.xlsx", [(d, pcn(100 * k + j)) for k, (_d8, d, n) in enumerate(weeks) for j in range(n)])
    out = cycle(state, dbx)
    assert out["ingest"]["ema"] == 1 and out["detect"]["findings"] >= 0 and out["serving"]["gz_bytes"] > 0
    full = raw_conn(state)
    record = link.stored_ema_coverage(full)
    assert flagged(record) == [{"week": "2026-03-09", "claims": 40, "in_ema": 10}]

    monkeypatch.setenv("REMITWATCH_MODE", "serverless")
    monkeypatch.setenv("REMITWATCH_STATE_DIR", str(root / "RemitWatch-state"))
    monkeypatch.setenv("REMITWATCH_PASSWORD", PW)
    monkeypatch.setattr(api, "PASSWORD", PW)
    monkeypatch.setattr(api, "USERNAME", "oda")
    monkeypatch.setattr(cloud, "SERVING_COPY", cloud.ServingCopy())
    tc = TestClient(api.app)
    auth = {"Authorization": "Basic " + base64.b64encode(f"oda:{PW}".encode()).decode()}
    served = tc.get("/api/coverage", headers=auth).json()
    assert served["ema"] == record                                   # measured on the full store, served from the copy
    assert tc.get("/api/brief", headers=auth).json()["ready"] is True
    copy = cloud.SERVING_COPY.connection()
    assert copy.execute("SELECT COUNT(*) FROM lines").fetchone()[0] < full.execute("SELECT COUNT(*) FROM lines").fetchone()[0]
    assert insights.brief(copy, today=TODAY) == insights.brief(full, today=TODAY)
    copy.close()
    full.close()


def test_the_home_screen_data_is_the_same_on_the_dashboard_copy(tmp_path):
    db = rich_store(tmp_path)
    run_detection(db)
    st = Store(db)
    path = str(tmp_path / "serving.sqlite")
    cloud.build_serving(st.conn, path)
    copy = sqlite3.connect(path)
    full_brief, copy_brief = insights.brief(st.conn, today=TODAY), insights.brief(copy, today=TODAY)
    assert full_brief == copy_brief
    sigs = {r[0] for r in st.conn.execute("SELECT DISTINCT signal FROM findings")}
    assert {"SECOND_RATE", "CHARGE_BELOW_RATE", "FEE_SKIM", "DENIED_AUTH", "DENIED_MISSING_MODIFIER", "CLAIM_DENIED_UNPAID"} <= sigs, sigs
    assert full_brief["contracts"]["payers"] and full_brief["fees"]["charge_caps"] and full_brief["fees"]["payment_fees"]
    copy.close()
    st.close()


def test_a_store_saved_by_the_live_release_answers_and_shows_the_panel_after_the_first_analysis(tmp_path, monkeypatch):
    live = live_tree()
    if live is None:
        pytest.skip("needs the live release (6dcc450) in git history")
    db = str(tmp_path / "s.sqlite")
    st = Store(db)
    st.ingest_835_paths(write_files(tmp_path, cov_files()))
    st.ingest_ema_rows(cov_rows())
    st.close()
    run_tree(live, db, f"""
        run_detection({db!r})
        c = sqlite3.connect({db!r})
        result = [r[0] for r in c.execute("SELECT key FROM meta")]
    """)
    tc, auth = client(monkeypatch, db)
    for url in ("/api/meta", "/api/summary", "/api/progress", "/api/brief", "/api/coverage"):
        assert tc.get(url, auth=auth).status_code == 200, url
    cov = tc.get("/api/coverage", auth=auth).json()
    assert cov["ema"]["weeks"] == [] and cov["ready"] is True                   # no panel until this release analyses the store
    assert tc.get("/api/brief", auth=auth).json()["ready"] is True
    run_detection(db)                                                           # the release's first cycle (its version differs)
    assert flagged(tc.get("/api/coverage", auth=auth).json()["ema"]) == FLAGGED


def test_empty_stores(tmp_path, monkeypatch):
    bare = str(tmp_path / "bare.sqlite")
    Store(bare).close()                                       # no derived table at all
    tc, auth = client(monkeypatch, bare)
    assert tc.get("/api/brief", auth=auth).json() == {"ready": False}
    cov = tc.get("/api/coverage", auth=auth).json()
    assert cov["ready"] is False and cov["ema"]["weeks"] == []
    schema = str(tmp_path / "schema.sqlite")
    c = sqlite3.connect(schema)
    ensure_schema(c)                                          # findings and the rate tables, empty; no transactions table
    c.close()
    b = insights.brief(sqlite3.connect(schema), today=TODAY)
    assert b["ready"] and b["newest_payment"] is None and b["work"]["total"]["n"] == 0 and b["health"] == []
    serving = str(tmp_path / "serving.sqlite")
    cloud.empty_serving(serving)                              # the dashboard's copy before the first load
    b = insights.brief(sqlite3.connect(serving), today=TODAY)
    assert b["ready"] and b["contracts"] == {"year": None, "payers": []} and b["causes"] == []
    assert link.ema_coverage(sqlite3.connect(serving))["weeks"] == []


def test_no_patient_name_member_number_or_claim_id_on_the_home_screen_data(tmp_path, monkeypatch):
    db = rich_store(tmp_path)
    run_detection(db)
    secrets = identifiers(db)
    assert {"QUIXOTEMBA", "ZQ99887766", pcn(1)} <= secrets
    tc, auth = client(monkeypatch, db)
    for url in ("/api/brief", "/api/coverage"):
        text = json.dumps(tc.get(url, auth=auth).json()).upper()
        words = set(re.findall(r"[A-Z0-9]{4,}", text))
        assert not (words & secrets), (url, sorted(words & secrets))


def test_every_signal_the_detector_emits_as_work_has_a_kind():
    src = open(os.path.join(REPO, "remitwatch", "detect.py"), encoding="utf-8").read()
    literal = set(re.findall(r'"([A-Z][A-Z0-9_]+)",\s*"(?:denial|bundling|downcoding|underpayment)"', src))   # _emit(...) and one_off tuples
    through_variable = set(re.findall(r'sig, sev, act, ttl = "([A-Z][A-Z0-9_]+)"', src))                       # the visit bundling rules
    assert "UNDERPAID_VS_OWN_RATE" in literal and "EM_BUNDLED_MOD25" in through_variable
    assert (literal | through_variable) <= insights.WORK_SIGNALS, (literal | through_variable) - insights.WORK_SIGNALS
