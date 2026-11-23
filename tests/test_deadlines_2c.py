"""Deadlines counted the way the rules state them, and follow-ups while the remittances are behind. All data invented.

- Medicare Part B: a redetermination is due 120 days after receipt of the remittance, and receipt is presumed 5 days
  after its date (42 CFR 405.942(a)), so 125 days from the remittance date.
- Windows stated in months or years (Humana's 18 months, UnitedHealthcare's 12 months, Anthem's two years, Medicare's
  calendar year to file) are counted in calendar months: a window that crosses February 29 keeps its calendar length.
- While the remittances are behind, the daily run sends no request that says a claim is unpaid; a person who checked in
  EMA that it is still unpaid can send it, and the item's history says so."""
from __future__ import annotations

from remitwatch.codes import appeal_rule, corrected_rule, filing_rule, window_end
from tests.test_fax import PAYER_FAX, XHR, _age, _number, _send, site  # noqa: F401  (site is a fixture)


def test_medicare_counts_the_five_days_the_notice_is_presumed_to_take():
    r = appeal_rule("Medicare KY (CGS J15)", "medicare")
    assert window_end("2026-10-01", r) == "2027-02-03"             # 5 + 120 days
    assert "presumed 5 days" in r["label"] and r["days"] == 120
    fix = corrected_rule("Medicare KY (CGS J15)", "medicare", None, "DENIED_MISSING_INFO", "16")
    assert window_end("2026-10-01", fix) == "2027-02-03"           # a redetermination with the records, same clock
    # a reopening is counted from the determination itself, a calendar year
    reopen = corrected_rule("Medicare KY (CGS J15)", "medicare", None, "DENIED_MISSING_MODIFIER", "4")
    assert window_end("2027-03-01", reopen) == "2028-03-01"
    # Medicare Advantage and commercial plans presume nothing
    assert window_end("2026-10-01", appeal_rule("Aetna", "commercial")) == "2027-03-30"


def test_months_and_years_are_counted_on_the_calendar():
    assert window_end("2026-10-01", appeal_rule("Humana", "commercial")) == "2028-04-01"           # 18 months, not 540 days
    assert window_end("2027-03-01", appeal_rule("UnitedHealthcare", "commercial")) == "2028-03-01"  # 12 months across Feb 29
    assert window_end("2026-03-01", appeal_rule("Anthem / BCBS", "commercial")) == "2028-03-01"     # two years across Feb 29
    assert window_end("2027-02-28", filing_rule("Medicare KY (CGS J15)", "medicare")) == "2028-02-28"
    assert window_end("2028-02-29", filing_rule("Medicare KY (CGS J15)", "medicare")) == "2029-02-28"
    assert window_end("2026-08-31", {"months": 6, "days": 0}) == "2027-02-28"                       # a month-end start
    assert window_end("2026-10-01", filing_rule("Some Plan", "commercial")) == "2026-12-30"          # 90 days stays days
    assert window_end(None, appeal_rule("Humana", "commercial")) is None and window_end("2026-10-01", None) is None
    assert window_end("not a date", appeal_rule("Humana", "commercial")) is None


def test_a_medicare_item_is_dated_125_days_from_its_remittance(tmp_path):
    from remitwatch.detect import Detector
    from tests.test_features import build_store, claim, clean_visits, svc
    denied = claim(301, svc("99214", "223.65", "0.00", None, "CAS*CO*50*223.65~", dos="20261001"), dos="20261001")
    s = build_store(tmp_path, clean_visits(1, 25, "99214", "223.65", "90.00") + [denied], pay_date="20261010", payer="KY MEDICARE")
    Detector(s, run_id="t").run()
    (d,) = [r[0] for r in s.conn.execute("SELECT appeal_deadline FROM findings WHERE patient_control_number='CB0000301C017'")]
    assert d == "2027-02-12"                                            # October 10 + 5 + 120
    s.close()


def test_a_person_can_send_a_follow_up_the_old_remittances_hold(site, monkeypatch):
    import remitwatch.api as api
    c, line = site.c, site.line
    rate = site.ids["UNDERPAID_VS_OWN_RATE"]
    _number(c, PAYER_FAX)
    assert _send(c, rate)["status"] == "sent"
    line.deliver_all()
    c.post("/api/faxes/refresh", headers=XHR)
    _age(site.db, 40)
    monkeypatch.setattr(api, "STALE_APPEAL_DAYS", 3)
    # the daily run and the page's "send them now" hold it, saying a person can send it
    r = c.post("/api/faxes/follow_up", json={}, headers=XHR).json()
    assert r["sent"] == 0 and r["results"][0]["code"] == "stale" and "Still unpaid in EMA" in r["results"][0]["reason"]
    # "still unpaid" said of everything due counts for nothing: it must name the items
    assert c.post("/api/faxes/follow_up", json={"stale_ok": True}, headers=XHR).json()["sent"] == 0
    # named by a person, it goes, and the item's history says how
    r = c.post("/api/faxes/follow_up", json={"finding_ids": [rate], "stale_ok": True}, headers=XHR).json()
    assert r["sent"] == 1 and len(line.sent) == 2
    hist = c.get(f"/api/findings/{rate}").json()
    events = " ".join(str(e) for e in (hist.get("events") or hist.get("history") or []))
    assert "checked in EMA that it is still unpaid" in events
    # the scheduled run never sends one
    import remitwatch.cli as cli
    out = cli._fax_run(site.db, budget_s=30)
    assert all(x.get("status") != "sent" for x in out["follow_up"]["results"])
