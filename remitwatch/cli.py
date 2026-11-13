"""Command line entry points for RemitWatch.

    python -m remitwatch.cli ingest-835 <dir-or-file>... [--db PATH]
    python -m remitwatch.cli ingest-ema <dir-or-file>... [--db PATH]
    python -m remitwatch.cli detect [--db PATH]
    python -m remitwatch.cli stats [--db PATH]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

DEFAULT_DB = os.environ.get("REMITWATCH_DB", "data/remitwatch.sqlite")


def cmd_ingest_835(args: argparse.Namespace) -> int:
    from .store import Store
    from .x12_835 import iter_835_paths

    store = Store(args.db)
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
    import hashlib

    from .ema_exports import export_created, iter_export_paths, iter_rows
    from .store import Store

    store = Store(args.db)
    paths = []
    for p in args.paths:
        if os.path.isdir(p):
            paths.extend(iter_export_paths(p))
        else:
            paths.append(p)
    total = {"files": 0, "skipped_seen": 0, "rows": 0, "inserted": 0, "errors": 0}
    for p in sorted(paths):
        total["files"] += 1
        with open(p, "rb") as fh:
            sha = hashlib.sha256(fh.read()).hexdigest()
        if store.file_seen(sha):
            total["skipped_seen"] += 1
            continue
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
    print(json.dumps(total, indent=1))
    return 0


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
    from .store import Store

    store = Store(args.db)
    stats = sync(store, full=args.full)
    print(json.dumps({k: v for k, v in stats.items() if k != "new_files"}, indent=1))
    if stats["new_files"] and not args.no_ingest:
        print(json.dumps(ingest_new_files(store, stats["new_files"]), indent=1, default=str))
    return 0


def cmd_cycle(args: argparse.Namespace) -> int:
    """One hands-off cycle: sync Dropbox -> ingest -> detect -> alerts -> notify (-> report when due)."""
    from .pipeline import run_cycle

    out = run_cycle(args.db, sync=not args.skip_sync, full=args.full, local=args.local, force_detect=args.force_detect,
                    report=True if args.report else None, email=not args.no_email, report_dir=args.report_dir,
                    log=lambda *a: print(*a, file=sys.stderr))
    print(json.dumps(out, indent=1, default=str))
    return 0 if "error" not in out else 1


def cmd_weekly(args: argparse.Namespace) -> int:
    """The cycle with the summary report forced (kept for existing cron entries)."""
    args.report, args.force_detect = True, True
    return cmd_cycle(args)


def cmd_alerts(args: argparse.Namespace) -> int:
    from .alerts import notify, run_alerts
    from .store import Store

    store = Store(args.db)
    out = run_alerts(store.conn)
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
        w.add_argument("--report-dir", default="data/reports"); w.set_defaults(fn=fn)
    al = sub.add_parser("alerts"); al.add_argument("--notify", action="store_true"); al.set_defaults(fn=cmd_alerts)
    v = sub.add_parser("serve"); v.add_argument("--host", default="127.0.0.1"); v.add_argument("--port", type=int, default=8000); v.set_defaults(fn=cmd_serve)
    c = sub.add_parser("stats"); c.set_defaults(fn=cmd_stats)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
