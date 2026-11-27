"""The fix wave's F8, the deadlines unit's follow-ups (plans/briefs/2026-11-26_fixwave.md, "F8 the deadlines follow-ups"):
each test holds a window or a payer's name to the words of its source, and each was written to fail before its fix.

- Anthem's commercial plans take a clinical appeal (a denial as not medically necessary or experimental) within 180
  calendar days of the notice: their 2026 Kentucky (v1, v2) and Indiana provider manuals, Clinical Appeals. The Claim
  Payment Dispute's two years 'does not apply to appeals regarding a clinical decision denial'.
- EMA and the 837s name the state's own Medicaid program with the state after the word ('Medicaid of Kentucky',
  'Medicaid of Indiana'): that is the program (Kentucky's: no appeal, 12 months, the crossover). A managed care plan's
  name that names the state ('<plan> Indiana Medicaid', '<plan> Medicaid of Kentucky') is never the state's program:
  Anthem's is Anthem's Medicaid group, and Indiana's managed care plans file within the program's 90 days (IHCP bulletin
  BT202072), 180 out of their network.
- Humana Medicare Advantage's corrected-claim rule cited a tip sheet nobody could find: it is not called confirmed.
- The VA's community care network goes out under payer ids VACCN and TWVACCN and is named after its administrator: the
  payer id decides its windows, and its first filing is the statute's 180 days (38 U.S.C. 1703D(b)).
- As the second payer, WellCare of Kentucky's Medicaid plan takes a claim within 12 months of the service or six months
  of the first payer's payment, whichever is later; Anthem's Medicaid plans count from the other payer's remittance
  (Indiana: 90 calendar days from its RA; Kentucky: the time frame begins on the third party's resolution).

Every claim, name, member number, amount and date is invented (tests/test_features, tests/test_remitwatch,
tests/test_837_sends, tests/test_ema_checks, tests/ema_fixtures.py). Where detection or EMA's checks date an item, the
test runs on the server's store file and on the compact image the serverless cycle analyses.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch import codes  # noqa: E402
from remitwatch.codes import appeal_rule, corrected_rule, filing_rule, legal_deadline, window_end  # noqa: E402
from tests.test_features import svc  # noqa: E402
from tests.test_payer_windows import _book, _run, dates  # noqa: E402

KINDS = [False, True]
KIND_IDS = ["store_file", "compact_image"]
CLINICAL = ("50", "55", "56")
NECESSITY = svc("11102", "150.00", "0.00", None, "CAS*CO*50*150.00~", dos="20260520")
MODIFIER = svc("99213", "150.00", "0.00", None, "CAS*CO*16*150.00~", dos="20260520") + "LQ*HE*N822~"


@pytest.fixture
def ema_env(monkeypatch):
    """The settings EMA's checks run under in tests (tests/test_ema_checks.py's _env)."""
    from remitwatch import ema_checks as ec, ema_reports as er
    from tests import ema_fixtures as F
    monkeypatch.setattr(er, "CLAIM_HOST_SUFFIX", F.HOST_SUFFIX)
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    for k in ("VERCEL", "REMITWATCH_EMA_REPORTS", "REMITWATCH_EMA_CLAIM_URL", "REMITWATCH_EMA_PAYER_ALIASES",
              "REMITWATCH_EMA_LEDGER", "REMITWATCH_EMA_REPORT_KEEP", "REMITWATCH_EMA_CLOSES"):
        monkeypatch.delenv(k, raising=False)
    ec._LAST_POST.clear()


def _ema_store(tmp_path, compact: bool):
    """A store with EMA's tables and key salt: the server's file or the compact image."""
    from remitwatch import ema_reports as er
    from remitwatch.store import Store
    from tests import test_ema_checks as TC
    st = Store(":memory:", compact=True) if compact else Store(str(tmp_path / "store.sqlite"))
    er.ensure_schema(st.conn)
    st.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('ema:key_salt', ?)", (TC.SALT_HEX,))
    st.conn.commit()
    return st


def _add(day: str, n: int) -> str:
    return (dt.date.fromisoformat(day) + dt.timedelta(days=n)).isoformat()


# ============================================================ 1. Anthem's commercial plans: a clinical appeal in 180 days
def test_an_anthem_commercial_clinical_denial_is_appealed_within_180_days_of_the_notice():
    """Anthem's 2026 Kentucky Commercial Provider Manual (v1, effective September 1, 2026, and v2, effective March 1, 2027)
    and Indiana Provider Manual (v1, effective August 1, 2026), Clinical Appeals: 'Providers and Facilities have one hundred
    eighty (180) calendar days to file a clinical appeal from the date they receive notice of Anthem's initial decision'; a
    clinical appeal is 'a situation in which an authorization or Claim for a service was denied as not medically necessary
    or experimental/investigational', and the Claim Payment Dispute 'does not apply to appeals regarding a clinical
    decision denial'. A denial on a March 1, 2026 remittance for one of Anthem's own plans (the plan names Kentucky or
    Indiana): 180 days, August 28, 2026, is the date shown; receipt is presumed 5 days after the remittance (as for KRS
    205.534), so September 2 is the last day. It was the two-year dispute, March 1, 2028, called confirmed."""
    for kind in ("commercial", "supplement", "other"):
        for carc in CLINICAL:
            for plan in ("KENTUCKY SAMPLE HSA", "IN SAMPLE HRA PPO", "INDIANA SAMPLE PPO", "SAMPLE PLAN OF KY"):
                r = appeal_rule("Anthem / BCBS", kind, plan, carc=carc)
                assert dates("2026-03-01", r) == ("2026-08-28", "2026-09-02"), (kind, carc, plan, r["label"])
                assert r["verified"] and "one hundred eighty (180) calendar days" in r["label"], r["label"]
                assert "Clinical Appeals" in r["source"], r["source"]
    # a payment dispute keeps the two years; a reason that is not known keeps them too (an underpayment has none)
    for carc in ("45", "97", "29", "16", None):
        for plan in (None, "SAMPLE PPO", "KENTUCKY SAMPLE PPO"):
            assert dates("2026-03-01", appeal_rule("Anthem / BCBS", "commercial", plan, carc=carc)) == ("2028-03-01", "2028-03-01"), \
                (carc, plan)
    # the Federal Employee Program keeps its own clinical row: the member's 6 months
    assert dates("2026-03-01", appeal_rule("Anthem / BCBS", "commercial", "FEDERAL EMPLOYEE PROGRAM", carc="50")) == \
        ("2026-08-28", "2026-09-01")
    # Anthem's Medicare Advantage and Medicaid plans keep theirs
    assert appeal_rule("Anthem / BCBS Medicare Advantage", "medicare_advantage", carc="50")["days"] == 120
    assert appeal_rule("Anthem / BCBS Medicaid", "medicaid", "INDIANA SAMPLE PLAN", carc="50")["days"] == 60


def test_a_bluecard_members_clinical_denial_keeps_the_claim_payment_disputes_last_day():
    """The same manuals, BlueCard Members: a clinical decision for a member of another Blue plan (the home plan) is the
    home plan's, and 'if the appeal relates to a Claim denial, and the Provider or Facility did not receive this
    information from the Member's Home Plan ... a Claim Payment Dispute should be submitted ... to the local Anthem Plan':
    two years from the EOP. The 835's plan says which members those are: BlueCard's own words (ITS HOST, BLUECARD, BLUE
    CARD) or a plan of a state other than Kentucky and Indiana, by its name or its two-letter code. Their denial is worked
    by the clinical appeal's 180 days (the home plan's directions can be that short) and stays open to the dispute's last
    day, March 1, 2028: had it expired on the clinical appeal's last day, a denial still open to a dispute would have left
    the list. A denial whose plan is not known (EMA names none) is dated the same way, since the item cannot say it is
    Anthem's own member's."""
    for plan in ("ITS HOST PPO SAMPLE", "SAMPLE BLUECARD PPO", "SAMPLE BLUE CARD PREFERRED", "GA SAMPLE HSA PPO",
                 "OHIO SAMPLE HSA", "VA SAMPLE HSA PPO", "  CT SAMPLE PPO", None):
        for carc in CLINICAL:
            r = appeal_rule("Anthem / BCBS", "commercial", plan, carc=carc)
            assert dates("2026-03-01", r) == ("2026-08-28", "2028-03-01"), (plan, carc, r["label"])
            assert r["verified"] and "Claim Payment Dispute" in r["label"] and "180" in r["label"], r["label"]
            assert "BlueCard Members" in r["source"], r["source"]
            if plan:
                assert "Home Plan" in r["label"], r["label"]
            else:
                assert "not known" in r["label"], r["label"]
    # the payer's name gives the state where the plan names none (a remittance from another state's Anthem plan)
    r = appeal_rule("Anthem / BCBS", "commercial", "SAMPLE PPO", carc="50", payer_name="ANTHEM BLUE CROSS OF GEORGIA")
    assert dates("2026-03-01", r) == ("2026-08-28", "2028-03-01"), r["label"]
    # a payment dispute of a BlueCard member keeps the dispute's two years as its date too
    assert dates("2026-03-01", appeal_rule("Anthem / BCBS", "commercial", "ITS HOST PPO SAMPLE", carc="45")) == \
        ("2028-03-01", "2028-03-01")


@pytest.mark.parametrize("compact", KINDS, ids=KIND_IDS)
def test_detection_dates_an_anthem_commercial_clinical_denial_by_its_180_days(tmp_path, compact):
    """A CO-50 (not medically necessary) denial of a May 20, 2026 visit on Anthem's June 1 remittance, for one of Anthem's
    own Kentucky plans: due November 28, 2026 (180 days), last day December 3 (receipt 5 days after the remittance). It was
    dated June 1, 2028."""
    got = _run(tmp_path, compact, _book("12", "KENTUCKY SAMPLE PPO", "20260520", [NECESSITY]), "20260601", "ANTHEM", "ANTHM",
               today="2026-07-15")
    got.pop("_store").close()
    f = got["DENIED_MEDICAL_NECESSITY"]
    assert f["payer_group"] == "Anthem / BCBS", f["payer_group"]
    assert (f["appeal_deadline"], f["legal_deadline"]) == ("2026-11-28", "2026-12-03"), (f["appeal_deadline"], f["legal_deadline"],
                                                                                      f["deadline_basis"])
    assert "clinical appeal" in f["deadline_basis"], f["deadline_basis"]


@pytest.mark.parametrize("compact", KINDS, ids=KIND_IDS)
def test_detection_keeps_a_bluecard_members_clinical_denial_open_to_the_dispute(tmp_path, compact):
    """The same denial for a BlueCard member (the 835's plan says ITS HOST): due November 28, 2026 all the same, and open
    to the Claim Payment Dispute's last day, June 1, 2028, so it does not leave the list on the clinical appeal's."""
    got = _run(tmp_path, compact, _book("12", "ITS HOST PPO SAMPLE", "20260520", [NECESSITY]), "20260601", "ANTHEM", "ANTHM",
               today="2026-07-15")
    got.pop("_store").close()
    f = got["DENIED_MEDICAL_NECESSITY"]
    assert (f["appeal_deadline"], f["legal_deadline"]) == ("2026-11-28", "2028-06-01"), (f["appeal_deadline"], f["legal_deadline"],
                                                                                      f["deadline_basis"])
    assert "Home Plan" in f["deadline_basis"], f["deadline_basis"]


@pytest.mark.parametrize("compact", KINDS, ids=KIND_IDS)
def test_a_clinical_denial_only_ema_saw_is_dated_by_anthems_180_days(tmp_path, ema_env, compact):
    """EMA posted a CO-50 denial from an Anthem commercial plan on April 10, 2026 for a March 2 visit, with no 835 on file.
    The item is dated by the clinical appeal's 180 days from the posting less the payer's usual delay (it was dated by the
    two-year Claim Payment Dispute); a CO-97 (bundled) denial of the same payer keeps the two years."""
    from remitwatch import ema_checks as ec, ema_ledger as el
    from tests import ema_fixtures as F, test_ema_checks as TC
    st = _ema_store(tmp_path, compact)
    lg = el.Ledger.open(str(tmp_path / "ema_ledger.sqlite"))
    try:
        TC.denial_world(st, lg, tmp_path, TC.c5_rows(link=2, reason="CO-50", payer=F.PAYERS["bcbs"])
                        + TC.c5_rows(link=3, patient=2, reason="CO-97", cpt="11102", payer=F.PAYERS["bcbs"]))
        items = {i["claim"]["procedure_code"]: i for i in ec.ema_denials(st.conn, today=TC.TODAY)}
        it = items["99213"]
        assert it["signal"] == "EMA_DENIED" and it["claim"]["payer_group"] == "Anthem / BCBS" and it["carc"] == "50", it
        ev = it["evidence"]
        start = _add(ev["first_denial_posted"], -int(ev["posting_lag_days"]))
        assert it["appeal_deadline"] == _add(start, 180), (it["appeal_deadline"], start, it["deadline_basis"])
        assert "clinical appeal" in it["deadline_basis"], it["deadline_basis"]
        other = items["11102"]
        ev = other["evidence"]
        start = _add(ev["first_denial_posted"], -int(ev["posting_lag_days"]))
        assert other["appeal_deadline"] == _add(start, 730), (other["appeal_deadline"], start)
    finally:
        lg.close()
        st.close()


def _ema_clinical_item(tmp_path, compact: bool) -> dict:
    """Detection's item for EMA's CO-50 denial from an Anthem commercial plan, with no 835 on file (detect._emit_proto)."""
    from remitwatch import ema_checks as ec, ema_ledger as el
    from remitwatch.detect import Detector
    from tests import ema_fixtures as F, test_ema_checks as TC
    st = _ema_store(tmp_path, compact)
    lg = el.Ledger.open(str(tmp_path / "ema_ledger.sqlite"))
    try:
        TC.denial_world(st, lg, tmp_path, TC.c5_rows(link=2, reason="CO-50", payer=F.PAYERS["bcbs"]))
        proto = [p for p in ec.ema_denials(st.conn, today=TC.TODAY) if p["carc"] == "50"]
        assert len(proto) == 1, proto
        det = Detector(st, run_id="t", today=TC.TODAY)
        det.ema_on = True
        det.load()
        det._emit_proto(proto[0])
        f = [x for x in det.findings.values() if x["signal"] == "EMA_DENIED"]
        assert len(f) == 1, f
        return dict(f[0], evidence=proto[0]["evidence"])
    finally:
        lg.close()
        st.close()


@pytest.mark.parametrize("compact", KINDS, ids=KIND_IDS)
def test_an_ema_clinical_denial_item_is_worked_by_the_clinical_appeal_and_stays_open_to_the_dispute(tmp_path, ema_env, compact):
    """EMA names no plan, so its Anthem clinical denial may be a BlueCard member's, whose denial goes to the local plan's
    Claim Payment Dispute: the item is due by the clinical appeal's 180 days from the remittance (EMA's posting less its
    usual delay) and stays listed to the dispute's two years, its last day."""
    f = _ema_clinical_item(tmp_path, compact)
    ev = f["evidence"]
    start = _add(ev["first_denial_posted"], -int(ev["posting_lag_days"]))
    assert f["appeal_deadline"] == _add(start, 180), (f["appeal_deadline"], start)
    assert f["legal_deadline"] == window_end(start, {"months": 24}), (f["legal_deadline"], start)


@pytest.mark.parametrize("compact", KINDS, ids=KIND_IDS)
def test_an_ema_denial_items_last_day_comes_from_the_window_its_reason_picks(tmp_path, ema_env, compact, monkeypatch):
    """Detection works out an EMA denial's last day from the same window the check dated it by: the one its reason picks
    (detect._emit_proto passes the reason to appeal_rule, as ema_checks.ema_denials does). Shown with an invented window for
    CO-50 of 100 days and a last day 111 days from the remittance, so the reason's window is told from the payer's
    general one (two years)."""
    invented = {"match": r"^Anthem / BCBS$", "kinds": {"commercial"}, "carcs": frozenset({"50"}), "days": 100, "verified": True,
                "legal": {"days": 111}, "label": "An invented window for this test", "source": "invented"}
    monkeypatch.setattr(codes, "APPEAL_RULES", [invented] + codes.APPEAL_RULES)
    f = _ema_clinical_item(tmp_path, compact)
    ev = f["evidence"]
    start = _add(ev["first_denial_posted"], -int(ev["posting_lag_days"]))
    assert f["appeal_deadline"] == _add(start, 100), (f["appeal_deadline"], start)
    assert f["legal_deadline"] == _add(start, 111), (f["legal_deadline"], start)


# ============================================================ 2. the state's own Medicaid program, by EMA's names
def test_the_state_program_named_with_the_state_after_the_word_medicaid_is_the_state_program():
    """EMA's payer list and the 837s name Kentucky's and Indiana's fee-for-service Medicaid 'Medicaid of Kentucky' and
    'Medicaid of Indiana' (the 835s: 'KY MEDICAID', 'IN MEDICAID'). They were read as 'Medicaid (other)', so the Kentucky
    program's rules (no appeal of an ordinary denial, 12 months, the crossover) and Indiana's (180 days, the 60-day review)
    missed the items only EMA or an 837 names."""
    for name in ("Medicaid of Kentucky", "MEDICAID OF KENTUCKY", "Medicaid Kentucky", "Medicaid - KY", "Medicaid of KY",
                 "KY MEDICAID", "Kentucky Medicaid", "Medcaid of Kentucky"):
        assert codes.normalize_payer(name) == ("Medicaid KY", "medicaid"), name
        for cfi in (None, "MC"):
            assert codes.payer_group_kind(name, cfi) == ("Medicaid KY", "medicaid"), (name, cfi)
    for name in ("Medicaid of Indiana", "Medicaid - IN", "Medicaid Indiana", "IN MEDICAID", "Indiana Medicaid"):
        assert codes.normalize_payer(name) == ("Medicaid IN", "medicaid"), name
    grp, kind = codes.payer_group_kind("Medicaid of Kentucky", None)
    a = appeal_rule(grp, kind, payer_name="Medicaid of Kentucky")
    assert a["no_appeal"] and "907 KAR 1:671" in a["source"], a
    f = filing_rule(grp, kind, payer_name="Medicaid of Kentucky")
    assert f["verified"] and "twelve (12) months" in f["label"] and window_end("2026-03-02", f) == "2027-03-02", f
    c = corrected_rule(grp, kind, payer_name="Medicaid of Kentucky")
    assert c and c.get("crossover"), c
    grp, kind = codes.payer_group_kind("Medicaid of Indiana", None)
    assert filing_rule(grp, kind)["days"] == 180 and appeal_rule(grp, kind)["days"] == 60


def test_a_plan_that_names_its_state_by_the_code_after_of_is_read_as_that_states():
    """EMA names a Kentucky managed care plan '<plan> Medicaid of Ky': the state is the code after 'of', so the plan gets
    Kentucky's managed care windows (an appeal in 120 days, the last day 125: KRS 205.534's receipt) instead of the 90-day
    default with no last day of its own, which would close the item 35 days early. A word that starts with the letters
    (OF INSURANCE) names no state."""
    assert codes.plan_state(None, "Sample Health Plan Medicaid of Ky") == "KY"
    assert codes.plan_state(None, "Sample Health Plan Medicaid of In") == "IN"
    assert codes.plan_state(None, "Sample Plan of Insurance") is None
    assert codes.plan_state("SAMPLE PPO", "Sample Health Plan") is None
    name = "Sample Health Plan Medicaid of Ky"
    g, k = codes.normalize_payer(name)
    assert (g, k) == ("Medicaid (other)", "medicaid")
    r = appeal_rule(g, k, payer_name=name)
    assert (r["days"], r["legal"]) == (120, {"days": 125}), r["label"]
    assert "Kentucky Medicaid managed care" in r["label"], r["label"]


def test_a_managed_care_plans_name_that_names_the_state_is_not_the_state_program():
    """A Medicaid managed care plan's name starts with the plan's own ('Anthem <state> Medicaid <program>', '<plan> <program>
    Indiana Medicaid', '<plan> Medicaid of Kentucky'): it is never the state's fee-for-service program, whose rules
    (Indiana's 180 days and 60-day review, Kentucky's no appeal) are not the plan's. Anthem's Medicaid names are Anthem's
    Medicaid group, the one its 835s get from CLP06 MC: Indiana's plan files within 90 days and disputes within 60, its
    Kentucky plan within 365 days. WellCare's Kentucky plan keeps WellCare's group."""
    for name in ("Anthem Indiana Medicaid Sample Wise", "Anthem Samplepath Indiana Medicaid", "Anthem Indiana Medicaid SMP"):
        grp, kind = codes.payer_group_kind(name, None)
        assert (grp, kind) == ("Anthem / BCBS Medicaid", "medicaid"), name
        f, a = filing_rule(grp, kind, payer_name=name), appeal_rule(grp, kind, payer_name=name)
        assert (f["days"], a["days"]) == (90, 60) and f["verified"] and a["verified"], (name, f["label"], a["label"])
    grp, kind = codes.payer_group_kind("Anthem Kentucky Medicaid", None)
    assert grp == "Anthem / BCBS Medicaid" and filing_rule(grp, kind, payer_name="Anthem Kentucky Medicaid")["days"] == 365
    assert not appeal_rule(grp, kind, payer_name="Anthem Kentucky Medicaid")["no_appeal"]
    for name in ("Sample Health Plan Medicaid of Kentucky", "Sample Better Health Medicaid of Ky", "Sample Kentucky Medicaid Plan",
                 "Sample Healthy Plans Medicaid of Kentucky"):
        grp, kind = codes.payer_group_kind(name, None)
        assert grp != "Medicaid KY" and kind == "medicaid", (name, grp)
        assert not appeal_rule(grp, kind, payer_name=name)["no_appeal"], name
    for name in ("Sample Health Indiana Medicaid Plan", "Sample Hoosier Healthwise Indiana Medicaid",
                 "Sample Healthy Plans  Samplepath Indiana Medicaid", "Samplewise Indiana Medicaid Sample Wise"):
        grp, kind = codes.payer_group_kind(name, None)
        assert grp != "Medicaid IN" and kind == "medicaid", (name, grp)
    assert codes.payer_group_kind("Sample WellCare Medcaid of Kentucky", None) == ("WellCare / Ambetter (Centene) Medicaid", "medicaid")


def test_indiana_medicaid_managed_care_plans_file_within_the_programs_90_days():
    """IHCP bulletin BT202072 (June 11, 2020): 'the timely filing limit on claims for services rendered to members enrolled
    in managed care by in-network providers is returning to 90 days', and 'The managed care claim timely filing limit for
    out-of-network providers also remains at 180 calendar days'. An Indiana Medicaid managed care plan that no rule of its
    own covers: a September 14, 2026 visit is due December 13, 2026, and its last day (out of the plan's network) is March
    13, 2027. It was 365 days, or Indiana fee-for-service's 180, by the name. Its dispute window is not known: 60 days is
    used, the window Anthem's Indiana plan gives and the program's own review."""
    for grp, name in (("Medicaid (other)", "Sample Health Indiana Medicaid Plan"), ("Humana Medicaid", "HUMANA"),
                      ("WellCare / Ambetter (Centene) Medicaid", "Sample Hoosier Healthwise Indiana Medicaid")):
        f = filing_rule(grp, "medicaid", payer_name=name, plan="INDIANA SAMPLE PLAN")
        assert dates("2026-09-14", f) == ("2026-12-13", "2027-03-13"), (grp, f["label"])
        assert f["verified"] and "BT202072" in f["source"], f
        a = appeal_rule(grp, "medicaid", payer_name=name, plan="INDIANA SAMPLE PLAN")
        assert a["days"] == 60 and not a["verified"], (grp, a["label"])
    # the program itself and Anthem's own plan keep their rows
    assert filing_rule("Medicaid IN", "medicaid")["days"] == 180
    assert filing_rule("Anthem / BCBS Medicaid", "medicaid", plan="INDIANA SAMPLE PLAN")["legal"] is None


@pytest.mark.parametrize("compact", KINDS, ids=KIND_IDS)
def test_a_kentucky_medicaid_denial_ema_names_with_the_state_after_the_word_is_dated_by_a_new_claim(tmp_path, ema_env, compact):
    """EMA posted a CO-197 denial from 'Medicaid of Kentucky' on April 10, 2026 for a March 2, 2026 visit, with no 835 on
    file. Kentucky Medicaid takes no appeal of an ordinary claim denial (907 KAR 1:671): the item is dated by a corrected or
    new claim within 12 months of the service, March 2, 2027 (it was 'Medicaid (other)' with a 90-day appeal)."""
    from remitwatch import ema_checks as ec, ema_ledger as el
    from tests import test_ema_checks as TC
    st = _ema_store(tmp_path, compact)
    lg = el.Ledger.open(str(tmp_path / "ema_ledger.sqlite"))
    try:
        TC.denial_world(st, lg, tmp_path, TC.c5_rows(link=2, payer="Medicaid of Kentucky"))
        items = ec.ema_denials(st.conn, today=TC.TODAY)
        assert len(items) == 1, items
        it = items[0]
        assert it["signal"] == "EMA_DENIED" and it["claim"]["payer_group"] == "Medicaid KY", it["claim"]
        assert it["appeal_deadline"] == "2027-03-02", (it["appeal_deadline"], it["deadline_basis"])
        assert "907 KAR 1:671" in it["deadline_basis"] and "twelve (12) months" in it["deadline_basis"], it["deadline_basis"]
    finally:
        lg.close()
        st.close()


@pytest.mark.parametrize("compact", KINDS, ids=KIND_IDS)
def test_a_claim_ema_shows_kentucky_medicaid_never_answered_is_dated_by_its_12_months(tmp_path, ema_env, compact):
    """EMA's Production report: a visit on March 6, 2026 charged to 'Medicaid of Kentucky', no answer posted. Its first
    filing runs 12 months from the service (907 KAR 1:673), confirmed: the item named the common Medicaid limit ('check the
    plan'), not confirmed. (Charged on time, the date shown then runs the payer's window past that limit: EMA's check.)"""
    import datetime as dtm
    from remitwatch import ema_checks as ec, ema_ledger as el
    from tests import ema_fixtures as F, test_ema_checks as TC
    st = _ema_store(tmp_path, compact)
    lg = el.Ledger.open(str(tmp_path / "ema_ledger.sqlite"))
    try:
        ky = "Medicaid of Kentucky"
        rows = [TC.charge(F.link_no(41), svc=dtm.date(2026, 3, 6), posted=dtm.date(2026, 3, 9), status="Payment Pending", payer=ky,
                          primary=ky),
                TC.charge(F.link_no(98), cpt="99212", patient=3, payer=ky, primary=ky),
                TC.pay(F.link_no(98), "PAPER-41", 20, cpt="99212", patient=3, posted=dtm.date(2026, 4, 28), payer=ky, primary=ky),
                TC.charge(F.link_no(99), cpt="99212", patient=3, svc=dtm.date(2026, 3, 20), posted=dtm.date(2026, 5, 1))]
        TC.load(st, lg, tmp_path, rows)
        items = {i["key_parts"]: i for i in ec.unanswered_claims(st.conn, today=TC.TODAY)}
        it = items[(f"link:{F.link_no(41)}",)]
        assert it["signal"] == "NO_ANSWER" and it["claim"]["payer_group"] == "Medicaid KY", it["claim"]
        basis = it["deadline_basis"]
        assert "send it again by 2027-03-06, the first-filing limit (Kentucky Medicaid (fee-for-service)" in basis, basis
        assert "twelve (12) months" in basis and "check the plan" not in basis, basis
    finally:
        lg.close()
        st.close()


# ============================================================ 3. Humana Medicare Advantage's corrected claim
def test_humana_medicare_advantage_corrected_claim_rule_is_not_called_confirmed_without_its_source():
    """The rule cited 'Humana, Claim Disputes and Corrected Claims tip sheet, August 2026' and was marked verified; the A17
    research and this unit could not find that tip sheet (humana.com's how-to page no longer carries it). Humana's 2026
    Provider Manual (773902ALL0725-A) gives only the claim limit: 'Medicare lines of business: 1 year from date of
    service'. The date stays a year from the service; the rule says it is not confirmed for a corrected claim."""
    r = corrected_rule("Humana Medicare Advantage", "medicare_advantage")
    assert r is not None and not r["verified"], r
    assert "tip sheet" not in r["source"].lower() and "773902ALL0725" in r["source"], r["source"]
    assert "1 year from date of service" in r["label"], r["label"]
    assert window_end("2026-02-10", r) == "2027-02-10"


@pytest.mark.parametrize("compact", KINDS, ids=KIND_IDS)
def test_a_humana_medicare_advantage_corrected_claim_item_says_its_window_is_not_confirmed(tmp_path, compact):
    """A Humana Medicare Advantage claim missing a modifier (CO-16, N822) of a May 20, 2026 visit on a June 1 remittance:
    the corrected claim is dated a year from the service (May 20, 2027) and the item says that window is not confirmed;
    Humana's 18-month dispute from the determination stays the last day (December 1, 2027)."""
    got = _run(tmp_path, compact, _book("16", "SAMPLE MA PLAN", "20260520", [MODIFIER]), "20260601", "HUMANA", "61101")
    got.pop("_store").close()
    f = got["DENIED_MISSING_MODIFIER"]
    assert f["payer_group"] == "Humana Medicare Advantage", f["payer_group"]
    assert (f["appeal_deadline"], f["legal_deadline"]) == ("2027-05-20", "2027-12-01"), (f["appeal_deadline"], f["legal_deadline"])
    assert "not confirmed" in f["deadline_basis"].split(". After that")[0], f["deadline_basis"]


# ============================================================ 4. the VA's community care network, by its payer id
def test_va_community_care_first_filing_is_the_statutes_180_days():
    """38 U.S.C. 1703D(b): a provider 'shall submit to the Secretary a claim for payment ... not later than 180 days after the
    date on which the entity or provider furnished' the care. The VA's network was dated 180 days by the common limit for
    its kind of payer, not confirmed."""
    r = filing_rule("VA Community Care", "va")
    assert r["verified"] and r["days"] == 180 and "1703D(b)" in r["source"], r
    assert "not later than 180 days after the date" in r["label"], r["label"]
    assert window_end("2026-08-10", r) == "2027-02-06"


def test_the_va_networks_payer_id_decides_its_windows_whatever_its_name():
    """EMA and the 837s name the VA's network after its administrator ('Optum Unitedhealth Care'), whose name rules give
    UnitedHealthcare's administered plans (12 months to dispute, the commercial 90 days to file). A claim that carries the
    network's payer id (VACCN, TWVACCN) is the VA's: its first filing is the statute's 180 days and its dispute window the
    VA's, whatever group its name gave."""
    for pid in ("VACCN", "TWVACCN", " vaccn "):
        f = filing_rule("Optum (UHC)", "commercial", payer_name="Optum Unitedhealth Care", payer_id=pid)
        assert f["days"] == 180 and f["verified"] and "1703D(b)" in f["source"], (pid, f["label"])
        a = appeal_rule("Optum (UHC)", "commercial", payer_name="Optum Unitedhealth Care", payer_id=pid)
        assert a == appeal_rule("VA Community Care", "va"), (pid, a["label"])
        assert corrected_rule("Optum (UHC)", "commercial", payer_id=pid) == corrected_rule("VA Community Care", "va")
    # another payer id under the same name keeps the name's windows
    assert filing_rule("Optum (UHC)", "commercial", payer_name="Optum Unitedhealth Care", payer_id="00100")["days"] == 90
    assert appeal_rule("Optum (UHC)", "commercial", payer_id="00100")["days"] == 365
    # the product's lists of the network's payer ids agree
    from remitwatch import detect, ema_reports
    assert codes.VA_PAYER_IDS == detect.VA_PAYER_IDS == ema_reports.VA_PAYER_IDS


@pytest.mark.parametrize("kind", ["file", "compact"])
def test_a_never_answered_send_to_the_va_network_is_dated_by_the_statute(tmp_path, kind):
    """A claim sent August 12, 2026 to the VA's network (payer id VACCN, named after its administrator) for an August 10
    visit, never answered, with the remittances reaching November 10: its item is the VA's, and the date it must reach the
    payer by is the statute's 180 days from the service (February 6, 2027), confirmed."""
    from tests.test_837_sends import VA, _chased, _remits, _store, _visits, clm837, send
    s = _store(tmp_path, kind)
    send(s, tmp_path, [clm837("CB0000V01C017", [("99214", "100.00", "20260810")], payer=VA, member="V00000001")], "20260812")
    _remits(s, tmp_path, _visits(), "20261110")
    found = _chased(s, "t1")
    a = found["CB0000V01C017"]
    assert a["payer_group"] == "VA Community Care" and a["payer_kind"] == "va", (a["payer_group"], a["payer_kind"])
    assert "2027-02-06" in a["deadline_basis"] and "1703D(b)" in a["deadline_basis"], a["deadline_basis"]
    assert "VA Community Care Network: 180 days from the date of service, not confirmed" not in a["deadline_basis"], a["deadline_basis"]
    s.close()


# ============================================================ 5. a claim the plan gets as the second payer
def test_wellcare_kentucky_medicaid_as_second_payer_has_six_months_from_the_first_payers_payment():
    """WellCare of Kentucky 2025-2026 Medicaid Provider Manual, Timely Claims Submission: 'Providers must submit claims
    (initialed, corrected and voided) within 12 months (or six months from the Medicaid or primary insurance payment date,
    whichever is later) from the date of service'. A January 10, 2026 visit the first payer paid on December 20, 2026: due
    June 20, 2027 (six months from that payment). Paid on February 15, 2026: due January 10, 2027 (12 months from the
    service). Both are the plan's own limit: no later last day. It was the 12 months only, with the first payer's
    remittance plus 12 months (December 20, 2027) as a guessed last day."""
    r = filing_rule("WellCare / Ambetter (Centene) Medicaid", "medicaid", payer_name="Sample WellCare Medcaid of Kentucky")
    safe, last, how = codes.second_claim_window(r, "2026-01-10", "2026-12-20", first_payer="Sample Health Plan")
    assert (safe, last) == ("2027-06-20", None), (safe, last, how)
    assert "6 months from Sample Health Plan's payment (2026-12-20)" in how, how
    assert codes.second_claim_window(r, "2026-01-10", "2026-02-15")[:2] == ("2027-01-10", None)
    assert codes.second_claim_window(r, "2026-01-10", None)[:2] == ("2027-01-10", None)
    assert "not counted here" not in r["label"], r["label"]


def test_anthem_medicaid_as_second_payer_counts_from_the_other_payers_remittance():
    """Anthem's Indiana Medicaid plans (IHCP Works 2026, Timely Filing): as the 'Secondary payer — providers have 90
    calendar days between the other payer's remittance advice (RA) date and the Anthem receipt date'. Its Kentucky
    Medicaid manual, 11.7 Timely Filing: 'For cases of coordination of benefits/subrogation, the time frames for filing a
    claim will begin on the date of the third party's resolution of the claim' (365 days). A January 10, 2026 visit the
    first payer answered on March 2, 2026: Indiana's plan by May 31, 2026, Kentucky's by March 2, 2027, a plan that does
    not say which by Indiana's (the shorter). They were dated from the service (April 10, 2026; January 10, 2027)."""
    ind = filing_rule("Anthem / BCBS Medicaid", "medicaid", plan="INDIANA SAMPLE PLAN")
    assert codes.second_claim_window(ind, "2026-01-10", "2026-03-02")[:2] == ("2026-05-31", None)
    ken = filing_rule("Anthem / BCBS Medicaid", "medicaid", plan="KENTUCKY SAMPLE MEDICAID")
    assert codes.second_claim_window(ken, "2026-01-10", "2026-03-02")[:2] == ("2027-03-02", None)
    unknown = filing_rule("Anthem / BCBS Medicaid", "medicaid")
    assert codes.second_claim_window(unknown, "2026-01-10", "2026-03-02")[:2] == ("2026-05-31", None)
    _s, _l, how = codes.second_claim_window(ind, "2026-01-10", "2026-03-02", first_payer="Sample Health Plan")
    assert "Sample Health Plan's remittance (2026-03-02)" in how, how


# ============================================================ 6. the fix round: the reviewers' findings (fx-windows r1, r2)
def _claim_of(i: int, member: str, plan: str, lines: str, dos: str = "20260215", status: str = "1") -> str:
    """tests/test_features.claim with the member id given (NM1*QC ... MI): invented ids only."""
    from tests.test_features import NPI
    return (f"CLP*CB{i:07d}C017*{status}*500.00*100.00*0.00*12*PC{i}*11~NM1*QC*1*SAMPLE*PAT****MI*{member}~"
            f"NM1*82*2*TEST PROVIDER*****XX*{NPI}~REF*CE*{plan}~DTM*232*{dos}~{lines}")


@pytest.mark.parametrize("compact", KINDS, ids=KIND_IDS)
def test_a_denial_of_another_states_member_keeps_two_years_when_the_plan_name_gives_no_state(tmp_path, compact):
    """Review r1-1 (high). The plan's name does not say whose member it is: Anthem's plans and other Blue plans give their
    products the same names, and the state shows only on some of a home plan's plan names. 25 visits of members whose ids
    start ZQX on a plan naming Ohio, then a ZQX member whose plan names no state ('SAMPLE ACCESS PPO') denied CO-55 on a
    March 1, 2026 remittance, seen on November 24, 2026 (268 days later): the denial keeps the Claim Payment Dispute's two
    years as its last day (March 1, 2028) and is not expired. It expired on September 2, 2026 (the clinical appeal's 185
    days), as if the member were Anthem's own."""
    visits = [_claim_of(i, f"ZQX{100000000 + i}", "OHIO SAMPLE HSA",
                        svc("99214", "223.65", "117.60", "117.60", "CAS*CO*45*106.05~", dos="20260215")) for i in range(1, 26)]
    denied = _claim_of(300, "ZQX100000099", "SAMPLE ACCESS PPO", svc("11102", "150.00", "0.00", None, "CAS*CO*55*150.00~", dos="20260215"))
    got = _run(tmp_path, compact, visits + [denied], "20260301", "ANTHEM", "ANTHM", today="2026-11-24")
    store = got.pop("_store")
    try:
        items = [f for f in got.values() if str(f.get("carc") or "") == "55"]
        assert len(items) == 1, sorted(got)
        f = items[0]
        assert f["payer_group"] == "Anthem / BCBS" and f["payer_kind"] == "commercial"
        assert f["appeal_deadline"] == "2026-08-28", (f["appeal_deadline"], f["deadline_basis"])
        assert (f["legal_deadline"] or f["appeal_deadline"]) == "2028-03-01", (f["appeal_deadline"], f["legal_deadline"])
        assert f["work_state"] != "expired", (f["work_state"], f["appeal_deadline"], f["legal_deadline"])
    finally:
        store.close()


def test_only_a_plan_that_names_kentucky_or_indiana_shows_anthems_own_member():
    """Reviews r1-1 and r2-6. A plan whose name gives no state ('SAMPLE PPO') may be a BlueCard member's: its clinical denial
    is worked by the clinical appeal's 180 days and stays open to the dispute's two years, as a plan that is not known. A
    plan naming Kentucky or Indiana is Anthem's own member's: 185 days is its last day. The payer's name is not read for it:
    the local plan sends the 835 for its own members and BlueCard members alike, so a payer named for Kentucky does not make
    a state-free plan Anthem's own."""
    for plan in ("SAMPLE PPO", "SAMPLE BLUE ACCESS PPO", "SAMPLE HSA", "PPO"):
        for carc in CLINICAL:
            r = appeal_rule("Anthem / BCBS", "commercial", plan, carc=carc)
            assert dates("2026-03-01", r) == ("2026-08-28", "2028-03-01"), (plan, carc, r["label"][:90])
            assert "not known" in r["label"] and "no state" in r["label"], r["label"]
        r = appeal_rule("Anthem / BCBS", "commercial", plan, carc="50", payer_name="SAMPLE BLUE CROSS OF KENTUCKY")
        assert dates("2026-03-01", r) == ("2026-08-28", "2028-03-01"), (plan, r["label"][:90])
    for plan in ("KENTUCKY SAMPLE PPO", "INDIANA SAMPLE PPO", "IN SAMPLE HSA"):
        r = appeal_rule("Anthem / BCBS", "commercial", plan, carc="50")
        assert dates("2026-03-01", r) == ("2026-08-28", "2026-09-02"), plan
        assert "Anthem's own member" in r["label"], r["label"]


@pytest.mark.parametrize("compact", KINDS, ids=KIND_IDS)
def test_detection_keeps_a_clinical_denial_of_a_plan_naming_no_state_listed_past_185_days(tmp_path, compact):
    """Review r2-6. A CO-50 denial on Anthem's June 1, 2026 remittance, plan 'SAMPLE PPO' (no state, no BlueCard words),
    seen on December 10, 2026: past the 185 days, inside the two-year dispute a BlueCard member still has. The item is not
    expired: its last day is June 1, 2028."""
    got = _run(tmp_path, compact, _book("12", "SAMPLE PPO", "20260520", [NECESSITY]), "20260601", "ANTHEM", "ANTHM", today="2026-12-10")
    got.pop("_store").close()
    f = got["DENIED_MEDICAL_NECESSITY"]
    assert (f["appeal_deadline"], f["legal_deadline"]) == ("2026-11-28", "2028-06-01"), (f["appeal_deadline"], f["legal_deadline"])
    assert f.get("work_state") != "expired", (f.get("work_state"), f.get("state_basis"))


@pytest.mark.parametrize("compact", KINDS, ids=KIND_IDS)
def test_an_ema_denial_of_an_indiana_medicaid_plan_named_with_the_state_keeps_its_60_days(tmp_path, ema_env, compact):
    """Review r2-1. EMA names an Indiana Medicaid managed care plan 'Sample Health Indiana Medicaid': 'Medicaid (other)' by
    the name rules, Indiana's by the state its name gives. EMA's check passed no payer name to the window rules, so the
    plan's state was lost and the EMA-only denial fell to the Medicaid default, 90 days: 30 days later than the plan's 60
    (and than the same plan's 835 denial)."""
    from remitwatch import ema_checks as ec, ema_ledger as el
    from tests import test_ema_checks as TC
    st = _ema_store(tmp_path, compact)
    lg = el.Ledger.open(str(tmp_path / "ema_ledger.sqlite"))
    try:
        name = "Sample Health Indiana Medicaid"
        TC.denial_world(st, lg, tmp_path, TC.c5_rows(link=2, reason="CO-197", payer=name, posted=dt.date(2026, 4, 10)))
        items = ec.ema_denials(st.conn, today="2026-05-03")
        assert len(items) == 1, [i.get("title") for i in items]
        it = items[0]
        ev = it["evidence"]
        start = _add(ev["first_denial_posted"], -int(ev["posting_lag_days"]))
        assert it["claim"]["payer_group"] == "Medicaid (other)", it["claim"]
        assert it["appeal_deadline"] == _add(start, 60), (it["appeal_deadline"], start, it["deadline_basis"])
        assert "Indiana Medicaid managed care plan" in it["deadline_basis"], it["deadline_basis"]
    finally:
        lg.close()
        st.close()


@pytest.mark.parametrize("compact", KINDS, ids=KIND_IDS)
def test_an_ema_denial_of_a_kentucky_plan_named_medicaid_of_ky_gets_kentuckys_120_days(tmp_path, ema_env, compact):
    """Review r2-1. '<plan> Medicaid of Ky' is a Kentucky managed care plan (the state's code after 'of'): a denial EMA posted
    June 10, 2026 (after April 14, 2026) has KRS 205.534's 120 days, its last day 125. It was dated by the 90-day Medicaid
    default, which would close it 30 days early. Detection's item keeps the same dates (detect._emit_proto)."""
    from remitwatch import ema_checks as ec, ema_ledger as el
    from remitwatch.detect import Detector
    from tests import test_ema_checks as TC
    st = _ema_store(tmp_path, compact)
    lg = el.Ledger.open(str(tmp_path / "ema_ledger.sqlite"))
    try:
        name = "Sample Health Plan Medicaid of Ky"
        TC.denial_world(st, lg, tmp_path, TC.c5_rows(link=2, reason="CO-197", payer=name, posted=dt.date(2026, 6, 10)))
        items = ec.ema_denials(st.conn, today="2026-07-01")
        assert len(items) == 1, items
        it = items[0]
        ev = it["evidence"]
        start = _add(ev["first_denial_posted"], -int(ev["posting_lag_days"]))
        assert start >= codes.KRS_205_534_SINCE, start
        assert it["appeal_deadline"] == _add(start, 120), (it["claim"]["payer_group"], it["appeal_deadline"], start, it["deadline_basis"])
        det = Detector(st, run_id="t", today="2026-07-01")
        det.ema_on = True
        det.load()
        det._emit_proto(it)
        f = [x for x in det.findings.values() if x["signal"] == "EMA_DENIED"]
        assert len(f) == 1 and f[0]["appeal_deadline"] == _add(start, 120) and f[0]["legal_deadline"] == _add(start, 125), \
            (f[0]["appeal_deadline"], f[0]["legal_deadline"], start)
    finally:
        lg.close()
        st.close()


@pytest.mark.parametrize("compact", KINDS, ids=KIND_IDS)
def test_a_claim_ema_shows_a_medicaid_of_ky_plan_never_answered_runs_kentuckys_120_days_past_its_limit(tmp_path, ema_env, compact):
    """Review r2-1. EMA's never-answered check dates a claim charged on time past its first-filing limit by the payer's appeal
    window. For '<plan> Medicaid of Ky', Kentucky's managed care window (120 days), as the 837 pass gives it; it was the
    90-day default. A March 6, 2026 visit charged March 9: first-filing limit March 6, 2027 (365 days), then 120 days:
    July 4, 2027."""
    import datetime as dtm
    from remitwatch import ema_checks as ec, ema_ledger as el
    from tests import ema_fixtures as F, test_ema_checks as TC
    st = _ema_store(tmp_path, compact)
    lg = el.Ledger.open(str(tmp_path / "ema_ledger.sqlite"))
    try:
        ky = "Sample Health Plan Medicaid of Ky"
        rows = [TC.charge(F.link_no(41), svc=dtm.date(2026, 3, 6), posted=dtm.date(2026, 3, 9), status="Payment Pending", payer=ky,
                          primary=ky),
                TC.charge(F.link_no(98), cpt="99212", patient=3, payer=ky, primary=ky),
                TC.pay(F.link_no(98), "PAPER-41", 20, cpt="99212", patient=3, posted=dtm.date(2026, 4, 28), payer=ky, primary=ky),
                TC.charge(F.link_no(99), cpt="99212", patient=3, svc=dtm.date(2026, 3, 20), posted=dtm.date(2026, 5, 1))]
        TC.load(st, lg, tmp_path, rows)
        items = {i["key_parts"]: i for i in ec.unanswered_claims(st.conn, today=TC.TODAY)}
        it = items[(f"link:{F.link_no(41)}",)]
        assert it["signal"] == "NO_ANSWER", it["signal"]
        assert it["appeal_deadline"] == "2027-07-04", (it["claim"]["payer_group"], it["appeal_deadline"], it["deadline_basis"])
    finally:
        lg.close()
        st.close()


def test_an_items_denial_reasons_together_give_the_earliest_date_and_the_latest_last_day():
    """Review r2-2. An item carrying several denial reasons whose windows differ is dated by the earliest of them and kept to
    the latest: an Anthem own-plan claim denied CO-96 (the two-year dispute) and CO-50 (the clinical appeal) shows the
    clinical appeal's 180 days and keeps the dispute's two years; one reason, or reasons with one window, are as before."""
    plan = "KENTUCKY SAMPLE PPO"
    r = appeal_rule("Anthem / BCBS", "commercial", plan, carc="96", carcs=["96", "50"])
    assert (window_end("2026-06-01", r), legal_deadline("2026-06-01", r)) == ("2026-11-28", "2028-06-01"), r["label"][:120]
    assert r["days"] == 180 and "more than one denial reason" in r["label"] and "two years" in r["label"], r["label"]
    assert codes.rule_at("2026-06-01", r)["days"] == 180
    # the reasons in any spelling ('CO-50'), and the top reason left out of the list, read the same
    r2 = appeal_rule("Anthem / BCBS", "commercial", plan, carc="96", carcs=["CO-50"])
    assert (window_end("2026-06-01", r2), legal_deadline("2026-06-01", r2)) == ("2026-11-28", "2028-06-01")
    # one window for every reason: that window, unchanged
    same = appeal_rule("Anthem / BCBS", "commercial", plan, carc="96", carcs=["96", "97", "16"])
    assert same == appeal_rule("Anthem / BCBS", "commercial", plan, carc="96")
    assert appeal_rule("Anthem / BCBS", "commercial", plan, carc="50", carcs=["50"]) == appeal_rule("Anthem / BCBS", "commercial", plan, carc="50")
    # WellCare of Kentucky's Medicaid plan: a payment appeal reason (24 months) beside a clinical one (60 days, 125 by the statute)
    w = appeal_rule("WellCare / Ambetter (Centene) Medicaid", "medicaid", payer_name="Sample WellCare Medicaid of Kentucky",
                    carc="96", carcs=["96", "50"])
    assert (window_end("2026-06-01", w), legal_deadline("2026-06-01", w)) == ("2026-07-31", "2028-06-01"), w["label"][:120]
    # an item that carries no list of reasons reads as before
    assert codes.rule_facts({"carc": "50"}) == {"plan": None, "payer_name": None, "payer_id": None, "cfi": None, "carc": "50", "paid": None}


@pytest.mark.parametrize("compact", KINDS, ids=KIND_IDS)
def test_a_whole_anthem_claim_with_a_clinical_line_among_other_denials_shows_the_clinical_appeals_date(tmp_path, compact):
    """Review r2-2. Anthem's own plan (KENTUCKY SAMPLE PPO) denies a whole claim of a May 20, 2026 visit on its June 1
    remittance: two lines CO-96 (non-covered: the Claim Payment Dispute, two years) and one line CO-50 (a clinical appeal,
    180 days from the notice). The one item (CLAIM_DENIED_UNPAID) took the most frequent reason (96) and was dated June 1,
    2028: the CO-50 service's clinical appeal was shown nowhere. Its date is now November 28, 2026, and the dispute's two
    years stay its last day for the other lines."""
    from tests.test_features import claim, clean_visits
    lines = (svc("11102", "150.00", "0.00", None, "CAS*CO*96*150.00~", dos="20260520")
             + svc("11103", "150.00", "0.00", None, "CAS*CO*96*150.00~", dos="20260520")
             + svc("11104", "150.00", "0.00", None, "CAS*CO*50*150.00~", dos="20260520"))
    denied = claim(300, lines, status="4", cfi="12", plan="KENTUCKY SAMPLE PPO", dos="20260520")
    book = clean_visits(1, 25, "99214", "223.65", "117.60", cfi="12", plan="KENTUCKY SAMPLE PPO", dos="20260520") + [denied]
    got = _run(tmp_path, compact, book, "20260601", "ANTHEM", "ANTHM", today="2026-07-15")
    got.pop("_store").close()
    f = got["CLAIM_DENIED_UNPAID"]
    assert f["payer_group"] == "Anthem / BCBS" and f["carc"] == "96", (f["payer_group"], f["carc"])
    assert (f["appeal_deadline"], f["legal_deadline"]) == ("2026-11-28", "2028-06-01"), (f["appeal_deadline"], f["legal_deadline"],
                                                                                      f["deadline_basis"][:120])


@pytest.mark.parametrize("compact", KINDS, ids=KIND_IDS)
def test_an_ema_item_with_a_co55_line_beside_a_bigger_co96_line_shows_the_clinical_appeals_date(tmp_path, ema_env, compact):
    """Review r2-2. EMA's denials are one item per claim and reason class; CO-96 and CO-55 are both 'noncovered'. An Anthem
    commercial claim with a $300 CO-96 line and a $100 CO-55 (experimental) line: the item takes the reason with the most
    money (96), and was dated by the two-year dispute; the CO-55 line's clinical appeal now dates it (180 days from the
    remittance), with the two years as its last day, in EMA's check and in detection's item."""
    from remitwatch import ema_checks as ec, ema_ledger as el
    from remitwatch.detect import Detector
    from tests import ema_fixtures as F, test_ema_checks as TC
    st = _ema_store(tmp_path, compact)
    lg = el.Ledger.open(str(tmp_path / "ema_ledger.sqlite"))
    try:
        rows = (TC.c5_rows(link=2, reason="CO-96", amount=300, cpt="99213", payer=F.PAYERS["bcbs"])
                + TC.c5_rows(link=2, reason="CO-55", amount=100, cpt="11102", payer=F.PAYERS["bcbs"]))
        TC.denial_world(st, lg, tmp_path, rows)
        items = [i for i in ec.ema_denials(st.conn, today=TC.TODAY) if i["key_parts"][0] == F.link_no(2)]
        assert len(items) == 1, [(i["key_parts"], i["carc"]) for i in items]
        it = items[0]
        assert it["carc"] == "96" and {x[3] for x in it["evidence"]["lines"]} == {"CO-96", "CO-55"}, it["evidence"]["lines"]
        ev = it["evidence"]
        start = _add(ev["first_denial_posted"], -int(ev["posting_lag_days"]))
        assert it["appeal_deadline"] == _add(start, 180), (it["carc"], it["appeal_deadline"], start)
        det = Detector(st, run_id="t", today=TC.TODAY)
        det.ema_on = True
        det.load()
        det._emit_proto(it)
        f = [x for x in det.findings.values() if x["signal"] == "EMA_DENIED"]
        assert len(f) == 1 and f[0]["appeal_deadline"] == _add(start, 180), f
        assert f[0]["legal_deadline"] == window_end(start, {"months": 24}), (f[0]["legal_deadline"], start)
    finally:
        lg.close()
        st.close()


def test_anthem_level_frequency_and_length_denials_are_worked_by_the_clinical_appeals_date():
    """Review r2-4. The brief lists Anthem's clinical denials as 'CARC 50, 55, 56, 150, 151, 152': the two-year dispute as
    the date shown is 'Too long: a biller could miss the real deadline'. A level, frequency or length denial (150, 151, 152)
    may rest on a medical policy's clinical criteria or on a coding edit, and the remittance does not say which; so may the
    rest of the product's medical-necessity class (a diagnosis not covered, coverage guidelines, the records, the place of
    service, a review organization's findings). For any Anthem commercial member: 180 days is the date shown, the dispute's
    two years the last day. The Federal Employee Program reads them the same way (its 6 months only for 50, 55 and 56)."""
    for carc in ("150", "151", "152", "167", "B22", "272", "B5", "B12", "58", "216", "A1"):
        for plan in ("KENTUCKY SAMPLE PPO", "SAMPLE PPO", "ITS HOST PPO SAMPLE", None):
            r = appeal_rule("Anthem / BCBS", "commercial", plan, carc=carc)
            assert dates("2026-03-01", r) == ("2026-08-28", "2028-03-01"), (carc, plan, r["label"][:80])
        fep = appeal_rule("Anthem / BCBS", "commercial", "FEDERAL EMPLOYEE PROGRAM", carc=carc)
        assert dates("2026-03-01", fep) == ("2026-08-28", "2028-03-01"), (carc, fep["label"][:80])
        assert "clinical denial" in fep["label"] and "6 months" in fep["label"], fep["label"][:120]
    # an office visit's level (150 on an E&M code) is Anthem's coding review ('Anthem may review E&M Claims to determine, in
    # accordance with correct coding requirements and/or reimbursement policy ...'), a Claim Payment Dispute: two years, as
    # is a level-of-care change on a downcode (186); 150 on another service may be a clinical decision
    for code in ("99214", "99203", "99499"):
        r = appeal_rule("Anthem / BCBS", "commercial", "KENTUCKY SAMPLE PPO", carc="150", code=code)
        assert dates("2026-03-01", r) == ("2028-03-01", "2028-03-01") and "E&M" in r["label"], (code, r["label"][:80])
    for code in ("11102", "17311", "99199", "99500"):
        assert dates("2026-03-01", appeal_rule("Anthem / BCBS", "commercial", "KENTUCKY SAMPLE PPO", carc="150", code=code)) == \
            ("2026-08-28", "2028-03-01"), code
    assert dates("2026-03-01", appeal_rule("Anthem / BCBS", "commercial", "KENTUCKY SAMPLE PPO", carc="186")) == ("2028-03-01", "2028-03-01")
    # the item's own procedure code reaches the rules (rule_facts), so its states and story read the same window
    item = {"plan": "KENTUCKY SAMPLE PPO", "carc": "150", "procedure_code": "99214"}
    assert codes.rule_facts(item)["code"] == "99214"
    assert dates("2026-03-01", appeal_rule("Anthem / BCBS", "commercial", **codes.rule_facts(item))) == ("2028-03-01", "2028-03-01")


@pytest.mark.parametrize("compact", KINDS, ids=KIND_IDS)
def test_an_anthem_frequency_denial_item_is_dated_no_later_than_the_clinical_appeal(tmp_path, compact):
    """Review r2-4. A CO-151 (frequency) denial of a May 20, 2026 visit on Anthem's own Kentucky plan's June 1 remittance
    (DENIED_FREQUENCY): dated November 28, 2026 (the clinical appeal's 180 days, if the plan's medical policy made the
    decision), its last day June 1, 2028 (the dispute's two years, if a coding edit did). It was dated June 1, 2028."""
    freq = svc("11102", "150.00", "0.00", None, "CAS*CO*151*150.00~", dos="20260520")
    got = _run(tmp_path, compact, _book("12", "KENTUCKY SAMPLE PPO", "20260520", [freq]), "20260601", "ANTHEM", "ANTHM",
               today="2026-07-15")
    got.pop("_store").close()
    f = got["DENIED_FREQUENCY"]
    assert f["payer_group"] == "Anthem / BCBS" and f["carc"] == "151", (f["payer_group"], f["carc"])
    assert (f["appeal_deadline"], f["legal_deadline"]) == ("2026-11-28", "2028-06-01"), (f["appeal_deadline"], f["legal_deadline"])


def test_an_anthem_clinical_rule_names_its_own_process_for_the_letter_and_the_next_step():
    """Review r2-3 (codes.py's part; the letter and the next step read these words: plans/progress/fx-windows.md, Needs from
    others). The clinical appeal of Anthem's own member is not a Claim Payment Dispute; a denial whose route is not known
    names both, and the dispute's general rule names neither."""
    own = appeal_rule("Anthem / BCBS", "commercial", "KENTUCKY SAMPLE PPO", carc="50")
    assert "clinical appeal" in own["process"] and "Claim Payment Reconsideration" not in own["process"], own["process"]
    assert "one hundred eighty (180) calendar days" in own["process"] and "two years" not in own["process"], own["process"]
    assert "clinical appeal" in own["channel"] and "not as a Claim Payment Dispute" in own["channel"], own["channel"]
    for plan, carc in (("SAMPLE PPO", "50"), (None, "55"), ("ITS HOST PPO SAMPLE", "56"), ("KENTUCKY SAMPLE PPO", "151")):
        r = appeal_rule("Anthem / BCBS", "commercial", plan, carc=carc)
        assert "clinical appeal" in r["process"] and "Claim Payment Dispute" in r["process"], (plan, carc, r["process"])
        assert "BlueCard" in r["channel"], r["channel"]
    assert "process" not in appeal_rule("Anthem / BCBS", "commercial", "KENTUCKY SAMPLE PPO", carc="96")


def test_wellcare_kentucky_medicaid_second_claim_with_no_date_of_service_is_not_dated_a_year_from_the_payment():
    """Review r2-5. WellCare of Kentucky's Medicaid plan as the second payer: 12 months from the service or 6 months from the
    first payer's payment, whichever is later. With no date of service the 12 months were counted from the payment and the
    words called the payment the date of service. The service came before the payment, so the plan's limit lies between 6
    and 12 months from the payment: 6 months is the date shown, 12 the last day, and the words say the date of service is
    not known."""
    r = filing_rule("WellCare / Ambetter (Centene) Medicaid", "medicaid", payer_name="Sample WellCare Medicaid of Kentucky")
    assert r.get("cob") == {"later": {"months": 6}}, r.get("cob")
    safe, last, how = codes.second_claim_window(r, None, "2026-12-20", first_payer="Sample Health Plan")
    assert (safe, last) == ("2027-06-20", "2027-12-20"), (safe, last, how)
    assert "date of service (2026-12-20)" not in how and "date of service is not known" in how, how
    assert codes.second_claim_window(r, None, None) == (None, None, "")


def test_routing_the_va_network_by_its_payer_id_never_shortens_an_unconfirmed_last_day():
    """Review r2-8. A claim carrying the VA network's payer id gets the VA's windows; its reconsideration window is not
    confirmed (no source for the region), and 90 days stays the date shown. The same claim named after its administrator
    had the UnitedHealthcare-administered plans' 12 months (not confirmed either): the payer id must not move its last day
    earlier on a guess, so 12 months is the VA's last day."""
    start = "2026-03-01"
    by_name = appeal_rule("Optum (UHC)", "commercial", payer_name="Sample Optum Network")
    by_id = appeal_rule("Optum (UHC)", "commercial", payer_name="Sample Optum Network", payer_id="VACCN")
    assert not by_id["verified"] and not by_name["verified"], (by_id["label"][:60], by_name["label"][:60])
    assert window_end(start, by_id) == "2026-05-30", by_id["label"]
    last_name = legal_deadline(start, by_name) or window_end(start, by_name)
    last_id = legal_deadline(start, by_id) or window_end(start, by_id)
    assert last_id >= last_name == "2027-03-01", (last_id, last_name)
    assert appeal_rule("VA Community Care", "va") == by_id
