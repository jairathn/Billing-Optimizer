"""GUARD lens L4, every write checks what the person saw: failing tests on invented data for writes the snapshot makes
blind (it applies them to whatever the item, number, report, alert or fax is now, not to what the person was shown).

Run against the frozen snapshot, never inside it:
    cd <this folder> && PYTHONDONTWRITEBYTECODE=1 SNAP=<snapshot> python3 -m pytest -q -p no:cacheprovider test_l4_stale_writes.py

Each test states the guard: the write carries what the person saw (seen_status, seen_number, seen_decision, the alert's
version, the fax's state), and it lands only where that is still true, at the moment and on the copy where it is applied
(the request on a server, the journal fold on Vercel); otherwise nothing changes and the refusal is said and kept. Every
patient, claim, number and amount here is made up (tests/test_features builders)."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest

SNAP = os.environ.get("SNAP") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "snap")
sys.path.insert(0, SNAP)
os.environ.setdefault("REMITWATCH_ALLOW_OPEN", "1")
os.environ.setdefault("REMITWATCH_MODE", "server")

from remitwatch import actions, cloud  # noqa: E402
from remitwatch.detect import Detector  # noqa: E402
from remitwatch.state import LocalState  # noqa: E402
from tests.test_features import build_store, claim, clean_visits, svc  # noqa: E402

XHR = {"X-Requested-With": "remitwatch"}
OWNER = "an-owners-own-passphrase-9"        # invented
NOW = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")       # noqa: E731


def _denied(i: int) -> str:
    return claim(i, svc("99214", "223.65", "0.00", None, "CAS*CO*50*223.65~", dos="20261101"), dos="20261101")


def _world(tmp_path):
    """An invented store after one analysis: 25 clean visits and three visits denied as not medically necessary."""
    store = build_store(tmp_path, clean_visits(1, 25, "99214", "223.65", "117.60") + [_denied(300), _denied(301), _denied(302)],
                        pay_date="20261110")
    Detector(store, run_id="t1").run()
    ids = [r[0] for r in store.conn.execute("SELECT finding_id FROM findings WHERE status='open' AND category='denial' ORDER BY finding_id")]
    assert len(ids) >= 2, "the invented world must have denied items to work"
    return store, ids


def _status(db_or_conn, fid: str) -> str:
    conn = sqlite3.connect(db_or_conn) if isinstance(db_or_conn, str) else db_or_conn
    try:
        return conn.execute("SELECT status FROM findings WHERE finding_id=?", (fid,)).fetchone()[0]
    finally:
        if isinstance(db_or_conn, str):
            conn.close()


def _run_closed_as_paid(conn: sqlite3.Connection, fid: str) -> None:
    """What the hourly (server) or daily (Vercel) run writes when a later remittance pays the claim: Paid, with its note
    and its event, stamped by the run."""
    at = NOW()
    conn.execute("""UPDATE findings SET status='resolved', auto_note='Paid on a later remittance (invented)', prev_status='open',
                    recovered_cents=shortfall_cents, recovered_date=?, status_changed_at=? WHERE finding_id=?""", (at[:10], at, fid))
    conn.execute("""INSERT INTO finding_events (finding_id, at, kind, from_status, to_status, amount_cents, actor, note, run_id)
                    VALUES (?,?,'paid_on_later_remit','open','resolved',NULL,'system','invented later payment','t2')""", (fid, at))
    conn.commit()


@pytest.fixture()
def site(tmp_path, monkeypatch):
    """The server site (AWS) on the invented store, open for a developer's machine, no owner's password unless a test sets one."""
    from fastapi.testclient import TestClient

    import remitwatch.api as api
    store, ids = _world(tmp_path)
    db = store.db_path
    store.close()
    api.ensure_server_schema(db)
    for k in ("DROPBOX_APP_KEY", "DROPBOX_APP_SECRET", "DROPBOX_REFRESH_TOKEN", "REMITWATCH_STATE_DIR", "SRFAX_ACCESS_ID",
              "SRFAX_ACCESS_PWD", "ANTHROPIC_API_KEY", "VERCEL", "VERCEL_ENV", "CRON_SECRET", "TRIZETTO_SFTP_USER", "MODMED_CLIENT_ID",
              "REMITWATCH_DB"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    monkeypatch.setattr(api, "DB_PATH", db)
    monkeypatch.setattr(api, "PASSWORD", "")
    monkeypatch.setattr(api, "OWNER_PASSWORD", "")
    monkeypatch.setattr(api, "SHOW_PHI", False)
    monkeypatch.setattr(api, "_FAILED", {})
    monkeypatch.setattr(api, "_OWNER_FAILED", {})
    conn = sqlite3.connect(db)
    pg, sig = conn.execute("SELECT payer_group, signal FROM findings WHERE finding_id=?", (ids[0],)).fetchone()
    conn.close()
    return type("Site", (), {"c": TestClient(api.app), "api": api, "db": db, "ids": ids, "payer": pg, "signal": sig})


# --------------------------------------------------------------------------------------------- L4-1 one item, stale panel
def test_l4_1_a_panel_drawn_before_the_run_paid_the_item_cannot_mark_it_sent(site):
    """Chaos 0a-4 on the push-2 code: the panel showed To do, the run then found the payment. The person's Sent must not
    replace the Paid (detect.save would treat it as a deliberate override and erase the recovery for good)."""
    fid = site.ids[0]
    conn = sqlite3.connect(site.db)
    _run_closed_as_paid(conn, fid)
    conn.close()
    r = site.c.post(f"/api/findings/{fid}/status", json={"status": "appealed", "seen_status": "open"}, headers=XHR)
    assert r.status_code == 409, f"the server applied a change made on a screen that showed another status ({r.status_code})"
    assert _status(site.db, fid) == "resolved"


# --------------------------------------------------------------------------------------------- L4-2 bulk, stale list
def test_l4_2_a_bulk_let_go_from_a_list_drawn_before_a_fax_leaves_the_faxed_item_alone(site):
    """Chaos 0a-5 on the push-2 code: another tab faxed item A and marked it Sent; a list drawn before that lets go A and B.
    A must be left as it is and named; B is let go."""
    a, b = site.ids[:2]
    conn = sqlite3.connect(site.db)
    actions.ensure_fax_tables(conn)
    actions.apply_status(conn, a, "appealed", "Faxed to the payer (invented)", None, "biller")
    actions.upsert_fax(conn, {"fax_key": "out:fake:aaaa:appeal:1:1", "direction": "out", "finding_id": a, "purpose": "appeal", "level": 1,
                              "status": "queued", "provider_id": "F-1", "created_at": NOW(), "number": "8005550142"})
    conn.commit()
    conn.close()
    r = site.c.post("/api/findings/bulk-status", json={"ids": [a, b], "status": "dismissed", "notes": "invented reason",
                                                       "seen": {a: "open", b: "open"}}, headers=XHR)
    assert r.status_code == 200
    assert _status(site.db, a) == "appealed", "a faxed appeal was let go from a list drawn before it was faxed: its follow-ups stop"
    assert a in (r.json().get("changed") or []), "the answer must name what was left out and why"
    assert _status(site.db, b) == "dismissed"


# ------------------------------------------------------------------------- L4-3 the run's save writes back what it read
@pytest.mark.parametrize("moment", ["after_the_read", "at_the_delete"])
def test_l4_3_a_status_saved_while_the_servers_run_saves_is_not_rolled_back(tmp_path, moment):
    """Server: Detector.save reads every finding (status included), works out heirs and states for tens of seconds (51 s on
    a copy of the live store, 16,165 items, with push 2's states), then deletes the table and writes back what it read. A
    person's Let go saved in between is undone (after_the_read: the item is To do again while its history says Let go), or,
    when it lands as the save starts writing (at_the_delete), the run itself dies on 'database is locked' and its whole
    analysis is lost. The run must merge (or hold the write lock and re-read), never write back a status it read before a
    person changed it, and never fail because a person worked. The hook is the connection's trace callback, which fires
    before a statement takes any lock: no code of the run is replaced."""
    store, ids = _world(tmp_path)
    db, fid = store.db_path, ids[0]
    store.close()
    from remitwatch.store import Store
    said: dict = {}

    def person_presses_let_go_now(sql: str) -> None:
        s = sql.strip()
        if s == "SELECT * FROM findings":
            said["armed"] = True
            if moment == "after_the_read":
                return
        if said.get("done") or not said.get("armed") or (moment == "at_the_delete" and s != "DELETE FROM findings"):
            return
        if moment == "after_the_read" and s == "SELECT * FROM findings":
            return
        said["done"] = True
        try:
            conn = sqlite3.connect(db, timeout=2)       # the API's own connection, as set_status opens it
            said["applied"] = actions.apply_status(conn, fid, "dismissed", "invented reason", None, "biller")
            conn.commit()
            conn.close()
        except sqlite3.OperationalError as exc:         # the run held the write lock: the API would wait, then say so
            said["blocked"] = str(exc)
    again = Store(db)
    again.conn.set_trace_callback(person_presses_let_go_now)
    try:
        Detector(again, run_id="t2").run()
    except sqlite3.OperationalError as exc:
        pytest.fail(f"the run died because a person saved a status while it saved ({exc}): its whole analysis is lost")
    finally:
        again.conn.set_trace_callback(None)
    if said.get("blocked"):
        again.close()
        return                                          # the run kept the lock for its whole save: nothing was lost
    st = _status(again.conn, fid)
    hist = [r[0] for r in again.conn.execute("SELECT to_status FROM finding_events WHERE finding_id=? AND kind='manual'", (fid,))]
    again.close()
    assert said.get("applied") is True and hist == ["dismissed"]
    assert st == "dismissed", f"the run's save rolled a person's Let go back to {st!r}; the history still says Let go"


# ------------------------------------------------------------------- L4-4 Vercel: the fold applies a journal entry blind
def test_l4_4_a_journal_entry_made_on_a_screen_the_run_has_since_changed_is_not_applied_blind(tmp_path):
    """Vercel: the request is checked against the serving copy, then journaled; the run folds the journal later. An entry
    written after the run's fold and before its new copy was served (the run takes minutes and chains) lands on an item the
    run closed as Paid. Let go then stands for ever (detect.save keeps a person's Let go over a payment). The fold must
    compare the entry's seen status with the item's status now and keep a record of the refusal instead."""
    store, ids = _world(tmp_path)
    fid = ids[0]
    _run_closed_as_paid(store.conn, fid)            # the 10:17 run, after the page was drawn
    entry = {"kind": "status", "finding_id": fid, "status": "dismissed", "notes": "invented reason", "actor": "biller",
             "at": NOW(), "seen": "open"}
    cloud.apply_actions(store.conn, [("actions/20261123T101900000000-aaaaaa.json", entry)])
    assert _status(store.conn, fid) == "resolved", "the fold let a stale screen's Let go replace the run's Paid"
    kept = store.conn.execute("SELECT COUNT(*) FROM finding_events WHERE finding_id=? AND at=? AND kind!='manual'", (fid, entry["at"])).fetchone()[0]
    assert kept >= 1, "the refused change must be kept where the person sees it (the item's history)"


# --------------------------------------------------------------------- L4-5 Vercel: three changes in one second
def test_l4_5_three_changes_by_one_person_within_one_second_all_count(tmp_path):
    """Chaos 0d-8 on the push-2 code: Sent, To do, Sent within one second ends as To do on Vercel (the replay takes the
    third for the first: same second, status and person)."""
    store, ids = _world(tmp_path)
    fid = ids[0]
    st = LocalState(str(tmp_path / "state"))
    at = NOW()
    for s in ("appealed", "open", "appealed"):
        cloud.record_action(st, {"kind": "status", "finding_id": fid, "status": s, "notes": None, "actor": "biller", "at": at, "seen": None})
    cloud.apply_actions(store.conn, cloud.read_actions(st))
    assert _status(store.conn, fid) == "appealed"


# ------------------------------------------------------------------- L4-6 Vercel: arrival order decides on an instance
def test_l4_6_two_copies_end_with_the_latest_change_whatever_order_the_journal_arrives_in(tmp_path):
    """Chaos 0a-6 on the push-2 code: an instance applies its own entry at once and another instance's older one when it
    reads the journal (every 15 s), so the older entry wins there until the next run."""
    store, ids = _world(tmp_path)
    fid = ids[0]
    db = store.db_path
    store.conn.commit()
    store.close()
    other = str(tmp_path / "copy2.sqlite")
    shutil.copy(db, other)
    st = LocalState(str(tmp_path / "state"))
    t1 = (datetime.now(timezone.utc) - timedelta(seconds=3)).isoformat(timespec="seconds")
    t2 = NOW()
    n1 = cloud.record_action(st, {"kind": "status", "finding_id": fid, "status": "dismissed", "notes": "phone: invented", "actor": "biller", "at": t1, "seen": "open"})
    n2 = cloud.record_action(st, {"kind": "status", "finding_id": fid, "status": "appealed", "notes": None, "actor": "biller", "at": t2, "seen": "open"})
    entries = dict(cloud.read_actions(st))
    c1, c2 = sqlite3.connect(db), sqlite3.connect(other)
    cloud.apply_actions(c1, [(n1, entries[n1]), (n2, entries[n2])])          # the order recorded
    cloud.apply_actions(c2, [(n2, entries[n2])])                              # the laptop's instance: its own first
    cloud.apply_actions(c2, [(n1, entries[n1])])                              # then the phone's, 15 s later
    s1, s2 = _status(c1, fid), _status(c2, fid)
    c1.close()
    c2.close()
    assert s1 == s2 == "appealed", f"two copies disagree: {s1!r} and {s2!r}"


# ------------------------------------------------------------- L4-7 a settle a person made on a fax that settled since
def test_l4_7_a_persons_did_not_go_out_never_lands_on_a_fax_the_line_has_taken_since(tmp_path):
    """The Faxes page offers 'It did not go out' for a send still 'sending'. The fax run then learns the line took it
    (provider id, queued). The person's answer, made on the older screen (or folded later on Vercel), turns it into a
    failed try, and the retry faxes the patient's records again. A settle must land only on a fax still unsettled."""
    store, ids = _world(tmp_path)
    conn = store.conn
    actions.ensure_fax_tables(conn)
    key = "out:fake:bbbb:appeal:1:1"
    actions.upsert_fax(conn, {"fax_key": key, "direction": "out", "finding_id": ids[0], "purpose": "appeal", "level": 1, "status": "sending",
                              "provider_id": None, "created_at": NOW(), "number": "8005550142", "doc": json.dumps({"actor": "biller"})})
    # the fax run (hourly on a server, the daily call on Vercel): SRFax took it
    actions.apply_fax(conn, {"fax_key": key, "provider_id": "F-77", "status": "queued", "updated_at": NOW()})
    # the person's answer from the screen drawn while it was 'sending' (api.faxes_settle -> _settle)
    entry = {"kind": "fax", "record": {"fax_key": key, "status": "failed", "error": "Not sent: a person checked SRFax's sent faxes.",
                                       "doc": {"actor": "biller", "settled_by": "biller"}, "updated_at": NOW()}, "seen": "sending", "at": NOW()}
    cloud.apply_actions(conn, [("actions/20261123T120000000000-bbbbbb.json", entry)])
    got = conn.execute("SELECT status, provider_id FROM faxes WHERE fax_key=?", (key,)).fetchone()
    assert got[0] == "queued", f"a fax the line took is now {got[0]!r}: the retry would fax it again"


# ---------------------------------------------------------------- L4-8 two decisions on one held report
def test_l4_8_a_discard_from_a_screen_drawn_before_someone_applied_the_report_is_refused(site):
    """Data tab: two people see one held A/R ('looks filtered'). One applies it; the other, on the screen drawn before,
    discards it. The second decision silently replaces the first. It must be refused (409) and say who decided what."""
    from remitwatch import ema_reports as er
    conn = sqlite3.connect(site.db)
    er.ensure_schema(conn)
    er._catalog_put(conn, "rep000000000001", commit=True, report="ar", status="held", status_reason="looks filtered (invented)",
                    source_name="invented-ar.xlsx")
    conn.close()
    a = site.c.post("/api/ema/reports/rep000000000001/decision", json={"decision": "apply", "seen_decision": None}, headers=XHR)
    assert a.status_code == 200
    b = site.c.post("/api/ema/reports/rep000000000001/decision", json={"decision": "discard", "seen_decision": None}, headers=XHR)
    conn = sqlite3.connect(site.db)
    row = conn.execute("SELECT status, decision FROM ema_report_files WHERE file_id='rep000000000001'").fetchone()
    conn.close()
    assert b.status_code == 409, f"a decision made on a stale screen replaced another person's ({b.status_code}, now {row})"
    assert row == ("held", "apply")


# ----------------------------------------------------------------- L4-9 a fax number saved over one the person never saw
def test_l4_9_a_number_saved_on_a_screen_that_showed_an_older_number_is_refused(site):
    """Faxes page: the owner replaced the payer's number X with Y. A second page, drawn before, still shows X; its save of
    Z replaces Y (newest wins) and nobody learns Y existed. The save must name the number it replaces (seen_number) and
    be refused (409) when that is no longer the number saved."""
    pg = site.payer
    for n in ("8005550142", "8005550177"):
        assert site.c.post("/api/faxes/payer", json={"payer_group": pg, "purpose": "appeal", "number": n}, headers=XHR).status_code == 200
    r = site.c.post("/api/faxes/payer", json={"payer_group": pg, "purpose": "appeal", "number": "8005550199", "seen_number": "8005550142"}, headers=XHR)
    conn = sqlite3.connect(site.db)
    now = conn.execute("SELECT number FROM payer_fax WHERE payer_group=? AND purpose='appeal'", (pg,)).fetchone()[0]
    conn.close()
    assert r.status_code == 409, "a number was saved over one the person never saw"
    assert now == "8005550177"


# ----------------------------------------------------------- L4-10 the owner's write-off decision, on what the owner saw
def test_l4_10_accepting_a_write_off_pattern_needs_the_owner_and_the_count_shown(site, monkeypatch):
    """Policy page: Accept takes every item of a pattern off the work list, now and in future (decision 14: only the owner's
    Accept does). The route takes any sign-in and no count. It must need the owner's password where one is set, and a
    confirmation equal to the items it accepts now (409 when the pattern grew since the page was drawn)."""
    monkeypatch.setattr(site.api, "OWNER_PASSWORD", OWNER)
    pattern = f"{site.payer}|99214||CO-50"
    conn = sqlite3.connect(site.db)
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('ema:writeoff_policy', ?)",
                 (json.dumps({pattern: {"decision": "pursue", "by": "owner", "at": "2026-11-01T10:00:00+00:00"}}),))
    conn.commit()
    conn.close()
    r = site.c.post("/api/ema/policy", json={"pattern": pattern, "decision": "accept", "name": "invented biller"}, headers=XHR)
    assert r.status_code == 403, f"any sign-in accepted a write-off pattern for the owner ({r.status_code})"


# ----------------------------------------------------------------- L4-11 hiding a reason for a payer (hotfix 2 H7, ported)
def test_l4_11_hiding_a_reason_needs_the_owner_and_the_count_shown(site, monkeypatch):
    """Chaos 0b-5 and 0d-7 on the push-2 code: one link and Enter hides every open item of a reason for a payer, now and in
    future, with no owner's password and no count seen first."""
    monkeypatch.setattr(site.api, "OWNER_PASSWORD", OWNER)
    r = site.c.post("/api/hide", json={"payer_group": site.payer, "signal": site.signal, "note": "invented reason"}, headers=XHR)
    assert r.status_code in (403, 409), f"a hide for a whole payer went through with no owner and no count ({r.status_code}, {r.json()})"
    assert _status(site.db, site.ids[0]) == "open"


# ------------------------------------------------------------------ L4-12 an alert acknowledged, then much worse
def test_l4_12_an_acknowledged_alert_that_gets_much_worse_is_new_again(tmp_path):
    """An acknowledgement is of the alert as it was shown (its severity and size). The same alert re-raised by later runs
    at high severity and ten times the money stays 'acknowledged' and is never mailed. It must come back as new."""
    from remitwatch import alerts as al
    store, _ids = _world(tmp_path)
    conn = store.conn
    shown = {}

    def raise_one(sev: str, n: int, cents: int):
        def evaluate(self):
            self._raise("RATE_CUT", sev, f"Invented payer cut a rate ({n} lines)", {"n": n}, "Invented Payer", "underpayment", None,
                        None, None, n, cents, key=("Invented Payer", "99214"))
            return self.active
        return evaluate
    orig = al.AlertEngine.evaluate
    try:
        al.AlertEngine.evaluate = raise_one("medium", 4, 40000)
        al.AlertEngine(conn, run_id="r1").run()
        aid = conn.execute("SELECT alert_id FROM alerts WHERE kind='RATE_CUT'").fetchone()[0]
        shown["v1"] = tuple(conn.execute("SELECT severity, cents_recent FROM alerts WHERE alert_id=?", (aid,)).fetchone())
        actions.apply_alert_ack(conn, aid)
        conn.commit()
        al.AlertEngine.evaluate = raise_one("high", 60, 900000)
        al.AlertEngine(conn, run_id="r2").run()
    finally:
        al.AlertEngine.evaluate = orig
    st, sev = conn.execute("SELECT status, severity FROM alerts WHERE alert_id=?", (aid,)).fetchone()
    assert sev == "high"
    assert st == "new", f"acknowledged at {shown['v1']}, it is now high and 22 times the money but stays {st!r} and is never mailed"


# ----------------------------------------------------- L4-13 Vercel: a change for an item that is gone is dropped silently
def test_l4_13_a_change_whose_item_is_gone_is_never_dropped_without_a_trace(tmp_path, monkeypatch):
    """Vercel keeps a journal entry whose item the store does not hold (a run gave the line a new id) and gives it up after
    seven days: deleted, with nothing kept that says a person's change was never applied."""
    from remitwatch.state import DropboxState
    from tests.fakes import FakeDropbox
    from tests.test_cloud import DATA, cycle, visits_with_downcodes, write_remits
    for k in ("DROPBOX_APP_KEY", "DROPBOX_APP_SECRET", "DROPBOX_REFRESH_TOKEN", "SMTP_HOST", "ALERT_WEBHOOK_URL", "REPORT_TO", "CRON_SECRET",
              "REMITWATCH_STATE_DIR", "ANTHROPIC_API_KEY", "VERCEL", "VERCEL_ENV", "VERCEL_PROJECT_PRODUCTION_URL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("REMITWATCH_TMP", str(tmp_path / "scratch"))
    monkeypatch.setenv("DROPBOX_ROOT_PATH", f"/{DATA}")
    root = tmp_path / "dbx"
    client = FakeDropbox(str(root))
    state = DropboxState(client, "/RemitWatch-state")
    write_remits(root / DATA, visits_with_downcodes())
    cycle(state, client)
    old = (datetime.now(timezone.utc) - timedelta(days=8)).strftime("%Y%m%dT%H%M%S%f")
    name = f"{cloud.ACTIONS}/{old}-cccccc.json"
    gone_id = "f" * 24
    state.put(name, json.dumps({"kind": "status", "finding_id": gone_id, "status": "dismissed", "notes": "invented reason",
                                "actor": "biller", "at": (datetime.now(timezone.utc) - timedelta(days=8)).isoformat(timespec="seconds"),
                                "seen": "open"}).encode())
    out = cycle(state, client)
    still = any(m["name"] == name for m in state.list(cloud.ACTIONS))
    store, _ = cloud.load_raw(state)
    traced = store.conn.execute("SELECT COUNT(*) FROM finding_events WHERE finding_id=?", (gone_id,)).fetchone()[0]
    store.close()
    status = json.loads(state.get(cloud.STATUS) or b"{}")
    assert still or traced or (status.get("actions_dropped") or out.get("actions_dropped")), \
        "a person's change was deleted from the journal with nothing kept that says it was never applied"


# ----------------------------------------------------------------- L4-14 the same status twice is one decision
def test_l4_14_the_same_status_sent_twice_writes_one_line_in_the_history(tmp_path):
    """Chaos 0a-10 on the push-2 code: a fast double click on the status bar's Sent writes 'To do -> Sent' and 'Sent -> Sent'."""
    store, ids = _world(tmp_path)
    fid = ids[0]
    assert actions.apply_status(store.conn, fid, "appealed", None, None, "biller")
    assert actions.apply_status(store.conn, fid, "appealed", None, None, "biller")
    n = store.conn.execute("SELECT COUNT(*) FROM finding_events WHERE finding_id=? AND kind='manual'", (fid,)).fetchone()[0]
    assert n == 1, f"{n} history lines for one decision"
