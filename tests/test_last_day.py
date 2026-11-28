"""
One definition of the last day an item's money can be asked for (review e-1). ui-work made an item past our target date
(appeal_deadline) and still inside the payer's own limit (legal_deadline), the "last chance", close on that limit in
Work's lists and Today's 7-day figure (api._last_day). insights.missed still counted it lost the day its target passed and
Results' "At risk in the next 14 days" still asked the target date: one item closing in 3 days showed as closing soon and
as lost on the same Today strip, and was missing from Results' at-risk figure. Now states.last_day / last_day_sql date it
everywhere. On both hosts, invented data (the tests/test_ema_pages world).
"""

from __future__ import annotations

import datetime as dt
import sqlite3

import pytest

from tests.conftest import _own_scratch_folder  # noqa: F401
from tests.test_ema_pages import CATS, _items, _site, world  # noqa: F401


@pytest.mark.parametrize("mode", ["server", "serverless"])
def test_an_item_that_closes_in_3_days_is_at_risk_wherever_it_closes(world, tmp_path, monkeypatch, mode):  # noqa: F811
    from remitwatch import results
    site = _site(world, tmp_path, monkeypatch, "server")
    today = site.api._day(0)
    monkeypatch.setattr(results, "_today", lambda *a, **k: today)
    from remitwatch import insights
    monkeypatch.setattr(insights, "utc_today", lambda *a, **k: today)
    conn = sqlite3.connect(site.db)
    try:
        rows = conn.execute("""SELECT finding_id FROM findings WHERE status='open' AND work_state='work_now' AND COALESCE(stale,0)=0
                              AND category IN ('denial','bundling','downcoding','underpayment') AND appeal_deadline IS NOT NULL
                              AND signal NOT IN ('RATE_PATTERN','ROUTINE_RATE') ORDER BY shortfall_cents DESC LIMIT 2""").fetchall()
        fid, control = rows[0][0], rows[1][0]
        yesterday = (dt.date.fromisoformat(today) - dt.timedelta(days=1)).isoformat()
        in3 = (dt.date.fromisoformat(today) + dt.timedelta(days=3)).isoformat()
        conn.execute("UPDATE findings SET appeal_deadline=?, legal_deadline=? WHERE finding_id=?", (yesterday, in3, fid))
        # the control: our own target date 3 days ahead, no later payer limit
        conn.execute("UPDATE findings SET appeal_deadline=?, legal_deadline=NULL WHERE finding_id=?", (in3, control))
        conn.commit()
    finally:
        conn.close()
    if mode == "serverless":
        site = _site_from_db(site, tmp_path, monkeypatch)
    week = [i["finding_id"] for i in _items(site, category=CATS, status="open,in_progress", deadline="week", limit=500)["items"]]
    assert fid in week and control in week               # Work: both close within 7 days
    # Today's "missed in the last 30 days" (insights.missed, appeal_deadline) and the list its link opens (deadline=missed,
    # api._last_day) must agree, and the item that still closes in 3 days is not missed
    missed_list = _items(site, category=CATS, status="open", deadline="missed", limit=500)
    brief = site.c.get("/api/brief").json()
    assert fid not in [i["finding_id"] for i in missed_list["items"]]
    assert brief["missed"]["n"] == missed_list["total"], (brief["missed"], missed_list["total"])
    rlist = site.c.get("/api/results/list", params={"key": "at_risk"}).json()
    ids = [r.get("finding_id") for r in (rlist.get("items") or rlist.get("rows") or [])]
    assert control in ids, (rlist.keys(), ids)           # the control is at risk on Results
    assert fid in ids, "Results' at risk in the next 14 days leaves out an item that closes in 3 days"


def _site_from_db(site, tmp_path, monkeypatch):
    from remitwatch import cloud
    from remitwatch.state import LocalState
    st = LocalState(str(tmp_path / "state2"))
    conn = sqlite3.connect(site.db)
    try:
        cloud.save_serving(st, conn)
    finally:
        conn.close()
    monkeypatch.setenv("REMITWATCH_MODE", "serverless")
    monkeypatch.setenv("REMITWATCH_STATE_DIR", str(tmp_path / "state2"))
    monkeypatch.setattr(cloud, "SERVING_COPY", cloud.ServingCopy())
    return site


def test_the_last_day_is_our_target_until_it_passes_then_the_payers_own_limit():
    from remitwatch.states import last_day, last_day_sql
    assert last_day("2026-05-06", "2026-06-01", "2026-05-03") == "2026-05-06"      # our target still ahead
    assert last_day("2026-05-02", "2026-05-06", "2026-05-03") == "2026-05-06"      # last chance: the payer's limit
    assert last_day("2026-05-02", "2026-05-03", "2026-05-03") == "2026-05-03"      # the limit is today: still open
    assert last_day("2026-05-01", "2026-05-02", "2026-05-03") == "2026-05-01"      # both passed: closed on our target
    assert last_day("2026-05-02", None, "2026-05-03") == "2026-05-02" and last_day(None, "2026-06-01", "2026-05-03") is None
    assert last_day_sql("2026-05-03", "f").startswith("(CASE WHEN f.appeal_deadline < '2026-05-03'")
    assert last_day_sql("2026-05-03' OR 1=1 --", "f") == "f.appeal_deadline"          # never anything but a date in the SQL
    assert last_day_sql("2026-05-03", has_legal=False) == "appeal_deadline"
