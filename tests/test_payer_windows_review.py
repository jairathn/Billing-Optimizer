"""The two reviews of the deadlines unit (2026-11-26, findings deadlines-r1-1 to r1-9 and deadlines-r2-1 to r2-6): each test
holds a window to the words of the source that binds the practice, a participating provider, and each was written to
fail before its fix.

- r1-1, r2-3: the Federal Employee Program's December 31 is the member's own deadline (its brochure speaks to the member);
  Anthem's provider manual holds a participating provider to the agreement's filing limit.
- r2-4: a provider's FEP payment dispute follows Anthem's Claim Payment Dispute (at least two years from the EOP); the
  member's 6 months stay for a clinical denial, which that process does not take.
- r2-1: WellCare's Kentucky Medicaid plan, named with 'Medcaid' in EMA and the 837s, is Medicaid with 12 months, never the
  Medicare Advantage plan's 180 days.
- r1-2: a second payer's limit runs on the clock its source states (the date of service for Medicare, CHAMPVA, TRICARE and
  GEHA; the first payer's EOP where the payer says so), with its months and its December 31.
- r1-3: WellCare of Kentucky's 24 months are for claim payment appeals (untimely filing, bundling, non-covered codes);
  documentation, authorization and medical necessity are clinical appeals of 60 days.
- r2-2, r2-6, r1-4, r1-5: Kentucky Medicaid takes no appeal (never 'appeal' in its next step; its write-offs a new claim can
  still recover are counted as such; Medicare's adjudication of a crossover adds 6 months); Indiana Medicaid wants a denied
  claim corrected and sent again within 180 days of the service before any review.
- r1-6 to r1-9, r2-5: Aetna's page covers commercial and Medicare plans only; Indiana's paid-or-not corrected claim takes the
  earlier window; UnitedHealthcare's 90 days is only the guide's example; a plan of another state says so; an item dated by
  the window before an effective date names that window.

Every claim, name, member number, payer id, amount and date is invented (tests/test_features.claim and svc,
tests/test_remitwatch.remit, tests/test_837_sends, tests/test_workable.ema, tests/test_ema_wiring, tests/test_ema_checks,
tests/ema_fixtures.py). Detection runs on the server's store file and on the compact image the serverless cycle analyses.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch import codes  # noqa: E402
from remitwatch.codes import appeal_rule, corrected_rule, corrected_window, filing_rule, legal_deadline, window_end  # noqa: E402
from tests.test_features import claim, clean_visits, svc  # noqa: E402
from tests.test_payer_windows import MODIFIER, _book, _run, dates  # noqa: E402
from tests.test_remitwatch import remit  # noqa: E402

KINDS = ["file", "compact"]
FEP = "FEDERAL EMPLOYEE PROGRAM"


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


# ===================================================================== r1-1, r2-3: the FEP follows the provider agreement
def test_an_fep_claim_is_dated_by_the_agreement_not_the_members_december_31():
    """Anthem's provider manual (2026 Kentucky v1 and v2, Indiana), 'Submission of Claims under the FEHBP and PSHBP': 'All
    Claims under the FEHBP and PSHBP must be submitted to the Plan for payment within the timeframe listed in the Provider
    or Facility Agreement'; 'Plan is not obligated to pay Claims received after the timeframe indicated in the Agreement'.
    The brochure's 'You must submit the claim by December 31 of the following year' speaks to the member. A January 5,
    2026 visit: the FEP and an Anthem PPO plan are both dated April 5, 2026 (the common 90 days, not confirmed), never
    December 31, 2027."""
    plain = window_end("2026-01-05", filing_rule("Anthem / BCBS", "commercial", plan="PPO"))
    fep = filing_rule("Anthem / BCBS", "commercial", plan=FEP)
    assert window_end("2026-01-05", fep) == plain == "2026-04-05", (fep["label"], fep["verified"])
    assert legal_deadline("2026-01-05", fep) is None and not fep["verified"]
    c = corrected_rule("Anthem / BCBS", "commercial", plan=FEP)
    assert c is None or window_end("2026-01-05", c) <= plain, c


@pytest.mark.parametrize("compact", [False, True], ids=["store_file", "compact_image"])
def test_an_fep_corrected_claim_item_keeps_the_agreement_warning_and_no_member_deadline(tmp_path, compact):
    """An FEP claim missing a modifier (CO-16, N822) of a May 20, 2026 visit, on a June 1 remittance: the corrected claim
    goes within the agreement's filing limit from the date of service, 90 days (August 18, 2026), and the item says the
    agreement's limit applies; it never shows the member's December 31, 2027."""
    got = _run(tmp_path, compact, _book("12", FEP, "20260520", [MODIFIER]), "20260601", "ANTHEM", "ANTHM")
    got.pop("_store").close()
    f = got["DENIED_MISSING_MODIFIER"]
    assert f["appeal_deadline"] == "2026-08-18", (f["appeal_deadline"], f["legal_deadline"], f["deadline_basis"])
    assert "participation agreement" in f["deadline_basis"], f["deadline_basis"]
    assert "December 31 of the following year" not in f["deadline_basis"], f["deadline_basis"]


# ===================================================================== r2-4: the FEP's provider dispute
def test_an_fep_underpayment_is_not_expired_on_the_members_6_months():
    """Anthem's Claim Payment Dispute ('Contractual payment issues', 'Disagreements over reduced or zero-paid Claims'):
    'Providers and Facilities have a minimum of two (2) years from the issue date of the EOP', with no FEP exception. The
    member's 6 months (5 CFR 890.105(b)(1)) is not the provider's last day: an underpayment paid December 1, 2025 stays
    workable to December 1, 2027."""
    from remitwatch import states
    f = {"finding_id": "F1", "signal": "UNDERPAID_VS_OWN_RATE", "category": "underpayment", "actionable": "appeal",
         "payer_group": "Anthem / BCBS", "payer_kind": "commercial", "plan": FEP, "payment_date": "2025-12-01",
         "service_date": "2025-11-10", "appeal_deadline": "2026-05-30", "legal_deadline": "2026-06-01", "paid_cents": 5000}
    assert max(w["date"] for w in states.windows(f)) == "2027-12-01", states.windows(f)


def test_an_fep_clinical_denial_keeps_the_members_6_months():
    """The same manual: the Claim Payment Dispute 'does not apply to appeals regarding a clinical decision denial, such as
    ... a Claim that has been denied as not medically necessary or experimental/investigational'; the FEP's disputed claims
    process takes those within 6 months of the notice (5 CFR 890.105(b)(1)), filed for the member with written consent."""
    for carc in ("50", "55", "56", "150", "151", "152"):
        assert dates("2026-03-01", appeal_rule("Anthem / BCBS", "commercial", FEP, carc=carc)) == ("2026-08-28", "2026-09-01"), carc
    for carc in ("45", "97", "29", None):
        assert dates("2026-03-01", appeal_rule("Anthem / BCBS", "commercial", FEP, carc=carc)) == ("2026-08-28", "2028-03-01"), carc


# ===================================================================== r2-1: WellCare's Kentucky Medicaid plan, misspelt
WK_NAME = "WellCare Sample Medcaid of Kentucky"      # the shape of the name EMA and the 837s carry: 'Medcaid'


def test_wellcare_kentucky_medicaid_named_without_the_word_medicaid_is_medicaid_with_12_months():
    """WellCare of Kentucky's 2025-2026 Medicaid manual: claims 'within 12 months ... from the date of service'. The name
    rules read 'Medcaid' as Medicaid: the plan is WellCare's Medicaid group (its 835s' group), so its own 12 months apply,
    never the Wellcare Medicare Advantage plan's 180 days. Medicaid and WellCare's Medicare plans keep their groups."""
    grp, kind = codes.normalize_payer(WK_NAME)
    assert (grp, kind) == ("WellCare / Ambetter (Centene) Medicaid", "medicaid")
    r = filing_rule(grp, kind, payer_name=WK_NAME, payer_id="99141")
    assert window_end("2025-12-10", r) == "2026-12-10", (grp, kind, r["label"])
    assert codes.normalize_payer("WELLCARE SAMPLE MEDICAID OF KENTUCKY") == ("WellCare / Ambetter (Centene) Medicaid", "medicaid")
    assert codes.normalize_payer("SAMPLE KY MEDCAID") == ("Medicaid KY", "medicaid")
    assert codes.normalize_payer("Wellcare Sample Medicare") == ("WellCare / Ambetter (Centene)", "medicare_advantage")
    assert codes.normalize_payer("Ambetter Sample Wellcare of Kentucky") == ("Ambetter (Centene)", "commercial")
    assert codes.payer_group_kind("WELLCARE SAMPLE OF KENTUCKY", "MC") == ("WellCare / Ambetter (Centene) Medicaid", "medicaid")


@pytest.mark.parametrize("kind", KINDS)
def test_a_claim_to_wellcare_kentucky_medicaid_is_not_expired_by_the_medicare_advantage_180_days(tmp_path, kind):
    """EMA's exports list a claim to WellCare's Kentucky Medicaid plan (visit December 10, 2025) rejected before it reached
    the payer. Its own limit, 12 months from the service, runs to December 10, 2026: on November 20, 2026 it can still be
    sent (it was dated June 8, 2026 by the Medicare Advantage plan's 180 days, and expired)."""
    from tests.test_837_sends import _chased, _remits, _store, _visits
    from tests.test_workable import ema
    s = _store(tmp_path, kind)
    _remits(s, tmp_path, _visits(), "20261110")
    ema(s.conn, "CB0000WK1C017", payer=WK_NAME, payer_id="99141", service_date="2025-12-10", bill_created="2025-12-11")
    ema(s.conn, "CB0000WK1C017", family="claim_status", claim_status="Rejected", payer=WK_NAME, payer_id="99141",
        service_date="2025-12-10")
    s.conn.commit()
    f = _chased(s, "t1", today="2026-11-20")["CB0000WK1C017"]
    assert max(x for x in (f["appeal_deadline"], f.get("legal_deadline")) if x) >= "2026-12-10", (f["payer_group"], f["deadline_basis"])
    assert f["work_state"] == "work_now", f["state_basis"]
    s.close()


# ===================================================================== r1-2: a second payer's own clock
def _second_payer_run(tmp_path, compact: bool, *, visit: str, paid_on: str, today: str, sec: str):
    """Blue Cross paid a visit on `paid_on` and left $30 of coinsurance; EMA shows `sec` on file as the second payer, never
    billed. Returns (the run's SECONDARY_NOT_BILLED items, its EMA counts)."""
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
        v = dt.date.fromisoformat(visit)
        posted = dt.date.fromisoformat(paid_on) + dt.timedelta(days=2)
        W.remit(st, trace="777000941", paid_on=paid_on,
                claims=[{"pcn": F.claim_id(41), "last": "SAMPLE", "first": "FOXTROT",
                         "lines": [(v.isoformat(), "99213", 150, 90, [("CO", "45", 30), ("PR", "2", 30)], {"allowed_cents": 12000})]}])
        rows = [W.charge(F.link_no(41), patient=2, svc=v, posted=v, secondary=sec),
                W.pay(F.link_no(41), "777000941", 90, patient=2, svc=v, posted=posted, secondary=sec),
                W.adj(F.link_no(41), "CO-45", 30, patient=2, svc=v, posted=posted, contractual=True, secondary=sec)]
        ar = [F.ar_row(claim=F.claim_id(41), link=F.link_no(41), status="Payment Pending", current=30, aging=posted, svc=v,
                       patient=2, secondary=sec)]
        W.denial_world(st, lg, tmp_path, rows, ar_rows=ar)
        det = Detector(st, run_id="r1snb", today=today)
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
def test_geha_as_second_payer_is_work_until_december_31_of_the_year_after_the_visit(tmp_path, ema_env, compact):
    """Visit February 3, 2025; Blue Cross paid March 3, 2025; GEHA on file as the second payer. Its brochure: 'by December 31
    of the year after the year you received the service': on May 7, 2026 the $30 share can still be billed (it was
    counted from Blue Cross's remittance by 365 days and dropped as a loss)."""
    got, counts = _second_payer_run(tmp_path, compact, visit="2025-02-03", paid_on="2025-03-03", today="2026-05-07", sec="GEHA")
    assert not counts.get("snb_past_limit"), counts
    assert len(got) == 1, (got, counts)
    assert max(d for d in (got[0]["appeal_deadline"], got[0]["legal_deadline"]) if d) == "2026-12-31", got[0]["deadline_basis"]
    assert "counted from the date of service" in got[0]["deadline_basis"], got[0]["deadline_basis"]


@pytest.mark.parametrize("compact", [False, True], ids=["store_file", "compact_image"])
def test_medicare_as_second_payer_is_dated_by_its_year_from_the_date_of_service(tmp_path, ema_env, compact):
    """Visit Monday March 3, 2025; Blue Cross paid May 20, 2025; Medicare on file as the second payer, never billed. 42 CFR
    424.44(a)(1): 'no later than the close of the period ending 1 calendar year after the date of service' (March 3, 2026,
    a Tuesday); a first payer's delay is not one of 424.44(b)'s exceptions. On April 15, 2026 the share is past Medicare's
    limit and a month's grace: a loss, counted with its dollars, never work dated May 20, 2026."""
    got, counts = _second_payer_run(tmp_path, compact, visit="2025-03-03", paid_on="2025-05-20", today="2026-04-15",
                                    sec="Medicare of Kentucky")
    for f in got:
        assert max(d for d in (f["appeal_deadline"], f["legal_deadline"]) if d) <= "2026-03-03", f["deadline_basis"]
    assert got == [] and counts.get("snb_past_limit") == 1 and counts.get("snb_past_limit_cents") == 3000, (got, counts)


def test_a_second_payers_clock_is_the_one_its_source_states():
    """codes.second_claim_window: Medicare, CHAMPVA, TRICARE and GEHA from the date of service; Ambetter (its coordination of
    benefits: 'calculated from the date of Explanation of Payment from the primary payers') from the first payer's
    remittance; a payer whose clock for a second claim is not known: the safe date from the service, the last day from the
    remittance, so a guess never makes the share a loss early."""
    svc_, paid = "2025-12-15", "2026-01-20"
    assert codes.second_claim_window(filing_rule("Medicare KY (CGS J15)", "medicare"), svc_, paid)[:2] == ("2026-12-15", None)
    assert codes.second_claim_window(filing_rule("Champva - Hac", "other"), svc_, paid)[:2] == ("2026-12-15", None)
    t = filing_rule("Tricare", "tricare")
    assert t["verified"] and "199.7(d)" in t["label"] and codes.second_claim_window(t, svc_, paid)[:2] == ("2026-12-15", None)
    assert codes.second_claim_window(filing_rule("Geha", "other"), svc_, paid)[:2] == ("2026-12-31", None)
    assert codes.second_claim_window(filing_rule("Ambetter (Centene)", "commercial"), svc_, paid)[:2] == ("2026-04-20", "2026-07-19")
    safe, last, how = codes.second_claim_window(filing_rule("Sample Health Plan", "commercial"), svc_, paid, first_payer="Anthem / BCBS")
    assert (safe, last) == ("2026-03-15", "2026-04-20") and "Anthem / BCBS's remittance" in how and "not known" in how


# ===================================================================== r1-3: WellCare of Kentucky's two appeal clocks
WC_KY = ("WellCare / Ambetter (Centene) Medicaid", "medicaid")
WHO = {"payer_name": "WELLCARE SAMPLE PLAN OF KENTUCKY"}


def test_wellcare_ky_documentation_denial_is_a_clinical_appeal_not_a_24_month_claim_payment_appeal():
    """WellCare of Kentucky's 2026 Medicaid Quick Reference Guide, CLINICAL APPEALS: 'within 60 calendar days of a claims
    denial for lack of prior authorization, services exceeding the authorization, insufficient documentation or late
    notification'. CARC 252 (an attachment or documentation required), 226 (information requested insufficient), 251, B12
    and 210 (late notification) are clinical appeals: 60 days safe, 125 days the last day (KRS 205.534's 120 from receipt)."""
    for carc in ("252", "226", "251", "B12", "210", "50", "197", "999", None):
        r = appeal_rule(*WC_KY, carc=carc, **WHO)
        assert dates("2026-06-01", r) == ("2026-07-31", "2026-10-04"), (carc, r["label"][:80])


def test_wellcare_ky_non_covered_and_bundled_codes_are_claim_payment_appeals_of_24_months():
    """The same guide, CLAIM PAYMENT APPEALS: 'claim denials for issues related to untimely filing, incidental procedures,
    unlisted procedure codes and non-covered codes ... within 24 months of the date on the EOP'. CARC 167 (a diagnosis not
    covered) is a non-covered code, not a clinical denial."""
    for carc in ("167", "96", "204", "97", "B15", "234", "236", "29"):
        r = appeal_rule(*WC_KY, carc=carc, **WHO)
        assert dates("2026-06-01", r) == ("2028-06-01", "2028-06-01"), (carc, r["label"][:80])


# ===================================================================== r2-2: Kentucky Medicaid is never told to appeal
@pytest.mark.parametrize("compact", [False, True], ids=["store_file", "compact_image"])
def test_a_kentucky_medicaid_whole_claim_denial_is_not_told_to_appeal(tmp_path, compact):
    """Kentucky Medicaid (fee-for-service) takes no appeal of an ordinary claim denial (907 KAR 1:671). A whole claim it
    denied (CO-96) is a decision between a corrected or new claim within 12 months of the service and letting it go: its
    next step never says 'appeal', and it says why."""
    from remitwatch import story
    denied = claim(300, "CAS*CO*96*223.65~" + svc("99214", "223.65", "0.00", None, dos="20260520"),
                   status="4", cfi="MC", plan="", dos="20260520")
    book = clean_visits(1, 25, "99214", "223.65", "117.60", cfi="MC", plan="", dos="20260520") + [denied]
    got = _run(tmp_path, compact, book, "20260601", "KY MEDICAID", "00661", today="2026-11-20")
    store = got.pop("_store")
    f = got.get("CLAIM_DENIED_UNPAID")
    assert f is not None, sorted(got)
    assert f["payer_group"] == "Medicaid KY" and f["work_state"] == "work_now", (f["payer_group"], f["state_basis"])
    nxt = story.next_step(store.conn, f["finding_id"], today="2026-11-20")
    words = " ".join([str(nxt.get("action") or "")] + [str(x) for x in nxt.get("steps") or []]).lower()
    assert "appeal if" not in words and "appeal it" not in words and "appeal with" not in words, (nxt.get("route"), nxt.get("action"))
    assert "907 kar 1:671" in words and "corrected or new claim" in words, (nxt.get("route"), nxt.get("action"), nxt.get("steps"))
    store.close()


def test_every_decision_step_for_a_kentucky_medicaid_item_offers_a_new_claim_not_an_appeal():
    """story's decision steps, worded for a payer that takes no appeal: the generic reason step, a code changed, a
    non-covered service, a timely-filing denial and a person's earlier decision."""
    from remitwatch import story
    texts = ["Read the reason (CO-96): fix and resubmit if it names an error, appeal if the payer is wrong, let it go with the reason "
             "if the payer is right.", "Compare the note with both codes: if the code billed was right, appeal; if the payer's code was, "
             "let it go.", "Covered after all, or another plan is primary: send it to the right payer, or appeal with the coverage rule.",
             "A timely-filing denial: appeal only with proof it reached the payer in time.", "If that decision stands, Let go with that "
             "reason; if not, appeal by Dec 1, 2026.", "If it pays this service, close it as Paid; if not, appeal it."]
    for t in texts:
        out = story._no_appeal_words(t).lower()
        assert " appeal" not in out.replace("no appeal", ""), out


# ===================================================================== r2-6: Indiana Medicaid's corrected claim comes first
@pytest.mark.parametrize("compact", [False, True], ids=["store_file", "compact_image"])
def test_an_indiana_medicaid_npi_denial_stays_workable_while_its_corrected_claim_can_go(tmp_path, compact):
    """IHCP Claim Administrative Review and Appeals (March 24, 2026), 'For Claim Denials': 'If the claim denial is due to a
    provider's incorrect or inaccurate claim information, the provider should make applicable corrections and resubmit the
    claim ... considered to be an initial claim', within 180 days of the service; an administrative review comes after. A
    whole claim denied CO-208 (NPI not matched) on August 24, 2026 for an August 1 visit can still be corrected and sent
    until January 28, 2027: on November 1 it is work, past its 60-day review (it was expired on October 23)."""
    denied = claim(300, "CAS*CO*208*223.65~" + svc("99214", "223.65", "0.00", None, dos="20260801"),
                   status="4", cfi="MC", plan="", dos="20260801")
    book = clean_visits(1, 25, "99214", "223.65", "117.60", cfi="MC", plan="", dos="20260801") + [denied]
    got = _run(tmp_path, compact, book, "20260824", "IN MEDICAID", "IHCP1", today="2026-11-01")
    store = got.pop("_store")
    f = got.get("CLAIM_DENIED_UNPAID")
    assert f is not None, sorted(got)
    assert f["payer_group"] == "Medicaid IN" and f["appeal_deadline"] == "2026-10-23", (f["payer_group"], f["appeal_deadline"])
    assert f["work_state"] == "work_now", (f["appeal_deadline"], f["state_basis"])
    assert "2027" in f["state_basis"] and "corrected" in f["state_basis"].lower(), f["state_basis"]
    store.close()


# ===================================================================== r1-4: write-offs a new claim can still recover
@pytest.mark.parametrize("compact", [False, True], ids=["store_file", "compact_image"])
def test_kentucky_medicaid_write_offs_a_new_claim_can_still_recover_count_as_inside_the_window(tmp_path, ema_env, compact):
    """The owner's rule 2 counts money written off 'with what is still recoverable'. Ten Kentucky Medicaid denials (CO-16)
    of March 2, 2026 visits, written off as contractual on March 31: on May 3 a corrected or new claim can still go until
    March 2, 2027 (907 KAR 1:673): all $1,100 is inside the window (the department's 30-day dispute is not its window)."""
    from remitwatch import ema_checks as ec, ema_ledger as el, ema_reports as er
    from remitwatch.store import Store
    from tests import ema_fixtures as F, test_ema_checks as TC
    st = Store(":memory:", compact=True) if compact else Store(str(tmp_path / "store.sqlite"))
    er.ensure_schema(st.conn)
    st.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('ema:key_salt', ?)", (TC.SALT_HEX,))
    st.conn.commit()
    lg = el.Ledger.open(str(tmp_path / "ema_ledger.sqlite"))
    try:
        ky = "Kentucky Medicaid"
        rows = []
        for i in range(10):
            rows += [TC.charge(F.link_no(10 + i), cpt="11102", amount=110, patient=i % 4, payer=ky, primary=ky),
                     TC.adj(F.link_no(10 + i), "CO-16", 110, cpt="11102", patient=i % 4, contractual=True, payer=ky, primary=ky)]
        TC.denial_world(st, lg, tmp_path, rows)
        items = [i for i in ec.denial_as_contractual(st.conn, today=TC.TODAY) if i["key_parts"][0] == "Medicaid KY"]
        assert len(items) == 1, [i["key_parts"] for i in ec.denial_as_contractual(st.conn, today=TC.TODAY)]
        ev = items[0]["evidence"]
        assert ev["written_off_cents"] == 110000 and ev["inside_window_cents"] == 110000, ev
    finally:
        lg.close()
        st.close()


# ===================================================================== r1-5: a Kentucky Medicaid crossover's 6 months
@pytest.mark.parametrize("compact", [False, True], ids=["store_file", "compact_image"])
def test_kentucky_medicaid_denial_of_a_crossover_runs_six_months_from_medicares_adjudication(tmp_path, compact):
    """907 KAR 3:005 Section 1, timely filing: 'Within six (6) months of the Medicare adjudication date if the service was
    billed to Medicare'. Visit June 10, 2025; Medicare answered it on January 15, 2026 (a denial, CO-96); Kentucky Medicaid
    denied it February 10, 2026 (CO-50). On July 1, 2026 the Medicaid item is still work: its last day is July 15, 2026
    (it was dated June 10, 2026 by the 12 months alone, and expired)."""
    from remitwatch.detect import Detector, ensure_schema
    from remitwatch.store import Store
    s = Store(":memory:", compact=True) if compact else Store(str(tmp_path / "x.sqlite"))
    dos = "20250610"

    def ingest(tag, claims, *, payer, payer_id, pay, ctrl):
        p = tmp_path / f"{tag}{int(compact)}.RMT"
        p.write_bytes(remit("".join(claims), "100.00", payer=payer, payer_id=payer_id, seg_count=99, isa_ctrl=f"{ctrl:09d}", pay_date=pay))
        s.ingest_835_paths([str(p)])
    mc = clean_visits(1, 25, "99214", "223.65", "117.60", cfi="MB", plan="", dos=dos)
    mc.append(claim(400, svc("11102", "150.00", "0.00", None, "CAS*CO*96*150.00~", dos=dos), cfi="MB", plan="", dos=dos))
    ingest("mc", mc[:20], payer="KY MEDICARE", payer_id="15102", pay="20260115", ctrl=1)
    ingest("mcb", mc[20:], payer="KY MEDICARE", payer_id="15102", pay="20260115", ctrl=2)
    md = [claim(400, svc("11102", "150.00", "0.00", None, "CAS*CO*50*150.00~", dos=dos), cfi="MC", plan="", dos=dos)]
    ingest("md", md, payer="KY MEDICAID", payer_id="00661", pay="20260210", ctrl=3)
    ensure_schema(s.conn)
    Detector(s, run_id="r1x", today="2026-07-01").run()
    rows = [dict(r) for r in s.query("SELECT * FROM findings WHERE COALESCE(stale, 0) = 0 AND payer_group = 'Medicaid KY'")]
    s.close()
    assert rows, "no Kentucky Medicaid item for the denied crossover"
    for f in rows:
        assert (f["appeal_deadline"], f["legal_deadline"]) == ("2026-06-10", "2026-07-15"), (f["signal"], f["deadline_basis"])
        assert "907 KAR 3:005" in f["deadline_basis"] and f["work_state"] != "expired", (f["work_state"], f["state_basis"])


# ===================================================================== r1-6 to r1-9, r2-5
def test_aetna_window_is_not_confirmed_for_plans_the_aetna_dispute_page_does_not_cover():
    """Aetna's 'Disputes and appeals overview' (180 calendar days) covers its commercial and Medicare plans. Its supplement
    group and its Medicaid plans (Aetna Better Health) are not on it: their windows are not confirmed. An Aetna Medicaid
    plan whose remittance names no state keeps the Medicaid default as its safe date and Aetna's 180 days as its last
    day; one that names Kentucky gets the state's managed care statute."""
    assert appeal_rule("Aetna", "commercial")["verified"] and appeal_rule("Aetna Medicare Advantage", "medicare_advantage")["verified"]
    s = appeal_rule("Aetna Senior Supplemental (American Continental)", "supplement")
    assert not s["verified"] and dates("2026-06-01", s) == ("2026-11-28", None), s["label"][:100]
    m = appeal_rule("Aetna Medicaid", "medicaid")
    assert not m["verified"] and dates("2026-06-01", m) == ("2026-08-30", "2026-11-28"), m["label"][:100]
    k = appeal_rule("Aetna Medicaid", "medicaid", payer_name="AETNA SAMPLE HEALTH OF KENTUCKY")
    assert "KRS 205.534" in k["label"]


def test_indiana_medicaid_corrected_claim_with_paid_unknown_takes_the_earlier_window():
    """IHCP: a denied claim corrected is a new claim within 180 days of the service; a paid claim's adjustment 'within 60
    days of notification of the claim's disposition'. Where the remittance does not say which, the earlier of the two is
    used: a late remittance (visit January 10, RA June 10, 2026) is dated July 9 (180 days), an early one (RA January 31)
    April 1 (60 days from the RA). codes.corrected_window is the clock every caller dates a corrected claim by."""
    unknown = corrected_rule("Medicaid IN", "medicaid")
    assert corrected_window(unknown, "2026-01-10", "2026-06-10") == ("2026-07-09", None)
    assert corrected_window(unknown, "2026-01-10", "2026-01-31") == ("2026-04-01", None)
    assert "earlier of the two" in unknown["label"]
    assert corrected_window(corrected_rule("Medicaid IN", "medicaid", paid=False), "2026-01-10", "2026-06-10") == ("2026-07-09", None)
    assert corrected_window(corrected_rule("Medicaid IN", "medicaid", paid=True), "2026-01-10", "2026-06-10") == ("2026-08-09", None)


def test_unitedhealthcare_corrected_claim_length_is_not_confirmed():
    """2026 UHC Administrative Guide, ch. 10: the agreement's limit from the date of service 'also applies to submission of
    corrected claims'; its 90 days is the guide's own example ('For example, if ... the timely filing limit in your
    participation agreement is 90 days'), not the practice's number."""
    r = corrected_rule("UnitedHealthcare", "commercial")
    assert r["days"] == 90 and r["clock"] == "dos" and not r["verified"], r["label"][:100]


def test_anthem_medicaid_plan_of_another_state_is_not_said_to_be_unnamed():
    """A remittance whose plan names Virginia or Ohio does say which plan it is: neither of the two the rules know. Its item
    says so, and that Indiana's shorter windows are used; WEST VIRGINIA is not read as Virginia."""
    assert codes.plan_state("WEST VIRGINIA SAMPLE PLAN") == "WV" and codes.plan_state("VIRGINIA SAMPLE MEDICAID") == "VA"
    assert codes.plan_state("SAMPLE PLAN", "ANTHEM SAMPLE OF OHIO") == "OH" and codes.plan_state("IN SAMPLE PLAN") == "IN"
    for plan in ("VIRGINIA SAMPLE MEDICAID", "OHIO SAMPLE GROUP PLAN"):
        a = appeal_rule("Anthem / BCBS Medicaid", "medicaid", plan)
        f = filing_rule("Anthem / BCBS Medicaid", "medicaid", plan=plan)
        for r in (a, f):
            assert "does not say whether this is Anthem's Kentucky or its Indiana" not in r["label"], (plan, r["label"][:110])
            assert "another state" in r["label"] and "Indiana" in r["label"] and not r["verified"], (plan, r["label"][:110])
        assert window_end("2026-06-01", a) == "2026-07-31" and window_end("2026-06-01", f) == "2026-08-30"


@pytest.mark.parametrize("compact", [False, True], ids=["store_file", "compact_image"])
def test_a_kentucky_managed_care_denial_before_april_14_says_the_window_it_was_dated_by(tmp_path, compact):
    """KRS 205.534's 120 days apply to a denial from April 14, 2026. A Kentucky Medicaid managed care plan's denial on a
    March 2, 2026 remittance is dated by the window before it, 90 days (May 31, 2026), and names that window, never 'The
    date shown is 120 days from the remittance'."""
    line = svc("11102", "150.00", "0.00", None, "CAS*CO*50*150.00~", dos="20260220")
    book = clean_visits(1, 25, "99214", "223.65", "117.60", cfi="MC", plan="", dos="20260220") + [
        claim(300, line, cfi="MC", plan="", dos="20260220")]
    got = _run(tmp_path, compact, book, "20260302", "SAMPLE BETTER HEALTH OF KENTUCKY", "99128", today="2026-04-01")
    got.pop("_store").close()
    f = got["DENIED_MEDICAL_NECESSITY"]
    assert f["appeal_deadline"] == "2026-05-31", (f["payer_group"], f["appeal_deadline"])
    assert "The date shown is 120 days from the remittance" not in f["deadline_basis"], f["deadline_basis"]
    assert "before April 14, 2026" in f["deadline_basis"], f["deadline_basis"]


def test_rule_at_names_the_window_an_earlier_start_was_dated_by():
    r = appeal_rule("Aetna Medicaid", "medicaid", payer_name="AETNA SAMPLE HEALTH OF KENTUCKY")
    assert codes.rule_at("2026-04-13", r)["days"] == 90 and "before April 14, 2026" in codes.rule_at("2026-04-13", r)["label"]
    assert codes.rule_at("2026-04-14", r) is r and codes.rule_at(None, r) is r


# ===================================================================== r2-2 and r2-5, the words where they had no test
@pytest.mark.parametrize("compact", [False, True], ids=["store_file", "compact_image"])
def test_a_kentucky_medicaid_denial_only_ema_saw_is_never_told_to_appeal(tmp_path, ema_env, compact):
    """EMA posted a Kentucky Medicaid denial (CO-50) with no remittance on file: get the EOB, then send a corrected or new
    claim; never 'appeal with the record' (907 KAR 1:671)."""
    from remitwatch import ema_ledger as el, ema_reports as er, story
    from remitwatch.detect import Detector
    from remitwatch.store import Store
    from tests import ema_fixtures as F, test_ema_wiring as W
    ky = "Kentucky Medicaid"
    st = Store(":memory:", compact=True) if compact else Store(str(tmp_path / "store.sqlite"))
    er.ensure_schema(st.conn)
    st.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('ema:key_salt', ?)", (W.SALT_HEX,))
    st.conn.commit()
    lg = el.Ledger.open(str(tmp_path / "ema_ledger.sqlite"))
    try:
        rows = [W.charge(F.link_no(2), patient=1, payer=ky, primary=ky),
                W.adj(F.link_no(2), "CO-50", 150, patient=1, posted=dt.date(2026, 4, 10), payer=ky, primary=ky)]
        ar = [F.ar_row(claim=F.claim_id(2), link=F.link_no(2), status="Denied", denial=150, current=0, aging=dt.date(2026, 4, 25),
                       svc=W.SVC, patient=1, primary=ky)]
        W.denial_world(st, lg, tmp_path, rows, ar_rows=ar)
        det = Detector(st, run_id="kyema", today=W.TODAY)
        det.run()
        assert det.ema_errors == [], det.ema_errors
        e = W.one(st, "signal='EMA_DENIED'")
        assert e["payer_group"] == "Medicaid KY" and e["work_state"] == "work_now", (e["payer_group"], e["state_basis"])
        nxt = story.next_step(st.conn, e["finding_id"], today=W.TODAY)
        words = " ".join([str(nxt.get("action") or "")] + [str(x) for x in nxt.get("steps") or []]).lower()
        assert "appeal" not in words.replace("no appeal", ""), (nxt.get("route"), nxt.get("action"), nxt.get("steps"))
        assert "907 kar 1:671" in words and "corrected" in words, (nxt.get("action"), nxt.get("steps"))
    finally:
        lg.close()
        st.close()


def test_a_no_appeal_payers_window_is_a_corrected_claims_in_every_route():
    """story._window: a payer that takes no appeal has a corrected or new claim's window, on the rate route too."""
    from remitwatch import story
    for route in ("appeal", "rate"):
        w = story._window("2026-12-01", "2026-11-01", None, route, no_appeal=True)
        assert "corrected claim" in w and "appeal" not in w, (route, w)
        assert "appeal" in story._window("2026-12-01", "2026-11-01", None, route), route


def test_the_window_an_item_is_open_under_names_the_rule_it_was_counted_by():
    """states.windows: a Kentucky managed care denial on a March 2, 2026 remittance is open under the plan's window before
    April 14 (90 days), and its basis says so; one on May 4, 2026 is open under KRS 205.534's 120 days."""
    from remitwatch import states
    base = {"finding_id": "F1", "signal": "DENIED_MEDICAL_NECESSITY", "category": "denial", "actionable": "appeal", "carc": "50",
            "payer_group": "Aetna Medicaid", "payer_kind": "medicaid", "payer_name": "AETNA SAMPLE HEALTH OF KENTUCKY",
            "service_date": "2026-02-20", "paid_cents": 0}
    early = [w for w in states.windows(dict(base, payment_date="2026-03-02"), "2026-05-31", "x") if w["what"] == "the payer's dispute window"]
    assert early and early[0]["date"] == "2026-05-31" and "before April 14, 2026" in early[0]["basis"], early
    late = [w for w in states.windows(dict(base, payment_date="2026-05-04"), "2026-09-01", "x") if w["what"] == "the payer's dispute window"]
    assert late and late[0]["date"] == "2026-09-06" and "KRS 205.534(1)(d)1" in late[0]["basis"], late
