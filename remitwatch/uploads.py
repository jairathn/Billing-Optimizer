"""
Files a person brings in by hand on the Upload page (a server only): ModMed EMA's exports, remittance and claim files,
and visit notes. Until ModMed's API is approved this is how EMA's side reaches RemitWatch.

An export or a remittance file takes the road a file found in the Dropbox folder takes. It is kept in the upload folder
(beside the store: /data/uploads on a server) and read by the next cycle (pipeline.run_cycle) with the same loader
(dropbox_sync.ingest_new_files), under the same lock, and the same analysis follows. The rows, claims and findings it
makes are the ones the same file makes arriving any other way, and uploading the same file again changes nothing (a file
is known by its SHA-256, a row of an export by its content, a remittance by its content).

A visit note is kept the same way and becomes one more place the matching behind appeals looks (modmed.find_note, here
over UploadSource): an appeal reads an uploaded note exactly as it reads one from EMA's API. Each note is tied to its
visit by the patient's name and the date of service it states, checked against the claims RemitWatch holds, or by the
person who attaches it to an item. As with EMA's API, an appeal takes the note by itself only when a second identifier
agrees as well (EMA's patient ID or the record number, stated on the note), or a person attached it; otherwise a person
confirms it in the item first.

The files stay in the upload folder, owner-only, outside the repository, and are never emailed. An export or a
remittance file never leaves the server. A visit note leaves it only inside the appeal packet built from it (faxed to
the payer, or emailed where the practice allows that) and for the note review, when the review is switched on.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import time
import unicodedata
import zipfile
import zlib
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import date, datetime, timezone
from typing import Any, Callable, Iterable, Iterator

from . import ema_exports, modmed

MAX_BYTES = 240 * 1024 * 1024           # one upload (a zip of remittances can be large); under Caddy's 260 MiB (272,629,760 bytes)
EXPORT_MAX_BYTES = 60 * 1024 * 1024     # one EMA export, as cloud.MAX_FILE_BYTES: a year of Encounter & Charge Lines is tens of MB,
                                        # and reading one takes ten to twenty times its size in memory
INCOMING_MAX_AGE = 24 * 60 * 60         # a file left in .incoming this long (a request cut off) is removed
NOTE_MAX_BYTES = 15 * 1024 * 1024       # as a note read from EMA's API (modmed.MAX_NOTE_BYTES)
ZIP_MAX_MEMBERS = 500
ZIP_MAX_ENTRIES = 5000                  # entries a zip may list at all (hidden ones included) before it is opened
ZIP_MAX_TOTAL = 1024 * 1024 * 1024
DOC_PREFIX = "upload-"                  # the id an uploaded note goes by among EMA's notes (a person's pick)
NOTE_EXT = {".pdf": "application/pdf", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".txt": "text/plain", ".rtf": "text/rtf", ".html": "text/html", ".htm": "text/html"}
REPORT = {"charge_lines": "Encounter & Charge Lines", "era_lines": "ERA & Payment Lines", "claim_status": "Claim & Claim Status"}
# the columns each report must carry for everything RemitWatch does with it, and why (said when one is missing)
WANT = {
    "charge_lines": [("claim_id", "Claim ID", "ties each charge to its remittance"),
                     ("cpt", "CPT/Product", "the code billed"),
                     ("service_date", "Service Date", "the visit"),
                     ("bill_item_id", "Bill Item Identifier", "keeps one charge from counting twice across overlapping exports"),
                     ("patient_id", "Patient ID", "ties visits billed to two payers to one patient, and finds the visit note"),
                     ("insurance_payments", "Insurance Payments", "what EMA has posted from insurers: it closes items EMA shows as paid"),
                     ("bill_id", "Bill ID", "groups the charges of one bill"),
                     ("bill_created", "Bill Creation Date", "tells a light day from a day whose bills were not written yet")],
    "era_lines": [("claim_id", "Claim ID", "ties each line to its remittance"), ("cpt", "CPT", "the code paid"),
                  ("service_date", "Service Date", "the visit"), ("carc", "Reason Code", "why the payer adjusted it"),
                  ("adjustment_status", "Adjustment Status", "how the biller handled it (written off, moved to the patient)")],
    "claim_status": [("claim_id", "Claim ID", "ties each line to its claim"), ("claim_status", "Claim Status", "where the claim stands"),
                     ("patient_id", "Patient ID", "says whose a rejected or unpaid claim is (it has no remittance)"),
                     ("denial_amount", "Denial Amount", "how much was denied")],
}
NO_NOTE = ("No visit note for this visit has been uploaded yet: save it from EMA as a PDF and drop it on the Upload page "
           "(or attach it in the item).")
SERVER_ONLY = ("Visit notes, and files sent in one piece, are uploaded on the practice's own server, where the files are kept "
               "beside the store. On this site, upload EMA's reports and exports with the checklist above (they go to the "
               "Dropbox folder the next run reads), or put them in the Dropbox folder.")

DDL = """
CREATE TABLE IF NOT EXISTS uploads (
    upload_id TEXT PRIMARY KEY,             -- the SHA-256 of the file as it came
    stored_sha TEXT,                        -- of the file as kept (the same, but for a remittance file trimmed of leading blanks)
    kind TEXT, name TEXT, path TEXT, size INTEGER, uploaded_at TEXT, uploaded_by TEXT,
    status TEXT,                            -- queued (waits for the cycle), done, rejected, failed
    summary TEXT,                           -- what was in it, what was added, what was already there, what was left out and why
    done_at TEXT, parent TEXT               -- parent: the zip it came in
);
CREATE INDEX IF NOT EXISTS ix_uploads_status ON uploads(status);
CREATE TABLE IF NOT EXISTS uploaded_notes (
    note_id TEXT PRIMARY KEY, sha256 TEXT UNIQUE, name TEXT, path TEXT, content_type TEXT, kind TEXT, size INTEGER,
    uploaded_at TEXT, uploaded_by TEXT,
    status TEXT,                            -- matched, choose (more than one visit fits: a person picks), unmatched
    reason TEXT, patient_last TEXT, patient_first TEXT, ema_patient_id TEXT, service_date TEXT, note_type TEXT, loinc TEXT,
    matched_by TEXT, matched_at TEXT, candidates TEXT, claims TEXT,
    ids_stated TEXT,                        -- the identifiers the note itself states (the value after "Patient ID", "MRN" ...),
                                            -- or for a note a person attached, the claim's: JSON; NULL = not worked out yet
    birth_date TEXT                         -- the birth date the note states, to tell two patients of one name apart
);
CREATE INDEX IF NOT EXISTS ix_unotes_dos ON uploaded_notes(service_date);
"""


class Rejected(ValueError):
    """The file is not one RemitWatch reads, said in plain words."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)
    have = {r[1] for r in conn.execute("PRAGMA table_info(uploaded_notes)")}
    for col in ("ids_stated", "birth_date"):            # a store made before these columns
        if col not in have:
            conn.execute(f"ALTER TABLE uploaded_notes ADD COLUMN {col} TEXT")
    conn.commit()


def upload_dir(db_path: str) -> str:
    return os.environ.get("REMITWATCH_UPLOAD_DIR") or os.path.join(os.path.dirname(os.path.abspath(db_path)), "uploads")


def _private_dir(path: str) -> str:
    os.makedirs(path, mode=0o700, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def incoming(db_path: str) -> str:
    """A new, owner-only file in the upload folder for a file arriving now (the request's bytes go straight to the disk
    the store is on, never to a temporary folder elsewhere)."""
    d = _private_dir(os.path.join(upload_dir(db_path), ".incoming"))
    _sweep(d)
    name = os.path.join(d, hashlib.sha256(os.urandom(16)).hexdigest()[:20])
    os.close(os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    return name


def _sweep(folder: str) -> None:
    """Remove what a request cut off (a dropped connection, a stopped server) left in .incoming a day ago or more."""
    now = datetime.now(timezone.utc).timestamp()
    try:
        for f in os.listdir(folder):
            p = os.path.join(folder, f)
            try:
                if os.path.isfile(p) and now - os.path.getmtime(p) > INCOMING_MAX_AGE:
                    os.remove(p)
            except OSError:
                pass
    except OSError:
        pass


def safe_name(name: str) -> str:
    """The file's own name, made safe to keep: no folders, no control characters, not hidden, not too long."""
    n = os.path.basename((name or "").replace("\\", "/"))
    n = re.sub(r"[\x00-\x1f\x7f/:*?\"<>|]+", " ", n).strip().lstrip(".").strip()
    stem, ext = os.path.splitext(n or "file")
    return (stem[:120].strip() or "file") + ext[:10]


def _keep(src: str, folder: str, name: str) -> str:
    _private_dir(folder)
    dest = os.path.join(folder, name)
    shutil.move(src, dest)
    os.chmod(dest, 0o600)
    return dest


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _count_known(conn: sqlite3.Connection, table: str, col: str, values: set) -> int:
    if not values or not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
        return 0
    vals, n = list(values), 0
    for i in range(0, len(vals), 900):
        part = vals[i:i + 900]
        n += conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {col} IN ({','.join('?' * len(part))})", part).fetchone()[0]
    return n


# --------------------------------------------------------------------------- what a file is
def sniff(path: str, name: str) -> str:
    """'ema' (an EMA export: .xlsx, or CSV), 'x12' (a remittance or claim file), 'note', 'zip', 'old_office' or 'unknown'."""
    with open(path, "rb") as fh:
        head = fh.read(65536)
    low = name.lower()
    if head.startswith(b"%PDF-"):
        return "note"
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return "old_office"
    if head.startswith(b"PK"):
        if zip_entries(path) > ZIP_MAX_ENTRIES:
            return "zip"                # far too many to list: _take_zip refuses it without opening it
        try:
            with zipfile.ZipFile(path) as z:
                names = set(z.namelist())
        except (zipfile.BadZipFile, OSError, ValueError):
            return "unknown"
        if "xl/workbook.xml" in names:
            return "ema"
        if "word/document.xml" in names:
            return "note"
        return "zip"
    if head.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"ISA"):
        return "x12"
    if b"\x00" in head:
        return "unknown"
    if low.endswith(ema_exports.TEXT_EXPORTS) or _export_text(head):
        return "ema"
    if os.path.splitext(low)[1] in NOTE_EXT:
        return "note"
    return "unknown"


def zip_entries(path: str) -> int:
    """How many entries a zip lists, read from its end record without opening its directory (which for a zip of a
    million empty entries alone takes hundreds of MB). 0 when there is no end record; a ZIP64 count reads as huge."""
    import struct
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 65557))
            tail = fh.read()
    except OSError:
        return 0
    at = tail.rfind(b"PK\x05\x06")
    if at < 0 or len(tail) - at < 22:
        return 0
    total = struct.unpack("<H", tail[at + 10: at + 12])[0]
    return 1 << 30 if total == 0xFFFF else total


def _export_text(head: bytes) -> bool:
    """Whether delimited text names the columns of one of EMA's reports (a CSV saved with another ending): the A/R or
    Production report (ema_reports.recognize), or one of the three weekly exports."""
    from . import ema_reports
    try:
        if ema_reports.recognize(head, "export.csv") is not None:
            return True
    except ema_reports.ReportRefused:
        return True                     # a report with a column twice: refused in its own words when it is taken
    except Exception:
        pass
    try:
        raw = ema_exports._csv_rows(head)
    except Exception:
        return False
    if not raw:
        return False
    hdr = ema_exports.header_row(raw)
    return ema_exports.detect_family([str(c) for c in raw[hdr] if c is not None]) != "unknown"


# --------------------------------------------------------------------------- what an export holds
def preview_export(conn: sqlite3.Connection, path: str) -> dict:
    """Read an export as the loader will (ema_exports.iter_rows) and say what it holds: the report, its rows, how many
    RemitWatch has already (by their content), what is left out and why, the columns it does not keep and the ones it lacks."""
    acct: dict = {}
    fps: list[str] = []
    svc: list[str] = []
    claims: set[str] = set()
    activity: Counter = Counter()
    for r in ema_exports.iter_rows(path, acct):
        fps.append(r["row_fingerprint"])
        if r.get("service_date"):
            svc.append(r["service_date"])
        if r.get("claim_id"):
            claims.add(r["claim_id"])
        if r.get("activity_type"):
            activity[r["activity_type"]] += 1
    family = acct.get("family") or "unknown"
    if family == "unknown":
        cols = [c for c in acct.get("columns") or [] if not c.startswith("Unnamed")]
        canon = set(acct.get("canon") or [])
        if canon & {"claim_status", "denial_amount", "charges", "payments"} and not canon & set(ema_exports.KEY_COLUMNS):
            raise Rejected("This report's rows name no claim or bill: it has no Claim ID, Bill ID or Bill Item Identifier column, "
                           "and it is not EMA's A/R report or Production report in their full layouts either. RemitWatch reads "
                           "those two reports with every column, and Encounter & Charge Lines, ERA & Payment Lines and Claim & "
                           "Claim Status. The checklist on this page lists the columns each must have. The file is not kept.")
        raise Rejected("RemitWatch cannot tell which EMA report this is: it reads the A/R report, the Production report, Encounter & "
                       "Charge Lines, ERA & Payment Lines and Claim & Claim Status. "
                       + (f"The columns found: {', '.join(cols[:12])}{'…' if len(cols) > 12 else ''}."
                          if cols else "No row of column names was found."))
    distinct = set(fps)
    known = _count_known(conn, "ema_rows", "row_fingerprint", distinct)
    stored = {r[1] for r in conn.execute("PRAGMA table_info(ema_rows)")}
    canon = acct.get("canon") or []
    unused = [label for label, c in zip(acct.get("columns") or [], canon)
              if (f"{c}_cents" if c in ema_exports.MONEY else c) not in stored and not label.startswith("Unnamed")]
    have = set(canon)
    missing = [{"column": label, "why": why} for key, label, why in WANT.get(family, []) if key not in have]
    from .ema_exports import export_created
    return {"kind": "ema", "family": family, "report": REPORT.get(family, family), "format": acct.get("format"),
            "rows": acct.get("rows", 0), "distinct": len(distinct), "repeats": acct.get("rows", 0) - len(distinct),
            "new": len(distinct) - known, "already": known, "left": acct.get("left") or {}, "above": acct.get("above", 0),
            "header_line": (acct.get("header_index") or 0) + 1, "service_from": min(svc) if svc else None,
            "service_to": max(svc) if svc else None, "claims": len(claims), "activity": dict(activity.most_common(8)),
            "unused_columns": unused, "missing": missing, "created_at": export_created(path)}


def preview_x12(conn: sqlite3.Connection, path: str) -> dict:
    with open(path, "rb") as fh:
        data = fh.read()
    head = data[:600]
    if b"GS*HC*" in head or b"*837*" in head:
        from .x12_837 import parse_837_bytes
        p = parse_837_bytes(data, source=path)
        if not p["claims"]:
            raise Rejected("This claim file (837) holds no claim RemitWatch can read.")
        days = [c["service_date"] for c in p["claims"] if c.get("service_date")]
        return {"kind": "x12", "x12": "837", "claims": len(p["claims"]), "lines": len(p["lines"]),
                "service_from": min(days) if days else None, "service_to": max(days) if days else None}
    from .x12_835 import parse_835_bytes
    p = parse_835_bytes(data, source=path)
    if not p.transactions:
        raise Rejected("This file starts like a remittance (835) but holds no payment RemitWatch can read"
                       + (f": {p.problems[0].get('message')}" if p.problems else "."))
    fps = {t["content_fingerprint"] for t in p.transactions if t.get("content_fingerprint")}
    known = _count_known(conn, "transactions", "content_fingerprint", fps)
    pays = [t["payment_date"] for t in p.transactions if t.get("payment_date")]
    return {"kind": "x12", "x12": "835", "transactions": len(p.transactions), "new": len(p.transactions) - known, "already": known,
            "claims": len(p.claims), "lines": len(p.lines), "problems": len(p.problems),
            "payers": sorted({t["payer_name"] for t in p.transactions if t.get("payer_name")})[:6],
            "paid_cents": sum(t.get("payment_amount_cents") or 0 for t in p.transactions),
            "payment_from": min(pays) if pays else None, "payment_to": max(pays) if pays else None}


# --------------------------------------------------------------------------- taking a file in
def _row(conn: sqlite3.Connection, sha: str) -> dict | None:
    r = conn.execute("SELECT * FROM uploads WHERE upload_id=?", (sha,)).fetchone()
    return dict(zip([d[0] for d in conn.execute("SELECT * FROM uploads LIMIT 0").description], r)) if r else None


def _record(conn: sqlite3.Connection, sha: str, **kw) -> None:
    cols = ["upload_id", "stored_sha", "kind", "name", "path", "size", "uploaded_at", "uploaded_by", "status", "summary", "done_at", "parent"]
    old = _row(conn, sha) or {}
    vals = {**{c: old.get(c) for c in cols}, **kw, "upload_id": sha}
    if isinstance(vals.get("summary"), dict):
        vals["summary"] = json.dumps(vals["summary"], default=str)
    conn.execute(f"INSERT OR REPLACE INTO uploads ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [vals[c] for c in cols])
    conn.commit()


def _already(conn: sqlite3.Connection, sha: str) -> dict | None:
    """What RemitWatch did with this exact file before, if it has it: uploaded, or read from Dropbox or TriZetto."""
    up = _row(conn, sha)
    if up and up["status"] in ("queued", "ready", "done"):
        return {"how": "upload", "at": up["uploaded_at"], "by": up["uploaded_by"], "name": up["name"], "status": up["status"]}
    report = known_file(conn, sha) if _has(conn, "ema_report_files") and _catalog(conn, sha) else None
    if report:                          # an A/R or Production report RemitWatch read, held, refused or keeps waiting
        return dict(report, name=None)
    f = conn.execute("SELECT source_name, source_path, ingested_at FROM files WHERE sha256=?", (sha,)).fetchone() \
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='files'").fetchone() else None
    if f:
        return {"how": "upload" if "/uploads/" in (f[1] or "") else "feed", "at": f[2], "name": f[0], "status": "done"}
    n = conn.execute("SELECT note_id FROM uploaded_notes WHERE sha256=?", (sha,)).fetchone()
    if n:
        return {"how": "upload", "at": None, "name": None, "status": "done", "note_id": n[0]}
    return None


def accept(db_path: str, tmp: str, name: str, user: str, sha: str | None = None, finding_id: str | None = None,
           parent: str | None = None, conn: sqlite3.Connection | None = None, reveal: bool = False) -> dict:
    """Take one uploaded file (already on disk at `tmp`, in the upload folder): say what it is and what it holds, keep it,
    and queue it for the next cycle (an export or a remittance file) or match it to its visit (a visit note). A file
    RemitWatch has already is left as it is. `finding_id`: a visit note attached to that item. The file at `tmp` is kept
    or removed, never left behind. Returns the summary; status 'queued' tells the caller to wake the cycle."""
    own = conn is None
    conn = conn or sqlite3.connect(db_path, timeout=60)
    work = tmp
    name = safe_name(name)
    base: dict = {"name": name}
    try:
        ensure_schema(conn)
        sha = sha or _sha(tmp)
        size = os.path.getsize(tmp)
        kind = sniff(tmp, name)
        base.update(upload_id=sha, size=size, kind=kind)
        if kind == "note" or finding_id:
            if kind != "note":
                raise Rejected("This is not a visit note RemitWatch reads (PDF, Word, text, RTF or HTML).")
            out = _take_note(conn, db_path, tmp, name, sha, user, finding_id, parent, base)
            out["lines"] = out.get("lines") or _note_lines(out["note"], reveal, conn)
            out["note"] = _note_public(out["note"], reveal, conn)
            out["name"] = shown_name(name, "note", sha, reveal)          # a note is often saved under the patient's name
            return out
        seen = _already(conn, sha)
        if seen:
            return dict(base, status="already", already=seen, lines=[_already_line(seen)])
        if kind == "zip":
            return _take_zip(conn, db_path, tmp, name, sha, user, base, reveal)
        if kind == "old_office":
            raise Rejected("This is the old Excel format (.xls). Export it from EMA as Excel (.xlsx) or CSV, "
                           "or open it in Excel and save it as an Excel Workbook (.xlsx).")
        if kind == "unknown":
            raise Rejected("This is not a file RemitWatch reads. It takes EMA's exports (Excel or CSV), remittance (835) and "
                           "claim (837) files, a .zip of those, and visit notes (PDF, Word or text).")
        stem, ext = os.path.splitext(name)
        if kind == "ema":
            handed = _report_hand_off(conn, db_path, tmp, name, sha, size, user, parent, base)
            if handed is not None:          # EMA's A/R or Production report: to the intake folder, as when sent in pieces
                return handed
            if size > EXPORT_MAX_BYTES:
                raise Rejected(f"An EMA export can be at most {EXPORT_MAX_BYTES // (1024 * 1024)} MB here. Export a shorter date range "
                               "(a quarter at a time, say).")
            ext = ext if ext.lower() in (".xlsx",) + ema_exports.TEXT_EXPORTS else (".xlsx" if _is_xlsx(tmp) else ".csv")
            work = tmp + ext                        # the loader tells a CSV from a workbook by its ending
            os.rename(tmp, work)
            folder = os.path.join(upload_dir(db_path), "exports")
            try:
                summary = preview_export(conn, work)
            except Rejected:
                raise
            except ema_exports.TooLarge as exc:
                raise Rejected(str(exc)) from exc
            except Exception as exc:
                raise Rejected(f"The file could not be read as a spreadsheet ({type(exc).__name__}). Export it from EMA again.") from exc
        else:
            with open(tmp, "rb") as fh:
                data = fh.read()
            lead = len(data) - len(data.lstrip(b"\xef\xbb\xbf \t\r\n"))
            if lead:            # the loader reads a file that starts with ISA
                with open(tmp, "wb") as fh:
                    fh.write(data[lead:])
            if not ext or ext.lower() in (".xlsx", ".zip") + ema_exports.TEXT_EXPORTS:
                ext = ".835"        # the loader sends a .csv, .tsv or .xlsx to the EMA reader: kept as what it is
            folder = os.path.join(upload_dir(db_path), "remittances")
            try:
                summary = preview_x12(conn, tmp)
            except Rejected:
                raise
            except Exception as exc:
                raise Rejected(f"The file could not be read as a remittance or claim file ({type(exc).__name__}).") from exc
        stored_sha = _sha(work)
        path = _keep(work, os.path.join(folder, sha[:16]), _free_name(conn, stem, ext) if kind == "ema" else f"{stem}{ext}")
        summary = dict(base, **summary)
        _record(conn, sha, stored_sha=stored_sha, kind=kind, name=name, path=path, size=size, uploaded_at=_now(), uploaded_by=user,
                status="queued", summary=summary, done_at=None, parent=parent)
        return dict(summary, status="queued", lines=describe(summary, "queued"))
    except Rejected as exc:
        if base.get("upload_id"):
            _record(conn, base["upload_id"], kind=base.get("kind"), name=name, path=None, size=base.get("size"), uploaded_at=_now(),
                    uploaded_by=user, status="rejected", summary={"reason": str(exc)}, done_at=_now(), parent=parent)
        return dict(base, name=shown_name(name, base.get("kind"), base.get("upload_id"), reveal), status="rejected", reason=str(exc),
                    lines=[str(exc)])
    finally:
        for f in {tmp, work}:
            if os.path.exists(f):
                os.remove(f)
        if own:
            conn.close()


def missing_line(report: str, missing: Iterable[str]) -> str:
    """A report that lacks columns its reader needs, said in plain words with why each is needed."""
    from . import ema_reports
    miss = [ema_reports.WHY.get(n, (n, "")) for n in missing]
    cols = "; ".join(f"{w} ({why})" if why else w for w, why in miss)
    return (f"This is EMA's {REPORT_TITLES.get(report, 'report')}, but it lacks {_n(len(miss), 'column')} RemitWatch needs: {cols}. "
            "Run it again with every column and upload that file.")


def _report_hand_off(conn: sqlite3.Connection, db_path: str, tmp: str, name: str, sha: str, size: int, user: str,
                     parent: str | None, base: dict) -> dict | None:
    """EMA's A/R or Production report, dropped in one piece, goes where a report sent in pieces goes: into the intake
    folder with its sidecar, for the cycle (remitwatch.intake), never to the weekly exports' reader. None when the file is
    neither report. A report that lacks a column, or names one twice, is refused here and not kept."""
    from . import ema_reports
    try:
        # an open file, not the path: the file waiting here has no ending, and the workbook reader refuses a path without one
        with open(tmp, "rb") as fh:
            rec = ema_reports.recognize(fh, name)
    except ema_reports.ReportRefused as exc:
        raise Rejected(exc.message) from exc
    except Exception:
        return None
    if rec is None:
        return None
    if rec.missing:
        raise Rejected(missing_line(rec.report, rec.missing))
    store = ServerUploads(db_path)
    store._dirs()
    pair = pair_name(secrets.token_hex(16), name, False)
    dest = store.ready_path(pair)
    shutil.move(tmp, dest)
    os.chmod(dest, 0o600)
    store.write_sidecar({"pair": pair}, make_sidecar(name, user, sha, size, False, rec.report))
    summary = dict(base, kind="ema_report", report=rec.report, title=REPORT_TITLES[rec.report], format=rec.fmt, pair=pair)
    _record(conn, sha, stored_sha=sha, kind="ema_report", name=name, path=dest, size=size, uploaded_at=_now(), uploaded_by=user,
            status="ready", summary=summary, done_at=None, parent=parent)
    kick_intake(db_path)
    return dict(summary, status="ready", lines=describe(summary, "ready"))


def _free_name(conn: sqlite3.Connection, stem: str, ext: str) -> str:
    """The name an export is kept under. It is its own name, as a file from Dropbox keeps its own: the store knows an
    export by it (an item closed by what EMA posted names the export it was seen in). A name the store already has for
    another export (EMA names every download the same) gets the day it was uploaded."""
    have = {r[0] for r in conn.execute("SELECT source_name FROM ema_sources")} if \
        conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ema_sources'").fetchone() else set()
    have |= {os.path.basename(r[0] or "") for r in conn.execute("SELECT path FROM uploads WHERE kind='ema' AND path IS NOT NULL")}
    name = f"{stem}{ext}"
    day = datetime.now(timezone.utc).date().isoformat()
    n = 1
    while name in have:
        n += 1
        name = f"{stem} (uploaded {day}{', ' + str(n - 1) if n > 2 else ''}){ext}"
    return name


def _is_xlsx(path: str) -> bool:
    try:
        with zipfile.ZipFile(path) as z:
            return "xl/workbook.xml" in z.namelist()
    except (zipfile.BadZipFile, OSError):
        return False


def _already_line(seen: dict) -> str:
    when = f" on {_day(seen['at'])}" if seen.get("at") else ""
    if seen.get("line"):
        return seen["line"]
    if seen["how"] == "feed":
        return f"RemitWatch already read this exact file{when} (as {seen.get('name')}, from the Dropbox folder or TriZetto): nothing changed."
    if seen.get("status") in ("queued", "ready"):
        return f"This exact file was already uploaded{when} and waits for the next cycle: nothing changed."
    return f"This exact file was already uploaded{when}{(' by ' + seen['by']) if seen.get('by') else ''}: nothing changed."


ZIP_UNREADABLE = ("This file in the zip could not be read (the zip is damaged, or the file is locked with a password): "
                  "upload it on its own.")


def _take_zip(conn, db_path: str, tmp: str, name: str, sha: str, user: str, base: dict, reveal: bool = False) -> dict:
    """Each file in a zip is taken as if uploaded on its own (one level: a zip inside is not opened). A file in it that
    cannot be unpacked is said so on its own line, and nothing of it is left behind."""
    out = []
    listed = zip_entries(tmp)
    if listed > ZIP_MAX_ENTRIES:
        raise Rejected(f"The zip lists {listed:,} files; upload at most {ZIP_MAX_MEMBERS} at a time.")
    try:
        z = zipfile.ZipFile(tmp)
        infos = z.infolist()
    except (zipfile.BadZipFile, OSError, EOFError, ValueError) as exc:
        raise Rejected("The zip could not be read: upload its files on their own.") from exc
    with z:
        members = [i for i in infos if not i.is_dir() and not os.path.basename(i.filename).startswith((".", "~$"))
                   and "__MACOSX/" not in i.filename]
        if len(members) > ZIP_MAX_MEMBERS:
            raise Rejected(f"The zip holds {len(members)} files; upload at most {ZIP_MAX_MEMBERS} at a time.")
        if sum(i.file_size for i in members) > ZIP_MAX_TOTAL or any(i.file_size > MAX_BYTES for i in members):
            raise Rejected("The zip is too large once unpacked. Upload its files a few at a time.")
        for i in members:
            dest = incoming(db_path)
            member = safe_name(i.filename)
            try:
                try:
                    with z.open(i) as src, open(dest, "wb") as fh:
                        shutil.copyfileobj(src, fh, 1 << 20)
                except (zipfile.BadZipFile, zlib.error, RuntimeError, EOFError, OSError, NotImplementedError, ValueError):
                    r = {"name": shown_name(member, "file", None, reveal), "status": "rejected", "reason": ZIP_UNREADABLE,
                         "lines": [ZIP_UNREADABLE]}
                else:
                    if sniff(dest, i.filename) == "zip":
                        r = {"name": shown_name(member, "zip", _sha(dest), reveal), "status": "rejected",
                             "reason": "A zip inside a zip is not opened: upload it on its own.",
                             "lines": ["A zip inside a zip is not opened: upload it on its own."]}
                    else:
                        r = accept(db_path, dest, i.filename, user, parent=sha, conn=conn, reveal=reveal)
            finally:
                if os.path.exists(dest):
                    os.remove(dest)
            out.append(r)
    counts = Counter(r["status"] for r in out)
    summary = dict(base, members=len(out), counts=dict(counts))
    _record(conn, sha, kind="zip", name=name, path=None, size=base["size"], uploaded_at=_now(), uploaded_by=user, status="done",
            summary=summary, done_at=_now(), parent=None)
    return dict(summary, name=shown_name(name, "zip", sha, reveal), status="zip", queued=any(r["status"] == "queued" for r in out), files=out,
                lines=[f"A zip of {len(out)} file{'s' if len(out) != 1 else ''}: " + ", ".join(f"{n} {STATUS_WORDS.get(s, s)}" for s, n in counts.items()) + "."])


def shown_name(name: str, kind: str | None, upload_id: str | None, reveal: bool) -> str:
    """The name a file is shown under. A visit note, a zip, or a file that was not read is often saved under the
    patient's name: with names switched off it is shown by its kind and a short id (as a note's download is named)."""
    if reveal or kind in ("ema", "ema_report", "x12"):
        return name
    label = {"note": "Visit note", "zip": "Zip"}.get(kind or "", "File")
    ext = os.path.splitext(name or "")[1].lower()[:6]
    return f"{label} {str(upload_id or '')[:8]}".strip() + ext


STATUS_WORDS = {"queued": "to be added", "ready": "to be read", "already": "already in RemitWatch", "rejected": "not read",
                "matched": "visit notes matched",
                "choose": "visit notes that need a choice", "unmatched": "visit notes not matched yet", "done": "done", "failed": "failed"}


# --------------------------------------------------------------------------- the cycle reads what waits
def ingest_queued(store, log: Callable = print) -> dict:
    """Read every upload waiting for the cycle with the loader Dropbox files go through (dropbox_sync.ingest_new_files),
    in one batch as a Dropbox delivery would be, then note on each what it added. Called by pipeline.run_cycle under its
    lock. Returns the loader's counts plus 'uploads' (how many were read)."""
    from .dropbox_sync import ingest_new_files
    conn = store.conn
    ensure_schema(conn)
    queued = [dict(r) for r in conn.execute("SELECT * FROM uploads WHERE status='queued' ORDER BY uploaded_at, upload_id")]
    if not queued:
        return {"remits": 0, "ema": 0, "claims_837": 0, "uploads": 0}
    errors: dict[str, str] = {}

    def capture(*a) -> None:
        line = " ".join(str(x) for x in a)
        log(line)
        m = re.match(r"\s*(?:EMA|837) ERROR (.+?): (.*)$", line)
        if m:
            errors[m.group(1)] = m.group(2)
    paths = [r["path"] for r in queued if r.get("path") and os.path.exists(r["path"])]
    res = ingest_new_files(store, paths, log=capture) if paths else {"remits": 0, "ema": 0, "claims_837": 0}
    res.pop("remit_stats", None)
    for r in queued:
        _finish(conn, r, errors)
    res["uploads"] = len(queued)
    return res


def _finish(conn: sqlite3.Connection, up: dict, errors: dict) -> None:
    summary = json.loads(up.get("summary") or "{}")
    path = up.get("path") or ""
    sha = up.get("stored_sha") or up["upload_id"]
    f = conn.execute("SELECT source_path, ingested_at, kind, status, transactions, claims, lines, duplicates FROM files WHERE sha256=?", (sha,)).fetchone()
    status = "done"
    if not path or not os.path.exists(path):
        status, summary["error"] = "failed", "The kept file is gone from the upload folder."
    elif f is None or f[3] == "not_read":     # the loader left it unread (and says why), or never got to it
        status = "failed"
        prob = conn.execute("SELECT message FROM problems WHERE source=? ORDER BY id DESC LIMIT 1", (path,)).fetchone()
        summary["error"] = errors.get(os.path.basename(path)) or (prob[0] if prob else "The loader did not read it.")
    elif f[0] != path:
        summary["added"] = 0
        summary["meanwhile"] = {"name": os.path.basename(f[0] or ""), "at": f[1]}
    elif up["kind"] == "ema":
        added = (f[6] or 0) - (f[7] or 0)
        summary["added"] = added
        summary["already"] = max(0, (summary.get("distinct") or 0) - added)
    elif summary.get("x12") == "835":
        summary["added"] = (f[4] or 0) - (f[7] or 0)
        summary["already"] = f[7] or 0
        summary["file_status"] = f[3]
    else:
        summary["added"] = f[5] or 0
    summary["read_at"] = _now()
    _record(conn, up["upload_id"], status=status, summary=summary, done_at=_now())


# --------------------------------------------------------------------------- the words shown for each upload
def _day(d: str | None) -> str:
    try:
        x = date.fromisoformat(str(d)[:10])
        return f"{x:%b} {x.day}, {x.year}"
    except (TypeError, ValueError):
        return str(d or "")


def _n(x: int, one: str, many: str | None = None) -> str:
    return f"{x:,} {one if x == 1 else (many or one + 's')}"


def describe(s: dict, status: str, reveal: bool = False, note: dict | None = None, conn: sqlite3.Connection | None = None) -> list[str]:
    """What the person reads about one upload, in plain words: what was in it, what was (or will be) added, what was
    already there, and what was left out and why."""
    if status == "failed" and str(s.get("error") or "").startswith("Not read"):
        # the loader found it is none of the files RemitWatch reads (an A/R or production report queued before the page
        # refused them): its own words say what it is and why, and the report this page guessed then is not repeated
        return [str(s["error"])]
    out: list[str] = []
    if s.get("kind") == "ema_report":
        fmt = "Excel" if s.get("format") == "xlsx" else "CSV"
        out.append(f"EMA's {s.get('title') or REPORT_TITLES.get(s.get('report'), 'report')} ({fmt}), "
                   f"{_size_words(int(s.get('size') or 0))}: received.")
        if status in ("ready", "queued"):
            out.append("It is read by the next cycle, within a few minutes. What it held, and what it replaced, shows here then.")
        return out
    if s.get("kind") == "ema":
        span = "" if not s.get("service_from") else f", visits on {_day(s['service_from'])}" if s["service_from"] == s.get("service_to") \
            else f", visits {_day(s['service_from'])} to {_day(s['service_to'])}"
        out.append(f"{s.get('report')} export ({'Excel' if s.get('format') == 'xlsx' else 'CSV'}){span}: "
                   f"{_n(s.get('rows', 0), 'row')}{', ' + _n(s['claims'], 'claim') if s.get('claims') else ''}.")
        if status == "queued":
            out.append(f"New to RemitWatch: {s.get('new', 0):,}. Already in it: {s.get('already', 0):,}. "
                       "The new rows go in with the next cycle, within a few minutes; the worklist follows when its analysis finishes.")
        elif status == "done":
            if s.get("meanwhile"):
                out.append(f"Nothing added: the same file was read meanwhile from {s['meanwhile'].get('name')}.")
            else:
                out.append(f"Added {_n(s.get('added', 0), 'row')} on {_day(s.get('read_at'))}. Already in RemitWatch: {s.get('already', 0):,}.")
        left = s.get("left") or {}
        parts = []
        if s.get("above"):
            parts.append(f"{_n(s['above'], 'line')} above the column names (the report's title and filters)")
        if left.get("total"):
            parts.append(_n(left["total"], "totals line"))
        if left.get("footer"):
            parts.append(_n(left["footer"], "line") + " naming no claim, bill, patient, code or date (a note or footer)")
        if left.get("blank"):
            parts.append(_n(left["blank"], "blank line"))
        if parts:
            out.append("Left out: " + "; ".join(parts) + ".")
        if s.get("repeats"):
            out.append(f"{_n(s['repeats'], 'row')} repeat{'s' if s['repeats'] == 1 else ''} another row of the file exactly, "
                       "column for column; RemitWatch keeps one of each.")
        if s.get("missing"):
            out.append("Missing columns RemitWatch uses: " + "; ".join(f"{m['column']} ({m['why']})" for m in s["missing"])
                       + ". Run the report with these columns next time (see the guide).")
        if s.get("unused_columns"):
            u = s["unused_columns"]
            out.append(f"Not kept (RemitWatch has no use for them): {', '.join(u[:10])}{'…' if len(u) > 10 else ''}.")
    elif s.get("kind") == "x12" and s.get("x12") == "835":
        span = f", paid {_day(s['payment_from'])}{' to ' + _day(s['payment_to']) if s.get('payment_to') != s.get('payment_from') else ''}" \
            if s.get("payment_from") else ""
        out.append(f"Remittance file (835){span}: {_n(s.get('transactions', 0), 'payment')}, {_n(s.get('claims', 0), 'claim')}"
                   f"{' from ' + ', '.join(s['payers']) if s.get('payers') else ''}.")
        if status == "queued":
            out.append(f"New to RemitWatch: {s.get('new', 0):,} payment{'s' if s.get('new') != 1 else ''}. Already in it: {s.get('already', 0):,}. "
                       "They go in with the next cycle, within a few minutes.")
        elif status == "done":
            out.append(f"Added {_n(s.get('added', 0), 'payment')} on {_day(s.get('read_at'))}. Already in RemitWatch: {s.get('already', 0):,}."
                       if not s.get("meanwhile") else f"Nothing added: the same file was read meanwhile from {s['meanwhile'].get('name')}.")
        if s.get("problems"):
            out.append(f"{_n(s['problems'], 'part')} of the file could not be read and {'was' if s['problems'] == 1 else 'were'} left out "
                       "(the Data tab lists them).")
    elif s.get("kind") == "x12":
        out.append(f"Claim file (837): {_n(s.get('claims', 0), 'claim')}, {_n(s.get('lines', 0), 'line')}"
                   f"{', visits ' + _day(s['service_from']) + ' to ' + _day(s['service_to']) if s.get('service_from') else ''}.")
        out.append("It goes in with the next cycle, within a few minutes." if status == "queued" else
                   f"Added {_n(s.get('added', 0), 'claim')} on {_day(s.get('read_at'))}." if status == "done" else "")
    if s.get("error"):
        out.append(f"Not read: {s['error']}")
    if note is not None:
        out += _note_lines(note, reveal, conn)
    return [x for x in out if x]


def _confirm(n: dict, conn: sqlite3.Connection | None = None) -> bool:
    """Whether an appeal waits for a person before it uses this note: matched by name and date, and no identifier the
    note states agrees with its claims' (EMA's patient ID, the record number). Without the store at hand, a note that
    states an identifier is taken to agree."""
    if n.get("status") != "matched" or n.get("matched_by") == "person":
        return False
    stated = set(json.loads(n.get("ids_stated") or "[]"))
    if not stated or conn is None:
        return not stated
    pcns = json.loads(n.get("claims") or "[]")
    known = {modmed._nid(x) for ids in _ema_ids(conn, pcns).values() for x in ids}
    if pcns and conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='claims'").fetchone():
        known |= {modmed._nid(r[0]) for r in conn.execute(f"SELECT DISTINCT medical_record_number FROM claims WHERE patient_control_number IN "
                                                         f"({','.join('?' * len(pcns))}) AND medical_record_number IS NOT NULL", pcns)}
    return not (stated & known)


def _note_lines(n: dict, reveal: bool, conn: sqlite3.Connection | None = None) -> list[str]:
    who = f"{(n.get('patient_first') or '').title()} {(n.get('patient_last') or '').title()}".strip() if reveal else ""
    kind = n.get("note_type") or "Note"
    if n.get("status") == "matched":
        how = "attached by hand" if n.get("matched_by") == "person" else f"matched by {n.get('matched_by') or 'name and date of service'}"
        claims = json.loads(n.get("claims") or "[]")
        then = ("Only the name and the date agree with the claim, so its appeal uses it once a person confirms it in the item "
                "(or attaches it there)." if _confirm(n, conn) else "Appeals for that visit use it as a note read from EMA.")
        return [f"{kind} for the visit of {_day(n.get('service_date'))}{(' (' + who + ')') if who else ''}, {how}"
                f"{': claim ' + ', '.join(claims[:3]) if claims else ''}. {then}"]
    if n.get("status") == "choose":
        return [f"{kind}: more than one visit fits it. Pick the right one below."]
    return [f"{kind}: {n.get('reason') or 'not matched to a visit yet.'}"]


# --------------------------------------------------------------------------- visit notes
MONTHS = {m: i + 1 for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"))}
_MON = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
DATE_RES = [
    (re.compile(r"(?<![\d/])(\d{1,2})[/-](\d{1,2})[/-](\d{4}|\d{2})(?![\d/])"), "mdy"),
    (re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)"), "ymd"),
    (re.compile(_MON + r"\.?\s+(\d{1,2}),?\s+(\d{4})", re.I), "Mdy"),
    (re.compile(r"(?<!\d)(\d{1,2})\s+" + _MON + r"\.?,?\s+(\d{4})", re.I), "dMy"),
]
SERVICE_LABEL = re.compile(r"(date\s+of\s+service|service\s+date|\bdos\b|visit\s+date|date\s+of\s+visit|encounter\s+date|"
                           r"appointment\s+date|appt\.?\s+date|exam\s+date|date\s+seen)", re.I)
BIRTH_LABEL = re.compile(r"(\bdob\b|\bd\.o\.b\.?|date\s+of\s+birth|birth\s*date|\bborn\b)", re.I)
# the labels an identifier follows on a note ("Patient ID: 12345", "MRN 12345"): a number anywhere else (an address, a
# dose) is never taken for one
ID_LABEL = re.compile(r"(patient\s*(?:id\b|#|no\b|number)|\bmrn\b|medical\s+record|chart\s*(?:#|no\b|number)|"
                      r"account\s*(?:#|no\b|number)|\bpms\s*(?:id\b|#)|person\s*(?:id\b|#))", re.I)


def _iso(kind: str, m: re.Match) -> str | None:
    try:
        if kind == "mdy":
            y = int(m.group(3))
            y = y if len(m.group(3)) == 4 else (2000 + y if y < 30 else 1900 + y)
            return date(y, int(m.group(1)), int(m.group(2))).isoformat()
        if kind == "ymd":
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        if kind == "Mdy":
            return date(int(m.group(3)), MONTHS[m.group(1)[:3].lower()], int(m.group(2))).isoformat()
        return date(int(m.group(3)), MONTHS[m.group(2)[:3].lower()], int(m.group(1))).isoformat()
    except (ValueError, KeyError):
        return None


def note_dates(text: str) -> tuple[set[str], set[str]]:
    """Every date the note states, and those it gives as the date of service (the first date after a label such as
    "Date of Service", "DOS" or "Visit Date")."""
    found: list[tuple[int, str]] = []
    for rx, kind in DATE_RES:
        for m in rx.finditer(text):
            d = _iso(kind, m)
            if d:
                found.append((m.start(), d))
    found.sort()
    labeled = set()
    for m in SERVICE_LABEL.finditer(text):
        nxt = next((d for at, d in found if m.end() <= at <= m.end() + 60), None)
        if nxt:
            labeled.add(nxt)
    return {d for _, d in found}, labeled


def _words(text: str) -> str:
    return " " + re.sub(r"[^A-Z0-9]+", " ", (text or "").upper()).strip() + " "


def birth_date(text: str) -> str | None:
    """The birth date a note states (the first date just after "DOB" or "Date of Birth"), or None."""
    for m in BIRTH_LABEL.finditer(text or ""):
        window = text[m.end(): m.end() + 40]
        for rx, kind in DATE_RES:
            d = next((x for x in (_iso(kind, mm) for mm in rx.finditer(window)) if x), None)
            if d:
                return d
    return None


def stated_ids(text: str, values) -> list[str]:
    """Which of these identifiers (EMA's patient ID, a record number) the note states, each just after a label such as
    "Patient ID" or "MRN"."""
    near = [_words(text[m.end(): m.end() + 40]) for m in ID_LABEL.finditer(text or "")]
    out = []
    for v in sorted({str(v) for v in values if v}):
        w = _words(v).strip()
        if w and any(f" {w} " in chunk for chunk in near):
            out.append(v)
    return out


ID_FILLER = {"ID", "NO", "NUMBER", "NUM", "IS", "OF", "THE"}


def note_ids(text: str) -> list[str]:
    """The identifiers a note states for its patient: the value just after each "Patient ID", "MRN", "Chart #" or like
    label, in capitals, letters and digits only. They are compared with the claim's when an appeal looks for its note."""
    out: list[str] = []
    for m in ID_LABEL.finditer(text or ""):
        for tok in re.findall(r"[A-Za-z0-9][A-Za-z0-9\-]*", text[m.end(): m.end() + 40]):
            t = modmed._nid(tok)
            if t in ID_FILLER or not any(c.isdigit() for c in t):
                continue            # "Patient ID number: ...": the value comes after; an identifier has a digit
            if t not in out:
                out.append(t)
            break
    return out[:6]


def _name_in(name: str | None, words: str, first_word: bool = False) -> bool:
    n = re.sub(r"[^A-Z0-9]+", " ", (name or "").upper()).strip()
    if first_word:
        n = n.split(" ")[0] if n else ""
    if len(n.replace(" ", "")) < 2:
        return False
    return f" {n} " in words or (" " in n and f" {n.replace(' ', '')} " in words)


def note_kind(text: str) -> tuple[str, str]:
    head = (text or "")[:4000].lower()
    if "procedure note" in head or "operative report" in head or "operative note" in head:
        return "28570-0", "Procedure note"
    if "progress note" in head or "office visit" in head or "visit note" in head or "chief complaint" in head:
        return "11506-3", "Progress note"
    return "", "Visit note"


def _visits_on(conn: sqlite3.Connection, days: set[str]) -> list[dict]:
    """The claims RemitWatch holds items for on these dates of service, with the patient's names as the payer gave them."""
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not days or not {"findings", "claims"} <= have:
        return []
    ds = sorted(days)
    q = f"""SELECT f.finding_id, f.claim_key, f.patient_control_number pcn, f.payer_group,
                   COALESCE(f.service_date, c.statement_from) dos,
                   c.patient_last, c.patient_first, c.corrected_patient_last, c.corrected_patient_first
            FROM findings f JOIN claims c ON c.claim_key = f.claim_key
            WHERE f.claim_key IS NOT NULL AND COALESCE(f.service_date, c.statement_from) IN ({','.join('?' * len(ds))})"""
    cur = conn.execute(q, ds)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _ema_ids(conn: sqlite3.Connection, pcns: list[str]) -> dict[str, set[str]]:
    if not pcns or not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ema_rows'").fetchone():
        return {}
    out: dict[str, set[str]] = defaultdict(set)
    for i in range(0, len(pcns), 900):
        part = pcns[i:i + 900]
        for cid, pid in conn.execute(f"SELECT DISTINCT claim_id, patient_id FROM ema_rows WHERE claim_id IN ({','.join('?' * len(part))}) "
                                     "AND COALESCE(patient_id,'')!=''", part):
            out[cid].add(str(pid))
    return out


def match_text(conn: sqlite3.Connection, text: str) -> dict:
    """The visit a note is for: a claim RemitWatch holds items for whose patient (family and first name) the note names
    and whose date of service it states. A note that labels its date of service is for that date only, never for an
    earlier visit it mentions. Two patients of one name that day are told apart by EMA's patient ID when the note states
    it. The identifiers of the visit's claims that the note states are kept (ids_stated): an appeal takes the note by
    itself only when one agrees as well as the name. Returns {'status': 'matched' | 'choose' | 'unmatched', ...}."""
    days, labeled = note_dates(text)
    if not days:
        return {"status": "unmatched", "reason": "The note states no date RemitWatch can read."}
    words = _words(text)
    hits = []
    for v in _visits_on(conn, days):
        last = next((x for x in (v["patient_last"], v["corrected_patient_last"]) if _name_in(x, words)), None)
        first = next((x for x in (v["patient_first"], v["corrected_patient_first"]) if _name_in(x, words, first_word=True)), None)
        if last and first:
            hits.append(dict(v, last=last, first=first))
    if labeled:             # the note says which visit it is for: never another date it only mentions
        hits = [h for h in hits if h["dos"] in labeled]
    if not hits:
        said = f" (its date of service, {_day(min(labeled))})" if labeled else ""
        return {"status": "unmatched", "reason": f"No item in RemitWatch is for a patient this note names on the date it is for{said}. "
                                                 "It is kept and matched again as new remittances arrive; or attach it to its item."}
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for h in hits:
        groups[(modmed._norm(h["last"]), modmed._norm((h["first"] or "").split(" ")[0]), h["dos"])].append(h)
    ids = _ema_ids(conn, sorted({h["pcn"] for h in hits if h.get("pcn")}))
    visits = []
    for (_l, _f, dos), hs in groups.items():
        people: dict[str, list[dict]] = defaultdict(list)
        for h in hs:
            for pid in ids.get(h.get("pcn"), {""}):
                people[pid].append(h)
        named = [p for p in people if p]
        if len(named) > 1:
            stated = stated_ids(text, named)
            if len(stated) != 1:
                for p in named:
                    visits.append(_visit(people[p] + people.get("", []), dos, p))
                continue
            hs = people[stated[0]] + people.get("", [])
            named = stated
        visits.append(_visit(hs, dos, named[0] if named else ""))
    dob, own = birth_date(text), note_ids(text)
    for v in visits:
        v.pop("mrns", None)
        v["ids_stated"] = own
        v["birth_date"] = dob
    if len(visits) == 1:
        return dict(visits[0], status="matched", how="patient name and date of service"
                    + (" (the date the note gives as the date of service)" if visits[0]["service_date"] in labeled else ""))
    return {"status": "choose", "reason": "More than one visit fits this note.", "candidates": visits}


def _visit(hs: list[dict], dos: str, ema_id: str) -> dict:
    h = hs[0]
    return {"patient_last": h["last"], "patient_first": h["first"], "service_date": dos, "ema_patient_id": ema_id or None,
            "claims": sorted({x["pcn"] for x in hs if x.get("pcn")}), "payers": sorted({x["payer_group"] for x in hs if x.get("payer_group")}),
            "findings": len({x["finding_id"] for x in hs})}


def match_finding(conn: sqlite3.Connection, finding_id: str) -> dict:
    """A note a person attached to an item: the item's visit, whatever the note says."""
    r = conn.execute("""SELECT f.claim_key, f.patient_control_number, f.payer_group,
                               COALESCE(f.service_date, c.statement_from) dos,
                               COALESCE(c.patient_last, c.corrected_patient_last), COALESCE(c.patient_first, c.corrected_patient_first)
                        FROM findings f LEFT JOIN claims c ON c.claim_key = f.claim_key WHERE f.finding_id=?""", (finding_id,)).fetchone()
    if not r or not r[0]:
        raise Rejected("That item is not tied to one claim, so a visit note cannot be attached to it.")
    if not r[3] or not r[4]:
        raise Rejected("That item's claim has no patient name or date of service, so a note cannot be tied to its visit.")
    ids = _ema_ids(conn, [r[1]] if r[1] else [])
    pid = sorted(ids.get(r[1], set()))
    mrn = conn.execute("SELECT medical_record_number FROM claims WHERE claim_key=?", (r[0],)).fetchone()
    # the person tied the note to this claim: its identifiers are the note's from now on
    return {"status": "matched", "how": "person", "patient_last": r[4], "patient_first": r[5], "service_date": r[3],
            "ema_patient_id": pid[0] if len(pid) == 1 else None, "claims": [r[1]] if r[1] else [], "payers": [r[2]] if r[2] else [],
            "ids_stated": sorted({modmed._nid(x) for x in (pid if len(pid) == 1 else []) + ([mrn[0]] if mrn and mrn[0] else [])} - {""})}


def _note_row(conn: sqlite3.Connection, **where) -> dict | None:
    k, v = next(iter(where.items()))
    cur = conn.execute(f"SELECT * FROM uploaded_notes WHERE {k}=?", (v,))
    r = cur.fetchone()
    return dict(zip([d[0] for d in cur.description], r)) if r else None


def _set_match(conn: sqlite3.Connection, note_id: str, m: dict, by: str | None = None) -> None:
    if m["status"] == "matched":
        stated = list(m.get("ids_stated") or [])
        if by == "person" and m.get("ema_patient_id") and modmed._nid(m["ema_patient_id"]) not in stated:
            stated.append(modmed._nid(m["ema_patient_id"]))      # a person picked this visit: its EMA patient is the note's
        conn.execute("""UPDATE uploaded_notes SET status='matched', reason=NULL, patient_last=?, patient_first=?, ema_patient_id=?,
                        service_date=?, matched_by=?, matched_at=?, candidates=NULL, claims=?, ids_stated=?,
                        birth_date=COALESCE(?, birth_date) WHERE note_id=?""",
                     (m["patient_last"], m["patient_first"], m.get("ema_patient_id"), m["service_date"],
                      by or m.get("how"), _now(), json.dumps(m.get("claims") or []), json.dumps(stated), m.get("birth_date"), note_id))
    else:
        conn.execute("UPDATE uploaded_notes SET status=?, reason=?, candidates=?, matched_at=? WHERE note_id=?",
                     (m["status"], m.get("reason"), json.dumps(m.get("candidates") or []), _now(), note_id))
    conn.commit()


def _take_note(conn, db_path: str, tmp: str, name: str, sha: str, user: str, finding_id: str | None, parent: str | None, base: dict) -> dict:
    """Keep a visit note and tie it to its visit: the item's, when it was attached to one; else the one its text names."""
    from .notes import extract_text
    old = _note_row(conn, sha256=sha)
    if old:
        if finding_id:
            m = match_finding(conn, finding_id)
            had = set(json.loads(old.get("claims") or "[]"))
            if not (old["status"] == "matched" and old.get("matched_by") == "person" and old["service_date"] == m["service_date"]
                    and set(m["claims"]) <= had):
                if old["status"] == "matched" and old.get("matched_by") == "person" and old["service_date"] == m["service_date"]:
                    # the same visit's other claim: the person's note for both
                    m = dict(m, claims=sorted(had | set(m["claims"])),
                             ids_stated=sorted(set(json.loads(old.get("ids_stated") or "[]")) | set(m.get("ids_stated") or [])))
                _set_match(conn, old["note_id"], m, by="person")      # attached by a person now, whatever matched it before
                n = _note_row(conn, note_id=old["note_id"])
                return dict(base, kind="note", note_id=n["note_id"], status=n["status"], note=n)
        seen = {"how": "upload", "at": old["uploaded_at"], "by": old["uploaded_by"], "status": "done"}
        return dict(base, kind="note", note_id=old["note_id"], status="already", note=old, already=seen, lines=[_already_line(seen)])
    size = os.path.getsize(tmp)
    if size > NOTE_MAX_BYTES:
        raise Rejected(f"A visit note can be at most {NOTE_MAX_BYTES // (1024 * 1024)} MB.")
    with open(tmp, "rb") as fh:
        data = fh.read()
    ext = os.path.splitext(name.lower())[1]
    ext = ".pdf" if data[:5] == b"%PDF-" else ".docx" if data[:2] == b"PK" else ext if ext in NOTE_EXT else ".txt"
    try:
        text, kind = extract_text("note" + ext, data)
    except ValueError as exc:
        raise Rejected(f"The note could not be read: {exc}.") from exc
    m = match_finding(conn, finding_id) if finding_id else None          # an item that cannot take a note refuses it before it is kept
    loinc, ntype = note_kind(text)
    note_id = sha[:24]
    path = _keep(tmp, os.path.join(upload_dir(db_path), "notes"), f"{note_id}{ext}")
    conn.execute("""INSERT INTO uploaded_notes (note_id, sha256, name, path, content_type, kind, size, uploaded_at, uploaded_by, status,
                    note_type, loinc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (note_id, sha, name, path, NOTE_EXT[ext], kind, size, _now(), user, "unmatched", ntype, loinc))
    conn.commit()
    by = "person" if m else None
    if m is None:
        if not (text or "").strip():        # a scanned page: a picture, no text (a short note with text is still read)
            m = {"status": "unmatched", "reason": "This PDF is a scanned picture, with no text to read the patient and date from: "
                                                  "attach it to its item in the list below."}
        else:
            m = match_text(conn, text)
    _set_match(conn, note_id, m, by=by)
    n = _note_row(conn, note_id=note_id)
    summary = dict(base, kind="note", note_id=note_id)
    _record(conn, sha, stored_sha=sha, kind="note", name=name, path=path, size=size, uploaded_at=n["uploaded_at"], uploaded_by=user,
            status="done", summary=summary, done_at=_now(), parent=parent)
    return dict(summary, status=n["status"], note=n)


def choose(conn: sqlite3.Connection, note_id: str, pick: int | None = None, finding_id: str | None = None) -> dict:
    """A person settles a note's visit: one of its candidates, or an item."""
    ensure_schema(conn)
    n = _note_row(conn, note_id=note_id)
    if not n:
        raise Rejected("No such note.")
    if finding_id:
        _set_match(conn, note_id, match_finding(conn, finding_id), by="person")
    else:
        cands = json.loads(n.get("candidates") or "[]")
        if pick is None or not 0 <= pick < len(cands):
            raise Rejected("Pick one of the visits listed.")
        _set_match(conn, note_id, dict(cands[pick], status="matched"), by="person")
    return _note_row(conn, note_id=note_id)


def remove_note(conn: sqlite3.Connection, note_id: str) -> bool:
    """Take a note back (attached to the wrong visit, or the wrong file): the file and its row go, and its upload is marked
    'removed'. A packet built from it stays with its item for the record, but it is not faxed from now on: the appeal code
    sees the note was taken back (api._packet_problem reads the upload's status and the missing row), and the next appeal
    builds the packet again from the note in its place."""
    ensure_schema(conn)
    n = _note_row(conn, note_id=note_id)
    if not n:
        return False
    if n.get("path") and os.path.exists(n["path"]):
        os.remove(n["path"])
    conn.execute("DELETE FROM uploaded_notes WHERE note_id=?", (note_id,))
    conn.execute("UPDATE uploads SET status='removed', done_at=? WHERE upload_id=?", (_now(), n["sha256"]))
    conn.commit()
    return True


def _note_text(path: str) -> str | None:
    from .notes import extract_text
    try:
        with open(path, "rb") as fh:
            text, _k = extract_text(os.path.basename(path), fh.read())
    except (OSError, ValueError):
        return None
    return text if (text or "").strip() else None


def rematch(conn: sqlite3.Connection, limit: int = 300) -> dict:
    """Notes not matched yet, matched again (new remittances bring new items): run at the end of each cycle. A note
    matched by its text before RemitWatch kept which identifiers it states has them worked out now."""
    ensure_schema(conn)
    out = Counter()
    for note_id, path in conn.execute("""SELECT note_id, path FROM uploaded_notes WHERE status='unmatched'
                                         ORDER BY uploaded_at DESC LIMIT ?""", (limit,)).fetchall():
        text = _note_text(path)
        if text is None:
            continue
        m = match_text(conn, text)
        if m["status"] != "unmatched":
            _set_match(conn, note_id, m)
            out[m["status"]] += 1
    for note_id, path, claims, ema_id, by in conn.execute("""SELECT note_id, path, claims, ema_patient_id, matched_by FROM uploaded_notes
                                                             WHERE status='matched' AND ids_stated IS NULL LIMIT ?""", (limit,)).fetchall():
        text = _note_text(path) or ""
        pcns = json.loads(claims or "[]")
        known = {ema_id} if ema_id else set()
        if by == "person" and pcns and conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='claims'").fetchone():
            known |= {r[0] for r in conn.execute(f"SELECT DISTINCT medical_record_number FROM claims WHERE patient_control_number IN "
                                                 f"({','.join('?' * len(pcns))}) AND medical_record_number IS NOT NULL", pcns)}
        stated = sorted({modmed._nid(x) for x in known} - {""}) if by == "person" else note_ids(text)   # a person settled them, or the note states them
        conn.execute("UPDATE uploaded_notes SET ids_stated=?, birth_date=COALESCE(birth_date, ?) WHERE note_id=?",
                     (json.dumps(stated), birth_date(text), note_id))
        out["identifiers"] += 1
    conn.commit()
    return dict(out)


class UploadSource(modmed.LiveSource):
    """The uploaded notes, offered to modmed.find_note as EMA's records are: each matched note a clinical note of its
    visit's date, under a patient record that carries only what the note itself establishes. find_note's rules then
    decide as they do for EMA's API.

    Who a note's patient is: the EMA patient ID or record number the note states (or a person's attaching it to an
    item settled), else the name and birth date it states. Notes of one patient so identified share a record; notes
    that state no identifier share one only by name and birth date, and that never identifies a patient by itself. A
    note attached to an item by a person is that claim's by the person's choice.

    for_claim narrows the notes to the ones that can be a claim's: tied to it, or of its EMA patient; a note tied to
    another EMA patient is never offered, and a note with no EMA patient ID only to a claim with none either."""

    def __init__(self, conn: sqlite3.Connection | None, notes: list[dict] | None = None):     # noqa: D107  (nothing is fetched)
        self._pract = {}
        if notes is None:
            ensure_schema(conn)
            cur = conn.execute("SELECT * FROM uploaded_notes WHERE status='matched' AND service_date IS NOT NULL")
            cols = [d[0] for d in cur.description]
            notes = [dict(zip(cols, r)) for r in cur.fetchall()]
        self.notes = notes
        self.people: dict[str, dict] = {}
        self.docs: dict[str, list[dict]] = defaultdict(list)
        self.paths: dict[str, tuple[str, str]] = {}
        for n in self.notes:
            stated = json.loads(n.get("ids_stated") or "[]")
            claims = json.loads(n.get("claims") or "[]")
            person = n.get("matched_by") == "person"
            ema = n.get("ema_patient_id") if modmed._nid(n.get("ema_patient_id")) in stated else None
            if ema:
                k = f"e|{ema}"
            elif stated:
                k = "s|" + "|".join(sorted(stated))
            elif person:
                k = "p|" + ",".join(sorted(claims))
            else:
                k = f"n|{modmed._norm(n['patient_last'])}|{modmed._norm((n['patient_first'] or '').split(' ')[0])}|{n.get('birth_date') or ''}"
            pid = "u" + hashlib.sha256(k.encode()).hexdigest()[:16]
            p = self.people.setdefault(pid, {"resourceType": "Patient", "id": pid, "identifier": [], "birthDate": n.get("birth_date"),
                                             "name": [{"family": n["patient_last"], "given": [n["patient_first"] or ""]}], "_by_person": set()})
            for v in stated:
                if {"value": v} not in p["identifier"]:
                    p["identifier"].append({"value": v})
            if person:
                p["_by_person"].update(claims)
            doc_id = DOC_PREFIX + n["note_id"]
            self.docs[pid].append({
                "resourceType": "DocumentReference", "id": doc_id, "status": "current", "docStatus": "final",
                "type": {"coding": [{"code": n["loinc"], "display": n["note_type"]}] if n.get("loinc") else [], "text": n.get("note_type") or "Visit note"},
                "category": [{"coding": [{"code": "clinical-note"}]}],
                "date": n["service_date"], "context": {"period": {"start": n["service_date"]}},
                "author": [{"display": "uploaded by " + (n.get("uploaded_by") or "a person")}],
                "subject": {"reference": f"Patient/{pid}"},
                "content": [{"attachment": {"contentType": n["content_type"], "url": f"upload:{n['note_id']}", "title": n["name"]}}]})
            self.paths[n["note_id"]] = (n["path"], n["content_type"])

    def for_claim(self, key: modmed.ClaimKey) -> "UploadSource":
        ids = set(key.ema_patient_ids or ())

        def ok(n: dict) -> bool:
            if key.claim_id and key.claim_id in json.loads(n.get("claims") or "[]"):
                return True
            if n.get("ema_patient_id"):
                return n["ema_patient_id"] in ids
            return not ids
        return UploadSource(None, [n for n in self.notes if ok(n)])

    def identify(self, p: dict, key: modmed.ClaimKey) -> list[str]:
        if key.claim_id and key.claim_id in p.get("_by_person", ()):
            return [modmed.PERSON]
        return modmed._agree(p, key)

    def patient(self, pid: str) -> dict | None:
        return self.people.get(pid)             # an EMA patient ID is looked for among the identifiers, every record that has it

    def patients_by_identifier(self, value: str) -> list[dict]:
        v = modmed._nid(value)
        return [p for p in self.people.values() if v and any(modmed._nid(i.get("value")) == v for i in p["identifier"])]

    def patients_by_name(self, family: str, given: str) -> list[dict]:
        return [p for p in self.people.values() if modmed._family_ok(p, family) and modmed._given_ok(p, given)]

    def encounters(self, pid: str, dos: str) -> list[dict]:
        return []

    def documents(self, pid: str, dos: str) -> list[dict]:
        return list(self.docs.get(pid, []))

    def practitioner(self, ref: str) -> dict | None:
        return None

    def content(self, att: dict) -> tuple[bytes, str]:
        note_id = (att.get("url") or "").split(":", 1)[-1]
        path, ctype = self.paths[note_id]
        with open(path, "rb") as fh:
            return fh.read(), ctype


def find_note(conn: sqlite3.Connection, key: modmed.ClaimKey, pick: str | None = None, reveal: bool = False,
              src: UploadSource | None = None) -> dict:
    """The uploaded note for a claim, with modmed.find_note's answer: found, ambiguous (a person picks) or not_found. A
    pick that is not an uploaded note's id is EMA's, and is not looked for here. `src`: the notes already read, for a
    caller that looks up many claims. The note's own file name is given only where names are shown (`reveal`)."""
    if pick and not str(pick).startswith(DOC_PREFIX):
        return {"status": "not_found", "reason": NO_NOTE, "candidates": [], "source": "upload"}
    src = src or UploadSource(conn)
    if not src.notes:
        return {"status": "not_found", "reason": NO_NOTE, "candidates": [], "source": "upload"}
    res = modmed.find_note(src, key, pick, reveal)
    res["source"] = "upload"
    if res["status"] == "not_found":
        res["reason"] = NO_NOTE
    elif res["status"] == "found":
        nid = res["note"]["id"][len(DOC_PREFIX):]
        n = next((n for n in src.notes if n["note_id"] == nid), None)
        res["note"]["uploaded_name"] = shown_name(n["name"], "note", n["sha256"], reveal) if n else None
    return res


def note_days(conn: sqlite3.Connection) -> set[str]:
    """The dates of service uploaded notes are for (a cheap first test before find_note)."""
    ensure_schema(conn)
    return {r[0] for r in conn.execute("SELECT DISTINCT service_date FROM uploaded_notes WHERE status='matched' AND service_date IS NOT NULL")}


# --------------------------------------------------------------------------- the page's history
def history(conn: sqlite3.Connection, reveal: bool = False, limit: int = 100) -> list[dict]:
    ensure_schema(conn)
    cur = conn.execute("SELECT * FROM uploads ORDER BY uploaded_at DESC, parent IS NOT NULL, name LIMIT ?", (limit,))
    cols = [d[0] for d in cur.description]
    out = []
    for r in cur.fetchall():
        u = dict(zip(cols, r))
        s = json.loads(u.get("summary") or "{}")
        note = _note_row(conn, sha256=u["upload_id"]) if u["kind"] == "note" else None
        if u["status"] == "rejected":
            lines = [s.get("reason") or "Not read."]
        elif u["kind"] == "zip":
            c = s.get("counts") or {}
            lines = [f"A zip of {s.get('members', 0)} file{'s' if s.get('members') != 1 else ''}: "
                     + ", ".join(f"{n} {STATUS_WORDS.get(k, k)}" for k, n in c.items()) + ". Each is listed on its own."]
        elif u["status"] == "removed":
            lines = ["Taken back: the note was removed."]
        elif u["kind"] == "ema_report":
            # handed to the intake folder: what the cycle made of it is in the report catalog, under the file's SHA-256
            res = report_outcome(conn, u["upload_id"])
            lines = (res["lines"] + [c["message"] for c in res["checks"]]) if res else describe(s, u["status"], reveal)
        else:
            lines = describe(s, u["status"], reveal, note=note, conn=conn)
        item = {"upload_id": u["upload_id"], "name": shown_name(u["name"], u["kind"], u["upload_id"], reveal), "kind": u["kind"],
                "status": u["status"], "uploaded_at": u["uploaded_at"],
                "uploaded_by": u["uploaded_by"], "done_at": u["done_at"], "lines": lines, "parent": u["parent"]}
        if note:
            item["note"] = _note_public(note, reveal, conn)
        out.append(item)
    return out


def _note_public(n: dict, reveal: bool, conn: sqlite3.Connection | None = None) -> dict:
    out = {k: n.get(k) for k in ("note_id", "status", "service_date", "note_type", "matched_by", "kind")}
    out["claims"] = json.loads(n.get("claims") or "[]")
    out["confirm"] = _confirm(n, conn)
    if reveal:
        out["patient"] = f"{(n.get('patient_first') or '').title()} {(n.get('patient_last') or '').title()}".strip()
    cands = json.loads(n.get("candidates") or "[]")
    out["candidates"] = [{"service_date": c.get("service_date"), "claims": c.get("claims"), "payers": c.get("payers"),
                          **({"patient": f"{(c.get('patient_first') or '').title()} {(c.get('patient_last') or '').title()}".strip()} if reveal else {})}
                         for c in cands]
    return out


# =============================================================================== reports and large files: the hand-off
#
# EMA's A/R and Production reports, and any file larger than CHUNK_FROM_BYTES, come in through the chunked upload
# (remitwatch.api_uploads). The page works out the SHA-256 of the file as it is on the person's computer, then sends it
# in pieces of CHUNK_BYTES (a text report compressed with gzip on the way), each with its own SHA-256, checked as it
# arrives. When the last piece is in, the whole file is checked against the first SHA-256 (unpacked on the way when it
# came compressed) and it becomes one file and a JSON sidecar in an intake folder, read by the next cycle
# (remitwatch.intake):
#
#   server      <data_dir>/incoming/ready/<upload_id>__<safe name>[.gz]      beside the store, owner-only
#   serverless  <state folder>/intake/<upload_id>__<safe name>[.gz]          REMITWATCH_STATE_PATH in Dropbox
#
# The sidecar, <same name>.json, is {"name", "origin": "upload", "user", "sha256", "size", "gz", "received_at",
# "report_hint"}. sha256 and size are those of the original bytes, so an upload and the same file found in the Dropbox
# folder are one file to RemitWatch. The sidecar is written last: a data file without one is not complete (an upload
# still finishing, or one cut off), and the cycle leaves it alone.
#
# While a file arrives, its record (who, what, the SHA-256 of each piece received, the bytes so far) is kept beside its
# pieces: on a server in <data_dir>/incoming/sessions/, the pieces appended to <data_dir>/incoming/<upload_id>.part; on a
# serverless host in the state folder (upload_sessions/<upload_id>.json), each piece relayed to a Dropbox upload session
# (a local state folder keeps them in upload_sessions/<upload_id>.part). Nothing is held in memory between two requests:
# any instance takes the next piece, and an upload cut off carries on from the first piece that did not arrive.

CHUNK_BYTES = 4 * 1024 * 1024           # a piece. Every piece but the last is exactly this long: a Dropbox upload session takes
                                        # whole 4 MiB parts, and Vercel refuses a request body over about 4.5 MB
CHUNK_FROM_BYTES = 20 * 1024 * 1024     # the page sends a file larger than this in pieces (an A/R or Production report always)
ORIGINAL_MAX_FACTOR = 10                # the file before the page compressed it may be at most this many times the cap
SESSION_MAX_AGE = 24 * 60 * 60          # an upload left unfinished this long is removed, pieces and all
SESSION_KEEP = 30 * 24 * 60 * 60        # what became of a finished upload is shown this long
SWEEP_EVERY = 600                       # seconds between two clean-ups made by one process
VERIFY_SECONDS = 200                    # a serverless host checks a finished file within this time (its function has 300)
INCOMING, READY, DONE, SESSIONS = "incoming", "ready", "done", "sessions"
STATE_INTAKE, STATE_SESSIONS = "intake", "upload_sessions"
REPORT_HINTS = ("ar_report", "production", "charge_lines", "era_lines", "claim_status")
SIDECAR_KEYS = ("name", "origin", "user", "sha256", "size", "gz", "received_at", "report_hint")
UPLOAD_ID = re.compile(r"[0-9a-f]{32}")
SHA256_HEX = re.compile(r"[0-9a-f]{64}")


def max_upload_bytes() -> int:
    """The most a file may weigh as it is sent (compressed, when the page compresses it): REMITWATCH_UPLOAD_MAX_GB, 2 by
    default."""
    try:
        gb = float(os.environ.get("REMITWATCH_UPLOAD_MAX_GB") or 2)
    except ValueError:
        gb = 2.0
    return int(max(gb, 0.001) * 1024 ** 3)


def _size_words(n: int) -> str:
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.1f} GB".replace(".0 GB", " GB")
    if n >= 1024 * 1024:
        return f"{n / (1024 * 1024):,.1f} MB".replace(".0 MB", " MB")
    return f"{max(n, 0) / 1024:,.0f} KB" if n >= 1024 else f"{n:,} bytes"


class PieceRefused(Exception):
    """What an upload cannot take (a start, a piece or a finish), with the HTTP status for it and a message in plain
    words. `next_index` is the piece the upload waits for, when the page should carry on from there."""

    def __init__(self, status: int, message: str, next_index: int | None = None):
        super().__init__(message)
        self.status, self.message, self.next_index = status, message, next_index


class Stale(Exception):
    """An upload's record changed between reading it and saving it (another request for the same upload)."""


def data_dir(db_path: str) -> str:
    """The folder the store is in (/data on a server): the intake folder sits beside the store, never in the repository."""
    return os.path.dirname(os.path.abspath(db_path))


def intake_safe_name(name: str) -> str:
    """The file's own name made safe for a file name on a server and in Dropbox: no folders, no character either one
    refuses, not hidden, not ending in a dot or a space, not too long. The original name is kept in the sidecar."""
    n = unicodedata.normalize("NFC", os.path.basename(str(name or "").replace("\\", "/")))
    stem, ext = os.path.splitext(n)
    stem = re.sub(r"[^\w .,()&+'#=@-]", "_", stem)
    stem = re.sub(r"_{2,}", "_", re.sub(r" {2,}", " ", stem)).strip(" ._")[:100].strip(" ._") or "file"
    ext = re.sub(r"[^A-Za-z0-9]", "", ext)[:10].lower()
    return stem + (f".{ext}" if ext else "")


def pair_name(upload_id: str, name: str, gz: bool) -> str:
    """The intake file's name: <upload_id>__<safe name>, and .gz when the page compressed it."""
    return f"{upload_id}__{intake_safe_name(name)}" + (".gz" if gz else "")


def make_sidecar(name: str, user: str, sha256: str, size: int, gz: bool, report_hint: str | None,
                 received_at: str | None = None) -> dict:
    """The JSON beside an intake file: exactly the keys the cycle reads (SIDECAR_KEYS)."""
    return {"name": str(name), "origin": "upload", "user": str(user or ""), "sha256": sha256, "size": int(size), "gz": bool(gz),
            "received_at": received_at or _now(), "report_hint": report_hint if report_hint in REPORT_HINTS else None}


def sidecar_bytes(sc: dict) -> bytes:
    return json.dumps({k: sc[k] for k in SIDECAR_KEYS}, ensure_ascii=False).encode("utf-8")


def new_record(user: str, name: str, size: int, gz: bool, report_hint: str | None, sha256: str | None) -> dict:
    """A new upload's record: who started it, the file's name, size and SHA-256 (as the page read them), whether it comes
    compressed, and what the page recognised it as."""
    now, uid = _now(), secrets.token_hex(16)
    return {"upload_id": uid, "user": user or "", "name": str(name)[:300], "size": int(size), "gz": bool(gz),
            "report_hint": report_hint if report_hint in REPORT_HINTS else None, "sha256": sha256,
            "created_at": now, "updated_at": now, "chunks": [], "bytes": 0, "total": None, "closed": False,
            "state": "receiving", "pair": pair_name(uid, name, gz), "finished_at": None, "message": None, "session": None}


def check_start(name: str, size: Any, gz: Any, report_hint: Any, sha256: Any) -> tuple[str, int, bool, str | None, str | None]:
    """The start of an upload, checked: (name, size, gz, report_hint, sha256). Raises PieceRefused."""
    name = str(name or "").strip()
    if not name or len(name) > 300 or any(ord(c) < 32 for c in name):
        raise PieceRefused(400, "The file's name is missing or not valid.")
    try:
        size = int(size)
    except (TypeError, ValueError):
        raise PieceRefused(400, "The file's size is missing.") from None
    if size <= 0:
        raise PieceRefused(400, "The file is empty.")
    gz = gz is True or str(gz).lower() in ("1", "true")
    cap = max_upload_bytes()
    if size > cap * (ORIGINAL_MAX_FACTOR if gz else 1):
        raise PieceRefused(413, f"A file can be at most {_size_words(cap)} as it is sent. Export a shorter date range.")
    hint = report_hint if report_hint in REPORT_HINTS else None
    sha = str(sha256 or "").lower() or None
    if sha is not None and not SHA256_HEX.fullmatch(sha):
        raise PieceRefused(400, "The file's checksum is not valid.")
    return name, size, gz, hint, sha


def check_piece(rec: dict, index: int, total: int, sha256: str, data: bytes) -> bool:
    """Whether this piece is to be added (True), or is one the upload has already (False: a piece sent again after an
    answer was lost; nothing to do). Raises PieceRefused for a piece the upload cannot take."""
    n = len(rec["chunks"])
    if rec["state"] != "receiving":
        raise PieceRefused(409, "This upload is finished: nothing more can be added to it.")
    if index < 0 or total < 0:
        raise PieceRefused(400, "The piece number is not valid.")
    if not data:
        raise PieceRefused(400, "The piece is empty.")
    if len(data) > CHUNK_BYTES:
        raise PieceRefused(413, f"A piece can be at most {CHUNK_BYTES // (1024 * 1024)} MB. The page cuts files into pieces of that size.")
    if hashlib.sha256(data).hexdigest() != sha256:
        raise PieceRefused(422, f"Piece {index + 1} did not arrive whole: its checksum does not match. The page sends it again.", n)
    if index < n:
        if rec["chunks"][index] == sha256:
            return False
        raise PieceRefused(409, f"Piece {index + 1} came before with other bytes: the file changed while it was sent. "
                                "Start the upload again.")
    if index > n:
        raise PieceRefused(409, f"Piece {index + 1} came before piece {n + 1}: the pieces go in order. "
                                f"The page carries on from piece {n + 1}.", n)
    known = rec.get("total") or 0
    if total and known and total != known:
        raise PieceRefused(409, "The number of pieces changed while the file was sent. Start the upload again.")
    last = (total or known) - 1 if (total or known) else None
    if last is not None and index > last:
        raise PieceRefused(400, "That piece is past the last one.")
    if rec.get("closed"):
        raise PieceRefused(409, "The last piece has arrived already, so nothing more can be added. Start the upload again.")
    if len(data) < CHUNK_BYTES and last is not None and index != last:
        raise PieceRefused(400, f"Only the last piece may be shorter than {CHUNK_BYTES // (1024 * 1024)} MB.")
    if rec["bytes"] + len(data) > max_upload_bytes():
        raise PieceRefused(413, f"A file can be at most {_size_words(max_upload_bytes())} as it is sent. Export a shorter date range.")
    return True


def record_piece(rec: dict, index: int, total: int, sha256: str, size: int) -> None:
    rec["chunks"].append(sha256)
    rec["bytes"] += size
    if total:
        rec["total"] = total
    if size < CHUNK_BYTES or (rec.get("total") and index == rec["total"] - 1):
        rec["closed"] = True            # the last piece: only a piece shorter than CHUNK_BYTES, or the one the page says is last
    rec["updated_at"] = _now()


def check_finish(rec: dict, sha256: str, chunks: int, nbytes: int) -> None:
    """Whether every piece is in, and the checksum is the one the upload started with. Raises PieceRefused."""
    n = len(rec["chunks"])
    if not SHA256_HEX.fullmatch(sha256 or ""):
        raise PieceRefused(400, "The file's checksum is missing or not valid.")
    if not n:
        raise PieceRefused(409, "No piece of this file has arrived yet.", 0)
    if chunks != n or nbytes != rec["bytes"] or (rec.get("total") and rec["total"] != n):
        raise PieceRefused(409, f"{n:,} of the file's {max(chunks, n):,} pieces have arrived. The page sends the rest, "
                                "then finishes again.", n)
    if rec.get("sha256") and rec["sha256"] != sha256:
        raise PieceRefused(409, "The file's checksum is not the one it had when the upload started: the file changed while it "
                                "was sent. Start the upload again.")


def verify(pieces: Iterable[bytes], *, gz: bool, sha256: str, size: int, deadline: float | None = None) -> str | None:
    """Check a received file against the SHA-256 and size of the original, unpacking it on the way when it came
    compressed. None when it is the file, else what is wrong, in plain words. The file is never held in memory, and
    unpacking stops as soon as it holds more than the original did. TimeoutError past `deadline`."""
    h = hashlib.sha256()
    n = 0
    d = zlib.decompressobj(31) if gz else None
    try:
        for piece in pieces:
            if not piece:
                continue
            if d is None:
                h.update(piece)
                n += len(piece)
            else:
                if d.eof:
                    return "Something follows the end of the compressed file."
                buf = piece
                while buf:
                    out = d.decompress(buf, 8 << 20)
                    h.update(out)
                    n += len(out)
                    if n > size:
                        break
                    if d.eof:
                        if d.unused_data:
                            return "Something follows the end of the compressed file."
                        break
                    buf = d.unconsumed_tail
            if n > size:
                return "It holds more than the file the page read."
            if deadline is not None and time.time() > deadline:
                raise TimeoutError("the check ran out of time")
        if d is not None:
            out = d.flush()
            h.update(out)
            n += len(out)
            if not d.eof:
                return "The compressed file stops before its end."
    except zlib.error:
        return "The compressed file is damaged."
    if n != size:
        return f"It holds {n:,} bytes where the file has {size:,}."
    if h.hexdigest() != sha256:
        return "Its checksum is not the file's."
    return None


def _write_private(path: str, data: bytes) -> None:
    """Write a file owner-only and whole: into a hidden temporary file beside it, then renamed over it."""
    d, base = os.path.split(path)
    tmp = os.path.join(d, f".{base}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _append_at(path: str, offset: int, data: bytes) -> None:
    """Add a piece at `offset`: whatever a request cut off left past it is cut away first."""
    with open(path, "r+b") as fh:
        fh.truncate(offset)
        fh.seek(offset)
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())


def _file_pieces(path: str, piece: int = 1 << 20) -> Iterator[bytes]:
    with open(path, "rb") as fh:
        while True:
            b = fh.read(piece)
            if not b:
                return
            yield b


def _epoch(iso: str | None) -> float:
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _old(path: str, now: float, age: float) -> bool:
    try:
        return now - os.path.getmtime(path) > age
    except OSError:
        return False


class ServerUploads:
    """A server's uploads: the pieces in <data_dir>/incoming/<upload_id>.part, the records in incoming/sessions/, the
    finished file and its sidecar in incoming/ready/. Owner-only throughout. Two requests for one upload take turns
    (lock)."""
    serverless = False

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.root = os.path.join(data_dir(db_path), INCOMING)
        self.sessions = os.path.join(self.root, SESSIONS)
        self.ready = os.path.join(self.root, READY)
        self.done = os.path.join(self.root, DONE)

    def _dirs(self) -> None:
        for d in (self.root, self.sessions, self.ready):
            _private_dir(d)

    def _rec(self, uid: str) -> str:
        return os.path.join(self.sessions, uid + ".json")

    def part_path(self, uid: str) -> str:
        return os.path.join(self.root, uid + ".part")

    def ready_path(self, pair: str) -> str:
        return os.path.join(self.ready, pair)

    @contextmanager
    def lock(self, uid: str) -> Iterator[None]:
        self._dirs()
        try:
            import fcntl
        except ImportError:             # no file locks here: a single process takes the requests one at a time anyway
            yield
            return
        with open(os.path.join(self.sessions, uid + ".lock"), "a+b") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    def load(self, uid: str) -> tuple[dict | None, Any]:
        try:
            with open(self._rec(uid), "rb") as fh:
                return json.loads(fh.read()), None
        except (OSError, ValueError):
            return None, None

    def save(self, rec: dict, version: Any = None) -> Any:
        self._dirs()
        _write_private(self._rec(rec["upload_id"]), json.dumps(rec).encode())
        return None

    def create(self, rec: dict) -> None:
        self._dirs()
        os.close(os.open(self.part_path(rec["upload_id"]), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        self.save(rec)

    def records(self, recent: bool = False) -> list[dict]:
        """Every upload's record (`recent`: only those changed within SESSION_MAX_AGE, the ones that can still go on)."""
        out = []
        try:
            names = os.listdir(self.sessions)
        except OSError:
            return out
        now = time.time()
        for f in names:
            if f.endswith(".json") and UPLOAD_ID.fullmatch(f[:-5]):
                if recent and _old(os.path.join(self.sessions, f), now, SESSION_MAX_AGE):
                    continue
                rec, _v = self.load(f[:-5])
                if rec:
                    out.append(rec)
        return out

    def append(self, rec: dict, data: bytes) -> None:
        p = self.part_path(rec["upload_id"])
        if not os.path.exists(p):
            raise PieceRefused(410, "The pieces received so far are gone from the server. Start the upload again.")
        _append_at(p, rec["bytes"], data)

    def commit(self, rec: dict) -> None:
        """The received bytes become the intake file (no sidecar yet: the cycle does not touch it)."""
        _private_dir(self.ready)
        part, dest = self.part_path(rec["upload_id"]), self.ready_path(rec["pair"])
        if os.path.exists(part):
            if os.path.getsize(part) != rec["bytes"]:
                with open(part, "r+b") as fh:
                    fh.truncate(rec["bytes"])
            os.replace(part, dest)
            os.chmod(dest, 0o600)
        elif not os.path.exists(dest):
            raise PieceRefused(410, "The pieces received are gone from the server. Start the upload again.")

    def committed(self, rec: dict) -> Iterator[bytes]:
        return _file_pieces(self.ready_path(rec["pair"]))

    def discard(self, rec: dict) -> None:
        for p in (self.ready_path(rec["pair"]), self.part_path(rec["upload_id"])):
            try:
                os.remove(p)
            except OSError:
                pass

    def write_sidecar(self, rec: dict, sc: dict) -> None:
        _write_private(self.ready_path(rec["pair"]) + ".json", sidecar_bytes(sc))

    def pair_state(self, pair: str) -> tuple[str, dict | None]:
        """'waiting' (in ready/, for the cycle), 'done' (the cycle moved it to done/; its sidecar, with the result the cycle
        added) or 'gone'."""
        if os.path.exists(self.ready_path(pair) + ".json"):
            return "waiting", None
        try:
            with open(os.path.join(self.done, pair + ".json"), "rb") as fh:
                return "done", json.loads(fh.read())
        except (OSError, ValueError):
            return "gone", None

    def pending(self) -> list[dict]:
        """The sidecars of the complete pairs the cycle has not read yet."""
        out = []
        try:
            names = os.listdir(self.ready)
        except OSError:
            return out
        for f in names:
            if f.endswith(".json") and not f.startswith(".") and os.path.exists(os.path.join(self.ready, f[:-5])):
                try:
                    with open(os.path.join(self.ready, f), "rb") as fh:
                        out.append(dict(json.loads(fh.read()), pair=f[:-5]))
                except (OSError, ValueError):
                    pass
        return out

    def remove(self, uid: str) -> None:
        for p in (self._rec(uid), self.part_path(uid), os.path.join(self.sessions, uid + ".lock")):
            try:
                os.remove(p)
            except OSError:
                pass

    def sweep(self, now: float | None = None) -> dict:
        """Remove what uploads left behind: an upload unfinished for a day (its pieces and record), the record of a
        finished one after SESSION_KEEP, pieces and locks with no record, temporary files, and a finished file left
        without its sidecar for a day (a finish cut off part-way). Pairs waiting for the cycle are never touched."""
        now = now or time.time()
        out: Counter = Counter()
        live = set()
        for rec in self.records():
            age = now - _epoch(rec.get("updated_at"))
            if rec.get("state") == "receiving" and age > SESSION_MAX_AGE:
                self.remove(rec["upload_id"])
                out["abandoned"] += 1
            elif rec.get("state") != "receiving" and age > SESSION_KEEP:
                self.remove(rec["upload_id"])
                out["forgotten"] += 1
            else:
                live.add(rec["upload_id"])
        for folder, ext in ((self.root, ".part"), (self.sessions, ".lock")):
            try:
                names = os.listdir(folder)
            except OSError:
                continue
            for f in names:
                p = os.path.join(folder, f)
                if f.endswith(ext) and f[:-len(ext)] not in live and _old(p, now, SESSION_MAX_AGE):
                    os.remove(p)
                    out["leftover"] += 1
        for folder in (self.sessions, self.ready):
            try:
                names = os.listdir(folder)
            except OSError:
                continue
            for f in names:
                p = os.path.join(folder, f)
                if f.startswith(".") and f.endswith(".tmp") and _old(p, now, SESSION_MAX_AGE):
                    os.remove(p)
                    out["leftover"] += 1
                elif (folder == self.ready and not f.startswith(".") and not f.endswith(".json")
                      and not os.path.exists(p + ".json") and _old(p, now, SESSION_MAX_AGE)):
                    os.remove(p)
                    out["half_pair"] += 1
        return dict(out)


class _StateUploads:
    """What the two serverless flavours share: the records in the state folder (upload_sessions/<id>.json), saved only
    if nobody saved them meanwhile, and the intake folder (intake/) the next run reads."""
    serverless = True

    def __init__(self, state):
        self.state = state

    @staticmethod
    def _rec(uid: str) -> str:
        return f"{STATE_SESSIONS}/{uid}.json"

    @contextmanager
    def lock(self, uid: str) -> Iterator[None]:
        yield                           # nothing to share between instances but the state folder: saves are conditional

    def save(self, rec: dict, version: Any = None) -> Any:
        from .state import Conflict
        data = json.dumps(rec).encode()
        try:
            m = self.state.put(self._rec(rec["upload_id"]), data, mode="update" if version else "add", rev=version)
        except Conflict as exc:
            raise Stale(rec["upload_id"]) from exc
        return (m or {}).get("rev")

    def committed(self, rec: dict) -> Iterator[bytes]:
        chunks = self.state.get_chunks(f"{STATE_INTAKE}/{rec['pair']}")
        if chunks is None:
            raise PieceRefused(410, "The finished file is gone from the intake folder. Start the upload again.")
        return chunks

    def discard(self, rec: dict) -> None:
        self.state.delete(f"{STATE_INTAKE}/{rec['pair']}")

    def write_sidecar(self, rec: dict, sc: dict) -> None:
        from .state import Conflict
        try:
            self.state.put(f"{STATE_INTAKE}/{rec['pair']}.json", sidecar_bytes(sc), mode="add")
        except Conflict:
            pass                        # written by an earlier finish of this upload whose answer was lost

    def pair_state(self, pair: str) -> tuple[str, dict | None]:
        if self.state.meta(f"{STATE_INTAKE}/{pair}.json"):
            return "waiting", None
        raw = self.state.get(f"{STATE_INTAKE}/{DONE}/{pair}.json")
        if raw:
            try:
                return "done", json.loads(raw)
            except ValueError:
                pass
        return "gone", None

    def _sweep_records(self, now: float, out: Counter) -> set:
        live = set()
        for rec in self.records():
            age = now - _epoch(rec.get("updated_at"))
            if (rec.get("state") == "receiving" and age > SESSION_MAX_AGE) or (rec.get("state") != "receiving" and age > SESSION_KEEP):
                self.remove(rec["upload_id"])
                out["abandoned" if rec.get("state") == "receiving" else "forgotten"] += 1
            else:
                live.add(rec["upload_id"])
        return live


class LocalStateUploads(_StateUploads):
    """Serverless with a local state folder (REMITWATCH_STATE_DIR, for trying the serverless mode on one machine): the
    pieces in upload_sessions/<id>.part there, the finished file renamed into intake/."""

    def __init__(self, state):
        super().__init__(state)
        self.root = state.root

    def part_path(self, uid: str) -> str:
        return os.path.join(self.root, STATE_SESSIONS, uid + ".part")

    def _intake(self, *parts: str) -> str:
        return os.path.join(self.root, STATE_INTAKE, *parts)

    def load(self, uid: str) -> tuple[dict | None, Any]:
        m = self.state.meta(self._rec(uid))
        raw = self.state.get(self._rec(uid)) if m else None
        try:
            return (json.loads(raw), m["rev"]) if raw else (None, None)
        except ValueError:
            return None, None

    def create(self, rec: dict) -> None:
        _private_dir(os.path.join(self.root, STATE_SESSIONS))
        os.close(os.open(self.part_path(rec["upload_id"]), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        self.save(rec, None)

    def records(self, recent: bool = False) -> list[dict]:
        out = []
        folder = os.path.join(self.root, STATE_SESSIONS)
        try:
            names = os.listdir(folder)
        except OSError:
            return out
        now = time.time()
        for f in names:
            if f.endswith(".json") and UPLOAD_ID.fullmatch(f[:-5]):
                if recent and _old(os.path.join(folder, f), now, SESSION_MAX_AGE):
                    continue
                rec, _v = self.load(f[:-5])
                if rec:
                    out.append(rec)
        return out

    def append(self, rec: dict, data: bytes) -> None:
        p = self.part_path(rec["upload_id"])
        if not os.path.exists(p):
            raise PieceRefused(410, "The pieces received so far are gone. Start the upload again.")
        _append_at(p, rec["bytes"], data)

    def commit(self, rec: dict) -> None:
        _private_dir(self._intake())
        part, dest = self.part_path(rec["upload_id"]), self._intake(rec["pair"])
        if os.path.exists(part):
            if os.path.getsize(part) != rec["bytes"]:
                with open(part, "r+b") as fh:
                    fh.truncate(rec["bytes"])
            os.replace(part, dest)
            os.chmod(dest, 0o600)
        elif not os.path.exists(dest):
            raise PieceRefused(410, "The pieces received are gone. Start the upload again.")

    def write_sidecar(self, rec: dict, sc: dict) -> None:
        super().write_sidecar(rec, sc)
        try:
            os.chmod(self._intake(rec["pair"] + ".json"), 0o600)     # owner-only, as the file beside it
        except OSError:
            pass

    def pending(self) -> list[dict]:
        out = []
        try:
            names = os.listdir(self._intake())
        except OSError:
            return out
        for f in names:
            if f.endswith(".json") and not f.startswith(".") and os.path.exists(self._intake(f[:-5])):
                try:
                    with open(self._intake(f), "rb") as fh:
                        out.append(dict(json.loads(fh.read()), pair=f[:-5]))
                except (OSError, ValueError):
                    pass
        return out

    def remove(self, uid: str) -> None:
        self.state.delete(self._rec(uid))
        try:
            os.remove(self.part_path(uid))
        except OSError:
            pass

    def sweep(self, now: float | None = None) -> dict:
        now = now or time.time()
        out: Counter = Counter()
        live = self._sweep_records(now, out)
        folder = os.path.join(self.root, STATE_SESSIONS)
        for f in (os.listdir(folder) if os.path.isdir(folder) else []):
            p = os.path.join(folder, f)
            if f.endswith(".part") and f[:-5] not in live and _old(p, now, SESSION_MAX_AGE):
                os.remove(p)
                out["leftover"] += 1
        for f in (os.listdir(self._intake()) if os.path.isdir(self._intake()) else []):
            p = self._intake(f)
            if (os.path.isfile(p) and not f.startswith(".") and not f.endswith(".json") and not os.path.exists(p + ".json")
                    and _old(p, now, SESSION_MAX_AGE)):
                os.remove(p)
                out["half_pair"] += 1
        return dict(out)


def _correct_offset(text: str) -> int | None:
    m = re.search(r'"correct_offset"\s*:\s*(\d+)', text)
    return int(m.group(1)) if m else None


class DropboxStateUploads(_StateUploads):
    """Serverless with the state folder in Dropbox (Vercel): each piece is relayed at once to a Dropbox upload session
    (start, then append at the record's offset), and the session is committed into intake/ when the upload finishes.
    The record keeps the session's id and offset, so the next piece can reach any instance."""

    def __init__(self, state):
        super().__init__(state)
        self.client = state.client
        self.base = state.base

    def _path(self, name: str) -> str:
        return f"{self.base}/{name}"

    def load(self, uid: str) -> tuple[dict | None, Any]:
        from .dropbox_sync import DropboxNotFound
        try:
            data, meta = self.client.download_bytes(self._path(self._rec(uid)))
        except DropboxNotFound:
            return None, None
        try:
            return json.loads(data), (meta or {}).get("rev")
        except ValueError:
            return None, None

    def create(self, rec: dict) -> None:
        self.save(rec, None)

    def _list(self, name: str) -> list[dict]:
        try:
            return [e for e in self.client.list_folder(self._path(name), recursive=False) if e.get(".tag", "file") == "file"]
        except RuntimeError as exc:
            if "not_found" in str(exc):
                return []
            raise

    def records(self, recent: bool = False) -> list[dict]:
        """The records, read eight at a time (each is one Dropbox call)."""
        from concurrent.futures import ThreadPoolExecutor
        now = time.time()
        ids = [n[:-5] for e in self._list(STATE_SESSIONS) for n in [e.get("name") or ""]
               if n.endswith(".json") and UPLOAD_ID.fullmatch(n[:-5])
               and not (recent and now - _epoch(e.get("server_modified")) > SESSION_MAX_AGE)]
        if not ids:
            return []
        with ThreadPoolExecutor(max_workers=8) as ex:
            return [rec for rec, _v in ex.map(self.load, ids) if rec]

    def append(self, rec: dict, data: bytes) -> None:
        if not rec.get("session"):
            if rec["bytes"]:
                raise PieceRefused(409, "This upload lost its place in Dropbox. Start the upload again.")
            rec["session"] = self.client._content_call("files/upload_session/start", {"close": False}, data)["session_id"]
            return
        try:
            self.client._content_call("files/upload_session/append_v2",
                                      {"cursor": {"session_id": rec["session"], "offset": rec["bytes"]}, "close": False}, data)
        except RuntimeError as exc:
            text = str(exc)
            if _correct_offset(text) == rec["bytes"] + len(data):
                return                  # this very piece reached Dropbox before: a request whose answer was lost
            if "incorrect_offset" in text:
                raise PieceRefused(409, "This upload went out of step with Dropbox. Start the upload again.") from None
            if "not_found" in text or "closed" in text:
                raise PieceRefused(410, "This upload's place in Dropbox has expired. Start the upload again.") from None
            raise

    def commit(self, rec: dict) -> None:
        from .dropbox_sync import DropboxConflict
        name = f"{STATE_INTAKE}/{rec['pair']}"
        arg = {"cursor": {"session_id": rec["session"], "offset": rec["bytes"]},
               "commit": {"path": self._path(name), "mode": "add", "autorename": False, "mute": True}}
        try:
            self.client._content_call("files/upload_session/finish", arg, b"")
        except (DropboxConflict, RuntimeError):
            m = self.state.meta(name)
            if m and int(m.get("size") or -1) == rec["bytes"]:
                return                  # committed by an earlier finish whose answer was lost
            raise

    def pending(self) -> list[dict]:
        out = []
        entries = self._list(STATE_INTAKE)
        names = {e.get("name") for e in entries}
        for e in entries:
            n = e.get("name") or ""
            if n.endswith(".json") and not n.startswith(".") and n[:-5] in names:
                raw = self.state.get(f"{STATE_INTAKE}/{n}")
                try:
                    out.append(dict(json.loads(raw or b"{}"), pair=n[:-5]))
                except ValueError:
                    pass
        return out

    def remove(self, uid: str) -> None:
        self.state.delete(self._rec(uid))      # the Dropbox session itself expires on Dropbox's side within a week

    def sweep(self, now: float | None = None) -> dict:
        now = now or time.time()
        out: Counter = Counter()
        self._sweep_records(now, out)
        entries = self._list(STATE_INTAKE)
        names = {e.get("name") for e in entries}
        for e in entries:
            n = e.get("name") or ""
            if (n and not n.startswith(".") and not n.endswith(".json") and f"{n}.json" not in names
                    and now - _epoch(e.get("server_modified")) > SESSION_MAX_AGE):
                self.state.delete(f"{STATE_INTAKE}/{n}")
                out["half_pair"] += 1
        return dict(out)


def state_uploads(state) -> _StateUploads:
    """The uploads of a serverless host, for its state folder."""
    from .state import LocalState
    if isinstance(state, LocalState):
        return LocalStateUploads(state)
    if hasattr(state, "client") and hasattr(state, "base"):
        return DropboxStateUploads(state)
    raise RuntimeError("This state folder cannot take uploads.")


def kick_intake(db_path: str) -> bool:
    """Ask the cycle to read the intake folder soon (remitwatch.intake.kick, under the cycle's lock). False when this
    build has no intake yet: the pair waits for the next cycle."""
    try:
        from . import intake            # type: ignore[attr-defined]
    except ImportError:
        return False
    kick = getattr(intake, "kick", None)
    if not callable(kick):
        return False
    try:
        kick(data_dir(db_path))
        return True
    except Exception:
        return False


# ------------------------------------------------------------------------- what RemitWatch already has, and what a file became
REPORT_TITLES = {"ar_report": "A/R report", "production": "Production (collections) report",
                 "charge_lines": "Encounter & Charge Lines", "era_lines": "ERA & Payment Line Adjustments",
                 "claim_status": "Claim & Claim Status"}
_READ_STATUSES = ("current", "partly_replaced", "replaced", "superseded")


def _has(conn: sqlite3.Connection | None, table: str) -> bool:
    try:
        return conn is not None and conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None
    except sqlite3.Error:
        return False


def _catalog(conn: sqlite3.Connection | None, sha: str) -> dict | None:
    if not _has(conn, "ema_report_files"):
        return None
    cur = conn.execute("SELECT * FROM ema_report_files WHERE file_id=?", (sha,))
    r = cur.fetchone()
    return dict(zip([d[0] for d in cur.description], r)) if r else None


def known_file(conn: sqlite3.Connection | None, sha256: str, pending: Iterable[dict] = (), skip_pair: str | None = None) -> dict | None:
    """What RemitWatch did with a file of exactly these bytes before, if it has one: a report read, held, refused or
    waiting (the report catalog), an export or a remittance read (files), a file uploaded and waiting for the cycle
    (the uploads table, or a pair in the intake folder). None when the file is new. 'line' says it in plain words."""
    row = _catalog(conn, sha256)
    if row:
        st, why = row.get("status"), (row.get("status_reason") or "").strip()
        when = _day(row.get("applied_at") or row.get("received_at"))
        if st in _READ_STATUSES or st == "discarded":
            line = f"Already have this; nothing changed. RemitWatch read this exact file on {when}."
        elif st == "held":
            line = f"Already have this, held: {why} Apply or Discard it on the Data tab."
        elif st == "refused":
            line = f"This exact file was read before and not taken: {why} Export it again from EMA."
        else:
            line = f"Already have this; it waits to be read: {why}".strip()
        return {"how": "report", "status": st, "at": row.get("applied_at") or row.get("received_at"), "line": line}
    if _has(conn, "files"):
        r = conn.execute("SELECT kind, ingested_at FROM files WHERE sha256=?", (sha256,)).fetchone()
        if r:
            return {"how": "read", "status": "done", "at": r[1],
                    "line": f"Already have this; nothing changed. RemitWatch read this exact file on {_day(r[1])}."}
    if _has(conn, "uploads"):
        r = conn.execute("SELECT status, uploaded_at FROM uploads WHERE upload_id=? AND status IN ('queued','ready','done')",
                         (sha256,)).fetchone()
        if r:
            return {"how": "upload", "status": r[0], "at": r[1],
                    "line": f"Already have this; nothing changed. This exact file was uploaded on {_day(r[1])}"
                            + (" and waits for the next cycle." if r[0] in ("queued", "ready") else ".")}
    for sc in pending:
        if sc.get("sha256") == sha256 and sc.get("pair") != skip_pair:
            return {"how": "upload", "status": "waiting", "at": sc.get("received_at"),
                    "line": f"Already have this; nothing changed. This exact file was uploaded on {_day(sc.get('received_at'))} "
                            "and waits for the next cycle."}
    return None


def _scope_words(row: dict) -> str:
    if row.get("report") == "ar_report":
        return f" as of {_day(row['as_of'])}" if row.get("as_of") else ""
    if row.get("scope") == "posted" and row.get("posted_lo"):
        return f" of postings from {_day(row['posted_lo'])} to {_day(row['posted_hi'])}"
    if row.get("svc_lo"):
        return f" of services from {_day(row['svc_lo'])} to {_day(row['svc_hi'])}"
    return ""


def _iso_day(v: Any) -> date | None:
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def upload_checks(row: dict, today: date | None = None) -> list[dict]:
    """The checks a person reads once a report is applied, from its catalog row: is the A/R the 30-day window only, do
    the dates cover what they should, is it fresh. (A file that looks filtered is held by the reader or the ledger, and
    its hold says so.)"""
    out: list[dict] = []
    try:
        acct = json.loads(row.get("account") or "{}") if isinstance(row.get("account"), str) else (row.get("account") or {})
    except ValueError:
        acct = {}
    came = _iso_day(row.get("received_at")) or today or date.today()
    if row.get("report") == "ar_report":
        if acct.get("aging_window") == 30:
            out.append({"code": "ar_30_days", "message": "This A/R export lists only lines with activity in the last 30 days, "
                        "denied lines and lines with no aging date. Ask EMA support for the A/R report with every aging bucket "
                        "(or a Total A/R column) and no 30-day limit, so RemitWatch sees every open balance."})
        as_of = _iso_day(row.get("as_of"))
        if as_of and (came - as_of).days > 8:
            out.append({"code": "old", "message": f"It was pulled on {_day(row['as_of'])}, {(came - as_of).days} days before it "
                        "arrived. Pull a fresh A/R every Monday."})
    elif row.get("report") == "production":
        lo, hi = (_iso_day(row.get("posted_lo")), _iso_day(row.get("posted_hi"))) if row.get("scope") == "posted" else \
            (_iso_day(row.get("svc_lo")), _iso_day(row.get("svc_hi")))
        posted_hi = _iso_day(row.get("posted_hi"))
        if row.get("scope") == "posted" and lo and hi and (hi - lo).days + 1 < 28:
            out.append({"code": "short_range", "message": f"It holds {(hi - lo).days + 1} days of postings ({_day(row['posted_lo'])} "
                        f"to {_day(row['posted_hi'])}). Pull the last five weeks by Posted Date, so postings made late are not missed."})
        if row.get("scope") == "service" and lo and hi and lo.year == hi.year and (lo.month, lo.day) > (1, 15):
            out.append({"code": "starts_late", "message": f"Its services start on {_day(row['svc_lo'])}. A service-year export "
                        "runs from January 1; pull the whole year, or pull by Posted Date."})
        if posted_hi and (came - posted_hi).days > 7:
            out.append({"code": "ends_early", "message": f"Its newest posting is from {_day(row['posted_hi'])}, "
                        f"{(came - posted_hi).days} days before it arrived. Pull through yesterday."})
    return out


def report_outcome(conn: sqlite3.Connection | None, sha256: str, today: date | None = None) -> dict | None:
    """What became of a report RemitWatch read from these bytes, from the report catalog: the plain lines a person
    reads (what it was recognised as, its scope, rows, the boxes it replaced, a hold) and the checks (upload_checks).
    None when the catalog has no such file."""
    row = _catalog(conn, sha256)
    if not row:
        return None
    title = REPORT_TITLES.get(row.get("report"), "EMA report")
    st, why = row.get("status"), (row.get("status_reason") or "").strip()
    lines: list[str] = []
    read = int(row.get("rows_read") or 0)
    kept = row.get("rows_applied") if row.get("rows_applied") is not None else row.get("rows_kept")
    if st in _READ_STATUSES:
        lines.append(f"Read on {_day(row.get('applied_at'))} as the {title}{_scope_words(row)}: {_n(read, 'row')}"
                     + (f", {int(kept):,} kept." if kept is not None else "."))
        for r in conn.execute("SELECT report, scope, svc_lo, svc_hi, posted_lo, posted_hi, as_of, status FROM ema_report_files "
                              "WHERE replaced_by=? AND file_id<>? ORDER BY as_of", (sha256, sha256)):
            old = dict(zip(("report", "scope", "svc_lo", "svc_hi", "posted_lo", "posted_hi", "as_of", "status"), r))
            lines.append(f"It {'replaces' if old['status'] == 'replaced' else 'replaces part of'} the export{_scope_words(old)}"
                         + (f" (as of {_day(old['as_of'])})" if old.get("as_of") and old["report"] != "ar_report" else "") + ".")
        if st in ("replaced", "superseded"):
            lines.append("A newer export has replaced it since.")
    elif st == "held":
        lines.append(f"Recognised as the {title}{_scope_words(row)}, and held: {why} Apply or Discard it on the Data tab.")
    elif st == "refused":
        lines.append(f"Recognised as the {title}, and not read: {why}")
    elif st == "discarded":
        lines.append(f"Recognised as the {title}; it was held, and a person discarded it.")
    else:
        lines.append(f"Recognised as the {title}, kept and not read yet: {why}".strip())
    return {"status": st, "report": row.get("report"), "title": title, "lines": lines,
            "checks": upload_checks(row, today) if st in _READ_STATUSES or st == "held" else []}


def record_view(rec: dict, *, conn: sqlite3.Connection | None = None, pair: tuple[str, dict | None] | None = None,
                reveal: bool = False, pieces: bool = False) -> dict:
    """An upload as the page shows it: where it stands, and the plain lines about it (what arrived, what the cycle made of
    it). `pieces` adds the SHA-256 of every piece received, for the page to carry on from the first missing one."""
    out = {k: rec.get(k) for k in ("upload_id", "size", "gz", "report_hint", "state", "bytes", "total", "created_at",
                                   "updated_at", "finished_at")}
    out["name"] = shown_name(rec.get("name") or "", "ema" if rec.get("report_hint") else "file", rec.get("upload_id"), reveal)
    out["received"] = len(rec.get("chunks") or [])
    out["chunk_bytes"] = CHUNK_BYTES
    if pieces:
        out["chunks"] = list(rec.get("chunks") or [])
    lines: list[str] = []
    checks: list[dict] = []
    applied = None
    st = rec.get("state")
    if st == "receiving":
        got = out["received"]
        lines.append(f"{_n(got, 'piece')} received ({_size_words(rec.get('bytes') or 0)}). Choose the same file again to carry on."
                     if got else "Nothing received yet. Choose the same file again to send it.")
    elif st in ("already", "refused"):
        lines.append(rec.get("message") or ("Already have this; nothing changed." if st == "already" else "Not taken."))
    else:
        where, sc = pair or ("waiting", None)
        res = report_outcome(conn, rec.get("sha256") or "") if conn is not None else None
        if res:
            lines += res["lines"]
            checks = res["checks"]
            applied = res["status"]
        elif where == "done" and isinstance((sc or {}).get("result"), dict):
            r = sc["result"]
            lines += [str(x) for x in (r.get("lines") or [])][:12] or [f"Read: {r.get('status') or 'done'}."]
            applied = r.get("status")
        elif where == "done" or (where == "gone" and conn is not None and known_file(conn, rec.get("sha256") or "")):
            seen = known_file(conn, rec.get("sha256") or "") if conn is not None else None
            lines.append(f"Read on {_day(seen['at'])}." if seen and seen.get("at") else "Read by the cycle.")
            applied = "read"
        else:
            from . import runtime
            sent = f" (sent compressed: {_size_words(rec.get('bytes') or 0)})" if rec.get("gz") else ""
            lines.append(f"Received and checked on {_day(rec.get('finished_at'))}: {_size_words(rec.get('size') or 0)}{sent}. "
                         f"It waits for the next {'run' if runtime.serverless() else 'cycle'} to read it.")
    out.update(lines=lines, checks=checks, applied=applied)
    return out


# =========================================================================================== the five EMA exports (checklist)
# What the practice pulls from EMA every week, row by row as the Upload page shows them: the report's name as EMA titles
# it, what it is for, the filters, the columns it must have (checked when it is dropped), the format, how often, when it
# last arrived and whether it is overdue, and the practice's own "How we pull this in our EMA" box. EMA's menus are never
# described here: the box holds the steps the practice writes down itself.
EXPORTS: tuple[dict, ...] = (
    {"key": "ar", "report": "ar_report", "title": "A/R report", "overdue_days": 8,
     "purpose": "Every open balance, insurance and patient, with its claim, status and denial amount. RemitWatch uses it to see "
                "what is still owed, what was denied and what never went out.",
     "filters": ["No filter: every payer, provider, location and biller, insurance and patient balances.",
                 "Every aging bucket, or a Total A/R column, when EMA offers it."],
     "columns_note": "All 22 columns.", "format": "Excel (.xlsx) or CSV.", "cadence": "Every week, on Monday."},
    {"key": "production", "report": "production", "title": "Production (collections) report", "overdue_days": 14,
     "purpose": "Every charge, payment and adjustment EMA posted. RemitWatch uses it to see what EMA paid, wrote off or never "
                "got an answer for, including payments that came with no remittance.",
     "filters": ["Posted Date: the last five weeks, through yesterday.",
                 "No other filter: not payer, provider, location or biller.",
                 "The whole service year only to catch up."],
     "columns_note": "All 39 columns.", "format": "CSV. Any size: this page compresses it and sends it in pieces.",
     "cadence": "Every week, on Monday."},
    {"key": "charge_lines", "family": "charge_lines", "title": "Encounter & Charge Lines", "overdue_days": 8,
     "purpose": "Each charge with its claim ID, bill, modifiers and the patient's EMA ID. RemitWatch uses it to tie each charge "
                "to its remittance and to find the visit note.",
     "filters": ["The full layout.",
                 "By posting or transaction date, the last five weeks, where EMA allows it. Otherwise by service date, the last six weeks."],
     "columns_note": "", "format": "Excel (.xlsx) or CSV.", "cadence": "Every week, on Monday."},
    {"key": "era_lines", "family": "era_lines", "title": "ERA & Payment Line Adjustments", "overdue_days": 8,
     "purpose": "What was done with each adjustment on a remittance (written off, moved to the patient). RemitWatch uses it to "
                "tell a write-off EMA's posting rule made from one a person made.",
     "filters": ["By posting or transaction date, the last five weeks, where EMA allows it. Otherwise by service date, the last six weeks."],
     "columns_note": "", "format": "Excel (.xlsx) or CSV.", "cadence": "Every week, on Monday."},
    {"key": "claim_status", "family": "claim_status", "title": "Claim & Claim Status", "overdue_days": 8,
     "purpose": "Where each claim stands, with its denial amount and reasons. Add the Patient ID column: a claim EMA shows as "
                "rejected or still waiting has no remittance, so nothing else says whose it is.",
     "filters": ["With the Patient ID column added.",
                 "By posting or transaction date, the last five weeks, where EMA allows it. Otherwise by service date, the last six weeks."],
     "columns_note": "", "format": "Excel (.xlsx) or CSV.", "cadence": "Every week, on Monday."},
)
EXPORT_KEYS = tuple(e["key"] for e in EXPORTS)
HOWTO_META = "ema:howto"                # the boxes, on a server: the store's meta table
HOWTO_STATE = "ema_howto.json"          # ... on a serverless host: the state folder (the web process cannot write the store)
HOWTO_MAX = 4000                        # characters in one box
HOWTO_HISTORY = 50                      # earlier versions kept per box
NAME_MAX = 60


def export_columns(e: dict) -> list[dict]:
    """The columns an export must have, as EMA writes them, each with why RemitWatch needs it."""
    from . import ema_reports as er
    if e.get("report"):
        return [{"column": er.WHY.get(n, (n, ""))[0], "why": er.WHY.get(n, (n, ""))[1], "key": n}
                for n in er.LAYOUTS[e["report"]]["required"]]
    return [{"column": label, "why": why, "key": key} for key, label, why in WANT.get(e["family"], [])]


def recognition() -> dict:
    """What the page needs to tell the exports apart from their row of column names before it sends anything (the rules
    of ema_reports.recognize and ema_exports.detect_family): the reports' core and required names, every name either
    reads, and the weekly exports' names with what each stands for."""
    from . import ema_reports as er
    return {"header_scan": er.HEADER_SCAN, "peek_bytes": er.PEEK_BYTES,
            "reports": {r: {"core": list(er.LAYOUTS[r]["core"]), "required": list(er.LAYOUTS[r]["required"]),
                            "optional": list(er.LAYOUTS[r]["optional"])} for r in er.REPORTS},
            "report_names": sorted(er.KNOWN_NAMES), "canon": dict(ema_exports.CANON), "key_columns": list(ema_exports.KEY_COLUMNS),
            "want": {f: [k for k, _l, _w in v] for f, v in WANT.items()}}


def arrivals(conn: sqlite3.Connection | None) -> dict:
    """When each export last arrived and how fresh its data is: {key: {'arrived', 'as_of', 'via', 'status'}}. The reports
    from the report catalog, the weekly exports from the files RemitWatch read."""
    out: dict = {}
    if _has(conn, "ema_report_files"):
        for e in EXPORTS:
            if not e.get("report"):
                continue
            last = conn.execute("""SELECT received_at, via, status FROM ema_report_files WHERE report=? AND status NOT IN ('refused','discarded')
                                   ORDER BY received_at DESC LIMIT 1""", (e["report"],)).fetchone()
            as_of = conn.execute("""SELECT MAX(as_of) FROM ema_report_files WHERE report=? AND status IN ('current','partly_replaced')""",
                                 (e["report"],)).fetchone()[0]
            if last or as_of:
                out[e["key"]] = {"arrived": last[0] if last else None, "via": last[1] if last else None,
                                 "status": last[2] if last else None, "as_of": as_of}
    if _has(conn, "files"):
        for e in EXPORTS:
            if not e.get("family"):
                continue
            r = conn.execute("SELECT MAX(ingested_at) FROM files WHERE kind=?", (f"ema:{e['family']}",)).fetchone()
            if r and r[0]:
                out[e["key"]] = {"arrived": r[0], "via": None, "status": "read", "as_of": None}
    return out


def _howto_public(box: dict | None) -> dict:
    box = box or {}
    return {"text": box.get("text") or "", "by": box.get("by"), "name": box.get("name"), "at": box.get("at"),
            "versions": len(box.get("history") or [])}


def exports_overview(conn: sqlite3.Connection | None, howto: dict | None = None, today: date | None = None) -> list[dict]:
    """The five rows of the checklist (GET /api/ema/exports)."""
    today = today or datetime.now(timezone.utc).date()
    seen = arrivals(conn)
    howto = howto or {}
    rows = []
    for e in EXPORTS:
        a = seen.get(e["key"]) or {}
        fresh = _iso_day(a.get("as_of")) or _iso_day(a.get("arrived"))
        age = (today - fresh).days if fresh else None
        overdue = age is None or age > e["overdue_days"]
        box = howto.get(e["key"]) or {}
        if a:
            via = {"upload": "uploaded here", "dropbox": "from the Dropbox folder", "mirror": "from the Dropbox folder",
                   "cli": "read on the server"}.get(a.get("via") or "", "")
            last = f"Last arrived {_day(a.get('arrived'))}{' (' + via + ')' if via else ''}" if a.get("arrived") else "Arrival not recorded"
            if a.get("as_of"):
                last += f"; its data runs to {_day(a['as_of'])}"
            last += "."
        else:
            last = "Has not arrived yet."
        if overdue:
            state = (f"Overdue: the newest is {age} days old, and it is due every {e['overdue_days']} days at most. Pull it now."
                     if age is not None else "Overdue: RemitWatch has none yet. Pull it now.")
        else:
            state = f"Up to date: the newest is {_n(age, 'day')} old."
        ask = None if (box.get("text") or "").strip() else (
            "Nobody has written down yet how to pull this in our EMA. Ask the person who last pulled it"
            + (f" (it last arrived {_day(a.get('arrived'))})" if a.get("arrived") else "") + " to write the steps here.")
        rows.append({"key": e["key"], "title": e["title"], "report": e.get("report"), "family": e.get("family"),
                     "purpose": e["purpose"], "filters": list(e["filters"]), "columns_note": e["columns_note"],
                     "columns": export_columns(e), "format": e["format"], "cadence": e["cadence"],
                     "overdue_days": e["overdue_days"], "arrived": a.get("arrived"), "as_of": a.get("as_of"), "via": a.get("via"),
                     "age_days": age, "overdue": overdue, "last_line": last, "state_line": state,
                     "howto": _howto_public(box), "ask": ask})
    return rows


def _clean_text(text: Any, limit: int, *, one_line: bool = False) -> str:
    s = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    s = "".join(c for c in s if c in "\n\t" or ord(c) >= 32 and ord(c) != 127)
    if one_line:
        s = " ".join(s.split())
    else:
        s = "\n".join(line.rstrip() for line in s.split("\n")).strip("\n")
    if len(s) > limit:
        raise PieceRefused(413, f"This box holds at most {limit:,} characters.")
    return s


def howto_edit(doc: dict, key: str, text: Any, user: str, name: Any = None, base_at: str | None = None,
               now: str | None = None) -> dict:
    """The boxes after one edit: the new text with who saved it and when, the version it replaces kept in the box's
    history (the newest HOWTO_HISTORY). `base_at` is when the version the person started from was saved ('' for an
    empty box): when someone saved the box since, nothing is saved (409) and the page shows their text."""
    if key not in EXPORT_KEYS:
        raise PieceRefused(404, "There is no such export.")
    text = _clean_text(text, HOWTO_MAX)
    name = _clean_text(name, NAME_MAX, one_line=True) or None
    cur = dict(doc.get(key) or {})
    if base_at is not None and (cur.get("at") or "") != str(base_at):
        raise PieceRefused(409, "Someone saved this box after you opened it. Their text is shown now: add yours to it and save again.")
    if text == (cur.get("text") or "") and cur.get("at"):
        return doc
    hist = list(cur.get("history") or [])
    if cur.get("at"):
        hist.insert(0, {"text": cur.get("text") or "", "by": cur.get("by"), "name": cur.get("name"), "at": cur["at"]})
    out = dict(doc)
    at = now or datetime.now(timezone.utc).isoformat(timespec="milliseconds")      # two saves within a second still differ
    out[key] = {"text": text, "by": user or "", "name": name, "at": at, "history": hist[:HOWTO_HISTORY]}
    return out


def howto_read_conn(conn: sqlite3.Connection | None) -> dict:
    if not _has(conn, "meta"):
        return {}
    r = conn.execute("SELECT value FROM meta WHERE key=?", (HOWTO_META,)).fetchone()
    try:
        doc = json.loads(r[0]) if r and r[0] else {}
    except ValueError:
        doc = {}
    return doc if isinstance(doc, dict) else {}


def howto_save_server(db_path: str, key: str, text: Any, user: str, name: Any = None, base_at: str | None = None) -> dict:
    """Save a box on a server: in the store's meta table, read and written in one transaction."""
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        doc = howto_edit(howto_read_conn(conn), key, text, user, name, base_at)
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (HOWTO_META, json.dumps(doc)))
        conn.commit()
        return doc
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def howto_read_state(state) -> dict | None:
    """The boxes a serverless host keeps in its state folder; None when it has none yet."""
    raw = state.get(HOWTO_STATE)
    if not raw:
        return None
    try:
        doc = json.loads(raw)
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None


def howto_save_state(state, key: str, text: Any, user: str, name: Any = None, base_at: str | None = None,
                     fallback: dict | None = None) -> dict:
    """Save a box on a serverless host: the state folder's ema_howto.json, replaced only if nobody replaced it since it
    was read (tried again a few times when two people save at once). `fallback`: the boxes the store's meta carries,
    for a folder that has no file yet."""
    from .state import Conflict
    for _ in range(4):
        m = state.meta(HOWTO_STATE)
        doc = (howto_read_state(state) if m else None)
        if doc is None:
            doc = dict(fallback or {})
        new = howto_edit(doc, key, text, user, name, base_at)
        try:
            state.put(HOWTO_STATE, json.dumps(new).encode(), mode="update" if m else "add", rev=m["rev"] if m else None)
            return new
        except Conflict:
            continue
    raise PieceRefused(409, "The box could not be saved just now: someone else was saving at the same time. Try again.")
