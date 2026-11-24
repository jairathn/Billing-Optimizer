"""
Items listed although the service was settled elsewhere (plans/catchup_trace_2026-11-23.md, and the auditor's already-done
items on push 2's Work now list):
T-C3 and T-N3, a coordination-of-benefits denial (CARC 22, or the same payer's other answers that another payer covers the
claim first) stayed listed as never paid after that payer processed the service as secondary; T-N6, a denial stayed open
after the same payer answered the service again and put it on the patient's deductible, coinsurance or copay; T-N8, CO-151
with M86 (payment already made) on a resubmission of a service the bill already had paid was listed as a frequency denial.

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
from tests.test_features import NPI, claim, svc  # noqa: E402
from tests.test_remitwatch import remit  # noqa: E402

KINDS = ["file", "compact"]
DOS = "20260914"
MEDICAID = {"payer": "KENTUCKY MEDICAID", "payer_id": "KYMCD"}
ANTHEM = {"payer": "ANTHEM", "payer_id": "ANTHM"}
HUMANA = {"payer": "HUMANA", "payer_id": "HUMNA"}


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


def _clp(cid: str, status: str, lines: str, paid: str = "0.00", cfi: str = "12", charge: str = "223.65") -> str:
    """One claim of the bill `cid` names (its last four characters are the submission), answered with claim status
    `status` (CLP02: 1 as primary, 2 as secondary, 4 denied, 22 a reversal)."""
    sign = "-" if status == "22" else ""
    return (f"CLP*{cid}*{status}*{sign}{charge}*{sign}{paid}*0.00*{cfi}*PCN{cid[4:9]}{cid[-3:]}{status}*11~NM1*QC*1*SAMPLE*PAT****MI*M00000777~"
            f"NM1*82*2*TEST PROVIDER*****XX*{NPI}~DTM*232*{DOS}~" + lines)


def _line(paid: str, cas: str, allowed: str | None = None, code: str = "99214", charge: str = "223.65", mods: str = "",
          sign: str = "") -> str:
    out = svc(code, f"{sign}{charge}", f"{sign}{paid}" if float(paid) else paid, allowed, cas, dos=DOS, mods=mods)
    return out.replace("**1~", f"**{sign}1~", 1)


def _usual(cfi: str, allowed: str) -> list[str]:
    """25 visits paid at the payer's usual rate for 99214: its baseline, so that each denial has a size."""
    co = f"{223.65 - float(allowed):.2f}"
    return [claim(i, svc("99214", "223.65", allowed, allowed, f"CAS*CO*45*{co}~", dos=DOS), cfi=cfi, dos=DOS) for i in range(1, 26)]


def _items(s: Store, code: str = "") -> dict[str, dict]:
    """The denial and bundling items by claim id (one each), leaving out a code's items when `code` names one."""
    return {r["patient_control_number"]: dict(r) for r in s.query(
        "SELECT * FROM findings WHERE category IN ('denial','bundling') AND COALESCE(stale,0)=0") if not code or r["procedure_code"] != code}


def _start(s: Store, pcn: str) -> None:
    """A person marks the item Started (in progress), as the panel's status action records it."""
    fid = s.conn.execute("SELECT finding_id FROM findings WHERE patient_control_number=?", (pcn,)).fetchone()[0]
    s.conn.execute("UPDATE findings SET status='in_progress', status_changed_at='2026-10-20T15:00:00+00:00' WHERE finding_id=?", (fid,))
    s.conn.execute("""INSERT INTO finding_events (finding_id, at, kind, from_status, to_status, actor, note)
                      VALUES (?, '2026-10-20T15:00:00+00:00', 'manual', 'open', 'in_progress', 'biller', 'called the payer')""", (fid,))
    s.conn.commit()


# --------------------------------------------------------------------------- T-C3, T-N3: settled by the same payer as secondary

COB = "CAS*CO*22*223.65~LQ*HE*N36~"            # another payer covers this claim first: send it to the primary payer


def _second(paid: str, oa23: bool = True) -> str:
    """Medicaid's line as secondary: the primary payer's answer applied (OA-23) and what is left of its allowed $70.00 paid."""
    if not oa23:
        return _line("0.00", COB)
    return _line(paid, f"CAS*OA*23*{70 - float(paid):.2f}~CAS*CO*45*153.65~", "70.00")


@pytest.mark.parametrize("kind", KINDS)
def test_a_coordination_denial_the_same_payer_then_processed_as_secondary_is_closed_by_that_answer(tmp_path, kind):
    """Medicaid denies claims on October 1 because another payer covers them first, and processes them as secondary on
    November 10 (trace findings T-C3, T-N3: before, each stayed listed as never paid, its secondary answer counted only
    when it paid half of the expected amount):
    - S01: the whole claim denied (CLP02 4, CO-22 with N36). Medicaid processes it as secondary, the primary's answer
      applied (OA-23), paying nothing more. Closed by that answer, $0 recovered, and the item says the primary's remittance
      is not on file (no other payer's answer to the bill is).
    - S02: the same, and the secondary pays $23.52 (under half the expected $70.00): closed, $23.52 recovered.
    - S03: Medicaid's later answer as secondary denies it again for coordination (no OA-23, nothing paid): still listed.
    - S04: another payer (Humana) processes it as secondary: Medicaid's denial is not settled by it. Still listed.
    - S05: denied for authorization (CO-197), not for coordination: a secondary answer does not settle it. Still listed.
    - S06: denied CO-109 with N36 (not covered by this payer: the primary payer's answer is needed first), the payer's other
      answer that another payer covers the claim first; a biller had started it. Closed by the secondary answer.
    - S07: as S01, but the primary's (Anthem's) answer is on file (it put the visit to the deductible), and Medicaid paid
      $20.00 as secondary: closed, $20.00 recovered, and nothing says a remittance is missing.
    - S08: one line of a processed claim denied CO-22 (DENIED_OTHER); Medicaid then pays that line $23.52 as secondary:
      closed, $23.52 recovered.
    - S09: denied and processed as secondary before any analysis listed it: no item at all (a claim denial is listed only
      while it is unpaid; one never listed is not made to be closed).
    - S10: a visit and its G2211 denied for coordination; as secondary Medicaid applies the primary's answer to the visit
      and denies G2211 (CO-96). What is left is nothing to appeal (G2211 is never listed alone): closed by the answer, not
      dropped without a record.
    - S11: a visit and a destruction (17110) denied for coordination; as secondary Medicaid pays nothing more on the visit
      and $40.00 on 17110 (over half its usual $50.00, so the payment rules count 17110 paid). Closed by the answer, and
      all of Medicaid's payment since, $40.00, is recovered."""
    s = _store(tmp_path, kind)
    usual_17110 = [claim(26 + i, svc("17110", "177.56", "50.00", "50.00", "CAS*CO*45*127.56~", dos=DOS), cfi="MC", dos=DOS) for i in range(25)]
    cob_17110 = "CAS*CO*22*177.56~LQ*HE*N36~"
    first = [_clp(f"CB0000S0{i}C017", "4", _line("0.00", COB), cfi="MC") for i in (1, 2, 3, 4)]
    first += [_clp("CB0000S05C017", "4", _line("0.00", "CAS*CO*197*223.65~"), cfi="MC"),
              _clp("CB0000S06C017", "4", _line("0.00", "CAS*CO*109*223.65~LQ*HE*N36~"), cfi="MC"),
              _clp("CB0000S07C017", "4", _line("0.00", COB), cfi="MC"),
              _clp("CB0000S08C017", "1", _line("91.31", "CAS*CO*45*86.25~", "91.31", code="17110", charge="177.56")
                   + _line("0.00", COB, mods="25"), paid="91.31", cfi="MC"),
              _clp("CB0000S10C017", "4", _line("0.00", COB) + _line("0.00", "CAS*CO*22*16.00~LQ*HE*N36~", code="G2211", charge="16.00"), cfi="MC"),
              _clp("CB0000S11C017", "4", _line("0.00", COB) + _line("0.00", cob_17110, code="17110", charge="177.56"), cfi="MC")]
    _add(s, tmp_path, _usual("MC", "70.00") + usual_17110 + first, "20261001", **MEDICAID)
    _add(s, tmp_path, [_clp("CB0000S07C017", "1", _line("0.00", "CAS*PR*1*117.60~CAS*CO*45*106.05~", "117.60"))], "20261001", **ANTHEM)
    Detector(s, run_id="t1").run()
    rows = _items(s)
    listed = {pcn for pcn, r in rows.items() if r["status"] == "open"}
    print("listed on October 1:", sorted(listed))
    assert listed == {f"CB0000S0{i}C017" for i in range(1, 9)} | {"CB0000S10C017", "CB0000S11C017"}, f"listed {sorted(listed)}"
    _start(s, "CB0000S06C017")
    _add(s, tmp_path, [_clp("CB0000S01C017", "2", _second("0.00"), cfi="MC"),
                       _clp("CB0000S02C017", "2", _second("23.52"), paid="23.52", cfi="MC"),
                       _clp("CB0000S03C017", "2", _second("0.00", oa23=False), cfi="MC"),
                       _clp("CB0000S05C017", "2", _second("0.00"), cfi="MC"),
                       _clp("CB0000S06C017", "2", _second("0.00"), cfi="MC"),
                       _clp("CB0000S07C017", "2", _second("20.00"), paid="20.00", cfi="MC"),
                       _clp("CB0000S08C017", "2", _second("23.52").replace("HC:99214", "HC:99214:25", 1), paid="23.52", cfi="MC"),
                       _clp("CB0000S09C017", "4", _line("0.00", COB), cfi="MC"),
                       _clp("CB0000S10C017", "2", _second("0.00") + _line("0.00", "CAS*CO*96*16.00~LQ*HE*N216~", code="G2211", charge="16.00"),
                            cfi="MC"),
                       _clp("CB0000S11C017", "2", _second("0.00") + _line("40.00", "CAS*OA*23*10.00~CAS*CO*45*127.56~", "50.00", code="17110",
                                                                          charge="177.56"), paid="40.00", cfi="MC")], "20261110", **MEDICAID)
    _add(s, tmp_path, [_clp("CB0000S04C017", "2", _second("0.00"))], "20261110", **HUMANA)
    _add(s, tmp_path, [_clp("CB0000S09C017", "2", _second("0.00"), cfi="MC")], "20261112", **MEDICAID)
    Detector(s, run_id="t2").run()
    rows = _items(s)
    for pcn in sorted(rows):
        r = rows[pcn]
        print(pcn, r["signal"], r["status"], r["recovered_cents"], "|", r["auto_note"])
    closed = {pcn for pcn, r in rows.items() if r["status"] == "resolved"}
    assert closed == {"CB0000S01C017", "CB0000S02C017", "CB0000S06C017", "CB0000S07C017", "CB0000S08C017", "CB0000S10C017",
                      "CB0000S11C017"}, (
        f"closed {sorted(closed)}: S01, S02, S06, S07, S08, S10 and S11 were processed as secondary by the payer that denied them")
    assert json.loads(rows["CB0000S10C017"]["evidence"])["settled_by"]["codes"] == ["99214"]
    eleven = rows["CB0000S11C017"]
    assert eleven["recovered_cents"] == 4000 and json.loads(eleven["evidence"])["settled_by"]["paid_cents"] == 4000, (
        f"S11 recovered {eleven['recovered_cents']}: Medicaid paid $40.00 on 17110 since its denial")
    assert {pcn for pcn, r in rows.items() if r["status"] == "open"} == {"CB0000S03C017", "CB0000S04C017", "CB0000S05C017"}
    assert "CB0000S09C017" not in rows, "a claim settled before it was ever listed was made into an item"
    a = rows["CB0000S01C017"]
    assert a["auto_note"] == ("Medicaid KY processed this claim as secondary on 2026-11-10 (claim CB0000S01C017), applying the "
                              "primary payer's answer (OA-23) and paying nothing more: its denial as the primary payer is "
                              "settled. The primary's remittance is not on file: look it up on the payer's portal, or ask the "
                              "payer to send it again")
    assert a["recovered_cents"] == 0 and a["recovered_date"] == "2026-11-10" and a["shortfall_cents"] == 7000
    ev = json.loads(a["evidence"])["settled_by"]
    assert ev == {"how": "processed as secondary", "payer": "Medicaid KY", "date": "2026-11-10", "claim": "CB0000S01C017",
                  "paid_cents": 0, "codes": ["99214"], "primary_remittance": "not on file"}, ev
    assert rows["CB0000S02C017"]["recovered_cents"] == 2352
    assert rows["CB0000S02C017"]["auto_note"].startswith("Medicaid KY processed this claim as secondary on 2026-11-10 "
                                                          "(claim CB0000S02C017) and paid $23.52, its share:")
    assert rows["CB0000S06C017"]["prev_status"] == "in_progress", "the person's Started is not remembered under the closure"
    seven = rows["CB0000S07C017"]
    assert seven["recovered_cents"] == 2000 and "not on file" not in seven["auto_note"]
    assert json.loads(seven["evidence"])["settled_by"]["primary_remittance"] == "on file"
    line = rows["CB0000S08C017"]
    assert line["signal"] == "DENIED_OTHER" and line["recovered_cents"] == 2352
    assert line["auto_note"].startswith("Medicaid KY processed this service as secondary on 2026-11-10 (claim CB0000S08C017) "
                                        "and paid $23.52, its share:")
    assert all(r["work_state"] == "done" for pcn, r in rows.items() if pcn in closed)
    # Medicaid's remittance of November 10, with its secondary answer to S01, is withdrawn (superseded): S01 is open again
    s.conn.execute("""UPDATE transactions SET superseded=1 WHERE transaction_key IN (
                          SELECT transaction_key FROM claims WHERE patient_control_number='CB0000S01C017' AND claim_status_code='2')""")
    s.conn.commit()
    Detector(s, run_id="t3").run()
    assert _items(s)["CB0000S01C017"]["status"] == "open"
    s.close()


# --------------------------------------------------------------------------- T-N6: processed to the patient's share

def _again(cid: str, pr: str, allowed: str = "117.60") -> str:
    """The payer answers the corrected claim (the same bill) as primary: allowed, nothing paid, `pr` on the patient."""
    return _clp(cid, "1", _line("0.00", f"{pr}CAS*CO*45*106.05~", allowed))


@pytest.mark.parametrize("kind", KINDS)
def test_a_denial_the_same_payer_then_put_on_the_patients_share_is_closed(tmp_path, kind):
    """Anthem denies visits on October 1 for missing information (CO-16): lines P01 to P08 of processed claims, and whole
    claims C01 and C02 (CLP02 4). On November 10 it answers corrected claims (the same bills, submission C024):
    - P01: allowed $117.60, nothing paid, $117.60 to the patient's deductible (PR-1). Processed to the patient's share:
      closed, $0 recovered (trace finding T-N6: before, it stayed open, a payment being the only later answer counted).
    - P02, P03, P06, P07, P08: allowed, nothing paid, put on the patient as out of network (PR-242), coordination of
      benefits (PR-22), coverage ended (PR-27), information the patient did not send (PR-227), not the payer's insured
      (PR-31). Those are denials to work, not the patient's balance (calibration fix 7): still open.
    - P04: Humana, another payer, puts the visit to its deductible: a second payer may owe a first payer's deductible, so
      Anthem's denial is not settled by it. Still open.
    - P05: put to the deductible, and Anthem reverses that answer on November 12: still open.
    - C01: the whole claim, answered again to the deductible: closed (before, it left the list with no record).
    - C02: the same, and a biller had started it: closed (before, it stayed listed while being worked).
    - P09: denied by Anthem's Medicaid plan, answered to the deductible by its commercial plan under the same payer id: two
      plans of one company are two payers (the Medicaid plan may owe the deductible as secondary). Still open.
    - P10: one claim bills 11102 and 11102-59, both denied. The corrected claim puts 11102 on the deductible and pays
      11102-59: the 11102 denial is closed, the 11102-59 denial is not closed as the patient's share.
    - P12: one claim bills 11102 twice, both denied; the corrected claim pays one and puts the other on the deductible.
      Nothing tells which line was which: neither denial is closed as the patient's share."""
    s = _store(tmp_path, kind)
    denied = [_clp(f"CB0000P0{i}C017", "1", _line("0.00", "CAS*CO*16*223.65~LQ*HE*MA130~")) for i in range(1, 9)]
    denied += [_clp(f"CB0000C0{i}C017", "4", _line("0.00", "CAS*CO*16*223.65~LQ*HE*MA130~")) for i in (1, 2)]
    no_info = "CAS*CO*16*150.00~LQ*HE*MA130~"
    denied += [_clp("CB0000P09C017", "1", _line("0.00", "CAS*CO*16*223.65~LQ*HE*MA130~"), cfi="MC"),
               _clp("CB0000P10C017", "1", _bx("0.00", no_info) + _bx("0.00", no_info, mods="59"), charge="300.00"),
               _clp("CB0000P12C017", "1", _bx("0.00", no_info) + _bx("0.00", no_info), charge="300.00")]
    _add(s, tmp_path, _usual("12", "117.60") + denied, "20261001", **ANTHEM)
    Detector(s, run_id="t1").run()
    rows = _items(s, code="11102")
    assert {pcn for pcn, r in rows.items() if r["status"] == "open"} == {f"CB0000P0{i}C017" for i in range(1, 10)} | {"CB0000C01C017", "CB0000C02C017"}
    lines_11102 = "SELECT patient_control_number, modifiers, status, auto_note FROM findings WHERE procedure_code='11102' AND COALESCE(stale,0)=0"
    assert sorted((r["patient_control_number"], r["modifiers"] or "", r["status"]) for r in s.query(lines_11102)) == [
        ("CB0000P10C017", "", "open"), ("CB0000P10C017", "59", "open"), ("CB0000P12C017", "", "open"), ("CB0000P12C017", "", "open")]
    _start(s, "CB0000C02C017")
    _add(s, tmp_path, [_again("CB0000P01C024", "CAS*PR*1*117.60~"), _again("CB0000P02C024", "CAS*PR*242*117.60~"),
                       _again("CB0000P03C024", "CAS*PR*22*117.60~"), _again("CB0000P05C024", "CAS*PR*1*117.60~"),
                       _again("CB0000P06C024", "CAS*PR*27*117.60~"), _again("CB0000P07C024", "CAS*PR*227*117.60~"),
                       _again("CB0000P08C024", "CAS*PR*31*117.60~"),
                       _again("CB0000C01C024", "CAS*PR*1*117.60~"), _again("CB0000C02C024", "CAS*PR*1*100.00~CAS*PR*2*17.60~"),
                       _again("CB0000P09C024", "CAS*PR*1*117.60~"),
                       _clp("CB0000P10C024", "1", _bx("0.00", "CAS*PR*1*50.00~CAS*CO*45*100.00~", allowed="50.00")
                            + _bx("40.00", "CAS*PR*2*10.00~CAS*CO*45*100.00~", allowed="50.00", mods="59"), paid="40.00", charge="300.00"),
                       _clp("CB0000P12C024", "1", _bx("40.00", "CAS*PR*2*10.00~CAS*CO*45*100.00~", allowed="50.00")
                            + _bx("0.00", "CAS*PR*1*50.00~CAS*CO*45*100.00~", allowed="50.00"), paid="40.00", charge="300.00")],
         "20261110", **ANTHEM)
    _add(s, tmp_path, [_again("CB0000P04C024", "CAS*PR*1*117.60~")], "20261110", **HUMANA)
    _add(s, tmp_path, [_clp("CB0000P05C024", "22", _line("0.00", "CAS*PR*1*-117.60~CAS*CO*45*-106.05~", sign="-"))], "20261112", **ANTHEM)
    Detector(s, run_id="t2").run()
    rows = _items(s, code="11102")
    for pcn in sorted(rows):
        r = rows[pcn]
        print(pcn, r["signal"], r["status"], r["recovered_cents"], "|", r["auto_note"])
    closed = {pcn for pcn, r in rows.items() if r["status"] == "resolved"}
    assert closed == {"CB0000P01C017", "CB0000C01C017", "CB0000C02C017"}, (
        f"closed {sorted(closed)}: P01, C01 and C02 were put on the patient's share by the payer that denied them")
    for pcn in ("CB0000P02C017", "CB0000P03C017", "CB0000P04C017", "CB0000P05C017", "CB0000P07C017", "CB0000P08C017"):
        assert rows[pcn]["status"] == "open", f"{pcn} was closed: {rows[pcn]['auto_note']}"
    # P06: the payer's newest answer, coverage ended (PR-27), is the item to work (the archive's rule 8 lists it, and the
    # denial it replaces folds into it); it is never closed as the patient's share
    assert (rows.get("CB0000P06C017") or {}).get("status") != "resolved"
    assert (rows["CB0000P06C024"]["signal"], rows["CB0000P06C024"]["status"]) == ("DENIED_COVERAGE_DATES", "open")
    p = rows["CB0000P01C017"]
    assert p["auto_note"] == ("Processed to the patient's responsibility: Anthem / BCBS answered this service again on 2026-11-10 "
                              "(claim CB0000P01C024), allowed $117.60 and put $117.60 on the patient's deductible; nothing is owed "
                              "by the payer")
    assert p["recovered_cents"] == 0 and p["shortfall_cents"] == 11760 and p["work_state"] == "done"
    assert json.loads(p["evidence"])["settled_by"] == {"how": "processed to the patient's responsibility", "payer": "Anthem / BCBS",
                                                        "date": "2026-11-10", "claim": "CB0000P01C024", "allowed_cents": 11760,
                                                        "patient_cents": 11760, "codes": ["99214"]}
    assert rows["CB0000C01C017"]["auto_note"].startswith("Processed to the patient's responsibility: Anthem / BCBS answered this "
                                                         "claim again on 2026-11-10 (claim CB0000C01C024)")
    assert "deductible and coinsurance" in rows["CB0000C02C017"]["auto_note"]
    assert rows["CB0000C02C017"]["prev_status"] == "in_progress"
    assert rows["CB0000P09C017"]["status"] == "open", f"another plan's answer closed P09: {rows['CB0000P09C017']['auto_note']}"
    by_line = [(r["patient_control_number"], r["modifiers"] or "", r["status"], r["auto_note"] or "") for r in s.query(lines_11102)]
    print("lines of 11102:", by_line)
    to_patient = sorted((pcn, mods, st) for pcn, mods, st, note in by_line if note.startswith("Processed to the patient's responsibility"))
    assert to_patient == [("CB0000P10C017", "", "resolved")], (
        f"closed as the patient's share: {to_patient}; only P10's 11102 line was put on the patient by its own answer")
    s.close()


# --------------------------------------------------------------------------- T-N8: CO-151 with M86 on a paid service

def _bx(paid: str, cas: str, sign: str = "", allowed: str | None = None, mods: str = "") -> str:
    """One biopsy line (11102, $150.00); allowed what it paid unless `allowed` says otherwise."""
    if allowed is None:
        allowed = paid if float(paid) and not sign else None
    return _line(paid, cas, allowed, code="11102", charge="150.00", mods=mods, sign=sign)


@pytest.mark.parametrize("kind", KINDS)
def test_a_payment_already_made_denial_of_a_service_the_bill_paid_is_not_listed(tmp_path, kind):
    """Anthem pays a biopsy (11102) on October 1 on D01, D02 and D03. On November 10 it answers resubmissions of the same
    bills (C024) with CO-151 and remark M86 (payment already made for the same service):
    - D01: its October payment still stands. The denial repeats an answer the payer already paid: nothing is owed and no
      item is listed (trace finding T-N8: before, a frequency denial to appeal).
    - D02: Anthem took its October payment back (a reversal, CLP02 22) on November 10: the denial is listed.
    - D03: CO-151 without M86, a frequency denial as before: listed.
    - D04: CO-151 with M86 and nothing paid on the bill: listed.
    - D05: CO-151 with M86 on October 1 and the bill paid on November 10: closed by that later payment, as before."""
    s = _store(tmp_path, kind)
    paid = "CAS*CO*45*70.00~"
    m86 = "CAS*CO*151*150.00~LQ*HE*M86~"
    _add(s, tmp_path, _usual("12", "117.60") + [_clp(f"CB0000D0{i}C017", "1", _bx("80.00", paid), paid="80.00", charge="150.00") for i in (1, 2, 3)]
         + [_clp("CB0000D05C017", "1", _bx("0.00", m86), charge="150.00")], "20261001", **ANTHEM)
    _add(s, tmp_path, [_clp("CB0000D01C024", "1", _bx("0.00", m86), charge="150.00"),
                       _clp("CB0000D02C017", "22", _bx("80.00", "CAS*CO*45*-70.00~", sign="-"), paid="80.00", charge="150.00"),
                       _clp("CB0000D02C024", "1", _bx("0.00", m86), charge="150.00"),
                       _clp("CB0000D03C024", "1", _bx("0.00", "CAS*CO*151*150.00~"), charge="150.00"),
                       _clp("CB0000D04C024", "1", _bx("0.00", m86), charge="150.00"),
                       _clp("CB0000D05C024", "1", _bx("80.00", paid), paid="80.00", charge="150.00")], "20261110", **ANTHEM)
    Detector(s, run_id="t1").run()
    rows = {r["patient_control_number"]: dict(r) for r in s.query(
        "SELECT patient_control_number, signal, status, auto_note FROM findings WHERE procedure_code='11102' AND category='denial'")}
    for pcn in sorted(rows):
        print(pcn, rows[pcn]["signal"], rows[pcn]["status"], rows[pcn]["auto_note"])
    assert "CB0000D01C024" not in rows, f"a denial of a service the bill already paid is listed: {rows.get('CB0000D01C024')}"
    assert {pcn: (r["signal"], r["status"]) for pcn, r in rows.items()} == {
        "CB0000D02C024": ("DENIED_FREQUENCY", "open"), "CB0000D03C024": ("DENIED_FREQUENCY", "open"),
        "CB0000D04C024": ("DENIED_FREQUENCY", "open"), "CB0000D05C017": ("DENIED_FREQUENCY", "resolved")}
    s.close()
