"""Appeals by fax: the SRFax adapter, one click from the Worklist to the payer's fax, the payer's answers read and matched,
the weekly follow-ups, the busy line tried again, and the suggested templates from notes that fell short."""
from __future__ import annotations

import json
import sqlite3

import pytest

from remitwatch import fax
from tests.fake_fax import FakeLine, FakeSRFax, pdf_of, pdf_text

XHR = {"X-Requested-With": "remitwatch"}
PAYER_FAX = "(800) 555-0142"
LEVEL2_FAX = "800-555-0177"


# --------------------------------------------------------------------------- the fax service
def test_numbers_and_letter_blanks():
    assert fax.digits("(502) 555-0142") == "5025550142" == fax.digits("1-502-555-0142") == fax.digits("+1 502 555 0142")
    assert fax.digits("555-0142") == "" and fax.digits("(102) 555-0142") == "" and fax.digits("") == ""
    assert fax.pretty("15025550142") == "(502) 555-0142"
    assert fax.letter_blanks("Dear [practice name], see [describe the separate problem]. Done [x].") == ["[describe the separate problem]", "[practice name]"]
    assert fax.letter_blanks("Member ID: M123 (paid as billed)") == []
    # a long prompt is still a prompt; a placeholder the scrub left in a quote is not
    long = "[describe: for example two or more stable chronic conditions, or one chronic condition with exacerbation, or an undiagnosed new problem]"
    assert fax.letter_blanks("Problems: " + long) == [long]
    assert fax.letter_blanks('The note says "biopsy on [DATE] of the [REDACTED] lesion".') == []


def test_srfax_speaks_its_web_service():
    svc = FakeSRFax()
    line = fax.SRFax("1234", "pw", "(270) 555-0100", "billing@example.org", session=svc)
    fid = line.send("(800) 555-0142", pdf_of("appeal"), "appeal-x.pdf")
    q = svc.calls[-1]
    assert q["action"] == "Queue_Fax" and q["sToFaxNumber"] == "18005550142" and q["sCallerID"] == "2705550100" and q["sFaxType"] == "SINGLE"
    assert q["_url"] == fax.SRFAX_URL and svc.queued[fid]["pdf"].startswith(b"%PDF-")
    assert line.status(fid)["state"] == "queued"
    svc.statuses[fid] = {"SentStatus": "Sent", "Pages": "3", "DateSent": "Nov/20/2026 10:02:00"}
    assert line.status(fid) == {"state": "sent", "pages": 3, "error": "", "at": "Nov/20/2026 10:02:00"}
    svc.statuses[fid] = {"SentStatus": "Failed", "ErrorCode": "Busy"}
    assert line.status(fid)["state"] == "failed" and line.status(fid)["error"] == "Busy"
    # both spellings of the status call's ID go along: SRFax's documentation uses both
    assert svc.calls[-1]["sFaxDetailsID"] == fid == svc.calls[-1]["sFaxDetailID"]
    svc.inbox = [{"FileName": "20261120-abc.pdf|9911", "Date": "Nov/20/2026 09:15:00", "CallerID": "8005550199", "Pages": "2"}]
    svc.files["20261120-abc.pdf|9911"] = pdf_of("decision", 2)
    got = line.inbox(30)
    assert got == [{"key": "20261120-abc.pdf|9911", "received_at": "Nov/20/2026 09:15:00", "caller": "8005550199", "pages": 2}]
    assert line.fetch(got[0]["key"]).startswith(b"%PDF-")
    assert line.check()["ok"] is True
    # refusals come back as errors with SRFax's own reason, never as an ID
    with pytest.raises(fax.FaxError, match="not a US fax number"):
        line.send("555-0142", pdf_of("x"), "x.pdf")
    with pytest.raises(fax.FaxError, match="Invalid Access Code"):
        fax.SRFax("1234", "wrong", "2705550100", "b@example.org", session=svc).send("8005550142", pdf_of("x"), "x.pdf")
    bad = fax.SRFax("1234", "wrong", "2705550100", "b@example.org", session=svc).check()
    assert bad["ok"] is False and "Invalid Access Code" in bad["steps"][0]["detail"]
    svc.down = True
    with pytest.raises(fax.FaxError, match="did not answer"):
        line.inbox(30)


def test_the_settings_name_the_line():
    assert fax.provider({}) is None and fax.configured({}) is False
    p = fax.provider({"SRFAX_ACCESS_ID": "1", "SRFAX_ACCESS_PWD": "x", "SRFAX_CALLER_ID": "2705550100", "SRFAX_SENDER_EMAIL": "a@b.org"})
    assert isinstance(p, fax.SRFax) and p.caller_id == "2705550100" and p.url == fax.SRFAX_URL


# --------------------------------------------------------------------------- one click from the Worklist
def _store(tmp_path):
    """Invented remittances from one commercial payer: a visit denied for medical necessity (the note answers it), a visit
    allowed below the payer's usual rate (the letter answers it), and a visit with a missing modifier (fixed in EMA,
    not appealed)."""
    from remitwatch.detect import Detector
    from tests.test_features import NPI, build_store, claim, clean_visits, svc
    denied = ("CLP*CB0000300C017*1*223.65*0.00*0.00*12*PC300*11~NM1*QC*1*SAMPLE*PAT*Q***MI*M00000300~"
              f"NM1*82*2*TEST PROVIDER*****XX*{NPI}~REF*1W*MEMB999888~DTM*232*20261101~"
              + svc("99214", "223.65", "0.00", None, "CAS*CO*50*223.65~", dos="20261101"))
    under = claim(202, svc("99214", "223.65", "110.00", "110.00", "CAS*CO*45*113.65~", dos="20261101"), dos="20261101")
    fixit = claim(203, svc("17000", "150.00", "0.00", None, "CAS*CO*4*150.00~", dos="20261101") + "LQ*HE*N519~", dos="20261101")
    store = build_store(tmp_path, clean_visits(1, 25, "99214", "223.65", "117.60") + [denied, under, fixit], pay_date="20261110")
    Detector(store, run_id="t").run()
    store.conn.execute("""INSERT INTO ema_rows (row_fingerprint, family, claim_id, patient_id, service_date, cpt)
                          VALUES ('fp1', 'charges', 'CB0000300C017', 'EMA77', '2026-11-01', '99214')""")
    store.conn.commit()
    store.close()
    return str(tmp_path / "s.sqlite"), NPI


GOOD_REVIEW = {"visit_summary": "A follow-up of a changing lesion.", "problems": {"level": "moderate", "rationale": "r", "quotes": ["q"]},
               "data": {"level": "low", "rationale": "r", "quotes": []}, "risk": {"level": "moderate", "rationale": "r", "quotes": []},
               "mdm_level": "moderate", "time_documented": "none", "supported_code": "99214", "supports_billed": "yes",
               "modifier_25": {"relevant": False, "separately_identifiable": "not_applicable", "rationale": "", "quotes": []},
               "procedure_support": "not_applicable", "strengths": ["s"], "gaps": [], "recommendation": "appeal", "recommendation_reason": "ok",
               "clinical_paragraphs": ["The note records a changing pigmented lesion on the back, \"enlarging over three months\", examined and biopsied."],
               "documentation_tips": []}
SHORT_REVIEW = dict(GOOD_REVIEW, supports_billed="no", recommendation="do_not_appeal", supported_code="99213", clinical_paragraphs=[],
                    recommendation_reason="The note documents one stable problem and no data; it supports 99213, not 99214.",
                    gaps=["No second problem addressed", "No prescription drug management documented"],
                    documentation_tips=["Record the status of each chronic problem addressed", "Name the drug and the change made"])


@pytest.fixture()
def site(tmp_path, monkeypatch):
    """The API on a real store, with the fake ModMed (one note for the denied visit), a fake fax line, a practice that
    has filled in its details, and Claude's review stood in for."""
    from fastapi.testclient import TestClient

    import remitwatch.api as api_mod
    from remitwatch import appeals, notes
    from remitwatch import modmed as M
    from tests.fake_modmed import FakeModMed
    from tests.test_modmed import PEM
    db, npi = _store(tmp_path)
    mm = FakeModMed(jwk=M.public_jwk(M.load_private_key(PEM))).start()
    mm.add_patient("EMA77", "Sample", "Pat")
    mm.add_practitioner("prx", npi, "Test Provider")
    mm.add_encounter("ex", "EMA77", "2026-11-01")
    mm.add_note("nx", "EMA77", "2026-11-01", b"<html><body><p>Changing lesion, enlarging over three months. Biopsy.</p></body></html>",
                "text/html", encounter="ex", author="prx")
    monkeypatch.setattr(api_mod, "DB_PATH", db)
    monkeypatch.setattr(api_mod, "PASSWORD", "")
    monkeypatch.setenv("MODMED_CLIENT_ID", mm.client_id)
    monkeypatch.setenv("MODMED_PRIVATE_KEY", PEM.replace("\n", "\\n"))
    monkeypatch.setenv("MODMED_FHIR_BASE", mm.fhir_base)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    for k, v in {"name": "Test Dermatology", "address": "1 Main St, Owensboro KY", "tin": "61-0000000", "npi": "1999999999",
                 "phone": "(270) 555-0100", "fax": "(270) 555-0101", "email": "billing@example.org"}.items():
        monkeypatch.setitem(appeals.PRACTICE, k, v)
    review = {"next": GOOD_REVIEW}
    monkeypatch.setattr(notes, "review_note", lambda context, text, pdf_bytes=None, client=None: dict(review["next"], model="test-model"))
    line = FakeLine(busy={"8005550999"})
    monkeypatch.setattr(fax, "provider", lambda env=None: line)
    monkeypatch.setattr(fax, "configured", lambda env=None: True)
    c = TestClient(api_mod.app)
    ids = {r["signal"]: r["finding_id"] for r in c.get("/api/findings?status=all&limit=500").json()["items"]}
    yield type("Site", (), {"c": c, "line": line, "ids": ids, "review": review, "mm": mm, "db": db, "monkeypatch": monkeypatch})
    mm.stop()


def test_select_all_then_appeal(site):
    c, line = site.c, site.line
    note_id, rate_id = site.ids["DENIED_MEDICAL_NECESSITY"], site.ids["UNDERPAID_VS_OWN_RATE"]
    fix_id = next(i for s, i in site.ids.items() if s.startswith("DENIED_MISSING") or s == "DENIED_CODING")
    send = lambda fid, **kw: c.post("/api/appeals/send", json=dict(finding_id=fid, **kw), headers=XHR).json()     # noqa: E731
    # what "Appeal everything appealable" would send: the two appeals, not the fix; the payer has no fax number yet
    a = c.get("/api/appeals/appealable").json()
    assert {i["finding_id"] for i in a["items"]} == {note_id, rate_id}
    assert a["payers"][0]["payer_group"] == "Anthem / BCBS" and a["payers"][0]["number"] == "" and a["payers"][0]["n"] == 2
    assert a["fax"] is True and a["practice_missing"] == [] and a["modmed"] is True
    # held, with why: not an appeal, no number
    assert send(fix_id)["code"] == "not_appealable"
    held = send(note_id)
    assert held["status"] == "held" and held["code"] == "no_number" and "Faxes page" in held["reason"]
    assert line.sent == {}
    # the number, typed once from the payer's papers
    assert c.post("/api/faxes/payer", json={"payer_group": "Anthem / BCBS", "number": "555-0142"}, headers=XHR).status_code == 400
    assert c.post("/api/faxes/payer", json={"payer_group": "Nobody", "number": PAYER_FAX}, headers=XHR).status_code == 404
    assert c.post("/api/faxes/payer", json={"payer_group": "Anthem / BCBS", "number": PAYER_FAX}).status_code == 400   # cross-site
    assert c.post("/api/faxes/payer", json={"payer_group": "Anthem / BCBS", "number": PAYER_FAX}, headers=XHR).json()["number"] == PAYER_FAX
    p = c.get("/api/faxes/payers").json()["payers"]
    assert p[0]["payer_group"] == "Anthem / BCBS" and p[0]["numbers"]["appeal"]["number"] == PAYER_FAX and p[0]["n"] == 2
    # the note from EMA, reviewed, into the letter, faxed; the item is marked sent
    r = send(note_id)
    assert r["status"] == "sent", r
    assert r["number"] == PAYER_FAX and r["note"]["id"] == "nx" and "data" not in r["note"]
    out = line.to(PAYER_FAX)
    assert len(out) == 1
    doc = out[0]["pdf"]
    cover, whole = pdf_text(doc, 0), pdf_text(doc)
    assert "Fax" in cover and "(800) 555-0142" in cover and "Test Dermatology" in cover and "protected health information" in cover
    assert "PAT" not in cover.upper().replace("PAGES", "").replace("PATIENT", "")       # no patient named on the cover
    assert "enlarging over three months" in whole and "M00000300" in whole and "Changing lesion" in whole   # paragraphs, member number, the note
    d = c.get(f"/api/findings/{note_id}").json()
    assert d["finding"]["status"] == "appealed" and d["faxes"][0]["direction"] == "out" and d["faxes"][0]["status"] == "queued"
    assert any("Faxed to Anthem / BCBS at (800) 555-0142" in (e["note"] or "") for e in d["events"])
    # never twice
    assert send(note_id)["status"] == "skipped" and len(line.sent) == 1
    # the underpaid line needs no note: the letter alone
    r2 = send(rate_id)
    assert r2["status"] == "sent", r2
    assert "allowed below" in pdf_text(line.to(PAYER_FAX)[1]["pdf"]).lower() or "reconciliation" in pdf_text(line.to(PAYER_FAX)[1]["pdf"]).lower()
    assert c.get("/api/appeals/appealable").json()["items"] == []
    # the line reports them sent
    line.deliver_all()
    got = c.post("/api/faxes/refresh", headers=XHR).json()
    assert got["checked"] == 2 and got["sent"] == 2 and got["received"] == 0
    f = c.get("/api/faxes").json()
    assert f["configured"] is True and [s["status"] for s in f["sent"]] == ["sent", "sent"] and f["due"] == []


def test_the_payers_answer_is_read_matched_and_answered(site):
    c, line, mp = site.c, site.line, site.monkeypatch
    note_id = site.ids["DENIED_MEDICAL_NECESSITY"]
    c.post("/api/faxes/payer", json={"payer_group": "Anthem / BCBS", "number": PAYER_FAX}, headers=XHR)
    assert c.post("/api/appeals/send", json={"finding_id": note_id}, headers=XHR).json()["status"] == "sent"
    line.deliver_all()
    # the payer's fax: Claude reads it (stood in for here) and its claim number ties it to the item
    line.receive("in-1", pdf_of("Appeal decision: the denial is upheld. Claim PC300.", 2), caller="8005550142")
    read = {"document_type": "appeal_decision", "decision": "upheld", "payer": "Anthem", "claim_numbers": ["PC300"], "patient_last": "SAMPLE",
            "patient_first": "PAT", "service_dates": ["2026-11-01"], "decision_date": "2026-11-18", "deadline": "", "amount": "",
            "summary": "The plan upheld its denial for medical necessity."}
    mp.setattr(fax, "classify", lambda pdf, client=None: read)
    got = c.post("/api/faxes/refresh", headers=XHR).json()
    assert got["received"] == 1 and got["matched"] == 1
    rec = c.get("/api/faxes").json()["received"][0]
    assert rec["finding_id"] == note_id and rec["decision"] == "upheld" and rec["document_type"] == "appeal_decision"
    assert "patient" not in rec            # names are off on this site
    d = c.get(f"/api/findings/{note_id}").json()
    assert any("Fax from the payer (decision, upheld)" in (e["note"] or "") for e in d["events"])
    assert c.get(f"/api/faxes/pdf?key={rec['fax_key']}").content.startswith(b"%PDF-")
    # read once: a second check finds nothing new
    assert c.post("/api/faxes/refresh", headers=XHR).json()["received"] == 0
    # upheld: the next level is due at once, to the second-level number when there is one, with the payer's decision behind it
    due = c.get("/api/faxes").json()["due"]
    assert [(x["finding_id"], x["action"], x["level"]) for x in due] == [(note_id, "next_level", 2)]
    c.post("/api/faxes/payer", json={"payer_group": "Anthem / BCBS", "purpose": "level_2", "number": LEVEL2_FAX}, headers=XHR)
    r = c.post("/api/faxes/follow_up", json={}, headers=XHR).json()
    assert r["sent"] == 1 and r["results"][0]["number"] == "(800) 555-0177"
    doc = line.to(LEVEL2_FAX)[0]["pdf"]
    text = pdf_text(doc)
    assert "Request for the next level of review" in text and "upheld" in text and "Claim PC300" in text   # the letter, then the decision
    assert c.get("/api/faxes").json()["due"] == []
    # the second level upheld too: past what is faxed without a person
    line.deliver_all()
    line.receive("in-2", pdf_of("Second level: upheld."), caller="8005550177")
    c.post("/api/faxes/refresh", headers=XHR)
    due = c.get("/api/faxes").json()["due"]
    assert due[0]["action"] == "person" and "person's call" in due[0]["why"]
    assert c.post("/api/faxes/follow_up", json={}, headers=XHR).json()["sent"] == 0


def test_an_unmatched_fax_is_tied_by_hand(site):
    c, line, mp = site.c, site.line, site.monkeypatch
    note_id = site.ids["DENIED_MEDICAL_NECESSITY"]
    line.receive("in-9", pdf_of("Please send records."))
    mp.setattr(fax, "classify", lambda pdf, client=None: {"document_type": "records_request", "decision": "none", "payer": "", "claim_numbers": [],
                                                          "patient_last": "", "patient_first": "", "service_dates": [], "decision_date": "",
                                                          "deadline": "", "amount": "", "summary": "Records requested."})
    assert c.post("/api/faxes/refresh", headers=XHR).json()["matched"] == 0
    key = c.get("/api/faxes").json()["received"][0]["fax_key"]
    assert c.post("/api/faxes/match", json={"fax_key": key, "finding_id": "nope"}, headers=XHR).status_code == 404
    assert c.post("/api/faxes/match", json={"fax_key": key, "finding_id": note_id}, headers=XHR).json()["ok"] is True
    assert c.get("/api/faxes").json()["received"][0]["finding_id"] == note_id


def test_a_busy_line_is_tried_three_times(site):
    c, line = site.c, site.line
    rate_id = site.ids["UNDERPAID_VS_OWN_RATE"]
    c.post("/api/faxes/payer", json={"payer_group": "Anthem / BCBS", "number": "8005550999"}, headers=XHR)     # always busy
    assert c.post("/api/appeals/send", json={"finding_id": rate_id}, headers=XHR).json()["status"] == "sent"
    for n in (1, 2):
        r = c.post("/api/faxes/refresh", headers=XHR).json()
        assert r["failed"] == 1 and r["retried"] == 1, (n, r)
    r = c.post("/api/faxes/refresh", headers=XHR).json()
    assert r["failed"] == 1 and r["retried"] == 0
    assert len(line.to("8005550999")) == 3
    assert [s["status"] for s in c.get("/api/faxes").json()["sent"]] == ["failed"] * 3
    # three tries without getting through: nothing is chased on a fax the payer never had; a person checks the number
    due = c.get("/api/faxes").json()["due"]
    assert [d["action"] for d in due] == ["person"] and "check the number" in due[0]["why"]
    assert c.post("/api/faxes/follow_up", json={}, headers=XHR).json()["sent"] == 0


def test_holds_say_what_to_do(site):
    from remitwatch import appeals
    c, mp = site.c, site.monkeypatch
    note_id = site.ids["DENIED_MEDICAL_NECESSITY"]
    c.post("/api/faxes/payer", json={"payer_group": "Anthem / BCBS", "number": PAYER_FAX}, headers=XHR)
    send = lambda **kw: c.post("/api/appeals/send", json=dict(finding_id=note_id, **kw), headers=XHR).json()     # noqa: E731
    mp.setitem(appeals.PRACTICE, "tin", "[practice tax ID]")
    r = send()
    assert r["code"] == "practice" and "PRACTICE_TIN" in r["reason"]
    mp.setitem(appeals.PRACTICE, "tin", "61-0000000")
    mp.setattr(fax, "provider", lambda env=None: None)
    assert send()["code"] == "no_fax"
    mp.setattr(fax, "provider", lambda env=None: site.line)
    # without the review the letter keeps its prompts: never faxed that way
    mp.delenv("ANTHROPIC_API_KEY")
    r = send()
    assert r["code"] == "blanks" and "[summarize]" in r["reason"] and site.line.sent == {}
    # the review can run now: the packet built without it is built again, and goes
    mp.setenv("ANTHROPIC_API_KEY", "test-key")
    assert send()["status"] == "sent"


def test_a_note_that_falls_short_becomes_a_suggested_template(site):
    c, mp = site.c, site.monkeypatch
    from remitwatch import lessons
    note_id = site.ids["DENIED_MEDICAL_NECESSITY"]
    c.post("/api/faxes/payer", json={"payer_group": "Anthem / BCBS", "number": PAYER_FAX}, headers=XHR)
    site.review["next"] = SHORT_REVIEW
    r = c.post("/api/appeals/send", json={"finding_id": note_id}, headers=XHR).json()
    assert r["status"] == "held" and r["code"] == "review_no" and "supports 99213" in r["reason"] and site.line.sent == {}
    assert c.get(f"/api/findings/{note_id}").json()["finding"]["status"] == "open"
    les = c.get("/api/lessons").json()
    g = les["codes"][0]
    assert les["n"] == 1 and les["lost_n"] == 1 and g["family"] == "necessity" and g["code"] == "99214"
    assert g["gaps"] == SHORT_REVIEW["gaps"] and g["tips"] == SHORT_REVIEW["documentation_tips"]
    assert g["payers"] == [{"payer_group": "Anthem / BCBS", "n": 1}] and g["providers"] == [{"name": "TEST PROVIDER", "n": 1}]
    assert any(e["field"] == "The payer's criteria" for e in g["elements"])
    # with an owner's password, which clinician is shown only to the owner
    import remitwatch.api as api_mod
    mp.setattr(api_mod, "OWNER_PASSWORD", "a-long-owner-passphrase-9")
    assert c.get("/api/lessons").json()["providers_locked"] is True and c.get("/api/lessons").json()["codes"][0]["providers"] == []
    assert c.get("/api/lessons", headers={"X-Owner-Password": "a-long-owner-passphrase-9"}).json()["codes"][0]["providers"]
    # the template Claude writes from them (stood in for): only the code, the payers and the gaps go to it
    sent = {}

    def write(group, client=None):
        sent.update(group)
        return {"name": "99214 medical necessity", "fields": [{"label": "Indication", "required": True, "prompt": "What made it necessary?", "why": "w"}],
                "attestation": "I performed and documented this work.", "notes": []}
    mp.setattr(lessons, "write_template", write)
    t = c.post("/api/lessons/template", json={"key": g["key"]}, headers=XHR).json()
    assert t["template"]["fields"][0]["required"] is True and "providers" not in json.dumps(sent).lower().replace('"providers": []', "")
    assert c.post("/api/lessons/template", json={"key": "nope"}, headers=XHR).status_code == 404


# --------------------------------------------------------------------------- the follow-up calendar
def _book(rows: list[tuple], finding_status: str = "appealed", payer_kind: str = "commercial") -> sqlite3.Connection:
    from remitwatch.actions import apply_fax, ensure_fax_tables
    conn = sqlite3.connect(":memory:")
    conn.execute("""CREATE TABLE findings (finding_id TEXT, status TEXT, payer_group TEXT, payer_kind TEXT, signal TEXT, title TEXT,
                    service_date TEXT, shortfall_cents INTEGER)""")
    conn.execute("INSERT INTO findings VALUES ('F1', ?, 'Payer', ?, 'S', 't', '2026-01-01', 1000)", (finding_status, payer_kind))
    ensure_fax_tables(conn)
    for i, (direction, day, purpose, level, status, doc) in enumerate(rows):
        if direction == "in":           # the payer's answer, tied as a fax is tied by itself: by the claim number, from its number
            doc = dict({"tied_by": "claim_id"}, **(doc or {}))
        apply_fax(conn, {"fax_key": f"k{i}", "direction": direction, "finding_id": "F1", "purpose": purpose, "level": level, "status": status,
                         "created_at": day + "T10:00:00+00:00", "doc": doc or {}})
    return conn


def test_follow_ups_wait_for_the_payer_then_come_every_week():
    first = ("out", "2026-09-01", "appeal", 1, "sent", None)
    assert fax.followups(_book([first]), "2026-09-30") == []                       # 29 days: the payer still has time
    d = fax.followups(_book([first]), "2026-10-01")
    assert [(x["action"], x["n"]) for x in d] == [("second_request", 1)]
    asked = ("out", "2026-10-01", "second_request", 1, "sent", None)
    assert fax.followups(_book([first, asked]), "2026-10-07") == []                # a week between requests
    assert [(x["action"], x["n"]) for x in fax.followups(_book([first, asked]), "2026-10-08")] == [("second_request", 2)]
    # Medicare and Medicare Advantage have 60 days to decide
    assert fax.followups(_book([first], payer_kind="medicare"), "2026-10-30") == []
    assert fax.followups(_book([first], payer_kind="medicare"), "2026-10-31")[0]["action"] == "second_request"
    # paid (or let go): nothing to chase
    assert fax.followups(_book([first], finding_status="resolved"), "2026-12-01") == []
    # twelve unanswered requests: a person takes it from there
    from datetime import date, timedelta
    many = [first] + [("out", (date(2026, 10, 1) + timedelta(days=7 * i)).isoformat(), "second_request", 1, "sent", None) for i in range(12)]
    assert fax.followups(_book(many), "2027-01-15")[0]["action"] == "person"


def test_follow_ups_after_the_payer_answers():
    first = ("out", "2026-09-01", "appeal", 1, "sent", None)
    ok = ("in", "2026-09-20", "appeal_decision", None, "received", {"document_type": "appeal_decision", "decision": "overturned"})
    assert fax.followups(_book([first, ok]), "2026-10-19") == []                   # approved: the payment has 30 days to come
    d = fax.followups(_book([first, ok]), "2026-10-20")
    assert d[0]["action"] == "payment" and d[0]["decision"]["decision"] == "overturned"
    rec = ("in", "2026-09-20", "records_request", None, "received", {"document_type": "records_request", "decision": "none"})
    assert fax.followups(_book([first, rec]), "2026-09-21")[0]["action"] == "records"
    sent_rec = ("out", "2026-09-22", "records", 1, "sent", None)
    assert fax.followups(_book([first, rec, sent_rec]), "2026-09-25") == []
    # an answer that decides nothing (an acknowledgement) does not stop the clock
    ack = ("in", "2026-09-10", "acknowledgement", None, "received", {"document_type": "acknowledgement", "decision": "pending"})
    assert fax.followups(_book([first, ack]), "2026-10-01")[0]["action"] == "second_request"
    # a fax that never got through is not an appeal the payer has: it is not chased; after the last try a person checks the number
    failed = [("out", "2026-09-01", "appeal", 1, "failed", {"attempts": n}) for n in (1, 2, 3)]
    assert [(x["action"], "check the number" in x["why"]) for x in fax.followups(_book(failed), "2026-12-01")] == [("person", True)]
    assert fax.followups(_book(failed[:2]), "2026-12-01") == []


# --------------------------------------------------------------------------- the journal (serverless hosts)
def test_faxes_numbers_and_lessons_last_through_the_journal():
    from remitwatch import cloud
    from remitwatch.detect import ensure_schema
    conn = sqlite3.connect(":memory:")
    ensure_schema(conn)
    conn.execute("INSERT INTO findings (finding_id, status) VALUES ('F1', 'appealed')")
    entries = [("a1", {"kind": "payer_fax", "payer_group": "Payer", "purpose": "appeal", "number": "8005550142", "actor": "x"}),
               ("a2", {"kind": "fax", "record": {"fax_key": "out:x:1", "direction": "out", "finding_id": "F1", "status": "queued", "doc": {"a": 1}}}),
               ("a3", {"kind": "fax", "record": {"fax_key": "out:x:1", "status": "sent"}}),
               ("a4", {"kind": "lesson", "record": {"lesson_key": "L1", "code": "99214", "gaps": ["g"], "tips": []}}),
               ("a5", {"kind": "event", "finding_id": "F1", "note": "Second request faxed", "at": "2026-11-20T10:00:00+00:00"}),
               ("a6", {"kind": "event", "finding_id": "NOT-HERE", "note": "x"})]
    done, left, wrote = cloud.apply_actions(conn, entries)
    assert left == ["a6"] and wrote          # an item this copy does not hold yet: kept and tried again
    again = cloud.apply_actions(conn, entries[:5])
    assert again[1] == []
    assert conn.execute("SELECT status, doc FROM faxes").fetchall() == [("sent", '{"a": 1}')]
    assert conn.execute("SELECT number FROM payer_fax").fetchone()[0] == "8005550142"
    assert conn.execute("SELECT gaps FROM note_lessons").fetchone()[0] == '["g"]'
    assert conn.execute("SELECT COUNT(*) FROM finding_events WHERE kind='fax'").fetchone()[0] == 1       # replayed, written once
    assert {"faxes", "payer_fax", "note_lessons"} <= {t for t, _c, _w in cloud.SERVING_TABLES}


def test_the_daily_fax_run_needs_the_secret(site, monkeypatch):
    c = site.c
    import remitwatch.api as api_mod
    monkeypatch.setattr(api_mod, "PASSWORD", "a-long-site-passphrase-1")
    monkeypatch.setenv("CRON_SECRET", "s3cret-value")
    assert c.get("/api/cron/faxes").status_code == 401
    r = c.get("/api/cron/faxes", headers={"Authorization": "Bearer s3cret-value"})
    assert r.status_code == 200 and "refresh" in r.json() and "follow_up" in r.json()


# --------------------------------------------------------------------------- a serverless host (Vercel): the journal
from tests.test_cloud import box  # noqa: E402,F401  (the fixture: a fake Dropbox with the state folder)


def test_on_a_serverless_host_the_appeal_goes_out_and_lasts(box, monkeypatch):
    import base64

    from fastapi.testclient import TestClient

    from remitwatch import api, appeals, cloud, notes
    from tests.test_cloud import DATA, PW, cycle, raw_conn, visits_with_downcodes
    from tests.test_remitwatch import remit
    root, client, state = box
    claims = visits_with_downcodes()
    (root / DATA).mkdir(parents=True, exist_ok=True)
    for n, i in enumerate(range(0, len(claims), 10)):          # paid last week: the filing windows are open
        (root / DATA / f"r{n:03d}.RMT").write_bytes(remit("".join(claims[i:i + 10]), "100.00", seg_count=99, isa_ctrl=f"{n + 1:09d}", pay_date="20261110"))
    cycle(state, client)
    monkeypatch.setenv("REMITWATCH_MODE", "serverless")
    monkeypatch.setenv("REMITWATCH_STATE_DIR", str(root / "RemitWatch-state"))
    monkeypatch.setattr(api, "PASSWORD", PW)
    monkeypatch.setattr(api, "USERNAME", "oda")
    monkeypatch.setattr(cloud, "SERVING_COPY", cloud.ServingCopy())
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    for k, v in {"name": "Test Dermatology", "address": "1 Main St", "tin": "61-0000000", "npi": "1999999999", "phone": "(270) 555-0100",
                 "fax": "(270) 555-0101", "email": "billing@example.org"}.items():
        monkeypatch.setitem(appeals.PRACTICE, k, v)
    review = dict(GOOD_REVIEW, clinical_paragraphs=["The note addresses two chronic problems not at goal and adjusts a prescription."])
    monkeypatch.setattr(notes, "review_note", lambda context, text, pdf_bytes=None, client=None: dict(review, model="test-model"))
    line = FakeLine()
    monkeypatch.setattr(fax, "provider", lambda env=None: line)
    h = dict(XHR, Authorization="Basic " + base64.b64encode(f"oda:{PW}".encode()).decode())
    tc = TestClient(api.app)
    fid = next(i["finding_id"] for i in tc.get("/api/findings?signal=DOWNCODE_LEVEL_REDUCED", headers=h).json()["items"])
    built = tc.post("/api/appeals/build", data={"finding_id": fid, "text": "Two chronic problems, not at goal; dose changed."}, headers=h).json()
    assert built["mode"] == "ai"
    payer = tc.get(f"/api/findings/{fid}", headers=h).json()["finding"]["payer_group"]
    assert tc.post("/api/faxes/payer", json={"payer_group": payer, "number": PAYER_FAX}, headers=h).status_code == 200
    r = tc.post("/api/appeals/send", json={"finding_id": fid}, headers=h).json()
    assert r["status"] == "sent" and r["packet_id"] == built["packet_id"], (r.get("code"), r.get("reason"))   # the packet already built goes
    assert "two chronic problems not at goal" in pdf_text(line.to(PAYER_FAX)[0]["pdf"])
    # shown at once from this instance's copy, and written to the journal
    d = tc.get(f"/api/findings/{fid}", headers=h).json()
    assert d["finding"]["status"] == "appealed" and d["faxes"][0]["status"] == "queued"
    kinds = sorted(json.loads(state.get(m["name"]))["kind"] for m in state.list(cloud.ACTIONS))
    # the send reserved, then settled with the line's ID (two records of one fax); the packet marked on the item for the
    # EMA batch; the number; the status
    assert kinds == ["event", "fax", "fax", "payer_fax", "status"]
    assert len(state.list("fax-sends")) == 1          # the marker only one instance can write: one send of this appeal
    line.deliver_all()
    assert tc.post("/api/faxes/refresh", headers=h).json()["sent"] == 1
    # the next run folds it all into the store; a new instance sees it
    cycle(state, client)
    conn = raw_conn(state)
    assert conn.execute("SELECT status FROM faxes WHERE direction='out'").fetchone()[0] == "sent"
    assert conn.execute("SELECT number FROM payer_fax").fetchone()[0] == "8005550142"
    fresh = cloud.ServingCopy().connection()
    assert fresh.execute("SELECT COUNT(*) FROM faxes").fetchone()[0] == 1 and fresh.execute("SELECT COUNT(*) FROM payer_fax").fetchone()[0] == 1


def test_the_reviewed_paragraphs_fill_the_letters_that_rest_on_the_note():
    """A bundled procedure, a frequency denial and a whole claim denied: with the note's paragraphs no prompt is left;
    without them the prompts stay (and such a letter is never faxed)."""
    from remitwatch.appeals import build_packet
    base = {"finding_id": "F", "procedure_code": "17311", "carc": "97", "payer_group": "Payer", "payer_kind": "commercial",
            "service_date": "2026-11-01", "shortfall_cents": 50000, "expected_cents": 50000, "evidence": {"same_day_codes": ["13132"]}}
    para = ["The note records two separate lesions, on the left cheek and on the right forearm, each excised in its own session."]
    for sig in ("PROCEDURE_BUNDLED", "DENIED_FREQUENCY", "CLAIM_DENIED_UNPAID", "DENIED_MEDICAL_NECESSITY"):
        bare = build_packet({"finding": dict(base, signal=sig)})["letter"]
        with_note = build_packet({"finding": dict(base, signal=sig)}, clinical=para)["letter"]
        body = lambda letter: letter.split("filed under the plan's provider manual.", 1)[1].split("Requested action")[0]   # noqa: E731
        assert fax.letter_blanks(body(bare)), sig
        assert not fax.letter_blanks(body(with_note)) and "two separate lesions" in with_note, (sig, fax.letter_blanks(body(with_note)))


def test_the_letters_give_the_fax_line_for_answers(monkeypatch):
    import importlib

    from remitwatch import appeals
    monkeypatch.setenv("SRFAX_CALLER_ID", "12705550101")
    monkeypatch.setenv("PRACTICE_FAX", "(270) 555-0199")
    try:
        assert importlib.reload(appeals).PRACTICE["fax"] == "(270) 555-0101"
        monkeypatch.delenv("SRFAX_CALLER_ID")
        assert importlib.reload(appeals).PRACTICE["fax"] == "(270) 555-0199"
    finally:
        monkeypatch.delenv("SRFAX_CALLER_ID", raising=False)
        monkeypatch.delenv("PRACTICE_FAX", raising=False)
        importlib.reload(appeals)


def test_a_code_the_remittance_does_not_say_was_changed_is_checked_first(site):
    """A visit whose higher level is inferred from the charge, or from EMA's charge rather than the claim file: a letter
    saying it was billed waits until a person has seen it went out, then goes in one click."""
    import sqlite3 as sq
    c = site.c
    fid = site.ids["DENIED_MEDICAL_NECESSITY"]
    conn = sq.connect(site.db)
    conn.execute("UPDATE findings SET signal='DOWNCODE_CHARGE', evidence=? WHERE finding_id=?", (json.dumps({"charge_implies": "99215"}), fid))
    conn.commit()
    conn.close()
    c.post("/api/faxes/payer", json={"payer_group": "Anthem / BCBS", "number": PAYER_FAX}, headers=XHR)
    r = c.post("/api/appeals/send", json={"finding_id": fid}, headers=XHR).json()
    assert r["code"] == "check_code" and "99215" in r["reason"] and site.line.sent == {}
    assert c.post("/api/appeals/send", json={"finding_id": fid, "confirmed": True}, headers=XHR).json()["status"] == "sent"
