"""
The daily run on a serverless host analyses a compact in-memory store that has almost no secondary indexes. A query
that looks something up for every row of another table then scans a whole table each time, and on the full archive one
such query ran far past the function's time limit. These tests run detection and the provider tables on a compact store
and check the plan of every statement they executed. Synthetic data only.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch import activity  # noqa: E402
from remitwatch.cloud import OutOfTime, deadline_guard  # noqa: E402
from remitwatch.detect import Detector, ensure_schema  # noqa: E402
from remitwatch.store import COMPACT_INDEXES, Store  # noqa: E402
from tests.test_features import claim, clean_visits, svc  # noqa: E402
from tests.test_remitwatch import remit  # noqa: E402

BIG = {"lines", "claims", "transactions", "adjustments", "remarks", "ema_rows", "plb", "findings", "files", "exposure",
       "rate_points", "line_links", "line_handling", "dropbox_seen"}


def compact_store(tmp_path) -> Store:
    claims = (clean_visits(1, 30, "99214", "223.65", "117.60") + clean_visits(100, 30, "99213", "156.66", "76.15")
              + clean_visits(200, 10, "17311", "900.00", "600.00"))
    claims += [claim(300 + i, svc("99214", "223.65", "0.00", None, "CAS*CO*197*223.65~"), status="4") for i in range(5)]
    claims += [claim(400 + i, "") for i in range(5)]            # paid with no service lines
    claims += [claim(500 + i, svc("99214", "223.65", "60.00", "60.00", "CAS*CO*45*163.65~")) for i in range(5)]
    store = Store(":memory:", compact=True)
    paths = []
    for n, chunk in enumerate([claims[i:i + 20] for i in range(0, len(claims), 20)]):
        p = tmp_path / f"r{n:04d}.RMT"
        p.write_bytes(remit("".join(chunk), "100.00", payer="ANTHEM", seg_count=99, isa_ctrl=f"{n + 1:09d}", pay_date="20260415"))
        paths.append(str(p))
    store.ingest_835_paths(paths)
    ensure_schema(store.conn)
    return store


def aliases(sql: str) -> dict[str, str]:
    """alias -> table for `FROM t a` / `JOIN t AS a` in one statement (good enough for this code's SQL)."""
    import re
    out = {}
    for t, a in re.findall(r"\b(?:FROM|JOIN)\s+(?:main\.)?([A-Za-z_][A-Za-z0-9_]*)\s+(?:AS\s+)?([A-Za-z_][A-Za-z0-9_]*)", sql, re.I):
        if a.upper() not in {"WHERE", "JOIN", "LEFT", "INNER", "ON", "GROUP", "ORDER", "USING", "CROSS", "LIMIT", "UNION", "SET"}:
            out[a] = t
    return out


def rescans(conn: sqlite3.Connection, sql: str) -> list[str]:
    """Plan steps that read a whole large table once per row of an outer loop."""
    try:
        plan = conn.execute("EXPLAIN QUERY PLAN " + sql).fetchall()
    except sqlite3.Error:
        return []
    node = {r[0]: (r[1], r[3]) for r in plan}
    names = aliases(sql)
    bad = []
    for nid, (parent, detail) in node.items():
        if not detail.startswith("SCAN ") or "AUTOMATIC" in detail:
            continue
        table = detail.split()[1]
        if names.get(table, table) not in BIG:
            continue
        p = parent
        while p in node:
            if node[p][1].startswith("CORRELATED"):
                bad.append(f"{detail} inside {node[p][1]}")
                break
            p = node[p][0]
    return bad


def test_compact_store_keeps_the_indexes_the_rules_need():
    store = Store(":memory:", compact=True)
    have = {r[0] for r in store.conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert set(COMPACT_INDEXES) <= have
    assert "ix_adj_line" not in have            # the rest stay out of the compact store
    # an image written before these indexes existed gets them when it is opened
    blank = sqlite3.connect(":memory:")
    blank.executescript("CREATE TABLE lines (line_key TEXT PRIMARY KEY, claim_key TEXT); CREATE TABLE claims (claim_key TEXT PRIMARY KEY, patient_control_number TEXT)")
    reopened = Store.from_bytes(blank.serialize())
    have = {r[0] for r in reopened.conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert set(COMPACT_INDEXES) <= have


def test_no_rule_rescans_a_table_for_every_row_on_the_compact_store(tmp_path):
    store = compact_store(tmp_path)
    seen: list[str] = []
    store.conn.set_trace_callback(seen.append)
    Detector(store, run_id="t").run()
    activity.refresh(store, log=lambda *_a: None)
    store.conn.set_trace_callback(None)
    assert store.conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0] > 0
    problems = {}
    for sql in dict.fromkeys(seen):
        if sql.lstrip().upper().startswith(("SELECT", "WITH", "INSERT", "UPDATE", "DELETE")):
            bad = rescans(store.conn, sql)
            if bad:
                problems[" ".join(sql.split())[:160]] = bad
    assert not problems, problems


def test_the_check_catches_a_rescan():
    conn = Store(":memory:", compact=True).conn
    sql = "SELECT c.claim_key FROM claims c WHERE NOT EXISTS (SELECT 1 FROM adjustments a WHERE a.claim_key = c.claim_key)"
    assert rescans(conn, sql)


def test_deadline_guard_stops_a_long_statement():
    conn = sqlite3.connect(":memory:")
    slow = "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) SELECT COUNT(*) FROM n"
    t = time.time()
    with pytest.raises(OutOfTime):
        with deadline_guard(conn, time.time() + 0.2, "Detection"):
            conn.execute(slow).fetchone()
    assert time.time() - t < 5
    # soft: only the running statement is stopped, the step carries on and can record what happened
    guard = deadline_guard(conn, time.time() - 1, "The provider tables", hard=False)
    with guard:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute(slow).fetchone()
        assert conn.execute("SELECT 1").fetchone() == (1,)
    assert guard.tripped
    # the handler is gone afterwards
    assert conn.execute("SELECT COUNT(*) FROM (WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n LIMIT 300000) SELECT i FROM n)").fetchone() == (300000,)
