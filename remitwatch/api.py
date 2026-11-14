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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

import asyncio
from contextvars import ContextVar

from . import __version__, runtime
from .actions import STATUSES, apply_alert_ack, apply_status
from .codes import CARC, RARC, member_number
from .detect import DOWNCODE_SIGNALS, EM_SQL, ensure_schema, pattern_windows

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



@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Bring an older store up to the current schema and start the background scheduler when it is configured."""
    if runtime.serverless():
        yield           # nothing on disk to migrate and no process to keep: remitwatch.cloud does both jobs
        return
    if os.path.exists(DB_PATH):
        conn = sqlite3.connect(DB_PATH, timeout=60)
        try:
            ensure_schema(conn)
            from .alerts import ALERTS_DDL
            from .notes import ensure_packets
            conn.executescript(ALERTS_DDL)
            ensure_packets(conn)
            conn.commit()
        finally:
            conn.close()
        from .pipeline import SCHEDULER, start_scheduler_from_env
        start_scheduler_from_env(DB_PATH)
        yield
        SCHEDULER.stop()
    else:
        yield


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


def require_xhr(request: Request) -> None:
    """State-changing calls must carry a header a cross-site form cannot set."""
    if request.headers.get("x-requested-with") != "remitwatch":
        raise HTTPException(status_code=400, detail="missing X-Requested-With header")


def open_allowed() -> bool:
    """With no password the dashboard is open. That is acceptable on a developer's own machine and nowhere else, so a
    deployed site refuses to serve data until REMITWATCH_PASSWORD is set."""
    return not runtime.serverless() or os.environ.get("REMITWATCH_ALLOW_OPEN") == "1"


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
    stands between the internet and the data, so a short one counts as none: the site stays locked and says why."""
    if not PASSWORD:
        return "not set"
    if open_allowed():
        return None             # a developer's own machine
    if not PASSWORD.isascii():
        return "has a character a browser cannot send in a login (use plain letters and digits)"
    if len(PASSWORD) < MIN_PASSWORD:
        return f"shorter than {MIN_PASSWORD} characters"
    return None


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
    """Basic auth with a single shared password. When no password is configured the
    dashboard is open (local use only)."""
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
    if runtime.serverless():
        return {"ok": True, "mode": "serverless", "version": __version__, "commit": (os.environ.get("VERCEL_GIT_COMMIT_SHA") or "")[:7],
                "runtime": runtime.describe(), "selftest": runtime.selftest(), "time": datetime.now(timezone.utc).isoformat()}
    return {"ok": os.path.exists(DB_PATH), "mode": "server", "version": __version__, "time": datetime.now(timezone.utc).isoformat()}


@app.get("/api/meta")
def meta(_: str = Depends(require_auth)):
    out: dict[str, Any] = {"version": __version__, "show_phi": SHOW_PHI, "mode": "serverless" if runtime.serverless() else "server",
                           # whether what each clinician is doing asks for the owner's password first
                           "providers": {"locked": bool(OWNER_PASSWORD)}}
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
    out["deadlines"] = rows("SELECT CASE WHEN appeal_deadline < date('now') THEN 'expired' WHEN appeal_deadline < date('now','+30 days') THEN 'due_30d' WHEN appeal_deadline < date('now','+90 days') THEN 'due_90d' ELSE 'later' END bucket, COUNT(*) n, SUM(shortfall_cents) shortfall_cents FROM findings WHERE status='open' AND appeal_deadline IS NOT NULL AND actionable IN ('appeal','correct_and_resubmit') GROUP BY 1")
    # a routine-rate finding counts by its claims' own deadlines, each finding once per bucket it touches
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
                "code_category, charge_cents, allowed_cents, paid_cents, expected_cents, shortfall_cents, carc, carc_text, rarc, rarc_text, appeal_deadline, ema_handling, status, notes, first_seen, last_seen, "
                "billed_code, code_pair, level_drop, plan, pos_class, month, modifier_class, location, diagnosis, deadline_basis, recovered_cents, recovered_date, appealed_at, stale, auto_note, hint")


def _day(offset: int) -> str:
    """Today plus `offset` days, as the SQL date('now') sees it (UTC)."""
    return (datetime.now(timezone.utc).date() + timedelta(days=offset)).isoformat()


DEADLINE_RANGES = {"valid": None, "week": 7, "month": 30, "missed": -30}   # filters that count a routine-rate finding's claims by their own dates


def _range(deadline: str) -> tuple[str, str | None]:
    days = DEADLINE_RANGES[deadline]
    if days is not None and days < 0:
        return _day(days), _day(0)              # windows that closed in the last N days
    return _day(0), (_day(days) if days else None)


def _pattern_cents(w: str, params: list, deadline: str | None) -> tuple[int, dict]:
    """The routine-rate findings a filter holds, by their claims: how much their claims outside the date range take off
    the total, and for each the cents and the first deadline of its claims in range (in window, without a range)."""
    found = rows(f"SELECT finding_id, COALESCE(shortfall_cents,0) c FROM findings{w}{' AND' if w else ' WHERE'} signal='SECOND_RATE'", tuple(params))
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
    return ", ".join(c if c in have else f"NULL AS {c}" for c in (x.strip() for x in FINDING_COLS.split(",")))


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


def _filters(signal, category, payer, year, status, severity, actionable, q, provider, code, deadline, handling=None, extra: dict | None = None,
             patient_of: str | None = None, kind: str | None = None, live: bool = False, paid_days: int = 0, min_dollars: int = 0):
    where, params = [], []
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
        where.append("(appeal_deadline >= date('now') AND appeal_deadline < date('now','+45 days')"
                     + (f" OR finding_id IN ({','.join('?' * len(ids))})" if ids else "") + ")")
        params.extend(ids)
    elif deadline == "expired":
        where.append("appeal_deadline < date('now')")
    elif deadline in DEADLINE_RANGES:
        # a routine-rate finding is in range when one of its claims is, by that claim's own date
        lo, hi = _range(deadline)
        ids = sorted({fid for fid, _g, _c, d, _n, _x in _windows(STATUSES) if d and d >= lo and (hi is None or d < hi)})
        days = DEADLINE_RANGES[deadline]
        dated = ("appeal_deadline >= date('now')" + ("" if hi is None else f" AND appeal_deadline < date('now','+{days} days')")) if not (days and days < 0) \
            else f"appeal_deadline >= date('now','{days} days') AND appeal_deadline < date('now')"
        where.append(f"((COALESCE(signal,'') != 'SECOND_RATE' AND {dated})" + (f" OR finding_id IN ({','.join('?' * len(ids))})" if ids else "") + ")")
        params.extend(ids)
    elif deadline == "none":
        where.append("appeal_deadline IS NULL")
    elif deadline == "followup":
        # the home screen's follow-ups: sent more than 30 days before the newest payment and still not paid
        where.append("status='appealed' AND COALESCE(appealed_at, status_changed_at) < "
                     "(SELECT date(MAX(payment_date),'-30 days') FROM transactions WHERE COALESCE(superseded,0)=0)")
    elif deadline == "older":
        # Today's "older ones": no filing date, and not among the items to decide
        where.append("appeal_deadline IS NULL AND NOT (COALESCE(actionable,'')='review' AND COALESCE(payment_date,'') >= "
                     "(SELECT date(MAX(payment_date),'-120 days') FROM transactions WHERE COALESCE(superseded,0)=0))")
    elif deadline == "decide":
        # the home screen's "decide": no filing date until a person says what the item is, paid in the 120 days to the newest payment
        where.append("appeal_deadline IS NULL AND actionable='review' AND payment_date >= "
                     "(SELECT date(MAX(payment_date),'-120 days') FROM transactions WHERE COALESCE(superseded,0)=0)")
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
            number = re.sub(r"[\s\-]", "", q)
            if re.search(r"\d", number):
                # or part of a member number, typed with or without the spaces and dashes a card prints, whichever way
                # the payer wrote it
                bare = "REPLACE(REPLACE(c.{k}_id,'-',''),' ','')"
                who += (f" OR ({bare.format(k='patient')} LIKE ? AND {_MEMBER_SQL.format(a='c', k='patient')})"
                        f" OR ({bare.format(k='insured')} LIKE ? AND {_MEMBER_SQL.format(a='c', k='insured')})")
                who_params += [f"%{number}%"] * 2
            clause += f" OR claim_key IN (SELECT c.claim_key FROM claims c WHERE {who})"
            params += who_params
        where.append(f"({clause})")
    if handling:
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
    for r in items:
        c = who.get(r.get("claim_key"))
        if SHOW_PHI:
            r["patient"] = _name(c["patient_last"], c["patient_first"], c["patient_middle"]) if c else ""
            r["member_id"] = _member(c) if c else ""
        else:
            r["patient"] = _initials(c["patient_last"], c["patient_first"]) if c else ""
    return items


@app.get("/api/findings")
def findings(signal: Optional[str] = None, category: Optional[str] = None, payer: Optional[str] = None, year: Optional[str] = None,
             status: str = "open", severity: Optional[str] = None, actionable: Optional[str] = None, q: Optional[str] = None,
             provider: Optional[str] = None, code: Optional[str] = None, deadline: Optional[str] = None, handling: Optional[str] = None,
             patient_of: Optional[str] = Query(None, max_length=200), sort: str = "shortfall", limit: int = Query(100, ge=1, le=2000),
             offset: int = Query(0, ge=0, le=10**9), kind: Optional[str] = Query(None, max_length=20), live: int = Query(0, ge=0, le=1),
             paid: int = Query(0, ge=0, le=3650), min: int = Query(0, ge=0, le=10**6), request: Request = None, _: str = Depends(require_auth)):
    w, params = _filters(signal, category, payer, year, status, severity, actionable, _search_text(request, q), provider, code, deadline, handling,
                         _extra(request), patient_of, kind, bool(live), paid, min)
    delta, each = _pattern_cents(w, params, deadline)
    due, order_params = "appeal_deadline", []
    if each and sort in ("deadline", "payer"):
        # a routine-rate finding sorts by the first of its claims still in range, not by the last
        nxt = [(fid, d) for fid, (_c, d, _n) in each.items() if d]
        if nxt:
            due = "COALESCE(CASE finding_id " + " ".join("WHEN ? THEN ?" for _ in nxt) + " END, appeal_deadline)"
            order_params = [x for pair in nxt for x in pair] * 3
    by_due = f"({due}) IS NULL, ({due}) < date('now'), {due} ASC"
    order = {"shortfall": "shortfall_cents DESC", "date": "service_date DESC", "deadline": f"{by_due}, shortfall_cents DESC",
             "payer": f"payer_group, {by_due}, shortfall_cents DESC", "paid": "payment_date DESC, shortfall_cents DESC",
             "sent": "COALESCE(appealed_at, status_changed_at) ASC, shortfall_cents DESC"}.get(sort, "shortfall_cents DESC")
    if sort not in ("deadline", "payer"):
        order_params = []
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
                 view: str = Query("raw", pattern="^(raw|worklist)$"), request: Request = None, _: str = Depends(require_auth)):
    w, params = _filters(signal, category, payer, year, status, severity, actionable, _search_text(request, q), provider, code, deadline, handling,
                         _extra(request), patient_of, kind, bool(live), paid, min)
    data = rows(f"SELECT {_finding_cols()} FROM findings{w} ORDER BY payer_group, shortfall_cents DESC", tuple(params))
    if view == "worklist":
        return _worklist_csv(data, deadline, w, params)
    # a routine-rate finding is about many claims: list them, each with its own filing deadline
    pattern = {r["finding_id"]: r["evidence"] for r in rows("SELECT finding_id, evidence FROM findings WHERE signal='SECOND_RATE'")} \
        if any(r.get("signal") == "SECOND_RATE" for r in data) else {}
    for r in data:
        ev = json.loads(pattern[r["finding_id"]]) if pattern.get(r.get("finding_id")) else {}
        r["claims"] = "; ".join(f"{c[0]} ({c[1]}, file by {c[3] if len(c) > 3 and c[3] else 'unknown'})" for c in (ev.get("claims") or [])) or None
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


def _packets_for(finding_id: str) -> list[dict]:
    import re
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", finding_id or ""):
        return []           # it becomes part of a storage path
    if runtime.serverless():
        from . import cloud
        st = cloud.SERVING_COPY.state(fast=True)        # part of opening a finding: must not wait on a slow Dropbox
        try:
            return cloud.list_packets(st, finding_id) if st is not None else []
        except Exception:
            return []
    return rows("SELECT packet_id, created_at, mode, subject, note_name, emailed_to, emailed_at FROM appeal_packets WHERE finding_id=? ORDER BY created_at DESC",
                (finding_id,)) if has_table("appeal_packets") else []


CLAIM_PUBLIC_COLS = ("claim_key", "transaction_key", "patient_control_number", "claim_status_code", "charge_cents", "paid_cents",
                     "patient_responsibility_cents", "claim_filing_indicator", "payer_claim_control_number", "facility_type_code",
                     "claim_frequency_code", "rendering_provider_name", "rendering_provider_npi", "contract_class", "received_date",
                     "statement_from", "statement_to", "payer_name", "payer_id", "payee_npi", "payment_date", "trace_number", "line_count", "balanced")


def _initials(last: str | None, first: str | None) -> str:
    return "".join(x[0] for x in (first or "", last or "") if x).upper() or "?"


@app.get("/api/findings/{finding_id}")
def finding_detail(finding_id: str, _: str = Depends(require_auth)):
    f = one("SELECT * FROM findings WHERE finding_id=?", (finding_id,))
    if not f:
        raise HTTPException(404, "finding not found")
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
    from .detect import bill_of
    bill = bill_of(f["patient_control_number"]) if f.get("patient_control_number") else (_bill_of_reference((f["evidence"] or {}).get("reference")) if f.get("signal") == "TAKEBACK" else None)
    if bill and _bill_ok(bill):
        out["bill"] = bill                  # the whole claim opens from the drawer: "See the whole claim"
    if f.get("transaction_key"):        # the remittance it came from (a takeback or a payment fee has one and no claim)
        out["remit"] = one("SELECT transaction_key, source_name, payer_name, payer_id, payer_contact_phone, payer_contact_url, payment_method, payment_amount_cents, payment_date, trace_number, production_date, payee_npi FROM transactions WHERE transaction_key=?", (f["transaction_key"],))
    # the record of what people did, for every finding (a payer's routine rate belongs to no one claim)
    out["events"] = rows("SELECT at, kind, from_status, to_status, amount_cents, actor, note FROM finding_events WHERE finding_id=? ORDER BY at DESC, id DESC LIMIT 30", (finding_id,)) if has_table("finding_events") else []
    out["packets"] = _packets_for(finding_id)
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


@app.post("/api/findings/{finding_id}/status", dependencies=[Depends(require_xhr)])
async def set_status(finding_id: str, request: Request, user: str = Depends(require_auth)):
    body = await request.json()
    status = body.get("status")
    if status not in STATUSES:
        raise HTTPException(400, "bad status")
    rec = body.get("recovered_cents")
    n = await asyncio.to_thread(_record_status, [finding_id], status, body.get("notes"), int(rec) if rec not in (None, "") else None, user)
    if not n:
        raise HTTPException(404, "finding not found")
    return {"ok": True}


@app.post("/api/findings/bulk-status", dependencies=[Depends(require_xhr)])
async def bulk_status(request: Request, user: str = Depends(require_auth)):
    body = await request.json()
    ids = body.get("ids") or []
    status = body.get("status")
    if status not in STATUSES or not ids or len(ids) > 5000:
        raise HTTPException(400, "bad request")
    n = await asyncio.to_thread(_record_status, list(ids), status, body.get("notes"), None, user)
    return {"ok": True, "updated": n}


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
        for f in rows("""SELECT finding_id, signal, category, status, shortfall_cents, appeal_deadline, procedure_code, stale, evidence
                         FROM findings WHERE signal='SECOND_RATE' AND COALESCE(stale,0)=0"""):
            ev = json.loads(f.pop("evidence") or "{}")
            mine = set(ev.get("line_keys") or []) & line_keys
            if not mine:
                continue
            per = max((ev.get("usual_cents") or 0) - (ev.get("amount_cents") or 0), 0)
            own = [c for c in (ev.get("claims") or []) if isinstance(c, list) and c and str(c[0]).upper() in {p.upper() for p in pcns}]
            patterns.append(dict(f, via="pattern", line_keys=sorted(mine), claim_cents=per * len(mine),
                                 claim_deadline=(own[0][3] if own and len(own[0]) > 3 else None)))
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
    found = rows("SELECT patient_control_number pcn FROM claims WHERE patient_control_number >= ? AND patient_control_number < ? LIMIT 500",
                 (up, up + "\uffff"))
    found += [{"pcn": b} for b in (bill_of(up), up)]                      # any submission of a bill, answered or not
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
        found += rows("SELECT DISTINCT claim_id pcn FROM ema_rows WHERE claim_id >= ? AND claim_id < ? LIMIT 200", (up, up + "\uffff"))
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
        if pcns:
            q2 = ",".join("?" * len(pcns))
            open_ids = {r["finding_id"] for r in rows(f"""SELECT finding_id FROM findings WHERE patient_control_number IN ({q2})
                                                           AND status IN ('open','in_progress') AND COALESCE(stale,0)=0""", tuple(pcns))}
            keys = [x["claim_key"] for x in rem]
            lks = {r["line_key"] for r in rows(f"SELECT line_key FROM lines WHERE claim_key IN ({','.join('?' * len(keys))})", tuple(keys))} if keys else set()
            _plb, tbs, pats = _bill_extras(pcns, {x["payer_claim_control_number"] for x in rem if x.get("payer_claim_control_number")}, lks)
            open_ids |= {x["finding_id"] for x in tbs + pats if x["status"] in ("open", "in_progress")}
        who = _who(rem[-1]) if rem else {"patient": ""}
        items.append(dict(who, bill=b, claim_ids=sorted(set(pcns) | set(ema_ids)), payers=sorted({x["payer_name"] for x in rem if x["payer_name"]}), remits=len(rem),
                          first_payment=rem[0]["payment_date"] if rem else None, last_payment=rem[-1]["payment_date"] if rem else None,
                          charge_cents=max(((x["charge_cents"] or 0) for x in rem), default=0), paid_cents=sum((x["paid_cents"] or 0) for x in rem),
                          open_findings=len(open_ids), exact=(b == up)))
    items.sort(key=lambda x: (not x["exact"], -(int((x["last_payment"] or "0000-00-00").replace("-", "")))))
    return {"items": items, "more": len(bills) > len(items) and len(items) >= 25}


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
    if pcns:
        qp = ",".join("?" * len(pcns))
        findings = [dict(f, via="line") for f in rows(f"""SELECT f.finding_id, f.signal, f.category, f.status, f.shortfall_cents, f.appeal_deadline, f.procedure_code,
                                                                f.claim_key, f.line_key, f.stale, c.payment_date remit_date, c.payer_name remit_payer
                                                         FROM findings f LEFT JOIN claims c ON c.claim_key = f.claim_key
                                                         WHERE f.patient_control_number IN ({qp}) ORDER BY f.payment_date, f.finding_id""", tuple(pcns))]
    plb, takebacks, patterns = _bill_extras(pcns, {r["payer_claim_control_number"] for r in rem if r.get("payer_claim_control_number")},
                                            {l["line_key"] for l in lines})
    ids = sorted(set(pcns) | set(ema_ids))
    qe = ",".join("?" * len(ids))
    ema = rows(f"""SELECT claim_id, family, source_name, service_date, cpt, modifiers, units, charges_cents, payments_cents, adjustments_cents, allowed_cents,
                          claim_status, activity_type, carc, rarc, adjustment_status, denial_category, posted_date, claim_submission_date, transaction_date
                   FROM ema_rows WHERE claim_id IN ({qe}) ORDER BY claim_id, family, service_date, row_index""", tuple(ids)) if has_table("ema_rows") and ids else []
    who = _who(rem[-1]) if rem else {"patient": ""}
    return dict(who, bill=bill, claim_ids=ids, answered=pcns, remits=remits, findings=findings + takebacks + patterns, taken_back=plb, ema=ema)


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
    newest payment's month, everything since the first fee, the months with a fee, the vendors named, and the ids of
    its fee items (one per payer and month), so asking can mark them all at once."""
    from .insights import fee_months, newest_payment
    conn = db()
    try:
        newest = newest_payment(conn)
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
                             COALESCE(appealed_at, status_changed_at) asked FROM findings WHERE {where} ORDER BY month""", params):
        p = by.setdefault(r["pn"], {"payer_name": r["pn"], "payer_group": r["payer_group"], "cents": 0, "cents12": 0, "months": set(),
                                    "ids": [], "evidence": [], "asked": None})
        p["cents"] += r["c"]
        p["ids"].append(r["finding_id"])
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
                           SUM(status='lost') lost_n, SUM(status IN ('open','in_progress') AND appeal_deadline >= date('now') AND signal != 'SECOND_RATE') live_n,
                           COALESCE(SUM(CASE WHEN status IN ('open','in_progress') AND appeal_deadline >= date('now') AND signal != 'SECOND_RATE' THEN shortfall_cents END),0) live_cents
                    FROM findings WHERE {' AND '.join(fwhere)} GROUP BY {grp} ORDER BY cents DESC LIMIT ?""", tuple(fparams) + (limit,))
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
        for pr in rows(f"SELECT finding_id, {fcols} FROM findings WHERE {' AND '.join(fwhere)} AND signal='SECOND_RATE' AND status IN ('open','in_progress')",
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
        # the red number on each category tab: what closes within 7 days, counted as that page's first tile and its list
        token = _WINDOWS_MEMO.set({})
        try:
            out["badges"] = {c: _bucket(c, None, TODO_STATUSES, "week") for c in BADGE_CATEGORIES}
        finally:
            _WINDOWS_MEMO.reset(token)
    return out


@app.get("/api/worklist/counts")
def worklist_counts(category: Optional[str] = None, payer: Optional[str] = None, status: str = "open", deadline: Optional[str] = None,
                    q: Optional[str] = None, signal: Optional[str] = None, live: int = Query(0, ge=0, le=1), paid: int = Query(0, ge=0, le=3650),
                    min: int = Query(0, ge=0, le=10**6), kind: Optional[str] = Query(None, max_length=20), year: Optional[str] = Query(None, max_length=4),
                    request: Request = None, _: str = Depends(require_auth)):
    """Items and dollars for each kind of work under the worklist's other filters, so each tab counts what a click on it
    opens, and for each reason (signal), so a reason chip counts what it opens too. Counts only: no patient, claim or date."""
    from .insights import WORK_TYPES, work_type
    w, params = _filters(signal, category, payer, year, status, None, None, _search_text(request, q), None, None, deadline, None, None,
                         None, kind, bool(live), paid, min)
    out = {t["key"]: {"key": t["key"], "n": 0, "cents": 0} for t in WORK_TYPES}
    by_signal: dict[str, dict] = {}
    for r in rows(f"SELECT signal, actionable, carc, COUNT(*) n, COALESCE(SUM(shortfall_cents),0) cents FROM findings{w} GROUP BY 1,2,3", tuple(params)):
        k = out[work_type(r["signal"], r["actionable"], r["carc"])]
        k["n"] += r["n"]
        k["cents"] += r["cents"]
        sg = by_signal.setdefault(r["signal"] or "", {"signal": r["signal"], "n": 0, "cents": 0})
        sg["n"] += r["n"]
        sg["cents"] += r["cents"]
    delta, _each = _pattern_cents(w, params, deadline)
    out[work_type("SECOND_RATE", "appeal")]["cents"] += delta
    if "SECOND_RATE" in by_signal:
        by_signal["SECOND_RATE"]["cents"] += delta
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


def _bucket(category, payer, status, deadline, min_dollars=0, signal=None, kind=None, year=None) -> dict:
    """Items and dollars under exactly the filter /api/findings uses for the same choices (live findings only): the
    count line of the list a click opens. A routine-rate pattern counts its claims in range, as the list does."""
    w, params = _filters(signal, category, payer, year, status, None, None, None, None, None, deadline, None, None, None, kind, True, 0, min_dollars)
    r = one(f"SELECT COUNT(*) n, COALESCE(SUM(shortfall_cents),0) cents FROM findings{w}", tuple(params))
    delta, _each = _pattern_cents(w, params, deadline)
    return {"n": r["n"], "cents": (r["cents"] or 0) + (delta if r["n"] else 0)}


def _desk_kinds(category, payer, status, deadline, min_dollars=0, signal=None, paid_days=0, q=None, year=None) -> dict:
    """The kinds of work under one filter, largest dollars first, each with its three biggest payer piles; a pile's count
    and dollars are what the list shows for that kind and payer."""
    from .insights import WORK_TYPES, work_type
    w, params = _filters(signal, category, payer, year, status, None, None, q, None, None, deadline, None, None, None, None, True, paid_days, min_dollars)
    kinds = {t["key"]: {"key": t["key"], "n": 0, "cents": 0, "payers": {}} for t in WORK_TYPES}
    for r in rows(f"""SELECT signal, actionable, carc, payer_group pg, COUNT(*) n, COALESCE(SUM(shortfall_cents),0) cents FROM findings{w}
                      GROUP BY 1,2,3,4""", tuple(params)):
        k = kinds[work_type(r["signal"], r["actionable"], r["carc"])]
        k["n"] += r["n"]
        k["cents"] += r["cents"]
        p = k["payers"].setdefault(r["pg"], [0, 0])
        p[0] += r["n"]
        p[1] += r["cents"]
    delta, each = _pattern_cents(w, params, deadline)
    if each and deadline in DEADLINE_RANGES:
        # a routine-rate pattern counts the cents of its claims in range, in its kind and in its payer's pile
        full = {r["finding_id"]: (r["pg"], r["c"]) for r in rows(
            f"SELECT finding_id, payer_group pg, COALESCE(shortfall_cents,0) c FROM findings{w}{' AND' if w else ' WHERE'} signal='SECOND_RATE'", tuple(params))}
        rk = kinds[work_type("SECOND_RATE", "appeal")]
        for fid, (cents, _d, _n) in each.items():
            if fid in full:
                g, c = full[fid]
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
         year: Optional[str] = Query(None, max_length=4), request: Request = None, _: str = Depends(require_auth)):
    """A category page in one call: its tiles (closes in 7 and 30 days, window open, to decide, older with no date, window
    closed, every item to do, sent and waiting, follow-ups due, closed in the last 30 days), and the kinds of work under
    the chosen status, filing date, payment window and search with their payer piles. `signal` narrows the whole page;
    the chosen kind of work and reason narrow the tiles only (a tile click keeps them, and the kinds of work stay listed so
    another can be chosen). Every number is the count line of the list it opens. Counts only: no patient, claim or date."""
    token = _WINDOWS_MEMO.set({})
    try:
        out = {"category": category, "scope": _desk_kinds(category, payer, status, deadline or None, min, signal, paid, _search_text(request, q), year)}
        if tiles:
            out["tiles"] = {name: _bucket(category, payer, st, dl, min, reason or signal, kind, year) for name, st, dl in DESK_TILES}
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


ASSUMPTIONS = "calculator/assumptions.json"     # in the state folder (cloud) or the meta table (server)
ASSUMPTIONS_KEY = "calculator:assumptions"
MAX_ASSUMPTIONS_BYTES = 256 * 1024
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
        return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)

    def fig(v: Any, top: float = MAX_FIGURE) -> bool:
        return v is None or (number(v) and 0 <= v <= top)

    def plain(v: Any, depth: int) -> bool:
        if v is None or isinstance(v, (bool, int, float)):
            return v is None or isinstance(v, bool) or number(v)
        if isinstance(v, str):
            return len(v) <= 200
        if depth <= 0:
            return False
        if isinstance(v, list):
            return len(v) <= 100 and all(plain(w, depth - 1) for w in v)
        if isinstance(v, dict):
            return len(v) <= 500 and all(isinstance(k, str) and len(k) <= 120 and plain(w, depth - 1) for k, w in v.items())
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
        if not isinstance(name, str) or not name.strip() or len(name) > 100:
            return "each scenario needs a name of up to 100 characters"
        if not isinstance(sc.get("who"), str) or not _FIG_ID.fullmatch(sc["who"]):
            return "each scenario needs a clinician"
        if "saved_at" in sc and not (isinstance(sc["saved_at"], str) and len(sc["saved_at"]) <= 40):
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
    return {"assumptions": data, "version": version}


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
                                      COALESCE(SUM(CASE WHEN status IN ('open','in_progress') AND appeal_deadline >= date('now') THEN shortfall_cents END),0) live_cents,
                                      ROUND(AVG(CASE WHEN status='resolved' AND recovered_date IS NOT NULL THEN julianday(recovered_date) - julianday(payment_date) END)) avg_days
                               FROM findings WHERE {MONEY} AND status != 'dismissed' AND line_key IS NOT NULL
                               GROUP BY 1 HAVING n >= 25 ORDER BY cents DESC LIMIT 30""")
    out["downcode_trend"] = rows(f"SELECT month, payer_group, COUNT(*) n, COALESCE(SUM(shortfall_cents),0) cents FROM findings WHERE {FAMILY['downcoding'][0]} AND status != 'dismissed' AND COALESCE(stale,0)=0 AND month >= '2024-01' GROUP BY 1,2 ORDER BY 1")
    out["visit_lines"] = rows(f"SELECT month, payer_group, SUM(lines) lines FROM exposure WHERE code IN ({EM_LIST}) AND month >= '2024-01' GROUP BY 1,2") if has_table("exposure") else []
    out["family_trend"] = rows(f"""SELECT month, category, COUNT(*) n, COALESCE(SUM(shortfall_cents),0) cents, SUM(status='resolved') resolved_n
                                   FROM findings WHERE {MONEY} AND status != 'dismissed' AND line_key IS NOT NULL AND month >= '2024-01' GROUP BY 1,2 ORDER BY 1""")
    out["all_lines"] = rows("SELECT month, SUM(lines) lines FROM exposure WHERE month >= '2024-01' GROUP BY 1 ORDER BY 1") if has_table("exposure") else []
    out["snapshots"] = rows("SELECT at, status, SUM(n) n, SUM(cents) cents FROM progress_snapshots WHERE category IN ('downcoding','bundling','denial','underpayment') GROUP BY run_id, status ORDER BY at") if has_table("progress_snapshots") else []
    out["events"] = rows("""SELECT e.at, e.kind, e.from_status, e.to_status, e.amount_cents, e.actor, f.payer_group, f.signal, f.procedure_code, e.finding_id
                            FROM finding_events e LEFT JOIN findings f ON f.finding_id = e.finding_id ORDER BY e.at DESC, e.id DESC LIMIT 60""") if has_table("finding_events") else []
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
    behind = (datetime.now(timezone.utc).date() - datetime.fromisoformat(newest).date()).days if newest else None
    return {"mode": "serverless", "dropbox_configured": dropbox_configured(), "webhook_ready": bool(os.environ.get("DROPBOX_APP_SECRET")),
            "interval_minutes": 0, "scheduler_running": False, "next_run": None, "cron_ready": bool(os.environ.get("CRON_SECRET")),
            "running": bool(st.get("running")), "running_since": (st.get("running") or {}).get("since"), "last_cycle": lc or None,
            "stalled_since": (st.get("stalled") or {}).get("since"), "memory_mb": lc.get("memory_mb"),
            "last_sync": lc.get("finished"), "newest_payment_date": newest, "days_behind": behind, "has_data": st.get("has_data"),
            "last_file_ingested": files.get("last"), "last_remit_ingested": files.get("last835"), "storage_error": st.get("error"),
            "smtp_configured": bool(os.environ.get("SMTP_HOST") and os.environ.get("REPORT_TO")),
            "alert_webhook": bool(os.environ.get("ALERT_WEBHOOK_URL")), "note_review": bool(os.environ.get("ANTHROPIC_API_KEY"))}


@app.get("/api/sync/status")
def sync_status(_: str = Depends(require_auth)):
    if runtime.serverless():
        return _cloud_sync_status()
    from .pipeline import status
    return dict(status(DB_PATH), mode="server")


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
    elif client is not None and not out.get("skipped") and not out.get("more"):
        try:        # files that landed while this cycle ran
            if cloud.has_new_files(st, client):
                out["continued"] = cloud.trigger_cycle(base_url(request), "continue", delay=30, hop=hop + 1)
        except Exception:
            pass
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
        fix = ("Add REMITWATCH_PASSWORD in Vercel (Settings, Environment Variables), then redeploy." if problem == "not set" else
               f"Replace REMITWATCH_PASSWORD in Vercel with one of {MIN_PASSWORD} or more plain letters and digits (the box below offers one), then redeploy.")
        return JSONResponse({"locked": True, "ready": False, "checks": [
            {"key": "password", "label": "Dashboard password (REMITWATCH_PASSWORD)", "ok": False, "detail": problem,
             "fix": fix + " Everything else on this page appears after that."}]},
            headers={"Cache-Control": "no-store"})
    require_auth(request, credentials)
    from . import cloud
    return JSONResponse(cloud.setup_status(base_url(request)), headers={"Cache-Control": "no-store"})


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
                  _: str = Depends(require_auth)):
    """Review the uploaded or pasted visit note for one finding and build the appeal packet (letter, review, evidence, note) as a PDF.
    A plain function on purpose: the review takes about a minute and must not hold up every other request."""
    from .notes import build_from_note

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
    if runtime.serverless():
        # no database to keep the packet in: build it in memory and store it as two files in the state folder
        from . import cloud
        st = cloud.SERVING_COPY.state()
        if st is None:
            raise HTTPException(503, "Storage is not connected yet. Open /setup.")
        mem = sqlite3.connect(":memory:")
        try:
            packet = build_from_note(mem, detail, raw_claim, name, data, pasted_text=text or None, reveal=SHOW_PHI, use_ai=use_ai == "1")
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
        # the PDF goes to the payer and carries the real identifiers; the letter text in this response does only when names are switched on
        return build_from_note(conn, detail, raw_claim, name, data, pasted_text=text or None, reveal=SHOW_PHI, use_ai=use_ai == "1")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    finally:
        conn.close()


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
        return list_packets(conn, finding_id)
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
    return p


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
    from .notes import email_packet
    conn = db()
    try:
        return email_packet(conn, packet_id, to, note)
    finally:
        conn.close()
