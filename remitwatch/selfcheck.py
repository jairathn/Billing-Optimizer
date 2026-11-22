"""
RemitWatch runs itself (unit G): self-regulation of memory and time, the self-check of every run with safe mode, the weekly
self-audit, its own track record, the pattern watch, housekeeping, and the money checks the owner made non-negotiable
(every dollar accounted for, money given up without a decision, blindness in dollars), with the recovery rates the
worklist ranks by. Plain rules and arithmetic only: no AI anywhere in this module.

Every function a run calls takes the run's context explicitly (the connection, the run id, the host mode, today) and
returns what it did. The clock is read only through today_of() and now_of(), and the environment only through setting(),
so a test (or the fourteen simulated days of the verification) can drive it day by day.

1. Self-regulation (plan_next, mark, budgets). Each run ends with its peak memory, wall time and /tmp use per step in its
   status (cloud.py already keeps memory per step; mark() adds /tmp and seconds beside it). The next run reads that status:
   past 80% of a budget (serverless: 1.6 GB of the 2 GB function, its time budget, 512 MB of /tmp; server: the configured
   budgets), it steps up one level, in this order, each step logged: 1 spread the work across runs (one report file per
   run, detection in a fresh invocation), 2 run the archive at the tighter end of its allowed horizon (unit E's bounds),
   3 leave the optional steps (provider tables, report email, export-time backfill, the weekly audit) for a calm run, and
   4, when nothing more can be shed, one alert ("near its memory limit ... nothing was lost ... this is what it did").
   Three calm runs in a row step it down again.

2. The self-check (begin_detection, after_detection, check_ledger, check_serving; CHECKS). A battery of rule-based
   invariants over the run's output, each with a name, a plain sentence, the count of offending items, their dollars and
   up to 20 example finding ids. Results are kept per run (meta selfcheck:history), so a check that starts failing shows
   as a change. A critical check (money or state) that finds offenders the last good run did not have, beyond its small
   tolerance, puts the run in SAFE MODE: the store keeps everything the run read and worked out (the run's own items are
   kept, compressed, in selfcheck_held for diagnosis), but the worklist people see goes back to the items as they were
   before the run (the last good run's, with every person's changes since), the run's automatic closures are held, and
   an alert says why and what to check. The next good run releases it. Offenders the last good run already had keep the
   check red and its alert on without freezing the worklist again (holding would not remove them, and would stop new
   work from showing); a person can accept a change on the Data tab (accept_findings: a business decision, recorded with
   who and when). The first run of the self-check sets the baseline and holds nothing. An open item that disappears with
   no reason (no closure, no replacement for its line, not folded, not superseded) is KEPT on the worklist with a note,
   never silently dropped.

3. The weekly self-audit (self_audit). Independent of the detection code: a separate, simpler rule set over the raw
   tables (the 835 archive, EMA's lines, checks and A/R), on the rules of the frozen answer keys (gate 1: coverage of
   every 2025+ line with recoverable dollars inside a window; gate 2: precision of the WORK NOW list). It lists coverage
   gaps (with dollars) and possible wasted work (with the reason), keeps the confusion matrix week by week, and raises an
   alert when new gaps over $500 or more than 10 new wasted items appear (both configurable).

4. The track record (updated by after_detection from what changed; track_record). Automatic closures later reopened (a
   payment taken back) as an error rate by rule; waiting items the payer then paid or denied, and how long it took (each
   payer group's answer time measured from RemitWatch's own waits); items a person let go that were later paid; deadlines
   missed (an item that expired while WORK NOW).

5. The pattern watch (pattern_watch): the last 30 days against the 90 before them: a new or rising denial at payer group x
   code x modifier x reason grain (only where the existing RAMP, RISING, NEW_BEHAVIOR, RESUMED and NEW_REASON_CODE alerts do
   not already cover the payer: they are extended with pattern_breakdown, not duplicated); a new clinician a payer denies
   for credentialing or enrollment; a clinician paid below the practice's own rate for the same code and payer; a payer
   that reprocessed and paid a batch of identical denials while others of the batch are still open. REMITS_MISSING (a
   payer's remittances missing while EMA posts its checks) stays ema_checks' alert.

6. Housekeeping (housekeeping): the clean-ups of units A1, A2 and E called from the cycle with their results recorded.

8-12. Money (check_ledger, given_up, blindness, scoreboard, recovery_rates, expected_value): money conservation per service
   month and payer group from the posting ledger, with every unattributed dollar and every line EMA's A/R and postings
   disagree on counted as a gap with its amount; the dollars given up without a decision; what the app cannot see and
   what that is worth; G's figures for the Results tab; and each item's dollars x the recovery rate of its pattern from the
   835 archive (payer group x code x reason, then the fallbacks, always named in the basis).

Everything a page shows is kept in meta selfcheck:* keys, so the dashboard's copy (which carries meta) shows the same
panel as the store (panel, data_health). Nothing here deletes source data or hides an item.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import time
import zlib
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Sequence

VERSION = "1"                       # this rule set's version (the history and the decision log name it)

# ------------------------------------------------------------------------------------------------------------ words
MONEY = ("denial", "bundling", "downcoding", "underpayment")
OPEN_STATUSES = ("open", "in_progress", "appealed")
CLOSED_STATUSES = ("resolved", "lost", "dismissed")
STATES = ("work_now", "waiting", "done", "accepted", "expired", "cant_check")
LISTED = ("work_now", "waiting")
CLAIM_SIGNALS = ("NO_ANSWER", "REJECTED_PRE_PAYER", "NOT_SENT")
EMA_ONLY = ("EMA_DENIED", "NO_ANSWER", "REJECTED_PRE_PAYER", "NOT_SENT", "REMIT_NOT_POSTED")
NOT_A_DENIAL = ("CREDIT_BALANCE", "REMIT_NOT_POSTED", "EMA_DENIAL_AS_CONTRACTUAL", "SECONDARY_NOT_BILLED", "FORWARDED_TO_CARRIER")
# the "nobody has worked it" openings of states.py's automatic work-now basis (states._open_basis), used where the store
# keeps no state_rule column yet
OPEN_BASIS = ("Not paid and nobody has worked it", "No answer from the payer on file", "Never reached the payer",
              "Never left EMA", "Denied in EMA, no remittance on file", "Nothing from ")
# CARCs a payer uses for its routine reductions and the patient's share: never a denial
ROUTINE_CARCS = frozenset({"45", "253", "144", "23", "94", "187", "59", "131", "237", "A2", "P1", "W1", "3", "2", "1", "42", "B6"})
PATIENT_SHARE = frozenset({"1", "2", "3"})
# enrollment and credentialing denials (detect.PROVIDER_CARCS, and the remark codes that name the rendering provider)
PROVIDER_CARCS = frozenset({"B7", "170", "171", "185", "208", "242", "54", "147"})
PROVIDER_RARCS = frozenset({"N290", "N95", "N55", "M16"})
SERVICE_FROM = "2025-01-01"         # the population the owner's gates measure

# ------------------------------------------------------------------------------------------------------------ settings
SERVERLESS_MEMORY_BUDGET_MB = 1600  # of the 2 GB function (cloud.NEEDED_MEMORY_MB)
TMP_BUDGET_MB = 512                 # Vercel's /tmp
PRESSURE_SHARE = 0.80               # a run past this share of a budget makes the next one act
CALM_RUNS_TO_STEP_DOWN = 3
LEVELS = ("calm", "spread", "archive", "shed", "alert")
OPTIONAL_STEPS = ("activity", "report", "backfill", "audit")
HISTORY_RUNS = 30                   # invariant results kept in meta for the panel (all runs in selfcheck_runs, up to 400)
EXAMPLES = 20
AUDIT_EVERY_DAYS = 7
AUDIT_GAP_ALERT_CENTS = 50_000      # any NEW coverage gap over $500 raises the audit alert
AUDIT_WASTED_ALERT = 10             # ... as do more than 10 new possibly wasted items
GIVEN_UP_ALERT_CENTS = 50_000       # the week's money given up without a decision rising by more than $500
TRACK_ALERT_SHARE = 0.05            # a closure rule whose reopen rate passes 5% (with 20 closures at least)
RATE_MIN_CASES = 10                 # a recovery-rate pattern needs this many cases, else the next level is used
RATE_MATURE_DAYS = 90               # a denial younger than this (from the newest remittance) has not had time to be paid
LATER_PAID_SHARE = 0.95             # a later payment of at least this share of the shortfall covers it (precision guard)
SETTLED_SHARE = 0.50                # the self-audit's "paid" test (half the target, as the answer keys have it)
STALE_835_DAYS = 10                 # alerts.STALE_DAYS
STALE_PRODUCTION_DAYS = 14          # ema_checks.PRODUCTION_FRESH_DAYS
STALE_AR_DAYS = 8                   # ema_checks.AR_FRESH_DAYS


def setting(name: str, default: Any = None) -> Any:
    """The one place this module reads the environment."""
    v = os.environ.get(name)
    return default if v in (None, "") else v


def _int_setting(name: str, default: int) -> int:
    try:
        return int(setting(name, default))
    except (TypeError, ValueError):
        return default


def today_of(today: Any = None) -> str:
    """Today as YYYY-MM-DD: the run's own day when it passes one, else REMITWATCH_TODAY (simulated days), else the clock."""
    if today:
        return str(today)[:10]
    v = setting("REMITWATCH_TODAY")
    if v and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(v)[:10]):
        return str(v)[:10]
    return datetime.now(timezone.utc).date().isoformat()


def now_of(now: Any = None) -> str:
    """Now as an ISO time (UTC, seconds): the run's own time when it passes one, else the clock."""
    if now:
        return str(now)
    v = setting("REMITWATCH_TODAY")
    if v and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(v)[:10]):
        return str(v)[:10] + "T12:00:00+00:00"
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------------------------------------------------ helpers
def _d(iso: Any) -> date | None:
    try:
        return date.fromisoformat(str(iso)[:10])
    except (TypeError, ValueError):
        return None


def _add(iso: Any, days: int) -> str | None:
    x = _d(iso)
    return (x + timedelta(days=days)).isoformat() if x else None


def _days(a: Any, b: Any) -> int | None:
    x, y = _d(a), _d(b)
    return (y - x).days if x and y else None


def usd(cents: Any) -> str:
    c = int(cents or 0)
    return f"-${abs(c) / 100:,.2f}" if c < 0 else f"${c / 100:,.2f}"


def usd0(cents: Any) -> str:
    c = int(cents or 0)
    return f"-${abs(c) / 100:,.0f}" if c < 0 else f"${c / 100:,.0f}"


def _n(k: int, word: str, plural: str | None = None) -> str:
    return f"{k:,} {word if k == 1 else (plural or word + 's')}"


def _has_table(conn: sqlite3.Connection, name: str, schema: str = "main") -> bool:
    master = "sqlite_temp_master" if schema == "temp" else f"{schema}.sqlite_master"
    try:
        return conn.execute(f"SELECT 1 FROM {master} WHERE type='table' AND name=?", (name,)).fetchone() is not None
    except sqlite3.Error:
        return False


def _cols(conn: sqlite3.Connection, table: str, schema: str = "main") -> list[str]:
    try:
        return [r[1] for r in conn.execute(f"PRAGMA {schema}.table_info({table})")]
    except sqlite3.Error:
        return []


def meta_get(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    try:
        r = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    except sqlite3.Error:
        return default
    if not r or r[0] is None:
        return default
    try:
        return json.loads(r[0])
    except (TypeError, ValueError):
        return r[0]


def meta_set(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, json.dumps(value, default=str, sort_keys=True)))


def _ev(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        v = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return v if isinstance(v, dict) else {}


_BILL = re.compile(r"^(F?[A-Z]{2}\d{2}[A-Z0-9]{5,6})C\d{0,3}$")
_FACILITY = re.compile(r"^F[A-Z]{2}\d{2}[A-Z0-9]{5,6}C\d{0,3}$")


def bill_of(pcn: Any) -> str | None:
    """An EMA claim id's bill (the id less its submission suffix; the surgery center's F kept). Any other id is its own bill."""
    if not pcn:
        return None
    s = str(pcn).strip()
    m = _BILL.match(s)
    return m.group(1) if m else s


def is_facility(pcn: Any) -> bool:
    return bool(pcn) and bool(_FACILITY.match(str(pcn).strip()))


def _bill_sql(col: str) -> str:
    """SQL for the bill of a claim id column (the id less a trailing C and up to three digits), plain SQL only."""
    return (f"CASE WHEN {col} GLOB '*C[0-9][0-9][0-9]' THEN substr({col}, 1, length({col}) - 4) "
            f"WHEN {col} GLOB '*C[0-9][0-9]' THEN substr({col}, 1, length({col}) - 3) "
            f"WHEN {col} GLOB '*C[0-9]' THEN substr({col}, 1, length({col}) - 2) "
            f"WHEN {col} GLOB '*C' THEN substr({col}, 1, length({col}) - 1) ELSE {col} END")


def _codes_of(item: dict) -> list[str]:
    out = []
    for k in ("procedure_code", "original_procedure_code", "billed_code"):
        v = item.get(k)
        if v and v not in out:
            out.append(str(v))
    return out


def _digits(s: Any) -> str:
    return re.sub(r"\D", "", str(s or "")).lstrip("0")


_GROUPS: dict[tuple, tuple[str, str]] = {}


def _group_kind(name: Any, cfi: Any = None) -> tuple[str, str]:
    """codes.payer_group_kind, remembered per (name, filing indicator): the archive has a few hundred of them."""
    k = (name, cfi)
    got = _GROUPS.get(k)
    if got is None:
        try:
            from .codes import payer_group_kind
            got = payer_group_kind(name, cfi)
        except Exception as exc:          # noqa: BLE001  (a payer name the rules cannot read stays its own group)
            _caught(exc, "payer_group")
            got = (str(name or "Unknown"), "other")
        if len(_GROUPS) > 50_000:
            _GROUPS.clear()
        _GROUPS[k] = got
    return got


def _log(name: str, **fields: Any) -> None:
    """An event in the run's log (remitwatch.runlog: this module keeps no logger of its own)."""
    try:
        from . import runlog
        runlog.event("selfcheck", name, **fields)
    except Exception:          # noqa: BLE001  (no run log in progress, or none installed: nothing to write to)
        return


def _caught(exc: BaseException, step: str, inputs: Any = None) -> None:
    """An exception this module contains, with its stack, in the run's log."""
    try:
        from . import runlog
        runlog.caught(exc, "selfcheck:" + step, inputs=inputs, level="warning")
    except Exception:          # noqa: BLE001  (no run log in progress, or none installed)
        return


def _pct(values: list, p: int) -> Any:
    if not values:
        return None
    v = sorted(values)
    return v[min(len(v) - 1, max(0, (len(v) * p + 99) // 100 - 1))]


def peak_mb() -> int | None:
    """The most memory this process has held so far, in MB."""
    try:
        import resource
        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
    except Exception:          # noqa: BLE001  (no resource module on this platform)
        return None


def tmp_used_mb(folder: str | None = None, limit: int = 20000) -> int | None:
    """What the temporary folder holds now, in MB (the files under it, at most `limit` entries looked at)."""
    import tempfile
    root = folder or tempfile.gettempdir()
    total, seen = 0, 0
    try:
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                seen += 1
                if seen > limit:
                    return total >> 20
                try:
                    total += os.lstat(os.path.join(dirpath, f)).st_size
                except OSError:
                    pass
    except OSError:
        return None
    return total >> 20


# ------------------------------------------------------------------------------------------------------------ schema
DDL = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS selfcheck_runs (run_id TEXT PRIMARY KEY, at TEXT, today TEXT, mode TEXT, held INTEGER DEFAULT 0,
    first INTEGER DEFAULT 0, failing INTEGER, critical INTEGER, checks TEXT, summary TEXT);
CREATE TABLE IF NOT EXISTS selfcheck_baseline (check_id TEXT NOT NULL, key TEXT NOT NULL, cents INTEGER, since TEXT,
    accepted_by TEXT, PRIMARY KEY (check_id, key)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS selfcheck_held (run_id TEXT NOT NULL, kind TEXT NOT NULL, part INTEGER NOT NULL, data BLOB,
    PRIMARY KEY (run_id, kind, part)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS selfcheck_cycles (started TEXT PRIMARY KEY, finished TEXT, mode TEXT, seconds REAL, budget_s REAL,
    peak_mb INTEGER, budget_mb INTEGER, limit_mb INTEGER, tmp_mb INTEGER, tmp_budget_mb INTEGER, pressure TEXT, level INTEGER,
    plan TEXT, error TEXT, steps TEXT);
CREATE TABLE IF NOT EXISTS selfcheck_audits (audit_id TEXT PRIMARY KEY, at TEXT, today TEXT, why TEXT, summary TEXT);
CREATE TABLE IF NOT EXISTS selfcheck_audit_items (audit_id TEXT NOT NULL, list TEXT NOT NULL, key TEXT NOT NULL, finding_id TEXT,
    category TEXT, reason TEXT, payer_group TEXT, service_month TEXT, cents INTEGER, deadline TEXT, new INTEGER,
    PRIMARY KEY (audit_id, list, key)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS selfcheck_track (finding_id TEXT NOT NULL, kind TEXT NOT NULL, started TEXT NOT NULL, ended TEXT,
    outcome TEXT, days INTEGER, payer_group TEXT, rule TEXT, cents INTEGER, run_id TEXT,
    PRIMARY KEY (finding_id, kind, started)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS selfcheck_money (month TEXT NOT NULL, payer_group TEXT NOT NULL, measure TEXT NOT NULL, cents INTEGER,
    rows INTEGER, PRIMARY KEY (month, payer_group, measure)) WITHOUT ROWID;
"""
TABLES = ("selfcheck_runs", "selfcheck_baseline", "selfcheck_held", "selfcheck_cycles", "selfcheck_audits",
          "selfcheck_audit_items", "selfcheck_track", "selfcheck_money")
# the finding columns the worklist ranks by (written at detection save from recovery_rates and expected_value)
VALUE_COLUMNS = {"expected_value_cents": "INTEGER", "recovery_rate": "REAL", "recovery_basis": "TEXT"}

K_LAST = "selfcheck:last"
K_HISTORY = "selfcheck:history"
K_SAFE = "selfcheck:safe_mode"
K_REGULATE = "selfcheck:regulate"
K_AUDIT = "selfcheck:audit"
K_TRACK = "selfcheck:track"
K_PATTERNS = "selfcheck:patterns"
K_MONEY = "selfcheck:money"
K_GIVEN_UP = "selfcheck:given_up"
K_BLIND = "selfcheck:blind"
K_RATES = "selfcheck:rates"
K_HOUSE = "selfcheck:housekeeping"
K_ANSWER = "selfcheck:answer_days"


def ensure_tables(conn: sqlite3.Connection) -> None:
    """Create this module's tables (none of them is in the dashboard's copy: what the pages need is in meta)."""
    conn.executescript(DDL)


def ensure_value_columns(conn: sqlite3.Connection) -> list[str]:
    """Add the ranking columns to findings when a store does not have them yet (detect.EXTRA_FINDING_COLS adds them once
    wired). Returns the columns added."""
    have = set(_cols(conn, "findings"))
    added = []
    if not have:
        return added
    for col, decl in VALUE_COLUMNS.items():
        if col not in have:
            conn.execute(f"ALTER TABLE findings ADD COLUMN {col} {decl}")
            added.append(col)
    return added


# =====================================================================================================================
# 1. Self-regulation
# =====================================================================================================================
def budgets(mode: str, *, budget_s: Any = None) -> dict:
    """The budgets a run is measured against. Serverless: 1.6 GB (REMITWATCH_MEMORY_BUDGET_MB), the run's time budget,
    512 MB of /tmp. Server: the configured memory budget (none unless REMITWATCH_MEMORY_BUDGET_MB is set) and
    REMITWATCH_CYCLE_BUDGET_SECONDS (3600)."""
    serverless = mode == "serverless"
    mem = setting("REMITWATCH_MEMORY_BUDGET_MB")
    try:
        mem_mb = int(mem) if mem else (SERVERLESS_MEMORY_BUDGET_MB if serverless else None)
    except ValueError:
        mem_mb = SERVERLESS_MEMORY_BUDGET_MB if serverless else None
    try:
        secs = float(budget_s) if budget_s else float(setting("REMITWATCH_MAX_SECONDS" if serverless else
                                                            "REMITWATCH_CYCLE_BUDGET_SECONDS", 240 if serverless else 3600))
    except (TypeError, ValueError):
        secs = 240.0 if serverless else 3600.0
    return {"memory_mb": mem_mb, "seconds": secs, "tmp_mb": TMP_BUDGET_MB if serverless else None, "share": PRESSURE_SHARE}


def mark(out: dict, step: str, started: float | None = None, *, tmp: bool | None = None) -> dict:
    """Record a step's figures in a run's status, beside the memory per step cloud.py keeps: peak memory so far
    (out['memory_mb'][step]), /tmp in use (out['tmp_mb'][step], serverless by default) and the step's seconds
    (out['step_seconds'][step], when `started` is given). Returns the figures."""
    fig = {"mem": peak_mb()}
    out.setdefault("memory_mb", {})[step] = fig["mem"]
    if tmp if tmp is not None else (out.get("mode") == "serverless"):
        fig["tmp"] = tmp_used_mb()
        out.setdefault("tmp_mb", {})[step] = fig["tmp"]
    if started is not None:
        fig["seconds"] = round(time.time() - started, 1)
        out.setdefault("step_seconds", {})[step] = fig["seconds"]
    return fig


def pressure_of(status: dict | None, b: dict) -> dict:
    """How close a finished run came to each budget: {'memory', 'time', 'tmp' (shares), 'pressured', 'killed', 'worst',
    'text'}. A run that stopped with an error that names memory or time, or that never finished, counts as pressured."""
    st = status or {}
    mem = st.get("memory_mb") or {}
    vals = [v for v in mem.values() if isinstance(v, (int, float))] if isinstance(mem, dict) else []
    peak = mem.get("peak") if isinstance(mem, dict) and isinstance(mem.get("peak"), (int, float)) else (max(vals) if vals else None)
    tmpd = st.get("tmp_mb") or {}
    tvals = [v for v in tmpd.values() if isinstance(v, (int, float))] if isinstance(tmpd, dict) else []
    tmp = max(tvals) if tvals else None
    secs = st.get("seconds")
    budget_s = st.get("budget_seconds") or b.get("seconds")
    share = {"memory": (peak / b["memory_mb"]) if peak and b.get("memory_mb") else None,
             "time": (float(secs) / float(budget_s)) if isinstance(secs, (int, float)) and budget_s else None,
             "tmp": (tmp / b["tmp_mb"]) if tmp is not None and b.get("tmp_mb") else None}
    err = str(st.get("error") or "")
    killed = bool(re.search(r"MemoryError|OutOfTime|out of time|Killed|Timeout|timed out", err, re.I)) or \
        (bool(st.get("started")) and not st.get("finished") and not st.get("skipped"))
    over = {k: v for k, v in share.items() if v is not None and v >= b.get("share", PRESSURE_SHARE)}
    worst = max(over, key=lambda k: over[k]) if over else None
    parts = []
    if peak and b.get("memory_mb"):
        parts.append(f"peak memory {peak:,} MB of a {b['memory_mb']:,} MB budget ({share['memory']:.0%})")
    if isinstance(secs, (int, float)) and budget_s:
        parts.append(f"{secs:,.0f} s of a {float(budget_s):,.0f} s budget ({share['time']:.0%})")
    if tmp is not None and b.get("tmp_mb"):
        parts.append(f"/tmp {tmp:,} MB of {b['tmp_mb']:,} MB ({share['tmp']:.0%})")
    if killed:
        parts.append("the run stopped before it finished")
    return {**{k: (round(v, 3) if v is not None else None) for k, v in share.items()}, "peak_mb": peak, "seconds": secs,
            "tmp_mb": tmp, "pressured": bool(over) or killed, "killed": killed, "worst": worst or ("killed" if killed else None),
            "text": "; ".join(parts)}


def plan_for(level: int, pressure: dict | None = None) -> dict:
    """What a run at this level does, with the plain sentences the panel and the log show."""
    level = max(0, min(level, len(LEVELS) - 1))
    actions = []
    if level >= 1:
        actions.append("Spread the work across runs: one report file per run, and detection in a fresh run of its own.")
    if level >= 2:
        actions.append("Ran the archive at the tighter end of its allowed horizon (never below the windows open items need).")
    if level >= 3:
        actions.append("Left the optional steps for a calm run: the provider tables, the report email, the export-time "
                       "backfill and the weekly self-audit.")
    if level >= 4:
        actions.append("Nothing more can be shed: an alert asks a person to look.")
    return {"level": level, "name": LEVELS[level], "spread": level >= 1, "archive_tight": level >= 2,
            "skip": list(OPTIONAL_STEPS) if level >= 3 else [], "alert": level >= 4, "actions": actions,
            "pressure": pressure or {}}


def plan_next(conn: sqlite3.Connection, prev: dict | None, *, mode: str, budget_s: Any = None, now: Any = None) -> dict:
    """At the start of a run: read the previous run's status (cloud: status.json; server: meta pipeline:last), record its
    figures (selfcheck_cycles), step the regulation level up (it passed 80% of a budget, or stopped before finishing) or,
    after three calm runs, down, and return this run's plan ({'level', 'name', 'spread', 'archive_tight', 'skip',
    'alert', 'actions', 'pressure'}). A status already counted (same 'started') changes nothing."""
    ensure_tables(conn)
    reg = meta_get(conn, K_REGULATE, {}) or {}
    level = int(reg.get("level") or 0)
    calm = int(reg.get("calm") or 0)
    pressure = reg.get("last_pressure") or {}
    if prev and prev.get("started") and prev.get("started") != reg.get("last_started") and not prev.get("skipped"):
        b = budgets(mode, budget_s=prev.get("budget_seconds") or budget_s)
        pressure = pressure_of(prev, b)
        before = level
        if pressure["pressured"]:
            level, calm = min(level + 1, len(LEVELS) - 1), 0
        else:
            calm += 1
            if calm >= CALM_RUNS_TO_STEP_DOWN and level > 0:
                level, calm = level - 1, 0
        _record_cycle(conn, prev, pressure, b, level, mode)
        reg.update(level=level, calm=calm, last_started=prev.get("started"), last_pressure=pressure,
                   last_run={k: prev.get(k) for k in ("started", "finished", "seconds", "budget_seconds", "trigger", "more",
                                                      "error", "deferred")})
        if level != before:
            reg["since"] = now_of(now)
            hist = list(reg.get("changes") or [])[-19:]
            hist.append({"at": now_of(now), "from": LEVELS[before], "to": LEVELS[level], "why": pressure.get("text")})
            reg["changes"] = hist
    plan = plan_for(level, pressure)
    reg["plan"] = {k: plan[k] for k in ("level", "name", "spread", "archive_tight", "skip", "alert", "actions")}
    reg["mode"] = mode
    meta_set(conn, K_REGULATE, reg)
    conn.commit()
    return plan


def _record_cycle(conn: sqlite3.Connection, st: dict, p: dict, b: dict, level: int, mode: str) -> None:
    steps = {"memory_mb": st.get("memory_mb"), "tmp_mb": st.get("tmp_mb"), "step_seconds": st.get("step_seconds"),
             "shed": st.get("shed"), "archive": (st.get("archive") or {}) if isinstance(st.get("archive"), dict) else None}
    mem = st.get("memory_mb") if isinstance(st.get("memory_mb"), dict) else {}
    conn.execute("""INSERT OR REPLACE INTO selfcheck_cycles (started, finished, mode, seconds, budget_s, peak_mb, budget_mb,
                    limit_mb, tmp_mb, tmp_budget_mb, pressure, level, plan, error, steps) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (st.get("started"), st.get("finished"), st.get("mode") or mode, st.get("seconds"),
                  st.get("budget_seconds") or b.get("seconds"), p.get("peak_mb"), b.get("memory_mb"), mem.get("limit"),
                  p.get("tmp_mb"), b.get("tmp_mb"), json.dumps(p, default=str), level,
                  json.dumps(st.get("plan") or {}, default=str), str(st.get("error") or "")[:300] or None,
                  json.dumps(steps, default=str)))
    conn.execute("DELETE FROM selfcheck_cycles WHERE started NOT IN (SELECT started FROM selfcheck_cycles ORDER BY started DESC LIMIT 400)")


def record_did(conn: sqlite3.Connection, did: dict) -> None:
    """What this run actually did with its plan (what it shed, what the archive retired): kept with the regulation state
    so the panel can say it. `did` is a small dict such as {'shed': ['activity'], 'archive': {...}, 'deferred': True}."""
    reg = meta_get(conn, K_REGULATE, {}) or {}
    reg["did"] = dict(did or {}, at=now_of())
    meta_set(conn, K_REGULATE, reg)


def regulation(conn: sqlite3.Connection) -> dict:
    """The regulation state for the panel and the alert."""
    reg = meta_get(conn, K_REGULATE, {}) or {}
    plan = reg.get("plan") or plan_for(0)
    p = reg.get("last_pressure") or {}
    return {"level": plan.get("level", 0), "name": plan.get("name", "calm"), "actions": plan.get("actions") or [],
            "since": reg.get("since"), "last_run": reg.get("last_run") or {}, "pressure": p, "did": reg.get("did") or {},
            "changes": reg.get("changes") or []}


# =====================================================================================================================
# 2. The self-check
# =====================================================================================================================
# id -> (severity, containment, tolerance (new offenders allowed before a hold), title, sentence, what to check)
CHECKS: dict[str, tuple] = {
    "state_valid": ("critical", "hold", 0, "Every open item has one state",
                    "Every open item has exactly one state (work now, waiting, done, accepted, expired or can't be checked).",
                    "States are decided in remitwatch/states.py at detection's save: read the run's errors (meta ema:wiring) and "
                    "an example item's state_basis."),
    "followup_date": ("critical", "hold", 0, "Every waiting item has its follow-up day",
                      "A waiting item without a follow-up day (or with one already past) would never come back.",
                      "states.py sets follow_up_on for every waiting item: look at the example items' state_basis."),
    "open_and_closed": ("critical", "hold", 0, "Nothing is both open and closed",
                        "An item closed (paid, lost or let go) is never listed to work, and an open item never carries a closing note.",
                        "Compare the example items' status, work_state and auto_note."),
    "closure_proof": ("critical", "hold", 0, "Every automatic closure carries its proof",
                      "Each item RemitWatch closed by itself names the payment that closed it (date, amount, source).",
                      "Closures are written by Detector._mark_paid: the example items' evidence.paid_later must hold the payment."),
    "facility_twin": ("critical", "hold", 0, "No surgery-center payment closed a professional line",
                      "A payment on the surgery center's facility claim never closes the clinician's own line.",
                      "Look at the example closures' proof: the claim or EMA claim number it names is a facility claim."),
    "precision_guard": ("critical", "hold", 5, "No item to work was already paid after its denial",
                        "Every item listed Work now still lacks the money: no later payment covers what it claims.",
                        "For each example, the 835 archive or EMA shows a later payment for the same bill, date and code; "
                        "detection should have closed it (resolve_elsewhere, the EMA outcomes) or states.py made it done."),
    "past_window": ("critical", "hold", 0, "No item to work is past every window",
                    "Nothing nobody has worked stays Work now more than the grace period past the payer's last window.",
                    "states.py expires an item 30 days past its last window: compare the example's deadlines and state_basis."),
    "vanished": ("critical", "keep", 0, "No open item disappeared without a reason",
                 "An item people were shown as work leaves the list only by a closure, a replacement for its line, a fold "
                 "into another item, or its remittance being superseded.",
                 "The run no longer produced these items: find the rule that stopped (the signal of the examples). They were "
                 "kept on the worklist with a note; accept the change on the Data tab once it is intended."),
    "money_conservation": ("critical", "hold", 0, "Each month's money adds up to its charges",
                           "For every service month and payer group, charges = insurer paid + patient paid + contractual + "
                           "written off + open +/- credits, to the dollar.",
                           "The cell's pieces are in meta selfcheck:money; a gap means a posting was counted twice or in no piece."),
    "ledger_store": ("critical", "hold", 0, "The store's EMA money equals the posting ledger's",
                     "The money the store's EMA summaries hold equals what the posting ledger holds, month by month and file by file.",
                     "A run that saved one and not the other, or a refresh that stopped part-way: ema_ledger.refresh repairs it "
                     "(the self-repair registry runs it once)."),
    "serving_copy": ("critical", None, 0, "The dashboard's copy shows what the store holds",
                     "The counts and dollars the dashboard's copy holds equal the store's.",
                     "Build the copy again (cloud.save_serving); if it still differs, compare the two findings tables."),
    "deadline_basis": ("warning", None, 0, "Every item to work has a deadline and its basis",
                       "Items to work that are claims or denials carry the date they must be worked by and why.",
                       "detect.py sets appeal_deadline and deadline_basis per signal: the example signals lack one."),
    "counts_lists": ("warning", None, 0, "Counts equal the lists they open",
                     "Each count the pages show equals the length of the list it opens.",
                     "Compare the count's query with the list's query for the named pair."),
    "feed_835": ("warning", None, 0, "The remittances are current",
                 "The newest remittance is at most 10 days old.",
                 "Check the TriZetto download and the Dropbox folder (Data tab)."),
    "feed_ema": ("warning", None, 0, "EMA's reports are current",
                 "The Production report is at most 14 days old and the A/R at most 8.",
                 "Pull the weekly EMA reports (Upload page lists them)."),
    "line_balance": ("warning", None, 0, "EMA's A/R agrees with its postings",
                     "Each line on EMA's newest A/R carries the balance its postings leave, to the dollar.",
                     "A posting missing from the Production export or an A/R pulled before the postings: compare the lines."),
}
CRITICAL = tuple(k for k, v in CHECKS.items() if v[0] == "critical")


def _result(check_id: str, offenders: list | None = None, *, status: str | None = None, info: dict | None = None,
            examples: list | None = None, text: str | None = None) -> dict:
    """One check's result. `offenders` is a list of (key, cents, finding_id or None)."""
    sev, contain, tol, title, sentence, what = CHECKS[check_id]
    offs = offenders or []
    n = len(offs)
    cents = sum(int(c or 0) for _k, c, _f in offs)
    ex = examples if examples is not None else [f for _k, _c, f in offs if f][:EXAMPLES]
    st = status or ("ok" if n == 0 else "fail")
    return {"id": check_id, "severity": sev, "contain": contain, "tolerance": tol, "title": title, "sentence": sentence,
            "what_to_check": what, "status": st, "n": n, "cents": cents, "examples": ex[:EXAMPLES],
            "keys": [(str(k), int(c or 0)) for k, c, _f in offs][:5000], "info": info or {},
            "text": text or (f"{title}: yes" if st == "ok" else
                             f"{title}: no. {_n(n, 'item')} ({usd0(cents)})" if st == "fail" else f"{title}: not checked")}


def _skip(check_id: str, why: str) -> dict:
    return _result(check_id, [], status="skip", info={"why": why}, text=f"{CHECKS[check_id][3]}: not checked ({why})")


def _findings_ok(conn: sqlite3.Connection) -> set[str]:
    return set(_cols(conn, "findings"))


def check_state_valid(conn: sqlite3.Connection, today: str) -> dict:
    cols = _findings_ok(conn)
    if "work_state" not in cols:
        return _skip("state_valid", "the store keeps no states yet")
    marks = ",".join("?" * len(STATES))
    rows = conn.execute(f"""SELECT finding_id, COALESCE(shortfall_cents, 0) FROM findings
                            WHERE COALESCE(stale, 0) = 0 AND status IN ('open','in_progress','appealed')
                              AND (work_state IS NULL OR work_state NOT IN ({marks}))""", STATES).fetchall()
    return _result("state_valid", [(f, c, f) for f, c in rows])


def check_followup_date(conn: sqlite3.Connection, today: str) -> dict:
    cols = _findings_ok(conn)
    if "work_state" not in cols or "follow_up_on" not in cols:
        return _skip("followup_date", "the store keeps no states yet")
    rows = conn.execute("""SELECT finding_id, COALESCE(shortfall_cents, 0) FROM findings
                           WHERE COALESCE(stale, 0) = 0 AND work_state = 'waiting'
                             AND (follow_up_on IS NULL OR follow_up_on = '' OR follow_up_on < ?)""", (today,)).fetchall()
    return _result("followup_date", [(f, c, f) for f, c in rows])


def check_open_and_closed(conn: sqlite3.Connection, today: str) -> dict:
    cols = _findings_ok(conn)
    if "work_state" not in cols:
        return _skip("open_and_closed", "the store keeps no states yet")
    auto = "auto_note" if "auto_note" in cols else "NULL"
    rows = conn.execute(f"""SELECT finding_id, COALESCE(shortfall_cents, 0),
                                   CASE WHEN status IN ('resolved','lost','dismissed') AND work_state IN ('work_now','waiting')
                                        THEN 'closed_but_listed'
                                        WHEN status IN ('open','in_progress','appealed') AND {auto} IS NOT NULL THEN 'open_with_closing_note'
                                        ELSE 'closed_by_analysis_not_done' END
                            FROM findings WHERE COALESCE(stale, 0) = 0 AND (
                                  (status IN ('resolved','lost','dismissed') AND work_state IN ('work_now','waiting'))
                               OR (status IN ('open','in_progress','appealed') AND {auto} IS NOT NULL)
                               OR (status = 'resolved' AND {auto} IS NOT NULL AND COALESCE(work_state, '') <> 'done'))""").fetchall()
    kinds = Counter(k for _f, _c, k in rows)
    return _result("open_and_closed", [(f, c, f) for f, c, _k in rows], info={"by_kind": dict(kinds)})


def has_proof(ev: dict) -> bool:
    """An automatic closure names the payment that closed it: proof rows, or a payment with its date and amount and the
    trace or claim it came on."""
    pl = ev.get("paid_later") if isinstance(ev.get("paid_later"), dict) else {}
    ar = ev.get("archive_rule") if isinstance(ev.get("archive_rule"), dict) else {}
    rows = list(pl.get("proof") or []) + (list(ar.get("proof") or []) if ar.get("action") == "close_with_proof" else [])
    if any(isinstance(r, dict) and (r.get("date") or r.get("amount_cents") is not None) for r in rows):
        return True
    paid = pl.get("paid_cents")
    return bool(pl.get("payment_date")) and isinstance(paid, (int, float)) and paid > 0 and bool(pl.get("trace") or pl.get("claim"))


def check_closure_proof(conn: sqlite3.Connection, today: str) -> dict:
    cols = _findings_ok(conn)
    if "auto_note" not in cols:
        return _skip("closure_proof", "the store keeps no automatic notes")
    offs = []
    for fid, ev, cents in conn.execute("""SELECT finding_id, evidence, COALESCE(shortfall_cents, 0) FROM findings
                                         WHERE status = 'resolved' AND auto_note IS NOT NULL AND COALESCE(stale, 0) = 0"""):
        if not has_proof(_ev(ev)):
            offs.append((fid, cents, fid))
    return _result("closure_proof", offs)


def _facility_links(conn: sqlite3.Connection) -> set[int]:
    links: set[int] = set()
    for table, col in (("ema_link_map", "facility"), ("ema_lines", "facility")):
        if _has_table(conn, table) and col in _cols(conn, table):
            links.update(int(r[0]) for r in conn.execute(f"SELECT DISTINCT link FROM {table} WHERE COALESCE({col}, 0) = 1 "
                                                         "AND link IS NOT NULL"))
    return links


def check_facility_twin(conn: sqlite3.Connection, today: str) -> dict:
    cols = _findings_ok(conn)
    if "auto_note" not in cols:
        return _skip("facility_twin", "the store keeps no automatic notes")
    fac = _facility_links(conn)
    offs = []
    for fid, pcn, ev, cents in conn.execute("""SELECT finding_id, patient_control_number, evidence, COALESCE(shortfall_cents, 0)
                                              FROM findings WHERE status = 'resolved' AND auto_note IS NOT NULL
                                                AND COALESCE(stale, 0) = 0"""):
        if is_facility(pcn):
            continue                    # the surgery center's own item: its own claim may close it
        pl = _ev(ev).get("paid_later") or {}
        if not isinstance(pl, dict):
            continue
        link = pl.get("ema_link")
        try:
            link = int(link) if link is not None else None
        except (TypeError, ValueError):
            link = None
        if is_facility(pl.get("claim")) or (link is not None and link in fac):
            offs.append((fid, cents, fid))
    return _result("facility_twin", offs)


def later_payments(conn: sqlite3.Connection, items: list[dict]) -> dict[str, dict]:
    """For each item (finding dicts with finding_id, patient_control_number, service_date, codes, payment_date, ema_link,
    transaction_key), the money paid on the same bill, date of service and code AFTER the item's own remittance: from the
    835 archive (net of reversals; never a surgery-center claim) and from EMA's payments no 835 carries (never the item's
    own check). {finding_id: {'r835', 'ema', 'total', 'first', 'same_payer'}}. Reads the 835 tables by the claim id
    index both kinds of store keep (claims by claim id, lines by claim), one bill at a time."""
    out: dict[str, dict] = {}
    by_bill: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        b = bill_of(it.get("patient_control_number"))
        if b and it.get("service_date") and _codes_of(it):
            by_bill[b].append(it)
    has_835 = _has_table(conn, "claims") and _has_table(conn, "lines")
    traces: dict[str, str] = {}
    if has_835 and items:
        tks = sorted({it.get("transaction_key") for it in items if it.get("transaction_key")})
        for i in range(0, len(tks), 500):
            part = tks[i:i + 500]
            for tk, tr in conn.execute(f"SELECT transaction_key, trace_number FROM transactions WHERE transaction_key IN "
                                       f"({','.join('?' * len(part))})", part):
                traces[tk] = _digits(tr)
    for bill, its in by_bill.items():
        rows = []
        if has_835:
            hi = bill + "~"
            rows = conn.execute("""SELECT c.patient_control_number, l.service_date, l.procedure_code, l.original_procedure_code,
                                          l.payment_date, COALESCE(l.paid_cents, 0), c.claim_status_code, c.payer_name, l.line_key
                                   FROM claims c JOIN lines l ON l.claim_key = c.claim_key
                                   JOIN transactions t ON t.transaction_key = c.transaction_key
                                   WHERE c.patient_control_number >= ? AND c.patient_control_number < ?
                                     AND COALESCE(t.superseded, 0) = 0""", (bill, hi)).fetchall()
        for it in its:
            codes = set(_codes_of(it))
            after = str(it.get("payment_date") or "")[:10]
            svc = str(it.get("service_date") or "")[:10]
            own_group = it.get("payer_group")
            r835 = same = 0
            first = None
            for pcn, sd, pc, opc, pd, paid, st, payer, lk in rows:
                if bill_of(pcn) != bill or is_facility(pcn) or str(sd or "")[:10] != svc or lk == it.get("line_key"):
                    continue
                if not ({pc, opc} & codes) or not pd or str(pd)[:10] <= after:
                    continue
                if st not in ("1", "2", "3", "19", "20", "21", "22"):
                    continue
                r835 += paid
                if paid > 0:
                    first = min(first or str(pd)[:10], str(pd)[:10])
                    if own_group and _group_kind(payer)[0] == own_group:
                        same += paid
            ema = 0
            link = it.get("ema_link")
            if link and _has_table(conn, "ema_line_payments"):
                own = traces.get(it.get("transaction_key") or "", "")
                for check, posted, cents, in835 in conn.execute(
                        f"""SELECT check_no, posted, COALESCE(ins_paid, 0), COALESCE(in_835, 0) FROM ema_line_payments
                            WHERE link = ? AND svc = ? AND cpt IN ({','.join('?' * len(codes))})""",
                        (int(link), svc, *sorted(codes))):
                    if in835 or not posted or str(posted)[:10] <= after or (own and _digits(check) == own):
                        continue
                    ema += cents
                    if cents > 0:
                        first = min(first or str(posted)[:10], str(posted)[:10])
            out[it["finding_id"]] = {"r835": r835, "ema": ema, "total": r835 + ema, "first": first, "same_payer": same}
    return out


def _work_now_money(conn: sqlite3.Connection, where: str = "", args: Sequence = ()) -> list[dict]:
    cols = _findings_ok(conn)
    want = ["finding_id", "signal", "category", "status", "patient_control_number", "service_date", "procedure_code",
            "original_procedure_code", "billed_code", "payment_date", "payer_group", "shortfall_cents", "expected_cents",
            "paid_cents", "transaction_key", "line_key", "ema_link", "work_state", "appeal_deadline", "legal_deadline",
            "state_basis", "carc", "claim_key", "evidence", "payer_kind", "first_seen"]
    sel = ", ".join(c if c in cols else f"NULL AS {c}" for c in want)
    cur = conn.execute(f"""SELECT {sel} FROM findings WHERE COALESCE(stale, 0) = 0 AND status = 'open'
                           AND work_state = 'work_now' AND category IN ('denial','bundling','downcoding','underpayment') {where}""",
                       tuple(args))
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def check_precision_guard(conn: sqlite3.Connection, today: str) -> dict:
    cols = _findings_ok(conn)
    if "work_state" not in cols:
        return _skip("precision_guard", "the store keeps no states yet")
    items = [it for it in _work_now_money(conn) if (it.get("shortfall_cents") or 0) > 0
             and it.get("signal") not in CLAIM_SIGNALS + NOT_A_DENIAL]
    later = later_payments(conn, items)
    offs = []
    by_signal: Counter = Counter()
    for it in items:
        got = later.get(it["finding_id"])
        short = int(it.get("shortfall_cents") or 0)
        if not got or got["total"] <= 0:
            continue
        if got["total"] >= max(short - 100, int(short * LATER_PAID_SHARE)):
            offs.append((it["finding_id"], min(got["total"], short), it["finding_id"]))
            by_signal[it.get("signal")] += 1
    return _result("precision_guard", offs, info={"checked": len(items), "by_signal": dict(by_signal.most_common(10))})


def _grace_days() -> int:
    try:
        from .states import EXPIRE_GRACE_DAYS
        return int(EXPIRE_GRACE_DAYS)
    except Exception:          # noqa: BLE001  (a store without the states module)
        return 30


def _automatic_open(row: dict) -> bool:
    """The item is Work now by states.py's own 'nobody has worked it' rule (state_rule when the store keeps it)."""
    rule = row.get("state_rule")
    if rule:
        return rule == "open"
    basis = str(row.get("state_basis") or "")
    return basis.startswith(OPEN_BASIS)


def check_past_window(conn: sqlite3.Connection, today: str) -> dict:
    cols = _findings_ok(conn)
    if "work_state" not in cols:
        return _skip("past_window", "the store keeps no states yet")
    grace = _grace_days()
    cut = _add(today, -grace)
    legal = "legal_deadline" if "legal_deadline" in cols else "NULL"
    rule = "state_rule" if "state_rule" in cols else "NULL"
    basis = "state_basis" if "state_basis" in cols else "NULL"
    offs, within = [], 0
    ema_only = ",".join(f"'{s}'" for s in EMA_ONLY)
    for fid, cents, ad, ld, r, b in conn.execute(
            f"""SELECT finding_id, COALESCE(shortfall_cents, 0), appeal_deadline, {legal}, {rule}, {basis} FROM findings
                WHERE COALESCE(stale, 0) = 0 AND status = 'open' AND work_state = 'work_now'
                  AND (category IN ('denial','bundling','downcoding','underpayment') OR signal IN ({ema_only}))
                  AND COALESCE(appeal_deadline, {legal}) IS NOT NULL"""):
        last = max(str(x)[:10] for x in (ad, ld) if x)
        if last >= today:
            continue
        if not _automatic_open({"state_rule": r, "state_basis": b}):
            continue
        if last < cut:
            offs.append((fid, cents, fid))
        else:
            within += 1
    return _result("past_window", offs, info={"grace_days": grace, "past_last_window_in_grace": within})


def check_deadline_basis(conn: sqlite3.Connection, today: str) -> dict:
    cols = _findings_ok(conn)
    if "work_state" not in cols:
        return _skip("deadline_basis", "the store keeps no states yet")
    basis = "deadline_basis" if "deadline_basis" in cols else "NULL"
    rows = conn.execute(f"""SELECT finding_id, COALESCE(shortfall_cents, 0), signal FROM findings
                            WHERE COALESCE(stale, 0) = 0 AND status IN ('open','in_progress','appealed') AND work_state = 'work_now'
                              AND category IN ('denial','bundling','downcoding','underpayment')
                              AND (appeal_deadline IS NULL OR {basis} IS NULL OR {basis} = '')""").fetchall()
    by_signal = Counter(s for _f, _c, s in rows)
    return _result("deadline_basis", [(f, c, f) for f, c, _s in rows], info={"by_signal": dict(by_signal.most_common(15))})


# The pairs a page shows as a count beside the list it opens. A pair is (name, count SQL -> (n, cents), list SQL -> rows of
# (finding_id, cents)), both over findings. The hook-in agent and the pages' owners add their own with register_pair.
COUNT_PAIRS: list[tuple[str, str, str]] = [
    ("work_now", "SELECT COUNT(*), COALESCE(SUM(shortfall_cents), 0) FROM findings WHERE COALESCE(stale,0)=0 AND work_state='work_now'",
     "SELECT finding_id, COALESCE(shortfall_cents, 0) FROM findings WHERE COALESCE(stale,0)=0 AND work_state='work_now'"),
    ("waiting", "SELECT COUNT(*), COALESCE(SUM(shortfall_cents), 0) FROM findings WHERE COALESCE(stale,0)=0 AND work_state='waiting'",
     "SELECT finding_id, COALESCE(shortfall_cents, 0) FROM findings WHERE COALESCE(stale,0)=0 AND work_state='waiting'"),
]
_EXTRA_PAIRS: list[tuple[str, Callable, Callable]] = []


def register_pair(name: str, count_fn: Callable[[sqlite3.Connection, str], tuple], list_fn: Callable[[sqlite3.Connection, str], list]) -> None:
    """Add a page's count and the list it opens to the counts_lists check: count_fn(conn, today) -> (n, cents) and
    list_fn(conn, today) -> [(finding_id, cents), ...]."""
    _EXTRA_PAIRS[:] = [p for p in _EXTRA_PAIRS if p[0] != name] + [(name, count_fn, list_fn)]


def check_counts_lists(conn: sqlite3.Connection, today: str, run_id: str | None = None) -> dict:
    if "work_state" not in _findings_ok(conn):
        return _skip("counts_lists", "the store keeps no states yet")
    offs, pairs = [], []
    for name, csql, lsql in COUNT_PAIRS:
        n, cents = conn.execute(csql).fetchone()
        rows = conn.execute(lsql).fetchall()
        pairs.append((name, (n, cents), rows))
    for name, cf, lf in _EXTRA_PAIRS:
        try:
            pairs.append((name, tuple(cf(conn, today)), list(lf(conn, today))))
        except Exception as exc:       # noqa: BLE001  (a page's own function failing is that pair's failure)
            offs.append((f"{name}:error", 0, None))
            pairs.append((name + ":error", (0, 0), [("", type(exc).__name__)]))
    shown = {}
    for name, (n, cents), rows in pairs:
        ln, lc = len(rows), sum(int(c or 0) for _f, c in rows if isinstance(c, (int, float)))
        shown[name] = {"count": n, "cents": cents, "list": ln, "list_cents": lc}
        if n != ln or (cents or 0) != lc:
            offs.append((name, abs((cents or 0) - lc), None))
    # the Progress page's counts for this run (detection's progress_snapshots) against the items themselves
    if run_id and _has_table(conn, "progress_snapshots"):
        snap = {(c, s): (n, cents) for c, s, n, cents in conn.execute(
            "SELECT category, status, n, cents FROM progress_snapshots WHERE run_id = ?", (run_id,))}
        if snap:
            now = {(c, s): (n, cents) for c, s, n, cents in conn.execute(
                """SELECT category, status, COUNT(*), COALESCE(SUM(shortfall_cents), 0) FROM findings
                   WHERE COALESCE(stale, 0) = 0 GROUP BY category, status""")}
            for k in set(snap) | set(now):
                if snap.get(k, (0, 0)) != now.get(k, (0, 0)):
                    offs.append((f"progress:{k[0]}:{k[1]}", abs(snap.get(k, (0, 0))[1] - now.get(k, (0, 0))[1]), None))
    return _result("counts_lists", offs, info={"pairs": shown}, examples=[])


def check_feed_835(conn: sqlite3.Connection, today: str) -> dict:
    if not _has_table(conn, "transactions"):
        return _skip("feed_835", "no remittances yet")
    newest = conn.execute("SELECT MAX(payment_date) FROM transactions WHERE COALESCE(superseded, 0) = 0").fetchone()[0]
    if not newest:
        return _skip("feed_835", "no remittances yet")
    age = _days(newest, today) or 0
    days = _int_setting("REMITWATCH_STALE_DAYS", STALE_835_DAYS)
    st = "ok" if age <= max(days, 0) else "fail"
    return _result("feed_835", [] if st == "ok" else [("835", 0, None)], status=st, info={"newest": newest, "days": age},
                   text=f"The newest remittance is from {newest} ({age} days ago)")


def check_feed_ema(conn: sqlite3.Connection, today: str) -> dict:
    if not _has_table(conn, "ema_report_files") or not conn.execute("SELECT 1 FROM ema_report_files LIMIT 1").fetchone():
        return _skip("feed_ema", "no EMA report on file yet")
    try:
        from .ema_checks import freshness
        fr = freshness(conn, today)
    except Exception as exc:       # noqa: BLE001  (the reports' module failing is a finding of its own)
        return _skip("feed_ema", f"freshness could not be read ({type(exc).__name__})")
    offs = []
    if fr.get("production_days") is not None and fr["production_days"] > STALE_PRODUCTION_DAYS:
        offs.append(("production", 0, None))
    if fr.get("ar_days") is not None and fr["ar_days"] > STALE_AR_DAYS:
        offs.append(("ar", 0, None))
    return _result("feed_ema", offs, info={k: fr.get(k) for k in ("production_as_of", "production_days", "ar_as_of", "ar_days")},
                   text=f"Production as of {fr.get('production_as_of') or 'never'}, A/R as of {fr.get('ar_as_of') or 'never'}")


ITEM_CHECKS: dict[str, Callable] = {
    "state_valid": check_state_valid, "followup_date": check_followup_date, "open_and_closed": check_open_and_closed,
    "closure_proof": check_closure_proof, "facility_twin": check_facility_twin, "precision_guard": check_precision_guard,
    "past_window": check_past_window, "deadline_basis": check_deadline_basis, "feed_835": check_feed_835,
    "feed_ema": check_feed_ema,
}


def run_checks(conn: sqlite3.Connection, *, today: Any = None, run_id: str | None = None, only: Iterable[str] | None = None) -> list[dict]:
    """The item checks on the saved findings, with no side effects (vanished needs begin_detection's snapshot; the money
    checks need the ledger: check_ledger). A check that fails to run is reported as failed with its error, never
    swallowed."""
    t = today_of(today)
    out = []
    for cid, fn in ITEM_CHECKS.items():
        if only is not None and cid not in set(only):
            continue
        try:
            out.append(fn(conn, t))
        except sqlite3.Error as exc:
            out.append(_result(cid, [("error", 0, None)], info={"error": f"{type(exc).__name__}: {str(exc)[:160]}"},
                               text=f"{CHECKS[cid][3]}: the check could not run ({type(exc).__name__})"))
    if only is None or "counts_lists" in set(only):
        try:
            out.append(check_counts_lists(conn, t, run_id))
        except sqlite3.Error as exc:
            out.append(_result("counts_lists", [("error", 0, None)], info={"error": f"{type(exc).__name__}: {str(exc)[:160]}"}))
    return out


# ---------------------------------------------------------------------------------------------- the run: before and after
SNAPSHOT = "sc_before"


def begin_detection(conn: sqlite3.Connection, *, run_id: str | None = None, today: Any = None) -> dict:
    """Before detection saves: a copy of the findings as people see them now (temp.sc_before, in this connection's
    temporary database), for the vanished check, the track record and safe mode. Cheap: one table copy."""
    ensure_tables(conn)
    conn.execute(f"DROP TABLE IF EXISTS temp.{SNAPSHOT}")
    n = 0
    if _has_table(conn, "findings"):
        conn.execute(f"CREATE TEMP TABLE {SNAPSHOT} AS SELECT * FROM main.findings")
        n = conn.execute(f"SELECT COUNT(*) FROM temp.{SNAPSHOT}").fetchone()[0]
    return {"run_id": run_id, "today": today_of(today), "before": n, "at": now_of(), "t0": time.time()}


def _before_rows(conn: sqlite3.Connection) -> dict[str, dict]:
    if not _has_table(conn, SNAPSHOT, "temp"):
        return {}
    cols = set(_cols(conn, SNAPSHOT, "temp"))
    want = ["finding_id", "signal", "category", "status", "work_state", "follow_up_on", "line_key", "claim_key",
            "patient_control_number", "service_date", "procedure_code", "shortfall_cents", "payer_group", "auto_note",
            "transaction_key", "payment_date", "stale", "evidence", "hint", "appeal_deadline"]
    sel = ", ".join(c if c in cols else f"NULL AS {c}" for c in want)
    cur = conn.execute(f"SELECT {sel} FROM temp.{SNAPSHOT}")
    names = [d[0] for d in cur.description]
    return {r[0]: dict(zip(names, r)) for r in cur.fetchall()}


def _after_rows(conn: sqlite3.Connection) -> dict[str, dict]:
    cols = _findings_ok(conn)
    want = ["finding_id", "signal", "category", "status", "work_state", "follow_up_on", "line_key", "claim_key",
            "patient_control_number", "service_date", "procedure_code", "shortfall_cents", "payer_group", "auto_note",
            "transaction_key", "payment_date", "stale", "evidence", "hint", "appeal_deadline", "recovered_cents"]
    sel = ", ".join(c if c in cols else f"NULL AS {c}" for c in want)
    cur = conn.execute(f"SELECT {sel} FROM findings")
    names = [d[0] for d in cur.description]
    return {r[0]: dict(zip(names, r)) for r in cur.fetchall()}


def check_vanished(conn: sqlite3.Connection, before: dict[str, dict], after: dict[str, dict]) -> dict:
    """Open items people were shown as work (Work now or Waiting) before the run that the run no longer has, with no
    route out: no item now for the same line or claim and code, not folded into another item, their remittance not
    superseded."""
    if not before:
        return _skip("vanished", "no items before this run")
    lines = {r["line_key"] for r in after.values() if r.get("line_key")}
    claim_codes = {(r.get("claim_key"), r.get("procedure_code")) for r in after.values() if r.get("claim_key")}
    service = {(bill_of(r.get("patient_control_number")), r.get("service_date"), r.get("procedure_code"))
               for r in after.values() if r.get("patient_control_number")}
    folded: set[str] = set()
    for r in after.values():
        ev = r.get("evidence")
        if ev and "folded" in str(ev):
            for x in _ev(ev).get("folded") or []:
                if isinstance(x, dict) and x.get("finding_id"):
                    folded.add(str(x["finding_id"]))
                elif isinstance(x, str):
                    folded.add(x)
    superseded: set[str] = set()
    tks = sorted({r.get("transaction_key") for f, r in before.items() if f not in after and r.get("transaction_key")})
    for i in range(0, len(tks), 500):
        part = tks[i:i + 500]
        have = {tk: sup for tk, sup in conn.execute(f"SELECT transaction_key, COALESCE(superseded, 0) FROM transactions "
                                                    f"WHERE transaction_key IN ({','.join('?' * len(part))})", part)}
        superseded.update(tk for tk in part if have.get(tk, 1))      # gone from the store, or superseded
    offs = []
    routes: Counter = Counter()
    for fid, r in before.items():
        if fid in after or r.get("stale") or r.get("status") not in OPEN_STATUSES or r.get("work_state") not in LISTED:
            continue
        if r.get("line_key") and r["line_key"] in lines:
            routes["same_line"] += 1
            continue
        if r.get("claim_key") and (r["claim_key"], r.get("procedure_code")) in claim_codes:
            routes["same_claim_code"] += 1
            continue
        if r.get("patient_control_number") and (bill_of(r["patient_control_number"]), r.get("service_date"), r.get("procedure_code")) in service:
            routes["same_service"] += 1
            continue
        if fid in folded:
            routes["folded"] += 1
            continue
        if r.get("transaction_key") and r["transaction_key"] in superseded:
            routes["superseded"] += 1
            continue
        offs.append((fid, int(r.get("shortfall_cents") or 0), fid))
    by_signal = Counter(before[f]["signal"] for f, _c, _x in offs)
    return _result("vanished", offs, info={"routes": dict(routes), "by_signal": dict(by_signal.most_common(10))})


def _baseline(conn: sqlite3.Connection) -> dict[str, set[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    for cid, key in conn.execute("SELECT check_id, key FROM selfcheck_baseline"):
        out[cid].add(key)
    return out


def _set_baseline(conn: sqlite3.Connection, results: list[dict], at: str, accepted_by: str | None = None,
                  only: Iterable[str] | None = None) -> None:
    keep = set(only) if only is not None else None
    for r in results:
        if r["severity"] != "critical" or r["status"] == "skip" or (keep is not None and r["id"] not in keep):
            continue
        if accepted_by is None:
            conn.execute("DELETE FROM selfcheck_baseline WHERE check_id = ? AND accepted_by IS NULL", (r["id"],))
        conn.executemany("INSERT OR IGNORE INTO selfcheck_baseline (check_id, key, cents, since, accepted_by) VALUES (?,?,?,?,?)",
                         [(r["id"], k, c, at, accepted_by) for k, c in r["keys"]])


def _new_keys(r: dict, base: dict[str, set[str]]) -> list[tuple[str, int]]:
    known = base.get(r["id"], set())
    return [(k, c) for k, c in r["keys"] if k not in known]


def _pack(rows: list) -> list[bytes]:
    raw = json.dumps(rows, default=str).encode()
    blob = zlib.compress(raw, 6)
    return [blob[i:i + (4 << 20)] for i in range(0, len(blob), 4 << 20)] or [b""]


def _unpack(parts: list[bytes]) -> list:
    if not parts:
        return []
    return json.loads(zlib.decompress(b"".join(parts)).decode())


def _hold(conn: sqlite3.Connection, run_id: str, at: str) -> dict:
    """Safe mode: keep the run's items (compressed, selfcheck_held) and events, put the items back as they were before the
    run (temp.sc_before: the last good run's, with every person's change since), take this run's events out of the item
    history (they describe changes that were held), and rebuild this run's progress counts from what people see."""
    cols = _cols(conn, "findings")
    bcols = set(_cols(conn, SNAPSHOT, "temp"))
    use = [c for c in cols if c in bcols]
    held_rows = [list(r) for r in conn.execute(f"SELECT {', '.join(cols)} FROM findings")]
    closures = conn.execute("""SELECT COUNT(*) FROM finding_events WHERE run_id = ? AND kind = 'paid_on_later_remit'""",
                            (run_id,)).fetchone()[0] if _has_table(conn, "finding_events") else 0
    ev_cols = _cols(conn, "finding_events")
    events = [list(r) for r in conn.execute(f"SELECT {', '.join(ev_cols)} FROM finding_events WHERE run_id = ?", (run_id,))] \
        if ev_cols else []
    conn.execute("DELETE FROM selfcheck_held")
    for kind, payload in (("findings", {"cols": cols, "rows": held_rows}), ("events", {"cols": ev_cols, "rows": events})):
        for i, part in enumerate(_pack(payload)):
            conn.execute("INSERT INTO selfcheck_held (run_id, kind, part, data) VALUES (?,?,?,?)", (run_id, kind, i, part))
    conn.execute("DELETE FROM findings")
    conn.execute(f"INSERT INTO findings ({', '.join(use)}) SELECT {', '.join(use)} FROM temp.{SNAPSHOT}")
    if ev_cols:
        conn.execute("DELETE FROM finding_events WHERE run_id = ?", (run_id,))
    if _has_table(conn, "progress_snapshots"):
        conn.execute("DELETE FROM progress_snapshots WHERE run_id = ?", (run_id,))
        conn.execute("""INSERT INTO progress_snapshots (run_id, at, category, status, n, cents)
                        SELECT ?, ?, category, status, COUNT(*), COALESCE(SUM(shortfall_cents), 0) FROM findings
                        WHERE COALESCE(stale, 0) = 0 GROUP BY category, status""", (run_id, at))
    return {"held_items": len(held_rows), "held_events": len(events), "held_closures": closures,
            "kept_items": conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0]}


KEPT_HINT = ("Kept by RemitWatch's self-check: the newest analysis no longer lists this item and nothing explains why "
             "(no payment, no replacement, no fold). It stays until a person checks it.")


def _keep_vanished(conn: sqlite3.Connection, fids: list[str], run_id: str, at: str) -> int:
    """Put back the items that disappeared with no reason, as they were before the run, with a note; record it."""
    if not fids:
        return 0
    cols = _cols(conn, "findings")
    bcols = set(_cols(conn, SNAPSHOT, "temp"))
    use = [c for c in cols if c in bcols]
    n = 0
    for i in range(0, len(fids), 500):
        part = fids[i:i + 500]
        conn.execute(f"INSERT OR IGNORE INTO findings ({', '.join(use)}) SELECT {', '.join(use)} FROM temp.{SNAPSHOT} "
                     f"WHERE finding_id IN ({','.join('?' * len(part))})", part)
        n += len(part)
    if "hint" in cols:
        for i in range(0, len(fids), 500):
            part = fids[i:i + 500]
            conn.execute(f"UPDATE findings SET hint = ? WHERE finding_id IN ({','.join('?' * len(part))})", (KEPT_HINT, *part))
    if _has_table(conn, "finding_events"):
        conn.executemany("""INSERT INTO finding_events (finding_id, at, kind, from_status, to_status, amount_cents, actor, note, run_id)
                            VALUES (?, ?, 'kept_by_selfcheck', NULL, NULL, NULL, 'system', ?, ?)""",
                         [(f, at, "The analysis no longer listed it with no reason; kept so nothing is dropped silently", run_id)
                          for f in fids])
    return n


def after_detection(conn: sqlite3.Connection, ctx: dict | None = None, *, mode: str = "server", today: Any = None,
                    run_id: str | None = None) -> dict:
    """After detection saved (inside Detector.run, before the hide rules and the decision log): run the checks, keep
    what disappeared with no reason, decide safe mode, update the track record and the history. Returns
    {'held', 'first', 'failing', 'new', 'kept', 'checks': [...summaries], 'safe_mode': {...}}."""
    ensure_tables(conn)
    ctx = ctx or {}
    t = today_of(today or ctx.get("today"))
    rid = run_id or ctx.get("run_id") or ("run-" + now_of()[:19])
    at = now_of()
    before = _before_rows(conn)
    after = _after_rows(conn)
    results = run_checks(conn, today=t, run_id=rid)
    van = check_vanished(conn, before, after)
    results.append(van)
    money = meta_get(conn, K_MONEY, {}) or {}
    for m in (money.get("checks") or []):
        if isinstance(m, dict) and m.get("id") in CHECKS and m.get("current", True):
            results.append(dict(m, keys=[tuple(k) for k in m.get("keys") or []]))
    first = conn.execute("SELECT COUNT(*) FROM selfcheck_runs").fetchone()[0] == 0
    base = _baseline(conn)
    new_by: dict[str, list] = {}
    hold_ids: list[str] = []
    for r in results:
        if r["severity"] != "critical" or r["status"] != "fail":
            continue
        nk = _new_keys(r, base)
        new_by[r["id"]] = nk
        r["new"] = len(nk)
        if r["contain"] == "hold" and len(nk) > r["tolerance"]:
            hold_ids.append(r["id"])
    kept = 0
    van_new = [k for k, _c in new_by.get("vanished", [])]
    held_info: dict = {}
    can_hold = not first and len(before) > 0 and _has_table(conn, SNAPSHOT, "temp")
    held = bool(hold_ids) and can_hold
    if held:
        held_info = _hold(conn, rid, at)
    elif van_new and not first:
        kept = _keep_vanished(conn, van_new, rid, at)
    if not held:
        # this run is the last good one: its offenders are the baseline (a person's acceptances stay)
        _set_baseline(conn, results, at)
        if before:
            _update_track(conn, before, _after_rows(conn), t, rid)
        conn.execute("DELETE FROM selfcheck_held")
    safe = _safe_state(conn, held, hold_ids, results, rid, at, held_info, first, can_hold)
    summary = _store_results(conn, results, rid, t, mode, held, first, kept, at)
    conn.execute(f"DROP TABLE IF EXISTS temp.{SNAPSHOT}")
    conn.commit()
    return {"run_id": rid, "held": held, "first": first, "hold_checks": hold_ids, "kept": kept,
            "failing": [r["id"] for r in results if r["status"] == "fail"], "new": {k: len(v) for k, v in new_by.items()},
            "checks": summary, "safe_mode": safe, "seconds": round(time.time() - ctx.get("t0", time.time()), 1)}


def _safe_state(conn, held, hold_ids, results, rid, at, held_info, first, can_hold) -> dict:
    prev = meta_get(conn, K_SAFE, {}) or {}
    if held:
        why = [r for r in results if r["id"] in hold_ids]
        state = {"on": True, "since": prev.get("since") if prev.get("on") else at, "run_id": rid, "at": at,
                 "checks": [{"id": r["id"], "title": r["title"], "n": r["n"], "new": r.get("new", 0), "cents": r["cents"],
                             "examples": r["examples"][:EXAMPLES], "what_to_check": r["what_to_check"]} for r in why],
                 **held_info,
                 "text": ("RemitWatch kept the worklist as it was before this run and held the run's automatic closures, "
                          f"because {', '.join(r['title'].lower() for r in why)} failed. Nothing was lost: the new results "
                          "are kept for diagnosis. The next run that passes releases it."),
                 "runs_held": int(prev.get("runs_held") or 0) + 1 if prev.get("on") else 1}
    else:
        state = {"on": False, "at": at, "run_id": rid}
        if prev.get("on"):
            state["released"] = {"at": at, "run_id": rid, "since": prev.get("since"), "runs_held": prev.get("runs_held")}
        elif prev.get("released"):
            state["released"] = prev["released"]
        blocked = [r["id"] for r in results if r["severity"] == "critical" and r["status"] == "fail" and r.get("new", 0) > r["tolerance"]
                   and r["contain"] == "hold"]
        if blocked and not can_hold:
            state["not_held"] = {"checks": blocked, "why": "first run of the self-check: it sets the baseline" if first
                                 else "nothing to keep from before this run"}
    meta_set(conn, K_SAFE, state)
    return state


def _store_results(conn, results, rid, today, mode, held, first, kept, at) -> list[dict]:
    summary = []
    for r in results:
        summary.append({k: r.get(k) for k in ("id", "severity", "status", "n", "cents", "new", "title", "sentence", "text",
                                              "what_to_check", "examples", "info")})
    failing = sum(1 for r in results if r["status"] == "fail")
    critical = sum(1 for r in results if r["status"] == "fail" and r["severity"] == "critical")
    conn.execute("""INSERT OR REPLACE INTO selfcheck_runs (run_id, at, today, mode, held, first, failing, critical, checks, summary)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                 (rid, at, today, mode, int(held), int(first), failing, critical,
                  json.dumps({r["id"]: r["status"] for r in results}),
                  json.dumps({"kept": kept, "version": VERSION}, default=str)))
    conn.execute("DELETE FROM selfcheck_runs WHERE run_id NOT IN (SELECT run_id FROM selfcheck_runs ORDER BY at DESC LIMIT 400)")
    meta_set(conn, K_LAST, {"run_id": rid, "at": at, "today": today, "mode": mode, "held": held, "first": first, "kept": kept,
                            "version": VERSION, "checks": summary})
    hist = list(meta_get(conn, K_HISTORY, []) or [])
    hist.append({"run_id": rid, "at": at, "held": held, "checks": {r["id"]: r["status"] for r in results}})
    meta_set(conn, K_HISTORY, hist[-HISTORY_RUNS:])
    return summary


def accept_findings(conn: sqlite3.Connection, user: str, check_ids: Iterable[str] | None = None) -> dict:
    """A person accepts what the latest checks found (a business decision on the Data tab: "this change is intended", or
    "use the new results"): those offenders become the baseline, recorded with who and when, so they no longer hold a
    run or keep items. Returns what was accepted."""
    ensure_tables(conn)
    last = meta_get(conn, K_LAST, {}) or {}
    ids = set(check_ids) if check_ids is not None else set(CRITICAL)
    at = now_of()
    accepted = {}
    for c in last.get("checks") or []:
        if c.get("id") in ids and c.get("status") == "fail":
            accepted[c["id"]] = c.get("n")
    # the offenders' keys are in the held copy or recomputed by the next run: record the acceptance itself; the next run
    # adds its offenders for these checks to the baseline under this person's name
    pend = meta_get(conn, "selfcheck:accepted", {}) or {}
    for cid in accepted:
        pend[cid] = {"by": str(user or "")[:80], "at": at}
    meta_set(conn, "selfcheck:accepted", pend)
    conn.commit()
    return {"accepted": accepted, "by": user, "at": at}


def _apply_acceptances(conn: sqlite3.Connection, results: list[dict]) -> None:
    pend = meta_get(conn, "selfcheck:accepted", {}) or {}
    if not pend:
        return
    for r in results:
        a = pend.get(r["id"])
        if a and r["status"] == "fail":
            conn.executemany("INSERT OR REPLACE INTO selfcheck_baseline (check_id, key, cents, since, accepted_by) VALUES (?,?,?,?,?)",
                             [(r["id"], k, c, a["at"], a["by"]) for k, c in r["keys"]])
    conn.execute("DELETE FROM meta WHERE key = 'selfcheck:accepted'")


def held_copy(conn: sqlite3.Connection) -> dict:
    """The items and events of the run safe mode held, for the diagnostic bundle: {'run_id', 'findings': {cols, rows},
    'events': {cols, rows}} (an empty dict when nothing is held)."""
    out: dict = {}
    if not _has_table(conn, "selfcheck_held"):
        return out
    parts: dict[tuple, list] = defaultdict(list)
    for rid, kind, _p, data in conn.execute("SELECT run_id, kind, part, data FROM selfcheck_held ORDER BY run_id, kind, part"):
        parts[(rid, kind)].append(data)
        out["run_id"] = rid
    for (_rid, kind), ps in parts.items():
        out[kind] = _unpack(ps)
    return out


def safe_mode(conn: sqlite3.Connection) -> dict:
    """The current safe-mode state ({'on': False} when the last run stood)."""
    return meta_get(conn, K_SAFE, {"on": False}) or {"on": False}


# =====================================================================================================================
# 4. The track record (from what changed between two runs)
# =====================================================================================================================
def _closure_rule(ev: Any) -> str:
    e = _ev(ev)
    ar = e.get("archive_rule") if isinstance(e.get("archive_rule"), dict) else {}
    if ar.get("action") == "close_with_proof":
        return "archive:" + str(ar.get("rule") or "rule")
    pl = e.get("paid_later") if isinstance(e.get("paid_later"), dict) else {}
    src = str(pl.get("source") or "")
    if pl.get("proof") or "Production" in src:
        return "ema:paid_in_production"
    if "EMA" in str(pl.get("trace") or "") or src:
        return "ema:shows_paid"
    if pl.get("how") and "already paid" in str(pl.get("how")):
        return "remittance:paid_before"
    return "remittance:paid_later"


def _update_track(conn: sqlite3.Connection, before: dict[str, dict], after: dict[str, dict], today: str, run_id: str) -> dict:
    """What happened to each item since the run before: waits that ended (paid, denied again, nothing came), automatic
    closures and their reopenings, items let go that were then paid, deadlines missed while Work now."""
    n: Counter = Counter()
    open_waits = {f: s for f, s in conn.execute("SELECT finding_id, started FROM selfcheck_track WHERE kind='waiting' AND ended IS NULL")}
    closures = {f: s for f, s in conn.execute("SELECT finding_id, started FROM selfcheck_track WHERE kind='closure' AND ended IS NULL")}
    for fid, a in after.items():
        b = before.get(fid)
        ws_b = (b or {}).get("work_state")
        ws_a = a.get("work_state")
        closed_a = a.get("status") == "resolved" and a.get("auto_note")
        closed_b = b is not None and b.get("status") == "resolved" and b.get("auto_note")
        # waits
        if ws_a == "waiting" and fid not in open_waits:
            conn.execute("INSERT OR IGNORE INTO selfcheck_track (finding_id, kind, started, payer_group, cents, run_id) VALUES (?,?,?,?,?,?)",
                         (fid, "waiting", today, a.get("payer_group"), a.get("shortfall_cents"), run_id))
            n["wait_started"] += 1
        elif ws_a != "waiting" and fid in open_waits:
            if closed_a or ws_a == "done":
                outcome = "paid"
            elif b is not None and a.get("payment_date") and str(a.get("payment_date")) > str(b.get("payment_date") or ""):
                outcome = "denied_again"
            elif ws_a == "expired":
                outcome = "expired"
            else:
                outcome = "no_answer"
            conn.execute("UPDATE selfcheck_track SET ended=?, outcome=?, days=? WHERE finding_id=? AND kind='waiting' AND started=?",
                         (today, outcome, _days(open_waits[fid], today), fid, open_waits[fid]))
            n["wait_" + outcome] += 1
        # closures and reopenings
        if closed_a and not closed_b and fid not in closures:
            conn.execute("""INSERT OR IGNORE INTO selfcheck_track (finding_id, kind, started, payer_group, rule, cents, run_id)
                            VALUES (?,?,?,?,?,?,?)""", (fid, "closure", today, a.get("payer_group"), _closure_rule(a.get("evidence")),
                                                        a.get("recovered_cents") or a.get("shortfall_cents"), run_id))
            n["closed"] += 1
        elif closed_b and not closed_a and a.get("status") in OPEN_STATUSES:
            started = closures.get(fid)
            if started:
                conn.execute("UPDATE selfcheck_track SET ended=?, outcome='reopened', days=? WHERE finding_id=? AND kind='closure' AND started=?",
                             (today, _days(started, today), fid, started))
            else:
                conn.execute("""INSERT OR IGNORE INTO selfcheck_track (finding_id, kind, started, ended, outcome, payer_group, rule, cents, run_id)
                                VALUES (?,?,?,?,?,?,?,?,?)""", (fid, "closure", today, today, "reopened", a.get("payer_group"),
                                                                 _closure_rule(b.get("evidence")), a.get("shortfall_cents"), run_id))
            n["reopened"] += 1
        # let go, then paid
        if a.get("status") in ("dismissed", "lost") and str(a.get("hint") or "").startswith("A later payment was found") and \
                not str((b or {}).get("hint") or "").startswith("A later payment was found"):
            conn.execute("""INSERT OR IGNORE INTO selfcheck_track (finding_id, kind, started, outcome, payer_group, cents, run_id)
                            VALUES (?,?,?,?,?,?,?)""", (fid, "let_go_paid", today, a.get("status"), a.get("payer_group"),
                                                         a.get("shortfall_cents"), run_id))
            n["let_go_paid"] += 1
        # a deadline missed: expired while Work now
        if ws_b == "work_now" and ws_a == "expired":
            conn.execute("""INSERT OR IGNORE INTO selfcheck_track (finding_id, kind, started, outcome, payer_group, cents, run_id)
                            VALUES (?,?,?,?,?,?,?)""", (fid, "missed_deadline", today, "expired", a.get("payer_group"),
                                                         (b or {}).get("shortfall_cents"), run_id))
            n["missed_deadline"] += 1
    return dict(n)


def track_record(conn: sqlite3.Connection, *, today: Any = None, save: bool = True) -> dict:
    """RemitWatch's own record, from selfcheck_track: closures reopened as an error rate by rule; waits by outcome and how
    long they took, with each payer group's 90th percentile (meta selfcheck:answer_days, n >= 20); items let go then paid;
    deadlines missed, week by week. Saved in meta selfcheck:track for the panel."""
    ensure_tables(conn)
    t = today_of(today)
    by_rule: dict[str, dict] = {}
    for rule, n, reopened in conn.execute("""SELECT COALESCE(rule, 'unknown'), COUNT(*), SUM(outcome = 'reopened')
                                             FROM selfcheck_track WHERE kind = 'closure' GROUP BY 1"""):
        by_rule[rule] = {"closures": n, "reopened": reopened or 0, "rate": round((reopened or 0) / n, 4) if n else None}
    waits = Counter()
    days_by_group: dict[str, list] = defaultdict(list)
    for grp, outcome, d in conn.execute("SELECT payer_group, outcome, days FROM selfcheck_track WHERE kind='waiting' AND ended IS NOT NULL"):
        waits[outcome] += 1
        if outcome in ("paid", "denied_again") and d is not None:
            days_by_group[grp or "Unknown"].append(d)
    answer = {g: {"p90": _pct(v, 90), "p50": _pct(v, 50), "n": len(v)} for g, v in days_by_group.items()}
    open_waits = conn.execute("SELECT COUNT(*) FROM selfcheck_track WHERE kind='waiting' AND ended IS NULL").fetchone()[0]
    let_go = conn.execute("SELECT COUNT(*), COALESCE(SUM(cents), 0) FROM selfcheck_track WHERE kind='let_go_paid'").fetchone()
    weeks: dict[str, dict] = {}
    for started, kind, cents in conn.execute("""SELECT started, kind, cents FROM selfcheck_track
                                               WHERE kind IN ('missed_deadline', 'closure', 'let_go_paid') AND started >= ?""",
                                            (_add(t, -84) or "",)):
        wk = _week_of(started)
        w = weeks.setdefault(wk, {"week": wk, "missed_n": 0, "missed_cents": 0, "closed_n": 0, "let_go_paid_n": 0})
        if kind == "missed_deadline":
            w["missed_n"] += 1
            w["missed_cents"] += int(cents or 0)
        elif kind == "closure":
            w["closed_n"] += 1
        else:
            w["let_go_paid_n"] += 1
    reopened = conn.execute("""SELECT COUNT(*) FROM selfcheck_track WHERE kind='closure' AND outcome='reopened'""").fetchone()[0]
    closed = conn.execute("SELECT COUNT(*) FROM selfcheck_track WHERE kind='closure'").fetchone()[0]
    out = {"at": now_of(), "closures": closed, "reopened": reopened,
           "closure_error_rate": round(reopened / closed, 4) if closed else None, "by_rule": by_rule,
           "waits": dict(waits), "waiting_now": open_waits, "answer_days": answer,
           "let_go_paid": {"n": let_go[0], "cents": let_go[1]},
           "weeks": [weeks[k] for k in sorted(weeks)][-12:]}
    if save:
        meta_set(conn, K_TRACK, out)
        meta_set(conn, K_ANSWER, {g: v for g, v in answer.items() if v["n"] >= 20})
    return out


def _week_of(iso: Any) -> str:
    d = _d(iso)
    if not d:
        return ""
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


# =====================================================================================================================
# 8. Money: the ledger against the store, conservation per month and payer group, the A/R against the postings
# =====================================================================================================================
def check_ledger(conn: sqlite3.Connection, ledger: Any, *, today: Any = None, sample: int = 2000, seed: str | None = None) -> dict:
    """When the posting ledger is open (a server cycle; a serverless run that applies a Production file): the store's EMA
    money against the ledger's (ledger_store), money conservation per service month and payer group from the ledger
    (money_conservation), and EMA's A/R against its postings (line_balance). Saved in meta selfcheck:money with the
    ledger's seq; after_detection counts these results while the store's summaries are of that seq. Returns the dict."""
    ensure_tables(conn)
    t = today_of(today)
    lc = ledger.conn if hasattr(ledger, "conn") else ledger
    seq = None
    try:
        seq = int(ledger.seq()) if hasattr(ledger, "seq") else None
    except Exception:          # noqa: BLE001
        seq = None
    store_seq = meta_get(conn, "ema:ledger_seq")
    out: dict = {"at": now_of(), "today": t, "ledger_seq": seq, "store_seq": store_seq}
    behind = seq is not None and store_seq is not None and str(store_seq) != str(seq)
    checks = []
    if behind:
        checks.append(_skip("ledger_store", f"the store's EMA summaries are of ledger revision {store_seq}, the ledger is at {seq}: "
                                            "the next refresh brings them level"))
    else:
        checks.append(_ledger_store(conn, lc, sample=sample, seed=seed or t))
    cons = _conservation(conn, lc, t)
    checks.append(cons.pop("check"))
    out["conservation"] = cons
    bal = _ar_balance(conn, lc, t)
    checks.append(bal.pop("check"))
    out["ar_balance"] = bal
    for c in checks:
        c["current"] = True
    out["checks"] = checks
    meta_set(conn, K_MONEY, out)
    conn.commit()
    return out


def _ledger_store(conn: sqlite3.Connection, lc: sqlite3.Connection, *, sample: int, seed: str) -> dict:
    """ema_months (posted month x EMA payer x measure) against a fresh sum of the ledger's postings; the catalog's live rows
    against the ledger's files; and a sample of the store's kept lines against their postings."""
    offs: list = []
    info: dict = {}
    if not _has_table(lc, "postings") or not _has_table(conn, "ema_months"):
        return _skip("ledger_store", "no posting ledger or no EMA summaries")
    # 1. money by posted month and payer, measure by measure (the measures ema_ledger._months writes from every posting)
    store = defaultdict(int)
    for month, payer, measure, cents in conn.execute("""SELECT month, payer, measure, SUM(cents) FROM ema_months
                                                        WHERE measure IN ('ins_paid','pat_paid','charges','product')
                                                        GROUP BY 1, 2, 3"""):
        store[(month, payer, measure)] += int(cents or 0)
    led = defaultdict(int)
    for month, payer, product, ins, pat, chg in lc.execute(
            """SELECT substr(p.posted, 1, 7), COALESCE(n.name, ''), ((p.flags & 1) <> 0), SUM(COALESCE(p.ins_paid, 0)),
                      SUM(COALESCE(p.pat_paid, 0)), SUM(COALESCE(p.charges, 0))
               FROM postings p LEFT JOIN names n ON n.id = p.payer GROUP BY 1, 2, 3"""):
        if ins:
            led[(month, payer, "ins_paid")] += int(ins)
        if pat:
            led[(month, payer, "pat_paid")] += int(pat)
        if chg:
            led[(month, payer, "product" if product else "charges")] += int(chg)
    diff_cells = 0
    for k in set(store) | set(led):
        d = store.get(k, 0) - led.get(k, 0)
        if abs(d) > 100:
            diff_cells += 1
            offs.append((f"month:{k[0]}|{k[2]}|{hashlib.sha256(k[1].encode()).hexdigest()[:10]}", abs(d), None))
    info["month_cells"] = len(set(store) | set(led))
    info["month_cells_off"] = diff_cells
    # 2. the files: what the catalog says each applied file holds against the ledger
    if _has_table(conn, "ema_report_files") and _has_table(lc, "ledger_files"):
        cat = {fid: (st, live) for fid, st, live in conn.execute(
            "SELECT file_id, status, live_rows FROM ema_report_files WHERE report = 'production'")}
        for fid, st, live, src in lc.execute("SELECT file_id, status, live_rows, src FROM ledger_files"):
            got = lc.execute("SELECT COUNT(*) FROM postings WHERE src = ?", (src,)).fetchone()[0]
            if st in ("current", "partly_replaced") and (live or 0) != got:
                offs.append((f"file:{str(fid)[:16]}:ledger", abs((live or 0) - got), None))
            c = cat.get(fid)
            if c is not None and st in ("current", "partly_replaced") and c[1] is not None and int(c[1]) != got:
                offs.append((f"file:{str(fid)[:16]}:catalog", abs(int(c[1]) - got), None))
        info["files"] = len(cat)
    # 3. a sample of the kept lines (the same lines for one day, a different sample each day)
    if _has_table(conn, "ema_lines"):
        total = conn.execute("SELECT COUNT(*) FROM ema_lines").fetchone()[0]
        mod = max(1, total // max(sample, 1))
        pick = int(hashlib.sha256(seed.encode()).hexdigest(), 16) % mod
        rows = conn.execute("""SELECT link, svc, cpt, COALESCE(charges, 0), COALESCE(ins_paid, 0), COALESCE(contractual, 0),
                                      COALESCE(noncontractual, 0) FROM ema_lines WHERE (link % ?) = ? LIMIT ?""",
                            (mod, pick, sample)).fetchall()
        by_link: dict[int, list] = defaultdict(list)
        for r in rows:
            by_link[int(r[0])].append(r)
        off_lines = 0
        links = sorted(by_link)
        for i in range(0, len(links), 400):
            part = links[i:i + 400]
            sums = {(int(l), s, c or ""): (a, b, x, y) for l, s, c, a, b, x, y in lc.execute(
                f"""SELECT link, svc, COALESCE(cpt, ''), SUM(COALESCE(charges, 0)), SUM(COALESCE(ins_paid, 0)),
                           SUM(COALESCE(contractual, 0)), SUM(COALESCE(noncontractual, 0)) FROM postings
                    WHERE link IN ({','.join('?' * len(part))}) GROUP BY link, svc, COALESCE(cpt, '')""", part)}
            for link in part:
                for r in by_link[link]:
                    got = sums.get((int(r[0]), r[1], r[2] or ""), (0, 0, 0, 0))
                    d = sum(abs(int(a) - int(b)) for a, b in zip(r[3:], got))
                    if d > 100:
                        off_lines += 1
                        offs.append((f"line:{r[0]}|{r[1]}|{r[2]}", d, None))
        info["lines_sampled"] = len(rows)
        info["lines_off"] = off_lines
    return _result("ledger_store", offs, info=info, examples=[])


def _reason_class_fn() -> Callable:
    try:
        from .ema_ledger import reason_class
        return reason_class
    except Exception:          # noqa: BLE001  (a store without the ledger module)
        return lambda grp, code, kind=None: "none" if not grp else ("patient" if grp == "PR" else "other")


ROUTINE_CLASSES = ("none", "routine", "patient", "ema_kind")


def _conservation(conn: sqlite3.Connection, lc: sqlite3.Connection, today: str) -> dict:
    """Money conservation per service month and payer group, from every posting of the ledger. Each line (EMA claim number,
    date of service, code) belongs to the cell of its service month and the group of its newest primary payer; each
    posting's money goes to one piece by its own column and reason:
      charges = insurer paid + patient paid + contractual (routine) + written off (by reason: EMA's rule at posting, a person
      later, timely filing) + patient adjustments + open (lines with a balance) - credits (lines paid past their charge)
    The pieces are summed by separate rules over the postings and the open and credit pieces from the lines' own balances,
    so a posting counted twice or in no piece shows as a cell whose pieces do not add up (critical past $1). Postings that
    belong to no line (patient money with no claim number that matches no single line) are a visible gap of their own,
    by cell. Stored in selfcheck_money."""
    if not _has_table(lc, "postings"):
        return {"check": _skip("money_conservation", "no posting ledger"), "cells": 0}
    from . import ema_reports as er
    rc = _reason_class_fn()
    lc.create_function("sc_class", 3, lambda g, c, k: rc(g, c, k), deterministic=True)
    names = {i: n for i, n in lc.execute("SELECT id, name FROM names")}
    group_cache: dict[Any, str] = {}

    def group(name_id: Any) -> str:
        if name_id not in group_cache:
            nm = names.get(name_id)
            try:
                group_cache[name_id] = er.payer_group_of(conn, nm)[0] if nm else "No payer named"
            except Exception:          # noqa: BLE001
                group_cache[name_id] = str(nm or "No payer named")
        return group_cache[name_id]
    lc.execute("DROP TABLE IF EXISTS temp.sc_ans")
    lc.execute("""CREATE TEMP TABLE sc_ans AS SELECT DISTINCT link, svc, COALESCE(cpt, '') AS cpt, posted FROM postings
                  WHERE link IS NOT NULL AND check_no IS NOT NULL""")
    lc.execute("CREATE INDEX temp.ix_sc_ans ON sc_ans(link, svc, cpt, posted)")
    kind_names = {i: n for i, n in names.items()}
    lc.create_function("sc_kind", 1, lambda k: kind_names.get(k), deterministic=True)
    # per line: the pieces and the line's cell key (its newest primary payer)
    cells: dict[tuple, Counter] = defaultdict(Counter)
    line_rows = 0
    for (svc_m, prim, chg, ins, pat, contr_r, wo_post, wo_later, timely, padj, n_rows, link_bal) in lc.execute(
            """SELECT substr(p.svc, 1, 7), (SELECT q.primary_payer FROM postings q WHERE q.link = p.link AND q.svc = p.svc
                                            AND COALESCE(q.cpt, '') = COALESCE(p.cpt, '') AND q.primary_payer IS NOT NULL
                                            ORDER BY q.posted DESC, q.rowid DESC LIMIT 1),
                      SUM(COALESCE(p.charges, 0)), SUM(COALESCE(p.ins_paid, 0)), SUM(COALESCE(p.pat_paid, 0)),
                      SUM(CASE WHEN sc_class(p.grp, p.code, sc_kind(p.kind)) IN ('none','routine','patient','ema_kind')
                               THEN COALESCE(p.contractual, 0) + COALESCE(p.noncontractual, 0) ELSE 0 END),
                      SUM(CASE WHEN sc_class(p.grp, p.code, sc_kind(p.kind)) NOT IN ('none','routine','patient','ema_kind','timely')
                                AND EXISTS (SELECT 1 FROM sc_ans a WHERE a.link = p.link AND a.svc = p.svc
                                            AND a.cpt = COALESCE(p.cpt, '') AND a.posted = p.posted)
                               THEN COALESCE(p.contractual, 0) + COALESCE(p.noncontractual, 0) ELSE 0 END),
                      SUM(CASE WHEN sc_class(p.grp, p.code, sc_kind(p.kind)) NOT IN ('none','routine','patient','ema_kind','timely')
                                AND NOT EXISTS (SELECT 1 FROM sc_ans a WHERE a.link = p.link AND a.svc = p.svc
                                            AND a.cpt = COALESCE(p.cpt, '') AND a.posted = p.posted)
                               THEN COALESCE(p.contractual, 0) + COALESCE(p.noncontractual, 0) ELSE 0 END),
                      SUM(CASE WHEN sc_class(p.grp, p.code, sc_kind(p.kind)) = 'timely'
                               THEN COALESCE(p.contractual, 0) + COALESCE(p.noncontractual, 0) ELSE 0 END),
                      SUM(COALESCE(p.pat_adj, 0)), COUNT(*),
                      SUM(COALESCE(p.charges, 0) - COALESCE(p.ins_paid, 0) - COALESCE(p.pat_paid, 0) - COALESCE(p.contractual, 0)
                          - COALESCE(p.noncontractual, 0) - COALESCE(p.pat_adj, 0))
               FROM postings p WHERE p.link IS NOT NULL GROUP BY p.link, p.svc, COALESCE(p.cpt, '')"""):
        line_rows += 1
        c = cells[(svc_m, group(prim))]
        c["charges"] += chg or 0
        c["ins_paid"] += ins or 0
        c["pat_paid"] += pat or 0
        c["contractual"] += contr_r or 0
        c["wo_at_posting"] += wo_post or 0
        c["wo_later"] += wo_later or 0
        c["wo_timely"] += timely or 0
        c["pat_adj"] += padj or 0
        c["lines"] += 1
        bal = link_bal or 0
        if bal > 0:
            c["open"] += bal
        elif bal < 0:
            c["credit"] += -bal
    # postings that belong to no line: their own visible gap, in the cell of their service month and payer
    for (svc_m, payer, prim, chg, ins, pat, adj, n_rows) in lc.execute(
            """SELECT substr(svc, 1, 7), payer, primary_payer, SUM(COALESCE(charges, 0)), SUM(COALESCE(ins_paid, 0)),
                      SUM(COALESCE(pat_paid, 0)), SUM(COALESCE(contractual, 0) + COALESCE(noncontractual, 0) + COALESCE(pat_adj, 0)), COUNT(*)
               FROM postings WHERE link IS NULL GROUP BY 1, 2, 3"""):
        c = cells[(svc_m, group(prim if prim is not None else payer))]
        c["unlinked_charges"] += chg or 0
        c["unlinked_money"] += (ins or 0) + (pat or 0) + (adj or 0)
        c["unlinked_rows"] += n_rows or 0
    lc.execute("DROP TABLE IF EXISTS temp.sc_ans")
    offs, gaps = [], []
    conn.execute("DELETE FROM selfcheck_money")
    rows = []
    for (m, g), c in sorted(cells.items(), key=lambda kv: (kv[0][0] or "", kv[0][1] or "")):
        pieces = (c["ins_paid"] + c["pat_paid"] + c["contractual"] + c["wo_at_posting"] + c["wo_later"] + c["wo_timely"]
                  + c["pat_adj"] + c["open"] - c["credit"])
        gap = c["charges"] - pieces
        c["gap"] = gap
        if abs(gap) > 100:
            offs.append((f"{m}|{g}", abs(gap), None))
        if c["unlinked_money"] or c["unlinked_charges"]:
            gaps.append({"month": m, "payer_group": g, "unlinked_money_cents": c["unlinked_money"],
                         "unlinked_charges_cents": c["unlinked_charges"], "rows": c["unlinked_rows"]})
        for measure, cents in c.items():
            rows.append((m or "", g or "", measure, int(cents), None))
    conn.executemany("INSERT OR REPLACE INTO selfcheck_money (month, payer_group, measure, cents, rows) VALUES (?,?,?,?,?)", rows)
    since = {k: v for k, v in cells.items() if (k[0] or "") >= SERVICE_FROM[:7]}
    tot = Counter()
    for c in since.values():
        tot.update(c)
    summary = {"cells": len(cells), "lines": line_rows, "since": SERVICE_FROM[:7],
               "totals_since": {k: int(v) for k, v in tot.items()},
               "cells_off": [{"cell": k, "gap_cents": c} for k, c, _f in offs][:50],
               "unlinked": sorted(gaps, key=lambda x: -abs(x["unlinked_money_cents"]))[:50],
               "unlinked_money_cents": int(sum(c["unlinked_money"] for c in cells.values())),
               "unlinked_since_cents": int(tot.get("unlinked_money", 0))}
    text = (f"{_n(len(cells), 'month and payer cell')} checked; " +
            ("every one adds up to its charges" if not offs else f"{_n(len(offs), 'cell')} do not add up ({usd0(sum(c for _k, c, _f in offs))})") +
            f"; {usd0(summary['unlinked_since_cents'])} of patient or insurance money since {SERVICE_FROM[:7]} sits on no line")
    summary["check"] = _result("money_conservation", offs, info={"cells": len(cells), "lines": line_rows,
                                                                 "unlinked_since_cents": summary["unlinked_since_cents"]},
                               examples=[], text=text)
    return summary


def _ar_balance(conn: sqlite3.Connection, lc: sqlite3.Connection, today: str) -> dict:
    """EMA's newest A/R against its own postings: each A/R line (claim number, date of service, code) carries a balance;
    the postings up to the A/R's day leave one. Lines that differ by more than $1 are the lines that do not reconcile,
    summed with their dollars (a warning: a posting missing from the export, or an A/R pulled before the postings)."""
    if not _has_table(conn, "ema_ar") or not _has_table(lc, "postings"):
        return {"check": _skip("line_balance", "no A/R or no posting ledger")}
    row = conn.execute("SELECT MAX(as_of) FROM ema_ar").fetchone()
    as_of = row[0] if row else None
    if not as_of:
        return {"check": _skip("line_balance", "no A/R on file")}
    ar: dict[tuple, int] = defaultdict(int)
    for link, svc, cpt, bal in conn.execute("""SELECT link, svc, cpt, SUM(COALESCE(balance_cents, 0)) FROM ema_ar
                                               WHERE as_of = ? AND link IS NOT NULL AND COALESCE(facility, 0) = 0
                                                 AND party = 'Insurance' GROUP BY link, svc, cpt""", (as_of,)):
        ar[(int(link), svc, cpt or "")] += int(bal or 0)
    links = sorted({k[0] for k in ar})
    led: dict[tuple, int] = {}
    for i in range(0, len(links), 400):
        part = links[i:i + 400]
        for link, svc, cpt, bal in lc.execute(
                f"""SELECT link, svc, COALESCE(cpt, ''), SUM(COALESCE(charges, 0) - COALESCE(ins_paid, 0) - COALESCE(pat_paid, 0)
                           - COALESCE(contractual, 0) - COALESCE(noncontractual, 0) - COALESCE(pat_adj, 0))
                    FROM postings WHERE link IN ({','.join('?' * len(part))}) AND posted <= ? GROUP BY link, svc, COALESCE(cpt, '')""",
                (*part, as_of)):
            led[(int(link), svc, cpt)] = int(bal or 0)
    offs = []
    for k, bal in ar.items():
        got = led.get(k)
        if got is None:
            offs.append((f"{k[0]}|{k[1]}|{k[2]}", abs(bal), None))
        elif abs(got - bal) > 100:
            offs.append((f"{k[0]}|{k[1]}|{k[2]}", abs(got - bal), None))
    info = {"as_of": as_of, "lines": len(ar), "not_in_postings": sum(1 for k in ar if k not in led)}
    return {"check": _result("line_balance", offs, info=info, examples=[],
                             text=f"{_n(len(ar), 'line')} on the A/R of {as_of}; {_n(len(offs), 'line')} do not reconcile "
                                  f"({usd0(sum(c for _k, c, _f in offs))})"),
            "as_of": as_of, "lines": len(ar), "off": len(offs), "off_cents": sum(c for _k, c, _f in offs)}


def check_serving(conn: sqlite3.Connection, serving: sqlite3.Connection) -> dict:
    """The dashboard's copy against the store: the items' counts and dollars by state and status, and the alerts. Saved
    in meta selfcheck:serving (of the store) for the next run's history and the panel."""
    def figures(c: sqlite3.Connection) -> dict:
        cols = set(_cols(c, "findings"))
        ws = "work_state" if "work_state" in cols else "NULL"
        out = {}
        for st, wk, n, cents in c.execute(f"""SELECT status, {ws}, COUNT(*), COALESCE(SUM(shortfall_cents), 0) FROM findings
                                              WHERE COALESCE(stale, 0) = 0 GROUP BY 1, 2"""):
            out[f"{st}|{wk}"] = [n, cents]
        if _has_table(c, "alerts"):
            out["alerts"] = [c.execute("SELECT COUNT(*) FROM alerts WHERE status != 'cleared'").fetchone()[0], 0]
        return out
    a, b = figures(conn), figures(serving)
    offs = [(k, abs((a.get(k, [0, 0])[1] or 0) - (b.get(k, [0, 0])[1] or 0)), None) for k in sorted(set(a) | set(b))
            if a.get(k, [0, 0]) != b.get(k, [0, 0])]
    r = _result("serving_copy", offs, examples=[], info={"store": a, "copy": b})
    meta_set(conn, "selfcheck:serving", {k: r[k] for k in ("status", "n", "cents", "text")} | {"at": now_of()})
    return r


# =====================================================================================================================
# 9-11. Money given up without a decision, blindness, the scoreboard figures
# =====================================================================================================================
AT_POSTING = ("at_posting",)
WRITE_OFF_OUTCOMES = ("auto_written_off", "written_off_at_posting", "contractual_at_posting", "written_off_while_pending",
                      "lost_to_timely_filing")


def _policy_accepts(conn: sqlite3.Connection) -> set[str]:
    pol = meta_get(conn, "ema:writeoff_policy", {}) or {}
    return {k for k, v in pol.items() if isinstance(v, dict) and v.get("decision") == "accept"}


def _pattern_key(row: dict) -> list[str]:
    mod = str(row.get("modifiers") or "").split(",")[0].strip()
    carc = str(row.get("carc") or "")
    return [f"{row.get('payer_group') or ''}|{row.get('procedure_code') or ''}|{mod}|{g}-{carc}" for g in ("CO", "OA", "PI", "CR")]


def given_up(conn: sqlite3.Connection, *, today: Any = None, save: bool = True) -> dict:
    """Money given up without a decision: the dollars EMA wrote off on 2025+ service, at the remittance's posting (EMA's
    posting rule: no person decided) on items no person let go and no owner policy accepts, by payer group, reason and
    service month, split into the part still inside its window (recoverable) and the part already lost. When the posting
    ledger's money check has run, the dollars written off at posting with no RemitWatch item at all are given beside it.
    Kept week by week in meta selfcheck:given_up; Today, Progress and the weekly email read it."""
    t = today_of(today)
    cols = set(_cols(conn, "findings"))
    if not {"written_off_cents", "written_off_when"} <= cols:
        return {"at": now_of(), "cents": 0, "items": 0, "note": "the store has no write-off columns yet"}
    accept = _policy_accepts(conn)
    legal = "legal_deadline" if "legal_deadline" in cols else "NULL"
    outcome = "ema_outcome" if "ema_outcome" in cols else "NULL"
    by: dict[tuple, Counter] = defaultdict(Counter)
    tot = Counter()
    for row in conn.execute(f"""SELECT finding_id, payer_group, procedure_code, modifiers, carc, service_date, written_off_cents,
                                       appeal_deadline, {legal}, status, {outcome}, signal
                                FROM findings WHERE COALESCE(stale, 0) = 0 AND COALESCE(written_off_cents, 0) > 0
                                  AND written_off_when = 'at_posting' AND service_date >= ?
                                  AND status NOT IN ('dismissed', 'lost')""", (SERVICE_FROM,)):
        fid, grp, code, mods, carc, svc, cents, ad, ld, status, oc, sig = row
        if sig == "EMA_DENIAL_AS_CONTRACTUAL":
            continue
        if any(k in accept for k in _pattern_key({"payer_group": grp, "procedure_code": code, "modifiers": mods, "carc": carc})):
            continue
        last = max([str(x)[:10] for x in (ad, ld) if x] or [""])
        part = "recoverable" if last and last >= t else "lost"
        key = (grp or "Unknown", str(carc or "?"), str(svc or "")[:7])
        by[key][part] += int(cents)
        by[key]["items"] += 1
        tot[part] += int(cents)
        tot["items"] += 1
    rows = sorted(({"payer_group": k[0], "reason": k[1], "month": k[2], "recoverable_cents": v["recoverable"],
                    "lost_cents": v["lost"], "items": v["items"]} for k, v in by.items()),
                  key=lambda r: -(r["recoverable_cents"] + r["lost_cents"]))
    money = meta_get(conn, K_MONEY, {}) or {}
    ledger_wo = (money.get("conservation") or {}).get("totals_since", {}).get("wo_at_posting")
    out = {"at": now_of(), "today": t, "week": _week_of(t), "cents": tot["recoverable"] + tot["lost"],
           "recoverable_cents": tot["recoverable"], "lost_cents": tot["lost"], "items": tot["items"],
           "by_payer": _top(rows, "payer_group"), "by_reason": _top(rows, "reason"), "by_month": _top(rows, "month"),
           "rows": rows[:200], "ledger_written_off_at_posting_cents": ledger_wo,
           "with_no_item_cents": (int(ledger_wo) - (tot["recoverable"] + tot["lost"])) if isinstance(ledger_wo, int) else None}
    if save:
        prev = meta_get(conn, K_GIVEN_UP, {}) or {}
        weeks = [w for w in (prev.get("weeks") or []) if w.get("week") != out["week"]]
        weeks.append({"week": out["week"], "cents": out["cents"], "recoverable_cents": out["recoverable_cents"],
                      "lost_cents": out["lost_cents"], "items": out["items"]})
        out["weeks"] = sorted(weeks, key=lambda w: w["week"])[-26:]
        meta_set(conn, K_GIVEN_UP, out)
    return out


def _top(rows: list[dict], key: str, k: int = 15) -> list[dict]:
    agg: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        a = agg[r[key]]
        a["recoverable_cents"] += r["recoverable_cents"]
        a["lost_cents"] += r["lost_cents"]
        a["items"] += r["items"]
    return sorted(({key: name, **dict(v)} for name, v in agg.items()),
                  key=lambda x: -(x["recoverable_cents"] + x["lost_cents"]))[:k]


def blindness(conn: sqlite3.Connection, *, today: Any = None, save: bool = True) -> dict:
    """What the app cannot see, since when, and the dollars of claims it cannot check. The 835 feed: claims billed since the
    newest remittance have no payer answer on file (their charges, from EMA's Production when the ledger's money check
    has run, else EMA's monthly totals, prorated, marked 'about'). EMA's reports: the items to work whose 'already worked or
    paid in EMA' check rests on a report older than its limit. One banner sentence for every page; never 'all clear'
    while blind."""
    t = today_of(today)
    sources = []
    newest = None
    if _has_table(conn, "transactions"):
        newest = conn.execute("SELECT MAX(payment_date) FROM transactions WHERE COALESCE(superseded, 0) = 0").fetchone()[0]
    days = _days(newest, t) if newest else None
    limit = _int_setting("REMITWATCH_STALE_DAYS", STALE_835_DAYS)
    if newest and days is not None and days > limit:
        cents, about = _charges_since(conn, newest, t)
        sources.append({"source": "835", "since": newest, "days": days, "cents": cents, "about": about,
                        "text": f"No remittance since {newest} ({days} days): {'about ' if about else ''}{usd0(cents)} billed since "
                                "then has no payer answer RemitWatch can check."})
    if _has_table(conn, "ema_report_files") and conn.execute("SELECT 1 FROM ema_report_files LIMIT 1").fetchone():
        try:
            from .ema_checks import freshness
            fr = freshness(conn, t)
        except Exception:          # noqa: BLE001  (no freshness: say nothing rather than guess)
            fr = {}
        stale = []
        if fr.get("production_days") is not None and fr["production_days"] > STALE_PRODUCTION_DAYS:
            stale.append(("production", "Production report", fr.get("production_as_of"), fr["production_days"]))
        if fr.get("ar_days") is not None and fr["ar_days"] > STALE_AR_DAYS:
            stale.append(("ar", "A/R report", fr.get("ar_as_of"), fr["ar_days"]))
        if stale:
            cols = set(_cols(conn, "findings"))
            ws = "work_state = 'work_now'" if "work_state" in cols else "status = 'open'"
            n, cents = conn.execute(f"""SELECT COUNT(*), COALESCE(SUM(shortfall_cents), 0) FROM findings
                                       WHERE COALESCE(stale, 0) = 0 AND {ws}
                                         AND category IN ('denial','bundling','downcoding','underpayment')""").fetchone()
            for key, label, as_of, d in stale:
                sources.append({"source": key, "since": as_of, "days": d, "cents": cents, "items": n, "about": False,
                                "text": f"EMA's {label} is {d} days old (as of {as_of}): for {_n(n, 'item')} to work ({usd0(cents)}) "
                                        "RemitWatch cannot see whether someone worked or posted them in EMA since."})
    out = {"at": now_of(), "today": t, "blind": bool(sources), "sources": sources,
           "banner": " ".join(s["text"] for s in sources) if sources else None}
    if save:
        meta_set(conn, K_BLIND, out)
    return out


def _charges_since(conn: sqlite3.Connection, newest: str, today: str) -> tuple[int, bool]:
    """Charges EMA billed for services after the newest remittance: exact from the ledger's cells when the money check has
    run (selfcheck_money, by service month: the newest remittance's month prorated by days), else from EMA's posted
    monthly charges (ema_months), prorated the same way: 'about'."""
    month = newest[:7]
    try:
        d = _d(newest)
        import calendar
        last = calendar.monthrange(d.year, d.month)[1]
        share = max(0.0, (last - d.day) / last)
    except Exception:          # noqa: BLE001
        share = 0.0
    if _has_table(conn, "selfcheck_money") and conn.execute("SELECT 1 FROM selfcheck_money LIMIT 1").fetchone():
        tot = 0.0
        for m, cents in conn.execute("SELECT month, SUM(cents) FROM selfcheck_money WHERE measure='charges' AND month >= ? GROUP BY 1",
                                     (month,)):
            tot += (cents or 0) * (share if m == month else 1.0)
        return int(tot), True
    if _has_table(conn, "ema_months"):
        tot = 0.0
        for m, cents in conn.execute("SELECT month, SUM(cents) FROM ema_months WHERE measure='charges' AND month >= ? GROUP BY 1",
                                     (month,)):
            tot += (cents or 0) * (share if m == month else 1.0)
        return int(tot), True
    return 0, True


def scoreboard(conn: sqlite3.Connection, *, today: Any = None) -> dict:
    """G's figures for the Results tab (unit R owns the page and the attribution): money given up without a decision (by
    week), money lost to deadlines (items that expired while Work now, by week), money at risk (Work now items due within
    14 days), the self-audit's gaps and possibly wasted work (by week), and the track record."""
    t = today_of(today)
    cols = set(_cols(conn, "findings"))
    ws = "work_state = 'work_now'" if "work_state" in cols else "status = 'open'"
    risk = conn.execute(f"""SELECT COUNT(*), COALESCE(SUM(shortfall_cents), 0) FROM findings WHERE COALESCE(stale, 0) = 0 AND {ws}
                            AND category IN ('denial','bundling','downcoding','underpayment')
                            AND appeal_deadline >= ? AND appeal_deadline <= ?""", (t, _add(t, 14))).fetchone() \
        if "appeal_deadline" in cols else (0, 0)
    gu = meta_get(conn, K_GIVEN_UP, {}) or {}
    tr = meta_get(conn, K_TRACK, {}) or {}
    au = meta_get(conn, K_AUDIT, {}) or {}
    return {"at": now_of(), "today": t, "at_risk_14": {"n": risk[0], "cents": risk[1]},
            "given_up": {"cents": gu.get("cents"), "recoverable_cents": gu.get("recoverable_cents"), "weeks": gu.get("weeks") or []},
            "lost_to_deadlines": [{"week": w["week"], "n": w["missed_n"], "cents": w["missed_cents"]} for w in tr.get("weeks") or []],
            "audit": {"gaps_cents": (au.get("latest") or {}).get("gaps_cents"), "wasted_n": (au.get("latest") or {}).get("wasted_n"),
                      "weeks": au.get("weeks") or []},
            "track": {k: tr.get(k) for k in ("closure_error_rate", "closures", "reopened", "waits", "let_go_paid")}}


# =====================================================================================================================
# 12. Recovery rates by pattern (from the 835 archive) and each item's expected value
# =====================================================================================================================
def _denial_cases(conn: sqlite3.Connection, newest: str) -> list[tuple]:
    """Every matured denial in the archive: a line its payer answered as primary with nothing paid and a denial reason
    (CO, OA or PI, not a routine reduction), and whether the same bill, date and code was paid later (any payer, net of
    reversals), how much and how soon. Rows: (payer group, code, modifier, reason, expected cents, later paid cents,
    days to the payment or None)."""
    mature = _add(newest, -RATE_MATURE_DAYS) or newest
    conn.execute("DROP TABLE IF EXISTS temp.sc_den")
    conn.execute("""CREATE TEMP TABLE sc_den AS
                    SELECT a.line_key AS line_key, MAX(a.reason_code) AS carc, SUM(a.amount_cents) AS cents
                    FROM adjustments a WHERE a.line_key IS NOT NULL AND a.group_code IN ('CO','OA','PI') AND a.amount_cents > 0
                      AND a.reason_code NOT IN (""" + ",".join(f"'{c}'" for c in sorted(ROUTINE_CARCS)) + """)
                    GROUP BY a.line_key""")
    conn.execute("CREATE INDEX temp.ix_sc_den ON sc_den(line_key)")
    bill = _bill_sql("l.patient_control_number")
    conn.execute("DROP TABLE IF EXISTS temp.sc_pay")
    conn.execute(f"""CREATE TEMP TABLE sc_pay AS
                     SELECT {bill} AS bill, l.service_date AS svc, COALESCE(l.original_procedure_code, l.procedure_code) AS code,
                            l.payment_date AS paid_on, COALESCE(l.paid_cents, 0) AS paid
                     FROM lines l JOIN transactions t ON t.transaction_key = l.transaction_key
                     WHERE COALESCE(t.superseded, 0) = 0 AND COALESCE(l.paid_cents, 0) <> 0
                       AND l.claim_status_code IN ('1','2','3','19','20','21','22')""")
    conn.execute("CREATE INDEX temp.ix_sc_pay ON sc_pay(bill, svc, code)")
    rows = conn.execute(f"""SELECT l.line_key, l.patient_control_number, {bill}, l.service_date,
                                   COALESCE(l.original_procedure_code, l.procedure_code), l.modifiers, l.payment_date,
                                   l.payer_name, c.claim_filing_indicator, d.carc, COALESCE(l.charge_cents, 0)
                            FROM lines l JOIN sc_den d ON d.line_key = l.line_key
                            JOIN claims c ON c.claim_key = l.claim_key
                            JOIN transactions t ON t.transaction_key = l.transaction_key
                            WHERE COALESCE(t.superseded, 0) = 0 AND l.claim_status_code IN ('1','19')
                              AND COALESCE(l.paid_cents, 0) = 0 AND COALESCE(l.charge_cents, 0) > 0
                              AND l.payment_date <= ?""", (mature,)).fetchall()
    # what each payer group usually allows for a code (median of its paid one-unit lines): the denied line's expected amount
    rates: dict[tuple, list] = defaultdict(list)
    for name, cfi, code, allowed in conn.execute(
            """SELECT l.payer_name, c.claim_filing_indicator, COALESCE(l.original_procedure_code, l.procedure_code),
                      COALESCE(l.allowed_cents, l.paid_cents) FROM lines l JOIN claims c ON c.claim_key = l.claim_key
               WHERE l.claim_status_code IN ('1','19') AND COALESCE(l.paid_cents, 0) > 0 AND COALESCE(l.paid_units, 1) <= 1"""):
        if allowed and allowed > 0:
            rates[(_group_kind(name, cfi)[0], code)].append(int(allowed))
    med = {k: _pct(v, 50) for k, v in rates.items() if len(v) >= 3}
    out = []
    for lk, pcn, b, svc, code, mods, paid_on, name, cfi, carc, charge in rows:
        grp = _group_kind(name, cfi)[0]
        later = conn.execute("SELECT paid_on, paid FROM sc_pay WHERE bill = ? AND svc = ? AND code = ? AND paid_on > ? ORDER BY paid_on",
                             (b, svc, code, paid_on)).fetchall()
        net = sum(p for _d2, p in later)
        first = next((d2 for d2, p in later if p > 0), None)
        exp = med.get((grp, code)) or (net if net > 0 else None) or int(charge * 0.4)
        mod = ""
        if mods:
            try:
                ml = json.loads(mods) if str(mods).startswith("[") else str(mods).split(",")
                mod = str(ml[0]).strip() if ml else ""
            except (TypeError, ValueError):
                mod = ""
        out.append((grp, code, mod, str(carc), int(exp), max(0, int(net)), _days(paid_on, first) if first and net > 0 else None))
    conn.execute("DROP TABLE IF EXISTS temp.sc_den")
    conn.execute("DROP TABLE IF EXISTS temp.sc_pay")
    return out


def recovery_rates(conn: sqlite3.Connection, *, save: bool = True) -> dict:
    """The share of denied dollars later paid, by pattern, from the 835 archive: payer group x code x reason x modifier,
    payer group x code x reason, payer group x reason, reason, and all denials, each with the cases it rests on and the
    median days to the payment. A pattern needs RATE_MIN_CASES cases; expected_value falls back level by level and says
    so. Returns {'levels': {level: {key: {...}}}, 'all': {...}, 'newest', 'cases', 'at'}; a summary goes to meta
    selfcheck:rates."""
    out: dict = {"levels": {"gcrm": {}, "gcr": {}, "gr": {}, "r": {}}, "all": None, "cases": 0, "at": now_of()}
    if not (_has_table(conn, "lines") and _has_table(conn, "adjustments")):
        return out
    newest = conn.execute("SELECT MAX(payment_date) FROM transactions WHERE COALESCE(superseded, 0) = 0").fetchone()[0]
    if not newest:
        return out
    out["newest"] = newest
    cases = _denial_cases(conn, newest)
    acc: dict[str, dict[tuple, list]] = {lv: defaultdict(lambda: [0, 0, 0, 0, []]) for lv in out["levels"]}
    allc = [0, 0, 0, 0, []]
    for grp, code, mod, carc, exp, net, days in cases:
        got = min(net, exp) if exp > 0 else 0
        keys = {"gcrm": (grp, code, carc, mod), "gcr": (grp, code, carc), "gr": (grp, carc), "r": (carc,)}
        for lv, k in keys.items():
            if lv == "gcrm" and not mod:
                continue
            a = acc[lv][k]
            a[0] += 1
            a[1] += exp
            a[2] += got
            a[3] += 1 if net > 0 else 0
            if days is not None:
                a[4].append(days)
        allc[0] += 1
        allc[1] += exp
        allc[2] += got
        allc[3] += 1 if net > 0 else 0
        if days is not None:
            allc[4].append(days)

    def pack(a: list) -> dict:
        return {"n": a[0], "denied_cents": a[1], "paid_cents": a[2], "paid_n": a[3],
                "rate": round(a[2] / a[1], 4) if a[1] > 0 else 0.0, "median_days": _pct(a[4], 50)}
    for lv in acc:
        out["levels"][lv] = {"|".join(k): pack(v) for k, v in acc[lv].items()}
    out["all"] = pack(allc)
    out["cases"] = allc[0]
    if save:
        top = sorted(((k, v) for k, v in out["levels"]["gcr"].items() if v["n"] >= RATE_MIN_CASES), key=lambda kv: -kv[1]["denied_cents"])[:40]
        meta_set(conn, K_RATES, {"at": out["at"], "newest": newest, "cases": out["cases"], "all": out["all"],
                                 "patterns": len([1 for v in out["levels"]["gcr"].values() if v["n"] >= RATE_MIN_CASES]),
                                 "top": [{"pattern": k, **v} for k, v in top]})
    return out


LEVEL_WORDS = {"gcrm": "payer group x code x reason x modifier", "gcr": "payer group x code x reason",
               "gr": "payer group x reason", "r": "reason", "all": "all denials"}


def expected_value(item: dict, rates: dict) -> tuple[int, float | None, str]:
    """(expected value in cents, the recovery rate, its basis) for an item: its dollars at stake x the share of denied
    dollars its pattern later recovered, from the most detailed level with RATE_MIN_CASES cases (payer group x code x
    reason x modifier, then payer group x code x reason, payer group x reason, reason, all denials), the basis naming the
    level, the cases and any fallback. Money owed back and items with nothing at stake are worth 0 to recover; kinds of
    item the denial archive cannot speak for (short payments, unanswered claims, contract questions) keep their full
    dollars, and say so."""
    dollars = int(item.get("shortfall_cents") or 0)
    if dollars <= 0 and item.get("expected_cents"):
        dollars = max(0, int(item.get("expected_cents") or 0) - int(item.get("paid_cents") or 0))
    cat, sig = item.get("category"), item.get("signal")
    if cat == "refund" or sig == "CREDIT_BALANCE":
        return 0, 0.0, "Money owed back, not money to recover"
    if dollars <= 0:
        return 0, None, "Nothing at stake on this item"
    carc = str(item.get("carc") or "").strip()
    m = re.match(r"^(?:CO|OA|PI|PR|CR)?-?([A-Z0-9]{1,4})$", carc.upper()) if carc else None
    carc = m.group(1) if m else carc
    if cat not in ("denial", "bundling") or not carc or sig in CLAIM_SIGNALS or carc in ROUTINE_CARCS:
        return dollars, None, ("No recovery history for this kind of item in the 835 archive (a short payment, an unanswered "
                               "claim or a contract question): ranked by its full dollars")
    grp = str(item.get("payer_group") or "")
    code = str(item.get("billed_code") or item.get("original_procedure_code") or item.get("procedure_code") or "")
    mod = str(item.get("modifiers") or "").split(",")[0].strip()
    lv = (rates or {}).get("levels") or {}
    tried = []
    for level, key in (("gcrm", (grp, code, carc, mod)), ("gcr", (grp, code, carc)), ("gr", (grp, carc)), ("r", (carc,))):
        if level == "gcrm" and not mod:
            continue
        v = (lv.get(level) or {}).get("|".join(key))
        if v and v["n"] >= RATE_MIN_CASES:
            basis = f"{LEVEL_WORDS[level]}: {v['paid_n']:,} of {v['n']:,} denials like this later paid ({v['rate']:.0%} of their dollars)"
            if tried:
                basis += f"; {' and '.join(tried)} had fewer than {RATE_MIN_CASES} cases"
            return int(round(dollars * v["rate"])), v["rate"], basis
        tried.append(LEVEL_WORDS[level])
    a = (rates or {}).get("all")
    if a and a.get("n"):
        return int(round(dollars * a["rate"])), a["rate"], (f"all denials: {a['paid_n']:,} of {a['n']:,} later paid ({a['rate']:.0%} of "
                                                           f"their dollars); this payer, code and reason have fewer than {RATE_MIN_CASES} cases")
    return dollars, None, "No denial history in the archive yet: ranked by its full dollars"


def apply_expected_values(conn: sqlite3.Connection, rates: dict | None = None) -> dict:
    """Write expected_value_cents, recovery_rate and recovery_basis on every item that is not stale (the hook-in agent can
    call this right after detection's save, or set the three fields on each item before it). Returns counts."""
    ensure_value_columns(conn)
    rates = rates if rates is not None else recovery_rates(conn)
    cols = set(_cols(conn, "findings"))
    want = ["finding_id", "category", "signal", "shortfall_cents", "expected_cents", "paid_cents", "carc", "payer_group",
            "billed_code", "original_procedure_code", "procedure_code", "modifiers"]
    sel = ", ".join(c if c in cols else f"NULL AS {c}" for c in want)
    cur = conn.execute(f"SELECT {sel} FROM findings WHERE COALESCE(stale, 0) = 0")
    names = [d[0] for d in cur.description]
    upd = []
    levels = Counter()
    for r in cur.fetchall():
        it = dict(zip(names, r))
        cents, rate, basis = expected_value(it, rates)
        upd.append((cents, rate, basis[:200], it["finding_id"]))
        levels[basis.split(":")[0]] += 1
    conn.executemany("UPDATE findings SET expected_value_cents = ?, recovery_rate = ?, recovery_basis = ? WHERE finding_id = ?", upd)
    conn.commit()
    return {"items": len(upd), "by_basis": dict(levels.most_common(8))}


# =====================================================================================================================
# 3. The weekly self-audit (independent of the detection code: its own, simpler rules over the raw tables)
# =====================================================================================================================
def audit_due(conn: sqlite3.Connection, today: Any = None) -> bool:
    ensure_tables(conn)
    t = today_of(today)
    last = conn.execute("SELECT MAX(today) FROM selfcheck_audits").fetchone()[0]
    return not last or (_days(last, t) or 0) >= AUDIT_EVERY_DAYS


def _windows(group: str | None, kind: str | None) -> tuple[int, int]:
    """(appeal days, filing days) for a payer group: codes.py's windows, read as data (as the answer keys did)."""
    try:
        from .codes import appeal_rule, filing_rule
        a = appeal_rule(group, kind)
        f = filing_rule(group, kind)
        legal = a.get("legal") or {}
        days = int(a.get("days") or 180)
        if legal.get("days"):
            days = max(days, int(legal["days"]))
        elif legal.get("months") or legal.get("years"):
            days = max(days, int(30.44 * (int(legal.get("months") or 0) + 12 * int(legal.get("years") or 0))))
        return days, int(f.get("days") or 365)
    except Exception:          # noqa: BLE001
        return 180, 365


def _item_index(conn: sqlite3.Connection) -> dict:
    """Which services RemitWatch lists, and how: {(bill, svc, code): [(finding_id, work_state, status, reason)]}, with the
    finding's EMA claim number too, so a line can be found by either."""
    cols = set(_cols(conn, "findings"))
    ws = "work_state" if "work_state" in cols else "NULL"
    link = "ema_link" if "ema_link" in cols else "NULL"
    by_service: dict[tuple, list] = defaultdict(list)
    by_link: dict[tuple, list] = defaultdict(list)
    by_bill: dict[str, list] = defaultdict(list)
    for fid, pcn, svc, pc, opc, bc, wk, st, lk, sig in conn.execute(
            f"""SELECT finding_id, patient_control_number, service_date, procedure_code, original_procedure_code,
                       {'billed_code' if 'billed_code' in cols else 'NULL'}, {ws}, status, {link}, signal FROM findings
                WHERE COALESCE(stale, 0) = 0"""):
        b = bill_of(pcn)
        entry = (fid, wk, st, sig)
        for code in {pc, opc, bc} - {None}:
            by_service[(b, str(svc or "")[:10], code)].append(entry)
            if lk is not None:
                by_link[(int(lk), str(svc or "")[:10], code)].append(entry)
        if b and sig in CLAIM_SIGNALS:
            by_bill[b].append(entry)
    return {"service": by_service, "link": by_link, "bill": by_bill}


def _covered(entries: list) -> str | None:
    """How RemitWatch holds a line: 'listed' (Work now or Waiting), 'reason' (accepted, let go, lost, a person's status, or
    done), None (no item at all)."""
    if not entries:
        return None
    if any(wk in LISTED for _f, wk, _s, _g in entries):
        return "listed"
    return "reason"


def self_audit(conn: sqlite3.Connection, *, today: Any = None, force: bool = False, why: str = "weekly",
               ledger: Any = None) -> dict:
    """Once a week (or on demand): (a) coverage: every 2025+ line with recoverable dollars inside a window (a denial, an
    automatic write-off still appealable, an unanswered claim, a short payment, an 835 payment EMA never posted, a second
    payer never billed, an open insurance balance) must have an item in Work now or Waiting or a recorded reason; every one
    that does not is a coverage gap with its dollars; (b) precision: every Work now item with evidence it is settled (paid
    after or before, the patient's share, worked and waiting, a duplicate listing, a facility twin, past every window) is
    possible wasted work; (c) the confusion matrix by count and dollars, kept week by week; (d) the alert when new gaps over
    $500 or more than 10 new wasted items appear. Its rules are its own (the answer keys' rules, simplified), never the
    detection code. Returns the summary."""
    ensure_tables(conn)
    t = today_of(today)
    if not force and not audit_due(conn, t):
        return {"skipped": "done this week", "last": conn.execute("SELECT MAX(today) FROM selfcheck_audits").fetchone()[0]}
    t0 = time.time()
    idx = _item_index(conn)
    gaps: list[dict] = []
    lines_needing: list[dict] = []
    notes: list[str] = []
    for fn in (_cov_denials, _cov_auto_writeoffs, _cov_unanswered, _cov_unposted, _cov_secondary, _cov_short_pay):
        try:
            got, note = fn(conn, t, idx)
        except sqlite3.Error as exc:
            got, note = [], f"{fn.__name__[5:]}: could not run ({type(exc).__name__})"
        lines_needing.extend(got)
        if note:
            notes.append(note)
    seen: set[str] = set()
    uniq = []
    for ln in sorted(lines_needing, key=lambda x: CATEGORY_ORDER.index(x["category"]) if x["category"] in CATEGORY_ORDER else 99):
        if ln["key"] in seen:
            continue
        seen.add(ln["key"])
        uniq.append(ln)
    listed = [x for x in uniq if x["held"] == "listed"]
    reasons = [x for x in uniq if x["held"] == "reason"]
    gaps = [x for x in uniq if x["held"] is None]
    wasted = _precision(conn, t)
    prev_id = conn.execute("SELECT audit_id FROM selfcheck_audits ORDER BY today DESC, at DESC LIMIT 1").fetchone()
    prev_keys: dict[str, set] = defaultdict(set)
    if prev_id:
        for lst, key in conn.execute("SELECT list, key FROM selfcheck_audit_items WHERE audit_id = ?", (prev_id[0],)):
            prev_keys[lst].add(key)
    audit_id = f"{t}-{why}"
    conn.execute("DELETE FROM selfcheck_audit_items WHERE audit_id = ?", (audit_id,))
    items = []
    for g in gaps:
        items.append((audit_id, "gap", g["key"], None, g["category"], g.get("reason"), g.get("payer_group"), g.get("month"),
                      int(g["cents"]), g.get("deadline"), int(bool(prev_id) and g["key"] not in prev_keys["gap"])))
    for w in wasted:
        items.append((audit_id, "wasted", w["finding_id"], w["finding_id"], w.get("category"), w["reason"], w.get("payer_group"),
                      w.get("month"), int(w["cents"]), w.get("deadline"), int(bool(prev_id) and w["finding_id"] not in prev_keys["wasted"])))
    conn.executemany("""INSERT OR REPLACE INTO selfcheck_audit_items (audit_id, list, key, finding_id, category, reason, payer_group,
                        service_month, cents, deadline, new) VALUES (?,?,?,?,?,?,?,?,?,?,?)""", items)
    cols = set(_cols(conn, "findings"))
    ws = "work_state" if "work_state" in cols else "NULL"
    wn_n, wn_cents = conn.execute(f"""SELECT COUNT(*), COALESCE(SUM(shortfall_cents), 0) FROM findings WHERE COALESCE(stale, 0) = 0
                                      AND {ws} = 'work_now' AND category IN ('denial','bundling','downcoding','underpayment')""").fetchone()
    nl_n, nl_cents = conn.execute(f"""SELECT COUNT(*), COALESCE(SUM(shortfall_cents), 0) FROM findings WHERE COALESCE(stale, 0) = 0
                                      AND COALESCE({ws}, '') NOT IN ('work_now', 'waiting')
                                      AND category IN ('denial','bundling','downcoding','underpayment')""").fetchone()
    w_n, w_cents = len(wasted), sum(int(w["cents"]) for w in wasted)
    matrix = {"needs_work_listed": {"n": len(listed) + max(0, wn_n - w_n), "cents": sum(int(x["cents"]) for x in listed) + max(0, wn_cents - w_cents),
                                    "lines": len(listed), "items": max(0, wn_n - w_n)},
              "needs_work_missed": {"n": len(gaps), "cents": sum(int(x["cents"]) for x in gaps)},
              "no_work_listed": {"n": w_n, "cents": w_cents},
              "no_work_not_listed": {"n": nl_n, "cents": nl_cents},
              "held_by_reason": {"n": len(reasons), "cents": sum(int(x["cents"]) for x in reasons)}}
    new_gaps = [g for g in gaps if prev_id and g["key"] not in prev_keys["gap"]]
    new_wasted = [w for w in wasted if prev_id and w["finding_id"] not in prev_keys["wasted"]]
    gap_alert = _int_setting("REMITWATCH_AUDIT_GAP_CENTS", AUDIT_GAP_ALERT_CENTS)
    wasted_alert = _int_setting("REMITWATCH_AUDIT_WASTED_ITEMS", AUDIT_WASTED_ALERT)
    big_new = [g for g in new_gaps if g["cents"] > gap_alert]
    summary = {"audit_id": audit_id, "at": now_of(), "today": t, "why": why, "seconds": round(time.time() - t0, 1),
               "gaps_n": len(gaps), "gaps_cents": sum(int(x["cents"]) for x in gaps),
               "gaps_by_category": _by(gaps, "category"), "gaps_by_payer": _by(gaps, "payer_group")[:12],
               "wasted_n": w_n, "wasted_cents": w_cents, "wasted_by_reason": _by(wasted, "reason"),
               "wasted_by_signal": _by(wasted, "signal")[:12], "matrix": matrix,
               "covered_by_category": {c: {"lines": sum(1 for x in uniq if x["category"] == c),
                                           "listed": sum(1 for x in listed if x["category"] == c),
                                           "reason": sum(1 for x in reasons if x["category"] == c),
                                           "gap": sum(1 for x in gaps if x["category"] == c)} for c in CATEGORY_ORDER},
               "new_gaps_n": len(new_gaps), "new_gaps_cents": sum(int(x["cents"]) for x in new_gaps), "new_big_gaps": len(big_new),
               "new_wasted_n": len(new_wasted), "first": not bool(prev_id), "notes": notes,
               "alert": bool(big_new) or len(new_wasted) > wasted_alert,
               "thresholds": {"gap_cents": gap_alert, "wasted_items": wasted_alert}}
    conn.execute("INSERT OR REPLACE INTO selfcheck_audits (audit_id, at, today, why, summary) VALUES (?,?,?,?,?)",
                 (audit_id, summary["at"], t, why, json.dumps(summary, default=str)))
    keep = [r[0] for r in conn.execute("SELECT audit_id FROM selfcheck_audits ORDER BY today DESC, at DESC LIMIT 3")]
    conn.execute(f"DELETE FROM selfcheck_audit_items WHERE audit_id NOT IN ({','.join('?' * len(keep))})", keep)
    weeks = []
    for at_, tdy, s in conn.execute("SELECT at, today, summary FROM selfcheck_audits ORDER BY today DESC, at DESC LIMIT 26"):
        x = _ev(s)
        weeks.append({"today": tdy, "gaps_n": x.get("gaps_n"), "gaps_cents": x.get("gaps_cents"), "wasted_n": x.get("wasted_n"),
                      "wasted_cents": x.get("wasted_cents"), "matrix": x.get("matrix")})
    meta_set(conn, K_AUDIT, {"latest": summary, "weeks": list(reversed(weeks))})
    conn.commit()
    return summary


CATEGORY_ORDER = ("era_payment_not_posted", "auto_written_off_appealable", "denial_in_window", "short_vs_payer_rate",
                  "secondary_not_billed", "open_balance_in_window", "unanswered_in_filing_limit")


def _by(rows: list[dict], key: str) -> list[dict]:
    agg: dict[str, list] = defaultdict(lambda: [0, 0])
    for r in rows:
        a = agg[str(r.get(key) or "")]
        a[0] += 1
        a[1] += int(r.get("cents") or 0)
    return [{key: k, "n": v[0], "cents": v[1]} for k, v in sorted(agg.items(), key=lambda kv: -kv[1][1])]


def _cov_denials(conn: sqlite3.Connection, today: str, idx: dict) -> tuple[list, str | None]:
    """Gate-1 rule set, denials: a 2025+ line its primary payer denied (nothing paid, a denial reason) that no later
    answer paid or moved to the patient, still inside the payer's window from the denial, worth the payer's usual amount
    for the code."""
    if not _has_table(conn, "lines"):
        return [], "denials: no remittances"
    bill = _bill_sql("l.patient_control_number")
    conn.execute("DROP TABLE IF EXISTS temp.sc_den")
    conn.execute("""CREATE TEMP TABLE sc_den AS SELECT a.line_key AS line_key, MAX(a.reason_code) AS carc
                    FROM adjustments a WHERE a.line_key IS NOT NULL AND a.group_code IN ('CO','OA','PI') AND a.amount_cents > 0
                      AND a.reason_code NOT IN (""" + ",".join(f"'{c}'" for c in sorted(ROUTINE_CARCS | {"209", "246", "24"})) + """)
                    GROUP BY a.line_key""")
    conn.execute("CREATE INDEX temp.ix_sc_den ON sc_den(line_key)")
    conn.execute("DROP TABLE IF EXISTS temp.sc_ans2")
    conn.execute(f"""CREATE TEMP TABLE sc_ans2 AS
                     SELECT {bill} AS bill, l.service_date AS svc, COALESCE(l.original_procedure_code, l.procedure_code) AS code,
                            l.payment_date AS paid_on, COALESCE(l.paid_cents, 0) AS paid, l.line_key AS line_key
                     FROM lines l JOIN transactions t ON t.transaction_key = l.transaction_key
                     WHERE COALESCE(t.superseded, 0) = 0 AND l.service_date >= ?""", (SERVICE_FROM,))
    conn.execute("CREATE INDEX temp.ix_sc_ans2 ON sc_ans2(bill, svc, code)")
    conn.execute("DROP TABLE IF EXISTS temp.sc_pr")
    conn.execute("""CREATE TEMP TABLE sc_pr AS SELECT line_key, SUM(amount_cents) AS pr FROM adjustments
                    WHERE line_key IS NOT NULL AND group_code = 'PR' AND reason_code IN ('1','2','3') GROUP BY line_key""")
    conn.execute("CREATE INDEX temp.ix_sc_pr ON sc_pr(line_key)")
    usual = _usual_rates(conn)
    out = []
    for lk, pcn, b, svc, code, paid_on, name, cfi, carc, charge in conn.execute(
            f"""SELECT l.line_key, l.patient_control_number, {bill}, l.service_date, COALESCE(l.original_procedure_code, l.procedure_code),
                       l.payment_date, l.payer_name, c.claim_filing_indicator, d.carc, COALESCE(l.charge_cents, 0)
                FROM lines l JOIN sc_den d ON d.line_key = l.line_key JOIN claims c ON c.claim_key = l.claim_key
                JOIN transactions t ON t.transaction_key = l.transaction_key
                WHERE COALESCE(t.superseded, 0) = 0 AND l.claim_status_code IN ('1','19') AND COALESCE(l.paid_cents, 0) = 0
                  AND COALESCE(l.charge_cents, 0) > 0 AND l.service_date >= ?""", (SERVICE_FROM,)).fetchall():
        if is_facility(pcn):
            continue
        later = conn.execute("""SELECT s.paid_on, s.paid, COALESCE(p.pr, 0) FROM sc_ans2 s LEFT JOIN sc_pr p ON p.line_key = s.line_key
                                WHERE s.bill = ? AND s.svc = ? AND s.code = ? AND s.paid_on >= ? AND s.line_key <> ?""",
                             (b, svc, code, paid_on, lk)).fetchall()
        grp, kind = _group_kind(name, cfi)
        exp = usual.get((grp, code)) or int(charge * 0.4)
        paid_after = sum(p for d2, p, _pr in later if d2 > paid_on)
        moved = sum(pr for _d2, _p, pr in later)
        if paid_after >= SETTLED_SHARE * exp or moved >= 0.8 * exp:
            continue
        appeal_days, _filing = _windows(grp, kind)
        deadline = _add(paid_on, appeal_days)
        if not deadline or deadline < today:
            continue
        cents = max(0, min(int(charge), int(exp) - max(0, paid_after)))
        if cents <= 0:
            continue
        held = _covered(idx["service"].get((bill_of(pcn), str(svc)[:10], code)))
        out.append({"key": f"835:{lk}", "category": "denial_in_window", "reason": str(carc), "payer_group": grp,
                    "month": str(svc)[:7], "cents": cents, "deadline": deadline, "held": held})
    for tb in ("sc_den", "sc_ans2", "sc_pr"):
        conn.execute(f"DROP TABLE IF EXISTS temp.{tb}")
    return out, None


def _usual_rates(conn: sqlite3.Connection) -> dict:
    rates: dict[tuple, list] = defaultdict(list)
    for name, cfi, code, allowed in conn.execute(
            """SELECT l.payer_name, c.claim_filing_indicator, COALESCE(l.original_procedure_code, l.procedure_code),
                      COALESCE(l.allowed_cents, l.paid_cents) FROM lines l JOIN claims c ON c.claim_key = l.claim_key
               WHERE l.claim_status_code IN ('1','19') AND COALESCE(l.paid_cents, 0) > 0 AND COALESCE(l.paid_units, 1) <= 1
                 AND l.service_date >= ?""", (_add(SERVICE_FROM, -365),)):
        if allowed and allowed > 0:
            rates[(_group_kind(name, cfi)[0], code)].append(int(allowed))
    return {k: _pct(v, 50) for k, v in rates.items() if len(v) >= 3}


def _cov_auto_writeoffs(conn: sqlite3.Connection, today: str, idx: dict) -> tuple[list, str | None]:
    """Gate-1 rule set, EMA's automatic write-offs: a 2025+ EMA line whose denial EMA wrote off on the day the payer's
    answer posted, with no insurance money after it, still inside the payer's window from that day."""
    if not _has_table(conn, "ema_lines"):
        return [], "automatic write-offs: no EMA lines"
    out = []
    for link, svc, cpt, grp, kind, nonroutine, ins, denial_on, first_ans, last_paid, charges, fac in conn.execute(
            """SELECT link, svc, cpt, payer_group, payer_kind, COALESCE(nonroutine, 0), COALESCE(ins_paid, 0), denial_first_posted,
                      first_ins_posting, last_ins_paid, COALESCE(charges, 0), COALESCE(facility, 0)
               FROM ema_lines WHERE svc >= ? AND COALESCE(nonroutine, 0) > 0 AND denial_first_posted IS NOT NULL""", (SERVICE_FROM,)):
        if fac or (last_paid and denial_on and last_paid > denial_on):
            continue
        if ins > 0:
            continue                    # something was paid: a short payment, not a write-off of the whole line
        if not first_ans or denial_on != first_ans:
            continue                    # written off on a later day: a person's decision (not EMA's posting rule)
        appeal_days, _f = _windows(grp, kind)
        deadline = _add(denial_on, appeal_days)
        if not deadline or deadline < today:
            continue
        cents = min(int(nonroutine), int(charges)) if charges > 0 else int(nonroutine)
        held = _covered(idx["link"].get((int(link), str(svc)[:10], cpt)))
        out.append({"key": f"ema:{link}|{svc}|{cpt}", "category": "auto_written_off_appealable", "reason": "posting_rule",
                    "payer_group": grp, "month": str(svc)[:7], "cents": cents, "deadline": deadline, "held": held})
    return out, None


def _cov_unanswered(conn: sqlite3.Connection, today: str, idx: dict) -> tuple[list, str | None]:
    """Gate-1 rule set, unanswered claims: a 2025+ EMA line billed to insurance with no insurance answer posted, older
    than the payer's answer time (at least 30 days), still inside its first-filing limit (plus the payer's dispute window
    for a claim sent in time, as the answer key allows)."""
    if not _has_table(conn, "ema_lines"):
        return [], "unanswered claims: no EMA lines"
    turn = meta_get(conn, "ema:turnaround", {}) or {}
    groups = (turn.get("groups") or {}) if isinstance(turn, dict) else {}
    out = []
    for link, svc, cpt, grp, kind, charges, ins_charges, first_charge, answers, bal, fac, status in conn.execute(
            """SELECT link, svc, cpt, payer_group, payer_kind, COALESCE(charges, 0), COALESCE(ins_charges, 0), first_charge_posted,
                      COALESCE(ins_answer_rows, 0), COALESCE(balance, 0), COALESCE(facility, 0), status_class
               FROM ema_lines WHERE svc >= ? AND COALESCE(ins_answer_rows, 0) = 0 AND COALESCE(ins_charges, 0) > 0""", (SERVICE_FROM,)):
        if fac or bal <= 0 or not first_charge:
            continue
        g = groups.get(grp) or {}
        wait = max(30, int(g.get("svc_p95") or g.get("p95") or 45)) if isinstance(g, dict) else 45
        if (_days(first_charge, today) or 0) < wait:
            continue
        appeal_days, filing_days = _windows(grp, kind)
        deadline = _add(svc, filing_days + appeal_days)
        if not deadline or deadline < today:
            continue
        cents = min(int(bal), int(charges)) if charges > 0 else int(bal)
        entries = list(idx["link"].get((int(link), str(svc)[:10], cpt)) or [])
        held = _covered(entries)
        out.append({"key": f"ema:{link}|{svc}|{cpt}", "category": "unanswered_in_filing_limit", "reason": str(status or "no_answer"),
                    "payer_group": grp, "month": str(svc)[:7], "cents": cents, "deadline": deadline, "held": held})
    return out, None


def _cov_unposted(conn: sqlite3.Connection, today: str, idx: dict) -> tuple[list, str | None]:
    """Gate-1 rule set, 835 money never posted: a funded 2025+ remittance (not a $0 or informational one) older than three
    weeks whose check number EMA never posted for that payer, by its claims."""
    if not (_has_table(conn, "transactions") and _has_table(conn, "ema_checks")):
        return [], "unposted 835 payments: no EMA checks"
    if not conn.execute("SELECT 1 FROM ema_checks LIMIT 1").fetchone():
        return [], "unposted 835 payments: no EMA checks"
    posted = {_digits(c) for (c,) in conn.execute("SELECT check_no FROM ema_checks")}
    newest_prod = conn.execute("SELECT MAX(last_posted) FROM ema_checks").fetchone()[0] or today
    cut = _add(min(today, newest_prod), -21)
    out = []
    for tk, trace, paid_on, cents, method, payer in conn.execute(
            """SELECT transaction_key, trace_number, payment_date, COALESCE(payment_amount_cents, 0), payment_method, payer_name
               FROM transactions WHERE COALESCE(superseded, 0) = 0 AND payment_date >= ? AND payment_date <= ?
                 AND COALESCE(payment_amount_cents, 0) > 0 AND COALESCE(payment_method, '') <> 'NON'""", (SERVICE_FROM, cut)):
        if _digits(trace) in posted:
            continue
        held = None
        for pcn, svc, code in conn.execute("""SELECT c.patient_control_number, l.service_date, COALESCE(l.original_procedure_code, l.procedure_code)
                                              FROM claims c JOIN lines l ON l.claim_key = c.claim_key WHERE c.transaction_key = ?
                                                AND COALESCE(l.paid_cents, 0) > 0""", (tk,)):
            h = _covered(idx["service"].get((bill_of(pcn), str(svc)[:10], code)))
            if h == "listed" or (h and not held):
                held = h
        out.append({"key": f"check:{tk}", "category": "era_payment_not_posted", "reason": "check_not_posted",
                    "payer_group": _group_kind(payer)[0], "month": str(paid_on)[:7], "cents": int(cents), "deadline": None,
                    "held": held})
    return out, None


def _cov_secondary(conn: sqlite3.Connection, today: str, idx: dict) -> tuple[list, str | None]:
    """Gate-1 rule set, second payer never billed: a 2025+ EMA line whose primary paid more than 30 days ago, with a second
    payer on file (not Medicaid), no money or answer from any other payer, and a balance left on it."""
    if not (_has_table(conn, "ema_lines") and _has_table(conn, "ema_line_payments")):
        return [], "second payers: no EMA lines"
    out = []
    for link, svc, cpt, grp, second, ins, bal, last_paid, fac in conn.execute(
            """SELECT link, svc, cpt, payer_group, secondary_payer, COALESCE(ins_paid, 0), COALESCE(balance, 0), last_ins_paid,
                      COALESCE(facility, 0)
               FROM ema_lines WHERE svc >= ? AND secondary_payer IS NOT NULL AND secondary_payer NOT IN ('', 'None')
                 AND COALESCE(ins_paid, 0) > 0 AND COALESCE(balance, 0) > 0""", (SERVICE_FROM,)):
        if fac or re.search(r"MEDICAID|PASSPORT|WELLCARE OF KENTUCKY|HUMANA HEALTHY", str(second).upper()):
            continue
        if not last_paid or (_days(last_paid, today) or 0) < 30:
            continue
        roles = {r for (r,) in conn.execute("SELECT DISTINCT role FROM ema_line_payments WHERE link=? AND svc=? AND cpt=?",
                                            (link, svc, cpt))}
        if roles - {"primary"}:
            continue
        _a, filing_days = _windows(second, "supplement")
        deadline = _add(last_paid, filing_days)
        if not deadline or deadline < today:
            continue
        held = _covered(idx["link"].get((int(link), str(svc)[:10], cpt)))
        out.append({"key": f"ema:{link}|{svc}|{cpt}", "category": "secondary_not_billed", "reason": "second_payer",
                    "payer_group": grp, "month": str(svc)[:7], "cents": int(bal), "deadline": deadline, "held": held})
    return out, None


def _cov_short_pay(conn: sqlite3.Connection, today: str, idx: dict) -> tuple[list, str | None]:
    """Gate-1 rule set, short payments: a 2025+ one-unit CPT line its payer paid below the payer's own consistent rate for
    the code (the most frequent allowed amount, held by at least 60% of at least 8 lines that year) by more than
    max($2, 3%) and less than 85% of it is not the reason (a non-physician rate), still inside the window."""
    if not _has_table(conn, "lines"):
        return [], "short payments: no remittances"
    modes: dict[tuple, Counter] = defaultdict(Counter)
    rows = conn.execute("""SELECT l.line_key, l.patient_control_number, l.service_date, l.procedure_code, l.payment_date, l.payer_name,
                                  c.claim_filing_indicator, COALESCE(l.allowed_cents, 0), COALESCE(l.paid_cents, 0), l.modifiers
                           FROM lines l JOIN claims c ON c.claim_key = l.claim_key JOIN transactions t ON t.transaction_key = l.transaction_key
                           WHERE COALESCE(t.superseded, 0) = 0 AND l.claim_status_code IN ('1','19') AND COALESCE(l.paid_cents, 0) > 0
                             AND COALESCE(l.paid_units, 1) = 1 AND l.service_date >= ? AND l.procedure_code GLOB '[0-9][0-9][0-9][0-9][0-9]'""",
                        (SERVICE_FROM,)).fetchall()
    for _lk, _pcn, svc, code, _pd, name, cfi, allowed, _paid, mods in rows:
        if allowed > 0 and not mods:
            modes[(_group_kind(name, cfi)[0], code, str(svc)[:4])][allowed] += 1
    usual = {}
    for k, cnt in modes.items():
        n = sum(cnt.values())
        top, m = cnt.most_common(1)[0]
        if n >= 8 and m / n >= 0.6:
            usual[k] = top
    out = []
    for lk, pcn, svc, code, paid_on, name, cfi, allowed, paid, mods in rows:
        if mods or is_facility(pcn):
            continue
        grp, kind = _group_kind(name, cfi)
        rate = usual.get((grp, code, str(svc)[:4]))
        if not rate or allowed <= 0:
            continue
        short = rate - allowed
        if short <= max(200, int(rate * 0.03)) or abs(allowed - int(rate * 0.85)) <= max(200, int(rate * 0.03)):
            continue
        appeal_days, _f = _windows(grp, kind)
        deadline = _add(paid_on, appeal_days)
        if not deadline or deadline < today:
            continue
        held = _covered(idx["service"].get((bill_of(pcn), str(svc)[:10], code)))
        out.append({"key": f"835:{lk}", "category": "short_vs_payer_rate", "reason": "below_own_rate", "payer_group": grp,
                    "month": str(svc)[:7], "cents": int(short), "deadline": deadline, "held": held})
    return out, None


def _precision(conn: sqlite3.Connection, today: str) -> list[dict]:
    """Gate-2 rule set over the Work now items: settled (paid after or before by the payer, another payer, or the patient's
    share), already worked and waiting, a duplicate listing, a facility claim, or past every window."""
    items = _work_now_money(conn)
    if not items:
        return []
    later = later_payments(conn, [it for it in items if it.get("signal") not in CLAIM_SIGNALS])
    before = _earlier_payments(conn, items)
    worked = _work_after(conn, items)
    out = []
    groups: dict[tuple, list] = defaultdict(list)
    for it in items:
        key = (bill_of(it.get("patient_control_number")), str(it.get("service_date") or "")[:10],
               it.get("billed_code") or it.get("original_procedure_code") or it.get("procedure_code"), it.get("signal"))
        if all(key[:3]):
            groups[key].append(it)
    dup: set[str] = set()
    for members in groups.values():
        if len(members) > 1:
            members.sort(key=lambda x: (str(x.get("payment_date") or ""), x["finding_id"]))
            dup.update(m["finding_id"] for m in members[:-1])
    grace = _grace_days()
    for it in items:
        fid = it["finding_id"]
        target = int(it.get("shortfall_cents") or 0) or max(0, int(it.get("expected_cents") or 0) - int(it.get("paid_cents") or 0))
        reason = None
        lp = later.get(fid) or {}
        bp = before.get(fid) or {}
        last = max([str(x)[:10] for x in (it.get("appeal_deadline"), it.get("legal_deadline")) if x] or [""])
        if target > 0 and lp.get("total", 0) >= SETTLED_SHARE * target and lp.get("total", 0) > 0:
            reason = "paid_after" if lp.get("same_payer", 0) > 0 else "paid_other_payer"
        elif target > 0 and bp.get("paid", 0) >= SETTLED_SHARE * target and bp.get("balance_ok"):
            reason = "paid_before"
        elif is_facility(it.get("patient_control_number")):
            reason = "asc_twin"
        elif fid in worked:
            reason = "worked_waiting"
        elif fid in dup:
            reason = "duplicate_listing"
        elif last and last < (_add(today, -grace) or ""):
            reason = "expired"
        if reason:
            out.append({"finding_id": fid, "reason": reason, "signal": it.get("signal"), "category": it.get("category"),
                        "payer_group": it.get("payer_group"), "month": str(it.get("service_date") or "")[:7],
                        "cents": target, "deadline": last or None})
    return out


def _earlier_payments(conn: sqlite3.Connection, items: list[dict]) -> dict[str, dict]:
    """The same payer's money on the same bill, date and code BEFORE the item's remittance, net of reversals, and whether
    EMA's line balance is $1 or less (gate 2: paid before)."""
    out: dict[str, dict] = {}
    by_bill: dict[str, list] = defaultdict(list)
    for it in items:
        b = bill_of(it.get("patient_control_number"))
        if b and it.get("service_date"):
            by_bill[b].append(it)
    bal: dict[tuple, int] = {}
    if _has_table(conn, "ema_lines"):
        links = sorted({int(it["ema_link"]) for it in items if it.get("ema_link")})
        for i in range(0, len(links), 500):
            part = links[i:i + 500]
            for link, svc, cpt, b in conn.execute(f"SELECT link, svc, cpt, COALESCE(balance, 0) FROM ema_lines WHERE link IN "
                                                  f"({','.join('?' * len(part))})", part):
                bal[(int(link), str(svc)[:10], cpt)] = int(b)
    for b, its in by_bill.items():
        rows = conn.execute("""SELECT c.patient_control_number, l.service_date, l.procedure_code, l.original_procedure_code, l.payment_date,
                                      COALESCE(l.paid_cents, 0), c.payer_name, c.claim_filing_indicator
                               FROM claims c JOIN lines l ON l.claim_key = c.claim_key JOIN transactions t ON t.transaction_key = c.transaction_key
                               WHERE c.patient_control_number >= ? AND c.patient_control_number < ? AND COALESCE(t.superseded, 0) = 0""",
                            (b, b + "~")).fetchall()
        for it in its:
            codes = set(_codes_of(it))
            svc = str(it.get("service_date") or "")[:10]
            on = str(it.get("payment_date") or "")[:10]
            paid = sum(p for pcn, sd, pc, opc, pd, p, name, cfi in rows
                       if bill_of(pcn) == b and not is_facility(pcn) and str(sd or "")[:10] == svc and ({pc, opc} & codes)
                       and pd and str(pd)[:10] < on and _group_kind(name, cfi)[0] == it.get("payer_group"))
            link = it.get("ema_link")
            balance_ok = True
            if link:
                bl = [bal.get((int(link), svc, c)) for c in codes if (int(link), svc, c) in bal]
                balance_ok = bool(bl) and all(x <= 100 for x in bl)
            out[it["finding_id"]] = {"paid": paid, "balance_ok": balance_ok}
    return out


def _work_after(conn: sqlite3.Connection, items: list[dict]) -> set[str]:
    """Items with dated work after their adverse answer, within 90 days of today (gate 2: worked and waiting): a person's
    status change or note in RemitWatch, EMA's claim action on the line, an A/R note about an appeal, call or resubmission."""
    out: set[str] = set()
    fids = [it["finding_id"] for it in items]
    on = {it["finding_id"]: str(it.get("payment_date") or "")[:10] for it in items}
    if _has_table(conn, "finding_events"):
        for i in range(0, len(fids), 500):
            part = fids[i:i + 500]
            for fid, at in conn.execute(f"""SELECT finding_id, MAX(at) FROM finding_events WHERE actor <> 'system' AND kind = 'manual'
                                            AND finding_id IN ({','.join('?' * len(part))}) GROUP BY finding_id""", part):
                if at and str(at)[:10] >= on.get(fid, ""):
                    out.add(fid)
    if _has_table(conn, "ema_lines"):
        cols = set(_cols(conn, "ema_lines"))
        if {"action", "action_on"} <= cols:
            by_link: dict[int, list] = defaultdict(list)
            for it in items:
                if it.get("ema_link"):
                    by_link[int(it["ema_link"])].append(it)
            links = sorted(by_link)
            for i in range(0, len(links), 500):
                part = links[i:i + 500]
                for link, svc, action, action_on in conn.execute(
                        f"""SELECT link, svc, action, action_on FROM ema_lines WHERE link IN ({','.join('?' * len(part))})
                            AND action IS NOT NULL AND action NOT IN ('', 'None')""", part):
                    if not re.search(r"APPEAL|CORRECT|RESUBMIT|REPROCESS|RECORDS", str(action), re.I):
                        continue
                    for it in by_link[int(link)]:
                        if str(svc)[:10] == str(it.get("service_date") or "")[:10] and action_on and str(action_on)[:10] >= on[it["finding_id"]] \
                                and (_days(action_on, today_of()) or 0) <= 90:
                            out.add(it["finding_id"])
    if _has_table(conn, "ema_ar") and "note_kind" in set(_cols(conn, "ema_ar")):
        by_link2: dict[int, list] = defaultdict(list)
        for it in items:
            if it.get("ema_link"):
                by_link2[int(it["ema_link"])].append(it)
        links = sorted(by_link2)
        for i in range(0, len(links), 500):
            part = links[i:i + 500]
            for link, kinds, nd in conn.execute(f"""SELECT link, note_kind, note_date FROM ema_ar WHERE link IN ({','.join('?' * len(part))})
                                                    AND note_kind IS NOT NULL""", part):
                if re.search(r"appeal|call|resubmitted|records|reprocess", str(kinds or "")):
                    for it in by_link2[int(link)]:
                        if nd and str(nd)[:10] >= on[it["finding_id"]] and (_days(nd, today_of()) or 0) <= 90:
                            out.add(it["finding_id"])
    return out


def audit_lists(conn: sqlite3.Connection, list_name: str = "gap", *, audit_id: str | None = None, limit: int = 500) -> list[dict]:
    """The latest self-audit's list ('gap' or 'wasted'), largest first, for the Data tab (gaps carry no finding id: their
    key names the 835 line, the EMA line or the check)."""
    ensure_tables(conn)
    aid = audit_id or (conn.execute("SELECT audit_id FROM selfcheck_audits ORDER BY today DESC, at DESC LIMIT 1").fetchone() or [None])[0]
    if not aid:
        return []
    cur = conn.execute("""SELECT key, finding_id, category, reason, payer_group, service_month, cents, deadline, new
                          FROM selfcheck_audit_items WHERE audit_id = ? AND list = ? ORDER BY cents DESC LIMIT ?""", (aid, list_name, limit))
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


# =====================================================================================================================
# 5. Pattern watch
# =====================================================================================================================
def _asof(conn: sqlite3.Connection, today: str) -> str:
    if _has_table(conn, "transactions"):
        r = conn.execute("SELECT MAX(payment_date) FROM transactions WHERE COALESCE(superseded, 0) = 0").fetchone()
        if r and r[0]:
            return str(r[0])[:10]
    return today


def _poisson_sf(k: int, lam: float) -> float:
    if k <= 0:
        return 1.0
    if lam <= 0:
        return 0.0
    if lam > 400:
        return 0.5 * math.erfc(((k - 0.5 - lam) / math.sqrt(lam)) / math.sqrt(2))
    term = math.exp(-lam)
    cdf = term
    for i in range(1, k):
        term *= lam / i
        cdf += term
    return max(0.0, 1.0 - cdf)


COVERING_KINDS = ("RAMP", "RISING", "NEW_BEHAVIOR", "RESUMED", "NEW_REASON_CODE")


def pattern_watch(conn: sqlite3.Connection, *, today: Any = None, asof: Any = None, active: dict | None = None,
                  save: bool = True) -> list[dict]:
    """The rule-based pattern alerts (alert dicts as alerts.AlertEngine raises them: kind, severity, title, detail,
    payer_group, key, n, cents). The last 30 days against the 90 before them, from the newest remittance's date."""
    t = today_of(today)
    a = str(asof)[:10] if asof else _asof(conn, t)
    out: list[dict] = []
    covered = set()
    for v in (active or {}).values():
        if v.get("kind") in COVERING_KINDS:
            covered.add(v.get("payer_group"))
    for fn in (_pw_denials, _pw_credentialing, _pw_below_group, _pw_batch):
        try:
            out.extend(fn(conn, a, covered))
        except sqlite3.Error as exc:
            out.append({"kind": "PATTERN_ERROR", "severity": "info", "title": f"A pattern rule could not run ({type(exc).__name__})",
                        "detail": {"rule": fn.__name__}, "payer_group": None, "key": ("PATTERN_ERROR", fn.__name__), "n": 0, "cents": 0})
    if save:
        meta_set(conn, K_PATTERNS, {"at": now_of(), "asof": a, "alerts": [{k: x[k] for k in ("kind", "severity", "title", "payer_group", "n", "cents")}
                                                                         for x in out][:60]})
    return out


def _pw_denials(conn: sqlite3.Connection, asof: str, covered: set) -> list[dict]:
    """A new or rising denial at payer group x code x modifier x reason x remark grain, from the items (denials and
    bundling, not let go): at least 5 in the last 30 days and none in the 90 before (new), or at least twice the earlier
    daily rate with p < 0.01 (rising). Skipped where an existing payer-level alert covers the payer."""
    cols = set(_cols(conn, "findings"))
    if "payment_date" not in cols:
        return []
    r0, b0 = _add(asof, -30), _add(asof, -120)
    rarc = "rarc" if "rarc" in cols else "NULL"
    billed = "billed_code" if "billed_code" in cols else "procedure_code"
    recent, base, cents, ids = Counter(), Counter(), Counter(), defaultdict(list)
    for fid, grp, code, mods, carc, rc, pd, sc in conn.execute(
            f"""SELECT finding_id, payer_group, COALESCE({billed}, procedure_code), modifiers, carc, {rarc}, payment_date,
                       COALESCE(shortfall_cents, 0) FROM findings
                WHERE COALESCE(stale, 0) = 0 AND status <> 'dismissed' AND category IN ('denial', 'bundling')
                  AND payment_date > ? AND payment_date <= ? AND payer_group IS NOT NULL AND carc IS NOT NULL""", (b0, asof)):
        k = (grp, code or "", str(mods or "").split(",")[0].strip(), str(carc), str(rc or "").split(",")[0].strip())
        if pd > r0:
            recent[k] += 1
            cents[k] += max(sc, 0)
            ids[k].append(fid)
        else:
            base[k] += 1
    out = []
    for k, n in recent.items():
        if n < 5 or k[0] in covered:
            continue
        b = base.get(k, 0)
        grp, code, mod, carc, rc = k
        what = f"{code}{'-' + mod if mod else ''}, reason {carc}{' / ' + rc if rc else ''}"
        if b == 0:
            kind, title = "DENIAL_PATTERN", f"{grp} began denying {what}: {n} in the last 30 days ({usd0(cents[k])}), none in the 90 before"
        else:
            lam = b / 90 * 30
            p = _poisson_sf(n, lam)
            if not (n >= 2 * lam and p < 0.01):
                continue
            kind, title = "DENIAL_PATTERN", f"{grp} is denying {what} more often: {n} in the last 30 days against about {lam:.1f} before ({usd0(cents[k])})"
        out.append({"kind": kind, "severity": "high" if cents[k] >= 250000 or n >= 15 else "medium", "title": title,
                    "detail": {"pattern": list(k), "recent": n, "before": b, "items": ids[k][:EXAMPLES], "window": [r0, asof]},
                    "payer_group": grp, "key": ("DENIAL_PATTERN",) + k, "n": n, "cents": cents[k]})
    return out


def pattern_breakdown(conn: sqlite3.Connection, payer_group: str, *, asof: Any = None, limit: int = 8) -> list[dict]:
    """For an existing RAMP or RISING alert on a payer: which code x modifier x reason drives it (the last 30 days), so the
    alert can be extended rather than duplicated."""
    a = str(asof)[:10] if asof else _asof(conn, today_of())
    cols = set(_cols(conn, "findings"))
    billed = "billed_code" if "billed_code" in cols else "procedure_code"
    rows = conn.execute(f"""SELECT COALESCE({billed}, procedure_code), carc, COUNT(*), COALESCE(SUM(shortfall_cents), 0) FROM findings
                            WHERE COALESCE(stale, 0) = 0 AND payer_group = ? AND category IN ('denial','bundling','downcoding','underpayment')
                              AND payment_date > ? AND payment_date <= ? GROUP BY 1, 2 ORDER BY 3 DESC LIMIT ?""",
                        (payer_group, _add(a, -30), a, limit)).fetchall()
    return [{"code": c, "reason": r, "n": n, "cents": cents} for c, r, n, cents in rows]


def _provider_names(conn: sqlite3.Connection) -> dict:
    if not _has_table(conn, "providers"):
        return {}
    return {npi: name for npi, name in conn.execute("SELECT npi, name FROM providers")}


def _pw_credentialing(conn: sqlite3.Connection, asof: str, covered: set) -> list[dict]:
    """A new clinician (first remittance line within 180 days) whose claims a payer denies for credentialing or enrollment:
    at least 3 lines in the last 30 days with an enrollment reason or remark."""
    if not (_has_table(conn, "lines") and _has_table(conn, "adjustments")):
        return []
    r0 = _add(asof, -30)
    first = {npi: d for npi, d in conn.execute("""SELECT rendering_provider_npi, MIN(payment_date) FROM lines
                                                   WHERE rendering_provider_npi IS NOT NULL GROUP BY 1""")}
    new = {npi for npi, d in first.items() if d and d >= (_add(asof, -180) or "")}
    if not new:
        return []
    marks = ",".join("?" * len(PROVIDER_CARCS))
    rmarks = ",".join("?" * len(PROVIDER_RARCS))
    hits: dict[tuple, list] = defaultdict(list)
    for npi, name, cfi, charge, lk in conn.execute(
            f"""SELECT l.rendering_provider_npi, l.payer_name, c.claim_filing_indicator, COALESCE(l.charge_cents, 0), l.line_key
                FROM lines l JOIN claims c ON c.claim_key = l.claim_key
                WHERE l.payment_date > ? AND l.payment_date <= ? AND COALESCE(l.paid_cents, 0) = 0
                  AND (EXISTS (SELECT 1 FROM adjustments a WHERE a.claim_key = l.claim_key AND a.reason_code IN ({marks}))
                       OR EXISTS (SELECT 1 FROM remarks m WHERE m.claim_key = l.claim_key AND m.code IN ({rmarks})))""",
            (r0, asof, *sorted(PROVIDER_CARCS), *sorted(PROVIDER_RARCS))):
        if npi in new:
            hits[(npi, _group_kind(name, cfi)[0])].append((charge, lk))
    names = _provider_names(conn)
    out = []
    for (npi, grp), rows in hits.items():
        if len(rows) < 3:
            continue
        cents = sum(c for c, _l in rows)
        who = names.get(npi) or "a new clinician"
        fids = _finding_ids_for_lines(conn, [lk for _c, lk in rows])
        out.append({"kind": "CREDENTIALING", "severity": "high", "payer_group": grp, "n": len(rows), "cents": cents,
                    "title": f"{grp} is denying {who}'s claims for enrollment or credentialing: {len(rows)} lines in 30 days "
                             f"({usd0(cents)} billed). Check the clinician's enrollment with this payer.",
                    "detail": {"npi": npi, "first_line": first.get(npi), "lines": len(rows), "items": fids[:EXAMPLES]},
                    "key": ("CREDENTIALING", npi, grp)})
    return out


def _finding_ids_for_lines(conn: sqlite3.Connection, line_keys: list[str]) -> list[str]:
    out = []
    for i in range(0, len(line_keys), 500):
        part = line_keys[i:i + 500]
        out += [r[0] for r in conn.execute(f"SELECT finding_id FROM findings WHERE line_key IN ({','.join('?' * len(part))})", part)]
    return out


def _pw_below_group(conn: sqlite3.Connection, asof: str, covered: set) -> list[dict]:
    """A clinician paid below the practice's own rate for the same code and payer: over the last 90 days, the clinician's
    usual allowed amount for a one-unit code is at least 5% (and $2) below the most frequent amount the payer allowed
    the other clinicians of the same tier, with 5 lines of theirs and 10 of the others'."""
    if not _has_table(conn, "lines"):
        return []
    tiers = {npi: tier for npi, tier in conn.execute("SELECT npi, tier FROM provider_tiers")} if _has_table(conn, "provider_tiers") else {}
    by: dict[tuple, Counter] = defaultdict(Counter)
    for npi, name, cfi, code, allowed in conn.execute(
            """SELECT l.rendering_provider_npi, l.payer_name, c.claim_filing_indicator, l.procedure_code, l.allowed_cents
               FROM lines l JOIN claims c ON c.claim_key = l.claim_key
               WHERE l.payment_date > ? AND l.payment_date <= ? AND l.claim_status_code IN ('1','19')
                 AND COALESCE(l.paid_cents, 0) > 0 AND COALESCE(l.paid_units, 1) = 1 AND l.rendering_provider_npi IS NOT NULL
                 AND COALESCE(l.allowed_cents, 0) > 0 AND (l.modifiers IS NULL OR l.modifiers IN ('', '[]'))""",
            (_add(asof, -90), asof)):
        by[(_group_kind(name, cfi)[0], code, tiers.get(npi) or "unknown", npi)][allowed] += 1
    pools: dict[tuple, Counter] = defaultdict(Counter)
    for (grp, code, tier, npi), cnt in by.items():
        pools[(grp, code, tier)].update(cnt)
    names = _provider_names(conn)
    out = []
    for (grp, code, tier, npi), cnt in by.items():
        n = sum(cnt.values())
        if n < 5:
            continue
        others = pools[(grp, code, tier)] - cnt
        n_o = sum(others.values())
        if n_o < 10:
            continue
        mode_o = others.most_common(1)[0][0]
        mine = _pct(sorted(a for a, k in cnt.items() for _ in range(k)), 50)
        if mine is None or mine >= mode_o * 0.95 or mode_o - mine < 200:
            continue
        cents = (mode_o - mine) * n
        who = names.get(npi) or "one clinician"
        out.append({"kind": "CLINICIAN_BELOW_GROUP", "severity": "medium", "payer_group": grp, "n": n, "cents": cents,
                    "title": f"{grp} pays {who} {usd(mine)} for {code}, below the {usd(mode_o)} it pays the practice's other "
                             f"clinicians ({n} lines in 90 days, about {usd0(cents)} short). Check the clinician's enrollment and fee schedule.",
                    "detail": {"npi": npi, "code": code, "tier": tier, "clinician_cents": mine, "others_cents": mode_o, "lines": n,
                               "others_lines": n_o}, "key": ("CLINICIAN_BELOW_GROUP", npi, grp, code)})
    return out


def _pw_batch(conn: sqlite3.Connection, asof: str, covered: set) -> list[dict]:
    """A payer that reprocessed and paid a batch of identical denials (payer group x code x reason) in the last 30 days, at
    least 3 closed by a later payment, while at least 2 of the same pattern are still Work now: the rest of the batch is
    one call to the payer (unit B's rule makes it one item; this raises the alert)."""
    cols = set(_cols(conn, "findings"))
    if not {"auto_note", "work_state"} <= cols:
        return []
    r0 = _add(asof, -30)
    paid = defaultdict(list)
    for fid, grp, code, carc, ev in conn.execute("""SELECT finding_id, payer_group, procedure_code, carc, evidence FROM findings
                                                     WHERE status = 'resolved' AND auto_note IS NOT NULL AND COALESCE(stale, 0) = 0
                                                       AND category IN ('denial','bundling') AND carc IS NOT NULL"""):
        pl = _ev(ev).get("paid_later") or {}
        when = str(pl.get("payment_date") or "")[:10] if isinstance(pl, dict) else ""
        if when and r0 < when <= asof:
            paid[(grp, code, str(carc))].append(fid)
    out = []
    for k, closed in paid.items():
        if len(closed) < 3:
            continue
        open_ = conn.execute("""SELECT finding_id, COALESCE(shortfall_cents, 0) FROM findings WHERE COALESCE(stale, 0) = 0
                                AND work_state = 'work_now' AND status = 'open' AND payer_group = ? AND procedure_code = ? AND carc = ?""",
                             k).fetchall()
        if len(open_) < 2:
            continue
        cents = sum(c for _f, c in open_)
        grp, code, carc = k
        out.append({"kind": "BATCH_REPROCESSED", "severity": "medium", "payer_group": grp, "n": len(open_), "cents": cents,
                    "title": f"{grp} reprocessed and paid {len(closed)} denials of {code} (reason {carc}) in 30 days; {len(open_)} like them "
                             f"({usd0(cents)}) are still to work: ask {grp} to reprocess the rest as one batch.",
                    "detail": {"paid": closed[:EXAMPLES], "open": [f for f, _c in open_][:EXAMPLES], "pattern": list(k)},
                    "key": ("BATCH_REPROCESSED",) + k})
    return out


# =====================================================================================================================
# The alerts G raises (alerts.AlertEngine raises them, as it does ema_checks.alert_candidates)
# =====================================================================================================================
def alert_candidates(conn: sqlite3.Connection, *, today: Any = None, active: dict | None = None) -> list[dict]:
    """SAFE_MODE (a run held), CHECK_FAILED (a critical check red without a hold), NEAR_LIMIT (nothing more to shed),
    AUDIT_GAPS and WASTED_WORK (the weekly self-audit), TRACK_RECORD (a closure rule reopened too often), GIVEN_UP_RISING
    (money given up without a decision rose this week), and the pattern alerts. Each names its numbers and the items."""
    t = today_of(today)
    out: list[dict] = []
    safe = meta_get(conn, K_SAFE, {}) or {}
    if safe.get("on"):
        n = sum(int(c.get("n") or 0) for c in safe.get("checks") or [])
        out.append({"kind": "SAFE_MODE", "severity": "high", "payer_group": None, "key": ("SAFE_MODE",), "n": n,
                    "cents": sum(int(c.get("cents") or 0) for c in safe.get("checks") or []),
                    "title": "RemitWatch is in safe mode: it kept the worklist from before its last run and held that run's "
                             "automatic closures. " + "; ".join(f"{c['title']}: {c['n']} items" for c in safe.get("checks") or [])
                             + ". Open the Data tab and download the diagnostic bundle.",
                    "detail": {"checks": safe.get("checks"), "since": safe.get("since"), "runs_held": safe.get("runs_held")}})
    last = meta_get(conn, K_LAST, {}) or {}
    for c in last.get("checks") or []:
        if c.get("severity") == "critical" and c.get("status") == "fail" and not safe.get("on"):
            out.append({"kind": "CHECK_FAILED", "severity": "high" if c["id"] in ("money_conservation", "ledger_store", "closure_proof",
                                                                                 "vanished") else "medium",
                        "payer_group": None, "key": ("CHECK_FAILED", c["id"]), "n": c.get("n"), "cents": c.get("cents"),
                        "title": f"Self-check: {c['title'].lower()} failed for {_n(int(c.get('n') or 0), 'item')} ({usd0(c.get('cents'))}). "
                                 f"{c.get('what_to_check') or ''}",
                        "detail": {"check": c["id"], "examples": c.get("examples"), "info": c.get("info")}})
    reg = regulation(conn)
    if reg["level"] >= len(LEVELS) - 1:
        p = reg.get("pressure") or {}
        out.append({"kind": "NEAR_LIMIT", "severity": "high", "payer_group": None, "key": ("NEAR_LIMIT",), "n": None, "cents": None,
                    "title": f"RemitWatch is near its limits: {p.get('text') or 'its last runs passed 80% of a budget'}. Nothing was lost. "
                             f"What it did: {' '.join(reg['actions'][:3])}",
                    "detail": {"pressure": p, "actions": reg["actions"], "since": reg.get("since")}})
    au = (meta_get(conn, K_AUDIT, {}) or {}).get("latest") or {}
    if au.get("alert"):
        if au.get("new_big_gaps"):
            out.append({"kind": "AUDIT_GAPS", "severity": "high", "payer_group": None, "key": ("AUDIT_GAPS", au.get("audit_id")),
                        "n": au.get("new_gaps_n"), "cents": au.get("new_gaps_cents"),
                        "title": f"The weekly self-audit found {_n(int(au.get('new_gaps_n') or 0), 'new line')} with money to recover and no "
                                 f"item ({usd0(au.get('new_gaps_cents'))}); {au.get('new_big_gaps')} over {usd0(au.get('thresholds', {}).get('gap_cents'))}.",
                        "detail": {"by_category": au.get("gaps_by_category"), "audit_id": au.get("audit_id")}})
        if (au.get("new_wasted_n") or 0) > (au.get("thresholds") or {}).get("wasted_items", AUDIT_WASTED_ALERT):
            out.append({"kind": "WASTED_WORK", "severity": "medium", "payer_group": None, "key": ("WASTED_WORK", au.get("audit_id")),
                        "n": au.get("new_wasted_n"), "cents": None,
                        "title": f"The weekly self-audit found {_n(int(au.get('new_wasted_n') or 0), 'new item')} listed Work now that look "
                                 "settled already (paid, worked or duplicate): check them before working them.",
                        "detail": {"by_reason": au.get("wasted_by_reason"), "audit_id": au.get("audit_id")}})
    tr = meta_get(conn, K_TRACK, {}) or {}
    for rule, v in (tr.get("by_rule") or {}).items():
        if (v.get("closures") or 0) >= 20 and (v.get("rate") or 0) > TRACK_ALERT_SHARE:
            out.append({"kind": "TRACK_RECORD", "severity": "medium", "payer_group": None, "key": ("TRACK_RECORD", rule),
                        "n": v.get("reopened"), "cents": None,
                        "title": f"Closures by the rule {rule} were reopened {v.get('reopened')} times out of {v.get('closures')} "
                                 f"({v['rate']:.0%}): the payments it closes on are being taken back.",
                        "detail": v})
    gu = meta_get(conn, K_GIVEN_UP, {}) or {}
    weeks = gu.get("weeks") or []
    if len(weeks) >= 2 and weeks[-1]["cents"] - weeks[-2]["cents"] > GIVEN_UP_ALERT_CENTS:
        out.append({"kind": "GIVEN_UP_RISING", "severity": "medium", "payer_group": None, "key": ("GIVEN_UP_RISING", weeks[-1]["week"]),
                    "n": gu.get("items"), "cents": weeks[-1]["cents"],
                    "title": f"Money written off with no decision rose to {usd0(weeks[-1]['cents'])} this week (from "
                             f"{usd0(weeks[-2]['cents'])}); {usd0(gu.get('recoverable_cents'))} of it can still be appealed.",
                    "detail": {"by_payer": gu.get("by_payer"), "by_reason": gu.get("by_reason")}})
    try:
        out.extend(pattern_watch(conn, today=t, active=active))
    except sqlite3.Error:
        pass
    return out


# =====================================================================================================================
# 6. Housekeeping
# =====================================================================================================================
def housekeeping(*, mode: str, db_path: str | None = None, state: Any = None, conn: sqlite3.Connection | None = None,
                 now: float | None = None, keep_days: int = 30) -> dict:
    """The clean-ups of units A1, A2 and E, called from the cycle with what each did: upload pieces left unfinished for a
    day (uploads.sweep), the ledger's and refreshes' work files (ema_ledger.clean_work_files), kept upload copies the
    reports' catalog no longer needs after `keep_days` (only a file whose report was applied and later replaced or
    superseded, or read as 'already'; the sidecar, the record of what happened, stays; never a held or waiting file's, and
    never the newest of a report), and the archive's compaction when unit E provides one. Each step reports or records
    its error; nothing waits for a person."""
    now = now or time.time()
    out: dict = {"at": now_of(), "mode": mode}
    try:
        from . import uploads
        if mode == "serverless" and state is not None:
            out["uploads"] = uploads.state_uploads(state).sweep(now)
        elif db_path:
            out["uploads"] = uploads.ServerUploads(db_path).sweep(now)
    except Exception as exc:       # noqa: BLE001  (recorded: the next cycle tries again)
        out["uploads"] = {"error": type(exc).__name__}
    try:
        from . import ema_ledger, runtime
        folder = runtime.tmp_dir() if mode == "serverless" else os.path.dirname(os.path.abspath(ema_ledger.ledger_path(db_path))) \
            if db_path else None
        if folder:
            out["work_files"] = ema_ledger.clean_work_files(folder)
    except Exception as exc:       # noqa: BLE001
        out["work_files"] = {"error": type(exc).__name__}
    try:
        out["kept_copies"] = _old_copies(mode, db_path, state, conn, now, keep_days)
    except Exception as exc:       # noqa: BLE001
        out["kept_copies"] = {"error": type(exc).__name__}
    try:
        from . import archive          # unit E's module (phase 3a): its compaction, when it has one
        fn = getattr(archive, "compact", None)
        if fn is not None and conn is not None:
            out["archive"] = fn(conn)
    except ImportError:
        out["archive"] = {"skipped": "no archive module"}
    except Exception as exc:       # noqa: BLE001
        out["archive"] = {"error": type(exc).__name__}
    if conn is not None:
        try:
            meta_set(conn, K_HOUSE, out)
        except sqlite3.Error:
            pass
    return out


def _old_copies(mode: str, db_path: str | None, state: Any, conn: sqlite3.Connection | None, now: float, keep_days: int) -> dict:
    """Kept upload copies (incoming/done on a server, intake/done in the state folder) whose report was applied and is
    covered by a newer file, or that were the same rows as a file on record, older than keep_days: the data file goes, its
    sidecar stays. The newest file of each report, a held or waiting file and anything the catalog does not know stay."""
    if conn is None or not _has_table(conn, "ema_report_files"):
        return {"skipped": "no catalog"}
    cut = now - keep_days * 86400
    cat = {}
    newest: dict[str, tuple] = {}
    for fid, report, status, received in conn.execute("SELECT file_id, report, status, received_at FROM ema_report_files"):
        cat[fid] = (report, status)
        if status in ("current", "partly_replaced") and (report not in newest or (received or "") > newest[report][1]):
            newest[report] = (fid, received or "")
    keep_ids = {v[0] for v in newest.values()}
    removed, kept = 0, 0

    def removable(sc: dict) -> bool:
        res = sc.get("result") or {}
        fid = res.get("file_id") or sc.get("sha256")
        st = res.get("status")
        if fid in keep_ids or st in ("held", "waiting", "failed", "not_read"):
            return False
        if st == "already":
            return True
        c = cat.get(fid)
        return bool(c) and c[1] in ("replaced", "superseded", "discarded")
    if mode == "serverless" and state is not None:
        for e in state.list("intake/done/"):
            name = e.get("name") or ""
            if not name.endswith(".json"):
                continue
            ts = e.get("modified") or e.get("server_modified") or e.get("mtime")
            when = ts if isinstance(ts, (int, float)) else (datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp() if ts else now)
            if when > cut:
                continue
            raw = state.get(f"intake/done/{name}")
            try:
                sc = json.loads(raw or b"{}")
            except ValueError:
                continue
            data = name[:-5]
            if removable(sc) and state.meta(f"intake/done/{data}"):
                if state.delete(f"intake/done/{data}"):
                    removed += 1
            else:
                kept += 1
    elif db_path:
        from . import uploads
        folder = os.path.join(uploads.data_dir(db_path), "incoming", "done")
        if os.path.isdir(folder):
            for name in os.listdir(folder):
                if not name.endswith(".json"):
                    continue
                p = os.path.join(folder, name)
                try:
                    if os.path.getmtime(p) > cut:
                        continue
                    with open(p, "rb") as fh:
                        sc = json.loads(fh.read() or b"{}")
                except (OSError, ValueError):
                    continue
                data = p[:-5]
                if removable(sc) and os.path.exists(data):
                    os.remove(data)
                    removed += 1
                else:
                    kept += 1
    return {"removed": removed, "kept": kept, "keep_days": keep_days}


# =====================================================================================================================
# 7. The panel ("Running on its own") and Data health
# =====================================================================================================================
def panel(conn: sqlite3.Connection) -> dict:
    """The Data tab's "Running on its own" panel, from meta only (so the dashboard's copy shows the same as the store):
    the last run (when, how long, peak memory against the budget, what it shed or archived), the checks with their
    history, safe mode, the last self-audit, the track record, the pattern alerts, the money checks, money given up
    without a decision, blindness and housekeeping. Plain sentences in 'lines'."""
    reg = regulation(conn)
    last = meta_get(conn, K_LAST, {}) or {}
    hist = meta_get(conn, K_HISTORY, []) or []
    safe = meta_get(conn, K_SAFE, {"on": False}) or {"on": False}
    audit = meta_get(conn, K_AUDIT, {}) or {}
    track = meta_get(conn, K_TRACK, {}) or {}
    pats = meta_get(conn, K_PATTERNS, {}) or {}
    money = meta_get(conn, K_MONEY, {}) or {}
    gu = meta_get(conn, K_GIVEN_UP, {}) or {}
    blind = meta_get(conn, K_BLIND, {}) or {}
    house = meta_get(conn, K_HOUSE, {}) or {}
    rates = meta_get(conn, K_RATES, {}) or {}
    checks = []
    for c in last.get("checks") or []:
        checks.append({**{k: c.get(k) for k in ("id", "title", "severity", "status", "n", "cents", "new", "text", "sentence",
                                                "what_to_check", "examples")},
                       "history": [h.get("checks", {}).get(c.get("id"), "-") for h in hist][-14:]})
    lr = reg.get("last_run") or {}
    p = reg.get("pressure") or {}
    lines = []
    if lr.get("started"):
        lines.append(f"Last run {str(lr.get('started'))[:16].replace('T', ' ')}: {lr.get('seconds') or '?'} s"
                     + (f"; {p.get('text')}" if p.get("text") else "") + ".")
    if reg["level"]:
        lines.append(f"Working lighter ({reg['name']}): " + " ".join(reg["actions"]))
    if safe.get("on"):
        lines.append(safe.get("text") or "Safe mode is on.")
    failing = [c for c in checks if c["status"] == "fail"]
    lines.append("Every check passed." if checks and not failing else
                 (f"{_n(len(failing), 'check')} failing: " + "; ".join(c["title"] for c in failing[:4]) + "." if failing else
                  "The checks have not run yet."))
    la = audit.get("latest") or {}
    if la:
        lines.append(f"Self-audit of {la.get('today')}: {_n(int(la.get('gaps_n') or 0), 'line')} with money and no item "
                     f"({usd0(la.get('gaps_cents'))}); {_n(int(la.get('wasted_n') or 0), 'item')} to work that look settled.")
    if gu.get("cents") is not None:
        lines.append(f"Written off with no decision (2025 on): {usd0(gu.get('cents'))}, of which {usd0(gu.get('recoverable_cents'))} can "
                     "still be appealed.")
    if blind.get("blind"):
        lines.append(blind.get("banner") or "")
    return {"title": "Running on its own", "lines": [x for x in lines if x], "regulation": reg, "safe_mode": safe,
            "last_check": {k: last.get(k) for k in ("run_id", "at", "today", "held", "first", "kept", "version")},
            "checks": checks, "audit": audit, "track": track, "patterns": pats, "money": {k: money.get(k) for k in ("at", "ledger_seq",
                                                                                                                  "conservation", "ar_balance")},
            "given_up": gu, "blind": blind, "housekeeping": house, "rates": rates}


def data_health(conn: sqlite3.Connection, today: Any = None) -> list[dict]:
    """Items for insights.data_health (the Data health list), from meta only: blindness first, then safe mode, the failing
    checks (warnings go here, as the brief asks), and the self-audit's gaps. Each item: key, ok, title, detail, who."""
    out = []
    blind = meta_get(conn, K_BLIND, {}) or {}
    if blind.get("blind"):
        out.append({"key": "selfcheck_blind", "ok": False, "title": "RemitWatch cannot see part of the money",
                    "detail": blind.get("banner") or "", "who": "Billing lead: bring the feeds up to date (Upload page, Data tab)."})
    safe = meta_get(conn, K_SAFE, {}) or {}
    if safe.get("on"):
        out.append({"key": "selfcheck_safe_mode", "ok": False, "title": "Safe mode: the worklist is from before the last run",
                    "detail": safe.get("text") or "", "who": "Owner: download the diagnostic bundle from the Data tab for the maintainer."})
    last = meta_get(conn, K_LAST, {}) or {}
    for c in last.get("checks") or []:
        if c.get("status") != "fail" or c.get("id") in ("feed_835", "feed_ema"):
            continue
        out.append({"key": f"selfcheck_{c['id']}", "ok": False, "severity": c.get("severity"),
                    "title": c.get("text") or c.get("title"), "detail": c.get("sentence") or "",
                    "who": "Maintainer: " + (c.get("what_to_check") or "see the diagnostic bundle")})
    la = (meta_get(conn, K_AUDIT, {}) or {}).get("latest") or {}
    if la.get("gaps_n"):
        out.append({"key": "selfcheck_audit", "ok": False,
                    "title": f"{_n(int(la['gaps_n']), 'line')} with money to recover have no item ({usd0(la.get('gaps_cents'))})",
                    "detail": f"From the self-audit of {la.get('today')}. " + "; ".join(f"{x['category'].replace('_', ' ')}: {x['n']}"
                                                                                     for x in (la.get("gaps_by_category") or [])[:4]),
                    "who": "Maintainer: the audit's gap list is on the Data tab (Running on its own)."})
    return out
