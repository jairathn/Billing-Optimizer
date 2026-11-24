"""What a closed denial recovered counts a second payer's share paid after the primary's payment.

paid_cover counts a secondary's payment only when it reaches half the expected amount: right for deciding that a service
is paid (a coinsurance alone does not pay a primary's denial), wrong for measuring what the service brought once the
primary paid it. A typical 20% coinsurance a secondary paid after the primary's payment was left out of the recovery and of
the closure's evidence. It counts now, on a line's closure and on a whole claim's; it still never closes a denial alone.

Everything here is invented: names, member numbers, claim ids, payer claim numbers, traces and amounts. Each test runs on
both kinds of store."""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.review3.r3helpers import clm, sv, visit_denied_line  # noqa: E402
from tests.test_features import clean_visits  # noqa: E402
from tests.test_takebacks_and_recoveries import World, kind  # noqa: E402,F401

MEDIGAP = {"payer": "MUTUAL OF OMAHA", "payer_id": "MOOMA"}
BILL = "CB0000300"


def primary_paid(pcn: str, ctl: str = "PC300B") -> str:
    """The primary pays the visit: $117.60 allowed, $94.08 paid, $23.52 coinsurance left."""
    return clm(pcn, "1", "223.65", "94.08", sv("99214", "223.65", "94.08", 1, "CAS*CO*45*106.05~CAS*PR*2*23.52~", allowed="117.60"), payer_ctl=ctl)


def secondary_paid(pcn: str, ctl: str = "MG300B") -> str:
    """The second payer pays the coinsurance the primary left: $23.52 (the primary's payment applied, OA-23)."""
    return clm(pcn, "2", "223.65", "23.52", sv("99214", "223.65", "23.52", 1, "CAS*OA*23*94.08~CAS*CO*45*106.05~"), payer_ctl=ctl)


def test_a_denial_paid_later_records_the_second_payers_share_too(tmp_path, kind):
    """C017's visit is denied (2026-04-15); C024 is paid by the primary ($94.08, 2026-05-20) and by the second payer
    ($23.52, the coinsurance, 2026-05-28). The closure recovered $117.60 and names both payments."""
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99214", "223.65", "117.60"), "20260301")
    w.remit([visit_denied_line(f"{BILL}C017")], "20260415")
    w.remit([primary_paid(f"{BILL}C024")], "20260520")
    w.remit([secondary_paid(f"{BILL}C024")], "20260528", **MEDIGAP)
    w.run("r1")
    r = w.items()[f"{BILL}C017:99214:DENIED_MISSING_INFO"]
    assert r["status"] == "resolved" and r["recovered_cents"] == 11760, (r["status"], r["recovered_cents"], r["auto_note"])
    later = json.loads(r["evidence"])["paid_later"]
    assert later["paid_cents"] == 11760 and sorted(p["paid_cents"] for p in later["payments"]) == [2352, 9408], later
    assert "2026-05-28" in r["auto_note"], r["auto_note"]
    w.store.close()


def test_a_whole_claim_denial_paid_later_records_the_second_payers_share_too(tmp_path, kind):
    """C017 is denied whole (CO-27); a biller works it; C024 is paid by the primary and by the second payer: the closure
    recovered both."""
    from remitwatch.actions import apply_status
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99214", "223.65", "117.60"), "20260301")
    w.remit([clm(f"{BILL}C017", "4", "223.65", "0.00", sv("99214", "223.65", "0.00", 1), claim_cas="CAS*CO*27*223.65~")], "20260415")
    w.run("r1")
    fid = w.items()[f"{BILL}C017::CLAIM_DENIED_UNPAID"]["finding_id"]
    apply_status(w.store.conn, fid, "in_progress", "Eligibility fixed, sent again", actor="biller")
    w.store.conn.commit()
    w.remit([primary_paid(f"{BILL}C024")], "20260520")
    w.remit([secondary_paid(f"{BILL}C024")], "20260528", **MEDIGAP)
    w.run("r2")
    r = w.items()[f"{BILL}C017::CLAIM_DENIED_UNPAID"]
    assert r["status"] == "resolved" and r["recovered_cents"] == 11760, (r["status"], r["recovered_cents"], r["auto_note"])
    w.store.close()


def test_a_second_payers_share_paid_twice_counts_once(tmp_path, kind):
    """The visit is denied on C017 and again on C024; C031 is paid by the primary ($94.08) and the second payer pays the
    coinsurance twice ($23.52 on two remittances). One item records $117.60, the other nothing (counted there): the second
    payer's second payment is not a second share."""
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99214", "223.65", "117.60"), "20260301")
    w.remit([visit_denied_line(f"{BILL}C017")], "20260415")
    w.remit([visit_denied_line(f"{BILL}C024", payer_ctl="PC300C")], "20260501")
    w.remit([primary_paid(f"{BILL}C031", ctl="PC300D")], "20260610")
    w.remit([secondary_paid(f"{BILL}C031")], "20260615", **MEDIGAP)
    w.remit([secondary_paid(f"{BILL}C031", ctl="MG300C")], "20260625", **MEDIGAP)
    w.run("r1")
    rows = w.rows("category='denial'")
    assert [r["status"] for r in rows] == ["resolved", "resolved"], [(r["patient_control_number"], r["status"]) for r in rows]
    assert sorted(r["recovered_cents"] for r in rows) == [0, 11760], [r["recovered_cents"] for r in rows]
    w.store.close()


def test_a_second_payers_share_alone_never_closes_a_primarys_denial(tmp_path, kind):
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99214", "223.65", "117.60"), "20260301")
    w.remit([visit_denied_line(f"{BILL}C017")], "20260415")
    w.remit([secondary_paid(f"{BILL}C024")], "20260528", **MEDIGAP)
    w.run("r1")
    assert w.items()[f"{BILL}C017:99214:DENIED_MISSING_INFO"]["status"] == "open"
    w.store.close()
