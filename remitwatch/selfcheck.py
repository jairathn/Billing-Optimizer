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
   past 80% of a budget (serverless: a 2,188 MB memory budget, whose 80% mark is release gate 3's 1.75 GB of the 2,278 MB
   function, so Vercel's normal runs of 1.3-1.5 GB are calm; its time budget; 512 MB of /tmp; server: the configured
   budgets), or killed by the platform before it wrote any status (runlog's open marker, found when the next run's log
   starts), it steps up one level, in this order, each step logged: 1 spread the work across runs (one report file per
   run, detection in a fresh invocation), 2 run the archive at the tighter end of its allowed horizon (unit E's bounds),
   3 leave the optional steps (provider tables, report email, export-time backfill, the weekly audit) for a calm run, but
   never one of them for more than MAX_DEFER_DAYS (then it runs, in a run that does no detection, before it may be left
   again), and 4, when nothing more can be shed, one alert ("near its memory limit ... nothing was lost ... this is what
   it did"). Three calm runs in a row step it down again.

2. The self-check (begin_detection, after_detection, check_ledger, check_serving; CHECKS). A battery of rule-based
   invariants over the run's output, each with a name, a plain sentence, the count of offending items, their dollars and
   up to 20 example finding ids. Results are kept per run (meta selfcheck:history), so a check that starts failing shows
   as a change. A critical check (money or state) that finds offenders the last good run did not have, beyond its small
   tolerance, puts the run in SAFE MODE: the store keeps everything the run read and worked out (the run's own items are
   kept, compressed, in selfcheck_held for diagnosis), but the worklist people see goes back to the items as they were
   before the run (the last good run's), with every status and note a person saved while the run ran put back on top,
   and with the new items to work the run found that no failing check names (a hold never hides new work); the run's
   automatic closures are held, and an alert says why and what to check. The next good run releases it. Offenders the
   last good run already had keep the check red and its alert on without freezing the worklist again (holding would not
   remove them, and would stop new work from showing); a person can accept a change on the Data tab (accept_findings: a
   business decision, recorded with who and when, for exactly the offenders of the run the person looked at). The first
   run of the self-check sets the baseline and holds nothing. An open item people were shown that disappears, or that
   detection keeps only as a stale row (an item a person worked that no rule produces any more), with no reason (no
   closure, no item this run made for its line, not folded, not superseded, nothing new on its claim since the run
   before) is KEPT on the worklist with a note on every run, the first included, until a person accepts the change.

3. The weekly self-audit (self_audit). Independent of the detection code: a separate, simpler rule set over the raw
   tables (the 835 archive and the store's EMA lines, checks and A/R, which keep every line its rules look at), on the
   rules of the frozen answer keys (gate 1: coverage of every 2025+ line with recoverable dollars inside a window; gate 2:
   precision of the WORK NOW list). A line counts as covered only when it is listed or a person (or the owner's policy)
   decided it; one RemitWatch closed, expired or set aside by itself is a gap the audit names with its item. It lists
   coverage gaps (with dollars) and possible wasted work (with the reason), keeps the confusion matrix week by week, and
   raises an alert when any new gap appears (no dollar floor: the owner's decision 9) or more than 10 new wasted items.

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

8-12. Money (check_ledger, check_posted, given_up, blindness, scoreboard, recovery_rates, expected_value): money
   conservation per service month and payer group from the posting ledger (every posting's money lands in exactly one
   piece); money posted in a closed month never disappears (a newer export of a box that lost a posting is a critical gap,
   from the high-water mark of every earlier check); EMA's A/R against the postings both ways, including lines that left
   the A/R while their postings leave money open; every unattributed dollar counted in a gap total with its amount; the
   dollars given up without a decision; what the app cannot see and what that is worth; G's figures for the Results tab;
   and each item's dollars x the recovery rate of its pattern from the 835 archive (payer group x code x reason, then the
   fallbacks, always named in the basis).

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
GATE_MEMORY_MB = 1750               # release gate 3: a serverless run peaks under 1.75 GB (Vercel's function has 2,278 MB)
SERVERLESS_MEMORY_BUDGET_MB = 2188  # the budget whose 80% mark is that gate: Vercel's normal 1.3-1.5 GB runs are calm
TMP_BUDGET_MB = 512                 # Vercel's /tmp
PRESSURE_SHARE = 0.80               # a run past this share of a budget makes the next one act
CALM_RUNS_TO_STEP_DOWN = 3
LEVELS = ("calm", "spread", "archive", "shed", "alert")
OPTIONAL_STEPS = ("activity", "report", "backfill", "audit")
MAX_DEFER_DAYS = 7                  # an optional step is never left for a calm run longer than this
HISTORY_RUNS = 30                   # invariant results kept in meta for the panel (all runs in selfcheck_runs, up to 400)
EXAMPLES = 20
AUDIT_EVERY_DAYS = 7
AUDIT_WASTED_ALERT = 10             # more than 10 new possibly wasted items raise the audit's alert (a count, not money)
# The owner's decision 9 ("no dollar amount is too small"): ANY new coverage gap raises the audit alert, and ANY rise in
# the week's money given up without a decision raises GIVEN_UP_RISING. There is no dollar floor to set.
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
    """Today as YYYY-MM-DD: the run's own day when it passes one, else REMITWATCH_TODAY (simulated days), else the
    practice's day by the clock (runtime.practice_today), the day every list counts from; never UTC's, a day ahead of a
    US practice each evening."""
    if today:
        return str(today)[:10]
    v = setting("REMITWATCH_TODAY")
    if v and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(v)[:10]):
        return str(v)[:10]
    from .runtime import practice_today
    return practice_today()


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
    """The most memory this run has held so far, in MB (remitwatch.runtime.peak_mb: a run sets the high-water mark back
    when it starts, so on a warm serverless instance an earlier invocation's peak is not taken as this run's)."""
    try:
        from .runtime import peak_mb as run_peak
        return run_peak()
    except Exception as exc:          # noqa: BLE001  (no measure on this platform: the run records none)
        _caught(exc, "peak_mb")
        return None


def tmp_used_mb(folder: str | None = None, limit: int = 20000) -> int | None:
    """What the temporary folder holds now, in MB: the files under it (at most `limit` entries looked at) and the files
    this process holds open there that are no longer listed (SQLite unlinks its temporary files as it opens them, so a
    sort or a temporary table spilled to /tmp is on no directory listing yet takes the same space)."""
    import tempfile
    root = folder or tempfile.gettempdir()
    total, seen = 0, 0
    try:
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                seen += 1
                if seen > limit:
                    return (total + _open_unlinked_bytes(root)) >> 20
                try:
                    total += os.lstat(os.path.join(dirpath, f)).st_size
                except OSError:
                    pass
    except OSError:
        return None
    return (total + _open_unlinked_bytes(root)) >> 20


def _open_unlinked_bytes(root: str) -> int:
    """Bytes in files this process holds open under `root` that were deleted from it (Linux: /proc/self/fd), each counted
    once; 0 where the platform has no /proc."""
    try:
        base = os.path.realpath(root).rstrip(os.sep) + os.sep
        fds = os.listdir("/proc/self/fd")
    except OSError:
        return 0
    total, seen = 0, set()
    for fd in fds:
        try:
            target = os.readlink(f"/proc/self/fd/{fd}")
        except OSError:
            continue
        if not target.endswith(" (deleted)") or not target.startswith(base):
            continue                           # a listed file is counted by the walk
        try:
            st = os.fstat(int(fd))
        except (OSError, ValueError):
            continue
        if (st.st_dev, st.st_ino) in seen:
            continue
        seen.add((st.st_dev, st.st_ino))
        total += st.st_size
    return total


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
CREATE TABLE IF NOT EXISTS selfcheck_offenders (check_id TEXT NOT NULL, key TEXT NOT NULL, cents INTEGER, run_id TEXT,
    PRIMARY KEY (check_id, key)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS selfcheck_facts (finding_id TEXT PRIMARY KEY, fp TEXT) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS selfcheck_posted (svc_month TEXT NOT NULL, posted_month TEXT NOT NULL, payer_group TEXT NOT NULL,
    measure TEXT NOT NULL, high_cents INTEGER, last_cents INTEGER, at TEXT,
    PRIMARY KEY (svc_month, posted_month, payer_group, measure)) WITHOUT ROWID;
"""
TABLES = ("selfcheck_runs", "selfcheck_baseline", "selfcheck_held", "selfcheck_cycles", "selfcheck_audits",
          "selfcheck_audit_items", "selfcheck_track", "selfcheck_money", "selfcheck_offenders", "selfcheck_facts",
          "selfcheck_posted")
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
K_POSTED = "selfcheck:posted"
K_POSTED_GROUPS = "selfcheck:posted_groups"   # {EMA payer name: the payer group its posted-money marks were made with}
K_ACCEPTED = "selfcheck:acceptances"
K_GIVEN_UP = "selfcheck:given_up"
K_BLIND = "selfcheck:blind"
K_RATES = "selfcheck:rates"
K_HOUSE = "selfcheck:housekeeping"
K_ANSWER = "selfcheck:answer_days"
K_FACTS_VERSION = "selfcheck:facts_version"
K_FACTS_INPUT = "selfcheck:facts_input"
# the fingerprint's own version (_facts_of): a build that reads another part of an item's claim changes every fingerprint,
# so fingerprints another build stored are never compared (raise it whenever _facts_of reads anything else). 4: an item
# about an 835 payment itself (a transaction and no line or claim: REMIT_NOT_POSTED, TAKEBACK) adds EMA's checks of it
FACTS_VERSION = "4"


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
    """The budgets a run is measured against. Serverless: 2,188 MB (REMITWATCH_MEMORY_BUDGET_MB), so that its 80% mark is
    release gate 3's 1.75 GB: Vercel's normal runs (1.3-1.5 GB of the 2,278 MB function) are calm and a run past the gate
    makes the next one act; the run's time budget; 512 MB of /tmp. Server: the configured memory budget (none unless
    REMITWATCH_MEMORY_BUDGET_MB is set) and REMITWATCH_CYCLE_BUDGET_SECONDS (3600)."""
    serverless = mode == "serverless"
    mem = setting("REMITWATCH_MEMORY_BUDGET_MB")
    try:
        mem_mb = int(mem) if mem else (SERVERLESS_MEMORY_BUDGET_MB if serverless else None)
    except ValueError:
        mem_mb = SERVERLESS_MEMORY_BUDGET_MB if serverless else None
    try:
        secs = float(budget_s) if budget_s else float(setting("REMITWATCH_MAX_SECONDS", 240) if serverless
                                                      else setting("REMITWATCH_CYCLE_BUDGET_SECONDS", 3600))
    except (TypeError, ValueError):
        secs = 240.0 if serverless else 3600.0
    # serverless: the store image's save goes through one block SQLite caps at 1,024 MB (store.MEMDB_CEILING_MB); a bigger
    # image is saved with a third copy of the store, past what the function's memory holds
    from .store import MEMDB_CEILING_MB
    return {"memory_mb": mem_mb, "seconds": secs, "tmp_mb": TMP_BUDGET_MB if serverless else None, "share": PRESSURE_SHARE,
            "ceiling_mb": MEMDB_CEILING_MB if serverless else None}


def mark(out: dict, step: str, started: float | None = None, *, tmp: bool | None = None) -> dict:
    """Record a step's figures in a run's status, beside the memory per step cloud.py keeps: peak memory so far
    (out['memory_mb'][step]), /tmp in use (out['tmp_mb'][step], serverless by default) and the step's seconds
    (out['step_seconds'][step], when `started` is given). Returns the figures."""
    fig = {"mem": peak_mb()}
    out.setdefault("memory_mb", {})[step] = fig["mem"]
    if tmp if tmp is not None else (out.get("mode") == "serverless"):
        # RemitWatch's temporary folder (REMITWATCH_TMP, as runlog.tmp_usage reads it; on Vercel /tmp is the function's own)
        fig["tmp"] = tmp_used_mb(setting("REMITWATCH_TMP") or None)
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
    if not isinstance(secs, (int, float)) and st.get("started") and st.get("finished"):
        try:                                    # the server's cycle summary has its start and end, not its seconds
            secs = (datetime.fromisoformat(str(st["finished"])) - datetime.fromisoformat(str(st["started"]))).total_seconds()
        except (TypeError, ValueError):
            secs = None
    budget_s = st.get("budget_seconds") or b.get("seconds")
    image = (st.get("store") or {}).get("image_mb") if isinstance(st.get("store"), dict) else None
    image = image if isinstance(image, (int, float)) and image > 0 else None
    share = {"memory": (peak / b["memory_mb"]) if peak and b.get("memory_mb") else None,
             "time": (float(secs) / float(budget_s)) if isinstance(secs, (int, float)) and budget_s else None,
             "tmp": (tmp / b["tmp_mb"]) if tmp is not None and b.get("tmp_mb") else None,
             "image": (image / b["ceiling_mb"]) if image and b.get("ceiling_mb") else None}
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
    if image and b.get("ceiling_mb"):
        parts.append(f"the store {image:,.0f} MB in memory, of the {b['ceiling_mb']:,} MB a run saves in one piece ({share['image']:.0%})")
    if killed:
        parts.append(err[:160] if err else "the run stopped before it finished")
    return {**{k: (round(v, 3) if v is not None else None) for k, v in share.items()}, "peak_mb": peak, "seconds": secs,
            "tmp_mb": tmp, "image_mb": image, "ceiling_mb": b.get("ceiling_mb"), "pressured": bool(over) or killed,
            "killed": killed, "worst": worst or ("killed" if killed else None), "text": "; ".join(parts)}


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
                       f"backfill and the weekly self-audit (never one of them for more than {MAX_DEFER_DAYS} days).")
    if level >= 4:
        actions.append("Nothing more can be shed: an alert asks a person to look.")
    return {"level": level, "name": LEVELS[level], "spread": level >= 1, "archive_tight": level >= 2,
            "skip": list(OPTIONAL_STEPS) if level >= 3 else [], "overdue": [], "alert": level >= 4, "actions": actions,
            "pressure": pressure or {}}


def _killed_runs(killed: Any) -> list[dict]:
    """The runs the platform stopped before they wrote any status: the given list (or one dict), else what the run log in
    progress found when it started (remitwatch.runlog.start_run records each open marker of a run that is gone)."""
    if killed is None:
        try:
            from . import runlog
            rl = runlog.current()
            killed = ((getattr(rl, "manifest", None) or {}).get("sections") or {}).get("killed_runs") if rl is not None else None
        except Exception as exc:       # noqa: BLE001  (no run log: nothing known to be killed)
            _caught(exc, "killed_runs")
            killed = None
    if isinstance(killed, dict):
        killed = [killed]
    return [k for k in (killed or []) if isinstance(k, dict)]


def plan_next(conn: sqlite3.Connection, prev: dict | None, *, mode: str, budget_s: Any = None, now: Any = None,
              killed: Any = None) -> dict:
    """At the start of a run: read the previous run's status (cloud: status.json; server: meta pipeline:last) and the runs
    the platform killed since (`killed`, else the run log in progress: their open markers), record their figures
    (selfcheck_cycles), step the regulation level up (a run passed 80% of a budget, stopped before finishing, or was
    killed) or, after three calm runs, down, and return this run's plan ({'level', 'name', 'spread', 'archive_tight',
    'skip', 'overdue', 'alert', 'actions', 'pressure'}). A status or a killed run already counted changes nothing. An
    optional step left for a calm run for MAX_DEFER_DAYS is not left again until it has run (record_did's 'ran', and the
    self-audit's own record)."""
    ensure_tables(conn)
    reg = meta_get(conn, K_REGULATE, {}) or {}
    level = int(reg.get("level") or 0)
    calm = int(reg.get("calm") or 0)
    pressure = reg.get("last_pressure") or {}
    from .runtime import practice_date
    t = today_of(practice_date(str(now)) if now else None)        # the practice's date of the run's start, as record_did's

    def step(st: dict, b: dict) -> None:
        nonlocal level, calm, pressure
        was = pressure
        pressure = pressure_of(st, b)
        if pressure.get("image_mb") is None and isinstance(was, dict) and was.get("image_mb"):
            # a run that saved no store (one that stopped, an idle one) measured no image, but the image is as large as
            # the last save left it: its size is kept, so the ceiling's warning stays up until a save measures it smaller.
            # It does not step the level: only a run that measured it does that
            ceiling = pressure.get("ceiling_mb") or was.get("ceiling_mb")
            pressure = dict(pressure, image_mb=was["image_mb"], ceiling_mb=ceiling, image_carried=True,
                            image=round(float(was["image_mb"]) / float(ceiling), 3) if ceiling else was.get("image"))
        before = level
        if pressure["pressured"]:
            level, calm = min(level + 1, len(LEVELS) - 1), 0
        else:
            calm += 1
            if calm >= CALM_RUNS_TO_STEP_DOWN and level > 0:
                level, calm = level - 1, 0
        _record_cycle(conn, st, pressure, b, level, mode)
        if level != before:
            reg["since"] = now_of(now)
            hist = list(reg.get("changes") or [])[-19:]
            hist.append({"at": now_of(now), "from": LEVELS[before], "to": LEVELS[level], "why": pressure.get("text")})
            reg["changes"] = hist
    # a run is known by its run id (two runs of a chain can start within one second: by the start alone, the second was
    # taken for the first and its pressure never counted); a regulation state from before run ids knows the start alone
    key = str((prev or {}).get("run_id") or (prev or {}).get("started") or "")
    counted = (key == reg.get("last_run_key")) if reg.get("last_run_key") else \
        bool(prev) and prev.get("started") == reg.get("last_started")
    if prev and prev.get("started") and not counted and not prev.get("skipped"):
        step(prev, budgets(mode, budget_s=prev.get("budget_seconds") or budget_s))
        reg.update(last_started=prev.get("started"), last_run_key=key,
                   last_run={k: prev.get(k) for k in ("started", "finished", "seconds", "budget_seconds", "trigger", "more",
                                                      "error", "deferred")})
    seen = list(reg.get("killed_seen") or [])
    for k in _killed_runs(killed):
        key = str(k.get("run_id") or k.get("started_at") or "")
        if not key or key in seen:
            continue
        seen.append(key)
        where = f" in its step {k['last_step']}" if k.get("last_step") else ""
        st = {"started": k.get("started_at") or k.get("last_at") or now_of(now), "finished": None, "mode": mode,
              "error": f"Killed by the platform{where} before it finished (no status written)",
              "memory_mb": {"peak": k["peak_mb"]} if isinstance(k.get("peak_mb"), (int, float)) else {}}
        step(st, budgets(mode, budget_s=budget_s))
        _log("killed_run_counted", detail={"run": key, "last_step": k.get("last_step"), "peak_mb": k.get("peak_mb")})
    reg["killed_seen"] = seen[-50:]
    reg.update(level=level, calm=calm, last_pressure=pressure)
    plan = plan_for(level, pressure)
    _defer(conn, reg, plan, t)
    reg["plan"] = {k: plan[k] for k in ("level", "name", "spread", "archive_tight", "skip", "overdue", "alert", "actions")}
    reg["mode"] = mode
    reg["seq"] = int(reg.get("seq") or 0) + 1         # which state is newer: the store's or a status's (adopt_regulation)
    meta_set(conn, K_REGULATE, reg)
    conn.commit()
    return plan


def regulation_state(conn: sqlite3.Connection) -> dict:
    """The self-regulation state the run leaves (meta selfcheck:regulate). A serverless run writes it in its status.json:
    an idle run saves no store, and without it the next run would start from the level the last saving run left."""
    return meta_get(conn, K_REGULATE, {}) or {}


def adopt_regulation(conn: sqlite3.Connection, status: dict | None) -> bool:
    """Before plan_next on a serverless host: the state the last run left in its status (status['regulate']) when it is
    newer than the store's (a higher 'seq': that run saved no store). Calm runs then count toward a step down and runs
    past a budget toward the alert, saved store or not. Returns True when adopted."""
    got = (status or {}).get("regulate")
    if not isinstance(got, dict):
        return False
    have = meta_get(conn, K_REGULATE, {}) or {}
    try:
        newer = int(got.get("seq") or 0) > int(have.get("seq") or 0)
    except (TypeError, ValueError):
        return False
    if newer:
        meta_set(conn, K_REGULATE, got)
        conn.commit()
    return newer


STEP_WORDS = {"activity": "the provider tables", "report": "the weekly report email", "backfill": "the export-time backfill",
              "audit": "the weekly self-audit"}


def _defer(conn: sqlite3.Connection, reg: dict, plan: dict, today: str) -> None:
    """Never leave an optional step for a calm run for more than MAX_DEFER_DAYS: a step left that long stays in the plan
    (it runs in a run that does no detection) until it has run (record_did 'ran'; the self-audit's own record), and only
    then may it be left again. Changes plan['skip'] and plan['overdue'] in place and keeps the dates in `reg`."""
    since = dict(reg.get("shed_since") or {})
    ran = dict(reg.get("last_ran") or {})
    try:
        last_audit = conn.execute("SELECT MAX(today) FROM selfcheck_audits").fetchone()[0]
    except sqlite3.Error:
        last_audit = None
    if last_audit:
        ran["audit"] = max(str(ran.get("audit") or ""), str(last_audit))
    keep, overdue = [], []
    for s in plan.get("skip") or []:
        first = since.get(s)
        if first and ran.get(s) and str(ran[s]) >= str(first):
            first = None                       # it ran since it was first left: a new stretch starts today
        if not first:
            since[s] = today
            keep.append(s)
        elif (_days(first, today) or 0) >= MAX_DEFER_DAYS:
            overdue.append(s)
        else:
            keep.append(s)
    if not plan.get("skip"):
        since = {}
    plan["skip"], plan["overdue"] = keep, overdue
    if overdue:
        plan["actions"] = list(plan["actions"]) + [
            f"{'; '.join(STEP_WORDS.get(s, s) for s in overdue).capitalize()}: left for a calm run for a week already, so it runs "
            "now (in a run that does no detection) and is never left longer."]
    reg["shed_since"] = since


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


def record_did(conn: sqlite3.Connection, did: dict, *, today: Any = None) -> None:
    """What this run actually did with its plan (what it shed, which optional steps ran, what the archive retired): kept
    with the regulation state so the panel can say it, and 'ran' ends an optional step's deferral (plan_next). `did` is a
    small dict such as {'shed': ['activity'], 'ran': ['report'], 'archive': {...}, 'deferred': True}."""
    reg = meta_get(conn, K_REGULATE, {}) or {}
    reg["did"] = dict(did or {}, at=now_of())
    t = today_of(today)
    ran = dict(reg.get("last_ran") or {})
    for s in (did or {}).get("ran") or []:
        if s in OPTIONAL_STEPS:
            ran[s] = t
    reg["last_ran"] = ran
    meta_set(conn, K_REGULATE, reg)


def archive_budget_state(plan: dict) -> dict:
    """What unit E's archive.run takes as `budget_state`: the last run's memory figures, with the share raised to the 80%
    mark when this run's plan says to archive at the tighter end of the horizon (archive.effective_horizon then takes six
    months more, never below its floor or the windows open items need), and the measured share otherwise (so a calm run
    lets the horizon relax)."""
    p = (plan or {}).get("pressure") or {}
    shares = [s for s in (p.get("memory"), p.get("image")) if isinstance(s, (int, float))]
    share = max(shares) if shares else None          # memory against its budget, or the image against its one-block ceiling
    if (plan or {}).get("archive_tight"):
        share = max(float(share or 0), PRESSURE_SHARE)
    return {"peak_mb": p.get("peak_mb"), "budget_mb": SERVERLESS_MEMORY_BUDGET_MB if p.get("peak_mb") else None, "share": share,
            "level": (plan or {}).get("level", 0), "why": p.get("text")}


CEILING_WARN_SHARE = 0.75           # the store image at this share of its one-block ceiling: said loudly, well before it


def ceiling_warning(conn: sqlite3.Connection) -> dict | None:
    """The serverless store image near the one block its save goes through (store.MEMDB_CEILING_MB), from the last run's
    figures (meta selfcheck:regulate): what would stop being checked if it went on, in dollars and items (the open items
    on the lists, and every new remittance). None below CEILING_WARN_SHARE or on a server."""
    p = (meta_get(conn, K_REGULATE, {}) or {}).get("last_pressure") or {}
    share, image, ceiling = p.get("image"), p.get("image_mb"), p.get("ceiling_mb")
    if not (isinstance(share, (int, float)) and share >= CEILING_WARN_SHARE and image and ceiling):
        return None
    n = cents = 0
    if _has_table(conn, "findings") and "work_state" in _cols(conn, "findings"):
        n, cents = conn.execute("""SELECT COUNT(*), COALESCE(SUM(shortfall_cents), 0) FROM findings WHERE COALESCE(stale, 0) = 0
                                   AND work_state IN ('work_now', 'waiting')""").fetchone()
    title = (f"The store is {image:,.0f} MB in memory, {share:.0%} of the {ceiling:,} MB a run can save in one piece. Past it, "
             f"each save needs a third copy of the store, more than this host's memory: the runs would stop, and "
             f"{_n(int(n), 'open item')} ({usd0(cents)}) and every new remittance would no longer be checked. RemitWatch moves "
             "older settled claims to the archive sooner to keep it down; if this stays, tell whoever looks after the hosting "
             "(a shorter horizon, REMITWATCH_RETENTION_MONTHS, or the server as the host that analyses).")
    return {"image_mb": image, "ceiling_mb": ceiling, "share": share, "n": int(n), "cents": int(cents),
            "severity": "high" if share >= PRESSURE_SHARE else "medium", "title": title}


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
                    "Nothing nobody has worked stays Work now past the payer's last window (states.EXPIRE_GRACE_DAYS after it; "
                    "the owner's decision 1: none).",
                    "states.py expires an item the day its last window closes: compare the example's deadlines and state_basis."),
    "vanished": ("critical", "keep", 0, "No open item disappeared without a reason",
                 "An item people were shown as work leaves the list only by a closure, a person's decision, an item this run "
                 "made for its line or claim, a fold into another item, its remittance being superseded, or new facts on its "
                 "own claim; never as a stale row nobody sees.",
                 "The run no longer produced these items: find the rule that stopped (the signal of the examples). They were "
                 "kept on the worklist with a note; accept the change on the Data tab once it is intended."),
    "money_conservation": ("critical", "hold", 0, "Each month's money adds up to its charges",
                           "For every service month and payer group, charges = insurer paid + patient paid + contractual + "
                           "written off + open +/- credits, to the dollar: every posting's money lands in exactly one piece.",
                           "The cell's pieces are in meta selfcheck:money; a gap means a posting was counted twice or in no piece "
                           "(a reason the rules do not know). A posting that left the ledger is caught by 'posted money' and by "
                           "the A/R check, not here."),
    "posted_money": ("critical", "hold", 0, "Money posted in EMA never disappears",
                     "The insurer's and the patient's payments and the adjustments EMA posted in a closed month stay in the "
                     "posting ledger: a newer export of the same dates that lacks some of them is a gap with its dollars.",
                     "Compare the newest Production export of those dates with the one before (a filtered or partial export "
                     "replaced a complete one): send the complete export again, or accept the change on the Data tab if EMA "
                     "itself removed those postings. A payer name that changed group (an alias learned or dropped from the "
                     "weekly exports' payer ids, the owner's REMITWATCH_EMA_PAYER_ALIASES, a release's name rules) takes its "
                     "money with it and none leaves: the check counts each name under the group its marks were made with and "
                     "moves the marks once nothing is missing, so that alone never fails it."),
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
    "left_record": ("warning", None, 0, "No item left the record without a reason",
                    "An item no list shows (expired past its windows, done, closed with its proof) is the scoreboard's record: "
                    "one the run no longer produced, with no reason found, is counted here (it is not put back).",
                    "The run no longer produced these items: find the rule that stopped (the signal of the examples, 'gone' "
                    "in the run's decision log). A rule changed on purpose explains them; a whole signal gone is a bug."),
    "line_balance": ("warning", None, 0, "EMA's A/R agrees with its postings",
                     "Each line carries on EMA's newest A/R the balance its postings leave, to the dollar, both ways (a denied "
                     "line by its Denial Amount); a line the A/R no longer lists (settled in EMA) leaves nothing open in its "
                     "postings.",
                     "A posting missing from the Production export (the postings leave more than the A/R), or one missing from "
                     "the A/R, or an A/R pulled before the postings: compare the example lines in EMA."),
}
CRITICAL = tuple(k for k, v in CHECKS.items() if v[0] == "critical")
# offenders' keys a result carries (the baseline and the items kept): more than the whole worklist
KEYS_MAX = 50000


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
            "keys": [(str(k), int(c or 0)) for k, c, _f in offs][:KEYS_MAX], "info": info or {},
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


NO_MONEY_KINDS = frozenset({"denial", "to_patient", "baseline", "answer"})    # proof rows that show an answer, never a payment
ANSWER_KINDS = frozenset({"answer", "to_patient", "payment"})        # the rows of a payer's answer lines (detect._answer_rows)
TAKEBACK_KINDS = frozenset({"reversal", "takeback"})                 # money taken back, whatever the sign it is written with


def proof_money(rows: Iterable[Any]) -> int:
    """The money a closure's proof rows show was paid: dated rows of payments (a row with no kind is one) net of the
    takebacks among them, 0 unless at least one payment is above $0. A $0 payment, a denial row or a takeback is no
    payment."""
    pos = net = 0
    for r in rows:
        if not isinstance(r, dict) or not r.get("date"):
            continue
        kind = str(r.get("kind") or "payment")
        amt = r.get("amount_cents")
        if kind in NO_MONEY_KINDS or not isinstance(amt, (int, float)):
            continue
        amt = -abs(int(amt)) if kind in TAKEBACK_KINDS else int(amt)
        net += amt
        pos += max(amt, 0)
    return net if pos > 0 and net > 0 else 0


def answer_reconciles(row: Any) -> bool:
    """One proof row of a payer's answer line (detect._answer_rows) adds up to the cent (review privacy-3): its charge is
    what the payer paid plus every adjustment it gave on the line (with the claim-level ones it carries, where the payer
    put its reason on the claim), and where it puts the allowed amount on the patient (kind to_patient) the allowed amount
    is what was paid plus the patient's share. A row without those amounts does not reconcile."""
    if not isinstance(row, dict):
        return False
    try:
        charge, paid = int(row["charge_cents"]), int(row["amount_cents"])
        adj = [(str(a.get("group") or ""), int(a["amount_cents"])) for a in row.get("adjustments") or []]
        extra = [int(a["amount_cents"]) for a in row.get("claim_adjustments") or []]
    except (KeyError, TypeError, ValueError, AttributeError):
        return False
    own = sum(a for _g, a in adj)
    if charge != paid + own and not (extra and charge == paid + own + sum(extra)):
        return False
    if row.get("kind") == "to_patient":
        allowed = row.get("allowed_cents")
        return isinstance(allowed, (int, float)) and int(allowed) == paid + sum(a for g, a in adj if g == "PR")
    return True


def has_proof(ev: dict) -> bool:
    """An automatic closure names the payment that closed it: proof rows that show money paid (proof_money), or a payment
    above $0 with its date and the trace or claim it came on. A closure by the payer's own later answer that settled it
    with nothing owed (detect._close_by_answer: settled_by) has its proof in that answer's lines, each dated, on its claim,
    and adding up to the cent (answer_reconciles); settled_by alone is no proof (review privacy-3)."""
    pl = ev.get("paid_later") if isinstance(ev.get("paid_later"), dict) else {}
    ar = ev.get("archive_rule") if isinstance(ev.get("archive_rule"), dict) else {}
    rows = list(pl.get("proof") or []) + (list(ar.get("proof") or []) if ar.get("action") == "close_with_proof" else [])
    if proof_money(rows) > 0:
        return True
    sb = ev.get("settled_by") if isinstance(ev.get("settled_by"), dict) else {}
    if sb.get("date") and sb.get("claim") and sb.get("how"):
        answers = [r for r in pl.get("proof") or [] if isinstance(r, dict) and r.get("kind") in ANSWER_KINDS]
        if answers and all(r.get("date") and r.get("claim") and answer_reconciles(r) for r in answers):
            return True         # the payer's own later answer settled it: its lines, dated, on its claim, adding up to the cent
    paid = pl.get("paid_cents")
    if not (isinstance(paid, (int, float)) and paid > 0):
        return False
    if pl.get("payment_date") and (pl.get("trace") or pl.get("claim")):
        return True
    # what EMA's report shows paid by insurance on the claim (the weekly charge lines carry no payment date): the amount,
    # the claim and the report that shows it let a person find it in EMA
    return bool(pl.get("claim")) and bool(pl.get("source"))


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
    link_col = "ema_link" if "ema_link" in cols else "NULL"
    offs = []
    for fid, pcn, own_link, ev, cents in conn.execute(f"""SELECT finding_id, patient_control_number, {link_col}, evidence,
                                                         COALESCE(shortfall_cents, 0) FROM findings
                                                         WHERE status = 'resolved' AND auto_note IS NOT NULL AND COALESCE(stale, 0) = 0"""):
        try:
            own_link = int(own_link) if own_link is not None else None
        except (TypeError, ValueError):
            own_link = None
        if is_facility(pcn) or (own_link is not None and own_link in fac):
            continue                    # the surgery center's own item: its own claim may close it
        pl = _ev(ev).get("paid_later") or {}
        if not isinstance(pl, dict):
            continue
        link = pl.get("ema_link")
        try:
            link = int(link) if link is not None else None
        except (TypeError, ValueError):
            link = None
        if is_facility(pl.get("claim")) or (link is not None and link in fac and link != own_link):
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
            "state_basis", "carc", "claim_key", "evidence", "payer_kind", "first_seen", "appeal_block", "modifiers"]
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
             and it.get("signal") not in CLAIM_SIGNALS + NOT_A_DENIAL and not _credit_work(it)]
    # one service (bill, date of service, code) can carry several items (lines of the same code, resubmissions): the money
    # paid on it after the earliest of them must cover all of them, or the guard cannot tell which line it paid
    groups: dict[tuple, list] = defaultdict(list)
    for it in items:
        groups[(bill_of(it.get("patient_control_number")), str(it.get("service_date") or "")[:10],
                tuple(sorted(_codes_of(it))))].append(it)
    reps = []
    for members in groups.values():
        first = min(members, key=lambda x: (str(x.get("payment_date") or ""), x["finding_id"]))
        reps.append(dict(first, finding_id="group:" + first["finding_id"], line_key=None))
    later = later_payments(conn, reps)
    offs = []
    by_signal: Counter = Counter()
    for key, members in groups.items():
        first = min(members, key=lambda x: (str(x.get("payment_date") or ""), x["finding_id"]))
        got = later.get("group:" + first["finding_id"])
        # lines of one code on one claim are separate units (their shortfalls add up); the same service sent again on a
        # new claim is the same money (the largest claim's total counts once)
        per_claim: dict[str, int] = defaultdict(int)
        for m in members:
            per_claim[str(m.get("patient_control_number") or m.get("claim_key") or m["finding_id"])] += int(m.get("shortfall_cents") or 0)
        short = max(per_claim.values())
        if not got or got["total"] <= 0 or short <= 0:
            continue
        if got["total"] >= max(short - 100, int(short * LATER_PAID_SHARE)):
            for m in members:
                offs.append((m["finding_id"], int(m.get("shortfall_cents") or 0), m["finding_id"]))
                by_signal[m.get("signal")] += 1
    return _result("precision_guard", offs, info={"checked": len(items), "services": len(groups),
                                                 "by_signal": dict(by_signal.most_common(10))})


def _credit_work(it: dict) -> bool:
    """An item whose work is a credit to refund (states.py: appeal_block 'credit'), not money to collect."""
    return it.get("appeal_block") == "credit" or str(it.get("state_basis") or "").startswith("EMA shows a credit")



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
    for cid, fn in list(ITEM_CHECKS.items()) + [("counts_lists", lambda c, d: check_counts_lists(c, d, run_id))]:
        if only is not None and cid not in set(only):
            continue
        try:
            out.append(fn(conn, t))
        except Exception as exc:       # noqa: BLE001  (a check that cannot run is a failed check, with its error)
            _caught(exc, cid)
            out.append(_check_error(cid, exc))
    return out


def _check_error(cid: str, exc: BaseException) -> dict:
    r = _result(cid, [("error", 0, None)], info={"error": f"{type(exc).__name__}: {str(exc)[:160]}"},
                text=f"{CHECKS[cid][3]}: the check could not run ({type(exc).__name__})")
    r["status"] = "error"
    return r


def _title_in(title: str) -> str:
    """A check's title inside a sentence: its first word in lower case when it is an ordinary word ("Every ...", "The
    ..."), never a name ("EMA's reports ..."), and nothing else changed ("money posted in EMA", not "ema")."""
    t = str(title or "")
    return t[:1].lower() + t[1:] if t[1:2].islower() else t


# ---------------------------------------------------------------------------------------------- the run: before and after
SNAPSHOT = "sc_before"
# what a person (or a hide rule a person made) records in an item's history: put back on a held worklist
PERSON_KINDS = ("manual", "hidden", "unhidden")
ROW_COLS = ("finding_id", "signal", "category", "status", "work_state", "follow_up_on", "line_key", "claim_key",
            "patient_control_number", "service_date", "procedure_code", "shortfall_cents", "payer_group", "auto_note",
            "transaction_key", "payment_date", "stale", "evidence", "hint", "appeal_deadline", "recovered_cents", "ema_link",
            "first_seen", "original_procedure_code", "paid_cents",     # the code billed and what was paid, as
                                                                        # detect.fold_payer_policy read them (_owner_accepted)
            "run_id")                                                   # the run that last wrote it (RELEASE_ACCEPTANCES)


def begin_detection(conn: sqlite3.Connection, *, run_id: str | None = None, today: Any = None) -> dict:
    """Before detection saves: a copy of the findings as people see them now (temp.sc_before, in this connection's
    temporary database), for the vanished check, the track record and safe mode, and the newest entry of the items'
    history (a status a person saves while the run runs is put back on a held worklist). Cheap: one table copy."""
    ensure_tables(conn)
    conn.execute(f"DROP TABLE IF EXISTS temp.{SNAPSHOT}")
    n = 0
    if _has_table(conn, "findings"):
        conn.execute(f"CREATE TEMP TABLE {SNAPSHOT} AS SELECT * FROM main.findings")
        n = conn.execute(f"SELECT COUNT(*) FROM temp.{SNAPSHOT}").fetchone()[0]
    events_max = None
    if _has_table(conn, "finding_events"):
        events_max = int(conn.execute("SELECT COALESCE(MAX(id), 0) FROM finding_events").fetchone()[0] or 0)
    return {"run_id": run_id, "today": today_of(today), "before": n, "at": now_of(), "t0": time.time(),
            "events_max": events_max}


def _rows_of(conn: sqlite3.Connection, table: str, schema: str = "main") -> dict[str, dict]:
    cols = set(_cols(conn, table, schema))
    if not cols:
        return {}
    sel = ", ".join(c if c in cols else f"NULL AS {c}" for c in ROW_COLS)
    cur = conn.execute(f"SELECT {sel} FROM {schema}.{table}")
    names = [d[0] for d in cur.description]
    return {r[0]: dict(zip(names, r)) for r in cur.fetchall()}


def _before_rows(conn: sqlite3.Connection) -> dict[str, dict]:
    if not _has_table(conn, SNAPSHOT, "temp"):
        return {}
    return _rows_of(conn, SNAPSHOT, "temp")


def _after_rows(conn: sqlite3.Connection) -> dict[str, dict]:
    return _rows_of(conn, "findings")


def _is_listed(r: dict, states_kept: bool) -> bool:
    """On a list people work from: not stale, open, and Work now or Waiting (an open money item where the store keeps no
    states yet). An open item with no state in a store that keeps them is listed: every list reads a missing state as
    Work now or Waiting (api._STATE_GUESS), so people see it."""
    if r.get("stale") or r.get("status") not in OPEN_STATUSES:
        return False
    if not states_kept:
        return r.get("status") == "open" and r.get("category") in MONEY
    return r.get("work_state") in LISTED or r.get("work_state") is None


def check_vanished(conn: sqlite3.Connection, before: dict[str, dict], after: dict[str, dict], *,
                   facts: dict[str, str] | None = None, delivered_again: Any = None) -> dict:
    """Open items people were shown as work (Work now or Waiting) before the run that the run no longer has, or keeps only
    as a stale row (an item a person worked that no rule produces any more: it is on no list), with no route out: a
    person's own decision on it while the run ran; an item THIS run made for the same line, claim and code, or service (an
    old item left on the line, such as one a person let go long ago, carries nothing on); for an item about a whole claim
    (no answer, rejected, never sent), an item this run made for its bill and day or an 835 answer on its bill since it
    was raised; a fold into another item; its remittance superseded; or new facts on its own claim since the run before
    (`facts`: the fingerprints that run stored, selfcheck_facts: the 835 answers on its bill, EMA's lines and newest A/R
    rows for its EMA claim number), as the rules' answer may then rightly change; or its line is a copy of an answer the
    payer delivered again the same day (`delivered_again`: the lines the detector passed by, detect._answer_copies), whose
    one item the other delivery keeps."""
    if not before:
        return _skip("vanished", "no items before this run")
    again = {str(x) for x in (delivered_again or ()) if x}
    states_kept = any(r.get("work_state") for r in before.values())
    new_rows = [r for f, r in after.items() if f not in before and not r.get("stale")]
    lines = {r["line_key"] for r in new_rows if r.get("line_key")}
    own_lines = set(lines)
    for r in new_rows:                  # a routine-rate pattern this run made lists the lines it groups (evidence line_keys)
        if not r.get("line_key") and r.get("evidence") and "line_keys" in str(r["evidence"]):
            lines.update(str(x) for x in (_ev(r["evidence"]).get("line_keys") or []) if x)
    for f, r in after.items():          # ... and an older pattern this run gave more lines (a reopened continuation takes back
        b = before.get(f)               # the lines a later one held): its lines grew this run
        if b is None or r.get("stale") or r.get("line_key") or not r.get("evidence") or "line_keys" not in str(r["evidence"]):
            continue
        grown = {str(x) for x in (_ev(r["evidence"]).get("line_keys") or []) if x} - \
            {str(x) for x in (_ev(b.get("evidence")).get("line_keys") or []) if x}
        lines.update(grown)
        own_lines.update(grown)
    claim_codes = {(r.get("claim_key"), r.get("procedure_code")) for r in new_rows if r.get("claim_key")}
    service = {(bill_of(r.get("patient_control_number")), str(r.get("service_date") or "")[:10], r.get("procedure_code"))
               for r in new_rows if r.get("patient_control_number")}
    bill_days = {(bill_of(r.get("patient_control_number")), str(r.get("service_date") or "")[:10])
                 for r in new_rows if r.get("patient_control_number")}
    folded: set[str] = set()
    for r in after.values():
        ev = r.get("evidence")
        if r.get("stale") or not ev or "folded" not in str(ev):
            continue
        for x in _ev(ev).get("folded") or []:
            if isinstance(x, dict) and x.get("finding_id"):
                folded.add(str(x["finding_id"]))
            elif isinstance(x, str):
                folded.add(x)
    routes: Counter = Counter()
    uroutes: Counter = Counter()        # the same routes for the items no list shows (the left_record check)
    cands: list[dict] = []
    ucands: list[dict] = []
    for fid, r in before.items():
        listed = _is_listed(r, states_kept)
        a = after.get(fid)
        if a is not None and not a.get("stale"):
            continue                            # still on record: where it went is the other checks' business
        if not listed and (a is not None or r.get("stale")):
            continue                            # not on a list, and still on record (a stale row): nothing left it
        rt = routes if listed else uroutes
        if a is not None and a.get("status") not in OPEN_STATUSES:
            rt["decided"] += 1                  # a person let it go, lost it or marked it paid while the run ran
            continue
        lk = r.get("line_key")
        b = bill_of(r.get("patient_control_number"))
        svc = str(r.get("service_date") or "")[:10]
        if lk and lk in lines:
            rt["same_line"] += 1
        elif r.get("claim_key") and (r["claim_key"], r.get("procedure_code")) in claim_codes:
            rt["same_claim_code"] += 1
        elif not lk and b and (b, svc, r.get("procedure_code")) in service:
            rt["same_service"] += 1             # a claim-level item carried on by an item for its service
        elif not lk and b and r.get("signal") in CLAIM_SIGNALS and (b, svc) in bill_days:
            rt["claim_answered"] += 1           # the claim's answer brought items of its own
        elif fid in folded:
            rt["folded"] += 1
        elif not lk and r.get("evidence") and own_lines & {str(x) for x in (_ev(r["evidence"]).get("line_keys") or []) if x}:
            rt["regrouped"] += 1                # a pattern whose lines this run lists one by one again
        elif lk and lk in again:
            rt["delivered_again"] += 1          # one answer delivered twice is one item: the other delivery keeps it (T-I3)
        else:
            (cands if listed else ucands).append(r)
    left = _no_route(conn, cands, routes, facts)
    uleft = _no_route(conn, ucands, uroutes, facts)
    offs = [(r["finding_id"], int(r.get("shortfall_cents") or 0), r["finding_id"]) for r in left]
    by_signal = Counter(r.get("signal") for r in left)
    out = _result("vanished", offs, info={"routes": dict(routes), "by_signal": dict(by_signal.most_common(10)),
                                          "stale": sum(1 for r in left if r["finding_id"] in after)})
    uoffs = [(r["finding_id"], int(r.get("shortfall_cents") or 0), r["finding_id"]) for r in uleft]
    out["left_record"] = _result("left_record", uoffs, info={
        "routes": dict(uroutes), "by_signal": dict(Counter(r.get("signal") for r in uleft).most_common(10)),
        "by_state": dict(Counter(str(r.get("work_state") or r.get("status")) for r in uleft).most_common(10))})
    return out


def _no_route(conn: sqlite3.Connection, cands: list[dict], routes: Counter, facts: dict[str, str] | None) -> list[dict]:
    """The candidates the store's own records give no route out: not their remittance superseded, not an 835 payment EMA
    has since posted in full, not a claim answered or sent again since, not the owner's accepted pattern, not new facts on
    their own claim (`facts`). Counts the routes."""
    superseded: set[str] = set()
    tks = sorted({r.get("transaction_key") for r in cands if r.get("transaction_key")})
    if tks and _has_table(conn, "transactions"):
        for i in range(0, len(tks), 500):
            part = tks[i:i + 500]
            have = {tk: sup for tk, sup in conn.execute(f"SELECT transaction_key, COALESCE(superseded, 0) FROM transactions "
                                                        f"WHERE transaction_key IN ({','.join('?' * len(part))})", part)}
            superseded.update(tk for tk in part if have.get(tk, 1))     # gone from the store, or superseded
    posted = _posted_in_ema(conn, [r for r in cands if r.get("signal") == "REMIT_NOT_POSTED" and r.get("transaction_key")])
    claim_items = [r for r in cands if not r.get("line_key") and r.get("signal") in CLAIM_SIGNALS]
    answered = _answered_since(conn, claim_items)
    sent_again = _sent_again(conn, claim_items)
    accepted = _policy_accepts(conn)
    policy = meta_get(conn, "ema:writeoff_policy", {}) or {}
    left: list[dict] = []
    for r in cands:
        if r.get("transaction_key") and r["transaction_key"] in superseded:
            routes["superseded"] += 1
        elif r["finding_id"] in posted:
            routes["posted_in_ema"] += 1        # EMA now holds the 835's payment: C4 rightly stops listing it (Results counts it)
        elif r["finding_id"] in answered:
            routes["claim_answered"] += 1
        elif r["finding_id"] in sent_again:
            routes["sent_again"] += 1           # its bill went out again: the earlier chase waits for the new one (detect.save)
        elif (accepted or policy) and _owner_accepted(r, accepted, policy):
            routes["owner_policy"] += 1         # the owner accepted its pattern (a person's decision): it left the work
        else:
            left.append(r)
    if facts and left:
        now_fp = _facts_of(conn, left)
        kept_left = []
        for r in left:
            old, new = facts.get(r["finding_id"]), now_fp.get(r["finding_id"])
            if old and new and old != new:
                routes["facts_changed"] += 1
            else:
                kept_left.append(r)
        left = kept_left
    return left


_SUB_NO = re.compile(r"C(\d{1,3})$")


def _sent_again(conn: sqlite3.Connection, items: list[dict]) -> set[str]:
    """Items about a whole claim whose bill went out again: under a later submission number (EMA's rows with a submission
    date, an 837 or an 835 claim of the same bill), or under the same claim id after the item was raised (an 837 of it
    created, or EMA's submission date of it, on or after the day the item was first seen and after the send the item is
    about, its evidence's `sent`). Detection chases only a bill's newest submission and waits for a send's due day: the
    earlier item waits, stale, and the claim shows on the Waiting view until the new send comes due (detect.save hands
    its notes on, and lists it again with them if that send goes unanswered)."""
    out: set[str] = set()
    sources = [(t, col, extra) for t, col, extra in (("ema_rows", "claim_id", " AND COALESCE(claim_submission_date,'') != ''"),
                                                     ("claims_837", "patient_control_number", ""),
                                                     ("claims", "patient_control_number", ""))
               if _has_table(conn, t) and col in _cols(conn, t)
               and (not extra or "claim_submission_date" in _cols(conn, t))]
    resent = [(t, col, day) for t, col, day in (("claims_837", "patient_control_number", "bht_created"),
                                                ("ema_rows", "claim_id", "claim_submission_date"))
              if _has_table(conn, t) and {col, day} <= set(_cols(conn, t))]
    for r in items:
        pcn = str(r.get("patient_control_number") or "")
        b, m = bill_of(pcn), _SUB_NO.search(pcn)
        found = False
        if b and m:
            mine = int(m.group(1))
            for t, col, extra in sources:
                for (other,) in conn.execute(f"SELECT DISTINCT {col} FROM {t} WHERE {col} >= ? AND {col} < ?{extra}", (b + "C", b + "D")):
                    mo = _SUB_NO.search(str(other or ""))
                    if mo and bill_of(other) == b and int(mo.group(1)) > mine:
                        found = True
                        break
                if found:
                    break
        since = str(r.get("first_seen") or "")[:10]
        if not found and pcn and since and resent:
            # the same claim id sent again (a rejected claim fixed and sent, or an unanswered one sent again, with no new
            # submission number): a send dated on or after the day the item was raised and after the send it is about
            own = str(_ev(r.get("evidence")).get("sent") or "")[:10]
            for t, col, day in resent:
                (newest,) = conn.execute(f"SELECT MAX({day}) FROM {t} WHERE {col} = ? AND COALESCE({day}, '') != ''", (pcn,)).fetchone()
                d = str(newest or "")[:10]
                if d and d >= since and (not own or d > own):
                    found = True
                    break
        if found:
            out.add(r["finding_id"])
    return out


def _ema_checks_of(conn: sqlite3.Connection, tks: Iterable[Any]) -> dict[str, list]:
    """EMA's checks of each of these 835 payments (transaction keys), read as ema_checks.unposted_remits reads them: the
    checks matched to the payment (trace_key) and the ones C1 paired with it under another number (pair_trace).
    {transaction key: [checks, cents posted, first posting, newest posting]}; a payment no check holds is left out."""
    out: dict[str, list] = {}
    keys = sorted({str(t) for t in tks if t})
    if not keys or not _has_table(conn, "ema_checks"):
        return out
    cols = set(_cols(conn, "ema_checks"))
    if not {"trace_key", "ins_paid", "first_posted", "last_posted"} <= cols:
        return out
    pair = "pair_trace" in cols
    for i in range(0, len(keys), 400):
        part = keys[i:i + 400]
        q = ",".join("?" * len(part))
        paired = (f"""UNION ALL SELECT pair_trace AS k, ins_paid, first_posted, last_posted FROM ema_checks
                      WHERE pair_trace IN ({q}) AND COALESCE(trace_key, '') <> pair_trace""" if pair else "")
        for k, n, cents, first, last in conn.execute(
                f"""SELECT k, COUNT(*), SUM(COALESCE(ins_paid, 0)), MIN(first_posted), MAX(last_posted) FROM (
                        SELECT trace_key AS k, ins_paid, first_posted, last_posted FROM ema_checks WHERE trace_key IN ({q})
                        {paired}) GROUP BY k""", part + (part if pair else [])):
            out[str(k)] = [int(n or 0), int(cents or 0), str(first or "")[:10] or None, str(last or "")[:10] or None]
    return out


def _posted_in_ema(conn: sqlite3.Connection, items: list[dict]) -> set[str]:
    """REMIT_NOT_POSTED items whose 835 payment EMA's checks now hold in full: the checks of its trace (and the ones C1
    paired with it) hold what its claims paid (the transaction's claim_paid_total) within unposted_remits' tolerance,
    max($1, 0.5%). C4 then rightly stops listing it and Results counts the posting as money found: the work is done."""
    out: set[str] = set()
    if not items or not _has_table(conn, "transactions"):
        return out
    from .ema_checks import DIFF_MIN, DIFF_SHARE
    held = _ema_checks_of(conn, [r.get("transaction_key") for r in items])
    tks = sorted(held)
    paid: dict[str, int] = {}
    for i in range(0, len(tks), 500):
        part = tks[i:i + 500]
        paid.update({str(tk): int(c or 0) for tk, c in conn.execute(
            f"SELECT transaction_key, claim_paid_total_cents FROM transactions WHERE transaction_key IN ({','.join('?' * len(part))})",
            part)})
    for r in items:
        tk = str(r.get("transaction_key") or "")
        p = paid.get(tk)
        if tk in held and p and p > 0 and abs(p - held[tk][1]) <= max(DIFF_MIN, DIFF_SHARE * abs(p)):
            out.add(r["finding_id"])
    return out


def _owner_accepted(r: dict, accepted: set[str], policy: dict | None = None) -> bool:
    """Whether the owner accepted the item's pattern (ema_checks.set_policy) in a way that took it off the run: its
    write-off pattern ('group|code|modifier|reason'), or its payer's policy for a code it almost never pays, read exactly
    as the two steps that leave such items out read it: detect.fold_payer_policy folds a remittance line by the first
    service rarely_paid.item_codes names (the billed code of a changed one included), and ema_checks C5 drops each line of
    an EMA-only denial, so that item goes only when every one of its lines is accepted. A decision is read per period of
    the payer's rule (rarely_paid.pattern_key, the owner's decision 4) with the one made for the pair at every date, the
    newer winning (rarely_paid.decision_for). A whole-claim denial is never left out by a payer-policy decision."""
    if set(_pattern_key(r)) & accepted:
        return True
    if not policy:
        return False
    from . import rarely_paid as rp

    def accepted_at(code: str, svc: str | None) -> bool:
        d = rp.decision_for(policy, rp.pattern_key(r.get("payer_group"), code, svc))
        return bool(d) and d.get("decision") == "accept"
    try:
        codes = rp.item_codes(r)
        if not codes:
            return False
        if r.get("line_key"):
            return accepted_at(*codes[0])
        if r.get("signal") == "EMA_DENIED":
            return all(accepted_at(c, s) for c, s in codes)
    except Exception as exc:        # noqa: BLE001  (an item it cannot read keeps no route: it stays listed, never dropped)
        _caught(exc, "vanished.owner_policy")
    return False


def _answered_since(conn: sqlite3.Connection, items: list[dict]) -> set[str]:
    """Items about a whole claim whose bill has an 835 answer (not superseded) paid on or after the day they were raised."""
    out: set[str] = set()
    if not items or not (_has_table(conn, "claims") and _has_table(conn, "transactions")):
        return out
    for r in items:
        b = bill_of(r.get("patient_control_number"))
        if not b:
            continue
        since = str(r.get("first_seen") or "")[:10]
        for pcn, paid_on in conn.execute("""SELECT c.patient_control_number, c.payment_date FROM claims c
                                            JOIN transactions t ON t.transaction_key = c.transaction_key
                                            WHERE c.patient_control_number >= ? AND c.patient_control_number < ?
                                              AND COALESCE(t.superseded, 0) = 0""", (b, b + "~")):
            if bill_of(pcn) == b and str(paid_on or "")[:10] >= since:
                out.add(r["finding_id"])
                break
    return out


def _facts_of(conn: sqlite3.Connection, items: list[dict]) -> dict[str, str]:
    """A fingerprint of what each item's own claim shows in the data now: the 835 answers on its bill (how many, the
    newest date, the money), and for its EMA claim number EMA's lines (count, insurer paid, balance, newest posting, status
    classes) and the newest A/R rows (claim status, balance), or with no EMA claim number EMA's lines on file for its bill,
    date and code; for an item with no bill, the remittance feed as a whole. An item about an 835 payment itself (a
    transaction and no line or claim: a payment EMA did not post, a takeback) adds EMA's checks of that payment (how many,
    the money posted, the first and newest posting), once any check holds it: EMA posting it is new facts on its subject.
    Two runs that see the same fingerprint saw the same facts, so an item that left between them left because a rule
    changed, not because its claim did."""
    out: dict[str, str] = {}
    if not items:
        return out
    has_835 = _has_table(conn, "claims") and _has_table(conn, "transactions")
    has_lines = _has_table(conn, "ema_lines")
    has_ar = _has_table(conn, "ema_ar")
    by_trace = _ema_checks_of(conn, [it.get("transaction_key") for it in items if _about_payment(it)])
    feed = list(conn.execute("SELECT COUNT(*), MAX(payment_date) FROM transactions WHERE COALESCE(superseded, 0) = 0")
                .fetchone()) if has_835 else None
    by_bill: dict[str, list] = {}
    by_link: dict[int, list] = {}
    by_service: dict[tuple, list | None] = {}
    for it in items:
        b = bill_of(it.get("patient_control_number"))
        if b and has_835 and b not in by_bill:
            mine = [(pd, paid) for pcn, pd, paid in conn.execute(
                """SELECT c.patient_control_number, c.payment_date, COALESCE(c.paid_cents, 0) FROM claims c
                   JOIN transactions t ON t.transaction_key = c.transaction_key
                   WHERE c.patient_control_number >= ? AND c.patient_control_number < ? AND COALESCE(t.superseded, 0) = 0""",
                (b, b + "~")) if bill_of(pcn) == b]
            by_bill[b] = [len(mine), max((str(pd or "") for pd, _p in mine), default=""), sum(int(p or 0) for _pd, p in mine)]
        link = it.get("ema_link")
        try:
            link = int(link) if link is not None else None
        except (TypeError, ValueError):
            link = None
        if link is not None and link not in by_link:
            ema = ar = None
            if has_lines:
                ema = list(conn.execute("""SELECT COUNT(*), SUM(COALESCE(ins_paid, 0)), SUM(COALESCE(balance, 0)), MAX(last_posted)
                                           FROM ema_lines WHERE link = ?""", (link,)).fetchone())
                ema.append(sorted({str(s or "") for (s,) in conn.execute("SELECT status_class FROM ema_lines WHERE link = ?", (link,))}))
            if has_ar:
                ar = [list(x) for x in conn.execute("""SELECT COALESCE(claim_status, ''), SUM(COALESCE(balance_cents, 0)) FROM ema_ar
                                                       WHERE link = ? AND as_of = (SELECT MAX(as_of) FROM ema_ar WHERE link = ?)
                                                       GROUP BY 1 ORDER BY 1""", (link, link))]
            by_link[link] = [ema, ar]
        parts = [by_bill.get(b) if b else feed, by_link.get(link) if link is not None else None]
        svc, code = str(it.get("service_date") or "")[:10], it.get("procedure_code")
        if link is None and b and has_lines and svc and code:
            # an item raised before EMA's line was on file (the item makes the next summary keep the line): the line's
            # coming is new facts on its own claim. Only a line on file is added, so an item with none keeps its fingerprint
            k = (b, svc, str(code))
            if k not in by_service:
                n, paid, bal, posted = conn.execute("""SELECT COUNT(*), SUM(COALESCE(ins_paid, 0)), SUM(COALESCE(balance, 0)),
                                                              MAX(last_posted) FROM ema_lines WHERE bill = ? AND svc = ? AND cpt = ?""",
                                                    k).fetchone()
                by_service[k] = [n, paid, bal, posted] if n else None
            if by_service[k] is not None:
                parts.append(by_service[k])
        if _about_payment(it) and str(it["transaction_key"]) in by_trace:
            parts.append(["ema_checks", *by_trace[str(it["transaction_key"])]])
        raw = json.dumps(parts, default=str)
        out[it["finding_id"]] = hashlib.sha1(raw.encode()).hexdigest()[:16]
    return out


def _about_payment(it: dict) -> bool:
    """An item about an 835 payment itself (its transaction, and no line or claim of it): REMIT_NOT_POSTED, TAKEBACK."""
    return bool(it.get("transaction_key")) and not it.get("line_key") and not it.get("claim_key")


def _input_position(conn: sqlite3.Connection) -> list:
    """How much source data the store has read: the 835 transactions (count, newest row), the 835 files and EMA's report
    files applied (count, newest). New facts on a claim can only come with a file read: when this has not moved, a
    fingerprint that changed is RemitWatch's own summaries changing (a refresh, a rebuild, a release), not new input."""
    pos: list = []
    for sql in ("SELECT COUNT(*), MAX(rowid) FROM transactions", "SELECT COUNT(*), MAX(ingested_at) FROM files",
                "SELECT COUNT(*), MAX(applied_at) FROM ema_report_files WHERE applied_at IS NOT NULL"):
        try:
            pos.append(list(conn.execute(sql).fetchone()))
        except sqlite3.Error as exc:    # a store without that table (an old copy, a test's): nothing read of that kind
            _caught(exc, "input_position")
            pos.append(None)
    return pos


def _stored_facts(conn: sqlite3.Connection) -> dict[str, str]:
    """The fingerprints the run before stored, when this build makes them the same way and a source file was read since
    (else none: never compared, so no departure is excused as new facts)."""
    if meta_get(conn, K_FACTS_VERSION) != FACTS_VERSION:
        return {}
    if meta_get(conn, K_FACTS_INPUT) == _input_position(conn):
        return {}                       # nothing read since the run before: no claim has new facts
    try:
        return {f: fp for f, fp in conn.execute("SELECT finding_id, fp FROM selfcheck_facts")}
    except sqlite3.Error as exc:
        _caught(exc, "facts")
        return {}


def _save_facts(conn: sqlite3.Connection) -> int:
    """The fingerprints of the items on record now, listed or not (the next run's vanished and left_record checks compare
    against them), and the source data read so far."""
    rows = _after_rows(conn)
    fps = _facts_of(conn, [r for r in rows.values() if not r.get("stale")])
    conn.execute("DELETE FROM selfcheck_facts")
    conn.executemany("INSERT OR REPLACE INTO selfcheck_facts (finding_id, fp) VALUES (?, ?)", list(fps.items()))
    meta_set(conn, K_FACTS_VERSION, FACTS_VERSION)
    meta_set(conn, K_FACTS_INPUT, _input_position(conn))
    return len(fps)


def _add_facts(conn: sqlite3.Connection, items: list[dict]) -> int:
    """The fingerprints of the new items a held run still lists (safe mode never hides new work), made from the data they
    were found on, beside the last good run's, whose input position stays. None when the stored ones are of another build
    (the next good run saves them all)."""
    if not items or meta_get(conn, K_FACTS_VERSION) != FACTS_VERSION:
        return 0
    fps = _facts_of(conn, items)
    conn.executemany("INSERT OR REPLACE INTO selfcheck_facts (finding_id, fp) VALUES (?, ?)", list(fps.items()))
    return len(fps)


def _stored_results(conn: sqlite3.Connection) -> list[dict]:
    """The checks other steps ran and kept in meta: the money checks (check_ledger, while the store's EMA summaries are of
    the ledger revision they were measured on), posted money (check_posted: the ledger's own history, current whenever it
    ran) and the dashboard copy's check (check_serving, from the last save)."""
    out = []
    money = meta_get(conn, K_MONEY, {}) or {}
    current = money.get("ledger_seq") is None or str(money.get("ledger_seq")) == str(meta_get(conn, "ema:ledger_seq"))
    for m in (money.get("checks") or []):
        if isinstance(m, dict) and m.get("id") in CHECKS and m.get("id") != "posted_money":
            r = dict(m, keys=[tuple(k) for k in m.get("keys") or []])
            if not current:
                r.update(status="skip", text=f"{CHECKS[r['id']][3]}: measured on an earlier ledger revision; checked again "
                                             "when the ledger is next open")
            out.append(r)
    posted = (meta_get(conn, K_POSTED, {}) or {}).get("check")
    if isinstance(posted, dict) and posted.get("id") == "posted_money":
        out.append(dict(posted, keys=[tuple(k) for k in posted.get("keys") or []]))
    sv = meta_get(conn, "selfcheck:serving")
    if isinstance(sv, dict) and sv.get("status") in ("ok", "fail"):
        out.append(_result("serving_copy", [(f"copy:{i}", 0, None) for i in range(int(sv.get("n") or 0))],
                           status=sv["status"], text=sv.get("text"), examples=[]))
    return out


def _baseline(conn: sqlite3.Connection) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = defaultdict(dict)
    for cid, key, cents in conn.execute("SELECT check_id, key, cents FROM selfcheck_baseline"):
        out[cid][key] = int(cents or 0)
    return out


def _accepted(conn: sqlite3.Connection) -> dict[str, set[str]]:
    """The offenders a person accepted (accept_findings), by check."""
    out: dict[str, set[str]] = defaultdict(set)
    for cid, key in conn.execute("SELECT check_id, key FROM selfcheck_baseline WHERE accepted_by IS NOT NULL"):
        out[cid].add(key)
    return out


def _set_baseline(conn: sqlite3.Connection, results: list[dict], at: str) -> None:
    """This run's offenders of each critical check that holds become the baseline the next run is compared with (a
    person's acceptances stay). The vanished check keeps no baseline of its own: an item stays kept until accepted."""
    for r in results:
        if r["severity"] != "critical" or r["status"] not in ("ok", "fail") or r.get("contain") == "keep":
            continue
        conn.execute("DELETE FROM selfcheck_baseline WHERE check_id = ? AND accepted_by IS NULL", (r["id"],))
        conn.executemany("INSERT OR IGNORE INTO selfcheck_baseline (check_id, key, cents, since, accepted_by) VALUES (?,?,?,?,?)",
                         [(r["id"], k, c, at, None) for k, c in r["keys"]])


MONEY_CHECKS = ("money_conservation", "ledger_store", "posted_money")
# a held run still lists the new items to work it found, up to this many or this share of the items listed before it
# (more is a flood: held with the run until it is released or a person looks)
NEW_WORK_FLOOD = (20, 0.10)
# The checks about the data a run read (the posting ledger, EMA's reports and the store's copy of them), not about what
# the run decided: when one of them holds a run, the run's new items rest on data that check distrusts (the hook's fault
# run: 2% of the ledger's rows gone, and 33 new items listed from what was left), so they are held with the run, counted
# on the banner with their dollars, and listed by the next run that passes. A check of the remittances themselves belongs
# here too when one is added.
INPUT_CHECKS = MONEY_CHECKS


def _new_keys(r: dict, base: dict[str, dict[str, int]]) -> list[tuple[str, int]]:
    """Offenders the baseline does not have; for a money check also a known cell whose gap grew by more than $1."""
    known = base.get(r["id"], {})
    out = []
    for k, c in r["keys"]:
        if k not in known or (r["id"] in MONEY_CHECKS and int(c or 0) > known[k] + 100):
            out.append((k, c))
    return out


def _save_offenders(conn: sqlite3.Connection, results: list[dict], rid: str) -> None:
    """Every offender of each failing critical check of this run (not only the 20 examples): what a person accepts, and
    what the Data tab lists (offenders)."""
    conn.execute("DELETE FROM selfcheck_offenders")
    rows = [(r["id"], str(k), int(c or 0), rid) for r in results if r["severity"] == "critical" and r["status"] == "fail"
            for k, c in r["keys"]]
    conn.executemany("INSERT OR REPLACE INTO selfcheck_offenders (check_id, key, cents, run_id) VALUES (?,?,?,?)", rows)
    meta_set(conn, "selfcheck:offenders_run", rid)


def offenders(conn: sqlite3.Connection, check_id: str, *, limit: int = 5000) -> list[dict]:
    """The latest run's offenders of one critical check, largest first (the Data tab's list behind a failing check, and
    what accept_findings accepts): [{'key', 'cents', 'run_id', 'accepted'}]."""
    ensure_tables(conn)
    acc = _accepted(conn).get(check_id, set())
    return [{"key": k, "cents": c, "run_id": rid, "accepted": k in acc} for k, c, rid in conn.execute(
        "SELECT key, cents, run_id FROM selfcheck_offenders WHERE check_id = ? ORDER BY cents DESC, key LIMIT ?", (check_id, limit))]


def _pack(rows: list) -> list[bytes]:
    raw = json.dumps(rows, default=str).encode()
    blob = zlib.compress(raw, 6)
    return [blob[i:i + (4 << 20)] for i in range(0, len(blob), 4 << 20)] or [b""]


def _unpack(parts: list[bytes]) -> list:
    if not parts:
        return []
    return json.loads(zlib.decompress(b"".join(parts)).decode())


def _new_work(conn: sqlite3.Connection, run_id: str, results: list[dict], before: dict[str, dict],
              after: dict[str, dict], hold_ids: Iterable[str] | None = None) -> tuple[list[str], list[str], str | None]:
    """The run's new items to work that a held worklist must still show: not on the worklist before, open and listed, no
    status of their own from this run (an item that inherits a person's work or closes at once is the run's to hold), named
    by no failing critical check, and on no line (or claim-level service) an item of the held worklist already covers.
    Returns (the ids it lists, the ids held back with the run, why they are held): 'input' when a check that holds the run
    is about the data the run read (INPUT_CHECKS: the new items rest on it), 'flood' when they are more than
    NEW_WORK_FLOOD allows, None when every one is listed."""
    blocked = {str(k) for r in results if r["severity"] == "critical" and r["status"] == "fail" for k, _c in r.get("keys") or []}
    touched = {f for (f,) in conn.execute("SELECT DISTINCT finding_id FROM finding_events WHERE run_id = ?", (run_id,))} \
        if _has_table(conn, "finding_events") else set()
    lines = {r["line_key"] for r in before.values() if r.get("line_key")}
    services = {(bill_of(r.get("patient_control_number")), str(r.get("service_date") or "")[:10], r.get("procedure_code"))
                for r in before.values()}
    states_kept = any(r.get("work_state") for r in after.values())
    out = []
    for fid, r in after.items():
        if fid in before or fid in blocked or fid in touched or r.get("status") != "open" or not _is_listed(r, states_kept):
            continue
        if r.get("line_key"):
            if r["line_key"] in lines:
                continue
        elif (bill_of(r.get("patient_control_number")), str(r.get("service_date") or "")[:10], r.get("procedure_code")) in services:
            continue
        out.append(fid)
    listed_before = sum(1 for r in before.values() if _is_listed(r, states_kept))
    if out and any(c in INPUT_CHECKS for c in (hold_ids or ())):
        return [], out, "input"     # found on data a failing check distrusts: held with the run until one passes
    if len(out) > NEW_WORK_FLOOD[0] and len(out) > NEW_WORK_FLOOD[1] * listed_before:
        return [], out, "flood"     # more new work than one run brings: a held run holds it too (the swing guard's to judge)
    return out, [], None


def _reapply_person_changes(conn: sqlite3.Connection, since_id: int | None) -> int:
    """Put back on the restored worklist every status a person saved while the run ran (remitwatch.actions.apply_status:
    the same columns, from the history entry it wrote), and every item a hide rule a person made let go or brought back:
    those history entries belong to no run, so the item must say what its history says. Returns the changes put back."""
    if since_id is None or not _has_table(conn, "finding_events"):
        return 0
    from .runtime import practice_date
    cols = set(_cols(conn, "findings"))
    n = 0
    marks = ",".join("?" * len(PERSON_KINDS))
    for fid, at, kind, to, amount, note in conn.execute(
            f"""SELECT finding_id, at, kind, to_status, amount_cents, note FROM finding_events WHERE id > ? AND kind IN ({marks})
                ORDER BY id""", (since_id, *PERSON_KINDS)).fetchall():
        sets, args = [], []
        if kind == "manual" and to:
            sets, args = ["status = ?"], [to]
            for col, expr, vals in (("notes", "notes = COALESCE(?, notes)", [note]),
                                    ("status_changed_at", "status_changed_at = ?", [at]),
                                    ("recovered_cents", "recovered_cents = ?", [amount if to == "resolved" else None]),
                                    ("recovered_date", "recovered_date = ?", [practice_date(at) if to == "resolved" else None]),
                                    ("appealed_at", "appealed_at = CASE WHEN ? = 'appealed' AND appealed_at IS NULL THEN ? "
                                                    "ELSE appealed_at END", [to, at]),
                                    ("auto_note", "auto_note = NULL", []), ("prev_status", "prev_status = NULL", [])):
                if col in cols:
                    sets.append(expr)
                    args += vals
            n += conn.execute(f"UPDATE findings SET {', '.join(sets)} WHERE finding_id = ?", (*args, fid)).rowcount
        elif kind in ("hidden", "unhidden"):
            want, was = ("dismissed", "open") if kind == "hidden" else ("open", "dismissed")
            extra = ", status_changed_at = ?" if "status_changed_at" in cols else ""
            n += conn.execute(f"UPDATE findings SET status = ?{extra} WHERE finding_id = ? AND status = ?",
                              (want, *([at] if extra else []), fid, was)).rowcount
    return n


def _hold(conn: sqlite3.Connection, run_id: str, at: str, ctx: dict, results: list[dict], before: dict[str, dict],
          after: dict[str, dict], hold_ids: Iterable[str] | None = None) -> dict:
    """Safe mode: keep the run's items (compressed, selfcheck_held) and events, put the items back as they were before the
    run (temp.sc_before: the last good run's), add the run's new items to work that no failing check names (a hold never
    hides new work), take this run's events out of the item history (they describe changes that were held), put back every
    status a person saved while the run ran, and rebuild this run's progress counts from what people see."""
    cols = _cols(conn, "findings")
    bcols = set(_cols(conn, SNAPSHOT, "temp"))
    use = [c for c in cols if c in bcols]
    held_rows = [list(r) for r in conn.execute(f"SELECT {', '.join(cols)} FROM findings")]
    has_ev = _has_table(conn, "finding_events")
    closures = conn.execute("SELECT COUNT(*) FROM finding_events WHERE run_id = ? AND kind = 'paid_on_later_remit'",
                            (run_id,)).fetchone()[0] if has_ev else 0
    ev_cols = _cols(conn, "finding_events")
    events = [list(r) for r in conn.execute(f"SELECT {', '.join(ev_cols)} FROM finding_events WHERE run_id = ?", (run_id,))] \
        if ev_cols else []
    conn.execute("DELETE FROM selfcheck_held")
    for kind, payload in (("findings", {"cols": cols, "rows": held_rows}), ("events", {"cols": ev_cols, "rows": events})):
        for i, part in enumerate(_pack(payload)):
            conn.execute("INSERT INTO selfcheck_held (run_id, kind, part, data) VALUES (?,?,?,?)", (run_id, kind, i, part))
    new_ids, held_new, why_new = _new_work(conn, run_id, results, before, after, hold_ids)
    for tb in ("sc_new", "sc_new_rows"):
        conn.execute(f"DROP TABLE IF EXISTS temp.{tb}")
    conn.execute("CREATE TEMP TABLE sc_new (finding_id TEXT PRIMARY KEY)")
    conn.executemany("INSERT OR IGNORE INTO temp.sc_new VALUES (?)", [(f,) for f in new_ids])
    conn.execute("CREATE TEMP TABLE sc_new_rows AS SELECT * FROM main.findings WHERE finding_id IN (SELECT finding_id FROM temp.sc_new)")
    conn.execute("DELETE FROM findings")
    conn.execute(f"INSERT INTO findings ({', '.join(use)}) SELECT {', '.join(use)} FROM temp.{SNAPSHOT}")
    conn.execute(f"INSERT OR IGNORE INTO findings ({', '.join(cols)}) SELECT {', '.join(cols)} FROM temp.sc_new_rows")
    for tb in ("sc_new", "sc_new_rows"):
        conn.execute(f"DROP TABLE IF EXISTS temp.{tb}")
    if ev_cols:
        conn.execute("DELETE FROM finding_events WHERE run_id = ?", (run_id,))
    person = _reapply_person_changes(conn, ctx.get("events_max"))
    if has_ev and ctx.get("events_max") is not None and "actor" in ev_cols:
        # the hide rules' entries for the run's held items (no item of the worklist carries them): kept with the held copy
        conn.execute("""DELETE FROM finding_events WHERE id > ? AND kind = 'hidden' AND actor = 'hide rule'
                        AND finding_id NOT IN (SELECT finding_id FROM findings)""", (ctx["events_max"],))
    if _has_table(conn, "progress_snapshots"):
        conn.execute("DELETE FROM progress_snapshots WHERE run_id = ?", (run_id,))
        conn.execute("""INSERT INTO progress_snapshots (run_id, at, category, status, n, cents)
                        SELECT ?, ?, category, status, COUNT(*), COALESCE(SUM(shortfall_cents), 0) FROM findings
                        WHERE COALESCE(stale, 0) = 0 GROUP BY category, status""", (run_id, at))
    cents = lambda ids: sum(int(after.get(f, {}).get("shortfall_cents") or 0) for f in ids)    # noqa: E731
    return {"held_items": len(held_rows), "held_events": len(events), "held_closures": closures,
            "kept_items": conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0], "new_items": len(new_ids),
            "new_items_cents": cents(new_ids), "new_items_held": len(held_new), "new_items_held_cents": cents(held_new),
            "new_items_why": why_new, "person_changes": person}


# The owner's acceptance of a release's changes, written in the code because the owner asked for it instead of pressing
# Accept on the Data tab (2026-11-28, for the 2026-11-27 release): the vanished check's items that the code before the
# release listed (last written by a run that started before `written_before`, UTC) and the release's code no longer
# produces are accepted on each host's first run after it, with who in the acceptance history, as the Data tab's Accept
# would. An item a run after the release listed, or one whose run nothing dates, is kept as usual when a later run drops it.
RELEASE_ACCEPTANCES = (
    {"id": "release-2026-11-27", "written_before": "2026-11-27T21:05:00",
     "by": "owner, in the code: the 2026-11-27 release's changes, accepted 2026-11-28"},
)
_RUN_ID_TIME = re.compile(r"run-(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z")


def _runs_started(conn: sqlite3.Connection, run_ids: Iterable[str]) -> dict[str, str]:
    """When each run started, UTC 'YYYY-MM-DDTHH:MM:SS': detection_runs, else the time in the run id (run-YYYYMMDDTHHMMSSZ);
    a run neither dates is left out."""
    ids = sorted({str(r) for r in run_ids if r})
    out: dict[str, str] = {}
    if ids and _has_table(conn, "detection_runs"):
        for i in range(0, len(ids), 500):
            part = ids[i:i + 500]
            for rid, st in conn.execute(f"SELECT run_id, started_at FROM detection_runs WHERE run_id IN ({','.join('?' * len(part))})",
                                        part):
                if st and re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", str(st)):
                    out[str(rid)] = str(st)[:19]
    for rid in ids:
        m = None if rid in out else _RUN_ID_TIME.match(rid)
        if m:
            out[rid] = f"{m[1]}-{m[2]}-{m[3]}T{m[4]}:{m[5]}:{m[6]}"
    return out


def _release_accept(conn: sqlite3.Connection, van: dict, before: dict[str, dict], at: str, rid: str) -> int:
    """Accept the vanished items a release acceptance covers (RELEASE_ACCEPTANCES): the accepted baseline with who, and a
    line in the acceptance history, as accept_findings writes them. Returns how many."""
    keys = [(k, c) for k, c in van.get("keys", []) if k in before]
    if not keys:
        return 0
    started = _runs_started(conn, (before[k].get("run_id") for k, _c in keys))
    done = _accepted(conn).get("vanished", set())
    total = 0
    for rel in RELEASE_ACCEPTANCES:
        take = [(k, c) for k, c in keys if k not in done
                and started.get(str(before[k].get("run_id") or ""), "9999") < rel["written_before"]]
        if not take:
            continue
        conn.executemany("INSERT OR REPLACE INTO selfcheck_baseline (check_id, key, cents, since, accepted_by) VALUES (?,?,?,?,?)",
                         [("vanished", k, c, at, rel["by"]) for k, c in take])
        hist = list(meta_get(conn, K_ACCEPTED, []) or [])[-49:]
        hist.append({"at": at, "by": rel["by"], "run_id": rid, "checks": {"vanished": len(take)}, "release": rel["id"]})
        meta_set(conn, K_ACCEPTED, hist)
        done |= {k for k, _c in take}
        total += len(take)
        _log("release_accepted", counts={"accepted": len(take), "cents": sum(int(c or 0) for _k, c in take)},
             detail={"run_id": rid})
    return total


KEPT_HINT = ("Kept by RemitWatch's self-check: the newest analysis no longer lists this item and nothing explains why "
             "(no payment, no replacement, no fold). It stays until a person checks it.")


def _keep_vanished(conn: sqlite3.Connection, fids: list[str], run_id: str, at: str, before: dict[str, dict],
                   after: dict[str, dict]) -> int:
    """Put back the items that disappeared with no reason, as they were before the run, and list again the ones detection
    kept only as stale rows (with the person's status and notes as they are now, and the state people last saw when the
    new one is not a list's), each with a note and a line in its history."""
    if not fids:
        return 0
    cols = _cols(conn, "findings")
    bcols = set(_cols(conn, SNAPSHOT, "temp"))
    use = [c for c in cols if c in bcols]
    gone = [f for f in fids if f not in after]
    for i in range(0, len(gone), 500):
        part = gone[i:i + 500]
        conn.execute(f"INSERT OR IGNORE INTO findings ({', '.join(use)}) SELECT {', '.join(use)} FROM temp.{SNAPSHOT} "
                     f"WHERE finding_id IN ({','.join('?' * len(part))})", part)
    state_cols = [c for c in ("work_state", "follow_up_on", "state_basis", "appeal_block") if c in cols and c in bcols]
    for f in fids:
        if f not in after:
            continue
        sets, args = ["stale = 0"], []
        if after[f].get("work_state") not in LISTED:
            for c in state_cols:
                sets.append(f"{c} = (SELECT {c} FROM temp.{SNAPSHOT} WHERE finding_id = ?)")
                args.append(f)
        conn.execute(f"UPDATE findings SET {', '.join(sets)} WHERE finding_id = ?", (*args, f))
    if "work_state" in cols:
        # an item kept from a store that kept no states (the first wired run) has none: it gets the one every list already
        # shows for it (api._STATE_GUESS: Sent waits on the payer, anything else is Work now), so it is on Work now and the
        # next run's vanished check sees it as listed (state_valid holds too)
        for i in range(0, len(fids), 500):
            part = fids[i:i + 500]
            conn.execute(f"""UPDATE findings SET work_state = CASE WHEN status = 'appealed' THEN 'waiting' ELSE 'work_now' END
                             WHERE finding_id IN ({','.join('?' * len(part))})
                               AND (work_state IS NULL OR work_state NOT IN ('work_now', 'waiting'))""", part)
    if "hint" in cols:
        for i in range(0, len(fids), 500):
            part = fids[i:i + 500]
            conn.execute(f"UPDATE findings SET hint = ? WHERE finding_id IN ({','.join('?' * len(part))})", (KEPT_HINT, *part))
    if _has_table(conn, "finding_events"):
        conn.executemany("""INSERT INTO finding_events (finding_id, at, kind, from_status, to_status, amount_cents, actor, note, run_id)
                            VALUES (?, ?, 'kept_by_selfcheck', NULL, NULL, NULL, 'system', ?, ?)""",
                         [(f, at, "The analysis no longer listed it with no reason; kept so nothing is dropped silently", run_id)
                          for f in fids])
    return len(fids)


def after_detection(conn: sqlite3.Connection, ctx: dict | None = None, *, mode: str = "server", today: Any = None,
                    run_id: str | None = None) -> dict:
    """After detection saved (inside Detector.run, after the hide rules, before the decision log): run the checks, keep
    what disappeared with no reason (every run, the first included), decide safe mode, update the track record, the
    history, the offenders a person may accept and the facts the next run's vanished check compares against (a held run
    keeps the last good run's, adding only those of the new items it still lists). Returns
    {'held', 'first', 'failing', 'errors', 'new', 'kept', 'checks': [...summaries], 'safe_mode': {...}}."""
    ensure_tables(conn)
    ctx = ctx or {}
    t = today_of(today or ctx.get("today"))
    rid = run_id or ctx.get("run_id") or ("run-" + now_of()[:19])
    at = now_of()
    before = _before_rows(conn)
    after = _after_rows(conn)
    results = run_checks(conn, today=t, run_id=rid)
    try:
        van = check_vanished(conn, before, after, facts=_stored_facts(conn), delivered_again=ctx.get("delivered_again"))
        left_record = van.pop("left_record", None)
        results.append(van)
        if left_record:
            results.append(left_record)     # the items no list shows that left the record: counted, never put back
    except Exception as exc:       # noqa: BLE001
        _caught(exc, "vanished")
        results.append(_check_error("vanished", exc))
    results.extend(_stored_results(conn))
    first = conn.execute("SELECT COUNT(*) FROM selfcheck_runs").fetchone()[0] == 0
    van = next((r for r in results if r["id"] == "vanished"), None)
    if van and van["status"] == "fail":
        try:
            _release_accept(conn, van, before, at, rid)      # before the baseline is read: accepted items are not new
        except Exception as exc:       # noqa: BLE001  (the items stay kept, as without it: nothing is dropped)
            _caught(exc, "release_accept")
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
    accepted = _accepted(conn).get("vanished", set())
    van_new = [k for k, _c in (van or {}).get("keys", []) if k not in accepted] if van and van["status"] == "fail" else []
    held_info: dict = {}
    can_hold = not first and len(before) > 0 and _has_table(conn, SNAPSHOT, "temp")
    held = bool(hold_ids) and can_hold
    track: dict = {}
    if held:
        held_info = _hold(conn, rid, at, ctx, results, before, after, hold_ids)
        _log("safe_mode", counts={"held_items": held_info.get("held_items"), "held_closures": held_info.get("held_closures"),
                                  "new_items": held_info.get("new_items"), "person_changes": held_info.get("person_changes")},
             detail={"checks": hold_ids, "run_id": rid})
    elif van_new:
        # kept on every run, the first included (keeping needs no baseline: only the hold does), until a person accepts
        kept = _keep_vanished(conn, van_new, rid, at, before, after)
        _log("kept_vanished", counts={"kept": kept}, detail={"examples": van_new[:EXAMPLES]})
    if not held:
        # this run is the last good one: its offenders are the baseline (a person's acceptances stay)
        _set_baseline(conn, results, at)
        if before:
            try:
                track = _update_track(conn, before, _after_rows(conn), t, rid)
            except Exception as exc:       # noqa: BLE001  (the track record waits for the next run; the checks stand)
                _caught(exc, "track")
        conn.execute("DELETE FROM selfcheck_held")
        # the facts go with the worklist: the next run's vanished check compares against the facts of this run's items
        try:
            _save_facts(conn)
        except Exception as exc:       # noqa: BLE001  (without them the next run judges every departure as unexplained)
            _caught(exc, "facts")
    else:
        # A held run put back the last good run's worklist, decided on that run's facts: those facts and the input position
        # they were taken at stay as they are, or the next good run would read an answer that came before this run as old
        # and keep every item it settled. Only the new items this run still lists (found on its own data) add theirs.
        try:
            _add_facts(conn, [r for f, r in _after_rows(conn).items() if f not in before and not r.get("stale")])
        except Exception as exc:       # noqa: BLE001  (their departure is then judged as unexplained: kept, never dropped)
            _caught(exc, "facts")
    _save_offenders(conn, results, rid)
    safe = _safe_state(conn, held, hold_ids, results, rid, at, held_info, first, can_hold)
    summary = _store_results(conn, results, rid, t, mode, held, first, kept, at)
    conn.execute(f"DROP TABLE IF EXISTS temp.{SNAPSHOT}")
    conn.commit()
    _log("checks", counts={"failing": sum(1 for r in results if r["status"] == "fail"), "kept": kept, "held": int(held)},
         detail={r["id"]: r["status"] for r in results})
    return {"run_id": rid, "held": held, "first": first, "hold_checks": hold_ids, "kept": kept, "track": track,
            "failing": [r["id"] for r in results if r["status"] == "fail"], "errors": [r["id"] for r in results if r["status"] == "error"],
            "new": {k: len(v) for k, v in new_by.items()}, "checks": summary, "safe_mode": safe,
            "seconds": round(time.time() - ctx.get("t0", time.time()), 1)}


def _new_items_sentence(held_info: dict, why: list[dict], *, long: bool) -> str:
    """What safe mode did with the held run's new items to work, with their dollars, and why (the reason is said either
    way): held with the run because a failing check distrusts the data they rest on, held as a flood, or listed because
    the failing checks are about what the run decided (its closures and states), not the data it read. '' when the run
    found none. long: the Data tab's sentence; else the banner's."""
    n, cents = int(held_info.get("new_items") or 0), int(held_info.get("new_items_cents") or 0)
    h, h_cents = int(held_info.get("new_items_held") or 0), int(held_info.get("new_items_held_cents") or 0)
    reason = held_info.get("new_items_why")
    out = ""
    if h and reason == "input":
        data = "; ".join(_title_in(r["title"]) for r in why if r["id"] in INPUT_CHECKS) or "the data the run read"
        out += (f"It held the {_n(h, 'new item')} to work ({usd(h_cents)}) the run found as well: they rest on data a failing "
                f"check distrusts ({data}), so the next run that passes lists them. " if long else
                f"It holds back the {_n(h, 'new item')} ({usd(h_cents)}) that run found: they rest on data a failing check "
                "distrusts. ")
    elif h:
        out += (f"It held the {_n(h, 'new item')} ({usd(h_cents)}) the run found as well: more than one run usually brings. "
                if long else f"It holds back the {_n(h, 'new item')} ({usd(h_cents)}) that run found: more than one run "
                "usually brings. ")
    if n:
        out += (f"It still lists the {_n(n, 'new item')} to work ({usd(cents)}) the run found: the failing "
                f"{'check is' if len(why) == 1 else 'checks are'} about what the run decided, not the data it read. " if long else
                f"It lists the {_n(n, 'new item')} ({usd(cents)}) that run found: the failing "
                f"{'check is' if len(why) == 1 else 'checks are'} about what it decided, not the data it read. ")
    return out


def _safe_state(conn, held, hold_ids, results, rid, at, held_info, first, can_hold) -> dict:
    prev = meta_get(conn, K_SAFE, {}) or {}
    if held:
        why = [r for r in results if r["id"] in hold_ids]
        n_new, n_person = int(held_info.get("new_items") or 0), int(held_info.get("person_changes") or 0)
        state = {"on": True, "since": prev.get("since") if prev.get("on") else at, "run_id": rid, "at": at,
                 "checks": [{"id": r["id"], "title": r["title"], "n": r["n"], "new": r.get("new", 0), "cents": r["cents"],
                             "examples": r["examples"][:EXAMPLES], "what_to_check": r["what_to_check"]} for r in why],
                 **held_info,
                 "text": ("RemitWatch kept the worklist as it was before this run and held the run's automatic closures, "
                          f"because {'this check' if len(why) == 1 else 'these checks'} failed: "
                          f"{'; '.join(_title_in(r['title']) for r in why)}. "
                          + _new_items_sentence(held_info, why, long=True)
                          + ("The 1 change a person saved during the run stands. " if n_person == 1 else
                             f"The {n_person:,} changes people saved during the run stand. " if n_person else "")
                          + "Nothing was lost: the new results are kept for diagnosis. The next run that passes releases it."),
                 "new_line": _new_items_sentence(held_info, why, long=False).strip(),
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
    critical = sum(1 for r in results if r["status"] in ("fail", "error") and r["severity"] == "critical")
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


CRASHED_TITLE = "The checks after the analysis"


def crashed(conn: sqlite3.Connection, exc: BaseException, *, run_id: str | None = None, today: Any = None) -> dict:
    """after_detection itself failed (a code bug in the checks). The items detection saved stand and the next run tries
    again; what after_detection left half done is rolled back. It is said as a critical check that could not run (the
    CHECK_FAILED alert, every run until the checks run again), never silent. Only the exception's type is kept."""
    try:
        conn.rollback()
        conn.execute(f"DROP TABLE IF EXISTS temp.{SNAPSHOT}")
    except sqlite3.Error as e2:
        _caught(e2, "crashed")
    rid, at, t = run_id or ("run-" + now_of()[:19]), now_of(), today_of(today)
    what = ("Download the diagnostic bundle from the Data tab and give it to the maintainer: until the checks run again, a run "
            "that drops or closes items wrongly is not held.")
    check = {"id": "selfcheck", "severity": "critical", "status": "error", "n": None, "cents": None, "new": None,
             "title": CRASHED_TITLE, "sentence": "The checks themselves could not run after this analysis.",
             "text": f"{CRASHED_TITLE}: they could not run ({type(exc).__name__})", "what_to_check": what, "examples": [],
             "info": {"error": type(exc).__name__}}
    meta_set(conn, K_LAST, {"run_id": rid, "at": at, "today": t, "mode": None, "held": False, "first": False, "kept": 0,
                            "version": VERSION, "checks": [check], "crashed": True})
    hist = list(meta_get(conn, K_HISTORY, []) or [])
    hist.append({"run_id": rid, "at": at, "held": False, "checks": {"selfcheck": "error"}})
    meta_set(conn, K_HISTORY, hist[-HISTORY_RUNS:])
    conn.commit()
    return {"run_id": rid, "held": False, "first": False, "crashed": True, "kept": 0, "failing": [], "errors": ["selfcheck"],
            "checks": [check], "safe_mode": meta_get(conn, K_SAFE, {}) or {}}


def accept_findings(conn: sqlite3.Connection, user: str, check_ids: Iterable[str] | None = None, *,
                    run_id: str | None = None) -> dict:
    """A person accepts what the latest checks found (a business decision on the Data tab: "this change is intended", or
    "use the new results"): exactly the offenders of the run they looked at (selfcheck_offenders: every one, not only the
    20 examples) become the accepted baseline, recorded with who and when, so they no longer hold a run or keep items; an
    offender a later run finds is new again. With `run_id` (the run the page showed), an acceptance that arrives after the
    checks ran again accepts nothing and says so. Accepting posted money moves its marks to what the ledger holds now.
    Returns what was accepted."""
    ensure_tables(conn)
    ids = set(check_ids) if check_ids is not None else set(CRITICAL)
    at = now_of()
    who = str(user or "")[:80]
    latest = meta_get(conn, "selfcheck:offenders_run")
    if run_id and latest and str(run_id) != str(latest):
        return {"accepted": {}, "by": who, "at": at, "run_id": latest, "stale": True,
                "text": "The checks ran again after this page was opened: look at the new results, then accept them."}
    accepted = {}
    for cid in sorted(ids):
        rows = conn.execute("SELECT key, cents FROM selfcheck_offenders WHERE check_id = ?", (cid,)).fetchall()
        if not rows:
            continue
        conn.executemany("INSERT OR REPLACE INTO selfcheck_baseline (check_id, key, cents, since, accepted_by) VALUES (?,?,?,?,?)",
                         [(cid, k, c, at, who) for k, c in rows])
        accepted[cid] = len(rows)
        if cid == "posted_money":
            conn.executemany("""UPDATE selfcheck_posted SET high_cents = last_cents
                                WHERE svc_month || '|' || posted_month || '|' || payer_group || '|' || measure = ?""",
                             [(k,) for k, _c in rows])
    if accepted:
        hist = list(meta_get(conn, K_ACCEPTED, []) or [])[-49:]
        hist.append({"at": at, "by": who, "run_id": latest, "checks": accepted})
        meta_set(conn, K_ACCEPTED, hist)
    conn.commit()
    if accepted:
        text = ("Accepted: " + "; ".join(f"{_n(n, 'item')} of '{CHECKS[c][3] if c in CHECKS else c}'" for c, n in sorted(accepted.items()))
                + f", what run {latest} found. They no longer hold a run or stay on the worklist because of these checks.")
    else:
        # a warning check keeps no list to accept (it holds no run and keeps no item), and a check that passed has none
        text = ("Nothing was accepted: the checks named have no items to accept (a warning keeps no list: it holds no run "
                "and keeps no item on the worklist).")
    return {"accepted": accepted, "by": who, "at": at, "run_id": latest, "text": text}


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
    (money_conservation), EMA's A/R against its postings both ways, in the same pass over the ledger (line_balance), and
    money posted in a closed month never disappearing (check_posted: posted_money). Every dollar the checks cannot tie
    is counted in out['gaps'] (a total with its parts). Saved in meta selfcheck:money with the ledger's seq (posted money
    in selfcheck:posted); after_detection counts these results while the store's summaries are of that seq. Returns the
    dict, its 'checks' with posted_money last."""
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
    cons = _conservation(conn, lc, t, ar=_ar_lines(conn))
    checks.append(cons.pop("check"))
    bal = cons.pop("ar_balance")
    checks.append(bal.pop("check"))
    out["conservation"] = cons
    out["ar_balance"] = bal
    for c in checks:
        c["current"] = True
    posted = check_posted(conn, ledger, today=t)
    out["posted"] = {k: posted.get(k) for k in ("status", "n", "cents", "text", "info")}
    out["gaps"] = _gap_total(checks[1], cons, bal, posted)
    out["checks"] = checks
    meta_set(conn, K_MONEY, out)
    conn.commit()
    return dict(out, checks=checks + [posted])


def _gap_total(conservation: dict, cons: dict, bal: dict, posted: dict) -> dict:
    """Every dollar the money checks could not tie, by kind, and their total (NON-NEGOTIABLE 1: an unreconciled dollar is
    a visible gap with an amount)."""
    g = {"posted_drop_cents": int(posted.get("cents") or 0) if posted.get("status") == "fail" else 0,
         "cells_off_cents": int(conservation.get("cents") or 0) if conservation.get("status") == "fail" else 0,
         "unlinked_since_cents": int(cons.get("unlinked_since_cents") or 0),
         "ar_above_cents": int(bal.get("ar_above_cents") or 0), "postings_above_cents": int(bal.get("postings_above_cents") or 0),
         "left_ar_cents": int(bal.get("left_ar_cents") or 0), "not_in_postings_cents": int(bal.get("not_in_postings_cents") or 0)}
    g["total_cents"] = sum(abs(v) for v in g.values())
    return g


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
    names = {i: n for i, n in lc.execute("SELECT id, name FROM names")}
    for pm in [m for (m,) in lc.execute("SELECT DISTINCT substr(posted, 1, 7) FROM postings") if m]:
        for month, payer, product, ins, pat, chg in lc.execute(
                """SELECT substr(posted, 1, 7), payer, ((flags & 1) <> 0), SUM(COALESCE(ins_paid, 0)), SUM(COALESCE(pat_paid, 0)),
                          SUM(COALESCE(charges, 0))
                   FROM postings WHERE posted >= ? AND posted < ? GROUP BY 1, 2, 3""", (pm + "-01", pm + "-32")):
            payer = names.get(payer) or ""
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
        per_src = {src: n for src, n in lc.execute("SELECT src, COUNT(*) FROM postings GROUP BY src")}
        for fid, st, live, src in lc.execute("SELECT file_id, status, live_rows, src FROM ledger_files"):
            got = per_src.get(src, 0)
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


def _conservation(conn: sqlite3.Connection, lc: sqlite3.Connection, today: str, *, ar: dict | None = None) -> dict:
    """Money conservation per service month and payer group, from every posting of the ledger. Each line (EMA claim number,
    date of service, code) belongs to the cell of its service month and the group of its newest primary payer; each
    posting's money goes to one piece by its own column and reason:
      charges = insurer paid + patient paid + contractual (routine) + written off (by reason: EMA's rule at posting, a person
      later, timely filing) + patient adjustments + open (lines with a balance) - credits (lines paid past their charge)
    The pieces are summed by separate rules (a reason class the rules do not know lands in no piece) and the open and
    credit pieces come from each line's own balance, so a posting counted twice or in no piece shows as a cell whose pieces
    do not add up (critical past $1); a posting that left the ledger is posted_money's and line_balance's to see. Postings
    that belong to no line (patient money with no claim number that matches no single line, insurance money with no claim
    number) are a visible gap of their own, by cell. In the same pass, with `ar` (_ar_lines: EMA's newest A/R by line),
    each line inside the A/R's service dates is compared with what its postings up to the A/R's day leave (line_balance,
    both ways, below). One service month at a time (the ledger's service-date index), so neither memory nor the temporary
    folder holds more than a month's lines. Stored in selfcheck_money."""
    if not _has_table(lc, "postings"):
        return {"check": _skip("money_conservation", "no posting ledger"), "cells": 0,
                "ar_balance": {"check": _skip("line_balance", "no posting ledger")}}
    from . import ema_reports as er
    rc = _reason_class_fn()
    names = {i: n for i, n in lc.execute("SELECT id, name FROM names")}
    # each posting's reason class (ema_ledger.reason_class), worked out once per distinct (group, code, kind)
    lc.execute("DROP TABLE IF EXISTS temp.sc_cls")
    lc.execute("CREATE TEMP TABLE sc_cls (grp TEXT, code TEXT, kind INTEGER, cls TEXT)")
    lc.executemany("INSERT INTO temp.sc_cls VALUES (?,?,?,?)",
                   [(g or "", c or "", k if k is not None else -1, rc(g, c, names.get(k)))
                    for g, c, k in lc.execute("SELECT DISTINCT grp, code, kind FROM postings").fetchall()])
    lc.execute("CREATE UNIQUE INDEX temp.ix_sc_cls ON sc_cls(grp, code, kind)")
    group_cache: dict[Any, str] = {}

    def group(name_id: Any) -> str:
        if name_id not in group_cache:
            nm = names.get(name_id)
            try:
                group_cache[name_id] = er.payer_group_of(conn, nm)[0] if nm else "No payer named"
            except Exception as exc:          # noqa: BLE001  (an unreadable payer name is its own group)
                _caught(exc, "payer_group")
                group_cache[name_id] = str(nm or "No payer named")
        return group_cache[name_id]
    routine = ",".join(f"'{c}'" for c in ROUTINE_CLASSES)
    money = "COALESCE(p.contractual, 0) + COALESCE(p.noncontractual, 0)"
    left_expr = ("COALESCE(p.charges, 0) - COALESCE(p.ins_paid, 0) - COALESCE(p.pat_paid, 0) - COALESCE(p.contractual, 0) "
                 "- COALESCE(p.noncontractual, 0) - COALESCE(p.pat_adj, 0)")
    moneyed_expr = ("COALESCE(ins_paid, 0) + COALESCE(pat_paid, 0) + COALESCE(contractual, 0) + COALESCE(noncontractual, 0) "
                    "+ COALESCE(pat_adj, 0)")
    months = [m for (m,) in lc.execute("SELECT DISTINCT substr(svc, 1, 7) FROM postings ORDER BY 1") if m]
    cells: dict[tuple, Counter] = defaultdict(Counter)
    line_rows = 0
    joined_total = 0
    cmp = _ArCompare(ar, _facility_links(conn)) if ar else None
    for m in months:
        lo, hi = m + "-01", m + "-32"
        asof = cmp.as_of(m) if cmp else None
        for tb in ("sc_ans", "sc_prim"):
            lc.execute(f"DROP TABLE IF EXISTS temp.{tb}")
        lc.execute("""CREATE TEMP TABLE sc_ans AS SELECT DISTINCT link, svc, COALESCE(cpt, '') AS cpt, posted FROM postings
                      WHERE svc >= ? AND svc < ? AND link IS NOT NULL AND check_no IS NOT NULL""", (lo, hi))
        lc.execute("CREATE INDEX temp.ix_sc_ans ON sc_ans(link, svc, cpt, posted)")
        lc.execute("""CREATE TEMP TABLE sc_prim AS SELECT link, svc, COALESCE(cpt, '') AS cpt, primary_payer AS prim,
                             MAX(posted || printf('%012d', rowid)) AS k
                      FROM postings WHERE svc >= ? AND svc < ? AND link IS NOT NULL AND primary_payer IS NOT NULL
                      GROUP BY link, svc, COALESCE(cpt, '')""", (lo, hi))
        lc.execute("CREATE UNIQUE INDEX temp.ix_sc_prim ON sc_prim(link, svc, cpt)")
        lines: dict[tuple, list] = {}
        for (link, svc, cpt, person, prim, chg, ins, pat, contr_r, wo_post, wo_later, timely, padj, bal, bal_ar) in lc.execute(
                f"""SELECT p.link, p.svc, COALESCE(p.cpt, ''), MAX(p.person), pr.prim, SUM(COALESCE(p.charges, 0)),
                           SUM(COALESCE(p.ins_paid, 0)), SUM(COALESCE(p.pat_paid, 0)),
                           SUM(CASE WHEN x.cls IN ({routine}) THEN {money} ELSE 0 END),
                           SUM(CASE WHEN x.cls NOT IN ({routine}, 'timely') AND a.posted IS NOT NULL THEN {money} ELSE 0 END),
                           SUM(CASE WHEN x.cls NOT IN ({routine}, 'timely') AND a.posted IS NULL THEN {money} ELSE 0 END),
                           SUM(CASE WHEN x.cls = 'timely' THEN {money} ELSE 0 END),
                           SUM(COALESCE(p.pat_adj, 0)), SUM({left_expr}),
                           SUM(CASE WHEN p.posted <= ? THEN {left_expr} ELSE 0 END)
                    FROM postings p
                    LEFT JOIN temp.sc_cls x ON x.grp = COALESCE(p.grp, '') AND x.code = COALESCE(p.code, '')
                         AND x.kind = COALESCE(p.kind, -1)
                    LEFT JOIN temp.sc_ans a ON a.link = p.link AND a.svc = p.svc AND a.cpt = COALESCE(p.cpt, '') AND a.posted = p.posted
                    LEFT JOIN temp.sc_prim pr ON pr.link = p.link AND pr.svc = p.svc AND pr.cpt = COALESCE(p.cpt, '')
                    WHERE p.svc >= ? AND p.svc < ? AND p.link IS NOT NULL
                    GROUP BY p.link, p.svc, COALESCE(p.cpt, '')""", (asof or "0000", lo, hi)):
            lines[(link, svc, cpt)] = [person, prim, chg or 0, ins or 0, pat or 0, contr_r or 0, wo_post or 0, wo_later or 0,
                                       timely or 0, padj or 0, bal or 0, bal_ar or 0]
        by_pd: dict[tuple, list] = defaultdict(list)
        for k, v in lines.items():
            if v[0] is not None:
                by_pd[(v[0], k[1], k[2])].append(k)
        # money with no claim number, by patient, day and code: joined to the one line with the same keys (the ledger's
        # own rule for the patient's payments); a patient-side service of its own when it carries charges (a self-pay
        # visit, a product); otherwise a visible gap (and, for the A/R comparison, money of that patient and day)
        own_lines: list[list] = []
        unlinked_pd: dict[tuple, int] = defaultdict(int)
        for (person, svc, cpt, payer, prim, chg, ins, pat, contr, nonc, padj, n_rows, moneyed_ar) in lc.execute(
                f"""SELECT person, svc, COALESCE(cpt, ''), MAX(payer), MAX(primary_payer), SUM(COALESCE(charges, 0)),
                           SUM(COALESCE(ins_paid, 0)), SUM(COALESCE(pat_paid, 0)), SUM(COALESCE(contractual, 0)),
                           SUM(COALESCE(noncontractual, 0)), SUM(COALESCE(pat_adj, 0)), COUNT(*),
                           SUM(CASE WHEN posted <= ? THEN {moneyed_expr} ELSE 0 END)
                    FROM postings WHERE svc >= ? AND svc < ? AND link IS NULL GROUP BY person, svc, COALESCE(cpt, '')""",
                (asof or "0000", lo, hi)):
            cands = by_pd.get((person, svc, cpt)) if person is not None else None
            moneyed = (ins or 0) + (pat or 0) + (contr or 0) + (nonc or 0) + (padj or 0)
            if not chg and cands and len(cands) == 1:
                v = lines[cands[0]]
                v[3] += ins or 0
                v[4] += pat or 0
                v[5] += (contr or 0) + (nonc or 0)          # adjustments on the patient's side: routine (they carry no payer reason)
                v[9] += padj or 0
                v[10] -= moneyed
                v[11] -= moneyed_ar or 0
                joined_total += moneyed
            elif chg:
                own_lines.append([None, prim if prim is not None else payer, chg or 0, ins or 0, pat or 0, (contr or 0) + (nonc or 0),
                                  0, 0, 0, padj or 0, (chg or 0) - moneyed, 0])
            else:
                c = cells[(m, group(prim if prim is not None else payer))]
                c["unlinked_money"] += moneyed
                c["unlinked_rows"] += n_rows or 0
                if person is not None:
                    unlinked_pd[(person, svc)] += int(moneyed_ar or 0)
        if cmp is not None and asof:
            cmp.month(lines, unlinked_pd)
        for v in list(lines.values()) + own_lines:
            line_rows += 1
            person, prim, chg, ins, pat, contr_r, wo_post, wo_later, timely, padj, bal, _bal_ar = v
            c = cells[(m, group(prim))]
            c["charges"] += chg
            c["ins_paid"] += ins
            c["pat_paid"] += pat
            c["contractual"] += contr_r
            c["wo_at_posting"] += wo_post
            c["wo_later"] += wo_later
            c["wo_timely"] += timely
            c["pat_adj"] += padj
            c["lines"] += 1
            if bal > 0:
                c["open"] += bal
            elif bal < 0:
                c["credit"] += -bal
    for tb in ("sc_ans", "sc_prim", "sc_cls"):
        lc.execute(f"DROP TABLE IF EXISTS temp.{tb}")
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
        if c["unlinked_money"]:
            gaps.append({"month": m, "payer_group": g, "unlinked_money_cents": c["unlinked_money"], "rows": c["unlinked_rows"]})
        for measure, cents in c.items():
            rows.append((m or "", g or "", measure, int(cents), None))
    conn.executemany("INSERT OR REPLACE INTO selfcheck_money (month, payer_group, measure, cents, rows) VALUES (?,?,?,?,?)", rows)
    since = {k: v for k, v in cells.items() if (k[0] or "") >= SERVICE_FROM[:7]}
    tot = Counter()
    for c in since.values():
        tot.update(c)
    summary = {"cells": len(cells), "lines": line_rows, "since": SERVICE_FROM[:7],
               "totals_since": {k: int(v) for k, v in tot.items()},
               "cells_off": [{"cell": k, "gap_cents": c} for k, c in [(o[0], o[1]) for o in offs]][:50],
               "unlinked": sorted(gaps, key=lambda x: -abs(x["unlinked_money_cents"]))[:50],
               "unlinked_money_cents": int(sum(c["unlinked_money"] for c in cells.values())),
               "unlinked_since_cents": int(tot.get("unlinked_money", 0)), "patient_money_joined_cents": int(joined_total)}
    text = (f"{_n(len(cells), 'month and payer cell')} checked; " +
            ("every one adds up to its charges" if not offs else f"{_n(len(offs), 'cell')} do not add up ({usd0(sum(o[1] for o in offs))})") +
            f"; {usd0(summary['unlinked_since_cents'])} of patient or insurance money since {SERVICE_FROM[:7]} sits on no line")
    summary["check"] = _result("money_conservation", offs, info={"cells": len(cells), "lines": line_rows,
                                                                 "unlinked_since_cents": summary["unlinked_since_cents"]},
                               examples=[], text=text)
    summary["ar_balance"] = cmp.result() if cmp is not None else {"check": _skip("line_balance", "no A/R on file")}
    return summary


def _ar_lines(conn: sqlite3.Connection) -> dict | None:
    """EMA's newest A/R by line (EMA claim number, date of service, code), the money it holds open (a denied line's
    Denial Amount when its Current A/R is $0), the insurance and the patient rows together:
    {'lines': {(link, svc, cpt): cents}, 'spans': [(first service date, last service date, as-of day)], 'as_of', 'left':
    lines an earlier A/R listed that the newest no longer does}, from each current A/R file of the catalog on its own day, or
    the newest day's rows when the catalog lists none. The span a file covers is its catalog's service dates, else the
    service dates of its rows (it picks the as-of day a month's postings are cut at). None when there is no A/R."""
    if not _has_table(conn, "ema_ar") or not conn.execute("SELECT 1 FROM ema_ar LIMIT 1").fetchone():
        return None
    files = []
    if _has_table(conn, "ema_report_files"):
        files = [f for f in conn.execute("""SELECT file_id, svc_lo, svc_hi, as_of FROM ema_report_files
                                            WHERE report = 'ar_report' AND status = 'current'""").fetchall()
                 if conn.execute("SELECT 1 FROM ema_ar WHERE file_id = ? LIMIT 1", (f[0],)).fetchone()]
    lines: dict[tuple, int] = defaultdict(int)
    spans = []
    # what the A/R holds open on the line: its open amount, or for a denied line, which EMA lists with $0 in Current A/R,
    # the money in Denial Amount (ema_reports keeps that as exposure_cents)
    held = ("COALESCE(exposure_cents, balance_cents, 0)" if "exposure_cents" in _cols(conn, "ema_ar")
            else "COALESCE(balance_cents, 0)")

    def take(where: str, args: tuple, lo: Any, hi: Any, as_of: Any) -> None:
        svc_lo, svc_hi, row_as_of = conn.execute(f"SELECT MIN(svc), MAX(svc), MAX(as_of) FROM ema_ar WHERE {where}", args).fetchone()
        for link, svc, cpt, bal in conn.execute(f"""SELECT link, svc, cpt, SUM({held}) FROM ema_ar
                                                    WHERE {where} AND link IS NOT NULL AND COALESCE(facility, 0) = 0
                                                    GROUP BY link, svc, cpt""", args):
            lines[(int(link), str(svc)[:10], cpt or "")] += int(bal or 0)
        a = str(as_of or row_as_of or "")[:10]
        if a and (lo or svc_lo):
            spans.append((str(lo or svc_lo)[:10], str(hi or svc_hi or a)[:10], a))
    if files:
        for fid, lo, hi, as_of in files:
            take("file_id = ?", (fid,), lo, hi, as_of)
    else:
        newest = conn.execute("SELECT MAX(as_of) FROM ema_ar").fetchone()[0]
        take("as_of = ?", (newest,), None, None, newest)
    if not spans:
        return None
    left = set()
    if _has_table(conn, "ema_ar_items") and "absent_since" in _cols(conn, "ema_ar_items"):
        # lines an earlier A/R listed and the newest no longer does (settled in EMA): the A/R's own history
        left = {(int(link), str(svc)[:10], cpt or "") for link, svc, cpt in conn.execute(
            """SELECT link, svc, cpt FROM ema_ar_items WHERE absent_since IS NOT NULL AND link IS NOT NULL""")}
    return {"lines": dict(lines), "spans": spans, "as_of": max(s[2] for s in spans), "left": left}


class _ArCompare:
    """EMA's newest A/R against what each line's postings leave up to the A/R's day: for a line on the A/R, both ways (the
    A/R holds more: a posting missing from Production, or an A/R pulled before the postings; the postings leave more: a
    payment or adjustment missing from Production); for a line that LEFT the A/R (an earlier A/R listed it, the newest no
    longer does: settled in EMA, ema_ar_items.absent_since) while its postings still leave money open, a gap; an A/R line
    with no posting at all, a gap. A line no A/R ever listed is not compared (the A/R lists open balances, and not every
    patient balance). A difference that patient money paid on no claim number, by the same patient on the same day, can
    account for is counted beside, not as a gap."""

    def __init__(self, ar: dict, facility: set[int]):
        self.ar = ar["lines"]
        self.left = set(ar.get("left") or ()) - set(self.ar)
        self.spans = ar["spans"]
        self.as_of_day = ar["as_of"]
        self.fac = facility
        self.seen: set[tuple] = set()
        self.offs: list[tuple] = []
        self.n = Counter()
        self.cents = Counter()

    def as_of(self, month: str) -> str | None:
        hits = [a for lo, hi, a in self.spans if lo[:7] <= month <= hi[:7]]
        return min(hits) if hits else None

    def _off(self, kind: str, key: tuple, cents: int) -> None:
        self.n[kind] += 1
        self.cents[kind] += cents
        self.offs.append((f"{kind}:{key[0]}|{key[1]}|{key[2]}", cents, None))

    def month(self, lines: dict[tuple, list], unlinked_pd: dict[tuple, int]) -> None:
        for (link, svc, cpt), v in lines.items():
            key = (int(link), str(svc)[:10], cpt or "")
            if key[0] in self.fac:
                continue
            a = self.ar.get(key)
            if a is None and key not in self.left:
                continue                        # no A/R ever listed it: nothing to compare with
            led = int(v[11] or 0)
            if a is not None:
                self.seen.add(key)
                if a - led > 100:
                    self._off("ar_above", key, a - led)
                    continue
                excess = led - a
            else:
                excess = led
            if excess <= 100:
                self.n["equal" if a is not None else "settled"] += 1
                continue
            u = unlinked_pd.get((v[0], key[1]), 0) if v[0] is not None else 0
            if u > 0 and excess <= u + 100:
                self.n["explained"] += 1
                self.cents["explained"] += excess
                continue
            self._off("postings_above" if a is not None else "left_ar", key, excess)

    def result(self) -> dict:
        for key, bal in self.ar.items():
            if key not in self.seen and key[0] not in self.fac and abs(bal) > 100:
                self._off("not_in_postings", key, abs(bal))
        info = {"as_of": self.as_of_day, "lines": len(self.ar), "left_lines": len(self.left), "equal": self.n["equal"],
                "settled": self.n["settled"], "explained": self.n["explained"], "explained_cents": self.cents["explained"]}
        for kind in ("ar_above", "postings_above", "left_ar", "not_in_postings"):
            info[kind] = self.n[kind]
            info[kind + "_cents"] = self.cents[kind]
        off_cents = sum(o[1] for o in self.offs)
        text = (f"{_n(len(self.ar), 'line')} on the A/R of {self.as_of_day}: {self.n['equal']:,} agree with the postings to the "
                f"dollar; {_n(len(self.offs), 'line')} do not ({usd0(off_cents)}): the A/R holds more on {self.n['ar_above']:,}, "
                f"the postings leave more on {self.n['postings_above']:,}, {self.n['left_ar']:,} left the A/R while their postings "
                f"leave money open, {self.n['not_in_postings']:,} have no posting")
        return dict(info, check=_result("line_balance", self.offs, info=dict(info), examples=[], text=text),
                    off=len(self.offs), off_cents=off_cents)


def _retain_floor(lc: sqlite3.Connection, newest: str) -> str:
    """The first service day whose postings the ledger's own retention (ema_ledger.Ledger.retain: service more than
    retain_days() before the newest posting) will not drop for a while: the first day of the second month after its
    cutoff, or after the cutoff it last applied, whichever is later."""
    cut = None
    try:
        from .ema_ledger import retain_days
        cut = _add(newest, -int(retain_days()))
    except Exception as exc:       # noqa: BLE001  (the ledger module's default, as a server keeps it)
        _caught(exc, "retain_days")
        cut = _add(newest, -1100)
    try:
        row = lc.execute("SELECT value FROM ledger_meta WHERE key = 'retained_before'").fetchone()
        if row and row[0] and str(row[0])[:10] > str(cut or ""):
            cut = str(row[0])[:10]
    except sqlite3.Error:
        pass
    d = _d(cut)
    if not d:
        return "0000-00-00"
    y, mo = d.year, d.month + 2
    y, mo = y + (mo - 1) // 12, (mo - 1) % 12 + 1
    return f"{y:04d}-{mo:02d}-01"


def check_posted(conn: sqlite3.Connection, ledger: Any, *, today: Any = None) -> dict:
    """Money EMA posted in a closed month never disappears (NON-NEGOTIABLE 1: an unreconciled dollar is a visible gap).
    For every service month, closed posting month (before the month of the ledger's newest posting) and payer group, the
    insurer's payments, the patient's payments, the adjustments and the patient adjustments, against the most this check
    has ever seen for them (their high-water mark, selfcheck_posted). Corrections and takebacks are new postings in their
    own month, so a closed month's money can only fall when postings left the ledger: a newer export of the same dates that
    lacks some (a filtered or partial export replacing a complete one), or EMA deleting them. Each fall over $1 is an
    offender with its dollars until a person accepts it (accept_findings moves the mark) or the missing postings are read
    again. Service months the ledger's retention is about to drop are left out, and nothing is judged while the catalog
    has a Production file waiting to be read again. A payer name that changes group (the name rules of now: an alias
    learned or dropped, the owner's aliases, a release's rules) takes its money with it, which is no money leaving: each
    name's money is compared under the group the marks were made with (meta selfcheck:posted_groups), and the marks move
    to the groups of now once nothing is missing. Cheap (one grouped pass); called by check_ledger and, on a serverless
    host, right after each Production file is applied. Saved in meta selfcheck:posted, which after_detection counts."""
    ensure_tables(conn)
    lc = ledger.conn if hasattr(ledger, "conn") else ledger
    at = now_of()

    def done(r: dict, **extra: Any) -> dict:
        meta_set(conn, K_POSTED, {"at": at, "today": today_of(today), "check": r, **extra})
        conn.commit()
        return r
    if lc is None or not _has_table(lc, "postings"):
        return done(_skip("posted_money", "no posting ledger"))
    if _has_table(conn, "ema_report_files") and conn.execute(
            "SELECT 1 FROM ema_report_files WHERE report = 'production' AND status = 'reread' LIMIT 1").fetchone():
        return done(_skip("posted_money", "the posting ledger is reading a Production file again: checked once it holds every file"))
    newest = lc.execute("SELECT MAX(posted) FROM postings").fetchone()[0]
    if not newest:
        return done(_skip("posted_money", "the posting ledger holds no posting yet"))
    open_month = str(newest)[:7]
    floor = _retain_floor(lc, str(newest)[:10])
    floor_m = floor[:7]
    from . import ema_reports as er
    names = {i: n for i, n in lc.execute("SELECT id, name FROM names")}
    groups: dict[Any, str] = {}

    def group(pid: Any) -> str:
        if pid not in groups:
            nm = names.get(pid)
            try:
                groups[pid] = er.payer_group_of(conn, nm)[0] if nm else "No payer named"
            except Exception as exc:          # noqa: BLE001
                _caught(exc, "payer_group")
                groups[pid] = str(nm or "No payer named")
        return groups[pid]
    # The name rules change on their own (an alias learned or dropped from the weekly exports' payer ids, the owner's
    # REMITWATCH_EMA_PAYER_ALIASES, a release's rules), and a payer name that changes group takes its money with it. So
    # each name's money is compared with the marks under the group the marks were made with (meta selfcheck:posted_groups):
    # the move itself is never read as money leaving the ledger, and a real loss shows with its own dollars.
    kept = meta_get(conn, K_POSTED_GROUPS)
    kept = kept if isinstance(kept, dict) else None

    def group_then(pid: Any) -> str:
        nm = names.get(pid)
        return str(kept[nm]) if kept is not None and nm and nm in kept else group(pid)
    now_cells: dict[tuple, int] = defaultdict(int)
    then_cells: dict[tuple, int] = defaultdict(int)
    payers: set = set()
    moved: set = set()
    for svc_m, post_m, payer, ins, pat, adj, padj in lc.execute(
            """SELECT substr(svc, 1, 7), substr(posted, 1, 7), payer, SUM(COALESCE(ins_paid, 0)), SUM(COALESCE(pat_paid, 0)),
                      SUM(COALESCE(contractual, 0) + COALESCE(noncontractual, 0)), SUM(COALESCE(pat_adj, 0))
               FROM postings WHERE svc >= ? AND posted < ? GROUP BY 1, 2, 3""", (floor, open_month + "-01")):
        g, g_then = group(payer), group_then(payer)
        for measure, v in (("ins_paid", ins), ("pat_paid", pat), ("adjustments", adj), ("pat_adj", padj)):
            if v:
                now_cells[(svc_m, post_m, g, measure)] += int(v)
                then_cells[(svc_m, post_m, g_then, measure)] += int(v)
                payers.add(payer)
                if g != g_then:
                    moved.add(payer)
    marks = {(s, p, g, m): h for s, p, g, m, h in conn.execute(
        "SELECT svc_month, posted_month, payer_group, measure, high_cents FROM selfcheck_posted")}

    def inside(k: tuple) -> bool:
        return k[0] >= floor_m and k[1] < open_month
    offs: list[tuple] = []
    rows = []
    for k in sorted(set(then_cells) | set(marks)):
        if not inside(k):
            continue
        now_c = int(then_cells.get(k, 0))
        high = marks.get(k)
        if high is None or now_c > high:
            high = now_c
        elif high - now_c > 100:
            offs.append((k, high - now_c))
        rows.append((*k, high, now_c, at))
    regrouped: dict = {}
    if kept is None and marks and offs:
        # marks an earlier version made, with no record of each name's group: a name that changed group since shows as a
        # fall in one group and a rise in another in the same months. Each month's money is summed across every group: a
        # month whose total did not fall lost nothing, and its marks move to the groups of now; one whose total fell keeps
        # its marks, and its fall is caught
        offs, rows, slices = _posted_by_month(offs, rows, marks, now_cells, inside, at)
        if slices:
            regrouped = {"slices": slices}
    elif moved and not offs:
        # nothing is missing under the groups the marks were made with: the marks move to the groups of now. A cell the
        # move changed starts again from what it holds now; every other cell keeps its mark
        changed = {k for k in set(now_cells) | set(then_cells) if inside(k) and now_cells.get(k, 0) != then_cells.get(k, 0)}
        rows = []
        for k in sorted(set(now_cells) | set(marks)):
            if inside(k):
                now_c, high = int(now_cells.get(k, 0)), marks.get(k)
                rows.append((*k, now_c if k in changed or high is None else max(high, now_c), now_c, at))
        regrouped = {"names": len(moved), "cells": len(changed)}
    elif moved:
        regrouped = {"names": len(moved), "waiting": True}     # moved once nothing is missing (read again, or accepted)
    conn.execute("DELETE FROM selfcheck_posted WHERE svc_month < ? OR posted_month >= ?", (floor_m, open_month))
    conn.executemany("""INSERT OR REPLACE INTO selfcheck_posted (svc_month, posted_month, payer_group, measure, high_cents, last_cents, at)
                        VALUES (?,?,?,?,?,?,?)""", rows)
    # the groups the marks stand in now: those of now once nothing is missing; else the ones they were made with (a name
    # met for the first time takes its group of now). An earlier version's marks with a month that fell record them once
    # nothing is missing
    if not offs:
        meta_set(conn, K_POSTED_GROUPS, {names[p]: group(p) for p in payers if names.get(p)})
    elif kept is not None:
        meta_set(conn, K_POSTED_GROUPS, {**kept, **{names[p]: group_then(p) for p in payers if names.get(p) and names[p] not in kept}})
    drop = sum(c for _k, c in offs)
    info = {"cells": len(rows), "closed_before": open_month, "service_from": floor_m, "first": not marks,
            "by_measure": dict(Counter(k[3] for k, _c in offs))}
    if regrouped:
        info["regrouped"] = regrouped
    if offs:
        text = (f"Money EMA posted left the posting ledger: {usd0(drop)} across {_n(len(offs), 'month cell')} (a newer export of "
                "the same dates lacks postings the one before had)")
        if regrouped.get("waiting"):
            k = regrouped["names"]
            text += (f". {_n(k, 'payer name')} also changed group: the check counts {'its' if k == 1 else 'their'} money "
                     "under the group the marks were made with until nothing is missing, then moves the marks")
        elif regrouped.get("slices"):
            text += (f". In {_n(regrouped['slices'], 'other month cell')} money only moved between payer groups (a payer name "
                     "changed group): none left there")
    else:
        text = f"{_n(len(rows), 'closed month cell')} checked: the money EMA posted stayed in the posting ledger"
        if regrouped.get("names"):
            k = regrouped["names"]
            text += (f"; {_n(k, 'payer name')} changed group, and the check moved {'its' if k == 1 else 'their'} marks with "
                     f"{'it' if k == 1 else 'them'}")
        elif regrouped.get("slices"):
            text += (f"; in {_n(regrouped['slices'], 'month cell')} money moved between payer groups (a payer name changed "
                     "group) and none left")
    seq = None
    try:
        seq = int(ledger.seq()) if hasattr(ledger, "seq") else None
    except Exception:          # noqa: BLE001
        seq = None
    return done(_result("posted_money", [("|".join(k), c, None) for k, c in offs], info=info, examples=[], text=text),
                ledger_seq=seq)


def _posted_by_month(offs: list[tuple], rows: list[tuple], marks: dict, now_cells: dict, inside: Callable[[tuple], bool],
                     at: str) -> tuple[list[tuple], list[tuple], int]:
    """check_posted on marks that kept no record of each payer name's group (an earlier version's): each (service month,
    posted month, measure) with a falling cell is summed across every payer group, marks against now. One whose total did
    not fall by more than $1 lost nothing (a name changed group: its money moved from one group to another), so its cells
    start again from what they hold now under the groups of now; one whose total fell keeps its marks and offenders.
    Returns (offenders, rows, how many months started again)."""
    def month(k: tuple) -> tuple:
        return (k[0], k[1], k[3])
    had: dict[tuple, int] = defaultdict(int)
    holds: dict[tuple, int] = defaultdict(int)
    for k, h in marks.items():
        if inside(k):
            had[month(k)] += int(h or 0)
    for k, v in now_cells.items():
        if inside(k):
            holds[month(k)] += int(v)
    calm = {month(k) for k, _c in offs if had[month(k)] - holds[month(k)] <= 100}
    if not calm:
        return offs, rows, 0
    rows = [r for r in rows if (r[0], r[1], r[3]) not in calm] + [
        (*k, int(now_cells.get(k, 0)), int(now_cells.get(k, 0)), at) for k in sorted(set(now_cells) | set(marks))
        if inside(k) and month(k) in calm]
    return [(k, c) for k, c in offs if month(k) not in calm], rows, len(calm)


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
    service month, split into the part still inside its window (recoverable) and the part already lost. The window is the
    item's own deadlines; an item with none on file is judged by its payer's appeal window from the day the payer answered
    (codes.py's windows: never 'lost' by default), and one with no date at all counts as recoverable. When the posting
    ledger's money check has run, the dollars written off at posting with no RemitWatch item at all (whatever the item's
    status: one a person let go is an item) are given beside it. Kept week by week in meta selfcheck:given_up; Today,
    Progress and the weekly email read it."""
    t = today_of(today)
    cols = set(_cols(conn, "findings"))
    if not {"written_off_cents", "written_off_when"} <= cols:
        return {"at": now_of(), "cents": 0, "items": 0, "note": "the store has no write-off columns yet"}
    accept = _policy_accepts(conn)
    legal = "legal_deadline" if "legal_deadline" in cols else "NULL"
    outcome = "ema_outcome" if "ema_outcome" in cols else "NULL"
    kind = "payer_kind" if "payer_kind" in cols else "NULL"
    by: dict[tuple, Counter] = defaultdict(Counter)
    tot = Counter()
    for row in conn.execute(f"""SELECT finding_id, payer_group, procedure_code, modifiers, carc, service_date, written_off_cents,
                                       appeal_deadline, {legal}, status, {outcome}, signal, {kind}, payment_date
                                FROM findings WHERE COALESCE(stale, 0) = 0 AND COALESCE(written_off_cents, 0) > 0
                                  AND written_off_when = 'at_posting' AND service_date >= ?
                                  AND status NOT IN ('dismissed', 'lost')""", (SERVICE_FROM,)):
        fid, grp, code, mods, carc, svc, cents, ad, ld, status, oc, sig, pkind, paid_on = row
        if sig == "EMA_DENIAL_AS_CONTRACTUAL":
            continue
        if any(k in accept for k in _pattern_key({"payer_group": grp, "procedure_code": code, "modifiers": mods, "carc": carc})):
            continue
        last = max([str(x)[:10] for x in (ad, ld) if x] or [""])
        if not last:
            # no deadline on file (677 items to work had none on the real archive): the payer's own appeal window from
            # its answer decides, never 'lost' by default
            days, _f = _windows(grp, pkind)
            last = _add(paid_on or svc, days) or ""
            tot["window_from_payer"] += int(cents)
        part = "recoverable" if not last or last >= t else "lost"
        if not last:
            tot["window_unknown"] += int(cents)
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
    on_items = _written_off_on_items(conn)
    out = {"at": now_of(), "today": t, "week": _week_of(t), "cents": tot["recoverable"] + tot["lost"],
           "recoverable_cents": tot["recoverable"], "lost_cents": tot["lost"], "items": tot["items"], "n": tot["items"],
           "window_from_payer_cents": tot["window_from_payer"], "window_unknown_cents": tot["window_unknown"],
           "by_payer": _top(rows, "payer_group"), "by_reason": _top(rows, "reason"), "by_month": _top(rows, "month"),
           "rows": rows[:200], "ledger_written_off_at_posting_cents": ledger_wo, "items_written_off_cents": on_items,
           "with_no_item_cents": max(0, int(ledger_wo) - on_items) if isinstance(ledger_wo, int) else None}
    if save:
        prev = meta_get(conn, K_GIVEN_UP, {}) or {}
        weeks = [w for w in (prev.get("weeks") or []) if w.get("week") != out["week"]]
        weeks.append({"week": out["week"], "cents": out["cents"], "recoverable_cents": out["recoverable_cents"],
                      "lost_cents": out["lost_cents"], "items": out["items"]})
        out["weeks"] = sorted(weeks, key=lambda w: w["week"])[-26:]
        meta_set(conn, K_GIVEN_UP, out)
    return out


def _written_off_on_items(conn: sqlite3.Connection) -> int:
    """The dollars written off at posting on 2025+ service that RemitWatch has an item for, whatever the item's status or
    whether it is listed (a person let it go, a policy accepts it, it is kept stale): each service (bill, date, code) once,
    at its largest write-off, so two items on one line are not counted twice."""
    cols = set(_cols(conn, "findings"))
    if not {"written_off_cents", "written_off_when"} <= cols:
        return 0
    per: dict[tuple, int] = {}
    for fid, pcn, svc, code, cents in conn.execute(
            """SELECT finding_id, patient_control_number, service_date, procedure_code, written_off_cents FROM findings
               WHERE COALESCE(written_off_cents, 0) > 0 AND written_off_when = 'at_posting' AND service_date >= ?""", (SERVICE_FROM,)):
        k = (bill_of(pcn), str(svc or "")[:10], code) if pcn else (fid,)
        per[k] = max(per.get(k, 0), int(cents or 0))
    return sum(per.values())


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
    """What the app cannot see, since when, and the dollars of claims it cannot check. The 835 feed: claims billed to
    insurance since the newest remittance with no payer's answer on file (their charges, line by line from the store's
    EMA lines, leaving out each line whose answer EMA posted; else EMA's monthly totals, prorated, marked 'about'). EMA's
    reports: the items to work whose 'already worked or paid in EMA' check rests on a report older than its limit. One
    banner sentence for every page; never 'all clear' while blind."""
    t = today_of(today)
    sources, feeds = [], []
    newest = None
    if _has_table(conn, "transactions"):
        newest = conn.execute("SELECT MAX(payment_date) FROM transactions WHERE COALESCE(superseded, 0) = 0").fetchone()[0]
    days = _days(newest, t) if newest else None
    limit = _int_setting("REMITWATCH_STALE_DAYS", STALE_835_DAYS)
    if newest:
        feeds.append({"feed": "835", "label": "Remittances (835)", "since": str(newest)[:10], "days": days,
                      "stale": days is not None and days > limit})
    if newest and days is not None and days > limit:
        cents, about = _charges_since(conn, newest, t)
        sources.append({"source": "835", "since": newest, "days": days, "cents": cents, "about": about,
                        "text": f"No remittance since {newest} ({days} days): {'about ' if about else ''}{usd0(cents)} billed since "
                                "then has no payer's answer on file (no 835, and none posted in EMA). RemitWatch sees only what "
                                "EMA posted for it."})
    if _has_table(conn, "ema_report_files") and conn.execute("SELECT 1 FROM ema_report_files LIMIT 1").fetchone():
        try:
            from .ema_checks import freshness
            fr = freshness(conn, t)
        except Exception:          # noqa: BLE001  (no freshness: say nothing rather than guess)
            fr = {}
        stale = []
        for key, label, lim in (("production", "Production report", STALE_PRODUCTION_DAYS), ("ar", "A/R report", STALE_AR_DAYS)):
            d = fr.get(f"{key}_days")
            if d is None:
                continue
            feeds.append({"feed": key, "label": f"EMA's {label}", "since": str(fr.get(f"{key}_as_of") or "")[:10] or None,
                          "days": d, "stale": d > lim})
            if d > lim:
                stale.append((key, label, fr.get(f"{key}_as_of"), d))
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
    # the dollars the site cannot check: the charges with no payer answer, plus the items to work once (both EMA reports
    # being old concerns the same items)
    cents = sum(int(x["cents"] or 0) for x in sources if x["source"] == "835") + \
        max([int(x["cents"] or 0) for x in sources if x["source"] != "835"], default=0)
    banner_text = " ".join(x["text"] for x in sources) if sources else None
    out = {"at": now_of(), "today": t, "blind": bool(sources), "sources": sources, "banner": banner_text,
           "feeds": feeds, "cents": cents, "lines": [x["text"] for x in sources], "basis": banner_text,
           "since": min((str(x["since"])[:10] for x in sources if x.get("since")), default=None)}
    if save:
        meta_set(conn, K_BLIND, out)
    return out


def _charges_since(conn: sqlite3.Connection, newest: str, today: str) -> tuple[int, bool]:
    """Charges billed to insurance for services after the newest remittance that have no payer's answer on file: from the
    store's EMA lines, line by line (a line whose insurer's answer EMA posted, a payment or a denial, is not blind; the
    summary keeps every line), exact; else EMA's monthly charges (the ledger's cells when the money check has run, else
    ema_months), the newest remittance's month prorated by days: 'about', and they cannot tell an answered claim from
    one with none."""
    if _has_table(conn, "ema_lines") and conn.execute("SELECT 1 FROM ema_lines LIMIT 1").fetchone():
        cols = set(_cols(conn, "ema_lines"))
        if {"svc", "charges"} <= cols:
            # a line the insurer answered, as the never-answered rule reads it (a bare $0 row under a check is no answer)
            from .ema_checks import line_answered_sql
            need = {"ins_paid", "contractual", "noncontractual", "allowed", "ins_answer_rows", "zero_pay_rows"}
            no_answer = (f"NOT {line_answered_sql('ema_lines')}" if need <= cols else
                         "COALESCE(ins_answer_rows, 0) = 0" if "ins_answer_rows" in cols else "1")
            to_ins = "COALESCE(ins_charges, 0) > 0" if "ins_charges" in cols else "1"
            n = conn.execute(f"""SELECT COALESCE(SUM(charges), 0) FROM ema_lines WHERE svc > ? AND {to_ins} AND {no_answer}""",
                             (str(newest)[:10],)).fetchone()[0]
            return int(n or 0), False
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
    med = _usual_rates(conn, since=None)
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


def _windows(group: str | None, kind: str | None, **facts: Any) -> tuple[int, int]:
    """(appeal days, filing days) for a payer group: codes.py's windows, read as data (as the answer keys did), with the
    plan facts the product passes (codes.rule_facts: the payer's name, the filing indicator, the reason, paid or not), so
    one plan of a group is never given another's window."""
    try:
        from .codes import appeal_rule, filing_rule
        a = appeal_rule(group, kind, **facts)
        f = filing_rule(group, kind, **facts)
        legal = a.get("legal") or {}
        days = int(a.get("days") or 180)
        if legal.get("days"):
            days = max(days, int(legal["days"]))
        elif legal.get("months") or legal.get("years"):
            days = max(days, int(30.44 * (int(legal.get("months") or 0) + 12 * int(legal.get("years") or 0))))
        return days, int(f.get("days") or 365)
    except Exception:          # noqa: BLE001
        return 180, 365


def _rule_facts(**facts: Any) -> dict:
    """The plan facts the product passes for a line (codes.rule_facts: the plan, the payer's name and id, the filing
    indicator, the denial's reason, paid or not), those known only: what the self-audit hands the window rules."""
    try:
        from .codes import PLAN_FACTS
    except ImportError:                 # pragma: no cover  (an older codes.py: the names it always took)
        PLAN_FACTS = ("plan", "payer_name", "payer_id", "cfi", "state", "carc", "paid")
    out = {}
    for k, v in facts.items():
        if k not in PLAN_FACTS or v is None:
            continue
        if isinstance(v, str):
            v = v.strip()
            if not v:
                continue
        out[k] = v
    return out


def _appeal_end(group: str | None, kind: str | None, answered: str | None, svc: str | None, **facts: Any) -> str | None:
    """The last day to work a line the payer answered on `answered`, as the product dates its item (states.windows, with the
    plan facts codes.rule_facts gives): the payer's dispute window from that answer, its own limit where later
    (codes.window_end, codes.legal_deadline); for a payer that takes no appeal of an ordinary claim denial (Kentucky
    Medicaid, 907 KAR 1:671) its corrected or new claim's window from the date of service (codes.corrected_rule)."""
    try:
        from . import codes
        r = codes.appeal_rule(group, kind, **facts)
        if r.get("no_appeal"):
            cr = codes.corrected_rule(group, kind, **facts)
            return codes.window_end(svc, cr) if cr and svc else None
        ends = [d for d in (codes.window_end(answered, r), codes.legal_deadline(answered, r)) if d]
        return max(ends) if ends else None
    except Exception as exc:          # noqa: BLE001  (a rule that cannot date the line dates it by the usual 180 days)
        _caught(exc, "selfcheck.appeal_end")
        return _add(answered, 180)


def _item_index(conn: sqlite3.Connection) -> dict:
    """Which services RemitWatch lists, and how: {(bill, svc, code): [(finding_id, work_state, status, signal, automatic)]}
    (automatic: closed by RemitWatch itself, with its note), with the finding's EMA claim number too, so a line can be
    found by either."""
    cols = set(_cols(conn, "findings"))
    ws = "work_state" if "work_state" in cols else "NULL"
    link = "ema_link" if "ema_link" in cols else "NULL"
    auto = "auto_note IS NOT NULL" if "auto_note" in cols else "0"
    by_service: dict[tuple, list] = defaultdict(list)
    by_link: dict[tuple, list] = defaultdict(list)
    by_bill: dict[str, list] = defaultdict(list)
    for fid, pcn, svc, pc, opc, bc, wk, st, lk, sig, is_auto in conn.execute(
            f"""SELECT finding_id, patient_control_number, service_date, procedure_code, original_procedure_code,
                       {'billed_code' if 'billed_code' in cols else 'NULL'}, {ws}, status, {link}, signal, {auto} FROM findings
                WHERE COALESCE(stale, 0) = 0"""):
        b = bill_of(pcn)
        entry = (fid, wk, st, sig, bool(is_auto))
        for code in {pc, opc, bc} - {None}:
            by_service[(b, str(svc or "")[:10], code)].append(entry)
            if lk is not None:
                by_link[(int(lk), str(svc or "")[:10], code)].append(entry)
        if b and sig in CLAIM_SIGNALS:
            by_bill[b].append(entry)
    return {"service": by_service, "link": by_link, "bill": by_bill}


def _decided(entry: tuple) -> bool:
    """A person's or the owner's decision holds the line: let go or lost, a pattern the owner's policy accepts, or marked
    paid by a person (resolved with no automatic note)."""
    _f, wk, st, _sig, is_auto = entry
    return st in ("dismissed", "lost") or wk == "accepted" or (st == "resolved" and not is_auto)


def _covered(entries: list) -> str | None:
    """How RemitWatch holds a line: 'listed' (Work now or Waiting), 'reason' (a person's or the owner's decision:
    _decided), None (no item, or only items RemitWatch set aside by itself: closed automatically, done, expired, can't be
    checked; those are the disagreements the audit exists to show)."""
    if not entries:
        return None
    if any(e[1] in LISTED and e[2] in OPEN_STATUSES for e in entries):
        return "listed"
    if any(_decided(e) for e in entries):
        return "reason"
    return None


def _held_as(entries: list) -> tuple[str, str | None]:
    """For a gap: how RemitWatch holds the line ('no_item', or the automatic state of its item) and that item's id."""
    if not entries:
        return "no_item", None
    e = sorted(entries, key=lambda x: x[0])[0]
    if e[2] == "resolved" and e[4]:
        return "closed_by_remitwatch", e[0]
    return (str(e[1]) if e[1] else "not_listed"), e[0]


def self_audit(conn: sqlite3.Connection, *, today: Any = None, force: bool = False, why: str = "weekly") -> dict:
    """Once a week (or on demand): (a) coverage: every 2025+ line with recoverable dollars inside a window (a denial, an
    automatic write-off still appealable, an unanswered claim, a short payment, an 835 payment EMA never posted, a second
    payer never billed, an open insurance balance) must have an item in Work now or Waiting or a person's (or the owner's
    policy) decision; every one that does not is a coverage gap with its dollars, named with the item RemitWatch closed,
    expired or set aside by itself when there is one; (b) precision: every Work now item with evidence it is settled (paid
    after or before, the patient's share, worked and waiting, a duplicate listing, a facility twin, past every window) is
    possible wasted work; (c) the confusion matrix by count and dollars, kept week by week; (d) the alert when any new gap
    (no dollar floor) or more than 10 new wasted items appear. Its rules are its own (the answer keys' rules, simplified),
    never the detection code. It reads the 835 archive and the store's own EMA lines (the summary keeps every line the
    ledger holds), the same on both hosts, so it takes no ledger. Returns the summary."""
    ensure_tables(conn)
    t = today_of(today)
    if not force and not audit_due(conn, t):
        return {"skipped": "done this week", "last": conn.execute("SELECT MAX(today) FROM selfcheck_audits").fetchone()[0]}
    t0 = time.time()
    idx = _item_index(conn)
    gaps: list[dict] = []
    lines_needing: list[dict] = []
    notes: list[str] = []
    got, notes = _cov_all(conn, t, idx)
    lines_needing.extend(got)
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
    for g in gaps:
        parts = tuple(str(g["key"]).split("|"))
        ents = idx["service"].get(parts) or (idx["bill"].get(parts[0]) if g["category"] == "unanswered_in_filing_limit" else None)
        g["held_as"], g["finding_id"] = _held_as(ents or [])
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
        items.append((audit_id, "gap", g["key"], g.get("finding_id"), g["category"], g.get("reason"), g.get("payer_group"),
                      g.get("month"), int(g["cents"]), g.get("deadline"), int(bool(prev_id) and g["key"] not in prev_keys["gap"])))
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
    wasted_alert = _int_setting("REMITWATCH_AUDIT_WASTED_ITEMS", AUDIT_WASTED_ALERT)
    big_new = list(new_gaps)                    # the owner's decision 9: any new gap, whatever its dollars
    # coverage: of the lines that needed work (and had no reason to be left), the share the worklist listed; precision: of
    # the items in Work now, the share the audit found no sign of being already paid or settled
    coverage = {"lines": len(uniq), "listed": len(listed), "held_by_reason": len(reasons), "missed": len(gaps),
                "missed_cents": sum(int(x["cents"]) for x in gaps), "missed_by": dict(Counter(g["held_as"] for g in gaps)),
                "share": round(len(listed) / (len(listed) + len(gaps)), 4) if (listed or gaps) else None}
    precision = {"work_now": wn_n, "possible_waste": w_n, "possible_waste_cents": w_cents,
                 "share": round(1 - w_n / wn_n, 4) if wn_n else None}
    summary = {"audit_id": audit_id, "at": now_of(), "today": t, "why": why, "seconds": round(time.time() - t0, 1),
               "coverage": coverage, "precision": precision,
               "gaps_n": len(gaps), "gaps_cents": sum(int(x["cents"]) for x in gaps),
               "gaps_by_category": _by(gaps, "category"), "gaps_by_payer": _by(gaps, "payer_group")[:12],
               "gaps_by_holding": _by(gaps, "held_as"),
               "wasted_n": w_n, "wasted_cents": w_cents, "wasted_by_reason": _by(wasted, "reason"),
               "wasted_by_signal": _by(wasted, "signal")[:12], "matrix": matrix,
               "covered_by_category": {c: {"lines": sum(1 for x in uniq if x["category"] == c),
                                           "listed": sum(1 for x in listed if x["category"] == c),
                                           "reason": sum(1 for x in reasons if x["category"] == c),
                                           "gap": sum(1 for x in gaps if x["category"] == c)} for c in CATEGORY_ORDER},
               "new_gaps_n": len(new_gaps), "new_gaps_cents": sum(int(x["cents"]) for x in new_gaps), "new_big_gaps": len(big_new),
               "new_wasted_n": len(new_wasted), "first": not bool(prev_id), "notes": notes,
               "alert": bool(big_new) or len(new_wasted) > wasted_alert,
               "thresholds": {"gap_cents": 0, "wasted_items": wasted_alert}}
    conn.execute("INSERT OR REPLACE INTO selfcheck_audits (audit_id, at, today, why, summary) VALUES (?,?,?,?,?)",
                 (audit_id, summary["at"], t, why, json.dumps(summary, default=str)))
    keep = [r[0] for r in conn.execute("SELECT audit_id FROM selfcheck_audits ORDER BY today DESC, at DESC LIMIT 3")]
    conn.execute(f"DELETE FROM selfcheck_audit_items WHERE audit_id NOT IN ({','.join('?' * len(keep))})", keep)
    weeks = []
    for at_, tdy, s in conn.execute("SELECT at, today, summary FROM selfcheck_audits ORDER BY today DESC, at DESC LIMIT 26"):
        x = _ev(s)
        weeks.append({"today": tdy, "gaps_n": x.get("gaps_n"), "gaps_cents": x.get("gaps_cents"), "wasted_n": x.get("wasted_n"),
                      "wasted_cents": x.get("wasted_cents"), "matrix": x.get("matrix")})
    meta_set(conn, K_AUDIT, {"latest": summary, "weeks": list(reversed(weeks)), "at": summary["at"], "coverage": coverage,
                             "precision": precision, "matrix": matrix})
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


def _usual_rates(conn: sqlite3.Connection, since: str | None = "default") -> dict:
    """What each payer group usually allows for a code: the median allowed amount of its paid one-unit lines (from
    `since`, by default the year before the gates' population), with at least 3 lines. {(payer group, code): cents}."""
    lo = _add(SERVICE_FROM, -365) if since == "default" else (since or "0000")
    counts: dict[tuple, Counter] = defaultdict(Counter)
    for name, cfi, code, allowed, n in conn.execute(
            """SELECT l.payer_name, c.claim_filing_indicator, COALESCE(l.original_procedure_code, l.procedure_code),
                      COALESCE(l.allowed_cents, l.paid_cents), COUNT(*) FROM lines l JOIN claims c ON c.claim_key = l.claim_key
               WHERE l.claim_status_code IN ('1','19') AND COALESCE(l.paid_cents, 0) > 0 AND COALESCE(l.paid_units, 1) <= 1
                 AND l.service_date >= ? GROUP BY 1, 2, 3, 4""", (lo,)):
        if allowed and allowed > 0:
            counts[(_group_kind(name, cfi)[0], code)][int(allowed)] += n
    out = {}
    for k, cnt in counts.items():
        total = sum(cnt.values())
        if total < 3:
            continue
        half, run = (total + 1) // 2, 0
        for amount in sorted(cnt):
            run += cnt[amount]
            if run >= half:
                out[k] = amount
                break
    return out


EXCLUDED_DENIAL_CARCS = frozenset({"209", "246", "24", "29"})   # Medicaid cost share, reporting codes, capitation, timely filing


class _Facts:
    """What the self-audit reads once, by service (bill, date of service, code), from the raw tables: EMA's lines (the
    store's lean summaries), the 835 answers of 2025+ service, each payer group's pay share and usual amount per code."""

    def __init__(self, conn: sqlite3.Connection, today: str):
        self.conn, self.today = conn, today
        self.ema: dict[tuple, dict] = {}
        # an EMA claim number's bill: from the map (835 claims) and from the A/R's claim ids (claims no 835 answered)
        self.bill_of_link: dict[int, str] = {}
        for table in ("ema_claim_links", "ema_link_map"):
            if _has_table(conn, table):
                for link, bill in conn.execute(f"SELECT link, bill FROM {table} WHERE link IS NOT NULL AND bill IS NOT NULL"):
                    self.bill_of_link[int(link)] = bill
        if _has_table(conn, "ema_lines"):
            # whether and when a payer answered each line, as the never-answered rule reads it (ema_checks: a bare $0 row
            # under a check, no reason, allowed amount or adjustment, answers nothing)
            from .ema_checks import first_answer_days, line_answered_sql
            fa = first_answer_days(conn, "sc_fa") if _has_table(conn, "ema_line_payments") and _has_table(conn, "ema_line_reasons") else None
            first_sql = f"(SELECT a.d FROM temp.{fa} a WHERE a.link = l.link AND a.svc = l.svc AND a.cpt = l.cpt)" if fa else "l.first_ins_posting"
            for (bill, link, svc, cpt, grp, kind, second, charges, ins_charges, ins, bal, nonroutine, den_first, first_ans,
                 last_paid, status_class, fac, answers, first_charge, payer, plan) in conn.execute(
                    f"""SELECT l.bill, l.link, l.svc, l.cpt, l.payer_group, l.payer_kind, l.secondary_payer, COALESCE(l.charges, 0),
                              COALESCE(l.ins_charges, 0), COALESCE(l.ins_paid, 0), COALESCE(l.balance, 0), COALESCE(l.nonroutine, 0),
                              l.denial_first_posted, {first_sql}, l.last_ins_paid, l.status_class, COALESCE(l.facility, 0),
                              {line_answered_sql('l')}, l.first_charge_posted, l.primary_payer, l.plan
                       FROM ema_lines l WHERE l.svc >= ?""", (SERVICE_FROM,)):
                key = (bill or self.bill_of_link.get(int(link)) or f"link:{link}", str(svc)[:10], cpt or "")
                e = self.ema.get(key)
                if e is None:
                    e = self.ema[key] = {"links": [], "group": grp, "kind": kind, "second": second, "payer": payer,
                                         "plan": (plan or "").strip() or None, "charges": 0, "ins_charges": 0,
                                         "ins_paid": 0, "balance": 0, "nonroutine": 0, "den_first": None, "first_ans": None,
                                         "last_paid": None, "status": set(), "facility": False, "answers": 0, "first_charge": None}
                e["links"].append(link)
                for k, v in (("charges", charges), ("ins_charges", ins_charges), ("ins_paid", ins), ("balance", bal),
                             ("nonroutine", nonroutine), ("answers", answers)):
                    e[k] += v or 0
                e["den_first"] = min(x for x in (e["den_first"], den_first) if x) if (e["den_first"] or den_first) else None
                e["first_ans"] = min(x for x in (e["first_ans"], first_ans) if x) if (e["first_ans"] or first_ans) else None
                e["last_paid"] = max(x for x in (e["last_paid"], last_paid) if x) if (e["last_paid"] or last_paid) else None
                e["first_charge"] = min(x for x in (e["first_charge"], first_charge) if x) if (e["first_charge"] or first_charge) else None
                e["status"].add(status_class)
                e["facility"] = e["facility"] or bool(fac)
            if fa:
                conn.execute(f"DROP TABLE IF EXISTS temp.{fa}")
        self.reasons: dict[tuple, set] = defaultdict(set)
        if _has_table(conn, "ema_line_reasons") and _has_table(conn, "ema_lines"):
            for bill, link, svc, cpt, code, cls in conn.execute(
                    """SELECT l.bill, r.link, r.svc, r.cpt, r.code, r.reason_class FROM ema_line_reasons r
                       JOIN ema_lines l ON l.link = r.link AND l.svc = r.svc AND l.cpt = r.cpt
                       WHERE r.svc >= ? AND r.reason_class NOT IN ('routine', 'patient', 'none')""", (SERVICE_FROM,)):
                self.reasons[(bill or self.bill_of_link.get(int(link)) or f"link:{link}", str(svc)[:10], cpt or "")].add((str(code), cls))
        self.has_835 = _has_table(conn, "lines") and _has_table(conn, "claims")
        bill = _bill_sql("l.patient_control_number")
        if self.has_835:
            conn.execute("DROP TABLE IF EXISTS temp.sc_svc")
            conn.execute(f"""CREATE TEMP TABLE sc_svc AS
                             SELECT {bill} AS bill, l.service_date AS svc, COALESCE(l.original_procedure_code, l.procedure_code) AS code,
                                    l.payment_date AS paid_on, COALESCE(l.paid_cents, 0) AS paid, l.claim_status_code AS st,
                                    c.payer_name AS payer, c.claim_filing_indicator AS cfi, l.line_key AS line_key,
                                    l.patient_control_number AS pcn, COALESCE(l.charge_cents, 0) AS charge,
                                    c.contract_class AS plan, c.payer_id AS payer_id
                             FROM lines l JOIN claims c ON c.claim_key = l.claim_key
                             JOIN transactions t ON t.transaction_key = l.transaction_key
                             WHERE COALESCE(t.superseded, 0) = 0 AND l.service_date >= ?""", (SERVICE_FROM,))
            conn.execute("CREATE INDEX temp.ix_sc_svc ON sc_svc(bill, svc, code)")
            conn.execute("DROP TABLE IF EXISTS temp.sc_pr")
            conn.execute("""CREATE TEMP TABLE sc_pr AS SELECT a.line_key AS line_key, SUM(a.amount_cents) AS pr FROM adjustments a
                            WHERE a.line_key IS NOT NULL AND a.group_code = 'PR' AND a.reason_code IN ('1','2','3') GROUP BY a.line_key""")
            conn.execute("CREATE INDEX temp.ix_sc_pr ON sc_pr(line_key)")
        self.usual = _usual_rates(conn) if self.has_835 else {}
        # each payer group's pay share for a code over its answered 2025+ primary lines (the answer key's policy rule R9)
        self.share: dict[tuple, tuple] = {}
        if self.has_835:
            agg: dict[tuple, list] = defaultdict(lambda: [0, 0])
            for payer, cfi, code, n, paid_n in conn.execute(
                    """SELECT payer, cfi, code, COUNT(*), SUM(paid > 0) FROM temp.sc_svc WHERE st IN ('1','19')
                       GROUP BY payer, cfi, code"""):
                a = agg[(_group_kind(payer, cfi)[0], code)]
                a[0] += n
                a[1] += paid_n or 0
            self.share = {k: (v[0], v[1]) for k, v in agg.items()}

    def policy(self, group: str, code: str) -> bool:
        n, paid = self.share.get((group, code), (0, 0))
        return n >= 20 and paid <= 0.05 * n

    def answers(self, key: tuple) -> list[tuple]:
        if not self.has_835:
            return []
        return self.conn.execute("""SELECT s.paid_on, s.paid, s.st, s.payer, s.cfi, COALESCE(p.pr, 0), s.line_key FROM temp.sc_svc s
                                    LEFT JOIN temp.sc_pr p ON p.line_key = s.line_key
                                    WHERE s.bill = ? AND s.svc = ? AND s.code = ?""", key).fetchall()

    def close(self) -> None:
        for tb in ("sc_svc", "sc_pr"):
            self.conn.execute(f"DROP TABLE IF EXISTS temp.{tb}")


def _cov_all(conn: sqlite3.Connection, today: str, idx: dict) -> tuple[list, list[str]]:
    """Gate-1 rule set over every 2025+ service (bill, date of service, code), one category per service (most actionable
    first, the answer key's R20): an 835 payment EMA never posted, an automatic write-off still appealable, a denial in
    window, a short payment, a second payer never billed, an unanswered claim."""
    facts = _Facts(conn, today)
    notes: list[str] = []
    found: list[dict] = []
    try:
        for fn in (_cov_denials, _cov_auto_writeoffs, _cov_unanswered, _cov_secondary, _cov_short_pay):
            try:
                found.extend(fn(facts, idx))
            except sqlite3.Error as exc:
                _caught(exc, fn.__name__)
                notes.append(f"{fn.__name__[5:]}: could not run ({type(exc).__name__})")
        try:
            found.extend(_cov_unposted(facts, idx))
        except sqlite3.Error as exc:
            _caught(exc, "unposted")
            notes.append(f"unposted: could not run ({type(exc).__name__})")
    finally:
        facts.close()
    if not facts.ema:
        notes.append("EMA's lines are not on file: automatic write-offs, unanswered claims and second payers are not checked")
    return found, notes


def _held(idx: dict, key: tuple) -> str | None:
    return _covered(idx["service"].get(key))


def _cov_denials(facts: _Facts, idx: dict) -> list[dict]:
    """Denials (answer key R2, R3, R9, R10, R19, simplified): a 2025+ service whose primary payer's newest answer denies it
    (nothing paid, a denial reason other than cost share, reporting, capitation or timely filing), with no money for it
    since or before from any payer (835 or EMA) reaching half its usual amount, not moved to the patient, not a code the
    payer never pays (5% of 20 answers), inside the payer's window from that answer, worth the usual amount less what
    came in."""
    if not facts.has_835:
        return []
    conn = facts.conn
    excluded = ",".join(f"'{c}'" for c in sorted(ROUTINE_CARCS | EXCLUDED_DENIAL_CARCS))
    conn.execute("DROP TABLE IF EXISTS temp.sc_den")
    conn.execute(f"""CREATE TEMP TABLE sc_den AS SELECT a.line_key AS line_key, MAX(a.reason_code) AS carc
                     FROM adjustments a WHERE a.line_key IS NOT NULL AND a.group_code IN ('CO','OA','PI') AND a.amount_cents > 0
                       AND a.reason_code NOT IN ({excluded}) GROUP BY a.line_key""")
    conn.execute("CREATE INDEX temp.ix_sc_den ON sc_den(line_key)")
    latest: dict[tuple, tuple] = {}
    for b, svc, code, paid_on, payer, cfi, carc, charge, pcn, plan, pid in conn.execute(
            """SELECT s.bill, s.svc, s.code, s.paid_on, s.payer, s.cfi, d.carc, s.charge, s.pcn, s.plan, s.payer_id FROM temp.sc_svc s
               JOIN temp.sc_den d ON d.line_key = s.line_key WHERE s.st IN ('1','19') AND s.paid = 0 AND s.charge > 0"""):
        key = (b, str(svc)[:10], code)
        if is_facility(pcn):
            continue
        if key not in latest or str(paid_on) > str(latest[key][0]):
            latest[key] = (paid_on, payer, cfi, carc, charge, plan, pid)
    conn.execute("DROP TABLE IF EXISTS temp.sc_den")
    out = []
    for key, (paid_on, payer, cfi, carc, charge, plan, pid) in latest.items():
        grp, kind = _group_kind(payer, cfi)
        if facts.policy(grp, key[2]):
            continue
        exp = facts.usual.get((grp, key[2])) or int(charge * 0.4)
        ans = facts.answers(key)
        r835 = sum(p for _d, p, st, _py, _c, _pr, _lk in ans if st in ("1", "2", "3", "19", "20", "21", "22"))
        moved = sum(pr for d2, _p, _st, _py, _c, pr, _lk in ans if str(d2) > str(paid_on))
        e = facts.ema.get(key) or {}
        got = max(r835, int(e.get("ins_paid") or 0))
        if got >= SETTLED_SHARE * exp or moved >= 0.8 * exp or "paid" in (e.get("status") or set()):
            continue
        # the plan facts the product passes for the item (codes.rule_facts: the plan, the payer's name and id, the filing
        # indicator, the reason, unpaid), so one plan of a group is dated by its own window
        deadline = _appeal_end(grp, kind, paid_on, key[1], **_rule_facts(plan=plan, payer_name=payer, payer_id=pid, cfi=cfi,
                                                                          carc=carc, paid=False))
        if not deadline or deadline < facts.today:
            continue
        cents = max(0, min(int(charge), int(exp) - max(0, got)))
        if cents <= 0:
            continue
        out.append({"key": "|".join(key), "category": "denial_in_window", "reason": str(carc), "payer_group": grp,
                    "month": key[1][:7], "cents": cents, "deadline": deadline, "held": _held(idx, key)})
    return out


def _cov_auto_writeoffs(facts: _Facts, idx: dict) -> list[dict]:
    """EMA's automatic write-offs (answer key R4, R9, simplified): a 2025+ EMA line whose denial EMA wrote off on the day
    the payer's first answer posted (its posting rule, not a person), with no insurance money reaching half its usual
    amount, the reasons not only cost share, reporting, capitation or timely filing, not a code the payer never pays, inside
    the payer's window from that day, worth what was written off."""
    out = []
    for key, e in facts.ema.items():
        if e["facility"] or e["nonroutine"] <= 0 or not e["den_first"] or e["den_first"] != e["first_ans"]:
            continue
        codes = {c for c, _cls in facts.reasons.get(key, set())}
        if codes and codes <= EXCLUDED_DENIAL_CARCS:
            continue
        if any(cls == "timely" for _c, cls in facts.reasons.get(key, set())) and len(codes) <= 1:
            continue
        grp, kind = e["group"], e["kind"]
        if facts.policy(grp, key[2]):
            continue
        exp = facts.usual.get((grp, key[2])) or e["nonroutine"]
        if e["ins_paid"] >= SETTLED_SHARE * exp or (e["last_paid"] and e["last_paid"] > e["den_first"]):
            continue
        top = sorted(c for c, _cls in facts.reasons.get(key, set()))
        deadline = _appeal_end(grp, kind, e["den_first"], key[1], **_rule_facts(
            plan=e.get("plan"), payer_name=e.get("payer"), carc=top[0] if len(top) == 1 else None, paid=e["ins_paid"] > 0))
        if not deadline or deadline < facts.today:
            continue
        cents = min(int(e["nonroutine"]), int(e["charges"])) if e["charges"] > 0 else int(e["nonroutine"])
        if cents <= 0:
            continue
        out.append({"key": "|".join(key), "category": "auto_written_off_appealable", "reason": ",".join(sorted(codes)) or "posting_rule",
                    "payer_group": grp, "month": key[1][:7], "cents": cents, "deadline": deadline, "held": _held(idx, key)})
    return out


def _cov_unanswered(facts: _Facts, idx: dict) -> list[dict]:
    """Unanswered claims (answer key R7, R11, simplified): a 2025+ EMA line billed to insurance with no answer in EMA or in
    the 835 archive, older than the payer's answer time (at least 30 days), still inside the first-filing limit plus the
    payer's window for a claim sent in time."""
    turn = meta_get(facts.conn, "ema:turnaround", {}) or {}
    groups = (turn.get("groups") or {}) if isinstance(turn, dict) else {}
    out = []
    for key, e in facts.ema.items():
        if e["facility"] or e["answers"] > 0 or e["ins_charges"] <= 0 or e["balance"] <= 0 or not e["first_charge"]:
            continue
        if e["status"] & {"paid", "settled", "void", "gave_up"}:
            continue
        if facts.answers(key):
            continue                    # the payer answered it in an 835: not unanswered
        g = groups.get(e["group"]) or {}
        wait = max(30, int(g.get("svc_p95") or g.get("p95") or 45)) if isinstance(g, dict) else 45
        if (_days(e["first_charge"], facts.today) or 0) < wait:
            continue
        appeal_days, filing_days = _windows(e["group"], e["kind"], **_rule_facts(plan=e.get("plan"), payer_name=e.get("payer")))
        deadline = _add(key[1], filing_days + appeal_days)
        if not deadline or deadline < facts.today:
            continue
        cents = min(int(e["balance"]), int(e["charges"])) if e["charges"] > 0 else int(e["balance"])
        held = _held(idx, key) or _covered(idx["bill"].get(key[0]))
        out.append({"key": "|".join(key), "category": "unanswered_in_filing_limit", "reason": ",".join(sorted(x for x in e["status"] if x)),
                    "payer_group": e["group"], "month": key[1][:7], "cents": cents, "deadline": deadline, "held": held})
    return out


def _cov_unposted(facts: _Facts, idx: dict) -> list[dict]:
    """835 money never posted (answer key R12, simplified to whole checks): a funded 2025+ remittance (not $0, not
    informational) at least three weeks older than the newest EMA posting, inside the span EMA's Production covers, that
    no EMA check was matched to (ema_checks.trace_key). Partial postings of a check are not seen here."""
    conn = facts.conn
    if not (_has_table(conn, "transactions") and _has_table(conn, "ema_checks")):
        return []
    lo, hi = conn.execute("SELECT MIN(first_posted), MAX(last_posted) FROM ema_checks").fetchone()
    if not lo or not hi:
        return []
    matched = {tk for (tk,) in conn.execute("SELECT DISTINCT trace_key FROM ema_checks WHERE trace_key IS NOT NULL")}
    cut = _add(min(facts.today, hi), -21)
    unposted = []
    for tk, paid_on, cents, payer in conn.execute(
            """SELECT transaction_key, payment_date, COALESCE(payment_amount_cents, 0), payer_name FROM transactions
               WHERE COALESCE(superseded, 0) = 0 AND payment_date >= ? AND payment_date <= ? AND payment_date >= ?
                 AND COALESCE(payment_amount_cents, 0) > 0 AND COALESCE(payment_method, '') <> 'NON'""", (SERVICE_FROM, cut, lo)):
        if tk not in matched:
            unposted.append((tk, paid_on, cents, payer))
    out = []
    if not unposted:
        return out
    conn.execute("DROP TABLE IF EXISTS temp.sc_tk")
    conn.execute("CREATE TEMP TABLE sc_tk (tk TEXT PRIMARY KEY)")
    conn.executemany("INSERT OR IGNORE INTO temp.sc_tk VALUES (?)", [(u[0],) for u in unposted])
    per: dict[str, list] = defaultdict(list)
    for tk, pcn, svc, code, paid in conn.execute(
            """SELECT c.transaction_key, c.patient_control_number, l.service_date, COALESCE(l.original_procedure_code, l.procedure_code),
                      COALESCE(l.paid_cents, 0)
               FROM temp.sc_tk k JOIN claims c ON c.transaction_key = k.tk JOIN lines l ON l.claim_key = c.claim_key
               WHERE COALESCE(l.paid_cents, 0) > 0 AND l.service_date >= ?""", (SERVICE_FROM,)):
        per[tk].append((bill_of(pcn), str(svc)[:10], code, paid))
    conn.execute("DROP TABLE IF EXISTS temp.sc_tk")
    for tk, paid_on, cents, payer in unposted:
        for b, svc, code, paid in per.get(tk, []):
            key = (b, svc, code)
            out.append({"key": "|".join(key), "category": "era_payment_not_posted", "reason": "check_not_posted",
                        "payer_group": _group_kind(payer)[0], "month": svc[:7], "cents": int(paid), "deadline": None,
                        "held": _held(idx, key)})
    return out


def _cov_secondary(facts: _Facts, idx: dict) -> list[dict]:
    """Second payer never billed (answer key R5, simplified): a 2025+ EMA line whose primary paid more than 30 days ago,
    with a second payer on file (not Medicaid), no money or answer from any other payer in EMA or the 835s, and a balance
    left, inside the second payer's filing limit from that payment."""
    conn = facts.conn
    if not _has_table(conn, "ema_line_payments"):
        return []
    from .ema_checks import answer_row_sql
    out = []
    for key, e in facts.ema.items():
        second = e["second"]
        if e["facility"] or not second or second in ("", "None") or e["ins_paid"] <= 0 or e["balance"] <= 0:
            continue
        if re.search(r"MEDICAID|PASSPORT|HEALTHY HORIZONS", str(second).upper()) or _group_kind(second)[1] == "medicaid":
            continue
        if not e["last_paid"] or (_days(e["last_paid"], facts.today) or 0) < 30:
            continue
        # an answer on the line from a payer other than the primary, as the product reads one (detect._ema_lines_for): a
        # payment row that answers (ema_checks.answer_row_sql: a bare $0 row, no reason, allowed amount or adjustment,
        # answers nothing) or that payer's own reason posted on the line
        roles = set()
        for link in e["links"]:
            roles |= {r for (r,) in conn.execute(f"""SELECT DISTINCT y.role FROM ema_line_payments y
                                                     WHERE y.link = ? AND y.svc = ? AND y.cpt = ? AND {answer_row_sql('y')}""",
                                                 (link, key[1], key[2]))}
            if _has_table(conn, "ema_line_reasons"):
                for (who,) in conn.execute("""SELECT DISTINCT payer FROM ema_line_reasons WHERE link = ? AND svc = ? AND cpt = ?
                                              AND COALESCE(grp, '') NOT IN ('', 'PR') AND payer IS NOT NULL""", (link, key[1], key[2])):
                    if who != e.get("payer") and _group_kind(who)[0] != e["group"]:
                        roles.add("other")
        if roles - {"primary"}:
            continue
        own_group = e["group"]
        ans = facts.answers(key)
        if any(st in ("2", "3", "20", "21") or (_group_kind(py, cfi)[0] != own_group and p > 0)
               for _d, p, st, py, cfi, _pr, _lk in ans):
            continue
        crossed = any(st == "19" for _d, _p, st, _py, _cfi, _pr, _lk in ans)
        if crossed and (_days(e["last_paid"], facts.today) or 0) < 45:
            continue
        # the second payer's own first-filing rule, as it counts a claim it gets second (codes.second_claim_window, as the
        # product dates the item), from the group its name gives: never its name read as a group
        deadline = _second_end(conn, second, key[1], e["last_paid"], own_group)
        if not deadline or deadline < facts.today:
            continue
        out.append({"key": "|".join(key), "category": "open_balance_in_window" if crossed else "secondary_not_billed",
                    "reason": "crossover_no_answer" if crossed else "second_payer", "payer_group": own_group,
                    "month": key[1][:7], "cents": int(e["balance"]), "deadline": deadline, "held": _held(idx, key)})
    return out


def _second_end(conn: sqlite3.Connection, second: str, svc: str | None, first_paid: str | None, first_group: str | None) -> str | None:
    """A second payer's last day for a share the first payer left: its group by EMA's name rules, its first-filing rule
    with its name as the plan fact, counted as it counts a second claim (codes.second_claim_window: the safe date, the
    payer's own last day where later), as the product dates SECONDARY_NOT_BILLED."""
    try:
        from . import codes
        from .ema_reports import payer_group_of
        sgrp, skind = payer_group_of(conn, second)
        rule = codes.filing_rule(sgrp, skind, payer_name=second)
        safe, last, _how = codes.second_claim_window(rule, svc, str(first_paid or "")[:10] or None, first_payer=first_group)
        return last or safe
    except Exception as exc:          # noqa: BLE001  (a rule that cannot date the share dates it a year from the payment)
        _caught(exc, "selfcheck.second_end")
        return _add(first_paid, 365)


def _cov_short_pay(facts: _Facts, idx: dict) -> list[dict]:
    """Short payments (answer key R8, simplified): a 2025+ one-unit CPT line its payer paid, with no modifier, below the
    payer's own consistent rate for the code that year (the most frequent allowed amount, at least 60% of at least 8 lines)
    by more than max($2, 3%), where the amount paid is not one the payer pays regularly (10% of its lines: another
    schedule) and not a known cut (the non-physician 85%, a multiple-procedure 50%, 75% or 25%), inside the window."""
    conn = facts.conn
    if not facts.has_835:
        return []
    try:
        from .codes import MPPR_CODES as mppr
    except ImportError:
        mppr = frozenset()
    modes: dict[tuple, Counter] = defaultdict(Counter)
    rows = conn.execute("""SELECT l.line_key, l.patient_control_number, l.service_date, l.procedure_code, l.payment_date, l.payer_name,
                                  c.claim_filing_indicator, COALESCE(l.allowed_cents, 0), COALESCE(l.paid_cents, 0), l.modifiers,
                                  c.contract_class, c.payer_id
                           FROM lines l JOIN claims c ON c.claim_key = l.claim_key JOIN transactions t ON t.transaction_key = l.transaction_key
                           WHERE COALESCE(t.superseded, 0) = 0 AND l.claim_status_code IN ('1','19') AND COALESCE(l.paid_cents, 0) > 0
                             AND COALESCE(l.paid_units, 1) = 1 AND l.service_date >= ? AND l.procedure_code GLOB '[0-9][0-9][0-9][0-9][0-9]'""",
                        (SERVICE_FROM,)).fetchall()
    for _lk, _pcn, svc, code, _pd, name, cfi, allowed, _paid, mods, _plan, _pid in rows:
        if allowed > 0 and not (mods and mods not in ("[]", "")):
            modes[(_group_kind(name, cfi)[0], code, str(svc)[:4])][allowed] += 1
    usual = {}
    for k, cnt in modes.items():
        n = sum(cnt.values())
        top, m = cnt.most_common(1)[0]
        if n >= 8 and m / n >= 0.6:
            usual[k] = (top, cnt, n)
    out = []
    for lk, pcn, svc, code, paid_on, name, cfi, allowed, paid, mods, plan, pid in rows:
        if (mods and mods not in ("[]", "")) or is_facility(pcn) or allowed <= 0:
            continue
        grp, kind = _group_kind(name, cfi)
        u = usual.get((grp, code, str(svc)[:4]))
        if not u:
            continue
        rate, cnt, n = u
        short = rate - allowed
        if short <= max(200, int(rate * 0.03)) or cnt.get(allowed, 0) >= 0.10 * n:
            continue
        ratio = allowed / rate
        if any(abs(ratio - r) <= 0.02 for r in (0.85, 0.5, 0.75, 0.25)) and (code in mppr or abs(ratio - 0.85) <= 0.02):
            continue
        key = (bill_of(pcn), str(svc)[:10], code)
        deadline = _appeal_end(grp, kind, paid_on, str(svc)[:10], **_rule_facts(plan=plan, payer_name=name, payer_id=pid,
                                                                                    cfi=cfi, paid=True))
        if not deadline or deadline < facts.today:
            continue
        out.append({"key": "|".join(key), "category": "short_vs_payer_rate", "reason": "below_own_rate", "payer_group": grp,
                    "month": key[1][:7], "cents": int(short), "deadline": deadline, "held": _held(idx, key)})
    return out


def _precision(conn: sqlite3.Connection, today: str) -> list[dict]:
    """Gate-2 rule set over the Work now items: settled (paid after or before by the payer, another payer, or the patient's
    share), already worked and waiting, a duplicate listing, a facility claim, or past every window."""
    items = _work_now_money(conn)
    if not items:
        return []
    later = later_payments(conn, [it for it in items if it.get("signal") not in CLAIM_SIGNALS])
    before = _earlier_payments(conn, items)
    worked = _work_after(conn, items, today)
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


def _work_after(conn: sqlite3.Connection, items: list[dict], today: str | None = None) -> set[str]:
    """Items with dated work after their adverse answer, within 90 days of today (gate 2: worked and waiting): a person's
    status change or note in RemitWatch, EMA's claim action on the line, an A/R note about an appeal, call or resubmission."""
    from .runtime import practice_date
    out: set[str] = set()
    t = today_of(today)
    fids = [it["finding_id"] for it in items]
    on = {it["finding_id"]: str(it.get("payment_date") or "")[:10] for it in items}
    if _has_table(conn, "finding_events"):
        for i in range(0, len(fids), 500):
            part = fids[i:i + 500]
            for fid, at in conn.execute(f"""SELECT finding_id, MAX(at) FROM finding_events WHERE actor <> 'system' AND kind = 'manual'
                                            AND finding_id IN ({','.join('?' * len(part))}) GROUP BY finding_id""", part):
                if at and practice_date(str(at)) >= on.get(fid, ""):     # the practice's day of the change, as the answer's
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
                                and (_days(action_on, t) or 0) <= 90:
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
                        if nd and str(nd)[:10] >= on[it["finding_id"]] and (_days(nd, t) or 0) <= 90:
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
KNOWN_RATIOS = (0.85, 0.5, 0.75)        # the non-physician rate, and the multiple-procedure cuts: never a clinician's own rate


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
        what = f"{code or 'whole claims'}{'-' + mod if mod else ''}, reason {carc}{' / ' + rc if rc else ''}"
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
    # the claims a payer denied for enrollment or credentialing: one pass over the reasons and remarks (indexed temp tables,
    # so the compact store, which keeps no index on them, is not rescanned line by line)
    conn.execute("DROP TABLE IF EXISTS temp.sc_cred")
    conn.execute("CREATE TEMP TABLE sc_cred (claim_key TEXT PRIMARY KEY)")
    conn.execute(f"""INSERT OR IGNORE INTO temp.sc_cred SELECT claim_key FROM adjustments WHERE claim_key IS NOT NULL
                     AND reason_code IN ({','.join('?' * len(PROVIDER_CARCS))})""", sorted(PROVIDER_CARCS))
    if _has_table(conn, "remarks"):
        conn.execute(f"""INSERT OR IGNORE INTO temp.sc_cred SELECT claim_key FROM remarks WHERE claim_key IS NOT NULL
                         AND code IN ({','.join('?' * len(PROVIDER_RARCS))})""", sorted(PROVIDER_RARCS))
    hits: dict[tuple, list] = defaultdict(list)
    for npi, name, cfi, charge, lk in conn.execute(
            """SELECT l.rendering_provider_npi, l.payer_name, c.claim_filing_indicator, COALESCE(l.charge_cents, 0), l.line_key
               FROM temp.sc_cred d JOIN claims c ON c.claim_key = d.claim_key JOIN lines l ON l.claim_key = c.claim_key
               WHERE l.payment_date > ? AND l.payment_date <= ? AND COALESCE(l.paid_cents, 0) = 0""", (r0, asof)):
        if npi in new:
            hits[(npi, _group_kind(name, cfi)[0])].append((charge, lk))
    conn.execute("DROP TABLE IF EXISTS temp.sc_cred")
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
    try:
        from .codes import MPPR_CODES as mppr
    except ImportError:
        mppr = frozenset()
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
        ratio = mine / mode_o if mode_o else 1.0
        if code in mppr or any(abs(ratio - r) <= 0.015 for r in KNOWN_RATIOS):
            continue                    # a multiple-procedure cut or the non-physician rate, not this clinician's own rate
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
        if c.get("severity") != "critical":
            continue
        if c.get("status") == "error":
            # a check that cannot run is a guard that is off: said every run until it runs again (never a hold: a check that
            # crashes every run would freeze the worklist for good)
            out.append({"kind": "CHECK_FAILED", "severity": "high", "payer_group": None, "key": ("CHECK_FAILED", c["id"]),
                        "n": None, "cents": None,
                        "title": f"Self-check: the check '{_title_in(c['title'])}' could not run "
                                 f"({(c.get('info') or {}).get('error') or 'an error'}). The guard it gives is off until it runs again: "
                                 "download the diagnostic bundle from the Data tab for the maintainer.",
                        "detail": {"check": c["id"], "info": c.get("info")}})
        elif c.get("status") == "fail" and not safe.get("on"):
            out.append({"kind": "CHECK_FAILED", "severity": "high" if c["id"] in ("money_conservation", "ledger_store", "closure_proof",
                                                                                 "vanished", "posted_money") else "medium",
                        "payer_group": None, "key": ("CHECK_FAILED", c["id"]), "n": c.get("n"), "cents": c.get("cents"),
                        "title": f"Self-check: the check '{_title_in(c['title'])}' failed for {_n(int(c.get('n') or 0), 'item')} "
                                 f"({usd0(c.get('cents'))}). "
                                 f"{c.get('what_to_check') or ''}",
                        "detail": {"check": c["id"], "examples": c.get("examples"), "info": c.get("info")}})
    ceil = ceiling_warning(conn)
    if ceil:
        out.append({"kind": "NEAR_CEILING", "severity": ceil["severity"], "payer_group": None, "key": ("NEAR_CEILING",),
                    "n": ceil["n"], "cents": ceil["cents"], "title": ceil["title"],
                    "detail": {k: ceil[k] for k in ("image_mb", "ceiling_mb", "share")}})
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
                        "title": f"The weekly self-audit found {_n(int(au.get('new_gaps_n') or 0), 'new line')} with money to recover that "
                                 f"no item is working ({usd0(au.get('new_gaps_cents'))}). The Data tab lists them.",
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
    if len(weeks) >= 2 and int(weeks[-1].get("cents") or 0) > int(weeks[-2].get("cents") or 0):     # any rise (decision 9)
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
                 now: float | None = None, keep_days: int = 30, archive_result: dict | None = None, uploads: bool = True) -> dict:
    """The clean-ups of units A1, A2 and E, called from the cycle with what each did: upload pieces left unfinished for a
    day (uploads.sweep), the ledger's and refreshes' work files (ema_ledger.clean_work_files), kept upload copies the
    reports' catalog no longer needs after `keep_days` (only a file whose report was applied and later replaced or
    superseded, or read as 'already'; the sidecar, the record of what happened, stays; never a held or waiting file's, and
    never the newest of a report). It does not compact the archive (VACUUM needs the store's size again; unit E runs
    archive.compact before detection): the cycle passes that result as `archive_result` to record it with the rest. Each
    step reports or records its error; nothing waits for a person. `uploads=False`: the cycle swept the uploads at its
    start already (a cut-off report can hold patient data: it goes first), so they are not swept twice."""
    now = now or time.time()
    out: dict = {"at": now_of(), "mode": mode}
    try:
        from . import uploads as up
        if not uploads:
            out["uploads"] = {"swept_at_start": True}
        elif mode == "serverless" and state is not None:
            out["uploads"] = up.state_uploads(state).sweep(now)
        elif db_path:
            out["uploads"] = up.ServerUploads(db_path).sweep(now)
    except Exception as exc:       # noqa: BLE001  (recorded: the next cycle tries again)
        _caught(exc, "housekeeping:uploads")
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
    if archive_result is not None:
        out["archive"] = archive_result
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
            name = e.get("name") or ""                  # relative to the state folder: intake/done/<pair>.json
            if not name.endswith(".json"):
                continue
            if "/" not in name:
                name = "intake/done/" + name
            ts = e.get("modified") or e.get("server_modified") or e.get("mtime")
            try:
                when = ts if isinstance(ts, (int, float)) else (datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
                                                                if ts else now)
            except ValueError:
                when = now
            if when > cut:
                continue
            try:
                sc = json.loads(state.get(name) or b"{}")
            except ValueError:
                continue
            data = name[:-5]
            if removable(sc) and state.meta(data):
                if state.delete(data):
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
    broken = [c for c in checks if c["status"] == "error"]
    lines.append("Every check passed." if checks and not failing and not broken else
                 (f"{_n(len(failing), 'check')} failing: " + "; ".join(c["title"] for c in failing[:4]) + "." if failing else
                  "The checks have not run yet." if not checks else ""))
    if broken:
        lines.append(f"{_n(len(broken), 'check')} could not run: " + "; ".join(c["title"] for c in broken[:4]) + ".")
    if last.get("kept"):
        lines.append(f"{_n(int(last['kept']), 'item')} the newest analysis no longer lists, with no reason found, kept on the "
                     "worklist until a person accepts the change.")
    la = audit.get("latest") or {}
    if la:
        lines.append(f"Self-audit of {la.get('today')}: {_n(int(la.get('gaps_n') or 0), 'line')} with money and no item "
                     f"({usd0(la.get('gaps_cents'))}); {_n(int(la.get('wasted_n') or 0), 'item')} to work that look settled.")
    if gu.get("cents") is not None:
        lines.append(f"Written off with no decision (2025 on): {usd0(gu.get('cents'))}, of which {usd0(gu.get('recoverable_cents'))} can "
                     "still be appealed.")
    gaps = (money.get("gaps") or {}) if isinstance(money, dict) else {}
    if gaps.get("total_cents"):
        lines.append(f"Money the checks could not tie to the cent: {usd0(gaps['total_cents'])} (the Data tab shows each part).")
    if blind.get("blind"):
        lines.append(blind.get("banner") or "")
    return {"title": "Running on its own", "lines": [x for x in lines if x], "regulation": reg, "safe_mode": safe,
            "last_check": {k: last.get(k) for k in ("run_id", "at", "today", "held", "first", "kept", "version")},
            "checks": checks, "audit": audit, "track": track, "patterns": pats,
            "money": {k: money.get(k) for k in ("at", "ledger_seq", "conservation", "ar_balance", "posted", "gaps")},
            "acceptances": (meta_get(conn, K_ACCEPTED, []) or [])[-10:],
            "given_up": gu, "blind": blind, "housekeeping": house, "rates": rates}


def banner(conn: sqlite3.Connection) -> dict | None:
    """The one line every page shows while RemitWatch is blind or in safe mode (from meta, so the dashboard's copy shows
    it too): {'level': 'blind' | 'safe_mode', 'text'}, or None when there is nothing to say. Never 'all clear' while blind."""
    safe = meta_get(conn, K_SAFE, {}) or {}
    blind = meta_get(conn, K_BLIND, {}) or {}
    parts, level, safe_text = [], None, None
    if safe.get("on"):
        safe_text = "Safe mode: the worklist is the one from before RemitWatch's last run, which it is checking. Nothing was lost."
        if safe.get("new_line"):        # what became of that run's new items, with their dollars, and why
            safe_text += " " + str(safe["new_line"])
        parts.append(safe_text)
        level = "safe_mode"
    if blind.get("blind") and blind.get("banner"):
        parts.append(blind["banner"])
        level = level or "blind"
    # 'safe_mode' on its own: the pages draw it above everything, and the blind-spot bar already says the rest in dollars
    return {"level": level, "text": " ".join(parts), "safe_mode": safe_text} if parts else None


REQUESTS = ("audit", "accept")


def apply_request(conn: sqlite3.Connection, action: dict, *, today: Any = None) -> dict:
    """A person's request from the Data tab, applied by the run (on a serverless host the page journals it; on a server
    the route may call this itself): {'kind': 'selfcheck_request', 'what': 'audit'} runs the self-audit now;
    {'what': 'accept', 'checks': [...], 'user': ..., 'run_id': the run the page showed} accepts exactly what that run found
    (accept_findings)."""
    what = (action or {}).get("what")
    if what == "audit":
        return {"audit": self_audit(conn, today=today, force=True, why="on demand")}
    if what == "accept":
        rid = action.get("run_id")
        if not (isinstance(rid, str) and re.fullmatch(r"run-\d{8}T\d{6}Z(-[0-9a-f]{4,8})?", rid)):
            # an acceptance names the run the page showed: without one it could only take whatever the latest run found
            return {"accepted": {}, "refused": True,
                    "text": "Not accepted: the request did not say which run's findings to accept. Press Accept again on the Data tab."}
        got = accept_findings(conn, str(action.get("user") or "a person"), action.get("checks"), run_id=rid)
        if got.get("stale"):
            # the checks ran again between the page and this run (Vercel's journal): kept where the panel reads it, so
            # the owner who was told "Accepted" learns it was not, and why
            hist = list(meta_get(conn, K_ACCEPTED, []) or [])[-49:]
            hist.append({"at": got.get("at"), "by": got.get("by"), "run_id": rid, "latest": got.get("run_id"), "stale": True,
                         "checks": {c: 0 for c in (action.get("checks") or []) if isinstance(c, str)}})
            meta_set(conn, K_ACCEPTED, hist)
            conn.commit()
        return got
    return {"skipped": f"unknown request {what!r}"}


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
    ceil = ceiling_warning(conn)
    if ceil:
        out.append({"key": "selfcheck_ceiling", "ok": False, "severity": ceil["severity"],
                    "title": f"The store is near what a run can save in one piece ({ceil['share']:.0%})",
                    "detail": ceil["title"], "who": "Whoever looks after the hosting, if this stays for a week."})
    last = meta_get(conn, K_LAST, {}) or {}
    for c in last.get("checks") or []:
        if c.get("status") not in ("fail", "error") or c.get("id") in ("feed_835", "feed_ema"):
            continue
        out.append({"key": f"selfcheck_{c['id']}", "ok": False, "severity": c.get("severity"),
                    "title": c.get("text") or c.get("title"), "detail": c.get("sentence") or "",
                    "who": "Maintainer: " + ("the check could not run; the diagnostic bundle has its error" if c.get("status") == "error"
                                             else (c.get("what_to_check") or "see the diagnostic bundle"))})
    la = (meta_get(conn, K_AUDIT, {}) or {}).get("latest") or {}
    if la.get("gaps_n"):
        out.append({"key": "selfcheck_audit", "ok": False,
                    "title": f"{_n(int(la['gaps_n']), 'line')} with money to recover have no item ({usd0(la.get('gaps_cents'))})",
                    "detail": f"From the self-audit of {la.get('today')}. " + "; ".join(f"{x['category'].replace('_', ' ')}: {x['n']}"
                                                                                     for x in (la.get("gaps_by_category") or [])[:4]),
                    "who": "Maintainer: the audit's gap list is on the Data tab (Running on its own)."})
    return out
