"""
The Results tab: what RemitWatch brings back (remitwatch.results does the work; this is the part a browser talks to).

    GET  /api/results                     every figure of the tab: money returned and its split, lift, ROI, work done,
                                          outcomes, hours saved, denials, credits, the week by week figures, today's spot
                                          checks with the agreement rates, the owner's minutes and costs
    GET  /api/results/scoreboard          Today's money row: recovered, lost to deadlines, at risk in 14 days, given up
                                          without a decision, week by week (unit C2 shows it)
    GET  /api/results/list?key=&page=&per=    the exact items behind one figure (its count is the list's length)
    GET  /api/results/list.csv?key=       the same list as CSV (no patient details; the claim id only with names on)
    GET  /api/results/settings            the owner's minutes and costs, with every earlier version
    PUT  /api/results/settings            {values, base_version, name}: save them (the owner's password when one is set)
    GET  /api/results/spot?day=           the day's five spot checks with their answers
    POST /api/results/spot                {day, finding_id, answer, reason, note, name}: one person's check of one item
    GET  /api/results/denials/clinicians  denial rates by clinician (the owner's password when one is set)

Every route needs the sign-in (api.require_auth); the ones that change something also need api.require_xhr. On a server
the page works the figures out itself before the first run that does (once, in the request) and, after a newer analysis,
in the background while it shows the stored figures with "updating"; on a serverless host it only reads what the run
stored (the dashboard copy holds part of the store), and says so until the first run has. day= (the page's own date,
YYYY-MM-DD) keys the spot checks by the practice's day rather than the server's UTC day.
"""
from __future__ import annotations

import threading
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from . import results, runtime

router = APIRouter()
_security = HTTPBasic(auto_error=False)
_COMPUTE = threading.Lock()


def _api():
    from . import api
    return api


def _user(request: Request, credentials: Optional[HTTPBasicCredentials] = Depends(_security)) -> str:
    """The same sign-in as every other route."""
    return _api().require_auth(request, credentials)


def _owner(request: Request, credentials: Optional[HTTPBasicCredentials] = Depends(_security)) -> str:
    """The sign-in and, when the owner has a password, that password (the Providers tab's rule)."""
    return _api().require_owner(request, credentials)


def _xhr(request: Request) -> None:
    _api().require_xhr(request)


def _state():
    """The state folder on a serverless host (the page's own records live there), else None."""
    if not runtime.serverless():
        return None
    from . import cloud
    st = cloud.SERVING_COPY.state()
    if st is None:
        raise HTTPException(503, "This site's storage is not set up yet: open /setup.")
    return st


def _refused(exc: results.ResultsError) -> JSONResponse:
    return JSONResponse({"detail": exc.message}, status_code=exc.status, headers={"Cache-Control": "no-store"})


_BG: dict = {"thread": None, "error": None}


def _refresh_in_background() -> None:
    """A server's figures worked out again after a newer analysis, in a thread of its own (one at a time, under the same
    lock as a request's first working out), so no page waits for them; the page shows the stored ones meanwhile."""
    t = _BG.get("thread")
    if t is not None and t.is_alive():
        return

    def work() -> None:
        conn = _api().db()
        try:
            with _COMPUTE:
                if results.needs_compute(conn):
                    results.refresh(conn, host="server", force=True)
            _BG["error"] = None
        except Exception as exc:        # the stored figures stay up; the page says they could not be brought up to date
            from . import runlog
            runlog.caught(exc, "results.refresh")
            _BG["error"] = f"{type(exc).__name__}"
        finally:
            conn.close()
    t = threading.Thread(target=work, name="results-refresh", daemon=True)
    _BG["thread"] = t
    t.start()


def wait_background(timeout: float = 60) -> None:
    """Wait for a background working out to finish (tests, and a clean shutdown)."""
    t = _BG.get("thread")
    if t is not None:
        t.join(timeout)


def _ensure(conn) -> tuple[dict, bool]:
    """(the run's figures, whether newer ones are being worked out). A server works them out in the request only when
    none exist yet; after a newer analysis it starts them in the background and answers with the stored ones. A serverless
    host never works them out (its dashboard copy holds only part of the store)."""
    if runtime.serverless():
        return results.dashboard(conn) or {}, False
    d = results.dashboard(conn)
    if d is None:
        with _COMPUTE:
            if results.dashboard(conn) is None:
                results.refresh(conn, host="server", force=True)
        return results.dashboard(conn) or {}, False
    if results.needs_compute(conn):
        _refresh_in_background()
        return d, True
    return d, False


def _today() -> str:
    return results._today()


def _day(day: Optional[str]) -> str:
    """The practice's day for the spot checks: the page's own date when it sends one within a day of the server's (a
    browser in the practice's time zone), else the server's day."""
    t = _today()
    d = str(day or "")[:10]
    if results._d(d) and abs(results._days(t, d) or 0) <= 1:
        return d
    return t


def _spot(conn, st, day: str, files=None) -> list[dict]:
    if st is not None:
        return results.spot_day_state(conn, st, day, files=files)
    return results.spot_day(conn, day)


def _spot_due(rows: list[dict]) -> dict:
    """The day's spot checks for Today and the top of the Results tab: how many wait for an answer."""
    answered = sum(1 for r in rows if r.get("answer"))
    return {"picked": len(rows), "answered": answered, "due": len(rows) - answered}


@router.get("/api/results")
def results_page(request: Request, day: Optional[str] = Query(None, max_length=10), _: str = Depends(_user)):
    api = _api()
    st = _state()
    conn = api.db()
    try:
        t = _today()
        sd = _day(day)
        files = results.StateFiles(st, sd) if st is not None else None
        d, updating = _ensure(conn)
        settings = results.read_settings(conn, st, files=files)
        live = results.live_figures(conn, t)
        try:
            spot_today = _spot(conn, st, sd, files)
        except results.ResultsError as exc:
            spot_today, spot_error = [], exc.message
        else:
            spot_error = None
        spot_all = results.spot_rows_all(conn if st is None else None, st, sd, files=files)
        denials = dict(d.get("denials") or {})
        out: dict[str, Any] = {
            "ready": bool(d), "today": t, "day": sd, "host": "serverless" if runtime.serverless() else "server", "as_of": d.get("at"),
            "detect_run": d.get("detect_run"), "first_run": d.get("first_run"), "version": results.VERSION, "updating": updating,
            "update_error": _BG.get("error") if updating else None,
            "money": d.get("money"), "lift": d.get("lift"), "work": d.get("work"), "outcomes": d.get("outcomes"),
            "lost": d.get("lost"), "credits": d.get("credits"), "denials": denials, "counts": d.get("counts") or {},
            "hours": results.hours_saved(d.get("hours_counts") if d else None, settings),
            "roi": results.roi(d, settings, t) if d else None,
            "live": live, "settings": settings, "minute_labels": results.MINUTE_LABELS, "cost_labels": results.COST_LABELS,
            "snapshots": results.snapshots(conn, d),
            "baseline": results._meta(conn, results.BASELINE_KEY, None),
            "spot": {"today": spot_today, "error": spot_error, "summary": results.spot_summary(spot_all), "due": _spot_due(spot_today),
                     "reasons": results.SPOT_REASONS, "strata": results.STRATUM_LABELS},
            "labels": {"buckets": results.BUCKET_LABELS, "work": results.WORK_KINDS, "states": results.STATE_LABELS,
                       "closures": results.CLOSURE_LABELS, "signals": results._labels(), "minutes": results.MINUTE_SHORT},
            "owner_open": api.owner_state(request) == "open", "owner_locked": bool(api.OWNER_PASSWORD), "show_phi": bool(api.SHOW_PHI),
            "errors": d.get("errors") or [],
        }
        if runtime.serverless() and not d:
            out["note"] = ("These figures are worked out by the daily run on the whole store. The first run after this "
                           "update fills them; the counts below are live.")
        return JSONResponse(out, headers={"Cache-Control": "no-store"})
    finally:
        conn.close()


@router.get("/api/results/scoreboard")
def results_scoreboard(day: Optional[str] = Query(None, max_length=10), _: str = Depends(_user)):
    """{recovered_cents, lost_cents, at_risk_cents, given_up_cents, as_of, weeks: [...], counts, basis, spot}; a figure the
    run has not worked out yet is null (Today shows a dash). spot: today's spot checks waiting for an answer."""
    st = _state()
    conn = _api().db()
    try:
        _ensure(conn)
        out = results.scoreboard(conn, _today())
        try:
            out["spot"] = _spot_due(_spot(conn, st, _day(day)))
        except results.ResultsError as exc:      # the figures stand; the spot checks say why they are missing
            out["spot"] = {"picked": 0, "answered": 0, "due": 0, "error": exc.message}
        return JSONResponse(out, headers={"Cache-Control": "no-store"})
    finally:
        conn.close()


GONE = ("That list is not in the figures any more: the figures were worked out again since this page was drawn (money "
        "that came or was taken back since moves items between lists). Reload the page to see the figures as they are now.")


@router.get("/api/results/list")
def results_list(key: str = Query(..., max_length=200), page: int = Query(0, ge=0, le=100000), per: int = Query(50, ge=1, le=500),
                 _: str = Depends(_user)):
    conn = _api().db()
    try:
        try:
            return JSONResponse(results.list_rows(conn, key, today=_today(), page=page, per=per, show_phi=bool(_api().SHOW_PHI)),
                                headers={"Cache-Control": "no-store"})
        except KeyError:
            raise HTTPException(404, GONE)
    finally:
        conn.close()


@router.get("/api/results/list.csv")
def results_list_csv(key: str = Query(..., max_length=200), _: str = Depends(_user)):
    conn = _api().db()
    try:
        try:
            text = results.list_csv(conn, key, today=_today(), show_phi=bool(_api().SHOW_PHI))
        except KeyError:
            raise HTTPException(404, GONE)
    finally:
        conn.close()
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in key)[:60] or "list"
    return Response(text, media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="results-{safe}.csv"',
                                                          "Cache-Control": "no-store"})


@router.get("/api/results/settings")
def results_settings(_: str = Depends(_user)):
    st = _state()
    conn = _api().db()
    try:
        rec = results.read_settings(conn, st)
    finally:
        conn.close()
    return JSONResponse(dict(rec, minute_labels=results.MINUTE_LABELS, cost_labels=results.COST_LABELS), headers={"Cache-Control": "no-store"})


@router.put("/api/results/settings", dependencies=[Depends(_xhr)])
def results_settings_save(body: dict = Body(default={}), user: str = Depends(_owner)):
    if not isinstance(body, dict):
        raise HTTPException(400, "Send an object.")
    values = body.get("values")
    who = str(body.get("name") or "").strip()[:80] or user
    st = _state()
    try:
        if st is not None:
            rec = results.save_settings(values, who=who, base_version=body.get("base_version"), state=st)
        else:
            conn = _api().db()
            try:
                rec = results.save_settings(values, who=who, base_version=body.get("base_version"), conn=conn)
            finally:
                conn.close()
    except results.ResultsError as exc:
        return _refused(exc)
    return JSONResponse(dict(rec, ok=True), headers={"Cache-Control": "no-store"})


@router.get("/api/results/spot")
def results_spot(day: Optional[str] = Query(None, max_length=10), summary: int = Query(0, ge=0, le=1), _: str = Depends(_user)):
    """The day's picks with their answers (the practice's day picks them on its first ask; an earlier day is read, never
    picked); summary=1 adds the agreement rates, so the page can redraw them after an answer without loading everything."""
    st = _state()
    d = str(day or _today())[:10]
    if not results._d(d):
        raise HTTPException(400, "The day must be written YYYY-MM-DD.")
    conn = _api().db()
    try:
        files = results.StateFiles(st, d) if st is not None else None
        try:
            if d == _day(d):
                picks = _spot(conn, st, d, files)
            else:
                picks = [r for r in results.spot_rows_all(conn if st is None else None, st, d, files=files) if r.get("day") == d]
            out = {"day": d, "items": picks, "reasons": results.SPOT_REASONS, "due": _spot_due(picks)}
            if summary:
                out["summary"] = results.spot_summary(results.spot_rows_all(conn if st is None else None, st, d, files=files))
        except results.ResultsError as exc:
            return _refused(exc)
        return JSONResponse(out, headers={"Cache-Control": "no-store"})
    finally:
        conn.close()


@router.post("/api/results/spot", dependencies=[Depends(_xhr)])
def results_spot_answer(body: dict = Body(default={}), user: str = Depends(_user)):
    if not isinstance(body, dict):
        raise HTTPException(400, "Send an object.")
    day = str(body.get("day") or "")[:10]
    fid = str(body.get("finding_id") or "")[:64]
    if not results._d(day) or not fid:
        raise HTTPException(400, "Name the day and the item.")
    who = str(body.get("name") or "").strip()[:80] or user
    st = _state()
    try:
        conn = _api().db()
        try:
            if st is not None:
                out = results.record_spot_check_state(st, day, fid, body.get("answer"), body.get("reason"), body.get("note"), who)
            else:
                out = results.record_spot_check(conn, day, fid, body.get("answer"), body.get("reason"), body.get("note"), who)
            # the agreement rates as they are now, so the page redraws them in place (it never reloads for an answer)
            files = results.StateFiles(st, day) if st is not None else None
            out["summary"] = results.spot_summary(results.spot_rows_all(conn if st is None else None, st, day, files=files))
            out["who"] = who
        finally:
            conn.close()
    except results.ResultsError as exc:
        return _refused(exc)
    return JSONResponse(out, headers={"Cache-Control": "no-store"})


@router.get("/api/results/denials/clinicians")
def results_denials_clinicians(_: str = Depends(_owner)):
    """Denial rates by clinician, month by month: what each clinician bills is the owner's to see (the Providers tab's
    rule), so this needs the owner's password when one is set."""
    conn = _api().db()
    try:
        rows = results._meta(conn, results.CLINICIAN_KEY, None)
        cache = results._meta(conn, results.DENIALS_KEY, {}) or {}
    finally:
        conn.close()
    clin = rows if isinstance(rows, list) else (cache.get("clinicians") or [])
    agg: dict = {}
    for m, npi, name, *v in clin:
        a = agg.setdefault(npi, {"npi": npi, "name": name, "answered": 0, "denied": 0, "billed_cents": 0, "denied_cents": 0, "months": []})
        a["answered"] += v[0]
        a["denied"] += v[1]
        a["billed_cents"] += v[2]
        a["denied_cents"] += v[3]
        a["months"].append({"month": m, "answered": v[0], "denied": v[1], "line_rate": results._pct(v[1], v[0])})
    out = sorted(agg.values(), key=lambda a: -a["answered"])[:40]
    for a in out:
        a["line_rate"] = results._pct(a["denied"], a["answered"])
        a["dollar_rate"] = results._pct(a["denied_cents"], a["billed_cents"])
        a["months"] = a["months"][-24:]
    return JSONResponse({"clinicians": out}, headers={"Cache-Control": "no-store"})
