"""One payer answer delivered twice the same day is one item (trace finding T-I3).

A payer can send the same answer to a claim twice on one day, on two remittances (two checks): the same claim id, the same
lines, the same amounts. An underpayment in that answer was listed once per delivery (the item is keyed by the line),
two items for one underpayment on the Work now list. One item now, on the first delivery; a person's work on the other
delivery's item keeps that one instead.

Everything here is invented: names, member numbers, claim ids, payer claim numbers, traces and amounts. Each test runs on
both kinds of store."""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.review3.r3helpers import clm, sv  # noqa: E402
from tests.test_features import clean_visits  # noqa: E402
from tests.test_takebacks_and_recoveries import World, kind  # noqa: E402,F401

BILL = "CB0000800"


def short_answer() -> str:
    """The payer allows the visit at $76.00 against its usual $94.00 and pays $74.00 (a $2.00 copay)."""
    return clm(f"{BILL}C017", "1", "156.00", "74.00", sv("99213", "156.00", "74.00", 1, "CAS*PR*3*2.00~CAS*CO*45*80.00~", allowed="76.00"),
               payer_ctl="PC800")


def test_one_underpayment_delivered_twice_the_same_day_is_one_item(tmp_path, kind):
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99213", "156.00", "94.00"), "20260301")
    w.remit([short_answer()], "20260520")
    w.remit([short_answer()], "20260520")          # the same answer again, on another check the same day
    w.run("r1")
    rows = w.rows("signal='UNDERPAID_VS_OWN_RATE' AND COALESCE(stale,0)=0")
    assert len(rows) == 1, [(r["finding_id"], r["shortfall_cents"]) for r in rows]
    # both payments stand: the one item says the payer paid the service twice (archive rule 4), nothing more to ask for
    ar = json.loads(rows[0]["evidence"])["archive_rule"]
    assert (ar["reclass"], ar["possible_overpayment_cents"]) == ("overpaid", 7400), ar
    w.store.close()


def test_the_delivery_a_person_worked_keeps_the_item(tmp_path, kind):
    """An earlier release listed both deliveries, and a biller started the second delivery's item: that one stays listed
    with the biller's status, the first is not listed."""
    from remitwatch.detect import _fid
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99213", "156.00", "94.00"), "20260301")
    w.remit([short_answer()], "20260520")
    w.remit([short_answer()], "20260520")
    w.run("r1")
    rows = w.rows("signal='UNDERPAID_VS_OWN_RATE'")
    assert len(rows) == 1
    kept = rows[0]
    other = next(r[0] for r in w.store.conn.execute("SELECT line_key FROM lines WHERE patient_control_number=? AND line_key != ?",
                                                    (f"{BILL}C017", kept["line_key"])))
    # the earlier release's item on the other delivery, started by a biller
    worked = dict(kept, finding_id=_fid("UNDERPAID_VS_OWN_RATE", other), line_key=other, status="in_progress", notes="asked to reprocess")
    cols = list(worked)
    w.store.conn.execute(f"INSERT INTO findings ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [worked[c] for c in cols])
    w.store.conn.commit()
    w.run("r2")
    rows = w.rows("signal='UNDERPAID_VS_OWN_RATE' AND COALESCE(stale,0)=0")
    assert [(r["line_key"], r["status"]) for r in rows] == [(other, "in_progress")], [(r["line_key"] == other, r["status"]) for r in rows]
    w.store.close()
