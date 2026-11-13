"""A whole claim denied (CLP02 = 4), then the corrected claim (same EMA bill, new submission suffix) processed by the same
payer. Live (3f8855b) treats the bill as settled once any later payment on it stands; the release lists every code and
date of the denied claim that no standing payment covers. Each case below is a common way the corrected claim is
processed in full without a payment under the denied claim's exact code and date. Invented data only."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lvn import add, base, both, clm, money, show, sv  # noqa: E402

DENIED_ON, PAID_ON = "20260415", "20260520"


def denial(pcn, lines, carc="16", charge="0.00"):
    """The whole claim denied: every line paid $0 with the denial reason, claim status 4."""
    return clm(pcn, "4", charge, "0.00", lines, payer_ctl="ANT900A")


def report(live, rel):
    show("live", live)
    show("release", rel)
    print("live owed:", money(live), " release owed:", money(rel))


def test_corrected_claim_line_applied_to_the_deductible(tmp_path):
    """C017 (99213 + 17000) denied CO-16 for a missing referring provider. The corrected C024 is processed: 17000 paid
    $60; the visit's $76.15 allowed goes to the patient's deductible (PR-1, paid $0). Nothing is owed by the payer."""
    base(tmp_path)
    add(tmp_path, [denial("CB0000900C017", sv("99213", "156.66", "0.00", cas="CAS*CO*16*156.66~")
                          + sv("17000", "150.00", "0.00", cas="CAS*CO*16*150.00~"))], DENIED_ON)
    add(tmp_path, [clm("CB0000900C024", "1", "306.66", "60.00",
                       sv("99213", "156.66", "0.00", cas="CAS*PR*1*76.15~CAS*CO*45*80.51~", allowed="76.15")
                       + sv("17000", "150.00", "60.00", cas="CAS*CO*45*90.00~", allowed="60.00"), payer_ctl="ANT900B", pr="76.15")], PAID_ON)
    live, rel = both(tmp_path)
    report(live, rel)
    assert money(rel) == money(live), "the release lists the deductible line of a corrected, fully processed claim as unpaid"


def test_corrected_claim_paid_at_a_lower_visit_level(tmp_path):
    """C017 (99214) denied CO-16. The corrected C024 is paid, downcoded to 99213 (SVC06 = 99214). The money at stake is the
    downcode (99214 rate less 99213 paid), which both releases list; the release also lists the whole 99214 as never paid."""
    base(tmp_path)
    add(tmp_path, [denial("CB0000901C017", sv("99214", "223.65", "0.00", cas="CAS*CO*16*223.65~"))], DENIED_ON)
    add(tmp_path, [clm("CB0000901C024", "1", "223.65", "76.15",
                       sv("99213", "223.65", "76.15", cas="CAS*CO*45*147.50~", allowed="76.15", orig="99214"), payer_ctl="ANT901B")], PAID_ON)
    live, rel = both(tmp_path)
    report(live, rel)
    assert money(rel) == money(live), "the release lists a downcoded visit twice: as the downcode and as a denial never paid"


def test_corrected_claim_with_the_date_of_service_fixed(tmp_path):
    """C017 billed the visit on the wrong date (04-02) and was denied; the corrected C024 has the right date (04-01) and is
    paid in full."""
    base(tmp_path)
    add(tmp_path, [clm("CB0000902C017", "4", "223.65", "0.00", sv("99214", "223.65", "0.00", cas="CAS*CO*16*223.65~", dos="20260402"),
                       payer_ctl="ANT902A", dos="20260402")], DENIED_ON)
    add(tmp_path, [clm("CB0000902C024", "1", "223.65", "117.60", sv("99214", "223.65", "117.60", cas="CAS*CO*45*106.05~", allowed="117.60"),
                       payer_ctl="ANT902B")], PAID_ON)
    live, rel = both(tmp_path)
    report(live, rel)
    assert money(rel) == money(live), "the release lists a visit as never paid after its corrected claim was paid in full"


def test_corrected_claim_with_the_visit_bundled_is_listed_twice(tmp_path):
    """C017 (99213-25 + 17000) denied CO-16. The corrected C024: 17000 paid, 99213-25 bundled (CO-97). Live lists the
    bundling once; the release lists the same visit again as an unpaid whole-claim denial."""
    base(tmp_path)
    add(tmp_path, [denial("CB0000903C017", sv("99213", "156.66", "0.00", cas="CAS*CO*16*156.66~", mods="25")
                          + sv("17000", "150.00", "0.00", cas="CAS*CO*16*150.00~"))], DENIED_ON)
    add(tmp_path, [clm("CB0000903C024", "1", "306.66", "60.00",
                       sv("99213", "156.66", "0.00", cas="CAS*CO*97*156.66~", mods="25")
                       + sv("17000", "150.00", "60.00", cas="CAS*CO*45*90.00~", allowed="60.00"), payer_ctl="ANT903B")], PAID_ON)
    live, rel = both(tmp_path)
    report(live, rel)
    visit = lambda r: r.get("patient_control_number", "").startswith("CB0000903")       # noqa: E731
    assert money(rel, visit) == money(live, visit), "one bundled visit is listed as two findings"


def test_corrected_claim_visit_covered_by_the_copay(tmp_path):
    """The Anthem PPO pattern: 99213 allowed about $52, a $55 specialist copay. C017 (99213-25 + 17000) denied CO-16; the
    corrected C024 is processed: the visit's whole allowed amount is the patient's copay (PR-3, paid $0), 17000 paid."""
    base(tmp_path)
    add(tmp_path, [denial("CB0000904C017", sv("99213", "156.66", "0.00", cas="CAS*CO*16*156.66~", mods="25")
                          + sv("17000", "150.00", "0.00", cas="CAS*CO*16*150.00~"))], DENIED_ON)
    add(tmp_path, [clm("CB0000904C024", "1", "306.66", "60.00",
                       sv("99213", "156.66", "0.00", cas="CAS*PR*3*52.00~CAS*CO*45*104.66~", allowed="52.00", mods="25")
                       + sv("17000", "150.00", "60.00", cas="CAS*CO*45*90.00~", allowed="60.00"), payer_ctl="ANT904B", pr="52.00")], PAID_ON)
    live, rel = both(tmp_path)
    report(live, rel)
    assert money(rel) == money(live), "the release lists a visit the patient's copay paid as a denial never paid"


def test_corrected_claim_that_drops_a_line(tmp_path):
    """C017 (99213 + 17000) denied CO-16. The practice's corrected claim C024 bills 17000 alone (the visit was not
    separately billable) and it is paid. Nothing is owed for the dropped visit."""
    base(tmp_path)
    add(tmp_path, [denial("CB0000905C017", sv("99213", "156.66", "0.00", cas="CAS*CO*16*156.66~")
                          + sv("17000", "150.00", "0.00", cas="CAS*CO*16*150.00~"))], DENIED_ON)
    add(tmp_path, [clm("CB0000905C024", "1", "150.00", "60.00", sv("17000", "150.00", "60.00", cas="CAS*CO*45*90.00~", allowed="60.00"),
                       payer_ctl="ANT905B")], PAID_ON)
    live, rel = both(tmp_path)
    report(live, rel)
    # Deliberate: the visit may have been dropped on purpose or forgotten (an earlier review asked for it to be listed), so
    # it stays listed, with a note naming the corrected claim, instead of disappearing as it did on the live release.
    listed = [r for r in rel if r["signal"] == "CLAIM_DENIED_UNPAID" and r["status"] == "open"]
    assert len(listed) == 1 and (listed[0]["hint"] or "").startswith("Billed again on claim CB0000905C024 without 99213")
