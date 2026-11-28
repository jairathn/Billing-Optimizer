"""
Serverless operation (Vercel and similar hosts): nothing on disk survives and no process outlives a request, so
everything that must last lives in the state folder (remitwatch.state), normally a private folder in the practice's
own Dropbox.

    store.sqlite.gz      the whole store, compact, gzip. Loaded into memory for one cycle and saved back.
    serving.sqlite.gz    what the dashboard reads: findings, rates, alerts and the claim detail behind each finding.
    actions/*.json       the journal: each status change or acknowledgement made in the dashboard, one small file.
    packets/<finding>/   appeal packets (metadata and PDF).
    status.json          the last cycle, for the dashboard.     cursor.json   the Dropbox position.
    serving.json         which version of the store the dashboard's copy was built from.
    notified.json        which alerts have been mailed and when the last report went out.
    lock.json            held by the cycle that is running.

One cycle (run_cloud_cycle): take the lock, load the store, fold the journal into it, pull new files from the data
folder, ingest, detect, raise alerts, save the store, then the dashboard's copy built from it, then send mail, write
the status and release the lock. The store is saved first because it is the record: if a run dies between the two
saves, the next run sees that the dashboard's copy is behind and rebuilds it. A cycle has a
time budget. When it cannot finish, it saves what it has and the caller starts a fresh invocation to continue, so a
first load of tens of thousands of files completes across several short runs.

The dashboard never waits on a cycle. Each instance keeps a copy of serving.sqlite.gz in its scratch space, checks for
a newer one every 45 seconds, and lays the journal over it so a status change shows at once.

Memory is the limit that matters: the whole store is held in memory while a cycle runs (about 0.56 GB for almost three
years of remittances) and detection or saving needs about as much again (a full re-analysis peaks near 1.4 GB). Each
cycle records its peak in status.json.
"""
from __future__ import annotations

import gc
import hashlib
import io
import json
import os
import secrets
import shutil
import sqlite3
import threading
import time
import zipfile
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Iterator

from . import actions as actions_mod
from . import trizetto
from . import activity, runtime
from .state import Conflict, state_from_env

RAW = "store.sqlite.gz"
SERVING = "serving.sqlite.gz"
STATUS = "status.json"
LOCK = "lock.json"
CURSOR = "cursor.json"
SERVING_MARK = "serving.json"       # which version of the store the dashboard's copy was built from, and by which build
SERVING_BUILD = "12"                # bump when SERVING_TABLES changes: a copy made by another build is made again (7: the
                                    # release's heirs, or push 2's EMA reports; 8: what the A/R's billing notes say, and the
                                    # 835 claims of whole bills; 9: both together; 10: the runs' logs, the decision log, the
                                    # self-check's lists and the archive's index; 11: what the payers' recoupments took from
                                    # each payment, ledger_taken; 12: the 837 sends of the items' bills)
MARKS = "notified.json"             # which alerts have been mailed, and when the last report went out
PRESS = "press.json"                # the owner's decision 1: when someone last pressed "Run the analysis now" (and who, as a
                                    # fingerprint), so a second press before the run has taken its lock starts nothing
PRESS_HOLD_SECONDS = 90             # a press this recent counts as a run going, until the run's own lock is there
PRESS_TRIGGERS = ("press", "button")    # the runs a person's press starts (the Data tab's, the Feed's and the Upload page's)
PRESS_ONLY_TEXT = ("This site analyses only when someone presses Run the analysis now (the Data tab). The practice's "
                   "server analyses on its own every day and is where the billers work: what is recorded on this site is "
                   "not on the server.")


def ema_reads_text() -> str:
    """What this site reads of EMA's reports when it is not both, and that the server's lists can differ for it (the
    integrator's decision 6: a serverless host reads the A/R only, remitwatch.ema_reports.switch, so a Production file
    waits and the image holds none of its rows). '' when the site reads both."""
    from . import ema_reports
    s = ema_reports.switch()
    if s == "ar":
        return ("This site reads EMA's A/R report only. The practice's server reads EMA's Production report as well, so "
                "its lists can differ from this site's.")
    if s == "off":
        return ("This site reads neither of EMA's reports. The practice's server reads them, so its lists can differ from "
                "this site's.")
    return ""


def press_only_text() -> str:
    """PRESS_ONLY_TEXT and what this site reads of EMA's reports (ema_reads_text): what the setup page says."""
    more = ema_reads_text()
    return PRESS_ONLY_TEXT + (" " + more if more else "")
ACTIONS = "actions"
PACKETS = "packets"
_PROCESS_START = time.time()
FAILED_BACKOFF_SECONDS = 6 * 3600   # after a failed run, notifications start nothing for this long (the daily run still does)
UNFINISHED_BACKOFF_SECONDS = 3600   # the same after a run that could not fetch a file or left work for a follow-up run
MAX_HOPS = 30                       # runs that may follow one another without a person or the scheduler in between
MAX_FILE_BYTES = 60 * 1024 * 1024    # larger files in the data folder are not read
NEEDED_MEMORY_MB = 1600          # a detection run on almost three years of remittances peaks at about 1.5 GB; Vercel's standard function has 2 GB
DEFER_DETECT_AFTER_FILES = int(os.environ.get("REMITWATCH_DEFER_DETECT_FILES", "3000"))
REFRESH_SECONDS = 45            # how often a dashboard instance looks for a newer serving copy
RETRY_SECONDS = 5               # how soon it looks again when storage did not answer and it has no copy at all
JOURNAL_SECONDS = 15            # how often it looks for actions recorded by other instances

CLOUD_DDL = """
CREATE TABLE IF NOT EXISTS dropbox_seen (id TEXT PRIMARY KEY, content_hash TEXT, path TEXT, at TEXT);
CREATE INDEX IF NOT EXISTS ix_dbx_hash ON dropbox_seen(content_hash);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def lock_stale_seconds() -> int:
    """A cycle cannot outlive the function's maximum duration, which the time budget sits just under. A lock older than
    the budget plus two minutes was left by a run that was killed (out of memory or out of time)."""
    return max(runtime.max_seconds() + 120, 420)


def peak_mb() -> int | None:
    """The most memory this run has held so far, in MB (runtime.peak_mb: since the run's start set the mark back, so a warm
    instance's earlier invocations do not count)."""
    try:
        return runtime.peak_mb()
    except Exception as exc:        # no measure on this platform: the run records none
        from . import runlog
        runlog.caught(exc, "peak_mb", level="warning")
        return None


def _age_seconds(iso: str | None) -> float:
    if not iso:
        return 1e9
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(iso.replace("Z", "+00:00"))).total_seconds()
    except ValueError:
        return 1e9


def _read_json(state, name: str) -> dict:
    try:
        j = json.loads(state.get(name) or b"{}")
        return j if isinstance(j, dict) else {}
    except ValueError:
        return {}


def _write_json(state, name: str, value: dict) -> None:
    state.put(name, json.dumps(value, default=str).encode())


# --------------------------------------------------------------------------- compression

def gunzip_chunks(chunks: Iterable[bytes]) -> bytearray:
    d = zlib.decompressobj(31)
    out = bytearray()
    for c in chunks:
        out += d.decompress(c)
    out += d.flush()
    return out


def gzip_chunks(data, level: int = 1, piece: int = 8 << 20) -> Iterator[bytes]:
    co = zlib.compressobj(level, zlib.DEFLATED, 31)
    mv = memoryview(data)
    for i in range(0, len(mv), piece):
        b = co.compress(mv[i:i + piece])
        if b:
            yield b
    yield co.flush()


# --------------------------------------------------------------------------- lock

LAST_TAKEOVER: dict | None = None   # the stale lock the last acquire() took over: {since, run_id, started} of the run that held it


def acquire(state, run_id: str | None = None) -> str | None:
    """Take the cycle lock; None when another cycle holds it. The lock names the run that holds it (`run_id`), so a run
    that finds it left behind can tell a run the platform stopped from one that finished and could not remove it. A lock
    older than any run can be is taken over, but only if nobody else took it first (LAST_TAKEOVER then says when it was
    taken and by which run)."""
    global LAST_TAKEOVER
    LAST_TAKEOVER = None
    token = secrets.token_hex(8)
    body = json.dumps(dict({"token": token, "started": _now()}, **({"run_id": run_id} if run_id else {}))).encode()
    for _ in range(2):
        try:
            state.put(LOCK, body, mode="add")
            return token
        except Conflict:
            m = state.meta(LOCK)
            if m is None:
                continue                    # released between the two calls: try once more
            if _age_seconds(m.get("modified")) < lock_stale_seconds():
                return None
            try:
                held = _read_json(state, LOCK)      # which run left it (an older build's lock names none)
            except Exception as exc:        # noqa: BLE001  (the takeover goes on; the kill is then counted by the lock's time)
                from . import runlog
                runlog.caught(exc, "acquire:read_lock", level="warning")
                held = {}
            try:
                state.put(LOCK, body, mode="update", rev=m["rev"])
                LAST_TAKEOVER = {"since": m.get("modified"), "run_id": held.get("run_id"), "started": held.get("started")}
                return token
            except Conflict:
                return None
    return None


def release(state, token: str) -> None:
    try:
        raw = state.get(LOCK)
        if raw and json.loads(raw).get("token") == token:
            state.delete(LOCK)
    except Exception as exc:        # a lock that cannot be released goes stale and is taken over
        from . import runlog
        runlog.caught(exc, "release", level="warning")


def lock_state(state, named: bool = False) -> tuple[dict | None, dict | None]:
    """(running, stalled): a cycle in progress, or a lock left behind by one that died without finishing. With `named`, a
    lock left behind says which run took it and when (its run_id and started), for run_killed."""
    m = state.meta(LOCK)
    if not m:
        return None, None
    info = {"since": m.get("modified")}
    if _age_seconds(m.get("modified")) < lock_stale_seconds():
        return info, None
    if named:
        held = _read_json(state, LOCK)
        info.update({k: held.get(k) for k in ("run_id", "started") if held.get(k)})
    return None, info


def _when(iso: str | None) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def run_killed(stalled: dict | None, last: dict | None) -> bool:
    """Whether a lock left behind (lock_state's stalled) was left by a run the platform stopped (out of memory or time):
    the run that took it wrote no status of its own. A run that finished and could not remove its lock wrote its status
    (it names the run, or, for a lock that names none, it is newer than the lock)."""
    if not stalled:
        return False
    last = last or {}
    rid = stalled.get("run_id")
    if rid:
        return last.get("run_id") != rid
    # an older build's lock names no run: its run finished if a status was written after the lock was taken (a run
    # takes seconds at least, so a status of the same second is the run before it)
    took, done = _when(stalled.get("started") or stalled.get("since")), _when(last.get("finished"))
    return not (took and done and done > took)


def running(state) -> dict | None:
    return lock_state(state)[0]


def _takeover_kill(takeover: dict | None, prev: dict | None, killed: list[dict], rl) -> dict | None:
    """The run that left the stale lock this run took over (LAST_TAKEOVER), as a run the platform stopped, counted once: not
    when its status says it finished (it only could not remove its lock: run_killed), not when its open marker was found
    already (`killed`), and, when its marker is still open, by that marker, recorded as killed now (runlog.claim_killed),
    so no later run counts the same run again under another name. None when there is nothing to count."""
    from . import runlog
    if not takeover:
        return None
    rid, took = takeover.get("run_id"), takeover.get("started") or takeover.get("since")
    if rid:
        if (prev or {}).get("run_id") == rid or any(k.get("run_id") == rid for k in killed):
            return None
        claimed = runlog.claim_killed(rl.sink, run_id=rid, exclude=rl.run_id)
    else:
        # an older build's lock names no run: its run is the one whose marker started as the lock was taken
        t = _when(took)
        if t is not None and any(runlog.started_near(k.get("started_at"), t) for k in killed):
            return None
        claimed = runlog.claim_killed(rl.sink, started=took, exclude=rl.run_id)
        if not claimed and not run_killed({"started": took, "since": takeover.get("since")}, prev):
            return None
    return claimed or {"run_id": rid, "started_at": took, "last_step": None}


def _hhmm(iso: str | None) -> str:
    """'2026-11-26T19:05:41+00:00' as people read it: '2026-11-26 19:05 UTC'."""
    return f"{str(iso)[:10]} {str(iso)[11:16]} UTC" if iso and len(str(iso)) >= 16 else "a moment ago"


def run_going(state) -> dict | None:
    """A run going now, as a press sees it: the lock a run holds, or a press made so recently that the run it asked for
    has not taken the lock yet (a run that started after the press, whether or not it has ended, has answered it)."""
    going = running(state)
    if going:
        return going
    p = _read_json(state, PRESS)
    at = p.get("at")
    if at and not p.get("answered") and _age_seconds(at) < PRESS_HOLD_SECONDS:
        return {"since": at, "pressed": True}
    return None


def _answer_press(state, run_id: str) -> None:
    """A run that has taken the lock answers the press made before it (it reads everything new and, for a press, analyses):
    a press after this moment asks for a run of its own once this one has ended."""
    try:
        p = _read_json(state, PRESS)
        if p.get("at") and not p.get("answered"):
            _write_json(state, PRESS, dict(p, answered=run_id))
    except Exception as exc:        # noqa: BLE001  (the press's hold ends by itself after PRESS_HOLD_SECONDS)
        from . import runlog
        runlog.caught(exc, "answer_press", level="warning")


def press(state, base_url: str | None, *, who: str | None = None, trigger: str = "press") -> dict:
    """The owner's decision 1: on this host an analysis runs only when a signed-in person presses for it. The press starts
    the same chain the daily scheduler started (/api/cron/cycle with the scheduler's secret: the lock, the journal folded,
    the uploads, the Dropbox delta, the analysis, the saves, and the chain's own next runs), with the analysis forced, so
    the lists move with the day even when no file has arrived. A run already going (its lock, or a press a moment ago that
    has not reached it yet) is said, and nothing more is started. `who` is a fingerprint of the sign-in, never a name.
    Answers {'started': True | False | None, 'running': {...} | None, 'text'}; None: the run could not be asked for."""
    going = run_going(state)
    if going:
        return {"started": False, "running": going,
                "text": (f"A run is already going (asked for or started {_hhmm(going.get('since'))}). It finishes by "
                         "itself, and the lists change when it has. Nothing more was started.")}
    asked = trigger_cycle(base_url, trigger, force=1)
    if not asked:
        # why the site's own call did not start the run (trigger_cycle says), so the person is sent to what is wrong
        why, status = getattr(asked, "why", None), getattr(asked, "status", None)
        if not why:
            why, status = not_started_why(base_url)
        return {"started": None, "running": None, "text": why, "status": status or 502}
    try:
        _write_json(state, PRESS, {"at": _now(), "by": who, "trigger": trigger})
    except Exception as exc:        # noqa: BLE001  (the run was asked for; a second press is then turned away by its lock)
        from . import runlog
        runlog.caught(exc, "press_mark", level="warning")
    return {"started": True, "running": None,
            "text": ("Started: the run reads what is new in Dropbox and the uploads, then analyses everything again. On this "
                     "site that takes a few minutes; the lists change when it has finished.")}


# --------------------------------------------------------------------------- the store, in memory

# The store image a run saved, by the gzip file's size and sha256, is recorded in status.json at once (IMAGE_KEY). A run
# loads only that image: a missing one where earlier runs left status.json or cursor.json, or another one (an older copy
# put back, a copy cut short), stops the run before anything changes. Starting from it would save it as the practice's.
IMAGE_KEY = "image"
IMAGE_MISSING = ("The store image is missing from the state folder. Nothing was changed. Put it back, or restore it from "
                 "Dropbox's version history.")
IMAGE_OTHER = ("The store image in the state folder is not the one the last run saved. Nothing was changed. Restore the newest "
               "version from Dropbox's version history, or, if this copy was put back on purpose, accept it: docs/OPERATIONS.md, "
               "Recovering the store.")
IMAGE_UNREADABLE = ("The store image in the state folder cannot be read (it may be cut short). Nothing was changed. Restore "
                    "another version from Dropbox's version history.")


class StoreImageProblem(RuntimeError):
    """The state folder's store image cannot be taken as the practice's store: the run stops before changing anything."""


def _counted(chunks: Iterable[bytes], h, size: list, then=None) -> Iterator[bytes]:
    """The chunks as they pass, added to a sha256 and a byte count (size[0]); then(), when given, once the last one has
    been taken (an upload has every byte by then and has not committed it yet)."""
    for c in chunks:
        h.update(c)
        size[0] += len(c)
        yield c
    if then is not None:
        then()


def load_raw(state, check: bool = True, status: dict | None = None):
    """The store from the state folder, and whether there was one. check (every run does): with no image while
    status.json or cursor.json show earlier runs, or with an image other than the one the last save recorded, this
    raises StoreImageProblem. A first load needs both files absent. The image a save was writing when its answer was
    lost (the pending record save_raw writes before the commit) is the last save too: with status (the run's own,
    which holds the cycle lock) it is recorded as the last save, in status.json and in status."""
    from .store import Store
    chunks = state.get_chunks(RAW)
    if chunks is None:
        if check and _saved_before(state):
            raise StoreImageProblem(IMAGE_MISSING)
        # Start from a serialized image even when it is empty. SQLite keeps such a database in one block of memory and
        # can hand that block out when the store is saved; a plain in-memory database has to be copied twice instead.
        blank = Store(":memory:", compact=True)
        image = blank.to_bytes()
        blank.close()
        return Store.from_bytes(image), False
    h, size = hashlib.sha256(), [0]
    data = gunzip_chunks(_counted(chunks, h, size))
    want = (_read_json(state, STATUS).get(IMAGE_KEY) or {}) if check else {}
    if isinstance(want, dict) and want.get("sha256") and (want["sha256"] != h.hexdigest() or want.get("size") != size[0]):
        pend = want.get("pending")
        if not (isinstance(pend, dict) and pend.get("sha256") == h.hexdigest() and pend.get("size") == size[0]):
            del data
            gc.collect()
            raise StoreImageProblem(IMAGE_OTHER)
        if status is not None:
            # Dropbox committed the last save although its answer was lost: from now on it is the image to load
            status[IMAGE_KEY] = dict(pend, promoted=_now())
            record_image(state, status[IMAGE_KEY])
    store = Store.from_bytes(data)
    del data
    gc.collect()
    return store, True


def _saved_before(state) -> bool:
    """Whether an earlier run saved a store image here: the Dropbox cursor (written only after the store is saved), the
    record of the last save in status.json (IMAGE_KEY, which every run keeps), or a status.json written by a release from
    before that record (the size of the store it saved, its totals). A status.json left by runs that had nothing to read
    proves nothing: the next run is still a first load. One that cannot be read counts as proof: nobody can tell that no
    store existed."""
    if state.meta(CURSOR) is not None:
        return True
    if state.meta(STATUS) is None:
        return False
    try:
        st = json.loads(state.get(STATUS) or b"{}")
    except ValueError:
        return True
    if not isinstance(st, dict):
        return True
    store, totals = st.get("store"), st.get("totals")
    return bool(st.get(IMAGE_KEY) or (isinstance(store, dict) and store.get("gz_bytes"))
                or (isinstance(totals, dict) and totals.get("remit_files")))


def save_raw(state, store, log=print, status: dict | None = None) -> dict:
    """Save the store image, and record it at once (record_image). The result carries IMAGE_KEY: the saved file's size and
    sha256. Before the upload is committed, status.json names the image as pending (and so does status, the run's own,
    when given): an upload Dropbox commits but whose answer is lost is then the image the next run loads (load_raw). The
    image is the store's one block (Store.image: no third copy of a store held in memory at the save's peak)."""
    h, size = hashlib.sha256(), [0]

    def pending() -> None:
        record_pending(state, {"size": size[0], "sha256": h.hexdigest(), "saved": _now()}, status)
    try:
        with store.image() as data:
            image_mb = round(len(data) / 1048576, 1)
            meta = dict(state.put_chunks(RAW, _counted(gzip_chunks(data), h, size, then=pending)) or {})
    finally:
        gc.collect()
    meta["image_mb"] = image_mb             # the store's size in memory: its share of the one-block ceiling (Store.image)
    meta[IMAGE_KEY] = {"size": size[0], "sha256": h.hexdigest(), "saved": _now()}
    record_image(state, meta[IMAGE_KEY], log)
    if status is not None:
        # the run's own status, which its end writes over status.json whatever happened: a save in the middle of a run
        # (the Production slot's) must not be followed by the record of the image before it, or the next run refuses a
        # good store
        status[IMAGE_KEY] = meta[IMAGE_KEY]
    return meta


def record_pending(state, image: dict, status: dict | None = None) -> None:
    """The image a save is about to commit, beside the record of the last one in status.json (a failure here stops the
    save before its commit). Only where an image is recorded: without one, load_raw checks none."""
    cur = _read_json(state, STATUS)
    last = cur.get(IMAGE_KEY)
    if not (isinstance(last, dict) and last.get("sha256")):
        return
    cur[IMAGE_KEY] = dict(last, pending=image)
    _write_json(state, STATUS, cur)
    if status is not None:
        status[IMAGE_KEY] = cur[IMAGE_KEY]


def record_image(state, image: dict | None, log=print) -> None:
    """The image just saved, written into status.json at once: a run stopped after its save (out of time or memory) must
    not leave the record behind the image, or the next run would refuse a good store. The run's own status, written at
    its end, carries it as well."""
    if not image:
        return
    try:
        cur = _read_json(state, STATUS)
        cur[IMAGE_KEY] = image
        _write_json(state, STATUS, cur)
    except Exception as exc:
        log(f"the saved store image could not be recorded in the status yet ({type(exc).__name__}); the run's status records it")


def accept_image(state) -> str:
    """A person's decision after a stop (docs/OPERATIONS.md, Recovering the store): the store image now in the state folder
    is the store, and the next run loads it. It must open as a store. Returns what was done, in words."""
    from .store import Store
    chunks = state.get_chunks(RAW)
    if chunks is None:
        raise StoreImageProblem(IMAGE_MISSING)
    h, size = hashlib.sha256(), [0]
    try:
        data = gunzip_chunks(_counted(chunks, h, size))
        store = Store.from_bytes(data)
        del data
        n = store.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        store.close()
    except (zlib.error, sqlite3.Error) as exc:
        raise StoreImageProblem(IMAGE_UNREADABLE) from exc
    cur = _read_json(state, STATUS)
    cur[IMAGE_KEY] = {"size": size[0], "sha256": h.hexdigest(), "saved": None, "accepted": _now()}
    _write_json(state, STATUS, cur)
    return f"The store image in the state folder is the store from now on: it holds {n:,} remittances. The next run loads it."


# --------------------------------------------------------------------------- the journal

def record_action(state, action: dict) -> str:
    # each entry carries its own id: replays tell entries apart by it, not by their second, status and person (three
    # changes by one person within one second are three changes)
    action = dict(action, at=action.get("at") or _now(), entry=action.get("entry") or secrets.token_hex(8))
    name = f"{ACTIONS}/{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')}-{secrets.token_hex(3)}.json"
    state.put(name, json.dumps(action).encode())
    return name


def read_actions(state, skip: set[str] | None = None) -> list[tuple[str, dict]]:
    """Journal entries in the order they were recorded, leaving out names already handled."""
    names = [m["name"] for m in state.list(ACTIONS) if m["name"].endswith(".json") and m["name"] not in (skip or set())]
    if not names:
        return []

    def fetch(name: str):
        raw = state.get(name)
        try:
            return name, json.loads(raw) if raw else None
        except ValueError:
            return name, None
    with ThreadPoolExecutor(max_workers=8) as ex:
        got = list(ex.map(fetch, names))
    return [(n, a) for n, a in sorted(got) if isinstance(a, dict)]


def apply_actions(conn: sqlite3.Connection, entries: list[tuple[str, dict]], *, results: list | None = None,
                  copy: bool = False) -> tuple[list[str], list[str], bool]:
    """Apply journal entries to a copy of the store. Returns (names applied, names left over, whether anything was
    written). An entry is left over when a finding it names is not in this copy: that happens when the dashboard showed
    a finding the copy does not hold yet, and such an entry must be kept and tried again, never dropped. A bulk entry
    with one finding missing is left over too, though its other findings were written. Applying an entry twice is
    harmless and writes nothing the second time. `results` collects what the Data tab's requests came to (the run's
    status says it); `copy`: the dashboard's copy, where a request for the self-audit waits for the run (the copy holds
    only part of the store, so an audit worked out on it would show figures that are not the practice's)."""
    done, left = [], []
    before = conn.total_changes
    for name, a in entries:
        try:
            if a.get("kind") == "bulk_status":
                found = [actions_mod.apply_status(conn, fid, a["status"], a.get("notes"), None, a.get("actor") or "", a.get("at"),
                                                  (a.get("seen") or {}).get(fid), a.get("entry"))
                         for fid in a.get("ids") or []]
                ok = all(found)
            elif a.get("kind") == "selfcheck_request":
                # the Data tab: the self-audit now, or the owner's acceptance of the offenders of the run the page showed
                # (a later run's offenders nobody has seen are never accepted: remitwatch.selfcheck.apply_request)
                from . import selfcheck
                if not (copy and a.get("what") == "audit"):
                    got = selfcheck.apply_request(conn, a) or {}
                    if results is not None:
                        results.append({"what": a.get("what"), "stale": bool(got.get("stale")),
                                        "accepted": got.get("accepted"), "text": got.get("text"),
                                        "audit": bool(got.get("audit"))})
                ok = True
            else:
                ok = actions_mod.apply(conn, a) is not False
            (done if ok else left).append(name)
        except Exception as exc:
            from . import runlog
            runlog.caught(exc, "journal", level="warning")
            done.append(name)       # malformed: nothing a later run could do with it either
    conn.commit()
    return done, left, conn.total_changes > before


def expired(name: str, days: int = 7) -> bool:
    """A journal entry's name starts with the time it was recorded. One that has found no finding for a week is given up."""
    try:
        at = datetime.strptime(name.rsplit("/", 1)[-1][:15], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return datetime.now(timezone.utc) - at > timedelta(days=days)


# --------------------------------------------------------------------------- pulling files from the data folder

def _state_prefix(state) -> str | None:
    base = getattr(state, "base", None)
    return (base.lower() + "/") if base else None


def _epoch(v) -> float | None:
    """A file's own time as seconds: from a Dropbox timestamp ("2026-09-07T22:34:00Z") or a zip member's date tuple."""
    try:
        if isinstance(v, str):
            return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
        if isinstance(v, tuple):
            return datetime(*v, tzinfo=timezone.utc).timestamp()
    except (ValueError, TypeError):
        pass
    return None


def _ingest_batch(store, items: list[tuple], log, *, wait_reason: str | None = None) -> dict:
    """Write a batch of downloaded files to scratch space, hand them to the loaders, and clean up. Each item is
    (name, bytes), (name, bytes, file time) or (name, bytes, file time, origin): the loaders keep the newest copy when a
    payer sends a payment twice, and newest must mean the file's own date, not the order this run happened to read it in.
    The origin (Dropbox id, path, content hash, size) goes with an EMA report into its catalog row, so a report kept
    waiting can be fetched again by its Dropbox id. A Production report never reaches this batch on purpose (it takes the
    run's slot, ProductionSlot); one that does waits, with `wait_reason`."""
    from .dropbox_sync import ingest_new_files
    d = os.path.join(runtime.tmp_dir(), "batch")
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d, exist_ok=True)
    paths = []
    origins: dict = {}
    for i, item in enumerate(items):
        name, data, when = item[0], item[1], (item[2] if len(item) > 2 else None)
        # each file in a folder of its own, under its real name: the loaders record the file name, and the link step reads it
        sub = os.path.join(d, f"{i:05d}")
        os.makedirs(sub)
        p = os.path.join(sub, os.path.basename(name.replace("\\", "/")) or "file")
        with open(p, "wb") as fh:
            fh.write(data)
        if when:
            os.utime(p, (when, when))
        paths.append(p)
        if len(item) > 3 and item[3]:
            origins[p] = item[3]
    unread: list[str] = []
    try:
        res = ingest_new_files(store, paths, log=log, unread=unread, origins=origins, via="dropbox",
                               wait_reason=wait_reason or PRODUCTION_LATER)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    if unread:
        # which items were left unread (a report none of the readers reads yet): the callers do not remember those as seen
        at = {p: i for i, p in enumerate(paths)}
        res["unread_items"] = sorted(at[p] for p in unread if p in at)
    return res


def _add(total: dict, part: dict) -> None:
    for k in ("remits", "ema", "claims_837", "errors", "skipped"):
        total[k] = total.get(k, 0) + (part.get(k) or 0)


def ingest_zip(store, client, root: str, deadline: float, out: dict, log) -> None:
    """First load only: when the data folder holds an archive of the remittance files, read it in one download instead of
    fetching tens of thousands of files one at a time. The content hash of every member is remembered, so the same file
    met later as a loose file in the folder is skipped without being downloaded."""
    from .dropbox_sync import WANTED_EXT, DropboxNotFound, content_hash_bytes
    conn = store.conn
    name = os.environ.get("REMITWATCH_BACKFILL_ZIP", "Trizetto.zip")
    if not name or conn.execute("SELECT 1 FROM meta WHERE key='cloud:zip_done'").fetchone():
        return
    path = f"{root.rstrip('/')}/{name}"
    try:
        meta = client.get_metadata(path)
        if not meta or (meta.get("size") or 0) > 400 * 1024 * 1024:
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('cloud:zip_done', 'absent')")
            conn.commit()
            return
        data, _ = client.download_bytes(path)
    except (DropboxNotFound, RuntimeError):
        return
    seen = {r[0] for r in conn.execute("SELECT content_hash FROM dropbox_seen WHERE content_hash IS NOT NULL")}
    zf = zipfile.ZipFile(io.BytesIO(data))
    members = [m for m in zf.infolist() if not m.is_dir() and m.filename.lower().endswith(WANTED_EXT) and "__MACOSX" not in m.filename
               and not os.path.basename(m.filename).startswith((".", "~$")) and m.file_size < 60 * 1024 * 1024]
    batch: list[tuple] = []
    rows: list[tuple] = []
    done = True

    def flush():
        if batch:
            part = _ingest_batch(store, batch, log)
            _add(out, part)
            out["failed"] = (out.get("failed") or []) + [f"{n} (could not be read)" for n in part.get("failed") or []]
            left = set(part.get("unread_items") or [])         # not remembered: read once a reader for it exists
            conn.executemany("INSERT OR REPLACE INTO dropbox_seen (id, content_hash, path, at) VALUES (?,?,?,?)",
                             [r for i, r in enumerate(rows) if i not in left])
            conn.commit()
            batch.clear()
            rows.clear()
    for m in members:
        if time.time() > deadline:
            done = False
            break
        blob = zf.read(m)
        h = content_hash_bytes(blob)
        if h in seen:
            continue
        seen.add(h)
        batch.append((m.filename, blob, _epoch(m.date_time)))
        rows.append((f"zip:{m.filename}", h, f"{path}!{m.filename}", _now()))
        out["from_archive"] = out.get("from_archive", 0) + 1
        if len(batch) >= 1500:
            flush()
    flush()
    if done:
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('cloud:zip_done', ?)", (meta.get("rev") or "yes",))
        conn.commit()
    else:
        out["remaining"] = out.get("remaining", 0) + 1


# --------------------------------------------------------------------------- EMA's reports on a serverless host
#
# The A/R report is small and read in the batch like any export. The Production report is not: a run applies at most one
# Production file (ProductionSlot), from a file of 60 MB or less as stored (a zipped or gzipped CSV, or a weekly
# posted-date pull), into the posting ledger kept gzipped in the state folder (ema_ledger.LedgerHandle). The attempt is
# written to the state folder before the file is read, so a run the platform stops still counts it (three, and the
# file is held). After the file is applied the ledger's summaries are refreshed and saved in this order: the ledger
# (released by the refresh as soon as Part A no longer needs it), its revision in the store's meta, the store, the
# dashboard's copy, then the Dropbox position; detection runs in the next invocation (more=True). A run that died
# between the ledger's save and the store's is repaired by the next (repair_ledger). A raw Production CSV larger than
# 60 MB is recognised from its first 64 KB and kept waiting with what to do about it.

PRODUCTION_LATER = ("This site reads one Production file per run; this one is read in a following run, which starts by "
                    "itself.")
from .ema_reports import BIG_CSV_REASON as BIG_REPORT_REASON      # noqa: E402  (one wording: a raw CSV too big for this site)
BIG_GZ_REASON = ("Too big to read on this site even compressed (over 60 MB): let the server read it, or export a shorter date "
                 "range (a Production export by posted date, the last five weeks, is about 20 MB).")
APPLY_PENDING = "ema:apply_pending"     # a Production file was being applied when the run stopped: the next run repairs
REPORT_FIRST_TEXT = (".csv", ".tsv", ".txt")


def _origin(e: dict) -> dict:
    """Where a report came from in Dropbox: kept in its catalog row (never served), to fetch it again by id."""
    return {"dropbox_id": e.get("id") or e.get("path_lower"), "path": e.get("path_display"), "content_hash": e.get("content_hash"),
            "size": e.get("size")}


def report_kind(data: bytes, name: str) -> str | None:
    """Which EMA report a downloaded file is or holds: 'production' (a Production report, or a zip holding one),
    'ar_report', 'refused' (a report whose columns break a rule), or None."""
    from . import ema_reports as er
    try:
        if er.is_plain_zip(data):
            recs = er.recognize_zip(data, name)
            if any(r.report == "production" for r in recs):
                return "production"
            return "ar_report" if recs else None
        rec = er.recognize(data, name)
    except er.ReportRefused:
        return "refused"
    except Exception as exc:       # noqa: BLE001  (not something recognition can open: not a report)
        from . import runlog
        runlog.caught(exc, "report_kind", level="warning")
        return None
    return rec.report if rec else None


def _gunzip_head(head: bytes, n: int = 65536) -> bytes:
    try:
        return zlib.decompressobj(31).decompress(head, n)
    except zlib.error:
        return b""


def _already_uploaded(conn: sqlite3.Connection, content_hash: str | None) -> bool:
    """A report read (or held) from an upload whose original bytes have this Dropbox content hash: the same file in the
    data folder is not read again."""
    if not content_hash:
        return False
    try:
        return conn.execute("SELECT 1 FROM ema_report_files WHERE origin LIKE ? AND status IN "
                            "('current','partly_replaced','replaced','superseded','held','discarded') LIMIT 1",
                            (f'%"content_hash": "{content_hash}"%',)).fetchone() is not None
    except sqlite3.Error:
        return False


def _held_to_apply(conn: sqlite3.Connection) -> list[dict]:
    """Held reports a person chose to apply: read again (holds skipped, never refusals)."""
    try:
        cur = conn.execute("SELECT * FROM ema_report_files WHERE status='held' AND decision='apply' ORDER BY received_at, file_id")
    except sqlite3.Error:
        return []
    names = [d[0] for d in cur.description]
    out = []
    for r in cur.fetchall():
        d = dict(zip(names, r))
        try:
            d["origin"] = json.loads(d.get("origin") or "{}")
        except ValueError:
            d["origin"] = {}
        out.append(d)
    return out


def production_ids(path: str, name: str) -> list[str]:
    """The catalog ids of the Production report(s) a file is or holds: the SHA-256 of a report's own bytes (a zip's
    member's, a gzip's unpacked bytes)."""
    from . import ema_reports as er
    from .intake import hash_file
    if er.is_plain_zip(path):
        return [er._member_id(path, r.member) for r in er.recognize_zip(path, name) if r.report == "production"]
    with open(path, "rb") as fh:
        gz = fh.read(2) == b"\x1f\x8b"
    return [hash_file(path, True).sha256()] if gz else [er.file_id_of(path)]


class ProductionSlot:
    """A serverless run's one Production file: apply() reads it into the ledger (or keeps it waiting), finish() refreshes
    the store's summaries and saves the ledger. Every caller with a Production report in hand (the intake folder, the
    Dropbox delta, the reports waiting by Dropbox id) goes through it."""

    def __init__(self, store, state, *, deadline: float, log=print, status: dict | None = None):
        self.store, self.state, self.deadline, self.log = store, state, deadline, log
        self.status = status                        # the run's status: a save here records its image there too (save_raw)
        self.handle = None
        self.used = False
        self.pending: list[str] | None = None       # file ids applied, waiting for finish()
        self.saved: list[str] = []                  # file ids whose attempts go once the store image is saved
        self.drop: list[str] = []                   # Production files whose kept upload copy goes once all is saved
        self.seq0: int | None = None
        self.out: dict = {}

    def _wait(self, path: str, rec, *, via: str, origin: dict, name: str, reason: str, log=None) -> dict:
        from . import ema_reports as er
        kw = {"name": name} if _takes(er.ingest_file, "name") else {}
        if _takes(er.ingest_file, "wait_reason"):
            kw["wait_reason"] = reason
        return er.ingest_file(self.store, path, rec, via=via, origin=origin, ledger=None, log=log or self.log, **kw)

    def apply(self, path: str, rec=None, *, file_id: str | None = None, origin: dict, name: str, via: str = "dropbox",
              leave: bool = False, log=None) -> dict:
        """Read one Production report into the ledger, or keep it waiting: when this host does not read Production (the
        switch), when the run has applied one already or has too little time left (`leave`: return intake.later() so
        an intake pair stays where it is), or when three runs have started it without finishing (it is held). `log`
        replaces the run's own for this file (an upload's lines name its upload id, never its file name)."""
        from . import ema_ledger, ema_reports as er
        from .intake import later
        conn = self.store.conn
        say = log or self.log
        if not er.reads("production"):
            return self._wait(path, rec, via=via, origin=origin, name=name, reason="", log=say)
        if self.used or time.time() > self.deadline:
            if leave:
                return later(PRODUCTION_LATER)
            return self._wait(path, rec, via=via, origin=origin, name=name, reason=PRODUCTION_LATER, log=say)
        try:
            fids = production_ids(path, name) or ([file_id] if file_id else [])
        except er.ReportRefused as exc:
            # it unpacks to far more than any export (a gzip or zip bomb): set aside under its own bytes, never read
            return er._record_refusal(self.store, er.file_id_of(path), exc, path=path, rec=None, via=via, origin=origin,
                                      log=say)
        self.handle = self.handle or ema_ledger.LedgerHandle(self.state)
        h = self.handle
        if fids and all((er._catalog_row(conn, f) or {}).get("status") in er.DONE_STATUSES for f in fids):
            # every report in it is in the ledger already: a run applied it and stopped before saving the store, and
            # repair_ledger brought the catalog back. It is 'already' (its rows and origin restored), never held, and the
            # attempts of that run go
            for f in fids:
                if h.attempts(f):
                    h.clear_attempt(f)
            return self._wait(path, rec, via=via, origin=origin, name=name, reason="", log=say)
        if any(h.attempts(f) >= ema_ledger.MAX_ATTEMPTS for f in fids):
            res = self._wait(path, rec, via=via, origin=origin, name=name, reason=ema_ledger.ATTEMPT_HOLD_REASON, log=say)
            for f in fids:
                ema_ledger.hold_slow_file(self.store, f)
            say(f"  EMA Production {name}: held after {ema_ledger.MAX_ATTEMPTS} runs that did not finish reading it")
            return dict(res, status="held", reason=ema_ledger.ATTEMPT_HOLD_REASON)
        if er.stored_salt(conn) is None:
            # The store's key salt is made now, and the store image saved with it before the ledger is keyed with it: a
            # run that died after saving the ledger and before saving the store would otherwise leave a store with no
            # salt, the next run would make another, and the ledger (keyed for the lost one) would be emptied and every
            # Production file read again. Once per host: the A/R usually makes the salt first.
            er.key_salt(conn)
            save_raw(self.state, self.store, status=self.status)
            say("  EMA: the store's key salt was made and saved before the posting ledger")
        for f in fids:
            h.note_attempt(f)               # in the state folder, before a byte of it is read
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (APPLY_PENDING, ",".join(fids)))
        self.used = True
        lg = h.open()
        self.seq0 = lg.seq()
        from . import runlog
        from . import selfcheck as sc
        try:        # (G) the store's summaries and the ledger, both as the last apply left them (the money checks)
            sc.check_ledger(conn, lg)
        except Exception as exc:               # noqa: BLE001  (measured next apply; detection is not held for it)
            runlog.caught(exc, "selfcheck:money")
        kw = {"name": name} if _takes(er.ingest_file, "name") else {}
        try:
            res = er.ingest_file(self.store, path, rec, via=via, origin=origin, ledger=lg, deadline=self.deadline, log=say, **kw)
        except BaseException:
            h.discard()
            raise
        try:        # (G) money posted in a closed month never leaves the ledger: seen in the run that applies the newer export
            sc.check_posted(conn, lg)
        except Exception as exc:               # noqa: BLE001
            runlog.caught(exc, "selfcheck:posted")
        members = res.get("members") if res.get("status") == "zip" else [res]
        statuses = {m.get("status") for m in members or []}
        if "waiting" in statuses or lg.seq() == self.seq0:
            # stopped at the time limit (the attempt stands), or nothing changed in the ledger (held, refused, already)
            h.discard()
            if "waiting" not in statuses:
                for f in fids:
                    h.clear_attempt(f)
                conn.execute("DELETE FROM meta WHERE key=?", (APPLY_PENDING,))
            if statuses <= {"already"}:
                self.used = False           # nothing was read: another file may take the slot
            return res
        self.pending = fids
        return res

    def finish(self, out: dict) -> bool:
        """After a file was applied: refresh the store's EMA summaries from the ledger, releasing (saving) the ledger to
        the state folder as soon as Part A no longer needs it; then clear the attempts and leave detection to the next
        invocation. Returns whether a file was applied."""
        from . import ema_ledger
        if not self.pending or self.handle is None or self.handle.ledger is None:
            return False
        h, conn = self.handle, self.store.conn
        try:
            drop = list(h.ledger.copies_to_drop())
        except Exception:       # noqa: BLE001  (a kept copy stays: nothing depends on its going)
            drop = []
        tat: dict = {}

        def release():
            # the payers' answer times from every claim in the ledger, read while it is still open (detection, in the next
            # run, reads meta ema:turnaround; the ledger is not opened there)
            try:
                from . import ema_checks
                t = time.time()
                ema_checks.turnaround(conn, ledger=h.ledger)
                tat["seconds"] = round(time.time() - t, 1)
            except Exception as exc:       # noqa: BLE001  (the kept lines' answer times stand in)
                tat["error"] = type(exc).__name__
            return h.save()
        try:
            r = ema_ledger.refresh(self.store, h.ledger, log=self.log, release=release)
            if h.ledger is not None:        # nothing to recompute: the ledger is saved all the same (it changed)
                h.save(conn)
        except Exception as exc:       # noqa: BLE001
            h.discard()
            out["ema_apply"] = {"error": type(exc).__name__}
            return False                    # APPLY_PENDING stays: the next run brings the catalog in line (repair_ledger)
        if tat.get("seconds") is not None:
            _stamp_turnaround(conn)         # read from the ledger the refresh has just recorded
        conn.execute("DELETE FROM meta WHERE key=?", (APPLY_PENDING,))
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('cloud:detect_pending', '1')")
        conn.commit()
        out["ema_apply"] = {"files": len(self.pending), "refresh_seconds": r.get("seconds"),
                            "part_a": bool(r.get("part_a")), "part_b": bool(r.get("part_b"))}
        self.saved, self.pending = list(self.pending), None
        self.drop = drop
        return True

    def clear_attempts(self) -> None:
        """Once the store image is saved (the ledger was saved before it), the files read this run no longer count an
        attempt. A run that dies before this keeps them: the next run finds the file in the ledger ('already')."""
        if self.handle is not None:
            for f in self.saved:
                self.handle.clear_attempt(f)
        self.saved = []

    def drop_copies(self) -> int:
        """After the saves: the kept upload copies (intake/done/<pair>) of Production files that newer files replaced whole
        (REMITWATCH_EMA_REPORT_KEEP of them, as on the server) are deleted from the state folder. The catalog row and the
        done sidecar stay. Never a file outside intake/done/."""
        n = 0
        for fid in self.drop:
            r = self.store.conn.execute("SELECT origin FROM ema_report_files WHERE file_id=?", (fid,)).fetchone()
            try:
                sp = (json.loads(r[0]) if r and r[0] else {}).get("state_path")
            except ValueError:
                sp = None
            if not sp or not sp.startswith("intake/done/") or sp.endswith(".json"):
                continue
            try:
                if self.state.meta(sp):
                    self.state.delete(sp)
                    n += 1
            except Exception as exc:       # noqa: BLE001  (tried again after the next Production file)
                from . import runlog
                runlog.caught(exc, "drop_copies", level="warning")
        self.drop = []
        if n:
            self.log(f"  {n} kept upload cop{'y' if n == 1 else 'ies'} of replaced Production files removed")
        return n


def _stamp_turnaround(conn) -> None:
    """The answer times read from the ledger before the refresh recorded its new revision belong to that revision: they
    are stamped with it, so the next run keeps them (ema_checks reads them again from the kept lines otherwise)."""
    from . import ema_checks
    key = getattr(ema_checks, "TURNAROUND_KEY", "ema:turnaround")
    r = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    try:
        stored = json.loads(r[0]) if r and r[0] else None
    except ValueError:
        stored = None
    if not isinstance(stored, dict) or stored.get("charge_basis") != "ledger" or not hasattr(ema_checks, "_ledger_rev"):
        return
    stored["ledger_rev"] = ema_checks._ledger_rev(conn)
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, json.dumps(stored)))
    conn.commit()


def _takes(fn, arg: str) -> bool:
    import inspect
    try:
        return arg in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


def copy_howto(state, conn) -> bool:
    """Copy the state folder's "How we pull this in our EMA" boxes (uploads.HOWTO_STATE) into the store's meta
    (uploads.HOWTO_META) when they differ. Not committed here. True when the store changed."""
    from . import uploads
    doc = uploads.howto_read_state(state)
    if doc is None:
        return False
    if uploads.howto_read_conn(conn) == doc:
        return False
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (uploads.HOWTO_META, json.dumps(doc)))
    return True


def repair_ledger(store, state, log=print) -> dict | None:
    """A run that saved the ledger and stopped before saving the store left the store behind the ledger (their revisions
    differ), and one that stopped while applying a file left meta ema:apply_pending: the ledger is opened, the catalog
    brought in line with it and the summaries refreshed (ema_ledger.refresh), and detection asked of the next run. The
    ledger is saved again only if the refresh changed it (retention). None when there is nothing to repair."""
    from . import ema_ledger
    conn = store.conn
    pending = conn.execute("SELECT 1 FROM meta WHERE key=?", (APPLY_PENDING,)).fetchone() is not None
    h = ema_ledger.LedgerHandle(state)
    try:
        behind = h.behind(conn)
    except Exception as exc:       # noqa: BLE001  (the state folder did not answer: the next run looks again)
        from . import runlog
        runlog.caught(exc, "repair_ledger", level="warning")
        return None
    if not (behind or pending):
        return None
    if h.state_rev() is None:           # no ledger was ever saved: nothing it could put right
        conn.execute("DELETE FROM meta WHERE key=?", (APPLY_PENDING,))
        conn.commit()
        return {"repaired": False}
    lg = h.open()
    seq0 = lg.seq()

    def release():
        if lg.seq() != seq0:
            return h.save()
        rev = h.rev
        h.discard()
        return rev
    try:
        r = ema_ledger.refresh(store, lg, log=log, release=release)
        if h.ledger is not None:
            if lg.seq() != seq0:
                h.save(conn)
            else:
                h.discard()
                if h.rev:
                    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (ema_ledger.META_REV, h.rev))
    except BaseException:
        h.discard()
        raise
    conn.execute("DELETE FROM meta WHERE key=?", (APPLY_PENDING,))
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('cloud:detect_pending', '1')")
    conn.commit()
    log("  EMA posting ledger: the store was behind it after an interrupted run; brought in line")
    return {"repaired": True, "part_a": bool(r.get("part_a")), "part_b": bool(r.get("part_b")), "seconds": r.get("seconds")}


def _write_tmp(name: str, data: bytes, folder: str) -> str:
    os.makedirs(folder, exist_ok=True)
    p = os.path.join(folder, os.path.basename(name.replace("\\", "/")) or "file")
    with open(p, "wb") as fh:
        fh.write(data)
    return p


def apply_production_items(store, slot: ProductionSlot, items: list[tuple[dict, bytes]], log) -> dict:
    """Production reports fetched from Dropbox in this run: each through the slot (one is read, the rest wait with their
    Dropbox origin, to be fetched again by id). A file whose read failed waits too: its attempt counts, and after three
    it is held."""
    from . import ema_reports as er
    out = {"ema": 0, "failed": [], "changed": False}
    d = os.path.join(runtime.tmp_dir(), "production")
    for e, data in items:
        shutil.rmtree(d, ignore_errors=True)
        name = e.get("name") or "file"
        p = _write_tmp(name, data, d)
        origin = _origin(e)
        try:
            res = slot.apply(p, None, origin=origin, name=name, via="dropbox")
        except Exception as exc:       # noqa: BLE001  (never the message: a converter's message can hold a cell)
            out["failed"].append(f"{name} (could not be read: {type(exc).__name__})")
            log(f"  EMA report ERROR {name}: {type(exc).__name__}")
            for fid in production_ids(p, name) or [hashlib.sha256(data).hexdigest()]:
                er.record_waiting(store.conn, file_id=fid, report="production", source_name=name, via="dropbox", origin=origin,
                                  reason="Reading stopped before the end; it is read again by a following run.")
            out["changed"] = True
            continue
        finally:
            del data
        members = res.get("members") if res.get("status") == "zip" else [res]
        if any(m.get("status") in ("current", "superseded") for m in members or []):
            out["ema"] += 1
        out["changed"] = True
    shutil.rmtree(d, ignore_errors=True)
    return out


REREAD_837_BATCH = 200          # 837 files fetched at once to be read again (about 7 MB on the real archive)


def reread_837(store, client, *, deadline: float, log=print) -> dict:
    """(F3a's need) This host's once-only read again of the 837 files whose claims were read before x12_837.READ_VERSION
    (pipeline.reread_837 is the server's): each is fetched from Dropbox by the name it was read under (the folder's files
    this store has seen, dropbox_seen; never the first load's archive), and its bytes are read only when their SHA-256 is
    the file's. Stops at the deadline ('remaining': the next run of the chain goes on). A file Dropbox no longer has, or
    holds under that name with other bytes, is counted ('missing') and kept as it was read; a download that may work next
    time ('download_errors') is tried again by a later run. Returns {} when there is nothing to read again."""
    from . import x12_837
    from .dropbox_sync import permanent_download_error
    from .pipeline import REREAD_837_META, remember_reread, reread_memory, reread_stuck
    conn = store.conn
    todo = x12_837.files_to_reread(conn)
    if not todo:
        if conn.execute("SELECT 1 FROM meta WHERE key=?", (REREAD_837_META,)).fetchone():
            remember_reread(conn, "serverless", set())
        return {}
    known = reread_memory(conn, "serverless")
    listed = set(todo)
    missing = {s for s in known if s in listed}
    todo = [s for s in todo if s not in missing]
    out = {"listed": len(listed), "read": 0, "claims": 0, "missing": len(missing), "remaining": 0, "download_errors": 0}
    if not todo:
        return out
    names: dict[str, str] = {}
    for i in range(0, len(todo), 500):
        part = todo[i:i + 500]
        for sha, name, path in conn.execute(f"SELECT sha256, source_name, source_path FROM files WHERE sha256 IN "
                                            f"({','.join('?' * len(part))})", part):
            names[sha] = (name or os.path.basename(path or "")).lower()
    where: dict[str, list[str]] = {}
    for fid, path in conn.execute("SELECT id, path FROM dropbox_seen WHERE id NOT LIKE 'zip:%' ORDER BY at DESC"):
        if fid:
            where.setdefault(os.path.basename(str(path or "")).lower(), []).append(fid)
    left = {sha: list(where.get(names.get(sha) or "", [])) for sha in todo}

    def fetch(job: tuple):
        sha, fid = job
        try:
            return sha, fid, client.download_bytes(fid)[0]
        except Exception as exc:       # noqa: BLE001  (sorted below: gone for good, or worth another run)
            return sha, fid, exc
    pending = [s for s in todo if left[s]]
    missing |= {s for s in todo if not left[s]}
    read: set[str] = set()
    while pending:
        if time.time() > deadline:
            out["remaining"] = len(pending)
            break
        batch, pending = pending[:REREAD_837_BATCH], pending[REREAD_837_BATCH:]
        with ThreadPoolExecutor(max_workers=12) as ex:
            got = list(ex.map(fetch, [(sha, left[sha].pop(0)) for sha in batch]))
        again = []
        for sha, _fid, data in got:
            if isinstance(data, Exception):
                if not permanent_download_error(data):
                    out["download_errors"] += 1         # a timeout or a busy server: a later run asks again
                    continue
            elif hashlib.sha256(data).hexdigest() == sha:
                r = x12_837.reread_837_bytes(conn, data)
                read.add(sha)
                out["read"] += 1
                out["claims"] += int(r.get("updated") or 0)
                continue
            # gone from Dropbox, or that name holds other bytes now: the next file of the same name, if there is one
            if left[sha]:
                again.append(sha)
            else:
                missing.add(sha)
        got = None
        pending = again + pending
    missing |= reread_stuck(conn, read)     # read, and its claims are not the ones the file makes now: nothing to change
    out["missing"] = len(missing)
    out["noted"] = missing != known
    if out["noted"]:
        remember_reread(conn, "serverless", missing)
    if out["read"] or out["noted"]:
        log(f"  837 files read again with the current reader: {out['read']} ({out['claims']} claims updated)"
            + (f"; {len(missing)} not found in Dropbox, kept as they were read" if missing else "")
            + (f"; {out['remaining']} left for the next run" if out["remaining"] else ""))
    return out


def reread_waiting(store, client, state, slot: ProductionSlot | None, *, deadline: float, log=print) -> dict:
    """The reports the catalog keeps waiting (and held files a person chose to apply), fetched again where they are kept:
    by Dropbox id, or from the state folder for an upload. A Production report goes through the slot; one larger than
    60 MB as stored waits for a zipped copy or an upload (its reason says so). A file that changed in Dropbox since is
    read as it is now; one that is gone closes its entry; one that turns out to be no report drops an entry the
    clean-up of misread exports queued (ema_reports.confirm_misread)."""
    from . import ema_reports as er
    from .dropbox_sync import DropboxNotFound
    conn = store.conn
    out = {"read": 0, "ema": 0, "gone": 0, "later": 0, "changed": False}
    seen: set = set()
    d = os.path.join(runtime.tmp_dir(), "waiting")
    for w in er.waiting(conn) + _held_to_apply(conn):
        if w["file_id"] in seen:
            continue
        seen.add(w["file_id"])
        if not er.reads(w.get("report") or ""):
            continue
        o = w.get("origin") or {}
        if (_stored_size(state, o) or 0) > MAX_FILE_BYTES:
            continue                        # a raw CSV too large for this site: a zipped copy or an upload closes it
        prod = w.get("report") == "production"
        if prod and slot is not None and slot.used:
            out["later"] += 1
            continue
        if time.time() > deadline:
            out["later"] += 1
            break
        shutil.rmtree(d, ignore_errors=True)
        try:
            if o.get("state_path"):
                chunks = state.get_chunks(o["state_path"])
                if chunks is None:
                    er.supersede_waiting(conn, w["file_id"], reason="The uploaded file is no longer kept; upload it again.")
                    out["gone"] += 1
                    out["changed"] = True
                    continue
                os.makedirs(d, exist_ok=True)
                p = os.path.join(d, os.path.basename(o["state_path"]).split("__", 1)[-1] or "file")
                with open(p, "wb") as fh:
                    for c in chunks:
                        fh.write(c)
            elif o.get("dropbox_id") and client is not None:
                data, meta = client.download_bytes(o["dropbox_id"])
                sha = hashlib.sha256(data).hexdigest()
                if (meta or {}).get("content_hash") and o.get("content_hash") and meta["content_hash"] != o["content_hash"] \
                        and not str(w["file_id"]).startswith("dropbox:") and not o.get("member") and sha != w["file_id"]:
                    er.supersede_waiting(conn, w["file_id"], reason="The file changed in Dropbox before it was read again; "
                                                                     "the newer copy is read instead.")
                    o = dict(o, content_hash=meta["content_hash"], size=meta.get("size"))
                p = _write_tmp(os.path.basename(o.get("path") or w.get("source_name") or "file"), data, d)
                del data
            else:
                continue
        except DropboxNotFound:
            er.supersede_waiting(conn, w["file_id"], reason="The file is no longer in Dropbox.")
            out["gone"] += 1
            out["changed"] = True
            continue
        except Exception as exc:       # noqa: BLE001  (a download that may work next time)
            log(f"  EMA report {w.get('source_name')}: could not be fetched again ({type(exc).__name__})")
            continue
        name = o.get("name") or os.path.basename(p)
        kind = report_kind_path(p, name)
        if kind is None:
            er.confirm_misread(store, w["file_id"], None)
            er.confirm_misread(store, er.file_id_of(p), None)
            out["changed"] = True
            continue
        out["read"] += 1
        try:
            if kind == "production" and slot is not None:
                res = slot.apply(p, None, origin=o, name=name, via=w.get("via") or "dropbox")
            else:
                kw = {"name": name} if _takes(er.ingest_file, "name") and o.get("state_path") else {}
                res = er.ingest_file(store, p, via=w.get("via") or "dropbox", origin=o, log=log, **kw)
        except Exception as exc:       # noqa: BLE001
            log(f"  EMA report ERROR {w.get('source_name')}: {type(exc).__name__}")
            continue
        members = res.get("members") if res.get("status") == "zip" else [res]
        if any(m.get("status") in ("current", "superseded") for m in members or []):
            out["ema"] += 1
            if str(w["file_id"]).startswith("dropbox:"):
                er.supersede_waiting(conn, w["file_id"], reason="A copy of it was read.", replaced_by=(members[0] or {}).get("file_id"))
        out["changed"] = True
    shutil.rmtree(d, ignore_errors=True)
    return out


def _stored_size(state, origin: dict) -> int | None:
    """What fetching a waiting report again costs this site: the bytes as they are kept. An upload's kept copy is stored
    compressed (a 206 MB CSV is a 15 MB gzip), so its origin's original size never decides it: the size its intake
    recorded ('stored'), else the kept copy's own size in the state folder. A Dropbox file is fetched as it is (its size)."""
    if origin.get("state_path"):
        if origin.get("stored") is not None:
            return int(origin["stored"])
        try:
            m = state.meta(origin["state_path"]) if state is not None else None
        except Exception:       # noqa: BLE001  (the state folder did not answer: the fetch says so)
            m = None
        return int(m["size"]) if m and m.get("size") is not None else None
    return origin.get("size")


def report_kind_path(path: str, name: str) -> str | None:
    """report_kind for a file on disk (a zip is opened, not read whole)."""
    from . import ema_reports as er
    try:
        if er.is_plain_zip(path):
            recs = er.recognize_zip(path, name)
            if any(r.report == "production" for r in recs):
                return "production"
            return "ar_report" if recs else None
        rec = er.recognize(path, name)
    except er.ReportRefused:
        return "refused"
    except Exception as exc:       # noqa: BLE001  (not something recognition can open: not a report)
        from . import runlog
        runlog.caught(exc, "report_kind_path", level="warning")
        return None
    return rec.report if rec else None


REFIND_KEY = "ema:refind_v1"        # the first run of a build that reads EMA's reports lists the whole folder once


def sync_and_ingest(store, client, state, deadline: float, full: bool = False, log=print, *,
                    slot: ProductionSlot | None = None) -> dict:
    """Pull what is new in the data folder and ingest it, stopping at the deadline. The Dropbox position (cursor) is only
    advanced when everything it listed was handled, so a run that stops early is simply repeated: files already taken
    are recognised by id and content hash and skipped without a download.

    EMA's reports: the first run of a build that reads them lists the whole folder once (meta ema:refind_v1), because
    the position moved past report files an older build did not read (a .csv was not even fetched). A Production report
    goes through the run's slot (one per run); a raw report CSV over 60 MB is recognised from its first 64 KB and kept
    waiting with what to do about it; every report keeps its Dropbox origin in the catalog."""
    from .dropbox_sync import permanent_download_error, wanted
    conn = store.conn
    root = os.environ.get("DROPBOX_ROOT_PATH", "/Trizetto Data")
    out: dict = {"listed": 0, "wanted": 0, "downloaded": 0, "remits": 0, "ema": 0, "claims_837": 0, "errors": 0, "remaining": 0,
                 "download_errors": 0, "failed": []}
    cursor = None if full else saved_cursor(state, root)
    out["mode"] = "delta" if cursor else "full"
    if not cursor:
        ingest_zip(store, client, root, deadline, out, log)
        if out["remaining"]:
            return out
    # a listing of the whole folder (the first load's, or the one-time look for report files an older build passed)
    # sets the flag once it is complete
    refind = conn.execute("SELECT 1 FROM meta WHERE key=?", (REFIND_KEY,)).fetchone() is None
    if refind and cursor:
        out["mode"] = "full (once: report files an older build passed)"
    try:
        entries = list(client.list_continue(cursor) if cursor and not refind else client.list_folder(root, recursive=True))
    except RuntimeError as exc:
        if cursor and _cursor_reset(exc):       # Dropbox dropped the position: list the folder afresh
            return sync_and_ingest(store, client, state, deadline, full=True, log=log, slot=slot)
        raise
    new_cursor = getattr(client, "last_cursor", None)
    seen_id = {r[0]: r[1] for r in conn.execute("SELECT id, content_hash FROM dropbox_seen")}
    seen_hash = {h for h in seen_id.values() if h}
    prefix = _state_prefix(state)
    todo, mark = [], []
    ema_changed = False
    for e in entries:
        out["listed"] += 1
        if not wanted(e) or (prefix and (e.get("path_lower") or "").startswith(prefix)):
            continue
        out["wanted"] += 1
        h, eid = e.get("content_hash"), e.get("id") or e.get("path_lower")
        if seen_id.get(eid) == h and h:
            continue
        if h and h in seen_hash:
            mark.append((eid, h, e.get("path_display"), _now()))       # same bytes already ingested under another name
            continue
        if (e.get("size") or 0) > MAX_FILE_BYTES:
            # a file this large is not downloaded here. An EMA report among them (a raw Production CSV) is recognised from
            # its first bytes and kept waiting, with what to do about it; anything else is noted as too large
            big = _big_report(store, client, e, out, log)
            if big is None:
                # its first bytes could not be fetched (a timeout, a busy server): not remembered, and the position
                # stays, so the next run looks at it again
                out["download_errors"] += 1
                continue
            if big:
                ema_changed = True
            else:
                out["errors"] += 1
                out["failed"].append(f"{e.get('name')} (too large)")
            mark.append((eid, h, e.get("path_display"), _now()))
            continue
        todo.append(e)
    if mark:
        conn.executemany("INSERT OR REPLACE INTO dropbox_seen (id, content_hash, path, at) VALUES (?,?,?,?)", mark)
        conn.commit()

    def fetch(e: dict):
        try:
            return e, client.download_bytes(e.get("id") or e["path_lower"])[0]
        except Exception as exc:
            return e, exc
    for i in range(0, len(todo), 400):
        if time.time() > deadline:
            out["remaining"] += len(todo) - i
            break
        chunk = todo[i:i + 400]
        with ThreadPoolExecutor(max_workers=12) as ex:
            got = list(ex.map(fetch, chunk))
        items, item_rows, rows, prod = [], [], [], []
        for e, data in got:
            if isinstance(data, Exception):
                log(f"  download failed {e.get('name')}: {data}")
                if permanent_download_error(data):
                    # Dropbox refuses this file itself: remember it so it is not asked for again until it changes
                    out["errors"] += 1
                    out["failed"].append(f"{e.get('name')} (cannot be downloaded)")
                    rows.append((e.get("id") or e.get("path_lower"), e.get("content_hash"), e.get("path_display"), _now()))
                else:
                    out["download_errors"] += 1         # a timeout or a busy server: try again on a later run
                continue
            row = (e.get("id") or e.get("path_lower"), e.get("content_hash"), e.get("path_display"), _now())
            name = e.get("name") or "file"
            if slot is not None and name.lower().endswith(_REPORT_ENDINGS) and report_kind(data, name) == "production":
                prod.append((e, data))          # the run's one Production file, or kept waiting by its Dropbox id
                rows.append(row)
                continue
            items.append((name, data, _epoch(e.get("client_modified") or e.get("server_modified")), _origin(e)))
            item_rows.append(row)
        got = None
        out["downloaded"] += len(items) + len(prod)
        if items:
            part = _ingest_batch(store, items, log)
            _add(out, part)
            ema_changed = ema_changed or bool(part.get("ema_changed"))
            out["failed"] += [f"{n} (could not be read)" for n in part.get("failed") or []]
            left = set(part.get("unread_items") or [])
            item_rows = [r for n, r in enumerate(item_rows) if n not in left]
        items = None
        if prod:
            pr = apply_production_items(store, slot, prod, log)
            out["ema"] += pr["ema"]
            out["failed"] += pr["failed"]
            ema_changed = ema_changed or pr["changed"]
        prod = None
        conn.executemany("INSERT OR REPLACE INTO dropbox_seen (id, content_hash, path, at) VALUES (?,?,?,?)", rows + item_rows)
        conn.commit()
    # A file that was fetched is remembered in the store whether or not it could be read, so the store must be saved.
    out["seen_changed"] = bool(out["downloaded"] or out["failed"] or mark or ema_changed)
    out["ema_changed"] = ema_changed
    out["failed"] = out["failed"][:20]
    # The position moves on when everything listed was handled. A file that could not be read does not hold it back (it
    # is remembered and skipped until it changes); only a download that may work next time does.
    if not out["remaining"] and not out["download_errors"] and new_cursor and (out["listed"] or not cursor):
        out["cursor"] = new_cursor
        if refind:
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (REFIND_KEY, _now()))
            conn.commit()
            out["seen_changed"] = out["seen_changed"] or bool(cursor)
    return out


_REPORT_ENDINGS = (".xlsx", ".csv", ".tsv", ".txt", ".zip", ".gz")


def _big_report(store, client, e: dict, out: dict, log) -> bool | None:
    """A file over the size this site downloads: when its first 64 KB show an EMA report (a raw Production CSV, or a
    gzip of one), it is kept waiting in the catalog under its Dropbox content hash, with its origin and what to do about
    it, and is not counted as an error. An upload of the same bytes already read makes it 'already'. False when it is no
    report (it is 'too large'); None when its first bytes could not be fetched this time (the next run looks again)."""
    from . import ema_reports as er
    from .dropbox_sync import peek, permanent_download_error
    name = e.get("name") or "file"
    low = name.lower()
    gz = low.endswith(".gz")
    if not (low.endswith(REPORT_FIRST_TEXT) or gz):
        return False
    try:
        head = peek(client, e.get("id") or e["path_lower"], er.PEEK_BYTES)
    except Exception as exc:       # noqa: BLE001
        log(f"  {name}: could not look at its first bytes ({type(exc).__name__})")
        return False if permanent_download_error(exc) else None
    if gz:
        head = _gunzip_head(head)
    try:
        rec = er.recognize(head, name[:-3] if gz else name)
    except er.ReportRefused:
        rec = None
    if not rec:
        return False
    if _already_uploaded(store.conn, e.get("content_hash")):
        log(f"  {name}: already read (the same file was uploaded in the app)")
        return True
    er.record_waiting(store.conn, file_id=er.provisional_id(e.get("content_hash") or e.get("id")), report=rec.report,
                      source_name=name, via="dropbox", origin=_origin(e), reason=BIG_GZ_REASON if gz else BIG_REPORT_REASON,
                      size=e.get("size"))
    out["ema_waiting"] = out.get("ema_waiting", 0) + 1
    log(f"  EMA report {name}: too big to read here; kept waiting")
    return True


def _cursor_reset(exc: Exception) -> bool:
    """Dropbox's own answer that a listing position is no longer valid (a 409 naming "reset"), and nothing else: a
    dropped connection also says "reset"."""
    text = str(exc).lower()
    return isinstance(exc, RuntimeError) and " failed 409" in text and "reset" in text


def saved_cursor(state, root: str) -> str | None:
    """The Dropbox position of the last complete sync, kept in its own small file so that an idle run writes nothing
    large. A position recorded for a different data folder is not used."""
    raw = state.get(CURSOR)
    if not raw:
        return None
    try:
        j = json.loads(raw)
    except ValueError:
        return None
    if j.get("root") not in (None, root):
        return None
    return j.get("cursor") or None


def has_new_files(state, client) -> bool:
    """Cheap check for the webhook: is there anything new in the data folder since the last completed sync? The app's own
    writes to its state folder also raise webhooks, and this is what keeps them from starting a cycle."""
    from .dropbox_sync import wanted
    cursor = saved_cursor(state, os.environ.get("DROPBOX_ROOT_PATH", "/Trizetto Data"))
    if not cursor:
        return False        # no complete sync yet: the first load is driven by the button and the daily run, not by notifications
    try:
        res = client._rpc("files/list_folder/continue", {"cursor": cursor})
    except Exception as exc:
        # Only a position Dropbox has dropped is worth a run (the run lists the folder afresh and saves a new one). Any
        # other failure would fail the run too, and answering "yes" here would have the site call itself for ever.
        return _cursor_reset(exc)
    prefix = _state_prefix(state)
    for e in res.get("entries", []):
        if wanted(e) and not (prefix and (e.get("path_lower") or "").startswith(prefix)):
            return True
    return bool(res.get("has_more"))


# --------------------------------------------------------------------------- the dashboard's copy

DETAIL = "claim_key IN (SELECT claim_key FROM main.findings WHERE claim_key IS NOT NULL)"


def _whole_bills() -> str:
    """Every 835 claim of a bill that has an item (any submission of the bill: a corrected claim, a second payer's answer),
    not only the item's own claim, so the dashboard's copy can work out an item's state from the whole bill as the server
    does (needs_D: about 22,700 claims, 63,100 lines and 108,300 adjustments against 21,600, 30,500 and 52,800; the copy
    grows from about 152 MB to 188 MB on the real archive). Plain SQL only (ema_reports.bill_sql), so the copy can be
    built on any connection."""
    from .ema_reports import bill_sql
    pcn = "patient_control_number"
    return (f"claim_key IN (SELECT c.claim_key FROM main.claims c WHERE c.{pcn} IS NOT NULL AND {bill_sql('c.' + pcn)} IN "
            f"(SELECT {bill_sql('f.' + pcn)} FROM main.findings f WHERE f.{pcn} IS NOT NULL))")


BILLS = _whole_bills()


def _bills_837() -> str:
    """The 837 claims and lines of every bill that has an item (every send, every payer), so the dashboard's copy tells an
    item's story and works out its state with the sends the server sees (F3a: without them the copy showed only the send
    the item's evidence names). Plain SQL only (ema_reports.bill_sql)."""
    from .ema_reports import bill_sql
    pcn = "patient_control_number"
    return f"{bill_sql(pcn)} IN (SELECT {bill_sql('f.' + pcn)} FROM main.findings f WHERE f.{pcn} IS NOT NULL)"


BILLS_837 = _bills_837()
# what the pages read of a send (story's 837 rows, states' resends): no keyed hash of a patient (patient_key, person_key,
# first_key), no file it came from, no member id or claim reference the pages do not show
CLAIMS_837_COLUMNS = ("claim_837_key,bht_created,patient_control_number,total_charge_cents,frequency_code,place_of_service,"
                      "payer_name,payer_id,service_date,line_count,payer_resp,filing_indicator,other_payer_name,other_payer_id,"
                      "other_payer_resp,other_paid_cents,other_left_cents,other_pr_cents,read_version")
LINES_837_COLUMNS = ("line_837_key,claim_837_key,patient_control_number,line_seq,procedure_code,modifiers,charge_cents,units,"
                     "service_date")
SERVING_TABLES: list[tuple[str, str | None, str | None]] = [
    # table, column whitelist (None = every shared column), filter
    ("findings", None, None), ("payer_rates", None, None), ("fee_table", None, None), ("provider_tiers", None, None),
    ("providers", None, None), ("exposure", None, None), ("detection_runs", None, None), ("alerts", None, None),
    ("finding_events", None, None), ("progress_snapshots", None, None), ("plb", None, None),
    ("finding_heirs", None, None),      # an item's new number, for a page drawn before the run that gave it (api._not_found)
    ("meta", None, "key NOT LIKE 'sync:cursor%' AND key NOT LIKE 'cache:%'"),
    ("files", "sha256,kind,ingested_at", None),
    ("transactions", "transaction_key,source_name,payer_name,payer_id,payer_contact_phone,payer_contact_url,payment_method,"
                     "payment_amount_cents,payment_date,trace_number,production_date,payee_npi,superseded", None),
    ("claims", None, f"{DETAIL} OR {BILLS} OR patient_control_number IN (SELECT patient_control_number FROM main.findings WHERE patient_control_number IS NOT NULL)"),
    ("lines", None, f"{DETAIL} OR {BILLS}"), ("adjustments", None, f"{DETAIL} OR {BILLS}"), ("remarks", None, f"{DETAIL} OR {BILLS}"),
    # what the payers' recoupments (PLB WO, FB, J1 naming a claim, on any later check) took from each payment of the copy's
    # own lines (detect._save_taken): the drawer's proof on the copy counts what a takeback left of a payment, as the
    # server's does (fx-ledger-r2-4). s is the copy build_serving attaches; lines is copied before this table.
    ("ledger_taken", None, "line_key IN (SELECT line_key FROM s.lines)"),
    ("ema_rows", None, "claim_id IN (SELECT patient_control_number FROM main.findings WHERE patient_control_number IS NOT NULL)"),
    # the 837 sends of the items' bills (F3a): the story's sends and the resends that make an item's state
    ("claims_837", CLAIMS_837_COLUMNS, BILLS_837), ("lines_837", LINES_837_COLUMNS, BILLS_837),
    ("line_links", None, "line_key IN (SELECT line_key FROM main.findings WHERE line_key IS NOT NULL)"),
    ("line_handling", None, "line_key IN (SELECT line_key FROM main.findings WHERE line_key IS NOT NULL)"),
    # what each clinician did (remitwatch.activity): counts and sums per clinician, no patient rows
    ("provider_roster", None, None), ("provider_days", None, None), ("provider_services", None, None), ("provider_payers", None, None),
    ("provider_panels", None, None), ("provider_cohorts", None, None), ("provider_recall", None, None),
    # faxes sent and received, payers' confirmed fax numbers, what the note reviews taught (remitwatch.fax, the journal)
    ("faxes", None, None), ("payer_fax", None, None), ("note_lessons", None, None),
    # reasons hidden for a payer (actions.apply_hide_rule)
    ("hide_rules", None, None),
    ("provider_ema_days", None, None), ("provider_ema_lag", None, None),
    # when each EMA export and report was pulled (the claim view's "as of" day): names, times and counts, no patient data
    ("ema_sources", None, None),
    # what the runs logged (remitwatch.runlog, repair): the Data tab's panel and the drawer's "Why did this change?"
    ("run_log", None, None), ("repair_log", None, None),
    ("item_decisions", None, "finding_id IN (SELECT finding_id FROM main.findings) AND at >= strftime('%Y-%m-%dT%H:%M:%S', 'now', '-180 days')"),
    # the self-check's lists behind a red check and the weekly self-audit's gaps and wasted work (remitwatch.selfcheck):
    # finding ids, keys and cents, so the Data tab lists them and a person accepts exactly what was shown
    ("selfcheck_audit_items", None, None), ("selfcheck_offenders", None, None),
]


def empty_serving(path: str) -> None:
    """A serving database with every table and no rows, so the dashboard works (and shows nothing) before the first load."""
    from .alerts import ALERTS_DDL
    from .detect import HEIRS_DDL, ensure_schema
    from .link import DDL as LINK_DDL
    from .store import connect
    if os.path.exists(path):
        os.remove(path)
    c = connect(path)
    c.execute("PRAGMA journal_mode=DELETE")
    ensure_schema(c)
    c.executescript(ALERTS_DDL + LINK_DDL)
    c.execute(HEIRS_DDL)                # an item's new number, for a page drawn before the run that gave it
    from . import x12_837
    x12_837.ensure_schema(c)            # the 837 sends of the items' bills (SERVING_TABLES, F3a)
    activity.ensure_schema(c)
    actions_mod.ensure_fax_tables(c)
    actions_mod.ensure_hide_rules(c)
    from . import archive, selfcheck
    archive.ensure_schema(c)            # Find a claim opens archived bills from the copy (the archive's index)
    selfcheck.ensure_tables(c)          # the lists behind a red check and the self-audit's (build_serving copies shared columns)
    c.commit()
    c.close()


def meta_summary(conn: sqlite3.Connection) -> dict:
    """The figures the dashboard header shows, computed on the full store (the serving copy holds only part of it)."""
    from . import ema_reports
    conn.row_factory = sqlite3.Row
    one = lambda sql: dict(conn.execute(sql).fetchone())      # noqa: E731
    return {
        "files": one("SELECT COUNT(*) n, SUM(kind='835') n835, SUM(kind LIKE 'ema:%') nema, MAX(ingested_at) last_ingest FROM files"),
        "remits": one("SELECT COUNT(*) n, MIN(payment_date) first_payment, MAX(payment_date) last_payment, SUM(payment_amount_cents) total_cents FROM transactions WHERE superseded=0"),
        "lines": one("SELECT COUNT(*) n, MIN(service_date) first_dos, MAX(service_date) last_dos FROM lines"),
        "ema": [dict(r) for r in conn.execute("SELECT family, COUNT(*) n, MIN(service_date) first_dos, MAX(service_date) last_dos FROM ema_rows GROUP BY family")],
        "ema_reports": ema_reports.coverage(conn),          # "EMA as of" in the header: the reports on file
    }


def serving_tables(conn: sqlite3.Connection) -> list[tuple[str, str | None, str | None]]:
    """What the dashboard's copy holds: SERVING_TABLES and EMA's report tables (remitwatch.ema_reports.serving_tables:
    no origin, no key or keyed hash, only the claim numbers the pages can show), with the meta filter that also leaves
    out the key salt."""
    from . import archive, ema_reports
    out = [(t, c, ema_reports.SERVING_META_FILTER if t == "meta" else w) for t, c, w in SERVING_TABLES]
    return out + list(ema_reports.serving_tables(conn)) + list(archive.SERVING_TABLES)


# Tables the detection run makes for itself, with no schema function the empty copy could call (detect._save_taken's
# ledger_taken). The copy makes each as the store holds it, so a column added to one later reaches the copy too.
RUN_MADE = ("ledger_taken",)


def build_serving(conn: sqlite3.Connection, path: str) -> int:
    """Copy what the dashboard needs out of the full store into a small database file. Returns its size in bytes."""
    empty_serving(path)
    summary = meta_summary(conn)
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.execute("ATTACH DATABASE ? AS s", (path,))
    try:
        for table, cols, where in serving_tables(conn):
            if table not in have:
                continue
            src = [r[1] for r in conn.execute(f"PRAGMA main.table_info({table})")]
            dst = {r[1] for r in conn.execute(f"PRAGMA s.table_info({table})")}
            if not dst and table in RUN_MADE:
                # SQLite keeps a table's definition as "CREATE TABLE <name> ..." (keywords upper case, no schema name and
                # no IF NOT EXISTS), so the same definition makes it in the copy
                made = conn.execute("SELECT sql FROM main.sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
                if made and str(made[0] or "").startswith("CREATE TABLE "):
                    conn.execute("CREATE TABLE s." + made[0][len("CREATE TABLE "):])
                    dst = {r[1] for r in conn.execute(f"PRAGMA s.table_info({table})")}
            use = [c for c in (cols.split(",") if cols else src) if c in src and c in dst]
            if not use:
                continue
            conn.execute(f"DELETE FROM s.{table}")
            conn.execute(f"INSERT OR REPLACE INTO s.{table} ({','.join(use)}) SELECT {','.join(use)} FROM main.{table}"
                         + (f" WHERE {where}" if where else ""))
        conn.execute("INSERT OR REPLACE INTO s.meta (key, value) VALUES ('cache:meta', ?)", (json.dumps(summary, default=str),))
        conn.execute("INSERT OR REPLACE INTO s.meta (key, value) VALUES ('cache:built', ?)", (_now(),))
        conn.commit()
    finally:
        conn.execute("DETACH DATABASE s")
    return os.path.getsize(path)


def save_serving(state, conn: sqlite3.Connection) -> dict:
    path = os.path.join(runtime.tmp_dir(), "build-serving.sqlite")
    size = build_serving(conn, path)
    from . import selfcheck
    try:        # (G) the copy against the store: what the pages show is what the store holds (meta selfcheck:serving)
        copy = sqlite3.connect(path)
        try:
            selfcheck.check_serving(conn, copy)
        finally:
            copy.close()
    except Exception as exc:                   # noqa: BLE001  (the copy goes out; the next run checks again)
        from . import runlog
        runlog.caught(exc, "selfcheck:serving")
    with open(path, "rb") as fh:
        data = fh.read()
    os.remove(path)
    blob = b"".join(gzip_chunks(data, level=6))
    del data
    meta = state.put(SERVING, blob)
    return {"bytes": size, "gz_bytes": len(blob), "rev": (meta or {}).get("rev")}


class StorageUnavailable(RuntimeError):
    """The state folder could not be reached and this process has no copy of the dashboard's data to fall back on."""


class ServingCopy:
    """One per process: the dashboard's local copy of the serving database, kept fresh, with the journal laid over it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = None
        self._quick = None
        self._state_key = None
        self.rev = None
        self.checked = 0.0
        self.journal_checked = 0.0
        self.applied: set[str] = set()
        self.error: str | None = None
        self.known = False          # has storage answered at least once (with a copy, or with "there is none yet")?
        self._status: tuple[float, dict | None] = (0.0, None)
        self._path: str | None = None
        self._seq = 0
        self._tag = f"serving-{os.getpid()}-{secrets.token_hex(3)}"

    @property
    def path(self) -> str | None:
        return self._path

    def _fresh_path(self) -> str:
        """Every version gets its own file name. Requests still reading the previous version keep their open file, and
        the two never share a rollback journal."""
        d = runtime.tmp_dir()
        if self._seq == 0:
            for f in os.listdir(d):         # left by an earlier process on this instance (a process id can repeat, a start time cannot)
                p = os.path.join(d, f)
                try:
                    if f.startswith("serving-") and not f.startswith(self._tag) and os.path.getmtime(p) < _PROCESS_START - 1:
                        os.remove(p)
                except OSError:
                    pass
        self._seq += 1
        return os.path.join(d, f"{self._tag}-{self._seq}.sqlite")

    def _swap(self, new_path: str) -> None:
        old, self._path = self._path, new_path
        if old and old != new_path:
            for suffix in ("", "-journal"):
                try:
                    os.remove(old + suffix)
                except OSError:
                    pass

    def _empty(self) -> None:
        p = self._fresh_path()
        empty_serving(p)
        self._swap(p)

    def state(self, fast: bool = False):
        key = tuple(os.environ.get(k) for k in ("REMITWATCH_STATE_DIR", "DROPBOX_REFRESH_TOKEN", "REMITWATCH_STATE_PATH"))
        if self._state is None or key != self._state_key:
            # Two clients on the same folder. Reads that every page passes through make one attempt with a short wait,
            # so a slow Dropbox cannot hold the site. What a person writes (a status, a packet) is tried patiently.
            self._state, self._quick, self._state_key = state_from_env(), state_from_env(fast=True), key
            self.rev, self.checked, self.journal_checked, self.applied = None, 0.0, 0.0, set()
            self.known = False
            self._status = (0.0, None)
        return self._quick if fast else self._state

    def ensure(self, force: bool = False) -> str:
        with self._lock:
            st = self.state(fast=True)
            now = time.time()
            have = bool(self._path and os.path.exists(self._path))
            if st is None:
                if not have:
                    self._empty()
                return self._path
            try:
                if force or not have or now - self.checked > REFRESH_SECONDS:
                    self.checked = now
                    m = st.meta(SERVING)
                    self.known = True
                    if m is None:
                        if not have or self.rev is not None:
                            self._empty()
                            self.rev, self.applied, self.journal_checked = None, set(), 0.0
                    elif m["rev"] != self.rev or not have:
                        blob = st.get(SERVING)
                        if blob:
                            new = self._fresh_path()
                            with open(new, "wb") as fh:
                                fh.write(zlib.decompress(blob, 31))
                            del blob
                            self._swap(new)
                            self.rev, self.applied, self.journal_checked = m["rev"], set(), 0.0
                    self.error = None
                if force or now - self.journal_checked > JOURNAL_SECONDS:
                    self.journal_checked = now
                    entries = read_actions(st, skip=self.applied)
                    if entries:
                        c = sqlite3.connect(self._path, timeout=30)
                        try:
                            apply_actions(c, entries, copy=True)
                        finally:
                            c.close()
                        self.applied.update(n for n, _ in entries)
            except Exception as exc:
                self.error = f"{type(exc).__name__}: {str(exc)[:200]}"
                if not self.known or not (self._path and os.path.exists(self._path)):
                    # Storage has never answered this process, so there is nothing true to show. Say so, and look
                    # again in a few seconds: an empty dashboard here would read as "no findings".
                    self.checked = now - REFRESH_SECONDS + RETRY_SECONDS
                    raise StorageUnavailable(self.error) from exc
                # otherwise keep serving the copy already here; the Data tab shows the error
            return self._path

    def connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.ensure(), timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def record(self, action: dict) -> None:
        """Write an action to the journal and apply it to this instance's copy at once."""
        st = self.state()
        if st is None:
            raise RuntimeError("storage is not configured")
        action = dict(action, at=action.get("at") or _now(), entry=action.get("entry") or secrets.token_hex(8))
        name = record_action(st, action)
        self.ensure()
        with self._lock:
            if self._path and os.path.exists(self._path):
                c = sqlite3.connect(self._path, timeout=30)
                try:
                    apply_actions(c, [(name, action)], copy=True)
                finally:
                    c.close()
                self.applied.add(name)

    def status(self) -> dict:
        ts, cached = self._status
        if time.time() - ts < 10 and cached is not None:
            return cached
        st = self.state(fast=True)
        out: dict = {"configured": st is not None}
        if st is not None:
            try:
                raw = st.get(STATUS)
                out["last_cycle"] = json.loads(raw) if raw else None
                out["running"], out["stalled"] = lock_state(st, named=True)
                out["has_data"] = st.meta(SERVING) is not None
            except Exception as exc:
                out["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
            if self.error and "error" not in out:
                out["error"] = self.error
        self._status = (time.time(), out)
        return out


SERVING_COPY = ServingCopy()


def webhook_should_start(state, client) -> bool:
    """A Dropbox notification starts a run only when all of this holds: no run is going, the last one did not just fail
    or die (its own writes raise notifications, and without this a failing run would restart itself for ever), and the
    data folder really has a new file the app reads."""
    going, stalled = lock_state(state)
    if going or stalled:
        return False        # a dead run's lock is cleared by the daily run or by a person, not by a notification
    last = _read_json(state, STATUS)
    age = _age_seconds(last.get("finished"))
    if last.get("error") and age < FAILED_BACKOFF_SECONDS:
        return False
    # A run that could not fetch a file, or stopped with more to do, leaves the Dropbox position where it was, so the
    # check below would say "new files" for every notification, including the ones the run's own writes raised.
    unfinished = (last.get("ingest") or {}).get("download_errors") or last.get("more") or last.get("halted")
    if unfinished and age < UNFINISHED_BACKOFF_SECONDS:
        return False
    return has_new_files(state, client)


# --------------------------------------------------------------------------- appeal packets as files

def save_packet(state, packet: dict, pdf: bytes) -> str:
    """Store a packet built by remitwatch.notes. Its id carries the finding id so it can be found again without an index."""
    fid = packet.get("finding_id") or "adhoc"
    pid = f"{fid}.{packet['packet_id']}"
    meta = {k: v for k, v in packet.items() if k not in ("pdf", "note_blob")}
    meta["packet_id"] = pid
    state.put(f"{PACKETS}/{fid}/{pid}.json", json.dumps(meta, default=str).encode())
    state.put(f"{PACKETS}/{fid}/{pid}.pdf", pdf)
    return pid


def _packet_paths(packet_id: str) -> tuple[str, str] | None:
    if "." not in packet_id or "/" in packet_id or ".." in packet_id:
        return None
    fid = packet_id.split(".", 1)[0]
    return f"{PACKETS}/{fid}/{packet_id}.json", f"{PACKETS}/{fid}/{packet_id}.pdf"


def get_packet(state, packet_id: str, with_pdf: bool = False, reveal: bool = False) -> dict | None:
    paths = _packet_paths(packet_id)
    raw = state.get(paths[0]) if paths else None
    if not raw:
        return None
    p = json.loads(raw)
    redacted = p.pop("letter_redacted", None)
    full = p.pop("letter_full", None)
    p["letter"] = (full if reveal else redacted) or p.get("letter") or "[The letter is in the PDF.]"
    if with_pdf:
        p["pdf"] = state.get(paths[1])
    return p


def list_packets(state, finding_id: str) -> list[dict]:
    out = []
    for m in state.list(f"{PACKETS}/{finding_id}"):
        if m["name"].endswith(".json"):
            raw = state.get(m["name"])
            if raw:
                p = json.loads(raw)
                out.append({k: p.get(k) for k in ("packet_id", "finding_id", "created_at", "mode", "model", "subject", "note_name", "emailed_to", "emailed_at")})
    return sorted(out, key=lambda p: p.get("created_at") or "", reverse=True)


def mark_packet_emailed(state, packet_id: str, to: str) -> None:
    paths = _packet_paths(packet_id)
    raw = state.get(paths[0]) if paths else None
    if raw:
        p = json.loads(raw)
        p["emailed_to"], p["emailed_at"] = to, _now()
        state.put(paths[0], json.dumps(p, default=str).encode())


# --------------------------------------------------------------------------- the cycle

class OutOfTime(Exception):
    """A step ran past the cycle's deadline and was stopped."""


class deadline_guard:
    """Stop the statement still running at the deadline. The platform ends a function at its maximum duration without a
    word, leaving no status and a lock behind. Stopped here instead, the cycle ends with an error the status records and
    the lock is released.

    hard: every statement after the deadline is stopped too and the step ends with OutOfTime, so nothing half-done is
    saved (detection). Otherwise only the one statement is stopped and the step handles it as any other fault (the
    provider tables record the error, and the detection that finished before them is still saved)."""

    def __init__(self, conn: sqlite3.Connection, deadline: float, step: str, hard: bool = True):
        self.conn, self.deadline, self.step, self.hard = conn, deadline, step, hard
        self.tripped = False

    def _check(self) -> int:
        if self.tripped and not self.hard:
            return 0
        if time.time() > self.deadline:
            self.tripped = True
            return 1
        return 0

    def __enter__(self):
        self.conn.set_progress_handler(self._check, 200_000)
        return self

    def __exit__(self, kind, exc, tb):
        self.conn.set_progress_handler(None, 0)
        if self.tripped and self.hard:
            raise OutOfTime(f"{self.step} was still running at the time limit and was stopped; nothing was saved") from exc
        return False


def _image_alert(prev: dict, out: dict, email: bool, log=print) -> None:
    """Mail a stop over the store image to the report address, at most once a day (the day is kept in status.json): the
    dashboard keeps showing the last good copy, so without this nobody may notice the daily runs stopped."""
    today = runtime.practice_today()        # once a day where the practice is: an evening's second stop is the same day
    if prev.get("image_alerted") == today:
        out["image_alerted"] = today
        return
    if not email:
        return
    try:
        from .report import email_report
        if email_report(f"RemitWatch stopped before changing anything.\n\n{out.get('error')}\n\nThis message contains no patient information.",
                        subject="RemitWatch alert: the store image"):
            out["image_alerted"] = today
    except Exception as exc:        # a mail failure must never hide the stop itself
        log(f"store image alert email failed: {type(exc).__name__}")


def write_status(state, out: dict) -> None:
    try:
        state.put(STATUS, json.dumps(out, default=str).encode())
    except Exception as exc:        # the next run writes it again; the run's log keeps the failure
        from . import runlog
        runlog.caught(exc, "write_status", level="warning")


def _last_detection(conn) -> dict | None:
    """The newest analysis the store records (detection_runs), for a status written before runs kept 'analysed'."""
    try:
        r = conn.execute("SELECT run_id, finished_at, findings FROM detection_runs ORDER BY finished_at DESC, rowid DESC LIMIT 1").fetchone()
    except sqlite3.Error as exc:        # a store that has never been analysed: the pages say "not analysed yet"
        from . import runlog
        runlog.caught(exc, "last_detection", level="warning")
        return None
    return {"at": r[1], "run_id": r[0], "trigger": None, "findings": r[2]} if r and r[1] else None


# why a run handed the analysis on, in the note's words (the self-regulation's worst pressure names its own)
DEFER_REASONS = {
    "time": "This run did not have the time left for it, so a fresh run does it.",
    "files": "This run read a large batch of files, so a fresh run does it, with clean memory.",
    "production": "This run read a Production report, so a fresh run does it, with clean memory.",
    "waiting": "Files are still waiting to be read: the next run reads them first.",
}
SPREAD_REASONS = {"memory": "to keep within memory", "time": "to keep within the time a run has",
                  "tmp": "to keep within the temporary disk", "image": "to keep the store within what a run saves in one piece",
                  "killed": "after a run the platform stopped"}


def unchecked_note(conn, *, pending: bool, waiting_files: bool, plan: dict | None, analysed: dict | None,
                   read_since: bool = True, why: str | None = None) -> dict | None:
    """What a run could not check (the owner's decision 1: a pressed run that sheds work says so in items and dollars):
    when it ends with the analysis still to come (handed to the next run of the chain, or files left to read), the open
    items on the lists, and their dollars, were not checked against what was read since the last analysis, or, when
    nothing was read since (a press alone asked for it), stand as that analysis left them; the reason the run handed it on
    is named (`why`: spread, time, files, production, waiting). When the self-regulation left the weekly self-audit for a
    calmer run, its last figures are named. None when nothing was left."""
    from . import selfcheck as sc
    skipped_audit = "audit" in ((plan or {}).get("skip") or [])
    if not (pending or waiting_files or skipped_audit):
        return None
    n = cents = 0
    try:        # an item with no state yet (the first run of a release that adds it) counts by its status (open_items)
        if pending or waiting_files:    # only then are the open items unchecked; a self-audit left alone has its own figures
            n, cents = open_items(conn)
    except sqlite3.Error as exc:        # a store with no items yet: said as such
        from . import runlog
        runlog.caught(exc, "unchecked_note", level="warning")
    parts = []
    read: dict = {}
    if pending or waiting_files:
        since = f" ({_hhmm(analysed.get('at'))})" if (analysed or {}).get("at") else ""
        items = f"{sc._n(int(n), 'open item')} ({sc.usd0(cents)})"
        if read_since or waiting_files:
            head = (f"The analysis has not run on what was read since the last analysis{since}: {items} on the lists were not "
                    "checked against it.")
            # the money in what was read: new denials and cuts there have no item yet, so no list and no other page shows
            # them (CLAUDE.md rule 3: what the site cannot check, in dollars)
            read = read_since_analysis(conn)
            if read.get("claims"):
                head += (f" Read since then and not checked yet: {sc._n(read['claims'], 'remittance claim')} "
                         f"({sc._n(read['lines'], 'line')}), with {sc.usd0(read['cents'])} of their charges the payers did "
                         "not pay; none of it is on a list until the analysis runs.")
        else:
            head = (f"The analysis this press asked for has not run yet. Nothing new has been read since the last analysis"
                    f"{since}, so the {items} on the lists stand as it left them.")
        if why == "spread":
            worst = ((plan or {}).get("pressure") or {}).get("worst")
            reason = (f"The self-regulation handed it to a fresh run, {SPREAD_REASONS.get(worst, 'to keep within the limits of a run')}.")
        else:
            reason = DEFER_REASONS.get(why or "", "")
        parts.append(" ".join(x for x in (head, reason, "The next run of the chain analyses them; if none follows, press Run the "
                                          "analysis now.") if x))
    if skipped_audit:
        au = (sc.meta_get(conn, sc.K_AUDIT, {}) or {}).get("latest") or {}
        last = (f" The last one found {sc._n(int(au.get('gaps_n') or 0), 'line')} with money to recover and no item "
                f"({sc.usd0(au.get('gaps_cents') or 0)})." if au.get("today") else "")
        parts.append("The weekly self-audit (money on lines no item is working) was not checked in this run, to keep within "
                     f"memory: a calmer run does it, within a week.{last}")
    return {"n": int(n), "cents": int(cents), "pending": bool(pending or waiting_files), "audit_skipped": skipped_audit,
            "read_since": bool(read_since or waiting_files),
            "read": {k: read.get(k) for k in ("claims", "lines", "cents")} if read.get("claims") else None,
            "text": " ".join(parts)}


def open_items(conn) -> tuple[int, int]:
    """The open items on the lists (Work now and Waiting; an item with no state yet by its status) and their dollars."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(findings)")}
    by_status = "status IN ('open', 'in_progress', 'appealed')"
    where = (f"(work_state IN ('work_now', 'waiting') OR (work_state IS NULL AND {by_status}))" if "work_state" in cols
             else by_status)
    n, cents = conn.execute(f"SELECT COUNT(*), COALESCE(SUM(shortfall_cents), 0) FROM findings WHERE COALESCE(stale, 0) = 0 "
                            f"AND {where}").fetchone()
    return int(n or 0), int(cents or 0)


def failed_note(last: dict | None, conn=None) -> dict | None:
    """The last run stopped before it finished (its status carries an error): nothing it did was saved (the store is
    saved only at a run's end), so the lists stand as the analysis before it left them. What every page of the Vercel
    site says then (the owner's decision 1: a press that did not get through is said, in items and dollars when the
    dashboard's copy is at hand). None when the last run finished."""
    last = last or {}
    err = str(last.get("error") or "")
    if not err:
        return None
    from . import selfcheck as sc
    mins = max(1, int(last.get("budget_seconds") or runtime.max_seconds()) // 60)
    why = (f"it ran out of time (a run here has {mins} minutes)" if err.startswith("OutOfTime")
           else "it hit an error (the Logs and diagnostics panel below has it)")
    an = (last.get("analysed") or {}).get("at")
    items = ""
    if conn is not None:
        try:
            n, cents = open_items(conn)
            items = f" the {sc._n(n, 'open item')} ({sc.usd0(cents)}) on the lists"
        except sqlite3.Error as exc:        # a copy with no items yet: the sentence goes without them
            from . import runlog
            runlog.caught(exc, "failed_note", level="warning")
    stand = (f"Nothing it did was saved, so{items or ' the lists'} stand as the analysis of {_hhmm(an)} left them."
             if an else f"Nothing it did was saved{(':' + items + ' are as they were') if items else ''}.")
    text = (f"The last run (started {_hhmm(last.get('started'))}) stopped before it finished: {why}. {stand} Press Run the "
            "analysis now to try again. If it stops again, make the file for Claude in Logs and diagnostics below.")
    return {"at": last.get("finished") or last.get("started"), "out_of_time": err.startswith("OutOfTime"), "text": text}


def _lists_stand(conn, an: str | None) -> str:
    """'the N open items ($X) on the lists stand as the analysis of T left them', from the dashboard's copy `conn`."""
    from . import selfcheck as sc
    items = "the lists"
    if conn is not None:
        try:
            n, cents = open_items(conn)
            items = f"the {sc._n(n, 'open item')} ({sc.usd0(cents)}) on the lists"
        except sqlite3.Error as exc:        # a copy with no items yet: the sentence goes without them
            from . import runlog
            runlog.caught(exc, "lists_stand", level="warning")
    return f"{items[0].upper()}{items[1:]} stand as the analysis of {_hhmm(an)} left them." if an else \
        f"{items[0].upper()}{items[1:]} are as they were."


def stopped_note(last: dict | None, conn=None, stalled: dict | None = None) -> dict | None:
    """The last run did not finish its work, in the words every page of the Vercel site uses (the owner's decision 1: a
    press that did not get through is said, in items and dollars from the dashboard's copy `conn`, with what to do): the
    run stopped with an error (failed_note); the platform stopped it, so it wrote no status at all and left its lock
    (`stalled`, lock_state's, when run_killed says so); or the chain stopped after MAX_HOPS runs that each left work.
    None when the last run finished its work."""
    last = last or {}
    if last.get("error"):
        return failed_note(last, conn)
    an = (last.get("analysed") or {}).get("at")
    if not an and conn is not None:
        an = (_last_detection(conn) or {}).get("at")
    if stalled and run_killed(stalled, last):
        since = stalled.get("started") or stalled.get("since")
        text = (f"The last run (started {_hhmm(since)}) stopped before it finished: the platform stopped it, out of memory or "
                f"out of time, before it could say why. {_lists_stand(conn, an)} Press Run the analysis now to try again. If it "
                "stops again, make the file for Claude in Logs and diagnostics below.")
        return {"at": since, "killed": True, "text": text}
    if last.get("halted"):
        text = (f"The last run chain stopped after {last['halted']}: what was left (files to read, or the analysis) waits. "
                f"{_lists_stand(conn, an)} Press Run the analysis now to carry on. If it stops again, make the file for "
                "Claude in Logs and diagnostics below.")
        return {"at": last.get("finished") or last.get("started"), "halted": True, "text": text}
    return None


def next_run(out: dict, hop: int) -> dict:
    """What /api/cron/cycle starts after a run (the owner's decision 1: on this host only a person's press starts a run,
    and the runs of its chain follow one another): {'trigger', 'delay'} for the next run; {'halted': words} once MAX_HOPS
    runs in a row have left work; {'end': True} when the chain ends here; {} when the caller should still look for files
    that landed while the run went on. One rule, used by the route and by the tests that follow a press's chain."""
    if hop >= MAX_HOPS:
        return {"halted": f"{hop} runs in a row without finishing"} if out.get("more") else {"end": True}
    if out.get("more") and out.get("progress"):
        return {"trigger": "continue", "delay": 0}
    if (out.get("ingest") or {}).get("download_errors"):
        # a file could not be fetched (a timeout, a busy server): tried twice more, a minute and a half apart
        return {"trigger": "retry", "delay": 90} if hop < 2 else {"end": True}
    if out.get("tidy"):
        # the work only a run that does not analyse does (tidy_due): one more run of the chain, which reads what is new too
        return {"trigger": "tidy", "delay": 0}
    if out.get("skipped") or out.get("more"):
        return {"end": True}
    return {}


TIDY_ARCHIVE_HOURS = 20         # the archive's pass on this host comes once a day at most (a server's comes every cycle)


def tidy_due(conn, plan: dict | None, *, today: str | None = None) -> list[str]:
    """What only a run that does not analyse does on this host, now due: the archive's pass ('archive': none for
    TIDY_ARCHIVE_HOURS, or the self-regulation has since asked for the tighter horizon), the provider tables an analysis
    left behind ('activity') and the weekly self-audit ('audit'), each unless the self-regulation leaves it for a calmer
    run. Since the owner's decision 1 every run here is a press or a run of its chain, and a press analyses: a run that
    analysed hands these to one more run of the chain ('tidy', next_run), which analyses nothing unless files arrived."""
    from . import archive
    from . import selfcheck as sc
    plan = plan or {}
    skip = set(plan.get("skip") or [])
    due: list[str] = []
    try:
        if conn.execute("SELECT 1 FROM lines LIMIT 1").fetchone():
            last = (archive._meta_json(conn, archive.META_HORIZON, {}) or {}).get("at")
            tighter = False
            if plan.get("archive_tight"):       # the level asking for the tighter horizon came after the last pass
                since, done = _when((sc.meta_get(conn, sc.K_REGULATE, {}) or {}).get("since")), _when(last)
                tighter = since is not None and (done is None or since > done)
            if _age_seconds(last) >= TIDY_ARCHIVE_HOURS * 3600 or tighter:
                due.append("archive")
        if "activity" not in skip and activity.stale(conn):
            due.append("activity")
        if "audit" not in skip and sc.audit_due(conn, today):
            due.append("audit")
    except sqlite3.Error as exc:        # the next press asks again
        from . import runlog
        runlog.caught(exc, "tidy_due", level="warning")
    return due


def read_since_analysis(conn) -> dict:
    """The remittance claims and lines read since the last analysis began, and the dollars of their charges the payers
    did not pay (charge less paid, line by line, superseded copies left out): what a run that hands the analysis on, or
    leaves files to read, read and has not checked. {'claims', 'lines', 'cents', 'since'}; zeros before any analysis. A
    line reaches its file through its transaction (the compact store this host holds keeps no file hash on a line)."""
    out = {"claims": 0, "lines": 0, "cents": 0, "since": None}
    try:
        r = conn.execute("SELECT started_at FROM detection_runs ORDER BY finished_at DESC, rowid DESC LIMIT 1").fetchone()
        if not r or not r[0]:
            return out
        out["since"] = r[0]
        n_claims, n_lines, cents = conn.execute(
            """SELECT COUNT(DISTINCT l.claim_key), COUNT(*),
                      COALESCE(SUM(CASE WHEN COALESCE(l.charge_cents, 0) > COALESCE(l.paid_cents, 0)
                                        THEN COALESCE(l.charge_cents, 0) - COALESCE(l.paid_cents, 0) ELSE 0 END), 0)
               FROM lines l JOIN transactions t ON t.transaction_key = l.transaction_key
               JOIN files f ON f.sha256 = t.file_sha256
               WHERE f.ingested_at > ? AND COALESCE(t.superseded, 0) = 0""", (r[0],)).fetchone()
        out.update(claims=int(n_claims or 0), lines=int(n_lines or 0), cents=int(cents or 0))
    except sqlite3.Error as exc:        # a store with no lines yet: nothing said
        from . import runlog
        runlog.caught(exc, "read_since_analysis", level="warning")
    return out


def run_cloud_cycle(state, client=None, *, budget_s: int | None = None, trigger: str = "manual", full: bool = False,
                    force_detect: bool = False, email: bool = True, delay_s: int = 0, log=print) -> dict:
    """One cycle against the state folder. Returns a summary; `more` is true when a fresh invocation should continue.
    `delay_s` is a wait taken after the lock, used when a notification arrives with the first file of a batch."""
    from . import repair, runlog
    from . import selfcheck as sc
    from .alerts import ALERTS_DDL, notify, run_alerts
    from .detect import Detector, ensure_schema

    budget = budget_s or runtime.max_seconds()
    out: dict = {"started": _now(), "trigger": trigger, "mode": "serverless", "budget_seconds": budget}
    rid = runlog.new_run_id()               # in the lock too: a later run tells a run killed from one that finished
    token = acquire(state, run_id=rid)
    if not token:
        return {"skipped": "another cycle is running"}
    takeover = LAST_TAKEOVER
    # the run's memory figures are its own: a warm instance's earlier invocations do not count (runtime.reset_peak)
    runtime.reset_peak()
    # (H) the run's manifest and event log in the state folder's logs/; the cycle's own lines go there (scrubbed), and
    # Vercel's function log gets the one line printed at the end
    rl = runlog.start_run("cycle", trigger=trigger, state=state, host="serverless", run_id=rid)
    out["run_id"] = rl.run_id
    log = rl.log
    if trigger in PRESS_TRIGGERS:
        _answer_press(state, rl.run_id)     # the run a press asked for has the lock: a later press asks for its own
    reg = None
    store = None
    t0 = time.time()
    try:
        if delay_s:
            time.sleep(delay_s)
            t0 = time.time()
        prev_status = _read_json(state, STATUS)
        if prev_status.get(IMAGE_KEY):
            out[IMAGE_KEY] = prev_status[IMAGE_KEY]        # the record of the last save, kept by a run that saves nothing
        try:
            with rl.step("load"):
                store, existed = load_raw(state, status=out)
        except StoreImageProblem as exc:
            # the store is missing or is not the one last saved: stop before anything is read, folded or saved. The status
            # this run writes keeps the proof that a store existed, so the next run stops too until the image is back
            out["error"] = str(exc)
            out[IMAGE_KEY] = prev_status.get(IMAGE_KEY) or {"missing": True, "since": _now()}
            _image_alert(prev_status, out, email, log)
            out["finished"] = _now()
            out["seconds"] = round(time.time() - t0, 1)
            return out
        conn = store.conn
        ensure_schema(conn)
        conn.executescript(ALERTS_DDL + CLOUD_DDL)
        out["load_seconds"] = round(time.time() - t0, 1)
        mem = {"load": peak_mb()}
        out["memory_mb"] = mem
        # (H) the scrubber learns the store's names, the feed positions, the run_log rows of earlier runs, the registry
        runlog.SCRUB.learn(conn)
        rl.manifest["positions"] = {"before": runlog.positions(conn, state)}
        runlog.sync_store_from_index(conn, rl.sink)
        reg = repair.registry_for(rl, conn=conn)
        if takeover:
            repair.note_stale_lock(reg, ["lock.json"], "cloud.acquire")
        # (G) self-regulation: the last run's status (and a run the platform killed) decide what this one spreads or sheds
        killed = list(rl.manifest["sections"].get("killed_runs") or [])      # their open markers
        held = _takeover_kill(takeover, prev_status, killed, rl)               # the run whose lock this run took over
        if held:
            killed.append(held)
        killed = killed or None
        sc.adopt_regulation(conn, prev_status)         # an idle run before this one saved no store: its state is in its status
        plan = sc.plan_next(conn, prev_status, mode="serverless", budget_s=budget, killed=killed)
        out["plan"] = {k: plan[k] for k in ("level", "name", "skip", "overdue")}
        out["regulate"] = sc.regulation_state(conn)     # in status.json, for the next run whether or not this one saves
        rl.set("self_regulation", plan)
        if plan["actions"]:
            out["self_regulation"] = plan["actions"]
            repair.note_pressure(reg, [{"action": a} for a in plan["actions"]], reading=plan["pressure"])
        sc.mark(out, "load")
        ran: list[str] = []             # the optional steps this run did (an optional step's deferral ends once it has)

        # EMA's reports: the clean-up of report rows an older build read as exports (once per host; rows go only once
        # the fetched file is seen to be a report), and the repair of a run that saved the ledger and not the store
        from . import ema_reports, intake
        ema_dirty = False
        try:
            mig = ema_reports.migrate_misread(store, serverless=True, log=log)
            # the store is saved for it only when there was something to clean: on a store that holds nothing yet (a new
            # site, or its first runs on an empty folder) an image saved for the mark alone would claim a store existed
            # (hotfix 2d, CH-2); the mark goes with the next save, and running the check again until then finds nothing
            if not mig.get("skipped") and any(v for k, v in mig.items() if isinstance(v, int)):
                ema_dirty = True
                if mig.get("candidates"):
                    out["ema_migrate"] = {k: v for k, v in mig.items() if isinstance(v, int)}
        except Exception as exc:       # noqa: BLE001
            runlog.caught(exc, "ema_migrate")
            out["ema_migrate"] = {"error": type(exc).__name__}
        try:
            rep = repair_ledger(store, state, log=log)
            if rep:
                out["ema_repair"] = rep
                ema_dirty = True
        except Exception as exc:       # noqa: BLE001
            runlog.caught(exc, "ema_repair")
            out["ema_repair"] = {"error": type(exc).__name__}
        if rl.manifest["sections"].get("killed_runs"):
            repair.note_killed_runs(reg, rl.manifest["sections"]["killed_runs"], repaired=out.get("ema_repair"))
        # the practice's "How we pull this in our EMA" boxes (the Upload page keeps them in the state folder): a copy in the
        # store, so a move to the server keeps them
        howto_changed = False
        try:
            howto_changed = copy_howto(state, conn)
        except Exception as exc:       # noqa: BLE001
            runlog.caught(exc, "ema_howto")
            out["ema_howto"] = {"error": type(exc).__name__}
        # one Production file per run, read in the time it leaves for its summaries and the saves (75 s)
        slot = ProductionSlot(store, state, deadline=t0 + budget - 75, log=log, status=out)

        # Is the dashboard's copy the one built from this store? A run that died between the two saves leaves it behind.
        data_rev = (conn.execute("SELECT value FROM meta WHERE key='cloud:data_rev'").fetchone() or [None])[0]
        mark = _read_json(state, SERVING_MARK)
        serving_stale = bool(data_rev) and (mark.get("data_rev") != data_rev or mark.get("build") != SERVING_BUILD)

        journal = read_actions(state)
        # (E) a reopening asked in the app is the archive's (it brings the bill back before detection)
        from . import archive
        arc_sink = archive.StateSink(state)
        taken, journal = archive.take_journal(conn, journal, sink=arc_sink) if journal else ([], journal)
        requests_done: list[dict] = []         # what the Data tab's requests came to (an acceptance refused as stale is said)
        if journal:
            # the names on the journal's changes, learned before the fold: a warning logged while folding is masked too
            runlog.SCRUB.add_people(a.get("actor") for _n, a in journal if isinstance(a, dict))
        done, left, wrote = apply_actions(conn, journal, results=requests_done) if journal else ([], [], False)
        done, wrote = done + taken, wrote or bool(taken)
        if journal:
            # the names of the people whose changes the journal just brought in (a name new since the scrubber learned the
            # store's, at load): masked from here on should one reach this run's log
            runlog.SCRUB.learn_people(conn)
        if requests_done:
            out["selfcheck_requests"] = requests_done

        # uploads cut off part-way (their pieces and records, after a day) go at every run, not only when someone opens the
        # Upload page (a cut-off report can hold patient data)
        try:
            from .uploads import state_uploads
            swept = {k: v for k, v in (state_uploads(state).sweep() or {}).items() if v}
            if swept:
                out["uploads_swept"] = swept
        except Exception as exc:       # noqa: BLE001  (tried again by the next run)
            runlog.caught(exc, "uploads_sweep")
            out["uploads_sweep"] = {"error": type(exc).__name__}
        # what people uploaded in the app (remitwatch.intake): read before the Dropbox folder
        up_in: dict = {}
        try:
            with rl.step("intake"):
                up_in = intake.process_state(store, state, production=lambda p, rec, **kw: slot.apply(p, rec, via="upload", leave=True, **kw),
                                             deadline=t0 + budget * 0.5, max_bytes=MAX_FILE_BYTES, defer_close=True, log=log)
        except Exception as exc:       # noqa: BLE001
            runlog.caught(exc, "intake")
            up_in = {"error": type(exc).__name__}
        if up_in.get("pairs") or up_in.get("error") or up_in.get("remaining"):
            out["intake"] = {k: v for k, v in up_in.items() if k not in ("results", "to_close")}

        # TriZetto first (remitwatch.trizetto): the remittances straight from the clearinghouse, the freshest source
        tz: dict = {}
        if trizetto.configured():
            try:
                # (H) a whole pull is safe to repeat; a refused sign-in, and anything the ingest raises, are not
                with rl.step("trizetto"):
                    tz = repair.retry_call(trizetto.pull, conn, repair.not_retried(lambda items: _ingest_batch(store, items, log)),
                                           deadline=t0 + budget * 0.4, log=log, target="trizetto", registry=reg,
                                           until=t0 + budget * 0.3)
            except Exception as exc:
                runlog.caught(exc, "trizetto")
                tz = {"error": str(exc)[:300] if isinstance(exc, trizetto.TriZettoError) else f"{type(exc).__name__}: {str(exc)[:200]}"}
                if getattr(exc, "seen_changed", False):
                    tz["seen_changed"] = True   # a refused password's hold is in the store: save it, or the next run signs in again
                if getattr(exc, "hold_changed", False):
                    tz["hold_changed"] = True
            out["trizetto"] = {k: v for k, v in tz.items() if k != "seen_changed"}
        ingest: dict = {"skipped": "Dropbox credentials are not configured"}
        if client is not None:
            # (H) a network error on a read is tried again; the state folder is never wrapped (its writes go once)
            client = repair.resilient(client, "dropbox", registry=reg, until=t0 + budget * 0.5)
            # leave room for detection and for saving: ingestion gets at most half of the budget
            with rl.step("dropbox"):
                ingest = sync_and_ingest(store, client, state, deadline=t0 + budget * 0.5, full=full, log=log, slot=slot)
        out["ingest"] = {k: v for k, v in ingest.items() if k not in ("cursor", "remit_stats", "seen_changed", "ema_changed")}
        # reports kept waiting: fetched again by their Dropbox id (or from the state folder, for an upload)
        rr: dict = {}
        try:
            rr = reread_waiting(store, client, state, slot, deadline=t0 + budget * 0.5, log=log)
        except Exception as exc:       # noqa: BLE001
            runlog.caught(exc, "ema_waiting")
            rr = {"error": type(exc).__name__}
        if rr.get("read") or rr.get("gone") or rr.get("error"):
            out["ema_waiting"] = rr
        # (F3a's need) the 837 files read before the reader's current version, read again once from Dropbox, after what is
        # new in the folder: a claim that changed is analysed as after new files, and what is left goes to the next run
        r837: dict = {}
        if client is not None and not (ingest.get("remaining") or 0):
            try:
                with rl.step("reread_837"):
                    r837 = reread_837(store, client, deadline=t0 + budget * 0.5, log=log)
            except Exception as exc:       # noqa: BLE001  (what it read is committed; a later run goes on)
                runlog.caught(exc, "reread_837")
                r837 = {"error": type(exc).__name__}
            if r837:
                out["reread_837"] = {k: v for k, v in r837.items() if k != "noted"}
        applied = slot.finish(out)          # the Production file read this run: summaries, then the ledger saved
        n_ingested = sum((src.get(k) or 0) for src in (ingest, tz) for k in ("remits", "ema", "claims_837"))
        n_ingested += int(up_in.get("read") or 0) + int(rr.get("ema") or 0)
        ingested = n_ingested > 0 or bool(r837.get("claims"))
        remaining = ((ingest.get("remaining") or 0) > 0 or (tz.get("remaining") or 0) > 0 or (up_in.get("remaining") or 0) > 0
                     or (rr.get("later") or 0) > 0 or (r837.get("remaining") or 0) > 0)
        ema_dirty = bool(ema_dirty or getattr(store, "ema_changed", False) or up_in.get("changed") or rr.get("changed")
                         or applied or "ema_apply" in out)
        mem["ingest"] = peak_mb()
        sc.mark(out, "ingest")
        # a release that changed which copies of a remittance stand decides them again once, new files or not: the store is
        # then saved and analysed as after new files
        if store.recheck_supersession():
            ingested = True

        # exports read by a release that did not yet keep their pull time: fetch that once (see backfill_export_times)
        if client is not None and not remaining and "backfill" not in plan["skip"]:
            got = backfill_export_times(store, client, deadline=t0 + budget * 0.6, log=log)
            ran.append("backfill")
            if got:
                out["export_times"] = got
                activity.mark_pending(conn)     # the recent-weeks figures depend on them

        # (E) reopenings asked in the app, and archived bills new data arrived for, come back before detection
        back = [archive.process_requests(conn, sink=arc_sink, log=log), archive.restore_touched(conn, sink=arc_sink, log=log)]
        if any(b.get("changed") for b in back):
            ingested = True             # the bills that came back are analysed in this run (or the deferred one)
            out["archive_back"] = {"asked": back[0].get("restored", 0), "new_data": back[1].get("restored", 0)}

        pend_row = conn.execute("SELECT value FROM meta WHERE key='cloud:detect_pending'").fetchone()
        pending = pend_row is not None
        # what an analysis still to come has to check: files read since the last one ('1'), or nothing new, when a press
        # alone asked for it ('press'): the note of a run that hands it on says which (unchecked_note)
        pending_read = pending and pend_row[0] != "press"
        defer_why = None
        has_run = conn.execute("SELECT 1 FROM detection_runs LIMIT 1").fetchone() is not None
        has_lines = conn.execute("SELECT 1 FROM lines LIMIT 1").fetchone() is not None
        # a release that changed the rules re-analyses the archive once, even when no new file has arrived
        from .detect import DETECT_VERSION
        # the newest run; of two that finished within the same second, the one written last
        last_version = conn.execute("SELECT version FROM detection_runs ORDER BY finished_at DESC, rowid DESC LIMIT 1").fetchone() \
            if has_run else None
        rules_changed = bool(last_version) and last_version[0] != DETECT_VERSION
        # a status, hide or write-off policy a person set since the last run (the journal above): the stored states catch up
        from .pipeline import people_changed_since_detection
        people = bool(has_run) and people_changed_since_detection(conn)
        if people:
            out["people_changed"] = True
        need_detect = has_lines and (ingested or pending or force_detect or not has_run or rules_changed or people)
        ran_detect = False
        if applied:
            # a Production file was read: its summaries are in the store, and detection runs in a fresh invocation
            # (clean memory: this one held the ledger and the file)
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('cloud:detect_pending', '1')")
            out["deferred"] = "detection"
            defer_why = "production"
        elif need_detect and not remaining:
            last = conn.execute("SELECT value FROM meta WHERE key='cloud:detect_seconds'").fetchone()
            estimate = float(last[0]) if last else 60.0
            left_s = budget - (time.time() - t0)
            # Hand detection to a fresh invocation when this one may not have the time left for it, or has just read a
            # large batch: a fresh process starts with clean memory, and detection is the step that needs the most. Under
            # pressure (the self-regulation's level 1 and up) any run that read something hands it over. A press forces the
            # analysis with nothing new read (the owner's decision 1): it is handed on the same way, so a pressed run never
            # starts an analysis it has no time or memory left for.
            if (ingested or force_detect) and (plan["spread"] or left_s < estimate * 1.5 + 45 or n_ingested >= DEFER_DETECT_AFTER_FILES):
                conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('cloud:detect_pending', ?)",
                             ("1" if ingested or pending_read else "press",))
                out["deferred"] = "detection"
                defer_why = ("spread" if plan["spread"] else "time" if left_s < estimate * 1.5 + 45 else "files")
            else:
                t1 = time.time()
                det = Detector(store, rl.run_id)    # the detection run and the run log share one id
                with rl.step("detect"), deadline_guard(conn, t0 + budget, "Detection"):
                    summary = det.run()
                del det
                gc.collect()
                conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('cloud:detect_seconds', ?)", (str(round(time.time() - t1, 1)),))
                conn.execute("DELETE FROM meta WHERE key='cloud:detect_pending'")
                activity.mark_pending(conn)             # the provider tables are behind the store until they are rebuilt
                out["detect"] = {"findings": sum(summary.values()), "seconds": round(time.time() - t1, 1)}
                ran_detect = True
                mem["detect"] = peak_mb()
                sc.mark(out, "detect")
        elif need_detect:
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('cloud:detect_pending', '1')")
            defer_why = "waiting"
        conn.commit()

        # (E) retention in a run that did not analyse (its memory is free), with time left; the image is this host's memory
        # and its upload, so the pages the archive frees are given back (VACUUM)
        arc: dict = {}
        if has_lines and not ran_detect and not remaining and "deferred" not in out and budget - (time.time() - t0) > 120:
            try:
                with rl.step("archive"):
                    arc = archive.run(conn, sink=arc_sink, budget_state=sc.archive_budget_state(plan), run_id=rl.run_id,
                                      mode="serverless", deadline=t0 + budget - 90, log=log)
            except Exception as exc:       # noqa: BLE001  (the archive waits for the next run; the store stands)
                runlog.caught(exc, "archive")
                out["archive"] = {"error": type(exc).__name__}
            if arc.get("bills") or (arc.get("ar") or {}).get("files"):
                out["archive"] = {k: arc.get(k) for k in ("bills", "lines", "findings", "cutoff", "more")}
                out["archive_compact"] = archive.compact(conn)
                ema_dirty = True                            # the store and the dashboard's copy are saved (items left)
            if arc:
                rl.set("archive", {k: arc.get(k) for k in ("bills", "claims", "lines", "findings", "files", "cutoff", "seconds")})

        # What each clinician did: rebuilt after detection, after an upgrade that changed how it is built, and by the next
        # run when this one has no time left for it (the store keeps a note that the tables are behind). Under pressure the
        # self-regulation leaves them for a calm run (never for more than a week: selfcheck.plan_next).
        ran_activity = False
        if has_lines and not remaining and "deferred" not in out and activity.stale(conn) and "activity" not in plan["skip"] \
                and budget - (time.time() - t0) > 45:
            before = activity.status(conn)["error"]
            ran.append("activity")
            with rl.step("activity"), deadline_guard(conn, t0 + budget, "The provider tables", hard=False):
                out["activity"] = activity.refresh(store, log=log)
            # a build that failed is saved once too, so the dashboard can say why the tab is empty
            ran_activity = "error" not in out["activity"] or out["activity"]["error"] != before
            gc.collect()
            mem["activity"] = peak_mb()

        if left and ran_detect:
            # entries that named a finding this store did not hold: detection may just have brought it back
            again, left, wrote_more = apply_actions(conn, [(n, a) for n, a in journal if n in set(left)])
            done += again
            wrote = wrote or wrote_more
        folded = wrote              # the store and the dashboard's copy are saved when something was really written
        out["actions_folded"] = len(done)
        if left:
            out["actions_waiting"] = len(left)

        settled = not remaining and "deferred" not in out and has_lines
        marks = _read_json(state, MARKS) if settled else {}
        marks.setdefault("alerts", {})
        if settled:
            # (G) the track record, money given up without a decision, what the app cannot see (in dollars), the weekly
            # self-audit (in a run that did not analyse: its tables never meet detection's peak), housekeeping
            today = sc.today_of()
            for step, fn in (("selfcheck_track", lambda: sc.track_record(conn, today=today)),
                             ("selfcheck_given_up", lambda: sc.given_up(conn, today=today)),
                             ("selfcheck_blind", lambda: sc.blindness(conn, today=today))):
                try:
                    fn()
                except Exception as exc:       # noqa: BLE001  (measured again by the next run)
                    runlog.caught(exc, step)
                    out[step] = {"error": type(exc).__name__}
            if not ran_detect and "audit" not in plan["skip"] and sc.audit_due(conn, today):
                try:
                    with rl.step("self_audit"):
                        audit = sc.self_audit(conn, today=today)
                    if audit and not audit.get("skipped"):
                        out["self_audit"] = {k: audit.get(k) for k in ("gaps_n", "gaps_cents", "wasted_n", "alert")}
                        ran.append("audit")
                        ema_dirty = True        # its lists and figures are saved with the store
                except Exception as exc:       # noqa: BLE001
                    runlog.caught(exc, "selfcheck_audit")
                    out["selfcheck_audit"] = {"error": type(exc).__name__}
            try:
                out["housekeeping"] = sc.housekeeping(mode="serverless", state=state, conn=conn,
                                                      archive_result=out.get("archive_compact"), uploads=False)
            except Exception as exc:       # noqa: BLE001
                runlog.caught(exc, "housekeeping")
            with rl.step("alerts"):
                out["alerts"] = run_alerts(conn, run_id=rl.run_id, today=runtime.practice_today())   # deadlines close at midnight where the practice is
            # What was mailed is recorded outside the store (the store is not saved on an idle run): put it back. The
            # record names the alert as it was raised then; one that cleared and has been raised again is a new alert.
            conn.executemany("UPDATE alerts SET notified_at=? WHERE alert_id=? AND first_seen=? AND notified_at IS NULL",
                             [(m[0], aid, m[1]) for aid, m in marks["alerts"].items() if isinstance(m, list) and len(m) == 2])
            conn.commit()

        # Save only what changed, the store first: it is the record, and the dashboard's copy is made from it. An idle
        # run (nothing new in Dropbox, nothing recorded by people) uploads neither.
        alerts_changed = bool((out.get("alerts") or {}).get("new") or (out.get("alerts") or {}).get("cleared"))
        progressed = ingested or (ingest.get("from_archive") or 0) > 0
        # a TriZetto sign-in hold set or ended is on Today's data checks, and the /setup test reads the server's key from the
        # dashboard's copy (trust on first use): either makes the copy again
        shown_changed = bool(ran_detect or ran_activity or folded or alerts_changed or (progressed and not existed)
                             or tz.get("hold_changed") or tz.get("hostkey") or ema_dirty)
        if shown_changed:
            data_rev = str(int(data_rev or 0) + 1)
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('cloud:data_rev', ?)", (data_rev,))
            conn.commit()
        # (G) what this run shed and ran; the weekly report is due when it goes out after the saves below
        every = int(os.environ.get("REMITWATCH_REPORT_EVERY_DAYS", "7"))
        last_report = marks.get("last_report") or (conn.execute("SELECT value FROM meta WHERE key='pipeline:last_report'").fetchone() or [None])[0]
        report_due = bool(settled and (not last_report or _age_seconds(last_report) >= every * 86400) and "report" not in plan["skip"]
                          and conn.execute("SELECT 1 FROM detection_runs LIMIT 1").fetchone())
        if report_due:
            ran.append("report")
        out["shed"] = list(plan["skip"])
        try:
            sc.record_did(conn, {"shed": plan["skip"], "level": plan["level"], "ran": ran})
        except Exception as exc:       # noqa: BLE001
            runlog.caught(exc, "selfcheck_did")
        # (E) this run's size and memory, for the Data tab and the next run's horizon
        try:
            archive.record_run(conn, run_id=rl.run_id, result=arc or None,
                               measured=archive.measure(conn, peak=peak_mb(), mode="serverless", limit_mb=runtime.memory_mb()))
        except Exception as exc:        # noqa: BLE001  (a measure that fails never stops the run; it is logged)
            runlog.caught(exc, "archive_measure")
            out["archive_measure"] = {"error": type(exc).__name__}
        # (H) reports the readers held, the steps that went well (a killed run's count clears), old log rows; the
        # registry's rows are saved with the store
        try:
            repair.sweep_reports(conn, reg, rl.manifest["started_at"])
            repair.note_run_finished(reg, rl.manifest["steps"])
            runlog.prune_store(conn)
            repair.prune(conn)
        except Exception as exc:       # noqa: BLE001
            runlog.caught(exc, "repair_sweep", level="warning")
        repairs_dirty = bool(reg is not None and reg.dirty)
        if repairs_dirty:
            reg.save()                  # meta repair:state (saved with the store) and logs/repair_state.json
        rl.save_to_store(conn)
        if shown_changed or progressed or "deferred" in out or ingest.get("seen_changed") or tz.get("seen_changed") or ema_dirty \
                or howto_changed or repairs_dirty or r837.get("noted"):
            t2 = time.time()
            with rl.step("save_store"):
                meta = save_raw(state, store, log, status=out)      # a lost answer leaves its pending record in this run's status
            out["store"] = {"gz_bytes": (meta or {}).get("size"), "save_seconds": round(time.time() - t2, 1),
                            "image_mb": (meta or {}).get("image_mb")}
            out[IMAGE_KEY] = meta.get(IMAGE_KEY)        # recorded in status.json at the save; the run's status keeps it
            slot.clear_attempts()           # the Production file read this run is in the saved ledger and store
        # the uploads read this run go to intake/done/ now that what they changed is saved (a run stopped before this
        # point reads them again: 'already' once applied)
        intake.close_state_pairs(state, up_in.get("to_close") or [])
        # read uploads kept past their time go (never a held, waiting or live report's copy, nor each report's newest)
        try:
            kept = intake.retain_done_state(store, state, log=log)
            if kept.get("removed") or kept.get("error"):
                out["intake_retain"] = kept
        except Exception as exc:       # noqa: BLE001  (tried again by the next run)
            runlog.caught(exc, "intake_retain")
            out["intake_retain"] = {"error": type(exc).__name__}
        # an upload finished while this run went on (its own request to start a run was turned away by this run's lock):
        # a follow-up run reads it now, not tomorrow
        try:
            left_pairs = 0 if up_in.get("error") else len(intake.state_pending(state))
        except Exception as exc:       # noqa: BLE001  (the next scheduled run reads it)
            runlog.caught(exc, "intake_pending", level="warning")
            left_pairs = 0
        if left_pairs:
            out["intake_waiting"] = left_pairs
        if applied:
            slot.drop_copies()
        if shown_changed or serving_stale:
            with rl.step("save_serving"):
                out["serving"] = save_serving(state, conn)
            state.put(SERVING_MARK, json.dumps({"data_rev": data_rev, "build": SERVING_BUILD, "at": _now()}).encode())
        # after the store, so the position never runs ahead of what was saved; and only beside a store image (saved now, or
        # there when the run started): a cursor proves an earlier save (_saved_before), so a first run that found an empty
        # data folder and saved nothing must leave none, or every run after it would stop as if the image were missing
        if ingest.get("cursor") and (existed or out.get("store")):
            state.put(CURSOR, json.dumps({"cursor": ingest["cursor"], "root": os.environ.get("DROPBOX_ROOT_PATH", "/Trizetto Data"), "at": _now()}).encode())
        # the journal: entries now in the saved store go; one whose finding never turned up is kept for a week
        gone = done + [n for n in left if expired(n)]
        if gone:
            state.delete_many(gone)

        # Mail goes out after everything is saved, and what was sent is written down at once in its own small file, so
        # neither a failed save nor an idle run can send the same alert or report again.
        if settled:
            with rl.step("mail"):
                out["notified"] = notify(conn, log=log) if email else {"sent": 0, "skipped": True}
            if out["notified"].get("sent"):
                marks["alerts"] = {r[0]: [r[1], r[2]] for r in conn.execute(
                    "SELECT alert_id, notified_at, first_seen FROM alerts WHERE notified_at IS NOT NULL AND status != 'cleared'")}
                _write_json(state, MARKS, marks)
            if report_due:             # (G) under pressure the weekly report waits for a calm run (never past a week)
                from .report import build_report, email_report
                try:
                    text = build_report(store, ingest_stats=ingest if "remits" in ingest else None)
                    state.put(f"reports/remitwatch-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}.md", build_report(store, detail=True).encode())
                    out["report_emailed"] = email_report(text) if email else False
                except Exception as exc:
                    runlog.caught(exc, "report")
                    out["report_error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
                marks["last_report"] = _now()
                _write_json(state, MARKS, marks)
        out["more"] = bool(remaining or "deferred" in out or left_pairs)
        # a forced analysis handed to the next run is progress: the chain goes on to it (else a press would end there)
        out["progress"] = bool(progressed or ran_detect or ran_activity or (force_detect and out.get("deferred") == "detection"))
        # (the owner's decision 1) a run that analysed leaves to one more run of the chain the work only a run that does not
        # analyse does (the archive, the provider tables it had no time left for, the weekly self-audit): nothing else
        # starts a run on this host, so without it they would never run here (next_run 'tidy')
        if ran_detect and not out["more"]:
            tidy = tidy_due(conn, plan, today=sc.today_of())
            if tidy:
                out["tidy"] = tidy
        out["changed"] = bool(shown_changed or progressed)
        try:
            one = lambda sql: conn.execute(sql).fetchone()[0]       # noqa: E731
            out["totals"] = {"remit_files": one("SELECT COUNT(*) FROM files WHERE kind='835'"),
                             "findings": one("SELECT COUNT(*) FROM findings WHERE COALESCE(stale,0)=0") if has_run or ran_detect else 0,
                             "newest_payment": one("SELECT MAX(payment_date) FROM transactions WHERE superseded=0")}
        except sqlite3.Error as exc:
            runlog.caught(exc, "totals", level="warning")
        out["finished"] = _now()
        out["seconds"] = round(time.time() - t0, 1)
        # (the owner's decision 1) when this site last analysed, for every page: this run, or the one before it
        if ran_detect:
            out["analysed"] = {"at": out["finished"], "run_id": rl.run_id, "trigger": trigger,
                               "findings": (out.get("totals") or {}).get("findings")}
            try:        # what the lists hold after it: the open items (Work now and Waiting), not every item it found
                out["analysed"]["open"], out["analysed"]["open_cents"] = open_items(conn)
            except sqlite3.Error as exc:
                runlog.caught(exc, "analysed_open", level="warning")
        else:
            out["analysed"] = prev_status.get("analysed") or _last_detection(conn)
        # what this run could not check, in items and dollars (the analysis still to come, an optional check shed)
        pend_row = conn.execute("SELECT value FROM meta WHERE key='cloud:detect_pending'").fetchone()
        note = unchecked_note(conn, pending=pend_row is not None and not ran_detect, waiting_files=bool(remaining),
                              plan=plan, analysed=out.get("analysed"), read_since=pend_row is None or pend_row[0] != "press",
                              why=defer_why)
        if note:
            out["unchecked"] = note
        mem["peak"] = peak_mb()
        mem["limit"] = runtime.memory_mb()
        return out
    except Exception as exc:
        rl.fail(exc, "cycle")
        out["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
        out["finished"] = _now()
        out["seconds"] = round(time.time() - t0, 1)
        raise
    finally:
        try:
            if store is not None:
                store.close()
        except Exception as exc:
            runlog.caught(exc, "store_close", level="warning")
        try:        # every run writes its status, so the dashboard can say when Dropbox was last checked
            if out.get("changed"):
                out["last_change"] = out.get("finished")
            else:
                prev = json.loads(state.get(STATUS) or b"{}")
                out["last_change"] = prev.get("last_change")
                out.setdefault("totals", prev.get("totals"))
            if "analysed" not in out:       # a run that stopped early: when this site last analysed stays said
                out["analysed"] = (json.loads(state.get(STATUS) or b"{}") or {}).get("analysed")
        except Exception as exc:
            runlog.caught(exc, "last_change", level="warning")
        write_status(state, out)        # before the lock goes, so nobody reads "not running" beside the previous run's status
        try:
            if reg is not None and reg.dirty:
                reg.save()              # a failed run still counts its failure in the state folder
            print(rl.finish(out=out, state=state), flush=True)      # the one line in Vercel's function log
        except Exception as exc:        # noqa: BLE001
            print(f"RemitWatch run {rl.run_id}: the log could not be written ({type(exc).__name__})", flush=True)
        release(state, token)


class Asked:
    """trigger_cycle's answer: true when the run was asked for (its request is under way); otherwise false, with why in
    words a person can act on (`why`), the status a press answers with (`status`) and the HTTP status the site's own call
    got, if any (`code`)."""
    __slots__ = ("ok", "why", "status", "code")

    def __init__(self, ok: bool, why: str | None = None, status: int | None = None, code: int | None = None):
        self.ok, self.why, self.status, self.code = bool(ok), why, status, code

    def __bool__(self) -> bool:
        return self.ok

    def __repr__(self) -> str:
        return f"Asked({self.ok}, code={self.code})"


NO_SECRET_TEXT = "The site cannot start a run: CRON_SECRET is not set. Add it in Vercel and redeploy."


def not_started_why(base_url: str | None) -> tuple[str, int]:
    """Why a run could not be asked for, when the call itself gave no reason: the setting that is missing, or the call."""
    if not os.environ.get("CRON_SECRET"):
        return NO_SECRET_TEXT, 503
    if not base_url:
        return ("The site cannot start a run: it does not know its own address. Open it at its production address and "
                "press again."), 503
    return "The site's own call to start the run did not get through. Try again in a minute.", 502


def _refused_why(code: int) -> str:
    """The site's own call to /api/cron/cycle was answered `code` at once: what that means, and what to do."""
    if code in (401, 403):
        if os.environ.get("VERCEL_AUTOMATION_BYPASS_SECRET"):
            return (f"The site's own call to start the run was refused ({code}). Check that VERCEL_AUTOMATION_BYPASS_SECRET is "
                    "the secret Vercel shows (Settings, Deployment Protection, Protection Bypass for Automation), then "
                    "redeploy.")
        return (f"The site's own call to start the run was refused ({code}). If the production address is protected "
                "(Vercel, Settings, Deployment Protection), set VERCEL_AUTOMATION_BYPASS_SECRET (Protection Bypass for "
                "Automation) and redeploy.")
    if code == 503:
        return ("The site's own call to start the run was answered 503: the state folder in Dropbox could not be reached. "
                "Try again in a minute; if it stays, open /setup.")
    if code in (301, 302, 307, 308):
        return (f"The site's own call to start the run was sent elsewhere ({code}). Open the site at its production address "
                "and press again.")
    if code >= 500:
        return (f"The site's own call to start the run failed ({code}). Try again in a minute; if it fails again, make the "
                "file for Claude in Logs and diagnostics below.")
    return f"The site's own call to start the run was refused ({code}). Try again in a minute."


def trigger_cycle(base_url: str | None, trigger: str = "webhook", delay: int = 0, **params: Any) -> Asked:
    """Start a cycle in a fresh invocation and return without waiting for it. The request is sent and the reply abandoned;
    the platform runs the invocation to its end regardless. The answer is true when the run is under way; when it is not,
    it says why (no secret, no address, the call refused with its status, or a network error)."""
    import requests
    secret = os.environ.get("CRON_SECRET")
    if not secret or not base_url:
        why, status = not_started_why(base_url)
        return Asked(False, why, status)
    headers = {"Authorization": f"Bearer {secret.strip()}"}
    bypass = os.environ.get("VERCEL_AUTOMATION_BYPASS_SECRET")
    if bypass:      # only present when the project protects its production address as well
        headers["x-vercel-protection-bypass"] = bypass
    url = f"{base_url.rstrip('/')}/api/cron/cycle"
    try:
        r = requests.post(url, params=dict(params, trigger=trigger, delay=delay), headers=headers, timeout=(5, 1.5), allow_redirects=False)
        target = r.headers.get("location") or ""
        if r.status_code in (301, 302, 307, 308) and _same_site(url, target):
            # the secret goes only to this site's own name, with or without "www."
            r = requests.post(target, headers=headers, timeout=(5, 1.5), allow_redirects=False)
    except requests.exceptions.ReadTimeout:
        return Asked(True)          # the normal case: the run is under way and will outlast this call
    except Exception as exc:        # said to the person who pressed; nothing started
        from . import runlog
        runlog.caught(exc, "trigger_cycle", level="warning")
        return Asked(False, "The site could not reach its own address to start the run (a network error). Try again in a "
                            "minute.", 502)
    if r.status_code < 300:
        return Asked(True, code=r.status_code)
    return Asked(False, _refused_why(r.status_code), 502, code=r.status_code)


def _same_site(a: str, b: str) -> bool:
    """Is `b` an https address on the same host as `a`, give or take "www."? The answer is taken from the address as the
    HTTP client will read it, and anything with a login part or a backslash is refused: two parsers can disagree there."""
    from urllib.parse import urlsplit

    import requests
    if not b or any(ch in b for ch in "\\@ \t\r\n"):
        return False
    try:
        y = urlsplit(requests.Request("POST", b).prepare().url)
    except Exception as exc:        # an address the client cannot read is not this site
        from . import runlog
        runlog.caught(exc, "same_site", level="warning")
        return False
    x = urlsplit(a)
    strip = lambda h: (h or "").lower().removeprefix("www.")       # noqa: E731
    return y.scheme == "https" and bool(y.hostname) and strip(x.hostname) == strip(y.hostname)


def backfill_export_times(store, client, deadline: float, log=print) -> int:
    """Exports read by an earlier release have no pull time on record, and the provider view needs it to tell a light day
    from a day whose bills were not written yet. The workbook holds it, so each such export is fetched once more, the
    time read, and noted. An export that can no longer be found is noted without a time and not asked for again.
    Returns how many were given a time."""
    import hashlib

    from .ema_exports import export_created
    conn = store.conn
    missing = [r[0] for r in conn.execute(
        """SELECT DISTINCT e.source_name FROM ema_rows e
           WHERE e.family = 'charge_lines' AND e.source_name IS NOT NULL
             AND NOT EXISTS (SELECT 1 FROM ema_sources s WHERE s.source_name = e.source_name)""")]
    if not missing:
        return 0
    where: dict[str, str] = {}
    for (path,) in conn.execute("SELECT path FROM dropbox_seen WHERE lower(path) LIKE '%.xlsx' ORDER BY at DESC"):
        if path:
            where.setdefault(os.path.basename(path), path)
    n = 0
    for name in missing:
        if time.time() > deadline:
            break
        path = where.get(name)
        if not path:
            store.record_export(name, None, "charge_lines", None, 0)
            continue
        try:
            data, _meta = client.download_bytes(path)
        except Exception as exc:        # noqa: BLE001  (tried again by the next run; nothing depends on it meanwhile)
            log(f"export pull time: could not fetch {name}: {type(exc).__name__}")
            continue
        created = export_created(io.BytesIO(data))
        store.record_export(name, hashlib.sha256(data).hexdigest(), "charge_lines", created, 0)
        n += 1 if created else 0
    return n


# --------------------------------------------------------------------------- setup checks

class _ServerStore(Exception):
    """A server has no state folder to try a write in (setup_status)."""


def setup_status(base_url: str | None = None, probe: bool = True) -> dict:
    """Every setting the deployment needs, checked live, in the order the setup sheet sets them."""
    from .dropbox_sync import DropboxAuthError, DropboxClient
    env = os.environ.get
    checks: list[dict] = []

    def add(key: str, label: str, ok: bool | None, detail: str = "", fix: str = "") -> None:
        checks.append({"key": key, "label": label, "ok": ok, "detail": detail, "fix": "" if ok else fix})

    # on a server the settings are its .env file and a restart applies them; on Vercel, the project's settings and a redeploy
    server = not runtime.serverless()
    where, apply = ("in the server's .env", "then restart it (docker compose up -d)") if server else ("in Vercel", "then redeploy")
    pw = env("REMITWATCH_PASSWORD") or ""
    pw_ok = bool(pw) and (not runtime.serverless() or (pw.isascii() and len(pw) >= 12))
    add("password", "Dashboard password (REMITWATCH_PASSWORD)", pw_ok,
        "set" if pw_ok else ("not set" if not pw else "set, but shorter than 12 characters or not plain letters and digits"),
        f"Set REMITWATCH_PASSWORD {where} to 12 or more plain letters and digits, {apply}.")
    cron = env("CRON_SECRET") or ""
    cron_ok = bool(cron) and cron == cron.strip() and "\n" not in cron and len(cron) >= 16
    if server and not cron:     # the server's own scheduler runs the cycles; the secret only lets an outside caller start one
        add("cron_secret", "Scheduler secret (CRON_SECRET, optional on a server)", None,
            "not set: not needed here, the server's own scheduler runs the cycles (set it only for an outside caller of /api/cron/)")
    else:
        add("cron_secret", "Scheduler secret (CRON_SECRET)", cron_ok,
            "set" if cron_ok else ("not set" if not cron else "set, but it is shorter than 16 characters or has a space or line break at an end"),
            f"Add CRON_SECRET {where} (a long random value with no spaces), {apply}. Without it the site cannot start its own updates.")
    key, secret, token = env("DROPBOX_APP_KEY"), env("DROPBOX_APP_SECRET"), env("DROPBOX_REFRESH_TOKEN")
    add("dropbox_app", "Dropbox app key and secret", bool(key and secret), "set" if key and secret else "not set",
        f"Add DROPBOX_APP_KEY and DROPBOX_APP_SECRET {where}, {apply}.")
    out: dict = {"checks": checks, "mode": "serverless" if runtime.serverless() else "server"}
    if key:
        out["authorize_url"] = DropboxClient.authorize_url(key)
    add("dropbox_token", "Dropbox connection (DROPBOX_REFRESH_TOKEN)", bool(token) and None, "not set" if not token else "",
        f"Use the Connect Dropbox step on this page, add the value it shows as DROPBOX_REFRESH_TOKEN {where}, {apply}.")
    root = env("DROPBOX_ROOT_PATH", "/Trizetto Data")
    if key and secret and token and probe:
        client = DropboxClient(attempts=1, timeout=10)      # the page must answer even when Dropbox is slow
        try:
            acct = client.current_account()
            checks[-1].update(ok=True, detail=f"connected as {(acct.get('name') or {}).get('display_name') or 'the account that approved the app'}", fix="")
            info = acct.get("root_info") or {}
            team_root = info.get("root_namespace_id") if info.get("root_namespace_id") != info.get("home_namespace_id") else None
        except DropboxAuthError as exc:
            out["reconnect"] = True
            checks[-1].update(ok=False, detail=str(exc)[:200], fix="The token was refused. Repeat the Connect Dropbox step and replace DROPBOX_REFRESH_TOKEN.")
            team_root = None
        except Exception as exc:
            checks[-1].update(ok=False, detail=str(exc)[:200], fix="Dropbox did not answer just now. Wait a minute and press Check again.")
            team_root = None
        if checks[-1]["ok"]:
            try:
                res = client._rpc("files/list_folder", {"path": root, "recursive": False, "limit": 200})
                n = len(res.get("entries", []))
                add("data_folder", f"Data folder {root}", True, f"found, {n}{'+' if res.get('has_more') else ''} items at the top level")
            except Exception as exc:
                hint = (f" This account is in a team space: add DROPBOX_PATH_ROOT_NS={team_root} {where}, {apply}." if team_root and not env("DROPBOX_PATH_ROOT_NS") else "")
                add("data_folder", f"Data folder {root}", False, str(exc)[:160],
                    "The folder was not found. Check DROPBOX_ROOT_PATH matches the folder name exactly, starting with a slash." + hint)
            from .state import DropboxState
            if server:
                st = None   # a server keeps its store on its own disk (checked below), not in a Dropbox folder
            elif env("REMITWATCH_STATE_DIR"):
                st = state_from_env()       # a laptop trying the mode with a local state folder: check that folder
            else:
                # a write is tried a few times: Dropbox answers "busy" when something else is writing to the account
                st = DropboxState(DropboxClient(attempts=3, timeout=10), env("REMITWATCH_STATE_PATH", "/RemitWatch-state"))
            try:
                if st is None:
                    raise _ServerStore()
                st.put("setup-check.txt", b"ok")
                st.delete("setup-check.txt")
                add("state_folder", f"Private storage ({st.describe()})", True, "the app can write and delete here")
            except _ServerStore:
                pass
            except DropboxAuthError as exc:
                out["reconnect"] = True
                add("state_folder", "Private storage", False, str(exc)[:200],
                    "The app cannot write to Dropbox. In the Dropbox Developer Console open the app, Permissions tab, tick files.content.write, "
                    "click Save changes, then repeat the Connect Dropbox step: a token only carries the permissions ticked when it was made.")
            except Exception as exc:
                add("state_folder", "Private storage", False, str(exc)[:160],
                    "Dropbox did not accept a test write just now. Wait a minute and press Check again. Nothing needs to be changed unless this stays red.")
    else:
        add("data_folder", f"Data folder {root}", None, "checked once Dropbox is connected")
        if not server:
            add("state_folder", "Private storage", None, "checked once Dropbox is connected")
    if server:
        db = os.environ.get("REMITWATCH_DB", "data/remitwatch.sqlite")
        try:
            free = shutil.disk_usage(os.path.dirname(os.path.abspath(db)) or ".").free // (1024 * 1024)
        except OSError:
            free = None
        size = os.path.getsize(db) // (1024 * 1024) if os.path.exists(db) else None
        add("store", f"The store ({db})", (free is None or free >= 2048) and size is not None,
            (f"{size:,} MB" if size is not None else "not made yet") + (f"; {free:,} MB free on its disk" if free is not None else ""),
            "Keep at least 2 GB free on the disk that holds the store (a run writes a copy beside it); the store is made when the site starts.")
    rt, st_ = runtime.describe(), runtime.selftest()
    add("runtime", "Runtime", bool(rt["in_memory_store"] and st_.get("ok")),
        f"Python {rt['python']}, SQLite {rt['sqlite']}" + ("" if st_.get("ok") else f"; self-test failed: {st_.get('error') or 'wrong result'}"),
        "This host cannot hold the store in memory and write its scratch files, which the site needs. Tell the practice owner.")
    mem, secs = runtime.memory_mb(), runtime.max_seconds()
    if server:
        add("limits", "Memory", None if mem is None else mem >= NEEDED_MEMORY_MB, f"{mem or 'not reported by the host'} MB",
            f"A run needs about {NEEDED_MEMORY_MB:,} MB at its peak: give the server 4 GB of memory.")
    else:
        add("limits", "Function memory and time", None if mem is None else mem >= NEEDED_MEMORY_MB,
            f"memory {mem or 'not reported by the host'} MB, time allowed for one run {secs} s",
            f"A run needs about {NEEDED_MEMORY_MB:,} MB at its peak. In Vercel open Settings, Functions, Function CPU, choose Performance (needs the Pro plan), then redeploy.")
    domains = env("APPEAL_EMAIL_DOMAINS") or ""
    add("email", "Email for alerts and the weekly summary (optional)", bool(env("SMTP_HOST") and env("REPORT_TO")) or None,
        ("configured" if env("SMTP_HOST") and env("REPORT_TO") else "not configured")
        + (f"; appeal packets may be emailed to {domains}" if domains else "; appeal packets cannot be emailed until APPEAL_EMAIL_DOMAINS names the domains allowed"))
    add("note_review", "Visit-note review (optional)", bool(env("ANTHROPIC_API_KEY")) or None,
        "on" if env("ANTHROPIC_API_KEY") else "off: packets are built from the template")
    mm_on = bool(env("MODMED_CLIENT_ID") and (env("MODMED_PRIVATE_KEY") or env("MODMED_CLIENT_SECRET")))
    # the key's id, so a person can see the key they put in the settings is the one this deployment uses
    mm_key, mm_bad = "", ""
    if env("MODMED_PRIVATE_KEY"):
        from . import modmed
        try:
            k = (modmed.jwks() or {"keys": [{}]})["keys"][0]
            mm_key = f"key {k.get('alg')}, id {k.get('kid')}"
        except modmed.ModMedError as exc:
            mm_bad = str(exc)
    add("modmed", "ModMed EMA: visit notes for appeals (optional)", False if mm_bad else (mm_on or None),
        mm_bad or (f"set ({mm_key}): press Test the ModMed connection below" if mm_on and mm_key else
                   "set: press Test the ModMed connection below" if mm_on else
                   f"{mm_key} is set and published at /.well-known/jwks.json: register the app with ModMed with that address, "
                   "then set MODMED_CLIENT_ID" if mm_key else
                   "not set: notes are attached by hand. To connect EMA, follow docs/OPERATIONS.md, Connecting ModMed EMA"),
        f"Paste the whole key into MODMED_PRIVATE_KEY again, BEGIN and END lines included, {apply}." if mm_bad else "")
    out["modmed"] = {"configured": mm_on, "key": bool(env("MODMED_PRIVATE_KEY")), "kid": mm_key.rsplit("id ", 1)[-1] if mm_key else None}
    # One site sends faxes (fax.sender, REMITWATCH_FAX_SENDER): the line can be set up on both, and the other one shows the
    # Faxes page and opens the faxes that came in, but sends nothing
    from . import fax
    fx_on, fx_line = fax.line() is not None, env("SRFAX_CALLER_ID") or ""
    fx_away = fax.not_here()
    fx_raw = (env(fax.SENDER_SETTING) or "").strip()
    fx_typo = bool(fx_raw) and fx_raw.lower() not in ("server", "serverless", "none")     # a typo pauses faxing on this site
    if not fx_on:
        fx_ok, fx_detail = None, "not set: appeal packets are downloaded and sent by hand. To fax them, follow docs/OPERATIONS.md, Faxing appeals"
        fx_fix = ""
    elif fx_away:
        fx_ok = False if fx_typo else None
        fx_detail = f"set, but {fx_away[0].lower()}{fx_away[1:]}" + (f" It says '{fx_raw}', which names no site." if fx_typo else "")
        fx_fix = (f"Set {fax.SENDER_SETTING} {where} to server (the practice's server sends, the default) or serverless (the "
                  f"Vercel site sends); none pauses faxing. Then {apply.removeprefix('then ')}." if fx_typo else "")
    else:
        fx_ok = bool(fx_line and env("SRFAX_SENDER_EMAIL"))
        fx_detail = (f"set: faxes go out from {fx_line}, and letters give it for the payer's answer; press Test the fax line below"
                     if fx_ok else "set, but SRFAX_CALLER_ID (the line's own number) or SRFAX_SENDER_EMAIL is missing")
        fx_fix = f"Set SRFAX_CALLER_ID to the SRFax fax number and SRFAX_SENDER_EMAIL to the address SRFax sends its notices to {where}, {apply}."
    add("fax", "Fax line: appeals faxed, payers' answers read (optional)", fx_ok, fx_detail, fx_fix)
    out["fax"] = {"configured": fx_on, "sends": fax.configured(), "not_here": fx_away}
    # TriZetto is read by one site only: the server, or a serverless host switched on for it (trizetto.here), because each
    # site keeps its own hold on a refused password and its own reminder before the password expires
    tz_set = bool(env("TRIZETTO_SFTP_USER") and env("TRIZETTO_SFTP_PASSWORD"))
    tz_here = trizetto.here()
    tz_on = tz_set and tz_here
    if tz_on:
        from .insights import password_days
        raw_days = (env("TRIZETTO_SFTP_PASSWORD_DAYS") or "").strip()
        days = password_days(raw_days)
        if days is None:
            reminder = (". TRIZETTO_SFTP_PASSWORD_DAYS is not a number of days, so no reminder comes before the password "
                        f"expires: enter it again {where}")
        elif days == 0 and raw_days:
            reminder = "; TRIZETTO_SFTP_PASSWORD_DAYS says the password never expires, so no reminder is due"
        elif not (env("TRIZETTO_SFTP_PASSWORD_SET") and days):
            reminder = (". No reminder comes before the password expires until TRIZETTO_SFTP_PASSWORD_SET (the day it was set, "
                        f"YYYY-MM-DD) and TRIZETTO_SFTP_PASSWORD_DAYS (how many days TriZetto lets it last) are set {where}")
        else:
            reminder = ""
        tz_detail = "set: press Test the TriZetto connection below" + reminder
    elif tz_set:
        tz_detail = ("set, but this site does not read TriZetto: the server does, so only one site signs in with the login "
                     "(docs/OPERATIONS.md, TriZetto direct)")
    else:
        tz_detail = ("not set: remittances come only from the Dropbox folder. To read them straight from TriZetto, follow "
                     "docs/OPERATIONS.md, TriZetto direct" + ("; the server reads TriZetto, not this site" if not tz_here else ""))
    add("trizetto", "TriZetto direct: remittances from the clearinghouse, no Dropbox needed (optional)", tz_on or None, tz_detail,
        f"Remove the TRIZETTO_SFTP_ settings {where}, {apply}. Set {trizetto.SERVERLESS_SETTING}=1 instead only if no server "
        "reads TriZetto with this login." if tz_set and not tz_here else "")
    out["trizetto"] = {"configured": tz_on, "set": tz_set, "here": tz_here}
    missing = [k for k in ("PRACTICE_NAME", "PRACTICE_ADDRESS", "PRACTICE_TIN", "PRACTICE_NPI", "PRACTICE_PHONE", "PRACTICE_EMAIL") if not env(k)]
    if not (env("SRFAX_CALLER_ID") or env("PRACTICE_FAX")):
        missing.append("PRACTICE_FAX (or SRFAX_CALLER_ID)")
    add("letterhead", "Practice details for appeal letters (optional)", None if missing else True,
        "set" if not missing else "not set: " + ", ".join(missing) + ". Letters show bracketed words in their place, and none is faxed until these are set.")
    owner = env("REMITWATCH_OWNER_PASSWORD") or ""
    owner_bad = runtime.owner_password_problem(owner, pw)
    add("owner_password", "Owner's password for the Providers tab (optional)", (not owner_bad) if owner else None,
        ("on: what each clinician is doing opens only after the owner's password is typed into the Providers tab" if not owner_bad else f"set, but it {owner_bad}")
        if owner else "off: everyone who can sign in sees the Providers tab",
        f"Replace REMITWATCH_OWNER_PASSWORD {where} with 12 or more plain letters and digits, {apply}. Leave the dashboard password as it is. "
        "Until then the Providers tab is closed to everyone.")
    if base_url:
        out["webhook_url"] = f"{base_url.rstrip('/')}/api/dropbox/webhook"
    st = state_from_env(fast=True) if (key and secret and token) or env("REMITWATCH_STATE_DIR") else None
    if st is not None and probe:
        try:
            raw = st.get(STATUS)
            out["last_cycle"] = json.loads(raw) if raw else None
            out["has_data"] = st.meta(SERVING) is not None
            out["running"], out["stalled"] = lock_state(st)
        except Exception as exc:
            out["storage_error"] = str(exc)[:160]
    if not server:      # the owner's decision 1: this site analyses only on a press; the page says when it last did
        out["press_only"] = True
        out["press_text"] = press_only_text()
        out["analysed"] = (out.get("last_cycle") or {}).get("analysed")
    required = [c for c in checks if c["key"] in (("password", "dropbox_app", "dropbox_token", "data_folder", "store") if server else
                                                    ("password", "cron_secret", "dropbox_app", "dropbox_token", "data_folder", "state_folder"))]
    out["ready"] = all(c["ok"] for c in required)
    # the Connect Dropbox step is needed when there is no token yet, or Dropbox refused the one in place or its
    # permissions; not when Dropbox was merely slow to answer
    out["connect_needed"] = bool(key and secret) and (not token or bool(out.pop("reconnect", False)))
    return out
