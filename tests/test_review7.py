"""Fixes from the seventh review (a first-time walkthrough, a code review and a recount of the daily screens on the
practice's archive). Everything here is invented."""
from __future__ import annotations

import csv
import io
import json
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch import insights  # noqa: E402
from remitwatch.actions import apply_status  # noqa: E402
from remitwatch.appeals import build_packet, next_step  # noqa: E402
from remitwatch.detect import Detector  # noqa: E402
from remitwatch.store import Store  # noqa: E402
from tests.review6.r6cov_h import add_file, clean_visits, clm, pcn, sv, utc_today, ymd  # noqa: E402
from tests.review6.r6helpers import build_remits, findings  # noqa: E402
from tests.review6.r6helpers import clm as clm6  # noqa: E402
from tests.review6.r6helpers import sv as sv6  # noqa: E402
from tests.test_activity import PW  # noqa: E402

MONEY = "denial,bundling,downcoding,underpayment"


def client(monkeypatch, db):
    from fastapi.testclient import TestClient

    from remitwatch import api
    monkeypatch.setattr(api, "DB_PATH", db)
    monkeypatch.setattr(api, "PASSWORD", PW)
    monkeypatch.setattr(api, "USERNAME", "oda")
    monkeypatch.delenv("REMITWATCH_MODE", raising=False)
    return TestClient(api.app), ("oda", PW)


def test_a_whole_claim_of_a_post_op_visit_or_a_token_charge_is_not_an_appeal(tmp_path):
    """A whole claim holding only 99024 (charged a cent, part of the surgery's global fee), G2211 or a charge of $1 or
    less is no more a finding than the same line inside a paid claim."""
    db = str(tmp_path / "s.sqlite")
    today = utc_today()
    dos, paid = ymd(today - timedelta(days=40)), ymd(today - timedelta(days=30))
    add_file(tmp_path, db, clean_visits(100, 10, "99213", "150.00", "90.00", dos), paid)
    add_file(tmp_path, db, [
        clm(pcn(1), "4", sv("99024", "0.01", "0.00", cas="CAS*CO*97*0.01~", dos=dos), dos=dos, charge="0.01"),
        clm(pcn(2), "4", sv("G2211", "16.00", "0.00", cas="CAS*CO*97*16.00~", dos=dos), dos=dos, charge="16.00"),
        clm(pcn(3), "4", sv("17000", "0.50", "0.00", cas="CAS*CO*97*0.50~", dos=dos), dos=dos, charge="0.50"),
        clm(pcn(4), "4", sv("99213", "150.00", "0.00", cas="CAS*CO*197*150.00~", dos=dos), dos=dos, charge="150.00"),
    ], paid)
    st = Store(db)
    Detector(st, run_id="r7").run()
    got = {r[0] for r in st.conn.execute("SELECT patient_control_number FROM findings WHERE signal='CLAIM_DENIED_UNPAID'")}
    st.close()
    assert got == {pcn(4)}, got


def test_a_payers_rate_that_rose_during_the_year_is_not_a_fee_to_raise(tmp_path):
    """Medicare raised its rates on March 9, 2024. A practice that bills Medicare's rate was paid its charge before the
    change and the new rate after: a 2 percent gap is the rate changing, not a fee set too low. A real gap (66 percent of
    the rate) on a code still billed is."""
    def paid_at(i, code, charge, allowed, dos):
        co = f"{float(charge) - float(allowed):.2f}"
        return clm6(f"AB{i:07d}C017", "1", charge, allowed, stmt=dos, lines=sv6(code, charge, allowed, allowed, cas=f"CAS*CO*45*{co}~" if co != "0.00" else "", dos=dos))
    claims = [paid_at(i, "77280", "300.00", "238.79", "20260410") for i in range(1, 9)] \
        + [paid_at(100 + i, "77280", "234.90", "234.90", "20260105") for i in range(8)] \
        + [paid_at(200 + i, "88305", "150.00", "98.00", "20260410") for i in range(8)] \
        + [paid_at(300 + i, "88305", "65.08", "65.08", "20260412") for i in range(8)] \
        + [paid_at(400 + i, "17313", "1200.00", "1000.00", "20260410") for i in range(8)] \
        + [paid_at(500 + i, "17313", "960.00", "960.00", "20260412") for i in range(8)] \
        + [paid_at(600 + i, "17311", "900.00", "600.00", "20251001") for i in range(8)] \
        + [paid_at(700 + i, "17311", "400.00", "400.00", "20251002") for i in range(8)]
    store = build_remits(tmp_path, [("20260501", claims)])
    Detector(store, run_id="r7").run()
    caps = {f["procedure_code"]: f for f in findings(store, "CHARGE_BELOW_RATE")}
    store.close()
    # 77280: Medicare's mid-year raise, the fee was raised with it. 17313: billed 4 percent under the rate, still today
    # ($320 in all): too close to be a fee set too low. 17311: a real gap, but the code is no longer billed at all.
    assert set(caps) == {"88305"}, sorted(caps)
    assert json.loads(caps["88305"]["evidence"])["payer_rate_cents"] == 9800


def test_medicare_fix_items_say_the_route_and_the_letter_names_the_level(tmp_path):
    """The What to do box reads the Medicare route (reopening, new claim, redetermination) from the server, and a
    Medicare letter is never titled a reconsideration, which is Medicare's second level."""
    base = {"payer_kind": "medicare", "actionable": "correct_and_resubmit", "signal": "DENIED_MISSING_MODIFIER", "procedure_code": "99213",
            "title": "t", "evidence": {}, "payer_group": "Medicare KY (CGS J15)"}
    reopen = dict(base, deadline_basis="Medicare Part B takes no corrected claims: ask for a reopening (a clerical error) within 1 year")
    newclaim = dict(base, deadline_basis="Medicare Part B: a claim returned as unprocessable (MA130) is fixed by sending a new claim")
    records = dict(base, signal="DENIED_MISSING_INFO", deadline_basis="Medicare Part B: information or records the claim lacked go in a "
                   "redetermination within 120 days of receipt of the remittance (42 CFR 405.942); a reopening takes no documents")
    assert "reopening" in next_step(reopen) and "new claim" in next_step(newclaim) and "redetermination" in next_step(records)
    assert next_step(dict(base, payer_kind="commercial")) is None
    for f, word in ((reopen, "Request to reopen"), (records, "Request for redetermination")):
        p = build_packet({"finding": f, "claim": {}, "remit": {}})
        assert p["subject"].startswith(word) and "reconsideration" not in p["subject"].lower(), p["subject"]
        assert "Claim Reconsideration" not in p["letter"]


def test_older_undated_items_open_exactly_what_today_counts(tmp_path, monkeypatch):
    db = str(tmp_path / "s.sqlite")
    today = utc_today()
    old_dos, old_paid = ymd(today - timedelta(days=210)), ymd(today - timedelta(days=200))
    new_dos, new_paid = ymd(today - timedelta(days=40)), ymd(today - timedelta(days=30))
    add_file(tmp_path, db, clean_visits(100, 10, "99213", "150.00", "90.00", old_dos), old_paid)
    add_file(tmp_path, db, [clm(pcn(1), "1", sv("99213", "150.00", "0.00", cas="CAS*CO*96*150.00~", dos=old_dos), dos=old_dos)], old_paid)
    add_file(tmp_path, db, clean_visits(200, 10, "99213", "150.00", "90.00", new_dos), new_paid)
    add_file(tmp_path, db, [clm(pcn(2), "1", sv("99213", "150.00", "0.00", cas="CAS*CO*96*150.00~", dos=new_dos), dos=new_dos)], new_paid)
    st = Store(db)
    Detector(st, run_id="r7").run()
    st.close()
    tc, auth = client(monkeypatch, db)
    b = tc.get("/api/brief", auth=auth).json()
    older = (b["work"]["undated_n"] - b["decide"]["n"], b["work"]["undated_cents"] - b["decide"]["cents"])
    assert older[0] == 1 and b["decide"]["n"] == 1
    got = tc.get("/api/findings", params={"category": MONEY, "live": 1, "status": "open,in_progress", "deadline": "older"}, auth=auth).json()
    assert (got["total"], got["shortfall_cents"]) == older


def test_work_done_counts_items_moved_on_not_clicks(tmp_path):
    from tests.test_insights import store_with_work
    st = store_with_work(tmp_path)
    today = utc_today().isoformat()
    ids = [r[0] for r in st.query("SELECT finding_id FROM findings WHERE category='denial' ORDER BY finding_id")]
    apply_status(st.conn, ids[0], "in_progress", actor="biller", at=today + "T09:00:00+00:00")
    apply_status(st.conn, ids[0], "appealed", actor="biller", at=today + "T09:05:00+00:00")       # one item, two clicks
    apply_status(st.conn, ids[1], "appealed", actor="biller", at=today + "T09:10:00+00:00")
    apply_status(st.conn, ids[1], "open", actor="biller", at=today + "T09:11:00+00:00")           # undone
    done = insights.done(st.conn, today)
    st.close()
    assert (done["today_n"], done["week_n"]) == (1, 1)


def test_soonest_first_puts_open_windows_first_then_closed_then_undated(tmp_path, monkeypatch):
    from tests.test_insights import store_with_work
    st = store_with_work(tmp_path)
    today = utc_today()
    ids = [r[0] for r in st.query("SELECT finding_id FROM findings WHERE category='denial' ORDER BY finding_id")]
    st.conn.execute("UPDATE findings SET appeal_deadline=NULL WHERE finding_id=?", (ids[0],))
    st.conn.execute("UPDATE findings SET appeal_deadline=? WHERE finding_id=?", ((today - timedelta(days=3)).isoformat(), ids[1]))
    st.conn.execute("UPDATE findings SET appeal_deadline=? WHERE finding_id=?", ((today + timedelta(days=3)).isoformat(), ids[2]))
    st.conn.commit()
    st.close()
    tc, auth = client(monkeypatch, str(tmp_path / "s.sqlite"))
    rows = tc.get("/api/findings", params={"category": MONEY, "status": "all", "sort": "deadline", "limit": 50}, auth=auth).json()["items"]
    order = [r["appeal_deadline"] for r in rows]
    nowish = today.isoformat()
    first_closed = next(i for i, d in enumerate(order) if d and d < nowish)
    first_undated = order.index(None)
    assert order[0] == (today + timedelta(days=3)).isoformat()
    assert all(d and d >= nowish for d in order[:first_closed]) and first_closed < first_undated and all(d is None for d in order[first_undated:])


def test_the_worklist_export_is_in_the_pages_words(tmp_path, monkeypatch):
    from tests.test_insights import store_with_work
    st = store_with_work(tmp_path)
    st.close()
    tc, auth = client(monkeypatch, str(tmp_path / "s.sqlite"))
    p = {"category": MONEY, "live": 1, "status": "open,in_progress", "deadline": "valid"}
    r = tc.get("/api/findings.csv", params=dict(p, view="worklist"), auth=auth)
    assert r.status_code == 200
    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[0][:4] == ["File by", "Kind of work", "What happened", "What to do"] and "Amount ($)" in rows[0]
    kinds = {row[1] for row in rows[1:]}
    assert kinds == {"Fix and resubmit", "Get the authorization"}, kinds
    status = rows[0].index("Status")
    assert {row[status] for row in rows[1:]} == {"To do"}
    listed = tc.get("/api/findings", params=p, auth=auth).json()
    assert len(rows) - 1 == listed["total"]
    assert "SAMPLE" not in r.text.upper()                    # names are off: no patient in the file


def test_the_feed_check_says_payments_are_old_when_files_did_arrive(tmp_path):
    from tests.test_insights import store_with_work
    st = store_with_work(tmp_path)
    today = utc_today()
    st.conn.execute("UPDATE files SET ingested_at=?", ((today - timedelta(days=2)).isoformat() + "T10:00:00+00:00",))
    st.conn.commit()
    feed = insights.data_health(st.conn, insights.newest_payment(st.conn), today.isoformat())[0]
    st.close()
    assert feed["key"] == "feed" and not feed["ok"]
    assert feed["title"].startswith("No payment newer than") and "Files did arrive" in feed["detail"] and "TriZetto" in feed["who"]
