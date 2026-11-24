"""
The 837 claim files as a source of sends (plans/design_837_never_answered.md; the catch-up trace's T-I2, T-I4, T-N9,
T-N10, T-N11, T-N13 and the release's T-C2 and CH-8):

- a claim the practice sent (an 837 on file) that no payer answered is chased even when EMA's weekly exports do not list
  it: one NO_ANSWER item of its own kind (id signal 837_OPEN_CLAIM), counted from its newest send, priced at the payer's
  own rate for each line and component, capped at the line's charge;
- a claim EMA's exports already chase keeps that item, dated by the 837 where EMA gives no date or a later send;
- a denial whose bill went out again to the denying payer is worked: it waits for that answer, it is not work now;
- the Data tab names a payer whose 837 claims never get an ERA, and the weeks one feed shows sends the other does not.

Every claim, member number, name, amount and date here is invented (tests/test_remitwatch.remit, tests/test_features.svc
and claim, tests/test_workable.ema, tests/ema_fixtures). Each test runs on the server's store file and on the compact
in-memory store the serverless cycle builds and analyses.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch.detect import Detector, _fid  # noqa: E402
from remitwatch.store import Store  # noqa: E402
from tests.test_features import NPI, claim, svc  # noqa: E402
from tests.test_remitwatch import remit  # noqa: E402

KINDS = ["file", "compact"]
ANTHEM = ("ANTHEM", "00660")
HUMANA = ("HUMANA", "61101")
VA = ("Optum Unitedhealth Care", "VACCN")


def _store(tmp_path, kind: str) -> Store:
    return Store(str(tmp_path / "s.sqlite")) if kind == "file" else Store(":memory:", compact=True)


def _remits(store: Store, tmp_path, claims: list[str], pay_date: str, payer: str = "ANTHEM", payer_id: str = "ANTHM") -> None:
    """Remittances holding these claims (20 to a file), read the way the feeds read them."""
    paths = []
    for chunk in [claims[i:i + 20] for i in range(0, len(claims), 20)]:
        n = len(list(tmp_path.glob("*.RMT")))
        p = tmp_path / f"r{n:04d}.RMT"
        p.write_bytes(remit("".join(chunk), "100.00", payer=payer, payer_id=payer_id, seg_count=99, isa_ctrl=f"{n + 1:09d}", pay_date=pay_date))
        paths.append(str(p))
    store.ingest_835_paths(paths)


def member_of(claim_id: str) -> str:
    """The invented member id of the patient on a bill (one patient per bill)."""
    return f"W{claim_id[4:9]}00"


def clm837(claim_id: str, lines: list[tuple], payer: tuple = ANTHEM, freq: str = "1", member: str | None = None) -> str:
    """One subscriber loop of an 837P and its claim: the payer it goes to (NM1*PR name and payer id), the member (NM1*IL),
    the claim (CLM01 the claim id, CLM05-3 its frequency: 1 an original, 7 a replacement, 8 a void) and its service lines
    (code, charge in dollars, date of service CCYYMMDD, modifiers 'A:B', units)."""
    member = member or member_of(claim_id)
    total = sum(float(ln[1]) for ln in lines)
    out = (f"HL*2*1*22*0~SBR*P*18*GRP1******CI~NM1*IL*1*SAMPLE*PAT****MI*{member}~NM1*PR*2*{payer[0]}*****PI*{payer[1]}~"
           f"CLM*{claim_id}*{total:.2f}***11:B:{freq}*Y*A*Y*Y~")
    for n, ln in enumerate(lines, 1):
        code, charge, dos = ln[0], ln[1], ln[2]
        mods = ln[3] if len(ln) > 3 else ""
        units = ln[4] if len(ln) > 4 else 1
        out += f"LX*{n}~SV1*HC:{code}{':' + mods if mods else ''}*{charge}*UN*{units}***1~DTP*472*D8*{dos}~"
    return out


def send(store: Store, tmp_path, claims: list[str], bht: str) -> None:
    """An 837P file made (and sent) on `bht` (CCYYMMDD, BHT04) holding these claims, read the way the feeds read it."""
    n = len(list(tmp_path.glob("*.837"))) + 1
    st = (f"ST*837*{n:04d}*005010X222A1~BHT*0019*00*{n}*{bht}*1200*CH~HL*1**20*1~NM1*85*2*TEST DERMATOLOGY*****XX*1777777772~"
          + "".join(claims) + f"SE*99*{n:04d}~")
    data = (f"ISA*00*          *00*          *ZZ*999999999      *ZZ*TRIZETTO       *{bht[2:]}*1200*^*00501*{n:09d}*0*P*:~"
            f"GS*HC*999999999*TRIZETTO*{bht}*1200*{n}*X*005010X222A1~" + st + f"GE*1*{n}~IEA*1*{n:09d}~")
    p = tmp_path / f"c{n:04d}.837"
    p.write_bytes(data.encode())
    stats = store.ingest_835_paths([str(p)])
    assert stats.get("claims_837") == len(claims), stats


def answer(claim_id: str, status: str = "1", paid: str = "117.60", dos: str = "20260914", code: str = "99214", charge: str = "223.65",
           cas: str | None = None) -> str:
    """A payer's answer to a claim (CLP02 `status`): one line paid `paid` (a secondary's answer carries OA-23)."""
    if cas is None:
        rest = float(charge) - float(paid)
        cas = (f"CAS*OA*23*{rest:.2f}~" if status in ("2", "3") else (f"CAS*CO*45*{rest:.2f}~" if float(paid) > 0 else f"CAS*CO*16*{charge}~"))
    return (f"CLP*{claim_id}*{status}*{charge}*{paid}*0.00*12*P{claim_id[4:9]}*11~NM1*QC*1*SAMPLE*PAT****MI*{member_of(claim_id)}~"
            f"NM1*82*2*TEST PROVIDER*****XX*{NPI}~DTM*232*{dos}~"
            + svc(code, charge, paid, paid if float(paid) > 0 else None, cas, dos=dos))


def _visits(n: int = 25, start: int = 1) -> list[str]:
    """Anthem paying 99214 at $117.60 on other claims: its usual rate, and remittances that reach the day they are dated."""
    return [claim(i, svc("99214", "223.65", "117.60", "117.60", "CAS*CO*45*106.05~")) for i in range(start, start + n)]


def _chased(s: Store, run: str, today: str | None = None) -> dict[str, dict]:
    Detector(s, run_id=run, today=today).run()
    return {r["patient_control_number"]: dict(r) for r in s.query(
        "SELECT * FROM findings WHERE signal IN ('NO_ANSWER','REJECTED_PRE_PAYER') AND COALESCE(stale,0)=0")}


V = [("99214", "223.65", "20260914")]


# --------------------------------------------------------------------------- a claim only the 837s know (T-I2)

@pytest.mark.parametrize("kind", KINDS)
def test_a_claim_only_the_837s_know_is_chased_until_a_payer_answers_it(tmp_path, kind):
    """The 837s show these claims sent; no EMA export lists any of them; the remittances reach November 10:
    - N01, sent to Anthem September 15 and answered by no payer: one NO_ANSWER item of its own kind (837_OPEN_CLAIM),
      dated from that send, at Anthem's usual $117.60 (before, no item: the never-answered pass read only EMA's exports).
    - N02, answered by Anthem October 1: not listed. N03, sent to Anthem, processed as primary by Humana: answered.
    - N04, sent to Anthem, answered only by Humana as secondary ($23.52): listed, saying the primary's remittance is
      missing, sized at Anthem's rate less what Humana paid (T-C2).
    - N05: C017 sent September 1 and C024 (the same bill) September 20, answered October 10: nothing listed.
    - N06: sent September 1, voided September 10 (its newest send has frequency 8): not listed.
    - N07: sent three times (C017 twice, C024 on September 20), never answered: one item, on C024, from September 20.
    - N08: sent November 1, not due by November 10. N09: sent to Humana itself, which answered as secondary: answered.
    - N10: sent to Anthem; the same bill's C017 was denied by Anthem on August 20, and C024 went out after that denial:
      the denial's work (waiting for that answer), not a second chase.
    - N11: a post-operative visit billed at $0 (99024), never answered: nothing is owed on it. Not listed. N12: a 99214
      beside a $0 99024: listed, with the 99214 alone."""
    s = _store(tmp_path, kind)
    send(s, tmp_path, [clm837("CB0000N01C017", V), clm837("CB0000N02C017", V), clm837("CB0000N03C017", V), clm837("CB0000N04C017", V),
                       clm837("CB0000N06C017", V), clm837("CB0000N07C017", V)], "20260901")
    send(s, tmp_path, [clm837("CB0000N05C017", V), clm837("CB0000N06C017", V, freq="8"), clm837("CB0000N07C017", V)], "20260910")
    send(s, tmp_path, [clm837("CB0000N01C017", V), clm837("CB0000N09C017", V, payer=HUMANA),
                       clm837("CB0000N11C017", [("99024", "0.00", "20260914")]),
                       clm837("CB0000N12C017", V + [("99024", "0.00", "20260914")])], "20260915")
    send(s, tmp_path, [clm837("CB0000N05C024", V, freq="7"), clm837("CB0000N07C024", V, freq="7"), clm837("CB0000N10C024", V, freq="7")],
         "20260920")
    send(s, tmp_path, [clm837("CB0000N08C017", V)], "20261101")
    _remits(s, tmp_path, [answer("CB0000N10C017", status="4", paid="0.00")], "20260820")
    _remits(s, tmp_path, [answer("CB0000N02C017"), answer("CB0000N05C024")], "20261001")
    _remits(s, tmp_path, [answer("CB0000N03C017"), answer("CB0000N04C017", status="2", paid="23.52"),
                          answer("CB0000N09C017", status="2", paid="23.52")], "20261020", payer="HUMANA", payer_id="HUMNA")
    _remits(s, tmp_path, _visits(), "20261110")
    found = _chased(s, "t1")
    print("listed:", sorted(found))
    assert set(found) == {"CB0000N01C017", "CB0000N04C017", "CB0000N07C024", "CB0000N12C017"}, (
        f"listed {sorted(found)}: N01, N04, N07's newest submission and N12 were sent and no payer answered them as their primary")
    assert json.loads(found["CB0000N12C017"]["evidence"])["codes"] == ["99214"]
    a = found["CB0000N01C017"]
    print(a["title"], "|", a["finding_id"], a["source"], a["expected_cents"], a["shortfall_cents"])
    assert a["signal"] == "NO_ANSWER" and a["finding_id"] == _fid("837_OPEN_CLAIM", "CB0000N01C017") and a["source"] == "837"
    assert a["payer_group"] == "Anthem / BCBS" and a["status"] == "open" and a["category"] == "denial"
    assert a["title"] == "Sent electronically 2026-09-15, no answer from Anthem / BCBS in 56 days: 99214"
    assert (a["expected_cents"], a["shortfall_cents"]) == (11760, 11760)
    ev = json.loads(a["evidence"])
    assert ev["sent"] == "2026-09-15" and ev["sent_basis"] == "the day its 837 claim file was sent" and ev["answer_due"] == "2026-10-15"
    assert ev["sent_837"]["frequency"] == "1" and ev["sent_837"]["payer_id"] == "00660" and ev["lines"] == [["99214", "2026-09-14", 22365, 11760]]
    b = found["CB0000N04C017"]
    print(b["title"], "|", b["hint"])
    assert b["finding_id"] == _fid("837_OPEN_CLAIM", "CB0000N04C017")
    assert b["hint"].startswith("Humana processed this claim as secondary on 2026-10-20 and paid $23.52, its share. The primary's "
                                "remittance, from Anthem / BCBS, is not on file")
    assert (b["expected_cents"], b["shortfall_cents"]) == (11760, 11760 - 2352)
    assert json.loads(found["CB0000N07C024"]["evidence"])["sent"] == "2026-09-20"
    # Anthem's remittance for N01 arrives (processed as primary): N01 is answered and leaves the list
    _remits(s, tmp_path, [answer("CB0000N01C017")], "20261112")
    found = _chased(s, "t2")
    assert set(found) == {"CB0000N04C017", "CB0000N07C024", "CB0000N12C017"}
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_an_837_claim_is_held_and_counted_when_its_payers_remittances_stop_before_its_due_day(tmp_path, kind):
    """Humana's remittances stop on September 25 while the feed reaches November 10 (misuse check CH-8). The 837s show:
    - H01, sent to Humana September 20 (due October 20): nothing on file says whether Humana answered it. Not listed,
      counted under Humana with its billed dollars for the Data tab (feed:payers_behind).
    - H02, sent to Humana August 1 (due August 31): Humana's remittances run past that day and do not answer it. Listed."""
    from remitwatch import insights
    s = _store(tmp_path, kind)
    humana = [claim(800 + i, svc("99214", "223.65", "117.60", "117.60", "CAS*CO*45*106.05~", dos="20260814"), dos="20260814") for i in range(5)]
    _remits(s, tmp_path, humana, "20260925", payer="HUMANA", payer_id="HUMNA")
    _remits(s, tmp_path, _visits(), "20261110")
    send(s, tmp_path, [clm837("CB0000H02C017", [("99214", "223.65", "20260731")], payer=HUMANA)], "20260801")
    send(s, tmp_path, [clm837("CB0000H01C017", [("99214", "200.00", "20260918")], payer=HUMANA)], "20260920")
    found = _chased(s, "t1")
    print("listed:", sorted(found))
    assert set(found) == {"CB0000H02C017"}, f"listed {sorted(found)}: H01 is due after Humana's last remittance"
    saved = json.loads(s.conn.execute("SELECT value FROM meta WHERE key='feed:payers_behind'").fetchone()[0])
    print(saved)
    assert [(p["payer"], p["since"], p["claims"], p["billed_cents"]) for p in saved] == [("Humana", "2026-09-25", 1, 20000)]
    check = next((h for h in insights.data_health(s.conn, "2026-11-10", "2026-11-12") if h.get("key") == "payer_stopped"), None)
    assert check is not None and check["n"] == 1 and check["cents"] == 20000
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_a_claim_the_weekly_exports_chase_keeps_one_item_dated_by_its_837(tmp_path, kind):
    """EMA's weekly exports list these claims to Anthem, and the 837s show when they went out (the remittances reach
    November 10; none answers them):
    - E01: EMA gives no submission date (bill made September 15); the 837 shows it sent September 18. One item, the
      exports' own (EMA_OPEN_CLAIM), saying it was sent electronically on September 18 (before: "Sent 2026-09-15", the
      day the bill was made), and no second item from the 837s.
    - E02: in EMA's print batch with no submission date, but the 837 shows it sent electronically September 12: chased,
      not listed as never sent (trace finding T-I4).
    - E03: EMA shows it submitted August 1; the 837 shows it sent again October 25. Counted from that send: not due yet.
    - E04: EMA shows it submitted September 1; its newest 837 is a void (September 20): not listed.
    - E05: EMA knows C017 (submitted August 1); the 837s show the bill's C024 sent September 1, which EMA's exports do
      not show. One item, on C024 (from the 837s), none on C017."""
    from tests.test_workable import ema
    s = _store(tmp_path, kind)
    _remits(s, tmp_path, _visits(), "20261110")
    c = s.conn
    ema(c, "CB0000E01C017")
    ema(c, "CB0000E02C017", bill_created="2026-09-10")
    ema(c, "CB0000E02C017", family="claim_status", claim_status="Batched for Printing")
    ema(c, "CB0000E03C017")
    ema(c, "CB0000E03C017", family="claim_status", claim_status="Submitted", claim_submission_date="2026-08-01")
    ema(c, "CB0000E04C017")
    ema(c, "CB0000E04C017", family="claim_status", claim_status="Submitted", claim_submission_date="2026-09-01")
    ema(c, "CB0000E05C017")
    ema(c, "CB0000E05C017", family="claim_status", claim_status="Submitted", claim_submission_date="2026-08-01")
    c.commit()
    send(s, tmp_path, [clm837("CB0000E04C017", V), clm837("CB0000E05C024", V, freq="7")], "20260901")
    send(s, tmp_path, [clm837("CB0000E02C017", V)], "20260912")
    send(s, tmp_path, [clm837("CB0000E01C017", V)], "20260918")
    send(s, tmp_path, [clm837("CB0000E04C017", V, freq="8")], "20260920")
    send(s, tmp_path, [clm837("CB0000E03C017", V, freq="7")], "20261025")
    found = _chased(s, "t1")
    print("listed:", {k: (v["signal"], v["title"]) for k, v in sorted(found.items())})
    assert set(found) == {"CB0000E01C017", "CB0000E02C017", "CB0000E05C024"}, f"listed {sorted(found)}"
    e1 = found["CB0000E01C017"]
    assert e1["finding_id"] == _fid("EMA_OPEN_CLAIM", "CB0000E01C017") and e1["source"] == "ema"
    assert e1["title"] == "Sent electronically 2026-09-18, no answer from Anthem / BCBS in 53 days: 99214"
    ev = json.loads(e1["evidence"])
    assert ev["sent"] == "2026-09-18" and ev["sent_basis"] == "the day its 837 claim file was sent" and ev["sent_837"]["frequency"] == "1"
    assert s.conn.execute("SELECT COUNT(*) FROM findings WHERE patient_control_number='CB0000E01C017'").fetchone()[0] == 1
    e2 = found["CB0000E02C017"]
    assert e2["signal"] == "NO_ANSWER" and e2["actionable"] == "review", (e2["signal"], e2["title"])
    assert e2["title"] == ("Sent electronically 2026-09-12, before EMA batched it for printing; no answer from Anthem / BCBS in 59 days: "
                           "99214")
    assert found["CB0000E05C024"]["finding_id"] == _fid("837_OPEN_CLAIM", "CB0000E05C024")
    s.close()


# --------------------------------------------------------------------------- a coordination denial, then the same payer as secondary

def _cob(claim_id: str, paid: str = "23.52", dos: str = "20260914") -> list[str]:
    """Humana's two answers to a claim: a denial as primary because another payer covers it first (CLP02 4, CO-22), then
    its processing as secondary (CLP02 2, OA-23 for the primary's answer), paying `paid`, its share."""
    return [answer(claim_id, status="4", paid="0.00", dos=dos, cas="CAS*CO*22*223.65~"), answer(claim_id, status="2", paid=paid, dos=dos)]


@pytest.mark.parametrize("kind", KINDS)
def test_a_claim_its_payer_denied_for_coordination_then_paid_as_secondary_chases_the_primarys_remittance(tmp_path, kind):
    """Humana denied these claims as primary saying another payer covers them first (CO-22) and then processed them as
    secondary, paying $23.52, its share. The denial items close on that answer (the settled-elsewhere fix), but the money
    the primary payer owes was chased by nothing: the coordination denial counted as the claim's primary answer.
    - K01, sent to Humana (only the 837s know it): no other payer's remittance is on file. The primary's remittance is
      missing: listed, saying so, sized at the usual rate less what Humana paid as secondary.
    - K02, in EMA's exports billed to Humana: the same, from the exports' path.
    - K03, as K01, and Anthem's remittance as primary is on file (it paid): answered, not listed.
    - K04, as K01, but Humana also paid it as primary once: answered by its primary, not listed."""
    from tests.test_workable import ema
    s = _store(tmp_path, kind)
    send(s, tmp_path, [clm837(f"CB0000K0{i}C017", V, payer=HUMANA) for i in (1, 3, 4)], "20260915")
    _remits(s, tmp_path, _cob("CB0000K01C017") + _cob("CB0000K02C017") + _cob("CB0000K03C017") + _cob("CB0000K04C017")
            + [answer("CB0000K04C017", paid="80.00")], "20261001", payer="HUMANA", payer_id="HUMNA")
    _remits(s, tmp_path, [answer("CB0000K03C017")], "20261005")
    _remits(s, tmp_path, _visits(), "20261110")
    ema(s.conn, "CB0000K02C017", payer="Humana", payer_id="61101")
    s.conn.commit()
    found = _chased(s, "t1")
    print({k: (v["finding_id"], v["title"], v["hint"], v["expected_cents"], v["shortfall_cents"]) for k, v in found.items()})
    assert set(found) == {"CB0000K01C017", "CB0000K02C017"}, (
        f"listed {sorted(found)}: K01 and K02 were answered only by Humana as secondary after its coordination denial")
    k1, k2 = found["CB0000K01C017"], found["CB0000K02C017"]
    assert k1["finding_id"] == _fid("837_OPEN_CLAIM", "CB0000K01C017") and k2["finding_id"] == _fid("EMA_OPEN_CLAIM", "CB0000K02C017")
    for f in (k1, k2):
        assert f["signal"] == "NO_ANSWER" and "the primary payer's remittance is missing" in f["title"], f["title"]
        assert f["hint"].startswith("Humana denied this claim as the primary payer on 2026-10-01 (another payer covers it first: CO-22) "
                                    "and processed it as secondary on 2026-10-01, paying $23.52. The primary payer's remittance is "
                                    "not on file"), f["hint"]
        assert (f["expected_cents"], f["shortfall_cents"]) == (11760, 11760 - 2352)
        ev = json.loads(f["evidence"])
        assert ev["secondary_answer"]["after_coordination_denial"] is True and ev["secondary_answer"]["paid_cents"] == 2352
    s.close()


# --------------------------------------------------------------------------- the size of a never-answered claim (T-N9)

def _parts(i: int, mods: str, paid: str) -> str:
    """Anthem paying one 88305 line for part of the service (26: the reading, TC: the slide), on claim i."""
    return claim(i, svc("88305", "150.00", paid, paid, f"CAS*CO*45*{150 - float(paid):.2f}~", mods=mods))


@pytest.mark.parametrize("kind", KINDS)
def test_a_never_answered_claim_is_priced_by_component_and_capped_at_each_lines_charge(tmp_path, kind):
    """Anthem pays 88305's reading (26) at $40 and its slide (TC) at $30, 99214 at $117.60 and 17110 at $80. Neither claim
    below was answered by a payer (the remittances reach November 10):
    - P01, only in the 837s: 88305-26, 88305-TC and a 99214 charged $100. Each line at Anthem's own rate for its code and
      part, a line never above its charge: $40 + $30 + $100 = $170 (before: the 26 and TC lines had no rate at all and
      the 99214 counted $117.60).
    - P02, in EMA's exports: a 99214 charged $100 and a 17110 charged $200: $100 + $80 = $180 (before: $117.60 + $80)."""
    from tests.test_workable import ema
    s = _store(tmp_path, kind)
    rates = ([_parts(300 + i, "26", "40.00") for i in range(10)] + [_parts(320 + i, "TC", "30.00") for i in range(10)]
             + [claim(340 + i, svc("17110", "200.00", "80.00", "80.00", "CAS*CO*45*120.00~")) for i in range(10)])
    _remits(s, tmp_path, rates + _visits(), "20261110")
    send(s, tmp_path, [clm837("CB0000P01C017", [("88305", "150.00", "20260914", "26"), ("88305", "150.00", "20260914", "TC"),
                                                ("99214", "100.00", "20260914")])], "20260915")
    ema(s.conn, "CB0000P02C017", charges_cents=10000)
    ema(s.conn, "CB0000P02C017", cpt="17110", charges_cents=20000)
    s.conn.commit()
    found = _chased(s, "t1")
    print({k: (v["expected_cents"], v["shortfall_cents"], json.loads(v["evidence"])["lines"]) for k, v in found.items()})
    assert set(found) == {"CB0000P01C017", "CB0000P02C017"}
    p1 = found["CB0000P01C017"]
    assert (p1["expected_cents"], p1["shortfall_cents"]) == (17000, 17000)
    assert json.loads(p1["evidence"])["lines"] == [["88305", "2026-09-14", 15000, 4000, "26"], ["88305", "2026-09-14", 15000, 3000, "TC"],
                                                   ["99214", "2026-09-14", 10000, 10000]]
    p2 = found["CB0000P02C017"]
    assert (p2["expected_cents"], p2["shortfall_cents"]) == (18000, 18000)
    s.close()


# --------------------------------------------------------------------------- a denial resent: worked, not work now (T-I4)

def _deny(i: str, carc: str, dos: str = "20260814") -> str:
    """Anthem's denial of claim CB0000<i>C017: the visit paid $0 with the reason given."""
    return answer(f"CB0000{i}C017", paid="0.00", dos=dos, cas=f"CAS*CO*{carc}*223.65~")


@pytest.mark.parametrize("kind", KINDS)
def test_a_denial_whose_bill_went_out_again_waits_for_that_answer(tmp_path, kind):
    """Anthem denied five visits on September 1 (remittances reach September 15; the run's day is September 20):
    - D01 (missing information, CO-16): the 837 shows the bill sent again (C024, a replacement) to Anthem on September 10.
      The denial is worked: it waits for that answer (before: work now, though the biller had already resent it).
    - D02 (CO-16): the bill went to Humana instead. Not the denying payer, and no coordination reason: work now.
    - D03 (coordination of benefits, CO-22: another payer is primary): the bill went to Humana. Worked: waiting.
    - D04 (CO-16): the only 837 is the first send, before the denial. Work now.
    - D05 (CO-16): no 837 at all. Work now.
    No resend is chased as a claim of its own. D01 waits Anthem's usual answer time after a send, as the 837s measure it
    (its 25 other claims were answered 50 days after they went out): it is followed up on October 30. Once that answer is
    overdue (January 15), D01 is work now again."""
    s = _store(tmp_path, kind)
    v = [("99214", "223.65", "20260814")]
    send(s, tmp_path, [clm837("CB0000D04C017", v)], "20260816")
    send(s, tmp_path, [clm837(f"CB{i:07d}C017", [("99214", "223.65", "20260401")]) for i in range(1, 26)], "20260727")
    _remits(s, tmp_path, [_deny("D01", "16"), _deny("D02", "16"), _deny("D03", "22"), _deny("D04", "16"), _deny("D05", "16")], "20260901")
    _remits(s, tmp_path, _visits(), "20260915")
    send(s, tmp_path, [clm837("CB0000D01C024", v, freq="7"), clm837("CB0000D02C024", v, payer=HUMANA),
                       clm837("CB0000D03C024", v, payer=HUMANA)], "20260910")
    Detector(s, run_id="t1", today="2026-09-20").run()
    rows = {r["patient_control_number"]: dict(r) for r in s.query(
        "SELECT patient_control_number, signal, work_state, follow_up_on, state_basis FROM findings WHERE category='denial'")}
    print({k: (v_["signal"], v_["work_state"], v_["follow_up_on"], v_["state_basis"]) for k, v_ in sorted(rows.items())})
    assert set(rows) == {"CB0000D01C017", "CB0000D02C017", "CB0000D03C017", "CB0000D04C017", "CB0000D05C017"}, (
        f"items {sorted(rows)}: a resend after a denial is the denial's work, never a claim chased on its own")
    d1 = rows["CB0000D01C017"]
    assert d1["work_state"] == "waiting", f"D01 was resent to Anthem on 2026-09-10: {d1['work_state']} ({d1['state_basis']})"
    assert d1["follow_up_on"] == "2026-10-30" and "Sent again electronically to Anthem / BCBS on Sep 10, 2026" in d1["state_basis"]
    assert rows["CB0000D02C017"]["work_state"] == "work_now", rows["CB0000D02C017"]["state_basis"]
    d3 = rows["CB0000D03C017"]
    assert d3["work_state"] == "waiting" and "to Humana" in d3["state_basis"], d3["state_basis"]
    assert rows["CB0000D04C017"]["work_state"] == "work_now" and rows["CB0000D05C017"]["work_state"] == "work_now"
    Detector(s, run_id="t2", today="2027-01-15").run()
    d1 = dict(s.query("SELECT work_state, state_basis FROM findings WHERE patient_control_number='CB0000D01C017'")[0])
    assert d1["work_state"] == "work_now" and "follow up" in d1["state_basis"], d1
    s.close()


# --------------------------------------------------------------------------- what EMA's postings answered

@pytest.mark.parametrize("kind", KINDS)
def test_an_837_claim_emas_postings_answered_is_not_chased(tmp_path, kind, monkeypatch):
    """Only the 837s know three claims to Blue Cross, sent March 4 (no weekly export lists them, no 835 answers them).
    EMA's Production report (as of May 1) holds:
    - claim 51 (the A/R pairs it with EMA claim 51): a payment posted April 10, after the send. EMA's postings answered
      it: not listed (a claim paid on a check no 835 carries is not work).
    - claim 52 (EMA claim 52): its charge, nothing posted since. Listed once, with EMA's claim number (the Production
      check's item for the same claim gives way to it).
    - claim 53: no EMA claim can be found for it, and only EMA's postings reach its due day (the remittances stop March
      28). Not listed; counted with the claims no source can show an answer to (meta ema:unanswered_unseen).
    - claim 57 (EMA claim 57), for the patient whose same visit and code Blue Cross answered as primary on claim 1 (another
      bill, the denial already listed): answered there, not chased again (the member id cannot link two payers' bills;
      EMA's keyed name for the line can).
    - claim 60, sent January 6 for the patient of claim 1 (its member id): EMA's claim 60 for that patient, day and code was
      paid January 30, but another patient's EMA claim has the same day, code and charge, so the claim's own EMA claim
      cannot be told by its content. The patient's name (from the member's remittances) finds it: answered, not listed.
    - claim 63 (EMA claim 63): EMA shows it settled: Blue Cross's contractual write-down and the patient's payment of the
      rest, nothing open, no insurance payment. Answered (before: a routine write-down alone never counted), not listed."""
    from remitwatch import ema_checks as ec, ema_ledger as el, ema_reports as er
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import adj, charge, denial_world, pay
    monkeypatch.setattr(er, "CLAIM_HOST_SUFFIX", F.HOST_SUFFIX)
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    monkeypatch.setenv("REMITWATCH_EMA_STRICT", "1")
    for k in ("VERCEL", "REMITWATCH_EMA_REPORTS", "REMITWATCH_EMA_CLAIM_URL", "REMITWATCH_EMA_PAYER_ALIASES", "REMITWATCH_EMA_LEDGER",
              "REMITWATCH_EMA_REPORT_KEEP", "REMITWATCH_EMA_CLOSES"):
        monkeypatch.delenv(k, raising=False)
    ec._LAST_POST.clear()
    s = _store(tmp_path, kind)
    er.ensure_schema(s.conn)
    s.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('ema:key_salt', ?)", ("ab" * 32,))
    s.conn.commit()
    lg = el.Ledger.open(str(tmp_path / "ema_ledger.sqlite"))
    D = dt.date
    rows = [charge(F.link_no(51), patient=1, posted=D(2026, 3, 3)), pay(F.link_no(51), "PAPER-51", 90, patient=1, posted=D(2026, 4, 10)),
            charge(F.link_no(52), cpt="99214", amount=200, patient=2, posted=D(2026, 3, 3)),
            charge(F.link_no(57), patient=0, posted=D(2026, 3, 3)),
            # claim 60's visit, paid on EMA's claim 60; another patient's EMA claim 62 has the same day, code and charge
            charge(F.link_no(60), cpt="11102", amount=120, patient=0, svc=D(2026, 1, 5), posted=D(2026, 1, 6)),
            pay(F.link_no(60), "PAPER-60", 70, cpt="11102", patient=0, svc=D(2026, 1, 5), posted=D(2026, 1, 30)),
            charge(F.link_no(62), cpt="11102", amount=120, patient=2, svc=D(2026, 1, 5), posted=D(2026, 4, 30)),
            # claim 63: Blue Cross's contractual write-down and the patient's payment of the rest, nothing open
            charge(F.link_no(63), patient=3, posted=D(2026, 3, 3)),
            adj(F.link_no(63), "CO-45", 100, patient=3, posted=D(2026, 3, 20), contractual=True),
            F.production_row(link=F.link_no(63), cpt="99213", pat_paid=50, patient=3, svc=D(2026, 3, 2), posted=D(2026, 3, 25),
                             payer="Patient")]
    ar = [F.ar_row(claim=F.claim_id(51), link=F.link_no(51), status="Payment Pending", current=0, svc=D(2026, 3, 2), patient=1),
          F.ar_row(claim=F.claim_id(52), link=F.link_no(52), status="Payment Pending", current=200, svc=D(2026, 3, 2), patient=2,
                   cpt="99214"),
          F.ar_row(claim=F.claim_id(57), link=F.link_no(57), status="Payment Pending", current=150, svc=D(2026, 3, 2), patient=0),
          F.ar_row(claim=F.claim_id(63), link=F.link_no(63), status="Payment Pending", current=0, svc=D(2026, 3, 2), patient=3)]
    denial_world(s, lg, tmp_path, rows, ar_rows=ar)
    bc = ("BLUE CROSS TEST", "BCT01")
    send(s, tmp_path, [clm837(F.claim_id(51), [("99213", "150.00", "20260302")], payer=bc),
                       clm837(F.claim_id(52), [("99214", "200.00", "20260302")], payer=bc),
                       clm837(F.claim_id(53), [("11102", "120.00", "20260302")], payer=bc),
                       clm837(F.claim_id(57), [("99213", "150.00", "20260302")], payer=bc),
                       clm837(F.claim_id(63), [("99213", "150.00", "20260302")], payer=bc)], "20260304")
    send(s, tmp_path, [clm837(F.claim_id(60), [("11102", "120.00", "20260105")], payer=bc, member="MEMBER000001")], "20260106")
    det = Detector(s, run_id="r1", today="2026-05-03")
    det.run()
    assert det.ema_errors == [], det.ema_errors
    lg.close()
    got = [dict(r) for r in s.query("SELECT finding_id, patient_control_number, ema_link, source, signal FROM findings WHERE signal='NO_ANSWER'")]
    print(got)
    assert [g["patient_control_number"] for g in got] == [F.claim_id(52)], f"listed {got}: only claim 52 is unanswered in EMA too"
    assert got[0]["finding_id"] == _fid("837_OPEN_CLAIM", F.claim_id(52)) and got[0]["ema_link"] == F.link_no(52)
    unseen = json.loads(s.conn.execute("SELECT value FROM meta WHERE key='ema:unanswered_unseen'").fetchone()[0])
    print(unseen)
    assert unseen["n"] == 1 and unseen["billed_cents"] == 12000
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_a_coordination_denials_primary_answer_posted_in_ema_leaves_nothing_to_chase(tmp_path, kind, monkeypatch):
    """Blue Cross denied claims 54 and 55 as primary because another payer covers them first (CO-22) and then processed
    them as secondary, paying $20; the 837s show both sent March 4, and no other payer's remittance is on file. EMA's
    Production report (as of May 1):
    - claim 54: Medicare's payment of $80 posted March 20, after the send: the primary answered, nothing to chase.
    - claim 55: only Blue Cross's own $20 (its answer as secondary) is posted. The primary's remittance is missing: listed,
      saying so (EMA's posting of the secondary's answer is not the primary's).
    - claim 56, in EMA's weekly exports billed to Blue Cross: only another payer answered, as secondary (T-C2), and EMA
      shows only that payer's posting. Listed from the exports' path (before: EMA's posting of the secondary's own answer
      read as the claim's answer, and the primary's missing remittance was dropped).
    - claim 59, as 54, but Medicare's payment was posted March 3, the day before the claim went to Blue Cross (the primary
      answers first; the claim is sent to the secondary after). The primary answered: nothing to chase."""
    from remitwatch import ema_checks as ec, ema_ledger as el, ema_reports as er
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import charge, denial_world, pay, remit as raw_remit
    monkeypatch.setattr(er, "CLAIM_HOST_SUFFIX", F.HOST_SUFFIX)
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    monkeypatch.setenv("REMITWATCH_EMA_STRICT", "1")
    for k in ("VERCEL", "REMITWATCH_EMA_REPORTS", "REMITWATCH_EMA_CLAIM_URL", "REMITWATCH_EMA_PAYER_ALIASES", "REMITWATCH_EMA_LEDGER",
              "REMITWATCH_EMA_REPORT_KEEP", "REMITWATCH_EMA_CLOSES"):
        monkeypatch.delenv(k, raising=False)
    ec._LAST_POST.clear()
    s = _store(tmp_path, kind)
    er.ensure_schema(s.conn)
    s.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('ema:key_salt', ?)", ("ab" * 32,))
    s.conn.commit()
    lg = el.Ledger.open(str(tmp_path / "ema_ledger.sqlite"))
    D = dt.date
    S_ = "2026-03-02"
    for n in (54, 55, 59):
        raw_remit(s, trace=f"77700{n}1", paid_on="2026-03-12", claims=[{"pcn": F.claim_id(n), "status": "4", "member": f"MEMBER0000{n}",
                                                                     "lines": [(S_, "99213", 150, 0, [("CO", "22", 150)])]}])
        raw_remit(s, trace=f"77700{n}2", paid_on="2026-03-26", claims=[{"pcn": F.claim_id(n), "status": "2", "member": f"MEMBER0000{n}",
                                                                     "lines": [(S_, "99213", 150, 20, [("OA", "23", 130)])]}])
    raw_remit(s, trace="777000562", paid_on="2026-03-26", payer="TESTCARE HEALTH",
              claims=[{"pcn": F.claim_id(56), "status": "2", "member": "MEMBER000056", "lines": [(S_, "99214", 200, 30, [("OA", "23", 170)])]}])
    rows = [charge(F.link_no(54), patient=1, posted=D(2026, 3, 3)),
            pay(F.link_no(54), "PAPER-54", 80, patient=1, posted=D(2026, 3, 20), payer=F.PAYERS["medicare"]),
            pay(F.link_no(54), "777000542", 20, patient=1, posted=D(2026, 3, 27)),
            charge(F.link_no(55), patient=2, posted=D(2026, 3, 3)),
            pay(F.link_no(55), "777000552", 20, patient=2, posted=D(2026, 3, 27)),
            charge(F.link_no(59), patient=3, posted=D(2026, 3, 3)),
            pay(F.link_no(59), "PAPER-59", 90, patient=3, posted=D(2026, 3, 3), payer=F.PAYERS["medicare"]),
            pay(F.link_no(59), "777000592", 20, patient=3, posted=D(2026, 3, 27)),
            charge(F.link_no(56), cpt="99214", amount=200, patient=3, posted=D(2026, 3, 3)),
            pay(F.link_no(56), "777000562", 30, cpt="99214", patient=3, posted=D(2026, 3, 27), payer=F.PAYERS["commercial"]),
            # Blue Cross's answers in EMA reach past April 1 (another claim's payment): its claims due by then are judged
            charge(F.link_no(58), cpt="11102", amount=120, patient=0, posted=D(2026, 3, 3)),
            pay(F.link_no(58), "PAPER-58", 60, cpt="11102", patient=0, posted=D(2026, 4, 10))]
    ar = [F.ar_row(claim=F.claim_id(54), link=F.link_no(54), status="Payment Pending", current=50, svc=D(2026, 3, 2), patient=1),
          F.ar_row(claim=F.claim_id(55), link=F.link_no(55), status="Payment Pending", current=130, svc=D(2026, 3, 2), patient=2),
          F.ar_row(claim=F.claim_id(56), link=F.link_no(56), status="Payment Pending", current=170, svc=D(2026, 3, 2), patient=3,
                   cpt="99214"),
          F.ar_row(claim=F.claim_id(59), link=F.link_no(59), status="Payment Pending", current=40, svc=D(2026, 3, 2), patient=3)]
    denial_world(s, lg, tmp_path, rows, ar_rows=ar)
    from tests.test_workable import ema
    ema(s.conn, F.claim_id(56), payer="Blue Cross Test Plan", payer_id="BCT01", service_date=S_, bill_created="2026-03-03",
        charges_cents=20000, patient_id="EMA56")
    s.conn.commit()
    bc = ("BLUE CROSS TEST", "BCT01")
    send(s, tmp_path, [clm837(F.claim_id(n), [("99213", "150.00", "20260302")], payer=bc, member=f"MEMBER0000{n}") for n in (54, 55, 59)],
         "20260304")
    det = Detector(s, run_id="r1", today="2026-05-03")
    det.run()
    assert det.ema_errors == [], det.ema_errors
    lg.close()
    got = {r["patient_control_number"]: dict(r) for r in s.query(
        "SELECT finding_id, patient_control_number, ema_link, hint, title FROM findings WHERE signal='NO_ANSWER'")}
    print(got)
    assert set(got) == {F.claim_id(55), F.claim_id(56)}, (
        f"listed {sorted(got)}: claim 54's primary answer is posted in EMA, the others' are not")
    f = got[F.claim_id(55)]
    assert f["finding_id"] == _fid("837_OPEN_CLAIM", F.claim_id(55)) and f["ema_link"] == F.link_no(55)
    assert f["hint"].startswith("Anthem / BCBS denied this claim as the primary payer on 2026-03-12 (another payer covers it first: CO-22)")
    g = got[F.claim_id(56)]
    assert g["finding_id"] == _fid("EMA_OPEN_CLAIM", F.claim_id(56))
    assert g["hint"].startswith("Testcare Health processed this claim as secondary on 2026-03-26 and paid $30.00, its share. The "
                                "primary's remittance, from Anthem / BCBS, is not on file"), g["hint"]
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_the_primarys_posting_is_told_by_its_payers_name_not_the_summarys_group(tmp_path, kind):
    """EMA's payment summary can carry another payer's group on a posting (a learned alias put United Healthcare's postings
    under 'Medicaid (other)' on the real data). A claim Medicaid KY answered as secondary, with United Healthcare's
    payment posted on its EMA claim, was answered by its primary (nothing to chase); one with only Medicaid's own
    postings was not."""
    from remitwatch import ema_reports as er
    s = _store(tmp_path, kind)
    er.ensure_schema(s.conn)
    s.conn.executemany("""INSERT INTO ema_line_payments (link, svc, cpt, check_no, posted, payer, payer_group, role, ins_paid, rows, in_835)
                          VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                       [(7001, "2026-03-02", "99213", "CHK1", "2026-03-20", "United Healthcare", "Medicaid (other)", "other", 6457, 1, 0),
                        (7001, "2026-03-02", "99213", "CHK2", "2026-04-20", "Medicaid of Kentucky", "Medicaid (other)", "secondary", 0, 1, 1),
                        (7002, "2026-03-02", "99213", "CHK3", "2026-04-20", "Medicaid of Kentucky", "Medicaid (other)", "secondary", 0, 1, 1)])
    s.conn.commit()
    det = Detector(s, run_id="t", today="2026-05-03")
    got = det._primary_posted([("CLAIM1", 7001, "2026-04-01", ["Medicaid KY"]), ("CLAIM2", 7002, "2026-04-01", ["Medicaid KY"])])
    assert got == {"CLAIM1"}, f"answered by the primary: {sorted(got)}"
    s.close()


# --------------------------------------------------------------------------- the Data tab: feeds that do not agree

def _health(conn, key: str) -> dict | None:
    from remitwatch import insights
    return next((h for h in insights.data_health(conn, "2026-11-10", "2026-11-12") if h.get("key") == key), None)


@pytest.mark.parametrize("kind", KINDS)
def test_a_payer_whose_837_claims_never_get_an_era_is_named_on_the_data_tab(tmp_path, kind):
    """Twelve claims went to the VA's community care network (payer id VACCN, named after its administrator) in August and
    September; no ERA from it is on file, and no EMA report can show its answers. They are not listed as never answered
    (the VA pays outside the 835 feed: EMA's postings are the only source of its answers), and the Data tab says so with
    the claims and dollars, on the store and on the dashboard's copy. Two claims to a small plan with no remittance on
    file at all are judged as before: listed, saying no remittance from it is on file."""
    from remitwatch import cloud
    s = _store(tmp_path, kind)
    _remits(s, tmp_path, _visits(), "20261110")
    send(s, tmp_path, [clm837(f"CB0000V{i:02d}C017", [("99214", "100.00", "20260810")], payer=VA, member=f"V{i:08d}") for i in range(12)],
         "20260812")
    send(s, tmp_path, [clm837(f"CB0000T{i:02d}C017", V, payer=("TESTCARE HEALTH", "TC001")) for i in range(2)], "20260915")
    found = _chased(s, "t1")
    print("listed:", sorted(found))
    assert set(found) == {"CB0000T00C017", "CB0000T01C017"}, f"listed {sorted(found)}"
    assert "No remittance from Testcare Health is on file at all" in found["CB0000T00C017"]["hint"]
    copy = str(tmp_path / "serving.sqlite")
    cloud.build_serving(s.conn, copy)
    served = Store(copy)
    for where, conn in (("store", s.conn), ("dashboard's copy", served.conn)):
        h = _health(conn, "no_era_837")
        print(where, h)
        assert h is not None and not h["ok"], f"nothing on the Data tab ({where}) names the payer whose claims never get an ERA"
        assert h["title"] == "No ERA comes for claims sent to VA Community Care"
        assert h["n"] == 12 and h["cents"] == 120000
        assert "12 claims sent since 2026-08-12 ($1,200.00 billed)" in h["detail"]
    served.close()
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_weeks_one_feed_shows_sends_and_the_other_does_not_are_named(tmp_path, kind):
    """EMA's claim-status exports and the 837s both reach from the week of September 7 to the week of September 28. EMA
    shows six claims sent in the week of September 14 and no 837 is on file for that week; the 837s show six claims
    sent in the week of September 21 and EMA shows none (trace finding T-N13: a hole in either feed)."""
    from tests.test_workable import ema
    s = _store(tmp_path, kind)
    _remits(s, tmp_path, _visits(), "20261110")
    c = s.conn
    weeks_ema = {"2026-09-08": "W1", "2026-09-15": "W2", "2026-09-29": "W4"}
    for day_, tag in weeks_ema.items():
        for i in range(6):
            ema(c, f"CB00{tag}X{i:02d}C017", family="claim_status", claim_status="Submitted", claim_submission_date=day_)
    c.commit()
    for bht, tag in (("20260908", "W1"), ("20260922", "W3"), ("20260929", "W4")):
        send(s, tmp_path, [clm837(f"CB00{tag}X{i:02d}C017", V) for i in range(6)], bht)
    Detector(s, run_id="t1").run()
    h = _health(s.conn, "send_weeks")
    print(h)
    assert h is not None and not h["ok"]
    assert h["ema_no_837"] == [{"week": "2026-09-14", "claims": 6}] and h["no_ema_837"] == [{"week": "2026-09-21", "claims": 6}]
    assert "week of 2026-09-14" in h["detail"] and "week of 2026-09-21" in h["detail"]
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_a_claim_in_emas_print_batch_is_dated_by_emas_export_not_the_remittances(tmp_path, kind):
    """The remittances stop on September 20 (the feed is behind). EMA's export of November 15 still shows two claims in
    its print batch with no submission date: X01, bill made September 1, has waited past 30 days by that export and is
    listed to be sent (before: it waited for the remittance feed to reach October 1, trace finding T-N11); X02, made
    November 1, has not."""
    from tests.test_workable import ema
    s = _store(tmp_path, kind)
    _remits(s, tmp_path, _visits(), "20260920")
    c = s.conn
    for cid, made in (("CB0000X01C017", "2026-09-01"), ("CB0000X02C017", "2026-11-01")):
        ema(c, cid, bill_created=made, service_date=made, source_sha256="charges-1115")
        ema(c, cid, family="claim_status", claim_status="Batched for Printing", source_sha256="status-1115", service_date=made)
    c.execute("INSERT INTO ema_sources (source_name, sha256, family, created_at, rows, recorded_at) VALUES (?,?,?,?,?,?)",
              ("Claim & Claim Status 11-15.xlsx", "status-1115", "claim_status", "2026-11-15T08:00:00", 2, "2026-11-15T09:00:00"))
    c.execute("INSERT INTO ema_sources (source_name, sha256, family, created_at, rows, recorded_at) VALUES (?,?,?,?,?,?)",
              ("Encounter & Charge Lines 11-15.xlsx", "charges-1115", "charge_lines", "2026-11-15T08:00:00", 2, "2026-11-15T09:00:00"))
    c.commit()
    found = _chased(s, "t1", today="2026-11-16")
    print({k: (v["signal"], v["title"]) for k, v in found.items()})
    assert set(found) == {"CB0000X01C017"}, f"listed {sorted(found)}"
    x1 = found["CB0000X01C017"]
    assert x1["signal"] == "REJECTED_PRE_PAYER" and x1["title"].startswith("In EMA's print batch, never sent to Anthem / BCBS")
    s.close()


# --------------------------------------------------------------------------- the work follows the claim between the two

@pytest.mark.parametrize("kind", KINDS)
def test_the_work_on_a_claim_follows_it_when_the_weekly_exports_start_to_chase_it(tmp_path, kind):
    """C01 is only in the 837s: chased under its own kind, and a person starts it and writes a note. The next week EMA's
    export lists it: one item, the exports' own, and it keeps the person's status and note (before: the 837's item went
    stale and the exports' item started To do, so the claim would be worked twice)."""
    from remitwatch.actions import apply_status
    from tests.test_workable import ema
    s = _store(tmp_path, kind)
    _remits(s, tmp_path, _visits(), "20261110")
    send(s, tmp_path, [clm837("CB0000C01C017", V)], "20260915")
    found = _chased(s, "t1")
    fid = found["CB0000C01C017"]["finding_id"]
    assert fid == _fid("837_OPEN_CLAIM", "CB0000C01C017")
    apply_status(s.conn, fid, "in_progress", "Called Anthem: no record of it", actor="biller")
    s.conn.commit()
    ema(s.conn, "CB0000C01C017")
    found = _chased(s, "t2")
    rows = [dict(r) for r in s.query("SELECT finding_id, status, notes, stale FROM findings WHERE patient_control_number='CB0000C01C017'")]
    print(rows)
    live = [r for r in rows if not r["stale"]]
    assert len(live) == 1 and live[0]["finding_id"] == _fid("EMA_OPEN_CLAIM", "CB0000C01C017")
    assert live[0]["status"] == "in_progress" and "no record of it" in (live[0]["notes"] or ""), live[0]
    s.close()
