"""
RemitWatch API and dashboard server.

    REMITWATCH_DB=data/remitwatch.sqlite REMITWATCH_PASSWORD=... uvicorn remitwatch.api:app

Every endpoint reads the SQLite store built by `remitwatch.cli`. Patient
names never leave the server: list endpoints return claim identifiers only,
and the detail endpoint returns the patient's initials unless
REMITWATCH_SHOW_PHI=1 is set.
"""
from __future__ import annotations

import csv
import io
import json
import os
import secrets
import sqlite3
from datetime import datetime, timezone
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

from . import __version__, runtime
from .actions import STATUSES, apply_alert_ack, apply_status
from .codes import CARC, RARC
from .detect import DOWNCODE_SIGNALS, EM_SQL, ensure_schema

DB_PATH = os.environ.get("REMITWATCH_DB", "data/remitwatch.sqlite")
PASSWORD = os.environ.get("REMITWATCH_PASSWORD", "")
USERNAME = os.environ.get("REMITWATCH_USER", "oda")
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


app = FastAPI(title="RemitWatch", version=__version__, docs_url=None, redoc_url=None, lifespan=lifespan)
# Vercel refuses answers over 4.5 MB; compressed, the largest list the dashboard asks for is a few hundred KB
app.add_middleware(GZipMiddleware, minimum_size=2000)


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


def require_auth(credentials: Optional[HTTPBasicCredentials] = Depends(security)) -> str:
    """Basic auth with a single shared password. When no password is configured the
    dashboard is open (local use only)."""
    if not PASSWORD:
        if open_allowed():
            return "anonymous"
        raise HTTPException(status_code=503, detail="This site is not set up yet: REMITWATCH_PASSWORD is missing. Open /setup.")
    if credentials is None or not (secrets.compare_digest(credentials.username, USERNAME)
                                   and secrets.compare_digest(credentials.password, PASSWORD)):
        raise HTTPException(status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": "Basic realm=RemitWatch"})
    return credentials.username


def db() -> sqlite3.Connection:
    if runtime.serverless():
        from .cloud import SERVING_COPY
        return SERVING_COPY.connection()        # the dashboard's copy, fetched from the state folder and kept fresh
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
def setup_page(credentials: Optional[HTTPBasicCredentials] = Depends(security)):
    """The setup checklist. Before a password exists it is open and says only what is missing; after that it needs the login."""
    if PASSWORD:
        require_auth(credentials)
    return FileResponse(STATIC_DIR / "setup.html", headers={"Cache-Control": "no-store"})


@app.get("/", include_in_schema=False)
def index(credentials: Optional[HTTPBasicCredentials] = Depends(security)):
    if not PASSWORD and not open_allowed():
        return RedirectResponse("/setup")
    require_auth(credentials)
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
                "runtime": runtime.describe(), "time": datetime.now(timezone.utc).isoformat()}
    return {"ok": os.path.exists(DB_PATH), "mode": "server", "version": __version__, "time": datetime.now(timezone.utc).isoformat()}


@app.get("/api/meta")
def meta(_: str = Depends(require_auth)):
    out: dict[str, Any] = {"version": __version__, "show_phi": SHOW_PHI, "mode": "serverless" if runtime.serverless() else "server"}
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
    out["by_signal"] = rows(f"SELECT signal, category, severity, actionable, COUNT(*) n, SUM(shortfall_cents) shortfall_cents, SUM(charge_cents) charge_cents FROM findings WHERE {w} GROUP BY signal, category, severity, actionable ORDER BY shortfall_cents DESC", tuple(params))
    out["by_payer"] = rows(f"SELECT payer_group, payer_kind, COUNT(*) n, SUM(shortfall_cents) shortfall_cents FROM findings WHERE {w} GROUP BY payer_group, payer_kind ORDER BY shortfall_cents DESC LIMIT 25", tuple(params))
    out["by_year"] = rows(f"SELECT year, category, COUNT(*) n, SUM(shortfall_cents) shortfall_cents FROM findings WHERE {w} GROUP BY year, category ORDER BY year", tuple(params))
    out["by_month"] = rows(f"SELECT substr(COALESCE(service_date, payment_date),1,7) month, category, COUNT(*) n, SUM(shortfall_cents) shortfall_cents FROM findings WHERE {w} AND COALESCE(service_date, payment_date) >= '2023-01' GROUP BY 1,2 ORDER BY 1", tuple(params))
    out["by_provider"] = rows(f"SELECT rendering_provider_npi npi, MAX(rendering_provider_name) name, MAX(provider_tier) tier, COUNT(*) n, SUM(shortfall_cents) shortfall_cents FROM findings WHERE {w} AND rendering_provider_npi IS NOT NULL GROUP BY rendering_provider_npi ORDER BY shortfall_cents DESC LIMIT 20", tuple(params))
    out["by_actionable"] = rows(f"SELECT actionable, COUNT(*) n, SUM(shortfall_cents) shortfall_cents FROM findings WHERE {w} GROUP BY actionable ORDER BY shortfall_cents DESC", tuple(params))
    out["by_handling"] = rows(f"SELECT COALESCE(ema_handling,'unknown') ema_handling, COUNT(*) n, SUM(shortfall_cents) shortfall_cents FROM findings WHERE {w} GROUP BY 1 ORDER BY shortfall_cents DESC", tuple(params))
    out["resolved_by_later_remit"] = one("SELECT COUNT(*) n, SUM(recovered_cents) shortfall_cents FROM findings WHERE status='resolved' AND auto_note IS NOT NULL")
    out["deadlines"] = rows("SELECT CASE WHEN appeal_deadline < date('now') THEN 'expired' WHEN appeal_deadline < date('now','+30 days') THEN 'due_30d' WHEN appeal_deadline < date('now','+90 days') THEN 'due_90d' ELSE 'later' END bucket, COUNT(*) n, SUM(shortfall_cents) shortfall_cents FROM findings WHERE status='open' AND appeal_deadline IS NOT NULL AND actionable IN ('appeal','correct_and_resubmit') GROUP BY 1")
    out["info"] = rows("SELECT signal, year, title, shortfall_cents, evidence FROM findings WHERE category='info' ORDER BY signal, year")
    out["years"] = [r["year"] for r in rows("SELECT DISTINCT year FROM findings WHERE year >= '2023' ORDER BY year")]
    return out


# --------------------------------------------------------------------------- findings
FINDING_COLS = ("finding_id, signal, category, severity, actionable, title, patient_control_number, claim_key, line_key, payer_name, payer_group, payer_kind, "
                "rendering_provider_name, rendering_provider_npi, provider_tier, service_date, payment_date, year, procedure_code, modifiers, original_procedure_code, "
                "code_category, charge_cents, allowed_cents, paid_cents, expected_cents, shortfall_cents, carc, carc_text, rarc, rarc_text, appeal_deadline, ema_handling, status, notes, first_seen, last_seen, "
                "billed_code, code_pair, level_drop, plan, pos_class, month, modifier_class, location, diagnosis, deadline_basis, recovered_cents, recovered_date, appealed_at, stale, auto_note")


EXTRA_FILTERS = {"plan": "COALESCE(plan,'')", "billed_code": "billed_code", "code_pair": "code_pair", "month": "month", "modifier": "modifier_class",
                 "pos": "pos_class", "location": "location", "diagnosis": "diagnosis", "carc": "carc", "tier": "provider_tier",
                 "payer_kind": "payer_kind", "code_category": "code_category", "level_drop": "level_drop", "quarter": None}


def _extra(request: Request) -> dict:
    return {k: request.query_params[k] for k in EXTRA_FILTERS if k in request.query_params}


def _filters(signal, category, payer, year, status, severity, actionable, q, provider, code, deadline, handling=None, extra: dict | None = None):
    where, params = [], []
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
        where.append("status=?")
        params.append(status)
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
        where.append("appeal_deadline >= date('now') AND appeal_deadline < date('now','+45 days')")
    elif deadline == "expired":
        where.append("appeal_deadline < date('now')")
    elif deadline == "valid":
        where.append("appeal_deadline >= date('now')")
    if q:
        where.append("(patient_control_number LIKE ? OR title LIKE ? OR payer_name LIKE ? OR procedure_code LIKE ? OR carc LIKE ? OR rarc LIKE ?)")
        params += [f"%{q}%"] * 6
    if handling:
        where.append("ema_handling=?")
        params.append(handling)
    return (" WHERE " + " AND ".join(where)) if where else "", params


@app.get("/api/findings")
def findings(signal: Optional[str] = None, category: Optional[str] = None, payer: Optional[str] = None, year: Optional[str] = None,
             status: str = "open", severity: Optional[str] = None, actionable: Optional[str] = None, q: Optional[str] = None,
             provider: Optional[str] = None, code: Optional[str] = None, deadline: Optional[str] = None, handling: Optional[str] = None,
             sort: str = "shortfall", limit: int = Query(100, le=2000), offset: int = 0, request: Request = None, _: str = Depends(require_auth)):
    w, params = _filters(signal, category, payer, year, status, severity, actionable, q, provider, code, deadline, handling, _extra(request))
    order = {"shortfall": "shortfall_cents DESC", "date": "service_date DESC", "deadline": "appeal_deadline ASC", "payer": "payer_group, shortfall_cents DESC"}.get(sort, "shortfall_cents DESC")
    total = one(f"SELECT COUNT(*) n, SUM(shortfall_cents) shortfall_cents FROM findings{w}", tuple(params))
    data = rows(f"SELECT {FINDING_COLS} FROM findings{w} ORDER BY {order} NULLS LAST LIMIT ? OFFSET ?", tuple(params) + (limit, offset))
    return {"total": total["n"], "shortfall_cents": total["shortfall_cents"], "items": data}


@app.get("/api/findings.csv")
def findings_csv(signal: Optional[str] = None, category: Optional[str] = None, payer: Optional[str] = None, year: Optional[str] = None,
                 status: str = "open", severity: Optional[str] = None, actionable: Optional[str] = None, q: Optional[str] = None,
                 provider: Optional[str] = None, code: Optional[str] = None, deadline: Optional[str] = None, handling: Optional[str] = None,
                 request: Request = None, _: str = Depends(require_auth)):
    w, params = _filters(signal, category, payer, year, status, severity, actionable, q, provider, code, deadline, handling, _extra(request))
    data = rows(f"SELECT {FINDING_COLS} FROM findings{w} ORDER BY payer_group, shortfall_cents DESC", tuple(params))

    def chunks():       # sent in pieces: a streamed answer has no size limit on a serverless host
        buf = io.StringIO()
        if data:
            writer = csv.DictWriter(buf, fieldnames=list(data[0].keys()))
            writer.writeheader()
            for i, r in enumerate(data):
                for k in list(r.keys()):
                    if k.endswith("_cents") and r[k] is not None:
                        r[k] = f"{r[k] / 100:.2f}"
                writer.writerow(r)
                if i % 400 == 399:
                    yield buf.getvalue()
                    buf.seek(0)
                    buf.truncate()
        yield buf.getvalue()
    return StreamingResponse(chunks(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=remitwatch-findings.csv"})


def _packets_for(finding_id: str) -> list[dict]:
    if runtime.serverless():
        from . import cloud
        st = cloud.SERVING_COPY.state()
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
    out: dict[str, Any] = {"finding": f}
    claim_key = f.get("claim_key")
    if claim_key:
        cl = one("SELECT * FROM claims WHERE claim_key=?", (claim_key,))
        if cl:
            if not SHOW_PHI:
                # only columns known to carry no patient identifier leave the server (the claim loop also holds member numbers,
                # other subscribers' names and free-form references)
                initials = _initials(cl.get("patient_last"), cl.get("patient_first"))
                cl = {k: cl.get(k) for k in CLAIM_PUBLIC_COLS}
                cl["patient"] = initials
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
        tx = one("SELECT transaction_key, source_name, payer_name, payer_id, payer_contact_phone, payer_contact_url, payment_method, payment_amount_cents, payment_date, trace_number, production_date, payee_npi FROM transactions WHERE transaction_key=?", (f["transaction_key"],))
        out["remit"] = tx
        if f.get("line_key") and has_table("line_links"):
            out["link"] = one("SELECT * FROM line_links WHERE line_key=?", (f["line_key"],))
            out["handling"] = rows("SELECT * FROM line_handling WHERE line_key=? ORDER BY transaction_date", (f["line_key"],))
        pcn = f.get("patient_control_number")
        if pcn:
            out["history"] = rows("SELECT c.claim_key, c.claim_status_code, c.charge_cents, c.paid_cents, c.payment_date, c.payer_name, c.trace_number FROM claims c JOIN transactions t ON t.transaction_key=c.transaction_key WHERE c.patient_control_number=? AND t.superseded=0 ORDER BY c.payment_date", (pcn,))
            out["events"] = rows("SELECT at, kind, from_status, to_status, amount_cents, actor, note FROM finding_events WHERE finding_id=? ORDER BY at DESC, id DESC LIMIT 30", (finding_id,)) if has_table("finding_events") else []
            out["packets"] = _packets_for(finding_id)
            out["ema"] = rows("SELECT family, source_name, service_date, cpt, modifiers, units, charges_cents, payments_cents, adjustments_cents, allowed_cents, claim_status, activity_type, carc, rarc, adjustment_status, adjustment_subtype, provider, payer, denial_category, previously_denied FROM ema_rows WHERE claim_id=? ORDER BY family, service_date, row_index", (pcn,))
    return out


def _record_status(ids: list[str], status: str, notes: str | None, recovered_cents: int | None, actor: str) -> int:
    """Apply a status to one or more findings. On a serverless host the change is also written to the journal in the
    state folder, which is what makes it last."""
    if runtime.serverless():
        from .cloud import SERVING_COPY
        conn = db()
        try:
            known = [i for i in ids if conn.execute("SELECT 1 FROM findings WHERE finding_id=?", (i,)).fetchone()]
        finally:
            conn.close()
        if not known:
            return 0
        if len(known) == 1:
            SERVING_COPY.record({"kind": "status", "finding_id": known[0], "status": status, "notes": notes,
                                 "recovered_cents": recovered_cents, "actor": actor})
        else:
            SERVING_COPY.record({"kind": "bulk_status", "ids": known, "status": status, "notes": notes, "actor": actor})
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
def rates(payer: Optional[str] = None, year: Optional[str] = None, code: Optional[str] = None, min_n: int = 20, _: str = Depends(require_auth)):
    where, params = ["n >= ?"], [min_n]
    if payer:
        where.append("payer_group=?"); params.append(payer)
    if year:
        where.append("year=?"); params.append(year)
    if code:
        where.append("code=?"); params.append(code)
    return rows(f"SELECT * FROM payer_rates WHERE {' AND '.join(where)} ORDER BY payer_group, year, code, tier, pos_class", tuple(params))


@app.get("/api/rates/matrix")
def rates_matrix(year: str, tier: str = "physician", codes: str = "99203,99204,99213,99214,11102,11103,17000,17003,17110,17311,88305,12031,13132,14040", _: str = Depends(require_auth)):
    code_list = [c for c in codes.split(",") if c]
    data = rows(f"SELECT payer_group, payer_kind, code, mode_cents, n, medicare_cents, pct_of_medicare FROM payer_rates WHERE year=? AND tier=? AND pos_class='office' AND code IN ({','.join('?' * len(code_list))}) AND n >= 20",
                (year, tier, *code_list))
    payers: dict[str, dict] = {}
    for r in data:
        p = payers.setdefault(r["payer_group"], {"payer_group": r["payer_group"], "payer_kind": r["payer_kind"], "codes": {}, "lines": 0})
        p["codes"][r["code"]] = {"cents": r["mode_cents"], "n": r["n"], "medicare_cents": r["medicare_cents"], "pct": r["pct_of_medicare"]}
        p["lines"] += r["n"]
    out = sorted(payers.values(), key=lambda p: -p["lines"])
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
    if SHOW_PHI and detail.get("claim"):
        detail = dict(detail, claim=one("SELECT * FROM claims WHERE claim_key=?", (detail["finding"]["claim_key"],)) or detail["claim"])
    return build_packet(detail, show_phi=SHOW_PHI)


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
              limit: int = Query(400, le=2000), _: str = Depends(require_auth)):
    """Findings grouped by one or two dimensions, with the number of billed lines behind them where the store has it,
    so every count comes with a rate (findings per 100 lines)."""
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
                           SUM(status='lost') lost_n, SUM(status IN ('open','in_progress') AND appeal_deadline >= date('now')) live_n,
                           COALESCE(SUM(CASE WHEN status IN ('open','in_progress') AND appeal_deadline >= date('now') THEN shortfall_cents END),0) live_cents
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
    return {"dim": dim, "dim2": dim2, "family": family, "rows": data, "total": totals, "total_lines": (total_lines or {}).get("lines"),
            "rate_scope": {"downcoding": "office visit lines billed", "code_change": "procedure lines billed"}.get(family, "service lines billed"),
            "denominator_for": exposure_level, "dims": sorted(DIMS)}


# --------------------------------------------------------------------------- progress: are we winning
MONEY = "category IN ('downcoding','bundling','denial','underpayment') AND COALESCE(stale,0)=0"


@app.get("/api/progress")
def progress(_: str = Depends(require_auth)):
    out: dict[str, Any] = {}
    out["pipeline"] = rows(f"SELECT status, COUNT(*) n, COALESCE(SUM(shortfall_cents),0) cents, COALESCE(SUM(recovered_cents),0) recovered_cents FROM findings WHERE {MONEY} GROUP BY 1")
    out["live"] = one(f"SELECT COUNT(*) n, COALESCE(SUM(shortfall_cents),0) cents FROM findings WHERE {MONEY} AND status IN ('open','in_progress') AND appeal_deadline >= date('now')")
    out["expired"] = one(f"SELECT COUNT(*) n, COALESCE(SUM(shortfall_cents),0) cents FROM findings WHERE {MONEY} AND status IN ('open','in_progress') AND appeal_deadline < date('now')")
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
        if not cloud.trigger_cycle(base_url(request), "button"):
            raise HTTPException(400, "The site cannot start a run: CRON_SECRET is not set. Add it in Vercel and redeploy.")
        cloud.SERVING_COPY._status = (0.0, None)
        return {"started": True}
    from .pipeline import run_once_in_background
    run_once_in_background(DB_PATH, "button")
    return {"started": True}


def _cron_auth(request: Request, credentials: Optional[HTTPBasicCredentials]) -> None:
    """Vercel's scheduler and the site's own follow-up calls send the CRON_SECRET as a bearer token. A person may also
    start a run with the dashboard login."""
    secret = (os.environ.get("CRON_SECRET") or "").strip()
    sent = (request.headers.get("authorization") or "").strip()
    if secret and secrets.compare_digest(sent.encode(), f"Bearer {secret}".encode()):
        return
    if not PASSWORD:
        raise HTTPException(status_code=401, detail="Unauthorized")
    require_auth(credentials)
    if request.method != "GET":
        require_xhr(request)


@app.api_route("/api/cron/cycle", methods=["GET", "POST"], include_in_schema=False)
def cron_cycle(request: Request, trigger: str = "cron", delay: int = 0, full: int = 0, force: int = 0,
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
    st = state_from_env()
    if st is None:
        raise HTTPException(503, "Dropbox is not connected yet. Open /setup.")
    delay = min(max(int(delay), 0), 90)
    client = (getattr(st, "client", None) or DropboxClient()) if dropbox_configured() else None
    # the wait happens inside the cycle, after it has taken the lock, so later notifications see a run in progress
    out = cloud.run_cloud_cycle(st, client, budget_s=max(60, runtime.max_seconds() - delay), trigger=trigger[:24], full=bool(full),
                                force_detect=bool(force), delay_s=delay)
    if out.get("more") and out.get("progress"):
        out["continued"] = cloud.trigger_cycle(base_url(request), "continue")
    elif client is not None and not out.get("skipped") and not out.get("more"):
        try:        # files that landed while this cycle ran
            if cloud.has_new_files(st, client):
                out["continued"] = cloud.trigger_cycle(base_url(request), "continue", delay=30)
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
        st = state_from_env()
        if st is None or cloud.running(st):
            return      # a run in progress looks for late arrivals itself when it ends
        if cloud.has_new_files(st, getattr(st, "client", None) or DropboxClient()):
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
    if not secret or not hmac.compare_digest(sig, hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()):
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
    if not PASSWORD and not open_allowed():
        # before a password exists the page may only say that it is missing: nothing else about the site is public
        return JSONResponse({"locked": True, "ready": False, "checks": [
            {"key": "password", "label": "Dashboard password (REMITWATCH_PASSWORD)", "ok": False, "detail": "not set",
             "fix": "Add REMITWATCH_PASSWORD in Vercel (Settings, Environment Variables), then redeploy. Everything else on this page appears after that."}]},
            headers={"Cache-Control": "no-store"})
    require_auth(credentials)
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
