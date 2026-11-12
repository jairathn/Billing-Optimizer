"""
Clinical-note appeal builder.

Takes the visit note for a finding (PDF, Word, text or pasted text), reviews it
against the coding rules that decide the dispute, and produces an appeal packet:
the letter with the clinical paragraphs written from the note, the review
table with verbatim quotes, the remittance evidence and the note itself, as
one PDF that can be downloaded or emailed.

The review is done by Claude through the Anthropic API when ANTHROPIC_API_KEY
is set. Notes are protected health information: turn this on only after a
business associate agreement covers the API organisation (see
docs/OPERATIONS.md). Known patient identifiers are removed from the text
before it is sent. Without a key the builder still assembles the packet from
the template letter and attaches the note.

The review is written to be honest. If the note does not support the level
billed it says so and recommends against the appeal, because an appeal the
chart cannot back loses and invites an audit.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import smtplib
import sqlite3
import zipfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from email.message import EmailMessage
from typing import Any

from .appeals import DOWNCODE_LETTER_SIGNALS, PRACTICE, build_packet, money
from .codes import EM_CODES

MODEL = os.environ.get("REMITWATCH_NOTE_MODEL", "claude-opus-5-5")
MAX_NOTE_CHARS = 60000

PACKETS_DDL = """
CREATE TABLE IF NOT EXISTS appeal_packets (
    packet_id TEXT PRIMARY KEY, finding_id TEXT, created_at TEXT, mode TEXT, model TEXT,
    subject TEXT, letter TEXT, analysis TEXT, evidence TEXT, attachments TEXT,
    note_name TEXT, note_sha256 TEXT, note_kind TEXT, note_blob BLOB, pdf BLOB,
    emailed_to TEXT, emailed_at TEXT, letter_redacted TEXT
);
CREATE INDEX IF NOT EXISTS ix_packets_finding ON appeal_packets(finding_id);
"""
MAX_TEXT_CHARS = 2_000_000          # nothing longer than this is parsed or scanned
MAX_DOCX_XML_BYTES = 30 * 1024 * 1024


def ensure_packets(conn: sqlite3.Connection) -> None:
    conn.executescript(PACKETS_DDL)
    if "letter_redacted" not in {r[1] for r in conn.execute("PRAGMA table_info(appeal_packets)")}:
        conn.execute("ALTER TABLE appeal_packets ADD COLUMN letter_redacted TEXT")
        conn.commit()


class _TextOnly(HTMLParser):
    """Collect the visible text of an HTML document in one linear pass (no regular expressions over the whole file)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in ("br", "p", "div", "tr", "li", "h1", "h2", "h3", "h4"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        elif tag in ("p", "div", "tr", "li"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)

# --------------------------------------------------------------------------- text extraction


def extract_text(filename: str, data: bytes) -> tuple[str, str]:
    """Return (text, kind). kind is pdf, pdf_scanned, docx or text. Raises ValueError for a file that cannot be read safely."""
    name = (filename or "").lower()
    if name.endswith(".pdf") or data[:5] == b"%PDF-":
        from pypdf import PdfReader
        try:
            reader = PdfReader(io.BytesIO(data))
            text = "\n\n".join((pg.extract_text() or "") for pg in reader.pages[:200])[:MAX_TEXT_CHARS]
        except Exception:
            text = ""
        return (text, "pdf") if len(text.strip()) >= 200 else (text, "pdf_scanned")
    if name.endswith(".docx") or data[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                sizes = {i.filename: i.file_size for i in z.infolist()}
            if "word/document.xml" not in sizes:
                raise ValueError("not a Word document")
            if sum(sizes.values()) > 4 * MAX_DOCX_XML_BYTES or sizes["word/document.xml"] > MAX_DOCX_XML_BYTES:
                raise ValueError("the Word document is too large once unpacked")
            import docx
            d = docx.Document(io.BytesIO(data))
            parts = [p.text for p in d.paragraphs]
            for t in d.tables:
                for row in t.rows:
                    parts.append(" | ".join(c.text for c in row.cells))
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(f"the Word document could not be read ({type(exc).__name__})") from exc
        return "\n".join(parts)[:MAX_TEXT_CHARS], "docx"
    text = data[:MAX_TEXT_CHARS * 4].decode("utf-8", errors="replace")[:MAX_TEXT_CHARS]
    if name.endswith(".rtf") or text.lstrip().startswith("{\\rtf"):
        text = re.sub(r"\\'[0-9a-f]{2}", "", text)
        text = re.sub(r"\\[a-z]+-?\d* ?", "", text)
        text = text.replace("{", "").replace("}", "")
    if name.endswith((".html", ".htm")) or "<html" in text[:500].lower():
        parser = _TextOnly()
        parser.feed(text)
        text = "".join(parser.parts)
    return text, "text"


def _ident_pattern(ident: str) -> tuple[str, int] | None:
    """Regular expression for one known identifier, tolerant of the ways a note reformats it."""
    ident = ident.strip()
    if len(ident) < 2:
        return None
    if any(ch.isdigit() for ch in ident):
        # member, claim and record numbers: the same characters with spaces or hyphens allowed between any two of them
        chars = [re.escape(ch) for ch in ident if not ch.isspace() and ch != "-"]
        if len(chars) < 4:
            return None
        return r"(?<![A-Za-z0-9])" + r"[\s-]*".join(chars) + r"(?![A-Za-z0-9])", re.I
    words = [re.escape(w) for w in ident.split()]
    if len(ident) <= 3:
        # a two or three letter surname (Ng, Li, Roe): whole word, as capitalised, so "ng/mL" and "li" inside prose survive
        alts = {ident, ident.upper(), ident.title()}
        return r"\b(?:" + "|".join(re.escape(a) for a in sorted(alts)) + r")\b", 0
    return r"\b" + r"\s+".join(words) + r"\b", re.I


def scrub(text: str, identifiers: list[str]) -> str:
    """Remove the identifiers we know (names, member, claim and record numbers) and common identifier patterns before the
    note leaves the server. This lowers the exposure; it is not a guarantee of de-identification, which is why a business
    associate agreement must cover the API before the review is switched on."""
    out = text
    for ident in sorted({i.strip() for i in identifiers if i and i.strip()}, key=len, reverse=True):
        pat = _ident_pattern(ident)
        if pat:
            out = re.sub(pat[0], "[REDACTED]", out, flags=pat[1])
    out = re.sub(r"\b\d{3}[- ]\d{2}[- ]\d{4}\b", "[SSN]", out)
    out = re.sub(r"(?i)\b(DOB|date of birth|birth ?date)\s*[:#]?\s*\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", r"\1: [DOB]", out)
    out = re.sub(r"(?i)\b(MRN|medical record (?:number|no\.?|#)|chart (?:number|no\.?|#)|account (?:number|no\.?|#)|member(?: id)?|subscriber(?: id)?|policy (?:number|no\.?|#)|SSN|social security(?: number)?)\s*[:#]?\s*[A-Z0-9][A-Z0-9 -]{3,}[A-Z0-9]",
                 r"\1: [ID]", out)
    out = re.sub(r"\(?\b\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b", "[PHONE]", out)
    out = re.sub(r"(?<![\d.$])\b\d{9,10}\b(?!\.\d|\d)", "[NUMBER]", out)       # a bare 9 or 10 digit number: SSN or phone written without separators
    out = re.sub(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b", "[DATE]", out)
    out = re.sub(r"(?i)\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.? \d{1,2},? \d{4}\b", "[DATE]", out)
    out = re.sub(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b", "[EMAIL]", out)
    out = re.sub(r"(?i)\b\d{1,5}\s+[A-Z0-9 .]{3,30}\b(street|st|avenue|ave|road|rd|drive|dr|lane|ln|boulevard|blvd|court|ct|highway|hwy)\b\.?", "[ADDRESS]", out)
    return out


def scrub_obj(obj: Any, identifiers: list[str]) -> Any:
    """Apply scrub() to every string inside a nested structure (the review quotes the note, so it gets the same treatment)."""
    if isinstance(obj, str):
        return scrub(obj, identifiers)
    if isinstance(obj, list):
        return [scrub_obj(x, identifiers) for x in obj]
    if isinstance(obj, dict):
        return {k: scrub_obj(v, identifiers) for k, v in obj.items()}
    return obj


# --------------------------------------------------------------------------- the review

RUBRIC = """You are a certified professional coder auditing a dermatology office note for a payment appeal. Apply the AMA CPT
evaluation and management guidelines for office or other outpatient visits (2021 revision, as amended) and the CMS National Correct
Coding Initiative policy on modifier 25.

Level selection. The level is set by medical decision making (MDM) or by total time on the date of the encounter, whichever
supports the higher level. MDM has three elements and the level is the one met or exceeded by two of the three:
- Number and complexity of problems addressed. Minimal: one self-limited or minor problem. Low: two or more self-limited or minor
  problems; one stable chronic illness; one acute uncomplicated illness or injury. Moderate: one or more chronic illnesses with
  exacerbation, progression or side effects of treatment; two or more stable chronic illnesses; one undiagnosed new problem with
  uncertain prognosis; one acute illness with systemic symptoms; one acute complicated injury. High: a chronic illness with severe
  exacerbation, or an acute or chronic illness or injury that poses a threat to life or bodily function.
- Amount and complexity of data reviewed and analyzed. Minimal or none. Limited: two items from review of external notes, review
  of each unique test result, ordering of each unique test; or an assessment requiring an independent historian. Moderate: three
  such items (including an independent historian), or independent interpretation of a test performed by another physician, or
  discussion of management with an external physician or appropriate source. Extensive: two of those three categories.
  A test ordered and resulted at the same encounter counts once. Pathology the same physician bills separately is not counted.
- Risk of complications or morbidity or mortality of patient management. Minimal. Low. Moderate: prescription drug management;
  decision regarding minor surgery with identified patient or procedure risk factors; decision regarding elective major surgery
  without identified risk factors; diagnosis or treatment significantly limited by social determinants of health. High: drug
  therapy requiring intensive monitoring for toxicity; decision regarding elective major surgery with risk factors; decision
  regarding emergency major surgery.
Straightforward MDM supports 99202 and 99212, low supports 99203 and 99213, moderate supports 99204 and 99214, high supports
99205 and 99215. A stable chronic illness means the patient is at the treatment goal; a chronic illness that is not at goal is
not stable. A problem is addressed when it is evaluated or treated at the encounter.

Time. Total time personally spent by the billing practitioner on the date of the encounter, when documented: 99202 15 to 29
minutes, 99203 30 to 44, 99204 45 to 59, 99205 60 to 74; 99212 10 to 19, 99213 20 to 29, 99214 30 to 39, 99215 40 to 54. Time
spent performing separately billed procedures does not count.

Modifier 25. A visit on the same day as a minor procedure is separately payable only when it is significant and separately
identifiable: it addresses a different problem, or work well beyond the usual pre-service and post-service work of the
procedure. The decision to perform a minor procedure is included in the procedure. A different diagnosis is not required.

Rules for this review:
1. Use only what the note says. Quote the note word for word for every element you credit. Never infer findings, history,
   counselling, time or decisions that are not written down.
2. Be exact about what is missing. If the documentation does not support the level billed, say so plainly and recommend against
   the appeal. An unsupported appeal harms the practice.
3. Work that belongs to a separately billed procedure must not be counted toward the visit level.
4. The clinical paragraphs you write go into a letter to the payer under the practice's name. Write them in plain, formal
   sentences, in the first person plural, with the quoted phrases from the note in quotation marks. No headings, no bullet
   symbols, no dashes used as punctuation, no adjectives of emphasis. One paragraph per element that supports the claim.
5. Identifiers in the note have been replaced with placeholders such as [REDACTED]. Do not try to restore them."""

ELEMENT = {
    "type": "object",
    "properties": {
        "level": {"type": "string", "enum": ["minimal", "low", "moderate", "high", "not_documented", "not_applicable"]},
        "rationale": {"type": "string"},
        "quotes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["level", "rationale", "quotes"], "additionalProperties": False,
}
SCHEMA = {
    "type": "object",
    "properties": {
        "visit_summary": {"type": "string", "description": "Two or three sentences on what was seen and done, without identifiers."},
        "problems": ELEMENT, "data": ELEMENT, "risk": ELEMENT,
        "mdm_level": {"type": "string", "enum": ["straightforward", "low", "moderate", "high", "not_applicable"]},
        "time_documented": {"type": "string", "description": "Total time as written in the note, or 'none'."},
        "supported_code": {"type": "string", "description": "The highest visit code the note supports, or 'not_applicable' for a procedure dispute."},
        "supports_billed": {"type": "string", "enum": ["yes", "no", "borderline"]},
        "modifier_25": {
            "type": "object",
            "properties": {
                "relevant": {"type": "boolean"},
                "separately_identifiable": {"type": "string", "enum": ["yes", "no", "borderline", "not_applicable"]},
                "rationale": {"type": "string"},
                "quotes": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["relevant", "separately_identifiable", "rationale", "quotes"], "additionalProperties": False,
        },
        "procedure_support": {"type": "string", "description": "For a procedure dispute: what the note documents for each disputed procedure. Otherwise 'not_applicable'."},
        "strengths": {"type": "array", "items": {"type": "string"}},
        "gaps": {"type": "array", "items": {"type": "string"}},
        "recommendation": {"type": "string", "enum": ["appeal", "appeal_with_caution", "do_not_appeal", "correct_and_resubmit"]},
        "recommendation_reason": {"type": "string"},
        "clinical_paragraphs": {"type": "array", "items": {"type": "string"}},
        "documentation_tips": {"type": "array", "items": {"type": "string"}, "description": "What the clinician could document next time so this does not recur."},
    },
    "required": ["visit_summary", "problems", "data", "risk", "mdm_level", "time_documented", "supported_code", "supports_billed", "modifier_25",
                 "procedure_support", "strengths", "gaps", "recommendation", "recommendation_reason", "clinical_paragraphs", "documentation_tips"],
    "additionalProperties": False,
}


def dispute_context(detail: dict) -> dict:
    """The non-identifying facts of the dispute that the reviewer needs."""
    f = detail["finding"]
    ev = f.get("evidence") or {}
    code = f.get("procedure_code")
    billed = f.get("billed_code") or f.get("original_procedure_code") or ev.get("billed_code") or ev.get("charge_implies") or code
    sig = f.get("signal") or ""
    if sig in DOWNCODE_LETTER_SIGNALS:
        issue = (f"The payer reduced the visit billed as {billed}" + (f" to {code}" if code and code != billed else "")
                 + (f", paying it at the {ev['paid_like']} rate with the code unchanged" if ev.get("paid_like") else "") + ".")
        mode = "em_level"
    elif sig.startswith("EM_BUNDLED"):
        issue = (f"The payer denied the visit {code} (modifiers {f.get('modifiers') or 'none'}) as included in the same-day procedure(s) "
                 f"{', '.join(ev.get('same_day_codes') or []) or 'on the claim'}.")
        mode = "modifier_25"
    else:
        issue = f"The payer denied or reduced {code or 'the claim'}: {f.get('title')}."
        mode = "em_level" if code in EM_CODES else "procedure"
    lines = []
    for ln in (detail.get("lines") or [])[:20]:
        mods = ",".join(json.loads(ln["modifiers"])) if ln.get("modifiers") else ""
        lines.append(f"{ln['procedure_code']}{'-' + mods if mods else ''}" + (f" (submitted as {ln['original_procedure_code']})" if ln.get("original_procedure_code") else ""))
    return {"mode": mode, "issue": issue, "billed_code": billed, "paid_code": code, "payer": f.get("payer_group") or f.get("payer_name"),
            "payer_reason": " ".join(x for x in [f.get("carc"), f.get("carc_text"), f.get("rarc"), f.get("rarc_text")] if x),
            "claim_lines": lines}


def review_note(context: dict, note_text: str | None, pdf_bytes: bytes | None = None, client: Any = None) -> dict:
    """Ask Claude to audit the note. Returns the analysis dict (SCHEMA) plus 'model'. Raises on API errors."""
    if client is None:
        import anthropic
        client = anthropic.Anthropic()
    ask = (f"Dispute: {context['issue']}\nPayer: {context['payer']}\nPayer's stated reason: {context['payer_reason'] or 'none given'}\n"
           f"Lines on the claim: {', '.join(context['claim_lines']) or 'not available'}\n"
           f"Review type: {context['mode']} (em_level: does the note support the level billed; modifier_25: is the visit significant and "
           f"separately identifiable from the procedure; procedure: does the note document the disputed procedure as billed).\n\n"
           "Review the visit note that follows and return the audit.")
    content: list[dict] = []
    if pdf_bytes is not None:
        content.append({"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                                         "data": base64.standard_b64encode(pdf_bytes).decode("ascii")}})
        content.append({"type": "text", "text": ask})
    else:
        content.append({"type": "text", "text": ask + "\n\n<visit_note>\n" + (note_text or "")[:MAX_NOTE_CHARS] + "\n</visit_note>"})
    with client.messages.stream(
        model=MODEL,
        max_tokens=16000,
        system=RUBRIC,
        output_config={"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": content}],
    ) as stream:
        message = stream.get_final_message()
    if message.stop_reason == "refusal":
        raise RuntimeError("The model declined to review this note.")
    if message.stop_reason == "max_tokens":
        raise RuntimeError("The review was cut off before it finished; try again with a shorter note.")
    text = next(b.text for b in message.content if b.type == "text")
    analysis = json.loads(text)
    analysis["model"] = getattr(message, "model", MODEL)
    return analysis


def template_analysis(reason: str) -> dict:
    return {"mode": "template", "reason": reason, "clinical_paragraphs": [], "recommendation": "review_manually",
            "recommendation_reason": reason}


# --------------------------------------------------------------------------- packet assembly

def _new_id(finding_id: str | None, sha: str) -> str:
    return hashlib.sha256(f"{finding_id}|{sha}|{datetime.now(timezone.utc).isoformat()}".encode()).hexdigest()[:16]


def identifiers_for(detail: dict, raw_claim: dict | None) -> list[str]:
    c = raw_claim or {}
    f = detail.get("finding") or {}
    vals = [c.get(k) for k in ("patient_last", "patient_first", "patient_middle", "patient_id", "insured_last", "insured_first", "insured_id",
                               "corrected_patient_last", "corrected_patient_first", "medical_record_number", "member_id_ref",
                               "payer_claim_control_number", "group_number", "authorization_number", "prior_authorization")]
    vals.append(f.get("patient_control_number"))
    try:        # other references on the claim loop (for example REF*SY, the social security number, and other subscriber names)
        other = json.loads(c.get("other_refs") or "{}")
        vals += [str(v) for v in (other.values() if isinstance(other, dict) else other) if isinstance(v, (str, int))]
    except Exception:
        pass
    return [str(v) for v in vals if v]


def build_from_note(conn: sqlite3.Connection, detail: dict, raw_claim: dict | None, filename: str, data: bytes | None,
                    pasted_text: str | None = None, reveal: bool = False, client: Any = None, use_ai: bool = True) -> dict:
    """Review the note, write the letter, render the PDF and store the packet. Returns the packet without the binary fields.
    The PDF always carries the patient's name and member number, because the payer needs them. The letter text returned to the
    caller carries them only when `reveal` is true."""
    ensure_packets(conn)
    data = data or (pasted_text or "").encode("utf-8")
    sha = hashlib.sha256(data).hexdigest()
    text, kind = (pasted_text[:MAX_TEXT_CHARS], "text") if (pasted_text and not filename) else extract_text(filename, data)
    context = dispute_context(detail)
    idents = identifiers_for(detail, raw_claim)
    have_key = bool(os.environ.get("ANTHROPIC_API_KEY")) or client is not None
    analysis: dict
    if not use_ai:
        analysis = template_analysis("Review by Claude was not requested; the letter keeps its prompts for the biller.")
    elif not have_key:
        analysis = template_analysis("ANTHROPIC_API_KEY is not set, so the note was attached without an automated review.")
    elif kind == "pdf_scanned" and os.environ.get("REMITWATCH_NOTE_SEND_SCANS", "0") != "1":
        analysis = template_analysis("This PDF has no text layer. A scanned page cannot have identifiers removed before review, so it was not sent. "
                                     "Paste the note text instead, or set REMITWATCH_NOTE_SEND_SCANS=1 once a business associate agreement covers the API.")
    else:
        try:
            if kind == "pdf_scanned":
                analysis = review_note(context, None, pdf_bytes=data, client=client)
            else:
                clean = scrub(text or "", idents) if os.environ.get("REMITWATCH_NOTE_SCRUB", "1") == "1" else (text or "")
                analysis = review_note(context, clean, client=client)
            analysis = scrub_obj(analysis, idents)      # quotes from a scanned page can still carry a name
            analysis["mode"] = "ai"
        except Exception as exc:        # the packet is still useful without the review
            analysis = template_analysis(f"The automated review failed ({type(exc).__name__}: {str(exc)[:200]}); the note was attached without it.")
    clinical = [p for p in (analysis.get("clinical_paragraphs") or []) if p.strip()]
    if analysis.get("recommendation") == "do_not_appeal":
        clinical = []       # never put arguments in a letter the review itself says not to send
    full = dict(detail, claim=raw_claim) if raw_claim else detail
    packet = build_packet(full, show_phi=True, clinical=clinical or None)
    redacted = build_packet(detail, show_phi=False, clinical=clinical or None)["letter"]
    pid = _new_id(detail["finding"].get("finding_id"), sha)
    out = {"packet_id": pid, "finding_id": detail["finding"].get("finding_id"), "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "mode": analysis.get("mode", "template"), "model": analysis.get("model"), "subject": packet["subject"], "letter": packet["letter"],
           "analysis": analysis, "evidence": packet["evidence"], "attachments": packet["attachments"], "note_name": filename or "pasted note",
           "note_kind": kind, "deadline": packet.get("deadline"), "deadline_basis": packet.get("deadline_basis"),
           "corrected_claim_instructions": packet.get("corrected_claim_instructions")}
    pdf = render_pdf(out, note_text=text, note_pdf=data if kind in ("pdf", "pdf_scanned") else None)
    conn.execute("""INSERT OR REPLACE INTO appeal_packets (packet_id, finding_id, created_at, mode, model, subject, letter, analysis, evidence, attachments,
                    note_name, note_sha256, note_kind, note_blob, pdf, letter_redacted) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (pid, out["finding_id"], out["created_at"], out["mode"], out["model"], out["subject"], out["letter"], json.dumps(analysis),
                  out["evidence"], json.dumps(out["attachments"]), out["note_name"], sha, kind, data, pdf, redacted))
    conn.commit()
    if not reveal:
        out["letter"] = redacted
    return out


# --------------------------------------------------------------------------- PDF

def render_pdf(packet: dict, note_text: str | None = None, note_pdf: bytes | None = None) -> bytes:
    from xml.sax.saxutils import escape

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import PageBreak, Paragraph, Preformatted, SimpleDocTemplate, Spacer, Table, TableStyle

    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["Normal"], fontName="Helvetica", fontSize=10.5, leading=14.5, spaceAfter=7)
    small = ParagraphStyle("small", parent=body, fontSize=8.5, leading=11.5)
    h = ParagraphStyle("h", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=12.5, spaceBefore=6, spaceAfter=8)
    mono = ParagraphStyle("mono", parent=styles["Code"], fontName="Courier", fontSize=7.4, leading=9.4)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=0.9 * inch, rightMargin=0.9 * inch, topMargin=0.8 * inch, bottomMargin=0.8 * inch,
                            title=packet["subject"], author=PRACTICE["name"])
    story: list = [Paragraph(escape(PRACTICE["name"]), h), Paragraph(escape(PRACTICE["address"]), small), Spacer(1, 10)]
    for block in packet["letter"].split("\n\n"):
        if block.strip():
            story.append(Paragraph(escape(block.strip()).replace("\n", "<br/>"), body))
    a = packet.get("analysis") or {}
    if a.get("mode") == "ai":
        story += [PageBreak(), Paragraph("Documentation review", h),
                  Paragraph(escape(a.get("visit_summary", "")), body)]
        rows = [[Paragraph("<b>Element</b>", small), Paragraph("<b>Level</b>", small), Paragraph("<b>Basis in the note</b>", small)]]
        for label, key in (("Problems addressed", "problems"), ("Data reviewed and analyzed", "data"), ("Risk of management", "risk")):
            e = a.get(key) or {}
            if e.get("level") in (None, "not_applicable"):
                continue
            quotes = "".join(f"<br/><i>&ldquo;{escape(q)}&rdquo;</i>" for q in (e.get("quotes") or [])[:5])
            rows.append([Paragraph(label, small), Paragraph(escape(e.get("level", "")), small), Paragraph(escape(e.get("rationale", "")) + quotes, small)])
        m25 = a.get("modifier_25") or {}
        if m25.get("relevant"):
            quotes = "".join(f"<br/><i>&ldquo;{escape(q)}&rdquo;</i>" for q in (m25.get("quotes") or [])[:5])
            rows.append([Paragraph("Separate from the procedure (modifier 25)", small), Paragraph(escape(m25.get("separately_identifiable", "")), small),
                         Paragraph(escape(m25.get("rationale", "")) + quotes, small)])
        if len(rows) > 1:
            t = Table(rows, colWidths=[1.5 * inch, 0.9 * inch, 4.3 * inch], repeatRows=1)
            t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.grey), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                   ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke)]))
            story += [t, Spacer(1, 8)]
        facts = [f"Medical decision making: {a.get('mdm_level')}" if a.get("mdm_level") not in (None, "not_applicable") else "",
                 f"Total time documented: {a.get('time_documented')}" if a.get("time_documented") not in (None, "none") else "",
                 f"Level supported by the note: {a.get('supported_code')}" if a.get("supported_code") not in (None, "not_applicable") else "",
                 a.get("procedure_support") if a.get("procedure_support") not in (None, "not_applicable") else ""]
        for line in facts:
            if line:
                story.append(Paragraph(escape(line), body))
    story += [PageBreak(), Paragraph("Remittance evidence", h), Preformatted(packet.get("evidence") or "", mono), Spacer(1, 10),
              Paragraph("Enclosures", h)]
    for att in packet.get("attachments") or []:
        story.append(Paragraph("&bull; " + escape(att), small))
    if note_text and not note_pdf:
        story += [PageBreak(), Paragraph("Visit note", h), Preformatted(_wrap(note_text, 105), mono)]
    doc.build(story)
    out = buf.getvalue()
    if note_pdf:
        try:
            from pypdf import PdfReader, PdfWriter
            w = PdfWriter()
            for src in (out, note_pdf):
                for pg in PdfReader(io.BytesIO(src)).pages:
                    w.add_page(pg)
            merged = io.BytesIO()
            w.write(merged)
            out = merged.getvalue()
        except Exception:
            pass        # an unreadable upload must not block the letter; the note is still stored with the packet
    return out


def _wrap(text: str, width: int) -> str:
    import textwrap
    lines: list[str] = []
    for ln in text.replace("\r", "").split("\n"):
        lines += textwrap.wrap(ln, width, replace_whitespace=False) or [""]
    return "\n".join(lines[:1500])


# --------------------------------------------------------------------------- storage and email

def get_packet(conn: sqlite3.Connection, packet_id: str, with_pdf: bool = False, reveal: bool = False) -> dict | None:
    """One stored packet. The letter comes back with placeholders for the patient unless `reveal` is true."""
    ensure_packets(conn)
    conn.row_factory = sqlite3.Row
    cols = "packet_id, finding_id, created_at, mode, model, subject, letter, letter_redacted, analysis, evidence, attachments, note_name, note_kind, emailed_to, emailed_at"
    r = conn.execute(f"SELECT {cols}{', pdf' if with_pdf else ''} FROM appeal_packets WHERE packet_id=?", (packet_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    redacted = d.pop("letter_redacted", None)
    if not reveal:
        d["letter"] = redacted or "[The letter is in the PDF.]"
    d["analysis"] = json.loads(d["analysis"]) if d.get("analysis") else {}
    d["attachments"] = json.loads(d["attachments"]) if d.get("attachments") else []
    return d


def list_packets(conn: sqlite3.Connection, finding_id: str | None = None) -> list[dict]:
    ensure_packets(conn)
    conn.row_factory = sqlite3.Row
    where, params = ("WHERE finding_id=?", (finding_id,)) if finding_id else ("", ())
    return [dict(r) for r in conn.execute(f"""SELECT packet_id, finding_id, created_at, mode, model, subject, note_name, emailed_to, emailed_at
                                              FROM appeal_packets {where} ORDER BY created_at DESC LIMIT 200""", params)]


def send_pdf(pdf: bytes, packet_id: str, to: str, note: str = "") -> dict:
    """Email one packet PDF through the configured SMTP server. The PDF holds patient information: send it only to an
    address inside the practice's secure mail or to a payer channel that accepts it."""
    host = os.environ.get("SMTP_HOST")
    if not host:
        return {"sent": False, "reason": "SMTP_HOST is not set"}
    if not re.fullmatch(r"[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+", to or ""):
        return {"sent": False, "reason": "not a valid email address"}
    allowed = [d.strip().lower() for d in os.environ.get("APPEAL_EMAIL_DOMAINS", "").split(",") if d.strip()]
    if allowed and to.split("@")[-1].lower() not in allowed:
        return {"sent": False, "reason": f"address is outside the allowed domains ({', '.join(allowed)})"}
    if not pdf:
        return {"sent": False, "reason": "packet not found"}
    msg = EmailMessage()
    msg["Subject"] = f"Appeal packet {packet_id}"
    msg["From"] = os.environ.get("SMTP_FROM", os.environ.get("SMTP_USER", "remitwatch@localhost"))
    msg["To"] = to
    msg.set_content((note.strip() + "\n\n" if note.strip() else "") + "The appeal packet is attached as a PDF. It contains patient information. "
                    "Handle it under the practice's privacy policy.\n")
    msg.add_attachment(pdf, maintype="application", subtype="pdf", filename=f"appeal-packet-{packet_id}.pdf")
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587")), timeout=30) as smtp:
        if os.environ.get("SMTP_STARTTLS", "1") == "1":
            smtp.starttls()
        if os.environ.get("SMTP_USER"):
            smtp.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASS", ""))
        smtp.send_message(msg)
    return {"sent": True, "to": to, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def email_packet(conn: sqlite3.Connection, packet_id: str, to: str, note: str = "") -> dict:
    """Email a packet stored in the database and record where it went."""
    p = get_packet(conn, packet_id, with_pdf=True)
    out = send_pdf((p or {}).get("pdf") or b"", packet_id, to, note)
    if out.get("sent"):
        conn.execute("UPDATE appeal_packets SET emailed_to=?, emailed_at=? WHERE packet_id=?", (to, out["at"], packet_id))
        conn.commit()
    return out
