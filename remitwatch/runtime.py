"""
Where the code is running.

Server mode: a host with a disk and a long-lived process (a VM, docker compose). The store is a SQLite file and the
scheduler in pipeline.py keeps it current.

Serverless mode: a platform such as Vercel, where nothing written to disk survives and no process outlives a request.
The store is held in memory for the length of one cycle and saved to a private Dropbox folder (remitwatch.cloud).
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone, tzinfo

# The practice's own day. A filing window stays open until midnight where the practice is (Kentucky and Indiana: US
# Eastern), so every "today" the site compares a deadline, a follow-up or an expiry with is that zone's date, never the
# host's (UTC on both hosts, where the date turns over at 7 PM Eastern).
TZ_SETTING = "REMITWATCH_TZ"
DEFAULT_TZ = "America/New_York"
_TZ_WARNED: set = set()


def _utc_now() -> datetime:
    """The clock, read in one place so a test can move it."""
    return datetime.now(timezone.utc)


class _UsEastern(tzinfo):
    """US Eastern time by the federal rule since 2007 (daylight time from 2 AM on the second Sunday of March to 2 AM on the
    first Sunday of November), for a host with no time zone data at all. requirements.txt installs tzdata, so this is
    only a last resort."""

    @staticmethod
    def _summer(year: int) -> tuple[datetime, datetime]:
        march, november = datetime(year, 3, 8), datetime(year, 11, 1)
        start = march + timedelta(days=(6 - march.weekday()) % 7, hours=7)          # 2 AM EST is 07:00 UTC
        end = november + timedelta(days=(6 - november.weekday()) % 7, hours=6)      # 2 AM EDT is 06:00 UTC
        return start, end

    def fromutc(self, dt: datetime) -> datetime:
        naive = dt.replace(tzinfo=None)
        start, end = self._summer(naive.year)
        return (naive + timedelta(hours=-4 if start <= naive < end else -5)).replace(tzinfo=self)

    def utcoffset(self, dt: datetime | None) -> timedelta:
        if dt is None:
            return timedelta(hours=-5)
        start, end = self._summer(dt.year)
        naive = dt.replace(tzinfo=None) + timedelta(hours=5)       # as if standard time; the repeated hour reads as standard
        return timedelta(hours=-4 if start <= naive < end else -5)

    def dst(self, dt: datetime | None) -> timedelta:
        return self.utcoffset(dt) - timedelta(hours=-5)

    def tzname(self, dt: datetime | None) -> str:
        return "EDT" if self.dst(dt) else "EST"


def _warn_tz(name: str, why: str) -> None:
    if name in _TZ_WARNED:
        return
    _TZ_WARNED.add(name)
    print(f"RemitWatch WARNING: {TZ_SETTING} {why}; {DEFAULT_TZ} is used for the practice's day.", file=sys.stderr, flush=True)


def practice_zone() -> tzinfo:
    """The practice's time zone: REMITWATCH_TZ, an IANA name such as America/Chicago (default America/New_York). A name
    this host does not know falls back to the default, with one warning in the log."""
    from zoneinfo import ZoneInfo
    name = (os.environ.get(TZ_SETTING) or "").strip() or DEFAULT_TZ
    try:
        return ZoneInfo(name)
    except Exception:          # unknown name, a path, or no time zone data on this host: never stop a page for it
        if name != DEFAULT_TZ:
            _warn_tz(name, f"is {name[:60]!r}, which is not a time zone this host knows")
    try:
        return ZoneInfo(DEFAULT_TZ)
    except Exception:
        _warn_tz("", "could not be read from this host's time zone data")
        return _UsEastern()


def practice_today(offset: int = 0) -> str:
    """Today where the practice is, plus `offset` days, as YYYY-MM-DD: the day every list, count, expiry check and follow-up
    compares with. A window whose last day this is stays open until midnight there."""
    return (_utc_now().astimezone(practice_zone()).date() + timedelta(days=offset)).isoformat()


def practice_date(stamp: str | None) -> str:
    """The practice's date of a moment the store keeps (an ISO time with its zone, as every record is written: a fax sent
    at 8:30 PM Eastern was sent that day, not the next). A plain date, or a time with no zone, is taken as written."""
    s = (stamp or "").strip()
    if len(s) <= 10:
        return s[:10]
    try:
        t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return s[:10]
    if t.tzinfo is None:
        return s[:10]
    return t.astimezone(practice_zone()).date().isoformat()


def practice_day_start(day: str) -> str:
    """The moment (UTC, ISO, as the store writes them) the practice's day `day` began: what a stored time is compared with
    to ask "before that day, where the practice is"."""
    d = date.fromisoformat(day[:10])
    local = datetime(d.year, d.month, d.day, tzinfo=practice_zone())
    # a zone's own rules give the midnight's offset; the last-resort zone does through utcoffset
    return local.astimezone(timezone.utc).isoformat(timespec="seconds")


def serverless() -> bool:
    mode = os.environ.get("REMITWATCH_MODE", "").lower()
    if mode in ("server", "serverless"):
        return mode == "serverless"
    return bool(os.environ.get("VERCEL"))


def preview() -> bool:
    """A Vercel build of some other branch. If it were given the production settings it would hold the Dropbox token
    and write the same state folder with different code, so it never runs a cycle."""
    return os.environ.get("VERCEL_ENV", "production") not in ("", "production")


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
_SELFTEST_AT = 0.0
_SELFTEST_LOCK = __import__("threading").Lock()


def selftest() -> dict:
    """Prove on this very host what the serverless mode depends on: the store can be held in memory, grown, saved,
    compressed and reopened, and the dashboard's copy can be built in scratch space and queried. It uses an empty store
    and takes a fraction of a second. A pass is remembered for the life of the process; a failure is tried again after a
    minute, so one bad moment is not held against the host for ever. One caller at a time: two requests reaching a cold
    instance together must not run it side by side."""
    global _SELFTEST, _SELFTEST_AT
    import time
    with _SELFTEST_LOCK:
        if _SELFTEST is not None and (_SELFTEST.get("ok") or time.time() - _SELFTEST_AT < 60):
            return _SELFTEST
        _SELFTEST, _SELFTEST_AT = _run_selftest(), time.time()
        return _SELFTEST


def _run_selftest() -> dict:
    import secrets
    import sqlite3
    import time
    t = time.time()
    path = os.path.join(tmp_dir(), f"selftest-{os.getpid()}-{secrets.token_hex(4)}.sqlite")
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
        size = cloud.build_serving(again.conn, path)
        c = sqlite3.connect(path)
        c.execute("""SELECT substr(COALESCE(service_date, payment_date),1,7) m, category, COUNT(*) n, SUM(status='open') o,
                            COALESCE(SUM(CASE WHEN appeal_deadline >= date('now') AND appeal_deadline < date('now','+45 days')
                                              THEN shortfall_cents END),0) due
                     FROM findings WHERE COALESCE(stale,0)=0 GROUP BY 1,2 ORDER BY 5 DESC NULLS LAST LIMIT 5""").fetchall()
        c.close()
        store.close()
        again.close()
        ok = kept == 4000 and len(data) > len(image) and size > 0
        return {"ok": ok, "ms": int((time.time() - t) * 1000)}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    finally:
        for suffix in ("", "-journal"):
            try:
                os.remove(path + suffix)
            except OSError:
                pass



def owner_password_problem(owner: str, shared: str, min_length: int = 12) -> str | None:
    """What is wrong with an owner's password, as words that follow "it", or None when it can be used or there is none."""
    if not owner:
        return None
    if any(not (0x20 <= ord(ch) <= 0x7e) for ch in owner):
        return "has a character a browser cannot send (use plain letters and digits)"
    if owner != owner.strip():
        return "has a space at one end"
    if len(owner) < min_length:
        return f"is shorter than {min_length} characters"
    if owner == shared:
        return "is the same as the dashboard password"
    return None
