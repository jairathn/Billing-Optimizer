"""
The owner's decision 4 of 2026-11-26 (plans/HANDOFF.md section 3b): codes a payer almost never pays are pursued, and
each such item says why the payer does not pay the code and what the note or the claim must show
(remitwatch.rarely_paid, remitwatch/data/payer_code_rules.json).

Every claim, member, name, amount and date here is invented (tests/test_features, tests/test_settled_elsewhere,
tests/ema_fixtures). The payer groups and codes are the code's own (codes.PAYER_RULES) and public CPT/HCPCS codes. Each
case that runs a detection runs on both kinds of store: the server's file store and the compact in-memory store of the
serverless host.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from remitwatch import codes
from remitwatch import rarely_paid as rp
from remitwatch.detect import RULE_FILES, Detector
from tests.test_features import claim, svc
from tests.test_settled_elsewhere import _add, _store

KINDS = ["file", "compact"]
ROOT = Path(__file__).resolve().parents[1]
ANTHEM = "Anthem / BCBS"


# ===================================================================================================== the data file
def _groups_the_code_names() -> set[str]:
    names = {g for _p, g, _k in codes.PAYER_RULES}
    # payer_group_kind appends the product to a company's group (a Medicare Advantage or Medicaid plan of it)
    return names | {g + " Medicare Advantage" for g in names} | {g + " Medicaid" for g in names}


def test_the_data_file_holds_public_facts_about_payers_and_nothing_of_the_practice():
    """remitwatch/data/payer_code_rules.json: per payer group and code, the verdict, the payer's rule, what the note or
    claim must show and the sources (title, https link, date, the sentence quoted, read twice). No count, no dollar, no
    patient, no payer name of the practice's own: payer groups are the code's (codes.py), codes are CPT/HCPCS."""
    d = json.loads((ROOT / "remitwatch" / "data" / "payer_code_rules.json").read_text(encoding="utf-8"))
    assert set(d) == {"about", "version", "verdicts", "rules", "sources"}
    assert set(d["verdicts"]) == set(rp.VERDICTS)
    groups = _groups_the_code_names()
    used = set()
    for r in d["rules"]:
        assert r["payer_group"] in groups, r["payer_group"]
        assert all(a in groups for a in r.get("payer_group_aliases") or []), r.get("payer_group_aliases")
        assert re.fullmatch(r"[0-9A-Z]\d{3}[0-9A-Z]", r["code"]), r["code"]
        parts = r.get("by_service_date") or [r]
        for p in parts:
            assert p["verdict"] in rp.VERDICTS and len(p["rule"]) > 40 and len(p["what_to_show"]) > 20, r["code"]
        for sd in [r.get("service_dates") or {}] + [p for p in r.get("by_service_date") or []]:
            for k in ("from", "to"):
                if sd.get(k):
                    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", sd[k])
        if r.get("by_service_date"):          # the periods of a split rule never overlap
            spans = sorted((p.get("from") or "0000", p.get("to") or "9999") for p in r["by_service_date"])
            assert all(a[1] < b[0] for a, b in zip(spans, spans[1:])), spans
        assert r.get("applies_when") in (None,) + rp.CONDITIONS
        for s in r["sources"]:
            assert s["source"] in d["sources"] and s["quote"].strip()
            used.add(s["source"])
    assert used == set(d["sources"])
    for s in d["sources"].values():
        assert s["url"].startswith("https://") and s["title"] and s["date"] and s["checked_twice"] is True
    text = json.dumps(d, ensure_ascii=False)
    # nothing of the practice: no dollars, no counts of its lines or items, no work-list words
    assert not re.search(r"\$\s?\d", text)
    assert not re.search(r"\b\d+ of \d+\b|\bopen items?\b|\bWork now\b|\d+ items?\b", text)
    assert len(d["rules"]) == 24 and len(d["sources"]) == 15


def test_the_rules_are_part_of_the_rules_version():
    """A change to the researched rules or to their code analyses the archive again once, like any rule change."""
    assert "rarely_paid.py" in RULE_FILES and any(str(f).endswith("payer_code_rules.json") for f in RULE_FILES)


# ======================================================================================================== matching
def test_an_entry_matches_its_payer_group_code_and_service_dates_to_the_day():
    """Anthem's 77600 is never payable from 2026-01-01 (CPT 2026): a 2025 service is not covered by the rule (Anthem paid
    it then), the year's first day and a leap day are. UnitedHealthcare's G6001 rule runs to 2025-12-31 (the code ended)."""
    assert rp.entry_for(ANTHEM, "77600", "2025-12-31") is None
    for day in ("2026-01-01", "2028-02-29"):
        e = rp.entry_for(ANTHEM, "77600", day)
        assert (e["verdict"], e["match"], e["period"]) == ("never_payable", "exact", {"from": "2026-01-01", "to": None})
        assert any("CPT" in s["title"] for s in e["sources"]) and all(s["url"].startswith("https://") for s in e["sources"])
    assert rp.entry_for("UnitedHealthcare", "G6001", "2025-12-31")["verdict"] == "never_payable"
    assert rp.entry_for("UnitedHealthcare", "G6001", "2026-01-01") is None
    assert rp.entry_for(ANTHEM, "77600", None) is None              # a rule bound to dates needs the service date
    assert rp.entry_for("Anthem / BCBS", "G2211", None)["verdict"] == "never_payable"      # one that is not, does not
    assert rp.entry_for("Cigna", "77600", "2026-03-02") is None and rp.entry_for(None, "77600", "2026-03-02") is None
    assert rp.entry_for(ANTHEM, "77600 ", "2026-03-02") is not None                         # a stray space is the code


def test_a_rule_that_changed_on_a_date_gives_each_period_its_own_key():
    """Optum's 77600: never payable for 2026 services (CPT 2026), fixable on the claim before (the VA referral). Each
    period is its own pattern in the owner's table, so the owner can accept the part no note changes and keep the rest."""
    k26, k25 = rp.pattern_key("Optum (UHC)", "77600", "2026-02-01"), rp.pattern_key("Optum (UHC)", "77600", "2025-12-31")
    assert (k26, k25) == ("Optum (UHC)|77600|from 2026-01-01|payer_policy", "Optum (UHC)|77600|to 2025-12-31|payer_policy")
    assert rp.entry_for("Optum (UHC)", "77600", "2026-02-01")["verdict"] == "never_payable"
    assert rp.entry_for("Optum (UHC)", "77600", "2025-06-01")["verdict"] == "claim"
    assert rp.parse_key(k26)["period"] == {"from": "2026-01-01", "to": None} and rp.parse_key(k25)["period"] == {"from": None, "to": "2025-12-31"}
    assert rp.pattern_key(ANTHEM, "77600", "2026-02-01") == "Anthem / BCBS|77600|from 2026-01-01|payer_policy"   # bound to dates
    assert rp.pattern_key(ANTHEM, "77600", "2025-06-02") == "Anthem / BCBS|77600||payer_policy"       # outside them: not its rule
    assert rp.pattern_key(ANTHEM, "G2211", "2025-06-02") == "Anthem / BCBS|G2211||payer_policy"       # a rule with no dates
    assert rp.pattern_key("Cigna", "17110", "2026-02-01") == "Cigna|17110||payer_policy"            # not researched
    from remitwatch.api_ema import kind_of, split_pattern
    assert kind_of(k26) == "payer_policy" and split_pattern(k26)["modifier"] == "from 2026-01-01"


def test_another_spelling_of_the_group_counts_only_where_the_run_found_the_pair_rarely_paid():
    """The research names UnitedHealthcare's Medicare Advantage plans one way and lists the code's other spelling; it puts
    Kentucky Medicaid's codes under 'Medicaid (other)'. Such a match applies to an item only where the run found the pair
    rarely paid: Kentucky Medicaid pays most 99213 lines when the claim is right."""
    e = rp.entry_for("UHC Medicare Advantage", "G6001", "2025-05-01")
    assert e["match"] == "alias" and not rp.applies(e, False) and rp.applies(e, True)
    e = rp.entry_for("Medicaid KY", "99213", "2025-05-01")
    assert e["match"] == "alias" and not rp.applies(e, False)
    assert rp.applies(rp.entry_for("Medicaid KY", "G2211", None), False)           # its own entry
    assert rp.is_rare(1, 20) and not rp.is_rare(2, 20) and not rp.is_rare(0, 19) and not rp.is_rare(None, "x")
    f = {"signal": "DENIED_OTHER", "category": "denial", "line_key": "L1", "paid_cents": 0, "payer_group": "Medicaid KY",
         "procedure_code": "99213", "service_date": "2025-05-01"}
    assert rp.block_for(f, "99213", "2025-05-01", pairs={}) is None
    b = rp.block_for(f, "99213", "2025-05-01", pairs={"Medicaid KY|99213||payer_policy": {"answered": 40, "paid": 1, "code": "99213"}})
    assert b["researched_verdict"] == "claim" and b["match"] == "alias"


def test_which_codes_of_an_item_can_be_such_a_pair():
    """The code the practice billed on an unpaid denial line (a code the payer changed and paid nothing on included), each
    line of a denial only EMA saw, each unpaid code of a whole-claim denial (up to six), G2211 for a payer's G2211 denials;
    never a paid line, a short payment, or a claim sent on to another carrier."""
    base = {"line_key": "L1", "paid_cents": 0, "service_date": "2026-03-02", "payer_group": ANTHEM}
    assert rp.item_codes(dict(base, signal="PROCEDURE_BUNDLED", category="bundling", procedure_code="77600")) == [("77600", "2026-03-02")]
    assert rp.item_codes(dict(base, signal="CODE_CHANGED", category="downcoding", procedure_code="77436",
                              original_procedure_code="77600")) == [("77600", "2026-03-02")]
    assert rp.item_codes(dict(base, signal="DENIED_AUTH", category="denial", procedure_code="G6001", paid_cents=100)) == []
    assert rp.item_codes(dict(base, signal="UNDERPAID_VS_OWN_RATE", category="underpayment", procedure_code="77600")) == []
    assert rp.item_codes(dict(base, signal="FORWARDED_TO_CARRIER", category="denial", procedure_code="77600")) == []
    ev = {"unpaid": [["99213", "2025-04-01"], ["G2211", "2025-04-01"], ["99213", "2025-04-01"]] + [[f"1110{i}", None] for i in range(8)]}
    got = rp.item_codes({"claim_key": "C1", "signal": "CLAIM_DENIED_UNPAID", "category": "denial", "evidence": json.dumps(ev),
                         "service_date": "2025-04-01"})
    assert got[:2] == [("99213", "2025-04-01"), ("G2211", "2025-04-01")] and len(got) == 6
    ema = {"signal": "EMA_DENIED", "category": "denial", "evidence": {"lines": [["77600", "2026-02-02", 300], ["77436", "2026-02-02", 200]]}}
    assert rp.item_codes(ema) == [("77600", "2026-02-02"), ("77436", "2026-02-02")]
    assert rp.item_codes({"signal": "G2211_DENIED", "category": "denial"}) == [("G2211", None)]
    assert rp.item_codes({"signal": "NO_ANSWER", "category": "unanswered", "claim_key": None}) == []


# ===================================================================================== the run's evidence and the drawer
DOS25, DOS26 = "20250602", "20260302"


def _anthem_radiation(store, tmp_path) -> None:
    """Anthem paid 77600 in 2025 (25 visits, $322 allowed). In 2026 it bundles it into the surface radiation code: 25
    claims pay 77436 and deny 77600 (CO-97, N19), three more answer the 77600 billed as 77436 and pay nothing, and one
    2025 visit has a 77600 denied too. Aetna denied 17110 on 22 lines and paid none (a pair nobody researched)."""
    paid25 = [claim(i, svc("77600", "861.00", "322.00", "322.00", "CAS*CO*45*539.00~", dos=DOS25), dos=DOS25) for i in range(1, 26)]
    paid25.append(claim(26, svc("77600", "861.00", "0.00", None, "CAS*CO*97*861.00~LQ*HE*N19~", dos=DOS25), dos=DOS25))
    _add(store, tmp_path, paid25, "20250620")
    bundled = [claim(100 + i, svc("77436", "400.00", "150.00", "150.00", "CAS*CO*45*250.00~", dos=DOS26)
                     + svc("77600", "861.00", "0.00", None, "CAS*CO*97*861.00~LQ*HE*N19~", dos=DOS26), dos=DOS26) for i in range(25)]
    changed = [claim(200 + i, svc("77436", "861.00", "0.00", None, "CAS*CO*97*861.00~", dos=DOS26, orig="77600"), dos=DOS26) for i in range(3)]
    _add(store, tmp_path, bundled + changed, "20260320")
    aetna = [claim(300 + i, svc("17110", "180.00", "0.00", None, "CAS*CO*96*180.00~LQ*HE*N130~", dos=DOS26, mods="59"), dos=DOS26)
             for i in range(22)]
    _add(store, tmp_path, aetna, "20260320", payer="AETNA", payer_id="AETNA")


def _items(s, where: str, *args) -> list[dict]:
    return [dict(r) for r in s.query(f"SELECT * FROM findings WHERE COALESCE(stale,0)=0 AND {where}", args)]


@pytest.mark.parametrize("kind", KINDS)
def test_the_run_works_out_why_the_payer_does_not_pay_from_the_remittances(tmp_path, kind):
    """Each run keeps, per pair, what the remittances show (meta rarely_paid:why): Anthem answered 28 lines billed as
    77600 for 2026 services and paid none, its reasons and remarks on them, the codes billed with them, how often it paid
    the claim's other line, and the code it answered instead. Counts, codes and years only. The researched pair is kept
    though Anthem paid half of its 77600 lines since 2025 (the 2025 ones); the 2025 denial has no block (the rule starts
    in 2026); Aetna's 17110, rarely paid and not researched, is kept with no rule."""
    from remitwatch import story
    s = _store(tmp_path, kind)
    _anthem_radiation(s, tmp_path)
    det = Detector(s, run_id="t1", today="2026-04-01")
    det.run()
    meta = json.loads(s.conn.execute("SELECT value FROM meta WHERE key=?", (rp.WHY_META,)).fetchone()[0])
    ev = meta["pairs"]["Anthem / BCBS|77600|from 2026-01-01|payer_policy"]
    assert (ev["answered"], ev["paid"], ev["unpaid"], ev["from"]) == (28, 0, 28, "2026-01-01")
    assert ev["by_year"] == {"2026": [0, 28]} and ev["recoded"] == {"77436": [3, 0]}
    assert ev["reasons"][0] == ["CO-97", 28] and ev["remarks"][0] == ["N19", 25]
    assert ev["with_codes"] == [["77436", 25]] and ev["other_line_paid"] == 25 and ev["pos_unpaid"][0][0] == "11"
    assert meta["pairs"]["Aetna|17110||payer_policy"]["answered"] == 22
    assert meta["pairs"]["Aetna|17110||payer_policy"]["modifiers_unpaid"] == [["59", 22]]
    assert det.ema_counts["rarely_paid_pairs"] == 2
    raw = s.conn.execute("SELECT value FROM meta WHERE key=?", (rp.WHY_META,)).fetchone()[0]
    assert "SAMPLE" not in raw and "CB0000" not in raw and "M000" not in raw         # nothing of a patient or a claim

    bundled = [f for f in _items(s, "payer_group=? AND procedure_code='77600' AND service_date LIKE '2026%'", ANTHEM)]
    changed = _items(s, "signal='CODE_CHANGED' AND original_procedure_code='77600'")
    assert len(bundled) == 25 and len(changed) == 3
    for f in bundled + changed:
        assert f["status"] == "open" and f["work_state"] == "work_now"              # pursued: never accepted by default
    x = story.explain(s.conn, bundled[0]["finding_id"], today="2026-04-01")
    (b,) = x["why_unpaid"]
    assert (b["code"], b["verdict"], b["pattern"], b["single"]) == ("77600", "never_payable", "Anthem / BCBS|77600|from 2026-01-01|payer_policy", True)
    assert b["verdict_text"].startswith("Not payable under the payer's published rule: Anthem / BCBS does not pay 77600 here")
    assert "stays on Work now until the owner accepts the pattern" in b["verdict_text"]
    assert "77436" in b["rule"] and "No note changes this" in b["what_to_show"]
    assert any("CPT" in src["title"] for src in b["sources"])
    words = " ".join(b["evidence_text"])
    assert "answered 28 77600 lines as the primary payer for services from 2026-01-01, and paid 0 (0%)" in words
    assert "CO-97 (28" in words and "N19 (25" in words and "77436 on 3, paid on 0" in words and "On 25 of the 28 unpaid lines" in words
    nx = x["next_step"]
    assert nx["action"].startswith("Not payable under Anthem / BCBS's published rule") and nx["payer_rule"] == "never_payable"
    assert any(st.startswith("No note changes this") for st in nx["steps"]) and x["state"]["work_state"] == "work_now"
    assert "accept_pattern" not in nx["buttons"]          # no owner's table without EMA's reports: nothing to accept yet
    (bc,) = story.explain(s.conn, changed[0]["finding_id"], today="2026-04-01")["why_unpaid"]
    assert (bc["code"], bc["verdict"]) == ("77600", "never_payable")
    old = _items(s, "payer_group=? AND procedure_code='77600' AND service_date LIKE '2025%'", ANTHEM)
    assert len(old) == 1 and story.explain(s.conn, old[0]["finding_id"], today="2026-04-01")["why_unpaid"] == []
    aetna = _items(s, "payer_group='Aetna' AND procedure_code='17110'")
    (ba,) = story.explain(s.conn, aetna[0]["finding_id"], today="2026-04-01")["why_unpaid"]
    assert ba["verdict"] is None and ba["rule"] is None and ba["verdict_text"].startswith("Not researched yet")
    assert "paid 0 (0%)" in " ".join(ba["evidence_text"])
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_a_failing_evidence_step_drops_the_old_evidence_and_the_run_goes_on(tmp_path, kind, monkeypatch):
    """The evidence step is contained: a failure is counted and logged, the evidence of an earlier run is removed (never
    shown as today's), the items are the same."""
    s = _store(tmp_path, kind)
    _anthem_radiation(s, tmp_path)
    Detector(s, run_id="t1", today="2026-04-01").run()
    before = {r["finding_id"] for r in _items(s, "1=1")}
    monkeypatch.delenv("REMITWATCH_EMA_STRICT", raising=False)

    def boom(*a, **k):
        raise RuntimeError("the evidence step failed")
    monkeypatch.setattr(rp, "evidence", boom)
    det = Detector(s, run_id="t2", today="2026-04-01")
    det.run()
    assert det.ema_counts["rarely_paid_failed"] == 1
    assert s.conn.execute("SELECT 1 FROM meta WHERE key=?", (rp.WHY_META,)).fetchone() is None
    assert {r["finding_id"] for r in _items(s, "1=1")} == before
    s.close()


# =============================================================================================== a rule's condition
def test_the_g2211_rule_of_medicare_needs_modifier_25_on_the_visit():
    """Medicare does not pay G2211 when its E/M carries modifier 25 for a procedure (MM13473). With 25 on the claim's E/M
    the block says never payable; without it the rule does not explain the denial, and the block says so (unclear); with
    no claim lines to check, it says it cannot tell."""
    f = {"signal": "CLAIM_DENIED_UNPAID", "category": "denial", "claim_key": "C1", "payer_group": "Medicare KY (CGS J15)",
         "service_date": "2026-02-03", "evidence": {"unpaid": [["G2211", "2026-02-03"]]}}
    with25 = [{"procedure_code": "99213", "modifiers": '["25"]', "service_date": "2026-02-03"}, {"procedure_code": "G2211", "modifiers": None}]
    plain = [{"procedure_code": "99213", "modifiers": None, "service_date": "2026-02-03"}]
    b = rp.block_for(f, "G2211", "2026-02-03", lines=with25)
    assert b["verdict"] == "never_payable" and b["condition"] is None and not b["single"]
    assert "Only this item's G2211 is concerned" in b["verdict_text"]
    b = rp.block_for(f, "G2211", "2026-02-03", lines=plain)
    assert b["verdict"] == "unclear" and b["researched_verdict"] == "never_payable" and "does not explain this denial" in b["condition"]
    b = rp.block_for(f, "G2211", "2026-02-03", lines=[])
    assert b["verdict"] == "unclear" and "not on file" in b["condition"]
    assert rp.checklist(dict(f, evidence=json.dumps(f["evidence"])), plain)[0].startswith("Medicare KY (CGS J15)'s rule for G2211")


# ==================================================================================== the packet and the note review
def _detail(f: dict, lines=None) -> dict:
    return {"finding": f, "lines": lines or [], "adjustments": [], "remarks": [], "remit": {}, "claim": {}, "ema": []}


def _bundled_item(**kw) -> dict:
    f = {"finding_id": "F1", "signal": "PROCEDURE_BUNDLED", "category": "bundling", "line_key": "L1", "claim_key": "C1",
         "payer_group": ANTHEM, "payer_kind": "commercial", "procedure_code": "77600", "service_date": "2026-03-02",
         "paid_cents": 0, "carc": "97", "rarc": "N19", "expected_cents": 32200, "shortfall_cents": 32200, "evidence": {},
         "patient_control_number": "CB0000101C017"}
    f.update(kw)
    return f


def test_the_packet_checklist_says_a_never_payable_rule_before_sending_and_encloses_nothing_against_itself():
    """Anthem's 77600 for 2026: the packet's checklist says first that the rule does not pay it and what the rule still
    allows; the rule is not among the enclosures (they go to the payer in the PDF). Humana Medicare Advantage's G0127, a
    claim the rule supports with the right modifier and note: the checklist says what the note and the claim must show,
    and the payer's published rule is enclosed with its address."""
    from remitwatch.appeals import build_packet
    p = build_packet(_detail(_bundled_item()))
    assert p["checklist"][0].startswith("Before sending: Anthem / BCBS does not pay 77600 under its published rule")
    assert "No note changes this" in p["checklist"][0]
    assert not [a for a in p["attachments"] if "CPT" in a or "Before sending" in a]
    hum = _bundled_item(signal="DENIED_NONCOVERED", category="denial", payer_group="Humana Medicare Advantage",
                        payer_kind="medicare_advantage", procedure_code="G0127", carc="96", rarc="N386")
    p = build_packet(_detail(hum))
    assert p["checklist"][0].startswith("What the note or claim must show for Humana Medicare Advantage to pay G0127")
    assert "Q7, Q8 or Q9" in p["checklist"][0]
    enc = [a for a in p["attachments"] if "https://www.cms.gov/" in a]
    assert enc and all("with the passage the record meets highlighted" in a for a in enc)
    # an item no research covers: the packet is as it was
    p = build_packet(_detail(_bundled_item(procedure_code="17311", signal="PROCEDURE_BUNDLED")))
    assert p["checklist"] == []


def test_the_packet_pdf_never_carries_the_checklist():
    from remitwatch.appeals import build_packet
    from remitwatch.notes import render_pdf
    p = build_packet(_detail(_bundled_item()))
    pdf = render_pdf(dict(p, analysis={}, note_name=""))
    assert b"Before sending" not in pdf and b"%PDF" in pdf[:8]


class _Text:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Msg:
    stop_reason = "end_turn"
    model = "test-model"

    def __init__(self, text):
        self.content = [_Text(text)]


class _Stream:
    def __init__(self, msg):
        self.msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.msg


class _Client:
    """Records what the review is asked."""
    def __init__(self, answer: dict):
        self.asked: list = []
        self.answer = answer
        outer = self

        class _M:
            def stream(self, **kw):
                outer.asked.append(kw)
                return _Stream(_Msg(json.dumps(outer.answer)))
        self.messages = _M()


def _review_answer() -> dict:
    el = {"level": "not_applicable", "rationale": "x", "quotes": []}
    return {"visit_summary": "A visit.", "problems": el, "data": el, "risk": el, "mdm_level": "not_applicable", "time_documented": "none",
            "supported_code": "not_applicable", "supports_billed": "no",
            "modifier_25": {"relevant": False, "separately_identifiable": "not_applicable", "rationale": "", "quotes": []},
            "procedure_support": "not_applicable", "strengths": [], "gaps": [], "recommendation": "do_not_appeal",
            "recommendation_reason": "The rule does not pay it.", "clinical_paragraphs": [], "documentation_tips": []}


def test_the_note_review_is_told_the_payers_rule_and_what_the_note_must_show():
    """The note review's input carries the researched rule, what the note or the claim must show and, for a rule no
    note changes, that it should recommend against the appeal unless the note shows what the rule allows."""
    from remitwatch import notes
    ctx = notes.dispute_context(_detail(_bundled_item()))
    (r,) = ctx["payer_rules"]
    assert (r["code"], r["verdict"]) == ("77600", "never_payable") and "77436" in r["rule"]
    client = _Client(_review_answer())
    notes.review_note(ctx, "Visit note text.", client=client)
    asked = client.asked[0]["messages"][0]["content"][0]["text"]
    assert "The payer's published rule for 77600" in asked and "What the note or the claim must show: No note changes this" in asked
    assert "recommend against the appeal" in asked
    # an item no research covers is asked as before
    ctx = notes.dispute_context(_detail(_bundled_item(procedure_code="17311")))
    client = _Client(_review_answer())
    notes.review_note(ctx, "Visit note text.", client=client)
    assert "published rule" not in client.asked[0]["messages"][0]["content"][0]["text"]
