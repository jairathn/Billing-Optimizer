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
    # the detector gives one of these no filing date: the whole claim denied as filed too late (CO-29), an item to decide
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
    monkeypatch.setattr(api, "USERNAME", "oda")
    monkeypatch.delenv("REMITWATCH_MODE", raising=False)
    return TestClient(api.app), ("oda", PW)


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
    monkeypatch.setattr(api, "USERNAME", "oda")
    monkeypatch.delenv("REMITWATCH_MODE", raising=False)
    from fastapi.testclient import TestClient
    tc, auth = TestClient(api.app), ("oda", PW)
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
    monkeypatch.setattr(api, "USERNAME", "oda")
    monkeypatch.delenv("REMITWATCH_MODE", raising=False)
    tc, auth = TestClient(api.app), ("oda", PW)
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
  await step('bulk let go, all four ticked', [true], async () => { p.tick(all); await p.press('[data-bulk="dismissed"]'); });
  p = page();
  await step('bulk let go, the two unanswered ticked', [true], async () => { p.tick(['c1', 'c2']); await p.press('[data-bulk="dismissed"]'); });
  await step('bulk let go, the two denials ticked', [true], async () => { p.tick(['d1', 'd2']); await p.press('[data-bulk="dismissed"]'); });
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
  await step('panel let go, a denial, confirmed', [true], () => set('d2', 'dismissed', 'DENIED_AUTH'));
  await step('panel let go, a denial, not confirmed', [false], () => set('d2', 'dismissed', 'DENIED_AUTH'));
  await step('panel lost, a denial, blank note', [''], () => set('d2', 'lost', 'DENIED_AUTH'));
  await step('let go and next, unanswered, blank note', [''], () => drawer(ITEMS[0]).querySelector('#letGoNext').onclick());
  await step('let go and next, unanswered, with a note', ['a duplicate of a paid claim'], () => drawer(ITEMS[0]).querySelector('#letGoNext').onclick());
  await step('let go and next, a denial, confirmed', [true], () => drawer(ITEMS[3]).querySelector('#letGoNext').onclick());
  await step('texts', [], async () => ({ intro: vm.runInContext('DESKS.denials.intro', ctx), help: vm.runInContext('({NO_ANSWER: SIGNAL_HELP.NO_ANSWER, REJECTED_PRE_PAYER: SIGNAL_HELP.REJECTED_PRE_PAYER})', ctx) }));
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
    bulk = lambda ids, status: [{"path": "/api/findings/bulk-status", "body": {"ids": ids, "status": status}}]          # noqa: E731
    one = lambda fid, **body: [{"path": f"/api/findings/{fid}/status", "body": body}]                                    # noqa: E731
    left_out = "Let go leaves out 2 claims with no answer from the payer or rejected before the payer. Open each one to let it go, with a note."
    # each row carries its reason; with such claims ticked the bar says what Let go will leave out
    assert s["rows"]["got"] == ["NO_ANSWER", "REJECTED_PRE_PAYER", "DENIED_MEDICAL_NECESSITY", "DENIED_AUTH"]
    assert s["bar, all four ticked"]["got"] == {"n": "4 selected", "line": left_out}
    assert s["bar, the two denials ticked"]["got"] == {"n": "2 selected", "line": ""}
    # the list's Let go: the two denials go, the two unanswered claims stay, and the question says so
    mixed = s["bulk let go, all four ticked"]
    assert mixed["sent"] == bulk(["d1", "d2"], "dismissed")
    assert mixed["asked"] == ["confirm: Let go of 2 items? They leave the list and count as let go. " + left_out]
    only = s["bulk let go, the two unanswered ticked"]
    assert only["sent"] == [] and only["asked"] == ["alert: Nothing let go. " + left_out]
    plain = s["bulk let go, the two denials ticked"]
    assert plain["sent"] == bulk(["d1", "d2"], "dismissed") and plain["asked"] == ["confirm: Let go of 2 items? They leave the list and count as let go."]
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
    assert s["let go and next, unanswered, with a note"]["sent"] == one("c1", status="dismissed", notes="a duplicate of a paid claim")
    assert s["panel sent, unanswered"]["sent"] == one("c1", status="appealed") and s["panel sent, unanswered"]["asked"] == []
    # any other item: the plain confirmation, and Lost keeps its optional note
    confirm = ["confirm: Let go of this item? It leaves the list and counts as let go."]
    assert s["panel let go, a denial, confirmed"]["sent"] == one("d2", status="dismissed") and s["panel let go, a denial, confirmed"]["asked"] == confirm
    assert s["panel let go, a denial, not confirmed"]["sent"] == [] and s["panel let go, a denial, not confirmed"]["got"] is False
    assert s["panel lost, a denial, blank note"]["sent"] == one("d2", status="lost")
    assert s["let go and next, a denial, confirmed"]["sent"] == one("d2", status="dismissed") and s["let go and next, a denial, confirmed"]["asked"] == confirm
    # the page draws what the stand-in assumed: the bar holds the line, and the panel's status buttons pass the item's reason
    static = os.path.join(os.path.dirname(insights.__file__), "static")
    desk = open(os.path.join(static, "desk.js"), encoding="utf-8").read()
    page = open(os.path.join(static, "index.html"), encoding="utf-8").read()
    assert '<span id="bulkLeftOut" class="small muted" role="status"></span></div>' in desk
    assert "await setFindingStatus(id, b.dataset.st, f.shortfall_cents, f.signal)" in page


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
        assert [(e["to_status"], e["note"], e["actor"]) for e in d["events"]] == [("dismissed", note, "oda")]
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
