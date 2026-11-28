"""
Payer routes (unit `routes`, 2026-11-28): each item's request goes to its payer the easiest way the payer accepts, with
the fields filled (remitwatch.routes, remitwatch/payer_routes.json).

- the researched table holds together: every channel has a source, ranks are unique, every request type and channel is
  known, the verifier's contradicted facts are gone, the unverifiable ones are marked, numbers are written one way and
  none looks like a tax ID or an NPI;
- the request type follows the item's route and facts (Medicare redetermination or reopening, Medicare Advantage or
  Original, the second level after a recorded first denial, a timely filing denial, a claim status request with the proof
  beside it near the filing limit), and work that goes to no payer gets no card;
- the payer's group and plan (BlueCard, TriWest, MHS, HealthSpring, a plan not covered) and the plain fallback;
- the fields: from the store and the PRACTICE_ settings, never invented; no patient name or member number with names off,
  in any field or in Copy all;
- the fax rule: the published number is shown, a fax goes only to the number a person confirmed;
- the API carries the card on the server's store and on Vercel's dashboard copy alike.

Every row is invented (the builders of tests/test_states.py).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sqlite3

import pytest

from remitwatch import actions, cloud, ema_checks as ec, ema_reports as er, routes, states, story
from tests import ema_fixtures as F
from tests.test_states import TODAY, denied, event, item, line_key, new_store, remit, ln

TABLE = routes.table()
NAME, MEMBER = "TESTPATIENT", "MEMBER-TEST-1"      # what tests/test_states.remit writes on every claim


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(er, "CLAIM_HOST_SUFFIX", F.HOST_SUFFIX)
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    for k in ("VERCEL", "REMITWATCH_EMA_REPORTS", "REMITWATCH_EMA_CLAIM_URL", "REMITWATCH_EMA_PAYER_ALIASES", "REMITWATCH_EMA_LEDGER",
              "REMITWATCH_EMA_REPORT_KEEP", "SRFAX_CALLER_ID", *routes.SETTINGS):
        monkeypatch.delenv(k, raising=False)
    ec._LAST_POST.clear()
    states._RESPONSE_CACHE.clear()
    states._RECOVERY_CACHE.clear()


@pytest.fixture
def store(tmp_path):
    s = new_store(str(tmp_path / "store.sqlite"))
    yield s
    s.close()


def world(store, *, payer="BLUE CROSS TEST", reason=("CO", "50"), cfi="12", code="99213", pccn="ICN-TEST-1", **kw):
    """One denied line answered by `payer` and its item (an appeal of a medical necessity denial by default)."""
    tk = denied(store, reason=reason, code=code, payer=payer, cfi=cfi, pccn=pccn)
    f = {"signal": "DENIED_MEDICAL_NECESSITY", "carc": reason[1], "actionable": "appeal", "procedure_code": code, "billed_code": code}
    f.update(kw)
    item(store, line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk, payer_name=payer, **f)
    return tk


def card(store, fid="F1", today=TODAY, names=False):
    ex = story.explain(store.conn, fid, today=today)
    return routes.card(store.conn, fid, explain=ex, today=today, show_names=names), ex


def every_text(c) -> str:
    return json.dumps(c, ensure_ascii=False)


# ================================================================================================ the researched table
def test_the_table_was_checked_on_the_day_and_names_every_request_and_channel():
    assert TABLE["checked"] == "2026-11-28"
    assert set(TABLE["requests"]) == {"medicare_reopening", "medicare_redetermination", "reconsideration_or_first_appeal",
                                      "second_level_appeal", "corrected_claim", "records_or_attachments", "overpayment_refund",
                                      "claim_status_or_reprocess", "timely_filing_proof"}
    assert set(TABLE["channels"]) == {"payer_portal", "clearinghouse", "phone", "fax", "mail", "email", "not_accepted"}
    groups = set(TABLE["groups"])
    for r in TABLE["routes"]:
        assert r["request"] in TABLE["requests"], r["key"]
        assert r["group"] in groups, r["key"]
        assert r["time_limit"].strip(), r["key"]
        assert r["confidence"] in ("high", "medium", "low"), r["key"]
        for c in r["channels"]:
            assert c["channel"] in TABLE["channels"], (r["key"], c["name"])
    # every group a route card can reach has rows, and every not-covered plan has its words
    for _rx, key in routes.GROUP_MAP:
        assert any(r["group"] == key for r in TABLE["routes"]), key
    for _rx, key in routes.NOT_COVERED_MAP:
        assert TABLE["not_covered"][key]


def test_every_channel_has_a_source_with_its_address_title_publisher_and_date():
    src = TABLE["sources"]
    for r in TABLE["routes"]:
        for c in r["channels"]:
            assert c["sources"], (r["key"], c["name"])
            for s in c["sources"]:
                assert s in src, (r["key"], s)
                assert src[s]["url"].startswith(("https://", "http://")) and src[s]["title"] and src[s]["publisher"], s
                assert "date" in src[s]


def test_ranks_are_unique_and_the_refused_ways_come_last():
    for r in TABLE["routes"]:
        ranks = [c["rank"] for c in r["channels"]]
        assert ranks == list(range(1, len(ranks) + 1)), r["key"]
        kinds = [c["channel"] == "not_accepted" for c in r["channels"]]
        assert kinds == sorted(kinds), r["key"]           # every accepted way before any refused one
        assert any(not k for k in kinds), r["key"]        # and at least one accepted way


def test_an_unverified_channel_says_why_and_a_low_confidence_row_is_never_called_verified():
    n = 0
    for r in TABLE["routes"]:
        for c in r["channels"]:
            assert isinstance(c["verified"], bool)
            if not c["verified"]:
                n += 1
                assert c.get("unverified_why"), (r["key"], c["name"])
            if r["confidence"] == "low":
                assert not c["verified"], (r["key"], c["name"])
    assert n >= 10


def _channel(key, name_part):
    r = next(r for r in TABLE["routes"] if r["key"] == key)
    return r, [c for c in r["channels"] if name_part.lower() in c["name"].lower()]


def test_the_verifiers_contradicted_and_outdated_facts_are_gone():
    # CGS's IVR asks for the date of service (MMDDYY), never 'date of service or ICN'
    _, (ivr,) = _channel("m6", "IVR, option 1")
    assert "Date of service (MMDDYY)" in ivr["fields"] and "Date of service or ICN" not in ivr["fields"]
    # WPS's timely filing proof is a retroactive entitlement letter, not a Medicaid recoupment letter; the address is known
    _, (wps,) = _channel("m15", "waiver")
    assert not any("Medicaid recoupment" in a for a in wps["attachments"]) and "PO Box 8940" in wps["where"]
    # the C2C portal takes one PDF, not form fields
    for key in ("m2", "m10", "m18"):
        _, (c2c,) = _channel(key, "Appeals Portal")
        assert any("one PDF" in f for f in c2c["fields"]) and not any("Reason for disagreement" == f for f in c2c["fields"])
    # Railroad Medicare's late redetermination uses the consolidated form, not AP-RRB-B-1001
    m17 = next(r for r in TABLE["routes"] if r["key"] == "m17")
    assert "AP-RRB-B-1001" not in m17["time_limit"] and "AP-RRB-B-1000" in m17["time_limit"]
    # Anthem: records it asked for go through Send Attachment; the 837 PWK route (unsolicited records) is not one of them
    a4 = next(r for r in TABLE["routes"] if r["key"] == "a4")
    assert not any(c["channel"] == "clearinghouse" for c in a4["channels"])
    # Anthem's 800-345-4344 is not the Exchange members' number
    _, (ph,) = _channel("a0", "Provider Services")
    assert "800-676-2583" in ph["notes"]
    # Humana Medicare Advantage: a corrected claim within 1 year of the original determination; the fax for nonpar appeals
    a23 = next(r for r in TABLE["routes"] if r["key"] == "a23")
    assert "1 year from the original claim determination" in a23["time_limit"]
    _, faxes = _channel("a21", "fax")
    assert faxes and faxes[0]["where"] == "800-949-2961"
    # TRICARE's claims auditing reconsideration has the allowable charge review's 90 days
    assert "90 calendar days" in next(r for r in TABLE["routes"] if r["key"] == "a45")["time_limit"]
    # UnitedHealthcare: no EDI 275 route for records it asked for
    for key in ("u4", "u11"):
        assert not any(c["channel"] == "clearinghouse" for c in next(r for r in TABLE["routes"] if r["key"] == key)["channels"])
    # UMR: providers file a claim inquiry; only the member appeals (UM2057)
    _, (umr,) = _channel("u24", "claim inquiry")
    assert "Only the member can appeal" in umr["notes"]
    # the VA's Optum overpayment: 30 days and a written dispute address
    u45 = next(r for r in TABLE["routes"] if r["key"] == "u45")
    assert "30 calendar days" in u45["time_limit"] and any("P.O. Box 202118" in c["where"] for c in u45["channels"])
    # Aetna's appeal forms in their printed order; Cigna's form fields as printed; the GWH-Cigna address
    _, (gr,) = _channel("o2", "Appeal form by fax")
    assert gr["fields"][:4] == ["Today's Date", "Member's ID Number", "Plan Type", "Member's Group Number (Optional)"]
    _, (cig,) = _channel("o9", "Payment review")
    assert "Account Number (from the Cigna ID card)" in cig["fields"] and "PO Box 188062" in cig["notes"]
    # MHS: the clearinghouse's rejection report is the proof, not a route
    o29 = next(r for r in TABLE["routes"] if r["key"] == "o29")
    assert not any(c["channel"] == "clearinghouse" for c in o29["channels"])
    assert any("payer rejection report" in a for c in o29["channels"] for a in c["attachments"])
    # Indiana Medicaid: a professional paper claim goes to the CMS-1500 box, the adjustment takes the TPL/Medicare form
    _, (ihcp,) = _channel("o56", "Paper claim")
    assert "PO Box 50447" in ihcp["where"]
    _, (adjf,) = _channel("o53", "adjustment request")
    assert any("TPL" in a for a in adjf["attachments"])
    # every correction names why and (but for the duplicate rows) its evidence
    for c in TABLE["corrections"]:
        assert c["why"] and c["change"] in ("fixed", "dropped", "added", "unverified")


def test_the_verifiers_missing_facts_with_sources_are_in():
    keys = {(r["group"], r["request"], r["variant"]) for r in TABLE["routes"]}
    assert ("HealthSpring", "reconsideration_or_first_appeal", "main") in keys          # Cigna Medicare Advantage is HealthSpring
    assert ("Anthem / BCBS", "reconsideration_or_first_appeal", "clinical") in keys
    va = [r for r in TABLE["routes"] if r["group"] == "VA Community Care" and r["variant"] == "main"]
    assert any("844-839-6108" == c["where"] for r in va for c in r["channels"])      # Kentucky and Indiana are Region 2 (Optum)
    assert all("faxAG1" not in c["where"] for r in va for c in r["channels"])        # Region 2's address first


def test_numbers_are_written_one_way_and_none_looks_like_a_tax_id_or_an_npi():
    text = routes.TABLE_PATH.read_text(encoding="utf-8")
    assert not re.search(r"\(\d{3}\)\s?\d{3}-\d{4}|\b1[.-]\d{3}[.-]\d{3}[.-]\d{4}\b|\b\d{3}\.\d{3}\.\d{4}\b", text)
    assert not re.findall(r"(?<![\d-])(\d{2}-\d{7})(?![\d-])", text)
    assert not re.findall(r"(?<![\d/:.])([12]\d{9})(?!\d)", text)
    assert "866-290-4036" in text          # CGS's IVR, as the brief writes it


def test_the_field_rules_read_every_label_the_table_prints():
    labels = {f for r in TABLE["routes"] for c in r["channels"] for f in c["fields"]}
    kinds = {routes.classify(x)[0] for x in labels}
    assert kinds <= {"step", "set", "field", "other"}
    assert routes.classify("Medicare Beneficiary Identifier (MBI)") == ("field", ("member_id",))
    assert routes.classify("Claim ICN (from the CGS remittance advice)") == ("field", ("payer_claim_number",))
    assert routes.classify("Last 5 digits of Tax ID Number") == ("field", ("practice_tin_last5",))
    assert routes.classify("Patient last name, first 6 letters, then #") == ("field", ("patient_last6",))
    assert routes.classify("Claims & Payments > Look up a Claim") == ("step", ())
    assert routes.classify("CLM05-3 = 7") == ("field", ("frequency_code",))
    assert routes.classify("Beneficiary Phone Number") == ("field", ())        # the patient's own: filled by hand


# ================================================================================================ the request type
def test_an_original_medicare_appeal_is_a_redetermination_and_never_faxed_to_cgs(store):
    world(store, payer="KY MEDICARE PART B", payer_group="Medicare KY (CGS J15)", payer_kind="medicare", cfi="MB")
    c, ex = card(store)
    assert ex["next_step"]["route"] == "appeal"
    assert c["request"] == "medicare_redetermination" and c["group"] == "Medicare KY (CGS J15)"
    assert c["best"]["channel"] == "payer_portal" and "myCGS" in c["best"]["name"]
    assert any(n["name"] == "Fax" for n in c["not_accepted"])           # CGS takes no redetermination by fax


def test_a_clerical_fix_original_medicare_allows_is_a_reopening(store):
    world(store, payer="KY MEDICARE PART B", payer_group="Medicare KY (CGS J15)", payer_kind="medicare", cfi="MB", reason=("CO", "4"),
          signal="DENIED_MISSING_MODIFIER", actionable="correct_and_resubmit",
          deadline_basis="a reopening of the clerical error within 1 year of the remittance")
    c, ex = card(store)
    assert ex["next_step"]["route"] == "corrected"
    assert c["request"] == "medicare_reopening"
    assert "Reopenings form" in c["best"]["name"]
    assert any("by fax" in n["name"] for n in c["not_accepted"])        # "CGS does not take reopenings by fax"


def test_an_unprocessable_medicare_claim_is_a_new_claim_through_the_clearinghouse(store):
    world(store, payer="IN MEDICARE PART B", payer_group="Medicare IN (WPS J8)", payer_kind="medicare", cfi="MB", reason=("CO", "16"),
          signal="DENIED_MISSING_INFO", actionable="correct_and_resubmit", deadline_basis="MA130: a new claim within a year")
    c, _ = card(store)
    assert c["request"] == "corrected_claim" and c["best"]["channel"] == "clearinghouse" and "MA130" in c["best"]["name"]


def test_medicare_advantage_is_never_routed_as_original_medicare(store):
    world(store, payer="ANTHEM BLUE CROSS", payer_group="Anthem / BCBS Medicare Advantage", payer_kind="medicare_advantage", cfi="16")
    c, _ = card(store)
    assert c["request"] == "reconsideration_or_first_appeal" and c["group"] == "Anthem / BCBS Medicare Advantage"
    assert c["variant"] == "main" and any("Not contracted" in a["when"] for a in c["alternatives"])


def test_a_second_appeal_after_a_recorded_first_denial_is_the_second_level(store):
    world(store, payer="UNITEDHEALTHCARE", payer_group="UnitedHealthcare", payer_kind="commercial", status="in_progress")
    event(store, "F1", "manual", at="2026-04-01T10:00:00", frm="open", to="appealed")
    event(store, "F1", "manual", at="2026-04-20T10:00:00", frm="appealed", to="in_progress", note="The payer said no: upheld")
    c, _ = card(store)
    assert c["request"] == "second_level_appeal" and "post-service appeal" in c["best"]["name"]
    assert any("used as an appeal" in n["name"] for n in c["not_accepted"])


def test_the_payers_unpaid_answer_to_an_appeal_takes_it_to_the_next_level(store):
    tk = world(store, payer="UNITEDHEALTHCARE", payer_group="UnitedHealthcare", payer_kind="commercial", status="appealed",
               appealed_at="2026-04-10")
    event(store, "F1", "manual", at="2026-04-10T15:00:00", frm="open", to="appealed")
    remit(store, trace="T3", paid_on="2026-04-25", payer="UNITEDHEALTHCARE",
          claims=[{"pcn": F.claim_id(1), "pccn": "ICN-TEST-1", "received": "2026-03-05", "lines": [ln("99213", 150, 0, [("CO", "50", 150)], lcn="1")]}])
    assert tk
    c, ex = card(store, today="2026-04-28")
    assert (ex["state"] or {}).get("rule") == "work_answered"
    assert c["request"] == "second_level_appeal"


def test_a_corrected_claim_sent_again_and_denied_is_not_a_second_level(store):
    world(store, payer="UNITEDHEALTHCARE", payer_group="UnitedHealthcare", payer_kind="commercial", status="in_progress",
          signal="EM_BUNDLED_NO_MODIFIER", category="bundling", reason=("CO", "97"), actionable="correct_and_resubmit")
    event(store, "F1", "manual", at="2026-04-01T10:00:00", frm="open", to="appealed")
    event(store, "F1", "manual", at="2026-04-20T10:00:00", frm="appealed", to="in_progress", note="The payer said no: missing 25")
    c, _ = card(store)
    assert c["request"] == "corrected_claim"


def test_a_timely_filing_denial_sends_the_proof(store):
    world(store, payer="UNITEDHEALTHCARE", payer_group="UnitedHealthcare", payer_kind="commercial", signal="DENIED_TIMELY_FILING",
          reason=("CO", "29"))
    c, _ = card(store)
    assert c["request"] == "timely_filing_proof" and "proof of timely filing" in c["best"]["name"]


def test_a_claim_status_call_near_the_filing_limit_carries_the_timely_filing_proof(store):
    world(store, payer="UNITEDHEALTHCARE", payer_group="UnitedHealthcare", payer_kind="commercial", signal="NO_ANSWER",
          category="no_answer", carc=None, actionable="follow_up", appeal_deadline="2026-05-20")
    c, ex = card(store)
    assert ex["next_step"]["route"] == "call"
    assert c["request"] == "claim_status_or_reprocess" and c["also"]["request"] == "timely_filing_proof"
    assert c["best"]["action"]["label"] == "Check it in the clearinghouse"
    # far from the limit, no proof beside it
    item(store, finding_id="F1", appeal_deadline="2026-12-20", payer_group="UnitedHealthcare", payer_kind="commercial",
         payer_name="UNITEDHEALTHCARE", signal="NO_ANSWER", category="no_answer", carc=None, actionable="follow_up",
         line_key=line_key(store, "TX00001"), claim_key="TX00001-0", transaction_key="TX00001")
    c2, ex2 = card(store)
    if ex2["next_step"]["route"] == "call" and (ex2["next_step"].get("deadline") or "") > "2026-06-02":
        assert "also" not in c2


@pytest.mark.parametrize("route", sorted(routes.NO_CARD))
def test_work_that_goes_to_no_payer_gets_no_card(store, route):
    world(store, payer="UNITEDHEALTHCARE", payer_group="UnitedHealthcare", payer_kind="commercial")
    ex = story.explain(store.conn, "F1", today=TODAY)
    ex["next_step"] = dict(ex["next_step"], route=route)
    assert routes.card(store.conn, "F1", explain=ex, today=TODAY) is None


def test_a_refund_is_the_overpayment_route(store):
    world(store, payer="UNITEDHEALTHCARE", payer_group="UnitedHealthcare", payer_kind="commercial")
    ex = story.explain(store.conn, "F1", today=TODAY)
    ex["next_step"] = dict(ex["next_step"], route="refund")
    ex["state"] = dict(ex["state"] or {}, credit_cents=4200)
    c = routes.card(store.conn, "F1", explain=ex, today=TODAY)
    assert c["request"] == "overpayment_refund"
    assert "Optum Recovery" in c["best"]["name"]


# ================================================================================================ the payer and its plan
def test_an_unknown_payer_gets_the_plain_fallback(store):
    world(store, payer="ACME HEALTH PLAN", payer_group="Acme Health Plan", payer_kind="other")
    c, _ = card(store)
    assert c["fallback"] == routes.FALLBACK and "best" not in c


def test_a_plan_the_research_did_not_cover_says_so_and_falls_back(store):
    world(store, payer="ANTHEM KY MEDICAID", payer_group="Anthem / BCBS Medicaid", payer_kind="medicaid", cfi="MC")
    c, _ = card(store)
    assert c["fallback"].startswith("Anthem's Medicaid plans") and c["fallback"].endswith(routes.FALLBACK)


def test_the_va_network_is_found_by_its_payer_id_whatever_group_its_name_gave(store):
    world(store, payer="OPTUM UNITEDHEALTH CARE", payer_group="UnitedHealthcare", payer_kind="commercial", payer_id="VACCN")
    c, _ = card(store)
    assert c["group"] == "VA Community Care" and c["variant"] == "main"
    world(store, payer="TRIWEST VA CCN", payer_group="VA Community Care", payer_kind="va", payer_id="TWVACCN")
    c, _ = card(store)
    assert c["variant"] == "triwest"


def test_a_bluecard_member_takes_the_bluecard_rows_and_a_clinical_denial_the_clinical_appeal(store):
    tk = world(store, payer="BLUE CROSS TEST", payer_group="Anthem / BCBS", payer_kind="commercial")
    store.conn.execute("UPDATE claims SET contract_class=? WHERE claim_key=?", ("ITS HOST PPO", f"{tk}-0"))
    store.conn.commit()
    c, _ = card(store)
    assert c["variant"] == "bluecard" and "BlueCard" in c["variant_why"]
    store.conn.execute("UPDATE claims SET contract_class=? WHERE claim_key=?", ("KENTUCKY PPO", f"{tk}-0"))
    store.conn.commit()
    c, _ = card(store)
    assert c["variant"] == "clinical" and c["best"]["channel"] == "mail"


def test_cigna_medicare_advantage_is_healthspring(store):
    world(store, payer="CIGNA", payer_group="Cigna Medicare Advantage", payer_kind="medicare_advantage", cfi="16")
    c, _ = card(store)
    assert c["group"] == "HealthSpring" and "52192" in c["best"]["name"]


def test_tricare_picks_its_review_by_the_denial(store):
    world(store, payer="TRICARE EAST", payer_group="Tricare", payer_kind="tricare", cfi="CH", reason=("CO", "97"),
          signal="DENIED_OTHER")
    c, ex = card(store)
    if ex["next_step"]["route"] in ("appeal", "rate"):
        assert c["variant"] == "coding_edit"
    assert routes.pick(routes._rows("Tricare", "reconsideration_or_first_appeal"), {"payer_kind": "tricare"}, {},
                       "reconsideration_or_first_appeal", "appeal", {"50"})[0]["variant"] == "clinical"
    assert routes.pick(routes._rows("Tricare", "reconsideration_or_first_appeal"), {"payer_kind": "tricare"}, {},
                       "reconsideration_or_first_appeal", "appeal", {"45"})[0]["variant"] == "main"


# ================================================================================================ the fields
def _fields(c):
    return c["best"]["fields"] + c["best"]["facts"]


def test_the_fields_come_from_the_store_and_the_settings_never_invented(store, monkeypatch):
    monkeypatch.setenv("PRACTICE_NAME", "Invented Skin Clinic")
    monkeypatch.setenv("PRACTICE_ADDRESS", "1 Test Way, Testville, KY 40000")
    monkeypatch.setenv("PRACTICE_TIN", "12-0000000")
    monkeypatch.setenv("PRACTICE_PTAN", "PTAN-TEST")
    world(store, payer="KY MEDICARE PART B", payer_group="Medicare KY (CGS J15)", payer_kind="medicare", cfi="MB", reason=("CO", "4"),
          signal="DENIED_MISSING_MODIFIER", actionable="correct_and_resubmit", modifiers=json.dumps(["25"]),
          deadline_basis="a reopening of the clerical error within 1 year of the remittance")
    c, _ = card(store, names=True)
    vals = {x["label"]: x["value"] for x in _fields(c)}
    assert vals["Claim ICN (from the CGS remittance advice)"] == "ICN-TEST-1"
    assert vals["Medicare Beneficiary Identifier (MBI)"] == MEMBER and vals["Patient name"] == "TESTPATIENT, ALPHA"
    assert vals["Date of service"] == "03/02/2026" and vals["Code and modifiers"] == "99213-25"
    # the IVR (the second way) fills the practice's own numbers from the settings, and leaves the NPI blank with what to set
    ivr = next(o for o in c["others"] if o["channel"] == "phone")
    got = {x["label"]: x for x in ivr["fields"]}
    assert got["PTAN then #"]["value"] == "PTAN-TEST" and got["Tax ID last 5 digits"]["value"] == "00000"
    assert got["NPI then #"]["value"] == "" and "PRACTICE_NPI" in got["NPI then #"]["fill"]
    assert got["Patient last name, first 6 letters, then #"]["value"] == "TESTPA"
    mail = next(o for o in c["others"] if o["channel"] == "mail")
    m = {x["label"]: x for x in mail["fields"]}
    assert m["Provider Name"]["value"] == "Invented Skin Clinic" and m["Last 5 digits of Tax ID Number"]["value"] == "00000"
    # a value the store has not: shown empty with what to fill
    assert m["Contact"]["value"] == "" and m["Contact"]["fill"] == "your name"


def test_with_names_off_no_name_or_member_number_is_in_any_field_or_in_copy_all(store):
    world(store, payer="KY MEDICARE PART B", payer_group="Medicare KY (CGS J15)", payer_kind="medicare", cfi="MB")
    c, _ = card(store, names=False)
    text = every_text(c)
    assert NAME not in text and "ALPHA" not in text and MEMBER not in text and "TESTPA" not in text
    assert NAME not in c["copy_all"] and MEMBER not in c["copy_all"]
    assert not c["names_shown"]
    named = [x for v in [c["best"]] + c["others"] for x in v["fields"] + v["facts"] if x.get("names")]
    assert named and all(x["value"] == "" for x in named)
    # with names on, the same card says who it is about
    c2, _ = card(store, names=True)
    assert NAME in every_text(c2) and MEMBER in c2["copy_all"]


def test_copy_all_is_a_clean_block_of_the_filled_fields(store):
    world(store, payer="UNITEDHEALTHCARE", payer_group="UnitedHealthcare", payer_kind="commercial")
    c, _ = card(store)
    lines = c["copy_all"].split("\n")
    assert lines[0] == "UnitedHealthcare (commercial): Reconsideration or first-level appeal"
    assert lines[1].startswith("Route: UnitedHealthcare Provider Portal claim reconsideration")
    assert "Payer claim number: ICN-TEST-1" in lines and "Date of service: 03/02/2026" in lines
    assert all(": " in x and not x.endswith(": ") for x in lines[1:])        # no blank field in the block


# ================================================================================================ the fax rule
def test_a_fax_route_shows_the_published_number_and_waits_for_a_confirmed_one(store):
    world(store, payer="OPTUM UNITEDHEALTH CARE", payer_group="VA Community Care", payer_kind="va", payer_id="VACCN")
    c, ex = card(store)
    b = c["best"]
    assert b["channel"] == "fax" and b["fax"]["published"] == "877-666-6597"
    assert not b["fax"]["confirmed"] and "Confirm it before the first fax" in b["fax"]["words"]
    assert "fax_payer" not in ex["next_step"]["buttons"]
    # a person confirms the number on the Faxes page: the app's own send may use it, and the card says so
    actions.ensure_fax_tables(store.conn)
    store.conn.execute("INSERT INTO payer_fax (payer_group, purpose, number, confirmed_by, confirmed_at, auto_ok) VALUES (?,?,?,?,?,1)",
                       ("VA Community Care", "appeal", "8776666597", "Owner", "2026-04-01"))
    store.conn.commit()
    c, _ = card(store)
    assert c["best"]["fax"]["confirmed"] and c["best"]["fax"]["same"] and "confirmed number" in c["best"]["fax"]["words"]
    # a number changed since and not yet confirmed again: never used by itself
    store.conn.execute("UPDATE payer_fax SET number='8775550000', auto_ok=0 WHERE payer_group='VA Community Care'")
    store.conn.commit()
    c, _ = card(store)
    assert not c["best"]["fax"]["confirmed"] and "nobody has confirmed it" in c["best"]["fax"]["words"]


def test_a_number_on_file_that_differs_from_the_published_one_is_named_beside_it(store):
    world(store, payer="TRICARE EAST", payer_group="Tricare", payer_kind="tricare", cfi="CH")
    actions.ensure_fax_tables(store.conn)
    store.conn.execute("INSERT INTO payer_fax (payer_group, purpose, number, confirmed_by, confirmed_at, auto_ok) VALUES (?,?,?,?,?,1)",
                       ("Tricare", "appeal", "8005550199", "Owner", "2026-04-01"))
    store.conn.commit()
    c, _ = card(store)
    fax = next((v["fax"] for v in [c["best"]] + c["others"] if v.get("fax")), None)
    assert fax and fax["confirmed"] and not fax["same"] and "Check which is right" in fax["words"]


# ================================================================================================ the API on both hosts
def _site(store_path, tmp_path, monkeypatch, mode, names=False):
    from fastapi.testclient import TestClient

    import remitwatch.api as api
    from remitwatch.state import LocalState
    srv = tmp_path / f"server-{mode}"
    srv.mkdir()
    db = str(srv / "store.sqlite")
    src, dst = sqlite3.connect(store_path), sqlite3.connect(db)     # a copy with what the store's open connection wrote
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()
    for k in ("DROPBOX_APP_KEY", "DROPBOX_APP_SECRET", "DROPBOX_REFRESH_TOKEN", "REMITWATCH_STATE_DIR", "SRFAX_ACCESS_ID",
              "SRFAX_ACCESS_PWD", "ANTHROPIC_API_KEY", "MODMED_CLIENT_ID", "VERCEL", "VERCEL_ENV", "CRON_SECRET"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(api, "PASSWORD", "")
    monkeypatch.setattr(api, "SHOW_PHI", names)
    monkeypatch.setattr(api, "DB_PATH", db)
    monkeypatch.setattr(api, "_day", lambda off: (dt.date.fromisoformat(TODAY) + dt.timedelta(days=off)).isoformat())
    api.ensure_server_schema(db)
    if mode == "serverless":
        st = LocalState(str(tmp_path / f"state-{mode}"))
        conn = sqlite3.connect(db)
        try:
            cloud.save_serving(st, conn)
        finally:
            conn.close()
        monkeypatch.setenv("REMITWATCH_MODE", "serverless")
        monkeypatch.setenv("REMITWATCH_STATE_DIR", str(tmp_path / f"state-{mode}"))
        monkeypatch.setattr(cloud, "SERVING_COPY", cloud.ServingCopy())
    else:
        monkeypatch.setenv("REMITWATCH_MODE", "server")
    return TestClient(api.app)


@pytest.mark.parametrize("mode", ["server", "serverless"])
def test_the_item_api_carries_the_route_card_on_both_hosts(store, tmp_path, monkeypatch, mode):
    world(store, payer="KY MEDICARE PART B", payer_group="Medicare KY (CGS J15)", payer_kind="medicare", cfi="MB")
    store.conn.commit()
    c = _site(str(tmp_path / "store.sqlite"), tmp_path, monkeypatch, mode)
    r = c.get("/api/findings/F1/explain")
    assert r.status_code == 200, r.text
    rc = r.json()["route_card"]
    assert rc["request"] == "medicare_redetermination" and rc["group"] == "Medicare KY (CGS J15)"
    vals = {x["label"]: x["value"] for x in rc["best"]["fields"] + rc["best"]["facts"]}
    # the claim's numbers reach the dashboard's copy too (the serving copy holds the item's claim and lines)
    assert vals.get("Payer claim number") == "ICN-TEST-1" or "ICN-TEST-1" in json.dumps(rc)
    assert vals.get("Date of service") == "03/02/2026"
    assert NAME not in json.dumps(rc) and MEMBER not in json.dumps(rc)
    # the panel's one answer (story=1) carries it too
    r2 = c.get("/api/findings/F1", params={"story": 1})
    assert r2.status_code == 200 and r2.json()["route_card"]["request"] == "medicare_redetermination"


@pytest.mark.parametrize("mode", ["server", "serverless"])
def test_the_route_card_and_its_names_follow_the_sites_names_switch_on_both_hosts(store, tmp_path, monkeypatch, mode):
    world(store, payer="UNITEDHEALTHCARE", payer_group="UnitedHealthcare", payer_kind="commercial")
    store.conn.commit()
    c = _site(str(tmp_path / "store.sqlite"), tmp_path, monkeypatch, mode, names=True)
    rc = c.get("/api/findings/F1/explain").json()["route_card"]
    assert rc["names_shown"] and MEMBER in rc["copy_all"]


def test_a_card_that_cannot_be_put_together_leaves_the_panel_and_says_why(store, tmp_path, monkeypatch):
    world(store, payer="UNITEDHEALTHCARE", payer_group="UnitedHealthcare", payer_kind="commercial")
    store.conn.commit()
    c = _site(str(tmp_path / "store.sqlite"), tmp_path, monkeypatch, "server")

    def boom(*a, **k):
        raise RuntimeError("invented failure")
    monkeypatch.setattr(routes, "card", boom)
    out = c.get("/api/findings/F1/explain").json()
    assert out["route_card"] is None and "could not be worked out (RuntimeError)" in out["route_card_error"]
    assert out["explain"]["next_step"]["route"] == "appeal"
