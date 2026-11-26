"""
The SWING alert (remitwatch.alerts): one analysis run that adds or removes far more open items or dollars than runs
normally do is raised as an alert on every page and in the alert mail, with its numbers, whatever the reason. It is an
interim guard: it holds nothing and changes no item.

Everything here goes through the real reader and detection: invented remittances are read into a store, detection runs,
and the alert engine compares the run with the one before it. Both store kinds (the server's store file and the compact
in-memory image the serverless host loads) and both hosts' cycles. Invented data only: claim ids, names, member numbers,
amounts and file names are made up, and some are planted only to prove that none of it reaches an alert, a page or a mail.
"""
from __future__ import annotations

import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch import alerts, detect  # noqa: E402
from remitwatch.actions import apply_alert_ack, apply_status  # noqa: E402
from remitwatch.alerts import digest, notify, run_alerts  # noqa: E402
from remitwatch.detect import Detector  # noqa: E402
from remitwatch.store import Store  # noqa: E402
from tests.test_features import claim, clean_visits, svc  # noqa: E402
from tests.test_cloud import box  # noqa: E402,F401  (the serverless fixture: a fake Dropbox and its state folder)
from tests.test_remitwatch import remit  # noqa: E402

DAY1, DAY2, DAY3 = "2026-11-20", "2026-11-21", "2026-11-22"
RUN1, RUN2, RUN3 = f"{DAY1}T10:17:00+00:00", f"{DAY2}T10:17:00+00:00", f"{DAY3}T10:17:00+00:00"
READ1, READ2, READ3 = f"{DAY1}T09:00:00+00:00", f"{DAY2}T09:00:00+00:00", f"{DAY3}T09:00:00+00:00"
# planted in the files and the claims: no byte of it may reach an alert, a page or a mail
PLANTED = ("QQPLANTEDLAST", "ZZPLANTEDFIRST", "MZZ998877", "PLANTED-FILE", "1984-02-29", "C017", "TRACE")
_CTRL = [100]


def denials(start: int, n: int) -> list[str]:
    """Visits the payer denied (CO-50): each one is an open item worth the payer's usual 99213 rate, $76.09."""
    return [claim(start + i, svc("99213", "156.66", "0.00", None, "CAS*CO*50*156.66~")) for i in range(n)]


def paid(start: int, n: int) -> list[str]:
    """The same visits paid on a later remittance: each closes its item."""
    return [claim(start + i, svc("99213", "156.66", "76.09", "76.09", "CAS*CO*45*80.57~")) for i in range(n)]


def planted(claims: list[str]) -> list[str]:
    return [c.replace("NM1*QC*1*SAMPLE*PAT****MI*", "NM1*QC*1*QQPLANTEDLAST*ZZPLANTEDFIRST****MI*MZZ998877") for c in claims]


def write(folder, claims: list[str], name: str, pay_date: str = "20260415", per_file: int = 200) -> list[str]:
    os.makedirs(folder, exist_ok=True)
    paths = []
    for k in range(0, len(claims), per_file):
        _CTRL[0] += 1
        p = os.path.join(str(folder), f"{name}-{k // per_file:02d}.RMT")
        with open(p, "wb") as fh:
            fh.write(remit("".join(claims[k:k + per_file]), "100.00", seg_count=99, isa_ctrl=f"{_CTRL[0]:09d}", pay_date=pay_date))
        paths.append(p)
    return paths


def new_store(kind: str, tmp_path) -> Store:
    """The server's store file, or the compact in-memory store the serverless host loads its image into."""
    return Store(str(tmp_path / "store.sqlite")) if kind == "file" else Store(":memory:", compact=True)


def read(store: Store, paths: list[str], at: str) -> None:
    """Read files through the real reader, as read at `at`."""
    store.ingest_835_paths(paths)
    marks = ",".join("?" * len(paths))
    store.conn.execute(f"UPDATE files SET ingested_at=? WHERE source_path IN ({marks})", (at, *paths))
    store.conn.commit()


def detect_at(store: Store, run_id: str, at: str) -> None:
    """One detection run, dated `at` (the clock of a real run is its start)."""
    Detector(store, run_id=run_id).run()
    store.conn.execute("UPDATE detection_runs SET started_at=?, finished_at=? WHERE run_id=?", (at, at, run_id))
    store.conn.execute("UPDATE progress_snapshots SET at=? WHERE run_id=?", (at, run_id))
    store.conn.commit()


def world(store: Store, tmp_path, extra_denials: int = 0) -> None:
    """Day one: a practice with paid visits and 40 denied ones, plus `extra_denials` more in files of their own (claim ids
    from CB0020000), analysed once."""
    claims = clean_visits(1, 30, "99213", "156.66", "76.09") + clean_visits(100, 30, "99214", "223.65", "117.60") + denials(500, 40)
    paths = write(tmp_path / "in", claims, "day1") + (write(tmp_path / "in", denials(20000, extra_denials), "extra") if extra_denials else [])
    read(store, paths, READ1)
    detect_at(store, "r1", RUN1)


def swings(conn, status: str | None = None) -> list[dict]:
    rows = [dict(r) for r in conn.execute("SELECT * FROM alerts WHERE kind='SWING' ORDER BY first_seen, alert_id")]
    for r in rows:
        r["detail"] = json.loads(r["detail"] or "{}")
    return [r for r in rows if status is None or r["status"] == status]


def text_of(a: dict) -> str:
    return a["title"] + "\n" + json.dumps(a["detail"])


@pytest.fixture(params=["file", "image"])
def kind(request):
    return request.param


@pytest.fixture(autouse=True)
def _eastern(monkeypatch):
    # the practice is in US Eastern time: a run at 10:17 UTC ran that morning there, and its alert's seven days count from
    # that day (alerts.swing_rule), whatever zone the suite runs in
    monkeypatch.setenv("REMITWATCH_TZ", "America/New_York")


# --------------------------------------------------------------------------- what raises it and what does not

def test_a_flood_of_1600_new_items_in_one_run_raises_a_high_alert_with_its_numbers(kind, tmp_path):
    store = new_store(kind, tmp_path)
    world(store, tmp_path)
    read(store, write(tmp_path / "in", denials(10000, 1600), "flood"), READ2)
    detect_at(store, "r2", RUN2)
    run_alerts(store.conn, today=DAY2)
    [a] = swings(store.conn, "new")
    d = a["detail"]
    assert a["severity"] == "high" and a["family"] == "swing"
    assert d["change"]["n"] == 1600 and d["change"]["cents"] == 1600 * 7609
    assert d["before"]["n"] == 40 and d["after"]["n"] == 1640
    den = next(c for c in d["by_category"] if c["category"] == "denial")
    assert (den["before_n"], den["after_n"], den["change_n"]) == (40, 1640, 1600)
    assert d["files"] == {"835": 8}
    assert "added 1,600 open items" in a["title"] and "+$121,744" in a["title"]
    lines = "\n".join(d["lines"])
    assert "40 at the run before" in lines and "1,640 after this run" in lines and "Denials: 40 to 1,640 open items" in lines
    assert "8 remittance (835) files" in lines and "2026-11-20 10:17 UTC" in lines
    assert d["action"] == ("Before working new items, open the Data tab and check the files read since 2026-11-20 10:17 UTC. "
                           "If one is wrong, tell the practice owner and do not work the new items yet.")
    store.close()


def test_a_run_that_closes_800_items_at_once_raises_it(kind, tmp_path):
    store = new_store(kind, tmp_path)
    world(store, tmp_path, extra_denials=900)
    read(store, write(tmp_path / "in", paid(20000, 800), "paid-later", pay_date="20260501"), READ2)
    detect_at(store, "r2", RUN2)
    run_alerts(store.conn, today=DAY2)
    [a] = swings(store.conn, "new")
    assert a["severity"] == "high" and a["detail"]["change"]["n"] == -800
    assert "removed 800 open items" in a["title"]
    store.close()


def test_a_run_that_drops_800_items_raises_it(kind, tmp_path):
    """Items that disappear (their remittance is taken out of the store, so the rules no longer find them) are a fall too."""
    store = new_store(kind, tmp_path)
    world(store, tmp_path, extra_denials=800)
    store.conn.execute("UPDATE transactions SET superseded=1 WHERE transaction_key IN (SELECT DISTINCT transaction_key FROM claims "
                       "WHERE patient_control_number >= 'CB0020000' AND patient_control_number < 'CB0021000')")
    store.conn.commit()
    detect_at(store, "r2", RUN2)
    run_alerts(store.conn, today=DAY2)
    [a] = swings(store.conn, "new")
    assert a["severity"] == "high" and a["detail"]["change"]["n"] == -800 and a["detail"]["files"] == {}
    assert "No files were read between the two runs." in a["detail"]["lines"]
    store.close()


def test_a_run_that_swaps_800_items_for_800_others_raises_it_though_the_totals_do_not_move(kind, tmp_path):
    """Guard audit L3-9: per-category totals net out when one run takes many open items off the list and puts as many others
    on it (different claims, or the same money under new item ids). The alarm also counts, item by item, the open items that
    appeared and those that vanished in the run, by category."""
    store = new_store(kind, tmp_path)
    world(store, tmp_path, extra_denials=800)
    read(store, write(tmp_path / "in", denials(30000, 800), "others"), READ2)
    store.conn.execute("UPDATE transactions SET superseded=1 WHERE transaction_key IN (SELECT DISTINCT transaction_key FROM claims "
                       "WHERE patient_control_number >= 'CB0020000' AND patient_control_number < 'CB0021000')")
    store.conn.commit()
    detect_at(store, "r2", RUN2)
    run_alerts(store.conn, today=DAY2)
    [a] = swings(store.conn, "new")
    d = a["detail"]
    assert a["severity"] == "high" and d["change"] == {"n": 0, "cents": 0}, d["change"]
    den = {c["category"]: c for c in d["by_category"]}["denial"]
    assert (den["appeared_n"], den["vanished_n"]) == (800, 800)
    assert den["appeared_cents"] == den["vanished_cents"] == 800 * 7609
    assert d["moves"] == {"appeared_n": 800, "appeared_cents": 800 * 7609, "vanished_n": 800, "vanished_cents": 800 * 7609}
    assert "put 800 open items on the list and took 800 off" in a["title"], a["title"]
    assert any("800 appeared and 800 vanished" in line for line in d["lines"]), d["lines"]
    store.close()


def test_an_ordinary_run_counts_what_appeared_and_vanished_without_raising_it(kind, tmp_path):
    """A normal day: some items close, a few new ones come. The counts are kept per run; nothing is raised."""
    store = new_store(kind, tmp_path)
    world(store, tmp_path)
    read(store, write(tmp_path / "in", paid(500, 25) + denials(40000, 30), "day2", pay_date="20260501"), READ2)
    detect_at(store, "r2", RUN2)
    moves = {r[0]: tuple(r[1:]) for r in store.conn.execute(
        "SELECT category, appeared_n, appeared_cents, vanished_n, vanished_cents FROM swing_moves WHERE run_id='r2'")}
    assert moves["denial"] == (30, 30 * 7609, 25, 25 * 7609)
    run_alerts(store.conn, today=DAY2)
    assert swings(store.conn) == []
    store.close()


def test_a_run_that_leaves_no_open_item_at_all_raises_it(kind, tmp_path):
    """Everything gone: the run keeps no snapshot row at all. That is the largest fall there is, not 'nothing to compare'."""
    store = new_store(kind, tmp_path)
    world(store, tmp_path, extra_denials=800)
    store.conn.execute("UPDATE transactions SET superseded=1")
    store.conn.commit()
    detect_at(store, "r2", RUN2)
    assert store.conn.execute("SELECT COUNT(*) FROM progress_snapshots WHERE run_id='r2'").fetchone()[0] == 0
    run_alerts(store.conn, today=DAY2)
    [a] = swings(store.conn, "new")
    assert a["severity"] == "high" and a["detail"]["change"]["n"] == -840 and a["detail"]["after"]["n"] == 0
    assert "removed 840 open items" in a["title"]
    store.close()


def test_the_first_load_into_an_empty_store_is_not_a_swing(kind, tmp_path):
    store = new_store(kind, tmp_path)
    detect_at(store, "r1", RUN1)                               # a new site's first run: nothing read yet
    read(store, write(tmp_path / "in", denials(10000, 1600), "first"), READ2)
    detect_at(store, "r2", RUN2)
    run_alerts(store.conn, today=DAY2)
    assert swings(store.conn) == []
    store.close()


def test_the_largest_normal_changes_raise_nothing(kind, tmp_path):
    """The largest changes the live store's twelve months of remittances make in one category: +190 open items (a week of
    bundling) and -97 (a day of denials paid later). Neither is a swing."""
    store = new_store(kind, tmp_path)
    world(store, tmp_path, extra_denials=200)
    read(store, write(tmp_path / "in", denials(30000, 190), "week"), READ2)
    detect_at(store, "r2", RUN2)
    read(store, write(tmp_path / "in", paid(20000, 97), "day3", pay_date="20260501"), READ3)
    detect_at(store, "r3", RUN3)
    runs = _runs(store.conn)
    moved = [next(c["change_n"] for c in alerts.swing_between(store.conn, x, y)["by_category"] if c["category"] == "denial")
             for x, y in zip(runs, runs[1:])]
    assert moved == [190, -97]
    run_alerts(store.conn, today=DAY3)
    assert swings(store.conn) == []
    store.close()


def _runs(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT run_id, started_at, version FROM detection_runs ORDER BY finished_at, run_id")]


LEVELS = [  # (what, the change at the level split over two categories or in one, the step past it): at the level nothing, past it an alert
    ("all items added", [("denial", alerts.SWING_RISE_ITEMS // 2, 0), ("bundling", alerts.SWING_RISE_ITEMS - alerts.SWING_RISE_ITEMS // 2, 0)], (1, 0)),
    ("all dollars added", [("denial", 0, alerts.SWING_RISE_CENTS // 2), ("bundling", 0, alerts.SWING_RISE_CENTS - alerts.SWING_RISE_CENTS // 2)], (0, 1)),
    ("all items removed", [("denial", -(alerts.SWING_FALL_ITEMS // 2), 0), ("bundling", -(alerts.SWING_FALL_ITEMS - alerts.SWING_FALL_ITEMS // 2), 0)], (-1, 0)),
    ("all dollars removed", [("denial", 0, -(alerts.SWING_FALL_CENTS // 2)), ("bundling", 0, -(alerts.SWING_FALL_CENTS - alerts.SWING_FALL_CENTS // 2))], (0, -1)),
    ("items added in one category", [("fees", alerts.SWING_RISE_ITEMS_CATEGORY, 0)], (1, 0)),
    ("dollars added in one category", [("fees", 0, alerts.SWING_RISE_CENTS_CATEGORY)], (0, 1)),
    ("items removed in one category", [("fees", -alerts.SWING_FALL_ITEMS_CATEGORY, 0)], (-1, 0)),
    ("dollars removed in one category", [("fees", 0, -alerts.SWING_FALL_CENTS_CATEGORY)], (0, -1)),
]


@pytest.mark.parametrize("what,moves,step", LEVELS, ids=[x[0] for x in LEVELS])
def test_each_alarm_level_is_exact(kind, tmp_path, what, moves, step):
    """At a level nothing is raised; one item or one cent past it, the alert is. Pins the measured levels exactly."""
    for extra, expect in ((0, []), (1, ["SWING"])):
        store = new_store(kind, tmp_path / f"{extra}")
        c = store.conn
        detect.ensure_schema(c)
        base = {"denial": (5000, 400_000_00), "bundling": (5000, 400_000_00), "fees": (5000, 400_000_00)}
        after = dict(base)
        for i, (cat, n, cents) in enumerate(moves):
            bump = extra if i == 0 else 0
            after[cat] = (base[cat][0] + n + bump * step[0], base[cat][1] + cents + bump * step[1])
        for rid, at, snap in (("r1", RUN1, base), ("r2", RUN2, after)):
            # two runs under this code's own rules: a pair whose later run ran under other rules is history (CH-3)
            c.execute("INSERT INTO detection_runs (run_id, started_at, finished_at, version) VALUES (?,?,?,?)", (rid, at, at, detect.DETECT_VERSION))
            c.executemany("INSERT INTO progress_snapshots (run_id, at, category, status, n, cents) VALUES (?,?,?,?,?,?)",
                          [(rid, at, cat, "open", n, cents) for cat, (n, cents) in snap.items()])
        c.commit()
        run_alerts(c, today=DAY2)
        assert [a["kind"] for a in swings(c, "new")] == expect, (what, extra)
        store.close()


def test_people_letting_go_hundreds_of_items_between_runs_is_not_the_run(kind, tmp_path):
    """A person's own status changes between two runs are their work, not the run's: they are left out of the comparison."""
    store = new_store(kind, tmp_path)
    world(store, tmp_path, extra_denials=400)
    ids = [r[0] for r in store.conn.execute("SELECT finding_id FROM findings WHERE status='open' ORDER BY finding_id LIMIT 300")]
    for fid in ids:
        apply_status(store.conn, fid, "dismissed", None, None, "invented biller", f"{DAY1}T15:00:00+00:00")
    store.conn.commit()
    detect_at(store, "r2", RUN2)
    snap = {r[0]: r[1] for r in store.conn.execute("SELECT status, n FROM progress_snapshots WHERE run_id='r2' AND category='denial'")}
    assert snap["dismissed"] == 300                          # the open list did shrink by 300
    run_alerts(store.conn, today=DAY2)
    assert swings(store.conn) == []
    # with a flood on top, the alert says what the run did and names the people's part apart
    read(store, write(tmp_path / "in", denials(40000, 1600), "flood"), READ3)
    for fid in ids[:10]:
        apply_status(store.conn, fid, "open", None, None, "invented biller", f"{DAY2}T15:00:00+00:00")
    store.conn.commit()
    detect_at(store, "r3", RUN3)
    run_alerts(store.conn, today=DAY3)
    [a] = swings(store.conn, "new")
    assert a["detail"]["change"]["n"] == 1600 and a["detail"]["people"]["n"] == 10
    assert any("People's own status changes" in x for x in a["detail"]["lines"])
    store.close()


def test_a_release_that_changed_the_rules_gives_info_not_high(kind, tmp_path, monkeypatch):
    store = new_store(kind, tmp_path)
    world(store, tmp_path)
    read(store, write(tmp_path / "in", denials(10000, 1600), "flood"), READ2)
    monkeypatch.setattr(detect, "DETECT_VERSION", "9.9-invented")
    detect_at(store, "r2", RUN2)
    run_alerts(store.conn, today=DAY2)
    [a] = swings(store.conn, "new")
    assert a["severity"] == "info" and a["detail"]["rules_changed"] is True
    assert "release" in a["title"]
    assert any("The rules changed between the two runs" in x and "9.9-invented" in x for x in a["detail"]["lines"])
    store.close()


def _flood_under_old_rules(store: Store, tmp_path, monkeypatch) -> str:
    """Runs r1 (a normal list) and r2 (a flood of 1,600 items) under rules older than this code's, as the live release
    left its store: that release had no alarm, so nothing was raised then. Returns this code's own rules version."""
    new = detect.DETECT_VERSION
    monkeypatch.setattr(detect, "DETECT_VERSION", "2.6-invented")
    world(store, tmp_path)
    read(store, write(tmp_path / "in", denials(10000, 1600), "flood"), READ2)
    detect_at(store, "r2", RUN2)
    monkeypatch.setattr(detect, "DETECT_VERSION", new)
    return new


def test_after_a_release_two_runs_under_the_old_rules_are_history(kind, tmp_path, monkeypatch):
    """Misuse check CH-3: the release's first run analyses again under its new rules. A pair of runs that both ran under
    the old rules is history: the old code's own flood is not raised now as a high alert, on every page for a week, beside
    the release's info alert. A run under the new rules is compared with the run before it as always."""
    store = new_store(kind, tmp_path)
    _flood_under_old_rules(store, tmp_path, monkeypatch)
    # the release's first run: its rules no longer read the flood's files (taken out here), so 1,600 items leave the list
    store.conn.execute("UPDATE transactions SET superseded=1 WHERE transaction_key IN (SELECT DISTINCT transaction_key FROM claims "
                       "WHERE patient_control_number >= 'CB0010000' AND patient_control_number < 'CB0012000')")
    store.conn.commit()
    detect_at(store, "r3", RUN3)
    run_alerts(store.conn, today=DAY3)
    got = swings(store.conn, "new")
    assert [(x["scope"], x["severity"]) for x in got] == [("r3", "info")], [(x["scope"], x["severity"], x["title"]) for x in got]
    [a] = got
    assert a["detail"]["rules_changed"] is True and a["detail"]["rules"] == ["2.6-invented", detect.DETECT_VERSION]
    assert a["detail"]["change"]["n"] == -1600 and "under new rules from a release" in a["title"]
    # a flood under the new rules is raised high, as before
    read(store, write(tmp_path / "in", denials(30000, 1600), "flood-later"), "2026-11-23T09:00:00+00:00")
    detect_at(store, "r4", "2026-11-23T10:17:00+00:00")
    run_alerts(store.conn, today="2026-11-23")
    got = {x["scope"]: x for x in swings(store.conn, "new")}
    assert sorted(got) == ["r3", "r4"] and got["r4"]["severity"] == "high" and got["r4"]["detail"]["rules_changed"] is False
    store.close()


def test_an_alert_raised_before_a_release_keeps_its_week(kind, tmp_path, monkeypatch):
    """CH-3's other half: an alert raised while the old rules were this code's own (two runs under the rules of the day)
    stays on the pages for its week after a release, with the numbers it was raised with, and clears seven days after the
    run that moved."""
    store = new_store(kind, tmp_path)
    new = detect.DETECT_VERSION
    monkeypatch.setattr(detect, "DETECT_VERSION", "2.8-invented")
    world(store, tmp_path)
    read(store, write(tmp_path / "in", denials(10000, 1600), "flood"), READ2)
    detect_at(store, "r2", RUN2)
    run_alerts(store.conn, today=DAY2)
    [a] = swings(store.conn, "new")
    assert a["severity"] == "high" and a["scope"] == "r2"
    monkeypatch.setattr(detect, "DETECT_VERSION", new)          # the release: this code's own rules
    detect_at(store, "r3", RUN3)                                # the same files under the new rules: nothing moves
    for day in (DAY3, "2026-11-27"):
        run_alerts(store.conn, today=day)
        [b] = swings(store.conn, "new")
        assert (b["alert_id"], b["title"], b["severity"]) == (a["alert_id"], a["title"], "high"), day
    run_alerts(store.conn, today="2026-11-28")                  # seven days after the run that moved
    assert swings(store.conn, "new") == [] and [x["alert_id"] for x in swings(store.conn, "cleared")] == [a["alert_id"]]
    store.close()


# --------------------------------------------------------------------------- how long it stays

def test_it_survives_quiet_runs_and_clears_seven_days_after_the_run_that_moved(kind, tmp_path):
    store = new_store(kind, tmp_path)
    world(store, tmp_path)
    read(store, write(tmp_path / "in", denials(10000, 1600), "flood"), READ2)
    detect_at(store, "r2", RUN2)
    run_alerts(store.conn, today=DAY2)
    [a] = swings(store.conn, "new")
    detect_at(store, "r3", RUN3)                               # the next run moves nothing
    for day in (DAY3, "2026-11-25", "2026-11-27"):
        out = run_alerts(store.conn, today=day)
        assert out["new"] == 0
        assert [x["alert_id"] for x in swings(store.conn, "new")] == [a["alert_id"]], day
    run_alerts(store.conn, today="2026-11-28")                 # seven days after the run that moved
    assert swings(store.conn, "new") == [] and swings(store.conn, "cleared")[0]["alert_id"] == a["alert_id"]
    store.close()


def test_a_run_in_the_evening_stays_seven_days_counted_where_the_practice_is(kind, tmp_path):
    """A run that moved at 9:30 PM Eastern on Nov 21 (UTC's 22nd) ran on Nov 21 where the practice is: its alert stays
    seven days counting that day, through Nov 27, and clears on Nov 28, as a morning run's does. Counted by UTC's date
    it stayed a day longer."""
    store = new_store(kind, tmp_path)
    world(store, tmp_path)
    read(store, write(tmp_path / "in", denials(10000, 1600), "flood"), READ2)
    detect_at(store, "r2", "2026-11-22T02:30:00+00:00")
    run_alerts(store.conn, today=DAY2)
    [a] = swings(store.conn, "new")
    run_alerts(store.conn, today="2026-11-27")
    assert [x["alert_id"] for x in swings(store.conn, "new")] == [a["alert_id"]]
    run_alerts(store.conn, today="2026-11-28")
    assert swings(store.conn, "new") == [] and swings(store.conn, "cleared")[0]["alert_id"] == a["alert_id"]
    store.close()


def test_it_leaves_the_pages_only_when_a_person_acknowledges_it(kind, tmp_path):
    store = new_store(kind, tmp_path)
    world(store, tmp_path)
    read(store, write(tmp_path / "in", denials(10000, 1600), "flood"), READ2)
    detect_at(store, "r2", RUN2)
    run_alerts(store.conn, today=DAY2)
    [a] = swings(store.conn, "new")
    apply_alert_ack(store.conn, a["alert_id"], f"{DAY2}T13:05:00+00:00")
    store.conn.commit()
    detect_at(store, "r3", RUN3)
    run_alerts(store.conn, today=DAY3)
    [b] = swings(store.conn)
    assert b["alert_id"] == a["alert_id"] and b["status"] == "acknowledged"     # not raised again as new by the next run
    run_alerts(store.conn, today="2026-11-28")
    assert swings(store.conn)[0]["status"] == "cleared"
    store.close()


def test_a_raised_alert_keeps_its_numbers_while_it_lasts(tmp_path):
    """A late journal entry can change what a pair of runs adds up to; an alert already raised stays raised all week."""
    store = new_store("file", tmp_path)
    world(store, tmp_path)
    read(store, write(tmp_path / "in", denials(10000, 1600), "flood"), READ2)
    detect_at(store, "r2", RUN2)
    run_alerts(store.conn, today=DAY2)
    [a] = swings(store.conn, "new")
    store.conn.execute("DELETE FROM progress_snapshots WHERE run_id='r1'")      # the pair can no longer be compared
    store.conn.commit()
    run_alerts(store.conn, today=DAY3)
    [b] = swings(store.conn, "new")
    assert b["alert_id"] == a["alert_id"] and b["title"] == a["title"]
    run_alerts(store.conn, today="2026-11-28")
    assert swings(store.conn, "new") == []
    store.close()


# --------------------------------------------------------------------------- what it may say

def test_no_file_name_and_no_patient_detail_reach_the_alert_or_the_mail(kind, tmp_path, monkeypatch):
    from remitwatch import report
    store = new_store(kind, tmp_path)
    world(store, tmp_path)
    folder = tmp_path / "in" / "QQPLANTEDLAST ZZPLANTEDFIRST 1984-02-29"
    read(store, write(folder, planted(denials(10000, 1600)), "PLANTED-FILE MZZ998877"), READ2)
    assert store.conn.execute("SELECT COUNT(*) FROM files WHERE source_name LIKE 'PLANTED-FILE%'").fetchone()[0] == 8
    assert store.conn.execute("SELECT COUNT(*) FROM claims WHERE patient_last='QQPLANTEDLAST' AND patient_id LIKE 'MZZ998877%'").fetchone()[0] == 1600
    detect_at(store, "r2", RUN2)
    run_alerts(store.conn, today=DAY2)
    [a] = swings(store.conn, "new")
    sent = []
    monkeypatch.setattr(report, "email_report", lambda text, subject="": sent.append(subject + "\n" + text) or True)
    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
    notify(store.conn, log=lambda *x: None)
    assert sent and "One analysis run added 1,600 open items" in sent[0]
    for blob in (text_of(a), sent[0]):
        for word in PLANTED:
            assert word not in blob, word
        assert ".RMT" not in blob and str(tmp_path) not in blob
    store.close()


# --------------------------------------------------------------------------- the alert mail

def _flooded(tmp_path) -> Store:
    store = new_store("file", tmp_path)
    world(store, tmp_path)
    read(store, write(tmp_path / "in", denials(10000, 1600), "flood"), READ2)
    detect_at(store, "r2", RUN2)
    return store


def test_the_alert_mail_carries_it_and_every_later_alert_mail_leads_with_it_until_acknowledged(tmp_path, monkeypatch):
    from remitwatch import report
    store = _flooded(tmp_path)
    run_alerts(store.conn, today=DAY2)
    [a] = swings(store.conn, "new")
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(report, "email_report", lambda text, subject="": sent.append((subject, text)) or True)
    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
    notify(store.conn, log=lambda *x: None)
    subject, text = sent[-1]
    assert subject.startswith("RemitWatch alert: One analysis run added 1,600 open items")
    assert a["title"] in text and a["detail"]["action"] in text and "Denials: 40 to 1,640 open items" in text
    # mailed once: a later run sends nothing new...
    assert notify(store.conn, log=lambda *x: None) == {"sent": 0}
    # ...but a mail about another alert still leads with it while nobody has acknowledged it
    store.conn.execute("""INSERT INTO alerts (alert_id, kind, severity, title, detail, status, first_seen, last_seen)
                          VALUES ('other', 'RAMP', 'high', 'Invented Payer is ramping up something', '{}', 'new', ?, ?)""", (RUN3, RUN3))
    store.conn.commit()
    notify(store.conn, log=lambda *x: None)
    subject, text = sent[-1]
    assert text.index(a["title"]) < text.index("Invented Payer is ramping up") and a["detail"]["action"] in text
    apply_alert_ack(store.conn, a["alert_id"])
    store.conn.execute("UPDATE alerts SET notified_at=NULL WHERE alert_id='other'")
    store.conn.commit()
    notify(store.conn, log=lambda *x: None)
    assert a["title"] not in sent[-1][1]
    store.close()


def test_a_release_swing_is_mailed_too(tmp_path, monkeypatch):
    from remitwatch import report
    store = new_store("file", tmp_path)
    world(store, tmp_path)
    read(store, write(tmp_path / "in", denials(10000, 1600), "flood"), READ2)
    monkeypatch.setattr(detect, "DETECT_VERSION", "9.9-invented")
    detect_at(store, "r2", RUN2)
    run_alerts(store.conn, today=DAY2)
    store.conn.execute("UPDATE alerts SET notified_at='2026-11-21T00:00:00+00:00' WHERE kind != 'SWING'")   # only the swing is waiting
    store.conn.commit()
    sent = []
    monkeypatch.setattr(report, "email_report", lambda text, subject="": sent.append(text) or True)
    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
    assert notify(store.conn, log=lambda *x: None)["sent"] == 1
    assert "[INFO]" in sent[0] and "The rules changed between the two runs" in sent[0]
    assert swings(store.conn)[0]["notified_at"]
    store.close()


def test_digest_still_reads_a_plain_list_of_alerts():
    text = digest([{"severity": "high", "title": "Invented Payer is ramping up something", "kind": "RAMP"}])
    assert text.startswith("RemitWatch: 1 new alert") and "[HIGH] Invented Payer is ramping up something" in text


# --------------------------------------------------------------------------- both hosts, end to end

def quiet(monkeypatch) -> None:
    """No real Dropbox, TriZetto, fax line, AI or mail: every such setting is removed for the test."""
    for k in list(os.environ):
        if k.startswith(("DROPBOX_", "TRIZETTO_", "SRFAX_", "ANTHROPIC_", "SMTP_")) or k in ("REPORT_TO", "ALERT_WEBHOOK_URL"):
            monkeypatch.delenv(k, raising=False)


def base_claims() -> list[str]:
    return clean_visits(1, 30, "99213", "156.66", "76.09") + clean_visits(100, 30, "99214", "223.65", "117.60") + denials(500, 40)


def test_the_server_cycle_raises_it_mails_it_and_its_dashboard_shows_it_until_acknowledged(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import remitwatch.api as api_mod
    from remitwatch import report
    from remitwatch.pipeline import run_cycle
    quiet(monkeypatch)
    sent: list[str] = []
    monkeypatch.setattr(report, "email_report", lambda text, subject="": sent.append(subject) or True)
    inbox, db = tmp_path / "inbox", str(tmp_path / "server.sqlite")
    write(inbox, base_claims(), "day1")
    cyc = lambda: run_cycle(db, sync=True, local=str(inbox), email=True, report=False, log=lambda *a: None)      # noqa: E731
    assert "detect" in cyc()
    time.sleep(1.1)                       # a run's id and its clock move in whole seconds
    write(inbox, denials(10000, 1600), "flood")
    out = cyc()
    assert out["alerts"]["by_kind"].get("SWING") == 1
    assert any(s.startswith("RemitWatch alert: One analysis run added 1,600 open items") for s in sent)
    monkeypatch.setattr(api_mod, "DB_PATH", db)
    monkeypatch.setattr(api_mod, "PASSWORD", "")
    tc = TestClient(api_mod.app)
    xhr = {"X-Requested-With": "remitwatch"}
    [sw] = [a for a in tc.get("/api/alerts?status=new").json()["items"] if a["kind"] == "SWING"]      # what every page's header reads
    assert sw["severity"] == "high" and sw["detail"]["action"].startswith("Before working new items") and sw["detail"]["lines"]
    time.sleep(1.1)
    assert "detect" not in cyc()          # a quiet run: still there
    assert [a["kind"] for a in tc.get("/api/alerts?status=new").json()["items"]].count("SWING") == 1
    assert tc.post(f"/api/alerts/{sw['alert_id']}/ack", headers=xhr).json() == {"ok": True}
    assert "SWING" not in [a["kind"] for a in tc.get("/api/alerts?status=new").json()["items"]]
    time.sleep(1.1)
    cyc()
    assert [a["status"] for a in tc.get("/api/alerts?status=all").json()["items"] if a["kind"] == "SWING"] == ["acknowledged"]


def test_the_serverless_cycle_raises_it_and_the_dashboard_copy_shows_it_until_acknowledged(box, monkeypatch):
    import base64

    from fastapi.testclient import TestClient

    from remitwatch import api, cloud, report
    from tests.test_cloud import DATA, PW, raw_conn
    root, client, state = box
    quiet(monkeypatch)
    sent: list[str] = []
    monkeypatch.setattr(report, "email_report", lambda text, subject="": sent.append(subject) or True)
    cyc = lambda: cloud.run_cloud_cycle(state, client, budget_s=600, email=True, log=lambda *a: None)     # noqa: E731
    write(root / DATA, base_claims(), "day1")
    assert "detect" in cyc()
    time.sleep(1.1)
    write(root / DATA, denials(10000, 1600), "flood")
    out = cyc()
    assert out["alerts"]["by_kind"].get("SWING") == 1 and "serving" in out
    assert any(s.startswith("RemitWatch alert: One analysis run added 1,600 open items") for s in sent)
    [a] = swings(raw_conn(state), "new")
    # the dashboard reads its copy of the store, as the serverless site does
    monkeypatch.setenv("REMITWATCH_MODE", "serverless")
    monkeypatch.delenv("REMITWATCH_ALLOW_OPEN", raising=False)
    monkeypatch.setenv("REMITWATCH_STATE_DIR", str(root / "RemitWatch-state"))
    monkeypatch.setenv("REMITWATCH_PASSWORD", PW)
    monkeypatch.setattr(api, "PASSWORD", PW)
    monkeypatch.setattr(api, "USERNAME", "oda")
    monkeypatch.setattr(cloud, "SERVING_COPY", cloud.ServingCopy())
    auth = {"Authorization": "Basic " + base64.b64encode(f"oda:{PW}".encode()).decode()}
    tc = TestClient(api.app)
    [sw] = [x for x in tc.get("/api/alerts?status=new", headers=auth).json()["items"] if x["kind"] == "SWING"]
    assert sw["alert_id"] == a["alert_id"] and sw["detail"]["lines"] == a["detail"]["lines"]
    assert tc.post(f"/api/alerts/{sw['alert_id']}/ack", headers=dict(auth, **{"X-Requested-With": "remitwatch"})).json() == {"ok": True}
    assert "SWING" not in [x["kind"] for x in tc.get("/api/alerts?status=new", headers=auth).json()["items"]]
    time.sleep(1.1)
    later = cyc()                         # folds the acknowledgement into the store and raises the alert again, acknowledged
    assert later["actions_folded"] == 1 and "detect" not in later
    assert [x["status"] for x in swings(raw_conn(state))] == ["acknowledged"]
    monkeypatch.setattr(cloud, "SERVING_COPY", cloud.ServingCopy())      # another instance, reading the copy the run rebuilt
    assert "SWING" not in [x["kind"] for x in tc.get("/api/alerts?status=new", headers=auth).json()["items"]]


# --------------------------------------------------------------------------- every page, in a browser, on both hosts

PAGE_PW = "correct-horse-battery-7"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _browser_ready() -> bool:
    import shutil
    import subprocess
    return bool(shutil.which("node")) and subprocess.run(["node", "-e", "require('playwright')"], capture_output=True).returncode == 0


def _site(env_extra: dict, ack: bool) -> dict:
    """Serve the app (uvicorn, as both hosts do) and look at its pages with Playwright (tests/swing_page.js)."""
    import socket
    import subprocess
    import urllib.request
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DROPBOX_", "TRIZETTO_", "SRFAX_", "ANTHROPIC_", "SMTP_"))
           and k not in ("REPORT_TO", "ALERT_WEBHOOK_URL", "REMITWATCH_MODE", "REMITWATCH_OWNER_PASSWORD", "REMITWATCH_SHOW_PHI",
                         "REMITWATCH_SYNC_INTERVAL_MINUTES", "REMITWATCH_ALLOW_OPEN")}
    env.update(REMITWATCH_PASSWORD=PAGE_PW, REMITWATCH_USER="oda", PYTHONDONTWRITEBYTECODE="1", **env_extra)
    srv = subprocess.Popen([sys.executable, "-B", "-m", "uvicorn", "remitwatch.api:app", "--host", "127.0.0.1", "--port", str(port),
                            "--log-level", "warning"], cwd=REPO, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for _ in range(150):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        got = subprocess.run(["node", os.path.join(REPO, "tests", "swing_page.js"), str(port), "oda", PAGE_PW, "1" if ack else "0"],
                             capture_output=True, text=True, timeout=240)
        assert got.returncode == 0, got.stderr[-2000:]
        return json.loads(got.stdout.strip().splitlines()[-1])
    finally:
        srv.terminate()
        srv.wait(timeout=10)


def _check_pages(seen: dict, title: str, action: str) -> None:
    assert seen["errors"] == [] and seen["failed"] == [], (seen["errors"], seen["failed"])
    assert len(seen["pages"]) == 18
    for where, p in seen["pages"].items():
        assert p["shown"] and p["above"], where                  # at the top, above the page's own content
        assert title in p["text"] and action in p["text"] and "Big change in one run" in p["text"], where
        assert p["buttons"] == 1 and p["bar_over"] <= 0, where                      # the banner fits the screen, phone width too
        # the dashboard's pages do not scroll sideways (the setup page does at phone width with no banner at all: its own matter)
        assert p["overflow"] <= 0 or where.endswith("/setup"), where
        for word in PLANTED:
            assert word not in p["text"], (where, word)
    assert seen["after_ack"]["hidden"] and seen["after_ack"]["asked"] and seen["after_reload"]["hidden"] and seen["upload_after"]["hidden"]


@pytest.mark.skipif(not _browser_ready(), reason="needs node and Playwright for node")
def test_every_page_of_the_server_site_shows_it_at_the_top_until_acknowledged(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    quiet(monkeypatch)
    now = datetime.now(timezone.utc)
    t1 = (now - timedelta(days=1)).replace(microsecond=0).isoformat()
    t2 = (now - timedelta(hours=1)).replace(microsecond=0).isoformat()
    db = str(tmp_path / "server.sqlite")
    store = Store(db)
    read(store, write(tmp_path / "in", planted(base_claims()), "day1"), (now - timedelta(days=1, hours=1)).replace(microsecond=0).isoformat())
    detect_at(store, "r1", t1)
    read(store, write(tmp_path / "in", planted(denials(10000, 1600)), "PLANTED-FILE"), (now - timedelta(hours=2)).replace(microsecond=0).isoformat())
    detect_at(store, "r2", t2)
    run_alerts(store.conn)
    [a] = swings(store.conn, "new")
    store.close()
    seen = _site({"REMITWATCH_DB": db}, ack=True)
    _check_pages(seen, a["title"], a["detail"]["action"])
    store = Store(db)
    assert [x["status"] for x in swings(store.conn)] == ["acknowledged"]
    store.close()


@pytest.mark.skipif(not _browser_ready(), reason="needs node and Playwright for node")
def test_every_page_of_the_serverless_site_shows_it_from_the_dashboard_copy_until_acknowledged(box, monkeypatch):
    from remitwatch import cloud
    from tests.test_cloud import DATA, raw_conn
    root, client, state = box
    quiet(monkeypatch)
    write(root / DATA, planted(base_claims()), "day1")
    cloud.run_cloud_cycle(state, client, budget_s=600, email=False, log=lambda *a: None)
    time.sleep(1.1)
    write(root / DATA, planted(denials(10000, 1600)), "PLANTED-FILE")
    assert cloud.run_cloud_cycle(state, client, budget_s=600, email=False, log=lambda *a: None)["alerts"]["by_kind"].get("SWING") == 1
    [a] = swings(raw_conn(state), "new")
    seen = _site({"REMITWATCH_MODE": "serverless", "REMITWATCH_STATE_DIR": str(root / "RemitWatch-state"),
                  "REMITWATCH_TMP": str(root.parent / "site-scratch")}, ack=True)
    _check_pages(seen, a["title"], a["detail"]["action"])
    assert [n["name"] for n in state.list(cloud.ACTIONS)]           # the acknowledgement is in the journal for the next run
