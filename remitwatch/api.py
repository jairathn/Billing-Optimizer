"""
RemitWatch API and dashboard server.

    REMITWATCH_DB=data/remitwatch.sqlite REMITWATCH_PASSWORD=... uvicorn remitwatch.api:app

Every endpoint reads the SQLite store built by `remitwatch.cli`. Unless
REMITWATCH_SHOW_PHI=1 is set, the lists and the detail view say who a finding
is about with initials only and the search box does not look at names. That
setting covers the screens and the CSV export, nothing more: an appeal packet
PDF always carries the patient's name and member number, for every login.
"""
from __future__ import annotations

import csv
import io
import json
from collections import defaultdict
import os
import re
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import hashlib
import hmac
from contextlib import asynccontextmanager

from fastapi import Body, Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

import asyncio
from contextvars import ContextVar

from . import __version__, runtime
from .actions import STATUSES, apply_alert_ack, apply_status
from .codes import CARC, RARC, member_number
from .detect import DOWNCODE_SIGNALS, EM_SQL, PATTERN_SQL, ensure_schema, pattern_windows

DB_PATH = os.environ.get("REMITWATCH_DB", "data/remitwatch.sqlite")
PASSWORD = os.environ.get("REMITWATCH_PASSWORD", "")
USERNAME = os.environ.get("REMITWATCH_USER", "oda")
# An optional second password. When it is set, what each clinician is doing (the Providers tab, and the number of lines
# billed per clinician on the Who and what tab) is shown only after it has been typed into the Providers tab. The page
# sends it in a header with those calls and keeps it in memory only. The browser's own login stays the shared one, so
# nobody is signed out or switched, and closing or reloading the page locks the tab again.
OWNER_PASSWORD = os.environ.get("REMITWATCH_OWNER_PASSWORD", "")
OWNER_HEADER = "x-owner-password"
SHOW_PHI = os.environ.get("REMITWATCH_SHOW_PHI", "0") == "1"

STATIC_DIR = Path(__file__).parent / "static"
security = HTTPBasic(auto_error=False)



def ensure_server_schema(path: str) -> None:
    """The store's tables, the analysis's, and the dashboard's own (alerts, links, the provider tables, faxes, hidden
    reasons, packets): the same set the serverless copy starts with (cloud.empty_serving), created where missing."""
    from . import activity
    from .actions import ensure_fax_tables, ensure_hide_rules
    from .alerts import ALERTS_DDL
    from .link import DDL as LINK_DDL
    from .notes import ensure_packets
    from .store import connect, mark_store, store_problem
    from .uploads import ensure_schema as ensure_uploads
    why = store_problem(path)
    if why:             # a store was made here and is missing: making the tables would make a new empty store in its place
        import sys
        print(f"RemitWatch: {why}", file=sys.stderr, flush=True)
        return
    conn = connect(path)
    try:
        ensure_schema(conn)
        conn.executescript(ALERTS_DDL + LINK_DDL)
        activity.ensure_schema(conn)
        ensure_fax_tables(conn)
        ensure_hide_rules(conn)
        ensure_packets(conn)
        ensure_uploads(conn)            # files and visit notes uploaded by hand (remitwatch.uploads): a server only
        conn.commit()
    finally:
        conn.close()
    mark_store(path)                    # the marker beside it: from now on a missing store is a stop, not a new one


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Bring an older store up to the current schema and start the background scheduler when it is configured."""
    if runtime.serverless():
        yield           # nothing on disk to migrate and no process to keep: remitwatch.cloud does both jobs
        return
    # every table the dashboard reads, whether or not a store exists yet (a new server's first page must not fail
    # before its first run), then the scheduler: the daily work runs without any outside cron
    log_password_state()
    ensure_server_schema(DB_PATH)
    from .pipeline import SCHEDULER, start_scheduler_from_env
    start_scheduler_from_env(DB_PATH)
    yield
    SCHEDULER.stop()


app = FastAPI(title="RemitWatch", version=__version__, docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
# Vercel refuses answers over 4.5 MB; compressed, the largest list the dashboard asks for is a few hundred KB
app.add_middleware(GZipMiddleware, minimum_size=2000)


@app.middleware("http")
async def protective_headers(request: Request, call_next):
    """No page of this site may be shown inside another site's frame, and no answer with data may be kept by a browser
    or a proxy."""
    resp = await call_next(request)
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    if request.url.path.startswith("/api/"):
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp


class Refused(Exception):
    """A refusal the page acts on by its code (in_flight, changed, amount, note_check...): the answer carries the plain
    message as `detail`, as every other refusal does, and the code beside it."""

    def __init__(self, http_status: int, code: str, message: str, **extra):
        super().__init__(message)
        self.http_status, self.code, self.message, self.extra = http_status, code, message, extra


@app.exception_handler(Refused)
async def _refused(_request: Request, exc: Refused):
    return JSONResponse(status_code=exc.http_status, content=dict(exc.extra, detail=exc.message, code=exc.code))


RENUMBERED = "This item has a new number since your page loaded: open it again from the list."


def _heir_now(conn: sqlite3.Connection, finding_id: str) -> str | None:
    """The item that carries `finding_id`'s money now, when detection gave it a new number since a page was drawn
    (finding_heirs, followed to the newest), and that item exists; else None."""
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='finding_heirs'").fetchone():
            return None
        seen, cur = {finding_id}, finding_id
        while True:
            r = conn.execute("SELECT new_id FROM finding_heirs WHERE old_id=?", (cur,)).fetchone()
            if not r or not r[0] or r[0] in seen:
                break
            cur = r[0]
            seen.add(cur)
        if cur != finding_id and conn.execute("SELECT 1 FROM findings WHERE finding_id=?", (cur,)).fetchone():
            return cur
    except sqlite3.Error:
        return None
    return None


def _not_found(finding_id: str, conn: sqlite3.Connection | None = None):
    """No item by this id: when its money has a new number since the person's page was drawn, say so and give the new one
    (409 changed, as for any item that changed since the page loaded); otherwise 404."""
    own = conn is None
    c = conn or db()
    try:
        heir = _heir_now(c, finding_id)
    finally:
        if own:
            c.close()
    if heir:
        raise Refused(409, "changed", RENUMBERED, heir=heir)
    raise HTTPException(404, "finding not found")


@app.exception_handler(RequestValidationError)
async def _unreadable_body(request: Request, exc: RequestValidationError):
    """A body a route cannot read (not JSON, or not the object the route takes): 400 with a plain sentence, as the status
    routes answer (_body_object), on every route that reads one, appeals_send and the hide routes among them; the
    routes stay plain functions. What is wrong with the address or the query keeps FastAPI's 422. The answer never
    repeats what was sent."""
    if any((e.get("loc") or ("",))[0] == "body" for e in exc.errors()):
        return JSONResponse(status_code=400, content={"detail": "The request could not be read. Reload the page and try again."})
    return await request_validation_exception_handler(request, exc)


def require_xhr(request: Request) -> None:
    """State-changing calls must carry a header a cross-site form cannot set."""
    if request.headers.get("x-requested-with") != "remitwatch":
        raise HTTPException(status_code=400, detail="missing X-Requested-With header")


def open_allowed() -> bool:
    """With no password the dashboard is open. That is acceptable on a developer's own machine and nowhere else, so
    every site, the server as much as Vercel, refuses to serve data until REMITWATCH_PASSWORD is set. Only
    REMITWATCH_ALLOW_OPEN=1 says this is a developer's own machine (never set it on the server or on Vercel)."""
    return os.environ.get("REMITWATCH_ALLOW_OPEN") == "1"


def base_url(request: Request) -> str:
    """The public address of this site, for the calls it makes to itself and for the webhook address shown during setup.
    On Vercel that is the production domain: the per-deployment addresses sit behind Vercel's own login."""
    prod = os.environ.get("VERCEL_PROJECT_PRODUCTION_URL")
    if prod and os.environ.get("VERCEL_ENV") == "production":
        return f"https://{prod}"
    # the forwarded headers are only trustworthy behind Vercel's own proxy; anywhere else a caller could set them
    forwarded = request.headers.get("x-forwarded-host") if os.environ.get("VERCEL") else None
    host = (forwarded or request.headers.get("host") or prod or "").split(",")[0].strip()
    local = host.startswith(("localhost", "127.", "testserver"))
    proto = ((request.headers.get("x-forwarded-proto") if os.environ.get("VERCEL") else None) or ("http" if local else "https")).split(",")[0].strip()
    return f"{proto}://{host}" if host else ""


MIN_PASSWORD = 12


def password_problem() -> str | None:
    """Why the dashboard password cannot be used, or None when it can. On a deployed site the password is all that
    stands between the internet and the data, so a short one counts as none on Vercel: the site stays locked and says
    why. The server accepts a short one it already has, so that the live site never locks its own staff out after an
    update; password_warning() says what is weak about it, on /setup and in the log."""
    if not PASSWORD:
        return "not set"
    if open_allowed():
        return None             # a developer's own machine
    if not PASSWORD.isascii():  # a login cannot carry it (the browser and the sign-in check read plain characters only)
        return "has a character a browser cannot send in a login (use plain letters and digits)"
    if len(PASSWORD) < MIN_PASSWORD and runtime.serverless():
        return f"shorter than {MIN_PASSWORD} characters"
    return None


def password_warning() -> str | None:
    """What is weak about a dashboard password the site still accepts (a short one on the server), or None."""
    if PASSWORD and not password_problem() and len(PASSWORD) < MIN_PASSWORD:
        return f"shorter than {MIN_PASSWORD} characters"
    return None


def _password_fix(problem: str) -> str:
    """Where this host's dashboard password is set, in the words of the setup page."""
    if runtime.serverless():
        return ("Add REMITWATCH_PASSWORD in Vercel (Settings, Environment Variables), then redeploy." if problem == "not set" else
                f"Replace REMITWATCH_PASSWORD in Vercel with one of {MIN_PASSWORD} or more plain letters and digits (the box below offers one), then redeploy.")
    return ("Set REMITWATCH_PASSWORD on the server: run sudo bash /opt/remitwatch/deploy/aws/configure.sh (it can make one for you)." if problem == "not set" else
            f"Replace REMITWATCH_PASSWORD on the server with one of {MIN_PASSWORD} or more plain letters and digits: run sudo bash /opt/remitwatch/deploy/aws/configure.sh.")


def log_password_state() -> None:
    """Say in the server's log, at start, when the site serves nothing for want of a password, or accepts a weak one."""
    import sys
    problem, weak = password_problem(), password_warning()
    if problem and not open_allowed():
        print(f"RemitWatch: REMITWATCH_PASSWORD is {problem}, so this site serves no data until it is fixed. {_password_fix(problem)}",
              file=sys.stderr, flush=True)
    elif weak:
        print(f"RemitWatch WARNING: REMITWATCH_PASSWORD is {weak}. This server still accepts it, so nobody is locked out, "
              f"but it is easy to guess. {_password_fix(weak)}", file=sys.stderr, flush=True)


def owner_problem() -> str | None:
    """Why the owner's password cannot be used, or None when it can or when there is none. A weak one is never quietly
    ignored: the provider figures are then refused to everyone until it is fixed."""
    return runtime.owner_password_problem(OWNER_PASSWORD, PASSWORD, MIN_PASSWORD)


def shared_login(credentials: Optional[HTTPBasicCredentials]) -> bool:
    """Whether these are the dashboard's user name and password. Both halves are compared every time, so how long the
    answer takes says nothing about which half was wrong."""
    if credentials is None or not PASSWORD:
        return False
    return secrets.compare_digest(credentials.username.encode(), USERNAME.encode()) & secrets.compare_digest(credentials.password.encode(), PASSWORD.encode())


_FAILED: dict[str, list[float]] = {}        # address -> times of recent wrong logins (this instance only)
_OWNER_FAILED: dict[str, list[float]] = {}  # the same for wrong tries at the owner's password
FAIL_LIMIT, FAIL_WINDOW = 10, 600


def _caller(request: Optional[Request]) -> str:
    if request is None:
        return "?"
    # behind Vercel the first address in this header is the caller's and cannot be set by the caller
    fwd = request.headers.get("x-forwarded-for") if os.environ.get("VERCEL") else None
    return (fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "?")) or "?"


def _blocked(who: str, record: bool = False, owner: bool = False) -> bool:
    """A brake on guessing: ten wrong tries from one address in ten minutes and it is refused for the rest of them.
    Wrong logins and wrong tries at the owner's password are counted apart, so guessing at one cannot lock out the other."""
    import time
    book = _OWNER_FAILED if owner else _FAILED
    now = time.time()
    recent = [t for t in book.get(who, []) if now - t < FAIL_WINDOW]
    if record:
        recent.append(now)
    if recent:
        book[who] = recent
    else:
        book.pop(who, None)
    if len(book) > 5000:
        book.clear()
    return len(recent) >= FAIL_LIMIT + (1 if record else 0)


def require_auth(request: Request = None, credentials: Optional[HTTPBasicCredentials] = Depends(security)) -> str:
    """Basic auth with a single shared password. When no password is configured the dashboard is open only on a
    developer's own machine (REMITWATCH_ALLOW_OPEN=1); everywhere else it answers 503 and /setup says why."""
    problem = password_problem()
    if problem:
        if not PASSWORD and open_allowed():
            return "anonymous"
        raise HTTPException(status_code=503, detail=f"This site is not set up yet: REMITWATCH_PASSWORD is {problem}. Open /setup.")
    who = _caller(request)
    if _blocked(who):
        raise HTTPException(status_code=429, detail="Too many wrong logins from this address. Wait ten minutes.")
    if not shared_login(credentials):
        if credentials is not None:
            _blocked(who, record=True)
        raise HTTPException(status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": "Basic realm=RemitWatch"})
    return credentials.username


def owner_state(request: Optional[Request]) -> str:
    """How this request stands with the owner's password: 'open' (none is set, or the right one came with the request),
    'missing' (none was sent), 'wrong', 'blocked' (too many wrong tries from this address) or 'unusable' (the one
    configured is too weak to be accepted). A wrong try is counted wherever it is made, so no route can be used to
    guess faster than another."""
    if not OWNER_PASSWORD:
        return "open"
    if owner_problem():
        return "unusable"
    who = _caller(request)
    if _blocked(who, owner=True):
        return "blocked"
    sent = request.headers.get(OWNER_HEADER) if request is not None else None
    if not sent:
        return "missing"
    if secrets.compare_digest(sent.encode(), OWNER_PASSWORD.encode()):
        return "open"
    _blocked(who, record=True, owner=True)
    return "wrong"


def require_owner(request: Request = None, credentials: Optional[HTTPBasicCredentials] = Depends(security)) -> str:
    """The Providers tab compares named clinicians. With no owner password it is open to everyone who can sign in; with
    one, the request must also carry it. Refusals are 403, never 401: a 401 would make the browser ask for a login
    again and drop the one it has."""
    user = require_auth(request, credentials)
    state = owner_state(request)
    if state == "open":
        return user
    if state == "unusable":             # the reason is on /setup; said here it would only help someone guess
        raise HTTPException(status_code=503, detail="The provider figures are switched off: the owner's password cannot be used as it is set. The setup page says why.")
    if state == "blocked":
        raise HTTPException(status_code=429, detail="Too many wrong tries at the owner's password from this address. Wait ten minutes.")
    raise HTTPException(status_code=403, detail="That is not the owner's password." if state == "wrong"
                        else "These figures open with the owner's password.")


def db() -> sqlite3.Connection:
    if runtime.serverless():
        from .cloud import SERVING_COPY, StorageUnavailable
        try:
            return SERVING_COPY.connection()    # the dashboard's copy, fetched from the state folder and kept fresh
        except StorageUnavailable:
            raise HTTPException(status_code=503, detail="The site could not reach its storage in Dropbox just now. Reload the page in a minute.")
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def rows(sql: str, params: tuple = ()) -> list[dict]:
    conn = db()
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def one(sql: str, params: tuple = ()) -> dict | None:
    r = rows(sql, params)
    return r[0] if r else None


def has_table(name: str) -> bool:
    return one("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)) is not None


MONEY_CATEGORIES = ("downcoding", "bundling", "denial", "underpayment", "fees", "contract")


# --------------------------------------------------------------------------- pages
@app.get("/setup", include_in_schema=False)
def setup_page(request: Request, credentials: Optional[HTTPBasicCredentials] = Depends(security)):
    """The setup checklist. Before a usable password exists it is open and says only what is missing; after that it needs the login."""
    if not password_problem():
        require_auth(request, credentials)
    return FileResponse(STATIC_DIR / "setup.html", headers={"Cache-Control": "no-store"})


@app.get("/upload", include_in_schema=False)
def upload_page(request: Request, credentials: Optional[HTTPBasicCredentials] = Depends(security)):
    """Bringing EMA's exports, remittance files and visit notes in by hand, with the guide to running them in EMA."""
    if password_problem() and not open_allowed():
        return RedirectResponse("/setup")
    require_auth(request, credentials)
    return FileResponse(STATIC_DIR / "upload.html", headers={"Cache-Control": "no-store"})


@app.get("/", include_in_schema=False)
def index(request: Request, credentials: Optional[HTTPBasicCredentials] = Depends(security)):
    if password_problem() and not open_allowed():
        return RedirectResponse("/setup")
    require_auth(request, credentials)
    f = STATIC_DIR / "index.html"
    if f.exists():
        return FileResponse(f)
    return HTMLResponse("<h1>RemitWatch</h1><p>static/index.html missing</p>")


@app.get("/robots.txt", include_in_schema=False)
def robots():
    return PlainTextResponse("User-agent: *\nDisallow: /\n")


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# --------------------------------------------------------------------------- health and meta
@app.get("/api/health")
def health():
    """Whether the site is up, with no login. `commit` is the commit this site runs, in short, so that the two sites can
    be compared: on Vercel the one Vercel built (VERCEL_GIT_COMMIT_SHA), on the server the one its image was built from
    (REMITWATCH_COMMIT, which the automatic deploy and configure.sh give the build). None when the site cannot tell.
    Only a commit hash is ever shown here, never what else such a setting holds."""
    serverless = runtime.serverless()
    sha = (os.environ.get("VERCEL_GIT_COMMIT_SHA" if serverless else "REMITWATCH_COMMIT") or "").strip().lower()
    commit = sha[:7] if re.fullmatch(r"[0-9a-f]{7,64}", sha) else None
    if serverless:
        return {"ok": True, "mode": "serverless", "version": __version__, "commit": commit,
                "runtime": runtime.describe(), "selftest": runtime.selftest(), "time": datetime.now(timezone.utc).isoformat()}
    from .store import store_problem
    return {"ok": os.path.exists(DB_PATH) and not store_problem(DB_PATH), "mode": "server", "version": __version__, "commit": commit,
            "time": datetime.now(timezone.utc).isoformat()}


def _trizetto_here() -> bool:
    from . import trizetto
    return trizetto.configured()


_ROUTES_SEEN: dict[str, bool] = {}


def _route_here(path: str) -> bool:
    """Whether this build answers GET `path` (a route another unit's router adds, included at the end of this file):
    the page asks for it only then, so a build without it shows no error. Worked out once per path."""
    if path not in _ROUTES_SEEN:
        from starlette.routing import Match
        scope = {"type": "http", "path": path, "method": "GET", "root_path": "", "headers": [], "query_string": b""}
        _ROUTES_SEEN[path] = any(getattr(r, "matches", None) and r.matches(scope)[0] == Match.FULL for r in app.router.routes)
    return _ROUTES_SEEN[path]


@app.get("/api/meta")
def meta(_: str = Depends(require_auth)):
    out: dict[str, Any] = {"version": __version__, "show_phi": SHOW_PHI, "mode": "serverless" if runtime.serverless() else "server",
                           # whether what each clinician is doing asks for the owner's password first
                           "providers": {"locked": bool(OWNER_PASSWORD)},
                           # whether visit notes can be read from ModMed EMA (remitwatch.modmed)
                           "modmed": bool(os.environ.get("MODMED_CLIENT_ID") and (os.environ.get("MODMED_PRIVATE_KEY") or os.environ.get("MODMED_CLIENT_SECRET"))),
                           # whether appeals go out by fax (remitwatch.fax)
                           "fax": bool(os.environ.get("SRFAX_ACCESS_ID") and os.environ.get("SRFAX_ACCESS_PWD")),
                           # whether this site reads remittances straight from TriZetto (remitwatch.trizetto): a serverless
                           # host does only with TRIZETTO_SFTP_SERVERLESS=1, whatever login it is given
                           "trizetto": _trizetto_here(),
                           # whether visit notes can be uploaded by hand (remitwatch.uploads): a server only
                           "uploads": not runtime.serverless(),
                           # whether EMA's A/R and Production reports and the weekly exports can be uploaded (remitwatch.api_uploads):
                           # both hosts, in pieces (the page shows its Upload link when either is true)
                           "report_uploads": True,
                           # whether appeals may be faxed in bulk (REMITWATCH_BULK_APPEAL): while it is off the pages offer
                           # no bulk appeal button, only the one-item send in each item's panel
                           "bulk_appeal": bulk_appeals_on(),
                           # whether this build has the run's decision log for an item ("Why did this change?" in its panel)
                           "decisions": _route_here("/api/findings/probe/decisions")}
    cached = one("SELECT value FROM meta WHERE key='cache:meta'") if has_table("meta") else None
    if cached:      # the dashboard's copy holds only part of the store; these totals were computed on all of it
        out.update(json.loads(cached["value"]))
        out["runs"] = rows("SELECT * FROM detection_runs ORDER BY started_at DESC LIMIT 5") if has_table("detection_runs") else []
        out["sync"] = rows("SELECT key, value FROM meta WHERE key LIKE 'sync:last%'")
        return out
    out["files"] = one("SELECT COUNT(*) n, SUM(kind='835') n835, SUM(kind LIKE 'ema:%') nema, MAX(ingested_at) last_ingest FROM files")
    out["remits"] = one("SELECT COUNT(*) n, MIN(payment_date) first_payment, MAX(payment_date) last_payment, SUM(payment_amount_cents) total_cents FROM transactions WHERE superseded=0")
    out["lines"] = one("SELECT COUNT(*) n, MIN(service_date) first_dos, MAX(service_date) last_dos FROM lines")
    out["runs"] = rows("SELECT * FROM detection_runs ORDER BY started_at DESC LIMIT 5") if has_table("detection_runs") else []
    out["ema"] = rows("SELECT family, COUNT(*) n, MIN(service_date) first_dos, MAX(service_date) last_dos FROM ema_rows GROUP BY family")
    out["sync"] = rows("SELECT key, value FROM meta WHERE key LIKE 'sync:%'")
    return out


# --------------------------------------------------------------------------- summary
@app.get("/api/summary")
def summary(year: Optional[str] = None, status: str = "open", _: str = Depends(require_auth)):
    where = ["category IN (%s)" % ",".join("?" * len(MONEY_CATEGORIES)), "COALESCE(stale,0)=0"]
    params: list[Any] = list(MONEY_CATEGORIES)
    if year:
        where.append("year=?")
        params.append(year)
    if status and status != "all":
        where.append("status=?")
        params.append(status)
    w = " AND ".join(where)
    out: dict[str, Any] = {}
    out["by_category"] = rows(f"SELECT category, COUNT(*) n, SUM(shortfall_cents) shortfall_cents FROM findings WHERE {w} GROUP BY category ORDER BY shortfall_cents DESC", tuple(params))
    # one row per signal (its highest severity, every action it takes), so a row opens a list of the same size
    out["by_signal"] = rows(f"""SELECT signal, category, CASE MIN(CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 WHEN 'low' THEN 2 ELSE 3 END)
                                       WHEN 0 THEN 'high' WHEN 1 THEN 'medium' WHEN 2 THEN 'low' ELSE 'info' END severity,
                                       GROUP_CONCAT(DISTINCT actionable) actionable, COUNT(*) n, SUM(shortfall_cents) shortfall_cents, SUM(charge_cents) charge_cents
                                FROM findings WHERE {w} GROUP BY signal, category ORDER BY shortfall_cents DESC""", tuple(params))
    out["by_payer"] = rows(f"SELECT payer_group, payer_kind, COUNT(*) n, SUM(shortfall_cents) shortfall_cents FROM findings WHERE {w} GROUP BY payer_group, payer_kind ORDER BY shortfall_cents DESC LIMIT 25", tuple(params))
    out["by_year"] = rows(f"SELECT year, category, COUNT(*) n, SUM(shortfall_cents) shortfall_cents FROM findings WHERE {w} GROUP BY year, category ORDER BY year", tuple(params))
    out["by_month"] = rows(f"SELECT substr(COALESCE(service_date, payment_date),1,7) month, category, COUNT(*) n, SUM(shortfall_cents) shortfall_cents FROM findings WHERE {w} AND COALESCE(service_date, payment_date) >= '2023-01' GROUP BY 1,2 ORDER BY 1", tuple(params))
    out["by_provider"] = rows(f"SELECT rendering_provider_npi npi, MAX(rendering_provider_name) name, MAX(provider_tier) tier, COUNT(*) n, SUM(shortfall_cents) shortfall_cents FROM findings WHERE {w} AND rendering_provider_npi IS NOT NULL GROUP BY rendering_provider_npi ORDER BY shortfall_cents DESC LIMIT 20", tuple(params))
    out["by_actionable"] = rows(f"SELECT actionable, COUNT(*) n, SUM(shortfall_cents) shortfall_cents FROM findings WHERE {w} GROUP BY actionable ORDER BY shortfall_cents DESC", tuple(params))
    out["by_handling"] = rows(f"SELECT COALESCE(ema_handling,'unknown') ema_handling, COUNT(*) n, SUM(shortfall_cents) shortfall_cents FROM findings WHERE {w} GROUP BY 1 ORDER BY shortfall_cents DESC", tuple(params))
    out["resolved_by_later_remit"] = one("SELECT COUNT(*) n, SUM(recovered_cents) shortfall_cents FROM findings WHERE status='resolved' AND auto_note IS NOT NULL")
    # an item the rules no longer find (stale: kept only for its notes and history) is in no total, so not in these either
    out["deadlines"] = rows("SELECT CASE WHEN appeal_deadline < ? THEN 'expired' WHEN appeal_deadline < ? THEN 'due_30d' WHEN appeal_deadline < ? THEN 'due_90d' ELSE 'later' END bucket, COUNT(*) n, SUM(shortfall_cents) shortfall_cents FROM findings WHERE status='open' AND COALESCE(stale,0)=0 AND appeal_deadline IS NOT NULL AND actionable IN ('appeal','correct_and_resubmit') "
                            f"AND signal NOT IN {PATTERN_SQL} GROUP BY 1", (_day(0), _day(30), _day(90)))
    # a routine-rate review or a repricing request counts by its claims' own deadlines, each finding once per bucket it touches
    buckets = {r["bucket"]: r for r in out["deadlines"]}
    seen: dict[str, set] = {}
    for fid, _g, _c, d, _n, cents in _windows():
        if d:
            b = "expired" if d < _day(0) else "due_30d" if d < _day(30) else "due_90d" if d < _day(90) else "later"
            r = buckets.setdefault(b, {"bucket": b, "n": 0, "shortfall_cents": 0})
            r["shortfall_cents"] = (r["shortfall_cents"] or 0) + cents
            if fid not in seen.setdefault(b, set()):
                seen[b].add(fid)
                r["n"] = (r["n"] or 0) + 1
    out["deadlines"] = list(buckets.values())
    out["info"] = rows("SELECT signal, year, title, shortfall_cents, evidence FROM findings WHERE category='info' ORDER BY signal, year")
    out["years"] = [r["year"] for r in rows("SELECT DISTINCT year FROM findings WHERE year >= '2023' ORDER BY year")]
    return out


# --------------------------------------------------------------------------- findings
FINDING_COLS = ("finding_id, signal, category, severity, actionable, title, patient_control_number, claim_key, line_key, payer_name, payer_group, payer_kind, "
                "rendering_provider_name, rendering_provider_npi, provider_tier, service_date, payment_date, year, procedure_code, modifiers, original_procedure_code, "
                "code_category, charge_cents, allowed_cents, paid_cents, expected_cents, shortfall_cents, carc, carc_text, rarc, rarc_text, appeal_deadline, ema_handling, written_off_cents, written_off_on, status, notes, first_seen, last_seen, "
                "billed_code, code_pair, level_drop, plan, pos_class, month, modifier_class, location, diagnosis, deadline_basis, recovered_cents, recovered_date, appealed_at, stale, auto_note, hint, "
                # what EMA's reports say about the item (remitwatch.ema_checks, through detection), its state (remitwatch.states)
                # and the payer's own limit beside the safe date the item is dated by
                "written_off_source, written_off_kind, written_off_when, ema_writeoff_evidence, ema_link, ema_status, ema_status_src, ema_as_of, "
                "ema_balance_cents, ema_worked_on, ema_worked_how, ema_outcome, ema_outcome_on, source, work_state, follow_up_on, state_basis, "
                "appeal_block, legal_deadline, "
                # what working it is worth: dollars x the payer's recovery rate for its pattern, with the basis (remitwatch.selfcheck)
                "expected_value_cents, recovery_rate, recovery_basis")


def _day(offset: int) -> str:
    """Today where the practice is (runtime.practice_today, REMITWATCH_TZ) plus `offset` days: the day every list, count and
    check here compares a deadline with, passed to SQL as a parameter (SQL's date('now') is UTC, a day ahead from 7 PM
    Eastern)."""
    return runtime.practice_today(offset)


DEADLINE_RANGES = {"valid": None, "week": 7, "month": 30, "missed": -30}   # filters that count a routine-rate finding's claims by their own dates
FOLLOW_UP_DAYS = 30         # an item sent this many days ago and still unpaid is followed up (insights.follow_ups)
DECIDE_DAYS = 120           # no filing date, paid in this many days to today: a person decides (insights.to_decide)


def _range(deadline: str) -> tuple[str, str | None]:
    days = DEADLINE_RANGES[deadline]
    if days is not None and days < 0:
        return _day(days), _day(0)              # windows that closed in the last N days
    return _day(0), (_day(days) if days else None)


def _pattern_cents(w: str, params: list, deadline: str | None) -> tuple[int, dict]:
    """The findings a filter holds that stand for many claims (a routine-rate review, a repricing request: PATTERN_SQL), by
    their claims: how much their claims outside the date range take off the total, and for each the cents and the first
    deadline of its claims in range (in window, without a range)."""
    found = rows(f"SELECT finding_id, COALESCE(shortfall_cents,0) c FROM findings{w}{' AND' if w else ' WHERE'} signal IN {PATTERN_SQL}", tuple(params))
    if not found:
        return 0, {}
    lo, hi = _range(deadline) if deadline in DEADLINE_RANGES else (_day(0), None)
    wins: dict[str, list] = defaultdict(list)
    for fid, _g, _c, d, n, cents in _windows(STATUSES):
        wins[fid].append((d, cents or 0, n or 0))
    delta, each = 0, {}
    for r in found:
        mine = [(d, c, n) for d, c, n in wins.get(r["finding_id"], []) if d and d >= lo and (hi is None or d < hi)]
        cents = sum(c for _d, c, _n in mine)
        if deadline in DEADLINE_RANGES:
            delta += cents - r["c"]
        each[r["finding_id"]] = (cents, min((d for d, _c, _n in mine), default=None), sum(n for _d, _c, n in mine))
    return delta, each


# A page that counts several lists in one call reads the routine-rate claims once (see desk() and brief()).
_WINDOWS_MEMO: ContextVar[dict | None] = ContextVar("_WINDOWS_MEMO", default=None)


def _windows(statuses: tuple = ("open",)) -> list[tuple]:
    memo = _WINDOWS_MEMO.get()
    if memo is not None and statuses in memo:
        return memo[statuses]
    conn = db()
    try:
        out = list(pattern_windows(conn, statuses))
    finally:
        conn.close()
    if memo is not None:
        memo[statuses] = out
    return out


def _finding_cols() -> str:
    """FINDING_COLS, with NULL for a column the store does not have yet. A store saved by an earlier release lacks the
    newest columns until the next detection run adds them, and the dashboard must work in that window."""
    have = {r["name"] for r in rows("PRAGMA table_info(findings)")}
    cols = [c if c in have else f"NULL AS {c}" for c in (x.strip() for x in FINDING_COLS.split(","))]
    if {"work_state", "follow_up_on", "state_basis"} <= have:
        # the item's state as the lists filter by it (_state_expr): a person's status set since the newest run decides
        at = {c: i for i, c in enumerate(cols)}
        for name, expr in zip(("work_state", "follow_up_on", "state_basis"), _state_cols(have)):
            cols[at[name]] = expr
    return ", ".join(cols)


def _value_sql(cols: set[str] | None = None) -> str:
    """What working an item is worth, as the lists sort by it: expected_value_cents (its dollars x the payer's recovery
    rate for its pattern, written by the run) where the run wrote one, else its dollars."""
    have = cols if cols is not None else _columns()
    return "COALESCE(expected_value_cents, shortfall_cents)" if "expected_value_cents" in have else "shortfall_cents"


EXTRA_FILTERS = {"plan": "COALESCE(plan,'')", "billed_code": "billed_code", "code_pair": "code_pair", "month": "month", "modifier": "modifier_class",
                 "pos": "pos_class", "location": "location", "diagnosis": "diagnosis", "carc": "carc", "tier": "provider_tier",
                 "payer_kind": "payer_kind", "code_category": "code_category", "level_drop": "level_drop", "quarter": None}


def _extra(request: Request) -> dict:
    return {k: request.query_params[k] for k in EXTRA_FILTERS if k in request.query_params}


# The member-number rule (codes.MEMBER_QUALS) as SQL, for the search.
_MEMBER_SQL = ("(COALESCE({a}.{k}_id_qual,'') IN ('MI','II','MR','') AND NOT (COALESCE({a}.{k}_id_qual,'')='MR' AND {a}.{k}_id GLOB "
               "'[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]'))")


_member = member_number


def _search_text(request: Request | None, q: str | None) -> str | None:
    """What was typed in the search box. The page sends it in a header, so that a patient's name is never part of an
    address, where access logs would keep it. A q in the address still works."""
    sent = request.headers.get("x-search") if request is not None else None
    if sent:
        from urllib.parse import unquote
        text = unquote(sent)
        if text.strip():
            return text
    return q


def _has_written_off() -> bool:
    """Whether the findings table has written_off_cents: a dashboard copy made before it was added has not."""
    return "written_off_cents" in {r["name"] for r in rows("PRAGMA table_info(findings)")}


def _columns() -> set[str]:
    """The findings table's columns on the copy at hand: a store or dashboard copy saved by an earlier release lacks the
    newest until the next detection run adds them, and every filter on one of them must still answer (with nothing)."""
    return {r["name"] for r in rows("PRAGMA table_info(findings)")}


# The item states (remitwatch.states, amendment 13.1) a list narrows to, plus 'followup_due' (a waiting item whose
# follow-up day has come: the state is worked out at each run, the day can pass between two runs) and 'todo' (work now
# and the follow-ups due: what the Worklist and the category pages open with). Several may be given, comma separated.
STATES = ("work_now", "waiting", "done", "accepted", "expired", "cant_check")
STATE_FILTERS = STATES + ("followup_due", "todo")
STATE_WORDS = {"work_now": "Work now", "waiting": "Waiting", "done": "Done", "accepted": "Accepted", "expired": "Expired",
               "cant_check": "Can't be checked", "followup_due": "Follow-up due", "todo": "Work now and follow-ups due"}
# an item the run has not given a state yet (a store saved before states, or a run whose states step failed) is read the
# way remitwatch.states reads a person's status, and as work to do otherwise: nothing drops off the lists for want of one
_STATE_GUESS_T = ("CASE WHEN COALESCE({a}stale,0)=1 THEN 'done' WHEN {a}status='resolved' THEN 'done' WHEN {a}status IN ('lost','dismissed') "
                  "THEN 'accepted' WHEN {a}status='appealed' THEN 'waiting' ELSE 'work_now' END")
_STATE_GUESS = _STATE_GUESS_T.format(a="")
# A status a person set after the newest detection run started decides the item's state until the next run works it out
# again, as remitwatch.states does (a person's status wins): Paid is done, Let go and Lost are accepted, Sent waits on the
# payer (no follow-up due until the run dates one), Started is work now, and To do is work now (done when no rule lists the
# item any more). Without it, an item a person let go, sent or marked paid stays on Work now until new data arrives.
# The hide rules' own changes (actions.apply_hide_rules after a run, a rule added or removed) are statuses like any other.
_STATUS_STATE = ("CASE {a}status WHEN 'resolved' THEN 'done' WHEN 'lost' THEN 'accepted' WHEN 'dismissed' THEN 'accepted' "
                 "WHEN 'appealed' THEN 'waiting' WHEN 'in_progress' THEN 'work_now' "
                 "ELSE CASE WHEN COALESCE({a}stale,0)=1 THEN 'done' ELSE 'work_now' END END")
_ANCHOR_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}[0-9T:.+Z -]{0,30}")
# EMA's view of an item (needs_ema_checks section 4): each a condition on the findings row's own columns
EMA_FILTERS = ("written_off", "contractual", "paid_other", "on_ar", "ema_only", "worked", "unworked", "auto_written_off")


def _status_state(f: dict) -> str:
    """_STATUS_STATE for one item at hand: the state a person's status gives it."""
    st = f.get("status") or "open"
    return {"resolved": "done", "lost": "accepted", "dismissed": "accepted", "appealed": "waiting", "in_progress": "work_now"}.get(
        st, "done" if f.get("stale") else "work_now")


def _run_anchor() -> str | None:
    """When the newest detection run started (detection_runs.started_at, the time each item it saved carries as
    last_seen): the states it stored were worked out on the statuses of that moment. None before any run is recorded (the
    stored states, or the guess, stand)."""
    if not has_table("detection_runs"):
        return None
    got = (one("SELECT MAX(started_at) a FROM detection_runs") or {}).get("a")
    return got if isinstance(got, str) and _ANCHOR_RE.fullmatch(got) else None


def _fresh_sql(cols: set[str], alias: str = "", anchor: str | None = None) -> str:
    """'A person set this item's status after the newest run started' as SQL (the run's time inlined: it is the store's
    own timestamp, checked by _ANCHOR_RE), or '0' when nothing can be newer."""
    if "status_changed_at" not in cols:
        return "0"
    a = anchor if anchor is not None else _run_anchor()
    return f"(COALESCE({alias}status_changed_at,'') > '{a}')" if a else "0"


def _state_expr(cols: set[str], alias: str = "", anchor: str | None = None) -> str:
    """The item's state as every list, tile, count and export reads it: the state the newest run stored (or the guess on a
    store without one), unless a person's status is newer, when the status decides (_STATUS_STATE)."""
    a = alias
    guess = _STATE_GUESS_T.format(a=a)
    stored = f"COALESCE({a}work_state, {guess})" if "work_state" in cols else guess
    fresh = _fresh_sql(cols, a, anchor)
    return stored if fresh == "0" else f"(CASE WHEN {fresh} THEN {_STATUS_STATE.format(a=a)} ELSE {stored} END)"


def _state_cols(cols: set[str], alias: str = "", anchor: str | None = None) -> list[str]:
    """work_state, follow_up_on and state_basis as a list shows them (three SQL expressions): the state per _state_expr,
    and no follow-up day or basis from the run while a person's newer status decides (the next run gives its own)."""
    a = alias
    fresh = _fresh_sql(cols, a, anchor)
    if fresh == "0":
        return [f"{a}work_state", f"{a}follow_up_on", f"{a}state_basis"]
    return [f"(CASE WHEN {fresh} THEN {_STATUS_STATE.format(a=a)} ELSE {a}work_state END) AS work_state",
            f"(CASE WHEN {fresh} THEN NULL ELSE {a}follow_up_on END) AS follow_up_on",
            f"(CASE WHEN {fresh} THEN NULL ELSE {a}state_basis END) AS state_basis"]


def _state_sql(states: str | None, cols: set[str], today: str) -> tuple[str, list] | None:
    """`state=` as one condition: work_now, waiting, done, accepted, expired, cant_check, followup_due, todo (several,
    comma separated, are any of them). None when nothing is asked; '0' for a name it does not know. The state is
    _state_expr's: a status a person set since the newest run decides it at once."""
    wanted = [x.strip() for x in (states or "").split(",") if x.strip()]
    if not wanted:
        return None
    anchor = _run_anchor()
    ws = _state_expr(cols, anchor=anchor)
    fresh = _fresh_sql(cols, anchor=anchor)
    due = "(follow_up_on IS NOT NULL AND follow_up_on <= ?)" if "follow_up_on" in cols else "0"
    if due != "0" and fresh != "0":
        due = f"({due[1:-1]} AND NOT {fresh})"      # a person's newer status: the run has not dated its follow-up yet
    parts, params = [], []
    for x in wanted:
        if x in STATES:
            parts.append(f"{ws} = ?")
            params.append(x)
        elif x == "followup_due":
            parts.append(f"({ws} IN ('work_now','waiting') AND {due})")
            params += [today] if due != "0" else []
        elif x == "todo":
            parts.append(f"({ws} = 'work_now' OR ({ws} = 'waiting' AND {due}))")
            params += [today] if due != "0" else []
        else:
            parts.append("0")
    return "(" + " OR ".join(parts) + ")", params


def _ema_sql(ema: str | None, cols: set[str]) -> tuple[str, list] | None:
    """`ema=` as one condition (needs_ema_checks section 4, so counts, tiles and lists agree): written_off, contractual,
    paid_other, on_ar, ema_only, worked, unworked (ema_checks.unworked_sql over the day the run measured it to) and
    auto_written_off. A copy without the column, or a name it does not know, answers with nothing."""
    if not ema:
        return None
    need = {"written_off": "written_off_cents", "contractual": "written_off_kind", "paid_other": "ema_outcome", "on_ar": "ema_link",
            "ema_only": "source", "worked": "ema_worked_on", "unworked": "ema_worked_on", "auto_written_off": "ema_outcome"}.get(ema)
    if need is None or need not in cols:
        return "0", []
    if ema == "written_off":
        return "COALESCE(written_off_cents,0) > 0", []
    if ema == "contractual":
        return "written_off_kind = 'contractual'", []
    if ema == "paid_other":
        return "ema_outcome = 'paid_after_other'", []
    if ema == "auto_written_off":
        return "ema_outcome = 'auto_written_off'", []
    if ema == "ema_only":
        return "source = 'ema'", []
    if ema == "worked":
        return "ema_worked_on IS NOT NULL", []
    if ema == "on_ar":
        return ("ema_link IN (SELECT link FROM ema_ar WHERE link IS NOT NULL)", []) if has_table("ema_ar") else ("0", [])
    # unworked: the day the run measured the number to (meta ema:unworked), so the list holds what Today's number counts
    from .ema_checks import UNWORKED_KEY, unworked_sql
    got = one("SELECT value FROM meta WHERE key=?", (UNWORKED_KEY,)) if has_table("meta") else None
    try:
        through = (json.loads(got["value"]) or {}).get("data_through") if got else None
    except (TypeError, ValueError):
        through = None
    if not through:
        return "0", []
    clause, params = unworked_sql(str(through))
    return f"({clause})", list(params)


def _list_sql(column: str, value: str | None, cols: set[str]) -> tuple[str, list] | None:
    """One column equal to one of the values given (comma separated); nothing on a copy without the column."""
    vals = [x.strip() for x in (value or "").split(",") if x.strip()]
    if not vals:
        return None
    if column not in cols:
        return "0", []
    return f"{column} IN ({','.join('?' * len(vals))})", vals


def _filters(signal, category, payer, year, status, severity, actionable, q, provider, code, deadline, handling=None, extra: dict | None = None,
             patient_of: str | None = None, kind: str | None = None, live: bool = False, paid_days: int = 0, min_dollars: int = 0, *,
             ema: str | None = None, state: str | None = None, ema_outcome: str | None = None, source: str | None = None,
             today: str | None = None):
    where, params = [], []
    if ema or state or ema_outcome or source:
        cols = _columns()
        for got in (_state_sql(state, cols, today or _day(0)), _ema_sql(ema, cols), _list_sql("ema_outcome", ema_outcome, cols),
                    _list_sql("source", source, cols)):
            if got is not None:
                where.append(got[0])
                params += got[1]
    if min_dollars:
        # the worklist's amount filter hides items known to be worth less (small items are a third of the list and little of the
        # money); an item whose value is not known stays, since it may be large
        where.append("NOT (COALESCE(shortfall_cents,0) > 0 AND shortfall_cents < ?)")
        params.append(int(min_dollars) * 100)
    if live:
        where.append("COALESCE(stale,0)=0")     # what the current rules still find, as the home screen counts it
    if paid_days:
        # paid in the last N days to the newest payment, as the home screen's denial causes count them
        where.append("payment_date > (SELECT date(MAX(payment_date), ?) FROM transactions WHERE COALESCE(superseded,0)=0)")
        params.append(f"-{int(paid_days)} days")
    if patient_of:
        if SHOW_PHI:
            # everything for the patient on this claim: same name and same member number, exactly. A search on the number
            # would also bring in the rest of a family on one policy.
            ident = "COALESCE(NULLIF({a}.patient_id,''), {a}.insured_id)"
            where.append("claim_key IN (SELECT c.claim_key FROM claims c, claims k WHERE k.claim_key=? AND c.patient_last IS k.patient_last "
                         f"AND c.patient_first IS k.patient_first AND {ident.format(a='c')} IS {ident.format(a='k')})")
            params.append(patient_of)
        else:
            where.append("0")       # with names hidden nothing says which findings belong to one person
    for k, v in (extra or {}).items():
        if k == "quarter":
            where.append("(substr(month,1,4) || '-Q' || ((CAST(substr(month,6,2) AS INTEGER)+2)/3)) = ?")
        else:
            where.append(f"{EXTRA_FILTERS[k]} = ?")
        params.append(v)
    if signal:
        sigs = [s for s in signal.split(",") if s]
        where.append("signal IN (%s)" % ",".join("?" * len(sigs)))
        params += sigs
    if kind:
        from .insights import kind_sql
        clause, kparams = kind_sql(kind)
        where.append(clause)
        params += kparams
    if category:
        cats = [c for c in category.split(",") if c]
        where.append("category IN (%s)" % ",".join("?" * len(cats)))
        params += cats
    if payer:
        where.append("payer_group=?")
        params.append(payer)
    if year:
        where.append("year=?")
        params.append(year)
    if status == "active":
        where.append("status != 'dismissed'")
    elif status and status != "all":
        wanted = [x for x in status.split(",") if x]
        where.append("status IN (%s)" % ",".join("?" * len(wanted)))
        params += wanted
    if severity:
        where.append("severity=?")
        params.append(severity)
    if actionable:
        where.append("actionable=?")
        params.append(actionable)
    if provider:
        where.append("rendering_provider_npi=?")
        params.append(provider)
    if code:
        where.append("procedure_code=?")
        params.append(code)
    if deadline == "due":
        # a routine-rate finding is due when any of its claims is (its row shows the last of their dates)
        ids = sorted({fid for fid, _g, _c, d, _n, _x in _windows(STATUSES) if d and _day(0) <= d < _day(45)})
        where.append("(appeal_deadline >= ? AND appeal_deadline < ?"
                     + (f" OR finding_id IN ({','.join('?' * len(ids))})" if ids else "") + ")")
        params.extend([_day(0), _day(45)] + ids)
    elif deadline == "expired":
        where.append("appeal_deadline < ?")
        params.append(_day(0))
    elif deadline in DEADLINE_RANGES:
        # a routine-rate finding is in range when one of its claims is, by that claim's own date
        lo, hi = _range(deadline)
        ids = sorted({fid for fid, _g, _c, d, _n, _x in _windows(STATUSES) if d and d >= lo and (hi is None or d < hi)})
        # the same day range as the claims above: from lo up to (not including) hi, the practice's days
        dated = "appeal_deadline >= ?" + ("" if hi is None else " AND appeal_deadline < ?")
        where.append(f"((COALESCE(signal,'') NOT IN {PATTERN_SQL} AND {dated})" + (f" OR finding_id IN ({','.join('?' * len(ids))})" if ids else "") + ")")
        params.extend([lo] + ([] if hi is None else [hi]) + ids)
    elif deadline == "none":
        where.append("appeal_deadline IS NULL")
    elif deadline == "followup":
        # the home screen's follow-ups: sent more than FOLLOW_UP_DAYS days ago, where the practice is, and still not paid.
        # Counted to today, not to the newest payment: a feed that stopped must not stop the follow-ups (the list says
        # when the remittances are behind)
        where.append("status='appealed' AND COALESCE(appealed_at, status_changed_at) < ?")
        params.append(runtime.practice_day_start(_day(-FOLLOW_UP_DAYS)))
    elif deadline == "older":
        # Today's "older ones": no filing date, and not among the items to decide
        where.append("appeal_deadline IS NULL AND NOT (COALESCE(actionable,'')='review' AND COALESCE(payment_date,'') >= ?)")
        params.append(_day(-DECIDE_DAYS))
    elif deadline == "decide":
        # the home screen's "decide": no filing date until a person says what the item is, paid in the last DECIDE_DAYS days
        # (to today, where the practice is)
        where.append("appeal_deadline IS NULL AND actionable='review' AND payment_date >= ?")
        params.append(_day(-DECIDE_DAYS))
    q = (q or "").strip()[:200]
    if q:
        clause = "patient_control_number LIKE ? OR title LIKE ? OR payer_name LIKE ? OR procedure_code LIKE ? OR carc LIKE ? OR rarc LIKE ?"
        params += [f"%{q}%"] * 6
        words = [w for w in re.split(r"[\s,]+", q) if w][:6]
        if SHOW_PHI and words:
            # by patient: every word typed must be in the name as the row shows it ("smith john", "John Smith" and
            # "SMITH, JOHN Q" all find the same person). One pass over the claims, no lookup per finding.
            # With names hidden this is left out, or the search box would answer "is there a patient called ...".
            # SQLite compares letters outside A to Z by case, and remittances spell names in capitals, so a word is also
            # tried in capitals ("nuñez" finds NUÑEZ)
            full = "(COALESCE(c.patient_last,'') || ' ' || COALESCE(c.patient_first,'') || ' ' || COALESCE(c.patient_middle,''))"
            parts, who_params = [], []
            for w in words:
                forms = list(dict.fromkeys([w, w.upper()]))
                parts.append("(" + " OR ".join(f"{full} LIKE ?" for _ in forms) + ")")
                who_params += [f"%{x}%" for x in forms]
            who = "(" + " AND ".join(parts) + ")"
            name_only, name_params = who, list(who_params)
            number = re.sub(r"[\s\-]", "", q)
            has_digit = bool(re.search(r"\d", number))
            if has_digit:
                # or part of a member number, typed with or without the spaces and dashes a card prints, whichever way
                # the payer wrote it
                bare = "REPLACE(REPLACE(c.{k}_id,'-',''),' ','')"
                who += (f" OR ({bare.format(k='patient')} LIKE ? AND {_MEMBER_SQL.format(a='c', k='patient')})"
                        f" OR ({bare.format(k='insured')} LIKE ? AND {_MEMBER_SQL.format(a='c', k='insured')})")
                who_params += [f"%{number}%"] * 2
            clause += f" OR claim_key IN (SELECT c.claim_key FROM claims c WHERE {who})"
            params += who_params
            if has_table("ema_rows"):
                # a claim no payer has answered has no remittance (no claim_key). Its row shows the member ID EMA holds for
                # it and the name from a remittance for another claim of the same EMA patient (_ema_people): the search finds
                # it by those too. Only those claims' own EMA rows are looked at (by claim ID, which is indexed)
                mine = "SELECT patient_control_number FROM findings WHERE claim_key IS NULL"
                ema = (f"patient_control_number IN (SELECT e.claim_id FROM ema_rows e WHERE e.claim_id IN ({mine}) AND e.patient_id IN ("
                       f"SELECT e2.patient_id FROM ema_rows e2 WHERE COALESCE(e2.patient_id,'') != '' AND e2.claim_id IN ("
                       f"SELECT c.patient_control_number FROM claims c WHERE {name_only})))")
                ema_params = name_params
                if has_digit:
                    ema += (f" OR patient_control_number IN (SELECT e.claim_id FROM ema_rows e WHERE e.claim_id IN ({mine}) "
                            "AND REPLACE(REPLACE(e.policy_number,'-',''),' ','') LIKE ?)")
                    ema_params = ema_params + [f"%{number}%"]
                clause += f" OR (claim_key IS NULL AND ({ema}))"
                params += ema_params
        where.append(f"({clause})")
    if handling == "written_off":
        # the biller wrote the disputed money off in EMA (detect.written_off_of): with a window still open, money given up.
        # A dashboard copy made before the column existed has none of it (and must not fail)
        where.append("COALESCE(written_off_cents,0) > 0" if _has_written_off() else "0")
    elif handling:
        where.append("ema_handling=?")
        params.append(handling)
    return (" WHERE " + " AND ".join(where)) if where else "", params


def _name(last: str | None, first: str | None, middle: str | None = None) -> str:
    """The patient's name as the payer wrote it: LAST, FIRST M."""
    out = ", ".join(x.strip() for x in (last or "", first or "") if x and x.strip())
    return f"{out} {middle.strip()}" if out and middle and middle.strip() else out


def _with_patient(items: list[dict]) -> list[dict]:
    """Say who each finding is about. With names shown that is the name and member number from the remittance; with names
    hidden it is the initials the detail view already gives. One query per 500 findings, by primary key, so it needs
    none of the other indexes."""
    keys = list({r["claim_key"] for r in items if r.get("claim_key")})
    who: dict[str, dict] = {}
    for i in range(0, len(keys), 500):
        part = keys[i:i + 500]
        for c in rows("SELECT claim_key, patient_last, patient_first, patient_middle, patient_id, patient_id_qual, insured_id, insured_id_qual "
                      f"FROM claims WHERE claim_key IN ({','.join('?' * len(part))})", tuple(part)):
            who[c["claim_key"]] = c
    # a claim no payer has answered (Chase, Fix and resubmit) has no remittance: EMA's rows say who it is about
    ema = _ema_people([r.get("patient_control_number") for r in items if not r.get("claim_key")])
    for r in items:
        c = who.get(r.get("claim_key"))
        if c is None and r.get("patient_control_number") in ema:
            r.update(_who_ema(ema[r["patient_control_number"]]))
        elif SHOW_PHI:
            r["patient"] = _name(c["patient_last"], c["patient_first"], c["patient_middle"]) if c else ""
            r["member_id"] = _member(c) if c else ""
        else:
            r["patient"] = _initials(c["patient_last"], c["patient_first"]) if c else ""
    return items


def _ema_people(claim_ids: list) -> dict[str, dict]:
    """Who each claim no payer has answered is about, from EMA's rows (it has no remittance to say so): the member ID EMA
    holds for that claim (the policy number on its newest row), and the patient's name from a remittance for another
    claim of the same EMA patient, when there is one. {claim ID: {patient_last, patient_first, patient_middle, member_id}}"""
    ids = sorted({str(x) for x in claim_ids if x})
    if not ids or not has_table("ema_rows"):
        return {}
    newest: dict[str, dict] = {}
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        for e in rows(f"""SELECT claim_id, policy_number, patient_id FROM ema_rows WHERE claim_id IN ({','.join('?' * len(part))})
                          ORDER BY claim_id, COALESCE(transaction_date, posted_date, service_date, ''), row_index""", tuple(part)):
            d = newest.setdefault(e["claim_id"], {})
            if (e["policy_number"] or "").strip():
                d["member_id"] = e["policy_number"].strip()
            if (e["patient_id"] or "").strip():
                d["ema_patient"] = e["patient_id"].strip()
    pids = sorted({d["ema_patient"] for d in newest.values() if d.get("ema_patient")})
    names: dict[str, dict] = {}
    for i in range(0, len(pids), 500):
        part = pids[i:i + 500]
        for n in rows(f"""SELECT e.patient_id pid, c.patient_last, c.patient_first, c.patient_middle FROM ema_rows e
                          JOIN claims c ON c.patient_control_number = e.claim_id
                          WHERE e.patient_id IN ({','.join('?' * len(part))}) AND COALESCE(c.patient_last,'') != ''
                          ORDER BY c.payment_date""", tuple(part)):
            names[n["pid"]] = n                 # the newest remittance's spelling wins
    out = {}
    for cid, d in newest.items():
        n = names.get(d.get("ema_patient") or "") or {}
        out[cid] = {"patient_last": n.get("patient_last"), "patient_first": n.get("patient_first"),
                    "patient_middle": n.get("patient_middle"), "member_id": d.get("member_id") or ""}
    return out


def _ema_first(claim_ids: list) -> dict:
    """_ema_people for a bill's submissions: the newest one that names the patient, else the newest with a member ID."""
    people = _ema_people(claim_ids)
    found = [people[c] for c in sorted(people, reverse=True)]
    return next((p for p in found if p.get("patient_last")), None) or next((p for p in found if p.get("member_id")), {})


def _who_ema(e: dict) -> dict:
    """_who for a claim known only from EMA's rows (_ema_people): initials, or with names shown the name and member ID."""
    if not SHOW_PHI:
        return {"patient": _initials(e.get("patient_last"), e.get("patient_first")) if e.get("patient_last") else ""}
    return {"patient": _name(e.get("patient_last"), e.get("patient_first"), e.get("patient_middle")) if e.get("patient_last") else "",
            "member_id": e.get("member_id") or ""}


@app.get("/api/findings")
def findings(signal: Optional[str] = None, category: Optional[str] = None, payer: Optional[str] = None, year: Optional[str] = None,
             status: str = "open", severity: Optional[str] = None, actionable: Optional[str] = None, q: Optional[str] = None,
             provider: Optional[str] = None, code: Optional[str] = None, deadline: Optional[str] = None, handling: Optional[str] = None,
             patient_of: Optional[str] = Query(None, max_length=200), sort: str = "shortfall", limit: int = Query(100, ge=1, le=2000),
             offset: int = Query(0, ge=0, le=10**9), kind: Optional[str] = Query(None, max_length=20), live: int = Query(0, ge=0, le=1),
             paid: int = Query(0, ge=0, le=3650), min: int = Query(0, ge=0, le=10**6), ema: Optional[str] = Query(None, max_length=40),
             state: Optional[str] = Query(None, max_length=120), work_state: Optional[str] = Query(None, max_length=120),
             ema_outcome: Optional[str] = Query(None, max_length=200), source: Optional[str] = Query(None, max_length=20),
             request: Request = None, _: str = Depends(require_auth)):
    w, params = _filters(signal, category, payer, year, status, severity, actionable, _search_text(request, q), provider, code, deadline, handling,
                         _extra(request), patient_of, kind, bool(live), paid, min, ema=ema, state=state or work_state, ema_outcome=ema_outcome,
                         source=source)
    delta, each = _pattern_cents(w, params, deadline)
    due, due_params = "appeal_deadline", []
    if each:
        # a routine-rate finding sorts by the first of its claims still in range, not by the last
        nxt = [(fid, d) for fid, (_c, d, _n) in each.items() if d]
        if nxt:
            due = "COALESCE(CASE finding_id " + " ".join("WHEN ? THEN ?" for _ in nxt) + " END, appeal_deadline)"
            due_params = [x for pair in nxt for x in pair]
    # by_due names `due` three times, the practice's day between the second and the third
    by_due = f"({due}) IS NULL, ({due}) < ?, {due} ASC"
    value = _value_sql()
    orders = {"shortfall": f"shortfall_cents DESC, {by_due}", "date": "service_date DESC", "deadline": f"{by_due}, shortfall_cents DESC",
              "payer": f"payer_group, {by_due}, shortfall_cents DESC", "paid": "payment_date DESC, shortfall_cents DESC",
              "sent": "COALESCE(appealed_at, status_changed_at) ASC, shortfall_cents DESC",
              # what working it is worth (dollars x the payer's recovery rate for its pattern), then the date it must be done by
              "value": f"({value}) IS NULL, {value} DESC, {by_due}"}
    order = orders.get(sort, orders["shortfall"])
    order_params = (due_params * 2 + [_day(0)] + due_params) if by_due in order else []
    total = one(f"SELECT COUNT(*) n, SUM(shortfall_cents) shortfall_cents FROM findings{w}", tuple(params))
    data = _with_patient(rows(f"SELECT {_finding_cols()} FROM findings{w} ORDER BY {order} NULLS LAST LIMIT ? OFFSET ?",
                              tuple(params) + tuple(order_params) + (limit, offset)))
    for r in data:
        if r.get("finding_id") in each and deadline in DEADLINE_RANGES:
            r["window_cents"], r["next_deadline"], r["window_lines"] = each[r["finding_id"]]
    cents = (total["shortfall_cents"] or 0) + delta if total["n"] else total["shortfall_cents"]
    return {"total": total["n"], "shortfall_cents": cents, "items": data}


def _csv_cell(v: Any) -> Any:
    """Text that came from a payer file must not be read as a formula when the export is opened in a spreadsheet."""
    if isinstance(v, str) and v and v[0] in "=+-@\t\r":
        try:
            float(v)
        except ValueError:
            return "'" + v
    return v


@app.get("/api/findings.csv")
def findings_csv(signal: Optional[str] = None, category: Optional[str] = None, payer: Optional[str] = None, year: Optional[str] = None,
                 status: str = "open", severity: Optional[str] = None, actionable: Optional[str] = None, q: Optional[str] = None,
                 provider: Optional[str] = None, code: Optional[str] = None, deadline: Optional[str] = None, handling: Optional[str] = None,
                 patient_of: Optional[str] = Query(None, max_length=200), kind: Optional[str] = Query(None, max_length=20),
                 live: int = Query(0, ge=0, le=1), paid: int = Query(0, ge=0, le=3650), min: int = Query(0, ge=0, le=10**6),
                 view: str = Query("raw", pattern="^(raw|worklist)$"), ema: Optional[str] = Query(None, max_length=40),
                 state: Optional[str] = Query(None, max_length=120), work_state: Optional[str] = Query(None, max_length=120),
                 ema_outcome: Optional[str] = Query(None, max_length=200), source: Optional[str] = Query(None, max_length=20),
                 request: Request = None, _: str = Depends(require_auth)):
    w, params = _filters(signal, category, payer, year, status, severity, actionable, _search_text(request, q), provider, code, deadline, handling,
                         _extra(request), patient_of, kind, bool(live), paid, min, ema=ema, state=state or work_state, ema_outcome=ema_outcome,
                         source=source)
    data = rows(f"SELECT {_finding_cols()} FROM findings{w} ORDER BY payer_group, shortfall_cents DESC", tuple(params))
    if view == "worklist":
        return _worklist_csv(data, deadline, w, params)
    # a routine-rate finding is about many claims: list them, each with its own filing deadline
    pattern = {r["finding_id"]: r["evidence"] for r in rows(f"SELECT finding_id, evidence FROM findings WHERE signal IN {PATTERN_SQL}")} \
        if any(r.get("signal") in ("SECOND_RATE", "OLD_FEE_SCHEDULE") for r in data) else {}
    for r in data:
        ev = json.loads(pattern[r["finding_id"]]) if pattern.get(r.get("finding_id")) else {}
        # each claim's filing date is its row's last field (a review's [claim, date, allowed, due], a repricing request's
        # [claim, date, code, allowed, owed, due])
        r["claims"] = "; ".join(f"{c[0]} ({c[1]}, file by {c[-1] if len(c) > 3 and c[-1] else 'unknown'})" for c in (ev.get("claims") or [])) or None
    if SHOW_PHI:
        _with_patient(data)     # with names hidden the file carries the claim ID only: initials beside a date and a clinician would point at a person

    def chunks():       # sent in pieces: a streamed answer has no size limit on a serverless host
        buf = io.StringIO()
        if data:
            who = [k for k in ("patient", "member_id") if k in data[0]]
            fields = [k for k in data[0].keys() if k not in who]
            at = fields.index("patient_control_number") + 1
            writer = csv.DictWriter(buf, fieldnames=fields[:at] + who + fields[at:])
            writer.writeheader()
            for i, r in enumerate(data):
                for k in list(r.keys()):
                    if k.endswith("_cents") and r[k] is not None:
                        r[k] = f"{r[k] / 100:.2f}"
                    else:
                        r[k] = _csv_cell(r[k])
                writer.writerow(r)
                if i % 400 == 399:
                    yield buf.getvalue()
                    buf.seek(0)
                    buf.truncate()
        yield buf.getvalue()
    return StreamingResponse(chunks(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=remitwatch-findings.csv"})


def _worklist_csv(data: list[dict], deadline: str | None, w: str, params: list) -> StreamingResponse:
    """The worklist as the page shows it: the date it must be done by, the kind of work, what happened and what to do in
    plain words, the amount (a routine-rate pattern's claims in the date range), the status in the page's words."""
    from .appeals import next_step
    from .insights import SIGNAL_LABELS, STATUS_LABELS, WORK_TYPES, work_type
    kinds = {t["key"]: t["label"] for t in WORK_TYPES}
    _delta, each = _pattern_cents(w, params, deadline)
    if SHOW_PHI:
        _with_patient(data)
    data.sort(key=lambda r: ((each.get(r["finding_id"], (0, None, 0))[1] or r.get("appeal_deadline")) is None,
                             each.get(r["finding_id"], (0, None, 0))[1] or r.get("appeal_deadline") or "", -(r.get("shortfall_cents") or 0)))
    cols = ["File by", "Kind of work", "What happened", "What to do", "Payer", "Code", "Date of service", "Claim ID"] \
        + (["Patient", "Member ID"] if SHOW_PHI else []) + ["Amount ($)", "Status", "Payer's reason", "Filing window"]

    def chunks():
        buf = io.StringIO()
        out = csv.writer(buf)
        out.writerow(cols)
        for i, r in enumerate(data):
            label, nxt = SIGNAL_LABELS.get(r.get("signal") or "", (r.get("title") or "", ""))
            cents, due, _n = each.get(r["finding_id"], (r.get("shortfall_cents"), None, 0))
            code = (r.get("original_procedure_code") + " to " if r.get("original_procedure_code") else "") + (r.get("procedure_code") or "")
            row = [due or r.get("appeal_deadline") or "", kinds.get(work_type(r.get("signal"), r.get("actionable"), r.get("carc")), ""), label,
                   next_step(r) or nxt, r.get("payer_group"), code, r.get("service_date"), r.get("patient_control_number")] \
                + ([r.get("patient"), r.get("member_id")] if SHOW_PHI else []) \
                + [f"{cents / 100:.2f}" if cents else "", STATUS_LABELS.get(r.get("status") or "", r.get("status")),
                   " ".join(x for x in (r.get("carc"), r.get("carc_text") if "carc_text" in r else None, r.get("rarc")) if x), r.get("deadline_basis")]
            out.writerow([_csv_cell(x) for x in row])
            if i % 400 == 399:
                yield buf.getvalue()
                buf.seek(0)
                buf.truncate()
        yield buf.getvalue()
    return StreamingResponse(chunks(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=remitwatch-worklist.csv"})


def _shown_note_name(p: dict | None) -> dict | None:
    """A packet as the page shows it: the note's file name is often the patient's name, so with names switched off it is
    shown by its kind only ("Visit note.pdf", as remitwatch.uploads.shown_name shows an uploaded note). What is kept and
    what goes to the payer are unchanged."""
    name = (p or {}).get("note_name")
    if p is not None and name and not SHOW_PHI and name != "pasted note":
        p["note_name"] = "Visit note" + os.path.splitext(str(name))[1].lower()[:6]
    return p


def _packets_for(finding_id: str) -> list[dict]:
    import re
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", finding_id or ""):
        return []           # it becomes part of a storage path
    if runtime.serverless():
        from . import cloud
        st = cloud.SERVING_COPY.state(fast=True)        # part of opening a finding: must not wait on a slow Dropbox
        try:
            got = [_shown_note_name(p) for p in cloud.list_packets(st, finding_id)] if st is not None else []
            # a packet stays in the folder of the item it was built for: once detection gave the item's money a new id
            # (detect.carry_to_heirs), the faxes it went out with name it
            named = [r["packet_id"] for r in rows("SELECT DISTINCT packet_id FROM faxes WHERE finding_id=? AND packet_id IS NOT NULL",
                                                    (finding_id,))] if (st is not None and has_table("faxes")) else []
            for pid in [p for p in named if p not in {g.get("packet_id") for g in got}]:
                p = cloud.get_packet(st, pid)
                if p:
                    got.append(_shown_note_name({k: p.get(k) for k in ("packet_id", "finding_id", "created_at", "mode", "model", "subject",
                                                                       "note_name", "emailed_to", "emailed_at")}))
            return got
        except Exception:
            return []
    return [_shown_note_name(p) for p in rows("SELECT packet_id, created_at, mode, subject, note_name, emailed_to, emailed_at FROM appeal_packets "
                                              "WHERE finding_id=? ORDER BY created_at DESC", (finding_id,))] if has_table("appeal_packets") else []


CLAIM_PUBLIC_COLS = ("claim_key", "transaction_key", "patient_control_number", "claim_status_code", "charge_cents", "paid_cents",
                     "patient_responsibility_cents", "claim_filing_indicator", "payer_claim_control_number", "facility_type_code",
                     "claim_frequency_code", "rendering_provider_name", "rendering_provider_npi", "contract_class", "received_date",
                     "statement_from", "statement_to", "payer_name", "payer_id", "payee_npi", "payment_date", "trace_number", "line_count", "balanced")


def _initials(last: str | None, first: str | None) -> str:
    return "".join(x[0] for x in (first or "", last or "") if x).upper() or "?"


def finding_detail(finding_id: str, _: str = "ok"):
    """One finding with everything its panel shows from the store: the claim (allowlisted columns), its lines, reasons
    and remarks, its remittances, the EMA export rows, the history, packets and faxes. The routes that build a letter or
    a fax read it too (without the story: see finding_page)."""
    f = one("SELECT * FROM findings WHERE finding_id=?", (finding_id,))
    if not f:
        _not_found(finding_id)
    f["evidence"] = json.loads(f["evidence"]) if f.get("evidence") else {}
    from .appeals import next_step
    f["next_step"] = next_step(f)
    out: dict[str, Any] = {"finding": f}
    claim_key = f.get("claim_key")
    if claim_key:
        cl = one("SELECT * FROM claims WHERE claim_key=?", (claim_key,))
        if cl:
            # only columns known to carry no patient identifier leave the server (the claim loop also holds other
            # subscribers' names, free-form references and sometimes a Social Security number), plus who the patient is:
            # initials, or with names shown the name, the member number and the insured person when that is someone else
            raw, cl = cl, {k: cl.get(k) for k in CLAIM_PUBLIC_COLS}
            if not SHOW_PHI:
                cl["patient"] = _initials(raw.get("patient_last"), raw.get("patient_first"))
            else:
                cl["patient"] = _name(raw.get("patient_last"), raw.get("patient_first"), raw.get("patient_middle"))
                cl["member_id"] = _member(raw)
                same = lambda a, b: (a or "").strip().upper() == (b or "").strip().upper()
                if (raw.get("insured_last") or raw.get("insured_first")) and not (same(raw.get("insured_last"), raw.get("patient_last"))
                                                                                    and same(raw.get("insured_first"), raw.get("patient_first"))):
                    cl["insured"] = _name(raw.get("insured_last"), raw.get("insured_first"))
            out["claim"] = cl
        out["lines"] = rows("SELECT line_key, line_seq, procedure_code, modifiers, original_procedure_code, original_modifiers, charge_cents, paid_cents, allowed_cents, paid_units, service_date, line_control_number FROM lines WHERE claim_key=? ORDER BY line_seq", (claim_key,))
        adjs = rows("SELECT line_key, level, group_code, reason_code, amount_cents FROM adjustments WHERE claim_key=? ORDER BY line_key, cas_index", (claim_key,))
        for a in adjs:
            a["text"] = CARC.get(a["reason_code"], "")
        out["adjustments"] = adjs
        rems = rows("SELECT line_key, level, qualifier, code FROM remarks WHERE claim_key=?", (claim_key,))
        for r in rems:
            r["text"] = RARC.get(r["code"], "")
        out["remarks"] = rems
        if f.get("line_key") and has_table("line_links"):
            out["link"] = one("SELECT * FROM line_links WHERE line_key=?", (f["line_key"],))
            out["handling"] = rows("SELECT * FROM line_handling WHERE line_key=? ORDER BY transaction_date", (f["line_key"],))
        pcn = f.get("patient_control_number")
        if pcn:
            out["history"] = rows(f"SELECT c.claim_key, c.claim_status_code, c.charge_cents, c.paid_cents, c.payment_date, c.payer_name, c.trace_number FROM claims c JOIN transactions t ON t.transaction_key=c.transaction_key WHERE c.patient_control_number=? AND t.superseded=0 ORDER BY {PAYER_ORDER}", (pcn,))
            out["ema"] = rows("SELECT family, source_name, service_date, cpt, modifiers, units, charges_cents, payments_cents, adjustments_cents, allowed_cents, claim_status, activity_type, carc, rarc, adjustment_status, adjustment_subtype, provider, payer, denial_category, previously_denied FROM ema_rows WHERE claim_id=? ORDER BY family, service_date, row_index", (pcn,))
    elif f.get("patient_control_number"):
        # a claim no payer has answered has no remittance: who it is about comes from EMA's rows (no payer claim number yet)
        e = _ema_people([f["patient_control_number"]]).get(f["patient_control_number"]) or {}
        if e.get("patient_last") or (SHOW_PHI and e.get("member_id")):
            out["claim"] = dict(_who_ema(e), from_ema=True)
    from .detect import bill_of
    bill = bill_of(f["patient_control_number"]) if f.get("patient_control_number") else (_bill_of_reference((f["evidence"] or {}).get("reference")) if f.get("signal") == "TAKEBACK" else None)
    if bill and _bill_ok(bill):
        out["bill"] = bill                  # the whole claim opens from the drawer: "See the whole claim"
    if f.get("transaction_key"):        # the remittance it came from (a takeback or a payment fee has one and no claim)
        out["remit"] = one("SELECT transaction_key, source_name, payer_name, payer_id, payer_contact_phone, payer_contact_url, payment_method, payment_amount_cents, payment_date, trace_number, production_date, payee_npi FROM transactions WHERE transaction_key=?", (f["transaction_key"],))
    # the record of what people did, for every finding (a payer's routine rate belongs to no one claim)
    out["events"] = rows("SELECT at, kind, from_status, to_status, amount_cents, actor, note FROM finding_events WHERE finding_id=? ORDER BY at DESC, id DESC LIMIT 30", (finding_id,)) if has_table("finding_events") else []
    out["packets"] = _packets_for(finding_id)
    # the faxes sent for it and the payer's faxes tied to it (remitwatch.fax)
    out["faxes"] = []
    if has_table("faxes"):
        # a payer's letter about several items of one claim is filed under the first of them and lists the rest as tied
        for x in rows("""SELECT fax_key, finding_id, direction, number, purpose, level, status, pages, error, created_at, sent_at, doc FROM faxes
                         WHERE finding_id=? OR (direction='in' AND doc LIKE ?) ORDER BY created_at DESC LIMIT 50""",
                      (finding_id, f'%"{finding_id}"%')):
            try:
                d = json.loads(x.pop("doc") or "{}")
            except ValueError:
                d = {}
            if x.pop("finding_id") != finding_id and finding_id not in (d.get("tied") or [] if isinstance(d, dict) else []):
                continue
            d = d if isinstance(d, dict) else {}
            x.update(summary=d.get("summary") if x["direction"] == "in" else d.get("subject"), decision=d.get("decision"))
            out["faxes"].append(x)
        sent = [x["created_at"] for x in out["faxes"] if x["direction"] == "out" and x["status"] != "failed"]
        f["faxed_at"] = min(sent) if sent else None      # its follow-ups go by fax by themselves
    return out


@app.get("/api/findings/{finding_id}")
def finding_page(finding_id: str, story: int = Query(0, ge=0, le=1), _: str = Depends(require_auth)):
    """An item's panel: finding_detail. story=1 adds what /api/findings/{id}/explain gives, in the same answer. The state
    is the one the lists show: a status a person set since the newest run decides it (state_by_status says so)."""
    out = finding_detail(finding_id, "ok")
    f = out["finding"]
    anchor = _run_anchor()
    if anchor and "work_state" in f and str(f.get("status_changed_at") or "") > anchor:
        f.update(work_state=_status_state(f), follow_up_on=None, state_basis=None, state_by_status=True)
    if story:
        out.update(_explained(out["finding"]))
    return out


@app.get("/api/findings/{finding_id}/explain")
def finding_explain(finding_id: str, _: str = Depends(require_auth)):
    """The item's story, verdict and next step (remitwatch.story.explain, one gathering of its facts: `explain`) and what
    EMA's reports hold on its claim (remitwatch.ema_checks.claim_block: `ema_block`). The panel asks for it beside the
    finding itself, so the item's own facts show at once. On a server the posting ledger is opened read-only and the story
    lists every EMA posting; on Vercel the dashboard's copy shows EMA's summaries and says the postings are kept on the
    server (and that the state shown was worked out on the daily run's full store)."""
    f = one("SELECT finding_id, patient_control_number, ema_link FROM findings WHERE finding_id=?", (finding_id,)) \
        if "ema_link" in _columns() else one("SELECT finding_id, patient_control_number FROM findings WHERE finding_id=?", (finding_id,))
    if not f:
        raise HTTPException(404, "finding not found")
    return dict(_explained(f), finding_id=finding_id)


def _ledger_readonly():
    """The posting ledger opened read-only (a server's, beside its store), or None: a serverless host keeps none, and a
    server has none until its first Production report. Closed by the caller."""
    if runtime.serverless():
        return None
    from . import ema_ledger
    path = ema_ledger.ledger_path(DB_PATH)
    if not os.path.exists(path):
        return None
    try:
        return ema_ledger.Ledger.open(path, readonly=True)
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        # the story is told from the store's summaries instead, and says the detail is kept on the server
        _say(f"api: the posting ledger could not be opened read-only ({type(exc).__name__}); the item panels show EMA's summaries")
        return None


def _say(line: str) -> None:
    """One line in the server's log (no patient, claim or check number in it: callers pass ids and kinds only)."""
    import sys
    print(line, file=sys.stderr, flush=True)


def _explained(f: dict, *, claim_key: str | None = None, bill: str | None = None) -> dict:
    """{'explain': {'story', 'verdict', 'next_step', 'state'}, 'ema_block': claim_block} for an item (f) or, with claim_key,
    for a whole claim ({'claim_story', 'ema_block'}). A story that cannot be put together leaves the rest of the panel as it
    is and says why ('explain_error'), and the server's log names the item and the kind of error."""
    from . import ema_checks as ec
    from . import story as story_mod
    from .detect import bill_of
    out: dict[str, Any] = {}
    lg = _ledger_readonly()
    conn = db()
    conn.row_factory = None
    try:
        try:
            if claim_key:
                out["claim_story"] = story_mod.item_story(conn, claim_key=claim_key, ledger=lg, today=_day(0))
            else:
                out["explain"] = story_mod.explain(conn, f["finding_id"], ledger=lg, today=_day(0))
        except Exception as exc:        # noqa: BLE001  (shown on the panel and logged: the item's own facts still show)
            out["explain_error"] = f"The story of this {'claim' if claim_key else 'item'} could not be put together ({type(exc).__name__})."
            _say(f"api: story failed for {'claim' if claim_key else 'item ' + str(f.get('finding_id'))}: {type(exc).__name__}: {str(exc)[:160]}")
        try:
            pcn = (f or {}).get("patient_control_number")
            links = [int(f["ema_link"])] if (f or {}).get("ema_link") not in (None, "") else []
            out["ema_block"] = ec.claim_block(conn, claim_id=pcn, bill=bill or (bill_of(pcn) if pcn else None), links=links, ledger=lg)
        except Exception as exc:        # noqa: BLE001  (a store with no EMA tables yet, or a damaged one: said on the panel)
            out["ema_block"] = None
            out["ema_block_error"] = f"EMA's reports for this claim could not be read ({type(exc).__name__})."
            _say(f"api: claim_block failed: {type(exc).__name__}: {str(exc)[:160]}")
    finally:
        conn.close()
        if lg is not None:
            lg.close()
    return out


def _record_status(ids: list[str], status: str, notes: str | None, recovered_cents: int | None, actor: str) -> int:
    """Apply a status to one or more findings. On a serverless host the change is also written to the journal in the
    state folder, which is what makes it last."""
    if runtime.serverless():
        from .cloud import SERVING_COPY
        conn = db()
        try:
            # the status each finding has on the copy the person is looking at: logged as what they changed it from
            seen = {i: r[0] for i in ids for r in [conn.execute("SELECT status FROM findings WHERE finding_id=?", (i,)).fetchone()] if r}
        finally:
            conn.close()
        known = [i for i in ids if i in seen]
        if not known:
            return 0
        if len(known) == 1:
            SERVING_COPY.record({"kind": "status", "finding_id": known[0], "status": status, "notes": notes,
                                 "recovered_cents": recovered_cents, "actor": actor, "seen": seen[known[0]]})
        else:
            SERVING_COPY.record({"kind": "bulk_status", "ids": known, "status": status, "notes": notes, "actor": actor,
                                 "seen": {i: seen[i] for i in known}})
        return len(known)
    conn = db()
    try:
        n = sum(1 for i in ids if apply_status(conn, i, status, notes, recovered_cents if len(ids) == 1 else None, actor))
        conn.commit()
        return n
    finally:
        conn.close()


# Let go and Lost take a claim off every list. A claim the payer never answered, or one rejected before it reached the
# payer (actions.NEVER_HIDDEN), is money nobody has asked for yet: it is let go one at a time, with a note, never in bulk.
# The page asks for the note; the server holds to the same rule. Only here, not in actions.apply_status: journal entries
# written before the rule must still replay.
LET_GO = ("dismissed", "lost")


def _one_at_a_time(ids: list) -> list[str]:
    """Which of these findings are such claims, read on the copy the dashboard shows (on Vercel, the serving copy)."""
    from .actions import NEVER_HIDDEN
    ids = [str(i) for i in ids]
    sigs = sorted(NEVER_HIDDEN)
    conn = db()
    try:
        out: list[str] = []
        for k in range(0, len(ids), 500):
            part = ids[k:k + 500]
            out += [r[0] for r in conn.execute(f"""SELECT finding_id FROM findings WHERE finding_id IN ({','.join('?' * len(part))})
                                                   AND signal IN ({','.join('?' * len(sigs))})""", (*part, *sigs))]
        return out
    finally:
        conn.close()


# What a person's status change is checked against before anything is written or journaled (the hotfix of chaos round 0):
# the body is what it says it is; the item still has the status the person's page showed (seen_status); no fax for it is
# going out; a Paid has the amount the payer paid.
STATUS_LABELS = {"open": "To do", "in_progress": "Started", "appealed": "Sent", "resolved": "Paid", "lost": "Lost", "dismissed": "Let go"}
MAX_NOTES = 5000
MAX_PAID_CENTS = 100_000_000            # $1,000,000: no single item is paid more
PAID_TIMES_CHARGE = 10                  # nor more than ten times its line's charge
IN_FLIGHT = "A fax for this item is going out now. Wait a minute and look at the item again."
NO_AMOUNT = "Type the amount the payer paid, from the remittance or EMA. If you do not know it yet, leave the item as it is."
PAID_ZERO = "Paid needs an amount above $0. If the payer paid nothing, mark it Lost or Let go instead."
# items done with $0 recovered, by their work (remitwatch.states' appeal_block): a payment the 835 made that EMA never posted
# ('post'), and a credit that is only a takeback EMA never posted ('credit'): the money already came, or none is owed
ZERO_PAID_BLOCKS = ("post", "credit")


async def _body_object(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "The change could not be read. Reload the page and try again.")
    if not isinstance(body, dict):
        raise HTTPException(400, "The change could not be read: it must say the status, and for Paid the amount.")
    return body


def _whole_cents(v: Any) -> int | None:
    """A whole number of cents as sent (a JSON number), or None when it is not one."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if isinstance(v, float) and not (v == v and abs(v) != float("inf") and v.is_integer()):
        return None
    return int(v)


def _status_fields(body: dict) -> tuple[str, str | None, int | None]:
    """The status, the note and a recovered amount, each checked for what it must be."""
    status = body.get("status")
    if not isinstance(status, str) or status not in STATUSES:
        raise HTTPException(400, "Say which status: To do, Started, Sent, Paid, Lost or Let go.")
    notes = body.get("notes")
    if notes is not None and not isinstance(notes, str):
        raise HTTPException(400, "The note must be text.")
    if notes and len(notes) > MAX_NOTES:
        raise HTTPException(400, f"A note can be at most {MAX_NOTES:,} characters.")
    if notes:
        try:
            notes.encode("utf-8")
        except UnicodeEncodeError:
            raise HTTPException(400, "The note has characters that cannot be kept. Type it again.")
    rec = body.get("recovered_cents")
    if rec is not None:
        rec = _whole_cents(rec)
        if rec is None:
            raise Refused(400, "amount", "The amount paid must be a number of whole cents.")
    return status, notes, rec if status == "resolved" else None


def _seen_ok(v: Any) -> bool:
    return v is None or (isinstance(v, str) and v in STATUSES)


def _status_now(conn: sqlite3.Connection, ids: list[str]) -> dict[str, dict]:
    """Each item's status on this copy, whether the daily run set it by itself (a payment it found, a reason hidden for a
    payer), and its charge (the bound on a Paid amount)."""
    from .actions import LAST_DECISION
    events = bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='finding_events'").fetchone())
    block = "f.appeal_block" if "appeal_block" in {r[1] for r in conn.execute("PRAGMA table_info(findings)")} else "NULL"
    out: dict[str, dict] = {}
    for k in range(0, len(ids), 500):
        part = ids[k:k + 500]
        for r in conn.execute(f"""SELECT f.finding_id, f.status, f.auto_note, f.charge_cents, {LAST_DECISION if events else "NULL"} last,
                                         {block} block
                                  FROM findings f WHERE f.finding_id IN ({','.join('?' * len(part))})""", part):
            out[r[0]] = {"status": r[1], "charge_cents": r[3], "block": r[5],
                         "by_run": (r[1] == "resolved" and bool(r[2])) or (r[1] == "dismissed" and r[4] == "hidden")}
    return out


def _sending_now(conn: sqlite3.Connection, ids: list[str]) -> set[str]:
    """Items with a fax going out now: reserved, not yet taken by the line, not settled as failed or unknown, and younger
    than a send can take; or an appeal being prepared for it (the note looked up and reviewed)."""
    if not ids or not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='faxes'").fetchone():
        out: set[str] = set()
    else:
        cut = (datetime.now(timezone.utc) - timedelta(seconds=IN_FLIGHT_S)).isoformat(timespec="microseconds")
        out = set()
        for k in range(0, len(ids), 500):
            part = ids[k:k + 500]
            out |= {r[0] for r in conn.execute(f"""SELECT DISTINCT finding_id FROM faxes WHERE finding_id IN ({','.join('?' * len(part))})
                                                   AND direction='out' AND status='sending' AND provider_id IS NULL AND error IS NULL
                                                   AND created_at > ?""", (*part, cut))}
    return out | _preparing(conn, ids)


def _person_status(ids: list[str], status: str, notes: str | None, rec: int | None, actor: str, seen: dict, single: bool) -> dict:
    """A person's status change, checked and applied in one step: on a server inside one transaction on the store, on a
    serverless host against the copy it serves, then journaled. single: one item's change, refused as a whole (Refused)
    when it must not be made; else the items that must not be changed are left as they are and named."""
    out: dict = {"n": 0, "changed": [], "in_flight": []}

    def sort(conn) -> list[str]:
        now = _status_now(conn, ids)
        flying = _sending_now(conn, [i for i in ids if i in now])
        keep: list[str] = []
        for i in ids:
            cur = now.get(i)
            if cur is None:
                if single:
                    _not_found(i, conn)
                if _heir_now(conn, i):
                    out["changed"].append(i)        # its money has a new number since the list was drawn: left as it is
                continue
            if i in flying:
                if single:
                    raise Refused(409, "in_flight", IN_FLIGHT)
                out["in_flight"].append(i)
                continue
            saw = seen.get(i)
            if (saw is not None and saw != cur["status"]) or (saw is None and cur["by_run"]):
                if single:
                    raise Refused(409, "changed", f"This item changed since your page loaded: it is now "
                                                  f"{STATUS_LABELS.get(cur['status'], cur['status'])}. It was not changed.", status=cur["status"])
                out["changed"].append(i)
                continue
            if single and status == "resolved":
                charge = cur.get("charge_cents") or 0
                top = min(MAX_PAID_CENTS, PAID_TIMES_CHARGE * charge) if charge > 0 else MAX_PAID_CENTS
                if rec is None:
                    raise Refused(400, "amount", NO_AMOUNT)
                if rec < 0:
                    raise Refused(400, "amount", "The amount paid cannot be below zero.")
                if rec == 0 and cur.get("block") not in ZERO_PAID_BLOCKS:
                    raise Refused(400, "amount", PAID_ZERO)   # nothing recovered: the item was not paid, it was lost or let go
                if rec > top:
                    raise Refused(400, "amount", f"That is more than {'ten times the charge on this line' if top < MAX_PAID_CENTS else '$1,000,000'}"
                                                 f" (${top / 100:,.2f}). Check the amount on the remittance or in EMA.")
            keep.append(i)
        if single and not keep:
            raise HTTPException(404, "finding not found")
        return keep

    if runtime.serverless():
        from .cloud import SERVING_COPY
        conn = db()
        try:
            keep = sort(conn)
            was = {i: s["status"] for i, s in _status_now(conn, keep).items()}
        finally:
            conn.close()
        if keep:
            # the status each finding had on the copy the person was looking at: logged as what they changed it from
            if single:
                SERVING_COPY.record({"kind": "status", "finding_id": keep[0], "status": status, "notes": notes, "recovered_cents": rec,
                                     "actor": actor, "seen": was.get(keep[0])})
            else:
                SERVING_COPY.record({"kind": "bulk_status", "ids": keep, "status": status, "notes": notes, "actor": actor,
                                     "seen": {i: was.get(i) for i in keep}})
        out["n"] = len(keep)
        return out
    conn = sqlite3.connect(DB_PATH, timeout=60, isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            keep = sort(conn)
            out["n"] = sum(1 for i in keep if apply_status(conn, i, status, notes, rec if single else None, actor))
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    return out


def _known(finding_id: str) -> bool:
    """Whether the copy the dashboard shows holds this item."""
    return one("SELECT 1 FROM findings WHERE finding_id=?", (finding_id,)) is not None


# Money is let go (or marked lost) only through the owner's policy table or by a person with a reason (brief C, shared
# rules): the reason goes in the item's history. Here, not in actions.apply_status, so journal entries from before keep
# replaying.
LET_GO_REASON = "Letting an item go, or marking it lost, needs a reason: say why (it is kept in the item's history)."


@app.post("/api/findings/{finding_id}/status", dependencies=[Depends(require_xhr)])
async def set_status(finding_id: str, request: Request, user: str = Depends(require_auth)):
    """A person's status for one item. seen_status: the status the item had on their page when it was drawn; an item that
    changed since is left as it is (409 changed), and so is one with a fax going out (409 in_flight). A page from before
    seen_status existed is refused for an item the daily run closed by itself. Paid takes the amount the payer paid. Let go
    and Lost take a reason (LET_GO_REASON), kept in the item's history."""
    body = await _body_object(request)
    status, notes, rec = _status_fields(body)
    seen = body.get("seen_status")
    if not _seen_ok(seen):
        raise HTTPException(400, "The status your page showed could not be read. Reload the page.")
    if status in LET_GO and not (notes or "").strip():
        if not await asyncio.to_thread(_known, finding_id):
            await asyncio.to_thread(_not_found, finding_id)        # 409 with its new number, or 404
        if await asyncio.to_thread(_one_at_a_time, [finding_id]):
            raise HTTPException(400, "A claim the payer never answered, or one rejected before it reached the payer, is let go "
                                     "only with a note. Say why.")
        raise HTTPException(400, LET_GO_REASON)
    await asyncio.to_thread(_person_status, [finding_id], status, notes, rec, user, {finding_id: seen} if seen else {}, True)
    return {"ok": True}


@app.post("/api/findings/bulk-status", dependencies=[Depends(require_xhr)])
async def bulk_status(request: Request, user: str = Depends(require_auth)):
    """A status for the items ticked on a list. seen (or seen_status): {finding_id: the status its row showed}; items that
    changed since, or that have a fax going out, are left as they are and named in `changed` and `in_flight`. An id sent
    twice counts once."""
    body = await _body_object(request)
    status, notes, _rec = _status_fields(body)
    ids = body.get("ids")
    if not isinstance(ids, list) or not ids or len(ids) > 5000 or not all(isinstance(i, str) and 0 < len(i) <= 200 for i in ids):
        raise HTTPException(400, "Say which items: a list of item ids.")
    ids = list(dict.fromkeys(ids))
    seen = body.get("seen_status") if isinstance(body.get("seen_status"), dict) else body.get("seen")
    if seen is not None and not (isinstance(seen, dict) and all(isinstance(k, str) and _seen_ok(v) for k, v in seen.items())):
        raise HTTPException(400, "The statuses your page showed could not be read. Reload the page.")
    if status in LET_GO and await asyncio.to_thread(_one_at_a_time, ids):
        raise HTTPException(400, "Claims the payer never answered, or rejected before reaching it, are let go one at a time, "
                                 "with a note. Open each one.")
    if status in LET_GO and not (notes or "").strip():
        raise HTTPException(400, "Letting items go, or marking them lost, needs a reason: say why, once for all of them (it is kept "
                                 "in each item's history).")
    out = await asyncio.to_thread(_person_status, ids, status, notes, None, user, {k: v for k, v in (seen or {}).items() if v}, False)
    return {"ok": True, "updated": out["n"], "changed": out["changed"], "in_flight": out["in_flight"]}


# --------------------------------------------------------------------------- rates and references
@app.get("/api/rates")
def rates(payer: Optional[str] = None, year: Optional[str] = None, code: Optional[str] = None, min_n: int = 20,
          request: Request = None, _: str = Depends(require_auth)):
    where, params = ["n >= ?"], [min_n]
    if payer:
        where.append("payer_group=?"); params.append(payer)
    if year:
        where.append("year=?"); params.append(year)
    if code:
        where.append("code=?"); params.append(code)
    out = rows(f"SELECT * FROM payer_rates WHERE {' AND '.join(where)} ORDER BY payer_group, year, code, tier, pos_class", tuple(params))
    if owner_state(request) != "open":
        solo = _solo_rate_groups()
        for r in out:
            if (r.get("payer_group"), r.get("tier"), str(r.get("year"))) in solo:
                r["n"] = None           # the rate stays; how many lines it rests on is one clinician's volume
    return out


@app.get("/api/rates/matrix")
def rates_matrix(year: str, tier: str = "physician", codes: str = "99203,99204,99213,99214,11102,11103,17000,17003,17110,17311,88305,12031,13132,14040",
                 request: Request = None, _: str = Depends(require_auth)):
    code_list = [c for c in codes.split(",") if c]
    solo = _solo_rate_groups() if owner_state(request) != "open" else set()
    data = rows(f"SELECT payer_group, payer_kind, code, mode_cents, n, medicare_cents, pct_of_medicare FROM payer_rates WHERE year=? AND tier=? AND pos_class='office' AND code IN ({','.join('?' * len(code_list))}) AND n >= 20",
                (year, tier, *code_list))
    payers: dict[str, dict] = {}
    for r in data:
        p = payers.setdefault(r["payer_group"], {"payer_group": r["payer_group"], "payer_kind": r["payer_kind"], "codes": {}, "lines": 0})
        hide = (r["payer_group"], tier, str(year)) in solo
        p["codes"][r["code"]] = {"cents": r["mode_cents"], "n": None if hide else r["n"], "medicare_cents": r["medicare_cents"], "pct": r["pct_of_medicare"]}
        p["order"] = p.get("order", 0) + r["n"]
        p["lines"] = None if hide else p["lines"] + r["n"]
    out = sorted(payers.values(), key=lambda p: -p.pop("order", 0))
    return {"year": year, "tier": tier, "codes": code_list, "payers": out}


@app.get("/api/trends")
def trends(category: Optional[str] = None, signal: Optional[str] = None, by: str = "payer_group", _: str = Depends(require_auth)):
    """Monthly counts and dollars by payer group (or signal) for a category or signal, all statuses, by month of service."""
    where, params = ["COALESCE(service_date, payment_date) >= '2023-01'", "COALESCE(stale,0)=0"], []
    if category:
        cats = [c for c in category.split(",") if c]
        where.append("category IN (%s)" % ",".join("?" * len(cats))); params += cats
    if signal:
        sigs = [x for x in signal.split(",") if x]
        where.append("signal IN (%s)" % ",".join("?" * len(sigs))); params += sigs
    col = "signal" if by == "signal" else "payer_group"
    return rows(f"SELECT substr(COALESCE(service_date, payment_date),1,7) month, {col} grp, COUNT(*) n, SUM(shortfall_cents) cents FROM findings WHERE {' AND '.join(where)} GROUP BY 1,2 ORDER BY 1", tuple(params))


@app.get("/api/payers")
def payers(_: str = Depends(require_auth)):
    return rows("""SELECT payer_group, payer_kind, COUNT(*) findings, SUM(shortfall_cents) shortfall_cents,
                          SUM(CASE WHEN status='open' THEN shortfall_cents ELSE 0 END) open_cents
                   FROM findings WHERE category IN ('downcoding','bundling','denial','underpayment','fees','contract') AND COALESCE(stale,0)=0
                   GROUP BY payer_group, payer_kind ORDER BY shortfall_cents DESC""")


@app.get("/api/providers")
def providers(_: str = Depends(require_auth)):
    return rows("""SELECT f.rendering_provider_npi npi, MAX(COALESCE(p.name, f.rendering_provider_name)) name, MAX(p.tier) tier, COUNT(*) findings, SUM(f.shortfall_cents) shortfall_cents
                   FROM findings f LEFT JOIN providers p ON p.npi=f.rendering_provider_npi
                   WHERE f.category IN ('downcoding','bundling','denial','underpayment') AND f.rendering_provider_npi IS NOT NULL AND COALESCE(f.stale,0)=0
                   GROUP BY f.rendering_provider_npi ORDER BY shortfall_cents DESC""")


@app.get("/api/codes")
def codes_ref(_: str = Depends(require_auth)):
    return {"carc": CARC, "rarc": RARC}


@app.get("/api/volume")
def volume(_: str = Depends(require_auth)):
    return {
        "by_month": rows("""SELECT substr(payment_date,1,7) month, COUNT(DISTINCT transaction_key) remits, SUM(payment_amount_cents) paid_cents
                            FROM transactions WHERE superseded=0 AND payment_date >= '2023-01' GROUP BY 1 ORDER BY 1"""),
        "by_payer_year": rows("""SELECT payer_name, substr(payment_date,1,4) year, COUNT(*) remits, SUM(payment_amount_cents) paid_cents
                                 FROM transactions WHERE superseded=0 GROUP BY 1,2 HAVING paid_cents > 1000000 ORDER BY paid_cents DESC LIMIT 60"""),
        "plb": rows("""SELECT substr(p.payment_date,1,7) month, p.reason_code, COUNT(*) n, SUM(p.amount_cents) cents
                       FROM plb p JOIN transactions t ON t.transaction_key=p.transaction_key WHERE t.superseded=0 AND p.payment_date >= '2023-01' GROUP BY 1,2 ORDER BY 1"""),
    }


# --------------------------------------------------------------------------- appeal packet
@app.get("/api/appeal/{finding_id}")
def appeal(finding_id: str, _: str = Depends(require_auth)):
    from .appeals import build_packet

    detail = finding_detail(finding_id, "ok")
    f = detail["finding"]
    if not f.get("claim_key") and f.get("signal") not in ("TAKEBACK", "FEE_SKIM"):
        # context rows (sequestration, MIPS) and contract-level findings have no letter: there is nothing to reprocess
        raise HTTPException(400, "There is no letter for this kind of finding.")
    if SHOW_PHI and detail.get("claim"):
        detail = dict(detail, claim=one("SELECT * FROM claims WHERE claim_key=?", (detail["finding"]["claim_key"],)) or detail["claim"])
    if f.get("signal") == "FEE_SKIM" and f.get("payer_name"):
        s = _fee_payers(payer_name=f["payer_name"])
        if s["payers"]:
            detail = dict(detail, fee_summary=dict(s["payers"][0], period_from=s["from"], period_to=s["to"]))
    return build_packet(detail, show_phi=SHOW_PHI)


# --------------------------------------------------------------------------- one claim, all of it
CLP02_WORDS = {"1": "Processed as primary", "2": "Processed as secondary", "3": "Processed as tertiary", "4": "Denied",
               "19": "Processed as primary, sent on to another payer", "20": "Processed as secondary, sent on to another payer",
               "21": "Processed as tertiary, sent on to another payer", "22": "Reversal of an earlier payment",
               "23": "Not this payer's claim, sent on to another payer", "25": "Priced only (predetermination)"}
# a remittance list in the payer's order: by payment date, a reversal before its re-payment that day, then the file's order
PAYER_ORDER = "c.payment_date, CASE WHEN c.claim_status_code='22' THEN 0 ELSE 1 END, c.transaction_key, c.claim_seq"
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _bill_ok(bill: str | None) -> bool:
    """A claim ID the view opens: 3 to 40 printable characters (shorter ones are placeholders shared by unrelated claims)."""
    return bool(bill) and 3 <= len(bill) <= 40 and not _CONTROL.search(bill)


def _ref_tokens(ref: str | None) -> list[str]:
    return [t for t in re.split(r"[\s/]+", (ref or "").strip().upper()) if len(t) >= 3]


def _bill_of_reference(ref: str | None) -> str | None:
    """The bill a payer's provider-level adjustment (a takeback) names in its reference: a claim ID, or the payer's claim
    number of one."""
    from .detect import bill_of
    for tok in _ref_tokens(ref):
        r = one("SELECT patient_control_number pcn FROM claims WHERE patient_control_number=? LIMIT 1", (tok,)) \
            or one("SELECT patient_control_number pcn FROM claims WHERE payer_claim_control_number=? LIMIT 1", (tok,))
        if r and r["pcn"]:
            return bill_of(r["pcn"])
    return None


def _who(raw: dict) -> dict:
    """Who a claim is about, as the finding detail says it: initials, or with names shown the name and member number."""
    if not SHOW_PHI:
        return {"patient": _initials(raw.get("patient_last"), raw.get("patient_first"))}
    return {"patient": _name(raw.get("patient_last"), raw.get("patient_first"), raw.get("patient_middle")), "member_id": _member(raw)}


def _bill_ids(bill: str) -> list[str]:
    """Every claim ID (submission) of one EMA bill: the bill ID plus a submission suffix, C017, C024 and so on. Any other
    claim ID is its own bill."""
    from .detect import bill_of
    found = rows("SELECT DISTINCT patient_control_number pcn FROM claims WHERE patient_control_number >= ? AND patient_control_number < ?",
                 (bill, bill + "\uffff"))
    return sorted(r["pcn"] for r in found if r["pcn"] and bill_of(r["pcn"]) == bill)


def _ema_ids(bill: str) -> list[str]:
    """The bill's claim IDs in the EMA exports, answered by a payer or not."""
    from .detect import bill_of
    if not has_table("ema_rows"):
        return []
    found = rows("SELECT DISTINCT claim_id FROM ema_rows WHERE claim_id >= ? AND claim_id < ?", (bill, bill + "\uffff"))
    return sorted(r["claim_id"] for r in found if r["claim_id"] and bill_of(r["claim_id"]) == bill)


def _bill_remits(pcns: list[str]) -> list[dict]:
    if not pcns:
        return []
    q = ",".join("?" * len(pcns))
    return rows(f"""SELECT c.*, t.source_name, t.payment_method, t.payment_amount_cents remit_amount_cents, t.payer_contact_phone,
                           t.payer_contact_url FROM claims c JOIN transactions t ON t.transaction_key = c.transaction_key
                    WHERE c.patient_control_number IN ({q}) AND COALESCE(t.superseded,0)=0 ORDER BY {PAYER_ORDER}""", tuple(pcns))


def _bill_extras(pcns: list[str], payer_numbers: set, line_keys: set) -> tuple[list, list, list]:
    """What names this bill without being on its claims: the takebacks whose reference names one of its claim IDs or the
    payer's claim numbers, the money the payer took back or held over naming it (provider-level adjustments), and the
    routine-rate patterns one of its lines belongs to."""
    ids = {p.upper() for p in pcns} | {x.upper() for x in payer_numbers if x}
    plb, takebacks, seen = [], [], set()
    for x in sorted(ids):
        if len(x) < 3:
            continue
        for r in rows("""SELECT p.id, p.payment_date, p.payer_name, p.reason_code, p.reference, p.amount_cents, p.trace_number, t.source_name
                         FROM plb p JOIN transactions t ON t.transaction_key = p.transaction_key
                         WHERE p.reference LIKE ? AND COALESCE(t.superseded,0)=0""", (f"%{x}%",)):
            if r["id"] not in seen and set(_ref_tokens(r["reference"])) & ids:
                seen.add(r["id"])
                plb.append(r)
        for f in rows("""SELECT finding_id, signal, category, status, shortfall_cents, appeal_deadline, procedure_code, claim_key, line_key, stale,
                                payment_date remit_date, payer_name remit_payer, evidence FROM findings WHERE signal='TAKEBACK' AND evidence LIKE ?""", (f"%{x}%",)):
            ev = json.loads(f.pop("evidence") or "{}")
            if f["finding_id"] not in {t["finding_id"] for t in takebacks} and set(_ref_tokens(ev.get("reference"))) & ids:
                takebacks.append(dict(f, via="takeback"))
    plb.sort(key=lambda r: (r["payment_date"] or "", r["id"]))
    patterns = []
    if line_keys:
        for f in rows(f"""SELECT finding_id, signal, category, status, shortfall_cents, appeal_deadline, procedure_code, stale, evidence
                          FROM findings WHERE signal IN {PATTERN_SQL} AND COALESCE(stale,0)=0"""):
            ev = json.loads(f.pop("evidence") or "{}")
            mine = set(ev.get("line_keys") or []) & line_keys
            if not mine:
                continue
            own = [c for c in (ev.get("claims") or []) if isinstance(c, list) and c and str(c[0]).upper() in {p.upper() for p in pcns}]
            if f["signal"] == "OLD_FEE_SCHEDULE":
                # a repricing request's claim rows: [claim, date, code, allowed, owed, due]
                cents = sum(max((c[4] or 0) - (c[3] or 0), 0) for c in own if len(c) > 5)
            else:
                cents = max((ev.get("usual_cents") or 0) - (ev.get("amount_cents") or 0), 0) * len(mine)
            patterns.append(dict(f, via="pattern", line_keys=sorted(mine), claim_cents=cents,
                                 claim_deadline=(own[0][-1] if own and len(own[0]) > 3 else None)))
    return plb, takebacks, patterns



@app.get("/api/claims/search")
def claims_search(q: Optional[str] = None, request: Request = None, _: str = Depends(require_auth)):
    """Find a claim by its claim ID (any submission of the bill, or the start of the ID), the payer's claim number, or, with
    names shown, the patient's name or member number. The page sends the text in the X-Search header, never in the
    address. One row per bill, the most recently paid first, at most 25."""
    from .detect import bill_of
    text = (_search_text(request, q) or "").strip()[:200]
    if len(text) < 3:
        return {"items": [], "more": False}
    up = re.sub(r"\s+", "", text).upper()
    # a claim ID typed the way it looks elsewhere: dashes, a leading '#', a trailing period or comma do not matter
    bare = re.sub(r"[-#]+", "", up).rstrip(".,;:")
    found = []
    for key in dict.fromkeys(k for k in (up, bare) if len(k) >= 3):
        found += rows("SELECT patient_control_number pcn FROM claims WHERE patient_control_number >= ? AND patient_control_number < ? LIMIT 500",
                      (key, key + "\uffff"))
        found += [{"pcn": b} for b in (bill_of(key), key)]                # any submission of a bill, answered or not
    # the payer's claim number however it is typed: case, spaces, dashes and slashes do not matter; a start of 6 or more
    # characters, or any 8 in a row, also finds it
    norm = re.sub(r"[\s\-/]+", "", text).upper()
    stored = "REPLACE(REPLACE(REPLACE(UPPER(payer_claim_control_number),' ',''),'-',''),'/','')"
    if norm:
        found += rows(f"""SELECT patient_control_number pcn FROM claims WHERE {stored} = ?
                          OR (? >= 6 AND {stored} LIKE ? ) OR (? >= 8 AND INSTR({stored}, ?) > 0) LIMIT 100""",
                      (norm, len(norm), norm + "%", len(norm), norm))
    if SHOW_PHI:
        # the same rules as the findings search: every word in the name as the remittance spells it (also in capitals),
        # or part of a number the payer labelled a member number
        words = [w for w in re.split(r"[\s,]+", text) if w][:6]
        full = "(COALESCE(c.patient_last,'') || ' ' || COALESCE(c.patient_first,'') || ' ' || COALESCE(c.patient_middle,''))"
        parts, params = [], []
        for w in words:
            forms = list(dict.fromkeys([w, w.upper()]))
            parts.append("(" + " OR ".join(f"{full} LIKE ?" for _ in forms) + ")")
            params += [f"%{x}%" for x in forms]
        clause = "(" + " AND ".join(parts) + ")" if parts else "0"
        number = re.sub(r"[\s\-]", "", text)
        if re.search(r"\d", number) and len(number) >= 4:
            bare = "REPLACE(REPLACE(c.{k}_id,'-',''),' ','')"
            clause += (f" OR ({bare.format(k='patient')} LIKE ? AND {_MEMBER_SQL.format(a='c', k='patient')})"
                       f" OR ({bare.format(k='insured')} LIKE ? AND {_MEMBER_SQL.format(a='c', k='insured')})")
            params += [f"%{number}%"] * 2
        found += rows(f"SELECT c.patient_control_number pcn FROM claims c WHERE {clause} LIMIT 500", tuple(params))
    # and claims EMA holds that no payer has answered yet
    if has_table("ema_rows"):
        for key in dict.fromkeys(k for k in (up, bare) if len(k) >= 3):
            found += rows("SELECT DISTINCT claim_id pcn FROM ema_rows WHERE claim_id >= ? AND claim_id < ? LIMIT 200", (key, key + "\uffff"))
    bills: list[str] = []
    for r in found:
        b = bill_of(r["pcn"]) if r["pcn"] else None
        if b and _bill_ok(b) and b not in bills:
            bills.append(b)
    items = []
    for b in bills:
        if len(items) >= 25:
            break
        pcns = _bill_ids(b)
        rem = _bill_remits(pcns)
        ema_ids = _ema_ids(b)
        if not rem and not ema_ids:
            continue
        open_ids = set()
        ids = sorted(set(pcns) | set(ema_ids))      # a submission no payer has answered has items too (Chase, Fix and resubmit)
        if ids:
            q2 = ",".join("?" * len(ids))
            open_ids = {r["finding_id"] for r in rows(f"""SELECT finding_id FROM findings WHERE patient_control_number IN ({q2})
                                                           AND status IN ('open','in_progress') AND COALESCE(stale,0)=0""", tuple(ids))}
        if pcns:
            keys = [x["claim_key"] for x in rem]
            lks = {r["line_key"] for r in rows(f"SELECT line_key FROM lines WHERE claim_key IN ({','.join('?' * len(keys))})", tuple(keys))} if keys else set()
            _plb, tbs, pats = _bill_extras(pcns, {x["payer_claim_control_number"] for x in rem if x.get("payer_claim_control_number")}, lks)
            open_ids |= {x["finding_id"] for x in tbs + pats if x["status"] in ("open", "in_progress")}
        who = _who(rem[-1]) if rem else _who_ema(_ema_first(ema_ids))
        items.append(dict(who, bill=b, claim_ids=sorted(set(pcns) | set(ema_ids)), payers=sorted({x["payer_name"] for x in rem if x["payer_name"]}), remits=len(rem),
                          first_payment=rem[0]["payment_date"] if rem else None, last_payment=rem[-1]["payment_date"] if rem else None,
                          charge_cents=max(((x["charge_cents"] or 0) for x in rem), default=0), paid_cents=sum((x["paid_cents"] or 0) for x in rem),
                          open_findings=len(open_ids), exact=(b == up)))
    items.sort(key=lambda x: (not x["exact"], -(int((x["last_payment"] or "0000-00-00").replace("-", "")))))
    out = {"items": items, "more": len(bills) > len(items) and len(items) >= 25}
    if not items and runtime.serverless():
        # this host's copy holds the claims of bills with items only: "nothing matches" would be a guess
        site = (os.environ.get("REMITWATCH_SERVER_SITE") or "").strip()
        out["partial"] = ("The copy of the data on this site holds only the claims that have items, so a paid claim with no item "
                          "does not show here. Look it up " + (f"on the practice's server site ({site}) or " if site else "") + "in EMA.")
    return out


@app.get("/api/claims/view")
def claim_view(request: Request, _: str = Depends(require_auth)):
    """Everything about one bill: every submission (claim ID) and every remittance for them in the payer's order, each with
    every line, adjustment and remark in words; the items found on it (its own, the takebacks and routine-rate patterns
    that name it); the money taken back on it; the EMA rows, also of submissions no payer has answered yet. The claim ID
    comes in the X-Claim header, never in the address. The same allowlist of claim columns as the finding detail, and the
    patient as it shows them."""
    from urllib.parse import unquote
    from .detect import bill_of
    raw = unquote(request.headers.get("x-claim") or "").strip().upper()
    bill = bill_of(raw) if raw else ""
    if not _bill_ok(bill):
        raise HTTPException(400, "That is not a claim ID.")
    pcns = _bill_ids(bill)
    rem = _bill_remits(pcns)
    ema_ids = _ema_ids(bill)
    if not rem and not ema_ids:
        raise HTTPException(404, "No remittance in the files and no EMA row has that claim ID.")
    keys = [r["claim_key"] for r in rem] or [""]
    qk = ",".join("?" * len(keys))
    lines = rows(f"""SELECT claim_key, line_key, line_seq, procedure_code, modifiers, original_procedure_code, original_modifiers, original_units,
                            charge_cents, allowed_cents, paid_cents, paid_units, service_date, line_control_number FROM lines
                     WHERE claim_key IN ({qk}) ORDER BY claim_key, line_seq""", tuple(keys))
    adjs = rows(f"SELECT claim_key, line_key, level, group_code, reason_code, amount_cents, quantity FROM adjustments WHERE claim_key IN ({qk}) ORDER BY claim_key, line_key, cas_index", tuple(keys))
    rems = rows(f"SELECT claim_key, line_key, level, qualifier, code FROM remarks WHERE claim_key IN ({qk})", tuple(keys))
    for a in adjs:
        a["text"] = CARC.get(a["reason_code"], "")
    for r in rems:
        r["text"] = RARC.get(r["code"], "")
    remits = []
    for r in rem:
        out = {k: r.get(k) for k in CLAIM_PUBLIC_COLS}
        out.update(status_words=CLP02_WORDS.get(str(r.get("claim_status_code") or ""), f"Status {r.get('claim_status_code') or '?'}"),
                   source_name=r.get("source_name"), payment_method=r.get("payment_method"), sent_on_to=r.get("crossover_carrier_name"),
                   payer_phone=r.get("payer_contact_phone"), payer_url=r.get("payer_contact_url"),
                   adjustments=[a for a in adjs if a["claim_key"] == r["claim_key"] and not a["line_key"]],
                   remarks=[x for x in rems if x["claim_key"] == r["claim_key"] and not x["line_key"]],
                   lines=[dict(l, adjustments=[a for a in adjs if a["line_key"] == l["line_key"]], remarks=[x for x in rems if x["line_key"] == l["line_key"]])
                          for l in lines if l["claim_key"] == r["claim_key"]])
        remits.append(out)
    findings = []
    ids = sorted(set(pcns) | set(ema_ids))      # with the submissions no payer has answered: their items are on the bill too
    if ids:
        qp = ",".join("?" * len(ids))
        have = _columns()
        state_cols = (", " + ", ".join(_state_cols(have, "f."))) if {"work_state", "follow_up_on", "state_basis"} <= have else ""
        findings = [dict(f, via="line") for f in rows(f"""SELECT f.finding_id, f.signal, f.category, f.status, f.shortfall_cents, f.appeal_deadline, f.procedure_code,
                                                                f.claim_key, f.line_key, f.stale, c.payment_date remit_date, c.payer_name remit_payer{state_cols}
                                                         FROM findings f LEFT JOIN claims c ON c.claim_key = f.claim_key
                                                         WHERE f.patient_control_number IN ({qp}) ORDER BY f.payment_date, f.finding_id""", tuple(ids))]
    plb, takebacks, patterns = _bill_extras(pcns, {r["payer_claim_control_number"] for r in rem if r.get("payer_claim_control_number")},
                                            {l["line_key"] for l in lines})
    qe = ",".join("?" * len(ids))
    ema = rows(f"""SELECT claim_id, family, source_name, service_date, cpt, modifiers, units, charges_cents, payments_cents, adjustments_cents, allowed_cents,
                          claim_status, activity_type, carc, rarc, adjustment_status, denial_category, posted_date, claim_submission_date, transaction_date
                   FROM ema_rows WHERE claim_id IN ({qe}) ORDER BY claim_id, family, service_date, row_index""", tuple(ids)) if has_table("ema_rows") and ids else []
    who = _who(rem[-1]) if rem else _who_ema(_ema_first(ema_ids))     # a bill no payer has answered yet: EMA says who
    # the claim's story (every remittance and EMA posting on its lines, the balance) and EMA's block for the whole bill
    newest = (pcns or ema_ids or [bill])[-1]
    told = _explained({"patient_control_number": newest}, claim_key=newest, bill=bill)
    return dict(who, bill=bill, claim_ids=ids, answered=pcns, remits=remits, findings=findings + takebacks + patterns, taken_back=plb, ema=ema,
                story=told.get("claim_story"), ema_block=told.get("ema_block"),
                **({"story_error": told["explain_error"]} if told.get("explain_error") else {}))


# --------------------------------------------------------------------------- payment fees: one request per payer
def _fee_vendors(evidence: list) -> list[str]:
    out = set()
    for ev in evidence:
        try:
            refs = ((json.loads(ev) if isinstance(ev, str) else ev) or {}).get("references") or {}
        except (TypeError, ValueError, AttributeError):
            refs = {}
        if any("ZELIS" in str(k).upper() for k in refs):
            out.add("Zelis")
    return sorted(out)


def _fee_payers(statuses: tuple = ("open", "in_progress"), payer_name: str | None = None) -> dict:
    """Payment fees by payer as the remittance names it (the one a request goes to): the last 12 full months before the
    month the remittances reach for most payers (the home screen's months: one stray newer file does not move them),
    everything since the first fee, the months with a fee, the vendors named, and the ids of its fee items (one per payer
    and month), so asking can mark them all at once."""
    from .insights import feed_day, fee_months, newest_payment
    conn = db()
    try:
        newest = feed_day(conn, _day(0)) or newest_payment(conn)
    finally:
        conn.close()
    lo, hi = fee_months(newest)
    where = f"signal='FEE_SKIM' AND COALESCE(stale,0)=0 AND status IN ({','.join('?' * len(statuses))})"
    params: tuple = tuple(statuses)
    if payer_name is not None:
        where += " AND COALESCE(payer_name,'')=?"
        params += (payer_name,)
    by: dict[str, dict] = {}
    for r in rows(f"""SELECT finding_id, COALESCE(payer_name,'') pn, payer_group, month, COALESCE(shortfall_cents,0) c, evidence,
                             COALESCE(appealed_at, status_changed_at) asked, status FROM findings WHERE {where} ORDER BY month""", params):
        p = by.setdefault(r["pn"], {"payer_name": r["pn"], "payer_group": r["payer_group"], "cents": 0, "cents12": 0, "months": set(),
                                    "ids": [], "evidence": [], "asked": None, "seen": {}})
        p["cents"] += r["c"]
        p["ids"].append(r["finding_id"])
        p["seen"][r["finding_id"]] = r["status"]         # what the page shows each one as: a change sent back with it (seen_status)
        p["evidence"].append(r["evidence"])
        if r["month"]:
            p["months"].add(r["month"])
            if lo and lo <= r["month"] <= hi:
                p["cents12"] += r["c"]
        if r["asked"] and (p["asked"] is None or r["asked"] > p["asked"]):
            p["asked"] = r["asked"]
    payers = []
    for p in by.values():
        months = sorted(p.pop("months"))
        vendors = _fee_vendors(p.pop("evidence"))
        payers.append(dict(p, months=len(months), first_month=months[0] if months else None, last_month=months[-1] if months else None,
                           vendors=vendors))
    payers.sort(key=lambda p: (-p["cents12"], -p["cents"], p["payer_name"]))
    return {"from": lo, "to": hi, "payers": payers, "cents12": sum(p["cents12"] for p in payers), "payers12": sum(1 for p in payers if p["cents12"]),
            "cents": sum(p["cents"] for p in payers), "since": min((p["first_month"] for p in payers if p["first_month"]), default=None)}


@app.get("/api/fees/payers")
def fee_payers(_: str = Depends(require_auth)):
    """Payment fees still to stop, one row per payer (largest in the last 12 full months first); the payers already asked,
    waiting for the change; and what the vendors took in all (every fee month but those let go), which asking does not
    change. About payers and the practice's payments: no patient or claim."""
    out = _fee_payers()
    asked = _fee_payers(("appealed",))
    every = _fee_payers(("open", "in_progress", "appealed", "resolved", "lost"))
    out["asked"] = asked["payers"]
    out["history"] = {k: every[k] for k in ("cents12", "payers12", "cents", "since")}
    return out


@app.get("/api/fees/letter")
def fee_letter(payer: str = Query(..., max_length=200), _: str = Depends(require_auth)):
    """The request to one payer for standard EFT with no fee, with that payer's own fee totals."""
    from .appeals import fee_request_letter
    s = _fee_payers(payer_name=payer)
    if not s["payers"]:
        s = _fee_payers(("appealed",), payer_name=payer)            # asked already: the same letter again
    if not s["payers"]:
        raise HTTPException(404, "No payment fees to ask about for this payer.")
    return fee_request_letter(dict(s["payers"][0], period_from=s["from"], period_to=s["to"]))


# --------------------------------------------------------------------------- breakdowns: who and what
QUARTER = "(substr({m},1,4) || '-Q' || ((CAST(substr({m},6,2) AS INTEGER)+2)/3))"
# dimension -> (expression on findings, expression on the exposure table or None when there is no denominator for it)
DIMS: dict[str, tuple[str, Optional[str]]] = {
    "provider": ("COALESCE(rendering_provider_npi,'')", "npi"),
    "payer": ("payer_group", "payer_group"),
    "payer_kind": ("payer_kind", "payer_kind"),
    "plan": ("COALESCE(plan,'')", "plan"),
    "billed_code": ("billed_code", "code"),
    "paid_code": ("procedure_code", None),
    "code_pair": ("code_pair", None),
    "level_drop": ("level_drop", None),
    "signal": ("signal", None),
    "category": ("category", None),
    "month": ("month", "month"),
    "quarter": (QUARTER.format(m="month"), QUARTER.format(m="month")),
    "year": ("substr(month,1,4)", "substr(month,1,4)"),
    "modifier": ("modifier_class", None),
    "pos": ("pos_class", "pos_class"),
    "tier": ("provider_tier", "tier"),
    "location": ("location", None),
    "diagnosis": ("diagnosis", None),
    "carc": ("carc", None),
    "status": ("status", None),
    "code_category": ("code_category", None),
    "action": ("actionable", None),
    "handling": ("ema_handling", None),
}
EM_LIST = EM_SQL
_DOWN = ",".join(f"'{x}'" for x in DOWNCODE_SIGNALS)
FAMILY: dict[str, tuple[str, str]] = {      # family -> (filter on findings, filter on exposure)
    # visit downcoding is counted against visit lines, so only findings on a billed visit code belong to it
    "downcoding": (f"signal IN ({_DOWN}) AND billed_code IN ({EM_LIST})", f"code IN ({EM_LIST})"),
    "code_change": (f"(signal='CODE_CHANGED' OR (signal IN ({_DOWN}) AND (billed_code IS NULL OR billed_code NOT IN ({EM_LIST}))))", f"code NOT IN ({EM_LIST})"),
    "bundling": ("category='bundling'", "1=1"),
    "denial": ("category='denial'", "1=1"),
    "underpayment": ("category='underpayment'", "1=1"),
    "all": ("category IN ('downcoding','bundling','denial','underpayment')", "1=1"),
}


def _provider_names() -> dict[str, str]:
    return {r["npi"]: r["name"] for r in rows("SELECT npi, name FROM providers")} if has_table("providers") else {}


@app.get("/api/breakdown")
def breakdown(dim: str = "payer", dim2: Optional[str] = None, family: str = "downcoding", status: str = "active",
              payer: Optional[str] = None, provider: Optional[str] = None, plan: Optional[str] = None, code: Optional[str] = None,
              year: Optional[str] = None, month_from: Optional[str] = None, month_to: Optional[str] = None, signal: Optional[str] = None,
              limit: int = Query(400, le=2000), request: Request = None, _: str = Depends(require_auth)):
    """Findings grouped by one or two dimensions, with the number of billed lines behind them where the store has it,
    so every count comes with a rate (findings per 100 lines). Lines billed by clinician are how busy each clinician
    is, so when an owner's password is set they are left out unless it came with the request."""
    if dim not in DIMS or (dim2 and dim2 not in DIMS) or family not in FAMILY or dim == dim2:
        raise HTTPException(400, "unknown dimension or family")
    fwhere, ewhere = [FAMILY[family][0], "month IS NOT NULL", "COALESCE(stale,0)=0"], [FAMILY[family][1]]
    fparams: list[Any] = []
    eparams: list[Any] = []
    if signal:
        sigs = [x for x in signal.split(",") if x]
        fwhere.append("signal IN (%s)" % ",".join("?" * len(sigs))); fparams += sigs
    if status == "active":
        fwhere.append("status != 'dismissed'")
    elif status != "all":
        fwhere.append("status=?"); fparams.append(status)
    for val, fcol, ecol in ((payer, "payer_group", "payer_group"), (provider, "rendering_provider_npi", "npi"), (plan, "COALESCE(plan,'')", "plan"),
                            (code, "billed_code", "code"), (year, "substr(month,1,4)", "substr(month,1,4)")):
        if val is not None and val != "":
            fwhere.append(f"{fcol}=?"); fparams.append(val)
            ewhere.append(f"{ecol}=?"); eparams.append(val)
    if month_from:
        fwhere.append("month>=?"); fparams.append(month_from); ewhere.append("month>=?"); eparams.append(month_from)
    if month_to:
        fwhere.append("month<=?"); fparams.append(month_to); ewhere.append("month<=?"); eparams.append(month_to)
    dims = [dim] + ([dim2] if dim2 else [])
    fcols = ", ".join(f"{DIMS[d][0]} k{i+1}" for i, d in enumerate(dims))
    grp = ", ".join(str(i + 1) for i in range(len(dims)))
    data = rows(f"""SELECT {fcols}, COUNT(*) n, COALESCE(SUM(shortfall_cents),0) cents,
                           SUM(status IN ('open','in_progress','appealed')) open_n,
                           COALESCE(SUM(CASE WHEN status IN ('open','in_progress','appealed') THEN shortfall_cents END),0) open_cents,
                           SUM(status='resolved') resolved_n, COALESCE(SUM(CASE WHEN status='resolved' THEN recovered_cents END),0) recovered_cents,
                           SUM(status='lost') lost_n, SUM(status IN ('open','in_progress') AND appeal_deadline >= ? AND signal NOT IN {PATTERN_SQL}) live_n,
                           COALESCE(SUM(CASE WHEN status IN ('open','in_progress') AND appeal_deadline >= ? AND signal NOT IN {PATTERN_SQL} THEN shortfall_cents END),0) live_cents
                    FROM findings WHERE {' AND '.join(fwhere)} GROUP BY {grp} ORDER BY cents DESC LIMIT ?""", (_day(0), _day(0)) + tuple(fparams) + (limit,))
    # denominators: for both dimensions when the exposure table carries them, otherwise for the first only
    exp_dims = [d for d in dims if DIMS[d][1]]
    exposure: dict[tuple, dict] = {}
    exposure_level = None
    if has_table("exposure") and exp_dims and (len(exp_dims) == len(dims) or exp_dims[0] == dim):
        use = exp_dims if len(exp_dims) == len(dims) else [dim]
        ecols = ", ".join(f"{DIMS[d][1]} k{i+1}" for i, d in enumerate(use))
        egrp = ", ".join(str(i + 1) for i in range(len(use)))
        for r in rows(f"SELECT {ecols}, SUM(lines) lines, SUM(charge_cents) charge_cents, SUM(allowed_cents) allowed_cents FROM exposure WHERE {' AND '.join(ewhere)} GROUP BY {egrp}", tuple(eparams)):
            exposure[tuple(r[f"k{i+1}"] for i in range(len(use)))] = r
        exposure_level = len(use)
    # a routine-rate finding is still appealable for its claims whose own windows are open
    live_by = defaultdict(int)
    for fid, _g, _c, d, _n, cents in _windows(("open", "in_progress")):
        if d and d >= _day(0):
            live_by[fid] += cents
    if live_by:
        at = {tuple(r[f"k{i+1}"] for i in range(len(dims))): r for r in data}
        for pr in rows(f"SELECT finding_id, {fcols} FROM findings WHERE {' AND '.join(fwhere)} AND signal IN {PATTERN_SQL} AND status IN ('open','in_progress')",
                       tuple(fparams)):
            r = at.get(tuple(pr[f"k{i+1}"] for i in range(len(dims))))
            if r is not None and live_by.get(pr["finding_id"]):
                r["live_cents"] = (r["live_cents"] or 0) + live_by[pr["finding_id"]]
                r["live_n"] = (r["live_n"] or 0) + 1
    names = _provider_names()
    for r in data:
        e = exposure.get(tuple(r[f"k{i+1}"] for i in range(exposure_level))) if exposure_level else None
        r["lines"] = e["lines"] if e else None
        r["rate"] = round(r["n"] / e["lines"], 5) if e and e["lines"] else None
        for i, d in enumerate(dims):
            if d == "provider":
                r[f"label{i+1}"] = names.get(r[f"k{i+1}"]) or r[f"k{i+1}"] or "(no rendering provider on the remit)"
    totals = one(f"SELECT COUNT(*) n, COALESCE(SUM(shortfall_cents),0) cents FROM findings WHERE {' AND '.join(fwhere)}", tuple(fparams))
    total_lines = one(f"SELECT SUM(lines) lines FROM exposure WHERE {' AND '.join(ewhere)}", tuple(eparams)) if has_table("exposure") else {"lines": None}
    by_clinician = "provider" in dims or (provider is not None and provider != "")
    locked = False
    if owner_state(request) != "open":
        if by_clinician:    # the rate goes too: the count divided by the rate gives the lines back
            locked = True
            for r in data:
                r["lines"] = r["rate"] = None
            total_lines = {"lines": None}
        elif has_table("exposure"):
            # grouped by something else (provider type, payer, code, month), a cell can still be one clinician's work
            if exposure_level:
                solo = _solo_cells(ecols, egrp, " AND ".join(ewhere), tuple(eparams))
                for r in data:
                    key = tuple(r[f"k{i+1}"] for i in range(exposure_level))
                    if r["lines"] and solo.get(key, 0) >= SOLO_SHARE * r["lines"]:
                        r["lines"] = r["rate"] = None
                        locked = True
            whole = (total_lines or {}).get("lines")
            if whole and max(_solo_cells("1 k1", "1", " AND ".join(ewhere), tuple(eparams)).values(), default=0) >= SOLO_SHARE * whole:
                total_lines = {"lines": None}
                locked = True
    return {"dim": dim, "dim2": dim2, "family": family, "rows": data, "total": totals, "total_lines": (total_lines or {}).get("lines"),
            "rates_locked": locked,
            "rate_scope": {"downcoding": "office visit lines billed", "code_change": "procedure lines billed"}.get(family, "service lines billed"),
            "denominator_for": exposure_level, "dims": sorted(DIMS)}


# --------------------------------------------------------------------------- one clinician must not show through a total
SOLO_SHARE = 0.75       # a count that is three quarters one clinician's lines says how busy that clinician is (the usual dominance rule for published tables)


def _clinicians() -> list[str]:
    """NPIs that are people. From the provider tables when they are built; else every NPI the exposure table names."""
    if has_table("provider_roster"):
        got = [r["provider_id"] for r in rows("SELECT provider_id FROM provider_roster WHERE kind='person'")]
        if got:
            return got
    return [r["npi"] for r in rows("SELECT DISTINCT npi FROM exposure WHERE COALESCE(npi,'') != ''")] if has_table("exposure") else []


def _solo_cells(cols: str, group: str, where: str, params: tuple) -> dict[tuple, int]:
    """For each cell of an exposure breakdown, the most lines any one clinician has in it."""
    people = _clinicians()
    if not people:
        return {}
    marks = ",".join("?" * len(people))
    n = len(group.split(","))
    keys = ", ".join(f"k{i + 1}" for i in range(n))
    out: dict[tuple, int] = {}
    for r in rows(f"""SELECT {keys}, MAX(l) top FROM (SELECT {cols}, npi, SUM(lines) l FROM exposure
                      WHERE {where} AND npi IN ({marks}) GROUP BY {group}, npi) GROUP BY {keys}""", params + tuple(people)):
        out[tuple(r[f"k{i + 1}"] for i in range(n))] = r["top"] or 0
    return out


def _solo_rate_groups() -> set[tuple]:
    """(payer, provider type, year) groups whose lines are nearly all one clinician's: the count behind a usual rate
    for such a group is that clinician's volume."""
    if not has_table("exposure"):
        return set()
    people = set(_clinicians())
    total: dict[tuple, int] = {}
    top: dict[tuple, int] = {}
    for r in rows("SELECT payer_group, tier, substr(month,1,4) y, COALESCE(npi,'') npi, SUM(lines) l FROM exposure GROUP BY 1, 2, 3, 4"):
        k = (r["payer_group"], r["tier"], r["y"])
        total[k] = total.get(k, 0) + (r["l"] or 0)
        if r["npi"] in people:
            top[k] = max(top.get(k, 0), r["l"] or 0)
    return {k for k, n in total.items() if n and top.get(k, 0) >= SOLO_SHARE * n}


# --------------------------------------------------------------------------- providers: what each clinician is doing
MONTH_PATTERN = r"^[0-9]{4}-(0[1-9]|1[0-2])$"
# The readers pull a few thousand rows into Python. Several at once crawl, because every row hands the interpreter lock
# from thread to thread (six together took seconds each on the real archive); one after another they take a few
# hundredths of a second each. The page asks for three at a time, so they queue here.
_ACTIVITY_LOCK = threading.Lock()


def _activity(fn, *args):
    """Run one of the readers in remitwatch.activity on the dashboard's database; say so plainly when the tables are not built yet."""
    from . import activity
    conn = db()
    try:
        state = activity.status(conn) if has_table("meta") else {"ready": False, "built_at": None, "error": None}
        if not state["ready"] or not has_table("provider_days"):
            return {"ready": False, "error": state.get("error"),
                    "message": "The provider figures are built by the next analysis run. Press Sync and analyse now on the Data tab."}
        with _ACTIVITY_LOCK:
            out = fn(conn, *args)
        if out is None:
            raise HTTPException(404, "No such clinician")
        out["ready"] = True
        out["built_at"] = state["built_at"]
        return out
    finally:
        conn.close()


def _written_off_summary() -> dict:
    """Open items on the claim pages, inside their filing window, whose disputed money the biller wrote off in EMA: what
    "Work what EMA wrote off" opens (the Worklist with handling=written_off), with the payers that hold the most."""
    if not _has_written_off():
        return {"n": 0, "cents": 0, "written_off_cents": 0, "payers": []}
    w, params = _filters(None, CLAIM_CATEGORIES, None, None, TODO_STATUSES, None, None, None, None, None, "valid", "written_off", None, None, None, True)
    tot = one(f"SELECT COUNT(*) n, COALESCE(SUM(shortfall_cents),0) cents, COALESCE(SUM(written_off_cents),0) w, MIN(written_off_on) since FROM findings{w}", tuple(params)) or {}
    payers = rows(f"""SELECT payer_group, COUNT(*) n, COALESCE(SUM(shortfall_cents),0) cents FROM findings{w}
                      GROUP BY payer_group ORDER BY cents DESC, n DESC LIMIT 3""", tuple(params))
    return {"n": tot.get("n") or 0, "cents": tot.get("cents") or 0, "written_off_cents": tot.get("w") or 0, "since": tot.get("since"), "payers": payers}


def _no_answer_summary() -> dict:
    """Claims EMA shows sent and unpaid that the payer never answered (Chase the payer), as the Worklist's kind opens them,
    and those EMA calls rejected before they reached the payer (under Fix and resubmit)."""
    out = {}
    for name, kind, signal in (("chase", "chase", "NO_ANSWER"), ("rejected", None, "REJECTED_PRE_PAYER")):
        w, params = _filters(signal if kind is None else None, CLAIM_CATEGORIES, None, None, TODO_STATUSES, None, None, None, None, None, "valid",
                             None, None, None, kind, True)
        r = one(f"SELECT COUNT(*) n, COALESCE(SUM(shortfall_cents),0) cents, MIN(appeal_deadline) first FROM findings{w}", tuple(params)) or {}
        out[name] = {"n": r.get("n") or 0, "cents": r.get("cents") or 0, "first_deadline": r.get("first")}
    return out


@app.get("/api/brief")
def brief(_: str = Depends(require_auth)):
    """Everything the home screen shows in one call: the open work still inside its filing window by the kind of work it
    takes and what falls due soon, what came back, the bigger levers that are not claim by claim (payer rates against
    Medicare, fees below what payers allow, payment fees, denial causes that keep recurring) and what in the data is
    hiding money. About the practice as a whole, so it is open to every login."""
    from . import insights
    conn = db()
    try:
        out = insights.brief(conn)
    finally:
        conn.close()
    if out.get("ready"):
        out["written_off"] = _written_off_summary()
        out["no_answer"] = _no_answer_summary()
        fu = out.get("follow_ups") or {}
        behind = _data_behind_days()
        if fu.get("n") and behind is not None and behind > STALE_APPEAL_DAYS:
            # the same measure the fax hold uses: the items stay listed, and the person checks before asking the payer again
            fu["behind_days"] = behind
            from .detect import gap_words
            gap = _feed_gap()   # weeks missing, then newer files (misuse check CH-6): named, not an age
            fu["note"] = (f"{gap_words(gap)} Some of these may have been paid in those weeks. " if gap else
                          f"The newest remittance RemitWatch has is {behind} days old, so some of these may have been paid since. ") \
                + "Check each one in EMA or on the payer's portal before you follow up."
        # the red number on each category tab: the work that closes within 7 days, counted as the list a click on it opens
        # (desk.js badgeOpen: to do and started, closing within 7 days, the work now and follow-ups due among them; never
        # an item that only waits on the payer)
        token = _WINDOWS_MEMO.set({})
        try:
            out["badges"] = {c: _bucket(c, None, TODO_STATUSES, "week", state="todo") for c in BADGE_CATEGORIES}
            _todo_soon(out.get("work") or {})
        finally:
            _WINDOWS_MEMO.reset(token)
    return out


def _todo_soon(work: dict) -> None:
    """Today's step 1 (what closes within 7 days, or within 30) and its chips by kind of work, counted as the list its
    Start button opens: the to-do statuses closing then, the work now and the follow-ups due among them (desk.js
    deskFilter), as the red numbers on the tabs are. insights.brief counts them by status and window alone, which also
    counted an item that only waits on the payer, and the list did not show it."""
    from .insights import WORK_TYPES
    meta = {t["key"]: t for t in WORK_TYPES}
    total = work.setdefault("total", {})
    types = {t["key"]: t for t in work.get("types") or []}
    for dl, key in (("week", "due7"), ("month", "due30")):
        got = _desk_kinds(CLAIM_CATEGORIES, None, TODO_STATUSES, dl, state="todo")
        total[key + "_n"], total[key + "_cents"] = got["n"], got["cents"]
        by = {k["key"]: k for k in got["kinds"]}
        for k in by:
            if k not in types:          # never expected (7 and 30 days are inside the window), but a chip is never lost
                types[k] = dict(meta.get(k, {"key": k}), n=0, cents=0, due7_n=0, due7_cents=0, due30_n=0, due30_cents=0, working_n=0)
                work.setdefault("types", []).append(types[k])
        for k, t in types.items():
            t[key + "_n"], t[key + "_cents"] = (by.get(k) or {}).get("n", 0), (by.get(k) or {}).get("cents", 0)


@app.get("/api/worklist/counts")
def worklist_counts(category: Optional[str] = None, payer: Optional[str] = None, status: str = "open", deadline: Optional[str] = None,
                    q: Optional[str] = None, signal: Optional[str] = None, live: int = Query(0, ge=0, le=1), paid: int = Query(0, ge=0, le=3650),
                    min: int = Query(0, ge=0, le=10**6), kind: Optional[str] = Query(None, max_length=20), year: Optional[str] = Query(None, max_length=4),
                    handling: Optional[str] = Query(None, max_length=40), ema: Optional[str] = Query(None, max_length=40),
                    state: Optional[str] = Query(None, max_length=120), work_state: Optional[str] = Query(None, max_length=120),
                    ema_outcome: Optional[str] = Query(None, max_length=200), source: Optional[str] = Query(None, max_length=20),
                    request: Request = None, _: str = Depends(require_auth)):
    """Items and dollars for each kind of work under the worklist's other filters, so each tab counts what a click on it
    opens, and for each reason (signal), so a reason chip counts what it opens too. Counts only: no patient, claim or date."""
    from .insights import WORK_TYPES, work_type
    w, params = _filters(signal, category, payer, year, status, None, None, _search_text(request, q), None, None, deadline, handling or None, None,
                         None, kind, bool(live), paid, min, ema=ema, state=state or work_state, ema_outcome=ema_outcome, source=source)
    out = {t["key"]: {"key": t["key"], "n": 0, "cents": 0} for t in WORK_TYPES}
    by_signal: dict[str, dict] = {}
    for r in rows(f"SELECT signal, actionable, carc, COUNT(*) n, COALESCE(SUM(shortfall_cents),0) cents FROM findings{w} GROUP BY 1,2,3", tuple(params)):
        k = out.setdefault(work_type(r["signal"], r["actionable"], r["carc"]), {"key": work_type(r["signal"], r["actionable"], r["carc"]), "n": 0, "cents": 0})
        k["n"] += r["n"]
        k["cents"] += r["cents"]
        sg = by_signal.setdefault(r["signal"] or "", {"signal": r["signal"], "n": 0, "cents": 0})
        sg["n"] += r["n"]
        sg["cents"] += r["cents"]
    delta, each = _pattern_cents(w, params, deadline)
    if each and deadline in DEADLINE_RANGES:
        # a routine-rate review and a repricing request count the cents of their claims in range, each in its own reason
        for r in rows(f"SELECT finding_id, signal, actionable, carc, COALESCE(shortfall_cents,0) c FROM findings{w}{' AND' if w else ' WHERE'} "
                      f"signal IN {PATTERN_SQL}", tuple(params)):
            if r["finding_id"] in each:
                d = each[r["finding_id"]][0] - r["c"]
                out[work_type(r["signal"], r["actionable"], r["carc"])]["cents"] += d
                if r["signal"] in by_signal:
                    by_signal[r["signal"]]["cents"] += d
    kinds = list(out.values())
    return {"kinds": kinds, "n": sum(k["n"] for k in kinds), "cents": sum(k["cents"] for k in kinds),
            "signals": sorted(by_signal.values(), key=lambda x: -x["cents"])}


# ---------------------------------------------------------------- the category pages
TODO_STATUSES = "open,in_progress"
# A category page's tiles: each is exactly what the list holds under that status and filing date
DESK_TILES = (
    ("week", TODO_STATUSES, "week"),            # closes within 7 days
    ("month", TODO_STATUSES, "month"),          # closes within 30 days
    ("valid", TODO_STATUSES, "valid"),          # the filing window is open: you can still get it back
    ("decide", TODO_STATUSES, "decide"),        # no filing date, paid in the last 120 days: a person decides
    ("older", TODO_STATUSES, "older"),          # no filing date, paid before that
    ("expired", TODO_STATUSES, "expired"),      # the window closed
    ("all", TODO_STATUSES, None),               # every item to do, any window: the earlier page header
    ("sent", "appealed", None),                 # sent to the payer, waiting
    ("followup", "appealed", "followup"),       # sent over 30 days ago and still unpaid
    ("missed", "open", "missed"),               # windows that closed in the last 30 days with nobody on it
)
BADGE_CATEGORIES = ("denial", "bundling", "downcoding", "underpayment")
CLAIM_CATEGORIES = ",".join(BADGE_CATEGORIES)          # the four claim pages together: the Worklist


# A category page's state tiles (amendment 13.1): what to work now (with the follow-ups whose day has come), the part of it
# that closes within 7 days, what waits on the payer, and the four kinds of item that need no work, each findable
STATE_TILES = (
    ("todo", "todo", None),                     # work now and follow-ups due: the list a page opens with
    ("todo_week", "todo", "week"),              # of those, the ones whose filing window closes within 7 days
    ("todo_decide", "todo", "decide"),          # of those, the ones with no filing date: a person says what they are
    ("waiting", "waiting", None),               # a person or EMA shows the work was done: waiting on the payer
    ("followup_due", "followup_due", None),     # waiting, and its follow-up day has come
    ("done", "done", None), ("accepted", "accepted", None), ("st_expired", "expired", None), ("cant_check", "cant_check", None),
)


def _bucket(category, payer, status, deadline, min_dollars=0, signal=None, kind=None, year=None, handling=None, ema=None, state=None) -> dict:
    """Items and dollars under exactly the filter /api/findings uses for the same choices (live findings only): the
    count line of the list a click opens. A routine-rate pattern counts its claims in range, as the list does."""
    w, params = _filters(signal, category, payer, year, status, None, None, None, None, None, deadline, handling, None, None, kind, True, 0, min_dollars,
                         ema=ema, state=state)
    r = one(f"SELECT COUNT(*) n, COALESCE(SUM(shortfall_cents),0) cents FROM findings{w}", tuple(params))
    delta, _each = _pattern_cents(w, params, deadline)
    return {"n": r["n"], "cents": (r["cents"] or 0) + (delta if r["n"] else 0)}


def _desk_kinds(category, payer, status, deadline, min_dollars=0, signal=None, paid_days=0, q=None, year=None, handling=None, ema=None,
                state=None) -> dict:
    """The kinds of work under one filter, largest dollars first, each with its three biggest payer piles; a pile's count
    and dollars are what the list shows for that kind and payer."""
    from .insights import WORK_TYPES, work_type
    w, params = _filters(signal, category, payer, year, status, None, None, q, None, None, deadline, handling, None, None, None, True, paid_days, min_dollars,
                         ema=ema, state=state)
    kinds = {t["key"]: {"key": t["key"], "n": 0, "cents": 0, "payers": {}} for t in WORK_TYPES}
    for r in rows(f"""SELECT signal, actionable, carc, payer_group pg, COUNT(*) n, COALESCE(SUM(shortfall_cents),0) cents FROM findings{w}
                      GROUP BY 1,2,3,4""", tuple(params)):
        wt = work_type(r["signal"], r["actionable"], r["carc"])
        k = kinds.setdefault(wt, {"key": wt, "n": 0, "cents": 0, "payers": {}})
        k["n"] += r["n"]
        k["cents"] += r["cents"]
        p = k["payers"].setdefault(r["pg"], [0, 0])
        p[0] += r["n"]
        p[1] += r["cents"]
    delta, each = _pattern_cents(w, params, deadline)
    if each and deadline in DEADLINE_RANGES:
        # a routine-rate pattern counts the cents of its claims in range, in its kind and in its payer's pile
        full = {r["finding_id"]: (r["pg"], r["c"], work_type(r["signal"], r["actionable"], r["carc"])) for r in rows(
            f"SELECT finding_id, payer_group pg, signal, actionable, carc, COALESCE(shortfall_cents,0) c FROM findings{w}{' AND' if w else ' WHERE'} "
            f"signal IN {PATTERN_SQL}", tuple(params))}
        for fid, (cents, _d, _n) in each.items():
            if fid in full:
                g, c, wt = full[fid]
                rk = kinds[wt]
                rk["cents"] += cents - c
                rk["payers"].setdefault(g, [0, 0])[1] += cents - c
    out = []
    for k in kinds.values():
        if not k["n"]:
            continue
        piles = sorted(((g, v) for g, v in k["payers"].items() if g), key=lambda kv: (-kv[1][1], -kv[1][0]))[:3]
        out.append({"key": k["key"], "n": k["n"], "cents": k["cents"], "piles": [{"payer": g, "n": v[0], "cents": v[1]} for g, v in piles]})
    out.sort(key=lambda k: (-k["cents"], -k["n"]))
    return {"n": sum(k["n"] for k in out), "cents": sum(k["cents"] for k in out), "kinds": out}


@app.get("/api/desk")
def desk(category: str = Query(..., max_length=80), payer: Optional[str] = Query(None, max_length=200), min: int = Query(0, ge=0, le=10**6),
         status: str = Query(TODO_STATUSES, max_length=80), deadline: Optional[str] = Query(None, max_length=20),
         signal: Optional[str] = Query(None, max_length=400), paid: int = Query(0, ge=0, le=3650), tiles: int = Query(1, ge=0, le=1),
         kind: Optional[str] = Query(None, max_length=20), reason: Optional[str] = Query(None, max_length=60), q: Optional[str] = None,
         year: Optional[str] = Query(None, max_length=4), handling: Optional[str] = Query(None, max_length=40),
         ema: Optional[str] = Query(None, max_length=40), state: Optional[str] = Query(None, max_length=120),
         tileset: str = Query("all", pattern="^(all|state)$"), request: Request = None, _: str = Depends(require_auth)):
    """A category page in one call: its tiles (closes in 7 and 30 days, window open, to decide, older with no date, window
    closed, every item to do, sent and waiting, follow-ups due, closed in the last 30 days), its state tiles (STATE_TILES:
    work now with the follow-ups due, of those what closes in 7 days, waiting, follow-ups due, done, accepted, expired,
    can't be checked), and the kinds of work under the chosen status, state, filing date, payment window and search with
    their payer piles. `signal` and `ema` narrow the whole page; the chosen kind of work and reason narrow the tiles only (a
    tile click keeps them, and the kinds of work stay listed so another can be chosen). Every number is the count line of
    the list it opens. Counts only: no patient, claim or date."""
    token = _WINDOWS_MEMO.set({})
    try:
        out = {"category": category, "scope": _desk_kinds(category, payer, status, deadline or None, min, signal, paid, _search_text(request, q), year,
                                                         handling or None, ema or None, state or None)}
        if tiles:
            # tileset=state: the state tiles the pages draw, and every item to do (the page's "any window" line); all: the
            # filing-date tiles too, as an earlier page and the counts elsewhere read them
            out["tiles"] = {name: _bucket(category, payer, st, dl, min, reason or signal, kind, year, handling or None, ema or None)
                            for name, st, dl in DESK_TILES if tileset == "all" or name == "all"}
            out["tiles"].update({name: _bucket(category, payer, None, dl, min, reason or signal, kind, year, handling or None, ema or None, sv)
                                 for name, sv, dl in STATE_TILES})
        return out
    finally:
        _WINDOWS_MEMO.reset(token)


@app.get("/api/coverage")
def coverage(_: str = Depends(require_auth)):
    """How complete the newest months of service are, and the weeks in which a payer that pays every week sent next to
    nothing and then resumed. About payers and the practice as a whole, so it is open to every login."""
    from . import activity
    from .link import stored_ema_coverage
    conn = db()
    try:
        ema = stored_ema_coverage(conn) if has_table("meta") else {"weeks": []}
        if not has_table("meta") or not has_table("provider_days") or not activity.status(conn)["ready"]:
            return {"ready": False, "gaps": [], "months": [], "ema": ema}
        with _ACTIVITY_LOCK:
            cov = activity.coverage(conn)
        return {"ready": True, "gaps": cov["gaps"], "months": cov["months"][-6:], "lag": cov["lag"], "last_payment": cov["last_payment"],
                "last_complete_month": cov["last_complete_month"], "complete_share": cov["complete_share"], "ema": ema}
    finally:
        conn.close()


@app.get("/api/activity/unlock")
def activity_unlock(_: str = Depends(require_owner)):
    """One light call for the page to try the owner's password with, so that a wrong try counts once and not once for
    every figure the tab then asks for."""
    return {"ok": True}


@app.get("/api/activity/overview")
def activity_overview(month_from: Optional[str] = Query(None, alias="from", pattern=MONTH_PATTERN),
                      month_to: Optional[str] = Query(None, alias="to", pattern=MONTH_PATTERN), _: str = Depends(require_owner)):
    """One row per clinician for a window of months of service: clinic days, patients per day, new patients, visit
    levels, biopsies, what the services were allowed, how many patients stay."""
    from . import activity
    return _activity(activity.overview, month_from, month_to)


@app.get("/api/activity/trend")
def activity_trend(by: str = Query("quarter", pattern="^(quarter|month)$"), _: str = Depends(require_owner)):
    from . import activity
    return _activity(activity.trend, by)


@app.get("/api/activity/recent")
def activity_recent(_: str = Depends(require_owner)):
    """The newest weeks from the EMA charge exports, with the days an export could not have captured yet set apart."""
    from . import activity
    return _activity(activity.recent)


@app.get("/api/activity/provider/{provider_id}")
def activity_provider(provider_id: str, month_from: Optional[str] = Query(None, alias="from", pattern=MONTH_PATTERN),
                      month_to: Optional[str] = Query(None, alias="to", pattern=MONTH_PATTERN), _: str = Depends(require_owner)):
    from . import activity
    if not re.fullmatch(r"[A-Za-z0-9:_-]{1,60}", provider_id):
        raise HTTPException(404, "No such clinician")
    return _activity(activity.detail, provider_id, month_from, month_to)


@app.get("/api/activity/calculator")
def activity_calculator(provider: str = Query("mohs", pattern=r"^[A-Za-z0-9:_-]{1,60}$"),
                        month_from: Optional[str] = Query(None, alias="from", pattern=MONTH_PATTERN),
                        month_to: Optional[str] = Query(None, alias="to", pattern=MONTH_PATTERN),
                        months: int = Query(6, ge=3, le=24), _: str = Depends(require_owner)):
    """What one clinician's schedule is worth at each payer's rates (remitwatch.calculator): the schedule now, per clinic
    day, how it turns into billed codes, each payer's rate and Medicare's, and what was allowed. The page does the
    arithmetic as the sliders move. Owner only: it is one named clinician's money."""
    from . import calculator
    return _activity(calculator.calculator, provider, month_from, month_to, months)


@app.get("/api/activity/asc_rule")
def activity_asc_rule(months: int = Query(12, ge=3, le=24),
                      month_from: Optional[str] = Query(None, alias="from", pattern=MONTH_PATTERN),
                      month_to: Optional[str] = Query(None, alias="to", pattern=MONTH_PATTERN), _: str = Depends(require_owner)):
    """The practice's cases at its ASC over a window, with Medicare's ASC and physician rates for the year before and the
    rule year, and what each kind of payer pays the practice against Medicare (remitwatch.calculator.asc_rule): the page
    works out what the rule year does to what is paid for them. Owner only, as the rest of the Calculator."""
    from . import calculator
    return _activity(calculator.asc_rule, months, month_from, month_to)


@app.get("/api/activity/asc_rates")
def activity_asc_rates(q: str = Query("", max_length=2400), state: str = Query("", pattern=r"^[A-Za-z]{0,2}$"),
                       county: str = Query("", max_length=60), offset: int = Query(0, ge=0, le=20000),
                       limit: int = Query(100, ge=1, le=500), _: str = Depends(require_owner)):
    """ASCA's rate calculator: Medicare's ASC amounts for the codes a search names (whole codes, the start of a code, a
    range), nationally and in a county, in the rule year against the year before (remitwatch.ascrates). CMS's published
    rates only, no practice figure."""
    from . import ascrates
    return ascrates.lookup(q, state or None, county or None, offset, limit)


@app.get("/api/activity/asc_places")
def activity_asc_places(_: str = Depends(require_owner)):
    """The rate calculator's picker: every state and county with an ASC wage index, the payment indicators' meanings
    (Addendum DD1), and the county of the practice's ASC to start from."""
    from . import ascrates
    from .calculator import ASC_SITE
    home = ascrates.office_place(ASC_SITE["office"]) or {}
    return {**ascrates.places(), "pi": ascrates.definitions(), "home": {"state": home.get("state"), "county": home.get("county")},
            "rule_year": ascrates.rule_year(), "prior_year": ascrates.prior_year()}


ASSUMPTIONS = "calculator/assumptions.json"     # in the state folder (cloud) or the meta table (server)
ASSUMPTIONS_KEY = "calculator:assumptions"
# A hard cap on the saved figures, scenarios included. A scenario carries the whole calculator plan (contracts corrected,
# targets and indexing per payer) and every strategy's settings: about 2.7 KB, 4.1 KB with every contract ticked and
# every strategy set (24 payers). 2 MiB holds MAX_SCENARIOS of those (about 0.8 MB) with the figures and pay plans and
# room for a practice with twice the payers, so the page's own limit of 200 is the one the owner meets; it stays well
# under the 4.5 MB a serverless request or answer may be.
MAX_ASSUMPTIONS_BYTES = 2 * 1024 * 1024
MAX_SCENARIOS = 200                 # as the page's LabModel.MAX_SCENARIOS
MAX_FIGURE = 1e9
FIGURES_VERSION_HEADER = "x-figures-version"
_FIG_KEY = re.compile(r"[a-z0-9_]{1,40}")
_FIG_ID = re.compile(r"[A-Za-z0-9:_-]{1,60}")
_COMP_TYPES = ("pct", "wrvu", "base")
_COMP_FIELDS = ("pct", "per_wrvu", "base", "threshold", "above_pct")


def require_owner_password(request: Request = None, credentials: Optional[HTTPBasicCredentials] = Depends(security)) -> str:
    """The practice figures hold what each clinician is paid. With no owner's password set, require_owner lets every
    login in (the Providers tab is open then); these figures are refused to everyone instead, until one is set."""
    user = require_owner(request, credentials)
    if not OWNER_PASSWORD:
        raise HTTPException(status_code=403, detail="The practice figures hold what each clinician is paid, so they need an owner's "
                            "password. Set REMITWATCH_OWNER_PASSWORD to use them.")
    return user


def _figures_version(raw: bytes | None) -> str | None:
    """A version of the saved figures: a fingerprint of what is stored. A save names the version it started from."""
    return hashlib.sha256(raw).hexdigest()[:16] if raw else None


def _figures_problem(data: Any) -> str | None:
    """Why a document cannot be stored as the practice figures, or None. Every figure is a number of 0 or more, or empty;
    pay plans are of a known kind; scenarios carry a name, a clinician and plain settings (numbers, true or false,
    short text). The page draws these values, so nothing else is let in."""
    import math

    def number(v: Any) -> bool:
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            return False
        try:          # an integer past a float's range (1 followed by 400 zeros) is not a number the page can hold
            return math.isfinite(v)
        except OverflowError:
            return False

    def fig(v: Any, top: float = MAX_FIGURE) -> bool:
        return v is None or (number(v) and 0 <= v <= top)

    def text(v: Any, top: int) -> bool:
        """Text the page can hold and the server can send back: no lone UTF-16 surrogate (which no UTF-8 answer can
        carry), and at most `top` UTF-16 code units, as the page counts (String.length, .slice, maxlength), so the page
        never cuts a stored name in the middle of a character."""
        if not isinstance(v, str) or any("\ud800" <= ch <= "\udfff" for ch in v):
            return False
        return len(v.encode("utf-16-le")) // 2 <= top

    def plain(v: Any, depth: int) -> bool:
        if v is None or isinstance(v, (bool, int, float)):
            return v is None or isinstance(v, bool) or number(v)
        if isinstance(v, str):
            return text(v, 200)
        if depth <= 0:
            return False
        if isinstance(v, list):
            return len(v) <= 100 and all(plain(w, depth - 1) for w in v)
        if isinstance(v, dict):
            return len(v) <= 500 and all(text(k, 120) and plain(w, depth - 1) for k, w in v.items())
        return False

    if not isinstance(data, dict):
        return "expected an object"
    extra = set(data) - {"minutes", "costs", "session_minutes", "comp", "scenarios", "saved_at"}
    if extra:
        return f"unknown field {sorted(extra)[0][:40]!r}"
    for group in ("minutes", "costs"):
        g = data.get(group, {})
        if not isinstance(g, dict):
            return f"{group} must be an object"
        for k, v in g.items():
            if not _FIG_KEY.fullmatch(k) or not fig(v):
                return f"{group}: each figure must be a number of 0 or more"
    if not fig(data.get("session_minutes")):
        return "the minutes in a clinic day must be a number of 0 or more"
    comp = data.get("comp", {})
    if not isinstance(comp, dict) or len(comp) > 1000:
        return "comp must be an object"
    for cid, plan in comp.items():
        if not _FIG_ID.fullmatch(cid) or not isinstance(plan, dict) or plan.get("type") not in _COMP_TYPES or set(plan) - {"type", *_COMP_FIELDS}:
            return "each pay plan needs a clinician and a known kind of plan"
        if not all(fig(plan.get(k), 1 if k.endswith("pct") else MAX_FIGURE) for k in _COMP_FIELDS):
            return "a pay plan's figures must be numbers of 0 or more (shares at most 100%)"
    scenarios = data.get("scenarios", [])
    if not isinstance(scenarios, list):
        return "scenarios must be a list"
    if len(scenarios) > MAX_SCENARIOS:
        return f"at most {MAX_SCENARIOS} saved scenarios: delete some first"
    for sc in scenarios:
        if not isinstance(sc, dict) or set(sc) - {"name", "who", "x", "chosen", "saved_at"}:
            return "a scenario has an unknown field"
        name = sc.get("name")
        if not isinstance(name, str) or not name.strip() or not text(name, 100):
            return "each scenario needs a name of up to 100 characters"
        if not isinstance(sc.get("who"), str) or not _FIG_ID.fullmatch(sc["who"]):
            return "each scenario needs a clinician"
        if "saved_at" in sc and not text(sc["saved_at"], 40):
            return "a scenario's date must be short text"
        x = sc.get("x", {})
        if not isinstance(x, dict) or not plain(x, 3):
            return "a scenario's plan must hold plain settings"
        chosen = sc.get("chosen", {})
        if not isinstance(chosen, dict) or len(chosen) > 50:
            return "a scenario's strategies must be an object"
        for sid, c in chosen.items():
            if not _FIG_KEY.fullmatch(sid) or not isinstance(c, dict) or set(c) - {"on", "params"} or not isinstance(c.get("on", False), bool):
                return "a scenario's strategy settings are not valid"
            params = c.get("params", {})
            if not isinstance(params, dict) or len(params) > 50 or not all(_FIG_KEY.fullmatch(k) and number(v) for k, v in params.items()):
                return "a scenario's strategy settings must be numbers"
    return None


@app.get("/api/activity/assumptions")
def activity_assumptions(_: str = Depends(require_owner_password)):
    """The practice's own figures the strategy lab works with (minutes per service, costs, pay plans, saved
    scenarios), as the owner last saved them, with their version. Owner only: it holds what each clinician is paid."""
    import math

    from fastapi.responses import Response
    raw = None
    if runtime.serverless():
        _, st = _cloud_state()
        raw = st.get(ASSUMPTIONS)
    else:
        conn = db()
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
            r = conn.execute("SELECT value FROM meta WHERE key=?", (ASSUMPTIONS_KEY,)).fetchone()
            raw = r[0].encode() if r else None
        finally:
            conn.close()
    if not raw:
        return {"assumptions": None}
    version = _figures_version(raw)
    try:      # a number that is not finite, saved before the checks below existed, comes back empty, not as an error
        data = json.loads(raw, parse_constant=lambda c: None, parse_float=lambda t: (lambda f: f if math.isfinite(f) else None)(float(t)))
    except (ValueError, RecursionError):
        data = None
    if not isinstance(data, dict):
        return {"assumptions": None, "error": "the saved figures could not be read", "version": version}
    # escaped as ASCII, so text saved before the checks below existed (a lone UTF-16 surrogate, which UTF-8 cannot carry)
    # still reads, and the owner can delete it from the page
    return Response(json.dumps({"assumptions": data, "version": version}, separators=(",", ":"), allow_nan=False),
                    media_type="application/json")


@app.post("/api/activity/assumptions")
async def activity_assumptions_save(request: Request, user: str = Depends(require_owner_password)):
    """Replace the saved figures. The request names the version it was made from (X-Figures-Version, empty when there
    were none); when another window has saved since, it is refused with 409 and the page reads them again."""
    require_xhr(request)
    body = await request.body()
    if len(body) > MAX_ASSUMPTIONS_BYTES:
        raise HTTPException(413, "The saved figures would be too large: delete some saved scenarios.")

    def not_a_number(name: str):
        raise ValueError(name)
    try:
        data = json.loads(body or b"{}", parse_constant=not_a_number)
    except (ValueError, RecursionError):
        raise HTTPException(400, "not JSON (or a number that is not finite)")
    if not isinstance(data, dict):
        raise HTTPException(400, "expected an object")
    problem = _figures_problem(data)
    if problem:
        raise HTTPException(400, problem)
    data["saved_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    blob = json.dumps(data, separators=(",", ":")).encode()
    sent = (request.headers.get(FIGURES_VERSION_HEADER) or "").strip() or None
    stale = HTTPException(409, "These figures were changed in another window since this one read them. Read them again and save once more.")
    if runtime.serverless():
        from .state import Conflict
        _, st = _cloud_state()
        meta = st.meta(ASSUMPTIONS)
        if _figures_version(st.get(ASSUMPTIONS) if meta else None) != sent:
            raise stale
        try:      # written only if nobody wrote in between
            st.put(ASSUMPTIONS, blob, mode="update", rev=meta["rev"]) if meta else st.put(ASSUMPTIONS, blob, mode="add")
        except Conflict:
            raise stale
    else:
        conn = db()
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
            conn.execute("BEGIN IMMEDIATE")
            r = conn.execute("SELECT value FROM meta WHERE key=?", (ASSUMPTIONS_KEY,)).fetchone()
            if _figures_version(r[0].encode() if r else None) != sent:
                conn.rollback()
                raise stale
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (ASSUMPTIONS_KEY, blob.decode()))
            conn.commit()
        finally:
            conn.close()
    return {"ok": True, "saved_at": data["saved_at"], "version": _figures_version(blob)}


# --------------------------------------------------------------------------- progress: are we winning
MONEY = "category IN ('downcoding','bundling','denial','underpayment') AND COALESCE(stale,0)=0"


@app.get("/api/progress")
def progress(_: str = Depends(require_auth)):
    out: dict[str, Any] = {}
    out["pipeline"] = rows(f"SELECT status, COUNT(*) n, COALESCE(SUM(shortfall_cents),0) cents, COALESCE(SUM(recovered_cents),0) recovered_cents FROM findings WHERE {MONEY} GROUP BY 1")
    # window open and window closed, counted as the Worklist's tiles count them (a routine-rate pattern is open while one
    # of its claims is, with those claims' cents), so the figures here are the ones a click on those tiles lists
    token = _WINDOWS_MEMO.set({})
    try:
        out["live"] = _bucket(CLAIM_CATEGORIES, None, TODO_STATUSES, "valid")
        out["expired"] = _bucket(CLAIM_CATEGORIES, None, TODO_STATUSES, "expired")
    finally:
        _WINDOWS_MEMO.reset(token)
    out["recovered_by_month"] = rows(f"""SELECT substr(recovered_date,1,7) month, category, COUNT(*) n, COALESCE(SUM(recovered_cents),0) cents
                                         FROM findings WHERE {MONEY} AND status='resolved' AND recovered_date >= '2023-01' GROUP BY 1,2 ORDER BY 1""")
    out["cohorts"] = rows(f"""SELECT month, COUNT(*) n, COALESCE(SUM(shortfall_cents),0) cents, SUM(status='resolved') resolved_n,
                                     COALESCE(SUM(CASE WHEN status='resolved' THEN recovered_cents END),0) recovered_cents,
                                     SUM(status IN ('appealed','in_progress')) working_n, SUM(status='lost') lost_n
                              FROM findings WHERE {MONEY} AND status != 'dismissed' AND line_key IS NOT NULL AND month >= '2024-01' GROUP BY 1 ORDER BY 1""")
    out["by_payer"] = rows(f"""SELECT payer_group, COUNT(*) n, COALESCE(SUM(shortfall_cents),0) cents, SUM(status='resolved') resolved_n,
                                      COALESCE(SUM(CASE WHEN status='resolved' THEN recovered_cents END),0) recovered_cents,
                                      SUM(status='lost') lost_n, SUM(status IN ('appealed','in_progress')) working_n,
                                      COALESCE(SUM(CASE WHEN status IN ('open','in_progress','appealed') THEN shortfall_cents END),0) open_cents,
                                      COALESCE(SUM(CASE WHEN status IN ('open','in_progress') AND appeal_deadline >= ? THEN shortfall_cents END),0) live_cents,
                                      ROUND(AVG(CASE WHEN status='resolved' AND recovered_date IS NOT NULL THEN julianday(recovered_date) - julianday(payment_date) END)) avg_days
                               FROM findings WHERE {MONEY} AND status != 'dismissed' AND line_key IS NOT NULL
                               GROUP BY 1 HAVING n >= 25 ORDER BY cents DESC LIMIT 30""", (_day(0),))
    out["downcode_trend"] = rows(f"SELECT month, payer_group, COUNT(*) n, COALESCE(SUM(shortfall_cents),0) cents FROM findings WHERE {FAMILY['downcoding'][0]} AND status != 'dismissed' AND COALESCE(stale,0)=0 AND month >= '2024-01' GROUP BY 1,2 ORDER BY 1")
    out["visit_lines"] = rows(f"SELECT month, payer_group, SUM(lines) lines FROM exposure WHERE code IN ({EM_LIST}) AND month >= '2024-01' GROUP BY 1,2") if has_table("exposure") else []
    out["family_trend"] = rows(f"""SELECT month, category, COUNT(*) n, COALESCE(SUM(shortfall_cents),0) cents, SUM(status='resolved') resolved_n
                                   FROM findings WHERE {MONEY} AND status != 'dismissed' AND line_key IS NOT NULL AND month >= '2024-01' GROUP BY 1,2 ORDER BY 1""")
    out["all_lines"] = rows("SELECT month, SUM(lines) lines FROM exposure WHERE month >= '2024-01' GROUP BY 1 ORDER BY 1") if has_table("exposure") else []
    out["snapshots"] = rows("SELECT at, status, SUM(n) n, SUM(cents) cents FROM progress_snapshots WHERE category IN ('downcoding','bundling','denial','underpayment') GROUP BY run_id, status ORDER BY at") if has_table("progress_snapshots") else []
    # Activity: what people and the payers did. The lines "Build packets from EMA" writes for each item it looked at (a note
    # lookup, a packet built) stay in the item's own history: ten of them a press would push the status changes out of the 60
    out["events"] = rows("""SELECT e.at, e.kind, e.from_status, e.to_status, e.amount_cents, e.actor, f.payer_group, f.signal, f.procedure_code, e.finding_id
                            FROM finding_events e LEFT JOIN findings f ON f.finding_id = e.finding_id
                            WHERE COALESCE(e.kind,'') NOT IN ('note_lookup','packet') ORDER BY e.at DESC, e.id DESC LIMIT 60""") if has_table("finding_events") else []
    return out


# --------------------------------------------------------------------------- alerts
@app.get("/api/alerts")
def alerts_list(status: str = "active", _: str = Depends(require_auth)):
    if not has_table("alerts"):
        return {"items": [], "counts": {}}
    where = {"active": "status IN ('new','acknowledged')", "new": "status='new'", "all": "1=1", "cleared": "status='cleared'"}.get(status, "status IN ('new','acknowledged')")
    items = rows(f"""SELECT * FROM alerts WHERE {where} ORDER BY CASE status WHEN 'new' THEN 0 WHEN 'acknowledged' THEN 1 ELSE 2 END,
                     CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, COALESCE(cents_recent,0) DESC, last_seen DESC LIMIT 300""")
    for a in items:
        a["detail"] = json.loads(a["detail"]) if a.get("detail") else {}
    counts = {r["status"]: r["n"] for r in rows("SELECT status, COUNT(*) n FROM alerts GROUP BY 1")}
    return {"items": items, "counts": counts}


@app.post("/api/alerts/{alert_id}/ack", dependencies=[Depends(require_xhr)])
def alert_ack(alert_id: str, _: str = Depends(require_auth)):
    if runtime.serverless():
        from .cloud import SERVING_COPY
        SERVING_COPY.record({"kind": "alert_ack", "alert_id": alert_id})
        return {"ok": True}
    conn = db()
    try:
        apply_alert_ack(conn, alert_id)
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


# --------------------------------------------------------------------------- the feed: status, sync now, Dropbox webhook
def _cloud_sync_status() -> dict:
    from . import cloud
    from .pipeline import dropbox_configured
    st = cloud.SERVING_COPY.status()
    lc = st.get("last_cycle") or {}
    newest = (one("SELECT MAX(payment_date) d FROM transactions WHERE superseded=0") or {}).get("d") if has_table("transactions") else None
    files = (one("SELECT MAX(ingested_at) last, MAX(CASE WHEN kind='835' THEN ingested_at END) last835 FROM files") or {}) if has_table("files") else {}
    feed = _feed_day()
    behind = (datetime.fromisoformat(_day(0)).date() - datetime.fromisoformat(feed).date()).days if feed else None     # the practice's day
    return {"mode": "serverless", "dropbox_configured": dropbox_configured(), "webhook_ready": bool(os.environ.get("DROPBOX_APP_SECRET")),
            "interval_minutes": 0, "scheduler_running": False, "next_run": None, "cron_ready": bool(os.environ.get("CRON_SECRET")),
            "running": bool(st.get("running")), "running_since": (st.get("running") or {}).get("since"), "last_cycle": lc or None,
            "stalled_since": (st.get("stalled") or {}).get("since"), "memory_mb": lc.get("memory_mb"),
            "last_sync": lc.get("finished"), "newest_payment_date": newest, "feed_date": feed, "days_behind": behind, "has_data": st.get("has_data"),
            "last_file_ingested": files.get("last"), "last_remit_ingested": files.get("last835"), "storage_error": st.get("error"),
            "smtp_configured": bool(os.environ.get("SMTP_HOST") and os.environ.get("REPORT_TO")),
            "alert_webhook": bool(os.environ.get("ALERT_WEBHOOK_URL")), "note_review": bool(os.environ.get("ANTHROPIC_API_KEY"))}


@app.get("/api/sync/status")
def sync_status(_: str = Depends(require_auth)):
    if runtime.serverless():
        return _cloud_sync_status()
    from .pipeline import status
    return dict(status(DB_PATH), mode="server")


FRESHEN_HOURS = float(os.environ.get("REMITWATCH_FRESHEN_HOURS", "3"))


def _last_pull_hours() -> float | None:
    """Hours since the last run that looked for new files (Dropbox or TriZetto), as its status says."""
    try:
        if runtime.serverless():
            from . import cloud
            lc = (cloud.SERVING_COPY.status() or {}).get("last_cycle") or {}
            at = lc.get("finished") or lc.get("started")
        else:
            from .pipeline import status
            at = (status(DB_PATH).get("last_cycle") or {}).get("finished")
        if not at:
            return None
        return (datetime.now(timezone.utc) - datetime.fromisoformat(str(at).replace("Z", "+00:00"))).total_seconds() / 3600
    except Exception:
        return None


@app.post("/api/sync/freshen", dependencies=[Depends(require_xhr)])
def sync_freshen(request: Request, _: str = Depends(require_auth)):
    """The dashboard asks this when it opens: when nothing has looked for new remittances for a few hours, a run starts in
    the background (TriZetto, Dropbox, the analysis), so the lists catch up with payments without anyone pressing Sync."""
    from . import trizetto
    from .pipeline import dropbox_configured
    if not (dropbox_configured() or trizetto.configured()):
        return {"started": False, "reason": "no feed is set up"}
    hours = _last_pull_hours()
    if hours is not None and hours < FRESHEN_HOURS:
        return {"started": False, "hours": round(hours, 1)}
    if runtime.serverless():
        from . import cloud
        if runtime.preview():
            return {"started": False, "reason": "preview"}
        st = cloud.SERVING_COPY.status()
        if st.get("running"):
            return {"started": False, "running": True}
        ok = cloud.trigger_cycle(base_url(request), "freshen")
        cloud.SERVING_COPY._status = (0.0, None)
        return {"started": bool(ok), "hours": None if hours is None else round(hours, 1)}
    from .pipeline import run_once_in_background
    run_once_in_background(DB_PATH, "freshen")
    return {"started": True, "hours": None if hours is None else round(hours, 1)}


@app.post("/api/sync/run", dependencies=[Depends(require_xhr)])
def sync_run(request: Request, _: str = Depends(require_auth)):
    if runtime.serverless():
        from . import cloud
        if runtime.preview():
            raise HTTPException(409, "This is a preview deployment. Only the production site reads and writes the practice's data.")
        if not cloud.trigger_cycle(base_url(request), "button"):
            raise HTTPException(400, "The site cannot start a run: CRON_SECRET is not set. Add it in Vercel and redeploy.")
        cloud.SERVING_COPY._status = (0.0, None)
        return {"started": True}
    from .pipeline import run_once_in_background
    run_once_in_background(DB_PATH, "button")
    return {"started": True}


def _cron_auth(request: Request, credentials: Optional[HTTPBasicCredentials]) -> None:
    """Vercel's scheduler and the site's own follow-up calls send the CRON_SECRET as a bearer token. The dashboard's
    own pages may also start a run with the login."""
    secret = (os.environ.get("CRON_SECRET") or "").strip()
    sent = (request.headers.get("authorization") or "").strip()
    if secret and secrets.compare_digest(sent.encode(), f"Bearer {secret}".encode()):
        return
    if password_problem():
        raise HTTPException(status_code=401, detail="Unauthorized")
    require_auth(request, credentials)
    # A browser attaches the saved login to any request for this site, including one another site's page causes. The
    # header below cannot be added that way, so a run is only started by this site's own pages (or by the secret).
    require_xhr(request)


@app.api_route("/api/cron/cycle", methods=["GET", "POST"], include_in_schema=False)
def cron_cycle(request: Request, trigger: str = "cron", delay: int = 0, full: int = 0, force: int = 0, hop: int = 0,
               credentials: Optional[HTTPBasicCredentials] = Depends(security)):
    """One cycle. On a serverless host this is the whole pipeline, run inside this request within its time budget; when
    there is more to do than fits, it starts a fresh invocation of itself before it returns."""
    _cron_auth(request, credentials)
    if not runtime.serverless():
        from .pipeline import run_once_in_background
        run_once_in_background(DB_PATH, trigger)
        return {"started": True, "mode": "server"}
    from . import cloud
    from .dropbox_sync import DropboxClient
    from .pipeline import dropbox_configured
    from .state import state_from_env
    if runtime.preview():
        raise HTTPException(409, "This is a preview deployment. Only the production site reads and writes the practice's data.")
    st = state_from_env()
    if st is None:
        raise HTTPException(503, "Dropbox is not connected yet. Open /setup.")
    delay = min(max(int(delay), 0), 90)
    client = (getattr(st, "client", None) or DropboxClient()) if dropbox_configured() else None
    # the wait happens inside the cycle, after it has taken the lock, so later notifications see a run in progress
    out = cloud.run_cloud_cycle(st, client, budget_s=max(60, runtime.max_seconds() - delay), trigger=trigger[:24], full=bool(full),
                                force_detect=bool(force), delay_s=delay)
    # A run starts the next one only while it is getting somewhere, and only so many times in a row. Whatever goes
    # wrong, the chain ends; the daily run or a person starts a new one.
    hop = max(int(hop), 0)
    if hop >= cloud.MAX_HOPS:
        if out.get("more"):
            out["halted"] = f"{hop} runs in a row without finishing"
            cloud.write_status(st, out)
    elif out.get("more") and out.get("progress"):
        out["continued"] = cloud.trigger_cycle(base_url(request), "continue", hop=hop + 1)
    elif (out.get("ingest") or {}).get("download_errors"):
        # a file could not be fetched (a timeout, a busy server): try twice more, a minute and a half apart, then leave
        # it to the daily run. Notifications start nothing in the meantime, so without this the file would wait a day.
        if hop < 2:
            out["continued"] = cloud.trigger_cycle(base_url(request), "retry", delay=90, hop=hop + 1)
    elif not out.get("skipped") and not out.get("more"):
        # files that landed while this cycle ran: new in the Dropbox folder, or an upload finished into the state folder's
        # intake/ (its own cron call was skipped while this run held the lock), so it is read now, not by tomorrow's run
        try:
            from .intake import state_pending
            late = "uploads" if state_pending(st) else ("files" if client is not None and cloud.has_new_files(st, client) else None)
            if late:
                out["continued"] = cloud.trigger_cycle(base_url(request), "continue", delay=30, hop=hop + 1)
                out["continued_for"] = late
        except Exception as exc:        # noqa: BLE001  (the daily run reads them; the answer says why none was started)
            out["continue_error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
    return out


@app.get("/api/dropbox/webhook", include_in_schema=False)
def dropbox_webhook_verify(challenge: str = ""):
    """Dropbox checks the address once by asking for its challenge to be echoed back."""
    return PlainTextResponse(challenge, headers={"X-Content-Type-Options": "nosniff"})


def _webhook_work(origin: str) -> None:
    """After a signed notification on a serverless host: start a cycle only when the data folder really has something
    new. The app's own writes to its state folder raise notifications too, and those must not start one."""
    try:
        from . import cloud
        from .dropbox_sync import DropboxClient
        from .state import state_from_env
        st = state_from_env(fast=True)
        if st is None or runtime.preview():
            return
        # a run in progress looks for late arrivals itself when it ends; a run that failed is not retried from here
        if cloud.webhook_should_start(st, getattr(st, "client", None) or DropboxClient(attempts=1, timeout=8)):
            cloud.trigger_cycle(origin, "webhook", delay=45)
    except Exception:
        pass        # the daily scheduled run picks up anything a failed notification missed


@app.post("/api/dropbox/webhook", include_in_schema=False)
async def dropbox_webhook(request: Request):
    """Dropbox calls this when a file changes. The body is signed with the app secret; Dropbox expects an answer within
    ten seconds, so the work happens after the reply (or in a fresh invocation)."""
    secret = os.environ.get("DROPBOX_APP_SECRET", "")
    body = await request.body()
    sig = request.headers.get("x-dropbox-signature", "")
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not secret or not hmac.compare_digest(sig.encode("utf-8", "replace"), expected.encode()):
        raise HTTPException(status_code=403, detail="bad signature")
    if runtime.serverless():
        # A serverless host may freeze the process once the reply is sent, so the check runs before it: three short
        # calls, well inside Dropbox's ten seconds. If it ever runs long the reply goes out anyway.
        try:
            await asyncio.wait_for(asyncio.to_thread(_webhook_work, base_url(request)), timeout=8)
        except Exception:
            pass
        return PlainTextResponse("ok")
    from .pipeline import SCHEDULER, run_once_in_background
    if SCHEDULER.is_alive():
        SCHEDULER.poke("webhook")
    else:
        run_once_in_background(DB_PATH, "webhook")
    return PlainTextResponse("ok")


# --------------------------------------------------------------------------- setup: every setting checked live
@app.get("/api/setup/status")
def setup_status(request: Request, credentials: Optional[HTTPBasicCredentials] = Depends(security)):
    problem = password_problem()
    if problem and not open_allowed():
        # before a usable password exists the page may only say what is wrong with it: nothing else about the site is public
        return JSONResponse({"locked": True, "ready": False, "checks": [
            {"key": "password", "label": "Dashboard password (REMITWATCH_PASSWORD)", "ok": False, "detail": problem,
             "fix": _password_fix(problem) + " Everything else on this page appears after that."}]},
            headers={"Cache-Control": "no-store"})
    require_auth(request, credentials)
    from . import cloud
    out = cloud.setup_status(base_url(request))
    weak = password_warning()
    for c in out.get("checks", []):
        if weak and c.get("key") == "password":
            # it works, so it stays ticked and the site stays ready, but the line says plainly that it must be replaced
            c.update(detail=f"set, but {weak}: this site still accepts it, so nobody is locked out, but it is easy to guess",
                     fix=_password_fix(weak), warn=True)
    return JSONResponse(out, headers={"Cache-Control": "no-store"})


@app.post("/api/setup/dropbox-token", dependencies=[Depends(require_xhr)])
def setup_dropbox_token(body: dict = Body(default={}), _: str = Depends(require_auth)):
    """Exchange the code Dropbox shows after approval for the long-lived token, and show it once so it can be pasted into
    the host's settings. The site cannot store it itself: a serverless deployment cannot change its own settings."""
    from .dropbox_sync import DropboxClient
    key, secret = os.environ.get("DROPBOX_APP_KEY"), os.environ.get("DROPBOX_APP_SECRET")
    code = str(body.get("code") or "").strip()
    if not key or not secret:
        raise HTTPException(400, "DROPBOX_APP_KEY and DROPBOX_APP_SECRET must be set first.")
    if not code:
        raise HTTPException(400, "Paste the code Dropbox showed you.")
    try:
        j = DropboxClient.exchange_code(key, secret, code)
    except Exception:
        raise HTTPException(400, "Dropbox refused the code. A code works once and only for a few minutes: open the Dropbox link again and paste the new code.")
    if not j.get("refresh_token"):
        raise HTTPException(400, "Dropbox did not return a long-lived token. Open the Dropbox link from this page again; do not use a link from elsewhere.")
    scope = j.get("scope") or ""
    missing = [x for x in ("files.metadata.read", "files.content.read", "files.content.write") if x not in scope.split()]
    return JSONResponse({"refresh_token": j["refresh_token"], "missing_permissions": missing}, headers={"Cache-Control": "no-store"})


# --------------------------------------------------------------------------- appeal packets built from the clinical note
# Vercel refuses request bodies over 4.5 MB before they reach the app, and the packet built from the note has to come back
MAX_NOTE_BYTES = (3 if runtime.serverless() else 15) * 1024 * 1024


@app.post("/api/appeals/build", dependencies=[Depends(require_xhr)])
def appeals_build(finding_id: str = Form(...), text: str = Form(""), use_ai: str = Form("1"), file: Optional[UploadFile] = File(None),
                  user: str = Depends(require_auth)):
    """Review the uploaded or pasted visit note for one finding and build the appeal packet (letter, review, evidence, note) as a PDF.
    A plain function on purpose: the review takes about a minute and must not hold up every other request."""
    data = None
    if file is not None:
        data = file.file.read(MAX_NOTE_BYTES + 1)
        if len(data) > MAX_NOTE_BYTES:
            raise HTTPException(413, f"file is larger than {MAX_NOTE_BYTES // (1024 * 1024)} MB")
    if not data and not text.strip():
        raise HTTPException(400, "attach the visit note or paste its text")
    if len(text) > 2_000_000:
        raise HTTPException(413, "pasted text is too long")
    detail = finding_detail(finding_id, "ok")
    raw_claim = one("SELECT * FROM claims WHERE claim_key=?", (detail["finding"].get("claim_key"),)) if detail["finding"].get("claim_key") else None
    name = file.filename if (file is not None and data) else ""
    if _note_identity(detail, raw_claim, name, data, text or None).get("code") == "several":
        from .uploads import SEVERAL
        raise HTTPException(400, SEVERAL)           # a report listing several patients is not this visit's note
    packet = _store_packet(detail, raw_claim, name, data, text or None, use_ai == "1", source="person")
    if data and not runtime.serverless() and detail["finding"].get("claim_key"):
        # kept as a note uploaded for this item's visit too (remitwatch.uploads): a rebuild, and the visit's other items, find it
        try:
            from . import uploads
            tmp = uploads.incoming(DB_PATH)
            with open(tmp, "wb") as fh:
                fh.write(data)
            uploads.accept(DB_PATH, tmp, name or "visit note", user, finding_id=finding_id)
        except Exception:
            pass        # the packet is what was asked for
    return packet


def _keep_packet(build) -> dict:
    """Run a packet builder from remitwatch.notes and keep what it made: in the store, or on a serverless host (no database
    to keep it in) built in memory and stored as two files in the state folder."""
    if runtime.serverless():
        from . import cloud
        st = cloud.SERVING_COPY.state()
        if st is None:
            raise HTTPException(503, "Storage is not connected yet. Open /setup.")
        mem = sqlite3.connect(":memory:")
        try:
            packet = build(mem)
            row = mem.execute("SELECT letter, letter_redacted, pdf FROM appeal_packets WHERE packet_id=?", (packet["packet_id"],)).fetchone()
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        finally:
            mem.close()
        stored = dict(packet, letter_full=row[0], letter_redacted=row[1])
        stored.pop("letter", None)
        packet["packet_id"] = cloud.save_packet(st, stored, row[2])
        return packet
    conn = db()
    try:
        return build(conn)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    finally:
        conn.close()


def _visit_day(detail: dict, raw_claim: dict | None) -> str:
    f, c = detail.get("finding") or {}, raw_claim or {}
    return str(f.get("service_date") or c.get("service_date_start") or c.get("statement_from") or "")[:10]


def _note_identity(detail: dict, raw_claim: dict | None, name: str | None, data: bytes | None, text: str | None) -> dict:
    """What a note's own text shows about whose visit it is (uploads.note_identity), against the item's claim: the
    patient's name, their EMA patient ID or record number, and the date of service."""
    from . import uploads
    from .notes import MAX_TEXT_CHARS, extract_text
    try:
        body = text[:MAX_TEXT_CHARS] if (text and not name) else extract_text(name or "note", data or (text or "").encode("utf-8"))[0]
    except ValueError:
        body = ""
    c, f = raw_claim or {}, detail.get("finding") or {}
    ids = _ema_patient_ids(f.get("patient_control_number")) + ([c["medical_record_number"]] if c.get("medical_record_number") else [])
    return uploads.note_identity(body, c.get("patient_last") or c.get("corrected_patient_last"),
                                 c.get("patient_first") or c.get("corrected_patient_first"), _visit_day(detail, raw_claim), ids)


def _packet_identity(packet_id: str, p: dict, detail: dict, raw_claim: dict | None) -> dict:
    """The note check kept with a packet when it was built; for a packet built before the check, the note read again where
    the store keeps it (a server). A packet whose note cannot be read again waits for a person."""
    got = (p.get("analysis") or {}).get("note_identity")
    if isinstance(got, dict) and "ok" in got:
        return got
    if not runtime.serverless() and has_table("appeal_packets"):
        r = one("SELECT note_name, note_blob FROM appeal_packets WHERE packet_id=?", (packet_id,))
        if r and r.get("note_blob") is not None:
            return _note_identity(detail, raw_claim, r.get("note_name") or "note", r["note_blob"], None)
    return {"ok": False, "code": "unknown", "why": "The note this packet was built from cannot be read again to see whose note it is."}


def _store_packet(detail: dict, raw_claim: dict | None, name: str, data: bytes | None, text: str | None, use_ai: bool,
                  source: str = "person") -> dict:
    """Build the packet from a note (uploaded, pasted or read from EMA) and keep it. A review that found the note short is
    also kept as a lesson, for the suggested templates. The PDF goes to the payer and carries the real identifiers; the
    letter text in the answer does only when names are switched on. What the note's text shows about whose visit it is
    is kept with it (analysis.note_identity): a note read from the patient's own chart in EMA ('ema') is theirs by EMA's
    two identifiers; one uploaded or attached by a person ('upload', 'person') only by what its text shows."""
    from .notes import build_from_note, lesson
    ident = {"ok": True} if source == "ema" else _note_identity(detail, raw_claim, name, data, text)

    def build(conn):
        out = build_from_note(conn, detail, raw_claim, name, data, pasted_text=text, reveal=SHOW_PHI, use_ai=use_ai)
        out.setdefault("analysis", {})["note_identity"] = ident
        conn.execute("UPDATE appeal_packets SET analysis=? WHERE packet_id=?", (json.dumps(out["analysis"]), out["packet_id"]))
        conn.commit()
        return out
    packet = _keep_packet(build)
    les = lesson(packet, detail)
    if les:
        try:
            _record_action({"kind": "lesson", "record": les})
        except Exception:
            pass        # the packet matters more than the lesson
    if runtime.serverless() and detail["finding"].get("finding_id"):
        _queue_mark(detail["finding"]["finding_id"], "packet", "An appeal packet was built for it.")
    return _shown_note_name(packet)


QUEUE_RETRY_DAYS = 3        # an item "Build packets from EMA" found no note for (or more than one) is tried again after this


def _queue_mark(finding_id: str, event: str, note: str) -> None:
    """A line in the item's history that "Build packets from EMA" goes by: 'packet' (one was built: on a serverless host
    the queue cannot see the packets themselves) or 'note_lookup' (no note, or more than one: left for a few days, so the
    next press goes on to the items behind it)."""
    try:
        _record_action({"kind": "event", "event": event, "finding_id": finding_id, "actor": "packets", "note": note})
    except Exception:
        pass        # the packet, or the answer, matters more than the mark


# --------------------------------------------------------------------------- ModMed EMA: the visit note behind an appeal
def _ema_patient_ids(pcn: str | None) -> list[str]:
    """EMA's own patient ID on the claim's EMA rows: the surest way to the chart."""
    if not pcn or not has_table("ema_rows"):
        return []
    return [r["patient_id"] for r in rows("SELECT DISTINCT patient_id FROM ema_rows WHERE claim_id=? AND COALESCE(patient_id,'')!=''", (pcn,))]


def _note_lookup(key, pick: Any = None, since: str | None = None) -> dict:
    """The visit note behind an appeal, through one matching (modmed.find_note): first among the notes uploaded on the
    Upload page (a server: remitwatch.uploads), then from EMA's API. An uploaded note is used exactly as a note read from
    EMA would be. With neither, a server answers not_found saying how to upload it; a serverless host raises
    modmed.NotConfigured as before."""
    from . import modmed
    pick = str(pick) if pick else None
    if not runtime.serverless():
        from . import uploads
        conn = db()
        try:
            res = uploads.find_note(conn, key, pick, reveal=SHOW_PHI)
        finally:
            conn.close()
        if res["status"] != "not_found" or (pick and pick.startswith(uploads.DOC_PREFIX)):
            return res
        if not modmed.Settings.from_env().configured():
            return res
    elif pick and pick.startswith("upload-"):
        pick = None
    # with names shown, each candidate carries the patient's name, birth date and IDs, so a person can tell two patients
    # apart (and a pick between them is accepted); with names hidden such a pick is refused and the note is attached by hand
    return modmed.note_for_claim(modmed.client(), key, pick=pick, budget=200, since=since, reveal=SHOW_PHI)


def _note_batch_since() -> str | None:
    """The first date of service among the open items that rest on the visit note, as "Build packets from EMA" works
    through them: where ModMed lets this app only export, one export from that day serves every item of a batch instead
    of a new one for each earlier claim (modmed.note_for_claim, `since`)."""
    from .insights import kind_sql
    cond, params = kind_sql("note")
    r = one(f"""SELECT MIN(service_date) d FROM findings WHERE {cond} AND status IN ('open','in_progress') AND COALESCE(stale,0)=0
                AND claim_key IS NOT NULL AND (appeal_deadline IS NULL OR appeal_deadline >= ?)""", tuple(params) + (_day(0),))
    d = (r or {}).get("d")
    return str(d)[:10] if d and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(d)[:10]) else None


def _note_ready(items: list[dict]) -> dict[str, str]:
    """For items that rest on the visit note: whether an uploaded note covers each ('found', 'ambiguous' or 'not_found'),
    as an appeal would find it. Items on a date no uploaded note is for are not looked at."""
    from . import modmed, uploads
    if runtime.serverless() or not items:
        return {}
    conn = db()
    try:
        days = uploads.note_days(conn)
        todo = [i for i in items if i.get("service_date") in days and i.get("claim_key")]
        if not todo:
            return {}
        src = uploads.UploadSource(conn)
        keys = sorted({i["claim_key"] for i in todo})
        claims: dict[str, dict] = {}
        for k in range(0, len(keys), 900):
            part = keys[k:k + 900]
            for r in conn.execute(f"SELECT * FROM claims WHERE claim_key IN ({','.join('?' * len(part))})", part):
                claims[r["claim_key"]] = dict(r)
        pcns = sorted({i.get("patient_control_number") for i in todo if i.get("patient_control_number")})
        ids = uploads._ema_ids(conn, pcns)
        out = {}
        for i in todo:
            key = modmed.claim_key({"finding": i}, claims.get(i["claim_key"]), sorted(ids.get(i.get("patient_control_number"), ())))
            out[i["finding_id"]] = modmed.find_note(src, key)["status"]
        return out
    finally:
        conn.close()


def _has_packet(finding_id: str) -> bool:
    if runtime.serverless():
        return bool(_packets_for(finding_id))
    return bool(has_table("appeal_packets") and one("SELECT 1 FROM appeal_packets WHERE finding_id=? LIMIT 1", (finding_id,)))


@app.post("/api/appeals/from_ema", dependencies=[Depends(require_xhr)])
def appeals_from_ema(body: dict = Body(default={}), _: str = Depends(require_auth)):
    """Find the visit note for one finding's claim in ModMed EMA and build the appeal packet from it, as an uploaded note
    would be (remitwatch.modmed). When a person has to choose, the candidates come back and the person picks one
    (document_id): more than one note that day, only the name agrees with the claim, two patients fit (with names off: no
    candidates, the note is attached by hand), or the only documents are a later note or not a visit note.
    skip_if_packet: leave a finding that already has a packet (the Worklist's batch)."""
    from . import modmed
    fid = str(body.get("finding_id") or "")
    if not re.fullmatch(r"[A-Za-z0-9:_\-.]{1,120}", fid):
        raise HTTPException(400, "finding_id is required")
    detail = finding_detail(fid, "ok")
    f = detail["finding"]
    if not f.get("claim_key"):
        raise HTTPException(400, "This item is not tied to one claim, so it has no visit note to read.")
    if body.get("skip_if_packet") and _has_packet(fid):
        if runtime.serverless():
            _queue_mark(fid, "packet", "An appeal packet was built for it.")     # the queue leaves it out from now on
        return {"status": "skipped", "reason": "It already has a packet."}
    raw_claim = one("SELECT * FROM claims WHERE claim_key=?", (f["claim_key"],))
    key = modmed.claim_key(detail, raw_claim, _ema_patient_ids(f.get("patient_control_number")))
    pick = body.get("document_id")
    since = body.get("since")
    try:
        res = _note_lookup(key, pick, since=str(since)[:10] if since and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(since)[:10]) else None)
    except modmed.NotConfigured as exc:
        raise HTTPException(503, str(exc))
    except modmed.ModMedError as exc:
        raise HTTPException(502, str(exc))
    out = {k: v for k, v in res.items() if k != "note"}
    if res["status"] != "found":
        if body.get("skip_if_packet"):         # the batch: the next press, and the nightly run, go on to the items behind it
            _queue_mark(fid, "note_lookup", "Build packets from EMA: " + (res.get("reason") or (
                "more than one note fits, a person picks one in the item" if res["status"] == "ambiguous" else "no signed note found")))
        return out
    note = res["note"]
    packet = _store_packet(detail, raw_claim, note["filename"], note["data"], None, _yes_ai(body.get("use_ai", True)),
                           source="upload" if res.get("source") == "upload" else "ema")
    out.update(status="built", packet=packet, note={k: v for k, v in note.items() if k != "data"})
    return out


@app.get("/api/appeals/ema_queue")
def appeals_ema_queue(limit: int = Query(20, ge=1, le=200), ready: int = Query(0, ge=0, le=1), _: str = Depends(require_auth)):
    """What "Build packets from EMA" works through: the items that need the visit note, open or started, still inside
    their filing window (or with none known), soonest window first and then the largest, without a packet yet where the
    store can tell (on a serverless host each item is checked as it is built). ready=1 (a server): only the items an
    uploaded note covers."""
    from .insights import kind_sql
    cond, params = kind_sql("note")
    skip_built = not runtime.serverless() and has_table("appeal_packets")
    # left out as well: items with a packet on a serverless host (marked when it was built), and items the batch found no
    # note (or more than one) for in the last few days: without that, every press works the same first items again
    marks = has_table("finding_events")
    since = (datetime.now(timezone.utc) - timedelta(days=QUEUE_RETRY_DAYS)).isoformat(timespec="seconds")
    sql = f"""SELECT finding_id, service_date, appeal_deadline, payer_group, shortfall_cents{", claim_key, patient_control_number" if ready else ""} FROM findings
              WHERE {cond} AND status IN ('open','in_progress') AND COALESCE(stale,0)=0 AND claim_key IS NOT NULL
                AND (appeal_deadline IS NULL OR appeal_deadline >= ?)
                {"AND finding_id NOT IN (SELECT finding_id FROM appeal_packets WHERE finding_id IS NOT NULL)" if skip_built else ""}
                {"AND finding_id NOT IN (SELECT finding_id FROM finding_events WHERE kind='packet' OR (kind='note_lookup' AND at >= ?))" if marks else ""}
              ORDER BY COALESCE(appeal_deadline, '9999-12-31'), COALESCE(shortfall_cents, 0) DESC, finding_id {"" if ready else "LIMIT ?"}"""
    args = (*params, _day(0), *((since,) if marks else ()))
    items = rows(sql, args if ready else (*args, limit))
    if ready:
        got = _note_ready(items)
        items = [{k: v for k, v in i.items() if k not in ("claim_key", "patient_control_number")}
                 for i in items if got.get(i["finding_id"]) == "found"][:limit]       # an ambiguous one needs a person
    return {"items": items, "since": min((i["service_date"] for i in items if i.get("service_date")), default=None)}


# --------------------------------------------------------------------------- files brought in by hand (remitwatch.uploads)
def _uploads_here() -> None:
    """Uploads are kept beside the store, so only a server takes them; a serverless host says so plainly."""
    if runtime.serverless():
        from .uploads import SERVER_ONLY
        raise HTTPException(409, SERVER_ONLY)


def _wake_cycle(trigger: str = "upload") -> None:
    """The next cycle reads what was uploaded. The scheduler waits its usual minute for the rest of a batch; on a server
    that had no feed to start it, it is started now (and from then on runs a cycle a day as well)."""
    from .pipeline import DAILY_MINUTES, SCHEDULER
    if not SCHEDULER.is_alive():
        SCHEDULER.start(DB_PATH, DAILY_MINUTES)
    SCHEDULER.poke(trigger)


@app.post("/api/uploads", dependencies=[Depends(require_xhr)])
async def uploads_post(request: Request, name: Optional[str] = Query(None, max_length=300),
                       finding_id: Optional[str] = Query(None, max_length=160), user: str = Depends(require_auth)):
    """One file as the request's body (the page sends each file on its own): an EMA export, a remittance or claim file,
    a zip of those, or a visit note (with finding_id: attached to that item). Its bytes go straight into the upload
    folder beside the store; the answer says what it holds, what is new, what RemitWatch has already, and what was left
    out and why. An export or a remittance file goes in with the next cycle, which this wakes. The file's name comes in
    the X-File-Name header (URL-encoded), so that a note saved under the patient's name is not written to the access
    log as part of the address; the `name` query is still read from a page that does not send the header yet."""
    _uploads_here()
    from urllib.parse import unquote
    from . import uploads
    header = request.headers.get("x-file-name")
    name = (unquote(header).strip()[:300] if header else (name or "").strip()) or None
    if not name:
        raise HTTPException(400, "The file's name is missing (X-File-Name).")
    if finding_id and not re.fullmatch(FAX_KEY, finding_id):
        raise HTTPException(400, "finding_id is not valid")
    tmp = uploads.incoming(DB_PATH)
    h, size = hashlib.sha256(), 0
    try:
        with open(tmp, "wb") as fh:
            async for chunk in request.stream():
                size += len(chunk)
                if size > uploads.MAX_BYTES:
                    raise HTTPException(413, f"A file can be at most {uploads.MAX_BYTES // (1024 * 1024)} MB. Export a shorter date range.")
                h.update(chunk)
                fh.write(chunk)
        if not size:
            raise HTTPException(400, "The file is empty.")
        res = await asyncio.to_thread(uploads.accept, DB_PATH, tmp, name, user, h.hexdigest(), finding_id or None, None, None, SHOW_PHI)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    if res.get("status") == "queued" or res.get("queued"):
        try:
            _wake_cycle()
        except Exception:
            pass            # the scheduler's own daily cycle reads it anyway
    return JSONResponse(res, headers={"Cache-Control": "no-store"})


@app.get("/api/uploads")
def uploads_list(_: str = Depends(require_auth)):
    """The Upload page: what was uploaded and what became of each file, what waits for the cycle, and the visit notes."""
    from . import uploads
    if runtime.serverless():
        return {"server": False, "message": uploads.SERVER_ONLY}
    from .pipeline import LAST_KEY, LOCK_KEY, SCHEDULER
    conn = db()
    try:
        items = uploads.history(conn, reveal=SHOW_PHI)
        queued = conn.execute("SELECT COUNT(*) FROM uploads WHERE status='queued'").fetchone()[0]
        notes = {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM uploaded_notes GROUP BY 1")}
        meta = {r[0]: r[1] for r in conn.execute("SELECT key, value FROM meta WHERE key IN (?, ?)", (LOCK_KEY, LAST_KEY))}
    finally:
        conn.close()
    last = json.loads(meta.get(LAST_KEY) or "{}")
    lock = json.loads(meta.get(LOCK_KEY) or "{}")
    return {"server": True, "items": items, "queued": queued, "notes": notes, "show_phi": SHOW_PHI,
            "name_header": True,         # the page sends a file's name in X-File-Name, not in the address
            "cycle": {"running": bool(lock), "since": lock.get("started"), "last_finished": last.get("finished"),
                      "last_error": last.get("error"), "next": SCHEDULER.next_run if SCHEDULER.is_alive() else None}}


@app.get("/api/uploads/needed")
def uploads_needed(limit: int = Query(100, ge=1, le=500), _: str = Depends(require_auth)):
    """The visit notes appeals wait for: open items that rest on the note, inside their filing window, with no packet
    yet, soonest window first; each says whether an uploaded note already covers it (as an appeal would find it)."""
    _uploads_here()
    from .insights import kind_sql
    cond, params = kind_sql("note")
    built = "AND finding_id NOT IN (SELECT finding_id FROM appeal_packets WHERE finding_id IS NOT NULL)" if has_table("appeal_packets") else ""
    items = rows(f"""SELECT finding_id, claim_key, patient_control_number, service_date, appeal_deadline, payer_group, shortfall_cents,
                            procedure_code, title FROM findings
                     WHERE {cond} AND status IN ('open','in_progress') AND COALESCE(stale,0)=0 AND claim_key IS NOT NULL
                       AND (appeal_deadline IS NULL OR appeal_deadline >= ?) {built}
                     ORDER BY COALESCE(appeal_deadline, '9999-12-31'), COALESCE(shortfall_cents, 0) DESC, finding_id""", tuple(params) + (_day(0),))
    got = _note_ready(items)
    names = {}
    if SHOW_PHI and items:
        keys = sorted({i["claim_key"] for i in items[:limit]})
        names = {r["claim_key"]: _name(r["patient_last"], r["patient_first"])
                 for r in rows(f"SELECT claim_key, patient_last, patient_first FROM claims WHERE claim_key IN ({','.join('?' * len(keys))})", tuple(keys))}
    out = []
    for i in items[:limit]:
        out.append({"finding_id": i["finding_id"], "claim_id": i["patient_control_number"], "service_date": i["service_date"],
                    "appeal_deadline": i["appeal_deadline"], "payer_group": i["payer_group"], "cents": i["shortfall_cents"] or 0,
                    "code": i["procedure_code"], "title": i["title"], "note": got.get(i["finding_id"], "not_found"),
                    **({"patient": names.get(i["claim_key"])} if SHOW_PHI else {})})
    return {"items": out, "total": len(items), "covered": sum(1 for v in got.values() if v == "found")}


@app.post("/api/uploads/notes/{note_id}/match", dependencies=[Depends(require_xhr)])
async def uploads_note_match(note_id: str, request: Request, _: str = Depends(require_auth)):
    """A person settles which visit an uploaded note is for: one of the visits it could be (pick), or an item (finding_id).
    The body is read after the sign-in, and at most 64 KB of it (api_uploads._json_body): a Body() parameter would be read
    and parsed before the sign-in, so anyone could make the server parse a large body (needs_A2 F.2)."""
    from . import api_uploads
    body = await api_uploads._json_body(request)
    return await asyncio.to_thread(_uploads_note_match, note_id, body)


def _uploads_note_match(note_id: str, body: dict):
    _uploads_here()
    from . import uploads
    if not re.fullmatch(r"[0-9a-f]{24}", note_id):
        raise HTTPException(400, "note_id is not valid")
    fid = body.get("finding_id")
    if fid is not None and not re.fullmatch(FAX_KEY, str(fid)):
        raise HTTPException(400, "finding_id is not valid")
    conn = db()
    try:
        n = uploads.choose(conn, note_id, pick=int(body["pick"]) if str(body.get("pick", "")).isdigit() else None,
                           finding_id=str(fid) if fid else None)
        # read before the store closes: the answer's confirm flag compares the note's identifiers with the claim's
        return {"note": uploads._note_public(n, SHOW_PHI, conn), "lines": uploads._note_lines(n, SHOW_PHI, conn)}
    except uploads.Rejected as exc:
        raise HTTPException(400, str(exc))
    finally:
        conn.close()


@app.post("/api/uploads/notes/{note_id}/remove", dependencies=[Depends(require_xhr)])
def uploads_note_remove(note_id: str, _: str = Depends(require_auth)):
    """Take an uploaded note back (the wrong file, or the wrong visit). A packet built from it stays with its item for the
    record, but it is never faxed: the next appeal builds it again (_packet_problem)."""
    _uploads_here()
    from . import uploads
    if not re.fullmatch(r"[0-9a-f]{24}", note_id):
        raise HTTPException(400, "note_id is not valid")
    conn = db()
    try:
        if not uploads.remove_note(conn, note_id):
            raise HTTPException(404, "No such note.")
    finally:
        conn.close()
    return {"removed": True}


@app.get("/api/uploads/notes/{note_id}/file")
def uploads_note_file(note_id: str, _: str = Depends(require_auth)):
    """The uploaded note itself, to check it is the right one."""
    _uploads_here()
    if not re.fullmatch(r"[0-9a-f]{24}", note_id):
        raise HTTPException(400, "note_id is not valid")
    n = one("SELECT name, path, content_type FROM uploaded_notes WHERE note_id=?", (note_id,)) if has_table("uploaded_notes") else None
    if not n or not n["path"] or not os.path.exists(n["path"]):
        raise HTTPException(404, "No such note.")
    ctype = n["content_type"] if n["content_type"] in ("application/pdf", "text/plain") else "application/octet-stream"
    return FileResponse(n["path"], media_type=ctype, headers={"Cache-Control": "no-store", "Content-Disposition":
                        f"{'inline' if ctype != 'application/octet-stream' else 'attachment'}; filename=note-{note_id[:8]}{os.path.splitext(n['path'])[1]}"})


@app.post("/api/modmed/test", dependencies=[Depends(require_xhr)])
def modmed_test(_: str = Depends(require_auth)):
    """Every step of the ModMed connection, for /setup: settings, key, discovery, sign-in, server, search. No patient
    data is read into the answer."""
    from . import modmed
    return JSONResponse(modmed.check(), headers={"Cache-Control": "no-store"})


@app.post("/api/trizetto/test", dependencies=[Depends(require_xhr)])
def trizetto_test(_: str = Depends(require_auth)):
    """Every step of the TriZetto connection, for /setup: settings, the server and its key, the sign-in, the files there,
    one remittance read. Only file names, counts and dates come back. While TriZetto's refusal of these settings is held
    (trizetto.refusal) the test does not sign in: each press would send the refused password again, and TriZetto locks a
    login after a few failures (it may be the login ModMed uses too). It says until when instead. A new password is tried
    at once, and the hold ends by itself a day after the refusal."""
    from . import trizetto
    # the store that holds the sign-in hold and the server's key: the live store on a server; on a serverless host this
    # instance's copy of the dashboard's tables (cloud.SERVING_TABLES carries meta)
    conn = db()
    try:
        try:
            r = conn.execute("SELECT value FROM meta WHERE key=?", (trizetto.HOSTKEY_META,)).fetchone()
            known = r[0] if r else None
        except sqlite3.OperationalError:
            known = None                        # no meta table yet: a store before its first run
        out = trizetto.check(known=known, conn=conn)
    finally:
        conn.close()
    if out.get("held") or out.get("not_here"):
        return JSONResponse(out, headers={"Cache-Control": "no-store"})     # nothing was tried, so nothing changes
    # a sign-in that works now ends a hold left by an earlier refusal (whatever else is still to fix); a refusal here holds the
    # scheduled runs for a day from now; a hold still in place is shown. On a server, in the store the cycles read; on a
    # serverless host, through the journal (this instance's copy at once, the store at the next run)
    signed_in = any(x["step"] == "Sign-in" and x["ok"] for x in out.get("steps", []))
    kind = str(out.get("refused_kind") or "")
    if runtime.serverless():
        from .cloud import SERVING_COPY
        try:
            if SERVING_COPY.state() is None:
                pass                            # no storage yet: nothing to hold, and the steps still come back
            elif signed_in:
                _record_action({"kind": "trizetto_hold", "op": "clear"})
            elif out.get("refused"):
                _record_action({"kind": "trizetto_hold", "op": "set", "who": trizetto.credential_tag(trizetto.Settings.from_env()),
                                "detail": out["refused"], "refused_kind": kind})
        except Exception as exc:
            out["hold_note"] = f"The hold could not be recorded just now ({type(exc).__name__}); the next run decides by itself."
    elif has_table("meta") or out.get("refused"):
        conn = db()
        try:
            if signed_in:
                trizetto.forget_refusal(conn)
            elif out.get("refused"):
                trizetto.remember_refusal(conn, trizetto.Settings.from_env(), out["refused"], kind=kind)
        finally:
            conn.close()
    if not signed_in and has_table("meta"):
        conn = db()
        try:
            held = trizetto.refusal(conn)
        finally:
            conn.close()
        if held:
            out["held"] = held
    return JSONResponse(out, headers={"Cache-Control": "no-store"})


@app.get("/.well-known/jwks.json", include_in_schema=False)
def jwks_document():
    """The public key ModMed checks the app's sign-in against (MODMED_PRIVATE_KEY's public half; never the private one),
    for a registration that asks for a JWKS URL. Public on purpose: it is a public key."""
    from . import modmed
    try:
        doc = modmed.jwks()
    except modmed.ModMedError as exc:
        raise HTTPException(503, str(exc))
    if not doc:
        raise HTTPException(404, "No key is set yet: add MODMED_PRIVATE_KEY in Vercel (Settings, Environment Variables), then redeploy."
                            if runtime.serverless() else
                            "No key is set yet: add MODMED_PRIVATE_KEY on the server with sudo bash /opt/remitwatch/deploy/aws/configure.sh, which restarts the site.")
    return JSONResponse(doc, headers={"Cache-Control": "public, max-age=300"})


# --------------------------------------------------------------------------- appeals by fax (remitwatch.fax)
FAX_KEY = r"[A-Za-z0-9:_\-.]{1,160}"
FAX_PURPOSES = ("appeal", "level_2")
STATUS_WORDS = {"appealed": "already sent", "resolved": "paid or settled", "lost": "marked lost", "dismissed": "let go"}
# one fax run at a time on a site (the hourly run, the daily call, the Faxes page's buttons): a row in the store's meta
# table, as the cycle's own lock (pipeline:lock) is, so the deploy script can wait for it too; on a serverless host a file
# in the state folder
FAX_LOCK_KEY = "fax:lock"
FAX_LOCK_FILE = "fax-lock.json"
FAX_LOCK_STALE_S = 15 * 60          # a lock nobody has refreshed for this long was left by a run that died
RESERVE_STALE_S = 10 * 60           # a send still not settled after this long is asked of the fax service (_fax_refresh)
MAX_READS = 3                       # tries at reading a payer's fax before it is left for a person
RESEND = ("second_request", "next_level", "records", "payment")       # what a person can send again in one click


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


STALE_APPEAL_DAYS = int(os.environ.get("REMITWATCH_STALE_APPEAL_DAYS", "14"))


def _feed_day() -> str | None:
    """The day the remittance feed reaches for most payers (insights.feed_day, the one the Data tab's feed check and the
    pages' warning line show): worked out from the store on a server; on a serverless host's copy the date the last
    analysis worked out on the full store. Never after today where the practice is (a future-dated file counts as today)."""
    if not has_table("transactions"):
        return None
    from .insights import feed_day
    conn = db()
    try:
        return feed_day(conn, _day(0))
    finally:
        conn.close()


def _feed_gap() -> dict | None:
    """The weeks with no remittance from any payer that the day above stops at (insights.feed_hole), else None: the fax hold
    names them and what to do, not an age the newest remittance does not have (misuse check CH-6)."""
    if not has_table("transactions"):
        return None
    from .insights import feed_hole
    conn = db()
    try:
        return feed_hole(conn, _day(0))
    finally:
        conn.close()


def _data_behind_days() -> int | None:
    """Days since the day the remittances reach for most payers (None without remittances): past two weeks or so, a claim
    on the lists may have been paid since and nobody here can see it. One stray newer file from one payer does not lift it.
    Counted to the practice's day, as the stale-feed alert counts (the UTC date is a day ahead from 7 PM Eastern)."""
    d = _feed_day()
    try:
        return (datetime.fromisoformat(_day(0)).date() - datetime.fromisoformat(d[:10]).date()).days if d else None
    except ValueError:
        return None


def _fax_now() -> str:
    """A fax's time to the microsecond: one daily run can read the payer's answer and send what it calls for in the same
    second, and the follow-ups go by which came first."""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _record_action(action: dict) -> None:
    """A fax, a payer's fax number, a lesson or a line in an item's history: into the store, or on a serverless host
    into the journal (which is what makes it last) and this instance's copy."""
    action = dict(action, at=action.get("at") or _now_iso())
    if runtime.serverless():
        from .cloud import SERVING_COPY
        SERVING_COPY.record(action)
        return
    from . import actions as actions_mod
    conn = db()
    try:
        actions_mod.apply(conn, action)
        conn.commit()
    finally:
        conn.close()


def _appealable_cond() -> tuple[str, list]:
    """The items an appeal letter settles (remitwatch.fax.APPEALABLE), as a condition on the findings table."""
    from . import fax
    from .insights import kind_sql
    parts, params = [], []
    for k in fax.APPEALABLE:
        c, p = kind_sql(k)
        parts.append(c)
        params += p
    return "(" + " OR ".join(parts) + ")", params


def _payer_number(payer_group: str | None, purpose: str = "appeal") -> str:
    """The number saved now for a payer and purpose: '' when there is none (or it was removed), and for a group that holds
    many insurers (fax.shared_group), whatever was saved for it before."""
    from . import fax
    if not payer_group or not has_table("payer_fax") or fax.shared_group(payer_group):
        return ""
    return fax.digits((one("SELECT number FROM payer_fax WHERE payer_group=? AND purpose=?", (payer_group, purpose)) or {}).get("number"))


def _practice_missing() -> list[str]:
    """The practice's details a letter still lacks: no letter is faxed with '[practice name]' on it."""
    from . import fax
    from .appeals import PRACTICE
    names = {"name": "PRACTICE_NAME", "address": "PRACTICE_ADDRESS", "tin": "PRACTICE_TIN", "npi": "PRACTICE_NPI",
             "phone": "PRACTICE_PHONE", "fax": "SRFAX_CALLER_ID (or PRACTICE_FAX)", "email": "PRACTICE_EMAIL"}
    return [names[k] for k, v in PRACTICE.items() if k in names and fax.letter_blanks(v)]


def _stored_packet(packet_id: str) -> dict | None:
    """A stored packet with its PDF, and the letter as it went to the payer (with the patient's name)."""
    if runtime.serverless():
        cloud, st = _cloud_state()
        return cloud.get_packet(st, packet_id, with_pdf=True, reveal=True)
    from .notes import get_packet
    conn = db()
    try:
        return get_packet(conn, packet_id, with_pdf=True, reveal=True)
    finally:
        conn.close()


def _packet_meta(packet_id: str) -> dict | None:
    """What a stored packet was built from, without its PDF: the review, the note's fingerprint, when."""
    if runtime.serverless():
        cloud, st = _cloud_state()
        p = cloud.get_packet(st, packet_id)
        return p if p else None
    if not has_table("appeal_packets"):
        return None
    p = one("SELECT packet_id, finding_id, created_at, mode, analysis, note_sha256 FROM appeal_packets WHERE packet_id=?", (packet_id,))
    if p:
        try:
            p["analysis"] = json.loads(p.get("analysis") or "{}")
        except ValueError:
            p["analysis"] = {}
    return p


def _same_name(a: str | None, b: str | None) -> bool:
    n = lambda s: re.sub(r"[^A-Z0-9]", "", (s or "").upper())      # noqa: E731
    return bool(n(a)) and n(a) == n(b)


def _packet_problem(packet_id: str | None, f: dict, first: bool = False) -> str | None:
    """Why a stored packet must not be faxed as it is (it can be built again, or a person takes it), or None: the uploaded
    note it rests on was taken back or matched to a visit again since it was built; or Claude's review in it was written
    before its quotations were checked against the note. first: before its first fax, another note uploaded for the visit
    since counts too (the packet is built again, and a person picks when two notes fit)."""
    meta = _packet_meta(packet_id) if packet_id else None
    if not meta:
        return "The packet cannot be found."
    a = meta.get("analysis") or {}
    if a.get("mode") == "ai" and "quote_check" not in a:
        return "It was built before Claude's quotations were checked against the note."
    if runtime.serverless() or not has_table("uploaded_notes") or meta.get("mode") == "letter":
        return None         # notes are uploaded on a server only; a letter on the payer's own rate rests on no note
    built, sha = meta.get("created_at") or "", meta.get("note_sha256")
    if sha:
        up = one("SELECT status FROM uploads WHERE upload_id=?", (sha,)) if has_table("uploads") else None
        n = one("SELECT status, matched_at FROM uploaded_notes WHERE sha256=?", (sha,))
        if (up and up.get("status") == "removed") or (up and not n):
            return "The note it was built from was taken back."
        if n and n.get("status") != "matched":
            return "The note it was built from is no longer matched to a visit."
        if n and (n.get("matched_at") or "") > built:
            return "The note it was built from was matched to a visit again since."
    claim = one("SELECT patient_last FROM claims WHERE claim_key=?", (f.get("claim_key"),)) if (first and f.get("claim_key")) else None
    if claim and f.get("service_date"):
        for r in rows("""SELECT patient_last, matched_at FROM uploaded_notes WHERE status='matched' AND service_date=? AND COALESCE(sha256,'') != ?""",
                      (f["service_date"], sha or "")):
            if (r["matched_at"] or "") > built and _same_name(r["patient_last"], claim["patient_last"]):
                return "Another note for this visit was uploaded since."
    return None


def _em_below(supported: str | None, billed: str | None) -> bool:
    """Whether the visit code the note supports is below the one billed (office visits, new or established)."""
    s, b = str(supported or ""), str(billed or "")
    return bool(re.fullmatch(r"992[01]\d", s) and re.fullmatch(r"992[01]\d", b)) and s[:4] == b[:4] and s < b


def _review_doubts(a: dict, detail: dict) -> str | None:
    """What in Claude's review stops an appeal from going by itself: a caution, a note that supports the code only in part or
    not at all, or a lower code than the one billed. Such a packet would tell the payer the practice concedes it."""
    if a.get("mode") != "ai":
        return None
    from .notes import dispute_context
    out = []
    if a.get("recommendation") == "appeal_with_caution":
        out.append("it says to appeal only with caution")
    if a.get("supports_billed") == "no":
        out.append("it finds the note does not support the code billed")
    elif a.get("supports_billed") == "borderline":
        out.append("it finds the note supports the code billed only in part")
    billed = dispute_context(detail)["billed_code"]
    if _em_below(a.get("supported_code"), billed):
        out.append(f"it finds the note supports {a.get('supported_code')}, below the {billed} billed")
    return "; ".join(out) if out else None


def _quotes_problem(a: dict) -> str | None:
    """A review whose quotations were not found in the note (or could not be checked): never faxed by itself."""
    if a.get("mode") != "ai":
        return None
    qc = a.get("quote_check") or {}
    if qc.get("checked") and not qc.get("missing"):
        return None
    if not qc.get("checked"):
        return qc.get("why") or "Claude's quotations could not be checked against the note."
    return "Claude's paragraphs quote words the note does not have: " + "; ".join(f'"{q[:80]}"' for q in qc["missing"][:3]) + "."


def _faxes_out(finding_id: str, live: bool = True) -> list[dict]:
    """The faxes sent for an item, oldest first; live: leaving out those the payer's line never took."""
    if not has_table("faxes"):
        return []
    return rows("SELECT * FROM faxes WHERE finding_id=? AND direction='out'" + (" AND COALESCE(status,'')!='failed'" if live else "")
                + " ORDER BY created_at", (finding_id,))


def _mark_sent_appeal(r: dict, actor: str) -> bool:
    """An item whose appeal went out by fax but was never marked Sent, because the send did not settle at once (SRFax took
    it but its answer was lost, or the run stopped between the send and its record): marked now, as the send would have
    done, so its follow-ups start. Not when a person changed the item's status since the fax was reserved, or the item is
    no longer open. r: the fax's row (purpose 'appeal'). Returns whether it was marked."""
    from . import fax
    fid = r.get("finding_id")
    if r.get("purpose") != "appeal" or not fid:
        return False
    f = one("SELECT status, payer_group FROM findings WHERE finding_id=?", (fid,))
    if not f or f.get("status") not in ("open", "in_progress"):
        return False
    if has_table("finding_events") and one("SELECT 1 FROM finding_events WHERE finding_id=? AND kind='manual' AND at >= ? LIMIT 1",
                                           (fid, (r.get("created_at") or "")[:19])):
        return False
    _record_status([fid], "appealed", f"Faxed to {f.get('payer_group') or 'the payer'} at {fax.pretty(r.get('number'))} "
                                      f"(fax {r.get('provider_id') or 'not settled yet'}).", None, actor)
    return True


SENT_KEPT = "It was {label} by the time the fax went out, so its status was left as it was. Look at it: the payer has this appeal now."


def _sent_once_faxed(fid: str, note: str, actor: str, again: bool) -> str | None:
    """After an appeal went out: the item is marked Sent only if it is still To do or Started (or, sent again after every try
    failed, still Sent), the check _mark_sent_appeal makes. One that changed while SRFax had the fax (the run closed it as
    Paid, a reason hidden for its payer let it go) keeps its status: the fax is on record already (_send_fax), a line in its
    history says it went out, and a person decides. On a server the check and the write are one step on the store; on a
    serverless host the check is on the copy it serves, and the journal's fold keeps a run's change too
    (actions.apply_status). Returns why it was not marked Sent, or None."""
    from .actions import apply_event, heir_of
    fine = ("open", "in_progress") + (("appealed",) if again else ())

    def why(row) -> str | None:
        return None if row is None or row[0] in fine else SENT_KEPT.format(label=STATUS_LABELS.get(row[0], row[0]))
    if runtime.serverless():
        conn = db()
        try:
            kept = why(conn.execute("SELECT status FROM findings WHERE finding_id=?", (heir_of(conn, fid),)).fetchone())
        finally:
            conn.close()
        if kept:
            _record_action({"kind": "event", "event": "fax", "finding_id": fid, "actor": actor, "note": f"{note} {kept}"})
        else:
            _record_status([fid], "appealed", note, None, actor)
        return kept
    conn = sqlite3.connect(DB_PATH, timeout=60, isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            kept = why(conn.execute("SELECT status FROM findings WHERE finding_id=?", (heir_of(conn, fid),)).fetchone())
            if kept:
                apply_event(conn, {"finding_id": fid, "event": "fax", "actor": actor, "note": f"{note} {kept}"})
            else:
                apply_status(conn, fid, "appealed", note, None, actor)
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    return kept


# ---- an appeal being prepared and sent: in flight from the first step (the note looked up, Claude's review) to the end of
# the send, so a person's status change and a second press wait for it instead of undoing it. A row in the store's meta
# table on a server; on a serverless host that sends, a marker file in the state folder that every instance can see.
IN_FLIGHT_S = RESERVE_STALE_S           # longer than any send takes: a marker or a reservation older than this is not in flight
PREP_KEY = "fax:prep:"
PREP_DIR = "fax-prep"


def _prep_name(fid: str) -> str:
    return f"{PREP_DIR}/{hashlib.sha256(str(fid).encode()).hexdigest()[:24]}.json"


def _young(at: str | None) -> bool:
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(at)).total_seconds() < IN_FLIGHT_S
    except (TypeError, ValueError):
        return False


def _prep_take(fid: str) -> str | None:
    """Mark an appeal for this item as being prepared; None when one already is (and is not older than a send takes)."""
    token = secrets.token_hex(8)
    body = json.dumps({"token": token, "at": _now_iso()})
    if runtime.serverless():
        from .state import Conflict
        cloud, st = _cloud_state()
        name = _prep_name(fid)
        for _ in range(2):
            try:
                st.put(name, body.encode(), mode="add")
                return token
            except Conflict:
                m = st.meta(name)
                if m is None:
                    continue
                if cloud._age_seconds(m.get("modified")) < IN_FLIGHT_S:
                    return None
                try:
                    st.put(name, body.encode(), mode="update", rev=m["rev"])
                    return token
                except Conflict:
                    return None
        return None
    conn = sqlite3.connect(DB_PATH, timeout=60, isolation_level=None)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("BEGIN IMMEDIATE")
        try:
            r = conn.execute("SELECT value FROM meta WHERE key=?", (PREP_KEY + fid,)).fetchone()
            try:
                held = json.loads(r[0]) if r else {}
            except ValueError:
                held = {}
            if held and _young(held.get("at")):
                conn.execute("ROLLBACK")
                return None
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (PREP_KEY + fid, body))
            conn.execute("COMMIT")
            return token
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def _prep_release(fid: str, token: str) -> None:
    try:
        if runtime.serverless():
            cloud, st = _cloud_state()
            raw = st.get(_prep_name(fid))
            if raw and json.loads(raw).get("token") == token:
                st.delete(_prep_name(fid))
            return
        conn = sqlite3.connect(DB_PATH, timeout=30)
        try:
            conn.execute("DELETE FROM meta WHERE key=? AND value LIKE ?", (PREP_KEY + fid, f'%"token": "{token}"%'))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass            # a marker left behind is not in flight once it is older than a send takes


def _preparing(conn: sqlite3.Connection, ids: list[str]) -> set[str]:
    """Which of these items have an appeal being prepared now (_prep_take)."""
    if not ids:
        return set()
    if runtime.serverless():
        from . import fax
        if fax.provider() is None:
            return set()            # this host sends nothing: nothing of it can be in flight here
        cloud, st = _cloud_state()
        want = {_prep_name(i): i for i in ids}
        try:
            listed = st.list(PREP_DIR)
        except Exception:
            return set()
        return {want[m["name"]] for m in listed if m.get("name") in want and cloud._age_seconds(m.get("modified")) < IN_FLIGHT_S}
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone():
        return set()
    out: set[str] = set()
    for k in range(0, len(ids), 500):
        part = [PREP_KEY + i for i in ids[k:k + 500]]
        for key, value in conn.execute(f"SELECT key, value FROM meta WHERE key IN ({','.join('?' * len(part))})", part):
            try:
                if _young(json.loads(value).get("at")):
                    out.add(key[len(PREP_KEY):])
            except (TypeError, ValueError, AttributeError):
                pass
    return out


def _fax_doc(prov, f: dict, number: str, packet_pdf: bytes, subject: str | None, front: bytes | None = None,
             extra: bytes | None = None, level: int = 1) -> tuple[bytes, int]:
    """What goes down the line: the cover sheet (to the desk of that level), a follow-up's letter when there is one, the
    packet, and anything the payer sent that the next level needs. Returns the PDF and its pages."""
    from . import fax
    from .appeals import PRACTICE, _desk
    body = fax.merge_pdfs(*(p for p in (front, packet_pdf, extra) if p))
    pages = fax.page_count(body) + 1
    sheet = fax.cover_sheet(f"{f.get('payer_group') or f.get('payer_name') or ''}, {_desk(f, level)}", number, PRACTICE, pages, subject)
    return fax.merge_pdfs(sheet, body), pages


# ---- one fax run at a time on a site
class FaxBusy(Exception):
    pass


def _fax_lock_take(who: str) -> str | None:
    """Take the fax run's lock; None when another run holds it. One nobody refreshed for FAX_LOCK_STALE_S is taken over."""
    token = secrets.token_hex(8)
    now = _now_iso()
    # the container it runs in, as the cycle's lock says it (pipeline._host): a fax run in another container (`docker compose
    # run ... fax-cycle`) is judged by its heartbeat there, never by a process number that means nothing here
    from .pipeline import _host
    body = {"token": token, "started": now, "heartbeat": now, "pid": os.getpid(), "who": str(who)[:60], "host": _host()}
    if runtime.serverless():
        from .state import Conflict
        cloud, st = _cloud_state()
        for _ in range(2):
            try:
                st.put(FAX_LOCK_FILE, json.dumps(body).encode(), mode="add")
                return token
            except Conflict:
                m = st.meta(FAX_LOCK_FILE)
                if m is None:
                    continue
                if cloud._age_seconds(m.get("modified")) < FAX_LOCK_STALE_S:
                    return None
                try:
                    st.put(FAX_LOCK_FILE, json.dumps(body).encode(), mode="update", rev=m["rev"])
                    return token
                except Conflict:
                    return None
        return None
    conn = sqlite3.connect(DB_PATH, timeout=60, isolation_level=None)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("BEGIN IMMEDIATE")
        try:
            r = conn.execute("SELECT value FROM meta WHERE key=?", (FAX_LOCK_KEY,)).fetchone()
            if r:
                try:
                    held = json.loads(r[0])
                    beat = datetime.fromisoformat(held.get("heartbeat") or held["started"])
                    fresh = (datetime.now(timezone.utc) - beat).total_seconds() < FAX_LOCK_STALE_S
                except Exception:
                    fresh = False
                if fresh:
                    conn.execute("ROLLBACK")
                    return None
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (FAX_LOCK_KEY, json.dumps(body)))
            conn.execute("COMMIT")
            return token
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def _fax_lock_beat(token: str | None) -> None:
    """A long run says it is alive, so its lock is not taken over. A serverless run cannot outlive its function."""
    if not token or runtime.serverless():
        return
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        try:
            r = conn.execute("SELECT value FROM meta WHERE key=?", (FAX_LOCK_KEY,)).fetchone()
            held = json.loads(r[0]) if r else {}
            if held.get("token") == token:
                conn.execute("UPDATE meta SET value=? WHERE key=? AND value=?", (json.dumps(dict(held, heartbeat=_now_iso())), FAX_LOCK_KEY, r[0]))
                conn.commit()
        finally:
            conn.close()
    except Exception:
        pass            # the run goes on; at worst its lock goes stale and another run may start


def _fax_lock_release(token: str) -> None:
    try:
        if runtime.serverless():
            cloud, st = _cloud_state()
            raw = st.get(FAX_LOCK_FILE)
            if raw and json.loads(raw).get("token") == token:
                st.delete(FAX_LOCK_FILE)
            return
        conn = sqlite3.connect(DB_PATH, timeout=30)
        try:
            conn.execute("DELETE FROM meta WHERE key=? AND value LIKE ?", (FAX_LOCK_KEY, f'%"token": "{token}"%'))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass            # a lock that cannot be released goes stale and is taken over


class _FaxRun:
    """`with fax_run(who) as lock:` around a fax run (the line brought up to date, the follow-ups sent). FaxBusy when another
    run holds it."""

    def __init__(self, who: str):
        self.who, self.token = who, None

    def __enter__(self) -> str:
        self.token = _fax_lock_take(self.who)
        if self.token is None:
            raise FaxBusy("Another fax run is going (the hourly or daily run, or someone else's button). Try again in a few minutes.")
        return self.token

    def __exit__(self, *exc) -> None:
        _fax_lock_release(self.token)


def fax_run(who: str) -> _FaxRun:
    return _FaxRun(who)


# ---- one send per thing: reserved before it is made
class SendBlocked(Exception):
    """A send that must not go now: something went out, or is going out, for the same item and step, or what it rests on
    changed (the item, the number) since it was prepared."""


def _reservation_key(prov, fid: str, purpose: str, level: int, conn) -> str:
    """The same item, step and level, and the same count of tries before it: two runs that prepared the same send get the
    same key, and only one can write it."""
    n = conn.execute("SELECT COUNT(*) FROM faxes WHERE direction='out' AND finding_id=? AND purpose=? AND COALESCE(level,1)=?",
                     (fid, purpose, level)).fetchone()[0] + 1
    return f"out:{prov.name}:{hashlib.sha256(str(fid).encode()).hexdigest()[:16]}:{purpose}:{level}:{n}"


def _appeal_on_file(conn, f: dict, level: int) -> str | None:
    """Why an appeal must not be faxed because one for the same claim line (for an item about a whole claim, the same
    claim) at the same level already went out from another item, or None. Detection moves an item's faxes to its heir when
    its signal changes (detect.carry_to_heirs), so this catches what the item's own faxes cannot: two items on one line.
    Read in the step that reserves the send."""
    fid = f.get("finding_id")
    if f.get("line_key"):
        cond, key = "g.line_key = ?", f["line_key"]
    elif f.get("claim_key"):
        cond, key = "g.line_key IS NULL AND g.claim_key = ?", f["claim_key"]
    else:
        return None
    try:
        r = conn.execute(f"""SELECT x.created_at FROM faxes x JOIN findings g ON g.finding_id = x.finding_id
                             WHERE x.direction='out' AND x.purpose='appeal' AND COALESCE(x.level,1)=? AND COALESCE(x.status,'')!='failed'
                               AND x.finding_id != ? AND {cond} ORDER BY x.created_at LIMIT 1""", (int(level or 1), fid, key)).fetchone()
    except sqlite3.OperationalError:        # no faxes table yet: nothing went out
        return None
    if not r:
        return None
    return "An appeal for this claim line was already faxed, from another item on the same line. Follow that item's appeal; a second one is not faxed."


def _adopt_marker(st, marker: str, key: str) -> None:
    """A serverless send's marker with no record of its own, older than any send takes: the run stopped between the two.
    Its record is written now, as not settled, so the next run asks the fax service whether it went out."""
    try:
        m = st.meta(marker)
        from . import cloud
        if not m or cloud._age_seconds(m.get("modified")) < RESERVE_STALE_S:
            return
        conn = db()
        try:
            have = conn.execute("SELECT 1 FROM faxes WHERE fax_key=?", (key,)).fetchone()
        finally:
            conn.close()
        if not have:
            rec = json.loads(st.get(marker) or b"{}")
            if rec.get("fax_key") == key:
                # added only where no copy has it: an instance whose copy is behind must not set a fax that went out back
                _record_action({"kind": "fax_new", "record": rec})
    except Exception:
        pass


def _reserve(prov, f: dict, number: str, *, packet_id: str | None, purpose: str, level: int, actor: str, subject: str | None,
             extra: dict | None, still_ok=None) -> dict:
    """Write a send down before it is made, in the one step that also checks nothing went out or is going out for this
    item, step and level (still_ok(conn): why it must not go now, or None). Two runs, two people, a request sent twice or
    a run that died between the send and its record: one send. On a server that step is a transaction on the store; on a
    serverless host a marker file in the state folder, which only one instance can write. Returns the record, in state
    'sending'; raises SendBlocked."""
    from . import fax
    from .actions import ensure_fax_tables, upsert_fax
    fid = f.get("finding_id")
    token = secrets.token_hex(6)
    filename = (f"{purpose}-{(packet_id or 'letter')[-24:]}".replace(".", "-") + f"-{token}.pdf")
    now = _fax_now()
    rec = {"direction": "out", "finding_id": fid, "packet_id": packet_id, "payer_group": f.get("payer_group"), "number": fax.digits(number),
           "purpose": purpose, "level": level, "provider": prov.name, "provider_id": None, "status": "sending", "pages": None, "error": None,
           "created_at": now, "updated_at": now,
           "doc": dict(extra or {}, actor=actor, subject=subject, attempts=(extra or {}).get("attempts", 1), cents=f.get("shortfall_cents"),
                       filename=filename)}
    if runtime.serverless():
        from .state import Conflict
        cloud, st = _cloud_state()
        conn = db()
        try:
            why = still_ok(conn) if still_ok else None
            if not why and purpose == "appeal":
                why = _appeal_on_file(conn, f, level)
            if why:
                raise SendBlocked(why)
            key = _reservation_key(prov, fid, purpose, level, conn)
            if conn.execute("SELECT 1 FROM faxes WHERE fax_key=?", (key,)).fetchone():
                raise SendBlocked("It is being sent already.")
        finally:
            conn.close()
        rec["fax_key"] = key
        marker = "fax-sends/" + key.replace(":", "_") + ".json"
        try:
            st.put(marker, json.dumps(rec, default=str).encode(), mode="add")
        except Conflict:
            _adopt_marker(st, marker, key)
            raise SendBlocked("It is being sent already.")
        _record_action({"kind": "fax", "record": rec})
        return rec
    conn = sqlite3.connect(DB_PATH, timeout=60, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        ensure_fax_tables(conn)
        conn.execute("BEGIN IMMEDIATE")
        try:
            why = still_ok(conn) if still_ok else None
            if not why and purpose == "appeal":
                why = _appeal_on_file(conn, f, level)
            if why:
                raise SendBlocked(why)
            key = _reservation_key(prov, fid, purpose, level, conn)
            if conn.execute("SELECT 1 FROM faxes WHERE fax_key=?", (key,)).fetchone():
                raise SendBlocked("It is being sent already.")
            rec["fax_key"] = key
            upsert_fax(conn, rec)
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    return rec


def _settle(rec: dict, **changes) -> None:
    _record_action({"kind": "fax", "record": dict({"fax_key": rec["fax_key"], "updated_at": _fax_now()}, **changes)})


def _send_fax(prov, f: dict, number: str, doc: bytes, pages: int, *, packet_id: str, purpose: str, level: int, actor: str,
              subject: str | None, extra: dict | None = None, still_ok=None) -> dict:
    """Fax a document and record it: reserved first (_reserve), then sent, then the record brought up to date with the fax
    service's ID (the record also says how to build it again, for a retry). Raises SendBlocked when it must not go, and
    fax.FaxError when the service refused it (recorded as a failed try) or did not answer (kept as not settled: the service
    is asked whether it went out before anything is sent again)."""
    from . import fax
    rec = _reserve(prov, f, number, packet_id=packet_id, purpose=purpose, level=level, actor=actor, subject=subject, extra=extra, still_ok=still_ok)
    try:
        pid = prov.send(number, doc, rec["doc"]["filename"])
    except fax.FaxError as exc:
        if getattr(exc, "maybe_sent", False):
            _settle(rec, pages=pages, error=f"{str(exc)[:200]} It may have gone out: SRFax is asked before it is sent again.")
        else:
            _settle(rec, pages=pages, status="failed", error=str(exc)[:300])
        raise
    except BaseException as exc:
        _settle(rec, pages=pages, error=f"The send stopped ({type(exc).__name__}): SRFax is asked whether it went out before it is sent again.")
        raise
    rec.update(provider_id=pid, status="queued", pages=pages)
    _settle(rec, provider_id=pid, status="queued", pages=pages)
    return rec


BULK_PAUSED = ("Sending many appeals at once is paused until RemitWatch checks EMA's payments (the next update): some items "
               "it lists were paid on checks with no remittance, and a letter would go to the payer for nothing. Open an item "
               "and use Appeal by fax there, after checking in EMA that it is still unpaid.")


def bulk_appeals_on() -> bool:
    """Bulk appeal faxing (Appeal everything appealable, and Appeal by fax on ticked items). Off unless
    REMITWATCH_BULK_APPEAL=1: until RemitWatch reads EMA's payments it cannot tell a paid line from an unpaid one, so only
    an appeal a person opens and checks one at a time goes out."""
    return os.environ.get("REMITWATCH_BULK_APPEAL", "").strip().lower() in ("1", "true", "yes", "on")


# What "Appeal everything appealable" may send (needs_B.md 1.3, unit B's rule, exactly): an open item to work now, with
# nothing that blocks an appeal (remitwatch.states' appeal_block: paid, credit, reprocessed, waiting), an appeal by its
# action, inside its window, never money owed back, and never a claim to chase, fix, post, bill or decide.
BULK_EXCLUDED_SIGNALS = ("NO_ANSWER", "REJECTED_PRE_PAYER", "NOT_SENT", "REMIT_NOT_POSTED", "SECONDARY_NOT_BILLED", "CREDIT_BALANCE",
                         "FORWARDED_TO_CARRIER", "EMA_DENIAL_AS_CONTRACTUAL")
BULK_RULE = ("status='open' AND COALESCE(stale,0)=0 AND work_state='work_now' AND appeal_block IS NULL AND actionable='appeal' "
             "AND appeal_deadline >= ? AND category <> 'refund' AND signal NOT IN (" + ",".join(f"'{x}'" for x in BULK_EXCLUDED_SIGNALS) + ")")
# why an appeal must not go (remitwatch.states' appeal_block), in the page's words
BLOCK_WORDS = {"paid": "it was paid or settled", "credit": "the line carries a credit (money owed back)",
               "reprocessed": "the payer reprocessed the line after this answer", "waiting": "it waits on the payer's answer to work already done",
               "settled_in_ema": "EMA shows the line settled: find which payer paid before appealing it",
               "likely_paid": "the archive shows it was likely paid: check it first (EMA, the payer's portal)"}
# never appealed, whoever asks and whatever the item's status: a line paid, credited or reprocessed (amendment 14); one EMA
# shows settled, or the archive shows likely paid (orchestrator decision 4), goes only after a person checked it is unpaid
NEVER_APPEALED = ("paid", "credit", "reprocessed")
CHECK_FIRST = ("settled_in_ema", "likely_paid")
# the states in which nothing is appealed: done (paid or settled, with proof), accepted (let go, lost, the owner's policy),
# expired (past every window)
# what EMA's postings show (ema_checks.fax_vouch) that holds an item in bulk even after a person's "still unpaid in EMA"
EMA_SHOWS_PAID = ("paid_after", "no_balance")
CLOSED_STATES = {"done": "it was found paid or settled", "accepted": "it is accepted (let go, lost, or the owner's write-off policy)",
                 "expired": "every window to appeal it has closed"}
# why an open item a letter settles is not on the bulk list, in the "Left out" line's words and order
LEFT_OUT = (("paid", "paid"), ("waiting", "waiting"), ("credit", "credit"), ("reprocessed", "reprocessed"), ("settled", "settled in EMA"),
            ("likely_paid", "likely paid"), ("no_date", "no filing date"), ("no_state", "state not worked out yet"), ("other", "other"))
LEFT_OUT_WORDS = dict(BLOCK_WORDS, settled="EMA shows it settled: find which payer paid", no_date="no filing date on file: its panel says what to check",
                      no_state="the run has not worked out its state yet", other="not an item to appeal now")


def _bulk_ready(cols: set[str]) -> bool:
    """Whether the store has the columns the bulk rule reads (a store analysed before them sends nothing in bulk)."""
    return {"work_state", "appeal_block"} <= cols


def _left_out_reason(r: dict) -> str:
    """Why an open item an appeal letter would settle is not on the bulk list (LEFT_OUT): paid, waiting, credit,
    reprocessed, settled (EMA shows the line settled, or the patient paid it), likely paid (the archive), no filing date,
    no state worked out yet, or other."""
    block = r.get("appeal_block")
    if block in ("paid", "credit", "reprocessed", "waiting", "likely_paid"):
        return block
    if block in ("settled_in_ema", "patient_paid"):
        return "settled"
    ws = r.get("work_state")
    if ws == "done":
        return "paid"
    if ws == "waiting":
        return "waiting"
    if r.get("category") == "refund" or r.get("signal") == "CREDIT_BALANCE":
        return "credit"
    if r.get("work_state") is None:
        return "no_state"
    if not r.get("appeal_deadline"):
        return "no_date"
    return "other"


def _appeal_ok_now(fid: str) -> tuple[bool, str | None]:
    """The item's own next step (remitwatch.story.next_step) says an appeal may go: never for a line paid, settled, credited,
    reprocessed or waiting, nor past its last window. (False, why) otherwise; (True, None) when it may."""
    from . import story as story_mod
    lg = _ledger_readonly()
    conn = db()
    conn.row_factory = None
    try:
        nxt = story_mod.next_step(conn, fid, ledger=lg, today=_day(0))
    except Exception as exc:        # noqa: BLE001  (an item whose story cannot be told is not sent in bulk)
        _say(f"api: next_step failed for item {fid}: {type(exc).__name__}: {str(exc)[:160]}")
        return False, "Its next step could not be worked out, so it is not sent in bulk: open it and send it from there."
    finally:
        conn.close()
        if lg is not None:
            lg.close()
    if nxt.get("appeal_ok"):
        return True, None
    why = BLOCK_WORDS.get(nxt.get("appeal_block") or "")
    return False, (f"Not appealed: {why}." if why else f"Not appealed: its next step is not an appeal ({nxt.get('action') or 'nothing to send'}).")


def _vouch(f: dict) -> dict | None:
    """W5 (ema_checks.fax_vouch): whether EMA's reports vouch for an item the remittances alone hold as stale (they stopped
    coming): its line's outcome worked out now, on whichever copy is at hand. None when EMA's reports cannot be read here."""
    from . import ema_checks as ec
    conn = db()
    conn.row_factory = None
    try:
        rules = ec.store_rules(conn)
        oc = ec.line_outcomes(conn, [f], code_once=rules.code_once, today=_day(0))
        return (ec.fax_vouch(conn, [f], oc, today=_day(0)) or {}).get(f["finding_id"])
    except Exception as exc:        # noqa: BLE001  (no EMA tables on this copy: nothing vouches, the stale hold stands)
        _say(f"api: fax_vouch failed for item {f.get('finding_id')}: {type(exc).__name__}: {str(exc)[:160]}")
        return None
    finally:
        conn.close()


@app.post("/api/appeals/send", dependencies=[Depends(require_xhr)])
def appeals_send(body: dict = Body(default={}), user: str = Depends(require_auth)):
    """One item all the way to the payer: the visit note from EMA where the appeal rests on it (Claude reviews it and
    writes the paragraphs that justify the claim), the letter, the fax to the payer's confirmed appeal number, and the
    item marked Sent. What cannot go comes back held, with why and what to do. A packet built earlier is reused (one
    built without the review is built again when the review can now run, and so is one whose note was taken back since);
    rebuild: build it again anyway. stale_ok: a person checked in EMA that it is still unpaid although the remittances are
    behind; confirmed: a person checked in EMA that the higher code went out on the claim. An item whose appeal never got
    through (every try failed) can be sent again, to the number saved now."""
    from . import fax
    fid = str(body.get("finding_id") or "")
    if not re.fullmatch(FAX_KEY, fid):
        raise HTTPException(400, "finding_id is required")
    detail = finding_detail(fid, "ok")
    f = detail["finding"]
    from .insights import work_type
    kind = work_type(f.get("signal"), f.get("actionable"), f.get("carc"))
    out = {"finding_id": fid, "payer_group": f.get("payer_group"), "cents": f.get("shortfall_cents"), "kind": kind}

    def held(code: str, reason: str, **kw) -> dict:
        return dict(out, status="held", code=code, reason=reason, **kw)
    # a yes that skips a hold counts only as JSON true: "false", "no" or "0" sent as text is not a yes
    if body.get("bulk") is True and not bulk_appeals_on():
        return held("bulk_paused", BULK_PAUSED)
    if f.get("signal") == "EMA_DENIED":
        # a denial only EMA's postings show: no remittance carries it, so there is nothing to cite yet (W5)
        from .ema_checks import NO_REMITTANCE_MESSAGE
        return held("no_remittance", NO_REMITTANCE_MESSAGE)
    if body.get("bulk"):
        # the bulk list's rule, read again on the item as it is now, and the item's own next step (unit B's rule and unit
        # D's appeal_ok): a paid, settled, credited, reprocessed or waiting line is never appealed in bulk
        cols = _columns()
        ok = _bulk_ready(cols) and one(f"SELECT 1 FROM findings WHERE finding_id=? AND {BULK_RULE}", (fid, _day(0))) is not None
        if not ok:
            why = BLOCK_WORDS.get(f.get("appeal_block") or "") or (
                "it is not an open item to work now inside its window" if _bulk_ready(cols) else "this site has not worked out the items' states yet")
            return held("left_out", f"Not sent in bulk: {why}.")
        fine, why = _appeal_ok_now(fid)
        if not fine:
            return held("left_out", why)
    # an appeal whose every fax failed (a busy line, a number replaced since) can be sent again, to the number saved now
    again = f.get("status") == "appealed" and not _faxes_out(fid) and bool(_faxes_out(fid, live=False))
    # one item a person sends: never a line paid, credited or reprocessed since, whatever its status (an appeal sent again
    # too); a line EMA shows settled, or the archive shows likely paid, only after the person checked it is unpaid
    # ("checked"); never an item whose state is done, accepted or expired; and never one that waits on the payer, unless
    # it waits only because its own failed fax marked it Sent (amendment 14, orchestrator decision 4)
    block = f.get("appeal_block") or ""
    live_state = _status_state(f) if (str(f.get("status_changed_at") or "") > (_run_anchor() or "~")) else f.get("work_state")
    if f.get("status") in ("open", "in_progress") or again:
        if block in NEVER_APPEALED or (block == "waiting" and not again):
            return held("blocked", f"Not sent: {BLOCK_WORDS[block]}. Its panel says what to do instead.")
        if block in CHECK_FIRST and not body.get("checked"):
            return held("check_first", f"Not sent yet: {BLOCK_WORDS[block]}. If it is still unpaid, press \"Checked: still unpaid, fax it\".",
                        block=block)
        if live_state in CLOSED_STATES:
            return held("blocked", f"Not sent: {CLOSED_STATES[live_state]}. Its panel says what to do instead.")
    if kind not in fax.APPEALABLE:
        return held("not_appealable", {"fix": "This one is corrected in EMA and sent again as a claim, not appealed.",
                                       "auth": "This one needs the authorization record, or a retro-authorization, first.",
                                       "check": "This one needs a person to decide first: another payer, the patient, or let it go."}
                    .get(kind, "This one is not an appeal."))
    if f.get("status") not in ("open", "in_progress") and not again:
        return dict(out, status="skipped", reason=f"It is {STATUS_WORDS.get(f.get('status'), f.get('status'))}.")
    # a payer that files no faxed request at the first level (CGS): a person files it on the payer's portal or by mail. An
    # item whose window has closed is told that instead, below
    route = fax.no_fax(f.get("payer_group"), 1, f.get("payer_name"))
    if route and not (f.get("appeal_deadline") and f["appeal_deadline"] < _day(0)):
        return held("no_fax_route", route)
    # remittances that stopped coming: anything could have been paid since, and a paid claim must not be appealed. Only a
    # person's "still unpaid in EMA" passes this: it says nothing about which code went out (the check below). In bulk that
    # answer covers many items at once, so EMA's own sign of a payment after the denial, or of no balance left, still holds
    # each one it shows (W5 is asked in bulk whatever the answer)
    behind = _data_behind_days()
    vouched = None
    if behind is not None and behind > STALE_APPEAL_DAYS and (body.get("stale_ok") is not True or body.get("bulk")):
        # W5: EMA's Production report can vouch for the item where the remittances stopped coming (still unpaid, on its EMA
        # claim, nothing posted since); only when neither can is it held
        v = _vouch(f)
        code = (v or {}).get("code") or "no_ema"
        if not (v and v.get("vouched")) and body.get("stale_ok") is True and code in EMA_SHOWS_PAID:
            return held(code, f"{v.get('message') or 'EMA shows this line paid or closed.'} It is not faxed in bulk: open it and check it first.",
                        vouch=code)
        if not (v and v.get("vouched")) and body.get("stale_ok") is not True:
            from .detect import gap_words
            gap = _feed_gap()       # weeks missing, then newer files (misuse check CH-6): named, with what to do
            extra = f" {v['message']}" if v and v.get("message") else ""
            return held("stale", (f"{gap_words(gap)} This claim may have been paid in those weeks.{extra} " if gap else
                                  f"The newest remittance RemitWatch has is {behind} days old, so this claim may have been paid since.{extra} "
                                  "Get the remittances current first (Data tab, or the TriZetto feed); ")
                        + "\"Still unpaid in EMA: fax it anyway\" in the item sends it now.", vouch=code)
        vouched = (v or {}).get("message") if v and v.get("vouched") else None
    # the remittance does not say the payer changed the code: the higher level is inferred (from the charge, or from EMA's
    # charge rather than the claim file), and a letter saying it was billed must not go before a person has seen it went out
    ev = f.get("evidence") if isinstance(f.get("evidence"), dict) else {}
    if (f.get("signal") == "DOWNCODE_CHARGE" or (f.get("signal") == "DOWNCODE_VS_BILLED" and ev.get("billed_source") != "837 claim file")) \
            and body.get("confirmed") is not True:
        billed = ev.get("billed_code") or ev.get("charge_implies") or "the higher level"
        return held("check_code", f"The remittance does not say the payer changed the code. Check in EMA that {billed} went out on the claim, "
                                  "then press \"It went out as billed: fax it\" in the item.")
    # the safe date (appeal_deadline) is what a bulk send keeps to (BULK_RULE); one item a person sends goes until the last
    # window closes: the payer's own limit (legal_deadline) when it is later (orchestrator decision 2)
    today = _day(0)                 # open until midnight where the practice is
    last = max((d for d in ((f.get("appeal_deadline") or "")[:10], "" if body.get("bulk") else (f.get("legal_deadline") or "")[:10]) if d), default="")
    if last and last < today:
        which = "the payer's own limit" if last != (f.get("appeal_deadline") or "")[:10] else "its filing window"
        return held("expired", f"Not sent: {which} closed on {last}.")
    if not f.get("claim_key"):
        return held("no_claim", "It is not tied to one claim.")
    missing = _practice_missing()
    if missing:
        return held("practice", "The letters still lack the practice's details: set " + ", ".join(missing) + " in the host's settings.")
    prov = fax.provider()
    if prov is None:
        return held("no_fax", fax.not_here() or "The fax line is not set up yet: SRFAX_ACCESS_ID and SRFAX_ACCESS_PWD in the host's settings.")
    number = _payer_number(f.get("payer_group"))
    if not number and fax.shared_group(f.get("payer_group")):
        return held("by_hand", fax.shared_why(f.get("payer_group")))
    if not number:
        return held("no_number", f"No appeal fax number is saved for {f.get('payer_group') or 'this payer'}. Add it on the Faxes page.")
    # from here to the end of the send the appeal is in flight: a second press, and a person's status change, wait for it
    token = _prep_take(fid)
    if token is None:
        raise Refused(409, "in_flight", IN_FLIGHT)
    try:
        return _appeal_after_holds(body, user, fid, detail, f, kind, out, held, again, prov, number, vouched)
    finally:
        _prep_release(fid, token)


def _appeal_after_holds(body: dict, user: str, fid: str, detail: dict, f: dict, kind: str, out: dict, held, again: bool, prov, number: str,
                        vouched: str | None = None) -> dict:
    """appeals_send from the moment the appeal is in flight: what went out for it before, the note and the packet, the
    holds on the packet, the fax."""
    from . import fax
    live = _faxes_out(fid)
    took = [x for x in live if x.get("provider_id")]
    if took:
        # its appeal went out (the line took it) but the item was never marked Sent (the run stopped between the two), and
        # nobody changed it since: marked now, so its follow-ups start
        _mark_sent_appeal(took[-1], user)
        return dict(out, status="skipped", reason="Its appeal was already faxed.")
    if live:
        # reserved and never taken by the line: going out now while it can still be; else nobody knows whether it went,
        # and it is never counted as sent (the hourly fax run asks SRFax, and the Faxes page lets a person say)
        x = live[-1]
        if x.get("status") == "sending" and not x.get("error") and _young(x.get("created_at")):
            raise Refused(409, "in_flight", IN_FLIGHT)
        when = str(x.get("created_at") or "")[:16].replace("T", " ")
        return held("unknown_send", f"A fax for this item was started at {when} UTC and RemitWatch does not know whether it went out. "
                                    "Look on the Faxes page before sending it again.")
    raw_claim = one("SELECT * FROM claims WHERE claim_key=?", (f["claim_key"],))
    use_ai = _yes_ai(body.get("use_ai", True))
    pk = None if body.get("rebuild") is True else next(iter(_packets_for(fid)), None)
    seen = str(body.get("packet_id") or "")
    if seen:
        # the packet the person read in the preview goes as it is, or not at all: never one built behind their back
        pk = next((p for p in _packets_for(fid) if p.get("packet_id") == seen), None)
        if pk is None or _packet_problem(seen, f, first=True):
            return held("packet_changed", "The packet you read can no longer go as it is: build it again and read it before it is sent.")
        use_ai = False
    if pk and pk.get("mode") == "template" and use_ai and os.environ.get("ANTHROPIC_API_KEY") and kind != "rate":
        pk = None           # built before the review could run: now it can
    if pk and _packet_problem(pk["packet_id"], f, first=True):
        pk = None           # its note was taken back or replaced, or its quotations were never checked: built again from the note now
    note = None
    if pk:
        packet_id = pk["packet_id"]
    elif kind == "rate":
        from .notes import build_letter_only
        packet_id = _keep_packet(lambda conn: build_letter_only(conn, detail, raw_claim, reveal=SHOW_PHI))["packet_id"]
    else:
        from . import modmed
        key = modmed.claim_key(detail, raw_claim, _ema_patient_ids(f.get("patient_control_number")))
        pick = body.get("document_id")
        since = str(body.get("since") or "")[:10]
        try:
            res = _note_lookup(key, pick, since=since if re.fullmatch(r"\d{4}-\d{2}-\d{2}", since) else _note_batch_since())
        except modmed.NotConfigured:
            return held("no_ema", "EMA is not connected, so the visit note cannot be read: connect it on /setup, or upload the note in the item.")
        except modmed.ModMedError as exc:
            return held("ema_error", f"EMA did not give the note: {exc}")
        if res["status"] == "ambiguous":       # the lookup says why: two notes, only the name agrees, two patients, a later note...
            return held("ambiguous", res.get("reason") or "More than one note fits that visit: open the item and pick the right one.",
                        candidates=res.get("candidates"))
        if res["status"] != "found":
            return held("no_note", res["reason"] if res.get("source") == "upload" else
                        "EMA has no signed note for this visit: sign it in EMA, or upload the note on the Upload page or in the item.")
        note = {k: v for k, v in res["note"].items() if k != "data"}
        packet_id = _store_packet(detail, raw_claim, res["note"]["filename"], res["note"]["data"], None, use_ai,
                                  source="upload" if res.get("source") == "upload" else "ema")["packet_id"]
    out["packet_id"] = packet_id
    p = _stored_packet(packet_id)
    if not p or not p.get("pdf"):
        return held("no_packet", "The packet could not be read back. Try again.")
    why = _packet_problem(packet_id, f, first=True)
    if why and "quotations" not in why:
        return held("no_packet", f"The packet cannot go: {why} Build it again in the item.")
    # the note is this patient's and this visit's, by what its text shows; else a person opened it and said so
    checked = None
    if p.get("mode") != "letter":
        ident = _packet_identity(packet_id, p, detail, raw_claim)
        if not ident.get("ok"):
            dos = _visit_day(detail, raw_claim)
            same = body.get("packet_id") in (None, packet_id)
            if ident.get("code") == "several" or body.get("note_checked") is not True or not same:
                return held("note_check", f"Check the visit note before it goes: {ident.get('why')} "
                            + ("Build the packet again from this visit's own note." if ident.get("code") == "several" else
                               f"Open the packet, and if the note is this patient's visit on {dos}, tick \"I opened this note: it is this "
                               f"patient's visit on {dos}\" and send it."),
                            confirm=ident.get("code") != "several", visit_date=dos)
            checked = dos
    a = p.get("analysis") or {}
    if a.get("recommendation") == "do_not_appeal":
        return held("review_no", "Claude's review says the note does not support it: " + (a.get("recommendation_reason") or "")
                    + " It is counted in Suggested templates on Bigger wins.")
    if a.get("recommendation") == "correct_and_resubmit":
        return held("review_fix", "Claude's review says to correct the claim and send it again instead: " + (a.get("recommendation_reason") or ""))
    doubts = _review_doubts(a, detail)
    if doubts:
        return held("review_caution", f"Claude's review has doubts: {doubts}. {a.get('recommendation_reason') or ''} "
                                      "It is not faxed by itself: open the packet, and send it by hand only if a person decides it should go.")
    quotes = _quotes_problem(a)
    if quotes:
        return held("quotes", f"{quotes} It is not faxed by itself: open the packet and check it against the note.")
    blanks = fax.letter_blanks(p.get("letter"))
    if blanks:
        why = a.get("reason") if a.get("mode") == "template" else ""
        return held("blanks", f"The letter still has blanks ({', '.join(blanks[:4])}). " + (why or "Open the item to finish it."))
    try:
        doc, pages = _fax_doc(prov, f, number, p["pdf"], p.get("subject"))
    except Exception as exc:
        return held("no_packet", f"The fax could not be put together ({type(exc).__name__}). Build the packet again in the item.")
    if checked:
        _record_action({"kind": "event", "event": "note_check", "finding_id": fid, "actor": user, "at": _fax_now(),
                        "note": f"{user} opened the note and said it is this patient's visit on {checked}."})

    def still_ok(conn) -> str | None:
        # checked again in the step that reserves the send: nothing went out for it meanwhile, it is still open, and the
        # number the cover sheet names is still the one saved
        st = conn.execute("SELECT status FROM findings WHERE finding_id=?", (fid,)).fetchone()
        if not st or (st[0] not in ("open", "in_progress") and not (again and st[0] == "appealed")):
            return f"It is {STATUS_WORDS.get(st[0], st[0]) if st else 'gone'} now."
        if conn.execute("SELECT 1 FROM faxes WHERE finding_id=? AND direction='out' AND COALESCE(status,'')!='failed' LIMIT 1", (fid,)).fetchone():
            return "Its appeal was already faxed."
        if fax.number_for(conn, f.get("payer_group"), f.get("payer_kind"), 1)["number"] != fax.digits(number):
            return "The payer's fax number changed while the packet was built: send it again."
        return None
    try:
        sent = _send_fax(prov, f, number, doc, pages, packet_id=packet_id, purpose="appeal", level=1, actor=user, subject=p.get("subject"),
                         still_ok=still_ok)
    except SendBlocked as exc:
        return dict(out, status="skipped", reason=str(exc))
    except fax.FaxError as exc:
        return held("fax_error", str(exc))
    # Sent only if nothing changed the item while SRFax had the fax (the run's Paid, a hide): else it keeps its status
    kept = _sent_once_faxed(fid, f"Faxed to {f.get('payer_group') or 'the payer'} at {fax.pretty(number)} (fax {sent['provider_id']}, {pages} pages)."
                            + (f" {vouched}" if vouched else ""), user, again)
    return dict(out, status="sent", fax_id=sent["provider_id"], number=fax.pretty(number), pages=pages, note=note, **({"kept": kept} if kept else {}))


def _yes_ai(v: Any) -> bool:
    """Claude's review is asked for unless the caller said no, in any of the ways a no is written."""
    if v is False or (isinstance(v, (int, float)) and not isinstance(v, bool) and v == 0):
        return False
    return not (isinstance(v, str) and v.strip().lower() in ("0", "false", "no", "off"))


@app.get("/api/appeals/appealable")
def appeals_appealable(payer: Optional[str] = Query(None, max_length=200), limit: int = Query(2000, ge=1, le=5000), _: str = Depends(require_auth)):
    """What "Appeal everything appealable" sends: unit B's rule exactly (BULK_RULE: open, work now, nothing blocking an
    appeal, an appeal by its action, inside its window, no refund and no claim to chase, fix, post, bill or decide) for the
    items an appeal letter settles, soonest window first; each is checked again against its own next step when it is sent.
    With it, what is left out and why ("Left out: N items (paid M, waiting K, credit C, reprocessed R)", each item listed)
    and what stands in the way (payers with no fax number yet, the fax line, the practice's details, EMA)."""
    from . import fax, modmed
    from .insights import work_type
    if not bulk_appeals_on():
        return {"paused": BULK_PAUSED, "items": [], "payers": [], "fax": fax.configured(),
                "practice_missing": [], "modmed": False, "review": False}
    cond, params = _appealable_cond()
    cols = _columns()
    by_payer = " AND payer_group=?" if payer else ""
    today = _day(0)
    if _bulk_ready(cols):
        items = rows(f"""SELECT finding_id, payer_group, signal, actionable, carc, shortfall_cents, appeal_deadline FROM findings
                         WHERE {cond} AND {BULK_RULE} AND claim_key IS NOT NULL{by_payer}
                         ORDER BY appeal_deadline, COALESCE(shortfall_cents, 0) DESC, finding_id LIMIT ?""",
                      (*params, today, *([payer] if payer else []), limit))
        # the open items in their windows a letter would settle that the rule leaves out, each with why
        out_rows = rows(f"""SELECT finding_id, payer_group, signal, category, shortfall_cents, appeal_deadline, work_state, appeal_block FROM findings
                            WHERE {cond} AND status IN ('open','in_progress') AND COALESCE(stale,0)=0
                            AND (appeal_deadline IS NULL OR appeal_deadline >= ?){by_payer}
                            AND NOT COALESCE(({BULK_RULE} AND claim_key IS NOT NULL), 0)
                            ORDER BY COALESCE(shortfall_cents, 0) DESC, finding_id""",
                        (*params, today, *([payer] if payer else []), today))
    else:
        # a store analysed before the states: nothing goes in bulk until the next run has worked them out
        items = []
        out_rows = [dict(r, work_state=None, appeal_block=None) for r in rows(
            f"""SELECT finding_id, payer_group, signal, category, shortfall_cents, appeal_deadline FROM findings
                WHERE {cond} AND status IN ('open','in_progress') AND COALESCE(stale,0)=0
                AND (appeal_deadline IS NULL OR appeal_deadline >= ?){by_payer} ORDER BY COALESCE(shortfall_cents, 0) DESC, finding_id""",
            (*params, today, *([payer] if payer else [])))]
    numbers = {r["payer_group"]: fax.digits(r["number"]) for r in rows("SELECT payer_group, number FROM payer_fax WHERE purpose='appeal'")
               if not fax.shared_group(r["payer_group"])} if has_table("payer_fax") else {}
    sent = {r["finding_id"] for r in rows("SELECT DISTINCT finding_id FROM faxes WHERE direction='out' AND COALESCE(status,'')!='failed'")} if has_table("faxes") else set()
    by: dict[str, dict] = {}
    out = []
    for i in items:
        if i["finding_id"] in sent:
            continue
        k = work_type(i["signal"], i["actionable"], i["carc"])
        pg = i["payer_group"] or ""
        # a group of many insurers keeps no number: its appeals are faxed by hand (fax.shared_group); a payer that files no
        # faxed first-level appeal (fax.no_fax) gets them on its portal or by mail
        by_hand = fax.shared_why(pg) if fax.shared_group(pg) else fax.no_fax(pg, 1)
        b = by.setdefault(pg, {"payer_group": pg, "n": 0, "cents": 0, "number": fax.pretty(numbers.get(pg)) if numbers.get(pg) else "", "kinds": {},
                               "by_hand": by_hand})
        b["n"] += 1
        b["cents"] += i["shortfall_cents"] or 0
        b["kinds"][k] = b["kinds"].get(k, 0) + 1
        out.append({"finding_id": i["finding_id"], "payer_group": pg, "kind": k, "cents": i["shortfall_cents"] or 0,
                    "ready": bool(numbers.get(pg)) and not by_hand, "by_hand": bool(by_hand), "no_fax": bool(fax.no_fax(pg, 1))})
    left = dict({k: 0 for k, _w in LEFT_OUT}, n=0, cents=0, items=[])
    for r in out_rows:
        why = _left_out_reason(r)
        left["n"] += 1
        left["cents"] += r["shortfall_cents"] or 0
        left[why] += 1
        if len(left["items"]) < 500:
            left["items"].append({"finding_id": r["finding_id"], "payer_group": r["payer_group"], "signal": r["signal"],
                                  "cents": r["shortfall_cents"] or 0, "reason": why, "words": LEFT_OUT_WORDS.get(why, LEFT_OUT_WORDS["other"])})
    parts = [f"{w} {left[k]}" for k, w in LEFT_OUT if left[k]]
    left["line"] = f"Left out: {left['n']:,} item{'s' if left['n'] != 1 else ''} ({', '.join(parts)})" if left["n"] else ""
    return {"items": out, "payers": sorted(by.values(), key=lambda b: -b["cents"]), "fax": fax.configured(), "not_here": fax.not_here(),
            "practice_missing": _practice_missing(), "modmed": modmed.Settings.from_env().configured(), "review": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "left_out": left}


@app.get("/api/faxes")
def faxes_list(_: str = Depends(require_auth)):
    """The Faxes page: the line, what went out and its state, what came in and what it was matched to (or which items a
    person should look at), and the follow-ups due. The payer's letters name the patient: those names are shown only where
    names are switched on."""
    from . import fax
    sent = rows("""SELECT x.fax_key, x.finding_id, x.packet_id, x.payer_group, x.number, x.purpose, x.level, x.provider_id, x.status, x.pages,
                          x.error, x.created_at, x.sent_at, f.title, f.shortfall_cents, f.status item_status
                   FROM faxes x LEFT JOIN findings f ON f.finding_id = x.finding_id WHERE x.direction='out' ORDER BY x.created_at DESC LIMIT 400""") \
        if has_table("faxes") else []
    got = rows("""SELECT x.fax_key, x.finding_id, x.payer_group, x.number, x.purpose, x.status, x.pages, x.created_at, x.doc, f.title, f.status item_status
                  FROM faxes x LEFT JOIN findings f ON f.finding_id = x.finding_id WHERE x.direction='in' ORDER BY x.created_at DESC LIMIT 400""") \
        if has_table("faxes") else []
    want: set[str] = set()
    for g in got:
        d = json.loads(g.pop("doc") or "{}")
        keep = ("document_type", "decision", "payer", "decision_date", "deadline", "amount", "summary", "matches", "error", "received_at",
                "needs", "tied", "tied_by", "suggested", "procedure_codes")
        g.update({k: d.get(k) for k in keep})
        want.update(d.get("suggested") or [])
        if SHOW_PHI:
            g["patient"] = _name(d.get("patient_last"), d.get("patient_first"))
            g["claim_numbers"] = d.get("claim_numbers") or []
    if want:
        names = {r["finding_id"]: r for r in rows(f"SELECT finding_id, title, payer_group, service_date, procedure_code, patient_control_number FROM findings "
                                                     f"WHERE finding_id IN ({','.join('?' * len(want))})", tuple(want))}
        for g in got:       # the items a person may tie it to, said plainly (the claim number only where names are on)
            g["suggested"] = [{k: v for k, v in names[i].items() if SHOW_PHI or k != "patient_control_number"} for i in g.get("suggested") or [] if i in names]
    now = datetime.now(timezone.utc)
    for s in sent:
        s["number"] = fax.pretty(s["number"])
        try:
            age = (now - datetime.fromisoformat(s["created_at"])).total_seconds()
        except (TypeError, ValueError):
            age = 1e9
        s["unsettled"] = s["status"] == "sending" and age > RESERVE_STALE_S       # a person can say whether it went out
    conn = db()
    try:
        due = fax.followups(conn)
    finally:
        conn.close()
    behind = _data_behind_days()
    gap = _feed_gap() if behind is not None and behind > STALE_APPEAL_DAYS else None
    for d in due:
        if d.get("number"):
            d["number"] = fax.pretty(d["number"])
        # a request that says the claim is unpaid, while the remittances are behind: listed for a person, not sent by the
        # daily run; the person checks EMA first and sends it ("Still unpaid in EMA: send it", stale_ok)
        act = d.get("resend") if d.get("action") == "person" else d.get("action")
        if act in ("second_request", "payment") and behind is not None and behind > STALE_APPEAL_DAYS:
            d["stale"] = True
            if not d.get("hold") and d.get("action") != "person":
                d["hold"] = stale_followup_why(behind, gap)
    prov = fax.provider()
    return {"configured": prov is not None or fax.line() is not None, "sends": prov is not None, "not_here": fax.not_here(),
            "line": fax.pretty(os.environ.get("SRFAX_CALLER_ID")), "practice_missing": _practice_missing(),
            "review": bool(os.environ.get("ANTHROPIC_API_KEY")), "sent": sent, "received": got, "due": due}


@app.get("/api/faxes/payers")
def fax_payers(_: str = Depends(require_auth)):
    """Each payer with items to appeal, with its confirmed fax numbers (who saved each and when, the number it replaced,
    and whether a person has confirmed a changed one for the follow-ups) and the phone and website its remittances give
    (where to look the numbers up)."""
    from . import fax
    cond, params = _appealable_cond()
    open_ = {r["payer_group"]: r for r in rows(f"""SELECT payer_group, payer_kind, COUNT(*) n, COALESCE(SUM(shortfall_cents),0) cents FROM findings
                                                 WHERE {cond} AND status IN ('open','in_progress') AND COALESCE(stale,0)=0 AND claim_key IS NOT NULL
                                                   AND (appeal_deadline IS NULL OR appeal_deadline >= ?) AND payer_group IS NOT NULL
                                                 GROUP BY payer_group""", tuple(params) + (_day(0),))}
    contact: dict[str, dict] = {}
    for r in rows("""SELECT f.payer_group, t.payer_contact_phone phone, t.payer_contact_url url, COUNT(*) n FROM findings f
                     JOIN transactions t ON t.transaction_key = f.transaction_key
                     WHERE f.payer_group IS NOT NULL AND (t.payer_contact_phone IS NOT NULL OR t.payer_contact_url IS NOT NULL)
                     GROUP BY 1, 2, 3 ORDER BY n DESC""") if has_table("transactions") else []:
        contact.setdefault(r["payer_group"], {"phone": r["phone"], "url": r["url"]})
    saved: dict[str, dict] = {}
    for r in rows("SELECT * FROM payer_fax") if has_table("payer_fax") else []:
        if not fax.digits(r.get("number")):
            continue            # removed: nothing to show, and nothing is sent there
        try:
            hist = json.loads(r.get("history") or "[]")
        except ValueError:
            hist = []
        saved.setdefault(r["payer_group"], {})[r["purpose"]] = {
            "number": fax.pretty(r["number"]), "by": r["confirmed_by"], "at": r["confirmed_at"], "previous": fax.pretty(r.get("previous")) if r.get("previous") else "",
            "changed_at": r.get("changed_at"), "confirmed": r.get("auto_ok") is None or bool(r.get("auto_ok")), "checked_by": r.get("checked_by"),
            "history": [dict(h, number=fax.pretty(h.get("number")) if h.get("number") else "removed") for h in hist[-6:] if isinstance(h, dict)]}
    out = []
    for pg in sorted(set(open_) | set(saved), key=lambda g: -((open_.get(g) or {}).get("cents") or 0)):
        o = open_.get(pg) or {}
        # a group of many insurers keeps no number (one saved before is not used): its appeals are faxed by hand
        shared = fax.shared_group(pg)
        out.append({"payer_group": pg, "payer_kind": o.get("payer_kind"), "n": o.get("n") or 0, "cents": o.get("cents") or 0,
                    "numbers": {} if shared else saved.get(pg, {}), "by_hand": fax.shared_why(pg) if shared else None,
                    "no_fax": fax.no_fax_short(pg, 1), **contact.get(pg, {})})
    return {"payers": out, "owner": bool(OWNER_PASSWORD)}


def _owner_actor(user: str) -> str:
    return f"{user} (owner's password)" if OWNER_PASSWORD else user


@app.post("/api/faxes/payer", dependencies=[Depends(require_xhr)])
def fax_payer_save(body: dict = Body(default={}), user: str = Depends(require_owner)):
    """Save a payer's fax number for appeals ('appeal') or for its second level ('level_2'), as a person confirmed it
    from the payer's own papers; an empty number removes it. A number decides where patient records are faxed, so adding
    or changing one takes the owner's password where one is set. Who and when are kept. A number that replaces another is
    not used by a retry or a follow-up until a person confirms it (/api/faxes/payer/confirm)."""
    from . import fax
    pg = str(body.get("payer_group") or "").strip()
    purpose = str(body.get("purpose") or "appeal")
    number = str(body.get("number") or "").strip()
    if not pg or len(pg) > 200 or purpose not in FAX_PURPOSES:
        raise HTTPException(400, "payer_group and purpose ('appeal' or 'level_2') are required")
    if number and any(ch.isdigit() and ch not in "0123456789" for ch in number):
        raise HTTPException(400, "Type the fax number with the ordinary digits 0 to 9.")
    if number and not fax.digits(number):
        raise HTTPException(400, "That is not a US fax number (ten digits).")
    if number and fax.shared_group(pg):
        raise HTTPException(400, fax.shared_why(pg))
    before = _payer_number(pg, purpose)
    if not one("SELECT 1 FROM findings WHERE payer_group=? LIMIT 1", (pg,)) and not before:
        raise HTTPException(404, "No payer by that name.")
    _record_action({"kind": "payer_fax", "payer_group": pg, "purpose": purpose, "number": fax.digits(number), "actor": _owner_actor(user), "at": _fax_now()})
    changed = bool(before) and bool(number) and fax.digits(number) != before
    return {"ok": True, "number": fax.pretty(number), "previous": fax.pretty(before) if changed else "", "needs_confirm": changed}


@app.post("/api/faxes/payer/confirm", dependencies=[Depends(require_xhr)])
def fax_payer_confirm(body: dict = Body(default={}), user: str = Depends(require_owner)):
    """A person confirms a payer's changed fax number, as the page showed it: retries and follow-ups may use it from now on."""
    from . import fax
    pg, purpose = str(body.get("payer_group") or "").strip(), str(body.get("purpose") or "appeal")
    if not pg or purpose not in FAX_PURPOSES:
        raise HTTPException(400, "payer_group and purpose ('appeal' or 'level_2') are required")
    if fax.shared_group(pg):
        raise HTTPException(400, fax.shared_why(pg))
    cur = _payer_number(pg, purpose)
    if not cur or fax.digits(str(body.get("number") or "")) != cur:
        raise HTTPException(409, "That is no longer the number saved. Open the Faxes page again.")
    _record_action({"kind": "payer_fax_check", "payer_group": pg, "purpose": purpose, "number": cur, "actor": _owner_actor(user), "at": _fax_now()})
    return {"ok": True, "number": fax.pretty(cur)}


def _mark_held(r: dict, doc: dict, why: str) -> None:
    """A failed fax that is not tried again by itself: the reason is kept on it, and the item goes to a person."""
    if doc.get("held") != why:
        _record_action({"kind": "fax", "record": {"fax_key": r["fax_key"], "doc": dict(doc, held=why), "updated_at": _fax_now()}})


HELD_BEHIND = "held_while_behind"       # a fax's hold that lasts only while the remittances are behind (_retry, _fax_refresh)


def _retry(prov, r: dict, doc: dict) -> str:
    """Another try for a fax the payer's line did not take: to the number saved for it now, and only if that is the number
    it went to (a number replaced or removed since sends nothing: a person decides). Never for money the store shows paid
    (fax.paid_on_file: a person's call, as the follow-ups hold it), and a request that says the claim is unpaid not while
    the remittances are behind (it waits for them, as the follow-ups do). Returns 'sent', 'held' or 'skip'."""
    from . import fax
    f = one("SELECT * FROM findings WHERE finding_id=?", (r["finding_id"],))
    if not f or f.get("status") != "appealed":
        return "skip"
    doc = {k: v for k, v in doc.items() if k != HELD_BEHIND}        # a hold for the remittances alone is decided again here
    paid = fax.paid_on_file(f.get("evidence"))
    if paid:
        # whatever the item's status says, nothing goes again by itself for money the store shows paid (fax.followups lists
        # it for a person): the item's history says why, once, in the follow-up run's words
        if not (has_table("finding_events") and one("SELECT 1 FROM finding_events WHERE finding_id=? AND note=? LIMIT 1", (f["finding_id"], paid))):
            _record_action({"kind": "event", "event": "chase_held", "finding_id": f["finding_id"], "note": paid, "actor": "retry"})
        _mark_held(r, doc, paid)
        return "held"
    behind = _data_behind_days() if r["purpose"] in ("second_request", "payment") else None
    if behind is not None and behind > STALE_APPEAL_DAYS:
        # the claim may have been paid since: listed for a person (who checks EMA and sends it), and tried again by itself
        # once the remittances are current (_fax_refresh)
        _mark_held(r, dict(doc, **{HELD_BEHIND: True}), stale_followup_why(behind, _feed_gap()))
        return "held"
    level = int(r["level"] or 1)
    conn = db()
    try:
        nb, hold = fax.number_hold(conn, f, level, [r])
    finally:
        conn.close()
    if hold:
        _mark_held(r, doc, hold)
        return "held"
    p = _stored_packet(r["packet_id"]) if r["packet_id"] else None
    why = "The packet that was faxed cannot be found." if not p or not p.get("pdf") else _packet_problem(r["packet_id"], f, first=r["purpose"] == "appeal")
    if why:
        _mark_held(r, doc, f"It was not tried again: {why} Open the item and send it by hand.")
        return "held"
    number = nb["number"]
    front = fax.letter_pdf(*doc["front"]) if doc.get("front") else None
    body, pages = _fax_doc(prov, f, number, p["pdf"], doc.get("subject"), front, level=level)

    def still_ok(conn) -> str | None:
        cur = conn.execute("SELECT status FROM faxes WHERE fax_key=?", (r["fax_key"],)).fetchone()
        if not cur or cur[0] != "failed":
            return "It is no longer a failed fax."
        if conn.execute("""SELECT 1 FROM faxes WHERE direction='out' AND finding_id=? AND purpose=? AND COALESCE(level,1)=? AND created_at > ?""",
                        (r["finding_id"], r["purpose"], level, r["created_at"])).fetchone():
            return "A later fax already carries it."
        st = conn.execute("SELECT status FROM findings WHERE finding_id=?", (r["finding_id"],)).fetchone()
        if not st or st[0] != "appealed":
            return "The item is no longer marked Sent."
        if fax.number_for(conn, f.get("payer_group"), f.get("payer_kind"), level)["number"] != number:
            return "The payer's fax number changed."
        return None
    _send_fax(prov, f, number, body, pages, packet_id=r["packet_id"], purpose=r["purpose"], level=level, actor="retry", subject=doc.get("subject"),
              extra={"attempts": int(doc.get("attempts") or 1) + 1, "front": doc.get("front"), "retry_of": r["fax_key"], "why": doc.get("why"),
                     "n": doc.get("n")}, still_ok=still_ok)
    return "sent"


def _fax_refresh(prov, use_ai: bool = True, days: int = 45, budget_s: float = 200.0, lock: str | None = None) -> dict:
    """Bring the faxes up to date: a send never settled, asked of the fax service; the state of each fax still going out;
    another try for one the payer's line did not take; and each new fax in, read by Claude and matched to the items it
    answers (a line in each one's history), and a fax not read before read again."""
    import time as _time
    from . import fax
    t0 = _time.monotonic()
    late = lambda: _time.monotonic() - t0 > budget_s       # noqa: E731
    out = {"checked": 0, "sent": 0, "failed": 0, "retried": 0, "received": 0, "reread": 0, "matched": 0, "settled": 0, "more": False, "errors": []}
    if not has_table("faxes"):
        conn = db()
        try:
            from .actions import ensure_fax_tables
            ensure_fax_tables(conn)
            conn.commit()
        finally:
            conn.close()
    now = datetime.now(timezone.utc)
    # sends never settled (the run stopped between the send and its record, or SRFax did not answer): asked of SRFax
    for r in rows("SELECT * FROM faxes WHERE direction='out' AND status='sending' AND provider=? ORDER BY created_at LIMIT 100", (prov.name,)):
        try:
            age = (now - datetime.fromisoformat(r["created_at"])).total_seconds()
        except (TypeError, ValueError):
            age = 1e9
        if age < RESERVE_STALE_S:
            continue
        if late():
            out["more"] = True
            break
        doc = json.loads(r["doc"] or "{}")
        try:
            pid = prov.find_sent(doc.get("filename") or "", r["number"], r["created_at"])
        except Exception as exc:
            why = str(exc)[:200] if isinstance(exc, fax.FaxError) else type(exc).__name__
            if doc.get("unsure") != why:
                _record_action({"kind": "fax", "record": {"fax_key": r["fax_key"], "doc": dict(doc, unsure=why), "updated_at": _fax_now()}})
            out["errors"].append(f"Could not tell whether a fax went out: {why}")
            continue
        if pid:
            _settle(r, status="queued", provider_id=pid, error=None)
            # the appeal did go out: its item is marked Sent now, as the send would have done (the person who pressed it)
            _mark_sent_appeal(dict(r, provider_id=pid), doc.get("actor") or "fax")
        else:
            _settle(r, status="failed", error="SRFax has no record of it: the run stopped before it went out.")
        out["settled"] += 1
    for q in rows("SELECT fax_key, provider_id FROM faxes WHERE direction='out' AND status='queued' AND provider=? ORDER BY created_at LIMIT 200", (prov.name,)):
        if late():
            out["more"] = True
            break
        try:
            s = prov.status(q["provider_id"])
        except fax.FaxError as exc:
            out["errors"].append(str(exc))
            break
        out["checked"] += 1
        if s["state"] == "queued":
            continue
        rec = {"fax_key": q["fax_key"], "status": s["state"], "updated_at": _fax_now()}
        if s["state"] == "sent":
            rec["sent_at"] = s.get("at") or _fax_now()
            out["sent"] += 1
        else:
            rec["error"] = s.get("error") or "the payer's line did not take it"
            out["failed"] += 1
        if s.get("pages"):
            rec["pages"] = s["pages"]
        _record_action({"kind": "fax", "record": rec})
    # another try for a fax that did not go through, unless a later fax already carried the same thing; one held only while
    # the remittances were behind (_retry) is tried again once they are current
    behind = _data_behind_days()
    current = behind is None or behind <= STALE_APPEAL_DAYS
    for r in rows("""SELECT * FROM faxes x WHERE direction='out' AND status='failed' AND provider=? AND NOT EXISTS (
                         SELECT 1 FROM faxes y WHERE y.direction='out' AND y.finding_id = x.finding_id AND y.purpose = x.purpose
                           AND COALESCE(y.level,1) = COALESCE(x.level,1) AND y.created_at > x.created_at)""", (prov.name,)):
        doc = json.loads(r["doc"] or "{}")
        if int(doc.get("attempts") or 1) >= fax.MAX_ATTEMPTS or not r["finding_id"] or (doc.get("held") and not (current and doc.get(HELD_BEHIND))):
            continue
        if late():
            out["more"] = True
            break
        _fax_lock_beat(lock)
        try:
            if _retry(prov, r, doc) == "sent":
                out["retried"] += 1
        except SendBlocked:
            pass
        except Exception as exc:        # one fax that cannot go must not stop the others, or the reading of what came in
            out["errors"].append(f"{type(exc).__name__}: {str(exc)[:200]}")
    # what came in: new faxes, and those not read before (Claude was off, or could not read it) read again
    seen = {}
    for r in rows("SELECT fax_key, provider_id, doc, created_at FROM faxes WHERE direction='in' AND provider=?", (prov.name,)):
        seen[r["provider_id"]] = (r, json.loads(r["doc"] or "{}"))
    can_read = use_ai and bool(os.environ.get("ANTHROPIC_API_KEY"))
    done = {k for k, (_r, d) in seen.items()
            if (d.get("document_type") and not d.get("error")) or d.get("tied_by") == "person" or int(d.get("tries") or 0) >= MAX_READS or not can_read}
    try:
        inbox = prov.inbox(days)
    except fax.FaxError as exc:
        out["errors"].append(str(exc))
        inbox = []
    for item in inbox:
        if item["key"] in done:
            continue
        if late():
            out["more"] = True
            break
        _fax_lock_beat(lock)
        try:
            pdf = prov.fetch(item["key"])
        except fax.FaxError as exc:
            out["errors"].append(str(exc))
            continue
        prev = seen.get(item["key"])
        tries = int((prev[1] if prev else {}).get("tries") or 0)
        cls: dict = {}
        if can_read:
            tries += 1
            try:
                cls = fax.classify(pdf)
            except Exception as exc:
                cls = {"error": f"Claude could not read it ({type(exc).__name__})" + (": it is read again on the next run." if tries < MAX_READS else ".")}
        conn = db()
        try:
            m = fax.match_inbound(conn, cls, item.get("caller")) if cls and not cls.get("error") else \
                {"tied": [], "how": None, "suggested": [], "needs": None, "matches": []}
        finally:
            conn.close()
        tied = m["tied"]
        first = one("SELECT payer_group FROM findings WHERE finding_id=?", (tied[0],)) if tied else None
        now_iso = _fax_now()
        rec = {"fax_key": f"in:{prov.name}:{item['key']}", "direction": "in", "finding_id": tied[0] if tied else None,
               "payer_group": (first or {}).get("payer_group"), "number": item.get("caller"), "purpose": cls.get("document_type") or "unread",
               "provider": prov.name, "provider_id": item["key"], "status": "received", "pages": item.get("pages"), "updated_at": now_iso,
               "doc": dict(cls, tied=tied, tied_by=m["how"], suggested=m["suggested"], needs=m["needs"], matches=m["matches"],
                           received_at=item.get("received_at"), tries=tries)}
        if prev:
            rec["fax_key"] = prev[0]["fax_key"]          # read again: it keeps the time it came in (follow-ups go by it)
            out["reread"] += 1
        else:
            rec["created_at"] = now_iso
            out["received"] += 1
        _record_action({"kind": "fax", "record": rec})
        if tied:
            out["matched"] += 1
            words = {"appeal_decision": "decision", "records_request": "request for records", "payment_notice": "payment notice",
                     "denial": "denial", "acknowledgement": "acknowledgement"}.get(cls.get("document_type"), "fax")
            note = f"Fax from the payer ({words}" + (f", {cls['decision'].replace('_', ' ')}" if cls.get("decision") not in (None, "", "none") else "") \
                + f"): {(cls.get('summary') or '')[:300]}"
            for fid in tied:
                _record_action({"kind": "event", "finding_id": fid, "note": note, "actor": "fax", "at": now_iso})
    return out


def _send_followup(prov, d: dict, actor: str, person_number: str | None = None, stale_ok: bool = False) -> dict:
    """One follow-up from fax.followups: the letter on top, the original packet behind it (and, for the next level, the
    payer's decision), to the number saved now for that level. A send nobody pressed a button for waits for a person when
    the number is not one it may use (fax.number_hold), and so does a request that says the claim is unpaid while the
    remittances are behind. person_number: a person pressed send for this one item, with this number in front of them;
    stale_ok: that person checked in EMA that the claim is still unpaid, which lets such a request go while the remittances
    are behind (as "Still unpaid in EMA" does for the first appeal). The daily run never sends one then."""
    from . import fax
    from .appeals import PRACTICE
    fid = d["finding_id"]
    out = {"finding_id": fid, "action": d["action"], "payer_group": d.get("payer_group")}

    def held(reason: str) -> dict:
        return dict(out, status="held", reason=reason)
    if d["action"] == "person":
        if d.get("paid"):
            # the store shows the money paid (fax.paid_on_file): the item's history says why its follow-ups stopped, once
            if not (has_table("finding_events") and one("SELECT 1 FROM finding_events WHERE finding_id=? AND note=? LIMIT 1", (fid, d["why"]))):
                _record_action({"kind": "event", "event": "chase_held", "finding_id": fid, "note": d["why"], "actor": actor})
        if not (person_number and d.get("resend") in RESEND):
            return held(d["why"])
        d = dict(d, action=d["resend"])         # a person sends it again
        out["action"] = d["action"]
    detail = finding_detail(fid, "ok")
    f = detail["finding"]
    level = int(d.get("level") or 1)
    conn = db()
    try:
        earlier = [dict(x) for x in conn.execute("SELECT * FROM faxes WHERE direction='out' AND finding_id=? AND COALESCE(level,1)=? ORDER BY created_at",
                                                 (fid, level))]
        nb, hold = fax.number_hold(conn, f, level, earlier)
    finally:
        conn.close()
    route = fax.no_fax(f.get("payer_group"), level, f.get("payer_name"))
    if route:                   # never faxed at this level, a person's send included: filed on the payer's portal or by mail
        return held(route)
    number = nb["number"]
    if person_number is not None:
        if not number:
            return held(hold or "No fax number is saved for it.")
        if fax.digits(person_number) != number:
            return held(f"The fax number for {f.get('payer_group') or 'the payer'} changed while the page was open. Open the Faxes page again.")
    elif hold:
        return held(hold)
    behind = _data_behind_days()
    if d["action"] in ("second_request", "payment") and behind is not None and behind > STALE_APPEAL_DAYS \
            and not (person_number is not None and stale_ok is True):
        return held(stale_followup_why(behind, _feed_gap()))
    missing = _practice_missing()
    if missing:
        return held("The letters still lack the practice's details: set " + ", ".join(missing) + " in the host's settings.")
    outs = _faxes_out(fid)
    packet_id = next((o["packet_id"] for o in reversed(outs) if o.get("packet_id")), None)
    p = _stored_packet(packet_id) if packet_id else None
    if not p or not p.get("pdf"):
        return held("The appeal packet that was faxed cannot be found.")
    why = _packet_problem(packet_id, f)
    if why:
        return held(f"The packet that was faxed cannot go again: {why} Open the item and send it by hand.")
    dec = d.get("decision") or {}
    facts = dict(f, payer_claim_control_number=(detail.get("claim") or {}).get("payer_claim_control_number"))
    title, paras = fax.follow_up_letter(d["action"], facts, outs, {"date": dec.get("decision_date") or dec.get("received")}, PRACTICE, d.get("n") or 1)
    blanks = fax.letter_blanks(title + "\n" + "\n".join(paras))
    if blanks:
        return held(f"The letter still has blanks ({', '.join(blanks[:4])}).")
    extra = None
    if d["action"] == "next_level" and has_table("faxes"):
        # the payer's own decision goes with the request: only a fax tied to this item by its claim and from the payer's
        # number, or by a person (never one matched on a name and a date)
        conn = db()
        try:
            mine = [x for x in fax._ties(conn).get(fid, []) if x.get("provider") == prov.name and x["_doc"].get("decision") == "upheld"]
        finally:
            conn.close()
        if mine:
            try:
                extra = prov.fetch(mine[-1]["provider_id"])
            except fax.FaxError:
                extra = None
    try:
        body, pages = _fax_doc(prov, f, number, p["pdf"], title, fax.letter_pdf(title, paras), extra, level=level)
    except Exception as exc:
        return held(f"The fax could not be put together ({type(exc).__name__}): open the item and send it by hand.")

    def still_ok(conn) -> str | None:
        # checked again in the step that reserves the send: still due, by nobody else meanwhile, to the same number
        now = fax.followups(conn, ids=[fid])
        hit = next((x for x in now if int(x.get("level") or 1) == level and (x["action"] == d["action"]
                    or (person_number and x["action"] == "person" and x.get("resend") == d["action"]))), None)
        if not hit:
            return "It is no longer due: it was sent, or the payer answered, meanwhile."
        if fax.number_for(conn, f.get("payer_group"), f.get("payer_kind"), level)["number"] != number:
            return "The payer's fax number changed meanwhile."
        if person_number is None and hit.get("hold"):
            return hit["hold"]
        return None
    try:
        sent = _send_fax(prov, f, number, body, pages, packet_id=packet_id, purpose=d["action"], level=level, actor=actor, subject=title,
                         extra={"front": [title, paras], "why": d["why"], "n": d.get("n") or 1}, still_ok=still_ok)
    except SendBlocked as exc:
        return dict(out, status="skipped", reason=str(exc))
    except fax.FaxError as exc:
        return held(str(exc))
    _record_action({"kind": "event", "finding_id": fid, "actor": actor,
                    "note": f"{title}: faxed to {fax.pretty(number)} (fax {sent['provider_id']}, {pages} pages). Why: {d['why']}."})
    return dict(out, status="sent", fax_id=sent["provider_id"], number=fax.pretty(number), title=title)


def stale_followup_why(behind: int, gap: dict | None = None) -> str:
    """Why a request that says the claim is unpaid waits while the remittances are behind, and how a person sends it. When
    weeks with no remittance from any payer hold the feed back (`gap`, from _feed_gap), it names them (misuse check CH-6)."""
    from .detect import gap_words
    first = (f"{gap_words(gap)} The claim may have been paid in those weeks." if gap else
             f"The newest remittance RemitWatch has is {behind} days old, so the claim may have been paid since.")
    return (f"{first} A request that says "
            "it is unpaid is not sent by itself until the remittances are current. Check EMA or the payer's portal: if it is still "
            "unpaid, press \"Still unpaid in EMA: send it\" on the Faxes page.")


def _send_followups(prov, actor: str, ids: list[str] | None = None, budget_s: float = 240.0, person_number: str | None = None,
                    lock: str | None = None, stale_ok: bool = False) -> dict:
    import time as _time
    from . import fax
    t0 = _time.monotonic()
    conn = db()
    try:
        due = fax.followups(conn, ids=ids)
    finally:
        conn.close()
    results, more = [], False
    for d in due:
        if _time.monotonic() - t0 > budget_s:
            more = True
            break
        _fax_lock_beat(lock)
        try:
            results.append(_send_followup(prov, d, actor, person_number=person_number, stale_ok=stale_ok))
        except Exception as exc:        # one item that cannot go must not stop the ones after it, run after run
            results.append({"finding_id": d["finding_id"], "action": d["action"], "payer_group": d.get("payer_group"), "status": "held",
                            "reason": f"It stopped on an error ({type(exc).__name__}: {str(exc)[:160]}). The others went on."})
    return {"results": results, "sent": sum(r["status"] == "sent" for r in results), "held": sum(r["status"] == "held" for r in results), "more": more}


def _line_or_503():
    from . import fax
    prov = fax.provider()
    if prov is None:
        raise HTTPException(503, fax.not_here() or "The fax line is not set up yet (SRFAX_ACCESS_ID and SRFAX_ACCESS_PWD).")
    return prov


@app.post("/api/faxes/refresh", dependencies=[Depends(require_xhr)])
def faxes_refresh(user: str = Depends(require_auth)):
    prov = _line_or_503()
    try:
        with fax_run(user) as lock:
            return _fax_refresh(prov, lock=lock)
    except FaxBusy as exc:
        raise HTTPException(409, str(exc))


@app.post("/api/faxes/follow_up", dependencies=[Depends(require_xhr)])
def faxes_follow_up(body: dict = Body(default={}), user: str = Depends(require_auth)):
    """Send the follow-ups that are due (all of them, or the items named). number, with one item named: a person sends that
    one to the number the page showed them, also where a send nobody pressed a button for would wait (a number changed
    since its last fax; a line that never took it, tried again). stale_ok (JSON true only, with that one item and its
    number): the person checked in EMA that the claim is still unpaid, so a second request or a payment request goes
    although the remittances are behind."""
    prov = _line_or_503()
    ids = body.get("finding_ids")
    ids = [str(i) for i in ids][:500] if isinstance(ids, list) else None
    person = str(body.get("number") or "") if ids and len(ids) == 1 and body.get("number") else None
    stale_ok = person is not None and body.get("stale_ok") is True
    try:
        with fax_run(user) as lock:
            return _send_followups(prov, user, ids, person_number=person, lock=lock, stale_ok=stale_ok)
    except FaxBusy as exc:
        raise HTTPException(409, str(exc))


SAYS = {"upheld": ("appeal_decision", "upheld"), "overturned": ("appeal_decision", "overturned"),
        "partially_overturned": ("appeal_decision", "partially_overturned"), "records_request": ("records_request", "none"),
        "other": ("other", "none")}


@app.post("/api/faxes/match", dependencies=[Depends(require_xhr)])
def faxes_match(body: dict = Body(default={}), user: str = Depends(require_auth)):
    """Tie a fax that came in to the item it answers, where reading it did not (or tied it to the wrong one). says: what the
    fax decides, as the person read it ('upheld', 'overturned', 'partially_overturned', 'records_request', 'other'); without
    it, a fax not read yet is read now where Claude can. From then on it answers that item."""
    from . import fax
    key, fid = str(body.get("fax_key") or ""), str(body.get("finding_id") or "")
    if not re.fullmatch(FAX_KEY, key) or not re.fullmatch(FAX_KEY, fid):
        raise HTTPException(400, "fax_key and finding_id are required")
    says = body.get("says")
    if says is not None and says not in SAYS:
        raise HTTPException(400, "says is one of " + ", ".join(SAYS))
    r = one("SELECT * FROM faxes WHERE fax_key=? AND direction='in'", (key,)) if has_table("faxes") else None
    f = one("SELECT finding_id, payer_group FROM findings WHERE finding_id=?", (fid,))
    if not r or not f:
        raise HTTPException(404, "No such fax or item.")
    doc = json.loads(r["doc"] or "{}")
    if says:
        doc.update(document_type=SAYS[says][0], decision=SAYS[says][1], read_by=user)
        doc.pop("error", None)
    elif (doc.get("error") or not doc.get("document_type")) and os.environ.get("ANTHROPIC_API_KEY"):
        line = fax.provider() or fax.line()
        try:
            got = fax.classify(line.fetch(r["provider_id"])) if line else None
        except Exception:
            got = None
        if got:
            doc.update(got)
            doc.pop("error", None)
    before = [i for i in (doc.get("tied") or ([r["finding_id"]] if r["finding_id"] else [])) if i != fid]
    doc.update(tied=[fid], tied_by="person", tied_at=_now_iso(), tied_actor=user, needs=None,
               matches=[fid] + [m for m in doc.get("matches") or [] if m != fid])
    _record_action({"kind": "fax", "record": {"fax_key": key, "finding_id": fid, "payer_group": f["payer_group"], "doc": doc,
                                              "purpose": doc.get("document_type") or r["purpose"], "updated_at": _fax_now()}})
    _record_action({"kind": "event", "finding_id": fid, "actor": user, "note": f"Fax from the payer tied to this item: {(doc.get('summary') or '')[:300]}"})
    for other in before:
        _record_action({"kind": "event", "finding_id": other, "actor": user, "note": "A payer's fax tied to this item was moved to another item by a person."})
    return {"ok": True, "read": bool(doc.get("document_type")) and not doc.get("error")}


@app.post("/api/faxes/settle", dependencies=[Depends(require_xhr)])
def faxes_settle(body: dict = Body(default={}), user: str = Depends(require_auth)):
    """A person says whether a fax whose send never settled went out, from SRFax's own list of sent faxes: it did (it counts
    as sent) or it did not (it counts as a failed try, and the line tries it again)."""
    key = str(body.get("fax_key") or "")
    if not re.fullmatch(FAX_KEY, key) or not isinstance(body.get("went"), bool):
        raise HTTPException(400, "fax_key and went (true or false) are required")
    r = one("SELECT * FROM faxes WHERE fax_key=? AND direction='out'", (key,)) if has_table("faxes") else None
    if not r:
        raise HTTPException(404, "No such fax.")
    if r["status"] != "sending":
        raise HTTPException(409, "That fax is settled already.")
    doc = dict(json.loads(r["doc"] or "{}"), settled_by=user)
    if body["went"]:
        _settle(r, status="sent", sent_at=_fax_now(), error=None, doc=doc)
        # an appeal that went out: its item is marked Sent, so its follow-ups start
        _mark_sent_appeal(dict(r, provider_id=r.get("provider_id") or "checked in SRFax by a person"), user)
    else:
        _settle(r, status="failed", error="Not sent: a person checked SRFax's sent faxes.", doc=doc)
    if r["finding_id"]:
        _record_action({"kind": "event", "finding_id": r["finding_id"], "actor": user,
                        "note": f"A fax whose send never settled was checked in SRFax: {'it went out' if body['went'] else 'it did not go out'}."})
    return {"ok": True}


@app.get("/api/faxes/pdf")
def faxes_pdf(key: str = Query(..., max_length=160), _: str = Depends(require_auth)):
    """A fax as a PDF: one that came in is fetched from the fax line; one that went out is its packet."""
    from . import fax
    r = one("SELECT * FROM faxes WHERE fax_key=?", (key,)) if has_table("faxes") else None
    if not r:
        raise HTTPException(404, "No such fax.")
    if r["direction"] == "in":
        prov = fax.provider() or fax.line()         # opening a fax that came in sends nothing: any site with the line can
        if prov is None:
            raise HTTPException(503, "The fax line is not set up.")
        try:
            pdf = prov.fetch(r["provider_id"])
        except fax.FaxError as exc:
            raise HTTPException(502, str(exc))
    else:
        p = _stored_packet(r["packet_id"]) if r["packet_id"] else None
        pdf = (p or {}).get("pdf") or b""
    if not pdf:
        raise HTTPException(404, "The fax could not be found.")
    safe = re.sub(r"[^A-Za-z0-9._-]", "-", key)[:80]
    return StreamingResponse(iter([pdf]), media_type="application/pdf",
                             headers={"Content-Disposition": f'inline; filename="fax-{safe}.pdf"', "Cache-Control": "no-store"})


@app.post("/api/faxes/test", dependencies=[Depends(require_xhr)])
def faxes_test(_: str = Depends(require_auth)):
    from . import fax
    prov = fax.provider() or fax.line()
    if prov is None:
        return {"ok": False, "steps": [{"step": "Settings", "ok": False, "detail": "SRFAX_ACCESS_ID and SRFAX_ACCESS_PWD are not set"}]}
    got = prov.check()
    if fax.not_here():
        got = dict(got, steps=list(got.get("steps") or []) + [{"step": "This site", "ok": True, "detail": fax.not_here()}])
    return JSONResponse(got, headers={"Cache-Control": "no-store"})


@app.api_route("/api/cron/faxes", methods=["GET", "POST"], include_in_schema=False)
def cron_faxes(request: Request, credentials: Optional[HTTPBasicCredentials] = Depends(security)):
    """Every day: the faxes brought up to date and the follow-ups that are due sent (a week apart for each item), on the one
    site that sends faxes (REMITWATCH_FAX_SENDER), and never while another fax run is going."""
    _cron_auth(request, credentials)
    from . import fax
    if runtime.serverless() and runtime.preview():
        raise HTTPException(409, "This is a preview deployment. Only the production site sends faxes.")
    prov = fax.provider()
    if prov is None:
        return {"skipped": fax.not_here() or "The fax line is not set up."}
    budget = max(60.0, runtime.max_seconds() - 60.0) if runtime.serverless() else 3600.0
    try:
        with fax_run("daily run") as lock:
            got = _fax_refresh(prov, budget_s=budget / 2, lock=lock)
            return {"refresh": got, "follow_up": _send_followups(prov, "daily run", budget_s=budget / 2, lock=lock)}
    except FaxBusy as exc:
        return {"skipped": str(exc)}


# --------------------------------------------------------------------------- a person's decision on a held EMA report
_FILE_ID = re.compile(r"[A-Za-z0-9:_.-]{8,128}")


@app.post("/api/ema/reports/{file_id}/decision", dependencies=[Depends(require_xhr)])
def ema_report_decision(file_id: str, request: Request, body: dict = Body(default={}), user: str = Depends(require_auth)):
    """Apply or discard a report the reader held (a filtered-looking A/R, a Production file that would replace a box with
    much less, a file three runs could not finish): remitwatch.ema_reports.decide. Apply lets the next run read it again
    from its kept copy; discard closes it. On a server the decision is made on the store and a run is asked for; on Vercel it
    goes through the journal (actions.apply, kind ema_report_decision) and a file the ledger held as too slow has its tries
    cleared (ema_ledger.LedgerHandle.clear_attempt), so the next run reads it again. Body {"decision": "apply"|"discard"}."""
    decision = str(body.get("decision") or "")
    if decision not in ("apply", "discard"):
        raise HTTPException(400, "The decision is apply or discard.")
    if not _FILE_ID.fullmatch(file_id or ""):
        raise HTTPException(400, "That is not a report's id.")
    if not has_table("ema_report_files"):
        raise HTTPException(404, "No EMA report is on file.")
    row = one("SELECT status FROM ema_report_files WHERE file_id=?", (file_id,))
    if row is None:
        raise HTTPException(404, "No report with that id is on file.")
    if row["status"] != "held":
        raise HTTPException(409, "Only a held report waits for a decision." if row["status"] != "refused"
                            else "A refused report cannot be applied. Export it again from EMA.")
    if runtime.serverless():
        from . import cloud, ema_ledger
        st = cloud.SERVING_COPY.state()
        if st is None:
            raise HTTPException(503, "Storage is not connected yet. Open /setup.")
        cloud.SERVING_COPY.record({"kind": "ema_report_decision", "file_id": file_id, "decision": decision, "actor": user})
        if decision == "apply":
            ema_ledger.LedgerHandle(st).clear_attempt(file_id)
            started = cloud.trigger_cycle(base_url(request), trigger="decision")
        else:
            started = False
        return {"ok": True, "file_id": file_id, "decision": decision, "run_started": bool(started)}
    from . import ema_reports
    conn = db()
    try:
        res = ema_reports.decide(conn, file_id, decision, user)
        conn.commit()
    finally:
        conn.close()
    if res.get("error"):
        raise HTTPException(409, res["error"])
    started = False
    if decision == "apply":
        from . import intake
        started = intake.kick(os.path.dirname(os.path.abspath(DB_PATH)))
    return {"ok": True, "file_id": file_id, "decision": decision, "status": res.get("status"), "run_started": bool(started)}


# --------------------------------------------------------------------------- a line in an item's history that changes no status
EVENT_KINDS = {"posted": "Posted in EMA", "era_requested": "ERA asked for", "billed": "Billed", "outcome": "Outcome logged",
               "instructions": "Instructions copied"}


@app.post("/api/findings/{finding_id}/event", dependencies=[Depends(require_xhr)])
def finding_event(finding_id: str, body: dict = Body(default={}), user: str = Depends(require_auth)):
    """Write down work done outside RemitWatch that changes no status (a remittance posted in EMA while the denial is still
    worked, the ERA asked for, a bill sent): one line in the item's history, kept like any other (the journal on Vercel)."""
    kind = str(body.get("event") or "")
    if kind not in EVENT_KINDS:
        raise HTTPException(400, "Unknown event.")
    if not one("SELECT 1 FROM findings WHERE finding_id=?", (finding_id,)):
        raise HTTPException(404, "finding not found")
    note = " ".join(str(body.get("note") or "").split())[:300]
    _record_action({"kind": "event", "finding_id": finding_id, "event": kind, "note": f"{EVENT_KINDS[kind]}: {note}" if note else EVENT_KINDS[kind],
                    "actor": user})
    return {"ok": True}


# --------------------------------------------------------------------------- reasons hidden for a payer (actions.apply_hide_rule)
@app.get("/api/hide")
def hide_list(patterns: int = Query(0, ge=0, le=1), _: str = Depends(require_auth)):
    """The reasons hidden for a payer (payer x reason x code x modifier x payer's reason code, remitwatch.archive_rules'
    grain), with how many items each hides now: the items the rule itself let go and that Show again brings back, not the
    ones a person let go by hand. patterns=1 adds the patterns a person can decide on at that grain, with the share the
    archive saw paid later (archive_rules.hide_patterns: the column to read before hiding one)."""
    from .actions import LAST_DECISION, hide_rule_of
    from .archive_rules import hide_patterns, hide_sql
    from .insights import SHORT_LABELS
    out: dict[str, Any] = {"rules": []}
    if has_table("hide_rules"):
        events = has_table("finding_events")
        have = {r["name"] for r in rows("PRAGMA table_info(hide_rules)")}
        grain = [c for c in ("code", "modifier", "reason") if c in have]
        for r in rows(f"SELECT payer_group, signal, {''.join(c + ', ' for c in grain)}actor, at, note FROM hide_rules "
                      f"WHERE {_rules_in_force()} ORDER BY at DESC"):
            rule = hide_rule_of(r)
            cond, params = hide_sql(rule, alias="f")
            n = one(f"""SELECT COUNT(*) n, COALESCE(SUM(shortfall_cents),0) cents FROM findings f WHERE {cond}
                        AND f.status='dismissed' AND COALESCE(f.stale,0)=0 AND {LAST_DECISION if events else "'hidden'"}='hidden'""",
                    tuple(params)) or {}
            label = SHORT_LABELS.get(rule["signal"], rule["signal"]) if rule["signal"] != "*" else "Any reason"
            narrow = [x for x in (rule["code"] if rule["code"] != "*" else "", f"modifier {rule['modifier']}" if rule["modifier"] != "*" else "",
                                  rule["reason"] if rule["reason"] != "*" else "") if x]
            out["rules"].append(dict(r, **{k: rule[k] for k in ("signal", "code", "modifier", "reason")},
                                     label=label + (f" ({', '.join(narrow)})" if narrow else ""), n=n.get("n") or 0, cents=n.get("cents") or 0))
    if patterns:
        items = rows("""SELECT finding_id, payer_group, signal, category, status, line_key, paid_cents, billed_code, procedure_code, modifiers,
                               carc, shortfall_cents, stale FROM findings WHERE category IN ('denial','bundling') AND COALESCE(stale,0)=0""")
        out["patterns"] = hide_patterns(items)[:200]
    return out


def _rules_in_force() -> str:
    """SQL that keeps the hide rules in force: a removed one stays in the table, dated (actions.apply_hide_rule)."""
    from .actions import rules_in_force
    conn = db()
    try:
        return rules_in_force(conn)
    finally:
        conn.close()


def _hide_grain_ok(raw: dict) -> bool:
    """A hide rule's finer grain as typed: a code (or a comma list, or an @family), one modifier (or none), and the payer's
    reason code (97 or CO-97); '*' for any."""
    code, mod, reason = raw.get("code") or "*", raw.get("modifier") or "*", raw.get("reason") or "*"
    return bool(re.fullmatch(r"\*|@[a-z_]{2,30}|[A-Z0-9]{3,7}(,[A-Z0-9]{3,7}){0,19}", code if code.startswith("@") else code.upper())
                and re.fullmatch(r"\*|none|[A-Z0-9]{2}", mod if mod in ("*", "none") else mod.upper())
                and re.fullmatch(r"\*|((CO|PR|OA|PI|CR)-?)?[A-Z0-9]{1,5}", reason.upper()))


def _hide_size(pg: str, sig: str, skip: list | None = None, rule: dict | None = None) -> dict:
    """What hiding one reason for one payer (at the rule's grain, when given) lets go now, from the copy the dashboard
    serves: the open items, their dollars, and how many of them are still inside their filing window. An item with a fax
    going out now (_sending_now) is left out: let go now, the send would find it let go (or mark it Sent over the hide).
    They are counted apart (in_flight_n), and their ids added to `skip` when given (the hide's journal entry carries them,
    so every copy leaves them out)."""
    from .actions import hide_candidates, hide_rule_of
    r = rule or hide_rule_of({"payer_group": pg, "signal": sig})
    conn = db()
    try:
        ids = hide_candidates(conn, pg, r["signal"], _now_iso(), r["code"], r["modifier"], r["reason"])
        flying = _sending_now(conn, ids)
        if skip is not None:
            skip.extend(sorted(flying))
        ids = [i for i in ids if i not in flying]
        n = cents = live_n = live_cents = 0
        today = _day(0)                     # inside its window until midnight where the practice is
        for k in range(0, len(ids), 500):
            part = ids[k:k + 500]
            for c, dl in conn.execute(f"SELECT COALESCE(shortfall_cents,0), appeal_deadline FROM findings WHERE finding_id IN ({','.join('?' * len(part))})", part):
                n, cents = n + 1, cents + c
                if dl and dl >= today:
                    live_n, live_cents = live_n + 1, live_cents + c
        return {"n": n, "cents": cents, "in_window_n": live_n, "in_window_cents": live_cents, "in_flight_n": len(flying)}
    finally:
        conn.close()


@app.get("/api/hide/preview")
def hide_preview(payer_group: str = Query(..., max_length=200), signal: str = Query(..., max_length=60),
                 code: str = Query("*", max_length=200), modifier: str = Query("*", max_length=10), reason: str = Query("*", max_length=12),
                 _: str = Depends(require_auth)):
    """Before a reason is hidden for a payer (at payer x reason, and where given x code x modifier x the payer's reason
    code, as POST /api/hide takes them): how many open items it lets go now, their dollars, how many of them are still
    inside their filing window, and that every future item of that kind for that payer will be let go too."""
    from .actions import NEVER_HIDDEN, hide_rule_of
    sig = (signal or "").strip() or "*"
    raw = {"code": code.strip() or "*", "modifier": modifier.strip() or "*", "reason": reason.strip() or "*"}
    if not (sig == "*" or re.fullmatch(r"[A-Z0-9_]{2,60}", sig)) or sig in NEVER_HIDDEN or not _hide_grain_ok(raw):
        raise HTTPException(400, "That reason is not hidden for a payer.")
    rule = hide_rule_of(dict(raw, payer_group=payer_group.strip(), signal=sig))
    if rule["signal"] == "*" and rule["code"] == "*" and rule["reason"] == "*":
        raise HTTPException(400, "Name a reason, a code or the payer's reason code: a whole payer is never hidden.")
    return dict(_hide_size(payer_group.strip(), rule["signal"], rule=rule), future=True, owner=bool(OWNER_PASSWORD))


@app.post("/api/hide", dependencies=[Depends(require_xhr)])
def hide_add(request: Request, body: dict = Body(default={}), user: str = Depends(require_auth)):
    """Hide one reason for one payer, at payer x reason, and where given x code (one, a comma list, or an '@family') x
    modifier (one, or 'none') x the payer's reason code: its open items go to Let go with the note, and so do new ones;
    nothing a person has worked is touched. Adding a rule takes the owner's password where one is set (as the Providers
    tab does), a typed reason, and `confirm`: the number of items the person was shown (GET /api/hide/preview), which
    must be what it lets go now (409 changed when not). remove: true (JSON true) takes the rule away and reopens what it
    hid (what another rule still covers stays hidden). A rule for any reason needs a code or a reason code: a whole payer
    is never hidden."""
    from .actions import HIDE_GRAIN, NEVER_HIDDEN, hide_rule_of
    from .archive_rules import hide_sql
    pg = str(body.get("payer_group") or "").strip()
    sig = str(body.get("signal") or "").strip() or "*"
    raw = {k: str(body.get(k) or "").strip() or "*" for k in ("code", "modifier", "reason")}
    if not pg or len(pg) > 200 or not (sig == "*" or re.fullmatch(r"[A-Z0-9_]{2,60}", sig)):
        raise HTTPException(400, "payer_group and signal are required")
    if not _hide_grain_ok(raw):
        raise HTTPException(400, "code, modifier and reason are a code (or a comma list, or an @family), one modifier (or none), "
                                 "and a reason code (97 or CO-97); leave one out for any")
    if body.get("remove") is not None and body.get("remove") is not True:
        raise HTTPException(400, "remove must be true or left out.")
    rule = hide_rule_of(dict(raw, payer_group=pg, signal=sig))
    entry = {"kind": "hide_rule", "payer_group": pg, "signal": rule["signal"], "code": rule["code"], "modifier": rule["modifier"],
             "reason": rule["reason"], "actor": user}
    if body.get("remove") is True:
        # Show again takes away the rule the Hidden list shows, at whatever grain it was made (a rule for any reason by a code
        # included). A table still at the two-part key (a copy saved by an earlier release, until its first run here) holds
        # payer x reason rules only: the grain it lacks is read as any ('*')
        have = {r["name"] for r in rows("PRAGMA table_info(hide_rules)")} if has_table("hide_rules") else set()
        if have and not any(k in body for k in HIDE_GRAIN) and set(HIDE_GRAIN) <= have:
            # a page loaded before Show again sent the rule's grain names the payer and the reason only: the one rule in
            # force for them, whatever its grain (never a guess between two)
            mine = rows(f"SELECT code, modifier, reason FROM hide_rules WHERE payer_group=? AND signal=? AND {_rules_in_force()}",
                        (pg, rule["signal"]))
            if len(mine) > 1:
                raise HTTPException(409, "This payer has more than one rule for that reason: open the Hidden list again and pick one.")
            if mine:
                rule = hide_rule_of(dict(mine[0], payer_group=pg, signal=rule["signal"]))
                entry.update(code=rule["code"], modifier=rule["modifier"], reason=rule["reason"])
        conds, params = ["payer_group=?", "signal=?"], [pg, rule["signal"]]
        for k in HIDE_GRAIN:
            if k in have:
                conds.append(f"COALESCE({k},'*')=?")
                params.append(rule[k])
            elif rule[k] != "*":
                raise HTTPException(404, "No such rule.")
        if not (have and one(f"SELECT 1 FROM hide_rules WHERE {' AND '.join(conds)} AND {_rules_in_force()}", tuple(params))):
            raise HTTPException(404, "No such rule.")
        _record_action(dict(entry, op="remove"))
        return {"ok": True}
    if rule["signal"] == "*" and rule["code"] == "*" and rule["reason"] == "*":
        raise HTTPException(400, "Name a reason, a code or the payer's reason code: a whole payer is never hidden.")
    if sig in NEVER_HIDDEN:
        raise HTTPException(400, "Claims the payer never answered are not hidden for a payer: each one is money nobody has "
                                 "asked for yet. Let them go one at a time, with a note.")
    cond, params = hide_sql(rule, alias="f")
    if not one(f"SELECT 1 FROM findings f WHERE {cond} LIMIT 1", tuple(params)):
        raise HTTPException(404, "Nothing of that kind for that payer.")
    # a policy decision for a payer: the owner's, as the Providers tab asks for it (wrong tries counted the same way)
    st = owner_state(request)
    if st == "unusable":
        raise HTTPException(503, "Hiding is switched off: the owner's password cannot be used as it is set. The setup page says why.")
    if st == "blocked":
        raise HTTPException(429, "Too many wrong tries at the owner's password from this address. Wait ten minutes.")
    if st != "open":
        raise HTTPException(403, "That is not the owner's password." if st == "wrong" else "Hiding a reason for a payer needs the owner's password.")
    note = body.get("note")
    if not isinstance(note, str) or not note.strip():
        raise HTTPException(400, "Type why this payer does not pay these (shown on each item).")
    if len(note) > 300:
        raise HTTPException(400, "Keep the reason to 300 characters.")
    confirm = _whole_cents(body.get("confirm"))
    if confirm is None:
        raise HTTPException(400, "Type the number of items it lets go, to confirm.")
    skip: list[str] = []                    # items with a fax going out now: left out of the hide, and named in its entry
    size = _hide_size(pg, rule["signal"], skip, rule)
    if confirm != size["n"]:
        raise Refused(409, "changed", f"The count changed: it lets go {size['n']} items now, not {confirm}. Look at the numbers again.", **size)
    count = f"SELECT COUNT(*) n FROM findings f WHERE {cond} AND f.status='open'"
    before = (one(count, tuple(params)) or {}).get("n") or 0
    _record_action(dict(entry, op="add", actor=_owner_actor(user), note=note.strip(), **({"skip": skip} if skip else {})))
    after = (one(count, tuple(params)) or {}).get("n") or 0
    return {"ok": True, "hidden": before - after, **({"left_out": len(skip)} if skip else {})}


# --------------------------------------------------------------------------- suggested templates (remitwatch.lessons)
@app.get("/api/lessons")
def lessons_list(request: Request, _: str = Depends(require_auth)):
    """What the note reviews found missing, by code, for the suggested templates. Which clinicians' notes fell short is
    shown only where the Providers tab would show it."""
    from . import lessons
    named = owner_state(request) == "open"
    conn = db()
    try:
        out = lessons.summarize(conn, with_providers=named)
    finally:
        conn.close()
    return dict(out, providers_locked=not named, review=bool(os.environ.get("ANTHROPIC_API_KEY")))


@app.post("/api/lessons/template", dependencies=[Depends(require_xhr)])
def lessons_template(body: dict = Body(default={}), _: str = Depends(require_auth)):
    """Claude's suggested EMA template for one code's lessons: the fields to require, each one's prompt, and the line
    the clinician signs. Only the code, the payers and the gaps go to the model."""
    from . import lessons
    key = str(body.get("key") or "")
    conn = db()
    try:
        groups = lessons.summarize(conn, with_providers=False)["codes"]
    finally:
        conn.close()
    g = next((x for x in groups if x["key"] == key), None)
    if not g:
        raise HTTPException(404, "No lessons for that code yet.")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(503, "ANTHROPIC_API_KEY is not set, so Claude cannot write the template. The elements above are the checklist.")
    try:
        return {"key": key, "template": lessons.write_template(g)}
    except Exception as exc:
        raise HTTPException(502, f"Claude could not write it just now ({type(exc).__name__}). Try again.")


def _cloud_state():
    from . import cloud
    st = cloud.SERVING_COPY.state()
    if st is None:
        raise HTTPException(503, "Storage is not connected yet. Open /setup.")
    return cloud, st


@app.get("/api/packets")
def packets(finding_id: Optional[str] = None, _: str = Depends(require_auth)):
    if runtime.serverless():
        return _packets_for(finding_id) if finding_id else []
    from .notes import list_packets
    conn = db()
    try:
        return [_shown_note_name(p) for p in list_packets(conn, finding_id)]
    finally:
        conn.close()


@app.get("/api/packets/{packet_id}")
def packet_get(packet_id: str, _: str = Depends(require_auth)):
    if runtime.serverless():
        cloud, st = _cloud_state()
        p = cloud.get_packet(st, packet_id, reveal=SHOW_PHI)
    else:
        from .notes import get_packet
        conn = db()
        try:
            p = get_packet(conn, packet_id, reveal=SHOW_PHI)
        finally:
            conn.close()
    if not p:
        raise HTTPException(404, "packet not found")
    return _shown_note_name(p)


@app.get("/api/packets/{packet_id}/pdf")
def packet_pdf(packet_id: str, _: str = Depends(require_auth)):
    if runtime.serverless():
        cloud, st = _cloud_state()
        p = cloud.get_packet(st, packet_id, with_pdf=True)
    else:
        from .notes import get_packet
        conn = db()
        try:
            p = get_packet(conn, packet_id, with_pdf=True)
        finally:
            conn.close()
    if not p or not p.get("pdf"):
        raise HTTPException(404, "packet not found")
    safe = "".join(ch for ch in packet_id if ch.isalnum() or ch in ".-_")[:80]
    pdf = p["pdf"]
    return StreamingResponse((pdf[i:i + 262144] for i in range(0, len(pdf), 262144)), media_type="application/pdf",
                             headers={"Content-Disposition": f'attachment; filename="appeal-packet-{safe}.pdf"', "Cache-Control": "no-store"})


@app.post("/api/packets/{packet_id}/email", dependencies=[Depends(require_xhr)])
def packet_email(packet_id: str, body: dict = Body(default={}), _: str = Depends(require_auth)):
    to = str(body.get("to") or os.environ.get("APPEAL_EMAIL_TO") or "").strip()
    note = str(body.get("note") or "")
    if runtime.serverless():
        from .notes import send_pdf
        cloud, st = _cloud_state()
        p = cloud.get_packet(st, packet_id, with_pdf=True)
        out = send_pdf((p or {}).get("pdf") or b"", packet_id, to, note)
        if out.get("sent"):
            cloud.mark_packet_emailed(st, packet_id, to)
        return out
    # a packet whose uploaded note was taken back, or matched to another visit since, may carry another patient's note:
    # it is not faxed any more (_packet_problem), and it is not emailed either
    why = _packet_problem(packet_id, {})
    if why and why.startswith("The note it was built from"):
        raise HTTPException(409, f"{why} It is not emailed: build the packet again in the item.")
    from .notes import email_packet
    conn = db()
    try:
        return email_packet(conn, packet_id, to, note)
    finally:
        conn.close()


from . import api_uploads  # noqa: E402  (unit A2: large files in pieces and the EMA export checklist, remitwatch/api_uploads.py)
app.include_router(api_uploads.router)

from . import api_ema  # noqa: E402  (unit C2: Today's EMA figures, the Data tab, the owner's write-off policy table)
app.include_router(api_ema.router)
from . import api_results  # noqa: E402  (unit R: the Results tab, the scoreboard Today shows, the daily spot checks)
app.include_router(api_results.router)
