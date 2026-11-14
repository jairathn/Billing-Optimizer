"""The home screen's data (remitwatch.insights): open work by the kind of work it takes, levers, and data health.
Everything here is invented."""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch import insights  # noqa: E402
from remitwatch.detect import Detector  # noqa: E402
from tests.test_activity import PW  # noqa: E402
from tests.test_features import build_store, claim, svc  # noqa: E402


def store_with_work(tmp_path):
    fix = [claim(i, svc("99213", "150.00", "0.00", None, "CAS*CO*16*150.00~LQ*HE*N822~", dos="20261001"), dos="20261001") for i in range(1, 4)]
    auth = [claim(i, svc("17311", "900.00", "0.00", None, "CAS*CO*197*900.00~", dos="20261001"), dos="20261001") for i in range(10, 12)]
    paid = [claim(i, svc("99213", "150.00", "90.00", "90.00", "CAS*CO*45*60.00~", dos="20261001"), dos="20261001") for i in range(20, 30)]
    st = build_store(tmp_path, fix + auth + paid, pay_date="20261015")
    Detector(st, run_id="brief").run()
    return st


def test_open_work_is_grouped_by_the_kind_of_work_and_what_falls_due(tmp_path):
    st = store_with_work(tmp_path)
    b = insights.brief(st.conn, today="2026-11-13")
    assert b["ready"] and b["newest_payment"] == "2026-10-15"
    types = {t["key"]: t for t in b["work"]["types"]}
    assert types["fix"]["n"] == 3 and types["auth"]["n"] == 2 and "rate" not in types       # paid lines are not work
    assert b["work"]["total"]["n"] == 5 and b["work"]["total"]["cents"] == sum(t["cents"] for t in b["work"]["types"])
    # an Anthem fix runs on the appeal window (two years) with the agreement note: nothing falls due within 30 days
    assert b["work"]["total"]["due30_n"] == 0 and b["work"]["expired_n"] == 0
    # 29 days after the newest remittance: the feed is stale and says so first
    feed = b["health"][0]
    assert feed["key"] == "feed" and feed["ok"] is False and feed["days"] == 29
    causes = {c["signal"]: c for c in b["causes"]}
    assert causes["DENIED_MISSING_MODIFIER"]["n90"] == 3 and causes["DENIED_AUTH"]["work"] == "auth"
    assert [t["key"] for t in b["work_types"]] == ["fix", "auth", "note", "rule", "rate", "check"]
    assert b["labels"]["DENIED_AUTH"]["label"] == "Denied: no authorization" and b["statuses"]["appealed"] == "Sent"
    st.close()


def test_every_signal_that_is_work_has_a_kind_and_the_endpoint_serves_it(tmp_path, monkeypatch):
    import re
    src = open(os.path.join(os.path.dirname(insights.__file__), "detect.py"), encoding="utf-8").read()
    work_signals = set(re.findall(r'_emit\(\s*"([A-Z0-9_]+)",\s*"(?:denial|bundling|downcoding|underpayment)"', src))
    assert work_signals and work_signals <= insights.WORK_SIGNALS, work_signals - insights.WORK_SIGNALS
    st = store_with_work(tmp_path)
    st.close()
    from fastapi.testclient import TestClient

    from remitwatch import api
    monkeypatch.setattr(api, "DB_PATH", str(tmp_path / "s.sqlite"))
    monkeypatch.setattr(api, "PASSWORD", PW)
    monkeypatch.setattr(api, "USERNAME", "oda")
    monkeypatch.delenv("REMITWATCH_MODE", raising=False)
    tc = TestClient(api.app)
    assert tc.get("/api/brief").status_code == 401
    b = tc.get("/api/brief", auth=("oda", PW)).json()
    assert b["ready"] and {t["key"] for t in b["work"]["types"]} == {"fix", "auth"}
    assert "SAMPLE" not in json.dumps(b).upper()           # no patient on the home screen's data


def test_batches_decisions_missed_and_done(tmp_path):
    from remitwatch.actions import apply_status
    st = store_with_work(tmp_path)
    b = insights.brief(st.conn, today="2026-11-13")
    kinds = {(x["kind"], x["payer_group"]): x for x in b["batches"]}
    fix = kinds[("fix", "Anthem / BCBS")]
    assert fix["n"] == 3 and fix["claims"] == 3 and fix["top_code"] == "99213" and fix["started_n"] == 0 and fix["first_deadline"] >= "2026-11-13"
    assert kinds[("auth", "Anthem / BCBS")]["n"] == 2
    assert b["missed"]["n"] == 0 and b["follow_ups"]["n"] == 0 and b["decide"]["n"] == 0
    # a person starts one and sends another: the batch shows it, and today's done counter counts both
    fid = st.query("SELECT finding_id FROM findings WHERE signal='DENIED_MISSING_MODIFIER' ORDER BY finding_id")
    apply_status(st.conn, fid[0][0], "in_progress", actor="biller", at="2026-11-13T15:00:00+00:00")
    apply_status(st.conn, fid[1][0], "appealed", actor="biller", at="2026-11-13T15:05:00+00:00")
    b = insights.brief(st.conn, today="2026-11-13")
    fix = {(x["kind"], x["payer_group"]): x for x in b["batches"]}[("fix", "Anthem / BCBS")]
    # the sent one has left the pile: the batch, the to-do totals and the list a click opens all count two
    assert fix["started_n"] == 1 and fix["sent_n"] == 1 and fix["n"] == 2
    assert {t["key"]: t["n"] for t in b["work"]["types"]}["fix"] == 2 and b["work"]["total"]["n"] == 4 and b["work"]["sent_n"] == 1
    assert b["done"]["today_n"] == 2 and b["done"]["week_n"] == 2 and b["done"]["week_cents"] > 0
    # sent more than 30 days before the newest remittance and not paid: a follow-up
    st.conn.execute("UPDATE findings SET appealed_at='2026-09-01T00:00:00+00:00' WHERE finding_id=?", (fid[1][0],))
    assert insights.brief(st.conn, today="2026-11-13")["follow_ups"]["n"] == 1
    # a window that closed in the last 30 days without anyone working the item is missed
    st.conn.execute("UPDATE findings SET appeal_deadline='2026-11-01' WHERE finding_id=?", (fid[2][0],))
    assert insights.brief(st.conn, today="2026-11-13")["missed"]["n"] == 1
    st.close()


def test_every_signal_the_page_knows_has_a_plain_label():
    import re
    page = open(os.path.join(os.path.dirname(insights.__file__), "static", "index.html"), encoding="utf-8").read()
    help_block = page[page.index("const SIGNAL_HELP = {"):page.index("};", page.index("const SIGNAL_HELP = {"))]
    known = set(re.findall(r"^\s*([A-Z][A-Z0-9_]+):", help_block, flags=re.M))
    assert known and known <= set(insights.SIGNAL_LABELS), known - set(insights.SIGNAL_LABELS)
    for sig, (label, nxt) in insights.SIGNAL_LABELS.items():
        assert label and nxt and "—" not in label + nxt, sig          # plain words, no dashes standing in for punctuation


def test_every_number_on_the_home_screen_opens_a_list_of_exactly_that_many(tmp_path, monkeypatch):
    """Each count Today shows is a link to the worklist with certain filters. The list must hold exactly the items the
    number counted: a click that opens 140 items under a "137" teaches people not to trust the numbers."""
    from datetime import date, timedelta

    from fastapi.testclient import TestClient

    from remitwatch import api
    from remitwatch.actions import apply_status
    st = store_with_work(tmp_path)
    today = date.today()
    ids = [r[0] for r in st.query("SELECT finding_id FROM findings WHERE category IN ('denial','bundling','downcoding','underpayment') ORDER BY finding_id")]
    assert len(ids) >= 5
    st.conn.execute("UPDATE findings SET appeal_deadline=? WHERE finding_id=?", ((today + timedelta(days=3)).isoformat(), ids[0]))       # this week
    st.conn.execute("UPDATE findings SET appeal_deadline=? WHERE finding_id=?", ((today - timedelta(days=5)).isoformat(), ids[1]))       # missed
    st.conn.execute("UPDATE findings SET appeal_deadline=NULL, actionable='review' WHERE finding_id=?", (ids[2],))                       # to decide
    apply_status(st.conn, ids[3], "appealed", actor="biller", at="2026-11-13T15:05:00+00:00")
    st.conn.execute("UPDATE findings SET appealed_at='2026-08-01T00:00:00+00:00' WHERE finding_id=?", (ids[3],))                          # follow up
    apply_status(st.conn, ids[4], "in_progress", actor="biller", at="2026-11-13T15:00:00+00:00")
    st.conn.execute("UPDATE findings SET stale=1 WHERE finding_id=?", (ids[4],))                                                         # no longer found
    st.conn.commit()
    st.close()
    monkeypatch.setattr(api, "DB_PATH", str(tmp_path / "s.sqlite"))
    monkeypatch.setattr(api, "PASSWORD", PW)
    monkeypatch.setattr(api, "USERNAME", "oda")
    monkeypatch.delenv("REMITWATCH_MODE", raising=False)
    tc, auth = TestClient(api.app), ("oda", PW)
    b = tc.get("/api/brief", auth=auth).json()
    money = "denial,bundling,downcoding,underpayment"

    def listed(**kw):
        p = dict({"category": money, "live": 1, "limit": 2000}, **kw)
        r = tc.get("/api/findings", params=p, auth=auth).json()
        return r["total"], r["shortfall_cents"] or 0

    todo = {"status": "open,in_progress", "deadline": "valid"}
    w = b["work"]["total"]
    assert listed(**todo) == (w["n"], w["cents"])
    assert listed(**dict(todo, deadline="week")) == (w["due7_n"], w["due7_cents"]) and w["due7_n"] == 1
    assert listed(**dict(todo, deadline="month"))[0] == w["due30_n"]
    for t in b["work"]["types"]:
        assert listed(**dict(todo, kind=t["key"])) == (t["n"], t["cents"]), t["key"]
    counts = tc.get("/api/worklist/counts", params=dict({"category": money, "live": 1}, **todo), auth=auth).json()
    assert {k["key"]: k["n"] for k in counts["kinds"] if k["n"]} == {t["key"]: t["n"] for t in b["work"]["types"]}
    for x in b["batches"]:
        assert listed(**dict(todo, kind=x["kind"], payer=x["payer_group"])) == (x["n"], x["cents"]), x
    assert listed(status="open", deadline="missed") == (b["missed"]["n"], b["missed"]["cents"]) and b["missed"]["n"] == 1
    assert listed(status="open,in_progress", deadline="decide") == (b["decide"]["n"], b["decide"]["cents"]) and b["decide"]["n"] == 1
    assert listed(status="appealed", deadline="followup") == (b["follow_ups"]["n"], b["follow_ups"]["cents"]) and b["follow_ups"]["n"] == 1
    for c in b["causes"]:
        assert listed(signal=c["signal"], status="all", paid=90) == (c["n90"], c["cents90"]), c["signal"]
    # the item no longer found by the current rules is in no count, and the worklist leaves it out too
    assert tc.get("/api/findings", params={"category": money, "status": "in_progress", "live": 1}, auth=auth).json()["total"] == 0


def test_the_page_scripts_declare_no_top_level_name_twice():
    """The page's scripts share one global scope: a name declared twice (a const in one file, a function in another)
    stops the later file from loading at all, and with it every screen it draws."""
    import re
    static = os.path.join(os.path.dirname(insights.__file__), "static")
    page = open(os.path.join(static, "index.html"), encoding="utf-8").read()
    sources = {"index.html": page[page.rindex("<script>"):]}
    for name in re.findall(r'<script src="/static/([a-z_]+\.js)"', page):
        sources[name] = open(os.path.join(static, name), encoding="utf-8").read()
    assert "today.js" in sources and "features.js" in sources
    seen: dict[str, str] = {}
    for fname, src in sources.items():
        for name in re.findall(r"^(?:const|let|var|function|async function)\s+([A-Za-z_$][\w$]*)", src, flags=re.M):
            assert name not in seen, f"{name} is declared in {seen[name]} and again in {fname}"
            seen[name] = fname


def test_patterns_and_whole_claim_denials_count_the_same_on_the_home_screen_and_in_the_list(tmp_path, monkeypatch):
    """A routine-rate pattern stands for claims with their own filing dates, and a whole claim denied takes the kind of
    work its reason needs. The home screen and the worklist must count both the same way, by kind and by date range."""
    from datetime import timedelta

    from fastapi.testclient import TestClient

    from remitwatch import api
    from remitwatch.store import Store
    from tests.review6.r6cov_h import add_file, clean_visits, clm, pcn, sv, utc_today, ymd
    db = str(tmp_path / "s.sqlite")
    today = utc_today()

    def visit(n, allowed, dos, plan):
        co = f"{1200 - float(allowed):.2f}"
        return clm(pcn(n), "1", sv("17311", "1200.00", allowed, allowed, f"CAS*CO*45*{co}~", dos=dos), dos=dos, charge="1200.00", paid=allowed, plan=plan)
    # Aetna (180 days from the remittance) allows $700, but $400 on one plan: a routine second rate. Its claims' windows
    # closed ten days ago, close in three days, and close in five months. The usual visits sit beside each batch, so the
    # rate holds in whichever calendar year a batch falls.
    paid = lambda back: ymd(today - timedelta(days=back))                       # noqa: E731
    for k, back in enumerate((190, 177, 30)):
        add_file(tmp_path, db, [visit(1000 + 100 * k + i, "700.00", paid(back + 10), "PPO") for i in range(25)]
                 + [visit(2000 + 100 * k + i, "400.00", paid(back + 10), "HMO SELECT") for i in range(6)], paid(back), payer="AETNA", payer_id="AETNA")
    # whole claims denied by Anthem for an authorization, missing information, bundling and timely filing
    dos = paid(40)
    add_file(tmp_path, db, clean_visits(100, 10, "99214", "223.65", "117.60", dos), paid(30))           # Anthem's usual rate for 99214
    add_file(tmp_path, db, [clm(pcn(90 + i), "4", sv("99214", "223.65", "0.00", cas=f"CAS*CO*{carc}*223.65~", dos=dos), dos=dos, charge="223.65")
                            for i, carc in enumerate(("197", "16", "97", "29", "208"))], paid(30))
    st = Store(db)
    Detector(st, run_id="mix").run()
    assert st.conn.execute("SELECT COUNT(*) FROM findings WHERE signal='SECOND_RATE'").fetchone()[0] == 1
    assert st.conn.execute("SELECT COUNT(*) FROM findings WHERE signal='CLAIM_DENIED_UNPAID'").fetchone()[0] == 5
    st.close()
    monkeypatch.setattr(api, "DB_PATH", db)
    monkeypatch.setattr(api, "PASSWORD", PW)
    monkeypatch.setattr(api, "USERNAME", "oda")
    monkeypatch.delenv("REMITWATCH_MODE", raising=False)
    tc, auth = TestClient(api.app), ("oda", PW)
    b = tc.get("/api/brief", auth=auth).json()
    kinds = {t["key"]: t for t in b["work"]["types"]}
    assert {"auth", "fix", "rule", "rate", "check"} <= set(kinds), kinds          # each whole claim under its reason's kind
    assert kinds["rate"]["cents"] == 2 * 180000 and kinds["rate"]["due7_cents"] == 180000        # the pattern's claims in window
    assert b["work"]["expired_cents"] >= 180000                                                  # its closed claims are not in window
    money = "denial,bundling,downcoding,underpayment"
    for rng, field in (("valid", "cents"), ("week", "due7_cents"), ("month", "due30_cents")):
        params = {"category": money, "live": 1, "status": "open,in_progress", "deadline": rng}
        cnt = {k["key"]: k for k in tc.get("/api/worklist/counts", params=params, auth=auth).json()["kinds"]}
        for key, t in kinds.items():
            got = tc.get("/api/findings", params=dict(params, kind=key, limit=500), auth=auth).json()
            want = t[field]
            assert want > 0 or rng != "valid", (key, t)
            assert (got["shortfall_cents"] or 0) == want and cnt[key]["cents"] == want, (rng, key, got["shortfall_cents"], cnt[key], want)
            if rng == "valid":
                assert got["total"] == t["n"] == cnt[key]["n"], (key, got["total"], t["n"], cnt[key])
    rows = tc.get("/api/findings", params={"category": money, "live": 1, "status": "open,in_progress", "deadline": "week", "kind": "rate"},
                  auth=auth).json()["items"]
    assert rows and rows[0]["window_cents"] == 180000 and rows[0]["next_deadline"] < rows[0]["appeal_deadline"]


def test_the_amount_filter_leaves_out_exactly_the_small_items_the_home_screen_counts(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from remitwatch import api
    st = store_with_work(tmp_path)
    fid = st.query("SELECT finding_id FROM findings WHERE signal='DENIED_AUTH' ORDER BY finding_id")[0][0]
    st.conn.execute("UPDATE findings SET shortfall_cents=1500 WHERE finding_id=?", (fid,))          # one item worth $15
    st.conn.commit()
    st.close()
    monkeypatch.setattr(api, "DB_PATH", str(tmp_path / "s.sqlite"))
    monkeypatch.setattr(api, "PASSWORD", PW)
    monkeypatch.setattr(api, "USERNAME", "oda")
    monkeypatch.delenv("REMITWATCH_MODE", raising=False)
    tc, auth = TestClient(api.app), ("oda", PW)
    w = tc.get("/api/brief", auth=auth).json()["work"]
    assert (w["small_n"], w["small_cents"], w["small_under"]) == (1, 1500, 2000)
    p = {"category": "denial,bundling,downcoding,underpayment", "live": 1, "status": "open,in_progress", "deadline": "valid"}
    every = tc.get("/api/findings", params=p, auth=auth).json()
    big = tc.get("/api/findings", params=dict(p, min=20), auth=auth).json()
    assert big["total"] == every["total"] - w["small_n"] and big["shortfall_cents"] == every["shortfall_cents"] - w["small_cents"]
    assert all(not (0 < (r["shortfall_cents"] or 0) < 2000) for r in big["items"])
    assert any(not r["shortfall_cents"] for r in big["items"])          # an item whose value is not known is not hidden
    counts = tc.get("/api/worklist/counts", params=dict(p, min=20), auth=auth).json()
    assert counts["n"] == big["total"]
