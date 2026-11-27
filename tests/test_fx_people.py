"""Fix wave F5, people and the owner's decision 2's follow-ups (plans/briefs/2026-11-26_fixwave.md, section F5): what a
person records is what they meant, once, on the item it is about, on both hosts.

people-1  push 2's next-step panel reads a Paid amount as the remittance prints it, starts its prompt empty, and saves once
people-2  the check-first hold of an appeal is skipped only by JSON true
people-3  "Mark posted in EMA" works on a Started unposted payment; no message steers a paid line to Lost or Let go
people-4  serverless: an appeal that went out marks the item's heir Sent when the dashboard's copy already shows the heir
people-5  Paid is never recorded in bulk (one item at a time, with the amount paid)
people-6  a history line pressed on a panel drawn before a run renumbered its item says so and names the new one

Ported from the review of 2026-11-26 (the reviewers' and verifiers' tests in the session's private folder, written against
fc73dec). Invented data only: claim ids, members, amounts and dates are made up (tests/test_status_and_sends, tests/test_hotfix2d,
tests/test_states and tests/test_ema_wiring builders). Each case runs on both hosts where the code runs on both: the server's
store file and the serverless site's dashboard copy with its journal.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import threading
import time
from datetime import date, timedelta

import pytest

from remitwatch import cloud, fax
from tests.test_cloud import box  # noqa: F401  (a fixture)
from tests.test_status_and_sends import XHR, _journal, _serve, _server, _serverless

HERE = os.path.dirname(os.path.abspath(__file__))
PAID_ZERO = "Paid needs an amount above $0. If the payer paid nothing, mark it Lost or Let go instead."


def _later(days: int = 60) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def _set_block_serverless(s, fid: str, block: str) -> None:
    """What a run stores on the serverless site: the store image and the dashboard's copy made from it, read again."""
    store, _ = cloud.load_raw(s.state)
    store.conn.execute("UPDATE findings SET appeal_block=? WHERE finding_id=?", (block, fid))
    store.conn.commit()
    cloud.save_raw(s.state, store, log=lambda *a: None)
    cloud.save_serving(s.state, store.conn)
    store.close()
    cloud.SERVING_COPY.ensure(force=True)


def _set_block_server(s, fid: str, block: str) -> None:
    c = sqlite3.connect(s.db)
    c.execute("UPDATE findings SET appeal_block=? WHERE finding_id=?", (block, fid))
    c.commit()
    c.close()


# =========================================================================================== people-2 (H5)
@pytest.mark.parametrize("block", ["likely_paid", "settled_in_ema"])
@pytest.mark.parametrize("said", ["false", "no", "0", "off", 1, [0], {"x": 0}])
def test_people2_a_no_or_a_non_boolean_checked_never_skips_the_check_first_hold(tmp_path, monkeypatch, block, said):
    """H5: a yes that skips a hold counts only as JSON true. An appeal for a line EMA shows settled, or the archive shows
    likely paid, goes only after a person checked it is still unpaid: "false", "no", "0", "off", 1, [0] or {"x": 0} sent as
    `checked` is no such check, and nothing is faxed."""
    s = _server(tmp_path, monkeypatch, n=1)
    fid = s.ids[0]
    _set_block_server(s, fid, block)
    first = s.c.post("/api/appeals/send", json={"finding_id": fid, "stale_ok": True}, headers=XHR).json()
    assert first["status"] == "held" and first["code"] == "check_first", first           # the hold, as designed
    r = s.c.post("/api/appeals/send", json={"finding_id": fid, "stale_ok": True, "checked": said}, headers=XHR).json()
    assert r.get("status") == "held" and r.get("code") == "check_first" and s.line.sent == {}, (
        f"block={block} checked={said!r}: answered status={r.get('status')!r} code={r.get('code')!r}; faxes on the line: {len(s.line.sent)}")


def test_people2_serverless_a_text_yes_is_no_check_either(box, monkeypatch):  # noqa: F811
    s = _serverless(box, monkeypatch, n=1)
    fid = s.ids[0]
    _set_block_serverless(s, fid, "likely_paid")
    for said in ("true", "yes", 1):
        r = s.c.post("/api/appeals/send", json={"finding_id": fid, "stale_ok": True, "checked": said}, headers=s.h).json()
        assert r.get("status") == "held" and r.get("code") == "check_first" and s.line.sent == {}, (said, r)
    r = s.c.post("/api/appeals/send", json={"finding_id": fid, "stale_ok": True, "checked": True}, headers=s.h).json()
    assert r["status"] == "sent" and len(s.line.sent) == 1, r                       # JSON true is the person's check


def test_people2_control_json_true_still_sends(tmp_path, monkeypatch):
    s = _server(tmp_path, monkeypatch, n=1)
    fid = s.ids[0]
    _set_block_server(s, fid, "likely_paid")
    r = s.c.post("/api/appeals/send", json={"finding_id": fid, "stale_ok": True, "checked": True}, headers=XHR).json()
    assert r["status"] == "sent" and len(s.line.sent) == 1, r


# =========================================================================================== people-5 (H4, CH-10)
def test_people5_server_bulk_paid_is_refused_and_changes_nothing(tmp_path, monkeypatch):
    """H4/CH-10: Paid takes the amount the payer paid, above $0, one item at a time. A bulk Paid carries no amount: it is
    refused (400) and no item changes; before, each item closed as Paid with its shortfall as the recovery nobody typed,
    and an item worth $0 closed as Paid with $0."""
    s = _server(tmp_path, monkeypatch, n=2)
    a, b = s.ids
    c = s.conn()
    c.execute("UPDATE findings SET shortfall_cents=0 WHERE finding_id=?", (b,))
    c.commit()
    c.close()
    r = s.c.post("/api/findings/bulk-status", json={"ids": [a, b], "status": "resolved", "seen": {a: "open", b: "open"}}, headers=XHR)
    c = s.conn()
    got = {k: (st, rec) for k, st, rec in c.execute("SELECT finding_id, status, recovered_cents FROM findings WHERE finding_id IN (?,?)", (a, b))}
    events = c.execute("SELECT COUNT(*) FROM finding_events WHERE kind='manual' AND finding_id IN (?,?)", (a, b)).fetchone()[0]
    c.close()
    assert r.status_code == 400 and "one item at a time" in r.json()["detail"], (r.status_code, r.json())
    assert all(v == ("open", None) for v in got.values()) and events == 0, got
    # the other statuses still go in bulk (control)
    r = s.c.post("/api/findings/bulk-status", json={"ids": [a, b], "status": "in_progress", "seen": {a: "open", b: "open"}}, headers=XHR)
    assert r.status_code == 200 and r.json()["updated"] == 2, r.json()


def test_people5_serverless_bulk_paid_is_refused_and_nothing_is_journaled(box, monkeypatch):  # noqa: F811
    from tests.test_cloud import cycle, raw_conn
    s = _serverless(box, monkeypatch, n=2)
    a, b = s.ids
    r = s.c.post("/api/findings/bulk-status", json={"ids": [a, b], "status": "resolved", "seen": {a: "open", b: "open"}}, headers=s.h)
    assert r.status_code == 400 and "one item at a time" in r.json()["detail"], (r.status_code, r.json())
    assert not [x for x in _journal(s.state) if x.get("kind") in ("status", "bulk_status")]
    time.sleep(1.05)
    out = cycle(s.state, s.client)
    assert "error" not in out, out
    conn = raw_conn(s.state)
    got = [tuple(x) for x in conn.execute("SELECT status, recovered_cents FROM findings WHERE finding_id IN (?,?)", (a, b))]
    assert got == [("open", None), ("open", None)], got


# =========================================================================================== people-4 (mr-4, H2)
def _renumbered_while_sending(tmp_path, monkeypatch, serverless: bool):
    """A To do downcode item is being faxed (its send reserved) when a run gives its money a new id (the claim files prove
    the billed level: tests/test_hotfix2d). Returns what is needed to finish the send the way appeals_send does."""
    from tests import test_hotfix2d as h
    if not serverless:
        from remitwatch import actions
        from remitwatch.detect import Detector
        s = h._downcode_store(tmp_path, "file")
        c = s.conn
        old = h._first_downcode(c)
        line_key = c.execute("SELECT line_key FROM findings WHERE finding_id=?", (old,)).fetchone()[0]
        actions.apply_fax(c, h._reserved(old))
        c.commit()
        h._claim_files_arrive(s)
        Detector(s, run_id="r2").run()
        s.close()
        client, line, api = h._site(tmp_path, monkeypatch, str(tmp_path / "s.sqlite"))
        return dict(api=api, old=old, line_key=line_key, client=client, h=XHR, conn=lambda: sqlite3.connect(str(tmp_path / "s.sqlite")))
    root, client, state = h._cloud_box(tmp_path, monkeypatch)
    h._write_remits(root / "Trizetto Data", h._downcode_claims(), "d", 900)
    assert "error" not in h._cycle(state, client)
    store, _ = cloud.load_raw(state)
    old = h._first_downcode(store.conn)
    line_key = store.conn.execute("SELECT line_key FROM findings WHERE finding_id=?", (old,)).fetchone()[0]
    h._claim_files_arrive(store)
    cloud.save_raw(state, store, log=lambda *a: None)
    store.close()
    cloud.record_action(state, {"kind": "fax", "record": h._reserved(old)})        # what api._reserve journals
    assert "error" not in h._cycle(state, client, force_detect=True)               # folds it, then gives the money a new id
    c, hdr, api = _serve(root, monkeypatch)
    cloud.SERVING_COPY.ensure(force=True)          # this instance's copy, refreshed while the line had the fax (45 s)
    return dict(api=api, old=old, line_key=line_key, state=state, cycle_client=client, client=c, h=hdr)


@pytest.mark.parametrize("host", ["server", "serverless"])
def test_people4_the_send_marks_the_heir_sent_on_both_hosts(tmp_path, monkeypatch, host):
    """mr-4 / H2: an appeal pressed on an item that a run renumbered while the line had the fax: when the send finishes,
    appeals_send calls _sent_once_faxed with the id it was pressed on. The heir is Sent on both hosts, so its follow-ups
    start; on a serverless host whose copy already shows the heir the Sent is journaled for the heir."""
    x = _renumbered_while_sending(tmp_path, monkeypatch, host == "serverless")
    api, old = x["api"], x["old"]
    kept = api._sent_once_faxed(old, "Faxed to Anthem / BCBS at (800) 555-0142 (fax F5, 6 pages).", "biller", False)
    assert kept is None                                       # the item is still To do: it is to be marked Sent
    if host == "server":
        conn = x["conn"]()
    else:
        from tests.test_cloud import cycle, raw_conn
        statuses = [a for a in _journal(x["state"]) if a.get("kind") == "status"]
        assert len(statuses) == 1, statuses
        time.sleep(1.05)
        assert "error" not in cycle(x["state"], x["cycle_client"])
        conn = raw_conn(x["state"])
    heir = conn.execute("SELECT finding_id FROM findings WHERE line_key=? AND COALESCE(stale,0)=0", (x["line_key"],)).fetchone()[0]
    st = conn.execute("SELECT status FROM findings WHERE finding_id=?", (heir,)).fetchone()[0]
    assert heir != old
    assert st == "appealed", f"{host}: the appeal went out, but its item's heir is {st!r}"


def test_people4_a_status_recorded_for_an_old_id_is_journaled_for_its_heir(tmp_path, monkeypatch):
    """_record_status on a serverless host whose copy shows the heir: the status is journaled for the heir (actions.heir_of),
    with the status the heir shows as what it was changed from, never dropped as an id the copy does not hold."""
    x = _renumbered_while_sending(tmp_path, monkeypatch, serverless=True)
    api, old = x["api"], x["old"]
    assert api._record_status([old], "appealed", "Faxed (invented)", None, "biller") == 1
    got = [a for a in _journal(x["state"]) if a.get("kind") == "status"]
    assert len(got) == 1 and got[0]["finding_id"] != old and got[0]["seen"] == "open", got


def test_people4_a_sent_that_reaches_no_item_is_an_error_and_logged(tmp_path, monkeypatch, capsys):
    """A Sent for an id neither the dashboard's copy nor its heirs know is no silent 0: _record_status says so in the
    server's log (the finding id only), and _sent_once_faxed tells the caller its status could not be recorded."""
    x = _renumbered_while_sending(tmp_path, monkeypatch, serverless=True)
    api = x["api"]
    ghost = "f" * 24
    assert api._record_status([ghost], "appealed", "Faxed", None, "biller") == 0
    err = capsys.readouterr().err
    assert ghost in err and "no item" in err, err
    kept = api._sent_once_faxed(ghost, "Faxed to the payer (fax F9).", "biller", False)
    assert kept and "could not be marked Sent" in kept, kept
    assert not [a for a in _journal(x["state"]) if a.get("kind") == "status"]


NUMBER = "8005550142"


def _hold_the_line(line, monkeypatch):
    entered, gate = threading.Event(), threading.Event()
    real = line.send

    def send(to, pdf, filename):
        entered.set()
        assert gate.wait(120)
        return real(to, pdf, filename)
    monkeypatch.setattr(line, "send", send)
    return entered, gate


def _send_in_thread(api, line, f: dict, old: str) -> tuple[threading.Thread, dict]:
    got: dict = {}

    def go():
        try:
            rec = api._send_fax(line, f, NUMBER, b"%PDF-1.4 invented packet", 1, packet_id="pk-invented-1", purpose="appeal", level=1,
                                actor="biller", subject="Appeal (invented)")
            got["rec"] = rec
            got["kept"] = api._sent_once_faxed(old, f"Faxed to {f.get('payer_group')} at (800) 555-0142 (fax {rec['provider_id']}, 1 pages).",
                                               "biller", False)
        except BaseException as exc:          # noqa: BLE001  (reported by the test)
            got["err"] = repr(exc)
    t = threading.Thread(target=go)
    t.start()
    return t, got


@pytest.mark.parametrize("refreshed", [True, False], ids=["copy_refreshed_mid_send", "copy_not_refreshed"])
def test_people4_serverless_the_whole_send_marks_the_heir_sent(tmp_path, monkeypatch, refreshed):
    """The real send from its reservation (api._send_fax, then api._sent_once_faxed, the two calls that end appeals_send) while
    a run renumbers the item. The line is held while the run lands; with the copy refreshed before the send ends (this
    instance last looked for a newer copy more than 45 seconds ago) or not, the heir ends Sent with the fax on file, and
    its follow-ups start."""
    from tests import test_hotfix2d as h
    from tests.fake_fax import FakeLine
    root, client, state = h._cloud_box(tmp_path, monkeypatch)
    h._write_remits(root / "Trizetto Data", h._downcode_claims(), "d", 900)
    assert "error" not in h._cycle(state, client)
    c, hdr, api = _serve(root, monkeypatch)                # the dashboard instance (REMITWATCH_MODE=serverless)
    line = FakeLine()
    monkeypatch.setattr(fax, "provider", lambda env=None: line)     # this host sends (REMITWATCH_FAX_SENDER=serverless)
    items = c.get("/api/findings?signal=DOWNCODE_CHARGE&limit=50", headers=hdr).json()["items"]
    old = sorted(items, key=lambda i: i.get("patient_control_number") or "")[0]["finding_id"]
    f = api.finding_detail(old, "ok")["finding"]
    line_key = f["line_key"]
    entered, gate = _hold_the_line(line, monkeypatch)
    t, got = _send_in_thread(api, line, f, old)
    try:
        assert entered.wait(60), got
        store, _ = cloud.load_raw(state)
        h._claim_files_arrive(store)
        cloud.save_raw(state, store, log=lambda *a: None)
        store.close()
        out = h._cycle(state, client, force_detect=True)
        assert "error" not in out, out
        cloud.SERVING_COPY.checked = 0 if refreshed else time.time() + 3600
    finally:
        gate.set()
        t.join(120)
    assert "err" not in got and got.get("kept") is None, got
    time.sleep(1.05)
    out = h._cycle(state, client)                            # the next run folds the journal
    assert "error" not in out and not out.get("actions_waiting"), out
    serving = h._serving(state, tmp_path)
    heir = serving.execute("SELECT finding_id FROM findings WHERE line_key=? AND COALESCE(stale,0)=0", (line_key,)).fetchone()[0]
    st = serving.execute("SELECT status FROM findings WHERE finding_id=?", (heir,)).fetchone()[0]
    faxed = [tuple(r) for r in serving.execute("SELECT finding_id, provider_id FROM faxes WHERE direction='out'")]
    chased = [d["finding_id"] for d in fax.followups(serving, today=_later())]
    serving.close()
    assert heir != old and faxed == [(heir, got["rec"]["provider_id"])], (heir, old, faxed)
    assert st == "appealed" and chased == [heir], (st, chased)


# =========================================================================================== people-6 (CH-14)
@pytest.mark.parametrize("host", ["server", "serverless"])
def test_people6_a_history_line_for_an_old_id_points_to_its_heir_like_a_status_does(tmp_path, monkeypatch, host):
    """CH-14: an action from a panel drawn before its item got a new number says so and names the new one (409 changed,
    heir), for a status and for a line in the item's history (Mark billed, Copy the steps, Copy the ERA request) alike."""
    x = _renumbered_while_sending(tmp_path, monkeypatch, serverless=host == "serverless")
    c, hdr, old = x["client"], x["h"], x["old"]
    st = c.post(f"/api/findings/{old}/status", json={"status": "in_progress", "seen_status": "open"}, headers=hdr)
    assert st.status_code == 409 and st.json().get("code") == "changed" and st.json().get("heir"), st.json()
    ev = c.post(f"/api/findings/{old}/event", json={"event": "billed", "note": "Billed the other payer (invented)"}, headers=hdr)
    assert ev.status_code == 409 and ev.json().get("code") == "changed" and ev.json().get("heir") == st.json()["heir"], (ev.status_code, ev.json())
    gone = c.post(f"/api/findings/{'e' * 24}/event", json={"event": "instructions"}, headers=hdr)
    assert gone.status_code == 404, gone.json()                                   # an id that never was: still 404


# =========================================================================================== people-3 (CH-10 x route post)
def _states_env(monkeypatch):
    from remitwatch import ema_checks as ec, ema_reports as er, states
    from tests import ema_fixtures as F
    monkeypatch.setattr(er, "CLAIM_HOST_SUFFIX", F.HOST_SUFFIX)
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    for k in ("VERCEL", "REMITWATCH_EMA_REPORTS", "REMITWATCH_EMA_CLAIM_URL", "REMITWATCH_EMA_PAYER_ALIASES",
              "REMITWATCH_EMA_LEDGER", "REMITWATCH_EMA_REPORT_KEEP"):
        monkeypatch.delenv(k, raising=False)
    ec._LAST_POST.clear()
    states._RESPONSE_CACHE.clear()
    states._RECOVERY_CACHE.clear()


def _started_unposted(db: str):
    """A remittance the 835 paid and EMA never posted (REMIT_NOT_POSTED), which a person marked Started; the run then
    stored its state (a person's status decides it, so no route block is stored)."""
    from remitwatch import states, story
    from tests.test_states import TODAY, event, item, new_store
    s = new_store(db)
    item(s, signal="REMIT_NOT_POSTED", category="denial", carc=None, shortfall_cents=8000, paid_cents=8000, status="in_progress",
         evidence={"trace_tail": "7788"})
    event(s, "F1", "manual", at="2026-04-02T15:00:00+00:00", frm="open", to="in_progress", note="Posting it")
    nx = story.explain(s.conn, "F1", today=TODAY)["next_step"]
    assert nx["route"] == "post" and "mark_posted" in nx["buttons"], nx            # the panel offers the button
    row = dict(s.conn.execute("SELECT * FROM findings WHERE finding_id='F1'").fetchone())
    st = states.state_of(s.conn, row, today=TODAY)
    s.conn.execute("UPDATE findings SET appeal_block=?, work_state=?, state_rule=? WHERE finding_id='F1'",
                   (st.get("appeal_block"), st.get("work_state"), st.get("rule")))
    s.conn.commit()
    return s, st


def test_people3_mark_posted_on_a_started_unposted_payment_is_done_on_the_server(tmp_path, monkeypatch):
    """The payer paid; EMA never posted it. A person pressed Started, then after the run "Mark posted in EMA" (route post),
    which sends Paid with $0: the money already came, so it closes the item. It used to be refused with "If the payer paid
    nothing, mark it Lost or Let go instead", steering a paid remittance to the weekly scoreboard as lost."""
    from tests.test_hotfix2d import _site
    _states_env(monkeypatch)
    db = str(tmp_path / "store.sqlite")
    s, st = _started_unposted(db)
    assert st["appeal_block"] is None and st["rule"] == "person_started"           # what the run stores for it
    s.close()
    client, _line, _api = _site(tmp_path, monkeypatch, db)
    r = client.post("/api/findings/F1/status", json={"status": "resolved", "notes": "Posted in EMA", "recovered_cents": 0,
                                                     "seen_status": "in_progress"}, headers=XHR)
    assert r.status_code == 200, r.json()
    c = sqlite3.connect(db)
    assert tuple(c.execute("SELECT status, recovered_cents FROM findings WHERE finding_id='F1'").fetchone()) == ("resolved", 0)
    c.close()


def test_people3_mark_posted_on_a_started_unposted_payment_is_done_on_the_serverless_site(tmp_path, monkeypatch):
    """The same on the serverless site: checked against the dashboard's copy and journaled; the next run's fold applies it."""
    from remitwatch.state import LocalState
    _states_env(monkeypatch)
    root = tmp_path / "dbx"
    (root / "RemitWatch-state").mkdir(parents=True)
    s, _st = _started_unposted(str(tmp_path / "store.sqlite"))
    state = LocalState(str(root / "RemitWatch-state"))
    cloud.save_serving(state, s.conn)
    c, hdr, _api = _serve(root, monkeypatch)
    r = c.post("/api/findings/F1/status", json={"status": "resolved", "notes": "Posted in EMA", "recovered_cents": 0,
                                                "seen_status": "in_progress"}, headers=hdr)
    assert r.status_code == 200, r.json()
    got = [a for a in _journal(state) if a.get("kind") == "status"]
    assert [(a["finding_id"], a["status"], a["recovered_cents"]) for a in got] == [("F1", "resolved", 0)], got
    from remitwatch import actions
    for a in got:
        assert actions.apply(s.conn, a)
    s.conn.commit()
    assert tuple(s.conn.execute("SELECT status, recovered_cents FROM findings WHERE finding_id='F1'").fetchone()) == ("resolved", 0)
    s.close()


def test_people3_through_the_real_readers_and_a_run(tmp_path, monkeypatch):
    """The verifier's world: two remittances the 835 paid ($120 and $95) that EMA never posted, read through the real
    readers; a poster presses Started on B; the next run goes by; Mark posted in EMA closes A (To do) and B (Started)."""
    from remitwatch import ema_checks as ec, ema_reports as er
    from remitwatch.store import Store
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import _new_store, denial_world, el, items, remit, run
    from tests.test_hotfix2d import _site
    monkeypatch.setattr(er, "CLAIM_HOST_SUFFIX", F.HOST_SUFFIX)
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    monkeypatch.setenv("REMITWATCH_EMA_STRICT", "1")
    for k in ("VERCEL", "REMITWATCH_EMA_REPORTS", "REMITWATCH_EMA_CLAIM_URL", "REMITWATCH_EMA_PAYER_ALIASES",
              "REMITWATCH_EMA_LEDGER", "REMITWATCH_EMA_REPORT_KEEP", "REMITWATCH_EMA_CLOSES"):
        monkeypatch.delenv(k, raising=False)
    ec._LAST_POST.clear()
    svc = "2026-03-02"
    store = _new_store(str(tmp_path / "store.sqlite"))
    ledger = el.Ledger.open(str(tmp_path / "ema_ledger.sqlite"))
    tk_a = remit(store, trace="777000301", paid_on="2026-04-01",
                 claims=[{"pcn": F.claim_id(7), "last": "SAMPLE", "first": "BRAVO", "lines": [(svc, "99214", 200, 120)]}])
    tk_b = remit(store, trace="777000302", paid_on="2026-04-02",
                 claims=[{"pcn": F.claim_id(8), "last": "SAMPLE", "first": "CHARLIE", "lines": [(svc, "99213", 150, 95)]}])
    denial_world(store, ledger, tmp_path)
    ledger.close()
    run(store, today="2026-05-03")
    got = {r["transaction_key"]: r for r in items(store, "signal='REMIT_NOT_POSTED'")}
    assert set(got) == {tk_a, tk_b}, sorted(got)
    a, b = got[tk_a], got[tk_b]
    db = str(tmp_path / "store.sqlite")
    store.close()
    client, _line, _api = _site(tmp_path, monkeypatch, db)
    r = client.post(f"/api/findings/{b['finding_id']}/status", json={"status": "in_progress", "seen_status": "open"}, headers=XHR)
    assert r.status_code == 200, r.json()
    time.sleep(1.1)
    store = Store(db)
    run(store, today="2026-05-03")
    after = {r["transaction_key"]: r for r in items(store, "signal='REMIT_NOT_POSTED'")}
    store.close()
    assert after[tk_b]["status"] == "in_progress" and after[tk_b]["appeal_block"] is None
    nx = client.get(f"/api/findings/{b['finding_id']}/explain").json()["explain"]["next_step"]
    assert nx["route"] == "post" and "mark_posted" in nx["buttons"], nx
    body = {"status": "resolved", "notes": "Posted in EMA", "recovered_cents": 0}
    ok_a = client.post(f"/api/findings/{a['finding_id']}/status", json=dict(body, seen_status="open"), headers=XHR)
    ok_b = client.post(f"/api/findings/{b['finding_id']}/status", json=dict(body, seen_status="in_progress"), headers=XHR)
    assert ok_a.status_code == 200, ok_a.json()
    assert ok_b.status_code == 200, ok_b.json()
    c = sqlite3.connect(db)
    st = dict(c.execute("SELECT finding_id, status FROM findings WHERE finding_id IN (?,?)", (a["finding_id"], b["finding_id"])).fetchall())
    c.close()
    assert st == {a["finding_id"]: "resolved", b["finding_id"]: "resolved"}, st


def test_people3_an_unpaid_line_still_needs_an_amount_above_zero(tmp_path, monkeypatch):
    """Control (CH-10): an item to recover money on, with a $0 Paid, is refused with the reason, and stays as it was."""
    s = _server(tmp_path, monkeypatch, n=1)
    fid = s.ids[0]
    r = s.c.post(f"/api/findings/{fid}/status", json={"status": "resolved", "recovered_cents": 0, "seen_status": "open"}, headers=XHR)
    assert r.status_code == 400 and r.json()["detail"] == PAID_ZERO, r.json()
    c = s.conn()
    assert c.execute("SELECT status FROM findings WHERE finding_id=?", (fid,)).fetchone()[0] == "open"
    c.close()


@pytest.mark.parametrize("host", ["server", "serverless"])
def test_people3_a_line_the_run_found_paid_before_it_was_listed_closes_as_paid_with_nothing_recovered(tmp_path, monkeypatch, box, host):  # noqa: F811
    """The story's own step for an item the run found paid (its money came before the item was listed: appeal_block
    'paid') says "Close it as Paid with $0.00 recovered (Log what happened > The payer paid, amount 0)". That Paid with $0
    is accepted on both hosts; it used to be refused with "mark it Lost or Let go", for a paid line."""
    if host == "server":
        s = _server(tmp_path, monkeypatch, n=1)
        _set_block_server(s, s.ids[0], "paid")
    else:
        s = _serverless(box, monkeypatch, n=1)
        _set_block_serverless(s, s.ids[0], "paid")
    fid = s.ids[0]
    r = s.c.post(f"/api/findings/{fid}/status", json={"status": "resolved", "recovered_cents": 0, "seen_status": "open",
                                                      "notes": "The payer paid"}, headers=s.h)
    assert r.status_code == 200, r.json()


def _started_denial(db: str, paid_on: str):
    """A denial a person marked Started (Anthem, 99213, $150.00 charge, denied March 28), and the same payer's payment of
    the same line ($90.00, line control number 1) on `paid_on`: before the denial or after it. The run stored the person's
    state: no route block."""
    from tests import ema_fixtures as F
    from tests.test_states import denied, event, item, ln, new_store, remit
    s = new_store(db)
    tk = denied(s)
    remit(s, trace=F.check_no(2), paid_on=paid_on, claims=[{"pcn": F.claim_id(1), "pccn": "PCN-A", "received": "2026-03-05",
                                                             "lines": [ln("99213", 150, 90, [("CO", "45", 60)], lcn="1")]}])
    item(s, line_key=f"{tk}-0-0", claim_key=f"{tk}-0", transaction_key=tk, status="in_progress", work_state="work_now",
         state_rule="person_started", appeal_block=None)
    event(s, "F1", "manual", at="2026-04-02T15:00:00+00:00", frm="open", to="in_progress", note="Working it (invented)")
    s.close()


def test_people3_a_line_paid_after_it_was_listed_takes_the_amount_paid_never_lost_or_let_go(tmp_path, monkeypatch):
    """A person Started a denial; the payer then paid the line ($90.00 on April 20). A Paid with $0 is refused, and the
    answer names the payment to type, never "mark it Lost or Let go" (the line is paid); the amount itself is taken."""
    from tests.test_hotfix2d import _site
    _states_env(monkeypatch)
    db = str(tmp_path / "store.sqlite")
    _started_denial(db, "2026-04-20")
    client, _line, _api = _site(tmp_path, monkeypatch, db)
    r = client.post("/api/findings/F1/status", json={"status": "resolved", "recovered_cents": 0, "seen_status": "in_progress"}, headers=XHR)
    assert r.status_code == 400 and "$90.00" in r.json()["detail"] and "Lost" not in r.json()["detail"], r.json()
    r = client.post("/api/findings/F1/status", json={"status": "resolved", "recovered_cents": 9000, "seen_status": "in_progress"}, headers=XHR)
    assert r.status_code == 200, r.json()


def test_people3_a_line_paid_before_it_was_listed_closes_as_paid_with_nothing_recovered(tmp_path, monkeypatch):
    """The same payer had paid the line ($90.00 on March 20) before this denial of another copy of the claim: nothing new
    can come, so a person's Paid with $0.00 is taken."""
    from tests.test_hotfix2d import _site
    _states_env(monkeypatch)
    db = str(tmp_path / "store.sqlite")
    _started_denial(db, "2026-03-20")
    client, _line, _api = _site(tmp_path, monkeypatch, db)
    r = client.post("/api/findings/F1/status", json={"status": "resolved", "recovered_cents": 0, "seen_status": "in_progress"}, headers=XHR)
    assert r.status_code == 200, r.json()
    c = sqlite3.connect(db)
    assert tuple(c.execute("SELECT status, recovered_cents FROM findings WHERE finding_id='F1'").fetchone()) == ("resolved", 0)
    c.close()


# =========================================================================================== people-1 (H4, H1): the page logic
def _node_ok() -> bool:
    return bool(shutil.which("node"))


@pytest.fixture(scope="module")
def xp():
    if not _node_ok():
        pytest.skip("needs node")
    static = os.path.join(os.path.dirname(HERE), "remitwatch", "static")
    r = subprocess.run(["node", os.path.join(HERE, "fx_people_xp.js"), static], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-3000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_people1_the_paid_prompt_starts_empty_with_the_disputed_amount_as_a_hint(xp):
    asked = xp["paid_typed_as_ema_prints_it"]["asked"]
    assert asked[0]["def"] == "" and "The amount in dispute is $34.60" in asked[0]["msg"], asked


def test_people1_an_amount_is_read_as_the_remittance_prints_it(xp):
    assert [c.get("recovered_cents") for c in xp["paid_typed_as_ema_prints_it"]["sent"]] == [125000], xp["paid_typed_as_ema_prints_it"]
    assert [c.get("recovered_cents") for c in xp["paid_typed_with_dollar_sign"]["sent"]] == [125000], xp["paid_typed_with_dollar_sign"]
    assert [c.get("recovered_cents") for c in xp["paid_typed_cents"]["sent"]] == [125050], xp["paid_typed_cents"]


def test_people1_ok_on_the_empty_prompt_never_records_the_disputed_amount(xp):
    r = xp["paid_ok_on_what_the_prompt_holds"]
    assert r["sent"] == [], r
    assert [a["msg"].split("\n\n")[0] for a in r["asked"][1:]] == [
        "Type the amount the payer paid, from the remittance or EMA. If you do not know it yet, leave the item as it is."], r["asked"]


def test_people1_zero_and_bad_input_are_asked_again(xp):
    r = xp["paid_typed_bad_then_cancel"]
    assert r["sent"] == [], r
    whys = [a["msg"].split("\n\n")[0] for a in r["asked"][1:]]
    assert whys == ["Use at most two decimals (dollars and cents).", PAID_ZERO,
                    "That is not an amount. Type it as the remittance or EMA shows it, for example 1,250.00."], whys


def test_people1_zero_is_taken_where_the_item_brings_no_new_money(xp):
    """A line the run found paid before it was listed (its step: Paid with $0.00 recovered): 0 is the answer, never asked
    again with "mark it Lost or Let go"."""
    r = xp["paid_zero_on_a_paid_line"]
    assert [c.get("recovered_cents") for c in r["sent"]] == [0], r
    assert "Type 0" in r["asked"][0]["msg"], r["asked"]


def test_people1_a_double_click_on_save_acts_once(xp):
    assert len(xp["working_double_click"]["sent"]) == 1, xp["working_double_click"]
    assert len(xp["paid_double_click"]["sent"]) == 1 and len(xp["paid_double_click"]["asked"]) == 1, xp["paid_double_click"]


def test_people1_a_double_click_on_go_in_the_decide_box_acts_once(xp):
    assert len(xp["decide_double_click"]["sent"]) == 1, xp["decide_double_click"]


def test_people1_a_panel_opened_by_the_walk_ignores_clicks_for_a_moment(xp):
    """The walk guard (index.html walkGuarded): a panel "open the next" just opened takes no click on its Save or Go."""
    assert xp["save_while_walk_guarded"]["sent"] == [] and xp["decide_while_walk_guarded"]["sent"] == [], xp


def test_people6_copy_the_steps_says_when_its_history_line_did_not_land(xp):
    """Copy the steps and Copy the ERA request copy, then write a line in the item's history; when that line is refused (a
    panel drawn before a run renumbered the item), the panel says so instead of swallowing it."""
    r = xp["copy_steps_history_refused"]
    assert r["copied"] == ["Call the payer (invented steps)."] and r["said"] == ["The steps copied."], r
    assert r["errors"] and "not kept in its history" in r["errors"][-1] and "new number" in r["errors"][-1], r


# =========================================================================================== decision 2's follow-ups
# The owner's notes of 2026-11-26, section 5 (built as written there): a Medicaid member is never billed for a missing
# referral; a later payer that answered with a contractual adjustment only settles the service (Done, its reason shown);
# the bill-the-patient step names the service year and the lists filter by it. Both store kinds (test_decisions' sstore:
# the server's file store and the compact in-memory store of the serverless host); invented payers, members and amounts.
from tests.test_decisions import TODAY2, _crossed_over, _patient_denial, _production_covers, _state, sstore  # noqa: E402,F401
from tests.test_decisions_review import _supplement_answer  # noqa: E402

MEDICAID_GRP = "Testcare Health Medicaid"
MEDCAID_NAME = "WELLCARE SAMPLE MEDCAID OF KENTUCKY"      # the shape of a name EMA and the 837s carry: 'Medcaid'


def _referral(s, *, kind="commercial", deadline="2027-01-31", group=None, cfi=None, payer=None, plan=None, covered=True):
    """A missing referral (PR-288) as archive rule 8 raises it: the payer put the visit's $150.00 on the patient on March 28.
    `cfi`, `payer` and `plan` change what the remittance says about the member's plan."""
    _patient_denial(s, "288", signal="DENIED_REFERRAL", deadline=deadline, payer_kind=kind, covered=covered)
    if group:
        s.conn.execute("UPDATE findings SET payer_group=? WHERE finding_id='F1'", (group,))
    if plan:
        s.conn.execute("UPDATE findings SET plan=? WHERE finding_id='F1'", (plan,))
    if cfi is not None:
        s.conn.execute("UPDATE claims SET claim_filing_indicator=?", (cfi,))
    if payer:
        for t in ("transactions", "claims", "lines"):
            s.conn.execute(f"UPDATE {t} SET payer_name=?", (payer,))
    s.conn.commit()


def test_d2f_a_medicaid_members_missing_referral_is_never_billed(sstore):
    """PR-288 for a member on a Medicaid plan (the item's group is a Medicaid plan): Medicaid's payment is payment in full
    and the referral is the practice's to fix. Work now, never "Bill the patient": the next step gets a retroactive
    referral and sends a corrected claim by the plan's date, or marks it Done when the plan will not take one; no Billed the
    patient button and no statement step."""
    from remitwatch import story
    _referral(sstore, kind="medicaid", group=MEDICAID_GRP)
    st = _state(sstore)
    assert (st["work_state"], st["rule"]) == ("work_now", "bill_patient_denial"), st["state_basis"]
    assert st["state_basis"] == (f"{MEDICAID_GRP} put this service on the patient (PR-288: no referral on file), but the member is on a "
                                 "Medicaid plan, whose payment is payment in full: never a bill to the patient; get a retroactive "
                                 "referral and send a corrected claim"), st["state_basis"]
    n = story.next_step(sstore.conn, "F1", today=TODAY2)
    assert n["action"] == ("Get a retroactive referral and send a corrected claim; if the plan will not take one, mark it Done. The member "
                           f"is on a Medicaid plan ({MEDICAID_GRP}): a missing referral is never billed to the patient. Service year: 2026."), n["action"]
    assert "Bill the patient" not in n["action"] and not any("statement" in x for x in n["steps"]), n
    assert n["steps"][0].startswith("Ask the member's primary care office for a retroactive referral") and "by Jan 31, 2027" in n["steps"][0], n["steps"]
    assert {"mark_sent", "mark_done"} <= set(n["buttons"]) and "billed_patient" not in n["buttons"] and "build_packet" not in n["buttons"], n["buttons"]
    assert n["deadline"] == "2027-01-31" and n["expected_cents"] == 15000, (n["deadline"], n["expected_cents"])


def test_d2f_a_medicaid_members_missing_referral_past_the_plans_windows_is_expired_not_billed(sstore):
    """The same denial past every window of the plan (its date was Sep 24): the corrected claim can no longer go, and the
    member is never billed, so nothing can be recovered: expired, with the reason. A member of a commercial plan in the
    same case is still billed (the owner's decision: a patient's share is not closed by a payer's window)."""
    from remitwatch import story
    _referral(sstore, kind="medicaid", group=MEDICAID_GRP, deadline="2026-09-24")
    st = _state(sstore)
    assert st["work_state"] == "expired", st["state_basis"]
    assert st["state_basis"].startswith(f"{MEDICAID_GRP} put this service on the patient (PR-288: no referral on file), but the member is "
                                        "on a Medicaid plan, whose payment is payment in full: never a bill to the patient. The last "
                                        "window closed on"), st["state_basis"]
    assert "Bill the patient" not in story.next_step(sstore.conn, "F1", today=TODAY2)["action"]


def test_d2f_a_commercial_members_missing_referral_past_the_windows_is_still_billed(sstore):
    from remitwatch import story
    _referral(sstore, deadline="2026-09-24")
    st = _state(sstore)
    assert (st["work_state"], st["rule"]) == ("work_now", "bill_patient_denial"), st["state_basis"]
    assert story.next_step(sstore.conn, "F1", today=TODAY2)["action"].startswith("Bill the patient $150.00:")


def test_d2f_a_plan_named_with_medicaid_misspelt_is_a_medicaid_plan(sstore):
    """The remittance's payer is a company's Kentucky Medicaid plan named with 'Medcaid' (as EMA and the 837s spell it), the
    item stored by an older run as a commercial plan: the name rules read it as Medicaid (codes.normalize_payer, after the
    deadlines merge), so the member is never billed. A plan named on the remittance (fix7c's contract class) with the same
    spelling does the same."""
    from remitwatch import codes, story
    assert codes.normalize_payer(MEDCAID_NAME) == ("WellCare / Ambetter (Centene) Medicaid", "medicaid")
    assert codes.payer_group_kind(MEDCAID_NAME, "HM") == ("WellCare / Ambetter (Centene) Medicaid", "medicaid")
    _referral(sstore, payer=MEDCAID_NAME, cfi="HM")
    n = story.next_step(sstore.conn, "F1", today=TODAY2)
    assert n["action"].startswith("Get a retroactive referral and send a corrected claim;"), n["action"]
    assert "billed_patient" not in n["buttons"], n["buttons"]


def test_d2f_a_plan_named_on_the_remittance_tells_the_member_is_on_medicaid(sstore):
    from remitwatch import story
    _referral(sstore, plan="SAMPLE STATE MEDCAID PLAN", cfi="HM")
    assert story.next_step(sstore.conn, "F1", today=TODAY2)["action"].startswith("Get a retroactive referral and send a corrected claim;")


def test_d2f_a_remittance_that_cannot_tell_the_companys_plans_apart_asks_for_the_coverage_first(sstore):
    """A company's plan with no program in its name, no plan named and an HMO filing indicator (which a company's Medicaid
    plan uses too): the step asks the biller to check the member's coverage before any bill goes out, never a bill on a
    guess. Its buttons let the person act on what the check finds: Billed the patient, Done, or Mark sent for a corrected
    claim with a retroactive referral."""
    from remitwatch import story
    _referral(sstore, cfi="HM")
    st = _state(sstore)
    assert (st["work_state"], st["rule"]) == ("work_now", "bill_patient_denial"), st["state_basis"]
    assert st["state_basis"] == ("Anthem / BCBS put this service on the patient (PR-288: no referral on file), and its remittance does not "
                                 "say whether the member's plan is a Medicaid plan: check the member's coverage before any bill goes "
                                 "out (a Medicaid member is never billed for it; any other member is billed $150.00)"), st["state_basis"]
    n = story.next_step(sstore.conn, "F1", today=TODAY2)
    assert n["action"] == ("Check the member's coverage before any bill goes out: Anthem / BCBS's remittance does not say whether the "
                           "member is on its Medicaid plan. A Medicaid member is never billed for a missing referral; any other member "
                           "is billed $150.00 (PR-288 $150.00: no referral on file). Service year: 2026."), n["action"]
    assert {"billed_patient", "mark_done", "mark_sent"} <= set(n["buttons"]), n["buttons"]
    assert any("retroactive referral" in x for x in n["steps"]), n["steps"]


def test_d2f_a_coverage_check_comes_first_when_the_patients_account_cannot_be_seen_either(sstore):
    from remitwatch import story
    _referral(sstore, cfi="", covered=False)
    st = _state(sstore)
    assert (st["work_state"], st["rule"]) == ("cant_check", "cant_check_patient"), st["state_basis"]
    n = story.next_step(sstore.conn, "F1", today=TODAY2)
    assert n["steps"][0].startswith("First check the member's coverage: Anthem / BCBS's remittance does not say whether the member is "
                                    "on its Medicaid plan"), n["steps"]
    assert n["action"].endswith("Service year: 2026."), n["action"]


@pytest.mark.parametrize("cfi,payer,plan,kind,want", [
    ("MC", "TESTCARE HEALTH", None, "commercial", "yes"),              # the filing indicator says Medicaid
    ("HM", MEDCAID_NAME, None, "commercial", "yes"),                   # the name, misspelt
    ("HM", "TESTCARE HEALTH", "SAMPLE MEDCAID PLAN", "other", "yes"),  # the plan the remittance names
    ("12", "TESTCARE HEALTH", None, "medicaid", "yes"),                # the item's own kind
    ("HM", "TESTCARE HEALTH", None, "other", "unclear"),
    ("ZZ", "TESTCARE HEALTH", None, "commercial", "unclear"),
    ("", "TESTCARE HEALTH", None, "commercial", "unclear"),
    ("11", "TESTCARE HEALTH", None, "", "unclear"),
    ("12", "TESTCARE HEALTH", None, "commercial", "no"),               # a PPO: a commercial product
    ("CI", "TESTCARE HEALTH", None, "commercial", "no"),
    ("HM", "TESTCARE HEALTH", "SAMPLE GOLD PPO", "commercial", "no"),  # a plan is named, and it is not Medicaid
    ("HM", "TESTCARE MEDICARE ADVANTAGE", None, "medicare_advantage", "no"),
    ("MB", "MEDICARE TEST", None, "medicare", "no"),
])
def test_d2f_which_members_are_on_a_medicaid_plan(cfi, payer, plan, kind, want):
    """states.medicaid_member on the item and its own 835 lines alone: what the remittance says decides, and only a
    remittance that names no program at all asks for the coverage. A reversal (status 22) of another payer's answer is not
    the item's plan."""
    from remitwatch import states
    own = {"line_key": "L1", "claim_key": "C1", "status": "1", "payer": payer, "cfi": cfi, "charge": 15000, "paid": 0, "adj": [("PR", "288", 15000)]}
    rev = {"line_key": "L1", "claim_key": "C1", "status": "22", "payer": "KENTUCKY MEDICAID", "cfi": "MC", "charge": -15000, "paid": 0, "adj": []}
    fx = {"item": {"line_key": "L1", "claim_key": "C1", "carc": "288", "payer_kind": kind, "plan": plan, "payer_name": payer},
          "r835": {"rows": [own, rev]}}
    assert states.medicaid_member(fx) == want
    assert states.referral_member(fx) == want
    fx["item"]["carc"] = "27"
    assert states.referral_member(fx) == "no"          # a Medicaid member's other reasons are billed as decided


def test_d2f_a_person_who_billed_a_medicaid_member_is_told_the_plan_is_the_way_before_another_statement(sstore):
    """A person pressed Billed the patient for a Medicaid member's missing referral before this rule: their choice stands
    (it waits, then is work again with nothing paid), and the step says first that another statement is not the way."""
    from remitwatch import actions, story
    _referral(sstore, kind="medicaid", group=MEDICAID_GRP)
    actions.apply_status(sstore.conn, "F1", "appealed", "Billed the patient", actor="Biller One", at="2026-09-01T15:00:00+00:00")
    st = _state(sstore)
    assert (st["work_state"], st["rule"]) == ("work_now", "patient_unpaid"), st["state_basis"]
    n = story.next_step(sstore.conn, "F1", today=TODAY2)
    assert n["steps"][0].startswith(f"Before another statement: the member is on a Medicaid plan ({MEDICAID_GRP})"), n["steps"]
    assert sstore.conn.execute("SELECT status FROM findings WHERE finding_id='F1'").fetchone()[0] == "appealed"


SETTLED_BASIS = ("Medicaid KY answered after it on Apr 6, 2026 and put the service on the practice under its contract (CO-45, $150.00), "
                 "paying nothing and leaving nothing to the member (a Medicaid plan: what it pays is payment in full): settled by another "
                 "payer, so the member is not billed")


def test_d2f_a_later_payer_that_answered_with_a_contractual_adjustment_only_settles_it(sstore):
    """Medicare put a routine visit on the patient (PR-49 $150.00) and sent it on; the member's Medicaid plan answered after
    it with CO-45 on all of it, nothing paid and nothing on the member. Its contract puts the service on the practice, so
    billing the patient would break it: straight to Done, with the reason shown and the later payer's remittance as the
    proof (its row named in the verdict). The step offers Done to close it, never Billed the patient or a Paid amount."""
    from remitwatch import story
    _crossed_over(sstore, "4", "KENTUCKY MEDICAID", "MC", ("CO", "45"))
    st = _state(sstore)
    assert (st["work_state"], st["rule"]) == ("done", "next_payer_contractual"), st["state_basis"]
    assert st["state_basis"] == SETTLED_BASIS, st["state_basis"]
    assert {k: st["proof"][k] for k in ("rule", "cents", "date", "source")} == {"rule": "next_payer_contractual", "cents": 0,
                                                                              "date": "2026-04-06", "source": "835"}, st["proof"]
    ex = story.explain(sstore.conn, "F1", today=TODAY2)
    n, v = ex["next_step"], ex["verdict"]
    assert n["route"] == "nothing" and n["action"] == f"Nothing to do: {SETTLED_BASIS}.", n["action"]
    assert "mark_done" in n["buttons"] and not {"billed_patient", "log_outcome", "mark_billed"} & set(n["buttons"]), n["buttons"]
    assert any(x.startswith("Nothing to bill: press Done") for x in n["steps"]), n["steps"]
    decisive = [r for r in ex["story"]["rows"] if r.get("decisive")]
    assert v["strength"] == "proven" and decisive and all(r["source"] == "835" and r["date"] == "2026-04-06" for r in decisive), (v, decisive)
    assert v["sentence"].startswith(SETTLED_BASIS) and "(row" in v["sentence"], v["sentence"]


def test_d2f_results_count_it_as_settled_by_another_payer_never_moved_to_the_patient(sstore):
    from remitwatch import results
    _crossed_over(sstore, "4", "KENTUCKY MEDICAID", "MC", ("CO", "45"))
    st = _state(sstore)
    f = {"work_state": st["work_state"], "status": "open", "state_basis": st["state_basis"], "ema_outcome": None}
    assert results._closure({}, f, 0) == "secondary"
    assert results.CLOSURE_LABELS["secondary"] == "Settled by another payer"


def test_d2f_a_supplement_that_answered_with_a_contractual_adjustment_only_settles_it_too(sstore):
    from remitwatch import story
    _supplement_answer(sstore, co=150, pr=0, allowed=0)
    st = _state(sstore)
    assert (st["work_state"], st["rule"]) == ("done", "next_payer_contractual"), st["state_basis"]
    assert st["state_basis"] == ("Medicare Supplement carriers answered after it on Apr 6, 2026 and put the service on the practice under "
                                 "its contract (CO-45, $150.00), paying nothing and leaving nothing to the member: settled by another "
                                 "payer, so the member is not billed"), st["state_basis"]
    assert "Bill the patient" not in story.next_step(sstore.conn, "F1", today=TODAY2)["action"]


@pytest.mark.parametrize("answer", [dict(co=30, pr=120, allowed=120), dict(co=50, pr=0, allowed=100, paid=100)],
                         ids=["part_on_the_patient", "part_paid"])
def test_d2f_a_later_payer_that_paid_or_put_some_on_the_patient_does_not_settle_it(sstore, answer):
    """Only a contractual adjustment with nothing paid and nothing on the member settles it: a later payer that put part on
    the patient, or paid part and took the rest off as contractual, leaves the bill (capped, with its caution: review
    r1-5)."""
    _supplement_answer(sstore, **answer)
    st = _state(sstore)
    assert (st["work_state"], st["rule"]) == ("work_now", "bill_patient_denial"), st["state_basis"]


def test_d2f_a_persons_billed_the_patient_stands_over_a_later_contractual_answer(sstore):
    """A person's decision is never overwritten by an automatic one: Billed the patient before the later answer was read
    keeps the item waiting for the patient's payment."""
    from remitwatch import actions
    _crossed_over(sstore, "4", "KENTUCKY MEDICAID", "MC", ("CO", "45"))
    actions.apply_status(sstore.conn, "F1", "appealed", "Billed the patient", actor="Biller One", at="2026-11-19T15:00:00+00:00")
    st = _state(sstore)
    assert (st["work_state"], st["rule"]) == ("waiting", "billed_patient"), st["state_basis"]


@pytest.mark.parametrize("svc,year", [("2024-03-02", "2024"), ("2025-12-31", "2025")])
def test_d2f_the_bill_the_patient_step_names_the_service_year(sstore, svc, year):
    """How old a bill to a patient would be is plain on its step (the owner decides the older ones as a batch: nothing in
    the code decides them). Year-end dates keep their own year."""
    from remitwatch import story
    _patient_denial(sstore, "204", signal="DENIED_PLAN_EXCLUSION")
    _production_covers(sstore, "2024-01-01", "2026-11-20")
    sstore.conn.execute("UPDATE findings SET service_date=?, year=? WHERE finding_id='F1'", (svc, year))
    sstore.conn.execute("UPDATE lines SET service_date=?", (svc,))
    sstore.conn.commit()
    st = _state(sstore)
    assert (st["work_state"], st["rule"]) == ("work_now", "bill_patient_denial"), st["state_basis"]
    n = story.next_step(sstore.conn, "F1", today=TODAY2)
    assert n["action"].startswith("Bill the patient $150.00:") and n["action"].endswith(f" Service year: {year}."), n["action"]


DESK_YEARS_JS = r"""
const fs = require('fs'), vm = require('vm'), path = require('path');
const read = f => fs.readFileSync(path.join(process.argv[2], f), 'utf8');
const html = read('index.html');
const grab = (src, re) => { const m = src.match(re); if (!m) throw new Error('not found: ' + re); return m[0]; };
const helpers = [/const money = [^\n]*\n/, /const money2 = [^\n]*\n/, /const esc = [^\n]*\n/].map(re => grab(html, re)).join('\n');
const ctx = { console, state: { summary: null, filt: {}, lim: {} } };
vm.createContext(ctx);
for (const src of [helpers, read('features.js'), read('today.js'), read('desk.js')]) vm.runInContext(src, ctx);
const out = {};
out.none = ctx.serviceYears({ today: '2026-11-27' }, '');
ctx.state.summary = { years: ['2023', 2024, '2025', '2026'] };
out.held = ctx.serviceYears({ today: '2026-11-27' }, '');
out.linked = ctx.serviceYears({ today: '2026-11-27' }, '2021');
out.chosen = ctx.serviceYears({ today: '2026-11-27' }, '2025');
console.log(JSON.stringify(out));
"""


def test_d2f_the_lists_filter_by_service_year(tmp_path):
    """The lists' More filters carry a Service year picker (desk.js serviceYears: the years the site holds, newest first;
    a year a link chose stays in the picker), its chip says "Services of <year>", and the findings the list asks for are
    filtered by the service year the run stores (findings.year)."""
    if not _node_ok():
        pytest.skip("needs node")
    static = os.path.join(os.path.dirname(HERE), "remitwatch", "static")
    js = tmp_path / "desk_years.js"
    js.write_text(DESK_YEARS_JS, encoding="utf-8")
    r = subprocess.run(["node", str(js), static], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-3000:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["none"] == ["2026", "2025", "2024", "2023"], out
    assert out["held"] == ["2026", "2025", "2024", "2023"] and out["chosen"] == out["held"], out
    assert out["linked"] == ["2021", "2026", "2025", "2024", "2023"], out
    desk = open(os.path.join(static, "desk.js"), encoding="utf-8").read()
    assert "sel('year','Service year',[['','Any service year'],...years.map(y=>[y,'Services of '+y])],f.year||'')" in desk
    assert "chip('year', `Services of ${esc(f.year)}`)" in desk and "year:f.year" in desk


def _later_answer(s, rows):
    """Medicare's PR-49 $150.00 on a routine visit, sent on; the member's Medicaid plan answered after it with `rows`
    ((group, reason, dollars), nothing paid) in place of the builder's CO-45."""
    _crossed_over(s, "4", "KENTUCKY MEDICAID", "MC", ("CO", "45"))
    lk = s.conn.execute("SELECT line_key, claim_key, transaction_key FROM lines WHERE payer_name='KENTUCKY MEDICAID'").fetchone()
    s.conn.execute("DELETE FROM adjustments WHERE line_key=?", (lk[0],))
    s.conn.executemany("INSERT INTO adjustments (transaction_key, claim_key, line_key, level, group_code, reason_code, amount_cents) "
                       "VALUES (?,?,?,?,?,?,?)", [(lk[2], lk[1], lk[0], "line", g, r, round(a * 100)) for g, r, a in rows])
    s.conn.commit()


def test_d2f_the_reason_shown_is_the_later_payers_net_contractual_amount(sstore):
    """A later answer can carry a negative adjustment beside a positive one (CO-45 -$127.81 and CO-B7 $277.81 on a $150.00
    line): the reason shown names every contractual code and the net amount the practice carries ($150.00, the line's
    charge), never the larger of the two (a hand-checker reconciles it to the cent)."""
    _later_answer(sstore, [("CO", "45", -127.81), ("CO", "B7", 277.81)])
    st = _state(sstore)
    assert (st["work_state"], st["rule"]) == ("done", "next_payer_contractual"), st["state_basis"]
    assert "under its contract (CO-45, CO-B7, $150.00), paying nothing" in st["state_basis"], st["state_basis"]


def test_d2f_a_later_answer_with_other_adjustments_beside_the_contractual_one_does_not_settle_it(sstore):
    """Only a contractual adjustment settles it (the owner's words: "a contractual adjustment only"): a later answer that
    took $30.00 off as contractual and the rest as another payer's adjudication (OA-23) keeps the bill, with its caution."""
    from remitwatch import story
    _later_answer(sstore, [("CO", "45", 30), ("OA", "23", 120)])
    st = _state(sstore)
    assert (st["work_state"], st["rule"]) == ("work_now", "bill_patient_denial"), st["state_basis"]
    assert story.next_step(sstore.conn, "F1", today=TODAY2)["steps"][0].startswith("Before billing: Medicaid KY answered after it")


# =========================================================================================== routed from F1's fix round
# A second payer's share taken back in full (F1's money-2: a TAKEBACK item of the second payer's line, evidence
# second_payer). F5 owns, for this change only, the TAKEBACK branch of states._decide those items reach and story's next
# step for them (plans/briefs/2026-11-26_fixwave.md, F5; findings fx-ledger-r1-2 and r2-10). Ported from
# plans/progress/fx-ledger_needs/need_second_payer_share_step.py; invented data, both store kinds.
from tests.review3.r3helpers import clm, sv, visit_paid  # noqa: E402
from tests.test_features import clean_visits  # noqa: E402
from tests.test_fx_ledger import GAP, KY, OTHER, World  # noqa: E402
from tests.test_fx_ledger import kind as ledger_kind  # noqa: E402,F401  (the both-store-kinds fixture)


def _share_taken_back(w, pcn: str, gap=GAP) -> None:
    """Medicare pays $64.00 of a 99214 and leaves $16.00 of coinsurance (PR-2); the supplement pays the $16.00 as second
    payer (claim status 2), then withholds it on a later check (PLB WO naming the claim)."""
    w.remit(clean_visits(1, 25, "99214", "223.65", "80.00"), "20260301", **KY)
    w.remit([clm(pcn, "1", "223.65", "64.00", sv("99214", "223.65", "64.00", 1, "CAS*CO*45*143.65~CAS*PR*2*16.00~", allowed="80.00"),
                 payer_ctl="MC1")], "20260415", **KY)
    w.remit([clm(pcn, "2", "223.65", "16.00", sv("99214", "223.65", "16.00", 1, "CAS*OA*23*207.65~"), payer_ctl="GP1")], "20260425", **gap)
    w.remit([visit_paid("CB0000900C017", who=OTHER, payer_ctl="GP9", paid="16.00")], "20260803",
            plb=f"PLB*1777777772*20261231*WO:5550001 {pcn}*16.00~", **gap)


def _taken_back_item(tmp_path, kind, gap=GAP, today="2026-09-20"):
    w = World(tmp_path, kind)
    _share_taken_back(w, "CB0000300C017", gap)
    w.run("r1", today=today)
    tb = w.rows("signal='TAKEBACK' AND COALESCE(stale, 0) = 0")
    assert len(tb) == 1, "the supplement's $16.00 taken back is one TAKEBACK item"
    return w, tb[0]


@pytest.fixture(params=["file", "compact"])
def tbkind(request):
    return request.param


def test_f1need_a_second_payers_share_taken_back_tells_the_biller_to_bill_the_patient(tmp_path, tbkind):
    """(r1-2) The item's state and the drawer's next step say to bill the patient the $16.00, or dispute the takeback, with
    the bill-the-patient action, never only "match the overpayment letter and post it"."""
    from remitwatch import story
    w, r = _taken_back_item(tmp_path, tbkind)
    out = story.explain(w.store.conn, r["finding_id"], today="2026-09-20")
    nxt, st = out["next_step"] or {}, out["state"] or {}
    assert (st["work_state"], st["rule"]) == ("work_now", "bill_patient"), st["state_basis"]
    assert nxt["route"] == "bill_patient" and nxt["action"].startswith("Bill the patient $16.00, or dispute the takeback:"), nxt["action"]
    assert "mark_billed" in nxt["buttons"] and "mark_sent" in nxt["buttons"], nxt["buttons"]
    assert nxt["expected_cents"] == 1600, nxt["expected_cents"]
    assert any("put $16.00 on the patient's next statement" in x for x in nxt["steps"]), nxt["steps"]
    assert any(x.startswith("If it was not owed, dispute it") for x in nxt["steps"]), nxt["steps"]
    w.store.close()


def test_f1need_a_second_payers_share_taken_back_says_bill_the_patient_or_dispute_everywhere(tmp_path, tbkind):
    """(r2-10) The title, the basis (the worklist's reason) and the stored state say "bill the patient, or dispute the
    takeback", never the primary's "bill the right payer"."""
    w, r = _taken_back_item(tmp_path, tbkind)
    assert "bill the patient" in r["title"]
    assert "bill the right payer" not in (r["state_basis"] or ""), r["state_basis"]
    assert "bill the patient, or dispute the takeback" in (r["state_basis"] or ""), r["state_basis"]
    assert r["state_rule"] == "bill_patient", r["state_rule"]
    w.store.close()


def test_f1need_a_primarys_takeback_keeps_its_own_words(tmp_path, tbkind):
    """Control: Medicare's own $80.00 taken back in full (PLB WO naming the claim) is still "find out why, then dispute or
    bill the right payer": the item's work is with the payer, not the patient."""
    w = World(tmp_path, tbkind)
    pcn = "CB0000301C017"
    w.remit(clean_visits(1, 25, "99214", "223.65", "80.00"), "20260301", **KY)
    w.remit([clm(pcn, "1", "223.65", "80.00", sv("99214", "223.65", "80.00", 1, "CAS*CO*45*143.65~", allowed="80.00"),
                 payer_ctl="MC1")], "20260415", **KY)
    w.remit([visit_paid("CB0000900C017", who=OTHER, payer_ctl="MC9", paid="80.00")], "20260803",
            plb=f"PLB*1777777772*20261231*WO:5550001 {pcn}*80.00~", **KY)
    w.run("r1", today="2026-09-20")
    tb = w.rows("signal='TAKEBACK' AND COALESCE(stale, 0) = 0")
    assert len(tb) == 1, tb
    assert tb[0]["state_rule"] == "fees", tb[0]["state_basis"]
    assert tb[0]["state_basis"].endswith("find out why, then dispute or bill the right payer"), tb[0]["state_basis"]
    w.store.close()


def test_f1need_a_medicaid_plans_share_taken_back_is_never_billed_to_its_member(tmp_path, tbkind):
    """The same takeback by the member's Medicaid plan as second payer: Medicaid's payment is payment in full, so the
    share is not the member's. Inside the plan's window the item stays the takeback's own work (find out why, dispute it,
    or find the plan that covered the day); past it, nothing can be recovered (expired). Neither says bill the patient."""
    from remitwatch import story
    w, r = _taken_back_item(tmp_path, tbkind, gap={"payer": "KENTUCKY MEDICAID", "payer_id": "TMCD1"}, today="2026-08-10")
    assert r["payer_kind"] == "medicaid", r["payer_kind"]
    assert (r["work_state"], r["state_rule"]) == ("work_now", "fees"), r["state_basis"]
    assert "a Medicaid member is not billed for it" in r["state_basis"] and "bill the patient" not in r["state_basis"], r["state_basis"]
    nxt = story.explain(w.store.conn, r["finding_id"], today="2026-08-10")["next_step"]
    assert nxt["route"] == "fees" and "Bill the patient" not in nxt["action"], nxt["action"]
    w.run("r2", today="2026-09-20")
    (late,) = w.rows("signal='TAKEBACK' AND COALESCE(stale, 0) = 0")
    assert late["work_state"] == "expired" and "bill the patient" not in (late["state_basis"] or ""), late["state_basis"]
    w.store.close()


def test_f1need_past_the_dispute_window_the_share_is_still_billed_to_the_patient(tmp_path, tbkind):
    """No payer's window closes a patient's share (as decision 2's patient-group denials): long after the supplement's
    window to dispute its takeback closed, the item still says bill the patient the $16.00, and no longer offers the
    dispute."""
    from remitwatch import story
    w, r = _taken_back_item(tmp_path, tbkind, today="2028-06-01")
    assert (r["work_state"], r["state_rule"]) == ("work_now", "bill_patient"), r["state_basis"]
    assert "bill the patient (the window to dispute the takeback closed on" in r["state_basis"], r["state_basis"]
    nxt = story.explain(w.store.conn, r["finding_id"], today="2028-06-01")["next_step"]
    assert nxt["action"].startswith("Bill the patient $16.00: ") and "dispute" not in nxt["action"], nxt["action"]
    assert "mark_billed" in nxt["buttons"] and "mark_sent" not in nxt["buttons"], nxt["buttons"]
    w.store.close()


# =========================================================================================== routed from F3a (837 pass)
# After "Merge wip/fx-837" (09def4e): the story names an 837 send in plain words, and the never-answered page lists the
# claims sent and not due yet with their due days, and the never-answered claims by the week they were sent, so a lost
# batch stands out. Invented claims, payers and dollars; both hosts.
def test_the_story_names_an_837_send_in_plain_words():
    """F3a's need (plans/progress/fx-837_needs_tests.py): the story's rows from an 837 claim file carry source '837'; the
    drawer labels each source in words and had none for '837', so it showed the bare code."""
    import re
    with open(os.path.join(os.path.dirname(HERE), "remitwatch", "static", "features.js"), encoding="utf-8") as fh:
        js = fh.read()
    m = re.search(r"const XP_SOURCE = \{([^}]*)\}", js)
    assert m and re.search(r"'837'\s*:\s*'837 claim file'", m.group(1)), m.group(1) if m else "no XP_SOURCE"


WAITING = {"n": 3, "billed_cents": 45000, "first_due": "2026-12-01", "last_due": "2026-12-20", "at": "2026-11-24T10:00:00+00:00",
           "claims": [["CB0000W03C017", "Plan B", "2026-11-02", "2026-12-20", 15000],
                      ["CB0000W01C017", "Plan A", "2026-11-01", "2026-12-01", 20000],
                      {"claim": "CB0000W02C017", "payer_group": "Plan A", "sent": "2026-11-01", "due": "2026-12-05", "billed_cents": 10000}],
           "sent_by_week": {"2026-10-05": 6, "2026-10-12": 20, "2026-10-19": 10}}


def _never_answered_store(db: str):
    """Five claims sent the week of October 5 never answered (of six sent), one of twenty the next week and one of ten the
    week after; three claims sent in November are not due yet (one row stored as a dict, as a later build may store it)."""
    from remitwatch.detect import WAITING_837_META
    from tests.test_states import item, new_store
    s = new_store(db)
    sends = [("2026-10-06", "Plan A")] * 5 + [("2026-10-13", "Plan B"), ("2026-10-20", "Plan A")]
    for i, (day_, grp) in enumerate(sends):
        item(s, finding_id=f"NA{i}", signal="NO_ANSWER", category="denial", carc=None, payer_group=grp, shortfall_cents=10000 + i,
             evidence={"sent_837": {"day": day_}})
    s.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (WAITING_837_META, json.dumps(WAITING)))
    s.conn.commit()
    return s


def _check_never_answered(d: dict) -> None:
    w = d["waiting"]
    assert (w["n"], w["billed_cents"], w["first_due"], w["last_due"], w["listed"]) == (3, 45000, "2026-12-01", "2026-12-20", 3), w
    assert [(r["claim"], r["payer_group"], r["sent"], r["due"], r["billed_cents"]) for r in w["claims"]] == [
        ("CB0000W01C017", "Plan A", "2026-11-01", "2026-12-01", 20000), ("CB0000W02C017", "Plan A", "2026-11-01", "2026-12-05", 10000),
        ("CB0000W03C017", "Plan B", "2026-11-02", "2026-12-20", 15000)], w["claims"]
    assert [(x["week"], x["unanswered"], x["sent"]) for x in d["weeks"]] == [("2026-10-05", 5, 6), ("2026-10-12", 1, 20), ("2026-10-19", 1, 10)]
    assert d["stand_out"] == ["2026-10-05"] and d["usual_share"] == 0.1, d


def test_the_never_answered_page_reads_the_waiting_claims_and_the_send_weeks_on_the_server(tmp_path, monkeypatch):
    from tests.test_hotfix2d import _site
    db = str(tmp_path / "store.sqlite")
    _never_answered_store(db).close()
    client, _line, _api = _site(tmp_path, monkeypatch, db)
    r = client.get("/api/never-answered", headers=XHR)
    assert r.status_code == 200, r.text
    _check_never_answered(r.json())


def test_the_never_answered_page_reads_the_dashboards_copy_on_the_serverless_site(tmp_path, monkeypatch):
    from remitwatch.state import LocalState
    root = tmp_path / "dbx"
    (root / "RemitWatch-state").mkdir(parents=True)
    s = _never_answered_store(str(tmp_path / "store.sqlite"))
    cloud.save_serving(LocalState(str(root / "RemitWatch-state")), s.conn)
    s.close()
    c, hdr, _api = _serve(root, monkeypatch)
    assert c.get("/api/never-answered", headers=XHR).status_code == 401          # behind the login, as every page
    r = c.get("/api/never-answered", headers=hdr)
    assert r.status_code == 200, r.text
    _check_never_answered(r.json())


def test_the_never_answered_page_with_nothing_waiting_says_nothing(tmp_path, monkeypatch):
    from tests.test_hotfix2d import _site
    from tests.test_states import new_store
    db = str(tmp_path / "store.sqlite")
    new_store(db).close()
    client, _line, _api = _site(tmp_path, monkeypatch, db)
    d = client.get("/api/never-answered", headers=XHR).json()
    assert d["waiting"]["n"] == 0 and d["waiting"]["claims"] == [] and d["weeks"] == [] and d["stand_out"] == [], d


NA_PAGE_JS = r"""
const fs = require('fs'), vm = require('vm'), path = require('path');
const read = f => fs.readFileSync(path.join(process.argv[2], f), 'utf8');
const html = read('index.html');
const grab = (src, re) => { const m = src.match(re); if (!m) throw new Error('not found: ' + re); return m[0]; };
const helpers = [/const money = [^\n]*\n/, /const money2 = [^\n]*\n/, /const esc = [^\n]*\n/].map(re => grab(html, re)).join('\n');
const ctx = { console, state: { summary: null, filt: {}, lim: {} } };
vm.createContext(ctx);
for (const src of [helpers, read('features.js'), read('today.js'), read('desk.js')]) vm.runInContext(src, ctx);
const d = JSON.parse(process.argv[3]);
const out = { full: ctx.naPanelHtml(d), none: ctx.naPanelHtml({ waiting: { n: 0 }, weeks: [], stand_out: [] }), empty: ctx.naPanelHtml(null) };
d.waiting.n = 5; d.stand_out = [];
out.more = ctx.naPanelHtml(d);
console.log(JSON.stringify(out));
"""


def test_the_never_answered_page_lists_the_waiting_claims_and_marks_the_week_that_stands_out(tmp_path, monkeypatch):
    """The page's own code (desk.js naPanelHtml) on what the route answers: the waiting claims soonest due first with their
    due days, the send weeks with the one that stands out marked and what to do about it; nothing at all when nothing
    waits and no week has a never-answered claim. desk.js asks for it on the never-answered claims' page only."""
    if not _node_ok():
        pytest.skip("needs node")
    from tests.test_hotfix2d import _site
    db = str(tmp_path / "store.sqlite")
    _never_answered_store(db).close()
    client, _line, _api = _site(tmp_path, monkeypatch, db)
    d = client.get("/api/never-answered", headers=XHR).json()
    static = os.path.join(os.path.dirname(HERE), "remitwatch", "static")
    js = tmp_path / "na_page.js"
    js.write_text(NA_PAGE_JS, encoding="utf-8")
    r = subprocess.run(["node", str(js), static, json.dumps(d)], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-3000:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    full = out["full"]
    assert "Sent, waiting for the payer's answer" in full and "3 claims sent ($450.00 billed) are not due yet, due from Dec 1, 2026 to Dec 20, 2026" in full, full
    assert full.index("CB0000W01C017") < full.index("CB0000W02C017") < full.index("CB0000W03C017"), "soonest due first"
    assert "<td>Dec 5, 2026</td>" in full and "$100.00" in full
    assert "Never answered, by the week they were sent" in full and full.count("stands out</span>") == 1, full
    assert "In a usual week 10% of the claims sent get no answer. 1 week stands out" in full
    assert "Oct 5, 2026 <span class=\"chip sev-high\">stands out</span>" in full and "83%" in full, full
    assert out["none"] == "" and out["empty"] == "", out
    assert "No week stands out." in out["more"] and "The first 3 by due day; 2 more come due later." in out["more"], out["more"]
    desk = open(os.path.join(static, "desk.js"), encoding="utf-8").read()
    assert "const naShown = f.kind === 'chase' || f.signal === 'NO_ANSWER';" in desk and "api('/api/never-answered')" in desk
