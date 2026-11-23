"""Hotfix 2c: a filing window closes at midnight where the practice is, windows stated in months or years are calendar
months, Medicare's 120 days run from presumed receipt, and follow-ups keep coming while the remittance feed is behind
(chaos round 0, findings 0f-1 to 0f-5). Both hosts: the server's store file and the serverless site with its dashboard
copy and journal. Invented data only."""
from __future__ import annotations

import base64
import json
import os
import sqlite3
import subprocess
from datetime import date, datetime, timedelta, timezone

import pytest

from remitwatch import fax, runtime
from remitwatch.codes import LEGAL, add_months, appeal_rule, corrected_rule, filing_rule, window_end
from tests.fake_fax import FakeLine, pdf_text
from tests.test_cloud import box  # noqa: F401  (the fixture: a fake Dropbox with the state folder)
from tests.test_features import claim, clean_visits, svc
from tests.test_remitwatch import remit

XHR = {"X-Requested-With": "remitwatch"}
PAYER_FAX = "(800) 555-0142"
MONEY = "category=denial,bundling,downcoding,underpayment"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _at(monkeypatch, iso: str) -> None:
    """Move the clock the practice's day is read from (runtime._utc_now)."""
    t = datetime.fromisoformat(iso)
    monkeypatch.setattr(runtime, "_utc_now", lambda: t)


def _claims(dos: str) -> list[str]:
    """Anthem commercial: a baseline of visits at the usual rate, and two visits allowed below it (two items to appeal)."""
    return clean_visits(1, 25, "99214", "223.65", "117.60", dos=dos) + [
        claim(202, svc("99214", "223.65", "110.00", "110.00", "CAS*CO*45*113.65~", dos=dos), dos=dos),
        claim(203, svc("99214", "223.65", "105.00", "105.00", "CAS*CO*45*118.65~", dos=dos), dos=dos)]


def _practice(monkeypatch) -> None:
    from remitwatch import appeals
    for k, v in {"name": "Test Dermatology", "address": "1 Main St, Anytown KY", "tin": "61-0000000", "npi": "1999999999",
                 "phone": "(270) 555-0100", "fax": "(270) 555-0101", "email": "billing@example.org"}.items():
        monkeypatch.setitem(appeals.PRACTICE, k, v)


def _server(tmp_path, monkeypatch, pay: date):
    """The API on a server's store file: the remittances paid on `pay`, a fake fax line, a practice with its details."""
    from fastapi.testclient import TestClient

    import remitwatch.api as api
    from remitwatch.detect import Detector
    from tests.test_features import build_store
    store = build_store(tmp_path, _claims((pay - timedelta(days=9)).strftime("%Y%m%d")), pay_date=pay.strftime("%Y%m%d"))
    Detector(store, run_id="t").run()
    store.close()
    db = str(tmp_path / "s.sqlite")
    monkeypatch.delenv("REMITWATCH_MODE", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(api, "DB_PATH", db)
    monkeypatch.setattr(api, "PASSWORD", "")
    _practice(monkeypatch)
    line = FakeLine()
    monkeypatch.setattr(fax, "provider", lambda env=None: line)
    monkeypatch.setattr(fax, "configured", lambda env=None: True)
    c = TestClient(api.app)
    ids = sorted(i["finding_id"] for i in c.get("/api/findings?signal=UNDERPAID_VS_OWN_RATE&status=all&limit=50").json()["items"])
    assert len(ids) == 2, ids
    return c, {}, line, ids, lambda sql, args=(): _write(db, sql, args)


def _serverless(box, monkeypatch, pay: date):  # noqa: F811
    """The same remittances read by a serverless cycle into the state folder; the API then serves the dashboard's copy and
    writes what people do to the journal."""
    from fastapi.testclient import TestClient

    from remitwatch import api, cloud
    from tests.test_cloud import DATA, PW, cycle
    root, client, state = box
    claims = _claims((pay - timedelta(days=9)).strftime("%Y%m%d"))
    (root / DATA).mkdir(parents=True, exist_ok=True)
    for n, i in enumerate(range(0, len(claims), 10)):
        (root / DATA / f"r{n:03d}.RMT").write_bytes(remit("".join(claims[i:i + 10]), "100.00", seg_count=99, isa_ctrl=f"{n + 1:09d}",
                                                          pay_date=pay.strftime("%Y%m%d")))
    cycle(state, client)
    monkeypatch.setenv("REMITWATCH_MODE", "serverless")
    monkeypatch.setenv("REMITWATCH_STATE_DIR", str(root / "RemitWatch-state"))
    monkeypatch.setattr(api, "PASSWORD", PW)
    monkeypatch.setattr(api, "USERNAME", "oda")
    monkeypatch.setattr(cloud, "SERVING_COPY", cloud.ServingCopy())
    _practice(monkeypatch)
    line = FakeLine()
    monkeypatch.setattr(fax, "provider", lambda env=None: line)
    monkeypatch.setattr(fax, "configured", lambda env=None: True)
    h = {"Authorization": "Basic " + base64.b64encode(f"oda:{PW}".encode()).decode()}
    c = TestClient(api.app)
    ids = sorted(i["finding_id"] for i in c.get("/api/findings?signal=UNDERPAID_VS_OWN_RATE&status=all&limit=50", headers=h).json()["items"])
    assert len(ids) == 2, ids
    copy = cloud.SERVING_COPY.ensure()          # the copy this instance serves: the test sets dates on it as the API sees them
    return c, h, line, ids, lambda sql, args=(): _write(copy, sql, args)


def _write(db: str, sql: str, args: tuple = ()) -> None:
    conn = sqlite3.connect(db)
    conn.execute(sql, args)
    conn.commit()
    conn.close()


HOSTS = ["server", "serverless"]


@pytest.fixture
def host(request, tmp_path, monkeypatch, box):  # noqa: F811
    """Both hosts, one at a time: make(pay) builds the site and gives (client, auth headers, fax line, item ids, write)."""
    return lambda pay: (_server(tmp_path, monkeypatch, pay) if request.param == "server" else _serverless(box, monkeypatch, pay))


def _ids(c, h, query: str) -> set:
    return {i["finding_id"] for i in c.get(f"/api/findings?{query}&limit=200", headers=h).json()["items"]}


# --------------------------------------------------------------------------- F1: one practice-local day (0f-1)

def test_the_practice_day_turns_over_at_midnight_where_the_practice_is(monkeypatch, capsys):
    from remitwatch import api, insights
    monkeypatch.delenv(runtime.TZ_SETTING, raising=False)
    _at(monkeypatch, "2026-11-25T00:15:00+00:00")           # 7:15 PM Eastern, Tuesday November 24
    assert runtime.practice_today() == api._day(0) == insights.utc_today() == "2026-11-24"
    assert api._day(7) == "2026-12-01" and api._day(-30) == "2026-10-25"
    _at(monkeypatch, "2026-11-25T05:05:00+00:00")           # 12:05 AM Eastern, Wednesday November 25
    assert runtime.practice_today() == api._day(0) == "2026-11-25"
    # summer: daylight time ends the day at 8 PM in UTC terms, not 7
    _at(monkeypatch, "2027-07-11T00:15:00+00:00")
    assert runtime.practice_today() == "2027-07-10"
    _at(monkeypatch, "2027-07-11T04:05:00+00:00")
    assert runtime.practice_today() == "2027-07-11"
    # year end: New Year's Eve evening is still the old year
    _at(monkeypatch, "2027-01-01T03:00:00+00:00")
    assert runtime.practice_today() == "2026-12-31"
    # another zone, by the setting
    monkeypatch.setenv(runtime.TZ_SETTING, "America/Chicago")
    _at(monkeypatch, "2026-11-25T05:30:00+00:00")           # 11:30 PM Central
    assert runtime.practice_today() == "2026-11-24"
    # a zone this host does not know: the default, with one warning in the log
    monkeypatch.setattr(runtime, "_TZ_WARNED", set())
    monkeypatch.setenv(runtime.TZ_SETTING, "Mars/Olympus_Base")
    _at(monkeypatch, "2026-11-25T00:15:00+00:00")
    assert runtime.practice_today() == runtime.practice_today() == "2026-11-24"
    err = capsys.readouterr().err
    assert err.count("REMITWATCH_TZ") == 1 and "America/New_York" in err
    monkeypatch.delenv(runtime.TZ_SETTING)
    # a time the store keeps (UTC) on the practice's own day, and the moment a practice day starts
    assert runtime.practice_date("2026-11-26T01:30:00+00:00") == "2026-11-25" and runtime.practice_date("2026-11-25") == "2026-11-25"
    assert runtime.practice_day_start("2026-11-24") == "2026-11-24T05:00:00+00:00"
    assert runtime.practice_day_start("2027-07-10") == "2027-07-10T04:00:00+00:00"


def test_with_no_time_zone_data_the_eastern_rule_still_holds(monkeypatch):
    """A host with no time zone data (and no tzdata package) still counts Eastern days, by the federal rule."""
    import zoneinfo

    def missing(name):
        raise zoneinfo.ZoneInfoNotFoundError(name)
    monkeypatch.setattr(zoneinfo, "ZoneInfo", missing)
    monkeypatch.setattr(runtime, "_TZ_WARNED", set())
    monkeypatch.delenv(runtime.TZ_SETTING, raising=False)
    for iso, day in (("2026-11-25T00:15:00+00:00", "2026-11-24"), ("2026-11-25T05:05:00+00:00", "2026-11-25"),
                     ("2027-07-11T00:15:00+00:00", "2027-07-10"), ("2027-07-11T04:05:00+00:00", "2027-07-11"),
                     ("2027-03-14T06:59:00+00:00", "2027-03-14"), ("2027-11-07T05:30:00+00:00", "2027-11-07")):
        _at(monkeypatch, iso)
        assert runtime.practice_today() == day, iso
    assert runtime.practice_day_start("2026-11-24") == "2026-11-24T05:00:00+00:00"


@pytest.mark.parametrize("host", HOSTS, indirect=True)
def test_an_item_due_today_stays_workable_until_midnight_eastern(host, monkeypatch):
    c, h, line, (a, b), write = host(date(2026, 11, 10))
    write("UPDATE findings SET appeal_deadline='2026-11-24' WHERE finding_id IN (?, ?)", (a, b))
    assert c.post("/api/faxes/payer", json={"payer_group": "Anthem / BCBS", "number": PAYER_FAX}, headers=dict(XHR, **h)).status_code == 200
    todo = f"{MONEY}&status=open,in_progress"
    _at(monkeypatch, "2026-11-25T00:15:00+00:00")           # 7:15 PM Eastern on the last day
    assert {a, b} <= _ids(c, h, f"{todo}&deadline=week") and {a, b} <= _ids(c, h, f"{todo}&deadline=valid")
    assert {a, b} <= _ids(c, h, f"{todo}&deadline=due") and not {a, b} & _ids(c, h, f"{todo}&deadline=expired")
    assert not {a, b} & _ids(c, h, f"{todo}&deadline=missed")
    assert c.get(f"/api/worklist/counts?{todo}&deadline=week", headers=h).json()["n"] >= 2
    tiles = c.get("/api/desk?category=underpayment&status=open,in_progress", headers=h).json()["tiles"]
    assert tiles["week"]["n"] >= 2 and tiles["expired"]["n"] == 0
    brief = c.get("/api/brief", headers=h).json()
    assert brief["today"] == "2026-11-24" and brief["work"]["total"]["due7_n"] >= 2 and brief["missed"]["n"] == 0
    # soonest first puts it with the open ones, not after them
    first = c.get(f"/api/findings?{todo}&sort=deadline&limit=5", headers=h).json()["items"][0]
    assert first["appeal_deadline"] == "2026-11-24"
    # Appeal by fax gets past the expiry check and goes; its letter and cover sheet carry the practice's date
    r = c.post("/api/appeals/send", json={"finding_id": a, "stale_ok": True}, headers=dict(XHR, **h)).json()
    assert r["status"] == "sent", r
    sent = pdf_text(line.to(PAYER_FAX)[0]["pdf"])
    assert "2026-11-24" in sent and "2026-11-25" not in sent
    # 12:05 AM Eastern the next day: closed, everywhere
    _at(monkeypatch, "2026-11-25T05:05:00+00:00")
    assert b not in _ids(c, h, f"{todo}&deadline=week") and b not in _ids(c, h, f"{todo}&deadline=valid")
    assert b in _ids(c, h, f"{todo}&deadline=expired") and b in _ids(c, h, f"{todo}&deadline=missed")
    assert c.get("/api/brief", headers=h).json()["missed"]["n"] >= 1
    r = c.post("/api/appeals/send", json={"finding_id": b, "stale_ok": True}, headers=dict(XHR, **h)).json()
    assert r["status"] == "held" and r["code"] == "expired" and "2026-11-24" in r["reason"], r
    assert len(line.sent) == 1


# --------------------------------------------------------------------------- F2: follow-ups while the feed is behind (0f-2)

def _sent_back(write, ids: list, day: date) -> None:
    """Move what was sent for these items (the faxes and the Sent dates) to the middle of `day`, the practice's day."""
    at = (datetime.fromisoformat(runtime.practice_day_start(day.isoformat())) + timedelta(hours=15)).astimezone(timezone.utc)
    q = ",".join("?" * len(ids))
    write(f"UPDATE faxes SET created_at=? WHERE finding_id IN ({q})", (at.isoformat(timespec="microseconds"), *ids))
    write(f"UPDATE findings SET appealed_at=?, status_changed_at=? WHERE finding_id IN ({q})",
          (at.isoformat(timespec="seconds"), at.isoformat(timespec="seconds"), *ids))


@pytest.mark.parametrize("host", HOSTS, indirect=True)
def test_follow_ups_keep_coming_while_the_remittances_are_behind(host):
    today = date.fromisoformat(runtime.practice_today())
    c, h, line, (a, b), write = host(today - timedelta(days=106))      # the newest payment is 106 days old
    hx = dict(XHR, **h)
    assert c.post("/api/faxes/payer", json={"payer_group": "Anthem / BCBS", "number": PAYER_FAX}, headers=hx).status_code == 200
    assert c.post("/api/appeals/send", json={"finding_id": a, "stale_ok": True}, headers=hx).json()["status"] == "sent"   # faxed
    assert c.post(f"/api/findings/{b}/status", json={"status": "appealed"}, headers=hx).status_code == 200    # mailed: marked Sent
    line.deliver_all()
    c.post("/api/faxes/refresh", headers=hx)
    sent_day = today - timedelta(days=61)
    _sent_back(write, [a, b], sent_day)
    # Today's step and the list it opens: both items, with a plain line that the remittances are behind
    fu = c.get("/api/brief", headers=h).json()["follow_ups"]
    assert fu["n"] == 2 and fu["behind_days"] >= 106 and "days old" in fu["note"] and "EMA" in fu["note"], fu
    assert _ids(c, h, f"{MONEY}&status=appealed&deadline=followup") == {a, b}
    tiles = c.get("/api/desk?category=underpayment&status=appealed", headers=h).json()["tiles"]
    assert tiles["followup"]["n"] == 2
    # sent 25 days ago: not yet
    _sent_back(write, [b], today - timedelta(days=25))
    assert _ids(c, h, f"{MONEY}&status=appealed&deadline=followup") == {a}
    assert c.get("/api/brief", headers=h).json()["follow_ups"]["n"] == 1
    # the faxed appeal's second request: listed for a person, never sent by the daily run while the feed is behind
    due = [d for d in c.get("/api/faxes", headers=h).json()["due"] if d["finding_id"] == a]
    assert len(due) == 1 and due[0]["action"] == "second_request" and due[0]["stale"] and "Still unpaid in EMA" in due[0]["hold"], due
    n0 = len(line.sent)
    run = c.post("/api/faxes/follow_up", json={}, headers=hx).json()
    assert run["sent"] == 0 and "days old" in run["results"][0]["reason"] and len(line.sent) == n0

    def one(**kw):
        return c.post("/api/faxes/follow_up", json=dict(finding_ids=[a], **kw), headers=hx).json()["results"][0]
    assert one(number=due[0]["number"])["status"] == "held"                         # a person, who has not said it is unpaid
    for not_true in ("true", 1, "yes"):
        assert one(number=due[0]["number"], stale_ok=not_true)["status"] == "held"   # only JSON true
    assert one(stale_ok=True)["status"] == "held"                                   # no number in front of a person: the run's send
    assert len(line.sent) == n0
    got = one(number=due[0]["number"], stale_ok=True)                               # checked in EMA: still unpaid
    assert got["status"] == "sent" and got["action"] == "second_request", got
    assert len(line.sent) == n0 + 1
    letter = pdf_text(line.to(PAYER_FAX)[-1]["pdf"])
    assert f"We faxed an appeal of this claim on {sent_day.isoformat()}" in " ".join(letter.split())


@pytest.mark.parametrize("host", HOSTS, indirect=True)
def test_a_request_the_line_did_not_take_waits_while_the_remittances_are_behind(host, monkeypatch):
    """Review mr-3: a second request a person sent after checking EMA, that the payer's line did not take (busy). While the
    remittances are behind, the fax run does not try it again by itself (it says the claim is unpaid, and the claim may
    have been paid since): it is listed for a person with why, and a person who checks EMA again sends it. Once the
    remittances are current, the run tries it again by itself, as the follow-ups go again then."""
    from remitwatch import api
    today = date.fromisoformat(runtime.practice_today())
    c, h, line, (a, b), write = host(today - timedelta(days=106))      # the newest payment is 106 days old
    hx = dict(XHR, **h)
    assert c.post("/api/faxes/payer", json={"payer_group": "Anthem / BCBS", "number": PAYER_FAX}, headers=hx).status_code == 200
    for fid in (a, b):
        assert c.post("/api/appeals/send", json={"finding_id": fid, "stale_ok": True}, headers=hx).json()["status"] == "sent"
    line.deliver_all()
    c.post("/api/faxes/refresh", headers=hx)
    _sent_back(write, [a, b], today - timedelta(days=61))
    number = {d["finding_id"]: d["number"] for d in c.get("/api/faxes", headers=h).json()["due"]}
    assert set(number) == {a, b}, number

    def person(fid):
        return c.post("/api/faxes/follow_up", json={"finding_ids": [fid], "number": number[fid], "stale_ok": True}, headers=hx).json()["results"][0]
    for fid in (a, b):                                           # checked in EMA: still unpaid, sent
        assert person(fid)["status"] == "sent"
    line.busy.add(fax.digits(PAYER_FAX))                         # the payer's line is busy: neither request gets through
    n0 = len(line.sent)
    for _ in range(2):                                           # the hourly fax run, twice
        got = c.post("/api/faxes/refresh", headers=hx).json()
        assert got["retried"] == 0, got
    assert len(line.sent) == n0, "a request saying the claim is unpaid was faxed again by itself while the remittances are behind"
    due = {d["finding_id"]: d for d in c.get("/api/faxes", headers=h).json()["due"]}
    for fid in (a, b):
        d = due[fid]
        assert d["action"] == "person" and d["resend"] == "second_request" and d["stale"] and "days old" in d["why"], d
    # a person checks EMA again and sends b; a stays for the run
    line.busy.clear()
    assert person(b)["status"] == "sent" and len(line.sent) == n0 + 1
    # the remittances are current again: the run tries a again by itself, once
    monkeypatch.setattr(api, "_data_behind_days", lambda: 0)
    got = c.post("/api/faxes/refresh", headers=hx).json()
    assert got["retried"] == 1 and len(line.sent) == n0 + 2, got
    assert c.post("/api/faxes/refresh", headers=hx).json()["retried"] == 0


@pytest.mark.parametrize("host", HOSTS, indirect=True)
def test_to_decide_counts_the_last_120_days_to_today(host):
    """While the feed is behind, To decide still counts what was paid in the 120 days to today (it said so; it counted to the
    newest payment); older ones go to the items with no filing date, paid before that."""
    today = date.fromisoformat(runtime.practice_today())
    c, h, _line, _ids2, write = host(today - timedelta(days=106))
    for fid, days in (("decide-recent", 100), ("decide-old", 150)):
        write("""INSERT INTO findings (finding_id, signal, category, severity, actionable, title, payer_group, payer_kind, status, stale,
                                       shortfall_cents, payment_date, appeal_deadline)
                 VALUES (?, 'CODE_CHANGED', 'denial', 'medium', 'review', 'invented', 'Anthem / BCBS', 'commercial', 'open', 0, 5000, ?, NULL)""",
              (fid, (today - timedelta(days=days)).isoformat()))
    dec = c.get("/api/brief", headers=h).json()["decide"]
    assert dec["n"] == 1 and dec["since"] == (today - timedelta(days=120)).isoformat() and dec["days"] == 120, dec
    todo = f"{MONEY}&status=open,in_progress"
    assert _ids(c, h, f"{todo}&deadline=decide") == {"decide-recent"}
    assert "decide-old" in _ids(c, h, f"{todo}&deadline=older") and "decide-recent" not in _ids(c, h, f"{todo}&deadline=older")


def test_people_s_changes_count_on_the_practice_s_day(tmp_path, monkeypatch):
    """Moved on today: a change made at 8 PM Eastern (01:00 UTC the next day) counts on the practice's day it was made."""
    import sqlite3 as sq

    from remitwatch import insights
    conn = sq.connect(":memory:")
    from remitwatch.detect import ensure_schema
    ensure_schema(conn)
    conn.execute("INSERT INTO findings (finding_id, signal, category, status, payer_name, title) VALUES ('x1', 'CODE_CHANGED', 'denial', 'appealed', 'P', 't')")
    conn.execute("INSERT INTO finding_events (finding_id, at, kind, from_status, to_status, amount_cents) VALUES ('x1', '2026-11-25T01:00:00+00:00', 'manual', 'open', 'appealed', 100)")
    conn.execute("INSERT INTO findings (finding_id, signal, category, status, payer_name, title) VALUES ('x2', 'CODE_CHANGED', 'denial', 'appealed', 'P', 't')")
    conn.execute("INSERT INTO finding_events (finding_id, at, kind, from_status, to_status, amount_cents) VALUES ('x2', '2026-11-23T04:30:00+00:00', 'manual', 'open', 'appealed', 100)")
    d = insights.done(conn, "2026-11-24")
    assert d["today_n"] == 1 and d["week_n"] == 1 and d["week_start"] == "2026-11-23"      # x2 was made on Sunday evening Eastern


# --------------------------------------------------------------------------- F3 and F4: how a window is counted (0f-3, 0f-4, 0f-5)

def test_windows_stated_in_months_or_years_are_calendar_months():
    assert window_end("2025-05-24", appeal_rule("Humana Medicare Advantage", "medicare_advantage")) == "2026-11-24"   # 18 months
    assert window_end("2025-05-24", appeal_rule("Humana", "commercial")) == "2026-11-24"
    assert window_end("2026-03-05", appeal_rule("Anthem / BCBS", "commercial")) == "2028-03-05"                     # two years, Feb 29
    assert window_end("2027-03-05", appeal_rule("UnitedHealthcare", "commercial")) == "2028-03-05"                  # 12 months
    assert window_end("2027-03-05", appeal_rule("UMR", "commercial")) == "2028-03-05"
    assert window_end("2027-03-05", appeal_rule("Optum (UHC)", "commercial")) == "2028-03-05"
    # first filing: one calendar year from the date of service (42 CFR 424.44(a)(1)); Humana Medicare Advantage; CHAMPVA
    assert window_end("2027-03-05", filing_rule("Medicare KY (CGS J15)", "medicare")) == "2028-03-05"
    assert window_end("2027-03-05", filing_rule("Humana Medicare Advantage", "medicare_advantage")) == "2028-03-05"
    assert window_end("2027-03-05", filing_rule("Champva", "other")) == "2028-03-05"
    for kind in ("medicare_advantage", "supplement", "tricare", "other"):       # the defaults stated as a year
        assert window_end("2027-03-05", filing_rule("Some Small Plan", kind)) == "2028-03-05", kind
    # corrected claims: Medicare's new claim (MA130) a calendar year from the visit, its reopening a year from the remittance
    assert window_end("2027-03-05", corrected_rule("Medicare KY (CGS J15)", "medicare", "MA130")) == "2028-03-05"
    assert window_end("2027-03-05", corrected_rule("Medicare KY (CGS J15)", "medicare", None, "DENIED_MISSING_MODIFIER")) == "2028-03-05"
    assert window_end("2027-03-05", corrected_rule("Humana Medicare Advantage", "medicare_advantage")) == "2028-03-05"
    # month ends and February 29: the last day of the target month
    assert add_months(date(2025, 8, 31), 18) == date(2027, 2, 28) and add_months(date(2027, 8, 31), 6) == date(2028, 2, 29)
    assert add_months(date(2028, 2, 29), 12) == date(2029, 2, 28) and add_months(date(2028, 2, 29), 24) == date(2030, 2, 28)
    assert add_months(date(2026, 2, 28), 24) == date(2028, 2, 28) and add_months(date(2026, 12, 31), 2) == date(2027, 2, 28)
    assert window_end("2028-02-29", appeal_rule("UnitedHealthcare", "commercial")) == "2029-02-28"
    assert window_end("2026-12-31", appeal_rule("Humana", "commercial")) == "2028-06-30"
    # windows stated in days stay days
    assert window_end("2027-03-05", appeal_rule("Aetna", "commercial")) == "2027-09-01"
    assert window_end("2027-03-05", appeal_rule("Anthem / BCBS Medicare Advantage", "medicare_advantage")) == "2027-07-03"
    assert window_end("2027-03-05", appeal_rule("Humana Medicaid", "medicaid")) == "2027-07-03"
    assert window_end("2027-03-05", appeal_rule("Anthem / BCBS", "commercial", "FEDERAL EMPLOYEE PROGRAM")) == "2027-09-01"
    assert window_end("2027-03-05", filing_rule("Anthem / BCBS", "commercial")) == "2027-06-03"                    # 90 days
    assert window_end("2027-03-05", filing_rule("Some Plan Medicaid", "medicaid")) == "2028-03-04"                   # "365 days"
    assert window_end("2027-03-05", corrected_rule("Anthem / BCBS Medicare Advantage", "medicare_advantage")) == "2028-03-04"
    assert window_end(None, appeal_rule("Aetna", "commercial")) is None and window_end("not a date", appeal_rule("Aetna", "commercial")) is None


def test_medicare_redetermination_runs_from_presumed_receipt():
    """42 CFR 405.942(a)(1): 120 days from receipt of the notice, presumed 5 days after its date: 125 days from the
    remittance, for the appeal and for the redetermination with the records a claim lacked. A reopening (405.980) counts
    from the determination itself."""
    r = appeal_rule("Medicare IN (WPS J8)", "medicare")
    assert r["receipt_days"] == 5 and "405.942" in r["label"] and "125 days" in r["label"]
    assert window_end("2026-07-21", r) == "2026-11-23"
    records = corrected_rule("Medicare KY (CGS J15)", "medicare", "N463", "DENIED_MISSING_INFO", "16")
    assert records["receipt_days"] == 5 and window_end("2026-07-21", records) == "2026-11-23" and "takes no documents" in records["label"]
    reopen = corrected_rule("Medicare KY (CGS J15)", "medicare", None, "DENIED_MISSING_MODIFIER")
    assert not reopen["receipt_days"] and window_end("2026-07-21", reopen) == "2027-07-21"
    # other payers count from the notice itself, as their manuals say
    assert window_end("2026-07-21", appeal_rule("Anthem / BCBS Medicare Advantage", "medicare_advantage")) == "2026-11-18"
    # the letter says the request is timely on the day it can be sent: within 120 days of receipt, not of the determination
    assert "receipt" in LEGAL["MEDICARE"] and "405.942" in LEGAL["MEDICARE"]
    from remitwatch.appeals import MEDICARE_NEXT
    assert "receiving the remittance" in MEDICARE_NEXT["redetermination"] and "5 days" in MEDICARE_NEXT["redetermination"]


def _detect(tmp_path, claims: list[str], pay: str, payer: str, payer_id: str, compact: bool) -> dict:
    from remitwatch.detect import Detector, ensure_schema
    from remitwatch.store import Store
    store = Store(":memory:", compact=True) if compact else Store(str(tmp_path / f"{payer_id}.sqlite"))
    paths = []
    for n, i in enumerate(range(0, len(claims), 20)):
        p = tmp_path / f"{payer_id}{int(compact)}{n:03d}.RMT"
        p.write_bytes(remit("".join(claims[i:i + 20]), "100.00", payer=payer, payer_id=payer_id, seg_count=99, isa_ctrl=f"{n + 1:09d}", pay_date=pay))
        paths.append(str(p))
    store.ingest_835_paths(paths)
    ensure_schema(store.conn)
    Detector(store, run_id="t").run()
    out = {r[0]: r[1] for r in store.conn.execute("SELECT signal, appeal_deadline FROM findings WHERE appeal_deadline IS NOT NULL")}
    store.close()
    return out


@pytest.mark.parametrize("compact", [False, True], ids=["store_file", "compact_image"])
def test_detection_dates_each_item_by_its_payers_own_words(tmp_path, compact):
    """The dates detection gives, on the server's store file and on the compact image the serverless cycle builds."""
    under = lambda dos, **kw: clean_visits(1, 25, "99214", "223.65", "117.60", dos=dos, **kw) + [            # noqa: E731
        claim(202, svc("99214", "223.65", "110.00", "110.00", "CAS*CO*45*113.65~", dos=dos), dos=dos, **kw)]
    hum = _detect(tmp_path, under("20250515", cfi="16"), "20250524", "HUMANA", "61101", compact)
    assert hum["UNDERPAID_VS_OWN_RATE"] == "2026-11-24"                                  # 18 months, not 540 days (2026-11-15)
    ant = _detect(tmp_path, under("20260225"), "20260305", "ANTHEM", "ANTHM", compact)
    assert ant["UNDERPAID_VS_OWN_RATE"] == "2028-03-05"                                  # two years across February 29
    uhc = _detect(tmp_path, under("20270225"), "20270305", "UNITEDHEALTHCARE", "87726", compact)
    assert uhc["UNDERPAID_VS_OWN_RATE"] == "2028-03-05"                                  # 12 months across February 29
    dos = "20260712"
    med = clean_visits(1, 25, "99214", "223.65", "117.60", cfi="MB", dos=dos) + [
        claim(300, svc("11102", "150.00", "0.00", None, "CAS*CO*50*150.00~", dos=dos), cfi="MB", dos=dos),
        claim(301, svc("11102", "150.00", "0.00", None, "CAS*CO*250*150.00~", dos=dos) + "LQ*HE*N463~", cfi="MB", dos=dos),
        claim(302, svc("99213", "150.00", "0.00", None, "CAS*CO*16*150.00~", dos=dos) + "LQ*HE*N822~", cfi="MB", dos=dos)]
    m = _detect(tmp_path, med, "20260721", "KY MEDICARE", "15004", compact)
    assert m["DENIED_MEDICAL_NECESSITY"] == "2026-11-23"                                 # redetermination: 125 days
    assert m["DENIED_MISSING_INFO"] == "2026-11-23"                                      # records go in a redetermination: 125 days
    assert m["DENIED_MISSING_MODIFIER"] == "2027-07-21"                                  # a reopening: a year from the remittance


# --------------------------------------------------------------------------- F5: the live stores re-detect once

def _live_version() -> str | None:
    """DETECT_VERSION as the live release (949c4db) computes it, from its own files."""
    import hashlib
    h = hashlib.sha256()
    try:
        for name in ("detect.py", "codes.py", "fees.py", "link.py", "pfs.py"):
            h.update(subprocess.run(["git", "-C", REPO, "show", f"949c4db:remitwatch/{name}"], capture_output=True, check=True).stdout)
    except (OSError, subprocess.CalledProcessError):
        return None
    return "2.6-" + h.hexdigest()[:8]


def test_a_store_the_live_release_analysed_is_analysed_again_once(tmp_path, monkeypatch, box):  # noqa: F811
    from remitwatch import cloud, detect
    from remitwatch.pipeline import run_cycle
    from tests.test_cloud import DATA, cycle, write_remits
    assert detect.DETECT_RULES != "2.6" and detect.DETECT_VERSION.startswith(detect.DETECT_RULES + "-")
    live = _live_version() or "2.6-00000000"
    assert live != detect.DETECT_VERSION
    for k in ("DROPBOX_APP_KEY", "DROPBOX_APP_SECRET", "DROPBOX_REFRESH_TOKEN", "SMTP_HOST", "ALERT_WEBHOOK_URL"):
        monkeypatch.delenv(k, raising=False)
    # the server: its store file
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "a.RMT").write_bytes(remit("".join(_claims("20261101")), "100.00", seg_count=99, pay_date="20261110"))
    db = str(tmp_path / "p.sqlite")
    assert "detect" in run_cycle(db, sync=False, local=str(inbox), email=False, report=False, log=lambda *a: None)
    _write(db, "UPDATE detection_runs SET version=?", (live,))
    assert "detect" in run_cycle(db, sync=False, local=str(inbox), email=False, report=False, log=lambda *a: None)
    assert "detect" not in run_cycle(db, sync=False, local=str(inbox), email=False, report=False, log=lambda *a: None)
    # the serverless site: its store image in the state folder
    root, client, state = box
    write_remits(root / DATA, _claims("20260405"))
    assert "detect" in cycle(state, client)
    store, _existed = cloud.load_raw(state)
    store.conn.execute("UPDATE detection_runs SET version=?", (live,))
    store.conn.commit()
    cloud.save_raw(state, store)
    store.close()
    assert "detect" in cycle(state, client)
    assert "detect" not in cycle(state, client)


def test_the_setting_is_named_for_both_hosts():
    """REMITWATCH_TZ is in .env.example and both runbooks (tests/test_docs.py checks every setting the app reads)."""
    for page in (".env.example", "docs/SERVER_RUNBOOK.md", "docs/VERCEL_SETUP.md"):
        with open(os.path.join(REPO, page), encoding="utf-8") as fh:
            assert "REMITWATCH_TZ" in fh.read(), page
    with open(os.path.join(REPO, "requirements.txt"), encoding="utf-8") as fh:
        assert any(line.strip().startswith("tzdata") for line in fh), "tzdata: the zone data on a host with none of its own"
    assert json.dumps(runtime.DEFAULT_TZ) == '"America/New_York"'


# --------------------------------------------------------------------------- the pages (Playwright)

def _port() -> int:
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_the_pages_count_by_the_practice_s_day_and_offer_the_checked_follow_up(tmp_path):
    """At 7:15 PM Eastern on an item's last day, in a browser whose own day is already the next one: Today's header names
    the practice's day, the item closes today and can still be sent; the Faxes page offers a second request held for old
    remittances to a person who checked EMA, and sends it. Desktop and phone width."""
    import shutil
    import sys
    import time
    import urllib.request
    if not shutil.which("node") or subprocess.run(["node", "-e", "require('playwright')"], capture_output=True, cwd=REPO).returncode:
        pytest.skip("needs node and Playwright for node")
    from remitwatch.detect import Detector
    from tests.test_cloud import PW
    from tests.test_features import build_store
    store = build_store(tmp_path, _claims("20261101"), pay_date="20261110")
    Detector(store, run_id="t").run()
    a, b = sorted(r[0] for r in store.conn.execute("SELECT finding_id FROM findings WHERE signal='UNDERPAID_VS_OWN_RATE'"))
    store.conn.execute("UPDATE findings SET appeal_deadline='2026-11-24' WHERE finding_id=?", (a,))
    store.conn.commit()
    store.close()
    db = str(tmp_path / "s.sqlite")
    port = _port()
    env = dict(os.environ, REMITWATCH_DB=db, REMITWATCH_PASSWORD=PW, REMITWATCH_USER="oda", PYTHONDONTWRITEBYTECODE="1",
               REMITWATCH_STALE_APPEAL_DAYS="3", PRACTICE_NAME="Test Dermatology", PRACTICE_ADDRESS="1 Main St, Anytown KY",
               PRACTICE_TIN="61-0000000", PRACTICE_NPI="1999999999", PRACTICE_PHONE="(270) 555-0100", PRACTICE_FAX="(270) 555-0101",
               PRACTICE_EMAIL="billing@example.org")
    for k in ("REMITWATCH_MODE", "REMITWATCH_OWNER_PASSWORD", "REMITWATCH_SHOW_PHI", "ANTHROPIC_API_KEY", "SRFAX_CALLER_ID", runtime.TZ_SETTING):
        env.pop(k, None)
    srv = subprocess.Popen([sys.executable, "-B", os.path.join(REPO, "tests", "review_assets", "clock_server.py"), str(port),
                            "2026-11-25T00:15:00+00:00"], cwd=REPO, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    auth = "Basic " + base64.b64encode(f"oda:{PW}".encode()).decode()

    def call(path: str, body: dict | None = None) -> dict:
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode() if body is not None else None,
                                     headers=dict(XHR, Authorization=auth, **({"Content-Type": "application/json"} if body is not None else {})))
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    try:
        for _ in range(200):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        call("/api/faxes/payer", {"payer_group": "Anthem / BCBS", "number": PAYER_FAX})
        assert call("/api/appeals/send", {"finding_id": b, "stale_ok": True})["status"] == "sent"
        # faxed 61 days before the practice's day of the clock
        when = (datetime.fromisoformat(runtime.practice_day_start("2026-09-24")) + timedelta(hours=15)).astimezone(timezone.utc)
        _write(db, "UPDATE faxes SET created_at=? WHERE finding_id=?", (when.isoformat(timespec="microseconds"), b))
        _write(db, "UPDATE findings SET appealed_at=?, status_changed_at=? WHERE finding_id=?", (when.isoformat(), when.isoformat(), b))
        out = subprocess.run(["node", os.path.join(REPO, "tests", "review_assets", "clock_page.js"), str(port), "oda", PW, a, b],
                             capture_output=True, text=True, timeout=240, cwd=REPO)
        assert out.returncode == 0, out.stderr[-2000:]
        seen = json.loads(out.stdout.strip().splitlines()[-1])
    finally:
        srv.terminate()
        srv.wait(timeout=10)
    for view, r in seen.items():
        assert r["errors"] == [] and r["failed"] == [], (view, r["errors"], r["failed"])
        assert r["overflow"] <= 0, (view, r["overflow"])
        assert r["header"] == "Tuesday, November 24" and r["site"] == "2026-11-24", (view, r["header"], r["site"])
        assert (r["today"], r["tomorrow"]) == ("closes today", "tomorrow"), view
        assert "closes today" in r["drawer"]["head"] and "closed" not in r["drawer"]["next"], (view, r["drawer"])
        assert "days old" in r["followup"] and "EMA" in r["followup"], (view, r["followup"])
    f = seen["desktop"]["faxes"]
    assert f["stale"] == "1" and f["button"].startswith("Still unpaid in EMA: send it to"), f
    assert f["message"].startswith("Faxed to (800) 555-0142"), f
    assert any("still unpaid" in d for d in seen["desktop"]["dialogs"]), seen["desktop"]["dialogs"]
