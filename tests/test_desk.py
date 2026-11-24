"""The category pages (Denials, Bundling & modifiers, Downcoding, Contracts & underpayment, the Worklist): every number
they show opens a list of exactly that many, and their tiles add up to Today's numbers. Everything here is invented."""
from __future__ import annotations

import json
import os
import sys
from datetime import timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch import insights  # noqa: E402
from remitwatch.detect import Detector  # noqa: E402
from tests.test_activity import PW  # noqa: E402

MONEY = "denial,bundling,downcoding,underpayment"
CATEGORIES = ("denial", "bundling", "downcoding", "underpayment")


def _client(tmp_path, monkeypatch):
    """A store with a routine-rate pattern whose claims close ten days ago, in three days and in five months; whole
    claims denied for five reasons; denied, bundled and underpaid lines; two items to decide (one made here, and the
    whole claim denied as filed too late, which the detector gives no filing date), one sent long ago and one whose
    window closed last week."""
    from fastapi.testclient import TestClient

    from remitwatch import api
    from remitwatch.actions import apply_status
    from remitwatch.store import Store
    from tests.review6.r6cov_h import add_file, clean_visits, clm, pcn, sv, utc_today, ymd
    db = str(tmp_path / "s.sqlite")
    today = utc_today()
    paid = lambda back: ymd(today - timedelta(days=back))                       # noqa: E731

    def visit(n, allowed, dos, plan):
        co = f"{1200 - float(allowed):.2f}"
        return clm(pcn(n), "1", sv("17311", "1200.00", allowed, allowed, f"CAS*CO*45*{co}~", dos=dos), dos=dos, charge="1200.00", paid=allowed, plan=plan)
    for k, back in enumerate((190, 177, 30)):
        add_file(tmp_path, db, [visit(1000 + 100 * k + i, "700.00", paid(back + 10), "PPO") for i in range(25)]
                 + [visit(2000 + 100 * k + i, "400.00", paid(back + 10), "HMO SELECT") for i in range(6)], paid(back), payer="AETNA", payer_id="AETNA")
    dos = paid(40)
    add_file(tmp_path, db, clean_visits(100, 10, "99214", "223.65", "117.60", dos), paid(30))
    add_file(tmp_path, db, [clm(pcn(90 + i), "4", sv("99214", "223.65", "0.00", cas=f"CAS*CO*{carc}*223.65~", dos=dos), dos=dos, charge="223.65")
                            for i, carc in enumerate(("197", "16", "97", "29", "208"))], paid(30))
    # denied and bundled lines inside paid claims
    add_file(tmp_path, db, [clm(pcn(300 + i), "1", sv("99213", "150.00", "0.00", None, "CAS*CO*16*150.00~LQ*HE*N822~", dos=dos)
                                + sv("11102", "180.00", "120.00", "120.00", "CAS*CO*45*60.00~", dos=dos), dos=dos, charge="330.00", paid="120.00")
                            for i in range(4)], paid(20))
    st = Store(db)
    Detector(st, run_id="desk").run()
    money = "category IN ('denial','bundling','downcoding','underpayment') AND signal != 'SECOND_RATE'"
    # one of these has no filing date: the whole claim denied as filed too late (CO-29), an item to decide. The pages are
    # tested on an undated item whatever the detector's dating rule (since checkpoint 1bb8071 it dates such a denial from its
    # remittance: detect.END_DATED_CATEGORIES), so the fixture makes it undated itself
    st.conn.execute(f"UPDATE findings SET appeal_deadline=NULL WHERE {money} AND carc='29'")
    st.conn.commit()
    assert [r[0] for r in st.query(f"SELECT carc FROM findings WHERE {money} AND appeal_deadline IS NULL")] == ["29"]
    # The items changed below are picked among the dated ones, in an order that does not move with the day: the four denied
    # lines by claim, then the whole claims by reason. (A finding's id is a hash of keys built from today's dates, so an
    # order by id picked other items from one day to the next, and on some days the undated claim itself.)
    ids = [r[0] for r in st.query(f"SELECT finding_id FROM findings WHERE {money} AND appeal_deadline IS NOT NULL "
                                  "ORDER BY signal DESC, carc, patient_control_number")]
    assert len(ids) >= 6
    st.conn.execute("UPDATE findings SET appeal_deadline=? WHERE finding_id=?", ((today + timedelta(days=2)).isoformat(), ids[0]))       # this week
    st.conn.execute("UPDATE findings SET appeal_deadline=? WHERE finding_id=?", ((today - timedelta(days=6)).isoformat(), ids[1]))       # missed
    st.conn.execute("UPDATE findings SET appeal_deadline=NULL, actionable='review' WHERE finding_id=?", (ids[2],))                       # to decide
    apply_status(st.conn, ids[3], "appealed", actor="biller", at="2026-11-13T15:05:00+00:00")
    st.conn.execute("UPDATE findings SET appealed_at='2026-08-01T00:00:00+00:00' WHERE finding_id=?", (ids[3],))                          # follow up
    st.conn.execute("UPDATE findings SET appeal_deadline=?, payment_date=? WHERE finding_id=?",                                           # this month,
                    ((today + timedelta(days=15)).isoformat(), (today - timedelta(days=200)).isoformat(), ids[5]))                        # paid long ago
    st.conn.commit()
    st.close()
    monkeypatch.setattr(api, "DB_PATH", db)
    monkeypatch.setattr(api, "PASSWORD", PW)
    monkeypatch.setattr(api, "USERNAME", "staff")
    monkeypatch.delenv("REMITWATCH_MODE", raising=False)
    return TestClient(api.app), ("staff", PW)


def test_every_number_on_a_category_page_opens_a_list_of_exactly_that_many(tmp_path, monkeypatch):
    from remitwatch.api import DESK_TILES
    tc, auth = _client(tmp_path, monkeypatch)

    def listed(category, **kw):
        p = dict({"category": category, "live": 1, "limit": 2000}, **{k: v for k, v in kw.items() if v is not None})
        r = tc.get("/api/findings", params=p, auth=auth).json()
        return r["total"], r["shortfall_cents"] or 0

    pages = {}
    for cat in CATEGORIES + (MONEY,):
        d = tc.get("/api/desk", params={"category": cat, "status": "open,in_progress", "deadline": "valid"}, auth=auth).json()
        pages[cat] = d
        for name, status, deadline in DESK_TILES:
            t = d["tiles"][name]
            assert listed(cat, status=status, deadline=deadline) == (t["n"], t["cents"]), (cat, name, t)
        # what to do: each kind of work, and each payer pile under it
        for k in d["scope"]["kinds"]:
            assert listed(cat, status="open,in_progress", deadline="valid", kind=k["key"]) == (k["n"], k["cents"]), (cat, k)
            for p in k["piles"]:
                assert listed(cat, status="open,in_progress", deadline="valid", kind=k["key"], payer=p["payer"]) == (p["n"], p["cents"]), (cat, k["key"], p)
        assert (d["scope"]["n"], d["scope"]["cents"]) == (d["tiles"]["valid"]["n"], d["tiles"]["valid"]["cents"])
    # the routine-rate pattern counts its claims in range, in the tile, its kind and its payer's pile alike
    under = pages["underpayment"]
    rate = [k for k in under["scope"]["kinds"] if k["key"] == "rate"][0]
    assert rate["cents"] == 2 * 180000 and rate["piles"][0]["cents"] == 2 * 180000             # its claims that close in 3 days and in 5 months
    assert under["tiles"]["week"]["cents"] == 180000 and under["tiles"]["missed"]["cents"] == 180000
    # the pages add up to Today: the badges are step 1, the window-open tiles the total, the to-decide and missed tiles too
    b = tc.get("/api/brief", auth=auth).json()
    w = b["work"]["total"]
    for cat in CATEGORIES:
        assert b["badges"][cat] == pages[cat]["tiles"]["week"], cat
    assert sum(b["badges"][c]["n"] for c in CATEGORIES) == w["due7_n"] >= 2 and w["due30_n"] > w["due7_n"]
    assert sum(b["badges"][c]["cents"] for c in CATEGORIES) == w["due7_cents"]
    for tile, want in (("valid", (w["n"], w["cents"])), ("decide", (b["decide"]["n"], b["decide"]["cents"])),
                       ("missed", (b["missed"]["n"], b["missed"]["cents"])), ("followup", (b["follow_ups"]["n"], b["follow_ups"]["cents"]))):
        got = (sum(pages[c]["tiles"][tile]["n"] for c in CATEGORIES), sum(pages[c]["tiles"][tile]["cents"] for c in CATEGORIES))
        assert got == want == (pages[MONEY]["tiles"][tile]["n"], pages[MONEY]["tiles"][tile]["cents"]), (tile, got, want)
    # to decide: the item the fixture left with no filing date, and the whole claim denied as filed too late
    assert b["decide"]["n"] == 2 and b["missed"]["n"] >= 2 and b["follow_ups"]["n"] == 1
    # every item to do falls in exactly one of window open, to decide, older with no date, window closed
    for cat in CATEGORIES:
        t = pages[cat]["tiles"]
        assert t["valid"]["n"] + t["decide"]["n"] + t["older"]["n"] + t["expired"]["n"] == t["all"]["n"], cat


def test_the_kinds_under_a_tile_and_a_payment_window_open_lists_of_that_size(tmp_path, monkeypatch):
    """What to do follows the chosen tile and the payment window Today can send (paid in the last N days); a reason chip
    counts its kind's items under the same filters."""
    tc, auth = _client(tmp_path, monkeypatch)
    for status, deadline, paid in (("open,in_progress", "expired", 0), ("open", "missed", 0), ("appealed", None, 0),
                                   ("open,in_progress", None, 90), ("all", None, 90)):
        q = {"category": MONEY, "status": status, "paid": paid, "tiles": 0}
        if deadline:
            q["deadline"] = deadline
        d = tc.get("/api/desk", params=q, auth=auth).json()
        assert "tiles" not in d
        for k in d["scope"]["kinds"]:
            p = dict(q, live=1, kind=k["key"], limit=2000)
            p.pop("tiles")
            r = tc.get("/api/findings", params=p, auth=auth).json()
            assert (r["total"], r["shortfall_cents"] or 0) == (k["n"], k["cents"]), (q, k)
            c = tc.get("/api/worklist/counts", params={x: y for x, y in p.items() if x != "limit"}, auth=auth).json()
            for s in c["signals"]:
                one = tc.get("/api/findings", params=dict(p, signal=s["signal"]), auth=auth).json()
                assert (one["total"], one["shortfall_cents"] or 0) == (s["n"], s["cents"]), (q, k["key"], s)
            assert sum(s["n"] for s in c["signals"]) == k["n"]
        if paid and status != "all":
            every = tc.get("/api/desk", params=dict(q, paid=0), auth=auth).json()["scope"]
            assert every["n"] > d["scope"]["n"], "the payment window must narrow what to do"        # the item paid 200 days ago


def test_payment_fees_count_the_twelve_full_months_before_the_newest_payment(tmp_path):
    assert insights.fee_months("2026-10-09") == ("2025-10", "2026-09")
    assert insights.fee_months("2026-01-31") == ("2025-01", "2025-12")
    assert insights.fee_months("2026-12-01") == ("2025-12", "2026-11")
    assert insights.fee_months(None) == (None, None)
    from tests.test_features import build_store, claim, svc
    st = build_store(tmp_path, [claim(1, svc("99213", "150.00", "90.00", "90.00", "CAS*CO*45*60.00~", dos="20261001"), dos="20261001")],
                     pay_date="20261015")
    Detector(st, run_id="fees").run()           # the findings table, as an analysis leaves it
    st.conn.execute("DELETE FROM findings WHERE signal='FEE_SKIM'")
    cols = {r[1] for r in st.conn.execute("PRAGMA table_info(findings)")}
    assert cols
    for i, (month, cents, status) in enumerate((("2025-09", 500, "open"), ("2025-10", 700, "open"), ("2026-09", 1100, "open"),
                                                 ("2026-10", 1300, "open"), ("2026-03", 1700, "dismissed"))):
        row = {"finding_id": f"fee{i}", "signal": "FEE_SKIM", "category": "fees", "payer_group": "Zelis payer", "month": month,
               "shortfall_cents": cents, "status": status, "stale": 0, "severity": "low", "actionable": "opt_out", "title": "fees"}
        row = {k: v for k, v in row.items() if k in cols}
        st.conn.execute(f"INSERT INTO findings ({','.join(row)}) VALUES ({','.join('?' * len(row))})", tuple(row.values()))
    st.conn.commit()
    f = insights.fee_levers(st.conn, "2026-10-15")
    [p] = f["payment_fees"]
    # October 2025 to September 2026: the two fees inside, not September 2025, not the month still being paid, not a dismissed one
    assert (p["cents"], p["cents12"]) == (500 + 700 + 1100 + 1300, 700 + 1100)
    assert (f["payment_fee12_cents"], f["payment_fee12_payers"], f["payment_fee12_from"], f["payment_fee12_to"]) == (1800, 1, "2025-10", "2026-09")
    st.close()


def test_short_labels_and_instructions_stay_short_and_cover_every_label():
    for sig in insights.SIGNAL_LABELS:
        assert sig in insights.SHORT_LABELS, sig
    for sig, text in insights.SHORT_LABELS.items():
        assert len(text.split()) <= 6, (sig, text)
    for t in insights.WORK_TYPES:
        assert t.get("short") and len(t["short"].split()) <= 12, t["key"]
    # the recurring causes use the list's own short words
    assert all(insights.CAUSES[s] == insights.SHORT_LABELS[s] for s in insights.CAUSES)


def test_the_page_puts_the_categories_after_today_and_sends_the_old_queue_to_the_worklist():
    import re
    static = os.path.join(os.path.dirname(insights.__file__), "static")
    page = open(os.path.join(static, "index.html"), encoding="utf-8").read()
    tabs = re.search(r"const TABS = \[(.*?)\];", page, flags=re.S).group(1)
    keys = re.findall(r"\['([a-z]+)','", tabs)
    # the Faxes page sits after the Worklist it sends from (remitwatch.fax)
    assert keys[:9] == ["today", "denials", "bundling", "downcoding", "contracts", "fees", "worklist", "faxes", "wins"], keys
    assert "appeals:'worklist'" in page and '<script src="/static/desk.js"></script>' in page
    desk = open(os.path.join(static, "desk.js"), encoding="utf-8").read()
    for key in ("denials", "bundling", "downcoding", "contracts", "worklist"):
        assert re.search(rf"\b{key}:\s+\{{cat:", desk), key


def test_the_tiles_follow_the_chosen_kind_and_reason_and_the_kinds_follow_the_search(tmp_path, monkeypatch):
    """With a kind of work or a reason chosen (Today's batch, a kind row, a reason chip), each tile counts that kind or
    reason, as the list it opens does (a tile click keeps them). With a search typed, the kinds of work and their payer
    piles count the searched items, as the lists they open do (their click keeps the search)."""
    from remitwatch.api import DESK_TILES
    from urllib.parse import quote
    tc, auth = _client(tmp_path, monkeypatch)
    for kind, reason in (("rate", None), ("fix", None), ("auth", None), (None, "SECOND_RATE"), (None, "CLAIM_DENIED_UNPAID")):
        d = tc.get("/api/desk", params={"category": MONEY, **({"kind": kind} if kind else {}), **({"reason": reason} if reason else {})}, auth=auth).json()
        for name, status, deadline in DESK_TILES:
            p = {"category": MONEY, "live": 1, "limit": 2000, "status": status, **({"deadline": deadline} if deadline else {}),
                 **({"kind": kind} if kind else {}), **({"signal": reason} if reason else {})}
            r = tc.get("/api/findings", params=p, auth=auth).json()
            assert (r["total"], r["shortfall_cents"] or 0) == (d["tiles"][name]["n"], d["tiles"][name]["cents"]), (kind, reason, name)
        assert len(d["scope"]["kinds"]) >= 3, "the kinds of work stay listed so another can be chosen"
    for typed in ("99214", "17311", "Aetna"):
        hdr = {"X-Search": quote(typed)}
        d = tc.get("/api/desk", params={"category": MONEY, "deadline": "valid"}, headers=hdr, auth=auth).json()
        every = tc.get("/api/desk", params={"category": MONEY, "deadline": "valid"}, auth=auth).json()
        assert d["tiles"] == every["tiles"], "the tiles are not searched: a tile click clears the search"
        for k in d["scope"]["kinds"]:
            base = {"category": MONEY, "live": 1, "limit": 2000, "status": "open,in_progress", "deadline": "valid", "kind": k["key"]}
            r = tc.get("/api/findings", params=base, headers=hdr, auth=auth).json()
            assert (r["total"], r["shortfall_cents"] or 0) == (k["n"], k["cents"]), (typed, k)
            for p in k["piles"]:
                r = tc.get("/api/findings", params=dict(base, payer=p["payer"]), headers=hdr, auth=auth).json()
                assert (r["total"], r["shortfall_cents"] or 0) == (p["n"], p["cents"]), (typed, k["key"], p)
        assert d["scope"]["n"] < every["scope"]["n"], typed


def test_progress_counts_open_and_closed_windows_as_the_worklist_tiles_do(tmp_path, monkeypatch):
    import sqlite3
    tc, auth = _client(tmp_path, monkeypatch)
    # a fee or contract item with a date is still not claim work: neither page counts it
    from remitwatch import api
    con = sqlite3.connect(api.DB_PATH)
    con.execute("""INSERT INTO findings (finding_id, signal, category, payer_group, shortfall_cents, status, stale, severity, actionable, title, appeal_deadline)
                   VALUES ('other1', 'MA_BELOW_MEDICARE', 'contract', 'Some plan', 12345, 'open', 0, 'medium', 'renegotiate', 'rates', date('now','+40 days'))""")
    con.execute("""INSERT INTO findings (finding_id, signal, category, payer_group, shortfall_cents, status, stale, severity, actionable, title, appeal_deadline)
                   VALUES ('other2', 'TAKEBACK', 'fees', 'Some plan', 54321, 'open', 0, 'medium', 'review', 'taken back', date('now','-40 days'))""")
    n_other = con.execute("SELECT COUNT(*) FROM findings WHERE category IN ('fees','contract') AND status IN ('open','in_progress')").fetchone()[0]
    con.commit()
    con.close()
    prog = tc.get("/api/progress", auth=auth).json()
    tiles = tc.get("/api/desk", params={"category": MONEY}, auth=auth).json()["tiles"]
    assert prog["live"] == tiles["valid"] and prog["expired"] == tiles["expired"], (prog["live"], prog["expired"], tiles["valid"], tiles["expired"])
    assert n_other, "the store has fee or contract items to leave out"


def test_payment_fees_are_one_request_per_payer_about_its_payments(tmp_path, monkeypatch):
    """Payment fees are listed by the payer the remittance names (the one to write to), and its letter asks for standard
    EFT with that payer's own totals: no patient or claim, no appeal statutes, whichever fee item it is opened from."""
    from remitwatch import api
    from tests.test_features import build_store, claim, svc
    st = build_store(tmp_path, [claim(1, svc("99213", "150.00", "90.00", "90.00", "CAS*CO*45*60.00~", dos="20261001"), dos="20261001")],
                     pay_date="20261015")
    Detector(st, run_id="fees").run()
    st.conn.execute("DELETE FROM findings WHERE signal='FEE_SKIM'")
    cols = {r[1] for r in st.conn.execute("PRAGMA table_info(findings)")}
    rows = [("fa1", "CARRIER ONE", "2025-08", 400, "open"), ("fa2", "CARRIER ONE", "2026-01", 900, "open"), ("fa3", "CARRIER ONE", "2026-09", 300, "in_progress"),
            ("fb1", "CARRIER TWO", "2024-03", 250, "open"), ("fc1", "CARRIER THREE", "2026-05", 700, "appealed")]
    for fid, name, month, cents, status in rows:
        row = {"finding_id": fid, "signal": "FEE_SKIM", "category": "fees", "payer_group": "Supplement carriers", "payer_name": name, "payer_kind": "supplement",
               "month": month, "shortfall_cents": cents, "status": status, "stale": 0, "severity": "medium", "actionable": "opt_out",
               "title": f"fees withheld by {name} in {month}", "evidence": json.dumps({"references": {"ZELIS TRANSACTION FEE": 3}}),
               "appealed_at": "2026-10-20T10:00:00+00:00" if status == "appealed" else None}
        row = {k: v for k, v in row.items() if k in cols}
        st.conn.execute(f"INSERT INTO findings ({','.join(row)}) VALUES ({','.join('?' * len(row))})", tuple(row.values()))
    st.conn.commit()
    st.close()
    monkeypatch.setattr(api, "DB_PATH", str(tmp_path / "s.sqlite"))
    monkeypatch.setattr(api, "PASSWORD", PW)
    monkeypatch.setattr(api, "USERNAME", "staff")
    monkeypatch.delenv("REMITWATCH_MODE", raising=False)
    from fastapi.testclient import TestClient
    tc, auth = TestClient(api.app), ("staff", PW)
    fp = tc.get("/api/fees/payers", auth=auth).json()
    by = {p["payer_name"]: p for p in fp["payers"]}
    assert [p["payer_name"] for p in fp["payers"]] == ["CARRIER ONE", "CARRIER TWO"]            # the 12-month total first; the one asked is apart
    assert (fp["from"], fp["to"]) == ("2025-10", "2026-09")
    one = by["CARRIER ONE"]
    assert (one["cents"], one["cents12"], one["months"], one["first_month"], one["last_month"]) == (1600, 1200, 3, "2025-08", "2026-09")
    assert sorted(one["ids"]) == ["fa1", "fa2", "fa3"] and one["vendors"] == ["Zelis"]
    assert (fp["cents12"], fp["payers12"], fp["cents"]) == (1200, 1, 1850)
    assert [a["payer_name"] for a in fp["asked"]] == ["CARRIER THREE"]
    letter = tc.get("/api/fees/letter", params={"payer": "CARRIER ONE"}, auth=auth).json()
    text = letter["letter"]
    assert "To: CARRIER ONE" in text and "$12.00 in the 12 months October 2025 to September 2026" in text and "$16.00 since August 2025" in text
    assert "Zelis" in text and "45 CFR 162.1601" in text
    for absent in ("Patient:", "claim number", "KRS", "ERISA", "Please reprocess the claim", "Date of service"):
        assert absent not in text, absent
    # opened from one month's fee item, the letter is the payer's, not that month's
    via = tc.get("/api/appeal/fa2", auth=auth).json()
    assert via["letter"].split("\n", 1)[1] == text.split("\n", 1)[1] and via["finding_id"] == "fa2"
    assert tc.get("/api/fees/letter", params={"payer": "NOBODY"}, auth=auth).status_code == 404


def test_asking_a_payer_keeps_its_fees_in_the_history_and_counts_once_on_today(tmp_path, monkeypatch):
    """Mark asked moves a payer's fee months to Sent: what vendors took stays in every total (the tile, Today, Bigger wins),
    the payer moves to Asked with its letter still to hand, and Today's done box counts one request with no dollars."""
    from fastapi.testclient import TestClient

    from remitwatch import api
    from tests.test_features import build_store, claim, svc
    st = build_store(tmp_path, [claim(1, svc("99213", "150.00", "90.00", "90.00", "CAS*CO*45*60.00~", dos="20261001"), dos="20261001")], pay_date="20261015")
    Detector(st, run_id="fees").run()
    st.conn.execute("DELETE FROM findings WHERE signal='FEE_SKIM'")
    cols = {r[1] for r in st.conn.execute("PRAGMA table_info(findings)")}
    for fid, name, month, cents in (("a1", "CARRIER ONE", "2026-01", 900), ("a2", "CARRIER ONE", "2026-02", 400), ("b1", "CARRIER TWO", "2026-03", 300)):
        row = {"finding_id": fid, "signal": "FEE_SKIM", "category": "fees", "payer_group": "Supplement carriers", "payer_name": name, "payer_kind": "supplement",
               "month": month, "shortfall_cents": cents, "status": "open", "stale": 0, "severity": "medium", "actionable": "opt_out", "title": "fees",
               "evidence": json.dumps({"references": {"ZELIS TRANSACTION FEE": 1}})}
        row = {k: v for k, v in row.items() if k in cols}
        st.conn.execute(f"INSERT INTO findings ({','.join(row)}) VALUES ({','.join('?' * len(row))})", tuple(row.values()))
    st.conn.commit()
    st.close()
    monkeypatch.setattr(api, "DB_PATH", str(tmp_path / "s.sqlite"))
    monkeypatch.setattr(api, "PASSWORD", PW)
    monkeypatch.setattr(api, "USERNAME", "staff")
    monkeypatch.delenv("REMITWATCH_MODE", raising=False)
    tc, auth = TestClient(api.app), ("staff", PW)
    before = tc.get("/api/fees/payers", auth=auth).json()
    brief0 = tc.get("/api/brief", auth=auth).json()
    one = [p for p in before["payers"] if p["payer_name"] == "CARRIER ONE"][0]
    r = tc.post("/api/findings/bulk-status", json={"ids": one["ids"], "status": "appealed"}, headers={"X-Requested-With": "remitwatch"}, auth=auth)
    assert r.status_code == 200, r.text
    after = tc.get("/api/fees/payers", auth=auth).json()
    assert after["history"] == before["history"] and after["history"]["cents"] == 1600
    assert [p["payer_name"] for p in after["payers"]] == ["CARRIER TWO"] and [a["payer_name"] for a in after["asked"]] == ["CARRIER ONE"]
    assert sorted(after["asked"][0]["ids"]) == ["a1", "a2"] and after["asked"][0]["asked"]
    again = tc.get("/api/fees/letter", params={"payer": "CARRIER ONE"}, auth=auth)
    assert again.status_code == 200 and "CARRIER ONE" in again.json()["letter"]
    brief1 = tc.get("/api/brief", auth=auth).json()
    assert brief1["fees"]["payment_fee_cents"] == brief0["fees"]["payment_fee_cents"] == 1600
    done = brief1["done"]
    assert (done["today_n"], done["week_n"], done["week_cents"]) == (1, 1, 0), done
    # put back: the payer is to ask again, and Today's request is undone
    tc.post("/api/findings/bulk-status", json={"ids": one["ids"], "status": "open"}, headers={"X-Requested-With": "remitwatch"}, auth=auth)
    assert {p["payer_name"] for p in tc.get("/api/fees/payers", auth=auth).json()["payers"]} == {"CARRIER ONE", "CARRIER TWO"}
    assert tc.get("/api/brief", auth=auth).json()["done"]["week_n"] == 0


def test_overview_rows_are_one_per_signal_and_a_year_narrows_the_category_page(tmp_path, monkeypatch):
    from remitwatch.api import DESK_TILES
    tc, auth = _client(tmp_path, monkeypatch)
    s = tc.get("/api/summary", params={"status": "all"}, auth=auth).json()
    sigs = [(r["signal"], r["category"]) for r in s["by_signal"]]
    assert len(sigs) == len(set(sigs)), "one row per signal"
    for r in s["by_signal"]:
        if r["category"] in CATEGORIES:
            got = tc.get("/api/findings", params={"category": r["category"], "signal": r["signal"], "status": "all", "live": 1, "limit": 1}, auth=auth).json()
            assert got["total"] == r["n"], r
    # half the items in another year, so the year filter has something to tell apart
    import sqlite3

    from remitwatch import api
    con = sqlite3.connect(api.DB_PATH)
    con.execute("UPDATE findings SET year='2025' WHERE rowid % 2 = 0")
    con.commit()
    con.close()
    years = sorted({r[0] for r in sqlite3.connect(api.DB_PATH).execute("SELECT DISTINCT year FROM findings WHERE year IS NOT NULL")})
    assert len(years) >= 2
    for y in years:
        d = tc.get("/api/desk", params={"category": MONEY, "year": y}, auth=auth).json()
        for name, status, deadline in DESK_TILES:
            p = {"category": MONEY, "live": 1, "limit": 1, "status": status, "year": y, **({"deadline": deadline} if deadline else {})}
            r = tc.get("/api/findings", params=p, auth=auth).json()
            assert (r["total"], r["shortfall_cents"] or 0) == (d["tiles"][name]["n"], d["tiles"][name]["cents"]), (y, name)


# --------------------------------------------------------------------------- claims no payer answered: one at a time, with a note
# The page's own code (desk.js, today.js, features.js and index.html's helpers) in node, on a stand-in for the page: the
# rows as rowHtml draws them, the bar as bindBulk binds it, and the status changes of an item's own panel. It prints what
# each step asked the person and what it sent. Usage: node <this file> <static dir>. Every item is invented.
LET_GO_PAGE_JS = r"""
const fs = require('fs'), vm = require('vm'), path = require('path');
const read = f => fs.readFileSync(path.join(process.argv[2], f), 'utf8');
const html = read('index.html');
const grab = (src, re) => { const m = src.match(re); if (!m) throw new Error('not found: ' + re); return m[0]; };
const helpers = [/const money = [^\n]*\n/, /const money2 = [^\n]*\n/, /const esc = [^\n]*\n/, /const SIGNAL_HELP = \{[\s\S]*?\n\};/].map(re => grab(html, re)).join('\n')
  + '\n' + grab(read('faxes.js'), /const FAX_KINDS = [^\n]*\n/);
// what the page asks the person (answered from a script; unanswered, a question is refused) and what it sends
const asked = [], sent = [];
let answers = [];
const ask = kind => text => { asked.push(kind + ': ' + text); const a = answers.length ? answers.shift() : (kind === 'prompt' ? null : false); return kind === 'alert' ? undefined : a; };
const ctx = { console, alert: ask('alert'), confirm: ask('confirm'), prompt: ask('prompt'), state: { worklist: ['c1', 'c2'], wlTotal: 2 },
  api: async (p, o) => { sent.push({ path: p, body: o && o.body ? JSON.parse(o.body) : null }); return { ok: true }; },
  render: async () => {}, openDetail: () => {}, closeDetail: () => {}, appealByFax: ids => { sent.push({ fax: ids }); } };
vm.createContext(ctx);
for (const src of [helpers, read('features.js'), read('today.js'), read('desk.js')]) vm.runInContext(src, ctx);
// a stand-in for the page: elements that answer the selectors the code uses (tag, #id, .class, [data-x], [data-x="y"], :checked)
class El {
  constructor(tag, o = {}, parent = null) { Object.assign(this, { tag, id: o.id || '', cls: o.cls || [], dataset: o.data || {}, checked: false, value: o.value, hidden: !!o.hidden, textContent: '', disabled: false, parent, kids: [] }); if (parent) parent.kids.push(this); }
  matches(sel) {
    const tag = (sel.match(/^[a-z]+/) || [''])[0], id = (sel.match(/#([\w-]+)/) || [])[1], cls = [...sel.matchAll(/\.([\w-]+)/g)].map(m => m[1]);
    const data = [...sel.matchAll(/\[data-([\w-]+)(?:="([^"]*)")?\]/g)];
    return (!tag || tag === this.tag) && (!id || id === this.id) && cls.every(c => this.cls.includes(c)) && (!/:checked/.test(sel) || this.checked)
      && data.every(([, k, val]) => k in this.dataset && (val === undefined || this.dataset[k] === val));
  }
  closest(sel) { for (let e = this; e; e = e.parent) if (e.matches(sel)) return e; return null; }
  all() { return this.kids.flatMap(k => [k, ...k.all()]); }
  querySelectorAll(sel) { return this.all().filter(e => e.matches(sel)); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}
const item = (finding_id, signal, actionable) => ({ finding_id, signal, actionable, status: 'open', payer_group: 'Plan A', appeal_deadline: '2027-03-01', shortfall_cents: 11760 });
const ITEMS = [item('c1', 'NO_ANSWER', 'review'), item('c2', 'REJECTED_PRE_PAYER', 'correct_and_resubmit'), item('d1', 'DENIED_MEDICAL_NECESSITY', 'appeal'), item('d2', 'DENIED_AUTH', 'appeal')];
const BRIEF = { today: '2026-11-22', work_types: [], claim_kinds: {}, labels: { NO_ANSWER: { label: 'Sent, and the payer never answered', work: 'chase' },
  REJECTED_PRE_PAYER: { label: 'Rejected before it reached the payer', work: 'fix' }, DENIED_MEDICAL_NECESSITY: { label: 'Denied as not medically necessary', work: 'note' },
  DENIED_AUTH: { label: 'Denied: no authorization', work: 'auth' } } };
function page() {             // the list as deskTab draws it: the bar, then one row per item with the attributes rowHtml gives it
  ctx.state.brief = BRIEF;    // the day the rows are dated from is the brief's, never the clock's (a change empties state.brief)
  const v = new El('main'), bar = new El('div', { id: 'bulk', cls: ['bulk'], hidden: true }, v);
  new El('span', { id: 'bulkN' }, bar); new El('button', { id: 'bulkFax' }, bar);
  for (const s of ['in_progress', 'appealed', 'dismissed', 'open']) new El('button', { data: { bulk: s } }, bar);
  new El('span', { id: 'bulkLeftOut' }, bar); new El('input', { id: 'selAll' }, v);
  for (const it of ITEMS) {
    const tag = ctx.rowHtml(BRIEF, it, {}).match(/^<tr[^>]*>/)[0];
    const tr = new El('tr', { cls: ['row'], data: Object.fromEntries([...tag.matchAll(/data-([\w-]+)="([^"]*)"/g)].map(m => [m[1], m[2]])) }, v);
    new El('input', { cls: ['sel'], value: it.finding_id }, tr);
  }
  ctx.bindBulk(v);
  const tick = ids => { v.querySelectorAll('.sel').forEach(cb => { cb.checked = ids.includes(cb.value); }); v.querySelector('.sel').onchange(); };
  return { v, tick, press: sel => v.querySelector(sel).onclick() };
}
const drawer = f => { const d = new El('div'); for (const id of ['sentNext', 'letGoNext', 'prevItem', 'nextItem', 'todoErr']) new El('button', { id }, d); ctx.bindWhatToDo(f, d); return d; };
const out = [];
const step = async (name, say, fn) => { ctx.state.brief = BRIEF; asked.length = 0; sent.length = 0; answers = say.slice(); const got = await fn(); out.push({ name, got, asked: asked.slice(), sent: sent.slice() }); };
(async () => {
  const all = ITEMS.map(i => i.finding_id), text = (p, sel) => p.v.querySelector(sel).textContent;
  let p = page();
  await step('rows', [], async () => p.v.querySelectorAll('tr.row').map(tr => tr.dataset.signal));
  await step('bar, all four ticked', [], async () => { p.tick(all); return { n: text(p, '#bulkN'), line: text(p, '#bulkLeftOut') }; });
  await step('bar, the two denials ticked', [], async () => { p.tick(['d1', 'd2']); return { n: text(p, '#bulkN'), line: text(p, '#bulkLeftOut') }; });
  await step('bulk let go, all four ticked', ['not ours to chase'], async () => { p.tick(all); await p.press('[data-bulk="dismissed"]'); });
  p = page();
  await step('bulk let go, the two unanswered ticked', [true], async () => { p.tick(['c1', 'c2']); await p.press('[data-bulk="dismissed"]'); });
  await step('bulk let go, the two denials ticked', [' the payer was right '], async () => { p.tick(['d1', 'd2']); await p.press('[data-bulk="dismissed"]'); });
  await step('bulk let go, the two denials ticked, no reason', ['  '], async () => { p.tick(['d1', 'd2']); await p.press('[data-bulk="dismissed"]'); });
  await step('bulk let go, the two denials ticked, cancelled', [null], async () => { p.tick(['d1', 'd2']); await p.press('[data-bulk="dismissed"]'); });
  p = page();
  await step('bulk mark started, all four ticked', [], async () => { p.tick(all); await p.press('[data-bulk="in_progress"]'); });
  p = page();
  await step('fax, an unanswered claim ticked', [], async () => { p.tick(['c1']); await p.press('#bulkFax'); });
  await step('fax, an unanswered claim and a denial ticked', [true], async () => { p.tick(['c1', 'd1']); await p.press('#bulkFax'); });
  // an item's own panel: its status buttons (setFindingStatus) and "Let go and open the next" (bindWhatToDo)
  const set = (id, status, signal) => ctx.setFindingStatus(id, status, 11760, signal);
  await step('panel let go, unanswered, cancelled', [null], () => set('c1', 'dismissed', 'NO_ANSWER'));
  await step('panel let go, unanswered, blank note', ['   '], () => set('c1', 'dismissed', 'NO_ANSWER'));
  await step('panel let go, unanswered, with a note', [' no record at the payer, past its limit '], () => set('c1', 'dismissed', 'NO_ANSWER'));
  await step('panel let go, rejected, with a note', ['billed to the patient'], () => set('c2', 'dismissed', 'REJECTED_PRE_PAYER'));
  await step('panel lost, rejected, blank note', [''], () => set('c2', 'lost', 'REJECTED_PRE_PAYER'));
  await step('panel lost, unanswered, with a note', ['proof of timely filing refused'], () => set('c1', 'lost', 'NO_ANSWER'));
  await step('panel sent, unanswered', [], () => set('c1', 'appealed', 'NO_ANSWER'));
  await step('panel let go, a denial, with a reason', ['the payer was right'], () => set('d2', 'dismissed', 'DENIED_AUTH'));
  await step('panel let go, a denial, cancelled', [null], () => set('d2', 'dismissed', 'DENIED_AUTH'));
  await step('panel let go, a denial, blank reason', ['  '], () => set('d2', 'dismissed', 'DENIED_AUTH'));
  await step('panel lost, a denial, blank note', [''], () => set('d2', 'lost', 'DENIED_AUTH'));
  await step('panel lost, a denial, with a reason', ['upheld on appeal'], () => set('d2', 'lost', 'DENIED_AUTH'));
  await step('let go and next, unanswered, blank note', [''], () => drawer(ITEMS[0]).querySelector('#letGoNext').onclick());
  await step('let go and next, unanswered, with a note', ['a duplicate of a paid claim'], () => drawer(ITEMS[0]).querySelector('#letGoNext').onclick());
  await step('let go and next, a denial, with a reason', ['billed to the patient'], () => drawer(ITEMS[3]).querySelector('#letGoNext').onclick());
  await step('let go and next, a denial, blank reason', [''], () => drawer(ITEMS[3]).querySelector('#letGoNext').onclick());
  await step('texts', [], async () => ({ intro: vm.runInContext('DESKS.denials.intro', ctx), help: vm.runInContext('({NO_ANSWER: SIGNAL_HELP.NO_ANSWER, REJECTED_PRE_PAYER: SIGNAL_HELP.REJECTED_PRE_PAYER})', ctx) }));
  // what an item's panel says is needed: a claim rejected on the way, or one EMA shows still in its print batch, never sent
  const need = ev => (ctx.whatToDoHtml(Object.assign(item('c2', 'REJECTED_PRE_PAYER', 'correct_and_resubmit'), { evidence: ev }), null).match(/You need: ([^<]*)</) || [])[1];
  await step('you need', [], async () => ({ rejected: need({}), unsent: need({ not_sent: true }) }));
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
"""


@pytest.fixture(scope="module")
def page_steps(tmp_path_factory):
    import shutil
    import subprocess
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    script = tmp_path_factory.mktemp("page") / "let_go_page.js"
    script.write_text(LET_GO_PAGE_JS, encoding="utf-8")
    res = subprocess.run(["node", str(script), os.path.join(os.path.dirname(insights.__file__), "static")], capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, res.stderr[-3000:]
    return {s["name"]: s for s in json.loads(res.stdout)}


def test_claims_no_payer_answered_are_let_go_one_at_a_time_with_a_note(page_steps):
    """A claim the payer never answered, or one rejected before reaching it, is money that may not have been asked for
    yet: it is never hidden for a payer, and it is let go one at a time, with a note (docs/OPERATIONS.md). The list's Let
    go leaves these claims out and the bar says so. Let go or Lost in the item's own panel asks why and sends the answer
    as the note; without one nothing changes. Every other item, and every other button, works as before."""
    s = page_steps
    # a list's change carries the status each ticked row showed, and a panel's walk the status it showed (hotfix 2, H3)
    bulk = lambda ids, status, **kw: [{"path": "/api/findings/bulk-status", "body": dict({"ids": ids, "status": status, "seen": {i: "open" for i in ids}}, **kw)}]  # noqa: E731
    one = lambda fid, **body: [{"path": f"/api/findings/{fid}/status", "body": body}]                                    # noqa: E731
    left_out = "Let go leaves out 2 claims with no answer from the payer or rejected before the payer. Open each one to let it go, with a note."
    # each row carries its reason; with such claims ticked the bar says what Let go will leave out
    assert s["rows"]["got"] == ["NO_ANSWER", "REJECTED_PRE_PAYER", "DENIED_MEDICAL_NECESSITY", "DENIED_AUTH"]
    assert s["bar, all four ticked"]["got"] == {"n": "4 selected", "line": left_out}
    assert s["bar, the two denials ticked"]["got"] == {"n": "2 selected", "line": ""}
    # the list's Let go: the two denials go, the two unanswered claims stay, and the question says so; one reason, required,
    # goes with all of them (review C1: money is let go only by a person with a reason)
    why = "\n\nWhy (kept in each item's history; required):"
    mixed = s["bulk let go, all four ticked"]
    assert mixed["sent"] == bulk(["d1", "d2"], "dismissed", notes="not ours to chase")
    assert mixed["asked"] == ["prompt: Let go of 2 items? They leave the list and count as let go. " + left_out + why]
    only = s["bulk let go, the two unanswered ticked"]
    assert only["sent"] == [] and only["asked"] == ["alert: Nothing let go. " + left_out]
    plain = s["bulk let go, the two denials ticked"]
    assert plain["sent"] == bulk(["d1", "d2"], "dismissed", notes="the payer was right")
    assert plain["asked"] == ["prompt: Let go of 2 items? They leave the list and count as let go." + why]
    blank = s["bulk let go, the two denials ticked, no reason"]
    assert blank["sent"] == [] and blank["asked"][1:] == ["alert: Not changed. Letting items go needs a reason."]
    assert s["bulk let go, the two denials ticked, cancelled"]["sent"] == []
    assert s["bulk mark started, all four ticked"]["sent"] == bulk(["c1", "c2", "d1", "d2"], "in_progress")             # nothing else leaves them out
    # the item's own panel: Let go and Lost ask why, and a blank answer changes nothing
    for name in ("panel let go, unanswered, cancelled", "panel let go, unanswered, blank note", "panel lost, rejected, blank note",
                 "let go and next, unanswered, blank note"):
        assert s[name]["sent"] == [] and not s[name].get("got"), name
        assert s[name]["asked"][0].startswith("prompt: ") and "Why (kept in the item's history):" in s[name]["asked"][0], name
    for name in ("panel let go, unanswered, blank note", "panel lost, rejected, blank note", "let go and next, unanswered, blank note"):
        assert s[name]["asked"][1:] == ["alert: Not changed. A claim with no answer from a payer needs a note that says why."], name
    noted = s["panel let go, unanswered, with a note"]
    assert noted["got"] is True and noted["sent"] == one("c1", status="dismissed", notes="no record at the payer, past its limit")
    assert len(noted["asked"]) == 1 and noted["asked"][0].startswith("prompt: Let go of this claim?")                   # the one question
    assert s["panel let go, rejected, with a note"]["sent"] == one("c2", status="dismissed", notes="billed to the patient")
    lost = s["panel lost, unanswered, with a note"]
    assert lost["sent"] == one("c1", status="lost", notes="proof of timely filing refused") and lost["asked"][0].startswith("prompt: Mark this claim lost?")
    assert s["let go and next, unanswered, with a note"]["sent"] == one("c1", status="dismissed", notes="a duplicate of a paid claim", seen_status="open")
    assert s["panel sent, unanswered"]["sent"] == one("c1", status="appealed") and s["panel sent, unanswered"]["asked"] == []
    # any other item: Let go and Lost ask why too, and a blank or cancelled answer changes nothing (review C1)
    ask = ["prompt: Let go of this item? It leaves the list and counts as let go.\n\nWhy (kept in the item's history; required):"]
    nope = "alert: Not changed. Letting an item go, or marking it lost, needs a reason."
    got = s["panel let go, a denial, with a reason"]
    assert got["sent"] == one("d2", status="dismissed", notes="the payer was right") and got["asked"] == ask
    assert s["panel let go, a denial, cancelled"]["sent"] == [] and s["panel let go, a denial, cancelled"]["got"] is False
    assert s["panel let go, a denial, blank reason"]["sent"] == [] and s["panel let go, a denial, blank reason"]["asked"][1:] == [nope]
    assert s["panel lost, a denial, blank note"]["sent"] == [] and s["panel lost, a denial, blank note"]["asked"][1:] == [nope]
    assert s["panel lost, a denial, blank note"]["asked"][0].startswith("prompt: Mark this item lost? It leaves the list.")
    assert s["panel lost, a denial, with a reason"]["sent"] == one("d2", status="lost", notes="upheld on appeal")
    nxt = s["let go and next, a denial, with a reason"]
    assert nxt["sent"] == one("d2", status="dismissed", notes="billed to the patient", seen_status="open") and nxt["asked"] == ask
    assert s["let go and next, a denial, blank reason"]["sent"] == [] and s["let go and next, a denial, blank reason"]["asked"][1:] == [nope]
    # the page draws what the stand-in assumed: the bar holds the line, and the panel's status buttons pass the item's reason
    static = os.path.join(os.path.dirname(insights.__file__), "static")
    desk = open(os.path.join(static, "desk.js"), encoding="utf-8").read()
    page = open(os.path.join(static, "index.html"), encoding="utf-8").read()
    assert '<span id="bulkLeftOut" class="small muted" role="status"></span></div>' in desk
    assert "await setFindingStatus(id, b.dataset.st, f.shortfall_cents, f.signal, f.status, f.charge_cents)" in page


def test_appeal_by_fax_says_a_payer_to_chase_is_not_settled_by_a_letter(page_steps):
    s = page_steps
    none = s["fax, an unanswered claim ticked"]
    assert none["sent"] == [] and len(none["asked"]) == 1 and none["asked"][0].startswith("alert: None of the ticked items")
    assert "the payer has to be chased first" in none["asked"][0]
    some = s["fax, an unanswered claim and a denial ticked"]
    assert some["sent"] == [{"fax": ["d1"]}] and "the payer to chase first" in some["asked"][0] and some["asked"][0].endswith("Fax the other 1?")


def test_the_note_given_for_a_claim_let_go_is_kept_in_its_history(tmp_path, monkeypatch):
    """What the page sends for a claim no payer answered (the status and the note, to the item's own address) is kept:
    the note shows on the item and in its history, and the claim leaves the chase count."""
    import sqlite3

    from remitwatch import api
    tc, auth = _client(tmp_path, monkeypatch)
    con = sqlite3.connect(api.DB_PATH)
    for fid, signal, action in (("na1", "NO_ANSWER", "review"), ("rj1", "REJECTED_PRE_PAYER", "correct_and_resubmit")):
        con.execute("""INSERT INTO findings (finding_id, signal, category, payer_group, shortfall_cents, status, stale, severity, actionable, title,
                       appeal_deadline, patient_control_number)
                       VALUES (?, ?, 'denial', 'Some plan', 11760, 'open', 0, 'medium', ?, 'no answer', date('now','+200 days'), ?)""",
                    (fid, signal, action, "CB0000900C017" if fid == "na1" else "CB0000901C017"))
    con.commit()
    con.close()
    xhr = {"X-Requested-With": "remitwatch"}
    assert tc.get("/api/brief", auth=auth).json()["no_answer"]["chase"]["n"] == 1
    listed = tc.get("/api/findings", params={"category": MONEY, "kind": "chase", "live": 1, "status": "open,in_progress"}, auth=auth).json()["items"]
    assert [(r["finding_id"], r["signal"]) for r in listed] == [("na1", "NO_ANSWER")]                # the row's reason reaches the page
    for fid, note in (("na1", "no record at the payer, past its limit"), ("rj1", "billed to the patient")):
        r = tc.post(f"/api/findings/{fid}/status", json={"status": "dismissed", "notes": note}, headers=xhr, auth=auth)
        assert r.status_code == 200, r.text
        d = tc.get(f"/api/findings/{fid}", auth=auth).json()
        assert d["finding"]["status"] == "dismissed" and d["finding"]["notes"] == note
        assert [(e["to_status"], e["note"], e["actor"]) for e in d["events"]] == [("dismissed", note, "staff")]
    assert tc.get("/api/brief", auth=auth).json()["no_answer"]["chase"]["n"] == 0


def test_the_pages_say_what_the_two_reasons_with_no_payer_answer_are(page_steps):
    """Unanswered and rejected claims sit on the Denials page though no payer denied them: its heading says so, the
    Overview card that leaves them out says so, and both reasons have their help text (the Signals table, the page's
    'What each label means' and an item's Technical details read it)."""
    texts = page_steps["texts"]["got"]
    assert "claims the payer never answered" in texts["intro"] and "rejected before they reached it" in texts["intro"]
    assert texts["help"]["NO_ANSWER"].startswith("EMA shows the claim sent and unpaid") and "portal" in texts["help"]["NO_ANSWER"]
    # the date an unanswered claim shows can be the payer's appeal window; sending it again goes by the first-filing limit
    assert "first-filing limit" in texts["help"]["NO_ANSWER"]
    assert texts["help"]["REJECTED_PRE_PAYER"].startswith("EMA shows the claim rejected before it reached the payer")
    for text in (texts["intro"], *texts["help"].values()):
        assert "—" not in text
    page = open(os.path.join(os.path.dirname(insights.__file__), "static", "index.html"), encoding="utf-8").read()
    card = next(line for line in page.splitlines() if "kpi('Denied, never paid'" in line)
    assert "sigRows(['NO_ANSWER','REJECTED_PRE_PAYER'])" in card and "not claims unanswered or rejected before the payer" in card


# --------------------------------------------------------------------------- the visit note a person picks, and the item's history
# The page's own code (index.html's helpers and the Claim details rows, features.js, today.js, desk.js, claim.js, faxes.js) in
# node, on a stand-in for the page whose elements are read back from the HTML the code writes (tags, ids, classes and data-
# attributes), so the buttons it draws can be pressed. The answers of the server are canned. It prints what each step drew
# and sent. Usage: node <this file> <static dir>. Every patient, ID and payer is invented.
NOTE_PAGE_JS = r"""
const fs = require('fs'), vm = require('vm'), path = require('path');
const read = f => fs.readFileSync(path.join(process.argv[2], f), 'utf8');
const html = read('index.html');
const grab = (src, re) => { const m = src.match(re); if (!m) throw new Error('not found: ' + re); return m[0]; };
const helpers = [/const money = [^\n]*\n/, /const money2 = [^\n]*\n/, /const esc = [^\n]*\n/, /const palette = [^\n]*\n/, /function kpi\([^\n]*\n/,
  /function claimWhoRows\(f, cl, phi\)\{[\s\S]*?\n\}\n/].map(re => grab(html, re)).join('\n');
const unesc = s => String(s).replace(/&(amp|lt|gt|quot|#39);/g, (m, k) => ({ amp: '&', lt: '<', gt: '>', quot: '"', '#39': "'" }[k]));
const camel = k => k.replace(/-([a-z])/g, (m, c) => c.toUpperCase());
// an element: what the code sets (innerHTML, textContent, className, onclick, disabled) and, from the HTML it is given, its
// elements as children, so querySelector finds them
class El {
  constructor(tag, o = {}, parent = null) {
    Object.assign(this, { tag, id: o.id || '', cls: o.cls || [], dataset: o.data || {}, disabled: !!o.disabled, checked: !!o.checked, value: o.value, parent, kids: [], _html: '', _text: '' });
    if (parent) parent.kids.push(this);
  }
  set innerHTML(h) {
    this._html = String(h); this._text = ''; this.kids = [];
    for (const m of this._html.matchAll(/<([a-zA-Z][\w-]*)((?:\s+[\w-]+(?:="[^"]*")?)*)\s*\/?>/g)) {
      const a = m[2], attr = n => { const x = a.match(new RegExp('\\s' + n + '="([^"]*)"')); return x ? unesc(x[1]) : undefined; };
      const data = {}; for (const d of a.matchAll(/\sdata-([\w-]+)(?:="([^"]*)")?/g)) data[camel(d[1])] = d[2] === undefined ? '' : unesc(d[2]);
      new El(m[1].toLowerCase(), { id: attr('id') || '', cls: (attr('class') || '').split(/\s+/).filter(Boolean), data, disabled: /\sdisabled\b/.test(a), checked: /\schecked\b/.test(a) }, this);
    }
  }
  get innerHTML() { return this._html; }
  set textContent(t) { this._text = String(t); this._html = ''; this.kids = []; }
  get textContent() { return this._text || unesc(this._html.replace(/<[^>]*>/g, '')); }
  set className(c) { this.cls = String(c).split(/\s+/).filter(Boolean); }
  get className() { return this.cls.join(' '); }
  matches(sel) {
    const tag = (sel.match(/^[a-z]+/) || [''])[0], id = (sel.match(/#([\w-]+)/) || [])[1], cls = [...sel.matchAll(/\.([\w-]+)/g)].map(m => m[1]);
    const data = [...sel.matchAll(/\[data-([\w-]+)(?:="([^"]*)")?\]/g)];
    return (!tag || tag === this.tag) && (!id || id === this.id) && cls.every(c => this.cls.includes(c))
      && data.every(([, k, val]) => camel(k) in this.dataset && (val === undefined || this.dataset[camel(k)] === val));
  }
  all() { return this.kids.flatMap(k => [k, ...k.all()]); }
  querySelectorAll(sel) { return this.all().filter(e => e.matches(sel)); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  closest(sel) { for (let e = this; e; e = e.parent) if (e.matches(sel)) return e; return null; }
}
const make = h => { const e = new El('div'); e.innerHTML = h; return e; };
// the server: canned answers by the start of the address, in order; what was sent is kept
const sent = [], replies = {};
const ctx = { console, Promise, setTimeout: (fn) => fn(), state: { meta: { modmed: true, show_phi: true }, brief: null, filt: {}, charts: {} },
  alert: () => {}, confirm: () => true, prompt: () => null, navigator: { clipboard: { writeText: () => {} } }, CSS: { escape: s => s }, location: {},
  api: async (p, o) => {
    const body = o && o.body ? JSON.parse(o.body) : null; sent.push(body ? { path: p, body } : { path: p });
    const k = Object.keys(replies).find(x => p.startsWith(x)), r = k && replies[k].length ? replies[k].shift() : undefined;
    if (r instanceof Error) throw r;
    return r;
  },
  render: async () => {}, openDetail: id => { sent.push({ open: id }); }, closeDetail: () => {}, chart: () => {}, goWork: () => {} };
vm.createContext(ctx);
for (const src of [helpers, read('features.js'), read('today.js'), read('desk.js'), read('claim.js'), read('faxes.js')]) vm.runInContext(src, ctx);
const out = [];
const step = async (name, canned, fn) => { sent.length = 0; for (const k of Object.keys(replies)) delete replies[k]; Object.assign(replies, canned); out.push({ name, got: await fn(), sent: sent.slice() }); };
// the note builder in an item's panel, as drawerExtras draws it: the EMA button, its message, the review box, the result
const panel = () => make('<button id="noteEma"></button><div id="emaMsg" class="small muted"></div><input type="checkbox" id="noteAi" checked><div id="noteOut"></div>');
const shown = d => ({ msg: d.querySelector('#emaMsg').innerHTML, out: d.querySelector('#noteOut').innerHTML,
  picks: d.querySelectorAll('[data-ema-pick]').map(b => b.dataset.emaPick), button_free: !d.querySelector('#noteEma').disabled });
const PACKET = { packet_id: 'pk1', letter: 'To the plan: please reprocess.', analysis: { recommendation: 'appeal', mode: 'template' } };
const who = (name, birth_date, ids) => ({ name, birth_date, ids });
const cand = (id, n, o = {}) => Object.assign({ id, date: '2026-09-14', written: '2026-09-14', type: 'Progress note', author: 'Clinician A', tie: 'encounter',
  status: 'final', patient_n: n, identified_by: ['name'], pick_only: false }, o);
(async () => {
  // more than one patient fits the claim, names shown: the notes in two groups, each saying who the patient is
  const twins = [cand('doc-a', 1, { patient: who('Sample Pat', '1961-03-04', ['P-1001']), identified_by: ['EMA patient ID', 'name'] }),
    cand('doc-b', 2, { patient: who('Sample Pat', '1990-07-08', ['P-2002', 'M-77']), identified_by: ['name'], written: '2026-09-15' }),
    cand('doc-c', 2, { patient: who('Sample Pat', '1990-07-08', ['P-2002', 'M-77']), identified_by: ['name'], type: 'Letter', date: '2026-09-16', written: '2026-09-16', tie: 'after', pick_only: true })];
  const d1 = panel();
  await step('two patients, names shown', { '/api/appeals/from_ema': [{ status: 'ambiguous', found_by: 'name', candidates: twins,
    reason: 'More than one patient who fits the claim has a note that day. Check who each is, then pick the note.' }] }, async () => { await ctx.noteFromEma('f1', d1); return shown(d1); });
  await step('two patients, the second patient picked', { '/api/appeals/from_ema': [{ status: 'built', found_by: 'name', identified_by: ['name', 'a person'], packet: PACKET,
    note: Object.assign(cand('doc-b', 2, { patient: twins[1].patient }), { identified_by: ['name', 'a person'] }), others: [twins[2]] }] },
    async () => { await d1.querySelector('[data-ema-pick="doc-b"]').onclick(); return shown(d1); });
  // two patients fit and names are switched off: nothing to pick from, the note is attached by hand
  const d2 = panel();
  await step('two patients, names off', { '/api/appeals/from_ema': [{ status: 'ambiguous', found_by: 'name', candidates: [],
    reason: 'More than one patient in EMA fits this claim, and names are switched off here, so they cannot be told apart. Save the visit note from EMA and attach it to the item.' }] },
    async () => { await ctx.noteFromEma('f2', d2); return shown(d2); });
  // one patient, only the name agrees, names shown: one group, with who the patient is; a later note that names no visit is marked
  const d3 = panel();
  await step('one patient, only the name agrees', { '/api/appeals/from_ema': [{ status: 'ambiguous', found_by: 'name', identified_by: ['name'],
    candidates: [cand('doc-d', 1, { patient: who('Sample Pat', '1961-03-04', ['P-1001']) }), cand('doc-e', 1, { patient: who('Sample Pat', '1961-03-04', ['P-1001']), type: 'Progress note', date: '2026-09-17', written: '2026-09-17', tie: 'after', pick_only: true })],
    reason: 'Only the name agrees with the claim. A note goes out by itself only when two identifiers agree. Check that the note is this patient\'s, then pick it.' }] },
    async () => { await ctx.noteFromEma('f3', d3); return shown(d3); });
  // built by itself: the message says what identified the patient; the patient's other documents stay one click away
  const d4 = panel();
  await step('built from EMA', { '/api/appeals/from_ema': [{ status: 'built', found_by: 'EMA patient ID', identified_by: ['EMA patient ID', 'name'], packet: PACKET,
    note: cand('doc-f', 1, { patient: who('Sample Pat', '1961-03-04', ['P-1001']), identified_by: ['EMA patient ID', 'name'] }),
    others: [cand('doc-g', 1, { patient: who('Sample Pat', '1961-03-04', ['P-1001']), type: 'Consent form', pick_only: true, identified_by: ['EMA patient ID', 'name'] })] }] },
    async () => { await ctx.noteFromEma('f4', d4); return shown(d4); });
  const d5 = panel();
  await step('built from a note attached to the item', { '/api/appeals/from_ema': [{ status: 'built', source: 'upload', found_by: 'EMA patient ID', identified_by: ['a person'], packet: PACKET,
    note: cand('upload-n1', 1, { type: 'Visit note', author: 'uploaded by a biller', identified_by: ['a person'], uploaded_name: 'Visit note.pdf' }), others: [] }] },
    async () => { await ctx.noteFromEma('f5', d5); return shown(d5); });
  const d6 = panel();
  await step('built, an older server', { '/api/appeals/from_ema': [{ status: 'built', found_by: 'name', packet: PACKET, note: { id: 'doc-h', type: 'Progress note', date: '2026-09-14' } }] },
    async () => { await ctx.noteFromEma('f6', d6); return shown(d6); });
  const d7 = panel();
  await step('no note in EMA', { '/api/appeals/from_ema': [{ status: 'not_found', candidates: [], reason: 'EMA has no signed note for 2026-09-14 for this patient.' }] },
    async () => { await ctx.noteFromEma('f7', d7); return shown(d7); });
  const d8 = panel();
  await step('EMA did not answer', { '/api/appeals/from_ema': [new Error('EMA did not answer in time.')] }, async () => { await ctx.noteFromEma('f8', d8); return shown(d8); });
  // the item's history and Progress's Activity: lines that change no status say what happened
  const events = [{ at: '2026-11-20T10:00:00', kind: 'note_lookup', from_status: 'open', to_status: 'open', actor: 'packets', note: 'Build packets from EMA: Only the name agrees with the claim.', finding_id: 'h1' },
    { at: '2026-11-20T09:00:00', kind: 'packet', from_status: 'open', to_status: 'open', actor: 'packets', note: 'An appeal packet was built for it.', finding_id: 'h1' },
    { at: '2026-11-19T09:00:00', kind: 'fax', from_status: 'appealed', to_status: 'appealed', actor: 'fax', note: 'Second request faxed.', finding_id: 'h1' },
    { at: '2026-11-18T09:00:00', kind: 'manual', from_status: 'open', to_status: 'appealed', actor: 'biller', note: '', finding_id: 'h1' },
    { at: '2026-11-17T09:00:00', kind: 'paid_on_later_remit', from_status: 'appealed', to_status: 'resolved', actor: 'system', note: '', finding_id: 'h2' }];
  const rows = h => [...h.matchAll(/<tr[^>]*>([\s\S]*?)<\/tr>/g)].map(m => [...m[1].matchAll(/<td[^>]*>([\s\S]*?)<\/td>/g)].map(c => unesc(c[1].replace(/<[^>]*>/g, '')).trim()));
  await step('history in the panel', {}, async () => rows(ctx.drawerExtras({ signal: 'DENIED_AUTH', status: 'open', evidence: {} }, { events }).facts).filter(r => r.length === 5).map(r => r[1]));
  const prog = { pipeline: [], live: { n: 0, cents: 0 }, expired: { n: 0, cents: 0 }, by_payer: [], events, recovered_by_month: [], cohorts: [], downcode_trend: [], visit_lines: [] };
  const pv = new El('main');
  await step('activity on Progress', { '/api/progress': [prog] }, async () => { await ctx.progressTab(pv); return rows(pv.innerHTML).filter(r => r.length === 6).map(r => r[1]); });
  // Claim details: a claim known only from EMA's rows has no payer claim number, and no Claim row
  const cell = h => rows('<table>' + h + '</table>').map(r => r.join(': '));
  await step('claim details', {}, async () => ({
    ema_names: cell(ctx.claimWhoRows({ patient_control_number: 'CB0000900C017' }, { patient: 'Sample Pat', member_id: 'M00000009', from_ema: true }, true)),
    ema_member_only: cell(ctx.claimWhoRows({ patient_control_number: 'CB0000900C017' }, { patient: '', member_id: 'M00000009', from_ema: true }, true)),
    ema_initials: cell(ctx.claimWhoRows({ patient_control_number: 'CB0000900C017' }, { patient: 'S.P.', from_ema: true }, false)),
    remit_names: cell(ctx.claimWhoRows({ patient_control_number: 'CB0000500C017' }, { patient: 'Sample Pat', member_id: 'M00000001', insured: 'Sample Lee', payer_claim_control_number: 'PCN-123' }, true)),
    remit_no_number: cell(ctx.claimWhoRows({ patient_control_number: 'CB0000500C017' }, { patient: 'S.P.', payer_claim_control_number: '' }, false)) }));
  await step('claim view title line', {}, async () => ({
    member_only: unesc(ctx.claimSub({ patient: '', member_id: 'M00000009', remits: [], claim_ids: ['CB0000900C017'] })),
    names: unesc(ctx.claimSub({ patient: 'Sample Pat', member_id: 'M00000001', remits: [{}, {}], claim_ids: ['CB0000500C017', 'CB0000500C024'] })),
    initials: unesc(ctx.claimSub({ patient: 'S.P.', remits: [{}], claim_ids: ['CB0000500C017'] })) }));
  // Appeal by fax: items held because a person has to pick or attach the note
  const held = (finding_id, reason, candidates) => ({ finding_id, status: 'held', code: 'ambiguous', reason, candidates, payer_group: 'Plan A', cents: 11760 });
  const box = new El('div');
  await step('fax summary', {}, async () => { ctx.faxSummary(box, [held('a1', 'Only the name agrees with the claim. Check that the note is this patient\'s, then pick it.', [cand('doc-a', 1)]),
    held('a2', 'More than one patient in EMA fits this claim, and names are switched off here, so they cannot be told apart. Save the visit note from EMA and attach it to the item.', [])], 0, {});
    return box.textContent.replace(/\s+/g, ' '); });
  const fx = () => make('<button id="fxOne"></button><div id="fxOneMsg" class="small"></div>');
  const f9 = fx();
  await step('fax from the panel, a note to pick', { '/api/appeals/send': [held('a1', 'Only the name agrees with the claim. Check that the note is this patient\'s, then pick it.', [cand('doc-a', 1)])] },
    async () => { ctx.bindFaxDrawer('a1', f9); await f9.querySelector('#fxOne').onclick(); return f9.querySelector('#fxOneMsg').textContent; });
  const f10 = fx();
  await step('fax from the panel, a note to attach', { '/api/appeals/send': [held('a2', 'More than one patient in EMA fits this claim, and names are switched off here, so they cannot be told apart. Save the visit note from EMA and attach it to the item.', [])] },
    async () => { ctx.bindFaxDrawer('a2', f10); await f10.querySelector('#fxOne').onclick(); return f10.querySelector('#fxOneMsg').textContent; });
  // Build packets from EMA: what the batch could not build says why
  const bt = new El('button'), bx = new El('span');
  await step('build packets from EMA', { '/api/appeals/ema_queue': [{ items: [{ finding_id: 'q1' }, { finding_id: 'q2' }, { finding_id: 'q3' }], since: '2026-09-01' }],
    '/api/appeals/from_ema': [{ status: 'built' }, { status: 'ambiguous', reason: 'Only the name agrees with the claim.', candidates: [] }, { status: 'not_found', reason: 'EMA has no signed note.' }] },
    async () => { await ctx.emaBatch(bt, bx); return bx.textContent; });
  // a whole claim EMA shows partly paid: the panel names the codes paid and the codes not paid; one line paid keeps its words
  const said = ev => unesc(ctx.drawerExtras({ signal: 'CLAIM_DENIED_UNPAID', status: 'open', evidence: ev }, {}).notes.replace(/<[^>]*>/g, ''));
  await step('EMA shows part of a claim paid', {}, async () => ({
    part: said({ ema_shows_paid: { paid_cents: 11760, date: '2026-11-02', source: 'Encounter & Charge Lines.xlsx', codes_paid: ['99214'], codes_not_paid: ['11102', '11103'] } }),
    line: said({ ema_shows_paid: { paid_cents: 11760, source: 'Encounter & Charge Lines.xlsx' } }) }));
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
"""


@pytest.fixture(scope="module")
def note_steps(tmp_path_factory):
    import shutil
    import subprocess
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    script = tmp_path_factory.mktemp("page") / "note_page.js"
    script.write_text(NOTE_PAGE_JS, encoding="utf-8")
    res = subprocess.run(["node", str(script), os.path.join(os.path.dirname(insights.__file__), "static")], capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, res.stderr[-3000:]
    return {s["name"]: s for s in json.loads(res.stdout)}


def _text(html: str) -> str:
    """What a person reads: inline tags dropped, every other tag a space."""
    import html as htmllib
    import re
    inline = re.sub(r"</?(?:b|i|em|strong|span|a|sup)\b[^>]*>", "", html)
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", inline))).strip()


def test_two_patients_notes_come_in_groups_that_say_who_each_patient_is(note_steps):
    """When more than one patient fits the claim and names are shown, the notes to pick from come in groups, Patient 1 and
    Patient 2, each over its own notes with the patient's name, birth date and IDs and what of the claim agrees with them,
    so two patients of one name can be told apart. A document that may not be this visit's note is marked. A pick builds
    from that note, and the message says the note was chosen by a person."""
    s = note_steps["two patients, names shown"]["got"]
    text = _text(s["out"])
    assert text.startswith("More than one patient who fits the claim has a note that day.")
    one = text.index("Patient 1: Sample Pat, born 1961-03-04, ID P-1001 · the claim's EMA patient ID and name agree")
    two = text.index("Patient 2: Sample Pat, born 1990-07-08, IDs P-2002, M-77 · the claim's name agrees")
    assert one < two
    assert s["picks"] == ["doc-a", "doc-b", "doc-c"]
    # each note sits under its own patient: doc-a under Patient 1, doc-b and doc-c under Patient 2
    out = s["out"]
    assert out.index('data-ema-pick="doc-a"') < out.index("Patient 2") < out.index('data-ema-pick="doc-b"') < out.index('data-ema-pick="doc-c"')
    # the later letter that names no visit says so, the notes of the day itself do not
    assert text.count("check first: may not be this visit's note") == 1
    assert out.index("check first") > out.index('data-ema-pick="doc-b"')
    # a note of the visit's date written later says when it was written; one written the day it is dated does not
    assert text.count("written ") == 1 and "2026-09-14 written 2026-09-15 Progress note" in text
    picked = note_steps["two patients, the second patient picked"]
    assert picked["sent"] == [{"path": "/api/appeals/from_ema", "body": {"finding_id": "f1", "use_ai": True, "document_id": "doc-b"}}]
    msg = _text(picked["got"]["msg"])
    assert msg.startswith("Built from EMA: Progress note of 2026-09-14 by Clinician A (patient identified by name, and the note chosen by a person).")
    assert "The patient's other documents:" in msg and "check first: may not be this visit's note" in msg and "Patient" not in msg.split("documents:")[1]
    assert picked["got"]["picks"] == ["doc-c"] and "Download the packet" in picked["got"]["out"]


def test_with_names_off_two_patients_show_only_the_reason_and_no_table(note_steps):
    """Two patients fit the claim and names are switched off: the server gives no notes to pick from. The page shows the
    reason alone (it says to attach the note by hand), with no empty table and no button."""
    s = note_steps["two patients, names off"]["got"]
    assert _text(s["out"]) == ("More than one patient in EMA fits this claim, and names are switched off here, so they cannot be told apart. "
                               "Save the visit note from EMA and attach it to the item.")
    assert "<table" not in s["out"] and s["picks"] == [] and s["button_free"]


def test_one_patient_found_by_name_only_shows_who_and_marks_a_later_note(note_steps):
    s = note_steps["one patient, only the name agrees"]["got"]
    text = _text(s["out"])
    assert text.startswith("Only the name agrees with the claim.")
    assert "Patient: Sample Pat, born 1961-03-04, ID P-1001" in text and "Patient 1" not in text and "agree" not in text.split("ID P-1001")[1].split("2026-09-14")[0]
    assert s["picks"] == ["doc-d", "doc-e"] and text.count("check first: may not be this visit's note") == 1


def test_the_built_message_says_what_identified_the_patient(note_steps):
    """A packet built says what tied the note to the claim's patient (identified_by), not only how the patient was found."""
    built = note_steps["built from EMA"]["got"]
    msg = _text(built["msg"])
    assert msg.startswith("Built from EMA: Progress note of 2026-09-14 by Clinician A (patient identified by EMA patient ID and name).")
    assert "found by" not in msg
    # the patient's other documents: one patient, so no group line over them; a consent form is marked
    assert "The patient's other documents:" in msg and "Patient:" not in msg and "check first: may not be this visit's note" in msg
    assert built["picks"] == ["doc-g"]
    upload = _text(note_steps["built from a note attached to the item"]["got"]["msg"])
    assert upload == "Built from the note uploaded from EMA (Visit note.pdf): Visit note of 2026-09-14 (the note chosen by a person)."
    # an older server's answer has no identified_by: what found the patient is said as before
    assert _text(note_steps["built, an older server"]["got"]["msg"]) == "Built from EMA: Progress note of 2026-09-14 (patient found by name)."
    # no note: the attach box is above the message, and an error says what failed
    assert _text(note_steps["no note in EMA"]["got"]["out"]) == "EMA has no signed note for 2026-09-14 for this patient. Attach it above instead."
    assert _text(note_steps["EMA did not answer"]["got"]["out"]) == "Could not get the note: EMA did not answer in time."


def test_history_lines_that_change_no_status_say_what_happened(note_steps):
    """"Build packets from EMA" writes a line when it found no note or a person has to choose (note_lookup), and one when it
    built a packet (packet). The item's history and Progress's Activity name them as they name a fax, instead of
    "open → open"."""
    want = ["Note lookup", "Packet built", "Fax", "open → appealed", "appealed → resolved"]
    assert note_steps["history in the panel"]["got"] == want
    assert note_steps["activity on Progress"]["got"] == want[:4] + ["appealed → resolved (paid on a later remit)"]


def test_claim_details_of_a_claim_no_payer_answered_have_no_empty_payer_claim_number(note_steps):
    """A claim known only from EMA's rows (from_ema: no remittance) shows who it is about and no Claim row with an empty
    payer claim number; the line above the fold gives its EMA claim ID. A claim with a remittance keeps its Claim row."""
    s = note_steps["claim details"]["got"]
    assert s["ema_names"] == ["Patient: Sample Pat · member ID M00000009"]
    assert s["ema_member_only"] == ["Patient: member ID M00000009"]
    assert s["ema_initials"] == ["Patient: S.P. (initials; names are switched off on this site)"]
    assert s["remit_names"] == ["Patient: Sample Pat · member ID M00000001 · insured Sample Lee", "Claim: EMA claim ID CB0000500C017 · payer claim # PCN-123"]
    assert s["remit_no_number"] == ["Patient: S.P. (initials; names are switched off on this site)", "Claim: EMA claim ID CB0000500C017"]
    t = note_steps["claim view title line"]["got"]
    assert t == {"member_only": "member ID M00000009 · 0 remittances", "names": "Sample Pat · member ID M00000001 · 2 remittances · submissions CB0000500C017, CB0000500C024",
                 "initials": "S.P. · 1 remittance"}
    page = open(os.path.join(os.path.dirname(insights.__file__), "static", "index.html"), encoding="utf-8").read()
    assert "${r.claim ? claimWhoRows(f, cl, showPhi()) : ''}" in page


def test_held_and_batch_notes_say_a_person_picks_or_attaches_the_note(note_steps):
    """An item held because its note needs a person is not only "more than one note that day" any more: only the name
    agrees, two patients fit, or the only document is a later note. The fax summary, the panel and the batch say so."""
    summary = note_steps["fax summary"]["got"]
    assert "The visit note needs a person to pick it or attach it: open each item" in summary and "More than one note that day" not in summary
    pick = note_steps["fax from the panel, a note to pick"]["got"]
    assert pick.endswith('Open "Build the appeal from the visit note" below and pick it there.')
    attach = note_steps["fax from the panel, a note to attach"]["got"]
    assert attach.endswith("Save the visit note from EMA and attach it to the item.") and "pick it there" not in attach
    batch = note_steps["build packets from EMA"]
    assert batch["got"] == ("Done: 1 packet built, 1 whose note a person has to pick or attach (open the item), 1 with no signed note found in EMA. "
                            "Open an item to send its packet.")
    assert [x.get("body", {}).get("finding_id") for x in batch["sent"] if x["path"] == "/api/appeals/from_ema"] == ["q1", "q2", "q3"]


def test_a_claim_ema_shows_partly_paid_names_the_codes_not_paid_in_its_panel(note_steps):
    """A whole-claim item that EMA shows paid for some codes only stays open (detect.py). Its panel, where the status
    buttons are, names the codes paid and the codes not paid, and says to mark it recovered only if the rest was paid too:
    "this line paid" would send the biller to mark the whole claim recovered. One line EMA shows paid keeps its words."""
    got = note_steps["EMA shows part of a claim paid"]["got"]
    assert got["part"] == ("EMA shows part of this claim paid. Insurance paid 99214 ($117.60 on 2026-11-02, Encounter & Charge Lines.xlsx) "
                           "but not 11102, 11103. The item stays open until every code is paid. Check EMA, and mark it recovered only if "
                           "the rest was paid too.")
    assert got["line"].startswith("EMA shows this line paid. $117.60 from insurance (Encounter & Charge Lines.xlsx), but no remittance")


def test_a_claim_still_in_emas_print_batch_is_not_called_a_rejection(page_steps):
    """A claim EMA shows waiting in its print batch, never sent, is a REJECTED_PRE_PAYER item too (detect.py). Its labels,
    the help text, Today's line and its panel say it never reached the payer, and send the biller to the print batch, not
    to a clearinghouse rejection report that does not exist."""
    import html as htmllib
    label, nxt = insights.SIGNAL_LABELS["REJECTED_PRE_PAYER"]
    assert "print batch" in label and label.startswith("Never reached the payer") and "print it" in nxt
    assert insights.SHORT_LABELS["REJECTED_PRE_PAYER"] == "Never reached the payer"
    fix = next(t for t in insights.WORK_TYPES if t["key"] == "fix")
    assert "rejection report" in fix["needs"] and "print batch" in fix["needs"]
    got = {k: htmllib.unescape(v) for k, v in page_steps["you need"]["got"].items()}
    assert got == {"rejected": "EMA and the rejection's reason (on the clearinghouse's rejection report)",
                   "unsent": "EMA's print batch (print and mail the claim, or send it electronically)"}
    assert "print batch" in page_steps["texts"]["got"]["help"]["REJECTED_PRE_PAYER"]
    today = open(os.path.join(os.path.dirname(insights.__file__), "static", "today.js"), encoding="utf-8").read()
    assert "more never reached the payer: rejected, or still in EMA's print batch (under Fix and resubmit)" in today
    for text in (label, nxt, fix["needs"], *got.values()):
        assert "—" not in text
