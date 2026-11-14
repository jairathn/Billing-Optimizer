"""/api/brief's "what came back" counts every resolved finding with recovered cents: stale rows (left out of every total,
ARCHITECTURE.md) and categories outside the money families (a payment fee a person marks recovered) included. Progress,
the screen that already reports recoveries, counts only the money categories and never a stale row, so the two disagree
as soon as such a row exists (none on the archive today). Invented data only."""
from __future__ import annotations

import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from r6cov_h import add_file, client, clean_visits, clm, pcn, sv, utc_today, ymd  # noqa: E402

from remitwatch import insights  # noqa: E402
from remitwatch.actions import apply_status  # noqa: E402
from remitwatch.detect import Detector  # noqa: E402
from remitwatch.store import Store  # noqa: E402


def test_recovered_on_the_home_screen_matches_progress(tmp_path, monkeypatch):
    db = str(tmp_path / "s.sqlite")
    today = utc_today()
    dos, paid = ymd(today - timedelta(days=40)), ymd(today - timedelta(days=30))
    add_file(tmp_path, db, clean_visits(100, 10, "99213", "150.00", "90.00", dos), paid)
    add_file(tmp_path, db, [clm(pcn(n), "1", sv("99213", "150.00", "0.00", cas="CAS*CO*197*150.00~", dos=dos), dos=dos) for n in (1, 2)], paid,
             plb="PLB*999999999*20261231*CS:ZELIS TRANSACTION FEE*15.00~")
    st = Store(db)
    Detector(st, run_id="r6").run()
    ids = dict(st.conn.execute("SELECT patient_control_number, finding_id FROM findings WHERE signal='DENIED_AUTH'").fetchall())
    fee = st.conn.execute("SELECT finding_id FROM findings WHERE signal='FEE_SKIM'").fetchone()[0]
    at = today.isoformat() + "T12:00:00+00:00"
    apply_status(st.conn, ids[pcn(1)], "resolved", recovered_cents=9000, actor="biller", at=at)
    apply_status(st.conn, ids[pcn(2)], "resolved", recovered_cents=9000, actor="biller", at=at)
    apply_status(st.conn, fee, "resolved", recovered_cents=1500, actor="biller", at=at)
    # a worked finding the rules no longer produce is kept and marked stale (Detector.save); left out of every total
    st.conn.execute("UPDATE findings SET stale=1 WHERE finding_id=?", (ids[pcn(2)],))
    st.conn.commit()
    b = insights.brief(st.conn, today=today.isoformat())
    st.close()
    tc, auth = client(monkeypatch, db)
    prog = tc.get("/api/progress", auth=auth).json()
    progress_year = sum(r["cents"] for r in prog["recovered_by_month"] if (r["month"] or "").startswith(today.strftime("%Y")))
    assert progress_year == 9000
    assert b["recovered"]["year_cents"] == progress_year, \
        f"home screen {b['recovered']['year_cents']} cents this year (a stale row and a payment fee included), Progress {progress_year}"
