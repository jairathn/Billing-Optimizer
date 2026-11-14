"""/api/brief counts findings already marked "appealed" as falling due (due7, due30) and, once their window has passed, as
expired (expired_n, expired_cents). An appeal filed in time is waiting on the payer: nothing falls due, and its money is
not lost to an expired window. Progress leaves appealed findings out of both its live and its expired figures, so the
home screen and Progress would disagree as soon as the billers record appeals (none is recorded on the archive yet).
Invented data only."""
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


def test_an_appeal_filed_in_time_is_neither_due_nor_expired(tmp_path, monkeypatch):
    db = str(tmp_path / "s.sqlite")
    today = utc_today()
    add_file(tmp_path, db, clean_visits(100, 10, "99213", "150.00", "90.00", ymd(today - timedelta(days=60))), ymd(today - timedelta(days=50)))
    # Anthem commercial: two years from the remittance. One window closes in 5 days, the other closed 10 days ago.
    for n, back in ((1, 725), (2, 740)):
        paid, dos = today - timedelta(days=back), today - timedelta(days=back + 10)
        add_file(tmp_path, db, [clm(pcn(n), "1", sv("99213", "150.00", "0.00", cas="CAS*CO*197*150.00~", dos=ymd(dos)), dos=ymd(dos))], ymd(paid))
    st = Store(db)
    Detector(st, run_id="r6").run()
    rows = st.conn.execute("SELECT finding_id, patient_control_number, appeal_deadline FROM findings WHERE signal='DENIED_AUTH' ORDER BY 2").fetchall()
    assert [r[2] for r in rows] == [(today + timedelta(days=5)).isoformat(), (today - timedelta(days=10)).isoformat()]
    for fid, _p, _d in rows:            # both appealed in time
        assert apply_status(st.conn, fid, "appealed", actor="biller", at=(today - timedelta(days=15)).isoformat() + "T15:00:00+00:00")
    st.conn.commit()
    b = insights.brief(st.conn, today=today.isoformat())
    st.close()
    tc, auth = client(monkeypatch, db)
    prog = tc.get("/api/progress", auth=auth).json()
    assert prog["expired"]["n"] == 0 and prog["live"]["n"] == 0        # Progress: an appealed finding is neither
    assert b["work"]["sent_n"] == 2 and b["work"]["total"]["n"] == 0      # both wait on the payer: neither is work to do
    assert (b["work"]["total"]["due7_n"], b["work"]["expired_n"]) == (0, 0), \
        f"appealed findings counted as due in 7 days ({b['work']['total']['due7_n']}) and as expired ({b['work']['expired_n']}, {b['work']['expired_cents']} cents)"
