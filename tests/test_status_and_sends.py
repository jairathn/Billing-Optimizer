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
import time
from types import SimpleNamespace

import pytest

from remitwatch import cloud
from remitwatch.store import Store
from tests.test_cloud import DATA, PW, box, cycle, raw_conn  # noqa: F401  (box is a fixture)
from tests.test_features import claim, clean_visits, svc
from tests.test_remitwatch import remit

XHR = {"X-Requested-With": "remitwatch"}
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
