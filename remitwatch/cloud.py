"""
Serverless operation (Vercel and similar hosts): nothing on disk survives and no process outlives a request, so
everything that must last lives in the state folder (remitwatch.state), normally a private folder in the practice's
own Dropbox.

    store.sqlite.gz      the whole store, compact, gzip. Loaded into memory for one cycle and saved back.
    serving.sqlite.gz    what the dashboard reads: findings, rates, alerts and the claim detail behind each finding.
    actions/*.json       the journal: each status change or acknowledgement made in the dashboard, one small file.
    packets/<finding>/   appeal packets (metadata and PDF).
    status.json          the last cycle, for the dashboard.     cursor.json   the Dropbox position, for the webhook.
    lock.json            held by the cycle that is running.

One cycle (run_cloud_cycle): take the lock, load the store, fold the journal into it, pull new files from the data
folder, ingest, detect, raise alerts, notify, build the dashboard's copy, save both, release the lock. A cycle has a
time budget. When it cannot finish, it saves what it has and the caller starts a fresh invocation to continue, so a
first load of tens of thousands of files completes across several short runs.

The dashboard never waits on a cycle. Each instance keeps a copy of serving.sqlite.gz in its scratch space, checks for
a newer one every 45 seconds, and lays the journal over it so a status change shows at once.

Memory is the limit that matters: the whole store is held in memory while a cycle runs (about 0.5 GB for five years of
remittances) and detection needs about as much again. Each cycle records its peak in status.json.
"""
from __future__ import annotations

import gc
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
from . import runtime
from .state import Conflict, state_from_env

RAW = "store.sqlite.gz"
SERVING = "serving.sqlite.gz"
STATUS = "status.json"
LOCK = "lock.json"
CURSOR = "cursor.json"
ACTIONS = "actions"
PACKETS = "packets"
NEEDED_MEMORY_MB = 1600          # measured peak of a detection run on five years of remittances
MIN_MEMORY_MB = 2000             # Vercel's standard function size is 2 GB
DEFER_DETECT_AFTER_FILES = int(os.environ.get("REMITWATCH_DEFER_DETECT_FILES", "3000"))
REFRESH_SECONDS = 45            # how often a dashboard instance looks for a newer serving copy
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
    """The most memory this process has held so far, in MB."""
    try:
        import resource
        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
    except Exception:
        return None


def _age_seconds(iso: str | None) -> float:
    if not iso:
        return 1e9
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(iso.replace("Z", "+00:00"))).total_seconds()
    except ValueError:
        return 1e9


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

def acquire(state) -> str | None:
    """Take the cycle lock; None when another cycle holds it. A lock older than any run can be was left by a run that was
    killed and is taken over, but only if nobody else took it first."""
    token = secrets.token_hex(8)
    body = json.dumps({"token": token, "started": _now()}).encode()
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
                state.put(LOCK, body, mode="update", rev=m["rev"])
                return token
            except Conflict:
                return None
    return None


def release(state, token: str) -> None:
    try:
        raw = state.get(LOCK)
        if raw and json.loads(raw).get("token") == token:
            state.delete(LOCK)
    except Exception:
        pass        # a lock that cannot be released goes stale and is taken over


def lock_state(state) -> tuple[dict | None, dict | None]:
    """(running, stalled): a cycle in progress, or a lock left behind by one that died without finishing."""
    m = state.meta(LOCK)
    if not m:
        return None, None
    info = {"since": m.get("modified")}
    return (info, None) if _age_seconds(m.get("modified")) < lock_stale_seconds() else (None, info)


def running(state) -> dict | None:
    return lock_state(state)[0]


# --------------------------------------------------------------------------- the store, in memory

def load_raw(state):
    from .store import Store
    chunks = state.get_chunks(RAW)
    if chunks is None:
        # Start from a serialized image even when it is empty. SQLite keeps such a database in one block of memory and
        # can hand that block out when the store is saved; a plain in-memory database has to be copied twice instead.
        blank = Store(":memory:", compact=True)
        image = blank.to_bytes()
        blank.close()
        return Store.from_bytes(image), False
    data = gunzip_chunks(chunks)
    store = Store.from_bytes(data)
    del data
    gc.collect()
    return store, True


def save_raw(state, store) -> dict:
    data = store.to_bytes()
    try:
        return state.put_chunks(RAW, gzip_chunks(data))
    finally:
        del data
        gc.collect()


# --------------------------------------------------------------------------- the journal

def record_action(state, action: dict) -> str:
    action = dict(action, at=action.get("at") or _now())
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


def apply_actions(conn: sqlite3.Connection, entries: list[tuple[str, dict]]) -> int:
    n = 0
    for _name, a in entries:
        try:
            if a.get("kind") == "bulk_status":
                for fid in a.get("ids") or []:
                    actions_mod.apply_status(conn, fid, a["status"], a.get("notes"), None, a.get("actor") or "", a.get("at"))
            else:
                actions_mod.apply(conn, a)
            n += 1
        except Exception:
            continue        # one malformed entry must not block the rest
    conn.commit()
    return n


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


def _ingest_batch(store, items: list[tuple], log) -> dict:
    """Write a batch of downloaded files to scratch space, hand them to the loaders, and clean up. Each item is
    (name, bytes) or (name, bytes, file time): the loaders keep the newest copy when a payer sends a payment twice, and
    newest must mean the file's own date, not the order this run happened to read it in."""
    from .dropbox_sync import ingest_new_files
    d = os.path.join(runtime.tmp_dir(), "batch")
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d, exist_ok=True)
    paths = []
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
    try:
        return ingest_new_files(store, paths, log=log)
    finally:
        shutil.rmtree(d, ignore_errors=True)


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
            _add(out, _ingest_batch(store, batch, log))
            conn.executemany("INSERT OR REPLACE INTO dropbox_seen (id, content_hash, path, at) VALUES (?,?,?,?)", rows)
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


def sync_and_ingest(store, client, state, deadline: float, full: bool = False, log=print) -> dict:
    """Pull what is new in the data folder and ingest it, stopping at the deadline. The Dropbox position (cursor) is only
    advanced when everything it listed was handled, so a run that stops early is simply repeated: files already taken
    are recognised by id and content hash and skipped without a download."""
    from .dropbox_sync import wanted
    conn = store.conn
    root = os.environ.get("DROPBOX_ROOT_PATH", "/Trizetto Data")
    out: dict = {"listed": 0, "wanted": 0, "downloaded": 0, "remits": 0, "ema": 0, "claims_837": 0, "errors": 0, "remaining": 0}
    cursor = None if full else saved_cursor(state, root)
    out["mode"] = "delta" if cursor else "full"
    if not cursor:
        ingest_zip(store, client, root, deadline, out, log)
        if out["remaining"]:
            return out
    try:
        entries = list(client.list_continue(cursor) if cursor else client.list_folder(root, recursive=True))
    except RuntimeError as exc:
        if cursor and "reset" in str(exc).lower():      # Dropbox dropped the position: list the folder afresh
            return sync_and_ingest(store, client, state, deadline, full=True, log=log)
        raise
    new_cursor = getattr(client, "last_cursor", None)
    seen_id = {r[0]: r[1] for r in conn.execute("SELECT id, content_hash FROM dropbox_seen")}
    seen_hash = {h for h in seen_id.values() if h}
    prefix = _state_prefix(state)
    todo, mark = [], []
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
        items, rows = [], []
        for e, data in got:
            if isinstance(data, Exception):
                out["errors"] += 1
                log(f"  download failed {e.get('name')}: {data}")
                continue
            items.append((e.get("name") or "file", data, _epoch(e.get("client_modified") or e.get("server_modified"))))
            rows.append((e.get("id") or e.get("path_lower"), e.get("content_hash"), e.get("path_display"), _now()))
        out["downloaded"] += len(items)
        if items:
            _add(out, _ingest_batch(store, items, log))
        conn.executemany("INSERT OR REPLACE INTO dropbox_seen (id, content_hash, path, at) VALUES (?,?,?,?)", rows)
        conn.commit()
    # the position moves on only when everything listed was handled, and only when there was something to move past
    if not out["remaining"] and not out["errors"] and new_cursor and (out["listed"] or not cursor):
        out["cursor"] = new_cursor
    return out


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
    except Exception:
        return True
    prefix = _state_prefix(state)
    for e in res.get("entries", []):
        if wanted(e) and not (prefix and (e.get("path_lower") or "").startswith(prefix)):
            return True
    return bool(res.get("has_more"))


# --------------------------------------------------------------------------- the dashboard's copy

DETAIL = "claim_key IN (SELECT claim_key FROM main.findings WHERE claim_key IS NOT NULL)"
SERVING_TABLES: list[tuple[str, str | None, str | None]] = [
    # table, column whitelist (None = every shared column), filter
    ("findings", None, None), ("payer_rates", None, None), ("fee_table", None, None), ("provider_tiers", None, None),
    ("providers", None, None), ("exposure", None, None), ("detection_runs", None, None), ("alerts", None, None),
    ("finding_events", None, None), ("progress_snapshots", None, None), ("plb", None, None),
    ("meta", None, "key NOT LIKE 'sync:cursor%' AND key NOT LIKE 'cache:%'"),
    ("files", "sha256,kind,ingested_at", None),
    ("transactions", "transaction_key,source_name,payer_name,payer_id,payer_contact_phone,payer_contact_url,payment_method,"
                     "payment_amount_cents,payment_date,trace_number,production_date,payee_npi,superseded", None),
    ("claims", None, f"{DETAIL} OR patient_control_number IN (SELECT patient_control_number FROM main.findings WHERE patient_control_number IS NOT NULL)"),
    ("lines", None, DETAIL), ("adjustments", None, DETAIL), ("remarks", None, DETAIL),
    ("ema_rows", None, "claim_id IN (SELECT patient_control_number FROM main.findings WHERE patient_control_number IS NOT NULL)"),
    ("line_links", None, "line_key IN (SELECT line_key FROM main.findings WHERE line_key IS NOT NULL)"),
    ("line_handling", None, "line_key IN (SELECT line_key FROM main.findings WHERE line_key IS NOT NULL)"),
]


def empty_serving(path: str) -> None:
    """A serving database with every table and no rows, so the dashboard works (and shows nothing) before the first load."""
    from .alerts import ALERTS_DDL
    from .detect import ensure_schema
    from .link import DDL as LINK_DDL
    from .store import connect
    if os.path.exists(path):
        os.remove(path)
    c = connect(path)
    c.execute("PRAGMA journal_mode=DELETE")
    ensure_schema(c)
    c.executescript(ALERTS_DDL + LINK_DDL)
    c.commit()
    c.close()


def meta_summary(conn: sqlite3.Connection) -> dict:
    """The figures the dashboard header shows, computed on the full store (the serving copy holds only part of it)."""
    conn.row_factory = sqlite3.Row
    one = lambda sql: dict(conn.execute(sql).fetchone())      # noqa: E731
    return {
        "files": one("SELECT COUNT(*) n, SUM(kind='835') n835, SUM(kind LIKE 'ema:%') nema, MAX(ingested_at) last_ingest FROM files"),
        "remits": one("SELECT COUNT(*) n, MIN(payment_date) first_payment, MAX(payment_date) last_payment, SUM(payment_amount_cents) total_cents FROM transactions WHERE superseded=0"),
        "lines": one("SELECT COUNT(*) n, MIN(service_date) first_dos, MAX(service_date) last_dos FROM lines"),
        "ema": [dict(r) for r in conn.execute("SELECT family, COUNT(*) n, MIN(service_date) first_dos, MAX(service_date) last_dos FROM ema_rows GROUP BY family")],
    }


def build_serving(conn: sqlite3.Connection, path: str) -> int:
    """Copy what the dashboard needs out of the full store into a small database file. Returns its size in bytes."""
    empty_serving(path)
    summary = meta_summary(conn)
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.execute("ATTACH DATABASE ? AS s", (path,))
    try:
        for table, cols, where in SERVING_TABLES:
            if table not in have:
                continue
            src = [r[1] for r in conn.execute(f"PRAGMA main.table_info({table})")]
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
    with open(path, "rb") as fh:
        data = fh.read()
    os.remove(path)
    blob = b"".join(gzip_chunks(data, level=6))
    del data
    meta = state.put(SERVING, blob)
    return {"bytes": size, "gz_bytes": len(blob), "rev": (meta or {}).get("rev")}


class ServingCopy:
    """One per process: the dashboard's local copy of the serving database, kept fresh, with the journal laid over it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = None
        self._state_key = None
        self.rev = None
        self.checked = 0.0
        self.journal_checked = 0.0
        self.applied: set[str] = set()
        self.error: str | None = None
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
            for f in os.listdir(d):         # left by an earlier process on this instance
                if f.startswith("serving-") and not f.startswith(f"serving-{os.getpid()}-"):
                    try:
                        os.remove(os.path.join(d, f))
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

    def state(self):
        key = tuple(os.environ.get(k) for k in ("REMITWATCH_STATE_DIR", "DROPBOX_REFRESH_TOKEN", "REMITWATCH_STATE_PATH"))
        if self._state is None or key != self._state_key:
            self._state, self._state_key = state_from_env(), key
            self.rev, self.checked, self.journal_checked, self.applied = None, 0.0, 0.0, set()
            self._status = (0.0, None)
        return self._state

    def ensure(self, force: bool = False) -> str:
        with self._lock:
            st = self.state()
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
                            apply_actions(c, entries)
                        finally:
                            c.close()
                        self.applied.update(n for n, _ in entries)
            except Exception as exc:
                self.error = f"{type(exc).__name__}: {str(exc)[:200]}"
                if not (self._path and os.path.exists(self._path)):     # storage unreachable: an empty dashboard, not an error page
                    self._empty()
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
        action = dict(action, at=action.get("at") or _now())
        name = record_action(st, action)
        self.ensure()
        with self._lock:
            if self._path and os.path.exists(self._path):
                c = sqlite3.connect(self._path, timeout=30)
                try:
                    apply_actions(c, [(name, action)])
                finally:
                    c.close()
                self.applied.add(name)

    def status(self) -> dict:
        ts, cached = self._status
        if time.time() - ts < 10 and cached is not None:
            return cached
        st = self.state()
        out: dict = {"configured": st is not None}
        if st is not None:
            try:
                raw = st.get(STATUS)
                out["last_cycle"] = json.loads(raw) if raw else None
                out["running"], out["stalled"] = lock_state(st)
                out["has_data"] = st.meta(SERVING) is not None
            except Exception as exc:
                out["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
            if self.error and "error" not in out:
                out["error"] = self.error
        self._status = (time.time(), out)
        return out


SERVING_COPY = ServingCopy()


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

def write_status(state, out: dict) -> None:
    try:
        state.put(STATUS, json.dumps(out, default=str).encode())
    except Exception:
        pass


def run_cloud_cycle(state, client=None, *, budget_s: int | None = None, trigger: str = "manual", full: bool = False,
                    force_detect: bool = False, email: bool = True, delay_s: int = 0, log=print) -> dict:
    """One cycle against the state folder. Returns a summary; `more` is true when a fresh invocation should continue.
    `delay_s` is a wait taken after the lock, used when a notification arrives with the first file of a batch."""
    from .alerts import ALERTS_DDL, notify, run_alerts
    from .detect import Detector, ensure_schema

    budget = budget_s or runtime.max_seconds()
    out: dict = {"started": _now(), "trigger": trigger, "mode": "serverless", "budget_seconds": budget}
    token = acquire(state)
    if not token:
        return {"skipped": "another cycle is running"}
    store = None
    t0 = time.time()
    try:
        if delay_s:
            time.sleep(delay_s)
            t0 = time.time()
        store, existed = load_raw(state)
        conn = store.conn
        ensure_schema(conn)
        conn.executescript(ALERTS_DDL + CLOUD_DDL)
        out["load_seconds"] = round(time.time() - t0, 1)
        mem = {"load": peak_mb()}
        out["memory_mb"] = mem

        journal = read_actions(state)
        folded = apply_actions(conn, journal) if journal else 0
        out["actions_folded"] = folded

        ingest: dict = {"skipped": "Dropbox credentials are not configured"}
        if client is not None:
            # leave room for detection and for saving: ingestion gets at most half of the budget
            ingest = sync_and_ingest(store, client, state, deadline=t0 + budget * 0.5, full=full, log=log)
        out["ingest"] = {k: v for k, v in ingest.items() if k not in ("cursor", "remit_stats")}
        n_ingested = ingest.get("remits", 0) + ingest.get("ema", 0) + ingest.get("claims_837", 0)
        ingested = n_ingested > 0
        remaining = (ingest.get("remaining") or 0) > 0
        mem["ingest"] = peak_mb()

        reported = False
        pending = conn.execute("SELECT 1 FROM meta WHERE key='cloud:detect_pending'").fetchone() is not None
        has_run = conn.execute("SELECT 1 FROM detection_runs LIMIT 1").fetchone() is not None
        has_lines = conn.execute("SELECT 1 FROM lines LIMIT 1").fetchone() is not None
        need_detect = has_lines and (ingested or pending or force_detect or not has_run)
        ran_detect = False
        if need_detect and not remaining:
            last = conn.execute("SELECT value FROM meta WHERE key='cloud:detect_seconds'").fetchone()
            estimate = float(last[0]) if last else 60.0
            left = budget - (time.time() - t0)
            # Hand detection to a fresh invocation when this one may not have the time left for it, or has just read a
            # large batch: a fresh process starts with clean memory, and detection is the step that needs the most.
            if ingested and (left < estimate * 1.5 + 45 or n_ingested >= DEFER_DETECT_AFTER_FILES):
                conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('cloud:detect_pending', '1')")
                out["deferred"] = "detection"
            else:
                t1 = time.time()
                det = Detector(store)
                summary = det.run()
                del det
                gc.collect()
                conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('cloud:detect_seconds', ?)", (str(round(time.time() - t1, 1)),))
                conn.execute("DELETE FROM meta WHERE key='cloud:detect_pending'")
                out["detect"] = {"findings": sum(summary.values()), "seconds": round(time.time() - t1, 1)}
                ran_detect = True
                mem["detect"] = peak_mb()
        elif need_detect:
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('cloud:detect_pending', '1')")
        conn.commit()

        if not remaining and "deferred" not in out and has_lines:
            out["alerts"] = run_alerts(conn)
            out["notified"] = notify(conn, log=log) if email else {"sent": 0, "skipped": True}
            every = int(os.environ.get("REMITWATCH_REPORT_EVERY_DAYS", "7"))
            last_report = conn.execute("SELECT value FROM meta WHERE key='pipeline:last_report'").fetchone()
            due = not last_report or (datetime.now(timezone.utc) - datetime.fromisoformat(last_report[0])) >= timedelta(days=every)
            if due and conn.execute("SELECT 1 FROM detection_runs LIMIT 1").fetchone():
                reported = True
                from .report import build_report, email_report
                text = build_report(store, ingest_stats=ingest if "remits" in ingest else None)
                try:
                    state.put(f"reports/remitwatch-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}.md", build_report(store, detail=True).encode())
                    out["report_emailed"] = email_report(text) if email else False
                except Exception as exc:
                    out["report_error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
                conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('pipeline:last_report', ?)", (_now(),))
                conn.commit()

        # Save only what changed. The dashboard's copy: whenever what it shows could differ. The full store: whenever its
        # contents differ. An idle run (nothing new in Dropbox, nothing recorded by people) uploads neither.
        alerts_changed = bool((out.get("alerts") or {}).get("new") or (out.get("alerts") or {}).get("cleared"))
        progressed = ingested or (ingest.get("from_archive") or 0) > 0
        if ran_detect or folded or alerts_changed or (progressed and not existed):
            out["serving"] = save_serving(state, conn)
        if ran_detect or folded or alerts_changed or progressed or reported or "deferred" in out:
            t2 = time.time()
            meta = save_raw(state, store)
            out["store"] = {"gz_bytes": (meta or {}).get("size"), "save_seconds": round(time.time() - t2, 1)}
        if ingest.get("cursor"):        # after the store, so the position never runs ahead of what was saved
            state.put(CURSOR, json.dumps({"cursor": ingest["cursor"], "root": os.environ.get("DROPBOX_ROOT_PATH", "/Trizetto Data"), "at": _now()}).encode())
        for name, _a in journal:
            state.delete(name)
        out["more"] = bool(remaining or "deferred" in out)
        out["progress"] = bool(progressed or ran_detect)
        out["changed"] = bool("serving" in out or "store" in out)
        try:
            one = lambda sql: conn.execute(sql).fetchone()[0]       # noqa: E731
            out["totals"] = {"remit_files": one("SELECT COUNT(*) FROM files WHERE kind='835'"),
                             "findings": one("SELECT COUNT(*) FROM findings WHERE COALESCE(stale,0)=0") if has_run or ran_detect else 0,
                             "newest_payment": one("SELECT MAX(payment_date) FROM transactions WHERE superseded=0")}
        except sqlite3.Error:
            pass
        out["finished"] = _now()
        out["seconds"] = round(time.time() - t0, 1)
        mem["peak"] = peak_mb()
        mem["limit"] = runtime.memory_mb()
        return out
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
        out["finished"] = _now()
        out["seconds"] = round(time.time() - t0, 1)
        raise
    finally:
        try:
            if store is not None:
                store.close()
        except Exception:
            pass
        release(state, token)
        try:        # every run writes its status, so the dashboard can say when Dropbox was last checked
            if out.get("changed"):
                out["last_change"] = out.get("finished")
            else:
                prev = json.loads(state.get(STATUS) or b"{}")
                out["last_change"] = prev.get("last_change")
                out.setdefault("totals", prev.get("totals"))
        except Exception:
            pass
        write_status(state, out)


def trigger_cycle(base_url: str | None, trigger: str = "webhook", delay: int = 0, **params: Any) -> bool:
    """Start a cycle in a fresh invocation and return without waiting for it. The request is sent and the reply abandoned;
    the platform runs the invocation to its end regardless."""
    import requests
    secret = os.environ.get("CRON_SECRET")
    if not secret or not base_url:
        return False
    headers = {"Authorization": f"Bearer {secret.strip()}"}
    bypass = os.environ.get("VERCEL_AUTOMATION_BYPASS_SECRET")
    if bypass:      # only present when the project protects its production address as well
        headers["x-vercel-protection-bypass"] = bypass
    try:
        r = requests.post(f"{base_url.rstrip('/')}/api/cron/cycle", params=dict(params, trigger=trigger, delay=delay),
                          headers=headers, timeout=(5, 1.5), allow_redirects=False)
    except requests.exceptions.ReadTimeout:
        return True         # the normal case: the run is under way and will outlast this call
    except Exception:
        return False
    return r.status_code < 300


# --------------------------------------------------------------------------- setup checks

def setup_status(base_url: str | None = None, probe: bool = True) -> dict:
    """Every setting the deployment needs, checked live, in the order the setup sheet sets them."""
    from .dropbox_sync import DropboxClient
    env = os.environ.get
    checks: list[dict] = []

    def add(key: str, label: str, ok: bool | None, detail: str = "", fix: str = "") -> None:
        checks.append({"key": key, "label": label, "ok": ok, "detail": detail, "fix": "" if ok else fix})

    add("password", "Dashboard password (REMITWATCH_PASSWORD)", bool(env("REMITWATCH_PASSWORD")),
        "set" if env("REMITWATCH_PASSWORD") else "not set", "Add REMITWATCH_PASSWORD in Vercel, then redeploy.")
    cron = env("CRON_SECRET") or ""
    cron_ok = bool(cron) and cron == cron.strip() and "\n" not in cron and len(cron) >= 16
    add("cron_secret", "Scheduler secret (CRON_SECRET)", cron_ok,
        "set" if cron_ok else ("not set" if not cron else "set, but it is shorter than 16 characters or has a space or line break at an end"),
        "Add CRON_SECRET in Vercel (a long random value with no spaces), then redeploy. Without it the site cannot start its own updates.")
    key, secret, token = env("DROPBOX_APP_KEY"), env("DROPBOX_APP_SECRET"), env("DROPBOX_REFRESH_TOKEN")
    add("dropbox_app", "Dropbox app key and secret", bool(key and secret), "set" if key and secret else "not set",
        "Add DROPBOX_APP_KEY and DROPBOX_APP_SECRET in Vercel, then redeploy.")
    out: dict = {"checks": checks, "mode": "serverless" if runtime.serverless() else "server"}
    if key:
        out["authorize_url"] = DropboxClient.authorize_url(key)
    add("dropbox_token", "Dropbox connection (DROPBOX_REFRESH_TOKEN)", bool(token) and None, "not set" if not token else "",
        "Use the Connect Dropbox step on this page, add the value it shows as DROPBOX_REFRESH_TOKEN in Vercel, then redeploy.")
    root = env("DROPBOX_ROOT_PATH", "/Trizetto Data")
    if key and secret and token and probe:
        client = DropboxClient()
        try:
            acct = client.current_account()
            checks[-1].update(ok=True, detail=f"connected as {(acct.get('name') or {}).get('display_name') or 'the account that approved the app'}", fix="")
            info = acct.get("root_info") or {}
            team_root = info.get("root_namespace_id") if info.get("root_namespace_id") != info.get("home_namespace_id") else None
        except Exception as exc:
            checks[-1].update(ok=False, detail=str(exc)[:200], fix="The token was refused. Repeat the Connect Dropbox step and replace DROPBOX_REFRESH_TOKEN.")
            team_root = None
        if checks[-1]["ok"]:
            try:
                res = client._rpc("files/list_folder", {"path": root, "recursive": False, "limit": 200})
                n = len(res.get("entries", []))
                add("data_folder", f"Data folder {root}", True, f"found, {n}{'+' if res.get('has_more') else ''} items at the top level")
            except Exception as exc:
                hint = (f" This account is in a team space: add DROPBOX_PATH_ROOT_NS={team_root} in Vercel and redeploy." if team_root and not env("DROPBOX_PATH_ROOT_NS") else "")
                add("data_folder", f"Data folder {root}", False, str(exc)[:160],
                    "The folder was not found. Check DROPBOX_ROOT_PATH matches the folder name exactly, starting with a slash." + hint)
            st = state_from_env()
            try:
                st.put("setup-check.txt", b"ok")
                st.delete("setup-check.txt")
                add("state_folder", f"Private storage ({st.describe()})", True, "the app can write and delete here")
            except Exception as exc:
                add("state_folder", "Private storage", False, str(exc)[:160],
                    "The app cannot write to Dropbox. In the Dropbox Developer Console open the app, Permissions tab, tick files.content.write, "
                    "click Save changes, then repeat the Connect Dropbox step: a token only carries the permissions ticked when it was made.")
    else:
        add("data_folder", f"Data folder {root}", None, "checked once Dropbox is connected")
        add("state_folder", "Private storage", None, "checked once Dropbox is connected")
    rt = runtime.describe()
    add("runtime", "Runtime", rt["in_memory_store"], f"Python {rt['python']}, SQLite {rt['sqlite']}",
        "This Python build cannot hold the store in memory, which the site needs on a serverless host. Tell Dr. Jairath.")
    mem, secs = runtime.memory_mb(), runtime.max_seconds()
    add("limits", "Function memory and time", None if mem is None else mem >= MIN_MEMORY_MB,
        f"memory {mem or 'not reported by the host'} MB, time allowed for one run {secs} s",
        f"A run needs about {NEEDED_MEMORY_MB:,} MB at its peak. In Vercel open Settings, Functions, Function CPU, choose Performance (needs the Pro plan), then redeploy.")
    add("email", "Email for alerts and the weekly summary (optional)", bool(env("SMTP_HOST") and env("REPORT_TO")) or None,
        "configured" if env("SMTP_HOST") and env("REPORT_TO") else "not configured")
    add("note_review", "Visit-note review (optional)", bool(env("ANTHROPIC_API_KEY")) or None,
        "on" if env("ANTHROPIC_API_KEY") else "off: packets are built from the template")
    if base_url:
        out["webhook_url"] = f"{base_url.rstrip('/')}/api/dropbox/webhook"
    st = state_from_env() if (key and secret and token) or env("REMITWATCH_STATE_DIR") else None
    if st is not None and probe:
        try:
            raw = st.get(STATUS)
            out["last_cycle"] = json.loads(raw) if raw else None
            out["has_data"] = st.meta(SERVING) is not None
            out["running"], out["stalled"] = lock_state(st)
        except Exception as exc:
            out["storage_error"] = str(exc)[:160]
    required = [c for c in checks if c["key"] in ("password", "cron_secret", "dropbox_app", "dropbox_token", "data_folder", "state_folder")]
    out["ready"] = all(c["ok"] for c in required)
    by_key = {c["key"]: c for c in checks}
    # the Connect Dropbox step is needed when there is no token yet, or the one in place was refused or cannot write
    out["connect_needed"] = bool(key and secret) and (not token or by_key["dropbox_token"]["ok"] is False
                                                      or by_key.get("state_folder", {}).get("ok") is False)
    return out
