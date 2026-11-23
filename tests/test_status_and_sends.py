"""The misuse check's second round, on the code the billers use (both hosts):

CH-1   a status pressed on a page drawn before a run cannot undo what the run did by itself (a Paid it found, a reason
       hidden for the payer) when the serverless journal is folded in
CH-4   a send that finishes after the run closed its item as Paid, or after a hide, does not overwrite it with Sent; a
       hide leaves out an item whose fax is going out, and says so
CH-10  Paid takes an amount above $0
CH-14  a panel drawn before a run gave its item a new number is told so, with the new number

Invented data only: claim ids, member numbers, amounts and dates are made up (tests/test_features.claim, tests/test_remitwatch
.remit, tests/test_cloud.py's fake Dropbox)."""
from __future__ import annotations

import base64
import json
import threading
import time
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from remitwatch import cloud, fax
from remitwatch.store import Store
from tests.fake_fax import FakeLine
from tests.test_cloud import DATA, PW, box, cycle, raw_conn  # noqa: F401  (box is a fixture)
from tests.test_features import build_store, claim, clean_visits, svc
from tests.test_remitwatch import remit

XHR = {"X-Requested-With": "remitwatch"}
PAYER_FAX = "(800) 555-0142"
PRACTICE = {"name": "Test Dermatology", "address": "1 Main St, Anytown KY", "tin": "61-0000000", "npi": "1999999999",
            "phone": "(270) 555-0100", "fax": "(270) 555-0101", "email": "billing@example.org"}


def _denied(k: int) -> str:
    """An invented visit (claim 200 + k) denied for medical necessity."""
    return claim(200 + k, svc("99214", "223.65", "0.00", None, "CAS*CO*50*223.65~"))


def _pays(k: int) -> str:
    """The same visit paid on a later remittance: $117.60, the payer's usual rate."""
    return claim(200 + k, svc("99214", "223.65", "117.60", "117.60", "CAS*CO*45*106.05~"))


def _first_files(n_denied: int) -> list[bytes]:
    claims = clean_visits(1, 25, "99214", "223.65", "117.60") + [_denied(k) for k in range(n_denied)]
    return [remit("".join(claims[:13]), "100.00", seg_count=99, isa_ctrl="000000001", pay_date="20261110"),
            remit("".join(claims[13:]), "100.00", seg_count=99, isa_ctrl="000000002", pay_date="20261110")]


def _later_file(n_denied: int) -> bytes:
    return remit("".join(_pays(k) for k in range(n_denied)), "117.60", seg_count=99, isa_ctrl="000000777", pay_date="20261121")


def _serve(root, monkeypatch):
    """The API as the serverless host runs it: the dashboard's copy of the state folder, people's changes journaled."""
    from fastapi.testclient import TestClient

    from remitwatch import api, appeals
    monkeypatch.setenv("REMITWATCH_MODE", "serverless")
    monkeypatch.delenv("REMITWATCH_ALLOW_OPEN", raising=False)
    monkeypatch.setenv("REMITWATCH_STATE_DIR", str(root / "RemitWatch-state"))
    monkeypatch.setattr(api, "PASSWORD", PW)
    monkeypatch.setattr(api, "USERNAME", "oda")
    monkeypatch.setattr(api, "OWNER_PASSWORD", "")
    monkeypatch.setattr(cloud, "SERVING_COPY", cloud.ServingCopy())
    for k, v in PRACTICE.items():
        monkeypatch.setitem(appeals.PRACTICE, k, v)
    h = dict(XHR, Authorization="Basic " + base64.b64encode(f"oda:{PW}".encode()).decode())
    return TestClient(api.app), h, api


def _denied_site(box, monkeypatch, n_denied: int = 1) -> SimpleNamespace:
    """The serverless site loaded by one run: 25 clean visits and `n_denied` visits denied for medical necessity, paid
    2026-11-10, each denial an open item."""
    root, client, state = box
    (root / DATA).mkdir(parents=True, exist_ok=True)
    for n, data in enumerate(_first_files(n_denied)):
        (root / DATA / f"r{n}.RMT").write_bytes(data)
    assert "error" not in cycle(state, client)
    c, h, api = _serve(root, monkeypatch)
    ids = [i["finding_id"] for i in c.get("/api/findings?signal=DENIED_MEDICAL_NECESSITY&limit=50", headers=h).json()["items"]]
    assert len(ids) == n_denied
    return SimpleNamespace(root=root, client=client, state=state, c=c, h=h, api=api, ids=ids)


def _history(conn, fid: str, skip: tuple = ("paid_on_later_remit",)) -> list[tuple]:
    return [tuple(r) for r in conn.execute(f"""SELECT kind, from_status, to_status, actor, note FROM finding_events WHERE finding_id=?
                                              AND kind NOT IN ({','.join('?' * len(skip))}) ORDER BY id""", (fid, *skip))]


# =========================================================================== CH-1: the fold keeps what the run did since
PAID_NOT_APPLIED = "Sent pressed on a page drawn before the run closed it as Paid: not applied."


def test_ch1_serverless_sent_pressed_on_a_page_drawn_before_the_run_paid_it_keeps_the_paid(box, monkeypatch):
    """The misuse check's try 8 (t8_fold): the panel shows a denial To do; a run reads the remittance that pays it and
    closes it as Paid; within the 45 seconds the dashboard keeps its copy the person presses Sent on that panel (the copy
    still says To do, so the check passes and the change is journaled). The next run folds the journal: the Paid and its
    recovery stay, the item's history says the Sent was not applied, and a person can still change it on purpose."""
    s = _denied_site(box, monkeypatch)
    fid = s.ids[0]
    assert s.c.get(f"/api/findings/{fid}", headers=s.h).json()["finding"]["status"] == "open"     # the panel: To do
    time.sleep(1.05)
    (s.root / DATA / "later.RMT").write_bytes(_later_file(1))
    out = cycle(s.state, s.client)
    assert "error" not in out and "detect" in out, out
    conn = raw_conn(s.state)
    assert tuple(conn.execute("SELECT status, recovered_cents FROM findings WHERE finding_id=?", (fid,)).fetchone()) == ("resolved", 11760)
    r = s.c.post(f"/api/findings/{fid}/status", json={"status": "appealed", "seen_status": "open"}, headers=s.h)
    assert r.status_code == 200, r.json()                       # the copy it checked against is the one drawn before the run
    time.sleep(1.05)
    out = cycle(s.state, s.client)
    assert "error" not in out and out["actions_folded"] == 1 and not out.get("actions_waiting"), out
    assert s.state.list(cloud.ACTIONS) == []
    conn = raw_conn(s.state)
    st, rec, auto = tuple(conn.execute("SELECT status, recovered_cents, auto_note FROM findings WHERE finding_id=?", (fid,)).fetchone())
    assert (st, rec) == ("resolved", 11760) and auto, "the run's Paid was overwritten by a Sent pressed before it"
    assert _history(conn, fid) == [("not_applied", "resolved", "resolved", "oda", PAID_NOT_APPLIED)]
    fresh = cloud.ServingCopy().connection()                    # the dashboard's copy, on an instance that starts now
    assert tuple(fresh.execute("SELECT status, recovered_cents FROM findings WHERE finding_id=?", (fid,)).fetchone()) == ("resolved", 11760)
    assert [r[0] for r in fresh.execute("SELECT note FROM finding_events WHERE finding_id=? AND kind='not_applied'", (fid,))] == [PAID_NOT_APPLIED]
    fresh.close()
    # a person who sees it Paid reopens it on purpose: that is applied
    cloud.SERVING_COPY.checked = 0
    r = s.c.post(f"/api/findings/{fid}/status", json={"status": "open", "seen_status": "resolved", "notes": "the payment is a different visit"},
                 headers=s.h)
    assert r.status_code == 200, r.json()
    cycle(s.state, s.client)
    assert raw_conn(s.state).execute("SELECT status FROM findings WHERE finding_id=?", (fid,)).fetchone()[0] == "open"


def test_ch1_serverless_changes_pressed_before_a_reason_was_hidden_keep_the_hide(box, monkeypatch):
    """The same with the owner's hide: it is journaled on another instance while this one's copy still shows both denials
    To do. A Sent on one and a list's Let go (with a note) on the other pass against that copy; folded in after the hide,
    neither is applied, each item's history says so (the note too), and Show again brings both back."""
    s = _denied_site(box, monkeypatch, n_denied=2)
    a, b = s.ids
    pg = s.c.get(f"/api/findings/{a}", headers=s.h).json()["finding"]["payer_group"]
    cloud.SERVING_COPY.journal_checked = time.time()        # this instance read the journal just now: the hide is not on its copy
    cloud.record_action(s.state, {"kind": "hide_rule", "op": "add", "payer_group": pg, "signal": "DENIED_MEDICAL_NECESSITY",
                                  "actor": "owner", "note": "This payer never pays these"})
    r = s.c.post(f"/api/findings/{a}/status", json={"status": "appealed", "seen_status": "open"}, headers=s.h)
    assert r.status_code == 200, r.json()
    r = s.c.post("/api/findings/bulk-status", json={"ids": [b], "status": "dismissed", "notes": "too small to chase", "seen": {b: "open"}},
                 headers=s.h)
    assert r.status_code == 200 and r.json()["updated"] == 1, r.json()
    time.sleep(1.05)
    out = cycle(s.state, s.client)
    assert "error" not in out and out["actions_folded"] == 3 and not out.get("actions_waiting"), out
    conn = raw_conn(s.state)
    assert [conn.execute("SELECT status FROM findings WHERE finding_id=?", (i,)).fetchone()[0] for i in (a, b)] == ["dismissed", "dismissed"]
    hid = ("hidden", "open", "dismissed", "hide rule", "This payer never pays these")
    assert _history(conn, a) == [hid, ("not_applied", "dismissed", "dismissed", "oda",
                                       "Sent pressed on a page drawn before its reason was hidden for this payer: not applied.")]
    assert _history(conn, b) == [hid, ("not_applied", "dismissed", "dismissed", "oda",
                                       "Let go pressed on a page drawn before its reason was hidden for this payer: not applied. "
                                       "Its note: too small to chase")]
    cloud.record_action(s.state, {"kind": "hide_rule", "op": "remove", "payer_group": pg, "signal": "DENIED_MEDICAL_NECESSITY", "actor": "owner"})
    cycle(s.state, s.client)
    conn = raw_conn(s.state)
    assert [conn.execute("SELECT status FROM findings WHERE finding_id=?", (i,)).fetchone()[0] for i in (a, b)] == ["open", "open"]


@pytest.fixture(params=["file", "compact"])
def kind(request):
    return request.param


def _paid_by_the_run(tmp_path, kind: str) -> tuple[Store, str]:
    """A store of either kind (the server's file, the serverless host's compact image) where a run closed a denial as Paid
    on the later remittance that pays it."""
    from remitwatch.detect import Detector
    s = Store(str(tmp_path / "s.sqlite")) if kind == "file" else Store(":memory:", compact=True)
    paths = []
    for n, data in enumerate(_first_files(1) + [_later_file(1)]):
        p = tmp_path / f"r{n}.RMT"
        p.write_bytes(data)
        paths.append(str(p))
    s.ingest_835_paths(paths[:2])
    Detector(s, run_id="r1").run()
    fid = s.conn.execute("SELECT finding_id FROM findings WHERE signal='DENIED_MEDICAL_NECESSITY'").fetchone()[0]
    s.ingest_835_paths(paths[2:])
    Detector(s, run_id="r2").run()
    assert tuple(s.conn.execute("SELECT status, recovered_cents FROM findings WHERE finding_id=?", (fid,)).fetchone()) == ("resolved", 11760)
    return s, fid


def test_ch1_the_fold_keeps_a_runs_paid_against_a_page_drawn_before_it(tmp_path, kind):
    """The fold's rule on both store kinds. An entry whose page showed another status than the item has, where the change
    since was the run's: not applied, one line in the history, whether the entry has its own id or is from before entries
    had one, single or from a list, however often it is applied. A page that showed the Paid (a person reopening it on
    purpose) and an entry that names no status seen (from a release before pages sent it) are applied as before."""
    s, fid = _paid_by_the_run(tmp_path, kind)
    c = s.conn
    lost = {"kind": "status", "finding_id": fid, "status": "lost", "notes": "the payer says no", "actor": "biller",
            "at": "2026-11-21T15:00:00+00:00", "seen": "open", "entry": "e1"}
    done, left, wrote = cloud.apply_actions(c, [("actions/1.json", lost)])
    assert (done, left, wrote) == (["actions/1.json"], [], True)
    paid = ("resolved", 11760)
    assert tuple(c.execute("SELECT status, recovered_cents FROM findings WHERE finding_id=?", (fid,)).fetchone()) == paid
    line = ("not_applied", "resolved", "resolved", "biller",
            "Lost pressed on a page drawn before the run closed it as Paid: not applied. Its note: the payer says no")
    assert _history(c, fid) == [line]
    assert cloud.apply_actions(c, [("actions/1.json", lost)])[2] is False          # applied again: nothing more
    # from before entries had their own id; and a list's Paid with an amount: each kept out once
    old = {"kind": "status", "finding_id": fid, "status": "appealed", "actor": "biller2", "at": "2026-11-21T15:00:05+00:00", "seen": "open"}
    bulk = {"kind": "bulk_status", "ids": [fid], "status": "in_progress", "actor": "biller3", "at": "2026-11-21T15:00:09+00:00",
            "seen": {fid: "in_progress"}, "entry": "e3"}
    for _ in range(2):
        cloud.apply_actions(c, [("actions/2.json", old), ("actions/3.json", bulk)])
    assert tuple(c.execute("SELECT status, recovered_cents FROM findings WHERE finding_id=?", (fid,)).fetchone()) == paid
    assert _history(c, fid) == [line, ("not_applied", "resolved", "resolved", "biller2",
                                       "Sent pressed on a page drawn before the run closed it as Paid: not applied."),
                                ("not_applied", "resolved", "resolved", "biller3",
                                 "Started pressed on a page drawn before the run closed it as Paid: not applied.")]
    # a page that showed it Paid: the person's decision, applied
    cloud.apply_actions(c, [("actions/4.json", {"kind": "status", "finding_id": fid, "status": "open", "actor": "biller",
                                                "at": "2026-11-21T15:01:00+00:00", "seen": "resolved", "entry": "e4"})])
    assert c.execute("SELECT status FROM findings WHERE finding_id=?", (fid,)).fetchone()[0] == "open"
    s.close()
    (tmp_path / "again").mkdir()
    s, fid = _paid_by_the_run(tmp_path / "again", kind)
    cloud.apply_actions(s.conn, [("actions/5.json", {"kind": "status", "finding_id": fid, "status": "appealed", "actor": "biller",
                                                     "at": "2026-11-21T15:02:00+00:00", "entry": "e5"})])
    assert s.conn.execute("SELECT status FROM findings WHERE finding_id=?", (fid,)).fetchone()[0] == "appealed"
    s.close()


# =========================================================================== CH-4: a send that finishes after a change
KEPT_PAID = "It was Paid by the time the fax went out, so its status was left as it was. Look at it: the payer has this appeal now."


def _under(n: int) -> list[str]:
    """Clean visits at the payer's usual rate ($117.60 for 99214) and `n` visits allowed less, each by its own amount: each
    an open item a letter answers (no visit note needed)."""
    return clean_visits(1, 25, "99214", "223.65", "117.60") + [
        claim(300 + k, svc("99214", "223.65", f"{110 - k}.00", f"{110 - k}.00", f"CAS*CO*45*{113.65 + k:.2f}~")) for k in range(n)]


def _recent() -> str:
    return (date.today() - timedelta(days=7)).strftime("%Y%m%d")


def _paid_by_the_run_now(conn, fid: str) -> None:
    """What a run writes when it finds the payment that pays an item: Paid, its recovery, its automatic note."""
    conn.execute("""UPDATE findings SET status='resolved', recovered_cents=760, recovered_date='2026-11-23', prev_status='open',
                    auto_note='Paid on a later remittance (2026-11-23, trace T9, $7.60)' WHERE finding_id=?""", (fid,))
    conn.execute("""INSERT INTO finding_events (finding_id, at, kind, from_status, to_status, amount_cents, actor, note)
                    VALUES (?, '2026-11-23T10:17:00+00:00', 'paid_on_later_remit', 'open', 'resolved', 760, 'system', 'Paid on a later remittance')""",
                 (fid,))
    conn.commit()


def _while_sending(c, body: dict, headers: dict, line, monkeypatch, during) -> dict:
    """Appeal by fax for one item, with the fax line taking its time (SRFax's POST with the PDF in it); `during` runs while
    the line has it. Returns the send's answer."""
    entered, gate = threading.Event(), threading.Event()
    real = line.send

    def send(to, pdf, filename):
        entered.set()
        assert gate.wait(30)
        return real(to, pdf, filename)
    monkeypatch.setattr(line, "send", send)
    got: dict = {}
    t = threading.Thread(target=lambda: got.update(r=c.post("/api/appeals/send", json=body, headers=headers)))
    t.start()
    try:
        assert entered.wait(30), "the send never reached the line"
        during()
    finally:
        gate.set()
        t.join(60)
    assert got["r"].status_code == 200, got["r"].json()
    return got["r"].json()


def _server(tmp_path, monkeypatch, n: int = 1) -> SimpleNamespace:
    """The server: a store file with `n` items a letter answers, paid last week, the payer's appeal fax number saved."""
    import sqlite3

    from remitwatch.detect import Detector
    from tests.test_hotfix2d import _site
    s = build_store(tmp_path, _under(n), pay_date=_recent())
    Detector(s, run_id="t").run()
    s.close()
    db = str(tmp_path / "s.sqlite")
    c, line, api = _site(tmp_path, monkeypatch, db)
    ids = [i["finding_id"] for i in c.get("/api/findings?signal=UNDERPAID_VS_OWN_RATE&limit=50").json()["items"]]
    assert len(ids) == n
    assert c.post("/api/faxes/payer", json={"payer_group": "Anthem / BCBS", "number": PAYER_FAX}, headers=XHR).status_code == 200
    return SimpleNamespace(c=c, line=line, api=api, db=db, ids=ids, h=XHR, conn=lambda: sqlite3.connect(db))


def _serverless(box, monkeypatch, n: int = 1) -> SimpleNamespace:
    """The serverless site with the same items, loaded by one run; this host sends faxes (a fake line)."""
    root, client, state = box
    (root / DATA).mkdir(parents=True, exist_ok=True)
    claims = _under(n)
    for k, i in enumerate(range(0, len(claims), 10)):
        (root / DATA / f"r{k:03d}.RMT").write_bytes(remit("".join(claims[i:i + 10]), "100.00", seg_count=99, isa_ctrl=f"{k + 1:09d}",
                                                         pay_date=_recent()))
    assert "error" not in cycle(state, client)
    c, h, api = _serve(root, monkeypatch)
    line = FakeLine()
    monkeypatch.setattr(fax, "provider", lambda env=None: line)
    ids = [i["finding_id"] for i in c.get("/api/findings?signal=UNDERPAID_VS_OWN_RATE&limit=50", headers=h).json()["items"]]
    assert len(ids) == n
    assert c.post("/api/faxes/payer", json={"payer_group": "Anthem / BCBS", "number": PAYER_FAX}, headers=h).status_code == 200
    return SimpleNamespace(root=root, client=client, state=state, c=c, h=h, api=api, line=line, ids=ids)


def _journal(state) -> list[dict]:
    return [json.loads(state.get(m["name"])) for m in state.list(cloud.ACTIONS)]


def test_ch4_a_send_that_finishes_after_the_run_paid_its_item_keeps_the_paid(tmp_path, monkeypatch):
    """The misuse check's try 8 (t8_races, 2): while SRFax has the appeal, the run closes the item as Paid. The send that
    then finishes records the fax and says so in the item's history, and leaves the Paid and its recovery as they are."""
    s = _server(tmp_path, monkeypatch)
    fid = s.ids[0]

    def during():
        conn = s.conn()
        _paid_by_the_run_now(conn, fid)
        conn.close()
    r = _while_sending(s.c, {"finding_id": fid, "stale_ok": True}, s.h, s.line, monkeypatch, during)
    assert r["status"] == "sent" and r.get("kept") == KEPT_PAID, r
    d = s.c.get(f"/api/findings/{fid}").json()
    f = d["finding"]
    assert (f["status"], f["recovered_cents"]) == ("resolved", 760) and f["auto_note"], "the send overwrote the run's Paid"
    assert [(x["direction"], x["status"]) for x in d["faxes"]] == [("out", "queued")]          # the fax that went out is on record
    assert not any(e["kind"] == "manual" for e in d["events"])
    assert [e["note"] for e in d["events"] if e["kind"] == "fax"] == [
        f"Faxed to Anthem / BCBS at {PAYER_FAX} (fax {r['fax_id']}, {r['pages']} pages). {KEPT_PAID}"]


def test_ch4_a_reason_hidden_while_an_appeal_goes_out_leaves_that_item_out_and_says_so(tmp_path, monkeypatch):
    """The misuse check's try 8 (t8_races, 1): the owner hides the item's reason for its payer while SRFax has its appeal.
    The preview and the hide leave that item out and say how many were left out; the send then marks it Sent; the other
    item of that reason goes to Let go."""
    s = _server(tmp_path, monkeypatch, n=2)
    going, other = s.ids
    q = {"payer_group": "Anthem / BCBS", "signal": "UNDERPAID_VS_OWN_RATE"}
    seen: dict = {}

    def during():
        seen["preview"] = s.c.get("/api/hide/preview", params=q).json()
        seen["hide"] = s.c.post("/api/hide", json=dict(q, note="This payer never pays more", confirm=1), headers=XHR)
        seen["status"] = s.c.get(f"/api/findings/{going}").json()["finding"]["status"]
    r = _while_sending(s.c, {"finding_id": going, "stale_ok": True}, s.h, s.line, monkeypatch, during)
    p = seen["preview"]
    assert (p["n"], p.get("in_flight_n")) == (1, 1), p
    h = seen["hide"]
    assert h.status_code == 200 and (h.json()["hidden"], h.json().get("left_out")) == (1, 1), h.json()
    assert seen["status"] == "open"
    assert r["status"] == "sent" and "kept" not in r, r
    assert [s.c.get(f"/api/findings/{i}").json()["finding"]["status"] for i in (going, other)] == ["appealed", "dismissed"]


def test_ch4_serverless_a_send_that_finishes_after_the_run_paid_its_item_keeps_the_paid(box, monkeypatch):
    """The same on the serverless host: a run closes the item as Paid while SRFax has the appeal, and this instance's copy
    has the Paid by the time the send finishes. Nothing that would mark it Sent is journaled; the fax and a line in its
    history are, and the next run's fold keeps the Paid and its recovery."""
    s = _serverless(box, monkeypatch)
    fid = s.ids[0]

    def during():
        store, _ = cloud.load_raw(s.state)
        _paid_by_the_run_now(store.conn, fid)
        cloud.save_raw(s.state, store, log=lambda *a: None)
        cloud.save_serving(s.state, store.conn)
        store.close()
        cloud.SERVING_COPY.ensure(force=True)                  # the instance's copy is refreshed with the run's Paid
        assert s.c.get(f"/api/findings/{fid}", headers=s.h).json()["finding"]["status"] == "resolved"
    r = _while_sending(s.c, {"finding_id": fid, "stale_ok": True}, s.h, s.line, monkeypatch, during)
    assert r["status"] == "sent" and r.get("kept") == KEPT_PAID, r
    assert "status" not in [a["kind"] for a in _journal(s.state)]
    assert s.c.get(f"/api/findings/{fid}", headers=s.h).json()["finding"]["status"] == "resolved"
    time.sleep(1.05)
    out = cycle(s.state, s.client)
    assert "error" not in out and not out.get("actions_waiting"), out
    conn = raw_conn(s.state)
    assert tuple(conn.execute("SELECT status, recovered_cents FROM findings WHERE finding_id=?", (fid,)).fetchone()) == ("resolved", 760)
    assert [tuple(x) for x in conn.execute("SELECT direction, status FROM faxes WHERE finding_id=?", (fid,))] == [("out", "queued")]
    assert [x[0] for x in conn.execute("SELECT note FROM finding_events WHERE finding_id=? AND kind='fax'", (fid,))] == [
        f"Faxed to Anthem / BCBS at {PAYER_FAX} (fax {r['fax_id']}, {r['pages']} pages). {KEPT_PAID}"]


def test_ch4_serverless_a_reason_hidden_while_an_appeal_goes_out_leaves_that_item_out(box, monkeypatch):
    """The hide on the serverless host while this host faxes an appeal of that reason: the preview and the hide leave the
    item out and say so, the journal entry names it, and the run's fold leaves it out too; the send marks it Sent."""
    s = _serverless(box, monkeypatch, n=2)
    going, other = s.ids
    q = {"payer_group": "Anthem / BCBS", "signal": "UNDERPAID_VS_OWN_RATE"}
    seen: dict = {}

    def during():
        seen["preview"] = s.c.get("/api/hide/preview", params=q, headers=s.h).json()
        seen["hide"] = s.c.post("/api/hide", json=dict(q, note="This payer never pays more", confirm=1), headers=s.h)
    r = _while_sending(s.c, {"finding_id": going, "stale_ok": True}, s.h, s.line, monkeypatch, during)
    p = seen["preview"]
    assert (p["n"], p.get("in_flight_n")) == (1, 1), p
    h = seen["hide"]
    assert h.status_code == 200 and (h.json()["hidden"], h.json().get("left_out")) == (1, 1), h.json()
    assert r["status"] == "sent" and "kept" not in r, r
    assert [a.get("skip") for a in _journal(s.state) if a["kind"] == "hide_rule"] == [[going]]
    time.sleep(1.05)
    out = cycle(s.state, s.client)
    assert "error" not in out and not out.get("actions_waiting"), out
    conn = raw_conn(s.state)
    assert [conn.execute("SELECT status FROM findings WHERE finding_id=?", (i,)).fetchone()[0] for i in (going, other)] == ["appealed", "dismissed"]
