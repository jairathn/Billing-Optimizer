"""
Round 2 of the fixes, the money that falls off the list (plans/catchup_trace_2026-11-23.md and the misuse check):
T-C1, a payment for the technical part (TC) of a service closed the denial of its professional part (26).

Every claim, member number, name, amount and date here is invented (tests/test_remitwatch.remit, tests/test_features.svc).
Each test runs on the server's store file and on the compact in-memory store the serverless cycle builds and analyses.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch.detect import Detector  # noqa: E402
from remitwatch.store import Store  # noqa: E402
from tests.test_features import NPI, svc  # noqa: E402
from tests.test_remitwatch import remit  # noqa: E402

KINDS = ["file", "compact"]


def _store(tmp_path, kind: str) -> Store:
    return Store(str(tmp_path / "s.sqlite")) if kind == "file" else Store(":memory:", compact=True)


def _add(store: Store, tmp_path, claims: list[str], pay_date: str, payer: str = "ANTHEM", payer_id: str = "ANTHM") -> None:
    """Remittances holding these claims (20 to a file), read into the store the way the feeds read them."""
    paths = []
    for chunk in [claims[i:i + 20] for i in range(0, len(claims), 20)]:
        n = len(list(tmp_path.glob("*.RMT")))
        p = tmp_path / f"r{n:04d}.RMT"
        p.write_bytes(remit("".join(chunk), "100.00", payer=payer, payer_id=payer_id, seg_count=99, isa_ctrl=f"{n + 1:09d}", pay_date=pay_date))
        paths.append(str(p))
    store.ingest_835_paths(paths)


def _denials(store: Store) -> dict[str, dict]:
    return {r["patient_control_number"]: dict(r) for r in store.query(
        "SELECT patient_control_number, signal, status, auto_note, hint, evidence FROM findings WHERE category='denial'")}


# --------------------------------------------------------------------------- T-C1: the parts of a service (26, TC)

def _path(i: int, who: str, mods: str, paid: str) -> str:
    """One claim with one 88305 line: `mods` is '26' (the professional part), 'TC' (the technical part) or '' (the whole
    service). Paid `paid`, or denied for missing information (CO-16) when that is 0."""
    cas = f"CAS*CO*45*{150 - float(paid):.2f}~" if float(paid) > 0 else "CAS*CO*16*150.00~"
    return (f"CLP*CB{i:07d}C017*1*150.00*{paid}*0.00*12*PC{i}*11~NM1*QC*1*{who}~NM1*82*2*TEST PROVIDER*****XX*{NPI}~DTM*232*20260401~"
            + svc("88305", "150.00", paid, paid if float(paid) > 0 else None, cas, mods=mods))


ORCHARD, BIRCH, ELM, FIR, YEW = ("ORCHARD*LEE****MI*W55500300", "BIRCH*ANN****MI*W55500301", "ELM*BO****MI*W55500302",
                                 "FIR*JO****MI*W55500303", "YEW*MO****MI*W55500304")


@pytest.mark.parametrize("kind", KINDS)
def test_a_payment_for_one_part_of_a_service_closes_only_that_part(tmp_path, kind):
    """88305 is paid once a patient and date on 80 other claims. Five patients have an 88305 line denied on April 15, and
    another bill for the same patient, date and code is paid later:
    - Orchard: the professional part (26) denied, the technical part (TC) paid. A TC payment pays the slide preparation,
      not the reading: the 26 denial stays open, and nothing says it may be paid (the trace's T-C1: wrong closure).
    - Birch: 26 denied, 26 paid. Closed.
    - Elm: 26 denied, the whole service paid (no 26 or TC). The whole service holds the reading: closed.
    - Fir: the whole service denied, 26 paid. Half of it: open, saying only the professional part was paid. When the TC is
      paid too (June), both parts are paid: closed.
    - Yew: TC denied, 26 paid. Open."""
    s = _store(tmp_path, kind)
    usual = [_path(10 + i, f"P{i:03d}*PAT****MI*W1{i:07d}", "26", "40.00") for i in range(80)]
    _add(s, tmp_path, usual + [_path(300, ORCHARD, "26", "0"), _path(301, BIRCH, "26", "0"), _path(302, ELM, "26", "0"),
                               _path(303, FIR, "", "0"), _path(304, YEW, "TC", "0")], "20260415")
    _add(s, tmp_path, [_path(700, ORCHARD, "TC", "30.00"), _path(701, BIRCH, "26", "40.00"), _path(702, ELM, "", "70.00"),
                       _path(703, FIR, "26", "40.00"), _path(705, YEW, "26", "40.00")], "20260520")
    Detector(s, run_id="t1").run()
    rows = _denials(s)
    a = rows["CB0000300C017"]
    print("26 denied, TC paid:", a["status"], a["auto_note"], a["hint"])
    assert a["status"] == "open", f"a TC payment closed the 26 denial: {a['auto_note']}"
    assert "CB0000700C017" not in (a["hint"] or "") and "maybe_paid_later" not in json.loads(a["evidence"])
    assert rows["CB0000301C017"]["status"] == "resolved" and "under claim CB0000701C017" in rows["CB0000301C017"]["auto_note"]
    assert rows["CB0000302C017"]["status"] == "resolved" and "under claim CB0000702C017" in rows["CB0000302C017"]["auto_note"]
    fir = rows["CB0000303C017"]
    print("whole service denied, 26 paid:", fir["status"], fir["auto_note"], fir["hint"])
    assert fir["status"] == "open", f"the 26 alone closed a denial of the whole service: {fir['auto_note']}"
    assert fir["hint"] == "Possibly paid on claim CB0000703C017 (2026-05-20)"
    assert json.loads(fir["evidence"])["maybe_paid_later"]["why_not_closed"] == "only the professional part (26) was paid there, not the whole service"
    yew = rows["CB0000304C017"]
    assert yew["status"] == "open", f"a 26 payment closed the TC denial: {yew['auto_note']}"
    # June: Fir's TC is paid on another bill. Both parts of the service are paid now
    _add(s, tmp_path, [_path(704, FIR, "TC", "30.00")], "20260610")
    Detector(s, run_id="t2").run()
    rows = _denials(s)
    print("whole service denied, 26 and TC paid:", rows["CB0000303C017"]["status"], rows["CB0000303C017"]["auto_note"])
    assert rows["CB0000303C017"]["status"] == "resolved" and "under claim CB0000703C017" in rows["CB0000303C017"]["auto_note"]
    assert rows["CB0000300C017"]["status"] == "open" and rows["CB0000304C017"]["status"] == "open"
    s.close()
