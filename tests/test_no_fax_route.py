"""A payer that files nothing faxed at a level is never faxed at that level, whoever presses the button.

CGS, Kentucky's Medicare contractor, files no faxed redetermination request (first level): such a fax is not filed and the
120-day window keeps running. The appeal, its follow-ups and its retries are held with what to do instead (myCGS or mail);
the second level (C2C) still goes by fax. All data invented."""
from __future__ import annotations

import sqlite3

from remitwatch import fax
from tests.test_fax import PAYER_FAX, XHR, _age, _book, _number, _send, site  # noqa: F401  (site is a fixture)

CGS = "Medicare KY (CGS J15)"


def _payer(db: str, fid: str, group: str) -> None:
    conn = sqlite3.connect(db)
    conn.execute("UPDATE findings SET payer_group=? WHERE finding_id=?", (group, fid))
    conn.commit()
    conn.close()


def test_the_rule_names_cgs_at_the_first_level_only():
    why = fax.no_fax(CGS, 1)
    assert why and "myCGS" in why and "not filed" in why and "C2C" in why
    assert fax.no_fax(CGS) == why and fax.no_fax("Medicare OH (CGS J15)", 1) == why
    assert fax.no_fax(CGS, 2) is None and fax.no_fax(CGS, 3) is None
    for other in ("Medicare IN (WPS J8)", "Medicare (other)", "Anthem / BCBS", "Humana Medicare Advantage", "", None):
        assert fax.no_fax(other, 1) is None and fax.no_fax_short(other, 1) is None
    assert "myCGS" in fax.no_fax_short(CGS, 1) and fax.no_fax_short(CGS, 2) is None


def test_the_rule_holds_whatever_the_remittance_calls_cgs():
    """A payer name the rules do not know is grouped under itself, title-cased: the rule ignores case, and it reads the
    remittance's own payer name too, for a CGS remittance grouped elsewhere."""
    from remitwatch.codes import payer_group_kind
    for raw in ("CGS J15 PART B", "CGS", "CGS MEDICARE", "cgs government services", "KY MEDICARE"):
        grp = payer_group_kind(raw, "MB")[0]
        assert fax.no_fax(grp, 1, raw), (raw, grp)
    assert fax.no_fax("Self-funded plan TPAs", 1, "CGS ADMINISTRATORS LLC")
    assert fax.no_fax("Medicare (other)", 1, "MEDICARE OF KENTUCKY")
    assert fax.no_fax("Medicare (other)", 1, "WPS GOVERNMENT HEALTH ADMINISTRATORS") is None
    assert "myCGS" in fax.shared_why("Medicare (other)") and "WPS" in fax.shared_why("Medicare (other)")


def test_the_number_check_holds_the_first_level_and_lets_the_second_go():
    from remitwatch.actions import apply_payer_fax
    conn = _book([("out", "2026-09-01", "appeal", 1, "sent", None)], payer_kind="medicare")
    conn.execute("UPDATE findings SET payer_group=?", (CGS,))
    for purpose, number in (("appeal", "8005550142"), ("level_2", "9045394081")):
        apply_payer_fax(conn, {"payer_group": CGS, "purpose": purpose, "number": number, "actor": "x", "at": "2026-08-01T00:00:00+00:00"})
    f = {"payer_group": CGS, "payer_kind": "medicare"}
    nb, hold = fax.number_hold(conn, f, 1, [])
    assert hold == fax.no_fax(CGS, 1)                    # a saved first-level number changes nothing
    nb, hold = fax.number_hold(conn, f, 2, [])
    assert hold is None and nb["number"] == "9045394081"
    # the follow-up the Faxes page lists says why it waits, and offers no number to send it to
    due = fax.followups(conn, "2026-11-15")
    first = [d for d in due if int(d.get("level") or 1) == 1]
    assert first and all("myCGS" in (d.get("hold") or d.get("why") or "") and not d.get("number") for d in first)


def test_an_appeal_to_cgs_is_never_faxed_and_says_where_it_goes(site, monkeypatch):
    c, line = site.c, site.line
    rate = site.ids["UNDERPAID_VS_OWN_RATE"]
    _payer(site.db, rate, CGS)
    assert _number(c, PAYER_FAX, payer=CGS).status_code == 200   # even with a first-level number saved
    r = _send(c, rate)
    assert r["status"] == "held" and r["code"] == "no_fax_route" and "myCGS" in r["reason"] and not line.sent
    # a person's "still unpaid in EMA" or "it went out as billed" changes nothing
    assert _send(c, rate, stale_ok=True, confirmed=True)["code"] == "no_fax_route" and not line.sent
    # sending many at once, when that is on, holds it the same way
    monkeypatch.setenv("REMITWATCH_BULK_APPEAL", "1")
    assert _send(c, rate, bulk=True)["code"] == "no_fax_route" and not line.sent
    # the list behind "Appeal everything appealable" does not count it as ready, and says why in place of a number
    a = c.get("/api/appeals/appealable").json()
    mine = [i for i in a["items"] if i["finding_id"] == rate]
    assert mine and mine[0]["ready"] is False and mine[0]["by_hand"] is True
    payer = next(p for p in a["payers"] if p["payer_group"] == CGS)
    assert "myCGS" in payer["by_hand"]
    # the Faxes page keeps the second-level number box and says why there is no first-level one
    p = next(p for p in c.get("/api/faxes/payers").json()["payers"] if p["payer_group"] == CGS)
    assert "myCGS" in p["no_fax"] and p["by_hand"] is None
    # the other payers are untouched
    other = site.ids["DENIED_MEDICAL_NECESSITY"]
    _number(c, PAYER_FAX)
    assert _send(c, other)["status"] == "sent"


def test_an_item_past_its_window_is_told_that_instead(site):
    rate = site.ids["UNDERPAID_VS_OWN_RATE"]
    _payer(site.db, rate, CGS)
    conn = sqlite3.connect(site.db)
    conn.execute("UPDATE findings SET appeal_deadline='2026-01-01' WHERE finding_id=?", (rate,))
    conn.commit()
    conn.close()
    r = _send(site.c, rate)
    assert r["code"] == "expired"


def test_follow_ups_and_retries_of_a_fax_sent_before_the_rule_are_held(site):
    c, line = site.c, site.line
    rate = site.ids["UNDERPAID_VS_OWN_RATE"]
    _number(c, PAYER_FAX)
    assert _send(c, rate)["status"] == "sent"            # faxed while the item was another payer's (or before this rule)
    line.deliver_all()
    c.post("/api/faxes/refresh", headers=XHR)
    _payer(site.db, rate, CGS)
    assert _number(c, PAYER_FAX, payer=CGS).status_code == 200
    _age(site.db, 70)                                    # past the 60 days Medicare has to answer
    n = len(line.sent)
    r = c.post("/api/faxes/follow_up", json={}, headers=XHR).json()
    mine = [x for x in r["results"] if x["finding_id"] == rate]
    assert mine and mine[0]["status"] == "held" and "myCGS" in mine[0]["reason"] and len(line.sent) == n
    # a person pressing send with the number in front of them is held too
    r = c.post("/api/faxes/follow_up", json={"finding_ids": [rate], "number": PAYER_FAX}, headers=XHR).json()
    assert r["sent"] == 0 and "myCGS" in r["results"][0]["reason"] and len(line.sent) == n


def test_a_failed_first_level_fax_to_cgs_is_not_tried_again(site):
    c, line = site.c, site.line
    rate = site.ids["UNDERPAID_VS_OWN_RATE"]
    BUSY = "8005550999"                                  # the fixture's always-busy line
    _number(c, BUSY)
    assert _send(c, rate)["status"] == "sent"
    _payer(site.db, rate, CGS)
    _number(c, BUSY, payer=CGS)
    for _ in range(3):
        c.post("/api/faxes/refresh", headers=XHR)
    assert len(line.to(BUSY)) == 1                      # the one try before the rule, and no other
    import json
    conn = sqlite3.connect(site.db)
    docs = [json.loads(d or "{}") for (d,) in conn.execute("SELECT doc FROM faxes WHERE direction='out' AND finding_id=?", (rate,))]
    conn.close()
    assert docs and all("myCGS" in (d.get("held") or "") for d in docs)


def test_cgs_second_level_still_goes_to_the_second_level_number(site, monkeypatch):
    """An appeal faxed at the first level before the rule, upheld: the reconsideration goes to C2C's number, never to the
    first-level number."""
    from tests.fake_fax import pdf_of
    from tests.test_fax import LEVEL2_FAX, _read
    c, line = site.c, site.line
    note_id = site.ids["DENIED_MEDICAL_NECESSITY"]
    _number(c, PAYER_FAX)
    assert _send(c, note_id)["status"] == "sent"
    line.deliver_all()
    c.post("/api/faxes/refresh", headers=XHR)
    conn = sqlite3.connect(site.db)
    conn.execute("UPDATE findings SET payer_group=?, payer_kind='medicare' WHERE finding_id=?", (CGS, note_id))
    conn.commit()
    conn.close()
    _number(c, PAYER_FAX, payer=CGS)
    _number(c, LEVEL2_FAX, purpose="level_2", payer=CGS)
    monkeypatch.setattr(fax, "classify", lambda pdf, client=None: _read(decision="upheld", claim_numbers=["PC300"]))
    line.receive("in-1", pdf_of("Redetermination upheld. Claim PC300"), caller=PAYER_FAX)
    c.post("/api/faxes/refresh", headers=XHR)
    before = len(line.to(PAYER_FAX))
    r = c.post("/api/faxes/follow_up", json={}, headers=XHR).json()
    mine = [x for x in r["results"] if x["finding_id"] == note_id]
    assert mine and mine[0]["status"] == "sent" and mine[0]["number"] == "(800) 555-0177"
    assert len(line.to(LEVEL2_FAX)) == 1 and len(line.to(PAYER_FAX)) == before
