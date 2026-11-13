"""Helpers for the fifth review (area: the first analysis after deploy, and corrected claims), live (3f8855b) against the
release (752c700, this tree). Built on tests/review4/lvn.py: each release runs in its own subprocess with its own tree
first on sys.path. Invented data only: names, member numbers, claim ids, payer ids and amounts are made up.
Run from the repository root."""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
for p in (REPO, os.path.join(REPO, "tests", "review4")):
    if p not in sys.path:
        sys.path.insert(0, p)

import lvn  # noqa: E402
from lvn import LIVE, PROV, REL, WHO, base, build_store, clean_visits, clm, copy_db, detect, money, run_tree, show, sv  # noqa: E402,F401
from remitwatch.store import Store  # noqa: E402
from tests.test_remitwatch import remit  # noqa: E402

if not LIVE:       # a shallow checkout without the live release in its history (CI): these comparisons are skipped
    import pytest
    pytest.skip("needs the live release (3f8855b) in git history", allow_module_level=True)


def add_to(tmp_path, dbs, claims, pay_date, payer="ANTHEM", payer_id="ANTHM", plb=""):
    """One more remittance file, ingested into each store in `dbs` (the ingest code is the same in both releases)."""
    n = len(list(tmp_path.glob("*.RMT")))
    p = tmp_path / f"r{n:04d}.RMT"
    p.write_bytes(remit("".join(claims), "100.00", payer=payer, payer_id=payer_id, seg_count=99, isa_ctrl=f"{n + 1:09d}",
                        pay_date=pay_date, plb=plb))
    for db in ([dbs] if isinstance(dbs, str) else dbs):
        s = Store(db)
        s.ingest_835_paths([str(p)])
        s.close()


def act(tree, db, pcn, signal, status, notes=None, recovered=None, at=None, line_seq=None, seen=None, actor="biller"):
    """A status set the way the page does it, with that tree's apply_status; `seen` (release only) is the status the person
    was looking at, as the serverless journal records it."""
    extra = f" AND line_key LIKE '%:{line_seq}'" if line_seq else ""
    seen_arg = f", seen={seen!r}" if seen is not None else ""
    body = f"""
        r = s.conn.execute("SELECT finding_id FROM findings WHERE patient_control_number=? AND signal=? AND COALESCE(stale,0)=0{extra}",
                           ({pcn!r}, {signal!r})).fetchall()
        assert len(r) == 1, ({pcn!r}, {signal!r}, r)
        assert apply_status(s.conn, r[0][0], {status!r}, {notes!r}, {recovered!r}, actor={actor!r}, at={at!r}{seen_arg})
        result = r[0][0]
    """
    return run_tree(tree, db, body)


def events(tree, db, fid):
    return run_tree(tree, db, f"""
        result = [list(r) for r in s.conn.execute("SELECT kind, from_status, to_status, at, amount_cents, note FROM finding_events WHERE finding_id=? ORDER BY id", ({fid!r},))]
    """)


def bill(rows, prefix):
    return [r for r in rows if (r.get("patient_control_number") or "").startswith(prefix)]


def brief(label, rows, prefix=None):
    sel = bill(rows, prefix) if prefix else rows
    print(f"--- {label}")
    for r in sorted(sel, key=lambda r: (r.get("patient_control_number") or "", r.get("signal") or "", r.get("line_key") or "")):
        if r.get("category") in ("info", "contract", "fees"):
            continue
        print({k: r.get(k) for k in ("patient_control_number", "procedure_code", "signal", "status", "stale", "shortfall_cents",
                                     "recovered_cents", "notes", "auto_note", "hint")})


def owed(rows, prefix=None):
    """(findings, cents) still owed in money categories, optionally for one bill."""
    return money(rows, (lambda r: (r.get("patient_control_number") or "").startswith(prefix)) if prefix else (lambda r: True))
