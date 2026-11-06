"""
X12 005010X221A1 (835 Health Care Claim Payment/Advice) parser.

Produces flat, normalized rows that load straight into a relational store:

    transactions  one row per ST/SE (one 835)
    claims        one row per CLP (loop 2100)
    lines         one row per SVC (loop 2110)
    adjustments   one row per CAS triplet, at claim or line level
    remarks       one row per RARC (LQ HE at line level, MOA/MIA at claim level)
    plb           one row per PLB adjustment pair

Money is kept in integer cents (never floats). Dates are ISO strings.
The parser is deliberately permissive: a malformed segment is recorded in
`problems` rather than aborting the file, because one bad remit must not
stop a weekly batch.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Any, Iterator

VERSION = "1.0"

X12_EXTENSIONS = (".rmt", ".835", ".era", ".edi", ".txt", ".x12", ".dat")


class X12ParseError(ValueError):
    pass


@dataclass(slots=True)
class Delims:
    element: str
    component: str
    segment: str
    repetition: str | None


@dataclass
class Parsed835:
    source: str
    sha256: str
    transactions: list[dict] = field(default_factory=list)
    claims: list[dict] = field(default_factory=list)
    lines: list[dict] = field(default_factory=list)
    adjustments: list[dict] = field(default_factory=list)
    remarks: list[dict] = field(default_factory=list)
    plb: list[dict] = field(default_factory=list)
    problems: list[dict] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return {
            "transactions": len(self.transactions),
            "claims": len(self.claims),
            "lines": len(self.lines),
            "adjustments": len(self.adjustments),
            "remarks": len(self.remarks),
            "plb": len(self.plb),
            "problems": len(self.problems),
        }


# ----------------------------------------------------------------------------
# low level helpers
# ----------------------------------------------------------------------------

def cents(value: str | None) -> int | None:
    """'293.16' -> 29316 ; '-41.41' -> -4141 ; '' -> None. Never floats."""
    if value is None:
        return None
    v = value.strip()
    if not v:
        return None
    neg = v.startswith("-")
    if neg or v.startswith("+"):
        v = v[1:]
    if "." in v:
        whole, _, frac = v.partition(".")
    else:
        whole, frac = v, ""
    frac = (frac + "00")[:2]
    try:
        n = int(whole or "0") * 100 + int(frac)
    except ValueError:
        return None
    return -n if neg else n


def to_float(value: str | None) -> float | None:
    if value is None or not value.strip():
        return None
    try:
        return float(value)
    except ValueError:
        return None


def iso_date(value: str | None) -> str | None:
    """CCYYMMDD -> YYYY-MM-DD ; YYMMDD -> YYYY-MM-DD (20xx) ; otherwise None."""
    if not value:
        return None
    v = value.strip()
    if len(v) == 8 and v.isdigit():
        return f"{v[0:4]}-{v[4:6]}-{v[6:8]}"
    if len(v) == 6 and v.isdigit():
        return f"20{v[0:2]}-{v[2:4]}-{v[4:6]}"
    return None


def sniff_delims(text: str, pos: int = 0) -> Delims:
    """Derive delimiters from the ISA segment that starts at `pos`."""
    if text[pos:pos + 3] != "ISA":
        raise X12ParseError(f"no ISA segment at offset {pos}")
    elem = text[pos + 3]
    head = text[pos:pos + 400]
    parts = head.split(elem)
    if len(parts) < 17 or len(parts[16]) < 2:
        raise X12ParseError("ISA segment too short to determine delimiters")
    comp = parts[16][0]
    seg = parts[16][1]
    version = parts[12].strip()
    rep: str | None = parts[11] if len(parts[11]) == 1 else None
    if version < "00402" or rep in (None, "U", " ", elem, comp, seg):
        rep = None
    return Delims(elem, comp, seg, rep)


def split_envelopes(text: str) -> list[str]:
    """A file may carry several ISA/IEA envelopes; split them apart.

    The first ISA defines the delimiters. A new envelope starts only where
    "ISA" + element separator appears at the beginning of the text or right
    after a segment terminator (optionally followed by line breaks), so an
    "ISA" inside a payer name never splits a file.
    """
    text = text.lstrip("\r\n \t")
    if not text.startswith("ISA"):
        first = text.find("ISA")
        if first < 0:
            return []
        text = text[first:]
    try:
        d = sniff_delims(text, 0)
    except X12ParseError:
        return [text]
    marker = "ISA" + d.element
    starts: list[int] = [0]
    idx = 1
    while True:
        i = text.find(marker, idx)
        if i < 0:
            break
        j = i - 1
        while j >= 0 and text[j] in "\r\n \t":
            j -= 1
        if j >= 0 and text[j] == d.segment:
            starts.append(i)
        idx = i + 3
    out: list[str] = []
    for n, s in enumerate(starts):
        e = starts[n + 1] if n + 1 < len(starts) else len(text)
        chunk = text[s:e].strip()
        if chunk:
            out.append(chunk)
    return out


class Seg:
    __slots__ = ("id", "el", "raw")

    def __init__(self, raw: str, d: Delims):
        self.raw = raw
        parts = raw.split(d.element)
        self.id = parts[0].strip()
        self.el = parts

    def get(self, i: int, default: str = "") -> str:
        if i < len(self.el):
            return self.el[i].strip()
        return default

    def comp(self, i: int, d: Delims) -> list[str]:
        v = self.get(i)
        return v.split(d.component) if v else []

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Seg {self.raw[:60]!r}>"


def iter_segments(envelope: str, d: Delims) -> Iterator[Seg]:
    for raw in envelope.split(d.segment):
        raw = raw.strip("\r\n \t")
        if not raw:
            continue
        yield Seg(raw, d)


def parse_procedure(composite: list[str]) -> dict[str, Any]:
    """SVC01/SVC06 composite -> qualifier, code, modifiers list, description."""
    if not composite:
        return {"qualifier": None, "code": None, "modifiers": [], "description": None}
    qualifier = composite[0] or None
    code = composite[1] if len(composite) > 1 else None
    mods = [m for m in composite[2:6] if m]
    desc = composite[6] if len(composite) > 6 else None
    return {"qualifier": qualifier, "code": code or None, "modifiers": mods, "description": desc or None}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ----------------------------------------------------------------------------
# main parser
# ----------------------------------------------------------------------------

def parse_835_bytes(data: bytes, source: str = "") -> Parsed835:
    text = data.decode("utf-8", errors="replace")
    if text.startswith("﻿"):
        text = text[1:]
    result = Parsed835(source=source, sha256=sha256_bytes(data))
    envelopes = split_envelopes(text)
    if not envelopes:
        result.problems.append({"source": source, "level": "file", "message": "no ISA envelope found"})
        return result
    for env_index, env in enumerate(envelopes):
        try:
            d = sniff_delims(env)
        except X12ParseError as exc:
            result.problems.append({"source": source, "level": "envelope", "message": str(exc), "envelope": env_index})
            continue
        _parse_envelope(env, d, env_index, result)
    return result


def parse_835_file(path: str) -> Parsed835:
    with open(path, "rb") as fh:
        data = fh.read()
    return parse_835_bytes(data, source=path)


def _parse_envelope(env: str, d: Delims, env_index: int, r: Parsed835) -> None:
    src = r.source
    isa: dict[str, Any] = {}
    gs: dict[str, Any] = {}
    txn: dict[str, Any] | None = None
    claim: dict[str, Any] | None = None
    line: dict[str, Any] | None = None
    loop = "header"          # header | payer | payee | claim | line
    n1_entity = ""           # PR or PE while in 1000A/1000B
    claim_seq = 0
    line_seq = 0
    lx_number: str | None = None
    txn_seq = 0

    def problem(msg: str, seg: Seg | None = None) -> None:
        r.problems.append({
            "source": src, "level": "segment", "message": msg,
            "segment": seg.raw[:120] if seg else None,
            "transaction": txn.get("st_control") if txn else None,
            "claim": claim.get("patient_control_number") if claim else None,
        })

    def txn_key() -> str:
        assert txn is not None
        return txn["transaction_key"]

    def finish_line() -> None:
        nonlocal line
        if line is None:
            return
        adj_total = sum(a["amount_cents"] or 0 for a in r.adjustments
                        if a["line_key"] == line["line_key"])
        charge = line["charge_cents"] or 0
        paid = line["paid_cents"] or 0
        line["adjustment_total_cents"] = adj_total
        line["balanced"] = (charge - adj_total == paid)
        r.lines.append(line)
        line = None

    def finish_claim() -> None:
        nonlocal claim
        finish_line()
        if claim is None:
            return
        ck = claim["claim_key"]
        claim_adj = sum(a["amount_cents"] or 0 for a in r.adjustments
                        if a["claim_key"] == ck and a["level"] == "claim")
        line_adj = sum(a["amount_cents"] or 0 for a in r.adjustments
                       if a["claim_key"] == ck and a["level"] == "line")
        lines = [l for l in r.lines if l["claim_key"] == ck]
        claim["line_count"] = len(lines)
        claim["line_charge_total_cents"] = sum(l["charge_cents"] or 0 for l in lines)
        claim["line_paid_total_cents"] = sum(l["paid_cents"] or 0 for l in lines)
        # CLP04 minus the sum of SVC03: claim-level money not attributed to a line (interest, claim-level adjustments)
        claim["claim_paid_minus_lines_cents"] = (claim["paid_cents"] or 0) - claim["line_paid_total_cents"]
        claim["claim_adjustment_total_cents"] = claim_adj
        claim["line_adjustment_total_cents"] = line_adj
        charge = claim["charge_cents"] or 0
        paid = claim["paid_cents"] or 0
        claim["balanced"] = (charge - claim_adj - line_adj == paid)
        r.claims.append(claim)
        claim = None

    def finish_txn() -> None:
        nonlocal txn
        finish_claim()
        if txn is None:
            return
        tk = txn["transaction_key"]
        claims = [c for c in r.claims if c["transaction_key"] == tk]
        plbs = [p for p in r.plb if p["transaction_key"] == tk]
        txn["claim_count"] = len(claims)
        txn["claim_paid_total_cents"] = sum(c["paid_cents"] or 0 for c in claims)
        txn["plb_total_cents"] = sum(p["amount_cents"] or 0 for p in plbs)
        total = txn["payment_amount_cents"] or 0
        # BPR02 = sum(CLP04) - sum(PLB04)
        txn["balanced"] = (txn["claim_paid_total_cents"] - txn["plb_total_cents"] == total)
        r.transactions.append(txn)
        txn = None

    raw_segments: list[str] = []

    for seg in iter_segments(env, d):
        sid = seg.id
        if txn is not None:
            raw_segments.append(seg.raw)
        try:
            if sid == "ISA":
                isa = {
                    "isa_sender_qual": seg.get(5), "isa_sender": seg.get(6),
                    "isa_receiver_qual": seg.get(7), "isa_receiver": seg.get(8),
                    "isa_date": iso_date(seg.get(9)), "isa_time": seg.get(10),
                    "isa_version": seg.get(12), "isa_control": seg.get(13),
                    "isa_usage": seg.get(15),
                }
            elif sid == "GS":
                gs = {
                    "gs_functional_id": seg.get(1), "gs_sender": seg.get(2), "gs_receiver": seg.get(3),
                    "gs_date": iso_date(seg.get(4)), "gs_time": seg.get(5), "gs_control": seg.get(6),
                    "gs_version": seg.get(8),
                }
            elif sid == "ST":
                finish_txn()
                txn_seq += 1
                st_control = seg.get(2)
                raw_segments = []
                txn = {
                    "transaction_key": f"{r.sha256[:16]}:{env_index}:{st_control or txn_seq}",
                    "source": src, "source_name": os.path.basename(src) if src else None,
                    "file_sha256": r.sha256, "envelope_index": env_index,
                    "st_type": seg.get(1), "st_control": st_control,
                    **isa, **gs,
                    "payment_amount_cents": None, "credit_debit": None, "payment_method": None,
                    "payment_format": None, "payment_date": None, "payer_bank_routing": None,
                    "payer_account": None, "payee_bank_routing": None, "payee_account": None,
                    "trace_type": None, "trace_number": None, "trace_payer_id": None, "trace_originating_company": None,
                    "receiver_id": None, "version_id": None, "production_date": None,
                    "payer_name": None, "payer_id": None, "payer_address": None, "payer_city": None,
                    "payer_state": None, "payer_zip": None, "payer_submitter_id": None, "payer_contact": None,
                    "payer_contact_phone": None, "payer_contact_email": None, "payer_contact_url": None,
                    "payee_name": None, "payee_npi": None, "payee_id_qual": None, "payee_id": None,
                    "payee_tin": None, "payee_address": None, "payee_city": None, "payee_state": None, "payee_zip": None,
                    "payee_other_ids": {},
                    "segment_count": None, "content_fingerprint": None,
                }
                loop = "header"
                claim_seq = 0
            elif txn is None:
                # segments outside a transaction (GE, IEA) are fine
                if sid not in ("GE", "IEA"):
                    problem(f"segment {sid} outside ST", seg)
            elif sid == "BPR":
                txn.update({
                    "payment_amount_cents": cents(seg.get(2)), "credit_debit": seg.get(3),
                    "payment_method": seg.get(4), "payment_format": seg.get(5),
                    "payer_bank_routing": seg.get(7) or None, "payer_account": seg.get(9) or None,
                    "trace_originating_company": seg.get(10) or None,
                    "payee_bank_routing": seg.get(13) or None, "payee_account": seg.get(15) or None,
                    "payment_date": iso_date(seg.get(16)),
                })
            elif sid == "TRN":
                txn.update({"trace_type": seg.get(1), "trace_number": seg.get(2), "trace_payer_id": seg.get(3),
                            "trace_originating_company": seg.get(4) or txn.get("trace_originating_company")})
            elif sid == "CUR":
                txn["currency"] = seg.get(2)
            elif sid == "LX":
                finish_claim()
                lx_number = seg.get(1)
                loop = "claim_group"
            elif sid == "N1":
                finish_claim()
                n1_entity = seg.get(1)
                if n1_entity == "PR":
                    loop = "payer"
                    txn["payer_name"] = seg.get(2)
                    if seg.get(3) and seg.get(4):
                        txn["payer_id"] = txn.get("payer_id") or seg.get(4)
                elif n1_entity == "PE":
                    loop = "payee"
                    txn["payee_name"] = seg.get(2)
                    txn["payee_id_qual"] = seg.get(3) or None
                    txn["payee_id"] = seg.get(4) or None
                    if seg.get(3) == "XX":
                        txn["payee_npi"] = seg.get(4)
                else:
                    loop = "header"
            elif sid == "N3":
                if loop == "payer":
                    txn["payer_address"] = " ".join(x for x in (seg.get(1), seg.get(2)) if x)
                elif loop == "payee":
                    txn["payee_address"] = " ".join(x for x in (seg.get(1), seg.get(2)) if x)
            elif sid == "N4":
                if loop == "payer":
                    txn["payer_city"], txn["payer_state"], txn["payer_zip"] = seg.get(1), seg.get(2), seg.get(3)
                elif loop == "payee":
                    txn["payee_city"], txn["payee_state"], txn["payee_zip"] = seg.get(1), seg.get(2), seg.get(3)
            elif sid == "PER":
                if loop == "payer":
                    txn["payer_contact"] = txn.get("payer_contact") or seg.get(2) or seg.get(1)
                    for qi, vi in ((3, 4), (5, 6), (7, 8)):
                        q, v = seg.get(qi), seg.get(vi)
                        if q == "TE" and v:
                            txn["payer_contact_phone"] = txn.get("payer_contact_phone") or v
                        elif q == "EM" and v:
                            txn["payer_contact_email"] = txn.get("payer_contact_email") or v
                        elif q == "UR" and v:
                            txn["payer_contact_url"] = txn.get("payer_contact_url") or v
                elif loop == "claim" and claim is not None:
                    for qi, vi in ((3, 4), (5, 6), (7, 8)):
                        if seg.get(qi) == "TE" and seg.get(vi):
                            claim["payer_claim_contact_phone"] = seg.get(vi)
            elif sid == "REF":
                q, v = seg.get(1), seg.get(2)
                if loop == "header":
                    if q == "EV":
                        txn["receiver_id"] = v
                    elif q == "F2":
                        txn["version_id"] = v
                elif loop == "payer":
                    if q == "2U":
                        txn["payer_id"] = v
                    elif q == "EO":
                        txn["payer_submitter_id"] = v
                    else:
                        txn.setdefault("payer_other_ids", {})[q] = v
                elif loop == "payee":
                    if q == "TJ":
                        txn["payee_tin"] = v
                    else:
                        txn["payee_other_ids"][q] = v
                elif loop == "line" and line is not None:
                    if q == "6R":
                        line["line_control_number"] = v
                    elif q == "LU":
                        line["location_number"] = v
                    else:
                        line["other_refs"][q] = v
                elif loop == "claim" and claim is not None:
                    if q == "1L":
                        claim["group_number"] = v
                    elif q == "CE":
                        claim["contract_class"] = v
                    elif q == "F8":
                        claim["original_reference_number"] = v
                    elif q == "EA":
                        claim["medical_record_number"] = v
                    elif q == "1W":
                        claim["member_id_ref"] = v
                    elif q == "BB":
                        claim["authorization_number"] = v
                    elif q == "G1":
                        claim["prior_authorization"] = v
                    else:
                        claim["other_refs"][q] = v
            elif sid == "DTM":
                q, v = seg.get(1), iso_date(seg.get(2))
                if loop in ("header", "payer", "payee"):
                    if q == "405":
                        txn["production_date"] = v
                elif loop == "line" and line is not None:
                    if q == "472":
                        line["service_date"] = v
                    elif q == "150":
                        line["service_period_start"] = v
                    elif q == "151":
                        line["service_period_end"] = v
                    else:
                        line["other_dates"][q] = v
                elif loop == "claim" and claim is not None:
                    if q == "050":
                        claim["received_date"] = v
                    elif q == "232":
                        claim["statement_from"] = v
                    elif q == "233":
                        claim["statement_to"] = v
                    elif q == "036":
                        claim["coverage_expiration"] = v
                    else:
                        claim["other_dates"][q] = v
            elif sid == "CLP":
                finish_claim()
                claim_seq += 1
                line_seq = 0
                pcn = seg.get(1)
                ck = f"{txn_key()}:{claim_seq}"
                claim = {
                    "claim_key": ck, "transaction_key": txn_key(), "file_sha256": r.sha256,
                    "claim_seq": claim_seq, "lx_number": lx_number,
                    "patient_control_number": pcn, "claim_status_code": seg.get(2),
                    "charge_cents": cents(seg.get(3)), "paid_cents": cents(seg.get(4)),
                    "patient_responsibility_cents": cents(seg.get(5)),
                    "claim_filing_indicator": seg.get(6) or None,
                    "payer_claim_control_number": seg.get(7) or None,
                    "facility_type_code": seg.get(8) or None,
                    "claim_frequency_code": seg.get(9) or None,
                    "drg_code": seg.get(11) or None, "drg_weight": to_float(seg.get(12)),
                    "patient_last": None, "patient_first": None, "patient_middle": None,
                    "patient_id_qual": None, "patient_id": None,
                    "insured_last": None, "insured_first": None, "insured_id_qual": None, "insured_id": None,
                    "corrected_patient_last": None, "corrected_patient_first": None,
                    "rendering_provider_name": None, "rendering_provider_last": None, "rendering_provider_first": None,
                    "rendering_provider_npi": None, "rendering_provider_id_qual": None, "rendering_provider_id": None,
                    "crossover_carrier_name": None, "crossover_carrier_id": None,
                    "corrected_payer_name": None, "corrected_payer_id": None,
                    "group_number": None, "contract_class": None, "original_reference_number": None,
                    "medical_record_number": None, "member_id_ref": None, "authorization_number": None,
                    "prior_authorization": None, "other_refs": {},
                    "received_date": None, "statement_from": None, "statement_to": None,
                    "coverage_expiration": None, "other_dates": {},
                    "coverage_amount_cents": None, "other_amounts": {}, "quantities": {},
                    "moa_reimbursement_rate": None, "moa_hcpcs_payable_cents": None,
                    "moa_esrd_cents": None, "moa_nonpayable_professional_cents": None,
                    "payer_claim_contact_phone": None,
                    # payer/payee context copied for convenient querying
                    "payer_name": txn.get("payer_name"), "payer_id": txn.get("payer_id"),
                    "payee_npi": txn.get("payee_npi"), "payment_date": txn.get("payment_date"),
                    "trace_number": txn.get("trace_number"),
                }
                loop = "claim"
            elif claim is None and sid in ("NM1", "MIA", "MOA", "SVC", "CAS", "AMT", "QTY", "LQ"):
                problem(f"segment {sid} before any CLP", seg)
            elif sid == "NM1":
                assert claim is not None
                ent = seg.get(1)
                if ent == "QC":
                    claim.update({"patient_last": seg.get(3), "patient_first": seg.get(4), "patient_middle": seg.get(5),
                                  "patient_id_qual": seg.get(8) or None, "patient_id": seg.get(9) or None})
                elif ent == "IL":
                    claim.update({"insured_last": seg.get(3), "insured_first": seg.get(4),
                                  "insured_id_qual": seg.get(8) or None, "insured_id": seg.get(9) or None})
                elif ent == "74":
                    claim.update({"corrected_patient_last": seg.get(3), "corrected_patient_first": seg.get(4)})
                elif ent == "82":
                    claim.update({
                        "rendering_provider_name": " ".join(x for x in (seg.get(3), seg.get(4), seg.get(5)) if x),
                        "rendering_provider_last": seg.get(3), "rendering_provider_first": seg.get(4),
                        "rendering_provider_id_qual": seg.get(8) or None, "rendering_provider_id": seg.get(9) or None,
                        "rendering_provider_npi": seg.get(9) if seg.get(8) == "XX" else None,
                    })
                elif ent == "TT":
                    claim.update({"crossover_carrier_name": seg.get(3), "crossover_carrier_id": seg.get(9) or None})
                elif ent == "PR":
                    claim.update({"corrected_payer_name": seg.get(3), "corrected_payer_id": seg.get(9) or None})
                else:
                    claim["other_refs"][f"NM1_{ent}"] = seg.get(3)
            elif sid == "MOA":
                assert claim is not None
                claim["moa_reimbursement_rate"] = to_float(seg.get(1))
                claim["moa_hcpcs_payable_cents"] = cents(seg.get(2))
                for i in (3, 4, 5, 6, 7):
                    code = seg.get(i)
                    if code:
                        r.remarks.append({"claim_key": claim["claim_key"], "line_key": None, "level": "claim",
                                          "qualifier": "MOA", "code": code, "transaction_key": txn_key()})
                claim["moa_esrd_cents"] = cents(seg.get(8))
                claim["moa_nonpayable_professional_cents"] = cents(seg.get(9))
            elif sid == "MIA":
                assert claim is not None
                for i in (5, 20, 21, 22, 23):
                    code = seg.get(i)
                    if code:
                        r.remarks.append({"claim_key": claim["claim_key"], "line_key": None, "level": "claim",
                                          "qualifier": "MIA", "code": code, "transaction_key": txn_key()})
            elif sid == "SVC":
                assert claim is not None
                finish_line()
                line_seq += 1
                proc = parse_procedure(seg.comp(1, d))
                orig = parse_procedure(seg.comp(6, d))
                lk = f"{claim['claim_key']}:{line_seq}"
                paid_units = to_float(seg.get(5))
                orig_units = to_float(seg.get(7))
                line = {
                    "line_key": lk, "claim_key": claim["claim_key"], "transaction_key": txn_key(),
                    "file_sha256": r.sha256, "line_seq": line_seq,
                    "procedure_qualifier": proc["qualifier"], "procedure_code": proc["code"],
                    "modifiers": proc["modifiers"], "modifier_1": (proc["modifiers"] + [None])[0],
                    "modifier_2": (proc["modifiers"] + [None, None])[1],
                    "procedure_description": proc["description"],
                    "charge_cents": cents(seg.get(2)), "paid_cents": cents(seg.get(3)),
                    "revenue_code": seg.get(4) or None,
                    "paid_units": paid_units,
                    "original_procedure_qualifier": orig["qualifier"], "original_procedure_code": orig["code"],
                    "original_modifiers": orig["modifiers"], "original_units": orig_units,
                    "code_changed": bool(orig["code"]) and orig["code"] != proc["code"],
                    "modifiers_changed": bool(orig["code"]) and sorted(orig["modifiers"]) != sorted(proc["modifiers"]),
                    "service_date": None, "service_period_start": None, "service_period_end": None, "other_dates": {},
                    "line_control_number": None, "location_number": None, "other_refs": {},
                    "allowed_cents": None, "other_amounts": {}, "quantities": {},
                    "adjustment_total_cents": None, "balanced": None,
                    # context copied for convenient querying
                    "patient_control_number": claim["patient_control_number"],
                    "claim_status_code": claim["claim_status_code"],
                    "payer_name": txn.get("payer_name"), "payer_id": txn.get("payer_id"),
                    "payment_date": txn.get("payment_date"),
                    "rendering_provider_npi": claim.get("rendering_provider_npi"),
                }
                loop = "line"
            elif sid == "CAS":
                group = seg.get(1)
                target_level = "line" if (loop == "line" and line is not None) else "claim"
                target_claim_key = claim["claim_key"] if claim else None
                target_line_key = line["line_key"] if (target_level == "line" and line) else None
                n_triplets = 0
                for base in (2, 5, 8, 11, 14, 17):
                    reason = seg.get(base)
                    if not reason:
                        break
                    n_triplets += 1
                    r.adjustments.append({
                        "transaction_key": txn_key(), "claim_key": target_claim_key, "line_key": target_line_key,
                        "level": target_level, "group_code": group, "reason_code": reason,
                        "amount_cents": cents(seg.get(base + 1)), "quantity": to_float(seg.get(base + 2)),
                        "cas_index": n_triplets,
                    })
                if n_triplets == 0:
                    problem("CAS without reason code", seg)
            elif sid == "AMT":
                q, v = seg.get(1), seg.get(2)
                if loop == "line" and line is not None:
                    if q == "B6":
                        line["allowed_cents"] = cents(v)
                    else:
                        line["other_amounts"][q] = cents(v)
                elif claim is not None:
                    if q == "AU":
                        claim["coverage_amount_cents"] = cents(v)
                    else:
                        claim["other_amounts"][q] = cents(v)
            elif sid == "QTY":
                q, v = seg.get(1), to_float(seg.get(2))
                if loop == "line" and line is not None:
                    line["quantities"][q] = v
                elif claim is not None:
                    claim["quantities"][q] = v
            elif sid == "LQ":
                q, code = seg.get(1), seg.get(2)
                if code:
                    r.remarks.append({
                        "transaction_key": txn_key(),
                        "claim_key": claim["claim_key"] if claim else None,
                        "line_key": line["line_key"] if (loop == "line" and line) else None,
                        "level": "line" if (loop == "line" and line) else "claim",
                        "qualifier": q, "code": code,
                    })
            elif sid == "PLB":
                finish_claim()
                loop = "plb"
                provider_id = seg.get(1)
                fiscal = iso_date(seg.get(2))
                for base in (3, 5, 7, 9, 11, 13):
                    comp = seg.comp(base, d)
                    if not comp or not comp[0]:
                        break
                    r.plb.append({
                        "transaction_key": txn_key(), "file_sha256": r.sha256,
                        "provider_id": provider_id, "fiscal_period_date": fiscal,
                        "reason_code": comp[0], "reference": comp[1] if len(comp) > 1 else None,
                        "amount_cents": cents(seg.get(base + 1)),
                        "payer_name": txn.get("payer_name"), "payer_id": txn.get("payer_id"),
                        "payment_date": txn.get("payment_date"), "trace_number": txn.get("trace_number"),
                    })
            elif sid == "SE":
                txn["segment_count"] = int(seg.get(1)) if seg.get(1).isdigit() else None
                # content fingerprint of the segments between ST and SE (control numbers excluded) lets the
                # store skip the same remittance re-delivered inside a new envelope
                txn["content_fingerprint"] = hashlib.sha256("\n".join(raw_segments[:-1]).encode("utf-8")).hexdigest()
                finish_txn()
            elif sid in ("GE", "IEA", "TS3", "TS2", "RDM"):
                pass
            else:
                problem(f"unhandled segment {sid}", seg)
        except Exception as exc:  # keep going; record the failure
            problem(f"{type(exc).__name__}: {exc}", seg)
    finish_txn()


# ----------------------------------------------------------------------------
# convenience
# ----------------------------------------------------------------------------

def looks_like_x12(path: str, sniff_bytes: int = 4) -> bool:
    try:
        with open(path, "rb") as fh:
            head = fh.read(sniff_bytes)
    except OSError:
        return False
    return head.startswith(b"ISA")


def iter_835_paths(root: str) -> Iterator[str]:
    """Yield files under root that look like 835s (by extension and ISA sniff)."""
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if name.lower().endswith(X12_EXTENSIONS):
                p = os.path.join(dirpath, name)
                if looks_like_x12(p):
                    yield p


if __name__ == "__main__":  # small CLI for smoke tests
    import json
    import sys

    paths: list[str] = []
    for arg in sys.argv[1:]:
        if os.path.isdir(arg):
            paths.extend(iter_835_paths(arg))
        else:
            paths.append(arg)
    totals: dict[str, int] = {}
    unbalanced = 0
    for p in paths:
        res = parse_835_file(p)
        for k, v in res.counts().items():
            totals[k] = totals.get(k, 0) + v
        unbalanced += sum(1 for t in res.transactions if not t["balanced"])
        if len(paths) <= 3:
            print(json.dumps({"source": p, "counts": res.counts(),
                              "transactions": [{k: t[k] for k in ("payer_name", "payer_id", "payment_amount_cents",
                                                                   "payment_date", "trace_number", "balanced",
                                                                   "claim_count", "plb_total_cents")}
                                               for t in res.transactions],
                              "problems": res.problems[:5]}, indent=1))
    print(json.dumps({"files": len(paths), "totals": totals, "unbalanced_transactions": unbalanced}))
