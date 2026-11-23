"""
Retention and the cold archive (amendment 11.3): old, settled data leaves the store the daily run loads, so the run's
memory stays flat as the years add up, and nothing is lost.

What stays in the store (hot): every bill with an open item (work now, waiting, can't be checked, or a person's open,
started or sent status), every bill a worked or open routine-rate pattern lists, every bill reopened in the last 90 days,
and everything with service inside the retention horizon. What leaves (to compressed archive files the daily run never
reads): a bill whose newest service is older than the horizon and whose items are all settled (done, accepted, expired, or
closed by a person): its 835 claims, lines, adjustments and remarks, its weekly EMA export rows, its 837 rows, its links,
its items and their history. The A/R snapshots older than 13 months go the same way, and the posting ledger's postings go
to the archive before the ledger's own retention deletes them (`before_ledger_retain`, the hook in Ledger.retain).

Never archived: the remittances themselves (transactions and provider-level adjustments stay: they are the payments by
month, and their content fingerprints stop a re-delivered remittance from being read twice), the files and Dropbox lists,
faxes, packets, notes, hide rules, alerts and every summary table. The archive is not "the 835 archive" the rules read
(remitwatch.archive_rules): that is the hot store's remittance history.

Where the archive lives:
  server      a folder beside the store (REMITWATCH_ARCHIVE_DIR, else <store dir>/archive, so /data/archive), files 0600
  serverless  the state folder's archive/ (the practice's Dropbox), through the state interface

Files (gzipped JSON lines, the bytes fixed by their content, never replaced, never deleted: EMA and TriZetto stay the
records of truth):
  bills/<year>/b-<sha>.jsonl.gz       one line per bill: every row of the tables above, column names with the values
  ar/<as_of>-<sha>.jsonl.gz           one A/R snapshot (its rows and its catalog row)
  ledger/<lo>_<hi>-<sha>.jsonl.gz     ledger postings by EMA claim number, names written out, before retention deletes them

The store keeps a small index: archive_files (each file, its digest and counts), archive_bills (bill -> file),
archive_ids (claim ids and payer claim numbers -> bill, for Find a claim), archive_findings (each archived item's summary,
so totals and deep links still count it), archive_rates / archive_market (the payer baselines of the years the archive
took, measured on the whole year), archive_pins (bills reopened lately) and archive_requests (reopen requests waiting for
the next run).

Safety, in this order: a batch is written, read back and checked record by record (digest and row counts) before one
transaction moves its index rows in and its hot rows out; inside that transaction the items and their history are checked
again (a person who changed an item meanwhile keeps the bill hot); the counts deleted must equal the counts archived or
nothing is deleted. A run that dies anywhere leaves either the rows hot (and at most an unreferenced archive file, which
the next run writes again with the same name) or the rows archived and indexed. A second run with nothing new changes
nothing.

The baselines: payer rates are measured per service year, and a year the archive took part of would be measured again on
what is left (mostly the bills of open items), which could make an underpaid line look normal. So detection keeps, for
every year old enough to be archived, a snapshot of the year's rates (`apply_frozen_rates`, a hook in
Detector.build_baselines); once the archive takes lines of a year, that year is locked and detection uses the snapshot.
The archive never takes lines of a year that has no snapshot.

Public names:
  settings(env=None, today=None) / horizon_months / budget_mb / archive_dir / sink_for / DirSink / StateSink
  run(conn, *, horizon=None, budget_state=None, sink=None, ...)       one retention pass; returns what it did
  plan(conn, *, horizon=None, today=None, ...)                           what run would take (nothing written)
  lookup_claim(conn, claim_or_bill, *, sink) / claim_db(...) / search(conn, text) / lookup_finding(...)
  restore(conn, bill, *, sink, ...) / request_restore / journal_entry / take_journal / process_requests / restore_touched
  before_ledger_retain(ledger, cutoff) / ledger_postings(sink, links, ...)
  apply_frozen_rates(det)                                                the detection hook
  measure(conn, ...) / record_run(conn, ...) / history(conn) / data_health(conn) / panel(conn, state=None) / compact(conn)

Nothing here reads the clock or the environment except through `_today` and `settings`; every function a run calls takes
the run's context (conn, run_id, today, the sink) and returns what it did. No log line or returned summary holds a claim
id, a name or any cell of a row: counts, dollars, years and file names only.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import os
import re
import sqlite3
import tempfile
import threading
import time
import zlib
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Iterator, Sequence

# ------------------------------------------------------------------------------------------------------------ constants
FORMAT = "remitwatch-archive"
FORMAT_VERSION = 1
RULE = "archive.retire"             # the rule id an archived item's history names
RULE_VERSION = "1"
DEFAULT_HORIZON_MONTHS = 36         # REMITWATCH_RETENTION_MONTHS (owner decision, amendment 11.3)
MIN_HORIZON_MONTHS = 24             # never shorter: the payers' longest windows (two years) and the answer-time measure
                                    # (states.RESPONSE_YEARS) need two years of remittances; open items stay whatever the age
TIGHTEN_STEP_MONTHS = 6             # a run over WARN_SHARE of its memory budget takes this much more, down to the floor
AR_MONTHS = 13                      # A/R snapshots older than this go to the archive
SERVERLESS_BUDGET_MB = 1600         # of Vercel's 2 GB function (amendment 11.3)
SERVER_BUDGET_SHARE = 0.80          # a server's budget: this share of its memory limit (REMITWATCH_MEMORY_BUDGET_MB overrides)
WARN_SHARE = 0.80                   # Data health warns at this share of the budget
RELAX_SHARE = 0.60                  # a tightened horizon relaxes a step when a run stays under this share
BATCH_BILLS = 1500                  # bills per archive file
BATCH_ROWS = 60_000                 # ... and at most about this many rows
PIN_DAYS = 90                       # a reopened bill stays hot this long
HISTORY_KEPT = 120                  # runs kept in meta archive:history
COMPACT_FREE_SHARE = 0.10           # compact() vacuums when this share of the pages is free
OPEN_STATES = ("work_now", "waiting", "cant_check")
CLOSED_STATES = ("done", "accepted", "expired")
ACTIVE_STATUSES = ("open", "in_progress", "appealed")
NAMED_FIELDS = ("kind", "status", "action", "payer", "plan", "primary_payer", "secondary_payer")   # ledger name ids

META_HORIZON = "archive:horizon"            # the horizon the last run used: {months, configured, why, at}
META_HISTORY = "archive:history"            # per run: hot size, peak memory against the budget, what was retired
META_SUMMARY = "archive:summary"            # the archive's totals (the dashboard's copy reads these)
META_LOCKED = "archive:locked_years"        # service years the archive took lines of: detection uses their snapshot
META_FROZEN = "archive:frozen_years"        # {year: {at, digest, rates}}: the years with a baseline snapshot
META_WHERE = "archive:where"                # where the archive lives, in words
META_DETECT_PENDING = "cloud:detect_pending"

# the tables a bill's record holds. MOVED rows leave the store; COPIED rows are a copy for the claim view and the story
MOVED = ("claims", "lines", "adjustments", "remarks", "line_links", "line_handling", "ema_rows", "claims_837", "lines_837",
         "findings", "finding_events")
COPIED = ("transactions", "ema_lines", "ema_line_payments", "ema_line_reasons", "ema_link_map", "ema_claim_links",
          "ema_ar", "ema_ar_items")
# copies put back on a reopen when the store no longer has them (summaries the ledger refresh keeps anyway); never the
# A/R rows (a later snapshot may have replaced them) nor the remittance envelope (it never left)
RESTORE_COPIES = ("ema_lines", "ema_line_payments", "ema_line_reasons", "ema_link_map", "ema_claim_links", "ema_ar_items")
SORT_KEYS = {"claims": ("claim_key",), "lines": ("line_key",), "adjustments": ("id",), "remarks": ("id",),
             "line_links": ("line_key",), "line_handling": ("id",), "ema_rows": ("row_fingerprint",),
             "claims_837": ("claim_837_key",), "lines_837": ("line_837_key",), "findings": ("finding_id",),
             "finding_events": ("id",), "transactions": ("transaction_key",), "ema_lines": ("link", "svc", "cpt"),
             "ema_line_payments": ("link", "svc", "cpt", "check_no", "posted", "payer"),
             "ema_line_reasons": ("link", "svc", "cpt", "grp", "code"), "ema_link_map": ("link",),
             "ema_claim_links": ("claim_id",), "ema_ar": ("file_id", "row_no"), "ema_ar_items": ("item_key",)}

DDL = """
CREATE TABLE IF NOT EXISTS archive_files (name TEXT PRIMARY KEY, kind TEXT, sha256 TEXT, gz_bytes INTEGER,
    raw_bytes INTEGER, records INTEGER, rows INTEGER, svc_lo TEXT, svc_hi TEXT, created_at TEXT, run_id TEXT,
    horizon_months INTEGER, cutoff TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS archive_bills (bill TEXT PRIMARY KEY, file TEXT, digest TEXT, svc_lo TEXT, svc_hi TEXT,
    claims INTEGER, lines INTEGER, findings INTEGER, archived_at TEXT, run_id TEXT, plb_seen INTEGER);
CREATE INDEX IF NOT EXISTS ix_archive_bills_file ON archive_bills(file);
CREATE TABLE IF NOT EXISTS archive_ids (id TEXT, kind TEXT, bill TEXT, PRIMARY KEY (id, kind, bill)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_archive_ids_bill ON archive_ids(bill);
CREATE TABLE IF NOT EXISTS archive_findings (finding_id TEXT PRIMARY KEY, bill TEXT, signal TEXT, category TEXT,
    status TEXT, work_state TEXT, payer_group TEXT, service_date TEXT, payment_date TEXT, appeal_deadline TEXT,
    shortfall_cents INTEGER, recovered_cents INTEGER, stale INTEGER, archived_at TEXT, file TEXT);
CREATE INDEX IF NOT EXISTS ix_archive_findings_bill ON archive_findings(bill);
CREATE TABLE IF NOT EXISTS archive_rates (payer_group TEXT, year TEXT, code TEXT, tier TEXT, pos_class TEXT,
    payer_kind TEXT, mode_cents INTEGER, median_cents INTEGER, p10_cents INTEGER, p90_cents INTEGER, n INTEGER,
    mode_share REAL, counts TEXT, PRIMARY KEY (payer_group, year, code, tier, pos_class)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS archive_market (year TEXT, code TEXT, tier TEXT, pos_class TEXT, median_cents INTEGER,
    n INTEGER, medicare_cents INTEGER, PRIMARY KEY (year, code, tier, pos_class)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS archive_pins (bill TEXT PRIMARY KEY, until TEXT, why TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS archive_requests (bill TEXT, finding_id TEXT, status TEXT, actor TEXT, reason TEXT, at TEXT,
    PRIMARY KEY (bill, at));
"""
TABLES = ("archive_files", "archive_bills", "archive_ids", "archive_findings", "archive_rates", "archive_market",
          "archive_pins", "archive_requests")
# what the dashboard's copy needs for Find a claim and the Data tab (small; no row of a bill)
SERVING_TABLES = (("archive_bills", None, None), ("archive_ids", None, None), ("archive_findings", None, None),
                  ("archive_files", "name,kind,gz_bytes,raw_bytes,records,rows,svc_lo,svc_hi,created_at,horizon_months,cutoff",
                   None))


class ArchiveError(RuntimeError):
    """The archive could not be written or read back intact: nothing was deleted."""


def _noop(*_a: Any, **_k: Any) -> None:
    return None


# ------------------------------------------------------------------------------------------------- clock and settings
def _today(today: Any = None) -> str:
    """The run's day (YYYY-MM-DD): the one place this module reads the clock."""
    return str(today)[:10] if today else datetime.now(timezone.utc).date().isoformat()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _env(env: dict | None = None) -> dict:
    """The settings source: the one place this module reads the environment."""
    return os.environ if env is None else env


def _int(raw: Any, default: int) -> int:
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


def horizon_months(env: dict | None = None) -> int:
    """The retention horizon in months of service: REMITWATCH_RETENTION_MONTHS (default 36), never below the floor."""
    raw = _env(env).get("REMITWATCH_RETENTION_MONTHS", "")
    return max(MIN_HORIZON_MONTHS, _int(raw, DEFAULT_HORIZON_MONTHS) if str(raw).strip() else DEFAULT_HORIZON_MONTHS)


def serverless(env: dict | None = None) -> bool:
    e = _env(env)
    mode = str(e.get("REMITWATCH_MODE", "")).lower()
    if mode in ("server", "serverless"):
        return mode == "serverless"
    return bool(e.get("VERCEL"))


def budget_mb(mode: str | None = None, *, limit_mb: int | None = None, env: dict | None = None) -> int | None:
    """The memory a run may use: REMITWATCH_MEMORY_BUDGET_MB, else 1,600 MB on a serverless host (of 2 GB), else 80% of
    the server's limit (`limit_mb`, the container's or the machine's memory)."""
    raw = _env(env).get("REMITWATCH_MEMORY_BUDGET_MB", "")
    if str(raw).strip() and _int(raw, 0) > 0:
        return _int(raw, 0)
    if (mode or ("serverless" if serverless(env) else "server")) == "serverless":
        return SERVERLESS_BUDGET_MB
    return int(limit_mb * SERVER_BUDGET_SHARE) if limit_mb else None


def archive_dir(db_path: str, env: dict | None = None) -> str:
    """The server's archive folder: REMITWATCH_ARCHIVE_DIR, else `archive` beside the store (/data/archive)."""
    raw = _env(env).get("REMITWATCH_ARCHIVE_DIR", "")
    if str(raw).strip():
        return os.path.abspath(str(raw).strip())
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), "archive")


def settings(env: dict | None = None, today: Any = None) -> dict:
    """Everything the archive reads from its surroundings, in one dict (the panel shows it)."""
    return {"today": _today(today), "horizon_months": horizon_months(env), "floor_months": MIN_HORIZON_MONTHS,
            "default_months": DEFAULT_HORIZON_MONTHS, "ar_months": AR_MONTHS,
            "mode": "serverless" if serverless(env) else "server", "budget_mb": budget_mb(env=env),
            "warn_share": WARN_SHARE}


def peak_mb() -> int | None:
    """The most memory this process has held so far, in MB (the high-water mark of the resident set)."""
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    try:
        import resource
        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
    except Exception:       # noqa: BLE001  (no measure on this platform: the run records none)
        return None


# -------------------------------------------------------------------------------------------------------- dates
def _d(iso: Any) -> date | None:
    try:
        return date.fromisoformat(str(iso)[:10])
    except (TypeError, ValueError):
        return None


def _months_before(day: str, months: int) -> str:
    """The first day of the month `months` before `day`'s month: the cutoff moves once a month, so a month's bills
    leave together."""
    d = _d(day) or _d(_today())
    total = d.year * 12 + (d.month - 1) - int(months)
    return date(total // 12, total % 12 + 1, 1).isoformat()


def _days_before(day: str, n: int) -> str:
    d = _d(day) or _d(_today())
    return (d - timedelta(days=int(n))).isoformat()


def _exact_months_before(day: str, months: int) -> str:
    """`day` less `months` months (the last day of a shorter month when the day does not exist)."""
    d = _d(day) or _d(_today())
    total = d.year * 12 + (d.month - 1) - int(months)
    y, m = total // 12, total % 12 + 1
    for dd in (d.day, 30, 29, 28):
        try:
            return date(y, m, dd).isoformat()
        except ValueError:
            continue
    return date(y, m, 28).isoformat()


# ----------------------------------------------------------------------------------------------------- encoding
def _enc(v: Any) -> Any:
    if isinstance(v, (bytes, bytearray, memoryview)):
        return {"$b": base64.b64encode(bytes(v)).decode("ascii")}
    return v


def _dec(v: Any) -> Any:
    if isinstance(v, dict) and "$b" in v:
        return base64.b64decode(v["$b"])
    return v


def _canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha(text: str | bytes) -> str:
    return hashlib.sha256(text.encode("utf-8") if isinstance(text, str) else text).hexdigest()


def _record_digest(rec: dict) -> str:
    """The digest of a record's content: its key and its rows (never when it was written)."""
    body = {k: rec.get(k) for k in ("kind", "bill", "file_id", "link", "tables") if k in rec}
    return _sha(_canon(body))


def _gz(data: bytes) -> bytes:
    """gzip with no timestamp and no name: the same content gives the same bytes on the same host."""
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=6, mtime=0, filename="") as fh:
        fh.write(data)
    return buf.getvalue()


def _gunzip(data: bytes) -> bytes:
    d = zlib.decompressobj(31)
    out = d.decompress(data) + d.flush()
    if not d.eof:
        raise ArchiveError("an archive file is cut short")
    return out


# --------------------------------------------------------------------------------------------------------- sinks
class DirSink:
    """The server's archive: files in a folder (0700), each written beside its name and moved into place (0600)."""

    kind = "dir"

    def __init__(self, root: str):
        self.root = os.path.abspath(root)

    def _p(self, name: str) -> str:
        p = os.path.abspath(os.path.join(self.root, name))
        if not p.startswith(self.root + os.sep):
            raise ValueError("an archive name must stay inside the archive folder")
        return p

    def put(self, name: str, data: bytes) -> dict:
        p = self._p(name)
        os.makedirs(os.path.dirname(p), mode=0o700, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p), prefix="." + os.path.basename(p) + ".", suffix=".part")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, p)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
        return {"name": name, "size": len(data)}

    def get(self, name: str) -> bytes | None:
        try:
            with open(self._p(name), "rb") as fh:
                return fh.read()
        except OSError:
            return None

    def exists(self, name: str) -> bool:
        return os.path.isfile(self._p(name))

    def list(self, prefix: str = "") -> list[str]:
        base = self._p(prefix.rstrip("/")) if prefix.strip("/") else self.root
        out = []
        for dp, _dirs, files in os.walk(base):
            for f in files:
                if f.endswith(".part") or f.startswith("."):
                    continue
                out.append(os.path.relpath(os.path.join(dp, f), self.root).replace(os.sep, "/"))
        return sorted(out)

    def describe(self) -> str:
        return f"the folder {self.root}"


class StateSink:
    """The serverless archive: the state folder's archive/ (the practice's Dropbox, or a local folder in tests)."""

    kind = "state"
    BIG = 8 << 20

    def __init__(self, state: Any, prefix: str = "archive"):
        self.state = state
        self.prefix = prefix.strip("/")

    def _n(self, name: str) -> str:
        if ".." in name.split("/"):
            raise ValueError("an archive name must stay inside the archive folder")
        return f"{self.prefix}/{name.strip('/')}"

    def put(self, name: str, data: bytes) -> dict:
        if len(data) > self.BIG:
            return dict(self.state.put_chunks(self._n(name), (data[i:i + (4 << 20)] for i in range(0, len(data), 4 << 20))) or {},
                        name=name, size=len(data))
        return dict(self.state.put(self._n(name), data) or {}, name=name, size=len(data))

    def get(self, name: str) -> bytes | None:
        chunks = self.state.get_chunks(self._n(name))
        if chunks is None:
            return None
        return b"".join(chunks)

    def exists(self, name: str) -> bool:
        return self.state.meta(self._n(name)) is not None

    def list(self, prefix: str = "") -> list[str]:
        sub = self._n(prefix) if prefix.strip("/") else self.prefix
        cut = len(self.prefix) + 1
        return sorted(m["name"][cut:] for m in self.state.list(sub) if str(m.get("name", "")).startswith(self.prefix + "/"))

    def describe(self) -> str:
        inner = getattr(self.state, "describe", None)
        return f"{inner() if callable(inner) else 'the state folder'}, {self.prefix}/"


def sink_for(*, db_path: str | None = None, state: Any = None, env: dict | None = None) -> DirSink | StateSink:
    """The archive of this host: the state folder's archive/ when a state folder is given (serverless), else the folder
    beside the store."""
    if state is not None:
        return StateSink(state)
    if not db_path:
        raise ValueError("a server's archive needs the store's path")
    return DirSink(archive_dir(db_path, env))


def _put_verified(sink: Any, name: str, gz: bytes, payload_sha: str) -> dict:
    """Write one archive file and read it back: its uncompressed content must have the digest it was written with. A file
    already there with the same content is left as it is (the same name always holds the same content); one with other
    content is never replaced."""
    have = sink.get(name)
    if have is not None:
        try:
            same = _sha(_gunzip(have)) == payload_sha
        except (ArchiveError, zlib.error, OSError):
            same = False
        if not same:
            raise ArchiveError(f"{name} is already in the archive with other content: it is never replaced")
        return {"name": name, "gz_bytes": len(have), "written": False}
    sink.put(name, gz)
    back = sink.get(name)
    if back is None:
        raise ArchiveError(f"{name} could not be read back after it was written")
    try:
        ok = _sha(_gunzip(back)) == payload_sha
    except (zlib.error, OSError) as exc:
        raise ArchiveError(f"{name} reads back damaged ({type(exc).__name__})") from exc
    if not ok:
        raise ArchiveError(f"{name} reads back with other content than was written")
    return {"name": name, "gz_bytes": len(gz), "written": True}


def _read_lines(sink: Any, name: str) -> Iterator[dict]:
    data = sink.get(name)
    if data is None:
        raise ArchiveError(f"{name} is not in the archive")
    for ln in _gunzip(data).split(b"\n"):
        if ln.strip():
            yield json.loads(ln)


# ---------------------------------------------------------------------------------------------------- the schema
def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    if "plb_seen" not in _cols(conn, "archive_bills"):
        # the newest provider-level adjustment the store held when the bill left: a payer's offset naming the bill after
        # that brings it back (restore_touched). An index written before this column treats every offset as new.
        conn.execute("ALTER TABLE archive_bills ADD COLUMN plb_seen INTEGER")


def _have(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _cols(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def _meta(conn: sqlite3.Connection, key: str) -> str | None:
    try:
        r = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    except sqlite3.OperationalError:
        return None
    return r[0] if r else None


def _meta_json(conn: sqlite3.Connection, key: str, default: Any) -> Any:
    raw = _meta(conn, key)
    if not raw:
        return default
    try:
        v = json.loads(raw)
    except ValueError:
        return default
    return v if isinstance(v, type(default)) else default


def _meta_put(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                 (key, value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)))


def _fetch(conn: sqlite3.Connection, sql: str, params: Sequence = ()) -> tuple[list[str], list[tuple]]:
    cur = conn.execute(sql, tuple(params))
    cols = [d[0] for d in cur.description]
    return cols, [tuple(r) for r in cur.fetchall()]


def _bill_of(pcn: Any) -> str:
    """The bill of a claim id, as the claim view groups them (detect.bill_of: the bill id less its submission suffix)."""
    from .detect import bill_of
    return bill_of(str(pcn)) if pcn else ""


def _bill_fn(pcn: Any, claim_key: Any) -> str | None:
    if pcn:
        b = _bill_of(pcn)
        if b:
            return b
    return ("#" + str(claim_key)) if claim_key else None


def _ebill_fn(claim_id: Any) -> str | None:
    """The bill of a claim id as EMA's reports write it (ema_reports.bill_of_claim: the ASC's facility claims keep their F),
    so a bill EMA's tables name meets the archive's own bills."""
    if not claim_id:
        return None
    from .ema_reports import bill_of_claim
    return bill_of_claim(str(claim_id))


def _register(conn: sqlite3.Connection) -> None:
    for name, n, fn in (("rw_arc_bill", 2, _bill_fn), ("rw_arc_ebill", 1, _ebill_fn)):
        try:
            conn.create_function(name, n, fn, deterministic=True)
        except (TypeError, sqlite3.NotSupportedError):        # an SQLite without deterministic functions
            conn.create_function(name, n, fn)


def _norm_ref(text: Any) -> str:
    return re.sub(r"[\s\-/]+", "", str(text or "")).upper()


# ---------------------------------------------------------------------------------------------- which items are open
def _is_open(status: Any, work_state: Any, stale: Any) -> bool:
    """An item that keeps its bill in the store: a person's started or sent status (whatever the last analysis said: the
    person's status wins, and the stored state may be older than it); else work now, waiting, can't be checked; with no
    state, a person's open status. A stale row (a person's work on an item the rules no longer list) only while started or
    sent."""
    st = str(status or "open")
    if st in ("in_progress", "appealed"):
        return True         # a person is working it: their status wins over the last analysis's state (states.py)
    if stale:
        return False
    ws = str(work_state or "")
    if ws in OPEN_STATES:
        return True
    if ws in CLOSED_STATES:
        return False
    return st in ACTIVE_STATUSES


def _worked(status: Any, notes: Any, appealed_at: Any) -> bool:
    return bool(notes) or bool(appealed_at) or str(status or "open") != "open"


# -------------------------------------------------------------------------------------------------------- the plan
def _locked_years(conn: sqlite3.Connection) -> set[str]:
    return {str(y) for y in _meta_json(conn, META_LOCKED, [])}


def _frozen_years(conn: sqlite3.Connection) -> dict:
    return _meta_json(conn, META_FROZEN, {})


def effective_horizon(conn: sqlite3.Connection, *, horizon: int | None = None, budget_state: dict | None = None,
                      env: dict | None = None, floor: int | None = None) -> dict:
    """The horizon this run uses. `horizon` (or REMITWATCH_RETENTION_MONTHS) is the owner's; a run whose memory passed
    80% of its budget (`budget_state`: {'peak_mb', 'budget_mb'} or measure()'s result) takes six months more, down to
    the floor, and a tightened horizon relaxes six months at a time once runs stay under 60%. Never below the floor
    (24 months) unless `floor` says otherwise (measurements only)."""
    lo = MIN_HORIZON_MONTHS if floor is None else int(floor)
    configured = max(lo, int(horizon) if horizon is not None else horizon_months(env))
    prev = _meta_json(conn, META_HORIZON, {})
    months, why = configured, "the owner's horizon" if horizon is not None or _env(env).get("REMITWATCH_RETENTION_MONTHS") \
        else "the default horizon"
    if prev.get("tightened") and isinstance(prev.get("months"), int) and prev["months"] < configured:
        months, why = max(lo, prev["months"]), "kept tighter after a run used most of its memory"
    share = None
    if budget_state:
        peak = budget_state.get("peak_mb")
        bud = budget_state.get("budget_mb")
        share = budget_state.get("share")
        if share is None and peak and bud:
            share = peak / bud
    tightened = months < configured
    if share is not None and share >= WARN_SHARE:
        new = max(lo, months - TIGHTEN_STEP_MONTHS)
        if new < months:
            months, why = new, f"the last run used {share:.0%} of its memory budget"
        tightened = months < configured
    elif share is not None and share < RELAX_SHARE and tightened:
        months = min(configured, months + TIGHTEN_STEP_MONTHS)
        why = "relaxed: the last run used less than 60% of its memory budget"
        tightened = months < configured
    return {"months": months, "configured": configured, "floor": lo, "why": why, "tightened": tightened, "share": share}


def _plan_tables(conn: sqlite3.Connection) -> None:
    """Temporary tables: every claim with its bill (arc_cb), every EMA and 837 claim id with its bill (arc_xb), and the
    newest service day of each bill with its counts (arc_bd). `ebill` is the same bill as EMA's reports write it."""
    _register(conn)
    for t in ("arc_cb", "arc_xb", "arc_dates", "arc_bd"):
        conn.execute(f"DROP TABLE IF EXISTS temp.{t}")
    conn.execute("CREATE TEMP TABLE arc_cb (claim_key TEXT PRIMARY KEY, bill TEXT, pcn TEXT, ebill TEXT)")
    conn.execute("""INSERT OR IGNORE INTO temp.arc_cb SELECT claim_key, rw_arc_bill(patient_control_number, claim_key),
                    patient_control_number, rw_arc_ebill(patient_control_number) FROM claims WHERE claim_key IS NOT NULL""")
    conn.execute("CREATE INDEX temp.ix_arc_cb_bill ON arc_cb(bill)")
    conn.execute("CREATE TEMP TABLE arc_xb (id TEXT, kind TEXT, bill TEXT, ebill TEXT, PRIMARY KEY (id, kind))")
    if _have(conn, "ema_rows"):
        conn.execute("""INSERT OR IGNORE INTO temp.arc_xb SELECT claim_id, 'ema', rw_arc_bill(claim_id, NULL), rw_arc_ebill(claim_id)
                        FROM (SELECT DISTINCT claim_id FROM ema_rows WHERE claim_id IS NOT NULL AND claim_id <> '')""")
    if _have(conn, "claims_837"):
        conn.execute("""INSERT OR IGNORE INTO temp.arc_xb SELECT pcn, '837', rw_arc_bill(pcn, NULL), rw_arc_ebill(pcn) FROM
                        (SELECT DISTINCT patient_control_number pcn FROM claims_837 WHERE patient_control_number IS NOT NULL
                         AND patient_control_number <> '')""")
    if _have(conn, "lines_837"):
        conn.execute("""INSERT OR IGNORE INTO temp.arc_xb SELECT pcn, '837', rw_arc_bill(pcn, NULL), rw_arc_ebill(pcn) FROM
                        (SELECT DISTINCT patient_control_number pcn FROM lines_837 WHERE patient_control_number IS NOT NULL
                         AND patient_control_number <> '')""")
    conn.execute("CREATE INDEX temp.ix_arc_xb_bill ON arc_xb(bill)")
    conn.execute("CREATE TEMP TABLE arc_dates (bill TEXT, d TEXT, paid TEXT, claims INTEGER, lines INTEGER, years TEXT)")
    conn.execute("""INSERT INTO temp.arc_dates (bill, d, lines, years)
                    SELECT k.bill, MAX(COALESCE(l.service_date, l.service_period_end, l.service_period_start)), COUNT(*),
                           GROUP_CONCAT(DISTINCT substr(COALESCE(l.service_date, l.service_period_start, l.payment_date), 1, 4))
                    FROM temp.arc_cb k JOIN lines l ON l.claim_key = k.claim_key GROUP BY k.bill""")
    conn.execute("""INSERT INTO temp.arc_dates (bill, d, paid, claims)
                    SELECT k.bill, MAX(COALESCE(c.statement_to, c.statement_from)), MAX(c.payment_date), COUNT(*)
                    FROM temp.arc_cb k JOIN claims c ON c.claim_key = k.claim_key GROUP BY k.bill""")
    if _have(conn, "ema_rows"):
        conn.execute("""INSERT INTO temp.arc_dates (bill, d, paid, lines)
                        SELECT x.bill, MAX(e.service_date), MAX(COALESCE(e.posted_date, e.transaction_date)), COUNT(*)
                        FROM ema_rows e JOIN temp.arc_xb x ON x.id = e.claim_id AND x.kind = 'ema' GROUP BY x.bill""")
    for t in ("claims_837", "lines_837"):
        if _have(conn, t):
            conn.execute(f"""INSERT INTO temp.arc_dates (bill, d, lines)
                             SELECT x.bill, MAX(t.service_date), COUNT(*) FROM {t} t JOIN temp.arc_xb x
                             ON x.id = t.patient_control_number AND x.kind = '837' GROUP BY x.bill""")
    conn.execute("""CREATE TEMP TABLE arc_bd AS SELECT bill, MAX(d) newest, MAX(paid) paid, COALESCE(SUM(claims), 0) claims,
                    COALESCE(SUM(lines), 0) lines, GROUP_CONCAT(years) years FROM temp.arc_dates
                    WHERE bill IS NOT NULL GROUP BY bill""")
    conn.execute("CREATE UNIQUE INDEX temp.ix_arc_bd ON arc_bd(bill)")


# EMA's tables that name a bill, as (table, its bill column, its claim id column): the posting ledger's summaries (kept
# for the ledger's retention: a line with a payer's denial posted, a payment under a check that is no 835, a line not yet
# paid, ...), the claim-number map, the A/R's claim ids and the A/R rows themselves
EMA_NAMES = (("ema_lines", "bill", "claim_id"), ("ema_link_map", "bill", "claim_id"), ("ema_claim_links", "bill", "claim_id"),
             ("ema_ar", "bill", "claim_id"))
HELD_EMA = "EMA still lists it"


def _ema_named(conn: sqlite3.Connection) -> int:
    """temp.arc_eb: every bill EMA's reports in the store still name (EMA_NAMES), in EMA's own form of a bill. While they
    do, the bill's 835 rows stay: EMA's checks (denials only EMA saw, claims no payer answered, the A/R buckets) find the
    remittance that answered a line by its bill, so a bill whose 835 left while EMA still listed it would come back as a
    denial "with no remittance on file". EMA's summaries let go of a bill with the ledger's own retention. Returns the
    number of bills named."""
    _register(conn)
    conn.execute("DROP TABLE IF EXISTS temp.arc_eb")
    conn.execute("CREATE TEMP TABLE arc_eb (ebill TEXT PRIMARY KEY)")
    for table, bill_col, claim_col in EMA_NAMES:
        if not _have(conn, table):
            continue
        cols = set(_cols(conn, table))
        if bill_col in cols:
            conn.execute(f"""INSERT OR IGNORE INTO temp.arc_eb SELECT DISTINCT {bill_col} FROM {table}
                             WHERE {bill_col} IS NOT NULL AND {bill_col} <> ''""")
        if claim_col in cols:
            conn.execute(f"""INSERT OR IGNORE INTO temp.arc_eb SELECT e FROM (SELECT DISTINCT rw_arc_ebill({claim_col}) e
                             FROM {table} WHERE {claim_col} IS NOT NULL AND {claim_col} <> '') WHERE e IS NOT NULL""")
    return conn.execute("SELECT COUNT(*) FROM temp.arc_eb").fetchone()[0]


def _held_bills(conn: sqlite3.Connection, today: str) -> dict[str, str]:
    """Bills that stay hot whatever their age, with the reason: an open item, a worked or open pattern listing one of
    their lines, a recent reopening, a reopening asked for, EMA's reports still listing the bill."""
    held: dict[str, str] = {}
    if _have(conn, "findings"):
        fcols = set(_cols(conn, "findings"))
        sel = ", ".join(c if c in fcols else f"NULL AS {c}" for c in
                        ("finding_id", "patient_control_number", "claim_key", "status", "work_state", "stale", "notes",
                         "appealed_at", "evidence", "line_key", "transaction_key"))
        need_claims: list[str] = []
        pattern_lines: list[str] = []
        remits: list[str] = []
        for fid, pcn, ck, status, ws, stale, notes, appealed, ev, lk, txn in conn.execute(f"SELECT {sel} FROM findings"):
            is_open = _is_open(status, ws, stale)
            if is_open and txn and not ck and not lk:
                remits.append(txn)          # a remittance-level item (a payment EMA never posted): its remittance stays whole
            if ev and '"line_keys"' in ev and not pcn and not ck:
                # a routine-rate pattern: its lines stay while it is open or a person worked it (its lines are its record)
                if is_open or _worked(status, notes, appealed):
                    try:
                        lks = json.loads(ev).get("line_keys") or []
                    except ValueError:
                        lks = []
                    pattern_lines += [str(x) for x in lks if x]
                continue
            if not is_open:
                continue
            if pcn:
                held.setdefault(_bill_of(pcn), "open item")
            elif ck:
                need_claims.append(ck)
        for i in range(0, len(need_claims), 500):
            part = need_claims[i:i + 500]
            for (b,) in conn.execute(f"SELECT bill FROM temp.arc_cb WHERE claim_key IN ({','.join('?' * len(part))})", part):
                held.setdefault(b, "open item")
        if remits:
            conn.execute("DROP TABLE IF EXISTS temp.arc_tx")
            conn.execute("CREATE TEMP TABLE arc_tx (transaction_key TEXT PRIMARY KEY)")
            conn.executemany("INSERT OR IGNORE INTO temp.arc_tx VALUES (?)", [(x,) for x in remits])
            for (b,) in conn.execute("""SELECT DISTINCT k.bill FROM claims c JOIN temp.arc_cb k ON k.claim_key = c.claim_key
                                        WHERE c.transaction_key IN (SELECT transaction_key FROM temp.arc_tx)"""):
                held.setdefault(b, "open item")
            conn.execute("DROP TABLE IF EXISTS temp.arc_tx")
        if pattern_lines:
            conn.execute("DROP TABLE IF EXISTS temp.arc_pl")
            conn.execute("CREATE TEMP TABLE arc_pl (line_key TEXT PRIMARY KEY)")
            conn.executemany("INSERT OR IGNORE INTO temp.arc_pl VALUES (?)", [(x,) for x in pattern_lines])
            for (b,) in conn.execute("""SELECT DISTINCT k.bill FROM temp.arc_pl p JOIN lines l ON l.line_key = p.line_key
                                        JOIN temp.arc_cb k ON k.claim_key = l.claim_key"""):
                held.setdefault(b, "pattern")
            conn.execute("DROP TABLE IF EXISTS temp.arc_pl")
    for (b,) in conn.execute("SELECT bill FROM archive_pins WHERE until > ?", (today,)):
        held.setdefault(b, "reopened")
    for (b,) in conn.execute("SELECT DISTINCT bill FROM archive_requests"):
        held.setdefault(b, "reopen asked")
    if _ema_named(conn):
        for t in ("arc_cb", "arc_xb"):
            for (b,) in conn.execute(f"SELECT DISTINCT bill FROM temp.{t} WHERE ebill IN (SELECT ebill FROM temp.arc_eb)"):
                held.setdefault(b, HELD_EMA)
    conn.execute("DROP TABLE IF EXISTS temp.arc_eb")
    return held


_TOKEN = re.compile(r"[A-Z0-9]{6,40}")
_KEY_FIELDS = ("claim_key", "line_key", "claim_keys", "line_keys")


def _named_bills(conn: sqlite3.Connection, skip: set[str] | frozenset = frozenset()) -> set[str]:
    """Bills another item's proof names: an item that stays may rest on a claim of another bill (a duplicate denial
    "already paid under claim ...", a payment the payer made on a corrected claim, an archive rule's proof row). The claim
    ids in its automatic note, its state, its hint, a person's note and its evidence, and the claim and line keys its
    evidence holds, keep their bills in the store, so the proof never leaves before the item. A routine-rate pattern's
    claim list is not a proof (the pattern itself is kept as it was, carry_locked_aggregates). Items of a bill in `skip`
    (leaving with this run) name nothing: their proof leaves with them. Returns the bills named."""
    named: set[str] = set()
    fcols = set(_cols(conn, "findings"))
    texts = [c for c in ("auto_note", "state_basis", "hint", "notes", "evidence") if c in fcols]
    if not texts:
        return named
    known = {b for (b,) in conn.execute("SELECT bill FROM temp.arc_bd")}
    seen: dict[str, str] = {}
    want_claims: set[str] = set()
    want_lines: set[str] = set()
    for row in conn.execute(f"SELECT patient_control_number, {', '.join(texts)} FROM findings WHERE patient_control_number IS NOT NULL"):
        own = _bill_of(row[0])
        if own in skip:
            continue
        for t in row[1:]:
            if not t:
                continue
            for tok in _TOKEN.findall(str(t).upper()):
                b = seen.get(tok)
                if b is None:
                    b = seen[tok] = _bill_of(tok)
                if b != own and b in known:
                    named.add(b)
        ev = row[-1] if texts[-1] == "evidence" else None
        if ev and any(k in ev for k in _KEY_FIELDS):
            try:
                obj = json.loads(ev)
            except ValueError:
                continue
            stack = [obj]
            while stack:
                x = stack.pop()
                if isinstance(x, dict):
                    for k, v in x.items():
                        if k in _KEY_FIELDS:
                            for y in (v if isinstance(v, list) else [v]):
                                if isinstance(y, str) and y:
                                    (want_claims if k.startswith("claim") else want_lines).add(y)
                        elif isinstance(v, (dict, list)):
                            stack.append(v)
                elif isinstance(x, list):
                    stack.extend(y for y in x if isinstance(y, (dict, list)))
    if want_claims or want_lines:
        conn.execute("DROP TABLE IF EXISTS temp.arc_nk")
        conn.execute("CREATE TEMP TABLE arc_nk (k TEXT PRIMARY KEY)")
        conn.executemany("INSERT OR IGNORE INTO temp.arc_nk VALUES (?)", [(x,) for x in want_claims])
        for (b,) in conn.execute("SELECT DISTINCT k.bill FROM temp.arc_nk n JOIN temp.arc_cb k ON k.claim_key = n.k"):
            named.add(b)
        conn.execute("DELETE FROM temp.arc_nk")
        conn.executemany("INSERT OR IGNORE INTO temp.arc_nk VALUES (?)", [(x,) for x in want_lines])
        for (b,) in conn.execute("""SELECT DISTINCT k.bill FROM temp.arc_nk n JOIN lines l ON l.line_key = n.k
                                    JOIN temp.arc_cb k ON k.claim_key = l.claim_key"""):
            named.add(b)
        conn.execute("DROP TABLE IF EXISTS temp.arc_nk")
    return named


def plan(conn: sqlite3.Connection, *, horizon: int | None = None, budget_state: dict | None = None, today: Any = None,
         env: dict | None = None, floor: int | None = None, require_frozen: bool = True, limit: int | None = None) -> dict:
    """What a run would archive, nothing written but temporary tables: {'horizon', 'cutoff', 'bills': [(bill, newest,
    lines, claims, years)], 'held': {reason: n}, 'waiting_rates': n, 'blocked': reason or None}."""
    ensure_schema(conn)
    today = _today(today)
    h = effective_horizon(conn, horizon=horizon, budget_state=budget_state, env=env, floor=floor)
    cutoff = _months_before(today, h["months"])
    out: dict = {"today": today, "horizon": h, "cutoff": cutoff, "bills": [], "held": {}, "waiting_rates": 0,
                 "waiting_years": [], "blocked": None}
    if _meta(conn, META_DETECT_PENDING) is not None:
        out["blocked"] = "the analysis has not run on the newest data yet"
        return out
    has_data = (_have(conn, "claims") and conn.execute("SELECT 1 FROM claims LIMIT 1").fetchone() is not None) or \
        (_have(conn, "ema_rows") and conn.execute("SELECT 1 FROM ema_rows LIMIT 1").fetchone() is not None)
    ran = _have(conn, "findings") and _have(conn, "detection_runs") and \
        conn.execute("SELECT 1 FROM detection_runs LIMIT 1").fetchone() is not None
    if has_data and not ran:
        out["blocked"] = "the analysis has never run on this store"
        return out
    if not has_data:
        return out
    _plan_tables(conn)
    held = _held_bills(conn, today)
    frozen = set(_frozen_years(conn)) | _locked_years(conn)
    waiting_years: set[str] = set()
    held_counts: Counter = Counter()
    rows = conn.execute("SELECT bill, newest, paid, lines, claims, years FROM temp.arc_bd ORDER BY newest, bill").fetchall()
    def can_leave(newest: Any, paid: Any, n_claims: Any, years: Any) -> bool:
        when = max(str(newest or "")[:10], str(paid or "")[:10])
        if not when or not newest or when >= cutoff:
            return False
        ys = {y for y in str(years or "").split(",") if y and y.isdigit()}
        return not (require_frozen and n_claims and any(y not in frozen for y in ys))
    leaving = {r[0] for r in rows if r[0] not in held and can_leave(r[1], r[2], r[4], r[5])}
    for _round in range(10):            # a bill another item that stays names stays; repeated until nothing changes
        named = _named_bills(conn, skip=leaving) & leaving
        if not named:
            break
        for b in named:
            held.setdefault(b, "named by another item")
        leaving -= named
    for bill, newest, paid, n_lines, n_claims, years in rows:
        # the bill's last activity: its newest service, and its newest remittance or EMA posting (a payment this year on
        # an old service is part of the payer's recent activity, which the rules measure: the bill stays)
        when = max(str(newest or "")[:10], str(paid or "")[:10])
        if not when or not newest or when >= cutoff:
            continue
        if bill in held:
            held_counts[held[bill]] += 1
            continue
        ys = sorted({y for y in str(years or "").split(",") if y and y.isdigit()})
        if require_frozen and n_claims and any(y not in frozen for y in ys):
            waiting_years.update(y for y in ys if y not in frozen)
            out["waiting_rates"] += 1
            continue
        out["bills"].append((bill, when, int(n_lines or 0), int(n_claims or 0), ys))
        if limit and len(out["bills"]) >= limit:
            break
    out["held"] = dict(held_counts)
    out["waiting_years"] = sorted(waiting_years)
    return out


# ----------------------------------------------------------------------------------------------- building records
def _batches(bills: list[tuple], size: int = BATCH_BILLS, max_rows: int = BATCH_ROWS) -> Iterator[list[tuple]]:
    cur: list[tuple] = []
    rows = 0
    for b in bills:
        weight = max(1, b[2] * 4 + b[3])          # lines carry adjustments and remarks: about four rows each
        if cur and (len(cur) >= size or rows + weight > max_rows):
            yield cur
            cur, rows = [], 0
        cur.append(b)
        rows += weight
    if cur:
        yield cur


def _batch_keys(conn: sqlite3.Connection, bills: Sequence[str]) -> None:
    """Temporary key tables for one batch: its bills, claims, lines, claim ids and items (with each one's bill)."""
    for t in ("arc_b", "arc_bc", "arc_bl", "arc_bp", "arc_bf"):
        conn.execute(f"DROP TABLE IF EXISTS temp.{t}")
    conn.execute("CREATE TEMP TABLE arc_b (bill TEXT PRIMARY KEY)")
    conn.executemany("INSERT OR IGNORE INTO temp.arc_b VALUES (?)", [(b,) for b in bills])
    conn.execute("CREATE TEMP TABLE arc_bc (claim_key TEXT PRIMARY KEY, bill TEXT)")
    conn.execute("INSERT INTO temp.arc_bc SELECT k.claim_key, k.bill FROM temp.arc_b b JOIN temp.arc_cb k ON k.bill = b.bill")
    conn.execute("CREATE TEMP TABLE arc_bl (line_key TEXT PRIMARY KEY, bill TEXT)")
    conn.execute("""INSERT OR IGNORE INTO temp.arc_bl SELECT l.line_key, k.bill FROM temp.arc_bc k JOIN lines l ON l.claim_key = k.claim_key
                    WHERE l.line_key IS NOT NULL""")
    conn.execute("CREATE TEMP TABLE arc_bp (pcn TEXT PRIMARY KEY, bill TEXT)")
    conn.execute("""INSERT OR IGNORE INTO temp.arc_bp SELECT k.pcn, k.bill FROM temp.arc_b b JOIN temp.arc_cb k ON k.bill = b.bill
                    WHERE k.pcn IS NOT NULL AND k.pcn <> ''""")
    conn.execute("""INSERT OR IGNORE INTO temp.arc_bp SELECT x.id, x.bill FROM temp.arc_b b JOIN temp.arc_xb x ON x.bill = b.bill""")
    conn.execute("CREATE TEMP TABLE arc_bf (finding_id TEXT PRIMARY KEY, bill TEXT)")
    if _have(conn, "findings"):
        fcols = set(_cols(conn, "findings"))
        evidence = "f.evidence" if "evidence" in fcols else "NULL"
        conn.execute("""INSERT OR IGNORE INTO temp.arc_bf SELECT f.finding_id, p.bill FROM temp.arc_bp p
                        JOIN findings f ON f.patient_control_number = p.pcn""")
        conn.execute(f"""INSERT OR IGNORE INTO temp.arc_bf SELECT f.finding_id, k.bill FROM temp.arc_bc k
                         JOIN findings f ON f.claim_key = k.claim_key
                         WHERE NOT (COALESCE({evidence}, '') LIKE '%"line_keys"%' AND f.patient_control_number IS NULL)""")


def _table_rows(conn: sqlite3.Connection, table: str) -> tuple[list[str], list[tuple], list[str]]:
    """(columns, rows, bill of each row) of one table for the batch in the key tables. Every statement reads its table
    once at most: a key table is probed, never a large table per key (the compact store has few indexes)."""
    if not _have(conn, table):
        return [], [], []
    if table == "claims":
        cols, rows = _fetch(conn, "SELECT k.bill AS _bill, c.* FROM temp.arc_bc k JOIN claims c ON c.claim_key = k.claim_key")
    elif table == "lines":
        cols, rows = _fetch(conn, "SELECT k.bill AS _bill, l.* FROM temp.arc_bc k JOIN lines l ON l.claim_key = k.claim_key")
    elif table in ("adjustments", "remarks"):
        cols, rows = _fetch(conn, f"""SELECT (SELECT k.bill FROM temp.arc_bc k WHERE k.claim_key = a.claim_key) AS _bill, a.*
                                      FROM {table} a WHERE a.claim_key IN (SELECT claim_key FROM temp.arc_bc)""")
    elif table in ("line_links", "line_handling"):
        cols, rows = _fetch(conn, f"""SELECT (SELECT k.bill FROM temp.arc_bl k WHERE k.line_key = a.line_key) AS _bill, a.*
                                      FROM {table} a WHERE a.line_key IN (SELECT line_key FROM temp.arc_bl)""")
    elif table == "ema_rows":
        cols, rows = _fetch(conn, """SELECT (SELECT p.bill FROM temp.arc_bp p WHERE p.pcn = e.claim_id) AS _bill, e.*
                                     FROM ema_rows e WHERE e.claim_id IN (SELECT pcn FROM temp.arc_bp)""")
    elif table in ("claims_837", "lines_837"):
        cols, rows = _fetch(conn, f"""SELECT (SELECT p.bill FROM temp.arc_bp p WHERE p.pcn = t.patient_control_number) AS _bill, t.*
                                      FROM {table} t WHERE t.patient_control_number IN (SELECT pcn FROM temp.arc_bp)""")
    elif table == "findings":
        cols, rows = _fetch(conn, "SELECT k.bill AS _bill, f.* FROM temp.arc_bf k JOIN findings f ON f.finding_id = k.finding_id")
    elif table == "finding_events":
        cols, rows = _fetch(conn, """SELECT (SELECT k.bill FROM temp.arc_bf k WHERE k.finding_id = e.finding_id) AS _bill, e.*
                                     FROM finding_events e WHERE e.finding_id IN (SELECT finding_id FROM temp.arc_bf)""")
    elif table == "transactions":
        cols, rows = _fetch(conn, """SELECT x.bill AS _bill, t.* FROM (SELECT DISTINCT k.bill, c.transaction_key FROM temp.arc_bc k
                                     JOIN claims c ON c.claim_key = k.claim_key) x JOIN transactions t ON t.transaction_key = x.transaction_key""")
    elif table in ("ema_lines", "ema_link_map"):
        if "bill" not in _cols(conn, table):
            return [], [], []
        cols, rows = _fetch(conn, f"SELECT t.bill AS _bill, t.* FROM {table} t WHERE t.bill IN (SELECT bill FROM temp.arc_b)")
    elif table in ("ema_line_payments", "ema_line_reasons"):
        if not _have(conn, "ema_lines"):
            return [], [], []
        cols, rows = _fetch(conn, f"""SELECT x.bill AS _bill, t.* FROM (SELECT DISTINCT bill, link FROM ema_lines
                                      WHERE bill IN (SELECT bill FROM temp.arc_b)) x JOIN {table} t ON t.link = x.link""")
    elif table in ("ema_claim_links", "ema_ar", "ema_ar_items"):
        cols, rows = _fetch(conn, f"""SELECT (SELECT p.bill FROM temp.arc_bp p WHERE p.pcn = t.claim_id) AS _bill, t.*
                                      FROM {table} t WHERE t.claim_id IN (SELECT pcn FROM temp.arc_bp)""")
    else:
        return [], [], []
    bills = [r[0] for r in rows]
    return cols[1:], [r[1:] for r in rows], bills


def _sort_rows(table: str, cols: list[str], rows: list[tuple]) -> list[tuple]:
    keys = [cols.index(k) for k in SORT_KEYS.get(table, ()) if k in cols]
    if not keys:
        return sorted(rows, key=lambda r: _canon([_enc(v) for v in r]))
    return sorted(rows, key=lambda r: tuple("" if r[i] is None else (f"{r[i]:020d}" if isinstance(r[i], int) else str(r[i]))
                                            for i in keys))


def _volatile_digest(tables: dict) -> str:
    """The digest of what a person can change while the run works: the items and their history."""
    return _sha(_canon({t: tables.get(t) for t in ("findings", "finding_events")}))


def _build_records(conn: sqlite3.Connection, bills: list[tuple]) -> tuple[list[dict], dict]:
    """The records of one batch (one per bill, in the batch's order) and the row counts by table."""
    _batch_keys(conn, [b[0] for b in bills])
    per: dict[str, dict[str, tuple[list[str], list[tuple]]]] = defaultdict(dict)
    counts: Counter = Counter()
    for table in MOVED + COPIED:
        cols, rows, owners = _table_rows(conn, table)
        if not rows:
            continue
        if table in MOVED:
            counts[table] += len(rows)
        grouped: dict[str, list[tuple]] = defaultdict(list)
        for owner, r in zip(owners, rows):
            if owner is not None:
                grouped[owner].append(r)
        for bill, rs in grouped.items():
            per[bill][table] = (cols, rs)
    records = []
    for bill, newest, _n_lines, _n_claims, years in bills:
        tables = {}
        for table, (cols, rs) in per.get(bill, {}).items():
            tables[table] = {"cols": cols, "rows": [[_enc(v) for v in r] for r in _sort_rows(table, cols, rs)]}
        lines = tables.get("lines")
        svc_dates = []
        if lines and "service_date" in lines["cols"]:
            i = lines["cols"].index("service_date")
            svc_dates = [str(r[i]) for r in lines["rows"] if r[i]]
        rec = {"kind": "bill", "bill": bill, "tables": tables, "svc_lo": min(svc_dates) if svc_dates else newest,
               "svc_hi": max(svc_dates) if svc_dates else newest, "years": years, "v": FORMAT_VERSION}
        rec["digest"] = _record_digest(rec)
        rec["volatile"] = _volatile_digest(tables)
        records.append(rec)
    return records, dict(counts)


def _payload(records: list[dict], kind: str) -> bytes:
    header = {"format": FORMAT, "v": FORMAT_VERSION, "kind": "header", "of": kind, "records": len(records)}
    lines = [_canon(header)] + [_canon({k: v for k, v in r.items() if k != "volatile"}) for r in records]
    return ("\n".join(lines) + "\n").encode("utf-8")


def _check_payload(data: bytes, expected: dict[str, str], key: str) -> int:
    """Read a payload back: a header, then one record per expected key with the expected digest, nothing else. Returns
    the rows it holds."""
    seen: set[str] = set()
    rows = 0
    first = True
    for ln in data.split(b"\n"):
        if not ln.strip():
            continue
        obj = json.loads(ln)
        if first:
            first = False
            if obj.get("format") != FORMAT or obj.get("kind") != "header":
                raise ArchiveError("an archive file has no header")
            continue
        k = obj.get(key)
        if k not in expected or k in seen:
            raise ArchiveError("an archive file holds a record it should not")
        if _record_digest(obj) != obj.get("digest") or obj.get("digest") != expected[k]:
            raise ArchiveError("an archive record reads back with other content")
        seen.add(k)
        rows += sum(len(t.get("rows") or []) for t in (obj.get("tables") or {}).values())
    if seen != set(expected):
        raise ArchiveError("an archive file is missing records")
    return rows


# ------------------------------------------------------------------------------------------------- moving rows out
_DELETE = (
    ("finding_events", "DELETE FROM finding_events WHERE finding_id IN (SELECT finding_id FROM temp.arc_bf)"),
    ("findings", "DELETE FROM findings WHERE finding_id IN (SELECT finding_id FROM temp.arc_bf)"),
    ("adjustments", "DELETE FROM adjustments WHERE claim_key IN (SELECT claim_key FROM temp.arc_bc)"),
    ("remarks", "DELETE FROM remarks WHERE claim_key IN (SELECT claim_key FROM temp.arc_bc)"),
    ("line_handling", "DELETE FROM line_handling WHERE line_key IN (SELECT line_key FROM temp.arc_bl)"),
    ("line_links", "DELETE FROM line_links WHERE line_key IN (SELECT line_key FROM temp.arc_bl)"),
    ("lines", "DELETE FROM lines WHERE claim_key IN (SELECT claim_key FROM temp.arc_bc)"),
    ("claims", "DELETE FROM claims WHERE claim_key IN (SELECT claim_key FROM temp.arc_bc)"),
    ("ema_rows", "DELETE FROM ema_rows WHERE claim_id IN (SELECT pcn FROM temp.arc_bp)"),
    ("lines_837", "DELETE FROM lines_837 WHERE patient_control_number IN (SELECT pcn FROM temp.arc_bp)"),
    ("claims_837", "DELETE FROM claims_837 WHERE patient_control_number IN (SELECT pcn FROM temp.arc_bp)"),
)


def _drop_from_keys(conn: sqlite3.Connection, bills: Iterable[str]) -> None:
    gone = [(b,) for b in bills]
    if not gone:
        return
    for t in ("arc_b", "arc_bc", "arc_bl", "arc_bp", "arc_bf"):
        conn.executemany(f"DELETE FROM temp.{t} WHERE bill = ?", gone)


BILL_COLS = ("bill", "file", "digest", "svc_lo", "svc_hi", "claims", "lines", "findings", "archived_at", "run_id", "plb_seen")


def _index_rows(rec: dict, name: str, at: str, run_id: str | None, plb_seen: int | None = None) -> tuple[tuple, list[tuple], list[tuple]]:
    t = rec["tables"]

    def dicts(table: str) -> list[dict]:
        x = t.get(table)
        return [dict(zip(x["cols"], r)) for r in x["rows"]] if x else []
    claims, findings = dicts("claims"), dicts("findings")
    ids = set()
    for c in claims:
        if c.get("patient_control_number"):
            ids.add((str(c["patient_control_number"]).upper(), "pcn"))
        if c.get("payer_claim_control_number"):
            ids.add((_norm_ref(c["payer_claim_control_number"]), "payer_ref"))
    for r in dicts("ema_rows"):
        if r.get("claim_id"):
            ids.add((str(r["claim_id"]).upper(), "ema"))
    for table in ("claims_837", "lines_837"):
        for r in dicts(table):
            if r.get("patient_control_number"):
                ids.add((str(r["patient_control_number"]).upper(), "837"))
    bill_row = (rec["bill"], name, rec["digest"], rec.get("svc_lo"), rec.get("svc_hi"), len(claims),
                len((t.get("lines") or {}).get("rows") or []), len(findings), at, run_id, plb_seen)
    id_rows = [(i, k, rec["bill"]) for i, k in sorted(ids) if i]
    f_rows = [(f.get("finding_id"), rec["bill"], f.get("signal"), f.get("category"), f.get("status"), f.get("work_state"),
               f.get("payer_group"), f.get("service_date"), f.get("payment_date"), f.get("appeal_deadline"),
               f.get("shortfall_cents"), f.get("recovered_cents"), f.get("stale"), at, name) for f in findings]
    return bill_row, id_rows, f_rows


def _commit_batch(conn: sqlite3.Connection, records: list[dict], counts: dict, name: str, meta: dict, *, at: str,
                  run_id: str | None, years: set[str], today: str) -> dict:
    """One transaction: check each bill's items again, put the index rows in, take the hot rows out (the counts must equal
    what the file holds), lock the service years taken."""
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        # a person who changed an item since the record was built keeps the bill in the store (it waits for the next run)
        _batch_keys(conn, [r["bill"] for r in records])
        changed = set()
        now_cols_f, now_f, owners_f = _table_rows(conn, "findings")
        now_cols_e, now_e, owners_e = _table_rows(conn, "finding_events")
        cur: dict[str, dict] = defaultdict(dict)
        for table, cols, rows, owners in (("findings", now_cols_f, now_f, owners_f), ("finding_events", now_cols_e, now_e, owners_e)):
            grouped: dict[str, list] = defaultdict(list)
            for o, r in zip(owners, rows):
                grouped[o].append(r)
            for b, rs in grouped.items():
                cur[b][table] = {"cols": cols, "rows": [[_enc(v) for v in r] for r in _sort_rows(table, cols, rs)]}
        for rec in records:
            if _volatile_digest(cur.get(rec["bill"], {})) != rec["volatile"]:
                changed.add(rec["bill"])
        # ... and a bill that has an open item now (a person reopened one after the plan was made, before this batch was
        # built), a fresh pin or a reopening asked for stays too
        for b, tabs in cur.items():
            x = tabs.get("findings")
            if not x or b in changed:
                continue
            i = {c: x["cols"].index(c) for c in ("status", "work_state", "stale") if c in x["cols"]}
            if any(_is_open(r[i["status"]] if "status" in i else None, r[i["work_state"]] if "work_state" in i else None,
                            r[i["stale"]] if "stale" in i else None) for r in x["rows"]):
                changed.add(b)
        q = ",".join("?" * len(records))
        bills_now = [r["bill"] for r in records]
        changed.update(b for (b,) in conn.execute(f"SELECT bill FROM archive_pins WHERE until > ? AND bill IN ({q})",
                                                  [today] + bills_now))
        changed.update(b for (b,) in conn.execute(f"SELECT DISTINCT bill FROM archive_requests WHERE bill IN ({q})", bills_now))
        keep = [r for r in records if r["bill"] not in changed]
        _drop_from_keys(conn, changed)
        want: Counter = Counter()
        for r in keep:
            for table, x in r["tables"].items():
                if table in MOVED:
                    want[table] += len(x["rows"])
        if keep:                    # a file whose every bill a person changed meanwhile indexes nothing: it is not listed
            conn.execute("""INSERT OR REPLACE INTO archive_files (name, kind, sha256, gz_bytes, raw_bytes, records, rows, svc_lo,
                            svc_hi, created_at, run_id, horizon_months, cutoff, detail) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         (name, "bills", meta["sha256"], meta["gz_bytes"], meta["raw_bytes"], len(records), meta["rows"],
                          meta.get("svc_lo"), meta.get("svc_hi"), at, run_id, meta.get("horizon_months"), meta.get("cutoff"),
                          _canon({"tables": counts, "bills": len(records), "indexed": len(keep)})))
        plb_seen = conn.execute("SELECT MAX(id) FROM plb").fetchone()[0] if _have(conn, "plb") else None
        for rec in keep:
            bill_row, id_rows, f_rows = _index_rows(rec, name, at, run_id, plb_seen or 0)
            conn.execute(f"INSERT OR REPLACE INTO archive_bills ({','.join(BILL_COLS)}) VALUES ({','.join('?' * len(BILL_COLS))})",
                         bill_row)
            conn.execute("DELETE FROM archive_ids WHERE bill = ?", (rec["bill"],))
            conn.executemany("INSERT OR IGNORE INTO archive_ids (id, kind, bill) VALUES (?,?,?)", id_rows)
            conn.executemany("INSERT OR REPLACE INTO archive_findings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", f_rows)
        got: Counter = Counter()
        for table, sql in _DELETE:
            if _have(conn, table):
                got[table] += conn.execute(sql).rowcount
        bad = {t: (want.get(t, 0), got.get(t, 0)) for t in set(want) | set(got) if want.get(t, 0) != got.get(t, 0)}
        if bad:
            raise ArchiveError("the rows leaving the store do not match the rows archived: nothing was deleted "
                               + ", ".join(f"{t} {w} archived vs {g}" for t, (w, g) in sorted(bad.items())))
        if keep and years:
            locked = _locked_years(conn) | set(years)
            _meta_put(conn, META_LOCKED, sorted(locked))
        conn.execute("COMMIT")
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    return {"bills": len(keep), "changed": len(changed), "rows": dict(got), "kept_bills": [r["bill"] for r in keep]}


# ---------------------------------------------------------------------------------------------------------- run
def run(conn: sqlite3.Connection, *, horizon: int | None = None, budget_state: dict | None = None, sink: Any = None,
        run_id: str | None = None, today: Any = None, mode: str | None = None, deadline: float | None = None,
        env: dict | None = None, floor: int | None = None, require_frozen: bool = True, max_bills: int | None = None,
        batch_bills: int = BATCH_BILLS, ar: bool = True, log: Callable[..., Any] | None = None) -> dict:
    """One retention pass over the store: bills past the horizon with nothing open go to the archive (written, read back
    and checked before their rows leave), then the A/R snapshots older than 13 months. Returns what it did:
    {'horizon', 'cutoff', 'bills', 'claims', 'lines', 'findings', 'rows': {table: n}, 'files': [...], 'held': {...},
    'waiting_rates', 'ar': {...}, 'seconds', 'more'}. `sink` is where the archive lives (sink_for); without one the run
    only measures what it would do. `deadline` (time.time()) stops it between batches; what is left waits for the next
    run. Idempotent: a second run with nothing new archives nothing."""
    log = log or _noop
    t0 = time.time()
    ensure_schema(conn)
    today = _today(today)
    at = _now()
    p = plan(conn, horizon=horizon, budget_state=budget_state, today=today, env=env, floor=floor, require_frozen=require_frozen)
    out: dict = {"today": today, "run_id": run_id, "mode": mode, "horizon": p["horizon"], "cutoff": p["cutoff"],
                 "blocked": p["blocked"], "eligible": len(p["bills"]), "held": p["held"], "waiting_rates": p["waiting_rates"],
                 "waiting_years": p["waiting_years"], "bills": 0, "claims": 0, "lines": 0, "findings": 0, "rows": {},
                 "files": [], "changed_meanwhile": 0, "more": False, "ar": {}, "errors": []}
    if sink is None:
        out["dry_run"] = True
    bills = p["bills"][:max_bills] if max_bills else p["bills"]
    if max_bills and len(p["bills"]) > max_bills:
        out["more"] = True
    if sink is not None:
        _meta_put(conn, META_WHERE, sink.describe())
        conn.commit()
    if sink is not None and bills and not p["blocked"]:
        for batch in _batches(bills, size=batch_bills):
            if deadline is not None and time.time() > deadline:
                out["more"] = True
                break
            try:
                records, counts = _build_records(conn, batch)
                payload = _payload(records, "bills")
                sha = _sha(payload)
                year = max((r.get("svc_hi") or "0000")[:4] for r in records)
                name = f"bills/{year}/b-{sha[:24]}.jsonl.gz"
                rows = _check_payload(payload, {r["bill"]: r["digest"] for r in records}, "bill")
                gz = _gz(payload)
                put = _put_verified(sink, name, gz, sha)
                meta = {"sha256": sha, "gz_bytes": put["gz_bytes"], "raw_bytes": len(payload), "rows": rows,
                        "svc_lo": min((r.get("svc_lo") or "") for r in records) or None,
                        "svc_hi": max((r.get("svc_hi") or "") for r in records) or None,
                        "horizon_months": p["horizon"]["months"], "cutoff": p["cutoff"]}
                years = {y for r in records for y in (r.get("years") or [])}
                del payload, gz
                done = _commit_batch(conn, records, counts, name, meta, at=at, run_id=run_id, years=years, today=today)
            except ArchiveError as exc:
                out["errors"].append(str(exc)[:300])
                log(f"  archive: a batch was not archived ({str(exc)[:120]})")
                break
            kept = set(done["kept_bills"])
            out["bills"] += done["bills"]
            out["changed_meanwhile"] += done["changed"]
            for t, n in done["rows"].items():
                out["rows"][t] = out["rows"].get(t, 0) + n
            out["claims"] = out["rows"].get("claims", 0)
            out["lines"] = out["rows"].get("lines", 0)
            out["findings"] = out["rows"].get("findings", 0)
            out["files"].append({"name": name, "bills": len(kept), "gz_bytes": meta["gz_bytes"], "raw_bytes": meta["raw_bytes"],
                                 "written": put["written"]})
            log(f"  archive: {len(kept):,} bills ({done['rows'].get('lines', 0):,} lines) to {name}")
    if ar and sink is not None and not p["blocked"]:
        try:
            out["ar"] = archive_ar(conn, sink, today=today, run_id=run_id, log=log)
        except ArchiveError as exc:
            out["errors"].append(str(exc)[:300])
    for t in ("arc_cb", "arc_xb", "arc_dates", "arc_bd", "arc_b", "arc_bc", "arc_bl", "arc_bp", "arc_bf"):
        conn.execute(f"DROP TABLE IF EXISTS temp.{t}")
    if sink is not None:            # a dry run (no archive) leaves the store as it was
        h = dict(p["horizon"], at=at)
        _meta_put(conn, META_HORIZON, {k: h[k] for k in ("months", "configured", "floor", "why", "tightened", "at")})
        _meta_put(conn, META_SUMMARY, summary(conn))
        _meta_put(conn, "archive:waiting", {"years": p["waiting_years"], "bills": p["waiting_rates"], "at": at})
    conn.commit()
    out["seconds"] = round(time.time() - t0, 2)
    return out


def summary(conn: sqlite3.Connection) -> dict:
    """The archive's totals from its index (no file read): files, bytes, bills, items by state and year."""
    ensure_schema(conn)
    f = conn.execute("SELECT COUNT(*), COALESCE(SUM(gz_bytes),0), COALESCE(SUM(raw_bytes),0), MIN(svc_lo), MAX(svc_hi) FROM archive_files").fetchone()
    by_kind = {k: {"files": n, "gz_bytes": b} for k, n, b in conn.execute(
        "SELECT kind, COUNT(*), COALESCE(SUM(gz_bytes),0) FROM archive_files GROUP BY kind")}
    b = conn.execute("SELECT COUNT(*), COALESCE(SUM(claims),0), COALESCE(SUM(lines),0) FROM archive_bills").fetchone()
    states = {str(k): {"n": n, "cents": c} for k, n, c in conn.execute(
        """SELECT COALESCE(work_state, status, 'unknown') k, COUNT(*), COALESCE(SUM(shortfall_cents),0) FROM archive_findings
           GROUP BY k ORDER BY k""")}
    years = [{"year": y, "bills": n} for y, n in conn.execute(
        "SELECT substr(svc_hi,1,4) y, COUNT(*) FROM archive_bills GROUP BY y ORDER BY y")]
    recovered = conn.execute("SELECT COALESCE(SUM(recovered_cents),0) FROM archive_findings").fetchone()[0]
    return {"files": f[0], "gz_bytes": f[1], "raw_bytes": f[2], "svc_lo": f[3], "svc_hi": f[4], "by_kind": by_kind,
            "bills": b[0], "claims": b[1], "lines": b[2],
            "findings": conn.execute("SELECT COUNT(*) FROM archive_findings").fetchone()[0], "by_state": states,
            "recovered_cents": recovered, "by_year": years,
            "pins": conn.execute("SELECT COUNT(*) FROM archive_pins").fetchone()[0],
            "requests": conn.execute("SELECT COUNT(*) FROM archive_requests").fetchone()[0]}


# ---------------------------------------------------------------------------------------------------- A/R snapshots
def archive_ar(conn: sqlite3.Connection, sink: Any, *, today: Any = None, months: int = AR_MONTHS, run_id: str | None = None,
               log: Callable[..., Any] | None = None) -> dict:
    """A/R snapshots pulled more than 13 months ago leave the store: each file's rows (and its catalog row) to
    ar/<as_of>-<sha>.jsonl.gz, read back and checked; then its rows go and its catalog row says where they went
    (status replaced, no live rows). The item history (ema_ar_items) stays: it has its own retention."""
    log = log or _noop
    ensure_schema(conn)
    if not _have(conn, "ema_ar"):
        return {"files": 0, "rows": 0}
    today = _today(today)
    cut = _exact_months_before(today, months)
    out = {"files": 0, "rows": 0, "names": []}
    olds = conn.execute("SELECT file_id, MAX(as_of), COUNT(*) FROM ema_ar GROUP BY file_id HAVING MAX(as_of) < ? ORDER BY 2, 1",
                        (cut,)).fetchall()
    for file_id, as_of, _n in olds:
        cols, rows = _fetch(conn, "SELECT * FROM ema_ar WHERE file_id = ? ORDER BY row_no", (file_id,))
        tables = {"ema_ar": {"cols": cols, "rows": [[_enc(v) for v in r] for r in rows]}}
        if _have(conn, "ema_report_files"):
            ccols, crow = _fetch(conn, "SELECT * FROM ema_report_files WHERE file_id = ?", (file_id,))
            if crow:
                tables["ema_report_files"] = {"cols": ccols, "rows": [[_enc(v) for v in r] for r in crow]}
        rec = {"kind": "ar", "file_id": file_id, "as_of": as_of, "tables": tables, "v": FORMAT_VERSION}
        rec["digest"] = _record_digest(rec)
        payload = _payload([rec], "ar")
        sha = _sha(payload)
        _check_payload(payload, {file_id: rec["digest"]}, "file_id")
        name = f"ar/{str(as_of)[:10]}-{sha[:24]}.jsonl.gz"
        put = _put_verified(sink, name, _gz(payload), sha)
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        try:
            n = conn.execute("DELETE FROM ema_ar WHERE file_id = ?", (file_id,)).rowcount
            if n != len(rows):
                raise ArchiveError("the A/R rows leaving the store do not match the rows archived: nothing was deleted")
            if _have(conn, "ema_report_files"):
                conn.execute("""UPDATE ema_report_files SET status = 'replaced', live_rows = 0, replaced_by = ?, status_reason = ?
                                WHERE file_id = ?""",
                             ("archive:" + name, f"Moved to the archive on {today}: pulled more than {months} months ago.", file_id))
            conn.execute("""INSERT OR REPLACE INTO archive_files (name, kind, sha256, gz_bytes, raw_bytes, records, rows, svc_lo, svc_hi,
                            created_at, run_id, horizon_months, cutoff, detail) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         (name, "ar", sha, put["gz_bytes"], len(payload), 1, len(rows), str(as_of)[:10], str(as_of)[:10], _now(),
                          run_id, None, cut, _canon({"file_id": file_id})))
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        out["files"] += 1
        out["rows"] += n
        out["names"].append(name)
        log(f"  archive: an A/R snapshot of {str(as_of)[:10]} ({n:,} rows) to {name}")
    return out


# ------------------------------------------------------------------------------------------------- the ledger hook
LEDGER_DDL = """CREATE TABLE IF NOT EXISTS archive_parts (name TEXT PRIMARY KEY, svc_lo TEXT, svc_hi TEXT, rows INTEGER,
    sha256 TEXT, cutoff TEXT, at TEXT)"""


def attach_ledger(ledger: Any, sink: Any) -> Any:
    """Tell a ledger where its retention archives postings (the cycle calls this right after opening it)."""
    ledger.archive_sink = sink
    return ledger


def _ledger_sink(ledger: Any) -> Any:
    sink = getattr(ledger, "archive_sink", None)
    if sink is not None:
        return sink
    if getattr(ledger, "scratch", False) or getattr(ledger, "readonly", False) or getattr(ledger, "path", ":memory:") == ":memory:":
        return None         # a serverless scratch copy without a state folder: nowhere lasting to write
    return DirSink(os.path.join(os.path.dirname(os.path.abspath(ledger.path)), "archive"))


def before_ledger_retain(ledger: Any, cutoff: str, *, sink: Any = None, run_id: str | None = None,
                         log: Callable[..., Any] | None = None) -> dict:
    """The hook in Ledger.retain, right before it deletes postings with service before `cutoff`: every such posting is
    written to ledger/<lo>_<hi>-<sha>.jsonl.gz (grouped by EMA claim number, names written out, its row id kept), read back
    and checked, and recorded in the ledger's own archive_parts. Raises ArchiveError when the file cannot be written: the
    caller then deletes nothing. Returns {'rows', 'file'} or {'skipped': why}."""
    log = log or _noop
    sink = sink if sink is not None else _ledger_sink(ledger)
    if sink is None:
        return {"skipped": "no archive folder for this ledger"}
    c = ledger.conn
    if c.execute("SELECT COUNT(*) FROM postings WHERE svc < ?", (cutoff,)).fetchone()[0] == 0:
        return {"rows": 0}
    names = {i: n for i, n in c.execute("SELECT id, name FROM names")}
    cols = [r[1] for r in c.execute("PRAGMA table_info(postings)")]
    named = [cols.index(f) for f in NAMED_FIELDS if f in cols]
    files = {}
    try:
        fcols = [r[1] for r in c.execute("PRAGMA table_info(ledger_files)")]
        files = {r[0]: dict(zip(fcols, r)) for r in c.execute("SELECT * FROM ledger_files")}
    except sqlite3.Error:
        pass
    raw = io.BytesIO()
    h = hashlib.sha256()
    n_rows = 0
    lo = hi = None
    srcs: set = set()
    with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=6, mtime=0, filename="") as gz:
        def emit(obj: dict) -> None:
            b = (_canon(obj) + "\n").encode("utf-8")
            h.update(b)
            gz.write(b)
        emit({"format": FORMAT, "v": FORMAT_VERSION, "kind": "header", "of": "ledger", "cutoff": cutoff})
        cur_link, group = object(), []

        def flush() -> None:
            if group:
                rec = {"kind": "ledger", "link": None if cur_link is None else cur_link, "tables":
                       {"postings": {"cols": ["rid"] + cols, "rows": group}}, "v": FORMAT_VERSION}
                rec["digest"] = _record_digest(rec)
                emit(rec)
        for sql in ("SELECT rowid, * FROM postings WHERE link IS NOT NULL AND svc < ? ORDER BY link, rowid",
                    "SELECT rowid, * FROM postings WHERE link IS NULL AND svc < ? ORDER BY rowid"):
            for r in c.execute(sql, (cutoff,)):
                r = list(r)
                vals = r[1:]
                for i in named:
                    if vals[i] is not None:
                        vals[i] = names.get(vals[i], vals[i])
                link = vals[cols.index("link")] if "link" in cols else None
                if link != cur_link:
                    flush()
                    cur_link, group = link, []
                group.append([_enc(r[0])] + [_enc(v) for v in vals])
                n_rows += 1
                svc = vals[cols.index("svc")]
                lo = svc if lo is None or svc < lo else lo
                hi = svc if hi is None or svc > hi else hi
                if "src" in cols:
                    srcs.add(vals[cols.index("src")])
        flush()
        emit({"kind": "ledger_files", "rows": [{k: _enc(v) for k, v in files[s].items()} for s in sorted(srcs, key=str) if s in files]})
    gz_bytes = raw.getvalue()
    sha = h.hexdigest()
    name = f"ledger/{str(lo)[:10]}_{str(hi)[:10]}-{sha[:24]}.jsonl.gz"
    # check it before writing: every record's digest, and the rows counted
    back_rows = 0
    data = _gunzip(gz_bytes)
    if _sha(data) != sha:
        raise ArchiveError("the ledger archive did not compress back to what was written")
    for ln in data.split(b"\n")[1:]:
        if not ln.strip():
            continue
        obj = json.loads(ln)
        if obj.get("kind") == "ledger":
            if _record_digest(obj) != obj.get("digest"):
                raise ArchiveError("a ledger archive record reads back with other content")
            back_rows += len(obj["tables"]["postings"]["rows"])
    if back_rows != n_rows:
        raise ArchiveError("the ledger archive holds another number of postings than were read")
    del data
    put = _put_verified(sink, name, gz_bytes, sha)
    c.execute(LEDGER_DDL)
    c.execute("INSERT OR REPLACE INTO archive_parts (name, svc_lo, svc_hi, rows, sha256, cutoff, at) VALUES (?,?,?,?,?,?,?)",
              (name, lo, hi, n_rows, sha, cutoff, _now()))
    if c.in_transaction:        # Ledger.retain begins its own transaction next: nothing of ours may be left open
        c.commit()
    log(f"  archive: {n_rows:,} ledger postings (service {str(lo)[:7]} to {str(hi)[:7]}) to {name}")
    return {"rows": n_rows, "file": name, "gz_bytes": put["gz_bytes"], "written": put["written"]}


def ledger_parts(sink: Any) -> list[dict]:
    """The ledger archive files with their service range, from their names."""
    out = []
    for name in sink.list("ledger"):
        m = re.match(r"ledger/(\d{4}-\d{2}-\d{2}|None)_(\d{4}-\d{2}-\d{2}|None)-([0-9a-f]+)\.jsonl\.gz$", name)
        if m:
            out.append({"name": name, "svc_lo": m.group(1), "svc_hi": m.group(2)})
    return out


def ledger_postings(sink: Any, links: Iterable[int], *, svc_lo: str | None = None, svc_hi: str | None = None,
                    limit: int = 500) -> list[dict]:
    """The archived ledger postings of these EMA claim numbers (the claim view of an old bill, server only), oldest
    first; a posting archived twice (a run that died between writing the file and deleting) is shown once."""
    want = {int(x) for x in links if x is not None}
    if not want:
        return []
    out, seen = [], set()
    for part in ledger_parts(sink):
        if svc_hi and part["svc_lo"] not in ("None",) and part["svc_lo"] > svc_hi:
            continue
        if svc_lo and part["svc_hi"] not in ("None",) and part["svc_hi"] < svc_lo:
            continue
        for obj in _read_lines(sink, part["name"]):
            if obj.get("kind") != "ledger" or obj.get("link") not in want:
                continue
            t = obj["tables"]["postings"]
            for r in t["rows"]:
                d = dict(zip(t["cols"], [_dec(v) for v in r]))
                key = _canon(d)
                if key in seen:
                    continue
                seen.add(key)
                d.pop("rid", None)
                for k in ("person", "name_key"):
                    d.pop(k, None)
                out.append(d)
    out.sort(key=lambda d: (str(d.get("posted") or ""), str(d.get("svc") or "")))
    return out[:limit]


# --------------------------------------------------------------------------------------------- the frozen baselines
def apply_frozen_rates(det: Any, *, today: Any = None) -> dict:
    """The hook in Detector.build_baselines (best right after the market figures, before the Medicare figures and the
    payer_rates table are built from the rates; at the end of build_baselines works too: it then sets the Medicare
    figures and the payer_rates rows of those years itself). For a service year the archive took lines of (locked), the
    rates, the counts behind them, the cross-payer and Medicare figures are the ones measured on the whole year before
    the archive took anything; for every other year old enough to be archived (before today less the floor), a fresh
    snapshot is kept. Returns {'locked', 'overridden', 'snapshot'}."""
    conn = det.conn
    ensure_schema(conn)
    today = _today(today or getattr(det, "today", None))
    locked = _locked_years(conn)
    out = {"locked": sorted(locked), "overridden": 0, "snapshot": []}
    rates = getattr(det, "rates", None)
    if rates is None:
        return out
    counts = getattr(det, "rate_counts", None)
    market = getattr(det, "market", None)
    kinds = getattr(det, "kinds", None)
    medicare = getattr(det, "medicare", None)       # set only when called at the end of build_baselines
    if locked:
        q = ",".join("?" * len(locked))
        frozen_rates = {}
        frozen_counts = {}
        for grp, year, code, tier, pos, kind, mode, med, p10, p90, n, share, cnt in conn.execute(
                f"""SELECT payer_group, year, code, tier, pos_class, payer_kind, mode_cents, median_cents, p10_cents, p90_cents, n,
                           mode_share, counts FROM archive_rates WHERE year IN ({q})""", sorted(locked)):
            key = (grp, year, code, tier, pos)
            frozen_rates[key] = {"mode": mode, "median": med, "p10": p10, "p90": p90, "n": n, "share": share, "key": key}
            try:
                frozen_counts[key] = Counter({int(a): int(b) for a, b in json.loads(cnt or "{}").items()})
            except (ValueError, TypeError):
                frozen_counts[key] = Counter()
            if kinds is not None and kind:
                kinds.setdefault(grp, kind)
        for key in [k for k in rates if k[1] in locked]:
            del rates[key]
        rates.update(frozen_rates)
        out["overridden"] = len(frozen_rates)
        if counts is not None:
            for key in [k for k in counts if k[1] in locked]:
                del counts[key]
            for key, cnt in frozen_counts.items():
                counts[key] = cnt
        frozen_medicare = {}
        frozen_market = {}
        for year, code, tier, pos, med, n, mcare in conn.execute(
                f"SELECT year, code, tier, pos_class, median_cents, n, medicare_cents FROM archive_market WHERE year IN ({q})",
                sorted(locked)):
            if med is not None:
                frozen_market[(year, code, tier, pos)] = {"median": med, "n": n}
            if mcare is not None:
                frozen_medicare[(year, code, tier, pos)] = mcare
        if market is not None:
            for key in [k for k in market if k[0] in locked]:
                del market[key]
            market.update(frozen_market)
        if medicare is not None:
            for key in [k for k in medicare if k[0] in locked]:
                del medicare[key]
            medicare.update(frozen_medicare)
            _rewrite_payer_rates(det, locked)
    # the snapshot of every year that could be archived before the next run
    top = _months_before(today, MIN_HORIZON_MONTHS)[:4]
    years = {str(l["year"]) for l in getattr(det, "lines", []) if l["year"]} | {k[1] for k in rates} | \
        ({k[0] for k in market} if market is not None else set())
    cand = sorted(y for y in years if y and y.isdigit() and y <= top and y not in locked)
    frozen = _frozen_years(conn)
    for y in cand:
        rows = []
        for key, r in rates.items():
            if key[1] != y:
                continue
            cnt = counts.get(key) if counts is not None else None
            rows.append((key[0], y, key[2], key[3], key[4], (kinds or {}).get(key[0]), r.get("mode"), r.get("median"),
                         r.get("p10"), r.get("p90"), r.get("n"), r.get("share"),
                         _canon({str(a): b for a, b in sorted((cnt or {}).items())})))
        mkeys = {k for k in (market or {}) if k[0] == y} | {k for k in (medicare or {}) if k[0] == y}
        mrows = [(k[0], k[1], k[2], k[3], (market or {}).get(k, {}).get("median"), (market or {}).get(k, {}).get("n"),
                  (medicare or {}).get(k)) for k in mkeys]
        digest = _sha(_canon([sorted(rows, key=str), sorted(mrows, key=str)]))
        if (frozen.get(y) or {}).get("digest") == digest:
            continue
        conn.execute("DELETE FROM archive_rates WHERE year = ?", (y,))
        conn.execute("DELETE FROM archive_market WHERE year = ?", (y,))
        conn.executemany("INSERT OR REPLACE INTO archive_rates VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        conn.executemany("INSERT OR REPLACE INTO archive_market VALUES (?,?,?,?,?,?,?)", mrows)
        frozen[y] = {"at": today, "digest": digest, "rates": len(rows)}
        out["snapshot"].append(y)
    if out["snapshot"]:
        _meta_put(conn, META_FROZEN, frozen)
    return out


# the items whose inputs never leave the store (provider-level adjustments, EMA's own reports): measured afresh every run
CARRY_SKIP = ("FEE_SKIM", "TAKEBACK")


def _aggregate(f: dict) -> bool:
    return not f.get("line_key") and not f.get("claim_key") and not f.get("patient_control_number") \
        and f.get("signal") not in CARRY_SKIP and str(f.get("source") or "") != "ema"


def carry_locked_aggregates(det: Any) -> dict:
    """The hook right before Detector.save: the aggregate items of a locked year (a year the archive took lines of: the
    payer's yearly totals, its routine second rates, the contract figures) were measured on the whole year. Measured
    again on what the archive left they would shrink, vanish or appear from nothing, so the stored ones are kept as they
    were (a person's status and notes stay theirs: save carries them as for any item), a new one is dropped, and an
    item on a line such a new one took is put back as it was. Pooled items with no year (a payer's multiple-procedure
    practice) are measured afresh. Returns {'kept', 'dropped', 'put_back'}."""
    conn = det.conn
    out = {"kept": 0, "dropped": 0, "put_back": 0}
    locked = _locked_years(conn)
    findings = getattr(det, "findings", None)
    if not locked or findings is None or not _have(conn, "findings"):
        return out
    cols = _cols(conn, "findings")
    q = ",".join("?" * len(locked))
    stored = {}
    for r in conn.execute(f"SELECT * FROM findings WHERE year IN ({q}) AND COALESCE(stale,0) = 0", sorted(locked)):
        d = dict(zip(cols, r))
        if _aggregate(d):
            stored[d["finding_id"]] = d
    taken: set[str] = set()
    for fid, f in list(findings.items()):
        if str(f.get("year") or "") in locked and _aggregate(f) and fid not in stored:
            try:
                ev = json.loads(f.get("evidence") or "{}") if isinstance(f.get("evidence"), str) else (f.get("evidence") or {})
            except ValueError:
                ev = {}
            taken.update(str(x) for x in (ev.get("line_keys") or []) if x)
            del findings[fid]
            out["dropped"] += 1
    for fid, d in stored.items():
        findings[fid] = dict(d)
        out["kept"] += 1
    if taken:
        lk = sorted(taken)
        for i in range(0, len(lk), 500):
            part = lk[i:i + 500]
            for r in conn.execute(f"SELECT * FROM findings WHERE line_key IN ({','.join('?' * len(part))}) AND COALESCE(stale,0) = 0", part):
                d = dict(zip(cols, r))
                if d["finding_id"] not in findings:
                    findings[d["finding_id"]] = d
                    out["put_back"] += 1
    return out


def _rewrite_payer_rates(det: Any, locked: set[str]) -> None:
    """At the end of build_baselines: the payer_rates rows of the locked years from the frozen rates (the table the
    pages read), in build_baselines' own form."""
    conn = det.conn
    if not _have(conn, "payer_rates") or not locked:
        return
    q = ",".join("?" * len(locked))
    conn.execute(f"DELETE FROM payer_rates WHERE year IN ({q})", sorted(locked))
    rows = []
    kinds = getattr(det, "kinds", {}) or {}
    for (grp, year, code, tier, pos), r in det.rates.items():
        if year not in locked:
            continue
        med = det.medicare.get((year, code, tier, pos))
        rows.append((grp, kinds.get(grp, "other"), year, code, tier, r["mode"], r["median"], r["p10"], r["p90"], r["n"],
                     round(r["share"], 3) if r.get("share") is not None else None, med,
                     round(r["mode"] / med, 4) if med else None, pos))
    conn.executemany("""INSERT OR REPLACE INTO payer_rates (payer_group, payer_kind, year, code, tier, mode_cents, median_cents,
                        p10_cents, p90_cents, n, mode_share, medicare_cents, pct_of_medicare, pos_class)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)


# ------------------------------------------------------------------------------------------------- finding it again
_LAST_FILE: dict = {}          # the last archive file read, uncompressed (a claim view often opens a few bills of one file)
_LAST_LOCK = threading.Lock()  # the dashboard answers requests on several threads


def _file_bytes(sink: Any, name: str) -> bytes:
    key = (id(sink), getattr(sink, "root", None) or getattr(sink, "prefix", None), name)
    with _LAST_LOCK:
        if _LAST_FILE.get("key") == key:
            return _LAST_FILE["data"]
    data = sink.get(name)
    if data is None:
        raise ArchiveError(f"{name} is not in the archive")
    raw = _gunzip(data)
    with _LAST_LOCK:
        _LAST_FILE.clear()
        _LAST_FILE.update(key=key, data=raw)
    return raw


def _find_record(raw: bytes, field: str, value: str) -> dict | None:
    """The one record of a file whose `field` is `value` (the file is canonical JSON: the field is written exactly so),
    checked against its digest. Only that line is parsed."""
    needle = (f'"{field}":' + json.dumps(value, ensure_ascii=False)).encode("utf-8")
    for ln in raw.split(b"\n"):
        if needle not in ln:
            continue
        obj = json.loads(ln)
        if obj.get(field) != value:
            continue
        if _record_digest(obj) != obj.get("digest"):
            raise ArchiveError("an archived record reads back with other content")
        return obj
    return None


def _record_of(conn: sqlite3.Connection, sink: Any, bill: str) -> tuple[dict, dict] | None:
    row = conn.execute("SELECT file, digest, archived_at, svc_lo, svc_hi FROM archive_bills WHERE bill = ?", (bill,)).fetchone()
    if not row:
        return None
    name, digest, archived_at, lo, hi = row
    rec = _find_record(_file_bytes(sink, name), "bill", bill)
    if rec is None or rec.get("digest") != digest:
        raise ArchiveError("an archived bill is not in the file its index names")
    return rec, {"file": name, "archived_at": archived_at, "svc_lo": lo, "svc_hi": hi}


def resolve_bill(conn: sqlite3.Connection, text: str) -> str | None:
    """The archived bill a claim id, a bill id or a payer's claim number names, or None."""
    ensure_schema(conn)
    raw = re.sub(r"\s+", "", str(text or "")).upper()
    if not raw:
        return None
    for cand in (_bill_of(raw), raw):
        if cand and conn.execute("SELECT 1 FROM archive_bills WHERE bill = ?", (cand,)).fetchone():
            return cand
    r = conn.execute("SELECT bill FROM archive_ids WHERE id = ? AND kind IN ('pcn','ema','837') LIMIT 1", (raw,)).fetchone() \
        or conn.execute("SELECT bill FROM archive_ids WHERE id = ? AND kind = 'payer_ref' LIMIT 1", (_norm_ref(text),)).fetchone()
    return r[0] if r else None


def lookup_claim(conn: sqlite3.Connection, claim_or_bill: str, *, sink: Any) -> dict | None:
    """Everything the archive holds for one bill (by claim id, bill id or payer claim number): {'bill', 'file',
    'archived_at', 'svc_lo', 'svc_hi', 'tables': {table: [row dicts]}}, or None when it is not archived. The rows are
    the store's rows as they were (patient details included: this is for the signed-in claim view, as the store is)."""
    bill = resolve_bill(conn, claim_or_bill)
    if not bill:
        return None
    got = _record_of(conn, sink, bill)
    if got is None:
        return None
    rec, where = got
    tables = {t: [dict(zip(x["cols"], [_dec(v) for v in r])) for r in x["rows"]] for t, x in rec["tables"].items()}
    return dict(where, bill=bill, tables=tables, archived=True)


def lookup_finding(conn: sqlite3.Connection, finding_id: str, *, sink: Any) -> dict | None:
    """An archived item by its id (a deep link to an item that left the worklist): {'finding', 'events', 'bill', ...}."""
    ensure_schema(conn)
    r = conn.execute("SELECT bill FROM archive_findings WHERE finding_id = ?", (finding_id,)).fetchone()
    if not r:
        return None
    got = lookup_claim(conn, r[0], sink=sink)
    if not got:
        return None
    f = next((x for x in got["tables"].get("findings", []) if x.get("finding_id") == finding_id), None)
    if f is None:
        return None
    events = [e for e in got["tables"].get("finding_events", []) if e.get("finding_id") == finding_id]
    events.append({"finding_id": finding_id, "at": got["archived_at"], "kind": "archived", "from_status": f.get("status"),
                   "to_status": f.get("status"), "actor": "system", "note": archived_note(f)})
    return {"finding": f, "events": events, "bill": got["bill"], "file": got["file"], "archived_at": got["archived_at"],
            "archived": True}


def archived_note(f: dict) -> str:
    """Why an item left the worklist, in words (the item's history shows it)."""
    ws = f.get("work_state") or f.get("status") or "closed"
    words = {"expired": "every window had closed", "done": "it was settled", "accepted": "the loss was accepted",
             "resolved": "it was marked paid", "lost": "it was marked lost", "dismissed": "it was let go"}.get(str(ws), "it was closed")
    return (f"Moved to the archive: {words}, and its service is older than the retention horizon. Rule {RULE} "
            f"version {RULE_VERSION}. Find a claim still opens it; reopening brings it back.")


def search(conn: sqlite3.Connection, text: str, *, limit: int = 25) -> list[dict]:
    """Archived bills a Find a claim search names: the start of a claim id (3 characters or more), a bill id, or a payer's
    claim number (exact, or its first 6 characters or more). One row per bill: {'bill', 'claim_ids', 'svc_lo',
    'svc_hi', 'archived_at', 'findings', 'file', 'archived': True}. Names are not searched in the archive."""
    ensure_schema(conn)
    up = re.sub(r"\s+", "", str(text or "")).upper()[:200]
    if len(up) < 3:
        return []
    found: list[str] = []
    for (b,) in conn.execute("""SELECT DISTINCT bill FROM archive_ids WHERE kind IN ('pcn','ema','837') AND id >= ? AND id < ?
                                LIMIT 200""", (up, up + "￿")):
        found.append(b)
    for cand in (_bill_of(up), up):
        if conn.execute("SELECT 1 FROM archive_bills WHERE bill = ?", (cand,)).fetchone():
            found.append(cand)
    norm = _norm_ref(text)
    if norm:
        for (b,) in conn.execute("""SELECT DISTINCT bill FROM archive_ids WHERE kind = 'payer_ref' AND (id = ? OR (? >= 6 AND id >= ? AND id < ?))
                                    LIMIT 100""", (norm, len(norm), norm, norm + "￿")):
            found.append(b)
    out, seen = [], set()
    for b in found:
        if b in seen:
            continue
        seen.add(b)
        r = conn.execute("SELECT bill, file, svc_lo, svc_hi, archived_at, findings, claims, lines FROM archive_bills WHERE bill = ?",
                         (b,)).fetchone()
        if not r:
            continue
        ids = [i for (i,) in conn.execute("SELECT DISTINCT id FROM archive_ids WHERE bill = ? AND kind IN ('pcn','ema','837') ORDER BY id", (b,))]
        out.append({"bill": r[0], "file": r[1], "svc_lo": r[2], "svc_hi": r[3], "archived_at": r[4], "findings": r[5],
                    "claims": r[6], "lines": r[7], "claim_ids": ids, "exact": b == up or b == _bill_of(up), "archived": True})
        if len(out) >= limit:
            break
    out.sort(key=lambda x: str(x["svc_hi"] or ""), reverse=True)      # the newest service first, the exact bill above all
    out.sort(key=lambda x: not x["exact"])
    return out


def claim_db(conn: sqlite3.Connection, claim_or_bill: str, *, sink: Any) -> sqlite3.Connection | None:
    """An in-memory database holding one archived bill under the store's own table names and schema (plus the store's
    meta, less any secret), so the claim view's and the story's own queries run on it unchanged. None when the bill is
    not archived. The caller closes it."""
    got = lookup_claim(conn, claim_or_bill, sink=sink)
    if got is None:
        return None
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.row_factory = sqlite3.Row
    tables = set(got["tables"]) | {"claims", "lines", "adjustments", "remarks", "findings", "finding_events", "transactions"}
    for name, sql in conn.execute("SELECT name, sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL"):
        if name in tables or name in ("meta", "plb", "payer_rates", "ema_report_files", "detection_runs") or name.startswith("ema_"):
            try:
                db.execute(sql)
            except sqlite3.Error:
                pass
    for name, sql in conn.execute("SELECT name, sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"):
        try:
            db.execute(sql)
        except sqlite3.Error:
            pass
    for table, rows in got["tables"].items():
        if not rows or not _have(db, table):
            continue
        have = set(_cols(db, table))
        cols = [c for c in rows[0] if c in have]
        db.executemany(f"INSERT OR IGNORE INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                       [tuple(r.get(c) for c in cols) for r in rows])
    try:
        db.executemany("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                       [(k, v) for k, v in conn.execute("SELECT key, value FROM meta WHERE key NOT IN ('ema:key_salt') "
                                                         "AND key NOT LIKE 'sync:cursor%' AND length(value) < 400000")])
    except sqlite3.Error:
        pass
    for table in ("payer_rates", "detection_runs", "ema_report_files"):
        if _have(conn, table) and _have(db, table):
            cols = [c for c in _cols(conn, table) if c in set(_cols(db, table))]
            db.executemany(f"INSERT OR IGNORE INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                           conn.execute(f"SELECT {','.join(cols)} FROM {table}").fetchall())
    # the payers' provider-level adjustments that name one of the bill's claim ids (takebacks, held money): they stay in
    # the store with the remittances, and the claim view and the story read them by reference
    if _have(conn, "plb") and _have(db, "plb") and "reference" in _cols(conn, "plb"):
        ids = {str(r.get("patient_control_number")) for t in ("claims", "claims_837") for r in got["tables"].get(t, [])
               if r.get("patient_control_number")} | {str(r.get("claim_id")) for r in got["tables"].get("ema_rows", []) if r.get("claim_id")}
        ids = {i for i in ids if len(i) >= 3}
        cols = _cols(conn, "plb")
        refs = cols.index("reference")
        hits = [r for r in conn.execute(f"SELECT {','.join(cols)} FROM plb WHERE reference IS NOT NULL")
                if any(i in str(r[refs]) for i in ids)]
        db.executemany(f"INSERT OR IGNORE INTO plb ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [tuple(r) for r in hits])
    db.commit()
    return db


# ------------------------------------------------------------------------------------------------- bringing it back
def _put_rows(conn: sqlite3.Connection, table: str, rows: list[dict]) -> int:
    if not rows or not _have(conn, table):
        return 0
    have = set(_cols(conn, table))
    cols = [c for c in rows[0] if c in have]
    if not cols:
        return 0
    before = conn.total_changes
    conn.executemany(f"INSERT OR IGNORE INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                     [tuple(r.get(c) for c in cols) for r in rows])
    return conn.total_changes - before


def _put_findings(conn: sqlite3.Connection, rows: list[dict]) -> int:
    """Archived items back: an item the store lists again meanwhile keeps a person's work: the archived row replaces a
    newer row nobody worked; a newer row a person worked stays."""
    if not rows or not _have(conn, "findings"):
        return 0
    have = set(_cols(conn, "findings"))
    n = 0
    for r in rows:
        cols = [c for c in r if c in have]
        cur = conn.execute("SELECT status, notes, appealed_at FROM findings WHERE finding_id = ?", (r.get("finding_id"),)).fetchone()
        if cur is not None:
            if _worked(*cur) or not _worked(r.get("status"), r.get("notes"), r.get("appealed_at")):
                continue
            conn.execute("DELETE FROM findings WHERE finding_id = ?", (r.get("finding_id"),))
        conn.execute(f"INSERT INTO findings ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", tuple(r.get(c) for c in cols))
        n += 1
    return n


def restore(conn: sqlite3.Connection, bill_or_claim: str, *, sink: Any, actor: str = "", reason: str = "reopened",
            run_id: str | None = None, today: Any = None, pin_days: int = PIN_DAYS, log: Callable[..., Any] | None = None) -> dict:
    """Bring an archived bill back into the store (a person reopened one of its items, or new data arrived for it): its
    rows go back in one transaction, its index rows go, and it stays hot for `pin_days`. The archive file stays (never
    deleted). Run it from the cycle (process_requests, restore_touched), never beside a running detection. Returns
    {'bill', 'rows': {table: n}, 'findings'} or {'missing': True}."""
    log = log or _noop
    ensure_schema(conn)
    today = _today(today)
    bill = resolve_bill(conn, bill_or_claim)
    if not bill:
        return {"missing": True}
    got = _record_of(conn, sink, bill)
    if got is None:
        return {"missing": True}
    rec, where = got
    tables = {t: [dict(zip(x["cols"], [_dec(v) for v in r])) for r in x["rows"]] for t, x in rec["tables"].items()}
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        rows: dict[str, int] = {}
        for table in MOVED:
            if table == "findings":
                rows[table] = _put_findings(conn, tables.get(table) or [])
            else:
                rows[table] = _put_rows(conn, table, tables.get(table) or [])
        for table in RESTORE_COPIES:
            rows[table] = _put_rows(conn, table, tables.get(table) or [])
        conn.execute("DELETE FROM archive_bills WHERE bill = ?", (bill,))
        conn.execute("DELETE FROM archive_ids WHERE bill = ?", (bill,))
        conn.execute("DELETE FROM archive_findings WHERE bill = ?", (bill,))
        conn.execute("INSERT OR REPLACE INTO archive_pins (bill, until, why, at) VALUES (?,?,?,?)",
                     (bill, (_d(today) + timedelta(days=int(pin_days))).isoformat(), str(reason)[:200], _now()))
        if _have(conn, "ema_lines"):
            _meta_put(conn, "ema:keep_dirty", "1")      # the ledger's next summary keeps the bill's EMA lines
        hist = _meta_json(conn, "archive:restores", [])
        hist.append({"at": _now(), "run_id": run_id, "reason": str(reason)[:80], "actor": bool(actor),
                     "rows": sum(rows.values()), "file": where["file"]})
        _meta_put(conn, "archive:restores", hist[-200:])
        conn.execute("COMMIT")
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    log(f"  archive: a bill came back from {where['file']} ({sum(rows.values()):,} rows; {reason})")
    return {"bill": bill, "rows": rows, "findings": rows.get("findings", 0), "file": where["file"], "changed": True}


def request_restore(conn: sqlite3.Connection, bill_or_claim: str, *, actor: str = "", reason: str = "reopened",
                    finding_id: str | None = None, status: str | None = "open", at: str | None = None) -> dict:
    """A reopening asked for in the app (server): queued for the next cycle, which brings the bill back before detection
    and then sets the item's status. Returns {'queued': True, 'bill'} or {'missing': True}."""
    ensure_schema(conn)
    bill = resolve_bill(conn, bill_or_claim)
    if not bill:
        return {"missing": True}
    conn.execute("INSERT OR REPLACE INTO archive_requests (bill, finding_id, status, actor, reason, at) VALUES (?,?,?,?,?,?)",
                 (bill, finding_id, status, actor, str(reason)[:200], at or _now()))
    conn.commit()
    return {"queued": True, "bill": bill}


def journal_entry(bill_or_claim: str, *, actor: str = "", reason: str = "reopened", finding_id: str | None = None,
                  status: str | None = "open", at: str | None = None) -> dict:
    """The journal entry a serverless dashboard records for a reopening (cloud.record_action); take_journal reads it."""
    return {"kind": "archive_restore", "bill": str(bill_or_claim), "actor": actor, "reason": str(reason)[:200],
            "finding_id": finding_id, "status": status, "at": at or _now()}


def take_journal(conn: sqlite3.Connection, entries: list[tuple[str, dict]], *, sink: Any = None) -> tuple[list[str], list[tuple]]:
    """The cycle's journal (serverless): the reopenings become requests; returns (names taken, the other entries)."""
    ensure_schema(conn)
    taken, rest = [], []
    for name, a in entries:
        if isinstance(a, dict) and a.get("kind") == "archive_restore":
            request_restore(conn, str(a.get("bill") or ""), actor=a.get("actor") or "", reason=a.get("reason") or "reopened",
                            finding_id=a.get("finding_id"), status=a.get("status"), at=a.get("at"))
            taken.append(name)
        else:
            rest.append((name, a))
    return taken, rest


def process_requests(conn: sqlite3.Connection, *, sink: Any, today: Any = None, run_id: str | None = None,
                     log: Callable[..., Any] | None = None) -> dict:
    """At the start of a cycle, before detection: bring back every bill a person asked to reopen, then set the asked
    status on the item (a person's action, logged as theirs). Returns {'restored', 'statuses', 'missing', 'changed'}."""
    ensure_schema(conn)
    out = {"restored": 0, "statuses": 0, "missing": 0, "errors": 0, "changed": False}
    reqs = conn.execute("SELECT bill, finding_id, status, actor, reason, at FROM archive_requests ORDER BY at").fetchall()
    for bill, fid, status, actor, reason, at in reqs:
        archived = conn.execute("SELECT 1 FROM archive_bills WHERE bill = ?", (bill,)).fetchone() is not None
        if archived:
            try:
                res = restore(conn, bill, sink=sink, actor=actor or "", reason=reason or "reopened", run_id=run_id, today=today, log=log)
            except ArchiveError:
                out["errors"] += 1
                continue
            if res.get("missing"):
                out["missing"] += 1
            else:
                out["restored"] += 1
                out["changed"] = True
        if fid and status:
            from .actions import STATUSES, apply_status
            if status in STATUSES and conn.execute("SELECT 1 FROM findings WHERE finding_id = ?", (fid,)).fetchone():
                cur = conn.execute("SELECT status FROM findings WHERE finding_id = ?", (fid,)).fetchone()[0]
                if cur != status:
                    apply_status(conn, fid, status, None, None, actor or "", at, None)
                    out["statuses"] += 1
                    out["changed"] = True
        conn.execute("DELETE FROM archive_requests WHERE bill = ? AND at = ?", (bill, at))
        conn.commit()
    return out


# a payer's provider-level adjustments that take a claim's money back or carry it forward (an offset, a forwarding
# balance, an authorized return): naming an archived bill, they are new data for it
PLB_TOUCH = ("WO", "FB", "72")


def _ref_forms(ref: Any) -> list[str]:
    """The forms a provider-level adjustment's reference can name a claim by: the whole reference, and its first word
    (some payers add the payer's claim number or a date after the claim id)."""
    raw = str(ref or "").strip().upper()
    if not raw:
        return []
    out = [raw]
    first = re.split(r"[\s:;,]+", raw)[0]
    if first and first != raw:
        out.append(first)
    return out


def _plb_touched(conn: sqlite3.Connection) -> set[str]:
    """Archived bills a payer's offset (PLB WO, FB or 72) names, added to the store after the bill left (its row id is
    newer than the bill's plb_seen): the takeback reverses a payment the rules counted, and opens a dispute window from
    the remittance that took the money. Matched by the claim id (any submission of the bill) or the payer's claim number."""
    if not _have(conn, "plb") or "reference" not in _cols(conn, "plb"):
        return set()
    lo = conn.execute("SELECT MIN(COALESCE(plb_seen, 0)) FROM archive_bills").fetchone()[0]
    if lo is None:
        return set()
    out: set[str] = set()
    q = ",".join("?" * len(PLB_TOUCH))
    for pid, ref in conn.execute(f"""SELECT id, reference FROM plb WHERE id > ? AND reason_code IN ({q})
                                     AND COALESCE(reference, '') <> ''""", (lo,) + PLB_TOUCH).fetchall():
        bills: set[str] = set()
        for form in _ref_forms(ref):
            for cand in (_bill_of(form), form):
                if cand and conn.execute("SELECT 1 FROM archive_bills WHERE bill = ?", (cand,)).fetchone():
                    bills.add(cand)
            bills.update(b for (b,) in conn.execute("SELECT bill FROM archive_ids WHERE id = ? AND kind IN ('pcn','ema','837')", (form,)))
            norm = _norm_ref(form)
            if norm:
                bills.update(b for (b,) in conn.execute("SELECT bill FROM archive_ids WHERE id = ? AND kind = 'payer_ref'", (norm,)))
        for b in bills:
            seen = conn.execute("SELECT COALESCE(plb_seen, 0) FROM archive_bills WHERE bill = ?", (b,)).fetchone()
            if seen is not None and pid > seen[0]:
                out.add(b)
    return out


def _ema_touched(conn: sqlite3.Connection) -> set[str]:
    """Archived bills EMA's reports name again (a new A/R pull lists the claim, the posting ledger holds new postings for
    it, the claim-number map ties a claim number to it): the bill comes back so EMA's checks find its remittances."""
    if not _ema_named(conn):
        conn.execute("DROP TABLE IF EXISTS temp.arc_eb")
        return set()
    got = {b for (b,) in conn.execute("""SELECT DISTINCT bill FROM archive_ids WHERE kind IN ('pcn','ema','837')
                                         AND rw_arc_ebill(id) IN (SELECT ebill FROM temp.arc_eb)""")}
    got |= {b for (b,) in conn.execute("SELECT bill FROM archive_bills WHERE bill IN (SELECT ebill FROM temp.arc_eb)")}
    conn.execute("DROP TABLE IF EXISTS temp.arc_eb")
    return got


def restore_touched(conn: sqlite3.Connection, *, sink: Any, today: Any = None, run_id: str | None = None, limit: int = 500,
                    log: Callable[..., Any] | None = None) -> dict:
    """Before detection: an archived bill that has new rows in the store comes back whole, so the rules see its history:
    a late remittance or a reprocessing of its claim, a weekly EMA export row or an 837 for it, a payer's offset that
    takes its money back (PLB WO, FB or 72 added after it left), or EMA's reports naming it again (the A/R, the posting
    ledger's summaries, the claim-number map). Returns {'restored', 'errors', 'changed', 'more', 'why': {reason: n}}."""
    ensure_schema(conn)
    out: dict = {"restored": 0, "errors": 0, "changed": False, "more": False, "why": {}}
    archived = {b for (b,) in conn.execute("SELECT bill FROM archive_bills")}
    if not archived:
        return out
    why: dict[str, str] = {}
    for (pcn,) in conn.execute("SELECT DISTINCT patient_control_number FROM claims WHERE patient_control_number IS NOT NULL"):
        b = _bill_of(pcn)
        if b in archived:
            why.setdefault(b, "new remittance")
    for table, col in (("ema_rows", "claim_id"), ("claims_837", "patient_control_number"), ("lines_837", "patient_control_number")):
        if _have(conn, table):
            for (x,) in conn.execute(f"SELECT DISTINCT {col} FROM {table} WHERE {col} IS NOT NULL"):
                b = _bill_of(x)
                if b in archived:
                    why.setdefault(b, "new EMA export row" if table == "ema_rows" else "new 837")
    for b in _plb_touched(conn):
        why.setdefault(b, "payer offset")
    for b in _ema_touched(conn):
        why.setdefault(b, "EMA lists it again")
    touched = set(why)
    for b in sorted(touched)[:limit]:
        try:
            restore(conn, b, sink=sink, reason="new data arrived", run_id=run_id, today=today, log=log)
            out["restored"] += 1
            out["changed"] = True
            out["why"][why[b]] = out["why"].get(why[b], 0) + 1
        except ArchiveError:
            out["errors"] += 1
    out["more"] = len(touched) > limit
    return out


# ----------------------------------------------------------------------------------------------- size and memory
def _table_sizes(conn: sqlite3.Connection, top: int, use_dbstat: bool = True) -> tuple[list[dict], bool]:
    try:
        if not use_dbstat:
            raise sqlite3.OperationalError("dbstat not used")
        rows = conn.execute("SELECT name, SUM(pgsize) FROM dbstat GROUP BY name").fetchall()
        idx = {n: t for n, t in conn.execute("SELECT name, tbl_name FROM sqlite_master WHERE type='index'")}
        by: Counter = Counter()
        for n, s in rows:
            by[idx.get(n, n)] += int(s or 0)
        return [{"table": t, "bytes": b} for t, b in by.most_common(top)], True
    except sqlite3.Error:
        pass
    est = []
    for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        try:
            n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            if not n:
                continue
            sample = conn.execute(f"SELECT * FROM {t} LIMIT 200").fetchall()
            per = sum(sum(len(v) if isinstance(v, (str, bytes)) else 8 for v in tuple(r)) + 6 for r in sample) / max(1, len(sample))
            est.append({"table": t, "bytes": int(per * n * 1.2), "rows": n})
        except sqlite3.Error:
            continue
    est.sort(key=lambda x: -x["bytes"])
    return est[:top], False


def measure(conn: sqlite3.Connection, *, peak: int | None = None, mode: str | None = None, budget: int | None = None,
            limit_mb: int | None = None, top: int = 8, env: dict | None = None, use_dbstat: bool = True) -> dict:
    """The hot store's size and the run's memory against its budget: {'hot_bytes', 'free_bytes', 'tables': [{table,
    bytes}], 'exact', 'peak_mb', 'budget_mb', 'limit_mb', 'share', 'level' ('ok' | 'warn' | 'over'), 'mode'}. Table sizes
    come from dbstat where SQLite has it (indexes counted with their table), else from row counts and a sample."""
    ps = conn.execute("PRAGMA page_size").fetchone()[0]
    pc = conn.execute("PRAGMA page_count").fetchone()[0]
    fl = conn.execute("PRAGMA freelist_count").fetchone()[0]
    tables, exact = _table_sizes(conn, top, use_dbstat)
    mode = mode or ("serverless" if serverless(env) else "server")
    bud = budget if budget is not None else budget_mb(mode, limit_mb=limit_mb, env=env)
    share = round(peak / bud, 3) if peak and bud else None
    level = "ok" if share is None or share < WARN_SHARE else ("over" if share >= 1 else "warn")
    return {"hot_bytes": ps * pc, "free_bytes": ps * fl, "tables": tables, "exact": exact, "peak_mb": peak, "budget_mb": bud,
            "limit_mb": limit_mb, "share": share, "level": level, "mode": mode}


def record_run(conn: sqlite3.Connection, *, run_id: str | None, measured: dict, result: dict | None = None,
               today: Any = None, seconds: float | None = None) -> dict:
    """Add this run to meta archive:history (the last 120 runs): its size, memory, budget and what it retired."""
    ensure_schema(conn)
    def mb(b: Any) -> float:
        return round((b or 0) / 1048576, 1)
    entry = {"at": _now(), "day": _today(today), "run_id": run_id, "mode": measured.get("mode"),
             "hot_mb": mb(measured.get("hot_bytes")), "free_mb": mb(measured.get("free_bytes")),
             "peak_mb": measured.get("peak_mb"), "budget_mb": measured.get("budget_mb"), "share": measured.get("share"),
             "level": measured.get("level"), "largest": [[t["table"], mb(t["bytes"])] for t in (measured.get("tables") or [])[:5]],
             "exact": measured.get("exact"), "seconds": seconds}
    if result:
        entry["retired"] = {k: result.get(k) for k in ("bills", "claims", "lines", "findings") if result.get(k)}
        if (result.get("ar") or {}).get("files"):
            entry["retired"]["ar_files"] = result["ar"]["files"]
        entry["horizon_months"] = (result.get("horizon") or {}).get("months")
        entry["cutoff"] = result.get("cutoff")
        if result.get("errors"):
            entry["errors"] = len(result["errors"])
    hist = _meta_json(conn, META_HISTORY, [])
    hist.append(entry)
    _meta_put(conn, META_HISTORY, hist[-HISTORY_KEPT:])
    conn.commit()
    return entry


def history(conn: sqlite3.Connection) -> list[dict]:
    return _meta_json(conn, META_HISTORY, [])


def budget_state(conn: sqlite3.Connection) -> dict | None:
    """The last recorded run's memory against its budget ({'peak_mb', 'budget_mb', 'share'}), or None: what run() takes as
    `budget_state`, so a run after one that came within 20% of its budget archives more on its own."""
    for h in reversed(history(conn)):
        if h.get("peak_mb") and h.get("budget_mb"):
            return {"peak_mb": h["peak_mb"], "budget_mb": h["budget_mb"], "share": h.get("share")}
    return None


def locked_years(conn: sqlite3.Connection) -> list[str]:
    """The service years the archive took lines of (their baselines and aggregate items are kept as measured whole)."""
    return sorted(_locked_years(conn))


def _mb_text(mb: Any) -> str:
    try:
        v = float(mb)
    except (TypeError, ValueError):
        return "?"
    return "under 1 MB" if v < 1 else f"{v:,.0f} MB"


def data_health(conn: sqlite3.Connection) -> list[dict]:
    """The Data tab's health items for size and memory (insights.data_health's shape: key, ok, title, detail, who), from
    what the last run recorded (the dashboard's copy has it)."""
    hist = history(conn)
    items: list[dict] = []
    last = next((h for h in reversed(hist) if h.get("peak_mb")), hist[-1] if hist else None)
    if last and last.get("budget_mb") and last.get("peak_mb"):
        share = last.get("share") or (last["peak_mb"] / last["budget_mb"])
        ok = share < WARN_SHARE
        largest = ", ".join(f"{t} {_mb_text(m)}" for t, m in (last.get("largest") or [])[:3])
        items.append({"key": "store_memory", "ok": ok, "share": share, "peak_mb": last["peak_mb"], "budget_mb": last["budget_mb"],
                      "title": (f"The daily run used {_mb_text(last['peak_mb'])} of its {_mb_text(last['budget_mb'])} memory budget"
                                f" ({share:.0%})"),
                      "detail": "" if ok else (f"The largest tables: {largest}. RemitWatch moves older settled claims to the "
                                               "archive by itself and takes more when a run comes this close to its limit."),
                      "who": "" if ok else ("Nobody needs to act today. If this stays above 80% for a week, tell whoever looks "
                                            "after the hosting: the horizon can be shortened (REMITWATCH_RETENTION_MONTHS).")})
    s = _meta_json(conn, META_SUMMARY, {})
    waiting = _meta_json(conn, "archive:waiting", {})
    if waiting.get("years"):
        items.append({"key": "archive_waiting", "ok": False, "years": waiting["years"],
                      "title": f"Older claims wait to move to the archive ({', '.join(waiting['years'])})",
                      "detail": "They move once the analysis has kept the payer rates of those years.",
                      "who": "Nobody: the next analysis does it."})
    if s.get("bills"):
        items.append({"key": "archive", "ok": True, "bills": s["bills"],
                      "title": f"{s['bills']:,} older settled claims are in the archive",
                      "detail": "Find a claim still opens every one of them.", "who": ""})
    return items


def panel(conn: sqlite3.Connection, state: Any = None) -> dict:
    """The Data tab's "Store size and memory" panel, from what the runs recorded in meta (never measured on the
    dashboard's copy): the hot store's size and largest tables, peak memory against the budget, the last runs, the
    archive's files and what the last run retired, the horizon in force. It writes nothing (the dashboard's copy may be
    read-only)."""
    hist = history(conn)
    last = hist[-1] if hist else {}
    s = _meta_json(conn, META_SUMMARY, {})
    hz = _meta_json(conn, META_HORIZON, {})
    where = _meta(conn, META_WHERE)
    if not where and state is not None:
        where = StateSink(state).describe()
    lines = []
    if last.get("peak_mb") and last.get("budget_mb"):
        lines.append(f"The last run used {_mb_text(last['peak_mb'])} of its {_mb_text(last['budget_mb'])} budget "
                     f"({(last.get('share') or 0):.0%}).")
    if last.get("hot_mb"):
        lines.append(f"The store the daily run loads is {_mb_text(last['hot_mb'])}.")
    if s.get("bills"):
        lines.append(f"{s['bills']:,} older settled claims ({s.get('lines', 0):,} service lines) are in the archive, "
                     f"in {s.get('files', 0):,} files; Find a claim still opens them.")
    if hz.get("months"):
        lines.append(f"Claims with service in the last {hz['months']} months stay in the store, and every claim with "
                     "open work, whatever its age.")
    retired = next((h.get("retired") for h in reversed(hist) if h.get("retired")), None)
    return {"hot": {"mb": last.get("hot_mb"), "free_mb": last.get("free_mb"), "largest": last.get("largest") or [],
                    "exact": last.get("exact")},
            "memory": {"peak_mb": last.get("peak_mb"), "budget_mb": last.get("budget_mb"), "share": last.get("share"),
                       "level": last.get("level"), "warn_share": WARN_SHARE, "mode": last.get("mode")},
            "history": [{k: h.get(k) for k in ("at", "day", "hot_mb", "peak_mb", "budget_mb", "share", "level", "retired")}
                        for h in hist[-30:]],
            "archive": s, "last_retired": retired, "horizon": hz, "where": where,
            "settings": {"default_months": DEFAULT_HORIZON_MONTHS, "floor_months": MIN_HORIZON_MONTHS, "ar_months": AR_MONTHS},
            "health": data_health(conn), "lines": lines}


def compact(conn: sqlite3.Connection, *, min_free_share: float = COMPACT_FREE_SHARE) -> dict:
    """Give back the pages the archive freed (VACUUM) when at least `min_free_share` of the store is free. A serverless
    host's store image is its memory and its upload, so it matters there; on a server SQLite reuses the free pages. It
    needs about the store's size again while it runs: call it where the run has that room (before detection)."""
    ps = conn.execute("PRAGMA page_size").fetchone()[0]
    pc = conn.execute("PRAGMA page_count").fetchone()[0]
    fl = conn.execute("PRAGMA freelist_count").fetchone()[0]
    if not pc or fl / pc < min_free_share:
        return {"skipped": True, "free_share": round(fl / pc, 3) if pc else 0, "bytes": ps * pc}
    t0 = time.time()
    conn.commit()
    conn.execute("VACUUM")
    pc2 = conn.execute("PRAGMA page_count").fetchone()[0]
    return {"before": ps * pc, "after": ps * pc2, "seconds": round(time.time() - t0, 2)}
