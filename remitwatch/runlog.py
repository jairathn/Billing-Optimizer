"""
Audit logs for every run, on both hosts: the run manifest, the structured event log, the decision log of automatic
changes to items, the one scrubber every writer goes through, and where the logs live and for how long.

    rl = runlog.start_run("cycle", trigger="cron", conn=store.conn, state=state)    # serverless; db_path=... on a server
    with rl.step("ingest", inputs={"files": 3}):           # wall time, memory, /tmp; an exception is logged with its
        ...                                                # stack and the step's inputs (ids only), then raised again
    rl.event("detect", "items_raised", counts={"raised": 12})
    log = rl.log                                           # in place of print for the existing code's progress lines
    except Exception as exc: runlog.caught(exc, "trizetto")   # every exception a step contains, with its stack
    print(rl.finish(conn=store.conn, out=out))             # the one line Vercel's own function log keeps

    before = runlog.snapshot_items(conn)                   # the decision log: before and after a step that changes
    ...                                                    # items (detection's save, the archive, a hide rule)
    runlog.record_decisions(conn, run_id, before)          # one row per automatic change, with its rule and evidence

Where the logs live (a sink):
  server       <the store's folder>/logs (/data/logs on AWS): events-YYYY-MM-DD.jsonl written line by line while the
               run goes (a killed run leaves its trail), runs-YYYY-MM-DD.jsonl (one manifest per line), both gzipped
               once the day is over and deleted after SERVER_KEEP_DAYS; index.json (the last runs, for the Data tab),
               open/<run_id>.json (a run in progress), repair_state.json (remitwatch.repair).
  serverless   the state folder's logs/: runs/YYYY-MM-DD/<run_id>.jsonl.gz (the manifest, then the events: one file
               per run, written when it ends), open/<run_id>.json (written at the start and at the big steps, so a
               run the platform kills is found by the next one), index.json, repair_state.json. Pruned by the run
               itself once a day after SERVERLESS_KEEP_DAYS.
  Both keep a size budget: past it the oldest files go first and the drop is logged. Vercel's own function log gets
  one line per run (run id and outcome), never detail. Logs are never in git (data/ is ignored).
The store keeps a short copy of each manifest (table run_log, for the Data tab's panel) and the decision log
(table item_decisions, read by the item drawer's "Why did this change?").

Privacy: nothing that names or identifies a patient goes into a log line, an error message, a manifest or a bundle:
no name, birth date, record number, member id, claim id, check number, note text or cell text. Every writer goes
through Scrubber (scrub, scrub_text): quoted values, dates, long numbers, ids that mix letters and digits, e-mail
addresses, phone numbers, links, file paths and file names are masked, and so are the patient names the store holds
(learn). What stays: finding ids, the store's own keys (line, claim and transaction keys made from file hashes),
payer groups, codes, counts and dollars. The decision log keeps no free text but the scrubbed basis of a state.

The clock is read in one place (utcnow; set_clock drives it in tests) and the environment in one place (_env).
"""
from __future__ import annotations

import ast
import contextlib
import contextvars
import gzip
import hashlib
import io
import json
import os
import platform
import re
import secrets
import shutil
import sqlite3
import sys
import tempfile
import time
import traceback
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterable

SCHEMA = 1
EVENT_KEYS = ("ts", "run_id", "step", "level", "event", "rule", "rule_version", "item", "counts", "ms", "mem_mb", "detail", "error")
MAX_EVENTS = 5000               # events kept per run; past it only errors and warnings are kept and the rest counted
MAX_ERRORS = 200                # errors kept per run (each with its stack)
MAX_INPUTS = 2000               # input files listed one by one in a manifest; past it they are counted by kind and result
STACK_FRAMES = 40
TEXT_CAP = 600                  # characters kept of one free-text value (after scrubbing)
LIST_CAP = 100                  # items kept of one list inside an event or a manifest section
SERVER_KEEP_DAYS = 180
SERVERLESS_KEEP_DAYS = 90
SERVER_BUDGET_BYTES = 512 << 20
SERVERLESS_BUDGET_BYTES = 100 << 20
INDEX_KEEP = 200                # runs in index.json
STORE_RUNS_KEEP_DAYS = 200      # run_log rows in the store
STORE_RUNS_MAX = 2000
DECISIONS_KEEP_DAYS = 400       # item_decisions rows: an appeal window and more
DECISIONS_ORPHAN_DAYS = 90      # rows of items no longer in the store
DECISIONS_MAX_ROWS = 500_000
VALUE_MIN_CENTS = 500           # a change of an item's dollars is logged from $5 or 10% (smaller drifts are not decisions)
VALUE_MIN_SHARE = 0.10
FOLLOW_UP_MIN_DAYS = 3          # a follow-up day that moves less than this is not logged on its own
CHECKPOINT_SECONDS_REMOTE = 20  # serverless: the open marker is rewritten at most this often (each write is a Dropbox call)
SERVER_STALE_SECONDS = 3 * 3600 + 600       # an open marker this old on a server was left by a killed run
SERVERLESS_STALE_SECONDS = 420              # the same on a serverless host (cloud.lock_stale_seconds)

# ----------------------------------------------------------------------------------------------------- clock, environment
_CLOCK: Any = None


def set_clock(value: Any) -> None:
    """Drive the clock in tests: a datetime (aware), a callable returning one, or None for the real clock."""
    global _CLOCK
    _CLOCK = value


def utcnow() -> datetime:
    """The one place this module reads the clock."""
    if _CLOCK is not None:
        v = _CLOCK() if callable(_CLOCK) else _CLOCK
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None = None) -> str:
    return (dt or utcnow()).astimezone(timezone.utc).isoformat(timespec="seconds")


def _env(env: dict | None = None) -> dict:
    """The one place this module reads the environment."""
    return os.environ if env is None else env


def host_mode(env: dict | None = None) -> str:
    e = _env(env)
    mode = (e.get("REMITWATCH_MODE") or "").lower()
    if mode in ("server", "serverless"):
        return mode
    return "serverless" if e.get("VERCEL") else "server"


def new_run_id(now: datetime | None = None) -> str:
    """run-YYYYMMDDTHHMMSSZ-xxxx: the detection run's own id format, with a suffix so two runs in one second differ."""
    return (now or utcnow()).strftime("run-%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(2)


# ---------------------------------------------------------------------------------------------------------- the scrubber
_RUN_ID = r"run-\d{8}T\d{6}Z(?:-[0-9a-f]{4,8})?"
_KEY = r"[0-9a-f]{16}(?::[0-9A-Za-z]{1,12}){1,4}"
_HEX = r"(?=[0-9a-f]*[a-f])[0-9a-f]{7,64}"
_PROTECT = re.compile(rf"\b(?:{_RUN_ID}|{_KEY}|{_HEX})\b")
_ISO_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?$")
_URL = re.compile(r"\bhttps?://[^\s'\"<>]+", re.I)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_QUOTED = re.compile(r"(['\"])(.*?)\1", re.S)
_LABELED = re.compile(r"(?i)\b(patient(?:'s)?(?:\s+name)?|name|first\s+name|last\s+name|member(?:\s+id)?|subscriber(?:\s+id)?|"
                      r"insured(?:\s+id)?|dob|date\s+of\s+birth|birth\s*date|mrn|medical\s+record(?:\s+number)?|ssn|"
                      r"policy(?:\s+number)?|account(?:\s+number)?|claim\s+(?:id|number)|check(?:\s+number)?)\s*[:=#]\s*"
                      r"([^\s,;]+(?:[ \t]+[^\s,;:=]+)?)")
_PATIENT_WORDS = re.compile(r"\b([Pp]atient(?:'s)?)\s+([A-Z][A-Za-z'\-]+(?:\s+[A-Z][A-Za-z'\-]+)?)")
_EXT = r"(?:pdf|csv|tsv|txt|xlsx|xlsm|xls|zip|gz|json|jsonl|sqlite|rmt|835|837|edi|x12|docx|doc|png|jpe?g|tiff?|html?|md|part)"
_PATH = re.compile(r"(?:[A-Za-z]:)?(?:[\w.~-]*[\\/])+[\w.~-]*")
_FILENAME = re.compile(rf"\b[\w.~-]+\.({_EXT})\b", re.I)
_MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_DATES = [re.compile(r"\b\d{4}[-/.]\d{1,2}[-/.]\d{1,2}(?:[T ]\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?"),
          re.compile(r"\b\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}\b"),
          re.compile(rf"(?i)\b{_MONTHS}\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{2,4}}\b"),
          re.compile(rf"(?i)\b\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTHS},?\s+\d{{2,4}}\b")]
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_PHONE = re.compile(r"(?:\+?1[\s.-]?)?\(?\b\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b")
_GROUPED = re.compile(r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b")            # 1,234,567: a count or an amount, never an id
_ALNUM = re.compile(r"\b(?=[A-Za-z0-9-]*\d)(?=[A-Za-z0-9-]*[A-Za-z])[A-Za-z0-9][A-Za-z0-9-]{4,}[A-Za-z0-9]\b")
_DIGITS = re.compile(r"(?<![\w.$])\d[\d-]{3,}\d(?![\w])")
_WORD = re.compile(r"[^\W\d_](?:[^\W\d_]|['\-]){2,}")      # a word of letters in any script (a name with accents too)
_ALNUM_OK = frozenset({"sha256", "x86-64", "utf-16", "utf-32", "base64", "py313", "py312", "py311"})
_ID_QUOTED = re.compile(r"^(?:[a-z][a-z0-9]*(?:_[a-z0-9]+)+|[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+|[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)+|"
                        r"%[-A-Za-z%/ :.]*|[A-Z][a-z]+(?:[A-Z][a-z]+)+(?:Error|Exception|Warning))$")
_PLACEHOLDER = re.compile(r"\x00(\d+)\x00")
_NAME_STOP = frozenset("the and for with from this that not none null true false payer payers claim claims line lines bill "
                       "paid patient insurance medicare medicaid".split())


def _digits_in(s: str) -> int:
    return sum(ch.isdigit() for ch in s)


class Scrubber:
    """Masks what could identify a patient in free text and in structured values. One instance per process (SCRUB);
    learn() adds the patient names a store holds, so a name that reaches an error message unquoted is masked too."""

    def __init__(self) -> None:
        self.names: set[str] = set()
        self._vocab: set[str] | None = None
        self._code_words: set[str] | None = None

    # ---------------------------------------------------------------- what may stay
    def vocab(self) -> set[str]:
        """The store's own table and column names: a quoted one in an error message is kept (KeyError: 'status')."""
        if self._vocab is None:
            words: set[str] = set()
            try:
                from . import store as _store
                words |= set(re.findall(r"\b[a-z_][a-z0-9_]*\b", _store.DDL))
            except Exception as exc:        # noqa: BLE001  (a vocabulary is a convenience: without it more is masked)
                _note_internal("vocab", exc)
            self._vocab = words
        return self._vocab

    def code_words(self) -> set[str]:
        """Lowercase words of RemitWatch's own source: a patient name that is also one of them is not masked as a name
        (the quote and shape rules still apply), so the code's own messages stay readable."""
        if self._code_words is None:
            words: set[str] = set()
            here = os.path.dirname(os.path.abspath(__file__))
            try:
                for fn in os.listdir(here):
                    if fn.endswith(".py"):
                        with open(os.path.join(here, fn), encoding="utf-8", errors="ignore") as fh:
                            words |= set(re.findall(r"\b[a-z]{3,}\b", fh.read()))
            except OSError as exc:
                _note_internal("code_words", exc)
            self._code_words = words
        return self._code_words

    def learn(self, conn: sqlite3.Connection, limit: int = 400_000) -> int:
        """Add the patient and subscriber names the store holds (the 835's names). Returns how many words it knows."""
        cols = ("patient_last", "patient_first", "insured_last", "insured_first", "corrected_patient_last", "corrected_patient_first")
        try:
            have = {r[1] for r in conn.execute("PRAGMA table_info(claims)")}
        except sqlite3.Error:
            return len(self.names)
        use = [c for c in cols if c in have]
        if not use:
            return len(self.names)
        sql = " UNION ".join(f"SELECT DISTINCT {c} FROM claims WHERE {c} IS NOT NULL" for c in use) + f" LIMIT {int(limit)}"
        stop = self.code_words() | _NAME_STOP
        cur = conn.cursor()
        cur.row_factory = None
        try:
            for (v,) in cur.execute(sql):
                for w in _WORD.findall(str(v)):
                    w = w.lower().strip("'-")
                    if len(w) >= 3 and w not in stop:
                        self.names.add(w)
        except sqlite3.Error as exc:
            _note_internal("learn", exc)
        return len(self.names)

    def add_names(self, words: Iterable[str]) -> None:
        for v in words:
            for w in _WORD.findall(str(v)):
                if len(w) >= 3:
                    self.names.add(w.lower().strip("'-"))

    # ---------------------------------------------------------------- free text
    def text(self, value: Any, cap: int = TEXT_CAP) -> str:
        """Free text with everything that could identify a patient masked."""
        s = value if isinstance(value, str) else str(value)
        if len(s) > cap * 4:
            s = s[:cap * 4]
        kept: list[str] = []

        def protect(m: re.Match) -> str:
            kept.append(m.group(0))
            return f"\x00{len(kept) - 1}\x00"
        s = s.replace("\x00", "")
        s = _PROTECT.sub(protect, s)
        s = _URL.sub("<url>", s)
        s = _EMAIL.sub("<email>", s)
        s = _QUOTED.sub(self._quoted, s)
        s = _LABELED.sub(lambda m: m.group(0)[:m.start(2) - m.start(0)] + "<redacted>", s)
        s = _PATIENT_WORDS.sub(lambda m: m.group(1) + " <name>", s)
        s = _FILENAME.sub(lambda m: "<file>." + m.group(1).lower(), s)
        s = _PATH.sub(self._path, s)
        for rx in _DATES:
            s = rx.sub("<date>", s)
        s = _SSN.sub("<id>", s)
        s = _PHONE.sub("<phone>", s)
        s = _GROUPED.sub(protect, s)
        s = _ALNUM.sub(lambda m: "<id>" if _digits_in(m.group(0)) >= 3 and m.group(0).lower() not in _ALNUM_OK else m.group(0), s)
        s = _DIGITS.sub(lambda m: "<n>" if _digits_in(m.group(0)) >= 5 else m.group(0), s)
        if self.names:
            names = self.names
            s = _WORD.sub(lambda m: "<name>" if m.group(0).lower().strip("'-") in names else m.group(0), s)
        s = _PLACEHOLDER.sub(lambda m: kept[int(m.group(1))], s)
        return s[:cap] + ("..." if len(s) > cap else "")

    def _quoted(self, m: re.Match) -> str:
        inner = m.group(2)
        if inner and (_ID_QUOTED.match(inner) or inner in self.vocab()) and not _digits_in(inner) >= 5:
            if not (self.names and inner.lower() in self.names):
                return m.group(0)
        return m.group(1) + "<value>" + m.group(1)

    @staticmethod
    def _path(m: re.Match) -> str:
        p = m.group(0)
        if "\x00" in p or p in ("/", "\\") or re.fullmatch(r"[\d.]+/[\d.]+", p) or re.fullmatch(r"[a-z]+/[a-z]+", p):
            return p            # a ratio, "and/or", a kept id: not a path
        return "<path>"

    # ---------------------------------------------------------------- structured values
    def value(self, v: Any, key: str | None = None, depth: int = 0) -> Any:
        """A structured value made safe: dicts and lists walked (lists cut at LIST_CAP), numbers kept, strings kept only
        when their key says what they are and their shape agrees (a run id, a finding id, a code, a timestamp...),
        otherwise scrubbed as free text."""
        if v is None or isinstance(v, (bool, int, float)):
            return v
        if depth > 8:
            return "<deep>"
        if isinstance(v, dict):
            out = {}
            for k, x in list(v.items())[:LIST_CAP * 2]:
                sk = self.key(k)
                out[sk] = self.value(x, str(k), depth + 1)
            return out
        if isinstance(v, (list, tuple, set, frozenset)):
            seq = list(v)
            out_l = [self.value(x, key, depth + 1) for x in seq[:LIST_CAP]]
            if len(seq) > LIST_CAP:
                out_l.append(f"... {len(seq) - LIST_CAP} more")
            return out_l
        if isinstance(v, (bytes, bytearray)):
            return f"<{len(v)} bytes>"
        s = str(v)
        if key is not None:
            shape = KEY_SHAPES.get(key)
            if shape is not None and shape.fullmatch(s) and not (self.names and _has_name(s, self.names)):
                return s
        return self.text(s)

    def key(self, k: Any) -> str:
        s = str(k)
        if re.fullmatch(r"[a-z_][a-z0-9_:.\-]{0,60}", s) and not (self.names and s.lower() in self.names):
            return s
        return self.text(s, cap=80)


def _has_name(s: str, names: set[str]) -> bool:
    return any(w.lower().strip("'-") in names for w in _WORD.findall(s))


_TS = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?")
_IDENT = re.compile(r"[A-Za-z][A-Za-z0-9_:.|/ ()&'+,-]{0,119}")
_PAYER = re.compile(r"[A-Za-z][A-Za-z0-9 .,&'()/+-]{0,79}")
_FID = re.compile(r"(?=[0-9a-f]*[a-f])[0-9a-f]{8,64}")
_SHA = re.compile(r"(?:dropbox:|sha256:)?[0-9a-f]{8,64}")
KEY_SHAPES: dict[str, re.Pattern] = {}
for _k in ("ts", "at", "started", "finished", "started_at", "finished_at", "first_seen", "last_seen", "heartbeat", "created_at",
           "saved_at", "taken_at", "since", "last_change", "built", "modified", "next_at", "last_at", "first_at"):
    KEY_SHAPES[_k] = _TS
for _k in ("run_id", "previous_run", "killed_run"):
    KEY_SHAPES[_k] = re.compile(_RUN_ID)
for _k in ("finding_id", "item", "fid", "alert_id", "upload_id", "carrier", "folded_into", "old_id", "new_id"):
    KEY_SHAPES[_k] = _FID
for _k in ("line_key", "claim_key", "transaction_key"):
    KEY_SHAPES[_k] = re.compile(_KEY)
for _k in ("sha256", "sha", "file_id", "content_hash", "cursor", "ledger_rev", "rev", "commit", "rules_hash", "fingerprint"):
    KEY_SHAPES[_k] = _SHA
for _k in ("code", "cpt", "procedure_code", "billed_code"):
    KEY_SHAPES[_k] = re.compile(r"[A-Z0-9]{5}")
for _k in ("carc", "rarc", "reason", "carc_group"):
    KEY_SHAPES[_k] = re.compile(r"(?:[A-Z]{2}-)?[A-Z]{0,2}\d{1,4}[A-Z]?|[A-Z]{2}")
for _k in ("step", "event", "level", "rule", "rule_version", "kind", "signal", "category", "status", "work_state", "outcome",
           "host_mode", "mode", "trigger", "report", "via", "source", "remedy", "target", "state", "action", "strength",
           "how", "type", "function", "detect_rules", "version", "python", "sqlite", "ema_outcome", "origin", "result",
           "section", "where", "unit", "decision"):
    KEY_SHAPES[_k] = _IDENT
for _k in ("payer_group", "payer", "payer_kind"):
    KEY_SHAPES[_k] = _PAYER
for _k in ("month", "service_month"):
    KEY_SHAPES[_k] = re.compile(r"\d{4}-\d{2}")
for _k in ("newest_payment", "newest_payment_date", "last_payment", "first_payment", "as_of", "last_file_ingested", "day",
           "window_start", "window_end", "horizon", "pruned_on"):
    KEY_SHAPES[_k] = re.compile(r"\d{4}-\d{2}-\d{2}")

SCRUB = Scrubber()


def scrub_text(value: Any, cap: int = TEXT_CAP) -> str:
    return SCRUB.text(value, cap)


def scrub(value: Any, key: str | None = None) -> Any:
    return SCRUB.value(value, key)


def safe_id(value: Any) -> str | None:
    """A finding id (hex) or None: what the event log's `item` field accepts."""
    s = str(value or "")
    return s if _FID.fullmatch(s) else None


def coarse_dates(value: Any, ref: date | None = None) -> Any:
    """For a bundle: exact dates of one item become their month ("2026-03"), so no claim's own date leaves the store.
    Timestamps of runs (with a time part) are kept."""
    if isinstance(value, dict):
        return {k: coarse_dates(v, ref) for k, v in value.items()}
    if isinstance(value, list):
        return [coarse_dates(v, ref) for v in value]
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return value[:7]
    return value


# ------------------------------------------------------------------------------------------------------------ errors
def _rel(path: str) -> str:
    """A frame's file as a path inside the repository or a library, never a home folder's name."""
    p = (path or "").replace("\\", "/")
    for marker in ("/remitwatch/", "/tests/", "/api/", "/scripts/"):
        if marker in p:
            return marker.strip("/") + "/" + p.split(marker, 1)[1]
    if "site-packages/" in p:
        return "site-packages/" + p.split("site-packages/", 1)[1]
    return os.path.basename(p)


def error_info(exc: BaseException, where: str | None = None) -> dict:
    """{type, message, stack: [{file, line, function, code}], where, causes}: everything about an exception a person needs
    to find its root cause, with the message scrubbed (a converter's message can hold a cell)."""
    frames = traceback.extract_tb(exc.__traceback__) if exc.__traceback__ else []
    stack = [{"file": _rel(f.filename), "line": f.lineno, "function": f.name, "code": scrub_text((f.line or "").strip(), 200)}
             for f in frames][-STACK_FRAMES:]
    if not where:
        own = [s for s in stack if s["file"].startswith("remitwatch/")]
        last = own[-1] if own else (stack[-1] if stack else None)
        where = f"{last['file']}:{last['line']} in {last['function']}" if last else None
    causes = []
    seen = {id(exc)}
    nxt = exc.__cause__ or exc.__context__
    while nxt is not None and len(causes) < 3 and id(nxt) not in seen:
        seen.add(id(nxt))
        tb = traceback.extract_tb(nxt.__traceback__) if nxt.__traceback__ else []
        causes.append({"type": type(nxt).__name__, "message": scrub_text(str(nxt), 300),
                       "where": f"{_rel(tb[-1].filename)}:{tb[-1].lineno} in {tb[-1].name}" if tb else None})
        nxt = nxt.__cause__ or nxt.__context__
    return {"type": type(exc).__name__, "message": scrub_text(str(exc), 500), "stack": stack, "where": where, "causes": causes}


_INTERNAL: list[dict] = []


def _note_internal(what: str, exc: BaseException) -> None:
    """The logger's own trouble: never raised into the run, never lost. Kept for the run's manifest (logging_problems)."""
    if len(_INTERNAL) < 50:
        _INTERNAL.append({"what": what, "type": type(exc).__name__, "message": scrub_text(str(exc), 200)})


# ------------------------------------------------------------------------------------------------------------- memory
def memory() -> dict:
    """{rss_mb, peak_mb}: now and the most this process has held (Linux /proc, else getrusage)."""
    out: dict = {"rss_mb": None, "peak_mb": None}
    try:
        with open("/proc/self/status") as fh:
            for ln in fh:
                if ln.startswith("VmRSS:"):
                    out["rss_mb"] = int(ln.split()[1]) >> 10
                elif ln.startswith("VmHWM:"):
                    out["peak_mb"] = int(ln.split()[1]) >> 10
    except (OSError, ValueError, IndexError):
        pass
    if out["peak_mb"] is None:
        try:
            import resource
            out["peak_mb"] = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
        except (ImportError, OSError, ValueError):
            pass
    return out


def tmp_usage(folder: str | None = None) -> dict:
    """The temporary folder's use: {used_mb, free_mb, ours_mb} (ours = RemitWatch's own scratch folder)."""
    base = folder or _env().get("REMITWATCH_TMP") or tempfile.gettempdir()
    out: dict = {}
    try:
        du = shutil.disk_usage(base)
        out["used_mb"], out["free_mb"] = du.used >> 20, du.free >> 20
    except OSError:
        pass
    ours = os.path.join(base, "remitwatch")
    total = 0
    for dp, _d, fs in os.walk(ours):
        for f in fs:
            try:
                total += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    out["ours_mb"] = total >> 20
    return out


# ----------------------------------------------------------------------------------------------------------- the sinks
def _gz(data: bytes) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=6, mtime=0) as g:
        g.write(data)
    return buf.getvalue()


def _jl(obj: Any) -> str:
    return json.dumps(obj, default=str, separators=(",", ":"), ensure_ascii=False)


class DirSink:
    """The server's logs folder."""
    remote = False
    keep_days = SERVER_KEEP_DAYS
    budget = SERVER_BUDGET_BYTES

    def __init__(self, root: str):
        self.root = os.path.abspath(root)

    def _p(self, name: str) -> str:
        p = os.path.abspath(os.path.join(self.root, name))
        if not p.startswith(self.root + os.sep):
            raise ValueError("name escapes the logs folder")
        return p

    def describe(self) -> str:
        return "the logs folder beside the store"

    def append_event(self, line: str, day: str) -> None:
        os.makedirs(self.root, exist_ok=True)
        fd = os.open(self._p(f"events-{day}.jsonl"), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, (line + "\n").encode("utf-8"))
        finally:
            os.close(fd)

    def write_run(self, run_id: str, day: str, manifest: dict, events: list[dict]) -> None:
        """The server writes events as they happen; at the end only the manifest is added to the day's file."""
        os.makedirs(self.root, exist_ok=True)
        fd = os.open(self._p(f"runs-{day}.jsonl"), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, (_jl(manifest) + "\n").encode("utf-8"))
        finally:
            os.close(fd)

    def read_json(self, name: str) -> Any:
        try:
            with open(self._p(name), encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    def write_json(self, name: str, value: Any) -> None:
        p = self._p(name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = f"{p}.{secrets.token_hex(3)}.part"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(value, fh, default=str, ensure_ascii=False)
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)

    def delete(self, name: str) -> None:
        try:
            os.remove(self._p(name))
        except FileNotFoundError:
            pass

    def list(self, prefix: str = "") -> list[dict]:
        out = []
        base = self._p(prefix) if prefix else self.root
        for dp, _d, fs in os.walk(base):
            for f in fs:
                full = os.path.join(dp, f)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                out.append({"name": os.path.relpath(full, self.root).replace(os.sep, "/"), "size": st.st_size,
                            "modified": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(timespec="seconds")})
        return sorted(out, key=lambda m: m["name"])

    def read_lines(self, name: str) -> list[str]:
        p = self._p(name)
        try:
            if p.endswith(".gz"):
                with gzip.open(p, "rt", encoding="utf-8") as fh:
                    return fh.read().splitlines()
            with open(p, encoding="utf-8") as fh:
                return fh.read().splitlines()
        except (OSError, EOFError, gzip.BadGzipFile):
            return []

    def rotate(self, today: str) -> int:
        """Gzip the daily files of days before today. Returns how many were packed."""
        n = 0
        if not os.path.isdir(self.root):
            return 0
        for f in sorted(os.listdir(self.root)):
            m = re.fullmatch(r"(events|runs)-(\d{4}-\d{2}-\d{2})\.jsonl", f)
            if not m or m.group(2) >= today:
                continue
            src = os.path.join(self.root, f)
            dst = src + ".gz"
            with open(src, "rb") as fh:
                data = fh.read()
            if os.path.exists(dst):             # a day's file packed before and written to again: keep both parts
                with gzip.open(dst, "rb") as old:
                    data = old.read() + data
            tmp = dst + ".part"
            with open(tmp, "wb") as out:
                out.write(_gz(data))
            os.chmod(tmp, 0o600)
            os.replace(tmp, dst)
            os.remove(src)
            n += 1
        return n

    def day_of(self, name: str) -> str | None:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", name)
        return m.group(1) if m else None


class StateSink:
    """The serverless state folder's logs/ (remitwatch.state: LocalState or DropboxState)."""
    remote = True
    keep_days = SERVERLESS_KEEP_DAYS
    budget = SERVERLESS_BUDGET_BYTES
    PREFIX = "logs"

    def __init__(self, state: Any):
        self.state = state

    def _n(self, name: str) -> str:
        return f"{self.PREFIX}/{name}"

    def describe(self) -> str:
        return "the logs folder in the state folder"

    def append_event(self, line: str, day: str) -> None:
        return None                 # events are kept in memory and written with the run (one file per run)

    def write_run(self, run_id: str, day: str, manifest: dict, events: list[dict]) -> None:
        body = "\n".join([_jl(manifest)] + [_jl(e) for e in events]) + "\n"
        self.state.put(self._n(f"runs/{day}/{run_id}.jsonl.gz"), _gz(body.encode("utf-8")))

    def read_json(self, name: str) -> Any:
        try:
            raw = self.state.get(self._n(name))
            return json.loads(raw) if raw else None
        except ValueError:
            return None

    def write_json(self, name: str, value: Any) -> None:
        self.state.put(self._n(name), json.dumps(value, default=str, ensure_ascii=False).encode("utf-8"))

    def delete(self, name: str) -> None:
        self.state.delete(self._n(name))

    def list(self, prefix: str = "") -> list[dict]:
        got = self.state.list(self._n(prefix) if prefix else self.PREFIX)
        cut = len(self.PREFIX) + 1
        return [dict(m, name=m["name"][cut:]) for m in got if m["name"].startswith(self.PREFIX + "/")]

    def read_lines(self, name: str) -> list[str]:
        raw = self.state.get(self._n(name))
        if not raw:
            return []
        try:
            data = gzip.decompress(raw) if name.endswith(".gz") else raw
            return data.decode("utf-8").splitlines()
        except (OSError, EOFError, UnicodeDecodeError):
            return []

    def rotate(self, today: str) -> int:
        return 0

    def day_of(self, name: str) -> str | None:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", name)
        return m.group(1) if m else None


class MemorySink:
    """Nowhere: for a run with no logs folder (a test, a one-off command). Keeps what it was given in memory."""
    remote = False
    keep_days = SERVER_KEEP_DAYS
    budget = SERVER_BUDGET_BYTES

    def __init__(self) -> None:
        self.files: dict[str, Any] = {}
        self.lines: list[str] = []
        self.runs: list[tuple[dict, list]] = []

    def describe(self) -> str:
        return "memory only"

    def append_event(self, line: str, day: str) -> None:
        self.lines.append(line)

    def write_run(self, run_id: str, day: str, manifest: dict, events: list[dict]) -> None:
        self.runs.append((manifest, events))

    def read_json(self, name: str) -> Any:
        return self.files.get(name)

    def write_json(self, name: str, value: Any) -> None:
        self.files[name] = json.loads(json.dumps(value, default=str))

    def delete(self, name: str) -> None:
        self.files.pop(name, None)

    def list(self, prefix: str = "") -> list[dict]:
        return [{"name": n, "size": len(json.dumps(v))} for n, v in sorted(self.files.items()) if n.startswith(prefix)]

    def read_lines(self, name: str) -> list[str]:
        return []

    def rotate(self, today: str) -> int:
        return 0

    def day_of(self, name: str) -> str | None:
        return None


def logs_dir_for(db_path: str) -> str:
    """The server's logs folder: beside the store (/data/logs on AWS)."""
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), "logs")


def sink_for(*, db_path: str | None = None, state: Any = None, logs_dir: Any = None):
    """The sink for this host: a state folder (serverless), a logs folder, or a store path's logs folder."""
    if logs_dir is not None and not isinstance(logs_dir, str):
        if isinstance(logs_dir, (DirSink, StateSink, MemorySink)):
            return logs_dir
        return StateSink(logs_dir)
    if logs_dir:
        return DirSink(logs_dir)
    if state is not None:
        return StateSink(state)
    if db_path and db_path != ":memory:":
        return DirSink(logs_dir_for(db_path))
    return MemorySink()


# ------------------------------------------------------------------------------------------------------------- the run
_CURRENT: contextvars.ContextVar = contextvars.ContextVar("remitwatch_runlog", default=None)


def current() -> "RunLog | None":
    return _CURRENT.get()


class _Step:
    def __init__(self, rl: "RunLog", name: str, inputs: dict | None, contain: bool, checkpoint: bool):
        self.rl, self.name, self.inputs, self.contain, self.checkpoint = rl, name, inputs, contain, checkpoint
        self.failed: BaseException | None = None
        self.counts: dict = {}

    def __enter__(self):
        self.t0 = time.monotonic()
        self.m0 = memory()
        self.prev = self.rl._step
        self.rl._step = self.name
        if self.checkpoint:
            self.rl.checkpoint(self.name)
        self.rl.event(self.name, "step_start", detail={"inputs": self.inputs} if self.inputs else None)
        return self

    def __exit__(self, kind, exc, tb):
        ms = int((time.monotonic() - self.t0) * 1000)
        m1 = memory()
        rec = {"ms": ms, "rss_mb": m1.get("rss_mb"), "peak_mb": m1.get("peak_mb"),
               "peak_rise_mb": (m1["peak_mb"] - self.m0["peak_mb"]) if (m1.get("peak_mb") is not None and self.m0.get("peak_mb") is not None) else None}
        if self.rl.host == "serverless":
            rec["tmp"] = tmp_usage()
        if self.counts:
            rec["counts"] = scrub(self.counts)
        self.rl.manifest["steps"].append(dict(rec, step=self.name, ok=exc is None))
        self.rl._step = self.prev
        if exc is not None and isinstance(exc, Exception):
            self.failed = exc
            self.rl.error(self.name, exc, inputs=self.inputs, contained=True if self.contain else None)
            self.rl.event(self.name, "step_end", level="error", ms=ms, counts=self.counts or None)
            return self.contain
        self.rl.event(self.name, "step_end", ms=ms, counts=self.counts or None)
        return False


class RunLog:
    """One run's manifest and events. Made by start_run; finish() writes them."""

    def __init__(self, kind: str, *, trigger: str = "manual", host: str | None = None, run_id: str | None = None,
                 sink=None, now: datetime | None = None, env: dict | None = None):
        self.kind = kind
        self.host = host or host_mode(env)
        t = now or utcnow()
        self.run_id = run_id or new_run_id(t)
        self.started = t
        self.sink = sink if sink is not None else MemorySink()
        self.events: list[dict] = []
        self.dropped = 0
        self.errors = 0
        self.warnings = 0
        self.uncontained = False
        self._step = "run"
        self._last_checkpoint = 0.0
        self._token = None
        self._finished: str | None = None
        self._logged: dict[int, tuple] = {}
        self._t0 = time.monotonic()
        self.manifest: dict = {
            "schema": SCHEMA, "run_id": self.run_id, "kind": kind, "host_mode": self.host, "trigger": scrub(trigger, "trigger"),
            "started_at": _iso(t), "finished_at": None, "seconds": None, "outcome": "running",
            "code": {"commit": code_commit(env, self.host), **rules_version()},
            "versions": versions(), "settings": settings_names(env), "inputs": [], "inputs_summary": {},
            "positions": {}, "outputs": {}, "steps": [], "invariants": None, "self_regulation": None, "repairs": [],
            "errors": [], "memory": {}, "sections": {},
        }

    # ---------------------------------------------------------------- events
    def event(self, step: str | None, event: str, *, level: str = "info", rule: str | None = None, rule_version: str | None = None,
              item: Any = None, counts: dict | None = None, ms: int | None = None, detail: Any = None, error: dict | None = None) -> dict:
        """One line of the event log, in the stable schema (EVENT_KEYS). Scrubbed here; nothing else writes a line."""
        ev = {"ts": _iso(), "run_id": self.run_id, "step": scrub(step or self._step, "step"), "level": level,
              "event": scrub(event, "event"), "rule": scrub(rule, "rule") if rule else None,
              "rule_version": scrub(rule_version, "rule_version") if rule_version else None, "item": safe_id(item),
              "counts": scrub(counts) if counts else None, "ms": ms, "mem_mb": memory().get("rss_mb"),
              "detail": _cap_detail(scrub(detail)) if detail is not None else None, "error": error}
        if level == "error":
            self.errors += 1
        elif level == "warning":
            self.warnings += 1
        keep = len(self.events) < MAX_EVENTS or (level in ("error", "warning") and self.errors + self.warnings <= MAX_ERRORS)
        if keep:
            self.events.append(ev)
            try:
                self.sink.append_event(_jl(ev), ev["ts"][:10])
            except Exception as exc:        # noqa: BLE001  (the logs folder full or gone: the run goes on, the trouble is kept)
                _note_internal("append_event", exc)
        else:
            self.dropped += 1
        return ev

    def log(self, *args: Any, **_kw: Any) -> None:
        """A drop-in for the existing code's `log=print`: each progress line becomes an event, scrubbed. On a server the
        line is also printed to the console's error stream when echo is on; on Vercel nothing is printed."""
        text = " ".join(str(a) for a in args)
        up = text.upper()
        level = "error" if "ERROR" in up else ("warning" if "WARN" in up else "info")
        self.event(None, "log", level=level, detail={"text": text})
        if self._echo is not None:
            try:
                self._echo(scrub_text(text))
            except Exception as exc:        # noqa: BLE001
                _note_internal("echo", exc)

    _echo: Callable | None = None

    def echo_to(self, fn: Callable | None) -> "RunLog":
        """Also hand each log line (scrubbed) to fn, e.g. a server console. Never set on Vercel."""
        self._echo = fn if self.host != "serverless" else None
        return self

    def error(self, step: str | None, exc: BaseException, *, inputs: Any = None, contained: bool | None = True,
              level: str = "error") -> dict:
        """An exception caught at a step boundary: its full stack, the step's inputs (ids only) and whether the run
        carried on (contained True: the run went on; None: raised to the caller; False: it ended the run). Kept in the
        manifest's errors too (up to MAX_ERRORS). The same exception logged again (a step logged it, then the caller's
        except block caught it) only updates the first record."""
        prev = self._logged.get(id(exc))
        if prev is not None and prev[0] is exc:
            rec = prev[1]
            if contained is not None:
                rec["contained"] = bool(contained)
            if contained is False:
                self.uncontained = True
            return {"run_id": self.run_id, "event": "error", "repeat": True}
        info = error_info(exc)
        info["contained"] = contained
        if contained is False:
            self.uncontained = True
        rec = dict(info, step=scrub(step or self._step, "step"), at=_iso(), inputs=scrub(inputs) if inputs is not None else None)
        if len(self.manifest["errors"]) < MAX_ERRORS:
            self.manifest["errors"].append(rec)
        if len(self._logged) < 500:
            self._logged[id(exc)] = (exc, rec)
        return self.event(step, "error", level=level, detail={"inputs": inputs} if inputs is not None else None, error=info)

    def fail(self, exc: BaseException, step: str | None = None) -> dict:
        """The exception that ended the run (the cycle's own except block)."""
        return self.error(step, exc, contained=False)

    def step(self, name: str, *, inputs: dict | None = None, contain: bool = False, checkpoint: bool = True) -> _Step:
        """`with rl.step(name):` times a step (ms, memory, /tmp on serverless) and logs an exception with its stack. With
        contain=True the exception is kept from the caller (the run carries on; `.failed` holds it)."""
        return _Step(self, name, inputs, contain, checkpoint)

    # ---------------------------------------------------------------- manifest sections
    def set(self, section: str, value: Any) -> None:
        """Attach a section to the manifest: invariants and self_regulation (remitwatch.selfcheck), archive
        (remitwatch.archive), decisions (record_decisions' summary), positions, outputs, or any other named part."""
        v = scrub(value)
        if section in ("invariants", "self_regulation", "positions", "outputs"):
            self.manifest[section] = v
        else:
            self.manifest["sections"][scrub(section, "section")] = v

    def add_inputs(self, items: Iterable[dict]) -> None:
        """Input files: {sha256 (or file_id), size, kind, origin, result, rows}. Never a name or a path."""
        for it in items:
            it = {k: it.get(k) for k in ("sha256", "file_id", "size", "kind", "origin", "result", "rows", "report", "reason", "attempts")
                  if it.get(k) is not None}
            for k in ("sha256", "file_id"):
                if k in it:
                    it[k] = str(it[k]).replace("dropbox:", "")[:12]
            s = self.manifest["inputs_summary"]
            key = f"{it.get('kind') or 'file'}:{it.get('result') or 'listed'}"
            s[key] = s.get(key, 0) + 1
            if len(self.manifest["inputs"]) < MAX_INPUTS:
                self.manifest["inputs"].append(scrub(it))

    def repair(self, entry: dict) -> None:
        """A self-repair attempt (remitwatch.repair records it here as well as in its own log)."""
        if len(self.manifest["repairs"]) < MAX_ERRORS:
            self.manifest["repairs"].append(scrub(entry))
        self.event("repair", "repair_" + str(entry.get("outcome") or "attempt"), level="warning" if entry.get("outcome") in ("held", "gave_up") else "info",
                   rule=entry.get("remedy"), detail={k: entry.get(k) for k in ("remedy", "target", "attempt", "outcome", "next_step")})

    # ---------------------------------------------------------------- a run in progress
    def checkpoint(self, step: str, force: bool = False) -> None:
        """Mark how far the run got (the open marker), so a run killed by the platform is found by the next one. On a
        serverless host at most every CHECKPOINT_SECONDS_REMOTE seconds unless forced."""
        now = time.monotonic()
        if getattr(self.sink, "remote", False) and not force and now - self._last_checkpoint < CHECKPOINT_SECONDS_REMOTE:
            return
        self._last_checkpoint = now
        mem = memory()
        body = {"run_id": self.run_id, "kind": self.kind, "host_mode": self.host, "trigger": self.manifest["trigger"],
                "started_at": self.manifest["started_at"], "last_step": scrub(step, "step"), "last_at": _iso(),
                "pid": os.getpid(), "host": _host_tag(), "peak_mb": mem.get("peak_mb"),
                "steps_done": [s["step"] for s in self.manifest["steps"]][-30:]}
        try:
            self.sink.write_json(f"open/{self.run_id}.json", body)
        except Exception as exc:        # noqa: BLE001
            _note_internal("checkpoint", exc)

    def outcome(self, out: dict | None = None) -> str:
        if out and out.get("skipped"):
            return "skipped"
        if self.uncontained or (out and out.get("error")):
            return "failed"
        if self.manifest.get("sections", {}).get("safe_mode"):
            return "safe_mode"
        if self.errors:
            return "partial"
        return "ok"

    def finish(self, *, conn: sqlite3.Connection | None = None, out: dict | None = None, outcome: str | None = None,
               state: Any = None, now: datetime | None = None, tables: bool = False) -> str:
        """End the run: positions and outputs taken from the store when given, the manifest and events written to the
        sink, the open marker removed, the index updated, old logs pruned (once a day), and a short copy of the
        manifest kept in the store (run_log) when conn is given and writable. Returns the one line for the platform's
        own log. Safe to call twice (the second call returns the same line)."""
        if self._finished is not None:
            return self._finished
        t = now or utcnow()
        m = self.manifest
        m["finished_at"] = _iso(t)
        m["seconds"] = round(time.monotonic() - self._t0, 1)
        if out is not None:
            m["sections"]["cycle"] = _cycle_summary(out)
        m["memory"] = memory()
        if self.host == "serverless":
            m["memory"]["tmp"] = tmp_usage()
        if conn is not None:
            try:
                pos = positions(conn, state)
                before = m["positions"].get("before") if isinstance(m["positions"], dict) else None
                m["positions"] = {"before": before, "after": pos}
                m["outputs"] = dict(m.get("outputs") or {}, **outputs_summary(conn))
                if not m["inputs"]:
                    self.add_inputs(inputs_from_store(conn, m["started_at"]))
                if tables:
                    m["store"] = store_tables(conn)
            except Exception as exc:        # noqa: BLE001
                _note_internal("finish_store", exc)
        m["events"] = {"kept": len(self.events), "dropped": self.dropped, "errors": self.errors, "warnings": self.warnings}
        m["outcome"] = outcome or self.outcome(out)
        if _INTERNAL:
            m["logging_problems"] = list(_INTERNAL)
            _INTERNAL.clear()
        day = m["started_at"][:10]
        try:
            self.sink.write_run(self.run_id, day, m, self.events)
        except Exception as exc:        # noqa: BLE001
            _note_internal("write_run", exc)
        try:
            update_index(self.sink, summary_of(m))
            self.sink.delete(f"open/{self.run_id}.json")
        except Exception as exc:        # noqa: BLE001
            _note_internal("index", exc)
        try:
            prune_logs(self.sink, now=t, run=self)
        except Exception as exc:        # noqa: BLE001
            _note_internal("prune", exc)
        if conn is not None:
            try:
                save_run_row(conn, m)
            except sqlite3.Error as exc:
                _note_internal("run_row", exc)
        if self._token is not None:
            try:
                _CURRENT.reset(self._token)
            except ValueError:
                _CURRENT.set(None)          # finished in another context than it started in
            self._token = None
        self._finished = one_line(m)
        return self._finished

    def save_to_store(self, conn: sqlite3.Connection) -> None:
        """Serverless: write the run's row (outcome so far) into the store before the store is saved. The final outcome
        reaches the store at the next run (sync_store_from_index)."""
        m = dict(self.manifest, outcome=self.outcome() if self.manifest["outcome"] == "running" else self.manifest["outcome"],
                 seconds=round(time.monotonic() - self._t0, 1))
        try:
            save_run_row(conn, m)
        except sqlite3.Error as exc:
            _note_internal("run_row", exc)


def _cap_detail(v: Any) -> Any:
    s = _jl(v)
    if len(s) <= 4000:
        return v
    return {"truncated": True, "text": s[:3900]}


def _cycle_summary(out: dict) -> dict:
    """A cycle's own summary (pipeline.run_cycle's or cloud.run_cloud_cycle's `out`), scrubbed and cut to its figures."""
    keep = {}
    for k, v in (out or {}).items():
        if k in ("report",):
            continue
        keep[k] = v
    return _cap_detail(scrub(keep))


def one_line(m: dict) -> str:
    """The single line for Vercel's own function log: the run id and its outcome, no detail."""
    return f"RemitWatch run {m.get('run_id')}: {m.get('outcome')} ({m.get('seconds')} s)"


def _host_tag() -> str:
    """Which machine a marker came from, as a hash (a host's name is never written)."""
    try:
        import socket
        return hashlib.sha256(socket.gethostname().encode()).hexdigest()[:8]
    except OSError:
        return ""


def start_run(kind: str = "cycle", *, trigger: str = "manual", conn: sqlite3.Connection | None = None, db_path: str | None = None,
              state: Any = None, logs_dir: Any = None, host: str | None = None, run_id: str | None = None,
              now: datetime | None = None, env: dict | None = None, learn: bool = True, make_current: bool = True) -> RunLog:
    """Begin a run's logs. The sink is the state folder's logs/ (state), a logs folder (logs_dir) or the store's logs
    folder (db_path). With conn: the scrubber learns the store's patient names, the feed positions are taken, and
    the run_log rows of earlier runs are brought up to date from the index. Runs that left an open marker and are
    gone (killed) are recorded as killed. Never raises: a logging failure must not stop a run."""
    sink = sink_for(db_path=db_path, state=state, logs_dir=logs_dir)
    rl = RunLog(kind, trigger=trigger, host=host, run_id=run_id, sink=sink, now=now, env=env)
    if make_current:
        rl._token = _CURRENT.set(rl)
    try:
        sink.rotate(rl.manifest["started_at"][:10])
    except Exception as exc:        # noqa: BLE001
        _note_internal("rotate", exc)
    if conn is not None:
        if learn:
            SCRUB.learn(conn)
        try:
            rl.manifest["positions"] = {"before": positions(conn, state)}
        except Exception as exc:        # noqa: BLE001
            _note_internal("positions", exc)
    try:
        killed = find_killed_runs(sink, now=rl.started, exclude=rl.run_id, host=rl.host)
        for k in killed:
            rl.event("run", "previous_run_killed", level="warning",
                     detail={"killed_run": k.get("run_id"), "last_step": k.get("last_step"), "last_at": k.get("last_at"),
                             "peak_mb": k.get("peak_mb")})
        if killed:
            rl.manifest["sections"]["killed_runs"] = scrub([{k: v for k, v in x.items() if k in ("run_id", "last_step", "last_at", "peak_mb", "trigger")} for x in killed])
    except Exception as exc:        # noqa: BLE001
        _note_internal("killed_runs", exc)
    if conn is not None:
        try:
            sync_store_from_index(conn, sink)
        except Exception as exc:        # noqa: BLE001
            _note_internal("sync_index", exc)
    rl.checkpoint("start", force=True)
    rl.event("run", "run_start", detail={"kind": kind, "trigger": trigger, "host_mode": rl.host})
    return rl


def event(step: str | None, name: str, **fields: Any) -> dict | None:
    """Log to the run in progress (G, E and the hooks use this); nothing when no run is in progress."""
    rl = current()
    return rl.event(step, name, **fields) if rl is not None else None


def caught(exc: BaseException, step: str | None = None, *, inputs: Any = None, level: str = "error") -> dict | None:
    """In an except block that contains an exception: log it with its stack to the run in progress (if any). One line
    in each such block is what keeps a failure from being swallowed silently."""
    rl = current()
    if rl is None:
        return None
    return rl.error(step, exc, inputs=inputs, contained=True, level=level)


# --------------------------------------------------------------------------------------------------- manifest helpers
def code_commit(env: dict | None = None, host: str | None = None) -> str | None:
    """The commit this code was deployed from: Vercel's VERCEL_GIT_COMMIT_SHA, the server image's REMITWATCH_COMMIT."""
    e = _env(env)
    for k in (("VERCEL_GIT_COMMIT_SHA", "REMITWATCH_COMMIT") if (host or host_mode(env)) == "serverless" else ("REMITWATCH_COMMIT", "VERCEL_GIT_COMMIT_SHA")):
        sha = (e.get(k) or "").strip().lower()
        if re.fullmatch(r"[0-9a-f]{7,64}", sha):
            return sha[:12]
    return None


def rules_version() -> dict:
    try:
        from .detect import DETECT_RULES, DETECT_VERSION
        return {"detect_rules": DETECT_RULES, "rules_version": DETECT_VERSION}
    except Exception as exc:        # noqa: BLE001
        _note_internal("rules_version", exc)
        return {"detect_rules": None, "rules_version": None}


def versions() -> dict:
    try:
        from . import __version__
    except ImportError:
        __version__ = None
    return {"python": platform.python_version(), "sqlite": sqlite3.sqlite_version, "remitwatch": __version__,
            "serialize": hasattr(sqlite3.Connection, "serialize")}


SETTING_PREFIXES = ("REMITWATCH_", "TRIZETTO_", "SRFAX_", "MODMED_", "DROPBOX_", "SMTP_", "PRACTICE_", "APPEAL_", "ALERT_",
                    "REPORT_", "ANTHROPIC_", "CRON_", "VERCEL", "SITE_", "AWS_LAMBDA_FUNCTION_MEMORY_SIZE")


def settings_names(env: dict | None = None) -> list[str]:
    """The names (never the values) of the settings in force."""
    return sorted(k for k in _env(env) if k.startswith(SETTING_PREFIXES) and re.fullmatch(r"[A-Z0-9_]{2,80}", k))


def fingerprint(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return hashlib.sha256(str(value).encode()).hexdigest()[:12]


def positions(conn: sqlite3.Connection | None = None, state: Any = None) -> dict:
    """The feed positions a run moves: the Dropbox cursor and the posting ledger's revision (fingerprints only)."""
    out: dict = {}
    if conn is not None:
        cur = conn.cursor()
        cur.row_factory = None
        for key, name in (("sync:cursor", "cursor"), ("ema:ledger_seq", "ledger_seq"), ("ema:ledger_rev", "ledger_rev"),
                          ("cloud:data_rev", "data_rev")):
            try:
                r = cur.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            except sqlite3.Error:
                r = None
            if r and r[0] is not None:
                out[name] = fingerprint(r[0]) if name in ("cursor", "ledger_rev") else str(r[0])[:20]
    if state is not None:
        try:
            raw = state.get("cursor.json")
            if raw:
                out["cursor"] = fingerprint(json.loads(raw).get("cursor"))
            m = state.meta("ema_ledger.sqlite.gz")
            if m:
                out["ledger_state_rev"] = fingerprint(m.get("rev"))
        except Exception as exc:        # noqa: BLE001
            _note_internal("state_positions", exc)
    return out


def outputs_summary(conn: sqlite3.Connection) -> dict:
    """Items by state and by signal (counts and dollars), from the findings table as the run left it."""
    cur = conn.cursor()
    cur.row_factory = None
    try:
        have = {r[1] for r in cur.execute("PRAGMA table_info(findings)")}
    except sqlite3.Error:
        return {}
    if not have:
        return {}
    ws = "COALESCE(work_state,'')" if "work_state" in have else "''"
    out: dict = {"by_state": {}, "by_signal": {}}
    for st, n, cents in cur.execute(f"SELECT {ws}, COUNT(*), COALESCE(SUM(shortfall_cents),0) FROM findings WHERE COALESCE(stale,0)=0 GROUP BY 1"):
        out["by_state"][st or "none"] = {"n": n, "cents": cents}
    for sig, n in cur.execute("SELECT signal, COUNT(*) FROM findings WHERE COALESCE(stale,0)=0 GROUP BY 1 ORDER BY 2 DESC LIMIT 60"):
        out["by_signal"][sig or "none"] = n
    return out


def store_tables(conn: sqlite3.Connection, budget_s: float = 3.0) -> dict:
    """Row counts of every table (stops when budget_s is spent; the rest are named with no count)."""
    cur = conn.cursor()
    cur.row_factory = None
    names = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    out: dict = {}
    t0 = time.monotonic()
    for n in names:
        if time.monotonic() - t0 > budget_s:
            out[n] = None
            continue
        try:
            out[n] = cur.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0]
        except sqlite3.Error:
            out[n] = None
    return out


def inputs_from_store(conn: sqlite3.Connection, since: str, limit: int = MAX_INPUTS) -> list[dict]:
    """The input files a run brought in, as the store recorded them (files read since `since`, report catalog rows
    received, applied or decided since then): hash prefix, size, kind, where it came from, result. Never a name."""
    cur = conn.cursor()
    cur.row_factory = None
    out: list[dict] = []
    try:
        for sha, size, kind, status, path in cur.execute(
                "SELECT sha256, size, kind, status, source_path FROM files WHERE ingested_at >= ? ORDER BY ingested_at LIMIT ?", (since, limit)):
            out.append({"sha256": sha, "size": size, "kind": kind, "result": status, "origin": _origin_of(path)})
    except sqlite3.Error as exc:
        _note_internal("inputs_files", exc)
    try:
        for fid, report, via, status, rows, attempts, reason in cur.execute(
                """SELECT file_id, report, via, status, rows_applied, attempts, status_reason FROM ema_report_files
                   WHERE COALESCE(received_at,'') >= ? OR COALESCE(applied_at,'') >= ? OR COALESCE(decided_at,'') >= ? LIMIT ?""",
                (since, since, since, limit)):
            out.append({"file_id": fid, "kind": "report", "report": report, "origin": via, "result": status, "rows": rows,
                        "attempts": attempts, "reason": reason})
    except sqlite3.Error:
        pass            # a store without EMA's reports
    return out


def _origin_of(path: str | None) -> str:
    p = (path or "").replace("\\", "/")
    if "/batch/" in p:
        return "dropbox"
    if "/incoming/" in p or "/uploads/" in p:
        return "upload"
    if "/trizetto" in p.lower():
        return "trizetto"
    return "folder" if p else "unknown"


# -------------------------------------------------------------------------------------------------- index and pruning
def summary_of(m: dict) -> dict:
    """A run's line in the index: what the Data tab's panel lists."""
    errs = m.get("errors") or []
    return {"run_id": m.get("run_id"), "kind": m.get("kind"), "host_mode": m.get("host_mode"), "trigger": m.get("trigger"),
            "started_at": m.get("started_at"), "finished_at": m.get("finished_at"), "seconds": m.get("seconds"),
            "outcome": m.get("outcome"), "errors": len(errs), "repairs": len(m.get("repairs") or []),
            "peak_mb": (m.get("memory") or {}).get("peak_mb"),
            "first_error": {k: errs[0].get(k) for k in ("type", "where", "step")} if errs else None,
            "commit": (m.get("code") or {}).get("commit"), "rules_version": (m.get("code") or {}).get("rules_version")}


def update_index(sink, entry: dict) -> list[dict]:
    idx = sink.read_json("index.json") or {}
    runs = [r for r in (idx.get("runs") or []) if isinstance(r, dict) and r.get("run_id") != entry.get("run_id")]
    runs.append(entry)
    runs = sorted(runs, key=lambda r: r.get("started_at") or "")[-INDEX_KEEP:]
    idx["runs"] = runs
    idx["schema"] = SCHEMA
    sink.write_json("index.json", idx)
    return runs


def read_index(sink) -> list[dict]:
    idx = sink.read_json("index.json") or {}
    return [r for r in (idx.get("runs") or []) if isinstance(r, dict)]


def find_killed_runs(sink, *, now: datetime | None = None, exclude: str | None = None, host: str = "server") -> list[dict]:
    """Open markers of runs that are gone: on a server, a marker whose process is not running here or that is older
    than SERVER_STALE_SECONDS; on a serverless host, one older than SERVERLESS_STALE_SECONDS. Each is recorded in the
    index as killed and its marker removed."""
    t = now or utcnow()
    out = []
    for meta in sink.list("open"):
        name = meta.get("name") or ""
        if not name.endswith(".json"):
            continue
        body = sink.read_json(name)
        if not isinstance(body, dict) or body.get("run_id") == exclude:
            continue
        try:
            last = datetime.fromisoformat(str(body.get("last_at") or body.get("started_at")).replace("Z", "+00:00"))
            age = (t - last).total_seconds()
        except (TypeError, ValueError):
            age = 1e9
        if host == "serverless" or body.get("host_mode") == "serverless":
            gone = age > SERVERLESS_STALE_SECONDS
        else:
            same_host = body.get("host") == _host_tag()
            gone = age > SERVER_STALE_SECONDS or (same_host and not _pid_alive(body.get("pid")))
        if not gone:
            continue
        entry = {"run_id": body.get("run_id"), "kind": body.get("kind"), "host_mode": body.get("host_mode"),
                 "trigger": body.get("trigger"), "started_at": body.get("started_at"), "finished_at": None,
                 "outcome": "killed", "last_step": body.get("last_step"), "last_at": body.get("last_at"),
                 "peak_mb": body.get("peak_mb"), "errors": 0, "repairs": 0}
        update_index(sink, entry)
        sink.delete(name)
        out.append(entry)
    return out


def _pid_alive(pid: Any) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid == os.getpid():
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            return b"remitwatch" in fh.read()
    except OSError:
        return True


def prune_logs(sink, *, now: datetime | None = None, run: RunLog | None = None, force: bool = False) -> dict:
    """Delete log files older than the sink's keep_days, then the oldest until the folder fits its budget. Once a day
    (index.json remembers the day) unless forced. The drop is logged to the run."""
    t = now or utcnow()
    today = t.date().isoformat()
    idx = sink.read_json("index.json") or {}
    if not force and idx.get("pruned_on") == today:
        return {"skipped": True}
    files = [m for m in sink.list("") if (m.get("name") or "").startswith(("events-", "runs-", "runs/"))]
    cutoff = (t.date() - timedelta(days=sink.keep_days)).isoformat()
    gone: list[str] = []
    for m in files:
        d = sink.day_of(m["name"])
        if d and d < cutoff:
            gone.append(m["name"])
    left = [m for m in files if m["name"] not in set(gone)]
    total = sum(int(m.get("size") or 0) for m in left)
    over: list[str] = []
    if total > sink.budget:
        for m in sorted(left, key=lambda m: (sink.day_of(m["name"]) or "", m["name"])):
            if total <= sink.budget:
                break
            if sink.day_of(m["name"]) == today:
                break           # never today's own file
            over.append(m["name"])
            total -= int(m.get("size") or 0)
    names = gone + over
    _delete_many(sink, names)
    idx = sink.read_json("index.json") or {}
    idx["pruned_on"] = today
    sink.write_json("index.json", idx)
    res = {"old": len(gone), "over_budget": len(over), "bytes_left": total}
    if names and run is not None:
        run.event("logs", "logs_pruned", level="warning" if over else "info", counts=res)
    return res


def _delete_many(sink, names: list[str]) -> None:
    if not names:
        return
    st = getattr(sink, "state", None)
    if st is not None and hasattr(st, "delete_many"):
        st.delete_many([f"{StateSink.PREFIX}/{n}" for n in names])
        return
    for n in names:
        sink.delete(n)


def read_run(sink, run_id: str) -> tuple[dict | None, list[dict]]:
    """A run's manifest and events from the sink (a server's daily files or a serverless run file)."""
    day = run_id[4:8] + "-" + run_id[8:10] + "-" + run_id[10:12] if re.match(r"run-\d{8}", run_id or "") else None
    manifest, events = None, []
    if isinstance(sink, StateSink):
        names = [m["name"] for m in sink.list("runs") if m["name"].endswith(f"/{run_id}.jsonl.gz")]
        for n in names[:1]:
            lines = sink.read_lines(n)
            if lines:
                manifest = _loads(lines[0])
                events = [e for e in (_loads(x) for x in lines[1:]) if e]
        return manifest, events
    days = [day] if day else []
    if day:
        nxt = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
        days.append(nxt)            # a run that went past midnight writes its manifest on the next day's file
    for d in days:
        for suffix in (".jsonl", ".jsonl.gz"):
            for ln in sink.read_lines(f"runs-{d}{suffix}"):
                m = _loads(ln)
                if m and m.get("run_id") == run_id:
                    manifest = m
            for ln in sink.read_lines(f"events-{d}{suffix}"):
                if f'"run_id":"{run_id}"' in ln:
                    e = _loads(ln)
                    if e:
                        events.append(e)
    return manifest, events


def _loads(s: str) -> Any:
    try:
        return json.loads(s)
    except ValueError:
        return None


# ---------------------------------------------------------------------------------------------------- the store tables
RUN_LOG_DDL = """
CREATE TABLE IF NOT EXISTS run_log (run_id TEXT PRIMARY KEY, kind TEXT, started_at TEXT, finished_at TEXT, host_mode TEXT,
  trigger TEXT, outcome TEXT, seconds REAL, peak_mb INTEGER, errors INTEGER, warnings INTEGER, repairs INTEGER,
  commit_sha TEXT, rules_version TEXT, summary TEXT);
CREATE INDEX IF NOT EXISTS ix_run_log_started ON run_log(started_at);
"""
DECISIONS_DDL = """
CREATE TABLE IF NOT EXISTS item_decisions (id INTEGER PRIMARY KEY AUTOINCREMENT, finding_id TEXT, at TEXT, run_id TEXT,
  step TEXT, kind TEXT, rule TEXT, rule_version TEXT, change TEXT, evidence TEXT);
CREATE INDEX IF NOT EXISTS ix_idec_finding ON item_decisions(finding_id);
CREATE INDEX IF NOT EXISTS ix_idec_run ON item_decisions(run_id);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    """run_log and item_decisions (the dashboard's copy makes them too, empty)."""
    conn.executescript(RUN_LOG_DDL + DECISIONS_DDL)


def save_run_row(conn: sqlite3.Connection, m: dict) -> None:
    ensure_schema(conn)
    small = {k: m.get(k) for k in ("run_id", "kind", "host_mode", "trigger", "started_at", "finished_at", "seconds", "outcome",
                                    "code", "versions", "positions", "outputs", "steps", "invariants", "self_regulation",
                                    "inputs_summary", "events", "memory")}
    small["errors"] = [{k: e.get(k) for k in ("type", "message", "where", "step", "contained")} for e in (m.get("errors") or [])[:20]]
    small["repairs"] = (m.get("repairs") or [])[:20]
    small["sections"] = {k: v for k, v in (m.get("sections") or {}).items() if k in ("decisions", "archive", "safe_mode", "killed_runs", "cycle")}
    text = _jl(small)
    if len(text) > 32000:
        small.pop("steps", None)
        small["sections"] = {k: v for k, v in small["sections"].items() if k != "cycle"}
        text = _jl(small)[:32000]
    conn.execute("""INSERT OR REPLACE INTO run_log (run_id, kind, started_at, finished_at, host_mode, trigger, outcome, seconds,
                    peak_mb, errors, warnings, repairs, commit_sha, rules_version, summary) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (m.get("run_id"), m.get("kind"), m.get("started_at"), m.get("finished_at"), m.get("host_mode"), m.get("trigger"),
                  m.get("outcome"), m.get("seconds"), (m.get("memory") or {}).get("peak_mb"), len(m.get("errors") or []),
                  (m.get("events") or {}).get("warnings"), len(m.get("repairs") or []), (m.get("code") or {}).get("commit"),
                  (m.get("code") or {}).get("rules_version"), text))
    conn.commit()


def sync_store_from_index(conn: sqlite3.Connection, sink) -> int:
    """Bring the store's run_log in line with the sink's index (a serverless run's final outcome, a killed run)."""
    runs = read_index(sink)
    if not runs:
        return 0
    ensure_schema(conn)
    cur = conn.cursor()
    cur.row_factory = None
    have = dict(cur.execute("SELECT run_id, outcome FROM run_log WHERE started_at >= ?", (min(r.get("started_at") or "" for r in runs),)).fetchall())
    n = 0
    for r in runs[-50:]:
        rid = r.get("run_id")
        if not rid or have.get(rid) == r.get("outcome"):
            continue
        if rid in have:
            conn.execute("UPDATE run_log SET outcome=?, finished_at=COALESCE(?, finished_at), seconds=COALESCE(?, seconds) WHERE run_id=?",
                         (r.get("outcome"), r.get("finished_at"), r.get("seconds"), rid))
        else:
            conn.execute("""INSERT OR IGNORE INTO run_log (run_id, kind, started_at, finished_at, host_mode, trigger, outcome, seconds,
                            peak_mb, errors, repairs, summary) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                         (rid, r.get("kind"), r.get("started_at"), r.get("finished_at"), r.get("host_mode"), r.get("trigger"),
                          r.get("outcome"), r.get("seconds"), r.get("peak_mb"), r.get("errors"), r.get("repairs"), _jl(r)))
        n += 1
    if n:
        conn.commit()
    return n


def prune_store(conn: sqlite3.Connection, *, now: datetime | None = None) -> dict:
    """Keep run_log to STORE_RUNS_KEEP_DAYS (at most STORE_RUNS_MAX rows) and item_decisions to DECISIONS_KEEP_DAYS,
    the rows of items no longer in the store to DECISIONS_ORPHAN_DAYS, and at most DECISIONS_MAX_ROWS rows."""
    ensure_schema(conn)
    t = now or utcnow()
    out = {}
    cut_runs = _iso(t - timedelta(days=STORE_RUNS_KEEP_DAYS))
    c = conn.execute("DELETE FROM run_log WHERE started_at < ?", (cut_runs,))
    out["run_log"] = c.rowcount
    conn.execute(f"DELETE FROM run_log WHERE run_id NOT IN (SELECT run_id FROM run_log ORDER BY started_at DESC LIMIT {STORE_RUNS_MAX})")
    c = conn.execute("DELETE FROM item_decisions WHERE at < ?", (_iso(t - timedelta(days=DECISIONS_KEEP_DAYS)),))
    out["old"] = c.rowcount
    has_findings = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='findings'").fetchone()
    if has_findings:
        c = conn.execute("DELETE FROM item_decisions WHERE at < ? AND finding_id NOT IN (SELECT finding_id FROM findings)",
                         (_iso(t - timedelta(days=DECISIONS_ORPHAN_DAYS)),))
        out["orphans"] = c.rowcount
    n = conn.execute("SELECT COUNT(*) FROM item_decisions").fetchone()[0]
    if n > DECISIONS_MAX_ROWS:
        conn.execute(f"DELETE FROM item_decisions WHERE id IN (SELECT id FROM item_decisions ORDER BY id LIMIT {n - DECISIONS_MAX_ROWS})")
        out["over"] = n - DECISIONS_MAX_ROWS
    conn.commit()
    return out


# --------------------------------------------------------------------------------------------------------- decision log
# fields compared between two snapshots of the findings table, and fields carried along to explain a change
TRACKED = ("status", "work_state", "follow_up_on", "appeal_deadline", "legal_deadline", "signal", "stale", "shortfall_cents",
           "expected_cents", "expected_value_cents", "recovery_rate", "ema_outcome")
CARRIED = ("line_key", "claim_key", "category", "payer_group")
_PERSON_KINDS = ("manual", "unhidden")
KIND_ORDER = ("closed", "reopened", "hidden", "raised", "signal_changed", "folded", "gone", "state", "outcome", "deadline",
              "value", "follow_up", "stale", "status")


class Snapshot:
    """The tracked fields of every item at one moment (finding id -> tuple), with when it was taken and when the
    previous detection run ended (a person's action after that is theirs, not an automatic change)."""
    __slots__ = ("taken_at", "prev_run_at", "cols", "rows")

    def __init__(self, taken_at: str, prev_run_at: str | None, cols: tuple, rows: dict):
        self.taken_at, self.prev_run_at, self.cols, self.rows = taken_at, prev_run_at, cols, rows

    def __len__(self) -> int:
        return len(self.rows)

    def get(self, fid: str, col: str) -> Any:
        r = self.rows.get(fid)
        if r is None or col not in self.cols:
            return None
        return r[self.cols.index(col)]


def snapshot_items(conn: sqlite3.Connection, *, now: datetime | None = None) -> Snapshot:
    """Take the tracked fields of every item (a few MB for tens of thousands of items)."""
    cur = conn.cursor()
    cur.row_factory = None
    taken = _iso(now)
    try:
        have = {r[1] for r in cur.execute("PRAGMA table_info(findings)")}
    except sqlite3.Error:
        have = set()
    prev = None
    try:
        r = cur.execute("SELECT MAX(finished_at) FROM detection_runs").fetchone()
        prev = r[0] if r else None
    except sqlite3.Error:
        pass
    if "finding_id" not in have:
        return Snapshot(taken, prev, (), {})
    cols = tuple(c for c in TRACKED + CARRIED if c in have) + ("auto",)
    auto = "(auto_note IS NOT NULL AND auto_note <> '')" if "auto_note" in have else "0"
    sql = f"SELECT finding_id, {', '.join(c for c in cols if c != 'auto')}, {auto} FROM findings"
    rows: dict = {}
    intern = sys.intern
    for r in cur.execute(sql):
        rows[r[0]] = tuple(intern(x) if isinstance(x, str) and len(x) <= 40 else x for x in r[1:])
    return Snapshot(taken, prev, cols, rows)


def _days(a: Any, b: Any) -> int | None:
    try:
        return (date.fromisoformat(str(b)[:10]) - date.fromisoformat(str(a)[:10])).days
    except (TypeError, ValueError):
        return None


def _value_moved(a: Any, b: Any) -> bool:
    if a == b:
        return False
    if a is None or b is None:
        return True
    try:
        d = abs(float(b) - float(a))
    except (TypeError, ValueError):
        return True
    return d >= VALUE_MIN_CENTS or (d >= 100 and d >= VALUE_MIN_SHARE * abs(float(a)))


def _diff(b: dict, a: dict) -> dict:
    """The tracked fields that changed, by the rules of what counts as a decision."""
    ch: dict = {}
    for col in ("status", "work_state", "signal", "ema_outcome", "appeal_deadline", "legal_deadline"):
        if col in a and b.get(col) != a.get(col):
            ch[col] = [b.get(col), a.get(col)]
    if "stale" in a and bool(b.get("stale")) != bool(a.get("stale")):
        ch["stale"] = [b.get("stale") or 0, a.get("stale") or 0]
    if "follow_up_on" in a and b.get("follow_up_on") != a.get("follow_up_on"):
        d = _days(b.get("follow_up_on"), a.get("follow_up_on"))
        if d is None or abs(d) >= FOLLOW_UP_MIN_DAYS or "work_state" in ch:
            ch["follow_up_on"] = [b.get("follow_up_on"), a.get("follow_up_on")]
    for col in ("shortfall_cents", "expected_cents", "expected_value_cents"):
        if col in a and _value_moved(b.get(col), a.get(col)):
            ch[col] = [b.get(col), a.get(col)]
    if "expected_value_cents" in ch and "recovery_rate" in a:
        ch["recovery_rate"] = [b.get("recovery_rate"), a.get("recovery_rate")]
    if b.get("auto") != a.get("auto") and ("status" in ch):
        ch["closed_automatically"] = [bool(b.get("auto")), bool(a.get("auto"))]
    return ch


def _kind_of(ch: dict, b: dict, a: dict) -> str:
    st = ch.get("status")
    if st and st[1] == "resolved" and a.get("auto"):
        return "closed"
    if st and st[0] == "resolved" and st[1] != "resolved" and b.get("auto"):
        return "reopened"
    if st and st[1] == "dismissed":
        return "hidden"
    for k, kind in (("signal", "signal_changed"), ("work_state", "state"), ("ema_outcome", "outcome"), ("appeal_deadline", "deadline"),
                    ("legal_deadline", "deadline"), ("shortfall_cents", "value"), ("expected_cents", "value"),
                    ("expected_value_cents", "value"), ("follow_up_on", "follow_up"), ("stale", "stale")):
        if k in ch:
            return kind
    return "status"


def _slug(s: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(s or "").lower()).strip("_")[:40] or "unknown"


def _details(conn: sqlite3.Connection, ids: list[str]) -> dict[str, dict]:
    """evidence, state_basis and deadline_basis of the items named (500 at a time)."""
    cur = conn.cursor()
    cur.row_factory = None
    have = {r[1] for r in cur.execute("PRAGMA table_info(findings)")}
    cols = [c for c in ("evidence", "state_basis", "deadline_basis", "recovery_basis") if c in have]
    if not cols:
        return {}
    out: dict = {}
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        q = f"SELECT finding_id, {', '.join(cols)} FROM findings WHERE finding_id IN ({','.join('?' * len(part))})"
        for r in cur.execute(q, part):
            out[r[0]] = dict(zip(cols, r[1:]))
    return out


def _ev_json(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        v = json.loads(raw) if raw else {}
        return v if isinstance(v, dict) else {}
    except (TypeError, ValueError):
        return {}


def _evidence_refs(row: dict, det: dict, ev: dict) -> list[dict]:
    """The evidence a decision rests on, as row keys and rule names only (no claim id, check number or free text but
    the scrubbed basis)."""
    out: list[dict] = []
    if row.get("line_key") or row.get("claim_key"):
        out.append({"source": "item", "line_key": row.get("line_key"), "claim_key": row.get("claim_key")})
    ar = ev.get("archive_rule")
    if isinstance(ar, dict):
        proof = []
        for p in (ar.get("proof") or [])[:5]:
            if isinstance(p, dict):
                proof.append({k: p.get(k) for k in ("source", "kind", "line_key", "claim_key", "amount_cents", "decisive") if p.get(k) is not None})
        out.append({"source": "archive_rules", "rule": ar.get("rule"), "action": ar.get("action") or ar.get("reclass"),
                    "strength": ar.get("strength"), "rows": proof})
    pl = ev.get("paid_later")
    if isinstance(pl, dict):
        out.append({"source": "paid_later", "how": pl.get("how"), "amount_cents": pl.get("paid_cents"),
                    "claim_status": pl.get("claim_status"), "from": pl.get("source")})
    folded = ev.get("folded")
    if isinstance(folded, list) and folded:
        out.append({"source": "folded", "items": [x.get("finding_id") for x in folded if isinstance(x, dict)][:20]})
    for k, src in (("state_basis", "states"), ("deadline_basis", "deadline"), ("recovery_basis", "recovery")):
        if det.get(k):
            out.append({"source": src, "basis": scrub_text(det[k], 300)})
    return scrub(out)


def _closed_rule(ev: dict, row: dict) -> str:
    ar = ev.get("archive_rule")
    if isinstance(ar, dict) and ar.get("rule"):
        return f"archive_rules:{_slug(ar.get('rule'))}"
    pl = ev.get("paid_later")
    if isinstance(pl, dict):
        return f"paid_later:{_slug(pl.get('how') or 'later_payment')}"
    if row.get("ema_outcome"):
        return f"ema_outcome:{_slug(row.get('ema_outcome'))}"
    return "detect:auto_close"


def _person_touched(conn: sqlite3.Connection, since: str | None) -> set[str]:
    if not since:
        return set()
    cur = conn.cursor()
    cur.row_factory = None
    try:
        return {r[0] for r in cur.execute(
            f"SELECT DISTINCT finding_id FROM finding_events WHERE at >= ? AND kind IN ({','.join('?' * len(_PERSON_KINDS))})",
            (since, *_PERSON_KINDS))}
    except sqlite3.Error:
        return set()


def _hidden_now(conn: sqlite3.Connection, since: str) -> set[str]:
    cur = conn.cursor()
    cur.row_factory = None
    try:
        return {r[0] for r in cur.execute("SELECT DISTINCT finding_id FROM finding_events WHERE kind='hidden' AND at >= ?", (since,))}
    except sqlite3.Error:
        return set()


def _latest_version(conn: sqlite3.Connection) -> str | None:
    try:
        r = conn.execute("SELECT version FROM detection_runs ORDER BY finished_at DESC LIMIT 1").fetchone()
        return r[0] if r else None
    except sqlite3.Error:
        return None


def record_decisions(conn: sqlite3.Connection, run_id: str, before: Snapshot, after: Snapshot | None = None, *,
                     step: str = "detect", rule_version: str | None = None, rules: dict | None = None,
                     now: datetime | None = None, write: bool = True) -> dict:
    """Compare the items before and after a step and write one row per item that changed automatically: raised, closed
    with proof, reopened, hidden by a rule, signal changed, folded into another, no longer raised, state changed,
    outcome, deadline, dollars or follow-up day moved. Each row names the rule (from the item's own evidence: the
    archive rule, the later payment, the EMA outcome; the state rule when `rules` gives it) and the rule version, the
    evidence row keys, and before and after. A change a person made (a status set by hand since the previous run) is
    left to their own events. Returns counts for the run's manifest."""
    after = after if after is not None else snapshot_items(conn, now=now)
    at = _iso(now)
    version = rule_version or _latest_version(conn)
    since = before.prev_run_at or before.taken_at
    person = _person_touched(conn, since)
    hidden = _hidden_now(conn, before.taken_at)
    bcols, acols = before.cols, after.cols

    def rowd(snap_cols: tuple, tup: tuple) -> dict:
        return dict(zip(snap_cols, tup))
    raised = [f for f in after.rows if f not in before.rows]
    gone = [f for f in before.rows if f not in after.rows]
    out_rows: list[tuple] = []
    counts: Counter = Counter()
    by_rule: Counter = Counter()
    raised_by: Counter = Counter()
    # a signal change gives an item a new id on the same line: pair what went with what came, by line and category
    gone_by_line: dict = {}
    for f in gone:
        r = rowd(bcols, before.rows[f])
        if r.get("line_key"):
            gone_by_line.setdefault((r["line_key"], r.get("category")), f)
    pairs: dict[str, str] = {}
    for f in raised:
        r = rowd(acols, after.rows[f])
        old = gone_by_line.pop((r.get("line_key"), r.get("category")), None) if r.get("line_key") else None
        if old:
            pairs[f] = old
    moved_from = set(pairs.values())
    changed: dict[str, dict] = {}
    kinds: dict[str, str] = {}
    same_cols = bcols == acols
    for f, tup in after.rows.items():
        old_t = before.rows.get(f)
        if old_t is None or (same_cols and old_t == tup):
            continue
        b, a = rowd(bcols, old_t), rowd(acols, tup)
        ch = _diff(b, a)
        if ch:
            changed[f] = ch
            kinds[f] = _kind_of(ch, b, a)
    need = list(changed) + raised + [f for f in gone if f not in moved_from]
    det = _details(conn, need) if need else {}
    # where folded items went: the carriers' evidence lists them
    folded_into: dict[str, str] = {}
    if gone:
        cur = conn.cursor()
        cur.row_factory = None
        try:
            for fid, raw in cur.execute("SELECT finding_id, evidence FROM findings WHERE evidence LIKE '%\"folded\"%'"):
                for x in _ev_json(raw).get("folded") or []:
                    if isinstance(x, dict) and x.get("finding_id"):
                        folded_into[x["finding_id"]] = fid
        except sqlite3.Error:
            pass

    def add(fid: str, kind: str, rule: str, change: dict, evidence: list) -> None:
        counts[kind] += 1
        by_rule[rule] += 1
        out_rows.append((fid, at, run_id, step, kind, rule, version, _jl(scrub(change)), _jl(evidence)))
    for f in raised:
        r = rowd(acols, after.rows[f])
        d = det.get(f, {})
        ev = _ev_json(d.get("evidence"))
        refs = _evidence_refs(r, d, ev)
        if f in pairs:
            old = pairs[f]
            ob = rowd(bcols, before.rows[old])
            ch = {"signal": [ob.get("signal"), r.get("signal")], "finding_id": [old, f]}
            add(f, "signal_changed", "detect:signal_changed", ch, refs)
            add(old, "signal_changed", "detect:signal_changed", ch, refs)
            continue
        ar = ev.get("archive_rule") if isinstance(ev.get("archive_rule"), dict) else {}
        rule = f"archive_rules:{_slug(ar.get('rule'))}" if ar.get("action") == "raise_item" else f"detect:{_slug(r.get('signal'))}"
        raised_by[r.get("signal") or "none"] += 1
        ch = {k: [None, r.get(k)] for k in ("signal", "status", "work_state", "shortfall_cents", "appeal_deadline") if r.get(k) is not None}
        add(f, "raised", rule, ch, refs)
    for f in gone:
        if f in moved_from:
            continue
        b = rowd(bcols, before.rows[f])
        if f in folded_into:
            add(f, "folded", "archive_rules:fold_into", {"folded_into": [None, folded_into[f]], "status": [b.get("status"), None]},
                [{"source": "item", "line_key": b.get("line_key"), "claim_key": b.get("claim_key")}])
        else:
            add(f, "gone", "detect:no_longer_raised", {"signal": [b.get("signal"), None], "status": [b.get("status"), None],
                                                         "work_state": [b.get("work_state"), None]},
                [{"source": "item", "line_key": b.get("line_key"), "claim_key": b.get("claim_key")}])
    for f, ch in changed.items():
        a = rowd(acols, after.rows[f])
        kind = kinds[f]
        automatic_status = kind == "closed" or (kind == "reopened" and f not in person) or (kind == "hidden" and f in hidden)
        if f in person and not automatic_status and kind in ("state", "follow_up", "status", "hidden", "stale", "reopened"):
            counts["after_person"] += 1
            continue
        d = det.get(f, {})
        ev = _ev_json(d.get("evidence"))
        refs = _evidence_refs(a, d, ev)
        if kind == "closed":
            rule = _closed_rule(ev, a)
        elif kind == "reopened":
            rule = "detect:payment_no_longer_stands"
        elif kind == "hidden":
            rule = f"hide_rule:{a.get('payer_group') or 'payer'}|{a.get('signal') or 'signal'}"
        elif kind == "state":
            rule = f"states:{_slug((rules or {}).get(f) or a.get('work_state'))}"
        elif kind == "outcome":
            rule = "ema_checks:line_outcome"
        elif kind == "deadline":
            rule = "codes:deadline"
        elif kind == "value":
            rule = "selfcheck:recovery_rate" if "expected_value_cents" in ch and len(ch) <= 2 else "detect:amount"
        elif kind == "stale":
            rule = "detect:no_longer_listed" if (ch["stale"][1] or 0) else "detect:listed_again"
        else:
            rule = f"detect:{kind}"
        add(f, kind, rule, ch, refs)
    if write and out_rows:
        ensure_schema(conn)
        conn.executemany("INSERT INTO item_decisions (finding_id, at, run_id, step, kind, rule, rule_version, change, evidence) "
                         "VALUES (?,?,?,?,?,?,?,?,?)", out_rows)
        conn.commit()
    closed_by = Counter()
    for row in out_rows:
        if row[4] == "closed":
            closed_by[row[5]] += 1
    return {"written": len(out_rows) if write else 0, "items_before": len(before), "items_after": len(after),
            "by_kind": dict(counts), "closed_by_rule": dict(closed_by.most_common(30)),
            "raised_by_signal": dict(raised_by.most_common(30)), "by_rule": dict(by_rule.most_common(40))}


def record_changes(conn: sqlite3.Connection, run_id: str, changes: Iterable[dict], *, step: str, rule_version: str | None = None,
                   now: datetime | None = None) -> int:
    """Write decisions a module makes directly (the archive retiring an item, a self-check holding a closure): each
    {"finding_id", "kind", "rule", "change": {field: [before, after]}, "evidence": [...]}. Scrubbed like the rest."""
    at = _iso(now)
    rows = []
    for c in changes:
        fid = safe_id(c.get("finding_id"))
        if not fid:
            continue
        rows.append((fid, at, run_id, scrub(step, "step"), scrub(c.get("kind") or "status", "kind"), scrub(c.get("rule") or step, "rule"),
                     c.get("rule_version") or rule_version, _jl(scrub(c.get("change") or {})), _jl(scrub(c.get("evidence") or []))))
    if rows:
        ensure_schema(conn)
        conn.executemany("INSERT INTO item_decisions (finding_id, at, run_id, step, kind, rule, rule_version, change, evidence) "
                         "VALUES (?,?,?,?,?,?,?,?,?)", rows)
        conn.commit()
    return len(rows)


def decisions_for(conn: sqlite3.Connection, finding_id: str, limit: int = 50) -> dict:
    """The automatic changes to one item, newest first: the body of GET /api/findings/{finding_id}/decisions."""
    cur = conn.cursor()
    cur.row_factory = None
    try:
        rows = cur.execute("""SELECT at, run_id, step, kind, rule, rule_version, change, evidence FROM item_decisions
                              WHERE finding_id=? ORDER BY at DESC, id DESC LIMIT ?""", (finding_id, int(limit))).fetchall()
    except sqlite3.Error:
        rows = []
    items = [{"at": r[0], "run_id": r[1], "step": r[2], "kind": r[3], "rule": r[4], "rule_version": r[5],
              "change": _loads(r[6]) or {}, "evidence": _loads(r[7]) or []} for r in rows]
    return {"items": items}


# --------------------------------------------------------------------------------------------------------- the panel
def panel(conn: sqlite3.Connection | None, logs_dir: Any = None, *, now: datetime | None = None, limit: int = 20) -> dict:
    """The Data tab's "Logs and diagnostics" panel: the last runs with their outcome, errors and self-repair attempts,
    open problems, and how to get the diagnostic bundle. conn: the store (server) or the dashboard's copy (Vercel);
    logs_dir: the logs folder (server), the state folder (Vercel) or a sink."""
    t = now or utcnow()
    runs: dict[str, dict] = {}
    if conn is not None:
        cur = conn.cursor()
        cur.row_factory = None
        try:
            for r in cur.execute("""SELECT run_id, kind, started_at, finished_at, host_mode, trigger, outcome, seconds, peak_mb, errors,
                                    repairs, summary FROM run_log ORDER BY started_at DESC LIMIT ?""", (limit,)):
                s = _loads(r[11]) or {}
                errs = s.get("errors") or []
                runs[r[0]] = {"run_id": r[0], "kind": r[1], "started_at": r[2], "finished_at": r[3], "host_mode": r[4], "trigger": r[5],
                              "outcome": r[6], "seconds": r[7], "peak_mb": r[8], "errors": r[9] or 0, "repairs": r[10] or 0,
                              "first_error": {k: errs[0].get(k) for k in ("type", "where", "step")} if errs else None}
        except sqlite3.Error:
            pass
    sink = None
    if logs_dir is not None:
        sink = sink_for(logs_dir=logs_dir)
        try:
            for r in read_index(sink):
                runs[r["run_id"]] = dict(runs.get(r["run_id"], {}), **{k: v for k, v in r.items() if v is not None})
        except Exception as exc:        # noqa: BLE001
            _note_internal("panel_index", exc)
    listed = sorted(runs.values(), key=lambda r: r.get("started_at") or "", reverse=True)[:limit]
    repairs: list[dict] = []
    problems: list[dict] = []
    try:
        from . import repair as _repair
        if conn is not None:
            repairs = _repair.recent(conn, limit=limit)
        problems = _repair.open_problems(conn=conn, sink=sink)
    except Exception as exc:        # noqa: BLE001
        _note_internal("panel_repairs", exc)
    last = listed[0] if listed else None
    if last is None:
        sentence = "No run has been logged yet."
    else:
        try:
            ago_h = (t - datetime.fromisoformat(str(last.get("started_at")).replace("Z", "+00:00"))).total_seconds() / 3600
        except (TypeError, ValueError):
            ago_h = None
        when = "just now" if ago_h is not None and ago_h < 1 else (f"{ago_h:.0f} hours ago" if ago_h is not None and ago_h < 48
                                                                  else (f"{ago_h / 24:.0f} days ago" if ago_h is not None else "at an unknown time"))
        words = {"ok": "finished with no errors", "partial": f"finished; {last.get('errors')} step(s) failed and were contained",
                 "failed": "failed", "killed": "was stopped before it finished (killed)", "skipped": "was skipped: another run was going",
                 "safe_mode": "finished in safe mode: automatic closures were held", "running": "is still running"}
        sentence = f"The last run started {when} and {words.get(last.get('outcome'), last.get('outcome'))}."
    if problems:
        sentence += f" {len(problems)} problem(s) could not be repaired automatically: see below."
    return {"title": "Logs and diagnostics", "sentence": sentence, "last_run": last, "runs": listed, "repairs": repairs,
            "open_problems": problems,
            "bundle": {"route": "/api/ops/diagnostic-bundle", "command": "python -m remitwatch.diagnose --days 7",
                       "what": "A zip with the run logs, errors, self-checks and repairs, and the store's layout. It holds no "
                               "patient data. Give it to Claude Code with the repository to find the cause of a problem."},
            "logs": {"where": sink.describe() if sink is not None else "the store", "keep_days": getattr(sink, "keep_days", None)}}


# -------------------------------------------------------------------------------------------- silent exception census
_LOGGING_CALLS = re.compile(r"(log|print|warn|caught|error|record|note|event|append|exception)", re.I)


def silent_excepts(paths: Iterable[str]) -> list[dict]:
    """except blocks that swallow an exception without a word: their body is only pass, continue, break, a bare return
    or a constant. A block that logs, records the error, assigns, calls anything or raises is not counted. Returns
    [{"file", "line", "handler"}]."""
    out = []
    for path in paths:
        try:
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=path)
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            if all(_silent_stmt(s) for s in node.body):
                typ = ast.unparse(node.type) if node.type is not None else "(bare)"
                out.append({"file": _rel(path), "line": node.lineno, "handler": typ})
    return out


def _silent_stmt(s: ast.stmt) -> bool:
    if isinstance(s, (ast.Pass, ast.Continue, ast.Break)):
        return True
    if isinstance(s, ast.Return):
        return s.value is None or isinstance(s.value, ast.Constant)
    if isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant):
        return True
    return False


@contextlib.contextmanager
def run_context(rl: RunLog):
    """Make rl the run in progress for a block (for code that runs in another thread or context)."""
    tok = _CURRENT.set(rl)
    try:
        yield rl
    finally:
        _CURRENT.reset(tok)
