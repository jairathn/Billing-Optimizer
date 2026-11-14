"""A routine-rate finding (SECOND_RATE) stands for many claims, each with its own filing deadline. ARCHITECTURE.md: the
summary, the "due" filter, Progress, the deadline alert and the weekly report count its claims by their own deadlines,
and count it as still appealable only for the claims whose windows are open. /api/brief counts the whole finding on the
date on its row, which is its last claim's deadline: claims due in three days are not "due in 7 days", and the cents of
claims whose windows have closed are counted as still in window. On the archive: 19 patterns, $7,120 counted in window
where $4,918 is; the brief's 30-day figure (319, $25,949) differs from the Appeals tab's (325, $25,442).
Invented data only."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from r6cov_h import add_file, clm, pcn, sv  # noqa: E402

from remitwatch import insights  # noqa: E402
from remitwatch.detect import Detector, pattern_windows  # noqa: E402
from remitwatch.store import Store  # noqa: E402

TODAY = "2026-11-13"


def visit(n, allowed, dos):
    co = f"{1200 - float(allowed):.2f}"
    return clm(pcn(n), "1", sv("17311", "1200.00", allowed, allowed, f"CAS*CO*45*{co}~", dos=dos), dos=dos, charge="1200.00", paid=allowed)


def test_a_routine_rate_finding_counts_its_claims_by_their_own_deadlines(tmp_path):
    db = str(tmp_path / "s.sqlite")
    # Aetna (180 days from the remittance): usual $700 in January, then $400 on every visit since (a rate change)
    add_file(tmp_path, db, [visit(i, "700.00", "20260105") for i in range(1, 26)], "20260115", payer="AETNA", payer_id="AETNA")
    add_file(tmp_path, db, [visit(i, "400.00", "20260427") for i in range(30, 33)], "20260507", payer="AETNA", payer_id="AETNA")   # closed 2026-11-03
    add_file(tmp_path, db, [visit(i, "400.00", "20260510") for i in range(40, 43)], "20260520", payer="AETNA", payer_id="AETNA")   # closes 2026-11-16
    add_file(tmp_path, db, [visit(i, "400.00", "20261014") for i in range(50, 53)], "20261024", payer="AETNA", payer_id="AETNA")   # closes 2027-04-22
    st = Store(db)
    Detector(st, run_id="r6").run()
    rows = st.conn.execute("SELECT finding_id, appeal_deadline, shortfall_cents, status FROM findings WHERE signal='SECOND_RATE'").fetchall()
    assert len(rows) == 1 and rows[0][1] == "2027-04-22" and rows[0][2] == 9 * 30000 and rows[0][3] == "open", rows
    windows = sorted((d, n, c) for _f, _g, _code, d, n, c in pattern_windows(st.conn))
    assert windows == [("2026-11-03", 3, 90000), ("2026-11-16", 3, 90000), ("2027-04-22", 3, 90000)]
    b = insights.brief(st.conn, today=TODAY)
    st.close()
    rate = next(t for t in b["work"]["types"] if t["key"] == "rate")
    # by the claims' own deadlines: $900 due within 7 days (and 30), $1,800 still in window, $900 past its window
    got = {"due7_cents": rate["due7_cents"], "due30_cents": rate["due30_cents"], "cents": rate["cents"], "expired_cents": b["work"]["expired_cents"]}
    assert got == {"due7_cents": 90000, "due30_cents": 90000, "cents": 180000, "expired_cents": 90000}, got
