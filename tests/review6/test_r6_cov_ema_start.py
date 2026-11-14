"""remitwatch.link.ema_coverage starts the EMA exports at the first week holding a fifth of the busiest week's charge ROWS.
A row is stored again whenever its content changes between pulls (the fingerprint is every column but the file), so a
week pulled in both layouts and re-pulled as its claims move from submitted to paid holds several rows per charge. The
six-week rolling pull docs/MISSING_DATA.md now asks for does exactly that, and once the busiest week holds five times
the rows of the first weeks those weeks drop out of the measure, with the week pulled as it ended among them. On the
archive the start is right (27 July, the same as counting distinct claim ids) and the busiest week (14 September, seven
export files) holds 1.5 times the rows of the first, so the margin is wide today. Invented data only."""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from r6cov_h import add_file, charge_row, clm, pcn, sv  # noqa: E402

from remitwatch import link  # noqa: E402
from remitwatch.store import Store  # noqa: E402

MON = date(2026, 8, 3)


def test_re_pulled_weeks_do_not_push_the_start_of_the_exports_past_a_week_that_misses_visits(tmp_path):
    db = str(tmp_path / "s.sqlite")
    weeks = [MON + timedelta(weeks=k) for k in range(8)]
    claims = [clm(pcn(100 * k + j), "1", sv("99213", "150.00", "72.00", "90.00", "CAS*CO*45*60.00~", dos=(w + timedelta(days=1)).strftime("%Y%m%d")),
                  dos=(w + timedelta(days=1)).strftime("%Y%m%d"), charge="150.00", paid="72.00")
              for k, w in enumerate(weeks) for j in range(40)]
    for i in range(0, len(claims), 80):
        add_file(tmp_path, db, claims[i:i + 80], "20261015")
    rows, i = [], 0
    for k, w in enumerate(weeks):
        d = (w + timedelta(days=1)).isoformat()
        if k == 0:          # pulled once, late enough: every visit
            pulls = [("Encounter Charge Line Report 8-14.xlsx", None, None)]
            visits = range(40)
        elif k == 1:        # pulled as it ended: a quarter of the visits
            pulls = [("Encounter Charge Line Report 8-14.xlsx", None, None)]
            visits = range(10)
        else:               # the rolling six-week pull, in both layouts, as the claims move from submitted to paid
            pulls = [(f"Encounter full {s}.xlsx", s, "P") for s in ("Submitted", "Accepted", "Paid")] + \
                    [(f"Encounter Charge Line Report {s}.xlsx", s, None) for s in ("Submitted", "Accepted", "Paid")]
            visits = range(40)
        for src, status, pat in pulls:
            for j in visits:
                rows.append(charge_row(src, i := i + 1, d, pcn(100 * k + j), status=status, patient=(f"{pat}{100 * k + j}" if pat else None)))
    st = Store(db)
    st.ingest_ema_rows(rows)
    per_week = dict(st.conn.execute("SELECT service_date, COUNT(*) FROM ema_rows GROUP BY 1").fetchall())
    assert per_week[(MON + timedelta(days=1)).isoformat()] == 40 and per_week[(MON + timedelta(weeks=2, days=1)).isoformat()] == 240
    cov = link.ema_coverage(st.conn)
    st.close()
    listed = {w["week"]: (w["claims"], w["in_ema"]) for w in cov["weeks"]}
    second = (MON + timedelta(weeks=1)).isoformat()
    assert listed.get(second) == (40, 10), \
        f"the week pulled as it ended (10 of 40 visits) is not measured: the exports are taken to start on {cov['first_week']}"


def test_a_stray_older_row_does_not_move_the_start_of_the_exports(tmp_path):
    """One charge row from months before the exports begin (a late-billed old visit) must not make every week in
    between look missed."""
    db = str(tmp_path / "s.sqlite")
    weeks = [MON + timedelta(weeks=k) for k in range(-12, 3)]
    claims = [clm(pcn(1000 + 100 * k + j), "1", sv("99213", "150.00", "72.00", "90.00", "CAS*CO*45*60.00~", dos=(w + timedelta(days=1)).strftime("%Y%m%d")),
                  dos=(w + timedelta(days=1)).strftime("%Y%m%d"), charge="150.00", paid="72.00")
              for k, w in enumerate(weeks) for j in range(40)]
    for i in range(0, len(claims), 100):
        add_file(tmp_path, db, claims[i:i + 100], "20261015")
    rows = [charge_row("Encounter old.xlsx", 1, (weeks[0] + timedelta(days=1)).isoformat(), pcn(1000))]       # the stray row
    rows += [charge_row("Encounter 8-24.xlsx", 2 + 100 * k + j, (w + timedelta(days=1)).isoformat(), pcn(1000 + 100 * (k + 12) + j))
             for k, w in enumerate(weeks[12:]) for j in range(40)]
    st = Store(db)
    st.ingest_ema_rows(rows)
    cov = link.ema_coverage(st.conn)
    st.close()
    assert cov["first_week"] == MON.isoformat(), cov["first_week"]
    assert all(w["week"] >= MON.isoformat() for w in cov["weeks"])
