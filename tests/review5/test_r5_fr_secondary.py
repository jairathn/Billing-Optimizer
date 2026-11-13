"""A secondary payer's whole-claim denial (CLP02 = 4) of a visit the primary applied wholly to the patient's deductible:
the secondary is the payer that pays the deductible (a Medigap Plan F or C, or a commercial secondary under coordination
of benefits). Live (3f8855b) lists the denial; the release (752c700)? First as a fresh analysis, then as the first run
after deploy over a store where the biller is working it. Invented data only."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from frh import LIVE, REL, act, add_to, base, brief, build_store, clean_visits, clm, copy_db, detect, owed, sv  # noqa: E402


def medicare_base(tmp_path):
    claims = [c.replace("*12*PC", "*MB*PC") for c in clean_visits(1, 25, "99213", "156.66", "76.15") + clean_visits(101, 25, "17000", "150.00", "60.00")]
    build_store(tmp_path, claims, pay_date="20260105", payer="KY MEDICARE").close()


def test_supplement_denial_of_a_deductible_visit_the_biller_is_working(tmp_path):
    """Medicare (status 19) applies a January visit (99213) wholly to the Part B deductible; the supplement denies the
    crossover whole: CO-31, patient not identified as its insured (a wrong member number). Live lists it; on 02-12 the
    biller sets it in progress (corrects the member number and resubmits). Deploy; the release's first run."""
    medicare_base(tmp_path)
    db = str(tmp_path / "s.sqlite")
    pfx = "CB0000990"
    add_to(tmp_path, db, [clm(f"{pfx}C017", "19", "156.66", "0.00", sv("99213", "156.66", "0.00", cas="CAS*PR*1*76.15~CAS*CO*45*80.51~", allowed="76.15"),
                              payer_ctl="MCR990", pr="76.15", cfi="MB")], "20260120", payer="KY MEDICARE", payer_id="MCRKY")
    add_to(tmp_path, db, [clm(f"{pfx}C017", "4", "156.66", "0.00", sv("99213", "156.66", "0.00", cas="CAS*CO*31*156.66~"), payer_ctl="SUP990", cfi="CI")],
           "20260204", payer="AARP MEDICARE SUPPLEMENT", payer_id="AARPS")
    live1 = detect(LIVE, db, "live-1", "2026-02-10T09:00:00+00:00")
    brief("live", live1, pfx)
    act(LIVE, db, f"{pfx}C017", "CLAIM_DENIED_UNPAID", "in_progress", at="2026-02-12T15:00:00+00:00")
    live_db, rel_db = copy_db(db, str(tmp_path / "live.sqlite")), copy_db(db, str(tmp_path / "rel.sqlite"))
    live = detect(LIVE, live_db, "live-2", "2026-02-17T09:00:00+00:00")
    rel = detect(REL, rel_db, "rel-1", "2026-02-17T09:00:00+00:00")
    brief("live, next run", live, pfx)
    brief("release, first run after deploy", rel, pfx)
    print("live owed:", owed(live, pfx), " release owed:", owed(rel, pfx))
    assert owed(rel, pfx) == owed(live, pfx), "the supplement's denial the biller is working is gone from the list (stale)"


def test_commercial_secondary_denies_a_visit_the_primary_put_to_the_deductible(tmp_path):
    """Commercial coordination of benefits: the primary (Anthem, status 1) applies the visit to the deductible (PR-1, paid
    $0); the secondary (Aetna) denies the claim whole for missing information (CO-16). Under COB the secondary pays the
    primary's deductible up to its own allowed amount once the claim is corrected."""
    base(tmp_path)
    db = str(tmp_path / "s.sqlite")
    pfx = "CB0000991"
    who = "OAK*LE****MI*W55500991"
    add_to(tmp_path, db, [clm(f"{pfx}C017", "1", "156.66", "0.00", sv("99213", "156.66", "0.00", cas="CAS*PR*1*76.15~CAS*CO*45*80.51~", allowed="76.15"),
                              payer_ctl="ANT991", pr="76.15", who=who)], "20260415")
    add_to(tmp_path, db, [clm(f"{pfx}C017", "4", "156.66", "0.00", sv("99213", "156.66", "0.00", cas="CAS*CO*16*156.66~"), payer_ctl="AET991", who=who)],
           "20260429", payer="AETNA", payer_id="AETNA")
    live_db, rel_db = copy_db(db, str(tmp_path / "live.sqlite")), copy_db(db, str(tmp_path / "rel.sqlite"))
    live = detect(LIVE, live_db, "live-1", "2026-05-01T09:00:00+00:00")
    rel = detect(REL, rel_db, "rel-1", "2026-05-01T09:00:00+00:00")
    brief("live", live, pfx)
    brief("release", rel, pfx)
    print("live owed:", owed(live, pfx), " release owed:", owed(rel, pfx))
    assert owed(rel, pfx) == owed(live, pfx), "the secondary's denial of the deductible it would pay is not listed"


def test_control_supplement_denial_after_medicare_paid_part_of_the_claim(tmp_path):
    """Control: Medicare paid the 17000 ($48) and put the visit to the deductible; both releases leave the supplement's
    whole-claim denial off the list (live because the claim id was paid)."""
    medicare_base(tmp_path)
    db = str(tmp_path / "s.sqlite")
    pfx = "CB0000992"
    add_to(tmp_path, db, [clm(f"{pfx}C017", "19", "306.66", "48.00", sv("99213", "156.66", "0.00", cas="CAS*PR*1*76.15~CAS*CO*45*80.51~", allowed="76.15")
                              + sv("17000", "150.00", "48.00", cas="CAS*PR*2*12.00~CAS*CO*45*90.00~", allowed="60.00"),
                              payer_ctl="MCR992", pr="88.15", cfi="MB")], "20260120", payer="KY MEDICARE", payer_id="MCRKY")
    add_to(tmp_path, db, [clm(f"{pfx}C017", "4", "306.66", "0.00", sv("99213", "156.66", "0.00", cas="CAS*CO*31*156.66~")
                              + sv("17000", "150.00", "0.00", cas="CAS*CO*31*150.00~"), payer_ctl="SUP992", cfi="CI")],
           "20260204", payer="AARP MEDICARE SUPPLEMENT", payer_id="AARPS")
    live_db, rel_db = copy_db(db, str(tmp_path / "live.sqlite")), copy_db(db, str(tmp_path / "rel.sqlite"))
    live = detect(LIVE, live_db, "live-1", "2026-02-10T09:00:00+00:00")
    rel = detect(REL, rel_db, "rel-1", "2026-02-10T09:00:00+00:00")
    brief("live", live, pfx)
    brief("release", rel, pfx)
    assert owed(rel, pfx) == owed(live, pfx) == (0, 0)


def test_supplement_claim_level_denial_after_medicare_paid(tmp_path):
    """Medicare (status 19) pays the visit ($60.92, PR-2 $15.23 coinsurance); the supplement sends its denial at claim level
    only (status 4, CO-27 coverage ended, no service lines). Live leaves it off (the claim id was paid); the release?"""
    medicare_base(tmp_path)
    db = str(tmp_path / "s.sqlite")
    pfx = "CB0000993"
    add_to(tmp_path, db, [clm(f"{pfx}C017", "19", "156.66", "60.92", sv("99213", "156.66", "60.92", cas="CAS*PR*2*15.23~CAS*CO*45*80.51~", allowed="76.15"),
                              payer_ctl="MCR993", pr="15.23", cfi="MB")], "20260120", payer="KY MEDICARE", payer_id="MCRKY")
    add_to(tmp_path, db, [clm(f"{pfx}C017", "4", "156.66", "0.00", "", payer_ctl="SUP993", cfi="CI", claim_cas="CAS*CO*27*156.66~")],
           "20260204", payer="AARP MEDICARE SUPPLEMENT", payer_id="AARPS")
    live_db, rel_db = copy_db(db, str(tmp_path / "live.sqlite")), copy_db(db, str(tmp_path / "rel.sqlite"))
    live = detect(LIVE, live_db, "live-1", "2026-02-10T09:00:00+00:00")
    rel = detect(REL, rel_db, "rel-1", "2026-02-10T09:00:00+00:00")
    brief("live", live, pfx)
    brief("release", rel, pfx)
    listed = lambda rows: [(r["signal"], r["status"], r["shortfall_cents"]) for r in rows if r["patient_control_number"].startswith(pfx) and r["status"] == "open"]  # noqa: E731
    print("live open:", listed(live), " release open:", listed(rel))
    assert listed(rel) == listed(live), "the release lists a supplement's claim-level denial of a crossover Medicare paid"
