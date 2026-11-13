"""Two more patterns that repeat in a dermatology archive, live (3f8855b) against release (36030eb). Invented data only."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lvn import add, both, build_store, clean_visits, clm, money, show, sv  # noqa: E402


def medicare_base(tmp_path):
    claims = [c.replace("*12*PC", "*MB*PC") for c in clean_visits(1, 25, "99213", "156.66", "76.15") + clean_visits(101, 25, "17000", "150.00", "60.00")]
    build_store(tmp_path, claims, pay_date="20260105", payer="KY MEDICARE").close()


def test_supplement_denies_the_crossover_of_a_visit_medicare_put_to_the_deductible(tmp_path):
    """January: Medicare (primary, status 19, crossed over) puts the 99213 to the Part B deductible (PR-1, paid $0) and
    pays 17000 ($48, PR-2 $12). The supplemental plan's coverage had ended: it denies the crossover as a whole (status 4,
    CO-27). Medicare decided both lines; the patient owes the deductible and coinsurance. Nothing is owed by an insurer."""
    medicare_base(tmp_path)
    add(tmp_path, [clm("CB0000930C017", "19", "306.66", "48.00",
                       sv("99213", "156.66", "0.00", cas="CAS*PR*1*76.15~CAS*CO*45*80.51~", allowed="76.15")
                       + sv("17000", "150.00", "48.00", cas="CAS*PR*2*12.00~CAS*CO*45*90.00~", allowed="60.00"),
                       payer_ctl="MCR930", pr="88.15", cfi="MB")], "20260120", payer="KY MEDICARE", payer_id="MCRKY")
    add(tmp_path, [clm("CB0000930C017", "4", "306.66", "0.00",
                       sv("99213", "156.66", "0.00", cas="CAS*CO*27*156.66~") + sv("17000", "150.00", "0.00", cas="CAS*CO*27*150.00~"),
                       payer_ctl="SUP930", cfi="CI")], "20260204", payer="AARP MEDICARE SUPPLEMENT", payer_id="AARPS")
    live, rel = both(tmp_path)
    show("live", live)
    show("release", rel)
    print("live owed:", money(live), " release owed:", money(rel))
    assert money(rel) == money(live), "the release lists the supplement's denial of a deductible visit as money never paid"


def test_corrected_claim_with_the_units_fixed_stays_open(tmp_path):
    """17003 billed for 6 lesions and denied for frequency (CO-151: the documentation supports 5). The practice corrects
    the claim to 5 units and the payer pays all 5. The live release closes the denial; the release keeps it open as
    'Partly paid 5 of 6 units' with the line's whole expected amount still listed."""
    claims = [f"CLP*CB{i:07d}C017*1*120.00*40.00*0.00*12*PC{i}*11~NM1*QC*1*SAMPLE*PAT****MI*M{i:08d}~NM1*82*2*TEST PROVIDER*****XX*1888888882~"
              f"DTM*232*20260401~SVC*HC:17003*120.00*40.00**1~DTM*472*20260401~CAS*CO*45*80.00~AMT*B6*40.00~" for i in range(1, 26)]
    build_store(tmp_path, claims, pay_date="20260301").close()
    add(tmp_path, [clm("CB0000940C017", "1", "720.00", "0.00", sv("17003", "720.00", "0.00", units=0, orig_units="6", cas="CAS*CO*151*720.00~"),
                       payer_ctl="ANT940A")], "20260415")
    add(tmp_path, [clm("CB0000940C024", "1", "600.00", "200.00", sv("17003", "600.00", "200.00", units=5, cas="CAS*CO*45*400.00~", allowed="200.00"),
                       payer_ctl="ANT940B")], "20260520")
    live, rel = both(tmp_path)
    show("live", live)
    show("release", rel)
    print("live owed:", money(live), " release owed:", money(rel))
    # Deliberate: a corrected claim billing fewer units is indistinguishable here from one that paid one of two specimens
    # (which must not close the other); the denial stays open with a note that says what was paid.
    [r] = [r for r in rel if r["patient_control_number"] == "CB0000940C017" and r["status"] == "open"]
    assert (r["hint"] or "").startswith("Partly paid on claim CB0000940C024 (2026-05-20): 5 of 6 units")
