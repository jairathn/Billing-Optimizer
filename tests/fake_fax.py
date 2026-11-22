"""
Stand-ins for the fax line, for the tests.

    FakeLine      a fax.Provider kept in memory: what was sent, to whom, in what state; what is waiting in the inbox; a
                  number that is always busy.
    FakeSRFax     SRFax's web service as a requests-like session: one POST per call, `action` naming it, the answer
                  {Status, Result}. It checks what SRFax checks (the account, the number's form, the file) so the adapter
                  is tested against the contract, not against itself.
"""
from __future__ import annotations

import base64
import io
import json

from remitwatch import fax


def pdf_of(text: str, pages: int = 1) -> bytes:
    """A small real PDF with the text on each page (what a payer's fax looks like to the code)."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    for _ in range(pages):
        c.drawString(72, 720, text)
        c.showPage()
    c.save()
    return buf.getvalue()


def pdf_text(pdf: bytes, page: int | None = None) -> str:
    from pypdf import PdfReader
    pages = PdfReader(io.BytesIO(pdf)).pages
    return "\n".join((p.extract_text() or "") for p in (pages if page is None else [pages[page]]))


class FakeLine(fax.Provider):
    name = "fake"

    def __init__(self, busy: set[str] | None = None, delay: float = 0.0):
        self.sent: dict[str, dict] = {}
        self.inbox_items: dict[str, dict] = {}
        self.busy = {fax.digits(b) for b in (busy or set())}
        self.n = 0
        self.delay = delay              # seconds a send takes (SRFax's POST with a PDF in it): widens any race
        self.outbox_down = False        # the outbox cannot be read: whether a fax went out cannot be told

    # what the code calls
    def send(self, to: str, pdf: bytes, filename: str) -> str:
        num = fax.digits(to)
        if not num:
            raise fax.FaxError(f"{to!r} is not a US fax number.")
        if not pdf.startswith(b"%PDF-"):
            raise fax.FaxError("not a PDF")
        if self.delay:
            import time
            time.sleep(self.delay)
        self.n += 1
        fid = f"F{self.n:04d}"
        self.sent[fid] = {"to": num, "pdf": pdf, "filename": filename, "state": "queued"}
        return fid

    def find_sent(self, filename: str, number: str, since: str) -> str | None:
        if self.outbox_down:
            raise fax.FaxError("the outbox did not answer")
        return next((k for k, v in self.sent.items() if v["filename"] == filename and v["to"] == fax.digits(number)), None)

    def status(self, provider_id: str) -> dict:
        s = self.sent[provider_id]
        if s["state"] == "queued" and s["to"] in self.busy:
            s["state"] = "failed"
        return {"state": s["state"], "pages": fax.page_count(s["pdf"]) if s["state"] == "sent" else None,
                "error": "Busy" if s["state"] == "failed" else "", "at": "2026-11-20 10:00:00" if s["state"] == "sent" else ""}

    def inbox(self, days: int) -> list[dict]:
        return [{"key": k, "received_at": v["at"], "caller": v["caller"], "pages": fax.page_count(v["pdf"])} for k, v in self.inbox_items.items()]

    def fetch(self, key: str) -> bytes:
        if key not in self.inbox_items:
            raise fax.FaxError("no such fax")
        return self.inbox_items[key]["pdf"]

    def check(self) -> dict:
        return {"ok": True, "steps": [{"step": "Sign-in", "ok": True, "detail": "fake"}]}

    # what the tests do
    def deliver_all(self) -> None:
        for s in self.sent.values():
            if s["state"] == "queued" and s["to"] not in self.busy:
                s["state"] = "sent"

    def receive(self, key: str, pdf: bytes, caller: str = "5025550199", at: str = "11/20/2026 09:15") -> None:
        self.inbox_items[key] = {"pdf": pdf, "caller": caller, "at": at}

    def to(self, number: str) -> list[dict]:
        return [dict(v, id=k) for k, v in self.sent.items() if v["to"] == fax.digits(number)]


class _Resp:
    def __init__(self, status: int, body):
        self.status_code, self._body = status, body

    def json(self):
        if isinstance(self._body, (bytes, str)):
            return json.loads(self._body)
        return self._body


class FakeSRFax:
    """SRFax's SRF_SecWebSvc.php: every call a POST with action, access_id and access_pwd."""

    def __init__(self, access_id: str = "1234", access_pwd: str = "pw"):
        self.access_id, self.access_pwd = access_id, access_pwd
        self.calls: list[dict] = []
        self.queued: dict[str, dict] = {}
        self.statuses: dict[str, dict] = {}
        self.inbox: list[dict] = []
        self.files: dict[str, bytes] = {}
        self.down = False

    def post(self, url, data=None, timeout=None):
        self.calls.append(dict(data or {}, _url=url))
        if self.down:
            raise ConnectionError("down")
        d = data or {}
        if d.get("sResponseFormat") != "JSON":
            return _Resp(200, "not json")
        if d.get("access_id") != self.access_id or d.get("access_pwd") != self.access_pwd:
            return _Resp(200, {"Status": "Failed", "Result": "Invalid Access Code / Password"})
        a = d.get("action")
        if a == "Queue_Fax":
            to = d.get("sToFaxNumber", "")
            if not (len(to) == 11 and to.isdigit() and to.startswith("1")):
                return _Resp(200, {"Status": "Failed", "Result": "Invalid Fax Number"})
            if d.get("sFaxType") not in ("SINGLE", "BROADCAST") or not d.get("sCallerID") or not d.get("sSenderEmail"):
                return _Resp(200, {"Status": "Failed", "Result": "Missing parameter"})
            raw = base64.b64decode(d.get("sFileContent_1") or "")
            if not raw.startswith(b"%PDF-") or not d.get("sFileName_1", "").endswith(".pdf"):
                return _Resp(200, {"Status": "Failed", "Result": "Invalid file"})
            fid = str(4000 + len(self.queued))
            self.queued[fid] = {"to": to, "pdf": raw, "caller": d["sCallerID"], "name": d["sFileName_1"]}
            return _Resp(200, {"Status": "Success", "Result": fid})
        if a == "Get_FaxStatus":
            fid = d.get("sFaxDetailsID") or d.get("sFaxDetailID")
            if fid not in self.queued:
                return _Resp(200, {"Status": "Failed", "Result": "Fax not found"})
            return _Resp(200, {"Status": "Success", "Result": [self.statuses.get(fid, {"SentStatus": "In Progress"})]})
        if a == "Get_Fax_Inbox":
            if d.get("sPeriod") != "RANGE" or len(d.get("sStartDate", "")) != 8 or len(d.get("sEndDate", "")) != 8:
                return _Resp(200, {"Status": "Failed", "Result": "Invalid period"})
            return _Resp(200, {"Status": "Success", "Result": self.inbox})
        if a == "Retrieve_Fax":
            name = d.get("sFaxFileName")
            if d.get("sDirection") != "IN" or name not in self.files:
                return _Resp(200, {"Status": "Failed", "Result": "Fax not found"})
            return _Resp(200, {"Status": "Success", "Result": base64.b64encode(self.files[name]).decode()})
        if a == "Get_Fax_Outbox":
            if d.get("sPeriod") != "RANGE" or len(d.get("sStartDate", "")) != 8 or len(d.get("sEndDate", "")) != 8:
                return _Resp(200, {"Status": "Failed", "Result": "Invalid period"})
            return _Resp(200, {"Status": "Success", "Result": [
                {"FileName": f"20261120101500-1-{fid}_0|{fid}", "SentStatus": self.statuses.get(fid, {}).get("SentStatus", "In Progress"),
                 "ToFaxNumber": q["to"], "SubmittedFiles": q["name"]} for fid, q in self.queued.items()]})
        return _Resp(200, {"Status": "Failed", "Result": f"Unknown action {a}"})
