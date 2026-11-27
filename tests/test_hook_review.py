"""
The hook unit's fix round (2026-11-26): what the two independent reviews of the hook-in unit and the owner's decision 1
found (hook-r1-1 .. 7, hook-r2-1 .. 12), each with the test that failed before its fix. Ported from the reviewers' own tests,
invented data only (tests/test_cloud.py's fake Dropbox and serverless site, tests/test_hooks.py's server store and feed,
tests/test_archive.py's visits). The settings named in the runbooks (hook-r2-4) are checked in tests/test_docs.py.
"""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch.selfcheck import audit_due as REAL_AUDIT_DUE  # noqa: E402  (before the box fixture turns it off)
from remitwatch.store import Store  # noqa: E402
from tests.test_cloud import DATA, PW, box, cycle, raw_conn, site, visits_with_downcodes, write_remits  # noqa: E402,F401
from tests.test_hooks import XHR, _browser_ready, _clean_env, _cloud, _db, _feed, _press, _server_client  # noqa: E402,F401

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OWNER_PW = "owner-only-figures-42-long"


# ======================================================================================================================
# helpers
# ======================================================================================================================
def _chain(state, client, *, budget_s: int = 600, first: str = "press", run=None) -> list[dict]:
    """One press on Vercel and every run /api/cron/cycle starts after it, by the route's own rule (cloud.next_run)."""
    from remitwatch import cloud
    run = run or (lambda **kw: cloud.run_cloud_cycle(state, client, email=False, log=lambda *a: None, **kw))
    outs = [run(trigger=first, force_detect=True, budget_s=budget_s)]
    hop = 0
    while True:
        nxt = cloud.next_run(outs[-1], hop)
        if not nxt.get("trigger"):
            return outs
        hop += 1
        outs.append(run(trigger=nxt["trigger"], budget_s=budget_s))


def _shape(runs: list[dict]) -> list[tuple]:
    return [(r.get("trigger"), "detect" in r, r.get("deferred"), (r.get("archive") or {}).get("bills"), "activity" in r,
             "self_audit" in r, r.get("tidy")) for r in runs]


def _no_analysis_without_a_press(runs: list[dict]) -> None:
    """The owner's decision 1 holds through the chain: a run that analyses is a press, or the chain's run the press handed
    its analysis to; the chain's last run (tidy) analyses nothing when no new file arrived."""
    for i, r in enumerate(runs):
        if "detect" in r:
            handed = i > 0 and runs[i - 1].get("deferred") == "detection" and r.get("trigger") == "continue"
            assert r.get("trigger") == "press" or handed, _shape(runs)
        if r.get("trigger") == "tidy":
            assert "detect" not in r, _shape(runs)


def _old_and_new(box):
    """Eight settled bills of 2022 (past the archive's horizon) and eight of 2026 (tests/test_archive.py's visits)."""
    from tests.test_archive import NEW, OLD, visits
    from tests.test_remitwatch import remit
    root, client, state = box
    folder = root / DATA
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "old.RMT").write_bytes(remit("".join(visits(1, 8, OLD)), "100.00", seg_count=99, isa_ctrl="000000001", pay_date="20220415"))
    (folder / "new.RMT").write_bytes(remit("".join(visits(100, 8, NEW)), "100.00", seg_count=99, isa_ctrl="000000002", pay_date="20260415"))
    return root, client, state


def _archived(state) -> int:
    conn = raw_conn(state)
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='archive_bills'").fetchone():
            return 0
        return conn.execute("SELECT COUNT(*) FROM archive_bills").fetchone()[0]
    finally:
        conn.close()


def _slow_analysis(state, seconds: int = 170) -> None:
    """The site's last analysis took `seconds` (Vercel's: about 170 s of a 240 s run), so a press hands it to a fresh run."""
    from remitwatch import cloud
    store, _ = cloud.load_raw(state)
    store.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('cloud:detect_seconds', ?)", (str(seconds),))
    store.conn.commit()
    cloud.save_raw(state, store, lambda *a: None)
    store.close()


def _serve(env_extra: dict, script: str, *args: str, timeout: int = 240) -> dict:
    """uvicorn on a free port with these settings, then `node tests/<script> <port> oda <pw> *args`; the script's JSON."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DROPBOX_", "TRIZETTO_", "SRFAX_", "ANTHROPIC_", "SMTP_", "MODMED_", "VERCEL"))
           and k not in ("REPORT_TO", "ALERT_WEBHOOK_URL", "REMITWATCH_MODE", "REMITWATCH_OWNER_PASSWORD", "REMITWATCH_SHOW_PHI",
                         "REMITWATCH_SYNC_INTERVAL_MINUTES", "REMITWATCH_ALLOW_OPEN", "REMITWATCH_STATE_DIR", "CRON_SECRET")}
    env.update(REMITWATCH_PASSWORD=PW, REMITWATCH_USER="oda", PYTHONDONTWRITEBYTECODE="1")
    env.update(env_extra)
    srv = subprocess.Popen([sys.executable, "-B", "-m", "uvicorn", "remitwatch.api:app", "--host", "127.0.0.1", "--port", str(port),
                            "--log-level", "warning"], cwd=REPO, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for _ in range(200):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        got = subprocess.run(["node", os.path.join(REPO, "tests", script), str(port), "oda", PW, *args],
                             capture_output=True, text=True, timeout=timeout)
    finally:
        srv.terminate()
        try:
            srv.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            srv.kill()
    assert got.returncode == 0, got.stderr[-3000:]
    return json.loads(got.stdout.strip().splitlines()[-1])


# ======================================================================================================================
# hook-r1-1: since decision 1 every Vercel run is a press or a run of its chain, and a press analyses. The work only a run
# that does not analyse did (the archive, the provider tables an analysis had no time for, the weekly self-audit) now
# takes the chain's last run (tidy), and no run analyses without a press
# ======================================================================================================================
def test_presses_on_vercel_run_the_archive_in_the_chains_last_run(box):
    """The world of test_hooks' archive test, where an idle run archives 8 settled bills of 2022: on Vercel nothing starts
    an idle run any more, so a press's chain ends with one that does it (it analyses nothing)."""
    _old_and_new(box)
    root, client, state = box
    runs = []
    for _ in range(5):
        runs += _chain(state, client)
    assert _archived(state) == 8, _shape(runs)
    tidy = [i for i, r in enumerate(runs) if r.get("trigger") == "tidy"]
    assert tidy and (runs[tidy[0]].get("archive") or {}).get("bills") == 8, _shape(runs)
    assert "archive" in runs[tidy[0] - 1]["tidy"] and "detect" in runs[tidy[0] - 1], _shape(runs)     # asked by the analysis
    # once a day: the next presses' chains end with their analysis (nothing more due)
    assert len(tidy) == 1 and runs[-1].get("trigger") == "press" and not runs[-1].get("tidy"), _shape(runs)
    _no_analysis_without_a_press(runs)


def test_presses_that_hand_the_analysis_on_run_the_archive_too(box):
    """As on the live site (the analysis takes about 170 s of the 240 s a run has): each press hands the analysis to the
    chain's next run, which analyses; the archive still takes its bills, in a run that analyses nothing."""
    _old_and_new(box)
    root, client, state = box
    runs = list(_chain(state, client, budget_s=240))              # the first load: the store is new
    for _ in range(5):
        _slow_analysis(state)
        runs += _chain(state, client, budget_s=240)
    assert any(r.get("deferred") == "detection" and r.get("trigger") == "press" for r in runs), _shape(runs)
    assert _archived(state) == 8, _shape(runs)
    _no_analysis_without_a_press(runs)


class _SlowClock:
    """cloud.py's clock, with each analysis taking `seconds` (Vercel's real image: about 170 to 240 s of a 240 s run)."""

    def __init__(self, seconds: float):
        self._t, self.offset, self.seconds = time, 0.0, seconds

    def time(self):
        return self._t.time() + self.offset

    def __getattr__(self, name):
        return getattr(self._t, name)


def test_presses_rebuild_the_provider_tables_when_the_analysis_fills_the_run(box, monkeypatch):
    """Each analysis leaves the provider tables behind (activity.mark_pending) and rebuilds them when it has 45 s left; on
    Vercel's real image it does not, so the chain's last run rebuilds them, on the fast and the slow analysis alike: a
    press that analyses itself, and one that hands its analysis on. Two slow chains: each slow analysis (200 s of 240) is
    pressure the self-regulation counts (selfcheck.plan_next), and a third in a row would leave the provider tables for a
    calm run by design (level 'shed'; the test below). The three chains this test ran before passed only while two runs
    started within one second were taken for one (so the analysing run's pressure was missed): on a fast machine; on a
    loaded one each run had its own second and the third chain's last run shed them."""
    from remitwatch import activity, cloud, detect
    root, client, state = box
    write_remits(root / DATA, visits_with_downcodes())
    first = _chain(state, client)
    assert any("activity" in r for r in first), _shape(first)            # a fresh site: built once, with time to spare
    clock = _SlowClock(200)
    monkeypatch.setattr(cloud, "time", clock)
    real = detect.Detector.run

    def slow(self):
        out = real(self)
        clock.offset += clock.seconds
        return out
    monkeypatch.setattr(detect.Detector, "run", slow)
    chains = [_chain(state, client, budget_s=240) for _ in range(2)]
    runs = chains[0] + chains[1]
    conn = raw_conn(state)
    try:
        stale = activity.stale(conn)
    finally:
        conn.close()
    assert [r["trigger"] for r in chains[0]] == ["press", "tidy"], _shape(runs)                 # the press analysed itself
    assert [r["trigger"] for r in chains[1]] == ["press", "continue", "tidy"], _shape(runs)     # it handed the analysis on
    for chain in chains:
        assert "activity" not in chain[-2] and "activity" in chain[-1], _shape(runs)    # the analysis had no time; tidy did
    assert not any("activity" in ((r.get("plan") or {}).get("skip") or []) for r in runs), _shape(runs)
    assert not stale, (_shape(runs), stale)
    _no_analysis_without_a_press(runs)


def test_three_slow_analyses_in_a_row_leave_the_provider_tables_for_a_calm_run(box, monkeypatch):
    """The third pressured analysis in a row steps the self-regulation to 'shed': the chain's last run leaves the provider
    tables (an optional step) for a calm run and says so in its plan, whatever second each run started in; the tables are
    stale until then (selfcheck.plan_for: never one optional step for more than MAX_DEFER_DAYS)."""
    from remitwatch import activity, cloud, detect
    from remitwatch import selfcheck as sc
    root, client, state = box
    write_remits(root / DATA, visits_with_downcodes())
    _chain(state, client)
    clock = _SlowClock(200)
    monkeypatch.setattr(cloud, "time", clock)
    real = detect.Detector.run

    def slow(self):
        out = real(self)
        clock.offset += clock.seconds
        return out
    monkeypatch.setattr(detect.Detector, "run", slow)
    runs = []
    for _ in range(3):
        runs += _chain(state, client, budget_s=240)
    conn = raw_conn(state)
    try:
        stale = activity.stale(conn)
    finally:
        conn.close()
    last = runs[-1]
    plan = last.get("plan") or {}
    assert last["trigger"] == "tidy" and plan.get("level", 0) >= 3 and "activity" in plan.get("skip", []), (_shape(runs), plan)
    assert "activity" not in last and stale, (_shape(runs), stale)
    assert sc.LEVELS[plan["level"]] in ("shed", "alert")
    _no_analysis_without_a_press(runs)


def test_presses_on_vercel_run_the_weekly_self_audit(box, monkeypatch):
    """The weekly self-audit takes a run that does not analyse: the chain's last run, once a week, never an analysis."""
    from remitwatch import selfcheck
    monkeypatch.setattr(selfcheck, "audit_due", REAL_AUDIT_DUE)      # the box fixture turns the audit off; here it is the subject
    root, client, state = box
    write_remits(root / DATA, visits_with_downcodes())
    runs = []
    for _ in range(5):
        runs += _chain(state, client)
    conn = raw_conn(state)
    try:
        audits = conn.execute("SELECT COUNT(*) FROM selfcheck_audits").fetchone()[0]
    finally:
        conn.close()
    audited = [r for r in runs if "self_audit" in r]
    assert audits == 1 and len(audited) == 1 and audited[0]["trigger"] == "tidy", _shape(runs)
    _no_analysis_without_a_press(runs)


def test_the_chains_rule_ends_after_the_tidy_run_and_never_repeats_it():
    """cloud.next_run: the chain goes on while a run has more to do and got somewhere, retries a file that timed out, ends
    with one tidy run after an analysis, and stops at MAX_HOPS (saying so only when work was left)."""
    from remitwatch import cloud
    assert cloud.next_run({"more": True, "progress": True}, 0) == {"trigger": "continue", "delay": 0}
    assert cloud.next_run({"more": True, "progress": False}, 0) == {"end": True}
    assert cloud.next_run({"tidy": ["archive"], "detect": {}}, 1) == {"trigger": "tidy", "delay": 0}
    assert cloud.next_run({"ingest": {"download_errors": 1}}, 0) == {"trigger": "retry", "delay": 90}
    assert cloud.next_run({"ingest": {"download_errors": 1}}, 2) == {"end": True}
    assert cloud.next_run({}, 3) == {}                               # nothing left: the caller looks for late files
    assert cloud.next_run({"more": True, "progress": True}, cloud.MAX_HOPS)["halted"]
    assert cloud.next_run({"tidy": ["audit"]}, cloud.MAX_HOPS) == {"end": True}


# ======================================================================================================================
# hook-r1-2: a pressed run the platform killed writes no status; a chain halted after 30 runs. Both are said in the header
# and the analysis panel, with what stands and the press this site has
# ======================================================================================================================
def _killed_run(state, monkeypatch, trigger: str = "press") -> str:
    """A run that took the lock, (for a press) answered it, logged its steps, then was killed in detection: no finish, no
    release, no status. Time then passes beyond the lock's life. The press's lock is an older build's (it names no run);
    the chain's run takes it as this build does, naming itself."""
    from remitwatch import cloud, runlog
    rid = None if trigger == "press" else runlog.new_run_id()
    token = cloud.acquire(state, run_id=rid)
    assert token
    rl = runlog.start_run("cycle", trigger=trigger, state=state, host="serverless", run_id=rid)
    if trigger == "press":
        cloud._answer_press(state, rl.run_id)
    with rl.step("load"):
        pass
    rl.step("detect").__enter__()          # in detection when it is killed (the step never ends)
    monkeypatch.setattr(cloud, "lock_stale_seconds", lambda: 0)
    monkeypatch.setattr(runlog, "SERVERLESS_STALE_SECONDS", 0)
    cloud.SERVING_COPY._status = (0.0, None)
    return rl.run_id


@pytest.mark.parametrize("trigger", ["press", "continue"])
def test_a_run_the_platform_killed_is_said_in_the_header_and_the_analysis_panel(site, monkeypatch, trigger):
    """A press, or the run of its chain that took the analysis on: killed in detection. Once its lock is stale the header
    says the last run stopped and the panel says the platform stopped it, what stands (open items and dollars) and to
    press Run the analysis now."""
    tc, auth, _api, state = site
    before = tc.get("/api/sync/status", headers=auth).json()
    assert before["analysed"]["at"] and not before["last_failed"]
    _killed_run(state, monkeypatch, trigger)
    st = tc.get("/api/sync/status", headers=auth).json()
    an = tc.get("/api/ops/panels", headers=auth).json()["analysis"]
    f = an.get("failed") or {}
    assert st.get("last_failed") is True and st.get("stalled_since"), st
    assert f.get("killed") and "the platform stopped it" in f["text"] and "open item" in f["text"] and "$" in f["text"], an
    assert "Run the analysis now" in f["text"] and an["running"] is None and "—" not in f["text"], f


def test_a_lock_left_by_a_run_that_finished_is_not_said_as_a_stopped_run(site, monkeypatch):
    """A run that finished and wrote its status, whose lock could not be removed: the pages say nothing stopped."""
    from remitwatch import cloud
    tc, auth, _api, state = site
    real_delete = state.delete

    def no_delete(name):
        if name == cloud.LOCK:
            raise OSError("the state folder did not answer")
        return real_delete(name)
    monkeypatch.setattr(state, "delete", no_delete)
    done = cycle(state, None, trigger="press", force_detect=True)
    monkeypatch.setattr(state, "delete", real_delete)
    assert done.get("finished") and not done.get("error") and state.meta(cloud.LOCK) is not None
    monkeypatch.setattr(cloud, "lock_stale_seconds", lambda: 0)
    cloud.SERVING_COPY._status = (0.0, None)
    st = tc.get("/api/sync/status", headers=auth).json()
    an = tc.get("/api/ops/panels", headers=auth).json()["analysis"]
    assert not st.get("last_failed") and not st.get("stalled_since") and not an.get("failed"), (st.get("stalled_since"), an.get("failed"))


def test_the_feed_panel_on_vercel_names_the_press_for_a_stopped_run_and_a_halted_chain():
    """features.js's Feed panel (Vercel branch): no run comes without a press there, and its button is the Data tab's
    Run the analysis now (the Feed has none on Vercel)."""
    src = open(os.path.join(REPO, "remitwatch", "static", "features.js"), encoding="utf-8").read()
    stalled = next(ln for ln in src.splitlines() if "stalled_since?" in ln)
    halted = next(ln for ln in src.splitlines() if "lc.halted?" in ln)
    assert "The next run picks up where it left off" not in stalled and "Run the analysis now" in stalled, stalled.strip()[:240]
    assert "Sync and analyse now" not in halted and "Run the analysis now" in halted, halted.strip()[:240]


def test_a_chain_halted_after_max_hops_is_said_on_the_analysis_panel_and_the_header(site):
    import remitwatch.cloud as cloud
    tc, auth, _api, state = site
    st = json.loads(state.get(cloud.STATUS))
    st.update(halted="30 runs in a row without finishing", more=True)
    state.put(cloud.STATUS, json.dumps(st).encode())
    cloud.SERVING_COPY._status = (0.0, None)
    an = tc.get("/api/ops/panels", headers=auth).json()["analysis"]
    f = an.get("failed") or {}
    assert f.get("halted") and "30 runs in a row" in f["text"] and "Run the analysis now" in f["text"] and "open item" in f["text"], an
    assert tc.get("/api/sync/status", headers=auth).json()["last_failed"] is True
    ops = open(os.path.join(REPO, "remitwatch", "static", "ops.js"), encoding="utf-8").read()
    assert "a.failed.halted" in ops                    # the panel heads it as a chain that stopped


# ======================================================================================================================
# hook-r1-3: one killed run is counted once by the self-regulation, and a run that finished is never counted as killed
# ======================================================================================================================
def test_one_killed_press_steps_the_regulation_up_once(box):
    """A press took the lock 500 s ago and was killed in its analysis 300 s ago (its last step). The next press takes the
    lock over (the marker is not old enough to be found yet): the kill is counted once, by the run's own marker, and a
    press ten minutes later does not count it again."""
    from remitwatch import cloud, runlog
    root, client, state = box
    write_remits(root / DATA, visits_with_downcodes())
    assert _cloud(state, client)["plan"]["level"] == 0
    now = datetime.now(timezone.utc)
    # an older build's lock: it names no run (the run is matched by when it started)
    state.put(cloud.LOCK, json.dumps({"token": "deadbeefdeadbeef", "started": (now - timedelta(seconds=500)).isoformat()}).encode())
    t_lock = time.time() - 500
    os.utime(root / "RemitWatch-state" / cloud.LOCK, (t_lock, t_lock))
    rid = (now - timedelta(seconds=500)).strftime("run-%Y%m%dT%H%M%SZ") + "-dead"
    sink = runlog.sink_for(state=state)
    sink.write_json(f"open/{rid}.json", {"run_id": rid, "kind": "cycle", "host_mode": "serverless", "trigger": "press",
                                         "started_at": runlog._iso(now - timedelta(seconds=500)),
                                         "last_at": runlog._iso(now - timedelta(seconds=300)), "last_step": "detect",
                                         "steps_done": ["load", "intake", "dropbox"], "peak_mb": 2100})
    pressed = _cloud(state, client, trigger="press", force_detect=True)
    assert pressed["plan"]["level"] == 1, pressed["plan"]
    assert [r["outcome"] for r in runlog.read_index(sink) if r["run_id"] == rid] == ["killed"]     # recorded, its marker gone
    runlog.set_clock(lambda: datetime.now(timezone.utc) + timedelta(minutes=10))
    try:
        again = _cloud(state, client, trigger="press", force_detect=True)
    finally:
        runlog.set_clock(None)
    why = [c.get("why") for c in (json.loads(state.get(cloud.STATUS)).get("regulate") or {}).get("changes") or []]
    assert again["plan"]["level"] == 1, (again["plan"], why)


def test_a_lock_that_names_its_run_counts_that_run_once(box):
    """This build's lock names the run that took it: a takeover counts that run by its id, and its marker found later
    (when it is old enough) is the same run, not a second kill."""
    from remitwatch import cloud, runlog
    root, client, state = box
    write_remits(root / DATA, visits_with_downcodes())
    assert _cloud(state, client)["plan"]["level"] == 0
    now = datetime.now(timezone.utc)
    rid = (now - timedelta(seconds=500)).strftime("run-%Y%m%dT%H%M%SZ") + "-beef"
    state.put(cloud.LOCK, json.dumps({"token": "feedfacefeedface", "started": (now - timedelta(seconds=500)).isoformat(),
                                      "run_id": rid}).encode())
    t_lock = time.time() - 500
    os.utime(root / "RemitWatch-state" / cloud.LOCK, (t_lock, t_lock))
    sink = runlog.sink_for(state=state)
    sink.write_json(f"open/{rid}.json", {"run_id": rid, "kind": "cycle", "host_mode": "serverless", "trigger": "continue",
                                         "started_at": runlog._iso(now - timedelta(seconds=500)),
                                         "last_at": runlog._iso(now - timedelta(seconds=300)), "last_step": "save_store", "peak_mb": 1500})
    assert _cloud(state, client, trigger="press", force_detect=True)["plan"]["level"] == 1
    runlog.set_clock(lambda: datetime.now(timezone.utc) + timedelta(minutes=10))
    try:
        assert _cloud(state, client, trigger="press", force_detect=True)["plan"]["level"] == 1
    finally:
        runlog.set_clock(None)


def test_a_lock_left_by_a_run_that_finished_is_not_counted_as_a_kill(box, monkeypatch):
    """A run that finished and wrote its status, but whose lock could not be removed: the next press takes the lock over
    and the regulation counts nothing (the run's status names it)."""
    from remitwatch import cloud
    root, client, state = box
    write_remits(root / DATA, visits_with_downcodes())
    assert _cloud(state, client)["plan"]["level"] == 0
    real_delete = state.delete

    def no_delete(name):
        if name == cloud.LOCK:
            raise OSError("the state folder did not answer")
        return real_delete(name)
    monkeypatch.setattr(state, "delete", no_delete)
    done = _cloud(state, client, trigger="press", force_detect=True)
    monkeypatch.setattr(state, "delete", real_delete)
    assert done.get("finished") and not done.get("error") and state.meta(cloud.LOCK) is not None
    st = json.loads(state.get(cloud.STATUS))
    assert st.get("finished") and not st.get("error") and st.get("run_id") == json.loads(state.get(cloud.LOCK))["run_id"]
    t_old = time.time() - 500
    os.utime(root / "RemitWatch-state" / cloud.LOCK, (t_old, t_old))
    nxt = _cloud(state, client, trigger="press", force_detect=True)
    why = [c.get("why") for c in (json.loads(state.get(cloud.STATUS)).get("regulate") or {}).get("changes") or []]
    assert nxt["plan"]["level"] == 0, f"level {nxt['plan']['level']} after a run that finished; regulation changes: {why}"


def _finished_with_its_lock_left(state, monkeypatch, run):
    """A run that finished and wrote its status but could not remove its lock (the state folder did not answer the
    delete). Returns the run's status."""
    from remitwatch import cloud
    real_delete = state.delete

    def no_delete(name):
        if name == cloud.LOCK:
            raise OSError("the state folder did not answer")
        return real_delete(name)
    monkeypatch.setattr(state, "delete", no_delete)
    done = run()
    monkeypatch.setattr(state, "delete", real_delete)
    assert done.get("finished") and not done.get("error") and state.meta(cloud.LOCK) is not None
    return json.loads(state.get(cloud.STATUS))


def _as_an_older_builds_lock(state, status):
    """The lock rewritten as an older build wrote it: no run named, taken when its run started (40 s before it finished:
    a run takes seconds at least, and the times are kept to the second)."""
    from remitwatch import cloud
    lock = json.loads(state.get(cloud.LOCK))
    lock.pop("run_id", None)
    lock["started"] = (datetime.fromisoformat(status["finished"]) - timedelta(seconds=40)).isoformat()
    state.put(cloud.LOCK, json.dumps(lock).encode())


def test_an_older_builds_lock_left_by_a_run_that_finished_is_not_said_as_a_stopped_run(site, monkeypatch):
    """The same with the lock an older build left (it names no run, as the first run after this build is deployed may
    find): the run's status, written after the lock was taken, says it finished."""
    from remitwatch import cloud
    tc, auth, _api, state = site
    status = _finished_with_its_lock_left(state, monkeypatch, lambda: cycle(state, None, trigger="press", force_detect=True))
    _as_an_older_builds_lock(state, status)
    monkeypatch.setattr(cloud, "lock_stale_seconds", lambda: 0)
    cloud.SERVING_COPY._status = (0.0, None)
    st = tc.get("/api/sync/status", headers=auth).json()
    an = tc.get("/api/ops/panels", headers=auth).json()["analysis"]
    assert not st.get("last_failed") and not st.get("stalled_since") and not an.get("failed"), (st.get("stalled_since"), an.get("failed"))


def test_an_older_builds_lock_left_by_a_run_that_finished_is_not_counted_as_a_kill(box, monkeypatch):
    """The next press takes over that older build's lock: no open marker of the run is left (it finished), and its status
    is newer than the lock, so the regulation counts nothing."""
    from remitwatch import cloud
    root, client, state = box
    write_remits(root / DATA, visits_with_downcodes())
    assert _cloud(state, client)["plan"]["level"] == 0
    status = _finished_with_its_lock_left(state, monkeypatch, lambda: _cloud(state, client, trigger="press", force_detect=True))
    _as_an_older_builds_lock(state, status)
    t_old = time.time() - 500
    os.utime(root / "RemitWatch-state" / cloud.LOCK, (t_old, t_old))
    nxt = _cloud(state, client, trigger="press", force_detect=True)
    why = [c.get("why") for c in (json.loads(state.get(cloud.STATUS)).get("regulate") or {}).get("changes") or []]
    assert nxt["plan"]["level"] == 0, f"level {nxt['plan']['level']} after a run that finished; regulation changes: {why}"


def test_a_kill_found_by_its_marker_and_by_its_lock_in_one_run_is_counted_once(box):
    """A run killed long enough ago that the next press finds its open marker at its start (runlog) and takes over its
    lock, which names it: one run, counted once (the takeover leaves a run its markers found to them, and the regulation
    counts each run once by its id)."""
    from remitwatch import cloud, runlog
    root, client, state = box
    write_remits(root / DATA, visits_with_downcodes())
    assert _cloud(state, client)["plan"]["level"] == 0
    now = datetime.now(timezone.utc)
    rid = (now - timedelta(seconds=900)).strftime("run-%Y%m%dT%H%M%SZ") + "-cafe"
    state.put(cloud.LOCK, json.dumps({"token": "cafecafecafecafe", "started": (now - timedelta(seconds=900)).isoformat(),
                                      "run_id": rid}).encode())
    t_lock = time.time() - 900
    os.utime(root / "RemitWatch-state" / cloud.LOCK, (t_lock, t_lock))
    sink = runlog.sink_for(state=state)
    sink.write_json(f"open/{rid}.json", {"run_id": rid, "kind": "cycle", "host_mode": "serverless", "trigger": "press",
                                         "started_at": runlog._iso(now - timedelta(seconds=900)),
                                         "last_at": runlog._iso(now - timedelta(seconds=800)), "last_step": "detect",
                                         "steps_done": ["load", "intake", "dropbox"], "peak_mb": 1900})
    pressed = _cloud(state, client, trigger="press", force_detect=True)
    assert [r["outcome"] for r in runlog.read_index(sink) if r["run_id"] == rid] == ["killed"]
    why = [c.get("why") for c in (json.loads(state.get(cloud.STATUS)).get("regulate") or {}).get("changes") or []]
    assert pressed["plan"]["level"] == 1, (pressed["plan"], why)


# ======================================================================================================================
# hook-r1-4: NEAR_CEILING stays up across a run that saved no store; a save measured smaller clears it
# ======================================================================================================================
@pytest.mark.parametrize("kind", ["file", "compact"])
def test_near_ceiling_stays_raised_across_a_run_that_stopped_and_a_smaller_save_clears_it(tmp_path, kind):
    from remitwatch import alerts
    from remitwatch import selfcheck as sc
    from remitwatch.detect import ensure_schema
    if kind == "file":
        s = Store(str(tmp_path / "s.sqlite"))
    else:
        blank = Store(":memory:", compact=True)
        s = Store.from_bytes(bytearray(blank.to_bytes()))
        blank.close()
    ensure_schema(s.conn)
    for i in range(3):
        s.conn.execute("INSERT INTO findings (finding_id, signal, category, status, work_state, shortfall_cents, stale) "
                       "VALUES (?, 'DENIED_AUTH', 'denial', 'open', 'work_now', 125000, 0)", (f"F-{i}",))
    s.conn.commit()

    def active():
        return s.conn.execute("SELECT alert_id, first_seen FROM alerts WHERE kind='NEAR_CEILING' AND status!='cleared'").fetchone()
    saved = {"started": "2026-11-24T10:17:00+00:00", "finished": "2026-11-24T10:20:00+00:00", "mode": "serverless",
             "seconds": 180, "budget_seconds": 240, "memory_mb": {"peak": 1500}, "store": {"image_mb": 900.0}}
    sc.plan_next(s.conn, saved, mode="serverless")
    alerts.run_alerts(s.conn, today="2026-11-24")
    first = active()
    assert first, "NEAR_CEILING raised after a saving run at 88% of the ceiling"
    stopped = {"started": "2026-11-24T11:17:00+00:00", "finished": "2026-11-24T11:21:00+00:00", "mode": "serverless",
               "seconds": 240, "budget_seconds": 240, "memory_mb": {"peak": 1900},
               "error": "OutOfTime: Detection was still running at the time limit and was stopped; nothing was saved"}
    sc.plan_next(s.conn, stopped, mode="serverless")
    alerts.run_alerts(s.conn, today="2026-11-24")
    health = [i for i in sc.data_health(s.conn) if i["key"] == "selfcheck_ceiling"]
    assert active() == first and health and "900 MB" in health[0]["detail"], (active(), health)    # the same alert, not a new one
    smaller = dict(saved, started="2026-11-24T12:17:00+00:00", finished="2026-11-24T12:20:00+00:00", store={"image_mb": 500.0})
    sc.plan_next(s.conn, smaller, mode="serverless")
    alerts.run_alerts(s.conn, today="2026-11-24")
    assert active() is None and not [i for i in sc.data_health(s.conn) if i["key"] == "selfcheck_ceiling"]
    s.close()


# ======================================================================================================================
# hook-r1-5: a run's memory figures are its own on a warm instance
# ======================================================================================================================
def test_a_runs_memory_figures_are_its_own_on_a_warm_instance(box, monkeypatch):
    """The same process held and freed 1,100 MB before a run that reads nothing (Vercel reuses a warm instance): that run
    stays calm, and the next run's plan does not step up."""
    monkeypatch.setenv("REMITWATCH_MEMORY_BUDGET_MB", "1200")     # the 80% mark: 960 MB
    root, client, state = box
    write_remits(root / DATA, visits_with_downcodes())
    first = _cloud(state, client)
    base = first["memory_mb"]["peak"]
    if base > 700:
        pytest.skip(f"this test process already holds {base} MB")
    assert first["plan"]["level"] == 0
    blob = bytearray(1100 << 20)
    blob[::4096] = b"\x01" * len(blob[::4096])
    del blob
    light = _cloud(state, client)
    nxt = _cloud(state, client)
    assert light["memory_mb"]["peak"] < 960 and nxt["plan"]["level"] == 0, (light["memory_mb"], nxt["plan"])


def test_where_the_mark_cannot_be_reset_a_peak_from_before_the_run_is_not_its_own(monkeypatch):
    import builtins

    from remitwatch import runtime
    vals = {"VmHWM": 1500, "VmRSS": 300}
    monkeypatch.setattr(runtime, "_PEAK", {"reset": False, "base": None, "seen": 0})
    monkeypatch.setattr(runtime, "_status_mb", lambda f: vals[f])
    monkeypatch.setattr(runtime, "_hwm_mb", lambda: vals["VmHWM"])
    real_open = builtins.open

    def no_clear(path, *a, **k):
        if str(path) == "/proc/self/clear_refs":
            raise PermissionError("not on this host")
        return real_open(path, *a, **k)
    monkeypatch.setattr(builtins, "open", no_clear)
    assert runtime.reset_peak() is False
    assert runtime.peak_mb() == 300                  # the 1,500 MB mark was an earlier run's
    vals["VmRSS"] = 900
    assert runtime.peak_mb() == 900
    vals["VmRSS"] = 400
    assert runtime.peak_mb() == 900                  # the most this run was seen to hold
    vals["VmHWM"] = 1800                             # this run raised the mark: that is its peak
    assert runtime.peak_mb() == 1800


# ======================================================================================================================
# hook-r2-1 and hook-r2-5: the owner's Accept passes the owner's password gate and says what it took
# ======================================================================================================================
RUN = "run-20261126T101500Z-abcd"


def _store_with_a_failing_check(tmp_path, check: str = "vanished", severity: str | None = None, offenders: bool = True) -> str:
    """A server store after one cycle whose last checks show `check` failing (with one offender, for a critical check)."""
    from remitwatch import pipeline
    from remitwatch import selfcheck as sc
    db = _db(tmp_path)
    pipeline.run_cycle(db, sync=False, local=_feed(tmp_path), email=False, report=False, log=lambda *a: None)
    s = Store(db)
    c = s.conn
    fid = c.execute("SELECT finding_id FROM findings LIMIT 1").fetchone()[0]
    last = json.loads(c.execute("SELECT value FROM meta WHERE key='selfcheck:last'").fetchone()[0])
    last["run_id"] = RUN
    if check not in [ch["id"] for ch in last["checks"]]:
        last["checks"].append({k: v for k, v in sc._result(check, [("k1", 1753900, None)]).items() if k != "keys"})
    for ch in last["checks"]:
        if ch["id"] == check:
            ch.update(status="fail", n=1, cents=12300, **({"severity": severity} if severity else {}))
        elif ch["status"] == "fail":
            ch.update(status="ok", n=0, cents=0)
    c.execute("UPDATE meta SET value=? WHERE key='selfcheck:last'", (json.dumps(last),))
    c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('selfcheck:offenders_run', ?)", (json.dumps(RUN),))
    c.execute("DELETE FROM selfcheck_offenders")
    if offenders:
        c.execute("INSERT INTO selfcheck_offenders (check_id, key, cents, run_id) VALUES (?, ?, 12300, ?)", (check, fid, RUN))
    c.commit()
    s.close()
    return db


def _baseline(db: str) -> int:
    s = Store(db)
    try:
        return s.conn.execute("SELECT COUNT(*) FROM selfcheck_baseline").fetchone()[0]
    finally:
        s.close()


def test_the_owners_accept_needs_the_owners_password_and_takes_it_on_a_server(tmp_path, monkeypatch):
    import remitwatch.api as api
    db = _store_with_a_failing_check(tmp_path)
    tc, auth = _server_client(monkeypatch, db)
    monkeypatch.setattr(api, "OWNER_PASSWORD", OWNER_PW)
    body = {"checks": ["vanished"], "run_id": RUN}
    refused = tc.post("/api/ops/selfcheck/accept", json=body, headers=dict(auth, **XHR))
    assert refused.status_code == 403 and "owner's decision" in refused.json()["detail"] and "figures" not in refused.json()["detail"]
    assert _baseline(db) == 0
    ok = tc.post("/api/ops/selfcheck/accept", json=body, headers=dict(auth, **XHR, **{"X-Owner-Password": OWNER_PW}))
    assert ok.status_code == 200 and ok.json()["accepted"] == {"vanished": 1} and ok.json()["text"].startswith("Accepted: 1 item"), ok.text
    assert _baseline(db) == 1


def test_the_owners_accept_needs_the_owners_password_on_vercel_too(site, monkeypatch):
    import remitwatch.api as api
    from remitwatch import cloud, selfcheck
    tc, auth, _api, state = site
    monkeypatch.setattr(api, "OWNER_PASSWORD", OWNER_PW)
    copy = cloud.SERVING_COPY.connection()
    run_id = selfcheck.meta_get(copy, "selfcheck:offenders_run") or RUN
    copy.close()
    body = {"checks": ["vanished"], "run_id": run_id}
    assert tc.post("/api/ops/selfcheck/accept", json=body, headers=dict(auth, **XHR)).status_code == 403
    ok = tc.post("/api/ops/selfcheck/accept", json=body, headers=dict(auth, **XHR, **{"X-Owner-Password": OWNER_PW}))
    assert ok.status_code == 200 and ok.json().get("asked") is True, ok.text


@pytest.mark.skipif(not _browser_ready(), reason="needs node and Playwright for node")
def test_the_owner_who_unlocked_the_owner_figures_accepts_from_the_data_tab(tmp_path):
    db = _store_with_a_failing_check(tmp_path)
    out = _serve({"REMITWATCH_MODE": "server", "REMITWATCH_DB": db, "REMITWATCH_OWNER_PASSWORD": OWNER_PW,
                  "REMITWATCH_TMP": str(tmp_path / "scratch2")}, "hook_accept_page.js", OWNER_PW, "unlocked")
    assert not out["errors"], out["errors"]
    first = out["accept"][0]
    assert first["status"] == 200 and first["sent_owner"], (first, out.get("notice"))
    assert _baseline(db) == 1 and out["notice"].startswith("Accepted: 1 item"), out["notice"]
    # the run shown is said as accepted; the same Accept is not offered again
    assert not out["after"]["accept_again"] and f"Accepted: what run {RUN} found" in out["after"]["running"], out["after"]


@pytest.mark.skipif(not _browser_ready(), reason="needs node and Playwright for node")
def test_the_data_tab_asks_the_owner_for_the_owners_password_to_accept(tmp_path):
    db = _store_with_a_failing_check(tmp_path)
    out = _serve({"REMITWATCH_MODE": "server", "REMITWATCH_DB": db, "REMITWATCH_OWNER_PASSWORD": OWNER_PW,
                  "REMITWATCH_TMP": str(tmp_path / "scratch2")}, "hook_accept_page.js", OWNER_PW, "locked")
    assert out["accept"] and out["accept"][0]["status"] == 403 and not out["accept"][0]["sent_owner"], out["accept"]
    assert out["asks_owner_password"] and "owner's decision" in out["ask_text"], (out["ask_text"], out.get("notice"))
    assert _baseline(db) == 0


def test_accepting_a_check_with_nothing_to_accept_says_so(tmp_path, monkeypatch):
    """A warning check keeps no list (selfcheck._save_offenders keeps the critical checks' only): accepting it accepts
    nothing, and the answer says so instead of "Accepted."."""
    db = _store_with_a_failing_check(tmp_path, check="line_balance", severity="warning", offenders=False)
    tc, auth = _server_client(monkeypatch, db)
    r = tc.post("/api/ops/selfcheck/accept", json={"checks": ["line_balance"], "run_id": RUN}, headers=dict(auth, **XHR))
    body = r.json()
    assert r.status_code == 200 and body["accepted"] == {} and body.get("text") and "Accepted" not in body["text"], body
    assert "Nothing was accepted" in body["text"]


@pytest.mark.skipif(not _browser_ready(), reason="needs node and Playwright for node")
def test_the_data_tab_offers_no_accept_for_a_warning_alone(tmp_path):
    db = _store_with_a_failing_check(tmp_path, check="line_balance", severity="warning", offenders=False)
    out = _serve({"REMITWATCH_MODE": "server", "REMITWATCH_DB": db, "REMITWATCH_TMP": str(tmp_path / "scratch2")},
                 "hook_accept_page.js", "unused-owner-password", "look")
    assert not out["errors"] and not out["offered"], out


# ======================================================================================================================
# hook-r2-2: safe mode is said above every page, and Today gives no all clear while /api/meta's banner is up
# ======================================================================================================================
def test_meta_publishes_the_banner_today_reads(tmp_path, monkeypatch):
    db = _db(tmp_path)
    Store(db).close()
    tc, auth = _server_client(monkeypatch, db)
    conn = sqlite3.connect(db)
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('selfcheck:safe_mode', ?)",
                 (json.dumps({"on": True, "text": "Safe mode: the run of today is held."}),))
    conn.commit()
    conn.close()
    b = tc.get("/api/meta", headers=auth).json()["banner"]
    assert b["level"] == "safe_mode" and b["safe_mode"].startswith("Safe mode"), b
    today = open(os.path.join(REPO, "remitwatch", "static", "today.js"), encoding="utf-8").read()
    assert "(state.meta || {}).banner" in today and "selfcheck_banner" not in today


@pytest.mark.skipif(not _browser_ready(), reason="needs node and Playwright for node")
def test_every_worklist_page_says_safe_mode_above_everything(tmp_path):
    from remitwatch import pipeline
    db = _db(tmp_path)
    pipeline.run_cycle(db, sync=False, local=_feed(tmp_path), email=False, report=False, log=lambda *a: None)
    s = Store(db)
    s.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('selfcheck:safe_mode', ?)",
                   (json.dumps({"on": True, "text": "Safe mode: the run of today is held."}),))
    s.conn.commit()
    s.close()
    out = _serve({"REMITWATCH_MODE": "server", "REMITWATCH_DB": db, "REMITWATCH_TMP": str(tmp_path / "scratch2")},
                 "hook_pages.js", "1400", "today,denials,worklist,data")
    assert not out["errors"], out["errors"]
    missing = [t for t, d in out["tabs"].items() if "Safe mode" not in d["bar"]]
    assert not missing, f"no safe-mode bar above: {missing}"
    assert "is-done" not in json.dumps(out["tabs"]["today"]) or "Nothing closes this week" not in out["tabs"]["today"]["text"]


# ======================================================================================================================
# hook-r2-3: a pressed run that hands the analysis on says the money in the files it read
# ======================================================================================================================
def test_a_handed_on_analysis_says_the_money_read_and_not_analysed(box):
    """Five new claims are read, each $147.56 short of a $223.65 charge ($737.80 in all), and the analysis is handed on
    under memory pressure: the note says the 5 claims and their $738, beside the open items not checked against them."""
    from remitwatch import cloud
    from tests.test_features import claim, svc
    from tests.test_remitwatch import remit
    root, client, state = box
    recent = (date.today() - timedelta(days=20)).strftime("%Y%m%d")
    folder = root / DATA
    folder.mkdir(parents=True, exist_ok=True)
    claims = visits_with_downcodes()
    for n, i in enumerate(range(0, len(claims), 10)):
        (folder / f"r{n:03d}.RMT").write_bytes(remit("".join(claims[i:i + 10]), "100.00", seg_count=99, isa_ctrl=f"{n + 1:09d}",
                                                     pay_date=recent))
    assert "detect" in _cloud(state, client)
    # the next file arrives in a later second than that analysis began, as in a real chain (its next run comes minutes
    # later): cloud.read_since_analysis compares the file's reading with the analysis's start to the second, so a file
    # read in the very same second would be taken as one that analysis saw (the test failed whenever the two shared it)
    began = int(time.time())
    while int(time.time()) == began:
        time.sleep(0.02)
    later = [claim(300 + i, svc("99214", "223.65", "76.09", "76.09", "CAS*CO*186*51.71~CAS*CO*45*95.85~"), cfi="16")
             for i in range(5)]
    (folder / "later.RMT").write_bytes(remit("".join(later), "100.00", seg_count=99, isa_ctrl="000000050", pay_date=recent))
    st = json.loads(state.get(cloud.STATUS))
    st["memory_mb"] = {"peak": 2100, "limit": 2278}             # the last run came close to its memory: this one spreads
    state.put(cloud.STATUS, json.dumps(st).encode())
    read = _cloud(state, client)
    assert read.get("deferred") == "detection"
    un = read["unchecked"]
    assert un["read"] == {"claims": 5, "lines": 5, "cents": 73780}, un
    assert "5 remittance claims (5 lines)" in un["text"] and "$738" in un["text"] and "none of it is on a list" in un["text"], un["text"]
    done = _cloud(state, client, trigger="continue")
    assert "detect" in done and not done.get("unchecked")
    header = open(os.path.join(REPO, "remitwatch", "static", "features.js"), encoding="utf-8").read()
    assert "un.read.claims" in header                     # the header chip says them too


@pytest.mark.parametrize("kind", ["file", "compact"])
def test_what_was_read_since_the_last_analysis_is_counted_on_both_store_kinds(tmp_path, kind):
    """cloud.read_since_analysis on the server's file store and the compact image Vercel holds (which keeps no file hash on
    a line): the claims and lines of files read after the last analysis began, and their charges the payers did not pay;
    superseded copies and files read before it are left out."""
    from remitwatch import cloud
    from tests.test_runlog import make_store
    s = make_store(kind, tmp_path)
    c = s.conn
    c.execute("INSERT INTO detection_runs (run_id, started_at, finished_at, version, lines, findings, summary) "
              "VALUES ('run-20261126T100000Z-aaaa', '2026-11-26T10:00:00+00:00', '2026-11-26T10:03:00+00:00', 'x', 0, 0, '{}')")
    for sha, at in (("f-old", "2026-11-26T09:00:00+00:00"), ("f-new", "2026-11-26T11:00:00+00:00"), ("f-sup", "2026-11-26T11:00:00+00:00")):
        c.execute("INSERT INTO files (sha256, kind, ingested_at) VALUES (?, '835', ?)", (sha, at))
    for tk, sha, sup in (("t-old", "f-old", 0), ("t-new", "f-new", 0), ("t-sup", "f-sup", 1)):
        c.execute("INSERT INTO transactions (transaction_key, file_sha256, superseded) VALUES (?, ?, ?)", (tk, sha, sup))
    rows = [("l1", "c-old", "t-old", 10000, 4000), ("l2", "c-new", "t-new", 22365, 7609), ("l3", "c-new", "t-new", 5000, 6000),
            ("l4", "c-new2", "t-new", 22365, 7609), ("l5", "c-sup", "t-sup", 9999, 0)]
    c.executemany("INSERT INTO lines (line_key, claim_key, transaction_key, charge_cents, paid_cents) VALUES (?,?,?,?,?)", rows)
    c.commit()
    got = cloud.read_since_analysis(c)
    assert {k: got[k] for k in ("claims", "lines", "cents")} == {"claims": 2, "lines": 3, "cents": 2 * 14756}, got
    s.close()


# ======================================================================================================================
# hook-r2-6 and hook-r1-7: a press the site cannot start names the real cause
# ======================================================================================================================
class _Answer:
    def __init__(self, code: int):
        self.status_code, self.headers, self.text = code, {}, "refused"


@pytest.mark.parametrize("code", [401, 403, 500, 503])
def test_a_press_the_site_cannot_start_names_the_real_cause(site, monkeypatch, code):
    import requests
    tc, auth, _api, _state = site
    assert os.environ.get("CRON_SECRET")
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Answer(code))
    r = _press(tc, auth)
    detail = r.json()["detail"]
    assert r.status_code == 502 and "CRON_SECRET is not set" not in detail and str(code) in detail, (r.status_code, detail)
    if code in (401, 403):
        assert "VERCEL_AUTOMATION_BYPASS_SECRET" in detail, detail
    if code == 503:
        assert "state folder" in detail, detail


def test_a_press_whose_self_call_is_refused_by_the_network_names_the_real_cause(site, monkeypatch):
    import requests
    tc, auth, _api, _state = site

    def refused(*a, **k):
        raise requests.exceptions.ConnectionError("connection refused")
    monkeypatch.setattr(requests, "post", refused)
    r = _press(tc, auth)
    assert r.status_code == 502 and "network error" in r.json()["detail"] and "CRON_SECRET" not in r.json()["detail"], r.json()


def test_a_press_without_the_secret_still_names_the_secret(site, monkeypatch):
    tc, auth, _api, _state = site
    monkeypatch.delenv("CRON_SECRET")
    r = _press(tc, auth)
    assert r.status_code == 503 and "CRON_SECRET is not set" in r.json()["detail"], r.json()


# ======================================================================================================================
# hook-r2-7: Run the self-audit now on the server waits for no cycle and never answers 500
# ======================================================================================================================
def test_the_self_audit_route_while_a_cycle_holds_the_lock_answers_in_words(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import remitwatch.api as api
    from remitwatch import pipeline
    db = _db(tmp_path)
    pipeline.run_cycle(db, sync=False, local=_feed(tmp_path), email=False, report=False, log=lambda *a: None)
    _tc, auth = _server_client(monkeypatch, db)
    tc = TestClient(api.app, raise_server_exceptions=False)      # what the browser gets
    s = Store(db)
    token = pipeline.acquire_lock(s.conn)              # a cycle is running (the scheduler's lock row)
    s.conn.commit()
    try:
        held = tc.post("/api/ops/selfcheck/audit", headers=dict(auth, **XHR))
        writer = sqlite3.connect(db, timeout=1)
        writer.execute("BEGIN IMMEDIATE")              # ... and is saving what detection found
        writer.execute("UPDATE meta SET value=value WHERE key='selfcheck:last'")
        t0 = time.time()
        try:
            busy = tc.post("/api/ops/selfcheck/audit", headers=dict(auth, **XHR))
        finally:
            writer.rollback()
            writer.close()
        seconds = time.time() - t0
    finally:
        pipeline.release_lock(s.conn, token)
        s.close()
    assert held.status_code == 409 and "A cycle is running" in held.json()["detail"], held.text[:200]
    # the store busy under the cycle's save: the same sentence, no server error (the reviewer's test: below 500)
    assert busy.status_code == 409 and "A cycle is running" in busy.json()["detail"] and seconds < 10, (busy.status_code, seconds)
    ran = tc.post("/api/ops/selfcheck/audit", headers=dict(auth, **XHR))      # with no cycle going, it runs
    assert ran.status_code == 200 and ran.json()["ran"] is True, ran.text[:200]


# ======================================================================================================================
# hook-r2-8: on Vercel an acceptance the checks overtook is said, at once or by the run that refused it
# ======================================================================================================================
def _vanished_offender(state) -> tuple[str, str]:
    """The run the page shows: its vanished check failed with one offender. Returns (its run id, the item)."""
    from remitwatch import cloud, selfcheck
    store, _ = cloud.load_raw(state)
    c = store.conn
    r1 = (selfcheck.meta_get(c, selfcheck.K_LAST, {}) or {}).get("run_id")
    fid = c.execute("SELECT finding_id FROM findings LIMIT 1").fetchone()[0]
    selfcheck.meta_set(c, "selfcheck:offenders_run", r1)
    c.execute("DELETE FROM selfcheck_offenders")
    c.execute("INSERT INTO selfcheck_offenders (check_id, key, cents, run_id) VALUES ('vanished', ?, 12300, ?)", (fid, r1))
    c.commit()
    cloud.save_raw(state, store, lambda *a: None)
    store.close()
    return r1, fid


def test_on_vercel_an_acceptance_the_checks_overtook_is_refused_at_once(site):
    """The dashboard's copy already holds a later run's checks than the page showed: the route says so at once, as the
    server does, and journals nothing."""
    from remitwatch import cloud
    tc, auth, _api, state = site
    r1, _fid = _vanished_offender(state)
    cycle(state, None, trigger="press", force_detect=True)          # another person's press: the checks run again
    cloud.SERVING_COPY = cloud.ServingCopy()                        # the copy that run made
    r = tc.post("/api/ops/selfcheck/accept", json={"checks": ["vanished"], "run_id": r1}, headers=dict(auth, **XHR))
    assert r.status_code == 200 and r.json().get("stale") and "ran again" in r.json()["text"], r.json()
    assert not [n for n, a in cloud.read_actions(state) if a.get("kind") == "selfcheck_request"]


def test_on_vercel_an_acceptance_refused_at_the_fold_is_said(site):
    """An acceptance journaled while a run re-ran the checks (the page showed r1; the run that folds it finds a later
    run's checks): its status says it was not taken, and the Data tab's panel says so with the run it named."""
    from remitwatch import cloud
    tc, auth, _api, state = site
    r1, _fid = _vanished_offender(state)
    cycle(state, None, trigger="press", force_detect=True)          # the checks ran again ...
    cloud.record_action(state, {"kind": "selfcheck_request", "what": "accept", "checks": ["vanished"], "run_id": r1,
                                "user": "owner@example.test"})      # ... while the owner's acceptance of r1 was on its way
    out = cycle(state, None, trigger="press", force_detect=True)    # the next press folds it
    said = out.get("selfcheck_requests") or []
    assert said and said[0]["stale"] and "ran again" in said[0]["text"], said
    assert json.loads(state.get(cloud.STATUS)).get("selfcheck_requests") == said
    cloud.SERVING_COPY = cloud.ServingCopy()
    sc_panel = tc.get("/api/ops/panels", headers=auth).json()["selfcheck"]
    refused = [a for a in sc_panel.get("acceptances") or [] if a.get("stale")]
    assert refused and refused[-1]["run_id"] == r1, sc_panel.get("acceptances")
    ops = open(os.path.join(REPO, "remitwatch", "static", "ops.js"), encoding="utf-8").read()
    assert "Not accepted: the acceptance of what run" in ops


def test_a_self_audit_asked_on_vercel_is_not_worked_out_on_the_dashboards_copy(site):
    """The journal is laid over the dashboard's copy for a quick view of what people recorded; the self-audit asked from
    the Data tab is the run's to do (the copy holds part of the store: an audit there would show figures that are not the
    practice's)."""
    from remitwatch import cloud, selfcheck
    tc, auth, _api, state = site
    assert tc.post("/api/ops/selfcheck/audit", headers=dict(auth, **XHR)).json().get("asked") is True
    copy = cloud.SERVING_COPY
    copy.ensure(force=True)
    c = copy.connection()
    try:
        assert not c.execute("SELECT COUNT(*) FROM selfcheck_audits").fetchone()[0]
        assert not (selfcheck.meta_get(c, selfcheck.K_AUDIT, {}) or {}).get("latest")
    finally:
        c.close()


# ======================================================================================================================
# hook-r2-9 and hook-r2-10: the Vercel pages and the runbook name the presses the site has
# ======================================================================================================================
def _vercel_env(root, tmp_path) -> dict:
    return {"REMITWATCH_MODE": "serverless", "REMITWATCH_STATE_DIR": str(root / "RemitWatch-state"),
            "CRON_SECRET": "s3cret-value-for-the-scheduler", "REMITWATCH_TMP": str(tmp_path / "scratch2")}


@pytest.mark.skipif(not _browser_ready(), reason="needs node and Playwright for node")
def test_a_stopped_chain_on_vercel_names_the_button_that_exists(box, tmp_path):
    from remitwatch import cloud
    root, client, state = box
    write_remits(root / DATA, visits_with_downcodes())
    cycle(state, client)
    st = json.loads(state.get(cloud.STATUS))
    st["halted"] = "30 runs in a row without finishing"
    st["more"] = True
    state.put(cloud.STATUS, json.dumps(st).encode())
    out = _serve(_vercel_env(root, tmp_path), "hook_pages.js", "1400", "data", "#opAnalysis")
    data = out["tabs"]["data"]
    assert "Stopped after 30 runs" in data["text"] and "The run chain stopped before it finished" in data["text"], data["text"][:800]
    assert "Run the analysis now" in data["buttons"] and "Sync and analyse now" not in data["buttons"], data["buttons"]
    assert "Sync and analyse now" not in data["text"], [x for x in data["text"].split(". ") if "Sync and analyse" in x]


def test_the_runbook_names_only_the_presses_and_limits_the_vercel_site_has():
    doc = open(os.path.join(REPO, "docs", "VERCEL_SETUP.md"), encoding="utf-8").read()
    rows = {ln.split("|")[1].strip(): ln for ln in doc.splitlines() if ln.startswith("| ") and ln.count("|") >= 4}
    assert "webhook covers" not in rows["Scheduler"] and "press" in rows["Scheduler"], rows["Scheduler"]
    assert "about a minute" not in rows["Time"] and "170 to 240 seconds" in rows["Time"], rows["Time"]
    assert "the Feed's button" not in doc and "Read them now" not in doc and "the first load on `/setup` is the" in doc


@pytest.mark.skipif(not _browser_ready(), reason="needs node and Playwright for node")
def test_the_presses_the_runbook_names_are_the_ones_the_vercel_site_has(box, tmp_path):
    root, client, state = box
    write_remits(root / DATA, visits_with_downcodes())
    cycle(state, client)
    seen = _serve(_vercel_env(root, tmp_path), "hook_upload_buttons.js")["seen"]
    assert seen["press_button"] and not seen["feed_button"] and not seen["upload_read_now_visible"], seen


# ======================================================================================================================
# hook-r2-11: an acceptance must name the run the page showed, on both hosts
# ======================================================================================================================
def test_accept_without_the_run_the_page_showed_accepts_nothing(tmp_path, monkeypatch):
    from remitwatch import selfcheck
    db = _db(tmp_path)
    s = Store(db)
    selfcheck.ensure_tables(s.conn)
    s.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('selfcheck:offenders_run', ?)", (json.dumps("run-20261127T101500Z-ffff"),))
    s.conn.execute("INSERT INTO selfcheck_offenders (check_id, key, cents, run_id) VALUES ('vanished', ?, 100, 'run-20261127T101500Z-ffff')", ("c" * 24,))
    s.conn.commit()
    s.close()
    tc, auth = _server_client(monkeypatch, db)
    for body in ({"checks": ["vanished"]}, {"checks": ["vanished"], "run_id": ""}, {"checks": ["vanished"], "run_id": None},
                 {"checks": ["vanished"], "run_id": "the latest"}):
        r = tc.post("/api/ops/selfcheck/accept", json=body, headers=dict(auth, **XHR))
        assert r.status_code == 400 and "which run" in r.json()["detail"], (body, r.text)
        assert _baseline(db) == 0, body


def test_on_vercel_an_acceptance_must_name_its_run_too(site):
    from remitwatch import cloud
    tc, auth, _api, state = site
    for body in ({"checks": ["vanished"]}, {"checks": ["vanished"], "run_id": None}):
        assert tc.post("/api/ops/selfcheck/accept", json=body, headers=dict(auth, **XHR)).status_code == 400
    assert not [n for n, a in cloud.read_actions(state) if a.get("kind") == "selfcheck_request"]


# ======================================================================================================================
# hook-r2-12: the analysis panel counts the open items on the lists, not every item the analysis found
# ======================================================================================================================
def test_the_items_on_the_lists_are_the_open_items(box, tmp_path, monkeypatch):
    import shutil

    from remitwatch import cloud
    from tests.test_runlog import denial_files
    root, client, state = box
    p1, p2 = denial_files(tmp_path / "f", paid_later=True)
    (root / DATA).mkdir(parents=True, exist_ok=True)
    shutil.copy(p1, root / DATA / "r001.RMT")
    cycle(state, client)
    shutil.copy(p2, root / DATA / "r002.RMT")               # the later remittance pays one denial: its item closes
    cycle(state, client, trigger="press", force_detect=True)
    st = json.loads(state.get(cloud.STATUS))
    store, _ = cloud.load_raw(state)
    try:
        n_open, cents = cloud.open_items(store.conn)
        closed = store.conn.execute("SELECT COUNT(*) FROM findings WHERE status='resolved' AND COALESCE(stale,0)=0").fetchone()[0]
    finally:
        store.close()
    assert closed >= 1
    assert st["analysed"]["open"] == n_open and st["analysed"]["open_cents"] == cents, (st["analysed"], n_open, closed)
    monkeypatch.setenv("REMITWATCH_MODE", "serverless")
    monkeypatch.setenv("REMITWATCH_STATE_DIR", str(root / "RemitWatch-state"))
    from remitwatch import api_ops
    cloud.SERVING_COPY = cloud.ServingCopy()
    conn = cloud.SERVING_COPY.connection()
    try:
        lists = api_ops.analysis_status(conn)["lists"]
    finally:
        conn.close()
    assert lists == {"n": n_open, "cents": cents}, lists
    ops = open(os.path.join(REPO, "remitwatch", "static", "ops.js"), encoding="utf-8").read()
    assert "'open item'" in ops and "on its lists" in ops


# ======================================================================================================================
# hook-r1-6: the Vercel logs panel sends nobody to a daily cron that no longer exists
# ======================================================================================================================
def test_the_vercel_logs_panel_does_not_send_the_owner_to_a_daily_cron(site):
    from remitwatch import api_ops, cloud, runlog
    tc, auth, _api, state = site
    sink = runlog.sink_for(state=state)
    now = datetime.now(timezone.utc)
    for d in (3, 2, 1):
        t = now - timedelta(days=d)
        runlog.update_index(sink, {"run_id": t.strftime("run-%Y%m%dT%H%M%SZ") + "-ab12", "kind": "cycle", "host_mode": "serverless",
                                   "trigger": "press", "started_at": runlog._iso(t), "finished_at": runlog._iso(t + timedelta(minutes=4)),
                                   "outcome": "ok", "seconds": 240, "errors": 0, "repairs": 0})
    assert tc.get("/api/ops/logs", headers=auth).status_code == 200
    conn = cloud.SERVING_COPY.connection()
    try:
        later = runlog.panel(conn, api_ops._logs(), now=now + timedelta(days=4))
    finally:
        conn.close()
    assert "daily cron" not in later["sentence"] and not later.get("stale"), later["sentence"]
    assert "only when someone presses Run the analysis now" in later["sentence"] and "nobody has for 4 days" in later["sentence"], later["sentence"]
