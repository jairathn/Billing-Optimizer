"""
Fix wave F3a: the 837 pass and the sizing of a claim no payer answered (plans/briefs/2026-11-26_fixwave.md, section F3a;
the review's answers-1, 3, 4, 5 and 6 in plans/review_2026-11-26.md, and the residuals of plans/next_fixes_2026-11-24.md
sections 2, 3 and 5).

The review's tests (since-answers-tests/test_answers_review.py) and the verifier's (since-answers-verify-tests/) were
written against fc73dec; they are ported here, with the residuals' own tests. Every claim id, member number, name,
amount and date is invented (the repository's builders: tests/test_837_sends, tests/test_features, tests/test_workable).
Each test that runs detection runs on the server's store file and on the compact in-memory store the serverless cycle
builds and analyses.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch.detect import Detector  # noqa: E402
from tests.test_837_sends import ANTHEM, HUMANA, KINDS, V, _chased, _remits, _store, _visits, answer, member_of, send  # noqa: E402
from tests.test_features import NPI, svc  # noqa: E402


def clm837(claim_id: str, lines: list[tuple], payer: tuple = ANTHEM, freq: str = "1", member: str | None = None,
           patient: tuple | None = None, subscriber: tuple = ("SAMPLE", "PAT", None), resp: str = "P", cfi: str = "CI",
           other: dict | None = None) -> str:
    """One subscriber loop of an 837P and its claim (tests/test_837_sends.clm837, with what that builder leaves out):
    - subscriber: the insured's (last, first, birth date CCYYMMDD or None) in 2010BA (NM1*IL, DMG);
    - patient: (last, first, birth date) adds the patient loop (2000C: HL level 23, PAT; 2010CA: NM1*QC, DMG): the patient
      is a dependent, and SBR02 is left empty; without it the subscriber is the patient (SBR02 18);
    - resp: SBR01, the payer's responsibility for this claim (P primary, S secondary, T tertiary); cfi: SBR09;
    - other: the other payer's loop for a claim sent to a payer as its secondary (2320/2330B): {'payer': (name, id),
      'resp': 'P', 'paid': dollars (AMT*D), 'left': dollars (AMT*EAF), 'pr': dollars of CAS*PR, 'co': dollars of CAS*CO,
      'lines': [(paid, pr dollars)] per service line (SVD, CAS*PR)}.
    Lines: (code, charge in dollars, date of service CCYYMMDD, modifiers 'A:B', units)."""
    member = member or member_of(claim_id)
    total = sum(float(ln[1]) for ln in lines)
    sl, sf, sdob = subscriber
    out = (f"HL*2*1*22*{1 if patient else 0}~SBR*{resp}*{'' if patient else '18'}*GRP1******{cfi}~"
           f"NM1*IL*1*{sl}*{sf}****MI*{member}~" + (f"DMG*D8*{sdob}*F~" if sdob else "")
           + f"NM1*PR*2*{payer[0]}*****PI*{payer[1]}~")
    if patient:
        pl, pf, pdob = patient
        out += f"HL*3*2*23*0~PAT*19~NM1*QC*1*{pl}*{pf}~" + (f"DMG*D8*{pdob}*M~" if pdob else "")
    out += f"CLM*{claim_id}*{total:.2f}***11:B:{freq}*Y*A*Y*Y~"
    if other:
        o = other
        out += (f"SBR*{o.get('resp', 'P')}*18*GRP2******CI~" + (f"CAS*PR*1*{o['pr']:.2f}~" if o.get("pr") else "")
                + (f"CAS*CO*45*{o['co']:.2f}~" if o.get("co") else "") + f"AMT*D*{o.get('paid', 0):.2f}~"
                + (f"AMT*EAF*{o['left']:.2f}~" if o.get("left") is not None else "")
                + "OI***Y***Y~NM1*IL*1*OTHER*INSURED****MI*OTHER0001~"
                + f"NM1*PR*2*{o['payer'][0]}*****PI*{o['payer'][1]}~")
    for n, ln in enumerate(lines, 1):
        code, charge, dos = ln[0], ln[1], ln[2]
        mods = ln[3] if len(ln) > 3 else ""
        units = ln[4] if len(ln) > 4 else 1
        out += f"LX*{n}~SV1*HC:{code}{':' + mods if mods else ''}*{charge}*UN*{units}***1~DTP*472*D8*{dos}~"
        if other and other.get("lines") and n <= len(other["lines"]):
            paid, pr = other["lines"][n - 1]
            out += (f"SVD*{other['payer'][1]}*{paid:.2f}*HC:{code}**{units}~" + (f"CAS*PR*1*{pr:.2f}~" if pr else "")
                    + f"DTP*573*D8*{dos}~")
    return out


def _show(found: dict) -> None:
    for k, v in sorted(found.items()):
        print(" ", k, v["signal"], v["finding_id"], v.get("source"), "shortfall", v["shortfall_cents"], "state", v.get("work_state"),
              "|", (v.get("title") or "")[:100])


# ================================================================================================ answers-1 (the 837 pass)
@pytest.mark.parametrize("kind", KINDS)
def test_one_visit_billed_again_under_a_new_bill_is_chased_once_from_the_837s(tmp_path, kind):
    """(answers-1; the reviewer's test and the verifier's port.) The same patient had one visit on September 14 (99214).
    The practice sent it to Anthem on bill CB0000M21 (C017, September 15), had no answer, made the bill again as CB0000M22
    (C017) and sent that on September 25. Neither bill was voided and no payer answered either; Anthem's remittances reach
    November 10. Only the 837s know both bills. One unpaid service: only the newest bill carries it."""
    s = _store(tmp_path, kind)
    member = "WDUP000001"
    send(s, tmp_path, [clm837("CB0000M21C017", V, member=member)], "20260915")
    send(s, tmp_path, [clm837("CB0000M22C017", V, member=member)], "20260925")
    _remits(s, tmp_path, _visits(), "20261110")
    found = _chased(s, "t1")
    _show(found)
    assert set(found) == {"CB0000M22C017"}, (
        f"listed {sorted(found)}: one visit (same patient, day and code) under two bills is one unpaid service; "
        "only the newest bill should carry it")
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_two_family_members_on_one_subscriber_id_seen_the_same_day_are_two_services(tmp_path, kind):
    """(The verifier's guard for answers-1.) Two dependents on one subscriber id, each seen September 14 for a 99214,
    billed on two bills sent the same day. Each 837 names its own patient (the patient loop, 2000C/2010CA): a one-visit
    rule keyed by the subscriber id alone would merge them and drop an owed service. Both are chased."""
    s = _store(tmp_path, kind)
    sub = "WFAMV00001"
    send(s, tmp_path, [clm837("CB0000F01C017", V, member=sub, patient=("SAMPLE", "CHILDA", "20150301")),
                       clm837("CB0000F02C017", V, member=sub, patient=("SAMPLE", "CHILDB", "20170704"))], "20260915")
    _remits(s, tmp_path, _visits(), "20261110")
    found = _chased(s, "t1")
    _show(found)
    assert set(found) == {"CB0000F01C017", "CB0000F02C017"}
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_two_bills_of_one_subscriber_id_sent_the_same_day_with_no_patient_named_stay_two(tmp_path, kind):
    """(The verifier's note, as it stands at fc73dec.) Two bills under one subscriber id, both sent September 15 for a
    99214 of September 14, with no patient loop and nothing else naming the patient (no remittance, no EMA row): nothing
    shows whether they are one visit billed twice or two family members, so neither is dropped."""
    s = _store(tmp_path, kind)
    member = "WFAMV00002"
    send(s, tmp_path, [clm837("CB0000F11C017", V, member=member), clm837("CB0000F12C017", V, member=member)], "20260915")
    _remits(s, tmp_path, _visits(), "20261110")
    found = _chased(s, "t1")
    _show(found)
    assert set(found) == {"CB0000F11C017", "CB0000F12C017"}
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_a_remade_bill_whose_first_bill_was_voided_is_chased_once(tmp_path, kind):
    """(The verifier's control.) The practice voided the first bill (its newest send has frequency 8) before sending the
    remade bill: the void ends the first bill (T-N5) and only the remade bill is chased."""
    s = _store(tmp_path, kind)
    member = "WDUPV00003"
    send(s, tmp_path, [clm837("CB0000M51C017", V, member=member)], "20260915")
    send(s, tmp_path, [clm837("CB0000M51C017", V, member=member, freq="8"), clm837("CB0000M52C017", V, member=member)], "20260925")
    _remits(s, tmp_path, _visits(), "20261110")
    found = _chased(s, "t1")
    _show(found)
    assert set(found) == {"CB0000M52C017"}
    s.close()


# ================================================================================================ answers-3 (the pricing half)
@pytest.mark.parametrize("kind", KINDS)
def test_a_line_billed_at_no_charge_is_priced_at_nothing(tmp_path, kind):
    """(answers-3, the pricing half: price_unanswered_lines.) Anthem pays 99214 at $117.60 and 99213 at $80. A line whose
    known charge is $0, or a token under $1, is worth nothing to chase (the payer allows the lesser of its rate and the
    charge); the payer's rate is kept only when the charge is unknown."""
    from tests.test_features import claim
    s = _store(tmp_path, kind)
    rates = [claim(500 + i, svc("99213", "150.00", "80.00", "80.00", "CAS*CO*45*70.00~")) for i in range(25)]
    _remits(s, tmp_path, rates + _visits(), "20261110")
    det = Detector(s, run_id="p1")
    det.run()                           # the payers' usual rates are worked out by the run
    got = det.price_unanswered_lines("Anthem / BCBS", "commercial", "2026", [
        ("99214", "2026-09-14", 22365, 1, None), ("99213", "2026-09-14", 0, 1, None), ("99213", "2026-09-15", 50, 1, None),
        ("99213", "2026-09-16", None, 1, None), ("99213", "2026-09-17", 15000, 2, None)])
    print(got)
    worth = {(x[0], x[1]): x[3] for x in got}
    assert worth[("99214", "2026-09-14")] == 11760
    assert worth[("99213", "2026-09-14")] == 0, "a line billed at $0 is worth nothing"
    assert worth[("99213", "2026-09-15")] == 0, "a line billed at a token $0.50 is worth nothing"
    assert worth[("99213", "2026-09-16")] == 8000, "a line whose charge is unknown keeps the payer's rate"
    assert worth[("99213", "2026-09-17")] == 15000, "two units at $80 each, capped at the line's $150 charge"
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_an_exports_claim_with_a_line_billed_at_no_charge_is_sized_without_it(tmp_path, kind):
    """(answers-3, the reviewer's test: the size half is the pricing.) EMA's weekly exports list claim CB0000Z01C017 to
    Anthem, never answered (remittances to November 10): a 99214 charged $223.65 and a 99213 the practice billed at no
    charge. The item is sized at the 99214's $117.60, not $197.60. Claim CB0000Z02C017 (only the 99213 at $0) is not listed
    at any amount."""
    from tests.test_features import claim
    from tests.test_workable import ema
    s = _store(tmp_path, kind)
    rates = [claim(500 + i, svc("99213", "150.00", "80.00", "80.00", "CAS*CO*45*70.00~")) for i in range(25)]
    _remits(s, tmp_path, rates + _visits(), "20261110")
    ema(s.conn, "CB0000Z01C017")
    ema(s.conn, "CB0000Z01C017", cpt="99213", charges_cents=0)
    ema(s.conn, "CB0000Z02C017", cpt="99213", charges_cents=0)
    s.conn.commit()
    found = _chased(s, "t1")
    _show(found)
    z1 = found["CB0000Z01C017"]
    assert z1["shortfall_cents"] <= 11760, f"sized at ${z1['shortfall_cents'] / 100:.2f}: the 99213 billed at $0 was valued at Anthem's rate"
    assert "CB0000Z02C017" not in found or not found["CB0000Z02C017"]["shortfall_cents"], (
        f"a claim billed $0 is listed at ${found['CB0000Z02C017']['shortfall_cents'] / 100:.2f}")
    s.close()


# ================================================================================================ answers-4
@pytest.mark.parametrize("kind", KINDS)
def test_a_claim_sent_again_to_the_same_payer_after_its_answer_to_an_earlier_submission_is_still_chased(tmp_path, kind):
    """(answers-4, the reviewer's test.) Bill CB0000R01 (only the 837s know it): C017 went to Anthem on August 1; Anthem
    processed C017 as a secondary payer on August 20 (CLP02 2, OA-23, nothing paid: its records named another payer
    first). The practice fixed the coverage and sent the bill again to Anthem as C024 on September 1. Nothing has answered
    C024 by November 10. An answer counts only when it is dated on or after the send, for its own claim id or a newer
    submission of the bill: C024 is chased. Control: bill CB0000R02, the same without Anthem's August answer."""
    s = _store(tmp_path, kind)
    send(s, tmp_path, [clm837("CB0000R01C017", V), clm837("CB0000R02C017", V)], "20260801")
    _remits(s, tmp_path, [answer("CB0000R01C017", status="2", paid="0.00", cas="CAS*OA*23*223.65~")], "20260820")
    send(s, tmp_path, [clm837("CB0000R01C024", V, freq="7"), clm837("CB0000R02C024", V, freq="7")], "20260901")
    _remits(s, tmp_path, _visits(), "20261110")
    det = Detector(s, run_id="t1")
    det.run()
    found = {r["patient_control_number"]: dict(r) for r in s.query(
        "SELECT * FROM findings WHERE signal IN ('NO_ANSWER','REJECTED_PRE_PAYER') AND COALESCE(stale,0)=0")}
    other = [dict(r) for r in s.query("SELECT signal, patient_control_number, status, work_state FROM findings "
                                     "WHERE patient_control_number LIKE 'CB0000R01%'")]
    print("listed:", sorted(found), "| R01 items of any kind:", other, "| counts:", dict(det.ema_counts))
    assert "CB0000R02C024" in found, "control: the bill sent again with no answer is chased"
    assert "CB0000R01C024" in found or other, (
        "C024, sent to Anthem after Anthem's answer to C017, has no answer and no item of any kind: "
        "an answer to another submission before the send was taken as its answer")
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_its_payers_answer_to_the_claim_it_chases_after_the_send_still_answers_it(tmp_path, kind):
    """(answers-4's control.) Anthem processed the newest submission itself, C024, as secondary on October 1 (after its
    September 1 send): the payer the 837 names answered it, so it is not chased from here. An answer to a newer submission
    of the bill (C031, sent September 20 and answered October 5 as secondary) answers the older one too, as the newest
    send's answer."""
    s = _store(tmp_path, kind)
    send(s, tmp_path, [clm837("CB0000R11C024", V, freq="7")], "20260901")
    _remits(s, tmp_path, [answer("CB0000R11C024", status="2", paid="0.00", cas="CAS*OA*23*223.65~")], "20261001")
    _remits(s, tmp_path, _visits(), "20261110")
    found = _chased(s, "t1")
    _show(found)
    assert "CB0000R11C024" not in found
    s.close()


# ================================================================================================ answers-5 (the 837 half)
@pytest.mark.parametrize("kind", KINDS)
def test_a_line_added_in_a_resend_after_the_bill_was_paid_is_chased_from_the_837s(tmp_path, kind):
    """(answers-5, the reviewer's L01.) Anthem pays 99214 at $117.60 and 17110 at $80. Bill CB0000L01's C017 (a 99214 of
    July 30, sent August 1) was paid in full by Anthem as primary on August 20. The practice then found a forgotten 17110
    of the same visit and sent the bill again with both lines (C024, a replacement, September 1). Nothing answered it by
    November 10. The 17110 no 835 of the bill answered is chased; the 99214 Anthem paid is not."""
    from tests.test_features import claim
    s = _store(tmp_path, kind)
    rates = [claim(600 + i, svc("17110", "200.00", "80.00", "80.00", "CAS*CO*45*120.00~")) for i in range(25)]
    v1 = [("99214", "223.65", "20260730")]
    v2 = v1 + [("17110", "200.00", "20260730")]
    send(s, tmp_path, [clm837("CB0000L01C017", v1)], "20260801")
    _remits(s, tmp_path, [answer("CB0000L01C017", dos="20260730")], "20260820")
    send(s, tmp_path, [clm837("CB0000L01C024", v2, freq="7")], "20260901")
    _remits(s, tmp_path, rates + _visits(), "20261110")
    det = Detector(s, run_id="t1")
    det.run()
    rows = [dict(r) for r in s.query("SELECT signal, patient_control_number, procedure_code, status, work_state, shortfall_cents, "
                                     "expected_cents, evidence FROM findings WHERE patient_control_number LIKE 'CB0000L01%'")]
    print("items on the bill:", [{k: v for k, v in r.items() if k != "evidence"} for r in rows])
    mine = [r for r in rows if r["patient_control_number"] == "CB0000L01C024" and r["signal"] == "NO_ANSWER"]
    assert len(mine) == 1, f"the 17110 added in the replacement was never answered, and no item chases it: {rows}"
    ev = json.loads(mine[0]["evidence"])
    assert ev["codes"] == ["17110"], f"only the service no 835 of the bill answered is chased: {ev['codes']}"
    assert mine[0]["shortfall_cents"] == 8000
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_a_resend_of_a_paid_bill_with_nothing_new_is_not_chased(tmp_path, kind):
    """(answers-5's control.) Bill CB0000L05's C017 (a 99214) was paid in full by Anthem as primary; the practice sent the
    same claim again (C024, the 99214 alone) and nothing answered the resend: every service of it is answered on the bill,
    so nothing is chased (the resend is the paid answer's work)."""
    s = _store(tmp_path, kind)
    v1 = [("99214", "223.65", "20260730")]
    send(s, tmp_path, [clm837("CB0000L05C017", v1)], "20260801")
    _remits(s, tmp_path, [answer("CB0000L05C017", dos="20260730")], "20260820")
    send(s, tmp_path, [clm837("CB0000L05C024", v1, freq="7")], "20260901")
    _remits(s, tmp_path, _visits(), "20261110")
    found = _chased(s, "t1")
    _show(found)
    assert not any(k.startswith("CB0000L05") for k in found)
    s.close()


# ================================================================================================ answers-6
@pytest.mark.parametrize("kind", KINDS)
def test_a_claim_a_secondary_paid_in_full_is_not_sized_at_the_whole_rate(tmp_path, kind):
    """(answers-6, the reviewer's test.) Anthem's usual rate for 99214 is $117.60. Two claims went to Anthem (only the
    837s know them, September 15); no remittance of Anthem's is on file for either, and Humana processed each as
    secondary (CLP02 2, OA-23):
    - S01: Humana paid $23.52, its share: the item chases Anthem's remittance at $117.60 - $23.52 = $94.08 (T-C2).
    - S02: Humana paid $117.60: nothing of the visit's $117.60 is left to collect. The item is sized at
      max(0, expected - what the secondary paid) = $0."""
    s = _store(tmp_path, kind)
    send(s, tmp_path, [clm837("CB0000S01C017", V), clm837("CB0000S02C017", V)], "20260915")
    _remits(s, tmp_path, [answer("CB0000S01C017", status="2", paid="23.52"), answer("CB0000S02C017", status="2", paid="117.60")],
            "20261020", payer="HUMANA", payer_id="HUMNA")
    _remits(s, tmp_path, _visits(), "20261110")
    found = _chased(s, "t1")
    _show(found)
    assert (found["CB0000S01C017"]["expected_cents"], found["CB0000S01C017"]["shortfall_cents"]) == (11760, 11760 - 2352)
    s2 = found.get("CB0000S02C017")
    assert s2 is None or s2["shortfall_cents"] <= 0, (
        f"sized at ${s2['shortfall_cents'] / 100:.2f} though the secondary paid $117.60 of the $117.60 expected")
    s.close()


# ================================================================================================ the dependent's 837
def dependent_paid(claim_id: str, subscriber: str, first: str = "CHILDA") -> str:
    """Anthem's answer to one dependent's claim: the patient (NM1*QC) is the child, with no id of its own; the insured
    (NM1*IL) is the parent, with the subscriber's member id. 99214 on 2026-09-14 paid $117.60."""
    return (f"CLP*{claim_id}*1*223.65*117.60*0.00*12*P{claim_id[4:9]}*11~NM1*QC*1*SAMPLE*{first}~"
            f"NM1*IL*1*SAMPLE*PARENT****MI*{subscriber}~NM1*82*2*TEST PROVIDER*****XX*{NPI}~DTM*232*20260914~"
            + svc("99214", "223.65", "117.60", "117.60", "CAS*CO*45*106.05~", dos="20260914"))


@pytest.mark.parametrize("kind", KINDS)
def test_a_siblings_paid_visit_does_not_answer_the_other_siblings_unanswered_claim(tmp_path, kind):
    """(The verifier's adjacent test; the residual 'a dependent's 837 carries the subscriber's id'.) Two children on one
    parent's plan (one subscriber id) are both seen September 14 for a 99214. Child A's bill CB0000S11 is paid by Anthem
    (its remittance names child A as the patient and the parent as the insured). Child B's bill CB0000S12 went out
    September 15 (its 837's patient loop names child B) and no payer answered it; Anthem's remittances reach November 10.
    Child B's visit is unpaid and owed: it is chased."""
    s = _store(tmp_path, kind)
    sub = "WFAMS00001"
    send(s, tmp_path, [clm837("CB0000S11C017", V, member=sub, patient=("SAMPLE", "CHILDA", "20150301")),
                       clm837("CB0000S12C017", V, member=sub, patient=("SAMPLE", "CHILDB", "20170704"))], "20260915")
    _remits(s, tmp_path, [dependent_paid("CB0000S11C017", sub)], "20261001")
    _remits(s, tmp_path, _visits(), "20261110")
    found = _chased(s, "t1")
    _show(found)
    assert "CB0000S12C017" in found, f"listed {sorted(found)}: child B's own visit was never answered"
    assert "CB0000S11C017" not in found
    s.close()
