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

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

from . import __version__
from .codes import CARC, RARC

DB_PATH = os.environ.get("REMITWATCH_DB", "data/remitwatch.sqlite")
PASSWORD = os.environ.get("REMITWATCH_PASSWORD", "")
USERNAME = os.environ.get("REMITWATCH_USER", "oda")
SHOW_PHI = os.environ.get("REMITWATCH_SHOW_PHI", "0") == "1"

STATIC_DIR = Path(__file__).parent / "static"
security = HTTPBasic(auto_error=False)

app = FastAPI(title="RemitWatch", version=__version__, docs_url="/api/docs", redoc_url=None)


def require_auth(credentials: Optional[HTTPBasicCredentials] = Depends(security)) -> str:
    """Basic auth with a single shared password. When no password is configured the
    dashboard is open (local use only)."""
    if not PASSWORD:
        return "anonymous"
    if credentials is None or not (secrets.compare_digest(credentials.username, USERNAME)
                                   and secrets.compare_digest(credentials.password, PASSWORD)):
        raise HTTPException(status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": "Basic realm=RemitWatch"})
    return credentials.username


def db() -> sqlite3.Connection:
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
@app.get("/", include_in_schema=False)
def index(_: str = Depends(require_auth)):
    f = STATIC_DIR / "index.html"
    if f.exists():
        return FileResponse(f)
    return HTMLResponse("<h1>RemitWatch</h1><p>static/index.html missing</p>")


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# --------------------------------------------------------------------------- health and meta
@app.get("/api/health")
def health():
    ok = os.path.exists(DB_PATH)
    return {"ok": ok, "db": DB_PATH, "version": __version__, "time": datetime.now(timezone.utc).isoformat()}


@app.get("/api/meta")
def meta(_: str = Depends(require_auth)):
    out: dict[str, Any] = {"version": __version__, "show_phi": SHOW_PHI}
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
    where = ["category IN (%s)" % ",".join("?" * len(MONEY_CATEGORIES))]
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
    out["resolved_by_later_remit"] = one("SELECT COUNT(*) n, SUM(shortfall_cents) shortfall_cents FROM findings WHERE status='resolved' AND notes LIKE 'Paid on a later remittance%'")
    out["deadlines"] = rows("SELECT CASE WHEN appeal_deadline < date('now') THEN 'expired' WHEN appeal_deadline < date('now','+30 days') THEN 'due_30d' WHEN appeal_deadline < date('now','+90 days') THEN 'due_90d' ELSE 'later' END bucket, COUNT(*) n, SUM(shortfall_cents) shortfall_cents FROM findings WHERE status='open' AND appeal_deadline IS NOT NULL AND actionable IN ('appeal','correct_and_resubmit') GROUP BY 1")
    out["info"] = rows("SELECT signal, year, title, shortfall_cents, evidence FROM findings WHERE category='info' ORDER BY signal, year")
    out["years"] = [r["year"] for r in rows("SELECT DISTINCT year FROM findings WHERE year >= '2023' ORDER BY year")]
    return out


# --------------------------------------------------------------------------- findings
FINDING_COLS = ("finding_id, signal, category, severity, actionable, title, patient_control_number, claim_key, line_key, payer_name, payer_group, payer_kind, "
                "rendering_provider_name, rendering_provider_npi, provider_tier, service_date, payment_date, year, procedure_code, modifiers, original_procedure_code, "
                "code_category, charge_cents, allowed_cents, paid_cents, expected_cents, shortfall_cents, carc, carc_text, rarc, rarc_text, appeal_deadline, ema_handling, status, notes, first_seen, last_seen")


def _filters(signal, category, payer, year, status, severity, actionable, q, provider, code, deadline, handling=None):
    where, params = [], []
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
    if status and status != "all":
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
             sort: str = "shortfall", limit: int = Query(100, le=2000), offset: int = 0, _: str = Depends(require_auth)):
    w, params = _filters(signal, category, payer, year, status, severity, actionable, q, provider, code, deadline, handling)
    order = {"shortfall": "shortfall_cents DESC", "date": "service_date DESC", "deadline": "appeal_deadline ASC", "payer": "payer_group, shortfall_cents DESC"}.get(sort, "shortfall_cents DESC")
    total = one(f"SELECT COUNT(*) n, SUM(shortfall_cents) shortfall_cents FROM findings{w}", tuple(params))
    data = rows(f"SELECT {FINDING_COLS} FROM findings{w} ORDER BY {order} NULLS LAST LIMIT ? OFFSET ?", tuple(params) + (limit, offset))
    return {"total": total["n"], "shortfall_cents": total["shortfall_cents"], "items": data}


@app.get("/api/findings.csv")
def findings_csv(signal: Optional[str] = None, category: Optional[str] = None, payer: Optional[str] = None, year: Optional[str] = None,
                 status: str = "open", severity: Optional[str] = None, actionable: Optional[str] = None, q: Optional[str] = None,
                 provider: Optional[str] = None, code: Optional[str] = None, deadline: Optional[str] = None, _: str = Depends(require_auth)):
    w, params = _filters(signal, category, payer, year, status, severity, actionable, q, provider, code, deadline)
    data = rows(f"SELECT {FINDING_COLS} FROM findings{w} ORDER BY payer_group, shortfall_cents DESC", tuple(params))
    buf = io.StringIO()
    if data:
        writer = csv.DictWriter(buf, fieldnames=list(data[0].keys()))
        writer.writeheader()
        for r in data:
            for k in list(r.keys()):
                if k.endswith("_cents") and r[k] is not None:
                    r[k] = f"{r[k] / 100:.2f}"
            writer.writerow(r)
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=remitwatch-findings.csv"})


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
                cl["patient"] = _initials(cl.pop("patient_last", None), cl.pop("patient_first", None))
                for k in ("patient_middle", "patient_id", "insured_last", "insured_first", "insured_id", "corrected_patient_last", "corrected_patient_first", "medical_record_number"):
                    cl.pop(k, None)
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
            out["ema"] = rows("SELECT family, source_name, service_date, cpt, modifiers, units, charges_cents, payments_cents, adjustments_cents, allowed_cents, claim_status, activity_type, carc, rarc, adjustment_status, adjustment_subtype, provider, payer, denial_category, previously_denied FROM ema_rows WHERE claim_id=? ORDER BY family, service_date, row_index", (pcn,))
    return out


@app.post("/api/findings/{finding_id}/status")
async def set_status(finding_id: str, request: Request, _: str = Depends(require_auth)):
    body = await request.json()
    status = body.get("status")
    if status not in ("open", "appealed", "resolved", "dismissed", "in_progress"):
        raise HTTPException(400, "bad status")
    conn = db()
    try:
        conn.execute("UPDATE findings SET status=?, notes=COALESCE(?, notes) WHERE finding_id=?", (status, body.get("notes"), finding_id))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@app.post("/api/findings/bulk-status")
async def bulk_status(request: Request, _: str = Depends(require_auth)):
    body = await request.json()
    ids = body.get("ids") or []
    status = body.get("status")
    if status not in ("open", "appealed", "resolved", "dismissed", "in_progress") or not ids:
        raise HTTPException(400, "bad request")
    conn = db()
    try:
        conn.executemany("UPDATE findings SET status=? WHERE finding_id=?", [(status, i) for i in ids])
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "updated": len(ids)}


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
    return rows(f"SELECT * FROM payer_rates WHERE {' AND '.join(where)} ORDER BY payer_group, year, code, tier", tuple(params))


@app.get("/api/rates/matrix")
def rates_matrix(year: str, tier: str = "physician", codes: str = "99203,99204,99213,99214,11102,11103,17000,17003,17110,17311,88305,12031,13132,14040", _: str = Depends(require_auth)):
    code_list = [c for c in codes.split(",") if c]
    data = rows(f"SELECT payer_group, payer_kind, code, mode_cents, n, medicare_cents, pct_of_medicare FROM payer_rates WHERE year=? AND tier=? AND code IN ({','.join('?' * len(code_list))}) AND n >= 20",
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
    where, params = ["COALESCE(service_date, payment_date) >= '2023-01'"], []
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
                   FROM findings WHERE category IN ('downcoding','bundling','denial','underpayment','fees','contract')
                   GROUP BY payer_group, payer_kind ORDER BY shortfall_cents DESC""")


@app.get("/api/providers")
def providers(_: str = Depends(require_auth)):
    return rows("""SELECT f.rendering_provider_npi npi, MAX(f.rendering_provider_name) name, MAX(p.tier) tier, COUNT(*) findings, SUM(f.shortfall_cents) shortfall_cents
                   FROM findings f LEFT JOIN provider_tiers p ON p.npi=f.rendering_provider_npi
                   WHERE f.category IN ('downcoding','bundling','denial','underpayment') AND f.rendering_provider_npi IS NOT NULL
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
    return build_packet(detail, show_phi=SHOW_PHI)
