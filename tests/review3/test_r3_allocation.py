"""One payment of one unit can only pay one unit: it must not close two denied lines. Invented data only."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from r3helpers import add, base, by_key, clm, show, sv  # noqa: E402

from remitwatch.detect import Detector  # noqa: E402

OAK = "OAK*KIM****MI*W55500399"


def spec(paid, cas, mods=""):
    return sv("88305", "150.00", paid, 1, cas, allowed=paid if float(paid) > 0 else None, mods=mods)


def test_one_specimen_paid_later_does_not_close_both_denied_specimens(tmp_path):
    """C017 bills two specimens (88305 and 88305-59); both are denied for missing information (two findings). The
    corrected C024 bills specimen 1 only and it is paid $70. Specimen 2 was never paid."""
    base(tmp_path, "88305", "150.00", "70.00").close()
    add(tmp_path, [clm("CB0000300C017", "1", "300.00", "0.00", spec("0.00", "CAS*CO*16*150.00~") + spec("0.00", "CAS*CO*16*150.00~", "59"))],
        "20260415").close()
    store = add(tmp_path, [clm("CB0000300C024", "1", "150.00", "70.00", spec("70.00", "CAS*CO*45*80.00~"))], "20260520")
    Detector(store, run_id="t").run()
    rows = [r for r in store.query("SELECT signal, status, recovered_cents, auto_note, modifiers FROM findings WHERE patient_control_number='CB0000300C017'")]
    for r in rows:
        print(dict(r))
    open_ = [r for r in rows if r["status"] == "open"]
    recovered = sum(r["recovered_cents"] or 0 for r in rows)
    assert open_ and recovered <= 7000, f"one $70 payment closed {len(rows) - len(open_)} specimens, ${recovered/100:.2f} recorded recovered"
    store.close()


def test_one_payment_on_another_bill_does_not_close_both_denied_specimens(tmp_path):
    """The same with the payment on another bill for the same patient (resolve_elsewhere)."""
    base(tmp_path, "88305", "150.00", "70.00").close()
    add(tmp_path, [clm("CB0000300C017", "1", "300.00", "0.00", spec("0.00", "CAS*CO*16*150.00~") + spec("0.00", "CAS*CO*16*150.00~", "59"), who=OAK)],
        "20260415").close()
    store = add(tmp_path, [clm("CB0000700C017", "1", "150.00", "70.00", spec("70.00", "CAS*CO*45*80.00~"), who=OAK, payer_ctl="PC700")], "20260520")
    Detector(store, run_id="t").run()
    rows = [r for r in store.query("SELECT signal, status, recovered_cents, auto_note, hint FROM findings WHERE patient_control_number='CB0000300C017'")]
    for r in rows:
        print(dict(r))
    open_ = [r for r in rows if r["status"] == "open"]
    recovered = sum(r["recovered_cents"] or 0 for r in rows)
    assert open_ and recovered <= 7000, f"one $70 payment closed {len(rows) - len(open_)} specimens, ${recovered/100:.2f} recorded recovered"
    store.close()
