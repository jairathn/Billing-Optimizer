"""Find a claim: one search box for any claim, whatever its status or filing window, and one view of the whole bill (every
submission, every remittance, every line, adjustment and remark). Everything here is invented."""
from __future__ import annotations

import json
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch.detect import Detector  # noqa: E402
from remitwatch.store import Store  # noqa: E402
from tests.test_activity import PW  # noqa: E402
from tests.review6.r6cov_h import add_file, clm, pcn, sv, utc_today, ymd  # noqa: E402


def _client(tmp_path, monkeypatch, show_phi=False):
    """Bill CB0000500 went out as C017 and was denied for a missing modifier; corrected, it went out as C024 and was paid,
    so nothing on it is left to do. Another patient's claim was paid on the same remittance, and a third patient's Mohs
    line was denied for a missing authorization and is still unpaid."""
    from fastapi.testclient import TestClient

    from remitwatch import api
    db = str(tmp_path / "s.sqlite")
    today = utc_today()
    paid = lambda back: ymd(today - timedelta(days=back))           # noqa: E731
    dos = paid(60)
    add_file(tmp_path, db, [clm(pcn(500, "C017"), "4", sv("99214", "223.65", "0.00", cas="CAS*CO*16*223.65~LQ*HE*N822~", dos=dos), dos=dos, charge="223.65")],
             paid(50))
    add_file(tmp_path, db, [clm(pcn(500, "C024"), "1", sv("99214", "223.65", "117.60", "117.60", "CAS*CO*45*106.05~", dos=dos), dos=dos, charge="223.65", paid="117.60"),
                            clm(pcn(501, "C017"), "1", sv("99213", "150.00", "90.00", "90.00", "CAS*CO*45*60.00~", dos=dos), dos=dos, charge="150.00",
                                paid="90.00", who="OTHER*PERSON****MI*M00000009"),
                            clm(pcn(502, "C017"), "1", sv("17311", "900.00", "0.00", cas="CAS*CO*197*900.00~", dos=dos), dos=dos, charge="900.00",
                                who="THIRD*ONE****MI*M00000077")], paid(20))
    st = Store(db)
    Detector(st, run_id="claims").run()
    st.close()
    monkeypatch.setattr(api, "DB_PATH", db)
    monkeypatch.setattr(api, "PASSWORD", PW)
    monkeypatch.setattr(api, "USERNAME", "oda")
    monkeypatch.setattr(api, "SHOW_PHI", show_phi)
    monkeypatch.delenv("REMITWATCH_MODE", raising=False)
    return TestClient(api.app), ("oda", PW)


def _find(tc, auth, text):
    from urllib.parse import quote
    return tc.get("/api/claims/search", headers={"X-Search": quote(text)}, auth=auth).json()


def test_any_submission_or_the_payers_number_finds_the_whole_bill(tmp_path, monkeypatch):
    tc, auth = _client(tmp_path, monkeypatch)
    for text in ("CB0000500", "cb0000500c024", "CB0000500C017", "PCCB0000500C017"):
        r = _find(tc, auth, text)
        assert [x["bill"] for x in r["items"]] == ["CB0000500"], (text, r)
        it = r["items"][0]
        assert it["claim_ids"] == ["CB0000500C017", "CB0000500C024"] and it["remits"] == 2 and it["paid_cents"] == 11760, it
    assert [x["bill"] for x in _find(tc, auth, "CB00005")["items"]] != [] and len(_find(tc, auth, "CB00005")["items"]) == 3    # the start of an ID lists each bill
    assert _find(tc, auth, "ab")["items"] == []
    assert tc.get("/api/claims/search", params={"q": "CB0000501"}, auth=auth).json()["items"][0]["bill"] == "CB0000501"


def test_the_claim_view_holds_every_remittance_line_adjustment_and_remark(tmp_path, monkeypatch):
    tc, auth = _client(tmp_path, monkeypatch)
    v = tc.get("/api/claims/CB0000500", auth=auth).json()
    assert v["claim_ids"] == ["CB0000500C017", "CB0000500C024"]
    assert [(r["patient_control_number"], r["status_words"]) for r in v["remits"]] == [("CB0000500C017", "Denied"), ("CB0000500C024", "Processed as primary")]
    denied, paid = v["remits"]
    [line] = denied["lines"]
    assert line["procedure_code"] == "99214" and [(a["group_code"], a["reason_code"], a["amount_cents"]) for a in line["adjustments"]] == [("CO", "16", 22365)]
    assert line["adjustments"][0]["text"] and [x["code"] for x in line["remarks"]] == ["N822"] and line["remarks"][0]["text"]
    assert paid["lines"][0]["paid_cents"] == 11760 and paid["lines"][0]["allowed_cents"] == 11760
    assert paid["payer_claim_control_number"] == "PCCB0000500C024" and paid["trace_number"]
    assert v["findings"] == [], "paid on the corrected claim: nothing on this bill is left to do"
    # names hidden: initials only, and nothing that identifies the patient beyond them
    text = json.dumps(v) + json.dumps(_find(tc, auth, "CB0000500"))
    for leak in ("SAMPLE", "M00000001", "patient_last", "patient_id", "insured"):
        assert leak not in text, leak
    assert v["patient"] and len(v["patient"]) <= 5 and "member_id" not in v
    # a name or member number finds nothing while names are hidden: the box would otherwise say who is a patient
    assert _find(tc, auth, "SAMPLE")["items"] == [] and _find(tc, auth, "M00000001")["items"] == []
    assert tc.get("/api/claims/NOPE1234", auth=auth).status_code == 404
    assert tc.get("/api/claims/%3Cscript%3E", auth=auth).status_code == 400
    # an item on a bill is listed in its view, and the item's detail names the bill, so its drawer can open the whole claim
    w = tc.get("/api/claims/CB0000502", auth=auth).json()
    assert [(f["signal"], f["status"]) for f in w["findings"]] == [("DENIED_AUTH", "open")], w["findings"]
    assert _find(tc, auth, "CB0000502")["items"][0]["open_findings"] == 1
    assert tc.get(f"/api/findings/{w['findings'][0]['finding_id']}", auth=auth).json()["bill"] == "CB0000502"


def test_with_names_shown_a_patient_or_member_number_finds_their_claims(tmp_path, monkeypatch):
    tc, auth = _client(tmp_path, monkeypatch, show_phi=True)
    assert [x["bill"] for x in _find(tc, auth, "sample pat")["items"]] == ["CB0000500"]
    assert [x["bill"] for x in _find(tc, auth, "M00000009")["items"]] == ["CB0000501"]
    v = tc.get("/api/claims/CB0000500", auth=auth).json()
    assert "SAMPLE" in v["patient"] and v["member_id"] == "M00000001"
