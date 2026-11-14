"""The category pages (Denials, Bundling & modifiers, Downcoding, Contracts & underpayment, the Worklist): every number
they show opens a list of exactly that many, and their tiles add up to Today's numbers. Everything here is invented."""
from __future__ import annotations

import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch import insights  # noqa: E402
from remitwatch.detect import Detector  # noqa: E402
from tests.test_activity import PW  # noqa: E402

MONEY = "denial,bundling,downcoding,underpayment"
CATEGORIES = ("denial", "bundling", "downcoding", "underpayment")


def _client(tmp_path, monkeypatch):
    """A store with a routine-rate pattern whose claims close ten days ago, in three days and in five months; whole
    claims denied for five reasons; denied, bundled and underpaid lines; an item to decide, one sent long ago and one
    whose window closed last week."""
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
    ids = [r[0] for r in st.query("SELECT finding_id FROM findings WHERE category IN ('denial','bundling','downcoding','underpayment') "
                                  "AND signal != 'SECOND_RATE' ORDER BY finding_id")]
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
    assert b["decide"]["n"] == 1 and b["missed"]["n"] >= 2 and b["follow_ups"]["n"] == 1
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
    assert keys[:8] == ["today", "denials", "bundling", "downcoding", "contracts", "fees", "worklist", "wins"], keys
    assert "appeals:'worklist'" in page and '<script src="/static/desk.js"></script>' in page
    desk = open(os.path.join(static, "desk.js"), encoding="utf-8").read()
    for key in ("denials", "bundling", "downcoding", "contracts", "worklist"):
        assert re.search(rf"\b{key}:\s+\{{cat:", desk), key
