"""Hotfix 2: what chaos round 0 found in the code the billers use, on the server and on the serverless host.

H2  one fax at a time per item, and Sent only when a fax went out
H3  a screen left open cannot undo what changed since; the serverless journal keeps every entry a person made
H4  a Paid amount means what was typed
H5  the API reads only what it was sent
H6  a note is faxed only for the patient and visit it shows
H7  hiding a reason for a payer shows its size first and needs the owner
H8  one stray remittance cannot make the whole feed look current
H9  fax numbers are ASCII digits
H10 Find a claim does not say "nothing matches" when it cannot know

Invented data only: names, member numbers, claim ids, record numbers and amounts are made up."""
from __future__ import annotations

import base64
import json
import os
import sqlite3
import threading
from datetime import date, datetime, timedelta, timezone

import pytest

from remitwatch import cloud, fax
from tests.fake_fax import FakeLine, pdf_of, pdf_text
from tests.test_cloud import DATA, PW, box, cycle, raw_conn, visits_with_downcodes  # noqa: F401  (box is a fixture)
from tests.test_fax import GOOD_REVIEW, PAYER_FAX, XHR, _age, _number, _rows, _send, site  # noqa: F401  (site is a fixture)
from tests.test_features import build_store, claim, clean_visits, svc
from tests.test_remitwatch import remit
from tests.test_workable import charge

OWNER = "owner-invented-passphrase-42"


def _status(c, fid, headers=None, **body):
    return c.post(f"/api/findings/{fid}/status", json=body, headers=dict(XHR, **(headers or {})))


def _now_iso(minutes: float = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(timespec="microseconds")


def _reserve_row(db: str, fid: str, minutes: float = 0, error: str | None = None) -> None:
    """A send reserved and never settled: what a server stopped between the reservation and the send leaves behind."""
    conn = sqlite3.connect(db)
    conn.execute("""INSERT INTO faxes (fax_key, direction, finding_id, payer_group, number, purpose, level, provider, provider_id, status,
                    created_at, updated_at, doc, error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (f"out:fake:{fid[:8]}:appeal:1:1", "out", fid, "Anthem / BCBS", "8005550142", "appeal", 1, "fake", None, "sending",
                  _now_iso(minutes), _now_iso(minutes), json.dumps({"actor": "oda", "filename": "appeal-x.pdf"}), error))
    conn.commit()
    conn.close()


def _events(c, fid) -> list[dict]:
    return c.get(f"/api/findings/{fid}").json()["events"]


# =========================================================================== H2: one fax at a time, Sent only when it went
def test_h2_sent_pressed_while_the_appeal_is_prepared_is_refused_and_the_fax_still_goes(site, monkeypatch):
    """0a-1, 0a-8, 0a-9: 'Mark sent' or 'Let go' pressed during the minute the appeal takes is refused, a second press runs
    no second review, and the fax still goes and marks the item Sent."""
    from remitwatch import notes
    c, line = site.c, site.line
    fid = site.ids["DENIED_MEDICAL_NECESSITY"]
    assert _number(c, PAYER_FAX).status_code == 200
    entered, release, reviews = threading.Event(), threading.Event(), []

    def slow_review(context, text, pdf_bytes=None, client=None):
        reviews.append(1)
        entered.set()
        assert release.wait(30)
        return dict(GOOD_REVIEW, model="test-model")
    monkeypatch.setattr(notes, "review_note", slow_review)
    got: dict = {}
    t = threading.Thread(target=lambda: got.update(first=_send(c, fid)))
    t.start()
    try:
        assert entered.wait(30)
        for body in ({"status": "appealed", "seen_status": "open"}, {"status": "appealed"}, {"status": "dismissed", "seen_status": "open"}):
            r = _status(c, fid, **body)
            assert r.status_code == 409 and r.json()["code"] == "in_flight", r.json()
            assert r.json()["detail"] == "A fax for this item is going out now. Wait a minute and look at the item again."
        b = c.post("/api/findings/bulk-status", json={"ids": [fid], "status": "dismissed", "seen": {fid: "open"}}, headers=XHR).json()
        assert b["updated"] == 0 and b["in_flight"] == [fid]
        second = c.post("/api/appeals/send", json={"finding_id": fid}, headers=XHR)
        assert second.status_code == 409 and second.json()["code"] == "in_flight"
    finally:
        release.set()
        t.join(60)
    assert got["first"]["status"] == "sent", got
    assert len(line.sent) == 1 and len(reviews) == 1
    d = c.get(f"/api/findings/{fid}").json()
    assert d["finding"]["status"] == "appealed"
    manual = [e for e in d["events"] if e["kind"] == "manual"]
    assert len(manual) == 1 and manual[0]["from_status"] == "open" and "Faxed to" in manual[0]["note"]
    # once it went, a person can change the item again
    assert _status(c, fid, status="in_progress", seen_status="appealed").status_code == 200


def test_h2_a_second_press_while_the_first_is_sending_never_marks_the_item_sent(site, monkeypatch):
    """0d-2: the first send is still with the line when the second press comes; the line then refuses it. The item stays
    To do, with no 'Faxed to' line in its history."""
    c, line = site.c, site.line
    rate = site.ids["UNDERPAID_VS_OWN_RATE"]
    assert _number(c, PAYER_FAX).status_code == 200
    entered, release = threading.Event(), threading.Event()

    def refused(to, pdf, filename):
        entered.set()
        assert release.wait(30)
        raise fax.FaxError("SRFax refused Queue_Fax: insufficient credit on the account.")
    monkeypatch.setattr(line, "send", refused)
    got: dict = {}
    t = threading.Thread(target=lambda: got.update(first=_send(c, rate)))
    t.start()
    try:
        assert entered.wait(30)
        r = c.post("/api/appeals/send", json={"finding_id": rate}, headers=XHR)
        assert r.status_code == 409 and r.json()["code"] == "in_flight", r.json()
        assert _status(c, rate, status="appealed", seen_status="open").status_code == 409
    finally:
        release.set()
        t.join(60)
    assert got["first"]["code"] == "fax_error"
    d = c.get(f"/api/findings/{rate}").json()
    assert d["finding"]["status"] == "open"
    assert not any("Faxed to" in (e["note"] or "") for e in d["events"])


def test_h2_a_reservation_left_by_a_stopped_server_is_in_flight_then_unknown_never_sent(site):
    """0a-12: a send reserved, then the server stopped. While it could still be going: in flight. Older than any send
    takes: reported as unknown, never counted as sent and never marking the item Sent. A fax SRFax took still does."""
    c, line = site.c, site.line
    rate = site.ids["UNDERPAID_VS_OWN_RATE"]
    assert _number(c, PAYER_FAX).status_code == 200
    _reserve_row(site.db, rate)
    r = c.post("/api/appeals/send", json={"finding_id": rate}, headers=XHR)
    assert r.status_code == 409 and r.json()["code"] == "in_flight"
    assert _status(c, rate, status="dismissed", seen_status="open").status_code == 409
    _age(site.db, minutes=11)
    r = _send(c, rate)
    assert r["status"] == "held" and r["code"] == "unknown_send", r
    assert "does not know whether it went out. Look on the Faxes page before sending it again." in r["reason"]
    assert line.sent == {} and c.get(f"/api/findings/{rate}").json()["finding"]["status"] == "open"
    # a person may decide now (the send is not in flight)
    assert _status(c, rate, status="in_progress", seen_status="open").status_code == 200


def test_h2_a_fax_the_line_took_still_marks_the_item_sent(site):
    c, line = site.c, site.line
    rate = site.ids["UNDERPAID_VS_OWN_RATE"]
    assert _number(c, PAYER_FAX).status_code == 200
    conn = sqlite3.connect(site.db)
    conn.execute("""INSERT INTO faxes (fax_key, direction, finding_id, payer_group, number, purpose, level, provider, provider_id, status, created_at,
                    updated_at, doc) VALUES ('out:fake:x:appeal:1:1','out',?,'Anthem / BCBS','8005550142','appeal',1,'fake','F0099','queued',?,?,'{}')""",
                 (rate, _now_iso(), _now_iso()))
    conn.commit()
    conn.close()
    r = _send(c, rate)
    assert r["status"] == "skipped" and c.get(f"/api/findings/{rate}").json()["finding"]["status"] == "appealed"


# --------------------------------------------------------------------------- the serverless host (it sends when told to)
@pytest.fixture()
def sl(box, monkeypatch):
    """The serverless site over a state folder loaded by one run, its remittances paid last week (open windows)."""
    from fastapi.testclient import TestClient

    from remitwatch import api, appeals
    root, client, state = box
    claims = visits_with_downcodes()
    (root / DATA).mkdir(parents=True, exist_ok=True)
    pay = (date.today() - timedelta(days=7)).strftime("%Y%m%d")
    for n, i in enumerate(range(0, len(claims), 10)):
        (root / DATA / f"r{n:03d}.RMT").write_bytes(remit("".join(claims[i:i + 10]), "100.00", seg_count=99, isa_ctrl=f"{n + 1:09d}", pay_date=pay))
    cycle(state, client)
    monkeypatch.setenv("REMITWATCH_MODE", "serverless")
    monkeypatch.delenv("REMITWATCH_ALLOW_OPEN", raising=False)
    monkeypatch.setenv("REMITWATCH_STATE_DIR", str(root / "RemitWatch-state"))
    monkeypatch.setattr(api, "PASSWORD", PW)
    monkeypatch.setattr(api, "USERNAME", "oda")
    monkeypatch.setattr(api, "OWNER_PASSWORD", "")
    monkeypatch.setattr(cloud, "SERVING_COPY", cloud.ServingCopy())
    for k, v in {"name": "Test Dermatology", "address": "1 Main St", "tin": "61-0000000", "npi": "1999999999", "phone": "(270) 555-0100",
                 "fax": "(270) 555-0101", "email": "billing@example.org"}.items():
        monkeypatch.setitem(appeals.PRACTICE, k, v)
    h = dict(XHR, Authorization="Basic " + base64.b64encode(f"oda:{PW}".encode()).decode())
    tc = TestClient(api.app)
    ids = [i["finding_id"] for i in tc.get("/api/findings?signal=DOWNCODE_LEVEL_REDUCED", headers=h).json()["items"]]
    return type("SL", (), {"c": tc, "h": h, "ids": ids, "state": state, "client": client, "api": api, "root": root})


def _journal(state) -> list[dict]:
    return [json.loads(state.get(m["name"])) for m in state.list(cloud.ACTIONS)]


def test_h2_serverless_a_status_change_waits_while_this_hosts_fax_is_going_out(sl, monkeypatch):
    line = FakeLine()
    monkeypatch.setattr(fax, "provider", lambda env=None: line)            # this host sends (REMITWATCH_FAX_SENDER=serverless)
    c, h, api, fid = sl.c, sl.h, sl.api, sl.ids[0]
    payer = c.get(f"/api/findings/{fid}", headers=h).json()["finding"]["payer_group"]
    assert c.post("/api/faxes/payer", json={"payer_group": payer, "number": PAYER_FAX}, headers=h).status_code == 200
    token = api._prep_take(fid)                                          # an appeal for it is being prepared on another instance
    assert token
    r = c.post(f"/api/findings/{fid}/status", json={"status": "appealed", "seen_status": "open"}, headers=h)
    assert r.status_code == 409 and r.json()["code"] == "in_flight"
    r = c.post("/api/appeals/send", json={"finding_id": fid}, headers=h)
    assert r.status_code == 409 and r.json()["code"] == "in_flight"
    api._prep_release(fid, token)
    # a send reserved (its record in the journal), no fax id yet
    f = c.get(f"/api/findings/{fid}", headers=h).json()["finding"]
    api._record_action({"kind": "fax", "record": {"fax_key": "out:fake:x:appeal:1:1", "direction": "out", "finding_id": fid,
                                                    "payer_group": f["payer_group"], "number": "8005550142", "purpose": "appeal", "level": 1,
                                                    "provider": "fake", "provider_id": None, "status": "sending",
                                                    "created_at": _now_iso(), "updated_at": _now_iso(), "doc": {}}})
    n = len(_journal(sl.state))
    r = c.post(f"/api/findings/{fid}/status", json={"status": "dismissed", "seen_status": "open"}, headers=h)
    assert r.status_code == 409 and r.json()["code"] == "in_flight" and len(_journal(sl.state)) == n
    assert c.get(f"/api/findings/{fid}", headers=h).json()["finding"]["status"] == "open"


# =========================================================================== H3: what the page saw
def _close_by_the_run(db: str, fid: str) -> None:
    conn = sqlite3.connect(db)
    conn.execute("""UPDATE findings SET status='resolved', recovered_cents=11760, recovered_date='2026-11-20', prev_status='open',
                    auto_note='Paid on a later remittance (2026-11-20, trace T1, $117.60)' WHERE finding_id=?""", (fid,))
    conn.execute("""INSERT INTO finding_events (finding_id, at, kind, from_status, to_status, amount_cents, actor, note)
                    VALUES (?, '2026-11-21T10:17:00+00:00', 'paid_on_later_remit', 'open', 'resolved', 11760, 'system', 'Paid on a later remittance')""", (fid,))
    conn.commit()
    conn.close()


def test_h3_a_panel_left_open_cannot_undo_a_payment_the_run_found(site):
    """0a-4: the run closed the item as Paid while the panel still showed it To do. Sent or Let go from that panel is
    refused with the status it has now; a page loaded before this release (no seen_status) is refused too; a person who
    sees it Paid can still reopen it on purpose."""
    c = site.c
    fid = site.ids["UNDERPAID_VS_OWN_RATE"]
    _close_by_the_run(site.db, fid)
    for body in ({"status": "appealed", "seen_status": "open"}, {"status": "dismissed", "seen_status": "open"}, {"status": "appealed"}):
        r = _status(c, fid, **body)
        assert r.status_code == 409 and r.json()["code"] == "changed", r.json()
        assert r.json()["detail"] == "This item changed since your page loaded: it is now Paid. It was not changed."
        assert r.json()["status"] == "resolved"
    f = c.get(f"/api/findings/{fid}").json()["finding"]
    assert f["status"] == "resolved" and f["recovered_cents"] == 11760
    assert _status(c, fid, status="open", seen_status="resolved").status_code == 200


def test_h3_a_list_opened_earlier_leaves_out_what_changed_since(site):
    """0a-5: a bulk Let go from a list drawn before another tab faxed an appeal leaves that appeal alone and says so."""
    c, line = site.c, site.line
    note_id, rate = site.ids["DENIED_MEDICAL_NECESSITY"], site.ids["UNDERPAID_VS_OWN_RATE"]
    assert _number(c, PAYER_FAX).status_code == 200
    assert _send(c, rate)["status"] == "sent"                            # tab B faxed it: it is Sent now
    b = c.post("/api/findings/bulk-status", json={"ids": [rate, note_id, note_id], "status": "dismissed",
                                                  "seen": {rate: "open", note_id: "open"}}, headers=XHR).json()
    assert b["updated"] == 1 and b["changed"] == [rate] and b["in_flight"] == []
    assert c.get(f"/api/findings/{rate}").json()["finding"]["status"] == "appealed"
    assert c.get(f"/api/findings/{note_id}").json()["finding"]["status"] == "dismissed"
    assert len([e for e in _events(c, note_id) if e["kind"] == "manual"]) == 1      # a repeated id counts once (0d-9)
    # a single change from that list, the same
    r = _status(c, rate, status="dismissed", seen_status="open")
    assert r.status_code == 409 and r.json()["code"] == "changed" and "it is now Sent" in r.json()["detail"]
    # a page loaded before this release still changes an ordinary item as before
    assert _status(c, site.ids["UNDERPAID_VS_OWN_RATE"], status="in_progress").status_code == 200


def test_h3_serverless_refuses_a_change_against_the_copy_it_serves(sl):
    c, h, fid = sl.c, sl.h, sl.ids[0]
    path = cloud.SERVING_COPY.ensure()
    _close_by_the_run(path, fid)
    n = len(_journal(sl.state))
    r = c.post(f"/api/findings/{fid}/status", json={"status": "appealed", "seen_status": "open"}, headers=h)
    assert r.status_code == 409 and r.json()["code"] == "changed" and r.json()["status"] == "resolved"
    r = c.post(f"/api/findings/{fid}/status", json={"status": "appealed"}, headers=h)
    assert r.status_code == 409 and r.json()["code"] == "changed"
    assert len(_journal(sl.state)) == n
    other = sl.ids[1]
    b = c.post("/api/findings/bulk-status", json={"ids": [fid, other], "status": "dismissed", "seen": {fid: "open", other: "open"}}, headers=h).json()
    assert b["updated"] == 1 and b["changed"] == [fid]


def test_h3_serverless_keeps_every_change_a_person_made_within_one_second(sl):
    """0d-8: Sent, To do, Sent within one second ends Sent on the instance, on a fresh instance and in the run's store."""
    c, h, fid = sl.c, sl.h, sl.ids[0]
    seen = "open"
    for st in ("appealed", "open", "appealed"):
        r = c.post(f"/api/findings/{fid}/status", json={"status": st, "seen_status": seen}, headers=h)
        assert r.status_code == 200, r.json()
        seen = st
    assert c.get(f"/api/findings/{fid}", headers=h).json()["finding"]["status"] == "appealed"
    assert all(a.get("entry") for a in _journal(sl.state)) and len({a["entry"] for a in _journal(sl.state)}) == 3
    fresh = cloud.ServingCopy()
    assert fresh.connection().execute("SELECT status FROM findings WHERE finding_id=?", (fid,)).fetchone()[0] == "appealed"
    cycle(sl.state, sl.client)
    conn = raw_conn(sl.state)
    assert conn.execute("SELECT status FROM findings WHERE finding_id=?", (fid,)).fetchone()[0] == "appealed"
    assert conn.execute("SELECT COUNT(*) FROM finding_events WHERE finding_id=? AND kind='manual'", (fid,)).fetchone()[0] == 3


def test_h3_an_old_journal_entry_with_no_id_is_applied_once_as_before(tmp_path):
    from remitwatch.actions import apply
    from remitwatch.detect import Detector
    s = build_store(tmp_path, clean_visits(1, 25, "99214", "223.65", "117.60") + [claim(300, svc("99214", "223.65", "0.00", None, "CAS*CO*50*223.65~"))],
                    pay_date="20261110")
    Detector(s, run_id="t").run()
    fid = s.conn.execute("SELECT finding_id FROM findings WHERE status='open' LIMIT 1").fetchone()[0]
    old = {"kind": "status", "finding_id": fid, "status": "appealed", "actor": "oda", "at": "2026-11-20T10:00:00+00:00", "seen": "open"}
    assert apply(s.conn, old) and apply(s.conn, dict(old))
    s.conn.commit()
    assert s.conn.execute("SELECT COUNT(*) FROM finding_events WHERE finding_id=? AND kind='manual'", (fid,)).fetchone()[0] == 1
    new = dict(old, entry="a1b2c3d4e5f60718")
    assert apply(s.conn, dict(new, status="open")) and apply(s.conn, dict(new, entry="f0e1d2c3b4a59687"))
    assert apply(s.conn, dict(new, entry="f0e1d2c3b4a59687"))      # the same entry again: nothing more
    s.conn.commit()
    assert s.conn.execute("SELECT status FROM findings WHERE finding_id=?", (fid,)).fetchone()[0] == "appealed"
    assert s.conn.execute("SELECT COUNT(*) FROM finding_events WHERE finding_id=? AND kind='manual'", (fid,)).fetchone()[0] == 3
    s.close()


# =========================================================================== H4: amounts mean what was typed
def test_h4_a_person_marks_paid_only_with_a_whole_amount_inside_the_bounds(site):
    c = site.c
    rate = site.ids["UNDERPAID_VS_OWN_RATE"]          # charge $223.65
    cases = [({}, "Type the amount the payer paid, from the remittance or EMA. If you do not know it yet, leave the item as it is."),
             ({"recovered_cents": -5000}, None), ({"recovered_cents": 1.5}, None), ({"recovered_cents": "125000"}, None),
             ({"recovered_cents": 223651}, None), ({"recovered_cents": 100000001}, None), ({"recovered_cents": 10 ** 30}, None)]
    for extra, msg in cases:
        r = _status(c, rate, status="resolved", seen_status="open", **extra)
        assert r.status_code == 400, (extra, r.json())
        if extra.get("recovered_cents") not in ("125000", 1.5):
            assert r.json()["code"] == "amount", (extra, r.json())
        if msg:
            assert r.json()["detail"] == msg
    assert c.get(f"/api/findings/{rate}").json()["finding"]["status"] == "open"
    assert _status(c, rate, status="resolved", seen_status="open", recovered_cents=125000).status_code == 200
    f = c.get(f"/api/findings/{rate}").json()["finding"]
    assert f["status"] == "resolved" and f["recovered_cents"] == 125000


def test_h4_serverless_refuses_the_same_amounts_before_anything_is_journaled(sl):
    c, h, fid = sl.c, sl.h, sl.ids[0]
    n = len(_journal(sl.state))
    for extra in ({}, {"recovered_cents": -1}, {"recovered_cents": 10 ** 30}, {"recovered_cents": 1e300}):
        r = c.post(f"/api/findings/{fid}/status", json=dict({"status": "resolved", "seen_status": "open"}, **extra), headers=h)
        assert r.status_code == 400 and r.json()["code"] == "amount", (extra, r.json())
    assert len(_journal(sl.state)) == n
    assert c.post(f"/api/findings/{fid}/status", json={"status": "resolved", "seen_status": "open", "recovered_cents": 4500}, headers=h).status_code == 200
    assert c.get(f"/api/findings/{fid}", headers=h).json()["finding"]["recovered_cents"] == 4500


# =========================================================================== H5: the API reads only what it was sent
@pytest.mark.parametrize("no", ["false", "no", "0", "off"])
def test_h5_a_no_written_as_text_never_skips_the_hold_for_old_remittances(site, monkeypatch, no):
    import remitwatch.api as api
    c, line = site.c, site.line
    rate = site.ids["UNDERPAID_VS_OWN_RATE"]
    assert _number(c, PAYER_FAX).status_code == 200
    monkeypatch.setattr(api, "STALE_APPEAL_DAYS", 1)          # the invented remittances are a few days old: behind, here
    r = _send(c, rate, stale_ok=no)
    assert r["status"] == "held" and r["code"] == "stale" and line.sent == {}
    assert _send(c, rate, stale_ok=True)["status"] == "sent"


def test_h5_confirmed_and_use_ai_and_bulk_count_only_as_json_true(site, monkeypatch):
    from remitwatch import notes
    c, line = site.c, site.line
    fid, rate = site.ids["DENIED_MEDICAL_NECESSITY"], site.ids["UNDERPAID_VS_OWN_RATE"]
    assert _number(c, PAYER_FAX).status_code == 200
    conn = sqlite3.connect(site.db)
    conn.execute("UPDATE findings SET signal='DOWNCODE_CHARGE', evidence=? WHERE finding_id=?", (json.dumps({"charge_implies": "99215"}), fid))
    conn.commit()
    conn.close()
    for no in ("false", "no", "0", 1, "yes"):
        assert _send(c, fid, confirmed=no)["code"] == "check_code"
    calls = []
    monkeypatch.setattr(notes, "review_note", lambda *a, **k: calls.append(1) or dict(GOOD_REVIEW, model="m"))
    for off in (False, 0, "0", "false", "no", "off", "OFF"):
        _send(c, fid, confirmed=True, use_ai=off, rebuild=True)
    assert calls == [] and line.sent == {}
    # bulk as text is not a bulk send (and so not held as one)
    monkeypatch.delenv("REMITWATCH_BULK_APPEAL", raising=False)
    assert _send(c, rate, bulk=True)["code"] == "bulk_paused"
    assert _send(c, rate, bulk="0")["status"] == "sent"


BAD_BODIES = [b"not json", b"[1, 2]", b'"appealed"', b"null", b"", b'{"status": ["open"]}', b'{"status": "in_progress", "notes": ["a"]}',
              b'{"status": "in_progress", "notes": {"a": 1}}', b'{"status": "in_progress", "notes": "\\ud800"}',
              b'{"status": "resolved", "recovered_cents": "abc"}', b'{"status": "resolved", "recovered_cents": "1,000"}',
              b'{"status": "resolved", "recovered_cents": [1]}', b'{"status": "resolved", "recovered_cents": NaN}',
              b'{"status": "resolved", "recovered_cents": Infinity}', b'{"status": "in_progress", "seen_status": 5}']


def _bad(c, url, headers):
    out = []
    for raw in BAD_BODIES:
        r = c.post(url, content=raw, headers=dict(headers, **{"Content-Type": "application/json"}))
        out.append((raw, r.status_code, r.json().get("detail") if r.headers.get("content-type", "").startswith("application/json") else r.text))
    return out


def test_h5_malformed_status_bodies_are_refused_plainly_on_the_server(site):
    c = site.c
    fid = site.ids["UNDERPAID_VS_OWN_RATE"]
    for raw, code, detail in _bad(c, f"/api/findings/{fid}/status", XHR):
        assert code == 400 and isinstance(detail, str) and detail, (raw, code, detail)
    for raw, code, detail in _bad(c, "/api/findings/bulk-status", XHR):
        assert code == 400, (raw, code, detail)
    for body in ({"ids": "x", "status": "open"}, {"ids": [1, 2], "status": "open"}, {"ids": [fid], "status": "open", "notes": "x" * 5001}):
        assert c.post("/api/findings/bulk-status", json=body, headers=XHR).status_code == 400
    assert _status(c, fid, status="in_progress", notes="x" * 5001).status_code == 400
    assert _status(c, fid, status="in_progress", notes="x" * 5000).status_code == 200
    assert c.get(f"/api/findings/{fid}").json()["finding"]["status"] == "in_progress"


def test_h5_malformed_status_bodies_are_refused_on_the_serverless_host_before_the_journal(sl):
    c, h, fid = sl.c, sl.h, sl.ids[0]
    n = len(_journal(sl.state))
    for raw, code, detail in _bad(c, f"/api/findings/{fid}/status", h) + _bad(c, "/api/findings/bulk-status", h):
        assert code == 400, (raw, code, detail)
    assert c.post(f"/api/findings/{fid}/status", json={"status": "in_progress", "notes": "x" * 5001}, headers=h).status_code == 400
    assert len(_journal(sl.state)) == n
    b = c.post("/api/findings/bulk-status", json={"ids": [fid, fid, fid], "status": "in_progress"}, headers=h).json()
    assert b["updated"] == 1 and len(_journal(sl.state)) == n + 1
    assert cloud.SERVING_COPY.connection().execute("SELECT COUNT(*) FROM finding_events WHERE finding_id=? AND kind='manual'", (fid,)).fetchone()[0] == 1


# =========================================================================== H7: hiding a reason for a payer
def test_h7_hiding_shows_its_size_first_and_needs_the_owner_and_the_count(site, monkeypatch):
    import remitwatch.api as api
    c = site.c
    monkeypatch.setattr(api, "OWNER_PASSWORD", OWNER)
    monkeypatch.setattr(api, "_OWNER_FAILED", {})
    rate = site.ids["UNDERPAID_VS_OWN_RATE"]
    f = c.get(f"/api/findings/{rate}").json()["finding"]
    q = {"payer_group": f["payer_group"], "signal": f["signal"]}
    p = c.get("/api/hide/preview", params=q).json()
    assert p["n"] == 1 and p["cents"] == f["shortfall_cents"] and p["in_window_n"] == 1 and p["in_window_cents"] == f["shortfall_cents"]
    assert p["owner"] is True and p["future"] is True
    body = dict(q, note="This payer does not pay these", confirm=1)
    r = c.post("/api/hide", json=body, headers=XHR)
    assert r.status_code == 403
    r = c.post("/api/hide", json=body, headers=dict(XHR, **{"x-owner-password": "wrong-guess"}))
    assert r.status_code == 403 and api._OWNER_FAILED
    own = dict(XHR, **{"x-owner-password": OWNER})
    r = c.post("/api/hide", json=dict(body, confirm=2), headers=own)
    assert r.status_code == 409 and r.json()["code"] == "changed" and r.json()["n"] == 1
    assert c.post("/api/hide", json=dict(body, confirm="1"), headers=own).status_code == 400
    assert c.post("/api/hide", json=dict(body, note="  "), headers=own).status_code == 400
    assert c.get(f"/api/findings/{rate}").json()["finding"]["status"] == "open"
    r = c.post("/api/hide", json=body, headers=own)
    assert r.status_code == 200 and r.json()["hidden"] == 1
    assert c.get(f"/api/findings/{rate}").json()["finding"]["status"] == "dismissed"
    # Show again needs nothing more, and "remove" counts only as JSON true
    assert c.post("/api/hide", json=dict(q, remove="false"), headers=XHR).status_code in (400, 403, 409)
    assert c.post("/api/hide", json=dict(q, remove=True), headers=XHR).status_code == 200
    assert c.get(f"/api/findings/{rate}").json()["finding"]["status"] == "open"


def test_h7_serverless_previews_from_the_copy_it_serves(sl):
    c, h, fid = sl.c, sl.h, sl.ids[0]
    f = c.get(f"/api/findings/{fid}", headers=h).json()["finding"]
    q = {"payer_group": f["payer_group"], "signal": f["signal"]}
    p = c.get("/api/hide/preview", params=q, headers=h).json()
    assert p["n"] == len(sl.ids) and p["owner"] is False
    assert c.post("/api/hide", json=dict(q, note="Never pays it", confirm=p["n"] + 1), headers=h).status_code == 409
    r = c.post("/api/hide", json=dict(q, note="Never pays it", confirm=p["n"]), headers=h)
    assert r.status_code == 200 and r.json()["hidden"] == p["n"]


# =========================================================================== H8: the day the feed reaches
def _payers(tmp_path, stray: str | None, kind: str = "file"):
    """Three payers whose remittances stop on September 1, 2026, and maybe one stray newer file from a fourth, small one.
    EMA's export shows two claims sent and never answered: one on July 20 (due before the feed stops) and one on
    September 20 (due after)."""
    from remitwatch.store import Store
    files = []
    for k, (payer, start) in enumerate((("ANTHEM", 1), ("KY MEDICARE", 100), ("HUMANA", 200))):
        p = tmp_path / f"r{k}.RMT"
        p.write_bytes(remit("".join(clean_visits(start, 25, "99214", "223.65", "117.60")), "100.00", payer=payer, seg_count=99,
                            isa_ctrl=f"{k + 1:09d}", pay_date="20260901"))
        files.append(str(p))
    if stray:
        p = tmp_path / "stray.RMT"
        p.write_bytes(remit(claim(900, svc("99213", "120.00", "0.00", None, "CAS*CO*45*120.00~")), "0.00", payer="AETNA", seg_count=99,
                            isa_ctrl="000000099", pay_date=stray))
        files.append(str(p))
    s = Store(str(tmp_path / "s.sqlite")) if kind == "file" else Store(":memory:", compact=True)
    s.ingest_835_paths(files)
    charge(s.conn, "x", "CB0000700C017", "2026-09-20", "2026-09-21")
    charge(s.conn, "y", "CB0000701C017", "2026-07-20", "2026-07-21")
    s.conn.commit()
    return s


@pytest.mark.parametrize("kind", ["file", "compact"])
@pytest.mark.parametrize("stray", [None, "20261110", "20991231"])
def test_h8_one_stray_remittance_does_not_make_every_payer_look_current(tmp_path, kind, stray):
    from remitwatch.alerts import run_alerts
    from remitwatch.detect import Detector, feed_date
    s = _payers(tmp_path, stray, kind)
    assert feed_date(s.conn) == "2026-09-01"
    Detector(s, run_id="t").run()
    chased = sorted(r[0] for r in s.conn.execute("SELECT patient_control_number FROM findings WHERE signal='NO_ANSWER'"))
    assert chased == ["CB0000701C017"], chased
    assert json.loads(s.conn.execute("SELECT value FROM meta WHERE key='feed:date'").fetchone()[0])["date"] == "2026-09-01"
    out = run_alerts(s.conn)
    s.conn.commit()
    assert out["asof"] == "2026-09-01"
    assert s.conn.execute("SELECT COUNT(*) FROM alerts WHERE kind='STALE_FEED' AND status!='cleared'").fetchone()[0] == 1
    s.close()


def test_h8_the_feed_date_with_fewer_than_three_payers_is_the_newest_day_never_after_today(tmp_path):
    from remitwatch.detect import feed_date
    from remitwatch.store import Store
    files = []
    for k, (payer, day) in enumerate((("ANTHEM", "20260901"), ("AETNA", "20991231"))):
        p = tmp_path / f"r{k}.RMT"
        p.write_bytes(remit("".join(clean_visits(1 + 100 * k, 5, "99214", "223.65", "117.60")), "100.00", payer=payer, seg_count=99,
                            isa_ctrl=f"{k + 1:09d}", pay_date=day))
        files.append(str(p))
    s = Store(str(tmp_path / "s.sqlite"))
    s.ingest_835_paths(files[:1])
    assert feed_date(s.conn) == "2026-09-01"
    s.ingest_835_paths(files[1:])
    assert feed_date(s.conn, today="2026-11-23") == "2026-11-23"
    assert feed_date(sqlite3.connect(":memory:")) is None
    s.close()


def test_h8_the_fax_hold_goes_by_the_day_the_feed_reaches(tmp_path, monkeypatch):
    """A stray newer file must not lift the hold on appeals while most payers' remittances are old."""
    from fastapi.testclient import TestClient

    import remitwatch.api as api
    from remitwatch import appeals
    from remitwatch.detect import Detector
    s = _payers(tmp_path, "20261110")
    Detector(s, run_id="t").run()
    s.close()
    db = str(tmp_path / "s.sqlite")
    api.ensure_server_schema(db)
    monkeypatch.delenv("REMITWATCH_MODE", raising=False)
    monkeypatch.setattr(api, "DB_PATH", db)
    monkeypatch.setattr(api, "PASSWORD", "")
    assert api._data_behind_days() == (datetime.now(timezone.utc).date() - date(2026, 9, 1)).days
    c = TestClient(api.app)
    st = c.get("/api/sync/status").json()
    assert st["feed_date"] == "2026-09-01" and st["days_behind"] == api._data_behind_days()


def _steady_feed(tmp_path, until: str, hole_from: str | None = None, hole_to: str | None = None, kind: str = "file"):
    """Three payers paying every other day from June 1, 2026 to `until`, with nothing at all between hole_from and hole_to
    (exclusive): the weeks a person forgot to put in the folder."""
    from remitwatch.store import Store
    files, day, k = [], date(2026, 6, 1), 0
    while day.isoformat() <= until:
        iso = day.isoformat()
        if not (hole_from and hole_from < iso < hole_to):
            for j, payer in enumerate(("ANTHEM", "KY MEDICARE", "HUMANA")):
                k += 1
                p = tmp_path / f"f{k:04d}.RMT"
                p.write_bytes(remit("".join(clean_visits(10 * k + j, 1, "99214", "223.65", "117.60")), "100.00", payer=payer,
                                    seg_count=99, isa_ctrl=f"{k:09d}", pay_date=iso.replace("-", "")))
                files.append(str(p))
        day += timedelta(days=2)
    s = Store(str(tmp_path / "s.sqlite")) if kind == "file" else Store(":memory:", compact=True)
    s.ingest_835_paths(files)
    return s


@pytest.mark.parametrize("kind", ["file", "compact"])
def test_h8_a_missing_stretch_of_weeks_keeps_the_feed_at_the_day_before_it(tmp_path, kind):
    """Weeks of remittances missing, then the newest week put in: every payer looks current, but nobody can see what the
    payers paid in the missing weeks. The feed reaches only the last day before the hole, so the open-claim pass, the
    stale-feed alert and the fax hold keep saying so until the missing weeks are read."""
    from remitwatch.alerts import run_alerts
    from remitwatch.detect import Detector, feed_date
    s = _steady_feed(tmp_path, "2026-11-20", hole_from="2026-10-09", hole_to="2026-11-16", kind=kind)
    assert feed_date(s.conn, today="2026-11-23") == "2026-10-09"
    Detector(s, run_id="t").run()
    assert json.loads(s.conn.execute("SELECT value FROM meta WHERE key='feed:date'").fetchone()[0])["date"] == "2026-10-09"
    out = run_alerts(s.conn, today="2026-11-23")
    s.conn.commit()
    assert out["asof"] == "2026-10-09"
    assert s.conn.execute("SELECT COUNT(*) FROM alerts WHERE kind='STALE_FEED' AND status!='cleared'").fetchone()[0] == 1
    s.close()


def test_h8_a_steady_feed_and_a_long_weekend_are_not_a_hole(tmp_path):
    from remitwatch.detect import feed_date
    s = _steady_feed(tmp_path, "2026-11-20")
    assert feed_date(s.conn, today="2026-11-23") == "2026-11-20"          # every other day from June 1 reaches the 20th
    s.close()
    t = tmp_path / "b"
    t.mkdir()
    s = _steady_feed(t, "2026-11-20", hole_from="2026-10-31", hole_to="2026-11-06")      # six days without a payment
    assert feed_date(s.conn, today="2026-11-23") == "2026-11-20"
    s.close()


# =========================================================================== H9: fax numbers in ASCII digits
def test_h9_a_fax_number_in_other_digits_is_refused(site):
    assert fax.digits("８００-５５５-０１４２") == "" and fax.digits("٨٠٠٥٥٥٠١٤٢") == "" and fax.digits("1-800-555-0142٣") == ""
    assert fax.digits("(800) 555-0142") == "8005550142" == fax.digits("1 800 555 0142")
    r = _number(site.c, "８００-５５５-０１４２")
    assert r.status_code == 400 and "0 to 9" in r.json()["detail"]
    assert site.c.get("/api/faxes/payers").json()["payers"][0]["numbers"] == {}


# =========================================================================== H10: Find a claim
def test_h10_a_claim_id_typed_with_dashes_or_a_hash_is_found(site):
    c = site.c
    for typed in ("CB00-00300-C017", "#CB0000300C017", "CB0000300C017.", "cb00 00300 c017"):
        items = c.get("/api/claims/search", headers={"X-Search": typed}).json()["items"]
        assert [i["bill"] for i in items][:1] == ["CB0000300"], (typed, items)
    assert c.get("/api/claims/search", headers={"X-Search": "ZZ99999999"}).json().get("partial") is None


def test_h10_serverless_says_its_copy_holds_only_claims_with_items(sl, monkeypatch):
    c, h = sl.c, sl.h
    r = c.get("/api/claims/search", headers=dict(h, **{"X-Search": "ZZ99999999"})).json()
    assert r["items"] == [] and "holds only the claims that have items" in r["partial"] and "EMA" in r["partial"]
    monkeypatch.setenv("REMITWATCH_SERVER_SITE", "https://remitwatch.example.org")
    r = c.get("/api/claims/search", headers=dict(h, **{"X-Search": "ZZ99999999"})).json()
    assert "https://remitwatch.example.org" in r["partial"]


# =========================================================================== H6: a note is faxed only for its patient and visit
OWN_NOTE = ("Progress note\nPatient: Pat Q Sample    PMS ID: EMA77\nDate of Service: 11/01/2026\n"
            "Chief complaint: changing lesion on the back, enlarging over three months. Biopsy taken.\n" + "Exam and plan. " * 20)
OTHER_NOTE = ("Progress note\nPatient: Quarta Zzfourth    DOB: 07/08/1983    MRN: ZZ000224\nDate of Service: 03/15/2026\n"
              "Contact dermatitis of both hands, enlarging over three months.\n" + "Exam and plan. " * 20)
REPORT = ("Encounter & Charge Lines report    Page 1 of 1\nClaim ID | Patient | DOS | CPT | Charge\n"
          + "\n".join(f"CB00009{i:02d}C017 | Zzpatient{i}, Testa | 03/{10 + i:02d}/2026 | 9921{i % 5} | $1{i}0.00" for i in range(6)) + "\n")


def _build(c, fid, text):
    return c.post("/api/appeals/build", data={"finding_id": fid, "text": text, "use_ai": "1"}, headers=XHR)


def test_h6_a_note_for_another_patient_attached_by_a_person_is_held_before_any_fax(site):
    """0b-2: a note naming another patient and another date goes nowhere by itself; it goes only once a person says they
    opened it and it is this patient's visit, which the item's history keeps with who and when."""
    c, line = site.c, site.line
    fid = site.ids["DENIED_MEDICAL_NECESSITY"]
    assert _number(c, PAYER_FAX).status_code == 200
    built = _build(c, fid, OTHER_NOTE)
    assert built.status_code == 200, built.text
    r = _send(c, fid)
    assert r["status"] == "held" and r["code"] == "note_check" and r["confirm"] is True and r["visit_date"] == "2026-11-01", r
    assert r["packet_id"] == built.json()["packet_id"] and line.sent == {}
    for no in ("true", 1, "yes"):
        assert _send(c, fid, note_checked=no)["code"] == "note_check"
    assert line.sent == {}
    r = _send(c, fid, note_checked=True, packet_id=built.json()["packet_id"])
    assert r["status"] == "sent", r
    ev = [e for e in _events(c, fid) if e["kind"] == "note_check"]
    assert len(ev) == 1 and ev[0]["actor"] and "this patient's visit on 2026-11-01" in ev[0]["note"]


def test_h6_a_note_that_shows_its_patient_and_visit_goes_without_a_tick(site):
    c, line = site.c, site.line
    fid = site.ids["DENIED_MEDICAL_NECESSITY"]
    assert _number(c, PAYER_FAX).status_code == 200
    assert _build(c, fid, OWN_NOTE).status_code == 200
    r = _send(c, fid)
    assert r["status"] == "sent", r
    assert not [e for e in _events(c, fid) if e["kind"] == "note_check"]


def test_h6_a_scanned_note_is_held_for_a_person(site):
    c, line = site.c, site.line
    fid = site.ids["DENIED_MEDICAL_NECESSITY"]
    assert _number(c, PAYER_FAX).status_code == 200
    scan = pdf_of("", 1)
    built = c.post("/api/appeals/build", data={"finding_id": fid, "use_ai": "1"}, files={"file": ("scan.pdf", scan, "application/pdf")}, headers=XHR)
    assert built.status_code == 200, built.text
    r = _send(c, fid)
    assert r["code"] == "note_check" and "no text" in r["reason"] and line.sent == {}


def test_h6_a_report_listing_several_patients_is_not_a_visit_note(site, tmp_path):
    """0b-3: refused as a note in the item and on the Upload page, with a plain reason, and never kept."""
    import remitwatch.api as api
    from remitwatch import uploads
    c = site.c
    fid = site.ids["DENIED_MEDICAL_NECESSITY"]
    r = _build(c, fid, REPORT)
    assert r.status_code == 400 and r.json()["detail"] == "This looks like a report listing several patients, not one visit note."
    two_dob = OTHER_NOTE + "\nSecond patient seen: DOB: 01/02/1950\n"
    assert _build(c, fid, two_dob).status_code == 400
    tmp = uploads.incoming(api.DB_PATH)
    with open(tmp, "w") as fh:
        fh.write(REPORT)
    out = uploads.accept(api.DB_PATH, tmp, "visit note.txt", "oda")
    assert out["status"] == "rejected" and out["reason"] == "This looks like a report listing several patients, not one visit note."
    assert _rows(site.db, "SELECT COUNT(*) FROM uploaded_notes") == [(0,)]


@pytest.mark.parametrize("name,data", [("Encounter & Charge Lines printed.csv", pdf_of("Claim ID Patient DOS")),
                                       ("Encounter & Charge Lines.xlsx", pdf_of("x")), ("visit.txt", pdf_of("x")),
                                       ("visit note.pdf", b"Claim ID,Patient,DOS\nCB0000901C017,Zz,03/10/2026\n")])
def test_h6_a_file_whose_name_and_bytes_disagree_is_refused_in_the_uploader(site, name, data):
    import remitwatch.api as api
    from remitwatch import uploads
    tmp = uploads.incoming(api.DB_PATH)
    with open(tmp, "wb") as fh:
        fh.write(data)
    out = uploads.accept(api.DB_PATH, tmp, name, "oda")
    assert out["status"] == "rejected", out
    assert ("is a PDF" in out["reason"]) or ("is not a PDF" in out["reason"]), out["reason"]
    assert _rows(site.db, "SELECT COUNT(*) FROM uploaded_notes") == [(0,)]
