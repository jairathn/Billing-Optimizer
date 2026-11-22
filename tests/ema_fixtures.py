"""
Invented EMA A/R and Production reports for the tests, in the column layout of the exports EMA writes (remitwatch.ema_reports
reads them). Every patient, date of birth, record number, note, staff member, clinician, claim id, claim number, check number
and amount below is made up. The claim links use an invented host under a reserved test domain (example.ema.test); tests set
ema_reports.CLAIM_HOST_SUFFIX to "ema.test" so the prefix validator accepts it.

Row builders take short keyword names and plain values: dates as datetime.date (or "M/D/YYYY" text, written as given),
money in dollars, a claim number as an int (written as EMA's claim link URL). The writers render the same rows as EMA does:
in a CSV, dates as M/D/YYYY and money as $1,234.56 or ($12.34); in an .xlsx, dates and money as typed cells, and CPT codes
that are all digits as numbers (Excel keeps them so). So one list of rows gives the same report as CSV, xlsx or zip.

Other builders put their own helpers in their own test files; this module is shared and only grows.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import os
import zipfile
from typing import Any, Iterable, Iterator

TEST_HOST = "example.ema.test"
CLAIM_PREFIX = f"https://{TEST_HOST}/ema/ws/v9/qlikRedirect/claim/"
CLAIM_URL = CLAIM_PREFIX + "{n}"
HOST_SUFFIX = "ema.test"            # monkeypatch ema_reports.CLAIM_HOST_SUFFIX with this

AR_COLUMNS = ["Coverage Type", "Responsible Party", "A/R Claim ID", "A/R Balance Type", "Aging Date", "Current A/R ($) (Aging)",
              "Denial Amount", "Bill Follow Up Date", "Claim Action", "Claim Link", "Claim Status", "CPT/Product",
              "CPT/Product Description", "Denial Category", "Last Billing Note", "Primary Payer", "Primary Biller",
              "Primary Provider", "Secondary Payer", "Service Date", "Patient MRN", "Patient Name"]
PRODUCTION_COLUMNS = ["Posted Date", "Service Date", "CPT/Product", "CPT/Product Description (Custom)", "Procedure Count", "Units",
                      "Payments", "Primary Biller", "Primary Provider", "Total wRVU", "Adjustment Reason Code", "Attendee Name",
                      "Bill Assignee", "Check Number", "Claim Action", "Claim Assignee", "Claim Link", "Claim Status",
                      "E/M Only Claim?", "Patient Name", "Payer", "Payer Claim Control Number", "Payer Plan Name",
                      "Payment Follow-Up Date", "Primary Payer", "Secondary Payer", "Unit Allowable", "Patient DOB", "Charges",
                      "Adjustments", "Contractual Adjustments", "Insurance Adjustments", "Insurance Charges", "Insurance Payments",
                      "Local Tax", "Non- Contractual Adjustments", "Patient Adjustments", "Patient Payments", "Payment Allowed Amount"]
assert len(AR_COLUMNS) == 22 and len(PRODUCTION_COLUMNS) == 39

# The three weekly exports, in the layouts the practice's exports use (for "today's exports still read as before").
WEEKLY_COLUMNS = {
    "charge_lines": ["Service Date", "Transaction Date", "Primary Provider", "Primary Provider Role", "Patient ID", "Bill ID",
                     "Bill Creation Date", "Claim ID", "Bill Item Identifier", "CPT/Product", "Modifiers", "Units", "ICD-10",
                     "Charges", "Payments", "Adjustments", "Insurance Payments", "Activity Type", "Payer", "Payer ID", "Location"],
    "era_lines": ["Transaction Date", "Service Date", "Claim ID", "CPT", "Modifiers", "Payer", "Reason Code", "Reason Code Description",
                  "RARC Code", "RARC Code Description", "Adjustment Status", "Adjustment Subtype", "Adjustments", "Payments",
                  "Patient ID"],
    "claim_status": ["Claim ID", "Patient ID", "Service Date", "CPT", "Payer", "Claim Status", "Claim Submission Date", "Denial Date",
                     "Denial Amount", "Denial Category", "Reason Code", "Previously Denied?"],
}
# A real kind of Claim & Claim Status export that names no claim id (bill ids on some rows only): it must never be purged.
GENUINE_STATUS_COLUMNS = ["Service Date", "Primary Provider", "Primary Provider Role", "Referring Provider Name", "Bill ID",
                          "CPT/Product", "Coverage Type", "Location", "Business Unit", "Charges", "Denial Amount", "Rejected Amount"]

# Invented people. The privacy tests look for every one of these strings in the store, the dashboard copy and the logs.
PATIENTS = [("Testpatient, Alpha", dt.date(1950, 1, 2), "TSTMRN0001"),
            ("Sample, Bravo Q", dt.date(1961, 7, 14), "TSTMRN0002"),
            ("Example, Charlie Jr", dt.date(1975, 11, 30), "TSTMRN0003"),
            ("Fixture, Delta", dt.date(1988, 3, 21), "TSTMRN0004")]
STAFF = ["Staff One", "Staff Two"]
CLINICIANS = ["Clinician One", "Clinician Two"]
NOTES = ["Test note: called the payer about this fixture claim", "Test note: resubmitted fixture claim"]
PAYERS = {"bcbs": "Blue Cross Test Plan", "medicare": "Medicare Test Region", "optum": "Optum Test Network",
          "va": "VA CCN Test", "commercial": "Testcare Health"}
SECRET_STRINGS = ([p[0] for p in PATIENTS] + [p[2] for p in PATIENTS] + STAFF + CLINICIANS + NOTES
                  + [p[0].split(",")[0] for p in PATIENTS])
SECRET_DATES = [f"{p[1].month}/{p[1].day}/{p[1].year}" for p in PATIENTS] + [p[1].isoformat() for p in PATIENTS]

D = dt.date


def bill_id(n: int = 1) -> str:
    """A bill id in EMA's shape: two letters, two digits, five more (9 characters)."""
    return f"ZZ00T{n:04d}"


def claim_id(n: int = 1, sub: int = 1, facility: bool = False) -> str:
    """A claim id in EMA's shape (bill + C + three digits, 13 characters); the ASC's facility claims carry an F in front
    (14 characters)."""
    return ("F" if facility else "") + f"{bill_id(n)}C{sub:03d}"


def link_no(n: int = 1) -> int:
    """An invented 8-digit EMA claim number."""
    return 90_000_000 + n


def claim_url(n: int) -> str:
    return CLAIM_URL.format(n=n)


def check_no(n: int = 1) -> str:
    """An invented 9-digit check number."""
    return f"{880_000_000 + n}"


# ------------------------------------------------------------------------------------------------------------ rows
def production_row(**kw: Any) -> dict:
    """One Production posting as {column: value}. Keywords (all optional): posted, svc, cpt, units, link (claim number,
    or None), check, reason, payer, patient (index into PATIENTS or a name), dob, status, action, plan, primary, secondary,
    pccn, charges, ins_charges, ins_paid, pat_paid, contractual, noncontractual, pat_adj, allowed, and to break the money
    identities on purpose: payments, adjustments, ins_adj. Money in dollars. Defaults: a $150 charge for 99213 posted two
    days after the visit, on the first invented patient and claim."""
    p = kw.get("patient", 0)
    name, dob, _mrn = PATIENTS[p] if isinstance(p, int) else (p, kw.get("dob", D(1970, 1, 1)), None)
    if "dob" in kw:
        dob = kw["dob"]
    payer = kw.get("payer", PAYERS["bcbs"])
    patient_side = isinstance(payer, str) and payer.lower() == "patient"
    link = kw.get("link", None if patient_side else link_no(1))
    def num(v: Any) -> float:
        return float(v) if isinstance(v, (int, float)) else 0.0
    # a money keyword given as text (say "abc") is written as given, to test what the reader does with it
    m = {k: (float(kw.get(k) or 0) if not isinstance(kw.get(k), str) else kw[k])
         for k in ("charges", "ins_paid", "pat_paid", "contractual", "noncontractual", "pat_adj")}
    if not any(k in kw for k in ("charges", "ins_paid", "pat_paid", "contractual", "noncontractual", "pat_adj")):
        m["charges"] = 150.0
    payments = kw.get("payments", num(m["ins_paid"]) + num(m["pat_paid"]))
    ins_adj = kw.get("ins_adj", num(m["contractual"]) + num(m["noncontractual"]))
    adjustments = kw.get("adjustments", num(ins_adj) + num(m["pat_adj"]))
    return {
        "Posted Date": kw.get("posted", D(2026, 3, 4)), "Service Date": kw.get("svc", D(2026, 3, 2)),
        "CPT/Product": kw.get("cpt", "99213"), "CPT/Product Description (Custom)": kw.get("desc", "Office visit, test"),
        "Procedure Count": kw.get("count", 1), "Units": kw.get("units", 1.0), "Payments": payments,
        "Primary Biller": STAFF[0], "Primary Provider": CLINICIANS[0], "Total wRVU": kw.get("wrvu", 1.3),
        "Adjustment Reason Code": kw.get("reason"), "Attendee Name": CLINICIANS[1], "Bill Assignee": STAFF[1],
        "Check Number": kw.get("check"), "Claim Action": kw.get("action"), "Claim Assignee": STAFF[1],
        "Claim Link": ("" if patient_side else None) if link is None else claim_url(link),
        "Claim Status": kw.get("status", "None" if patient_side else "Resolved/Paid"), "E/M Only Claim?": "Yes",
        "Patient Name": name, "Payer": payer, "Payer Claim Control Number": kw.get("pccn", "None"),
        "Payer Plan Name": kw.get("plan", "None"), "Payment Follow-Up Date": None,
        "Primary Payer": kw.get("primary", PAYERS["bcbs"]), "Secondary Payer": kw.get("secondary", "None"),
        "Unit Allowable": kw.get("unit_allowable", 150.0), "Patient DOB": dob, "Charges": m["charges"],
        "Adjustments": adjustments, "Contractual Adjustments": m["contractual"], "Insurance Adjustments": ins_adj,
        "Insurance Charges": kw.get("ins_charges", 0.0 if patient_side else num(m["charges"])), "Insurance Payments": m["ins_paid"],
        "Local Tax": 0.0, "Non- Contractual Adjustments": m["noncontractual"], "Patient Adjustments": m["pat_adj"],
        "Patient Payments": m["pat_paid"], "Payment Allowed Amount": kw.get("allowed", 0.0),
    }


def ar_row(**kw: Any) -> dict:
    """One A/R line as {column: value}. Keywords: coverage, party, claim (claim id, or None for a patient row), btype,
    aging, current, denial, follow, action, link (claim number or None), status, cpt, dcat, note, primary, secondary, svc,
    patient (index into PATIENTS or a name), mrn, and buckets ({column name: dollars}) for exports with more aging
    buckets. Money in dollars. Defaults: a $120 primary insurance line, Payment Pending, aged five days."""
    party = kw.get("party", "Insurance")
    insurance = party == "Insurance"
    p = kw.get("patient", 0)
    name, _dob, mrn = PATIENTS[p] if isinstance(p, int) else (p, None, kw.get("mrn"))
    if "mrn" in kw:
        mrn = kw["mrn"]
    claim = kw.get("claim", claim_id(1) if insurance else None)
    link = kw.get("link", link_no(1) if claim else None)
    row = {
        "Coverage Type": kw.get("coverage", "Primary" if insurance else "Self-Pay"), "Responsible Party": party,
        "A/R Claim ID": claim if claim else "-", "A/R Balance Type": kw.get("btype", "Debit"),
        "Aging Date": kw.get("aging", D(2026, 11, 15)), "Current A/R ($) (Aging)": kw.get("current", 120.0),
        "Denial Amount": kw.get("denial", 0.0), "Bill Follow Up Date": kw.get("follow"), "Claim Action": kw.get("action"),
        "Claim Link": claim_url(link) if link else "-", "Claim Status": kw.get("status", "Payment Pending" if claim else "-"),
        "CPT/Product": kw.get("cpt", "99213"), "CPT/Product Description": "Office visit, test",
        "Denial Category": kw.get("dcat", "None" if claim else "-"), "Last Billing Note": kw.get("note"),
        "Primary Payer": kw.get("primary", PAYERS["bcbs"]), "Primary Biller": STAFF[0], "Primary Provider": CLINICIANS[0],
        "Secondary Payer": kw.get("secondary", "None"), "Service Date": kw.get("svc", D(2026, 11, 10)),
        "Patient MRN": mrn, "Patient Name": name,
    }
    for col, dollars in (kw.get("buckets") or {}).items():
        row[col] = dollars
    return row


# --------------------------------------------------------------------------------------------------------- writers
MONEY_COLUMNS = {"Payments", "Total wRVU", "Unit Allowable", "Charges", "Adjustments", "Contractual Adjustments",
                 "Insurance Adjustments", "Insurance Charges", "Insurance Payments", "Local Tax", "Non- Contractual Adjustments",
                 "Patient Adjustments", "Patient Payments", "Payment Allowed Amount", "Current A/R ($) (Aging)", "Denial Amount"}
PLAIN_NUMBER_COLUMNS = {"Units", "Procedure Count", "Total wRVU"}


def money_text(dollars: Any) -> str:
    """Dollars as EMA writes them in a CSV: $1,234.56, and ($12.34) for a negative amount."""
    cents = int(round(float(dollars) * 100))
    s = f"${abs(cents) // 100:,}.{abs(cents) % 100:02d}"
    return f"({s})" if cents < 0 else s


def date_text(d: Any) -> str:
    return f"{d.month}/{d.day}/{d.year}" if isinstance(d, (dt.date, dt.datetime)) else str(d)


def _csv_value(col: str, v: Any) -> str:
    if v is None:
        return "-"
    if isinstance(v, (dt.date, dt.datetime)):
        return date_text(v)
    if col in PLAIN_NUMBER_COLUMNS and isinstance(v, (int, float)):
        return f"{float(v):.2f}" if col == "Units" else (f"{v}" if isinstance(v, int) else f"{v:g}")
    if (col in MONEY_COLUMNS or col.endswith("(Aging)") or col.startswith("Total A/R")) and isinstance(v, (int, float)):
        return money_text(v)
    return str(v)


def _xlsx_value(col: str, v: Any) -> Any:
    if v is None:
        return "-"
    if isinstance(v, dt.date) and not isinstance(v, dt.datetime):
        return dt.datetime(v.year, v.month, v.day)
    if col == "CPT/Product" and isinstance(v, str) and v.isdigit():
        return int(v)
    if isinstance(v, str) and col in MONEY_COLUMNS and v.startswith("$"):
        return v
    return v


def _columns_of(rows: list[dict], columns: list[str] | None, base: list[str]) -> list[str]:
    """The columns written: exactly `columns` when given (to leave one out, or name one twice), else the report's own
    columns plus any extra a row carries (aging buckets)."""
    if columns is not None:
        return list(columns)
    cols = list(base)
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    return cols


def _csv_bytes(rows: list[dict], cols: list[str], *, crlf: bool, bom: bool, title: str | None, delimiter: str) -> bytes:
    buf = io.StringIO(newline="")
    w = csv.writer(buf, delimiter=delimiter, lineterminator="\r\n" if crlf else "\n")
    if title:
        w.writerow([title])
        w.writerow([])
    w.writerow(cols)
    for r in rows:
        w.writerow([_csv_value(c, r.get(c)) for c in cols])
    data = buf.getvalue().encode("utf-8")
    return (b"\xef\xbb\xbf" + data) if bom else data


# openpyxl stamps every save with the clock (docProps/core.xml "modified" and each zip entry's time), so two workbooks
# built from the same rows a second apart had different bytes and a test that saves the same rows twice failed now and then.
# Fixture workbooks get one fixed time instead: the same rows always give the same file.
FIXED_SAVE_TIME = dt.datetime(2026, 1, 1, 0, 0, 0)


def stable_xlsx(data: bytes) -> bytes:
    """The workbook with its save time and zip entry times set to FIXED_SAVE_TIME."""
    import re

    stamp = FIXED_SAVE_TIME.strftime("%Y-%m-%dT%H:%M:%SZ").encode()
    src = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            body = src.read(info.filename)
            if info.filename == "docProps/core.xml":
                body = re.sub(rb"(<dcterms:modified[^>]*>)[^<]*(</dcterms:modified>)", rb"\g<1>" + stamp + rb"\g<2>", body)
            entry = zipfile.ZipInfo(info.filename, date_time=FIXED_SAVE_TIME.timetuple()[:6])
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = info.external_attr
            dst.writestr(entry, body)
    return out.getvalue()


def _xlsx_bytes(rows: list[dict], cols: list[str], *, title: str | None, created: dt.datetime | None, sheet: str) -> bytes:
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    if title:
        ws.append([title])
        ws.append([])
    ws.append(cols)
    for r in rows:
        ws.append([_xlsx_value(c, r.get(c)) for c in cols])
    wb.properties.created = created or FIXED_SAVE_TIME
    out = io.BytesIO()
    wb.save(out)
    return stable_xlsx(out.getvalue())


def production_csv(rows: Iterable[dict], *, crlf: bool = True, bom: bool = False, title: str | None = None, delimiter: str = ",",
                   columns: list[str] | None = None) -> bytes:
    """A Production report as CSV bytes (EMA writes CRLF lines; the practice's files have no byte-order mark)."""
    rows = list(rows)
    return _csv_bytes(rows, _columns_of(rows, columns, PRODUCTION_COLUMNS), crlf=crlf, bom=bom, title=title, delimiter=delimiter)


def production_xlsx(rows: Iterable[dict], *, title: str | None = None, created: dt.datetime | None = None,
                    columns: list[str] | None = None, sheet: str = "Sheet1") -> bytes:
    rows = list(rows)
    return _xlsx_bytes(rows, _columns_of(rows, columns, PRODUCTION_COLUMNS), title=title, created=created, sheet=sheet)


def ar_xlsx(rows: Iterable[dict], *, created: dt.datetime | None = None, title: str | None = None,
            columns: list[str] | None = None, sheet: str = "Sheet1") -> bytes:
    """An A/R report as an .xlsx workbook (one sheet, header in row 1, as EMA saves it). `created` is the workbook's own
    creation time, which orders two pulls with the same dates."""
    rows = list(rows)
    return _xlsx_bytes(rows, _columns_of(rows, columns, AR_COLUMNS), title=title, created=created, sheet=sheet)


def ar_csv(rows: Iterable[dict], *, crlf: bool = True, bom: bool = False, title: str | None = None, delimiter: str = ",",
           columns: list[str] | None = None) -> bytes:
    rows = list(rows)
    return _csv_bytes(rows, _columns_of(rows, columns, AR_COLUMNS), crlf=crlf, bom=bom, title=title, delimiter=delimiter)


def weekly_xlsx(family: str, rows: list[list]) -> bytes:
    """One of the three weekly exports (WEEKLY_COLUMNS[family]) with the given rows as lists."""
    import openpyxl

    wb = openpyxl.Workbook()
    wb.properties.created = FIXED_SAVE_TIME
    ws = wb.active
    ws.append(WEEKLY_COLUMNS[family])
    for r in rows:
        ws.append(r)
    out = io.BytesIO()
    wb.save(out)
    return stable_xlsx(out.getvalue())


def genuine_status_export(n: int = 23) -> bytes:
    """A real kind of Claim & Claim Status export that names no claim id: bill ids on some rows, coverage type, location,
    charges, denial and rejected amounts (the migration must leave it alone)."""
    import openpyxl

    wb = openpyxl.Workbook()
    wb.properties.created = FIXED_SAVE_TIME
    ws = wb.active
    ws.append(GENUINE_STATUS_COLUMNS)
    for i in range(n):
        ws.append([dt.datetime(2026, 9, 1 + i % 20), CLINICIANS[0], "Rendering", "-", bill_id(500 + i) if i % 2 == 0 else "-",
                   "99213", "Primary", "Test Office", "Test Unit", 150.0, 0.0, 25.0 if i % 3 == 0 else 0.0])
    out = io.BytesIO()
    wb.save(out)
    return stable_xlsx(out.getvalue())


def zipped(name: Any, data: bytes | None = None) -> bytes:
    """A zip of one member (zipped("a.csv", data)) or several (zipped({"a.csv": data, "b.xlsx": data2}))."""
    members = name if isinstance(name, dict) else {name: data}
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for n, d in members.items():
            zf.writestr(n, d)
    return out.getvalue()


def write(path: str, data: bytes) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def big_production_csv(path: str, n: int, *, start: dt.date = D(2025, 1, 2), iso: bool = False) -> dict:
    """Write a Production CSV of n rows straight to disk (never held in memory), cycling through invented patients,
    claim numbers, checks and codes. `iso` writes the dates as YYYY-MM-DD (no date order to decide). Returns the totals a
    reader must find: rows, insurance paid, charges (cents), distinct claim numbers."""
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\r\n")
        w.writerow(PRODUCTION_COLUMNS)
        tot = None
        for r, t in _big_rows(n, start, iso=iso):
            w.writerow(r)
            tot = t
    return _big_totals(tot)


def big_production_xlsx(path: str, n: int, *, start: dt.date = D(2025, 1, 2)) -> dict:
    """The same rows as big_production_csv as an .xlsx workbook with typed dates (written in openpyxl's streaming mode, so
    a large one is quick to make). Returns the same totals."""
    import openpyxl

    wb = openpyxl.Workbook(write_only=True)
    ws = wb.create_sheet("Sheet1")
    ws.append(PRODUCTION_COLUMNS)
    i_dates = [PRODUCTION_COLUMNS.index(c) for c in ("Posted Date", "Service Date", "Patient DOB")]
    tot = None
    for r, t in _big_rows(n, start, iso=True):
        for i in i_dates:
            r[i] = dt.datetime.fromisoformat(r[i])
        ws.append(r)
        tot = t
    wb.save(path)
    return _big_totals(tot)


def _big_totals(tot: dict | None) -> dict:
    tot = dict(tot or {"rows": 0, "ins_paid_cents": 0, "charges_cents": 0, "links": set()})
    tot["links"] = len(tot["links"])
    return tot


def _big_rows(n: int, start: dt.date, *, iso: bool = False) -> Iterator[tuple[list, dict]]:
    cols = PRODUCTION_COLUMNS
    idx = {c: i for i, c in enumerate(cols)}
    codes_ = ["99213", "99214", "17000", "11102", "G2211", "SERUM TEST"]
    payers = [PAYERS["bcbs"], PAYERS["medicare"], PAYERS["commercial"], "Patient"]
    tot = {"rows": 0, "ins_paid_cents": 0, "charges_cents": 0, "links": set()}
    day = (lambda d: d.isoformat()) if iso else date_text
    if True:
        for i in range(n):
            svc = start + dt.timedelta(days=(i // 400) % 330)
            posted = svc + dt.timedelta(days=i % 40)
            name, dob, _mrn = PATIENTS[i % len(PATIENTS)]
            payer = payers[i % len(payers)]
            kind = i % 3                        # 0 charge, 1 payment, 2 adjustment
            charge = 150 + (i % 7) * 10 if kind == 0 else 0
            ins_paid = (60 + i % 13) if kind == 1 and payer != "Patient" else 0
            pat_paid = 20 if kind == 1 and payer == "Patient" else 0
            contractual = (40 + i % 5) if kind == 2 and payer != "Patient" else 0
            link = None if payer == "Patient" else 90_000_000 + (i // 6)
            r = [""] * len(cols)
            r[idx["Posted Date"]] = day(posted)
            r[idx["Service Date"]] = day(svc)
            r[idx["CPT/Product"]] = codes_[i % len(codes_)]
            r[idx["CPT/Product Description (Custom)"]] = "Test description"
            r[idx["Procedure Count"]] = "1"
            r[idx["Units"]] = "1.00"
            r[idx["Payments"]] = money_text(ins_paid + pat_paid)
            r[idx["Primary Biller"]] = STAFF[0]
            r[idx["Primary Provider"]] = CLINICIANS[i % 2]
            r[idx["Total wRVU"]] = "1.3"
            r[idx["Adjustment Reason Code"]] = "CO-45" if kind == 2 else "-"
            r[idx["Attendee Name"]] = CLINICIANS[0]
            r[idx["Bill Assignee"]] = STAFF[1]
            r[idx["Check Number"]] = f"{880_000_000 + i // 50}" if kind == 1 and payer != "Patient" else "-"
            r[idx["Claim Action"]] = "-"
            r[idx["Claim Assignee"]] = STAFF[1]
            r[idx["Claim Link"]] = claim_url(link) if link else ""
            r[idx["Claim Status"]] = "Resolved/Paid" if link else "None"
            r[idx["E/M Only Claim?"]] = "No"
            r[idx["Patient Name"]] = name
            r[idx["Payer"]] = payer
            r[idx["Payer Claim Control Number"]] = "None"
            r[idx["Payer Plan Name"]] = "None"
            r[idx["Payment Follow-Up Date"]] = "-"
            r[idx["Primary Payer"]] = PAYERS["bcbs"]
            r[idx["Secondary Payer"]] = "None"
            r[idx["Unit Allowable"]] = money_text(150)
            r[idx["Patient DOB"]] = day(dob)
            r[idx["Charges"]] = money_text(charge)
            r[idx["Adjustments"]] = money_text(contractual)
            r[idx["Contractual Adjustments"]] = money_text(contractual)
            r[idx["Insurance Adjustments"]] = money_text(contractual)
            r[idx["Insurance Charges"]] = money_text(charge if payer != "Patient" else 0)
            r[idx["Insurance Payments"]] = money_text(ins_paid)
            r[idx["Local Tax"]] = money_text(0)
            r[idx["Non- Contractual Adjustments"]] = money_text(0)
            r[idx["Patient Adjustments"]] = money_text(0)
            r[idx["Patient Payments"]] = money_text(pat_paid)
            r[idx["Payment Allowed Amount"]] = money_text(ins_paid)
            tot["rows"] += 1
            tot["ins_paid_cents"] += ins_paid * 100
            tot["charges_cents"] += charge * 100
            if link:
                tot["links"].add(link)
            yield r, tot
