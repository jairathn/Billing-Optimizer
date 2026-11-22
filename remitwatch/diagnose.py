"""
The diagnostic bundle: one zip with everything a person, or Claude Code with this repository, needs to trace a problem
to its cause, and nothing that identifies a patient.

    python -m remitwatch.diagnose [--db PATH] [--days N] [--run RUN_ID] [--out FILE]           a server (its store and logs)
    python -m remitwatch.diagnose --state-dir FOLDER [--days N] [--run RUN_ID] [--out FILE]    a copy of Vercel's state folder
    the Data tab's button (Logs and diagnostics): built from the dashboard's copy and the state folder's logs

What is in it (BUNDLE_PARTS):
  README.md            how to read the bundle, in order
  REPRODUCE.md         the exact commands to reproduce on a copy (server and serverless) and to run the tests
  bundle.json          when, from where, which runs, the versions and the code commit, and the scan of the bundle itself
  runs/<run_id>.json   each chosen run's manifest (inputs by hash, steps with time and memory, outputs, errors, repairs)
  events/<run_id>.jsonl  each chosen run's event log
  errors.json          every error of the chosen runs, with its stack and where it happened
  invariants.json      the self-check results run by run (remitwatch.selfcheck) and the weekly self-audit when there is one
  repairs.json         the self-repair attempts and what could not be repaired
  decisions.json       the automatic changes to items in the window, counted by kind and rule
  examples/<check>.json  for each failing check: up to 20 example finding ids with their decision logs
  store.json           the store's tables (columns, indexes, row counts) and meta keys (values only where safe)
  settings.json        the names of the settings in force (never their values)
Every part is built from scrubbed data (runlog.Scrubber), dates of single items are cut to their month, and the
finished bundle is scanned again before it is written; scan.found counts anything the second pass had to mask.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sqlite3
import sys
import zipfile
from datetime import datetime, timedelta
from typing import Any, Callable

from . import runlog

MAX_RUNS = 60
EXAMPLES_PER_CHECK = 20
BUNDLE_PARTS = ("README.md", "REPRODUCE.md", "bundle.json", "runs/", "events/", "errors.json", "invariants.json", "repairs.json",
                "decisions.json", "examples/", "store.json", "settings.json")
# meta keys whose values go into the bundle (scrubbed): figures and states, never a key, a cursor or a patient's data
SAFE_META = {"schema_version", "derived_schema", "cloud:data_rev", "cloud:detect_seconds", "cloud:detect_pending", "pipeline:last_cycle",
             "pipeline:last_report", "sync:last_run", "ema:wiring", "ema:map_coverage", "ema:map_dirty", "ema:keep_dirty",
             "ema:ledger_seq", "ema:retain_held", "ema:refind_v1", "states:response_days", "repair:state", "backup:last",
             "activity:built", "activity:version", "activity:error", "selfcheck:safe_mode", "selfcheck:last", "archive:last"}
NEVER_META = re.compile(r"(salt|cursor|token|secret|password|key$|howto|hostkey|credential|refusal)", re.I)
# other units can add a part: {name: fn(conn) -> JSON-able}, e.g. selfcheck's history or the archive's state
PARTS: dict[str, Callable[[sqlite3.Connection], Any]] = {}


def register_part(name: str, fn: Callable[[sqlite3.Connection], Any]) -> None:
    """Add a part to every bundle (selfcheck and archive use this). Its result is scrubbed like the rest."""
    PARTS[name] = fn


# ------------------------------------------------------------------------------------------------------- collecting
def _cur(conn: sqlite3.Connection) -> sqlite3.Cursor:
    c = conn.cursor()
    c.row_factory = None
    return c


def _tables(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in _cur(conn).execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def store_layout(conn: sqlite3.Connection, budget_s: float = 5.0) -> dict:
    """Tables with their columns, indexes and row counts, and the meta keys (values only for SAFE_META)."""
    cur = _cur(conn)
    counts = runlog.store_tables(conn, budget_s=budget_s)
    tables = {}
    for t in _tables(conn):
        cols = [r[1] for r in cur.execute(f'PRAGMA table_info("{t}")')]
        idx = [r[1] for r in cur.execute(f'PRAGMA index_list("{t}")')]
        tables[t] = {"rows": counts.get(t), "columns": cols, "indexes": idx}
    meta = {}
    if "meta" in tables:
        for k, v in cur.execute("SELECT key, value FROM meta ORDER BY key"):
            meta[runlog.scrub(k, "key") if not re.fullmatch(r"[a-z_][a-z0-9_:.\-]{0,80}", str(k)) else k] = _meta_value(str(k), v)
    return {"tables": tables, "meta": meta, "page_size": cur.execute("PRAGMA page_size").fetchone()[0],
            "pages": cur.execute("PRAGMA page_count").fetchone()[0]}


def _meta_value(key: str, value: Any) -> Any:
    raw = "" if value is None else str(value)
    desc = {"length": len(raw), "sha": runlog.fingerprint(raw)}
    if NEVER_META.search(key) and key not in SAFE_META:
        return desc
    if key.endswith(":lock"):
        try:
            held = json.loads(raw)
            return {"lock": {k: held.get(k) for k in ("started", "heartbeat") if k in held}, **desc}
        except ValueError:
            return desc
    if key in SAFE_META:
        try:
            return runlog.coarse_dates(runlog.scrub(json.loads(raw)))
        except ValueError:
            return runlog.scrub_text(raw, 400)
    return desc


def _runs_wanted(conn: sqlite3.Connection | None, sink: Any, days: int, run_id: str | None, now: datetime) -> list[dict]:
    since = runlog._iso(now - timedelta(days=days))
    found: dict[str, dict] = {}
    for r in runlog.read_index(sink):
        if run_id and r.get("run_id") != run_id:
            continue
        if run_id or (r.get("started_at") or "") >= since:
            found[r["run_id"]] = r
    if conn is not None:
        try:
            for rid, started, outcome in _cur(conn).execute("SELECT run_id, started_at, outcome FROM run_log WHERE started_at >= ? OR run_id = ?",
                                                             (since if not run_id else "9999", run_id or "")):
                found.setdefault(rid, {"run_id": rid, "started_at": started, "outcome": outcome})
        except sqlite3.Error:
            pass
    return sorted(found.values(), key=lambda r: r.get("started_at") or "")[-MAX_RUNS:]


def _read_runs(sink: Any, runs: list[dict], conn: sqlite3.Connection | None) -> dict[str, tuple[dict | None, list[dict]]]:
    """Each run's manifest and events from the sink (a server's daily files are read once each), falling back on the
    store's short copy (run_log) when the sink has none."""
    out: dict[str, tuple[dict | None, list[dict]]] = {r["run_id"]: (None, []) for r in runs}
    if isinstance(sink, runlog.DirSink):
        days = sorted({(r.get("started_at") or "")[:10] for r in runs if r.get("started_at")})
        extra = set()
        for d in days:
            try:
                nxt = (datetime.fromisoformat(d) + timedelta(days=1)).date().isoformat()
                extra.add(nxt)
            except ValueError:
                pass
        for d in sorted(set(days) | extra):
            for suffix in (".jsonl", ".jsonl.gz"):
                for ln in sink.read_lines(f"runs-{d}{suffix}"):
                    m = runlog._loads(ln)
                    if m and m.get("run_id") in out:
                        out[m["run_id"]] = (m, out[m["run_id"]][1])
                for ln in sink.read_lines(f"events-{d}{suffix}"):
                    e = runlog._loads(ln)
                    if e and e.get("run_id") in out:
                        out[e["run_id"]][1].append(e)
    else:
        for r in runs:
            out[r["run_id"]] = runlog.read_run(sink, r["run_id"])
    if conn is not None:
        for rid, (m, ev) in list(out.items()):
            if m is None:
                try:
                    row = _cur(conn).execute("SELECT summary FROM run_log WHERE run_id=?", (rid,)).fetchone()
                except sqlite3.Error:
                    row = None
                if row and row[0]:
                    out[rid] = (dict(runlog._loads(row[0]) or {}, note="short copy from the store; the full manifest was not found"), ev)
    return out


def _examples(conn: sqlite3.Connection, ids: list[str]) -> list[dict]:
    """Example items with their decision logs: ids, the item's codes and states, months instead of dates."""
    cur = _cur(conn)
    have = {r[1] for r in cur.execute("PRAGMA table_info(findings)")}
    cols = [c for c in ("finding_id", "signal", "category", "status", "work_state", "payer_group", "payer_kind", "procedure_code",
                        "carc", "rarc", "shortfall_cents", "expected_cents", "stale", "ema_outcome", "source", "service_month",
                        "follow_up_on", "appeal_deadline", "line_key", "claim_key") if c in have]
    out = []
    for fid in ids[:EXAMPLES_PER_CHECK]:
        fid = runlog.safe_id(fid)
        if not fid:
            continue
        row = cur.execute(f"SELECT {', '.join(cols)} FROM findings WHERE finding_id=?", (fid,)).fetchone() if cols else None
        item = dict(zip(cols, row)) if row else {"finding_id": fid, "missing": True}
        item["decisions"] = runlog.decisions_for(conn, fid, limit=30)["items"]
        out.append(runlog.coarse_dates(runlog.scrub(item)))
    return out


def _failing_checks(manifests: list[dict]) -> dict[str, list[str]]:
    """Failing invariants of the newest run that has any, with their example ids: {name: [finding ids]}."""
    for m in reversed(manifests):
        inv = m.get("invariants")
        rows = inv.get("results") if isinstance(inv, dict) else inv
        if not isinstance(rows, list):
            continue
        out = {}
        for r in rows:
            if isinstance(r, dict) and r.get("ok") is False:
                out[str(r.get("name") or r.get("check") or "check")] = [x for x in (r.get("examples") or []) if isinstance(x, str)]
        if out:
            return out
    return {}


def _decision_counts(conn: sqlite3.Connection, since: str) -> dict:
    try:
        cur = _cur(conn)
        by = [{"kind": k, "rule": r, "n": n} for k, r, n in cur.execute(
            "SELECT kind, rule, COUNT(*) FROM item_decisions WHERE at >= ? GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 200", (since,))]
        runs = [{"run_id": rid, "n": n} for rid, n in cur.execute(
            "SELECT run_id, COUNT(*) FROM item_decisions WHERE at >= ? GROUP BY 1 ORDER BY 1 DESC LIMIT 60", (since,))]
        return {"by_kind_and_rule": by, "by_run": runs}
    except sqlite3.Error:
        return {"note": "no decision log in this store yet"}


def _selfcheck_tables(conn: sqlite3.Connection) -> dict:
    """Any table the self-check keeps (named selfcheck_*, invariant*, self_audit*): rows scrubbed, dates by month."""
    out = {}
    for t in _tables(conn):
        if re.match(r"^(selfcheck|invariant|self_audit|audit_)", t):
            try:
                cur = _cur(conn).execute(f'SELECT * FROM "{t}" ORDER BY rowid DESC LIMIT 400')
                names = [d[0] for d in cur.description]
                out[t] = [runlog.coarse_dates(runlog.scrub(dict(zip(names, r)))) for r in cur.fetchall()]
            except sqlite3.Error as exc:
                out[t] = {"error": type(exc).__name__}
    return out


# ----------------------------------------------------------------------------------------------------------- texts
README = """# RemitWatch diagnostic bundle

Built {built} from {source}. It holds no patient data: finding ids, payer groups, codes, counts and dollars only. Dates
of single items are cut to their month.

Read it in this order:
1. bundle.json: which runs it covers, the code commit and the versions, and the scan of this bundle.
2. errors.json: every error of these runs, newest last, each with `where` (file:line in function) and its stack. Start
   with the first error of the first failing run: later errors are often its consequences.
3. runs/<run_id>.json: one run's manifest. `steps` gives each step's time and memory (a step that stops a run shows as
   the last one; a killed run has outcome "killed" and its last step in the index). `inputs` lists the files by hash,
   kind, origin and result. `outputs` counts items by state and signal. `sections.decisions` counts what the run
   changed (raised, closed by rule, reopened).
4. events/<run_id>.jsonl: the run's event log, one JSON object per line: ts, run_id, step, level, event, rule,
   rule_version, item (a finding id), counts, ms, mem_mb, detail, error.
5. invariants.json and examples/: the self-checks that failed, with up to 20 example items each and the decisions
   that changed them. A check that started failing at one run points at what that run changed.
6. repairs.json: what RemitWatch repaired by itself and what it could not (open problems, with the next step).
7. store.json: the store's tables, columns and row counts, and its settings (meta) where they are safe to show.

Then open CLAUDE.md and docs/TROUBLESHOOTING.md in the repository (the failure classes and their fixes), and follow
REPRODUCE.md to reproduce on a copy before changing code. A fix starts with a test that fails.
"""

REPRODUCE = """# Reproduce on a copy

Never on the live store, and never with the live Dropbox or fax line. The copy holds patient data: it stays on the
server (or on a machine the practice allows for patient data), in a scratch folder, and is deleted afterwards.

Code: the runs in this bundle ran commit {commit} (rules {rules}). Check it out: `git checkout {commit}`.

## Server (AWS, Docker)
1. A copy of the store: the newest backup in the backups folder beside the store (/data/backups/remitwatch-<stamp>.sqlite.gz),
   or a fresh one: `sudo docker compose exec app python -m remitwatch.cli backup --dir /data/scratch`.
2. Unpack it in the scratch folder: `sudo gunzip -k /data/scratch/remitwatch-<stamp>.sqlite.gz`.
3. One cycle on the copy, with Dropbox, TriZetto, the fax line and mail switched off for that command only (a copy
   must never send a fax or pull from the live feeds):
   `sudo docker compose exec -e REMITWATCH_FAX_SENDER=none -e TRIZETTO_SFTP_USER= -e DROPBOX_REFRESH_TOKEN= -e SMTP_HOST= -e ALERT_WEBHOOK_URL= app python -m remitwatch.cli --db /data/scratch/remitwatch-<stamp>.sqlite cycle --skip-sync --no-email --force-detect`
4. Detection alone: `python -m remitwatch.cli --db <copy> detect`. The logs of the copy's runs go to the logs folder
   beside the copy (/data/scratch/logs), not the live one.
5. A bundle from the copy: `python -m remitwatch.diagnose --db <copy> --days 2`.

## Serverless (Vercel)
1. Copy the state folder (Dropbox, /RemitWatch-state) to a local folder, for example with the Dropbox desktop app or a
   download of the folder. Leave out packets/ unless the problem is about faxes.
2. In a shell that has none of the site's settings (no DROPBOX_*, TRIZETTO_*, SRFAX_* or SMTP_* values), run the cycle
   on the copy with no Dropbox client (nothing is read from or written to Dropbox, no fax is sent):
   `python -c "from remitwatch.state import LocalState; from remitwatch import cloud; print(cloud.run_cloud_cycle(LocalState('<copy>'), None, email=False, force_detect=True))"`
3. The run's logs are in <copy>/logs. A bundle from the copy: `python -m remitwatch.diagnose --state-dir <copy> --days 2`.

## The tests, in the four environments (all must pass before a release)
- `python3 -m pytest -q`: the newest Python with a recent SQLite (3.5x).
- The same with the newest libraries: `pip install --upgrade -r requirements-dev.txt` in a fresh virtual environment.
- Python 3.12 with SQLite 3.45: what the AWS image and CI run (SQL must not name an outer table inside a subquery's ORDER BY).
- Python 3.11 with SQLite 3.40.0: Vercel's SQLite (`uv python install 3.11.1`: that build carries SQLite 3.40.0). Only tests
  that serialize a store image may fail there; no SQL function newer than 3.40.
- `node tests/calc_model_check.js` and `node tests/lab_review_check.js` when pages changed.
"""


# --------------------------------------------------------------------------------------------------------- the bundle
def build_bundle(*, conn: sqlite3.Connection | None = None, db_path: str | None = None, state: Any = None, logs: Any = None,
                 days: int = 7, run_id: str | None = None, now: datetime | None = None, out_path: str | None = None,
                 known: list[str] | None = None) -> dict:
    """Make the bundle. Sources: conn (a store or the dashboard's copy) or db_path (opened read-only), and the logs
    (state: a state folder; logs: a logs folder or a sink; by default the store's logs folder). Returns {"path" or
    "bytes", "parts", "runs", "scan"}. known: words that must not appear anywhere in the bundle (tests plant them)."""
    t = now or runlog.utcnow()
    own_conn = None
    if conn is None and db_path:
        own_conn = sqlite3.connect(f"file:{os.path.abspath(db_path)}?mode=ro", uri=True, timeout=30)
        conn = own_conn
    try:
        if conn is not None:
            runlog.SCRUB.learn(conn)
        if known:
            runlog.SCRUB.add_names(known)
        sink = runlog.sink_for(db_path=db_path, state=state, logs_dir=logs)
        runs = _runs_wanted(conn, sink, days, run_id, t)
        got = _read_runs(sink, runs, conn)
        parts: dict[str, Any] = {}
        manifests = []
        errors = []
        for r in runs:
            m, events = got.get(r["run_id"], (None, []))
            m = m or dict(r, note="only the index line was found")
            manifests.append(m)
            parts[f"runs/{r['run_id']}.json"] = runlog.scrub(m)
            if events:
                parts[f"events/{r['run_id']}.jsonl"] = [runlog.scrub(e) for e in events]
            for e in m.get("errors") or []:
                errors.append(dict(e, run_id=r["run_id"]))
            for e in events:
                if e.get("level") == "error" and e.get("error") and not any(x.get("at") == e.get("ts") and x.get("type") == e["error"].get("type")
                                                                              for x in m.get("errors") or []):
                    errors.append(dict(e["error"], run_id=r["run_id"], step=e.get("step"), at=e.get("ts")))
        parts["errors.json"] = runlog.scrub({"count": len(errors), "errors": errors[-500:]})
        inv = [{"run_id": m.get("run_id"), "started_at": m.get("started_at"), "outcome": m.get("outcome"),
                "invariants": m.get("invariants"), "self_regulation": m.get("self_regulation"),
                "self_audit": (m.get("sections") or {}).get("self_audit")} for m in manifests]
        parts["invariants.json"] = runlog.coarse_dates(runlog.scrub({"by_run": inv, "tables": _selfcheck_tables(conn) if conn is not None else {}}))
        if conn is not None:
            from . import repair
            parts["repairs.json"] = runlog.coarse_dates(runlog.scrub({"recent": repair.recent(conn, limit=200),
                                                                      "open_problems": repair.open_problems(conn=conn, sink=sink)}))
            parts["decisions.json"] = runlog.scrub(_decision_counts(conn, runlog._iso(t - timedelta(days=days))))
            for name, ids in _failing_checks(manifests).items():
                parts[f"examples/{re.sub(r'[^a-z0-9_]+', '_', name.lower())[:60]}.json"] = {"check": runlog.scrub(name, "rule"),
                                                                                         "items": _examples(conn, ids)}
            parts["store.json"] = runlog.scrub(store_layout(conn))
            for name, fn in PARTS.items():
                try:
                    parts[f"{re.sub(r'[^a-z0-9_]+', '_', name.lower())}.json"] = runlog.coarse_dates(runlog.scrub(fn(conn)))
                except Exception as exc:        # noqa: BLE001  (a part that fails is named in the bundle with its error)
                    parts[f"{re.sub(r'[^a-z0-9_]+', '_', name.lower())}.json"] = {"error": runlog.error_info(exc)}
        else:
            from . import repair
            parts["repairs.json"] = runlog.scrub({"open_problems": repair.open_problems(sink=sink)})
        latest = manifests[-1] if manifests else {}
        here = runlog.versions()
        parts["settings.json"] = {"in_force_at_last_run": (latest.get("settings") or []), "here": runlog.settings_names()}
        code = latest.get("code") or {"commit": runlog.code_commit(), **runlog.rules_version()}
        info = {"built": runlog._iso(t), "source": _describe(db_path, state, logs, conn), "days": days, "run": run_id,
                "runs": [{"run_id": m.get("run_id"), "outcome": m.get("outcome"), "started_at": m.get("started_at")} for m in manifests],
                "versions": {"bundle_built_with": here, "last_run": latest.get("versions")}, "code": code,
                "parts": sorted(parts) + ["README.md", "REPRODUCE.md", "bundle.json"]}
        texts = {"README.md": README.format(built=info["built"], source=info["source"]),
                 "REPRODUCE.md": REPRODUCE.format(commit=code.get("commit") or "<the commit on /api/health>",
                                                  rules=code.get("rules_version") or "unknown")}
        # the second pass: anything a part still holds that the scrubber would mask is masked now, and counted
        found = 0
        clean: dict[str, Any] = {}
        for name, value in parts.items():
            again = runlog.scrub(value)
            if again != value:
                found += _count_diff(value, again)
            clean[name] = again
        info["scan"] = {"masked_on_second_pass": found, "known_words_checked": len(known or [])}
        blobs = {n: _dump(v) for n, v in clean.items()}
        blobs["bundle.json"] = json.dumps(runlog.scrub(info), indent=1, ensure_ascii=False, default=str)
        blobs.update(texts)
        if known:
            hits = scan_bytes({n: b.encode("utf-8") if isinstance(b, str) else b for n, b in blobs.items()}, known)
            if hits:
                raise RuntimeError(f"the bundle still holds {len(hits)} planted word(s) in: {sorted({h[0] for h in hits})}")
        data = _zip(blobs)
        out = {"parts": sorted(blobs), "runs": [m.get("run_id") for m in manifests], "scan": info["scan"], "size": len(data)}
        if out_path:
            os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
            tmp = out_path + ".part"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, out_path)
            out["path"] = out_path
        else:
            out["bytes"] = data
        return out
    finally:
        if own_conn is not None:
            own_conn.close()


def _describe(db_path: str | None, state: Any, logs: Any, conn: Any) -> str:
    if state is not None:
        return "a serverless state folder and " + ("the dashboard's copy" if conn is not None else "no store")
    if db_path:
        return "a server's store and its logs folder"
    return "a store" if conn is not None else "logs only"


def _count_diff(a: Any, b: Any) -> int:
    if isinstance(a, dict) and isinstance(b, dict):
        return sum(_count_diff(a.get(k), b.get(k)) for k in set(a) | set(b))
    if isinstance(a, list) and isinstance(b, list):
        return sum(_count_diff(x, y) for x, y in zip(a, b)) + abs(len(a) - len(b))
    return 0 if a == b else 1


def _dump(v: Any) -> str:
    if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
        return "\n".join(json.dumps(x, ensure_ascii=False, default=str, separators=(",", ":")) for x in v) + "\n"
    return json.dumps(v, indent=1, ensure_ascii=False, default=str)


def _zip(blobs: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for name in sorted(blobs):
            zi = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o600 << 16
            data = blobs[name]
            z.writestr(zi, data.encode("utf-8") if isinstance(data, str) else data)
    return buf.getvalue()


def scan_bytes(files: dict[str, bytes], known: list[str]) -> list[tuple[str, str]]:
    """Which planted words appear in which file (case-insensitive, as text and as JSON's escaped form)."""
    hits = []
    for name, data in files.items():
        low = data.decode("utf-8", errors="ignore").lower()
        esc = json.dumps(low)[1:-1]
        for w in known:
            wl = w.lower()
            if wl and (wl in low or json.dumps(wl)[1:-1] in esc):
                hits.append((name, w))
    return hits


def unpack(data: bytes) -> dict[str, bytes]:
    """The members of a bundle (for tests and for reading one in Python)."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return {n: z.read(n) for n in z.namelist()}


# ------------------------------------------------------------------------------------------------------ command line
def default_out(db_path: str | None, state_dir: str | None, now: datetime | None = None) -> str:
    stamp = (now or runlog.utcnow()).strftime("%Y%m%d-%H%M%S")
    if state_dir:
        base = os.path.join(os.path.dirname(os.path.abspath(state_dir)), "diagnostics")
    else:
        base = os.path.join(os.path.dirname(os.path.abspath(db_path or "data/remitwatch.sqlite")), "diagnostics")
    return os.path.join(base, f"remitwatch-diagnostic-{stamp}.zip")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m remitwatch.diagnose", description="Make a diagnostic bundle (no patient data).")
    ap.add_argument("--db", default=os.environ.get("REMITWATCH_DB", "data/remitwatch.sqlite"), help="the store (a server)")
    ap.add_argument("--state-dir", default=None, help="a local copy of a serverless state folder (instead of --db)")
    ap.add_argument("--logs", default=None, help="a logs folder (default: the one beside the store, or the state folder's)")
    ap.add_argument("--days", type=int, default=7, help="the runs of the last N days (default 7)")
    ap.add_argument("--run", default=None, help="only this run id")
    ap.add_argument("--out", default=None, help="where to write the zip (default: a diagnostics folder beside the store)")
    a = ap.parse_args(argv)
    state = None
    db_path = None
    conn = None
    if a.state_dir:
        from . import cloud
        from .state import LocalState
        state = LocalState(a.state_dir)
        if state.meta(cloud.RAW):
            store, _existed = cloud.load_raw(state)
            conn = store.conn
    elif os.path.exists(a.db):
        db_path = a.db
    else:
        print(f"No store at {a.db}: give --db or --state-dir.", file=sys.stderr)
        return 2
    out_path = a.out or default_out(db_path, a.state_dir)
    res = build_bundle(conn=conn, db_path=db_path, state=state, logs=a.logs, days=a.days, run_id=a.run, out_path=out_path)
    print(json.dumps({"bundle": res.get("path"), "runs": len(res["runs"]), "size": res["size"], "scan": res["scan"]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
