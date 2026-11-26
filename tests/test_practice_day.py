"""
One day for the whole site: the practice's (runtime.practice_today, REMITWATCH_TZ), never the machine's or UTC's.

A filing window stays open until midnight where the practice is, and a report pulled eight days ago is eight days old
there. Every list counts from the practice's day; these are the other places the site counts days: the reports' ages on
the Data tab and Today, the module defaults every check falls back on (EMA's checks, the states, the Results figures, the
self-check, the archive, the report readers), the calculator's open findings, the upload checklist, the once-a-day stop
mails, the fax line's date ranges and a person's Paid. Each counted from the machine's day (UTC on both hosts), a day
ahead of a US practice from 7 PM Eastern (8 PM in summer).

A stored moment (a person's Sent, Started or Paid, a packet, a fax, a fax number's change, a refused sign-in, a fix, a
saved setting) is dated on the practice's calendar too (runtime.practice_date): in the states and the story, on every
page (today.js practiceDate, in the zone the brief names), in the Results figures and the self-check. The weekly report's
week, the Results spot checks and the swing alarm's week count from the practice's day.

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


# ------------------------------------------------------------------------------------------ a person's marks and a fax
NIGHT = "2026-11-25T02:30:00+00:00"            # 9:30 PM Eastern on Tuesday November 24 (UTC's day is already the 25th)


def _marked_world(store):
    """Five invented denials of Blue Cross, each with its own claim: one marked Sent (with a packet), one whose only record
    of the send is the item's appealed_at, one Started, one faxed, one marked Paid, all at 9:30 PM Eastern on Nov 24."""
    from remitwatch import actions, ema_reports as er, states
    from tests import ema_fixtures as F
    from tests.test_states import denied, event, item, line_key
    states._RESPONSE_CACHE.clear()
    states._RECOVERY_CACHE.clear()
    c = store.conn
    er.ensure_schema(c)
    actions.ensure_fax_tables(c)
    rows = {}
    for n, fid in enumerate(("F-sent", "F-appealed", "F-started", "F-fax", "F-paid"), 1):
        tk = denied(store, trace=F.check_no(n), pcn=F.claim_id(n), paid_on="2026-11-02", received="2026-10-20")
        rows[fid] = dict(finding_id=fid, patient_control_number=F.claim_id(n), line_key=line_key(store, tk), claim_key=f"{tk}-0",
                         transaction_key=tk, payment_date="2026-11-02")
    item(store, **rows["F-sent"], status="appealed", appealed_at=NIGHT, status_changed_at=NIGHT)
    event(store, "F-sent", "packet", at=NIGHT, actor="Biller One")
    event(store, "F-sent", "manual", at=NIGHT, frm="open", to="appealed", actor="Biller One")
    item(store, **rows["F-appealed"], status="appealed", appealed_at=NIGHT, status_changed_at=NIGHT)
    item(store, **rows["F-started"], status="in_progress", status_changed_at=NIGHT)
    event(store, "F-started", "manual", at=NIGHT, frm="open", to="in_progress", actor="Biller One")
    item(store, **rows["F-fax"])
    c.execute("""INSERT INTO faxes (fax_key, direction, finding_id, payer_group, number, purpose, level, status, created_at, sent_at)
                 VALUES ('X1', 'out', 'F-fax', 'Anthem / BCBS', '8005550142', 'appeal', 1, 'sent', ?, ?)""", (NIGHT, NIGHT))
    item(store, **rows["F-paid"], status="resolved", recovered_cents=9000, status_changed_at=NIGHT)
    event(store, "F-paid", "manual", at=NIGHT, frm="open", to="resolved", actor="Biller One", cents=9000)
    c.commit()


def test_a_persons_marks_and_a_fax_in_the_evening_are_dated_that_day_where_the_practice_is(store, monkeypatch):
    """A person who marks an item Sent, Started or Paid, builds its packet, or faxes it at 9:30 PM Eastern did it that day
    where the practice is, not UTC's next day: the dated work on the item, its state's words, the day its follow-up counts
    from and the item's story all say Nov 24. Both store kinds."""
    from remitwatch import states, story
    monkeypatch.delenv(runtime.TZ_SETTING, raising=False)          # the default: US Eastern
    _marked_world(store)
    c = store.conn
    today = "2026-11-30"

    def item_of(fid):
        return dict(c.execute("SELECT * FROM findings WHERE finding_id=?", (fid,)).fetchone())

    def work(fid):
        return {e["kind"]: e for e in states.work_evidence(c, item_of(fid), today=today)}

    ev = work("F-sent")
    assert (ev["sent"]["date"], ev["packet"]["date"]) == ("2026-11-24", "2026-11-24"), ev
    assert "Marked Sent by Biller One on Nov 24, 2026" in ev["sent"]["text"] and "built on Nov 24, 2026" in ev["packet"]["text"]
    st = states.state_of(c, item_of("F-sent"), today=today)
    # no answers on file to measure: the 45-day default, counted from the practice's day of the send
    assert (st["work_state"], st["follow_up_on"]) == ("waiting", "2027-01-08"), st
    assert "Marked Sent by Biller One on Nov 24, 2026" in st["state_basis"], st["state_basis"]
    ev = work("F-appealed")
    assert ev["sent"]["date"] == "2026-11-24" and ev["sent"]["text"] == "Marked Sent on Nov 24, 2026", ev
    assert states.state_of(c, item_of("F-appealed"), today=today)["follow_up_on"] == "2027-01-08"
    ev = work("F-started")
    assert ev["started"]["date"] == "2026-11-24" and "Started by Biller One on Nov 24, 2026" in ev["started"]["text"], ev
    assert "Started by Biller One on Nov 24, 2026" in states.state_of(c, item_of("F-started"), today=today)["state_basis"]
    ev = work("F-fax")
    assert ev["fax"]["date"] == "2026-11-24" and " on Nov 24, 2026" in ev["fax"]["text"], ev
    assert "Marked Paid by Biller One on Nov 24, 2026" in states.state_of(c, item_of("F-paid"), today=today)["state_basis"]
    # the item's story: RemitWatch's own rows are dated the practice's day, beside the payer's own dates
    for fid in ("F-sent", "F-started", "F-fax", "F-paid"):
        rows = [r for r in story.item_story(c, fid, today=today)["rows"] if r["source"] == "remitwatch" and r["kind"] != "raised"]
        assert rows and {r["date"] for r in rows} == {"2026-11-24"}, (fid, rows)


def test_a_payment_dated_the_day_after_an_evening_reopen_is_newer_than_the_reopen(store, monkeypatch):
    """A person reopened an item RemitWatch had closed on a later payment, at 9:30 PM Eastern on Nov 24. A payment the
    payer dated Nov 25 came after that (the next day where the practice is): it closes the item as usual. Counted by UTC's
    date the reopen was on the 25th too, and the new payment was taken for the old one."""
    from tests import ema_fixtures as F
    from tests.test_states import denied, event, item, line_key, ln, remit
    monkeypatch.delenv(runtime.TZ_SETTING, raising=False)
    from remitwatch import ema_reports as er, states
    states._RESPONSE_CACHE.clear()
    states._RECOVERY_CACHE.clear()
    er.ensure_schema(store.conn)
    tk = denied(store, reason=("CO", "50"), paid_on="2026-11-02", received="2026-10-20")
    remit(store, trace=F.check_no(2), paid_on="2026-11-10", claims=[{"pcn": F.claim_id(1, sub=2), "pccn": "PCN-B", "received": "2026-11-03",
          "lines": [ln("99213", 150, 90, [("CO", "45", 60)], lcn="1")]}])
    item(store, signal="DENIED_MEDICAL_NECESSITY", carc="50", line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk,
         payment_date="2026-11-02", status="open", status_changed_at=NIGHT)
    event(store, "F1", "manual", at=NIGHT, frm="resolved", to="open", actor="Biller One", note="not this line")
    f = dict(store.conn.execute("SELECT * FROM findings WHERE finding_id='F1'").fetchone())
    st = states.state_of(store.conn, f, today="2026-11-30")
    assert st["rule"] == "person_reopened" and st["state_basis"].startswith("Reopened by Biller One on Nov 24, 2026"), st["state_basis"]
    remit(store, trace=F.check_no(3), paid_on="2026-11-25", claims=[{"pcn": F.claim_id(1, sub=3), "pccn": "PCN-C", "received": "2026-11-12",
          "lines": [ln("99213", 150, 90, [("CO", "45", 60)], lcn="1")]}])
    assert states.state_of(store.conn, f, today="2026-11-30")["work_state"] == "done"


def _read_at(store, tk: str, at: str) -> None:
    """The remittance file that carried payment `tk`, read by RemitWatch at `at` (every 835 in a store came in a file)."""
    sha = f"file-of-{tk}"
    store.conn.execute("INSERT OR REPLACE INTO files (sha256, source_path, source_name, kind, ingested_at, status) VALUES (?,?,?,?,?,?)",
                       (sha, f"in/{tk}.835", f"{tk}.835", "835", at, "ok"))
    store.conn.execute("UPDATE transactions SET file_sha256=? WHERE transaction_key=?", (sha, tk))
    store.conn.commit()


REOPENS = {"evening": "2026-11-25T02:30:00+00:00",       # 9:30 PM Eastern on Tuesday Nov 24 (UTC's day is the 25th)
           "2359": "2026-11-25T04:59:00+00:00",          # 11:59 PM Eastern on Nov 24
           "afternoon": "2026-11-24T20:00:00+00:00"}     # 3:00 PM Eastern on Nov 24


def _reopened(store, reopen_at, *, rejected_on="2026-11-25", rejected_read="2026-11-24T15:00:00+00:00"):
    """An item RemitWatch closed on a later 835 payment ($90, check 2) and a person set back to To do at `reopen_at` with
    'not this line'. That payment's file was read at `rejected_read`; its payment date (BPR16, the EFT's) is `rejected_on`."""
    from remitwatch import ema_reports as er, states
    from tests import ema_fixtures as F
    from tests.test_states import denied, event, item, line_key, ln, remit
    states._RESPONSE_CACHE.clear()
    states._RECOVERY_CACHE.clear()
    er.ensure_schema(store.conn)
    tk = denied(store, reason=("CO", "50"), paid_on="2026-11-02", received="2026-10-20")
    _read_at(store, tk, "2026-11-03T15:00:00+00:00")
    paid = remit(store, trace=F.check_no(2), paid_on=rejected_on, claims=[{"pcn": F.claim_id(1, sub=2), "pccn": "PCN-B",
                 "received": "2026-11-03", "lines": [ln("99213", 150, 90, [("CO", "45", 60)], lcn="1")]}])
    _read_at(store, paid, rejected_read)
    item(store, signal="DENIED_MEDICAL_NECESSITY", carc="50", line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk,
         payment_date="2026-11-02", status="open", status_changed_at=reopen_at)
    event(store, "F1", "manual", at=reopen_at, frm="resolved", to="open", actor="Biller One", note="not this line")
    return dict(store.conn.execute("SELECT * FROM findings WHERE finding_id='F1'").fetchone())


@pytest.mark.parametrize("when", sorted(REOPENS))
def test_a_reopen_holds_against_the_payment_the_person_rejected(store, monkeypatch, when):
    """RemitWatch closed an item on an 835 payment dated Nov 25 (the EFT's day) whose file came on Nov 24, a day before the
    money settles, as 835s often do. A person read it, said it does not pay this line, and set the item back to To do on
    Nov 24, in the afternoon, the evening or at 11:59 PM. Nothing came after. That payment was on file when they decided:
    it is the one they rejected, never 'a payment after the reopen' that closes the item again (a person's decision is
    never overwritten by an automatic one). Counted by its payer's date it was newer than any reopen on Nov 24."""
    from remitwatch import states
    monkeypatch.delenv(runtime.TZ_SETTING, raising=False)          # the default: US Eastern
    f = _reopened(store, REOPENS[when])
    st = states.state_of(store.conn, f, today="2026-11-30")
    assert (st["work_state"], st["rule"]) == ("work_now", "person_reopened"), (when, st["work_state"], st["rule"], st["state_basis"])


def test_a_payment_read_after_an_evening_reopen_is_new_money_whatever_its_date(store, monkeypatch):
    """The person reopened at 9:30 PM Eastern on Nov 24, rejecting a payment read on Nov 11. A new remittance file read on
    Nov 25 pays the line with an EFT dated Nov 24, the reopen's own day: it came after the person decided, so the usual
    rules close the item on it. By its payer's date alone it was not after the reopen."""
    from remitwatch import states
    from tests import ema_fixtures as F
    from tests.test_states import ln, remit
    monkeypatch.delenv(runtime.TZ_SETTING, raising=False)
    f = _reopened(store, REOPENS["evening"], rejected_on="2026-11-10", rejected_read="2026-11-11T15:00:00+00:00")
    assert states.state_of(store.conn, f, today="2026-11-30")["rule"] == "person_reopened"
    new = remit(store, trace=F.check_no(3), paid_on="2026-11-24", claims=[{"pcn": F.claim_id(1, sub=3), "pccn": "PCN-C", "received": "2026-11-12",
                "lines": [ln("99213", 150, 90, [("CO", "45", 60)], lcn="1")]}])
    _read_at(store, new, "2026-11-25T15:00:00+00:00")
    st = states.state_of(store.conn, f, today="2026-11-30")
    assert st["work_state"] == "done", (st["rule"], st["state_basis"])


# ------------------------------------------------------------------------------------------- when a fax went
@pytest.mark.parametrize("sent_at", ["Nov/24/2026 21:31:00", "2026-11-24 21:31:00", "2026-11-25T02:31:00+00:00"],
                         ids=["srfax_words", "no_zone", "with_zone"])
def test_an_evening_fax_is_dated_and_followed_up_from_the_practices_day(store, monkeypatch, sent_at):
    """An appeal faxed at 9:30 PM Eastern on Nov 24 (created_at, a UTC time with its zone). Its sent time may be stored as a
    time with its zone (as the status check now writes it), or, in a record written before, as the fax line worded it:
    SRFax's 'Nov/24/2026 21:31:00' in its account's zone, or a time with no zone. Whatever the record says, the fax is
    dated Nov 24 and the follow-up counts 45 days from it (Jan 8): SRFax's words, cut to ten characters, were 'Nov/24/202',
    which sorted after every real day, became the work's date and lost the follow-up."""
    from remitwatch import actions, ema_reports as er, states
    from tests import ema_fixtures as F
    from tests.test_states import denied, item, line_key
    monkeypatch.delenv(runtime.TZ_SETTING, raising=False)
    states._RESPONSE_CACHE.clear()
    states._RECOVERY_CACHE.clear()
    er.ensure_schema(store.conn)
    actions.ensure_fax_tables(store.conn)
    c = store.conn
    tk = denied(store, trace=F.check_no(1), pcn=F.claim_id(1), paid_on="2026-11-02", received="2026-10-20")
    item(store, finding_id="F-fax", patient_control_number=F.claim_id(1), line_key=line_key(store, tk), claim_key=f"{tk}-0",
         transaction_key=tk, payment_date="2026-11-02", status="appealed", appealed_at=NIGHT, status_changed_at=NIGHT)
    c.execute("""INSERT INTO faxes (fax_key, direction, finding_id, payer_group, number, purpose, level, status, created_at, sent_at)
                 VALUES ('X1', 'out', 'F-fax', 'Anthem / BCBS', '8005550142', 'appeal', 1, 'sent', ?, ?)""", (NIGHT, sent_at))
    c.commit()
    f = dict(c.execute("SELECT * FROM findings WHERE finding_id='F-fax'").fetchone())
    ev = {e["kind"]: e for e in states.work_evidence(c, f, today="2026-11-30")}
    st = states.state_of(c, f, today="2026-11-30")
    assert (ev["fax"]["date"], st["work_state"], st["follow_up_on"]) == ("2026-11-24", "waiting", "2027-01-08"), \
        (ev["fax"]["date"], st["work_state"], st["follow_up_on"], st["state_basis"])


def test_a_fax_is_stored_as_sent_at_a_time_with_its_zone_never_before_it_was_queued():
    """What the status check stores as a fax's sent time (fax.sent_moment): the line's time when it is one with its zone,
    else the moment the status was read; never before the fax was queued, never after that moment."""
    from remitwatch import fax
    queued, now = "2026-11-25T02:30:00.000001+00:00", "2026-11-25T03:00:00.000000+00:00"
    assert fax.sent_moment("2026-11-25T02:31:00+00:00", queued, now) == "2026-11-25T02:31:00.000000+00:00"
    assert fax.sent_moment("2026-11-24T21:31:00-05:00", queued, now) == "2026-11-25T02:31:00.000000+00:00"   # moved to UTC
    for words in ("Nov/24/2026 21:31:00", "2026-11-24 21:31:00", "", None):            # no time with its zone: read now
        assert fax.sent_moment(words, queued, now) == now, words
    # a time before the fax was queued (another zone's clock read as UTC, a placeholder): the queueing; one after now: now
    assert fax.sent_moment("2026-11-24T21:31:00+00:00", queued, now) == queued
    assert fax.sent_moment("0001-01-01T00:00:00Z", queued, now) == queued
    assert fax.sent_moment("2026-11-26T09:00:00+00:00", queued, now) == now
    assert runtime.practice_date(fax.sent_moment("2026-11-25T02:31:00+00:00", queued, now)) == "2026-11-24"


def test_one_placeholder_moment_does_not_stop_every_items_state(store, monkeypatch):
    """A service that words an empty time as the calendar's first moment ('0001-01-01T00:00:00Z', the placeholder of
    services written on .NET) for one fax: no zone can move it, and the states of every item must still be worked out (the
    day-cut never raised; moving it to the practice's zone raised OverflowError and stopped the whole batch). The fax is
    dated the day it was queued."""
    from remitwatch import actions, ema_reports as er, states
    from tests import ema_fixtures as F
    from tests.test_states import denied, item, line_key
    monkeypatch.delenv(runtime.TZ_SETTING, raising=False)
    states._RESPONSE_CACHE.clear()
    states._RECOVERY_CACHE.clear()
    er.ensure_schema(store.conn)
    actions.ensure_fax_tables(store.conn)
    c = store.conn
    for n, fid in enumerate(("F-ok", "F-odd"), 1):
        tk = denied(store, trace=F.check_no(n), pcn=F.claim_id(n), paid_on="2026-11-02", received="2026-10-20")
        item(store, finding_id=fid, patient_control_number=F.claim_id(n), line_key=line_key(store, tk), claim_key=f"{tk}-0",
             transaction_key=tk, payment_date="2026-11-02", status="appealed", appealed_at=NIGHT, status_changed_at=NIGHT)
    c.execute("""INSERT INTO faxes (fax_key, direction, finding_id, payer_group, number, purpose, level, status, created_at, sent_at)
                 VALUES ('X1', 'out', 'F-odd', 'Anthem / BCBS', '8005550142', 'appeal', 1, 'sent', ?, '0001-01-01T00:00:00Z')""", (NIGHT,))
    c.commit()
    for v in ("0001-01-01T00:00:00Z", "9999-12-31T23:59:59-05:00"):
        assert runtime.practice_date(v) == v[:10]
    items = [dict(r) for r in c.execute("SELECT * FROM findings ORDER BY finding_id")]
    got = states.states_of(c, items, today="2026-11-30")
    assert set(got) == {"F-ok", "F-odd"} and got["F-ok"]["work_state"] == "waiting", got
    odd = dict(c.execute("SELECT * FROM findings WHERE finding_id='F-odd'").fetchone())
    assert {e["kind"]: e["date"] for e in states.work_evidence(c, odd, today="2026-11-30")}["fax"] == "2026-11-24"


def test_a_value_that_is_no_day_is_no_day():
    """runtime.practice_date: a fax line's words for a time, or anything that is not a day, give '' (no day), never their
    first ten characters, which sort after every real day."""
    for v in ("Nov/24/2026 21:31:00", "Nov/24/202", "2026-13-45", "20261124", "soon", ""):
        assert runtime.practice_date(v) == "", v
    assert runtime.practice_date("2026-11-24 21:31:00") == "2026-11-24" and runtime.practice_date("2026-11-24") == "2026-11-24"


# ------------------------------------------------------------------------------------ the day the last file came
ARRIVALS = {"afternoon": "2026-11-13T20:30:00+00:00",   # 3:30 PM Eastern on Friday Nov 13 (UTC's day is the 13th too)
            "evening": "2026-11-14T01:30:00+00:00"}     # 8:30 PM Eastern on Nov 13 (UTC's day is already the 14th)


@pytest.mark.parametrize("when", sorted(ARRIVALS))
def test_the_stale_feed_alarm_and_the_data_tab_date_the_last_file_on_the_practices_day(store, monkeypatch, when):
    """The last remittance file came on Nov 13 where the practice is, in the afternoon or at 8:30 PM; today is Nov 24 and
    the payments reach Nov 12 (12 days behind, under the 14-day line). Eleven practice days with no new file is more than
    STALE_DAYS (10): the stale-feed alarm is due, and the Data tab says the file came on Nov 13. Read as UTC's day, the
    evening file came on the 14th: ten days, no alarm, and the next day named."""
    from remitwatch import alerts, insights
    monkeypatch.delenv(runtime.TZ_SETTING, raising=False)
    store.conn.execute("""INSERT INTO files (sha256, source_path, source_name, kind, ingested_at, status)
                          VALUES ('a1b2', 'in/remit.835', 'remit.835', '835', ?, 'ok')""", (ARRIVALS[when],))
    store.conn.commit()
    eng = alerts.AlertEngine(store.conn, today="2026-11-24", asof="2026-11-12")
    eng.stale_rule()
    stale = [a for a in eng.active.values() if a["kind"] == "STALE_FEED"]
    assert alerts.STALE_DAYS == 10 and stale, when
    assert json.loads(stale[0]["detail"])["last_file_ingested"] == "2026-11-13", stale[0]["detail"]
    [feed] = [h for h in insights.data_health(store.conn, "2026-11-12", "2026-11-24", feed="2026-11-12") if h["key"] == "feed"]
    assert feed["last_file"] == "2026-11-13", feed["last_file"]


# ------------------------------------------------------------------------------------------- the words a page says
def test_a_changed_fax_number_is_dated_the_practices_day(store, monkeypatch):
    """A payer's fax number changed at 9:30 PM Eastern on Nov 24: a retry or a follow-up waits for a person, and the hold
    says the number was changed on Nov 24 (the practice's day), not UTC's 25th. Both store kinds."""
    from remitwatch import actions, fax
    monkeypatch.delenv(runtime.TZ_SETTING, raising=False)
    c = store.conn
    actions.apply_payer_fax(c, {"payer_group": "Anthem / BCBS", "purpose": "appeal", "number": "8005550142", "actor": "Biller One",
                                "at": "2026-11-20T15:00:00+00:00"})
    actions.apply_payer_fax(c, {"payer_group": "Anthem / BCBS", "purpose": "appeal", "number": "8005550199", "actor": "Biller One",
                                "at": NIGHT})
    nb, hold = fax.number_hold(c, {"payer_group": "Anthem / BCBS", "payer_kind": "commercial"}, 1, [])
    assert nb["number"] == "8005550199" and hold and "was changed on 2026-11-24 and nobody has confirmed it yet" in hold, hold


def test_the_brief_names_the_practices_zone(store, monkeypatch):
    """The pages date a stored moment the way the server does: in the practice's zone, which the brief names beside the
    practice's day. A name this host does not know falls back to the default, as the server's own day does."""
    from remitwatch import insights
    for setting, want in ((None, "America/New_York"), ("America/Chicago", "America/Chicago"), ("Etc/GMT+12", "Etc/GMT+12"),
                          ("Not/A_Zone", "America/New_York")):
        if setting is None:
            monkeypatch.delenv(runtime.TZ_SETTING, raising=False)
        else:
            monkeypatch.setenv(runtime.TZ_SETTING, setting)
        assert runtime.practice_zone_name() == want, setting
        b = insights.brief(store.conn)
        assert (b["today"], b["tz"]) == (runtime.practice_today(), want), (setting, b.get("tz"))


# The pages say a stored moment's day (a send, a fax number's change) on the practice's calendar: today.js practiceDate,
# with the zone the brief names. Run in node with the page's own helpers, a brief and the drawer's facts line.
MOMENT_JS = r"""
const fs = require('fs'), vm = require('vm');
const [todayPath, featuresPath, casesJson] = process.argv.slice(-3);
const today = fs.readFileSync(todayPath, 'utf8'), features = fs.readFileSync(featuresPath, 'utf8');
const grab = (src, re) => { const m = src.match(re); if (!m) throw new Error('not found: ' + re); return m[0]; };
const helper = grab(today, /function practiceDate\([^\n]*\n/);
const facts = grab(features, /const facts = \[\['Billed code'[\s\S]*?\.filter\(x=>x\[1\]!=null && x\[1\]!==''\);/);
const out = [];
for (const [brief, iso] of JSON.parse(casesJson)) {
  const ctx = {state: {brief}, f: {appealed_at: iso}, money2: x => String(x), Intl, Object, String, Date, isNaN};
  vm.runInNewContext(helper + facts + 'said = practiceDate(f.appealed_at); shown = (facts.find(x => x[0] === "Appealed") || [])[1];', ctx);
  out.push([ctx.said, ctx.shown]);
}
console.log(JSON.stringify(out));
"""


def test_the_pages_say_a_moment_on_the_practices_day():
    """Marked Sent at 9:30 PM Eastern on Nov 24: the drawer's Appealed says Nov 24, as the server dates it, where it said
    UTC's 25th. A plain day, a time with no zone, and a page with no brief yet (or a zone the browser does not know) say
    the day as written."""
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    static = os.path.join(root, "remitwatch", "static")
    east = {"today": "2026-11-24", "tz": "America/New_York"}
    cases = [[east, NIGHT], [east, "2026-11-25T02:30:00.123456+00:00"], [east, "2026-11-25T15:00:00Z"],
             [{"today": "2026-11-25", "tz": "Etc/GMT-14"}, "2026-11-24T10:30:00+00:00"], [east, "2026-11-25"],
             [east, "2026-11-25T02:30:00"], [None, NIGHT], [{"today": "2026-11-24", "tz": "Not/A_Zone"}, NIGHT]]
    res = subprocess.run([node, "-e", MOMENT_JS, os.path.join(static, "today.js"), os.path.join(static, "features.js"), json.dumps(cases)],
                         capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout) == [["2026-11-24"] * 2, ["2026-11-24"] * 2, ["2026-11-25"] * 2, ["2026-11-25"] * 2,
                                      ["2026-11-25"] * 2, ["2026-11-25"] * 2, ["2026-11-25"] * 2, ["2026-11-25"] * 2]


# What the pages' own helpers say for a stored moment, run in node with the brief: the Data tab's day (data.js dtDay: a
# held report's decision, a check, a file read, a run), the Results tab's day (results.js rsDate: a week frozen), and the
# date and time the drawer's History, "Why did this change?", the Progress activity, the feed status and the fax lists show
# (today.js practiceTime, through features.js atTime and faxes.js faxTime).
HELPERS_JS = r"""
const fs = require('fs'), vm = require('vm');
const [dir, casesJson] = process.argv.slice(-2);
const read = f => fs.readFileSync(dir + '/' + f, 'utf8');
const grab = (src, re) => { const m = src.match(re); if (!m) throw new Error('not found: ' + re); return m[0]; };
const today = read('today.js');
const code = [grab(today, /function practiceDate\([^\n]*\n/), grab(today, /function practiceTime\([^\n]*\n/), grab(today, /function longDate\([^\n]*\n/),
              grab(read('data.js'), /function dtDay\([^\n]*\n/), grab(read('results.js'), /function rsDate\([^\n]*\n/),
              grab(read('features.js'), /function atTime\([^\n]*\n/), grab(read('faxes.js'), /const faxTime = [^\n]*\n/)].join('');
const out = [];
for (const [brief, iso] of JSON.parse(casesJson)) {
  const ctx = {state: {brief}, esc: s => String(s), Intl, Object, String, Date, isNaN, JSON};
  vm.runInNewContext(code + 'r = {dtDay: dtDay(ISO), rsDate: rsDate(ISO) === rsDate(practiceDate(ISO)), atTime: atTime(ISO), faxTime: faxTime(ISO), time: practiceTime(ISO)};'
                     .replace(/ISO/g, JSON.stringify(iso)), ctx);
  out.push(ctx.r);
}
console.log(JSON.stringify(out));
"""


def test_the_pages_helpers_say_a_stored_moment_where_the_practice_is():
    """A held report discarded, a fax sent or a status changed at 9:30 PM Eastern on Nov 24: the Data tab says Nov 24, the
    History and the fax lists say '2026-11-24 21:30', the Results tab dates a moment on the practice's day. Before the brief
    has loaded (no zone yet), a time is UTC's and says so; a plain day and a time with no zone are said as written."""
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    static = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "remitwatch", "static")
    east = {"today": "2026-11-24", "tz": "America/New_York"}
    cases = [[east, NIGHT], [None, NIGHT], [east, "2026-11-24"], [east, "2026-11-24 21:31:00"], [{"today": "2026-11-24", "tz": "Not/A_Zone"}, NIGHT]]
    res = subprocess.run([node, "-e", HELPERS_JS, static, json.dumps(cases)], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    got = json.loads(res.stdout)
    assert got[0] == {"dtDay": "Nov 24, 2026", "rsDate": True, "atTime": "2026-11-24 21:30", "faxTime": "2026-11-24 21:30",
                      "time": "2026-11-24 21:30"}, got[0]
    assert got[1]["time"] == got[1]["atTime"] == got[1]["faxTime"] == "2026-11-25 02:30 UTC", got[1]
    assert got[2]["dtDay"] == "Nov 24, 2026" and got[2]["time"] == "2026-11-24", got[2]
    assert got[3]["time"] == "2026-11-24 21:31", got[3]
    assert got[4]["time"] == "2026-11-25 02:30 UTC", got[4]          # a zone this browser does not know: UTC's, said so


def test_no_page_shows_a_stored_moments_utc_date_and_time_without_saying_so():
    """A stored moment cut to 'YYYY-MM-DD HH:MM' is UTC's date and time, the next day's evening from 7 PM Eastern. Each page
    line that cuts one goes through today.js practiceTime (the practice's date and time) or says UTC on the same line."""
    import re
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "remitwatch", "static")
    cut = re.compile(r"replace\(\s*'T'\s*,\s*' '\s*\)\s*\.slice\(\s*0\s*,\s*16\s*\)")
    bad = []
    for name in sorted(os.listdir(root)):
        if not name.endswith((".js", ".html")):
            continue
        with open(os.path.join(root, name), encoding="utf-8") as fh:
            for n, line in enumerate(fh, 1):
                if cut.search(line) and "practiceTime" not in line and "UTC" not in line:
                    bad.append(f"{name}:{n}")
    assert not bad, bad


def test_no_page_cuts_a_stored_moment_to_its_utc_day():
    """A stored moment (a send, a fax, a save, a decision, a run: a time with its zone) is said on the practice's day on
    every page (today.js practiceDate), never cut to its first ten characters, which is UTC's day: from 7 PM Eastern the
    next one. A line that cuts one does so only as practiceDate's fallback, on the same line."""
    import re
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "remitwatch", "static")
    moment = re.compile(r"(_at|\.at|first_seen|last_seen|\basked)\b[^\n]{0,40}?\.slice\(0, ?10\)")
    bad = []
    for name in sorted(os.listdir(root)):
        if not name.endswith((".js", ".html")):
            continue
        with open(os.path.join(root, name), encoding="utf-8") as fh:
            for n, line in enumerate(fh, 1):
                if moment.search(line) and "practiceDate" not in line:
                    bad.append(f"{name}:{n}")
    assert not bad, bad


# ------------------------------------------------------------------------------------- the other days the site counts
def test_the_weekly_reports_closed_this_week_starts_on_the_practices_day_a_week_ago(store, monkeypatch):
    """The weekly report's "closed this week because EMA shows them paid" counts from the start of the practice's day a
    week ago: an item closed half an hour after that start counts, one closed half an hour before it does not. SQL's
    datetime('now') is UTC and written with a space, which a stored time (with its T) always passes on its own day.
    Both store kinds."""
    from remitwatch import report
    pin_zone(monkeypatch)
    store.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('ema:wiring', ?)", (json.dumps({"on": True}),))
    start = datetime.fromisoformat(runtime.practice_day_start(runtime.practice_today(-7)))
    for fid, when in (("F-in", start + timedelta(minutes=30)), ("F-out", start - timedelta(minutes=30))):
        _item(store.conn, fid, status="resolved", recovered_cents=2500, auto_note="EMA shows this line paid",
              status_changed_at=when.isoformat(timespec="seconds"))
    lines = report.ema_section(store)
    assert "- Closed this week because EMA shows them paid: 1 items, $25" in lines, lines


def test_a_second_upload_of_the_same_name_is_named_for_the_practices_day(store, monkeypatch):
    """EMA names every download the same, so a second export of that name is kept as '<name> (uploaded <day>)': the day
    it came where the practice is, as the Upload page dates it."""
    from remitwatch import uploads
    pin_zone(monkeypatch)
    c = store.conn
    c.execute("CREATE TABLE IF NOT EXISTS ema_sources (source_name TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS uploads (kind TEXT, path TEXT)")
    c.execute("INSERT INTO ema_sources (source_name) VALUES ('charges.csv')")
    c.commit()
    assert uploads._free_name(c, "charges", ".csv") == f"charges (uploaded {runtime.practice_today()}).csv"


def test_a_persons_work_counts_on_the_practices_day_in_the_results_and_the_self_check(store, monkeypatch):
    """A Sent marked, a fax sent and a fix recorded at 9:30 PM Eastern on Nov 24 are Nov 24's work in the Results figures
    (the week they count in), minutes the owner saved then are in force that day, and that Sent is not work after an
    answer the payer dated Nov 25 (the self-check's worked and waiting). Both store kinds."""
    from remitwatch import actions, results, selfcheck as sc
    monkeypatch.delenv(runtime.TZ_SETTING, raising=False)
    c = store.conn
    actions.ensure_fax_tables(c)
    _item(c, "F-sent", status="appealed", payment_date="2026-11-25")
    c.execute("INSERT INTO finding_events (finding_id, at, kind, from_status, to_status, actor) VALUES ('F-sent', ?, 'manual', 'open', "
              "'appealed', 'Biller One')", (NIGHT,))
    _item(c, "F-fax", payment_date="2026-11-20")
    c.execute("""INSERT INTO faxes (fax_key, direction, finding_id, payer_group, number, purpose, level, status, created_at, sent_at)
                 VALUES ('X1', 'out', 'F-fax', 'Anthem / BCBS', '8005550142', 'appeal', 1, 'sent', ?, ?)""", (NIGHT, NIGHT))
    c.commit()
    work = results.work_of(c, ["F-sent", "F-fax"])
    assert [(w["kind"], w["date"]) for w in work["F-sent"]] == [("sent", "2026-11-24")], work
    assert [(w["kind"], w["date"]) for w in work["F-fax"]] == [("fax", "2026-11-24")], work
    # a fix recorded that evening is that day's fix, and minutes the owner saved that evening are in force that day
    c.execute("INSERT INTO finding_events (finding_id, at, kind, note, actor) VALUES ('F-fax', ?, 'fix', 'credentialing fixed', 'Biller One')",
              (NIGHT,))
    c.commit()
    assert [m["date"] for m in results._fix_markers(c)] == ["2026-11-24"]
    saved = {"history": [{"at": NIGHT, "values": {"minutes": {"fax": 99}}}]}
    assert results._minutes_at(saved, "2026-11-24")["fax"] == 99 and results._minutes_at(saved, "2026-11-23")["fax"] == 10
    items = [dict(r) for r in c.execute("SELECT * FROM findings WHERE finding_id='F-sent'")]
    assert sc._work_after(c, items, "2026-11-30") == set()
    c.execute("UPDATE finding_events SET at='2026-11-25T15:00:00+00:00'")      # 10 AM on the 25th: after the answer
    assert sc._work_after(c, items, "2026-11-30") == {"F-sent"}


def test_a_held_trizetto_sign_in_is_dated_the_practices_day(store, monkeypatch):
    """The Data tab says the day TriZetto refused the feed's sign-in where the practice is: Nov 24 for a refusal at 9:30 PM
    Eastern, not UTC's 25th."""
    from remitwatch import insights, trizetto
    monkeypatch.delenv(runtime.TZ_SETTING, raising=False)
    store.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (trizetto.REFUSED_META, json.dumps({"who": "h", "at": NIGHT})))
    env = {"TRIZETTO_SFTP_USER": "SITE", "TRIZETTO_SFTP_PASSWORD": "x", "TRIZETTO_SFTP_PASSWORD_SET": "2026-10-01"}
    [h] = insights.trizetto_health(store.conn, "2026-11-25", env)
    assert h["at"] == "2026-11-24" and h["title"].endswith("on 2026-11-24"), h
