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


# ----------------------------------------------------------------------------------------------- build 2: no answer from the payer
def charge(conn, fp, claim, dos, created, payer="Anthem Blue Cross", cpt="99214", cents=22365, paid=0, act="Charge", item=None, status=None,
           sent=None, family="charge_lines", payer_id="00660"):
    conn.execute("""INSERT INTO ema_rows (row_fingerprint, family, source_name, row_index, claim_id, activity_type, bill_item_id, cpt, service_date,
                    bill_created, charges_cents, insurance_payments_cents, payer, payer_id, patient_id, provider, claim_status, claim_submission_date)
                    VALUES (?,?,'Encounter & Charge Lines.xlsx',0,?,?,?,?,?,?,?,?,?,?,'EMA1','Provider, Test',?,?)""",
                 (fp, family, claim, act, item or fp, cpt, dos, created, cents, paid, payer, payer_id, status, sent))


@pytest.fixture()
def open_claims(tmp_path):
    """Remittances to November 10, 2026 (Anthem, and Kentucky Medicare for one claim); an earlier submission of a bill denied
    on September 1; and EMA's exports with the claims that went out."""
    from remitwatch.detect import Detector
    from tests.test_features import claim
    s = build_store(tmp_path, [claim(506, svc("99213", "150.00", "0.00", None, "CAS*CO*16*150.00~", dos="20260803"), dos="20260803")],
                    pay_date="20260901")
    s.close()
    s = build_store(tmp_path, [claim(600, svc("99214", "223.65", "90.00", "110.00", "CAS*CO*45*113.65~", dos="20260810"), dos="20260810", cfi="MB")],
                    pay_date="20261105", payer="KY MEDICARE")
    s.close()
    s = build_store(tmp_path, clean_visits(1, 25, "99214", "223.65", "117.60"), pay_date="20261110")
    c = s.conn
    charge(c, "a", "CB0000501C017", "2026-09-14", "2026-09-15")                                   # sent, never answered: chase
    charge(c, "b", "CB0000502C017", "2026-11-01", "2026-11-02")                                   # sent 8 days before the newest remittance
    charge(c, "c", "CB0000503C017", "2026-09-14", "2026-09-15", paid=11760)                       # EMA shows the insurer paid
    charge(c, "d", "CB0000504C017", "2026-09-14", "2026-09-15", payer="Self Pay")                 # the patient's own bill
    charge(c, "e", "CB0000001C017", "2026-04-01", "2026-04-02")                                   # answered by a remittance
    charge(c, "f", "CB0000505C017", "2026-11-05", "2026-11-06")                                   # rejected before reaching the payer
    charge(c, "f2", "CB0000505C017", "2026-11-05", None, family="claim_status", status="Rejected", act=None, cpt=None, cents=None)
    charge(c, "g", "CB0000506C024", "2026-08-03", "2026-08-04", cpt="99213", cents=15000)         # sent again after the bill's denial
    charge(c, "g2", "CB0000506C024", "2026-08-03", None, family="claim_status", status="Submitted", sent="2026-09-20", act=None, cpt=None, cents=None)
    charge(c, "h", "CB0000507C017", "2026-09-14", "2026-09-15", item="i7")                       # voided
    charge(c, "h2", "CB0000507C017", "2026-09-14", "2026-09-15", act="Void Charge", item="i7")
    charge(c, "m1", "CB0000600C017", "2026-08-10", "2026-08-11", payer="Medicare Part B KY", payer_id="15102")   # answered: teaches the name
    charge(c, "m2", "CB0000601C017", "2026-09-10", "2026-09-11", payer="Medicare Part B KY", payer_id="15102")   # never answered
    charge(c, "n", "CB0000508C017", "2026-09-14", "2026-09-15", status="On Hold")                # EMA says it never went out
    c.commit()
    Detector(s, run_id="t").run()
    return s


def test_claims_the_payer_never_answered_are_listed_and_nothing_else(open_claims):
    rows = {r["patient_control_number"]: dict(r) for r in open_claims.query(
        "SELECT * FROM findings WHERE signal IN ('NO_ANSWER','REJECTED_PRE_PAYER')")}
    assert set(rows) == {"CB0000501C017", "CB0000505C017", "CB0000506C024", "CB0000601C017"}
    a = rows["CB0000501C017"]
    assert a["signal"] == "NO_ANSWER" and a["payer_group"] == "Anthem / BCBS" and a["status"] == "open"
    assert a["appeal_deadline"] == "2026-12-13" and "not confirmed" in a["deadline_basis"]          # 90 days from the date of service
    assert a["title"].startswith("Sent 2026-09-15, no answer from Anthem / BCBS in 56 days") and a["charge_cents"] == 22365
    assert a["shortfall_cents"] == 11760                                                           # the payer's usual rate, not the charge
    rej = rows["CB0000505C017"]
    assert rej["signal"] == "REJECTED_PRE_PAYER" and "(EMA: Rejected)" in rej["title"]             # at once, though sent days ago
    assert rows["CB0000506C024"]["signal"] == "NO_ANSWER"                                           # the bill's answer came before it was sent
    med = rows["CB0000601C017"]
    assert med["payer_group"] == "Medicare KY (CGS J15)" and med["payer_kind"] == "medicare"         # what the remittances call EMA's name
    assert med["appeal_deadline"] == "2027-09-10" and "not confirmed" not in med["deadline_basis"] and "42 CFR 424.44" in med["deadline_basis"]
    from remitwatch.insights import work_type
    assert work_type("NO_ANSWER", "review", None) == "chase" and work_type("REJECTED_PRE_PAYER", "correct_and_resubmit", None) == "fix"


def test_one_claim_keeps_its_item_and_its_work_as_its_status_changes(open_claims):
    from remitwatch.actions import apply_status
    from remitwatch.detect import Detector
    s, c = open_claims, open_claims.conn
    fid = s.query("SELECT finding_id FROM findings WHERE patient_control_number='CB0000501C017'")[0]["finding_id"]
    apply_status(c, fid, "in_progress", "called Anthem: not on file", actor="biller")
    c.commit()
    charge(c, "a2", "CB0000501C017", "2026-09-14", None, family="claim_status", status="Rejected", act=None, cpt=None, cents=None)
    c.commit()
    Detector(s, run_id="t2").run()
    r = s.query("SELECT finding_id, signal, status, notes FROM findings WHERE patient_control_number='CB0000501C017'")[0]
    assert r["finding_id"] == fid and r["signal"] == "REJECTED_PRE_PAYER" and r["status"] == "in_progress" and "not on file" in (r["notes"] or "")


def test_the_page_counts_them_and_today_points_at_them(open_claims, monkeypatch):
    from fastapi.testclient import TestClient

    import remitwatch.api as api
    open_claims.close()
    db = os.path.join(os.path.dirname(open_claims.db_path), "s.sqlite")
    api.ensure_server_schema(db)
    monkeypatch.delenv("REMITWATCH_MODE", raising=False)
    monkeypatch.setattr(api, "DB_PATH", db)
    monkeypatch.setattr(api, "PASSWORD", "")
    c = TestClient(api.app)
    na = c.get("/api/brief").json()["no_answer"]
    # the claim sent again in September is for an August visit: Anthem's 90 days from that date have passed (window closed)
    assert na["chase"]["n"] == 2 and na["rejected"]["n"] == 1 and na["chase"]["first_deadline"] == "2026-12-13"
    kinds = {k["key"]: k for k in c.get("/api/worklist/counts?category=denial,bundling,downcoding,underpayment&status=open,in_progress&deadline=valid&live=1").json()["kinds"]}
    assert kinds["chase"]["n"] == 2
    items = c.get("/api/findings?kind=chase&status=open,in_progress&deadline=valid&live=1&limit=50").json()["items"]
    assert {i["patient_control_number"] for i in items} == {"CB0000501C017", "CB0000601C017"}
    closed = c.get("/api/findings?kind=chase&status=open,in_progress&deadline=expired&live=1&limit=50").json()["items"]
    assert [i["patient_control_number"] for i in closed] == ["CB0000506C024"]
    d = c.get(f"/api/findings/{items[0]['finding_id']}").json()["finding"]
    assert d["evidence"]["sent"] and d["evidence"]["answer_due"] and d["evidence"]["lines"]
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    today = open(os.path.join(root, "remitwatch", "static", "today.js")).read()
    assert "title:'Chase claims the payer never answered'" in today and "go:{kind:'chase', sort:'deadline'}" in today


def test_a_stopped_feed_does_not_make_newer_claims_look_unanswered(tmp_path):
    """Remittances that stop on September 1 cannot say a claim sent in late September went unanswered."""
    from remitwatch.detect import Detector
    s = build_store(tmp_path, clean_visits(1, 25, "99214", "223.65", "117.60"), pay_date="20260901")
    charge(s.conn, "x", "CB0000700C017", "2026-09-20", "2026-09-21")
    charge(s.conn, "y", "CB0000701C017", "2026-07-20", "2026-07-21")
    s.conn.commit()
    Detector(s, run_id="t").run()
    assert [r[0] for r in s.conn.execute("SELECT patient_control_number FROM findings WHERE signal='NO_ANSWER'")] == ["CB0000701C017"]
