"""The day the remittances reach, and the day it is (review of the live hotfix).

mr-1   a payment date no calendar has (BPR16 20260931, 20270229) never stops the analysis, the alerts or a page
mr-2   the Data tab's feed check, Today's banner, the brief and the warning line go by the day the feed reaches
mr-8   days behind and the clamp of a future-dated file count from the practice's day, not the UTC date
Both hosts: the server's store file and the serverless site (the compact store image its cycle keeps, the dashboard's
copy). Invented data only: payers, claim numbers and amounts are made up."""
from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta

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


# =========================================================================== mr-2: the feed check, the banner, the brief
def _three(pay: str) -> dict[str, bytes]:
    """Three payers whose remittances all stop on one day, 25 visits each."""
    return {f"p{k}.RMT": remit("".join(clean_visits(20000 + 100 * k, 25, "99214", "223.65", "117.60")), "100.00", payer=payer,
                               seg_count=99, isa_ctrl=f"{700000000 + k:09d}", pay_date=pay)
            for k, payer in enumerate(("ANTHEM", "KY MEDICARE", "HUMANA"))}


def _stray(when: str) -> str:
    """A stray remittance's day as its file writes it: two days ago where the practice is, or far in the future."""
    return (date.fromisoformat(runtime.practice_today()) - timedelta(days=2)).strftime("%Y%m%d") if when == "recent" else "20991231"


def _denied(pay: str) -> dict[str, bytes]:
    """One Anthem visit denied for want of an authorization, paid (at $0) on `pay`: a cause the home screen counts."""
    return {"denied.RMT": remit(claim(950, svc("99214", "223.65", "0.00", None, "CAS*CO*197*223.65~")), "0.00", seg_count=99,
                                isa_ctrl="000000950", pay_date=pay)}


@pytest.mark.parametrize("host", HOSTS, indirect=True)
@pytest.mark.parametrize("when", ["recent", "future"])
def test_the_feed_check_and_the_brief_go_by_the_day_the_feed_reaches(host, when):
    """Three payers whose remittances stop on September 1, 2026 and one stray file from a fourth, small one (dated two days
    ago, or in 2099). The Data tab's feed check fails with the day the feed reaches (it said "Payments are current"),
    Today's banner leads with it, and the brief carries that day for the warning line; the newest payment stays the
    newest payment. The home screen's windows end there too: the 12 full months of payment fees (the same on the Fees
    page), the last full year of payer rates, and the 90 days of denial causes."""
    stray = _stray(when)
    c, h = host({**_three("20260901"), **_denied("20260901"), **_one("stray.RMT", stray)})
    b = c.get("/api/brief", headers=h).json()
    assert b.get("feed_date") == "2026-09-01" and b["newest_payment"] == f"{stray[:4]}-{stray[4:6]}-{stray[6:]}", b.get("feed_date")
    bad = [x for x in b["health"] if not x["ok"]]
    assert bad and bad[0]["key"] == "feed", [x["key"] for x in bad]          # the first check Today's banner shows
    feed = bad[0]
    assert feed.get("feed") == "2026-09-01" and feed["title"].startswith("The remittances reach 2026-09-01 for most payers ("), feed["title"]
    assert feed["days"] == (date.fromisoformat(b["today"]) - date(2026, 9, 1)).days
    assert "only a few payers" in feed["detail"] and "Payments are current" not in json.dumps(b["health"])
    months = insights.fee_months("2026-09-01")
    assert (b["fees"]["payment_fee12_from"], b["fees"]["payment_fee12_to"]) == months == ("2025-09", "2026-08")
    fp = c.get("/api/fees/payers", headers=h).json()
    assert (fp["from"], fp["to"]) == months
    assert b["contracts"]["year"] == "2025"
    assert "DENIED_AUTH" in [x["signal"] for x in b["causes"]], b["causes"]


def _browser_ready() -> bool:
    return bool(shutil.which("node")) and subprocess.run(["node", "-e", "require('playwright')"], capture_output=True).returncode == 0


def _pages(env_extra: dict) -> dict:
    """Serve the app (uvicorn, as both hosts do) and read Today's banner and the Worklist's warning line in a browser
    (tests/feed_page.js)."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DROPBOX_", "TRIZETTO_", "SRFAX_", "ANTHROPIC_", "SMTP_", "MODMED_"))
           and k not in ("REPORT_TO", "ALERT_WEBHOOK_URL", "REMITWATCH_MODE", "REMITWATCH_OWNER_PASSWORD", "REMITWATCH_SHOW_PHI",
                         "REMITWATCH_SYNC_INTERVAL_MINUTES", "REMITWATCH_ALLOW_OPEN", "REMITWATCH_DB", "REMITWATCH_STATE_DIR")}
    env.update(REMITWATCH_PASSWORD=PW, REMITWATCH_USER="oda", PYTHONDONTWRITEBYTECODE="1", **env_extra)
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    srv = subprocess.Popen([sys.executable, "-B", "-m", "uvicorn", "remitwatch.api:app", "--host", "127.0.0.1", "--port", str(port),
                            "--log-level", "warning"], cwd=repo, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for _ in range(150):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        got = subprocess.run(["node", os.path.join(repo, "tests", "feed_page.js"), str(port), "oda", PW], capture_output=True, text=True,
                             timeout=240)
        assert got.returncode == 0, got.stderr[-2000:]
        return json.loads(got.stdout.strip().splitlines()[-1])
    finally:
        srv.terminate()
        srv.wait(timeout=10)


@pytest.mark.skipif(not _browser_ready(), reason="needs node and Playwright for node")
@pytest.mark.parametrize("on", HOSTS)
def test_the_pages_say_how_far_the_remittances_reach(tmp_path, box, on):  # noqa: F811
    """In a browser, on both hosts: Today's banner leads with the day the feed reaches, and the Worklist's warning line says
    payments after that day are not all in (before, one stray file hid both)."""
    files = {**_three("20260901"), **_one("stray.RMT", _stray("recent"))}
    if on == "server":
        s = _store(tmp_path, files)
        Detector(s, run_id="t").run()
        s.close()
        env = {"REMITWATCH_DB": str(tmp_path / "s.sqlite")}
    else:
        root, client, state = box
        (root / DATA).mkdir(parents=True, exist_ok=True)
        for name, data in files.items():
            (root / DATA / name).write_bytes(data)
        cycle(state, client)
        env = {"REMITWATCH_MODE": "serverless", "REMITWATCH_STATE_DIR": str(root / "RemitWatch-state"),
               "REMITWATCH_TMP": str(root.parent / "site-scratch")}
    seen = _pages(env)
    assert seen["errors"] == [] and seen["failed"] == [], (seen["errors"], seen["failed"])
    assert seen["banner"].startswith("Before you start: The remittances reach 2026-09-01 for most payers ("), seen["banner"]
    assert "Payments after Sep 1, 2026 are not all in yet; some items may already be paid." in seen["line"], seen["line"]


# =========================================================================== mr-8: the practice's day
EVENING = "2026-11-23T01:30:00+00:00"           # 8:30 PM Eastern on Sunday, November 22, 2026: already the 23rd in UTC


def _at(monkeypatch, iso: str) -> None:
    """Move the clock the practice's day is read from (runtime._utc_now), as tests/test_hotfix2c.py does."""
    t = datetime.fromisoformat(iso)
    monkeypatch.setattr(runtime, "_utc_now", lambda: t)


@pytest.mark.parametrize("host", HOSTS, indirect=True)
def test_days_behind_count_from_the_practice_s_day_in_the_evening(host, monkeypatch):
    """At 8:30 PM Eastern, remittances paid every other day to November 8 are 14 days behind where the practice is. The
    sync status on both hosts and the fax hold count 14, as the stale-feed alert does (they counted 15 from the UTC
    date, and held an appeal the alert called current)."""
    _at(monkeypatch, EVENING)
    assert runtime.practice_today() == "2026-11-22"
    c, h = host({**_steady("2026-11-08"), **_downcodes("20261108")})
    st = c.get("/api/sync/status", headers=h).json()
    assert (st["feed_date"], st["days_behind"]) == ("2026-11-08", 14), st
    from remitwatch import api
    assert api._data_behind_days() == 14
    assert "STALE_FEED" not in [a["kind"] for a in c.get("/api/alerts", headers=h).json()["items"]]
    items = c.get("/api/findings?signal=DOWNCODE_LEVEL_REDUCED", headers=h).json()["items"]
    r = c.post("/api/appeals/send", json={"finding_id": items[0]["finding_id"]}, headers=h).json()
    assert r["status"] in ("held", "sent") and r.get("code") != "stale", r


@pytest.mark.parametrize("kind", KINDS)
def test_with_no_day_given_the_feed_date_and_the_alerts_take_the_practice_s_day(tmp_path, monkeypatch, kind):
    """At 8:30 PM Eastern on November 22, a file dated November 23 (the UTC day, the practice's tomorrow) counts as
    November 22: in the feed date with no day given, in the date the analysis saves, in the alert engine's own day and,
    on a server, in the sync status (they took the UTC date)."""
    from remitwatch import pipeline
    from remitwatch.alerts import AlertEngine
    _at(monkeypatch, EVENING)
    s = _store(tmp_path, {**_one("a.RMT", "20261120", payer="ANTHEM", n=910), **_one("b.RMT", "20261123", n=911)}, kind)
    assert feed_date(s.conn) == "2026-11-22"
    Detector(s, run_id="t").run()
    assert _saved_feed(s.conn) == "2026-11-22"
    assert AlertEngine(s.conn).today == "2026-11-22"
    s.close()
    if kind == "file":
        st = pipeline.status(str(tmp_path / "s.sqlite"))
        assert (st["feed_date"], st["days_behind"]) == ("2026-11-22", 0), st
