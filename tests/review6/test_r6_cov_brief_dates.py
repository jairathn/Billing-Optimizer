"""Two date choices in remitwatch.insights. (1) brief() takes "today" from the server's local clock (date.today()); the
rest of the dashboard (the summary's deadline buckets, Progress, the due filter) takes it from UTC (SQL date('now'),
api._day). A server whose clock is not on UTC puts the home screen a day apart from the Appeals tab for part of every day.
(2) contract_levers() promises "the last full year" but takes the current year once the newest payment is in December.
Invented data only."""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from r6cov_h import REPO  # noqa: E402

from remitwatch import insights  # noqa: E402
from remitwatch.detect import ensure_schema  # noqa: E402
from remitwatch.store import Store  # noqa: E402


def test_the_home_screen_and_the_appeals_tab_count_the_same_day(tmp_path):
    db = str(tmp_path / "s.sqlite")
    st = Store(db)
    ensure_schema(st.conn)
    st.close()
    code = textwrap.dedent(f"""
        import sys, sqlite3
        sys.path.insert(0, {REPO!r})
        from remitwatch import insights
        print(insights.brief(sqlite3.connect({db!r}))["today"])
    """)
    days = {}
    # POSIX zones: 14 hours ahead of UTC and 12 hours behind. At any moment one of them is on another date than UTC.
    for tz in ("AHEAD-14", "BEHIND+12"):
        out = subprocess.run([sys.executable, "-B", "-I", "-c", code], capture_output=True, text=True, env=dict(os.environ, TZ=tz))
        assert out.returncode == 0, out.stderr[-2000:]
        days[tz] = out.stdout.strip()
    utc = datetime.now(timezone.utc).date().isoformat()
    assert set(days.values()) == {utc}, f"the brief's today follows the server's clock ({days}); the dashboard's SQL uses UTC ({utc})"


def test_fee_levers_say_which_years_they_cover():
    """insights.fee_levers said "last two years" but summed every year with no year on the total. Now it counts every
    open finding and says the period: the first month of the payment fees, each code's years for the fees below a rate."""
    import sqlite3
    conn = sqlite3.connect(":memory:")
    ensure_schema(conn)
    rows = [("f23", "FEE_SKIM", "fees", "2023-05", "2023", 5000), ("f26", "FEE_SKIM", "fees", "2026-09", "2026", 700),
            ("c23", "CHARGE_BELOW_RATE", "contract", None, "2023", 9000), ("c26", "CHARGE_BELOW_RATE", "contract", None, "2026", 300)]
    conn.executemany("""INSERT INTO findings (finding_id, signal, category, status, month, year, shortfall_cents, payer_group, procedure_code, title, stale)
                        VALUES (?, ?, ?, 'open', ?, ?, ?, 'Anthem / BCBS', '88305', 't', 0)""", rows)
    out = insights.fee_levers(conn)
    assert (out["payment_fee_cents"], out["charge_cap_cents"]) == (5700, 9300)
    assert out["payment_fee_since"] == "2023-05" and (out["charge_caps"][0]["first_year"], out["charge_caps"][0]["year"]) == ("2023", "2026")


def test_contract_levers_use_the_last_full_year():
    import sqlite3
    conn = sqlite3.connect(":memory:")
    ensure_schema(conn)
    assert insights.contract_levers(conn, "2026-10-09")["year"] == "2025"
    assert insights.contract_levers(conn, "2027-01-05")["year"] == "2026"
    assert insights.contract_levers(conn, "2026-12-03")["year"] == "2025", "with the newest payment on 3 December the current year is not a full year"
