"""The EMA coverage record (meta 'ema:coverage') is written at the end of link.build_links, which Detector.run calls inside
a try that swallows every exception. When building the links fails (a locked database, memory on the serverless host),
the run still completes and the record measured by an earlier run stays, with nothing in it saying when it was measured.
/api/coverage and the Data tab then keep telling the practice administrator to pull again a week whose export has since
arrived. Invented data only."""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from r6cov_h import add_file, charge_row, client, clm, pcn, sv  # noqa: E402

import remitwatch.link as link  # noqa: E402
from remitwatch.detect import Detector  # noqa: E402
from remitwatch.store import Store  # noqa: E402

MON = date(2026, 8, 3)


def test_a_failed_measurement_does_not_leave_last_weeks_list_looking_current(tmp_path, monkeypatch):
    db = str(tmp_path / "s.sqlite")
    weeks = [MON + timedelta(weeks=k) for k in range(3)]
    day = lambda k: (weeks[k] + timedelta(days=1))            # noqa: E731
    claims = [clm(pcn(100 * k + j), "1", sv("99213", "150.00", "72.00", "90.00", "CAS*CO*45*60.00~", dos=day(k).strftime("%Y%m%d")),
                  dos=day(k).strftime("%Y%m%d"), charge="150.00", paid="72.00") for k in range(3) for j in range(40)]
    for i in range(0, len(claims), 60):
        add_file(tmp_path, db, claims[i:i + 60], "20261015")
    st = Store(db)
    st.ingest_ema_rows([charge_row("Encounter 8-21.xlsx", 100 * k + j, day(k).isoformat(), pcn(100 * k + j))
                        for k in range(3) for j in range(40 if k != 1 else 10)])
    Detector(st, run_id="first").run()
    st.close()
    tc, auth = client(monkeypatch, db)
    second = weeks[1].isoformat()
    before = tc.get("/api/coverage", auth=auth).json()["ema"]
    assert {w["week"]: w["in_ema"] for w in before["weeks"]}[second] == 10          # listed: 10 of 40

    # the administrator pulls the week again; the next run cannot build the links
    st = Store(db)
    st.ingest_ema_rows([charge_row("Encounter 8-10 again.xlsx", 1000 + j, day(1).isoformat(), pcn(100 + j)) for j in range(40)])

    def locked(*_a, **_k):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(link, "build_links", locked)
    Detector(st, run_id="second").run()
    runs = [r[0] for r in st.conn.execute("SELECT run_id FROM detection_runs ORDER BY finished_at, run_id")]
    assert set(runs) == {"first", "second"}                                      # the run completed
    assert {w["week"]: w["in_ema"] for w in link.ema_coverage(st.conn)["weeks"]}[second] == 40     # measured now: complete
    st.close()
    served = tc.get("/api/coverage", auth=auth).json()["ema"]
    still = {w["week"]: w["in_ema"] for w in served["weeks"]}.get(second)
    assert still == 40 or any(k in served for k in ("measured_at", "run_id")), \
        f"after the re-pull and a completed run the Data tab still lists the week at {still} of 40, with nothing saying when that was measured"
