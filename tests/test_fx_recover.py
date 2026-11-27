"""Fix wave F2, recoveries and closures (plans/briefs/2026-11-26_fixwave.md, section F2; plans/review_2026-11-26.md).

The review's and its verifiers' failing tests, ported (money-3, money-4, money-7, money-10, money-11, privacy-3,
privacy-4), then F2's own tests for the residuals and the needs routed to it. Every claim id, member number, name, payer,
amount and date here is invented. Each test runs on the server's store file and on the compact in-memory store the
serverless cycle analyses."""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch import selfcheck  # noqa: E402
from remitwatch.detect import Detector  # noqa: E402
from remitwatch.store import Store  # noqa: E402
from tests.review3.r3helpers import clm, sv, visit_denied_line, visit_paid  # noqa: E402
from tests.test_features import clean_visits  # noqa: E402
from tests.test_remitwatch import remit  # noqa: E402
from tests.test_settled_elsewhere import ANTHEM, COB, KINDS, MEDICAID, _add, _again, _clp, _line, _second, _store, _usual  # noqa: E402

KY = {"payer": "KY MEDICARE", "payer_id": "MCRKY"}
GAP = {"payer": "TESTGAP SUPPLEMENT", "payer_id": "TGAP1"}
MCD = {"payer": "KENTUCKY MEDICAID", "payer_id": "KYMCD"}
PINE = {"payer": "PINE RIDGE HEALTH PLAN", "payer_id": "PINER"}
MAPLE = {"payer": "MAPLE VALLEY HEALTH", "payer_id": "MAPLV"}


@pytest.fixture(params=KINDS)
def kind(request):
    return request.param


class World:
    """One test store of either kind, fed remittance files one at a time, analysed on a pinned day."""

    def __init__(self, tmp_path, kind: str):
        self.tmp, self.n = tmp_path, 0
        self.store = Store(str(tmp_path / "s.sqlite")) if kind == "file" else Store(":memory:", compact=True)

    def remit(self, claims: list[str], pay_date: str, plb: str = "", payer: str = "ANTHEM", payer_id: str = "ANTHM") -> None:
        self.n += 1
        p = self.tmp / f"r{self.n:04d}.RMT"
        p.write_bytes(remit("".join(claims), "100.00", payer=payer, payer_id=payer_id, seg_count=99, isa_ctrl=f"{self.n:09d}",
                            pay_date=pay_date, plb=plb))
        self.store.ingest_835_paths([str(p)])

    def run(self, run_id: str, today: str | None = None) -> Detector:
        det = Detector(self.store, run_id=run_id, today=today)
        det.run()
        return det

    def rows(self, where: str = "1=1") -> list[dict]:
        return [dict(r) for r in self.store.query(f"SELECT * FROM findings WHERE {where} ORDER BY finding_id")]

    def meta(self, key: str):
        row = self.store.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row and row[0] else None


def brief(rows: list[dict]) -> list[tuple]:
    return [(r["patient_control_number"], r["procedure_code"], r["signal"], r["payer_name"], r["status"], r.get("stale"),
             "line" if r["line_key"] else "claim", r["shortfall_cents"], r["recovered_cents"], (r.get("auto_note") or "")[:110])
            for r in rows]


# =========================================================================== money-3: one secondary payment, one recovery

@pytest.mark.parametrize("mc_paid,coins,mcd_paid", [("117.60", "0.00", "20.00"), ("94.08", "23.52", "20.00")])
def test_money3_one_secondary_payment_is_recovered_once(tmp_path, kind, mc_paid, coins, mcd_paid):
    """Medicare denies C017 (CO-16) and pays the corrected claim C024; Medicaid answers C024 as primary and denies it for
    coordination (CO-22), then processes it as secondary and pays $20.00. The $20.00 arrived once: the Medicare closure
    (with_second_shares) and the Medicaid closure as secondary (_close_by_answer) must not both count it."""
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99214", "223.65", "117.60"), "20260301", **KY)
    w.remit([visit_denied_line("CB0000300C017", carc="16")], "20260415", **KY)
    pr = f"CAS*PR*2*{coins}~" if float(coins) else ""
    w.remit([clm("CB0000300C024", "1", "223.65", mc_paid, sv("99214", "223.65", mc_paid, 1, f"CAS*CO*45*106.05~{pr}", allowed="117.60"),
                 payer_ctl="PC300B")], "20260520", **KY)
    w.remit([clm("CB0000300C024", "1", "223.65", "0.00", sv("99214", "223.65", "0.00", 1, "CAS*CO*22*223.65~"), payer_ctl="MD300A")],
            "20260610", **MCD)
    oa = f"{223.65 - float(mcd_paid) - 106.05:.2f}"
    w.remit([clm("CB0000300C024", "2", "223.65", mcd_paid,
                 sv("99214", "223.65", mcd_paid, 1, f"CAS*OA*23*{oa}~CAS*CO*45*106.05~", allowed="117.60"), payer_ctl="MD300B")],
            "20260701", **MCD)
    w.run("r1", today="2026-09-20")
    rows = w.rows("procedure_code='99214' AND patient_control_number LIKE 'CB0000300%'")
    print(brief(rows))
    arrived = int(round(float(mc_paid) * 100)) + int(round(float(mcd_paid) * 100))
    counted = sum(r["recovered_cents"] or 0 for r in rows)
    assert counted <= arrived, f"recovered ${counted/100:.2f} in all, but only ${arrived/100:.2f} arrived for the service"
    w.store.close()


# =========================================================================== money-4: the recovery nets every payer's takes

def _svc4(paid_code: str, charge: str, paid: str, cas: str, units: int = 1, allowed: str | None = None, billed: str = "99214") -> str:
    sub = f"*HC:{billed}" if paid_code != billed else ""
    return f"SVC*HC:{paid_code}*{charge}*{paid}**{units}{sub}~DTM*472*20260401~{cas}" + (f"AMT*B6*{allowed}~" if allowed else "")


@pytest.mark.parametrize("first_code", ["99213", "99214"])
def test_money4_the_recovery_is_what_both_payers_added_net(tmp_path, kind, first_code):
    """Medicare pays $64.00 (as a 99213 for the 99214 billed, or the 99214 short), the supplement $16.00; on appeal Medicare
    reverses and pays $94.08, the supplement reverses its $16.00 and pays $23.52. Both payers added $37.60 net since the
    item's answer: the recovery is never more."""
    pcn = "CB0000300C017"
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99214", "223.65", "117.60") + clean_visits(101, 25, "99213", "160.00", "80.00"), "20260301", **KY)
    w.remit([clm(pcn, "1", "223.65", "64.00", _svc4(first_code, "223.65", "64.00", "CAS*CO*45*143.65~CAS*PR*2*16.00~", allowed="80.00"),
                 payer_ctl="MC1")], "20260415", **KY)
    w.remit([clm(pcn, "2", "223.65", "16.00", _svc4(first_code, "223.65", "16.00", "CAS*OA*23*207.65~"), payer_ctl="GP1")], "20260425", **GAP)
    w.remit([clm(pcn, "22", "-223.65", "-64.00", _svc4(first_code, "-223.65", "-64.00", "CAS*CO*45*-143.65~CAS*PR*2*-16.00~", units=-1),
                 payer_ctl="MC1"),
             clm(pcn, "1", "223.65", "94.08", _svc4("99214", "223.65", "94.08", "CAS*CO*45*106.05~CAS*PR*2*23.52~", allowed="117.60"),
                 payer_ctl="MC2")], "20260701", **KY)
    w.remit([clm(pcn, "22", "-223.65", "-16.00", _svc4(first_code, "-223.65", "-16.00", "CAS*OA*23*-207.65~", units=-1), payer_ctl="GP1"),
             clm(pcn, "2", "223.65", "23.52", _svc4("99214", "223.65", "23.52", "CAS*OA*23*200.13~"), payer_ctl="GP2")], "20260710", **GAP)
    w.run("r1", today="2026-08-01")
    rows = w.rows(f"patient_control_number='{pcn}'")
    print(first_code, brief(rows))
    med = [r for r in rows if r["payer_name"] == "KY MEDICARE" and r["category"] in ("downcoding", "underpayment")]
    assert med and med[0]["status"] == "resolved", brief(rows)
    net = (9408 - 6400) + (2352 - 1600)
    got = sum(r["recovered_cents"] or 0 for r in rows)
    assert got <= net, f"recovered ${got/100:.2f} recorded; the payers added ${net/100:.2f} net since the item's answer"
    w.store.close()


# =========================================================================== money-7: a payment of another part closes nothing

def _part(pcn: str, mods: str, paid: str, ctl: str, who: str = "ELM*BO****MI*W55500302") -> str:
    cas = f"CAS*CO*45*{150 - float(paid):.2f}~" if float(paid) > 0 else "CAS*CO*16*150.00~"
    return clm(pcn, "1", "150.00", paid, sv("88305", "150.00", paid, 1, cas, allowed=paid if float(paid) > 0 else None, mods=mods),
               who=who, payer_ctl=ctl)


def _usual_parts(w: World) -> None:
    w.remit([_part(f"CB{1000 + i:07d}C017", "26", "40.00", f"PCU{i}", who=f"P{i:03d}*PAT****MI*W1{i:07d}") for i in range(30)]
            + [_part(f"CB{2000 + i:07d}C017", "", "70.00", f"PCG{i}", who=f"Q{i:03d}*PAT****MI*W2{i:07d}") for i in range(30)], "20260301")


@pytest.mark.parametrize("denied_mods,paid_pcn", [("26", "CB0000300C024"), ("", "CB0000300C024"), ("26", "CB0000301C017")])
def test_money7_a_payment_for_another_part_does_not_close_the_denial(tmp_path, kind, denied_mods, paid_pcn):
    """C017's 88305-26 (or the whole 88305) is denied CO-16; the corrected claim C024 of the same bill (or another bill)
    bills only the 88305-TC, paid $30.00. Nobody paid the reading: the denial stays open (T-C1's rule: a 26 or TC denial
    closes only on its part or the whole service; a whole service only on a whole payment or both parts)."""
    w = World(tmp_path, kind)
    _usual_parts(w)
    w.remit([_part("CB0000300C017", denied_mods, "0", "PC300A")], "20260415")
    w.remit([_part(paid_pcn, "TC", "30.00", "PC300B")], "20260520")
    w.run("r1", today="2026-06-20")
    r = w.rows("patient_control_number='CB0000300C017' AND procedure_code='88305'")
    print(denied_mods or "whole", paid_pcn, brief(r))
    assert r and r[0]["status"] == "open", f"the {denied_mods or 'whole'} denial was closed by a TC payment: {r[0]['auto_note'] if r else None}"
    w.store.close()


def test_money7_control_the_same_part_paid_on_the_corrected_claim_closes_the_denial(tmp_path, kind):
    """The control: the corrected claim bills the 26 itself and is paid. The denial closes, $40.00 recovered."""
    w = World(tmp_path, kind)
    _usual_parts(w)
    w.remit([_part("CB0000300C017", "26", "0", "PC300A")], "20260415")
    w.remit([_part("CB0000300C024", "26", "40.00", "PC300B")], "20260520")
    w.run("r1", today="2026-06-20")
    r = w.rows("patient_control_number='CB0000300C017' AND procedure_code='88305'")
    print(brief(r))
    assert r and r[0]["status"] == "resolved" and r[0]["recovered_cents"] == 4000, brief(r)
    w.store.close()


# =========================================================================== money-10: interest is no claim-level payment

def _claim10(interest: str | None) -> str:
    lines = (sv("99214", "223.65", "117.60", 1, "CAS*CO*45*106.05~", allowed="117.60")
             + sv("17110", "177.56", "0.00", 1, "CAS*CO*45*177.56~"))
    paid = f"{117.60 + float(interest or 0):.2f}"
    amt = f"AMT*I*{interest}~" if interest else ""
    return clm("CB0000500C017", "1", "401.21", paid, amt + lines, payer_ctl="PC500")


@pytest.mark.parametrize("interest", [None, "2.15"])
def test_money10_a_whole_charge_co45_line_is_listed_whatever_interest_the_claim_carries(tmp_path, kind, interest):
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99214", "223.65", "117.60") + clean_visits(101, 25, "17110", "177.56", "50.00"), "20260301")
    w.remit([_claim10(interest)], "20260520")
    w.run("r1", today="2026-06-20")
    got = [(r["signal"], r["shortfall_cents"]) for r in w.rows("patient_control_number='CB0000500C017' AND procedure_code='17110'")]
    print(interest, got)
    assert got and got[0][0] == "DENIED_OTHER", (f"the 17110 the payer took wholly as CO-45 is on no item when the claim carries "
                                                 f"${interest or '0.00'} of interest: {got}")
    w.store.close()


# =========================================================================== money-11: one denial delivered twice, one item

def test_money11_a_denial_delivered_twice_the_same_day_is_one_item(tmp_path, kind):
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99214", "223.65", "117.60"), "20260301")
    w.remit([visit_denied_line("CB0000300C017", carc="16", payer_ctl="PC300A")], "20260415")
    w.remit([visit_denied_line("CB0000300C017", carc="16", payer_ctl="PC300A")], "20260415")      # the same answer, another check
    w.run("r1", today="2026-06-20")
    rows = w.rows("patient_control_number='CB0000300C017' AND status='open' AND COALESCE(stale,0)=0")
    print([(r["signal"], r["shortfall_cents"], r["work_state"]) for r in rows])
    listed = [r for r in rows if r.get("work_state") not in ("done",)]
    assert len(listed) == 1, (f"one denial delivered twice is {len(listed)} items, ${sum(r['shortfall_cents'] or 0 for r in listed)/100:.2f} "
                              "listed for one $117.60 service")
    w.store.close()


# =========================================================================== privacy-3: a $0 closure by the payer's answer has proof rows

def _auto_closures(s) -> dict[str, dict]:
    return {r["patient_control_number"]: dict(r) for r in s.query(
        "SELECT finding_id, patient_control_number, status, auto_note, recovered_cents, evidence FROM findings "
        "WHERE status='resolved' AND auto_note IS NOT NULL AND COALESCE(stale,0)=0")}


def test_privacy3_a_closure_by_the_secondary_answer_with_nothing_paid_carries_proof(tmp_path, kind):
    """Medicaid denies a claim for coordination (CO-22) on October 1, then processes it as secondary on November 10, the
    primary's answer applied (OA-23), paying nothing more. The run closes the item ($0 recovered); the closure_proof
    self-check must find its proof."""
    s = _store(tmp_path, kind)
    _add(s, tmp_path, _usual("MC", "70.00") + [_clp("CB0000S01C017", "4", _line("0.00", COB), cfi="MC")], "20261001", **MEDICAID)
    Detector(s, run_id="t1").run()
    _add(s, tmp_path, [_clp("CB0000S01C017", "2", _second("0.00"), cfi="MC")], "20261110", **MEDICAID)
    Detector(s, run_id="t2").run()
    closed = _auto_closures(s)
    assert "CB0000S01C017" in closed, "the scenario no longer closes the item"
    ev = json.loads(closed["CB0000S01C017"]["evidence"])
    res = selfcheck.check_closure_proof(s.conn, "2026-11-20")
    print("closure_proof:", res["status"], res["n"], res["text"])
    assert selfcheck.has_proof(ev), "an automatic closure with no proof rows: closure_proof (critical, hold) fails on it"
    assert res["status"] == "ok", res["text"]
    s.close()


def test_privacy3_a_closure_as_the_patients_share_carries_proof(tmp_path, kind):
    """Anthem denies a visit for missing information (CO-16), then answers the corrected claim: allowed $117.60, nothing
    paid, all of it on the patient's deductible (PR-1). The run closes the denial ($0 recovered, T-N6)."""
    s = _store(tmp_path, kind)
    _add(s, tmp_path, _usual("12", "117.60") + [_clp("CB0000P01C017", "1", _line("0.00", "CAS*CO*16*223.65~LQ*HE*MA130~"))],
         "20261001", **ANTHEM)
    Detector(s, run_id="t1").run()
    _add(s, tmp_path, [_again("CB0000P01C024", "CAS*PR*1*117.60~")], "20261110", **ANTHEM)
    Detector(s, run_id="t2").run()
    closed = _auto_closures(s)
    assert "CB0000P01C017" in closed, "the scenario no longer closes the item"
    ev = json.loads(closed["CB0000P01C017"]["evidence"])
    res = selfcheck.check_closure_proof(s.conn, "2026-11-20")
    print("closure_proof:", res["status"], res["n"], res["text"])
    assert selfcheck.has_proof(ev), "an automatic closure with no proof rows: closure_proof (critical, hold) fails on it"
    assert res["status"] == "ok", res["text"]
    s.close()


def _p3_scenario(s, tmp_path, which: str) -> str:
    if which == "secondary":
        _add(s, tmp_path, _usual("MC", "70.00") + [_clp("CB0000V71C017", "4", _line("0.00", COB), cfi="MC")], "20261002", **MEDICAID)
        return "CB0000V71C017"
    _add(s, tmp_path, _usual("12", "117.60") + [_clp("CB0000V72C017", "1", _line("0.00", "CAS*CO*16*223.65~LQ*HE*MA130~"))],
         "20261002", **ANTHEM)
    return "CB0000V72C017"


def _p3_settle(s, tmp_path, which: str) -> None:
    if which == "secondary":
        _add(s, tmp_path, [_clp("CB0000V71C017", "2", _second("0.00"), cfi="MC")], "20261112", **MEDICAID)
    else:
        _add(s, tmp_path, [_clp("CB0000V72C024", "1", _line("0.00", "CAS*PR*2*96.40~CAS*CO*45*127.25~", "96.40"))], "20261112", **ANTHEM)


def _p3_run(s, rid: str, today: str) -> dict:
    det = Detector(s, run_id=rid, today=today)
    det.run()
    return det.selfcheck or {}


@pytest.mark.parametrize("which", ["secondary", "to_patient"])
def test_privacy3_a_run_that_closes_a_denial_its_payer_settled_with_nothing_owed_is_not_held(tmp_path, kind, which):
    """The verifier's test: three analyses with the self-check wired as the hook wires it; the run that makes the $0
    closure is not held for closure_proof, and the closure carries proof rows."""
    s = _store(tmp_path, kind)
    cid = _p3_scenario(s, tmp_path, which)
    _p3_run(s, "v1", "2026-11-05")
    _p3_run(s, "v2", "2026-11-06")
    _p3_settle(s, tmp_path, which)
    r3 = _p3_run(s, "v3", "2026-11-14")
    row = s.conn.execute("SELECT status, recovered_cents, evidence FROM findings WHERE patient_control_number=? AND COALESCE(stale,0)=0",
                         (cid,)).fetchone()
    cp = next((c for c in r3.get("checks") or [] if c.get("id") == "closure_proof"), {})
    print(which, "held:", r3.get("held"), r3.get("hold_checks"), "closure_proof:", cp.get("status"), cp.get("n"), tuple(row[:2]) if row else None)
    assert row and row[0] == "resolved" and row[1] == 0, "the run closes the item, $0 recovered"
    assert "closure_proof" not in (r3.get("hold_checks") or []), "the run was held in safe mode by closure_proof"
    assert cp.get("status") == "ok", cp.get("text")
    s.close()


# =========================================================================== privacy-4: a write-off on a code the payer never paid has an owner

def _maple_history(w: World) -> None:
    w.remit([clm("CB0000710C001", "1", "146.00", "61.20", sv("99213", "146.00", "61.20", 1, "CAS*CO*45*84.80~", dos="20260302",
                                                              allowed="61.20"), payer_ctl="MV0", dos="20260302")], "20260320", **MAPLE)


def test_privacy4_a_whole_charge_written_off_for_a_code_the_payer_never_paid_is_listed_or_counted(tmp_path, kind):
    """The fixer's own scenario: a plan that never allowed or paid 11401 takes a $250.00 charge entirely as CO-45 with
    nothing allowed. It is listed (an item a person can decide on) or counted as money given up without a decision."""
    w = World(tmp_path, kind)
    w.remit([clm("CB0000500C001", "1", "146.00", "60.00", sv("99213", "146.00", "60.00", 1, "CAS*CO*45*86.00~", dos="20260210", allowed="60.00"),
                 payer_ctl="PR0", dos="20260210")], "20260301", **PINE)
    w.remit([clm("CB0000500C017", "1", "250.00", "0.00", sv("11401", "250.00", "0.00", 1, "CAS*CO*45*250.00~"), payer_ctl="PR1")],
            "20260415", **PINE)
    det = w.run("r1")
    items = w.rows("procedure_code='11401' AND COALESCE(stale,0)=0")
    gu = selfcheck.given_up(w.store.conn, today="2026-05-01", save=False)
    print("co45_payer_has_no_rate:", det.ema_counts.get("co45_payer_has_no_rate"), "| items for 11401:", len(items),
          "| given up without a decision:", gu.get("cents"), gu.get("by_payer"))
    assert items or (gu.get("cents") or 0) >= 25000, "the payer wrote off the whole $250.00 charge of 11401: no item, no figure"
    w.store.close()


def test_privacy4_a_whole_charge_co45_on_a_code_the_payer_never_paid_is_listed(tmp_path, kind):
    """The verifier's scenario: the plan takes the whole $212.00 of a 17110 as CO-45, nothing allowed or paid, and has
    never allowed the code for anyone. It is an item a person can decide on, its dollars counted."""
    w = World(tmp_path, kind)
    _maple_history(w)
    w.remit([clm("CB0000720C017", "1", "212.00", "0.00", sv("17110", "212.00", "0.00", 1, "CAS*CO*45*212.00~", dos="20260407"),
                 payer_ctl="MV1", dos="20260407")], "20260428", **MAPLE)
    w.run("v1", today="2026-05-01")
    items = w.rows("procedure_code='17110' AND COALESCE(stale,0)=0")
    print([(r["signal"], r["status"], r["shortfall_cents"], r["work_state"]) for r in items])
    assert items, "the payer took the whole $212.00 of 17110 as CO-45 with nothing allowed: no item lists it"
    w.store.close()


def test_privacy4_control_the_same_whole_charge_co45_is_listed_when_the_payer_allowed_the_code_before(tmp_path, kind):
    w = World(tmp_path, kind)
    _maple_history(w)
    w.remit([clm("CB0000730C001", "1", "212.00", "98.40", sv("17110", "212.00", "98.40", 1, "CAS*CO*45*113.60~", dos="20260303",
                                                           allowed="98.40"), payer_ctl="MV2", dos="20260303")], "20260321", **MAPLE)
    w.remit([clm("CB0000720C017", "1", "212.00", "0.00", sv("17110", "212.00", "0.00", 1, "CAS*CO*45*212.00~", dos="20260407"),
                 payer_ctl="MV1", dos="20260407")], "20260428", **MAPLE)
    w.run("v1", today="2026-05-01")
    items = [r for r in w.rows("procedure_code='17110' AND COALESCE(stale,0)=0") if r["patient_control_number"] == "CB0000720C017"]
    print([(r["signal"], r["status"], r["shortfall_cents"]) for r in items])
    assert items, "with a rate on file the whole-charge CO-45 is a denial and is listed"
    w.store.close()


def test_privacy4_a_code_written_with_its_modifiers_in_the_code_field_is_not_a_code_the_payer_never_paid(tmp_path, kind):
    """A payer that writes the modifiers into the code ('17263 76') has 'never paid' it by the raw code, though it paid
    17263 itself before: the whole-charge CO-45 is a denial to list."""
    w = World(tmp_path, kind)
    _maple_history(w)
    w.remit([clm("CB0000740C001", "1", "310.00", "141.30", sv("17263", "310.00", "141.30", 1, "CAS*CO*45*168.70~", dos="20260304",
                                                              allowed="141.30"), payer_ctl="MV3", dos="20260304")], "20260322", **MAPLE)
    w.remit([clm("CB0000750C017", "1", "310.00", "0.00", sv("17263 76", "310.00", "0.00", 1, "CAS*CO*45*310.00~", dos="20260408"),
                 payer_ctl="MV4", dos="20260408")], "20260429", **MAPLE)
    w.run("v1", today="2026-05-01")
    items = [r for r in w.rows("COALESCE(stale,0)=0") if r["patient_control_number"] == "CB0000750C017"]
    print([(r["signal"], r["status"]) for r in items])
    assert items, "the payer paid 17263 before: its whole-charge CO-45 on '17263 76' is a denial"
    w.store.close()
