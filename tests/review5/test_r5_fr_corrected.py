"""Whole claims denied (CLP02 = 4) and their corrected claims: live (3f8855b) against the release (752c700), each on its
own copy of the same store. Invented data only."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from frh import LIVE, REL, add_to, base, brief, build_store, clean_visits, clm, copy_db, detect, owed, sv  # noqa: E402

DENIED_ON, PAID_ON = "20260415", "20260520"


def two(tmp_path):
    db = str(tmp_path / "s.sqlite")
    return copy_db(db, str(tmp_path / "live.sqlite")), copy_db(db, str(tmp_path / "rel.sqlite"))


def test_corrected_claim_paid_at_a_lower_level_shown_only_by_the_charge(tmp_path):
    """C017 (99214, $223.65, the practice's 99214 fee) is denied whole for a missing referring provider (CO-16). The
    corrected C024 is paid as 99213 ($76.15) with the 99214 charge and no SVC06: the payer downcoded without saying so,
    which DOWNCODE_CHARGE catches. The money at stake is the downcode ($41.45). Live lists the downcode; does the release
    also list the whole 99214 as never paid?"""
    base(tmp_path)
    db = str(tmp_path / "s.sqlite")
    add_to(tmp_path, db, [clm("CB0000970C017", "4", "223.65", "0.00", sv("99214", "223.65", "0.00", cas="CAS*CO*16*223.65~"),
                              payer_ctl="A970A")], DENIED_ON)
    add_to(tmp_path, db, [clm("CB0000970C024", "1", "223.65", "76.15", sv("99213", "223.65", "76.15", cas="CAS*CO*45*147.50~", allowed="76.15"),
                              payer_ctl="A970B")], PAID_ON)
    live_db, rel_db = two(tmp_path)
    live = detect(LIVE, live_db, "live-1", "2026-05-25T09:00:00+00:00")
    rel = detect(REL, rel_db, "rel-1", "2026-05-25T09:00:00+00:00")
    brief("live", live, "CB0000970")
    brief("release", rel, "CB0000970")
    print("live owed:", owed(live, "CB0000970"), " release owed:", owed(rel, "CB0000970"))
    assert owed(rel, "CB0000970") == owed(live, "CB0000970"), "the release lists the silently downcoded visit twice: as the downcode and as a 99214 never paid"


def test_corrected_claim_paid_at_a_lower_level_shown_by_the_claim_file(tmp_path):
    """The same, with the 99213 charged at the 99213 fee: the 837 claim file shows C024 billed 99214 (DOWNCODE_VS_BILLED)."""
    from remitwatch.x12_837 import DDL as DDL_837
    from remitwatch.store import Store
    base(tmp_path)
    db = str(tmp_path / "s.sqlite")
    add_to(tmp_path, db, [clm("CB0000971C017", "4", "223.65", "0.00", sv("99214", "223.65", "0.00", cas="CAS*CO*16*223.65~"),
                              payer_ctl="A971A")], DENIED_ON)
    add_to(tmp_path, db, [clm("CB0000971C024", "1", "156.66", "76.15", sv("99213", "156.66", "76.15", cas="CAS*CO*45*80.51~", allowed="76.15"),
                              payer_ctl="A971B")], PAID_ON)
    s = Store(db)
    s.conn.executescript(DDL_837)
    s.conn.execute("INSERT INTO lines_837 (line_837_key, claim_837_key, patient_control_number, line_seq, procedure_code, charge_cents, units, service_date) "
                   "VALUES ('k971','c971','CB0000971C024',1,'99214',22365,1,'2026-04-01')")
    s.conn.commit()
    s.close()
    live_db, rel_db = two(tmp_path)
    live = detect(LIVE, live_db, "live-1", "2026-05-25T09:00:00+00:00")
    rel = detect(REL, rel_db, "rel-1", "2026-05-25T09:00:00+00:00")
    brief("live", live, "CB0000971")
    brief("release", rel, "CB0000971")
    print("live owed:", owed(live, "CB0000971"), " release owed:", owed(rel, "CB0000971"))
    assert owed(rel, "CB0000971") == owed(live, "CB0000971"), "the release lists the downcoded visit twice: as the downcode and as a 99214 never paid"


def medicare_base(tmp_path):
    claims = [c.replace("*12*PC", "*MB*PC") for c in clean_visits(1, 25, "99213", "156.66", "76.15") + clean_visits(101, 25, "17000", "150.00", "60.00")]
    build_store(tmp_path, claims, pay_date="20260105", payer="KY MEDICARE").close()


def test_supplement_denies_for_missing_information_a_visit_medicare_put_to_the_deductible(tmp_path):
    """January: Medicare (primary, status 19, crossed over) puts a visit-only claim (99213) wholly to the Part B deductible
    (PR-1 $76.15, paid $0). The supplement denies the crossover as a whole for missing information (CO-16, status 4). A
    supplement that covers the Part B deductible (Plan F or C) owes the $76.15 once the claim is corrected: the denial is
    work for the biller. Live lists it; the release?"""
    medicare_base(tmp_path)
    db = str(tmp_path / "s.sqlite")
    add_to(tmp_path, db, [clm("CB0000972C017", "19", "156.66", "0.00",
                              sv("99213", "156.66", "0.00", cas="CAS*PR*1*76.15~CAS*CO*45*80.51~", allowed="76.15"),
                              payer_ctl="MCR972", pr="76.15", cfi="MB")], "20260120", payer="KY MEDICARE", payer_id="MCRKY")
    add_to(tmp_path, db, [clm("CB0000972C017", "4", "156.66", "0.00", sv("99213", "156.66", "0.00", cas="CAS*CO*16*156.66~"),
                              payer_ctl="SUP972", cfi="CI")], "20260204", payer="AARP MEDICARE SUPPLEMENT", payer_id="AARPS")
    live_db, rel_db = two(tmp_path)
    live = detect(LIVE, live_db, "live-1", "2026-02-10T09:00:00+00:00")
    rel = detect(REL, rel_db, "rel-1", "2026-02-10T09:00:00+00:00")
    brief("live", live, "CB0000972")
    brief("release", rel, "CB0000972")
    print("live owed:", owed(live, "CB0000972"), " release owed:", owed(rel, "CB0000972"))
    assert owed(rel, "CB0000972") == owed(live, "CB0000972"), "the supplement's denial of the deductible it owes is not listed"
