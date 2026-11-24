"""Review fixes on the live hotfix, on the server and on the serverless host.

mr-7   one patient's note with a table of rows (a procedure list, a pathology history) is that patient's note; only a
       table that shows two or more different patients is refused as a report
mr-11  a body that is not a JSON object, or not JSON, is refused with 400 and a plain sentence on every route that reads
       one (appeals/send and hide among them), on both hosts; other checks of an address keep their 422
mr-12  the send-appeals command prints each item's id, what happened and its code, never a reason (a held item's reason
       names the visit's date)

Invented data only: names, record numbers, accession numbers, claim ids and amounts are made up."""
from __future__ import annotations

import base64
import re
from datetime import date, timedelta

import pytest

from remitwatch import cloud, fax, uploads
from tests.fake_fax import FakeLine
from tests.test_cloud import DATA, PW, box, cycle, visits_with_downcodes  # noqa: F401  (box is a fixture)
from tests.test_fax import GOOD_REVIEW, PAYER_FAX, XHR, _number, _rows, _send, site  # noqa: F401  (site is a fixture)
from tests.test_hotfix2 import OTHER_NOTE, REPORT, _build, _events, _journal, sl  # noqa: F401  (sl is a fixture)
from tests.test_remitwatch import remit

SEVERAL = "This looks like a report listing several patients, not one visit note."


# =========================================================================== mr-7: one patient's table of rows
def _procedure_note(dos: str, ident: str) -> str:
    """One patient's procedure note: the patient, an identifier, the date of service, and what was done that day as a
    table of five rows (date, code, description, units, charge)."""
    done = [("11102", "Tangential biopsy of skin, single lesion", 1, "150.00"),
            ("11103", "Tangential biopsy of skin, each additional", 2, "90.00"),
            ("17000", "Destruction of premalignant lesion, first", 1, "95.00"),
            ("17003", "Destruction of premalignant lesions, 2 to 14", 3, "75.00"),
            ("99214", "Office visit, established patient, moderate", 1, "223.65")]
    return (f"Procedure note\nPatient: Pat Q Sample    {ident}\nDate of Service: {dos}\n"
            "Chief complaint: changing lesion on the back, enlarging over three months. Biopsy taken.\n"
            "Procedures performed on the patient today\n"
            "Date        CPT     Description                                     Units   Charge\n"
            + "".join(f"{dos}  {code}   {what:<46}  {units}       ${amount}\n" for code, what, units, amount in done)
            + "Exam and plan. " * 20)


def _pathology_note(dos: str) -> str:
    """One patient's progress note with their pathology history: five earlier specimens, each a row with its date, its
    accession number and the diagnosis. It names the patient and the date of service, and no record number."""
    history = [("03/14/2025", "DS25-14410", "BASAL CELL CARCINOMA, NODULAR TYPE, LEFT CHEEK"),
               ("06/02/2025", "DS25-22871", "SQUAMOUS CELL CARCINOMA IN SITU, RIGHT FOREARM"),
               ("09/19/2025", "DS25-31502", "DYSPLASTIC NEVUS, MILD ATYPIA, UPPER BACK"),
               ("01/08/2026", "DS26-00917", "ACTINIC KERATOSIS, SCALP"),
               ("05/27/2026", "DS26-18240", "BASAL CELL CARCINOMA, SUPERFICIAL TYPE, RIGHT SHOULDER")]
    return (f"Progress note\nPatient: Pat Q Sample\nDate of Service: {dos}\n"
            "Chief complaint: a new spot on the left cheek, enlarging over three months. A history of skin cancer.\n"
            "Patient pathology history\n"
            + "".join(f"{day}  {accession}  {dx}\n" for day, accession, dx in history) + "Exam and plan. " * 20)


def _charges_report(dos: str, own_id: str) -> str:
    """A real list of patients: one day's charges printed from the practice-management system, a row per visit with the
    patient's name, birth date and record number. This patient's row comes first, with their name, their record number
    and the date of service, so that the list shows this patient's visit too."""
    people = [("Sample, Pat Q", "04/12/1961", own_id, "99214", "223.65"), ("Zzother, Una", "02/03/1958", "ZZ000481", "99213", "120.00"),
              ("Zzthird, Bo", "09/30/1972", "ZZ000493", "11102", "150.00"), ("Zzfourth, Cy", "12/24/1949", "ZZ000495", "17000", "95.00"),
              ("Zzfifth, Di", "07/07/1985", "ZZ000497", "99212", "85.00")]
    return ("Charges by date of service    Printed 11/20/2026    Page 1 of 1\n"
            "Patient             DOB          MRN         DOS          CPT     Charge\n"
            + "".join(f"{name:<19} {born}   {mrn:<10}  {dos}   {code}   ${amount}\n" for name, born, mrn, code, amount in people))


def _single_spaced(text: str) -> str:
    """The same text as a PDF reader often gives a printed table: one space between the columns."""
    return re.sub(r" {2,}", " ", text)


def test_mr7_one_patients_procedure_list_or_pathology_history_is_their_note_and_a_list_of_patients_is_not():
    """A table of rows counts as several patients only when it shows two or more different patients: by the names in its
    Patient column, the birth dates its rows give, or two record numbers under one label."""
    proc, path = _procedure_note("11/01/2026", "PMS ID: EMA77"), _pathology_note("11/01/2026")
    for text in (proc, path, _single_spaced(proc), _single_spaced(path)):
        assert uploads.several_patients(text) is False
    # it shows this patient by two identifiers and the date of service: it is this patient's note
    assert uploads.note_identity(proc, "SAMPLE", "PAT", "2026-11-01", ["EMA77"]) == {"ok": True}
    assert uploads.note_identity(_single_spaced(proc), "SAMPLE", "PAT", "2026-11-01", ["EMA77"]) == {"ok": True}
    # no second patient, but no record number either: a person's tick decides (not a refusal)
    ident = uploads.note_identity(path, "SAMPLE", "PAT", "2026-11-01", ["EMA77"])
    assert ident["ok"] is False and ident["code"] == "mismatch" and "EMA patient ID or record number" in ident["why"]
    # one patient's own list with a Patient column (every row theirs) shows no second patient either
    own = ("Patient | DOS | CPT | Charge\n" + "".join(f"Sample, Pat Q | 0{m}/01/2026 | 9921{m} | $1{m}0.00\n" for m in range(1, 6)))
    assert uploads.several_patients(own) is False
    # nor do one patient's statement (its "Patient Balance" column), nor a sentence about the patient just above the rows
    statement = ("Date of Service Description CPT Charges Insurance Paid Patient Balance\n"
                 + "".join(f"0{m}/01/2026 Office visit 9921{m} $1{m}0.00 $80.00 $20.00\n" for m in range(1, 6)))
    eob = statement.replace("Insurance Paid Patient Balance", "Allowed Paid Member Responsibility Account Balance")
    sentence = proc.replace("Date        CPT     Description", "The patient was seen on the date of service below for these CPT codes  ")
    for text in (statement, _single_spaced(statement), eob, sentence, _single_spaced(sentence)):
        assert uploads.several_patients(text) is False, text[:60]
    # a line far longer than a CSV field may be (a PDF read as one long line), with commas in it, is no error
    huge = proc.replace("Procedures performed on the patient today", "Patient, notes, " + "w" * 200_000)
    assert uploads.several_patients(huge) is False and uploads.note_identity(huge, "SAMPLE", "PAT", "2026-11-01", ["EMA77"]) == {"ok": True}
    # lists of patients: by their Patient column, as printed (one space between columns: their birth dates), as CSV, and
    # rows that each state a record number
    report = _charges_report("11/01/2026", "EMA77")
    csv_rows = "Patient,DOB,MRN,DOS,CPT,Charge\n" + "".join(
        f'"{n}",{b},{m},11/01/2026,{c},${a}\n' for n, b, m, c, a in
        [("Sample, Pat Q", "04/12/1961", "EMA77", "99214", "223.65"), ("Zzother, Una", "02/03/1958", "ZZ000481", "99213", "120.00"),
         ("Zzthird, Bo", "09/30/1972", "ZZ000493", "11102", "150.00"), ("Zzfourth, Cy", "12/24/1949", "ZZ000495", "17000", "95.00"),
         ("Zzfifth, Di", "07/07/1985", "ZZ000497", "99212", "85.00")])
    labelled = "".join(f"MRN: ZZ00049{i}  11/01/2026  9921{i}  $1{i}0.00\n" for i in range(5))
    printed = _single_spaced(REPORT.replace(" | ", "  "))         # no birth dates; its columns one space apart, as a PDF reader gives them
    for text in (report, _single_spaced(report), csv_rows, REPORT, labelled, printed):
        assert uploads.several_patients(text) is True, text[:60]
        assert uploads.note_identity(text, "SAMPLE", "PAT", "2026-11-01", ["EMA77"])["code"] == "several"


@pytest.mark.parametrize("ident", ["PMS ID: EMA77", "Patient ID: EMA77"])
def test_mr7_server_a_note_with_a_procedure_list_goes_without_a_tick(site, ident):
    c, line = site.c, site.line
    fid = site.ids["DENIED_MEDICAL_NECESSITY"]
    assert _number(c, PAYER_FAX).status_code == 200
    built = _build(c, fid, _procedure_note("11/01/2026", ident))
    assert built.status_code == 200, built.text
    r = _send(c, fid)
    assert r["status"] == "sent", r
    assert len(line.sent) == 1 and not [e for e in _events(c, fid) if e["kind"] == "note_check"]


def test_mr7_server_a_pathology_history_with_no_record_number_waits_for_a_persons_tick(site):
    c, line = site.c, site.line
    fid = site.ids["DENIED_MEDICAL_NECESSITY"]
    assert _number(c, PAYER_FAX).status_code == 200
    built = _build(c, fid, _pathology_note("11/01/2026"))
    assert built.status_code == 200, built.text
    r = _send(c, fid)
    assert r["status"] == "held" and r["code"] == "note_check" and r["confirm"] is True and r["visit_date"] == "2026-11-01", r
    assert line.sent == {}
    r = _send(c, fid, note_checked=True, packet_id=built.json()["packet_id"])
    assert r["status"] == "sent", r
    assert len([e for e in _events(c, fid) if e["kind"] == "note_check"]) == 1


def test_mr7_server_the_upload_page_keeps_one_patients_notes_and_refuses_a_list_of_patients(site):
    import remitwatch.api as api

    def upload(name, text, finding_id=None):
        tmp = uploads.incoming(api.DB_PATH)
        with open(tmp, "w") as fh:
            fh.write(text)
        return uploads.accept(api.DB_PATH, tmp, name, "oda", finding_id=finding_id)
    fid = site.ids["DENIED_MEDICAL_NECESSITY"]
    out = upload("procedure note.txt", _procedure_note("11/01/2026", "PMS ID: EMA77"))
    assert out["kind"] == "note" and out["status"] == "matched", out
    out = upload("progress note.txt", _pathology_note("11/01/2026"), finding_id=fid)
    assert out["kind"] == "note" and out["status"] == "matched", out
    assert _rows(site.db, "SELECT COUNT(*) FROM uploaded_notes") == [(2,)]
    for name, text in (("charges.txt", _charges_report("11/01/2026", "EMA77")), ("charges printed.txt", _single_spaced(_charges_report("11/01/2026", "EMA77")))):
        out = upload(name, text)
        assert out["status"] == "rejected" and out["reason"] == SEVERAL, out
        assert _build(site.c, fid, text).json()["detail"] == SEVERAL
    assert _rows(site.db, "SELECT COUNT(*) FROM uploaded_notes") == [(2,)]


# --------------------------------------------------------------------------- the serverless host, its claims with a record number
@pytest.fixture()
def slm(box, monkeypatch):
    """The serverless site over a state folder loaded by one run (as tests/test_hotfix2.py's sl), whose reduced visits
    carry the patient's record number on the claim; Claude's review stood in for, a fake fax line on this host, and the
    payer's appeal number saved."""
    from fastapi.testclient import TestClient

    from remitwatch import api, appeals, notes
    root, client, state = box
    claims = visits_with_downcodes()
    claims[-3:] = [c.replace("~REF*CE*", f"~REF*EA*ZZ000{200 + i}~REF*CE*", 1) for i, c in enumerate(claims[-3:])]
    (root / DATA).mkdir(parents=True, exist_ok=True)
    pay = (date.today() - timedelta(days=7)).strftime("%Y%m%d")
    for n, i in enumerate(range(0, len(claims), 10)):
        (root / DATA / f"r{n:03d}.RMT").write_bytes(remit("".join(claims[i:i + 10]), "100.00", seg_count=99, isa_ctrl=f"{n + 1:09d}", pay_date=pay))
    cycle(state, client)
    monkeypatch.setenv("REMITWATCH_MODE", "serverless")
    monkeypatch.delenv("REMITWATCH_ALLOW_OPEN", raising=False)
    monkeypatch.setenv("REMITWATCH_STATE_DIR", str(root / "RemitWatch-state"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(api, "PASSWORD", PW)
    monkeypatch.setattr(api, "USERNAME", "oda")
    monkeypatch.setattr(api, "OWNER_PASSWORD", "")
    monkeypatch.setattr(cloud, "SERVING_COPY", cloud.ServingCopy())
    for k, v in {"name": "Test Dermatology", "address": "1 Main St", "tin": "61-0000000", "npi": "1999999999", "phone": "(270) 555-0100",
                 "fax": "(270) 555-0101", "email": "billing@example.org"}.items():
        monkeypatch.setitem(appeals.PRACTICE, k, v)
    monkeypatch.setattr(notes, "review_note", lambda context, text, pdf_bytes=None, client=None: dict(GOOD_REVIEW, model="test-model"))
    line = FakeLine()
    monkeypatch.setattr(fax, "provider", lambda env=None: line)
    h = dict(XHR, Authorization="Basic " + base64.b64encode(f"oda:{PW}".encode()).decode())
    tc = TestClient(api.app)
    ids = [i["finding_id"] for i in tc.get("/api/findings?signal=DOWNCODE_LEVEL_REDUCED", headers=h).json()["items"]]
    payer = tc.get(f"/api/findings/{ids[0]}", headers=h).json()["finding"]["payer_group"]
    assert tc.post("/api/faxes/payer", json={"payer_group": payer, "number": PAYER_FAX}, headers=h).status_code == 200
    return type("SLM", (), {"c": tc, "h": h, "ids": ids, "line": line, "state": state})


def test_mr7_serverless_one_patients_tables_go_as_their_note_and_a_list_of_patients_is_refused(slm):
    c, h, line = slm.c, slm.h, slm.line
    first, second, third = slm.ids
    own = cloud.SERVING_COPY.connection().execute("""SELECT c.medical_record_number FROM findings f JOIN claims c ON c.claim_key=f.claim_key
                                                     WHERE f.finding_id=?""", (first,)).fetchone()[0]
    assert own and own.startswith("ZZ000")

    def build(fid, text):
        return c.post("/api/appeals/build", data={"finding_id": fid, "text": text, "use_ai": "1"}, headers=h)

    def send(fid, **kw):
        return c.post("/api/appeals/send", json=dict(finding_id=fid, **kw), headers=h).json()
    # the procedure list, with this patient's record number: their note, faxed without a tick
    built = build(first, _procedure_note("04/01/2026", f"MRN: {own}"))
    assert built.status_code == 200, built.text
    r = send(first)
    assert r["status"] == "sent", r
    # the pathology history, no record number: held for the tick, then it goes
    built = build(second, _pathology_note("04/01/2026"))
    assert built.status_code == 200, built.text
    r = send(second)
    assert r["status"] == "held" and r["code"] == "note_check" and r["confirm"] is True, r
    r = send(second, note_checked=True, packet_id=built.json()["packet_id"])
    assert r["status"] == "sent", r
    # a list of patients is still refused, as printed and as sent through the page
    for text in (_charges_report("04/01/2026", own), _single_spaced(_charges_report("04/01/2026", own))):
        r = build(third, text)
        assert r.status_code == 400 and r.json()["detail"] == SEVERAL, r.text
    assert len(line.sent) == 2


# =========================================================================== mr-11: a body that is not an object
NOT_OBJECTS = [b"not json", b"{", b"[1, 2]", b'"appealed"', b"5", b"true"]
UNREADABLE = "The request could not be read. Reload the page and try again."


def _posted(c, url, headers) -> list[tuple]:
    out = []
    for raw in NOT_OBJECTS:
        r = c.post(url, content=raw, headers=dict(headers, **{"Content-Type": "application/json"}))
        out.append((raw, r.status_code, r.json().get("detail")))
    return out


def test_mr11_server_appeals_send_and_hide_refuse_a_body_that_is_not_an_object_plainly(site):
    c = site.c
    before = _rows(site.db, "SELECT finding_id, status FROM findings ORDER BY finding_id")
    for url in ("/api/appeals/send", "/api/hide"):
        for raw, code, detail in _posted(c, url, XHR):
            assert code == 400 and detail == UNREADABLE, (url, raw, code, detail)
    assert site.line.sent == {} and _rows(site.db, "SELECT finding_id, status FROM findings ORDER BY finding_id") == before
    # what is not about the body keeps FastAPI's answer
    assert c.get("/api/hide/preview").status_code == 422 and c.get("/api/findings?limit=-1").status_code == 422
    # the routes stay plain functions: a send's review and fax must not hold up every other request
    import inspect

    import remitwatch.api as api
    assert not inspect.iscoroutinefunction(api.appeals_send) and not inspect.iscoroutinefunction(api.hide_add)


def test_mr11_serverless_appeals_send_and_hide_refuse_a_body_that_is_not_an_object_before_the_journal(sl):
    c, h = sl.c, sl.h
    n = len(_journal(sl.state))
    for url in ("/api/appeals/send", "/api/hide"):
        for raw, code, detail in _posted(c, url, h):
            assert code == 400 and detail == UNREADABLE, (url, raw, code, detail)
    assert len(_journal(sl.state)) == n
    assert c.get("/api/hide/preview", headers=h).status_code == 422


# =========================================================================== mr-12: what the send-appeals command prints
def test_mr12_send_appeals_prints_no_date_from_a_held_items_reason(site, capsys):
    """A held item's reason can name the visit's date (note_check: "if the note is this patient's visit on <date>") or
    repeat what Claude's review said about the note. The command's output is a log; it carries the item's id, what
    happened and the code, and the item's panel gives the reason."""
    import json

    from remitwatch import cli
    c = site.c
    fid, rate = site.ids["DENIED_MEDICAL_NECESSITY"], site.ids["UNDERPAID_VS_OWN_RATE"]
    assert _number(c, PAYER_FAX).status_code == 200
    assert _build(c, fid, OTHER_NOTE).status_code == 200            # another patient's note: it waits for a person's look
    # push 2's states route a medical-necessity denial to a person's decision first (appeal_block 'check_first'), which
    # keeps it off the bulk list; here it is an item the list takes, so the send itself meets the note check
    import sqlite3
    con = sqlite3.connect(site.db)
    con.execute("UPDATE findings SET appeal_block=NULL WHERE finding_id=?", (fid,))
    con.commit()
    con.close()
    capsys.readouterr()
    assert cli.main(["--db", site.db, "send-appeals"]) == 0
    out = capsys.readouterr().out
    lines = out.strip().splitlines()
    assert [ln.split() for ln in lines if ln.startswith(fid)] == [[fid, "held", "note_check"]], out
    assert [ln.split() for ln in lines if ln.startswith(rate)] == [[rate, "sent"]], out
    assert json.loads(lines[-1]) == {"appealable": 2, "held": 1, "sent": 1}
    # no date of the visit, nor any other date, in what the command prints
    assert "2026-11-01" not in out and not any(rx.search(out) for rx, _k in uploads.DATE_RES), out
    # a send that fails with a message: its HTTP status, not the message
    import remitwatch.api as api
    from fastapi import HTTPException

    def fails(body, user):
        raise HTTPException(400, "The note of the visit on 11/01/2026 could not be read.")
    site.monkeypatch.setattr(api, "appeals_send", fails)
    assert cli.main(["--db", site.db, "send-appeals"]) == 1
    out = capsys.readouterr().out
    assert out.strip().splitlines()[0].split() == [fid, "failed", "400"], out
    assert not any(rx.search(out) for rx, _k in uploads.DATE_RES), out
