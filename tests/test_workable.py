"""
Money that can still be worked and is not being worked: items whose disputed money the biller wrote off in EMA while the
payer can still be asked for it (build 1), and claims EMA shows open that the payer never answered (build 2). Every
patient, claim and amount is made up.
"""
from __future__ import annotations

import os
import sqlite3

import pytest

from tests.test_features import NPI, build_store, clean_visits, svc

XHR = {"X-Requested-With": "remitwatch"}
DENIED = "CB0000300C017"
WHOLE = "CB0000400C017"


def denied_claims() -> list[str]:
    """A visit denied for medical necessity (CO-50, a line), and a whole claim denied for missing information (CLP02 4)."""
    line = ("CLP*CB0000300C017*1*223.65*0.00*0.00*12*PC300*11~NM1*QC*1*SAMPLE*PAT*Q***MI*M00000300~"
            f"NM1*82*2*TEST PROVIDER*****XX*{NPI}~DTM*232*20261101~" + svc("99214", "223.65", "0.00", None, "CAS*CO*50*223.65~", dos="20261101"))
    whole = ("CLP*CB0000400C017*4*150.00*0.00*0.00*12*PC400*11~NM1*QC*1*ORCHARD*LEE****MI*W00000400~"
             f"NM1*82*2*TEST PROVIDER*****XX*{NPI}~DTM*232*20261101~" + svc("99213", "150.00", "0.00", None, "CAS*CO*16*150.00~", dos="20261101"))
    return clean_visits(1, 25, "99214", "223.65", "117.60") + [line, whole]


def era(conn, rows) -> None:
    """ERA & Payment Line Adjustments rows, as the export gives them: (fingerprint, source, claim, code, reason, how, cents, date)."""
    conn.executemany("""INSERT INTO ema_rows (row_fingerprint, family, source_name, row_index, claim_id, cpt, service_date, carc, adjustment_status,
                        adjustments_cents, transaction_date) VALUES (?, 'era_lines', ?, 0, ?, ?, '2026-11-01', ?, ?, ?, ?)""", rows)
    conn.commit()


@pytest.fixture()
def store(tmp_path):
    from remitwatch.detect import Detector
    s = build_store(tmp_path, denied_claims(), pay_date="20261110")
    era(s.conn, [
        ("w1", "ERA & Payment Lines.xlsx", DENIED, "99214", "CO-50", "WRITE_OFF", 22365, "2026-11-12"),
        ("w1b", "ERA & Payment Lines (1).xlsx", DENIED, "99214", "CO-50", "WRITE_OFF", 22365, "2026-11-12"),   # the same write-off, another export
        ("w2", "ERA & Payment Lines.xlsx", DENIED, "99214", "PR-1", "TRANSFER_RESPONSIBILITY", 2000, "2026-11-12"),
        ("w3", "ERA & Payment Lines.xlsx", DENIED, "99214", "PR-2", "WRITE_OFF", 500, "2026-11-12"),          # the patient's share: not the payer's
        ("w4", "ERA & Payment Lines.xlsx", WHOLE, "99213", "CO-16", "WRITE_OFF", 15000, "2026-11-13"),
    ])
    Detector(s, run_id="t").run()
    return s


def test_the_disputed_money_written_off_in_ema_is_recorded_on_its_item(store):
    from remitwatch.detect import written_off_of
    f = {r["signal"]: dict(r) for r in store.query("SELECT signal, patient_control_number, written_off_cents, written_off_on, ema_handling FROM findings")}
    line = f["DENIED_MEDICAL_NECESSITY"]
    assert line["written_off_cents"] == 22365 and line["written_off_on"] == "2026-11-12"      # once, though two exports carry it
    whole = next(v for k, v in f.items() if v["patient_control_number"] == WHOLE)
    assert whole["written_off_cents"] == 15000 and whole["written_off_on"] == "2026-11-13"       # a whole claim: its lines' write-offs
    assert written_off_of([{"adjustment_status": "TRANSFER_RESPONSIBILITY", "carc": "CO-50", "adjustments_cents": 100}]) == (None, None)
    assert written_off_of([{"adjustment_status": "WRITE_OFF", "carc": "PR-3", "adjustments_cents": 100}]) == (None, None)
    assert written_off_of([{"adjustment_status": "write_off", "carc": "OA-23", "adjustments_cents": -250, "transaction_date": "2026-11-02"}]) == (250, "2026-11-02")
    assert all(v["written_off_cents"] is None for v in f.values() if v["patient_control_number"] not in (DENIED, WHOLE))


@pytest.fixture()
def site(store, monkeypatch):
    from fastapi.testclient import TestClient

    import remitwatch.api as api
    store.close()
    db = os.path.join(os.path.dirname(store.db_path), "s.sqlite")
    api.ensure_server_schema(db)
    monkeypatch.delenv("REMITWATCH_MODE", raising=False)
    monkeypatch.setattr(api, "DB_PATH", db)
    monkeypatch.setattr(api, "PASSWORD", "")
    return type("Site", (), {"c": TestClient(api.app), "db": db})


def test_the_list_the_tiles_the_counts_and_today_agree(site):
    c = site.c
    cats = "denial,bundling,downcoding,underpayment"
    items = c.get(f"/api/findings?category={cats}&status=open,in_progress&deadline=valid&live=1&handling=written_off&limit=50").json()
    ids = {i["patient_control_number"] for i in items["items"]}
    assert ids == {DENIED, WHOLE} and all(i["written_off_cents"] for i in items["items"])
    cents = sum(i["shortfall_cents"] or 0 for i in items["items"])
    wo = c.get("/api/brief").json()["written_off"]
    assert wo["n"] == 2 and wo["cents"] == cents and wo["written_off_cents"] == 22365 + 15000 and wo["since"] == "2026-11-12"
    assert wo["payers"][0]["payer_group"] == "Anthem / BCBS"
    desk = c.get(f"/api/desk?category={cats}&status=open,in_progress&deadline=valid&handling=written_off").json()
    assert desk["tiles"]["valid"]["n"] == 2 and desk["scope"]["n"] == 2
    counts = c.get(f"/api/worklist/counts?category={cats}&status=open,in_progress&deadline=valid&live=1&handling=written_off").json()
    assert counts["n"] == 2 and counts["cents"] == cents
    # without the filter the page is what it was
    assert c.get(f"/api/desk?category={cats}&status=open,in_progress&deadline=valid").json()["tiles"]["valid"]["n"] >= 2
    # the drawer has it too
    fid = next(i["finding_id"] for i in items["items"] if i["patient_control_number"] == DENIED)
    d = c.get(f"/api/findings/{fid}").json()["finding"]
    assert d["written_off_cents"] == 22365 and d["written_off_on"] == "2026-11-12"


def test_what_is_sent_or_past_its_window_is_not_counted(site):
    c = site.c
    fid = next(i["finding_id"] for i in c.get("/api/findings?handling=written_off&status=all&limit=50").json()["items"] if i["patient_control_number"] == DENIED)
    assert c.post(f"/api/findings/{fid}/status", json={"status": "appealed", "notes": "faxed"}, headers=XHR).status_code == 200
    assert c.get("/api/brief").json()["written_off"]["n"] == 1                # sent: being worked
    conn = sqlite3.connect(site.db)
    conn.execute("UPDATE findings SET appeal_deadline='2026-01-01' WHERE patient_control_number=?", (WHOLE,))
    conn.commit()
    conn.close()
    assert c.get("/api/brief").json()["written_off"]["n"] == 0                # its window closed: nothing left to work


def test_the_page_offers_it():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    today = open(os.path.join(root, "remitwatch", "static", "today.js")).read()
    desk = open(os.path.join(root, "remitwatch", "static", "desk.js")).read()
    feat = open(os.path.join(root, "remitwatch", "static", "features.js")).read()
    assert "title:'Work what EMA wrote off'" in today and "go:{handling:'written_off', sort:'shortfall'}" in today
    assert "handling:f.handling" in desk and "chip('handling', 'Written off in EMA, still workable')" in desk and "written off in EMA</span>" in desk
    assert "<b>Written off in EMA.</b>" in feat
