"""Every payer window from its primary source (unit deadlines, 2026-11-26; plans/review_2026-11-26.md, section "Deadlines",
and the review's answers-8).

The A17 research read the filing, corrected-claim and appeal windows of the practice's payer groups in the CFR, the CMS
manuals, the Kentucky and Indiana statutes and regulations and the payers' own 2026 manuals, and found 28 rows where
remitwatch/codes.py disagreed. Each test below pins one window to its source's words: a window the product had too long
gets the earlier date (a biller working by it would miss the payer's limit), one it had too short keeps a safe date no
later than the true one and gets the true last day (the item is not called expired while it can still be worked). Plans
inside one payer group are told apart by what the remittance carries (the plan, the payer's name, the filing indicator);
an item that does not say which plan it is gets the shorter window, and its label says so.

Every claim, member number, name, amount and date is invented (tests/test_features.claim and svc, tests/test_remitwatch.remit,
tests/test_837_sends, tests/test_workable.ema). Detection runs on the server's store file and on the compact image the
serverless cycle analyses.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch import codes  # noqa: E402
from remitwatch.codes import appeal_rule, corrected_rule, filing_rule, legal_deadline, window_end  # noqa: E402
from tests.test_features import claim, clean_visits, svc  # noqa: E402
from tests.test_remitwatch import remit  # noqa: E402

KINDS = ["file", "compact"]


def dates(start: str, rule: dict) -> tuple:
    """(the safe date, the payer's own last day) of a window opening on `start`."""
    return window_end(start, rule), legal_deadline(start, rule)


# =========================================================================================== 1. Medicare's first filing
def test_medicare_first_filing_moves_a_last_day_on_a_weekend_or_federal_holiday_to_the_next_workday():
    """42 CFR 424.44(a)(1): 'the claim must be filed no later than the close of the period ending 1 calendar year after the
    date of service'; 424.44(c): 'If the last day of the period ... falls on a Federal nonworkday (a Saturday, Sunday, legal
    holiday ...), the time is extended to the next succeeding workday.' The safe date stays the calendar year; the payer's
    own last day moves (before: the same calendar day, so a claim was called late 1 to 3 days early)."""
    for grp in ("Medicare KY (CGS J15)", "Medicare IN (WPS J8)", "Railroad Medicare"):
        r = filing_rule(grp, "medicare")
        assert r["verified"] and "424.44(c)" in r["label"]
        assert dates("2025-11-29", r) == ("2026-11-29", "2026-11-30"), grp      # a Sunday
        assert dates("2025-12-25", r) == ("2026-12-25", "2026-12-28"), grp      # Christmas, a Friday
        assert dates("2025-07-03", r) == ("2026-07-03", "2026-07-06"), grp      # July 4 is a Saturday: observed on Friday the 3rd
        assert dates("2026-12-31", r) == ("2027-12-31", "2028-01-03"), grp      # New Year's Day 2028 (a Saturday) observed December 31
        assert dates("2025-06-10", r) == ("2026-06-10", "2026-06-10"), grp      # a Wednesday: no change
        assert dates("2028-02-29", r) == ("2029-02-28", "2029-02-28"), grp      # a service on February 29: February 28


def test_federal_holidays_and_their_observed_days():
    """5 U.S.C. 6103(a), and the workday observed in place of a holiday on a weekend (Executive Order 11582)."""
    from datetime import date
    h = codes.federal_holidays(2026)
    for d in ("2026-01-01", "2026-01-19", "2026-02-16", "2026-05-25", "2026-06-19", "2026-07-03", "2026-07-04", "2026-09-07",
              "2026-10-12", "2026-11-11", "2026-11-26", "2026-12-25"):
        assert date.fromisoformat(d) in h, d
    assert date(2026, 7, 2) not in h and date(2026, 11, 27) not in h
    assert date(2027, 12, 24) in codes.federal_holidays(2027) and date(2027, 12, 31) in codes.federal_holidays(2027)
    assert codes.next_workday(date(2026, 11, 26)) == date(2026, 11, 27)              # Thanksgiving: the Friday after works
    assert codes.next_workday(date(2026, 11, 27)) == date(2026, 11, 27)


# =========================================================================================== 2-3. Medicare Advantage
def test_a_contracted_medicare_advantage_window_names_the_non_contracted_one():
    """42 CFR 422.582(b): a provider not contracted with the plan files a reconsideration 'within 60 calendar days after
    receipt' of the notice, presumed 5 days after its date; Anthem's guidebook 7.2: a nonparticipating provider 'within 65
    calendar days from the remittance notification'. The practice bills these plans as a contracted provider (their volume
    and contracted payments say so): the dates stay the contracted ones, the labels name the other."""
    hum = appeal_rule("Humana Medicare Advantage", "medicare_advantage")
    assert dates("2026-06-01", hum) == ("2027-12-01", "2027-12-01") and "422.582" in hum["label"] and hum["verified"]
    uhc = appeal_rule("UnitedHealthcare Medicare Advantage", "medicare_advantage")
    assert dates("2026-06-01", uhc) == ("2027-06-01", "2027-06-01") and "422.582" in uhc["label"]
    ant = appeal_rule("Anthem / BCBS Medicare Advantage", "medicare_advantage")
    assert window_end("2026-06-01", ant) == "2026-09-29" and "65 calendar days" in ant["label"]
    assert "422.582" not in appeal_rule("Humana", "commercial")["label"]           # the commercial plan has no such rule


# =========================================================================================== 4. UnitedHealthcare corrected claims
def test_unitedhealthcare_corrected_claim_runs_from_the_date_of_service_inside_the_agreement_limit():
    """2026 UHC Administrative Guide, ch. 10: the agreement's filing limit counted from the date of service 'also applies
    to submission of corrected claims' (its example: 90 days). Before: no rule, so a corrected claim was dated by the
    12-month reconsideration window from the remittance."""
    for grp, kind in (("UnitedHealthcare", "commercial"), ("UnitedHealthcare Medicare Advantage", "medicare_advantage"),
                      ("UHC Medicare Advantage", "medicare_advantage")):
        r = corrected_rule(grp, kind)
        assert r is not None and r["clock"] == "dos" and window_end("2026-05-20", r) == "2026-08-18", grp
        assert "also applies to submission of corrected claims" in r["label"]
    assert corrected_rule("UHC Integrated Services (Wausau)", "commercial") is None   # its own plan rules are not read


# =========================================================================================== 5. WellCare Medicare Advantage
def test_wellcare_medicare_advantage_first_filing_is_180_days():
    """Wellcare Kentucky Medicare Quick Reference Guide (January 2026): 'Timely Filing guidelines: 180 days from date of
    service.' Before: the Medicare Advantage default of 12 months."""
    r = filing_rule("WellCare / Ambetter (Centene) Medicare Advantage", "medicare_advantage")
    assert r["verified"] and window_end("2026-05-20", r) == "2026-11-16" and legal_deadline("2026-05-20", r) is None


# =========================================================================================== 6, 24. federal employee plans
def test_federal_employee_plans_take_a_claim_until_december_31_of_the_next_year():
    """FEP brochure RI 71-005 and GEHA brochure RI 71-006, Section 7: the claim by 'December 31 of the following year' (of
    the year after the year) of the service. Before: FEP got the commercial 90 days (filing_rule took no plan), GEHA the
    unknown-payer 12 months."""
    fep = filing_rule("Anthem / BCBS", "commercial", plan="FEDERAL EMPLOYEE PROGRAM")
    assert fep["verified"] and dates("2026-01-05", fep) == ("2027-12-31", None) and dates("2026-12-31", fep) == ("2027-12-31", None)
    assert window_end("2026-01-05", filing_rule("Anthem / BCBS", "commercial", plan="PPO")) == "2026-04-05"   # not the FEP: 90 days
    for grp in ("Geha", "Government Employees Health Association", "Government Employees Hospital Association (Geha)"):
        g = filing_rule(grp, "other")
        assert g["verified"] and g["earliest"] is None and window_end("2025-02-11", g) == "2026-12-31", grp
    # a returned FEP claim: 'within 90 days, or before the timely filing period expires, whichever is later'
    c = corrected_rule("Anthem / BCBS", "commercial", plan="FEDERAL EMPLOYEE PROGRAM")
    assert c["clock"] == "dos" and window_end("2026-03-10", c) == "2027-12-31"
    # the FEP reconsideration: 180 days as the safe date, 6 months the member's limit (5 CFR 890.105(b)(1))
    assert dates("2026-03-01", appeal_rule("Anthem / BCBS", "commercial", "FEDERAL EMPLOYEE PROGRAM")) == ("2026-08-28", "2026-09-01")


def test_geha_reconsideration_has_six_months():
    """GEHA brochure Section 8, step 1: 'Write to us within 6 months from the date of our decision' (5 CFR 890.105(b)(1)).
    Before: the unknown-payer 180 days, with no last day of the payer's own."""
    for grp in ("Geha", "Government Employees Health Association", "Government Employees Hospital Association (Geha)"):
        r = appeal_rule(grp, "other")
        assert r["verified"] and dates("2026-06-01", r) == ("2026-11-28", "2026-12-01"), grp


# =========================================================================================== 7. Aetna
def test_aetna_uses_180_days_and_names_the_fully_insured_two_years():
    """Aetna's overview: 'File your reconsideration within 180 calendar days of the initial claim decision' (read: now
    confirmed). Its state exceptions give fully insured plans 2 years in Kentucky (participating) and Indiana; the 835 does
    not say whether a plan is fully insured, so the shorter window stays and the label says so."""
    r = appeal_rule("Aetna", "commercial")
    assert r["verified"] and dates("2026-06-01", r) == ("2026-11-28", None)
    assert "2 years" in r["label"] and "fully insured" in r["label"]


# =========================================================================================== 8. Ambetter
def test_ambetter_first_filing_keeps_90_days_as_the_safe_date_and_180_as_the_participating_limit():
    """Ambetter's 2026 Kentucky and Indiana manuals, Timely Filing: initial claims Par 180 calendar days, Non-Par 90, from
    the date of service. Before: the commercial 90 days only, so a participating provider's claim expired early."""
    r = filing_rule("Ambetter (Centene)", "commercial")
    assert r["verified"] and dates("2026-05-20", r) == ("2026-08-18", "2026-11-16")


# =========================================================================================== 9-11. Anthem Medicaid
def test_anthem_medicaid_plans_are_told_apart_by_the_plan_name():
    """The 'Anthem / BCBS Medicaid' group holds Kentucky and Indiana plans; the 835's contract class names them
    ('KENTUCKY SAMPLE MEDICAID', 'INDIANA SAMPLE PLAN', 'IN SAMPLE PLAN LTSS'). Indiana (IHCP Works 2026, Anthem): claims in
    90 calendar days from the service, a dispute within 60 calendar days of the EOB. Kentucky (Anthem's manual and policy
    G-16001): claims and corrected claims within 365 days of the service, a payment appeal within 90 days, and since April
    14, 2026 at least 120 days from receipt (KRS 205.534(1)(d)1). A plan that names neither gets the shorter window."""
    g, k = "Anthem / BCBS Medicaid", "medicaid"
    for plan, state in (("KENTUCKY SAMPLE MEDICAID", "KY"), ("INDIANA SAMPLE PLAN", "IN"), ("IN SAMPLE PLAN LTSS", "IN"),
                        ("VIRGINIA SAMPLE MEDICAID", None), (None, None)):
        assert codes.plan_state(plan, "ANTHEM") == state, plan
    ky, ind, unk = {"plan": "KENTUCKY SAMPLE MEDICAID"}, {"plan": "INDIANA SAMPLE PLAN"}, {"plan": None}
    assert window_end("2026-05-20", filing_rule(g, k, **ky)) == "2027-05-20"
    assert window_end("2026-05-20", filing_rule(g, k, **ind)) == "2026-08-18"
    u = filing_rule(g, k, **unk)
    assert window_end("2026-05-20", u) == "2026-08-18" and "does not say" in u["label"]
    c = corrected_rule(g, k, **ky)
    assert c["clock"] == "dos" and window_end("2026-05-20", c) == "2027-05-20"
    assert corrected_rule(g, k, **ind) is None and corrected_rule(g, k, **unk) is None
    assert dates("2026-06-01", appeal_rule(g, k, "KENTUCKY SAMPLE MEDICAID")) == ("2026-08-30", "2026-10-04")
    assert dates("2026-03-02", appeal_rule(g, k, "KENTUCKY SAMPLE MEDICAID")) == ("2026-05-31", None)    # a denial before April 14
    assert dates("2026-06-01", appeal_rule(g, k, "INDIANA SAMPLE PLAN")) == ("2026-07-31", None)
    a = appeal_rule(g, k)
    assert dates("2026-06-01", a) == ("2026-07-31", None) and "does not say" in a["label"]
    # the payer's name says the state where the plan does not (a facts dict from an item works the same way)
    assert window_end("2026-06-01", appeal_rule("Aetna Medicaid", k, **codes.rule_facts({"payer_name": "AETNA SAMPLE HEALTH OF KENTUCKY"}))) \
        == "2026-09-29"


# =========================================================================================== 11. Kentucky's statute by date
def test_kentucky_medicaid_managed_care_120_days_from_receipt_apply_by_the_date_of_the_denial():
    """KRS 205.534(1)(d)1 (effective April 14, 2026): 'within one hundred twenty (120) days of confirmed receipt of a
    notification ... that payment for a submitted claim has been reduced or denied'. Counted from the remittance plus the 5
    days of presumed receipt the product uses for Medicare (125 days), for a denial dated on or after April 14, 2026."""
    r = appeal_rule("Aetna Medicaid", "medicaid", payer_name="AETNA SAMPLE HEALTH OF KENTUCKY")
    assert r["verified"] and r["since"] == "2026-04-14" and "KRS 205.534(1)(d)1" in r["label"]
    assert dates("2026-04-14", r) == ("2026-08-12", "2026-08-17")
    assert dates("2026-04-13", r) == ("2026-07-12", None)                       # the day before: the window before the law
    w = appeal_rule("WellCare / Ambetter (Centene) Medicaid", "medicaid", payer_name="WELLCARE SAMPLE PLAN OF KENTUCKY", carc="50")
    assert dates("2026-06-01", w) == ("2026-07-31", "2026-10-04")


# =========================================================================================== 12-14. WellCare Kentucky Medicaid
def test_wellcare_kentucky_medicaid_windows():
    """WellCare of Kentucky's 2025-2026 Medicaid manual: claims, corrected and voided ones included, 'within 12 months'
    of the service; 'Claim payment appeals must be submitted ... within 24 months of the date of denial of the EOP' for
    untimely filing, incidental procedures, bundling, unlisted and non-covered codes; an appeal of a utilization-management
    denial within 60 days (at least 120 from receipt by the statute since April 14, 2026). Before: the Medicaid defaults
    (365 days to file, a 90-day appeal for every denial)."""
    g, k, who = "WellCare / Ambetter (Centene) Medicaid", "medicaid", {"payer_name": "WELLCARE SAMPLE PLAN OF KENTUCKY"}
    f = filing_rule(g, k, **who)
    assert f["verified"] and window_end("2024-02-29", f) == "2025-02-28" and window_end("2027-03-01", f) == "2028-03-01"
    c = corrected_rule(g, k, **who)
    assert c["clock"] == "dos" and window_end("2026-05-20", c) == "2027-05-20"
    assert dates("2026-06-01", appeal_rule(g, k, carc="97", **who)) == ("2028-06-01", "2028-06-01")   # bundling: 24 months
    assert dates("2026-06-01", appeal_rule(g, k, carc="197", **who)) == ("2026-07-31", "2026-10-04")  # an authorization
    unknown = appeal_rule(g, k, **who)                                            # no reason known: the shorter
    assert dates("2026-06-01", unknown) == ("2026-07-31", "2026-10-04") and "24 months" in unknown["label"]


# =========================================================================================== 15-17. Kentucky Medicaid fee-for-service
def test_kentucky_medicaid_fee_for_service_takes_no_appeal_of_a_claim_denial():
    """907 KAR 1:671 Section 9(4) lists what the administrative appeal is for (a terminated provider, discrimination, a
    sanction, an overpayment to repay, payments withheld): an ordinary claim denial is not one. Its dispute resolution is
    'within thirty (30) calendar days of the date the notice was received' (Section 8(1)). A denied claim is fixed by a
    corrected or new claim within 12 months of the service (907 KAR 1:673 Section 2(1)(b)); a claim billed to Medicare
    first also 'Within six (6) months of the Medicare adjudication date' (907 KAR 3:005). Before: a 90-day appeal."""
    a = appeal_rule("Medicaid KY", "medicaid")
    assert a["no_appeal"] and a["verified"] and "907 KAR 1:671" in a["label"] and dates("2026-06-01", a) == ("2026-07-01", "2026-07-06")
    c = corrected_rule("Medicaid KY", "medicaid")
    assert c["clock"] == "dos" and window_end("2024-02-29", c) == "2025-02-28" and "907 KAR 1:673" in c["label"]
    f = filing_rule("Medicaid KY", "medicaid")
    assert f["verified"] and window_end("2026-05-20", f) == "2027-05-20" and "907 KAR 3:005" in f["label"]
    assert not appeal_rule("Medicaid IN", "medicaid")["no_appeal"] and not appeal_rule("Anthem / BCBS", "commercial")["no_appeal"]


# =========================================================================================== 18-20, answers-8. Indiana Medicaid
def test_indiana_medicaid_first_filing_limit_is_180_days_from_the_date_of_service():
    """The review's answers-8: the IHCP's limit for a fee-for-service claim: 'must be originally filed within 180 days of
    the date of service' (Claim Administrative Review and Appeals module, 2026). Before: the Medicaid default, 365 days."""
    grp, kind = codes.normalize_payer("INDIANA MEDICAID")
    assert (grp, kind) == ("Medicaid IN", "medicaid")
    r = filing_rule(grp, kind)
    assert r["verified"] and r.get("earliest") is None
    assert window_end("2026-01-10", r) == "2026-07-09" and window_end("2026-09-14", r) == "2027-03-13"
    assert "crossover" in r["label"]


def test_indiana_medicaid_corrected_claim_and_administrative_review():
    """IHCP: a denied claim 'resubmitted with corrected information is considered to be an initial claim' (180 days from
    the service); a paid claim's adjustment or replacement 'within 60 days of notification of the claim's disposition' (the
    RA date); an administrative review 'within 60 calendar days of notification of claim payment or denial'. Before: a 90-day
    appeal and no corrected-claim rule."""
    a = appeal_rule("Medicaid IN", "medicaid")
    assert a["verified"] and dates("2026-06-01", a) == ("2026-07-31", None)
    denied = corrected_rule("Medicaid IN", "medicaid", paid=False)
    assert denied["clock"] == "dos" and window_end("2026-05-20", denied) == "2026-11-16"
    paid = corrected_rule("Medicaid IN", "medicaid", paid=True)
    assert paid["clock"] == "remit" and window_end("2026-06-01", paid) == "2026-07-31"
    assert corrected_rule("Medicaid IN", "medicaid")["clock"] == "remit"            # not known whether paid: the shorter


# =========================================================================================== 21. TRICARE
def test_tricare_returned_claim_goes_back_within_a_year_of_the_service():
    """32 CFR 199.7(d)(1): a claim returned for more information goes back by 'the later of' one year after the services
    or 90 days from the return. Before: no rule (the 90-day appeal window from the remittance)."""
    r = corrected_rule("Tricare", "tricare")
    assert r["verified"] and r["clock"] == "dos" and window_end("2026-05-20", r) == "2027-05-20" and "199.7(d)(1)" in r["label"]


# =========================================================================================== 22-23. CHAMPVA
def test_champva_windows_cover_every_name_it_comes_under():
    """38 CFR 17.276(a)(1): claims 'One year after the date of service'; 17.277(b): a reconsideration 'within one year of the
    date of the initial determination'. The old pattern ^champva missed 'Champ Va - Hac'; the appeal had no rule (180 days)."""
    for grp in ("Champva", "Champ Va - Hac", "Champva-Hac Medicare Crossover"):
        f = filing_rule(grp, "other")
        assert f["verified"] and f["earliest"] is None and window_end("2026-05-20", f) == "2027-05-20", grp
        a = appeal_rule(grp, "other")
        assert a["verified"] and dates("2026-06-01", a) == ("2027-06-01", "2027-06-01"), grp


# =========================================================================================== 26-27. Oxford and other Medicare-indicator payers
def test_oxford_is_not_medicare():
    """Oxford's claims carry filing indicator MB, so payer_group_kind calls its group Medicare. The 2026 UHC Administrative
    Guide's Oxford supplement: a reconsideration and appeal 'within 12 months ... from the date of the original EOB or PRA';
    its filing limit is the agreement's. Before: Medicare's 125 days and 12 months, both marked confirmed."""
    grp, kind = codes.payer_group_kind("OXFORD HEALTH PLAN", "MB")
    assert kind == "medicare"
    a = appeal_rule(grp, kind)
    assert a["verified"] and dates("2026-06-01", a) == ("2027-06-01", "2027-06-01") and "Oxford" in a["label"]
    f = filing_rule(grp, kind)
    assert not f["verified"] and f["earliest"] == 90 and "Participation Agreement" in f["label"]
    c = corrected_rule(grp, kind)
    assert c["clock"] == "remit" and window_end("2026-06-01", c) == "2027-06-01"


def test_a_payer_the_rules_do_not_know_is_not_called_medicare_for_its_filing_indicator():
    """A payer the name rules do not know whose claims carry MA or MB got Medicare's windows marked confirmed. Its own are
    not known: Medicare's redetermination window stays as the shorter one, unconfirmed and saying why, and its first filing
    is the unknown payer's (the longest common limit, with the shortest named)."""
    grp, kind = codes.payer_group_kind("SAMPLE LIFE INSURANCE COMPANY", "MB")
    assert kind == "medicare"
    a = appeal_rule(grp, kind)
    assert not a["verified"] and dates("2026-06-01", a) == ("2026-10-04", "2026-10-04") and "not Medicare itself" in a["label"]
    f = filing_rule(grp, kind)
    assert not f["verified"] and f["earliest"] == 90 and window_end("2026-05-20", f) == "2027-05-20"
    assert appeal_rule("Medicare KY (CGS J15)", "medicare")["verified"] and filing_rule("Medicare (other)", "medicare")["verified"]


# =========================================================================================== 28. workers' compensation
def test_workers_compensation_bills_are_due_within_45_days_of_the_treatment():
    """803 KAR 25:096 (KRS 342.020): bills 'following treatment within forty-five (45) days'. Before: the unknown-payer
    12 months."""
    for r in (filing_rule("Gallagher Bassett", "other"), filing_rule("Sample Claims Services", "other", cfi="WC")):
        assert r["verified"] and window_end("2026-03-10", r) == "2026-04-24" and "342.020" in r["label"]
    assert window_end("2026-03-10", filing_rule("Sample Claims Services", "other")) == "2027-03-10"     # no WC indicator: unknown


# =========================================================================================== the facts
def test_rule_facts_reads_an_item_a_line_and_a_claim():
    item = {"plan": "INDIANA SAMPLE PLAN", "payer_name": "ANTHEM", "payer_id": "ANTHM", "carc": "50", "paid_cents": 0}
    assert codes.rule_facts(item) == {"plan": "INDIANA SAMPLE PLAN", "payer_name": "ANTHEM", "payer_id": "ANTHM", "cfi": None, "carc": "50",
                                "paid": False}
    line = {"contract_class": " KENTUCKY SAMPLE MEDICAID ", "claim_filing_indicator": "MC", "paid_cents": 1500}
    assert codes.rule_facts(line)["plan"] == "KENTUCKY SAMPLE MEDICAID" and codes.rule_facts(line)["cfi"] == "MC" and codes.rule_facts(line)["paid"] is True
    assert codes.rule_facts(None) == {"plan": None, "payer_name": None, "payer_id": None, "cfi": None, "carc": None, "paid": None}
    assert codes.rule_facts(item, carc="97")["carc"] == "97" and "ignored" not in codes.rule_facts(item, ignored=1)


def test_an_unknown_plan_fact_is_refused():
    with pytest.raises(TypeError):
        appeal_rule("Anthem / BCBS", "commercial", plan_name="FEDERAL EMPLOYEE PROGRAM")
    with pytest.raises(TypeError):
        filing_rule("Anthem / BCBS", "commercial", contract_class="FEDERAL EMPLOYEE PROGRAM")


# =========================================================================================== detection, both store kinds
def _run(tmp_path, compact: bool, claims: list[str], pay: str, payer: str, payer_id: str, today: str = "2026-07-15") -> dict:
    """Detection over one payer's remittances; {signal: the finding} (one item per signal in these tests)."""
    from remitwatch.detect import Detector, ensure_schema
    from remitwatch.store import Store
    store = Store(":memory:", compact=True) if compact else Store(str(tmp_path / f"{payer_id}.sqlite"))
    paths = []
    for n, i in enumerate(range(0, len(claims), 20)):
        p = tmp_path / f"{payer_id}{int(compact)}{n:03d}.RMT"
        p.write_bytes(remit("".join(claims[i:i + 20]), "100.00", payer=payer, payer_id=payer_id, seg_count=99,
                            isa_ctrl=f"{n + 1:09d}", pay_date=pay))
        paths.append(str(p))
    store.ingest_835_paths(paths)
    ensure_schema(store.conn)
    Detector(store, run_id="t", today=today).run()
    out = {r["signal"]: dict(r) for r in store.query("SELECT * FROM findings WHERE COALESCE(stale, 0) = 0")}
    out["_store"] = store
    return out


def _book(cfi: str, plan: str, dos: str, lines: list[str]) -> list[str]:
    """25 visits at the payer's usual rate, then one claim per extra line."""
    return clean_visits(1, 25, "99214", "223.65", "117.60", cfi=cfi, plan=plan, dos=dos) + [
        claim(300 + n, ln, cfi=cfi, plan=plan, dos=dos) for n, ln in enumerate(lines)]


NECESSITY = svc("11102", "150.00", "0.00", None, "CAS*CO*50*150.00~", dos="20260520")
MODIFIER = svc("99213", "150.00", "0.00", None, "CAS*CO*16*150.00~", dos="20260520") + "LQ*HE*N822~"


@pytest.mark.parametrize("compact", [False, True], ids=["store_file", "compact_image"])
def test_detection_dates_each_plan_by_its_own_window(tmp_path, compact):
    """A medical-necessity denial (CO-50) of a May 20, 2026 visit on a June 1 remittance, and a missing modifier (CO-16,
    N822) to correct, at each payer: the item's safe date and the payer's own last day as the sources say."""
    def item(payer, pid, cfi, plan="PPO", sig="DENIED_MEDICAL_NECESSITY", line=NECESSITY):
        got = _run(tmp_path, compact, _book(cfi, plan, "20260520", [line]), "20260601", payer, pid)
        got.pop("_store").close()
        return got[sig]
    f = item("IN MEDICAID", "IHCP1", "MC")
    assert f["payer_group"] == "Medicaid IN" and (f["appeal_deadline"], f["legal_deadline"]) == ("2026-07-31", None)
    f = item("ANTHEM", "ANTHM", "MC", "INDIANA SAMPLE PLAN")
    assert f["payer_group"] == "Anthem / BCBS Medicaid" and (f["appeal_deadline"], f["legal_deadline"]) == ("2026-07-31", None)
    f = item("ANTHEM", "ANTHM", "MC", "KENTUCKY SAMPLE MEDICAID")
    assert (f["appeal_deadline"], f["legal_deadline"]) == ("2026-08-30", "2026-10-04") and "KRS 205.534" in f["deadline_basis"]
    f = item("ANTHEM", "ANTHM", "12", "FEDERAL EMPLOYEE PROGRAM")
    assert (f["appeal_deadline"], f["legal_deadline"]) == ("2026-11-28", "2026-12-01")
    f = item("CHAMP VA - HAC", "84146", "12")
    assert (f["appeal_deadline"], f["legal_deadline"]) == ("2027-06-01", None)
    f = item("GEHA", "39026", "12")
    assert (f["appeal_deadline"], f["legal_deadline"]) == ("2026-11-28", "2026-12-01")
    f = item("OXFORD HEALTH PLAN", "06111", "MB")
    assert f["payer_kind"] == "medicare" and (f["appeal_deadline"], f["legal_deadline"]) == ("2027-06-01", None)
    f = item("UNITEDHEALTHCARE", "87726", "12", sig="DENIED_MISSING_MODIFIER", line=MODIFIER)
    assert (f["appeal_deadline"], f["legal_deadline"]) == ("2026-08-18", "2027-06-01")       # DOS + 90 days; the 12-month dispute after
    f = item("TRICARE FOR LIFE", "TDDIR", "CH", sig="DENIED_MISSING_MODIFIER", line=MODIFIER)
    assert (f["appeal_deadline"], f["legal_deadline"]) == ("2027-05-20", None)                # a year from the service


@pytest.mark.parametrize("compact", [False, True], ids=["store_file", "compact_image"])
def test_a_kentucky_medicaid_denial_is_worked_by_a_new_claim_within_12_months(tmp_path, compact):
    """A Kentucky Medicaid (fee-for-service) medical-necessity denial: no appeal exists for it (907 KAR 1:671), so the item
    is dated by the new or corrected claim's 12 months from the service (907 KAR 1:673), its next step is the corrected
    claim, and its words say why. Before: a 90-day appeal from the remittance, expired by November while the claim could
    still be sent."""
    from remitwatch import story
    got = _run(tmp_path, compact, _book("MC", "", "20260520", [NECESSITY]), "20260601", "KY MEDICAID", "00661", today="2026-11-20")
    store = got.pop("_store")
    f = got["DENIED_MEDICAL_NECESSITY"]
    assert f["payer_group"] == "Medicaid KY" and f["appeal_deadline"] == "2027-05-20"
    assert "no appeal" in f["deadline_basis"] and "907 KAR 1:673" in f["deadline_basis"]
    assert f["work_state"] == "work_now", f["state_basis"]
    nxt = story.next_step(store.conn, f["finding_id"], today="2026-11-20")
    assert nxt["route"] == "corrected", nxt
    assert "no appeal" in nxt["instructions"].lower() or "takes no appeal" in " ".join(nxt.get("steps") or []), nxt
    store.close()


@pytest.mark.parametrize("kind", KINDS)
def test_a_claim_rejected_before_reaching_indiana_medicaid_is_dated_by_its_180_day_limit(tmp_path, kind):
    """The review's answers-8, second test: EMA's weekly exports list claim CB0000IN1C017 to Indiana Medicaid (visit
    September 14), rejected before it reached the payer. The item (REJECTED_PRE_PAYER, Fix and resubmit) is dated by the
    first-filing limit: 180 days from the service, 2027-03-13 (before: 365 days, 2027-09-14)."""
    from tests.test_837_sends import _chased, _remits, _store, _visits
    from tests.test_workable import ema
    s = _store(tmp_path, kind)
    _remits(s, tmp_path, _visits(), "20261110")
    ema(s.conn, "CB0000IN1C017", payer="Indiana Medicaid", payer_id="IHCP1")
    ema(s.conn, "CB0000IN1C017", family="claim_status", claim_status="Rejected", payer="Indiana Medicaid", payer_id="IHCP1")
    s.conn.commit()
    f = _chased(s, "t1")["CB0000IN1C017"]
    assert f["signal"] == "REJECTED_PRE_PAYER" and f["payer_group"] == "Medicaid IN"
    assert f["appeal_deadline"] == "2027-03-13" and "180 days" in f["deadline_basis"], f["deadline_basis"]
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_a_claim_medicare_never_answered_keeps_its_last_day_to_the_next_workday(tmp_path, kind):
    """A claim EMA shows rejected before it reached Medicare (visit November 29, 2025): it must reach Medicare within the
    calendar year, by November 29, 2026, a Sunday, so the regulation's last day is Monday November 30 (424.44(c)). The
    safe date stays the 29th; the payer's own last day is shown beside it."""
    from tests.test_837_sends import _chased, _remits, _store, _visits
    from tests.test_workable import ema
    s = _store(tmp_path, kind)
    _remits(s, tmp_path, _visits(), "20261110")
    ema(s.conn, "CB0000MC1C017", payer="Medicare of Kentucky", payer_id="15102", service_date="2025-11-29", bill_created="2025-11-30")
    ema(s.conn, "CB0000MC1C017", family="claim_status", claim_status="Rejected", payer="Medicare of Kentucky", payer_id="15102",
        service_date="2025-11-29")
    s.conn.commit()
    f = _chased(s, "t1", today="2026-11-20")["CB0000MC1C017"]
    assert f["payer_group"] == "Medicare KY (CGS J15)", f["payer_group"]
    assert (f["appeal_deadline"], f["legal_deadline"]) == ("2026-11-29", "2026-11-30")
    s.close()


def test_a_federal_employee_program_item_expires_after_its_own_window_not_anthems_two_years():
    """states.windows adds the payer's dispute window to every denial; for an FEP item it was Anthem commercial's two years
    (the plan was not passed), so the item stayed workable 18 months past the FEP's 6 months."""
    from remitwatch import states
    f = {"finding_id": "F1", "signal": "DENIED_MEDICAL_NECESSITY", "category": "denial", "actionable": "appeal",
         "payer_group": "Anthem / BCBS", "payer_kind": "commercial", "plan": "FEDERAL EMPLOYEE PROGRAM", "payment_date": "2026-03-01",
         "service_date": "2026-02-10", "appeal_deadline": "2026-08-28", "legal_deadline": "2026-09-01", "carc": "50"}
    last = max(w["date"] for w in states.windows(f))
    assert last == "2026-09-01", states.windows(f)
    g = dict(f, plan="PPO", appeal_deadline="2028-03-01", legal_deadline=None)
    assert max(w["date"] for w in states.windows(g)) == "2028-03-01"


# ==================================================================== a second payer never billed: its last day decides
@pytest.fixture
def ema_env(monkeypatch):
    """The settings the EMA wiring tests run under (tests/test_ema_wiring.py's _env)."""
    from remitwatch import ema_checks as ec, ema_reports as er
    from tests import ema_fixtures as F
    monkeypatch.setattr(er, "CLAIM_HOST_SUFFIX", F.HOST_SUFFIX)
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    monkeypatch.setenv("REMITWATCH_EMA_STRICT", "1")
    for k in ("VERCEL", "REMITWATCH_EMA_REPORTS", "REMITWATCH_EMA_CLAIM_URL", "REMITWATCH_EMA_PAYER_ALIASES",
              "REMITWATCH_EMA_LEDGER", "REMITWATCH_EMA_REPORT_KEEP", "REMITWATCH_EMA_CLOSES"):
        monkeypatch.delenv(k, raising=False)
    ec._LAST_POST.clear()


def _second_payer_run(tmp_path, compact: bool, paid_on: str, today: str) -> tuple[list[dict], dict]:
    """Blue Cross paid a December 15, 2025 visit on `paid_on` and left $30 of coinsurance; EMA shows an Ambetter plan on
    file as the second payer, never billed. Ambetter's first filing: 90 days as the safe date, 180 days for a participating
    provider (the payer's own last day), counted here from the first payer's remittance. Returns the run's
    SECONDARY_NOT_BILLED items and its EMA counts."""
    import datetime as dt
    from remitwatch import ema_ledger as el, ema_reports as er
    from remitwatch.detect import Detector
    from remitwatch.store import Store
    from tests import ema_fixtures as F, test_ema_wiring as W
    st = Store(":memory:", compact=True) if compact else Store(str(tmp_path / "store.sqlite"))
    er.ensure_schema(st.conn)
    st.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('ema:key_salt', ?)", (W.SALT_HEX,))
    st.conn.commit()
    lg = el.Ledger.open(str(tmp_path / "ema_ledger.sqlite"))
    try:
        visit = dt.date(2025, 12, 15)
        posted = dt.date.fromisoformat(paid_on) + dt.timedelta(days=2)
        sec = "Ambetter Sample Plan"
        W.remit(st, trace="777000931", paid_on=paid_on,
                claims=[{"pcn": F.claim_id(31), "last": "SAMPLE", "first": "ECHO",
                         "lines": [(visit.isoformat(), "99213", 150, 90, [("CO", "45", 30), ("PR", "2", 30)], {"allowed_cents": 12000})]}])
        rows = [W.charge(F.link_no(31), patient=2, svc=visit, posted=visit, secondary=sec),
                W.pay(F.link_no(31), "777000931", 90, patient=2, svc=visit, posted=posted, secondary=sec),
                W.adj(F.link_no(31), "CO-45", 30, patient=2, svc=visit, posted=posted, contractual=True, secondary=sec)]
        ar = [F.ar_row(claim=F.claim_id(31), link=F.link_no(31), status="Payment Pending", current=30, aging=posted, svc=visit,
                       patient=2, secondary=sec)]
        W.denial_world(st, lg, tmp_path, rows, ar_rows=ar)
        det = Detector(st, run_id="snb1", today=today)
        det.run()
        assert det.ema_errors == [], det.ema_errors
        st.conn.row_factory = None
        cols = [r[1] for r in st.conn.execute("PRAGMA table_info(findings)")]
        got = [dict(zip(cols, r)) for r in st.conn.execute("SELECT * FROM findings WHERE signal='SECONDARY_NOT_BILLED'")]
        wiring = json.loads(st.conn.execute("SELECT value FROM meta WHERE key='ema:wiring'").fetchone()[0])
        return got, wiring.get("counts") or {}
    finally:
        lg.close()
        st.close()


@pytest.mark.parametrize("compact", [False, True], ids=["store_file", "compact_image"])
def test_a_second_payer_past_its_safe_date_but_inside_its_own_limit_is_still_work(tmp_path, ema_env, compact):
    """Paid January 2, 2026: Ambetter's 90 days ended April 2, more than a month before May 7, but a participating
    provider has 180 days, to July 1. The share is still billable: the item is listed with both dates (it was dropped as a
    loss because only the 90 days were read)."""
    got, counts = _second_payer_run(tmp_path, compact, "2026-01-02", "2026-05-07")
    assert len(got) == 1, (got, counts)
    f = got[0]
    assert (f["appeal_deadline"], f["legal_deadline"]) == ("2026-04-02", "2026-07-01")
    assert f["work_state"] == "work_now", f["state_basis"]
    assert "Ambetter" in f["deadline_basis"] and "180" in f["deadline_basis"], f["deadline_basis"]
    assert not counts.get("snb_past_limit")


@pytest.mark.parametrize("compact", [False, True], ids=["store_file", "compact_image"])
def test_a_second_payer_past_its_own_limit_is_counted_not_dropped_silently(tmp_path, ema_env, compact):
    """Paid October 1, 2025: Ambetter's 180 days ended March 30, 2026, more than a month before May 7. The share is a
    loss, not work: no item, but the run counts it with its dollars (before, nothing recorded it)."""
    got, counts = _second_payer_run(tmp_path, compact, "2025-10-01", "2026-05-07")
    assert got == [], got
    assert counts.get("snb_past_limit") == 1 and counts.get("snb_past_limit_cents") == 3000, counts
