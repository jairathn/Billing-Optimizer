"""
Faxing appeals, and reading what payers fax back.

    provider    SRFax (HIPAA plans, a BAA for healthcare accounts; an HTTPS POST API that is polled, so there is no
                incoming webhook to set up): SRFAX_ACCESS_ID, SRFAX_ACCESS_PWD, SRFAX_CALLER_ID (the practice's fax
                number), SRFAX_SENDER_EMAIL (SRFax's delivery notices), SRFAX_URL (default its secure web service).
    numbers     each payer's appeal fax number ('appeal') and, where the payer has one, its second-level number
                ('level_2'), typed once and confirmed by a person. Nothing is faxed to a number nobody confirmed: a fax with
                patient information sent to a wrong number is a breach.
    letters     a letter with an unfilled prompt in brackets is never faxed (letter_blanks).
    follow-ups  an appeal sent with no payment and no decision after the payer's time to answer (60 days for Medicare and
                Medicare Advantage, 30 for commercial plans) gets a second request, then another every week until the payer
                answers; an appeal the payer upheld goes to its next level; a request for records gets the packet again; an
                approval not paid 30 days later gets a payment request, then weekly. Twelve unanswered requests at one level,
                or a second level upheld, go to a person (a call, a complaint to the Department of Insurance).
    retries     a fax the payer's line did not take (busy, no answer) is sent again, three tries in all.
    incoming    each fax received is read by Claude (the practice's BAA with Anthropic covers it): what it is, the payer's
                decision, claim numbers, the patient and dates of service, any deadline; then matched to the findings it
                answers.

The API (remitwatch.api) does the work around these: the packet, the status, the journal. Nothing here touches a store
directly except the read-only queries in followups and match_inbound.
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any

SRFAX_URL = "https://www.srfax.com/SRF_SecWebSvc.php"
TIMEOUT = (10, 120)
# days a payer has to answer an appeal before a second request is sent: Medicare and Medicare Advantage decide a
# redetermination or reconsideration within 60 days; Kentucky's prompt-pay law gives commercial plans 30
WAIT_DAYS = {"medicare": 60, "medicare_advantage": 60, "medicaid": 30, "commercial": 30, "supplement": 30, "other": 45}
REPEAT_DAYS = 7                 # after that, another request every week until the payer answers
MAX_REQUESTS = 12               # unanswered requests at one level before a person takes it (a call, a complaint to the regulator)
PAID_WAIT_DAYS = 30             # an approval with no payment this long after it: a payment request, then weekly
MAX_LEVEL = 2                   # levels faxed without a person: the payer's first and second; past them a person decides
MAX_ATTEMPTS = 3                # tries for a fax the payer's line did not take (busy, no answer)
APPEALABLE = ("note", "rule", "rate")       # kinds of work an appeal letter settles (insights.WORK_TYPES)
BLANK = re.compile(r"\[[^\]\n]{2,600}\]")
# what remitwatch.notes.scrub puts in place of an identifier before a note goes to Claude: one quoted back in a paragraph is
# not a prompt left for the biller (the note itself, attached, has the real text)
SCRUBBED = {"[REDACTED]", "[SSN]", "[DOB]", "[ID]", "[PHONE]", "[NUMBER]", "[DATE]", "[EMAIL]", "[ADDRESS]"}


class FaxError(Exception):
    pass


def digits(number: str | None) -> str:
    """A US fax number as its ten digits, or '' when it is not one."""
    d = re.sub(r"\D", "", number or "")
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    return d if len(d) == 10 and d[0] not in "01" else ""


def pretty(number: str | None) -> str:
    d = digits(number)
    return f"({d[:3]}) {d[3:6]}-{d[6:]}" if d else (number or "")


def letter_blanks(text: str | None) -> list[str]:
    """The prompts in brackets a letter still has ('[practice name]', '[describe the separate problem]'): a letter with
    any of them is not ready to fax."""
    return sorted({b for b in BLANK.findall(text or "") if b not in SCRUBBED})


# --------------------------------------------------------------------------- the fax service
class Provider:
    name = "none"

    def send(self, to: str, pdf: bytes, filename: str) -> str:
        raise NotImplementedError

    def status(self, provider_id: str) -> dict:
        raise NotImplementedError

    def inbox(self, days: int) -> list[dict]:
        raise NotImplementedError

    def fetch(self, key: str) -> bytes:
        raise NotImplementedError

    def check(self) -> dict:
        raise NotImplementedError


class SRFax(Provider):
    """SRFax's web service: one HTTPS POST per call, `action` naming it, the account's ID and password with every call;
    the answer is {Status: Success|Failed, Result}."""
    name = "srfax"

    def __init__(self, access_id: str, access_pwd: str, caller_id: str, sender_email: str, url: str = SRFAX_URL, session: Any = None):
        if session is None:
            import requests
            session = requests.Session()
        self.http, self.url = session, url
        self.access_id, self.access_pwd = access_id, access_pwd
        self.caller_id, self.sender_email = digits(caller_id), sender_email

    def _call(self, action: str, **params) -> Any:
        data = {"action": action, "access_id": self.access_id, "access_pwd": self.access_pwd, "sResponseFormat": "JSON", **params}
        try:
            r = self.http.post(self.url, data=data, timeout=TIMEOUT)
        except Exception as exc:
            raise FaxError(f"SRFax did not answer ({type(exc).__name__}).") from exc
        try:
            j = r.json()
        except Exception as exc:
            raise FaxError(f"SRFax answered {r.status_code} without a readable reply.") from exc
        if str(j.get("Status", "")).lower() != "success":
            raise FaxError(f"SRFax refused {action}: {str(j.get('Result') or 'no reason given')[:200]}")
        return j.get("Result")

    def send(self, to: str, pdf: bytes, filename: str) -> str:
        num = digits(to)
        if not num:
            raise FaxError(f"{to!r} is not a US fax number.")
        res = self._call("Queue_Fax", sCallerID=self.caller_id, sSenderEmail=self.sender_email, sFaxType="SINGLE",
                         sToFaxNumber="1" + num, sFileName_1=filename, sFileContent_1=base64.b64encode(pdf).decode("ascii"))
        fid = str(res if not isinstance(res, (list, dict)) else (res[0] if isinstance(res, list) and res else res)).strip()
        if not fid:
            raise FaxError("SRFax queued the fax without an ID.")
        return fid

    def status(self, provider_id: str) -> dict:
        res = self._call("Get_FaxStatus", sFaxDetailsID=provider_id, sFaxDetailID=provider_id)
        r = res[0] if isinstance(res, list) and res else res if isinstance(res, dict) else {}
        word = str(r.get("SentStatus") or r.get("Status") or "").lower()
        state = "failed" if "fail" in word else "sent" if word.startswith("sent") or word == "success" else "queued"
        pages = r.get("Pages") or r.get("PagesSent")
        return {"state": state, "pages": int(pages) if str(pages or "").isdigit() else None,
                "error": str(r.get("ErrorCode") or "")[:200] if state == "failed" else "", "at": r.get("DateSent") or ""}

    def inbox(self, days: int) -> list[dict]:
        end = date.today()
        res = self._call("Get_Fax_Inbox", sPeriod="RANGE", sStartDate=(end - timedelta(days=days)).strftime("%Y%m%d"), sEndDate=end.strftime("%Y%m%d"))
        out = []
        for r in res if isinstance(res, list) else []:
            name = str(r.get("FileName") or "")
            if name:
                out.append({"key": name, "received_at": str(r.get("Date") or ""), "caller": digits(str(r.get("CallerID") or "")) or str(r.get("CallerID") or ""),
                            "pages": int(r["Pages"]) if str(r.get("Pages") or "").isdigit() else None})
        return out

    def fetch(self, key: str) -> bytes:
        res = self._call("Retrieve_Fax", sFaxFileName=key, sDirection="IN", sFaxFormat="PDF")
        raw = res[0] if isinstance(res, list) and res else res
        try:
            return base64.b64decode(raw if isinstance(raw, str) else "")
        except Exception as exc:
            raise FaxError("SRFax returned a fax that is not readable.") from exc

    def check(self) -> dict:
        steps = []
        try:
            self._call("Get_Fax_Inbox", sPeriod="RANGE", sStartDate=date.today().strftime("%Y%m%d"), sEndDate=date.today().strftime("%Y%m%d"))
            steps.append({"step": "Sign-in", "ok": True, "detail": "SRFax accepted the account ID and password"})
        except FaxError as exc:
            steps.append({"step": "Sign-in", "ok": False, "detail": str(exc)})
            return {"ok": False, "steps": steps}
        steps.append({"step": "Sender", "ok": bool(self.caller_id and self.sender_email),
                      "detail": f"faxes go out from {pretty(self.caller_id)}; notices to {self.sender_email}" if self.caller_id and self.sender_email
                      else "set SRFAX_CALLER_ID (the practice's SRFax number) and SRFAX_SENDER_EMAIL"})
        return {"ok": all(s["ok"] for s in steps), "steps": steps}


def provider(env: dict | None = None) -> Provider | None:
    """The fax service the settings name, or None when none is set up (packets are then built but not sent)."""
    e = os.environ if env is None else env
    if e.get("SRFAX_ACCESS_ID") and e.get("SRFAX_ACCESS_PWD"):
        return SRFax(e["SRFAX_ACCESS_ID"].strip(), e["SRFAX_ACCESS_PWD"].strip(), e.get("SRFAX_CALLER_ID", ""), e.get("SRFAX_SENDER_EMAIL", ""),
                     (e.get("SRFAX_URL") or SRFAX_URL).strip())
    return None


def configured(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return bool(e.get("SRFAX_ACCESS_ID") and e.get("SRFAX_ACCESS_PWD"))


# --------------------------------------------------------------------------- letters as PDFs
def letter_pdf(title: str, paragraphs: list[str]) -> bytes:
    """A one- or two-page letter (a follow-up's cover), as the packets are printed."""
    from xml.sax.saxutils import escape

    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    buf = io.BytesIO()
    st = getSampleStyleSheet()
    flow = [Paragraph(escape(title), st["Heading2"]), Spacer(1, 8)]
    for p in paragraphs:
        flow += [Paragraph(escape(p).replace("\n", "<br/>"), st["BodyText"]), Spacer(1, 6)]
    SimpleDocTemplate(buf, pagesize=letter, leftMargin=54, rightMargin=54, topMargin=54, bottomMargin=54, title=title).build(flow)
    return buf.getvalue()


def merge_pdfs(*pdfs: bytes) -> bytes:
    from pypdf import PdfReader, PdfWriter
    w = PdfWriter()
    for p in pdfs:
        if p:
            for page in PdfReader(io.BytesIO(p)).pages:
                w.add_page(page)
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


def ordinal(k: int) -> str:
    words = {1: "First", 2: "Second", 3: "Third", 4: "Fourth", 5: "Fifth", 6: "Sixth", 7: "Seventh", 8: "Eighth", 9: "Ninth", 10: "Tenth",
             11: "Eleventh", 12: "Twelfth", 13: "Thirteenth"}
    return words.get(k) or f"{k}th"


def page_count(pdf: bytes) -> int:
    from pypdf import PdfReader
    try:
        return len(PdfReader(io.BytesIO(pdf)).pages)
    except Exception:
        return 0


def cover_sheet(to: str, to_number: str, practice: dict, pages: int, subject: str | None) -> bytes:
    """The fax's first page: who it is for and from, how many pages, where to answer, and the privacy notice. No patient
    is named on it: whoever takes it off the machine sees only this page."""
    rows = [f"Date: {date.today().isoformat()}", f"To: {to}", f"Fax: {pretty(to_number)}",
            f"From: {practice.get('name') or ''}, Billing Department", f"Phone: {practice.get('phone') or ''}",
            f"Answer by fax to: {practice.get('fax') or ''}", f"Pages: {pages}, this one included"]
    if subject:
        rows.append(f"Re: {subject}")
    rows.append("This fax holds protected health information, sent for payment of a claim as HIPAA permits (45 CFR 164.506). "
                f"If it reached you in error, call {practice.get('phone') or 'the sender'} and destroy it.")
    return letter_pdf("Fax", rows)


def follow_up_letter(action: str, finding: dict, sent: list[dict], decision: dict | None, practice: dict, n: int = 1) -> tuple[str, list[str]]:
    """The letter on top of a follow-up: what was sent and when, what is asked now. action: 'second_request',
    'next_level', 'records', 'payment'; n: which request of its kind this is (a third request says so)."""
    today = date.today().isoformat()
    first = min((s.get("created_at") or "")[:10] for s in sent) if sent else ""
    hist = "; ".join(f"{(s.get('created_at') or '')[:10]} to {pretty(s.get('number'))}" + (f" (fax {s['provider_id']})" if s.get("provider_id") else "")
                     for s in sorted(sent, key=lambda x: x.get("created_at") or ""))
    who = practice.get("name") or "[practice name]"
    claim = finding.get("patient_control_number") or ""
    pcn = finding.get("payer_claim_control_number") or ""
    head = (f"Claim {claim}" + (f" (your claim number {pcn})" if pcn else "") + f", date of service {finding.get('service_date') or ''}, "
            f"procedure {finding.get('procedure_code') or ''}.")
    titles = {"second_request": f"{ordinal(n + 1)} request: no decision on our appeal",
              "next_level": "Request for the next level of review", "records": "Records you asked for",
              "payment": "Approved appeal not yet paid" + (f" ({ordinal(n).lower()} request)" if n > 1 else "")}
    asks = {
        "second_request": f"We faxed an appeal of this claim on {first} and have had neither a decision nor a payment. Please process it "
                          "and send your decision. If your records show the appeal was not received, treat this fax, with the appeal "
                          "attached again, as the appeal, received on the date of our first fax."
                          + (" We will keep sending this request every week until the claim is decided. Interest accrues on a clean "
                             "claim paid late." if n > 1 else ""),
        "next_level": "Your decision on our appeal upheld the denial. We ask for the next level of review of this claim. The original "
                      "appeal, with the documentation, is attached again." + (f" Your decision of {decision.get('date')} is on file."
                                                                                  if decision and decision.get("date") else ""),
        "records": "You asked for records for this claim. The visit note and our appeal are attached.",
        "payment": "Your decision approved this appeal" + (f" on {decision.get('date')}" if decision and decision.get("date") else "")
                   + ". No payment has reached us. Please pay the claim as approved.",
    }
    paras = [f"Date: {today}", f"From: {who}", head, asks[action], f"Faxes sent so far: {hist}." if hist else "",
             "Please reply by fax to " + pretty(practice.get("fax") or "") + "." if practice.get("fax") else ""]
    return titles[action], [p for p in paras if p]


# --------------------------------------------------------------------------- follow-ups
def _day(v: str | None) -> str:
    return (v or "")[:10]


def _decision(doc: str | None) -> dict:
    try:
        d = json.loads(doc or "{}")
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


def followups(conn: sqlite3.Connection, today: str | None = None) -> list[dict]:
    """Every appeal that needs the next push, and why: items marked Sent whose appeal went out by fax (a fax the payer's
    line took, or one still being tried), with no payment since. Read only. action is what to fax: 'second_request',
    'next_level', 'records', 'payment'; or 'person' when it is past what is faxed without one."""
    today = today or datetime.now(timezone.utc).date().isoformat()
    t = date.fromisoformat(today)
    conn.row_factory = sqlite3.Row
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "faxes" not in have:
        return []
    out_by: dict[str, list[dict]] = {}
    in_by: dict[str, list[dict]] = {}
    for r in conn.execute("SELECT * FROM faxes WHERE finding_id IS NOT NULL ORDER BY created_at"):
        r = dict(r)
        (out_by if r["direction"] == "out" else in_by).setdefault(r["finding_id"], []).append(r)
    days = lambda d: (t - date.fromisoformat(d)).days       # noqa: E731
    due = []
    for fid, outs in out_by.items():
        f = conn.execute("""SELECT finding_id, status, payer_group, payer_kind, signal, title, service_date, shortfall_cents
                            FROM findings WHERE finding_id=?""", (fid,)).fetchone()
        if not f or f["status"] != "appealed":
            continue
        sent = [o for o in outs if o["status"] != "failed"]
        if not sent:
            # nothing got through: the busy line is tried again (api._fax_refresh); after the last try, a person checks the number
            if any(int(_decision(o["doc"]).get("attempts") or 1) >= MAX_ATTEMPTS for o in outs):
                due.append({"finding_id": fid, "action": "person", "n": 1, "level": int(outs[-1]["level"] or 1),
                            "why": f"the payer's line never took the fax ({MAX_ATTEMPTS} tries): check the number on the Faxes page",
                            "payer_group": f["payer_group"], "payer_kind": f["payer_kind"], "title": f["title"], "service_date": f["service_date"],
                            "shortfall_cents": f["shortfall_cents"], "first_sent": _day(outs[0]["created_at"]),
                            "last_sent": _day(outs[-1]["created_at"]), "faxes": 0, "decision": None})
            continue
        level = max(int(o["level"] or 1) for o in sent)
        at_level = [o for o in sent if int(o["level"] or 1) == level]
        # order by the moment (an answer to the first level can come in on the day the second is asked for, or in the same
        # second, when one daily run reads it and then sends what it calls for: an answer is after a fax only if later); count days
        first_at = min(o["created_at"] or "" for o in at_level)
        first, last = _day(first_at), max(_day(o["created_at"]) for o in sent)
        # the payer's answers since this level's appeal went out
        answers = [dict(_decision(i["doc"]), received=_day(i["created_at"]), at=i["created_at"] or "") for i in in_by.get(fid, [])]
        answers = [a for a in answers if a["at"] > first_at and (a.get("decision") in ("upheld", "overturned", "partially_overturned")
                                                                   or a.get("document_type") == "records_request")]
        latest = max(answers, key=lambda a: a["at"], default=None)
        after = lambda purpose, a: [o for o in at_level if o["purpose"] == purpose and (o["created_at"] or "") >= a["at"]]   # noqa: E731
        action, why, n = None, "", 1
        if latest and latest.get("document_type") == "records_request":
            if not after("records", latest):
                action, why = "records", f"the payer asked for records ({latest['received']})"
        elif latest and latest.get("decision") == "upheld":
            if level >= MAX_LEVEL:
                action, why = "person", f"level {level} upheld on {latest['received']}: the next step (an independent review, the Department of Insurance, a hearing) is a person's call"
            else:
                action, why = "next_level", f"the payer upheld the denial ({latest['received']})"
        elif latest:            # approved, in full or in part, and not paid yet (a payment closes the item)
            asked = after("payment", latest)
            if days(latest["received"]) >= PAID_WAIT_DAYS and (not asked or days(max(_day(o["created_at"]) for o in asked)) >= REPEAT_DAYS):
                action, why, n = "payment", f"approved {latest['received']}, no payment in {days(latest['received'])} days", len(asked) + 1
        else:
            wait = WAIT_DAYS.get(f["payer_kind"] or "other", 45)
            asked = [o for o in at_level if o["purpose"] == "second_request"]
            if days(first) >= wait and days(last) >= REPEAT_DAYS:
                if len(asked) >= MAX_REQUESTS:
                    action, why = "person", f"{len(asked) + 1} faxes at level {level} without an answer"
                else:
                    action, why, n = "second_request", f"no decision or payment {days(first)} days after the appeal of {first}", len(asked) + 1
        if action:
            due.append({"finding_id": fid, "action": action, "why": why, "n": n, "level": level + (1 if action == "next_level" else 0),
                        "payer_group": f["payer_group"], "payer_kind": f["payer_kind"], "title": f["title"], "service_date": f["service_date"],
                        "shortfall_cents": f["shortfall_cents"], "first_sent": first, "last_sent": last, "faxes": len(sent),
                        "decision": {k: latest.get(k) for k in ("decision", "decision_date", "received", "document_type")} if latest else None})
    return sorted(due, key=lambda d: (d["action"] == "person", d["last_sent"], -(d["shortfall_cents"] or 0)))


# --------------------------------------------------------------------------- incoming faxes
INBOUND_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["document_type", "decision", "payer", "claim_numbers", "patient_last", "patient_first", "service_dates",
                 "decision_date", "deadline", "amount", "summary"],
    "properties": {
        "document_type": {"type": "string", "enum": ["appeal_decision", "records_request", "payment_notice", "denial", "acknowledgement", "other"]},
        "decision": {"type": "string", "enum": ["overturned", "partially_overturned", "upheld", "pending", "none"]},
        "payer": {"type": "string"},
        "claim_numbers": {"type": "array", "items": {"type": "string"}},
        "patient_last": {"type": "string"}, "patient_first": {"type": "string"},
        "service_dates": {"type": "array", "items": {"type": "string"}},
        "decision_date": {"type": "string"}, "deadline": {"type": "string"},
        "amount": {"type": "string"},
        "summary": {"type": "string"},
    },
}
INBOUND_RUBRIC = ("You read faxes a medical practice receives from health insurers. Return what the fax is and what it says, "
                  "exactly as written: document_type; the payer's decision on an appeal (overturned, partially_overturned, upheld, "
                  "pending, or none when the fax decides nothing); the payer's name; every claim number, reference or ICN on it; the "
                  "patient's last and first name; each date of service (YYYY-MM-DD); the decision's date and any deadline it sets "
                  "(YYYY-MM-DD, or empty); any amount approved or paid; and a two-sentence summary of what it decides and what it asks "
                  "the practice to do, without the patient's name or any member, claim or account number (those go in their own fields). "
                  "Leave a field empty rather than guess.")


def classify(pdf: bytes, client: Any = None) -> dict:
    """What an incoming fax is, read by Claude from the fax itself."""
    if client is None:
        import anthropic
        client = anthropic.Anthropic()
    from .notes import MODEL
    content = [{"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": base64.standard_b64encode(pdf).decode("ascii")}},
               {"type": "text", "text": "Read this fax and return the fields."}]
    with client.messages.stream(model=MODEL, max_tokens=4000, system=INBOUND_RUBRIC,
                                output_config={"effort": "medium", "format": {"type": "json_schema", "schema": INBOUND_SCHEMA}},
                                messages=[{"role": "user", "content": content}]) as stream:
        message = stream.get_final_message()
    text = next(b.text for b in message.content if b.type == "text")
    return json.loads(text)


def _norm(s: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def match_inbound(conn: sqlite3.Connection, cls: dict) -> list[str]:
    """The findings an incoming fax answers: by a claim number on it (the payer's claim number or the practice's claim
    ID, as the remittance has them), else by the patient's last name and a date of service; appeals already sent first.
    Read only."""
    conn.row_factory = sqlite3.Row
    nums = {_norm(n) for n in cls.get("claim_numbers") or [] if len(_norm(n)) >= 5}
    found: list[str] = []
    if nums:
        for r in conn.execute("""SELECT f.finding_id, f.status, f.patient_control_number, c.payer_claim_control_number
                                 FROM findings f LEFT JOIN claims c ON c.claim_key = f.claim_key
                                 WHERE f.patient_control_number IS NOT NULL OR c.payer_claim_control_number IS NOT NULL"""):
            if _norm(r["patient_control_number"]) in nums or _norm(r["payer_claim_control_number"]) in nums \
                    or any(_norm(r["patient_control_number"])[:9] == n[:9] for n in nums if len(n) >= 9):
                found.append(r["finding_id"])
    if not found and cls.get("patient_last") and cls.get("service_dates"):
        days = [d[:10] for d in cls["service_dates"] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d[:10])]
        if days:
            q = ",".join("?" * len(days))
            for r in conn.execute(f"""SELECT f.finding_id, c.patient_last FROM findings f JOIN claims c ON c.claim_key = f.claim_key
                                      WHERE f.service_date IN ({q})""", days):
                if _norm(r["patient_last"]) and _norm(r["patient_last"]) == _norm(cls["patient_last"]):
                    found.append(r["finding_id"])
    if not found:
        return []
    order = {r[0]: r[1] for r in conn.execute(f"SELECT finding_id, status FROM findings WHERE finding_id IN ({','.join('?' * len(found))})", found)}
    return sorted(set(found), key=lambda i: (order.get(i) != "appealed", i))[:20]
