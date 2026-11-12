"""
Where the code is running.

Server mode: a host with a disk and a long-lived process (a VM, docker compose). The store is a SQLite file and the
scheduler in pipeline.py keeps it current.

Serverless mode: a platform such as Vercel, where nothing written to disk survives and no process outlives a request.
The store is held in memory for the length of one cycle and saved to a private Dropbox folder (remitwatch.cloud).
"""
from __future__ import annotations

import os
import tempfile


def serverless() -> bool:
    mode = os.environ.get("REMITWATCH_MODE", "").lower()
    if mode in ("server", "serverless"):
        return mode == "serverless"
    return bool(os.environ.get("VERCEL"))


def max_seconds() -> int:
    """How long one cycle may run before it must save and hand over to a fresh invocation. Set REMITWATCH_MAX_SECONDS a
    minute below the function's maximum duration."""
    try:
        return max(30, int(os.environ.get("REMITWATCH_MAX_SECONDS", "240")))
    except ValueError:
        return 240


def tmp_dir() -> str:
    d = os.path.join(os.environ.get("REMITWATCH_TMP") or tempfile.gettempdir(), "remitwatch")
    os.makedirs(d, exist_ok=True)
    return d


def memory_mb() -> int | None:
    """Memory available to this process in MB: the function size the platform reports, else the container's limit, else
    the machine's total."""
    for k in ("AWS_LAMBDA_FUNCTION_MEMORY_SIZE", "VERCEL_FUNCTION_MEMORY"):
        v = os.environ.get(k)
        if v and v.isdigit():
            return int(v)
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            with open(path) as fh:
                v = fh.read().strip()
            if v.isdigit() and int(v) < (1 << 44):
                return int(v) >> 20
        except OSError:
            pass
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) >> 10
    except (OSError, ValueError):
        pass
    return None


def describe() -> dict:
    """What the running platform offers, for the health check and the setup page."""
    import platform
    import sqlite3
    return {"python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
            "in_memory_store": hasattr(sqlite3.Connection, "deserialize") and hasattr(sqlite3.Connection, "serialize"),
            "memory_mb": memory_mb(), "max_seconds": max_seconds()}


_SELFTEST: dict | None = None


def selftest() -> dict:
    """Once per process, prove on this very host what the serverless mode depends on: the store can be held in memory,
    grown, saved, compressed and reopened, and the dashboard's copy can be built in scratch space and queried. It uses
    an empty store and takes a fraction of a second."""
    global _SELFTEST
    if _SELFTEST is not None:
        return _SELFTEST
    import sqlite3
    import time
    t = time.time()
    try:
        from . import cloud
        from .alerts import ALERTS_DDL
        from .detect import ensure_schema
        from .store import Store
        blank = Store(":memory:", compact=True)
        image = blank.to_bytes()
        blank.close()
        store = Store.from_bytes(image)
        ensure_schema(store.conn)
        store.conn.executescript(ALERTS_DDL + cloud.CLOUD_DDL)
        store.conn.executemany("INSERT INTO dropbox_seen (id, content_hash, path, at) VALUES (?,?,?,?)",
                               [(f"id:{i}", "h" * 64, "/x/" + "y" * 200, "2026-01-01") for i in range(4000)])
        store.conn.commit()
        data = store.to_bytes()                                   # grown well past the image it started from
        again = Store.from_bytes(cloud.gunzip_chunks([b"".join(cloud.gzip_chunks(data))]))
        kept = again.conn.execute("SELECT COUNT(*) FROM dropbox_seen").fetchone()[0]
        path = os.path.join(tmp_dir(), f"selftest-{os.getpid()}.sqlite")
        size = cloud.build_serving(again.conn, path)
        c = sqlite3.connect(path)
        c.execute("""SELECT substr(COALESCE(service_date, payment_date),1,7) m, category, COUNT(*) n, SUM(status='open') o,
                            COALESCE(SUM(CASE WHEN appeal_deadline >= date('now') AND appeal_deadline < date('now','+45 days')
                                              THEN shortfall_cents END),0) due
                     FROM findings WHERE COALESCE(stale,0)=0 GROUP BY 1,2 ORDER BY 5 DESC NULLS LAST LIMIT 5""").fetchall()
        c.close()
        os.remove(path)
        store.close()
        again.close()
        ok = kept == 4000 and len(data) > len(image) and size > 0
        _SELFTEST = {"ok": ok, "ms": int((time.time() - t) * 1000)}
    except Exception as exc:
        _SELFTEST = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    return _SELFTEST
