"""
The hands-off pipeline and the scheduler that keeps the site current.

One cycle:  pull new files from Dropbox (and TriZetto, and what was uploaded by hand) -> ingest -> detect -> alerts
            -> notify (-> weekly report)

A cycle can be started three ways and they all go through the same lock, so
two never overlap:
  * the background scheduler in the web process (REMITWATCH_SYNC_INTERVAL_MINUTES)
  * a Dropbox webhook, which fires within seconds of a new file landing
  * `python -m remitwatch.cli cycle` from cron or by hand

The scheduler runs each cycle in a child process. Detection holds every
service line in memory, and a child process gives that memory back when it
finishes and keeps a failure away from the dashboard.

A run that ends without releasing its lock (its container stopped, the memory
limit killed it) leaves the lock behind, and every cycle would be refused for
up to LOCK_STALE_MINUTES. So the scheduler removes the locks of a child that
ended, and ends its running child when the server stops; and the server's
start removes the locks of runs that ended with its last container
(clear_locks_left_behind), as the deploy script does after its own swaps.
"""
from __future__ import annotations

import json
import os
import secrets
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

from .store import Store

LOCK_KEY = "pipeline:lock"
LAST_KEY = "pipeline:last_cycle"
REPORT_KEY = "pipeline:last_report"
SERVERS_KEY = "pipeline:servers"        # the containers (host names) the web server started in on this store, newest last
SERVERS_KEPT = 10
CHILD_TIMEOUT_SECONDS = 170 * 60        # a cycle the scheduler started is stopped after this long
LOCK_STALE_MINUTES = 180                # a lock with no heartbeat for this long was left behind by a crash; longer than the child timeout
STOP_WAIT_SECONDS = 3                   # when the server stops, the time its running cycle gets to end before it is killed
BOOT_MARGIN_SECONDS = 120               # a lock last refreshed this long before the machine started was left by the restart


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def dropbox_configured() -> bool:
    return all(os.environ.get(k) for k in ("DROPBOX_APP_KEY", "DROPBOX_APP_SECRET", "DROPBOX_REFRESH_TOKEN"))


def _meta_get(conn, key: str):
    r = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r[0] if r else None


def _meta_set(conn, key: str, value) -> None:
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value if isinstance(value, str) else json.dumps(value, default=str)))


def _host() -> str:
    """The name of the machine or container this process runs in. Docker names each container after its own ID, so a
    lock says which container its run was in: a process in another container cannot be seen from this one."""
    try:
        return socket.gethostname()
    except OSError:
        return ""


def acquire_lock(conn) -> str | None:
    """Take the pipeline lock and return its token, or None when another cycle holds it. The holder refreshes a heartbeat;
    a lock whose heartbeat is three hours old was left behind by a crash and is taken over. The lock keeps the shape the
    deploy script reads (pid, started, heartbeat) and says which container took it (host)."""
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
        _meta_set(conn, LOCK_KEY, {"started": _now(), "heartbeat": _now(), "pid": os.getpid(), "token": token, "host": _host()})
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


def _held(value) -> dict:
    try:
        held = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return held if isinstance(held, dict) else {}


def _when(held: dict, *keys: str) -> float | None:
    """The first of these times the lock gives, in seconds since 1970."""
    for k in keys:
        try:
            return datetime.fromisoformat(held[k]).timestamp()
        except (KeyError, TypeError, ValueError):
            pass
    return None


def _run_locks(conn) -> list[tuple[str, str]]:
    """Every run's lock in the store: the cycle's, the fax run's, the backup's (meta rows whose key ends in ':lock')."""
    try:
        return conn.execute("SELECT key, value FROM meta WHERE key LIKE '%:lock'").fetchall()
    except sqlite3.Error:
        return []


def clear_lock_of_pid(db_path: str, pid: int, since: float | None = None) -> bool:
    """After a child process ended (killed, or it died), remove the locks it could not release itself: the cycle's, and
    the fax run's or the backup's when it was in one. `since`: when the child was started; a lock taken before that is
    another process's that had the same number. Returns whether a lock was removed."""
    if not pid or not os.path.exists(db_path) or os.path.getsize(db_path) == 0:
        return False
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        gone = False
        for key, value in _run_locks(conn):
            held = _held(value)
            if held.get("pid") != pid:
                continue
            if since is not None:
                took = _when(held, "started", "heartbeat")
                if took is None or took < since - 5:
                    continue
            conn.execute("DELETE FROM meta WHERE key=? AND value=?", (key, value))
            gone = True
        conn.commit()
        return gone
    finally:
        conn.close()


def _boot_time() -> float:
    """When this machine started, in seconds since 1970 (Linux)."""
    with open("/proc/stat") as f:
        return float(next(int(ln.split()[1]) for ln in f if ln.startswith("btime")))


def _began(pid) -> float | None:
    """When the process with this number started, if one runs here and it is RemitWatch's; None otherwise."""
    try:
        with open(f"/proc/{int(pid)}/cmdline", "rb") as f:
            if b"remitwatch" not in f.read():
                return None
        with open(f"/proc/{int(pid)}/stat") as f:
            ticks = int(f.read().rsplit(")", 1)[1].split()[19])
        return _boot_time() + ticks / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, TypeError, IndexError, StopIteration):
        return None


def holder_running(held: dict) -> bool:
    """Whether the run holding a lock still goes, judged as the deploy script judges it (deploy/auto-deploy.sh, LOCKS_PY):
    its process runs here, is RemitWatch's, and started no later than the lock was taken (a process that started after it
    only got the same number). A lock with no process number counts while its heartbeat is under 15 minutes old."""
    if held.get("pid"):
        start, took = _began(held["pid"]), _when(held, "started", "heartbeat")
        return start is not None and (took is None or start <= took + 5)
    beat = _when(held, "heartbeat", "started")
    return beat is not None and time.time() - beat < 900


def clear_locks_left_behind(db_path: str) -> list[str]:
    """At the server's start: remove the locks of runs that ended with the server's last container (a restart, `up -d`
    after a change to .env, a reboot, AWS's own recovery). Without this the next cycle is refused for hours
    (LOCK_STALE_MINUTES), and the fax run and the backup wait too. A lock with a process number is left behind when:
      - it was taken in this container (or names none), and its process is gone (holder_running, the deploy's rule);
      - it was taken in an earlier container of the server (each start records its own, SERVERS_KEY): that container
        ended before this one started;
      - it was taken in another container (`docker compose run`), which cannot be seen from here, only when the machine
        has started since it was last refreshed.
    A lock with no process number goes stale by its own rule. Records this container. Nothing is done where processes
    cannot be looked at (no /proc), or when there is no store yet (none is made). Returns the keys removed."""
    if not os.path.exists("/proc/self/stat") or not os.path.exists(db_path) or os.path.getsize(db_path) == 0:
        return []
    here = _host()
    try:
        boot = _boot_time()
    except (OSError, ValueError, StopIteration):
        boot = None
    out: list[str] = []
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        try:
            servers = [str(h) for h in json.loads(_meta_get(conn, SERVERS_KEY) or "[]") if h]
        except (sqlite3.Error, TypeError, ValueError):
            servers = []
        for key, value in _run_locks(conn):
            held = _held(value)
            if not held.get("pid"):
                continue
            host = str(held.get("host") or "")
            if not host or host == here:
                gone = not holder_running(held)
            elif host in servers:
                gone = True
            else:
                beat = _when(held, "heartbeat", "started")
                gone = boot is not None and beat is not None and beat < boot - BOOT_MARGIN_SECONDS
            if gone:
                conn.execute("DELETE FROM meta WHERE key=? AND value=?", (key, value))
                out.append(key)
        if here:
            try:
                _meta_set(conn, SERVERS_KEY, [h for h in servers if h != here][-(SERVERS_KEPT - 1):] + [here])
            except sqlite3.Error:
                pass            # a store with no meta table yet holds no lock either
        conn.commit()
    finally:
        conn.close()
    return out


def _ema_step(out: dict, key: str, fn, *a, **kw):
    """One EMA step of the cycle: its failure is noted under `key` and the cycle goes on (the reports must never stop the
    remittances, the analysis or the faxes). Never an exception's message: a converter's message can hold a cell."""
    try:
        return fn(*a, **kw)
    except Exception as exc:       # noqa: BLE001
        out[key] = {"error": type(exc).__name__}
        return None


def reread_waiting(store, *, ledger=None, data_dir: str | None = None, log=print) -> dict:
    """Read again what the report catalog keeps waiting (the switch did not read it, Production waited for the ledger, a
    misread export was queued by the clean-up) and the held files a person chose to apply, from their kept copy on this
    server: an upload's copy, a file of the Dropbox mirror, or (for an entry brought from the serverless site) the
    mirror's copy of its Dropbox path. A copy that is gone closes the entry; an entry whose Dropbox file is not in the
    mirror yet waits for the sync that brings it."""
    from . import ema_reports as er
    from .dropbox_sync import local_path
    conn = store.conn
    data_dir = data_dir or os.environ.get("REMITWATCH_DATA_DIR", "data/dropbox")
    out = {"read": 0, "applied": 0, "gone": 0, "left": 0}
    held = []
    try:
        cur = conn.execute("SELECT file_id FROM ema_report_files WHERE status='held' AND decision='apply'")
        held_ids = [r[0] for r in cur.fetchall()]
    except Exception:       # noqa: BLE001
        held_ids = []
    for fid in held_ids:
        row = conn.execute("SELECT * FROM ema_report_files WHERE file_id=?", (fid,))
        names = [d[0] for d in row.description]
        r = row.fetchone()
        if r:
            d = dict(zip(names, r))
            try:
                d["origin"] = json.loads(d.get("origin") or "{}")
            except ValueError:
                d["origin"] = {}
            held.append(d)
    seen = set()
    for w in er.waiting(conn) + held:
        if w["file_id"] in seen or not er.reads(w.get("report") or ""):
            continue
        seen.add(w["file_id"])
        o = w.get("origin") or {}
        path = None
        if o.get("dropbox_id"):         # kept by the serverless site (import-cloud): its path is a Dropbox path
            mirror = local_path(data_dir, {"path_display": o.get("path") or "", "name": w.get("source_name") or "file"}) if o.get("path") else None
            if mirror and os.path.isfile(mirror):
                path = mirror
            else:
                out["left"] += 1            # brought from the serverless site: the sync brings its file into the mirror
                continue
        elif o.get("path"):
            path = o["path"]
            if not os.path.isfile(path):
                er.supersede_waiting(conn, w["file_id"], reason="The file is no longer in the folder.")
                out["gone"] += 1
                continue
        else:
            out["left"] += 1
            continue
        res = er.ingest_file(store, path, via=w.get("via") or "mirror", origin=o, ledger=ledger, log=log)
        out["read"] += 1
        members = res.get("members") if res.get("status") == "zip" else [res]
        if any(m.get("status") in ("current", "superseded") for m in members or []):
            out["applied"] += 1
            if str(w["file_id"]).startswith("dropbox:"):        # a raw CSV the serverless site recognised from its first bytes
                er.supersede_waiting(conn, w["file_id"], reason="Read on the server.", replaced_by=(members[0] or {}).get("file_id"))
        if res.get("status") == "not_report":
            er.confirm_misread(store, w["file_id"], None)       # a genuine export after all: its queued entry goes
    return out


def turnaround_is_current(conn) -> bool:
    """Whether meta ema:turnaround already holds the answer times read from the posting ledger as it is now."""
    from . import ema_checks as ec
    try:
        stored = json.loads(_meta_get(conn, getattr(ec, "TURNAROUND_KEY", "ema:turnaround")) or "{}")
    except (TypeError, ValueError):
        return False
    rev = ec._ledger_rev(conn) if hasattr(ec, "_ledger_rev") else None
    return isinstance(stored, dict) and stored.get("charge_basis") == "ledger" and rev is not None and stored.get("ledger_rev") == rev


def ledger_turnaround(store, lg) -> dict | None:
    """ema_checks.turnaround with the posting ledger (every claim's first charge to its first insurance posting), once
    per change of the ledger; None when what is stored is already that. Detection reads meta ema:turnaround."""
    from . import ema_checks as ec
    if lg is None or turnaround_is_current(store.conn):
        return None
    t = time.time()
    groups = ec.turnaround(store.conn, ledger=lg)
    return {"groups": len(groups or {}), "seconds": round(time.time() - t, 1)}


def sweep_uploads(db_path: str) -> dict:
    """The Upload page's leftovers on a server (remitwatch.uploads.ServerUploads.sweep): uploads unfinished for a day,
    records kept past their time, half pairs. Counts by kind; {} when there was nothing (or no Upload page here)."""
    try:
        from .uploads import ServerUploads
    except ImportError:         # a build without the Upload page's pieces
        return {}
    return {k: v for k, v in (ServerUploads(db_path).sweep() or {}).items() if v}


def drop_kept_copies(store, ledger, db_path: str, log=print) -> int:
    """The kept upload copies of Production files whose rows newer files have replaced (REMITWATCH_EMA_REPORT_KEEP newer
    files cover each whole) are deleted; the catalog row stays. Only copies the Upload page made (the intake folder or
    the uploads folder), never a file of the Dropbox mirror."""
    data_dir = os.path.dirname(os.path.abspath(db_path))
    roots = [os.path.join(data_dir, "incoming") + os.sep]
    try:
        from .uploads import upload_dir
        roots.append(os.path.abspath(upload_dir(db_path)) + os.sep)
    except Exception:       # noqa: BLE001
        pass
    n = 0
    for fid in ledger.copies_to_drop():
        r = store.conn.execute("SELECT origin FROM ema_report_files WHERE file_id=?", (fid,)).fetchone()
        try:
            path = (json.loads(r[0]) if r and r[0] else {}).get("path")
        except ValueError:
            path = None
        if path and os.path.isfile(path) and any(os.path.abspath(path).startswith(root) for root in roots):
            try:
                os.remove(path)
                n += 1
            except OSError:
                pass
    if n:
        log(f"  {n} kept upload cop{'y' if n == 1 else 'ies'} of replaced Production files removed")
    return n


def run_cycle(db_path: str, *, sync: bool = True, full: bool = False, local: str | None = None, force_detect: bool = False,
              report: bool | None = None, email: bool = True, report_dir: str = "data/reports", log=print) -> dict:
    """Run one cycle. `report=None` sends the summary report when the last one is at least REMITWATCH_REPORT_EVERY_DAYS old.

    EMA's reports (remitwatch.ema_reports, ema_ledger) take part on the way: the once-per-host clean-up of report rows an
    older build read as exports, the uploads handed to the intake folder (remitwatch.intake), the report files the
    Dropbox position passed before this build (once), every Production file of the cycle applied to the posting ledger
    (opened once, only when one comes), the reports waiting to be read again, and the ledger's summaries refreshed
    before detection (and once more after it when detection kept new lines)."""
    from . import ema_ledger, ema_reports, intake
    from .alerts import notify, run_alerts
    from .detect import run_detection
    from .dropbox_sync import ingest_new_files, refind_reports
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

    lg_box: list = []

    def ledger():                       # the posting ledger, opened once per cycle and only when a Production file comes
        if not lg_box:
            lg_box.append(ema_ledger.open_for_store(db_path))
        return lg_box[0]
    try:
        # uploads cut off part-way (their pieces and records, after a day) go at every cycle, not only when someone opens
        # the Upload page: a cut-off report can hold patient data and up to the upload cap
        swept = _ema_step(out, "uploads_sweep", sweep_uploads, db_path)
        if swept:
            out["uploads_swept"] = swept
        mig = _ema_step(out, "ema_migrate", ema_reports.migrate_misread, store, serverless=False, log=beat_log)
        if mig and not mig.get("skipped") and mig.get("candidates"):
            out["ema_migrate"] = {k: v for k, v in mig.items() if isinstance(v, int)}
        # uploads the Upload page handed to the intake folder: read first, in the order they arrived
        up_in = _ema_step(out, "intake", intake.process_ready, store, ledger=ledger, log=beat_log) or {}
        if up_in.get("pairs"):
            out["intake"] = {k: v for k, v in up_in.items() if k != "results"}
        # read uploads kept past their time go (never a held, waiting or live report's copy, nor each report's newest)
        kept = _ema_step(out, "intake_retain", intake.retain_done_server, store, log=beat_log) or {}
        if kept.get("removed"):
            out["intake_retain"] = kept
        new_files: list[str] = []
        refound: list[str] = []
        if sync:
            if dropbox_configured():
                # a Dropbox failure (a revoked token, an outage) must not stop TriZetto, the uploads, the analysis, the
                # backup or the faxes: it is noted, and shown as the cycle's error once the rest is done
                try:
                    st = dropbox_sync(store, full=full, log=beat_log)
                    new_files = st.pop("new_files", [])
                    out["sync"] = st
                except Exception as exc:
                    new_files = list(getattr(exc, "new_files", None) or [])     # what it fetched before it failed
                    out["sync"] = {"error": f"{type(exc).__name__}: {str(exc)[:300]}"}
                    try:
                        store.conn.rollback()
                    except Exception:
                        pass
                if "error" not in out["sync"]:
                    refound = _ema_step(out, "ema_refind", refind_reports, store, log=beat_log) or []
            else:
                out["sync"] = {"skipped": "Dropbox credentials are not configured"}
        if local:
            new_files += [os.path.join(dp, f) for dp, _d, fs in os.walk(local) for f in fs]
        new_files += [p for p in refound if p not in set(new_files)]
        ingested = {"remits": 0, "ema": 0, "claims_837": 0}
        heartbeat(store.conn, token)
        if new_files:
            ingested = ingest_new_files(store, new_files, log=log, ledger=ledger, via="mirror",
                                        origins={p: {"path": p} for p in new_files})
            ingested.pop("remit_stats", None)
            heartbeat(store.conn, token)
        from . import trizetto
        if trizetto.configured():         # the remittances straight from TriZetto's SFTP (remitwatch.trizetto)
            from .cloud import _ingest_batch
            try:
                tz = trizetto.pull(store.conn, lambda items: _ingest_batch(store, items, beat_log), deadline=time.time() + 3000, log=beat_log)
            except Exception as exc:
                tz = {"error": str(exc)[:300]}
            out["trizetto"] = {k: v for k, v in tz.items() if k != "seen_changed"}
            for k in ("remits", "ema", "claims_837"):
                ingested[k] = ingested.get(k, 0) + (tz.get(k) or 0)
            heartbeat(store.conn, token)
        # files uploaded by hand (the Upload page, remitwatch.uploads): the same loader, in the same cycle
        from . import uploads
        try:
            import inspect
            takes_ledger = "ledger" in inspect.signature(uploads.ingest_queued).parameters
            up = uploads.ingest_queued(store, log=beat_log, **({"ledger": ledger} if takes_ledger else {}))
        except Exception as exc:
            up = {"error": f"{type(exc).__name__}: {str(exc)[:300]}"}
        if up.get("uploads") or up.get("error"):
            out["uploads"] = up
            for k in ("remits", "ema", "claims_837"):
                ingested[k] = ingested.get(k, 0) + (up.get(k) or 0)
            heartbeat(store.conn, token)
        # reports waiting to be read again (the switch, the ledger, the clean-up, a held file a person applied)
        rr = _ema_step(out, "ema_waiting", reread_waiting, store, ledger=ledger, log=beat_log) or {}
        if rr.get("read") or rr.get("gone"):
            out["ema_waiting"] = rr
        out["ingest"] = ingested
        changed = (ingested.get("remits", 0) + ingested.get("ema", 0) + ingested.get("claims_837", 0)) > 0
        changed = changed or bool(up_in.get("changed")) or bool(rr.get("applied"))
        # the posting ledger's summaries, before detection reads them (cheap when nothing changed: retention, a catalog
        # check and a number compared)
        if lg_box or os.path.exists(ema_ledger.ledger_path(db_path)):
            r = _ema_step(out, "ema_refresh", ema_ledger.refresh, store, ledger(), log=beat_log)
            if r and (r.get("part_a") or r.get("part_b")):
                changed = True              # new EMA summaries: detection runs
                out["ema_refresh"] = {"part_a": bool(r.get("part_a")), "part_b": bool(r.get("part_b")), "seconds": r.get("seconds")}
            # the payers' answer times from every claim in the ledger (not only the kept lines, which leave out most claims
            # paid cleanly and run long), when the ledger changed since they were last read from it: detection reads them
            tat = _ema_step(out, "ema_turnaround", ledger_turnaround, store, ledger())
            if tat:
                out["ema_turnaround"] = tat
            if lg_box:
                _ema_step(out, "ema_copies", drop_kept_copies, store, lg_box[0], db_path, log=beat_log)
            heartbeat(store.conn, token)
        has_run = bool(store.conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='detection_runs'").fetchone()) and \
            bool(store.conn.execute("SELECT 1 FROM detection_runs LIMIT 1").fetchone())
        rules_changed = False
        if has_run:
            from .detect import DETECT_VERSION
            last = store.conn.execute("SELECT version FROM detection_runs ORDER BY finished_at DESC LIMIT 1").fetchone()
            rules_changed = bool(last) and last[0] != DETECT_VERSION     # a release that changed the rules re-analyses once
        store.close()
        if changed or force_detect or not has_run or rules_changed:
            summary = run_detection(db_path)
            out["detect"] = {"findings": sum(summary.values())}
        store = Store(db_path)
        heartbeat(store.conn, token)
        if "detect" in out and store.conn.execute("SELECT 1 FROM meta WHERE key=?", (ema_ledger.META_KEEP_DIRTY,)).fetchone() \
                and (lg_box or os.path.exists(ema_ledger.ledger_path(db_path))):
            # detection gave new bills an item: their EMA lines are kept now rather than at the next Production file (X10)
            r = _ema_step(out, "ema_refresh_after", ema_ledger.refresh, store, ledger(), log=beat_log)
            if r and r.get("part_a"):
                out["ema_refresh_after"] = {"seconds": r.get("seconds")}
            heartbeat(store.conn, token)
        if "detect" not in out:         # detection rebuilds the provider tables itself; this covers an upgrade with no new files
            from . import activity
            if activity.stale(store.conn):
                out["activity"] = activity.refresh(store, log=log)
                heartbeat(store.conn, token)
        if "detect" in out:             # new items can be the visits of uploaded notes not matched yet
            try:
                matched = uploads.rematch(store.conn)
                if matched:
                    out["notes_matched"] = matched
            except Exception as exc:
                out["notes_matched"] = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
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
        if isinstance(out.get("sync"), dict) and out["sync"].get("error"):
            out["error"] = "Dropbox: " + out["sync"]["error"]          # the rest ran; the feed's failure still shows
        out["finished"] = _now()
        return out
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
        out["finished"] = _now()
        raise
    finally:
        for lg in lg_box:
            try:
                lg.close()
            except Exception:
                pass
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
        self._children: dict[int, tuple[subprocess.Popen, float, str]] = {}   # running now: pid -> (process, started, store)
        self._guard = threading.Lock()

    def is_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def argv(self, db_path: str) -> list[str]:
        """The command one cycle runs as, in its own process."""
        return [sys.executable, "-m", "remitwatch.cli", "--db", db_path, "cycle"]

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
        """The server stops: no more cycles, and a cycle still running is ended (asked to stop, killed after
        STOP_WAIT_SECONDS). The container would end it anyway, and a cycle ended that way cannot release its lock: its
        locks are released here, so the next start's cycles are not refused for hours."""
        self._stop.set()
        self._wake.set()
        with self._guard:
            running = list(self._children.values())
        for proc, started, db_path in running:
            _end_child(proc)
            _release_child(db_path, proc.pid, started)
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
        db_path, started = self.db_path, time.time()
        try:
            proc = subprocess.Popen(self.argv(db_path), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            return -1
        with self._guard:
            self._children[proc.pid] = (proc, started, db_path)
        try:
            return proc.wait(timeout=CHILD_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            return -9
        finally:
            with self._guard:
                self._children.pop(proc.pid, None)
            # a child that ended without releasing its locks (killed: the time limit above, the system's memory limit, a
            # stop) left them behind: the cycle's, and the fax run's or the backup's when it was in one
            _release_child(db_path, proc.pid, started)


def _end_child(proc: subprocess.Popen) -> None:
    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=STOP_WAIT_SECONDS)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=STOP_WAIT_SECONDS)
    except Exception:
        pass


def _release_child(db_path: str, pid: int, started: float) -> None:
    try:
        clear_lock_of_pid(db_path, pid, since=started)
    except Exception:
        pass            # a lock that cannot be removed now is removed at the next start, or goes stale


SCHEDULER = Scheduler()


DAILY_MINUTES = 24 * 60


def start_scheduler_from_env(db_path: str) -> bool:
    """The background cycles on a server: every REMITWATCH_SYNC_INTERVAL_MINUTES (60 is a good value), and at least once
    a day whenever a feed is set up (Dropbox or TriZetto) or the fax line is, as Vercel's daily scheduled calls do;
    Dropbox's webhook and the dashboard's Sync now add cycles in between. Each cycle also looks after the fax line
    (cli.cmd_cycle): its follow-ups, retries and the payers' faxes. First, the locks of runs that ended with the server's
    last container are removed (clear_locks_left_behind)."""
    from . import fax, trizetto
    try:
        gone = clear_locks_left_behind(db_path)
        if gone:
            print(f"RemitWatch: removed the lock of a run that ended with the server's last container ({', '.join(gone)}).",
                  file=sys.stderr, flush=True)
    except Exception as exc:     # never in the way of the server's start: a lock left behind still goes stale by itself
        print(f"RemitWatch: the run locks could not be checked at start ({type(exc).__name__}).", file=sys.stderr, flush=True)
    try:
        interval = int(os.environ.get("REMITWATCH_SYNC_INTERVAL_MINUTES", "0") or 0)
    except ValueError:
        interval = 0
    try:
        faxing = bool(fax.configured())
    except Exception:
        faxing = False
    feed = bool(os.environ.get("DROPBOX_APP_SECRET") or dropbox_configured() or trizetto.configured() or faxing)
    if interval <= 0 and not feed:
        return False
    SCHEDULER.start(db_path, interval if interval > 0 else DAILY_MINUTES)
    return True


def run_once_in_background(db_path: str, trigger: str = "button") -> None:
    """Sync now: use the scheduler when it is running, otherwise a one-off thread."""
    if SCHEDULER.is_alive():
        SCHEDULER.poke(trigger)
        return
    SCHEDULER.db_path = db_path
    threading.Thread(target=SCHEDULER.run_child, args=(trigger,), name="remitwatch-cycle", daemon=True).start()
