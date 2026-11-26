"""
EMA's A/R report and Production (collections) report: recognition, streaming readers, keys, A/R snapshots, the report
catalog, ingest, preview, held-file decisions, and the clean-up of report rows that older code read as an export.

The practice pulls two reports from ModMed EMA beside the three weekly exports (remitwatch.ema_exports):

  A/R report         one row per open claim line (an A/R item): who owes it, the claim id, the open amount, the denial
                     amount, EMA's claim status and the latest billing note. About 10,000 rows. Each file becomes the
                     current snapshot of the service years it spans (ema_ar), with a history per item (ema_ar_items).
  Production report  one row per posting (a charge, a payment or an adjustment): 400,000 to 520,000 rows a year, 160 to
                     206 MB as CSV. Streamed row by row (iter_production) into the posting ledger (remitwatch.ema_ledger),
                     never into the store and never held in memory.

Each report is told apart by its exact column names (recognize), before the weekly exports' family test runs, so no
caller can read one as an export again. Names, dates of birth, record numbers and billing notes never leave the reader:
they become keyed hashes (name_key, person_key, mrn_key, note_hash) made with a random salt kept in the store's meta table
(ema:key_salt), which is never copied to the dashboard. The claim link's address is dropped at once: only EMA's claim
number is kept, plus one validated address prefix, so "Open in EMA" can link to a claim.

The A/R export is a window, not the whole A/R: it lists the lines with activity in the last 30 days, the denied lines of
any age, and lines with no aging date. Nothing here reads absence from it as paid, closed or settled.

Who writes what (the store tables are defined here, in DDL): this module writes ema_report_files, the files and
ema_sources rows of reports, ema_ar, ema_ar_items and ema_claim_links. The ledger module writes the posting summaries
and the claim-link map; the checks module writes causes and findings.
"""
from __future__ import annotations

import codecs
import csv
import gzip
import hashlib
import hmac
import io
import itertools
import json
import math
import os
import re
import secrets
import sqlite3
import time
import unicodedata
import zipfile
import zlib
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from typing import Any, BinaryIO, ContextManager, Iterable, Iterator, NamedTuple, Sequence

from . import codes, runlog, runtime
from .ema_exports import NA_TEXT, TOTAL_LINE, export_created

# --------------------------------------------------------------------------------------------------------- constants
REPORTS = ("production", "ar_report")
HEADER_SCAN = 25                    # rows searched for the column names (a title and filter lines can sit above them)
PEEK_BYTES = 65536                  # what recognition reads of a text file
CLAIM_HOST_SUFFIX = "ema.md"        # the vendor's public domain the claim-link prefix must sit under (tests set "ema.test")
VA_PAYER_IDS = frozenset({"VACCN", "TWVACCN"})
FLAG_PRODUCT, FLAG_POSTED_BEFORE_SERVICE, FLAG_DAMAGED_CHECK, FLAG_IDENTITY_BREAK, FLAG_PATIENT_SIDE = 1, 2, 4, 8, 16
MAX_ROWS = 3_000_000                # a report with more rows is refused
ORDER_SAMPLES = 5000                # date values read before a file's date order is decided (and at most this many rows)
EARLIEST_DATE = "2000-01-01"        # no posting, aging or service date before this is real
POSTED_AHEAD_DAYS = 1               # a posted or aging date may be at most this many days after the day the file is read
SERVICE_AHEAD_DAYS = 31             # a service date may be at most this many days after it (a visit booked ahead)
FORMAT_HOLD_SHARE = 0.01            # an A/R file is held when more than 1% of its rows (and more than 20) are left out
FORMAT_HOLD_MIN = 20                #   for a date that cannot be read or cells that do not line up with the column names
H1_NO_DATE_SHARE = 0.01             # Production H1: rows left out for their date or their cells, or cut short
H1_BREAK_SHARE = 0.005              #   ... or rows breaking EMA's own money totals
H7_DAMAGED_MIN, H7_DAMAGED_SHARE = 10, 0.005     # Production H7: check numbers a spreadsheet changed
DEADLINE_EVERY = 10_000             # a reader checks its deadline this often (rows)
XLSX_MEMBER_MAX = 64 * 1024 * 1024  # an .xlsx inside a zip is read into memory; larger ones are refused
# What a packed file (a gzip, a zip member, a workbook's parts) may unpack to: EMA's text compresses about 14 to 1 and its
# workbooks about 10 to 1, so a file that unpacks to far more is a damaged or hostile file (a gzip or zip bomb), refused as
# it is read and never held whole. A small file may always unpack to the floor; no file unpacks to more than the cap
# (MAX_ROWS rows of a report are well under it).
UNPACK_RATIO_MAX = 200
UNPACK_FLOOR = 64 * 1024 * 1024
UNPACK_MAX = 2 * 1024 * 1024 * 1024
LINE_MAX = 1024 * 1024              # one line of a report or export: about 400 bytes, 621 at most in the real files
TEXT_MEMBERS = (".csv", ".tsv", ".txt")
REPORT_MEMBERS = TEXT_MEMBERS + (".xlsx",)
AR_HISTORY_DAYS = 120               # A/R items absent this long are dropped from the history
CLAIM_LINK_DAYS = 1100              # claim id to claim number pairs are kept this long after they were last seen
AR_HOLD_MIN_ROWS = 500              # the shrink hold compares snapshots only when this many rows must still be listed
AR_HOLD_MAX_GAP = 10                # ... and the current snapshot is at most this many days older
AR_HOLD_SHARE = 0.40                # a re-export listing fewer of those rows than this is held
AR_HOLD_VALUE_SHARE = 0.05          # a party, coverage or payer holding this share of them must still appear
VA_GROUP = ("VA Community Care", "va")
REPORT_NAMES = {"production": "Production report", "ar_report": "A/R report"}
REPORT_KINDS = ("ema:ar", "ema:production")      # the files.kind of a report that was read as one
CATALOG_STATUSES = ("waiting", "current", "partly_replaced", "replaced", "superseded", "held", "refused", "discarded", "reread")
DONE_STATUSES = ("current", "partly_replaced", "replaced", "superseded")
ZIP_OTHERS_MAX = 20000                  # members of a zip handed to the router (a year of remittances is a few thousand)

META_SALT = "ema:key_salt"
META_PREFIX = "ema:claim_url_prefix"
META_ALIASES = "ema:payer_aliases_learned"
META_UNKNOWN_STATUS = "ema:unknown_status_words"
META_MIGRATED = "ema:migrated_v1"
META_SUSPECTS = "ema:misread_suspects"
META_MAP_DIRTY = "ema:map_dirty"

_NA = NA_TEXT


class ReportRefused(ValueError):
    """A report RemitWatch will not read, with a code and a message in plain words. The message never holds a cell value.

    Codes: missing_columns, duplicate_column, two_hosts, two_date_orders, too_many_rows, zip_in_zip, unreadable, empty,
    unpacks_too_large (a gzip or zip that unpacks to far more than any export), long_line (one line over LINE_MAX)."""

    def __init__(self, code: str, message: str, report: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.report = report


class ReadTimeout(RuntimeError):
    """A reader passed its deadline (checked every DEADLINE_EVERY rows)."""


SIZE_CODES = ("unpacks_too_large", "long_line")      # refusals for a file's size or shape: the file is set aside, never re-read


def unpack_limit(packed: int | None) -> int:
    """The most a packed file of `packed` bytes may unpack to (UNPACK_RATIO_MAX to one, at least UNPACK_FLOOR, never more
    than UNPACK_MAX); UNPACK_MAX when its size is not known."""
    if packed is None or packed < 0:
        return UNPACK_MAX
    return min(UNPACK_MAX, max(UNPACK_FLOOR, UNPACK_RATIO_MAX * int(packed)))


def _mb(n: int) -> str:
    return f"{max(1, round(n / 1048576)):,} MB"


def unpack_refusal(limit: int, packed: int | None = None, report: str | None = None) -> ReportRefused:
    """The refusal of a file that unpacks to more than `limit`: in plain words, what was seen and what to do."""
    what = f" from a file of {_mb(packed)}" if packed else ""
    return ReportRefused("unpacks_too_large", f"It unpacks to more than {_mb(limit)}{what}, far beyond any export EMA makes, so it "
                         "was set aside without being read. Export it again from EMA (a damaged or wrongly saved file does "
                         "this).", report)


def long_line_refusal(line_no: int, report: str | None = None) -> ReportRefused:
    return ReportRefused("long_line", f"Line {line_no:,} is longer than {LINE_MAX // 1048576} MB, which no export EMA makes "
                         "has, so the file was set aside without being read. Export it again from EMA.", report)


class Capped(io.RawIOBase):
    """A read-only stream over another that gives at most `limit` bytes: one more raises the unpacks_too_large refusal
    (a gzip or zip bomb is stopped as it is read; nothing is held but the piece being read)."""

    def __init__(self, raw: Any, limit: int, *, packed: int | None = None, report: str | None = None):
        super().__init__()
        self.raw, self.limit, self.packed, self.report = raw, int(limit), packed, report
        self.n = 0

    def readable(self) -> bool:
        return True

    def readinto(self, b: Any) -> int:
        want = len(b)
        data = self.raw.read(want) if not hasattr(self.raw, "readinto") else None
        if data is None:
            n = self.raw.readinto(b)
            n = n or 0
        else:
            n = len(data)
            b[:n] = data
        self.n += n
        if self.n > self.limit:
            raise unpack_refusal(self.limit, self.packed, self.report)
        return n


def capped(raw: Any, limit: int, **kw: Any) -> io.BufferedReader:
    """`raw` behind Capped, buffered (what a TextIOWrapper or a reader wants)."""
    return io.BufferedReader(Capped(raw, limit, **kw), 1 << 20)


def zip_refusal(src: Any, packed: int | None = None, report: str | None = None) -> ReportRefused | None:
    """A zip (a workbook is one) whose members say they unpack to far more than any export: the refusal, else None. Read
    from the zip's own directory, before a member is opened (Python's zip reader never gives more than a member's stated
    size, so the stated sizes bound what reading it costs)."""
    try:
        with zipfile.ZipFile(_zip_source(src)) as zf:
            infos = zf.infolist()
    except (zipfile.BadZipFile, OSError, ValueError):
        return None                     # not a zip this reader can open: the readers say so in their own words
    if packed is None:
        packed = _source_size(src)
    total = 0
    for i in infos:
        total += i.file_size
        if i.file_size > unpack_limit(i.compress_size):
            return unpack_refusal(unpack_limit(i.compress_size), i.compress_size, report)
    limit = unpack_limit(packed)
    if total > limit:
        return unpack_refusal(limit, packed, report)
    return None


def _source_size(src: Any) -> int | None:
    src = _path(src)
    if isinstance(src, (bytes, bytearray, memoryview)):
        return len(src)
    if isinstance(src, str):
        try:
            return os.path.getsize(src)
        except OSError:
            return None
    try:
        pos = src.tell()
        end = src.seek(0, io.SEEK_END)
        src.seek(pos)
        return end
    except (AttributeError, OSError, ValueError):
        return None


class Account(dict):
    """What a reader counts while it reads (counts and dates only: it is stored and served as JSON). Two things a reader
    must hand back but that may never be stored with the counts ride along as attributes: the claim-link prefixes it saw
    (they name the practice's EMA address) and the claim status words it did not know."""

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.prefixes: list[str] = []
        self.status_words: set[str] = set()
        self.content_id: str | None = None      # a hash of the rows read, in order (the same export saved again has it too)
        self.dated_notes: dict[int, str] = {}   # A/R: row -> billing note that may state a date (read once, then dropped)


# ------------------------------------------------------------------------------------------------------ the layouts
def norm(header: Any) -> str:
    """A column name as the readers compare it: stripped, lower-cased, spaces collapsed, and no spaces around "-" and "/"
    ("Non- Contractual Adjustments" becomes "non-contractual adjustments")."""
    s = " ".join(str(header if header is not None else "").replace("﻿", "").split()).lower()
    return re.sub(r"\s*([-/])\s*", r"\1", s)


PRODUCTION_CORE = ("posted date", "service date", "cpt/product", "check number", "claim link", "insurance payments",
                   "patient payments", "contractual adjustments")
PRODUCTION_REQUIRED = PRODUCTION_CORE + ("adjustment reason code", "payer", "charges", "non-contractual adjustments",
                                         "patient adjustments", "patient name", "patient dob")
PRODUCTION_OPTIONAL = ("units", "claim status", "claim action", "payer claim control number", "payer plan name", "primary payer",
                       "secondary payer", "payment allowed amount", "insurance charges", "payments", "adjustments",
                       "insurance adjustments")
PRODUCTION_NEVER = ("cpt/product description (custom)", "procedure count", "primary biller", "primary provider", "total wrvu",
                    "attendee name", "bill assignee", "claim assignee", "e/m only claim?", "payment follow-up date", "local tax",
                    "unit allowable")
AR_CORE = ("a/r claim id", "responsible party", "a/r balance type", "current a/r ($) (aging)", "service date")
AR_REQUIRED = AR_CORE + ("coverage type", "claim link", "claim status", "cpt/product", "primary payer", "patient name")
AR_OPTIONAL = ("aging date", "denial amount", "bill follow up date", "claim action", "denial category", "last billing note",
               "secondary payer", "patient mrn")
AR_NEVER = ("cpt/product description", "primary biller", "primary provider")
AR_CURRENT = "current a/r ($) (aging)"

LAYOUTS = {
    "production": {"core": PRODUCTION_CORE, "required": PRODUCTION_REQUIRED, "optional": PRODUCTION_OPTIONAL,
                   "never": PRODUCTION_NEVER},
    "ar_report": {"core": AR_CORE, "required": AR_REQUIRED, "optional": AR_OPTIONAL, "never": AR_NEVER},
}

# How each required column is written in EMA's export, and why RemitWatch needs it (said when one is missing).
WHY = {
    "posted date": ("Posted Date", "when each posting was made"),
    "service date": ("Service Date", "the visit each line belongs to"),
    "cpt/product": ("CPT/Product", "the code billed"),
    "check number": ("Check Number", "ties each payment to its remittance"),
    "claim link": ("Claim Link", "ties each line to its claim in EMA"),
    "insurance payments": ("Insurance Payments", "what insurers paid"),
    "patient payments": ("Patient Payments", "what patients paid"),
    "contractual adjustments": ("Contractual Adjustments", "what was written off under the contract"),
    "adjustment reason code": ("Adjustment Reason Code", "why each adjustment was made"),
    "payer": ("Payer", "who each posting came from"),
    "charges": ("Charges", "what was billed"),
    "non-contractual adjustments": ("Non- Contractual Adjustments", "what was written off outside the contract"),
    "patient adjustments": ("Patient Adjustments", "what was written off on the patient's side"),
    "patient name": ("Patient Name", "tells patients apart (kept only as a scrambled key, never as a name)"),
    "patient dob": ("Patient DOB", "tells two patients of one name apart (kept only inside a scrambled key)"),
    "a/r claim id": ("A/R Claim ID", "names the claim"),
    "responsible party": ("Responsible Party", "says who owes the balance"),
    "a/r balance type": ("A/R Balance Type", "tells a debit from a credit"),
    "current a/r ($) (aging)": ("Current A/R ($) (Aging)", "the open amount"),
    "coverage type": ("Coverage Type", "says whether the primary, secondary or tertiary payer owes it"),
    "claim status": ("Claim Status", "where the claim stands"),
    "primary payer": ("Primary Payer", "who should pay"),
}


def _is_bucket(name: str) -> bool:
    """An A/R aging bucket other than the current one (the export can offer 31-60, 61-90 ... columns)."""
    return name.endswith("a/r ($) (aging)") and name != AR_CURRENT and not name.startswith("total")


def _is_total(name: str) -> bool:
    return name.startswith("total a/r")


def _read_names(report: str, names: Sequence[str]) -> set[str]:
    lay = LAYOUTS[report]
    read = set(lay["required"]) | set(lay["optional"])
    if report == "ar_report":
        read |= {n for n in names if _is_bucket(n) or _is_total(n)}
    return read


KNOWN_NAMES = frozenset(PRODUCTION_REQUIRED + PRODUCTION_OPTIONAL + PRODUCTION_NEVER + AR_REQUIRED + AR_OPTIONAL + AR_NEVER)


def _near(report: str, names: set[str]) -> bool:
    """A layout that is plainly the report with a column or two taken out: every core name but at most two, and at least
    three quarters of the required names. It is recognised so that it is refused with the missing columns named, never
    read as a weekly export."""
    lay = LAYOUTS[report]
    return (len(set(lay["core"]) - names) <= 2 and len(set(lay["required"]) & names) >= 0.75 * len(lay["required"]))


def detect_report(columns: Sequence[Any]) -> str | None:
    """'production' or 'ar_report' when the column names hold every core name of one of the two reports (or all but two
    of them, with three quarters of the required names: a report with columns taken out, refused later with the missing
    columns named), else None. Production is tested first; a layout with an Activity Type column is an Encounter & Charge
    Lines export, not Production. ema_exports.detect_family calls this before its own family test."""
    names = {norm(c) for c in columns if c is not None}
    production_ok = "activity type" not in names
    if set(PRODUCTION_CORE) <= names and production_ok:
        return "production"
    if set(AR_CORE) <= names:
        return "ar_report"
    if production_ok and _near("production", names):
        return "production"
    if _near("ar_report", names):
        return "ar_report"
    return None


@dataclass(frozen=True)
class Recognized:
    """What recognition found: which report, where its column names are, and which columns it reads."""
    report: str             # 'production' | 'ar_report'
    fmt: str                # 'csv' (any delimited text) | 'xlsx'
    header_index: int       # the row (0-based, among the sheet's rows or the text's records) holding the column names
    index: dict             # canonical (normalised) column name -> position, for every column a reader reads
    columns: tuple          # the header cells as written
    missing: tuple          # required names not present (not empty: the file is refused)
    unused: tuple           # header cells no reader reads (as written)
    delimiter: str | None = None
    dimension: int | None = None    # .xlsx: data rows below the header by the sheet's declared dimension
    member: str | None = None       # the member's name when the report came inside a zip
    sheet: str | None = None        # .xlsx: the sheet that holds the report
    gz: bool = False                # the file is gzipped delimited text (an upload the page compressed): read as it unpacks
    name: str | None = None         # the report's own name when the file at hand is a working copy under another name


def _build(report: str, fmt: str, header_index: int, cells: Sequence[Any], *, delimiter: str | None = None,
           dimension: int | None = None, sheet: str | None = None) -> Recognized:
    written = tuple("" if c is None else str(c).strip() for c in cells)
    names = [norm(c) for c in written]
    read = _read_names(report, names)
    index: dict[str, int] = {}
    unused: list[str] = []
    for pos, (w, n) in enumerate(zip(written, names)):
        if n in read:
            if n in index:
                raise ReportRefused("duplicate_column",
                                    f"The column {w or n} appears twice in this {REPORT_NAMES[report]}. "
                                    "Export it again from EMA without adding or renaming columns.", report)
            index[n] = pos
        elif w:
            unused.append(w)
    missing = tuple(n for n in LAYOUTS[report]["required"] if n not in index)
    return Recognized(report=report, fmt=fmt, header_index=header_index, index=index, columns=written, missing=missing,
                      unused=tuple(unused), delimiter=delimiter, dimension=dimension, sheet=sheet)


# ------------------------------------------------------------------------------------------------------ text decoding
def _cp1252_fallback(exc: UnicodeError) -> tuple[str, int]:
    """Bytes that are not UTF-8 are read as Windows-1252 (Excel's own export encoding), not lost."""
    if not isinstance(exc, UnicodeDecodeError):
        raise exc
    return exc.object[exc.start:exc.end].decode("cp1252", errors="replace"), exc.end


_ERRORS = "remitwatch_cp1252"
codecs.register_error(_ERRORS, _cp1252_fallback)


def _text_records(head: bytes, truncated: bool, delimiter: str, limit: int) -> list[list[str]]:
    text = head.decode("utf-8-sig", errors=_ERRORS)
    if truncated:
        cut = max(text.rfind("\n"), text.rfind("\r"))
        text = text[:cut + 1] if cut >= 0 else ""
    out: list[list[str]] = []
    try:
        for rec in csv.reader(io.StringIO(text, newline=""), delimiter=delimiter):
            out.append(rec)
            if len(out) >= limit:
                break
    except csv.Error:
        pass
    return out


def _find_header(rows: Iterable[Sequence[Any]]) -> tuple[str, int, Sequence[Any]] | None:
    """The first of the first HEADER_SCAN rows that holds a report's core names; failing that, the first that is a near
    match (a report with columns taken out)."""
    near = None
    for i, row in enumerate(rows):
        if i >= HEADER_SCAN:
            break
        cells = [c for c in row if c is not None]
        names = {norm(c) for c in cells}
        if ("activity type" not in names and set(PRODUCTION_CORE) <= names) or set(AR_CORE) <= names:
            return detect_report(cells), i, row
        if near is None:
            rep = detect_report(cells)
            if rep:
                near = (rep, i, row)
    return near


def _known_count(row: Sequence[Any]) -> int:
    return sum(1 for c in row if c is not None and norm(c) in KNOWN_NAMES)


# --------------------------------------------------------------------------------------------------------- recognition
def _path(src: Any) -> Any:
    """A path given as a pathlib.Path (or any path-like) as plain text; anything else as it is."""
    return os.fspath(src) if isinstance(src, os.PathLike) else src


def _head_of(src: str | bytes | BinaryIO, n: int = PEEK_BYTES) -> tuple[bytes, bool]:
    """The first n bytes of a source, and whether there is more."""
    src = _path(src)
    if isinstance(src, (bytes, bytearray, memoryview)):
        b = bytes(src)
        return b[:n], len(b) > n
    if isinstance(src, str):
        with open(src, "rb") as fh:
            head = fh.read(n + 1)
        return head[:n], len(head) > n
    pos = src.tell() if hasattr(src, "tell") else None
    head = src.read(n + 1)
    if pos is not None and hasattr(src, "seek"):
        try:
            src.seek(pos)
        except (OSError, ValueError):
            pass
    return head[:n], len(head) > n


def _is_zip_head(head: bytes) -> bool:
    return head[:4] == b"PK\x03\x04" or head[:4] == b"PK\x05\x06"


def _is_gzip_head(head: bytes) -> bool:
    return head[:2] == b"\x1f\x8b"


def _gunzip_head(src: str | bytes | BinaryIO, n: int = PEEK_BYTES) -> tuple[bytes, bool]:
    """The first n unpacked bytes of a gzipped source (a path, bytes, or a stream put back where it was), and whether
    there is more. A source that is not whole (the first bytes of a large file) gives what its bytes unpack to."""
    src = _path(src)
    d = zlib.decompressobj(31)
    out = b""
    try:
        if isinstance(src, (bytes, bytearray, memoryview)):
            out = d.decompress(bytes(src), n + 1)
        else:
            fh = open(src, "rb") if isinstance(src, str) else src
            pos = fh.tell() if fh is src and hasattr(fh, "tell") else None
            try:
                while len(out) <= n:
                    piece = d.unconsumed_tail or fh.read(65536)
                    if not piece:
                        break
                    out += d.decompress(piece, n + 1 - len(out))
            finally:
                if fh is not src:
                    fh.close()
                elif pos is not None and hasattr(fh, "seek"):
                    try:
                        fh.seek(pos)
                    except (OSError, ValueError):
                        pass
    except zlib.error:
        return b"", False
    return out[:n], len(out) > n


def _zip_names(src: str | bytes | BinaryIO) -> list[str] | None:
    try:
        with zipfile.ZipFile(_zip_source(src)) as zf:
            return zf.namelist()
    except (zipfile.BadZipFile, OSError, ValueError):
        return None


def _zip_source(src: str | bytes | BinaryIO) -> Any:
    src = _path(src)
    if isinstance(src, (bytes, bytearray, memoryview)):
        return io.BytesIO(bytes(src))
    return src


def _recognize_xlsx(src: str | bytes | BinaryIO, member: str | None = None) -> Recognized | None:
    """A workbook's report, from its first rows. One whose parts say they unpack to far more than any export (openpyxl
    holds a workbook's shared strings whole) raises the unpacks_too_large refusal before it is opened."""
    import openpyxl

    bad = zip_refusal(src)
    if bad is not None:
        raise bad
    try:
        wb = openpyxl.load_workbook(_zip_source(src), read_only=True, data_only=True)
    except Exception:           # noqa: BLE001  (not a workbook openpyxl can open: not a report this module reads)
        return None
    try:
        for ws in wb.worksheets:
            try:
                declared = ws.max_row
            except Exception:   # noqa: BLE001
                declared = None
            rows = []
            try:
                ws.reset_dimensions()
                for row in ws.iter_rows(values_only=True):
                    rows.append(row)
                    if len(rows) >= HEADER_SCAN:
                        break
            except Exception:   # noqa: BLE001  (a sheet that cannot be read is not a report this module reads)
                continue
            hit = _find_header(rows)
            if hit:
                rep, i, row = hit
                dim = (declared - i - 1) if isinstance(declared, int) and declared > i else None
                rec = _build(rep, "xlsx", i, list(row), dimension=dim, sheet=ws.title)
                return replace(rec, member=member) if member else rec
        return None
    finally:
        wb.close()


def recognize(src: str | bytes, name: str) -> Recognized | None:
    """Which report a file is, from its column names: a path, or the file's bytes (its first PEEK_BYTES are enough for
    delimited text; an .xlsx needs all of it). None when it is neither report (the three weekly exports, a zip of other
    files, anything else). A report whose columns break a rule raises ReportRefused ('duplicate_column')."""
    head, truncated = _head_of(src)
    if not head:
        return None
    if _is_gzip_head(head):
        # a gzipped report (the Upload page compresses text reports on the way): recognised from its first unpacked bytes
        head, truncated = _gunzip_head(src)
        if not head or _is_zip_head(head) or _is_gzip_head(head) or b"\x00" in head:
            return None
        rec = _recognize_text(head, truncated)
        return replace(rec, gz=True) if rec else None
    if _is_zip_head(head):
        names = _zip_names(src)
        if names is None or "xl/workbook.xml" not in names:
            return None                     # a zip of files: recognize_zip
        return _recognize_xlsx(src)
    if b"\x00" in head:
        return None
    return _recognize_text(head, truncated)


def _recognize_text(head: bytes, truncated: bool) -> Recognized | None:
    """Delimited text (comma, tab, pipe or semicolon) from its first bytes."""
    best: tuple[int, int, str, str, Sequence[Any]] | None = None
    for d in (",", "\t", "|", ";"):
        hit = _find_header(_text_records(head, truncated, d, HEADER_SCAN))
        if hit:
            rep, i, row = hit
            score = _known_count(row)
            if best is None or score > best[0]:
                best = (score, i, d, rep, row)
    if best is None:
        return None
    _score, i, d, rep, row = best
    return _build(rep, "csv", i, list(row), delimiter=d)


def recognize_zip(src: str | bytes, name: str) -> list[Recognized]:
    """The report members of a zip (an .xlsx file is a workbook, not a zip of reports: it gives []). Members ending .csv,
    .tsv, .txt or .xlsx are looked at; __MACOSX and hidden members are skipped; a zip inside the zip refuses it."""
    head, _ = _head_of(src, 8)
    if not _is_zip_head(head):
        return []
    try:
        zf = zipfile.ZipFile(_zip_source(src))
    except (zipfile.BadZipFile, OSError, ValueError):
        return []
    out: list[Recognized] = []
    try:
        names = zf.namelist()
        if "xl/workbook.xml" in names:
            return []
        bad = zip_refusal(src)
        if bad is not None:
            raise bad                       # a zip whose members unpack to far more than any export: set aside, not opened
        for info in zf.infolist():
            fn = info.filename
            base = os.path.basename(fn.rstrip("/"))
            if info.is_dir() or "__MACOSX" in fn or base.startswith((".", "~$")):
                continue
            low = base.lower()
            if low.endswith(".zip"):
                raise ReportRefused("zip_in_zip", "This zip holds another zip. Put the reports in one zip directly.")
            if not low.endswith(REPORT_MEMBERS):
                continue
            if low.endswith(".xlsx"):
                if info.file_size > XLSX_MEMBER_MAX:
                    continue
                with zf.open(info) as m:
                    data = m.read(XLSX_MEMBER_MAX + 1)
                rec = _recognize_xlsx(data, member=fn)
            else:
                with zf.open(info) as m:
                    data = m.read(PEEK_BYTES + 1)
                rec = recognize(data, base)
                rec = replace(rec, member=fn) if rec else None
            if rec:
                out.append(rec)
        return out
    finally:
        zf.close()


def is_plain_zip(src: str | bytes) -> bool:
    """A zip that is not an .xlsx workbook (so it may hold reports as members)."""
    head, _ = _head_of(src, 8)
    if not _is_zip_head(head):
        return False
    names = _zip_names(src)
    return names is not None and "xl/workbook.xml" not in names


@contextmanager
def _open(src: str | bytes | BinaryIO, rec: Recognized) -> Iterator[BinaryIO]:
    src = _path(src)
    if rec.member:
        zf = zipfile.ZipFile(_zip_source(src))
        try:
            info = zf.getinfo(rec.member)
            limit = unpack_limit(info.compress_size)
            if info.file_size > limit:
                raise unpack_refusal(limit, info.compress_size, rec.report)
            with zf.open(rec.member) as m:
                if rec.fmt == "xlsx":
                    data = m.read(XLSX_MEMBER_MAX + 1)
                    if len(data) > XLSX_MEMBER_MAX:
                        raise ReportRefused("unreadable", "The workbook inside the zip is too large. Save the report as CSV.",
                                            rec.report)
                    bad = zip_refusal(data, report=rec.report)
                    if bad is not None:
                        raise bad
                    yield io.BytesIO(data)
                else:
                    yield capped(m, limit, packed=info.compress_size, report=rec.report)
        finally:
            zf.close()
    elif rec.gz:
        # a gzipped report: unpacked as it is read, never whole (a 300 MB CSV is about 25 MB gzipped), and never to more
        # than unpack_limit of its size (a gzip bomb is refused as it is read)
        raw = open(src, "rb") if isinstance(src, str) else (io.BytesIO(bytes(src)) if isinstance(src, (bytes, bytearray, memoryview))
                                                              else src)
        packed = _source_size(src)
        try:
            with gzip.GzipFile(fileobj=raw, mode="rb") as fh:
                yield capped(fh, unpack_limit(packed), packed=packed, report=rec.report)
        finally:
            if raw is not src:
                raw.close()
    elif isinstance(src, str):
        with open(src, "rb") as fh:
            yield fh
    elif isinstance(src, (bytes, bytearray, memoryview)):
        yield io.BytesIO(bytes(src))
    else:
        yield src           # a stream the caller owns (it is not closed here)


def open_report(src: str | bytes | BinaryIO, rec: Recognized) -> ContextManager[BinaryIO]:
    """The report's bytes as one stream, whatever it came as: a file on disk, bytes in memory, a member of a zip, or a
    stream (a Dropbox download wrapped by ChunkStream). Nothing is copied to disk."""
    return _open(src, rec)


class ChunkStream(io.RawIOBase):
    """A read-only stream over an iterable of byte chunks (DropboxClient.download_chunks), so a download can be read
    by the same reader as a file without being written anywhere."""

    def __init__(self, chunks: Iterable[bytes]):
        super().__init__()
        self._it = iter(chunks)
        self._buf = b""

    def readable(self) -> bool:
        return True

    def readinto(self, b: Any) -> int:
        while not self._buf:
            try:
                self._buf = next(self._it)
            except StopIteration:
                return 0
        n = min(len(b), len(self._buf))
        b[:n] = self._buf[:n]
        self._buf = self._buf[n:]
        return n


def _rows(fh: BinaryIO, rec: Recognized, state: dict | None = None) -> Iterator[Sequence[Any]]:
    """The rows below the column names, as cells: text for delimited text, typed values for an .xlsx. For delimited
    text, `state['cut_short']` is set when the file's last line ends without a line break (an interrupted copy)."""
    if rec.fmt == "xlsx":
        import openpyxl

        bad = zip_refusal(fh, report=rec.report)
        if bad is not None:
            raise bad
        try:
            fh.seek(0)
        except (AttributeError, OSError, ValueError):
            pass
        try:
            wb = openpyxl.load_workbook(fh, read_only=True, data_only=True)
        except Exception as exc:        # noqa: BLE001
            raise ReportRefused("unreadable", f"The workbook could not be opened ({type(exc).__name__}). Export it again.",
                                rec.report) from None
        failed = None
        try:
            ws = wb[rec.sheet] if rec.sheet and rec.sheet in wb.sheetnames else wb.worksheets[0]
            ws.reset_dimensions()
            it = enumerate(ws.iter_rows(values_only=True))
            while True:
                try:
                    i, row = next(it)
                except StopIteration:
                    break
                except Exception as exc:        # noqa: BLE001  (a converter's message can hold the cell's text: never passed on)
                    failed = type(exc).__name__
                    break
                if i > rec.header_index:
                    yield row
        finally:
            wb.close()
        if failed:
            raise ReportRefused("unreadable", f"The workbook could not be read to its end ({failed}). Export it again.", rec.report)
        return
    text = io.TextIOWrapper(fh, encoding="utf-8-sig", errors=_ERRORS, newline="")
    last = [""]
    cap = LINE_MAX + 2                  # room for the line's own CR LF

    def lines() -> Iterator[str]:
        # one line at a time, and never more than LINE_MAX of it: a file that is one giant line (or holds one) is refused
        # as soon as the line passes the cap, not held whole first
        n = 0
        while True:
            line = text.readline(cap)
            if not line:
                return
            n += 1
            if len(line) >= cap and not line.endswith(("\n", "\r")):
                raise long_line_refusal(n, rec.report)
            last[0] = line
            yield line
    try:
        reader = csv.reader(lines(), delimiter=rec.delimiter or ",")
        i = -1
        while True:
            try:
                row = next(reader)
            except StopIteration:
                break
            except csv.Error:
                raise ReportRefused("unreadable", f"Line {reader.line_num:,} of the {REPORT_NAMES[rec.report]} could not be read "
                                    "as delimited text. Export it again without opening it in Excel.", rec.report) from None
            i += 1
            if i > rec.header_index:
                yield row
        if state is not None and last[0] and not last[0].endswith(("\n", "\r")):
            state["cut_short"] = True
    finally:
        try:
            text.detach()
        except (ValueError, OSError):
            pass


# --------------------------------------------------------------------------------------------------------------- cells
_BAD = object()     # a cell that holds something but not a valid value of its kind (counted, never shown)
_MONEY = re.compile(r"^(-)?\(?(-)?\$?(-)?(\d{1,3}(?:,\d{3})*|\d+)(?:\.(\d*))?\)?$")
_MDY = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4}|\d{2})(?:[ T].*)?$")
_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[ T].*)?$")
_LINK = re.compile(r"/claim/(\d{1,12})/?\s*$")
_SCI = re.compile(r"^\d(\.\d+)?E[+-]?\d+$", re.I)
_CPT = re.compile(r"^(\d{4}[0-9A-Z]|[A-V]\d{4})$")
_CARC = re.compile(r"^(CO|PR|OA|PI|CR)\s*-\s*([A-Z0-9]{1,4})$", re.I)
_KIND_PREFIX = re.compile(r"^adjustment\s*-\s*", re.I)
_EXCEL_EPOCH = date(1899, 12, 30)
_MONEY_CACHE: dict[str, Any] = {}


def _text(v: Any) -> str | None:
    """A cell as text: placeholders ("-", "None", empty) become None; a whole number read as a float loses its ".0"."""
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        return None if s in _NA else s
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float):
        if math.isnan(v):
            return None
        return str(int(v)) if v.is_integer() and abs(v) < 1e15 else repr(v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    s = str(v).strip()
    return None if s in _NA else s


def _money_text(s: str) -> Any:
    m = _MONEY.match(s)
    if not m or (("(" in s) != (")" in s)):
        return _BAD
    whole = int(m.group(4).replace(",", ""))
    frac = m.group(5) or ""
    cents = whole * 100 + int((frac + "00")[:2]) + (1 if len(frac) > 2 and frac[2] >= "5" else 0)
    return -cents if (m.group(1) or m.group(2) or m.group(3) or "(" in s) else cents


def _money(v: Any) -> Any:
    """Cents, None for an empty cell or a placeholder, _BAD for anything else."""
    if v is None:
        return None
    if isinstance(v, str):
        c = _MONEY_CACHE.get(v)
        if c is None:
            s = v.strip()
            c = None if s in _NA else _money_text(s)
            if c is not None:
                if len(_MONEY_CACHE) > 50_000:
                    _MONEY_CACHE.clear()
                _MONEY_CACHE[v] = c
        return c
    if isinstance(v, bool):
        return _BAD
    if isinstance(v, (int, float)):
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return _BAD
        return int(round(v * 100))
    return _money(str(v))


def money(v: Any) -> int | None:
    """Integer cents from a money cell: a number, or text such as $1,234.56, ($12.34), -$12.34 or $45. (4500 cents).
    Parentheses or a minus sign make it negative. A placeholder, an empty cell or anything else gives None."""
    c = _money(v)
    return None if c is _BAD else c


def _iso_date(y: int, m: int, d: int) -> Any:
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return _BAD


def _date(v: Any, order: str = "mdy") -> Any:
    """ISO date, None for an empty cell or a placeholder, _BAD for anything else."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, bool):
        return _BAD
    if isinstance(v, (int, float)):
        if isinstance(v, float) and math.isnan(v):
            return None
        if 20000 <= v <= 80000:
            return (_EXCEL_EPOCH + timedelta(days=int(v))).isoformat()
        return _BAD
    s = str(v).strip()
    if s in _NA:
        return None
    m = _MDY.match(s)
    if m:
        a, b, y = m.groups()
        yy = int(y) if len(y) == 4 else 2000 + int(y)
        return _iso_date(yy, int(a), int(b)) if order == "mdy" else _iso_date(yy, int(b), int(a))
    m = _ISO.match(s)
    if m:
        return _iso_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return _BAD             # five digits written as text are a damaged cell, never an Excel serial (only a typed number is)


def parse_date(v: Any, order: str = "mdy") -> str | None:
    """An ISO date (YYYY-MM-DD) from a date cell: a date or datetime, M/D/YYYY (D/M/YYYY when order is 'dmy'; a two-digit
    year becomes 20YY), YYYY-MM-DD with or without a time, or an Excel serial number from 20000 to 80000 when the cell is a
    typed number (an .xlsx cell; the same digits written as text are not a date). None otherwise."""
    d = _date(v, order)
    return None if d is _BAD else d


def today_iso() -> str:
    """Today's date where the practice is (runtime.practice_today) as YYYY-MM-DD: the day a report is read, which bounds
    the dates it may hold and caps its as_of, and the latest day the ledger's retention may count from. EMA dates its
    rows in the practice's days, and the Data tab counts a report's age to that day. The tests set it."""
    return runtime.practice_today()


def date_bounds(today: str | None = None) -> tuple[str, str, str]:
    """(earliest real date, latest posted or aging date, latest service date) for a file read on `today`: a posting
    can carry at most tomorrow's date, a visit at most a month ahead, and nothing is older than EARLIEST_DATE."""
    t = date.fromisoformat((today or today_iso())[:10])
    return (EARLIEST_DATE, (t + timedelta(days=POSTED_AHEAD_DAYS)).isoformat(),
            (t + timedelta(days=SERVICE_AHEAD_DAYS)).isoformat())


def date_order(samples: Iterable[Any]) -> str:
    """'mdy' or 'dmy' for a file, from its first date values written as text with slashes: a first part above 12 with
    no second part above 12 means day first. Both happening refuses the file (ReportRefused 'two_date_orders')."""
    first = second = False
    for v in samples:
        if not isinstance(v, str):
            continue
        m = _MDY.match(v.strip())
        if not m:
            continue
        if int(m.group(1)) > 12:
            first = True
        if int(m.group(2)) > 12:
            second = True
    if first and second:
        raise ReportRefused("two_date_orders", "Its dates are written in two orders (month first on some lines, day first on "
                            "others). Export it again from EMA without opening it in another program.")
    return "dmy" if first else "mdy"


def link_number(v: Any) -> tuple[int | None, str | None]:
    """EMA's claim number from a Claim Link cell, and the link's prefix up to and including "/claim/". The rest of the
    address is dropped at once; the prefix is only ever validated and kept by learn_claim_prefix."""
    s = _text(v)
    if not s:
        return None, None
    m = _LINK.search(s)
    if not m:
        return None, None
    return int(m.group(1)), s[:m.start() + 7]


def check_number(v: Any) -> tuple[str | None, bool]:
    """A check number as text, leading zeros kept, and whether a spreadsheet damaged it (turned it into scientific
    notation, or into a number too large to hold exactly)."""
    if v is None or isinstance(v, bool):
        return None, False
    if isinstance(v, float):
        if math.isnan(v):
            return None, False
        if v.is_integer() and abs(v) < 1e15:
            return str(int(v)), False
        return repr(v).upper(), True
    if isinstance(v, int):
        return str(v), False
    s = str(v).strip()
    if s in _NA:
        return None, False
    return s, bool(_SCI.match(s))


def code_of(v: Any) -> tuple[str | None, bool]:
    """The code of a CPT/Product cell, upper-cased, and whether it is a product (anything that is not CPT or HCPCS)."""
    s = _text(v)
    if s is None:
        return None, False
    u = s.upper()[:60]
    return u, not _CPT.match(u)


def reason_of(v: Any) -> tuple[str | None, str | None, str | None]:
    """(group, code, None) for a CARC such as CO-45; (None, None, kind) for EMA's own adjustment kinds, with the
    "Adjustment - " prefix dropped ("Small Balance"); (None, None, None) for an empty cell."""
    s = _text(v)
    if s is None:
        return None, None, None
    m = _CARC.match(s)
    if m:
        return m.group(1).upper(), m.group(2).upper(), None
    kind = _KIND_PREFIX.sub("", s).strip()[:60]
    return None, None, kind or None


_SUFFIXES = frozenset({"JR", "SR", "II", "III", "IV"})


def name_tokens(name: str | None) -> str | None:
    """A person's name as sorted words: the words of two or more letters of the last and first names, accents dropped,
    the suffixes JR, SR, II, III and IV dropped. "Last, First M" and "First Last" give the same tokens."""
    if not name:
        return None
    s = unicodedata.normalize("NFKD", str(name))
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).upper()
    toks = [t for t in re.findall(r"[A-Z]{2,}", s) if t not in _SUFFIXES]
    return " ".join(sorted(toks)) or None


def _initials(name: str | None) -> str | None:
    """First-name and last-name initials ("Testpatient, Alpha" gives "AT")."""
    if not name:
        return None
    s = unicodedata.normalize("NFKD", str(name))
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).upper()
    if "," in s:
        last, first = s.split(",", 1)
    else:
        parts = s.split()
        first, last = (parts[0], parts[-1]) if len(parts) > 1 else ("", s)
    fl = re.search(r"[A-Z]", first)
    ll = re.search(r"[A-Z]", last)
    out = (fl.group(0) if fl else "") + (ll.group(0) if ll else "")
    return out or None


# ---------------------------------------------------------------------------------------------------------------- keys
def key_salt(conn: sqlite3.Connection) -> bytes:
    """The store's key salt (32 random bytes, meta ema:key_salt), made on first use. It never leaves the store. Only the
    readers and the ledger make it; a page or a check that only reads uses stored_salt, which never writes."""
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    r = conn.execute("SELECT value FROM meta WHERE key=?", (META_SALT,)).fetchone()
    if r and r[0]:
        return bytes.fromhex(r[0])
    conn.execute("INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)", (META_SALT, secrets.token_bytes(32).hex()))
    conn.commit()
    return bytes.fromhex(conn.execute("SELECT value FROM meta WHERE key=?", (META_SALT,)).fetchone()[0])


def stored_salt(conn: sqlite3.Connection) -> bytes | None:
    """The store's key salt when it has one, else None; never makes one. The dashboard's copy never holds the salt, and
    a store that never read a report has none (it holds no keyed row to compare with either)."""
    try:
        r = conn.execute("SELECT value FROM meta WHERE key=?", (META_SALT,)).fetchone()
    except sqlite3.OperationalError:
        return None
    try:
        return bytes.fromhex(r[0]) if r and r[0] else None
    except ValueError:
        return None


def salt_fingerprint(salt: bytes) -> str:
    return hashlib.sha256(salt).hexdigest()[:16]


def _h64(salt: bytes, msg: str) -> int:
    return int.from_bytes(hmac.new(salt, msg.encode("utf-8"), hashlib.sha256).digest()[:8], "big", signed=True)


def _h16(salt: bytes, msg: str) -> str:
    return hmac.new(salt, msg.encode("utf-8"), hashlib.sha256).hexdigest()[:16]


def name_key(salt: bytes, name: str | None) -> int | None:
    """A signed 64-bit key for a patient's name ("Last, First" or "First Last"): HMAC-SHA256(salt, "N|" + tokens)."""
    t = name_tokens(name)
    return _h64(salt, "N|" + t) if t else None


def name_key_parts(salt: bytes, last: str | None, first: str | None) -> int | None:
    """The same key from a remittance's last and first names (rw_name_key on the 835 side)."""
    if not (last or first):
        return None
    return name_key(salt, f"{last or ''}, {first or ''}")


def person_key(salt: bytes, name: str | None, dob: Any) -> int | None:
    """A key for a patient's name and date of birth together; None when either is missing."""
    t = name_tokens(name)
    d = parse_date(dob) if dob is not None else None
    return _h64(salt, "N|" + t + "|" + d) if t and d else None


def mrn_key(salt: bytes, mrn: Any) -> int | None:
    s = _text(mrn)
    return _h64(salt, "M|" + s) if s else None


def note_hash(salt: bytes, note: Any) -> int | None:
    s = _text(note)
    return _h64(salt, "T|" + s) if s else None


def check_loose(check: Any) -> str | None:
    """A check number compared loosely: letters and digits only, upper-cased, leading zeros dropped."""
    s = _text(check)
    if not s:
        return None
    t = re.sub(r"[^A-Z0-9]", "", s.upper()).lstrip("0")
    return t or None


_SHAPE = str.maketrans("0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ", "9" * 10 + "A" * 52)


def shape(text: Any) -> str | None:
    """Digits become 9 and letters A; everything else and every run length is kept ("A12-3B" gives "A99-9A")."""
    s = _text(text)
    return s.translate(_SHAPE) if s else None


_ROUTES: dict[int, list] = {}       # per connection: the payer groupers its SQL functions use (refreshed on learning)


def register_functions(conn: sqlite3.Connection, salt: bytes | None = None, *, create: bool = True) -> None:
    """SQL functions the EMA modules use on the store connection: rw_name_key(last, first), rw_status_class(status
    [, has_answer]), rw_bill(claim_id), rw_payer_group(name), rw_payer_kind(name), rw_check_loose(check), rw_shape(text).
    Without a salt the store's own is used: made on first use when `create` is true (the readers and the ledger), never
    made when it is false (checks and pages, which only read: on the dashboard's copy, which holds no salt, rw_name_key
    then gives NULL, and the copy is never written)."""
    if salt is None:
        salt = key_salt(conn) if create else stored_salt(conn)
    holder = [_grouper(conn)]
    if id(conn) not in _ROUTES and len(_ROUTES) >= 64:
        _ROUTES.clear()                 # a long-lived process opens many connections; older ones keep what they had
    _ROUTES.setdefault(id(conn), []).append(holder)
    del _ROUTES[id(conn)][:-4]          # registering again replaces older holders of the same connection
    if salt is None:
        conn.create_function("rw_name_key", 2, lambda last, first: None, deterministic=True)
    else:
        conn.create_function("rw_name_key", 2, lambda last, first: name_key_parts(salt, last, first), deterministic=True)
    conn.create_function("rw_status_class", 1, lambda s: status_class(s), deterministic=True)
    conn.create_function("rw_status_class", 2, lambda s, a: status_class(s, None if a is None else bool(a)), deterministic=True)
    conn.create_function("rw_bill", 1, bill_of_claim, deterministic=True)
    conn.create_function("rw_payer_group", 1, lambda n: holder[0](n)[0])
    conn.create_function("rw_payer_kind", 1, lambda n: holder[0](n)[1])
    conn.create_function("rw_check_loose", 1, check_loose, deterministic=True)
    conn.create_function("rw_shape", 1, shape, deterministic=True)


# ----------------------------------------------------------------------------------------------------------------- bills
_CLAIM = re.compile(r"^(F?[A-Z]{2}\d{2}[A-Z0-9]{5,6})C\d{0,3}$")
_FACILITY = re.compile(r"^F[A-Z]{2}\d{2}[A-Z0-9]{5,6}C\d{0,3}$")


def bill_of_claim(claim_id: str | None) -> str | None:
    """The bill of an EMA claim id: the id less its submission suffix (C and up to three digits). The ASC's facility
    claim ids (an F before the usual shape) keep their F. Any other id is its own bill, as detect.bill_of has it."""
    if not claim_id:
        return None
    s = str(claim_id).strip()
    m = _CLAIM.match(s)
    return m.group(1) if m else (s or None)


def is_facility_claim(claim_id: str | None) -> bool:
    """True for the ASC's facility claim ids (F before the usual EMA shape)."""
    return bool(claim_id) and bool(_FACILITY.match(str(claim_id).strip()))


# -------------------------------------------------------------------------------------------------------------- statuses
STATUS_CLASS: dict[str, str] = {
    "payment pending": "pending", "insurance reprocessing": "pending", "medical records sent": "pending",
    "denied": "denied", "rejected": "rejected",
    "batched for printing": "not_sent", "batched for sending": "not_sent", "invalid": "not_sent",
    "ready for posting": "answered", "resolved/paid": "paid", "resolved/other": "settled",
    "inactive/voided": "void", "-": "none", "none": "none", "": "none",
}
# words EMA does not use today: the regular expressions detect.py used before the dictionary
_FALLBACK = (
    (re.compile(r"VOID|CANCEL", re.I), "void"),
    (re.compile(r"REJECT", re.I), "rejected"),
    (re.compile(r"DENI", re.I), "denied"),
    (re.compile(r"CLOSED|WRITE.?OFF|WRITTEN OFF|ZERO BALANCE", re.I), "gave_up"),
    (re.compile(r"\bPAID\b", re.I), "paid"),
    (re.compile(r"SETTLED|COLLECTION|RESOLVED", re.I), "settled"),
    (re.compile(r"HOLD|READY|UNBILLED|NOT SENT|NOT SUBMITTED|DRAFT|QUEUED|UNSUBMITTED", re.I), "not_sent"),
)


def _status_word(status: Any) -> str:
    return re.sub(r"\s*/\s*", "/", " ".join(str(status).split()).lower())


def status_class(status: str | None, has_answer: bool | None = None) -> str:
    """The class of an EMA claim status: pending, denied, rejected, not_sent, answered, paid, settled, gave_up, void,
    none or unknown. Exact words first (STATUS_CLASS); a word EMA does not use today falls back to the older regular
    expressions, else 'unknown'. Resolved/Other is settled when an insurance answer is on file and gave_up when there is
    none (has_answer False); unknown either way, it stays settled. A status is claim-level and undated: it routes an
    item, it never closes one."""
    if status is None:
        return "none"
    w = _status_word(status)
    cls = STATUS_CLASS.get(w)
    if cls is None:
        cls = next((c for rx, c in _FALLBACK if rx.search(w)), "unknown")
    if cls == "settled" and has_answer is False:
        return "gave_up"
    return cls


_WORD_OK = re.compile(r"^[a-z][a-z0-9 /&().,'-]{0,39}$")


def record_unknown_statuses(conn: sqlite3.Connection, words: Iterable[str]) -> None:
    """Add the status words the dictionary does not know to meta ema:unknown_status_words (the Data tab lists them)."""
    new = {_status_word(w) for w in words if w is not None}
    new = {w for w in new if w not in STATUS_CLASS and _WORD_OK.match(w)}
    if not new:
        return
    r = conn.execute("SELECT value FROM meta WHERE key=?", (META_UNKNOWN_STATUS,)).fetchone()
    have = set(json.loads(r[0])) if r and r[0] else set()
    if new <= have:
        return
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (META_UNKNOWN_STATUS, json.dumps(sorted(have | new)[:100])))
    conn.commit()


# ----------------------------------------------------------------------------------------------------------- payer groups
VA_RULE = re.compile(r"VETERAN|^VA\b|TRIWEST|OPTUM VA|CCN|VACAA")
_GROUP_KINDS = {g: k for _p, g, k in codes.PAYER_RULES}
_GROUP_KINDS[VA_GROUP[0]] = VA_GROUP[1]


def _payer_key(name: Any) -> str:
    return " ".join(str(name).split()).upper()


def _env_aliases(raw: str) -> dict[str, str]:
    if not raw.strip():
        return {}
    try:
        d = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(d, dict):
        return {}
    return {_payer_key(k): str(v) for k, v in d.items() if k and v}


def _group_of_name(name: str) -> tuple[str, str]:
    if name in _GROUP_KINDS:                      # an alias may name a group directly
        return name, _GROUP_KINDS[name]
    if VA_RULE.search(_payer_key(name)):
        return VA_GROUP
    return codes.normalize_payer(name)


class _Grouper:
    def __init__(self, env: dict[str, str], learned: dict[str, str]):
        self.env, self.learned = env, learned
        self.cache: dict[str, tuple[str, str]] = {}

    def __call__(self, name: Any) -> tuple[str, str]:
        s = _text(name)
        if not s:
            return ("Unknown", "other")
        got = self.cache.get(s)
        if got is None:
            k = _payer_key(s)
            target = self.env.get(k) or self.learned.get(k) or s
            got = _group_of_name(target)
            if len(self.cache) > 20_000:
                self.cache.clear()
            self.cache[s] = got
        return got


_GROUPERS: dict[tuple[str, str], _Grouper] = {}


def _learned_raw(conn: sqlite3.Connection) -> str:
    try:
        r = conn.execute("SELECT value FROM meta WHERE key=?", (META_ALIASES,)).fetchone()
    except sqlite3.OperationalError:
        return ""
    return r[0] if r and r[0] else ""


def _grouper(conn: sqlite3.Connection) -> _Grouper:
    env_raw = os.environ.get("REMITWATCH_EMA_PAYER_ALIASES", "")
    learned_raw = _learned_raw(conn)
    key = (env_raw, learned_raw)
    g = _GROUPERS.get(key)
    if g is None:
        try:
            learned = json.loads(learned_raw) if learned_raw else {}
        except ValueError:
            learned = {}
        g = _Grouper(_env_aliases(env_raw), {_payer_key(k): str(v) for k, v in (learned or {}).items()})
        if len(_GROUPERS) > 16:
            _GROUPERS.clear()
        _GROUPERS[key] = g
    return g


def payer_group_of(conn: sqlite3.Connection, ema_payer: str | None) -> tuple[str, str]:
    """(payer group, kind) for an EMA payer name: the owner's aliases (REMITWATCH_EMA_PAYER_ALIASES, JSON), then the aliases
    learned from payer ids (learn_payer_aliases), then the VA pattern (VA Community Care, tested before every other rule),
    then codes.normalize_payer."""
    return _grouper(conn)(ema_payer)


def learn_payer_aliases(conn: sqlite3.Connection) -> dict:
    """Map an EMA payer name to VA Community Care when at least 20 of its weekly-export rows carry a payer id and at least
    90% of those are VA network ids (VA_PAYER_IDS). The map is kept in meta ema:payer_aliases_learned, so it survives the
    weekly exports being dropped; a name with too few rows keeps its earlier answer."""
    raw = _learned_raw(conn)
    try:
        have = {str(k): str(v) for k, v in (json.loads(raw) if raw else {}).items()}
    except (ValueError, AttributeError):
        have = {}
    new = dict(have)
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ema_rows'").fetchone():
        per: dict[str, list[int]] = {}
        for payer, pid, n in conn.execute(
                """SELECT payer, upper(trim(payer_id)), COUNT(*) FROM ema_rows
                   WHERE payer IS NOT NULL AND payer_id IS NOT NULL AND trim(payer_id) <> '' GROUP BY 1, 2"""):
            k = _payer_key(payer)
            if not k:
                continue
            t = per.setdefault(k, [0, 0])
            t[0] += n
            if pid in VA_PAYER_IDS:
                t[1] += n
        for k, (n, va) in per.items():
            if n >= 20:
                if va >= 0.9 * n:
                    new[k] = VA_GROUP[0]
                else:
                    new.pop(k, None)
    if new != have:
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (META_ALIASES, json.dumps(new, sort_keys=True)))
        conn.commit()
        for holder in _ROUTES.get(id(conn), []):
            holder[0] = _grouper(conn)
    return new


# ------------------------------------------------------------------------------------------------- Open in EMA addresses
def _prefix_pattern() -> re.Pattern:
    return re.compile(r"^https://[a-z0-9][a-z0-9-]{0,62}\." + re.escape(CLAIM_HOST_SUFFIX)
                      + r"/ema/ws/v\d{1,3}/qlikRedirect/claim/$", re.I)


def learn_claim_prefix(conn: sqlite3.Connection, prefixes: Iterable[str]) -> str | None:
    """Keep the claim-link prefix a file showed, in meta ema:claim_url_prefix, when it is exactly one prefix of EMA's
    claim address under the vendor's domain. Two different prefixes refuse the file (ReportRefused 'two_hosts'). Another
    domain, or anything that is not an https address, is never kept. Returns the prefix kept, or None."""
    seen: list[str] = []
    for p in prefixes:
        if p and p.lower() not in {s.lower() for s in seen}:
            seen.append(p)
    if len(seen) > 1:
        raise ReportRefused("two_hosts", "Its claim links come from two EMA systems. Export the report from the practice's own EMA only.")
    if not seen:
        return None
    p = seen[0]
    if not _prefix_pattern().match(p):
        return None
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (META_PREFIX, p))
    conn.commit()
    return p


def claim_url_template(conn: sqlite3.Connection) -> str | None:
    """The address that opens a claim in EMA, with {claim} where the claim number goes: REMITWATCH_EMA_CLAIM_URL when set
    ('off' turns the links off), else the prefix learned from the reports. None when there is neither. The browser builds
    a link only for numbers of 6 to 12 digits, and no RemitWatch address ever carries one."""
    env = os.environ.get("REMITWATCH_EMA_CLAIM_URL", "").strip()
    if env.lower() == "off":
        return None
    if env and env.lower().startswith("https://") and env.count("{claim}") == 1 and not re.search(r"[\s\"'<>]", env):
        return env
    try:
        r = conn.execute("SELECT value FROM meta WHERE key=?", (META_PREFIX,)).fetchone()
    except sqlite3.OperationalError:
        return None
    return (r[0] + "{claim}") if r and r[0] and _prefix_pattern().match(r[0]) else None


# ----------------------------------------------------------------------------------------------------------- the switch
def switch() -> str:
    """REMITWATCH_EMA_REPORTS: 'all' reads both reports, 'ar' only the A/R, 'off' neither. Default: 'all' on a server,
    'ar' on a serverless host (until Production passes the release gate there)."""
    v = os.environ.get("REMITWATCH_EMA_REPORTS", "").strip().lower()
    if v in ("all", "ar", "off"):
        return v
    return "ar" if runtime.serverless() else "all"


def reads(report: str) -> bool:
    """Whether this host reads the report now. One it does not read is kept as waiting and read once the switch allows."""
    s = switch()
    if report == "ar_report":
        return s in ("all", "ar")
    if report == "production":
        return s == "all"
    return False


# ------------------------------------------------------------------------------------------------------------ the schema
DDL = """
CREATE TABLE IF NOT EXISTS ema_report_files (file_id TEXT PRIMARY KEY, report TEXT, source_name TEXT, via TEXT,
  origin TEXT, container TEXT, format TEXT, scope TEXT, svc_lo TEXT, svc_hi TEXT, posted_lo TEXT, posted_hi TEXT,
  as_of TEXT, created_at TEXT, received_at TEXT, applied_at TEXT, rows_read INTEGER, rows_kept INTEGER,
  rows_applied INTEGER, live_rows INTEGER, links INTEGER, account TEXT, columns TEXT, missing TEXT, unused TEXT,
  status TEXT, status_reason TEXT, attempts INTEGER DEFAULT 0, replaced_by TEXT, decision TEXT, decided_by TEXT, decided_at TEXT,
  content_id TEXT, source_kind TEXT);
CREATE INDEX IF NOT EXISTS ix_erf_status ON ema_report_files(report, status);
CREATE TABLE IF NOT EXISTS ema_ar (file_id TEXT, row_no INTEGER, as_of TEXT, svc_year TEXT, item_key TEXT,
  coverage TEXT, party TEXT, claim_id TEXT, bill TEXT, link INTEGER, balance_type TEXT, balance_cents INTEGER,
  denial_cents INTEGER, aging_date TEXT, follow_up TEXT, claim_action TEXT, claim_status TEXT, status_class TEXT,
  cpt TEXT, product INTEGER, denial_category TEXT, has_note INTEGER, note_chars INTEGER, note_hash INTEGER,
  primary_payer TEXT, secondary_payer TEXT, payer_group TEXT, payer_kind TEXT, svc TEXT, initials TEXT,
  name_key INTEGER, mrn_key INTEGER, exposure_cents INTEGER, facility INTEGER, bucket TEXT,
  note_kind TEXT, note_date TEXT, note_wait INTEGER, PRIMARY KEY (file_id, row_no));
CREATE INDEX IF NOT EXISTS ix_ar_claim ON ema_ar(claim_id);
CREATE INDEX IF NOT EXISTS ix_ar_link ON ema_ar(link);
CREATE INDEX IF NOT EXISTS ix_ar_year ON ema_ar(svc_year);
CREATE INDEX IF NOT EXISTS ix_ar_item ON ema_ar(item_key);
CREATE TABLE IF NOT EXISTS ema_ar_items (item_key TEXT PRIMARY KEY, party TEXT, coverage TEXT, claim_id TEXT,
  link INTEGER, cpt TEXT, svc TEXT, first_seen TEXT, last_seen TEXT, snapshots INTEGER, absent_since TEXT,
  first_status TEXT, last_status TEXT, last_balance_cents INTEGER, max_balance_cents INTEGER, last_action TEXT,
  last_note_hash INTEGER, note_changed TEXT, last_follow_up TEXT, worked_on TEXT, worked_how TEXT);
CREATE INDEX IF NOT EXISTS ix_eai_claim ON ema_ar_items(claim_id);
CREATE TABLE IF NOT EXISTS ema_claim_links (claim_id TEXT PRIMARY KEY, link INTEGER, bill TEXT, first_seen TEXT, last_seen TEXT);
CREATE INDEX IF NOT EXISTS ix_ecl_link ON ema_claim_links(link);
CREATE TABLE IF NOT EXISTS ema_lines (link INTEGER, svc TEXT, cpt TEXT, bill TEXT, claim_id TEXT, map_method TEXT,
  person INTEGER, name_key INTEGER, twin_link INTEGER, primary_payer TEXT, secondary_payer TEXT, payer_group TEXT,
  payer_kind TEXT, plan TEXT, pccn TEXT, units REAL, charges INTEGER, ins_charges INTEGER, ins_paid INTEGER,
  ins_paid_no835 INTEGER, contractual INTEGER, noncontractual INTEGER, nonroutine INTEGER, denial_first_posted TEXT,
  pat_paid_joined INTEGER, pat_adj_joined INTEGER, pat_join TEXT, pat_after_denial INTEGER, allowed INTEGER,
  balance INTEGER, first_posted TEXT, last_posted TEXT, first_ins_paid TEXT, last_ins_paid TEXT,
  zero_pay_rows INTEGER, checks INTEGER, status TEXT, status_class TEXT, action TEXT, action_on TEXT, flags INTEGER,
  as_of TEXT, keep TEXT, facility INTEGER, first_charge_posted TEXT, first_ins_posting TEXT, ins_answer_rows INTEGER,
  PRIMARY KEY (link, svc, cpt)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_el_bill ON ema_lines(bill, svc);
CREATE TABLE IF NOT EXISTS ema_line_payments (link INTEGER, svc TEXT, cpt TEXT, check_no TEXT, posted TEXT, payer TEXT,
  payer_group TEXT, role TEXT, ins_paid INTEGER, rows INTEGER, allowed INTEGER, name_key INTEGER, in_835 INTEGER,
  trace_key TEXT, PRIMARY KEY (link, svc, cpt, check_no, posted, payer)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS ema_line_reasons (link INTEGER, svc TEXT, cpt TEXT, grp TEXT, code TEXT, reason_class TEXT,
  payer TEXT, contractual INTEGER, noncontractual INTEGER, rows INTEGER, first_posted TEXT, last_posted TEXT,
  PRIMARY KEY (link, svc, cpt, grp, code)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS ema_checks (check_no TEXT, payer TEXT, payer_group TEXT, payer_kind TEXT, shape TEXT,
  damaged INTEGER, first_posted TEXT, last_posted TEXT, rows INTEGER, links INTEGER, lines INTEGER, ins_paid INTEGER,
  zero_rows INTEGER, svc_lo TEXT, svc_hi TEXT, twin_cents INTEGER, match TEXT, trace_key TEXT, trace_payment_date TEXT,
  trace_paid_cents INTEGER, cause TEXT, pair_trace TEXT, PRIMARY KEY (check_no, payer)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS ema_months (month TEXT, payer TEXT, measure TEXT, detail TEXT, cents INTEGER, rows INTEGER,
  PRIMARY KEY (month, payer, measure, detail)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS ema_link_map (link INTEGER PRIMARY KEY, bill TEXT, claim_id TEXT, method TEXT,
  votes INTEGER, total INTEGER, conflict INTEGER DEFAULT 0, mapped_at TEXT, facility INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_elm_bill ON ema_link_map(bill);
CREATE TABLE IF NOT EXISTS ema_link_events (link INTEGER, kind TEXT, value TEXT, first_seen TEXT,
  PRIMARY KEY (link, kind, value)) WITHOUT ROWID;
"""
# indexes an earlier build made that no query uses (8 MB of a serverless host's in-memory store for the payments' check
# numbers: every lookup goes through a line's key)
DROPPED_INDEXES = ("ix_elp_check",)
EMA_TABLES = ("ema_report_files", "ema_ar", "ema_ar_items", "ema_claim_links", "ema_lines", "ema_line_payments",
              "ema_line_reasons", "ema_checks", "ema_months", "ema_link_map", "ema_link_events")
# columns that hold keys or keyed hashes: never in the dashboard's copy, never shown
KEY_COLUMNS = ("person", "name_key", "mrn_key", "note_hash", "last_note_hash")
_EXPECTED: dict[str, list[tuple[str, str]]] = {}


def _expected_columns() -> dict[str, list[tuple[str, str]]]:
    if not _EXPECTED:
        c = sqlite3.connect(":memory:")
        try:
            c.executescript(DDL)
            for t in EMA_TABLES:
                _EXPECTED[t] = [(r[1], r[2] or "") for r in c.execute(f"PRAGMA table_info({t})")]
        finally:
            c.close()
    return _EXPECTED


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create every EMA table and index, and add any column a table made by an earlier build lacks. Safe on every start;
    the same tables in a compact (in-memory) store."""
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    statements = [x.strip() for x in DDL.split(";") if x.strip()]
    conn.executescript(";\n".join(x for x in statements if x.upper().startswith("CREATE TABLE")) + ";")
    for table, cols in _expected_columns().items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in cols:
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
                if table == "ema_ar_items" and name == "absent_since" and "gone_since" in have:
                    conn.execute("UPDATE ema_ar_items SET absent_since = gone_since")
    conn.commit()
    conn.executescript(";\n".join(x for x in statements if x.upper().startswith("CREATE INDEX")) + ";")
    for ix in DROPPED_INDEXES:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (ix,)).fetchone():
            conn.execute(f"DROP INDEX {ix}")
    conn.commit()


def bill_sql(col: str) -> str:
    """SQL for the bill of a claim id column, without a function of RemitWatch's own (so a copy can be built on any
    connection): the id less a trailing C and up to three digits. bill_of_claim also requires EMA's shape first, so a
    caller matches both this and the id itself (an id of another shape is its own bill)."""
    return (f"CASE WHEN {col} GLOB '*C[0-9][0-9][0-9]' THEN substr({col}, 1, length({col}) - 4) "
            f"WHEN {col} GLOB '*C[0-9][0-9]' THEN substr({col}, 1, length({col}) - 3) "
            f"WHEN {col} GLOB '*C[0-9]' THEN substr({col}, 1, length({col}) - 2) "
            f"WHEN {col} GLOB '*C' THEN substr({col}, 1, length({col}) - 1) ELSE {col} END")


def serving_tables(conn: sqlite3.Connection | None = None) -> list[tuple[str, str | None, str | None]]:
    """The EMA rows the dashboard's copy holds, as (table, column whitelist, filter) entries for cloud.SERVING_TABLES: no
    origin, no key or keyed hash in any table, and only the claim numbers the pages can show: those on the A/R, every
    claim number of a bill that has an item (any submission of the bill: the map's claim id is only one of them), and the
    claim numbers items name (EMA-only denials). Their lines, payments, reasons, map rows and events come along. The
    filters use plain SQL only, so the copy can be built on any connection. The meta filter must also leave out
    ema:key_salt (SERVING_META_FILTER)."""
    cols = _expected_columns()

    def keep(table: str, drop: tuple = ()) -> str:
        return ",".join(n for n, _d in cols[table] if n not in KEY_COLUMNS and n not in drop)
    have_link = False
    if conn is not None:
        try:
            have_link = "ema_link" in {r[1] for r in conn.execute("PRAGMA main.table_info(findings)")}
        except sqlite3.Error:
            have_link = False
    finding_links = " OR link IN (SELECT ema_link FROM main.findings WHERE ema_link IS NOT NULL)" if have_link else ""
    pcn = "patient_control_number"
    bills = (f"SELECT {pcn} FROM main.findings WHERE {pcn} IS NOT NULL "
             f"UNION SELECT {bill_sql(pcn)} FROM main.findings WHERE {pcn} IS NOT NULL")
    links = ("(link IN (SELECT link FROM main.ema_ar WHERE link IS NOT NULL)"
             f" OR link IN (SELECT link FROM main.ema_link_map WHERE COALESCE(conflict, 0) = 0 AND bill IN ({bills}))"
             f" OR link IN (SELECT link FROM main.ema_link_map WHERE claim_id IN (SELECT {pcn} FROM main.findings"
             f" WHERE {pcn} IS NOT NULL))" + finding_links + ")")
    return [
        ("ema_report_files", keep("ema_report_files", ("origin",)), None),
        ("ema_ar", keep("ema_ar"), None),
        ("ema_ar_items", keep("ema_ar_items"), "item_key IN (SELECT item_key FROM main.ema_ar)"),
        ("ema_claim_links", keep("ema_claim_links"),
         "claim_id IN (SELECT claim_id FROM main.ema_ar WHERE claim_id IS NOT NULL) OR claim_id IN "
         "(SELECT patient_control_number FROM main.findings WHERE patient_control_number IS NOT NULL)"),
        ("ema_link_map", keep("ema_link_map"), links),
        ("ema_lines", keep("ema_lines"), links),
        ("ema_line_payments", keep("ema_line_payments"), links),
        ("ema_line_reasons", keep("ema_line_reasons"), links),
        ("ema_link_events", keep("ema_link_events"), links),
        # the checks no 835 carries (C1's list), and the checks of the payments the copy holds (a line's outcome reads them)
        ("ema_checks", keep("ema_checks"), f"match = 'none' OR check_no IN (SELECT check_no FROM main.ema_line_payments WHERE {links})"),
        ("ema_months", keep("ema_months"), None),
    ]


SERVING_META_FILTER = "key NOT LIKE 'sync:cursor%' AND key NOT LIKE 'cache:%' AND key NOT LIKE 'ema:key_salt%'"


def file_id_of(src: str | bytes | BinaryIO) -> str:
    """The SHA-256 of a file's bytes, read in 1 MB pieces: a path, bytes, or a binary stream (read to its end)."""
    h = hashlib.sha256()
    src = _path(src)
    if isinstance(src, (bytes, bytearray, memoryview)):
        h.update(src)
    elif isinstance(src, str):
        with open(src, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    else:
        for chunk in iter(lambda: src.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------------------------------------------- readers
_MISS = object()


def _missing_message(rec: Recognized) -> str:
    parts = []
    for n in rec.missing:
        label, why = WHY.get(n, (n, "the reports need it"))
        parts.append(f"{label} ({why})")
    return (f"This {REPORT_NAMES[rec.report]} lacks the column{'s' if len(parts) > 1 else ''} {', '.join(parts)}. "
            "Export it again from EMA with every column of the report.")


def _refuse_missing(rec: Recognized) -> None:
    if rec.missing:
        raise ReportRefused("missing_columns", _missing_message(rec), rec.report)


def _first_rows(rows: Iterator[Sequence[Any]], positions: Sequence[int | None]) -> tuple[list, str]:
    """Read rows until ORDER_SAMPLES date values written as text are seen, or ORDER_SAMPLES rows whatever they hold (typed
    dates in an .xlsx and ISO text need no order), and decide the file's date order from the text dates seen. The rows read
    are handed back so nothing is lost; at most ORDER_SAMPLES rows are ever held."""
    buf: list = []
    samples: list[str] = []
    pos = [p for p in positions if p is not None]
    for row in rows:
        buf.append(row)
        for p in pos:
            if p < len(row):
                v = row[p]
                if isinstance(v, str) and "/" in v:
                    samples.append(v)
        if len(samples) >= ORDER_SAMPLES or len(buf) >= ORDER_SAMPLES:
            break
    return buf, date_order(samples)


def _header_width(rec: Recognized) -> int:
    """How many cells a row of delimited text must have: the column names, less empty ones at the end."""
    n = len(rec.columns)
    while n and not str(rec.columns[n - 1] if rec.columns[n - 1] is not None else "").strip():
        n -= 1
    return n


def _misaligned(row: Sequence[Any], width: int) -> bool:
    """A line of delimited text whose cells do not line up with the column names: an extra cell (an unquoted comma in a
    description shifts every later column by one) or a missing one (a line cut short). Empty cells after the last
    column do not count."""
    n = len(row)
    if n == width:
        return False
    if n > width:
        return any(isinstance(c, str) and c.strip() for c in row[width:])
    return True


def _left_out(row: Sequence[Any], counts: dict, kind: str) -> None:
    """Count a line that is not kept: blank, a total line, or `kind`."""
    if _blank(row):
        counts["blank"] += 1
    elif _total_line(row):
        counts["total"] += 1
    else:
        counts[kind] += 1


def _blank(row: Sequence[Any]) -> bool:
    """A line with nothing in it: every cell empty or a placeholder ("-", "None")."""
    for c in row:
        if c is None:
            continue
        if isinstance(c, str):
            if c.strip() not in _NA:
                return False
        else:
            return False
    return True


def _total_line(row: Sequence[Any]) -> bool:
    seen = 0
    for c in row:
        if isinstance(c, str) and c.strip():
            if TOTAL_LINE.match(c):
                return True
            seen += 1
            if seen >= 3:
                return False
    return False


def _start_account(account: dict, rec: Recognized) -> None:
    account.update(rows_read=0, rows_kept=0, left={"blank": 0, "total": 0, "no_date": 0, "misaligned": 0}, bad_amounts=0,
                   bad_dates=0, out_of_range_dates=0, damaged_checks=0, check_rows=0, identity_breaks=0, bad_links=0, hosts=0, svc_lo=None,
                   svc_hi=None, posted_lo=None, posted_hi=None, as_of=None, date_order=None, format=rec.fmt, cut_short=False)


class Posting(NamedTuple):
    """One Production row, keys only. The field order is the ledger's column order."""
    posted: str
    svc: str
    cpt: str | None
    units: float | None
    link: int | None
    check_no: str | None
    grp: str | None
    code: str | None
    kind: str | None
    status: str | None
    action: str | None
    payer: str | None
    plan: str | None
    primary_payer: str | None
    secondary_payer: str | None
    pccn: str | None
    person: int | None
    name_key: int | None
    charges: int | None
    ins_charges: int | None
    ins_paid: int | None
    pat_paid: int | None
    contractual: int | None
    noncontractual: int | None
    pat_adj: int | None
    allowed: int | None
    flags: int


def iter_production(src: str | bytes | BinaryIO, rec: Recognized, *, salt: bytes, account: dict,
                    deadline: float | None = None, today: str | None = None) -> Iterator[Posting]:
    """Stream a Production report as Posting tuples (keys only: no name, date of birth, description or staff), one per
    row whose posted date and service date parse and are real for a file read on `today` (default: today): a posted date
    from 2000 to tomorrow, a service date from 2000 to a month ahead. Other rows are left out and counted, never kept: a
    date outside that range (a damaged cell) would otherwise make the file look newer than any later export. In delimited
    text a row whose cells do not line up with the column names (an unquoted comma shifts every later column) is left
    out as 'misaligned', and a file whose last line has no line break is marked cut_short. The as_of is the newest posted
    date, never later than `today`. Nothing is held but the row being read, at most ORDER_SAMPLES rows while the date
    order is decided, and a few small look-up caches. `account` is filled with counts and dates (an Account also collects
    the claim-link prefixes and the status words it did not know). Raises ReportRefused (missing_columns, two_hosts,
    two_date_orders, too_many_rows, unreadable) and ReadTimeout when `deadline` (a time.time() value) passes; the deadline
    is checked every DEADLINE_EVERY rows."""
    if rec.report != "production":
        raise ValueError("iter_production reads a Production report")
    _refuse_missing(rec)
    _start_account(account, rec)
    ix = rec.index
    i_posted, i_svc, i_cpt, i_check, i_link = (ix["posted date"], ix["service date"], ix["cpt/product"], ix["check number"],
                                               ix["claim link"])
    i_ins_paid, i_pat_paid, i_contr = ix["insurance payments"], ix["patient payments"], ix["contractual adjustments"]
    i_reason, i_payer, i_charges = ix["adjustment reason code"], ix["payer"], ix["charges"]
    i_noncontr, i_pat_adj, i_name, i_dob = (ix["non-contractual adjustments"], ix["patient adjustments"], ix["patient name"],
                                            ix["patient dob"])
    g = ix.get
    i_units, i_status, i_action, i_pccn, i_plan = (g("units"), g("claim status"), g("claim action"),
                                                   g("payer claim control number"), g("payer plan name"))
    i_primary, i_secondary, i_allowed, i_ins_charges = (g("primary payer"), g("secondary payer"), g("payment allowed amount"),
                                                        g("insurance charges"))
    i_pay, i_adj, i_ins_adj = g("payments"), g("adjustments"), g("insurance adjustments")
    identities = None not in (i_pay, i_adj, i_ins_adj)
    width = max(ix.values()) + 1
    text_rows = rec.fmt != "xlsx"
    hw = _header_width(rec)
    day = (today or today_iso())[:10]
    earliest, posted_max, svc_max = date_bounds(day)
    n = kept = 0
    left = {"blank": 0, "total": 0, "no_date": 0, "misaligned": 0}
    c = {"bad_amounts": 0, "bad_dates": 0, "far": 0, "damaged": 0, "breaks": 0, "bad_links": 0, "checks": 0}
    state: dict = {}
    svc_lo = svc_hi = posted_lo = posted_hi = None
    prefixes: list[str] = []
    prefix_low: set[str] = set()
    last_prefix: str | None = None
    keys: dict = {}
    dates: dict = {}
    code_cache: dict = {}
    reason_cache: dict = {}
    unit_cache: dict = {}
    status_seen: set[str] = set()
    account_extra = account if isinstance(account, Account) else None

    def mv(v: Any) -> int | None:
        x = _money(v)
        if x is _BAD:
            c["bad_amounts"] += 1
            return None
        return x

    def opt_text(row: Sequence[Any], i: int | None) -> str | None:
        return _text(row[i]) if i is not None else None

    def flush() -> None:
        account.update(rows_read=n, rows_kept=kept, left=dict(left), bad_amounts=c["bad_amounts"], bad_dates=c["bad_dates"],
                       out_of_range_dates=c["far"], damaged_checks=c["damaged"], check_rows=c["checks"],
                       identity_breaks=c["breaks"],
                       bad_links=c["bad_links"], hosts=len(prefixes), svc_lo=svc_lo, svc_hi=svc_hi, posted_lo=posted_lo,
                       posted_hi=posted_hi, as_of=min(posted_hi, day) if posted_hi else None,
                       cut_short=bool(state.get("cut_short")))
        if account_extra is not None:
            account_extra.prefixes = prefixes[:3]
            account_extra.status_words |= status_seen

    with open_report(src, rec) as fh:
        rows = _rows(fh, rec, state)
        buf, order = _first_rows(rows, (i_posted, i_svc, i_dob))
        account["date_order"] = order
        failed = None
        try:
            for row in itertools.chain(buf, rows):
                n += 1
                if n > MAX_ROWS:
                    raise ReportRefused("too_many_rows", f"It has more than {MAX_ROWS:,} rows. Export it in two service-date ranges.",
                                        "production")
                if deadline is not None and n % DEADLINE_EVERY == 0 and time.time() > deadline:
                    raise ReadTimeout(f"Reading stopped at the time limit after {n:,} rows.")
                if text_rows and len(row) != hw and _misaligned(row, hw):
                    _left_out(row, left, "misaligned")
                    continue
                if len(row) < width:
                    row = list(row) + [None] * (width - len(row))
                v = row[i_posted]
                posted = dates.get(v, _MISS)
                if posted is _MISS:
                    posted = dates[v] = _date(v, order)
                v = row[i_svc]
                svc = dates.get(v, _MISS)
                if svc is _MISS:
                    svc = dates[v] = _date(v, order)
                far_p = posted is not None and posted is not _BAD and not (earliest <= posted <= posted_max)
                far_s = svc is not None and svc is not _BAD and not (earliest <= svc <= svc_max)
                if far_p:
                    posted = _BAD
                if far_s:
                    svc = _BAD
                if posted is None or posted is _BAD or svc is None or svc is _BAD:
                    before = left["no_date"]
                    _left_out(row, left, "no_date")
                    if left["no_date"] > before:
                        c["bad_dates"] += (posted is _BAD) + (svc is _BAD)
                        c["far"] += far_p + far_s
                    continue
                if len(dates) > 20_000:
                    dates.clear()
                kept += 1
                if svc_lo is None or svc < svc_lo:
                    svc_lo = svc
                if svc_hi is None or svc > svc_hi:
                    svc_hi = svc
                if posted_lo is None or posted < posted_lo:
                    posted_lo = posted
                if posted_hi is None or posted > posted_hi:
                    posted_hi = posted
                flags = 0
                v = row[i_cpt]
                got = code_cache.get(v)
                if got is None:
                    got = code_cache[v] = code_of(v)
                cpt, product = got
                if product:
                    flags |= FLAG_PRODUCT
                if posted < svc:
                    flags |= FLAG_POSTED_BEFORE_SERVICE
                check_no, damaged = check_number(row[i_check])
                if check_no is not None:
                    c["checks"] += 1
                if damaged:
                    flags |= FLAG_DAMAGED_CHECK
                    c["damaged"] += 1
                v = row[i_reason]
                got = reason_cache.get(v)
                if got is None:
                    got = reason_cache[v] = reason_of(v)
                grp, code, kind = got
                lv = row[i_link]
                link = None
                if isinstance(lv, str) and last_prefix is not None and lv.startswith(last_prefix):
                    tail = lv[len(last_prefix):].strip().rstrip("/")
                    if tail.isascii() and tail.isdigit() and 0 < len(tail) <= 12:
                        link = int(tail)
                if link is None and lv is not None:
                    num, pre = link_number(lv)
                    if num is None:
                        if _text(lv) is not None:
                            c["bad_links"] += 1
                    else:
                        link = num
                        if pre != last_prefix:
                            low = pre.lower()
                            if low not in prefix_low:
                                prefix_low.add(low)
                                prefixes.append(pre)
                                if len(prefix_low) > 1:
                                    raise ReportRefused("two_hosts", "Its claim links come from two EMA systems. Export the "
                                                        "report from the practice's own EMA only.", "production")
                            last_prefix = pre
                units = None
                if i_units is not None:
                    v = row[i_units]
                    units = unit_cache.get(v, _MISS)
                    if units is _MISS:
                        u = _text(v)
                        try:
                            units = float(u) if u is not None else None
                        except ValueError:
                            units = None
                        if len(unit_cache) < 5000:
                            unit_cache[v] = units
                status = opt_text(row, i_status)
                if status is not None and len(status_seen) < 50 and _status_word(status) not in STATUS_CLASS:
                    status_seen.add(status[:40])
                payer = _text(row[i_payer])
                if payer is not None and payer.lower() == "patient":
                    flags |= FLAG_PATIENT_SIDE
                nm, dob = row[i_name], row[i_dob]
                kk = (nm, dob)
                pk = keys.get(kk)
                if pk is None:
                    nt = _text(nm)
                    d = _date(dob, order)
                    d = None if d is _BAD else d
                    pk = keys[kk] = (person_key(salt, nt, d) if d else None, name_key(salt, nt))
                    if len(keys) > 100_000:
                        keys.clear()
                charges = mv(row[i_charges])
                ins_paid = mv(row[i_ins_paid])
                pat_paid = mv(row[i_pat_paid])
                contractual = mv(row[i_contr])
                noncontractual = mv(row[i_noncontr])
                pat_adj = mv(row[i_pat_adj])
                ins_charges = mv(row[i_ins_charges]) if i_ins_charges is not None else None
                allowed = mv(row[i_allowed]) if i_allowed is not None else None
                if identities:
                    pay, adj, ins_adj = mv(row[i_pay]), mv(row[i_adj]), mv(row[i_ins_adj])
                    if ((pay or 0) != (ins_paid or 0) + (pat_paid or 0)
                            or (ins_adj or 0) != (contractual or 0) + (noncontractual or 0)
                            or (adj or 0) != (ins_adj or 0) + (pat_adj or 0)):
                        flags |= FLAG_IDENTITY_BREAK
                        c["breaks"] += 1
                yield Posting(posted, svc, cpt, units, link, check_no, grp, code, kind, status, opt_text(row, i_action),
                              payer, opt_text(row, i_plan), opt_text(row, i_primary), opt_text(row, i_secondary),
                              opt_text(row, i_pccn), pk[0], pk[1], charges, ins_charges, ins_paid, pat_paid, contractual,
                              noncontractual, pat_adj, allowed, flags)
        except (ReportRefused, ReadTimeout):
            raise
        except Exception as exc:        # noqa: BLE001  (a converter's message can hold a cell's text: never passed on)
            failed = type(exc).__name__
        finally:
            flush()
        if failed:
            raise ReportRefused("unreadable", f"Row {n:,} of the Production report could not be read ({failed}). "
                                "Export it again from EMA.", 'production')


AR_ROW_COLUMNS = ("row_no", "svc_year", "item_key", "coverage", "party", "claim_id", "bill", "link", "balance_type",
                  "balance_cents", "denial_cents", "aging_date", "follow_up", "claim_action", "claim_status", "status_class",
                  "cpt", "product", "denial_category", "has_note", "note_chars", "note_hash", "primary_payer",
                  "secondary_payer", "payer_group", "payer_kind", "svc", "initials", "name_key", "mrn_key", "exposure_cents",
                  "facility", "note_kind", "note_date", "note_wait")
_NOTE_DATED = re.compile(r"\d[/-]\d")      # a billing note that may state a date (states.classify_note reads it)


def _cap(s: str | None, n: int) -> str | None:
    return s[:n] if s else s


def _note_classifier() -> Any:
    """states.classify_note (what an A/R billing note says was done: its kinds, the newest date it states and an "allow N
    days"), or None on a build without it: the note columns then stay empty and the states see only that a note exists."""
    try:
        from .states import classify_note
    except ImportError:
        return None
    return classify_note


def _stage_ar_notes(conn: sqlite3.Connection, acct: Any, as_of: str | None) -> int:
    """The dates the staged A/R rows' billing notes state, resolved on the file's own as_of (a date written without a year
    takes as_of's year, or the year before when it would fall after as_of; one after as_of is no work done yet): the
    reader resolved them on the day it read the file, which a file read later than it was pulled must not keep. The
    notes' words are dropped here. Returns how many rows changed."""
    notes = getattr(acct, "dated_notes", None) or {}
    classify = _note_classifier() if notes and as_of else None
    n = 0
    if classify is not None:
        batch = []
        failed = 0
        for row_no, text in notes.items():
            try:
                batch.append((classify(text, as_of).get("date"), row_no))
            except Exception as exc:       # noqa: BLE001  (left as the reader resolved it)
                if not failed:      # the first one with its stack, never its message (it can quote the note's words)
                    runlog.caught(RuntimeError(f"An A/R billing note could not be read for its date ({type(exc).__name__})")
                                  .with_traceback(exc.__traceback__), "ar_note_dates", level="warning")
                failed += 1
        if failed > 1:
            runlog.event("ar_note_dates", "notes_not_dated", level="warning", counts={"notes": failed})
        n = conn.executemany("UPDATE temp.ema_ar_stage SET note_date=? WHERE row_no=? AND note_date IS NOT ?",
                             [(d, r, d) for d, r in batch]).rowcount
    if hasattr(acct, "dated_notes"):
        acct.dated_notes = {}
    return n


def iter_ar(src: str | bytes | BinaryIO, rec: Recognized, *, salt: bytes, account: dict,
            today: str | None = None) -> Iterator[dict]:
    """Stream an A/R report as dicts holding the ema_ar columns (all but file_id and as_of, which the caller sets; the
    payer group is set by apply_ar, which knows the aliases). A row is kept when it has a real service date (from 2000 to a
    month after `today`, default today) and a responsible party; other rows are left out and counted. An aging date
    after tomorrow or before 2000 is a damaged cell: it is left blank and counted, as an unreadable one is. In delimited
    text a row whose cells do not line up with the column names is left out as 'misaligned', and a file whose last line
    has no line break is marked cut_short. The as_of (the newest service or aging date) is never later than `today`.
    Exposure is the open A/R (the sum of the aging buckets when the export has more than the current one), else the
    denial amount: never the two added. `account` gets counts, dates, the date order, the aging window (30 when every
    open line's aging date is within 31 days of the file's as_of, None for a full export) and the bucket columns."""
    if rec.report != "ar_report":
        raise ValueError("iter_ar reads an A/R report")
    _refuse_missing(rec)
    _start_account(account, rec)
    ix = rec.index
    g = ix.get
    i_claim, i_party, i_btype, i_current, i_svc = (ix["a/r claim id"], ix["responsible party"], ix["a/r balance type"],
                                                   ix[AR_CURRENT], ix["service date"])
    i_cov, i_link, i_status, i_cpt, i_primary, i_name = (ix["coverage type"], ix["claim link"], ix["claim status"],
                                                         ix["cpt/product"], ix["primary payer"], ix["patient name"])
    i_aging, i_denial, i_follow, i_action = g("aging date"), g("denial amount"), g("bill follow up date"), g("claim action")
    i_dcat, i_note, i_secondary, i_mrn = g("denial category"), g("last billing note"), g("secondary payer"), g("patient mrn")
    buckets = sorted(p for name, p in ix.items() if _is_bucket(name))
    totals = sorted(p for name, p in ix.items() if _is_total(name))
    i_total = totals[0] if totals else None
    width = max(ix.values()) + 1
    text_rows = rec.fmt != "xlsx"
    hw = _header_width(rec)
    day = (today or today_iso())[:10]
    earliest, posted_max, svc_max = date_bounds(day)
    n = kept = 0
    left = {"blank": 0, "total": 0, "no_date": 0, "misaligned": 0}
    c = {"bad_amounts": 0, "bad_dates": 0, "far": 0}
    state: dict = {}
    svc_lo = svc_hi = aging_hi = oldest_open = None
    prefixes: list[str] = []
    occurrences: dict[str, int] = {}
    status_seen: set[str] = set()
    account_extra = account if isinstance(account, Account) else None
    bucket_names = sorted(name for name in ix if _is_bucket(name) or _is_total(name))
    classify = _note_classifier()
    dated: dict[int, str] = {}          # row -> note, for the notes that may state a date (resolved on the file's as_of)

    def mv(v: Any) -> int | None:
        x = _money(v)
        if x is _BAD:
            c["bad_amounts"] += 1
            return None
        return x

    def dv(v: Any) -> str | None:
        x = _date(v, order)
        if x is _BAD:
            c["bad_dates"] += 1
            return None
        return x

    def aging_of(v: Any) -> str | None:
        x = dv(v)
        if x is not None and not (earliest <= x <= posted_max):
            c["bad_dates"] += 1
            c["far"] += 1
            return None
        return x

    def opt(row: Sequence[Any], i: int | None) -> Any:
        return row[i] if i is not None else None

    def flush() -> None:
        as_of = max(x for x in (svc_hi, aging_hi) if x) if (svc_hi or aging_hi) else None
        as_of = min(as_of, day) if as_of else None
        window = None
        if not bucket_names:
            window = 30
            if oldest_open and as_of:
                window = 30 if (date.fromisoformat(as_of) - date.fromisoformat(oldest_open)).days <= 31 else None
        account.update(rows_read=n, rows_kept=kept, left=dict(left), bad_amounts=c["bad_amounts"], bad_dates=c["bad_dates"],
                       out_of_range_dates=c["far"], hosts=len(prefixes), svc_lo=svc_lo, svc_hi=svc_hi, as_of=as_of,
                       aging_window=window, buckets=bucket_names, cut_short=bool(state.get("cut_short")))
        if account_extra is not None:
            account_extra.prefixes = prefixes[:3]
            account_extra.status_words |= status_seen
            account_extra.dated_notes = dated       # never stored or served: _stage_ar_notes reads it once, then drops it

    order = "mdy"
    with open_report(src, rec) as fh:
        rows = _rows(fh, rec, state)
        buf, order = _first_rows(rows, (i_svc, i_aging, i_follow))
        account["date_order"] = order
        failed = None
        try:
            for row in itertools.chain(buf, rows):
                n += 1
                if n > MAX_ROWS:
                    raise ReportRefused("too_many_rows", f"It has more than {MAX_ROWS:,} rows.", "ar_report")
                if text_rows and len(row) != hw and _misaligned(row, hw):
                    _left_out(row, left, "misaligned")
                    continue
                if len(row) < width:
                    row = list(row) + [None] * (width - len(row))
                svc = _date(row[i_svc], order)
                far = svc is not None and svc is not _BAD and not (earliest <= svc <= svc_max)
                if far:
                    svc = _BAD
                party = _text(row[i_party])
                if svc is None or svc is _BAD or not party:
                    before = left["no_date"]
                    _left_out(row, left, "no_date")
                    if left["no_date"] > before:
                        c["bad_dates"] += svc is _BAD
                        c["far"] += far
                    continue
                kept += 1
                svc_lo = svc if svc_lo is None or svc < svc_lo else svc_lo
                svc_hi = svc if svc_hi is None or svc > svc_hi else svc_hi
                claim_id = _text(row[i_claim])
                claim_id = claim_id.upper() if claim_id else None
                coverage = _cap(_text(row[i_cov]), 40)
                btype = _cap(_text(row[i_btype]), 20)
                current = mv(row[i_current])
                denial = mv(opt(row, i_denial))
                if i_total is not None:
                    open_amount = mv(row[i_total])
                elif buckets:
                    open_amount = (current or 0) + sum(mv(row[p]) or 0 for p in buckets)
                else:
                    open_amount = current
                exposure = open_amount if open_amount else (denial if denial is not None else open_amount)
                aging = aging_of(opt(row, i_aging))
                if aging and (aging_hi is None or aging > aging_hi):
                    aging_hi = aging
                if aging and current and (oldest_open is None or aging < oldest_open):
                    oldest_open = aging
                follow = dv(opt(row, i_follow))
                status = _cap(_text(row[i_status]), 60)
                if status is not None and len(status_seen) < 50 and _status_word(status) not in STATUS_CLASS:
                    status_seen.add(status[:40])
                cpt, product = code_of(row[i_cpt])
                note = _text(opt(row, i_note))
                name = _text(row[i_name])
                nk = name_key(salt, name)
                mk = mrn_key(salt, opt(row, i_mrn))
                link, pre = link_number(row[i_link])
                if pre is not None and pre.lower() not in {p.lower() for p in prefixes}:
                    prefixes.append(pre)
                    if len(prefixes) > 1:
                        raise ReportRefused("two_hosts", "Its claim links come from two EMA systems. Export the report from "
                                            "the practice's own EMA only.", "ar_report")
                insurance = party.lower().startswith("insur")
                if insurance and claim_id:
                    base = f"I|{claim_id}|{cpt or ''}|{svc}|{coverage or ''}"
                else:
                    tag = "U" if insurance else "P"
                    who = mk if mk is not None else nk
                    base = f"{tag}|" + _h16(salt, f"{tag}|{who}|{cpt or ''}|{svc}|{(coverage if insurance else btype) or ''}")
                k = occurrences[base] = occurrences.get(base, 0) + 1
                # what the billing note says was done (an appeal, a call, a resubmission...), never its words: the kinds
                # and an "allow N days" now; a date it states is resolved on the file's own as_of once the file is read
                n_kind = n_date = n_wait = None
                if note and classify is not None:
                    try:
                        said = classify(note, day)
                        n_kind, n_date, n_wait = ",".join(said.get("kinds") or []) or None, said.get("date"), said.get("wait_days")
                    except Exception:   # noqa: BLE001  (a note the classifier cannot read is a note with no kind)
                        n_kind = n_date = n_wait = None
                    if _NOTE_DATED.search(note) and len(dated) < 200_000:
                        dated[n] = note
                yield {
                    "row_no": n, "svc_year": svc[:4], "item_key": f"{base}|{k}", "coverage": coverage,
                    "party": _cap(party, 20), "claim_id": claim_id, "bill": bill_of_claim(claim_id), "link": link,
                    "balance_type": btype, "balance_cents": current, "denial_cents": denial, "aging_date": aging,
                    "follow_up": follow, "claim_action": _cap(_text(opt(row, i_action)), 80), "claim_status": status,
                    "status_class": status_class(status), "cpt": cpt, "product": 1 if product else 0,
                    "denial_category": _cap(_text(opt(row, i_dcat)), 200), "has_note": 1 if note else 0,
                    "note_chars": len(note) if note else 0, "note_hash": note_hash(salt, note),
                    "primary_payer": _cap(_text(row[i_primary]), 120), "secondary_payer": _cap(_text(opt(row, i_secondary)), 120),
                    "payer_group": None, "payer_kind": None, "svc": svc, "initials": _initials(name), "name_key": nk,
                    "mrn_key": mk, "exposure_cents": exposure, "facility": 1 if is_facility_claim(claim_id) else 0,
                    "note_kind": n_kind, "note_date": n_date, "note_wait": n_wait,
                }
        except (ReportRefused, ReadTimeout):
            raise
        except Exception as exc:        # noqa: BLE001  (a converter's message can hold a cell's text: never passed on)
            failed = type(exc).__name__
        finally:
            flush()
        if failed:
            raise ReportRefused("unreadable", f"Row {n:,} of the A/R report could not be read ({failed}). "
                                "Export it again from EMA.", 'ar_report')


# ---------------------------------------------------------------------------------------------------------- the catalog
def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_JSON_FIELDS = ("origin", "account", "columns", "missing", "unused")


def _catalog_row(conn: sqlite3.Connection, file_id: str) -> dict | None:
    cur = conn.execute("SELECT * FROM ema_report_files WHERE file_id=?", (file_id,))
    r = cur.fetchone()
    return dict(zip([d[0] for d in cur.description], r)) if r else None


def _catalog_put(conn: sqlite3.Connection, file_id: str, commit: bool = False, **fields: Any) -> None:
    """Insert or update a catalog row; on an update only the fields given change."""
    for k in _JSON_FIELDS:
        if k in fields and fields[k] is not None and not isinstance(fields[k], str):
            fields[k] = json.dumps(dict(fields[k]) if isinstance(fields[k], dict) else fields[k], default=str, sort_keys=True)
    have = conn.execute("SELECT 1 FROM ema_report_files WHERE file_id=?", (file_id,)).fetchone()
    if have is None:
        fields.setdefault("received_at", _now())
        fields.setdefault("attempts", 0)
        cols = ["file_id"] + list(fields)
        conn.execute(f"INSERT INTO ema_report_files ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                     [file_id] + list(fields.values()))
    elif fields:
        conn.execute(f"UPDATE ema_report_files SET {', '.join(f'{k}=?' for k in fields)} WHERE file_id=?",
                     list(fields.values()) + [file_id])
    if commit:
        conn.commit()


def _public(row: dict) -> dict:
    out = {k: v for k, v in row.items() if k != "origin"}
    for k in ("account", "columns", "missing", "unused"):
        if isinstance(out.get(k), str):
            try:
                out[k] = json.loads(out[k])
            except ValueError:
                pass
    return out


def record_waiting(conn: sqlite3.Connection, *, file_id: str, report: str, source_name: str, via: str, origin: dict,
                   reason: str, size: int | None = None) -> None:
    """Keep a report this host does not read yet (or must fetch again) as waiting, with where it came from, so it is read
    once it can be. A file already read, held, refused or discarded is left as it is."""
    o = dict(origin or {})
    if size is not None:
        o.setdefault("size", size)
    row = _catalog_row(conn, file_id)
    if row is None:
        _catalog_put(conn, file_id, report=report, source_name=source_name, via=via, origin=o, status="waiting", status_reason=reason)
    elif row["status"] in ("waiting", "reread"):
        old = json.loads(row["origin"]) if row.get("origin") else {}
        _catalog_put(conn, file_id, report=report or row["report"], source_name=source_name or row["source_name"], via=via,
                     origin={**old, **o}, status_reason=reason)
    conn.commit()


CANDIDATE_EXTS = (".xlsx", ".csv", ".tsv", ".txt", ".zip")      # the endings the router asks recognize() about first
# what a raw report CSV too large for a serverless host says, wherever it shows (the catalog row, the Data tab, Today's
# freshness line, the stale-report alert): exactly what to do about it
BIG_CSV_REASON = ("Too big to read on this site as it is (over 60 MB). Upload it on the Upload page (it compresses it), or put "
                  "a zipped copy in the same Dropbox folder.")


def provisional_id(content_hash: str) -> str:
    """The catalog id of a report recognised from its first bytes but never downloaded (a raw CSV too large for a
    serverless host): its SHA-256 is not known, so it goes by its Dropbox content hash until a copy of it is read."""
    return "dropbox:" + str(content_hash)


def supersede_waiting(conn: sqlite3.Connection, file_id: str, *, reason: str, replaced_by: str | None = None) -> bool:
    """Close a waiting entry that will never be read as it is (a zipped copy was read instead, or the file changed in
    Dropbox before it could be fetched again). Returns whether there was one."""
    row = _catalog_row(conn, file_id)
    if not row or row["status"] not in ("waiting", "reread"):
        return False
    _catalog_put(conn, file_id, status="superseded", status_reason=reason, replaced_by=replaced_by, commit=True)
    return True


def waiting_reason(conn: sqlite3.Connection, report: str) -> str | None:
    """What the newest file of a report that waits to be read says about it (a raw CSV too large for this site: what to
    do), for a freshness line or a stale-report alert; None when no file of that report waits."""
    try:
        r = conn.execute("SELECT status_reason FROM ema_report_files WHERE report=? AND status IN ('waiting','reread') "
                         "ORDER BY received_at DESC, file_id LIMIT 1", (report,)).fetchone()
    except sqlite3.Error:
        return None
    return (r[0] or None) if r else None


def waiting(conn: sqlite3.Connection, report: str | None = None) -> list[dict]:
    """The reports to read, oldest first, each with its origin (a server path, an upload id, or a Dropbox id, path,
    content hash and size)."""
    if not _table(conn, "ema_report_files"):
        return []
    sql = "SELECT * FROM ema_report_files WHERE status IN ('waiting','reread')"
    args: tuple = ()
    if report:
        sql += " AND report=?"
        args = (report,)
    cur = conn.execute(sql + " ORDER BY received_at, file_id", args)
    names = [d[0] for d in cur.description]
    out = []
    for r in cur.fetchall():
        d = dict(zip(names, r))
        try:
            origin = json.loads(d["origin"]) if d.get("origin") else {}
        except ValueError:
            origin = {}
        d = _public(d)
        d["origin"] = origin
        out.append(d)
    return out


def coverage(conn: sqlite3.Connection) -> dict:
    """What the reports on file cover: the boxes of the current Production files and their newest as_of, and per A/R
    service year the current snapshot (as_of, file, rows, aging window)."""
    if not _table(conn, "ema_report_files") or not _table(conn, "ema_ar"):
        return {"production": [], "production_as_of": None, "ar": {}}
    prod = [{"file_id": r[0], "scope": r[1], "svc_lo": r[2], "svc_hi": r[3], "posted_lo": r[4], "posted_hi": r[5], "as_of": r[6]}
            for r in conn.execute("""SELECT file_id, scope, svc_lo, svc_hi, posted_lo, posted_hi, as_of FROM ema_report_files
                                     WHERE report='production' AND status IN ('current','partly_replaced') ORDER BY as_of, file_id""")]
    ar: dict = {}
    for year, fid, as_of, n in conn.execute("SELECT svc_year, file_id, MAX(as_of), COUNT(*) FROM ema_ar GROUP BY svc_year, file_id"):
        acct = conn.execute("SELECT account FROM ema_report_files WHERE file_id=?", (fid,)).fetchone()
        try:
            window = (json.loads(acct[0]) or {}).get("aging_window") if acct and acct[0] else None
        except ValueError:
            window = None
        ar[year] = {"as_of": as_of, "file_id": fid, "rows": n, "aging_window": window}
    return {"production": prod, "production_as_of": max((b["as_of"] for b in prod if b["as_of"]), default=None), "ar": ar}


def _meta_json(conn: sqlite3.Connection, key: str, default: Any) -> Any:
    try:
        r = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    except sqlite3.OperationalError:
        return default
    if not r or not r[0]:
        return default
    try:
        return json.loads(r[0])
    except ValueError:
        return default


def catalog(conn: sqlite3.Connection) -> dict:
    """The reports on file for GET /api/ema/reports and the Data tab: every catalog row (never its origin), the held ones,
    how many wait, the switch, the claim address template, coverage, unknown status words and possible misread exports."""
    files: list[dict] = []
    if _table(conn, "ema_report_files"):
        cur = conn.execute("SELECT * FROM ema_report_files ORDER BY COALESCE(applied_at, received_at) DESC, file_id")
        names = [d[0] for d in cur.description]
        files = [_public(dict(zip(names, r))) for r in cur.fetchall()]
    return {"files": files, "held": [f for f in files if f["status"] == "held"],
            "waiting": sum(1 for f in files if f["status"] in ("waiting", "reread")),
            "switch": switch(), "reads": {r: reads(r) for r in REPORTS}, "claim_url": claim_url_template(conn),
            "coverage": coverage(conn), "unknown_status_words": _meta_json(conn, META_UNKNOWN_STATUS, []),
            "misread_suspects": _meta_json(conn, META_SUSPECTS, [])}


# --------------------------------------------------------------------------------------------------------------- ingest
def _result(status: str, report: str | None = None, file_id: str | None = None, **kw: Any) -> dict:
    out = {"status": status, "report": report, "file_id": file_id, "reason": None, "rows_read": 0, "rows_kept": 0,
           "rows_applied": 0, "live_rows": 0, "scope": None, "box": None, "as_of": None, "replaced": [], "account": None}
    out.update(kw)
    return out


def _member_id(path: str, member: str) -> str:
    with zipfile.ZipFile(path) as zf:
        info = zf.getinfo(member)
        limit = unpack_limit(info.compress_size)
        if info.file_size > limit:
            raise unpack_refusal(limit, info.compress_size)
        with zf.open(member) as m:
            return file_id_of(m)


def _member_size(path: str, member: str) -> int | None:
    try:
        with zipfile.ZipFile(path) as zf:
            return zf.getinfo(member).file_size
    except (KeyError, OSError, zipfile.BadZipFile):
        return None


def _names_of(path: str, rec: Recognized) -> tuple[str, str | None]:
    """(the report's own name, the zip it came in or None). A working copy read under another name (an upload) carries
    its own in rec.name; a gzipped file is named as it is unpacked."""
    if rec.member:
        return os.path.basename(rec.member), os.path.basename(path)
    if rec.name:
        return os.path.basename(rec.name), None
    base = os.path.basename(path)
    if rec.gz and base.lower().endswith(".gz"):
        base = base[:-3] or base
    return base, None


def report_id(path: str, rec: Recognized | None = None) -> str:
    """The catalog id of a report: the SHA-256 of its own bytes (a zip member's, a gzip's unpacked bytes), the same
    whichever way the same report arrives (an upload the page compressed, the file in Dropbox, a zipped copy)."""
    if rec is not None and rec.member:
        return _member_id(path, rec.member)
    gz = rec.gz if rec is not None else _is_gzip_head(_head_of(path, 2)[0])
    if gz:
        packed = _source_size(path)
        with gzip.open(path, "rb") as fh:
            return file_id_of(Capped(fh, unpack_limit(packed), packed=packed, report=rec.report if rec is not None else None))
    return file_id_of(path)


def _unpacked_size(path: str) -> int | None:
    try:
        n = 0
        packed = _source_size(path)
        with gzip.open(path, "rb") as fh:
            src = Capped(fh, unpack_limit(packed), packed=packed)
            for piece in iter(lambda: src.read(1 << 20), b""):
                n += len(piece)
        return n
    except (OSError, EOFError, zlib.error, ReportRefused):
        return None


def _created_at(path: str, rec: Recognized) -> str | None:
    if rec.fmt != "xlsx":
        return None
    try:
        if rec.member:
            with zipfile.ZipFile(path) as zf:
                return export_created(io.BytesIO(zf.read(rec.member)))
        return export_created(path)
    except Exception:       # noqa: BLE001  (the workbook does not say when it was made)
        return None


def _write_files_row(conn: sqlite3.Connection, file_id: str, path: str, rec: Recognized, *, kind: str, status: str,
                     rows_read: int, rows_applied: int, created: str | None, rows_kept: int) -> None:
    source_name, _container = _names_of(path, rec)
    size = mtime = None
    try:
        if rec.member:
            size = _member_size(path, rec.member)
        else:
            st = os.stat(path)
            size, mtime = st.st_size, datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(timespec="seconds")
    except OSError:
        pass
    conn.execute("INSERT OR REPLACE INTO files (sha256, source_path, source_name, size, mtime, kind, ingested_at, transactions, "
                 "claims, lines, duplicates, problems, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (file_id, path, source_name, size, mtime, kind, _now(), None, None, rows_applied,
                  max(0, rows_read - rows_applied), None, "ok" if status == "current" else status))
    # ema_sources is keyed by name and the weekly exports are looked up by it (EMA names every download alike): a report
    # goes in under a name no export can have, so it never replaces an export's row or its pull time
    conn.execute("INSERT OR REPLACE INTO ema_sources (source_name, sha256, family, created_at, rows, recorded_at) VALUES (?,?,?,?,?,?)",
                 (source_key(source_name), file_id, rec.report, created, rows_kept, _now()))


def source_key(name: str) -> str:
    """The ema_sources name a report is recorded under ('report:' and its file name), never an export's."""
    return "report:" + str(name or "")


def _record_refusal(store: Any, file_id: str, exc: ReportRefused, *, path: str, rec: Recognized | None, via: str,
                    origin: dict | None, log: Any) -> dict:
    conn = store.conn
    name = os.path.basename(rec.member) if rec and rec.member else os.path.basename(path)
    report = exc.report or (rec.report if rec else None)
    fields: dict = dict(report=report, source_name=name, via=via, origin=origin or {}, status="refused", status_reason=exc.message)
    if rec is not None:
        fields.update(format=rec.fmt, columns=list(rec.columns), missing=list(rec.missing), unused=list(rec.unused),
                      container=os.path.basename(path) if rec.member else None)
    row = _catalog_row(conn, file_id)
    if row is None or row["status"] in ("waiting", "reread", "held"):
        _catalog_put(conn, file_id, commit=True, **fields)
    log(f"  EMA report {name}: refused ({exc.code})")
    store.ema_changed = True
    return _result("refused", report, file_id, reason=exc.message, code=exc.code)


SOURCE_KINDS = ("file", "upload", "api")       # where an applied batch came from (amendment 10): recorded on each


def source_kind_of(via: str | None) -> str:
    """The source kind of a batch read by a route: an upload, an API pull, or a file (Dropbox, the server's mirror, the
    command line)."""
    return via if via in ("upload", "api") else "file"


def _waiting_reason(report: str) -> str:
    return (f"This host does not read the {REPORT_NAMES[report]} yet (REMITWATCH_EMA_REPORTS is '{switch()}'). "
            "It is read as soon as that setting allows.")


def ingest_file(store: Any, path: str, rec: Recognized | None = None, *, via: str, origin: dict | None = None,
                force: bool = False, ledger: Any = None, deadline: float | None = None, log: Any = print,
                name: str | None = None, wait_reason: str | None = None) -> dict:
    """Read one report (or a zip of reports) into RemitWatch: recognise it, record it in the catalog, and apply it (the
    A/R here, Production through ema_ledger.apply_production_file). The same bytes arriving again by any route return
    'already'. A report the switch does not read, or Production with no ledger given, is kept as 'waiting' with its
    origin. Rows the old code read from these same bytes as an export are removed first. A zip gives {'status': 'zip',
    'members': [...]}, one result per report member. Sets store.ema_changed when the catalog changed."""
    conn = store.conn
    ensure_schema(conn)
    if rec is None:
        if is_plain_zip(path):
            return _ingest_zip(store, path, via=via, origin=origin, force=force, ledger=ledger, deadline=deadline, log=log,
                               wait_reason=wait_reason)
        try:
            rec = recognize(path, name or os.path.basename(path))
        except ReportRefused as exc:
            try:
                fid = report_id(path)
            except ReportRefused:
                fid = file_id_of(path)          # a file that unpacks to far more than any export: its own bytes name it
            return _record_refusal(store, fid, exc, path=path, rec=None, via=via, origin=origin, log=log)
        if rec is None:
            return _result("not_report")
    if name and not rec.member:
        rec = replace(rec, name=os.path.basename(name))
    if rec.gz:
        # a gzip set aside before (it unpacked to far more than any export) is known by its packed bytes: not unpacked again
        packed_id = file_id_of(path)
        row = _catalog_row(conn, packed_id)
        if row and row.get("status") == "refused":
            return _result("refused", rec.report, packed_id, reason=row.get("status_reason"))
        try:
            file_id = report_id(path, rec)
        except ReportRefused as exc:
            return _record_refusal(store, packed_id, exc, path=path, rec=rec, via=via, origin=origin, log=log)
    else:
        try:
            file_id = report_id(path, rec)
        except ReportRefused as exc:        # a zip member that says it unpacks to far more than any export
            return _record_refusal(store, file_id_of(path), exc, path=path, rec=rec, via=via, origin=origin, log=log)
    res = _ingest_one(store, path, rec, file_id, via=via, origin=origin, force=force, ledger=ledger, deadline=deadline, log=log,
                      wait_reason=wait_reason)
    if rec.gz and rec.report == "production" and res.get("status") in ("current", "superseded"):
        # a raw CSV a serverless host left waiting (too large to read there) is closed by this gzipped copy of it
        _supersede_raw_twins(conn, _names_of(path, rec)[0], _unpacked_size(path), file_id)
    return res


def _ingest_zip(store: Any, path: str, *, via: str, origin: dict | None, force: bool, ledger: Any, deadline: float | None,
                log: Any, wait_reason: str | None = None) -> dict:
    name = os.path.basename(path)
    try:
        recs = recognize_zip(path, name)
    except ReportRefused as exc:
        # a zip holding a zip, or one that unpacks to far more than any export: set aside under its own bytes, and listed
        # on the Data tab with the reason (never skipped in silence)
        fid = file_id_of(path)
        _record_refusal(store, fid, exc, path=path, rec=None, via=via, origin=origin, log=log)
        return {"status": "refused", "code": exc.code, "reason": exc.message, "members": [], "ignored": [], "file_id": fid}
    members = []
    for rec in recs:
        fid = _member_id(path, rec.member)
        res = _ingest_one(store, path, rec, fid, via=via, origin=dict(origin or {}, member=rec.member), force=force,
                          ledger=ledger, deadline=deadline, log=log, wait_reason=wait_reason)
        members.append(res)
        if rec.report == "production" and res.get("status") in ("current", "superseded"):
            _supersede_raw_twins(store.conn, os.path.basename(rec.member), _member_size(path, rec.member), fid)
    taken = {r.member for r in recs}
    try:
        with zipfile.ZipFile(path) as zf:
            others = [i.filename for i in zf.infolist()
                      if not i.is_dir() and i.filename not in taken and "__MACOSX" not in i.filename
                      and not os.path.basename(i.filename).startswith((".", "~$"))]
    except (OSError, zipfile.BadZipFile):
        others = []
    # 'others': the members that are no report (a week of remittances, the weekly exports), in full, for the router to read
    # each on its own (dropbox_sync.ingest_new_files); 'ignored' names them for the page
    return {"status": "zip", "members": members, "ignored": [os.path.basename(o) for o in others[:50]],
            "others": others[:ZIP_OTHERS_MAX]}


def _supersede_raw_twins(conn: sqlite3.Connection, name: str, size: int | None, file_id: str) -> None:
    """A raw CSV left waiting because it was too large for this site is closed once a zipped copy of the same name and
    size has been read."""
    if size is None:
        return
    for w in waiting(conn, "production"):
        if w["file_id"] != file_id and w.get("source_name") == name and (w.get("origin") or {}).get("size") == size:
            supersede_waiting(conn, w["file_id"], reason="A zipped copy of this file was read instead.", replaced_by=file_id)


def _ingest_one(store: Any, path: str, rec: Recognized, file_id: str, *, via: str, origin: dict | None, force: bool,
                ledger: Any, deadline: float | None, log: Any, wait_reason: str | None = None) -> dict:
    conn = store.conn
    source_name, container = _names_of(path, rec)
    row = _catalog_row(conn, file_id)
    if row:
        st = row["status"]
        if st in DONE_STATUSES or st == "discarded":
            if rec.report == "production" and st in DONE_STATUSES:
                _restore_rows(store, file_id, path, rec, via=via, origin=origin)
            return _result("already", rec.report, file_id, reason=st, as_of=row.get("as_of"))
        if st == "refused":
            return _result("refused", rec.report, file_id, reason=row.get("status_reason"))
        if st == "held":
            if row.get("decision") == "apply":
                force = True
            elif not force:
                return _result("held", rec.report, file_id, reason=row.get("status_reason"))
    if confirm_misread(store, file_id, rec):
        log(f"  EMA report {source_name}: rows read from it before as another export were removed")
    if not reads(rec.report):
        record_waiting(conn, file_id=file_id, report=rec.report, source_name=source_name, via=via, origin=origin or {},
                       reason=_waiting_reason(rec.report))
        _catalog_put(conn, file_id, commit=True, container=container, format=rec.fmt, columns=list(rec.columns),
                     missing=list(rec.missing), unused=list(rec.unused))
        store.ema_changed = True
        return _result("waiting", rec.report, file_id, reason=_waiting_reason(rec.report))
    if rec.missing:
        return _record_refusal(store, file_id, ReportRefused("missing_columns", _missing_message(rec), rec.report), path=path,
                               rec=rec, via=via, origin=origin, log=log)
    if rec.report == "ar_report":
        return apply_ar(store, path, rec, file_id=file_id, via=via, origin=origin, force=force, log=log)
    return _ingest_production(store, path, rec, file_id, via=via, origin=origin, force=force, ledger=ledger, deadline=deadline,
                              log=log, wait_reason=wait_reason)


def _box_dict(box: Any) -> dict:
    if box is None:
        return {}
    if isinstance(box, dict):
        return box
    return {k: getattr(box, k, None) for k in ("file_id", "scope", "svc_lo", "svc_hi", "posted_lo", "posted_hi", "as_of")}


def _json_safe(d: Any) -> dict:
    if not isinstance(d, dict):
        return {}
    return json.loads(json.dumps(dict(d), default=str))


def _ingest_production(store: Any, path: str, rec: Recognized, file_id: str, *, via: str, origin: dict | None, force: bool,
                       ledger: Any, deadline: float | None, log: Any, wait_reason: str | None = None) -> dict:
    conn = store.conn
    source_name, container = _names_of(path, rec)
    try:
        from . import ema_ledger        # noqa: PLC0415  (Production needs the ledger; until it ships the file waits)
    except Exception:                   # noqa: BLE001  (missing, or not importable on this build: the file waits)
        ema_ledger = None
    if ema_ledger is None or ledger is None or not hasattr(ema_ledger, "apply_production_file"):
        reason = wait_reason or "Production is read once the posting ledger is set up on this host."
        record_waiting(conn, file_id=file_id, report="production", source_name=source_name, via=via, origin=origin or {},
                       reason=reason)
        _catalog_put(conn, file_id, commit=True, container=container, format=rec.fmt, columns=list(rec.columns),
                     missing=list(rec.missing), unused=list(rec.unused))
        store.ema_changed = True
        return _result("waiting", "production", file_id, reason=reason)
    lg = ledger() if (callable(ledger) and not hasattr(ledger, "conn")) else ledger
    new_row = _catalog_row(conn, file_id) is None
    try:
        res = dict(ema_ledger.apply_production_file(store, path, rec, file_id=file_id, via=via, origin=origin, force=force,
                                                    ledger=lg, deadline=deadline, serverless=runtime.serverless(), log=log,
                                                    source_name=source_name, source_kind=source_kind_of(via)) or {})
    except ReportRefused as exc:
        return _record_refusal(store, file_id, exc, path=path, rec=rec, via=via, origin=origin, log=log)
    status = res.get("status") or "refused"
    box = _box_dict(res.get("box"))
    acct = _json_safe(res.get("account"))
    if status == "already":
        if res.get("duplicate_of"):
            # the same rows as a file on file, saved again (another save time, zip times or line endings): nothing changes
            supersede_waiting(conn, file_id, reason=res.get("reason") or "The same rows as a file already read.",
                              replaced_by=res["duplicate_of"])
            log(f"  EMA report {source_name}: already read (the same rows as a file on file)")
        else:
            _restore_rows(store, file_id, path, rec, via=via, origin=origin, ledger=lg)
        return _result("already", "production", file_id, **{k: v for k, v in res.items() if k not in ("status", "report", "file_id")})
    if status == "waiting":
        record_waiting(conn, file_id=file_id, report="production", source_name=source_name, via=via, origin=origin or {},
                       reason=res.get("reason") or "Waiting to be read.")
    else:
        created = _created_at(path, rec)
        fields: dict = dict(report="production", source_name=source_name, container=container, via=via, origin=origin or {},
                            format=rec.fmt, columns=list(rec.columns), missing=list(rec.missing), unused=list(rec.unused),
                            scope=res.get("scope") or box.get("scope"),
                            svc_lo=box.get("svc_lo") or acct.get("svc_lo"), svc_hi=box.get("svc_hi") or acct.get("svc_hi"),
                            posted_lo=box.get("posted_lo") or acct.get("posted_lo"),
                            posted_hi=box.get("posted_hi") or acct.get("posted_hi"),
                            as_of=res.get("as_of") or box.get("as_of") or acct.get("as_of"), created_at=created,
                            rows_read=res.get("rows_read") or acct.get("rows_read") or 0,
                            rows_kept=res.get("rows_kept") or acct.get("rows_kept") or 0,
                            rows_applied=res.get("rows_applied") or 0, live_rows=res.get("live_rows") or 0,
                            account=acct, status=status, status_reason=res.get("reason"),
                            source_kind=source_kind_of(via), content_id=res.get("content_id"))
        if new_row and res.get("received_at"):
            fields["received_at"] = res["received_at"]          # the arrival the ledger orders files by
        if res.get("links") is not None:
            fields["links"] = res.get("links")
        if status in ("current", "superseded"):
            fields["applied_at"] = _now()
        _catalog_put(conn, file_id, **fields)
        for old, st in res.get("replaced") or []:
            if old != file_id and _catalog_row(conn, old) is not None:
                _catalog_put(conn, old, status=st, replaced_by=file_id)
        if status in ("current", "superseded"):
            _write_files_row(conn, file_id, path, rec, kind="ema:production", status=status,
                             rows_read=fields["rows_read"], rows_applied=fields["rows_applied"], created=created,
                             rows_kept=fields["rows_kept"])
        conn.commit()
        if status in ("current", "superseded"):
            _learn_prefix_from(conn, path, rec)
    store.ema_changed = True
    log(f"  EMA report {source_name}: {status}" + (f", {res.get('rows_applied') or 0:,} rows applied" if status == "current" else ""))
    out = _result(status, "production", file_id)
    out.update({k: v for k, v in res.items() if k not in ("report", "file_id")})
    out["account"] = acct
    return out


def _restore_rows(store: Any, file_id: str, path: str, rec: Recognized, *, via: str | None, origin: dict | None,
                  ledger: Any = None) -> None:
    """A Production file the ledger already holds, met again after a run that applied it and stopped before saving the
    store: its catalog row comes back from the ledger when the store lost it (ema_ledger.sync_catalog), with where the
    file is kept (its origin: a later re-read fetches it from there) and how it came when the restored row lacks them;
    then its files and ema_sources rows (_repair_store_rows)."""
    conn = store.conn
    row = _catalog_row(conn, file_id)
    if row is None and ledger is not None:
        from . import ema_ledger        # noqa: PLC0415
        ema_ledger.sync_catalog(store, ledger, [file_id])
        row = _catalog_row(conn, file_id)
    if row is None or row.get("status") not in DONE_STATUSES:
        return
    try:
        have = json.loads(row.get("origin") or "{}") if isinstance(row.get("origin"), str) else dict(row.get("origin") or {})
    except ValueError:
        have = {}
    fields: dict = {}
    if origin and not have:
        fields["origin"] = dict(origin)
    if via and not row.get("via"):
        fields["via"] = via
    if fields:
        _catalog_put(conn, file_id, commit=True, **fields)
        store.ema_changed = True
    _repair_store_rows(conn, file_id, path, rec)


def _repair_store_rows(conn: sqlite3.Connection, file_id: str, path: str, rec: Recognized) -> None:
    """A Production file the ledger already holds: a run that died after the ledger's commit and before the store's left
    the store without its files and ema_sources rows and without the claim address prefix. The catalog row is restored
    from the ledger (ema_ledger.sync_catalog); the rest is written here from it, so the router stops offering the file
    and the Data tab lists it."""
    row = _catalog_row(conn, file_id)
    if not row or row.get("status") not in DONE_STATUSES:
        return
    have_file = conn.execute("SELECT 1 FROM files WHERE sha256=?", (file_id,)).fetchone() is not None
    have_src = conn.execute("SELECT 1 FROM ema_sources WHERE sha256=?", (file_id,)).fetchone() is not None
    if not (have_file and have_src):
        _write_files_row(conn, file_id, path, rec, kind="ema:production", status=row["status"],
                         rows_read=int(row.get("rows_read") or 0), rows_applied=int(row.get("rows_applied") or 0),
                         created=row.get("created_at"), rows_kept=int(row.get("rows_kept") or 0))
        conn.commit()
    if conn.execute("SELECT 1 FROM meta WHERE key=?", (META_PREFIX,)).fetchone() is None:
        _learn_prefix_from(conn, path, rec)


def _learn_prefix_from(conn: sqlite3.Connection, path: str, rec: Recognized, rows: int = 2000) -> None:
    """Learn the claim address prefix from the first rows that carry a claim link (a Production file is not read twice)."""
    i = rec.index.get("claim link")
    if i is None:
        return
    seen: list[str] = []
    try:
        with open_report(path, rec) as fh:
            for k, row in enumerate(_rows(fh, rec)):
                if k >= rows:
                    break
                if i < len(row):
                    _n, pre = link_number(row[i])
                    if pre and pre.lower() not in {s.lower() for s in seen}:
                        seen.append(pre)
        learn_claim_prefix(conn, seen)
    except (ReportRefused, OSError, ValueError, zipfile.BadZipFile):
        pass


# ---------------------------------------------------------------------------------------------------------- the A/R
_AR_COLS = None


def _ar_cols() -> list[str]:
    global _AR_COLS
    if _AR_COLS is None:
        _AR_COLS = [n for n, _d in _expected_columns()["ema_ar"]]
    return _AR_COLS


# not the export's own rows (or worked out from them: what a billing note says, already in its note_hash): left out of the
# snapshot's content id, so the same export read by another build is still 'already'
AR_CONTENT_SKIP = ("file_id", "as_of", "payer_group", "payer_kind", "note_kind", "note_date", "note_wait")


def _stage_ar_rows(conn: sqlite3.Connection, rows: Iterable[dict], file_id: str) -> tuple[int, str]:
    """Stage A/R rows (ema_ar's columns but file_id and as_of, as iter_ar yields them) in the temporary table
    ema_ar_stage. Returns how many, and the snapshot's content id: a hash of its rows in order (what the same export saved
    again has too, whatever its save time or line endings)."""
    cols = _ar_cols()
    conn.execute("DROP TABLE IF EXISTS temp.ema_ar_stage")
    conn.execute("CREATE TEMP TABLE ema_ar_stage AS SELECT * FROM main.ema_ar WHERE 0")
    conn.execute("CREATE INDEX temp.ix_ar_stage ON ema_ar_stage(svc_year, item_key)")
    sql = f"INSERT INTO temp.ema_ar_stage ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"
    keep = [c for c in cols if c not in AR_CONTENT_SKIP]
    digest = hashlib.sha256()
    batch: list[tuple] = []
    n = 0
    for r in rows:
        r = dict(r)
        r["file_id"] = file_id
        digest.update(repr(tuple(r.get(c) for c in keep)).encode())
        batch.append(tuple(r.get(c) for c in cols))
        n += 1
        if len(batch) >= 1000:
            conn.executemany(sql, batch)
            batch.clear()
    if batch:
        conn.executemany(sql, batch)
    return n, digest.hexdigest()


def _stage_ar_groups(conn: sqlite3.Connection, as_of: str | None) -> None:
    payer = ("CASE WHEN lower(coverage) LIKE 'secondary%' AND secondary_payer IS NOT NULL THEN secondary_payer "
             "ELSE primary_payer END")
    conn.execute(f"UPDATE temp.ema_ar_stage SET as_of=?, payer_group=rw_payer_group({payer}), payer_kind=rw_payer_kind({payer})",
                 (as_of,))


def _stage_ar(conn: sqlite3.Connection, path: str, rec: Recognized, salt: bytes, file_id: str) -> Account:
    """Read an A/R file into the temporary table ema_ar_stage (rows as ema_ar will hold them, as_of and payer groups set)."""
    acct = Account()
    _n, acct.content_id = _stage_ar_rows(conn, iter_ar(path, rec, salt=salt, account=acct), file_id)
    _stage_ar_groups(conn, acct.get("as_of"))
    _stage_ar_notes(conn, acct, acct.get("as_of"))
    return acct


def _drop_stage(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("DROP TABLE IF EXISTS temp.ema_ar_stage")
    except sqlite3.Error:
        pass


def _order(conn: sqlite3.Connection, file_id: str) -> tuple:
    r = conn.execute("SELECT as_of, created_at, received_at FROM ema_report_files WHERE file_id=?", (file_id,)).fetchone()
    if r is None:
        a = conn.execute("SELECT MAX(as_of) FROM ema_ar WHERE file_id=?", (file_id,)).fetchone()
        return ((a[0] if a else None) or "", "", "", file_id)
    return (r[0] or "", r[1] or "", r[2] or "", file_id)


def _day(d: str | None) -> str:
    try:
        x = date.fromisoformat(str(d)[:10])
        return f"{x:%b} {x.day}, {x.year}"
    except (TypeError, ValueError):
        return str(d)


def format_holds(acct: dict, report: str = "ar_report") -> list[tuple[str, str]]:
    """The format hold of a report read whole: more than 1% of its rows (and more than 20) left out because a date could
    not be read or its cells do not line up with the column names, or a file whose last line was cut short. A
    re-export in a changed layout would otherwise replace the year's rows with what could be read."""
    out: list[tuple[str, str]] = []
    left = acct.get("left") or {}
    read = int(acct.get("rows_read") or 0)
    bad = int(left.get("no_date") or 0) + int(left.get("misaligned") or 0)
    if read and bad > max(FORMAT_HOLD_MIN, FORMAT_HOLD_SHARE * read):
        parts = []
        if left.get("no_date"):
            parts.append(f"{int(left['no_date']):,} rows have no readable date")
        if left.get("misaligned"):
            parts.append(f"{int(left['misaligned']):,} rows have more or fewer cells than the column names")
        out.append(("format", f"The layout or format changed: {' and '.join(parts)}, of {read:,}. Export it again from EMA "
                              "without opening it in another program."))
    if acct.get("cut_short"):
        out.append(("cut_short", f"It looks cut short: its last line ends without a line break. Copy or export the "
                                 f"{REPORT_NAMES.get(report, 'report')} again."))
    return out


def production_format_holds(acct: dict) -> list[tuple[str, str]]:
    """The holds a Production file meets on what its reader counted alone (the ledger adds the ones that compare it with
    the files on file): H1 when more than 1% of its rows were left out for a date that cannot be read or cells that do
    not line up with the column names, more than 0.5% break EMA's own money totals, or the file was cut short; H7 when
    more than max(10, 0.5% of the rows with a check number) carry a check number a spreadsheet changed (scientific
    notation): those checks match no remittance, so every payment on them would read as not posted in EMA."""
    out: list[tuple[str, str]] = []
    left = acct.get("left") or {}
    read = int(acct.get("rows_read") or acct.get("rows") or 0)
    no_date = int(acct.get("no_date") if acct.get("no_date") is not None else left.get("no_date") or 0)
    misaligned = int(acct.get("misaligned") if acct.get("misaligned") is not None else left.get("misaligned") or 0)
    breaks = int(acct.get("identity_breaks") or 0)
    if read and (no_date + misaligned > H1_NO_DATE_SHARE * read or breaks > H1_BREAK_SHARE * read):
        parts = [f"{no_date:,} rows have no readable date"]
        if misaligned:
            parts.append(f"{misaligned:,} rows have more or fewer cells than the column names")
        parts.append(f"{breaks:,} rows break EMA's own money totals")
        out.append(("H1", f"The layout or format changed: {', '.join(parts[:-1])} and {parts[-1]}. Export it again from EMA "
                          "without opening it in Excel."))
    if acct.get("cut_short"):
        out.append(("H1", "It looks cut short: its last line ends without a line break. Copy or export it again."))
    damaged = int(acct.get("damaged_checks") or 0)
    with_check = int(acct.get("check_rows") or 0)
    if damaged > max(H7_DAMAGED_MIN, H7_DAMAGED_SHARE * with_check):
        out.append(("H7", f"{damaged:,} check numbers were changed by a spreadsheet (written as 9.1E+11 and the like): export "
                          "it again from EMA without opening it in Excel."))
    return out


def _years_label(years: Sequence[str]) -> str:
    """'2026', '2025-2026', or '2023, 2026' when the years are not one run."""
    if not years:
        return ""
    if len(years) == 1:
        return years[0]
    run = all(int(b) == int(a) + 1 for a, b in zip(years, years[1:]))
    return f"{years[0]}-{years[-1]}" if run else ", ".join(years)


def _stage_years(conn: sqlite3.Connection) -> list[str]:
    """The service years an A/R file has rows for (never the years between them it has none of)."""
    return [r[0] for r in conn.execute("SELECT DISTINCT svc_year FROM temp.ema_ar_stage WHERE svc_year IS NOT NULL "
                                       "ORDER BY svc_year")]


def _ar_holds(conn: sqlite3.Connection, years: Sequence[str], current_of: dict, as_of: str) -> list[tuple[str, str]]:
    """The shrink hold: for a year whose current snapshot is at most AR_HOLD_MAX_GAP days older, compare only the lines an
    honest re-export must still list (a denial amount, or an aging date within 30 minus the gap days of the new as_of).
    Held when the new file lists fewer than 40% of them, or lacks a responsible party, coverage type or primary payer that
    held at least 5% of them."""
    out: list[tuple[str, str]] = []
    for y in years:
        cur = current_of.get(y)
        if not cur:
            continue
        cur_as_of = conn.execute("SELECT MAX(as_of) FROM main.ema_ar WHERE svc_year=? AND file_id=?", (y, cur)).fetchone()[0]
        if not cur_as_of or not as_of:
            continue
        gap = (date.fromisoformat(as_of) - date.fromisoformat(cur_as_of)).days
        if gap < 0 or gap > AR_HOLD_MAX_GAP:
            continue
        must = ("FROM main.ema_ar m WHERE m.svc_year=? AND m.file_id=? AND (COALESCE(m.denial_cents,0) <> 0 OR "
                "(m.aging_date IS NOT NULL AND julianday(?) - julianday(m.aging_date) <= ?))")
        args = (y, cur, as_of, 30 - gap)
        n_must = conn.execute(f"SELECT COUNT(*) {must}", args).fetchone()[0]
        if n_must < AR_HOLD_MIN_ROWS:
            continue
        listed = conn.execute(f"SELECT COUNT(*) {must} AND m.item_key IN (SELECT item_key FROM temp.ema_ar_stage WHERE svc_year=?)",
                              args + (y,)).fetchone()[0]
        if listed < AR_HOLD_SHARE * n_must:
            out.append(("shrink", f"It lists {100 * listed // n_must}% of the {n_must:,} lines of {y} that the A/R of "
                                  f"{_day(cur_as_of)} shows and a new export must still list. Was it filtered by payer, "
                                  "provider or location?"))
        for col, label in (("party", "responsible party"), ("coverage", "coverage type"), ("primary_payer", "primary payer")):
            for val, k in conn.execute(f"SELECT m.{col}, COUNT(*) {must} AND m.{col} IS NOT NULL GROUP BY m.{col}", args).fetchall():
                if k < AR_HOLD_VALUE_SHARE * n_must:
                    continue
                if not conn.execute(f"SELECT 1 FROM temp.ema_ar_stage WHERE svc_year=? AND {col}=? LIMIT 1", (y, val)).fetchone():
                    out.append(("missing_value", f"It has no {y} line for the {label} {val}, which held {100 * k // n_must}% of "
                                                 f"those lines on {_day(cur_as_of)}. Was it filtered?"))
    return out


_NOTE = "excluded.last_note_hash IS NOT NULL AND excluded.last_note_hash IS NOT ema_ar_items.last_note_hash"
_FOLLOW = "excluded.last_follow_up IS NOT NULL AND excluded.last_follow_up IS NOT ema_ar_items.last_follow_up"
_ACTION = "excluded.last_action IS NOT NULL AND excluded.last_action IS NOT ema_ar_items.last_action"
_RESUB = ("rw_status_class(ema_ar_items.last_status) IN ('denied','rejected') AND "
          "rw_status_class(excluded.last_status) IN ('pending','not_sent')")
_ANY = f"(({_NOTE}) OR ({_FOLLOW}) OR ({_ACTION}) OR ({_RESUB}))"
_HOW = (f"rtrim(CASE WHEN {_NOTE} THEN 'note,' ELSE '' END || CASE WHEN {_FOLLOW} THEN 'follow_up,' ELSE '' END || "
        f"CASE WHEN {_ACTION} THEN 'action,' ELSE '' END || CASE WHEN {_RESUB} THEN 'resubmitted,' ELSE '' END, ',')")
_HISTORY_SQL = f"""
INSERT INTO ema_ar_items (item_key, party, coverage, claim_id, link, cpt, svc, first_seen, last_seen, snapshots, absent_since,
    first_status, last_status, last_balance_cents, max_balance_cents, last_action, last_note_hash, note_changed,
    last_follow_up, worked_on, worked_how)
SELECT item_key, party, coverage, claim_id, link, cpt, svc, as_of, as_of, 1, NULL, claim_status, claim_status,
       exposure_cents, exposure_cents, claim_action, note_hash, NULL, follow_up, NULL, NULL
FROM temp.ema_ar_stage WHERE svc_year = ?
ON CONFLICT(item_key) DO UPDATE SET
    party = excluded.party, coverage = excluded.coverage, claim_id = excluded.claim_id,
    link = COALESCE(excluded.link, ema_ar_items.link), cpt = excluded.cpt, svc = excluded.svc,
    last_seen = excluded.last_seen, snapshots = COALESCE(ema_ar_items.snapshots, 0) + 1, absent_since = NULL,
    last_status = excluded.last_status, last_balance_cents = excluded.last_balance_cents,
    max_balance_cents = CASE WHEN excluded.max_balance_cents IS NULL THEN ema_ar_items.max_balance_cents
                             WHEN ema_ar_items.max_balance_cents IS NULL THEN excluded.max_balance_cents
                             ELSE MAX(ema_ar_items.max_balance_cents, excluded.max_balance_cents) END,
    last_action = excluded.last_action, last_note_hash = excluded.last_note_hash,
    note_changed = CASE WHEN {_NOTE} THEN excluded.last_seen ELSE ema_ar_items.note_changed END,
    last_follow_up = excluded.last_follow_up,
    worked_on = CASE WHEN {_ANY} THEN excluded.last_seen ELSE ema_ar_items.worked_on END,
    worked_how = CASE WHEN {_ANY} THEN {_HOW} ELSE ema_ar_items.worked_how END
"""
_LINKS_SQL = """
INSERT INTO ema_claim_links (claim_id, link, bill, first_seen, last_seen)
SELECT claim_id, MAX(link), MAX(bill), MIN(as_of), MAX(as_of) FROM temp.ema_ar_stage
WHERE svc_year = ? AND claim_id IS NOT NULL AND link IS NOT NULL GROUP BY claim_id
ON CONFLICT(claim_id) DO UPDATE SET link = excluded.link, bill = excluded.bill,
    first_seen = MIN(ema_claim_links.first_seen, excluded.first_seen), last_seen = MAX(ema_claim_links.last_seen, excluded.last_seen)
"""


def apply_ar(store: Any, path: str, rec: Recognized, *, file_id: str, via: str, origin: dict | None = None, force: bool = False,
             log: Any = print) -> dict:
    """Read an A/R file and apply it as a snapshot: the file adapter of apply_ar_snapshot (it reads the rows with iter_ar
    and hands them on, with the file's name, layout and origin)."""
    conn = store.conn
    ensure_schema(conn)
    salt = key_salt(conn)
    register_functions(conn, salt)
    if rec.missing:
        return _record_refusal(store, file_id, ReportRefused("missing_columns", _missing_message(rec), "ar_report"), path=path,
                               rec=rec, via=via, origin=origin, log=log)
    acct = Account()
    return apply_ar_snapshot(store, file_id, None, iter_ar(path, rec, salt=salt, account=acct), source_kind=source_kind_of(via),
                             account=acct, path=path, rec=rec, via=via, origin=origin, force=force, log=log)


class _ConnStore:
    """A store-like holder for a bare connection (apply_ar_snapshot's caller may hold only that)."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.ema_changed = False


def apply_ar_snapshot(target: Any, source_id: str, as_of: str | None, rows: Iterable[dict], *, source_kind: str,
                      account: Account | None = None, path: str | None = None, rec: Recognized | None = None,
                      via: str | None = None, origin: dict | None = None, force: bool = False, log: Any = print,
                      source_name: str | None = None) -> dict:
    """The seam every source of A/R rows goes through (amendment 10): make these rows (ema_ar's columns but file_id and
    as_of, as iter_ar yields them) the current snapshot of each service year they span for which they are newer than the
    snapshot on file (order: as_of, the workbook's creation time, when it arrived, its id); an older snapshot never
    replaces a newer one, and is superseded for that year. For each year it takes: its rows replace the year's rows in
    ema_ar, the item history is brought forward (first and last seen, absent since, worked marks), and every claim id to
    claim number pair is kept in ema_claim_links. A snapshot that lists far fewer of the lines a re-export must still show
    is held until a person decides (force skips holds, never refusals). Every snapshot is dated (as_of: given, or the
    reader's newest date) and records `source_kind` ('file', 'upload' or 'api') and a content id: the same rows as a
    snapshot on file (the export saved again) are 'already' and change nothing. `target` is the store (or a bare
    connection). A file's reader passes its `account`, `path` and `rec`; an API pull passes none of them."""
    if source_kind not in SOURCE_KINDS:
        raise ValueError("source_kind is file, upload or api")
    store = target if hasattr(target, "conn") else _ConnStore(target)
    conn = store.conn
    ensure_schema(conn)
    salt = key_salt(conn)
    register_functions(conn, salt)
    acct = account if account is not None else Account()
    if rec is not None and path is not None:
        source_name, container = _names_of(path, rec)
    else:
        source_name, container = source_name or "A/R snapshot", None
    base: dict = dict(report="ar_report", source_name=source_name, container=container, via=via or source_kind,
                      origin=origin or {}, source_kind=source_kind)
    if rec is not None:
        base.update(format=rec.fmt, columns=list(rec.columns), missing=list(rec.missing), unused=list(rec.unused))
    where = path or source_name
    try:
        staged, content_id = _stage_ar_rows(conn, rows, source_id)
    except ReportRefused as exc:
        conn.rollback()
        _drop_stage(conn)
        return _record_refusal(store, source_id, exc, path=where, rec=rec, via=via or source_kind, origin=origin, log=log)
    if account is None:
        lo, hi = conn.execute("SELECT MIN(svc), MAX(svc) FROM temp.ema_ar_stage").fetchone()
        acct.update(rows_read=staged, rows_kept=staged, svc_lo=lo, svc_hi=hi, as_of=as_of)
    as_of = as_of or acct.get("as_of")
    acct["as_of"] = as_of
    _stage_ar_groups(conn, as_of)
    _stage_ar_notes(conn, acct, as_of)
    try:
        if not acct.get("rows_kept"):
            conn.rollback()
            return _record_refusal(store, source_id, ReportRefused("empty", "It holds no line with a service date and a responsible "
                                                                   "party.", "ar_report"), path=where, rec=rec,
                                   via=via or source_kind, origin=origin, log=log)
        dup = conn.execute(f"""SELECT file_id, applied_at FROM ema_report_files WHERE report='ar_report' AND content_id=?
                               AND file_id<>? AND status IN ({','.join('?' * len(DONE_STATUSES))}) LIMIT 1""",
                           (content_id, source_id, *DONE_STATUSES)).fetchone()
        if dup is not None:
            # the same rows as a snapshot on file, saved again (another save time or line endings): nothing changes
            conn.rollback()
            reason = f"It holds the same rows as the A/R read on {_day(dup[1])} (saved again): nothing changed."
            supersede_waiting(conn, source_id, reason=reason, replaced_by=dup[0])
            log(f"  EMA report {source_name}: already read (the same rows as an A/R on file)")
            return _result("already", "ar_report", source_id, reason=reason, duplicate_of=dup[0], content_id=content_id,
                           rows_read=acct.get("rows_read") or 0, as_of=as_of)
        years = _stage_years(conn)          # a year the file has no row for keeps its own snapshot
        row = _catalog_row(conn, source_id)
        received = (row or {}).get("received_at") or _now()
        created = _created_at(path, rec) if (path and rec is not None) else None
        mine = (as_of or "", created or "", received, source_id)
        take, behind, current_of = [], [], {}
        for y in years:
            cur = conn.execute("SELECT file_id FROM main.ema_ar WHERE svc_year=? LIMIT 1", (y,)).fetchone()
            if cur is None:
                take.append(y)
            elif cur[0] != source_id and mine > _order(conn, cur[0]):
                take.append(y)
                current_of[y] = cur[0]
            elif cur[0] != source_id:
                behind.append(y)
        scope = _years_label(years)
        links = conn.execute("SELECT COUNT(DISTINCT link) FROM temp.ema_ar_stage WHERE link IS NOT NULL").fetchone()[0]
        info = dict(scope=scope, svc_lo=acct.get("svc_lo"), svc_hi=acct.get("svc_hi"), as_of=as_of, created_at=created,
                    received_at=received, rows_read=acct.get("rows_read") or 0, rows_kept=acct.get("rows_kept") or 0, links=links,
                    account=dict(acct), content_id=content_id)
        holds = [] if force else format_holds(acct) + _ar_holds(conn, take, current_of, as_of)
        if holds:
            reason = " ".join(m for _c, m in holds)
            _catalog_put(conn, source_id, **base, **info, status="held", status_reason=reason)
            conn.commit()
            store.ema_changed = True
            log(f"  EMA report {source_name}: held ({', '.join(sorted({c for c, _m in holds}))})")
            return _result("held", "ar_report", source_id, reason=reason, rows_read=info["rows_read"], rows_kept=info["rows_kept"],
                           scope=scope, as_of=as_of, account=dict(acct), content_id=content_id,
                           box={"years": years, "svc_lo": info["svc_lo"], "svc_hi": info["svc_hi"], "as_of": as_of})
        applied = 0
        for y in take:
            conn.execute("UPDATE ema_ar_items SET absent_since=? WHERE substr(svc,1,4)=? AND absent_since IS NULL "
                         "AND item_key NOT IN (SELECT item_key FROM temp.ema_ar_stage WHERE svc_year=?)", (as_of, y, y))
            conn.execute(_HISTORY_SQL, (y,))
            conn.execute("DELETE FROM main.ema_ar WHERE svc_year=?", (y,))
            cols = ",".join(_ar_cols())
            applied += conn.execute(f"INSERT INTO main.ema_ar ({cols}) SELECT {cols} FROM temp.ema_ar_stage WHERE svc_year=?",
                                    (y,)).rowcount
            conn.execute(_LINKS_SQL, (y,))
        replaced = []
        for old in sorted(set(current_of.values())):
            left = conn.execute("SELECT COUNT(*) FROM main.ema_ar WHERE file_id=?", (old,)).fetchone()[0]
            st = "partly_replaced" if left else "replaced"
            if _catalog_row(conn, old) is not None:
                _catalog_put(conn, old, status=st, live_rows=left, replaced_by=source_id)
            replaced.append((old, st))
        status = "current" if take else "superseded"
        if take and behind:
            reason = f"Current for {', '.join(take)}; newer snapshots of {', '.join(behind)} are on file."
        elif behind:
            reason = f"Newer snapshots of {', '.join(behind)} are on file."
        else:
            reason = None
        live = conn.execute("SELECT COUNT(*) FROM main.ema_ar WHERE file_id=?", (source_id,)).fetchone()[0]
        _catalog_put(conn, source_id, **base, **info, status=status, status_reason=reason, applied_at=_now(),
                     rows_applied=applied, live_rows=live)
        if take:
            since = min(as_of or today_iso(), today_iso())      # retention never counts from a day still to come
            conn.execute("DELETE FROM ema_ar_items WHERE absent_since IS NOT NULL AND absent_since < date(?, ?)",
                         (since, f"-{AR_HISTORY_DAYS} days"))
            conn.execute("DELETE FROM ema_claim_links WHERE last_seen < date(?, ?)", (since, f"-{CLAIM_LINK_DAYS} days"))
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, '1')", (META_MAP_DIRTY,))
        if path and rec is not None:
            _write_files_row(conn, source_id, path, rec, kind="ema:ar", status=status, rows_read=info["rows_read"],
                             rows_applied=applied, created=created, rows_kept=info["rows_kept"])
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        _drop_stage(conn)
    try:
        learn_claim_prefix(conn, getattr(acct, "prefixes", []))
    except ReportRefused:
        pass
    record_unknown_statuses(conn, getattr(acct, "status_words", set()))
    store.ema_changed = True
    log(f"  EMA report {source_name}: {status}, {applied:,} rows applied" + (f" ({reason})" if reason else ""))
    return _result(status, "ar_report", source_id, reason=reason, rows_read=info["rows_read"], rows_kept=info["rows_kept"],
                   rows_applied=applied, live_rows=live, scope=scope, as_of=as_of, replaced=replaced, account=dict(acct),
                   content_id=content_id, source_kind=source_kind,
                   box={"years": years, "svc_lo": info["svc_lo"], "svc_hi": info["svc_hi"], "as_of": as_of})


# -------------------------------------------------------------------------------------------------------------- preview
def _boxes_meet(a: dict, b: dict) -> bool:
    """Whether two Production boxes share a (service date, posted date) point. A service box is its service dates by every
    posting up to its as_of; a posted box is every service date by its posted dates."""
    def span(box: dict) -> tuple[tuple, tuple]:
        if box.get("scope") == "posted":
            return (None, None), (box.get("posted_lo"), box.get("posted_hi"))
        return (box.get("svc_lo"), box.get("svc_hi")), (None, box.get("as_of"))

    def meet(x: tuple, y: tuple) -> bool:
        lo = max((v for v in (x[0], y[0]) if v), default=None)
        hi = min((v for v in (x[1], y[1]) if v), default=None)
        return lo is None or hi is None or lo <= hi
    (sa, pa), (sb, pb) = span(a), span(b)
    return meet(sa, sb) and meet(pa, pb)


def _scope_of(svc_lo: str | None, posted_lo: str | None) -> str | None:
    if not svc_lo or not posted_lo:
        return None
    try:
        from .ema_ledger import scope_of        # noqa: PLC0415  (one rule for both modules once the ledger ships)
        return scope_of(svc_lo, posted_lo)
    except Exception:       # noqa: BLE001
        return "posted" if (date.fromisoformat(posted_lo) - date.fromisoformat(svc_lo)).days > 45 else "service"


def preview(conn: sqlite3.Connection, path: str, rec: Recognized | None = None) -> dict:
    """Say what a report holds before it is read for good (the Upload page): which report, its rows, dates, scope, box and
    as_of, what it would replace or overlap, and any refusal or hold that can be decided without the ledger (the ledger's
    own holds are decided when the cycle applies the file). Never opens the ledger and changes nothing (not even the key
    salt: a store that has none has no snapshot to compare with). An .xlsx Production export is judged by its column
    names and declared size only."""
    ensure_schema(conn)
    name = os.path.basename(path)
    if rec is None:
        if is_plain_zip(path):
            try:
                recs = recognize_zip(path, name)
            except ReportRefused as exc:
                return {"kind": "ema_report_zip", "members": [], "refused": exc.message, "code": exc.code}
            return {"kind": "ema_report_zip", "members": [preview(conn, path, r) for r in recs], "refused": None}
        try:
            rec = recognize(path, name)
        except ReportRefused as exc:
            return {"kind": "ema_report", "report": exc.report, "refused": exc.message, "code": exc.code, "holds": []}
        if rec is None:
            return {"kind": "ema_report", "report": None, "refused": None, "holds": []}
    fid = _member_id(path, rec.member) if rec.member else file_id_of(path)
    row = _catalog_row(conn, fid)
    out: dict = {"kind": "ema_report", "report": rec.report, "report_name": REPORT_NAMES[rec.report], "format": rec.fmt,
                 "file_id": fid, "name": _names_of(path, rec)[0], "catalog_status": row["status"] if row else None,
                 "columns": len(rec.columns), "unused_columns": list(rec.unused),
                 "missing": [{"column": WHY.get(n, (n, ""))[0], "why": WHY.get(n, (n, ""))[1]} for n in rec.missing],
                 "refused": None, "code": None, "holds": [], "reads_here": reads(rec.report)}
    if rec.missing:
        out.update(refused=_missing_message(rec), code="missing_columns")
        return out
    try:
        out.update(_preview_ar(conn, path, rec, fid) if rec.report == "ar_report" else _preview_production(conn, path, rec, fid))
    except ReportRefused as exc:
        out.update(refused=exc.message, code=exc.code)
    return out


def _preview_ar(conn: sqlite3.Connection, path: str, rec: Recognized, file_id: str) -> dict:
    salt = stored_salt(conn) or secrets.token_bytes(32)      # no salt yet: no snapshot on file either
    register_functions(conn, salt)
    try:
        acct = _stage_ar(conn, path, rec, salt, file_id)
        if not acct.get("rows_kept"):
            raise ReportRefused("empty", "It holds no line with a service date and a responsible party.", "ar_report")
        as_of = acct["as_of"]
        years = _stage_years(conn)          # a year the file has no row for keeps its own snapshot
        row = _catalog_row(conn, file_id)
        mine = (as_of or "", _created_at(path, rec) or "", (row or {}).get("received_at") or _now(), file_id)
        replaces, behind, current_of = [], [], {}
        for y in years:
            cur = conn.execute("SELECT file_id, MAX(as_of) FROM main.ema_ar WHERE svc_year=? GROUP BY file_id", (y,)).fetchone()
            if cur is None:
                replaces.append({"year": y, "file_id": None, "as_of": None})
            elif cur[0] != file_id and mine > _order(conn, cur[0]):
                replaces.append({"year": y, "file_id": cur[0], "as_of": cur[1]})
                current_of[y] = cur[0]
            elif cur[0] != file_id:
                behind.append({"year": y, "file_id": cur[0], "as_of": cur[1]})
        holds = format_holds(acct) + _ar_holds(conn, [r["year"] for r in replaces], current_of, as_of)
        t = conn.execute("""SELECT COUNT(DISTINCT claim_id),
                                   SUM(CASE WHEN lower(party) LIKE 'insur%' THEN exposure_cents ELSE 0 END),
                                   SUM(CASE WHEN lower(party) LIKE 'insur%' THEN 0 ELSE exposure_cents END),
                                   SUM(CASE WHEN lower(party) LIKE 'insur%' AND claim_id IS NULL THEN 1 ELSE 0 END)
                            FROM temp.ema_ar_stage""").fetchone()
        return {"rows": acct["rows_read"], "rows_kept": acct["rows_kept"], "claim_ids": t[0] or 0,
                "insurance_cents": t[1] or 0, "patient_cents": t[2] or 0, "insurance_no_claim": t[3] or 0,
                "svc_lo": acct["svc_lo"], "svc_hi": acct["svc_hi"], "as_of": as_of, "years": years,
                "scope": _years_label(years), "aging_window": acct.get("aging_window"),
                "replaces": replaces, "behind": behind, "holds": [{"code": c, "message": m} for c, m in holds],
                "account": dict(acct)}
    finally:
        conn.rollback()
        _drop_stage(conn)


def _preview_production(conn: sqlite3.Connection, path: str, rec: Recognized, file_id: str) -> dict:
    holds: list[dict] = []
    current = coverage(conn)["production"]
    if rec.fmt == "xlsx":
        if runtime.serverless() and (rec.dimension or 0) > 120_000:
            holds.append({"code": "H6", "message": "Save it as CSV: an Excel Production export this large is not read on this site."})
        return {"rows": rec.dimension, "rows_kept": None, "holds": holds, "overlaps": [],
                "note": "An Excel Production export is read in full only when the cycle applies it."}
    acct = Account()
    payers: set[str] = set()
    for p in iter_production(path, rec, salt=secrets.token_bytes(32), account=acct):
        if p.payer and not (p.flags & FLAG_PATIENT_SIDE) and len(payers) < 50:
            payers.add(p.payer)
    scope = _scope_of(acct.get("svc_lo"), acct.get("posted_lo"))
    box = {"scope": scope, "svc_lo": acct.get("svc_lo"), "svc_hi": acct.get("svc_hi"), "posted_lo": acct.get("posted_lo"),
           "posted_hi": acct.get("posted_hi"), "as_of": acct.get("as_of")}
    overlaps = [b for b in current if b["file_id"] != file_id and _boxes_meet(box, b)]
    read = acct.get("rows_read") or 0
    holds += [{"code": c, "message": m} for c, m in production_format_holds(acct)]
    if not current and (acct.get("rows_kept") or 0) >= 2000 and len(payers) < 3:
        holds.append({"code": "H5", "message": "It names fewer than three insurers: was it filtered by payer?"})
    return {"rows": read, "rows_kept": acct.get("rows_kept"), "scope": scope, "box": box, "as_of": acct.get("as_of"),
            "svc_lo": acct.get("svc_lo"), "svc_hi": acct.get("svc_hi"), "posted_lo": acct.get("posted_lo"),
            "posted_hi": acct.get("posted_hi"), "insurers": len(payers), "overlaps": overlaps, "holds": holds,
            "account": dict(acct), "note": "Whether it shrinks or double-filters what is on file is decided when the cycle applies it."}


# ------------------------------------------------------------------------------------------------------------ decisions
def decide(conn: sqlite3.Connection, file_id: str, decision: str, user: str) -> dict:
    """A person's decision on a held report: 'discard' closes it; 'apply' records the decision and returns
    {'requeue': origin} so the caller queues the file again (the next read skips holds, never refusals)."""
    ensure_schema(conn)
    if decision not in ("apply", "discard"):
        return {"error": "The decision is apply or discard."}
    row = _catalog_row(conn, file_id)
    if row is None:
        return {"error": "No report with that id is on file."}
    if row["status"] == "refused":
        return {"error": "A refused report cannot be applied. Export it again from EMA.", "status": "refused"}
    if row["status"] != "held":
        return {"error": "Only a held report waits for a decision.", "status": row["status"]}
    now = _now()
    if decision == "discard":
        _catalog_put(conn, file_id, commit=True, status="discarded", decision="discard", decided_by=user, decided_at=now)
        return {"file_id": file_id, "status": "discarded"}
    _catalog_put(conn, file_id, commit=True, decision="apply", decided_by=user, decided_at=now)
    try:
        origin = json.loads(row["origin"]) if row.get("origin") else {}
    except ValueError:
        origin = {}
    return {"file_id": file_id, "status": "held", "decision": "apply", "requeue": origin}


# -------------------------------------------------------------------------------------------------- misread report rows
KEYED_ROW = "(claim_id IS NOT NULL OR bill_id IS NOT NULL OR bill_item_id IS NOT NULL OR patient_id IS NOT NULL)"


def _table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _misread_candidates(conn: sqlite3.Connection) -> list[dict]:
    """Sources the old code read as a Claim & Claim Status export (or as an unknown layout) in which no row names a claim,
    a bill, a bill item or a patient. A genuine export names one of them on some row; an A/R or Production report never
    does. Also the files rows of those kinds whose rows all went in under another file's copy."""
    out: dict[str, dict] = {}
    if _table(conn, "ema_rows"):
        for sha, fam, n, keyed, posted, adj in conn.execute(
                f"""SELECT source_sha256, family, COUNT(*), SUM({KEYED_ROW}), SUM(posted_date IS NOT NULL),
                           SUM(adjustment_reason IS NOT NULL)
                    FROM ema_rows WHERE family IN ('claim_status', 'unknown') GROUP BY source_sha256, family"""):
            if sha is None or keyed:
                continue
            out[sha] = {"sha": sha, "family": fam, "rows": n,
                        "guess": "production" if (posted or adj) else "ar_report", "kind": None, "name": None, "path": None}
    for sha, kind, name, path in conn.execute(
            "SELECT sha256, kind, source_name, source_path FROM files WHERE kind IN ('ema:claim_status', 'ema:unknown')"):
        c = out.get(sha)
        if c is None:
            if _table(conn, "ema_rows") and conn.execute("SELECT 1 FROM ema_rows WHERE source_sha256=? LIMIT 1", (sha,)).fetchone():
                continue                    # rows that name a claim or a bill: a genuine export
            low = (name or "").lower()
            c = out[sha] = {"sha": sha, "family": kind.split(":", 1)[1], "rows": 0,
                            "guess": "production" if low.endswith(TEXT_MEMBERS) else "ar_report"}
        c.update(kind=kind, name=name, path=path)
    return list(out.values())


def _purge_source(conn: sqlite3.Connection, sha: str) -> None:
    if _table(conn, "ema_rows"):
        conn.execute("DELETE FROM ema_rows WHERE source_sha256=?", (sha,))
    conn.execute("DELETE FROM ema_sources WHERE sha256=?", (sha,))
    conn.execute("DELETE FROM files WHERE sha256=?", (sha,))


def _clear_unknown(conn: sqlite3.Connection, sha: str) -> int:
    """Rows of an unknown layout feed nothing: they go, and the file is noted as not read."""
    n = conn.execute("DELETE FROM ema_rows WHERE source_sha256=? AND family='unknown'", (sha,)).rowcount if _table(conn, "ema_rows") else 0
    conn.execute("UPDATE files SET kind='ema:unknown', status='not_read', lines=0 WHERE sha256=?", (sha,))
    return n


def _set_suspects(conn: sqlite3.Connection, add: list[dict] | None = None, drop: Iterable[str] = ()) -> None:
    have = {s["sha256"]: s for s in _meta_json(conn, META_SUSPECTS, []) if isinstance(s, dict) and s.get("sha256")}
    for sha in drop:
        have.pop(sha, None)
    for s in add or []:
        have[s["sha256"]] = s
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (META_SUSPECTS, json.dumps(list(have.values()))))


def _upload_copy(conn: sqlite3.Connection, sha: str) -> tuple[str | None, str | None]:
    if not _table(conn, "uploads"):
        return None, None
    r = conn.execute("SELECT upload_id, path FROM uploads WHERE (upload_id=? OR stored_sha=?) AND path IS NOT NULL "
                     "ORDER BY uploaded_at DESC LIMIT 1", (sha, sha)).fetchone()
    return (r[0], r[1]) if r else (None, None)


def _dropbox_origin(conn: sqlite3.Connection, name: str | None) -> dict | None:
    """Where the misread file is in Dropbox, by its name: the newest entry the site has seen under that name."""
    if not name or not _table(conn, "dropbox_seen"):
        return None
    best = None
    for eid, h, p, at in conn.execute("SELECT id, content_hash, path, at FROM dropbox_seen WHERE path LIKE ?", ("%" + name,)):
        if not p or str(eid).startswith("zip:") or os.path.basename(p) != name:
            continue
        if best is None or (at or "") > (best[3] or ""):
            best = (eid, h, p, at)
    if best is None:
        return None
    return {"dropbox_id": best[0], "path": best[2], "content_hash": best[1], "misread": True}


def migrate_misread(store: Any, *, serverless: bool, log: Any = print) -> dict:
    """Once per host (meta ema:migrated_v1): find what the old code read from the two reports as an export, and put it
    right. A candidate is confirmed only when its original file is recognised as a report: then its rows, its export
    record and its files row go, and the file is queued as waiting with its origin, to be read as a report. A candidate
    whose original is not a report is a genuine export and is not touched; one whose original is gone keeps its rows
    and is listed as a possible misread report (meta ema:misread_suspects). Rows of an unknown layout are deleted and
    their file noted as not read. Serverless: the candidate is queued from its Dropbox entry and its rows stay until the
    fetched file is confirmed (confirm_misread)."""
    conn = store.conn
    ensure_schema(conn)
    if conn.execute("SELECT 1 FROM meta WHERE key=?", (META_MIGRATED,)).fetchone():
        return {"skipped": True}
    out = {"candidates": 0, "purged": 0, "queued": 0, "kept": 0, "unknown_rows_deleted": 0, "genuine": 0, "rowless": 0}
    suspects: list[dict] = []
    for c in _misread_candidates(conn):
        out["candidates"] += 1
        sha, fam = c["sha"], c["family"]
        if serverless:
            if not c["rows"]:
                # nothing to purge (its rows went in under another copy of the same rows); an unknown file is noted
                if fam == "unknown":
                    _clear_unknown(conn, sha)
                out["rowless"] = out.get("rowless", 0) + 1
                continue
            origin = _dropbox_origin(conn, c.get("name"))
            if fam == "unknown":
                out["unknown_rows_deleted"] += _clear_unknown(conn, sha)
            if origin:
                record_waiting(conn, file_id=sha, report=c["guess"], source_name=c.get("name") or "", via="dropbox", origin=origin,
                               reason="Read before as another export; it is read again as a report once fetched.")
                out["queued"] += 1
            elif fam != "unknown":
                suspects.append({"sha256": sha, "family": fam, "rows": c["rows"], "why": "not found in Dropbox"})
                out["kept"] += 1
            continue
        upload_id, upload_path = _upload_copy(conn, sha)
        rec: Any = None
        found = None
        for p in (c.get("path"), upload_path):
            if p and os.path.isfile(p) and file_id_of(p) == sha:
                found = p
                try:
                    rec = recognize(p, os.path.basename(p))
                except ReportRefused:
                    rec = "refused"         # a report all the same: it is read (and refused) as one
                break
        if rec:
            _purge_source(conn, sha)
            origin = {"path": found, "misread": True}
            if upload_id and found == upload_path:
                origin["upload_id"] = upload_id
                conn.execute("UPDATE uploads SET status='queued', done_at=NULL WHERE upload_id=?", (upload_id,))
            report = rec.report if isinstance(rec, Recognized) else c["guess"]
            record_waiting(conn, file_id=sha, report=report, source_name=c.get("name") or os.path.basename(found), via=(
                "upload" if "upload_id" in origin else "mirror"), origin=origin,
                reason="Read before as another export; it is read again as a report.")
            out["purged"] += 1
            out["queued"] += 1
        elif fam == "unknown":
            out["unknown_rows_deleted"] += _clear_unknown(conn, sha)
        elif found:
            out["genuine"] += 1             # the original is a real export that names no claim: left as it is
        elif not c["rows"]:
            out["rowless"] = out.get("rowless", 0) + 1     # no rows of its own and no file to look at: no evidence either way
        else:
            suspects.append({"sha256": sha, "family": fam, "rows": c["rows"], "why": "original file gone"})
            out["kept"] += 1
    _set_suspects(conn, suspects)
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (META_MIGRATED, _now()))
    conn.commit()
    if out["candidates"]:
        log(f"  EMA reports read before as exports: {out['candidates']} found, {out['queued']} queued to be read again, "
            f"{out['kept']} kept, {out['genuine']} genuine")
    return out


def confirm_misread(store: Any, sha256: str, rec: Recognized | None) -> bool:
    """The second half of the clean-up, once a file is in hand: when these exact bytes are recognised as a report and the
    store still holds rows the old code read from them as an export (no row naming a claim, bill, bill item or patient),
    those rows, their export record and their files row are removed. Returns whether anything was removed. When the file
    is not a report, a waiting entry queued for it by migrate_misread is dropped and nothing else changes."""
    conn = store.conn
    if rec is None or getattr(rec, "report", None) not in REPORTS:
        row = _catalog_row(conn, sha256) if _table(conn, "ema_report_files") else None
        if row and row["status"] == "waiting":
            try:
                misread = bool((json.loads(row["origin"]) if row.get("origin") else {}).get("misread"))
            except ValueError:
                misread = False
            if misread:
                conn.execute("DELETE FROM ema_report_files WHERE file_id=?", (sha256,))
                conn.commit()
        return False
    f = conn.execute("SELECT kind FROM files WHERE sha256=?", (sha256,)).fetchone()
    has_rows = _table(conn, "ema_rows")
    fams = {r[0] for r in conn.execute("SELECT DISTINCT family FROM ema_rows WHERE source_sha256=?", (sha256,))} if has_rows else set()
    if f is None and not fams:
        return False
    if f is not None and f[0] in REPORT_KINDS:
        return False
    if f is not None and f[0] not in ("ema:claim_status", "ema:unknown") and not fams:
        return False
    if fams - {"claim_status", "unknown"}:
        return False
    if has_rows and conn.execute(f"SELECT 1 FROM ema_rows WHERE source_sha256=? AND {KEYED_ROW} LIMIT 1", (sha256,)).fetchone():
        return False
    _purge_source(conn, sha256)
    _set_suspects(conn, drop=[sha256])
    conn.commit()
    return True


def misread_suspects(conn: sqlite3.Connection) -> list[dict]:
    """Sources that may be a misread report but whose original could not be checked (the Data tab lists them)."""
    return _meta_json(conn, META_SUSPECTS, [])
