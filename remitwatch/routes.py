"""
Payer routes: the one place that picks how an item's request reaches its payer.

The owner's ask (2026-11-28): route each request the first time by the easiest way the payer accepts, with the least work
for the biller: the fields filled in, ready to copy. remitwatch/payer_routes.json holds the researched table (each payer
group and request type, its accepted channels ranked easiest first, the ones it refuses, the time limit, the sources and
the day it was checked: plans/tools/routes/build_payer_routes.py builds it). This module reads the item, its state and its
next step (remitwatch.states, remitwatch.story) and gives the drawer's route card:

- the request type, from the item's route (states.route_of) and its facts: an Original Medicare appeal is a
  redetermination, or a reopening when the fix is clerical (appeals._medicare_route); a second appeal after a recorded
  first denial is the second level; a call is a claim status request (with the timely filing proof beside it when the
  first-filing limit is near); a correction is a corrected claim; money owed back is a refund. Routes with no payer step
  (post, bill the patient, contract, policy, fees, decide, wait, nothing) get no card;
- the payer group (codes.payer_group_kind) mapped to the research's, with the plan's scope (Original Medicare or Medicare
  Advantage, commercial or Medicaid, BlueCard, TriWest's regions, MHS Indiana...); no match gives a plain fallback;
- the channels in rank order, the not-accepted ones kept as warnings, and each channel's fields filled from the item and
  its claim where the store holds them (the patient's name and member number only with the names switch on,
  REMITWATCH_SHOW_PHI) and from the practice's settings (PRACTICE_*), never invented: a field with no value says what to
  fill;
- a fax channel shows the published number and the number on file (remitwatch.fax.number_for): nothing is ever faxed to a
  number a person has not confirmed on the Faxes page (the app's own fax send does the sending, to the confirmed number).

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

from . import codes

TABLE_PATH = Path(__file__).with_name("payer_routes.json")
FALLBACK = "No checked route for this payer yet. Use its provider portal, then note what worked."
# the routes whose work goes to no payer: they get no card (states.route_of)
NO_CARD = frozenset({"post", "bill_patient", "bill_secondary", "contract", "policy", "fees", "decide", "wait", "nothing", "status"})
# claims that never reached the payer (rejected before it, or never sent): no payer route, a new claim from EMA
NEVER_REACHED = frozenset({"REJECTED_PRE_PAYER", "NOT_SENT"})
# a first-filing limit this close (or past) puts the timely filing proof beside a claim status request
NEAR_FILING_DAYS = 30
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
        except sqlite3.Error:
            pass                # a copy with no adjustments table: the item's own reason stands
    return out - {""}


def _first_level_denied(conn: sqlite3.Connection | None, f: dict, st: dict) -> bool:
    """Whether the payer already turned down a first-level request on this item: its answer after an appeal a person
    sent or faxed came back unpaid (states rule work_answered), or a person logged 'The payer said no' after marking it
    Sent. A corrected claim sent again is not an appeal: its denial is worked as a new denial."""
    if f.get("actionable") == "correct_and_resubmit":
        return False
    a, w = st.get("answered") or {}, st.get("work") or {}
    if st.get("rule") == "work_answered" and a and int(a.get("paid") or 0) <= 0 and w.get("kind") in ("sent", "fax", "appeal"):
        return True
    if conn is None:
        return False
    try:
        rows = conn.execute("SELECT to_status, note FROM finding_events WHERE finding_id=? ORDER BY at, id", (f["finding_id"],)).fetchall()
    except sqlite3.Error:
        return False
    sent = False
    for to, note in rows:
        if to == "appealed":
            sent = True
        elif sent and to == "in_progress" and str(note or "").startswith("The payer said no"):
            return True
    return False


def _days(a: str | None, b: str | None) -> int | None:
    try:
        return (date.fromisoformat(str(b)[:10]) - date.fromisoformat(str(a)[:10])).days
    except (TypeError, ValueError):
        return None


def request_for(conn: sqlite3.Connection | None, f: dict, st: dict, nx: dict, today: str) -> tuple[str | None, str, str | None]:
    """(the request type, why in words, a second request to show beside it or None). None when the item's work goes to
    no payer."""
    route = nx.get("route") or st.get("route") or ""
    if not route or route in NO_CARD:
        return None, "", None
    if f.get("signal") in NEVER_REACHED:
        # the claim never reached the payer: it is fixed and sent again from EMA as a new claim, and the payer's own
        # corrected-claim route (a replacement of a claim it has, frequency 7 with its claim number) does not apply
        return None, "", None
    medicare = f.get("payer_kind") == "medicare"
    carcs = _carcs(conn, f)
    timely = f.get("signal") == "DENIED_TIMELY_FILING" or "29" in carcs
    if route == "refund":
        return "overpayment_refund", "Money is owed back to the payer.", None
    if route == "call":
        if _first_level_denied(conn, f, st):
            return "second_level_appeal", "The payer turned down the first appeal: the next level.", None
        also = None
        left = _days(today, st.get("deadline")) if f.get("signal") in ("NO_ANSWER", "REJECTED_PRE_PAYER") else None
        if left is not None and left <= NEAR_FILING_DAYS:
            also = "timely_filing_proof"
        return "claim_status_or_reprocess", "Ask the payer where the claim stands, or to reprocess it.", also
    if route in ("eob_first", "cob"):
        return "claim_status_or_reprocess", ("Get the payer's answer first." if route == "eob_first" else
                                            "Have the payer update which plan pays first, then reprocess."), None
    if timely and route in ("appeal", "corrected", "rate"):
        return "timely_filing_proof", "A timely filing denial: send the proof the claim reached the payer in time.", None
    if route in ("appeal", "rate", "repricing", "corrected") and _first_level_denied(conn, f, st):
        return "second_level_appeal", "The payer turned down the first appeal: the next level.", None
    if medicare:
        from .appeals import _medicare_route
        how = _medicare_route(dict(f, evidence=f.get("evidence") or {}))
        if route in ("rate", "repricing"):
            return "medicare_reopening", "Original Medicare fixes a payment by reopening the claim.", None
        if how == "reopening":
            return "medicare_reopening", "A clerical error Original Medicare fixes by reopening, not by appeal.", None
        if how == "new_claim":
            return "corrected_claim", "Medicare returned the claim as unprocessable: a new claim, not an appeal.", None
        return "medicare_redetermination", "Original Medicare's first-level appeal is a redetermination.", None
    if route == "corrected":
        return "corrected_claim", "A corrected claim replaces the one the payer has.", None
    return "reconsideration_or_first_appeal", ("Ask the payer to reprocess the payment (a payment dispute)." if route in ("rate", "repricing")
                                               else "The payer's first-level review of its decision."), None


# ------------------------------------------------------------------------------------------------ the plan's row
TRICARE_CLINICAL = frozenset({"50", "55", "56", "96", "204", "167", "B5", "272"})
TRICARE_CODING = frozenset({"97", "234", "236", "B15", "B20", "231"})


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
    elif grp == "Ambetter (Centene)" and request == "reconsideration_or_first_appeal" \
            and carcs & (codes.MEDICAL_NECESSITY_CARCS | {"197", "198", "15", "39", "62", "243", "288"}):
        want, why = "clinical", "A medical necessity or authorization denial goes through the member appeal process."
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
    # selections and the patient's own contact details a person fills (the store has none of them)
    (r"^type:", "set", ()),
    (r"^action\b|^type\b|^fee schedule change|^lines:|^bank information|beneficiary phone|member address|beneficiary state|"
     r"state of residence|relationship of the subscriber", "field", ()),
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
    (r"billed amount|total billed|claim billed amount|charge amount|billed charges|total claim billed|9\. total billed", "field",
     ("billed",)),
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
     r"provider or facility name, address", "field", ("practice_name", "practice_address", "practice_phone", "practice_email", "practice_npi")),
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
}
NAME_KEYS = frozenset({"patient_name", "patient_first", "patient_last", "patient_initial", "patient_last6", "patient_last_initial",
                       "insured_name", "member_id", "dob"})


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
    try:
        x = date.fromisoformat(str(d)[:10])
    except (TypeError, ValueError):
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
    except sqlite3.Error:
        return {}
    return dict(zip([d[0] for d in cur.description], r)) if r else {}


def _strip_lead(action: str) -> str:
    """The next step's argument, the reason itself: without the deadline words put in front of it (story._next), its
    'Appeal to <payer>:' head, and the how-to-file sentence the card says in its own words."""
    s = re.sub(r"^(?:Only \d+ days? left \(by [^)]*\): do it today\. |Today is the last day \([^)]*\): do it today\. |"
               r"The safe date passed on [^:]*: (?:send it|act) now\. )", "", str(action or "")).strip()
    s = re.sub(r"^(?:Appeal to|Dispute it with) [^:]{1,80}: ", "", s)
    s = s.replace(" Medicare contractors take no faxed redeterminations: file it on the contractor's portal or by mail.", "")
    return s[:1000]


def _ema_person(conn: sqlite3.Connection, claim_id: str | None) -> dict:
    """Who a claim no payer has answered is about, from EMA's rows (as the item's panel says it, api._ema_people): the member
    ID EMA holds for the claim (the policy number on its newest row) and the patient's name from a remittance for another
    claim of the same EMA patient. {} where the copy has none."""
    if not claim_id or conn is None:
        return {}
    try:
        rows = conn.execute("SELECT policy_number, patient_id FROM ema_rows WHERE claim_id=? ORDER BY "
                            "COALESCE(transaction_date, posted_date, service_date, ''), row_index", (str(claim_id),)).fetchall()
    except sqlite3.Error:
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
        except sqlite3.Error:
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
    except sqlite3.Error:
        return {}
    mine = [r for r in rows if not code or str(r[3] or "") == str(code)] or rows
    dx = []
    for r in mine:
        for x in re.split(r"[,;\s]+", str(r[0] or "")):
            if x and x not in dx:
                dx.append(x)
    first = lambda i: next((str(r[i]).strip() for r in mine if str(r[i] or "").strip()), "")      # noqa: E731
    return {"diagnosis": ", ".join(dx[:12]), "authorization": first(1), "group_number": first(2)}


def values(conn: sqlite3.Connection, f: dict, claim: dict, line: dict, nx: dict, group: str | None, show_names: bool,
           today: str, st: dict | None = None) -> dict[str, str]:
    """Every value a field can take for this item: the item and its claim from the store, the practice from the settings.
    The patient's name and member number only with the names switch on (REMITWATCH_SHOW_PHI); '' where the store has
    nothing."""
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
    mods = f.get("modifiers") or line.get("modifiers") or ""
    try:
        mods = json.loads(mods) if isinstance(mods, str) and mods.startswith("[") else mods
    except ValueError:
        pass
    mods = [m for m in (mods if isinstance(mods, list) else re.split(r"[,\s]+", str(mods))) if m]
    code = str(f.get("procedure_code") or "")
    carcs = []
    if conn is not None and f.get("line_key"):
        try:
            carcs = [f"{g}-{c}" for g, c in conn.execute("SELECT group_code, reason_code FROM adjustments WHERE line_key=? "
                                                         "AND amount_cents != 0 ORDER BY rowid", (f["line_key"],))]
        except sqlite3.Error:
            carcs = []
    if not carcs and f.get("carc"):
        carcs = [str(f["carc"])]
    v.update({
        "payer_claim_number": str(claim.get("payer_claim_control_number") or ""),
        "patient_account": str(f.get("patient_control_number") or ""),
        "authorization": str(claim.get("authorization_number") or claim.get("prior_authorization") or ""),
        "service_date": _us(f.get("service_date")), "remit_date": _us(f.get("payment_date") or claim.get("payment_date")),
        "today": _us(today), "codes": "-".join([code] + mods) if code else "",
        "denial_codes": ", ".join(dict.fromkeys(carcs + ([str(f["rarc"])] if f.get("rarc") else []))),
        "billed": _money(f.get("charge_cents")), "paid": _money(f.get("paid_cents")),
        "expected": _money(f.get("expected_cents")) if f.get("expected_cents") else "",
        "shortfall": _money(f.get("shortfall_cents")) if f.get("shortfall_cents") else "",
        "frequency_code": "7", "rendering_npi": str(f.get("rendering_provider_npi") or ""),
        "rendering_name": str(f.get("rendering_provider_name") or ""),
        "reason": _strip_lead(nx.get("action") or ""),
    })
    v["group_number"] = str(claim.get("group_number") or "")
    ema = _ema_facts(conn, f.get("patient_control_number"), f.get("procedure_code"))
    v["diagnosis"] = ema.get("diagnosis", "")
    v["authorization"] = v["authorization"] or ema.get("authorization", "")
    v["group_number"] = v["group_number"] or ema.get("group_number", "")
    if (nx.get("route") or "") == "refund":
        v["refund_reason"] = v["reason"]
        credit = int((st or {}).get("credit_cents") or 0)
        v["refund_amount"] = _money(credit) if credit > 0 else ""
    pr = practice()
    v.update({k: pr[k] for k in pr if k in v or k.startswith("practice_")})
    v["state_of_service"] = CONTRACTOR_STATE.get(group or "", "") or {"KY": "Kentucky", "IN": "Indiana"}.get(pr["practice_state"], "")
    return v


def fill(fields: list[str], vals: dict[str, str]) -> tuple[list[dict], list[str]]:
    """(the fields to type, each with its value or what to fill; the steps where to click)."""
    out, steps = [], []
    for label in fields or []:
        kind, keys = classify(label)
        if kind == "step":
            steps.append(label)
            continue
        if kind == "set":
            out.append({"label": label, "value": "", "keys": [], "fill": "", "set": True})
            continue
        got = [vals.get(k, "") for k in keys]
        value = " · ".join(x for x in got if x)
        missing = [HINTS.get(k, "fill it in") for k, x in zip(keys, got) if not x] if keys else ["by hand"]
        out.append({"label": label, "value": value, "keys": list(keys), "fill": "; ".join(missing) if missing else "",
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
               ("Billed", "billed"), ("Paid", "paid"), ("Denial codes", "denial_codes"), ("Remittance date", "remit_date")]


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
    try:
        n = int(m)
    except (TypeError, ValueError):
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
    fields, steps = fill(c.get("fields") or [], vals)
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


def _fax_view(conn: sqlite3.Connection, f: dict, request: str, published: str) -> dict:
    """The published fax number beside the one on file: a fax goes only to a number a person confirmed on the Faxes page
    (remitwatch.fax keeps it; the app's own fax send uses it), never to the published one by itself."""
    from . import fax
    level = 2 if request == "second_level_appeal" else 1
    out = {"published": published, "on_file": "", "confirmed": False, "same": False, "words": ""}
    why = fax.no_fax(f.get("payer_group"), level, f.get("payer_name"))
    if why:
        out["words"] = why
        return out
    if fax.shared_group(f.get("payer_group")):
        out["words"] = fax.shared_why(f.get("payer_group"))
        return out
    try:
        nb = fax.number_for(conn, f.get("payer_group"), f.get("payer_kind"), level)
    except sqlite3.Error:
        nb = {"number": ""}
    on = nb.get("number") or ""
    out.update(on_file=fax.pretty(on) if on else "", confirmed=bool(on) and bool(nb.get("auto_ok", 1)),
               same=bool(on) and _digits(on) == _digits(published))
    if not on:
        out["words"] = (f"The payer publishes {published or 'a fax number'} for this. Confirm it before the first fax: save it "
                        "on the Faxes page, where a person confirms each payer's number; nothing is faxed to a number nobody "
                        "confirmed.")
    elif not out["confirmed"]:
        out["words"] = "The number on file was changed and nobody has confirmed it yet: confirm it on the Faxes page first."
    elif out["same"] or not published:
        out["words"] = f"Faxes go to the confirmed number {out['on_file']}."
    else:
        out["words"] = (f"The confirmed number on file is {out['on_file']}; the payer publishes {published} for this request. "
                        "Check which is right before you fax.")
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


def _request_block(conn: sqlite3.Connection, f: dict, claim: dict, line: dict, nx: dict, st: dict, request: str, route: str,
                   group: str, show_names: bool, today: str, carcs: set[str]) -> dict | None:
    rows = _rows(group, request)
    row, others, chose = pick(rows, f, claim, request, route, carcs)
    if row is None:
        return None
    t = table()
    checked = t["checked"]
    vals = values(conn, f, claim, line, nx, group, show_names, today, st)
    acc = [c for c in row["channels"] if c["channel"] != "not_accepted"]
    nac = [c for c in row["channels"] if c["channel"] == "not_accepted"]
    if request == "corrected_claim" and f.get("payer_kind") == "medicare":
        # Medicare returned the claim as unprocessable: the new claim through the clearinghouse is the route, not a reopening
        acc = sorted(acc, key=lambda c: (c["channel"] != "clearinghouse", c["rank"]))
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
    return {"request": request, "request_label": t["requests"][request], "payer": label, "group": group, "variant": row["variant"],
            "variant_why": chose, "scope": row["scope"], "time_limit": row["time_limit"],
            "time_limit_verified": bool(row.get("time_limit_verified", True)), "confidence": row["confidence"],
            "best": best, "others": views[1:],
            "not_accepted": [{"name": c["name"], "notes": c.get("notes") or "", "where": c.get("where") or ""} for c in nac],
            "alternatives": [{"when": r["when"] or r["scope"], "scope": r["scope"], "best": (r["channels"][0]["name"] if r["channels"] else "")}
                             for r in others],
            "copy_all": _copy_all(head, best)}


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
    except ValueError:
        f["evidence"] = {}
    request, why, also = request_for(conn, f, st, nx, today)
    if request is None:
        return None
    claim = _one(conn, "SELECT * FROM claims WHERE claim_key=?", (f["claim_key"],)) if f.get("claim_key") else {}
    line = _one(conn, "SELECT * FROM lines WHERE line_key=?", (f["line_key"],)) if f.get("line_key") else {}
    group, not_covered = research_group(f, claim)
    t = table()
    base = {"checked": t["checked"], "request": request, "request_label": t["requests"][request], "request_why": why,
            "route": nx.get("route") or st.get("route"), "deadline": nx.get("deadline"), "deadline_basis": nx.get("basis"),
            "names_shown": bool(show_names), "payer_group": f.get("payer_group"), "fallback": None}
    carcs = _carcs(conn, f)
    block = _request_block(conn, f, claim, line, nx, st, request, base["route"] or "", group, show_names, today, carcs) if group else None
    if block is None:
        base["fallback"] = (not_covered + " " + FALLBACK) if not_covered else FALLBACK
        return base
    base.update(block)
    if also:
        extra = _request_block(conn, f, claim, line, nx, st, also, base["route"] or "", group, show_names, today, carcs)
        if extra:
            base["also"] = {k: extra[k] for k in ("request", "request_label", "best", "others", "not_accepted", "time_limit",
                                                  "time_limit_verified", "copy_all")}
            base["also"]["why"] = "The first-filing limit is near: have the proof that the claim reached the payer in time."
    return base
