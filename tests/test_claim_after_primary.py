"""A whole claim a second payer denied after the primary paid it (archive rule 2 for a claim).

Archive rule 2 (other_payer_paid_first) reads a line a payer denied after another payer paid it as primary: the item
names the primary's payment and is sized at the patient's share the primary left that no one has paid since. A whole
claim denied the same way (claim status 4: a Medicaid plan behind a Medicare Advantage plan, a provider-enrollment or
NPI denial of the claim) was listed as a denial of the whole service, or with no amount at all when the payer sent the
claim back without its lines, and never named the primary's payment. It is read as rule 2 reads a line now.

Everything here is invented: names, member numbers, claim ids, payer claim numbers, traces and amounts. Each test runs on
both kinds of store."""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch.detect import Detector  # noqa: E402
from tests.review3.r3helpers import clm, sv  # noqa: E402
from tests.test_features import clean_visits  # noqa: E402
from tests.test_takebacks_and_recoveries import World, kind  # noqa: E402,F401

HUMANA = {"payer": "HUMANA CARE PLAN", "payer_id": "HUMNA"}
MEDICAID = {"payer": "IN MEDICAID", "payer_id": "INMCD"}
BILL = "CB0000700"
TODAY = "2026-06-01"            # the run's day (pinned: the windows below are open on it)


def run(w: World, run_id: str) -> Detector:
    det = Detector(w.store, run_id=run_id, today=TODAY)
    det.run()
    return det


def primary_answer() -> str:
    """The primary pays the destruction ($23.00, coinsurance $6.00 left) and puts the visit on the deductible ($105.00)."""
    return clm(f"{BILL}C017", "1", "172.00", "23.00",
               sv("17000", "62.00", "23.00", 1, "CAS*PR*2*6.00~CAS*CO*253*0.50~CAS*CO*45*32.50~", allowed="29.50", mods="59")
               + sv("99203", "110.00", "0.00", 1, "CAS*PR*1*105.00~CAS*CO*45*5.00~", allowed="105.00", mods="25"), payer_ctl="HU1")


def test_a_second_payers_claim_denial_names_the_primarys_payment_and_is_sized_at_the_share_left(tmp_path, kind):
    """Medicaid denies the whole claim (CO-B7: the clinician is not enrolled with it) ten days after the primary paid:
    the item names the primary's payment and is about the $111.00 the primary left to the patient, not the whole visit."""
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99203", "110.00", "105.00"), "20260301", **MEDICAID)
    w.remit([primary_answer()], "20260501", **HUMANA)
    w.remit([clm(f"{BILL}C018", "4", "172.00", "0.00", sv("99203", "110.00", "0.00", 0, "CAS*CO*B7*110.00~", mods="25")
                 + sv("17000", "62.00", "0.00", 0, "CAS*CO*B7*62.00~", mods="59"), payer_ctl="MD1")], "20260511", **MEDICAID)
    run(w, "r1")
    r = w.items()[f"{BILL}C018::CLAIM_DENIED_UNPAID"]
    assert r["status"] == "open" and r["work_state"] == "work_now", (r["work_state"], r["state_basis"])
    assert "Paid by HUMANA CARE PLAN as primary on 2026-05-01" in (r["hint"] or ""), r["hint"]
    assert r["shortfall_cents"] == 11100, (r["shortfall_cents"], r["hint"])
    ar = json.loads(r["evidence"])["archive_rule"]
    assert (ar["rule"], ar["reclass"], ar["strength"], ar["patient_share_left_cents"]) == ("other_payer_paid_first", "other_payer_paid", "proven", 11100)
    assert "HUMANA CARE PLAN" in (r["state_basis"] or ""), r["state_basis"]
    w.store.close()


def test_a_second_payers_claim_denial_without_lines_names_the_primarys_payment(tmp_path, kind):
    """Medicaid sends the claim back denied with no service lines (CO-208, the NPI not matched): before, a denial of 'no
    service lines' with no amount. The primary's payment is named, and the share it left is the item's size."""
    w = World(tmp_path, kind)
    w.remit([primary_answer()], "20260501", **HUMANA)
    w.remit([clm(f"{BILL}C018", "4", "172.00", "0.00", claim_cas="CAS*CO*208*172.00~", payer_ctl="MD1")], "20260511", **MEDICAID)
    run(w, "r1")
    r = w.items()[f"{BILL}C018::CLAIM_DENIED_UNPAID"]
    assert "Paid by HUMANA CARE PLAN as primary on 2026-05-01" in (r["hint"] or ""), r["hint"]
    assert r["shortfall_cents"] == 11100 and r["work_state"] == "work_now", (r["shortfall_cents"], r["work_state"], r["hint"])
    w.store.close()


def test_a_second_payers_claim_denial_after_its_share_was_paid_is_done(tmp_path, kind):
    """The share the primary left was paid since by another plan of the patient's (the Medigap, $111.00): nothing is left
    for the denying payer to owe; the claim item is done, the payments named."""
    w = World(tmp_path, kind)
    w.remit([primary_answer()], "20260501", **HUMANA)
    w.remit([clm(f"{BILL}C018", "4", "172.00", "0.00", claim_cas="CAS*CO*208*172.00~", payer_ctl="MD1")], "20260511", **MEDICAID)
    w.remit([clm(f"{BILL}C017", "2", "172.00", "111.00",
                 sv("17000", "62.00", "6.00", 1, "CAS*OA*23*56.00~", mods="59") + sv("99203", "110.00", "105.00", 1, "CAS*OA*23*5.00~", mods="25"),
                 payer_ctl="MG1")], "20260520", payer="MUTUAL OF OMAHA", payer_id="MOOMA")
    run(w, "r1")
    r = w.items()[f"{BILL}C018::CLAIM_DENIED_UNPAID"]
    assert r["work_state"] == "done" and "paid by HUMANA CARE PLAN as primary" in (r["state_basis"] or ""), (r["work_state"], r["state_basis"])
    w.store.close()


# --------------------------------------------------------------------------- an archive rule's Done names its reading
def test_an_archive_rules_done_shows_the_rules_sentence_as_its_basis(tmp_path, kind):
    """Medicare pays the visit as primary ($94.08) and the Medigap pays the coinsurance ($23.52); a third plan denies the
    line afterwards. Archive rule 2 reads it done (another payer's answer, nothing left to collect). Its Done showed only
    the rule's label ('Paid by another payer first') as its basis: it shows the rule's sentence, which names who paid."""
    w = World(tmp_path, kind)
    medicare = {"payer": "KY MEDICARE", "payer_id": "MCRKY"}
    w.remit([clm(f"{BILL}C017", "1", "223.65", "94.08", sv("99214", "223.65", "94.08", 1, "CAS*CO*45*106.05~CAS*PR*2*23.52~", allowed="117.60"),
                 payer_ctl="MC1")], "20260501", **medicare)
    w.remit([clm(f"{BILL}C017", "2", "223.65", "23.52", sv("99214", "223.65", "23.52", 1, "CAS*OA*23*94.08~CAS*CO*45*106.05~"), payer_ctl="MG1")],
            "20260508", payer="MUTUAL OF OMAHA", payer_id="MOOMA")
    w.remit([clm(f"{BILL}C017", "1", "223.65", "0.00", sv("99214", "223.65", "0.00", 1, "CAS*CO*16*223.65~"), payer_ctl="BP1")], "20260515",
            payer="BRIGHTPATH HEALTH", payer_id="BRGHT")
    run(w, "r1")
    r = next(x for x in w.rows("category='denial'") if x["payer_name"] == "BRIGHTPATH HEALTH")
    ar = json.loads(r["evidence"])["archive_rule"]
    assert (ar["reclass"], ar["state_hint"], ar["strength"]) == ("other_payer_paid", "done", "proven"), ar
    assert r["work_state"] == "done", (r["work_state"], r["state_basis"])
    assert r["state_basis"].startswith("Paid by another payer first: paid by KY MEDICARE as primary on 2026-05-01"), r["state_basis"]
    assert "MUTUAL OF OMAHA" in r["state_basis"], r["state_basis"]
    w.store.close()
