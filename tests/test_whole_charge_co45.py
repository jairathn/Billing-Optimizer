"""A whole charge taken as a contractual reduction (CO-45) with nothing allowed is listed as a denial in all but name
(detect.py _settle_co45_candidates), except where it is not one:
- the payer paid the claim at the claim level (CLP04 more than its lines pay): a secondary that pays the coinsurance on
  the claim and lists the line as CO-45 for the whole charge;
- the practice billed the service again under another code, and the payer paid that code (a code corrected after a
  coding remark, or another code of the same kind);
- (no longer: fix wave F2, review privacy-4) the payer has no rate for the code, as it never allowed or paid it. That
  line is listed after all, marked as a code the payer never paid (evidence payer_has_no_rate): the owner's decision 4
  pursues codes a payer rarely pays, and rule 2 gives every write-off an owner.
Each was listed on Work now (the auditor's already-done items). Everything here is invented: names, member numbers, claim
ids, payer claim numbers, traces and amounts. Each test runs on both kinds of store."""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.review3.r3helpers import clm, sv  # noqa: E402
from tests.test_features import clean_visits  # noqa: E402
from tests.test_takebacks_and_recoveries import World, kind  # noqa: E402,F401

KY = {"payer": "KY MEDICARE", "payer_id": "MCRKY"}
BRIGHT = {"payer": "BRIGHTPATH HEALTH", "payer_id": "BRGHT"}
PINE = {"payer": "PINE RIDGE HEALTH PLAN", "payer_id": "PINER"}


def co45_items(w: World) -> list[dict]:
    return [r for r in w.rows("signal='DENIED_OTHER'") if json.loads(r["evidence"] or "{}").get("whole_charge_co45")]


def test_a_claim_paid_at_the_claim_level_is_no_denial_of_its_co45_line(tmp_path, kind):
    """Medicare pays C017's visit as primary ($47.04, the coinsurance $12.00 left) and forwards it. The second payer answers
    its claim as primary: it pays $12.00 on the claim (CLP04) and lists the visit as CO-45 for the whole charge, the flap
    repair beside it not covered (CO-96). The visit's coinsurance was paid at the claim level: no denial of the visit."""
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99213", "60.00", "60.00"), "20260301", **BRIGHT)
    flap = "570.00"
    w.remit([clm("CB0000500C017", "19", "630.00", "275.04",
                 sv("99213", "60.00", "47.04", 1, "CAS*CO*253*0.96~CAS*PR*2*12.00~", allowed="60.00")
                 + sv("14040", flap, "228.00", 1, "CAS*CO*253*4.60~CAS*CO*59*285.00~CAS*PR*2*52.40~", allowed="285.00"), payer_ctl="MC1")],
            "20260501", **KY)
    w.remit([clm("CB0000500C017", "1", "630.00", "12.00", sv("99213", "60.00", "0.00", 1, "CAS*CO*45*60.00~")
                 + sv("14040", flap, "0.00", 1, f"CAS*CO*96*{flap}~"), payer_ctl="BP1")], "20260515", **BRIGHT)
    det = w.run("r1")
    assert co45_items(w) == [], [(r["procedure_code"], r["payer_group"], r["work_state"]) for r in co45_items(w)]
    assert det.ema_counts["co45_claim_level_paid"] == 1
    w.store.close()


def test_a_code_billed_again_under_another_code_and_paid_is_no_denial(tmp_path, kind):
    """The payer takes C017's destruction (17000) as CO-45 for the whole charge, nothing allowed (2026-04-15); the
    practice bills the service again on C024 as 17110 and the payer pays it (2026-05-20). The service was paid under the
    code billed again: no denial."""
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "17000", "106.00", "64.00") + clean_visits(101, 25, "17110", "178.00", "88.00"), "20260301")
    w.remit([clm("CB0000500C017", "1", "106.00", "0.00", sv("17000", "106.00", "0.00", 1, "CAS*CO*45*106.00~"), payer_ctl="PC1")], "20260415")
    w.remit([clm("CB0000500C024", "1", "178.00", "88.00", sv("17110", "178.00", "88.00", 1, "CAS*CO*45*90.00~", allowed="88.00"),
                 payer_ctl="PC2")], "20260520")
    det = w.run("r1")
    assert co45_items(w) == [], [(r["procedure_code"], r["status"]) for r in co45_items(w)]
    assert det.ema_counts["co45_rebilled_paid"] == 1
    w.store.close()


def test_a_code_corrected_after_the_payers_coding_remark_and_paid_is_no_denial(tmp_path, kind):
    """The payer takes a telehealth visit billed as 98006 as CO-45 for the whole charge with remark M51 (the code is not
    valid); the practice sends a replacement claim as 99214 and the payer pays it: no denial of 98006."""
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "98006", "177.00", "90.00") + clean_visits(101, 25, "99214", "224.00", "110.00"), "20260301", **BRIGHT)
    w.remit([clm("CB0000500C017", "1", "177.00", "0.00", sv("98006", "177.00", "0.00", 1, "CAS*CO*45*177.00~LQ*HE*M51~"), payer_ctl="BP1")],
            "20260415", **BRIGHT)
    w.remit([clm("CB0000500C024", "1", "224.00", "85.00", sv("99214", "224.00", "85.00", 1, "CAS*CO*45*114.00~CAS*PR*3*25.00~",
                                                             allowed="110.00", mods="95"), payer_ctl="BP2")], "20260820", **BRIGHT)
    w.run("r1")
    assert co45_items(w) == [], [(r["procedure_code"], r["status"]) for r in co45_items(w)]
    w.store.close()


def test_a_payer_with_no_rate_for_the_code_makes_no_denial_but_one_that_pays_it_does(tmp_path, kind):
    """The plan never allowed or paid 11401: its CO-45 for the whole charge is how it answers a code it has no rate for.
    It is listed all the same, marked as a code the payer never paid, for a person or the owner's policy to decide (fix
    wave F2, review privacy-4: it was dropped with no item and no dollar count). The same plan paid 99213 before: its CO-45
    for a whole 99213 charge is a denial in all but name."""
    w = World(tmp_path, kind)
    w.remit([clm("CB0000500C001", "1", "146.00", "60.00", sv("99213", "146.00", "60.00", 1, "CAS*CO*45*86.00~", dos="20260210", allowed="60.00"),
                 payer_ctl="PR0", dos="20260210")], "20260301", **PINE)
    w.remit([clm("CB0000500C017", "1", "250.00", "0.00", sv("11401", "250.00", "0.00", 1, "CAS*CO*45*250.00~"), payer_ctl="PR1"),
             clm("CB0000600C017", "1", "146.00", "0.00", sv("99213", "146.00", "0.00", 1, "CAS*CO*45*146.00~"), payer_ctl="PR2")],
            "20260415", **PINE)
    det = w.run("r1")
    got = {r["procedure_code"]: bool(json.loads(r["evidence"]).get("payer_has_no_rate")) for r in co45_items(w)}
    assert got == {"11401": True, "99213": False}, [(r["procedure_code"], r["patient_control_number"]) for r in co45_items(w)]
    assert det.ema_counts["co45_payer_has_no_rate"] == 1
    w.store.close()
