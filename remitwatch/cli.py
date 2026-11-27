"""Command line entry points for RemitWatch.

    python -m remitwatch.cli ingest-835 <dir-or-file>... [--db PATH]
    python -m remitwatch.cli ingest-ema <dir-or-file>... [--db PATH]
    python -m remitwatch.cli detect [--db PATH]
    python -m remitwatch.cli stats [--db PATH]
    python -m remitwatch.cli modmed-key [--rsa]          the key pair for ModMed's FHIR API (remitwatch.modmed)
    python -m remitwatch.cli modmed-check                every step of the ModMed connection
    python -m remitwatch.cli prepare-appeals [--limit N] [--no-ai] [--db PATH]
                                                         build packets from EMA for the next items that need the visit note
    python -m remitwatch.cli send-appeals [--limit N] [--payer NAME] [--db PATH]
                                                         fax every open item a letter settles (Appeal everything appealable)
    python -m remitwatch.cli fax-cycle [--db PATH]       the daily fax run: states, retries, faxes in, follow-ups due
    python -m remitwatch.cli import-cloud [--force] [--drop-waiting]
                                                         move the Vercel site's store and packets to this server, once
    python -m remitwatch.cli compare-cloud               prove it: the Vercel site's state against this server, table by table
    python -m remitwatch.cli backup [--dir D] [--keep N] a consistent copy of the store while the site runs (also made daily)
    python -m remitwatch.cli ema-reports [--status | --summarize | --reread FILE_ID|all | --decide FILE_ID apply|discard
                                          | --retain-force]
                                                         EMA's A/R and Production reports and the posting ledger
    python -m remitwatch.cli diagnose [--days N] [--run RUN_ID] [--out ZIP]
                                                         the diagnostic bundle: run logs, errors, checks (no patient data)
    python -m remitwatch.cli self-audit                  the weekly self-audit now (money with no item, settled items listed)
    python -m remitwatch.cli archive --dry-run|--run [--horizon N]
                                                         older settled claims to the archive (what would move, or move it)
    python -m remitwatch.cli archive-restore CLAIM_ID    an archived bill back in the store, whole
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

DEFAULT_DB = os.environ.get("REMITWATCH_DB", "data/remitwatch.sqlite")


def cmd_ingest_835(args: argparse.Namespace) -> int:
    from .pipeline import owe_analysis
    from .store import Store
    from .x12_835 import iter_835_paths

    store = Store(args.db)
    owe_analysis(store.conn)            # read outside a cycle: the next cycle analyses it, new files or not
    paths = []
    for p in args.paths:
        if os.path.isdir(p):
            paths.extend(iter_835_paths(p))
        else:
            paths.append(p)
    t0 = time.time()

    def progress(stats):
        print(f"  ... {stats['files']} files, {stats['ingested']} ingested, {stats['skipped_seen']} already seen", file=sys.stderr)

    stats = store.ingest_835_paths(paths, progress=progress)
    flagged = store.mark_superseded()
    stats["superseded_marked"] = flagged
    stats["wall_seconds"] = round(time.time() - t0, 1)
    print(json.dumps(stats, indent=1))
    return 0


def cmd_ingest_ema(args: argparse.Namespace) -> int:
    from . import ema_ledger, ema_reports
    from .dropbox_sync import file_sha256
    from .ema_exports import export_created, is_report_file, iter_export_paths, iter_rows
    from .pipeline import owe_analysis
    from .store import Store

    store = Store(args.db)
    owe_analysis(store.conn)            # read outside a cycle: the next cycle analyses it, new files or not
    paths = []
    for p in args.paths:
        if os.path.isdir(p):
            paths.extend(iter_export_paths(p))
        else:
            paths.append(p)
    total = {"files": 0, "skipped_seen": 0, "rows": 0, "inserted": 0, "errors": 0, "reports": 0}
    lg_box: list = []

    def ledger():                       # opened only when a Production report comes
        if not lg_box:
            lg_box.append(ema_ledger.open_for_store(args.db))
        return lg_box[0]
    try:
        for p in sorted(paths):
            if is_report_file(p):
                # EMA's A/R or Production report: its own reader (the export reader below refuses it)
                total["files"] += 1
                total["reports"] += 1
                try:
                    res = ema_reports.ingest_file(store, p, via="cli", origin={"path": os.path.abspath(p)}, ledger=ledger,
                                                  log=lambda *a: print(*a, file=sys.stderr))
                except Exception as exc:        # never the message: a converter's message can hold a cell
                    total["errors"] += 1
                    print(f"  ERROR {os.path.basename(p)}: {type(exc).__name__}", file=sys.stderr)
                    continue
                for m in (res.get("members") if res.get("status") == "zip" else [res]) or []:
                    print(f"  {m.get('report') or '?':12s} {m.get('status'):10s} rows={int(m.get('rows_applied') or 0):7d}  "
                          f"{os.path.basename(p)}", file=sys.stderr)
            else:
                _ingest_export(store, p, total, file_sha256, export_created, iter_rows)
        if lg_box:
            r = ema_ledger.refresh(store, lg_box[0], log=lambda *a: print(*a, file=sys.stderr))
            total["ema_summaries"] = {"part_a": bool(r.get("part_a")), "part_b": bool(r.get("part_b"))}
    finally:
        for lg in lg_box:
            lg.close()
    print(json.dumps(total, indent=1))
    return 0


def _ingest_export(store, p: str, total: dict, file_sha256, export_created, iter_rows) -> None:
    """One of the three weekly exports, read whole as before (a file already read is skipped)."""
    total["files"] += 1
    sha = file_sha256(p)
    if store.file_seen(sha):
        total["skipped_seen"] += 1
        return
    try:
        rows = list(iter_rows(p))
        r = store.ingest_ema_rows(rows)
        fam = rows[0]["family"] if rows else "unknown"
        store.record_file(sha, p, kind=f"ema:{fam}", status="ok", lines=len(rows), duplicates=len(rows) - r["inserted"])
        store.record_export(os.path.basename(p), sha, fam, export_created(p), len(rows))
        total["rows"] += len(rows)
        total["inserted"] += r["inserted"]
        print(f"  {fam:12s} rows={len(rows):6d} new={r['inserted']:6d}  {os.path.basename(p)}", file=sys.stderr)
    except Exception as exc:
        total["errors"] += 1
        store.record_file(sha, p, kind="ema:error", status=f"error: {type(exc).__name__}: {exc}"[:200])
        print(f"  ERROR {os.path.basename(p)}: {exc}", file=sys.stderr)


def cmd_ema_reports(args: argparse.Namespace) -> int:
    """EMA's A/R and Production reports on this server: --status (the catalog, what the reports cover, the posting
    ledger), --summarize (the summaries made again from the ledger, Part A and B), --reread <file id|all> (a Production
    file taken out of the ledger, to be read again from its kept copy by the next cycle), --decide <file id> apply|discard
    (a held report). The last three take the cycle's lock: they wait for no cycle and change nothing while one runs."""
    from . import ema_ledger, ema_reports
    from .pipeline import acquire_lock, release_lock
    from .store import Store
    store = Store(args.db)
    conn = store.conn
    lpath = ema_ledger.ledger_path(args.db)
    try:
        if args.status or not (args.summarize or args.reread or args.decide or args.retain_force):
            cat = ema_reports.catalog(conn)
            files = [{k: f.get(k) for k in ("file_id", "report", "source_name", "status", "as_of", "rows_read", "rows_applied",
                                            "live_rows", "scope", "status_reason")} for f in cat["files"]]
            stats = None
            if os.path.exists(lpath):
                c = __import__("sqlite3").connect(lpath, timeout=60)
                try:
                    n, lo, hi = c.execute("SELECT COUNT(*), MIN(svc), MAX(posted) FROM postings").fetchone()
                    stats = {"rows": n, "svc_lo": lo, "posted_hi": hi, "bytes": os.path.getsize(lpath),
                             "files": c.execute("SELECT COUNT(*) FROM ledger_files").fetchone()[0],
                             "seq": (c.execute("SELECT value FROM ledger_meta WHERE key='seq'").fetchone() or [None])[0]}
                finally:
                    c.close()
            cov = (conn.execute("SELECT value FROM meta WHERE key=?", (ema_ledger.META_MAP_COVERAGE,)).fetchone() or [None])[0]
            print(json.dumps({"files": files, "held": len(cat["held"]), "waiting": cat["waiting"], "switch": cat["switch"],
                              "coverage": cat["coverage"], "ledger": stats, "ledger_path": lpath,
                              "map_coverage": json.loads(cov) if cov else None}, indent=1, default=str))
            return 0
        token = acquire_lock(conn)
        if not token:
            print("A cycle is running. Run this again when it has finished.")
            return 1
        try:
            if args.decide:
                fid, decision = args.decide
                out = ema_reports.decide(conn, fid, decision, "cli")
                print(json.dumps(out, indent=1, default=str))
                return 0 if "error" not in out else 1
            if args.retain_force:
                with ema_ledger.open_for_store(args.db) as lg:
                    held = lg.retain_held()
                    gone = lg.retain(force=True)
                    r = ema_ledger.refresh(store, lg, log=lambda *a: print(*a, file=sys.stderr))
                print(json.dumps({"was_waiting": held, "postings_removed": gone, "summaries": bool(r.get("part_a"))}, default=str))
                return 0
            if args.reread:
                if args.reread == "all":
                    if os.path.exists(lpath):
                        aside = f"{lpath}.aside-{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}"
                        for suffix in ("-wal", "-shm"):
                            if os.path.exists(lpath + suffix):
                                os.remove(lpath + suffix)
                        os.replace(lpath, aside)
                        print(f"The ledger was moved aside to {aside}.")
                    with ema_ledger.open_for_store(args.db) as lg:
                        n = ema_ledger.sync_catalog(store, lg)
                    print(json.dumps({"marked_to_read_again": n}))
                    return 0
                with ema_ledger.open_for_store(args.db) as lg:
                    gone = lg.forget(args.reread)
                    ema_ledger.sync_catalog(store, lg)
                row = conn.execute("SELECT status FROM ema_report_files WHERE file_id=?", (args.reread,)).fetchone()
                print(json.dumps({"postings_removed": gone, "status": row[0] if row else None}))
                return 0
            with ema_ledger.open_for_store(args.db) as lg:
                r = ema_ledger.refresh(store, lg, parts="AB", log=lambda *a: print(*a, file=sys.stderr))
            print(json.dumps({k: r.get(k) for k in ("published", "retained", "seconds")}, indent=1, default=str))
            return 0
        finally:
            release_lock(conn, token)
    finally:
        store.close()


def cmd_link(args: argparse.Namespace) -> int:
    from .link import build_links
    from .store import Store

    build_links(Store(args.db))
    return 0


def cmd_detect(args: argparse.Namespace) -> int:
    from .detect import run_detection

    t0 = time.time()
    summary = run_detection(args.db)
    print(json.dumps({"findings_by_signal": summary, "seconds": round(time.time() - t0, 1)}, indent=1))
    return 0


def cmd_activity(args: argparse.Namespace) -> int:
    """Rebuild the provider tables (what each clinician did) without re-running detection."""
    from .activity import refresh
    from .store import Store
    store = Store(args.db)
    try:
        out = refresh(store, log=lambda *_a: None)
    finally:
        store.close()
    print(json.dumps(out, indent=1))
    return 1 if "error" in out else 0


def cmd_oauth(args: argparse.Namespace) -> int:
    from .dropbox_sync import DropboxClient

    key = os.environ.get("DROPBOX_APP_KEY") or input("Dropbox app key: ").strip()
    secret = os.environ.get("DROPBOX_APP_SECRET") or input("Dropbox app secret: ").strip()
    print("1. Open this link, approve access, and copy the code:")
    print("   " + DropboxClient.authorize_url(key))
    code = input("2. Paste the code here: ").strip()
    tok = DropboxClient.exchange_code(key, secret, code)
    print("3. Put this in the environment of the scheduled job (keep it secret):")
    print("   DROPBOX_REFRESH_TOKEN=" + tok.get("refresh_token", "<missing: the app must request offline access>"))
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    from .dropbox_sync import ingest_new_files, sync
    from .pipeline import owe_analysis
    from .store import Store

    store = Store(args.db)
    if not args.no_ingest:
        owe_analysis(store.conn)        # read outside a cycle: the next cycle analyses it, new files or not
    try:
        stats = sync(store, full=args.full)
    except Exception as exc:
        # a sync that failed part-way hands back what it downloaded before the failure (exc.new_files): read it now, or the
        # mirror keeps those files and every later sync calls them unchanged, so they would never be read
        got = list(getattr(exc, "new_files", None) or [])
        if got and not args.no_ingest:
            print(f"The sync stopped part-way; reading the {len(got)} file(s) it downloaded first.", file=sys.stderr)
            print(json.dumps(ingest_new_files(store, got), indent=1, default=str))
        raise
    print(json.dumps({k: v for k, v in stats.items() if k != "new_files"}, indent=1))
    if stats["new_files"] and not args.no_ingest:
        print(json.dumps(ingest_new_files(store, stats["new_files"]), indent=1, default=str))
    return 0


def cmd_cycle(args: argparse.Namespace) -> int:
    """One hands-off cycle: sync Dropbox -> ingest -> detect -> alerts -> notify (-> report when due)."""
    from .pipeline import run_cycle

    report_dir = args.report_dir or os.path.join(os.path.dirname(os.path.abspath(args.db)), "reports")
    out = run_cycle(args.db, sync=not args.skip_sync, full=args.full, local=args.local, force_detect=args.force_detect,
                    report=True if args.report else None, email=not args.no_email, report_dir=report_dir,
                    log=lambda *a: print(*a, file=sys.stderr))
    # a copy of the store once a day, kept beside it (REMITWATCH_BACKUP_KEEP of them; 0 turns it off): a mistake or a
    # damaged file is undone from it; the disk's own snapshots (docs/SERVER_RUNBOOK.md) cover losing the disk
    if "skipped" not in out and int(os.environ.get("REMITWATCH_BACKUP_KEEP", "7") or 0) > 0:
        try:
            out["backup"] = _daily_backup(args.db)
        except Exception as exc:
            out["backup"] = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    # on a server the fax line is looked after with every cycle (on Vercel it has its own daily call): the state of what
    # went out, the busy lines tried again, the payers' faxes read, and the follow-ups that are due (each a week apart)
    from . import fax
    if fax.configured() and "skipped" not in out:
        try:
            out["fax"] = _fax_run(args.db, budget_s=1500)
        except Exception as exc:
            out["fax"] = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    print(json.dumps(out, indent=1, default=str))
    return 0 if "error" not in out else 1


# One copy of the store at a time, under a lock kept as the cycle's is (a row of the store's meta table; the deploy waits
# for every row whose key ends in ":lock" while its holder runs, so a swap of the container never cuts a copy short).
BACKUP_LOCK = "backup:lock"
BACKUP_LOCK_STALE_MINUTES = 60          # a lock nobody released for this long was left by a copy that was cut short
# what a copy cut short leaves in the backups folder (the raw copy, its journal, a gzip not finished): never a backup
_BACKUP_LEFTOVER = re.compile(r"^(?:remitwatch|ema_ledger)-\d{8}-\d{6}\.sqlite(?:\.part|\.gz\.part)?(?:-journal|-wal|-shm)?$")


class BackupBusy(Exception):
    """Another copy of the store is being made."""


def _take_backup_lock(db: str) -> str:
    import secrets
    import sqlite3
    from datetime import datetime, timedelta, timezone
    c = sqlite3.connect(db, timeout=60)
    try:
        c.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT value FROM meta WHERE key=?", (BACKUP_LOCK,)).fetchone()
        if row:
            try:
                held = json.loads(row[0])
                fresh = datetime.now(timezone.utc) - datetime.fromisoformat(held.get("heartbeat") or held["started"]) \
                    < timedelta(minutes=BACKUP_LOCK_STALE_MINUTES)
            except Exception:
                held, fresh = {}, False
            if fresh:
                c.execute("ROLLBACK")
                raise BackupBusy(f"Another copy of the store is being made (since {held.get('started')} UTC).")
        token, now = secrets.token_hex(8), datetime.now(timezone.utc).isoformat(timespec="seconds")
        from .pipeline import _host         # the container it runs in, as the cycle's lock says it
        c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                  (BACKUP_LOCK, json.dumps({"token": token, "started": now, "heartbeat": now, "pid": os.getpid(), "who": "backup",
                                            "host": _host()})))
        c.execute("COMMIT")
        return token
    finally:
        c.close()


def _drop_backup_lock(db: str, token: str) -> None:
    import sqlite3
    c = sqlite3.connect(db, timeout=60)
    try:
        c.execute("DELETE FROM meta WHERE key=? AND value LIKE ?", (BACKUP_LOCK, f'%"token": "{token}"%'))
        c.commit()
    finally:
        c.close()


def backup_store(db: str, dest_dir: str | None = None, keep: int = 7) -> str:
    """A consistent copy of the store, taken while the site runs (SQLite's online backup reads one snapshot), gzipped
    into a backups folder beside it; the oldest beyond `keep` are removed. Returns the file written. The copy is written
    under a .part name and renamed only once it is whole, so a copy cut short (a deploy, a reboot) is never taken for a
    backup; what one left behind is removed by the next. One copy at a time: BackupBusy when another is being made."""
    import gzip
    import shutil
    import sqlite3
    from .store import store_problem
    why = store_problem(db)
    if why:                 # a copy of an empty file made in its place would push the good copies out, one a day
        raise RuntimeError(why)
    dest_dir = dest_dir or os.environ.get("REMITWATCH_BACKUP_DIR") or os.path.join(os.path.dirname(os.path.abspath(db)), "backups")
    os.makedirs(dest_dir, mode=0o700, exist_ok=True)
    os.chmod(dest_dir, 0o700)             # copies of patient data: the owner only
    token = _take_backup_lock(db)
    try:
        for f in os.listdir(dest_dir):    # no other copy is being made now (the lock), so these were cut short
            if _BACKUP_LEFTOVER.match(f):
                os.remove(os.path.join(dest_dir, f))
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        final = os.path.join(dest_dir, f"remitwatch-{stamp}.sqlite.gz")
        raw, part = final[:-3] + ".part", final + ".part"
        src, dst = sqlite3.connect(db, timeout=60), sqlite3.connect(raw)
        try:
            src.backup(dst)
            dst.execute("DELETE FROM meta WHERE key=?", (BACKUP_LOCK,))     # the copy's own lock is not part of it
            dst.commit()
        finally:
            dst.close()
            src.close()
        fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with open(raw, "rb") as fi, gzip.open(os.fdopen(fd, "wb"), "wb", compresslevel=6) as fo:
            shutil.copyfileobj(fi, fo, 8 << 20)
        os.replace(part, final)
        os.remove(raw)
        _backup_ledger(db, dest_dir, stamp)
        for prefix in ("remitwatch-", "ema_ledger-"):
            old = sorted(f for f in os.listdir(dest_dir) if f.startswith(prefix) and f.endswith(".sqlite.gz"))
            for f in old[:-keep] if keep > 0 else []:
                os.remove(os.path.join(dest_dir, f))
        return final
    finally:
        _drop_backup_lock(db, token)


def _backup_ledger(db: str, dest_dir: str, stamp: str) -> str | None:
    """The posting ledger of EMA's Production report (remitwatch.ema_ledger), copied beside the store's copy with
    SQLite's online backup and gzipped the same way; nothing when this server has no ledger. Opened as a plain reader:
    never through Ledger.open, which tidies files a cycle that is running may be using."""
    import gzip
    import shutil
    import sqlite3

    from .ema_ledger import ledger_path
    src_path = ledger_path(db)
    if not os.path.exists(src_path):
        return None
    final = os.path.join(dest_dir, f"ema_ledger-{stamp}.sqlite.gz")
    raw, part = final[:-3] + ".part", final + ".part"
    src, dst = sqlite3.connect(src_path, timeout=60), sqlite3.connect(raw)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with open(raw, "rb") as fi, gzip.open(os.fdopen(fd, "wb"), "wb", compresslevel=6) as fo:
        shutil.copyfileobj(fi, fo, 8 << 20)
    os.replace(part, final)
    os.remove(raw)
    return final


def _daily_backup(db: str) -> dict:
    """The day's copy, made by the first cycle of the day (the time of the last one is kept in the store)."""
    import sqlite3
    today = time.strftime("%Y-%m-%d", time.gmtime())
    c = sqlite3.connect(db, timeout=60)
    try:
        last = (c.execute("SELECT value FROM meta WHERE key='backup:last'").fetchone() or [""])[0]
    finally:
        c.close()
    if last[:10] == today:
        return {"skipped": f"made today ({last})"}
    try:
        path = backup_store(db, keep=int(os.environ.get("REMITWATCH_BACKUP_KEEP", "7") or 7))
    except BackupBusy as exc:
        return {"skipped": str(exc)}
    c = sqlite3.connect(db, timeout=60)
    try:
        c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('backup:last', ?)", (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),))
        c.commit()
    finally:
        c.close()
    return {"file": path, "mb": round(os.path.getsize(path) / 1048576, 1)}


def cmd_accept_store(args: argparse.Namespace) -> int:
    """After a run stopped because the store was missing or not the one last saved (docs/OPERATIONS.md, Recovering the
    store): a person says the store now in place is the store. On a server, the store file (--db); with --state-folder,
    the serverless site's store image in its state folder (the Dropbox settings, or REMITWATCH_STATE_DIR)."""
    if args.state_folder:
        from . import cloud
        from .state import state_from_env
        st = state_from_env()
        if st is None:
            print("No state folder is configured: set the site's DROPBOX_APP_KEY, DROPBOX_APP_SECRET and DROPBOX_REFRESH_TOKEN "
                  "(and REMITWATCH_STATE_PATH if the site sets it).")
            return 1
        try:
            print(cloud.accept_image(st))
        except cloud.StoreImageProblem as exc:
            print(exc)
            return 1
        return 0
    from .store import accept_store
    print(accept_store(args.db))
    return 0


def cmd_backup(args: argparse.Namespace) -> int:
    try:
        path = backup_store(args.db, args.dir, args.keep)
    except BackupBusy as exc:
        print(f"{exc} Run this again when it has finished.")
        return 1
    except RuntimeError as exc:         # the store is missing (remitwatch.store.store_problem): nothing to copy
        print(exc)
        return 1
    print(json.dumps({"file": path, "mb": round(os.path.getsize(path) / 1048576, 1)}))
    return 0


def _fax_run(db: str, budget_s: float = 3600) -> dict:
    """The fax line looked after (states, retries, faxes in, follow-ups due), under the fax run's lock (api.fax_run): a run
    already going (the button, the daily call, a cycle started by hand) is left to finish, and nothing is sent twice. The
    deploy script waits for that lock as for the cycle's (meta key fax:lock)."""
    from . import api, fax
    api.DB_PATH = db
    prov = fax.provider()
    if prov is None:
        return {"skipped": fax.not_here() or "The fax line is not set up."}
    # the fax line's reads (status, inbox, fetch, check, find_sent) are tried again after a dropped connection
    # (remitwatch.repair); a send is never wrapped: it may have gone out
    from . import repair
    prov = repair.resilient(prov, "srfax", only=("status", "inbox", "fetch", "check", "find_sent"))
    try:
        with api.fax_run("scheduled run") as lock:
            return {"refresh": api._fax_refresh(prov, budget_s=budget_s / 2, lock=lock),
                    "follow_up": api._send_followups(prov, "scheduled run", budget_s=budget_s / 2, lock=lock)}
    except api.FaxBusy as exc:
        return {"skipped": str(exc)}


def cmd_weekly(args: argparse.Namespace) -> int:
    """The cycle with the summary report forced (kept for existing cron entries)."""
    args.report, args.force_detect = True, True
    return cmd_cycle(args)


def cmd_alerts(args: argparse.Namespace) -> int:
    from .alerts import notify, run_alerts
    from .store import Store

    from .runtime import practice_today
    store = Store(args.db)
    out = run_alerts(store.conn, today=practice_today())
    if args.notify:
        out["notified"] = notify(store.conn)
    rows = store.query("SELECT kind, severity, title FROM alerts WHERE status != 'cleared' ORDER BY CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END")
    print(json.dumps({"summary": out, "active": [dict(r) for r in rows]}, indent=1, default=str))
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    os.environ["REMITWATCH_DB"] = args.db
    uvicorn.run("remitwatch.api:app", host=args.host, port=args.port, reload=False, workers=1)     # one worker: the scheduler lives in it
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    from .store import Store

    store = Store(args.db)
    out = {}
    for t in ("files", "transactions", "claims", "lines", "adjustments", "remarks", "plb", "problems", "ema_rows"):
        out[t] = store.query(f"SELECT COUNT(*) AS n FROM {t}")[0]["n"]
    out["payment_date_range"] = dict(store.query("SELECT MIN(payment_date) AS lo, MAX(payment_date) AS hi FROM transactions")[0])
    out["superseded_transactions"] = store.query("SELECT COUNT(*) AS n FROM transactions WHERE superseded=1")[0]["n"]
    out["ema_families"] = {r["family"]: r["n"] for r in store.query("SELECT family, COUNT(*) AS n FROM ema_rows GROUP BY family")}
    try:
        out["findings"] = {r["signal"]: r["n"] for r in store.query("SELECT signal, COUNT(*) AS n FROM findings GROUP BY signal")}
    except Exception:
        out["findings"] = None
    print(json.dumps(out, indent=1, default=str))
    return 0


def cmd_modmed_key(args: argparse.Namespace) -> int:
    """A new key for ModMed: the private half for the host's settings, the public half for ModMed's registration."""
    from .modmed import generate_key, load_private_key, public_jwk
    pem = generate_key("RSA" if args.rsa else "EC")
    jwk = public_jwk(load_private_key(pem))
    print("PRIVATE KEY: keep it secret. Put all of it, BEGIN and END lines included, in the host's settings as MODMED_PRIVATE_KEY.\n")
    print(pem)
    print("PUBLIC KEY for ModMed's app registration (a JWK set). Once MODMED_PRIVATE_KEY is set, the site also publishes it at")
    print("https://<the site>/.well-known/jwks.json, for a registration that asks for a JWKS URL.\n")
    print(json.dumps({"keys": [jwk]}, indent=2))
    print(f"\nKey id (kid): {jwk['kid']}   algorithm: {jwk['alg']}")
    return 0


def cmd_modmed_check(args: argparse.Namespace) -> int:
    from .modmed import check
    r = check()
    print(json.dumps(r, indent=2))
    return 0 if r["ok"] else 1


def cmd_prepare_appeals(args: argparse.Namespace) -> int:
    """The Worklist's "Build packets from EMA" on a host with a disk (run it nightly from cron): the same routes, called
    in turn, so what this builds is what the button would."""
    from collections import Counter

    from fastapi import HTTPException

    from . import api, modmed
    api.DB_PATH = args.db
    # every argument given, as the button gives them: a route called as a plain function keeps FastAPI's Query(...) objects
    # for the rest, and those count as set (ready would then mean "uploaded notes only" even with EMA connected)
    q = api.appeals_ema_queue(limit=args.limit, ready=0 if modmed.Settings.from_env().configured() else 1, _="cli")
    n: Counter = Counter()
    for it in q["items"]:
        try:
            r = api.appeals_from_ema({"finding_id": it["finding_id"], "skip_if_packet": True, "since": q["since"], "use_ai": not args.no_ai}, "cli")
            n[r["status"]] += 1
            print(it["finding_id"], r["status"], r.get("reason") or "", flush=True)
        except HTTPException as exc:
            n["failed"] += 1
            print(it["finding_id"], "failed", exc.detail, flush=True)
            if exc.status_code in (502, 503):          # ModMed down or not connected: the rest would fail the same way
                break
    print(json.dumps({"queued": len(q["items"]), **n}))
    return 0 if not n["failed"] else 1


def cmd_send_appeals(args: argparse.Namespace) -> int:
    """The Worklist's "Appeal everything appealable" on a host with a disk: the same routes, one item after another."""
    from collections import Counter

    from fastapi import HTTPException

    from . import api
    api.DB_PATH = args.db
    q = api.appeals_appealable(payer=args.payer, limit=args.limit, _="cli")
    n: Counter = Counter()
    for it in q["items"]:
        if not it["ready"]:
            # a payer that files no faxed first-level appeal (filed on its portal or by mail); a group of many insurers (faxed
            # by hand); or no number saved yet
            n["no_fax_route" if it.get("no_fax") else "by_hand" if it.get("by_hand") else "no_number"] += 1
            continue
        try:
            r = api.appeals_send({"finding_id": it["finding_id"]}, "cli")
        except HTTPException as exc:
            r = {"status": "failed", "code": str(exc.status_code)}
        n[r["status"]] += 1
        # the item's id, what happened and its code only: a reason can name the visit's date or repeat what the review said
        # about the note, and this output is a log. The item's panel shows the reason
        print(it["finding_id"], r["status"], r.get("code") or "", flush=True)
        if r.get("code") in ("practice", "no_fax"):        # every other item would stop on the same thing
            break
    print(json.dumps({"appealable": len(q["items"]), **n}))
    return 0 if not n["failed"] else 1


def cmd_fax_cycle(args: argparse.Namespace) -> int:
    from . import fax
    if not fax.configured():
        print(fax.not_here() or "The fax line is not set up (SRFAX_ACCESS_ID, SRFAX_ACCESS_PWD).")
        return 1
    out = _fax_run(args.db, budget_s=7200)
    print(json.dumps(out, indent=2, default=str))
    return 0 if "skipped" not in out else 1


# What a Vercel site keeps in its Dropbox state folder, and what becomes of each on a server (cmd_import_cloud):
#   store.sqlite.gz     the store: every remittance, finding, status, event, fax, payer number, lesson, hidden reason,
#                       provider table and the Dropbox files read                          -> the server's store, as is
#   actions/            the journal: what people did since the site's last run              -> applied to it
#   packets/            appeal packets (letter, review, PDF)                                -> appeal_packets
#   cursor.json         where the site had got to in the Dropbox data folder               -> meta sync:cursor
#   notified.json       which alerts were emailed, when the last weekly report went out    -> alerts.notified_at, meta
#   calculator/assumptions.json   the owner's Practice figures                             -> meta calculator:assumptions
#   reports/            copies of the weekly summaries                                     -> the reports folder
#   serving.sqlite.gz, serving.json, status.json, lock.json: the dashboard's copy, the last run's summary, the lock: made
#   again by the server's own runs, nothing to carry
#   fax-sends/, fax-lock.json   where the Vercel site was the one sending faxes (REMITWATCH_FAX_SENDER=serverless): a
#                       marker per send, written before it, and its fax run's lock. The faxes themselves are in the store
#                       and the journal; the server reserves its own sends in its store: nothing to carry
# what people did and decided, compared row by row (every column both stores have); the rest by row count, since the move
# is a copy of the same SQLite pages and those tables are the remittances themselves
HASHED_TABLES = ("findings", "finding_events", "payer_fax", "faxes", "note_lessons", "hide_rules", "alerts", "detection_runs",
                 "progress_snapshots", "line_handling", "line_links")


def _cloud_image(st, log=print, *, check: bool = True):
    """The state folder's store with the journal applied, the alert-mail record and the Practice figures put in: what the
    Vercel site shows, as one store. Returns (Store, details). An image the site's last run did not save (or a missing
    one) raises cloud.StoreImageProblem; with check=False (a tool that only reads a copy, like compare-cloud) the image is
    read as it is and details['image_problem'] holds the check's sentence (hosts-3)."""
    from . import cloud
    from .api import ASSUMPTIONS, ASSUMPTIONS_KEY
    log(f"Reading the store from {st.describe()}...")
    problem = None
    try:
        store, existed = cloud.load_raw(st)
    except cloud.StoreImageProblem as exc:
        if check:
            raise
        problem = str(exc)
        store, existed = cloud.load_raw(st, check=False)
    if not existed:
        if store is not None:
            store.close()
        return None, ({"image_problem": problem} if problem else {})
    journal = cloud.read_actions(st)
    done, left, _ = cloud.apply_actions(store.conn, journal) if journal else ([], [], False)
    waiting = [(n, a) for n, a in journal if n in set(left)]
    conn = store.conn
    marks = cloud._read_json(st, cloud.MARKS)
    mailed = 0
    for aid, m in (marks.get("alerts") or {}).items():
        if isinstance(m, list) and len(m) == 2:
            mailed += conn.execute("UPDATE alerts SET notified_at=? WHERE alert_id=? AND first_seen=? AND notified_at IS NULL", (m[0], aid, m[1])).rowcount
    if marks.get("last_report"):
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('pipeline:last_report', ?)", (marks["last_report"],))
    figures = st.get(ASSUMPTIONS)
    if figures:
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (ASSUMPTIONS_KEY, figures.decode()))
    conn.commit()
    return store, {"journal_applied": len(done), "journal_waiting": len(left), "waiting": [_waiting_line(n, a) for n, a in waiting],
                   "alerts_marked_mailed": mailed, "last_report": marks.get("last_report"), "practice_figures": bool(figures),
                   **({"image_problem": problem} if problem else {})}


def _waiting_line(name: str, a: dict) -> str:
    """A journal entry still waiting for its item, in one line: when, what, which items (their ids), who. Never the note."""
    what = str(a.get("kind") or "?") + (f" {a.get('status')}" if a.get("status") else "") + (f" {a.get('event')}" if a.get("event") else "")
    ids = [str(i) for i in (a.get("ids") or [a.get("finding_id")]) if i]
    when = str(a.get("at") or name.rsplit("/", 1)[-1][:15])[:19]
    return f"{when}  {what}  {', '.join(ids[:5])}{' and more' if len(ids) > 5 else ''}  by {a.get('actor') or 'someone'}"


def _baseline(conn) -> dict:
    """The numbers to check a move against (the Vercel site's first load: 32,718 remittance files, 17,446 findings)."""
    from .actions import rules_in_force
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    n = lambda sql: conn.execute(sql).fetchone()[0] if all(t in have for t in re.findall(r"FROM (\w+)", sql)) else 0     # noqa: E731
    return {"remittance_files": n("SELECT COUNT(*) FROM files WHERE kind='835'"), "remits": n("SELECT COUNT(*) FROM transactions"),
            "findings": n("SELECT COUNT(*) FROM findings"), "findings_current": n("SELECT COUNT(*) FROM findings WHERE COALESCE(stale,0)=0"),
            "decisions": n("SELECT COUNT(*) FROM finding_events WHERE kind IN ('manual','hidden','unhidden','fax')"),
            "not_open": n("SELECT COUNT(*) FROM findings WHERE status != 'open'"), "payer_fax_numbers": n("SELECT COUNT(*) FROM payer_fax"),
            "faxes": n("SELECT COUNT(*) FROM faxes"), "lessons": n("SELECT COUNT(*) FROM note_lessons"),
            "hidden_reasons": n(f"SELECT COUNT(*) FROM hide_rules WHERE {rules_in_force(conn)}"),     # a removed one stays, dated
            "packets": n("SELECT COUNT(*) FROM appeal_packets")}


def _fingerprint(conn, table: str, cols: list[str] | None) -> tuple[int, str] | None:
    """Rows, and with `cols` a hash of those columns of every row in order: two stores with the same fingerprint hold the
    same table. Without `cols`, the row count alone."""
    import hashlib
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
        return None
    if not cols:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], ""
    h = hashlib.sha256()
    n = 0
    for row in conn.execute(f"SELECT {', '.join(cols)} FROM {table} ORDER BY {', '.join(cols)}"):
        h.update(repr(tuple(row)).encode())
        n += 1
    return n, h.hexdigest()[:16]


EMA_SKIP_COLUMNS = ("mapped_at",)          # when the map was made: each host's own


def _ema_fingerprint(conn, table: str) -> tuple[int, str] | None:
    """Rows of an EMA report table and a hash of the sum of each number column, leaving out the keys and keyed hashes
    (ema_reports.KEY_COLUMNS) and when the map was made: two hosts with the same reports hold the same figures."""
    import hashlib

    from .ema_reports import KEY_COLUMNS
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
        return None
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")
            if r[1] not in KEY_COLUMNS and r[1] not in EMA_SKIP_COLUMNS and (r[2] or "").upper() in ("INTEGER", "REAL")]
    n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    sums = conn.execute(f"SELECT {', '.join(f'TOTAL({c})' for c in cols)} FROM {table}").fetchone() if cols else ()
    return n, hashlib.sha256(repr((cols, tuple(sums or ()))).encode()).hexdigest()[:16]


def _ledger_sums(c) -> tuple[int, str]:
    import hashlib
    n, paid, charges, adj = c.execute("SELECT COUNT(*), TOTAL(ins_paid), TOTAL(charges), TOTAL(contractual) FROM postings").fetchone()
    files = sorted(tuple(r) for r in c.execute("SELECT file_id, status, live_rows FROM ledger_files"))
    return n, hashlib.sha256(repr((paid, charges, adj, files)).encode()).hexdigest()[:16]


def _ledger_fingerprint_file(db: str) -> tuple[int, str] | None:
    import sqlite3

    from .ema_ledger import ledger_path
    p = ledger_path(db)
    if not os.path.exists(p):
        return None
    c = sqlite3.connect(p, timeout=60)
    try:
        return _ledger_sums(c)
    finally:
        c.close()


def _ledger_fingerprint_state(st) -> tuple[int, str] | None:
    import shutil
    import tempfile

    from . import ema_ledger
    if not st.meta(ema_ledger.STATE_NAME):
        return None
    tmp = tempfile.mkdtemp(prefix="compare-ledger-")
    h = ema_ledger.LedgerHandle(st, tmp_dir=tmp)
    try:
        return _ledger_sums(h.open().conn)
    finally:
        h.discard()
        shutil.rmtree(tmp, ignore_errors=True)


def cmd_import_cloud(args: argparse.Namespace) -> int:
    """Move from the serverless site (Vercel) to this server, once (see the table above): the state folder's store with
    the journal applied, its packets, its Dropbox position, the alert-mail record, the Practice figures and the saved
    reports. Run it with the app stopped. It needs the same Dropbox settings as that site."""
    import sqlite3

    from . import cloud
    from .notes import ensure_packets
    from .state import state_from_env
    from .store import Store
    if os.path.exists(args.db) and os.path.getsize(args.db) > 0:
        c = sqlite3.connect(args.db)
        try:
            has = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            data = "transactions" in has and c.execute("SELECT 1 FROM transactions LIMIT 1").fetchone() is not None
            row = c.execute("SELECT value FROM meta WHERE key='pipeline:lock'").fetchone() if "meta" in has else None
        finally:
            c.close()
        live = False
        if row:         # a run's lock, beaten within the last quarter hour: a run may be writing (one stopped midway leaves an old one)
            from datetime import datetime, timedelta, timezone
            try:
                held = json.loads(row[0])
                live = datetime.now(timezone.utc) - datetime.fromisoformat(held.get("heartbeat") or held["started"]) < timedelta(minutes=15)
            except Exception:
                live = False
        if live and not args.force:
            # never a hint at --force here when the store holds data: that would replace the server's own work
            print("A run is going on this server's store, and the server already keeps its own data. The move from Vercel is "
                  "only for the first deploy and the cutover (docs/SERVER_RUNBOOK.md); for those, stop the app first "
                  "(sudo docker compose stop app), then run this again." if data else
                  "A run is going on this server's store. Stop the app (sudo docker compose stop app), then run this again. "
                  "A run the stop cut short leaves its lock for up to 15 minutes.")
            return 1
        if data and not args.force:
            print(f"There is already a store with data at {args.db}. Add --force to replace it with the one from the state folder.")
            return 1
    st = state_from_env()
    if st is None:
        print("Set DROPBOX_APP_KEY, DROPBOX_APP_SECRET and DROPBOX_REFRESH_TOKEN (the same values as on Vercel), then run this again.")
        return 1
    try:
        store, details = _cloud_image(st, log=lambda *a: print(*a, flush=True))
    except cloud.StoreImageProblem as exc:
        # the image is not the one the site's last run saved (or it is missing, or cut short): nothing is moved, and the
        # check's own sentence says what to do (hosts-3)
        print(exc)
        return 1
    if store is None:
        print("The state folder has no store yet: nothing to move. Start this server with an empty store instead.")
        return 1
    waiting = details.pop("waiting", [])
    if waiting and not args.drop_waiting:
        # what people did on the Vercel site for an item its store does not hold yet: the site keeps trying these for a week,
        # but the server would never see them, so the move stops rather than lose them without a word
        store.close()
        print(f"{len(waiting)} thing{'s' if len(waiting) != 1 else ''} people did on the Vercel site still wait{'s' if len(waiting) == 1 else ''} "
              "for an item its store does not hold yet, so the move would leave them behind:")
        for line in waiting:
            print("  " + line)
        print("The Vercel site's next run usually adds those items: press Sync and analyse now there, let the run finish, then "
              "run this again. To move without them (they are listed above, to be done again by hand), add --drop-waiting.")
        return 1
    os.makedirs(os.path.dirname(os.path.abspath(args.db)) or ".", exist_ok=True)
    tmp = args.db + ".importing"
    for p in (tmp, tmp + "-wal", tmp + "-shm"):
        if os.path.exists(p):
            os.remove(p)
    dst = sqlite3.connect(tmp)
    store.conn.backup(dst)
    dst.close()
    store.close()
    full = Store(tmp)                    # the server's schema: the indexes the serverless copy leaves out
    conn = full.conn
    cur = st.get(cloud.CURSOR)
    if cur:
        try:
            c = json.loads(cur)
            if c.get("cursor"):
                conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('sync:cursor', ?)", (c["cursor"],))
        except ValueError:
            pass
    ensure_packets(conn)
    packets = 0
    reports_dir = os.path.join(os.path.dirname(os.path.abspath(args.db)), "reports")
    reports = 0
    for m in st.list(""):
        name = m["name"]
        if name.startswith("reports/") and name.endswith(".md"):
            os.makedirs(reports_dir, exist_ok=True)
            with open(os.path.join(reports_dir, os.path.basename(name)), "wb") as fh:
                fh.write(st.get(name) or b"")
            reports += 1
            continue
        if not (name.startswith(cloud.PACKETS + "/") and name.endswith(".json")):
            continue
        try:
            p = json.loads(st.get(name) or b"{}")
        except ValueError:
            continue
        pdf = st.get(name[:-5] + ".pdf")
        if not p.get("packet_id") or not pdf:
            continue
        conn.execute("""INSERT OR REPLACE INTO appeal_packets (packet_id, finding_id, created_at, mode, model, subject, letter, analysis, evidence,
                        attachments, note_name, note_sha256, note_kind, note_blob, pdf, emailed_to, emailed_at, letter_redacted)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (p["packet_id"], p.get("finding_id"), p.get("created_at"), p.get("mode"), p.get("model"), p.get("subject"),
                      p.get("letter_full") or p.get("letter"), json.dumps(p.get("analysis") or {}), p.get("evidence"),
                      json.dumps(p.get("attachments") or []), p.get("note_name"), None, p.get("note_kind"), None, pdf,
                      p.get("emailed_to"), p.get("emailed_at"), p.get("letter_redacted")))
        packets += 1
    conn.commit()
    base = _baseline(conn)
    full.close()
    for p in (args.db + "-wal", args.db + "-shm"):
        if os.path.exists(p):
            os.remove(p)
    os.replace(tmp, args.db)
    from .api import ensure_server_schema
    ensure_server_schema(args.db)           # the dashboard's own tables, where the old store had none yet
    ledger = _import_ledger(st, args.db)
    howto = _import_howto(st, args.db)
    print(json.dumps(dict(details, store=args.db, packets_moved=packets, reports_moved=reports, dropbox_position=bool(cur),
                          journal_left_behind=waiting, posting_ledger=ledger, howto_boxes=howto, **base), indent=1))
    return 0


def _import_howto(st, db: str) -> bool:
    """The Upload page's "How we pull this in our EMA" boxes kept in the state folder become this server's (meta)."""
    import sqlite3

    from . import cloud
    conn = sqlite3.connect(db)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        changed = cloud.copy_howto(st, conn)
        conn.commit()
        return changed
    finally:
        conn.close()


def _import_ledger(st, db: str) -> dict | None:
    """The posting ledger of EMA's Production report, kept gzipped in the state folder (ema_ledger.sqlite.gz), becomes
    this server's ledger beside the store. The imported store keeps its key salt, so the two agree. None when the state
    folder has no ledger."""
    import shutil

    from . import ema_ledger
    if not st.meta(ema_ledger.STATE_NAME):
        return None
    tmp = os.path.join(os.path.dirname(os.path.abspath(db)) or ".", ".import-ledger")
    h = ema_ledger.LedgerHandle(st, tmp_dir=tmp)
    try:
        lg = h.open()
        dest = ema_ledger.ledger_path(db)
        for suffix in ("-wal", "-shm"):
            if os.path.exists(dest + suffix):
                os.remove(dest + suffix)
        lg.backup(dest)
        stats = {"rows": lg.conn.execute("SELECT COUNT(*) FROM postings").fetchone()[0], "files": len(lg.files()), "path": dest}
    finally:
        h.discard()
        shutil.rmtree(tmp, ignore_errors=True)
    return stats


def cmd_compare_cloud(args: argparse.Namespace) -> int:
    """Prove a move: the Vercel site's state (store, journal, alert-mail record, Practice figures, packets) against this
    server's store, table by table, by row count and a hash of every row. Run it right after import-cloud, before the
    server's first run changes anything; at cutover, import again (--force) and compare again."""
    import sqlite3

    from . import cloud
    from .state import state_from_env
    st = state_from_env()
    if st is None or not os.path.exists(args.db):
        print("Needs the Dropbox settings and this server's store.")
        return 1
    try:
        # the image as it is, even one the site's last run did not save: the comparison is still worth reading, and the
        # mismatch is said first (hosts-3)
        store, details = _cloud_image(st, log=lambda *a: print(*a, flush=True), check=False)
    except cloud.StoreImageProblem as exc:      # an image that cannot be read at all (cut short)
        print(exc)
        return 1
    problem = details.get("image_problem")
    if problem:
        print(f"Not a proof of the move: {problem}")
    if store is None:
        print("The state folder has no store.")
        return 1
    mine = sqlite3.connect(args.db)
    theirs = store.conn
    tables = sorted({r[0] for r in theirs.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")})
    rows, bad = [], 0
    for t in tables:
        if t == "meta":
            continue        # the server keeps its own run records there; the keys that matter are checked below
        if t.startswith("ema_") and t != "ema_rows" and t != "ema_sources":
            # EMA's report tables: rows and the sum of every number, keys and keyed hashes left out
            a, b = _ema_fingerprint(theirs, t), _ema_fingerprint(mine, t)
            same = a == b
            bad += not same
            rows.append((t + " (rows and sums)", a[0] if a else None, b[0] if b else None, "same" if same else "DIFFERENT"))
            continue
        cols = None
        if t in HASHED_TABLES:
            mine_cols = {r[1] for r in mine.execute(f"PRAGMA table_info({t})")}
            cols = [r[1] for r in theirs.execute(f"PRAGMA table_info({t})") if r[1] in mine_cols and r[1] != "id"]
        a, b = _fingerprint(theirs, t, cols), _fingerprint(mine, t, cols)
        same = a == b
        bad += not same
        rows.append((t + (" (every row)" if cols else ""), a[0] if a else None, b[0] if b else None, "same" if same else "DIFFERENT"))
    lg_a, lg_b = _ledger_fingerprint_state(st), _ledger_fingerprint_file(args.db)
    if lg_a is not None or lg_b is not None:
        same = lg_a == lg_b
        bad += not same
        rows.append(("posting ledger (rows and sums)", lg_a[0] if lg_a else None, lg_b[0] if lg_b else None,
                     "same" if same else "DIFFERENT"))
    for key in ("calculator:assumptions", "pipeline:last_report"):
        a = (theirs.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone() or [None])[0]
        b = (mine.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone() or [None])[0]
        bad += a != b
        rows.append((f"meta {key}", int(a is not None), int(b is not None), "same" if a == b else "DIFFERENT"))
    waiting = details.get("journal_waiting") or 0     # applied to neither store: the Vercel site still waits for their items
    bad += bool(waiting)
    rows.append(("journal entries still waiting", waiting, 0, "same" if not waiting else "DIFFERENT"))
    want = sum(1 for m in st.list(cloud.PACKETS) if m["name"].endswith(".json"))
    have = mine.execute("SELECT COUNT(*) FROM appeal_packets").fetchone()[0] if mine.execute(
        "SELECT 1 FROM sqlite_master WHERE name='appeal_packets'").fetchone() else 0
    bad += want != have
    rows.append(("appeal packets", want, have, "same" if want == have else "DIFFERENT"))
    width = max(len(r[0]) for r in rows)
    print(f"{'table'.ljust(width)}  {'Vercel':>9}  {'server':>9}")
    for t, a, b, verdict in rows:
        print(f"{t.ljust(width)}  {str(a):>9}  {str(b):>9}  {verdict}")
    print(json.dumps({"different": bad, "vercel": _baseline(theirs), "server": _baseline(mine), "waiting": details.get("waiting") or [],
                      **({"image_problem": problem} if problem else {})}, indent=1))
    mine.close()
    store.close()
    if problem:
        print(f"Not a proof of the move: {problem}")
        return 1
    return 0 if not bad else 2


def cmd_diagnose(args: argparse.Namespace) -> int:
    """The diagnostic bundle (remitwatch.diagnose): run logs, errors, self-checks and repairs, no patient data."""
    from . import diagnose
    return diagnose.main(["--db", args.db, "--days", str(args.days)] + (["--run", args.run] if args.run else [])
                         + (["--out", args.out] if args.out else []))


def cmd_self_audit(args: argparse.Namespace) -> int:
    """The weekly self-audit now (remitwatch.selfcheck): lines with money to recover and no item, and items on Work now
    that look settled. Prints counts and dollars only."""
    from . import selfcheck
    from .pipeline import acquire_lock, release_lock
    from .store import Store
    store = Store(args.db)
    try:
        token = acquire_lock(store.conn)
        if not token:
            print("A cycle is running: try again when it has finished.")
            return 1
        try:
            au = selfcheck.self_audit(store.conn, force=True, why="by hand") or {}
            store.conn.commit()
        finally:
            release_lock(store.conn, token)
        print(json.dumps({k: au.get(k) for k in ("today", "gaps_n", "gaps_cents", "wasted_n", "alert")}, default=str))
        return 0
    finally:
        store.close()


def cmd_archive(args: argparse.Namespace) -> int:
    """Retention (remitwatch.archive): --dry-run says what would move (counts only), --run moves it, under the cycle's
    lock. Older settled claims go to the archive folder beside the store; Find a claim still opens them."""
    from . import archive
    from .pipeline import acquire_lock, release_lock
    from .store import Store
    store = Store(args.db)
    try:
        if not args.run:
            got = archive.plan(store.conn, horizon=args.horizon)
            bills = got.get("bills") or []          # (bill, newest, lines, claims, years): counted, never printed
            print(json.dumps({"cutoff": got.get("cutoff"), "horizon": got.get("horizon"), "bills": len(bills),
                              "claims": sum(int(b[3] or 0) for b in bills), "lines": sum(int(b[2] or 0) for b in bills),
                              "held": got.get("held"), "waiting_rates": got.get("waiting_rates"), "blocked": got.get("blocked")},
                             default=str))
            return 0
        token = acquire_lock(store.conn)
        if not token:
            print("A cycle is running: try again when it has finished.")
            return 1
        try:
            got = archive.run(store.conn, horizon=args.horizon, sink=archive.sink_for(db_path=args.db), mode="server",
                              log=lambda *a: print(*a, flush=True))
        finally:
            release_lock(store.conn, token)
        print(json.dumps({k: got.get(k) for k in ("cutoff", "bills", "claims", "lines", "findings", "files", "errors")}, default=str))
        return 0 if not got.get("errors") else 2
    finally:
        store.close()


def cmd_archive_restore(args: argparse.Namespace) -> int:
    """An archived bill back in the store, whole (by a claim id or the bill), under the cycle's lock."""
    from . import archive
    from .pipeline import acquire_lock, release_lock
    from .store import Store
    store = Store(args.db)
    try:
        token = acquire_lock(store.conn)
        if not token:
            print("A cycle is running: try again when it has finished.")
            return 1
        try:
            got = archive.restore(store.conn, args.claim, sink=archive.sink_for(db_path=args.db), actor="cli", reason=args.reason)
        finally:
            release_lock(store.conn, token)
        print(json.dumps({k: v for k, v in (got or {}).items() if isinstance(v, (int, bool, str)) and k != "bill"}, default=str))
        return 0 if got and not got.get("missing") else 1
    finally:
        store.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="remitwatch")
    ap.add_argument("--db", default=DEFAULT_DB)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("ingest-835"); a.add_argument("paths", nargs="+"); a.set_defaults(fn=cmd_ingest_835)
    b = sub.add_parser("ingest-ema"); b.add_argument("paths", nargs="+"); b.set_defaults(fn=cmd_ingest_ema)
    d = sub.add_parser("detect"); d.set_defaults(fn=cmd_detect)
    lk = sub.add_parser("link"); lk.set_defaults(fn=cmd_link)
    ac = sub.add_parser("activity"); ac.set_defaults(fn=cmd_activity)
    o = sub.add_parser("oauth"); o.set_defaults(fn=cmd_oauth)
    s_ = sub.add_parser("sync"); s_.add_argument("--full", action="store_true"); s_.add_argument("--no-ingest", action="store_true"); s_.set_defaults(fn=cmd_sync)
    for name, fn in (("cycle", cmd_cycle), ("weekly", cmd_weekly)):
        w = sub.add_parser(name); w.add_argument("--full", action="store_true"); w.add_argument("--skip-sync", action="store_true")
        w.add_argument("--local", default=None, help="also ingest every file under this local folder"); w.add_argument("--no-email", action="store_true")
        w.add_argument("--force-detect", action="store_true", help="re-run detection even when no new file arrived")
        w.add_argument("--report", action="store_true", help="send the summary report now instead of waiting until it is due")
        w.add_argument("--report-dir", default=None, help="where the weekly summaries are saved (default: a reports folder beside the store)")
        w.set_defaults(fn=fn)
    al = sub.add_parser("alerts"); al.add_argument("--notify", action="store_true"); al.set_defaults(fn=cmd_alerts)
    er_ = sub.add_parser("ema-reports", help="EMA's A/R and Production reports: the catalog, the posting ledger, held files")
    er_.add_argument("--status", action="store_true", help="the catalog, what the reports cover and the posting ledger (the default)")
    er_.add_argument("--summarize", action="store_true", help="make the summaries again from the posting ledger")
    er_.add_argument("--reread", metavar="FILE_ID|all", default=None, help="read a Production file again (all: every one)")
    er_.add_argument("--decide", nargs=2, metavar=("FILE_ID", "apply|discard"), default=None, help="decide on a held report")
    er_.add_argument("--retain-force", action="store_true",
                     help="let the ledger's retention delete what waits for a person (more than 60%% of the postings at once)")
    er_.set_defaults(fn=cmd_ema_reports)
    v = sub.add_parser("serve"); v.add_argument("--host", default="127.0.0.1"); v.add_argument("--port", type=int, default=8000); v.set_defaults(fn=cmd_serve)
    c = sub.add_parser("stats"); c.set_defaults(fn=cmd_stats)
    mk = sub.add_parser("modmed-key"); mk.add_argument("--rsa", action="store_true", help="an RSA key (RS384) instead of EC P-384 (ES384)")
    mk.set_defaults(fn=cmd_modmed_key)
    sub.add_parser("modmed-check").set_defaults(fn=cmd_modmed_check)
    pa = sub.add_parser("prepare-appeals"); pa.add_argument("--limit", type=int, default=25)
    pa.add_argument("--no-ai", action="store_true", help="attach the note without Claude's review"); pa.set_defaults(fn=cmd_prepare_appeals)
    sa = sub.add_parser("send-appeals"); sa.add_argument("--limit", type=int, default=2000); sa.add_argument("--payer", default=None)
    sa.set_defaults(fn=cmd_send_appeals)
    sub.add_parser("fax-cycle").set_defaults(fn=cmd_fax_cycle)
    ic = sub.add_parser("import-cloud", help="move the store and packets from the Vercel site's Dropbox state folder to this server")
    ic.add_argument("--force", action="store_true", help="replace a store already on this server")
    ic.add_argument("--drop-waiting", action="store_true",
                    help="move even though some of what people did on Vercel still waits for its item (listed; it is left behind)")
    ic.set_defaults(fn=cmd_import_cloud)
    sub.add_parser("compare-cloud", help="prove a move: the Vercel site's state against this server's store, table by table").set_defaults(fn=cmd_compare_cloud)
    acc = sub.add_parser("accept-store", help="after a run stopped for a missing or replaced store: the store now in place is the store")
    acc.add_argument("--state-folder", action="store_true", help="the serverless site's store image, in its state folder")
    acc.set_defaults(fn=cmd_accept_store)
    bk = sub.add_parser("backup", help="a consistent, gzipped copy of the store, taken while the site runs")
    bk.add_argument("--dir", default=None, help="where (default: a backups folder beside the store)")
    bk.add_argument("--keep", type=int, default=7, help="how many copies to keep"); bk.set_defaults(fn=cmd_backup)
    dg = sub.add_parser("diagnose", help="a diagnostic bundle: run logs, errors, self-checks and repairs (no patient data)")
    dg.add_argument("--days", type=int, default=7, help="the runs of the last N days")
    dg.add_argument("--run", default=None, help="only this run id")
    dg.add_argument("--out", default=None, help="where to write the zip (default: a diagnostics folder beside the store)")
    dg.set_defaults(fn=cmd_diagnose)
    sub.add_parser("self-audit", help="the weekly self-audit now: money with no item, and items that look settled").set_defaults(fn=cmd_self_audit)
    arc = sub.add_parser("archive", help="older settled claims to the archive (--dry-run says what would move; --run moves it)")
    mode = arc.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="say what would move, counts only")
    mode.add_argument("--run", action="store_true", help="move it now, under the cycle's lock")
    arc.add_argument("--horizon", type=int, default=None, help="months of service kept in the store (never below 24)")
    arc.set_defaults(fn=cmd_archive)
    ar_ = sub.add_parser("archive-restore", help="an archived bill back in the store, whole")
    ar_.add_argument("claim", help="a claim id of the bill, or the bill"); ar_.add_argument("--reason", default="reopened by hand")
    ar_.set_defaults(fn=cmd_archive_restore)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
