"""
Self-repair by fixed rules: what RemitWatch does by itself when an operational step fails. Each remedy has an attempt
limit, a back-off, a log entry with before and after (the run's event log, its manifest and the repair_log table), and
an alert when it runs out of attempts. It never edits its own code: a code bug is contained (remitwatch.selfcheck's
safe mode keeps the last good worklist and holds automatic closures) and turned into a diagnostic bundle
(remitwatch.diagnose) complete enough for a person with Claude Code and this repository to find and fix the cause.

Remedies (REMEDIES):
  network          transient network trouble or HTTP 5xx/429 from Dropbox, TriZetto or SRFax: the call is tried again
                   after a back-off, within the run's time; then the step is held (Held) and the run carries on with
                   the rest. Once a target is held in a run, its later calls in that run get one plain try each (no
                   back-off; a success ends the hold), and after PROBES_AFTER_HOLD of those fail, none. A run counts
                   once however many of its calls were held, and a target held on RUNS_BEFORE_ALERT runs in a row
                   raises an alert. Only network trouble is tried again: a code bug is raised at once whatever its
                   message says, and so is anything raised inside a callback wrapped with not_retried (the ingest a
                   TriZetto pull hands its files to).
  unreadable_file  a file that cannot be read: counted per file (by its content hash); held with its reason after
                   `attempts` failed reads, so later runs skip it while the other files keep going. An alert says
                   which file (by hash and kind) and what to do.
  derived_table    a derived table that fails an invariant (the ledger summaries, the link map, the serving copy, the
                   detection run): rebuilt once from its sources and checked again; still failing, the run asks for
                   safe mode (selfcheck) and an alert is raised.
  killed_run       a run the platform stopped (memory, time): the next run finds its open marker (runlog) and the
                   cycle's own repair paths (cloud.repair_ledger, the Production attempts) are logged here. Counted
                   per step; a later run that finished and passed that step clears the count (read from the run
                   index when the registry is made, so no hook is needed for it).
  stale_lock       a lock left by a dead run: removed by the existing stale-lock rules (pipeline, cloud), logged here.
  schema           a store missing a column: the migration (ensure_schema) runs, with the columns before and after.
  pressure         memory or time pressure: selfcheck's self-regulation acts; its actions are logged here.

The registry's state (attempts, back-off, holds) is kept in the run's logs folder (repair_state.json: a serverless
run that dies before saving the store still counts its failure) and in the store's meta (repair:state, read by the
alerts). Nothing here reads the clock or the environment except through runlog.utcnow and the arguments it is given.
"""
from __future__ import annotations

import functools
import json
import re
import secrets
import socket
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Iterable

from . import runlog

STATE_FILE = "repair_state.json"
META_KEY = "repair:state"
RUNS_BEFORE_ALERT = 3
PROBES_AFTER_HOLD = 3           # plain tries a held target gets later in the same run before its calls are skipped
BUNDLE_STEP = ("download the diagnostic bundle from the Data tab (Logs and diagnostics) and give it to Claude Code with the "
               "repository")


@dataclass(frozen=True)
class Remedy:
    name: str
    what: str                       # what failed, in plain words
    attempts: int                   # tries within a run (network) or failed runs before a hold (a file)
    delays: tuple = ()              # seconds between tries within a run
    backoff: tuple = (0,)           # seconds a held target waits before it is tried again in a later run
    runs_before_alert: int = 1      # failed runs in a row before an alert
    next_step: str = ""             # the exact next step for a person once it has run out


REMEDIES: dict[str, Remedy] = {
    "network": Remedy("network", "a connection to Dropbox, TriZetto or SRFax", attempts=4, delays=(2, 8, 30), backoff=(0,),
                      runs_before_alert=RUNS_BEFORE_ALERT,
                      next_step="Check that the service answers (its test on the setup page) and that its settings are right. The next "
                                f"run tries again by itself. If it keeps failing, {BUNDLE_STEP}."),
    "unreadable_file": Remedy("unreadable_file", "a file that could not be read", attempts=2, backoff=(0, 6 * 3600),
                              runs_before_alert=1,
                              next_step="Open the Data tab: the file is listed as held with the reason. Export it again from EMA (or "
                                        "download the remittance again) and drop the new copy in the folder; discard the held one. "
                                        f"If a fresh copy is held too, {BUNDLE_STEP}."),
    "derived_table": Remedy("derived_table", "a table built from the data that failed its check", attempts=1, backoff=(0, 3600),
                            runs_before_alert=1,
                            next_step="RemitWatch is in safe mode: the worklist shows the last good run and automatic closures are "
                                      f"held, so nothing is lost meanwhile. To find the cause, {BUNDLE_STEP}."),
    "killed_run": Remedy("killed_run", "a run that was stopped before it finished", attempts=1, runs_before_alert=2,
                         next_step="The next run repairs what the stopped run left. If runs keep stopping at the same step, "
                                   f"{BUNDLE_STEP}."),
    "stale_lock": Remedy("stale_lock", "a lock left by a run that died", attempts=1, runs_before_alert=3,
                         next_step="Nothing to do: the lock was removed by its rule. If this happens every day, " + BUNDLE_STEP + "."),
    "schema": Remedy("schema", "a store missing a column", attempts=1, runs_before_alert=2,
                     next_step="Nothing to do: the store was brought up to date. If it happens again, " + BUNDLE_STEP + "."),
    "pressure": Remedy("pressure", "memory or time near the host's limit", attempts=1, runs_before_alert=3,
                       next_step="Nothing to do: RemitWatch spread its work over more runs. If the memory alert stays, " + BUNDLE_STEP + "."),
}

REPAIR_DDL = """
CREATE TABLE IF NOT EXISTS repair_log (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, run_id TEXT, remedy TEXT, target TEXT,
  attempt INTEGER, outcome TEXT, before TEXT, after TEXT, error TEXT, next_step TEXT);
CREATE INDEX IF NOT EXISTS ix_repair_log_at ON repair_log(at);
"""
REPAIR_KEEP_DAYS = 200


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(REPAIR_DDL)


class Held(RuntimeError):
    """A step or a file held after its remedy ran out of tries: the run carries on without it. `cause` is the last
    error; the registry remembers the hold."""

    def __init__(self, remedy: str, target: str, cause: BaseException | None, attempts: int, reason: str | None = None):
        self.remedy, self.target, self.cause, self.attempts = remedy, target, cause, attempts
        self.reason = reason or (f"{type(cause).__name__}: {runlog.scrub_text(str(cause), 200)}" if cause is not None else "held")
        super().__init__(f"{remedy} held {target} after {attempts} attempt(s): {self.reason}")


# ------------------------------------------------------------------------------------------------- what is transient
_TRANSIENT_WORDS = ("too many retries", "timed out", "timeout", "temporarily", "service unavailable", "bad gateway",
                    "gateway time", "connection reset", "connection aborted", "connection refused", "broken pipe",
                    "remote end closed", "eof occurred", "name resolution", "temporary failure", "try again", "rate limit",
                    "too_many_requests", "too_many_write_operations", "internal server error", "network is unreachable",
                    "no route to host", "ssh handshake", "error reading ssh protocol banner", "stalled")
# an HTTP status in a message, only as a status: after the word that names it ("failed 503", "answered 502", "HTTP 429",
# "(503"), or before its reason ("503 Server Error"); never a number that merely holds one ("row 5021", "found 429")
_STATUS_WORDS = re.compile(r"(?:\b(?:failed|answered|status|http|error|code|returned|got)[\s:=#]*|\()(?:429|5\d\d)\b|"
                           r"\b(?:429|5\d\d)\s+(?:server error|too many requests|service unavailable|bad gateway|gateway time)", re.I)
_NEVER_TRANSIENT = frozenset({"DropboxAuthError", "SignInRefused", "SignInUnclear", "FileServiceRefused", "DropboxNotFound",
                              "DropboxConflict", "Conflict", "PermissionError", "FileNotFoundError", "OutOfTime", "Held",
                              "ReportRefused",
                              # paramiko's refused sign-ins and changed server keys: SSHExceptions, never network trouble
                              "AuthenticationException", "BadAuthenticationType", "PasswordRequiredException",
                              "BadHostKeyException"})
_TRANSIENT_NAMES = frozenset({"ConnectionError", "Timeout", "ReadTimeout", "ConnectTimeout", "ChunkedEncodingError", "SSLError",
                              "ProtocolError", "RemoteDisconnected", "IncompleteRead", "SSHException", "NoValidConnectionsError",
                              "Stalled", "ProxyError"})
# a code bug, whatever its message says (a TypeError about a `timeout` argument, a ValueError about row 5021 or 429
# columns): its words are never read as network trouble
_CODE_BUGS = (TypeError, LookupError, AttributeError, NameError, ValueError, AssertionError, ArithmeticError, NotImplementedError,
              RecursionError, ImportError, SyntaxError, MemoryError, StopIteration, sqlite3.Error)
# the errors whose words can name network trouble: the network's own (OSError: sockets, TLS, requests' exceptions) and the
# clients' (the Dropbox client's RuntimeError, TriZettoError, FaxError)
_CARRIER_NAMES = frozenset({"TriZettoError", "FaxError", "DropboxError"})
NOT_RETRIED_FLAG = "_remitwatch_not_retried"


def status_of(exc: BaseException) -> int | None:
    """An HTTP status an exception carries (requests' HTTPError, a client error with status_code)."""
    for obj in (exc, getattr(exc, "response", None)):
        code = getattr(obj, "status_code", None) if obj is not None else None
        if isinstance(code, int):
            return code
    return None


def _not_retried_mark(exc: BaseException | None) -> bool:
    """Whether the exception, or one it was raised from or while handling, came out of a not_retried callback."""
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen and len(seen) < 8:
        if getattr(exc, NOT_RETRIED_FLAG, False):
            return True
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    return False


def is_transient(exc: BaseException, *, read: bool = False, _depth: int = 0) -> bool:
    """Network trouble worth trying again: a reset or refused connection, a timeout, a server's 5xx or 429, a line
    that went silent (by its type, its HTTP status, the network error it was raised from, or, for the network's own
    and the clients' errors only, its words). Never a refused password or token, a missing file, a conflict, a code
    bug, or anything raised inside a not_retried callback. read: the call only reads (a fax status, an inbox), so a
    fax error's "it may have been sent" does not apply to it."""
    if _depth > 4 or _not_retried_mark(exc):
        return False
    names = {c.__name__ for c in type(exc).__mro__}
    if names & _NEVER_TRANSIENT:
        return False            # a refused login, a conflict: trying again could lock the login or repeat the conflict
    if getattr(exc, "maybe_sent", False) and not read:
        return False            # a fax that may have gone out: only a call known to be a read is tried again
    code = status_of(exc)
    if code is not None:
        return code == 429 or code >= 500
    if isinstance(exc, (TimeoutError, ConnectionError, socket.timeout, EOFError)):
        return True
    if names & _TRANSIENT_NAMES:
        return True
    if isinstance(exc, _CODE_BUGS):
        return False
    if not (isinstance(exc, (OSError, RuntimeError)) or names & _CARRIER_NAMES):
        return False
    cause = exc.__cause__
    if cause is not None and cause is not exc and is_transient(cause, read=read, _depth=_depth + 1):
        return True             # a client's own error raised from the network's (TriZetto: "Could not reach ...")
    text = f" {exc}".lower()
    return any(w in text for w in _TRANSIENT_WORDS) or bool(_STATUS_WORDS.search(text))


def not_retried(fn: Callable) -> Callable:
    """Wrap a callback that a retried call makes (trizetto.pull hands the files it read to `handle`, the ingest): what
    the callback raises is its own (a code bug, a file that cannot be read), never the network's, so retry_call raises
    it at once instead of repeating the whole call. The exception keeps its type; it only carries a mark."""
    @functools.wraps(fn)
    def call(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except BaseException as exc:
            try:
                setattr(exc, NOT_RETRIED_FLAG, True)
            except (AttributeError, TypeError) as no_attr:      # an exception that takes no attribute: retried as before
                runlog.caught(no_attr, "not_retried", level="warning")
            raise
    return call


# ---------------------------------------------------------------------------------------------------------- registry
class Registry:
    """Attempts, back-off and holds per (remedy, target), kept across runs."""

    def __init__(self, conn: sqlite3.Connection | None = None, sink: Any = None, run: Any = None, now: datetime | None = None):
        self.conn, self.sink, self.run = conn, sink, run
        self.now = now
        self.state: dict = {"version": 1, "saved_at": None, "entries": {}}
        self.dirty = False
        # one registry is one run: a run without a run log still counts once (its own token), and the targets this run
        # held, with the plain tries they got since, live here (retry_call)
        self._token = "registry-" + secrets.token_hex(4)
        self._run_holds: dict[str, int] = {}
        self.load()

    def _run_key(self) -> str:
        return getattr(self.run, "run_id", None) or getattr(self, "_token", None) or "registry"

    # ---------------------------------------------------------------- persistence
    def _t(self) -> datetime:
        return self.now or runlog.utcnow()

    def load(self) -> dict:
        got = []
        if self.sink is not None:
            try:
                s = self.sink.read_json(STATE_FILE)
                if isinstance(s, dict):
                    got.append(s)
            except Exception as exc:        # noqa: BLE001  (an unreadable state starts afresh, and says so)
                runlog.caught(exc, "repair", level="warning")
        if self.conn is not None:
            try:
                r = self.conn.execute("SELECT value FROM meta WHERE key=?", (META_KEY,)).fetchone()
                s = json.loads(r[0]) if r and r[0] else None
                if isinstance(s, dict):
                    got.append(s)
            except (sqlite3.Error, ValueError) as exc:
                runlog.caught(exc, "repair", level="warning")
        if got:
            best = max(got, key=lambda s: s.get("saved_at") or "")
            if isinstance(best.get("entries"), dict):
                self.state = best
        return self.state

    def save(self) -> None:
        """Write the state to the logs folder and to the store's meta (the store is saved with the run)."""
        self.state["saved_at"] = runlog._iso(self._t())
        body = json.loads(json.dumps(self.state, default=str))
        if self.sink is not None:
            try:
                self.sink.write_json(STATE_FILE, body)
            except Exception as exc:        # noqa: BLE001
                runlog.caught(exc, "repair", level="warning")
        if self.conn is not None:
            try:
                self.conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
                self.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (META_KEY, json.dumps(body)))
                self.conn.commit()
            except sqlite3.Error as exc:
                runlog.caught(exc, "repair", level="warning")
        self.dirty = False

    # ---------------------------------------------------------------- entries
    @staticmethod
    def key(remedy: str, target: str) -> str:
        return f"{remedy}|{target}"

    def entry(self, remedy: str, target: str) -> dict:
        return self.state["entries"].get(self.key(remedy, target)) or {"remedy": remedy, "target": target, "attempts": 0,
                                                                       "failed_runs": 0, "status": "ok"}

    def _put(self, e: dict) -> dict:
        self.state["entries"][self.key(e["remedy"], e["target"])] = e
        self.dirty = True
        return e

    def held(self, remedy: str, target: str) -> bool:
        return self.entry(remedy, target).get("status") in ("held", "exhausted")

    def can_try(self, remedy: str, target: str) -> tuple[bool, str | None]:
        """Whether a held target may be tried again now (its back-off is over)."""
        e = self.entry(remedy, target)
        nxt = e.get("next_at")
        if nxt and runlog._iso(self._t()) < nxt:
            return False, f"waiting until {nxt} after {e.get('attempts')} failed attempt(s)"
        return True, None

    def record(self, remedy: str, target: str, outcome: str, *, attempt: int = 1, before: Any = None, after: Any = None,
               error: BaseException | dict | None = None, next_step: str | None = None) -> dict:
        """One remedy step: the run's event log and manifest, and a repair_log row (both scrubbed)."""
        err = runlog.error_info(error) if isinstance(error, BaseException) else error
        row = {"at": runlog._iso(self._t()), "run_id": getattr(self.run, "run_id", None), "remedy": remedy, "target": target,
               "attempt": attempt, "outcome": outcome, "before": before, "after": after,
               "error": {k: err.get(k) for k in ("type", "message", "where")} if isinstance(err, dict) else None,
               "next_step": next_step}
        row = runlog.scrub(row)
        if self.run is not None:
            self.run.repair(row)
        else:
            rl = runlog.current()
            if rl is not None:
                rl.repair(row)
        if self.conn is not None:
            try:
                ensure_schema(self.conn)
                self.conn.execute("INSERT INTO repair_log (at, run_id, remedy, target, attempt, outcome, before, after, error, next_step) "
                                  "VALUES (?,?,?,?,?,?,?,?,?,?)",
                                  (row["at"], row["run_id"], remedy, row["target"], attempt, outcome, runlog._jl(row["before"]),
                                   runlog._jl(row["after"]), runlog._jl(row["error"]), row["next_step"]))
                self.conn.commit()
            except sqlite3.Error as exc:
                runlog.caught(exc, "repair", level="warning")
        return row

    def succeeded(self, remedy: str, target: str, *, after: Any = None) -> dict:
        """The target worked (on its first try or after a remedy): its failures are cleared, and a target that had been
        failing gets a 'repaired' entry."""
        e = self.entry(remedy, target)
        if e.get("attempts") or e.get("failed_runs") or e.get("status") != "ok":
            self.record(remedy, target, "repaired", attempt=int(e.get("attempts") or 0) + 1,
                        before={"status": e.get("status"), "failed_runs": e.get("failed_runs"), "reason": e.get("reason")},
                        after=after or {"status": "ok"})
            self.state["entries"].pop(self.key(remedy, target), None)
            self.dirty = True
        return {"remedy": remedy, "target": target, "status": "ok"}

    def failed(self, remedy: str, target: str, exc: BaseException | None, *, reason: str | None = None, run_failed: bool = True,
               hold: bool | None = None, tries: int = 1, run_id: str | None = None) -> dict:
        """A failure counted against the target. run_failed: this ends the target's try in this run: it counts toward
        failed_runs and the back-off, once per run however many calls of the run were held (run_id: the run it counts
        for, by default this registry's own; a killed run is counted under its own id). hold: hold it now (None: when
        its remedy's attempts are used up). tries: the calls actually made (attempts counts them)."""
        spec = REMEDIES.get(remedy) or REMEDIES["network"]
        e = dict(self.entry(remedy, target))
        e["attempts"] = int(e.get("attempts") or 0) + max(1, int(tries or 1))
        now = self._t()
        e.setdefault("first_at", runlog._iso(now))
        e["last_at"] = runlog._iso(now)
        if exc is not None:
            info = runlog.error_info(exc)
            e["last_error"] = {"type": info["type"], "message": info["message"], "where": info["where"]}
        if reason:
            e["reason"] = runlog.scrub_text(reason, 300)
        elif exc is not None:
            e["reason"] = f"{type(exc).__name__}: {runlog.scrub_text(str(exc), 200)}"
        rid = run_id or self._run_key()
        if run_failed and e.get("counted_run") != rid:
            e["failed_runs"] = int(e.get("failed_runs") or 0) + 1
            e["counted_run"] = rid
            wait = spec.backoff[min(e["failed_runs"] - 1, len(spec.backoff) - 1)] if spec.backoff else 0
            e["next_at"] = runlog._iso(now + timedelta(seconds=wait)) if wait else None
        do_hold = hold if hold is not None else (remedy != "network" and e["failed_runs"] >= spec.attempts)
        if do_hold:
            e["status"] = "held"
        elif run_failed and e["failed_runs"] >= spec.runs_before_alert and remedy == "network":
            e["status"] = "exhausted"
        else:
            e["status"] = "retrying" if remedy == "network" else e.get("status") if e.get("status") in ("held",) else "retrying"
        if (e["status"] in ("held", "exhausted")) and e["failed_runs"] >= spec.runs_before_alert:
            e["alert"] = True
            e["next_step"] = spec.next_step
        return self._put(e)

    def release(self, remedy: str, target: str, *, by: str = "a person") -> bool:
        """End a hold (a person discarded or replaced the file)."""
        k = self.key(remedy, target)
        if k in self.state["entries"]:
            e = self.state["entries"].pop(k)
            self.dirty = True
            self.record(remedy, target, "released", before={"status": e.get("status")}, after={"status": "ok", "by": by})
            return True
        return False

    def open_problems(self) -> list[dict]:
        """Targets that could not be repaired automatically: held or out of attempts, with the next step."""
        out = []
        for e in self.state["entries"].values():
            if e.get("status") in ("held", "exhausted"):
                spec = REMEDIES.get(e.get("remedy") or "")
                out.append({"remedy": e.get("remedy"), "target": e.get("target"), "status": e.get("status"),
                            "what": spec.what if spec else e.get("remedy"), "reason": e.get("reason"),
                            "attempts": e.get("attempts"), "failed_runs": e.get("failed_runs"), "since": e.get("first_at"),
                            "last_at": e.get("last_at"), "alert": bool(e.get("alert")),
                            "next_step": e.get("next_step") or (spec.next_step if spec else BUNDLE_STEP)})
        return sorted(out, key=lambda x: x.get("last_at") or "", reverse=True)


def registry_for(rl: Any = None, *, conn: sqlite3.Connection | None = None, now: datetime | None = None) -> Registry:
    """The registry of a run: state kept beside the run's logs (rl.sink) and in the store's meta (conn). Killed-run
    counts that a later finished run has cleared (it passed the step) are cleared here, from the run index."""
    reg = Registry(conn=conn, sink=getattr(rl, "sink", None), run=rl, now=now)
    try:
        clear_killed_by_index(reg)
    except Exception as exc:        # noqa: BLE001  (the counts stay as they were; the next run tries again)
        runlog.caught(exc, "repair", level="warning")
    return reg


# ------------------------------------------------------------------------------------------------------------ remedies
def retry_call(fn: Callable, *args: Any, remedy: str = "network", target: str = "dropbox", registry: Registry | None = None,
               run: Any = None, until: float | None = None, sleep: Callable[[float], None] = time.sleep,
               attempts: int | None = None, delays: Iterable[float] | None = None,
               classify: Callable[[BaseException], bool] = is_transient, clock: Callable[[], float] = time.time, **kwargs: Any) -> Any:
    """Call fn(*args, **kwargs), trying again after a transient failure (is_transient) with the remedy's back-off, never
    waiting past `until` (seconds since the epoch: the run's own deadline; named so that a callee's own `deadline`
    argument passes through). Out of tries: the target is counted as failed for this run and Held is raised from the
    last error, so the caller's existing except block holds the step and the run carries on. A failure that is not
    transient is raised at once, unchanged."""
    spec = REMEDIES[remedy]
    n = attempts or spec.attempts
    waits = list(delays if delays is not None else spec.delays)
    run = run or (registry.run if registry is not None else None) or runlog.current()
    for i in range(n):
        try:
            value = fn(*args, **kwargs)
        except Exception as exc:        # noqa: BLE001  (sorted below: transient ones are tried again, the rest raised)
            if not classify(exc):
                raise
            wait = waits[min(i, len(waits) - 1)] if waits else 0
            last = i + 1 >= n or (until is not None and clock() + wait >= until)
            if last:
                if registry is not None:
                    registry.failed(remedy, target, exc)
                    registry.record(remedy, target, "held", attempt=i + 1, before={"tries": i + 1},
                                    after={"held": True, "carry_on": True}, error=exc, next_step=REMEDIES[remedy].next_step)
                elif run is not None:
                    run.repair(runlog.scrub({"remedy": remedy, "target": target, "attempt": i + 1, "outcome": "held"}))
                raise Held(remedy, target, exc, i + 1) from exc
            if registry is not None:
                registry.record(remedy, target, "retrying", attempt=i + 1, before={"tries": i + 1}, after={"wait_s": wait}, error=exc)
            elif run is not None:
                run.repair(runlog.scrub({"remedy": remedy, "target": target, "attempt": i + 1, "outcome": "retrying"}))
            if wait:
                sleep(wait)
            continue
        if i and registry is not None:
            registry.record(remedy, target, "repaired", attempt=i + 1, before={"failed_tries": i}, after={"ok": True})
        if registry is not None:
            e = registry.entry(remedy, target)
            if e.get("failed_runs") or e.get("status") != "ok":
                registry.succeeded(remedy, target)
        return value
    raise Held(remedy, target, None, n)         # n == 0


# calls that send something that must go once: never tried again by a wrapper, whatever the error (the fax line keeps
# its own send reservation and its own retries; an upload of the store is the cycle's to repeat)
NEVER_RETRIED = frozenset({"send", "send_fax", "send_packet", "upload", "upload_chunks", "put", "put_chunks", "move", "delete",
                           "delete_many"})


class _Resilient:
    """A client whose calls go through retry_call. A call that returns an iterator (a listing, a download in chunks) is
    tried again only until its first item arrives: once data has flowed, a failure is the caller's to handle. Calls in
    NEVER_RETRIED, and any call outside `only` when it is given, pass straight through."""

    def __init__(self, client: Any, target: str, opts: dict, only: frozenset | None = None):
        self._client, self._target, self._opts, self._only = client, target, opts, only

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._client, name)
        if name.startswith("_") or not callable(attr) or name in NEVER_RETRIED or (self._only is not None and name not in self._only):
            return attr
        opts, target = self._opts, self._target

        @functools.wraps(attr)          # the client's own signature stays visible (callers check it with inspect)
        def call(*args: Any, **kwargs: Any) -> Any:
            def first() -> Any:
                got = attr(*args, **kwargs)
                if hasattr(got, "__next__") and not isinstance(got, (bytes, str, dict, list, tuple)):
                    try:
                        head = next(got)
                    except StopIteration:
                        return iter(())
                    return _chain(head, got)
                return got
            return retry_call(first, target=target, **opts)
        return call


def _chain(head: Any, rest: Any):
    yield head
    yield from rest


def resilient(client: Any, target: str, *, registry: Registry | None = None, run: Any = None, until: float | None = None,
              sleep: Callable[[float], None] = time.sleep, attempts: int | None = None, delays: Iterable[float] | None = None,
              only: Iterable[str] | None = None) -> Any:
    """Wrap a Dropbox, TriZetto or SRFax client so each call is tried again after a transient failure. `only` names the
    calls to wrap (for the fax line: status, inbox, fetch, check, find_sent); a call that sends something that must go
    once (NEVER_RETRIED: a fax, an upload, a move) is never wrapped."""
    if client is None or isinstance(client, _Resilient):
        return client
    return _Resilient(client, target, {"registry": registry, "run": run, "until": until, "sleep": sleep,
                                       "attempts": attempts, "delays": delays}, frozenset(only) if only is not None else None)


def read_file(key: str, fn: Callable, *args: Any, registry: Registry, kind: str = "file", **kwargs: Any) -> Any:
    """Read one file through the unreadable_file remedy: a file held before is skipped (Held is raised at once, with
    the reason) until its back-off is over; a failure is counted (in this run) and the file is held after the
    remedy's attempts; a success clears its record. Other files are never affected. `key`: the file's content hash
    (never its name)."""
    target = f"{kind}:{str(key)[:16]}"
    if registry.held("unreadable_file", target):
        ok, why = registry.can_try("unreadable_file", target)
        e = registry.entry("unreadable_file", target)
        if not ok or e.get("status") == "held":
            raise Held("unreadable_file", target, None, int(e.get("attempts") or 0), reason=e.get("reason") or why)
    try:
        value = fn(*args, **kwargs)
    except Exception as exc:        # noqa: BLE001  (counted, logged, then raised or held)
        runlog.caught(exc, "read_file", inputs={"file": target})
        e = registry.failed("unreadable_file", target, exc)
        outcome = "held" if e.get("status") == "held" else "failed"
        registry.record("unreadable_file", target, outcome, attempt=e["attempts"], before={"failed_runs": e["failed_runs"] - 1},
                        after={"status": e["status"]}, error=exc,
                        next_step=REMEDIES["unreadable_file"].next_step if outcome == "held" else None)
        if outcome == "held":
            raise Held("unreadable_file", target, exc, e["attempts"]) from exc
        raise
    registry.succeeded("unreadable_file", target)
    return value


# the derived tables a check can rebuild from their sources (the names the hooks use)
DERIVED = {
    "ledger_summaries": "the EMA posting ledger's summaries (ema_ledger.refresh, parts A and B)",
    "link_map": "the map from remittance lines to EMA's claims (link.build_links)",
    "serving_copy": "the dashboard's copy of the store (cloud.build_serving)",
    "detection": "the items (a detection run)",
}


def rebuild_if_failing(name: str, check: Callable[[], Any], rebuild: Callable[[], Any], *, registry: Registry) -> dict:
    """A derived table that fails its check is rebuilt once from its sources and checked again. Returns {"ok", "rebuilt",
    "safe_mode", "before", "after"}: safe_mode True means the rebuild did not help (or was not allowed yet) and the run
    should hold its automatic closures (selfcheck)."""
    def run_check() -> dict:
        try:
            got = check()
        except Exception as exc:        # noqa: BLE001  (a check that crashes counts as failing, with its error)
            runlog.caught(exc, "derived_check", inputs={"table": name})
            return {"ok": False, "error": type(exc).__name__}
        return got if isinstance(got, dict) else {"ok": bool(got)}
    before = run_check()
    if before.get("ok"):
        if registry.entry("derived_table", name).get("status") != "ok":
            registry.succeeded("derived_table", name, after=before)
        return {"ok": True, "rebuilt": False, "safe_mode": False, "before": before}
    ok, why = registry.can_try("derived_table", name)
    if not ok:
        registry.record("derived_table", name, "waiting", before=before, after={"why": why})
        return {"ok": False, "rebuilt": False, "safe_mode": True, "before": before, "why": why}
    err = None
    try:
        rebuild()
    except Exception as exc:        # noqa: BLE001  (a rebuild that crashes: safe mode, with its error logged)
        runlog.caught(exc, "derived_rebuild", inputs={"table": name})
        err = exc
    after = run_check() if err is None else {"ok": False, "error": type(err).__name__}
    if after.get("ok"):
        registry.record("derived_table", name, "repaired", before=before, after=after)
        registry.succeeded("derived_table", name, after=after)
        return {"ok": True, "rebuilt": True, "safe_mode": False, "before": before, "after": after}
    registry.failed("derived_table", name, err, reason=f"{DERIVED.get(name, name)} still fails its check after a rebuild", hold=True)
    registry.record("derived_table", name, "gave_up", before=before, after=after, error=err,
                    next_step=REMEDIES["derived_table"].next_step)
    return {"ok": False, "rebuilt": True, "safe_mode": True, "before": before, "after": after}


def note_killed_runs(registry: Registry, killed: list[dict], repaired: dict | None = None) -> int:
    """Runs the platform stopped (runlog.find_killed_runs) and what this run's repair paths did about them
    (cloud.repair_ledger's answer, the Production attempts): logged, and counted toward an alert when runs keep
    stopping."""
    for k in killed:
        target = f"step:{k.get('last_step') or 'unknown'}"
        e = registry.failed("killed_run", target, None, reason=f"run {k.get('run_id')} stopped during {k.get('last_step')}",
                            hold=False)
        registry.record("killed_run", target, "repaired" if repaired else "noted", attempt=e["attempts"],
                        before={"killed_run": k.get("run_id"), "last_step": k.get("last_step"), "peak_mb": k.get("peak_mb")},
                        after=repaired or {"next": "the cycle's own repair paths run"},
                        next_step=REMEDIES["killed_run"].next_step if e.get("failed_runs", 0) >= REMEDIES["killed_run"].runs_before_alert else None)
        if e.get("failed_runs", 0) >= REMEDIES["killed_run"].runs_before_alert:
            e["status"], e["alert"], e["next_step"] = "exhausted", True, REMEDIES["killed_run"].next_step
            registry._put(e)
    return len(killed)


def note_run_finished(registry: Registry, last_step_ok: Iterable[str] = ()) -> None:
    """A run that finished clears the killed-run counts of the steps it passed."""
    for e in list(registry.state["entries"].values()):
        if e.get("remedy") == "killed_run" and str(e.get("target", "")).split(":", 1)[-1] in set(last_step_ok):
            registry.succeeded("killed_run", e["target"])


def note_stale_lock(registry: Registry, removed: Iterable[str], how: str) -> int:
    """Locks of dead runs removed by the existing rules (pipeline.clear_locks_left_behind at a server's start,
    clear_lock_of_pid after a child, cloud.acquire taking over a stale lock.json): logged with before and after."""
    n = 0
    for key in removed:
        registry.record("stale_lock", str(key), "repaired", before={"lock": str(key), "holder": "a run that ended"},
                        after={"removed": True, "how": how})
        n += 1
    return n


def log_stale_locks(removed: Iterable[str], how: str, *, db_path: str | None = None, state: Any = None) -> int:
    """Outside a run (a server's start, the scheduler after a child ended, a serverless lock taken over): log the locks a
    rule removed, in the store's repair_log (db_path) and the logs folder's registry state. Never raises."""
    removed = [str(k) for k in removed or []]
    if not removed:
        return 0
    conn = None
    try:
        if db_path:
            conn = sqlite3.connect(db_path, timeout=30)
        sink = runlog.sink_for(db_path=db_path, state=state)
        reg = Registry(conn=conn, sink=sink, run=runlog.current())
        n = note_stale_lock(reg, removed, how)
        reg.save()
        return n
    except Exception as exc:        # noqa: BLE001  (the lock is gone either way; only its log line could not be written)
        runlog.caught(exc, "stale_lock", level="warning")
        return 0
    finally:
        if conn is not None:
            conn.close()


def check_schema(conn: sqlite3.Connection, ensure: Callable[[sqlite3.Connection], Any], expected: dict[str, Iterable[str]], *,
                 registry: Registry) -> dict:
    """Columns a table should have and does not: the migration (ensure) runs, and what was missing before and after it is
    logged. expected: {table: [columns]}."""
    def missing() -> dict:
        out = {}
        for table, cols in expected.items():
            have = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
            gone = sorted(set(cols) - have)
            if gone:
                out[table] = gone
        return out
    before = missing()
    if not before:
        return {"ok": True, "missing": {}}
    err = None
    try:
        ensure(conn)
    except Exception as exc:        # noqa: BLE001
        runlog.caught(exc, "schema", inputs={"tables": sorted(before)})
        err = exc
    after = missing()
    for table in before:
        if table in after:
            registry.failed("schema", table, err, reason=f"columns still missing: {', '.join(after[table])}", hold=True)
            registry.record("schema", table, "gave_up", before={"missing": before[table]}, after={"missing": after[table]},
                            error=err, next_step=REMEDIES["schema"].next_step)
        else:
            registry.record("schema", table, "repaired", before={"missing": before[table]}, after={"missing": []})
    return {"ok": not after, "missing": after, "before": before}


def note_pressure(registry: Registry, actions: Iterable[dict], *, reading: dict | None = None) -> int:
    """Self-regulation actions (selfcheck: spread work over runs, archive at the tighter horizon, leave optional steps):
    logged with the memory or time reading that caused them."""
    n = 0
    for a in actions:
        registry.record("pressure", str(a.get("target") or a.get("action") or "memory"), "repaired",
                        before=reading or {}, after={k: v for k, v in a.items() if k != "target"})
        n += 1
    return n


def sweep_reports(conn: sqlite3.Connection, registry: Registry, since: str) -> int:
    """Reports the readers held or refused in this run (ema_report_files): each becomes a held unreadable_file entry
    with its reason, so the Data tab and the alerts say which file (by hash and kind) and what to do. The readers
    already keep the other files going."""
    try:
        rows = conn.execute("""SELECT file_id, report, status, status_reason, attempts FROM ema_report_files
                               WHERE status IN ('held','refused','failed') AND
                                     (COALESCE(received_at,'') >= ? OR COALESCE(applied_at,'') >= ? OR COALESCE(decided_at,'') >= ?)""",
                            (since, since, since)).fetchall()
    except sqlite3.Error:
        return 0
    n = 0
    for fid, report, status, reason, attempts in rows:
        target = f"{report or 'report'}:{str(fid).replace('dropbox:', '')[:16]}"
        if registry.held("unreadable_file", target):
            continue
        registry.failed("unreadable_file", target, None, reason=f"{status}: {reason or 'no reason given'}", hold=True)
        registry.record("unreadable_file", target, "held", attempt=int(attempts or 1), before={"status": "received"},
                        after={"status": status, "reason": runlog.scrub_text(reason or "", 200)},
                        next_step=REMEDIES["unreadable_file"].next_step)
        n += 1
    return n


# ----------------------------------------------------------------------------------------------- reading the record
def _state_from_meta(conn: sqlite3.Connection | None) -> dict:
    if conn is None:
        return {}
    try:
        r = conn.execute("SELECT value FROM meta WHERE key=?", (META_KEY,)).fetchone()
        s = json.loads(r[0]) if r and r[0] else {}
        return s if isinstance(s, dict) else {}
    except (sqlite3.Error, ValueError):
        return {}


def open_problems(conn: sqlite3.Connection | None = None, sink: Any = None) -> list[dict]:
    """What could not be repaired automatically, newest first (the Data tab's panel and the alerts)."""
    reg = Registry.__new__(Registry)
    reg.conn, reg.sink, reg.run, reg.now, reg.dirty = conn, sink, None, None, False
    reg.state = {"version": 1, "saved_at": None, "entries": {}}
    reg.load()
    return reg.open_problems()


def alert_candidates(conn: sqlite3.Connection) -> list[dict]:
    """Alerts for alerts.py (kind SELF_REPAIR_FAILED): one per target whose remedy ran out, with what failed, where,
    what was tried and the exact next step. Cleared by itself once the target works again."""
    out = []
    for p in open_problems(conn=conn):
        if not p.get("alert"):
            continue
        tried = f"{p.get('attempts')} attempt(s) over {p.get('failed_runs')} run(s)"
        title = (f"RemitWatch could not repair {p.get('what')} by itself ({p.get('target')}): {p.get('reason') or 'see the Data tab'}. "
                 f"Tried: {tried}. Next step: {p.get('next_step')}")
        out.append({"kind": "SELF_REPAIR_FAILED", "severity": "high", "title": runlog.scrub_text(title, 600),
                    "detail": runlog.scrub({k: p.get(k) for k in ("remedy", "target", "status", "reason", "attempts", "failed_runs",
                                                                 "since", "last_at", "next_step")}),
                    "key": ["repair", str(p.get("remedy")), str(p.get("target"))], "n": p.get("attempts")})
    return out


def recent(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    """The last self-repair attempts (repair_log), newest first."""
    try:
        rows = conn.execute("""SELECT at, run_id, remedy, target, attempt, outcome, before, after, error, next_step FROM repair_log
                               ORDER BY at DESC, id DESC LIMIT ?""", (int(limit),)).fetchall()
    except sqlite3.Error:
        return []
    return [{"at": r[0], "run_id": r[1], "remedy": r[2], "target": r[3], "attempt": r[4], "outcome": r[5],
             "before": runlog._loads(r[6]), "after": runlog._loads(r[7]), "error": runlog._loads(r[8]), "next_step": r[9]} for r in rows]


def prune(conn: sqlite3.Connection, *, now: datetime | None = None) -> int:
    ensure_schema(conn)
    cut = runlog._iso((now or runlog.utcnow()) - timedelta(days=REPAIR_KEEP_DAYS))
    c = conn.execute("DELETE FROM repair_log WHERE at < ?", (cut,))
    conn.commit()
    return c.rowcount
