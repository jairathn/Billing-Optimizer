"""resolve_elsewhere closes a denial from another claim only for codes paid at most once per patient and date. That
measure still reads the bill as the first nine characters of the claim id, which bill_of() no longer does. Invented data."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from r3helpers import add, base, by_key, clm, show, sv  # noqa: E402

from remitwatch.detect import Detector  # noqa: E402


def paid(pcn, who, payer_ctl):
    return clm(pcn, "1", "150.00", "70.00", sv("88305", "150.00", "70.00", 1, "CAS*CO*45*80.00~", allowed="70.00"), who=who, payer_ctl=payer_ctl)


def test_a_code_paid_twice_a_day_on_separate_claims_is_not_called_paid_once(tmp_path):
    """Claim ids from the other system (a date and a sequence number). Four patients had two specimens (88305) paid on
    the same date on two separate claims: 88305 is paid more than once for a patient on a date (4 of 29 patient-dates).
    Patient Oak's second specimen is denied on claim 20260401051 and her first is paid on claim 20260401052 later: that
    payment may be the other specimen, so the denial must not be closed from it."""
    base(tmp_path, "88305", "150.00", "70.00").close()
    twice = []
    for i in range(4):
        who = f"PAIR*PAT{i}****MI*W7770000{i}"
        twice += [paid(f"2026040103{i}", who, f"P3{i}"), paid(f"2026040104{i}", who, f"P4{i}")]
    oak = "OAK*KIM****MI*W55500399"
    add(tmp_path, twice + [clm("20260401051", "1", "150.00", "0.00", sv("88305", "150.00", "0.00", 1, "CAS*CO*16*150.00~"), who=oak, payer_ctl="P51")],
        "20260415").close()
    store = add(tmp_path, [paid("20260401052", oak, "P52")], "20260520")
    Detector(store, run_id="t").run()
    r = by_key(store)["20260401051:88305:DENIED_MISSING_INFO"]
    show([r])
    assert r["status"] == "open", f"closed from another claim although 88305 is paid twice a day for some patients: {r['auto_note']}"
    store.close()


def test_control_the_same_with_ema_claim_ids(tmp_path):
    base(tmp_path, "88305", "150.00", "70.00").close()
    twice = []
    for i in range(4):
        who = f"PAIR*PAT{i}****MI*W7770000{i}"
        twice += [paid(f"CB000{i}300C017", who, f"P3{i}"), paid(f"CB000{i}400C017", who, f"P4{i}")]
    oak = "OAK*KIM****MI*W55500399"
    add(tmp_path, twice + [clm("CB0000510C017", "1", "150.00", "0.00", sv("88305", "150.00", "0.00", 1, "CAS*CO*16*150.00~"), who=oak, payer_ctl="P51")],
        "20260415").close()
    store = add(tmp_path, [paid("CB0000520C017", oak, "P52")], "20260520")
    Detector(store, run_id="t").run()
    r = by_key(store)["CB0000510C017:88305:DENIED_MISSING_INFO"]
    show([r])
    assert r["status"] == "open"
    store.close()
