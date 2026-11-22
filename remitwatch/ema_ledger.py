"""
The posting ledger for EMA's Production report, and the lean summaries the store keeps from it.

The Production report has one row per posting: a charge, a payment or an adjustment. Two years are about 930,000 rows,
far too many for the store (which a serverless host holds in memory whole). So the postings live in a ledger of their
own, a separate SQLite file:

  server      REMITWATCH_EMA_LEDGER, else ema_ledger.sqlite beside the store (mode 0600, WAL)
  serverless  ema_ledger.sqlite.gz in the state folder; a run that applies a Production file unpacks it to a scratch
              copy in /tmp (LedgerHandle), applies the file, refreshes the store and uploads the ledger again

Each Production file is staged (read into a staging file beside the ledger), checked against holds H1-H6, and then
applied in one transaction under the box invariant: for every (service date, posted date) point the ledger holds the
rows of the newest applied file whose box contains that point. A file filtered by service date covers its service
dates up to its newest posting; a file filtered by posted date covers every service date over its posted dates. Rows
are never de-duplicated by content: two identical postings are two postings. Applying the same files in any order gives
the same ledger.

refresh() turns the ledger into small store tables:

  Part A (from the ledger)  ema_lines, ema_line_payments, ema_line_reasons, ema_checks, ema_months, ema_link_events
  Part B (store only)       the claim-link map (ema_link_map), which EMA payments an 835 carries (in_835), the ASC's
                            facility links, payer groups, and the lean keep rule that prunes the lines nothing needs

Part B runs alone after 835s or an A/R arrived; Part A runs when the ledger changed. Neither writes findings.

Names, dates of birth and record numbers never reach the ledger: the reader (remitwatch.ema_reports.iter_production)
turns them into keyed hashes first. No log line, exception or status reason here holds a cell value.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import secrets
import shutil
import sqlite3
import time
import urllib.parse
import zlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

from . import ema_reports as er
from . import runtime

# ---------------------------------------------------------------------------------------------------------- constants
LEDGER_SCHEMA = "1"
LEDGER_FILE = "ema_ledger.sqlite"
STATE_NAME = "ema_ledger.sqlite.gz"
ATTEMPTS_NAME = "ema_attempts.json"
RETAIN_DAYS = 1100                  # postings whose service date is this much older than the newest posting are dropped
SERVERLESS_RETAIN_DAYS = 730        # ... on a serverless host, whose temporary folder (512 MB) must hold the ledger twice over
RETAIN_HOLD_SHARE = 0.60            # retention deleting more than this share of the ledger at once waits for a person (a year of
                                    #   service arriving at once on a two-year ledger deletes about half)
WORK_STALE_SECONDS = 3600           # a refresh's work file this old was left by a run that was killed: it is deleted
# serverless: the temporary space a refresh needs, per ledger row (measured on the real files at two service years, with
# about 15% added): the ledger file, the work file at the end of Part A (the ledger is let go then), and at the end of Part B
LEDGER_BYTES_PER_ROW, WORK_A_BYTES_PER_ROW, WORK_B_BYTES_PER_ROW = 170, 200, 300
TMP_MARGIN = 32 * 1024 * 1024       #   ... plus this much
SCOPE_GAP_DAYS = 45                 # posted_lo - svc_lo above this: the file was filtered by posted date
APPLIED = ("current", "partly_replaced", "replaced", "superseded")      # in the ledger (its box counts for the invariant)
LIVE = ("current", "partly_replaced")                                   # still holds rows
MAX_ATTEMPTS = 3                    # serverless: a file whose read was started this often without finishing is held
XLSX_MAX_ROWS = 120_000             # H6: a larger Excel Production export is not read on a serverless host
STAGE_BATCH = 5000

# holds (spec 6.3); H1 and H7 are judged on what the reader counted (ema_reports.production_format_holds)
H1_NO_DATE_SHARE = er.H1_NO_DATE_SHARE
H1_BREAK_SHARE = er.H1_BREAK_SHARE
H2_CHARGE_SHARE = 0.97
H2_MIN_LINES = 20                   # a file of fewer claim lines says nothing about its scope
H3_MIN_LINKS = 200
H3_LINK_SHARE = 0.90
H3_MIN_PAYERS = 10
H3_PAYER_SHARE = 0.50
H4_MIN_ROWS = 20
H5_MIN_ROWS = 2000
H5_MIN_PAYERS = 3

# the claim-link map (spec 7.2 as amended by E7)
VOTE_SHARE = (2, 3)                 # a link takes a bill holding at least 2/3 of its votes
CHARGE_TOLERANCE = 0.02             # patient_day: the link's charge equals the 835 line's billed charge within 2%

META_SEQ = "ema:ledger_seq"
META_REV = "ema:ledger_rev"
META_MAP_DIRTY = er.META_MAP_DIRTY
META_KEEP_DIRTY = "ema:keep_dirty"
META_MAP_COVERAGE = "ema:map_coverage"
META_RETAIN_HELD = "ema:retain_held"        # retention that waits for a person (the Data tab shows it)

# the work database refresh() attaches to the store connection (and the ledger's alias there)
_W = "emaw"
_L = "emal"

LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS ledger_meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS ledger_files (src INTEGER PRIMARY KEY, file_id TEXT UNIQUE, scope TEXT,
    svc_lo TEXT, svc_hi TEXT, posted_lo TEXT, posted_hi TEXT, as_of TEXT, created_at TEXT, received_at TEXT,
    rows INTEGER, live_rows INTEGER, status TEXT, source_name TEXT, via TEXT, rows_read INTEGER, applied_at TEXT,
    replaced_by TEXT, links INTEGER);
CREATE TABLE IF NOT EXISTS names (id INTEGER PRIMARY KEY, name TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS postings (src INTEGER NOT NULL, posted TEXT NOT NULL, svc TEXT NOT NULL, cpt TEXT, units REAL,
    link INTEGER, check_no TEXT, grp TEXT, code TEXT, kind INTEGER, status INTEGER, action INTEGER,
    payer INTEGER, plan INTEGER, primary_payer INTEGER, secondary_payer INTEGER, pccn TEXT,
    person INTEGER, name_key INTEGER, charges INTEGER, ins_charges INTEGER, ins_paid INTEGER, pat_paid INTEGER,
    contractual INTEGER, noncontractual INTEGER, pat_adj INTEGER, allowed INTEGER, flags INTEGER);
CREATE INDEX IF NOT EXISTS ix_p_svc ON postings(svc);
CREATE INDEX IF NOT EXISTS ix_p_posted ON postings(posted);
CREATE INDEX IF NOT EXISTS ix_p_link ON postings(link) WHERE link IS NOT NULL;
"""
# the posting columns after src, in Posting's order (names as integer ids: kind, status, action and the payers)
POSTING_COLUMNS = ("posted", "svc", "cpt", "units", "link", "check_no", "grp", "code", "kind", "status", "action", "payer",
                   "plan", "primary_payer", "secondary_payer", "pccn", "person", "name_key", "charges", "ins_charges",
                   "ins_paid", "pat_paid", "contractual", "noncontractual", "pat_adj", "allowed", "flags")
_NAME_FIELDS = (8, 9, 10, 11, 12, 13, 14)          # Posting fields stored as name ids
STAGE_DDL = ("CREATE TABLE IF NOT EXISTS stage.rows (" + ", ".join(
    f"{c} {'TEXT' if c in ('posted', 'svc', 'cpt', 'check_no', 'grp', 'code', 'pccn') else 'REAL' if c == 'units' else 'INTEGER'}"
    for c in POSTING_COLUMNS) + ");\nCREATE TABLE IF NOT EXISTS stage.new_names (id INTEGER PRIMARY KEY, name TEXT);")


# ------------------------------------------------------------------------------------------------------------ helpers
def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _days(a: str | None, b: str | None) -> int | None:
    try:
        return (date.fromisoformat(str(b)[:10]) - date.fromisoformat(str(a)[:10])).days
    except (TypeError, ValueError):
        return None


def _day(d: str | None) -> str:
    try:
        x = date.fromisoformat(str(d)[:10])
        return f"{x:%b} {x.day}, {x.year}"
    except (TypeError, ValueError):
        return str(d or "")


def _remove(*paths: str) -> None:
    for p in paths:
        for q in (p, p + "-journal", p + "-wal", p + "-shm"):
            try:
                os.remove(q)
            except OSError:
                pass


WORK_PREFIX = "ema_refresh-"


def clean_work_files(folder: str, older_than: float = WORK_STALE_SECONDS) -> int:
    """Delete the work files (and their journals) refreshes left in this folder when they were killed (an out-of-memory
    stop, a container stopped mid-refresh): a live refresh's file is younger than `older_than` seconds. Returns how many
    went."""
    n = 0
    try:
        names = os.listdir(folder)
    except OSError:
        return 0
    now = time.time()
    for f in names:
        if not f.startswith(WORK_PREFIX):
            continue
        p = os.path.join(folder, f)
        try:
            if now - os.path.getmtime(p) < older_than:
                continue
            os.remove(p)
            n += 1
        except OSError:
            pass
    return n


def retain_days() -> int:
    """How many days of service the ledger keeps before its newest posting: REMITWATCH_EMA_RETAIN_DAYS when set, else
    RETAIN_DAYS on a server and SERVERLESS_RETAIN_DAYS on a serverless host (its temporary folder holds the ledger and
    the refresh's work file; at three service years they do not fit)."""
    raw = os.environ.get("REMITWATCH_EMA_RETAIN_DAYS", "").strip()
    if raw:
        try:
            return max(366, int(raw))
        except ValueError:
            pass
    return SERVERLESS_RETAIN_DAYS if runtime.serverless() else RETAIN_DAYS


def _meta_get(conn: sqlite3.Connection, key: str) -> str | None:
    try:
        r = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    except sqlite3.OperationalError:
        return None
    return r[0] if r else None


def _meta_set(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, None if value is None else str(value)))


def _columns(conn: sqlite3.Connection, schema: str, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA {schema}.table_info({table})")]


def _table_exists(conn: sqlite3.Connection, schema: str, table: str) -> bool:
    return conn.execute(f"SELECT 1 FROM {schema}.sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _attached(conn: sqlite3.Connection, name: str) -> bool:
    return any(r[1] == name for r in conn.execute("PRAGMA database_list"))


def _ignore_sql() -> str:
    from .detect import IGNORE_CARCS        # noqa: PLC0415  (detect imports the store and the rules; loaded only when needed)
    return ",".join(f"'{c}'" for c in sorted(IGNORE_CARCS))


def check_key(check: Any) -> str | None:
    """A check number as the 835 match compares it: stripped text, and an all-digit number without its leading zeros (so
    "00123" equals the trace "123"). Anything with a letter is compared exactly."""
    if check is None:
        return None
    s = str(check).strip()
    if not s:
        return None
    if s.isascii() and s.isdigit():
        return s.lstrip("0") or "0"
    return s


_CLASSES: list[tuple[str, frozenset]] | None = None
ROUTINE_CLASSES = ("none", "routine", "patient", "ema_kind")      # not a payer's denial


def _reason_sets() -> list[tuple[str, frozenset]]:
    global _CLASSES
    if _CLASSES is None:
        from . import detect as d           # noqa: PLC0415
        _CLASSES = [("routine", frozenset(d.IGNORE_CARCS)), ("timely", frozenset(d.TIMELY_CARCS)),
                    ("bundling", frozenset(d.BUNDLING_CARCS)), ("level", frozenset(d.LEVEL_CARCS)),
                    ("coding", frozenset(d.CODING_CARCS)), ("cob", frozenset(d.PRIMARY_CARCS)),
                    ("information", frozenset(d.INFO_CARCS)), ("auth", frozenset(d.AUTH_CARCS)),
                    ("medical_necessity", frozenset(d.MEDNEC_CARCS)), ("frequency", frozenset(d.FREQ_CARCS)),
                    ("noncovered", frozenset(d.NONCOV_CARCS)), ("provider", frozenset(d.PROVIDER_CARCS)),
                    ("duplicate", frozenset(d.DUP_CARCS))]
    return _CLASSES


def reason_class(grp: str | None, code: str | None, kind: str | None = None) -> str:
    """The class of an adjustment's reason. A CARC with its group: 'patient' for group PR, 'routine' for the codes
    detect.py ignores (CO-45 and the like), else the class of detect.py's reason sets ('bundling', 'level', 'coding',
    'cob', 'information', 'auth', 'medical_necessity', 'frequency', 'noncovered', 'provider', 'duplicate', 'timely') or
    'other'. EMA's own adjustment kinds (Small Balance, Pro Courtesy ...) are 'ema_kind'; no reason at all is 'none'.
    In ema_line_reasons an EMA kind is stored with group '' and its name as the code."""
    g = (grp or "").strip().upper()
    c = (code or "").strip().upper()
    if not g:
        return "ema_kind" if (kind or code) else "none"
    if g == "PR":
        return "patient"
    for name, codes in _reason_sets():
        if c in codes:
            return name
    return "other"


def _register(conn: sqlite3.Connection, salt: bytes | None = None) -> None:
    """The SQL functions the ledger's SQL uses: ema_reports' (rw_name_key, rw_bill, rw_payer_group ...) plus rw_ckey,
    rw_reason_class and rw_is_facility."""
    er.register_functions(conn, salt)
    conn.create_function("rw_ckey", 1, check_key, deterministic=True)
    conn.create_function("rw_reason_class", 2, lambda g, c: reason_class(g, c), deterministic=True)
    conn.create_function("rw_reason_class", 3, reason_class, deterministic=True)
    conn.create_function("rw_is_facility", 1, lambda c: 1 if er.is_facility_claim(c) else 0, deterministic=True)


# -------------------------------------------------------------------------------------------------------- boxes, files
def scope_of(svc_lo: str, posted_lo: str) -> str:
    """'posted' when the file's first posting is more than 45 days after its first service date (a file filtered by
    posted date holds postings for services months or years older), else 'service'. A file filtered by service date
    starts posting the day its services start, so the test cannot tie on a real export."""
    gap = _days(svc_lo, posted_lo)
    return "posted" if gap is not None and gap > SCOPE_GAP_DAYS else "service"


@dataclass(frozen=True)
class Box:
    """The (service date, posted date) region a Production file covers. Service scope: its service dates, by every
    posting up to its as_of. Posted scope: every service date, by its posted dates."""
    file_id: str
    scope: str
    svc_lo: str
    svc_hi: str
    posted_lo: str
    posted_hi: str
    as_of: str
    order: tuple
    src: int | None = None
    status: str | None = None

    def sql(self, alias: str = "") -> tuple[str, list]:
        """The box as an SQL condition on svc and posted, with its arguments."""
        a = alias + "." if alias else ""
        if self.scope == "posted":
            return f"({a}posted BETWEEN ? AND ?)", [self.posted_lo, self.posted_hi]
        return f"({a}svc BETWEEN ? AND ? AND {a}posted <= ?)", [self.svc_lo, self.svc_hi, self.as_of]

    def _ranges(self) -> tuple[tuple, tuple]:
        if self.scope == "posted":
            return (None, None), (self.posted_lo, self.posted_hi)
        return (self.svc_lo, self.svc_hi), (None, self.as_of)

    def meets(self, other: "Box") -> bool:
        """Whether two boxes share a (service date, posted date) point."""
        def meet(x: tuple, y: tuple) -> bool:
            lo = max((v for v in (x[0], y[0]) if v), default=None)
            hi = min((v for v in (x[1], y[1]) if v), default=None)
            return lo is None or hi is None or lo <= hi
        (sa, pa), (sb, pb) = self._ranges(), other._ranges()
        return meet(sa, sb) and meet(pa, pb)

    def covers(self, other: "Box") -> bool:
        """Whether this box holds every point of the other."""
        if self.scope == "posted":
            return other.scope == "posted" and self.posted_lo <= other.posted_lo and self.posted_hi >= other.posted_hi
        return (other.scope == "service" and self.svc_lo <= other.svc_lo and self.svc_hi >= other.svc_hi
                and self.as_of >= other.as_of)


@dataclass
class FileMeta:
    """What the ledger records about a Production file when it is applied."""
    file_id: str
    source_name: str
    via: str
    scope: str | None
    svc_lo: str | None
    svc_hi: str | None
    posted_lo: str | None
    posted_hi: str | None
    as_of: str | None
    created_at: str | None
    received_at: str
    rows_read: int = 0
    rows_kept: int = 0
    links: int | None = None
    content_id: str | None = None       # a hash of its postings in order: the same rows saved again are one batch
    source_kind: str | None = None      # 'file', 'upload' or 'api' (amendment 10)

    @property
    def order(self) -> tuple:
        """Newer files win: (as_of, the workbook's creation time, when it arrived, its id)."""
        return (self.as_of or "", self.created_at or "", self.received_at or "", self.file_id)

    def box(self) -> Box:
        return Box(self.file_id, self.scope or "service", self.svc_lo or "", self.svc_hi or "", self.posted_lo or "",
                   self.posted_hi or "", self.as_of or "", self.order)


def ledger_path(db_path: str) -> str:
    """Where the server keeps the ledger: REMITWATCH_EMA_LEDGER, else ema_ledger.sqlite beside the store."""
    env = os.environ.get("REMITWATCH_EMA_LEDGER", "").strip()
    if env:
        return env
    if not db_path or db_path == ":memory:":
        return os.path.join(runtime.tmp_dir(), LEDGER_FILE)
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), LEDGER_FILE)


# ------------------------------------------------------------------------------------------------------------ the ledger
class Ledger:
    """The posting ledger: one SQLite file of Production postings (keys only), the files applied to it and their boxes.

    Writes happen in explicit transactions (the connection runs in autocommit mode otherwise). A server ledger uses WAL
    and mode 0600; a serverless scratch copy runs with journaling and syncing off, and the copy in the state folder is
    the durable one."""

    def __init__(self, path: str, conn: sqlite3.Connection, *, readonly: bool, scratch: bool):
        self.path = path
        self.conn = conn
        self.readonly = readonly
        self.scratch = scratch
        self.rev: str | None = None         # the state folder's revision this copy came from (serverless)
        self._stage_stats: dict | None = None

    # ------------------------------------------------------------------ opening
    @classmethod
    def open(cls, path: str, *, readonly: bool = False, scratch: bool = False) -> "Ledger":
        """Open (or create) the ledger at `path`. `readonly` never writes; `scratch` turns journaling and syncing off (a
        serverless copy that is thrown away when a run fails)."""
        if readonly:
            if not os.path.exists(path):
                raise FileNotFoundError("The posting ledger has not been created yet.")
            uri = "file:" + urllib.parse.quote(os.path.abspath(path)) + "?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=60, isolation_level=None, check_same_thread=False)
            lg = cls(path, conn, readonly=True, scratch=scratch)
            if lg._meta("schema") not in (None, LEDGER_SCHEMA):
                conn.close()
                raise RuntimeError("The posting ledger was written by another version of RemitWatch.")
            return lg
        d = os.path.dirname(os.path.abspath(path))
        os.makedirs(d, exist_ok=True)
        if not os.path.exists(path):
            fd = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
            os.close(fd)
        else:
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        conn = sqlite3.connect(path, timeout=60, isolation_level=None, check_same_thread=False)
        if scratch:
            conn.execute("PRAGMA journal_mode=OFF")
            conn.execute("PRAGMA synchronous=OFF")
        else:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(LEDGER_DDL)
        lg = cls(path, conn, readonly=False, scratch=scratch)
        schema = lg._meta("schema")
        if schema is None:
            lg._meta_put("schema", LEDGER_SCHEMA)
            lg._meta_put("seq", "0")
        elif schema != LEDGER_SCHEMA:
            conn.close()
            raise RuntimeError("The posting ledger was written by another version of RemitWatch.")
        have = {r[1] for r in conn.execute("PRAGMA table_info(ledger_files)")}
        for col in ("content_id", "source_kind"):           # added after the first ledgers were written
            if col not in have:
                conn.execute(f"ALTER TABLE ledger_files ADD COLUMN {col} TEXT")
        lg._remove_stage_file()             # a staging file left by a run that was killed
        clean_work_files(d)                 # and the work files of refreshes that were killed
        return lg

    def __enter__(self) -> "Ledger":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        try:
            self.drop_stage()
        except sqlite3.Error:
            pass
        try:
            self.conn.close()
        except sqlite3.Error:
            pass

    # ------------------------------------------------------------------ meta
    def _meta(self, key: str) -> str | None:
        r = self.conn.execute("SELECT value FROM ledger_meta WHERE key=?", (key,)).fetchone()
        return r[0] if r else None

    def _meta_put(self, key: str, value: Any) -> None:
        self.conn.execute("INSERT OR REPLACE INTO ledger_meta (key, value) VALUES (?, ?)", (key, str(value)))

    def seq(self) -> int:
        """A number that changes with every change to the postings (applies, retention, a salt reset)."""
        try:
            return int(self._meta("seq") or 0)
        except ValueError:
            return 0

    def _bump(self) -> None:
        self._meta_put("seq", self.seq() + 1)

    def salt_fp(self) -> str | None:
        return self._meta("salt_fp")

    def bind_salt(self, salt_fp: str) -> list[str]:
        """Tie the ledger to the store's key salt (by its fingerprint). On an empty ledger the fingerprint is recorded. When
        it differs, the postings were keyed with another salt and cannot be joined: the ledger is emptied and the ids of
        the files it held are returned, to be read again."""
        cur = self.salt_fp()
        if cur == salt_fp:
            return []
        has_rows = self.conn.execute("SELECT 1 FROM ledger_files LIMIT 1").fetchone() is not None
        ids: list[str] = []
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            if cur is not None or has_rows:
                ids = [r[0] for r in self.conn.execute("SELECT file_id FROM ledger_files ORDER BY src")]
                self.conn.execute("DELETE FROM postings")
                self.conn.execute("DELETE FROM ledger_files")
                self._bump()
            self._meta_put("salt_fp", salt_fp)
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        return ids

    # ------------------------------------------------------------------ files and boxes
    def files(self) -> list[dict]:
        cur = self.conn.execute("SELECT * FROM ledger_files ORDER BY src")
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]

    def file(self, file_id: str) -> dict | None:
        cur = self.conn.execute("SELECT * FROM ledger_files WHERE file_id=?", (file_id,))
        r = cur.fetchone()
        return dict(zip([d[0] for d in cur.description], r)) if r else None

    def duplicate_of(self, content_id: str | None, file_id: str | None) -> dict | None:
        """An applied file whose postings, in order, are exactly these (the same export saved again: another save time,
        other zip times, other line endings), or None."""
        if not content_id:
            return None
        cur = self.conn.execute("SELECT file_id, applied_at, status FROM ledger_files WHERE content_id=? AND file_id IS NOT ? "
                                f"AND status IN ({','.join('?' * len(APPLIED))}) ORDER BY src LIMIT 1",
                                (content_id, file_id, *APPLIED))
        r = cur.fetchone()
        return {"file_id": r[0], "applied_at": r[1], "status": r[2]} if r else None

    def boxes(self, statuses: tuple = LIVE) -> list[Box]:
        """The boxes of the applied files with these statuses (by default those that still hold rows)."""
        q = (f"SELECT file_id, scope, svc_lo, svc_hi, posted_lo, posted_hi, as_of, created_at, received_at, src, status "
             f"FROM ledger_files WHERE status IN ({','.join('?' * len(statuses))}) ORDER BY src")
        out = []
        for fid, scope, slo, shi, plo, phi, as_of, created, received, src, st in self.conn.execute(q, statuses):
            out.append(Box(fid, scope or "service", slo or "", shi or "", plo or "", phi or "", as_of or "",
                           (as_of or "", created or "", received or "", fid), src, st))
        return out

    def stats(self) -> dict:
        """Counts for the CLI and the Data tab."""
        n, lo, hi = self.conn.execute("SELECT COUNT(*), MIN(svc), MAX(posted) FROM postings").fetchone()
        try:
            size = os.path.getsize(self.path)
        except OSError:
            size = None
        return {"rows": n, "svc_lo": lo, "posted_hi": hi, "files": len(self.files()), "seq": self.seq(), "bytes": size}

    # ------------------------------------------------------------------ staging
    def _stage_path(self) -> str:
        if self.path == ":memory:":
            return os.path.join(runtime.tmp_dir(), f"ema_ledger-stage-{os.getpid()}.sqlite")
        return self.path + "-stage"

    def _remove_stage_file(self) -> None:
        _remove(self._stage_path())

    def _stage_attached(self) -> bool:
        return _attached(self.conn, "stage")

    def stage(self, rows: Iterable[Any], *, deadline: float | None = None) -> dict:
        """Read a file's postings (Posting tuples) into a staging file beside the ledger; the ledger itself is not touched
        until commit(). Returns what the holds need: rows, links, insurance payers, claim lines and the share of them that
        carry their charge, identity breaks, and the file's dates. Raises ema_reports.ReadTimeout past `deadline`."""
        if self.readonly:
            raise RuntimeError("A read-only ledger cannot stage a file.")
        self.drop_stage()
        c = self.conn
        sp = self._stage_path()
        _remove(sp)
        c.execute("ATTACH DATABASE ? AS stage", (sp,))
        c.execute("PRAGMA stage.journal_mode=OFF")
        c.execute("PRAGMA stage.synchronous=OFF")
        c.executescript(STAGE_DDL)
        names = {n: i for i, n in c.execute("SELECT id, name FROM main.names")}
        nxt = [max(names.values(), default=0) + 1]
        new: list[tuple[int, str]] = []

        def nid(v: Any) -> int | None:
            if v is None:
                return None
            i = names.get(v)
            if i is None:
                i = names[v] = nxt[0]
                nxt[0] += 1
                new.append((i, v))
            return i

        sql = f"INSERT INTO stage.rows ({','.join(POSTING_COLUMNS)}) VALUES ({','.join('?' * len(POSTING_COLUMNS))})"
        batch: list[tuple] = []
        n = 0
        c.execute("BEGIN")
        try:
            for p in rows:
                r = list(p)
                for k in _NAME_FIELDS:
                    r[k] = nid(r[k])
                batch.append(tuple(r))
                n += 1
                if len(batch) >= STAGE_BATCH:
                    c.executemany(sql, batch)
                    batch.clear()
                    if deadline is not None and n % er.DEADLINE_EVERY < STAGE_BATCH and time.time() > deadline:
                        raise er.ReadTimeout(f"Reading stopped at the time limit after {n:,} rows.")
            if batch:
                c.executemany(sql, batch)
            if new:
                c.executemany("INSERT INTO stage.new_names (id, name) VALUES (?, ?)", new)
            c.execute("COMMIT")
        except BaseException:
            try:
                c.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            self.drop_stage()
            raise
        self._stage_stats = self._staged_stats()
        return dict(self._stage_stats)

    def _staged_stats(self) -> dict:
        c = self.conn
        rows, links, breaks, slo, shi, plo, phi = c.execute(
            "SELECT COUNT(*), COUNT(DISTINCT link), SUM((flags & 8) <> 0), MIN(svc), MAX(svc), MIN(posted), MAX(posted) "
            "FROM stage.rows").fetchone()
        payers = c.execute("SELECT COUNT(DISTINCT payer) FROM stage.rows WHERE (flags & 16) = 0 AND payer IS NOT NULL").fetchone()[0]
        lines, charged = c.execute("SELECT COUNT(*), SUM(ch) FROM (SELECT MAX(COALESCE(charges, 0) > 0) AS ch FROM stage.rows "
                                   "WHERE link IS NOT NULL GROUP BY link, svc, cpt)").fetchone()
        return {"rows": rows, "links": links, "payers": payers, "lines": lines, "charge_lines": charged or 0,
                "charge_line_share": round((charged or 0) / lines, 4) if lines else None, "identity_breaks": breaks or 0,
                "svc_lo": slo, "svc_hi": shi, "posted_lo": plo, "posted_hi": phi}

    def drop_stage(self) -> None:
        """Forget what is staged and delete the staging file."""
        try:
            if self.conn.in_transaction:
                self.conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        if self._stage_attached():
            try:
                self.conn.execute("DETACH DATABASE stage")
            except sqlite3.Error:
                pass
        self._stage_stats = None
        if not self.readonly:
            self._remove_stage_file()

    # ------------------------------------------------------------------ holds
    def tmp_need(self, staged_rows: int = 0, *, newest: str | None = None, replaced: int = 0) -> tuple[int, int]:
        """(bytes the ledger and a refresh's work file need in the ledger's folder once a file of `staged_rows` postings
        whose newest posting is `newest` is applied, bytes there are for them: what is free plus what the ledger and the
        staging file hold now). The ledger after it holds today's rows less what retention takes (as of `newest`) and
        what the file replaces (`replaced`), plus its own; its file never shrinks. A refresh needs the ledger and Part A's
        work file at once, then (the ledger let go) Part B's. A serverless host's temporary folder is 512 MB, so a file
        that would not leave room for its own summaries is held there (H8) instead of failing in the refresh, attempt
        after attempt."""
        c = self.conn
        rows_now = c.execute("SELECT COUNT(*) FROM postings").fetchone()[0] or 0
        cut = self._retention_cut(newest=newest)
        gone = cut[1] if cut and not cut[2] else 0
        rows_after = max(0, rows_now - gone - max(0, replaced)) + max(0, staged_rows)
        sizes = []
        for p in (self.path, self._stage_path()):
            try:
                sizes.append(os.path.getsize(p))
            except OSError:
                sizes.append(0)
        ledger_after = max(sizes[0], rows_after * LEDGER_BYTES_PER_ROW)
        need = max(ledger_after + rows_after * WORK_A_BYTES_PER_ROW, rows_after * WORK_B_BYTES_PER_ROW) + TMP_MARGIN
        folder = os.path.dirname(os.path.abspath(self.path)) if self.path != ":memory:" else runtime.tmp_dir()
        try:
            free = shutil.disk_usage(folder).free
        except OSError:
            return need, need
        return need, free + sum(sizes)

    def _older_newer(self, mine: Box) -> tuple[list[Box], list[Box]]:
        boxes = [b for b in self.boxes(APPLIED) if b.file_id != mine.file_id and b.meets(mine)]
        return [b for b in boxes if b.order < mine.order], [b for b in boxes if b.order > mine.order]

    def holds(self, meta: FileMeta, stats: dict, *, serverless: bool, fmt: str = "csv",
              dimension: int | None = None) -> list[tuple[str, str]]:
        """The holds (spec 6.3) a staged file meets, as (code, message). A held file waits for a person to press Apply or
        Discard; Apply skips holds, never refusals. `stats` is what stage() returned plus the reader's counts (rows_read,
        no_date, misaligned, cut_short, damaged_checks, check_rows): H1 (format, cut short) and H7 (check numbers a
        spreadsheet changed) are judged on those (ema_reports.production_format_holds)."""
        out: list[tuple[str, str]] = []
        if serverless and fmt == "xlsx" and (dimension or 0) > XLSX_MAX_ROWS:
            out.append(("H6", f"Save it as CSV: an Excel Production export of more than {XLSX_MAX_ROWS:,} rows is not read on "
                              "this site."))
        out += er.production_format_holds(stats)
        share = stats.get("charge_line_share")
        if (meta.scope == "service" and share is not None and int(stats.get("lines") or 0) >= H2_MIN_LINES
                and share < H2_CHARGE_SHARE):
            out.append(("H2", f"Only {int(share * 100)}% of its claim lines carry their charge, so it looks filtered by posted "
                              "date while its dates read as a service-date export. Export it again with one date filter."))
        if not self._stage_attached():
            return out + (self._tmp_hold(meta, stats, 0) if serverless else [])
        mine = meta.box()
        older, newer = self._older_newer(mine)
        live_older = [b for b in older if b.status in LIVE]
        c = self.conn
        replaced = 0
        if live_older:
            pred, args = mine.sql("p")
            srcs = [b.src for b in live_older]
            base = f"FROM main.postings p WHERE p.src IN ({','.join('?' * len(srcs))}) AND {pred}"
            bargs = srcs + args
            replaced = c.execute(f"SELECT COUNT(*) {base}", bargs).fetchone()[0] or 0
            links_old = c.execute(f"SELECT COUNT(DISTINCT p.link) {base} AND p.link IS NOT NULL", bargs).fetchone()[0]
            when = _day(max(b.as_of for b in live_older))
            if links_old >= H3_MIN_LINKS:
                kept = c.execute(f"SELECT COUNT(*) FROM (SELECT DISTINCT p.link AS link {base} AND p.link IS NOT NULL) o "
                                 "WHERE o.link IN (SELECT link FROM stage.rows WHERE link IS NOT NULL)", bargs).fetchone()[0]
                if kept < H3_LINK_SHARE * links_old:
                    out.append(("H3", f"It has {100 * kept // links_old}% of the claims the export of {when} had for these "
                                      "dates: was it filtered by payer, provider or location?"))
            payers_old = c.execute(f"SELECT COUNT(DISTINCT p.payer) {base} AND (p.flags & 16) = 0 AND p.payer IS NOT NULL",
                                   bargs).fetchone()[0]
            if payers_old >= H3_MIN_PAYERS:
                kept = c.execute(f"SELECT COUNT(*) FROM (SELECT DISTINCT p.payer AS payer {base} AND (p.flags & 16) = 0 "
                                 "AND p.payer IS NOT NULL) o WHERE o.payer IN (SELECT payer FROM stage.rows)", bargs).fetchone()[0]
                if kept < H3_PAYER_SHARE * payers_old and not any(code == "H3" for code, _m in out):
                    out.append(("H3", f"It has {100 * kept // payers_old}% of the insurers the export of {when} had for these "
                                      "dates: was it filtered by payer, provider or location?"))
            if meta.scope == "posted":
                must = c.execute(f"SELECT COUNT(*) {base} AND p.svc < ?", bargs + [meta.svc_lo]).fetchone()[0]
            else:
                must = c.execute(f"SELECT COUNT(*) {base} AND p.posted < ?", bargs + [meta.posted_lo]).fetchone()[0]
            if must >= H4_MIN_ROWS:
                out.append(("H4", f"It seems filtered by both dates: it lacks {must:,} postings that the export of {when} holds "
                                  "for its dates. Export it with one date filter only."))
        if not older and not newer:
            rows = int(stats.get("rows") or 0)
            payers = int(stats.get("payers") or 0)
            if rows >= H5_MIN_ROWS and payers < H5_MIN_PAYERS:
                out.append(("H5", f"It names {payers} insurer{'s' if payers != 1 else ''} on {rows:,} rows and it is the first "
                                  "Production file for its dates: was it filtered by payer? It must hold every payer."))
        if serverless:
            out += self._tmp_hold(meta, stats, replaced)
        return out

    def _tmp_hold(self, meta: FileMeta, stats: dict, replaced: int) -> list[tuple[str, str]]:
        need, have = self.tmp_need(int(stats.get("rows") or 0), newest=meta.posted_hi, replaced=replaced)
        if need <= have:
            return []
        return [("H8", f"Too large for this site's temporary space: the summaries after it need about {need >> 20:,} MB and "
                       f"{have >> 20:,} MB is free. The server reads it; this site keeps {retain_days():,} days of postings "
                       "(REMITWATCH_EMA_RETAIN_DAYS).")]

    # ------------------------------------------------------------------ applying
    def commit(self, meta: FileMeta) -> dict:
        """Apply the staged file in one transaction (spec 6.2): delete the rows of older files inside its box, insert its
        rows that fall inside no newer file's box, update every file's live rows and status, and add 1 to seq. Returns
        {'status': 'current'|'superseded', 'rows_applied', 'live_rows', 'replaced': [(file_id, status)], 'box', 'src'}."""
        if not self._stage_attached():
            raise RuntimeError("Nothing is staged.")
        c = self.conn
        mine = meta.box()
        older, newer = self._older_newer(mine)
        rows = c.execute("SELECT COUNT(*) FROM stage.rows").fetchone()[0]
        links = meta.links if meta.links is not None else (self._stage_stats or {}).get("links")
        now = _now()
        replaced: list[tuple[str, str]] = []
        c.execute("BEGIN IMMEDIATE")
        try:
            c.execute("DELETE FROM ledger_files WHERE file_id=?", (meta.file_id,))
            src = c.execute(
                "INSERT INTO ledger_files (file_id, scope, svc_lo, svc_hi, posted_lo, posted_hi, as_of, created_at, received_at, "
                "rows, live_rows, status, source_name, via, rows_read, applied_at, links, content_id, source_kind) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (meta.file_id, mine.scope, meta.svc_lo, meta.svc_hi, meta.posted_lo, meta.posted_hi, meta.as_of, meta.created_at,
                 meta.received_at, rows, 0, "current", meta.source_name, meta.via, meta.rows_read, now, links, meta.content_id,
                 meta.source_kind)).lastrowid
            c.execute("INSERT OR IGNORE INTO main.names (id, name) SELECT id, name FROM stage.new_names")
            pred, args = mine.sql()
            for b in older:
                if b.status not in LIVE:
                    continue
                gone = c.execute(f"DELETE FROM main.postings WHERE src = ? AND {pred}", [b.src] + args).rowcount
                if gone:
                    live = c.execute("SELECT live_rows FROM ledger_files WHERE src=?", (b.src,)).fetchone()[0] or 0
                    live = max(0, live - gone)
                    st = "replaced" if live == 0 else "partly_replaced"
                    c.execute("UPDATE ledger_files SET live_rows=?, status=?, replaced_by=? WHERE src=?", (live, st, meta.file_id, b.src))
                    replaced.append((b.file_id, st))
            conds, cargs = [], []
            for b in newer:
                p2, a2 = b.sql()
                conds.append(f"NOT {p2}")
                cargs += a2
            where = " AND ".join(conds) if conds else "1"
            cols = ",".join(POSTING_COLUMNS)
            live_f = c.execute(f"INSERT INTO main.postings (src,{cols}) SELECT ?,{cols} FROM stage.rows WHERE {where} ORDER BY rowid",
                               [src] + cargs).rowcount
            status = "current" if live_f or not rows else "superseded"
            by = max(newer, key=lambda b: b.order).file_id if (status == "superseded" and newer) else None
            c.execute("UPDATE ledger_files SET live_rows=?, status=?, replaced_by=? WHERE src=?", (live_f, status, by, src))
            self._bump()
            c.execute("COMMIT")
        except BaseException:
            try:
                c.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        box = Box(mine.file_id, mine.scope, mine.svc_lo, mine.svc_hi, mine.posted_lo, mine.posted_hi, mine.as_of, mine.order,
                  src, status)
        return {"status": status, "rows_applied": live_f, "live_rows": live_f, "rows": rows, "replaced": replaced, "box": box,
                "src": src, "replaced_by": by}

    # ------------------------------------------------------------------ retention and copies
    def retain(self, days: int | None = None, *, force: bool = False, today: str | None = None,
               newest: str | None = None) -> int:
        """Delete postings whose service date is more than `days` (retain_days()) before the ledger's newest posting (or
        `newest`, the newest posting of a file about to be applied, when later), or before today when that is later than
        today (a damaged date must never wipe the ledger). When that would delete more than RETAIN_HOLD_SHARE of the ledger
        at once, nothing is deleted: the cutoff and the count are kept (retain_held) until a person runs it with force
        (retention normally takes a few days of service a week). Returns the rows deleted (and adds 1 to seq when there
        were any)."""
        if self.readonly:
            return 0
        days = retain_days() if days is None else int(days)
        c = self.conn
        cut = self._retention_cut(days, today=today, newest=newest)
        if cut is None:
            return 0
        cutoff, gone, would_wait = cut
        if not gone:
            if self._meta("retain_held"):
                self.conn.execute("DELETE FROM ledger_meta WHERE key='retain_held'")
            return 0
        per = c.execute("SELECT src, COUNT(*) FROM postings WHERE svc < ? GROUP BY src", (cutoff,)).fetchall()
        total = c.execute("SELECT COUNT(*) FROM postings").fetchone()[0] or 0
        if not force and would_wait:
            self._meta_put("retain_held", json.dumps({"cutoff": cutoff, "rows": gone, "of": total, "days": days, "at": _now()},
                                                     sort_keys=True))
            return 0
        c.execute("BEGIN IMMEDIATE")
        try:
            n = c.execute("DELETE FROM postings WHERE svc < ?", (cutoff,)).rowcount
            for src, k in per:
                live = c.execute("SELECT live_rows FROM ledger_files WHERE src=?", (src,)).fetchone()
                left = max(0, (live[0] if live else 0) - k)
                c.execute("UPDATE ledger_files SET live_rows=?, status=CASE WHEN ?=0 THEN 'replaced' ELSE status END WHERE src=?",
                          (left, left, src))
            self._meta_put("retained_before", cutoff)
            c.execute("DELETE FROM ledger_meta WHERE key='retain_held'")
            self._bump()
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise
        return n

    def _retention_cut(self, days: int | None = None, *, today: str | None = None,
                       newest: str | None = None) -> tuple[str, int, bool] | None:
        """(cutoff, postings retention would delete, whether that waits for a person), or None on an empty ledger."""
        days = retain_days() if days is None else int(days)
        c = self.conn
        have = c.execute("SELECT MAX(posted) FROM postings").fetchone()[0]
        if not have:
            return None
        top = min(max(have, newest or "")[:10], (today or er.today_iso())[:10])
        cutoff = (date.fromisoformat(top) - timedelta(days=days)).isoformat()
        gone = c.execute("SELECT COUNT(*) FROM postings WHERE svc < ?", (cutoff,)).fetchone()[0] or 0
        total = c.execute("SELECT COUNT(*) FROM postings").fetchone()[0] or 0
        return cutoff, gone, bool(total) and gone > RETAIN_HOLD_SHARE * total

    def retain_held(self) -> dict | None:
        """Retention that waits for a person: {'cutoff', 'rows', 'of', 'days', 'at'}, or None."""
        raw = self._meta("retain_held")
        try:
            return json.loads(raw) if raw else None
        except ValueError:
            return None

    def forget(self, file_id: str) -> int:
        """Take one file out of the ledger (its rows and its record), to read it again (the CLI's --reread). The rows of
        older files it replaced do not come back: reading it again restores the ledger. Returns the rows removed."""
        if self.readonly:
            return 0
        f = self.file(file_id)
        if f is None:
            return 0
        c = self.conn
        c.execute("BEGIN IMMEDIATE")
        try:
            n = c.execute("DELETE FROM postings WHERE src=?", (f["src"],)).rowcount
            c.execute("DELETE FROM ledger_files WHERE src=?", (f["src"],))
            self._bump()
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise
        return n

    def copies_to_drop(self, keep: int | None = None) -> list[str]:
        """Files with no live rows whose box at least `keep` newer files with rows cover whole
        (REMITWATCH_EMA_REPORT_KEEP, default 2): the server may delete its kept copy of them. The catalog row stays."""
        if keep is None:
            try:
                keep = int(os.environ.get("REMITWATCH_EMA_REPORT_KEEP", "2") or 2)
            except ValueError:
                keep = 2
        live = self.boxes(LIVE)
        out = []
        for b in self.boxes(("replaced", "superseded")):
            if sum(1 for g in live if g.order > b.order and g.covers(b)) >= keep:
                out.append(b.file_id)
        return out

    def backup(self, dest: str) -> None:
        """Copy the ledger with SQLite's online backup (safe while it is open), written beside `dest` and moved into place."""
        tmp = dest + ".part"
        _remove(tmp)
        out = sqlite3.connect(tmp)
        try:
            self.conn.backup(out)
        finally:
            out.close()
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, dest)


# --------------------------------------------------------------------------------------------------- applying a file
def _result(status: str, file_id: str, **kw: Any) -> dict:
    out = {"status": status, "report": "production", "file_id": file_id, "reason": None, "rows_read": 0, "rows_kept": 0,
           "rows_applied": 0, "live_rows": 0, "scope": None, "box": None, "as_of": None, "replaced": [], "account": None,
           "holds": []}
    out.update(kw)
    return out


def _catalog_received(conn: sqlite3.Connection, file_id: str) -> str | None:
    try:
        r = conn.execute("SELECT received_at FROM ema_report_files WHERE file_id=?", (file_id,)).fetchone()
    except sqlite3.OperationalError:
        return None
    return r[0] if r and r[0] else None


def _mark_reread(conn: sqlite3.Connection, file_ids: Iterable[str], reason: str) -> int:
    n = 0
    for fid in file_ids:
        n += conn.execute("UPDATE ema_report_files SET status='reread', status_reason=? WHERE file_id=? AND report='production' "
                          "AND status IN ('current','partly_replaced')", (reason, fid)).rowcount
    if n:
        conn.commit()
    return n


class _Hashing(io.RawIOBase):
    """A read-only stream that hashes what is read through it (the file id of a report read from a stream)."""

    def __init__(self, raw: Any):
        super().__init__()
        self.raw = raw
        self.sha = hashlib.sha256()

    def readable(self) -> bool:
        return True

    def readinto(self, b: Any) -> int:
        data = self.raw.read(len(b))
        if not data:
            return 0
        n = len(data)
        b[:n] = data
        self.sha.update(data)
        return n


def _already(store: Any, ledger: Ledger, file_id: str) -> dict | None:
    """The ledger already holds this file: nothing is read again. The whole catalog is brought in line with the ledger
    (a run that died between the ledger's commit and the store's lost this file's row and the statuses of the files it
    replaced); ema_reports then writes the files and ema_sources rows the store lacks."""
    have = ledger.file(file_id)
    if not have or have.get("status") not in APPLIED:
        return None
    sync_catalog(store, ledger)
    box = next((b for b in ledger.boxes(APPLIED) if b.file_id == file_id), None)
    return _result("already", file_id, reason=have.get("status"), rows_read=have.get("rows_read") or 0,
                   rows_kept=have.get("rows") or 0, rows_applied=have.get("rows") or 0, live_rows=have.get("live_rows") or 0,
                   scope=have.get("scope"), box=box, as_of=have.get("as_of"))


def _ledger_of(target: Any) -> "Ledger":
    """A Ledger from what a caller holds: the ledger, a LedgerHandle (opened), or a zero-argument opener."""
    if isinstance(target, Ledger):
        return target
    if isinstance(target, LedgerHandle):
        return target.open()
    if callable(target):
        return target()
    raise TypeError("apply_postings needs a Ledger, a LedgerHandle or a callable that opens one")


def _bind(store: Any, ledger: Ledger, file_id: str | None, log: Any) -> None:
    """Tie the ledger to the store's key salt; a ledger keyed for another store is emptied and its files read again."""
    reread = ledger.bind_salt(er.salt_fingerprint(er.key_salt(store.conn)))
    if reread:
        _mark_reread(store.conn, [f for f in reread if f != file_id],
                     "The posting ledger was keyed for another store; the file is read again.")
        log(f"  EMA posting ledger: emptied (keyed for another store); {len(reread)} file(s) to read again")


def _box_fields(box: Any) -> dict:
    keys = ("scope", "svc_lo", "svc_hi", "posted_lo", "posted_hi", "as_of")
    if isinstance(box, dict):
        return {k: box.get(k) for k in keys}
    return {k: getattr(box, k, None) for k in keys}


def apply_postings(target: Any, source_id: str | None, box: Any, postings: Iterable[Any], *, source_kind: str, store: Any,
                   source_name: str | None = None, via: str | None = None, account: Any = None, force: bool = False,
                   deadline: float | None = None, serverless: bool = False, log: Any = print, fmt: str = "csv",
                   dimension: int | None = None, created_at: str | None = None, resolve_id: Any = None) -> dict:
    """The seam every source of postings goes through (amendment 10): apply an iterable of ema_reports.Posting for one
    box, with the same replacement, holds and invariant as a file. The file readers are one adapter that calls this
    (apply_production_file); an API pull would be another, with nothing else to change.

    `target`: a Ledger, a LedgerHandle, or a zero-argument callable that opens one. `box`: None to take the box from the
    postings (a file: service scope when it starts posting when its services start, else posted scope), or the range the
    postings were asked for (a Box, or a dict of scope, svc_lo, svc_hi, posted_lo, posted_hi, as_of). `source_kind`:
    'file', 'upload' or 'api', recorded on the applied batch with a content id (a hash of its postings in order): a batch
    whose postings are those of a batch on file is 'already' and changes nothing (the same export saved again). Postings
    are never de-duplicated one by one: two identical postings are two postings. `account`: the reader's counts (an
    ema_reports.Account) when a reader made the postings. `resolve_id`: for a batch whose id is known only once it is
    read (a stream), a callable that returns it after staging. Returns what apply_production_file returns, plus
    'content_id' and 'source_kind'."""
    if source_kind not in er.SOURCE_KINDS:
        raise ValueError("source_kind is file, upload or api")
    ledger = _ledger_of(target)
    conn = store.conn
    er.ensure_schema(conn)
    _bind(store, ledger, source_id, log)
    if source_id is not None:
        done = _already(store, ledger, source_id)
        if done is not None:
            return done
    acct = account if account is not None else er.Account()
    digest = hashlib.sha256()

    def hashed(rows: Iterable[Any]) -> Iterable[Any]:
        for p in rows:
            digest.update(repr(tuple(p)).encode())
            yield p
    t0 = time.time()
    try:
        stats = ledger.stage(hashed(postings), deadline=deadline)
    except er.ReadTimeout:
        ledger.drop_stage()
        log("  EMA Production: stopped at the time limit; it is read again on the next run")
        return _result("waiting", source_id, reason="Reading stopped at the time limit; it is read again on the next run.",
                       rows_read=acct.get("rows_read") or 0)
    except BaseException:
        ledger.drop_stage()
        raise
    if resolve_id is not None:
        source_id = resolve_id()
        done = _already(store, ledger, source_id)
        if done is not None:
            ledger.drop_stage()
            return done
    content_id = digest.hexdigest()
    dup = ledger.duplicate_of(content_id, source_id)
    if dup is not None:
        ledger.drop_stage()
        reason = f"It holds the same postings as the export read on {_day(dup['applied_at'])} (saved again): nothing changed."
        log("  EMA Production: already read (the same postings as a file on file)")
        return _result("already", source_id, reason=reason, duplicate_of=dup["file_id"], content_id=content_id,
                       rows_read=acct.get("rows_read") or stats.get("rows") or 0)
    try:
        rows_kept = acct.get("rows_kept") if account is not None else stats.get("rows")
        if not rows_kept:
            raise er.ReportRefused("empty", "It holds no posting with a posted date and a service date.", "production")
        left = acct.get("left") or {}
        stats.update(rows_read=acct.get("rows_read") or stats.get("rows") or 0, no_date=left.get("no_date") or 0,
                     misaligned=left.get("misaligned") or 0, cut_short=bool(acct.get("cut_short")),
                     damaged_checks=acct.get("damaged_checks") or 0, check_rows=acct.get("check_rows") or 0,
                     identity_breaks=acct.get("identity_breaks") or stats.get("identity_breaks") or 0)
        lo = {k: acct.get(k) or stats.get(k) for k in ("svc_lo", "svc_hi", "posted_lo", "posted_hi")}
        if box is None:
            scope = scope_of(lo["svc_lo"], lo["posted_lo"])
            as_of = acct.get("as_of") or lo["posted_hi"]
        else:
            b = _box_fields(box)
            scope = b["scope"] if b["scope"] in ("service", "posted") else scope_of(lo["svc_lo"], lo["posted_lo"])
            lo.update({k: b[k] for k in ("svc_lo", "svc_hi", "posted_lo", "posted_hi") if b.get(k)})
            as_of = b.get("as_of") or lo["posted_hi"]
        meta = FileMeta(file_id=source_id, source_name=source_name or "postings", via=via or source_kind, scope=scope,
                        svc_lo=lo["svc_lo"], svc_hi=lo["svc_hi"], posted_lo=lo["posted_lo"], posted_hi=lo["posted_hi"],
                        as_of=as_of, created_at=created_at, received_at=_catalog_received(conn, source_id) or _now(),
                        rows_read=stats["rows_read"], rows_kept=rows_kept, links=stats.get("links"), content_id=content_id,
                        source_kind=source_kind)
        acc = dict(acct)
        acc.update(links=stats.get("links"), payers=stats.get("payers"), claim_lines=stats.get("lines"),
                   charge_line_share=stats.get("charge_line_share"))
        holds = [] if force else ledger.holds(meta, stats, serverless=serverless, fmt=fmt, dimension=dimension)
        if holds:
            reason = " ".join(m for _c, m in holds)
            log(f"  EMA Production: held ({', '.join(c for c, _m in holds)})")
            return _result("held", source_id, reason=reason, holds=[{"code": c, "message": m} for c, m in holds],
                           rows_read=meta.rows_read, rows_kept=meta.rows_kept, scope=meta.scope, box=meta.box(), as_of=meta.as_of,
                           account=acc, links=stats.get("links"), received_at=meta.received_at, content_id=content_id,
                           source_kind=source_kind)
        # retention first, as of the batch's own newest posting: the pages the old postings free are the ones its rows
        # take, so the ledger file does not grow by a year of service on the day a new service year arrives (a
        # serverless host's temporary folder holds it twice over during the refresh)
        ledger.retain(newest=meta.posted_hi)
        res = ledger.commit(meta)
    finally:
        ledger.drop_stage()
    if getattr(acct, "status_words", None):
        er.record_unknown_statuses(conn, acct.status_words)
    secs = round(time.time() - t0, 1)
    log(f"  EMA Production: {res['status']}, {res['rows_applied']:,} of {meta.rows_kept:,} rows applied ({meta.scope} scope, "
        f"{secs} s)" + (f"; {len(res['replaced'])} older file(s) replaced in part or whole" if res["replaced"] else ""))
    reason = None
    if res["status"] == "superseded":
        reason = "Newer exports on file cover every posting it holds."
    elif res["live_rows"] < res["rows"]:
        reason = f"{res['rows'] - res['live_rows']:,} of its postings fall where a newer export is on file."
    return _result(res["status"], source_id, reason=reason, rows_read=meta.rows_read, rows_kept=meta.rows_kept,
                   rows_applied=res["rows_applied"], live_rows=res["live_rows"], scope=meta.scope, box=res["box"],
                   as_of=meta.as_of, replaced=res["replaced"], account=acc, links=stats.get("links"),
                   received_at=meta.received_at, seq=ledger.seq(), seconds=secs, content_id=content_id, source_kind=source_kind)


def apply_production_file(store: Any, path: Any, rec: Any, *, file_id: str | None, via: str, origin: dict | None = None,
                          force: bool = False, ledger: Ledger, deadline: float | None = None, serverless: bool = False,
                          log: Any = print, source_name: str | None = None, source_kind: str | None = None) -> dict:
    """Read one Production file into the ledger: the file adapter of apply_postings (it reads the rows and hands them on).
    ema_reports.ingest_file calls this and writes the catalog, files and ema_sources rows from the result; it does not
    write them here (except a catalog row the ledger holds and the store lost: see sync_catalog).

    `path` is a file on disk (CSV, gzipped CSV, xlsx or a zip member as `rec` says), or a binary stream of delimited text
    (a download read as it arrives, never written anywhere). For a stream `file_id` may be None: the SHA-256 of the bytes
    read becomes the file's id, and a file the ledger already holds is then left as it is ('already').

    Result: {'status': 'current'|'superseded'|'held'|'waiting'|'already', 'reason', 'rows_read', 'rows_kept',
    'rows_applied', 'live_rows', 'scope', 'box', 'as_of', 'replaced': [(file_id, status)], 'account', 'links', 'holds',
    'received_at', 'seq', 'content_id', 'source_kind'} ('duplicate_of' when the same postings are on file under another
    file). A refusal raises ema_reports.ReportRefused (never applied, whatever `force` says). Past `deadline` the file is
    left 'waiting' and the ledger is unchanged."""
    conn = store.conn
    er.ensure_schema(conn)
    salt = er.key_salt(conn)
    hasher = None
    if isinstance(path, (bytes, bytearray, memoryview)):
        path = io.BytesIO(bytes(path))
    if not isinstance(path, (str, os.PathLike)):
        if getattr(rec, "fmt", "csv") != "csv" or getattr(rec, "member", None):
            raise er.ReportRefused("unreadable", "Only a CSV Production report can be read as it downloads. Save the file "
                                   "first, or zip it and let the zip be read.", "production")
        source_name = source_name or os.path.basename(str(getattr(path, "name", "") or "")) or "stream"
        hasher = _Hashing(path)
        path = io.BufferedReader(hasher, 1 << 20)
    else:
        path = os.fspath(path)
        source_name = source_name or er._names_of(path, rec)[0]
        if file_id is None:
            file_id = er.report_id(path, rec)
    _bind(store, ledger, file_id, log)
    if file_id is not None:
        done = _already(store, ledger, file_id)
        if done is not None:
            return done
    if getattr(rec, "missing", ()):
        raise er.ReportRefused("missing_columns", er._missing_message(rec), "production")
    if not force and serverless and rec.fmt == "xlsx" and (rec.dimension or 0) > XLSX_MAX_ROWS:
        msg = (f"Save it as CSV: an Excel Production export of more than {XLSX_MAX_ROWS:,} rows is not read on this site.")
        log("  EMA Production: held (H6)")
        return _result("held", file_id, reason=msg, holds=[{"code": "H6", "message": msg}], rows_read=rec.dimension or 0)
    created = None
    if hasher is None:
        try:
            created = er._created_at(path, rec)
        except Exception:       # noqa: BLE001  (the workbook does not say when it was made)
            created = None

    def stream_id() -> str | None:
        while path.read(1 << 20):           # what the reader left (lines after the last row) is part of the file's bytes
            pass
        return file_id or hasher.sha.hexdigest()
    acct = er.Account()
    return apply_postings(ledger, file_id, None, er.iter_production(path, rec, salt=salt, account=acct, deadline=deadline),
                          source_kind=source_kind or er.source_kind_of(via), store=store, source_name=source_name, via=via,
                          account=acct, force=force, deadline=deadline, serverless=serverless, log=log,
                          fmt=getattr(rec, "fmt", "csv"), dimension=getattr(rec, "dimension", None), created_at=created,
                          resolve_id=stream_id if hasher is not None else None)


def sync_catalog(store: Any, ledger: Ledger, file_ids: Iterable[str] | None = None) -> int:
    """Bring the catalog (ema_report_files) in line with the ledger: each file the ledger holds gets its status, live rows
    and box (a row is restored when the store lost it, as after a run that saved the ledger and died before saving the
    store); a Production file the catalog calls current or partly replaced that the ledger no longer holds becomes
    'reread', to be read again. Returns the rows changed."""
    conn = store.conn
    er.ensure_schema(conn)
    only = set(file_ids) if file_ids is not None else None
    n = 0
    held = set()
    for f in ledger.files():
        held.add(f["file_id"])
        if only is not None and f["file_id"] not in only:
            continue
        row = er._catalog_row(conn, f["file_id"])
        want = dict(status=f["status"], live_rows=f["live_rows"], scope=f["scope"], svc_lo=f["svc_lo"], svc_hi=f["svc_hi"],
                    posted_lo=f["posted_lo"], posted_hi=f["posted_hi"], as_of=f["as_of"], rows_applied=f["rows"])
        if f.get("replaced_by"):
            want["replaced_by"] = f["replaced_by"]
        if row is None:
            er._catalog_put(conn, f["file_id"], report="production", source_name=f.get("source_name"), via=f.get("via"),
                            received_at=f.get("received_at"), applied_at=f.get("applied_at"), created_at=f.get("created_at"),
                            rows_read=f.get("rows_read"), rows_kept=f.get("rows"), links=f.get("links"),
                            status_reason="Restored from the posting ledger after an interrupted run.", **want)
            n += 1
        elif any(row.get(k) != v for k, v in want.items()):
            er._catalog_put(conn, f["file_id"], **want)
            n += 1
    if only is None:
        for (fid,) in conn.execute("SELECT file_id FROM ema_report_files WHERE report='production' "
                                   "AND status IN ('current','partly_replaced')").fetchall():
            if fid not in held:
                er._catalog_put(conn, fid, status="reread",
                                status_reason="The posting ledger no longer holds it; it is read again.")
                n += 1
    conn.commit()
    return n


# ---------------------------------------------------------------------------------------------------- the keep rule
KEEP_RULES = ("nonroutine", "no835", "unmapped", "on_ar", "finding", "open_status")
_LIVE_FINDING = ("COALESCE(stale, 0) = 0 AND (status IN ('open','in_progress','appealed','lost','dismissed') "
                 "OR notes IS NOT NULL OR appealed_at IS NOT NULL OR (status = 'resolved' AND auto_note IS NULL))")


def _finding_bills_sql(conn: sqlite3.Connection) -> str | None:
    """SELECT bill FROM the store's findings that are open, started, sent or worked (by a person), or None when the store
    has no findings yet."""
    cols = set(_columns(conn, "main", "findings")) if _table_exists(conn, "main", "findings") else set()
    if "patient_control_number" not in cols:
        return None
    cond = _LIVE_FINDING
    for c in ("stale", "status", "notes", "appealed_at", "auto_note"):
        if c not in cols:
            cond = "status IN ('open','in_progress','appealed')" if "status" in cols else "1"
            break
    return f"SELECT rw_bill(patient_control_number) AS bill FROM main.findings WHERE patient_control_number IS NOT NULL AND {cond}"


def _keep_conditions(lines: str, payments: str, link_map: str, claim_links: str, finding_bills: str | None) -> list[tuple[str, str]]:
    L = lines.split(".")[-1]
    return [
        ("nonroutine", f"{L}.denial_first_posted IS NOT NULL"),
        ("no835", f"EXISTS (SELECT 1 FROM {payments} y WHERE y.link = {L}.link AND y.svc = {L}.svc AND y.cpt = {L}.cpt "
                  f"AND COALESCE(y.in_835, 0) = 0 AND COALESCE(y.ins_paid, 0) <> 0)"),
        ("unmapped", f"{L}.link NOT IN (SELECT link FROM {link_map} WHERE COALESCE(conflict, 0) = 0 AND bill IS NOT NULL)"),
        ("on_ar", f"{L}.link IN (SELECT link FROM {claim_links} WHERE link IS NOT NULL)"),
        ("finding", (f"{L}.link IN (SELECT m.link FROM {link_map} m WHERE COALESCE(m.conflict, 0) = 0 AND m.bill IN ({finding_bills}))"
                     if finding_bills else "0")),
        ("open_status", f"COALESCE({L}.status_class, 'none') <> 'paid'"),
    ]


def keep_rule_sql(lines: str = "ema_lines", payments: str = "main.ema_line_payments", link_map: str = "main.ema_link_map",
                  claim_links: str = "main.ema_claim_links", finding_bills: str | None = "auto",
                  conn: sqlite3.Connection | None = None) -> str:
    """The lean keep rule (spec 7.1) as one SQL condition over a lines table (ema_lines by default). A line stays when any
    of these holds: a non-routine insurance adjustment (a payer's CARC, group not PR, code not in detect.IGNORE_CARCS);
    an insurance payment under a check that is not an 835 trace; its link is not mapped to a bill (or is a conflict);
    its link is on the A/R; its mapped bill has a finding that is open, started, sent or worked; its newest status is not
    Resolved/Paid. The finding rule reads the store's findings (pass `conn` to leave it out when there are none) and
    needs rw_bill registered (ema_reports.register_functions)."""
    if finding_bills == "auto":
        finding_bills = _finding_bills_sql(conn) if conn is not None else (
            "SELECT rw_bill(patient_control_number) AS bill FROM main.findings WHERE patient_control_number IS NOT NULL "
            f"AND {_LIVE_FINDING}")
    return "(" + " OR ".join(f"({cond})" for _n, cond in _keep_conditions(lines, payments, link_map, claim_links, finding_bills)) + ")"


# ------------------------------------------------------------------------------------------------------- refresh work
class _Work:
    """A scratch database attached to the store connection for one refresh (in the ledger's folder, or the temporary
    folder), so intermediate tables never grow the store and are deleted whole when the refresh ends. On a serverless
    host SQLite's own sorts stay in memory (temp_store=MEMORY): written to the temporary folder, they took 50 MB of its
    512 MB at two service years. A server sorts to files, as before."""

    def __init__(self, conn: sqlite3.Connection, where: str | None):
        self.conn = conn
        d = where or runtime.tmp_dir()
        os.makedirs(d, exist_ok=True)
        clean_work_files(d)
        self.path = os.path.join(d, f"{WORK_PREFIX}{os.getpid()}-{secrets.token_hex(4)}.sqlite")
        self.temp_store: int | None = None
        self.ledger_attached = False

    def __enter__(self) -> "_Work":
        c = self.conn
        c.commit()
        self.temp_store = c.execute("PRAGMA temp_store").fetchone()[0]
        c.execute("PRAGMA temp_store=MEMORY" if runtime.serverless() else "PRAGMA temp_store=FILE")
        c.execute(f"ATTACH DATABASE ? AS {_W}", (self.path,))
        c.execute(f"PRAGMA {_W}.journal_mode=OFF")
        c.execute(f"PRAGMA {_W}.synchronous=OFF")
        c.execute(f"PRAGMA {_W}.cache_size=-32768")
        return self

    def attach_ledger(self, ledger: Ledger) -> None:
        if ledger.path == ":memory:":
            raise ValueError("refresh() needs a ledger kept in a file.")
        self.conn.commit()
        self.conn.execute(f"ATTACH DATABASE ? AS {_L}", (ledger.path,))
        self.conn.execute(f"PRAGMA {_L}.cache_size=-32768")
        self.ledger_attached = True

    def detach_ledger(self) -> None:
        """Part B and the publish never read the ledger: once Part A is done it is let go, so a serverless run can save
        and delete its scratch copy before the larger half of the work."""
        if self.ledger_attached:
            self.conn.commit()
            self.conn.execute(f"DETACH DATABASE {_L}")
            self.ledger_attached = False

    def __exit__(self, *exc: Any) -> None:
        c = self.conn
        try:
            if c.in_transaction:
                c.rollback()
        except sqlite3.Error:
            pass
        for name in ((_L,) if self.ledger_attached else ()) + (_W,):
            try:
                c.execute(f"DETACH DATABASE {name}")
            except sqlite3.Error:
                pass
        try:
            if self.temp_store is not None:
                c.execute(f"PRAGMA temp_store={int(self.temp_store)}")
        except sqlite3.Error:
            pass
        _remove(self.path)


def _drop(conn: sqlite3.Connection, *tables: str) -> None:
    """Drop work tables that are no longer needed: the work database has no journal, so their pages are reused at once
    and the file stops growing (the serverless host's temporary folder is small)."""
    for t in tables:
        conn.execute(f"DROP TABLE IF EXISTS {_W}.{t}")


def _insert_select(conn: sqlite3.Connection, target: str, exprs: dict, tail: str, args: Sequence = (),
                   verb: str = "INSERT") -> int:
    """INSERT INTO target (every column) SELECT the given expression per column (NULL for the rest) <tail>."""
    schema, table = target.split(".")
    cols = _columns(conn, schema, table)
    sel = ", ".join(exprs.get(c, "NULL") for c in cols)
    return conn.execute(f"{verb} INTO {target} ({', '.join(cols)}) SELECT {sel} {tail}", list(args)).rowcount


# -------------------------------------------------------------------------------------------------------------- Part A
def _part_a(conn: sqlite3.Connection, log: Any) -> dict:
    """Part A: summaries of the whole ledger into the work database (nothing in the store changes here)."""
    t0 = time.time()
    x = conn.execute
    W, L = _W, _L
    nr = f"(p.grp IS NOT NULL AND p.grp <> 'PR' AND COALESCE(p.code, '') NOT IN ({_ignore_sql()}))"
    adj = "(COALESCE(p.contractual, 0) <> 0 OR COALESCE(p.noncontractual, 0) <> 0)"
    answer = "(COALESCE(p.ins_paid, 0) <> 0 OR p.check_no IS NOT NULL OR p.grp IS NOT NULL)"
    cpt = "COALESCE(p.cpt, '')"
    cptc = "CAST(COALESCE(p.cpt, '') AS TEXT)"          # work tables get declared types, so their indexes are used
    newest = "MAX(p.posted || printf('%012d', p.rowid))"
    # the months first, while the work file is empty: their grouping is one more full pass over the ledger, and its
    # intermediate tables are dropped before the line tables are made
    n_mon = _months(conn, nr, adj)
    # one row per claim line (link, service date, code)
    x(f"""CREATE TABLE {W}.pa_agg AS
        SELECT p.link AS link, p.svc AS svc, {cptc} AS cpt, CAST(MAX(p.person) AS INTEGER) AS person,
               CAST(MAX(p.name_key) AS INTEGER) AS name_key,
               SUM(CASE WHEN COALESCE(p.charges, 0) <> 0 THEN p.units END) AS units_c, MAX(p.units) AS units_m,
               SUM(COALESCE(p.charges, 0)) AS charges, SUM(COALESCE(p.ins_charges, 0)) AS ins_charges,
               SUM(COALESCE(p.ins_paid, 0)) AS ins_paid, SUM(COALESCE(p.contractual, 0)) AS contractual,
               SUM(COALESCE(p.noncontractual, 0)) AS noncontractual,
               SUM(CASE WHEN {nr} THEN COALESCE(p.contractual, 0) + COALESCE(p.noncontractual, 0) ELSE 0 END) AS nonroutine,
               MIN(CASE WHEN {nr} AND {adj} THEN p.posted END) AS denial_first_posted,
               SUM(COALESCE(p.pat_paid, 0)) AS own_pat_paid, SUM(COALESCE(p.pat_adj, 0)) AS own_pat_adj,
               MIN(p.posted) AS first_posted, MAX(p.posted) AS last_posted,
               MIN(CASE WHEN COALESCE(p.ins_paid, 0) <> 0 THEN p.posted END) AS first_ins_paid,
               MAX(CASE WHEN COALESCE(p.ins_paid, 0) <> 0 THEN p.posted END) AS last_ins_paid,
               SUM(CASE WHEN p.check_no IS NOT NULL AND COALESCE(p.ins_paid, 0) = 0 THEN 1 ELSE 0 END) AS zero_pay_rows,
               COUNT(DISTINCT p.check_no) AS checks,
               MAX(p.flags & 1) + MAX(p.flags & 2) + MAX(p.flags & 4) + MAX(p.flags & 8) + MAX(p.flags & 16) AS flags,
               MAX(f.as_of) AS as_of,
               MIN(CASE WHEN COALESCE(p.charges, 0) > 0 THEN p.posted END) AS first_charge_posted,
               MIN(CASE WHEN {answer} THEN p.posted END) AS first_ins_posting,
               SUM(CASE WHEN {answer} THEN 1 ELSE 0 END) AS ins_answer_rows,
               MAX(p.pccn) AS pccn
        FROM {L}.postings p JOIN {L}.ledger_files f ON f.src = p.src
        WHERE p.link IS NOT NULL
        GROUP BY p.link, p.svc, {cpt}""")
    x(f"CREATE UNIQUE INDEX {W}.ix_pa_agg ON pa_agg(link, svc, cpt)")
    x(f"CREATE INDEX {W}.ix_pa_agg_pd ON pa_agg(person, svc, cpt)")
    # the newest posting's status, action and payers (bare columns take the row of the single max())
    for name, col in (("pa_st", "status"), ("pa_ac", "action")):
        x(f"""CREATE TABLE {W}.{name} AS SELECT p.link AS link, p.svc AS svc, {cptc} AS cpt, {newest} AS k, p.{col} AS v
              FROM {L}.postings p WHERE p.link IS NOT NULL AND p.{col} IS NOT NULL GROUP BY p.link, p.svc, {cpt}""")
        x(f"CREATE UNIQUE INDEX {W}.ix_{name} ON {name}(link, svc, cpt)")
    x(f"""CREATE TABLE {W}.pa_py AS SELECT p.link AS link, p.svc AS svc, {cptc} AS cpt, {newest} AS k,
              p.primary_payer AS primary_payer, p.secondary_payer AS secondary_payer, p.plan AS plan
          FROM {L}.postings p WHERE p.link IS NOT NULL AND (p.primary_payer IS NOT NULL OR p.secondary_payer IS NOT NULL
                OR p.plan IS NOT NULL) GROUP BY p.link, p.svc, {cpt}""")
    x(f"CREATE UNIQUE INDEX {W}.ix_pa_py ON pa_py(link, svc, cpt)")
    # patient-side money, by patient, day and code, joined to the one claim line with the same keys
    x(f"""CREATE TABLE {W}.pa_pat AS SELECT p.person AS person, p.svc AS svc, {cptc} AS cpt,
              SUM(COALESCE(p.pat_paid, 0)) AS pat_paid, SUM(COALESCE(p.pat_adj, 0)) AS pat_adj, COUNT(*) AS rows
          FROM {L}.postings p WHERE p.link IS NULL AND (p.flags & 16) <> 0 AND p.person IS NOT NULL
          GROUP BY p.person, p.svc, {cpt}""")
    x(f"""CREATE TABLE {W}.pa_pj AS SELECT g.person AS person, g.svc AS svc, g.cpt AS cpt, g.pat_paid AS pat_paid,
              g.pat_adj AS pat_adj, g.rows AS rows,
              CAST((SELECT COUNT(*) FROM {W}.pa_agg l WHERE l.person = g.person AND l.svc = g.svc AND l.cpt = g.cpt) AS INTEGER) AS n,
              CAST((SELECT MIN(l.link) FROM {W}.pa_agg l WHERE l.person = g.person AND l.svc = g.svc AND l.cpt = g.cpt) AS INTEGER)
                  AS link
          FROM {W}.pa_pat g""")
    x(f"CREATE UNIQUE INDEX {W}.ix_pa_pj ON pa_pj(person, svc, cpt)")
    x(f"""CREATE TABLE {W}.pa_pad AS SELECT j.link AS link, j.svc AS svc, j.cpt AS cpt, SUM(COALESCE(p.pat_paid, 0)) AS cents
          FROM {L}.postings p
          JOIN {W}.pa_pj j ON j.person = p.person AND j.svc = p.svc AND j.cpt = {cpt} AND j.n = 1
          JOIN {W}.pa_agg l ON l.link = j.link AND l.svc = j.svc AND l.cpt = j.cpt
          WHERE p.link IS NULL AND (p.flags & 16) <> 0 AND l.denial_first_posted IS NOT NULL
            AND p.posted > l.denial_first_posted AND p.posted > p.svc AND COALESCE(p.pat_paid, 0) <> 0
          GROUP BY j.link, j.svc, j.cpt""")
    x(f"CREATE UNIQUE INDEX {W}.ix_pa_pad ON pa_pad(link, svc, cpt)")
    # EMA's allowed amount: the largest net allowed of one payer (a second payer's allowed is not added)
    x(f"""CREATE TABLE {W}.pa_alw AS SELECT CAST(link AS INTEGER) AS link, CAST(svc AS TEXT) AS svc, CAST(cpt AS TEXT) AS cpt,
              MAX(a) AS allowed FROM (
              SELECT p.link AS link, p.svc AS svc, {cpt} AS cpt, p.payer AS payer, SUM(p.allowed) AS a
              FROM {L}.postings p WHERE p.link IS NOT NULL AND COALESCE(p.allowed, 0) <> 0 GROUP BY 1, 2, 3, 4)
          GROUP BY link, svc, cpt""")
    x(f"CREATE UNIQUE INDEX {W}.ix_pa_alw ON pa_alw(link, svc, cpt)")
    # the lines, in ema_lines' layout
    x(f"CREATE TABLE {W}.pa_full AS SELECT * FROM main.ema_lines WHERE 0")
    n_lines = _insert_select(conn, f"{W}.pa_full", {
        "link": "a.link", "svc": "a.svc", "cpt": "a.cpt", "person": "a.person", "name_key": "a.name_key",
        "primary_payer": "npp.name", "secondary_payer": "nsp.name", "plan": "npl.name", "pccn": "a.pccn",
        "units": "COALESCE(a.units_c, a.units_m)", "charges": "a.charges", "ins_charges": "a.ins_charges", "ins_paid": "a.ins_paid",
        "contractual": "a.contractual", "noncontractual": "a.noncontractual", "nonroutine": "a.nonroutine",
        "denial_first_posted": "a.denial_first_posted",
        "pat_paid_joined": "a.own_pat_paid + CASE WHEN j.n = 1 THEN j.pat_paid ELSE 0 END",
        "pat_adj_joined": "a.own_pat_adj + CASE WHEN j.n = 1 THEN j.pat_adj ELSE 0 END",
        "pat_join": "CASE WHEN j.n = 1 THEN 'one' WHEN j.n > 1 THEN 'several' END",
        "pat_after_denial": "COALESCE(d.cents, 0)", "allowed": "w.allowed",
        "first_posted": "a.first_posted", "last_posted": "a.last_posted", "first_ins_paid": "a.first_ins_paid",
        "last_ins_paid": "a.last_ins_paid", "zero_pay_rows": "a.zero_pay_rows", "checks": "a.checks", "status": "nst.name",
        "action": "nac.name", "flags": "a.flags", "as_of": "a.as_of", "facility": "0",
        "first_charge_posted": "a.first_charge_posted", "first_ins_posting": "a.first_ins_posting",
        "ins_answer_rows": "a.ins_answer_rows"},
        f"""FROM {W}.pa_agg a
            LEFT JOIN {W}.pa_st st ON st.link = a.link AND st.svc = a.svc AND st.cpt = a.cpt
            LEFT JOIN {L}.names nst ON nst.id = st.v
            LEFT JOIN {W}.pa_ac ac ON ac.link = a.link AND ac.svc = a.svc AND ac.cpt = a.cpt
            LEFT JOIN {L}.names nac ON nac.id = ac.v
            LEFT JOIN {W}.pa_py py ON py.link = a.link AND py.svc = a.svc AND py.cpt = a.cpt
            LEFT JOIN {L}.names npp ON npp.id = py.primary_payer
            LEFT JOIN {L}.names nsp ON nsp.id = py.secondary_payer
            LEFT JOIN {L}.names npl ON npl.id = py.plan
            LEFT JOIN {W}.pa_pj j ON j.person = a.person AND j.svc = a.svc AND j.cpt = a.cpt
            LEFT JOIN {W}.pa_pad d ON d.link = a.link AND d.svc = a.svc AND d.cpt = a.cpt
            LEFT JOIN {W}.pa_alw w ON w.link = a.link AND w.svc = a.svc AND w.cpt = a.cpt""")
    _drop(conn, "pa_agg", "pa_py", "pa_pat", "pa_pj", "pa_pad", "pa_alw")     # their pages are reused from here on
    _line_indexes(conn)
    x(f"""UPDATE {W}.pa_full SET balance = charges - ins_paid - contractual - noncontractual - pat_paid_joined - pat_adj_joined,
              status_class = rw_status_class(status, ins_answer_rows > 0),
              twin_link = CASE WHEN person IS NULL THEN NULL ELSE (SELECT MIN(t.link) FROM {W}.pa_full t
                  WHERE t.person = pa_full.person AND t.svc = pa_full.svc AND t.cpt = pa_full.cpt AND t.link <> pa_full.link) END""")
    n_ev = _events(conn)
    _drop(conn, "pa_st", "pa_ac")
    # payments and $0 answers: one row per (line, check, posted date, payer)
    x(f"CREATE TABLE {W}.pa_pay AS SELECT * FROM main.ema_line_payments WHERE 0")
    role = ("MIN(CASE WHEN p.payer IS NOT NULL AND p.payer = p.primary_payer THEN 'primary' WHEN p.payer IS NOT NULL "
            "AND p.payer = p.secondary_payer THEN 'secondary' ELSE 'other' END)")
    n_pay = _insert_select(conn, f"{W}.pa_pay", {
        "link": "p.link", "svc": "p.svc", "cpt": cpt, "check_no": "COALESCE(p.check_no, '')", "posted": "p.posted",
        "payer": "COALESCE(n.name, '')", "role": role, "ins_paid": "SUM(COALESCE(p.ins_paid, 0))", "rows": "COUNT(*)",
        "allowed": "SUM(COALESCE(p.allowed, 0))", "name_key": "MAX(p.name_key)", "in_835": "0"},
        f"""FROM {L}.postings p LEFT JOIN {L}.names n ON n.id = p.payer
            WHERE p.link IS NOT NULL AND (p.flags & 17) = 0 AND (p.check_no IS NOT NULL OR COALESCE(p.ins_paid, 0) <> 0)
            GROUP BY p.link, p.svc, {cpt}, COALESCE(p.check_no, ''), p.posted, COALESCE(n.name, '')""")
    _pay_indexes(conn)
    # adjustments by reason: the payers' non-routine reasons and EMA's own kinds (the routine reasons of lines whose bill
    # has a finding are read from the ledger when the store is written)
    x(f"CREATE TABLE {W}.pa_rsn AS SELECT * FROM main.ema_line_reasons WHERE 0")
    n_rsn = _insert_select(conn, f"{W}.pa_rsn", REASON_EXPRS, _reason_tail(f"p.link IS NOT NULL AND ({nr} OR (p.grp IS NULL "
                                                                           "AND p.kind IS NOT NULL))"))
    x(f"CREATE INDEX {W}.ix_pa_rsn ON pa_rsn(link, svc, cpt)")
    # the routine reasons (and group PR) of every line: the store keeps them only for lines whose bill has an item, which
    # Part B's map decides; they are read here so the ledger can be let go before Part B
    x(f"CREATE TABLE {W}.pa_rsr AS SELECT * FROM main.ema_line_reasons WHERE 0")
    _insert_select(conn, f"{W}.pa_rsr", REASON_EXPRS, _reason_tail(f"p.link IS NOT NULL AND p.grp IS NOT NULL AND NOT {nr}"))
    x(f"CREATE INDEX {W}.ix_pa_rsr ON pa_rsr(link, svc, cpt)")
    # checks (insurance rows with a check number; never product or patient-side rows)
    x(f"CREATE TABLE {W}.pa_chk AS SELECT * FROM main.ema_checks WHERE 0")
    n_chk = _insert_select(conn, f"{W}.pa_chk", {
        "check_no": "p.check_no", "payer": "COALESCE(n.name, '')", "shape": "rw_shape(p.check_no)",
        "damaged": "MAX((p.flags & 4) <> 0)", "first_posted": "MIN(p.posted)", "last_posted": "MAX(p.posted)", "rows": "COUNT(*)",
        "links": "COUNT(DISTINCT p.link)", "lines": f"COUNT(DISTINCT p.link || '|' || p.svc || '|' || {cpt})",
        "ins_paid": "SUM(COALESCE(p.ins_paid, 0))", "zero_rows": "SUM(CASE WHEN COALESCE(p.ins_paid, 0) = 0 THEN 1 ELSE 0 END)",
        "svc_lo": "MIN(p.svc)", "svc_hi": "MAX(p.svc)", "twin_cents": "0"},
        f"""FROM {L}.postings p LEFT JOIN {L}.names n ON n.id = p.payer
            WHERE p.check_no IS NOT NULL AND (p.flags & 17) = 0 GROUP BY p.check_no, COALESCE(n.name, '')""")
    x(f"CREATE UNIQUE INDEX {W}.ix_pa_chk ON pa_chk(check_no, payer)")
    x(f"""CREATE TABLE {W}.pa_twc AS SELECT y.check_no AS check_no, y.payer AS payer, SUM(y.ins_paid) AS cents
          FROM {W}.pa_pay y JOIN {W}.pa_full l ON l.link = y.link AND l.svc = y.svc AND l.cpt = y.cpt
          WHERE l.twin_link IS NOT NULL GROUP BY y.check_no, y.payer""")
    x(f"CREATE UNIQUE INDEX {W}.ix_pa_twc ON pa_twc(check_no, payer)")
    x(f"""UPDATE {W}.pa_chk SET twin_cents = COALESCE((SELECT t.cents FROM {W}.pa_twc t WHERE t.check_no = pa_chk.check_no
                  AND t.payer = pa_chk.payer), 0),
              cause = (SELECT c.cause FROM main.ema_checks c WHERE c.check_no = pa_chk.check_no AND c.payer = pa_chk.payer),
              pair_trace = (SELECT c.pair_trace FROM main.ema_checks c WHERE c.check_no = pa_chk.check_no AND c.payer = pa_chk.payer)""")
    _drop(conn, "pa_twc")
    conn.commit()               # only the work database changed: the store is written in one step by _publish
    return {"lines": n_lines, "payments": n_pay, "reasons": n_rsn, "checks": n_chk, "months": n_mon, "events": n_ev,
            "seconds": round(time.time() - t0, 1)}


REASON_EXPRS = {
    "link": "p.link", "svc": "p.svc", "cpt": "COALESCE(p.cpt, '')", "grp": "COALESCE(p.grp, '')",
    "code": "COALESCE(p.code, k.name, '')", "reason_class": "rw_reason_class(p.grp, p.code, k.name)",
    "payer": "COALESCE(MAX(n.name), '')", "contractual": "SUM(COALESCE(p.contractual, 0))",
    "noncontractual": "SUM(COALESCE(p.noncontractual, 0))", "rows": "COUNT(*)", "first_posted": "MIN(p.posted)",
    "last_posted": "MAX(p.posted)"}


def _reason_tail(where: str) -> str:
    """FROM ... WHERE <where> GROUP BY ...: the ledger's adjustments grouped as ema_line_reasons holds them."""
    return (f"FROM {_L}.postings p LEFT JOIN {_L}.names k ON k.id = p.kind LEFT JOIN {_L}.names n ON n.id = p.payer "
            f"WHERE {where} GROUP BY p.link, p.svc, COALESCE(p.cpt, ''), COALESCE(p.grp, ''), COALESCE(p.code, k.name, '')")


def _line_indexes(conn: sqlite3.Connection) -> None:
    conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {_W}.ix_pa_full ON pa_full(link, svc, cpt)")
    conn.execute(f"CREATE INDEX IF NOT EXISTS {_W}.ix_pa_full_pd ON pa_full(person, svc, cpt)")


def _pay_indexes(conn: sqlite3.Connection) -> None:
    conn.execute(f"CREATE INDEX IF NOT EXISTS {_W}.ix_pa_pay ON pa_pay(link, svc, cpt)")


def _months(conn: sqlite3.Connection, nr: str, adj: str) -> int:
    """ema_months: money by posted month and EMA payer (all measures but no835, which the checks write)."""
    x = conn.execute
    W, L = _W, _L
    x(f"""CREATE TABLE {W}.pa_mraw AS
        SELECT substr(p.posted, 1, 7) AS month, COALESCE(n.name, '') AS payer, p.grp AS grp, p.code AS code, p.kind AS kind,
               ((p.flags & 1) <> 0) AS product,
               SUM(COALESCE(p.ins_paid, 0)) AS ins_paid, SUM(COALESCE(p.ins_paid, 0) <> 0) AS ins_rows,
               SUM(COALESCE(p.pat_paid, 0)) AS pat_paid, SUM(COALESCE(p.pat_paid, 0) <> 0) AS pat_rows,
               SUM(COALESCE(p.charges, 0)) AS charges, SUM(COALESCE(p.charges, 0) <> 0) AS charge_rows,
               SUM(COALESCE(p.contractual, 0)) AS contractual, SUM(COALESCE(p.contractual, 0) <> 0) AS contr_rows,
               SUM(COALESCE(p.noncontractual, 0)) AS noncontractual, SUM(COALESCE(p.noncontractual, 0) <> 0) AS nonc_rows
        FROM {L}.postings p LEFT JOIN {L}.names n ON n.id = p.payer
        GROUP BY 1, 2, 3, 4, 5, 6""")
    x(f"ALTER TABLE {W}.pa_mraw ADD COLUMN cls TEXT")
    x(f"UPDATE {W}.pa_mraw SET cls = rw_reason_class(grp, code, (SELECT name FROM {L}.names WHERE id = pa_mraw.kind))")
    routine = ",".join(f"'{c}'" for c in ROUTINE_CLASSES)
    x(f"CREATE TABLE {W}.pa_mon AS SELECT * FROM main.ema_months WHERE 0")
    cols = "month, payer, measure, detail, cents, rows"
    x(f"""INSERT INTO {W}.pa_mon ({cols})
        SELECT month, payer, 'ins_paid', '', SUM(ins_paid), SUM(ins_rows) FROM {W}.pa_mraw WHERE ins_rows > 0 GROUP BY month, payer
        UNION ALL SELECT month, payer, 'pat_paid', '', SUM(pat_paid), SUM(pat_rows) FROM {W}.pa_mraw WHERE pat_rows > 0 GROUP BY month, payer
        UNION ALL SELECT month, payer, 'charges', '', SUM(charges), SUM(charge_rows) FROM {W}.pa_mraw
                  WHERE charge_rows > 0 AND NOT product GROUP BY month, payer
        UNION ALL SELECT month, payer, 'product', '', SUM(charges), SUM(charge_rows) FROM {W}.pa_mraw
                  WHERE charge_rows > 0 AND product GROUP BY month, payer
        UNION ALL SELECT month, payer, 'contractual_routine', '', SUM(contractual), SUM(contr_rows) FROM {W}.pa_mraw
                  WHERE contr_rows > 0 AND cls IN ({routine}) GROUP BY month, payer
        UNION ALL SELECT month, payer, 'contractual_denial', cls, SUM(contractual), SUM(contr_rows) FROM {W}.pa_mraw
                  WHERE contr_rows > 0 AND cls NOT IN ({routine}) GROUP BY month, payer, cls
        UNION ALL SELECT month, payer, 'noncontractual', cls, SUM(noncontractual), SUM(nonc_rows) FROM {W}.pa_mraw
                  WHERE nonc_rows > 0 GROUP BY month, payer, cls
        UNION ALL SELECT month, payer, 'timely', '', SUM(contractual + noncontractual), SUM(contr_rows + nonc_rows) FROM {W}.pa_mraw
                  WHERE cls = 'timely' AND (contr_rows > 0 OR nonc_rows > 0) GROUP BY month, payer""")
    # G16: denial-type write-offs by reason class and code, so C7 can name its top codes by month
    x(f"""CREATE TABLE {W}.pa_dcode AS
        SELECT substr(p.posted, 1, 7) AS month, COALESCE(n.name, '') AS payer, p.grp AS grp, p.code AS code,
               COALESCE(p.cpt, '') AS cpt, SUM(COALESCE(p.contractual, 0) + COALESCE(p.noncontractual, 0)) AS cents, COUNT(*) AS rows
        FROM {L}.postings p LEFT JOIN {L}.names n ON n.id = p.payer
        WHERE {nr} AND {adj} AND (p.flags & 17) = 0 GROUP BY 1, 2, 3, 4, 5""")
    x(f"""INSERT INTO {W}.pa_mon ({cols})
        SELECT month, payer, 'denial_code', rw_reason_class(grp, code) || '|' || cpt AS detail, SUM(cents), SUM(rows)
        FROM {W}.pa_dcode GROUP BY month, payer, detail""")
    _drop(conn, "pa_mraw", "pa_dcode")
    return conn.execute(f"SELECT COUNT(*) FROM {W}.pa_mon").fetchone()[0]


def _events(conn: sqlite3.Connection) -> int:
    """G14: each link's newest status and action, first seen at this Production as_of (kept first-seen by the store)."""
    x = conn.execute
    W, L = _W, _L
    x(f"CREATE TABLE {W}.pa_lk AS SELECT link, MAX(as_of) AS as_of FROM {W}.pa_full GROUP BY link")
    x(f"CREATE UNIQUE INDEX {W}.ix_pa_lk ON pa_lk(link)")
    x(f"""CREATE TABLE {W}.pa_ev AS
        SELECT CAST(s.link AS INTEGER) AS link, CAST('status' AS TEXT) AS kind, CAST(n.name AS TEXT) AS value,
               CAST(l.as_of AS TEXT) AS first_seen
        FROM (SELECT link, MAX(k) AS k, v FROM {W}.pa_st GROUP BY link) s
        JOIN {L}.names n ON n.id = s.v JOIN {W}.pa_lk l ON l.link = s.link
        UNION ALL
        SELECT s.link, 'action', n.name, l.as_of
        FROM (SELECT link, MAX(k) AS k, v FROM {W}.pa_ac GROUP BY link) s
        JOIN {L}.names n ON n.id = s.v JOIN {W}.pa_lk l ON l.link = s.link""")
    x(f"CREATE INDEX {W}.ix_pa_ev ON pa_ev(link, kind, value)")
    x(f"""UPDATE {W}.pa_full SET action_on = COALESCE(
              (SELECT e.first_seen FROM main.ema_link_events e WHERE e.link = pa_full.link AND e.kind = 'action' AND e.value = pa_full.action),
              (SELECT e.first_seen FROM {W}.pa_ev e WHERE e.link = pa_full.link AND e.kind = 'action' AND e.value = pa_full.action))
          WHERE action IS NOT NULL""")
    return conn.execute(f"SELECT COUNT(*) FROM {W}.pa_ev").fetchone()[0]


def _copy_store(conn: sqlite3.Connection) -> None:
    """Part B alone works on copies of the store's lean tables, so the store changes in one step at the end."""
    x = conn.execute
    W = _W
    x(f"CREATE TABLE {W}.pa_full AS SELECT * FROM main.ema_lines")
    _line_indexes(conn)
    x(f"CREATE TABLE {W}.pa_pay AS SELECT * FROM main.ema_line_payments")
    _pay_indexes(conn)
    x(f"CREATE TABLE {W}.pa_rsn AS SELECT * FROM main.ema_line_reasons")
    x(f"CREATE INDEX {W}.ix_pa_rsn ON pa_rsn(link, svc, cpt)")
    x(f"CREATE TABLE {W}.pa_chk AS SELECT * FROM main.ema_checks")
    x(f"CREATE UNIQUE INDEX {W}.ix_pa_chk ON pa_chk(check_no, payer)")


# -------------------------------------------------------------------------------------------------------------- Part B
def _traces(conn: sqlite3.Connection) -> int:
    """The 835 payments (not superseded) with a trace number, keyed for the exact and the loose comparison."""
    x = conn.execute
    W = _W
    x(f"""CREATE TABLE {W}.pb_tr AS
        SELECT t.transaction_key AS tk, CAST(rw_ckey(t.trace_number) AS TEXT) AS ckey,
               CAST(rw_check_loose(t.trace_number) AS TEXT) AS lkey, CAST(substr(trim(t.trace_number), -10) AS TEXT) AS last10,
               CAST(length(trim(t.trace_number)) AS INTEGER) AS len, t.payment_date AS paid_on,
               CAST(rw_payer_group(t.payer_name) AS TEXT) AS grp, t.claim_paid_total_cents AS paid
        FROM main.transactions t
        WHERE COALESCE(t.superseded, 0) = 0 AND t.trace_number IS NOT NULL AND trim(t.trace_number) <> ''""")
    x(f"CREATE INDEX {W}.ix_pb_tr_c ON pb_tr(ckey)")
    x(f"CREATE INDEX {W}.ix_pb_tr_l ON pb_tr(lkey)")
    x(f"CREATE INDEX {W}.ix_pb_tr_t ON pb_tr(last10, grp)")
    x(f"CREATE INDEX {W}.ix_pb_tr_k ON pb_tr(tk)")
    return conn.execute(f"SELECT COUNT(*) FROM {W}.pb_tr").fetchone()[0]


def _marks(conn: sqlite3.Connection) -> dict:
    """Which EMA checks an 835 carries: exact (the check equals a trace, or as integers when both are all digits), loose
    (letters and digits only with leading zeros dropped, or the last 10 characters within the payer group) or none; then
    in_835 and trace_key on each payment, ins_paid_no835 and payer groups on each line."""
    x = conn.execute
    W = _W
    def pick(cond: str) -> str:
        # the payer group is compared in a derived table's select list, not in ORDER BY: SQLite 3.45 (Ubuntu 24.04)
        # cannot name the updated table's columns in a subquery's ORDER BY ("no such column"); 3.53 can
        return (f"(SELECT tk FROM (SELECT t.tk AS tk, t.paid_on AS paid_on, (t.grp = pa_chk.payer_group) AS same "
                f"FROM {W}.pb_tr t WHERE {cond}) ORDER BY same DESC, paid_on DESC, tk LIMIT 1)")
    x(f"""UPDATE {W}.pa_chk SET payer_group = rw_payer_group(payer), payer_kind = rw_payer_kind(payer), match = 'none',
              trace_key = NULL, trace_payment_date = NULL, trace_paid_cents = NULL""")
    x(f"""UPDATE {W}.pa_chk SET match = 'exact',
              trace_key = {pick("t.ckey = rw_ckey(pa_chk.check_no)")}
          WHERE EXISTS (SELECT 1 FROM {W}.pb_tr t WHERE t.ckey = rw_ckey(pa_chk.check_no))""")
    x(f"""UPDATE {W}.pa_chk SET match = 'loose',
              trace_key = {pick("t.lkey = rw_check_loose(pa_chk.check_no)")}
          WHERE match = 'none' AND rw_check_loose(check_no) IS NOT NULL
            AND EXISTS (SELECT 1 FROM {W}.pb_tr t WHERE t.lkey = rw_check_loose(pa_chk.check_no))""")
    x(f"""UPDATE {W}.pa_chk SET match = 'loose',
              trace_key = {pick("t.last10 = substr(trim(pa_chk.check_no), -10) AND t.grp = pa_chk.payer_group AND t.len >= 10")}
          WHERE match = 'none' AND length(trim(check_no)) >= 10
            AND EXISTS (SELECT 1 FROM {W}.pb_tr t WHERE t.last10 = substr(trim(pa_chk.check_no), -10)
                        AND t.grp = pa_chk.payer_group AND t.len >= 10)""")
    x(f"""UPDATE {W}.pa_chk SET trace_payment_date = (SELECT t.paid_on FROM {W}.pb_tr t WHERE t.tk = pa_chk.trace_key),
              trace_paid_cents = (SELECT t.paid FROM {W}.pb_tr t WHERE t.tk = pa_chk.trace_key)
          WHERE trace_key IS NOT NULL""")
    x(f"""UPDATE {W}.pa_pay SET payer_group = rw_payer_group(payer),
              in_835 = CASE WHEN check_no <> '' AND (SELECT c.match FROM {W}.pa_chk c WHERE c.check_no = pa_pay.check_no
                       AND c.payer = pa_pay.payer) = 'exact' THEN 1 ELSE 0 END,
              trace_key = CASE WHEN check_no <> '' THEN (SELECT c.trace_key FROM {W}.pa_chk c WHERE c.check_no = pa_pay.check_no
                       AND c.payer = pa_pay.payer AND c.match = 'exact') END""")
    x(f"""UPDATE {W}.pa_full SET ins_paid_no835 = COALESCE((SELECT SUM(y.ins_paid) FROM {W}.pa_pay y WHERE y.link = pa_full.link
              AND y.svc = pa_full.svc AND y.cpt = pa_full.cpt AND COALESCE(y.in_835, 0) = 0), 0),
              payer_group = rw_payer_group(COALESCE(primary_payer, (SELECT y.payer FROM {W}.pa_pay y WHERE y.link = pa_full.link
                  AND y.payer <> '' ORDER BY y.posted DESC LIMIT 1))),
              payer_kind = rw_payer_kind(COALESCE(primary_payer, (SELECT y.payer FROM {W}.pa_pay y WHERE y.link = pa_full.link
                  AND y.payer <> '' ORDER BY y.posted DESC LIMIT 1)))""")
    out = dict(conn.execute(f"SELECT match, COUNT(*) FROM {W}.pa_chk GROUP BY match").fetchall())
    return {"exact": out.get("exact", 0), "loose": out.get("loose", 0), "none": out.get("none", 0)}


def _coverage_sql(boxes: Sequence[Box], svc_col: str, paid_col: str) -> tuple[str, list]:
    parts, args = [], []
    for b in boxes:
        if b.scope == "posted":
            parts.append(f"({paid_col} BETWEEN ? AND ?)")
            args += [b.posted_lo, b.posted_hi]
        else:
            parts.append(f"({svc_col} BETWEEN ? AND ?)")
            args += [b.svc_lo, b.svc_hi]
    return " OR ".join(parts), args


def _map(conn: sqlite3.Connection, *, full: bool, boxes: Sequence[Box]) -> dict:
    """The claim-link map (spec 7.2 as amended by E7 and G2) into the work database's pb_map: 'ar' from the A/R's claim
    ids, then 'payment' votes from every insurance row ($0 answers too) whose check is an 835 trace, then 'patient_day'
    for 835 bills still without a link. Facility links (the ASC's claims) are never a candidate. `full` rebuilds every
    link (after Part A); otherwise only links with no bill (or a conflict) are mapped again and the rest are kept."""
    x = conn.execute
    W = _W
    now = _now()
    a, b = VOTE_SHARE
    x(f"CREATE TABLE {W}.pb_old AS SELECT * FROM main.ema_link_map")
    x(f"CREATE UNIQUE INDEX {W}.ix_pb_old ON pb_old(link)")
    x(f"""CREATE TABLE {W}.pb_ar AS SELECT CAST(link AS INTEGER) AS link, CAST(claim_id AS TEXT) AS claim_id,
              CAST(rw_bill(claim_id) AS TEXT) AS bill, CAST(rw_is_facility(claim_id) AS INTEGER) AS fac FROM (
              SELECT link, MAX(COALESCE(last_seen, '') || '|' || claim_id) AS k, claim_id FROM main.ema_claim_links
              WHERE link IS NOT NULL AND claim_id IS NOT NULL GROUP BY link)""")
    x(f"CREATE UNIQUE INDEX {W}.ix_pb_ar ON pb_ar(link)")
    if full:
        x(f"CREATE TABLE {W}.pb_need AS SELECT DISTINCT link FROM {W}.pa_full")
    else:
        x(f"""CREATE TABLE {W}.pb_need AS SELECT DISTINCT l.link AS link FROM {W}.pa_full l LEFT JOIN {W}.pb_old m ON m.link = l.link
              WHERE m.link IS NULL OR COALESCE(m.conflict, 0) = 1 OR m.bill IS NULL OR l.link IN (SELECT link FROM {W}.pb_ar)""")
    x(f"CREATE UNIQUE INDEX {W}.ix_pb_need ON pb_need(link)")
    # payment votes
    x(f"""CREATE TABLE {W}.pb_ck AS SELECT CAST(check_no AS TEXT) AS check_no, CAST(rw_ckey(check_no) AS TEXT) AS ckey FROM (
              SELECT DISTINCT check_no FROM {W}.pa_pay WHERE in_835 = 1 AND check_no <> '')""")
    x(f"CREATE UNIQUE INDEX {W}.ix_pb_ck ON pb_ck(check_no)")
    x(f"CREATE TABLE {W}.pb_tx AS SELECT DISTINCT t.tk AS tk, t.ckey AS ckey FROM {W}.pb_tr t WHERE t.ckey IN (SELECT ckey FROM {W}.pb_ck)")
    x(f"CREATE UNIQUE INDEX {W}.ix_pb_tx ON pb_tx(tk)")
    x(f"""CREATE TABLE {W}.pb_l835 AS
        SELECT x.ckey AS ckey, CAST(COALESCE(l.service_date, c.statement_from) AS TEXT) AS svc, l.procedure_code AS code,
               l.original_procedure_code AS ocode, l.paid_cents AS paid, CAST(rw_bill(c.patient_control_number) AS TEXT) AS bill,
               c.patient_control_number AS pcn, CAST(rw_name_key(c.patient_last, c.patient_first) AS INTEGER) AS nk,
               CAST(CASE WHEN c.corrected_patient_last IS NOT NULL OR c.corrected_patient_first IS NOT NULL
                    THEN rw_name_key(COALESCE(c.corrected_patient_last, c.patient_last),
                                     COALESCE(c.corrected_patient_first, c.patient_first)) END AS INTEGER) AS cnk
        FROM main.lines l JOIN {W}.pb_tx x ON x.tk = l.transaction_key JOIN main.claims c ON c.claim_key = l.claim_key
        WHERE c.patient_control_number IS NOT NULL""")
    x(f"""CREATE TABLE {W}.pb_k835 AS
        SELECT CAST(ckey AS TEXT) AS ckey, CAST(svc AS TEXT) AS svc, CAST(code AS TEXT) AS code, paid, CAST(bill AS TEXT) AS bill,
               pcn, CAST(nk AS INTEGER) AS nk, CAST(cnk AS INTEGER) AS cnk FROM (
            SELECT ckey, svc, code, paid, bill, pcn, nk, cnk FROM {W}.pb_l835 WHERE code IS NOT NULL
            UNION ALL SELECT ckey, svc, ocode, paid, bill, pcn, nk, cnk FROM {W}.pb_l835
                      WHERE ocode IS NOT NULL AND ocode <> COALESCE(code, ''))""")
    _drop(conn, "pb_l835")
    x(f"CREATE INDEX {W}.ix_pb_k835 ON pb_k835(ckey, svc, code)")
    cand = f"""SELECT y.link AS link, y.svc AS svc, y.cpt AS cpt, y.check_no AS check_no, y.posted AS posted, y.payer AS payer,
               k.bill AS bill, k.pcn AS pcn,
               (y.name_key IS NOT NULL AND (y.name_key = k.nk OR y.name_key = k.cnk)) AS sn, (y.ins_paid = k.paid) AS sa
        FROM {W}.pa_pay y JOIN {W}.pb_ck ck ON ck.check_no = y.check_no
        JOIN {W}.pb_k835 k ON k.ckey = ck.ckey AND k.svc = y.svc AND k.code = y.cpt
        WHERE y.in_835 = 1 AND y.link IN (SELECT link FROM {W}.pb_need)
          AND y.link NOT IN (SELECT link FROM {W}.pb_ar WHERE fac = 1)"""
    # each payment votes for the bill of the first narrowing (name and amount, name, amount, any) that leaves exactly one
    x(f"""CREATE TABLE {W}.pb_votes AS
        SELECT CAST(link AS INTEGER) AS link,
               CAST(CASE WHEN n1 = 1 THEN b1 WHEN n2 = 1 THEN b2 WHEN n3 = 1 THEN b3 WHEN n4 = 1 THEN b4 END AS TEXT) AS bill,
               CASE WHEN n1 = 1 THEN p1 WHEN n2 = 1 THEN p2 WHEN n3 = 1 THEN p3 WHEN n4 = 1 THEN p4 END AS pcn
        FROM (SELECT link,
                COUNT(DISTINCT CASE WHEN sn AND sa THEN bill END) AS n1, MIN(CASE WHEN sn AND sa THEN bill END) AS b1,
                MAX(CASE WHEN sn AND sa THEN pcn END) AS p1,
                COUNT(DISTINCT CASE WHEN sn THEN bill END) AS n2, MIN(CASE WHEN sn THEN bill END) AS b2,
                MAX(CASE WHEN sn THEN pcn END) AS p2,
                COUNT(DISTINCT CASE WHEN sa THEN bill END) AS n3, MIN(CASE WHEN sa THEN bill END) AS b3,
                MAX(CASE WHEN sa THEN pcn END) AS p3,
                COUNT(DISTINCT bill) AS n4, MIN(bill) AS b4, MAX(pcn) AS p4
              FROM ({cand}) GROUP BY link, svc, cpt, check_no, posted, payer)""")
    x(f"""CREATE TABLE {W}.pb_pay AS SELECT CAST(link AS INTEGER) AS link, CAST(bill AS TEXT) AS bill, n AS votes, total, pcn FROM (
              SELECT link, bill, n, pcn, SUM(n) OVER (PARTITION BY link) AS total,
                     ROW_NUMBER() OVER (PARTITION BY link ORDER BY n DESC, bill) AS rk
              FROM (SELECT link, bill, COUNT(*) AS n, MAX(pcn) AS pcn FROM {W}.pb_votes WHERE bill IS NOT NULL GROUP BY link, bill))
          WHERE rk = 1""")
    x(f"CREATE UNIQUE INDEX {W}.ix_pb_pay ON pb_pay(link)")
    _drop(conn, "pb_votes", "pb_k835", "pb_tx", "pb_ck")
    # the map
    x(f"CREATE TABLE {W}.pb_map AS SELECT * FROM main.ema_link_map WHERE 0")
    x(f"CREATE UNIQUE INDEX {W}.ix_pb_map ON pb_map(link)")
    x(f"CREATE INDEX {W}.ix_pb_map_b ON pb_map(bill)")
    cols = "link, bill, claim_id, method, votes, total, conflict, mapped_at, facility"
    if not full:
        x(f"""INSERT INTO {W}.pb_map ({cols}) SELECT {cols} FROM {W}.pb_old
              WHERE link NOT IN (SELECT link FROM {W}.pb_need) AND link NOT IN (SELECT link FROM {W}.pb_ar)""")
    x(f"""INSERT OR IGNORE INTO {W}.pb_map ({cols})
        SELECT r.link, r.bill, r.claim_id, 'ar', COALESCE(p.votes, 0), COALESCE(p.total, 0),
               CASE WHEN p.bill IS NOT NULL AND p.votes * {b} >= p.total * {a} AND p.bill <> r.bill THEN 1 ELSE 0 END, ?, r.fac
        FROM {W}.pb_ar r LEFT JOIN {W}.pb_pay p ON p.link = r.link""", (now,))
    x(f"""INSERT OR IGNORE INTO {W}.pb_map ({cols})
        SELECT p.link, p.bill, p.pcn, 'payment', p.votes, p.total, CASE WHEN p.votes * {b} >= p.total * {a} THEN 0 ELSE 1 END, ?, 0
        FROM {W}.pb_pay p WHERE p.link NOT IN (SELECT link FROM {W}.pb_ar)""", (now,))
    fac1 = _facility(conn, full=full, now=now)
    pd = _patient_day(conn, boxes, now)
    fac2 = _facility(conn, full=full, now=now)
    x(f"""UPDATE {W}.pb_map SET mapped_at = (SELECT o.mapped_at FROM {W}.pb_old o WHERE o.link = pb_map.link
              AND o.method IS pb_map.method AND o.bill IS pb_map.bill)
          WHERE EXISTS (SELECT 1 FROM {W}.pb_old o WHERE o.link = pb_map.link AND o.method IS pb_map.method AND o.bill IS pb_map.bill
                        AND o.mapped_at IS NOT NULL)""")
    by = dict(conn.execute(f"SELECT method, COUNT(*) FROM {W}.pb_map WHERE COALESCE(conflict, 0) = 0 AND bill IS NOT NULL "
                           "GROUP BY method").fetchall())
    conflicts = conn.execute(f"SELECT COUNT(*) FROM {W}.pb_map WHERE conflict = 1").fetchone()[0]
    total = conn.execute(f"SELECT COUNT(*) FROM {W}.pb_map").fetchone()[0]
    return {"links": total, "by_method": by, "conflicts": conflicts, "facility": max(fac1, fac2), "patient_day": pd}


def _facility(conn: sqlite3.Connection, *, full: bool, now: str) -> int:
    """G2: a link is the ASC's facility claim when its A/R claim id has the F shape, or when it has a twin link (same
    patient, date and code) mapped without conflict to an 835 bill, none of its own insurance checks is an 835 trace, and
    the same payer group paid both. A link whose A/R claim id is not a facility claim, or that is itself mapped to an 835
    bill, is never marked. Facility links with no row in the map get one (no bill, method 'facility')."""
    x = conn.execute
    W = _W
    if not _table_exists(conn, W, "pb_b835"):
        x(f"CREATE TABLE {W}.pb_b835 AS SELECT DISTINCT CAST(rw_bill(patient_control_number) AS TEXT) AS bill FROM main.claims "
          "WHERE patient_control_number IS NOT NULL")
        x(f"CREATE UNIQUE INDEX {W}.ix_pb_b835 ON pb_b835(bill)")
    x(f"DROP TABLE IF EXISTS {W}.pb_fac")
    x(f"""CREATE TABLE {W}.pb_fac AS SELECT CAST(link AS INTEGER) AS link FROM (SELECT link FROM {W}.pb_ar WHERE fac = 1
        UNION SELECT l.link FROM {W}.pa_full l
        JOIN {W}.pa_full t ON t.person = l.person AND t.svc = l.svc AND t.cpt = l.cpt AND t.link <> l.link
        JOIN {W}.pb_map m ON m.link = t.link AND COALESCE(m.conflict, 0) = 0 AND m.bill IN (SELECT bill FROM {W}.pb_b835)
        WHERE l.person IS NOT NULL
          AND l.link NOT IN (SELECT link FROM {W}.pb_ar WHERE fac = 0)
          AND l.link NOT IN (SELECT link FROM {W}.pb_map WHERE COALESCE(conflict, 0) = 0 AND bill IN (SELECT bill FROM {W}.pb_b835))
          AND NOT EXISTS (SELECT 1 FROM {W}.pa_pay y WHERE y.link = l.link AND y.in_835 = 1)
          AND EXISTS (SELECT 1 FROM {W}.pa_pay pa JOIN {W}.pa_pay pb ON pb.link = t.link AND pb.payer_group = pa.payer_group
                      AND COALESCE(pb.ins_paid, 0) <> 0 WHERE pa.link = l.link AND COALESCE(pa.ins_paid, 0) <> 0))""")
    x(f"CREATE UNIQUE INDEX {W}.ix_pb_fac ON pb_fac(link)")
    if not full:            # a facility mark from the last full refresh stands (the twin's payments may be pruned since)
        x(f"INSERT INTO {W}.pb_fac SELECT link FROM {W}.pb_old WHERE facility = 1 AND link NOT IN (SELECT link FROM {W}.pb_fac)")
    x(f"UPDATE {W}.pb_map SET facility = 1 WHERE link IN (SELECT link FROM {W}.pb_fac)")
    x(f"""INSERT OR IGNORE INTO {W}.pb_map (link, bill, claim_id, method, votes, total, conflict, mapped_at, facility)
        SELECT link, NULL, NULL, 'facility', 0, 0, 0, ?, 1 FROM {W}.pb_fac WHERE link NOT IN (SELECT link FROM {W}.pb_map)""", (now,))
    return conn.execute(f"SELECT COUNT(*) FROM {W}.pb_map WHERE facility = 1").fetchone()[0]


def _patient_day(conn: sqlite3.Connection, boxes: Sequence[Box], now: str) -> dict:
    """patient_day (spec 7.2.1.4 with E7): an 835 bill inside the ledger's coverage that has no link yet takes the link
    whose lines share the most (name key, date, code) with it, with the fewest extra codes, when exactly one link is on
    top. Every matched code's charge must equal the 835 line's billed charge within 2%; links already in the map, and
    facility links, are never candidates; a link on top for two bills maps to neither. Ties stay unmapped, which keeps an
    ASC twin from taking the professional bill."""
    x = conn.execute
    W = _W
    cov, args = _coverage_sql(boxes, "svc_min", "paid_max")
    if not cov:
        return {"bills": 0, "mapped": 0, "ties": 0, "charge_mismatch": 0}
    x(f"""CREATE TABLE {W}.pd_bill AS SELECT CAST(bill AS TEXT) AS bill, MIN(svc) AS svc_min, MAX(paid) AS paid_max FROM (
              SELECT rw_bill(c.patient_control_number) AS bill, COALESCE(l.service_date, c.statement_from) AS svc, c.payment_date AS paid
              FROM main.claims c JOIN main.lines l ON l.claim_key = c.claim_key WHERE c.patient_control_number IS NOT NULL)
          GROUP BY bill""")
    x(f"DELETE FROM {W}.pd_bill WHERE NOT ({cov})", args)
    x(f"DELETE FROM {W}.pd_bill WHERE bill IN (SELECT bill FROM {W}.pb_map WHERE COALESCE(conflict, 0) = 0 AND bill IS NOT NULL)")
    x(f"CREATE UNIQUE INDEX {W}.ix_pd_bill ON pd_bill(bill)")
    bills = conn.execute(f"SELECT COUNT(*) FROM {W}.pd_bill").fetchone()[0]
    x(f"""CREATE TABLE {W}.pd_l AS
        SELECT CAST(rw_bill(c.patient_control_number) AS TEXT) AS bill, c.claim_key AS ck, c.patient_control_number AS pcn,
               CAST(rw_name_key(c.patient_last, c.patient_first) AS INTEGER) AS nk,
               CAST(CASE WHEN c.corrected_patient_last IS NOT NULL OR c.corrected_patient_first IS NOT NULL
                    THEN rw_name_key(COALESCE(c.corrected_patient_last, c.patient_last),
                                     COALESCE(c.corrected_patient_first, c.patient_first)) END AS INTEGER) AS cnk,
               CAST(COALESCE(l.service_date, c.statement_from) AS TEXT) AS svc, l.procedure_code AS code,
               l.original_procedure_code AS ocode,
               l.charge_cents AS charge
        FROM main.claims c JOIN main.lines l ON l.claim_key = c.claim_key
        WHERE c.patient_control_number IS NOT NULL AND rw_bill(c.patient_control_number) IN (SELECT bill FROM {W}.pd_bill)""")
    x(f"""CREATE TABLE {W}.pd_key AS SELECT CAST(bill AS TEXT) AS bill, CAST(nk AS INTEGER) AS nk, CAST(svc AS TEXT) AS svc,
              CAST(code AS TEXT) AS code, MAX(charge) AS charge, MAX(pcn) AS pcn FROM (
              SELECT bill, ck, nk, svc, code, SUM(charge) AS charge, MAX(pcn) AS pcn FROM (
                  SELECT bill, ck, pcn, nk, svc, code, charge FROM {W}.pd_l WHERE nk IS NOT NULL AND code IS NOT NULL
                  UNION ALL SELECT bill, ck, pcn, nk, svc, ocode, charge FROM {W}.pd_l
                            WHERE nk IS NOT NULL AND ocode IS NOT NULL AND ocode <> COALESCE(code, '')
                  UNION ALL SELECT bill, ck, pcn, cnk, svc, code, charge FROM {W}.pd_l
                            WHERE cnk IS NOT NULL AND cnk <> COALESCE(nk, 0) AND code IS NOT NULL
                  UNION ALL SELECT bill, ck, pcn, cnk, svc, ocode, charge FROM {W}.pd_l
                            WHERE cnk IS NOT NULL AND cnk <> COALESCE(nk, 0) AND ocode IS NOT NULL AND ocode <> COALESCE(code, ''))
              GROUP BY bill, ck, nk, svc, code)
          GROUP BY bill, nk, svc, code""")
    x(f"CREATE INDEX {W}.ix_pd_key ON pd_key(nk, svc, code)")
    x(f"CREATE INDEX {W}.ix_pd_key_b ON pd_key(bill)")         # the winners' claim id and code count, per bill
    x(f"""CREATE TABLE {W}.pd_cl AS SELECT l.link AS link, l.svc AS svc, l.cpt AS cpt, l.name_key AS name_key, l.charges AS charges
          FROM {W}.pa_full l WHERE l.name_key IS NOT NULL AND l.link NOT IN (SELECT link FROM {W}.pb_map)""")
    x(f"CREATE INDEX {W}.ix_pd_cl ON pd_cl(name_key, svc, cpt)")
    x(f"CREATE TABLE {W}.pd_lk AS SELECT link, COUNT(*) AS keys FROM {W}.pa_full WHERE link IN (SELECT link FROM {W}.pd_cl) GROUP BY link")
    x(f"CREATE UNIQUE INDEX {W}.ix_pd_lk ON pd_lk(link)")
    x(f"""CREATE TABLE {W}.pd_pair AS
        SELECT k.bill AS bill, e.link AS link, COUNT(DISTINCT k.svc || '|' || k.code) AS shared,
               SUM(CASE WHEN COALESCE(k.charge, 0) = 0 AND COALESCE(e.charges, 0) = 0 THEN 0
                        WHEN COALESCE(k.charge, 0) <> 0 AND abs(COALESCE(e.charges, 0) - k.charge) <= ? * abs(k.charge) THEN 0
                        ELSE 1 END) AS bad
        FROM {W}.pd_key k JOIN {W}.pd_cl e ON e.name_key = k.nk AND e.svc = k.svc AND e.cpt = k.code
        GROUP BY k.bill, e.link""", (CHARGE_TOLERANCE,))
    x(f"""CREATE TABLE {W}.pd_win AS SELECT CAST(bill AS TEXT) AS bill, CAST(link AS INTEGER) AS link, shared, ties FROM (
              SELECT p.bill AS bill, p.link AS link, p.shared AS shared,
                     RANK() OVER (PARTITION BY p.bill ORDER BY p.shared DESC, lk.keys - p.shared ASC) AS rk,
                     COUNT(*) OVER (PARTITION BY p.bill, p.shared, lk.keys - p.shared) AS ties
              FROM {W}.pd_pair p JOIN {W}.pd_lk lk ON lk.link = p.link WHERE p.bad = 0)
          WHERE rk = 1""")
    ties = conn.execute(f"SELECT COUNT(DISTINCT bill) FROM {W}.pd_win WHERE ties > 1").fetchone()[0]
    mismatch = conn.execute(f"""SELECT COUNT(*) FROM (SELECT bill FROM {W}.pd_pair GROUP BY bill HAVING MIN(bad) > 0)""").fetchone()[0]
    x(f"DELETE FROM {W}.pd_win WHERE ties > 1")
    x(f"DELETE FROM {W}.pd_win WHERE link IN (SELECT link FROM {W}.pd_win GROUP BY link HAVING COUNT(*) > 1)")
    n = x(f"""INSERT OR IGNORE INTO {W}.pb_map (link, bill, claim_id, method, votes, total, conflict, mapped_at, facility)
        SELECT w.link, w.bill, (SELECT MAX(k.pcn) FROM {W}.pd_key k WHERE k.bill = w.bill), 'patient_day', w.shared,
               (SELECT COUNT(DISTINCT k.svc || '|' || k.code) FROM {W}.pd_key k WHERE k.bill = w.bill), 0, ?, 0
        FROM {W}.pd_win w""", (now,)).rowcount
    _drop(conn, "pd_bill", "pd_l", "pd_key", "pd_cl", "pd_lk", "pd_pair", "pd_win")
    return {"bills": bills, "mapped": n, "ties": ties, "charge_mismatch": mismatch}


def _map_lines(conn: sqlite3.Connection) -> None:
    """Copy each line's bill, claim id, method and facility mark from the map (a conflict maps to no bill)."""
    W = _W
    conn.execute(f"""UPDATE {W}.pa_full SET
            bill = (SELECT m.bill FROM {W}.pb_map m WHERE m.link = pa_full.link AND COALESCE(m.conflict, 0) = 0),
            claim_id = COALESCE((SELECT r.claim_id FROM {W}.pb_ar r WHERE r.link = pa_full.link),
                                (SELECT m.claim_id FROM {W}.pb_map m WHERE m.link = pa_full.link AND COALESCE(m.conflict, 0) = 0)),
            map_method = (SELECT CASE WHEN COALESCE(m.conflict, 0) = 1 THEN 'conflict' ELSE m.method END
                          FROM {W}.pb_map m WHERE m.link = pa_full.link),
            facility = COALESCE((SELECT m.facility FROM {W}.pb_map m WHERE m.link = pa_full.link), 0)""")


def _keep(conn: sqlite3.Connection) -> dict:
    """Evaluate the lean keep rule on every line in the work database; `keep` lists the rules a line meets."""
    W = _W
    fb = _finding_bills_sql(conn)
    if fb:
        conn.execute(f"CREATE TABLE {W}.pb_fbills AS SELECT DISTINCT CAST(bill AS TEXT) AS bill FROM ({fb}) WHERE bill IS NOT NULL")
        conn.execute(f"CREATE UNIQUE INDEX {W}.ix_pb_fbills ON pb_fbills(bill)")
    conds = _keep_conditions(f"{W}.pa_full", f"{W}.pa_pay", f"{W}.pb_map", "main.ema_claim_links",
                             f"SELECT bill FROM {W}.pb_fbills" if fb else None)
    expr = " || ".join(f"CASE WHEN {c} THEN '{n},' ELSE '' END" for n, c in conds)
    conn.execute(f"UPDATE {W}.pa_full SET keep = rtrim({expr}, ',')")
    total, kept = conn.execute(f"SELECT COUNT(*), SUM(keep <> '') FROM {W}.pa_full").fetchone()
    only: dict[str, int] = {}
    each: dict[str, int] = {}
    for k, n in conn.execute(f"SELECT keep, COUNT(*) FROM {W}.pa_full WHERE keep <> '' GROUP BY keep"):
        parts = k.split(",")
        for p in parts:
            each[p] = each.get(p, 0) + n
        if len(parts) == 1:
            only[parts[0]] = only.get(parts[0], 0) + n
    return {"lines": total or 0, "kept": kept or 0, "by_rule": each, "by_rule_alone": only}


def _map_coverage(conn: sqlite3.Connection, boxes: Sequence[Box]) -> dict:
    """Of the 835 bills inside the ledger's coverage, how many have a link (no conflict), and by which method."""
    W = _W
    cov, args = _coverage_sql(boxes, "svc_min", "paid_max")
    if not cov:
        return {"bills": 0, "mapped": 0, "share": None, "by_method": {}}
    conn.execute(f"DROP TABLE IF EXISTS {W}.mc_bill")
    conn.execute(f"""CREATE TABLE {W}.mc_bill AS SELECT CAST(bill AS TEXT) AS bill, MIN(svc) AS svc_min, MAX(paid) AS paid_max FROM (
              SELECT rw_bill(c.patient_control_number) AS bill, COALESCE(l.service_date, c.statement_from) AS svc, c.payment_date AS paid
              FROM main.claims c JOIN main.lines l ON l.claim_key = c.claim_key WHERE c.patient_control_number IS NOT NULL)
          GROUP BY bill""")
    conn.execute(f"DELETE FROM {W}.mc_bill WHERE NOT ({cov})", args)
    bills = conn.execute(f"SELECT COUNT(*) FROM {W}.mc_bill").fetchone()[0]
    rank = "CASE m.method WHEN 'ar' THEN 1 WHEN 'payment' THEN 2 WHEN 'patient_day' THEN 3 ELSE 4 END"
    by: dict[str, int] = {}
    for method, n in conn.execute(f"""SELECT CASE best WHEN 1 THEN 'ar' WHEN 2 THEN 'payment' WHEN 3 THEN 'patient_day' ELSE 'other' END, COUNT(*)
            FROM (SELECT b.bill, MIN({rank}) AS best FROM {W}.mc_bill b JOIN {W}.pb_map m ON m.bill = b.bill
                  AND COALESCE(m.conflict, 0) = 0 GROUP BY b.bill) GROUP BY 1"""):
        by[method] = n
    mapped = sum(by.values())
    _drop(conn, "mc_bill")
    return {"bills": bills, "mapped": mapped, "share": round(mapped / bills, 4) if bills else None, "by_method": by}


def _part_b(conn: sqlite3.Connection, *, full: bool, boxes: Sequence[Box], log: Any) -> dict:
    t0 = time.time()
    n_tr = _traces(conn)
    marks = _marks(conn)
    mp = _map(conn, full=full, boxes=boxes)
    _map_lines(conn)
    keep = _keep(conn)
    cov = _map_coverage(conn, boxes)
    conn.commit()               # only the work database changed so far
    return {"traces": n_tr, "checks": marks, "map": mp, "keep": keep, "coverage": cov, "seconds": round(time.time() - t0, 1)}


# ---------------------------------------------------------------------------------------------------------- publishing
def _publish(conn: sqlite3.Connection, *, part_a: bool, pruned: bool, meta: dict, cutoff: str | None) -> dict:
    """Write the work database's tables into the store in one transaction."""
    W = _W
    x = conn.execute
    keep = "(keep IS NULL OR keep <> '')" if not pruned else "keep <> ''"
    lkeep = keep.replace("keep", "l.keep")

    def cols(t: str) -> str:
        return ", ".join(_columns(conn, "main", t))

    try:
        x("DELETE FROM main.ema_lines")
        n_lines = x(f"INSERT INTO main.ema_lines ({cols('ema_lines')}) SELECT {cols('ema_lines')} FROM {W}.pa_full WHERE {keep}").rowcount
        x("DELETE FROM main.ema_line_payments")
        pc = cols("ema_line_payments")
        n_pay = x(f"""INSERT INTO main.ema_line_payments ({pc}) SELECT {', '.join('y.' + c for c in pc.split(', '))} FROM {W}.pa_pay y
                      WHERE EXISTS (SELECT 1 FROM {W}.pa_full l WHERE l.link = y.link AND l.svc = y.svc AND l.cpt = y.cpt AND {lkeep})""").rowcount
        x("DELETE FROM main.ema_line_reasons")
        rc = cols("ema_line_reasons")
        routine = ",".join(f"'{c}'" for c in ("routine", "patient", "none"))
        n_rsn = x(f"""INSERT INTO main.ema_line_reasons ({rc}) SELECT {', '.join('r.' + c for c in rc.split(', '))} FROM {W}.pa_rsn r
                      JOIN {W}.pa_full l ON l.link = r.link AND l.svc = r.svc AND l.cpt = r.cpt
                      WHERE {lkeep} AND (COALESCE(r.reason_class, 'other') NOT IN ({routine}) OR l.keep IS NULL
                                         OR instr(',' || l.keep || ',', ',finding,') > 0)""").rowcount
        if part_a:
            # every reason, routine ones too, of the lines whose bill has a finding (Part A read them from the ledger)
            fk = "(l.keep IS NULL OR instr(',' || l.keep || ',', ',finding,') > 0)"
            n_rsn += x(f"""INSERT OR IGNORE INTO main.ema_line_reasons ({rc}) SELECT {', '.join('r.' + c for c in rc.split(', '))}
                           FROM {W}.pa_rsr r JOIN {W}.pa_full l ON l.link = r.link AND l.svc = r.svc AND l.cpt = r.cpt
                           WHERE {fk}""").rowcount
        x("DELETE FROM main.ema_checks")
        cc = cols("ema_checks")
        n_chk = x(f"INSERT INTO main.ema_checks ({cc}) SELECT {cc} FROM {W}.pa_chk").rowcount
        if _table_exists(conn, W, "pb_map"):
            x("DELETE FROM main.ema_link_map")
            mc = cols("ema_link_map")
            x(f"INSERT INTO main.ema_link_map ({mc}) SELECT {mc} FROM {W}.pb_map")
            x("DELETE FROM meta WHERE key=?", (META_MAP_DIRTY,))
        if part_a:
            x("DELETE FROM main.ema_months WHERE measure <> 'no835'")
            mo = cols("ema_months")
            x(f"INSERT INTO main.ema_months ({mo}) SELECT {mo} FROM {W}.pa_mon")
            x(f"INSERT OR IGNORE INTO main.ema_link_events (link, kind, value, first_seen) SELECT link, kind, value, first_seen FROM {W}.pa_ev")
            if cutoff:
                x(f"DELETE FROM main.ema_link_events WHERE first_seen < ? AND link NOT IN (SELECT link FROM {W}.pa_lk)", (cutoff,))
            x("DELETE FROM meta WHERE key=?", (META_KEEP_DIRTY,))
        for k, v in meta.items():
            if v is None:
                x("DELETE FROM meta WHERE key=?", (k,))
            else:
                _meta_set(conn, k, v)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return {"lines": n_lines, "payments": n_pay, "reasons": n_rsn, "checks": n_chk}


# ------------------------------------------------------------------------------------------------------------ refresh
def _catalog_boxes(conn: sqlite3.Connection) -> list[Box]:
    out = []
    for b in er.coverage(conn).get("production") or []:
        out.append(Box(b["file_id"], b.get("scope") or "service", b.get("svc_lo") or "", b.get("svc_hi") or "",
                       b.get("posted_lo") or "", b.get("posted_hi") or "", b.get("as_of") or "", (b.get("as_of") or "", "", "", b["file_id"])))
    return out


def _ledger_changed(conn: sqlite3.Connection, ledger: Ledger) -> bool:
    seq = _meta_get(conn, META_SEQ)
    if seq is None or str(ledger.seq()) != seq:
        return True
    if ledger.rev is not None and ledger.rev != _meta_get(conn, META_REV):
        return True
    return _meta_get(conn, META_KEEP_DIRTY) is not None


def refresh(store: Any, ledger: Ledger | None = None, *, parts: str = "auto", log: Any = print,
            release: Any = None) -> dict:
    """Bring the store's EMA summaries up to date.

    parts: 'auto' runs Part A when a ledger is given and it changed since the store last read it (its seq, or the state
    folder revision on a serverless host, differs from meta ema:ledger_seq / ema:ledger_rev; retention deleted rows; or
    meta ema:keep_dirty is set), and Part B after Part A or when meta ema:map_dirty is set. 'A', 'B' and 'AB' run those
    parts. Part A needs the ledger; Part B reads only the store. With a ledger, retention runs first and the catalog is
    brought in line with the ledger (sync_catalog). The store changes in one transaction at the end.

    `release` (serverless): called with no argument as soon as the ledger is no longer needed (after Part A, before Part
    B), to save the scratch ledger to the state folder and delete it, so the temporary folder never holds the ledger and
    the larger half of the work at once (LedgerHandle.save). What it returns, the state folder's new revision, is what
    meta ema:ledger_rev records."""
    conn = store.conn
    er.ensure_schema(conn)
    salt = er.key_salt(conn)
    _register(conn, salt)
    t0 = time.time()
    out: dict = {"part_a": None, "part_b": None, "retained": 0, "catalog": 0, "retain_held": None, "released": False}
    if parts not in ("auto", "A", "B", "AB"):
        raise ValueError("parts is one of auto, A, B, AB")
    if ledger is not None and not ledger.readonly:
        bind = ledger.bind_salt(er.salt_fingerprint(salt))
        if bind:
            log(f"  EMA posting ledger: emptied (keyed for another store); {len(bind)} file(s) to read again")
        out["retained"] = ledger.retain()
        held = ledger.retain_held()
        out["retain_held"] = held
        if held:
            log(f"  EMA posting ledger: retention waits for a person: it would delete {held['rows']:,} of {held['of']:,} "
                f"postings at once (service before {held['cutoff']})")
        if bool(held) != (_meta_get(conn, META_RETAIN_HELD) is not None):
            if held:
                _meta_set(conn, META_RETAIN_HELD, json.dumps(held, sort_keys=True))
            else:
                conn.execute("DELETE FROM meta WHERE key=?", (META_RETAIN_HELD,))
            conn.commit()
        out["catalog"] = sync_catalog(store, ledger)
    want_a = parts in ("A", "AB") or (parts == "auto" and ledger is not None and _ledger_changed(conn, ledger))
    if want_a and ledger is None:
        raise ValueError("Part A needs the posting ledger.")
    want_b = parts in ("B", "AB") or (parts == "auto" and (want_a or _meta_get(conn, META_MAP_DIRTY) is not None))
    if not (want_a or want_b):
        return out
    boxes = ledger.boxes() if ledger is not None else _catalog_boxes(conn)
    where = os.path.dirname(os.path.abspath(ledger.path)) if ledger is not None and ledger.path != ":memory:" else None
    meta: dict = {}
    cutoff = None

    def let_go() -> None:
        if release is not None and not out["released"]:
            rev = release()
            out["released"] = True
            if rev:
                meta[META_REV] = rev
    with _Work(conn, where) as w:
        if want_a:
            w.attach_ledger(ledger)
            out["part_a"] = _part_a(conn, log)
            newest = conn.execute(f"SELECT MAX(posted) FROM {_L}.postings").fetchone()[0]
            if newest:
                top = min(newest[:10], er.today_iso())
                cutoff = (date.fromisoformat(top) - timedelta(days=retain_days())).isoformat()
            meta[META_SEQ] = ledger.seq()
            if ledger.rev is not None:
                meta[META_REV] = ledger.rev
            w.detach_ledger()
            let_go()
        else:
            let_go()
            _copy_store(conn)
        if want_b:
            out["part_b"] = _part_b(conn, full=want_a, boxes=boxes, log=log)
            cov = dict(out["part_b"]["coverage"], links=out["part_b"]["map"]["links"],
                       conflicts=out["part_b"]["map"]["conflicts"], facility=out["part_b"]["map"]["facility"],
                       patient_day=out["part_b"]["map"]["patient_day"], at=_now())
            meta[META_MAP_COVERAGE] = json.dumps(cov, sort_keys=True)
        out["published"] = _publish(conn, part_a=want_a, pruned=want_b, meta=meta, cutoff=cutoff)
    out["seconds"] = round(time.time() - t0, 1)
    pb = out.get("part_b") or {}
    if pb:
        cov = pb.get("coverage") or {}
        share = cov.get("share")
        log(f"  EMA summaries: {out['published']['lines']:,} claim lines kept"
            + (f" of {pb['keep']['lines']:,}" if want_a else "")
            + (f"; {cov.get('mapped', 0):,} of {cov.get('bills', 0):,} remittance bills mapped ({share:.1%})" if share is not None else "")
            + f" ({out['seconds']} s)")
    return out


def map_links(conn: sqlite3.Connection) -> dict:
    """Part B's map alone, from the store's own tables (as after 835s or an A/R arrived): links with no bill are mapped
    again and the rest are kept; the in-835 marks are recomputed on the way. Writes ema_link_map and the lines' map
    columns; prunes nothing. Returns counts by method, conflicts and coverage."""
    er.ensure_schema(conn)
    _register(conn)
    boxes = _catalog_boxes(conn)
    with _Work(conn, None):
        _copy_store(conn)
        _traces(conn)
        _marks(conn)
        mp = _map(conn, full=False, boxes=boxes)
        _map_lines(conn)
        cov = _map_coverage(conn, boxes)
        try:
            conn.execute("DELETE FROM main.ema_link_map")
            mc = ", ".join(_columns(conn, "main", "ema_link_map"))
            conn.execute(f"INSERT INTO main.ema_link_map ({mc}) SELECT {mc} FROM {_W}.pb_map")
            conn.execute(f"""UPDATE main.ema_lines SET
                    bill = (SELECT l.bill FROM {_W}.pa_full l WHERE l.link = ema_lines.link AND l.svc = ema_lines.svc AND l.cpt = ema_lines.cpt),
                    claim_id = (SELECT l.claim_id FROM {_W}.pa_full l WHERE l.link = ema_lines.link AND l.svc = ema_lines.svc AND l.cpt = ema_lines.cpt),
                    map_method = (SELECT l.map_method FROM {_W}.pa_full l WHERE l.link = ema_lines.link AND l.svc = ema_lines.svc AND l.cpt = ema_lines.cpt),
                    facility = (SELECT l.facility FROM {_W}.pa_full l WHERE l.link = ema_lines.link AND l.svc = ema_lines.svc AND l.cpt = ema_lines.cpt)""")
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    return dict(mp, coverage=cov)


def mark_facility(conn: sqlite3.Connection) -> int:
    """G2 on the store as it stands: mark the ASC's facility links in ema_link_map and ema_lines. Returns how many links
    are marked."""
    er.ensure_schema(conn)
    _register(conn)
    with _Work(conn, None):
        _copy_store(conn)
        _traces(conn)
        _marks(conn)
        conn.execute(f"CREATE TABLE {_W}.pb_old AS SELECT * FROM main.ema_link_map")
        conn.execute(f"CREATE TABLE {_W}.pb_map AS SELECT * FROM main.ema_link_map")
        conn.execute(f"CREATE UNIQUE INDEX {_W}.ix_pb_map ON pb_map(link)")
        conn.execute(f"""CREATE TABLE {_W}.pb_ar AS SELECT CAST(link AS INTEGER) AS link, CAST(claim_id AS TEXT) AS claim_id,
                  CAST(rw_bill(claim_id) AS TEXT) AS bill, CAST(rw_is_facility(claim_id) AS INTEGER) AS fac FROM (
                  SELECT link, MAX(COALESCE(last_seen, '') || '|' || claim_id) AS k, claim_id FROM main.ema_claim_links
                  WHERE link IS NOT NULL AND claim_id IS NOT NULL GROUP BY link)""")
        n = _facility(conn, full=False, now=_now())
        try:
            conn.execute("DELETE FROM main.ema_link_map")
            mc = ", ".join(_columns(conn, "main", "ema_link_map"))
            conn.execute(f"INSERT INTO main.ema_link_map ({mc}) SELECT {mc} FROM {_W}.pb_map")
            conn.execute("UPDATE main.ema_lines SET facility = COALESCE((SELECT m.facility FROM main.ema_link_map m "
                         "WHERE m.link = ema_lines.link), 0)")
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    return n


# ------------------------------------------------------------------------------------------------- the claim view
def ledger_postings(ledger: Ledger, links: Sequence[int], *, limit: int = 500) -> list[dict]:
    """The postings of the given claim numbers, newest first (at most `limit`), for the claim view on the server. No key
    columns: no person or name key."""
    ids = sorted({int(v) for v in links if v is not None})[:500]
    if not ids:
        return []
    q = f"""SELECT p.posted, p.svc, p.cpt, p.units, p.link, p.check_no, p.grp, p.code, k.name AS kind, s.name AS status,
                   a.name AS action, py.name AS payer, pl.name AS plan, pp.name AS primary_payer, sp.name AS secondary_payer,
                   p.pccn, p.charges, p.ins_charges, p.ins_paid, p.pat_paid, p.contractual, p.noncontractual, p.pat_adj,
                   p.allowed, p.flags, f.as_of
            FROM postings p
            LEFT JOIN names k ON k.id = p.kind LEFT JOIN names s ON s.id = p.status LEFT JOIN names a ON a.id = p.action
            LEFT JOIN names py ON py.id = p.payer LEFT JOIN names pl ON pl.id = p.plan
            LEFT JOIN names pp ON pp.id = p.primary_payer LEFT JOIN names sp ON sp.id = p.secondary_payer
            LEFT JOIN ledger_files f ON f.src = p.src
            WHERE p.link IN ({','.join('?' * len(ids))}) ORDER BY p.posted DESC, p.rowid DESC LIMIT ?"""
    cur = ledger.conn.execute(q, ids + [int(limit)])
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


# ----------------------------------------------------------------------------------------------- the serverless handle
class LedgerHandle:
    """The ledger of a serverless host, kept gzipped in the state folder (STATE_NAME). Only a run that applies a
    Production file opens it: open() unpacks it to a scratch copy in the temporary folder (or starts an empty one),
    save() gzips the copy at level 1 back to the state folder and returns the new revision, discard() throws the copy
    away (a failed run: the state folder's copy is untouched). note_attempt() records, in the state folder and before a
    read starts, that a file's read began, so a run the platform kills still counts its attempt."""

    def __init__(self, state: Any, tmp_dir: str | None = None):
        self.state = state
        self.dir = tmp_dir or runtime.tmp_dir()
        os.makedirs(self.dir, exist_ok=True)
        self.path = os.path.join(self.dir, LEDGER_FILE)
        self.ledger: Ledger | None = None
        self.rev: str | None = None

    def state_rev(self) -> str | None:
        """The revision of the ledger in the state folder (None when there is none yet)."""
        m = self.state.meta(STATE_NAME)
        return (m or {}).get("rev") if m else None

    def behind(self, conn: sqlite3.Connection) -> bool:
        """Whether the store last read another revision of the ledger than the state folder holds: a run saved the
        ledger and died before saving the store. The next run that opens the ledger runs Part A again (refresh)."""
        rev = self.state_rev()
        return rev is not None and rev != _meta_get(conn, META_REV)

    def open(self) -> Ledger:
        if self.ledger is not None:
            return self.ledger
        _remove(self.path, self.path + "-stage")
        chunks = self.state.get_chunks(STATE_NAME)
        self.rev = None
        if chunks is not None:
            meta = self.state.meta(STATE_NAME) or {}
            d = zlib.decompressobj(31)
            part = self.path + ".part"
            try:
                with open(part, "wb") as fh:
                    for c in chunks:
                        fh.write(d.decompress(c))
                    fh.write(d.flush())
                os.replace(part, self.path)
            except BaseException:
                _remove(part)
                raise
            self.rev = meta.get("rev")
        self.ledger = Ledger.open(self.path, scratch=True)
        self.ledger.rev = self.rev
        return self.ledger

    def save(self, conn: sqlite3.Connection | None = None) -> str | None:
        """Gzip the scratch ledger (level 1) to the state folder, delete the scratch copy and return the new revision.
        With the store's connection, the revision is also written to meta ema:ledger_rev (not committed: the caller saves
        the store next)."""
        if self.ledger is None:
            raise RuntimeError("The ledger is not open.")
        self.ledger.drop_stage()
        self.ledger.close()
        self.ledger = None

        def gz() -> Any:
            co = zlib.compressobj(1, zlib.DEFLATED, 31)
            with open(self.path, "rb") as fh:
                while True:
                    b = fh.read(4 << 20)
                    if not b:
                        break
                    out = co.compress(b)
                    if out:
                        yield out
            yield co.flush()

        meta = self.state.put_chunks(STATE_NAME, gz()) or {}
        self.rev = meta.get("rev") or self.state_rev()
        if conn is not None and self.rev is not None:
            _meta_set(conn, META_REV, self.rev)
        self.discard()
        return self.rev

    def discard(self) -> None:
        """Close and delete the scratch copy (and any staging file) without saving."""
        if self.ledger is not None:
            self.ledger.close()
            self.ledger = None
        _remove(self.path, self.path + "-stage", self.path + ".part")

    # ---------------------------------------------------------------- attempts (E12)
    def _attempts(self) -> dict:
        try:
            raw = self.state.get(ATTEMPTS_NAME)
            d = json.loads(raw) if raw else {}
            return d if isinstance(d, dict) else {}
        except (ValueError, TypeError):
            return {}

    def attempts(self, file_id: str) -> int:
        e = self._attempts().get(file_id) or {}
        return int(e.get("n") or 0)

    def note_attempt(self, file_id: str) -> int:
        """Count one more attempt to read this file, in the state folder, before the read starts. Returns the count (from
        1). The caller holds the file when it exceeds MAX_ATTEMPTS."""
        d = self._attempts()
        e = d.get(file_id) or {}
        e = {"n": int(e.get("n") or 0) + 1, "at": _now()}
        d[file_id] = e
        self.state.put(ATTEMPTS_NAME, json.dumps(d, sort_keys=True).encode())
        return e["n"]

    def clear_attempt(self, file_id: str) -> None:
        d = self._attempts()
        if file_id in d:
            d.pop(file_id)
            if d:
                self.state.put(ATTEMPTS_NAME, json.dumps(d, sort_keys=True).encode())
            else:
                try:
                    self.state.delete(ATTEMPTS_NAME)
                except Exception:       # noqa: BLE001  (an attempts file left behind only counts an old attempt)
                    self.state.put(ATTEMPTS_NAME, b"{}")


ATTEMPT_HOLD_REASON = ("Too slow to read on this site: zip the CSV (about 12-15 MB) and put the zip in the same folder, or let "
                       "the server read it.")


def hold_slow_file(store: Any, file_id: str, reason: str = ATTEMPT_HOLD_REASON) -> bool:
    """Hold a Production file whose read was started MAX_ATTEMPTS times on a serverless host without finishing (the run
    was stopped each time), so it is not tried forever. A person can press Apply (another try) or Discard. Only a
    waiting file is held; returns whether it was."""
    conn = store.conn
    er.ensure_schema(conn)
    n = conn.execute("UPDATE ema_report_files SET status='held', status_reason=? WHERE file_id=? AND status IN ('waiting','reread')",
                     (reason, file_id)).rowcount
    conn.commit()
    if n:
        store.ema_changed = True
    return bool(n)
