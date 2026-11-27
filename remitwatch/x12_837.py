"""
Minimal X12 837P (005010X222A1) reader: what the practice actually submitted.

Captures, per claim (CLM) and service line (SV1): patient control number
(CLM01), total charge, frequency code, payer, billing/rendering NPIs, service
date (DTP*472), procedure code and modifiers, charge, units, diagnosis
pointers and the line item control number (REF*6R). Stored in `claims_837`
and `lines_837` so the 835 paid code can be compared with the submitted code
even when the payer omits SVC06.

Read since READ_VERSION 2 (the fix wave's F3a), per claim:
- who the payer is to this claim (SBR01 of the subscriber loop 2000B: P primary, S secondary, T tertiary, ...), the
  claim filing indicator (SBR09) and the patient's relationship to the subscriber (SBR02 18 when the subscriber is the
  patient, else PAT01 of the patient loop);
- the patient: the dependent of the patient loop (2000C/2010CA, NM1*QC and its DMG) where the 837 has one, else the
  subscriber (2010BA, NM1*IL and its DMG). The store keeps only keyed values of the patient's name (patient_key), of
  the name with the birth date (person_key) and, since READ_VERSION 3, of the first name alone (first_key: one person's
  name written two ways, O'TESTER and OTESTER, keeps one first name), made with the store's own key salt as EMA's
  reports' are (ema_reports.name_key_parts and person_key), never the name or the birth date: a dependent's claim
  carries the subscriber's member id, and only the patient tells two family members on one id apart;
- for a claim sent to a payer after another payer answered it (2320/2330B): the other payer that answered just before
  it (its name, id and responsibility), what the payers before it paid (AMT*D), and what that payer left to the patient
  (AMT*EAF, else its CAS*PR adjustments on the claim and on the lines' 2430 SVD): what a secondary payer is asked to pay.
  The payers before a claim are those whose place (2320 SBR01) comes before the claim's own (SBR01): the guide asks for
  loop 2320 whenever another payer may pay, so a claim to the primary lists the secondary there with nothing adjudicated,
  and a secondary's claim may list the tertiary after it. With no place on the claim, only an other payer's loop that
  shows an adjudication (AMT*D, AMT*EAF, CAS or SVD) answered before it (READ_VERSION 3; version 2 took the last place).
The segments of the other payer's loops (2320 to 2330G) never change the claim's own payer, subscriber or rendering
clinician, as they did before (a second claim of the same subscriber loop took the other payer as its payer); reading a
file again (reread_837_bytes) rewrites what the reader before took from those loops.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
from typing import Any

from .x12_835 import Seg, cents, iso_date, iter_segments, sniff_delims, split_envelopes, to_float

# claims read since the patient loop, SBR01/SBR09 and the other payer's loop were read (2), and since the other payer is
# the one before the claim's own place, with the patient's keyed first name (3)
READ_VERSION = 3

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

# columns added after the first release (ensure_schema adds them to an older store; a claim read before has them NULL,
# and read_version NULL says so)
EXTRA_CLAIM_COLS = {
    "payer_resp": "TEXT", "relationship": "TEXT", "filing_indicator": "TEXT", "patient_key": "INTEGER", "person_key": "INTEGER",
    "other_payer_name": "TEXT", "other_payer_id": "TEXT", "other_payer_resp": "TEXT", "other_paid_cents": "INTEGER",
    "other_left_cents": "INTEGER", "other_pr_cents": "INTEGER", "read_version": "INTEGER", "first_key": "INTEGER",
}
# what the reader before READ_VERSION 2 could take from another payer's loop (2330A NM1*IL, 2330B NM1*PR, 2330D NM1*82):
# reading a file again rewrites them from the fresh read (reread_837_bytes)
REREAD_CLAIM_COLS = ["payer_name", "payer_id", "subscriber_id", "rendering_npi"]
CLAIM_COLS = ["claim_837_key", "file_sha256", "source_name", "st_control", "bht_created", "patient_control_number", "total_charge_cents",
              "frequency_code", "place_of_service", "payer_name", "payer_id", "billing_npi", "rendering_npi", "subscriber_id",
              "service_date", "original_reference_number", "prior_auth", "line_count"] + list(EXTRA_CLAIM_COLS)
LINE_COLS = ["line_837_key", "claim_837_key", "patient_control_number", "line_seq", "procedure_code", "modifiers", "charge_cents", "units",
             "service_date", "diagnosis_pointers", "line_control_number", "rendering_npi"]
_RESP_ORDER = "PSTABCDEFGH"     # SBR01's payer responsibility sequence, first to last (U, unknown, after them)


def ensure_schema(conn: sqlite3.Connection) -> None:
    """The 837 tables, with the columns added since the first release on a store made before them."""
    conn.executescript(DDL)
    have = {r[1] for r in conn.execute("PRAGMA table_info(claims_837)")}
    for col, typ in EXTRA_CLAIM_COLS.items():
        if col not in have:
            conn.execute(f"ALTER TABLE claims_837 ADD COLUMN {col} {typ}")


def _cas_pr(seg: Seg) -> int:
    """The patient-responsibility amounts (group PR) of one CAS segment: CAS01 the group, then up to six (reason, amount,
    quantity) triples."""
    if seg.get(1) != "PR":
        return 0
    total = 0
    for i in range(3, 19, 3):
        c = cents(seg.get(i))
        if c:
            total += c
    return total


def _resp_rank(resp: str | None) -> int:
    r = (resp or "").strip().upper()
    i = _RESP_ORDER.find(r) if len(r) == 1 else -1
    return i if i >= 0 else len(_RESP_ORDER)


def _payers_before(others: list[dict], own: str | None) -> list[dict]:
    """The other payers that answered before the claim's own payer: those whose place (2320 SBR01) comes before the
    claim's own (SBR01: P before S before T ...). A claim with no known place of its own (no SBR01, or U) has only the
    other payers whose loop shows an adjudication (AMT*D, AMT*EAF, CAS or SVD) answering before it: the guide asks for
    loop 2320 whenever another payer may pay, so a payer listed there has not answered unless its loop says so."""
    rank = _resp_rank(own)
    if rank < len(_RESP_ORDER):
        return [o for o in others if _resp_rank(o["resp"]) < rank]
    return [o for o in others if o["adjudicated"]]


def later_payers_send(resp: str | None, got: dict) -> bool:
    """Whether a claim went to its payer as a second or later payer, after another payer answered it: its own place
    (SBR01) is a later one than P; with no place of its own on file (no SBR01, or U), only when the loop of the payer
    before it shows an answer (what it paid, what it left the patient, its PR adjustments; or, read since READ_VERSION 3,
    any adjudication). A claim to its primary that lists the secondary in loop 2320, as the guide asks, is the primary's
    (fx-837-r1-4, r2-1). got: the claim's other_* fields and read_version, as claims_837 keeps them."""
    r = (resp or "").strip().upper()
    if _resp_rank(r) < len(_RESP_ORDER):
        return r != "P"
    if any(got.get(k) is not None for k in ("other_paid_cents", "other_left_cents", "other_pr_cents")):
        return True
    return bool(got.get("other_payer_name") and (got.get("read_version") or 0) >= 3)


def _svd_owner(others: list[dict], svd_payer: str | None, own: str | None) -> dict:
    """The other payer a line's adjudication (2430 SVD, SVD01 its payer id) belongs to: the one with that id, else the
    payer just before the claim's own place, else the last listed."""
    hit = next((x for x in others if x["id"] and x["id"] == svd_payer), None)
    if hit is not None:
        return hit
    before = [o for o in others if _resp_rank(o["resp"]) < _resp_rank(own)]
    return max(before, key=lambda o: _resp_rank(o["resp"])) if before else others[-1]


def parse_837_bytes(data: bytes, source: str = "", salt: bytes | None = None) -> dict[str, list[dict]]:
    """The claims and lines of an 837P file. With the store's key salt, each claim carries its patient's keyed name
    (patient_key) and keyed name and birth date (person_key); without one (a preview of an upload) they are None. No name
    or birth date is ever returned."""
    text = data.decode("utf-8", errors="replace").lstrip("﻿")
    sha = hashlib.sha256(data).hexdigest()
    claims: list[dict] = []
    lines: list[dict] = []
    if salt is not None:
        from .ema_reports import name_key_parts, person_key
    for env_index, env in enumerate(split_envelopes(text)):
        d = sniff_delims(env)
        st_control = None
        bht_created = None
        payer_name = payer_id = billing_npi = None
        claim: dict | None = None
        line: dict | None = None
        claim_seq = 0
        line_seq = 0
        rendering_npi = None
        subscriber_id = None
        # the subscriber loop's (2000B/2010BA) and the patient loop's (2000C/2010CA) own fields: never the other payer's
        sub = {"resp": None, "rel": None, "cfi": None, "last": None, "first": None, "dob": None}
        pat: dict | None = None
        where = ""          # inside a claim: "claim" (2300/2310), "other" (2320/2330), "line" (2400/2420), "line_other" (2430)
        level = ""          # the HL level the segments before a claim belong to: "subscriber" or "patient"
        others: list[dict] = []
        svd_payer = None

        def keys() -> tuple[Any, Any, Any, Any]:
            who = pat if pat is not None else sub
            rel = (pat or {}).get("rel") if pat is not None else sub.get("rel")
            if salt is None or not (who.get("last") or who.get("first")):
                return rel, None, None, None
            pk = name_key_parts(salt, who.get("last"), who.get("first"))
            name = f"{who.get('last') or ''}, {who.get('first') or ''}"
            return (rel, pk, (person_key(salt, name, who["dob"]) if who.get("dob") else None),
                    name_key_parts(salt, None, who.get("first")))

        def finish_claim():
            nonlocal claim, line, others
            if line is not None:
                lines.append(line)
                line = None
            if claim is not None:
                claim["line_count"] = sum(1 for x in lines if x["claim_837_key"] == claim["claim_837_key"])
                before = _payers_before(others, claim.get("payer_resp"))
                if before:
                    # the payer just before this claim's own place answered last; what every payer before it paid
                    last = max(before, key=lambda o: _resp_rank(o["resp"]))
                    claim["other_payer_name"], claim["other_payer_id"], claim["other_payer_resp"] = last["name"], last["id"], last["resp"]
                    paid = [o["paid"] for o in before if o["paid"] is not None]
                    claim["other_paid_cents"] = sum(paid) if paid else None
                    claim["other_left_cents"] = last["left"]
                    pr = last["pr"] + last["line_pr"]
                    claim["other_pr_cents"] = pr if (last["has_pr"] or pr) else None
                claims.append(claim)
                claim = None
            others = []

        for seg in iter_segments(env, d):
            sid = seg.id
            if sid == "ST":
                finish_claim()
                st_control = seg.get(2)
                where = ""
            elif sid == "BHT":
                bht_created = iso_date(seg.get(4))
            elif sid == "HL":
                hl = seg.get(3)
                if hl == "20":
                    finish_claim()
                    level, where = "billing", ""
                elif hl == "22":
                    finish_claim()
                    level, where = "subscriber", ""
                    # a new subscriber: none of the previous one's payer, member id or names carries over
                    payer_name = payer_id = subscriber_id = None
                    sub = {"resp": None, "rel": None, "cfi": None, "last": None, "first": None, "dob": None}
                    pat = None
                elif hl == "23":
                    finish_claim()
                    level, where = "patient", ""
                    pat = {"rel": None, "last": None, "first": None, "dob": None}
            elif sid == "SBR":
                if claim is None:
                    # 2000B: this payer's responsibility for the claims below, the relationship (18: the subscriber is the
                    # patient) and the claim filing indicator
                    sub["resp"], sub["rel"], sub["cfi"] = seg.get(1) or None, seg.get(2) or None, seg.get(9) or None
                else:
                    # 2320: another payer of this claim (its responsibility); its loops follow until the service lines
                    where = "other"
                    others.append({"resp": seg.get(1) or None, "name": None, "id": None, "paid": None, "left": None, "pr": 0,
                                   "line_pr": 0, "has_pr": False, "adjudicated": False})
            elif sid == "PAT" and claim is None:
                if pat is not None and seg.get(1):
                    pat["rel"] = seg.get(1)
            elif sid == "DMG" and claim is None:
                if seg.get(1) == "D8":
                    who = pat if level == "patient" and pat is not None else sub
                    who["dob"] = iso_date(seg.get(2))
            elif sid == "NM1":
                ent = seg.get(1)
                if where in ("other", "line_other"):
                    # 2330A to 2330G: the other payer's own subscriber, payer and providers. Only its payer is kept
                    if ent == "PR" and others:
                        others[-1]["name"], others[-1]["id"] = seg.get(3) or None, seg.get(9) or None
                    continue
                if ent == "85":
                    billing_npi = seg.get(9) if seg.get(8) == "XX" else billing_npi
                elif ent == "PR" and claim is None:
                    payer_name = seg.get(3)
                    payer_id = seg.get(9) or payer_id
                elif ent == "IL" and claim is None:
                    subscriber_id = seg.get(9) or subscriber_id
                    sub["last"], sub["first"] = seg.get(3) or None, seg.get(4) or None
                elif ent == "QC" and claim is None and pat is not None:
                    pat["last"], pat["first"] = seg.get(3) or None, seg.get(4) or None
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
                where = "claim"
                pos = seg.comp(5, d)
                rel, pk, prk, fk = keys()
                claim = {
                    "claim_837_key": f"{sha[:16]}:{env_index}:{st_control}:{claim_seq}", "file_sha256": sha,
                    "source_name": os.path.basename(source) if source else None, "st_control": st_control, "bht_created": bht_created,
                    "patient_control_number": seg.get(1), "total_charge_cents": cents(seg.get(2)),
                    "frequency_code": pos[2] if len(pos) > 2 else None, "place_of_service": pos[0] if pos else None,
                    "payer_name": payer_name, "payer_id": payer_id, "billing_npi": billing_npi, "rendering_npi": rendering_npi,
                    "subscriber_id": subscriber_id, "service_date": None, "original_reference_number": None, "prior_auth": None,
                    "line_count": 0, "payer_resp": sub["resp"], "relationship": rel, "filing_indicator": sub["cfi"],
                    "patient_key": pk, "person_key": prk, "other_payer_name": None, "other_payer_id": None, "other_payer_resp": None,
                    "other_paid_cents": None, "other_left_cents": None, "other_pr_cents": None, "read_version": READ_VERSION,
                    "first_key": fk,
                }
            elif claim is None:
                continue
            elif sid == "CAS":
                pr = _cas_pr(seg)
                if where == "other" and others:
                    others[-1]["pr"] += pr
                    others[-1]["has_pr"] = others[-1]["has_pr"] or seg.get(1) == "PR"
                    others[-1]["adjudicated"] = True
                elif where == "line_other" and others:
                    o = _svd_owner(others, svd_payer, claim.get("payer_resp"))
                    o["line_pr"] += pr
                    o["has_pr"] = o["has_pr"] or seg.get(1) == "PR"
                    o["adjudicated"] = True
            elif sid == "AMT":
                if where == "other" and others:
                    q = seg.get(1)
                    if q == "D":
                        others[-1]["paid"] = cents(seg.get(2))
                        others[-1]["adjudicated"] = True
                    elif q == "EAF":
                        others[-1]["left"] = cents(seg.get(2))
                        others[-1]["adjudicated"] = True
            elif sid == "SVD":
                where = "line_other"
                svd_payer = seg.get(1) or None
                if others:
                    _svd_owner(others, svd_payer, claim.get("payer_resp"))["adjudicated"] = True
            elif sid == "REF":
                q, v = seg.get(1), seg.get(2)
                if where in ("other", "line_other"):
                    continue
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
                where = "line"
            elif sid == "SV1":
                if line is not None:
                    lines.append(line)
                line_seq += 1
                where = "line"
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
                where = ""
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


def _salt(conn: sqlite3.Connection) -> bytes:
    """The store's key salt (ema_reports.key_salt: made on first use by a reader, never leaves the store)."""
    from .ema_reports import key_salt
    return key_salt(conn)


def ingest_837_file(store, path: str) -> dict:
    with open(path, "rb") as fh:
        data = fh.read()
    sha = hashlib.sha256(data).hexdigest()
    conn = store.conn
    ensure_schema(conn)
    if store.file_seen(sha):
        return {"skipped": True}
    parsed = parse_837_bytes(data, source=path, salt=_salt(conn))
    conn.executemany(f"INSERT OR IGNORE INTO claims_837 ({','.join(CLAIM_COLS)}) VALUES ({','.join('?' * len(CLAIM_COLS))})",
                     [tuple(c.get(k) for k in CLAIM_COLS) for c in parsed["claims"]])
    conn.executemany(f"INSERT OR IGNORE INTO lines_837 ({','.join(LINE_COLS)}) VALUES ({','.join('?' * len(LINE_COLS))})",
                     [tuple(l.get(k) for k in LINE_COLS) for l in parsed["lines"]])
    store.record_file(sha, path, kind="837", status="ok", claims=len(parsed["claims"]), lines=len(parsed["lines"]))
    conn.commit()
    return {"claims": len(parsed["claims"]), "lines": len(parsed["lines"])}


def files_to_reread(conn: sqlite3.Connection) -> list[str]:
    """The 837 files (by sha256) whose claims were read before READ_VERSION: their patient, payer responsibility and
    other payer are not on file until the file is read again (reread_837_bytes)."""
    ensure_schema(conn)
    return [r[0] for r in conn.execute("""SELECT DISTINCT file_sha256 FROM claims_837 WHERE file_sha256 IS NOT NULL
                                          AND COALESCE(read_version, 0) < ? ORDER BY file_sha256""", (READ_VERSION,))]


def reread_837_bytes(conn: sqlite3.Connection, data: bytes) -> dict:
    """Fill in what an 837 file read before READ_VERSION left out (the claim's patient, the payer's responsibility, the
    claim filing indicator and the other payer), and rewrite what the reader before READ_VERSION 2 took from another
    payer's loops (a claim's payer, payer id, member id and rendering clinician, and its lines' rendering clinician), by
    each claim's and line's key: the same file read again makes the same keys. The rest of each row stays as it is. A
    file the store never read changes nothing."""
    ensure_schema(conn)
    parsed = parse_837_bytes(data, salt=_salt(conn))
    cols = list(EXTRA_CLAIM_COLS) + REREAD_CLAIM_COLS
    cur = conn.executemany(f"UPDATE claims_837 SET {', '.join(c + '=?' for c in cols)} WHERE claim_837_key=?",
                           [tuple(c.get(k) for k in cols) + (c["claim_837_key"],) for c in parsed["claims"]])
    updated = cur.rowcount if cur.rowcount is not None else 0
    conn.executemany("UPDATE lines_837 SET rendering_npi=? WHERE line_837_key=?",
                     [(ln.get("rendering_npi"), ln["line_837_key"]) for ln in parsed["lines"]])
    conn.commit()
    return {"claims": len(parsed["claims"]), "updated": updated}
