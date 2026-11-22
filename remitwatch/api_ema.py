"""
The EMA pages: Today's EMA figures, the Data tab, the owner's write-off policy table, and the reconcile summary with its
CSVs. remitwatch.ema_checks does the work on the whole store during the run; this is the part a browser talks to.

    GET  /api/ema/today                 Today's counts: items by state, follow-ups due, EMA's automatic write-offs still
                                        appealable, the items nobody has worked, payments with no remittance, the reports'
                                        ages. Each count carries the Worklist filter it was counted with (`go`), so the
                                        number on the page is the count line of the list a click opens.
    GET  /api/ema/blind                 what the site cannot check while a feed is behind, in dollars (one banner)
    GET  /api/ema/data                  the Data tab's EMA panels in one call
    GET  /api/ema/reconcile             the reconcile summary (what the run stored in meta ema:reconcile)
    GET  /api/ema/reports               EMA's reports on file: what each was read as, its scope, rows, the files it
                                        replaced, holds and the decisions on them (who and when), where it came from
    GET  /api/ema/ar?bucket=&page=      one page of the A/R rows of one bucket of the A/R accounted for
    GET  /api/ema/checks?cause=&month=&payer=&page=   one page of the checks EMA posted that no 835 carries
    GET  /api/ema/checks.csv, /api/ema/unposted.csv, /api/ema/credits.csv   (no patient names)
    GET  /api/ema/policy                the write-off patterns (payer group x code x modifier x reason) with the owner's
                                        decisions
    POST /api/ema/policy                {pattern, decision: pursue|accept, name}: decide a pattern (any signed-in person,
                                        as with the hide rules; who and when are kept)
    GET  /api/ema/policy/items?pattern=&page=   one page of a pattern's items

Every route needs the sign-in (api.require_auth); the one that changes something also needs the header a cross-site form
cannot set (api.require_xhr). Nothing here writes on a GET. The figures the run worked out on the whole store are read
from what it stored (meta), so the dashboard's copy on a serverless host, which holds part of the store, shows the same
numbers as the server. A decision on a serverless host goes through the journal (actions.apply, kind ema_policy).
"""
from __future__ import annotations

import inspect
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterator, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from . import runtime

router = APIRouter()
_security = HTTPBasic(auto_error=False)

MONEY_CATS = "denial,bundling,downcoding,underpayment"       # the Worklist's own scope (desk.js DESKS.worklist)
TODO = "open,in_progress"
STATES = ("work_now", "waiting", "done", "accepted", "expired", "cant_check")
STATE_WORDS = {"work_now": "Work now", "waiting": "Waiting", "done": "Done", "accepted": "Accepted", "expired": "Expired",
               "cant_check": "Can't be checked", "followup_due": "Follow-ups due", "todo": "Work now"}
# Today's tiles: what to do now (work now and the follow-ups due: the Worklist's default), the follow-ups due among them,
# what waits on the payer, and the four states that need nothing today
TODAY_STATES = ("todo", "followup_due", "waiting", "done", "accepted", "expired", "cant_check")
EMA_VALUES = ("written_off", "contractual", "paid_other", "on_ar", "ema_only", "worked", "unworked", "auto_written_off")
FEED_STALE_DAYS = 10            # no payment newer than this: the remittances are behind (insights.data_health's "feed")
AR_STALE_DAYS = 8               # EMA's A/R older than this is stale (amendment 10; the Upload page's checklist)
PRODUCTION_STALE_DAYS = 14      # EMA's Production report older than this is stale
PAGE = 50                       # rows per page of the lists here
# the causes of a check EMA posted that no 835 carries (ema_checks.classify_checks), in plain words, and what to ask for
CHECK_CAUSES = {
    "feed_gap": ("The remittance exists but never reached RemitWatch", "Ask TriZetto to send these remittances again."),
    "after_feed": ("Posted after the newest remittance on file", "The remittance feed is behind: get the newest TriZetto downloads into the folder."),
    "no_era": ("This payer sends no electronic remittance here", "Enroll this payer's electronic remittances (ERA) with TriZetto."),
    "no_era_va": ("VA community care sends no electronic remittance here", "Enroll VA's electronic remittances (ERA) with TriZetto."),
    "separate_payee": ("Paid to the surgery center, whose remittances go to another payee", "Nothing to ask for here: the surgery center's own remittances cover these."),
    "misnumbered": ("The same money under another check number", "Nothing to ask for: the remittance is on file under another number."),
    "damaged": ("The check number was damaged in a spreadsheet", "Correct the check number in EMA."),
    "before_feed": ("Posted before the first remittance on file", "Older than the remittance archive: nothing to ask for."),
    "other": ("No cause found", "Look the check up in EMA and on the payer's portal."),
}
MISSING_CAUSES = ("feed_gap", "after_feed", "no_era", "no_era_va")          # remittances someone can ask for
OUTCOME_WORDS = {"auto_written_off": "Written off by EMA's posting rule", "written_off_while_pending": "Written off while the payer was still deciding",
                 "lost_to_timely_filing": "Lost to late filing", "written_off_at_posting": "Written off the day it posted",
                 "contractual_at_posting": "Taken as contractual the day it posted", "written_off_later": "Written off later",
                 "contractual_later": "Taken as contractual later", "written_off_by_policy": "Written off by the owner's policy"}


# --------------------------------------------------------------------------------------------- the API's own helpers
def _api():
    from . import api
    return api


def _user(request: Request, credentials: Optional[HTTPBasicCredentials] = Depends(_security)) -> str:
    """The same sign-in as every other route."""
    return _api().require_auth(request, credentials)


def _xhr(request: Request) -> None:
    """The same guard against cross-site forms as every other route that changes something."""
    _api().require_xhr(request)


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    """The store (a server) or the dashboard's copy of it (a serverless host)."""
    c = _api().db()
    try:
        yield c
    finally:
        c.close()


def _table(c: sqlite3.Connection, name: str) -> bool:
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _reports_arrived(c: sqlite3.Connection) -> bool:
    """Whether any EMA report has reached the site (its catalog has a row: read, held, waiting or refused). The tables
    themselves exist on every store (ema_reports.ensure_schema), so they say nothing."""
    return _table(c, "ema_report_files") and c.execute("SELECT 1 FROM ema_report_files LIMIT 1").fetchone() is not None


def _cols(c: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in c.execute(f"PRAGMA table_info({table})")} if _table(c, table) else set()


def _meta(c: sqlite3.Connection, key: str, default: Any = None) -> Any:
    """A meta value the run stored (JSON), or the default when there is none. A value that is not JSON is a fault the
    page reports by its kind, never a silent default."""
    if not _table(c, "meta"):
        return default
    r = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    if not r or r[0] is None:
        return default
    try:
        return json.loads(r[0])
    except ValueError:
        return {"error": f"meta {key} is not readable"}


def _today() -> str:
    """The site's day, as the lists count it (SQL date('now') in api._filters): UTC."""
    return datetime.now(timezone.utc).date().isoformat()


def _days(a: Any, b: Any) -> int | None:
    try:
        return (date.fromisoformat(str(b)[:10]) - date.fromisoformat(str(a)[:10])).days
    except (TypeError, ValueError):
        return None


def _usd(cents: Any) -> str:
    c = int(cents or 0)
    return ("-" if c < 0 else "") + f"${abs(c) / 100:,.0f}"


def _n(n: Any, one: str, many: str | None = None) -> str:
    n = int(n or 0)
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


def _long(d: Any) -> str:
    try:
        x = date.fromisoformat(str(d)[:10])
    except (TypeError, ValueError):
        return str(d or "")
    return f"{x.strftime('%b')} {x.day}, {x.year}"


def _fault(exc: BaseException) -> str:
    """A failure is shown by its kind only: its own text can quote a row."""
    return type(exc).__name__


# ------------------------------------------------------------------------------- counting the way the lists count
def _unworked_since(c: sqlite3.Connection) -> str | None:
    """W3's data_through, as the run stored it (meta ema:unworked): the day the 'nobody has worked these' count is taken to."""
    u = _meta(c, "ema:unworked", None) or {}
    return u.get("data_through") if isinstance(u, dict) else None


def condition(name: str, value: str, c: sqlite3.Connection | None = None) -> tuple[str, list] | None:
    """The `state=` and `ema=` filters as a condition on a findings row: the rule the Today counts use when api._filters
    does not take them yet (needs_C2.md section 4 gives the same SQL to unit C1). None for an unknown value."""
    if name == "state":
        due = "(follow_up_on IS NOT NULL AND follow_up_on <= date('now'))"
        if value == "followup_due":
            return (f"work_state IN ('work_now','waiting') AND {due}", [])
        if value == "todo":
            return (f"(work_state = 'work_now' OR (work_state = 'waiting' AND {due}))", [])
        if value in STATES:
            return ("work_state = ?", [value])
        return None
    if name == "ema":
        simple = {"written_off": "COALESCE(written_off_cents,0) > 0", "contractual": "written_off_kind = 'contractual'",
                  "paid_other": "ema_outcome = 'paid_after_other'", "ema_only": "source = 'ema'",
                  "worked": "ema_worked_on IS NOT NULL", "auto_written_off": "ema_outcome = 'auto_written_off'",
                  "on_ar": "ema_link IN (SELECT link FROM ema_ar WHERE link IS NOT NULL)"}
        if value in simple:
            return (simple[value], [])
        if value == "unworked":
            through = _unworked_since(c) if c is not None else None
            if not through:
                return ("0", [])            # the run has not measured it: nothing is listed, and nothing is counted
            from . import ema_checks as ec
            sql, params = ec.unworked_sql(through)
            return (f"({sql})", list(params))
    return None


def _where(c: sqlite3.Connection, go: dict) -> tuple[str, list]:
    """The WHERE clause of the Worklist (desk.js: category = the four claim pages, live items) under a goWork filter,
    built by api._filters itself so the count equals the list. `state` and `ema` go to api._filters when it takes them,
    else they are added with condition()."""
    a = _api()
    takes = inspect.signature(a._filters).parameters
    kw = {"signal": go.get("signal") or None, "category": go.get("category") or MONEY_CATS, "payer": go.get("payer") or None,
          "year": go.get("year") or None, "status": go.get("status") or TODO, "severity": None, "actionable": None, "q": None,
          "provider": None, "code": go.get("code") or None,
          "deadline": go.get("deadline", "valid") or None, "handling": go.get("handling") or None, "extra": None,
          "patient_of": None, "kind": go.get("kind") or None, "live": True, "paid_days": 0, "min_dollars": 0}
    more, more_params = [], []
    for name in ("state", "ema"):
        val = go.get(name)
        if not val:
            continue
        if name in takes:
            kw[name] = val
            continue
        cond = condition(name, val, c)
        if cond is None:
            raise HTTPException(400, f"Unknown {name} filter.")
        more.append(cond[0])
        more_params += cond[1]
    w, params = a._filters(**{k: v for k, v in kw.items() if k in takes})
    if more:
        w = (w + " AND " if w else " WHERE ") + " AND ".join(f"({m})" for m in more)
        params = list(params) + more_params
    return w, list(params)


def count(go: dict) -> dict:
    """{n, cents} of the Worklist under this filter, as its count line shows it (api.findings: the routine-rate patterns
    counted by their claims in the date range), with the filter itself (`go`) for the page to open, and the cents of
    items whose amount is only an upper bound (EMA wrote off a line no rate prices: charge less paid; needs_B 1.6)."""
    a = _api()
    with _conn() as c:
        w, params = _where(c, go)
        r = c.execute(f"SELECT COUNT(*), COALESCE(SUM(shortfall_cents),0) FROM findings{w}", params).fetchone()
        upper = 0
        if r[0] and {"evidence", "written_off_cents"} <= _cols(c, "findings"):
            # only a line EMA wrote off can carry the bound, so the JSON is read for those rows alone
            upper = c.execute(f"""SELECT COALESCE(SUM(shortfall_cents),0) FROM findings{w}{' AND' if w else ' WHERE'}
                                  COALESCE(written_off_cents,0) > 0
                                  AND json_extract(evidence, '$.written_off_basis') = 'charge_less_paid'""", params).fetchone()[0]
    delta = 0
    if r[0]:
        delta, _each = a._pattern_cents(w, params, go.get("deadline", "valid") or None)
    return {"n": r[0], "cents": (r[1] or 0) + delta, "upper_cents": upper or 0, "go": go}


# ------------------------------------------------------------------------------------------------- the blind spot
def blind_spot(c: sqlite3.Connection) -> dict:
    """What the site cannot see while a feed is behind, since when, and in dollars (PLAN non-negotiable 3). The run's own
    figure (meta selfcheck:blind, unit G) is used when it is stored; until then: the remittances (no payment newer than
    10 days), EMA's A/R (older than 8 days) and Production report (older than 14), with the dollars the run stored:
    insurance payments EMA posted after the newest remittance that no 835 carries (meta ema:reconcile), and the items in
    Work now, any of which may have been paid or worked since."""
    stored = _meta(c, "selfcheck:blind", None)
    if isinstance(stored, dict) and "blind" in stored and "error" not in stored:
        return dict(stored, source="selfcheck")
    today = _today()
    feeds: list[dict] = []
    newest = None
    if _table(c, "transactions"):
        newest = c.execute("SELECT MAX(payment_date) FROM transactions WHERE COALESCE(superseded,0)=0").fetchone()[0]
    if newest:
        d = _days(newest, today)
        feeds.append({"feed": "835", "label": "Remittances (835)", "since": str(newest)[:10], "days": d,
                      "stale": d is not None and d > FEED_STALE_DAYS})
    fr = _freshness(c)
    for key, label, limit in (("ar", "EMA's A/R report", AR_STALE_DAYS), ("production", "EMA's Production report", PRODUCTION_STALE_DAYS)):
        as_of = fr.get(f"{key}_as_of")
        if as_of:
            d = _days(as_of, today)
            feeds.append({"feed": key, "label": label, "since": str(as_of)[:10], "days": d, "stale": d is not None and d > limit})
    stale = [f for f in feeds if f["stale"]]
    out: dict = {"blind": bool(stale), "feeds": feeds, "cents": 0, "lines": [], "source": "run", "today": today}
    if not stale:
        return out
    work = _work_now(c)
    lines = []
    cents = 0
    by = {f["feed"]: f for f in stale}
    if "835" in by:
        f = by["835"]
        rec = _meta(c, "ema:reconcile", {}) or {}
        after = (rec.get("since_feed_stopped") or {}) if isinstance(rec, dict) else {}
        if after.get("cents"):
            cents += int(after["cents"])
            lines.append(f"No remittance after {_long(f['since'])} ({_n(f['days'], 'day')}). Since then EMA posted {_usd(after['cents'])} "
                         f"of insurance payments on {_n(after.get('checks'), 'check')} that no remittance on file explains, so "
                         "RemitWatch cannot see what the payers paid, cut or denied on them.")
        else:
            cents += work["cents"]
            lines.append(f"No remittance after {_long(f['since'])} ({_n(f['days'], 'day')}). {_n(work['n'], 'item')} worth "
                         f"{_usd(work['cents'])} in Work now may have been paid since, and still show as open.")
    ema_stale = [by[k] for k in ("ar", "production") if k in by]
    if ema_stale:
        names = " and ".join(f"{f['label']} ({_n(f['days'], 'day')} old)" for f in ema_stale)
        oldest = min(f["since"] for f in ema_stale)
        if "835" not in by or not lines or "may have been paid" not in lines[-1]:
            cents += work["cents"]
        lines.append(f"{names[0].upper()}{names[1:]}: work done in EMA after {_long(oldest)} is not seen, so any of the "
                     f"{_n(work['n'], 'item')} worth {_usd(work['cents'])} in Work now may already be worked.")
    out.update(cents=cents, lines=lines, since=min(f["since"] for f in stale))
    return out


def _work_now(c: sqlite3.Connection) -> dict:
    if "work_state" not in _cols(c, "findings"):
        return {"n": 0, "cents": 0}
    r = c.execute(f"""SELECT COUNT(*), COALESCE(SUM(shortfall_cents),0) FROM findings WHERE work_state='work_now' AND COALESCE(stale,0)=0
                      AND category IN ({','.join('?' * 4)})""", MONEY_CATS.split(",")).fetchone()
    return {"n": r[0], "cents": r[1]}


def _freshness(c: sqlite3.Connection) -> dict:
    """The reports' as-of dates (ema_checks.freshness), or nothing before EMA's tables exist."""
    if not _table(c, "ema_report_files"):
        return {}
    from . import ema_checks as ec
    fr = ec.freshness(c)
    return {k: fr.get(k) for k in ("today", "ar_as_of", "ar_days", "production_as_of", "production_days", "newest_835", "first_835",
                                   "production_fresh", "ar_fresh")}


@router.get("/api/ema/blind")
def blind(user: str = Depends(_user)):
    with _conn() as c:
        return blind_spot(c)


# --------------------------------------------------------------------------------------------------------- Today
@router.get("/api/ema/today")
def today(user: str = Depends(_user)):
    """Today's counts beside the plan: the money items by state (Work now with the follow-ups due, which is the Worklist's
    default; the follow-ups due among them; Waiting; and the four that need nothing today), EMA's automatic write-offs
    still appealable, the items nobody has worked, the payments EMA posted with no remittance last month, the reports'
    ages and what the run noted. Each count carries the Worklist filter it was counted with (`go`): a click opens exactly
    that list."""
    a = _api()
    with _conn() as c:
        fcols = _cols(c, "findings")
        ready = "work_state" in fcols
        out: dict = {"today": _today(), "ready": ready, "states": {}, "blind": blind_spot(c)}
        rec = _meta(c, "ema:reconcile", {}) or {}
        # last month's payments with no remittance, as the run judged them (ema_checks data health remits_missing)
        out["remits_missing"] = rec.get("remits_missing") if isinstance(rec, dict) and _reports_arrived(c) else None
        out["fresh"] = _freshness(c)
        wiring = _meta(c, "ema:wiring", {}) or {}
        out["wiring_errors"] = len(wiring.get("errors") or []) if isinstance(wiring, dict) else 0
        has_outcome = "ema_outcome" in fcols
        through = _unworked_since(c)
    token = a._WINDOWS_MEMO.set({})
    try:
        if ready:
            for key in TODAY_STATES:
                out["states"][key] = dict(count({"state": key, "status": "all", "deadline": ""}), label=STATE_WORDS[key])
        if has_outcome:
            out["auto_written_off"] = count({"ema": "auto_written_off", "status": TODO, "deadline": "valid", "sort": "shortfall"})
        if has_outcome and through:
            u = count({"ema": "unworked", "status": "all", "deadline": ""})
            out["unworked"] = dict(u, data_through=through)
        out["other"] = {"post": count({"signal": "REMIT_NOT_POSTED", "category": "fees", "status": TODO, "deadline": ""}),
                        "refund": count({"signal": "CREDIT_BALANCE", "category": "refund", "status": TODO, "deadline": ""})}
    finally:
        a._WINDOWS_MEMO.reset(token)
    if isinstance(rec, dict) and rec.get("since_feed_stopped"):
        out["since_feed_stopped"] = rec["since_feed_stopped"]
    return out


# -------------------------------------------------------------------------------------------------- the Data tab
def _catalog(c: sqlite3.Connection) -> dict:
    """ema_reports.catalog with what the Data tab shows of each file: the files it replaced, its holds and the decision
    on them (who and when), where it came from; and the files the site did not read (not one of EMA's reports or
    exports, or too big). Never a file's origin path or a key."""
    from . import ema_reports as er
    cat = er.catalog(c)
    by_id = {f["file_id"]: f for f in cat["files"]}
    for f in cat["files"]:
        f["replaced"] = [{"file_id": g["file_id"], "source_name": g.get("source_name"), "status": g.get("status")}
                         for g in cat["files"] if g.get("replaced_by") == f["file_id"]]
        f["from"] = {"upload": "Uploaded here", "dropbox": "From the Dropbox folder", "mirror": "From the Dropbox folder",
                     "cli": "Read on the server", "test": "Read on the server"}.get(f.get("via") or "", f.get("via") or "")
        acct = f.get("account") if isinstance(f.get("account"), dict) else {}
        f["aging_window"] = acct.get("aging_window")
        f.pop("columns", None)
        f.pop("unused", None)
    fcols = _cols(c, "files")
    want = [x for x in ("kind", "ingested_at", "status", "source_name") if x in fcols]
    not_read = []
    if "kind" in fcols:
        cond = "kind IN ('ema:unknown','ema:too_large')" + (" AND COALESCE(status,'not_read') = 'not_read'" if "status" in fcols else "")
        not_read = [dict(zip(want, r)) for r in c.execute(f"SELECT {', '.join(want)} FROM files WHERE {cond} ORDER BY ingested_at DESC LIMIT 50")]
    cat["not_read"] = not_read
    cat["by_status"] = {}
    for f in by_id.values():
        cat["by_status"][f["status"]] = cat["by_status"].get(f["status"], 0) + 1
    return cat


@router.get("/api/ema/reports")
def reports(user: str = Depends(_user)):
    with _conn() as c:
        if not _table(c, "ema_report_files"):
            return {"files": [], "held": [], "waiting": 0, "not_read": [], "by_status": {}}
        return _catalog(c)


def _reconcile(c: sqlite3.Connection) -> dict:
    """What the run stored (meta ema:reconcile); on a server with none stored yet, worked out now without writing."""
    rec = _meta(c, "ema:reconcile", None)
    if rec:
        return dict(rec, stored=True)
    if runtime.serverless() or not _table(c, "ema_checks"):
        return {}
    from . import ema_checks as ec
    return dict(ec.reconcile_summary(c, save=False), stored=False)


@router.get("/api/ema/reconcile")
def reconcile(user: str = Depends(_user)):
    with _conn() as c:
        return _reconcile(c)


def _c1_rows(c: sqlite3.Connection, rec: dict) -> list[dict]:
    """The checks no 835 carries, by payer group, month and cause, for the remittances someone can ask for (MISSING_CAUSES):
    the rows of the re-send request table, each with what to ask for."""
    if not _table(c, "ema_checks"):
        return []
    out = []
    for grp, month, cause, n, cents in c.execute(
            f"""SELECT COALESCE(payer_group,''), substr(first_posted,1,7), cause, COUNT(*), COALESCE(SUM(ins_paid),0) FROM ema_checks
                WHERE match = 'none' AND COALESCE(ins_paid,0) <> 0 AND cause IN ({','.join('?' * len(MISSING_CAUSES))})
                GROUP BY 1, 2, 3 ORDER BY 2 DESC, 5 DESC""", MISSING_CAUSES):
        why, ask = CHECK_CAUSES.get(cause, ("", ""))
        out.append({"payer_group": grp or "Unknown", "month": month, "cause": cause, "why": why, "ask": ask, "checks": n, "cents": cents})
    return out


def _upper_bound(c: sqlite3.Connection) -> dict:
    """Open items whose amount is only an upper bound: EMA wrote off a line no rate prices, so the item is sized at the
    charge less what was paid (ema_checks C2, written_off_basis charge_less_paid)."""
    if "evidence" not in _cols(c, "findings") or "written_off_cents" not in _cols(c, "findings"):
        return {"n": 0, "cents": 0}
    r = c.execute("""SELECT COUNT(*), COALESCE(SUM(shortfall_cents),0) FROM findings WHERE COALESCE(stale,0)=0 AND status IN ('open','in_progress')
                     AND COALESCE(written_off_cents,0) > 0 AND json_extract(evidence, '$.written_off_basis') = 'charge_less_paid'""").fetchone()
    return {"n": r[0], "cents": r[1]}


@router.get("/api/ema/data")
def data(user: str = Depends(_user)):
    """The Data tab's EMA panels: the reports on file, freshness, the reconcile summary (payments with no remittance,
    835 payments EMA never posted, credits, the A/R accounted for), the re-send requests, what the run noted, and the
    write-off patterns waiting for the owner. A panel whose figures cannot be read says so by the fault's kind."""
    with _conn() as c:
        if not _reports_arrived(c):
            return {"ready": False}
        out: dict = {"ready": True, "today": _today(), "fresh": _freshness(c), "blind": blind_spot(c)}
        for name, fn in (("catalog", _catalog), ("reconcile", _reconcile), ("resend", lambda cc: _c1_rows(cc, {})),
                         ("upper_bound", _upper_bound)):
            try:
                out[name] = fn(c)
            except (sqlite3.Error, ValueError, TypeError, KeyError) as exc:
                out[name] = {"error": _fault(exc)}
        wiring = _meta(c, "ema:wiring", {}) or {}
        out["wiring"] = wiring if isinstance(wiring, dict) else {}
        pol = _meta(c, "ema:policy_summary", {}) or {}
        decided = _meta(c, "ema:writeoff_policy", {}) or {}
        pats = pol.get("patterns") or [] if isinstance(pol, dict) else []
        out["policy"] = {"patterns": len(pats), "cents": sum(int(p.get("cents") or 0) for p in pats),
                         "appealable_cents": sum(int(p.get("appealable_cents") or 0) for p in pats),
                         "accepted": sum(1 for v in (decided.values() if isinstance(decided, dict) else []) if (v or {}).get("decision") == "accept"),
                         "at": pol.get("at") if isinstance(pol, dict) else None}
        out["cause_words"] = {k: {"why": v[0], "ask": v[1]} for k, v in CHECK_CAUSES.items()}
        return out


@router.get("/api/ema/ar")
def ar(bucket: str = Query(..., max_length=40), page: int = Query(1, ge=1, le=100000), user: str = Depends(_user)):
    """One page of the A/R rows in one bucket of the A/R accounted for (ema_checks.ar_list: the rows the run's count of
    that bucket counted). Never writes."""
    from . import ema_checks as ec
    from . import ema_reports as er
    if bucket not in ec.AR_BUCKETS:
        raise HTTPException(400, "There is no such bucket.")
    with _conn() as c:
        if not _table(c, "ema_ar"):
            return {"bucket": bucket, "label": ec.AR_BUCKET_LABELS[bucket], "page": page, "size": 100, "count": 0, "cents": 0, "rows": [], "pending": 0}
        out = ec.ar_list(c, bucket, page)
        out["claim_url"] = er.claim_url_template(c)
        return out


@router.get("/api/ema/checks")
def checks(cause: Optional[str] = Query(None, max_length=40), month: Optional[str] = Query(None, max_length=7),
           payer: Optional[str] = Query(None, max_length=200), page: int = Query(1, ge=1, le=100000), user: str = Depends(_user)):
    """One page of the checks EMA posted that no 835 carries (C1), newest first: check number, first posted, EMA's payer,
    amount, cause. What TriZetto or the payer needs to send the remittances again. No patient data."""
    if cause and cause not in CHECK_CAUSES:
        raise HTTPException(400, "There is no such cause.")
    if month and not re.fullmatch(r"\d{4}-\d{2}", month):
        raise HTTPException(400, "The month is YYYY-MM.")
    with _conn() as c:
        if not _table(c, "ema_checks"):
            return {"count": 0, "cents": 0, "rows": [], "page": page, "size": PAGE}
        where, args = ["match = 'none'", "COALESCE(ins_paid,0) <> 0"], []
        if cause:
            where.append("cause = ?")
            args.append(cause)
        if month:
            where.append("substr(first_posted,1,7) = ?")
            args.append(month)
        if payer:
            where.append("COALESCE(payer_group,'') = ?")
            args.append("" if payer == "Unknown" else payer)
        w = " AND ".join(where)
        n, cents = c.execute(f"SELECT COUNT(*), COALESCE(SUM(ins_paid),0) FROM ema_checks WHERE {w}", args).fetchone()
        rows = [{"check_number": r[0], "first_posted": r[1], "payer": r[2], "payer_group": r[3] or "Unknown", "cents": r[4] or 0,
                 "cause": r[5], "claims": r[6] or 0}
                for r in c.execute(f"""SELECT check_no, first_posted, payer, payer_group, ins_paid, cause, links FROM ema_checks WHERE {w}
                                       ORDER BY first_posted DESC, check_no LIMIT ? OFFSET ?""", args + [PAGE, (page - 1) * PAGE])]
        return {"count": n, "cents": cents, "rows": rows, "page": page, "size": PAGE,
                "why": CHECK_CAUSES.get(cause or "", ("", ""))[0], "ask": CHECK_CAUSES.get(cause or "", ("", ""))[1]}


def _csv(name: str, gen) -> StreamingResponse:
    """A CSV export: streamed, never kept by a browser or a proxy, and with any cell that could start a spreadsheet
    formula quoted (api._csv_cell)."""
    import csv
    import io
    cell = _api()._csv_cell

    def lines():
        buf = io.StringIO()
        w = csv.writer(buf)
        for row in gen:
            w.writerow([cell(v) for v in row])
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate(0)
    return StreamingResponse(lines(), media_type="text/csv; charset=utf-8",
                             headers={"Cache-Control": "no-store", "Content-Disposition": f'attachment; filename="{name}"'})


def _stream(fn, *args, **kw):
    """Rows from an ema_checks CSV function on a connection that stays open while the answer streams."""
    c = _api().db()
    try:
        yield from fn(c, *args, **kw)
    finally:
        c.close()


@router.get("/api/ema/checks.csv")
def checks_csv(cause: Optional[str] = Query(None, max_length=40), date_from: Optional[str] = Query(None, alias="from", max_length=10),
               date_to: Optional[str] = Query(None, alias="to", max_length=10), user: str = Depends(_user)):
    from . import ema_checks as ec
    if cause and cause not in CHECK_CAUSES:
        raise HTTPException(400, "There is no such cause.")
    for d in (date_from, date_to):
        if d and not re.fullmatch(r"\d{4}-\d{2}(-\d{2})?", d):
            raise HTTPException(400, "Dates are YYYY-MM-DD.")
    with _conn() as c:
        if not _table(c, "ema_checks"):
            raise HTTPException(404, "No EMA Production report has been read yet.")
    return _csv("remitwatch-checks-without-remittance.csv", _stream(ec.checks_csv, cause=cause or None, date_from=date_from, date_to=date_to))


@router.get("/api/ema/unposted.csv")
def unposted_csv(user: str = Depends(_user)):
    from . import ema_checks as ec
    with _conn() as c:
        if not _table(c, "ema_checks"):
            raise HTTPException(404, "No EMA Production report has been read yet.")
    return _csv("remitwatch-payments-not-posted.csv", _stream(ec.unposted_csv))


@router.get("/api/ema/credits.csv")
def credits_csv(user: str = Depends(_user)):
    from . import ema_checks as ec
    with _conn() as c:
        if not _table(c, "ema_ar"):
            raise HTTPException(404, "No EMA A/R report has been read yet.")
    return _csv("remitwatch-credits.csv", _stream(ec.credits_csv))


# ------------------------------------------------------------------------------- the owner's write-off policy (O11)
def _write_off_outcomes() -> tuple:
    from . import ema_checks as ec
    return tuple(ec.WRITE_OFF_OUTCOMES)


def _pattern_where(c: sqlite3.Connection) -> tuple[str, list]:
    """The items of the write-off patterns: live items EMA wrote off (a write-off outcome) whose pattern the run recorded
    (evidence.writeoff_pattern, detect.run_ema_writeoffs)."""
    outs = _write_off_outcomes()
    return (f"COALESCE(stale,0)=0 AND ema_outcome IN ({','.join('?' * len(outs))}) "
            "AND json_extract(evidence, '$.writeoff_pattern') IS NOT NULL", list(outs))


def split_pattern(pattern: str) -> dict:
    parts = (str(pattern or "").split("|") + ["", "", "", ""])[:4]
    return {"payer_group": parts[0], "code": parts[1], "modifier": parts[2], "reason": parts[3]}


def _reason_text(reason: str) -> str:
    from .codes import CARC
    code = reason.split("-", 1)[-1] if reason else ""
    return CARC.get(code, "") if code else ""


def policy_table(c: sqlite3.Connection) -> dict:
    """Every write-off pattern with its items counted now (so a count equals the list it opens), the archive's later-paid
    share and the run's 'at posting' figures from what the run stored (meta ema:policy_summary), and the decision (meta
    ema:writeoff_policy: pursue by default, accept by a person, who and when). A decision saved after the run is marked
    `pending`: the next run moves the pattern's items (accepted ones leave the work queue and stay findable)."""
    stored = _meta(c, "ema:policy_summary", {}) or {}
    stored = stored if isinstance(stored, dict) else {}
    decided = _meta(c, "ema:writeoff_policy", {}) or {}
    decided = decided if isinstance(decided, dict) else {}
    run_at = stored.get("at")
    by_key = {p.get("pattern"): p for p in stored.get("patterns") or [] if isinstance(p, dict)}
    pats: dict[str, dict] = {}
    if {"ema_outcome", "evidence"} <= _cols(c, "findings"):
        w, params = _pattern_where(c)
        for (key, n, cents, appealable, appealable_cents, auto, at_posting, proven, by_policy, open_n, upper) in c.execute(
                f"""SELECT json_extract(evidence, '$.writeoff_pattern') k, COUNT(*), COALESCE(SUM(shortfall_cents),0),
                           SUM(status IN ('open','in_progress') AND appeal_deadline >= date('now')),
                           COALESCE(SUM(CASE WHEN status IN ('open','in_progress') AND appeal_deadline >= date('now') THEN shortfall_cents END),0),
                           SUM(ema_outcome = 'auto_written_off'), SUM(written_off_when = 'at_posting'),
                           SUM(ema_writeoff_evidence = 'rule_proven'), SUM(ema_outcome = 'written_off_by_policy'),
                           SUM(status IN ('open','in_progress')),
                           COALESCE(SUM(CASE WHEN json_extract(evidence, '$.written_off_basis') = 'charge_less_paid' THEN shortfall_cents END),0)
                    FROM findings WHERE {w} GROUP BY k""", params):
            pats[key] = {"pattern": key, "items": n, "cents": cents, "appealable": appealable or 0, "appealable_cents": appealable_cents or 0,
                         "auto": auto or 0, "at_posting": at_posting or 0, "rule_proven": proven or 0, "by_policy": by_policy or 0,
                         "open": open_n or 0, "upper_cents": upper or 0}
    for key, d in {}.items():          # a pattern decided earlier that has no item now stays listed, so it can be changed back
        if key not in pats and isinstance(d, dict):
            pats[key] = {"pattern": key, "items": 0, "cents": 0, "appealable": 0, "appealable_cents": 0, "auto": 0, "at_posting": 0,
                         "rule_proven": 0, "by_policy": 0, "open": 0, "upper_cents": 0}
    out = []
    for key, p in pats.items():
        d = decided.get(key) if isinstance(decided.get(key), dict) else {}
        s = by_key.get(key) or {}
        p.update(split_pattern(key))
        p["reason_text"] = _reason_text(p["reason"])
        p["later_paid_share"] = s.get("later_paid_share")
        p["decision"] = d.get("decision") or "pursue"
        p["decided_by"], p["decided_at"] = d.get("by"), d.get("at")
        p["pending"] = bool(d.get("at")) and (not run_at or str(d.get("at")) > str(run_at))
        out.append(p)
    out.sort(key=lambda p: (-p["appealable_cents"], -p["cents"], p["pattern"]))
    return {"patterns": out, "run_at": run_at, "today": _today(),
            "totals": {"items": sum(p["items"] for p in out), "cents": sum(p["cents"] for p in out),
                       "appealable": sum(p["appealable"] for p in out), "appealable_cents": sum(p["appealable_cents"] for p in out),
                       "accepted": sum(1 for p in out if p["decision"] == "accept")}}


@router.get("/api/ema/policy")
def policy(user: str = Depends(_user)):
    with _conn() as c:
        return policy_table(c)


@router.get("/api/ema/policy/items")
def policy_items(pattern: str = Query(..., max_length=200), page: int = Query(1, ge=1, le=100000), user: str = Depends(_user)):
    """One page of a pattern's items, most dollars first: what the pattern's count counts. No patient names."""
    with _conn() as c:
        if not {"ema_outcome", "evidence"} <= _cols(c, "findings"):
            return {"pattern": pattern, "count": 0, "cents": 0, "rows": [], "page": page, "size": PAGE}
        w, params = _pattern_where(c)
        w += " AND json_extract(evidence, '$.writeoff_pattern') = ?"
        params.append(pattern)
        n, cents = c.execute(f"SELECT COUNT(*), COALESCE(SUM(shortfall_cents),0) FROM findings WHERE {w}", params).fetchone()
        cols = [x for x in ("finding_id", "signal", "service_date", "procedure_code", "payer_group", "shortfall_cents", "appeal_deadline",
                            "status", "work_state", "ema_outcome", "written_off_cents", "written_off_on", "carc", "patient_control_number")
                if x in _cols(c, "findings")]
        rows = [dict(zip(cols, r)) for r in c.execute(
            f"SELECT {', '.join(cols)} FROM findings WHERE {w} ORDER BY shortfall_cents DESC, appeal_deadline, finding_id LIMIT ? OFFSET ?",
            params + [PAGE, (page - 1) * PAGE])]
        for r in rows:
            r["outcome_text"] = OUTCOME_WORDS.get(r.get("ema_outcome") or "", "")
            r["state_text"] = STATE_WORDS.get(r.get("work_state") or "", "")
        return {"pattern": pattern, "count": n, "cents": cents, "rows": rows, "page": page, "size": PAGE}


def _known_pattern(c: sqlite3.Connection, pattern: str) -> bool:
    tab = policy_table(c)
    return any(p["pattern"] == pattern for p in tab["patterns"])


@router.post("/api/ema/policy", dependencies=[Depends(_xhr)])
def policy_set(body: dict = Body(default={}), user: str = Depends(_user)):
    """Pursue or accept one write-off pattern. Any signed-in person may, as with the hide rules; who and when are kept
    (the name typed, beside the sign-in). Accepting takes the pattern's items off the work queue at the next run, as
    written off by the owner's policy; they stay findable, and pursue brings them back."""
    pattern = str(body.get("pattern") or "").strip()
    decision = str(body.get("decision") or "").strip()
    if decision not in ("pursue", "accept"):
        raise HTTPException(400, "The decision is pursue or accept.")
    if not pattern or len(pattern) > 200 or pattern.count("|") != 3:
        raise HTTPException(400, "That is not a write-off pattern.")
    name = " ".join(str(body.get("name") or "").split())[:40]
    who = f"{name} ({user})" if name and name != user else user
    with _conn() as c:
        if not _known_pattern(c, pattern):
            raise HTTPException(404, "No item has that write-off pattern.")
    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if runtime.serverless():
        from .cloud import SERVING_COPY
        try:
            SERVING_COPY.record({"kind": "ema_policy", "pattern": pattern, "decision": decision, "actor": who[:80], "at": at})
        except RuntimeError:
            raise HTTPException(503, "This site's storage is not set up yet: open /setup.") from None
        return {"ok": True, "pattern": pattern, "decision": decision, "by": who[:80], "at": at, "applies": "next run"}
    from . import ema_checks as ec
    with _conn() as c:
        rec = ec.set_policy(c, pattern, decision, who)
    return {"ok": True, "pattern": pattern, "decision": rec["decision"], "by": rec["by"], "at": rec["at"], "applies": "next run"}
