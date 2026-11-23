"""
Money that can still be worked and is not being worked: items whose disputed money the biller wrote off in EMA while the
payer can still be asked for it (build 1), and claims EMA shows open that the payer never answered (build 2). Every
patient, claim and amount is made up.
"""
from __future__ import annotations

import json
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
    # once, though two exports carry it, and only the part in dispute: the payer's usual $117.60, not the $223.65 charge
    assert line["written_off_cents"] == 11760 and line["written_off_on"] == "2026-11-12"
    whole = next(v for k, v in f.items() if v["patient_control_number"] == WHOLE)
    assert whole["written_off_cents"] == 15000 and whole["written_off_on"] == "2026-11-13"       # a whole claim: its lines' write-offs
    # a payer's denial moved on to the patient counts; the patient's share and EMA's routine contractual posting do not
    assert written_off_of([{"adjustment_status": "TRANSFER_RESPONSIBILITY", "carc": "CO-50", "adjustments_cents": 100}]) == (100, None)
    assert written_off_of([{"adjustment_status": "WRITE_OFF", "carc": "PR-3", "adjustments_cents": 100}]) == (None, None)
    for routine in ("CO-45", "CO-253", "CO-59", "OA-23"):
        assert written_off_of([{"adjustment_status": "WRITE_OFF", "carc": routine, "adjustments_cents": 13365}]) == (None, None)
    neg = [{"adjustment_status": "write_off", "carc": "CO-16", "adjustments_cents": -250, "transaction_date": "2026-11-02"}]
    assert written_off_of(neg, sign=-1) == (250, "2026-11-02")          # EMA posting its write-offs as negative amounts
    assert written_off_of(neg) == (None, None)                          # where they are positive, that row alone is a reversal
    # however EMA spaces a reason; the patient's share and the routine ones stay out; the finding's own reason never does
    spaced = lambda carc, cents=500: {"adjustment_status": "WRITE_OFF", "carc": carc, "adjustments_cents": cents}
    assert written_off_of([spaced("CO - 45"), spaced("PR - 2"), spaced("co45"), spaced("OA - 23")]) == (None, None)
    assert written_off_of([spaced("CO - 50")]) == (500, None) and written_off_of([spaced("CO-59")], own="59") == (500, None)
    # a reversal nets out; one row read from a layout without its date adds nothing; two codes count apart; the cap holds
    wo = lambda cpt, cents, when: {"adjustment_status": "WRITE_OFF", "carc": "CO-50", "cpt": cpt, "service_date": "2026-10-01",
                                   "adjustments_cents": cents, "transaction_date": when}
    assert written_off_of([wo("11102", 15000, "2026-11-12"), wo("11102", -15000, "2026-11-20")]) == (None, None)
    assert written_off_of([wo("11102", 15000, "2026-11-12"), wo("11102", 15000, None)]) == (15000, "2026-11-12")
    assert written_off_of([wo("11102", 15000, "2026-11-12"), wo("99213", 15000, "2026-11-12")]) == (30000, "2026-11-12")
    assert written_off_of([wo("11102", 15000, "2026-11-12")], cap=4260) == (4260, "2026-11-12")
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
    assert wo["n"] == 2 and wo["cents"] == cents and wo["written_off_cents"] == 11760 + 15000 and wo["since"] == "2026-11-12"
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
    assert d["written_off_cents"] == 11760 and d["written_off_on"] == "2026-11-12"


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
                    VALUES (?,?,'Encounter & Charge Lines.xlsx',0,?,?,?,?,?,?,?,?,?,?,?,'Provider, Test',?,?)""",
                 (fp, family, claim, act, item or fp, cpt, dos, created, cents, paid, payer, payer_id, f"P{claim[4:9]}", status, sent))   # one patient per bill


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
    # sent on time: "send it again by" the first-filing limit (90 days from the date of service, not confirmed for Anthem), and
    # the date shown runs Anthem's two-year appeal window past it (two calendar years), so it never expires on that limit
    assert a["appeal_deadline"] == "2028-12-13" and "send it again by 2026-12-13" in a["deadline_basis"] and "not confirmed" in a["deadline_basis"]
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
    # the claim sent again in September for an August visit went out on time: past Anthem's 90 days from the visit it stays
    # listed (the payer owes a claim it has, and takes proof of timely filing for one it has no record of)
    assert na["chase"]["n"] == 3 and na["rejected"]["n"] == 1 and na["chase"]["first_deadline"] == "2027-09-10"   # Medicare's firm year
    kinds = {k["key"]: k for k in c.get("/api/worklist/counts?category=denial,bundling,downcoding,underpayment&status=open,in_progress&deadline=valid&live=1").json()["kinds"]}
    assert kinds["chase"]["n"] == 3
    items = c.get("/api/findings?kind=chase&status=open,in_progress&deadline=valid&live=1&limit=50").json()["items"]
    assert {i["patient_control_number"] for i in items} == {"CB0000501C017", "CB0000601C017", "CB0000506C024"}
    assert c.get("/api/findings?kind=chase&status=open,in_progress&deadline=expired&live=1&limit=50").json()["items"] == []
    late = next(i for i in items if i["patient_control_number"] == "CB0000506C024")
    assert late["appeal_deadline"] == "2028-11-01" and "proof of timely filing" in late["deadline_basis"]   # Nov 1 limit + Anthem's two years
    d = c.get(f"/api/findings/{items[0]['finding_id']}").json()["finding"]
    assert d["evidence"]["sent"] and d["evidence"]["answer_due"] and d["evidence"]["lines"]
    # never hidden wholesale for a payer: each unanswered claim is let go one at a time, by a person
    xhr = {"X-Requested-With": "remitwatch"}
    for sig in ("NO_ANSWER", "REJECTED_PRE_PAYER"):
        r = c.post("/api/hide", json={"payer_group": "Anthem / BCBS", "signal": sig, "note": "x"}, headers=xhr)
        assert r.status_code == 400 and "one at a time" in r.json()["detail"]
    from remitwatch.actions import _hide_matching
    conn = sqlite3.connect(db)
    assert _hide_matching(conn, "Anthem / BCBS", "NO_ANSWER", "x", "2026-11-21") == 0
    conn.close()
    assert c.get("/api/brief").json()["no_answer"]["chase"]["n"] == 3
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    today = open(os.path.join(root, "remitwatch", "static", "today.js")).read()
    assert "title:'Chase claims the payer never answered'" in today and "go:{kind:'chase', sort:'deadline'}" in today
    desk = open(os.path.join(root, "remitwatch", "static", "desk.js")).read()
    assert "const NEVER_HIDDEN = new Set(['NO_ANSWER', 'REJECTED_PRE_PAYER']);" in desk and desk.count("NEVER_HIDDEN.has(") == 2


def test_a_stopped_feed_does_not_make_newer_claims_look_unanswered(tmp_path):
    """Remittances that stop on September 1 cannot say a claim sent in late September went unanswered."""
    from remitwatch.detect import Detector
    s = build_store(tmp_path, clean_visits(1, 25, "99214", "223.65", "117.60"), pay_date="20260901")
    charge(s.conn, "x", "CB0000700C017", "2026-09-20", "2026-09-21")
    charge(s.conn, "y", "CB0000701C017", "2026-07-20", "2026-07-21")
    s.conn.commit()
    Detector(s, run_id="t").run()
    assert [r[0] for r in s.conn.execute("SELECT patient_control_number FROM findings WHERE signal='NO_ANSWER'")] == ["CB0000701C017"]


# --------------------------------------------------------------------------- what the review of the build found
_N = [0]


def ema(conn, claim, **kw) -> None:
    """One EMA export row as the loaders store it: by default a 99214 charge to Anthem on the charge export."""
    _N[0] += 1
    row = {"row_fingerprint": f"r{_N[0]}", "family": "charge_lines", "source_name": "Encounter & Charge Lines.xlsx", "source_sha256": "charges",
           "row_index": _N[0], "claim_id": claim, "activity_type": "Charge", "bill_item_id": f"i{_N[0]}", "cpt": "99214",
           "service_date": "2026-09-14", "bill_created": "2026-09-15", "charges_cents": 22365, "payer": "Anthem Blue Cross",
           "payer_id": "00660", "patient_id": f"P{claim[4:9]}", "provider": "Provider, Test", "units": 1.0}   # one patient per bill
    if kw.get("family", "charge_lines") != "charge_lines":
        row.update(activity_type=None, bill_item_id=None, charges_cents=None, bill_created=None, source_name="Claim & Claim Status.xlsx",
                   source_sha256="status")
    row.update(kw)
    conn.execute(f"INSERT INTO ema_rows ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))


def chased(s) -> dict:
    from remitwatch.detect import Detector
    s.conn.commit()
    Detector(s, run_id=f"t{_N[0]}").run()
    return {r["patient_control_number"]: dict(r) for r in s.query(
        "SELECT * FROM findings WHERE signal IN ('NO_ANSWER','REJECTED_PRE_PAYER') AND COALESCE(stale,0)=0")}


def test_a_claim_answered_superseded_settled_or_paid_is_not_chased(tmp_path):
    from tests.test_features import claim
    paid = claim(0, svc("99214", "223.65", "117.60", "117.60", "CAS*CO*45*106.05~", dos="20260914"), dos="20260914").replace("CB0000000C017", "CB0000H02C017")
    s = build_store(tmp_path, clean_visits(1, 25, "99214", "223.65", "117.60") + [paid], pay_date="20261110")
    c = s.conn
    ema(c, "CB0000A01C017")                                       # the payer's answer is posted in EMA, though its 835 never came
    ema(c, "CB0000A01C017", family="era_lines", source_name="ERA & Payment Lines.xlsx", carc="PR-1", adjustment_status="TRANSFER_RESPONSIBILITY",
        adjustments_cents=11760, transaction_date="2026-10-02")
    ema(c, "CB0000A02C017")                                       # EMA records the payer's denial
    ema(c, "CB0000A02C017", family="claim_status", claim_status="Denied", claim_submission_date="2026-09-16", denial_date="2026-10-12")
    ema(c, "CB0000A03C017")                                       # sent, then sent again: only the later submission is chased
    ema(c, "CB0000A03C017", family="claim_status", claim_status="Submitted", claim_submission_date="2026-09-01")
    ema(c, "CB0000A03C024")
    ema(c, "CB0000A03C024", family="claim_status", claim_status="Submitted", claim_submission_date="2026-10-05")
    ema(c, "CB0000A04C017")                                       # rejected and already resent (not due yet)
    ema(c, "CB0000A04C017", family="claim_status", claim_status="Rejected", claim_submission_date="2026-11-01")
    ema(c, "CB0000A04C024", service_date="2026-09-14")
    ema(c, "CB0000A04C024", family="claim_status", claim_status="Submitted", claim_submission_date="2026-11-04")
    ema(c, "CB0000A05C017", bill_item_id="v5")                    # rejected, and its charge voided
    ema(c, "CB0000A05C017", bill_item_id="v5", activity_type="Void Charge")
    ema(c, "CB0000A05C017", family="claim_status", claim_status="Rejected", claim_submission_date="2026-09-16")
    ema(c, "CB0000A06C017")                                       # rejected and voided by its status
    ema(c, "CB0000A06C017", family="claim_status", claim_status="Rejected - Voided", claim_submission_date="2026-09-16")
    ema(c, "CB0000A07C017", bill_item_id=None, source_name="Weekly.csv", source_sha256="weekly")   # a payment row naming only the payer
    ema(c, "CB0000A07C017", bill_item_id=None, source_name="Weekly.csv", source_sha256="weekly", activity_type="Payment", charges_cents=0,
        payments_cents=11760, payer="Blue Cross and Blue Shield")
    ema(c, "CB0000H01C017", patient_id="EMA9")                    # the bill was made again (CB0000H02) and that one was paid
    ema(c, "CB0000H02C017", patient_id="EMA9", bill_created="2026-09-20", insurance_payments_cents=11760)
    found = chased(s)
    # a payment row that only names the payer could be the insurer's or a copay: the claim stays listed, and the item says so
    assert set(found) == {"CB0000A03C024", "CB0000A07C017"}
    assert found["CB0000A03C024"]["shortfall_cents"] == 11760           # one visit, counted once
    assert "names Anthem Blue Cross but is not marked insurance or patient" in found["CB0000A07C017"]["hint"]


def test_each_claim_is_chased_once_at_its_own_size_and_closed_ones_show_as_written_off(tmp_path):
    from fastapi.testclient import TestClient

    import remitwatch.api as api
    s = build_store(tmp_path, clean_visits(1, 25, "99214", "223.65", "117.60") + clean_visits(100, 10, "17003", "30.00", "20.00"), pay_date="20261110")
    c = s.conn
    ema(c, "CB0000B01C017")                                       # closed in EMA though no payer answered: given up
    ema(c, "CB0000B01C017", family="claim_status", claim_status="Closed", claim_submission_date="2026-09-16")
    ema(c, "CB0000B02C017", bill_item_id="k1")                    # one charge in two export layouts, with and without its item id
    ema(c, "CB0000B02C017", bill_item_id=None, source_name="Weekly.csv", source_sha256="weekly")
    ema(c, "CB0000B03C017", bill_item_id=None, source_name="Weekly.csv", source_sha256="weekly")   # voided in a layout with no item ids
    ema(c, "CB0000B03C017", bill_item_id=None, source_name="Weekly.csv", source_sha256="weekly", activity_type="Void Charge")
    ema(c, "CB0000B04C017", family="claim_status", claim_status="Submitted", claim_submission_date="2026-09-16")   # known from the status report alone
    ema(c, "CB0000B05C017", cpt="17003", units=13.0, charges_cents=39000)                                      # 13 units at Anthem's $20
    for name, sha, row, status, sent in (("Claim Status Oct.csv", "oct", 40, "Submitted", "2026-09-16"), ("Claim Status Nov.csv", "nov", 2, "Closed", "2026-09-16")):
        ema(c, "CB0000B06C017", family="claim_status", source_name=name, source_sha256=sha, row_index=row, claim_status=status, claim_submission_date=sent)
    ema(c, "CB0000B06C017")
    ema(c, "CB0000B07C017", family="claim_status", source_name="Claim Status Nov.csv", source_sha256="nov", claim_status="Submitted",
        claim_submission_date="2026-10-20")                       # the November export holds a later date than the October one
    ema(c, "CB0000B08C017", payer="Passport Health Plan", payer_id="PASS1")   # a payer that has never sent a remittance
    c.execute("INSERT INTO ema_sources (source_name, sha256, family, created_at, rows, recorded_at) VALUES ('Claim Status Nov.csv','nov','claim_status',NULL,2,'2026-11-01')")
    c.execute("INSERT INTO ema_sources (source_name, sha256, family, created_at, rows, recorded_at) VALUES ('Claim Status Oct.csv','oct','claim_status',NULL,1,'2026-11-02')")
    found = chased(s)
    assert set(found) == {"CB0000B01C017", "CB0000B02C017", "CB0000B04C017", "CB0000B05C017", "CB0000B06C017", "CB0000B08C017"}   # B07 is not due yet
    closed = found["CB0000B01C017"]
    assert "(EMA: Closed)" in closed["title"] and closed["written_off_cents"] == 11760
    assert found["CB0000B02C017"]["charge_cents"] == 22365 and found["CB0000B02C017"]["shortfall_cents"] == 11760
    assert found["CB0000B04C017"]["charge_cents"] is None and found["CB0000B04C017"]["shortfall_cents"] == 11760
    assert found["CB0000B05C017"]["shortfall_cents"] == 26000
    assert "(EMA: Closed)" in found["CB0000B06C017"]["title"]          # the newer export's status, uploaded first or not
    assert "on file at all" in found["CB0000B08C017"]["hint"]
    # on the page: a claim given up in EMA is both chased and in "Work what EMA wrote off"; the claim view and the search list them
    s.close()
    db = os.path.join(os.path.dirname(s.db_path), "s.sqlite")
    api.ensure_server_schema(db)
    api.DB_PATH, api.PASSWORD = db, ""
    cl = TestClient(api.app)
    wo = cl.get("/api/findings?handling=written_off&status=open,in_progress&deadline=valid&live=1&limit=50").json()["items"]
    assert {i["patient_control_number"] for i in wo} == {"CB0000B01C017", "CB0000B06C017"}
    view = cl.get("/api/claims/view", headers={"X-Claim": "CB0000B04C017"}).json()
    assert [f["signal"] for f in view["findings"]] == ["NO_ANSWER"]
    hit = cl.get("/api/claims/search", headers={"X-Search": "CB0000B04"}).json()["items"]
    assert hit and hit[0]["open_findings"] == 1
    brief = cl.get("/api/brief").json()
    assert all(b["claims"] >= 1 for b in brief["batches"] if b["kind"] == "chase")
    assert not any(h["key"] == "ema_handling" for h in brief.get("health", []))     # no remittance, so no ERA row can describe them
    rej = {"signal": "REJECTED_PRE_PAYER", "payer_kind": "medicare", "actionable": "correct_and_resubmit", "deadline_basis": "42 CFR 424.44"}
    from remitwatch.appeals import next_step
    assert next_step(rej) is None and next_step(dict(rej, signal="DENIED_MISSING_INFO")).startswith("Send Medicare a redetermination")


def test_the_work_on_an_unanswered_claim_follows_it_when_the_payer_answers(tmp_path):
    import pathlib

    from remitwatch.actions import apply_status
    from tests.test_features import claim
    from tests.test_remitwatch import remit
    s = build_store(tmp_path, clean_visits(1, 25, "99214", "223.65", "117.60"), pay_date="20261110")
    ema(s.conn, "CB0000L01C017")
    fid = chased(s)["CB0000L01C017"]["finding_id"]
    apply_status(s.conn, fid, "in_progress", "called Anthem 11/12: denied, records requested", actor="biller")
    s.conn.commit()
    den = claim(0, svc("99214", "223.65", "0.00", None, "CAS*CO*50*223.65~", dos="20260914"), dos="20260914").replace("CB0000000C017", "CB0000L01C017")
    p = pathlib.Path(s.db_path).parent / "late.RMT"
    p.write_bytes(remit(den, "0.00", seg_count=99, isa_ctrl="000000077", pay_date="20261115"))
    s.ingest_835_paths([str(p)])
    assert chased(s) == {}
    r = s.query("SELECT signal, status, notes FROM findings WHERE patient_control_number='CB0000L01C017' AND COALESCE(stale,0)=0")
    assert [(x["signal"], x["status"]) for x in r] == [("DENIED_MEDICAL_NECESSITY", "in_progress")] and "records requested" in r[0]["notes"]


def test_only_the_disputed_money_written_off_marks_an_item(tmp_path):
    """EMA's routine CO-45 posting gives nothing up; a code twice on one date cannot say which line EMA's row is."""
    from remitwatch.detect import Detector
    from tests.test_features import claim
    under = claim(300, svc("99214", "223.65", "72.00", "90.00", "CAS*CO*45*133.65~CAS*PR*2*18.00~", dos="20261101"), dos="20261101")
    twice = claim(301, svc("11102", "150.00", "0.00", None, "CAS*CO*50*150.00~", dos="20261101")
                  + svc("11102", "120.00", "0.00", None, "CAS*CO*50*120.00~", dos="20261101", mods="59"), dos="20261101")
    s = build_store(tmp_path, clean_visits(1, 25, "99214", "223.65", "117.60") + clean_visits(50, 10, "11102", "150.00", "110.00") + [under, twice],
                    pay_date="20261110")
    era(s.conn, [("u1", "ERA & Payment Lines.xlsx", "CB0000300C017", "99214", "CO-45", "WRITE_OFF", 13365, "2026-11-12"),
                 ("t1", "ERA & Payment Lines.xlsx", "CB0000301C017", "11102", "CO-50", "WRITE_OFF", 15000, "2026-11-12"),
                 ("t2", "ERA & Payment Lines.xlsx", "CB0000301C017", "11102", "CO-50", "IGNORE", 12000, "2026-11-12")])
    Detector(s, run_id="t").run()
    rows = s.query("SELECT signal, patient_control_number pcn, written_off_cents FROM findings WHERE patient_control_number IN ('CB0000300C017','CB0000301C017')")
    assert rows and all(r["written_off_cents"] is None for r in rows)


# --------------------------------------------------------------------------- what the second review found
def test_a_claim_sent_again_after_a_denial_is_still_chased(tmp_path):
    """A denial of the bill's first submission, or of an earlier bill for the same visit, does not answer the claim sent again."""
    from tests.test_features import claim
    den = claim(0, svc("99214", "223.65", "0.00", None, "CAS*CO*16*223.65~", dos="20260803"), dos="20260803").replace("CB0000000C017", "CB0000A31C017")
    old = claim(0, svc("99214", "223.65", "0.00", None, "CAS*CO*16*223.65~", dos="20260914"), dos="20260914").replace("CB0000000C017", "CB0000H51C017")
    s = build_store(tmp_path, clean_visits(1, 25, "99214", "223.65", "117.60"), pay_date="20261110")
    s.close()
    s = build_store(tmp_path, [den, old], pay_date="20260901")
    c = s.conn
    ema(c, "CB0000A31C017", service_date="2026-08-03")                          # denied on Sep 1 (its row stays in the store)
    ema(c, "CB0000A31C024", service_date="2026-08-03")                          # the same bill sent again Sep 20, never answered
    ema(c, "CB0000A31C024", family="claim_status", service_date="2026-08-03", claim_status="Submitted", claim_submission_date="2026-09-20")
    ema(c, "CB0000H51C017", patient_id="PH51")                                  # denied on Sep 1, then a new bill made for the visit
    ema(c, "CB0000H52C017", patient_id="PH51", bill_created="2026-09-25")
    ema(c, "CB0000H52C017", family="claim_status", patient_id="PH51", claim_status="Submitted", claim_submission_date="2026-09-25")
    assert set(chased(s)) == {"CB0000A31C024", "CB0000H52C017"}


def test_the_latest_submission_is_known_by_its_number_and_a_remade_bill_carries_the_visit(tmp_path):
    s = build_store(tmp_path, clean_visits(1, 25, "99214", "223.65", "117.60"), pay_date="20261110")
    c = s.conn
    ema(c, "CB0000A41C017")                                                     # rejected Nov 1, already sent again as C024
    ema(c, "CB0000A41C017", family="claim_status", claim_status="Rejected", claim_submission_date="2026-11-01")
    ema(c, "CB0000A41C024")                                                     # only in the charge export so far
    ema(c, "CB0000H61C017", patient_id="PH61", bill_created="2026-09-05")      # closed in EMA; the visit was billed again
    ema(c, "CB0000H61C017", family="claim_status", patient_id="PH61", claim_status="Closed", claim_submission_date="2026-09-05")
    ema(c, "CB0000H62C017", patient_id="PH61", bill_created="2026-09-26")
    ema(c, "CB0000H62C017", family="claim_status", patient_id="PH61", claim_status="Submitted", claim_submission_date="2026-09-26")
    ema(c, "CB0000P01C017")                                                     # a $25 copay is not the insurer's payment
    ema(c, "CB0000P01C017", activity_type="Payment", charges_cents=0, payments_cents=2500, insurance_payments_cents=0)
    ema(c, "CB0000P03C017", bill_item_id="p3")                                  # closed: the patient paid it all
    ema(c, "CB0000P03C017", bill_item_id="p3", activity_type="Payment", charges_cents=0, payments_cents=22365, insurance_payments_cents=0)
    ema(c, "CB0000P03C017", family="claim_status", claim_status="Closed", claim_submission_date="2026-09-16")
    found = chased(s)
    assert set(found) == {"CB0000A41C017", "CB0000H62C017", "CB0000P01C017"} - {"CB0000A41C017"}
    assert found["CB0000H62C017"]["written_off_cents"] is None and found["CB0000P01C017"]["shortfall_cents"] == 11760


def test_a_claim_sent_on_time_never_expires_on_its_first_filing_date(tmp_path):
    """Remittances on file run to Nov 10; a visit of Aug 17 sent Aug 19 reaches Anthem's 90 days on Nov 15. Its date runs past
    that, whatever day the page is opened."""
    s = build_store(tmp_path, clean_visits(1, 25, "99214", "223.65", "117.60"), pay_date="20261110")
    ema(s.conn, "CB0000T01C017", service_date="2026-08-17", bill_created="2026-08-19")
    ema(s.conn, "CB0000T01C017", family="claim_status", service_date="2026-08-17", claim_status="Submitted", claim_submission_date="2026-08-19")
    f = chased(s)["CB0000T01C017"]
    assert f["appeal_deadline"] == "2028-11-15" and "send it again by 2026-11-15" in f["deadline_basis"]      # two calendar years


def test_the_payers_answer_starts_to_do_with_the_notes_and_never_inherits_sent_or_paid(tmp_path):
    import pathlib

    from remitwatch.actions import apply_status
    from tests.test_features import claim
    from tests.test_remitwatch import remit
    s = build_store(tmp_path, clean_visits(1, 25, "99214", "223.65", "117.60"), pay_date="20261110")
    ema(s.conn, "CB0000L11C017")
    fid = chased(s)["CB0000L11C017"]["finding_id"]
    apply_status(s.conn, fid, "appealed", "not on file at Anthem: sent again 11/12", actor="biller")
    s.conn.commit()
    den = claim(0, svc("99214", "223.65", "0.00", None, "CAS*CO*50*223.65~", dos="20260914"), dos="20260914").replace("CB0000000C017", "CB0000L11C017")
    p = pathlib.Path(s.db_path).parent / "late.RMT"
    p.write_bytes(remit(den, "0.00", seg_count=99, isa_ctrl="000000078", pay_date="20261125"))
    s.ingest_835_paths([str(p)])
    chased(s)
    r = s.query("SELECT signal, status, notes, appealed_at FROM findings WHERE patient_control_number='CB0000L11C017' AND COALESCE(stale,0)=0")
    assert [(x["signal"], x["status"], x["appealed_at"]) for x in r] == [("DENIED_MEDICAL_NECESSITY", "in_progress", None)]
    assert "sent again" in r[0]["notes"]
    ev = s.query("SELECT note FROM finding_events WHERE kind='signal_changed'")
    assert any("was NO_ANSWER on claim CB0000L11C017 (appealed)" in (e["note"] or "") for e in ev)


def test_the_work_waits_for_the_claim_sent_again_a_run_later(tmp_path):
    """A rejected claim is fixed and sent again as C024; C024 is due only weeks later. Its notes are there when it comes due."""
    from remitwatch.actions import apply_status
    s = build_store(tmp_path, clean_visits(1, 25, "99214", "223.65", "117.60"), pay_date="20261110")
    c = s.conn
    ema(c, "CB0000R01C017")
    ema(c, "CB0000R01C017", family="claim_status", claim_status="Rejected", claim_submission_date="2026-10-20")
    fid = chased(s)["CB0000R01C017"]["finding_id"]
    apply_status(c, fid, "open", "member id corrected, resending", actor="biller")      # a note on an item still To do
    c.commit()
    ema(c, "CB0000R01C024")
    ema(c, "CB0000R01C024", family="claim_status", claim_status="Submitted", claim_submission_date="2026-10-21")
    assert chased(s) == {}                                                       # C024 is not due yet: nothing to chase
    kept = s.query("SELECT status, stale FROM findings WHERE finding_id=?", (fid,))
    assert [(x["status"], x["stale"]) for x in kept] == [("open", 1)]           # kept, not deleted, waiting for its bill
    s.conn.execute("UPDATE ema_rows SET claim_submission_date='2026-10-01' WHERE claim_id='CB0000R01C024'")   # now it is due
    found = chased(s)
    assert found["CB0000R01C024"]["status"] == "open" and "member id corrected" in found["CB0000R01C024"]["notes"]
    # the earlier item stays (stale, out of every list) with its own notes, should its claim id be listed again
    assert [(x["stale"], x["notes"]) for x in s.query("SELECT stale, notes FROM findings WHERE finding_id=?", (fid,))] == [(1, "member id corrected, resending")]


# --------------------------------------------------------------------------- what the third review found
def test_only_a_payment_still_standing_on_that_visit_line_takes_it_off_the_list(tmp_path):
    """The old bill's answer paid a different line, EMA showed a payment on a different line, or the payment was taken back:
    the visit billed again is still chased. A later submission on hold does not hide the one that went out."""
    s = build_store(tmp_path, clean_visits(1, 25, "99214", "223.65", "117.60") + clean_visits(50, 10, "11102", "150.00", "110.00"), pay_date="20261110")
    c = s.conn
    ema(c, "CB0000H71C017", patient_id="P71")                                    # its answer posted in EMA: 99214 paid, 11102 denied
    ema(c, "CB0000H71C017", patient_id="P71", cpt="11102", charges_cents=15000)
    ema(c, "CB0000H71C017", family="era_lines", source_name="ERA.xlsx", cpt="99214", carc="CO-45", adjustment_status="IGNORE",
        adjustments_cents=10605, payments_cents=11760, transaction_date="2026-09-25")
    ema(c, "CB0000H71C017", family="era_lines", source_name="ERA.xlsx", cpt="11102", carc="CO-50", adjustment_status="IGNORE",
        adjustments_cents=15000, payments_cents=0, transaction_date="2026-09-25")
    ema(c, "CB0000H72C017", patient_id="P71", cpt="11102", charges_cents=15000, bill_created="2026-10-01")
    ema(c, "CB0000H72C017", family="claim_status", patient_id="P71", cpt="11102", claim_status="Submitted", claim_submission_date="2026-10-01")
    ema(c, "CB0000H73C017", patient_id="P73", insurance_payments_cents=11760)   # EMA shows its 99214 paid, not its 11102
    ema(c, "CB0000H73C017", patient_id="P73", cpt="11102", charges_cents=15000)
    ema(c, "CB0000H74C017", patient_id="P73", cpt="11102", charges_cents=15000, bill_created="2026-10-01")
    ema(c, "CB0000H74C017", family="claim_status", patient_id="P73", cpt="11102", claim_status="Submitted", claim_submission_date="2026-10-01")
    ema(c, "CB0000N01C017")                                                      # sent, unanswered; its corrected claim is on hold
    ema(c, "CB0000N01C017", family="claim_status", claim_status="Submitted", claim_submission_date="2026-09-01")
    ema(c, "CB0000N01C024")
    ema(c, "CB0000N01C024", family="claim_status", claim_status="On Hold")
    ema(c, "CB0000S21C017")                                                      # the payer's denial recorded in EMA on Oct 12
    ema(c, "CB0000S21C017", family="claim_status", claim_status="Denied", claim_submission_date="2026-09-16", denial_date="2026-10-12")
    ema(c, "CB0000S21C024")                                                      # sent again after it: not due before Nov 11
    found = chased(s)
    assert set(found) == {"CB0000H72C017", "CB0000H74C017", "CB0000N01C017"}
    assert found["CB0000H72C017"]["shortfall_cents"] == 11000 and found["CB0000H74C017"]["shortfall_cents"] == 11000


def test_a_whole_claim_denial_with_an_undated_line_does_not_stop_the_analysis(tmp_path):
    from remitwatch.detect import Detector
    from tests.test_features import claim
    lines = (svc("96910", "150.00", "0.00", None, "CAS*CO*16*150.00~", dos="20261101")
             + "SVC*HC:96910*150.00*0.00**1~CAS*CO*16*150.00~")                    # a second line with no date of its own
    whole = claim(800, lines, status="4", dos="20261101").replace("DTM*232*20261101~", "DTM*232*20261101~DTM*233*20261105~")
    s = build_store(tmp_path, clean_visits(1, 25, "99214", "223.65", "117.60") + [whole], pay_date="20261110")
    Detector(s, run_id="t").run()
    assert s.query("SELECT signal FROM findings WHERE patient_control_number='CB0000800C017'")


def test_the_work_goes_only_to_the_claims_own_answer_or_its_resend_to_the_same_payer(tmp_path, monkeypatch):
    """A claim sent again under its own id gets its decision back; another payer's claim on the bill, or a reprocessing
    months later, starts with nothing."""
    import pathlib

    from remitwatch.actions import apply_status
    from tests.test_features import claim
    from tests.test_remitwatch import remit
    s = build_store(tmp_path, clean_visits(1, 25, "99214", "223.65", "117.60"), pay_date="20261110")
    c = s.conn
    ema(c, "CB0000R31C017")                                                      # rejected; fixed and marked Sent
    ema(c, "CB0000R31C017", family="claim_status", claim_status="Rejected", claim_submission_date="2026-10-20")
    ema(c, "CB0000S31C017")                                                      # no answer; being chased
    fid_r, fid_s = (chased(s)[x]["finding_id"] for x in ("CB0000R31C017", "CB0000S31C017"))
    apply_status(c, fid_r, "appealed", "subscriber id fixed; resent 10/22", actor="biller")
    apply_status(c, fid_s, "in_progress", "Anthem: no record; resent with proof of timely filing", actor="biller")
    c.commit()
    # the same claim id goes out again (not due yet); Anthem pays S31 in full, and its coinsurance goes to Medicaid as C024
    c.execute("UPDATE ema_rows SET claim_status='Submitted', claim_submission_date='2026-10-22' WHERE claim_id='CB0000R31C017' AND family='claim_status'")
    paid = claim(0, svc("99214", "223.65", "117.60", "117.60", "CAS*CO*45*82.13~CAS*PR*2*23.92~", dos="20260914"), dos="20260914").replace("CB0000000C017", "CB0000S31C017")
    p = pathlib.Path(s.db_path).parent / "paid.RMT"
    p.write_bytes(remit(paid, "117.60", seg_count=99, isa_ctrl="000000079", pay_date="20261120"))
    s.ingest_835_paths([str(p)])
    ema(c, "CB0000S31C024", payer="Medicaid of Kentucky", payer_id="KYMCD", bill_created="2026-11-21")
    ema(c, "CB0000S31C024", family="claim_status", payer="Medicaid of Kentucky", payer_id="KYMCD", claim_status="Submitted", claim_submission_date="2026-11-21")
    later = pathlib.Path(s.db_path).parent / "later.RMT"                       # the remittances on file reach January
    later.write_bytes(remit(clean_visits(90, 1, "99214", "223.65", "117.60")[0], "117.60", seg_count=99, isa_ctrl="000000080", pay_date="20270105"))
    s.ingest_835_paths([str(later)])
    # and the calendar is in January too: a payment dated after today counts as today (hotfix 2, H8)
    import remitwatch.detect as detect_mod
    from datetime import datetime as _real, timezone as _utc

    class _January(_real):
        @classmethod
        def now(cls, tz=None):
            return _real(2027, 1, 6, 12, 0, tzinfo=tz or _utc)
    monkeypatch.setattr(detect_mod, "datetime", _January)
    found = chased(s)
    assert "CB0000S31C024" in found and found["CB0000S31C024"]["notes"] is None and found["CB0000S31C024"]["status"] == "open"
    c.execute("UPDATE ema_rows SET claim_submission_date='2026-10-01' WHERE claim_id='CB0000R31C017' AND family='claim_status'")
    back = chased(s)["CB0000R31C017"]                                            # due now: the same item, with its decision
    assert back["finding_id"] == fid_r and back["status"] == "appealed" and "resent 10/22" in back["notes"]


# --------------------------------------------------------------------------- what the fourth review found
def test_another_submissions_answer_or_a_voided_payment_never_drops_a_claim(tmp_path):
    """The primary's remittance dated the day the secondary went out; a copay voided in EMA; an insurer's payment posted and
    taken back: each claim is still chased. A date EMA wrote that is not a real day stops nothing."""
    import pathlib

    from tests.test_features import claim
    from tests.test_remitwatch import remit
    s = build_store(tmp_path, clean_visits(1, 25, "99214", "223.65", "117.60"), pay_date="20261110")
    c = s.conn
    paid = claim(0, svc("99214", "223.65", "117.60", "117.60", "CAS*CO*45*82.13~CAS*PR*2*23.92~", dos="20260914"), dos="20260914").replace("CB0000000C017", "CB0000S41C017")
    p = pathlib.Path(s.db_path).parent / "primary.RMT"
    p.write_bytes(remit(paid, "117.60", seg_count=99, isa_ctrl="000000081", pay_date="20261002"))
    s.ingest_835_paths([str(p)])
    ema(c, "CB0000S41C024", payer="Medicaid of Kentucky", payer_id="KYMCD")     # the secondary, sent the day the 835 is dated
    ema(c, "CB0000S41C024", family="claim_status", payer="Medicaid of Kentucky", payer_id="KYMCD", claim_status="Submitted",
        claim_submission_date="2026-10-02")
    ema(c, "CB0000V01C017", bill_item_id="v1")                                   # a copay, then the copay voided
    ema(c, "CB0000V01C017", bill_item_id="v1", activity_type="Payment", charges_cents=0, payments_cents=2500, insurance_payments_cents=0)
    ema(c, "CB0000V01C017", bill_item_id="v1", activity_type="Void Payment", charges_cents=0, payments_cents=-2500, insurance_payments_cents=0)
    ema(c, "CB0000M71C017", bill_item_id="m1")                                   # an insurer's payment posted, then reversed
    ema(c, "CB0000M71C017", bill_item_id="m1", activity_type="Payment", charges_cents=0, payments_cents=11760, insurance_payments_cents=11760)
    ema(c, "CB0000M71C017", bill_item_id="m1", activity_type="Payment", charges_cents=0, payments_cents=-11760, insurance_payments_cents=-11760)
    ema(c, "CB0000Q01C017")                                                      # an impossible date, and a later submission with none
    ema(c, "CB0000Q01C017", family="claim_status", claim_status="Submitted", claim_submission_date="0000-00-00")
    ema(c, "CB0000Q01C024")
    found = chased(s)
    assert {"CB0000S41C024", "CB0000V01C017", "CB0000M71C017"} <= set(found)
    assert "Another submission of this bill was answered on 2026-10-02" in found["CB0000S41C024"]["hint"]


def test_a_claim_sent_on_time_stays_listed_until_its_own_date(tmp_path):
    """An Anthem claim sent on time 14 months ago is still inside its date (the first-filing limit plus the appeal window);
    the remittances on file reach back that far, so it is judged and listed."""
    from tests.test_features import claim
    s = build_store(tmp_path, [claim(900, svc("99213", "150.00", "90.00", "90.00", "CAS*CO*45*60.00~", dos="20250901"), dos="20250901")],
                    pay_date="20250915")
    s.close()
    s = build_store(tmp_path, clean_visits(1, 25, "99214", "223.65", "117.60"), pay_date="20261110")
    ema(s.conn, "CB0000T41C017", service_date="2025-09-29", bill_created="2025-09-30")
    ema(s.conn, "CB0000T41C017", family="claim_status", service_date="2025-09-29", claim_status="Submitted", claim_submission_date="2025-09-30")
    f = chased(s)["CB0000T41C017"]
    assert f["appeal_deadline"] == "2027-12-28"


def test_a_marked_recovery_stays_counted_and_a_passing_test_clears_only_older_holds(tmp_path):
    import json
    import pathlib
    import sqlite3

    from remitwatch import actions, trizetto
    from remitwatch.actions import apply_status
    from tests.test_features import claim
    from tests.test_remitwatch import remit
    s = build_store(tmp_path, clean_visits(1, 25, "99214", "223.65", "117.60"), pay_date="20261110")
    ema(s.conn, "CB0000W01C017")
    fid = chased(s)["CB0000W01C017"]["finding_id"]
    apply_status(s.conn, fid, "resolved", "Anthem is paying it", recovered_cents=11760, actor="biller")
    s.conn.commit()
    paid = claim(0, svc("99214", "223.65", "117.60", "117.60", "CAS*CO*45*106.05~", dos="20260914"), dos="20260914").replace("CB0000000C017", "CB0000W01C017")
    p = pathlib.Path(s.db_path).parent / "w.RMT"
    p.write_bytes(remit(paid, "117.60", seg_count=99, isa_ctrl="000000082", pay_date="20261120"))
    s.ingest_835_paths([str(p)])
    chased(s)
    r = s.query("SELECT status, stale, recovered_cents FROM findings WHERE finding_id=?", (fid,))
    assert [(x["status"], x["stale"], x["recovered_cents"]) for x in r] == [("resolved", 0, 11760)]
    # Vercel's journal: a passing test with no hold is done at once; it never clears a hold recorded after it
    conn = sqlite3.connect(str(tmp_path / "m.sqlite"))
    assert actions.apply(conn, {"kind": "trizetto_hold", "op": "clear", "at": "2026-11-21T10:00:00+00:00"}) is True
    actions.apply(conn, {"kind": "trizetto_hold", "op": "set", "who": "w", "detail": "refused", "at": "2026-11-22T10:00:00+00:00"})
    assert actions.apply(conn, {"kind": "trizetto_hold", "op": "clear", "at": "2026-11-21T10:00:00+00:00"}) is True
    assert json.loads(conn.execute("SELECT value FROM meta WHERE key=?", (trizetto.REFUSED_META,)).fetchone()[0])["who"] == "w"


def test_a_claim_sent_again_and_answered_takes_the_notes_and_the_earlier_one_stays_superseded(tmp_path):
    import pathlib

    from remitwatch.actions import apply_status
    from tests.test_features import claim
    from tests.test_remitwatch import remit
    s = build_store(tmp_path, clean_visits(1, 25, "99214", "223.65", "117.60"), pay_date="20261110")
    c = s.conn
    ema(c, "CB0000R51C017")
    fid = chased(s)["CB0000R51C017"]["finding_id"]
    apply_status(c, fid, "appealed", "Anthem has no record (ref 4471); resent as C024 10/21 with the acceptance report", actor="biller")
    c.commit()
    ema(c, "CB0000R51C024")
    ema(c, "CB0000R51C024", family="claim_status", claim_status="Submitted", claim_submission_date="2026-10-21")
    assert chased(s) == {}                                                       # C024 not due yet
    den = claim(0, svc("99214", "223.65", "0.00", None, "CAS*CO*29*223.65~", dos="20260914"), dos="20260914").replace("CB0000000C017", "CB0000R51C024")
    p = pathlib.Path(s.db_path).parent / "c024.RMT"
    p.write_bytes(remit(den, "0.00", seg_count=99, isa_ctrl="000000083", pay_date="20261118"))
    s.ingest_835_paths([str(p)])
    assert chased(s) == {}                                                       # C017 stays superseded by the answered C024
    r = s.query("SELECT signal, status, notes FROM findings WHERE patient_control_number='CB0000R51C024' AND COALESCE(stale,0)=0")
    assert [(x["signal"], x["status"]) for x in r] == [("DENIED_TIMELY_FILING", "in_progress")] and "ref 4471" in r[0]["notes"]


# --------------------------------------------------------------------------- the review of round 7
def test_the_claim_status_words_emas_exports_use(tmp_path):
    """The words the practice's EMA writes: Payment Pending, Resolved/Paid, Resolved/Other, Denied, Batched for Printing,
    Rejected, Ready for Posting, Invalid and Inactive/Voided, and a Claims State column that holds a US state."""
    from remitwatch.detect import Detector
    s = build_store(tmp_path, clean_visits(1, 25, "99214", "223.65", "117.60"), pay_date="20261110")
    c = s.conn
    status = lambda claim, word, **kw: ema(c, claim, family="claim_status", claim_status=word, **kw)      # noqa: E731
    ema(c, "CB0000E01C017")                                         # sent and never answered: chased
    status("CB0000E01C017", "Payment Pending", claim_submission_date="2026-09-16", claims_state="TN")
    ema(c, "CB0000E02C017", service_date="2026-08-03", bill_created="2026-08-04")      # in the print batch since August
    status("CB0000E02C017", "Batched for Printing", service_date="2026-08-03")
    ema(c, "CB0000E03C017", bill_created="2026-10-20")              # batched three weeks ago: not listed yet
    status("CB0000E03C017", "Batched for Printing")
    for claim, word in (("CB0000E04C017", "Resolved/Paid"), ("CB0000E05C017", "Resolved/Other"), ("CB0000E06C017", "Inactive/Voided"),
                        ("CB0000E07C017", "Ready for Posting")):
        ema(c, claim)
        status(claim, word, claim_submission_date="2026-09-16")
    ema(c, "CB0000E08C017")                                         # Invalid with a submission date: still chased
    status("CB0000E08C017", "Invalid", claim_submission_date="2026-09-16")
    # October's status report says Denied; November's charge export lists the claim with only a Claims State
    status("CB0000E09C017", "Denied", claim_submission_date="2026-09-16", denial_date="2026-10-02", source_sha256="oct")
    ema(c, "CB0000E09C017", claims_state="KY", source_sha256="nov")
    ema(c, "CB0000E10C017", bill_created=None, claim_status="Payment Pending")          # no date EMA sent it, nor made the bill
    c.execute("INSERT INTO ema_sources (source_name, sha256, family, created_at, rows, recorded_at) VALUES ('Status Oct.csv','oct','claim_status','2026-10-15T08:00:00',1,'2026-10-15')")
    c.execute("INSERT INTO ema_sources (source_name, sha256, family, created_at, rows, recorded_at) VALUES ('Charges Nov.xlsx','nov','charge_lines','2026-11-15T08:00:00',1,'2026-11-15')")
    c.commit()
    det = Detector(s, run_id="words")
    det.run()
    assert not hasattr(det, "ema_answered") and not hasattr(det, "ema_latest")     # nothing reads them: not kept for the run
    found = {r["patient_control_number"]: dict(r) for r in s.query("SELECT * FROM findings WHERE signal IN ('NO_ANSWER','REJECTED_PRE_PAYER')")}
    assert set(found) == {"CB0000E01C017", "CB0000E02C017", "CB0000E08C017", "CB0000E10C017"}
    sent = found["CB0000E01C017"]
    assert sent["signal"] == "NO_ANSWER" and sent["title"] == "Sent 2026-09-16, no answer from Anthem / BCBS in 55 days: 99214"
    assert json.loads(sent["evidence"])["ema_status"] == "Payment Pending" and sent["appeal_deadline"] == "2028-12-13"   # two calendar years
    # never sent: listed to be sent (Fix and resubmit), by the first-filing limit (Anthem's 90 days from the visit)
    batch = found["CB0000E02C017"]
    assert batch["signal"] == "REJECTED_PRE_PAYER" and batch["actionable"] == "correct_and_resubmit"
    assert batch["title"] == "In EMA's print batch, never sent to Anthem / BCBS (bill made 2026-08-04): 99214"
    assert batch["appeal_deadline"] == "2026-11-01" and "Print and mail it" in batch["deadline_basis"] and "Sent on time" not in batch["deadline_basis"]
    assert batch["hint"].startswith("EMA shows it waiting in its print batch") and json.loads(batch["evidence"])["not_sent"] is True
    assert batch["severity"] == "high"                              # its limit was reached before the newest remittance
    assert found["CB0000E08C017"]["signal"] == "NO_ANSWER"
    # no date EMA gives for it: the date of service proves nothing about when it went out, so it is dated by the limit
    dos = found["CB0000E10C017"]
    assert dos["title"] == "Visit of 2026-09-14, no answer from Anthem / BCBS in 57 days, and EMA gives no date it was sent: 99214"
    assert dos["appeal_deadline"] == "2026-12-13" and "proof of timely filing" not in dos["deadline_basis"]


def test_the_work_never_goes_to_another_payer_of_a_group_of_many(tmp_path):
    """Medicare and a Medigap carrier, two administrators of self-funded plans, or two payers the rules do not know that
    share a first word, are other payers: the bill's later claim to them starts with nothing. The same payer under another
    name (Anthem, Medicaid as EMA and the remittance spell it) still takes the work."""
    import pathlib

    from remitwatch.actions import apply_status
    from tests.test_features import claim
    from tests.test_remitwatch import remit
    s = build_store(tmp_path, clean_visits(1, 25, "99214", "223.65", "117.60"), pay_date="20261110")
    c = s.conn
    pairs = {"G1": ("Medicare of Kentucky - J15", "15102", "Mutual of Omaha Insurance Company", "MOO01"),
             "G2": ("Auxiant", "AUX01", "Meritain Health", "MER01"),
             "G3": ("Health Alpha Plan", "HAP01", "Health Beta Cooperative", "HBC01"),
             "G4": ("Anthem Blue Cross", "00660", "Blue Cross and Blue Shield of Kentucky", "00660"),
             "G5": ("Medicaid of Kentucky", "KYMCD", "Medicaid of Kentucky", "KYMCD"),
             "G6": ("Optum Unitedhealth Care", "VACCN", "Optum Unitedhealth Care", "VACCN")}
    for g, (first, first_id, _second, _sid) in pairs.items():
        ema(c, f"CB0000{g}1C017", payer=first, payer_id=first_id)
        ema(c, f"CB0000{g}1C017", family="claim_status", payer=first, claim_status="Payment Pending", claim_submission_date="2026-09-15")
    first = chased(s)
    for g in pairs:
        apply_status(c, first[f"CB0000{g}1C017"]["finding_id"], "in_progress", f"{g}: no record of the claim; resent", actor="biller")
    c.commit()
    for g, (_first, _fid, second, second_id) in pairs.items():
        if g in ("G5", "G6"):
            continue                                                # these answer C024 by a remittance below
        ema(c, f"CB0000{g}1C024", payer=second, payer_id=second_id)
        ema(c, f"CB0000{g}1C024", family="claim_status", payer=second, claim_status="Payment Pending", claim_submission_date="2026-10-01")
    # Medicaid as the remittance spells it; the VA's network as its own remittance names it (EMA names its administrator)
    for g, payer, ctrl in (("G5", "KENTUCKY MEDICAID", "000000093"), ("G6", "VA CCN OPTUM", "000000094")):
        den = claim(0, svc("99214", "223.65", "0.00", None, "CAS*CO*50*223.65~", dos="20260914"), dos="20260914").replace("CB0000000C017", f"CB0000{g}1C024")
        p = pathlib.Path(s.db_path).parent / f"{g}.RMT"
        p.write_bytes(remit(den, "0.00", payer=payer, seg_count=99, isa_ctrl=ctrl, pay_date="20261118"))
        s.ingest_835_paths([str(p)])
    found = chased(s)
    for g in ("G1", "G2", "G3"):                                     # another payer: nothing handed over
        assert found[f"CB0000{g}1C024"]["status"] == "open" and found[f"CB0000{g}1C024"]["notes"] is None, g
    assert found["CB0000G41C024"]["status"] == "in_progress" and "G4: no record" in found["CB0000G41C024"]["notes"]
    for g in ("G5", "G6"):
        r = s.query(f"SELECT signal, status, notes FROM findings WHERE patient_control_number='CB0000{g}1C024' AND COALESCE(stale,0)=0")
        assert [(x["signal"], x["status"]) for x in r] == [("DENIED_MEDICAL_NECESSITY", "in_progress")] and f"{g}: no record" in r[0]["notes"], g


def test_a_va_claim_named_after_its_administrator_runs_on_the_vas_limits(tmp_path):
    """EMA names the VA's network after its administrator ('Optum Unitedhealth Care') with payer id VACCN: the claim is the
    VA's, with its 180 days to file, not a commercial plan's 90. A claim of that name with no id of its own is the VA's
    too when the name's rows carry the VA's id; the same name with another payer's id is not."""
    s = build_store(tmp_path, clean_visits(1, 25, "99214", "223.65", "117.60"), pay_date="20261110")
    c = s.conn
    optum = "Optum Unitedhealth Care"
    # as in EMA's exports, the status report carries no payer id; one charge layout does, the other does not
    ema(c, "CB0000V11C017", payer=optum, payer_id="VACCN", service_date="2026-07-01", bill_created="2026-07-02")
    ema(c, "CB0000V11C017", family="claim_status", payer=optum, payer_id=None, service_date="2026-07-01", claim_status="Rejected",
        claim_submission_date="2026-07-02")
    ema(c, "CB0000V12C017", payer=optum, payer_id=None, service_date="2026-07-01", bill_created="2026-07-02")
    ema(c, "CB0000V12C017", family="claim_status", payer=optum, payer_id=None, service_date="2026-07-01", claim_status="Rejected",
        claim_submission_date="2026-07-02")
    for i in range(20):                                              # the name's other rows carry the VA's id
        ema(c, f"CB0000V{20 + i:02d}C017", payer=optum, payer_id="VACCN", claim_status="Resolved/Paid", insurance_payments_cents=11760)
    ema(c, "CB0000U11C017", payer="Optum Health Plan", payer_id="87726", service_date="2026-07-01", bill_created="2026-07-02")
    ema(c, "CB0000U11C017", family="claim_status", payer="Optum Health Plan", payer_id=None, service_date="2026-07-01",
        claim_status="Rejected", claim_submission_date="2026-07-02")
    found = chased(s)
    for claim_id in ("CB0000V11C017", "CB0000V12C017"):
        va = found[claim_id]
        assert (va["payer_group"], va["payer_kind"]) == ("VA Community Care", "va"), claim_id
        assert va["appeal_deadline"] == "2026-12-28" and "VA Community Care Network: 180 days" in va["deadline_basis"]
        assert "Rejected before reaching VA Community Care" in va["title"]
    commercial = found["CB0000U11C017"]
    assert commercial["payer_group"] == "Optum (UHC)" and commercial["appeal_deadline"] == "2026-09-29"
    from remitwatch.codes import normalize_payer
    assert normalize_payer("OPTUM VA CCN") == ("VA Community Care", "va") and normalize_payer(optum) == ("Optum (UHC)", "commercial")


def test_a_payer_the_rules_do_not_know_is_dated_by_the_longest_limit(tmp_path):
    """A payer the rules do not know has a first-filing limit nobody here knows: its item is dated by the longest common
    limit (a year), so a guess never makes it expire, and it names the shortest (90 days) as the date to send it by where
    possible. CHAMPVA's own limit is a year from the date of service (38 CFR 17.276)."""
    from remitwatch.codes import filing_rule
    s = build_store(tmp_path, clean_visits(1, 25, "99214", "223.65", "117.60"), pay_date="20261110")
    c = s.conn
    for claim_id, payer in (("CB0000K11C017", "Imagined Health Cooperative"), ("CB0000K12C017", "Champva - HAC")):
        ema(c, claim_id, payer=payer, payer_id="X" + claim_id[6:9], service_date="2026-07-01", bill_created="2026-07-02")
        ema(c, claim_id, family="claim_status", payer=payer, service_date="2026-07-01", claim_status="Rejected", claim_submission_date="2026-07-02")
    ema(c, "CB0000K13C017", payer="Imagined Health Cooperative", payer_id="XK13", service_date="2026-07-01")   # sent within 90 days
    ema(c, "CB0000K13C017", family="claim_status", payer="Imagined Health Cooperative", service_date="2026-07-01",
        claim_status="Payment Pending", claim_submission_date="2026-07-05")
    ema(c, "CB0000K14C017", payer="Imagined Health Cooperative", payer_id="XK14", service_date="2026-05-01")   # sent after 90 days
    ema(c, "CB0000K14C017", family="claim_status", payer="Imagined Health Cooperative", service_date="2026-05-01",
        claim_status="Payment Pending", claim_submission_date="2026-08-15")
    found = chased(s)
    unknown = found["CB0000K11C017"]
    assert unknown["payer_kind"] == "other" and unknown["appeal_deadline"] == "2027-07-01"       # not 2026-09-29, already past
    assert "send it again by 2026-09-29 where you can; it must reach the payer by this date at the latest" in unknown["deadline_basis"]
    assert "it can be 90 days" in unknown["deadline_basis"]
    champva = found["CB0000K12C017"]
    assert champva["appeal_deadline"] == "2027-07-01" and "38 CFR 17.276" in champva["deadline_basis"]
    assert "where you can" not in champva["deadline_basis"] and "not confirmed" not in champva["deadline_basis"]
    on_time = found["CB0000K13C017"]                                # within the shortest limit: on time whatever the limit is
    assert on_time["appeal_deadline"] == "2027-12-28" and "by 2026-09-29 where you can, and by 2027-07-01 at the latest" in on_time["deadline_basis"]
    late = found["CB0000K14C017"]                                   # past 90 days: not shown as on time, dated by the year
    assert late["appeal_deadline"] == "2027-05-01" and "Sent on time" not in late["deadline_basis"]
    assert filing_rule("Champva-Hac Medicare Crossover", "other")["days"] == 365 and filing_rule("Champva", "other")["verified"]
    assert (filing_rule("Some Small Plan", "other")["days"], filing_rule("Some Small Plan", "other")["earliest"]) == (365, 90)
    assert filing_rule("Anthem / BCBS", "commercial")["days"] == 90 and filing_rule("Anthem / BCBS", "commercial")["earliest"] is None


def test_the_progress_snapshot_leaves_out_what_is_stale(tmp_path):
    """An unanswered claim's item whose notes moved on is kept, stale, so the notes are not lost: the Progress tab's
    snapshot of the run does not count it, as no other total does."""
    from remitwatch.actions import apply_status
    from remitwatch.detect import Detector
    s = build_store(tmp_path, clean_visits(1, 25, "99214", "223.65", "117.60"), pay_date="20261110")
    c = s.conn
    ema(c, "CB0000Z01C017")
    ema(c, "CB0000Z01C017", family="claim_status", claim_status="Rejected", claim_submission_date="2026-10-20")
    fid = chased(s)["CB0000Z01C017"]["finding_id"]
    apply_status(c, fid, "open", "member id corrected, resending", actor="biller")
    c.commit()
    ema(c, "CB0000Z01C024")
    ema(c, "CB0000Z01C024", family="claim_status", claim_status="Payment Pending", claim_submission_date="2026-10-25")
    assert chased(s) == {}                                          # C024 is not due yet; C017's item waits, stale
    Detector(s, run_id="snap").run()
    assert [(x["status"], x["stale"]) for x in s.query("SELECT status, stale FROM findings WHERE finding_id=?", (fid,))] == [("open", 1)]
    snap = {(r["category"], r["status"]): r["n"] for r in s.query("SELECT category, status, n FROM progress_snapshots WHERE run_id='snap'")}
    live = {(r["category"], r["status"]): r["n"] for r in s.query(
        "SELECT category, status, COUNT(*) n FROM findings WHERE COALESCE(stale,0)=0 GROUP BY 1, 2")}
    assert snap == live and snap.get(("denial", "open"), 0) == live.get(("denial", "open"), 0)
