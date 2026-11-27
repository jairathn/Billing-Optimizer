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
