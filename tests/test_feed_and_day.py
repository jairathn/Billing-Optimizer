"""The day the remittances reach, and the day it is (review of the live hotfix).

mr-1   a payment date no calendar has (BPR16 20260931, 20270229) never stops the analysis, the alerts or a page
Both hosts: the server's store file and the serverless site (the compact store image its cycle keeps, the dashboard's
copy). Invented data only: payers, claim numbers and amounts are made up."""
from __future__ import annotations

import base64
import json
import sqlite3
from datetime import date, timedelta

import pytest

from remitwatch import cloud, insights, runtime
from remitwatch.alerts import run_alerts
from remitwatch.detect import Detector, feed_date
from remitwatch.store import Store
from tests.test_cloud import DATA, PW, box, cycle, visits_with_downcodes  # noqa: F401  (box is a fixture)
from tests.test_features import claim, clean_visits, svc
from tests.test_remitwatch import remit
from tests.test_workable import charge

KINDS = ["file", "compact"]
HOSTS = ["server", "serverless"]
XHR = {"X-Requested-With": "remitwatch"}


def _steady(until: str, start: str = "2026-06-01", hole: tuple | None = None) -> dict[str, bytes]:
    """Three payers paying every other day from `start` to `until`, one remittance each, with nothing at all strictly inside
    `hole` (weeks a person forgot to put in the folder)."""
    out, day, k = {}, date.fromisoformat(start), 0
    while day.isoformat() <= until:
        iso = day.isoformat()
        if not (hole and hole[0] < iso < hole[1]):
            for j, payer in enumerate(("ANTHEM", "KY MEDICARE", "HUMANA")):
                k += 1
                out[f"f{k:04d}.RMT"] = remit("".join(clean_visits(10000 + 10 * k + j, 1, "99214", "223.65", "117.60")), "100.00",
                                             payer=payer, seg_count=99, isa_ctrl=f"{k:09d}", pay_date=iso.replace("-", ""))
        day += timedelta(days=2)
    return out


def _one(name: str, pay: str, payer: str = "AETNA", n: int = 900) -> dict[str, bytes]:
    """One remittance from one payer, paid on `pay` as the file writes it (CCYYMMDD), whatever day that is."""
    return {name: remit(claim(n, svc("99213", "120.00", "60.00", "60.00", "CAS*CO*45*60.00~")), "60.00", payer=payer, seg_count=99,
                        isa_ctrl=f"{900000000 + n:09d}", pay_date=pay)}


def _downcodes(pay: str) -> dict[str, bytes]:
    """Anthem visits paid on `pay`, three of them cut for the level: items a person can appeal by fax."""
    claims = visits_with_downcodes()
    return {f"d{n:03d}.RMT": remit("".join(claims[i:i + 10]), "100.00", seg_count=99, isa_ctrl=f"{800000000 + n:09d}", pay_date=pay)
            for n, i in enumerate(range(0, len(claims), 10))}


def _store(tmp_path, files: dict[str, bytes], kind: str = "file") -> Store:
    """The files read into a server's store file, or into the compact store image the serverless cycle keeps."""
    paths = []
    for name, data in files.items():
        (tmp_path / name).write_bytes(data)
        paths.append(str(tmp_path / name))
    s = Store(str(tmp_path / "s.sqlite")) if kind == "file" else Store(":memory:", compact=True)
    s.ingest_835_paths(paths)
    return s


def _server(tmp_path, monkeypatch, files: dict[str, bytes]):
    """The API on a server's store file: the files read, an EMA row (so the open-claim pass runs), one analysis, the alerts."""
    from fastapi.testclient import TestClient

    from remitwatch import api
    s = _store(tmp_path, files)
    charge(s.conn, "x", "CB0000700C017", "2026-09-20", "2026-09-21")
    s.conn.commit()
    Detector(s, run_id="t").run()
    run_alerts(s.conn, today=runtime.practice_today())
    s.conn.commit()
    s.close()
    db = str(tmp_path / "s.sqlite")
    api.ensure_server_schema(db)
    monkeypatch.delenv("REMITWATCH_MODE", raising=False)
    monkeypatch.setattr(api, "DB_PATH", db)
    monkeypatch.setattr(api, "PASSWORD", "")
    return TestClient(api.app), dict(XHR)


def _serverless(box, monkeypatch, files: dict[str, bytes]):  # noqa: F811
    """The same files read by a serverless cycle into the state folder; the API then serves the dashboard's copy."""
    from fastapi.testclient import TestClient

    from remitwatch import api
    root, client, state = box
    (root / DATA).mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (root / DATA / name).write_bytes(data)
    cycle(state, client)
    monkeypatch.setenv("REMITWATCH_MODE", "serverless")
    monkeypatch.setenv("REMITWATCH_STATE_DIR", str(root / "RemitWatch-state"))
    monkeypatch.setattr(api, "PASSWORD", PW)
    monkeypatch.setattr(api, "USERNAME", "oda")
    monkeypatch.setattr(cloud, "SERVING_COPY", cloud.ServingCopy())
    return TestClient(api.app), dict(XHR, Authorization="Basic " + base64.b64encode(f"oda:{PW}".encode()).decode())


@pytest.fixture
def host(request, tmp_path, monkeypatch, box):  # noqa: F811
    """Both hosts, one at a time: make(files) builds the site and gives (client, headers)."""
    return lambda files: (_server(tmp_path, monkeypatch, files) if request.param == "server" else _serverless(box, monkeypatch, files))


def _saved_feed(conn: sqlite3.Connection) -> str | None:
    r = conn.execute("SELECT value FROM meta WHERE key='feed:date'").fetchone()
    return json.loads(r[0])["date"] if r else None


# =========================================================================== mr-1: a payment date no calendar has
# (the steady feed's last day, a day the analysis runs on), with the date no calendar has inside the 120-day window
BAD = {"20260931": ("2026-11-20", "2026-11-23"), "20270229": ("2027-03-18", "2027-03-22")}


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("bad", sorted(BAD))
def test_a_payment_date_no_calendar_has_is_not_a_day_the_feed_reaches(tmp_path, kind, bad):
    """Two remittances whose BPR16 is a day no calendar has (one from a payer that pays every other day, one from a payer
    with no other file), inside the window: the store keeps what the files say, and the feed date and the alerts skip it."""
    until, today = BAD[bad]
    s = _store(tmp_path, {**_steady(until), **_one("odd1.RMT", bad), **_one("odd2.RMT", bad, payer="ANTHEM", n=901)}, kind)
    odd = f"{bad[:4]}-{bad[4:6]}-{bad[6:]}"
    assert s.conn.execute("SELECT COUNT(*) FROM transactions WHERE payment_date=?", (odd,)).fetchone()[0] == 2
    assert feed_date(s.conn, today=today) == until
    s.conn.execute("UPDATE files SET ingested_at=?", (today + "T10:00:00+00:00",))      # read that day: only the reach counts
    out = run_alerts(s.conn, today=today)
    s.conn.commit()
    assert out["asof"] == until
    assert s.conn.execute("SELECT COUNT(*) FROM alerts WHERE kind='STALE_FEED' AND status!='cleared'").fetchone()[0] == 0
    s.close()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("bad", sorted(BAD))
def test_the_newest_payment_date_no_calendar_has_is_never_the_day_the_feed_reaches(tmp_path, kind, bad):
    """Three payers paid on one day; a fourth's only remittance is dated after it, on a day no calendar has. The feed
    reaches the real day (not the one no calendar has), and the newest payment the pages count from is a real day."""
    day = f"{bad[:4]}-{bad[4:6]}-20"
    files = {}
    for k, payer in enumerate(("ANTHEM", "KY MEDICARE", "HUMANA")):
        files[f"r{k}.RMT"] = remit("".join(clean_visits(10000 + 100 * k, 5, "99214", "223.65", "117.60")), "100.00", payer=payer,
                                   seg_count=99, isa_ctrl=f"{k + 1:09d}", pay_date=day.replace("-", ""))
    s = _store(tmp_path, {**files, **_one("odd.RMT", bad)}, kind)
    assert s.conn.execute("SELECT MAX(payment_date) FROM transactions").fetchone()[0] == f"{bad[:4]}-{bad[4:6]}-{bad[6:]}"
    assert feed_date(s.conn, today=BAD[bad][1]) == day
    assert insights.newest_payment(s.conn) == day
    out = run_alerts(s.conn, today=BAD[bad][1])
    assert out["asof"] == day
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_a_payer_whose_newest_date_no_calendar_has_counts_with_its_real_newest_day(tmp_path, kind):
    """The payer carrying most of the lines stopped on September 1; its one later remittance is dated September 31. It still
    counts, as current through September 1, so the feed reaches September 1 (left out, the three small payers paid every
    other day to November 20 would make the whole feed look current)."""
    big = {f"big{n}.RMT": remit("".join(clean_visits(30000 + 100 * n, 50, "99214", "223.65", "117.60")), "100.00", payer="AETNA",
                                seg_count=99, isa_ctrl=f"{600000000 + n:09d}", pay_date=f"202608{n + 10:02d}") for n in range(6)}
    big["big9.RMT"] = remit("".join(clean_visits(30900, 50, "99214", "223.65", "117.60")), "100.00", payer="AETNA", seg_count=99,
                            isa_ctrl="600000009", pay_date="20260901")
    s = _store(tmp_path, {**_steady("2026-11-20"), **big}, kind)
    assert feed_date(s.conn, today="2026-11-23") == "2026-09-01"
    s.ingest_835_paths([str(p) for p in _written(tmp_path, _one("odd.RMT", "20260931", n=905))])
    assert s.conn.execute("SELECT MAX(payment_date) FROM transactions WHERE payer_name='AETNA'").fetchone()[0] == "2026-09-31"
    assert feed_date(s.conn, today="2026-11-23") == "2026-09-01"
    s.close()


def _written(tmp_path, files: dict[str, bytes]) -> list:
    for name, data in files.items():
        (tmp_path / name).write_bytes(data)
    return [tmp_path / name for name in files]


@pytest.mark.parametrize("kind", KINDS)
def test_detection_runs_over_payment_dates_no_calendar_has(tmp_path, kind):
    """The reviewer's case: one remittance dated September 31 inside the window, one dated February 29, 2027 (after today),
    and an EMA row so the open-claim pass runs. The analysis finishes and saves the day the feed reaches."""
    s = _store(tmp_path, {**_steady("2026-11-20"), **_one("odd1.RMT", "20260931"), **_one("odd2.RMT", "20270229", payer="ANTHEM", n=901)},
               kind)
    charge(s.conn, "x", "CB0000700C017", "2026-09-20", "2026-09-21")
    s.conn.commit()
    Detector(s, run_id="t").run()
    assert _saved_feed(s.conn) == "2026-11-20"
    assert run_alerts(s.conn, today=runtime.practice_today())["asof"] == "2026-11-20"
    s.close()


@pytest.mark.parametrize("host", HOSTS, indirect=True)
def test_both_hosts_keep_working_over_payment_dates_no_calendar_has(host):
    """The pages that count days behind (the brief, the sync status, the Faxes page, Appeal by fax) answer on both hosts,
    and the day the feed reaches is the real one. A dashboard copy with no saved feed date (an analysis before this
    release) falls back to the newest payment that is a real day."""
    c, h = host({**_steady("2026-11-20"), **_downcodes("20261118"), **_one("odd1.RMT", "20260931"),
                 **_one("odd2.RMT", "20270229", payer="ANTHEM", n=901)})
    b = c.get("/api/brief", headers=h)
    assert b.status_code == 200 and b.json()["ready"] is True
    st = c.get("/api/sync/status", headers=h)
    assert st.status_code == 200 and st.json()["feed_date"] == "2026-11-20", st.text
    assert c.get("/api/faxes", headers=h).status_code == 200
    items = c.get("/api/findings?signal=DOWNCODE_LEVEL_REDUCED", headers=h).json()["items"]
    assert items
    r = c.post("/api/appeals/send", json={"finding_id": items[0]["finding_id"]}, headers=h)
    assert r.status_code == 200 and r.json()["status"] in ("held", "sent"), r.text
    if "Authorization" in h:            # the serverless host: its copy without the date the analysis saved
        copy = sqlite3.connect(cloud.SERVING_COPY.ensure())
        copy.execute("DELETE FROM meta WHERE key='feed:date'")
        copy.commit()
        copy.close()
        st = c.get("/api/sync/status", headers=h)
        assert st.status_code == 200 and st.json()["feed_date"] == "2026-11-20", st.text
        assert c.get("/api/brief", headers=h).status_code == 200
