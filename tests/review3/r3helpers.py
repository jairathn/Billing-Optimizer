"""Builders for the third review (payment ledger and closures). Invented data only: names, member numbers, claim ids,
payer ids and amounts are made up. Run from the repository root."""
from __future__ import annotations

import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from remitwatch.store import Store  # noqa: E402
from tests.test_features import NPI, build_store, clean_visits  # noqa: E402,F401
from tests.test_remitwatch import remit  # noqa: E402

WHO = "ELM*BO****MI*W55500302"


def sv(code, charge, paid, units=1, cas="", dos="20260401", allowed=None, mods=""):
    """One service line with its units (SVC05)."""
    out = f"SVC*HC:{code}{':' + mods if mods else ''}*{charge}*{paid}**{units}~DTM*472*{dos}~{cas}"
    return out + (f"AMT*B6*{allowed}~" if allowed else "")


def clm(pcn, status, charge, paid, lines="", who=WHO, payer_ctl="PC1", dos="20260401", claim_cas=""):
    """One claim. lines="" gives a claim with no service lines (claim-level only)."""
    return (f"CLP*{pcn}*{status}*{charge}*{paid}*0.00*12*{payer_ctl}*11~NM1*QC*1*{who}~NM1*82*2*TEST PROVIDER*****XX*{NPI}~"
            f"DTM*232*{dos}~{claim_cas}{lines}")


def visit_paid(pcn, paid="117.60", status="1", dos="20260401", who=WHO, payer_ctl="PC1", code="99214", charge="223.65"):
    co = f"{float(charge) - float(paid):.2f}"
    return clm(pcn, status, charge, paid, sv(code, charge, paid, 1, f"CAS*CO*45*{co}~", dos, allowed=paid), who, payer_ctl, dos)


def visit_reversed(pcn, paid="117.60", dos="20260401", who=WHO, payer_ctl="PC1", code="99214", charge="223.65"):
    co = f"{float(charge) - float(paid):.2f}"
    return clm(pcn, "22", f"-{charge}", f"-{paid}", sv(code, f"-{charge}", f"-{paid}", -1, f"CAS*CO*45*-{co}~", dos), who, payer_ctl, dos)


def visit_denied_line(pcn, carc="16", dos="20260401", who=WHO, payer_ctl="PC1", code="99214", charge="223.65", status="1", grp="CO"):
    return clm(pcn, status, charge, "0.00", sv(code, charge, "0.00", 1, f"CAS*{grp}*{carc}*{charge}~", dos), who, payer_ctl, dos)


def add(tmp_path, claims, pay_date, payer="ANTHEM", payer_id="ANTHM", plb=""):
    """One more remittance file from this payer, ingested into the test store."""
    store = Store(str(tmp_path / "s.sqlite"))
    n = len(list(tmp_path.glob("*.RMT")))
    p = tmp_path / f"r{n:04d}.RMT"
    p.write_bytes(remit("".join(claims), "100.00", payer=payer, payer_id=payer_id, seg_count=99, isa_ctrl=f"{n + 1:09d}",
                        pay_date=pay_date, plb=plb))
    store.ingest_835_paths([str(p)])
    return store


def base(tmp_path, code="99214", charge="223.65", allowed="117.60", pay_date="20260301"):
    """25 clean visits so the payer has a usual rate and the code counts as paid once per patient and date."""
    return build_store(tmp_path, clean_visits(1, 25, code, charge, allowed), pay_date=pay_date)


def findings(store, where="1=1"):
    return [dict(r) for r in store.query(f"SELECT * FROM findings WHERE {where}")]


def by_key(store, where="category='denial'"):
    return {f"{r['patient_control_number']}:{r['procedure_code'] or ''}:{r['signal']}": r for r in findings(store, where)}


def show(rows, cols=("patient_control_number", "procedure_code", "signal", "status", "stale", "shortfall_cents", "recovered_cents", "auto_note", "hint")):
    for r in rows:
        print({c: r.get(c) for c in cols})


def ev(r):
    return json.loads(r["evidence"]) if r.get("evidence") else {}
