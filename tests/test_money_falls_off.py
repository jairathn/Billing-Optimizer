"""
Round 2 of the fixes, the money that falls off the list (plans/catchup_trace_2026-11-23.md and the misuse check):
T-C1, a payment for the technical part (TC) of a service closed the denial of its professional part (26); T-C2, a claim
no primary payer answered left the never-answered list when only a secondary payer answered it; T-N5, items were made
from a payer's answer to a claim the practice voided (CLP09 8); CH-8, one payer's remittances stopped while the others'
went on, and its claims became never-answered items with nothing saying the payer had stopped.

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


# --------------------------------------------------------------------------- T-C2: only a secondary payer answered

def _secondary(claim_id: str, paid: str = "23.52", dos: str = "20260914") -> str:
    """A secondary payer's answer to a claim (CLP02 2, processed as secondary): OA-23 for what the primary did, and its share."""
    return (f"CLP*{claim_id}*2*223.65*{paid}*0.00*12*SEC{claim_id[4:9]}*11~NM1*QC*1*SAMPLE*PAT****MI*M00000777~"
            f"NM1*82*2*TEST PROVIDER*****XX*{NPI}~DTM*232*{dos}~"
            + svc("99214", "223.65", paid, "117.60", f"CAS*OA*23*{223.65 - float(paid):.2f}~", dos=dos))


def _primary(claim_id: str, dos: str = "20260914") -> str:
    """The claim's primary payer processed it and paid (CLP02 1)."""
    return (f"CLP*{claim_id}*1*223.65*117.60*0.00*12*PRI{claim_id[4:9]}*11~NM1*QC*1*SAMPLE*PAT****MI*M00000778~"
            f"NM1*82*2*TEST PROVIDER*****XX*{NPI}~DTM*232*{dos}~" + svc("99214", "223.65", "117.60", "117.60", "CAS*CO*45*106.05~", dos=dos))


def _visits(n: int = 25) -> list[str]:
    """Anthem paying 99214 at $117.60 on other claims: its usual rate, and remittances that reach the day they are dated."""
    from tests.test_features import claim
    return [claim(i, svc("99214", "223.65", "117.60", "117.60", "CAS*CO*45*106.05~")) for i in range(1, n + 1)]


def _chased(s: Store, run: str) -> dict[str, dict]:
    Detector(s, run_id=run).run()
    return {r["patient_control_number"]: dict(r) for r in s.query(
        "SELECT * FROM findings WHERE signal IN ('NO_ANSWER','REJECTED_PRE_PAYER') AND COALESCE(stale,0)=0")}


@pytest.mark.parametrize("kind", KINDS)
def test_a_claim_only_a_secondary_payer_answered_is_still_chased_from_its_primary(tmp_path, kind):
    """EMA shows four claims sent in September. The remittances to November 10 hold:
    - S01, billed to Anthem: only Humana's answer, processed as secondary (CLP02 2, OA-23), $23.52, its share. Anthem's
      remittance is not on file: the claim stays listed, sized at Anthem's usual $117.60 less the $23.52, and the item
      says the secondary paid its share and the primary's remittance is missing (trace finding T-C2).
    - S02, billed to Humana itself, and Humana processed it as secondary: the payer EMA billed answered. Not listed.
    - S03: C017 sent September 1 and C024 sent again on the 20th, only Humana answered C024 as secondary. The bill is not
      answered by its primary: C024, the latest submission, is listed (before, the answer to C024 dropped the whole bill).
    - S04, billed to Anthem, processed by Anthem as primary (CLP02 1). Not listed."""
    from tests.test_workable import ema
    s = _store(tmp_path, kind)
    _add(s, tmp_path, [_secondary("CB0000S01C017"), _secondary("CB0000S02C017"), _secondary("CB0000S03C024")], "20261020", payer="HUMANA",
         payer_id="HUMNA")
    _add(s, tmp_path, [_primary("CB0000S04C017")], "20261021")
    _add(s, tmp_path, _visits(), "20261110")
    c = s.conn
    ema(c, "CB0000S01C017")
    ema(c, "CB0000S02C017", payer="Humana", payer_id="61101")
    ema(c, "CB0000S03C017")
    ema(c, "CB0000S03C017", family="claim_status", claim_status="Submitted", claim_submission_date="2026-09-01")
    ema(c, "CB0000S03C024")
    ema(c, "CB0000S03C024", family="claim_status", claim_status="Submitted", claim_submission_date="2026-09-20")
    ema(c, "CB0000S04C017")
    c.commit()
    found = _chased(s, "t1")
    print("listed:", sorted(found))
    assert set(found) == {"CB0000S01C017", "CB0000S03C024"}, (
        f"listed {sorted(found)}: S01 and S03's C024 wait for their primary's remittance; S02's own payer and S04's primary answered")
    a = found["CB0000S01C017"]
    print(a["title"], "|", a["hint"], "|", a["expected_cents"], a["shortfall_cents"])
    assert a["signal"] == "NO_ANSWER" and a["payer_group"] == "Anthem / BCBS" and a["status"] == "open"
    assert a["title"] == "Sent 2026-09-15, no answer from Anthem / BCBS in 56 days; only Humana answered, as secondary: 99214"
    assert a["hint"].startswith("Humana processed this claim as secondary on 2026-10-20 and paid $23.52, its share. The primary's "
                                "remittance, from Anthem / BCBS, is not on file")
    assert (a["expected_cents"], a["shortfall_cents"]) == (11760, 11760 - 2352)
    ev = json.loads(a["evidence"])
    assert ev["secondary_answer"] == {"payer": "Humana", "date": "2026-10-20", "paid_cents": 2352, "processed_as": "secondary"}
    assert "Another submission of this bill was answered" not in (found["CB0000S03C024"]["hint"] or "")
    # Anthem's remittance for S01 arrives (processed as primary): S01 is answered and leaves the list
    _add(s, tmp_path, [_primary("CB0000S01C017")], "20261112")
    found = _chased(s, "t2")
    assert set(found) == {"CB0000S03C024"}
    s.close()


# --------------------------------------------------------------------------- T-N5: a payer's answer to a voided claim

def _answer(i: int, status: str, freq: str, paid: str = "0.00", cas: str = "CAS*CO*16*223.65~") -> str:
    """A payer's answer to claim i (CLP02 `status`), naming the claim's frequency code in CLP09: 1 an original claim, 7 a
    corrected one, 8 a claim the practice voided. One 99214 line, paid `paid`."""
    sign = "-" if status == "22" else ""
    allowed = "117.60" if float(paid) else None
    return (f"CLP*CB{i:07d}C017*{status}*{sign}223.65*{sign}{paid}*0.00*12*PC{i}*11*{freq}~NM1*QC*1*VOID*PAT****MI*W{i:08d}~"
            f"NM1*82*2*TEST PROVIDER*****XX*{NPI}~DTM*232*20260401~"
            + svc("99214", f"{sign}223.65", f"{sign}{paid}", allowed if status != "22" else None, cas).replace("**1~", f"**{sign}1~", 1))


@pytest.mark.parametrize("kind", KINDS)
def test_no_item_is_made_from_a_payers_answer_to_a_voided_claim(tmp_path, kind):
    """The payer's answers to claims the practice voided (CLP09 8) are not money owed: a line paid nothing with CO-16 on
    claim 501, and a whole claim denied (CLP02 4) on claim 502, both voids, make no item (trace finding T-N5). The same
    answers to an original claim (503) and a corrected one (504) still do. A void's reversal still takes its payment back:
    claim 505 was paid, then voided, and the payer's reversal of it (CLP02 22, CLP09 8) leaves nothing of that payment."""
    from tests.test_features import clean_visits
    s = _store(tmp_path, kind)
    _add(s, tmp_path, clean_visits(1, 25, "99214", "223.65", "117.60") + [_answer(505, "1", "1", "117.60", "CAS*CO*45*106.05~")], "20260415")
    _add(s, tmp_path, [_answer(501, "1", "8"), _answer(502, "4", "8"), _answer(503, "1", "1"), _answer(504, "4", "7"),
                       _answer(505, "22", "8", "117.60", "CAS*CO*45*-106.05~")], "20260520")
    Detector(s, run_id="t").run()
    made = {(r["patient_control_number"], r["signal"]) for r in s.query(
        "SELECT patient_control_number, signal FROM findings WHERE category IN ('denial','bundling','downcoding','underpayment')")}
    print("items:", sorted(made))
    assert not {pcn for pcn, _sig in made} & {"CB0000501C017", "CB0000502C017"}, f"items made from answers to voided claims: {sorted(made)}"
    assert ("CB0000503C017", "DENIED_MISSING_INFO") in made and ("CB0000504C017", "CLAIM_DENIED_UNPAID") in made
    d = Detector(s, run_id="ledger")
    d.load()
    paid_505 = [ln["line_key"] for ln in d.lines if ln["patient_control_number"] == "CB0000505C017" and ln["claim_status_code"] == "1"]
    assert paid_505 and not any(k in d.standing for k in paid_505), "the void's reversal no longer takes the payment back"
    s.close()


# --------------------------------------------------------------------------- CH-8: one payer's remittances stop

def _stopped(checks: list[dict]) -> dict | None:
    return next((h for h in checks if h.get("key") == "payer_stopped"), None)


@pytest.mark.parametrize("kind", KINDS)
def test_a_payer_whose_remittances_stopped_has_its_claims_counted_not_listed_as_never_answered(tmp_path, kind):
    """Anthem's remittances reach November 10; Humana's stop on September 25 (its files no longer come, or it stopped
    sending them), and the feed reaches November 10. EMA shows claims sent that no remittance answers:
    - H01, sent to Humana October 1 (due October 31): Humana's remittances do not reach that day, so nothing on file says
      whether it answered. It is not listed as never answered (before, it was: misuse check CH-8), and the Data tab's
      checks say "No remittance from Humana since 2026-09-25" with the claim and its dollars, from the store on a server
      and from the dashboard's copy on the serverless site.
    - H02, sent to Humana August 1 (due August 31): Humana's remittances run past that day and do not answer it. Listed.
    - A01, sent to Anthem October 1: Anthem's remittances reach past its due date. Listed.
    - Z01, sent to Aetna October 1: no remittance from Aetna is on file at all, so nothing says a feed of its stopped. It
      is judged by the feed date as before: listed, with the hint that no remittance from Aetna is on file.
    - U01, sent to UnitedHealthcare October 9 (due November 8), whose last remittance is November 5: between two files,
      not stopped. It waits for UnitedHealthcare's next file and is counted, but the Data tab does not name a payer only
      5 days behind the feed (the feed check allows the whole feed 10)."""
    from remitwatch import cloud, insights
    from tests.test_features import claim
    from tests.test_workable import ema
    s = _store(tmp_path, kind)
    humana = [claim(800 + i, svc("99214", "223.65", "117.60", "117.60", "CAS*CO*45*106.05~", dos="20260814"), dos="20260814") for i in range(5)]
    _add(s, tmp_path, humana, "20260925", payer="HUMANA", payer_id="HUMNA")
    uhc = [claim(820 + i, svc("99214", "223.65", "117.60", "117.60", "CAS*CO*45*106.05~", dos="20261001"), dos="20261001") for i in range(3)]
    _add(s, tmp_path, uhc, "20261105", payer="UNITEDHEALTHCARE", payer_id="87726")
    _add(s, tmp_path, _visits(), "20261110")
    c = s.conn
    ema(c, "CB0000H01C017", payer="Humana", payer_id="61101", service_date="2026-09-30", bill_created="2026-10-01")
    ema(c, "CB0000H02C017", payer="Humana", payer_id="61101", service_date="2026-07-31", bill_created="2026-08-01")
    ema(c, "CB0000A01C017", service_date="2026-09-30", bill_created="2026-10-01")
    ema(c, "CB0000Z01C017", payer="Aetna", payer_id="60054", service_date="2026-09-30", bill_created="2026-10-01")
    ema(c, "CB0000U01C017", payer="UnitedHealthcare", payer_id="87726", service_date="2026-10-08", bill_created="2026-10-09")
    c.commit()
    found = _chased(s, "t1")
    print("listed:", sorted(found))
    assert set(found) == {"CB0000H02C017", "CB0000A01C017", "CB0000Z01C017"}, (
        f"listed {sorted(found)}: H01 and U01 are due after their payer's last remittance, so nothing can say they went unanswered")
    assert "No remittance from Aetna is on file at all" in found["CB0000Z01C017"]["hint"]
    saved = json.loads(s.conn.execute("SELECT value FROM meta WHERE key='feed:payers_behind'").fetchone()[0])
    assert [(p["payer"], p["since"], p["claims"], p["billed_cents"]) for p in saved] == [
        ("Humana", "2026-09-25", 1, 22365), ("UnitedHealthcare", "2026-11-05", 1, 22365)], f"the held claims are not all counted: {saved}"
    copy = str(tmp_path / "serving.sqlite")
    cloud.build_serving(s.conn, copy)
    served = Store(copy)
    for where, conn in (("store", s.conn), ("dashboard's copy", served.conn)):
        check = _stopped(insights.data_health(conn, "2026-11-10", "2026-11-12"))
        print(where, check)
        assert check is not None and not check["ok"], f"nothing on the Data tab ({where}) says Humana's remittances stopped"
        assert check["title"] == "No remittance from Humana since 2026-09-25"
        assert check["n"] == 1 and check["cents"] == 22365
        assert "1 claim sent to Humana and due since then ($223.65 billed) is not listed as never answered" in check["detail"]
    served.close()
    # Humana's remittances come in again, through November 10, and still do not answer H01: it is listed, and the check clears
    # (U01 still waits for UnitedHealthcare's next file, which is not named)
    _add(s, tmp_path, [claim(810, svc("99214", "223.65", "117.60", "117.60", "CAS*CO*45*106.05~", dos="20261020"), dos="20261020")],
         "20261110", payer="HUMANA", payer_id="HUMNA")
    found = _chased(s, "t2")
    assert set(found) == {"CB0000H01C017", "CB0000H02C017", "CB0000A01C017", "CB0000Z01C017"}
    assert _stopped(insights.data_health(s.conn, "2026-11-10", "2026-11-12")) is None, "a payer between two files is named as stopped"
    s.close()
