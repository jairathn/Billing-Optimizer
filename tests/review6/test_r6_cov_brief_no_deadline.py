"""/api/brief lists open work "by the kind of work it takes" and gives a kind to DENIED_NONCOVERED, DENIED_TIMELY_FILING,
DENIED_PROVIDER, DENIED_DUPLICATE_UNPAID, DENIED_OTHER, DENIED_MISSING_PRIMARY, CODE_CHANGED and the whole-claim denials
the detector marks "review". The detector gives none of these a filing deadline, and insights.work() keeps only rows
with appeal_deadline >= today, so they are never in the work, never in "expired", and the kinds written for them are
never used. On the archive that leaves out 2,248 open findings worth $292,500, more than the $238,900 the brief shows.
Invented data only."""
from __future__ import annotations

import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from r6cov_h import add_file, clean_visits, clm, pcn, sv, utc_today, ymd  # noqa: E402

from remitwatch import insights  # noqa: E402
from remitwatch.detect import Detector  # noqa: E402
from remitwatch.store import Store  # noqa: E402


def test_open_findings_without_a_deadline_still_show_as_work(tmp_path):
    db = str(tmp_path / "s.sqlite")
    today = utc_today()
    dos, paid = ymd(today - timedelta(days=40)), ymd(today - timedelta(days=30))
    add_file(tmp_path, db, clean_visits(100, 10, "99213", "150.00", "90.00", dos), paid)
    add_file(tmp_path, db, [
        clm(pcn(1), "1", sv("99213", "150.00", "0.00", cas="CAS*CO*197*150.00~", dos=dos), dos=dos),   # no authorization: appeal, has a deadline
        clm(pcn(2), "1", sv("99213", "150.00", "0.00", cas="CAS*CO*96*150.00~", dos=dos), dos=dos),    # not covered
        clm(pcn(3), "1", sv("99213", "150.00", "0.00", cas="CAS*CO*29*150.00~", dos=dos), dos=dos),    # timely filing
        clm(pcn(4), "1", sv("99213", "150.00", "0.00", cas="CAS*CO*18*150.00~", dos=dos), dos=dos),    # a duplicate never paid
    ], paid)
    st = Store(db)
    Detector(st, run_id="r6").run()
    found = {r[0]: (r[1], r[2], r[3]) for r in st.conn.execute(
        "SELECT signal, actionable, appeal_deadline, status FROM findings WHERE category='denial'")}
    assert set(found) == {"DENIED_AUTH", "DENIED_NONCOVERED", "DENIED_TIMELY_FILING", "DENIED_DUPLICATE_UNPAID"}, found
    assert all(v[1] is None for k, v in found.items() if k != "DENIED_AUTH") and all(v[2] == "open" for v in found.values())
    b = insights.brief(st.conn, today=today.isoformat())
    st.close()
    kinds = {t["key"]: t["n"] for t in b["work"]["types"]}
    assert set(found) <= getattr(insights, "WORK_SIGNALS", set(found))     # the brief has a kind written for every one of them
    # the three with no filing date are not hidden: the home screen counts them as items to decide (paid in the last
    # 120 days) and as items with no filing date
    assert b["work"]["total"]["n"] + b["work"]["expired_n"] + b["work"]["undated_n"] == 4, \
        f"four open denials; the brief shows {b['work']['total']['n']} as work, {b['work']['expired_n']} expired, {b['work']['undated_n']} undated ({kinds})"
    assert b["decide"]["n"] == 3 and b["work"]["undated_n"] == 3
