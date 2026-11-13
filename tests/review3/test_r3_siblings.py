"""Two lines of the same code on one date (two specimens, 88305 and 88305-59): a payment of one is not a payment of the
other. Invented data only."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from r3helpers import add, base, by_key, clm, show, sv  # noqa: E402

from remitwatch.actions import apply_status  # noqa: E402
from remitwatch.detect import Detector  # noqa: E402

PAID = sv("88305", "150.00", "70.00", 1, "CAS*CO*45*80.00~", allowed="70.00")


def spec(paid, cas, mods=""):
    return sv("88305", "150.00", paid, 1, cas, allowed=paid if float(paid) > 0 else None, mods=mods)


def test_a_whole_claim_denial_with_one_of_two_specimens_paid_counts_one_specimen(tmp_path):
    """C017 paid one specimen ($70). The corrected C024 bills both specimens and is denied whole as a duplicate. One
    specimen ($70) is unpaid; the finding must not say $140."""
    base(tmp_path, "88305", "150.00", "70.00").close()
    add(tmp_path, [clm("CB0000300C017", "1", "150.00", "70.00", PAID)], "20260415").close()
    store = add(tmp_path, [clm("CB0000300C024", "4", "300.00", "0.00", spec("0.00", "CAS*CO*18*150.00~") + spec("0.00", "CAS*CO*18*150.00~", "59"))],
                "20260520")
    Detector(store, run_id="t").run()
    r = by_key(store)["CB0000300C024::CLAIM_DENIED_UNPAID"]
    show([r])
    assert r["shortfall_cents"] == 7000, f"one unpaid specimen counted as ${r['shortfall_cents']/100:.2f}"
    store.close()


def test_a_second_specimen_denied_as_a_duplicate_of_the_first_is_listed(tmp_path):
    """On one claim, specimen 1 (88305) is paid and specimen 2 (88305, modifier 59 left off) is denied CO-18 as a
    duplicate. The whole-claim version of this is listed (tests/test_review_takeback.py); the line version is hidden.
    Here the practice bills specimens as separate lines (five visits with two specimens each, both paid), so 88305 is a
    code paid more than once a day; where a code is paid at most once a day, a second line is a charge posted twice."""
    base(tmp_path, "88305", "150.00", "70.00").close()
    add(tmp_path, [clm(f"CB{900 + i:07d}C017", "1", "300.00", "140.00", PAID + PAID) for i in range(5)], "20260310").close()
    store = add(tmp_path, [clm("CB0000300C017", "1", "300.00", "70.00", PAID + spec("0.00", "CAS*CO*18*150.00~"))], "20260415")
    Detector(store, run_id="t").run()
    rows = by_key(store)
    show(rows.values())
    assert any(k.startswith("CB0000300C017:88305:") for k in rows), "specimen 2 was never paid and nothing is listed"
    store.close()


def test_a_corrected_claim_that_pays_specimen_1_again_does_not_close_specimen_2(tmp_path):
    """C017: specimen 1 paid, specimen 2 (88305-59) denied for missing information; the biller appeals specimen 2. The
    replacement claim C024 is processed: C017's payment is reversed, specimen 1 is paid again on C024 and specimen 2 is
    denied again. Specimen 2 was never paid: its appealed finding must not close as recovered."""
    base(tmp_path, "88305", "150.00", "70.00").close()
    store = add(tmp_path, [clm("CB0000300C017", "1", "300.00", "70.00", PAID + spec("0.00", "CAS*CO*16*150.00~", "59"))], "20260415")
    Detector(store, run_id="t1").run()
    f = by_key(store)["CB0000300C017:88305:DENIED_MISSING_INFO"]
    apply_status(store.conn, f["finding_id"], "appealed", "Sent the path report for specimen 2", actor="biller")
    store.conn.commit()
    store.close()
    reversal = clm("CB0000300C017", "22", "-150.00", "-70.00", sv("88305", "-150.00", "-70.00", -1, "CAS*CO*45*-80.00~"))
    again = clm("CB0000300C024", "1", "300.00", "70.00", PAID + spec("0.00", "CAS*CO*16*150.00~", "59"))
    store = add(tmp_path, [reversal, again], "20260610")
    Detector(store, run_id="t2").run()
    rows = by_key(store)
    show(rows.values())
    r = rows["CB0000300C017:88305:DENIED_MISSING_INFO"]
    assert r["status"] == "appealed", f"closed as {r['status']} with ${(r['recovered_cents'] or 0)/100:.2f} recovered: {r['auto_note']}"
    store.close()
