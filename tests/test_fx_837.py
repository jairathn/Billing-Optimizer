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


def _other_loop(o: dict, amt_d: bool) -> str:
    """One other payer's loops of a claim (2320 SBR, CAS, AMT*D, AMT*EAF, OI; 2330A NM1*IL; 2330B NM1*PR). With amt_d the
    payer's AMT*D is written (0 when 'paid' is not given); without it, only when 'paid' is given: a payer listed for
    information, that has adjudicated nothing (no AMT*D, no CAS), as a claim to the primary payer lists the secondary."""
    paid = o.get("paid", 0 if amt_d else None)
    return (f"SBR*{o.get('resp', 'P')}*18*GRP2******CI~" + (f"CAS*PR*1*{o['pr']:.2f}~" if o.get("pr") else "")
            + (f"CAS*CO*45*{o['co']:.2f}~" if o.get("co") else "") + (f"AMT*D*{paid:.2f}~" if paid is not None else "")
            + (f"AMT*EAF*{o['left']:.2f}~" if o.get("left") is not None else "")
            + f"OI***Y***Y~NM1*IL*1*OTHER*INSURED****MI*{o.get('member', 'OTHER0001')}~"
            + f"NM1*PR*2*{o['payer'][0]}*****PI*{o['payer'][1]}~")


def clm837(claim_id: str, lines: list[tuple], payer: tuple = ANTHEM, freq: str = "1", member: str | None = None,
           patient: tuple | None = None, subscriber: tuple = ("SAMPLE", "PAT", None), resp: str = "P", cfi: str = "CI",
           other: dict | None = None, others: list[dict] | None = None) -> str:
    """One subscriber loop of an 837P and its claim (tests/test_837_sends.clm837, with what that builder leaves out):
    - subscriber: the insured's (last, first, birth date CCYYMMDD or None) in 2010BA (NM1*IL, DMG);
    - patient: (last, first, birth date) adds the patient loop (2000C: HL level 23, PAT; 2010CA: NM1*QC, DMG): the patient
      is a dependent, and SBR02 is left empty; without it the subscriber is the patient (SBR02 18);
    - resp: SBR01, the payer's responsibility for this claim (P primary, S secondary, T tertiary; '' none); cfi: SBR09;
    - other: the other payer's loop for a claim sent to a payer as its secondary (2320/2330B): {'payer': (name, id),
      'resp': 'P', 'paid': dollars (AMT*D), 'left': dollars (AMT*EAF), 'pr': dollars of CAS*PR, 'co': dollars of CAS*CO,
      'lines': [(paid, pr dollars)] per service line (SVD, CAS*PR)};
    - others: more other payers' loops in that order, the same keys; one with no 'paid' has adjudicated nothing (no AMT*D).
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
        out += _other_loop(other, True)
    for o in others or []:
        out += _other_loop(o, False)
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
def test_the_work_on_the_older_bill_passes_to_the_bill_made_again(tmp_path, kind):
    """(answers-1: the older item's work goes to the newest bill's item through save()'s heir keys.) Bill CB0000M61 (one
    patient's 99214 of September 14) is listed as never answered; a biller starts it and writes a note. The practice makes
    the bill again as CB0000M62 and sends it September 25. The next run lists the visit once, on M62, Started, with the
    note; M61's item is not listed."""
    from remitwatch.actions import apply_status
    s = _store(tmp_path, kind)
    member = "WDUP000061"
    send(s, tmp_path, [clm837("CB0000M61C017", V, member=member)], "20260915")
    _remits(s, tmp_path, _visits(), "20261020")
    found = _chased(s, "t1")
    assert set(found) == {"CB0000M61C017"}
    apply_status(s.conn, found["CB0000M61C017"]["finding_id"], "in_progress", "Called Anthem: no record of it", actor="biller")
    s.conn.commit()
    send(s, tmp_path, [clm837("CB0000M62C017", V, member=member)], "20260925")
    _remits(s, tmp_path, _visits(start=40), "20261110")
    found = _chased(s, "t2")
    _show(found)
    assert set(found) == {"CB0000M62C017"}
    m62 = found["CB0000M62C017"]
    assert m62["status"] == "in_progress" and "no record of it" in (m62["notes"] or ""), (m62["status"], m62["notes"])
    s.close()


def _exports_claim(s, claim_id: str, sent: str, **kw) -> None:
    """EMA's weekly exports list this claim: its 99214 charge (September 14) and its status, Submitted on `sent`."""
    from tests.test_workable import ema
    ema(s.conn, claim_id, bill_created=sent, **kw)
    ema(s.conn, claim_id, family="claim_status", claim_status="Submitted", claim_submission_date=sent, **kw)
    s.conn.commit()


@pytest.mark.parametrize("kind", KINDS)
def test_an_older_bill_only_the_837s_know_yields_to_the_newer_bill_the_exports_chase(tmp_path, kind):
    """(answers-1 across the passes, the 837 pass's side; F3b's need.) The practice sent one patient's visit of September
    14 (99214) on bill CB0000M41 September 15; only the 837s know it. No payer answered, so it made the bill again as
    CB0000M42 and sent it September 25; EMA's weekly exports list M42 (its 837 is on file too). The exports' pass chases
    M42; the 837 pass would chase M41 as well: one visit twice. Only M42 is chased (the exports' pass makes its own older
    claim yield to a newer 837 bill the other way round)."""
    s = _store(tmp_path, kind)
    member = "WDUP000041"
    send(s, tmp_path, [clm837("CB0000M41C017", V, member=member)], "20260915")
    _exports_claim(s, "CB0000M42C017", "2026-09-25")
    send(s, tmp_path, [clm837("CB0000M42C017", V, member=member)], "20260925")
    _remits(s, tmp_path, _visits(), "20261110")
    det = Detector(s, run_id="t1")
    det.run()
    found = {r["patient_control_number"]: dict(r) for r in s.query(
        "SELECT * FROM findings WHERE signal IN ('NO_ANSWER','REJECTED_PRE_PAYER') AND COALESCE(stale,0)=0")}
    _show(found)
    assert set(found) == {"CB0000M42C017"}
    assert det.ema_counts["837_visit_under_exports_bill"] == 1
    assert "billed before on claim CB0000M41C017" in (found["CB0000M42C017"]["hint"] or "")
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_a_family_members_older_837_bill_does_not_yield_to_the_subscribers_exports_bill(tmp_path, kind):
    """The control: bill CB0000M43 (837 only, September 15) is the subscriber's child's visit (the patient loop names the
    child); bill CB0000M44, the exports' (September 25), is the subscriber's own visit the same day, under the same
    subscriber id. Two people's visits: both are chased."""
    s = _store(tmp_path, kind)
    member = "WDUP000043"
    send(s, tmp_path, [clm837("CB0000M43C017", V, member=member, patient=("SAMPLE", "KID", "20150301"))], "20260915")
    _exports_claim(s, "CB0000M44C017", "2026-09-25")
    send(s, tmp_path, [clm837("CB0000M44C017", V, member=member)], "20260925")
    _remits(s, tmp_path, _visits(), "20261110")
    found = _chased(s, "t1")
    _show(found)
    assert set(found) == {"CB0000M43C017", "CB0000M44C017"}
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_a_newer_837_bill_of_the_visit_an_older_exports_bill_carries_stays_chased(tmp_path, kind):
    """The other way round: the exports' bill CB0000M45 (September 15) is the older; the practice sent the visit again as
    CB0000M46 (September 25), which only the 837s know. The 837 pass keeps M46: the newest bill carries the visit (the
    exports' pass is the one that drops its older claim, _exports_yield_to_newer_bills on the exports' branch)."""
    s = _store(tmp_path, kind)
    member = "WDUP000045"
    _exports_claim(s, "CB0000M45C017", "2026-09-15")
    send(s, tmp_path, [clm837("CB0000M45C017", V, member=member)], "20260915")
    send(s, tmp_path, [clm837("CB0000M46C017", V, member=member)], "20260925")
    _remits(s, tmp_path, _visits(), "20261110")
    found = _chased(s, "t1")
    _show(found)
    assert "CB0000M46C017" in found and found["CB0000M46C017"]["source"] == "837"
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_an_837_bill_sent_the_same_day_as_the_exports_bill_of_the_visit_yields_to_it(tmp_path, kind):
    """Two bills of one patient's visit sent the same day, September 25: CB0000M52, which EMA's weekly exports list, and
    CB0000M53, which only the 837s know (its claim id is the higher). The exports' pass keeps its claim (a bill sent the
    same day is not a bill made again), so the 837 pass yields: the visit is chased once, on M52."""
    s = _store(tmp_path, kind)
    member = "WDUP000052"
    _exports_claim(s, "CB0000M52C017", "2026-09-25")
    send(s, tmp_path, [clm837("CB0000M52C017", V, member=member), clm837("CB0000M53C017", V, member=member)], "20260925")
    _remits(s, tmp_path, _visits(), "20261110")
    found = _chased(s, "t1")
    _show(found)
    assert set(found) == {"CB0000M52C017"}
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_the_work_on_an_older_837_bill_passes_to_the_newer_bill_the_exports_chase(tmp_path, kind):
    """Bill CB0000M47 (837 only) is listed as never answered; a biller starts it and writes a note. The practice makes the
    bill again as CB0000M48 (sent September 25), which EMA's weekly exports then list. The next run chases M48 only,
    Started, with the note."""
    from remitwatch.actions import apply_status
    s = _store(tmp_path, kind)
    member = "WDUP000047"
    send(s, tmp_path, [clm837("CB0000M47C017", V, member=member)], "20260915")
    _remits(s, tmp_path, _visits(), "20261020")
    found = _chased(s, "t1")
    assert set(found) == {"CB0000M47C017"}
    apply_status(s.conn, found["CB0000M47C017"]["finding_id"], "in_progress", "Called Anthem: no record of it", actor="biller")
    s.conn.commit()
    _exports_claim(s, "CB0000M48C017", "2026-09-25")
    send(s, tmp_path, [clm837("CB0000M48C017", V, member=member)], "20260925")
    _remits(s, tmp_path, _visits(start=40), "20261110")
    found = _chased(s, "t2")
    _show(found)
    assert set(found) == {"CB0000M48C017"}
    m48 = found["CB0000M48C017"]
    assert m48["status"] == "in_progress" and "no record of it" in (m48["notes"] or ""), (m48["status"], m48["notes"])
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_a_line_added_in_a_resend_the_exports_do_not_show_yet_is_chased_from_the_837s(tmp_path, kind):
    """(answers-5, the reviewer's L02.) Anthem paid bill CB0000L02's C017 (a 99214) in full. EMA's weekly exports list the
    replacement C024 (the 99214 and a forgotten 17110, submitted September 1); then the 837s show the bill sent again as
    C031 (September 20, both lines), which the exports do not list yet. Nothing answered C024 or C031. The exports' pass
    leaves C024 for the newer submission; the 837 pass chases C031's 17110, the one service no remittance of the bill
    answered (before: no item of any kind on L02)."""
    from tests.test_features import claim
    from tests.test_workable import ema
    s = _store(tmp_path, kind)
    rates = [claim(600 + i, svc("17110", "200.00", "80.00", "80.00", "CAS*CO*45*120.00~")) for i in range(25)]
    v1 = [("99214", "223.65", "20260730")]
    v2 = v1 + [("17110", "200.00", "20260730")]
    send(s, tmp_path, [clm837("CB0000L02C017", v1)], "20260801")
    _remits(s, tmp_path, [answer("CB0000L02C017", dos="20260730")], "20260820")
    send(s, tmp_path, [clm837("CB0000L02C024", v2, freq="7")], "20260901")
    send(s, tmp_path, [clm837("CB0000L02C031", v2, freq="7")], "20260920")
    ema(s.conn, "CB0000L02C024", service_date="2026-07-30", bill_created="2026-07-31")
    ema(s.conn, "CB0000L02C024", cpt="17110", charges_cents=20000, service_date="2026-07-30", bill_created="2026-07-31")
    ema(s.conn, "CB0000L02C024", family="claim_status", claim_status="Submitted", claim_submission_date="2026-09-01",
        service_date="2026-07-30")
    s.conn.commit()
    _remits(s, tmp_path, rates + _visits(), "20261110")
    Detector(s, run_id="t1").run()
    rows = [dict(r) for r in s.query("SELECT signal, patient_control_number, status, shortfall_cents, evidence FROM findings "
                                     "WHERE patient_control_number LIKE 'CB0000L02%' AND COALESCE(stale,0)=0")]
    print([{k: v for k, v in r.items() if k != "evidence"} for r in rows])
    mine = [r for r in rows if r["patient_control_number"] == "CB0000L02C031" and r["signal"] == "NO_ANSWER"]
    assert len(mine) == 1 and json.loads(mine[0]["evidence"])["codes"] == ["17110"] and mine[0]["shortfall_cents"] == 8000, rows
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
def test_two_bills_of_one_subscriber_id_with_no_patient_named_stay_two(tmp_path, kind):
    """(The verifier's guard, as it stands for the 837s read before the patient loop was read.) Two bills under one
    subscriber id, both sent September 15 for a 99214 of September 14, read by the first release's reader (no patient, no
    read_version), and nothing else naming the patient (no remittance of either bill, no EMA row): nothing shows whether
    they are one visit billed twice or two family members, so neither is dropped."""
    s = _store(tmp_path, kind)
    member = "WFAMV00002"
    send(s, tmp_path, [clm837("CB0000F11C017", V, member=member), clm837("CB0000F12C017", V, member=member)], "20260915")
    s.conn.execute("UPDATE claims_837 SET patient_key = NULL, person_key = NULL, relationship = NULL, read_version = NULL")
    s.conn.commit()
    _remits(s, tmp_path, _visits(), "20261110")
    found = _chased(s, "t1")
    _show(found)
    assert set(found) == {"CB0000F11C017", "CB0000F12C017"}
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_one_patient_billed_twice_for_one_visit_the_same_day_is_chased_once(tmp_path, kind):
    """Two bills, both sent September 15, whose 837s name the same patient (the subscriber, with the same birth date) for
    the same 99214 of September 14: one visit billed twice. Only the newer bill (the higher claim id on the same day) carries
    it. A 99214 with modifier 76 (a repeat service) on a third bill of the same day is another service and stays."""
    s = _store(tmp_path, kind)
    member = "WDUP000009"
    pt = ("SAMPLE", "PAT", "19700101")
    send(s, tmp_path, [clm837("CB0000D11C017", V, member=member, subscriber=pt), clm837("CB0000D12C017", V, member=member, subscriber=pt),
                       clm837("CB0000D13C017", [("99214", "223.65", "20260914", "76")], member=member, subscriber=pt)], "20260915")
    _remits(s, tmp_path, _visits(), "20261110")
    found = _chased(s, "t1")
    _show(found)
    assert set(found) == {"CB0000D12C017", "CB0000D13C017"}
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
def test_a_resend_of_a_bill_whose_first_send_is_older_than_the_837s_on_file_is_not_chased(tmp_path, kind):
    """(Found hand-tracing the world copy: a bill sent before the 837 files on file begin.) Bill CB0000L09's visit of July 1
    (a 99214 and a 17110) went out before the first 837 on file. Anthem's remittance of August 20 (the first on file)
    answers its 99214 only, with CO-97 (included in another service already adjudicated): the 17110 was answered before
    the remittances on file begin. The practice sent the claim again September 1 with both services. The 837s on file
    hold no send of the bill before its first answer, so the archive cannot tell whether the 17110 was answered: it is
    not chased as never answered (counted in meta ema:wiring). The reviewer's L01, whose first send is on file, still is."""
    from tests.test_features import claim
    s = _store(tmp_path, kind)
    rates = [claim(600 + i, svc("17110", "200.00", "80.00", "80.00", "CAS*CO*45*120.00~")) for i in range(25)]
    v = [("99214", "223.65", "20260701"), ("17110", "200.00", "20260701")]
    _remits(s, tmp_path, [answer("CB0000L09C017", paid="0.00", dos="20260701", cas="CAS*CO*97*223.65~")], "20260820")
    send(s, tmp_path, [clm837("CB0000L09C017", v)], "20260901")
    _remits(s, tmp_path, rates + _visits(), "20261110")
    det = Detector(s, run_id="t1")
    det.run()
    rows = [dict(r) for r in s.query("SELECT signal, patient_control_number, shortfall_cents FROM findings WHERE patient_control_number LIKE 'CB0000L09%' "
                                     "AND signal IN ('NO_ANSWER', 'REJECTED_PRE_PAYER')")]
    print(rows, dict(det.ema_counts))
    assert rows == []
    assert det.ema_counts["837_resend_bill_older_than_837s"] == 1
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_the_837_pass_walks_its_bills_not_every_answered_claim(tmp_path, kind):
    """(Found measuring on the world copy: the run spent more than 20 minutes in the resend scan, _837_resends.) Each query
    that reads claims by a bill's range of claim ids from a temporary list of bills must walk that list and look the claims
    up by the claim id index. The server's store also indexes claims by status: with a status filter beside the range,
    SQLite (which keeps no statistics here) read every claim a payer answered as primary and probed the list for each, once
    for every 500 bills. The answers-5 scenario (L01) makes every such query of the 837 pass run."""
    import re
    from tests.test_features import claim
    s = _store(tmp_path, kind)
    rates = [claim(600 + i, svc("17110", "200.00", "80.00", "80.00", "CAS*CO*45*120.00~")) for i in range(25)]
    v1 = [("99214", "223.65", "20260730")]
    send(s, tmp_path, [clm837("CB0000L01C017", v1)], "20260801")
    _remits(s, tmp_path, [answer("CB0000L01C017", dos="20260730")], "20260820")
    send(s, tmp_path, [clm837("CB0000L01C024", v1 + [("17110", "200.00", "20260730")], freq="7")], "20260901")
    _remits(s, tmp_path, rates + _visits(), "20261110")
    seen: list[str] = []
    s.conn.set_trace_callback(seen.append)
    Detector(s, run_id="t1").run()
    s.conn.set_trace_callback(None)
    ddl = {m.group(1): q for q in seen for m in [re.match(r"\s*CREATE TEMP TABLE (d837_\w+)", q)] if m}
    joins = [q for q in seen if re.search(r"FROM temp\.d837_\w+ x\s+JOIN claims cl ON cl\.patient_control_number >= x\.bill", q)]
    assert any("temp.d837_rs" in q for q in joins), "the scenario reaches the resend scan"
    bad = []
    for q in joins:
        t = re.search(r"FROM temp\.(d837_\w+) x", q).group(1)
        s.conn.execute(f"DROP TABLE IF EXISTS temp.{t}")
        s.conn.execute(ddl[t])
        plan = [r[3] for r in s.conn.execute("EXPLAIN QUERY PLAN " + q)]
        if not plan[0].startswith("SCAN x"):
            bad.append((t, plan))
    assert not bad, bad
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


# ================================================================================================ the 837 reader
def _file837(claims: list[str], bht: str = "20260915", n: int = 1) -> bytes:
    """An 837P file made on `bht` holding these subscriber loops (tests/test_837_sends.send's envelope)."""
    st = (f"ST*837*{n:04d}*005010X222A1~BHT*0019*00*{n}*{bht}*1200*CH~HL*1**20*1~NM1*85*2*TEST DERMATOLOGY*****XX*1777777772~"
          + "".join(claims) + f"SE*99*{n:04d}~")
    return (f"ISA*00*          *00*          *ZZ*999999999      *ZZ*TRIZETTO       *{bht[2:]}*1200*^*00501*{n:09d}*0*P*:~"
            f"GS*HC*999999999*TRIZETTO*{bht}*1200*{n}*X*005010X222A1~" + st + f"GE*1*{n}~IEA*1*{n:09d}~").encode()


def test_the_837_reader_reads_the_patient_the_payers_place_and_the_other_payer():
    """Three subscriber loops of invented claims:
    - CB0000P01 (the subscriber is the patient, SBR02 18, with a birth date): relationship 18, the patient keyed by the
      subscriber's name and birth date; Anthem is the primary (SBR01 P), claim filing indicator CI.
    - CB0000P02 (a dependent: the patient loop names the child): relationship 19, the patient keyed by the child's name
      and birth date, not the subscriber's; the member id stays the subscriber's.
    - CB0000P03 sent to Humana as secondary (SBR01 S) after Anthem paid $94.08 (2320: AMT*D, AMT*EAF $23.52; 2430: the
      line's SVD and its CAS*PR $23.52) and
      CB0000P04 in the same subscriber loop after it: P04 keeps its own payer (Humana), not the other payer's loop's.
    No name or birth date reaches the parsed claims; keys are made only with the store's salt."""
    from remitwatch.ema_reports import name_key_parts, person_key
    from remitwatch.x12_837 import parse_837_bytes
    salt = bytes(range(32))
    data = _file837([
        clm837("CB0000P01C017", V, member="WSELF00001", subscriber=("TESTLAST", "ALPHA", "19800102")),
        clm837("CB0000P02C017", V, member="WFAM000001", subscriber=("TESTLAST", "BRAVO", "19790304"),
               patient=("TESTLAST", "CHARLIE", "20150506")),
        clm837("CB0000P03C017", V, payer=HUMANA, member="WSEC000001", resp="S", cfi="MB",
               other={"payer": ANTHEM, "paid": 94.08, "left": 23.52, "co": 106.05, "lines": [(94.08, 23.52)]})
        + f"CLM*CB0000P04C017*223.65***11:B:1*Y*A*Y*Y~LX*1~SV1*HC:99214*223.65*UN*1***1~DTP*472*D8*20260914~",
    ])
    out = parse_837_bytes(data, salt=salt)
    c = {x["patient_control_number"]: x for x in out["claims"]}
    assert c["CB0000P01C017"]["relationship"] == "18" and c["CB0000P01C017"]["payer_resp"] == "P"
    assert c["CB0000P01C017"]["filing_indicator"] == "CI"
    assert c["CB0000P01C017"]["patient_key"] == name_key_parts(salt, "TESTLAST", "ALPHA")
    assert c["CB0000P01C017"]["person_key"] == person_key(salt, "TESTLAST, ALPHA", "1980-01-02")
    assert c["CB0000P02C017"]["relationship"] == "19" and c["CB0000P02C017"]["subscriber_id"] == "WFAM000001"
    assert c["CB0000P02C017"]["patient_key"] == name_key_parts(salt, "TESTLAST", "CHARLIE")
    assert c["CB0000P02C017"]["person_key"] == person_key(salt, "TESTLAST, CHARLIE", "2015-05-06")
    p3 = c["CB0000P03C017"]
    assert (p3["payer_resp"], p3["filing_indicator"], p3["payer_name"]) == ("S", "MB", "HUMANA")
    assert (p3["other_payer_name"], p3["other_payer_id"], p3["other_payer_resp"]) == ("ANTHEM", "00660", "P")
    assert (p3["other_paid_cents"], p3["other_left_cents"], p3["other_pr_cents"]) == (9408, 2352, 2352)
    p4 = c["CB0000P04C017"]
    assert (p4["payer_name"], p4["payer_id"], p4["subscriber_id"]) == ("HUMANA", "61101", "WSEC000001"), (
        "the other payer's loop of the claim before it changed this claim's payer or member")
    assert p4["other_payer_name"] is None and p4["other_paid_cents"] is None
    flat = json.dumps(out, default=str).upper()
    for word in ("ALPHA", "BRAVO", "CHARLIE", "TESTLAST", "1980-01-02", "2015-05-06", "19800102", "20150506"):
        assert word not in flat, f"{word} reached the parsed claims"
    nokey = parse_837_bytes(data)
    assert all(x["patient_key"] is None and x["person_key"] is None for x in nokey["claims"])


@pytest.mark.parametrize("kind", KINDS)
def test_an_837_read_before_the_patient_loop_was_read_is_filled_in_when_read_again(tmp_path, kind):
    """A store whose 837 tables were made by the first release (no patient, payer's place or other payer columns): the
    columns are added on the next read, a file read before keeps NULLs (read_version NULL) and is listed to be read again,
    and reading its bytes again fills them in by each claim's key without touching anything else. No name is stored."""
    from remitwatch import x12_837 as X
    s = _store(tmp_path, kind)
    c = s.conn
    old = X.DDL
    c.executescript(old)
    data = _file837([clm837("CB0000P11C017", V, member="WFAM000002", patient=("TESTLAST", "DELTA", "20160707"))])
    parsed = X.parse_837_bytes(data)
    cols = ["claim_837_key", "file_sha256", "bht_created", "patient_control_number", "total_charge_cents", "payer_name", "payer_id",
            "subscriber_id"]
    c.executemany(f"INSERT INTO claims_837 ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                  [tuple(x.get(k) for k in cols) for x in parsed["claims"]])
    c.commit()
    X.ensure_schema(c)
    have = {r[1] for r in c.execute("PRAGMA table_info(claims_837)")}
    assert set(X.EXTRA_CLAIM_COLS) <= have
    assert X.files_to_reread(c) == [parsed["sha256"]]
    got = X.reread_837_bytes(c, data)
    assert got["updated"] == 1
    row = dict(zip([d[0] for d in c.execute("SELECT * FROM claims_837").description], c.execute("SELECT * FROM claims_837").fetchone()))
    assert row["relationship"] == "19" and row["read_version"] == X.READ_VERSION and row["patient_key"] is not None
    assert row["subscriber_id"] == "WFAM000002" and row["bht_created"] == "2026-09-15"
    assert X.files_to_reread(c) == []
    blob = json.dumps([list(r) for r in c.execute("SELECT * FROM claims_837")], default=str).upper()
    assert "DELTA" not in blob and "TESTLAST" not in blob and "2016-07-07" not in blob
    s.close()


# ================================================================================================ a claim sent to its secondary
@pytest.mark.parametrize("kind", KINDS)
def test_a_claim_sent_to_a_payer_as_its_secondary_is_sized_by_what_the_primary_left(tmp_path, kind):
    """(Residual: a claim sent to a payer as its secondary was priced at that payer's full rate.) Humana pays 99214 at
    $117.60 as a primary. Three claims only the 837s know went to Humana as the secondary payer (SBR01 S) on September 15
    after Anthem answered on paper (no Anthem remittance is on file), and Humana has not answered by November 10:
    - CB0000Y01: Anthem paid $94.08 and left the patient $23.52 (AMT*EAF): the item is $23.52, not $117.60;
    - CB0000Y02: Anthem's left amount is only in its PR adjustments on the line (no AMT*EAF): $30.00;
    - CB0000Y03: Anthem left the patient nothing (AMT*EAF 0): Humana owes nothing, no item."""
    s = _store(tmp_path, kind)
    send(s, tmp_path, [
        clm837("CB0000Y01C017", V, payer=HUMANA, resp="S", other={"payer": ANTHEM, "paid": 94.08, "left": 23.52, "lines": [(94.08, 23.52)]}),
        clm837("CB0000Y02C017", V, payer=HUMANA, resp="S", other={"payer": ANTHEM, "paid": 87.60, "lines": [(87.60, 30.00)]}),
        clm837("CB0000Y03C017", V, payer=HUMANA, resp="S", other={"payer": ANTHEM, "paid": 117.60, "left": 0.0, "lines": [(117.60, 0)]}),
    ], "20260915")
    _remits(s, tmp_path, _visits(), "20261110", payer="HUMANA", payer_id="61101")
    found = _chased(s, "t1")
    _show(found)
    assert found["CB0000Y01C017"]["shortfall_cents"] == 2352
    assert found["CB0000Y02C017"]["shortfall_cents"] == 3000
    assert "CB0000Y03C017" not in found
    ev = json.loads(found["CB0000Y01C017"]["evidence"])
    assert ev["sent_837"]["payer_resp"] == "S" and ev["sent_as"]["left_cents"] == 2352
    assert "as its secondary payer after ANTHEM answered" in found["CB0000Y01C017"]["hint"]
    s.close()


# ================================================================================================ waiting, and send weeks
@pytest.mark.parametrize("kind", KINDS)
def test_claims_not_due_yet_are_shown_waiting_with_their_due_day(tmp_path, kind):
    """(Residual: claims the 837 pass lists only at their due day were on no list meanwhile.) Two claims went to Anthem on
    November 1 and November 3 (due December 1 and 3); the remittances reach November 10. Neither is listed yet; the Data
    tab says two claims ($447.30 billed) wait for the payer's answer, the first due December 1, by payer."""
    from remitwatch import insights
    from remitwatch.detect import WAITING_837_META
    s = _store(tmp_path, kind)
    send(s, tmp_path, [clm837("CB0000W01C017", V)], "20261101")
    send(s, tmp_path, [clm837("CB0000W02C017", V)], "20261103")
    _remits(s, tmp_path, _visits(), "20261110")
    found = _chased(s, "t1")
    assert not any(k.startswith("CB0000W0") for k in found)
    w = json.loads(s.conn.execute("SELECT value FROM meta WHERE key=?", (WAITING_837_META,)).fetchone()[0])
    assert (w["n"], w["billed_cents"], w["first_due"], w["last_due"]) == (2, 44730, "2026-12-01", "2026-12-03")
    assert w["by_group"]["Anthem / BCBS"]["n"] == 2 and [c[0] for c in w["claims"]] == ["CB0000W01C017", "CB0000W02C017"]
    items = {h["key"]: h for h in insights.data_health(s.conn, "2026-11-10", "2026-11-12")}
    h = items["waiting_837"]
    assert h["ok"] and h["n"] == 2 and h["cents"] == 44730 and "2026-12-01" in h["detail"]
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_a_send_week_whose_claims_went_mostly_unanswered_stands_out(tmp_path, kind):
    """(Residual: unanswered claims grouped by send week, so a lost batch stands out; the rest of T-N13.) Ten claims went to
    Anthem in each of three weeks (September 1, 8 and 15). Anthem answered nine of each of the first two weeks and two of
    the third. The never-answered claims by send week: one, one and eight; the week of September 14 stands out (80%
    against a usual 10%) and the Data tab says so, with the claims and dollars."""
    from remitwatch import insights
    s = _store(tmp_path, kind)
    answered = []
    for w, day in enumerate(("20260901", "20260908", "20260915")):
        ids = [f"CB00{w}0K{n:02d}C017" for n in range(10)]
        send(s, tmp_path, [clm837(i, V) for i in ids], day)
        answered += ids[:9] if w < 2 else ids[:2]
    _remits(s, tmp_path, [answer(i) for i in answered], "20261001")
    _remits(s, tmp_path, _visits(), "20261110")
    _chased(s, "t1")
    got = insights.unanswered_weeks(s.conn)
    print(got)
    assert [(w["week"], w["unanswered"], w["sent"]) for w in got["weeks"]] == [("2026-08-31", 1, 10), ("2026-09-07", 1, 10), ("2026-09-14", 8, 10)]
    assert [w["week"] for w in got["stand_out"]] == ["2026-09-14"]
    items = {h["key"]: h for h in insights.data_health(s.conn, "2026-11-10", "2026-11-12")}
    h = items["unanswered_weeks"]
    assert not h["ok"] and "The week of 2026-09-14: 8 of 10 claims sent got no answer ($940.80, Anthem / BCBS 8)" in h["detail"]
    s.close()


# ================================================================================================ the story's 837 rows
@pytest.mark.parametrize("kind", KINDS)
def test_every_items_story_shows_the_837_sends_its_answers_and_the_open_balance(tmp_path, kind):
    """(Residual: story.py showed no 837 rows.) Bill CB0000T01: C017 sent to Anthem September 1, the replacement C024
    September 15, never answered (the item is C024's). Its story lists both sends (source 837: the day, the claim id, an
    original or a replacement, the 99214 billed $223.65), then the item, and a balance line that reconciles to the cent
    (charge $223.65, all of it open). Bill CB0000T02, sent to Humana as its secondary after Anthem paid $94.08 and left
    the patient $23.52, says so on its send row. On the dashboard's copy, which keeps no 837 file, the story shows the
    send the item was dated by, and says the 837 files are kept on the server."""
    from remitwatch import story
    s = _store(tmp_path, kind)
    send(s, tmp_path, [clm837("CB0000T01C017", V)], "20260901")
    send(s, tmp_path, [clm837("CB0000T01C024", V, freq="7"),
                       clm837("CB0000T02C017", V, payer=HUMANA, resp="S",
                              other={"payer": ANTHEM, "paid": 94.08, "left": 23.52, "lines": [(94.08, 23.52)]})], "20260915")
    _remits(s, tmp_path, _visits(), "20261110")
    _remits(s, tmp_path, _visits(start=60), "20261110", payer="HUMANA", payer_id="61101")
    found = _chased(s, "t1")
    st = story.item_story(s.conn, found["CB0000T01C024"]["finding_id"])
    sends = [r for r in st["rows"] if r["source"] == "837"]
    print([(r["date"], r["text"]) for r in sends])
    assert [(r["date"], r["claim_id"], r["kind"]) for r in sends] == [("2026-09-01", "CB0000T01C017", "send"),
                                                                      ("2026-09-15", "CB0000T01C024", "send")]
    assert "an original claim" in sends[0]["text"] and "a replacement (corrected) claim" in sends[1]["text"]
    assert "99214 on Sep 14, 2026 billed $223.65" in sends[1]["text"] and "own_send" in sends[1]["tags"]
    assert st["rows"][0]["source"] == "837", "the sends come first: they are the oldest events"
    assert st["reconciles"] and st["balance"]["charge_cents"] == 22365 and st["balance"]["open_cents"] == 22365
    t2 = [r for r in story.item_story(s.conn, found["CB0000T02C017"]["finding_id"])["rows"] if r["source"] == "837"]
    assert "as its secondary payer" in t2[0]["text"] and "after ANTHEM answered it (paid $94.08, left the patient $23.52)" in t2[0]["text"]
    s.conn.execute("DROP TABLE lines_837")
    s.conn.execute("DROP TABLE claims_837")
    s.conn.commit()
    st = story.item_story(s.conn, found["CB0000T01C024"]["finding_id"])
    sends = [r for r in st["rows"] if r["source"] == "837"]
    assert [(r["date"], r["claim_id"]) for r in sends] == [("2026-09-15", "CB0000T01C024")]
    assert "sent 2 times, first on Sep 15, 2026" not in sends[0]["text"] and "a replacement (corrected) claim" in sends[0]["text"]
    assert story.COPY_837_NOTE in st["notes"]
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_a_denials_story_shows_the_bill_sent_and_sent_again(tmp_path, kind):
    """A remittance item: Anthem denied bill CB0000T03's C017 (a 99214, CO-16) on October 1; the practice had sent it
    September 15 and sent it again as C024 on October 10. The denial's story shows both sends beside the 835 answer, in
    the order they happened."""
    from remitwatch import story
    s = _store(tmp_path, kind)
    send(s, tmp_path, [clm837("CB0000T03C017", V)], "20260915")
    _remits(s, tmp_path, [answer("CB0000T03C017", paid="0.00")], "20261001")
    send(s, tmp_path, [clm837("CB0000T03C024", V, freq="7")], "20261010")
    _remits(s, tmp_path, _visits(), "20261110")
    Detector(s, run_id="t1").run()
    fid = s.query("SELECT finding_id FROM findings WHERE patient_control_number='CB0000T03C017' AND line_key IS NOT NULL")[0]["finding_id"]
    rows = story.item_story(s.conn, fid)["rows"]
    print([(r["date"], r["source"], r["kind"]) for r in rows])
    seq = [(r["date"], r["source"]) for r in rows if r["source"] in ("837", "835")]
    assert seq == [("2026-09-15", "837"), ("2026-10-01", "835"), ("2026-10-10", "837")]
    s.close()


# ================================================================================================ the units residual

@pytest.mark.parametrize("kind", KINDS)
def test_a_whole_charge_co45_on_several_units_is_sized_at_the_rate_times_its_units(tmp_path, kind):
    """(Residual: a claim item sized without its units, $98 where the units say $196.) On the integration tip the only
    items the A17 auditor still finds sized for one unit of several are whole charges taken as a contractual reduction
    (CO-45, nothing allowed): detect._settle_co45_candidates sized them at the payer's rate for one unit. The payer pays
    17003 at $15.70 a unit (25 clean answers). It takes C017's 17003, billed for 13 units at $246.35, as CO-45 for the
    whole charge: the item is worth 13 x $15.70 = $204.10. Bill 501's 17003, 3 units billed at $40.00, is worth its
    charge, $40.00 (3 x $15.70 is more: a payer allows the lesser of the two)."""
    from tests.review3.r3helpers import WHO, clm, sv
    from tests.test_features import clean_visits
    from tests.test_takebacks_and_recoveries import World
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "17003", "18.95", "15.70"), "20260301")
    w.remit([clm("CB0000500C017", "1", "246.35", "0.00", sv("17003", "246.35", "0.00", 13, "CAS*CO*45*246.35~"), payer_ctl="PC1"),
             clm("CB0000501C017", "1", "40.00", "0.00", sv("17003", "40.00", "0.00", 3, "CAS*CO*45*40.00~", dos="20260402"),
                 who=WHO.replace("ELM*BO", "OAK*CY").replace("W55500302", "W55500303"), payer_ctl="PC2", dos="20260402")],
            "20260415")
    w.run("r1")
    got = sorted((r["patient_control_number"], r["procedure_code"], r["expected_cents"], r["shortfall_cents"])
                 for r in w.rows("signal='DENIED_OTHER'") if json.loads(r["evidence"] or "{}").get("whole_charge_co45"))
    print(got, [json.loads(r["evidence"])["basis"] for r in w.rows("signal='DENIED_OTHER'")])
    assert got == [("CB0000500C017", "17003", 20410, 20410), ("CB0000501C017", "17003", 4000, 4000)]
    w.store.close()


# ================================================================================================ the fix round
# The two reviews of wip/fx-837 (fx-837-r1 and fx-837-r2): their failing tests, ported, with the controls and guards the
# fixes need. Every claim id, member number, name, birth date and amount is invented.
STATE_MEDICAID = ("STATE MEDICAID", "SMCD1")


def _story_837(s, fid: str) -> list[str]:
    from remitwatch import story
    return [r["text"] for r in story.item_story(s.conn, fid)["rows"] if r["source"] == "837"]


def test_a_claims_other_payer_is_the_payer_before_it_never_one_listed_after_it():
    """(fx-837-r2-1 and r2-2, the reader.) The 837 guide asks for loop 2320 whenever another payer may pay, so a claim to
    the primary routinely lists the secondary there, with nothing adjudicated. The other payer that answered before a
    claim is the latest one whose place (SBR01) comes before the claim's own:
    - CB0000Q01 to Anthem as primary (P) lists Humana (S, nothing adjudicated): no other payer answered it;
    - CB0000Q02 to Humana as secondary (S) lists Anthem (P: paid $94.08, left the patient $23.52) and the tertiary
      (T, nothing adjudicated): Anthem's answer, not the tertiary's;
    - CB0000Q03 with no SBR01 lists Humana with nothing adjudicated: no other payer answered it;
    - CB0000Q04 with no SBR01 lists Anthem with its adjudication (AMT*D, AMT*EAF): Anthem answered before it."""
    from remitwatch.x12_837 import parse_837_bytes
    data = _file837([
        clm837("CB0000Q01C017", V, others=[{"resp": "S", "payer": HUMANA}]),
        clm837("CB0000Q02C017", V, payer=HUMANA, resp="S", others=[
            {"resp": "P", "payer": ANTHEM, "paid": 94.08, "left": 23.52, "pr": 23.52}, {"resp": "T", "payer": STATE_MEDICAID}]),
        clm837("CB0000Q03C017", V, resp="", others=[{"resp": "S", "payer": HUMANA}]),
        clm837("CB0000Q04C017", V, payer=HUMANA, resp="", others=[{"resp": "P", "payer": ANTHEM, "paid": 94.08, "left": 23.52}]),
    ])
    c = {x["patient_control_number"]: x for x in parse_837_bytes(data)["claims"]}
    keys = ("other_payer_name", "other_payer_resp", "other_paid_cents", "other_left_cents", "other_pr_cents")
    got = {k: tuple(v[x] for x in keys) for k, v in c.items()}
    print(got)
    assert c["CB0000Q01C017"]["payer_resp"] == "P"
    assert got["CB0000Q01C017"] == (None, None, None, None, None), "a primary claim's 2320 names the payer after it"
    assert got["CB0000Q02C017"] == ("ANTHEM", "P", 9408, 2352, 2352), "the tertiary listed after the claim is no answer before it"
    assert got["CB0000Q03C017"] == (None, None, None, None, None)
    assert got["CB0000Q04C017"][:4] == ("ANTHEM", "P", 9408, 2352)


@pytest.mark.parametrize("kind", KINDS)
def test_a_never_answered_primary_claim_that_lists_its_secondary_is_chased_as_the_primarys(tmp_path, kind):
    """(fx-837-r1-4 and r2-1.) CB0000Q11 went to Anthem as the primary on September 15 and lists Humana, the secondary, in
    loop 2320 with nothing adjudicated; Anthem never answers, and its remittances reach November 10. It is chased as
    Anthem's primary claim at Anthem's $117.60: its evidence has no 'sent_as', its hint never says it went to Anthem as a
    second or later payer, and its story never says Humana answered it."""
    s = _store(tmp_path, kind)
    send(s, tmp_path, [clm837("CB0000Q11C017", V, others=[{"resp": "S", "payer": HUMANA}])], "20260915")
    _remits(s, tmp_path, _visits(), "20261110")
    found = _chased(s, "t1")
    _show(found)
    f = found["CB0000Q11C017"]
    ev = json.loads(f["evidence"])
    rows = _story_837(s, f["finding_id"])
    print(ev.get("sent_837"), ev.get("sent_as"), "|", f["hint"], "|", rows)
    assert (ev.get("sent_837") or {}).get("payer_resp") == "P"
    assert "sent_as" not in ev, f"a claim sent to its primary payer is described as a later payer's send: {ev.get('sent_as')}"
    assert "second or later payer" not in (f["hint"] or "") and "answered" not in (f["hint"] or "")
    assert rows and not any("HUMANA answered" in t for t in rows), rows
    assert f["shortfall_cents"] == 11760
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_a_primary_claim_the_version_2_reader_read_is_not_called_a_second_payers_claim(tmp_path, kind):
    """(fx-837-r1-4, r2-1: the rule the item and its story apply, whatever the reader kept.) The reader of READ_VERSION 2
    kept the secondary a claim to the primary lists in loop 2320 as its 'other payer' (other_payer_name HUMANA, place S,
    nothing paid). Such a row is a claim to the primary all the same (SBR01 P): no 'sent_as', and the story never says
    Humana answered it."""
    s = _store(tmp_path, kind)
    send(s, tmp_path, [clm837("CB0000Q13C017", V, others=[{"resp": "S", "payer": HUMANA}])], "20260915")
    s.conn.execute("UPDATE claims_837 SET other_payer_name='HUMANA', other_payer_id='61101', other_payer_resp='S', read_version=2")
    s.conn.commit()
    _remits(s, tmp_path, _visits(), "20261110")
    found = _chased(s, "t1")
    f = found["CB0000Q13C017"]
    assert "sent_as" not in json.loads(f["evidence"])
    assert not any("HUMANA answered" in t for t in _story_837(s, f["finding_id"]))
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_a_secondary_claim_that_also_lists_the_tertiary_is_sized_by_what_the_primary_left(tmp_path, kind):
    """(fx-837-r2-2.) CB0000Q15 went to Humana as the secondary (SBR01 S) on September 15 after Anthem paid $94.08 and
    left the patient $23.52 (AMT*EAF); it also lists the tertiary (nothing adjudicated). Humana pays 99214 at $117.60 as a
    primary and has not answered by November 10. Humana owes at most the $23.52 Anthem left, and the hint names Anthem."""
    s = _store(tmp_path, kind)
    send(s, tmp_path, [clm837("CB0000Q15C017", V, payer=HUMANA, resp="S", others=[
        {"resp": "P", "payer": ANTHEM, "paid": 94.08, "left": 23.52, "pr": 23.52}, {"resp": "T", "payer": STATE_MEDICAID}])], "20260915")
    _remits(s, tmp_path, _visits(), "20261110", payer="HUMANA", payer_id="61101")
    found = _chased(s, "t1")
    f = found["CB0000Q15C017"]
    print(f["shortfall_cents"], "|", f["hint"])
    assert f["shortfall_cents"] == 2352
    assert "as its secondary payer after ANTHEM answered (paid $94.08)" in f["hint"]
    assert "STATE MEDICAID" not in f["hint"]
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_the_primarys_unanswered_bill_stays_chased_when_the_newer_bill_was_answered_only_as_secondary(tmp_path, kind):
    """(fx-837-r1-1.) One patient (born 1960) is seen September 14 (99214). The practice bills Anthem, the primary, on
    CB0000Q21 (September 15); Anthem never answers. It then makes the bill again as CB0000Q22 and sends it to Humana
    (September 25), and Humana answers October 20 as the SECONDARY payer (CLP02 2), paying $23.52. Anthem's remittances
    reach November 10. Anthem's answer to the visit is still missing and owed: Q21 stays chased, says Humana answered the
    visit as secondary on the other bill, and is sized at what is left once Humana's $23.52 is taken off (T-C2)."""
    s = _store(tmp_path, kind)
    who = ("SAMPLE", "PAT", "19600101")
    send(s, tmp_path, [clm837("CB0000Q21C017", V, payer=ANTHEM, member="WANTH00021", subscriber=who)], "20260915")
    send(s, tmp_path, [clm837("CB0000Q22C017", V, payer=HUMANA, member="WHUMA00022", subscriber=who)], "20260925")
    _remits(s, tmp_path, [answer("CB0000Q22C017", status="2", paid="23.52")], "20261020", payer="HUMANA", payer_id="61101")
    _remits(s, tmp_path, _visits(), "20261110")
    _remits(s, tmp_path, _visits(start=60), "20261110", payer="HUMANA", payer_id="61101")
    found = _chased(s, "t1")
    _show(found)
    assert "CB0000Q21C017" in found, (
        f"listed {sorted(found)}: Anthem, the primary, never answered the visit; Humana answered only as secondary")
    f = found["CB0000Q21C017"]
    print(f["hint"])
    assert "CB0000Q22C017" in (f["hint"] or "") and "as secondary" in (f["hint"] or "")
    assert f["shortfall_cents"] == 11760 - 2352
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_an_older_bill_gives_way_when_its_own_payer_answered_the_newer_bill_of_the_visit(tmp_path, kind):
    """(fx-837-r1-1's control.) Bill CB0000Q31 went to Anthem September 15 and got no answer; the practice made the bill
    again as CB0000Q32 and sent it to Anthem September 25, and Anthem answered Q32 on October 20 as a secondary payer.
    Anthem has answered the visit (on the newer bill): Q31 is not chased as never answered by Anthem."""
    s = _store(tmp_path, kind)
    who = ("SAMPLE", "PAT", "19600101")
    send(s, tmp_path, [clm837("CB0000Q31C017", V, member="WANTH00031", subscriber=who)], "20260915")
    send(s, tmp_path, [clm837("CB0000Q32C017", V, member="WANTH00031", subscriber=who)], "20260925")
    _remits(s, tmp_path, [answer("CB0000Q32C017", status="2", paid="23.52")], "20261020")
    _remits(s, tmp_path, _visits(), "20261110")
    found = _chased(s, "t1")
    _show(found)
    assert "CB0000Q31C017" not in found and "CB0000Q32C017" not in found
    s.close()


V_MARCH = [("99214", "223.65", "20260302")]


@pytest.mark.parametrize("kind", KINDS)
def test_a_visit_billed_on_time_keeps_its_timely_filing_when_a_late_newer_bill_carries_it(tmp_path, kind):
    """(fx-837-r1-2.) One patient's 99214 of March 2, 2026. The practice sent it to Anthem on bill CB0000K01 on March 5,
    inside Anthem's 90-day first-filing limit (May 31); Anthem never answered. In September the practice made the bill
    again as CB0000K02 and sent it September 25, past that limit; no answer either. Anthem's remittances reach November
    10. One visit, one item (K02's), and it is still recoverable: Anthem takes proof of timely filing (the March send), so
    the item is dated by Anthem's appeal window past the limit, and its basis names K01's send."""
    s = _store(tmp_path, kind)
    m = "WTIME00001"
    send(s, tmp_path, [clm837("CB0000K01C017", V_MARCH, member=m)], "20260305")
    send(s, tmp_path, [clm837("CB0000K02C017", V_MARCH, member=m)], "20260925")
    _remits(s, tmp_path, _visits(), "20261110")
    found = _chased(s, "t1")
    _show(found)
    visit = {k: v for k, v in found.items() if k in ("CB0000K01C017", "CB0000K02C017")}
    assert set(visit) == {"CB0000K02C017"}
    f = visit["CB0000K02C017"]
    print(f["appeal_deadline"], "|", f["deadline_basis"], "|", f["hint"])
    assert f["appeal_deadline"] > "2026-11-10", f"the March send was on time: {f['appeal_deadline']}, {f['deadline_basis']}"
    assert "CB0000K01C017" in f["deadline_basis"] and "2026-03-05" in f["deadline_basis"]
    assert f["work_state"] != "expired"
    s.close()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("status", ["Submitted", "Rejected"])
def test_an_exports_bill_carrying_a_visit_first_billed_on_time_keeps_its_timely_filing(tmp_path, kind, status):
    """(fx-837-r1-2 with the exports' carrier.) The same visit (99214 of March 2) went to Anthem on bill CB0000K11 on March
    5 (only the 837s know it) and was never answered. The practice made the bill again as CB0000K12, which EMA's weekly
    exports list, submitted September 25 (its 837 too): Submitted and never answered, or Rejected before it reached the
    payer. The exports' item carries the visit; the March send is proof of timely filing, so it is dated past the limit."""
    s = _store(tmp_path, kind)
    m = "WTIME00011"
    send(s, tmp_path, [clm837("CB0000K11C017", V_MARCH, member=m)], "20260305")
    from tests.test_workable import ema
    ema(s.conn, "CB0000K12C017", service_date="2026-03-02", bill_created="2026-09-25")
    ema(s.conn, "CB0000K12C017", family="claim_status", claim_status=status, claim_submission_date="2026-09-25",
        service_date="2026-03-02")
    s.conn.commit()
    send(s, tmp_path, [clm837("CB0000K12C017", V_MARCH, member=m)], "20260925")
    _remits(s, tmp_path, _visits(), "20261110")
    found = _chased(s, "t1")
    _show(found)
    assert set(k for k in found if k.startswith("CB0000K1")) == {"CB0000K12C017"}
    f = found["CB0000K12C017"]
    print(f["signal"], f["appeal_deadline"], "|", f["deadline_basis"])
    assert f["appeal_deadline"] > "2026-11-10" and "CB0000K11C017" in f["deadline_basis"]
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_work_on_the_older_bill_stays_in_view_when_the_837s_are_read_again_and_name_the_patient(tmp_path, kind):
    """(fx-837-r1-3.) Bills CB0000H01 (September 15) and CB0000H02 (September 25) carry one patient's 99214 of September
    14; Anthem answered neither. The 837s were read before the patient loop was read (no patient on file), so both bills
    are listed, and a biller starts H01 ('Called Anthem: no record of it'). Then the 837 files are read again
    (x12_837.reread_837_bytes, F6's re-read) and name the patient: the next run lists the visit once, and the biller's
    work is on the item listed (Started, with the note), not hidden on a stale row."""
    from remitwatch import x12_837
    from remitwatch.actions import apply_status
    s = _store(tmp_path, kind)
    m, who = "WREAD00001", ("SAMPLE", "PAT", "19600101")
    send(s, tmp_path, [clm837("CB0000H01C017", V, member=m, subscriber=who)], "20260915")
    send(s, tmp_path, [clm837("CB0000H02C017", V, member=m, subscriber=who)], "20260925")
    s.conn.execute(f"UPDATE claims_837 SET {', '.join(c + '=NULL' for c in x12_837.EXTRA_CLAIM_COLS)}")
    s.conn.commit()
    _remits(s, tmp_path, _visits(), "20261110")
    found = _chased(s, "t1")
    _show(found)
    assert {"CB0000H01C017", "CB0000H02C017"} <= set(found), "with no patient on file the two bills are two items"
    apply_status(s.conn, found["CB0000H01C017"]["finding_id"], "in_progress", "Called Anthem: no record of it", actor="biller")
    s.conn.commit()
    for p in sorted(tmp_path.glob("*.837")):
        x12_837.reread_837_bytes(s.conn, p.read_bytes())
    found = _chased(s, "t2")
    _show(found)
    visit = {k: v for k, v in found.items() if k in ("CB0000H01C017", "CB0000H02C017")}
    assert set(visit) == {"CB0000H02C017"}, f"one visit, one item: {sorted(visit)}"
    item = visit["CB0000H02C017"]
    assert item["status"] == "in_progress" and "no record of it" in (item.get("notes") or ""), (item["status"], item.get("notes"))
    found = _chased(s, "t3")
    assert found["CB0000H02C017"]["status"] == "in_progress", "the carried work stays on the next run"
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_a_newer_item_a_person_worked_keeps_its_own_work_when_an_older_bill_of_the_visit_yields(tmp_path, kind):
    """(fx-837-r1-3's guard: a person's decision is never overwritten.) The same two bills, both listed before the patient
    was on file: one biller starts H11 ('Called Anthem: no record of it'), another writes on H12 ('Sent the claim again by
    fax'). Once the patient is on file the visit is listed once, on H12, with H12's own work, not H11's."""
    from remitwatch import x12_837
    from remitwatch.actions import apply_status
    s = _store(tmp_path, kind)
    m, who = "WREAD00011", ("SAMPLE", "PAT", "19600101")
    send(s, tmp_path, [clm837("CB0000H11C017", V, member=m, subscriber=who)], "20260915")
    send(s, tmp_path, [clm837("CB0000H12C017", V, member=m, subscriber=who)], "20260925")
    s.conn.execute(f"UPDATE claims_837 SET {', '.join(c + '=NULL' for c in x12_837.EXTRA_CLAIM_COLS)}")
    s.conn.commit()
    _remits(s, tmp_path, _visits(), "20261110")
    found = _chased(s, "t1")
    apply_status(s.conn, found["CB0000H11C017"]["finding_id"], "in_progress", "Called Anthem: no record of it", actor="biller")
    apply_status(s.conn, found["CB0000H12C017"]["finding_id"], "open", "Sent the claim again by fax", actor="biller")
    s.conn.commit()
    for p in sorted(tmp_path.glob("*.837")):
        x12_837.reread_837_bytes(s.conn, p.read_bytes())
    found = _chased(s, "t2")
    item = found["CB0000H12C017"]
    assert "CB0000H11C017" not in found
    assert item["status"] == "open" and item["notes"] == "Sent the claim again by fax", (item["status"], item["notes"])
    s.close()


def _two_claims_one_subscriber_loop() -> bytes:
    """One 837P: one subscriber loop with two claims. The first (CB0000Z01C017, to Anthem) names another payer in its
    other-subscriber loops (2320; 2330A NM1*IL with that plan's member id; 2330B NM1*PR Humana; 2330D NM1*82 with no NPI,
    as the guide allows); the second (CB0000Z02C017) goes to Anthem too."""
    return ("ISA*00*          *00*          *ZZ*999999999      *ZZ*TRIZETTO       *260915*1200*^*00501*000000001*0*P*:~"
            "GS*HC*999999999*TRIZETTO*20260915*1200*1*X*005010X222A1~ST*837*0001*005010X222A1~BHT*0019*00*1*20260915*1200*CH~"
            "HL*1**20*1~NM1*85*2*TEST DERMATOLOGY*****XX*1777777772~"
            "HL*2*1*22*0~SBR*P*18*GRP1******CI~NM1*IL*1*SAMPLE*PAT****MI*WMAIN00001~DMG*D8*19600101*F~NM1*PR*2*ANTHEM*****PI*00660~"
            "CLM*CB0000Z01C017*223.65***11:B:1*Y*A*Y*Y~NM1*82*1*DOC*TEST****XX*1999999999~"
            "SBR*S*18*GRP2******CI~OI***Y***Y~NM1*IL*1*SAMPLE*PAT****MI*WOTHER0001~NM1*PR*2*HUMANA*****PI*61101~NM1*82*1~"
            "LX*1~SV1*HC:99214*223.65*UN*1***1~DTP*472*D8*20260914~"
            "CLM*CB0000Z02C017*80.00***11:B:1*Y*A*Y*Y~NM1*82*1*DOC*TEST****XX*1999999999~"
            "LX*1~SV1*HC:99213*80.00*UN*1***1~DTP*472*D8*20260914~"
            "SE*20*0001~GE*1*1~IEA*1*000000001~").encode()


@pytest.mark.parametrize("kind", KINDS)
def test_reading_an_837_again_repairs_what_the_first_reader_took_from_the_other_payers_loop(tmp_path, kind):
    """(fx-837-r1-5.) The reader before the other payer's loop was read let the first claim's other-payer loops change what
    it wrote for the claim after it in the same subscriber loop (CB0000Z02 got payer HUMANA 61101 and member WOTHER0001,
    and CB0000Z01 and its line lost the rendering NPI to 2330D's NM1*82). The store keeps those rows as written; reading
    the file again is the one chance to put them right: after it, CB0000Z02 is Anthem's claim under the subscriber's own
    member id, and CB0000Z01 and its line name the rendering clinician, as a fresh read of the same file writes them."""
    from remitwatch import x12_837
    s = _store(tmp_path, kind)
    data = _two_claims_one_subscriber_loop()
    p = tmp_path / "c0001.837"
    p.write_bytes(data)
    s.ingest_835_paths([str(p)])
    c = s.conn
    c.execute("UPDATE claims_837 SET payer_name='HUMANA', payer_id='61101', subscriber_id='WOTHER0001' WHERE patient_control_number='CB0000Z02C017'")
    c.execute("UPDATE claims_837 SET rendering_npi=NULL WHERE patient_control_number='CB0000Z01C017'")
    c.execute("UPDATE lines_837 SET rendering_npi=NULL WHERE patient_control_number='CB0000Z01C017'")
    c.execute(f"UPDATE claims_837 SET {', '.join(k + '=NULL' for k in x12_837.EXTRA_CLAIM_COLS)}")
    c.commit()
    assert x12_837.files_to_reread(c), "the file is listed to be read again"
    x12_837.reread_837_bytes(c, data)
    z2 = c.execute("SELECT payer_name, payer_id, subscriber_id FROM claims_837 WHERE patient_control_number='CB0000Z02C017'").fetchone()
    z1 = c.execute("SELECT rendering_npi FROM claims_837 WHERE patient_control_number='CB0000Z01C017'").fetchone()
    l1 = c.execute("SELECT rendering_npi FROM lines_837 WHERE patient_control_number='CB0000Z01C017'").fetchone()
    assert tuple(z2) == ("ANTHEM", "00660", "WMAIN00001"), f"CB0000Z02 still carries the other payer's loop: {tuple(z2)}"
    assert z1[0] == "1999999999" and l1[0] == "1999999999", "CB0000Z01's rendering clinician is still missing"
    assert x12_837.files_to_reread(c) == []
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_a_father_and_son_of_one_name_on_one_subscriber_id_are_two_patients(tmp_path, kind):
    """(fx-837-r1-6.) A father (the subscriber, born 1970) and his son of the same name (a dependent on the father's plan,
    born 2005: the 837's patient loop names him, with his own birth date) are both seen September 14 for a 99214. The
    father's bill CB0000J01 went to Anthem September 15, the son's CB0000J02 September 16; Anthem answered neither. Two
    people, two unpaid visits: both are chased (their birth dates tell them apart, though their keyed names and the
    subscriber id are the same)."""
    s = _store(tmp_path, kind)
    sub = "WFAMJ00001"
    send(s, tmp_path, [clm837("CB0000J01C017", V, member=sub, subscriber=("SAMPLE", "JOHN", "19700101"))], "20260915")
    send(s, tmp_path, [clm837("CB0000J02C017", V, member=sub, subscriber=("SAMPLE", "JOHN", "19700101"),
                              patient=("SAMPLE", "JOHN", "20050505"))], "20260916")
    _remits(s, tmp_path, _visits(), "20261110")
    found = _chased(s, "t1")
    _show(found)
    assert {"CB0000J01C017", "CB0000J02C017"} <= set(found)
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_one_patients_bills_with_and_without_a_birth_date_are_one_visit(tmp_path, kind):
    """(fx-837-r1-6's control.) One patient's visit billed twice under one subscriber id: the first bill (CB0000J11,
    September 15) gives the birth date, the bill made again (CB0000J12, September 25) does not. With a birth date on one
    side only, the name and the member id still say one patient: the visit is chased once, on J12."""
    s = _store(tmp_path, kind)
    sub = "WFAMJ00011"
    send(s, tmp_path, [clm837("CB0000J11C017", V, member=sub, subscriber=("SAMPLE", "JOHN", "19700101"))], "20260915")
    send(s, tmp_path, [clm837("CB0000J12C017", V, member=sub, subscriber=("SAMPLE", "JOHN", None))], "20260925")
    _remits(s, tmp_path, _visits(), "20261110")
    found = _chased(s, "t1")
    _show(found)
    assert {k for k in found if k.startswith("CB0000J1")} == {"CB0000J12C017"}
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_the_payers_own_earlier_answer_is_not_another_payers_answer(tmp_path, kind):
    """(fx-837-r2-3: answers-4 with T-C2.) Bill CB0000R01 (only the 837s know it): C017 went to Anthem August 1; Anthem
    processed it as a secondary payer August 20 (CLP02 2, OA-23, nothing paid). The practice sent the bill again to Anthem
    as C024 September 1; nothing answered C024 by November 10. C024 is chased (answers-4), but Anthem's answer to the
    older submission, before the send, is not 'another payer' answering it: the item never says only Anthem answered
    while the primary's remittance, from Anthem, is missing. Its hint names Anthem's earlier answer."""
    s = _store(tmp_path, kind)
    send(s, tmp_path, [clm837("CB0000R01C017", V)], "20260801")
    _remits(s, tmp_path, [answer("CB0000R01C017", status="2", paid="0.00", cas="CAS*OA*23*223.65~")], "20260820")
    send(s, tmp_path, [clm837("CB0000R01C024", V, freq="7")], "20260901")
    _remits(s, tmp_path, _visits(), "20261110")
    found = _chased(s, "t1")
    f = found["CB0000R01C024"]
    ev = json.loads(f["evidence"])
    print(f["title"], "|", f["hint"], "|", ev.get("secondary_answer"))
    assert "secondary_answer" not in ev, ev.get("secondary_answer")
    assert f"only {f['payer_group']} answered" not in f["title"], f["title"]
    assert "CB0000R01C017" in (f["hint"] or "") and "2026-08-20" in (f["hint"] or "")
    assert f["shortfall_cents"] == 11760
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_what_the_payer_paid_on_an_earlier_submission_comes_off_the_resend(tmp_path, kind):
    """(fx-837-r2-3: the money stays right.) The same bill, but Anthem paid $23.52 as a secondary payer on C017 (August
    20) before the practice sent C024 (September 1). C024 is chased, never as only another payer's answer, sized at
    what is left of Anthem's $117.60 once its own $23.52 is taken off; the hint says so."""
    s = _store(tmp_path, kind)
    send(s, tmp_path, [clm837("CB0000R11C017", V)], "20260801")
    _remits(s, tmp_path, [answer("CB0000R11C017", status="2", paid="23.52")], "20260820")
    send(s, tmp_path, [clm837("CB0000R11C024", V, freq="7")], "20260901")
    _remits(s, tmp_path, _visits(), "20261110")
    found = _chased(s, "t1")
    f = found["CB0000R11C024"]
    print(f["shortfall_cents"], "|", f["hint"])
    assert "secondary_answer" not in json.loads(f["evidence"])
    assert f["shortfall_cents"] == 11760 - 2352
    assert "paying $23.52 (taken off this item)" in f["hint"]
    s.close()


def _rates_17110() -> list[str]:
    """Anthem paying 17110 at $80 on other claims: its usual rate."""
    from tests.test_features import claim
    return [claim(600 + i, svc("17110", "200.00", "80.00", "80.00", "CAS*CO*45*120.00~")) for i in range(25)]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("ema_mods,mods", [("59", "59"), (None, "59"), (None, None)])
def test_an_older_837_bill_yields_to_an_exports_bill_only_for_the_same_service(tmp_path, kind, ema_mods, mods):
    """(fx-837-r2-4.) Two bills carry one service only when the date, code, part and the modifiers that mark a separate
    service agree. Bill CB0000M71 (837 only, September 15) carries one patient's 17110 of September 14 with no modifier.
    Bill CB0000M72, which EMA's weekly exports chase (its 837 on file too, sent September 25), carries a 17110 of the same
    day with modifier 59 on its 837 (and on EMA's charge row, or not: the 837 is what was sent): another lesion, another
    service. No payer answered either: both are owed, so M71 stays chased. (Control, no modifier anywhere: the same
    service billed again: only M72.)"""
    from tests.test_workable import ema
    s = _store(tmp_path, kind)
    member = "WDUP000071"
    line = [("17110", "200.00", "20260914")]
    send(s, tmp_path, [clm837("CB0000M71C017", line, member=member)], "20260915")
    ema(s.conn, "CB0000M72C017", cpt="17110", charges_cents=20000, modifiers=ema_mods, bill_created="2026-09-25")
    ema(s.conn, "CB0000M72C017", family="claim_status", claim_status="Submitted", claim_submission_date="2026-09-25")
    s.conn.commit()
    send(s, tmp_path, [clm837("CB0000M72C017", [("17110", "200.00", "20260914", mods)] if mods else line, member=member)], "20260925")
    _remits(s, tmp_path, _rates_17110() + _visits(), "20261110")
    found = _chased(s, "t1")
    print({k: (v["source"], v["shortfall_cents"], v["hint"]) for k, v in found.items()})
    assert "CB0000M72C017" in found
    if mods:
        assert "CB0000M71C017" in found, "M71's 17110 gave way to the exports' 17110-59 of another lesion"
    else:
        assert "CB0000M71C017" not in found
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_the_exports_carrier_keeps_its_lines_separate_service_modifiers_without_an_837(tmp_path, kind):
    """(fx-837-r2-4, when the exports' claim's 837 is not on file.) EMA's weekly exports chase CB0000M75, which no 837 on
    file carries: a 17110 of September 14 with modifier 59 and a plain 17110 the same day. Its item is then the only record
    of its lines the one-visit rule reads: as a carrier of a visit, its two lines keep their separate-service modifiers
    (59 on one, none on the other), so a plain 17110 on an older bill matches only the plain one."""
    from remitwatch.detect import _visit_line
    from tests.test_workable import ema
    s = _store(tmp_path, kind)
    ema(s.conn, "CB0000M75C017", cpt="17110", charges_cents=20000, modifiers="59", bill_created="2026-09-25")
    ema(s.conn, "CB0000M75C017", cpt="17110", charges_cents=20000, bill_created="2026-09-25")
    ema(s.conn, "CB0000M75C017", family="claim_status", claim_status="Submitted", claim_submission_date="2026-09-25")
    s.conn.commit()
    _remits(s, tmp_path, _rates_17110() + _visits(), "20261110")
    det = Detector(s, run_id="t1")
    det.run()
    got = det._837_export_carriers({})
    print(got)
    assert sorted(_visit_line(x) for x in got["CB0000M75C017"]["lines"]) == [("2026-09-14", "17110", "", ()),
                                                                            ("2026-09-14", "17110", "", ("59",))]
    s.close()


def paid_as(claim_id: str, member: str, last: str, first: str) -> str:
    """Anthem's answer to a claim, naming the patient (NM1*QC) as the payer has it, with the member id: 99214 of
    2026-09-14 paid $117.60 as primary."""
    return (f"CLP*{claim_id}*1*223.65*117.60*0.00*12*P{claim_id[4:9]}*11~NM1*QC*1*{last}*{first}****MI*{member}~"
            f"NM1*82*2*TEST PROVIDER*****XX*{NPI}~DTM*232*20260914~"
            + svc("99214", "223.65", "117.60", "117.60", "CAS*CO*45*106.05~", dos="20260914"))


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("on_837,on_835", [(("O'TESTER", "SEAN"), ("OTESTER", "SEAN")),
                                           (("MÜLLERTEST", "JOSÉ"), ("MULLERTEST", "JOSE")),
                                           (("SAMPLE", "PAT"), ("SAMPLE", "PAT"))])
def test_a_visit_paid_under_another_bill_is_paid_whatever_form_the_payer_writes_the_name_in(tmp_path, kind, on_837, on_835):
    """(fx-837-r2-5: T-N10 with the sibling rule.) One patient's visit of September 14 (99214) was paid by Anthem as primary
    on bill CB0000N01 (October 1); the remittance writes the patient's name as the payer has it. The practice had made the
    bill again as CB0000N02 (sent September 15, the same member id; its 837 names the patient as the practice has it),
    and no payer answered that bill. The visit is paid: N02 is not chased. The cases: an apostrophe the payer drops,
    accents the payer drops, and the control with one form on both."""
    s = _store(tmp_path, kind)
    member = "WNAME00001"
    send(s, tmp_path, [clm837("CB0000N02C017", V, member=member, subscriber=on_837 + (None,))], "20260915")
    _remits(s, tmp_path, [paid_as("CB0000N01C017", member, *on_835)], "20261001")
    _remits(s, tmp_path, _visits(), "20261110")
    found = _chased(s, "t1")
    assert "CB0000N02C017" not in found, f"the visit was paid on CB0000N01 for the same person ({on_837} / {on_835})"
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_a_siblings_paid_visit_under_another_bill_does_not_pay_the_other_siblings_visit(tmp_path, kind):
    """(fx-837-r2-5's guard.) Two children on one parent's plan (one subscriber id), seen September 14 for a 99214. Child
    A's visit was paid on bill CB0000N11 (the remittance names child A); child B's bill CB0000N12 (its 837's patient loop
    names child B) was never answered. Different first names: child A's payment does not pay child B's visit."""
    s = _store(tmp_path, kind)
    sub = "WFAMN00011"
    send(s, tmp_path, [clm837("CB0000N12C017", V, member=sub, patient=("SAMPLE", "CHILDB", "20170704"))], "20260915")
    _remits(s, tmp_path, [paid_as("CB0000N11C017", sub, "SAMPLE", "CHILDA")], "20261001")
    _remits(s, tmp_path, _visits(), "20261110")
    found = _chased(s, "t1")
    assert "CB0000N12C017" in found
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_an_older_bill_stays_given_way_once_the_newer_bill_of_the_visit_is_answered_with_a_denial(tmp_path, kind):
    """(fx-837-r2-6.) One patient's visit of September 14 (99214): bill CB0000M81 (837 only) went to Anthem September 15
    and got no answer; the practice made the bill again as CB0000M82 and sent it September 25. Run 1 (remittances to
    October 20): M81 gives way to M82, which is not due yet. Then Anthem denies M82 (CO-16, October 30): the visit's
    answer is on the newest bill. Run 2: M81, the bill the practice already replaced, does not come back as a
    never-answered claim beside M82's denial."""
    s = _store(tmp_path, kind)
    member = "WDUP000081"
    send(s, tmp_path, [clm837("CB0000M81C017", V, member=member)], "20260915")
    send(s, tmp_path, [clm837("CB0000M82C017", V, member=member)], "20260925")
    _remits(s, tmp_path, _visits(), "20261020")
    first = _chased(s, "t1")
    assert "CB0000M81C017" not in first
    _remits(s, tmp_path, [answer("CB0000M82C017", status="1", paid="0.00")], "20261030")
    _remits(s, tmp_path, _visits(start=40), "20261110")
    second = _chased(s, "t2")
    m82 = [dict(r) for r in s.query("SELECT signal, status FROM findings WHERE patient_control_number='CB0000M82C017' AND COALESCE(stale,0)=0")]
    print(sorted(second), m82)
    assert m82, "control: M82's denial is an item"
    assert "CB0000M81C017" not in second, "the visit is chased twice (M81's no-answer and M82's denial)"
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_the_work_on_an_older_bill_passes_to_the_denial_of_the_newer_bill_of_the_visit(tmp_path, kind):
    """(fx-837-r2-6: the older bill's work goes to the answer's item.) Bill CB0000M91 (837 only, September 15) is listed
    as never answered and a biller starts it. The practice makes the bill again as CB0000M92 (September 25), and Anthem
    denies M92 (CO-16, October 30). The next run lists M92's denial, Started, with the note; M91 is not chased."""
    from remitwatch.actions import apply_status
    s = _store(tmp_path, kind)
    member = "WDUP000091"
    send(s, tmp_path, [clm837("CB0000M91C017", V, member=member)], "20260915")
    _remits(s, tmp_path, _visits(), "20261020")
    found = _chased(s, "t1")
    assert set(found) == {"CB0000M91C017"}
    apply_status(s.conn, found["CB0000M91C017"]["finding_id"], "in_progress", "Called Anthem: no record of it", actor="biller")
    s.conn.commit()
    send(s, tmp_path, [clm837("CB0000M92C017", V, member=member)], "20260925")
    _remits(s, tmp_path, [answer("CB0000M92C017", status="1", paid="0.00")], "20261030")
    _remits(s, tmp_path, _visits(start=40), "20261110")
    found = _chased(s, "t2")
    assert "CB0000M91C017" not in found
    m92 = [dict(r) for r in s.query("SELECT signal, status, notes FROM findings WHERE patient_control_number='CB0000M92C017' "
                                    "AND COALESCE(stale,0)=0")]
    print(m92)
    assert any(r["status"] == "in_progress" and "no record of it" in (r["notes"] or "") for r in m92), m92
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_a_send_weeks_unanswered_claims_are_never_more_than_the_claims_it_measures_them_against(tmp_path, kind):
    """(fx-837-r2-7.) Ten claims went to Anthem in each of the weeks of August 31 and September 7 (one of each week got no
    answer), and two in the week of September 14 (both answered). EMA's weekly exports also list six claims submitted
    September 15 that no 837 carried, and no payer answered them. The week of September 14 is measured against the claims
    the 837s show sent that week: the Data tab never says that more claims of a week went unanswered than were sent."""
    from remitwatch import insights
    from tests.test_workable import ema
    s = _store(tmp_path, kind)
    answered = []
    for w, (day, n) in enumerate((("20260901", 10), ("20260908", 10), ("20260915", 2))):
        ids = [f"CB00{w}0K{i:02d}C017" for i in range(n)]
        send(s, tmp_path, [clm837(i, V) for i in ids], day)
        answered += ids[:9] if w < 2 else ids
    for i in range(6):
        cid = f"CB0009P{i:02d}C017"
        ema(s.conn, cid, bill_created="2026-09-15")
        ema(s.conn, cid, family="claim_status", claim_status="Submitted", claim_submission_date="2026-09-15")
    s.conn.commit()
    _remits(s, tmp_path, [answer(i) for i in answered], "20261001")
    _remits(s, tmp_path, _visits(), "20261110")
    _chased(s, "t1")
    got = insights.unanswered_weeks(s.conn)
    print(got)
    for w in got["weeks"]:
        assert w["sent"] is not None and w["unanswered"] <= w["sent"], w
    items = {h["key"]: h for h in insights.data_health(s.conn, "2026-11-10", "2026-11-12")}
    h = items.get("unanswered_weeks")
    assert h is None or h["ok"], h["detail"]
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_a_send_week_counts_every_listed_claim_sent_before_the_400_day_window(tmp_path, kind):
    """(fx-837-r2-7: the same span on both sides.) A claim sent in March 2025, more than 400 days before the newest
    remittance (November 10, 2026), is listed as never answered (the remittances on file begin in February 2025). Its send
    week is measured against the claims the 837s show sent that week, not left without a count."""
    from remitwatch import insights
    s = _store(tmp_path, kind)
    _remits(s, tmp_path, _visits(n=3), "20250201")
    send(s, tmp_path, [clm837("CB0000O01C017", [("99214", "223.65", "20250303")])], "20250305")
    _remits(s, tmp_path, _visits(start=40), "20261110")
    found = _chased(s, "t1")
    assert "CB0000O01C017" in found
    got = insights.unanswered_weeks(s.conn)
    print(got)
    wk = [w for w in got["weeks"] if w["week"] == "2025-03-03"]
    assert wk and wk[0]["sent"] == 1 and wk[0]["unanswered"] == 1, got
    s.close()


def test_every_except_block_the_fixer_added_logs_through_runlog_caught():
    """(fx-837-r2-8; CLAUDE.md: never catch an exception without logging it.) Every except block in the functions of the
    837 pass, its reader, its send-week view and its story rows either re-raises or calls runlog.caught."""
    import ast
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    new = {"remitwatch/detect.py": ("_finish_waiting", "_837_export_carriers", "_837_send_info", "_837_patients", "_837_one_visit",
                                    "_837_resends", "_note_waiting", "_run_837", "run_837_open_claims", "_837_paid_elsewhere",
                                    "_emit_open_claim", "price_unanswered_lines", "_open_claim_dates", "_837_answered_carriers",
                                    "_837_answer_lines", "_redate_carrier", "_hand_to_answers"),
           "remitwatch/insights.py": ("unanswered_weeks", "_waiting_837", "_unanswered_weeks_item"),
           "remitwatch/x12_837.py": ("parse_837_bytes", "ensure_schema", "files_to_reread", "reread_837_bytes", "_cas_pr", "_resp_rank",
                                     "_payers_before", "_svd_owner", "later_payers_send"),
           "remitwatch/story.py": ("_rows_837",)}

    def logs(h: ast.ExceptHandler) -> bool:
        return any((isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "caught"
                    and isinstance(n.func.value, ast.Name) and n.func.value.id == "runlog") or isinstance(n, ast.Raise)
                   for st in h.body for n in ast.walk(st))
    silent = []
    for rel, names in new.items():
        with open(os.path.join(root, rel), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name in names:
                for h in ast.walk(fn):
                    if isinstance(h, ast.ExceptHandler) and not logs(h):
                        silent.append(f"{rel}:{h.lineno} {fn.name}")
    assert not silent, "except blocks with no runlog.caught: " + "; ".join(sorted(set(silent)))
