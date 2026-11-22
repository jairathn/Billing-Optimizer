"""
Bulk appeal faxing stays off until RemitWatch reads EMA's payments (REMITWATCH_BULK_APPEAL unset). Until then RemitWatch cannot
tell a line paid on a check with no remittance from an unpaid one, so "Appeal everything appealable" and "Appeal by fax" on
ticked items would fax payers about claims already paid. One appeal a person opens and checks still goes. Invented data only.
"""
from __future__ import annotations

from pathlib import Path

from tests.test_fax import PAYER_FAX, XHR, site  # noqa: F401  (site is the fixture: an app with a fake fax line)


def _number(c):
    assert c.post("/api/faxes/payer", json={"payer_group": "Anthem / BCBS", "number": PAYER_FAX}, headers=XHR).status_code == 200


def test_bulk_appeals_are_paused_by_default(site):
    site.monkeypatch.delenv("REMITWATCH_BULK_APPEAL", raising=False)
    c, line = site.c, site.line
    _number(c)
    a = c.get("/api/appeals/appealable").json()
    assert a["items"] == [] and a["payers"] == [] and "paused" in a and "EMA" in a["paused"]
    rate_id = site.ids["UNDERPAID_VS_OWN_RATE"]          # needs no note: it would go at once if bulk were on
    r = c.post("/api/appeals/send", json={"finding_id": rate_id, "bulk": True}, headers=XHR).json()
    assert r["status"] == "held" and r["code"] == "bulk_paused" and "Open an item" in r["reason"]
    assert line.sent == {}
    d = c.get(f"/api/findings/{rate_id}").json()
    assert d["finding"]["status"] == "open"


def test_one_appeal_a_person_opens_still_goes_while_bulk_is_paused(site):
    site.monkeypatch.delenv("REMITWATCH_BULK_APPEAL", raising=False)
    c, line = site.c, site.line
    _number(c)
    rate_id = site.ids["UNDERPAID_VS_OWN_RATE"]
    r = c.post("/api/appeals/send", json={"finding_id": rate_id}, headers=XHR).json()
    assert r["status"] == "sent", r
    assert len(line.to(PAYER_FAX)) == 1


def test_the_setting_turns_bulk_back_on(site):
    for value in ("1", "true", "on"):
        site.monkeypatch.setenv("REMITWATCH_BULK_APPEAL", value)
        assert site.c.get("/api/appeals/appealable").json()["items"], value
    site.monkeypatch.setenv("REMITWATCH_BULK_APPEAL", "0")
    assert site.c.get("/api/appeals/appealable").json()["items"] == []


def test_the_page_marks_its_batch_sends_and_says_when_bulk_is_paused():
    js = (Path(__file__).resolve().parents[1] / "remitwatch" / "static" / "faxes.js").read_text(encoding="utf-8")
    assert "stale_ok: !!opts.staleOk, bulk: true" in js          # every send of a batch says it is part of one
    assert "r.code === 'bulk_paused'" in js                       # the batch stops at the first pause, once
    assert "if(a.paused)" in js                                   # "Appeal everything appealable" shows the reason
