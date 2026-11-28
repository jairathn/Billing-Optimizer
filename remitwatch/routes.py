"""
Payer routes: the one place that picks how an item's request reaches its payer.

The owner's ask (2026-11-28): route each request the first time by the easiest way the payer accepts, with the least work
for the biller: the fields filled in, ready to copy. remitwatch/payer_routes.json holds the researched table (each payer
group and request type, its accepted channels ranked easiest first, the ones it refuses, the time limit, the sources and
the day it was checked: plans/tools/routes/build_payer_routes.py builds it). This module reads the item, its state and its
next step (remitwatch.states, remitwatch.story) and gives the drawer's route card:

- the request type, from the item's route (states.route_of) and its facts: an Original Medicare appeal is a
  redetermination, or a reopening when the fix is clerical (appeals._medicare_route); after the payer turned down what
  was sent (sent_request reads what that was from the Sent's note), an appeal goes to the second level and a Medicare
  reopening to a redetermination of the first decision, while a status call, a corrected claim or a bill turned down is no
  level at all; a call is a claim status request (with the timely filing proof beside it when the first-filing limit is
  near); a correction is a corrected claim; money owed back is a refund. Routes with no payer step (post, bill the patient,
  contract, policy, fees, decide, wait, nothing) get no card;
- the payer group (codes.payer_group_kind) mapped to the research's, with the plan's scope (Original Medicare or Medicare
  Advantage, commercial or Medicaid, BlueCard, TriWest's regions, MHS Indiana...); no match gives a plain fallback;
- the channels in rank order, the not-accepted ones kept as warnings, and each channel's fields filled from the item and
  its claim where the store holds them (the patient's name and member number only with the names switch on,
  REMITWATCH_SHOW_PHI) and from the practice's settings (PRACTICE_*), never invented: a field with no value says what to
  fill;
- a fax channel shows the published number and the number on file (remitwatch.fax.number_for): nothing is ever faxed to a
  number a person has not confirmed on the Faxes page. The app's own fax send carries a first-level appeal to the payer's
  confirmed appeal number only, so the card offers it only for a first-level appeal whose published number is that number
  (sendable); every other fax is sent by hand, after checking the number.

Rules only: nothing here decides a state, an outcome or a closure, and nothing is logged (the card is built for the page
that asks for it and goes nowhere else).
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import codes, runlog

TABLE_PATH = Path(__file__).with_name("payer_routes.json")
FALLBACK = "No checked route for this payer yet. Use its provider portal, then note what worked."
# the routes whose work goes to no payer: they get no card (states.route_of)
NO_CARD = frozenset({"post", "bill_patient", "bill_secondary", "contract", "policy", "fees", "decide", "wait", "nothing", "status"})
# claims that never reached the payer (rejected before it, or never sent): no payer route, a new claim from EMA
NEVER_REACHED = frozenset({"REJECTED_PRE_PAYER", "NOT_SENT"})
# work that goes to another payer than the item's (the second plan never billed): no request to the item's payer, which
# answered as billed, whatever route a person's start leaves the item on
OTHER_PAYER = frozenset({"SECONDARY_NOT_BILLED"})
# a first-filing limit this close (or past) puts the timely filing proof beside a claim status request
NEAR_FILING_DAYS = 30
# the requests that are appeals: the payer's "no" to one takes the next level
APPEALS = frozenset({"reconsideration_or_first_appeal", "medicare_redetermination"})
# what a person's Sent carried, in the words the card's "I sent it" writes in its note ("Sent a reopening by Portal: ..."),
# so the next request follows what was sent, never the item's route alone (review routes-r1-1, r1-2). A status request
# writes no Sent: "I asked the payer" is a line in the item's history (the 'asked' event)
SENT_WORDS = {"medicare_reopening": "a reopening", "medicare_redetermination": "a redetermination",
              "reconsideration_or_first_appeal": "a first-level appeal", "second_level_appeal": "a second-level appeal",
              "corrected_claim": "a corrected claim", "records_or_attachments": "the records", "overpayment_refund": "a refund",
              "timely_filing_proof": "the proof of timely filing"}
_SENT_AS = re.compile(r"\bSent (" + "|".join(re.escape(w) for w in sorted(SENT_WORDS.values(), key=len, reverse=True)) + r") by ")
# claims no payer has answered: what goes to the payer about one is the claim itself (a status call, the claim sent again),
# never an appeal, so a "no" to it is no first level turned down
UNANSWERED = frozenset({"NO_ANSWER", "DENIED_DUPLICATE_UNPAID"})
# the rate signals states.route_of sends to a payment dispute (route rate)
RATE_SIGNALS = frozenset({"UNDERPAID_VS_OWN_RATE", "MPPR_EXCESS", "SECOND_RATE"})
SETTINGS = ("PRACTICE_NAME", "PRACTICE_ADDRESS", "PRACTICE_TIN", "PRACTICE_NPI", "PRACTICE_PTAN", "PRACTICE_PHONE",
            "PRACTICE_FAX", "PRACTICE_EMAIL")


@lru_cache(maxsize=1)
def table() -> dict:
    """The researched table (remitwatch/payer_routes.json), read once."""
    with open(TABLE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _rows(group: str, request: str) -> list[dict]:
    return [r for r in table()["routes"] if r["group"] == group and r["request"] == request]


# ------------------------------------------------------------------------------------------------ the payer group
# The app's payer groups (codes.PAYER_RULES, refined by the claim filing indicator: codes.payer_group_kind) that the
# research's groups serve. First match wins; a group a research row was not written for gets the fallback.
GROUP_MAP: list[tuple[str, str]] = [
    (r"^Medicare KY \(CGS J15\)$", "Medicare KY (CGS J15)"),
    (r"^Medicare IN \(WPS J8\)$", "Medicare IN (WPS J8)"),
    (r"^Railroad Medicare$", "Railroad Medicare"),
    (r"^Anthem / BCBS$", "Anthem / BCBS"),
    (r"^(?:Anthem / BCBS|Anthem) Medicare Advantage$", "Anthem / BCBS Medicare Advantage"),
    (r"^Humana$", "Humana"),
    (r"^Humana Medicare Advantage$", "Humana Medicare Advantage"),
    (r"^Humana(?: Medicare Advantage)? Medicaid$", "Humana Medicare Advantage Medicaid"),
    (r"^Tricare$", "Tricare"),
    (r"^UnitedHealthcare$", "UnitedHealthcare"),
    (r"^(?:UHC|UnitedHealthcare) Medicare Advantage$", "UnitedHealthcare Medicare Advantage"),
    (r"^UMR$", "UMR"),
    (r"^United Healthcare Shared Services$", "United Healthcare Shared Services"),
    (r"^UHC Integrated Services \(Wausau\)$", "UHC Integrated Services (Wausau)"),
    (r"^VA Community Care$", "VA Community Care"),
    (r"^Aetna(?: Medicare Advantage)?$", "Aetna"),
    (r"^Cigna$", "Cigna"),
    (r"^(?:Health ?spring|Cigna(?: Medicare Supplement)? Medicare Advantage)$", "HealthSpring"),
    (r"^WellCare / Ambetter \(Centene\) Medicaid$", "WellCare / Ambetter (Centene) Medicaid"),
    (r"^WellCare / Ambetter \(Centene\)(?: Medicare Advantage)?$", "WellCare / Ambetter (Centene) Medicare Advantage"),
    (r"^Ambetter \(Centene\)$", "Ambetter (Centene)"),
    (r"^Medicaid KY$", "Medicaid KY"),
    (r"^Medicaid IN$", "Medicaid IN"),
    (r"^UMWA Health & Retirement Funds", "UMWA Health & Retirement Funds"),
    (r"^(?:Medicare Supplement carriers|AARP Medicare Supplement \(UHC\)|Cigna Medicare Supplement|Aetna Senior Supplemental)",
     "Medicare Supplement carriers"),
]
# a plan of a covered payer the research did not cover: the card says so instead of borrowing the payer's other plans
NOT_COVERED_MAP: list[tuple[str, str]] = [
    (r"^Anthem / BCBS Medicaid$", "Anthem / BCBS Medicaid"),
    (r"^Aetna Medicaid$", "Aetna Medicaid"),
    (r"^(?:UnitedHealthcare|UHC) Medicaid$", "UnitedHealthcare Medicaid"),
]
CONTRACTOR_STATE = {"Medicare KY (CGS J15)": "Kentucky", "Medicare IN (WPS J8)": "Indiana"}


def _plan(f: dict, claim: dict) -> str:
    return " ".join(str(claim.get("contract_class") or f.get("plan") or "").upper().split())


def research_group(f: dict, claim: dict | None = None) -> tuple[str | None, str | None]:
    """(the research's payer group for this item, or None; the words for a plan the research did not cover, or None).
    The VA's network is found by its payer id whatever group its name gave (codes.VA_PAYER_IDS)."""
    claim = claim or {}
    grp, kind = str(f.get("payer_group") or "").strip(), str(f.get("payer_kind") or "")
    pid = str(f.get("payer_id") or claim.get("payer_id") or "").strip().upper()
    if pid in codes.VA_PAYER_IDS:
        return "VA Community Care", None
    if re.search(r"HEALTH ?SPRING", str(f.get("payer_name") or "").upper()):
        return "HealthSpring", None
    for rx, key in NOT_COVERED_MAP:
        if re.search(rx, grp):
            return None, table()["not_covered"][key]
    if re.search(r"^Humana(?: Medicare Advantage)? Medicaid$", grp) and codes.plan_state(_plan(f, claim), f.get("payer_name")) == "IN":
        return None, table()["not_covered"]["Humana Medicaid IN"]
    for rx, key in GROUP_MAP:
        if re.search(rx, grp):
            if key == "Cigna" and kind == "medicare_advantage":
                return "HealthSpring", None
            return key, None
    return None, None


# ------------------------------------------------------------------------------------------------ the request type
def _carcs(conn: sqlite3.Connection | None, f: dict) -> set[str]:
    out = {codes._carc_of(f.get("carc"))} - {""}
    if conn is not None and f.get("line_key"):
        try:
            out |= {codes._carc_of(r[0]) for r in conn.execute("SELECT reason_code FROM adjustments WHERE line_key=?", (f["line_key"],))}
        except sqlite3.Error as exc:
            runlog.caught(exc, "routes.carcs", level="warning")     # a copy with no adjustments table: the item's own reason stands
    return out - {""}


def sent_request(text: str | None) -> str | None:
    """The request a person's Sent carried, read from its note (or the state's words for it, which quote the note): the
    card's own note names it ('Sent a reopening by Portal: ...', SENT_WORDS); the page's other notes say what they were (a
    status call: 'Asked the payer', 'Call or portal check'; a bill: 'Billed'; a corrected claim sent again); a reopening is
    named by the payer's own form. None when the note does not say (a Sent marked by hand, an appeal the app faxed)."""
    t = " ".join(str(text or "").split())
    m = _SENT_AS.search(t)
    if m:
        return next(k for k, v in SENT_WORDS.items() if v == m.group(1))
    if re.search(r"\bAsked the payer\b|\bCall or portal check\b", t, re.I):
        return "claim_status_or_reprocess"
    if re.search(r"(?:^|: )Billed\b", t):
        return "billed"
    if re.search(r"\bcorrected (?:and sent again|claim)\b", t, re.I):
        return "corrected_claim"
    if re.search(r"\breopening", t, re.I):
        return "medicare_reopening"
    return None


def _first_level(conn: sqlite3.Connection | None, f: dict, st: dict) -> dict:
    """What last went to the payer on this item and whether the payer turned it down: {'sent': the request it carried
    (sent_request: None when its note does not say), 'denied', 'day': the day of the payer's no}. Turned down: the payer's
    unpaid answer on a remittance to the work a person or the app sent (states rule work_answered), or a person's 'The
    payer said no' logged after a Sent. A person's status changes only (kind 'manual'): a line in the history ('asked', a
    fax, an outcome) repeats the status and is no Sent. A corrected claim sent again is no level: its denial is worked as
    a new denial."""
    out: dict = {"sent": None, "denied": False, "day": None}
    if f.get("actionable") == "correct_and_resubmit":
        return out
    a, w = st.get("answered") or {}, st.get("work") or {}
    if st.get("rule") == "work_answered" and a and int(a.get("paid") or 0) <= 0 and w.get("kind") in ("sent", "fax", "appeal"):
        out.update(denied=True, day=str(a.get("date") or "")[:10] or None,
                   sent=sent_request(w.get("text")) if w.get("kind") == "sent" else None)
        return out
    if conn is None:
        return out
    try:
        rows = conn.execute("SELECT at, to_status, note FROM finding_events WHERE finding_id=? AND kind='manual' ORDER BY at, id",
                            (f["finding_id"],)).fetchall()
    except sqlite3.Error as exc:
        runlog.caught(exc, "routes.first_level", level="warning")
        return out
    sent_note = None
    for at, to, note in rows:
        if to == "appealed":
            sent_note = str(note or "")
        elif sent_note is not None and to == "in_progress" and str(note or "").startswith("The payer said no"):
            out.update(denied=True, day=str(at)[:10], sent=sent_request(sent_note))
    return out


def _long(d: str | None) -> str:
    try:
        x = date.fromisoformat(str(d)[:10])
    except (TypeError, ValueError) as exc:
        runlog.caught(exc, "routes.long_date", level="warning")
        return ""
    return f"{x:%b} {x.day}, {x.year}"


def _first_filing(f: dict, st: dict) -> dict | None:
    """The first-filing limit of a claim no payer has answered (the window of that name states.windows lists), or None:
    the date a status request is due by, which the item's own date can run past (a dispute window after the limit)."""
    if f.get("signal") not in ("NO_ANSWER", "REJECTED_PRE_PAYER"):
        return None
    from . import states
    try:
        wins = states.windows(f, st.get("deadline"), st.get("deadline_basis"))
    except (KeyError, TypeError, ValueError) as exc:
        runlog.caught(exc, "routes.first_filing", level="warning")
        return None
    return next((w for w in wins if w.get("what") == "the first-filing limit"), None)


def _days(a: str | None, b: str | None) -> int | None:
    if not a or not b:
        return None
    try:
        return (date.fromisoformat(str(b)[:10]) - date.fromisoformat(str(a)[:10])).days
    except (TypeError, ValueError) as exc:
        runlog.caught(exc, "routes.days", level="warning")
        return None


def _medicare_first(f: dict, route: str) -> tuple[str, str]:
    """(the first request Original Medicare takes for this item, why): a payment fixed by reopening the claim, a clerical
    error reopened, an unprocessable claim sent again as a new claim, else the redetermination. An item whose route is now
    a call (the payer answered work sent on it) is read by its signal: a rate or repricing signal was a payment fix."""
    from . import states
    from .appeals import _medicare_route
    how = _medicare_route(dict(f, evidence=f.get("evidence") or {}))
    sig, cat = str(f.get("signal") or ""), str(f.get("category") or "")
    dispute = route in ("rate", "repricing") or (route == "call" and (sig in states.BATCH or sig in RATE_SIGNALS or (
        cat in states.SHORT and not sig.startswith("DOWNCODE"))))
    if dispute:
        return "medicare_reopening", "Original Medicare fixes a payment by reopening the claim."
    if how == "reopening":
        return "medicare_reopening", "A clerical error Original Medicare fixes by reopening, not by appeal."
    if how == "new_claim":
        return "corrected_claim", "Medicare returned the claim as unprocessable: a new claim, not an appeal."
    return "medicare_redetermination", "Original Medicare's first-level appeal is a redetermination."


def _own_first(f: dict, route: str, medicare: bool) -> str:
    """The first request this item's own route takes (what a Sent whose note does not say most likely carried)."""
    if route == "corrected" or f.get("actionable") == "correct_and_resubmit":
        return "corrected_claim"
    if f.get("signal") in UNANSWERED:
        return "claim_status_or_reprocess"
    if medicare:
        return _medicare_first(f, route)[0]
    return "reconsideration_or_first_appeal"


def _after_no(first: dict, f: dict, route: str, medicare: bool) -> tuple[str, str] | None:
    """(the request after the payer turned down what was sent, why), or None when what was turned down was no level of
    review (a status call, a corrected claim, a bill, a refund): the item's own request stands. An appeal goes to the
    second level. Original Medicare's decision on a reopening cannot be appealed (42 CFR 405.980(a)(5)), and the QIC takes
    a reconsideration only of a redetermination (405.960): the first decision is appealed, by redetermination."""
    if not first["denied"]:
        return None
    sent = first["sent"] or _own_first(f, route, medicare)
    if sent in APPEALS or sent == "second_level_appeal" or (sent == "timely_filing_proof" and not medicare):
        return "second_level_appeal", "The payer turned down the first appeal: the next level."
    if sent == "medicare_reopening" and medicare:
        return "medicare_redetermination", ("Medicare turned the reopening down. Its decision on a reopening cannot be appealed, so "
                                            "appeal the first decision: a redetermination.")
    return None


def _request(conn: sqlite3.Connection | None, f: dict, st: dict, nx: dict, today: str) -> dict:
    """{'request', 'why', 'also' (a second request to show beside it), 'after' (what the payer turned down, when the request
    follows it), 'first' (_first_level)}; request None when the item's work goes to no payer."""
    none = {"request": None, "why": "", "also": None, "after": None, "first": None}
    route = nx.get("route") or st.get("route") or ""
    if not route or route in NO_CARD:
        return none
    if f.get("signal") in NEVER_REACHED:
        # the claim never reached the payer: it is fixed and sent again from EMA as a new claim, and the payer's own
        # corrected-claim route (a replacement of a claim it has, frequency 7 with its claim number) does not apply
        return none
    if f.get("signal") in OTHER_PAYER:
        return none
    medicare = f.get("payer_kind") == "medicare"
    carcs = _carcs(conn, f)
    timely = f.get("signal") == "DENIED_TIMELY_FILING" or "29" in carcs

    def out(request: str, why: str, also: str | None = None, first: dict | None = None) -> dict:
        return {"request": request, "why": why, "also": also, "first": first,
                "after": (first or {}).get("sent") or (_own_first(f, route, medicare) if first else None)}
    if route == "refund":
        return out("overpayment_refund", "Money is owed back to the payer.")
    if route == "call":
        first = _first_level(conn, f, st)
        nxt = _after_no(first, f, route, medicare)
        if nxt:
            return out(*nxt, first=first)
        also = None
        ff = _first_filing(f, st)
        left = _days(today, ff["date"]) if ff else None
        if left is not None and left <= NEAR_FILING_DAYS:
            also = "timely_filing_proof"
        return out("claim_status_or_reprocess", "Ask the payer where the claim stands, or to reprocess it.", also)
    if route in ("eob_first", "cob"):
        return out("claim_status_or_reprocess", "Get the payer's answer first." if route == "eob_first" else
                   "Have the payer update which plan pays first, then reprocess.")
    if timely and route in ("appeal", "corrected", "rate"):
        return out("timely_filing_proof", "A timely filing denial: send the proof the claim reached the payer in time.")
    if route in ("appeal", "rate", "repricing", "corrected"):
        first = _first_level(conn, f, st)
        nxt = _after_no(first, f, route, medicare)
        if nxt:
            return out(*nxt, first=first)
    if medicare:
        return out(*_medicare_first(f, route))
    if route == "corrected":
        return out("corrected_claim", "A corrected claim replaces the one the payer has.")
    return out("reconsideration_or_first_appeal", "Ask the payer to reprocess the payment (a payment dispute)." if route in ("rate", "repricing")
               else "The payer's first-level review of its decision.")


def request_for(conn: sqlite3.Connection | None, f: dict, st: dict, nx: dict, today: str) -> tuple[str | None, str, str | None]:
    """(the request type, why in words, a second request to show beside it or None). None when the item's work goes to
    no payer."""
    r = _request(conn, f, st, nx, today)
    return r["request"], r["why"], r["also"]


# ------------------------------------------------------------------------------------------------ the plan's row
TRICARE_CLINICAL = frozenset({"50", "55", "56", "96", "204", "167", "B5", "272"})
TRICARE_CODING = frozenset({"97", "234", "236", "B15", "B20", "231"})
# a medical necessity, authorization or referral denial (the plans that send these to an appeal and everything else to a
# payment reconsideration: Ambetter's member appeal, HealthSpring's one level of appeal)
CLINICAL_AUTH_CARCS = codes.MEDICAL_NECESSITY_CARCS | frozenset({"197", "198", "15", "39", "62", "243", "288"})


def pick(rows: list[dict], f: dict, claim: dict, request: str, route: str, carcs: set[str]) -> tuple[dict | None, list[dict], str]:
    """(the row for this item's plan, the others to show as alternatives, why this one). The practice is contracted with
    the large plans (codes: the contracted Medicare Advantage plans), so a non-contracted row is only an alternative."""
    if not rows:
        return None, [], ""
    by = {r["variant"]: r for r in rows}
    want, why = "main", ""
    grp = rows[0]["group"]
    plan, name = _plan(f, claim), str(f.get("payer_name") or "").upper()
    pid = str(f.get("payer_id") or claim.get("payer_id") or "").strip().upper()
    state = codes.plan_state(plan, f.get("payer_name"))
    if grp == "Anthem / BCBS":
        if re.search(codes.FEP_PLAN, plan):
            want, why = "fep", "The plan is the Federal Employee Program."
        elif re.search(codes.ANTHEM_HOME_PLAN, plan) or (state and state not in codes.ANTHEM_OWN_STATES):
            want, why = "bluecard", "The plan is another Blue plan's (BlueCard)."
        elif request == "reconsideration_or_first_appeal" and carcs & codes.ANTHEM_CLINICAL_CARCS:
            want, why = "clinical", "A clinical denial (not medically necessary) takes Anthem's clinical appeal."
    elif grp == "VA Community Care":
        if pid == "TWVACCN" or "TRIWEST" in name:
            want, why = "triwest", "TriWest's network (payer ID TWVACCN)."
        else:
            why = "Kentucky and Indiana are in Region 2 (Optum)."
    elif grp == "WellCare / Ambetter (Centene) Medicaid":
        if re.search(r"\bMHS\b|MANAGED HEALTH SERVICES", name) or state == "IN":
            want, why = "mhs", "MHS, Indiana's Centene Medicaid plan."
        elif request == "reconsideration_or_first_appeal" and carcs and not (carcs & codes.WELLCARE_KY_PAYMENT_CARCS) \
                and carcs & codes.WELLCARE_KY_CLINICAL_CARCS:
            want, why = "clinical", "A clinical denial (authorization, documentation) takes WellCare's clinical appeal."
    elif grp == "Tricare" and request == "reconsideration_or_first_appeal":
        if carcs & TRICARE_CLINICAL:
            want, why = "clinical", "A medical necessity or coverage denial is appealed for the beneficiary."
        elif carcs & TRICARE_CODING:
            want, why = "coding_edit", "A coding edit's rejection has its own reconsideration."
    elif grp == "Humana Medicare Advantage Medicaid" and request == "reconsideration_or_first_appeal" and route in ("rate", "repricing"):
        want, why = "rate", "Paid below the contracted rate."
    elif grp == "Aetna" and request == "second_level_appeal" and f.get("payer_kind") == "medicare_advantage":
        want, why = "ma", "A Medicare Advantage plan."
    elif grp == "Ambetter (Centene)" and request == "reconsideration_or_first_appeal" and carcs & CLINICAL_AUTH_CARCS:
        want, why = "clinical", "A medical necessity or authorization denial goes through the member appeal process."
    elif grp == "HealthSpring" and request == "reconsideration_or_first_appeal" and route not in ("rate", "repricing") \
            and carcs & CLINICAL_AUTH_CARCS:
        # HealthSpring's manual: an appeal is for a decision on medical necessity, clinical guidelines, or prior
        # authorization and referral requirements (65 days); the claim reconsideration (180 days) for everything else
        want, why = "clinical", ("A medical necessity, authorization or referral denial takes HealthSpring's appeal, not its claim "
                                 "reconsideration.")
    row = by.get(want) or by.get("main") or rows[0]
    if row["variant"] != want:
        why = ""
    return row, [r for r in rows if r is not row], why


# ------------------------------------------------------------------------------------------------ the fields
# A field's label (as the payer prints it) to what fills it: (pattern, kind, keys). kind 'step' is where to click (shown
# as steps, never with a value), 'set' a fixed setting the label itself gives, 'field' a value to type. First match wins.
FIELD_RULES: list[tuple[str, str, tuple[str, ...]]] = [
    # where to click
    (r">|^(?:sign ?in|open |click|find |select |submit|review(?: the|$)|confirm it|request redetermination|request authorization|"
     r"request claim inquiry|claim inquiry\b|claim status inquiry|claims tab|forms tab|financial tools|first level appeal confirmation|"
     r"pre-populated|cer\b|oca\b|edit (?:the )?claim|add each|add \(|patient search|search criteria|view|one form per|mark the|"
     r"leave out|upload|attach|appeal/review this claim|claim found|reopening request|or prepayment|post-payment|claim details|"
     r"comment box|other form fields|same |form fields|paper form fields|documentation: attach|pdf attachments|attachments \(|file$|"
     r"claim dispute comments|every asterisked|every field marked|open the pended|urgent care box|show me|claim search|"
     r"select the patient|optional: an excel|create a reconsideration|act on)", "step", ()),
    # what the label itself says to do, or what the payer's form fills in (shown as the label says)
    (r"^type:|^attn:|\(pre-filled\)$|^(?:all|every|entire)\b[^,]*?\b(?:lines?|line items|claim)\b", "set", ()),
    # a line's change on Medicare's reopening and refund forms: what the line becomes and where, never the date of service
    # the label names among its examples (review routes-r2-4), and the other payer's amounts only for an MSP claim
    (r"^new value\b", "field", ("new_value",)),
    (r"^msp only\b", "field", ("msp_amounts",)),
    (r"^line number and position", "field", ("line_number", "line_position")),
    (r"^line(?: \(|$)", "field", ("line_number",)),
    (r"^pos(?:ition)?(?: \(|$)", "field", ("line_position",)),
    # what the store knows the answer to: an appeal of a denial or an underpayment is no overpayment appeal; the level; one
    # claim per request; a service already given; the provider appealing (review routes-r2-7)
    (r"^type of appeal: non-overpayment", "field", ("appeal_kind",)),
    (r"^overpayment appeal \(yes or no\)|^appealing an overpayment\?", "field", ("overpayment_appeal",)),
    (r"^overpayment appeal \(|^\d+\. overpayment involved", "field", ("overpayment_details",)),
    (r"^level \(i or ii\)", "field", ("appeal_level",)),
    (r"^have services been rendered", "field", ("services_rendered",)),
    (r"^provider or patient \(appeals\)", "field", ("appellant_role",)),
    (r"^appealing multiple claims", "field", ("multiple_claims",)),
    (r"^adjustment or void", "field", ("adjust_or_void",)),
    (r"reconsideration denial notification date", "field", ("first_answer_date",)),
    # a refund's check and its amount (review routes-r2-2: the amount to send back, on every refund way)
    (r"^check number, date, amount", "field", ("refund_check", "refund_check_date", "refund_amount")),
    (r"^check number and amount", "field", ("refund_check", "refund_amount")),
    (r"^amount of payment, check|^payment details$", "field", ("refund_amount", "refund_check", "refund_check_date")),
    (r"^(?:enclosed )?check (?:number|#)", "field", ("refund_check",)),
    (r"^(?:enclosed )?check date|^date of refund", "field", ("refund_check_date",)),
    (r"^amount$|^amount of check|^(?:enclosed )?check amount", "field", ("refund_amount",)),
    (r"^enclosed payment stub", "field", ("payment_stub",)),
    (r"^refund control number", "field", ("rcn",)),
    (r"^refund letter request", "field", ("refund_letter",)),
    (r"^corporate integrity", "field", ("cia",)),
    (r"^bank information", "field", ("bank_account", "refund_amount")),
    # selections from the payer's own list, and what a person fills from the letter, the card or EMA
    (r"^action\b|^type\b(?! of bill)|^adjustment details|^msp or non-msp|^medicare type|^dispute \(inquiry\) type|^issue \(|"
     r"^category$|^document type|^refund is the result of|^payment recoupment|^recoupment or refund|^did medicare request|"
     r"^contract$|^in or out of network", "field", ("pick",)),
    (r"^fee schedule change", "field", ("fee_change",)),
    (r"^lines:", "field", ("lines_to_change",)),
    (r"id number and relationship to the patient", "field", ("member_id", "relationship")),
    (r"beneficiary phone|member address|beneficiary state|state of residence|relationship of the subscriber", "field",
     ("patient_contact",)),
    (r"^redetermination number", "field", ("redetermination_number",)),
    (r"^contractor number", "field", ("contractor_number",)),
    (r"^\d*\.? ?person appealing", "field", ("appellant",)),
    (r"^i have additional information to submit|are you including medical records", "field", ("records_included",)),
    (r"^suggested documentation checklist|^(?:\d+\. )?supporting (?:material|evidence|documents)|^additional information, clinical|"
     r"^new material information", "field", ("supporting",)),
    (r"^total number of documentation pages|^number of attached pages", "field", ("page_count",)),
    (r"^adr letter number", "field", ("adr_number",)),
    (r"^acn \(", "field", ("acn",)),
    (r"^passcode", "field", ("passcode",)),
    (r"^claim form type", "field", ("claim_form",)),
    (r"^plan type$", "field", ("plan_type",)),
    (r"^provider identification", "field", ("practice_npi_or_tin",)),
    (r"^(?:provider )?location$", "field", ("practice_address",)),
    (r"^services and charges", "field", ("codes", "billed")),
    (r"^authorization validity dates", "field", ("auth_dates",)),
    (r"^vamc$", "field", ("vamc",)),
    (r"^employer name", "field", ("employer",)),
    (r"^referring health care professional", "field", ("referring",)),
    (r"'ncci' first", "field", ("ncci_first",)),
    (r"^supplement policy number", "field", ("supplement_policy",)),
    (r"^medicare payment and adjustments", "field", ("medicare_payment",)),
    (r"^documentation for untimely filing", "field", ("late_proof",)),
    (r"^waiver of liability", "field", ("waiver",)),
    (r"date (?:the claim was )?accepted by|^date accepted by", "field", ("sent_date",)),
    (r"^diagnosis pointer", "field", ("dx_pointer",)),
    (r"^other \(explain the correction\)", "field", ("reason",)),
    (r"^notes \(written to a cover page\)", "field", ("cover_note",)),
    # claim settings the label states (a replacement claim's frequency code, the payer's id)
    (r"clm05|frequency|resubmission code|type of bill|box 22.*code 7|box 22: code 7|item 22 resubmission", "field", ("frequency_code",)),
    (r"ref02|ref\*f8|ref\*8|original ref|original reference|original (?:payer )?claim number|original icn|payer claim control number: "
     r"original|payer control number|12-digit original", "field", ("payer_claim_number",)),
    (r"ref01|pwk0|payer id|loop 2300 ref01|^(?:state|region) \(|^contractor name", "set", ()),
    # the patient (names switch)
    (r"first 6 letters", "field", ("patient_last6",)),
    (r"last name and first initial|last name \(first 6 letters\) and first initial", "field", ("patient_last_initial",)),
    (r"first initial", "field", ("patient_initial",)),
    (r"^(?:member|patient|beneficiary|enrollee|veteran)(?:'s)? (?:last )?name,? (?:first name|and first)", "field", ("patient_name",)),
    (r"(?:member|patient|beneficiary)(?:'s)? name and (?:id|medicare id|anthem id|medicaid number|identification number|number|"
     r"medicare id number)|medicare number and patient name|cover letter: member", "field", ("patient_name", "member_id")),
    (r"patient name and date of birth", "field", ("patient_name", "dob")),
    (r"member id, name, group number, birthdate", "field", ("member_id", "patient_name", "dob")),
    (r"subscriber name and id", "field", ("insured_name", "member_id")),
    (r"beneficiary first name", "field", ("patient_first",)),
    (r"beneficiary last name|^patient last name$", "field", ("patient_last",)),
    (r"^(?:\d+[a-z]?\. )?(?:name of beneficiary|medicare beneficiary's name|patient/beneficiary name|beneficiary(?:'s)? name|"
     r"patient(?:'s)? name|member(?:'s)? name|member name|enrollee name|veteran name|veteran last name, first name|patient$)",
     "field", ("patient_name",)),
    # member numbers (names switch)
    (r"member id, tax id and service date", "field", ("member_id", "practice_tin", "service_date")),
    (r"claim id, or member id", "field", ("payer_claim_number",)),
    (r"\bmbi\b|medicare (?:beneficiary identifier|number|id)|medicare id$|patient(?:'s)? medicare|beneficiary's medicare|"
     r"member(?:'s)? id|member \(rid\)|subscriber id|cigna id|healthy horizons id|medicaid number|umr member id|"
     r"member identification|account number \(from the cigna id card\)|wellcare id|enrollee .*id|icn, edipi|"
     r"veteran ssn or icn|sponsor", "field", ("member_id",)),
    (r"date of birth|birthdate|birth date|\bdob\b", "field", ("dob",)),
    # the claim's numbers
    (r"cover sheet: provider, npi, service dates, member id", "field", ("practice_name", "practice_npi", "service_date", "member_id")),
    (r"corrected claim line fields|hcpcs code, modifier fields", "field", ("codes", "service_date", "billed")),
    (r"claim, authorization or reference number and date", "field", ("payer_claim_number", "service_date")),
    (r"claim service date/\(icn", "field", ("service_date", "payer_claim_number")),
    (r"date of service, claim number and name of the provider", "field", ("service_date", "payer_claim_number", "rendering_name")),
    (r"claim number and date|claims: claim number and date|claim number and date\(s\) of service|anthem claim number and date",
     "field", ("payer_claim_number", "service_date")),
    (r"patient account number", "field", ("patient_account",)),
    (r"\bicn\b|\bdcn\b|claim (?:#|number|id|control number|icn)|control/claim|claim/document control|claims? id|latest claim id|"
     r"internal control number|aetna claim number|umr claim number|medicare claim number|claim audit number|8\. claim control", "field",
     ("payer_claim_number",)),
    (r"reference(?: number)?/authorization|authorization number|referral number|va authorization|precertification", "field",
     ("authorization",)),
    # dates
    (r"specific service\(s\) or item\(s\) and date", "field", ("codes", "service_date")),
    (r"date of service, procedure code, overpaid", "field", ("service_date", "codes", "refund_amount")),
    (r"date\(s\) of service and billed", "field", ("service_date", "billed")),
    (r"date of initial determination|initial denial (?:notification )?date|date of original claim|ra date|date of the eop|"
     r"remittance date", "field", ("remit_date",)),
    (r"date\(?s?\)? of service|service date|dos (?:from|to)|dates of service|start and end dates|begin date of service|"
     r"claim service date|^\d+\. date of service", "field", ("service_date",)),
    (r"today['\u2019]?s date|^date$|date form completed|date of request|date of reconsideration request", "field", ("today",)),
    # codes
    (r"denied cpt|procedure codes? and modifiers|cpt/hcpc/service|cpt codes? being|cpt/hcpcs|^hcpcs$|cpt code|procedure/type of "
     r"service|item or service you wish|^\d+\. item or service", "field", ("codes",)),
    (r"denial reason code", "field", ("denial_codes",)),
    (r"place of service", "field", ("place_of_service",)),
    (r"diagnosis code|icd-10", "field", ("diagnosis",)),
    (r"group number|^\d+\. plan number", "field", ("group_number",)),
    (r"overpaid amount|refund amount|claim amount refunded|total overpaid", "field", ("refund_amount",)),
    (r"previous appeal or contracted rate dispute reference|reference, interaction or invoice numbers", "field", ("earlier_reference",)),
    (r"date (?:the claim was )?submitted to|date the claim was submitted", "field", ("sent_date",)),
    # amounts
    (r"billed and paid|amount billed and paid", "field", ("billed", "paid")),
    (r"charge amount, actual payment amount, expected", "field", ("billed", "paid", "expected")),
    (r"difference between the amount paid", "field", ("shortfall",)),
    (r"expected amount owed|proposed correct payment", "field", ("expected",)),
    # the claim's total charge, not the item's own line (review routes-r2-15)
    (r"total billed|claim billed amount|total claim billed|billed amount of claim|^total charges", "field", ("claim_total",)),
    (r"billed amount|charge amount|billed charges", "field", ("billed",)),
    (r"^payment amount$", "field", ("paid",)),
    # the practice
    (r"last 5 digits of (?:the )?(?:tax|tin)|last five digits of the federal tax|tax id last 5|\(last 5 digits\)", "field",
     ("practice_tin_last5",)),
    (r"npi, ptan, tax id", "field", ("practice_npi", "practice_ptan", "practice_tin")),
    (r"ptan, npi and tin", "field", ("practice_ptan", "practice_npi", "practice_tin")),
    (r"provider name, tin/npi", "field", ("practice_name", "practice_tin", "practice_npi")),
    (r"provider name, address and tin|provider name/id/address", "field", ("practice_name", "practice_address", "practice_tin")),
    (r"provider name and tax", "field", ("practice_name", "practice_tin")),
    (r"provider npi and tin", "field", ("practice_npi", "practice_tin")),
    (r"(?:your|provider|provider or facility) name, address, phone(?: number)?, email,? (?:and either )?npi or tin|"
     r"provider or facility name, address", "field", ("practice_name", "practice_address", "practice_phone", "practice_email",
                                                     "practice_npi_or_tin")),
    # either number the payer accepts: the NPI when it is set, else the tax ID (review routes-r2-5)
    (r"\bnpi or tin\b|\btin or npi\b", "field", ("practice_npi_or_tin",)),
    (r"name, mailing address, phone, fax and email", "field", ("practice_name", "practice_address", "practice_phone", "practice_fax",
                                                              "practice_email")),
    (r"provider's address, telephone/fax", "field", ("practice_address", "practice_phone", "practice_fax", "practice_email")),
    (r"provider name, address, telephone, fax and contact", "field", ("practice_name", "practice_address", "practice_phone",
                                                                     "practice_fax", "contact_name")),
    (r"contact name, title, address, phone, fax, email", "field", ("contact_name", "practice_address", "practice_phone",
                                                                  "practice_fax", "practice_email")),
    (r"name, address and phone number of the person", "field", ("contact_name", "practice_address", "practice_phone")),
    (r"your name and business telephone|requestor name and phone|contact (?:person )?and phone|contact and phone", "field",
     ("contact_name", "practice_phone")),
    (r"^(?:contact information|your contact information|contact information for)", "field", ("contact_name", "practice_phone",
                                                                                            "practice_email")),
    (r"requestor name, address, email, phone", "field", ("contact_name", "practice_address", "practice_email", "practice_phone")),
    (r"rendering npi|servicing npi", "field", ("rendering_npi",)),
    (r"provider name/address", "field", ("practice_name", "practice_address")),
    (r"signature", "field", ("signature",)),
    (r"contact address", "field", ("practice_address",)),
    (r"physician (?:or other health care professional )?name as listed", "field", ("rendering_name",)),
    (r"\bptan\b|provider number", "field", ("practice_ptan",)),
    (r"\btin\b|tax id|tax identification|federal tax", "field", ("practice_tin",)),
    (r"\bnpi\b|national provider identifier|service location", "field", ("practice_npi",)),
    (r"provider(?:/physician/supplier)? name|billing provider|facility name|facility/group name|health care provider/physician|"
     r"name as it appears on w-9|provider names|^provider$|^\d+\. provider name|appellant|name of claimant", "field", ("practice_name",)),
    (r"billing street address, city, state, zip|street address, (?:city, )?state, zip|provider address|^address$|"
     r"^\d+\. address of the person|provider name/address", "field", ("practice_address",)),
    (r"^city, state, zip", "field", ("practice_city", "practice_state", "practice_zip")),
    (r"^city$", "field", ("practice_city",)),
    (r"^zip code$", "field", ("practice_zip",)),
    (r"state where services were provided|state of service|^state$|state service was performed", "field", ("state_of_service",)),
    (r"fax", "field", ("practice_fax",)),
    (r"e-?mail", "field", ("practice_email",)),
    (r"phone|telephone", "field", ("practice_phone",)),
    (r"contact|caller name|requestor|name of the person requesting|printed name|^name$", "field", ("contact_name",)),
    # the request itself
    (r"reason request submission is late", "field", ("late_reason",)),
    (r"reason for (?:the )?refund|reasons? for overpayment|refund reason|reason\(s\) for overpayment", "field", ("refund_reason",)),
    (r"^(?:\d+\. )?(?:reasons?|why|explanation|position statement|statement of|description of|summary of issue|reasons/rationale|"
     r"specific rationale|what to review|letter stating|brief description|each specific issue|what to adjust|reconsideration request "
     r"explanation|reason and)|reason for (?:the )?(?:appeal|request|dispute|disagreement|review|redetermination|reopening)|"
     r"reason you disagree|reason:? |position statement|specific reasons|^(?:comments|notes|additional information/comments|"
     r"providers message)$", "field", ("reason",)),
]
_RULES = [(re.compile(p, re.I), k, keys) for p, k, keys in FIELD_RULES]
# what fills an empty field, said plainly (never invented)
HINTS = {
    "patient_name": "the patient's name (from EMA)", "patient_first": "the patient's first name (from EMA)",
    "patient_last": "the patient's last name (from EMA)", "patient_initial": "the patient's first initial (from EMA)",
    "patient_last6": "the first 6 letters of the patient's last name (from EMA)",
    "patient_last_initial": "the patient's last name and first initial (from EMA)", "insured_name": "the subscriber's name (from EMA)",
    "member_id": "the member ID (from EMA or the card)", "dob": "the date of birth (from EMA; remittances do not carry it)",
    "payer_claim_number": "the payer's claim number (from its remittance or portal)", "patient_account": "the EMA claim ID",
    "authorization": "the authorization or referral number, if any", "service_date": "the date of service",
    "remit_date": "the date on the payer's remittance", "today": "today's date", "codes": "the code and modifiers",
    "denial_codes": "the denial reason codes", "place_of_service": "the place of service code (from EMA)", "billed": "the billed amount",
    "paid": "the amount paid", "expected": "the amount owed", "shortfall": "the difference", "frequency_code": "7 (replacement)",
    "practice_name": "the practice's name (set PRACTICE_NAME)", "practice_address": "the practice's address (set PRACTICE_ADDRESS)",
    "practice_city": "the city (from PRACTICE_ADDRESS)", "practice_state": "the state (from PRACTICE_ADDRESS)",
    "practice_zip": "the ZIP code (from PRACTICE_ADDRESS)", "practice_tin": "the tax ID (set PRACTICE_TIN)",
    "practice_tin_last5": "the last 5 digits of the tax ID (set PRACTICE_TIN)", "practice_npi": "the group NPI (set PRACTICE_NPI)",
    "practice_ptan": "the Medicare PTAN (set PRACTICE_PTAN)", "practice_phone": "the billing phone (set PRACTICE_PHONE)",
    "practice_fax": "the fax number (set PRACTICE_FAX)", "practice_email": "the billing email (set PRACTICE_EMAIL)",
    "rendering_npi": "the rendering clinician's NPI", "rendering_name": "the rendering clinician's name",
    "state_of_service": "the state where the service was given", "contact_name": "your name", "signature": "your signature",
    "late_reason": "why the request is late, if it is", "refund_reason": "why the money is owed back", "reason": "why the decision is wrong",
    "diagnosis": "the diagnosis codes (from EMA; remittances do not carry them)", "group_number": "the plan's group number (from the card)",
    "refund_amount": "the amount you are returning", "earlier_reference": "the reference number of your earlier request",
    "sent_date": "the date the claim was first sent (EMA or the clearinghouse report)",
    # the fields the store can fill since the review (routes-r2-1 to r2-15) and what the others ask for
    "paid_code": "the code the payer paid", "claim_total": "the claim's total charge, every line (from EMA or the remittance)",
    "practice_npi_or_tin": "the group NPI or the tax ID (set PRACTICE_NPI or PRACTICE_TIN)",
    "rate_expected": "the amount the payer usually pays", "rate_shortfall": "the difference",
    "new_value": "what the line becomes: the corrected units, date of service, procedure code or modifier",
    "msp_amounts": "only when another payer paid first (Medicare Secondary Payer): its allowed, paid and obligated-to-accept amounts; "
                   "otherwise leave it empty",
    "line_number": "the line's number on the claim (from the remittance)",
    "line_position": "the modifier's or diagnosis pointer's position on the line (1 to 4)",
    "overpayment_appeal": "No: this request is not about an overpayment", "appeal_kind": "Non-Overpayment",
    "overpayment_details": "leave it empty: this request is not about an overpayment",
    "appeal_level": "I for a first request, II for a second", "services_rendered": "Yes: the service was given",
    "appellant_role": "Provider", "multiple_claims": "No: one claim per request",
    "adjust_or_void": "Adjustment to correct the claim, Void to cancel it",
    "first_answer_date": "the date of the payer's answer to the first request",
    "refund_check": "the number of the refund check you send", "refund_check_date": "the date on the refund check",
    "payment_stub": "the stub from the payer's refund request letter, if one came",
    "rcn": "the refund control number to write on the check, if the payer gave one",
    "refund_letter": "the payer's refund request letter, if one came", "cia": "only if the practice has a Corporate Integrity "
    "Agreement; otherwise leave it empty",
    "bank_account": "the bank account on the practice's Medicare EFT agreement (CMS-588)",
    "pick": "pick the one that fits from the payer's list (the label lists them)",
    "fee_change": "Yes only when the billed amount changes", "lines_to_change": "the lines to change, as the label says",
    "patient_contact": "the patient's own details (from EMA)", "relationship": "the member's relationship to the patient "
    "(self, spouse, child)",
    "redetermination_number": "the number on the redetermination decision letter",
    "contractor_number": "the contractor number on the redetermination decision letter",
    "appellant": "the provider appealing: the practice, and the name of the person signing",
    "records_included": "Yes when you attach records (see Attach)", "supporting": "the records you attach (see Attach)",
    "page_count": "the number of pages you attach", "adr_number": "the number on the payer's request letter (it must match)",
    "acn": "the attachment control number put in the claim's PWK loop", "passcode": "the passcode from the payer's site (it changes)",
    "claim_form": "HCFA-1500 for a professional claim, UB-04 for a facility claim", "plan_type": "the plan type on the member's ID card",
    "auth_dates": "the authorization's start and end dates (from the VA referral)", "vamc": "the VA medical center on the referral",
    "employer": "the member's employer (from the card or EMA)", "referring": "the referring clinician, if any (from EMA)",
    "ncci_first": "start the reason with NCCI when the denial is an NCCI edit",
    "supplement_policy": "the supplement plan's policy number (from the card)",
    "medicare_payment": "Medicare's payment and adjustments, from its remittance",
    "late_proof": "the proof the claim was filed in time, only if the request is late",
    "waiver": "a signed waiver of liability (a non-contracted provider's appeal)",
    "dx_pointer": "the line's diagnosis pointer (from EMA)", "cover_note": "a short note for the cover page",
}
# what an empty field no rule reads asks for: the label is the payer's own words, and the store holds nothing for it
OTHER_FILL = "as the label asks: RemitWatch holds nothing for it"
NAME_KEYS = frozenset({"patient_name", "patient_first", "patient_last", "patient_initial", "patient_last6", "patient_last_initial",
                       "insured_name", "member_id", "dob", "patient_contact", "relationship"})


@lru_cache(maxsize=4096)
def classify(label: str) -> tuple[str, tuple[str, ...]]:
    """('step' | 'set' | 'field' | 'other', the value keys that fill it) for a field label as the payer prints it."""
    text = " ".join(str(label or "").split())
    for rx, kind, keys in _RULES:
        if rx.search(text):
            return kind, keys
    return "other", ()


def _money(c: Any) -> str:
    return "" if c is None else f"${int(c) / 100:,.2f}"


def _us(d: Any) -> str:
    if not d:
        return ""
    try:
        x = date.fromisoformat(str(d)[:10])
    except (TypeError, ValueError) as exc:
        runlog.caught(exc, "routes.us_date", level="warning")
        return ""
    return x.strftime("%m/%d/%Y")


def _digits(s: Any) -> str:
    return "".join(ch for ch in str(s or "") if ch.isdigit())


def practice() -> dict[str, str]:
    """The practice's own details as the host's settings give them now ('' where one is not set): the same settings the
    appeal letters read (remitwatch.appeals), plus PRACTICE_PTAN. The fax payers answer to is RemitWatch's own line where
    one is set up (SRFAX_CALLER_ID), as on the letters."""
    from .appeals import _fax_line
    e = {k: (os.environ.get(k) or "").strip() for k in SETTINGS}
    addr = e["PRACTICE_ADDRESS"]
    m = re.match(r"^\s*(.*?),\s*([^,]+?),\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)\s*$", addr)
    tin = _digits(e["PRACTICE_TIN"])
    return {"practice_name": e["PRACTICE_NAME"], "practice_address": addr, "practice_street": m.group(1) if m else "",
            "practice_city": m.group(2) if m else "", "practice_state": m.group(3) if m else "", "practice_zip": m.group(4) if m else "",
            "practice_tin": e["PRACTICE_TIN"], "practice_tin_last5": tin[-5:] if len(tin) >= 5 else "", "practice_npi": e["PRACTICE_NPI"],
            "practice_ptan": e["PRACTICE_PTAN"], "practice_phone": e["PRACTICE_PHONE"],
            "practice_fax": _fax_line(os.environ.get("SRFAX_CALLER_ID")) or e["PRACTICE_FAX"], "practice_email": e["PRACTICE_EMAIL"]}


def _one(conn: sqlite3.Connection, sql: str, args: tuple) -> dict:
    try:
        cur = conn.execute(sql, args)
        r = cur.fetchone()
    except sqlite3.Error as exc:
        runlog.caught(exc, "routes.one", level="warning")
        return {}
    return dict(zip([d[0] for d in cur.description], r)) if r else {}


# the head story.next_step puts before the argument it gives the payer, after any deadline or late-window words
_ARGUMENT = re.compile(r"(?:^|[.)] )(?:Appeal to|Dispute it with) [^:]{1,80}: (.+)$", re.S)
_MEDICARE_HOW = " Medicare contractors take no faxed redeterminations: file it on the contractor's portal or by mail."


def _argument(action: str) -> str:
    """The reason itself: the argument the next step puts to the payer, what follows its 'Appeal to <payer>:' or 'Dispute
    it with <payer>:' head, without the deadline words before it and the how-to-file sentence the card says in its own
    words. '' for every other next step: those are the biller's own instructions ("Fix it by asking the Medicare
    contractor for a reopening ...", "decide whether to refund it ...", "Look the claim up ..."), never a reason to give
    the payer, so the field is left to fill."""
    m = _ARGUMENT.search(str(action or ""))
    return m.group(1).replace(_MEDICARE_HOW, "").strip()[:1000] if m else ""


def _ema_person(conn: sqlite3.Connection, claim_id: str | None) -> dict:
    """Who a claim no payer has answered is about, from EMA's rows (as the item's panel says it, api._ema_people): the member
    ID EMA holds for the claim (the policy number on its newest row) and the patient's name from a remittance for another
    claim of the same EMA patient. {} where the copy has none."""
    if not claim_id or conn is None:
        return {}
    try:
        rows = conn.execute("SELECT policy_number, patient_id FROM ema_rows WHERE claim_id=? ORDER BY "
                            "COALESCE(transaction_date, posted_date, service_date, ''), row_index", (str(claim_id),)).fetchall()
    except sqlite3.Error as exc:
        runlog.caught(exc, "routes.ema_person", level="warning")
        return {}
    member, pid = "", ""
    for pol, p in rows:
        member = (pol or "").strip() or member
        pid = (p or "").strip() or pid
    out = {"patient_id": member, "patient_id_qual": "MI"} if member else {}
    if pid:
        try:
            n = conn.execute("SELECT c.patient_last, c.patient_first FROM ema_rows e JOIN claims c ON c.patient_control_number = e.claim_id "
                             "WHERE e.patient_id=? AND COALESCE(c.patient_last,'') != '' ORDER BY c.payment_date DESC LIMIT 1", (pid,)).fetchone()
        except sqlite3.Error as exc:
            runlog.caught(exc, "routes.ema_person_name", level="warning")
            n = None
        if n:
            out.update(patient_last=n[0], patient_first=n[1])
    return out


def _ema_facts(conn: sqlite3.Connection, claim_id: str | None, code: str | None) -> dict[str, str]:
    """What EMA's rows hold on the claim's line that no remittance carries: its diagnosis codes, the authorization number
    and the plan's group number (no patient identifier). {} where the copy has none."""
    if not claim_id or conn is None:
        return {}
    try:
        rows = conn.execute("SELECT icd10, auth_number, group_number, cpt FROM ema_rows WHERE claim_id=? ORDER BY row_index",
                            (str(claim_id),)).fetchall()
    except sqlite3.Error as exc:
        runlog.caught(exc, "routes.ema_facts", level="warning")
        return {}
    mine = [r for r in rows if not code or str(r[3] or "") == str(code)] or rows
    dx = []
    for r in mine:
        for x in re.split(r"[,;\s]+", str(r[0] or "")):
            if x and x not in dx:
                dx.append(x)
    first = lambda i: next((str(r[i]).strip() for r in mine if str(r[i] or "").strip()), "")      # noqa: E731
    return {"diagnosis": ", ".join(dx[:12]), "authorization": first(1), "group_number": first(2)}


def _mods(raw: Any) -> list[str]:
    """A line's modifiers as a list, from the JSON list or the text the store holds."""
    try:
        raw = json.loads(raw) if isinstance(raw, str) and raw.startswith("[") else raw
    except ValueError as exc:
        runlog.caught(exc, "routes.modifiers", level="warning")    # read as the text it is
    return [m for m in (raw if isinstance(raw, list) else re.split(r"[,\s]+", str(raw or ""))) if m]


def _billed_code(f: dict) -> str:
    """The code the claim billed when the payer paid another (a downcode): the payer's own SVC06 on its line, or the code a
    claim file or EMA row shows was billed. Never a code only the charge implies: that one is an inference, not what was
    billed. '' when the payer paid the code billed."""
    ev = f.get("evidence") if isinstance(f.get("evidence"), dict) else {}
    billed = str(f.get("original_procedure_code") or (ev.get("billed_code") if ev.get("billed_source") else "") or "").strip()
    return billed if billed and billed != str(f.get("procedure_code") or "") else ""


def values(conn: sqlite3.Connection, f: dict, claim: dict, line: dict, nx: dict, group: str | None, show_names: bool,
           today: str, st: dict | None = None, request: str | None = None, extra: dict | None = None) -> dict[str, str]:
    """Every value a field can take for this item: the item and its claim from the store, the practice from the settings.
    The patient's name and member number only with the names switch on (REMITWATCH_SHOW_PHI); '' where the store has
    nothing. extra: what the card worked out around the item ('refund_cents', the amount the next step says is owed back;
    'first_day', the day the payer turned down the first request; 'references', the reference numbers a person recorded
    with I sent it or I asked the payer, newest first)."""
    extra = extra or {}
    v: dict[str, str] = {k: "" for k in HINTS}
    if show_names:
        if not claim:
            claim = _ema_person(conn, f.get("patient_control_number"))       # a claim no payer answered: EMA's rows say who
        last, first = str(claim.get("patient_last") or "").strip(), str(claim.get("patient_first") or "").strip()
        v["patient_name"] = ", ".join(x for x in (last, first) if x)
        v["patient_last"], v["patient_first"] = last, first
        v["patient_initial"] = first[:1].upper()
        v["patient_last6"] = re.sub(r"[^A-Za-z]", "", last)[:6].upper()
        v["patient_last_initial"] = f"{last} {first[:1]}".strip()
        ins = ", ".join(x for x in (str(claim.get("insured_last") or "").strip(), str(claim.get("insured_first") or "").strip()) if x)
        v["insured_name"] = ins or v["patient_name"]
        v["member_id"] = codes.member_number(claim) if claim else ""
    mods = _mods(f.get("modifiers") or line.get("modifiers") or "")
    code = str(f.get("procedure_code") or "")
    # a downcode is disputed for the code billed, not the payer's lower one (review routes-r2-1): every code field copies the
    # code billed with its modifiers, and the payer's code is its own fact
    billed_code = _billed_code(f)
    billed_mods = _mods(line.get("original_modifiers")) or mods if billed_code else mods
    carcs = []
    if conn is not None and f.get("line_key"):
        try:
            carcs = [f"{g}-{c}" for g, c in conn.execute("SELECT group_code, reason_code FROM adjustments WHERE line_key=? "
                                                         "AND amount_cents != 0 ORDER BY rowid", (f["line_key"],))]
        except sqlite3.Error as exc:
            runlog.caught(exc, "routes.denial_codes", level="warning")
            carcs = []
    if not carcs and f.get("carc"):
        carcs = [str(f["carc"])]
    route = nx.get("route") or ""
    v.update({
        "payer_claim_number": str(claim.get("payer_claim_control_number") or ""),
        "patient_account": str(f.get("patient_control_number") or ""),
        "authorization": str(claim.get("authorization_number") or claim.get("prior_authorization") or ""),
        "service_date": _us(f.get("service_date")), "remit_date": _us(claim.get("payment_date") or f.get("payment_date")) if claim else "",
        "today": _us(today),
        "codes": "-".join([billed_code or code] + billed_mods) if (billed_code or code) else "",
        "paid_code": "-".join([code] + mods) if billed_code and code else "",
        "denial_codes": ", ".join(dict.fromkeys(carcs + ([str(f["rarc"])] if f.get("rarc") else []))),
        "billed": _money(f.get("charge_cents")), "paid": _money(f.get("paid_cents")),
        # the claim's own total charge (every line), never the item's line (review routes-r2-15)
        "claim_total": _money(claim.get("charge_cents")) if claim.get("charge_cents") is not None else "",
        "expected": _money(f.get("expected_cents")) if f.get("expected_cents") else "",
        "shortfall": _money(f.get("shortfall_cents")) if f.get("shortfall_cents") else "",
        "frequency_code": "7", "rendering_npi": str(f.get("rendering_provider_npi") or ""),
        "rendering_name": str(f.get("rendering_provider_name") or ""),
        "reason": _argument(nx.get("action") or ""),
        # the line's place on the payer's claim (lines.line_seq counts from 1, as Medicare's line numbers do)
        "line_number": str(line.get("line_seq") or "") if line else "",
        # what the store knows the answer to (review routes-r2-7): the card's requests appeal a denial or an underpayment,
        # never an overpayment; one claim per request; a service already given; the provider appealing
        "overpayment_appeal": "No", "appeal_kind": "Non-Overpayment", "services_rendered": "Yes", "appellant_role": "Provider",
        "multiple_claims": "No",
        "appeal_level": {"reconsideration_or_first_appeal": "I", "second_level_appeal": "II"}.get(request or "", ""),
        "adjust_or_void": "Adjustment" if request == "corrected_claim" else "",
        "first_answer_date": _us(extra.get("first_day")),
        # every reference a person recorded with I sent it or I asked the payer (review routes-r2-14)
        "earlier_reference": "; ".join(extra.get("references") or []),
    })
    if route in ("rate", "repricing") and f.get("expected_cents") and code:
        # a payment dispute says what the payer should have paid, and by how much it fell short (review routes-r2-6)
        paid, expected, short = _money(f.get("paid_cents") or 0), _money(f.get("expected_cents")), _money(f.get("shortfall_cents") or 0)
        v["rate_expected"], v["rate_shortfall"] = expected, short if f.get("shortfall_cents") else ""
        v["reason"] = v["reason"] or (
            f"{code} was paid {paid}; your usual rate for it is {expected}. Please reprocess it at that rate (underpaid by {short})."
            if route == "rate" else
            f"{code} was paid {paid} on the earlier fee schedule; the current rate is {expected}. Please reprocess it at the current "
            f"rate (underpaid by {short}).")
    v["group_number"] = str(claim.get("group_number") or "")
    ema = _ema_facts(conn, f.get("patient_control_number"), f.get("procedure_code"))
    v["diagnosis"] = ema.get("diagnosis", "")
    v["authorization"] = v["authorization"] or ema.get("authorization", "")
    v["group_number"] = v["group_number"] or ema.get("group_number", "")
    if route == "refund":
        v["reason"] = ""        # why the money is owed back is the biller's to write: the next step only says what to decide
        # the amount the next step says is owed back (story._refund_step), on every refund way and in Copy all (review r2-2)
        credit = int(extra.get("refund_cents") or (st or {}).get("credit_cents") or 0)
        v["refund_amount"] = _money(credit) if credit > 0 else ""
    pr = practice()
    v.update({k: pr[k] for k in pr if k in v or k.startswith("practice_")})
    v["practice_npi_or_tin"] = pr["practice_npi"] or pr["practice_tin"]
    v["state_of_service"] = CONTRACTOR_STATE.get(group or "", "") or {"KY": "Kentucky", "IN": "Indiana"}.get(pr["practice_state"], "")
    return v


# what an empty reason field asks for, by request (a refund's reason is why the money is owed back, not why a decision
# is wrong)
REQUEST_HINTS = {"overpayment_refund": {"reason": "why the money is owed back"},
                 "medicare_reopening": {"reason": "what to change, and why"},
                 "corrected_claim": {"reason": "what was corrected"},
                 "claim_status_or_reprocess": {"reason": "what you ask the payer"}}


# X12 loop and segment names are not a biller's words (DESIGN rule 7, review d-7): the card says what EMA calls them, the
# claim's resubmission code and the first claim's number. The research file keeps its sources' own words.
_X12_WORDS = (
    (re.compile(r"(?i)\bloop\s*2300\b.*\b(clm0?5(-3)?|frequency)\b.*\b8\b"), "Resubmission code: 7 replaces, 8 voids"),
    (re.compile(r"(?i)\bloop\s*2300\b.*\b(clm0?5(-3)?|frequency)\b"), "Resubmission code: 7 (it replaces the claim)"),
    (re.compile(r"(?i)\bloop\s*2300\b.*\bref0?1\b"), "Original claim number: say it is the payer's claim number"),
    (re.compile(r"(?i)\bloop\s*2300\b.*\bref(0?2)?\b"), "Original claim number: the payer's number for the first claim"),
)


def ema_words(label: str) -> str:
    """A route field's label in EMA's words where its source names an X12 loop or segment; any other label as it is."""
    for rx, words in _X12_WORDS:
        if rx.search(label or ""):
            return words
    return label


def fill(fields: list[str], vals: dict[str, str], hints: dict | None = None) -> tuple[list[dict], list[str]]:
    """(the fields to type, each with its value or what to fill; the steps where to click). hints: what an empty field
    asks for when the request words it differently (REQUEST_HINTS)."""
    out, steps = [], []
    for label in fields or []:
        kind, keys = classify(label)
        if kind == "step":
            steps.append(label)
            continue
        if kind == "set":
            out.append({"label": ema_words(label), "value": "", "keys": [], "fill": "", "set": True})
            continue
        got = [vals.get(k, "") for k in keys]
        value = " · ".join(x for x in got if x)
        missing = [(hints or {}).get(k) or HINTS.get(k, OTHER_FILL) for k, x in zip(keys, got) if not x] if keys else [OTHER_FILL]
        out.append({"label": ema_words(label), "value": value, "keys": list(keys), "fill": "; ".join(missing) if missing else "",
                    "names": bool(set(keys) & NAME_KEYS)})
    return out, steps


# ------------------------------------------------------------------------------------------------ the channels
ATTACH_RULES = [
    (r"\b(?:appeal|reconsideration|redetermination|dispute|reopening) (?:letter|request)\b|request form|letter stating|position "
     r"statement|cover letter$", "letter"),
    (r"\b(?:adr|request|demand|recoupment|refund|denial|decision|determination|rejection) (?:letter|notice|report)|letter received|"
     r"notice\b|\bmrn\b", "payer_letter"),
    (r"remittance|\bra\b|\beob\b|\beop\b|\bpra\b|eomb|explanation of (?:payment|benefits)", "remittance"),
    (r"record|visit note|progress note|operative|documentation|chart|clinical|supporting", "packet"),
    (r"corrected claim|cms-1500|claim copy|copy of the (?:original )?claim|hardcopy claim|paper .*claim", "claim"),
    (r"waiver|appointment of representative|\babn\b|advance beneficiary|designation of representative|signed", "signed_form"),
]
_ATTACH = [(re.compile(p, re.I), k) for p, k in ATTACH_RULES]
ACTION_WORDS = {"payer_portal": "Open the portal", "phone": "Call", "fax": "Start the fax", "mail": "Print the letter",
                "clearinghouse": "Send it from EMA", "email": "Copy the address"}
# the claim's own facts a payer asks for whatever the channel (to find the claim on a portal, to say which one on a call):
# shown beside the channel's fields, each with its copy button, unless the channel's fields already give it
CLAIM_FACTS = [("Patient", "patient_name"), ("Member ID", "member_id"), ("Payer claim number", "payer_claim_number"),
               ("EMA claim ID", "patient_account"), ("Date of service", "service_date"), ("Code and modifiers", "codes"),
               ("Code the payer paid", "paid_code"), ("Billed", "billed"), ("Paid", "paid"),
               ("Should have paid", "rate_expected"), ("Underpaid by", "rate_shortfall"), ("Refund amount", "refund_amount"),
               ("Denial codes", "denial_codes"), ("Remittance date", "remit_date")]


def attachment_kind(label: str) -> str:
    for rx, kind in _ATTACH:
        if rx.search(label or ""):
            return kind
    return ""


def _phone_of(where: str) -> str:
    m = re.search(r"(?<!\d)(\d{3}-\d{3}-\d{4})(?!\d)", where or "")
    return m.group(1) if m else ""


def _source_line(srcs: list[str], checked: str) -> tuple[str, list[dict]]:
    t = table()["sources"]
    got = [dict(t[s], id=s) for s in srcs if s in t]
    if not got:
        return f"Checked {checked}", []
    s = got[0]
    return f"From {s['publisher']}, {s['title']}, checked {checked}", got


def _minutes(m: Any) -> str:
    if m in (None, ""):
        return ""
    try:
        n = int(m)
    except (TypeError, ValueError) as exc:
        runlog.caught(exc, "routes.minutes", level="warning")
        return ""
    return f"about {n} minute{'s' if n != 1 else ''}" if n > 0 else ""


def claim_facts(vals: dict, fields: list[dict]) -> list[dict]:
    """The claim's own facts not already among the channel's fields: only those the store holds (the patient's name and
    member number only with names shown), never a blank to fill."""
    have = {k for x in fields for k in x.get("keys") or []}
    return [{"label": label, "value": vals[k], "keys": [k], "fill": "", "names": k in NAME_KEYS}
            for label, k in CLAIM_FACTS if k not in have and vals.get(k)]


def channel_view(c: dict, vals: dict, checked: str, request: str = "") -> dict:
    """One channel as the card shows it: its main action, its fields filled, its attachments, its source line."""
    fields, steps = fill(c.get("fields") or [], vals, REQUEST_HINTS.get(request))
    kind = c["channel"]
    act: dict[str, str] = {"kind": kind, "label": ACTION_WORDS.get(kind, "")}
    if kind == "payer_portal":
        url = c.get("url") or ""
        doc = bool(re.search(r"\.(?:pdf|ashx|docx?)(?:$|[?#])", url, re.I))
        act.update(url=url if url.startswith("https://") else "", label="Open the payer's guide" if doc else "Open the portal")
        if not act["url"]:
            act["label"] = ""
    elif kind == "phone":
        tel = _phone_of(c.get("where") or "")
        act.update(tel=tel, label=f"Call {tel}" if tel else "")
    elif kind == "fax":
        act["number"] = _phone_of(c.get("where") or "")
    elif kind == "clearinghouse" and request == "claim_status_or_reprocess":
        act["label"] = "Check it in the clearinghouse"
    line, srcs = _source_line(c.get("sources") or [], checked)
    minutes = c.get("minutes")
    return {"rank": c["rank"], "channel": kind, "name": c["name"], "where": c.get("where") or "", "form": c.get("form") or "",
            "minutes": minutes, "notes": c.get("notes") or "", "required": bool(c.get("required")),
            "verified": bool(c.get("verified")), "unverified_why": c.get("unverified_why") or "",
            "fields": fields, "facts": claim_facts(vals, fields), "steps": steps, "action": act,
            "attachments": [{"label": a, "kind": attachment_kind(a)} for a in c.get("attachments") or []],
            "source_line": line, "sources": [{k: s[k] for k in ("url", "title", "publisher", "date")} for s in srcs],
            "line": c["name"] + (f", {_minutes(minutes)}" if _minutes(minutes) else "")}


# what the app's own fax send carries (api.appeals_send, the panel's "Fax it to the payer"): a first-level appeal, to the
# payer's confirmed appeal number (purpose 'appeal'). Any other request's fax is sent by hand
FAX_SENDS = frozenset({"reconsideration_or_first_appeal", "medicare_redetermination"})


def _fax_view(conn: sqlite3.Connection, f: dict, request: str, published: str) -> dict:
    """The published fax number beside the one on file: a fax goes only to a number a person confirmed on the Faxes page
    (remitwatch.fax keeps it), never to the published one by itself. sendable: the app's own send may carry this request,
    a first-level appeal, to the confirmed appeal number it uses (api._payer_number), and that number is the one the payer
    publishes for this request (review routes-r1-4: the card never names one number while its button faxes to another).
    by_hand: the app's send does not carry this request (a second level, a corrected claim, proof of timely filing,
    records, a refund): the card shows the published number to fax by hand after checking it."""
    from . import fax
    level = 2 if request == "second_level_appeal" else 1
    out = {"published": published, "on_file": "", "confirmed": False, "same": False, "sendable": False,
           "by_hand": request not in FAX_SENDS, "words": ""}
    why = fax.no_fax(f.get("payer_group"), level, f.get("payer_name"))
    if why:
        out["words"] = why
        return out
    if fax.shared_group(f.get("payer_group")):
        out["words"] = fax.shared_why(f.get("payer_group"))
        return out
    try:
        # the number the app's send would use for this request: the appeal number for a first-level appeal; a second-level
        # number is shown for a second level, which a person faxes by hand
        nb = (fax.number_for(conn, f.get("payer_group"), f.get("payer_kind"), level)
              if request in FAX_SENDS or level == 2 else {"number": "", "auto_ok": 1})
    except sqlite3.Error as exc:
        runlog.caught(exc, "routes.fax_number", level="warning")    # no number on file: the card says to confirm one
        nb = {"number": ""}
    on = nb.get("number") or ""
    if level == 2 and nb.get("purpose") != "level_2":
        on = ""             # the appeal number number_for falls back to is the first level's, not this request's
    out.update(on_file=fax.pretty(on) if on else "", confirmed=bool(on) and bool(nb.get("auto_ok", 1)),
               same=bool(on) and _digits(on) == _digits(published))
    if out["by_hand"]:
        number = published or "the number the payer publishes"
        if on and out["confirmed"] and (out["same"] or not published):
            out["words"] = (f"RemitWatch's own fax send carries first-level appeals only: fax this one yourself to the confirmed "
                            f"second-level number {out['on_file']}.")
        elif on:
            out["words"] = (f"RemitWatch's own fax send carries first-level appeals only: fax this one yourself. The second-level "
                            f"number on file is {out['on_file']}; the payer publishes {number} for this request. Check which is "
                            "right before you fax.")
        else:
            out["words"] = (f"RemitWatch's own fax send carries first-level appeals only: fax this one yourself to {number}. Confirm "
                            "it before the first fax (call the payer or check its site): a fax with patient information sent to "
                            "a wrong number is a breach.")
        return out
    out["sendable"] = out["confirmed"] and (out["same"] or not published)
    if not on:
        out["words"] = (f"The payer publishes {published or 'a fax number'} for this. Confirm it before the first fax: save it "
                        "on the Faxes page, where a person confirms each payer's number; nothing is faxed to a number nobody "
                        "confirmed.")
    elif not out["confirmed"]:
        out["words"] = "The number on file was changed and nobody has confirmed it yet: confirm it on the Faxes page first."
    elif out["sendable"]:
        out["words"] = f"Faxes go to the confirmed number {out['on_file']}."
    else:
        out["words"] = (f"The confirmed appeal number on file is {out['on_file']}; the payer publishes {published} for this "
                        "request, and RemitWatch's fax goes to the number on file. Check which is right and confirm it on the "
                        "Faxes page before you fax.")
    return out


def _copy_all(head: str, best: dict) -> str:
    """The clean block 'Copy all' puts on the clipboard: the route, then each field that has a value."""
    lines = [head, f"Route: {best['name']}"]
    if best.get("where") and best["channel"] in ("mail", "fax", "phone", "email"):
        lines.append(f"Send to: {best['where']}")
    for x in best["fields"] + best.get("facts", []):
        if x.get("value"):
            lines.append(f"{x['label']}: {x['value']}")
    return "\n".join(lines)


def _status_only(c: dict) -> bool:
    """A claim status inquiry (the X12 276/277 through the clearinghouse): it returns a status code, never the payer's EOB,
    and changes nothing at the payer."""
    return c["channel"] == "clearinghouse" and bool(re.search(r"\b27[67]\b", c.get("name") or ""))


def _request_block(conn: sqlite3.Connection, f: dict, claim: dict, line: dict, nx: dict, st: dict, request: str, route: str,
                   group: str, show_names: bool, today: str, carcs: set[str], extra: dict | None = None) -> dict | None:
    rows = _rows(group, request)
    row, others, chose = pick(rows, f, claim, request, route, carcs)
    if row is None:
        return None
    t = table()
    checked = t["checked"]
    vals = values(conn, f, claim, line, nx, group, show_names, today, st, request, extra)
    acc = [c for c in row["channels"] if c["channel"] != "not_accepted"]
    nac = [c for c in row["channels"] if c["channel"] == "not_accepted"]
    if request == "corrected_claim" and f.get("payer_kind") == "medicare":
        # Medicare returned the claim as unprocessable: the new claim through the clearinghouse is the route, not a reopening
        acc = sorted(acc, key=lambda c: (c["channel"] != "clearinghouse", c["rank"]))
    if request == "claim_status_or_reprocess" and route in ("eob_first", "cob"):
        # the payer's EOB to fetch, or its record of which plan pays first to update: a status inquiry returns neither and
        # changes nothing, so the payer's portal or phone comes first (review routes-r2-9, r2-10)
        acc = sorted(acc, key=lambda c: (_status_only(c), c["rank"]))
    views = [channel_view(c, vals, checked, request) for c in acc]
    if not views:
        return None
    best = views[0]
    if best["channel"] == "fax":
        best["fax"] = _fax_view(conn, f, request, best["action"].get("number") or "")
    for v in views[1:]:
        if v["channel"] == "fax":
            v["fax"] = _fax_view(conn, f, request, v["action"].get("number") or "")
    label = t["groups"].get(group, {}).get("label") or group
    head = f"{label}: {t['requests'][request]}"

    def alt(r: dict) -> dict:
        b = r["channels"][0] if r["channels"] else {}
        # an alternative's easiest way says whether it was confirmed, as the card's own line does (review routes-r1-5)
        return {"when": r["when"] or r["scope"], "scope": r["scope"], "best": b.get("name", ""), "verified": bool(b.get("verified")),
                "unverified_why": b.get("unverified_why") or "", "time_limit": r["time_limit"],
                "time_limit_verified": bool(r.get("time_limit_verified", True))}
    return {"request": request, "request_label": t["requests"][request], "payer": label, "group": group, "variant": row["variant"],
            "variant_why": chose, "scope": row["scope"], "time_limit": row["time_limit"],
            "time_limit_verified": bool(row.get("time_limit_verified", True)), "confidence": row["confidence"],
            "best": best, "others": views[1:],
            "not_accepted": [{"name": c["name"], "notes": c.get("notes") or "", "where": c.get("where") or ""} for c in nac],
            "alternatives": [alt(r) for r in others],
            "copy_all": _copy_all(head, best)}


def _claim_by_account(conn: sqlite3.Connection, f: dict) -> dict:
    """The newest remittance claim of the item's payer for its EMA claim ID, as the item's panel lists its history
    (api.finding_detail): a refund's item comes from EMA's A/R with no claim key of its own, while the payer's claim (its
    claim number, the patient) is in the store (review routes-r2-3). Reversals and superseded files left out; {} when the
    store holds no claim of the item's payer for it."""
    pcn = f.get("patient_control_number")
    if not pcn:
        return {}
    try:
        cur = conn.execute("""SELECT c.* FROM claims c LEFT JOIN transactions t ON t.transaction_key = c.transaction_key
                              WHERE c.patient_control_number=? AND COALESCE(t.superseded, 0)=0 AND COALESCE(c.claim_status_code, '') <> '22'
                              ORDER BY c.payment_date DESC, c.claim_seq DESC""", (str(pcn),))
        cols = [d[0] for d in cur.description]
        found = [dict(zip(cols, r)) for r in cur.fetchall()]
    except sqlite3.Error as exc:
        runlog.caught(exc, "routes.claim_by_account", level="warning")
        return {}
    from . import states
    for c in found:
        if states._same_payer(codes.payer_group_kind(c.get("payer_name"), c.get("claim_filing_indicator"))[0], f.get("payer_group")):
            return c
    return {}


def _refund_cents(f: dict, explain: dict) -> int:
    """The amount the next step says is owed back (story._refund_step, its 'Overpaid by'): the state's credit, else (a
    payment the payer made twice) the extra payment the archive found, else the open balance below zero."""
    st = explain.get("state") or {}
    if int(st.get("credit_cents") or 0) > 0:
        return int(st["credit_cents"])
    if st.get("rule") == "possible_overpayment":
        from . import states
        return int((states._archive_hint(f) or {}).get("possible_overpayment_cents") or 0)
    open_cents = int(((explain.get("story") or {}).get("balance") or {}).get("open_cents") or 0)
    return -open_cents if open_cents < 0 else 0


def _references(conn: sqlite3.Connection, finding_id: str) -> list[str]:
    """The reference numbers a person recorded on this item (the 'Reference X' that I sent it and I asked the payer put at
    the end of their note), newest first: what a next level asks for from earlier attempts (review routes-r2-14)."""
    try:
        rows = conn.execute("SELECT note FROM finding_events WHERE finding_id=? AND note LIKE '%Reference %' ORDER BY at DESC, id DESC",
                            (finding_id,)).fetchall()
    except sqlite3.Error as exc:
        runlog.caught(exc, "routes.references", level="warning")
        return []
    out: list[str] = []
    for (note,) in rows:
        m = re.search(r"(?:^|\s)Reference (\S.*?)\.?\s*$", str(note or ""))
        if m and m.group(1) not in out:
            out.append(m.group(1))
    return out[:6]


def _facts_card(head: str, facts: list[dict]) -> str:
    return "\n".join([head] + [f"{x['label']}: {x['value']}" for x in facts if x.get("value")])


def card(conn: sqlite3.Connection, finding_id: str, *, explain: dict | None = None, today: str | None = None,
         show_names: bool = False) -> dict | None:
    """The drawer's route card for one item, or None when its work goes to no payer (or the item is not on this copy).
    explain: story.explain's answer for the item (its next step and state), so the card follows the same route."""
    from .runtime import practice_today
    today = today or practice_today()
    explain = explain or {}
    nx, st = explain.get("next_step") or {}, explain.get("state") or {}
    f = _one(conn, "SELECT * FROM findings WHERE finding_id=?", (finding_id,))
    if not f:
        return None
    try:
        f["evidence"] = json.loads(f.get("evidence") or "{}") if isinstance(f.get("evidence"), str) else (f.get("evidence") or {})
    except ValueError as exc:
        runlog.caught(exc, "routes.evidence", level="warning")
        f["evidence"] = {}
    req = _request(conn, f, st, nx, today)
    request, why, also = req["request"], req["why"], req["also"]
    if request is None:
        return None
    route = nx.get("route") or st.get("route") or ""
    if f.get("claim_key"):
        claim = _one(conn, "SELECT * FROM claims WHERE claim_key=?", (f["claim_key"],))
    else:
        claim = _claim_by_account(conn, f) if route == "refund" else {}
    line = _one(conn, "SELECT * FROM lines WHERE line_key=?", (f["line_key"],)) if f.get("line_key") else {}
    group, not_covered = research_group(f, claim)
    t = table()
    first = req["first"] or {}
    extra = {"refund_cents": _refund_cents(f, explain) if route == "refund" else 0, "first_day": first.get("day"),
             "references": _references(conn, finding_id)}
    base = {"checked": t["checked"], "request": request, "request_label": t["requests"][request], "request_why": why,
            "route": route or None, "deadline": nx.get("deadline"), "deadline_basis": nx.get("basis"),
            "names_shown": bool(show_names), "payer_group": f.get("payer_group"), "fallback": None,
            # what the card's "I sent it" writes in its note, so a later "no" is read for what was sent (sent_request)
            "sent_words": SENT_WORDS.get(request, "")}
    if request == "claim_status_or_reprocess":
        # a claim no payer has answered is due at the payer by its first-filing limit, which the item's own date can run past
        ff = _first_filing(f, st)
        if ff and ff["date"] >= today and (not base["deadline"] or ff["date"] < base["deadline"]):
            base["deadline"], base["deadline_basis"] = ff["date"], f"The first-filing limit: {ff['basis']}"
    single = (t.get("single_level") or {}).get(group or "")
    if request == "second_level_appeal" and single:
        # a payer that offers one level of appeal (HealthSpring for participating providers): no second level to file
        line_words, _src = _source_line(single["sources"], t["checked"])
        base.update(fallback=single["text"], single_level=True, source_line=line_words, deadline=None, deadline_basis=None)
        return base
    carcs = _carcs(conn, f)
    block = (_request_block(conn, f, claim, line, nx, st, request, route, group, show_names, today, carcs, extra)
             if group else None)
    if block is None:
        base["fallback"] = (not_covered + " " + FALLBACK) if not_covered else FALLBACK
        # a payer the table does not cover still asks for the claim's own facts: they are shown, Copy all copies them,
        # and I sent it records how it went (review routes-r2-12)
        vals = values(conn, f, claim, line, nx, group, show_names, today, st, request, extra)
        base["facts"] = claim_facts(vals, [])
        base["copy_all"] = _facts_card(f"{f.get('payer_group') or 'The payer'}: {t['requests'][request]}", base["facts"])
        return base
    base.update(block)
    if request == "second_level_appeal":
        # the item's date is the first level's window; the second level runs on its own clock from the payer's answer to
        # the first, so the card names that limit and the answer's day, and shows no date that could read as more time
        day = first.get("day")
        limit = str(base.get("time_limit") or "").strip()
        base["deadline"], base["deadline_basis"] = None, None
        base["deadline_note"] = (f"This level has its own limit: {limit}{'' if limit.endswith('.') else '.'}"
                                 + (f" The payer answered the first level on {_long(day)}." if day else
                                    " It counts from the date of the payer's answer to the first level."))
    if request == "medicare_redetermination" and req["after"] == "medicare_reopening" and first.get("denied"):
        # the redetermination of the first decision runs on that decision's clock (120 days from receipt of its remittance),
        # never from the reopening's answer (review routes-r1-1)
        remit = str(claim.get("payment_date") or f.get("payment_date") or "")[:10] or None
        rule = codes.appeal_rule(f.get("payer_group"), f.get("payer_kind"), **codes.rule_facts(f))
        end = codes.window_end(remit, rule) if remit else None
        base["deadline"] = end
        base["deadline_basis"] = (codes.rule_at(remit, rule) or {}).get("label") if end else None
        base["deadline_note"] = ("Medicare turned the reopening down" + (f" on {_long(first['day'])}" if first.get("day") else "")
                                 + ". A decision on a reopening cannot be appealed, so the first decision is: the "
                                 "redetermination's limit counts from that decision's remittance"
                                 + (f" ({_long(remit)})" if remit else "") + ", not from the reopening's answer.")
    if also:
        extra_block = _request_block(conn, f, claim, line, nx, st, also, route, group, show_names, today, carcs, extra)
        if extra_block:
            base["also"] = {k: extra_block[k] for k in ("request", "request_label", "best", "others", "not_accepted", "time_limit",
                                                        "time_limit_verified", "copy_all")}
            base["also"]["why"] = "The first-filing limit is near: have the proof that the claim reached the payer in time."
    return base
