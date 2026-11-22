"""
Results and ROI: what RemitWatch brings back, worked out by fixed rules, every figure with its rule and the exact list of
items behind it (unit R).

THE TOP LINE: "Money returned on claims RemitWatch flagged or worked"
  Every dollar a payer paid on an item's line (or claim, for a claim-level item) after RemitWatch first flagged that item,
  plus the money RemitWatch found. Never money paid before the item was first flagged. One payment counts once: items that
  share a payment (the same line seen by two checks, a claim-level item and a line of the same claim) form one group, and
  the group's money goes to its earliest-raised item. Beneath it, the split, strongest first:
    (a) after RemitWatch work: paid after a person's work on the item in RemitWatch (a fax or appeal sent through
        RemitWatch, a corrected claim or appeal marked Sent, a call or portal check logged, a payment marked posted, a
        patient or second payer billed) and inside that payer's answer time after the work (the 90th percentile from the
        archive, states.response_days, by the kind of work);
    (b) found by RemitWatch: money the practice's own systems missed (a second payer never billed, then billed and paid;
        an 835 payment EMA never posted, then posted; a claim that never left EMA, then sent and paid);
    (c) flagged, no recorded work: paid with no work recorded in RemitWatch (or after the payer's answer time had passed).
        Labelled on the page as money that may have come back anyway, with the measured rate.
  A payment the payer later took back is no longer counted (a reversal pairs with the payment it reverses), and money the
  payer had paid before the flag and took back after it is subtracted (newest money first). What is left over after that
  is shown as "taken back", never hidden.

THE QUALIFIER (the owner's decision of 2026-11-22): "About $Y of this may have come back anyway: similar items with no work
were paid at Z% (n items)." Z is the share of unworked items, in the patterns and age bands of the worked ones, that were
paid; Y = Z x the dollars flagged on the items of bucket (c), at most bucket (c).

LIFT: the share of worked items that brought money against similar unworked items (payer group x code x reason, the same
age band since the flag), with the counts; the incremental dollars are the worked items' money less what the unworked
rate predicts. Under 20 cases on either side the page says "not enough cases yet".

Everything else on the Results tab (work done, outcomes of the open book, hours saved as an estimate, denials by month,
cost and ROI, spot checks) is described where it is worked out below. The daily run calls refresh() on the full store
(both hosts): it keeps a registry of every item RemitWatch ever flagged (results_items; an item that drops off the list
keeps its first-flag date, so money on it still counts), works out the figures into meta results:dashboard and the lists
behind them into meta results:lists, freezes each week's figures once they settle (results_snapshots) and the "before"
on its first run. The pages never work money out on a serverless host's dashboard copy: they read what the run stored.
Counts that rest only on the findings table (items by state, at risk in 14 days, given up without a decision) are counted
live on both hosts, with the same condition as the list they open.

This module writes only its own tables (results_items, results_snapshots, spot_checks) and meta keys under results:; on a
serverless host the page's own records (the owner's figures, the spot checks) live in the state folder under results/
and the run copies them in (merge_state). Money is integer cents, dates ISO strings; the clock is read only through
_today() and _now(), so tests drive it day by day.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import secrets
import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Sequence

VERSION = "1"                               # the attribution rules: every figure and snapshot records it
DASH_KEY = "results:dashboard"              # meta: the figures the run worked out
LISTS_KEY = "results:lists"                 # meta: the items behind each figure (counts equal lists)
SETTINGS_KEY = "results:settings"           # meta: the owner's minutes and costs, with their history
BASELINE_KEY = "results:baseline"           # meta: the "before", frozen on the first run
CLINICIAN_KEY = "results:denials_clinician"  # meta: denial rates by clinician (owner only)
DENIALS_KEY = "results:denials_cache"       # meta: denial rates, worked out again only when the 835s change
SALT_KEY = "results:salt"                   # meta: the spot checks' random seed (no secret: it only spreads the picks)
STATE_SETTINGS = "results/settings.json"    # serverless: the owner's figures as the page saved them
STATE_SPOT = "results/spot_checks_{year}.json"   # serverless: each day's picks and the answers

RETURN_CATEGORIES = ("denial", "bundling", "downcoding", "underpayment")    # items whose line can bring money back
REGISTER_CATEGORIES = RETURN_CATEGORIES + ("fees", "refund")               # items the registry keeps
STATE_CATEGORIES = ("downcoding", "bundling", "denial", "underpayment", "fees", "contract")   # = api.MONEY_CATEGORIES
FOUND_SIGNALS = ("SECONDARY_NOT_BILLED", "REMIT_NOT_POSTED", "NOT_SENT")
OPEN_STATES = ("work_now", "waiting")
CLOSED_STATES = ("done", "accepted", "expired")
GIVEN_UP_FROM = "2025-01-01"                # written off on service from this day on (the owner's "2025+")
AT_RISK_DAYS = 14
SETTLE_DAYS = 14                            # a week's money figures freeze this long after the week ends
MIN_RATE_N = 20                             # fewer cases than this on either side: "not enough cases yet"
MIN_STRATUM = 5                             # a pattern's unworked rate needs this many cases, else a wider pattern
SAME_PAYMENT_DAYS = 45                      # an EMA posting of an 835's amount within this many days is that payment
FACTS_CHUNK = 800                           # items per states.facts_of call (bounds the memory of one call)
SPOT_PER_DAY = 5
SPOT_STRATA = (("work_now", 2), ("waiting", 1), ("done", 1), ("closed_auto", 1))
SPOT_RECENT_DAYS = 30                       # "recently closed": closed automatically within this many days
SPOT_REPEAT_DAYS = 90                       # an item checked within this many days is not picked again
SPOT_ANSWERS = ("agree", "disagree")
SPOT_REASONS = {"already_worked": "Already worked", "already_paid": "Already paid", "not_paid": "Not paid",
                "wrong_amount": "Wrong amount", "wrong_deadline": "Wrong deadline", "other": "Other"}
STRATUM_LABELS = {"work_now": "Work now", "waiting": "Waiting", "done": "Done", "closed_auto": "Closed by RemitWatch"}
STATE_LABELS = {"work_now": "Work now", "waiting": "Waiting", "done": "Done", "accepted": "Accepted", "expired": "Expired",
                "cant_check": "Can't be checked"}
AGE_BANDS = ((30, "0 to 30 days"), (90, "31 to 90 days"), (180, "91 to 180 days"), (None, "over 180 days"))
BUCKETS = ("a", "b", "c")
BUCKET_LABELS = {"a": "After RemitWatch work", "b": "Found by RemitWatch", "c": "Flagged, no recorded work"}

# What counts as a person's work on an item in RemitWatch, and how long the payer is given to answer each kind
# (states.response_days: the payer's 90th percentile from the archive, at least its appeal decision time for an appeal)
WORK_KINDS = {"fax": "Fax or appeal sent through RemitWatch", "sent": "Corrected claim or appeal marked sent",
              "call": "Call or portal check logged", "posted": "Payment marked posted", "billed": "Patient or second payer billed",
              "fix": "Fix recorded (credentialing, authorization, coding)"}
EVENT_WORK = {"sent": "sent", "resubmitted": "sent", "corrected": "sent", "appeal": "sent", "mark_sent": "sent",
              "call": "call", "portal": "call", "outcome": "call", "log_outcome": "call",
              "posted": "posted", "mark_posted": "posted", "billed": "billed", "mark_billed": "billed", "fix": "fix"}
WAIT_OF = {"fax": "fax", "sent": "sent", "call": "call", "posted": "call", "billed": "secondary", "fix": "corrected"}
SYSTEM_ACTORS = frozenset({"system", "hide rule", "packets", "fax", "remitwatch"})
# a fix noted on an item (a note on a pattern item that says what was fixed): the marker on the denial-rate chart
FIX_RE = re.compile(r"\b(credential\w*|enrol\w*|re-?enrol\w*|authori[sz]\w*|prior auth|retro-?auth|coding|modifier|"
                    r"fixed|fix(es)? (made|in place|applied)|front desk (told|trained))\b", re.I)
FIX_KINDS = (("credentialing", re.compile(r"credential|enrol", re.I)), ("authorization", re.compile(r"auth", re.I)),
             ("coding", re.compile(r"coding|modifier", re.I)))

DEFAULT_SETTINGS = {
    # minutes a person would spend by hand on each task RemitWatch did (the owner edits each; the history keeps them)
    "minutes": {"investigate": 12, "packet": 20, "fax": 10, "duplicate": 5},
    # monthly costs in dollars, and the cost of one fax page and one AI call
    "costs": {"hosting_month": 0.0, "other_month": 0.0, "fax_page": 0.0, "ai_call": 0.0},
}
MINUTE_LABELS = {"investigate": "Items RemitWatch proved settled or closed by itself (minutes to investigate one by hand)",
                 "packet": "Appeal packets built (minutes saved per packet)",
                 "fax": "Faxes sent and followed up by RemitWatch (minutes saved per fax)",
                 "duplicate": "Repeat listings folded into one item (minutes saved per item)"}
COST_LABELS = {"hosting_month": "Hosting per month", "other_month": "Other costs per month", "fax_page": "Cost of one fax page",
               "ai_call": "Cost of one AI call"}
SETTINGS_HISTORY = 100

FINDING_FIELDS = ("finding_id", "signal", "category", "status", "stale", "line_key", "claim_key", "transaction_key",
                  "patient_control_number", "service_date", "payment_date", "procedure_code", "original_procedure_code",
                  "billed_code", "ema_link", "payer_group", "payer_kind", "carc", "shortfall_cents", "expected_cents", "first_seen",
                  "work_state", "appeal_deadline", "legal_deadline", "evidence", "auto_note", "recovered_cents", "recovered_date",
                  "status_changed_at", "state_basis", "ema_outcome", "written_off_cents", "written_off_on", "notes")
ITEM_KEYS = ("finding_id", "signal", "category", "status", "line_key", "claim_key", "transaction_key", "patient_control_number",
             "service_date", "payment_date", "procedure_code", "original_procedure_code", "billed_code", "ema_link", "payer_group",
             "payer_kind", "carc")
EVIDENCE_KEYS = ("unpaid", "lines", "codes", "ema_link", "ema_links")

SCHEMA = """
CREATE TABLE IF NOT EXISTS results_items (
    finding_id TEXT PRIMARY KEY, first_flagged TEXT, last_seen TEXT, gone_on TEXT, ever_open TEXT,
    signal TEXT, category TEXT, payer_group TEXT, payer_kind TEXT, procedure_code TEXT, carc TEXT, service_date TEXT,
    flagged_cents INTEGER, shortfall_cents INTEGER, last_state TEXT, last_status TEXT,
    expired_on TEXT, lost_on TEXT, lost_cents INTEGER, flag_info TEXT, item TEXT);
CREATE TABLE IF NOT EXISTS results_snapshots (week TEXT PRIMARY KEY, at TEXT, final INTEGER DEFAULT 0, version TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS spot_checks (
    day TEXT, finding_id TEXT, stratum TEXT, picked_at TEXT, state TEXT, signal TEXT, payer_group TEXT, cents INTEGER,
    basis TEXT, answer TEXT, reason TEXT, note TEXT, who TEXT, answered_at TEXT, PRIMARY KEY (day, finding_id));
"""


class ResultsError(ValueError):
    """A request the Results figures refuse, in words a person can act on (the route answers it with a 4xx)."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message, self.status = message, status


# --------------------------------------------------------------------------------------------------------- helpers
def _today(today: Any = None) -> str:
    """The day the figures are for: the run's day when it passes one, else today in UTC (the one place the clock is read
    for a date)."""
    if today:
        return str(today)[:10]
    return datetime.now(timezone.utc).date().isoformat()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _d(iso: Any) -> date | None:
    try:
        return date.fromisoformat(str(iso)[:10])
    except (TypeError, ValueError):
        return None


def _days(a: Any, b: Any) -> int | None:
    x, y = _d(a), _d(b)
    return (y - x).days if x and y else None


def _add(iso: Any, n: int) -> str | None:
    x = _d(iso)
    return (x + timedelta(days=n)).isoformat() if x else None


def week_of(iso: Any) -> str | None:
    """The Monday of an ISO date's week."""
    x = _d(iso)
    return (x - timedelta(days=x.weekday())).isoformat() if x else None


def usd(cents: Any) -> str:
    c = int(cents or 0)
    return f"-${abs(c) / 100:,.2f}" if c < 0 else f"${c / 100:,.2f}"


def _table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def _meta(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    if not _table(conn, "meta"):
        return default
    r = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    if not r or r[0] is None:
        return default
    try:
        return json.loads(r[0])
    except ValueError:
        return default


def _set_meta(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, json.dumps(value, separators=(",", ":"), default=str)))


def _json(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        out = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return out if isinstance(out, dict) else {}


def _chunks(seq: Sequence, n: int = 500):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _temp_ids(conn: sqlite3.Connection, name: str, ids: Iterable[str]) -> None:
    conn.execute(f"DROP TABLE IF EXISTS temp.{name}")
    conn.execute(f"CREATE TEMP TABLE {name} (id TEXT PRIMARY KEY)")
    conn.executemany(f"INSERT OR IGNORE INTO temp.{name} VALUES (?)", [(x,) for x in ids])


def _drop(conn: sqlite3.Connection, *names: str) -> None:
    for n in names:
        conn.execute(f"DROP TABLE IF EXISTS temp.{n}")


def _pct(k: int, n: int) -> float | None:
    return round(k / n, 4) if n else None


def _median(vals: list) -> float | None:
    v = sorted(x for x in vals if x is not None)
    if not v:
        return None
    m = len(v) // 2
    return float(v[m]) if len(v) % 2 else (v[m - 1] + v[m]) / 2


def _labels() -> dict:
    try:
        from .insights import SHORT_LABELS
        return SHORT_LABELS
    except ImportError:         # a build without the labels still shows the signal's own name
        return {}


def ensure_schema(conn: sqlite3.Connection) -> None:
    """This module's own tables, made where missing (safe on every call)."""
    conn.executescript(SCHEMA)


# ---------------------------------------------------------------------------------------------------- the settings
def _clean_settings(raw: Any) -> dict:
    """The owner's figures with every value checked: minutes 0 to 600, dollars 0 to 1,000,000, anything else back to its
    default. Unknown keys are dropped."""
    out = json.loads(json.dumps(DEFAULT_SETTINGS))
    src = raw if isinstance(raw, dict) else {}
    for group, lo, hi in (("minutes", 0, 600), ("costs", 0, 1_000_000)):
        got = src.get(group) if isinstance(src.get(group), dict) else {}
        for k in out[group]:
            v = got.get(k)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)) or not lo <= float(v) <= hi:
                continue
            out[group][k] = round(float(v), 4) if group == "costs" else round(float(v), 2)
    return out


def settings_problem(values: Any) -> str | None:
    """Why figures sent by the page cannot be saved, in plain words, or None."""
    if not isinstance(values, dict):
        return "Send the figures as an object."
    for group, lo, hi, unit in (("minutes", 0, 600, "minutes"), ("costs", 0, 1_000_000, "dollars")):
        got = values.get(group)
        if got is None:
            continue
        if not isinstance(got, dict):
            return f"The {group} must be a list of named figures."
        for k, v in got.items():
            if k not in DEFAULT_SETTINGS[group]:
                return f"There is no figure called {k}."
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
                return f"{k} must be a number."
            if not lo <= float(v) <= hi:
                return f"{k} must be between {lo} and {hi:,} {unit}."
    return None


def settings_record(raw: Any) -> dict:
    """{'values', 'history', 'version'}: the saved figures (defaults where none were saved), every earlier version (who,
    when, the values in force), and a version string a save names to refuse a stale one."""
    rec = raw if isinstance(raw, dict) else {}
    values = _clean_settings(rec.get("values"))
    hist = [h for h in (rec.get("history") or []) if isinstance(h, dict)][-SETTINGS_HISTORY:]
    version = hashlib.sha256(json.dumps(rec.get("values") or {}, sort_keys=True).encode()).hexdigest()[:16] if rec.get("values") else ""
    return {"values": values, "history": hist, "version": version, "at": rec.get("at"), "by": rec.get("by"),
            "saved": bool(rec.get("values"))}


def _apply_settings(rec: dict, values: dict, who: str, at: str) -> dict:
    merged = _clean_settings({g: dict((rec.get("values") or {}).get(g) or {}, **(values.get(g) or {})) for g in ("minutes", "costs")})
    hist = [h for h in (rec.get("history") or []) if isinstance(h, dict)]
    hist.append({"at": at, "by": who, "values": merged})
    return {"values": merged, "history": hist[-SETTINGS_HISTORY:], "at": at, "by": who}


def read_settings(conn: sqlite3.Connection | None = None, state: Any = None) -> dict:
    """The owner's figures: on a serverless host the state folder's copy first (what the page saved last), else what the
    run copied into meta, else the defaults."""
    if state is not None:
        raw = state.get(STATE_SETTINGS)
        if raw:
            return settings_record(_json(raw.decode() if isinstance(raw, bytes) else raw))
    return settings_record(_meta(conn, SETTINGS_KEY, {}) if conn is not None else {})


def save_settings(values: dict, *, who: str, base_version: str | None, conn: sqlite3.Connection | None = None,
                  state: Any = None) -> dict:
    """Save the owner's figures (a server's store, or a serverless host's state folder), refusing a save made from an older
    version than the one now saved (ResultsError 409, the page reads them again). Returns the new record."""
    problem = settings_problem(values)
    if problem:
        raise ResultsError(problem)
    who = (str(who or "").strip() or "someone")[:80]
    at = _now()
    if state is not None:
        from .state import Conflict
        meta = state.meta(STATE_SETTINGS)
        raw = state.get(STATE_SETTINGS) if meta else None
        cur = _json(raw.decode() if isinstance(raw, bytes) else raw) if raw else {}
        if (base_version or "") != settings_record(cur)["version"]:
            raise ResultsError("These figures were changed in another window since this one read them. Read them again and save once more.", 409)
        new = _apply_settings(cur, values, who, at)
        blob = json.dumps(new, separators=(",", ":")).encode()
        try:
            state.put(STATE_SETTINGS, blob, mode="update", rev=meta["rev"]) if meta else state.put(STATE_SETTINGS, blob, mode="add")
        except Conflict:
            raise ResultsError("These figures were changed in another window since this one read them. Read them again and save once more.", 409)
        return settings_record(new)
    conn.execute("BEGIN IMMEDIATE")
    try:
        cur = _meta(conn, SETTINGS_KEY, {}) or {}
        if (base_version or "") != settings_record(cur)["version"]:
            raise ResultsError("These figures were changed in another window since this one read them. Read them again and save once more.", 409)
        new = _apply_settings(cur, values, who, at)
        _set_meta(conn, SETTINGS_KEY, new)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return settings_record(new)


# ------------------------------------------------------------------------------------------------- the registry
def _deadline_of(f: dict) -> str | None:
    """The last day an item could still be worked: the payer's own limit when it is later than the safe date."""
    vals = [str(v)[:10] for v in (f.get("appeal_deadline"), f.get("legal_deadline")) if v]
    return max(vals) if vals else None


def _item_json(f: dict) -> str:
    """What states.facts_of needs to find an item's payments again after it drops off the list (no names)."""
    out = {k: f.get(k) for k in ITEM_KEYS if f.get(k) not in (None, "")}
    ev = _json(f.get("evidence"))
    sub = {k: ev[k] for k in EVIDENCE_KEYS if k in ev}
    if sub:
        out["evidence"] = json.dumps(sub, separators=(",", ":"), default=str)
    return json.dumps(out, separators=(",", ":"), default=str)


def _flag_info(f: dict) -> str:
    """Facts kept as they were when the item was first registered (an unposted payment: what EMA had posted then)."""
    ev = _json(f.get("evidence"))
    keep = {k: ev.get(k) for k in ("ema_posted_cents", "claim_paid_cents", "deposit_cents") if ev.get(k) is not None}
    return json.dumps(keep, separators=(",", ":"))


def update_registry(conn: sqlite3.Connection, today: Any = None) -> dict:
    """Keep the registry of every item RemitWatch ever flagged up to date with the findings just saved: a new item is
    written with its first-flag date (the item's own first_seen); a listed one keeps the earliest first-flag date ever
    seen; one no longer listed is marked gone (it stays, so money that comes on it later still counts); an item that went
    from Work now to Expired is recorded as lost to its deadline, with its dollars. Returns the counts."""
    ensure_schema(conn)
    t = _today(today)
    have: dict[str, tuple] = {}
    for r in conn.execute("""SELECT finding_id, first_flagged, last_state, expired_on, lost_on, lost_cents, ever_open, flagged_cents,
                                    gone_on, flag_info FROM results_items"""):
        have[r[0]] = r
    if not _table(conn, "findings"):
        return {"listed": 0, "new": 0, "gone": 0, "lost": 0}
    fc = _cols(conn, "findings")
    sel = [c for c in FINDING_FIELDS if c in fc]
    cats = ",".join("?" * len(REGISTER_CATEGORIES))
    rows, seen = [], set()
    n_new = n_lost = 0
    for r in conn.execute(f"SELECT {', '.join(sel)} FROM findings WHERE category IN ({cats})", REGISTER_CATEGORIES):
        f = dict(zip(sel, r))
        fid = f["finding_id"]
        seen.add(fid)
        first = str(f.get("first_seen") or "")[:10] or t
        ws = f.get("work_state")
        prev = have.get(fid)
        if prev is None:
            n_new += 1
            ever_open = first if ws in OPEN_STATES else None
            expired_on = lost_on = lost_cents = None
            flagged = f.get("shortfall_cents")
            info = _flag_info(f)
        else:
            first = min(prev[1] or first, first)
            ever_open = prev[6] or (t if ws in OPEN_STATES else None)
            expired_on, lost_on, lost_cents = prev[3], prev[4], prev[5]
            flagged = prev[7] if prev[7] is not None else f.get("shortfall_cents")
            info = prev[9] or _flag_info(f)
            if prev[2] == "work_now" and ws == "expired" and not expired_on:
                expired_on, lost_on, lost_cents = t, _deadline_of(f) or t, int(f.get("shortfall_cents") or 0)
                n_lost += 1
        rows.append((fid, first, t, None, ever_open, f.get("signal"), f.get("category"), f.get("payer_group"), f.get("payer_kind"),
                     f.get("procedure_code"), f.get("carc"), f.get("service_date"), flagged, f.get("shortfall_cents"), ws,
                     f.get("status"), expired_on, lost_on, lost_cents, info, _item_json(f)))
    conn.executemany("""INSERT OR REPLACE INTO results_items (finding_id, first_flagged, last_seen, gone_on, ever_open, signal, category,
                        payer_group, payer_kind, procedure_code, carc, service_date, flagged_cents, shortfall_cents, last_state,
                        last_status, expired_on, lost_on, lost_cents, flag_info, item) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     rows)
    gone = [fid for fid, r in have.items() if fid not in seen and not r[8]]
    conn.executemany("UPDATE results_items SET gone_on=? WHERE finding_id=?", [(t, fid) for fid in gone])
    return {"listed": len(seen), "new": n_new, "gone": len(gone), "lost": n_lost}


def load_registry(conn: sqlite3.Connection) -> dict[str, dict]:
    """Every registered item: {finding_id: {..., 'F' (first-flag day), 'item' (the facts_of dict), 'returnable'}}."""
    if not _table(conn, "results_items"):
        return {}
    cols = [r[1] for r in conn.execute("PRAGMA table_info(results_items)")]
    out = {}
    for r in conn.execute("SELECT * FROM results_items"):
        d = dict(zip(cols, r))
        d["F"] = d.get("first_flagged") or ""
        d["item"] = _json(d.get("item"))
        d["item"]["finding_id"] = d["finding_id"]
        d["flag_info"] = _json(d.get("flag_info"))
        d["returnable"] = d.get("category") in RETURN_CATEGORIES or d.get("signal") == "REMIT_NOT_POSTED"
        out[d["finding_id"]] = d
    return out


# --------------------------------------------------------------------------------------------- the work on items
def _work_kind(kind: Any, to_status: Any, actor: Any) -> str | None:
    """The kind of a person's work an item history line records, or None (an automatic line, a note, a packet)."""
    if kind == "manual":
        return "sent" if to_status == "appealed" else None
    if str(actor or "").strip().lower() in SYSTEM_ACTORS or not str(actor or "").strip():
        return None
    return EVENT_WORK.get(str(kind or ""))


def work_of(conn: sqlite3.Connection, fids: Iterable[str]) -> dict[str, list[dict]]:
    """Each item's work in RemitWatch, oldest first: {'date', 'at', 'kind', 'who', 'pages'}. A person's status Sent, the
    work lines the item's buttons record (sent, call, posted, billed, fix) and every fax sent through RemitWatch (a fax
    counts once it went out: status 'sent')."""
    ids = sorted(set(fids))
    out: dict[str, list[dict]] = {fid: [] for fid in ids}
    if not ids:
        return out
    if _table(conn, "finding_events"):
        for part in _chunks(ids):
            _temp_ids(conn, "rs_w", part)
            for fid, at, kind, to, actor in conn.execute("""SELECT e.finding_id, e.at, e.kind, e.to_status, e.actor FROM finding_events e
                                                            WHERE e.finding_id IN (SELECT id FROM temp.rs_w) ORDER BY e.at, e.id"""):
                k = _work_kind(kind, to, actor)
                if k and at:
                    out[fid].append({"date": str(at)[:10], "at": str(at), "kind": k, "who": str(actor or "").strip()[:80] or "a person",
                                     "pages": 0})
        _drop(conn, "rs_w")
    if _table(conn, "faxes") and {"finding_id", "direction", "status"} <= _cols(conn, "faxes"):
        for part in _chunks(ids):
            _temp_ids(conn, "rs_fx", part)
            for fid, status, sent_at, created_at, pages, doc in conn.execute(
                    """SELECT x.finding_id, x.status, x.sent_at, x.created_at, x.pages, x.doc FROM faxes x
                       WHERE x.direction='out' AND x.finding_id IN (SELECT id FROM temp.rs_fx)"""):
                when = str(sent_at or created_at or "")
                if status != "sent" or not when:
                    continue
                who = str(_json(doc).get("actor") or "").strip()[:80] or "RemitWatch"
                out[fid].append({"date": when[:10], "at": when, "kind": "fax", "who": who, "pages": int(pages or 0)})
        _drop(conn, "rs_fx")
    for v in out.values():
        v.sort(key=lambda w: (w["at"], w["kind"]))
    return out


# ------------------------------------------------------------------------------------------------ money on a line
def _newest_data(conn: sqlite3.Connection) -> str:
    """The newest day any payment record reaches (835 remittances, EMA's postings and checks)."""
    vals = []
    if _table(conn, "transactions"):
        sup = "COALESCE(superseded,0)=0" if "superseded" in _cols(conn, "transactions") else "1=1"
        vals.append(conn.execute(f"SELECT MAX(payment_date) FROM transactions WHERE {sup}").fetchone()[0])
    if _table(conn, "ema_line_payments"):
        vals.append(conn.execute("SELECT MAX(posted) FROM ema_line_payments").fetchone()[0])
    if _table(conn, "ema_checks"):
        vals.append(conn.execute("SELECT MAX(last_posted) FROM ema_checks").fetchone()[0])
    return max((str(v)[:10] for v in vals if v), default="")


def _candidates(conn: sqlite3.Connection, items: dict[str, dict]) -> set[str]:
    """The items whose bill shows an 835 answer, or whose EMA lines show a posting, dated after the item was first
    flagged: only these can have money after the flag (the rest are not gathered at all)."""
    from .states import bill_of
    out: set[str] = set()
    rows = []
    for fid, r in items.items():
        it = r["item"]
        b = bill_of(it.get("patient_control_number"))
        if b and r["F"]:
            rows.append((fid, b, b + "~", r["F"]))
    if not rows:
        return out
    has_tx = _table(conn, "claims") and _table(conn, "transactions")
    sup = "COALESCE(t.superseded,0)=0" if "superseded" in _cols(conn, "transactions") else "1=1"
    has_map = _table(conn, "ema_link_map") and _table(conn, "ema_line_payments")
    mcols = _cols(conn, "ema_link_map")
    fac = "COALESCE(m.facility,0)=0" if "facility" in mcols else "1=1"
    for part in _chunks(rows):
        conn.execute("DROP TABLE IF EXISTS temp.rs_c")
        conn.execute("CREATE TEMP TABLE rs_c (fid TEXT PRIMARY KEY, bill TEXT, hi TEXT, f TEXT)")
        conn.executemany("INSERT OR IGNORE INTO temp.rs_c VALUES (?,?,?,?)", part)
        if has_tx:
            out.update(r[0] for r in conn.execute(f"""SELECT DISTINCT r.fid FROM temp.rs_c r
                    JOIN claims c ON c.patient_control_number >= r.bill AND c.patient_control_number < r.hi
                    JOIN transactions t ON t.transaction_key = c.transaction_key
                    WHERE {sup} AND t.payment_date > r.f"""))
        if has_map:
            out.update(r[0] for r in conn.execute(f"""SELECT DISTINCT r.fid FROM temp.rs_c r
                    JOIN ema_link_map m ON m.bill = r.bill AND COALESCE(m.conflict,0)=0 AND {fac}
                    JOIN ema_line_payments y ON y.link = m.link AND y.posted > r.f"""))
    _drop(conn, "rs_c")
    if _table(conn, "ema_line_payments"):
        linked = []
        for fid, r in items.items():
            it = r["item"]
            ev = _json(it.get("evidence"))
            for v in [it.get("ema_link"), ev.get("ema_link")] + list(ev.get("ema_links") or []):
                try:
                    if v is not None and r["F"]:
                        linked.append((fid, int(v), r["F"]))
                except (TypeError, ValueError):
                    continue
        for part in _chunks(linked):
            conn.execute("DROP TABLE IF EXISTS temp.rs_l")
            conn.execute("CREATE TEMP TABLE rs_l (fid TEXT, link INTEGER, f TEXT)")
            conn.executemany("INSERT INTO temp.rs_l VALUES (?,?,?)", part)
            out.update(r[0] for r in conn.execute("""SELECT DISTINCT r.fid FROM temp.rs_l r JOIN ema_line_payments y
                                                     ON y.link = r.link AND y.posted > r.f"""))
        _drop(conn, "rs_l")
    return out


def _facts(conn: sqlite3.Connection, items: list[dict], today: str, errors: list) -> dict[str, dict]:
    """The 835 adjudications (reversals paired) and EMA's postings of each item's services, from states.facts_of: the
    same rows the item's story shows."""
    from . import states
    out: dict[str, dict] = {}
    ctx = None
    for part in _chunks(items, FACTS_CHUNK):
        try:
            got = states.facts_of(conn, part, outcomes={}, today=today, context=ctx)
        except Exception as exc:        # recorded on the figures (errors), and the items stay without money: never a guess
            errors.append(f"facts_of: {type(exc).__name__}: {str(exc)[:200]}")
            continue
        for fid, fx in got.items():
            out[fid] = {"rows": (fx.get("r835") or {}).get("rows") or [], "pays": (fx.get("ema") or {}).get("pays") or []}
            if ctx is None:
                ctx = fx.get("ctx")
        del got
    return out


def _row_id(x: dict) -> tuple:
    return ("835", x.get("line_key") or x.get("claim_key"))


def _pay_id(p: dict) -> tuple:
    return ("ema", p.get("link"), p.get("svc"), p.get("cpt"), p.get("check"), p.get("posted"), p.get("payer"))


def _amount(x: dict) -> int:
    v = x.get("paid") if x.get("line_key") else x.get("claim_paid")
    return int(v or 0)


def _line_words(x: dict) -> str:
    code = x.get("code") or x.get("cpt") or ""
    svc = x.get("svc") or ""
    return (f" for {code}" if code else "") + (f" of {svc}" if svc else "")


def _group_events(rows: dict, pays: dict, F: str, own_tk: Any, own_ck: Any) -> list[dict]:
    """The money on one group's lines after its first flag F: standing 835 payments dated after F (not the remittance
    that raised the earliest item), less the 835 reversals after F of payments made on or before F; and EMA's insurer
    postings after F that no 835 carries (a payment an 835 also carries, the same amount within SAME_PAYMENT_DAYS, is that
    payment)."""
    ev: list[dict] = []
    by_key = {(x.get("line_key") or x.get("claim_key")): x for x in rows.values()}
    standing_835 = []
    for rid, x in rows.items():
        if x.get("status") != "22" and not x.get("reversed_by") and _amount(x) > 0 and x.get("date"):
            standing_835.append(x)
    for rid, x in sorted(rows.items(), key=lambda kv: (str(kv[1].get("date") or ""), str(kv[0]))):
        if (own_tk and x.get("tk") == own_tk) or (own_ck and x.get("claim_key") == own_ck):
            continue
        d = str(x.get("date") or "")[:10]
        if not d or d <= F:
            continue
        if x.get("status") == "22":
            o = by_key.get(x.get("reverses")) if x.get("reverses") else None
            if o is None:
                continue
            od = str(o.get("date") or "")[:10]
            amt = _amount(x)
            took = amt if amt < 0 else -_amount(o)
            if od and od <= F and took < 0:
                ev.append({"id": ("835r", rid[1]), "date": d, "cents": took, "kind": "takeback", "source": "835",
                           "payer": x.get("payer") or "",
                           "text": f"{x.get('payer') or 'The payer'} took back {usd(-took)}{_line_words(x)} on {d} (paid before the flag)"})
            continue
        if x.get("reversed_by"):
            continue
        amt = _amount(x)
        if amt > 0:
            ev.append({"id": rid, "date": d, "cents": amt, "kind": "payment", "source": "835", "payer": x.get("payer") or "",
                       "text": f"{x.get('payer') or 'The payer'} paid {usd(amt)}{_line_words(x)} on {d} (835 remittance)"})
    for pid, p in sorted(pays.items(), key=lambda kv: (str(kv[1].get("posted") or ""), str(kv[0]))):
        posted = str(p.get("posted") or "")[:10]
        cents = int(p.get("cents") or 0)
        if not posted or posted <= F or p.get("in_835") or not cents:
            continue
        if cents > 0 and any(_amount(x) == cents and abs(_days(x.get("date"), posted) or 999) <= SAME_PAYMENT_DAYS for x in standing_835):
            continue
        who = p.get("payer") or p.get("group") or "the payer"
        ev.append({"id": pid, "date": posted, "cents": cents, "kind": "payment" if cents > 0 else "takeback", "source": "production",
                   "payer": who, "text": (f"EMA posted {usd(cents)} from {who}{_line_words(p)} on {posted} (a check no 835 carries)" if cents > 0
                                          else f"EMA posted a takeback of {usd(-cents)} from {who}{_line_words(p)} on {posted}")})
    ev.sort(key=lambda e: (e["date"], str(e["id"])))
    return ev


def _found_postings(conn: sqlite3.Connection, items: dict[str, dict]) -> dict[str, list[dict]]:
    """An 835 payment EMA had not posted when RemitWatch flagged it, posted since: EMA's checks matched to that 835
    (ema_checks.trace_key = the item's payment), the amount posted beyond what EMA had posted when the item was first
    flagged, at most what was unposted then, dated by EMA's newest posting of the check (after the flag)."""
    out: dict[str, list[dict]] = {}
    rnp = {fid: r for fid, r in items.items() if r.get("signal") == "REMIT_NOT_POSTED" and r["item"].get("transaction_key")}
    if not rnp or not _table(conn, "ema_checks") or "trace_key" not in _cols(conn, "ema_checks"):
        return out
    by_tk: dict[str, list] = defaultdict(list)
    for fid, r in rnp.items():
        by_tk[r["item"]["transaction_key"]].append(fid)
    got: dict[str, tuple] = {}
    for part in _chunks(sorted(by_tk)):
        _temp_ids(conn, "rs_tk", part)
        for tk, last, cents in conn.execute("""SELECT trace_key, MAX(last_posted), SUM(ins_paid) FROM ema_checks
                                               WHERE trace_key IN (SELECT id FROM temp.rs_tk) GROUP BY trace_key"""):
            got[tk] = (str(last or "")[:10], int(cents or 0))
    _drop(conn, "rs_tk")
    for tk, fids in by_tk.items():
        if tk not in got:
            continue
        last, now_cents = got[tk]
        for fid in fids:
            r = rnp[fid]
            before = int((r.get("flag_info") or {}).get("ema_posted_cents") or 0)
            cap = int(r.get("flagged_cents") or 0)
            found = min(cap, max(0, now_cents - before)) if cap > 0 else 0
            if found > 0 and last and last > r["F"]:
                out[fid] = [{"id": ("posted", tk), "date": last, "cents": found, "kind": "posting", "source": "production",
                             "payer": r.get("payer_group") or "",
                             "text": f"EMA posted {usd(found)} of this 835 payment by {last}, after RemitWatch found it unposted"}]
    return out


def _window(conn: sqlite3.Connection, group: Any, kind_of_payer: Any, work: str, cache: dict) -> int:
    key = (group, kind_of_payer, work)
    if key not in cache:
        from . import states
        cache[key] = int(states.response_days(conn, group, work=WAIT_OF.get(work, "claim"), payer_kind=kind_of_payer))
    return cache[key]


def _bucket(conn: sqlite3.Connection, ev_date: str, works: list[dict], signals: set, group: Any, pkind: Any,
            cache: dict) -> tuple[str, dict | None, int | None]:
    """(a) when a person's work in RemitWatch came before the payment and the payment came inside the payer's answer time
    for that kind of work (the most recent such work is credited); else (b) when the item is money the practice's own
    systems missed; else (c)."""
    for w in sorted(works, key=lambda w: w["at"], reverse=True):
        if w["date"] >= ev_date:
            continue
        win = _window(conn, group, pkind, w["kind"], cache)
        if (_days(w["date"], ev_date) or 0) <= win:
            return "a", w, win
    if signals & set(FOUND_SIGNALS):
        return "b", None, None
    return "c", None, None


def _rule_text(bucket: str, ev: dict, credit: dict | None, win: int | None, F: str, signals: set) -> str:
    if bucket == "a" and credit:
        return (f"{ev['text']}: {_days(credit['date'], ev['date'])} days after the {WORK_KINDS[credit['kind']].lower()} of "
                f"{credit['date']} by {credit['who']}, inside the payer's {win}-day answer time")
    if bucket == "b":
        what = ("a second payer nobody had billed" if "SECONDARY_NOT_BILLED" in signals else
                "an 835 payment EMA had not posted" if "REMIT_NOT_POSTED" in signals else "a claim that had never left EMA")
        return f"{ev['text']}: money on {what}, found by RemitWatch on {F}"
    return f"{ev['text']}: after RemitWatch flagged it on {F}, with no work recorded in RemitWatch inside the payer's answer time"


def attribute(conn: sqlite3.Connection, today: Any = None, *, registry: dict | None = None, errors: list | None = None) -> dict:
    """The top line, item by item. Returns {'items': {winner finding id: {'a', 'b', 'c', 'returned', 'taken_back', 'events',
    'members', 'F'}}, 'groups': {finding id: winner}, 'work': {finding id: [...]}, 'denied_again': {finding id: date},
    'answered': {finding id: 'win'|'loss'}, 'candidates': n, 'newest': day, 'errors': [...]}."""
    t = _today(today)
    errors = errors if errors is not None else []
    reg = registry if registry is not None else load_registry(conn)
    items = {fid: r for fid, r in reg.items() if r["returnable"] and r["F"]}
    newest = _newest_data(conn)
    work = work_of(conn, reg.keys())
    cands = sorted(_candidates(conn, items))
    facts = _facts(conn, [items[f]["item"] for f in cands], t, errors)
    # one group per set of items that share a payment record (union by the records' identities)
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    first_of: dict[tuple, str] = {}
    for fid in cands:
        find(fid)
        fx = facts.get(fid) or {}
        for x in fx.get("rows", []):
            union(fid, first_of.setdefault(_row_id(x), fid))
        for p in fx.get("pays", []):
            union(fid, first_of.setdefault(_pay_id(p), fid))
    members: dict[str, list] = defaultdict(list)
    for fid in cands:
        members[find(fid)].append(fid)
    found = _found_postings(conn, items)
    out_items: dict[str, dict] = {}
    group_of: dict[str, str] = {}
    group_events: dict[str, list] = {}
    group_members: dict[str, list] = {}
    cache: dict = {}
    denied_again: dict[str, str] = {}
    answered: dict[str, str] = {}

    def settle(winner: str, mem: list, evs: list[dict]) -> None:
        r = items.get(winner) or reg[winner]
        signals = {reg[m].get("signal") for m in mem if m in reg}
        works = [w for m in mem for w in work.get(m, [])]
        stack: list[list] = []
        took = 0
        kept: list[dict] = []
        # on one day, money in before money out: a reprocessing reverses the old payment and pays the new one together
        for e in sorted(evs, key=lambda e: (e["date"], e["cents"] < 0, str(e["id"]))):
            if e["cents"] > 0:
                b, credit, win = _bucket(conn, e["date"], works, signals, r.get("payer_group"), r.get("payer_kind"), cache)
                rec = dict(e, bucket=b, rule=_rule_text(b, e, credit, win, r["F"], signals),
                           work_kind=credit["kind"] if credit else None, work_who=credit["who"] if credit else None,
                           work_date=credit["date"] if credit else None)
                rec.pop("id", None)
                kept.append(rec)
                stack.append([rec, e["cents"]])
            else:
                need = -e["cents"]
                while need > 0 and stack:
                    top = stack[-1]
                    use = min(need, top[1])
                    top[1] -= use
                    need -= use
                    if top[1] == 0:
                        stack.pop()
                took += need
                rec = dict(e, bucket=None, rule=e["text"])
                rec.pop("id", None)
                kept.append(rec)
        left = {id(rec): cents for rec, cents in stack}
        split = {b: 0 for b in BUCKETS}
        for rec in kept:
            if rec["cents"] > 0:
                rec["counted_cents"] = left.get(id(rec), 0)
                split[rec["bucket"]] += rec["counted_cents"]
        total = sum(split.values())
        out_items[winner] = dict(split, returned=total, taken_back=took, events=kept[:12], n_events=len(kept), members=sorted(mem),
                                 F=r["F"])

    for root, mem in members.items():
        winner = min(mem, key=lambda m: (items[m]["F"], m))
        F = items[winner]["F"]
        rows: dict = {}
        pays: dict = {}
        for m in mem:
            group_of[m] = winner
            fx = facts.get(m) or {}
            for x in fx.get("rows", []):
                rows.setdefault(_row_id(x), x)
            for p in fx.get("pays", []):
                pays.setdefault(_pay_id(p), p)
        evs = _group_events(rows, pays, F, items[winner]["item"].get("transaction_key"), items[winner]["item"].get("claim_key"))
        for m in mem:
            if m in found:
                evs += found[m]
        group_events[winner], group_members[winner] = evs, sorted(mem)
        if evs:
            settle(winner, mem, evs)
        # worked items answered again: money after the first work (a win), or a denial after it with no money (a loss)
        for m in mem:
            ws = work.get(m) or []
            if not ws:
                continue
            w0 = ws[0]["date"]
            paid_after = any(e["cents"] > 0 and e["date"] > w0 for e in evs)
            denied = None
            for x in rows.values():
                d = str(x.get("date") or "")[:10]
                if d > w0 and x.get("status") != "22" and not x.get("reversed_by") and _amount(x) == 0 and (
                        x.get("status") == "4" or any(g in ("CO", "OA", "PI") and (a or 0) > 0 for g, _rc, a in (x.get("adj") or []))):
                    denied = min(denied or d, d)
            if denied:
                denied_again[m] = denied
            if paid_after:
                answered[m] = "win"
            elif denied:
                answered[m] = "loss"
    # an unposted payment found posted, on an item no 835 or EMA line ties to another
    for fid, evs in found.items():
        if fid not in group_of:
            settle(fid, [fid], list(evs))
    # a person who marked an item Paid, where no payment record shows the money: their amount, on their day
    for fid, r in reg.items():
        if fid in group_of and out_items.get(group_of[fid], {}).get("returned"):
            continue
        st, rec_c, rec_d = r.get("last_status"), None, None
        if st != "resolved":
            continue
        row = conn.execute("SELECT recovered_cents, recovered_date, auto_note FROM findings WHERE finding_id=?", (fid,)).fetchone() \
            if _table(conn, "findings") else None
        if not row or row[2] or not (row[0] or 0) > 0 or not row[1]:
            continue
        rec_c, rec_d = int(row[0]), str(row[1])[:10]
        if not r["F"] or rec_d <= r["F"]:
            continue
        e = {"id": ("mark", fid), "date": rec_d, "cents": rec_c, "kind": "mark", "source": "person", "payer": r.get("payer_group") or "",
             "text": f"Marked Paid ({usd(rec_c)}) on {rec_d} by a person; no payment record shows it yet"}
        if fid in group_of:
            winner = group_of[fid]
            mem = out_items[winner]["members"] if winner in out_items else [fid]
        else:
            winner, mem = fid, [fid]
        if winner in out_items and out_items[winner]["returned"]:
            continue
        settle(winner if winner in reg else fid, mem, [e])
    return {"items": out_items, "groups": group_of, "work": work, "denied_again": denied_again, "answered": answered,
            "candidates": len(cands), "newest": newest, "errors": errors}


# ------------------------------------------------------------------------------------------------------------- lift
def _band(F: str, today: str) -> str:
    d = _days(F, today) or 0
    for lim, label in AGE_BANDS:
        if lim is None or d <= lim:
            return label
    return AGE_BANDS[-1][1]


def _pattern(r: dict) -> tuple:
    return (r.get("payer_group") or "", r.get("procedure_code") or "", str(r.get("carc") or r.get("signal") or ""))


def lift(reg: dict, att: dict, today: Any = None) -> dict:
    """Worked against unworked, among the items that can bring money back: the share that brought money in each pattern
    (payer group x code x reason) and age band since the flag, the worked items' money against what the unworked rate
    predicts for them (the pattern's own rate when it has MIN_STRATUM unworked cases, else payer group x reason, else the
    age band, else every unworked item), and Z: the share paid among the unworked items the comparison used."""
    t = _today(today)
    groups = att["groups"]
    money = att["items"]
    pop = []
    for fid, r in reg.items():
        if not r["returnable"] or not r["F"]:
            continue
        winner = groups.get(fid)
        if winner is not None and winner != fid:
            continue            # its money is its group's: the group is counted once, by its earliest item
        mem = (money.get(fid) or {}).get("members") or [fid]
        worked = any(att["work"].get(m) for m in mem)
        ret = int((money.get(fid) or {}).get("returned") or 0)
        stake = max(int(reg[m].get("flagged_cents") or 0) for m in mem if m in reg) if mem else 0
        pat = _pattern(r)
        band = _band(r["F"], t)
        pop.append({"fid": fid, "worked": worked, "paid": ret > 0, "ret": ret, "stake": max(stake, 0),
                    "keys": ((pat[0], pat[1], pat[2], band), (pat[0], pat[2], band), (band,), ())})
    levels: list[dict] = [defaultdict(lambda: [0, 0, 0, 0]) for _ in range(4)]       # n, paid, stake, returned
    for p in pop:
        if p["worked"]:
            continue
        for i, k in enumerate(p["keys"]):
            s = levels[i][k]
            s[0] += 1
            s[1] += int(p["paid"])
            s[2] += p["stake"]
            s[3] += p["ret"]
    worked = [p for p in pop if p["worked"]]
    unworked = [p for p in pop if not p["worked"]]
    pred_n = pred_cents = 0.0
    used: set = set()
    level_used = Counter()
    for p in worked:
        for i, k in enumerate(p["keys"]):
            s = levels[i].get(k)
            if s and s[0] >= (MIN_STRATUM if i < 3 else 1):
                pred_n += s[1] / s[0]
                pred_cents += p["stake"] * (s[3] / s[2]) if s[2] else 0
                used.add((i, k))
                level_used[i] += 1
                break
    matched = [p for p in unworked if any((i, k) in used for i, k in enumerate(p["keys"]))]
    zpop = matched if worked else unworked
    z_n, z_k = len(zpop), sum(1 for p in zpop if p["paid"])
    w_n, w_k = len(worked), sum(1 for p in worked if p["paid"])
    u_n, u_k = len(unworked), sum(1 for p in unworked if p["paid"])
    w_ret = sum(p["ret"] for p in worked)
    return {"worked": {"n": w_n, "paid": w_k, "rate": _pct(w_k, w_n), "returned_cents": w_ret, "stake_cents": sum(p["stake"] for p in worked)},
            "unworked": {"n": u_n, "paid": u_k, "rate": _pct(u_k, u_n), "returned_cents": sum(p["ret"] for p in unworked),
                         "stake_cents": sum(p["stake"] for p in unworked)},
            "predicted_paid": round(pred_n, 1), "predicted_cents": int(round(pred_cents)),
            "incremental_cents": int(round(w_ret - pred_cents)), "z": _pct(z_k, z_n), "z_n": z_n, "z_paid": z_k,
            "enough": w_n >= MIN_RATE_N and u_n >= MIN_RATE_N,
            "levels_used": {["pattern and age", "payer group and reason", "age band", "every unworked item"][i]: n for i, n in level_used.items()},
            "basis": (f"Worked items: {w_k} of {w_n} brought money. Similar items nobody worked: {u_k} of {u_n}. "
                      f"Matched by payer group, code and reason in the same age band since the flag (a wider pattern where "
                      f"fewer than {MIN_STRATUM} unworked items match).")}


# ------------------------------------------------------------------------------------------------------ the figures
def _sum_rows(rows: list[tuple], key: Callable) -> list[dict]:
    agg: dict = defaultdict(lambda: {"n": 0, "cents": 0, "a": 0, "b": 0, "c": 0})
    for fid, it in rows:
        k = key(fid, it)
        if k is None:
            continue
        s = agg[k]
        s["n"] += 1
        s["cents"] += it["returned"]
        for b in BUCKETS:
            s[b] += it[b]
    return sorted(({"key": k, **v} for k, v in agg.items()), key=lambda r: (-r["cents"], str(r["key"])))


def _by_event(att: dict, reg: dict, key: Callable) -> tuple[list[dict], dict]:
    """Money split by something each counted dollar carries (its week, its month, the kind of work credited, who did it).
    Returns the rows and {key: [[finding id, cents], ...]} for the lists."""
    agg: dict = defaultdict(lambda: {"cents": 0, "a": 0, "b": 0, "c": 0, "ids": defaultdict(int)})
    for fid, it in att["items"].items():
        for e in it["events"]:
            c = int(e.get("counted_cents") or 0)
            if c <= 0:
                continue
            k = key(fid, e)
            if k is None:
                continue
            s = agg[k]
            s["cents"] += c
            s[e["bucket"]] += c
            s["ids"][fid] += c
    rows, lists = [], {}
    for k, s in agg.items():
        rows.append({"key": k, "n": len(s["ids"]), "cents": s["cents"], "a": s["a"], "b": s["b"], "c": s["c"]})
        lists[k] = sorted(([f, c] for f, c in s["ids"].items()), key=lambda x: (-x[1], x[0]))
    return sorted(rows, key=lambda r: str(r["key"])), lists


def _money(conn: sqlite3.Connection, reg: dict, att: dict, lf: dict, today: str) -> tuple[dict, dict]:
    items = att["items"]
    rows = sorted(((fid, it) for fid, it in items.items() if it["returned"] > 0), key=lambda x: (-x[1]["returned"], x[0]))
    lists: dict[str, list] = {"returned": [[fid, it["returned"]] for fid, it in rows]}
    for b in BUCKETS:
        lists[f"returned:{b}"] = [[fid, it[b]] for fid, it in sorted(rows, key=lambda x: (-x[1][b], x[0])) if it[b] > 0]
    lists["taken_back"] = [[fid, it["taken_back"]] for fid, it in sorted(items.items(), key=lambda x: (-x[1]["taken_back"], x[0]))
                           if it["taken_back"] > 0]
    tot = {b: sum(it[b] for _f, it in rows) for b in BUCKETS}
    total = sum(tot.values())
    # the qualifier: Z of the dollars flagged on the items whose money is in (c), at most (c)
    c_flagged = sum(int(reg[f].get("flagged_cents") or 0) for f, it in rows if it["c"] > 0 and f in reg)
    z = lf.get("z")
    y = min(int(round((z or 0) * c_flagged)), tot["c"]) if z is not None else None
    by_payer = _sum_rows(rows, lambda f, it: (reg.get(f) or {}).get("payer_group") or "Unknown")
    by_signal = _sum_rows(rows, lambda f, it: (reg.get(f) or {}).get("signal") or "")
    for r in by_payer:
        lists[f"returned:payer:{r['key']}"] = [[f, it["returned"]] for f, it in rows if ((reg.get(f) or {}).get("payer_group") or "Unknown") == r["key"]]
    for r in by_signal:
        lists[f"returned:signal:{r['key']}"] = [[f, it["returned"]] for f, it in rows if (reg.get(f) or {}).get("signal") == r["key"]]
    by_action, la = _by_event(att, reg, lambda f, e: e.get("work_kind") or ("found" if e["bucket"] == "b" else "none"))
    by_person, lp = _by_event(att, reg, lambda f, e: e.get("work_who") if e["bucket"] == "a" else None)
    by_week, lw = _by_event(att, reg, lambda f, e: week_of(e["date"]))
    by_month, lm = _by_event(att, reg, lambda f, e: str(e["date"])[:7])
    for k, v in la.items():
        lists[f"returned:action:{k}"] = v
    for k, v in lp.items():
        lists[f"returned:person:{k}"] = v
    for k, v in lw.items():
        lists[f"returned:week:{k}"] = v
    for k, v in lm.items():
        lists[f"returned:month:{k}"] = v
    gaps = [_days(e["work_date"], e["date"]) for it in items.values() for e in it["events"]
            if e.get("bucket") == "a" and e.get("work_date") and (e.get("counted_cents") or 0) > 0]
    first_flag = min((r["F"] for r in reg.values() if r["F"]), default=None)
    out = {"returned_cents": total, "a_cents": tot["a"], "b_cents": tot["b"], "c_cents": tot["c"], "items": len(rows),
           "a_items": len(lists["returned:a"]), "b_items": len(lists["returned:b"]), "c_items": len(lists["returned:c"]),
           "taken_back_cents": sum(it["taken_back"] for it in items.values()), "taken_back_items": len(lists["taken_back"]),
           "anyway": {"cents": y, "rate": z, "n": lf.get("z_n"), "paid": lf.get("z_paid"), "c_flagged_cents": c_flagged,
                      "basis": (f"Similar items with no work were paid at {round((z or 0) * 100)}% ({lf.get('z_n')} items); that share "
                                f"of the {usd(c_flagged)} flagged on the items that came back with no work, at most what they brought "
                                f"back ({usd(tot['c'])})") if z is not None else "Not enough similar items yet"},
           "by_payer": by_payer[:40], "by_signal": by_signal[:40], "by_action": by_action, "by_person": by_person,
           "by_week": by_week, "by_month": by_month, "median_days_work_to_money": _median(gaps), "first_flag": first_flag,
           "rules": {b: BUCKET_LABELS[b] for b in BUCKETS}}
    return out, lists


def _money_items_count(att: dict) -> int:
    return sum(1 for it in att["items"].values() if it["returned"] > 0)


def _work_done(conn: sqlite3.Connection, reg: dict, att: dict, today: str) -> tuple[dict, dict]:
    """Items worked by kind of work, the faxes sent (pages), by person and by week; items let go with a reason and items
    accepted by the owner's policy or a hide rule."""
    work = att["work"]
    by_kind: dict = defaultdict(lambda: {"items": set(), "n": 0, "pages": 0})
    by_person: dict = defaultdict(lambda: {"items": set(), "n": 0})
    by_week: dict = defaultdict(lambda: defaultdict(int))
    fax_pages: dict = defaultdict(int)
    for fid, ws in work.items():
        for w in ws:
            s = by_kind[w["kind"]]
            s["items"].add(fid)
            s["n"] += 1
            s["pages"] += w.get("pages") or 0
            p = by_person[w["who"]]
            p["items"].add(fid)
            p["n"] += 1
            by_week[week_of(w["date"])][w["kind"]] += 1
            if w["kind"] == "fax":
                fax_pages[str(w["date"])[:7]] += w.get("pages") or 0
    lists = {f"work:{k}": [[f, 0] for f in sorted(v["items"])] for k, v in by_kind.items()}
    lists.update({f"work:person:{k}": [[f, 0] for f in sorted(v["items"])] for k, v in by_person.items()})
    let_go, policy = [], []
    if _table(conn, "findings"):
        has_ev = _table(conn, "finding_events")
        cats = ",".join("?" * len(RETURN_CATEGORIES))
        if has_ev:
            let_go = [r[0] for r in conn.execute(f"""SELECT f.finding_id FROM findings f WHERE f.status IN ('dismissed','lost')
                    AND f.category IN ({cats}) AND EXISTS (SELECT 1 FROM finding_events e WHERE e.finding_id=f.finding_id
                    AND e.kind='manual' AND e.to_status IN ('dismissed','lost') AND COALESCE(e.note,'') <> '')""", RETURN_CATEGORIES)]
        policy = [r[0] for r in conn.execute(f"""SELECT finding_id FROM findings WHERE work_state='accepted' AND category IN ({cats})
                    AND status NOT IN ('dismissed','lost')""", RETURN_CATEGORIES)] if "work_state" in _cols(conn, "findings") else []
        if has_ev:
            hidden = {r[0] for r in conn.execute(f"""SELECT DISTINCT f.finding_id FROM findings f JOIN finding_events e ON e.finding_id=f.finding_id
                    WHERE e.kind='hidden' AND f.status='dismissed' AND f.category IN ({cats})""", RETURN_CATEGORIES)}
            policy = sorted(set(policy) | hidden)
            let_go = sorted(set(let_go) - hidden)
    lists["work:let_go"] = [[f, 0] for f in sorted(let_go)]
    lists["work:policy"] = [[f, 0] for f in sorted(policy)]
    worked_items = sorted({f for f, ws in work.items() if ws})
    lists["work:any"] = [[f, 0] for f in worked_items]
    out = {"kinds": [{"key": k, "label": WORK_KINDS.get(k, k), "items": len(v["items"]), "n": v["n"], "pages": v["pages"]}
                     for k, v in sorted(by_kind.items(), key=lambda kv: -len(kv[1]["items"]))],
           "people": [{"key": k, "items": len(v["items"]), "n": v["n"]} for k, v in sorted(by_person.items(), key=lambda kv: -kv[1]["n"])],
           "weeks": [{"week": w, **dict(v)} for w, v in sorted(by_week.items()) if w][-26:],
           "let_go": len(let_go), "policy": len(policy), "worked_items": len(worked_items),
           "fax_pages_by_month": dict(sorted(fax_pages.items())),
           "faxes_sent": by_kind["fax"]["n"] if "fax" in by_kind else 0, "fax_pages": by_kind["fax"]["pages"] if "fax" in by_kind else 0}
    return out, lists


def _closure(r: dict, f: dict | None, money: int) -> str | None:
    """Why an item of the open book is closed now, or None while it is open: paid, patient, second payer, owner policy,
    let go by a person, expired, otherwise settled, or no longer listed."""
    if f is None:
        return "paid" if money > 0 else "gone"
    ws, st = f.get("work_state"), f.get("status")
    basis = str(f.get("state_basis") or "").lower()
    outcome = str(f.get("ema_outcome") or "")
    if ws in OPEN_STATES or ws == "cant_check" or ws is None:
        return None
    if st == "resolved" or money > 0 or outcome.startswith("paid_after"):
        return "paid"
    if ws == "accepted":
        return "let_go" if st in ("dismissed", "lost") and "hidden by the rule" not in basis else "policy"
    if ws == "expired":
        return "expired"
    if outcome == "patient" or "patient" in basis:
        return "patient"
    if "second payer" in basis or "secondary" in basis or "another payer" in basis or "other payer" in basis:
        return "secondary"
    return "other"


CLOSURE_LABELS = {"paid": "Paid", "patient": "Moved to the patient", "secondary": "Settled by another payer", "policy": "Owner policy or hide rule",
                  "let_go": "Let go by a person", "expired": "Expired", "other": "Otherwise settled (paid before, a duplicate, a twin)",
                  "gone": "No longer listed"}


def _outcomes(conn: sqlite3.Connection, reg: dict, att: dict, today: str) -> tuple[dict, dict]:
    """The open book: every item that was ever Work now or Waiting. How many are closed now and why, how many cannot be
    checked and why, how many were worked and wait with no money yet (and for how long), how many were worked and denied
    again, and the win rate by kind of work and by pattern."""
    book = {fid: r for fid, r in reg.items() if r.get("ever_open") and (r["returnable"])}
    cur: dict[str, dict] = {}
    if book and _table(conn, "findings"):
        fc = _cols(conn, "findings")
        sel = [c for c in ("finding_id", "work_state", "status", "state_basis", "ema_outcome", "follow_up_on") if c in fc]
        for part in _chunks(sorted(book)):
            _temp_ids(conn, "rs_o", part)
            for r in conn.execute(f"SELECT {', '.join(sel)} FROM findings WHERE finding_id IN (SELECT id FROM temp.rs_o)"):
                cur[r[0]] = dict(zip(sel, r))
        _drop(conn, "rs_o")
    money = {fid: (att["items"].get(att["groups"].get(fid, fid)) or {}).get("returned", 0) for fid in book}
    closed: dict[str, list] = defaultdict(list)
    cant: dict[str, list] = defaultdict(list)
    waiting_no_money, waited = [], []
    for fid, r in book.items():
        f = cur.get(fid)
        why = _closure(r, f, money.get(fid, 0))
        if why:
            closed[why].append(fid)
        elif f and f.get("work_state") == "cant_check":
            basis = str(f.get("state_basis") or "No reason recorded")
            cant[re.split(r"[:.(]", basis, maxsplit=1)[0].strip()[:80] or "No reason recorded"].append(fid)
        ws = att["work"].get(fid) or []
        if ws and f and f.get("work_state") in OPEN_STATES and not money.get(fid):
            waiting_no_money.append(fid)
            waited.append(_days(ws[-1]["date"], today))
    n = len(book)
    n_closed = sum(len(v) for v in closed.values())
    worked = [fid for fid in book if att["work"].get(fid)]
    denied = sorted(fid for fid in worked if fid in att["denied_again"])
    wins = defaultdict(lambda: [0, 0])
    pat = defaultdict(lambda: [0, 0])
    for fid in worked:
        a = att["answered"].get(fid)
        if not a:
            continue
        k = att["work"][fid][0]["kind"]
        wins[k][0 if a == "win" else 1] += 1
        p = _pattern(book[fid])
        pat[" x ".join(x for x in p if x) or "Unknown"][0 if a == "win" else 1] += 1
    lists = {f"closed:{k}": [[f, 0] for f in sorted(v)] for k, v in closed.items()}
    lists["closed:any"] = [[f, 0] for f in sorted(fid for v in closed.values() for fid in v)]
    lists.update({f"cant:{k}": [[f, 0] for f in sorted(v)] for k, v in cant.items()})
    lists["cant:any"] = [[f, 0] for f in sorted(fid for v in cant.values() for fid in v)]
    lists["waiting_no_money"] = [[f, 0] for f in sorted(waiting_no_money)]
    lists["denied_again"] = [[f, 0] for f in denied]
    lists["book"] = [[f, 0] for f in sorted(book)]
    n_cant = sum(len(v) for v in cant.values())
    out = {"book": n, "closed": n_closed, "closed_rate": _pct(n_closed, n),
           "closed_by": [{"key": k, "label": CLOSURE_LABELS.get(k, k), "n": len(v), "rate": _pct(len(v), n)}
                         for k, v in sorted(closed.items(), key=lambda kv: -len(kv[1]))],
           "cant_check": n_cant, "cant_rate": _pct(n_cant, n),
           "cant_by": [{"key": k, "n": len(v)} for k, v in sorted(cant.items(), key=lambda kv: -len(kv[1]))][:12],
           "worked": len(worked), "waiting_no_money": len(waiting_no_money), "waiting_no_money_rate": _pct(len(waiting_no_money), len(worked)),
           "waiting_median_days": _median(waited), "denied_again": len(denied), "denied_again_rate": _pct(len(denied), len(worked)),
           "win_by_kind": [{"key": k, "label": WORK_KINDS.get(k, k), "won": v[0], "lost": v[1], "rate": _pct(v[0], v[0] + v[1])}
                           for k, v in sorted(wins.items())],
           "win_by_pattern": [{"key": k, "won": v[0], "lost": v[1], "rate": _pct(v[0], v[0] + v[1])}
                              for k, v in sorted(pat.items(), key=lambda kv: -(kv[1][0] + kv[1][1]))][:15]}
    return out, lists


def _hours_counts(conn: sqlite3.Connection, att: dict) -> tuple[dict, dict]:
    """What the hours-saved estimate multiplies: items RemitWatch proved settled or closed by itself, appeal packets built,
    faxes sent, and repeat listings folded into one item."""
    lists: dict[str, list] = {}
    invest: list = []
    if _table(conn, "findings"):
        w, p = live_sql("hours:investigate", _today())
        invest = [r[0] for r in conn.execute(f"SELECT finding_id FROM findings f WHERE {w} ORDER BY finding_id", p)]
    packets: set = set()
    n_packets = 0
    if _table(conn, "appeal_packets") and "finding_id" in _cols(conn, "appeal_packets"):
        for fid, n in conn.execute("SELECT finding_id, COUNT(*) FROM appeal_packets GROUP BY finding_id"):
            if fid:
                packets.add(fid)
                n_packets += n
    if _table(conn, "finding_events"):
        ev_n = 0
        for fid, n in conn.execute("SELECT finding_id, COUNT(*) FROM finding_events WHERE kind='packet' GROUP BY finding_id"):
            packets.add(fid)
            ev_n += n
        n_packets = max(n_packets, ev_n)
    faxed = sorted({f for f, ws in att["work"].items() for w in ws if w["kind"] == "fax"})
    n_fax = sum(1 for ws in att["work"].values() for w in ws if w["kind"] == "fax")
    folded_n, carriers = 0, []
    if _table(conn, "findings"):
        for fid, ev in conn.execute("SELECT finding_id, evidence FROM findings WHERE evidence LIKE '%\"folded\"%'"):
            k = len(_json(ev).get("folded") or [])
            if k:
                folded_n += k
                carriers.append(fid)
    lists["hours:investigate"] = [[f, 0] for f in invest]
    lists["hours:packet"] = [[f, 0] for f in sorted(packets)]
    lists["hours:fax"] = [[f, 0] for f in faxed]
    lists["hours:duplicate"] = [[f, 0] for f in sorted(carriers)]
    return {"investigate": len(invest), "packet": n_packets, "fax": n_fax, "duplicate": folded_n}, lists


def _ai_calls(conn: sqlite3.Connection) -> dict:
    """AI calls the app makes, counted from its own records, by month: each payer fax it read, each appeal packet built
    (a visit-note review), each documentation template written on request."""
    out: dict = defaultdict(int)
    if _table(conn, "faxes") and "direction" in _cols(conn, "faxes"):
        for m, n in conn.execute("SELECT substr(created_at,1,7), COUNT(*) FROM faxes WHERE direction='in' AND created_at IS NOT NULL GROUP BY 1"):
            out[m] += n
    if _table(conn, "appeal_packets") and "created_at" in _cols(conn, "appeal_packets"):
        for m, n in conn.execute("SELECT substr(created_at,1,7), COUNT(*) FROM appeal_packets WHERE created_at IS NOT NULL GROUP BY 1"):
            out[m] += n
    elif _table(conn, "finding_events"):
        for m, n in conn.execute("SELECT substr(at,1,7), COUNT(*) FROM finding_events WHERE kind='packet' GROUP BY 1"):
            out[m] += n
    if _table(conn, "note_lessons") and "created_at" in _cols(conn, "note_lessons"):
        for m, n in conn.execute("SELECT substr(created_at,1,7), COUNT(*) FROM note_lessons WHERE tips IS NOT NULL AND tips <> '[]' GROUP BY 1"):
            out[m] += n
    return dict(sorted((k, v) for k, v in out.items() if k))


def _credits(conn: sqlite3.Connection, today: str) -> dict:
    """Credits to refund (CREDIT_BALANCE items): open, settled, and the Medicare ones open more than 60 days (compliance)."""
    if not _table(conn, "findings"):
        return {}
    out = {"open_n": 0, "open_cents": 0, "settled_n": 0, "settled_cents": 0, "medicare_overdue_n": 0}
    fc = _cols(conn, "findings")
    ws = "work_state" if "work_state" in fc else "NULL"
    for st, wst, kind, cents, first in conn.execute(f"""SELECT status, {ws}, payer_kind, shortfall_cents, first_seen FROM findings
                                                         WHERE signal='CREDIT_BALANCE' AND COALESCE(stale,0)=0"""):
        if st == "resolved" or wst == "done":
            out["settled_n"] += 1
            out["settled_cents"] += int(cents or 0)
        elif st not in ("dismissed", "lost"):
            out["open_n"] += 1
            out["open_cents"] += int(cents or 0)
            if kind == "medicare" and (_days(first, today) or 0) > 60:
                out["medicare_overdue_n"] += 1
    return out


def _lost(reg: dict) -> tuple[dict, dict]:
    """Items that expired while they were Work now (lost to the deadline), recorded by the registry when the state changed."""
    rows = sorted(((fid, r) for fid, r in reg.items() if r.get("expired_on")), key=lambda x: (-(x[1].get("lost_cents") or 0), x[0]))
    weeks: dict = defaultdict(lambda: [0, 0])
    for fid, r in rows:
        w = week_of(r.get("lost_on") or r.get("expired_on"))
        weeks[w][0] += 1
        weeks[w][1] += int(r.get("lost_cents") or 0)
    lists = {"lost": [[fid, int(r.get("lost_cents") or 0)] for fid, r in rows]}
    for w in weeks:
        lists[f"lost:week:{w}"] = [[fid, int(r.get("lost_cents") or 0)] for fid, r in rows
                                   if week_of(r.get("lost_on") or r.get("expired_on")) == w]
    return {"n": len(rows), "cents": sum(int(r.get("lost_cents") or 0) for _f, r in rows),
            "weeks": [{"week": w, "n": v[0], "cents": v[1]} for w, v in sorted(weeks.items()) if w]}, lists


# ---------------------------------------------------------------------------------- denials by month, and their fixes
def _denials(conn: sqlite3.Connection, today: str, errors: list) -> tuple[dict, dict]:
    """Denial rates from the 835s, month by month (payment month), the last 24 months: denied lines over answered lines and
    denied dollars over billed dollars, by payer group, by code family and by clinician (owner only). A line is answered
    when its claim was processed (status 1, 2, 3, 4, 19 or 20, never a reversal) and denied when the payer allowed and paid
    nothing on it. Fix markers: a fix noted on an item (a credentialing, authorization or coding fix) with the payer
    group's rate before and after. The top rising and falling reasons: the last 90 days of payments against the 90 before.
    Worked out again only when the 835s change."""
    if not (_table(conn, "lines") and _table(conn, "claims") and _table(conn, "transactions")):
        return {"months": [], "by_group": [], "by_family": [], "markers": [], "reasons": {}}, {}
    newest = conn.execute("SELECT MAX(payment_date) FROM transactions WHERE COALESCE(superseded,0)=0").fetchone()[0]
    count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    rev = f"{VERSION}|{newest}|{count}"
    cached = _meta(conn, DENIALS_KEY, None)
    fix_rows = _fix_markers(conn)
    if isinstance(cached, dict) and cached.get("rev") == rev:
        base, clin = cached["base"], cached["clinicians"]
    else:
        if not newest:
            return {"months": [], "by_group": [], "by_family": [], "markers": [], "reasons": {}}, {}
        from .codes import code_category, payer_group_kind
        since = (_add(newest, -730) or "")[:7] + "-01"
        gk: dict = {}

        def grp(name: Any, cfi: Any) -> str:
            k = (name, cfi)
            if k not in gk:
                gk[k] = payer_group_kind(name, cfi)[0] if name else "Unknown"
            return gk[k]
        g = defaultdict(lambda: [0, 0, 0, 0])       # answered, denied, billed, denied cents
        fam = defaultdict(lambda: [0, 0, 0, 0])
        cl = defaultdict(lambda: [0, 0, 0, 0])
        lcols = _cols(conn, "lines")
        npi = "COALESCE(l.rendering_provider_npi, c.rendering_provider_npi)" if "rendering_provider_npi" in lcols else "c.rendering_provider_npi"
        for m, payer, cfi, code, who, n, den, billed, dcents in conn.execute(
                f"""SELECT substr(t.payment_date,1,7), c.payer_name, c.claim_filing_indicator, COALESCE(l.original_procedure_code, l.procedure_code),
                           {npi}, COUNT(*),
                           SUM(CASE WHEN COALESCE(l.paid_cents,0)=0 AND COALESCE(l.allowed_cents,0)=0 THEN 1 ELSE 0 END),
                           SUM(l.charge_cents),
                           SUM(CASE WHEN COALESCE(l.paid_cents,0)=0 AND COALESCE(l.allowed_cents,0)=0 THEN l.charge_cents ELSE 0 END)
                    FROM lines l JOIN claims c ON c.claim_key = l.claim_key JOIN transactions t ON t.transaction_key = l.transaction_key
                    WHERE COALESCE(t.superseded,0)=0 AND c.claim_status_code IN ('1','2','3','4','19','20') AND COALESCE(l.charge_cents,0) > 0
                      AND t.payment_date >= ?
                    GROUP BY 1,2,3,4,5""", (since,)):
            for book, k in ((g, (m, grp(payer, cfi))), (fam, (m, code_category(code))), (cl, (m, who or ""))):
                s = book[k]
                s[0] += n
                s[1] += den or 0
                s[2] += billed or 0
                s[3] += dcents or 0
        base = {"newest": newest, "group": [[k[0], k[1], *v] for k, v in sorted(g.items())],
                "family": [[k[0], k[1], *v] for k, v in sorted(fam.items())], "reasons": _reasons(conn, newest)}
        names = {}
        if _table(conn, "providers"):
            names = {r[0]: r[1] for r in conn.execute("SELECT npi, name FROM providers")}
        clin = [[k[0], k[1], names.get(k[1]) or "", *v] for k, v in sorted(cl.items())]
        _set_meta(conn, DENIALS_KEY, {"rev": rev, "base": base, "clinicians": clin})
    months = sorted({r[0] for r in base["group"]})
    totals = defaultdict(lambda: [0, 0, 0, 0])
    for m, _g, *v in base["group"]:
        for i in range(4):
            totals[m][i] += v[i]

    def rates(v):
        return {"answered": v[0], "denied": v[1], "billed_cents": v[2], "denied_cents": v[3],
                "line_rate": _pct(v[1], v[0]), "dollar_rate": _pct(v[3], v[2])}
    month_rows = [dict(month=m, **rates(totals[m])) for m in months]
    by_group = _rate_table(base["group"])
    by_family = _rate_table(base["family"])
    markers = []
    for mk in fix_rows:
        m0 = str(mk["date"])[:7]
        before = [r for r in base["group"] if r[1] == mk["payer_group"] and _months_between(r[0], m0) in (1, 2, 3)]
        after = [r for r in base["group"] if r[1] == mk["payer_group"] and _months_between(m0, r[0]) in (1, 2, 3)]
        bsum = [sum(r[2 + i] for r in before) for i in range(4)]
        asum = [sum(r[2 + i] for r in after) for i in range(4)]
        markers.append(dict(mk, before=rates(bsum), after=rates(asum)))
    out = {"months": month_rows[-24:], "by_group": by_group, "by_family": by_family, "markers": markers,
           "reasons": base.get("reasons") or {}, "newest_835": base.get("newest"),
           "definition": ("A line is answered when the payer processed its claim, and denied when the payer allowed and paid "
                          "nothing on it. Months are payment months.")}
    lists = {f"denial_fix:{i}": [[mk["finding_id"], 0]] for i, mk in enumerate(markers)}
    return out, lists


def _months_between(a: str, b: str) -> int | None:
    try:
        return (int(b[:4]) - int(a[:4])) * 12 + int(b[5:7]) - int(a[5:7])
    except (TypeError, ValueError):
        return None


def _rate_table(rows: list) -> list[dict]:
    """[{key, months: [{month, line_rate, dollar_rate, answered, denied}], answered, denied, line_rate}] largest first."""
    by: dict = defaultdict(list)
    for m, k, *v in rows:
        by[k].append((m, v))
    out = []
    for k, ms in by.items():
        tot = [sum(v[i] for _m, v in ms) for i in range(4)]
        out.append({"key": k, "answered": tot[0], "denied": tot[1], "billed_cents": tot[2], "denied_cents": tot[3],
                    "line_rate": _pct(tot[1], tot[0]), "dollar_rate": _pct(tot[3], tot[2]),
                    "months": [{"month": m, "answered": v[0], "denied": v[1], "line_rate": _pct(v[1], v[0]), "dollar_rate": _pct(v[3], v[2])}
                               for m, v in sorted(ms)][-24:]})
    return sorted(out, key=lambda r: -r["answered"])[:25]


def _reasons(conn: sqlite3.Connection, newest: str) -> dict:
    """The reasons on denied lines (group and reason code), the last 90 days of payments against the 90 before: the five
    that rose most and the five that fell most, by lines."""
    if not _table(conn, "adjustments"):
        return {"rising": [], "falling": []}
    mid, lo = _add(newest, -90), _add(newest, -180)
    cnt: dict = defaultdict(lambda: [0, 0])
    for grp_code, rc, recent, n in conn.execute(
            """SELECT a.group_code, a.reason_code, t.payment_date > ?, COUNT(DISTINCT l.line_key)
               FROM lines l JOIN claims c ON c.claim_key = l.claim_key JOIN transactions t ON t.transaction_key = l.transaction_key
               JOIN adjustments a ON a.line_key = l.line_key
               WHERE COALESCE(t.superseded,0)=0 AND c.claim_status_code IN ('1','2','3','4','19','20') AND COALESCE(l.charge_cents,0) > 0
                 AND COALESCE(l.paid_cents,0)=0 AND COALESCE(l.allowed_cents,0)=0 AND t.payment_date > ? AND a.group_code IN ('CO','OA','PI','PR')
                 AND COALESCE(a.amount_cents,0) > 0
               GROUP BY 1,2,3""", (mid, lo)):
        cnt[f"{grp_code}-{rc}"][1 if recent else 0] += n
    from .codes import carc_text
    rows = [{"reason": k, "meaning": carc_text(k.split("-", 1)[1]) or "", "before": v[0], "recent": v[1], "change": v[1] - v[0]}
            for k, v in cnt.items()]
    return {"rising": sorted((r for r in rows if r["change"] > 0), key=lambda r: (-r["change"], r["reason"]))[:5],
            "falling": sorted((r for r in rows if r["change"] < 0), key=lambda r: (r["change"], r["reason"]))[:5],
            "window": [lo, mid, newest]}


def _fix_markers(conn: sqlite3.Connection) -> list[dict]:
    """Fixes people recorded on items: a line in an item's history of kind 'fix', or a person's note that names a
    credentialing, authorization or coding fix. Each with its day, the item's payer group and the kind of fix (the note
    itself is not copied)."""
    if not (_table(conn, "finding_events") and _table(conn, "findings")):
        return []
    out, seen = [], set()
    for fid, at, kind, note, actor, grp, sig in conn.execute(
            """SELECT e.finding_id, e.at, e.kind, e.note, e.actor, f.payer_group, f.signal FROM finding_events e
               JOIN findings f ON f.finding_id = e.finding_id
               WHERE (e.kind='fix' OR (e.kind='manual' AND e.note IS NOT NULL AND e.note <> '')) ORDER BY e.at"""):
        text = str(note or "")
        if kind != "fix" and not FIX_RE.search(text):
            continue
        what = next((k for k, rx in FIX_KINDS if rx.search(text)), "fix")
        key = (grp, str(at)[:10], what)
        if key in seen:
            continue
        seen.add(key)
        out.append({"date": str(at)[:10], "payer_group": grp or "", "kind": what, "finding_id": fid, "signal": sig})
    return out[-40:]


# --------------------------------------------------------------------------------------- live counts (both hosts)
def live_sql(key: str, today: str) -> tuple[str, list]:
    """A figure counted on the findings table alone, as one condition over `findings f`: the count and the list it opens
    run the same condition, so they agree on either host. Raises KeyError for a key that is not one of these."""
    money = ",".join(f"'{c}'" for c in RETURN_CATEGORIES)
    scope = ",".join(f"'{c}'" for c in STATE_CATEGORIES)
    if key.startswith("state:"):
        st = key.split(":", 1)[1]
        if st not in STATE_LABELS:
            raise KeyError(key)
        return f"f.category IN ({scope}) AND COALESCE(f.stale,0)=0 AND f.work_state=?", [st]
    if key == "at_risk":
        return (f"f.category IN ({money}) AND COALESCE(f.stale,0)=0 AND f.work_state='work_now' AND f.appeal_deadline >= ? "
                f"AND f.appeal_deadline <= ?", [today, _add(today, AT_RISK_DAYS)])
    if key in ("given_up", "given_up:recoverable", "given_up:past"):
        cond = (f"f.category IN ({money}) AND COALESCE(f.stale,0)=0 AND COALESCE(f.written_off_cents,0) > 0 AND f.service_date >= ? "
                f"AND f.status='open' AND COALESCE(f.work_state,'') NOT IN ('accepted') "
                f"AND NOT EXISTS (SELECT 1 FROM finding_events e WHERE e.finding_id=f.finding_id AND e.kind IN ('manual','hidden'))")
        params: list = [GIVEN_UP_FROM]
        if key == "given_up:recoverable":
            cond += " AND f.appeal_deadline >= ?"
            params.append(today)
        elif key == "given_up:past":
            cond += " AND (f.appeal_deadline IS NULL OR f.appeal_deadline < ?)"
            params.append(today)
        return cond, params
    if key == "hours:investigate":
        return ((f"f.category IN ({scope}) AND ((f.status='resolved' AND f.auto_note IS NOT NULL) OR "
                 f"(f.work_state='done' AND f.status='open' AND COALESCE(f.stale,0)=0))"), [])
    raise KeyError(key)


LIVE_KEYS = tuple([f"state:{s}" for s in STATE_LABELS] + ["at_risk", "given_up", "given_up:recoverable", "given_up:past"])


def live_figures(conn: sqlite3.Connection, today: Any = None) -> dict:
    """The figures counted on the findings table at the moment of asking (both hosts): items by state, money at risk in
    the next 14 days, money given up without a decision (with the part still inside its window)."""
    t = _today(today)
    out: dict = {"states": {}, "at_risk": {"n": 0, "cents": 0}, "given_up": {"n": 0, "cents": 0}}
    if not _table(conn, "findings"):
        return out
    fc = _cols(conn, "findings")
    if "work_state" not in fc:
        return out
    has_ev = _table(conn, "finding_events")
    for key in LIVE_KEYS:
        if key.startswith("given_up") and (not has_ev or "written_off_cents" not in fc):
            continue
        w, p = live_sql(key, t)
        n, c = conn.execute(f"SELECT COUNT(*), COALESCE(SUM(f.shortfall_cents),0) FROM findings f WHERE {w}" if not key.startswith("given_up")
                            else f"SELECT COUNT(*), COALESCE(SUM(f.written_off_cents),0) FROM findings f WHERE {w}", p).fetchone()
        if key.startswith("state:"):
            out["states"][key.split(":", 1)[1]] = {"n": n, "cents": c}
        else:
            out[key.replace(":", "_")] = {"n": n, "cents": c}
    out["given_up"]["source"] = "results"
    out["given_up"]["basis"] = ("Worked out here until the self-check is in place: dollars EMA wrote off on service from 2025 on "
                                "that no person decided and no owner-approved policy covers.")
    g = _given_up_selfcheck(conn, t)
    if g:
        out["given_up_selfcheck"] = g
    return out


def _given_up_selfcheck(conn: sqlite3.Connection, today: str) -> dict | None:
    """The self-check's own figure for money given up without a decision, when the run stores it (unit G): shown beside
    ours, named as its own."""
    g = _meta(conn, "selfcheck:given_up", None)
    if isinstance(g, dict) and g.get("cents") is not None:
        return {"cents": int(g.get("cents") or 0), "n": g.get("n"), "recoverable_cents": g.get("recoverable_cents"), "at": g.get("at"),
                "source": "selfcheck"}
    return None


def _given_up_weeks(conn: sqlite3.Connection, today: str) -> list[dict]:
    if not _table(conn, "findings") or not _table(conn, "finding_events") or "written_off_on" not in _cols(conn, "findings"):
        return []
    w, p = live_sql("given_up", today)
    agg: dict = defaultdict(lambda: [0, 0])
    for on, cents in conn.execute(f"SELECT f.written_off_on, f.written_off_cents FROM findings f WHERE {w}", p):
        wk = week_of(on)
        if wk:
            agg[wk][0] += 1
            agg[wk][1] += int(cents or 0)
    return [{"week": k, "n": v[0], "cents": v[1]} for k, v in sorted(agg.items())]


# ------------------------------------------------------------------------------------------------ compute and store
def data_rev(conn: sqlite3.Connection, today: Any = None) -> str:
    """What the figures rest on, as one string: the newest detection run, the newest history line, the faxes, the spot
    checks, the owner's figures, the newest payment record and the day. Equal strings: nothing to work out again."""
    parts = [VERSION, _today(today)]
    for table, sql in (("detection_runs", "SELECT MAX(finished_at), COUNT(*) FROM detection_runs"),
                       ("finding_events", "SELECT MAX(id), COUNT(*) FROM finding_events"),
                       ("faxes", "SELECT MAX(updated_at), COUNT(*) FROM faxes"),
                       ("spot_checks", "SELECT MAX(answered_at), COUNT(*) FROM spot_checks"),
                       ("findings", "SELECT COUNT(*), SUM(CASE WHEN status='resolved' THEN 1 ELSE 0 END) FROM findings")):
        if _table(conn, table):
            try:
                parts.append(json.dumps(list(conn.execute(sql).fetchone()), default=str))
            except sqlite3.Error:
                parts.append("?")
    parts.append(json.dumps(_meta(conn, SETTINGS_KEY, {}), sort_keys=True, default=str))
    parts.append(_newest_data(conn))
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:20]


def compute(conn: sqlite3.Connection, today: Any = None, *, errors: list | None = None) -> tuple[dict, dict]:
    """Every figure of the Results tab from the full store, and the lists behind them: (payload, lists)."""
    t = _today(today)
    errors = errors if errors is not None else []
    reg = load_registry(conn)
    att = attribute(conn, t, registry=reg, errors=errors)
    lf = lift(reg, att, t)
    money, lists = _money(conn, reg, att, lf, t)
    for k, v in (("lift:worked", [p for p in reg if att["work"].get(p)]),):
        lists[k] = [[f, (att["items"].get(att["groups"].get(f, f)) or {}).get("returned", 0)] for f in sorted(v) if reg[f]["returnable"]]
    work, wl = _work_done(conn, reg, att, t)
    lists.update(wl)
    outcomes, ol = _outcomes(conn, reg, att, t)
    lists.update(ol)
    hours, hl = _hours_counts(conn, att)
    lists.update(hl)
    lost, ll = _lost(reg)
    lists.update(ll)
    try:
        denials, dl = _denials(conn, t, errors)
        lists.update(dl)
    except sqlite3.Error as exc:     # recorded on the figures; the rest of the page stands
        errors.append(f"denials: {type(exc).__name__}: {str(exc)[:200]}")
        denials = {"months": [], "by_group": [], "by_family": [], "markers": [], "reasons": {}}
    gone = {}
    for f, r in reg.items():
        if r.get("gone_on"):
            gone[f] = {"signal": r.get("signal"), "payer_group": r.get("payer_group"), "procedure_code": r.get("procedure_code"),
                       "service_month": str(r.get("service_date") or "")[:7], "first_flagged": r.get("F"), "gone_on": r.get("gone_on"),
                       "flagged_cents": r.get("flagged_cents")}
    detail = {}
    for f, it in att["items"].items():
        detail[f] = {"a": it["a"], "b": it["b"], "c": it["c"], "returned": it["returned"], "taken_back": it["taken_back"],
                     "F": it["F"], "members": it["members"],
                     "events": [{k: e.get(k) for k in ("date", "cents", "counted_cents", "bucket", "kind", "source", "rule", "work_kind")}
                                for e in it["events"]]}
    payload = {"version": VERSION, "at": _now(), "today": t, "money": money, "lift": lf, "work": work, "outcomes": outcomes,
               "hours_counts": hours, "ai_calls_by_month": _ai_calls(conn), "lost": lost, "denials": denials,
               "credits": _credits(conn, t), "registry": {"items": len(reg), "gone": len(gone)},
               "candidates": att["candidates"], "newest_data": att["newest"], "errors": errors[:20],
               "counts": {k: len(v) for k, v in lists.items()}}
    return payload, {"lists": lists, "detail": detail, "gone": gone}


def _weeks_payload(payload: dict) -> None:
    """The scoreboard's week by week money (recovered by payment week, lost by deadline week) from the payload itself."""
    rec = {r["key"]: r["cents"] for r in payload["money"].get("by_week") or [] if r.get("key")}
    lost = {r["week"]: r["cents"] for r in payload["lost"].get("weeks") or []}
    weeks = sorted(set(rec) | set(lost))
    payload["weeks"] = [{"week": w, "recovered_cents": rec.get(w, 0), "lost_cents": lost.get(w, 0)} for w in weeks][-26:]


def snapshot(conn: sqlite3.Connection, payload: dict, live: dict, today: Any = None, settings: dict | None = None) -> dict:
    """Weekly snapshots (results_snapshots). The current week's row keeps what holds at a moment (items by state, money at
    risk) as of the newest run in the week; a week is frozen once it ended SETTLE_DAYS ago: its money figures are written
    with the settings in force, and a frozen week is never written again. Returns what it did."""
    ensure_schema(conn)
    t = _today(today)
    wk = week_of(t)
    did = {"week": wk, "point": False, "frozen": []}
    row = conn.execute("SELECT final, data FROM results_snapshots WHERE week=?", (wk,)).fetchone()
    point = {"at": _now(), "states": live.get("states") or {}, "at_risk": live.get("at_risk") or {}, "given_up": live.get("given_up") or {}}
    if row is None:
        conn.execute("INSERT INTO results_snapshots (week, at, final, version, data) VALUES (?,?,0,?,?)",
                     (wk, _now(), VERSION, json.dumps({"point": point}, separators=(",", ":"))))
        did["point"] = True
    elif not row[0]:
        data = _json(row[1])
        data["point"] = point
        conn.execute("UPDATE results_snapshots SET at=?, data=? WHERE week=? AND final=0", (_now(), json.dumps(data, separators=(",", ":")), wk))
        did["point"] = True
    money_week = {r["key"]: r for r in payload["money"].get("by_week") or []}
    lost_week = {r["week"]: r for r in payload["lost"].get("weeks") or []}
    for w, data_raw in conn.execute("SELECT week, data FROM results_snapshots WHERE final=0 AND week < ? ORDER BY week", (wk,)).fetchall():
        end = _add(w, 6)
        if not end or _add(end, SETTLE_DAYS) > t:
            continue
        data = _json(data_raw)
        m = money_week.get(w) or {}
        data.update(money={"returned_cents": m.get("cents", 0), "a_cents": m.get("a", 0), "b_cents": m.get("b", 0),
                           "c_cents": m.get("c", 0), "items": m.get("n", 0)},
                    lost={"n": (lost_week.get(w) or {}).get("n", 0), "cents": (lost_week.get(w) or {}).get("cents", 0)},
                    settings=(settings or {}).get("values"), frozen_at=_now(), rules_version=VERSION)
        conn.execute("UPDATE results_snapshots SET final=1, at=?, data=? WHERE week=? AND final=0",
                     (_now(), json.dumps(data, separators=(",", ":")), w))
        did["frozen"].append(w)
    return did


def snapshots(conn: sqlite3.Connection) -> list[dict]:
    if not _table(conn, "results_snapshots"):
        return []
    return [{"week": w, "at": at, "final": bool(fin), "version": v, **_json(d)}
            for w, at, fin, v, d in conn.execute("SELECT week, at, final, version, data FROM results_snapshots ORDER BY week")]


def baseline(conn: sqlite3.Connection, live: dict, today: Any = None) -> dict | None:
    """The "before", frozen on the first run after the release: the worklist on that day (items and dollars by state) and
    the self-audit's counts when the run has them. Never written again. Returns it when it was written now."""
    if _meta(conn, BASELINE_KEY, None):
        return None
    t = _today(today)
    base = {"at": _now(), "day": t, "version": VERSION, "states": live.get("states") or {}, "at_risk": live.get("at_risk"),
            "given_up": live.get("given_up")}
    audit = _meta(conn, "selfcheck:audit", None)
    if isinstance(audit, dict):
        base["self_audit"] = {k: audit.get(k) for k in ("coverage", "precision", "matrix", "at") if k in audit}
    _set_meta(conn, BASELINE_KEY, base)
    return base


def refresh(conn: sqlite3.Connection, *, today: Any = None, run_id: Any = None, host: str | None = None, state: Any = None,
            force: bool = False, log: Callable | None = None) -> dict:
    """The run's step (both hosts, after detection saved its items): bring in what the page saved in the state folder
    (serverless), update the registry, work out every figure and the lists behind them, take the week's snapshot, freeze
    the "before" on the first run. Does nothing when nothing it rests on changed (data_rev). Returns {'changed', ...}."""
    import time
    t0 = time.time()
    t = _today(today)
    ensure_schema(conn)
    out: dict = {"changed": False, "host": host, "run_id": run_id}
    if state is not None:
        out["merged"] = merge_state(conn, state)
    rev = data_rev(conn, t)
    dash = _meta(conn, DASH_KEY, None)
    if not force and isinstance(dash, dict) and dash.get("rev") == rev:
        out["skipped"] = "nothing changed"
        return out
    errors: list = []
    out["registry"] = update_registry(conn, t)
    payload, lists = compute(conn, t, errors=errors)
    live = live_figures(conn, t)
    _weeks_payload(payload)
    payload.update(rev=rev, run_id=run_id, host=host, seconds=round(time.time() - t0, 1))
    _set_meta(conn, DASH_KEY, payload)
    _set_meta(conn, LISTS_KEY, lists)
    out["snapshot"] = snapshot(conn, payload, live, t, read_settings(conn))
    b = baseline(conn, live, t)
    out["baseline"] = bool(b)
    conn.commit()
    out.update(changed=True, seconds=round(time.time() - t0, 1), returned_cents=payload["money"]["returned_cents"],
               items=payload["money"]["items"], candidates=payload["candidates"], errors=errors[:5])
    if log:
        log(f"results: {payload['money']['items']} items with money returned, {payload['candidates']} looked at, "
            f"{out['seconds']} s" + (f", {len(errors)} errors" if errors else ""))
    return out


def merge_state(conn: sqlite3.Connection, state: Any) -> dict:
    """Serverless: copy what the page saved in the state folder into the store (the owner's figures into meta, the spot
    checks into spot_checks), so the run's figures include them. Newest answer wins; a pick is never removed."""
    ensure_schema(conn)
    out = {"settings": False, "spot": 0}
    raw = state.get(STATE_SETTINGS)
    if raw:
        rec = _json(raw.decode() if isinstance(raw, bytes) else raw)
        if rec.get("values") and rec != _meta(conn, SETTINGS_KEY, {}):
            _set_meta(conn, SETTINGS_KEY, rec)
            out["settings"] = True
    for m in state.list("results/"):
        name = m.get("name") or ""
        if not re.fullmatch(r"results/spot_checks_\d{4}\.json", name):
            continue
        data = _json((state.get(name) or b"{}").decode())
        for day, rec in (data.get("days") or {}).items():
            for p in rec.get("picks") or []:
                out["spot"] += _upsert_spot(conn, day, p, (rec.get("answers") or {}).get(p.get("finding_id")))
    return out


def _upsert_spot(conn: sqlite3.Connection, day: str, pick: dict, ans: dict | None) -> int:
    fid = pick.get("finding_id")
    if not fid:
        return 0
    conn.execute("""INSERT OR IGNORE INTO spot_checks (day, finding_id, stratum, picked_at, state, signal, payer_group, cents, basis)
                    VALUES (?,?,?,?,?,?,?,?,?)""", (day, fid, pick.get("stratum"), pick.get("picked_at"), pick.get("state"),
                                                    pick.get("signal"), pick.get("payer_group"), pick.get("cents"), pick.get("basis")))
    if ans and ans.get("answer") in SPOT_ANSWERS:
        cur = conn.execute("SELECT answered_at FROM spot_checks WHERE day=? AND finding_id=?", (day, fid)).fetchone()
        if not cur or not cur[0] or str(cur[0]) < str(ans.get("answered_at") or ""):
            conn.execute("UPDATE spot_checks SET answer=?, reason=?, note=?, who=?, answered_at=? WHERE day=? AND finding_id=?",
                         (ans.get("answer"), ans.get("reason"), ans.get("note"), ans.get("who"), ans.get("answered_at"), day, fid))
            return 1
    return 0


# ------------------------------------------------------------------------------------------------------------ reading
def dashboard(conn: sqlite3.Connection) -> dict | None:
    """What the run stored (both hosts), or None before the first run that worked it out."""
    d = _meta(conn, DASH_KEY, None)
    return d if isinstance(d, dict) and d.get("version") else None


def stale(conn: sqlite3.Connection, today: Any = None) -> bool:
    """Whether the stored figures rest on older data than the store holds now (a server works them out again then)."""
    d = dashboard(conn)
    return d is None or d.get("rev") != data_rev(conn, today)


def hours_saved(counts: dict, settings: dict) -> dict:
    """The estimate (labelled as one): each count times the minutes the owner set for it."""
    mins = (settings or {}).get("values", {}).get("minutes") or DEFAULT_SETTINGS["minutes"]
    rows = []
    total = 0.0
    for k in ("investigate", "packet", "fax", "duplicate"):
        n = int((counts or {}).get(k) or 0)
        h = n * float(mins.get(k) or 0) / 60
        total += h
        rows.append({"key": k, "label": MINUTE_LABELS[k], "n": n, "minutes": mins.get(k), "hours": round(h, 1)})
    return {"rows": rows, "hours": round(total, 1)}


def roi(payload: dict, settings: dict, today: Any = None) -> dict:
    """Cost and ROI by month with the owner's costs: hosting and other costs, fax pages at their price, AI calls at theirs.
    ROI = the month's money returned over its cost (the headline); beside it the lift-adjusted ROI: (a) scaled by the share
    of the worked items' money that the unworked rate does not explain, plus (b), over the cost."""
    t = _today(today)
    costs = (settings or {}).get("values", {}).get("costs") or DEFAULT_SETTINGS["costs"]
    money = payload.get("money") or {}
    lf = payload.get("lift") or {}
    w_ret = (lf.get("worked") or {}).get("returned_cents") or 0
    factor = max(0.0, min(1.0, (lf.get("incremental_cents") or 0) / w_ret)) if w_ret > 0 else 0.0
    pages = (payload.get("work") or {}).get("fax_pages_by_month") or {}
    ai = payload.get("ai_calls_by_month") or {}
    by_month = {r["key"]: r for r in money.get("by_month") or []}
    first = (money.get("first_flag") or t)[:7]
    months, m = [], first
    while m <= t[:7] and len(months) < 36:
        months.append(m)
        y, mo = int(m[:4]), int(m[5:7]) + 1
        m = f"{y + (mo > 12)}-{(mo - 1) % 12 + 1:02d}"
    rows = []
    fixed = round((float(costs.get("hosting_month") or 0) + float(costs.get("other_month") or 0)) * 100)
    for m in months[-24:]:
        r = by_month.get(m) or {}
        cost = fixed + round(pages.get(m, 0) * float(costs.get("fax_page") or 0) * 100) + round(ai.get(m, 0) * float(costs.get("ai_call") or 0) * 100)
        top = int(r.get("cents") or 0)
        cons = int(round((r.get("a") or 0) * factor + (r.get("b") or 0)))
        rows.append({"month": m, "returned_cents": top, "conservative_cents": cons, "cost_cents": cost, "fax_pages": pages.get(m, 0),
                     "ai_calls": ai.get(m, 0), "roi": round(top / cost, 2) if cost > 0 else None,
                     "roi_conservative": round(cons / cost, 2) if cost > 0 else None})
    tot_cost = sum(r["cost_cents"] for r in rows)
    tot = sum(r["returned_cents"] for r in rows)
    tot_cons = sum(r["conservative_cents"] for r in rows)
    return {"months": rows, "cost_cents": tot_cost, "returned_cents": tot, "conservative_cents": tot_cons,
            "roi": round(tot / tot_cost, 2) if tot_cost > 0 else None, "roi_conservative": round(tot_cons / tot_cost, 2) if tot_cost > 0 else None,
            "factor": round(factor, 3), "costs_set": tot_cost > 0,
            "basis": ("ROI is the money returned over what RemitWatch cost that month. The lift-adjusted ROI counts only the share of "
                      "the money after RemitWatch work that similar unworked items do not explain, plus the money found.")}


# ------------------------------------------------------------------------------------------------------------- lists
def list_entries(conn: sqlite3.Connection, key: str, today: Any = None, lists: dict | None = None) -> list[list]:
    """The items behind one figure: [[finding id, cents], ...] in the order shown. Live keys are counted on the findings
    table now; every other key is the run's own list."""
    t = _today(today)
    if key in LIVE_KEYS or key == "hours:investigate":
        w, p = live_sql(key, t)
        col = "f.written_off_cents" if key.startswith("given_up") else "f.shortfall_cents"
        order = "f.appeal_deadline, f.finding_id" if key == "at_risk" else f"COALESCE({col},0) DESC, f.finding_id"
        return [[r[0], int(r[1] or 0)] for r in conn.execute(f"SELECT f.finding_id, {col} FROM findings f WHERE {w} ORDER BY {order}", p)]
    got = lists if lists is not None else _meta(conn, LISTS_KEY, {}) or {}
    if key not in (got.get("lists") or {}):
        raise KeyError(key)
    return got["lists"][key]


def list_rows(conn: sqlite3.Connection, key: str, *, today: Any = None, page: int = 0, per: int = 50, show_phi: bool = False) -> dict:
    """One page of the items behind a figure, with what each brought back and why: {'key', 'total', 'items': [...]}.
    Items no longer listed show what the registry kept of them."""
    t = _today(today)
    stored = _meta(conn, LISTS_KEY, {}) or {}
    entries = list_entries(conn, key, t, stored)
    total = len(entries)
    per = max(1, min(int(per or 50), 500))
    page = max(0, int(page or 0))
    part = entries[page * per:(page + 1) * per]
    ids = [e[0] for e in part]
    found: dict[str, dict] = {}
    if ids and _table(conn, "findings"):
        fc = _cols(conn, "findings")
        sel = [c for c in ("finding_id", "signal", "category", "payer_group", "procedure_code", "service_date", "shortfall_cents",
                           "work_state", "status", "appeal_deadline", "state_basis", "patient_control_number", "first_seen") if c in fc]
        _temp_ids(conn, "rs_lr", ids)
        for r in conn.execute(f"SELECT {', '.join(sel)} FROM findings WHERE finding_id IN (SELECT id FROM temp.rs_lr)"):
            found[r[0]] = dict(zip(sel, r))
        _drop(conn, "rs_lr")
    labels = _labels()
    detail = stored.get("detail") or {}
    gone = stored.get("gone") or {}
    rows = []
    for fid, cents in part:
        f = found.get(fid)
        d = detail.get(fid) or {}
        g = gone.get(fid) or {}
        row = {"finding_id": fid, "cents": cents, "listed": f is not None}
        src = f or g
        sig = src.get("signal")
        row.update(signal=sig, label=labels.get(sig or "", sig or ""), payer_group=src.get("payer_group"), procedure_code=src.get("procedure_code"),
                   service_date=(f or {}).get("service_date") or g.get("service_month"), work_state=(f or {}).get("work_state"),
                   status=(f or {}).get("status"), appeal_deadline=(f or {}).get("appeal_deadline"),
                   state_basis=((f or {}).get("state_basis") or "")[:200], first_flagged=d.get("F") or g.get("first_flagged"),
                   shortfall_cents=(f or {}).get("shortfall_cents", g.get("flagged_cents")))
        if d:
            row.update(a=d.get("a"), b=d.get("b"), c=d.get("c"), returned=d.get("returned"), taken_back=d.get("taken_back"),
                       events=d.get("events") or [], members=d.get("members") or [])
        if show_phi and f:
            row["claim_id"] = f.get("patient_control_number")
        rows.append(row)
    return {"key": key, "total": total, "page": page, "per": per, "items": rows}


CSV_COLUMNS = ("finding_id", "label", "signal", "payer_group", "procedure_code", "service_date", "first_flagged", "work_state", "status",
               "cents", "a", "b", "c", "taken_back", "rule")


def list_csv(conn: sqlite3.Connection, key: str, *, today: Any = None, show_phi: bool = False) -> str:
    """Every item behind a figure as CSV (no patient details; the claim id only when the names switch is on)."""
    import csv
    import io
    buf = io.StringIO()
    cols = list(CSV_COLUMNS) + (["claim_id"] if show_phi else [])
    w = csv.writer(buf)
    w.writerow(cols)
    got = list_rows(conn, key, today=today, page=0, per=500, show_phi=show_phi)
    pages = (got["total"] + 499) // 500
    for pg in range(max(pages, 1)):
        part = got if pg == 0 else list_rows(conn, key, today=today, page=pg, per=500, show_phi=show_phi)
        for r in part["items"]:
            rule = "; ".join(e.get("rule") or "" for e in (r.get("events") or [])[:3])
            vals = dict(r, rule=rule)
            w.writerow([_csv_safe(vals.get(c)) for c in cols])
    return buf.getvalue()


def _csv_safe(v: Any) -> Any:
    """A cell a spreadsheet would read as a formula starts with an apostrophe."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return "" if v is None else v


# -------------------------------------------------------------------------------------------------------- scoreboard
def scoreboard(conn: sqlite3.Connection, today: Any = None) -> dict:
    """Today's money scoreboard: recovered (the top line, to date), lost to deadlines, at risk in the next 14 days and given
    up without a decision, week by week. Recovered and lost come from the run's figures; at risk and given up are counted
    now on the findings table. A figure the run has not worked out yet is None (the page shows a dash)."""
    t = _today(today)
    d = dashboard(conn)
    live = live_figures(conn, t)
    gu = live.get("given_up_selfcheck") or live.get("given_up") or {}
    out = {"recovered_cents": d["money"]["returned_cents"] if d else None, "lost_cents": d["lost"]["cents"] if d else None,
           "at_risk_cents": live["at_risk"]["cents"], "given_up_cents": gu.get("cents"), "as_of": (d or {}).get("at"),
           "today": t, "counts": {"recovered": d["money"]["items"] if d else None, "lost": d["lost"]["n"] if d else None,
                                  "at_risk": live["at_risk"]["n"], "given_up": gu.get("n")},
           "basis": {"recovered": "Money paid on items after RemitWatch first flagged them, plus money it found (some may have come back anyway: see Results).",
                     "lost": "Items that expired while they were in Work now, with their dollars.",
                     "at_risk": f"Work now items whose filing date is in the next {AT_RISK_DAYS} days.",
                     "given_up": (gu.get("basis") or "Dollars written off with no person's decision and no owner-approved policy.")},
           "given_up_source": gu.get("source")}
    weeks: dict = {}
    for r in (d or {}).get("weeks") or []:
        weeks[r["week"]] = {"week": r["week"], "recovered_cents": r.get("recovered_cents", 0), "lost_cents": r.get("lost_cents", 0),
                            "at_risk_cents": None, "given_up_cents": 0}
    for s in snapshots(conn):
        pt = s.get("point") or {}
        w = weeks.setdefault(s["week"], {"week": s["week"], "recovered_cents": 0, "lost_cents": 0, "at_risk_cents": None, "given_up_cents": 0})
        if pt.get("at_risk"):
            w["at_risk_cents"] = pt["at_risk"].get("cents")
        if s.get("final") and s.get("money"):
            w["recovered_cents"] = s["money"].get("returned_cents", w["recovered_cents"])
            w["lost_cents"] = (s.get("lost") or {}).get("cents", w["lost_cents"])
            w["frozen"] = True
    for r in _given_up_weeks(conn, t):
        w = weeks.setdefault(r["week"], {"week": r["week"], "recovered_cents": 0, "lost_cents": 0, "at_risk_cents": None, "given_up_cents": 0})
        w["given_up_cents"] = r["cents"]
    cur = week_of(t)
    w = weeks.setdefault(cur, {"week": cur, "recovered_cents": 0, "lost_cents": 0, "at_risk_cents": None, "given_up_cents": 0})
    w["at_risk_cents"] = live["at_risk"]["cents"]
    out["weeks"] = [weeks[k] for k in sorted(weeks)][-26:]
    return out


# ------------------------------------------------------------------------------------------------------- spot checks
def _salt(conn: sqlite3.Connection | None, state: Any = None) -> str:
    s = _meta(conn, SALT_KEY, None) if conn is not None else None
    if isinstance(s, str) and s:
        return s
    if conn is not None and state is None:
        try:
            s = secrets.token_hex(8)
            _set_meta(conn, SALT_KEY, s)
            conn.commit()
            return s
        except sqlite3.Error:
            pass
    return "remitwatch-spot"


def spot_candidates(conn: sqlite3.Connection, day: str, recent_ids: set) -> dict[str, list[dict]]:
    """The items each stratum may pick from: open items in Work now, items Waiting, items Done (proved settled while
    still open), and items RemitWatch closed on a payment in the last 30 days. Never one checked in the last 90 days."""
    out: dict[str, list[dict]] = {k: [] for k, _n in SPOT_STRATA}
    if not _table(conn, "findings") or "work_state" not in _cols(conn, "findings"):
        return out
    since = _add(day, -SPOT_RECENT_DAYS)
    scope = ",".join(f"'{c}'" for c in RETURN_CATEGORIES)
    conds = {"work_now": "f.work_state='work_now' AND f.status IN ('open','in_progress')",
             "waiting": "f.work_state='waiting'",
             "done": "f.work_state='done' AND f.status='open'",
             "closed_auto": "f.status='resolved' AND f.auto_note IS NOT NULL AND f.status_changed_at >= ?"}
    for k, cond in conds.items():
        params = [since] if k == "closed_auto" else []
        for fid, sig, grp, ws, cents, basis in conn.execute(
                f"""SELECT f.finding_id, f.signal, f.payer_group, f.work_state, f.shortfall_cents, f.state_basis FROM findings f
                    WHERE f.category IN ({scope}) AND COALESCE(f.stale,0)=0 AND {cond}""", params):
            if fid not in recent_ids:
                out[k].append({"finding_id": fid, "signal": sig, "payer_group": grp, "state": ws, "cents": int(cents or 0),
                               "basis": str(basis or "")[:300]})
    return out


def pick_spot_checks(conn: sqlite3.Connection, day: str, salt: str, recent_ids: set) -> list[dict]:
    """The day's picks, at random but the same for every instance on the same data: each candidate is ranked by a hash of
    the salt, the day and its finding id; each stratum takes its share and a stratum with too few gives its places to
    the next."""
    cands = spot_candidates(conn, day, recent_ids)

    def rank(fid: str) -> str:
        return hashlib.sha256(f"{salt}|{day}|{fid}".encode()).hexdigest()
    for k in cands:
        cands[k].sort(key=lambda c: rank(c["finding_id"]))
    picks, taken = [], set()
    short = 0
    for k, n in SPOT_STRATA:
        got = [c for c in cands[k] if c["finding_id"] not in taken][:n]
        short += n - len(got)
        for c in got:
            taken.add(c["finding_id"])
            picks.append(dict(c, stratum=k))
    for k, _n in SPOT_STRATA:
        if short <= 0:
            break
        more = [c for c in cands[k] if c["finding_id"] not in taken][:short]
        for c in more:
            taken.add(c["finding_id"])
            picks.append(dict(c, stratum=k))
        short -= len(more)
    at = _now()
    return [dict(p, picked_at=at) for p in picks[:SPOT_PER_DAY]]


def spot_day(conn: sqlite3.Connection, day: Any = None) -> list[dict]:
    """Server: the day's picks with their answers, picked and kept on the first ask of the day."""
    ensure_schema(conn)
    d = _today(day)
    rows = _spot_rows(conn, d)
    if rows:
        return rows
    recent = {r[0] for r in conn.execute("SELECT finding_id FROM spot_checks WHERE day >= ?", (_add(d, -SPOT_REPEAT_DAYS),))}
    picks = pick_spot_checks(conn, d, _salt(conn), recent)
    conn.execute("BEGIN IMMEDIATE")
    try:
        if not conn.execute("SELECT 1 FROM spot_checks WHERE day=? LIMIT 1", (d,)).fetchone():
            for p in picks:
                _upsert_spot(conn, d, p, None)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return _spot_rows(conn, d)


def _spot_rows(conn: sqlite3.Connection, day: str) -> list[dict]:
    cols = ["day", "finding_id", "stratum", "picked_at", "state", "signal", "payer_group", "cents", "basis", "answer", "reason", "note",
            "who", "answered_at"]
    return [dict(zip(cols, r)) for r in conn.execute(f"SELECT {', '.join(cols)} FROM spot_checks WHERE day=? ORDER BY rowid", (day,))]


def _check_answer(answer: Any, reason: Any, note: Any) -> tuple[str, str | None, str]:
    a = str(answer or "")
    if a not in SPOT_ANSWERS:
        raise ResultsError("Answer agree or disagree.")
    r = str(reason or "") or None
    if a == "disagree" and r not in SPOT_REASONS:
        raise ResultsError("Pick what is wrong: already worked, already paid, not paid, wrong amount, wrong deadline or other.")
    if a == "agree":
        r = None
    n = str(note or "").strip()[:500]
    if a == "disagree" and r == "other" and not n:
        raise ResultsError("Say in a few words what is wrong.")
    return a, r, n


def record_spot_check(conn: sqlite3.Connection, day: str, finding_id: str, answer: str, reason: str | None, note: str | None,
                      who: str) -> dict:
    """Server: a person's answer on one of the day's picks (agree, or disagree with the reason), with who and when. A
    newer answer replaces an older one."""
    a, r, n = _check_answer(answer, reason, note)
    ensure_schema(conn)
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT 1 FROM spot_checks WHERE day=? AND finding_id=?", (day, finding_id)).fetchone()
        if not row:
            raise ResultsError("That item is not one of the spot checks of that day.", 404)
        at = _now()
        conn.execute("UPDATE spot_checks SET answer=?, reason=?, note=?, who=?, answered_at=? WHERE day=? AND finding_id=?",
                     (a, r, n or None, (who or "someone")[:80], at, day, finding_id))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return {"ok": True, "answered_at": at}


def _state_spot_name(day: str) -> str:
    return STATE_SPOT.format(year=day[:4])


def _read_state_spot(state: Any, year_day: str) -> tuple[dict, dict | None]:
    name = _state_spot_name(year_day)
    meta = state.meta(name)
    raw = state.get(name) if meta else None
    return (_json(raw.decode() if isinstance(raw, bytes) else raw) if raw else {}), meta


def _write_state_spot(state: Any, day: str, change: Callable[[dict], dict | None], tries: int = 6) -> dict:
    """Read the year's file, apply one change, write it only if nobody wrote in between (else read again and retry)."""
    from .state import Conflict
    name = _state_spot_name(day)
    for _i in range(tries):
        data, meta = _read_state_spot(state, day)
        data.setdefault("days", {})
        new = change(data)
        if new is None:
            return data
        blob = json.dumps(new, separators=(",", ":")).encode()
        try:
            if meta:
                state.put(name, blob, mode="update", rev=meta["rev"])
            else:
                state.put(name, blob, mode="add")
            return new
        except Conflict:
            continue
    raise ResultsError("Someone else saved at the same moment several times. Press the button again.", 409)


def spot_day_state(conn: sqlite3.Connection, state: Any, day: Any = None) -> list[dict]:
    """Serverless: the day's picks with their answers, kept in the state folder; picked from the dashboard copy on the
    first ask of the day (every instance picks the same items from the same copy; the first one written wins)."""
    d = _today(day)
    data, _meta_ = _read_state_spot(state, d)
    rec = (data.get("days") or {}).get(d)
    if not rec:
        recent = set()
        for y in {d[:4], _add(d, -SPOT_REPEAT_DAYS)[:4]}:
            old, _m = _read_state_spot(state, y + "-01-01")
            for od, orec in (old.get("days") or {}).items():
                if od >= _add(d, -SPOT_REPEAT_DAYS):
                    recent.update(p.get("finding_id") for p in orec.get("picks") or [])
        picks = pick_spot_checks(conn, d, _salt(conn, state), recent)

        def add(data: dict) -> dict | None:
            if d in data["days"]:
                return None
            data["days"][d] = {"picks": picks, "answers": {}}
            return data
        data = _write_state_spot(state, d, add)
        rec = data["days"][d]
    answers = rec.get("answers") or {}
    return [dict(p, day=d, **{k: (answers.get(p["finding_id"]) or {}).get(k) for k in ("answer", "reason", "note", "who", "answered_at")})
            for p in rec.get("picks") or []]


def record_spot_check_state(state: Any, day: str, finding_id: str, answer: str, reason: str | None, note: str | None, who: str) -> dict:
    """Serverless: a person's answer, written into the year's file in the state folder (two people at once: both kept,
    the newer answer for the same item wins)."""
    a, r, n = _check_answer(answer, reason, note)
    at = _now()

    def put(data: dict) -> dict | None:
        rec = data["days"].get(day)
        if not rec or finding_id not in {p.get("finding_id") for p in rec.get("picks") or []}:
            raise ResultsError("That item is not one of the spot checks of that day.", 404)
        rec.setdefault("answers", {})[finding_id] = {"answer": a, "reason": r, "note": n or None, "who": (who or "someone")[:80],
                                                     "answered_at": at}
        return data
    _write_state_spot(state, day, put)
    return {"ok": True, "answered_at": at}


def spot_rows_all(conn: sqlite3.Connection | None = None, state: Any = None, today: Any = None, years: int = 2) -> list[dict]:
    """Every pick with its answer: the store's table (a server, and a serverless run after merge_state), or the state
    folder's files (a serverless page)."""
    rows: list[dict] = []
    if state is not None:
        y = int(_today(today)[:4])
        for yr in range(y - years + 1, y + 1):
            data, _m = _read_state_spot(state, f"{yr}-01-01")
            for d, rec in sorted((data.get("days") or {}).items()):
                ans = rec.get("answers") or {}
                for p in rec.get("picks") or []:
                    rows.append(dict(p, day=d, **{k: (ans.get(p["finding_id"]) or {}).get(k)
                                                  for k in ("answer", "reason", "note", "who", "answered_at")}))
        return rows
    if conn is None or not _table(conn, "spot_checks"):
        return rows
    cols = ["day", "finding_id", "stratum", "state", "signal", "payer_group", "cents", "basis", "answer", "reason", "note", "who", "answered_at"]
    return [dict(zip(cols, r)) for r in conn.execute(f"SELECT {', '.join(cols)} FROM spot_checks ORDER BY day, rowid")]


def spot_summary(rows: list[dict]) -> dict:
    """The agreement rate overall, by stratum, by signal and week by week (answered picks only), and every disagreement."""
    answered = [r for r in rows if r.get("answer") in SPOT_ANSWERS]

    def rate(rs):
        n = len(rs)
        k = sum(1 for r in rs if r["answer"] == "agree")
        return {"n": n, "agree": k, "rate": _pct(k, n)}
    by = lambda key: [dict(key=k, **rate(v)) for k, v in sorted(_group(answered, key).items())]      # noqa: E731
    out = {"picked": len(rows), "answered": len(answered), **rate(answered),
           "by_stratum": [dict(r, label=STRATUM_LABELS.get(r["key"], r["key"])) for r in by(lambda r: r.get("stratum") or "")],
           "by_signal": by(lambda r: r.get("signal") or ""), "by_week": by(lambda r: week_of(r.get("day"))),
           "disagreements": [{"day": r.get("day"), "finding_id": r.get("finding_id"), "stratum": r.get("stratum"), "signal": r.get("signal"),
                              "payer_group": r.get("payer_group"), "reason": r.get("reason"), "reason_label": SPOT_REASONS.get(r.get("reason") or "", ""),
                              "note": r.get("note"), "who": r.get("who"), "answered_at": r.get("answered_at"), "basis": r.get("basis")}
                             for r in answered if r["answer"] == "disagree"][-200:]}
    return out


def _group(rows: list[dict], key: Callable) -> dict:
    out: dict = defaultdict(list)
    for r in rows:
        out[key(r)].append(r)
    return out


def spot_check_cases(conn: sqlite3.Connection, days: int = 90, today: Any = None) -> list[dict]:
    """The disagreements of the last `days` days for the diagnostic bundle (unit H): finding id, day, stratum, the state
    and rule RemitWatch held when it was picked, the reason given. The typed note is left out (it is free text a person
    wrote and may name a patient); only its length is given."""
    since = _add(_today(today), -days)
    out = []
    for r in spot_rows_all(conn):
        if r.get("answer") == "disagree" and (r.get("day") or "") >= (since or ""):
            out.append({"finding_id": r["finding_id"], "day": r["day"], "stratum": r.get("stratum"), "state": r.get("state"),
                        "signal": r.get("signal"), "payer_group": r.get("payer_group"), "reason": r.get("reason"),
                        "basis": r.get("basis"), "note_chars": len(r.get("note") or ""), "answered_at": r.get("answered_at")})
    return out


# ------------------------------------------------------------------------------------------------------------- email
def email_lines(conn: sqlite3.Connection, today: Any = None) -> list[str]:
    """A few plain lines for the weekly email: the top line with its split and qualifier, lost and at risk, and how the
    spot checks agree."""
    d = dashboard(conn)
    if not d:
        return []
    m = d["money"]
    live = live_figures(conn, today)
    lines = [f"Money returned on claims RemitWatch flagged or worked: {usd(m['returned_cents'])} on {m['items']} items "
             f"(after RemitWatch work {usd(m['a_cents'])}, found by RemitWatch {usd(m['b_cents'])}, flagged with no recorded work "
             f"{usd(m['c_cents'])})."]
    if m.get("anyway", {}).get("cents") is not None:
        lines.append(f"About {usd(m['anyway']['cents'])} of this may have come back anyway: similar items with no work were paid "
                     f"at {round((m['anyway']['rate'] or 0) * 100)}% ({m['anyway']['n']} items).")
    lines.append(f"Lost to deadlines: {usd(d['lost']['cents'])} on {d['lost']['n']} items. At risk in the next {AT_RISK_DAYS} days: "
                 f"{usd(live['at_risk']['cents'])} on {live['at_risk']['n']} items.")
    s = spot_summary(spot_rows_all(conn))
    if s["answered"]:
        lines.append(f"Spot checks: people agreed with RemitWatch on {s['agree']} of {s['answered']} items checked.")
    return lines
