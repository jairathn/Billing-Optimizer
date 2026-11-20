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

    from . import api
    api.DB_PATH = args.db
    q = api.appeals_ema_queue(limit=args.limit, _="cli")
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
            n["no_number"] += 1
            continue
        try:
            r = api.appeals_send({"finding_id": it["finding_id"]}, "cli")
        except HTTPException as exc:
            r = {"status": "failed", "reason": exc.detail}
        n[r["status"]] += 1
        print(it["finding_id"], r["status"], r.get("code") or "", r.get("reason") or "", flush=True)
        if r.get("code") in ("practice", "no_fax"):        # every other item would stop on the same thing
            break
    print(json.dumps({"appealable": len(q["items"]), **n}))
    return 0 if not n["failed"] else 1


def cmd_fax_cycle(args: argparse.Namespace) -> int:
    from . import api, fax
    api.DB_PATH = args.db
    prov = fax.provider()
    if prov is None:
        print("The fax line is not set up (SRFAX_ACCESS_ID, SRFAX_ACCESS_PWD).")
        return 1
    out = {"refresh": api._fax_refresh(prov, budget_s=3600), "follow_up": api._send_followups(prov, "cli", budget_s=3600)}
    print(json.dumps(out, indent=2, default=str))
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
    mk = sub.add_parser("modmed-key"); mk.add_argument("--rsa", action="store_true", help="an RSA key (RS384) instead of EC P-384 (ES384)")
    mk.set_defaults(fn=cmd_modmed_key)
    sub.add_parser("modmed-check").set_defaults(fn=cmd_modmed_check)
    pa = sub.add_parser("prepare-appeals"); pa.add_argument("--limit", type=int, default=25)
    pa.add_argument("--no-ai", action="store_true", help="attach the note without Claude's review"); pa.set_defaults(fn=cmd_prepare_appeals)
    sa = sub.add_parser("send-appeals"); sa.add_argument("--limit", type=int, default=2000); sa.add_argument("--payer", default=None)
    sa.set_defaults(fn=cmd_send_appeals)
    sub.add_parser("fax-cycle").set_defaults(fn=cmd_fax_cycle)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
