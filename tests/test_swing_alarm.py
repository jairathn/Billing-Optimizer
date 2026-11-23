"""The swing alarm: one analysis run that adds or removes far more work than any day of the last year is held up to a
person, on every page and by email, for 7 days or until acknowledged; amber when a release re-checked every claim.
Counted item by item, leaving out what people changed while the run went. All data invented."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from remitwatch import detect
from remitwatch.alerts import run_alerts
from remitwatch.detect import Detector, record_swing
from tests.test_features import build_store, claim, svc


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("""CREATE TABLE finding_events (id INTEGER PRIMARY KEY AUTOINCREMENT, finding_id TEXT, at TEXT, kind TEXT, from_status TEXT,
                    to_status TEXT, amount_cents INTEGER, actor TEXT, note TEXT, run_id TEXT)""")
    return conn


def _items(prefix: str, n: int, cents: int = 10000, cat: str = "denial") -> dict:
    return {f"{prefix}{i:05d}": (cat, cents) for i in range(n)}


SINCE = "2026-11-23T00:00:00+00:00"


def test_an_ordinary_run_raises_nothing():
    conn = _conn()
    before = _items("a", 1000)
    after = dict(before, **_items("b", 290))                      # the busiest day of the last year
    assert record_swing(conn, before, after, SINCE, "r", False) is None
    assert conn.execute("SELECT COUNT(*) FROM meta").fetchone()[0] == 0


def test_a_flood_of_new_items_is_held_up():
    conn = _conn()
    before = _items("a", 1000)
    after = dict(before, **_items("b", 600))
    s = record_swing(conn, before, after, SINCE, "r1", False)
    assert s and s["added"] == [600, 6000000] and s["removed"] == [0, 0]
    assert json.loads(conn.execute("SELECT value FROM meta WHERE key=?", (detect.SWING_KEY,)).fetchone()[0])["run_id"] == "r1"


def test_a_swap_that_nets_to_nothing_is_still_held_up():
    """800 items replaced by 800 others: the totals do not move, the items do."""
    conn = _conn()
    before = dict(_items("a", 500), **_items("x", 800))
    after = dict(_items("a", 500), **_items("y", 800))
    s = record_swing(conn, before, after, SINCE, "r", False)
    assert s and s["added"][0] == 800 and s["removed"][0] == 800


def test_dollars_and_one_category_count_on_their_own():
    conn = _conn()
    before = _items("a", 100)
    assert record_swing(conn, before, dict(before, **_items("b", 50, cents=600_000)), SINCE, "r", False)          # $300,000 added
    assert record_swing(_conn(), before, dict(before, **_items("c", 301, cents=100, cat="bundling")), SINCE, "r", False)
    assert record_swing(_conn(), before, {k: v for k, v in list(before.items())[:-1]}, SINCE, "r", False) is None
    big = _items("d", 300, cents=40000)                                                                            # $120,000
    assert record_swing(_conn(), dict(before, **big), before, SINCE, "r", False)                                   # a drop


def test_what_people_changed_during_the_run_does_not_count():
    """A busy Let-go session removes many items: it is people's work, not the run's."""
    conn = _conn()
    before = _items("a", 1000)
    after = {k: v for k, v in before.items() if not k.startswith("a00")}                          # 100 left by people
    conn.executemany("INSERT INTO finding_events (finding_id, at, kind, actor) VALUES (?, ?, 'status', 'biller')",
                     [(k, "2026-11-23T10:00:00+00:00") for k in before if k.startswith("a0")])    # 1,000 touched by people
    assert record_swing(conn, before, after, SINCE, "r", False) is None
    # the same removal by a run (its events carry the run id) is held up
    conn2 = _conn()
    conn2.executemany("INSERT INTO finding_events (finding_id, at, kind, actor, run_id) VALUES (?, ?, 'auto', 'system', 'r')",
                      [(k, "2026-11-23T10:00:00+00:00") for k in before if k.startswith("a0")])
    after2 = {k: v for k, v in before.items() if not k.startswith("a0")}
    assert record_swing(conn2, before, after2, SINCE, "r", False)


def test_a_first_run_raises_nothing():
    assert record_swing(_conn(), {}, _items("a", 5000), SINCE, "r", False) is None


def _alert_store(swing: dict) -> sqlite3.Connection:
    from remitwatch.store import connect
    conn = connect(":memory:")
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (detect.SWING_KEY, json.dumps(swing)))
    conn.commit()
    return conn


def test_the_alert_is_red_amber_after_a_release_and_lasts_seven_days():
    now = datetime.now(timezone.utc)
    swing = {"at": now.isoformat(timespec="seconds"), "run_id": "r", "rules_changed": False, "added": [1612, 41230000], "removed": [3, 1200],
             "categories": {"denial": {"added": [1600, 41000000], "removed": [3, 1200]}}}
    conn = _alert_store(swing)
    run_alerts(conn)
    a = conn.execute("SELECT kind, severity, title, status FROM alerts WHERE kind='WORKLIST_SWING'").fetchone()
    assert a[0] == "WORKLIST_SWING" and a[1] == "high" and "1,612 items added" in a[2] and a[3] == "new"
    # acknowledged, it stays acknowledged while it lasts
    conn.execute("UPDATE alerts SET status='acknowledged' WHERE kind='WORKLIST_SWING'")
    run_alerts(conn)
    assert conn.execute("SELECT status FROM alerts WHERE kind='WORKLIST_SWING'").fetchone()[0] == "acknowledged"
    # a week later it clears by itself
    old = dict(swing, at=(now - timedelta(days=8)).isoformat(timespec="seconds"))
    conn.execute("UPDATE meta SET value=? WHERE key=?", (json.dumps(old), detect.SWING_KEY))
    run_alerts(conn)
    assert {r[0] for r in conn.execute("SELECT status FROM alerts WHERE kind='WORKLIST_SWING'")} == {"cleared"}
    # after a release that re-checked every claim: amber, and it says so
    conn2 = _alert_store(dict(swing, rules_changed=True))
    run_alerts(conn2)
    sev, title = conn2.execute("SELECT severity, title FROM alerts WHERE kind='WORKLIST_SWING'").fetchone()
    assert sev == "medium" and "new release re-checked every claim" in title


def test_a_run_that_floods_the_worklist_records_the_swing(tmp_path, monkeypatch):
    monkeypatch.setattr(detect, "SWING_ADD_N", 40)               # the levels scaled down to a small store
    denied = lambda i: claim(i, svc("99214", "223.65", "0.00", None, "CAS*CO*50*223.65~", dos="20261001"), dos="20261001")  # noqa: E731
    s = build_store(tmp_path, [denied(i) for i in range(1, 11)], pay_date="20261110")
    Detector(s, run_id="first").run()
    assert s.conn.execute("SELECT value FROM meta WHERE key=?", (detect.SWING_KEY,)).fetchone() is None
    s.close()
    s = build_store(tmp_path, [denied(i) for i in range(100, 160)], pay_date="20261112")
    Detector(s, run_id="flood").run()
    swing = json.loads(s.conn.execute("SELECT value FROM meta WHERE key=?", (detect.SWING_KEY,)).fetchone()[0])
    assert swing["run_id"] == "flood" and swing["added"][0] >= 41 and swing["rules_changed"] is False
    s.close()
