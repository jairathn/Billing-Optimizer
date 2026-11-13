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
    assert types["fix"]["n"] == 3 and types["front"]["n"] == 2 and "rate" not in types       # paid lines are not work
    assert b["work"]["total"]["n"] == 5 and b["work"]["total"]["cents"] == sum(t["cents"] for t in b["work"]["types"])
    # an Anthem fix runs on the appeal window (two years) with the agreement note: nothing falls due within 30 days
    assert b["work"]["total"]["due30_n"] == 0 and b["work"]["expired_n"] == 0
    # 29 days after the newest remittance: the feed is stale and says so first
    feed = b["health"][0]
    assert feed["key"] == "feed" and feed["ok"] is False and feed["days"] == 29
    causes = {c["signal"]: c for c in b["causes"]}
    assert causes["DENIED_MISSING_MODIFIER"]["n90"] == 3 and causes["DENIED_AUTH"]["work"] == "front"
    assert [t["key"] for t in b["work_types"]] == ["fix", "note", "rules", "front", "rate", "review"]
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
    assert b["ready"] and {t["key"] for t in b["work"]["types"]} == {"fix", "front"}
    assert "SAMPLE" not in json.dumps(b).upper()           # no patient on the home screen's data


def test_batches_decisions_missed_and_done(tmp_path):
    from remitwatch.actions import apply_status
    st = store_with_work(tmp_path)
    b = insights.brief(st.conn, today="2026-11-13")
    kinds = {(x["kind"], x["payer_group"]): x for x in b["batches"]}
    fix = kinds[("fix", "Anthem / BCBS")]
    assert fix["n"] == 3 and fix["claims"] == 3 and fix["top_code"] == "99213" and fix["started_n"] == 0 and fix["first_deadline"] >= "2026-11-13"
    assert kinds[("front", "Anthem / BCBS")]["n"] == 2
    assert b["missed"]["n"] == 0 and b["follow_ups"]["n"] == 0 and b["decide"]["n"] == 0
    # a person starts one and sends another: the batch shows it, and today's done counter counts both
    fid = st.query("SELECT finding_id FROM findings WHERE signal='DENIED_MISSING_MODIFIER' ORDER BY finding_id")
    apply_status(st.conn, fid[0][0], "in_progress", actor="biller", at="2026-11-13T15:00:00+00:00")
    apply_status(st.conn, fid[1][0], "appealed", actor="biller", at="2026-11-13T15:05:00+00:00")
    b = insights.brief(st.conn, today="2026-11-13")
    fix = {(x["kind"], x["payer_group"]): x for x in b["batches"]}[("fix", "Anthem / BCBS")]
    assert fix["started_n"] == 1 and fix["sent_n"] == 1 and fix["n"] == 3
    assert b["done"]["today_n"] == 2 and b["done"]["week_n"] == 2 and b["done"]["week_cents"] > 0
    # sent more than 30 days before the newest remittance and not paid: a follow-up
    st.conn.execute("UPDATE findings SET appealed_at='2026-09-01T00:00:00+00:00' WHERE finding_id=?", (fid[1][0],))
    assert insights.brief(st.conn, today="2026-11-13")["follow_ups"]["n"] == 1
    # a window that closed in the last 30 days without anyone working the item is missed
    st.conn.execute("UPDATE findings SET appeal_deadline='2026-11-01' WHERE finding_id=?", (fid[2][0],))
    assert insights.brief(st.conn, today="2026-11-13")["missed"]["n"] == 1
    st.close()
