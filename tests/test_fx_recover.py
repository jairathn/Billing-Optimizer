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
    # under one payer id the second delivery makes no item at all (the detector passes it by as a copy of the answer, and
    # the self-check's vanished count knows it): no listing of it is folded into the item either
    assert len(w.rows("patient_control_number='CB0000300C017'")) == 1, [(r["signal"], r["stale"]) for r in w.rows()]
    assert not json.loads(listed[0]["evidence"]).get("folded"), json.loads(listed[0]["evidence"]).get("folded")
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
    assert not json.loads(items[0]["evidence"]).get("payer_has_no_rate"), "17263 was paid before: the payer has a rate for it"
    w.store.close()


def _proof_rows(s, pcn: str) -> tuple[dict, list[dict]]:
    r = s.conn.execute("SELECT evidence FROM findings WHERE patient_control_number=? AND COALESCE(stale,0)=0", (pcn,)).fetchone()
    ev = json.loads(r[0]) if r and r[0] else {}
    return ev, list((ev.get("paid_later") or {}).get("proof") or [])


def test_privacy3_the_patients_share_closure_carries_the_answer_lines_reconciled_to_the_cent(tmp_path, kind):
    """The closure as the patient's share names the settling answer line: dated, on its claim and trace, its code,
    charge, allowed, paid and every adjustment by group and reason; it adds up (charge = paid + adjustments; allowed =
    paid + the patient's share), and has_proof finds the closure proven by it."""
    s = _store(tmp_path, kind)
    _add(s, tmp_path, _usual("12", "117.60") + [_clp("CB0000P01C017", "1", _line("0.00", "CAS*CO*16*223.65~LQ*HE*MA130~"))],
         "20261001", **ANTHEM)
    Detector(s, run_id="t1").run()
    _add(s, tmp_path, [_again("CB0000P01C024", "CAS*PR*1*117.60~")], "20261110", **ANTHEM)
    Detector(s, run_id="t2").run()
    ev, rows = _proof_rows(s, "CB0000P01C017")
    print(rows)
    assert ev.get("settled_by", {}).get("how") == "processed to the patient's responsibility"
    assert len(rows) == 1
    row = rows[0]
    assert (row["date"], row["claim"], row["code"], row["kind"]) == ("2026-11-10", "CB0000P01C024", "99214", "to_patient")
    assert row["trace"] and row["charge_cents"] == 22365 and row["allowed_cents"] == 11760 and row["amount_cents"] == 0
    assert sorted((a["group"], a["carc"], a["amount_cents"]) for a in row["adjustments"]) == [("CO", "45", 10605), ("PR", "1", 11760)]
    assert selfcheck.answer_reconciles(row) and selfcheck.has_proof(ev)
    s.close()


def test_privacy3_the_secondary_closure_with_nothing_paid_carries_the_answer_lines(tmp_path, kind):
    """Medicaid's answer as secondary (OA-23 the primary's payment applied, CO-45 its cut, nothing paid) is the proof:
    charge $223.65 = paid $0 + OA-23 $70.00 + CO-45 $153.65."""
    s = _store(tmp_path, kind)
    _add(s, tmp_path, _usual("MC", "70.00") + [_clp("CB0000S01C017", "4", _line("0.00", COB), cfi="MC")], "20261001", **MEDICAID)
    Detector(s, run_id="t1").run()
    _add(s, tmp_path, [_clp("CB0000S01C017", "2", _second("0.00"), cfi="MC")], "20261110", **MEDICAID)
    Detector(s, run_id="t2").run()
    ev, rows = _proof_rows(s, "CB0000S01C017")
    print(rows)
    assert len(rows) == 1 and rows[0]["kind"] == "answer" and rows[0]["claim_status"] == "2"
    assert sorted((a["group"], a["carc"], a["amount_cents"]) for a in rows[0]["adjustments"]) == [("CO", "45", 15365), ("OA", "23", 7000)]
    assert selfcheck.answer_reconciles(rows[0]) and selfcheck.has_proof(ev)
    s.close()


def test_privacy3_settled_by_alone_or_with_rows_that_do_not_add_up_is_no_proof():
    """has_proof accepts the payer's settling answer only when its rows are dated, on a claim, and add up to the cent."""
    sb = {"payer": "Anthem / BCBS", "date": "2026-11-10", "claim": "CB0000P01C024", "how": "processed to the patient's responsibility"}
    good = {"date": "2026-11-10", "claim": "CB0000P01C024", "kind": "to_patient", "charge_cents": 22365, "allowed_cents": 11760,
            "amount_cents": 0, "adjustments": [{"group": "PR", "carc": "1", "amount_cents": 11760},
                                               {"group": "CO", "carc": "45", "amount_cents": 10605}]}
    later = {"payment_date": "2026-11-10", "claim": "CB0000P01C024", "paid_cents": 0}
    assert not selfcheck.has_proof({"settled_by": sb, "paid_later": later})
    assert selfcheck.has_proof({"settled_by": sb, "paid_later": dict(later, proof=[good])})
    short = dict(good, adjustments=[{"group": "PR", "carc": "1", "amount_cents": 5000}, {"group": "CO", "carc": "45", "amount_cents": 10605}])
    assert not selfcheck.has_proof({"settled_by": sb, "paid_later": dict(later, proof=[short])}), "$67.60 of the charge unexplained"
    wrong_allowed = dict(good, allowed_cents=12000)
    assert not selfcheck.has_proof({"settled_by": sb, "paid_later": dict(later, proof=[wrong_allowed])}), "allowed is not paid + the patient's share"
    undated = dict(good, date=None)
    assert not selfcheck.has_proof({"settled_by": sb, "paid_later": dict(later, proof=[undated])})
    claim_level = {"date": "2026-11-10", "claim": "CB0000S01C017", "kind": "answer", "charge_cents": 22365, "allowed_cents": 7000,
                   "amount_cents": 0, "adjustments": [{"group": "CO", "carc": "45", "amount_cents": 15365}],
                   "claim_adjustments": [{"group": "OA", "carc": "23", "amount_cents": 7000}]}
    assert selfcheck.answer_reconciles(claim_level), "a secondary's OA-23 put on its claim counts"


def test_privacy3_an_answer_that_does_not_add_up_closes_nothing(tmp_path, kind):
    """The corrected claim's answer puts $50.00 on the patient's deductible and $106.05 as CO-45 against a $223.65 charge:
    $67.60 of it is unexplained. Nothing is closed on it: the denial stays listed, the hint says what does not add up."""
    s = _store(tmp_path, kind)
    _add(s, tmp_path, _usual("12", "117.60") + [_clp("CB0000P09C017", "1", _line("0.00", "CAS*CO*16*223.65~LQ*HE*MA130~"))],
         "20261001", **ANTHEM)
    Detector(s, run_id="t1").run()
    _add(s, tmp_path, [_clp("CB0000P09C024", "1", _line("0.00", "CAS*PR*1*50.00~CAS*CO*45*106.05~", "117.60"))], "20261110", **ANTHEM)
    det = Detector(s, run_id="t2")
    det.run()
    r = s.conn.execute("SELECT status, evidence FROM findings WHERE patient_control_number='CB0000P09C017' AND COALESCE(stale,0)=0").fetchone()
    print(r[0] if r else None, det.ema_counts.get("settled_not_reconciled"))
    assert r and r[0] == "open", "an answer that does not add up to the cent closed the item"
    said = json.loads(r[1]).get("answer_not_reconciled") or {}
    assert det.ema_counts.get("settled_not_reconciled") == 1 and "does not add up" in (said.get("text") or "")
    assert said.get("proof") and not selfcheck.answer_reconciles(said["proof"][0])
    s.close()


def test_money11_a_denial_delivered_twice_the_same_day_under_two_payer_ids_folds_into_one_item(tmp_path, kind):
    """The same denial of C017 arrives the same day on two remittances of one payer under two of its payer ids (the
    detector's copies key on the payer id, so both deliveries make an item). Rule 7's fold keeps every line of the newest
    day as units of that answer; a second delivery of the very same answer is no unit: it folds into the first."""
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99214", "223.65", "117.60"), "20260301")
    w.remit([visit_denied_line("CB0000300C017", carc="16", payer_ctl="PC300A")], "20260415", payer="ANTHEM", payer_id="ANTHM")
    w.remit([visit_denied_line("CB0000300C017", carc="16", payer_ctl="PC300A")], "20260415", payer="ANTHEM", payer_id="ANTH2")
    w.run("r1", today="2026-06-20")
    rows = w.rows("patient_control_number='CB0000300C017' AND COALESCE(stale,0)=0")
    listed = [r for r in rows if r["status"] == "open" and r.get("work_state") not in ("done",)]
    print([(r["signal"], r["status"], r["work_state"], r["shortfall_cents"]) for r in rows])
    assert len(listed) == 1, f"one denial delivered twice is {len(listed)} items"
    w.store.close()


def test_money11_control_two_denied_units_of_one_answer_stay_two_items(tmp_path, kind):
    """The control: one remittance denies two lines of 17110 on the same claim (two lesions, two units of its answer).
    Both stay listed: lines of one remittance are never copies of each other."""
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "17110", "177.56", "50.00"), "20260301")
    two = clm("CB0000301C017", "1", "355.12", "0.00", sv("17110", "177.56", "0.00", 1, "CAS*CO*16*177.56~")
              + sv("17110", "177.56", "0.00", 1, "CAS*CO*16*177.56~", mods="59"), payer_ctl="PC301")
    w.remit([two], "20260415")
    w.run("r1", today="2026-06-20")
    rows = [r for r in w.rows("patient_control_number='CB0000301C017' AND COALESCE(stale,0)=0") if r["status"] == "open"]
    print([(r["signal"], r["modifiers"], r["shortfall_cents"]) for r in rows])
    assert len(rows) == 2, rows
    w.store.close()


def test_money7_a_second_payers_payment_of_another_part_is_no_share_of_the_recovery(tmp_path, kind):
    """C017's 88305-26 is denied (CO-16). The corrected claim C024 is paid $24.00 for the 26 by the primary ($6.00 left as
    coinsurance); the supplement then pays $12.00 for the TC (another part) and $6.00 for the 26. The reading recovered
    $30.00: the supplement's TC payment pays nothing of it (with_second_shares by part)."""
    w = World(tmp_path, kind)
    _usual_parts(w)
    w.remit([_part("CB0000300C017", "26", "0", "PC300A")], "20260415")
    w.remit([clm("CB0000300C024", "1", "150.00", "24.00", sv("88305", "150.00", "24.00", 1, "CAS*CO*45*120.00~CAS*PR*2*6.00~",
                                                            allowed="30.00", mods="26"), payer_ctl="PC300B")], "20260520")
    w.remit([clm("CB0000300C024", "2", "150.00", "12.00", sv("88305", "150.00", "12.00", 1, "CAS*OA*23*138.00~", mods="TC"),
                 payer_ctl="GP300A")], "20260525", **GAP)
    w.remit([clm("CB0000300C024", "2", "150.00", "6.00", sv("88305", "150.00", "6.00", 1, "CAS*OA*23*144.00~", mods="26"),
                 payer_ctl="GP300B")], "20260601", **GAP)
    w.run("r1", today="2026-06-20")
    r = w.rows("patient_control_number='CB0000300C017' AND procedure_code='88305' AND COALESCE(stale,0)=0")
    print(brief(r))
    assert r and r[0]["status"] == "resolved" and r[0]["recovered_cents"] == 3000, brief(r)
    w.store.close()


@pytest.mark.parametrize("mc_paid,mc_coins,gap_paid,gap_coins", [("84.00", "21.00", "21.00", "0")])
def test_money4_the_recovery_is_the_gain_not_the_cap(tmp_path, kind, mc_paid, mc_coins, gap_paid, gap_coins):
    """As money-4's case, but the appeal brings less than the shortfall: Medicare reverses its $64.00 and pays $84.00,
    the supplement reverses its $16.00 and pays $21.00. The service held $80.00 after the downcode (the supplement's
    share of it included) and holds $105.00 now: $25.00 recovered, though the shortfall is $37.60."""
    pcn = "CB0000300C017"
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99214", "223.65", "117.60") + clean_visits(101, 25, "99213", "160.00", "80.00"), "20260301", **KY)
    w.remit([clm(pcn, "1", "223.65", "64.00", _svc4("99213", "223.65", "64.00", "CAS*CO*45*143.65~CAS*PR*2*16.00~", allowed="80.00"),
                 payer_ctl="MC1")], "20260415", **KY)
    w.remit([clm(pcn, "2", "223.65", "16.00", _svc4("99213", "223.65", "16.00", "CAS*OA*23*207.65~"), payer_ctl="GP1")], "20260425", **GAP)
    w.remit([clm(pcn, "22", "-223.65", "-64.00", _svc4("99213", "-223.65", "-64.00", "CAS*CO*45*-143.65~CAS*PR*2*-16.00~", units=-1),
                 payer_ctl="MC1"),
             clm(pcn, "1", "223.65", mc_paid, _svc4("99214", "223.65", mc_paid, f"CAS*CO*45*118.65~CAS*PR*2*{mc_coins}~", allowed="105.00"),
                 payer_ctl="MC2")], "20260701", **KY)
    w.remit([clm(pcn, "22", "-223.65", "-16.00", _svc4("99213", "-223.65", "-16.00", "CAS*OA*23*-207.65~", units=-1), payer_ctl="GP1"),
             clm(pcn, "2", "223.65", gap_paid, _svc4("99214", "223.65", gap_paid, "CAS*OA*23*202.65~"), payer_ctl="GP2")], "20260710", **GAP)
    w.run("r1", today="2026-08-01")
    rows = w.rows(f"patient_control_number='{pcn}'")
    print(brief(rows))
    med = [r for r in rows if r["payer_name"] == "KY MEDICARE" and r["category"] == "downcoding"]
    assert med and med[0]["status"] == "resolved", brief(rows)
    assert med[0]["shortfall_cents"] > 2500, med[0]["shortfall_cents"]
    assert sum(r["recovered_cents"] or 0 for r in rows) == 2500, [(r["signal"], r["recovered_cents"]) for r in rows]
    w.store.close()


def test_money3_a_secondary_share_another_item_counted_is_not_counted_again_by_the_closure_as_secondary(tmp_path, kind):
    """Medicare denies C017 (CO-16) and pays the corrected claim C024 $94.08, $23.52 coinsurance; Medicaid denies C024 for
    coordination (CO-22), then processes it as secondary and pays the whole $23.52. The Medicare closure counts the
    $23.52 as part of the service's recovery (its line is used); Medicaid's closure as secondary must not count it again
    (one per-line ledger): $117.60 arrived, $117.60 recovered in all."""
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99214", "223.65", "117.60"), "20260301", **KY)
    w.remit([visit_denied_line("CB0000300C017", carc="16")], "20260415", **KY)
    w.remit([clm("CB0000300C024", "1", "223.65", "94.08", sv("99214", "223.65", "94.08", 1, "CAS*CO*45*106.05~CAS*PR*2*23.52~", allowed="117.60"),
                 payer_ctl="PC300B")], "20260520", **KY)
    w.remit([clm("CB0000300C024", "1", "223.65", "0.00", sv("99214", "223.65", "0.00", 1, "CAS*CO*22*223.65~"), payer_ctl="MD300A")],
            "20260610", **MCD)
    w.remit([clm("CB0000300C024", "2", "223.65", "23.52",
                 sv("99214", "223.65", "23.52", 1, "CAS*OA*23*94.08~CAS*CO*45*106.05~", allowed="117.60"), payer_ctl="MD300B")],
            "20260701", **MCD)
    w.run("r1", today="2026-09-20")
    rows = w.rows("procedure_code='99214' AND patient_control_number LIKE 'CB0000300%'")
    print(brief(rows))
    closed = {r["payer_name"]: r["recovered_cents"] for r in rows if r["status"] == "resolved"}
    assert "KENTUCKY MEDICAID" in closed, f"the scenario no longer closes Medicaid's denial as secondary: {brief(rows)}"
    assert sum(r["recovered_cents"] or 0 for r in rows) <= 9408 + 2352, f"recovered more than arrived: {closed}"
    w.store.close()


# =========================================================================== residual: a routine-rate review lists the lines still short

def _pattern_world(tmp_path, kind) -> World:
    """Anthem allows 99214 at $117.60 on 25 visits and at $100.00 on 8 (claims 101 to 108): one routine-rate review."""
    from tests.test_features import claim, svc
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99214", "223.65", "117.60") + clean_visits(101, 8, "99214", "223.65", "100.00"), "20260415")
    return w


def _repaid(w: World) -> None:
    """June 10: the payer reverses claim 101's $100.00 and pays it $117.60; claim 102's corrected claim (C024) is paid the
    $17.60 more."""
    from tests.test_features import NPI
    who = lambda i: f"NM1*QC*1*SAMPLE*PAT****MI*M{i:08d}~NM1*82*2*TEST PROVIDER*****XX*{NPI}~DTM*232*20260401~"   # noqa: E731
    rev = (f"CLP*CB0000101C017*22*-223.65*-100.00*0.00*12*PC101*11~{who(101)}"
           "SVC*HC:99214*-223.65*-100.00**-1~DTM*472*20260401~CAS*CO*45*-123.65~")
    again = (f"CLP*CB0000101C017*1*223.65*117.60*0.00*12*PC101B*11~{who(101)}"
             "SVC*HC:99214*223.65*117.60**1~DTM*472*20260401~CAS*CO*45*106.05~AMT*B6*117.60~")
    more = (f"CLP*CB0000102C024*1*223.65*17.60*0.00*12*PC102B*11~{who(102)}"
            "SVC*HC:99214*223.65*17.60**1~DTM*472*20260401~CAS*CO*45*106.05~CAS*OA*23*100.00~AMT*B6*117.60~")
    w.remit([rev, again, more], "20260610")


def test_pattern_a_routine_rate_review_leaves_out_the_claims_the_payer_repaid_since(tmp_path, kind):
    w = _pattern_world(tmp_path, kind)
    w.run("r1", today="2026-05-01")
    first = w.rows("signal='SECOND_RATE'")
    assert len(first) == 1 and json.loads(first[0]["evidence"])["lines"] == 8
    _repaid(w)
    w.run("r2", today="2026-07-01")
    rows = w.rows("signal='SECOND_RATE' AND COALESCE(stale,0)=0")
    assert len(rows) == 1, brief(rows)
    ev = json.loads(rows[0]["evidence"])
    print(ev["lines"], [c[0] for c in ev["claims"]], ev.get("left"))
    assert sorted(c[0] for c in ev["claims"]) == [f"CB{i:07d}C017" for i in range(103, 109)], ev["claims"]
    assert rows[0]["shortfall_cents"] == 6 * 1760, rows[0]["shortfall_cents"]
    assert sorted((x[0], x[2]) for x in ev.get("left") or []) == [("CB0000101C017", "taken back"), ("CB0000102C017", "paid more since")]
    w.store.close()


def test_pattern_a_worked_routine_rate_review_keeps_its_claims_but_those_the_payer_repaid(tmp_path, kind):
    """A person works the review (Started); the payer then repays two of its claims. The review keeps its other six claims
    and names the two that left; it is not reopened as new work."""
    w = _pattern_world(tmp_path, kind)
    w.run("r1", today="2026-05-01")
    fid = w.rows("signal='SECOND_RATE'")[0]["finding_id"]
    w.store.conn.execute("UPDATE findings SET status='in_progress', notes='asked the payer' WHERE finding_id=?", (fid,))
    w.store.conn.commit()
    _repaid(w)
    w.run("r2", today="2026-07-01")
    r = w.rows(f"finding_id='{fid}'")[0]
    ev = json.loads(r["evidence"])
    print(r["status"], ev["lines"], ev.get("left"))
    assert r["status"] == "in_progress" and ev["lines"] == 6 and r["shortfall_cents"] == 6 * 1760
    assert sorted(x[0] for x in ev.get("left") or []) == ["CB0000101C017", "CB0000102C017"]
    w.store.close()


@pytest.fixture
def results_env(monkeypatch):
    """The Results tab's own settings (tests/test_results.py's): a server, on the practice's US Eastern calendar."""
    from remitwatch import states
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    monkeypatch.setenv("REMITWATCH_TZ", "America/New_York")
    for k in ("VERCEL", "REMITWATCH_STATE_DIR", "REMITWATCH_EMA_REPORTS"):
        monkeypatch.delenv(k, raising=False)
    states._RESPONSE_CACHE.clear()
    states._RECOVERY_CACHE.clear()


def test_results_credit_a_recovery_on_a_routine_rate_reviews_claims_to_it(tmp_path, kind, results_env):
    """The review was flagged on May 1. In June the payer reverses claim 101's $100.00 and pays $117.60, and pays claim 102
    $17.60 more: $35.20 came back on the review's claims after its flag, and the Results tab counts it for the review."""
    from remitwatch import results
    w = _pattern_world(tmp_path, kind)
    w.run("r1", today="2026-05-01")
    fid = w.rows("signal='SECOND_RATE'")[0]["finding_id"]
    w.store.conn.execute("UPDATE findings SET first_seen='2026-05-01T12:00:00+00:00' WHERE finding_id=?", (fid,))
    w.store.conn.commit()
    results.refresh(w.store.conn, today="2026-05-02", force=True)
    _repaid(w)
    w.run("r2", today="2026-07-01")
    results.refresh(w.store.conn, today="2026-07-01", force=True)
    detail = (results._meta(w.store.conn, results.LISTS_KEY, {}) or {}).get("detail", {})
    got = detail.get(fid) or {}
    print({k: got.get(k) for k in ("returned", "taken_back", "members")}, [(e.get("date"), e.get("cents")) for e in got.get("events") or []])
    assert got.get("returned") == 3520, got
    w.store.close()


def _detail(w: World, fid: str) -> dict:
    from remitwatch import results
    return ((results._meta(w.store.conn, results.LISTS_KEY, {}) or {}).get("detail", {}) or {}).get(fid) or {}


def _flag_review(w: World, today: str) -> str:
    """The routine-rate review raised by the run of `today`, flagged that day, and Results' registry brought up to date."""
    from remitwatch import results
    fid = w.rows("signal='SECOND_RATE'")[0]["finding_id"]
    w.store.conn.execute("UPDATE findings SET first_seen=? WHERE finding_id=?", (f"{today}T12:00:00+00:00", fid))
    w.store.conn.commit()
    results.refresh(w.store.conn, today=today, force=True)
    return fid


def test_results_a_claim_that_joined_the_review_later_counts_what_the_payer_paid_more(tmp_path, kind, results_env):
    """The review is flagged on May 1 on claims 101 to 108. Claim 109 is paid $100.00 on June 1 and joins it. On July 1
    the payer reverses that $100.00 and pays $117.60: $17.60 came back for the review, not $117.60 (claim 109's own
    payment came before that claim was flagged, so its takeback is money paid before the flag)."""
    from remitwatch import results
    from tests.test_features import NPI
    w = _pattern_world(tmp_path, kind)
    w.run("r1", today="2026-05-01")
    fid = _flag_review(w, "2026-05-01")
    w.remit(clean_visits(109, 1, "99214", "223.65", "100.00"), "20260601")
    w.run("r2", today="2026-06-02")
    rows = w.rows("signal='SECOND_RATE' AND COALESCE(stale,0)=0")
    assert [r["finding_id"] for r in rows] == [fid] and "CB0000109C017" in [c[0] for c in json.loads(rows[0]["evidence"])["claims"]]
    results.refresh(w.store.conn, today="2026-06-02", force=True)
    assert not _detail(w, fid).get("returned"), _detail(w, fid)       # claim 109's own payment is no money back
    who = f"NM1*QC*1*SAMPLE*PAT****MI*M{109:08d}~NM1*82*2*TEST PROVIDER*****XX*{NPI}~DTM*232*20260401~"
    w.remit([f"CLP*CB0000109C017*22*-223.65*-100.00*0.00*12*PC109*11~{who}SVC*HC:99214*-223.65*-100.00**-1~DTM*472*20260401~"
             "CAS*CO*45*-123.65~",
             f"CLP*CB0000109C017*1*223.65*117.60*0.00*12*PC109B*11~{who}SVC*HC:99214*223.65*117.60**1~DTM*472*20260401~"
             "CAS*CO*45*106.05~AMT*B6*117.60~"], "20260701")
    w.run("r3", today="2026-07-02")
    results.refresh(w.store.conn, today="2026-07-02", force=True)
    got = _detail(w, fid)
    assert (got.get("returned"), got.get("taken_back")) == (1760, 0), got
    w.store.close()


def test_results_a_second_payers_money_on_a_reviews_claim_is_not_the_reviews(tmp_path, kind, results_env):
    """After the review's flag a second payer pays its share of claim 103 ($23.52). The review asks Anthem for its rate:
    that money is no answer to it, and Results counts nothing for the review."""
    from remitwatch import results
    from tests.test_features import claim, svc
    w = _pattern_world(tmp_path, kind)
    w.run("r1", today="2026-05-01")
    fid = _flag_review(w, "2026-05-01")
    w.remit([claim(103, svc("99214", "223.65", "23.52", None, "CAS*OA*23*100.00~CAS*CO*45*100.13~"), status="2")], "20260610", **MCD)
    w.run("r2", today="2026-07-01")
    results.refresh(w.store.conn, today="2026-07-01", force=True)
    assert not _detail(w, fid).get("returned"), _detail(w, fid)
    w.store.close()


def test_results_the_registry_keeps_every_claim_a_review_listed():
    """A routine-rate review's registered item keeps every claim it listed (a claim the payer repaid leaves the review, and
    its money still counts). On the first registration the claims that had left it are taken in too; after that a claim
    that came and went between two runs, never listed, is not."""
    from remitwatch import results
    f = {"finding_id": "f1", "signal": "SECOND_RATE", "category": "underpayment", "procedure_code": "99214",
         "payer_group": "Anthem / BCBS",
         "evidence": json.dumps({"claims": [["CB1", "2026-04-01", 10000, 1760], ["CB2", "2026-04-01", 10000, 1760]],
                                 "line_keys": ["k1", "k2"], "left": [["CB3", "2026-04-01", "paid more since", 11760, "k3"]]})}
    first = json.loads(results._item_json(f))
    ever = json.loads(first["evidence"])["ever"]
    assert sorted((c[0], c[2], c[3]) for c in ever) == [("CB1", "99214", "k1"), ("CB2", "99214", "k2"), ("CB3", "99214", "k3")], ever
    again = dict(f, evidence=json.dumps({"claims": [["CB2", "2026-04-01", 10000, 1760]], "line_keys": ["k2"],
                                         "left": [["CB1", "2026-04-01", "taken back", 0, "k1"],
                                                  ["CB4", "2026-04-01", "paid more since", 11760, "k4"]]}))
    second = json.loads(results._item_json(again, json.dumps(first)))
    assert sorted(c[0] for c in json.loads(second["evidence"])["ever"]) == ["CB1", "CB2", "CB3"], second


def test_results_credit_a_repricing_request_with_the_claims_the_payer_repriced(tmp_path, kind, results_env):
    """A repricing request (OLD_FEE_SCHEDULE) for ten January visits paid on the 2025 schedule is flagged on April 15. On
    May 1 the payer reprices four of them (each $76.64 reversed and $82.36 paid): $22.88 came back on the request's
    claims, counted once (each repriced claim is also closed on its own line)."""
    from remitwatch import results
    from tests.test_plan_rates import DOC, DR, many, reversal, types
    from tests.test_sizes_and_rates import World as RWorld
    w = RWorld(tmp_path, kind)
    types(w, (DOC, "127.80", DR, 1))
    payer = dict(payer="UNITEDHEALTHCARE", payer_id="UHCTEST")
    w.remit(many(100, 25, "99213", "156.66", "76.64", cfi="16", dos="20250610"), "20250701", **payer)
    w.remit(many(200, 25, "99213", "156.66", "82.36", cfi="16", dos="20260105"), "20260310", **payer)
    w.remit(many(900, 10, "99213", "156.66", "76.64", cfi="16", dos="20260112"), "20260215", **payer)
    w.run(today="2026-04-15")
    fid = w.one("signal='OLD_FEE_SCHEDULE'")["finding_id"]
    w.store.conn.execute("UPDATE findings SET first_seen='2026-04-15T12:00:00+00:00' WHERE finding_id=?", (fid,))
    w.store.conn.commit()
    results.refresh(w.store.conn, today="2026-04-15", force=True)
    w.remit([reversal(900 + k, "99213", "156.66", "76.64", cfi="16", dos="20260112") for k in range(4)]
            + many(900, 4, "99213", "156.66", "82.36", cfi="16", dos="20260112"), "20260501", **payer)
    w.run(today="2026-06-15")
    results.refresh(w.store.conn, today="2026-06-15", force=True)
    detail = (results._meta(w.store.conn, results.LISTS_KEY, {}) or {}).get("detail", {})
    got = detail.get(fid) or {}
    assert got.get("returned") == 4 * (8236 - 7664), got
    money = results._meta(w.store.conn, results.DASH_KEY, {})["money"]
    assert money["returned_cents"] == 4 * (8236 - 7664), money
    w.store.close()


def test_results_a_reviews_money_is_counted_once_beside_older_items_on_its_claims(tmp_path, kind, results_env):
    """Claims 101 and 102 are first flagged as one-offs (April 16); with six more the review takes them in (May 1). On June
    10 the payer repays 101, 102 and 103 ($17.60 each): $52.80 came back, counted once (101's and 102's for the one-offs
    flagged first, 103's for the review), the review in one group with the items it shares claims with."""
    from remitwatch import results
    from tests.test_features import NPI
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99214", "223.65", "117.60") + clean_visits(101, 2, "99214", "223.65", "100.00"), "20260415")
    w.run("r0", today="2026-04-16")
    ones = [r["finding_id"] for r in w.rows("signal='UNDERPAID_VS_OWN_RATE'")]
    assert len(ones) == 2, brief(w.rows())
    w.store.conn.execute("UPDATE findings SET first_seen='2026-04-16T12:00:00+00:00'")
    w.store.conn.commit()
    results.refresh(w.store.conn, today="2026-04-16", force=True)
    w.remit(clean_visits(103, 6, "99214", "223.65", "100.00"), "20260420")
    w.run("r1", today="2026-05-01")
    fid = _flag_review(w, "2026-05-01")
    _repaid(w)
    who = f"NM1*QC*1*SAMPLE*PAT****MI*M{103:08d}~NM1*82*2*TEST PROVIDER*****XX*{NPI}~DTM*232*20260401~"
    w.remit([f"CLP*CB0000103C024*1*223.65*17.60*0.00*12*PC103B*11~{who}SVC*HC:99214*223.65*17.60**1~DTM*472*20260401~"
             "CAS*CO*45*106.05~CAS*OA*23*100.00~AMT*B6*117.60~"], "20260610")
    w.run("r2", today="2026-07-01")
    results.refresh(w.store.conn, today="2026-07-01", force=True)
    got = {f: _detail(w, f).get("returned") for f in ones + [fid]}
    assert got == {ones[0]: 1760, ones[1]: 1760, fid: 1760}, got
    money = results._meta(w.store.conn, results.DASH_KEY, {})["money"]
    assert money["returned_cents"] == 3 * 1760, money
    # one group: the review and the items it shares claims with (each group's money counted once, as the lift counts it)
    att = results.attribute(w.store.conn, "2026-07-01")
    assert len({att["groups"].get(f) for f in ones + [fid]}) == 1, {f: att["groups"].get(f) for f in ones + [fid]}
    assert sum(att["group_money"].values()) == 3 * 1760, att["group_money"]
    w.store.close()


def test_results_a_person_marking_a_review_paid_counts_when_no_payment_shows(tmp_path, kind, results_env):
    """A person marks the review Paid ($50.00 on June 20) and no payment of its claims after their flags shows it. Claim
    109, which joined the review in June, was paid then, but that payment is the claim's own, not money back: the person's
    mark is counted, as it is for any item with no payment record after its flag."""
    from remitwatch import results
    w = _pattern_world(tmp_path, kind)
    w.run("r1", today="2026-05-01")
    fid = _flag_review(w, "2026-05-01")
    w.remit(clean_visits(109, 1, "99214", "223.65", "100.00"), "20260601")
    w.run("r2", today="2026-06-02")
    results.refresh(w.store.conn, today="2026-06-02", force=True)
    w.store.conn.execute("""UPDATE findings SET status='resolved', recovered_cents=5000, recovered_date='2026-06-20', auto_note=NULL
                            WHERE finding_id=?""", (fid,))
    w.store.conn.commit()
    results.refresh(w.store.conn, today="2026-07-01", force=True)
    got = _detail(w, fid)
    assert got.get("returned") == 5000 and [e.get("kind") for e in got.get("events") or []] == ["mark"], got
    w.store.close()


def _repricing_world(tmp_path, kind):
    """UnitedHealthcare's 2026 rate for 99213 is $82.36 (2025: $76.64); ten January 2026 visits (claims 900 to 909) were paid
    at the 2025 amount: one repricing request (tests/test_plan_rates.py's world)."""
    from tests.test_plan_rates import DOC, DR, many, types
    from tests.test_sizes_and_rates import World as RWorld
    w = RWorld(tmp_path, kind)
    types(w, (DOC, "127.80", DR, 1))
    payer = dict(payer="UNITEDHEALTHCARE", payer_id="UHCTEST")
    w.remit(many(100, 25, "99213", "156.66", "76.64", cfi="16", dos="20250610"), "20250701", **payer)
    w.remit(many(200, 25, "99213", "156.66", "82.36", cfi="16", dos="20260105"), "20260310", **payer)
    w.remit(many(900, 10, "99213", "156.66", "76.64", cfi="16", dos="20260112"), "20260215", **payer)
    return w, payer


def _paid_difference(w, payer: dict, claims: range, day: str) -> None:
    """The payer pays the $5.72 difference on each claim's corrected claim (C024), its first payment left standing."""
    from tests.test_plan_rates import DOC, DR
    w.remit([f"CLP*CB{i:07d}C024*1*156.66*5.72*0.00*16*PC{i:07d}B*11~NM1*QC*1*PATIENT*P{i:05d}****MI*M{i:08d}~"
             f"NM1*82*{DR}****XX*{DOC}~DTM*232*20260112~SVC*HC:99213*156.66*5.72**1~DTM*472*20260112~"
             "CAS*CO*45*74.30~CAS*OA*23*76.64~AMT*B6*82.36~" for i in claims], day, **payer)


@pytest.mark.parametrize("worked", [False, True])
def test_pattern_a_repricing_request_leaves_out_the_claims_the_payer_paid_more_since(tmp_path, kind, worked):
    """The payer pays four of the ten claims the $5.72 difference on corrected claims, without taking their first payments
    back. The request lists the six still owed (a request someone worked as well), and names the four as repriced, each at
    $82.36 now."""
    from tests.test_plan_rates import pcn
    w, payer = _repricing_world(tmp_path, kind)
    w.run(today="2026-04-15")
    fid = w.one("signal='OLD_FEE_SCHEDULE'")["finding_id"]
    if worked:
        w.store.conn.execute("UPDATE findings SET status='in_progress' WHERE finding_id=?", (fid,))
        w.store.conn.commit()
    _paid_difference(w, payer, range(900, 904), "20260501")
    w.run(today="2026-06-15")
    item = w.one("signal='OLD_FEE_SCHEDULE'")
    assert item["finding_id"] == fid
    listed = sorted(c[0] for c in item["ev"]["claims"])
    assert listed == sorted(pcn(i) for i in range(904, 910)) and item["shortfall_cents"] == 6 * (8236 - 7664), \
        (listed, item["shortfall_cents"])
    assert sorted((c[0], c[4]) for c in item["ev"].get("repriced") or []) == [(pcn(i), 8236) for i in range(900, 904)], \
        item["ev"].get("repriced")
    w.store.close()


# =========================================================================== residual: J3490, an unlisted drug the payer paid once
def _j3490_history(w: World, units: int) -> None:
    """Anthem allowed J3490 once, for a drug billed with `units` units ($35.00)."""
    w.remit([clm("CB0000800C001", "1", "50.00", "35.00", sv("J3490", "50.00", "35.00", units, "CAS*CO*45*15.00~", dos="20260210",
                                                             allowed="35.00"), payer_ctl="AJ0", dos="20260210")], "20260301")


@pytest.mark.parametrize("units_before,flag", [(1, True), (4, False)])
def test_j3490_an_unlisted_drug_is_priced_by_the_payers_history_with_its_units(tmp_path, kind, units_before, flag):
    """J3490 is any drug. Anthem writes off the whole $400.00 of a 4-unit J3490 as CO-45. It allowed J3490 once before: for
    a 1-unit drug that says nothing of this drug, so the line is listed as a code the payer has no rate for (decision 4's
    research of why it does not pay); for the same 4 units it is a denial of a drug the payer pays. Listed either way."""
    w = World(tmp_path, kind)
    _j3490_history(w, units_before)
    w.remit([clm("CB0000810C017", "1", "400.00", "0.00", sv("J3490", "400.00", "0.00", 4, "CAS*CO*45*400.00~", dos="20260407"),
                 payer_ctl="AJ1", dos="20260407")], "20260428")
    w.run("j1", today="2026-05-01")
    items = [r for r in w.rows("COALESCE(stale,0)=0") if r["patient_control_number"] == "CB0000810C017"]
    print([(r["signal"], r["status"], r["shortfall_cents"], json.loads(r["evidence"]).get("payer_has_no_rate")) for r in items])
    assert items, "the payer wrote off the whole $400.00 of J3490: listed"
    assert bool(json.loads(items[0]["evidence"]).get("payer_has_no_rate")) is flag, items[0]["evidence"]
    w.store.close()


# =========================================================================== residual: Medicaid owes a share up to its own rate
def _claim_denied_after_primary(tmp_path, kind, mcd_rates: dict):
    """Medicaid's own rates (25 visits of each code it paid: `mcd_rates`); Humana pays the destruction ($23.00, coinsurance
    $6.00 left) and puts the visit on the deductible ($105.00); Medicaid denies the whole claim ten days later (CO-B7)."""
    from tests.test_claim_after_primary import BILL, HUMANA, MEDICAID, World, primary_answer, run
    w = World(tmp_path, kind)
    start = 1
    for code, (charge, rate) in mcd_rates.items():
        w.remit(clean_visits(start, 25, code, charge, rate), "20260301", **MEDICAID)
        start += 100
    w.remit([primary_answer()], "20260501", **HUMANA)
    w.remit([clm(f"{BILL}C018", "4", "172.00", "0.00", sv("99203", "110.00", "0.00", 0, "CAS*CO*B7*110.00~", mods="25")
                 + sv("17000", "62.00", "0.00", 0, "CAS*CO*B7*62.00~", mods="59"), payer_ctl="MD1")], "20260511", **MEDICAID)
    run(w, "r1")
    return w, w.items()[f"{BILL}C018::CLAIM_DENIED_UNPAID"]


def test_medicaid_a_claim_denied_after_the_primary_is_sized_at_what_medicaid_owes_by_its_own_rates(tmp_path, kind):
    """Medicaid's own rates: 99203 $105.00, 17000 $20.00. The primary left $111.00 to the patient ($105.00 of the visit,
    $6.00 of the destruction), but Medicaid pays a service it answers second only up to its rate less the primary's
    payment: the visit $105.00, the destruction nothing ($20.00 less $23.00 paid). The item is the $105.00 Medicaid owes."""
    w, r = _claim_denied_after_primary(tmp_path, kind, {"99203": ("110.00", "105.00"), "17000": ("62.00", "20.00")})
    print(r["shortfall_cents"], r["work_state"], r["hint"])
    assert (r["shortfall_cents"], r["work_state"]) == (10500, "work_now"), (r["shortfall_cents"], r["work_state"], r["hint"])
    assert "owes $105.00 of the $111.00 share" in (r["hint"] or ""), r["hint"]
    assert json.loads(r["evidence"])["archive_rule"]["patient_share_left_cents"] == 11100
    w.store.close()


def test_medicaid_owes_nothing_of_a_share_when_its_rate_is_below_what_the_primary_paid(tmp_path, kind):
    """Medicaid's own rates: 99203 $20.00, 17000 $20.00. Of the $111.00 share it owes $20.00 for the visit (the primary paid
    nothing on it) and nothing for the destruction (its $20.00 less the primary's $23.00)."""
    w, r = _claim_denied_after_primary(tmp_path, kind, {"99203": ("110.00", "20.00"), "17000": ("62.00", "20.00")})
    assert (r["shortfall_cents"], r["work_state"]) == (2000, "work_now"), (r["shortfall_cents"], r["work_state"], r["hint"])
    w.store.close()


def test_medicaid_control_a_service_medicaid_never_priced_caps_nothing(tmp_path, kind):
    """Medicaid never paid 17000: what it owes for the destruction is not known, so nothing caps the $111.00 share."""
    w, r = _claim_denied_after_primary(tmp_path, kind, {"99203": ("110.00", "105.00")})
    assert (r["shortfall_cents"], r["work_state"]) == (11100, "work_now"), (r["shortfall_cents"], r["work_state"], r["hint"])
    w.store.close()
