"""
One day for the whole site: the practice's (runtime.practice_today, REMITWATCH_TZ), never the machine's or UTC's.

A filing window stays open until midnight where the practice is, and a report pulled eight days ago is eight days old
there. Every list counts from the practice's day; these are the other places the site counts days: the reports' ages on
the Data tab and Today, the module defaults every check falls back on (EMA's checks, the states, the Results figures, the
self-check, the archive, the report readers), the calculator's open findings, the upload checklist, the once-a-day stop
mails, the fax line's date ranges and a person's Paid. Each counted from the machine's day (UTC on both hosts), a day
ahead of a US practice from 7 PM Eastern (8 PM in summer).

pin_zone puts the practice a day off UTC whatever the hour a test runs, so each test fails on a UTC day at any hour.
Both store kinds where the code runs on both: the server's file store and the compact in-memory store a serverless run
works on (the serverless pages read the dashboard's copy made from it). Invented data only.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone

import pytest

from remitwatch import runtime
from remitwatch.detect import ensure_schema
from remitwatch.store import Store

NPI = "1000000001"          # an invented clinician


def pin_zone(monkeypatch) -> bool:
    """Put the practice a day off UTC at this hour: UTC-12 before 11:00 UTC (the practice's day is UTC's yesterday, as a US
    practice's is each evening), UTC+14 from 11:00 (it is UTC's tomorrow). Both zones are a day off between 10:00 and noon
    UTC, so the switch at 11:00 keeps an hour from either edge. Returns True when the practice is behind UTC."""
    behind = datetime.now(timezone.utc).hour < 11
    monkeypatch.setenv(runtime.TZ_SETTING, "Etc/GMT+12" if behind else "Etc/GMT-14")
    monkeypatch.delenv("REMITWATCH_TODAY", raising=False)
    assert runtime.practice_today() != utc_day()
    return behind


def utc_day(offset: int = 0) -> str:
    return (datetime.now(timezone.utc).date() + timedelta(days=offset)).isoformat()


def _store(kind: str, tmp_path) -> Store:
    s = Store(str(tmp_path / "store.sqlite")) if kind == "file" else Store(":memory:", compact=True)
    ensure_schema(s.conn)
    s.conn.commit()
    return s


@pytest.fixture(params=["file", "compact"])
def store(request, tmp_path):
    s = _store(request.param, tmp_path)
    yield s
    s.close()


def _item(conn, fid: str, **kw) -> None:
    from tests.test_selfcheck import add_item
    add_item(conn, fid, **kw)


def _due(behind: bool) -> str:
    """A filing date on the edge: the practice's today where the practice is behind UTC (still open there; UTC's day is
    past it), the practice's yesterday where it is ahead (closed there; UTC's day is that day)."""
    return runtime.practice_today() if behind else runtime.practice_today(-1)


# ------------------------------------------------------------------------------------------------ the modules' own day
def test_every_modules_own_today_is_the_practices_day(monkeypatch):
    """Each module's day when the caller passes none: EMA's checks, the states, the Results figures, the self-check, the
    archive and the report readers (the latest date a file read today may hold). A day the caller passes still wins, and
    simulated days (REMITWATCH_TODAY) still steer the self-check."""
    from remitwatch import archive, ema_checks as ec, ema_reports as er, results, selfcheck as sc, states
    pin_zone(monkeypatch)
    day = runtime.practice_today()
    assert ec._today(None) == states._today(None) == results._today() == sc.today_of() == archive._today() == er.today_iso() == day
    assert archive.settings()["today"] == day
    assert er.date_bounds()[1:] == (runtime.practice_today(er.POSTED_AHEAD_DAYS), runtime.practice_today(er.SERVICE_AHEAD_DAYS))
    for f in (ec._today, states._today, results._today, sc.today_of, archive._today):
        assert f("2026-02-28") == "2026-02-28"
    monkeypatch.setenv("REMITWATCH_TODAY", "2028-02-29")
    assert sc.today_of() == "2028-02-29"


def test_a_billing_note_with_no_year_is_read_on_the_practices_day(monkeypatch):
    """A note's month and day with no year is the latest such day on or before the day it is read: the practice's day,
    where the note was written. UTC's day is the practice's tomorrow (behind) or yesterday (ahead)."""
    from remitwatch import states
    behind = pin_zone(monkeypatch)
    day = date.fromisoformat(runtime.practice_today())
    other = date.fromisoformat(utc_day())
    # behind: a note dated UTC's day is the practice's tomorrow, so it is last year's; ahead: one dated the practice's
    # day is this year's, though UTC's day is still the day before
    d = other if behind else day
    want = d.replace(year=d.year - 1) if behind else d
    if behind and (d.month, d.day) == (2, 29):
        pytest.skip("no February 29 the year before")
    got = states.classify_note(f"{d.month:02d}/{d.day:02d} resubmitted corrected claim")
    assert got["date"] == want.isoformat(), (behind, got)


# --------------------------------------------------------------------------------------------- the reports' ages
def _reports(conn, ar_as_of: str, prod_as_of: str) -> None:
    conn.execute("""INSERT INTO ema_report_files (file_id, report, status, as_of, received_at, svc_lo, svc_hi, scope) VALUES
                    ('p1', 'production', 'current', ?, ?, '2026-01-01', ?, 'posted'),
                    ('a1', 'ar_report', 'current', ?, ?, '2026-01-01', ?, 'service')""",
                 (prod_as_of, prod_as_of + "T12:00:00+00:00", prod_as_of, ar_as_of, ar_as_of + "T12:00:00+00:00", ar_as_of))
    conn.execute("INSERT INTO ema_ar (file_id, row_no, as_of, svc_year, balance_cents) VALUES ('a1', 1, ?, '2026', 5000)", (ar_as_of,))
    conn.commit()


def test_the_reports_ages_count_to_the_practices_day_on_both_store_kinds(store, monkeypatch):
    """An A/R pulled eight days ago is eight days old where the practice is, still fresh enough to rest a conclusion on
    (at most 8); a Production report two days ago is two days old. ema_checks.freshness with no day (the Data tab, Today,
    the reconcile summary a page asks for) and the self-check's feed check say so."""
    from remitwatch import ema_checks as ec, selfcheck as sc
    pin_zone(monkeypatch)
    c = store.conn
    _reports(c, runtime.practice_today(-8), runtime.practice_today(-2))
    fr = ec.freshness(c)
    assert (fr["today"], fr["ar_days"], fr["ar_fresh"], fr["production_days"]) == (runtime.practice_today(), 8, True, 2), fr
    feed = next(r for r in sc.run_checks(c, only=["feed_ema"]))
    assert feed["status"] == "ok" and feed["info"]["ar_days"] == 8, feed


# ----------------------------------------------------------------------------------------------------- the self-check
def test_the_self_check_counts_follow_ups_deadlines_and_its_own_day_from_the_practices_day(store, monkeypatch):
    """A waiting item's follow-up day has passed only once the practice's day is after it; money at risk in the next 14
    days counts an item due on the practice's today and not one due on its yesterday; the run stores the practice's day as
    its own; the regulation dates an optional step it leaves by the practice's date of the run's start."""
    from remitwatch import selfcheck as sc
    from tests.test_selfcheck import status
    behind = pin_zone(monkeypatch)
    day = runtime.practice_today()
    c = store.conn
    _item(c, "F-wait", work_state="waiting", follow_up_on=_due(behind))           # passed only where the practice is ahead
    _item(c, "F-due", appeal_deadline=_due(behind), shortfall_cents=4000)
    follow = next(r for r in sc.run_checks(c, only=["followup_date"]))
    assert follow["status"] == ("ok" if behind else "fail"), (behind, follow)
    assert sc.scoreboard(c)["at_risk_14"] == ({"n": 1, "cents": 4000} if behind else {"n": 0, "cents": 0})
    ctx = sc.begin_detection(c, run_id="r1")
    sc.after_detection(c, ctx, mode="server", run_id="r1")
    assert ctx["today"] == day
    assert c.execute("SELECT today FROM selfcheck_runs WHERE run_id='r1'").fetchone()[0] == day
    assert sc.meta_get(c, sc.K_LAST)["today"] == day
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for n, peak in enumerate((1800, 1780, 1760)):           # three runs past 80%: the third plan leaves the optional steps
        plan = sc.plan_next(c, status(f"2026-11-0{n + 2}T10:00:00+00:00", peak), mode="serverless", now=now)
    assert plan["skip"], plan
    assert set(sc.meta_get(c, sc.K_REGULATE)["shed_since"].values()) == {day}


# ------------------------------------------------------------------------------------------------------- Results
def _results_store(s) -> None:
    from remitwatch import actions, results
    actions.ensure_fax_tables(s.conn)
    results.ensure_schema(s.conn)


def test_the_results_page_counts_money_at_risk_from_the_practices_day_on_both_hosts(tmp_path, monkeypatch):
    """The Results tab's day and its money at risk in the next 14 days: the server counts on its store, the serverless page
    on the dashboard's copy. An item due on the practice's today is at risk (open until midnight there); one due on its
    yesterday is lost. The count is the list it opens."""
    from fastapi.testclient import TestClient

    from remitwatch import api, cloud, results
    from remitwatch.state import LocalState
    behind = pin_zone(monkeypatch)
    day = runtime.practice_today()
    want = 1 if behind else 0
    monkeypatch.setattr(api, "PASSWORD", "")
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    for k in ("VERCEL", "REMITWATCH_STATE_DIR"):
        monkeypatch.delenv(k, raising=False)
    path = str(tmp_path / "srv.sqlite")
    s = Store(path)
    ensure_schema(s.conn)
    _results_store(s)
    _item(s.conn, "F-due", appeal_deadline=_due(behind), shortfall_cents=4000)
    s.close()
    monkeypatch.setattr(api, "DB_PATH", path)
    tc = TestClient(api.app)
    d = tc.get("/api/results").json()
    assert d["today"] == day and d["host"] == "server" and d["live"]["at_risk"]["n"] == want, (behind, d["live"])
    assert tc.get("/api/results/list", params={"key": "at_risk"}).json()["total"] == want
    s = Store(path)
    assert results.dashboard(s.conn)["today"] == day           # the figures the server worked out are the practice's day's
    s.close()
    # the serverless host: the run works the figures out on its compact store, the page counts on the dashboard's copy
    monkeypatch.setenv("REMITWATCH_TMP", str(tmp_path / "scratch"))
    root = tmp_path / "state"
    st = LocalState(str(root))
    cs = _store("compact", tmp_path)
    _results_store(cs)
    _item(cs.conn, "F-due", appeal_deadline=_due(behind), shortfall_cents=4000)
    results.refresh(cs.conn, host="serverless", state=st, force=True)
    assert results.dashboard(cs.conn)["today"] == day
    cloud.save_serving(st, cs.conn)
    cs.close()
    monkeypatch.setenv("REMITWATCH_MODE", "serverless")
    monkeypatch.setenv("REMITWATCH_STATE_DIR", str(root))
    monkeypatch.setattr(cloud, "SERVING_COPY", cloud.ServingCopy())
    d = tc.get("/api/results").json()
    assert d["today"] == day and d["host"] == "serverless" and d["live"]["at_risk"]["n"] == want, (behind, d["live"])
    assert tc.get("/api/results/list", params={"key": "at_risk"}).json()["total"] == want


# ------------------------------------------------------------------------------------------------------- the archive
def test_the_archive_takes_its_horizon_and_pins_from_the_practices_day(store, monkeypatch):
    """The archive's cutoff moves once a month from the practice's day, and a bill brought back stays hot until a day
    counted from it."""
    from remitwatch import archive
    pin_zone(monkeypatch)
    day = runtime.practice_today()
    p = archive.plan(store.conn)
    assert p["today"] == day and p["cutoff"] == archive._months_before(day, p["horizon"]["months"])
    assert archive.record_run(store.conn, run_id="r1", measured={"mode": "server"})["day"] == day


# --------------------------------------------------------------------------------------------------- the calculator
def test_the_calculator_counts_open_findings_still_recoverable_to_the_practices_day(store, monkeypatch):
    """The strategy lab's money still recoverable: an open finding due on the practice's today counts (open until
    midnight there); one due on its yesterday does not."""
    from remitwatch import calculator
    behind = pin_zone(monkeypatch)
    _item(store.conn, "F-due", appeal_deadline=_due(behind), shortfall_cents=4000, rendering_provider_npi=NPI)
    got = calculator._strategy_inputs(store.conn, [NPI], "2026-01-01", "2026-06-30", 6)["open_findings_total"]
    assert got == ({"denial": {"n": 1, "cents": 4000}} if behind else {}), (behind, got)


def test_the_calculators_current_year_follows_the_practices_clock(monkeypatch):
    """With no rates on file the calculator's current year is the practice's: New Year's Eve evening in the US is still the
    old year, and a clock in another year entirely gives that year."""
    from remitwatch import activity, calculator
    monkeypatch.delenv(runtime.TZ_SETTING, raising=False)
    s = _store("compact", None)
    activity.ensure_schema(s.conn)
    for iso, year in (("2031-01-01T03:00:00+00:00", "2030"), ("2031-07-01T12:00:00+00:00", "2031")):
        t = datetime.fromisoformat(iso)
        monkeypatch.setattr(runtime, "_utc_now", lambda t=t: t)
        assert calculator.Rates(s.conn, "physician", {}).year == year, iso
    s.close()


# ---------------------------------------------------------------------------------------------- the upload checklist
def test_the_upload_checklist_counts_ages_to_the_practices_day(store, monkeypatch):
    """The Upload page's checklist: an A/R pulled eight days ago is eight days old where the practice is (not overdue:
    at most 8); a weekly export read a moment ago is read today there, never a day off; an A/R that arrived nine days after
    it was pulled says nine."""
    from remitwatch import uploads
    pin_zone(monkeypatch)
    c = store.conn
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    c.execute("""INSERT INTO ema_report_files (file_id, report, status, as_of, received_at, svc_lo, svc_hi, scope, via)
                 VALUES ('a1', 'ar_report', 'current', ?, ?, '2026-01-01', ?, 'service', 'upload')""",
              (runtime.practice_today(-8), now, runtime.practice_today(-8)))
    c.execute("INSERT INTO files (sha256, source_path, source_name, size, mtime, kind, ingested_at, status) VALUES (?,?,?,?,?,?,?,?)",
              ("ab" * 32, "/x/charges.csv", "charges.csv", 10, now, "ema:charge_lines", now, "read"))
    c.commit()
    rows = {r["key"]: r for r in uploads.exports_overview(c)}
    assert (rows["ar"]["age_days"], rows["ar"]["overdue"]) == (8, False), rows["ar"]
    assert "8 days old" in rows["ar"]["state_line"]
    assert (rows["charge_lines"]["age_days"], rows["charge_lines"]["overdue"]) == (0, False), rows["charge_lines"]
    late = uploads.upload_checks({"report": "ar_report", "as_of": runtime.practice_today(-9), "received_at": now})
    assert [x["code"] for x in late] == ["old"] and "9 days before it arrived" in late[0]["message"], late
    unsaid = uploads.upload_checks({"report": "ar_report", "as_of": runtime.practice_today(-9)})      # no arrival on file: today
    assert [x["code"] for x in unsaid] == ["old"] and "9 days before it arrived" in unsaid[0]["message"], unsaid
    # the days the page says a file arrived or was read are the practice's, as its age is counted
    words = uploads._day(runtime.practice_today())
    assert uploads._day(now) == words and rows["charge_lines"]["last_line"] == f"Last arrived {words}."
    from remitwatch import dropbox_sync
    assert dropbox_sync._export_summary("charges.csv", [], {}, "charge_lines", 0)["read_at"] == runtime.practice_today()


# ------------------------------------------------------------------------------------------------- the stop mails
def test_a_run_that_stops_mails_once_a_practice_day_on_both_hosts(tmp_path, monkeypatch):
    """A run that stops over the store mails the report address at most once a day: the practice's day, so the evening's
    second stop is not a new day's mail. The serverless run keeps the day in status.json, the server in the store's
    marker."""
    from remitwatch import cloud, pipeline, report
    pin_zone(monkeypatch)
    day = runtime.practice_today()
    sent = []
    monkeypatch.setattr(report, "email_report", lambda text, subject="": sent.append(subject) or True)
    out = {"error": "The store image is missing."}
    cloud._image_alert({}, out, True)
    assert out["image_alerted"] == day and len(sent) == 1
    again = {"error": "The store image is missing."}
    cloud._image_alert({"image_alerted": day}, again, True)
    assert again["image_alerted"] == day and len(sent) == 1
    db = str(tmp_path / "data" / "remitwatch.sqlite")
    Store(db).close()
    from remitwatch.store import _read_marker, mark_store
    mark_store(db)
    pipeline._store_alert(db, "The store is missing.")
    assert _read_marker(db)["alerted_on"] == day and len(sent) == 2
    pipeline._store_alert(db, "The store is missing.")
    assert len(sent) == 2


# ------------------------------------------------------------------------------------------------- the fax line
def test_the_fax_lines_date_ranges_hold_the_practices_day_and_utcs(monkeypatch):
    """SRFax is asked for faxes by date range. Whatever zone it dates a fax in, the range holds both the practice's day and
    UTC's, so a fax that came in this evening is never left out until tomorrow."""
    from remitwatch import fax
    from tests.fake_fax import FakeSRFax, pdf_of
    pin_zone(monkeypatch)
    days = sorted([runtime.practice_today(), utc_day()])
    ymd = lambda iso: iso.replace("-", "")          # noqa: E731
    svc = FakeSRFax()
    line = fax.SRFax("1234", "pw", "(270) 555-0100", "billing@example.org", session=svc)
    line.inbox(30)
    q = svc.calls[-1]
    assert q["sStartDate"] <= ymd((date.fromisoformat(days[0]) - timedelta(days=30)).isoformat()) and q["sEndDate"] >= ymd(days[1]), q
    assert line.check()["ok"] is True
    q = svc.calls[-1]
    assert q["sStartDate"] <= ymd(days[0]) and q["sEndDate"] >= ymd(days[1]), q
    line.send("(800) 555-0142", pdf_of("appeal"), "appeal-day-1.pdf")
    assert line.find_sent("appeal-day-1.pdf", "8005550142", days[0] + "T10:00:00+00:00")
    q = svc.calls[-1]
    assert q["sEndDate"] >= ymd(days[1]), q
    line.find_sent("appeal-day-1.pdf", "8005550142", "not a time")
    q = svc.calls[-1]
    assert q["sStartDate"] <= ymd((date.fromisoformat(days[0]) - timedelta(days=7)).isoformat()) and q["sEndDate"] >= ymd(days[1]), q


# ------------------------------------------------------------------------------------------------- the page's own count
# index.html's findings table says how old the newest remittance is (and warns past ten days). The line that counts it is
# run in node with the page's own day helpers (today.js), a brief and a fixed browser clock.
REMIT_AGE_JS = r"""
const fs = require('fs'), vm = require('vm');
const [htmlPath, todayPath, casesJson] = process.argv.slice(-3);
const html = fs.readFileSync(htmlPath, 'utf8'), today = fs.readFileSync(todayPath, 'utf8');
const grab = (src, re) => { const m = src.match(re); if (!m) throw new Error('not found: ' + re); return m[0]; };
const helpers = ['localDay', 'siteDay', 'daysUntil'].map(n => grab(today, new RegExp('function ' + n + '\\([^\\n]*\\n'))).join('');
const line = grab(html, /const last = \(\(state\.meta[^\n]*\n/);
const clock = iso => { const t = Date.parse(iso); return class extends Date { constructor(...a) { if (a.length) super(...a); else super(t); } static now() { return t; } }; };
const out = [];
for (const [now, brief, last] of JSON.parse(casesJson)) {
  const ctx = {state: {meta: {remits: {last_payment: last}}, brief: brief ? {today: brief} : null}, Date: clock(now), Math, String};
  vm.runInNewContext(helpers + line + 'out = behind;', ctx);
  out.push(ctx.out);
}
console.log(JSON.stringify(out));
"""


def test_the_pages_remittance_age_counts_to_the_practices_day():
    """At 8 PM Eastern on Nov 24 a remittance paid Nov 14 is ten days old where the practice is (no warning yet), as the
    server's days behind says; the page counted from the browser's clock to midnight UTC and said eleven. The next morning
    it is eleven."""
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    static = os.path.join(root, "remitwatch", "static")
    cases = [["2026-11-25T01:00:00Z", "2026-11-24", "2026-11-14"],      # 8 PM Eastern, Nov 24
             ["2026-11-25T15:00:00Z", "2026-11-25", "2026-11-14"],      # 10 AM Eastern, Nov 25
             ["2027-01-01T04:30:00Z", "2026-12-31", "2026-12-21"]]      # New Year's Eve, 11:30 PM Eastern
    res = subprocess.run([node, "-e", REMIT_AGE_JS, os.path.join(static, "index.html"), os.path.join(static, "today.js"), json.dumps(cases)],
                         capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout) == [10, 11, 10]


# ------------------------------------------------------------------------------------------------- a person's Paid
EVENING = "2026-11-25T01:30:00+00:00"           # 8:30 PM Eastern on Tuesday November 24


def test_a_paid_marked_in_the_evening_is_recovered_that_day_where_the_practice_is(store, monkeypatch):
    """A person who marks an item Paid at 8:30 PM Eastern recovered it that day (its week, its year), not the next; the
    self-check puts the same day back when it restores a person's change on a held worklist."""
    from remitwatch import actions, selfcheck as sc
    monkeypatch.delenv(runtime.TZ_SETTING, raising=False)
    c = store.conn
    _item(c, "F-paid")
    assert actions.apply_status(c, "F-paid", "resolved", recovered_cents=4000, actor="Biller One", at=EVENING)
    c.commit()
    assert c.execute("SELECT recovered_date FROM findings WHERE finding_id='F-paid'").fetchone()[0] == "2026-11-24"
    since = c.execute("SELECT COALESCE(MIN(id), 1) - 1 FROM finding_events").fetchone()[0]
    c.execute("UPDATE findings SET status='open', recovered_date=NULL, recovered_cents=NULL WHERE finding_id='F-paid'")
    assert sc._reapply_person_changes(c, since) == 1
    assert tuple(c.execute("SELECT status, recovered_date FROM findings WHERE finding_id='F-paid'").fetchone()) == ("resolved", "2026-11-24")
