"""Smaller differences between live (3f8855b) and the release (752c700) around corrected claims. Invented data only."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from frh import LIVE, REL, act, add_to, base, brief, clm, copy_db, detect, owed, sv  # noqa: E402


def test_code_corrected_on_the_corrected_claim(tmp_path):
    """C017 bills a punch biopsy (11104) and is denied whole: diagnosis inconsistent with the procedure (CO-11). The
    practice corrects the code to the tangential biopsy actually done (11102) on C024, which is paid. Nothing is owed."""
    base(tmp_path)
    db = str(tmp_path / "s.sqlite")
    pfx = "CB0000994"
    add_to(tmp_path, db, [clm(f"{pfx}C017", "4", "210.00", "0.00", sv("11104", "210.00", "0.00", cas="CAS*CO*11*210.00~"), payer_ctl="A994A")], "20260415")
    add_to(tmp_path, db, [clm(f"{pfx}C024", "1", "180.00", "90.00", sv("11102", "180.00", "90.00", cas="CAS*CO*45*90.00~", allowed="90.00"), payer_ctl="A994B")],
           "20260520")
    live_db, rel_db = copy_db(db, str(tmp_path / "live.sqlite")), copy_db(db, str(tmp_path / "rel.sqlite"))
    live = detect(LIVE, live_db, "live-1", "2026-05-25T09:00:00+00:00")
    rel = detect(REL, rel_db, "rel-1", "2026-05-25T09:00:00+00:00")
    brief("live", live, pfx)
    brief("release", rel, pfx)
    print("live owed:", owed(live, pfx), " release owed:", owed(rel, pfx))
    assert owed(rel, pfx) == owed(live, pfx), "a code corrected on the corrected claim is listed as a line dropped from it"


def test_one_payment_counted_as_two_recoveries(tmp_path):
    """C017 (99214) denied whole (04-15), the biller sets it in progress (04-20). C024 is processed with the visit denied
    again at line level (05-06). C031 is paid $117.60 (06-01). One payment: how much does the release count recovered?"""
    base(tmp_path)
    db = str(tmp_path / "s.sqlite")
    pfx = "CB0000995"
    add_to(tmp_path, db, [clm(f"{pfx}C017", "4", "223.65", "0.00", sv("99214", "223.65", "0.00", cas="CAS*CO*16*223.65~"), payer_ctl="A995A")], "20260415")
    detect(REL, db, "rel-1", "2026-04-16T09:00:00+00:00")
    act(REL, db, f"{pfx}C017", "CLAIM_DENIED_UNPAID", "in_progress", at="2026-04-20T15:00:00+00:00")
    add_to(tmp_path, db, [clm(f"{pfx}C024", "1", "223.65", "0.00", sv("99214", "223.65", "0.00", cas="CAS*CO*16*223.65~"), payer_ctl="A995B")], "20260506")
    add_to(tmp_path, db, [clm(f"{pfx}C031", "1", "223.65", "117.60", sv("99214", "223.65", "117.60", cas="CAS*CO*45*106.05~", allowed="117.60"),
                              payer_ctl="A995C")], "20260601")
    rel = detect(REL, db, "rel-2", "2026-06-02T09:00:00+00:00")
    brief("release", rel, pfx)
    got = sum(r["recovered_cents"] or 0 for r in rel if r["patient_control_number"].startswith(pfx) and r["status"] == "resolved" and not r["stale"])
    print("recovered counted:", got)
    assert got <= 11760, f"one $117.60 payment counted as ${got/100:.2f} recovered"
