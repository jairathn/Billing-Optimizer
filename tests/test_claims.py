"""Find a claim: one search box for any claim, whatever its status or filing window, and one view of the whole bill (every
submission, every remittance in the payer's order, every line, adjustment and remark, the items and takebacks that name
it, the EMA rows). Everything here is invented."""
from __future__ import annotations

import json
import os
import sys
from datetime import timedelta
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch.detect import Detector  # noqa: E402
from remitwatch.store import Store  # noqa: E402
from tests.test_activity import PW  # noqa: E402
from tests.review6.r6cov_h import add_file, clm, pcn, sv, utc_today, ymd  # noqa: E402


def _client(tmp_path, monkeypatch, show_phi=False):
    """Bill CB0000500 went out as C017 and was denied for a missing modifier; corrected, it went out as C024 and was paid,
    so nothing on it is left to do. Another patient's claim was paid on the same remittance, a third patient's Mohs line
    was denied for a missing authorization and is still unpaid, and a fourth claim was paid, reversed and paid again in one
    file, as its ninth and tenth claims (claim numbers that sort wrongly as text). Later the payer took $650 back naming the
    fourth claim. EMA holds a fifth claim no payer has answered. One payer claim number is stored with a space."""
    from fastapi.testclient import TestClient

    from remitwatch import api
    db = str(tmp_path / "s.sqlite")
    today = utc_today()
    paid = lambda back: ymd(today - timedelta(days=back))           # noqa: E731
    dos = paid(60)
    add_file(tmp_path, db, [clm(pcn(500, "C017"), "4", sv("99214", "223.65", "0.00", cas="CAS*CO*16*223.65~LQ*HE*N822~", dos=dos), dos=dos, charge="223.65")],
             paid(50))
    filler = [clm(pcn(700 + i, "C017"), "1", sv("99212", "90.00", "50.00", "50.00", "CAS*CO*45*40.00~", dos=dos), dos=dos, charge="90.00", paid="50.00",
                  who=f"FILL*ER{i}****MI*M0000077{i}") for i in range(5)]
    add_file(tmp_path, db, [clm(pcn(500, "C024"), "1", sv("99214", "223.65", "117.60", "117.60", "CAS*CO*45*106.05~", dos=dos), dos=dos, charge="223.65", paid="117.60"),
                            clm(pcn(501, "C017"), "1", sv("99213", "150.00", "90.00", "90.00", "CAS*CO*45*60.00~", dos=dos), dos=dos, charge="150.00",
                                paid="90.00", who="OTHER*PERSON****MI*M00000009"),
                            clm(pcn(502, "C017"), "1", sv("17311", "900.00", "0.00", cas="CAS*CO*197*900.00~", dos=dos), dos=dos, charge="900.00",
                                who="THIRD*ONE****MI*M00000077")] + filler
             + [clm(pcn(600, "C017"), "22", sv("11102", "-900.00", "-600.00", "600.00", "CAS*CO*45*-300.00~", dos=dos), dos=dos, charge="-900.00", paid="-600.00",
                    who="FOURTH*PAT****MI*M00000066"),
                clm(pcn(600, "C017"), "1", sv("11102", "900.00", "650.00", "650.00", "CAS*CO*45*250.00~", dos=dos), dos=dos, charge="900.00", paid="650.00",
                    who="FOURTH*PAT****MI*M00000066")], paid(20))
    add_file(tmp_path, db, [clm(pcn(600, "C017"), "1", sv("11102", "900.00", "600.00", "600.00", "CAS*CO*45*300.00~", dos=dos), dos=dos, charge="900.00", paid="600.00",
                                who="FOURTH*PAT****MI*M00000066")], paid(40))
    add_file(tmp_path, db, [clm(pcn(800 + i, "C017"), "1", sv("99212", "90.00", "50.00", "50.00", "CAS*CO*45*40.00~", dos=dos), dos=dos, charge="90.00", paid="50.00",
                                who=f"LATE*ER{i}****MI*M0000088{i}") for i in range(3)], paid(5), plb=f"PLB*1777777772*20261231*WO:PC{pcn(600, 'C017')}    {pcn(600, 'C017')}*650.00~")
    st = Store(db)
    st.conn.execute("""INSERT INTO ema_rows (row_fingerprint, family, source_name, row_index, service_date, claim_id, cpt, charges_cents, claim_status, posted_date)
                       VALUES ('emaonly1', 'charges', 'export.xlsx', 1, ?, 'CB0000900C017', '99213', 15000, 'Payment Pending', ?)""", (dos, dos))
    st.conn.execute("UPDATE claims SET payer_claim_control_number='ABC1234 5678901' WHERE patient_control_number='CB0000501C017'")
    st.conn.commit()
    Detector(st, run_id="claims").run()
    st.close()
    monkeypatch.setattr(api, "DB_PATH", db)
    monkeypatch.setattr(api, "PASSWORD", PW)
    monkeypatch.setattr(api, "USERNAME", "oda")
    monkeypatch.setattr(api, "SHOW_PHI", show_phi)
    monkeypatch.delenv("REMITWATCH_MODE", raising=False)
    return TestClient(api.app), ("oda", PW)


def _find(tc, auth, text):
    return tc.get("/api/claims/search", headers={"X-Search": quote(text)}, auth=auth).json()


def _view(tc, auth, claim):
    return tc.get("/api/claims/view", headers={"X-Claim": quote(claim)}, auth=auth)


def test_any_submission_or_the_payers_number_finds_the_whole_bill(tmp_path, monkeypatch):
    tc, auth = _client(tmp_path, monkeypatch)
    for text in ("CB0000500", "cb0000500c024", "CB0000500C017", "PCCB0000500C017", "pccb0000500c017", "PC CB0000500-C017", "CB0000500C0"):
        r = _find(tc, auth, text)
        assert r["items"] and r["items"][0]["bill"] == "CB0000500", (text, r)
        it = r["items"][0]
        assert it["claim_ids"] == ["CB0000500C017", "CB0000500C024"] and it["remits"] == 2 and it["paid_cents"] == 11760, it
    assert _find(tc, auth, "PCCB00005")["items"][0]["bill"] in {"CB0000500", "CB0000501", "CB0000502"}      # a start of the payer's number
    for typed in ("abc12345678901", "ABC1234-5678901", "abc1234 5678901", "12345678901", "ABC123"):
        assert [x["bill"] for x in _find(tc, auth, typed)["items"]] == ["CB0000501"], typed          # stored with a space; any 8 in a row, or a start of 6
    assert len(_find(tc, auth, "CB00005")["items"]) == 3
    assert _find(tc, auth, "ab")["items"] == []
    assert tc.get("/api/claims/search", params={"q": "CB0000501"}, auth=auth).json()["items"][0]["bill"] == "CB0000501"


def test_the_claim_view_holds_every_remittance_line_adjustment_and_remark(tmp_path, monkeypatch):
    tc, auth = _client(tmp_path, monkeypatch)
    v = _view(tc, auth, "CB0000500").json()
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
    assert _view(tc, auth, "NOPE1234").status_code == 404
    assert _view(tc, auth, "ab").status_code == 400 and tc.get("/api/claims/view", auth=auth).status_code == 400
    # an item on a bill is listed in its view with the remittance it was found on, and the item's detail names the bill
    w = _view(tc, auth, "CB0000502").json()
    assert [(f["signal"], f["status"], f["via"]) for f in w["findings"]] == [("DENIED_AUTH", "open", "line")], w["findings"]
    assert w["findings"][0]["remit_date"] and w["findings"][0]["line_key"] in {l["line_key"] for r in w["remits"] for l in r["lines"]}
    assert _find(tc, auth, "CB0000502")["items"][0]["open_findings"] == 1
    assert tc.get(f"/api/findings/{w['findings'][0]['finding_id']}", auth=auth).json()["bill"] == "CB0000502"


def test_remittances_follow_the_payers_order_and_a_takeback_reaches_its_claim(tmp_path, monkeypatch):
    """Paid, then reversed and paid again in one file (its 9th and 10th claims): the reversal comes before the re-payment,
    so the remittance opened last is the payment that stands. The $650 the payer took back later names the claim: the
    takeback has a dispute-by date, its drawer opens the claim, and the claim's view lists the money and the takeback."""
    tc, auth = _client(tmp_path, monkeypatch)
    v = _view(tc, auth, "CB0000600C017").json()
    assert [r["status_words"] for r in v["remits"]] == ["Processed as primary", "Reversal of an earlier payment", "Processed as primary"], v["remits"]
    assert [r["paid_cents"] for r in v["remits"]] == [60000, -60000, 65000]
    assert [(p["reason_code"], p["amount_cents"]) for p in v["taken_back"]] == [("WO", 65000)]
    [tb] = [f for f in v["findings"] if f["via"] == "takeback"]
    assert tb["signal"] == "TAKEBACK" and tb["appeal_deadline"], tb
    d = tc.get(f"/api/findings/{tb['finding_id']}", auth=auth).json()
    assert d["bill"] == "CB0000600" and d["finding"]["appeal_deadline"] and d["finding"]["deadline_basis"]
    assert _find(tc, auth, "CB0000600")["items"][0]["open_findings"] >= 1


def test_a_claim_ema_holds_that_no_payer_answered_opens_with_its_ema_rows(tmp_path, monkeypatch):
    tc, auth = _client(tmp_path, monkeypatch)
    r = _find(tc, auth, "CB0000900")
    assert [x["bill"] for x in r["items"]] == ["CB0000900"] and r["items"][0]["remits"] == 0
    v = _view(tc, auth, "CB0000900C017").json()
    assert v["remits"] == [] and [e["claim_status"] for e in v["ema"]] == ["Payment Pending"] and v["claim_ids"] == ["CB0000900C017"]


def test_a_routine_rate_pattern_shows_on_each_of_its_claims(tmp_path, monkeypatch):
    from tests.test_desk import _client as desk_client
    tc, auth = desk_client(tmp_path, monkeypatch)
    v = _view(tc, auth, "CB0002200C017").json()          # an HMO visit paid at the payer's second rate, filed recently
    pats = [f for f in v["findings"] if f["via"] == "pattern"]
    assert pats and pats[0]["signal"] == "SECOND_RATE" and pats[0]["claim_cents"] == 30000 and pats[0]["claim_deadline"], v["findings"]
    assert set(pats[0]["line_keys"]) <= {l["line_key"] for r in v["remits"] for l in r["lines"]}


def test_with_names_shown_a_patient_or_member_number_finds_their_claims(tmp_path, monkeypatch):
    tc, auth = _client(tmp_path, monkeypatch, show_phi=True)
    assert [x["bill"] for x in _find(tc, auth, "sample pat")["items"]] == ["CB0000500"]
    assert [x["bill"] for x in _find(tc, auth, "M00000009")["items"]] == ["CB0000501"]
    v = _view(tc, auth, "CB0000500").json()
    assert "SAMPLE" in v["patient"] and v["member_id"] == "M00000001"
