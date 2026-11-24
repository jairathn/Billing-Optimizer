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
    GET  /api/ema/policy                the owner's write-off policy table: the write-off patterns (payer group x code x
                                        modifier x reason), the codes a payer almost never pays (group|code||payer_policy,
                                        out of Work now until the owner decides) and the reasons EMA posts as contractual
                                        (group|||reason, PLAN decision 3), with the decisions
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
from datetime import date, datetime, timezone
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
# remittances someone can ask for: a gap in the feed, a payer that sends none. Checks posted after the newest remittance
# (after_feed) are not asked for one by one: the feed is behind, and they match once the newest downloads arrive.
MISSING_CAUSES = ("feed_gap", "no_era", "no_era_va")
# what each dollar figure rests on, said on hover beside it (PLAN decision 5); "estimate" where the figure is priced, not
# paid or posted
BASIS = {
    "shortfall": "Estimate: what the payer kept short, against its usual rate for the code (from the remittances); no "
                 "contract is on file to price it exactly.",
    "auto_written_off": "EMA's postings: written off the day the payer's answer posted, nobody worked it, and its safe date to "
                        "appeal has not passed (the date bulk appeal uses). Dollars: an estimate against the payer's usual rate.",
    "unworked": "EMA's postings and A/R, and this site's own record: no work on the item in the 21 days to the date shown. "
                "Dollars: an estimate against the payer's usual rate.",
    "post": "The remittance (835): the payer paid it and EMA has no posting for it.",
    "refund": "EMA's Production report: a line paid beyond what it owed.",
    "upper": "An upper bound: the charge less what was paid, as EMA wrote it off. No rate prices the line, so it is in no total.",
    "c1": "EMA's postings: insurance payments posted on checks no remittance on file carries.",
    "c4": "The remittances (835): payments the payer sent that EMA has no posting for.",
    "c8": "EMA's A/R report: lines with a balance below zero.",
    "w4": "EMA's A/R report, each insurance line in one place.",
    "policy": "Estimate: the shortfall against the payer's usual rate for the code, from the remittances.",
    "payer_policy": "Estimate: what the set-aside denials would bring at the payer's usual rate; the payer paid the code on few "
                    "of the lines it answered (shown beside it).",
    "contractual": "EMA's postings: what EMA wrote off as contractual for this reason. Not in any total.",
    "blind_835": "EMA's postings: insurance payments posted on checks no remittance on file explains.",
    "blind_work": "Estimate: the shortfall of the items in Work now, against the payer's usual rate.",
}
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
    counted by their claims in the date range), with the filter itself (`go`) for the page to open. Items EMA wrote off on
    a line no rate prices have no shortfall, so they are counted but add nothing to the dollars; `upper_n` and
    `upper_cents` say how many there are and what EMA wrote off on them, capped at the charge less what was paid: an upper
    bound, never added to a total (ema_checks C2 written_off_basis charge_less_paid; needs_B 1.6)."""
    a = _api()
    with _conn() as c:
        w, params = _where(c, go)
        r = c.execute(f"SELECT COUNT(*), COALESCE(SUM(shortfall_cents),0) FROM findings{w}", params).fetchone()
        upper = (0, 0)
        if r[0] and {"evidence", "written_off_cents"} <= _cols(c, "findings"):
            # only a line EMA wrote off can carry the bound, so the JSON is read for those rows alone
            upper = c.execute(f"""SELECT COUNT(*), COALESCE(SUM(written_off_cents),0) FROM findings{w}{' AND' if w else ' WHERE'}
                                  COALESCE(written_off_cents,0) > 0
                                  AND json_extract(evidence, '$.written_off_basis') = 'charge_less_paid'""", params).fetchone()
    delta = 0
    if r[0]:
        delta, _each = a._pattern_cents(w, params, go.get("deadline", "valid") or None)
    return {"n": r[0], "cents": (r[1] or 0) + delta, "upper_n": upper[0] or 0, "upper_cents": upper[1] or 0, "go": go}


# ------------------------------------------------------------------------------------------------- the blind spot
TODO_GO = {"state": "todo", "status": "all", "deadline": ""}      # Today's Work now tile (the Worklist's default list)


def blind_spot(c: sqlite3.Connection, work: dict | None = None) -> dict:
    """What the site cannot see while a feed is behind, since when, and in dollars (PLAN non-negotiable 3). The run's own
    figure (meta selfcheck:blind, unit G) is used when it is stored; until then: the remittances (no payment newer than
    10 days), EMA's A/R (older than 8 days) and Production report (older than 14), with the dollars the run stored:
    insurance payments EMA posted after the newest remittance that no 835 carries (meta ema:reconcile), and the items in
    Work now (the Work now tile's own count, `work` when the caller has it), any of which may have been paid or worked
    since. Each part (`parts`) carries its own dollars, its words and what they rest on; `cents` is their sum."""
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
    out: dict = {"blind": bool(stale), "feeds": feeds, "cents": 0, "lines": [], "parts": [], "source": "run", "today": today}
    if not stale:
        return out
    if work is None:
        work = _work_now(c)
    lines, parts = [], []
    by = {f["feed"]: f for f in stale}
    work_said = False
    if "835" in by:
        f = by["835"]
        rec = _meta(c, "ema:reconcile", {}) or {}
        after = (rec.get("since_feed_stopped") or {}) if isinstance(rec, dict) else {}
        if after.get("cents"):
            parts.append({"feed": "835", "cents": int(after["cents"]), "words": "of insurance payments no remittance explains",
                          "basis": BASIS["blind_835"]})
            lines.append(f"No remittance after {_long(f['since'])} ({_n(f['days'], 'day')}). Since then EMA posted {_usd(after['cents'])} "
                         f"of insurance payments on {_n(after.get('checks'), 'check')} that no remittance on file explains, so "
                         "RemitWatch cannot see what the payers paid, cut or denied on them.")
        else:
            work_said = True
            parts.append({"feed": "835", "cents": int(work["cents"] or 0), "words": "in Work now that may have been paid",
                          "basis": BASIS["blind_work"]})
            lines.append(f"No remittance after {_long(f['since'])} ({_n(f['days'], 'day')}). The {_n(work['n'], 'item')} worth "
                         f"{_usd(work['cents'])} in Work now may have been paid since, and still show as open.")
    ema_stale = [by[k] for k in ("ar", "production") if k in by]
    if ema_stale:
        names = " and ".join(f"{f['label']} ({_n(f['days'], 'day')} old)" for f in ema_stale)
        oldest = min(f["since"] for f in ema_stale)
        if not work_said:
            parts.append({"feed": "ema", "cents": int(work["cents"] or 0), "words": "in Work now that may already be worked",
                          "basis": BASIS["blind_work"]})
        lines.append(f"{names[0].upper()}{names[1:]}: work done in EMA after {_long(oldest)} is not seen, so any of the "
                     f"{_n(work['n'], 'item')} worth {_usd(work['cents'])} in Work now may already be worked.")
    out.update(cents=sum(p["cents"] for p in parts), lines=lines, parts=parts, since=min(f["since"] for f in stale))
    return out


def _work_now(c: sqlite3.Connection) -> dict:
    """The Work now tile's own count (work now and the follow-ups due, as the Worklist lists them), so the banner beside it
    says the same number."""
    if "work_state" not in _cols(c, "findings"):
        return {"n": 0, "cents": 0}
    return count(dict(TODO_GO))


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
# the money that is not claim work: 835 payments EMA never posted (to post) and credits (to refund). Each count opens
# its own list on the Data tab, by this very filter (/api/findings), so the number is the list's count line
OTHER_GO = {"post": {"signal": "REMIT_NOT_POSTED", "category": "fees", "status": TODO, "deadline": ""},
            "refund": {"signal": "CREDIT_BALANCE", "category": "refund", "status": TODO, "deadline": ""}}


def _other_counts() -> dict:
    return {k: count(dict(go)) for k, go in OTHER_GO.items()}


def _last_run_at(c: sqlite3.Connection) -> str | None:
    """When the newest run that read EMA's reports finished its detection (meta ema:wiring at)."""
    w = _meta(c, "ema:wiring", {}) or {}
    return w.get("at") if isinstance(w, dict) else None


def _after(a: Any, b: Any) -> bool:
    """Whether time a is later than time b (ISO times; a missing b: yes)."""
    if not b:
        return True
    try:
        return datetime.fromisoformat(str(a)) > datetime.fromisoformat(str(b))
    except (TypeError, ValueError):
        return str(a or "") > str(b)


def _decision_pending(row: dict, run_at: str | None) -> bool:
    """A held report someone chose to apply after the newest run: the next run reads it. One chosen before the newest
    run and still held was not read by it (too slow, or held again): it needs a person again."""
    return row.get("status") == "held" and row.get("decision") == "apply" and bool(row.get("decided_by")) and \
        _after(row.get("decided_at"), run_at)


@router.get("/api/ema/today")
def today(user: str = Depends(_user)):
    """Today's counts beside the plan: the money items by state (Work now with the follow-ups due, which is the Worklist's
    default; the follow-ups due among them; Waiting; and the four that need nothing today), EMA's automatic write-offs
    still appealable, the items nobody has worked (inside Work now), the payments to post and credits to refund, the
    patterns that wait for the owner's decision, the payments EMA posted with no remittance last month, the reports'
    ages and what the run noted. Each count carries the Worklist filter it was counted with (`go`): a click opens exactly
    that list. `basis` says what each figure's dollars rest on (PLAN decision 5)."""
    a = _api()
    with _conn() as c:
        fcols = _cols(c, "findings")
        ready = "work_state" in fcols
        out: dict = {"today": _today(), "ready": ready, "states": {}, "basis": dict(BASIS)}
        rec = _meta(c, "ema:reconcile", {}) or {}
        # last month's payments with no remittance, as the run judged them (ema_checks data health remits_missing)
        out["remits_missing"] = rec.get("remits_missing") if isinstance(rec, dict) and _reports_arrived(c) else None
        out["fresh"] = _freshness(c)
        # reports that wait (a raw CSV this site cannot read, a second Production file in one run) or need a decision, with
        # the catalog's own words for what to do. A held report a person chose to apply since the newest run waits for the
        # next run, not for a decision
        out["reports_waiting"], out["reports_held"], out["reports_pending"] = [], 0, 0
        if _reports_arrived(c):
            run_at = _last_run_at(c)
            for rep, name, status, reason, decision, by, at in c.execute(
                    """SELECT report, source_name, status, status_reason, decision, decided_by, decided_at FROM ema_report_files
                       WHERE status IN ('waiting','reread','held') ORDER BY received_at DESC"""):
                if status == "held":
                    if _decision_pending({"status": status, "decision": decision, "decided_by": by, "decided_at": at}, run_at):
                        out["reports_pending"] += 1
                    else:
                        out["reports_held"] += 1
                elif len(out["reports_waiting"]) < 10:
                    out["reports_waiting"].append({"report": rep, "source_name": name, "status": status, "reason": reason})
        wiring = _meta(c, "ema:wiring", {}) or {}
        out["wiring_errors"] = len(wiring.get("errors") or []) if isinstance(wiring, dict) else 0
        has_outcome = "ema_outcome" in fcols
        through = _unworked_since(c)
        try:
            out["decide"] = decide_totals(c)
        except (sqlite3.Error, ValueError, TypeError, KeyError):
            out["decide"] = None
    token = a._WINDOWS_MEMO.set({})
    try:
        if ready:
            for key in TODAY_STATES:
                out["states"][key] = dict(count({"state": key, "status": "all", "deadline": ""}), label=STATE_WORDS[key])
        if has_outcome:
            out["auto_written_off"] = count({"ema": "auto_written_off", "status": TODO, "deadline": "valid", "sort": "shortfall"})
        if has_outcome and through:
            # inside Work now: an item in Done, Expired or Accepted is not work, whatever EMA shows on it (a store before
            # the states has no Work now to count inside)
            go = {"state": "todo", "ema": "unworked", "status": "all", "deadline": ""} if ready else \
                {"ema": "unworked", "status": "all", "deadline": ""}
            out["unworked"] = dict(count(go), data_through=through)
        out["other"] = _other_counts()
        with _conn() as c:
            out["blind"] = blind_spot(c, work=out["states"].get("todo"))
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
    # a held report someone chose to apply after the newest run waits for the next run; one chosen before it and still
    # held was not read by that run and needs a person again (the page offers Apply and Discard again)
    run_at = _last_run_at(c)
    cat["run_at"] = run_at
    for f in cat["files"]:
        f["decision_pending"] = _decision_pending(f, run_at)
    cat["held"] = [f for f in cat["files"] if f.get("status") == "held"]
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


# a check EMA posted with no payer name is grouped as Unknown (ema_reports' grouper names it so; an empty group is the same)
_GROUP_SQL = "COALESCE(NULLIF(payer_group,''),'Unknown')"


def _c1_rows(c: sqlite3.Connection, rec: dict) -> list[dict]:
    """The checks no 835 carries, by payer group, month and cause, for the remittances someone can ask for (MISSING_CAUSES):
    the rows of the re-send request table, each with what to ask for."""
    if not _table(c, "ema_checks"):
        return []
    out = []
    for grp, month, cause, n, cents in c.execute(
            f"""SELECT {_GROUP_SQL}, substr(first_posted,1,7), cause, COUNT(*), COALESCE(SUM(ins_paid),0) FROM ema_checks
                WHERE match = 'none' AND COALESCE(ins_paid,0) <> 0 AND cause IN ({','.join('?' * len(MISSING_CAUSES))})
                GROUP BY 1, 2, 3 ORDER BY 2 DESC, 5 DESC""", MISSING_CAUSES):
        why, ask = CHECK_CAUSES.get(cause, ("", ""))
        out.append({"payer_group": grp, "month": month, "cause": cause, "why": why, "ask": ask, "checks": n, "cents": cents})
    return out


def _upper_bound(c: sqlite3.Connection) -> dict:
    """Open items EMA wrote off on a line no rate prices: they have no shortfall, so no total counts their dollars; what
    EMA wrote off on them is capped at the charge less what was paid (ema_checks C2, written_off_basis charge_less_paid),
    an upper bound shown apart."""
    if "evidence" not in _cols(c, "findings") or "written_off_cents" not in _cols(c, "findings"):
        return {"n": 0, "cents": 0}
    r = c.execute("""SELECT COUNT(*), COALESCE(SUM(written_off_cents),0) FROM findings WHERE COALESCE(stale,0)=0 AND status IN ('open','in_progress')
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
        # the same figures the write-off policy page opens with (counted now, every pattern: the run stores its 500 largest)
        try:
            tab = policy_table(c)
            tot = tab["totals"]
            out["policy"] = {"patterns": len(tab["patterns"]), "cents": tot["cents"], "appealable_cents": tot["appealable_cents"],
                             "legal_only_cents": tot["legal_only_cents"], "accepted": tot["accepted"], "decide": tot["decide"],
                             "contractual": tot["contractual"], "at": tab["run_at"]}
        except (sqlite3.Error, ValueError, TypeError, KeyError) as exc:
            out["policy"] = {"error": _fault(exc)}
        out["cause_words"] = {k: {"why": v[0], "ask": v[1]} for k, v in CHECK_CAUSES.items()}
        out["basis"] = dict(BASIS)
    # the payments to post and the credits to refund the run found (Today's two counts): each with the filter of the list
    # the panel opens, so the number is that list's count line
    try:
        out["items"] = _other_counts()
    except (sqlite3.Error, ValueError, TypeError, KeyError) as exc:
        out["items"] = {"error": _fault(exc)}
    return out


ITEM_BUCKETS = ("denied_todo", "denied_started", "denied_sent")     # the A/R buckets whose rows have an item on this site


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
        if bucket in ITEM_BUCKETS and out.get("rows") and "ema_link" in _cols(c, "findings"):
            # the item each row has (its EMA claim link), the open one first: a click opens it
            links = sorted({r["link"] for r in out["rows"] if r.get("link") is not None})
            got: dict = {}
            for i in range(0, len(links), 500):
                part = links[i:i + 500]
                for link, fid in c.execute(
                        f"""SELECT ema_link, finding_id FROM findings WHERE ema_link IN ({','.join('?' * len(part))})
                            AND COALESCE(stale,0)=0 ORDER BY status NOT IN ('open','in_progress'), shortfall_cents DESC, finding_id""", part):
                    got.setdefault(link, fid)
            for r in out["rows"]:
                if r.get("link") in got:
                    r["finding_id"] = got[r["link"]]
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
            where.append(f"{_GROUP_SQL} = ?")
            args.append(payer)
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


def _unposted_rows(conn: sqlite3.Connection) -> Iterator[list]:
    """The 835 payments EMA never posted, as the run listed them (meta ema:unposted rows), on either host: the Data tab's
    count is that same meta, so the file lists exactly what the page counts, even when the code that finds them changed
    since the run. A store whose run kept no rows gets the live list (ema_checks.unposted_csv)."""
    from . import ema_checks as ec
    live = ec.unposted_csv(conn)
    header = next(live)                 # the column names: the live rows are worked out only if they are read
    stored = _meta(conn, ec.UNPOSTED_KEY, {}) or {}
    rows = stored.get("rows") if isinstance(stored, dict) else None
    yield header
    if isinstance(rows, list):
        live.close()
        for r in rows:
            yield list(r)
        return
    yield from live


@router.get("/api/ema/unposted.csv")
def unposted_csv(user: str = Depends(_user)):
    with _conn() as c:
        if not _table(c, "ema_checks"):
            raise HTTPException(404, "No EMA Production report has been read yet.")
    return _csv("remitwatch-payments-not-posted.csv", _stream(_unposted_rows))


def _credit_rows(conn: sqlite3.Connection) -> Iterator[list]:
    """The credits on EMA's A/R (a balance below zero: the rows ema_checks.credits counts), one row each with the claim ID
    to find it in EMA. The patient's initials only while the names switch is on: with names off the file carries the claim
    ID only, as findings.csv does (initials beside a date of service would point at a person)."""
    names = bool(_api().SHOW_PHI)
    yield ["claim_id", "initials", "service_date", "code", "amount", "age_days", "party", "coverage", "payer_group"]
    cur = conn.execute("""SELECT claim_id, initials, svc, cpt, exposure_cents,
                                 CAST(julianday(as_of) - julianday(COALESCE(aging_date, svc)) AS INTEGER), party, coverage, payer_group
                          FROM ema_ar WHERE COALESCE(exposure_cents, 0) < 0 ORDER BY svc, cpt, claim_id""")
    while True:
        got = cur.fetchmany(500)
        if not got:
            break
        for cid, ini, svc, cpt, cents, age, party, cov, grp in got:
            yield [cid or "", (ini or "") if names else "", svc, cpt or "", f"{(cents or 0) / 100:.2f}", age, party, cov or "", grp or ""]


@router.get("/api/ema/credits.csv")
def credits_csv(user: str = Depends(_user)):
    with _conn() as c:
        if not _table(c, "ema_ar"):
            raise HTTPException(404, "No EMA A/R report has been read yet.")
    return _csv("remitwatch-credits.csv", _stream(_credit_rows))


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


def kind_of(pattern: str) -> str:
    """The three kinds of key the owner decides: a code a payer almost never pays ('group|code||payer_policy': its denials
    stay out of Work now until the owner decides), a reason EMA posts as contractual ('group|||reason', PLAN decision 3:
    the EMA_DENIAL_AS_CONTRACTUAL items), and a write-off pattern ('group|code|modifier|reason')."""
    parts = split_pattern(pattern)
    if parts["reason"] == "payer_policy":
        return "payer_policy"
    if not parts["code"] and not parts["modifier"] and parts["reason"]:
        return "contractual"
    return "write_off"


def _reason_text(reason: str) -> str:
    from .codes import CARC
    code = reason.split("-", 1)[-1] if reason else ""
    return CARC.get(code, "") if code else ""


def _c7_key(group: Any, reason: Any, carc: Any) -> str:
    """The owner's key for an EMA_DENIAL_AS_CONTRACTUAL item, exactly as story.next_step and states.py build it."""
    return f"{group or ''}|||{str(reason or carc or '')}"


def _payer_policies(c: sqlite3.Connection, stored: dict) -> dict:
    """The codes a payer almost never pays, as the run folded them (meta ema:payer_policy: every one; the table the run
    stores keeps its 500 largest rows): {pattern: {group, code, paid, answered, n, cents}}."""
    out: dict = {}
    pp = _meta(c, "ema:payer_policy", {}) or {}
    pats = pp.get("patterns") if isinstance(pp, dict) else None
    if isinstance(pats, dict):
        out.update({k: v for k, v in pats.items() if isinstance(v, dict)})
    for row in stored.get("patterns") or []:
        if isinstance(row, dict) and row.get("kind") == "payer_policy" and row.get("pattern") and row["pattern"] not in out:
            out[row["pattern"]] = {"n": row.get("items"), "cents": row.get("cents"), "paid": row.get("paid"), "answered": row.get("answered"),
                                   "decision": row.get("decision"), "default": row.get("default")}
    return out


def decide_totals(c: sqlite3.Connection) -> dict:
    """The codes a payer almost never pays that wait for the owner's decision (no person decided them): their count, the
    denials the run set aside and those denials' dollars. What the policy page's totals.decide says, from meta alone (so
    Today does not count the whole table)."""
    stored = _meta(c, "ema:policy_summary", {}) or {}
    decided = _meta(c, "ema:writeoff_policy", {}) or {}
    decided = decided if isinstance(decided, dict) else {}
    waiting = [p for k, p in _payer_policies(c, stored if isinstance(stored, dict) else {}).items()
               if not (isinstance(decided.get(k), dict) and decided[k].get("decision"))]
    return {"patterns": len(waiting), "items": sum(int(p.get("n") or 0) for p in waiting), "cents": sum(int(p.get("cents") or 0) for p in waiting)}


def _blank(key: str) -> dict:
    return {"pattern": key, "items": 0, "cents": 0, "appealable": 0, "appealable_cents": 0, "legal_only": 0, "legal_only_cents": 0,
            "auto": 0, "at_posting": 0, "rule_proven": 0, "by_policy": 0, "open": 0, "upper_n": 0, "upper_cents": 0}


def policy_table(c: sqlite3.Connection) -> dict:
    """The owner's write-off policy table (O11), one row per key the owner decides:
      - write-off patterns (kind write_off): every live item EMA wrote off, by the pattern the run recorded on it, counted
        now (so a count equals the list it opens), with what is still appealable: open items inside the safe date or the
        payer's own limit (orchestrator decision 2: past the safe date, one at a time; `legal_only` is that part);
        undecided means pursue (the items stay in Work now);
      - the codes a payer almost never pays (kind payer_policy), as the run folded them (meta ema:payer_policy): their
        denials stay in Work now, pursued in a batch per payer, until the owner accepts the pattern (PLAN decision 14:
        never accepted by default; the owner's decision 16: pursue at any win rate), so an undecided one is 'pursue';
      - the reasons EMA posts as contractual (kind contractual, PLAN decision 3: 'group|||reason'), from the live
        EMA_DENIAL_AS_CONTRACTUAL items, their written-off dollars said apart (outside every money total).
    The archive's later-paid share comes from what the run stored (meta ema:policy_summary); the decision from meta
    ema:writeoff_policy (who and when). A decision saved after the run is `pending`: the next run moves the items
    (accepted ones leave the work queue and stay findable)."""
    stored = _meta(c, "ema:policy_summary", {}) or {}
    stored = stored if isinstance(stored, dict) else {}
    decided = _meta(c, "ema:writeoff_policy", {}) or {}
    decided = decided if isinstance(decided, dict) else {}
    run_at = stored.get("at")
    by_key = {p.get("pattern"): p for p in stored.get("patterns") or [] if isinstance(p, dict)}
    fcols = _cols(c, "findings")
    pats: dict[str, dict] = {}
    if {"ema_outcome", "evidence"} <= fcols:
        w, params = _pattern_where(c)
        # still appealable: the safe date, or the payer's own limit where it is later (legal_deadline)
        last = "MAX(appeal_deadline, COALESCE(legal_deadline, appeal_deadline))" if "legal_deadline" in fcols else "appeal_deadline"
        legal = "(appeal_deadline < date('now') AND legal_deadline >= date('now'))" if "legal_deadline" in fcols else "0"
        todo = "status IN ('open','in_progress')"
        for (key, n, cents, appealable, appealable_cents, legal_n, legal_cents, auto, at_posting, proven, by_policy, open_n, upper_n,
             upper) in c.execute(
                f"""SELECT json_extract(evidence, '$.writeoff_pattern') k, COUNT(*), COALESCE(SUM(shortfall_cents),0),
                           SUM({todo} AND {last} >= date('now')),
                           COALESCE(SUM(CASE WHEN {todo} AND {last} >= date('now') THEN shortfall_cents END),0),
                           SUM({todo} AND {legal}), COALESCE(SUM(CASE WHEN {todo} AND {legal} THEN shortfall_cents END),0),
                           SUM(ema_outcome = 'auto_written_off'), SUM(written_off_when = 'at_posting'),
                           SUM(ema_writeoff_evidence = 'rule_proven'), SUM(ema_outcome = 'written_off_by_policy'),
                           SUM({todo}),
                           SUM(json_extract(evidence, '$.written_off_basis') = 'charge_less_paid'),
                           COALESCE(SUM(CASE WHEN json_extract(evidence, '$.written_off_basis') = 'charge_less_paid' THEN written_off_cents END),0)
                    FROM findings WHERE {w} GROUP BY k""", params):
            pats[key] = dict(_blank(key), kind="write_off", items=n, cents=cents, appealable=appealable or 0,
                             appealable_cents=appealable_cents or 0, legal_only=legal_n or 0, legal_only_cents=legal_cents or 0,
                             auto=auto or 0, at_posting=at_posting or 0, rule_proven=proven or 0, by_policy=by_policy or 0,
                             open=open_n or 0, upper_n=upper_n or 0, upper_cents=upper or 0)
    # the codes a payer almost never pays, as the run folded them
    folded = _payer_policies(c, stored)
    for key, pp in folded.items():
        n, cents = int(pp.get("n") or 0), int(pp.get("cents") or 0)
        pats[key] = dict(_blank(key), kind="payer_policy", items=n, cents=cents, appealable=None, appealable_cents=None,
                         legal_only=None, legal_only_cents=None, auto=None, at_posting=None, rule_proven=None, by_policy=None,
                         open=None, paid=pp.get("paid"), answered=pp.get("answered"))
    # the reasons EMA posts as contractual: one key per payer group and reason (the items are one per posted year)
    if "evidence" in fcols:
        for group, carc, ev_text, status in c.execute("""SELECT payer_group, carc, evidence, status FROM findings
                                                         WHERE signal='EMA_DENIAL_AS_CONTRACTUAL' AND COALESCE(stale,0)=0"""):
            try:
                ev = json.loads(ev_text or "{}")
            except ValueError:
                ev = {}
            ev = ev if isinstance(ev, dict) else {}
            key = _c7_key(group, ev.get("reason"), carc)
            # a claim-level write-off pattern has the same shape ('group|||reason'): the item joins that row, said apart
            p = pats.setdefault(key, dict(_blank(key), kind="contractual", cents=0, appealable=None, appealable_cents=None,
                                          legal_only=None, legal_only_cents=None, auto=None, at_posting=None, rule_proven=None,
                                          by_policy=None))
            p["items"] += 1
            p["contractual_items"] = (p.get("contractual_items") or 0) + 1
            p["open"] = (p.get("open") or 0) + (status in ("open", "in_progress"))
            p["written_off_cents"] = (p.get("written_off_cents") or 0) + int(ev.get("written_off_cents") or 0)
            p["inside_cents"] = (p.get("inside_cents") or 0) + int(ev.get("inside_window_cents") or 0)
            p["lines"] = (p.get("lines") or 0) + int(ev.get("lines") or 0)
    for key, d in decided.items():          # a pattern decided earlier that has no item now stays listed, so it can be changed back
        if key not in pats and isinstance(d, dict):
            pats[key] = dict(_blank(key), kind=kind_of(key))
    out = []
    for key, p in pats.items():
        d = decided.get(key) if isinstance(decided.get(key), dict) else {}
        s = by_key.get(key) or {}
        kind = p["kind"]
        p.update(split_pattern(key))
        if kind == "payer_policy":
            p["reason"] = ""
            p["reason_text"] = "The payer almost never pays this code"
            if p.get("answered"):
                p["later_paid_share"] = round((p.get("paid") or 0) / p["answered"], 3)
        else:
            p["reason_text"] = _reason_text(p["reason"])
        p.setdefault("later_paid_share", s.get("later_paid_share"))
        # a person's decision, else: pursue for a write-off pattern and for a code a payer almost never pays (its items stay
        # in Work now, appealed in a batch per payer: the owner's decision 16); waiting for the owner for a contractual
        # reason (nothing is accepted by default, PLAN decision 14)
        p["decision"] = d.get("decision") or ("pursue" if kind in ("write_off", "payer_policy") else "decide")
        p["decided_by"], p["decided_at"] = d.get("by"), d.get("at")
        # a decision the newest run has not applied yet: the run stored the decision it applied for most patterns (a
        # code a payer almost never pays: none while it was the default), so pending is a difference from that; for the
        # rest, a decision saved after the run began
        ran = folded.get(key) if kind == "payer_policy" else (s if "decision" in s else None)
        if not d.get("decision"):
            p["pending"] = False
        elif ran is not None:
            p["pending"] = (None if ran.get("default") else ran.get("decision")) != p["decision"]
        else:
            p["pending"] = not run_at or _after(d.get("at"), run_at)
        p["basis"] = BASIS["policy" if kind == "write_off" else kind]
        out.append(p)
    first = {"decide": 0}

    def order(p: dict) -> tuple:
        return (first.get(p["decision"], 1), -(p.get("appealable_cents") or 0), -(p.get("cents") or 0), -(p.get("written_off_cents") or 0),
                p["pattern"])
    out.sort(key=order)
    wo = [p for p in out if p["kind"] == "write_off"]
    undecided = [p for p in out if p["kind"] == "payer_policy" and p["decision"] == "decide"]
    c7 = [p for p in out if p.get("contractual_items")]
    return {"patterns": out, "run_at": run_at, "today": _today(),
            "totals": {"items": sum(p["items"] for p in wo), "cents": sum(p["cents"] for p in wo),
                       "appealable": sum(p["appealable"] for p in wo), "appealable_cents": sum(p["appealable_cents"] for p in wo),
                       "legal_only": sum(p["legal_only"] for p in wo), "legal_only_cents": sum(p["legal_only_cents"] for p in wo),
                       "accepted": sum(1 for p in out if p["decision"] == "accept"),
                       "decide": {"patterns": len(undecided), "items": sum(p["items"] for p in undecided),
                                  "cents": sum(p["cents"] for p in undecided)},
                       "contractual": {"patterns": len(c7), "items": sum(p["contractual_items"] for p in c7),
                                       "written_off_cents": sum(p.get("written_off_cents") or 0 for p in c7)}}}


@router.get("/api/ema/policy")
def policy(user: str = Depends(_user)):
    with _conn() as c:
        return policy_table(c)


def _c7_rows(c: sqlite3.Connection, pattern: str) -> list[dict]:
    """The EMA_DENIAL_AS_CONTRACTUAL items of one 'group|||reason' key (one per posted year), most dollars first."""
    fcols = _cols(c, "findings")
    if "evidence" not in fcols or kind_of(pattern) != "contractual":
        return []
    ws = "work_state" if "work_state" in fcols else "NULL"
    found = []
    for fid, group, carc, ev_text, status, state in c.execute(
            f"""SELECT finding_id, payer_group, carc, evidence, status, {ws} FROM findings
                WHERE signal='EMA_DENIAL_AS_CONTRACTUAL' AND COALESCE(stale,0)=0"""):
        try:
            ev = json.loads(ev_text or "{}")
        except ValueError:
            ev = {}
        ev = ev if isinstance(ev, dict) else {}
        if _c7_key(group, ev.get("reason"), carc) != pattern:
            continue
        found.append({"finding_id": fid, "kind": "contractual", "payer_group": group, "year": ev.get("year"),
                      "written_off_cents": int(ev.get("written_off_cents") or 0), "inside_cents": int(ev.get("inside_window_cents") or 0),
                      "lines": ev.get("lines"), "top_codes": [x[0] for x in ev.get("top_codes") or [] if isinstance(x, list) and x][:5],
                      "status": status, "work_state": state, "state_text": STATE_WORDS.get(state or "", ""),
                      "outcome_text": "Posted as a contractual write-off"})
    found.sort(key=lambda r: (-r["written_off_cents"], r["finding_id"]))
    return found


@router.get("/api/ema/policy/items")
def policy_items(pattern: str = Query(..., max_length=200), page: int = Query(1, ge=1, le=100000), user: str = Depends(_user)):
    """One page of a key's items: what its count counts, the items EMA posted as contractual for the reason first, then
    the written-off items, most dollars first. No patient names. A code a payer almost never pays lists nothing here: the
    run sets its denials aside and lists them in the Worklist once the owner chooses Pursue."""
    kind = kind_of(pattern)
    empty = {"pattern": pattern, "kind": kind, "count": 0, "cents": 0, "written_off_cents": 0, "rows": [], "page": page, "size": PAGE}
    if kind == "payer_policy":
        return dict(empty, note=("The run sets these denials aside while the payer's policy is to not pay the code. Choose Pursue "
                                 "and the next run lists every one in the Worklist, to appeal line by line."))
    with _conn() as c:
        fcols = _cols(c, "findings")
        c7 = _c7_rows(c, pattern)
        n = cents = 0
        if {"ema_outcome", "evidence"} <= fcols:
            w, params = _pattern_where(c)
            w += " AND json_extract(evidence, '$.writeoff_pattern') = ?"
            params.append(pattern)
            n, cents = c.execute(f"SELECT COUNT(*), COALESCE(SUM(shortfall_cents),0) FROM findings WHERE {w}", params).fetchone()
        start = (page - 1) * PAGE
        rows = c7[start:start + PAGE]
        if n and len(rows) < PAGE:
            cols = [x for x in ("finding_id", "signal", "service_date", "procedure_code", "payer_group", "shortfall_cents",
                                "appeal_deadline", "legal_deadline", "status", "work_state", "ema_outcome", "written_off_cents",
                                "written_off_on", "carc", "patient_control_number") if x in fcols]
            # an item no rate prices has no shortfall; what EMA wrote off on it (charge less paid) is shown as an upper bound
            got = [dict(zip(cols + ["written_off_basis"], r)) for r in c.execute(
                f"""SELECT {', '.join(cols)}, json_extract(evidence, '$.written_off_basis') FROM findings WHERE {w}
                    ORDER BY shortfall_cents DESC, appeal_deadline, finding_id LIMIT ? OFFSET ?""",
                params + [PAGE - len(rows), max(0, start - len(c7))])]
            for r in got:
                r["kind"] = "write_off"
                r["outcome_text"] = OUTCOME_WORDS.get(r.get("ema_outcome") or "", "")
                r["state_text"] = STATE_WORDS.get(r.get("work_state") or "", "")
            rows = rows + got
        return dict(empty, count=n + len(c7), cents=cents, rows=rows, written_off_cents=sum(r["written_off_cents"] for r in c7))


def _known_pattern(c: sqlite3.Connection, pattern: str) -> bool:
    """A key the owner's table lists (a write-off pattern, a code a payer almost never pays, a reason EMA posts as
    contractual, or one decided before)."""
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
        # the read and the write of the decisions under one write lock: two people deciding a moment apart both count
        c.execute("BEGIN IMMEDIATE")
        try:
            rec = ec.set_policy(c, pattern, decision, who)
        except BaseException:
            c.rollback()
            raise
    return {"ok": True, "pattern": pattern, "decision": rec["decision"], "by": rec["by"], "at": rec["at"], "applies": "next run"}
