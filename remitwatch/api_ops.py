"""
The Data tab's operations panels and the decision log an item's drawer shows (the self-running modules: remitwatch.selfcheck,
runlog, repair, diagnose and archive). Every route needs the sign-in (api.require_auth); the ones that change something
also need api.require_xhr, and accepting the self-check's findings is the owner's (api.require_owner).

    GET  /api/ops/panels                        the three panels at once: "Running on its own" (selfcheck.panel), "Logs
                                                and diagnostics" (runlog.panel), "Store size and memory" (archive.panel)
    GET  /api/ops/selfcheck                     "Running on its own"
    GET  /api/ops/selfcheck/offenders/{check}   every item behind a red check of the latest run, largest first
    GET  /api/ops/selfcheck/audit/{list}        the weekly self-audit's gaps (gap) or possibly wasted work (wasted)
    POST /api/ops/selfcheck/audit               run the self-audit now (a server) or ask the next run (Vercel)
    POST /api/ops/selfcheck/accept              {checks, run_id}: the owner accepts what that run found (exactly that)
    GET  /api/ops/logs                          "Logs and diagnostics"
    GET  /api/ops/diagnostic-bundle             the diagnostic bundle as a zip (no patient data; logged with who and when)
    GET  /api/ops/archive                       "Store size and memory"
    POST /api/archive/reopen                    {claim_id | finding_id, reason}: an archived bill comes back with the next run
    GET  /api/findings/{finding_id}/decisions   "Why did this change?": every automatic change to the item, with its rule

On a serverless host every route reads what the runs stored (the dashboard's copy and the state folder); nothing is
worked out again on the copy, and a request is journaled for the next run.
"""
from __future__ import annotations

import time
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, Request
from fastapi.responses import Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from . import runlog, runtime

router = APIRouter()
_security = HTTPBasic(auto_error=False)
BUNDLE_WHAT = "Give this file and the repository to Claude to find the cause of a problem. It holds no patient data."


def _api():
    from . import api
    return api


def _user(request: Request, credentials: Optional[HTTPBasicCredentials] = Depends(_security)) -> str:
    """The same sign-in as every other route."""
    return _api().require_auth(request, credentials)


def _owner(request: Request, credentials: Optional[HTTPBasicCredentials] = Depends(_security)) -> str:
    """The sign-in and, when the owner has a password, that password: accepting a change is a business decision."""
    return _api().require_owner(request, credentials)


def _xhr(request: Request) -> None:
    _api().require_xhr(request)


class _Conn:
    """The store (a server) or the dashboard's copy (Vercel, shared by the instance: never closed here)."""

    def __enter__(self):
        self.c = _api().db()
        return self.c

    def __exit__(self, *exc: Any) -> bool:
        if not runtime.serverless():
            self.c.close()
        return False


def _state():
    from .cloud import SERVING_COPY
    return SERVING_COPY.state()


def _logs():
    """Where this host's logs are: the server's logs folder, or (Vercel) the state folder."""
    if runtime.serverless():
        return _state()
    return runlog.logs_dir_for(_api().DB_PATH)


def archive_sink():
    """Where the archive's files are: beside the store (a server), or the state folder's archive/ (Vercel)."""
    from . import archive
    if runtime.serverless():
        return archive.StateSink(_state())
    return archive.sink_for(db_path=_api().DB_PATH)


# ------------------------------------------------------------------------------------------------------------- panels
@router.get("/api/ops/panels")
def ops_panels(_: str = Depends(_user)):
    from . import archive, selfcheck
    with _Conn() as c:
        out: dict = {"selfcheck": selfcheck.panel(c), "archive": archive.panel(c)}
        try:
            out["logs"] = dict(runlog.panel(c, _logs()), bundle={"what": BUNDLE_WHAT})
        except Exception as exc:        # noqa: BLE001  (the logs folder could not be read: the other panels stand)
            runlog.caught(exc, "ops:logs", level="warning")
            out["logs"] = {"error": "The logs could not be read just now. Reload the page in a minute.",
                           "bundle": {"what": BUNDLE_WHAT}}
        return out


@router.get("/api/ops/selfcheck")
def ops_selfcheck(_: str = Depends(_user)):
    from . import selfcheck
    with _Conn() as c:
        return selfcheck.panel(c)


@router.get("/api/ops/selfcheck/offenders/{check}")
def ops_offenders(check: str = Path(..., pattern=r"^[a-z_0-9]{2,40}$"), _: str = Depends(_user)):
    from . import selfcheck
    with _Conn() as c:
        last = (selfcheck.meta_get(c, selfcheck.K_LAST, {}) or {})
        return {"check": check, "run_id": last.get("run_id"), "items": selfcheck.offenders(c, check)}


@router.get("/api/ops/selfcheck/audit/{list_name}")
def ops_audit_list(list_name: str = Path(..., pattern=r"^(gap|wasted)$"), _: str = Depends(_user)):
    from . import selfcheck
    with _Conn() as c:
        return {"list": list_name, "items": selfcheck.audit_lists(c, list_name)}


@router.post("/api/ops/selfcheck/audit")
def ops_audit_now(request: Request, user: str = Depends(_user)):
    """The self-audit now (a server: it takes a few seconds on the full store), or asked of the next run (Vercel)."""
    _xhr(request)
    if runtime.serverless():
        from .cloud import record_action
        record_action(_state(), {"kind": "selfcheck_request", "what": "audit", "user": user})
        return {"asked": True, "text": "The next run checks the worklist against the money (within a day, or press Run now)."}
    from . import selfcheck
    conn = _api().db()
    try:
        got = selfcheck.apply_request(conn, {"what": "audit"}) or {}
        conn.commit()
        au = got.get("audit") or {}
        return {"ran": True, "audit": {k: au.get(k) for k in ("gaps_n", "gaps_cents", "wasted_n", "alert", "today")}}
    finally:
        conn.close()


@router.post("/api/ops/selfcheck/accept")
def ops_accept(request: Request, body: dict = Body(...), user: str = Depends(_owner)):
    """The owner accepts exactly the offenders of the run the page showed (`run_id`); a later run's are never accepted."""
    _xhr(request)
    checks = body.get("checks") if isinstance(body, dict) else None
    run_id = body.get("run_id") if isinstance(body, dict) else None
    if not isinstance(checks, list) or not all(isinstance(x, str) for x in checks) or not checks:
        raise HTTPException(400, "Choose the checks to accept.")
    if runtime.serverless():
        from .cloud import record_action
        record_action(_state(), {"kind": "selfcheck_request", "what": "accept", "checks": checks, "run_id": run_id, "user": user})
        return {"asked": True, "text": "Accepted: the next run takes it (within a day, or press Run now)."}
    from . import selfcheck
    conn = _api().db()
    try:
        got = selfcheck.accept_findings(conn, user, checks, run_id=run_id)
        conn.commit()
        return got
    finally:
        conn.close()


# --------------------------------------------------------------------------------------------- logs and diagnostics
@router.get("/api/ops/logs")
def ops_logs(_: str = Depends(_user)):
    with _Conn() as c:
        return dict(runlog.panel(c, _logs()), bundle={"what": BUNDLE_WHAT})


@router.get("/api/ops/diagnostic-bundle")
@router.get("/api/ops/bundle")
def ops_bundle(days: int = Query(7, ge=1, le=60),
               run: Optional[str] = Query(None, pattern=r"^run-\d{8}T\d{6}Z(-[0-9a-f]{4,8})?$"), user: str = Depends(_user)):
    """The diagnostic bundle (remitwatch.diagnose): run logs, errors, self-checks and repairs, no patient data. Who asked
    for it and when goes in the event log (decision 7). A problem building it is a 500 with no detail, never a zip
    holding an exception's text."""
    from . import diagnose
    if runtime.serverless():
        with _Conn() as c:
            res = diagnose.build_bundle(conn=c, state=_state(), days=days, run_id=run)
    else:
        res = diagnose.build_bundle(db_path=_api().DB_PATH, days=days, run_id=run)
    _log_bundle(user, days, run)
    name = time.strftime("remitwatch-diagnostic-%Y%m%d-%H%M.zip", time.gmtime())
    return Response(res["bytes"], media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


def _log_bundle(user: str, days: int, run: Optional[str]) -> None:
    """Who downloaded the bundle (a fingerprint of the sign-in, never the name), when, for which days (decision 7): one
    small file in the logs folder's downloads/, beside the runs (it is no run of its own, so the panel's last run stays
    the last cycle)."""
    try:
        sink = runlog.sink_for(state=_state()) if runtime.serverless() else runlog.sink_for(db_path=_api().DB_PATH)
        at = runlog.utcnow()
        sink.write_json(f"downloads/bundle-{at.strftime('%Y%m%dT%H%M%S%f')}.json",
                        {"at": at.isoformat(timespec="seconds"), "who": runlog.fingerprint(user), "days": days, "run": run})
    except Exception as exc:        # noqa: BLE001  (the download stands)
        runlog.caught(exc, "ops:bundle_log", level="warning")


@router.get("/api/findings/{finding_id}/decisions")
def finding_decisions(finding_id: str = Path(..., min_length=1, max_length=128), _: str = Depends(_user)):
    # any id the store holds (an item kept from an earlier release has another shape): it is only a bound parameter
    with _Conn() as c:
        return runlog.decisions_for(c, finding_id)


# ----------------------------------------------------------------------------------------------------- the archive
@router.get("/api/ops/archive")
def ops_archive(_: str = Depends(_user)):
    from . import archive
    with _Conn() as c:
        return archive.panel(c, _state() if runtime.serverless() else None)


@router.post("/api/archive/reopen")
def archive_reopen(request: Request, body: dict = Body(...), user: str = Depends(_user)):
    """An archived bill comes back whole with the next run (its items with it), the item's status set to To do."""
    _xhr(request)
    from . import archive
    if not isinstance(body, dict):
        raise HTTPException(400, "The request could not be read. Reload the page and try again.")
    claim, fid = str(body.get("claim_id") or "").strip(), str(body.get("finding_id") or "").strip()
    reason = str(body.get("reason") or "reopened")[:200]
    with _Conn() as c:
        if not claim and fid:
            row = c.execute("SELECT bill FROM archive_findings WHERE finding_id = ?", (fid,)).fetchone() \
                if c.execute("SELECT 1 FROM sqlite_master WHERE name='archive_findings'").fetchone() else None
            claim = row[0] if row else ""
        bill = archive.resolve_bill(c, claim) if claim else None
    if not bill:
        raise HTTPException(404, "That claim is not in the archive.")
    if runtime.serverless():
        from .cloud import record_action
        record_action(_state(), archive.journal_entry(bill, actor=user, reason=reason, finding_id=fid or None))
    else:
        conn = _api().db()
        try:
            archive.request_restore(conn, bill, actor=user, reason=reason, finding_id=fid or None, status="open")
        finally:
            conn.close()
    return {"queued": True, "text": "It comes back with the next run (within a day, or press Run now)."}


def archived_finding(finding_id: str) -> dict | None:
    """An item the archive took, by its id (a deep link to an item that left the worklist), or None."""
    from . import archive
    with _Conn() as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='archive_findings'").fetchone():
            return None
        return archive.lookup_finding(c, finding_id, sink=archive_sink())


def archived_search(text: str, have: set) -> list[dict]:
    """Archived bills Find a claim names (by claim id, bill or payer claim number; never by name or member number)."""
    from . import archive
    with _Conn() as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='archive_ids'").fetchone():
            return []
        return [dict(x, archived=True) for x in archive.search(c, text) if x.get("bill") not in have]
