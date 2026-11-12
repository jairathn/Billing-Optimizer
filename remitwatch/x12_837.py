"""
Minimal X12 837P (005010X222A1) reader: what the practice actually submitted.

Captures, per claim (CLM) and service line (SV1): patient control number
(CLM01), total charge, frequency code, payer, billing/rendering NPIs, service
date (DTP*472), procedure code and modifiers, charge, units, diagnosis
pointers and the line item control number (REF*6R). Stored in `claims_837`
and `lines_837` so the 835 paid code can be compared with the submitted code
even when the payer omits SVC06.
"""
from __future__ import annotations

import hashlib
import os
from typing import Any

from .x12_835 import Seg, cents, iso_date, iter_segments, sniff_delims, split_envelopes, to_float

DDL = """
CREATE TABLE IF NOT EXISTS claims_837 (
    claim_837_key TEXT PRIMARY KEY, file_sha256 TEXT, source_name TEXT, st_control TEXT, bht_created TEXT,
    patient_control_number TEXT, total_charge_cents INTEGER, frequency_code TEXT, place_of_service TEXT,
    payer_name TEXT, payer_id TEXT, billing_npi TEXT, rendering_npi TEXT, subscriber_id TEXT, service_date TEXT,
    original_reference_number TEXT, prior_auth TEXT, line_count INTEGER
);
CREATE INDEX IF NOT EXISTS ix_c837_pcn ON claims_837(patient_control_number);
CREATE TABLE IF NOT EXISTS lines_837 (
    line_837_key TEXT PRIMARY KEY, claim_837_key TEXT, patient_control_number TEXT, line_seq INTEGER,
    procedure_code TEXT, modifiers TEXT, charge_cents INTEGER, units REAL, service_date TEXT,
    diagnosis_pointers TEXT, line_control_number TEXT, rendering_npi TEXT
);
CREATE INDEX IF NOT EXISTS ix_l837_pcn ON lines_837(patient_control_number);
"""


def parse_837_bytes(data: bytes, source: str = "") -> dict[str, list[dict]]:
    text = data.decode("utf-8", errors="replace").lstrip("﻿")
    sha = hashlib.sha256(data).hexdigest()
    claims: list[dict] = []
    lines: list[dict] = []
    for env_index, env in enumerate(split_envelopes(text)):
        d = sniff_delims(env)
        st_control = None
        bht_created = None
        payer_name = payer_id = billing_npi = None
        loop = ""
        claim: dict | None = None
        line: dict | None = None
        claim_seq = 0
        line_seq = 0
        rendering_npi = None
        subscriber_id = None

        def finish_claim():
            nonlocal claim, line
            if line is not None:
                lines.append(line)
                line = None
            if claim is not None:
                claim["line_count"] = sum(1 for x in lines if x["claim_837_key"] == claim["claim_837_key"])
                claims.append(claim)
                claim = None

        for seg in iter_segments(env, d):
            sid = seg.id
            if sid == "ST":
                finish_claim()
                st_control = seg.get(2)
            elif sid == "BHT":
                bht_created = iso_date(seg.get(4))
            elif sid == "HL":
                level = seg.get(3)
                if level == "20":
                    loop = "billing"
                elif level == "22":
                    loop = "subscriber"
                    finish_claim()
                elif level == "23":
                    loop = "patient"
                    finish_claim()
            elif sid == "NM1":
                ent = seg.get(1)
                if ent == "85":
                    billing_npi = seg.get(9) if seg.get(8) == "XX" else billing_npi
                elif ent == "PR":
                    payer_name = seg.get(3)
                    payer_id = seg.get(9) or payer_id
                elif ent == "IL":
                    subscriber_id = seg.get(9) or subscriber_id
                elif ent == "82":
                    npi = seg.get(9) if seg.get(8) == "XX" else None
                    if line is not None:
                        line["rendering_npi"] = npi
                    elif claim is not None:
                        claim["rendering_npi"] = npi
                    else:
                        rendering_npi = npi
            elif sid == "CLM":
                finish_claim()
                claim_seq += 1
                line_seq = 0
                pos = seg.comp(5, d)
                claim = {
                    "claim_837_key": f"{sha[:16]}:{env_index}:{st_control}:{claim_seq}", "file_sha256": sha,
                    "source_name": os.path.basename(source) if source else None, "st_control": st_control, "bht_created": bht_created,
                    "patient_control_number": seg.get(1), "total_charge_cents": cents(seg.get(2)),
                    "frequency_code": pos[2] if len(pos) > 2 else None, "place_of_service": pos[0] if pos else None,
                    "payer_name": payer_name, "payer_id": payer_id, "billing_npi": billing_npi, "rendering_npi": rendering_npi,
                    "subscriber_id": subscriber_id, "service_date": None, "original_reference_number": None, "prior_auth": None,
                    "line_count": 0,
                }
            elif claim is None:
                continue
            elif sid == "REF":
                q, v = seg.get(1), seg.get(2)
                if line is not None and q == "6R":
                    line["line_control_number"] = v
                elif q == "F8":
                    claim["original_reference_number"] = v
                elif q == "G1":
                    claim["prior_auth"] = v
            elif sid == "DTP":
                q, v = seg.get(1), seg.get(3)
                if q == "472":
                    dv = iso_date(v[:8])
                    if line is not None:
                        line["service_date"] = dv
                    else:
                        claim["service_date"] = dv
            elif sid == "LX":
                if line is not None:
                    lines.append(line)
                line = None
            elif sid == "SV1":
                if line is not None:
                    lines.append(line)
                line_seq += 1
                comp = seg.comp(1, d)
                code = comp[1] if len(comp) > 1 else None
                mods = [m for m in comp[2:6] if m]
                ptrs = seg.comp(7, d)
                line = {
                    "line_837_key": f"{claim['claim_837_key']}:{line_seq}", "claim_837_key": claim["claim_837_key"],
                    "patient_control_number": claim["patient_control_number"], "line_seq": line_seq,
                    "procedure_code": code, "modifiers": ",".join(mods) if mods else None,
                    "charge_cents": cents(seg.get(2)), "units": to_float(seg.get(4)), "service_date": None,
                    "diagnosis_pointers": ",".join(p for p in ptrs if p) if ptrs else None, "line_control_number": None,
                    "rendering_npi": claim.get("rendering_npi"),
                }
            elif sid == "SE":
                finish_claim()
        finish_claim()
    by_key = {c["claim_837_key"]: c for c in claims}
    for l in lines:
        c = by_key.get(l["claim_837_key"])
        if not c:
            continue
        if not l["service_date"]:
            l["service_date"] = c.get("service_date")
        elif not c.get("service_date") or l["service_date"] < c["service_date"]:
            # professional claims carry the date of service on each line (2400 DTP*472), not on the claim
            c["service_date"] = l["service_date"]
    return {"claims": claims, "lines": lines, "sha256": sha}


def ingest_837_file(store, path: str) -> dict:
    with open(path, "rb") as fh:
        data = fh.read()
    sha = hashlib.sha256(data).hexdigest()
    conn = store.conn
    conn.executescript(DDL)
    if store.file_seen(sha):
        return {"skipped": True}
    parsed = parse_837_bytes(data, source=path)
    ccols = ["claim_837_key", "file_sha256", "source_name", "st_control", "bht_created", "patient_control_number", "total_charge_cents",
             "frequency_code", "place_of_service", "payer_name", "payer_id", "billing_npi", "rendering_npi", "subscriber_id", "service_date",
             "original_reference_number", "prior_auth", "line_count"]
    lcols = ["line_837_key", "claim_837_key", "patient_control_number", "line_seq", "procedure_code", "modifiers", "charge_cents", "units",
             "service_date", "diagnosis_pointers", "line_control_number", "rendering_npi"]
    conn.executemany(f"INSERT OR IGNORE INTO claims_837 ({','.join(ccols)}) VALUES ({','.join('?' * len(ccols))})",
                     [tuple(c.get(k) for k in ccols) for c in parsed["claims"]])
    conn.executemany(f"INSERT OR IGNORE INTO lines_837 ({','.join(lcols)}) VALUES ({','.join('?' * len(lcols))})",
                     [tuple(l.get(k) for k in lcols) for l in parsed["lines"]])
    store.record_file(sha, path, kind="837", status="ok", claims=len(parsed["claims"]), lines=len(parsed["lines"]))
    conn.commit()
    return {"claims": len(parsed["claims"]), "lines": len(parsed["lines"])}
