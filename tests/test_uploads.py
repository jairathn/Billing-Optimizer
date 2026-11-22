"""
The manual ModMed workflow (remitwatch.uploads and the Upload page): EMA's exports, remittance files and visit notes
brought in by hand land where the automated feeds put them, through the same loader and the same note matching; the
same file twice changes nothing; every upload says what it held, what was added, what was already there, and what was
left out and why; the Vercel site keeps working and says uploads belong to the server.

The exports below are built in the layouts the practice's EMA exports use (the column names remitwatch.ema_exports reads
from the Dropbox folder), with a report title above the columns and a totals line below, as a report saved from EMA has.
Every patient, claim, clinician and amount is made up.
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import os
import re
import sqlite3
import stat
import zipfile

import pytest

from remitwatch import ema_exports, uploads

XHR = {"X-Requested-With": "remitwatch"}
DOS = dt.datetime(2026, 11, 1)
DENIED = "CB0000300C017"            # the visit the payer denied for medical necessity (CARC 50): its note answers it
PAID_ELSEWHERE = "CB0000301C017"    # a visit the payer denied for missing information that EMA shows paid since

# ----------------------------------------------------------------------------------------------- exports, as EMA saves them
CHARGE_COLS = ["Service Date", "Transaction Date", "Primary Provider", "Primary Provider Role", "Patient ID", "Bill ID", "Bill Creation Date",
               "Claim ID", "Bill Item Identifier", "CPT/Product", "Modifiers", "Units", "ICD-10", "Charges", "Payments", "Adjustments",
               "Insurance Payments", "Activity Type", "Payer", "Payer ID", "Location"]
ERA_COLS = ["Transaction Date", "Service Date", "Claim ID", "CPT", "Modifiers", "Payer", "Reason Code", "Reason Code Description",
            "RARC Code", "RARC Code Description", "Adjustment Status", "Adjustment Subtype", "Adjustments", "Payments", "Patient ID"]
STATUS_COLS = ["Claim ID", "Patient ID", "Service Date", "CPT", "Payer", "Claim Status", "Claim Submission Date", "Denial Date",
               "Denial Amount", "Denial Category", "Reason Code", "Previously Denied?"]


def charge_rows() -> list[list]:
    return [
        [DOS, dt.datetime(2026, 11, 3), "Provider, Test", "Rendering", "EMA77", "B300", dt.datetime(2026, 11, 2), DENIED, 7000300, "99214", None, 1,
         "L98.9", 223.65, 0, 0, 0, "Charge", "Anthem Blue Cross", "00660", "Main Office"],
        [DOS, dt.datetime(2026, 11, 3), "Provider, Test", "Rendering", "EMA78", "B301", dt.datetime(2026, 11, 2), PAID_ELSEWHERE, 7000301, "99214", None, 1,
         "L57.0", 223.65, 117.60, 106.05, 117.60, "Charge", "Anthem Blue Cross", "00660", "Main Office"],
        [DOS, dt.datetime(2026, 11, 3), "Provider, Test", "Rendering", "EMA78", "B301", dt.datetime(2026, 11, 2), PAID_ELSEWHERE, 7000302, "17000", None, 1,
         "L57.0", 150.00, 0, 0, 0, "Charge", "Anthem Blue Cross", "00660", "Main Office"],
    ]


def era_rows() -> list[list]:
    return [[dt.datetime(2026, 11, 10), DOS, DENIED, "99214", None, "Anthem Blue Cross", "CO-50", "Not medically necessary", "N115",
             "Decision based on LCD", "IGNORE", None, 0, 0, "EMA77"]]


def status_rows() -> list[list]:
    return [[DENIED, "EMA77", DOS, "99214", "Anthem Blue Cross", "Denied", dt.datetime(2026, 11, 2), dt.datetime(2026, 11, 10), 223.65,
             "Medical necessity", "CO-50", "No"]]


def xlsx(title: str, cols: list[str], rows: list[list], total: int | None = None, blank_inside: bool = False,
         created: dt.datetime = dt.datetime(2026, 11, 14, 9, 30)) -> bytes:
    """A workbook as EMA saves a report: its title and range above the column names, a totals line below."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append([title])
    ws.append(["Service Date: 09/20/2026 - 11/01/2026"])
    ws.append([])
    ws.append(cols)
    for i, r in enumerate(rows):
        ws.append(r)
        if blank_inside and i == 0:
            ws.append([None] * len(cols))
    if total is not None:
        line = [None] * len(cols)
        line[0] = "Total"
        line[total] = round(sum(r[total] or 0 for r in rows), 2)
        ws.append(line)
    wb.properties.created = created
    wb.properties.modified = created          # openpyxl stamps the save time otherwise: the same rows must give the same bytes
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def as_csv(cols: list[str], rows: list[list], delimiter: str = ",", money: set[str] = frozenset()) -> bytes:
    """The same rows as EMA's CSV writes them: dates as month/day/year, amounts as text (some with a dollar sign)."""
    out = io.StringIO()
    w = csv.writer(out, delimiter=delimiter, lineterminator="\r\n")
    w.writerow(["Encounter & Charge Lines"])
    w.writerow(cols)
    for r in rows:
        cells = []
        for c, v in zip(cols, r):
            if isinstance(v, dt.datetime):
                cells.append(f"{v.month:02d}/{v.day:02d}/{v.year}")
            elif isinstance(v, float) and c in money:
                cells.append(f"${v:,.2f}")
            elif isinstance(v, float):
                cells.append(f"{v:.2f}")
            elif v is None:
                cells.append("")
            else:
                cells.append(str(v))
        w.writerow(cells)
    return out.getvalue().encode("utf-8-sig")


def note_pdf(lines: list[str]) -> bytes:
    """A visit note as EMA prints one: a text PDF (made the same byte for byte each time)."""
    from reportlab.pdfgen import canvas
    b = io.BytesIO()
    c = canvas.Canvas(b, invariant=1)
    y = 800
    for ln in lines:
        c.drawString(40, y, ln)
        y -= 16
    c.save()
    return b.getvalue()


NOTE = ["Test Dermatology  -  Progress Note", "Patient: Pat Sample    DOB: 01/02/1960    Patient ID: EMA77", "Date of Service: 11/01/2026",
        "Chief complaint: a changing mole on the back, enlarging over three months.",
        "History: the patient noticed the lesion growing and darkening; no bleeding. Prior visit 08/14/2026 for a rash.",
        "Exam: 6 mm irregular pigmented papule, left upper back. Assessment: atypical nevus, rule out melanoma.",
        "Plan: shave biopsy today after consent; pathology sent; follow up in two weeks. Signed 11/02/2026 by Test Provider."]


def scanned_pdf() -> bytes:
    """A page with a picture and no text, as a scanned note is."""
    from reportlab.pdfgen import canvas
    b = io.BytesIO()
    c = canvas.Canvas(b, invariant=1)
    c.rect(40, 600, 300, 150, fill=1)
    c.save()
    return b.getvalue()


# ----------------------------------------------------------------------------------------------- the store and the site
def remittances() -> list[str]:
    from tests.test_features import NPI, clean_visits, svc
    denied = ("CLP*CB0000300C017*1*223.65*0.00*0.00*12*PC300*11~NM1*QC*1*SAMPLE*PAT*Q***MI*M00000300~"
              f"NM1*82*2*TEST PROVIDER*****XX*{NPI}~REF*1W*MEMB999888~DTM*232*20261101~"
              + svc("99214", "223.65", "0.00", None, "CAS*CO*50*223.65~", dos="20261101"))
    missing = ("CLP*CB0000301C017*1*223.65*0.00*0.00*12*PC301*11~NM1*QC*1*ORCHARD*LEE****MI*W55500301~"
               f"NM1*82*2*TEST PROVIDER*****XX*{NPI}~DTM*232*20261101~" + svc("99214", "223.65", "0.00", None, "CAS*CO*16*223.65~", dos="20261101"))
    return clean_visits(1, 25, "99214", "223.65", "117.60") + [denied, missing]


def make_store(folder) -> str:
    from remitwatch.api import ensure_server_schema
    from remitwatch.detect import Detector
    from tests.test_features import build_store
    os.makedirs(folder, exist_ok=True)
    store = build_store(folder, remittances(), pay_date="20261110")
    Detector(store, run_id="t").run()
    store.close()
    db = os.path.join(str(folder), "s.sqlite")
    ensure_server_schema(db)
    return db


def new_remittance() -> bytes:
    """A remittance file the store does not have yet: two more visits paid."""
    from tests.test_features import clean_visits
    from tests.test_remitwatch import remit
    return remit("".join(clean_visits(500, 2, "99213", "150.00", "80.00", dos="20261103")), "160.00", seg_count=99, isa_ctrl="000000777",
                 pay_date="20261112")


@pytest.fixture()
def site(tmp_path, monkeypatch):
    """The dashboard on a server's store, signed in, with the cycle's wake-up recorded instead of run (the tests run the
    cycle themselves)."""
    from fastapi.testclient import TestClient

    import remitwatch.api as api
    db = make_store(tmp_path / "server")
    monkeypatch.delenv("REMITWATCH_MODE", raising=False)
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.setattr(api, "DB_PATH", db)
    monkeypatch.setattr(api, "PASSWORD", "")
    woken: list[str] = []
    monkeypatch.setattr(api, "_wake_cycle", lambda trigger="upload": woken.append(trigger))
    for k in ("MODMED_CLIENT_ID", "MODMED_PRIVATE_KEY", "MODMED_CLIENT_SECRET", "DROPBOX_APP_KEY", "DROPBOX_APP_SECRET",
              "DROPBOX_REFRESH_TOKEN", "TRIZETTO_SFTP_USER", "SMTP_HOST", "ANTHROPIC_API_KEY", "REMITWATCH_UPLOAD_DIR"):
        monkeypatch.delenv(k, raising=False)
    c = TestClient(api.app)

    def up(name: str, data: bytes, **q) -> dict:
        r = c.post("/api/uploads", params=dict(name=name, **q), content=data, headers=XHR)
        assert r.status_code == 200, r.text
        return r.json()
    return type("Site", (), {"c": c, "db": db, "up": staticmethod(up), "woken": woken, "tmp": tmp_path})


def cycle(db: str, **kw) -> dict:
    from remitwatch.pipeline import run_cycle
    return run_cycle(db, email=False, report=False, report_dir=os.path.join(os.path.dirname(db), "reports"), log=lambda *a: None, **kw)


def snapshot(db: str) -> dict:
    """Every row of every table, so that "nothing changed" is checked on all of the store."""
    conn = sqlite3.connect(db)
    try:
        out = {}
        for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
            rows = sorted(repr(r) for r in conn.execute(f'SELECT * FROM "{t}"'))
            out[t] = hashlib.sha256("\n".join(rows).encode()).hexdigest()
        return out
    finally:
        conn.close()


def ema_row(db: str, claim_id: str, patient_id: str, fp: str | None = None) -> None:
    """The claim's row in a week's EMA export: the claim ID with EMA's patient ID (the Encounter & Charge Lines layout)."""
    conn = sqlite3.connect(db)
    try:
        conn.execute("INSERT INTO ema_rows (row_fingerprint, family, claim_id, patient_id) VALUES (?,?,?,?)",
                     (fp or f"fp-{claim_id}-{patient_id}", "charge_lines", claim_id, patient_id))
        conn.commit()
    finally:
        conn.close()


def table(db: str, sql: str) -> list[tuple]:
    conn = sqlite3.connect(db)
    try:
        return sorted(tuple(r) for r in conn.execute(sql))
    finally:
        conn.close()


def findings(db: str) -> list[tuple]:
    conn = sqlite3.connect(db)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(findings)") if r[1] not in ("run_id", "first_seen", "last_seen") and not r[1].endswith("_at")]
        return sorted(tuple(r) for r in conn.execute(f"SELECT {','.join(cols)} FROM findings"))
    finally:
        conn.close()


# ----------------------------------------------------------------------------------------------- reading an export
def test_a_csv_and_an_excel_export_of_the_same_rows_read_the_same(tmp_path):
    rows = charge_rows()
    paths = {}
    for name, data in {"Encounter & Charge Lines.xlsx": xlsx("Encounter & Charge Lines", CHARGE_COLS, rows),
                       "Encounter & Charge Lines.csv": as_csv(CHARGE_COLS, rows, money={"Charges", "Insurance Payments"}),
                       "Encounter & Charge Lines.tsv": as_csv(CHARGE_COLS, rows, delimiter="\t"),
                       "pipe.csv": as_csv(CHARGE_COLS, rows, delimiter="|")}.items():
        (tmp_path / name).write_bytes(data)
        paths[name] = str(tmp_path / name)
    read = {n: list(ema_exports.iter_rows(p)) for n, p in paths.items()}
    strip = lambda rs: [{k: v for k, v in r.items() if k not in ("source_sha256", "source_name", "row_index")} for r in rs]    # noqa: E731
    base = strip(read["Encounter & Charge Lines.xlsx"])
    assert len(base) == 3 and base[0]["family"] == "charge_lines" and base[1]["insurance_payments_cents"] == 11760
    assert base[0]["service_date"] == "2026-11-01" and base[0]["bill_created"] == "2026-11-02" and base[0]["patient_id"] == "EMA77"
    for n in paths:
        assert strip(read[n]) == base, n                     # every value, and so every row's identity, the same
    assert ema_exports._csv_cell("($1,234.50)") == -1234.5 and ema_exports._csv_cell("00123") == "00123"
    assert ema_exports._csv_cell("11/01/26") == dt.datetime(2026, 11, 1) and ema_exports._csv_cell("TRUE") is True


def test_every_line_left_out_is_counted_with_its_reason(tmp_path):
    p = tmp_path / "Encounter & Charge Lines.xlsx"
    p.write_bytes(xlsx("Encounter & Charge Lines", CHARGE_COLS, charge_rows() + [charge_rows()[0]], total=13, blank_inside=True))
    acct: dict = {}
    rows = list(ema_exports.iter_rows(str(p), acct))
    assert len(rows) == 4 and acct["above"] == 2 and acct["header_index"] == 3 and acct["left"] == {"blank": 1, "total": 1, "footer": 0}
    assert len({r["row_fingerprint"] for r in rows}) == 3            # the repeated row: one identity, kept once by the store


# ----------------------------------------------------------------------------------------------- the same place as the feed
def test_an_upload_lands_where_the_same_files_from_dropbox_land(tmp_path, monkeypatch, site):
    """The automated route (the Dropbox folder, read by the server's cycle) and the manual one (the Upload page, read by
    the same cycle) end in the same store: the same EMA rows and export records, the same remittances, the same findings,
    the same pages."""
    from remitwatch import dropbox_sync
    from tests.test_sync import FakeClient
    files = {
        "/Trizetto Data/Reports/Encounter & Charge Lines.xlsx": xlsx("Encounter & Charge Lines", CHARGE_COLS, charge_rows(), total=13),
        "/Trizetto Data/Reports/ERA & Payment Lines.xlsx": xlsx("ERA & Payment Line Adjustments", ERA_COLS, era_rows()),
        "/Trizetto Data/Reports/Claim & Claim Status.xlsx": xlsx("Claim & Claim Status", STATUS_COLS, status_rows()),
        "/Trizetto Data/Trizetto/remit-777.RMT": new_remittance(),
    }
    # the automated route
    feed_db = make_store(tmp_path / "feed")
    for k, v in {"DROPBOX_APP_KEY": "k", "DROPBOX_APP_SECRET": "s", "DROPBOX_REFRESH_TOKEN": "r", "DROPBOX_ROOT_PATH": "/Trizetto Data",
                 "REMITWATCH_DATA_DIR": str(tmp_path / "mirror")}.items():
        monkeypatch.setenv(k, v)
    client = FakeClient(files)
    monkeypatch.setattr(dropbox_sync, "DropboxClient", lambda *a, **kw: client)
    feed = cycle(feed_db)
    assert feed["ingest"] == {"remits": 1, "ema": 3, "claims_837": 0, "skipped": 0, "errors": 0, "failed": []} and "detect" in feed
    # the manual route: the same files, uploaded, read by the next cycle (no Dropbox there)
    for path, data in files.items():
        r = site.up(os.path.basename(path), data)
        assert r["status"] == "queued", r
    assert site.woken == ["upload"] * 4
    by_hand = cycle(site.db, sync=False)
    assert by_hand["uploads"]["uploads"] == 4 and by_hand["ingest"]["ema"] == 3 and by_hand["ingest"]["remits"] == 1 and "detect" in by_hand
    # the same rows, the same records of each export, the same remittances, the same findings
    ema_cols = "row_fingerprint, family, source_sha256, source_name, row_index, service_date, claim_id, cpt, patient_id, insurance_payments_cents, carc, adjustment_status, claim_status, denial_amount_cents, bill_created"
    assert table(site.db, f"SELECT {ema_cols} FROM ema_rows") == table(feed_db, f"SELECT {ema_cols} FROM ema_rows")
    assert len(table(site.db, "SELECT * FROM ema_rows")) == 5
    assert table(site.db, "SELECT source_name, sha256, family, created_at, rows FROM ema_sources") == \
        table(feed_db, "SELECT source_name, sha256, family, created_at, rows FROM ema_sources")
    assert table(site.db, "SELECT transaction_key, payer_name, payment_date, payment_amount_cents, content_fingerprint, superseded FROM transactions") == \
        table(feed_db, "SELECT transaction_key, payer_name, payment_date, payment_amount_cents, content_fingerprint, superseded FROM transactions")
    assert table(site.db, "SELECT claim_key, patient_control_number, paid_cents FROM claims") == table(feed_db, "SELECT claim_key, patient_control_number, paid_cents FROM claims")
    assert findings(site.db) == findings(feed_db)
    # and what EMA shows did the same in both: the visit EMA shows paid is closed, naming the export it was seen in
    st = dict(table(site.db, f"SELECT patient_control_number, status || ' | ' || COALESCE(auto_note,'') FROM findings WHERE category='denial' AND patient_control_number IN ('{DENIED}','{PAID_ELSEWHERE}')"))
    assert st[PAID_ELSEWHERE].startswith("resolved | EMA shows $117.60 paid by insurance (Encounter & Charge Lines.xlsx)") and st[DENIED].startswith("open")
    # the pages people use answer the same from either store (but for the clock: when each run saw each item)
    import remitwatch.api as api
    from tests.test_parity import _strip

    import re

    def clockless(x):
        """The two runs differ only by when they ran: a full timestamp (a date with a time) is left out; dates, amounts and
        statuses are compared."""
        if isinstance(x, dict):
            return {k: clockless(v) for k, v in x.items() if k not in ("run_id", "snapshots")}
        if isinstance(x, list):
            return [clockless(v) for v in x]
        return "<time>" if isinstance(x, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}.*", x) else x
    for page in ("/api/summary", "/api/findings?status=all&limit=500", "/api/worklist/counts", "/api/desk?category=denial", "/api/coverage", "/api/progress"):
        monkeypatch.setattr(api, "DB_PATH", feed_db)
        a = site.c.get(page).json()
        monkeypatch.setattr(api, "DB_PATH", site.db)
        b = site.c.get(page).json()
        assert clockless(_strip(a)) == clockless(_strip(b)), page
    # each upload says what the cycle added
    hist = {i["name"]: i for i in site.c.get("/api/uploads").json()["items"]}
    charge = hist["Encounter & Charge Lines.xlsx"]
    assert charge["status"] == "done" and any(ln.startswith("Added 3 rows on") for ln in charge["lines"])
    assert any("1 totals line" in ln and "2 lines above the column names" in ln for ln in charge["lines"])
    assert any(ln.startswith("Added 1 payment on") for ln in hist["remit-777.RMT"]["lines"])
    # kept beside the store, owner-only, under its own name
    kept = table(site.db, "SELECT path FROM uploads WHERE kind='ema'")
    for (path,) in kept:
        assert path.startswith(os.path.join(os.path.dirname(site.db), "uploads", "exports") + os.sep)
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600 and stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode) == 0o700
    assert sorted(os.path.basename(p) for (p,) in kept) == ["Claim & Claim Status.xlsx", "ERA & Payment Lines.xlsx", "Encounter & Charge Lines.xlsx"]


def test_the_same_file_twice_changes_nothing(site):
    data = xlsx("Encounter & Charge Lines", CHARGE_COLS, charge_rows(), total=13)
    first = site.up("Encounter & Charge Lines.xlsx", data)
    assert first["status"] == "queued" and first["new"] == 3 and first["already"] == 0
    assert "New to RemitWatch: 3. Already in it: 0." in first["lines"][1]
    cycle(site.db, sync=False)
    note = site.up("visit note.pdf", note_pdf(NOTE))
    assert note["status"] == "matched"
    before, woken = snapshot(site.db), len(site.woken)
    # the same bytes again, under any name: refused as already there, and not one row anywhere changes
    for name in ("Encounter & Charge Lines.xlsx", "Encounter & Charge Lines (1).xlsx"):
        again = site.up(name, data)
        assert again["status"] == "already" and again["lines"][0].startswith("This exact file was already uploaded on ")
    again = site.up("visit note (1).pdf", note_pdf(NOTE))
    assert again["status"] == "already"
    assert snapshot(site.db) == before and len(site.woken) == woken
    # a later export of the same rows (EMA writes a new file each time): read, and nothing is added
    later = site.up("Encounter & Charge Lines.xlsx", xlsx("Encounter & Charge Lines", CHARGE_COLS, charge_rows(), created=dt.datetime(2026, 11, 21, 8)))
    assert later["status"] == "queued" and later["new"] == 0 and later["already"] == 3
    rows_before = table(site.db, "SELECT * FROM ema_rows")
    cycle(site.db, sync=False)
    assert table(site.db, "SELECT * FROM ema_rows") == rows_before
    done = next(i for i in site.c.get("/api/uploads").json()["items"] if i["name"] == "Encounter & Charge Lines.xlsx" and i["status"] == "done"
                and any("Added 0 rows" in ln for ln in i["lines"]))
    assert any("Already in RemitWatch: 3." in ln for ln in done["lines"])
    # its name was taken by the first export: it is kept under the day it came, so each export stays its own
    names = sorted(r[0] for r in table(site.db, "SELECT source_name FROM ema_sources"))
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    assert names == [f"Encounter & Charge Lines (uploaded {today}).xlsx", "Encounter & Charge Lines.xlsx"]


# ----------------------------------------------------------------------------------------------- visit notes
def test_a_visit_note_uploaded_reaches_the_appeal_as_one_from_emas_api(tmp_path, monkeypatch):
    """The note behind an appeal, once from ModMed's API (FHIR, as the Bulk FHIR app will give it) and once uploaded by
    hand: the same review, the same letter, the same packet, the same fax. And the same caution: with only the name and
    the date to go by, both hold the item for a person; once the week's export gives the claim EMA's patient ID (which
    the uploaded note states too), both send it."""
    from fastapi.testclient import TestClient

    import remitwatch.api as api
    from remitwatch import appeals, fax, notes
    from remitwatch import modmed as M
    from tests.fake_fax import FakeLine
    from tests.fake_modmed import FakeModMed
    from tests.test_fax import GOOD_REVIEW
    from tests.test_features import NPI
    from tests.test_modmed import PEM
    pdf = note_pdf(NOTE)
    for k, v in {"name": "Test Dermatology", "address": "1 Main St, Owensboro KY", "tin": "61-0000000", "npi": "1999999999",
                 "phone": "(270) 555-0100", "fax": "(270) 555-0101", "email": "billing@example.org"}.items():
        monkeypatch.setitem(appeals.PRACTICE, k, v)
    seen: list[str] = []
    monkeypatch.setattr(notes, "review_note", lambda context, text, pdf_bytes=None, client=None: (seen.append(text), dict(GOOD_REVIEW, model="test-model"))[1])
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(api, "PASSWORD", "")
    monkeypatch.setattr(api, "_wake_cycle", lambda trigger="upload": None)
    monkeypatch.setattr(fax, "configured", lambda env=None: True)
    for k in ("MODMED_CLIENT_ID", "MODMED_PRIVATE_KEY", "MODMED_CLIENT_SECRET", "REMITWATCH_MODE"):
        monkeypatch.delenv(k, raising=False)
    c = TestClient(api.app)
    sent = {}
    for route in ("api", "upload"):
        db = make_store(tmp_path / route)
        monkeypatch.setattr(api, "DB_PATH", db)
        line = FakeLine()
        monkeypatch.setattr(fax, "provider", lambda env=None, line=line: line)
        mm = None
        if route == "api":
            mm = FakeModMed(jwk=M.public_jwk(M.load_private_key(PEM))).start()
            mm.add_patient("EMA77", "Sample", "Pat")
            mm.add_practitioner("prx", NPI, "Test Provider")
            mm.add_encounter("ex", "EMA77", "2026-11-01")
            mm.add_note("nx", "EMA77", "2026-11-02", pdf, encounter="ex", author="prx")
            monkeypatch.setenv("MODMED_CLIENT_ID", mm.client_id)
            monkeypatch.setenv("MODMED_PRIVATE_KEY", PEM.replace("\n", "\\n"))
            monkeypatch.setenv("MODMED_FHIR_BASE", mm.fhir_base)
        else:
            for k in ("MODMED_CLIENT_ID", "MODMED_PRIVATE_KEY", "MODMED_FHIR_BASE"):
                monkeypatch.delenv(k, raising=False)
            assert c.get("/api/meta").json()["modmed"] is False and c.get("/api/meta").json()["uploads"] is True
            r = c.post("/api/uploads", params={"name": "Sample Pat 11-01.pdf"}, content=pdf, headers=XHR).json()
            assert r["status"] == "matched" and r["note"]["claims"] == [DENIED] and r["note"]["service_date"] == "2026-11-01"
            assert "patient" not in r["note"]                     # names only where the site shows names
        try:
            fid = c.get("/api/findings?signal=DENIED_MEDICAL_NECESSITY").json()["items"][0]["finding_id"]
            assert c.post("/api/faxes/payer", json={"payer_group": "Anthem / BCBS", "number": "(800) 555-0142"}, headers=XHR).status_code == 200
            held = c.post("/api/appeals/send", json={"finding_id": fid, "stale_ok": True}, headers=XHR).json()
            assert held["status"] == "held" and held["code"] == "ambiguous" and not line.sent, held
            ema_row(db, DENIED, "EMA77")
            out = c.post("/api/appeals/send", json={"finding_id": fid, "stale_ok": True}, headers=XHR).json()
            assert out["status"] == "sent", out
            p = c.get(f"/api/packets/{out['packet_id']}").json()
            sent[route] = {"packet": p, "fax": line.sent, "pages": out["pages"], "review_text": seen[-1]}
        finally:
            if mm:
                mm.stop()
    a, b = sent["api"], sent["upload"]
    assert a["review_text"] == b["review_text"] and "enlarging over three months" in a["review_text"]
    for k in ("subject", "letter", "analysis", "evidence", "attachments", "mode", "note_kind", "deadline"):
        assert a["packet"].get(k) == b["packet"].get(k), k
    assert a["pages"] == b["pages"] and len(a["fax"]) == len(b["fax"]) == 1


def test_notes_are_matched_by_patient_and_date_or_by_a_person(site, monkeypatch):
    c = site.c
    fid = c.get("/api/findings?signal=DENIED_MEDICAL_NECESSITY").json()["items"][0]["finding_id"]
    need = c.get("/api/uploads/needed").json()
    assert [i["finding_id"] for i in need["items"]] == [fid] and need["items"][0]["note"] == "not_found" and need["covered"] == 0
    assert "patient" not in need["items"][0]
    # a note for a visit RemitWatch has no item for: kept, and said so
    other = site.up("other.pdf", note_pdf(["Progress Note", "Patient: Ann Nobody", "Date of Service: 10/15/2026", "Exam and plan."]))
    assert other["status"] == "unmatched" and "No item in RemitWatch" in other["lines"][0]
    # a scanned note cannot be read: attached to its item by hand instead
    scan = site.up("scan.pdf", scanned_pdf())
    assert scan["status"] == "unmatched" and "scanned picture" in scan["lines"][0]
    attached = site.up("scan.pdf", scanned_pdf(), finding_id=fid)
    assert attached["status"] == "matched" and attached["note"]["matched_by"] == "person" and attached["note"]["claims"] == [DENIED]
    need = c.get("/api/uploads/needed").json()
    assert need["items"][0]["note"] == "found" and need["covered"] == 1
    assert c.get("/api/appeals/ema_queue?ready=1").json()["items"][0]["finding_id"] == fid
    # the text note for the same visit, matched by its name and date only (this claim has no EMA patient ID to check the
    # note's against): it waits for a person, and the note the person attached is the one used meanwhile
    good = site.up("visit.pdf", note_pdf(NOTE))
    assert good["status"] == "matched" and good["note"]["confirm"] is True and "once a person confirms it" in good["lines"][0]
    r = c.post("/api/appeals/from_ema", json={"finding_id": fid, "use_ai": False}, headers=XHR).json()
    assert r["status"] == "built" and r["source"] == "upload" and r["note"]["id"] == "upload-" + attached["note"]["note_id"]
    assert r["identified_by"] == ["a person"] and r["others"] == []
    # attached to the item too: both are the person's, and the progress note is the one used (as with EMA's API)
    again = site.up("visit.pdf", note_pdf(NOTE), finding_id=fid)
    assert again["note"]["matched_by"] == "person"
    r = c.post("/api/appeals/from_ema", json={"finding_id": fid, "use_ai": False}, headers=XHR).json()
    assert r["status"] == "built" and r["note"]["type"] == "Progress note" and len(r["others"]) == 1
    # the person can switch to the other one, as with EMA's notes
    r2 = c.post("/api/appeals/from_ema", json={"finding_id": fid, "use_ai": False, "document_id": r["others"][0]["id"]}, headers=XHR).json()
    assert r2["status"] == "built" and r2["note"]["id"] == r["others"][0]["id"]
    # taken back: the file goes, the packet built from it stays
    nid = attached["note"]["note_id"]
    path = table(site.db, f"SELECT path FROM uploaded_notes WHERE note_id='{nid}'")[0][0]
    assert c.post(f"/api/uploads/notes/{nid}/remove", headers=XHR).json() == {"removed": True}
    assert not os.path.exists(path) and c.get(f"/api/uploads/notes/{nid}/file").status_code == 404
    assert len(c.get(f"/api/packets?finding_id={fid}").json()) == 3            # each packet built stays
    # a note view, for checking it is the right one
    gid = good["note"]["note_id"]
    v = c.get(f"/api/uploads/notes/{gid}/file")
    assert v.status_code == 200 and v.content[:5] == b"%PDF-" and v.headers["content-type"] == "application/pdf"


def test_two_patients_of_one_name_that_day_are_told_apart_or_left_to_a_person(site):
    conn = sqlite3.connect(site.db)
    # a second Pat Sample seen the same day, on a claim of her own, another EMA patient
    conn.execute("""INSERT INTO claims (claim_key, transaction_key, patient_control_number, patient_last, patient_first, statement_from)
                    VALUES ('k-twin', 't-twin', 'CB0000999C017', 'SAMPLE', 'PAT', '2026-11-01')""")
    conn.execute("""INSERT INTO findings (finding_id, signal, category, claim_key, patient_control_number, service_date, payer_group, status)
                    VALUES ('f-twin', 'DENIED_MEDICAL_NECESSITY', 'denial', 'k-twin', 'CB0000999C017', '2026-11-01', 'Humana', 'open')""")
    conn.executemany("INSERT INTO ema_rows (row_fingerprint, family, claim_id, patient_id) VALUES (?,?,?,?)",
                     [("tw1", "charge_lines", DENIED, "EMA77"), ("tw2", "charge_lines", "CB0000999C017", "EMA90")])
    conn.commit()
    conn.close()
    # the note states its patient ID: the one patient it names
    one = site.up("note.pdf", note_pdf(NOTE))
    assert one["status"] == "matched" and one["note"]["claims"] == [DENIED]
    # without it: both visits fit, and a person picks
    plain = [ln.replace("    Patient ID: EMA77", "") for ln in NOTE] + ["Addendum."]
    two = site.up("note2.pdf", note_pdf(plain))
    assert two["status"] == "choose" and len(two["note"]["candidates"]) == 2 and "Pick the right one" in two["lines"][0]
    pick = next(i for i, cnd in enumerate(two["note"]["candidates"]) if cnd["claims"] == ["CB0000999C017"])
    r = site.c.post(f"/api/uploads/notes/{two['note']['note_id']}/match", json={"pick": pick}, headers=XHR).json()
    assert r["note"]["status"] == "matched" and r["note"]["claims"] == ["CB0000999C017"] and r["note"]["matched_by"] == "person"


def _twin(db: str, last: str = "SAMPLE", first: str = "PAT") -> None:
    """Another patient seen on 11/01 with a denial of her own (claim CB0000999C017, EMA patient EMA90); the store's denied
    visit (CB0000300C017) is EMA patient EMA77's."""
    conn = sqlite3.connect(db)
    conn.execute("""INSERT INTO claims (claim_key, transaction_key, patient_control_number, patient_last, patient_first, statement_from)
                    VALUES ('k-twin', 't-twin', 'CB0000999C017', ?, ?, '2026-11-01')""", (last, first))
    conn.execute("""INSERT INTO findings (finding_id, signal, category, claim_key, patient_control_number, service_date, payer_group, status)
                    VALUES ('f-twin', 'DENIED_MEDICAL_NECESSITY', 'denial', 'k-twin', 'CB0000999C017', '2026-11-01', 'Humana', 'open')""")
    conn.commit()
    conn.close()
    ema_row(db, DENIED, "EMA77")
    ema_row(db, "CB0000999C017", "EMA90")


@pytest.mark.parametrize("last, first", [("SAMPLE", "PAT"), ("SAMPLE", "PATRICIA"), ("SAMPLESON", "PAT")])
def test_the_other_patients_uploaded_note_is_never_used(site, last, first):
    """Only EMA77's note is uploaded, and it states her patient ID. The other patient seen that day (same name, a first
    name that starts like hers, or a family name that holds hers) gets no note from it: not by name, not in the list of
    notes waiting, not in the batch, not in an appeal. Before, her appeal was built (and faxed) from EMA77's note."""
    from remitwatch import modmed
    _twin(site.db, last, first)
    one = site.up("note.pdf", note_pdf(NOTE))
    assert one["status"] == "matched" and one["note"]["claims"] == [DENIED] and one["note"]["confirm"] is False
    conn = sqlite3.connect(site.db)
    conn.row_factory = sqlite3.Row
    try:
        twin = modmed.ClaimKey(last, first, "2026-11-01", ema_patient_ids=["EMA90"], claim_id="CB0000999C017")
        assert uploads.find_note(conn, twin)["status"] == "not_found"
        mine = uploads.find_note(conn, modmed.ClaimKey("SAMPLE", "PAT", "2026-11-01", ema_patient_ids=["EMA77"], claim_id=DENIED))
        assert mine["status"] == "found" and mine["identified_by"] == ["EMA patient ID", "name"]
    finally:
        conn.close()
    need = {i["finding_id"]: i["note"] for i in site.c.get("/api/uploads/needed").json()["items"]}
    assert need["f-twin"] == "not_found" and list(need.values()).count("found") == 1
    assert "f-twin" not in [i["finding_id"] for i in site.c.get("/api/appeals/ema_queue?ready=1").json()["items"]]
    r = site.c.post("/api/appeals/from_ema", json={"finding_id": "f-twin", "use_ai": False}, headers=XHR).json()
    assert r["status"] == "not_found" and "packet" not in r


def test_two_patients_notes_of_one_name_each_go_to_their_own_claim(site):
    """Both patients' notes uploaded, each stating its patient ID: each claim finds its own, never the other's (they no
    longer merge into one patient)."""
    _twin(site.db)
    site.up("note-77.pdf", note_pdf(NOTE))
    other = [ln.replace("EMA77", "EMA90").replace("changing mole on the back", "rash on the arm") for ln in NOTE]
    two = site.up("note-90.pdf", note_pdf(other))
    assert two["status"] == "matched" and two["note"]["claims"] == ["CB0000999C017"]
    a = site.c.post("/api/appeals/from_ema", json={"finding_id": "f-twin", "use_ai": False}, headers=XHR).json()
    assert a["status"] == "built" and a["note"]["id"] == "upload-" + two["note"]["note_id"] and a["others"] == []
    fid = next(i["finding_id"] for i in site.c.get("/api/uploads/needed").json()["items"] if i["finding_id"] != "f-twin")
    b = site.c.post("/api/appeals/from_ema", json={"finding_id": fid, "use_ai": False}, headers=XHR).json()
    assert b["status"] == "built" and b["note"]["id"] != a["note"]["id"] and b["others"] == []


def test_notes_of_two_patients_of_one_name_with_no_patient_id_are_never_merged(site, monkeypatch):
    """No EMA export yet, so no claim carries EMA's patient ID, and two Pat Samples were seen on 11/01. Their notes (with
    different birth dates, no patient ID) are two patients' notes, not one patient's two: with names off neither is
    offered, since the rows could not be told apart; with names on, each is shown with its birth date."""
    conn = sqlite3.connect(site.db)
    conn.execute("""INSERT INTO claims (claim_key, transaction_key, patient_control_number, patient_last, patient_first, statement_from)
                    VALUES ('k-twin', 't-twin', 'CB0000999C017', 'SAMPLE', 'PAT', '2026-11-01')""")
    conn.execute("""INSERT INTO findings (finding_id, signal, category, claim_key, patient_control_number, service_date, payer_group, status)
                    VALUES ('f-twin', 'DENIED_MEDICAL_NECESSITY', 'denial', 'k-twin', 'CB0000999C017', '2026-11-01', 'Humana', 'open')""")
    conn.commit()
    conn.close()
    a = [ln.replace("    Patient ID: EMA77", "") for ln in NOTE]
    b = [ln.replace("    Patient ID: EMA77", "").replace("01/02/1960", "05/06/1990").replace("changing mole", "itchy rash") for ln in NOTE]
    assert site.up("a.pdf", note_pdf(a))["status"] == "matched" and site.up("b.pdf", note_pdf(b))["status"] == "matched"
    r = site.c.post("/api/appeals/from_ema", json={"finding_id": "f-twin", "use_ai": False}, headers=XHR).json()
    assert r["status"] == "ambiguous" and r["candidates"] == [] and "cannot be told apart" in r["reason"]
    from remitwatch import modmed
    conn = sqlite3.connect(site.db)
    conn.row_factory = sqlite3.Row
    try:
        on = uploads.find_note(conn, modmed.ClaimKey("SAMPLE", "PAT", "2026-11-01", claim_id="CB0000999C017"), reveal=True)
    finally:
        conn.close()
    assert sorted(c["patient"]["birth_date"] for c in on["candidates"]) == ["1960-01-02", "1990-05-06"]


def test_a_note_matched_by_name_and_date_alone_waits_for_a_person(site):
    """The claim has EMA's patient ID, the note states none: name and date are all that agree, so nothing goes out until
    a person confirms it in the item (a pick) or attaches it there."""
    ema_row(site.db, DENIED, "EMA77")
    plain = [ln.replace("    Patient ID: EMA77", "") for ln in NOTE]
    up = site.up("plain.pdf", note_pdf(plain))
    assert up["status"] == "matched" and up["note"]["confirm"] is True
    fid = site.c.get("/api/uploads/needed").json()["items"][0]["finding_id"]
    assert site.c.get("/api/uploads/needed").json()["items"][0]["note"] == "ambiguous"
    r = site.c.post("/api/appeals/from_ema", json={"finding_id": fid, "use_ai": False}, headers=XHR).json()
    assert r["status"] == "ambiguous" and [c["id"] for c in r["candidates"]] == ["upload-" + up["note"]["note_id"]]
    assert "Only the name agrees" in r["reason"]
    picked = site.c.post("/api/appeals/from_ema", json={"finding_id": fid, "use_ai": False, "document_id": r["candidates"][0]["id"]},
                         headers=XHR).json()
    assert picked["status"] == "built" and "a person" in picked["identified_by"]
    # a number on the note that is not after a label (the practice's ZIP code, a dose) is never taken for the patient's ID
    assert uploads.note_ids("Test Dermatology, 1 Main St, Anytown KY 42301\nPatient ID: EMA77 DOB: 01/02/1960") == ["EMA77"]
    assert uploads.note_ids("Anytown KY 42301. Ibuprofen 400 mg.") == []
    assert uploads.birth_date("Patient: Pat Sample    DOB: 01/02/1960") == "1960-01-02"


def test_a_note_for_a_visit_with_no_item_is_not_tied_to_one_it_mentions(site):
    """A follow-up note (its date of service 11/15) mentions the biopsy of 11/01, which has a denial. It is the 11/15
    visit's note, so it is never tied to the 11/01 claim, and that appeal is not built from it."""
    follow = ["Test Dermatology  -  Progress Note", "Patient: Pat Sample    Patient ID: EMA77", "Date of Service: 11/15/2026",
              "Follow-up of the shave biopsy done 11/01/2026: healing well, pathology benign."]
    r = site.up("follow-up.pdf", note_pdf(follow))
    assert r["status"] == "unmatched" and "Nov 15, 2026" in r["lines"][0]
    fid = site.c.get("/api/uploads/needed").json()["items"][0]["finding_id"]
    built = site.c.post("/api/appeals/from_ema", json={"finding_id": fid, "use_ai": False}, headers=XHR).json()
    assert built["status"] == "not_found"
    conn = sqlite3.connect(site.db)
    try:
        assert uploads.rematch(conn) == {} and table(site.db, "SELECT status FROM uploaded_notes") == [("unmatched",)]
    finally:
        conn.close()


def test_names_off_hide_the_note_files_own_name(site, monkeypatch):
    """A note saved under the patient's name: with names off, the answer, the history and the appeal show it by kind and
    a short id. With names on, its own name."""
    import remitwatch.api as api
    ema_row(site.db, DENIED, "EMA77")
    r = site.up("Sample Pat 11-01.pdf", note_pdf(NOTE))
    assert r["name"].startswith("Visit note ") and "Sample" not in json.dumps(r)
    hist = site.c.get("/api/uploads").json()["items"]
    assert [i["name"] for i in hist] == [r["name"]]
    fid = site.c.get("/api/uploads/needed").json()["items"][0]["finding_id"]
    built = site.c.post("/api/appeals/from_ema", json={"finding_id": fid, "use_ai": False}, headers=XHR).json()
    assert built["status"] == "built" and built["note"]["uploaded_name"].startswith("Visit note ")
    monkeypatch.setattr(api, "SHOW_PHI", True)
    assert [i["name"] for i in site.c.get("/api/uploads").json()["items"]] == ["Sample Pat 11-01.pdf"]


def test_a_remittance_saved_under_a_spreadsheet_ending_is_read_as_one(site):
    r = site.up("remit-777.csv", new_remittance())
    assert r["status"] == "queued" and r["kind"] == "x12"
    kept = table(site.db, "SELECT path FROM uploads WHERE kind='x12'")[0][0]
    assert kept.endswith("remit-777.835")
    out = cycle(site.db, sync=False)
    assert out["ingest"]["remits"] == 1
    done = next(i for i in site.c.get("/api/uploads").json()["items"] if i["name"] == "remit-777.csv")
    assert done["status"] == "done" and any(ln.startswith("Added 1 payment on") for ln in done["lines"])


def test_a_report_whose_rows_name_no_claim_is_turned_away(site):
    """An AR aging report (and a production report) carries a Claim Status column but no Claim ID, Bill ID or Bill Item
    Identifier: it is none of the three reports, so it is refused in plain words and not kept."""
    cols = ["Patient", "Payer", "Claim Status", "Charges", "Balance", "0-30", "31-60", "61-90"]
    rows = [["Sample, Pat", "Anthem Blue Cross", "Denied", 223.65, 223.65, 0, 223.65, 0],
            ["Example, Kim", "Humana", "Submitted", 150.00, 150.00, 150.00, 0, 0]]
    for name, data in (("AR 2026.xlsx", xlsx("AR and Collections", cols, rows, total=4)), ("AR 2026.csv", as_csv(cols, rows))):
        r = site.up(name, data)
        assert r["status"] == "rejected" and "no Claim ID, Bill ID or Bill Item Identifier" in r["reason"], r
    assert ema_exports.detect_family(cols) == "unknown" and ema_exports.detect_family(STATUS_COLS) == "claim_status"
    root = os.path.join(os.path.dirname(site.db), "uploads")
    assert [f for _d, _s, fs in os.walk(root) for f in fs] == [] and site.woken == []


def test_an_export_too_large_or_shaped_to_look_it_is_refused_before_it_fills_memory(site, monkeypatch):
    import time

    import openpyxl
    # a workbook with three rows and one cell a million rows down (5 KB on disk): refused quickly, not read row by row
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Encounter & Charge Lines"])
    ws.append(CHARGE_COLS)
    for r in charge_rows():
        ws.append(r)
    ws.cell(row=1_048_576, column=21, value="x")
    b = io.BytesIO()
    wb.save(b)
    t0 = time.time()
    r = site.up("Encounter & Charge Lines.xlsx", b.getvalue())
    assert r["status"] == "rejected" and "blank lines in a row" in r["reason"] and time.time() - t0 < 30
    # blank formatted rows below a report are no reason to refuse it
    wb2 = openpyxl.Workbook()
    ws2 = wb2.active
    ws2.append(CHARGE_COLS)
    for r2 in charge_rows():
        ws2.append(r2)
    for i in range(5, 3005):
        ws2.cell(row=i, column=1).number_format = "0.00"
    p = site.tmp / "formatted.xlsx"
    wb2.save(p)
    assert len(list(ema_exports.iter_rows(str(p)))) == 3
    # over the size an export may be: refused before it is read
    monkeypatch.setattr(uploads, "EXPORT_MAX_BYTES", 1000)
    big = site.up("Encounter & Charge Lines (2).xlsx", xlsx("Encounter & Charge Lines", CHARGE_COLS, charge_rows()))
    assert big["status"] == "rejected" and "at most" in big["reason"]
    root = os.path.join(os.path.dirname(site.db), "uploads")
    assert [f for _d, _s, fs in os.walk(root) for f in fs] == []


def test_a_zip_with_a_damaged_file_says_so_and_leaves_nothing_behind(site):
    """One file in the zip fails its check (the data was damaged): it is listed as not read, the rest is taken, and no
    part of it is left in the upload folder. Before, the whole upload answered 500 and left 2 MB of it behind."""
    payload = b"%PDF-1.4 " + b"x" * (3 * 1024 * 1024)
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("week/remit-777.835", new_remittance())
        zf.writestr("week/big note.pdf", payload)
    raw = bytearray(z.getvalue())
    at = raw.index(b"x" * 64) + 2 * 1024 * 1024
    raw[at] = ord("y")                                  # one byte changed far into the file: its CRC no longer holds
    r = site.up("week.zip", bytes(raw))
    st = sorted(f["status"] for f in r["files"])
    assert r["status"] == "zip" and st == ["queued", "rejected"]
    bad = next(f for f in r["files"] if f["status"] == "rejected")
    assert "could not be read" in bad["reason"] and not bad["name"].startswith("big note")
    incoming = os.path.join(os.path.dirname(site.db), "uploads", ".incoming")
    assert os.listdir(incoming) == []
    # a zip that cannot be opened at all: refused in plain words
    broken = site.up("broken.zip", b"PK\x03\x04" + b"\x00" * 64)
    assert broken["status"] == "rejected"
    # a zip that lists far more entries than anyone uploads (hidden ones too) is refused before its list is read
    many = io.BytesIO()
    with zipfile.ZipFile(many, "w") as zf:
        for i in range(uploads.ZIP_MAX_ENTRIES + 1):
            zf.writestr(f".hidden/{i}", b"")
    assert uploads.zip_entries(str(site.tmp / "none")) == 0
    lots = site.up("lots.zip", many.getvalue())
    assert lots["status"] == "rejected" and "lists 5,001 files" in lots["reason"]


def test_files_a_stopped_upload_left_behind_are_removed(site):
    import time
    folder = os.path.join(os.path.dirname(site.db), "uploads", ".incoming")
    os.makedirs(folder, exist_ok=True)
    old, fresh = os.path.join(folder, "old"), os.path.join(folder, "fresh")
    for f in (old, fresh):
        with open(f, "wb") as fh:
            fh.write(b"part of a file")
    os.utime(old, (time.time() - 2 * 86400, time.time() - 2 * 86400))
    new = uploads.incoming(site.db)
    assert not os.path.exists(old) and os.path.exists(fresh) and os.path.exists(new)


# ----------------------------------------------------------------------------------------------- the cycle when a feed fails
class _BrokenDropbox:
    def list_folder(self, path, recursive=True):
        from remitwatch.dropbox_sync import DropboxAuthError
        raise DropboxAuthError("Dropbox token refresh failed 400: invalid_grant")
        yield  # pragma: no cover

    list_continue = list_folder


def _dropbox_env(monkeypatch, tmp_path):
    for k, v in {"DROPBOX_APP_KEY": "k", "DROPBOX_APP_SECRET": "s", "DROPBOX_REFRESH_TOKEN": "r", "DROPBOX_ROOT_PATH": "/Trizetto Data",
                 "REMITWATCH_DATA_DIR": str(tmp_path / "mirror")}.items():
        monkeypatch.setenv(k, v)


def test_files_uploaded_by_hand_are_read_while_the_dropbox_feed_fails(site, monkeypatch):
    """Dropbox refuses the sign-in (a revoked token): the export and the remittance uploaded by hand are still read and
    analysed in the same cycle, and the cycle still says Dropbox failed."""
    from remitwatch import dropbox_sync
    _dropbox_env(monkeypatch, site.tmp)
    monkeypatch.setattr(dropbox_sync, "DropboxClient", lambda *a, **kw: _BrokenDropbox())
    assert site.up("Encounter & Charge Lines.xlsx", xlsx("Encounter & Charge Lines", CHARGE_COLS, charge_rows()))["status"] == "queued"
    assert site.up("remit-777.RMT", new_remittance())["status"] == "queued"
    out = cycle(site.db)
    assert out["uploads"]["uploads"] == 2 and out["ingest"]["remits"] == 1 and out["ingest"]["ema"] == 1 and "detect" in out
    assert out["error"].startswith("Dropbox: DropboxAuthError") and "invalid_grant" in out["sync"]["error"]
    assert table(site.db, "SELECT status FROM uploads") == [("done",), ("done",)]
    last = site.c.get("/api/uploads").json()["cycle"]
    assert "invalid_grant" in (last["last_error"] or "")


def test_what_dropbox_fetched_before_it_failed_is_read_in_the_same_cycle(site, monkeypatch):
    """A sync that stops part-way hands back the files it downloaded (on the exception): they are read, not left on disk
    for the next sync to call unchanged."""
    from remitwatch import dropbox_sync
    _dropbox_env(monkeypatch, site.tmp)
    got = site.tmp / "mirror" / "remit-777.RMT"
    got.parent.mkdir(parents=True, exist_ok=True)
    got.write_bytes(new_remittance())

    def part_way(store, **kw):
        exc = dropbox_sync.DropboxAuthError("the listing stopped: 503")
        exc.new_files = [str(got)]
        raise exc
    monkeypatch.setattr(dropbox_sync, "sync", part_way)
    out = cycle(site.db)
    assert out["ingest"]["remits"] == 1 and out["error"].startswith("Dropbox:")


def test_the_servers_cycle_reads_trizetto_and_the_uploads_while_dropbox_fails(site, monkeypatch, tmp_path):
    """The hourly cycle on the server (pipeline.run_cycle, as AWS runs it): Dropbox is down, TriZetto's SFTP holds a new
    remittance, and a file waits on the Upload page. Both are read, once; the next cycle signs in to TriZetto and reads
    nothing again."""
    pytest.importorskip("paramiko")
    from remitwatch import dropbox_sync
    from tests import fake_sftp
    _dropbox_env(monkeypatch, site.tmp)
    monkeypatch.setattr(dropbox_sync, "DropboxClient", lambda *a, **kw: _BrokenDropbox())
    from tests.test_features import clean_visits
    from tests.test_remitwatch import remit
    root = tmp_path / "trizetto"
    (root / "remits").mkdir(parents=True)
    (root / "remits" / "r-900.RMT").write_bytes(remit("".join(clean_visits(900, 2, "99213", "150.00", "80.00", dos="20261104")), "160.00",
                                                      seg_count=99, isa_ctrl="000000900", pay_date="20261113"))
    f = fake_sftp.FakeSFTP(str(root))
    try:
        for k, v in f.env().items():
            monkeypatch.setenv(k, v)
        assert site.up("remit-777.RMT", new_remittance())["status"] == "queued"
        first = cycle(site.db)
        assert first["trizetto"].get("remits") == 1 and first["uploads"]["uploads"] == 1 and first["ingest"]["remits"] == 2, first
        assert "detect" in first and first["error"].startswith("Dropbox:")
        again = cycle(site.db)
        assert again["ingest"]["remits"] == 0 and not again["trizetto"].get("error"), again
        assert len(table(site.db, "SELECT payment_date FROM transactions WHERE payment_date IN ('2026-11-12','2026-11-13')")) == 2
    finally:
        f.stop()


def test_a_server_with_only_the_fax_line_still_runs_its_daily_cycle(monkeypatch, tmp_path):
    """No Dropbox, no TriZetto, no interval: with the fax line set up, the follow-ups, retries and the payers' faxes still
    need the daily run, as Vercel's daily call gives them."""
    from remitwatch import fax, pipeline
    for k in ("DROPBOX_APP_KEY", "DROPBOX_APP_SECRET", "DROPBOX_REFRESH_TOKEN", "TRIZETTO_SFTP_USER", "TRIZETTO_SFTP_PASSWORD",
              "REMITWATCH_SYNC_INTERVAL_MINUTES"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(fax, "configured", lambda env=None: False)
    assert pipeline.start_scheduler_from_env(str(tmp_path / "s.sqlite")) is False
    monkeypatch.setattr(fax, "configured", lambda env=None: True)
    try:
        assert pipeline.start_scheduler_from_env(str(tmp_path / "s.sqlite")) is True and pipeline.SCHEDULER.is_alive()
        assert pipeline.SCHEDULER.interval == pipeline.DAILY_MINUTES
    finally:
        pipeline.SCHEDULER.stop()


# ----------------------------------------------------------------------------------------------- what is not read, and why
def test_files_that_are_not_read_say_why_and_are_not_kept(site, monkeypatch):
    import openpyxl
    wb = openpyxl.Workbook()
    wb.active.append(["Foo", "Bar", "Baz", "Qux", "Quux"])
    wb.active.append([1, 2, 3, 4, 5])
    b = io.BytesIO()
    wb.save(b)
    r = site.up("Some other report.xlsx", b.getvalue())
    assert r["status"] == "rejected" and "cannot tell which EMA report" in r["reason"] and "Foo, Bar" in r["reason"]
    assert "old Excel format" in site.up("old.xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 600)["reason"]
    assert "not a file RemitWatch reads" in site.up("photo.jpg", b"\xff\xd8\xff\xe0" + b"\x00\x10JFIF" + bytes(range(256)) * 4)["reason"]
    assert "holds no payment" in site.up("bad.835", b"ISA*00*garbage~")["reason"]
    assert site.c.post("/api/uploads", params={"name": "empty.csv"}, content=b"", headers=XHR).status_code == 400
    assert site.c.post("/api/uploads", params={"name": "x.csv"}, content=b"a,b").status_code == 400          # a cross-site form cannot
    monkeypatch.setattr(uploads, "MAX_BYTES", 1000)
    big = site.c.post("/api/uploads", params={"name": "big.csv"}, content=b"x" * 5000, headers=XHR)
    assert big.status_code == 413 and "at most" in big.json()["detail"]
    # nothing refused is kept: only the empty folders are left
    root = os.path.join(os.path.dirname(site.db), "uploads")
    assert [f for _d, _s, fs in os.walk(root) for f in fs] == []
    hist = site.c.get("/api/uploads").json()["items"]
    assert {i["status"] for i in hist} == {"rejected"} and all(i["lines"] for i in hist)
    assert site.woken == []


def test_a_zip_of_exports_remittances_and_notes(site):
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("week/Encounter & Charge Lines.xlsx", xlsx("Encounter & Charge Lines", CHARGE_COLS, charge_rows()))
        zf.writestr("week/remit-777.835", new_remittance())
        zf.writestr("week/Sample visit.pdf", note_pdf(NOTE))
        zf.writestr("week/inner.zip", b"PK\x05\x06" + b"\x00" * 18)
        zf.writestr("__MACOSX/._junk", b"x")
    r = site.up("week.zip", z.getvalue())
    st = {f["name"]: f["status"] for f in r["files"]}
    # names are switched off on this site: a note and a zip are shown by kind and a short id, never under their own names
    note_name = next(n for n in st if n.startswith("Visit note "))
    zip_name = next(n for n in st if n.startswith("Zip "))
    assert st == {"Encounter & Charge Lines.xlsx": "queued", "remit-777.835": "queued", note_name: "matched", zip_name: "rejected"}
    assert note_name.endswith(".pdf") and zip_name.endswith(".zip") and r["name"].startswith("Zip ")
    assert r["status"] == "zip" and r["queued"] is True and site.woken == ["upload"]
    out = cycle(site.db, sync=False)
    assert out["uploads"]["uploads"] == 2 and out["ingest"]["remits"] == 1 and out["ingest"]["ema"] == 1


# ----------------------------------------------------------------------------------------------- the page, and Vercel
def test_the_page_and_its_guide_need_the_login(site, monkeypatch):
    import remitwatch.api as api
    page = site.c.get("/upload")
    assert page.status_code == 200
    for words in ("Encounter &amp; Charge Lines", "ERA &amp; Payment Line Adjustments", "Claim &amp; Claim Status", "the last six weeks by date of service",
                  "Patient ID", "Insurance Payments", "Bill Creation Date", "TriZetto", "never emailed", "Uploading the same file again changes nothing"):
        assert words in page.text, words
    monkeypatch.setattr(api, "PASSWORD", "correct-horse-battery-7")
    assert site.c.get("/upload").status_code == 401
    assert site.c.get("/api/uploads").status_code == 401
    assert site.c.post("/api/uploads", params={"name": "x.csv"}, content=b"a", headers=XHR).status_code == 401
    # where files go, said as it is: a visit note leaves the server inside its appeal packet (and for the review)
    assert "A visit note leaves it only inside the appeal packet built from it" in page.text and "never emailed or sent elsewhere" not in page.text
    # one wait at a time while files are queued, however often the page refreshes
    assert "clearTimeout(refresh.t);" in page.text and "setTimeout(refresh, 15000);" not in page.text.replace("refresh.t = h.queued ? setTimeout(refresh, 15000)", "")
    # the file's name goes in a header once the server says it reads one, never in the address a log keeps
    assert "info.name_header ? {} : {name: file.name}" in page.text and "X-File-Name" in page.text
    # the dashboard links the page only where uploads are taken
    index = open(os.path.join(os.path.dirname(api.__file__), "static", "index.html")).read()
    assert '<a id="uploadLink" href="/upload" hidden' in index and "$('#uploadLink').hidden = !m.uploads;" in index


def test_vercel_keeps_working_and_says_uploads_belong_to_the_server(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import remitwatch.api as api
    monkeypatch.setenv("REMITWATCH_MODE", "serverless")
    monkeypatch.setattr(api, "PASSWORD", "")
    monkeypatch.setenv("REMITWATCH_ALLOW_OPEN", "1")
    monkeypatch.setattr(api, "_wake_cycle", lambda trigger="upload": pytest.fail("no cycle on Vercel"))
    monkeypatch.setattr(api, "has_table", lambda name: False)
    monkeypatch.setattr(api, "one", lambda sql, params=(): None)
    monkeypatch.setattr(api, "rows", lambda sql, params=(): [])
    c = TestClient(api.app)
    assert c.get("/upload").status_code == 200                         # the page loads, and says where uploads go
    assert c.get("/api/uploads").json() == {"server": False, "message": uploads.SERVER_ONLY}
    for path in ("/api/uploads/needed",):
        assert c.get(path).status_code == 409
    r = c.post("/api/uploads", params={"name": "x.xlsx"}, content=b"PK", headers=XHR)
    assert r.status_code == 409 and "Dropbox folder" in r.json()["detail"]
    assert c.post("/api/uploads/notes/" + "a" * 24 + "/remove", headers=XHR).status_code == 409
    assert c.get("/api/meta").json()["uploads"] is False


def test_caddy_takes_large_uploads_only_on_the_upload_route():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    text = open(os.path.join(root, "deploy", "Caddyfile")).read()
    assert "@uploads path /api/uploads /api/uploads/*" in text and "@other not path /api/uploads /api/uploads/*" in text
    big = text[text.index("request_body @uploads"):text.index("@other")]
    small = text[text.index("request_body @other"):]
    assert re.search(r"max_size 20Mi?B", small[:60])
    # Caddy reads 260MB as 260,000,000 bytes and 260MiB as 260 x 1,048,576: the app's own limit must be the smaller, so
    # a file too large is refused with the app's plain answer, not Caddy's bare 413
    n, unit = re.search(r"max_size (\d+)(MiB|MB)", big).groups()
    assert uploads.MAX_BYTES < int(n) * (1024 * 1024 if unit == "MiB" else 1000 * 1000)
    assert uploads.MAX_BYTES < 260 * 1000 * 1000                 # under either way of reading 260
    # uploads stay out of the repository: the folder is beside the store, and never tracked
    ignore = open(os.path.join(root, ".gitignore")).read()
    assert "uploads/" in ignore
