"""A PLB WO reference is matched against every payer's claims. Invented data only."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from r3helpers import add, base, by_key, show, visit_denied_line, visit_paid  # noqa: E402

from remitwatch.detect import Detector  # noqa: E402

OAK = "OAK*KIM****MI*W55500399"


def test_another_payers_claim_number_is_not_recouped(tmp_path):
    """Oak's visit: C017 denied by Humana (2026-04-15), corrected C024 paid by Humana on 2026-05-01; Humana's claim
    number for it is 2026050100001. On 2026-06-01 Anthem recoups an overpayment of its own, for another patient's claim
    paid before the archive, whose Anthem claim number happens to be 2026050100001 (PLB WO:2026050100001). Oak's
    Humana payment stands."""
    base(tmp_path).close()
    add(tmp_path, [visit_denied_line("CB0000700C017", who=OAK, payer_ctl="2026041500007")], "20260415", payer="HUMANA", payer_id="HUMNA").close()
    add(tmp_path, [visit_paid("CB0000700C024", who=OAK, payer_ctl="2026050100001")], "20260501", payer="HUMANA", payer_id="HUMNA").close()
    store = add(tmp_path, [visit_paid("CB0000800C017", who="ASH*RO****MI*W55500304", payer_ctl="2026060100008")], "20260601",
                plb="PLB*1777777772*20261231*WO:2026050100001*117.60~")
    Detector(store, run_id="t").run()
    r = by_key(store)["CB0000700C017:99214:DENIED_MISSING_INFO"]
    show([r])
    assert r["status"] == "resolved", "Anthem's recoupment, by its own claim number, took Humana's payment for another patient"
    store.close()
