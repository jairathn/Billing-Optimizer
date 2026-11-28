"""A person who lets go of a line's denial is not handed the same money back the next day as a whole-claim denial "nobody
has worked".

The month-by-month replay of 2026 found this on a real visit: one visit, one claim id, one payer. The
payer first denied the claim; three weeks later, on one day, it reversed that denial, processed the claim again (paid the
procedure, bundled the 99214 and the G2211 into it, CO-97) and also sent a whole-claim denial of the same claim id
(CO-129, every line). Detection listed the 99214's bundling denial as its own item (EM_BUNDLED_MOD25) and, rightly, no
whole-claim item: the 99214 was "denied again as a line" with its own finding. A biller let the line item go. On the
next run the whole-claim denial came up as a new CLAIM_DENIED_UNPAID for the 99214 and the G2211, "Not paid and nobody
has worked it", Work now: detect.py's settled() counts a line denial only while no person dismissed or marked
lost its finding (closed_lines). Without the let-go (the one-shot run) the whole-claim item never appears.

The rule was written for a person keeping one of two listings of one visit (tests/review5/test_r5_fr_dismissed_elsewhere:
both were listed when the biller dismissed one); here only the line was ever listed, and the person's decision on its
money is undone by an automatic one. Invented data only, on both store kinds; the control is the same visit with nobody
acting: one item, the line's. More cases pin the rule down: when the whole-claim denial also denies a service nobody
decided (a 17000 the processed claim left out), only the let-go code is settled and the 17000 stays listed as the
claim's denial, whether the claim's item came before the let-go (listing the other codes) or after it; and the review5
rule stands where the claim's own item listed the code the person let go on the line: there the person chose between two
listings of the same money, and the claim keeps it.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch import actions  # noqa: E402
from tests.m1_ledger_helpers import World  # noqa: E402
from tests.review3.r3helpers import clm, sv  # noqa: E402

PCN = "CB0000910C017"
DOS = "20260401"


@pytest.fixture(params=["file", "compact"])
def kind(request):
    return request.param


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    for k in ("VERCEL", "REMITWATCH_EMA_REPORTS", "REMITWATCH_EMA_LEDGER", "REMITWATCH_TODAY"):
        monkeypatch.delenv(k, raising=False)


def _lines(c99, c11, cg, paid11="0.00", sign=""):
    """The visit's three lines: the 99214, the procedure (11900) and the G2211, with their adjustments."""
    u = -1 if sign else 1
    return (sv("99214", f"{sign}223.65", "0.00", u, c99, DOS) + sv("11900", f"{sign}95.00", paid11, u, c11, DOS)
            + sv("G2211", f"{sign}30.00", "0.00", u, cg, DOS))


def _visit(tmp_path, kind) -> World:
    w = World(tmp_path, kind)
    # May 9: Anthem denies the claim (the procedure for missing information, the 99214 and the G2211 as bundled)
    w.remit([clm(PCN, "4", "348.65", "0.00", _lines("CAS*CO*97*223.65~", "CAS*CO*16*95.00~", "CAS*CO*97*30.00~"), payer_ctl="PCA1",
                 dos=DOS)], "20260509")
    # May 30: it reverses that denial and processes the claim again (the procedure paid, the 99214 and G2211 bundled) ...
    w.remit([clm(PCN, "22", "-348.65", "0.00", _lines("CAS*CO*97*-223.65~", "CAS*CO*16*-95.00~", "CAS*CO*97*-30.00~", sign="-"),
                 payer_ctl="PCA1", dos=DOS),
             clm(PCN, "1", "348.65", "30.10", _lines("CAS*CO*97*223.65~", "CAS*CO*45*54.90~CAS*PR*2*10.00~", "CAS*CO*97*30.00~",
                                                     paid11="30.10"), payer_ctl="PCA2", dos=DOS)], "20260530")
    # ... and, the same day on another remittance, denies the same claim id whole: prior processing information incorrect
    w.remit([clm(PCN, "4", "348.65", "0.00", _lines("CAS*CO*129*223.65~", "CAS*CO*129*95.00~", "CAS*CO*129*30.00~"),
                 payer_ctl="PCA3", dos=DOS)], "20260530")
    w.run("r1", today="2026-06-10")
    return w


def _listed(w) -> list[dict]:
    return [r for r in w.rows("COALESCE(stale, 0) = 0") if r["patient_control_number"] == PCN and r["status"] == "open"
            and r.get("work_state") == "work_now"]


def test_control_nobody_acts_the_99214_is_listed_once_as_its_lines_denial(tmp_path, kind):
    w = _visit(tmp_path, kind)
    listed = _listed(w)
    assert any(r["procedure_code"] == "99214" and r["line_key"] for r in listed), [(r["signal"], r["procedure_code"]) for r in listed]
    assert not [r for r in listed if r["signal"] == "CLAIM_DENIED_UNPAID"], [(r["signal"], r["shortfall_cents"]) for r in listed]
    w.store.close()


def test_a_line_denial_a_person_let_go_does_not_come_back_as_the_claims_denial(tmp_path, kind):
    w = _visit(tmp_path, kind)
    line = [r for r in _listed(w) if r["procedure_code"] == "99214" and r["line_key"]]
    assert line, [(r["signal"], r["procedure_code"]) for r in w.rows()]
    assert actions.apply_status(w.store.conn, line[0]["finding_id"], "dismissed", "Not worth pursuing", None, "Test Biller",
                                "2026-06-11T15:00:00+00:00", "open")
    w.store.conn.commit()
    w.run("r2", today="2026-06-12")
    back = [(r["signal"], r["procedure_code"], r["shortfall_cents"], (r.get("state_basis") or "")[:60]) for r in _listed(w)
            if r["signal"] == "CLAIM_DENIED_UNPAID"]
    assert not back, ("the 99214 the biller let go came back the next run as a whole-claim denial to work, under a new item: "
                      f"{back}")
    w.store.close()


def _lines4(c99, c11, cg, c17):
    """The visit's lines with a fourth service, a 17000 the processed claim left out."""
    return _lines(c99, c11, cg) + sv("17000", "120.00", "0.00", 1, c17, DOS)


def test_only_the_codes_a_person_let_go_are_settled_codes_nobody_decided_stay_listed(tmp_path, kind):
    """The payer processes the claim (the 99214 bundled, a line denial the biller lets go); only then does the whole-claim
    CO-129 denial arrive, for the same codes and a 17000 the processed claim left out. The 99214 stays settled by the
    person's decision; the 17000, which nobody decided, is listed as the claim's denial (with the G2211, which no item of
    its own carries)."""
    w = World(tmp_path, kind)
    w.remit([clm(PCN, "4", "348.65", "0.00", _lines("CAS*CO*97*223.65~", "CAS*CO*16*95.00~", "CAS*CO*97*30.00~"), payer_ctl="PCA1",
                 dos=DOS)], "20260509")
    w.remit([clm(PCN, "22", "-348.65", "0.00", _lines("CAS*CO*97*-223.65~", "CAS*CO*16*-95.00~", "CAS*CO*97*-30.00~", sign="-"),
                 payer_ctl="PCA1", dos=DOS),
             clm(PCN, "1", "348.65", "30.10", _lines("CAS*CO*97*223.65~", "CAS*CO*45*54.90~CAS*PR*2*10.00~", "CAS*CO*97*30.00~",
                                                     paid11="30.10"), payer_ctl="PCA2", dos=DOS)], "20260530")
    w.run("r1", today="2026-06-10")
    line = [r for r in _listed(w) if r["procedure_code"] == "99214" and r["line_key"]]
    assert line, [(r["signal"], r["procedure_code"]) for r in w.rows()]
    assert actions.apply_status(w.store.conn, line[0]["finding_id"], "dismissed", "Not worth pursuing", None, "Test Biller",
                                "2026-06-11T15:00:00+00:00", "open")
    w.store.conn.commit()
    w.remit([clm(PCN, "4", "468.65", "0.00", _lines4("CAS*CO*129*223.65~", "CAS*CO*129*95.00~", "CAS*CO*129*30.00~",
                                                     "CAS*CO*129*120.00~"), payer_ctl="PCA3", dos=DOS)], "20260612")
    w.run("r2", today="2026-06-15")
    claim = [r for r in _listed(w) if r["signal"] == "CLAIM_DENIED_UNPAID"]
    assert len(claim) == 1, [(r["signal"], r["procedure_code"]) for r in _listed(w)]
    ev = json.loads(claim[0]["evidence"] or "{}")
    # the 17000 (and the G2211, which has no item of its own) nobody decided; the 99214 the person let go is not listed
    assert "17000" in ev.get("codes", []) and "99214" not in ev.get("codes", []), ev.get("codes")
    assert "99214" in (ev.get("settled_elsewhere") or {}), ev.get("settled_elsewhere")
    w.store.close()


def _visit4(tmp_path, kind) -> World:
    """The visit of _visit, its whole-claim CO-129 denial also denying a 17000 the processed claim left out: the claim is
    listed for the 17000 (and the G2211), its 99214 settled by the line's own denial."""
    w = World(tmp_path, kind)
    w.remit([clm(PCN, "4", "348.65", "0.00", _lines("CAS*CO*97*223.65~", "CAS*CO*16*95.00~", "CAS*CO*97*30.00~"), payer_ctl="PCA1",
                 dos=DOS)], "20260509")
    w.remit([clm(PCN, "22", "-348.65", "0.00", _lines("CAS*CO*97*-223.65~", "CAS*CO*16*-95.00~", "CAS*CO*97*-30.00~", sign="-"),
                 payer_ctl="PCA1", dos=DOS),
             clm(PCN, "1", "348.65", "30.10", _lines("CAS*CO*97*223.65~", "CAS*CO*45*54.90~CAS*PR*2*10.00~", "CAS*CO*97*30.00~",
                                                     paid11="30.10"), payer_ctl="PCA2", dos=DOS)], "20260530")
    w.remit([clm(PCN, "4", "468.65", "0.00", _lines4("CAS*CO*129*223.65~", "CAS*CO*129*95.00~", "CAS*CO*129*30.00~",
                                                     "CAS*CO*129*120.00~"), payer_ctl="PCA3", dos=DOS)], "20260530")
    w.run("r1", today="2026-06-10")
    return w


def _claim_codes(w) -> tuple[list, int]:
    claim = [r for r in _listed(w) if r["signal"] == "CLAIM_DENIED_UNPAID"]
    assert len(claim) == 1, [(r["signal"], r["procedure_code"]) for r in _listed(w)]
    return json.loads(claim[0]["evidence"] or "{}").get("codes") or [], claim[0]["finding_id"]


def _let_go_the_99214_line(w) -> None:
    line = [r for r in _listed(w) if r["procedure_code"] == "99214" and r["line_key"]]
    assert line, [(r["signal"], r["procedure_code"]) for r in w.rows()]
    assert actions.apply_status(w.store.conn, line[0]["finding_id"], "dismissed", "Not worth pursuing", None, "Test Biller",
                                "2026-06-11T15:00:00+00:00", "open")
    w.store.conn.commit()


def test_a_claim_listed_for_other_codes_does_not_take_back_the_code_a_person_let_go(tmp_path, kind):
    w = _visit4(tmp_path, kind)
    codes, fid = _claim_codes(w)
    assert "17000" in codes and "99214" not in codes, codes
    _let_go_the_99214_line(w)
    w.run("r2", today="2026-06-12")
    codes2, fid2 = _claim_codes(w)
    assert fid2 == fid and "99214" not in codes2 and "17000" in codes2, (
        "the claim's item, listed for the 17000, took back the 99214 the person let go on its line: " + str(codes2))
    w.store.close()


def test_review5_rule_a_claim_whose_item_listed_the_code_keeps_it_when_its_line_is_let_go(tmp_path, kind):
    """An older release listed the visit's 99214 twice, on the claim's item and on the line's (tests/review5): the person
    let go of the line's listing and kept the claim's. The claim's item keeps the 99214."""
    w = _visit4(tmp_path, kind)
    codes, fid = _claim_codes(w)
    row = w.store.conn.execute("SELECT evidence FROM findings WHERE finding_id = ?", (fid,)).fetchone()
    ev = json.loads(row[0])
    ev["codes"] = sorted(set(ev.get("codes") or []) | {"99214"})
    ev["unpaid"] = sorted((ev.get("unpaid") or []) + [["99214", "2026-04-01"]])
    w.store.conn.execute("UPDATE findings SET evidence = ? WHERE finding_id = ?", (json.dumps(ev), fid))
    w.store.conn.commit()
    _let_go_the_99214_line(w)
    w.run("r2", today="2026-06-12")
    codes2, fid2 = _claim_codes(w)
    assert fid2 == fid and "99214" in codes2, codes2
    w.store.close()
