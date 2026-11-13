"""
Loaders for ModMed EMA xlsx exports.

Three export families appear in the practice's Dropbox, in several column
layouts (annual, weekly, daily, by-transaction-date):

  charge_lines   "Encounter & Charge Lines": an activity ledger, one row per
                 charge / payment / adjustment / void per bill item. The
                 Activity Type = Charge rows are the billed service lines
                 (CPT, modifiers, units, charge, diagnosis, payer, Claim ID).
  era_lines      "ERA & Payment Lines": one row per ERA adjustment reason
                 (CARC/RARC) per charge line with how the biller handled it
                 (WRITE_OFF / TRANSFER_RESPONSIBILITY / IGNORE).
  claim_status   "Claim & Claim Status": one row per claim line with status,
                 denial amount, denial category and reason codes.

Columns are mapped to canonical names so every layout loads the same way.
Money becomes integer cents. "-" becomes NULL. Modifiers become a list.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from typing import Any, Iterator

CANON = {
    "service date": "service_date", "transaction date": "transaction_date", "posted date": "posted_date",
    "bill creation date": "bill_created", "primary provider": "provider", "primary provider role": "provider_role",
    "referring provider name": "referring_provider", "patient id": "patient_id", "bill id": "bill_id",
    "claim id": "claim_id", "bill item identifier": "bill_item_id", "authorization number": "auth_number",
    "cpt/product": "cpt", "cpt": "cpt", "modifiers": "modifiers", "icd-10": "icd10", "icd-10 description": "icd10_desc",
    "ndc code": "ndc_code", "ndc quantity": "ndc_quantity", "ndc unit of measure": "ndc_unit", "ndc description": "ndc_desc",
    "payer": "payer", "payer id": "payer_id", "payer policy #": "policy_number", "policy group number": "group_number",
    "coverage type": "coverage_type", "claims state": "claims_state", "claim status": "claim_status",
    "claim action": "claim_action", "claim submission date": "claim_submission_date", "denial date": "denial_date",
    "location": "location", "business unit": "business_unit",
    "charges": "charges", "payments": "payments", "adjustments": "adjustments",
    "insurance payments": "insurance_payments", "insurance adjustments": "insurance_adjustments",
    "contractual adjustments": "contractual_adjustments", "non- contractual adjustments": "noncontractual_adjustments",
    "non-contractual adjustments": "noncontractual_adjustments", "patient adjustments": "patient_adjustments",
    "payment allowed amount": "allowed", "patient charges": "patient_charges", "patient payments": "patient_payments",
    "denial amount": "denial_amount", "rejected amount": "rejected_amount",
    "units": "units", "activity type": "activity_type", "adjustment reason code": "adjustment_reason",
    "payment method": "payment_method", "batch name": "batch_name",
    "rarc code": "rarc", "rarc code description": "rarc_desc", "reason code": "carc", "reason code description": "carc_desc",
    "recreated bill?": "recreated_bill", "adjustment status": "adjustment_status", "adjustment subtype": "adjustment_subtype",
    "denial category": "denial_category", "previously denied?": "previously_denied", "submission format": "submission_format",
}
MONEY = ("charges", "payments", "adjustments", "insurance_payments", "insurance_adjustments", "contractual_adjustments",
         "noncontractual_adjustments", "patient_adjustments", "allowed", "patient_charges", "patient_payments",
         "denial_amount", "rejected_amount")
DATES = ("service_date", "transaction_date", "posted_date", "bill_created", "claim_submission_date", "denial_date")


def detect_family(columns: list[str]) -> str:
    cols = {c.lower().strip() for c in columns}
    canon = {CANON.get(c) for c in cols}
    if "adjustment_status" in canon or ("carc" in canon and "rarc" in canon and "activity_type" not in canon):
        return "era_lines"
    if "activity_type" in canon:
        return "charge_lines"
    if "denial_amount" in canon or "claim_status" in canon:
        return "claim_status"
    return "unknown"


# Text that stands for "no value". The first five are the app's own; the rest are what the spreadsheet reader used until
# now treated as empty (including Excel's error cells), kept so that the same file always produces the same rows.
NA_TEXT = frozenset(("", "-", "nan", "NaN", "None", "NA", "N/A", "n/a", "NULL", "null", "#N/A", "#NA", "#N/A N/A", "<NA>", "-nan", "-NaN",
                     "1.#IND", "-1.#IND", "1.#QNAN", "-1.#QNAN", "#DIV/0!", "#VALUE!", "#REF!", "#NAME?", "#NUM!", "#NULL!"))


def _clean(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, str):
        s = v.strip()
        if s in NA_TEXT:
            return None
        return s
    return v


def _cents(v: Any) -> int | None:
    v = _clean(v)
    if v is None:
        return None
    try:
        return int(round(float(v) * 100))
    except (TypeError, ValueError):
        return None


def _date(v: Any) -> str | None:
    v = _clean(v)
    if v is None:
        return None
    if hasattr(v, "strftime"):
        try:
            return v.strftime("%Y-%m-%d")
        except ValueError:
            return None
    s = str(v)
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", s)
    if m:
        y = m.group(3)
        y = ("20" + y) if len(y) == 2 else y
        return f"{y}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    return None


def _text(v: Any) -> str | None:
    v = _clean(v)
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def split_modifiers(v: Any) -> list[str]:
    v = _clean(v)
    if v is None:
        return []
    return [m.strip().upper() for m in re.split(r"[,\s;|/]+", str(v)) if m.strip() and m.strip() != "-"]


def split_carc(v: Any) -> tuple[str | None, str | None]:
    """'CO-45' -> ('CO','45'); 'PR-1' -> ('PR','1'); 'CO-' -> ('CO', None); 'P' -> (None,'P')."""
    v = _clean(v)
    if v is None:
        return None, None
    s = str(v).upper()
    m = re.match(r"^(CO|PR|OA|PI|CR)\s*-\s*([A-Z0-9]*)$", s)
    if m:
        return m.group(1), (m.group(2) or None)
    return None, s


def export_created(path: str) -> str | None:
    """When the workbook was created, as it records itself (docProps/core.xml), in ISO form; None when it does not say.
    For an export saved straight out of EMA or Excel this is the moment the export was pulled."""
    import zipfile

    try:
        with zipfile.ZipFile(path) as z:
            core = z.read("docProps/core.xml").decode("utf-8", "replace")
    except (OSError, KeyError, zipfile.BadZipFile):
        return None
    m = re.search(r"<dcterms:created[^>]*>\s*(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", core)
    return m.group(1) + "Z" if m else None


def read_export(path: str) -> tuple[list[dict], str, str]:
    """Return (rows as dicts keyed by canonical column name, family, sha256 of the file). First sheet only."""
    import openpyxl

    with open(path, "rb") as fh:
        data = fh.read()
    sha = hashlib.sha256(data).hexdigest()
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb.worksheets[0]
        raw = [list(r) for r in ws.iter_rows(values_only=True)]
    finally:
        wb.close()
    # A sheet often declares more columns and rows than it uses. Keep only up to the last cell that holds something:
    # a row's identity (its fingerprint) is built from its columns, and empty ones must not change it from file to file.
    def used(r: list) -> int:
        n = len(r)
        while n and (r[n - 1] is None or r[n - 1] == ""):
            n -= 1
        return n
    widths = [used(r) for r in raw]
    while widths and widths[-1] == 0:
        widths.pop()
    raw = raw[:len(widths)]
    width = max(widths, default=0)
    raw = [r[:width] + [None] * (width - len(r)) for r in raw]
    hdr = 0
    for i in range(min(10, len(raw))):
        if sum(isinstance(v, str) for v in raw[i]) >= 5:
            hdr = i
            break
    header = raw[hdr] if raw else []
    names: list[str] = []
    seen: dict[str, int] = {}
    for i, c in enumerate(header):
        label = str(c) if c is not None else f"Unnamed: {i}"
        n = seen.get(label, 0)
        seen[label] = n + 1
        names.append(label if n == 0 else f"{label}.{n}")       # a repeated header gets a numeric suffix
    family = detect_family(names)
    canon = []
    for c in names:
        key = c.lower().strip()
        canon.append(CANON.get(key, re.sub(r"[^a-z0-9]+", "_", key).strip("_")))
    rows = []
    for r in raw[hdr + 1:]:
        r = list(r) + [None] * (len(canon) - len(r))
        rows.append({canon[i]: r[i] for i in range(len(canon))})
    return rows, family, sha


def iter_rows(path: str) -> Iterator[dict]:
    """Yield canonical row dicts (cents, ISO dates, modifier lists) ready for the store."""
    records, family, sha = read_export(path)
    name = os.path.basename(path)
    for idx, rec in enumerate(records):
        if all(_clean(v) is None for v in rec.values()):
            continue
        row: dict[str, Any] = {"family": family, "source_sha256": sha, "source_name": name, "row_index": idx}
        for k, v in rec.items():
            if k in MONEY:
                row[f"{k}_cents"] = _cents(v)
            elif k in DATES:
                row[k] = _date(v)
            elif k == "modifiers":
                mods = split_modifiers(v)
                row["modifiers"] = json.dumps(mods) if mods else None
                row["modifier_1"] = mods[0] if mods else None
                row["modifier_2"] = mods[1] if len(mods) > 1 else None
            elif k == "carc":
                row["carc"] = _text(v)
                row["carc_group"], row["carc_code"] = split_carc(v)
            elif k == "units":
                c = _clean(v)
                try:
                    row["units"] = float(c) if c is not None else None
                except (TypeError, ValueError):
                    row["units"] = None
            else:
                row[k] = _text(v)
        # rename to table column names
        if "allowed_cents" in row:
            pass
        fp_fields = sorted((k, str(v)) for k, v in row.items() if k not in ("source_sha256", "source_name", "row_index"))
        row["row_fingerprint"] = hashlib.sha256(json.dumps(fp_fields).encode()).hexdigest()
        yield row


def iter_export_paths(root: str) -> Iterator[str]:
    for dirpath, _dirs, files in os.walk(root):
        for n in files:
            if n.lower().endswith(".xlsx") and not n.startswith("~$"):
                yield os.path.join(dirpath, n)


if __name__ == "__main__":  # smoke test
    import sys
    for p in sys.argv[1:]:
        recs, fam, sha = read_export(p)
        cols = sorted(recs[0]) if recs else []
        print(f"{fam:12s} rows={len(recs):6d} cols={len(cols):3d} {os.path.basename(p)}")
        print("   ", cols[:50])
