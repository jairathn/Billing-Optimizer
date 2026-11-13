"""A charge posted twice in EMA: the same visit code twice on one claim and date. The payer pays one line and denies the
other as a duplicate (OA-18). A visit is paid at most once per patient and day, so nothing more is owed. The live release
(3f8855b) lists nothing; the release counts the paid line as a second unit at stake. Invented data only."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lvn import add, base, both, clm, money, show, sv  # noqa: E402


def test_a_visit_posted_twice_on_one_claim(tmp_path):
    base(tmp_path)
    add(tmp_path, [clm("CB0000960C017", "1", "313.32", "76.15", sv("99213", "156.66", "76.15", cas="CAS*CO*45*80.51~", allowed="76.15")
                       + sv("99213", "156.66", "0.00", cas="CAS*OA*18*156.66~"), payer_ctl="A960")], "20260415")
    live, rel = both(tmp_path)
    show("live", live)
    show("release", rel)
    print("live owed:", money(live), " release owed:", money(rel))
    assert money(rel) == money(live), "the second copy of a once-a-day visit is listed as a duplicate never paid"


def test_first_lesion_destruction_posted_twice_on_one_claim(tmp_path):
    """17000 is the first actinic keratosis destroyed that day (the rest are 17003): it is billed once per day."""
    base(tmp_path)
    add(tmp_path, [clm("CB0000961C017", "1", "300.00", "60.00", sv("17000", "150.00", "60.00", cas="CAS*CO*45*90.00~", allowed="60.00")
                       + sv("17000", "150.00", "0.00", cas="CAS*OA*18*150.00~"), payer_ctl="A961")], "20260415")
    live, rel = both(tmp_path)
    show("live", live)
    show("release", rel)
    print("live owed:", money(live), " release owed:", money(rel))
    assert money(rel) == money(live), "the second copy of a once-a-day code is listed as a duplicate never paid"
