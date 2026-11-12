"""
The hands-off pipeline and the scheduler that keeps the site current.

One cycle:  pull new files from Dropbox -> ingest -> detect -> alerts -> notify (-> weekly report)

A cycle can be started three ways and they all go through the same lock, so
two never overlap:
  * the background scheduler in the web process (REMITWATCH_SYNC_INTERVAL_MINUTES)
  * a Dropbox webhook, which fires within seconds of a new file landing
  * `python -m remitwatch.cli cycle` from cron or by hand

The scheduler runs each cycle in a child process. Detection holds every
service line in memory, and a child process gives that memory back when it
finishes and keeps a failure away from the dashboard.
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

from .store import Store

LOCK_KEY = "pipeline:lock"
LAST_KEY = "pipeline:last_cycle"
REPORT_KEY = "pipeline:last_report"
CHILD_TIMEOUT_SECONDS = 170 * 60        # a cycle the scheduler started is stopped after this long
LOCK_STALE_MINUTES = 180                # a lock with no heartbeat for this long was left behind by a crash; longer than the child timeout


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def dropbox_configured() -> bool:
    return all(os.environ.get(k) for k in ("DROPBOX_APP_KEY", "DROPBOX_APP_SECRET", "DROPBOX_REFRESH_TOKEN"))


def _meta_get(conn, key: str):
    r = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r[0] if r else None


def _meta_set(conn, key: str, value) -> None:
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value if isinstance(value, str) else json.dumps(value, default=str)))


def acquire_lock(conn) -> str | None:
    """Take the pipeline lock and return its token, or None when another cycle holds it. The holder refreshes a heartbeat;
    a lock whose heartbeat is three hours old was left behind by a crash and is taken over."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        raw = _meta_get(conn, LOCK_KEY)
        if raw:
            try:
                held = json.loads(raw)
                beat = datetime.fromisoformat(held.get("heartbeat") or held["started"])
                if datetime.now(timezone.utc) - beat < timedelta(minutes=LOCK_STALE_MINUTES):
                    conn.execute("ROLLBACK")
                    return None
            except Exception:
                pass
        token = secrets.token_hex(8)
        _meta_set(conn, LOCK_KEY, {"started": _now(), "heartbeat": _now(), "pid": os.getpid(), "token": token})
        conn.execute("COMMIT")
        return token
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _lock_owner(conn) -> dict:
    raw = _meta_get(conn, LOCK_KEY)
    try:
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def heartbeat(conn, token: str) -> None:
    held = _lock_owner(conn)
    if held.get("token") == token:
        held["heartbeat"] = _now()
        _meta_set(conn, LOCK_KEY, held)
        conn.commit()


def release_lock(conn, token: str | None = None) -> bool:
    """Release the lock. With a token, only the cycle that took the lock can release it."""
    if token is not None and _lock_owner(conn).get("token") != token:
        return False
    conn.execute("DELETE FROM meta WHERE key=?", (LOCK_KEY,))
    conn.commit()
    return True


def clear_lock_of_pid(db_path: str, pid: int) -> bool:
    """After a child process was killed, remove the lock it could not release itself."""
    store = Store(db_path)
    try:
        if _lock_owner(store.conn).get("pid") == pid:
            return release_lock(store.conn)
        return False
    finally:
        store.close()


def run_cycle(db_path: str, *, sync: bool = True, full: bool = False, local: str | None = None, force_detect: bool = False,
              report: bool | None = None, email: bool = True, report_dir: str = "data/reports", log=print) -> dict:
    """Run one cycle. `report=None` sends the summary report when the last one is at least REMITWATCH_REPORT_EVERY_DAYS old."""
    from .alerts import notify, run_alerts
    from .detect import run_detection
    from .dropbox_sync import ingest_new_files
    from .dropbox_sync import sync as dropbox_sync

    store = Store(db_path)
    out: dict = {"started": _now(), "trigger": os.environ.get("REMITWATCH_TRIGGER", "manual")}
    token = acquire_lock(store.conn)
    if not token:
        store.close()
        return {"skipped": "another cycle is running"}

    def beat_log(*a) -> None:           # long downloads report progress; each report also proves the cycle is alive
        log(*a)
        try:
            heartbeat(store.conn, token)
        except Exception:
            pass
    try:
        new_files: list[str] = []
        if sync:
            if dropbox_configured():
                st = dropbox_sync(store, full=full, log=beat_log)
                new_files = st.pop("new_files", [])
                out["sync"] = st
            else:
                out["sync"] = {"skipped": "Dropbox credentials are not configured"}
        if local:
            new_files += [os.path.join(dp, f) for dp, _d, fs in os.walk(local) for f in fs]
        ingested = {"remits": 0, "ema": 0, "claims_837": 0}
        heartbeat(store.conn, token)
        if new_files:
            ingested = ingest_new_files(store, new_files, log=log)
            ingested.pop("remit_stats", None)
            heartbeat(store.conn, token)
        out["ingest"] = ingested
        changed = (ingested.get("remits", 0) + ingested.get("ema", 0) + ingested.get("claims_837", 0)) > 0
        has_run = bool(store.conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='detection_runs'").fetchone()) and \
            bool(store.conn.execute("SELECT 1 FROM detection_runs LIMIT 1").fetchone())
        store.close()
        if changed or force_detect or not has_run:
            summary = run_detection(db_path)
            out["detect"] = {"findings": sum(summary.values())}
        store = Store(db_path)
        heartbeat(store.conn, token)
        if "detect" not in out:         # detection rebuilds the provider tables itself; this covers an upgrade with no new files
            from . import activity
            if activity.stale(store.conn):
                out["activity"] = activity.refresh(store, log=log)
                heartbeat(store.conn, token)
        out["alerts"] = run_alerts(store.conn)
        out["notified"] = notify(store.conn, log=log) if email else {"sent": 0, "skipped": True}
        every = int(os.environ.get("REMITWATCH_REPORT_EVERY_DAYS", "7"))
        last_report = _meta_get(store.conn, REPORT_KEY)
        due = not last_report or (datetime.now(timezone.utc) - datetime.fromisoformat(last_report)) >= timedelta(days=every)
        if report or (report is None and due):
            from .report import build_report, email_report, save_report
            text = build_report(store, sync_stats=out.get("sync") if isinstance(out.get("sync"), dict) else None, ingest_stats=ingested)
            out["report"] = save_report(build_report(store, sync_stats=out.get("sync") if isinstance(out.get("sync"), dict) else None,
                                                     ingest_stats=ingested, detail=True), report_dir)
            out["report_emailed"] = email_report(text) if email else False
            _meta_set(store.conn, REPORT_KEY, _now())
        out["finished"] = _now()
        return out
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
        out["finished"] = _now()
        raise
    finally:
        try:
            store.conn.execute("SELECT 1")
        except Exception:
            store = Store(db_path)
        try:        # the lock goes first and on its own, so a failure to write the summary can never leave it behind
            try:
                store.conn.rollback()
            except Exception:
                pass
            release_lock(store.conn, token)
        finally:
            try:
                _meta_set(store.conn, LAST_KEY, out)
                store.conn.commit()
            except Exception:
                pass
            store.close()


def status(db_path: str) -> dict:
    """What the dashboard shows about the feed: is it configured, when it last ran, how current the data is."""
    store = Store(db_path)
    c = store.conn
    try:
        last = _meta_get(c, LAST_KEY)
        lock = _meta_get(c, LOCK_KEY)
        newest = c.execute("SELECT MAX(payment_date) FROM transactions WHERE superseded=0").fetchone()[0]
        last_file = c.execute("SELECT MAX(ingested_at) FROM files").fetchone()[0]
        last_remit = c.execute("SELECT MAX(ingested_at) FROM files WHERE kind='835'").fetchone()[0]
        behind = (datetime.now(timezone.utc).date() - datetime.fromisoformat(newest).date()).days if newest else None
        interval = int(os.environ.get("REMITWATCH_SYNC_INTERVAL_MINUTES", "0") or 0)
        return {
            "dropbox_configured": dropbox_configured(), "webhook_ready": bool(os.environ.get("DROPBOX_APP_SECRET")),
            "interval_minutes": interval, "scheduler_running": SCHEDULER.is_alive(), "next_run": SCHEDULER.next_run,
            "running": bool(lock), "running_since": json.loads(lock)["started"] if lock else None,
            "last_cycle": json.loads(last) if last else None, "last_sync": _meta_get(c, "sync:last_run"),
            "newest_payment_date": newest, "days_behind": behind, "last_file_ingested": last_file, "last_remit_ingested": last_remit,
            "smtp_configured": bool(os.environ.get("SMTP_HOST") and os.environ.get("REPORT_TO")),
            "alert_webhook": bool(os.environ.get("ALERT_WEBHOOK_URL")), "note_review": bool(os.environ.get("ANTHROPIC_API_KEY")),
        }
    finally:
        store.close()


class Scheduler:
    """Background thread in the web process: runs a cycle every interval and whenever poked (webhook or the Sync now button)."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._wake = threading.Event()
        self._stop = threading.Event()
        self.next_run: str | None = None
        self.db_path = ""
        self.interval = 0
        self._trigger = "schedule"

    def is_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self, db_path: str, interval_minutes: int) -> None:
        if self.is_alive() and not self._stop.is_set():
            return
        if self.is_alive():                 # a stop was requested and the old thread is still finishing
            self._thread.join(timeout=5)
        self.db_path, self.interval = db_path, interval_minutes
        self._stop.clear()
        self._wake.clear()
        self._thread = threading.Thread(target=self._loop, name="remitwatch-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread and self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=2)

    def poke(self, trigger: str = "webhook") -> None:
        """Ask for a cycle soon. Several pokes in a short time become one cycle."""
        self._trigger = trigger
        self._wake.set()

    def _loop(self) -> None:
        first = True
        while not self._stop.is_set():
            wait = (45 if first else self.interval * 60) if self.interval > 0 else None
            first = False
            self.next_run = (datetime.now(timezone.utc) + timedelta(seconds=wait)).isoformat(timespec="seconds") if wait else None
            poked = self._wake.wait(timeout=wait)
            if self._stop.is_set():
                break
            if poked:
                # let a batch of uploads finish; a stop request ends the wait at once
                if self._stop.wait(timeout=int(os.environ.get("REMITWATCH_WEBHOOK_DEBOUNCE_SECONDS", "45"))):
                    break
                self._wake.clear()
            trigger, self._trigger = (self._trigger if poked else "schedule"), "schedule"
            self.run_child(trigger)

    def run_child(self, trigger: str) -> int:
        env = dict(os.environ, REMITWATCH_TRIGGER=trigger)
        try:
            proc = subprocess.Popen([sys.executable, "-m", "remitwatch.cli", "--db", self.db_path, "cycle"], env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            return -1
        try:
            return proc.wait(timeout=CHILD_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            try:
                clear_lock_of_pid(self.db_path, proc.pid)      # a killed child cannot release its own lock
            except Exception:
                pass
            return -9


SCHEDULER = Scheduler()


def start_scheduler_from_env(db_path: str) -> bool:
    interval = int(os.environ.get("REMITWATCH_SYNC_INTERVAL_MINUTES", "0") or 0)
    if interval <= 0 and not os.environ.get("DROPBOX_APP_SECRET"):
        return False
    SCHEDULER.start(db_path, interval)
    return True


def run_once_in_background(db_path: str, trigger: str = "button") -> None:
    """Sync now: use the scheduler when it is running, otherwise a one-off thread."""
    if SCHEDULER.is_alive():
        SCHEDULER.poke(trigger)
        return
    SCHEDULER.db_path = db_path
    threading.Thread(target=SCHEDULER.run_child, args=(trigger,), name="remitwatch-cycle", daemon=True).start()
