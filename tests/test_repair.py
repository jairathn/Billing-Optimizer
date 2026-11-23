"""
Tests for remitwatch.repair, the self-repair registry: each remedy driven by a fake client or reader that fails on
purpose (Dropbox, TriZetto and SRFax calls that drop, time out or answer 5xx/429; a file that cannot be read; a derived
table that fails its check; a run that was killed; a lock left by a dead run; a store missing a column; memory
pressure). Each must be tried within its limits, logged with before and after, held when it runs out, and raise an
alert that says what failed, where, what was tried and the next step. Both hosts' ways of keeping the registry's state
(a server's logs folder and store file; a serverless state folder and the compact in-memory store). Invented data only.
"""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import sys
import time

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch import cloud, pipeline, repair, runlog  # noqa: E402
from remitwatch.detect import EXTRA_FINDING_COLS, FINDINGS_DDL, ensure_schema  # noqa: E402
from remitwatch.dropbox_sync import DropboxAuthError  # noqa: E402
from remitwatch.fax import FaxError  # noqa: E402
from remitwatch.state import LocalState  # noqa: E402
from remitwatch.store import Store  # noqa: E402
from remitwatch.trizetto import SignInRefused, Stalled  # noqa: E402


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    for k in ("VERCEL", "REMITWATCH_STATE_DIR", "DROPBOX_REFRESH_TOKEN", "SRFAX_ACCESS_ID", "TRIZETTO_SFTP_USER"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    monkeypatch.setenv("REMITWATCH_TMP", str(tmp_path / "scratch"))
    runlog.set_clock(None)
    yield
    runlog.set_clock(None)
    runlog._CURRENT.set(None)


def http_error(status: int) -> requests.HTTPError:
    r = requests.Response()
    r.status_code = status
    return requests.HTTPError(f"{status} Server Error", response=r)


class Waits(list):
    """A sleep that records the waits instead of waiting."""
    def __call__(self, s):
        self.append(s)


class FlakyDropbox:
    """A Dropbox client that fails with the given errors, one per call, then answers."""

    def __init__(self, failures):
        self.failures = list(failures)
        self.calls = 0
        self.app_secret = "fake"
        self.fail_midway = False

    def _maybe_fail(self):
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)

    def download_bytes(self, path):
        self._maybe_fail()
        return b"data", {"path": path}

    def list_folder(self, path, recursive=True):
        self._maybe_fail()
        yield {"name": "a.RMT"}
        if self.fail_midway:
            self.fail_midway = False
            raise requests.ConnectionError("Connection reset by peer")
        yield {"name": "b.RMT"}


class FlakyTriZetto:
    """A TriZetto pull that stalls, then reads two remittances."""

    def __init__(self, failures):
        self.failures = list(failures)
        self.calls = 0

    def pull(self):
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return {"remits": 2}


class FlakySRFax:
    """SRFax's status check: 503s, then an answer. A send whose fate is unknown is never retried."""

    def __init__(self, failures):
        self.failures = list(failures)
        self.calls = 0

    def status(self, fax_id):
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return {"fax_id": fax_id, "sent": True}

    def send(self, to, pdf):
        self.calls += 1
        raise FaxError("SRFax answered 502 without a readable reply.", maybe_sent=True)


def server_registry(tmp_path, rl=None):
    store = Store(str(tmp_path / "data" / "remitwatch.sqlite"))
    sink = runlog.DirSink(str(tmp_path / "data" / "logs"))
    return store, repair.Registry(conn=store.conn, sink=sink, run=rl)


def compact_store() -> Store:
    blank = Store(":memory:", compact=True)
    image = blank.to_bytes()
    blank.close()
    return Store.from_bytes(image)


# ======================================================================================================= what is transient
@pytest.mark.parametrize("exc, transient", [
    (requests.ConnectionError("Connection reset by peer"), True),
    (requests.Timeout("read timed out"), True),
    (socket.timeout("timed out"), True),
    (ConnectionResetError(104, "Connection reset by peer"), True),
    (EOFError(), True),                                              # an SFTP line that closed
    (RuntimeError("Dropbox files/list_folder: too many retries"), True),
    (RuntimeError("Dropbox files/download failed 503: Service Unavailable"), True),
    (http_error(503), True),
    (http_error(429), True),
    (http_error(404), False),
    (Stalled("TriZetto stopped answering"), True),
    (SignInRefused("refused", kind="password"), False),              # trying again would lock the login
    (DropboxAuthError("Dropbox refused the token"), False),
    (FaxError("SRFax answered 502 without a readable reply.", maybe_sent=True), False),   # the fax may have gone out
    (FaxError("SRFax did not answer (ReadTimeout).", maybe_sent=False), True),
    (ValueError("invalid literal for int()"), False),               # a code bug is never retried
    (KeyError("work_state"), False),
])
def test_what_counts_as_a_transient_failure(exc, transient):
    assert repair.is_transient(exc) is transient


# =========================================================================================================== network
def test_a_dropbox_call_that_fails_twice_is_tried_again_and_logged(tmp_path):
    rl = runlog.start_run("cycle", db_path=str(tmp_path / "data" / "remitwatch.sqlite"), host="server")
    store, reg = server_registry(tmp_path, rl)
    client = FlakyDropbox([requests.ConnectionError("Connection reset by peer"), RuntimeError("Dropbox files/download: too many retries")])
    waits = Waits()
    got = repair.retry_call(client.download_bytes, "/x.RMT", target="dropbox", registry=reg, sleep=waits)
    assert got[0] == b"data" and client.calls == 3 and waits == [2, 8]
    outcomes = [r["outcome"] for r in rl.manifest["repairs"]]
    assert outcomes == ["retrying", "retrying", "repaired"]
    rows = store.conn.execute("SELECT remedy, target, attempt, outcome FROM repair_log ORDER BY id").fetchall()
    assert [tuple(r) for r in rows] == [("network", "dropbox", 1, "retrying"), ("network", "dropbox", 2, "retrying"),
                                        ("network", "dropbox", 3, "repaired")]
    err = json.loads(store.conn.execute("SELECT error FROM repair_log ORDER BY id LIMIT 1").fetchone()[0])
    assert err["type"] == "ConnectionError" and err["where"]
    assert reg.entry("network", "dropbox")["status"] == "ok" and not reg.open_problems()
    rl.finish()
    store.close()


def test_a_step_that_keeps_failing_is_held_the_run_goes_on_and_three_runs_raise_an_alert(tmp_path):
    db = str(tmp_path / "data" / "remitwatch.sqlite")
    for run in range(3):
        rl = runlog.start_run("cycle", db_path=db, host="server")
        store, reg = server_registry(tmp_path, rl)
        client = FlakyDropbox([requests.ConnectionError("Connection reset by peer")] * 10)
        with pytest.raises(repair.Held) as held:
            repair.retry_call(lambda: list(client.list_folder("/Trizetto Data")), target="dropbox", registry=reg, sleep=Waits())
        assert isinstance(held.value.cause, requests.ConnectionError) and client.calls == 4 and held.value.attempts == 4
        reg.save()
        e = reg.entry("network", "dropbox")
        assert e["failed_runs"] == run + 1
        assert e["status"] == ("exhausted" if run == 2 else "retrying")
        alerts = repair.alert_candidates(store.conn)
        if run < 2:
            assert alerts == []
        else:
            (a,) = alerts
            assert a["kind"] == "SELF_REPAIR_FAILED" and a["severity"] == "high" and a["key"] == ["repair", "network", "dropbox"]
            assert "a connection to Dropbox, TriZetto or SRFax" in a["title"] and "(dropbox)" in a["title"]
            assert "Tried: 3 attempt(s) over 3 run(s)" in a["title"] and "give it to Claude Code with the repository" in a["title"]
            assert "Data tab" in a["title"] and "—" not in a["title"]
        rl.finish()
        store.close()
    # the next run's call works: the problem is repaired and the alert has nothing to stand on
    store, reg = server_registry(tmp_path)
    assert repair.retry_call(FlakyDropbox([]).download_bytes, "/x", target="dropbox", registry=reg, sleep=Waits())[0] == b"data"
    reg.save()
    assert repair.alert_candidates(store.conn) == [] and reg.open_problems() == []
    assert store.conn.execute("SELECT outcome FROM repair_log ORDER BY id DESC LIMIT 1").fetchone()[0] == "repaired"
    store.close()


def test_a_refused_token_or_a_code_bug_is_not_retried(tmp_path):
    store, reg = server_registry(tmp_path)
    for exc in (DropboxAuthError("Dropbox refused the token"), KeyError("work_state")):
        client = FlakyDropbox([exc])
        waits = Waits()
        with pytest.raises(type(exc)):
            repair.retry_call(client.download_bytes, "/x", target="dropbox", registry=reg, sleep=waits)
        assert client.calls == 1 and waits == []
    assert not store.conn.execute("SELECT 1 FROM sqlite_master WHERE name='repair_log'").fetchone()     # nothing to log
    store.close()


def test_the_backoff_never_passes_the_runs_deadline(tmp_path):
    store, reg = server_registry(tmp_path)
    now = [1000.0]
    waits = Waits()

    def sleep(s):
        waits.append(s)
        now[0] += s
    client = FlakyDropbox([requests.Timeout("read timed out")] * 10)
    with pytest.raises(repair.Held) as held:
        repair.retry_call(client.download_bytes, "/x", target="dropbox", registry=reg, sleep=sleep, until=1009.0,
                          clock=lambda: now[0])
    assert waits == [2] and client.calls == 2 and held.value.attempts == 2        # the next wait (8 s) would pass the deadline
    store.close()


def test_a_wrapped_client_retries_each_call_and_a_listing_only_before_its_first_entry(tmp_path):
    store, reg = server_registry(tmp_path)
    client = FlakyDropbox([requests.ConnectionError("reset")])
    safe = repair.resilient(client, "dropbox", registry=reg, sleep=Waits())
    assert [e["name"] for e in safe.list_folder("/Trizetto Data")] == ["a.RMT", "b.RMT"] and client.calls == 2
    assert safe.app_secret == "fake" and repair.resilient(safe, "dropbox") is safe
    import inspect
    assert list(inspect.signature(safe.download_bytes).parameters) == ["path"]          # callers that read it still can
    client.fail_midway = True                     # data already flowed: the caller handles a failure in the middle
    with pytest.raises(requests.ConnectionError):
        list(safe.list_folder("/Trizetto Data"))
    client.failures = [http_error(503), http_error(429)]
    assert safe.download_bytes("/x")[0] == b"data"
    store.close()


def test_trizetto_stalls_are_retried_and_a_refused_password_is_not(tmp_path):
    store, reg = server_registry(tmp_path)
    tz = FlakyTriZetto([Stalled("TriZetto stopped answering"), EOFError()])
    assert repair.retry_call(tz.pull, target="trizetto", registry=reg, sleep=Waits()) == {"remits": 2} and tz.calls == 3
    seen = {}
    repair.retry_call(lambda conn, handle, deadline, log: seen.update(deadline=deadline), "conn", "handle", deadline=123.0,
                      log=print, target="trizetto", registry=reg, until=10 ** 10)
    assert seen == {"deadline": 123.0}                           # the callee's own deadline passes through
    tz = FlakyTriZetto([SignInRefused("refused", kind="password")])
    with pytest.raises(SignInRefused):
        repair.retry_call(tz.pull, target="trizetto", registry=reg, sleep=Waits())
    assert tz.calls == 1
    store.close()


def test_srfax_answers_are_retried_but_a_send_whose_fate_is_unknown_never_is(tmp_path):
    store, reg = server_registry(tmp_path)
    fx = FlakySRFax([FaxError("SRFax did not answer (ConnectTimeout)."), http_error(503)])
    safe = repair.resilient(fx, "srfax", registry=reg, sleep=Waits())
    assert safe.status("F1") == {"fax_id": "F1", "sent": True} and fx.calls == 3
    with pytest.raises(FaxError):
        safe.send("5025550100", b"%PDF")
    assert fx.calls == 4
    assert safe.send == fx.send                                   # a send is never even wrapped
    reads = repair.resilient(FlakySRFax([http_error(503)]), "srfax", registry=reg, sleep=Waits(), only=("status",))
    assert reads.status("F2")["sent"] and reads._client.calls == 2
    reads._client.failures = [http_error(503)]
    reads.check = None                                            # (an attribute set on the wrapper stays on the wrapper)
    with pytest.raises(requests.HTTPError):
        repair.resilient(FlakySRFax([http_error(503)]), "srfax", only=("check",)).status("F3")   # not in `only`: not retried
    store.close()


# ====================================================================================================== unreadable file
def test_an_unreadable_file_is_held_after_two_runs_and_the_others_keep_going(tmp_path):
    def reader(key):
        if key == "bad":
            raise ValueError("line 4: expected 39 columns, found 12")
        return {"rows": 10}
    for run in range(2):
        rl = runlog.start_run("cycle", db_path=str(tmp_path / "data" / "remitwatch.sqlite"), host="server")
        store, reg = server_registry(tmp_path, rl)
        assert repair.read_file("good" * 4, reader, "good", registry=reg, kind="production") == {"rows": 10}
        exc_type = repair.Held if run == 1 else ValueError
        with pytest.raises(exc_type):
            repair.read_file("abcdef0123456789", reader, "bad", registry=reg, kind="production")
        reg.save()
        assert any(e["step"] == "read_file" for e in rl.manifest["errors"])
        rl.finish()
        store.close()
    store, reg = server_registry(tmp_path)
    calls = []
    with pytest.raises(repair.Held) as held:                      # held: not read again until a person acts
        repair.read_file("abcdef0123456789", lambda: calls.append(1), registry=reg, kind="production")
    assert calls == [] and "expected 39 columns" in held.value.reason
    (p,) = reg.open_problems()
    assert p["target"] == "production:abcdef0123456789" and p["status"] == "held" and "Export it again from EMA" in p["next_step"]
    assert repair.alert_candidates(store.conn)[0]["key"] == ["repair", "unreadable_file", "production:abcdef0123456789"]
    assert reg.release("unreadable_file", "production:abcdef0123456789", by="the Data tab")
    assert repair.read_file("abcdef0123456789", lambda: "ok", registry=reg, kind="production") == "ok"
    store.close()


def test_held_and_refused_reports_become_open_problems(tmp_path):
    store, reg = server_registry(tmp_path)
    c = store.conn
    since = runlog._iso()
    c.execute("INSERT INTO ema_report_files (file_id, report, status, status_reason, attempts, received_at) VALUES (?,?,?,?,?,?)",
              ("f" * 64, "production", "held", "the file was cut short at row 4,211", 3, since))
    c.execute("INSERT INTO ema_report_files (file_id, report, status, received_at) VALUES (?,?,?,?)", ("e" * 64, "ar", "current", since))
    c.commit()
    assert repair.sweep_reports(c, reg, since) == 1 and repair.sweep_reports(c, reg, since) == 0
    (p,) = reg.open_problems()
    assert p["target"] == "production:" + "f" * 16 and "cut short" in p["reason"]
    store.close()


# ======================================================================================================= derived tables
def test_a_derived_table_is_rebuilt_once_and_safe_mode_is_asked_when_that_does_not_help(tmp_path):
    store, reg = server_registry(tmp_path)
    state = {"ok": False, "rebuilds": 0}

    def check():
        return {"ok": state["ok"], "gap_cents": 0 if state["ok"] else 125000}

    def fix():
        state["rebuilds"] += 1
        state["ok"] = True
    got = repair.rebuild_if_failing("ledger_summaries", check, fix, registry=reg)
    assert got["ok"] and got["rebuilt"] and not got["safe_mode"] and state["rebuilds"] == 1
    assert got["before"] == {"ok": False, "gap_cents": 125000} and got["after"]["ok"]
    state["ok"] = False
    got = repair.rebuild_if_failing("ledger_summaries", check, lambda: None, registry=reg)
    assert not got["ok"] and got["rebuilt"] and got["safe_mode"]
    (p,) = reg.open_problems()
    assert p["remedy"] == "derived_table" and p["status"] == "held" and "safe mode" in p["next_step"]
    outcomes = [r[0] for r in store.conn.execute("SELECT outcome FROM repair_log ORDER BY id")]
    assert outcomes == ["repaired", "gave_up"]

    def crashing():
        raise sqlite3.OperationalError("no such table: ema_lines")
    got = repair.rebuild_if_failing("link_map", crashing, lambda: None, registry=reg)
    assert got["safe_mode"] and got["before"] == {"ok": False, "error": "OperationalError"}
    store.close()


# ====================================================================================== killed runs, locks, schema, pressure
def test_killed_runs_are_logged_and_raise_an_alert_when_they_repeat_at_one_step(tmp_path):
    store, reg = server_registry(tmp_path)
    k = {"run_id": "run-20261122T101700Z-dead", "last_step": "detect", "peak_mb": 1990}
    assert repair.note_killed_runs(reg, [k], repaired={"ema_repair": {"part_a": True}}) == 1
    assert reg.open_problems() == []
    repair.note_killed_runs(reg, [dict(k, run_id="run-20261123T101700Z-dead")])
    (p,) = reg.open_problems()
    assert p["target"] == "step:detect" and p["status"] == "exhausted" and p["alert"]
    repair.note_run_finished(reg, ["load", "detect"])
    assert reg.open_problems() == []
    store.close()


def test_a_lock_left_by_a_dead_run_is_removed_by_its_rule_and_logged_on_both_hosts(tmp_path):
    db = str(tmp_path / "data" / "remitwatch.sqlite")
    store, reg = server_registry(tmp_path)
    dead = 2 ** 22 + 4321
    held = {"started": "2026-11-22T10:00:00+00:00", "heartbeat": "2026-11-22T10:00:00+00:00", "pid": dead, "token": "t",
            "host": pipeline._host()}
    store.conn.execute("INSERT INTO meta (key, value) VALUES (?, ?)", (pipeline.LOCK_KEY, json.dumps(held)))
    store.conn.commit()
    gone = pipeline.clear_locks_left_behind(db)
    assert gone == [pipeline.LOCK_KEY]
    assert repair.note_stale_lock(reg, gone, "pipeline.clear_locks_left_behind") == 1
    row = store.conn.execute("SELECT remedy, target, outcome, after FROM repair_log").fetchone()
    assert tuple(row[:3]) == ("stale_lock", "pipeline:lock", "repaired") and json.loads(row[3])["how"] == "pipeline.clear_locks_left_behind"
    store.close()
    # serverless: lock.json older than any run can be; the hook reads cloud.lock_state before cloud.acquire takes it over
    st = LocalState(str(tmp_path / "state"))
    st.put(cloud.LOCK, b'{"token": "old"}')
    old = time.time() - cloud.lock_stale_seconds() - 60
    os.utime(os.path.join(st.root, cloud.LOCK), (old, old))
    running, stalled = cloud.lock_state(st)
    assert running is None and stalled
    assert cloud.acquire(st)
    mem = compact_store()
    reg2 = repair.Registry(conn=mem.conn, sink=runlog.StateSink(st))
    assert repair.note_stale_lock(reg2, ["lock.json"], "cloud.acquire") == 1
    reg2.save()
    assert json.loads(st.get("logs/repair_state.json"))["saved_at"]
    # outside a run: the server's start and the serverless takeover log through log_stale_locks
    assert repair.log_stale_locks(["pipeline:lock"], "pipeline.clear_locks_left_behind", db_path=db) == 1
    c = sqlite3.connect(db)
    assert c.execute("SELECT COUNT(*) FROM repair_log WHERE remedy='stale_lock'").fetchone()[0] == 2
    c.close()
    assert repair.log_stale_locks(["lock.json"], "cloud.acquire", state=st) == 1 and repair.log_stale_locks([], "x") == 0


def test_a_store_missing_columns_is_migrated_and_logged(tmp_path):
    for kind in ("file", "compact"):
        store = Store(str(tmp_path / f"{kind}.sqlite")) if kind == "file" else compact_store()
        c = store.conn
        c.executescript(FINDINGS_DDL)                 # a store from before the columns added since (EXTRA_FINDING_COLS)
        reg = repair.Registry(conn=c)
        got = repair.check_schema(c, ensure_schema, {"findings": list(EXTRA_FINDING_COLS)}, registry=reg)
        assert got["ok"] and "work_state" in got["before"]["findings"]
        row = c.execute("SELECT remedy, target, outcome, before, after FROM repair_log").fetchone()
        assert tuple(row[:3]) == ("schema", "findings", "repaired") and json.loads(row[4]) == {"missing": []}
        assert repair.check_schema(c, ensure_schema, {"findings": list(EXTRA_FINDING_COLS)}, registry=reg) == {"ok": True, "missing": {}}
        c.execute("CREATE TABLE odd (a TEXT)")
        got = repair.check_schema(c, lambda conn: None, {"odd": ["b"]}, registry=reg)
        assert not got["ok"] and reg.open_problems()[0]["target"] == "odd"
        store.close()


def test_pressure_actions_are_logged_with_the_reading_that_caused_them(tmp_path):
    rl = runlog.start_run("cycle", db_path=str(tmp_path / "data" / "remitwatch.sqlite"), host="server")
    store, reg = server_registry(tmp_path, rl)
    n = repair.note_pressure(reg, [{"action": "spread", "target": "memory", "files_per_run": 1},
                                   {"action": "archive_tighter", "target": "memory", "horizon_months": 24}],
                             reading={"peak_mb": 1700, "budget_mb": 1600})
    assert n == 2
    rows = store.conn.execute("SELECT remedy, outcome, before, after FROM repair_log ORDER BY id").fetchall()
    assert [r[0] for r in rows] == ["pressure", "pressure"] and json.loads(rows[0][2]) == {"peak_mb": 1700, "budget_mb": 1600}
    assert json.loads(rows[1][3])["horizon_months"] == 24 and len(rl.manifest["repairs"]) == 2
    rl.finish()
    store.close()


# ========================================================================================================= persistence
def test_the_registry_keeps_its_state_on_both_hosts_and_the_newest_copy_wins(tmp_path):
    store, reg = server_registry(tmp_path)
    reg.failed("unreadable_file", "ar:0011223344556677", None, reason="held: no header row", hold=True)
    reg.save()
    again = repair.Registry(conn=store.conn, sink=runlog.DirSink(str(tmp_path / "data" / "logs")))
    assert again.held("unreadable_file", "ar:0011223344556677")
    store.close()
    # serverless: the state folder's copy is newer than the store's (a run that failed before saving the store)
    st = LocalState(str(tmp_path / "state"))
    mem = compact_store()
    runlog.set_clock(runlog.utcnow())
    r1 = repair.Registry(conn=mem.conn, sink=runlog.StateSink(st))
    r1.failed("network", "dropbox", requests.ConnectionError("reset"))
    r1.save()
    from datetime import timedelta
    runlog.set_clock(runlog.utcnow() + timedelta(minutes=5))
    r2 = repair.Registry(sink=runlog.StateSink(st))                    # a run whose store was not saved: only the folder
    r2.failed("network", "dropbox", requests.ConnectionError("reset"))
    r2.save()
    r3 = repair.Registry(conn=mem.conn, sink=runlog.StateSink(st))
    assert r3.entry("network", "dropbox")["failed_runs"] == 2


def test_recent_attempts_are_listed_newest_first_and_old_ones_pruned(tmp_path):
    from datetime import datetime, timedelta, timezone
    store, reg = server_registry(tmp_path)
    t = datetime(2026, 11, 22, tzinfo=timezone.utc)
    runlog.set_clock(t - timedelta(days=300))
    reg.record("network", "dropbox", "retrying")
    runlog.set_clock(t)
    reg.record("network", "dropbox", "repaired")
    got = repair.recent(store.conn)
    assert [g["outcome"] for g in got] == ["repaired", "retrying"]
    assert repair.prune(store.conn, now=t) == 1 and len(repair.recent(store.conn)) == 1
    store.close()
