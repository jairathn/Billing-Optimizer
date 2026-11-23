"""
GUARD lens L3: the blast radius of one run. Each test drives one run that changes far more than a run normally does, and
asserts what a swing guard must do. On the snapshot (push 2 at 2f9c6ea) they FAIL: that is the finding. The controls at
the end PASS, to show the harness can see a guard where one exists (the vanished check keeps a plain dropped item).

Invented data only (the project's own builders: tests/test_selfcheck.py, tests/test_resolution.py, tests/test_features.py).
Both store kinds where the code runs on both: the server's file store and the compact in-memory store of the serverless host.

Run (read-only on the snapshot, no bytecode written into it):
  cd <this folder> && PYTHONDONTWRITEBYTECODE=1 SNAP=<snapshot> python3 -B -m pytest -q -p no:cacheprovider test_l3_blast_radius.py
"""
from __future__ import annotations

import json
import os
import sys

import pytest

SNAP = os.environ.get("SNAP") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "snap")
sys.path.insert(0, os.path.abspath(SNAP))

from remitwatch import ema_reports as er  # noqa: E402
from remitwatch import fax  # noqa: E402
from remitwatch import selfcheck as sc  # noqa: E402
from remitwatch.actions import apply_status, ensure_fax_tables  # noqa: E402
from remitwatch.detect import Detector  # noqa: E402
from tests import ema_fixtures as F  # noqa: E402
from tests.test_selfcheck import TODAY, _new, add_item, check_of, remit, run  # noqa: E402

TRUE_ITEMS = 40          # the worklist people saw before the run (kept small: the guard must not need a big list)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(er, "CLAIM_HOST_SUFFIX", F.HOST_SUFFIX)
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    for k in ("VERCEL", "REMITWATCH_EMA_REPORTS", "REMITWATCH_EMA_PAYER_ALIASES", "REMITWATCH_EMA_LEDGER", "REMITWATCH_TODAY",
              "REMITWATCH_MEMORY_BUDGET_MB", "REMITWATCH_MAX_SECONDS", "REMITWATCH_CYCLE_BUDGET_SECONDS", "REMITWATCH_STALE_DAYS",
              "REMITWATCH_AUDIT_GAP_CENTS", "REMITWATCH_AUDIT_WASTED_ITEMS", "SRFAX_ACCESS_ID", "SRFAX_ACCESS_PWD"):
        monkeypatch.delenv(k, raising=False)
    sc._GROUPS.clear()


@pytest.fixture(params=["file", "compact"])
def store(request, tmp_path):
    s = _new(request.param, tmp_path)
    yield s
    s.close()


def _baseline(c, n: int = TRUE_ITEMS, **kw) -> None:
    """A clean first run: n listed items, the baseline the next run is compared with."""
    for i in range(n):
        add_item(c, f"BASE-{i:04d}", line_key=f"LB{i:04d}", patient_control_number=f"ZZ01T{i:04d}C001", **kw)
    out = run(c, None, "r1")
    assert out["first"] and not out["held"]


def _held_or_named(out: dict, *ids: str) -> bool:
    """The swing guard acted: the run was held, or a failing check names it."""
    return bool(out["held"]) or any(i in out["failing"] for i in ids)


# ======================================================================================== L3-1: nothing runs the checks
def test_detection_runs_the_self_check_by_itself(store):
    """Detector.run alone (what both cycles call) must leave the self-check's record: today nothing calls
    begin_detection/after_detection, so safe mode, the vanished keep and every invariant are inert (L3-1)."""
    c = store.conn
    for i in range(3):
        remit(c, "2026-10-20", f"ZZ00T000{i}C001", [("99213", 150, 0, [("CO", "197", 150)], "2026-10-01")])
    Detector(store, "d1", today=TODAY).run()
    have = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    ran = "selfcheck_runs" in have and c.execute("SELECT COUNT(*) FROM selfcheck_runs").fetchone()[0] > 0
    assert ran, "Detector.run never ran the self-check: no selfcheck_runs row, safe mode cannot hold anything"


# ======================================================================================== L3-2: a flood passes every check
def test_a_flood_of_new_work_is_held(store):
    """One run adds 1,600 'never answered' items (the stray-835 flood, 0b-1/0e-2): far more than a run brings (the archive's
    largest week brought 439 items). No check fails and nothing holds (L3-2)."""
    c = store.conn
    _baseline(c)

    def flood(cn):
        for i in range(1600):
            add_item(cn, f"FLOOD-{i:05d}", signal="NO_ANSWER", category="denial", actionable="review", carc=None,
                     line_key=None, patient_control_number=f"ZZ02T{i:05d}C001", shortfall_cents=12000, expected_cents=12000,
                     state_basis="No answer from the payer on file 45 days after the claim was sent", run_id="r2",
                     first_seen="2026-11-22T10:18:00+00:00")
    out = run(c, flood, "r2")
    listed = c.execute("SELECT COUNT(*) FROM findings WHERE work_state='work_now'").fetchone()[0]
    assert _held_or_named(out, "swing", "flood"), f"1,600 new items reached Work now ({listed} listed); held={out['held']}, failing={out['failing']}"


# ======================================================================================== L3-3: leaving the list by state
def test_items_that_leave_work_now_by_a_state_change_are_counted(store):
    """One run moves 800 items from Work now to Can't be checked (Production turned stale on day 15; one posted-scope box
    flips them back): the items stay in the table, so check_vanished skips them ('still on record') (L3-3)."""
    c = store.conn
    for i in range(800):
        add_item(c, f"NA-{i:04d}", signal="NO_ANSWER", category="denial", actionable="review", carc=None, line_key=None,
                 patient_control_number=f"ZZ03T{i:04d}C001", state_basis="No answer from the payer on file")
    assert not run(c, None, "r1")["held"]

    def blind(cn):
        cn.execute("""UPDATE findings SET work_state='cant_check',
                      state_basis='No source can show the payer''s answer: no 835 after 2026-10-09. Check the payer''s portal.'""")
    out = run(c, blind, "r2")
    left = c.execute("SELECT COUNT(*) FROM findings WHERE work_state='work_now'").fetchone()[0]
    van = check_of(sc.meta_get(c, sc.K_LAST)["checks"], "vanished")
    assert _held_or_named(out, "vanished", "swing"), (f"800 items left Work now in one run ({left} left); vanished says "
                                                      f"{van['status']}, held={out['held']}")


# ======================================================================================== L3-4: superseded excuses all
def test_a_cut_short_copy_does_not_excuse_the_items_it_drops(store):
    """A cut-short copy of a remittance supersedes the complete one (0e-1, L2-2/L2-3): 27 of its 30 claims are on no current
    remittance any more, their items vanish, and check_vanished files every one under 'superseded' (L3-4)."""
    c = store.conn
    lines = [("99213", 150, 0, [("CO", "197", 150)], "2026-10-01")]
    keys = []
    for i in range(30):
        lk = remit(c, "2026-10-20", f"ZZ04T{i:04d}C001", lines, trace="977000001")[0]
        tk = c.execute("SELECT transaction_key FROM lines WHERE line_key=?", (lk,)).fetchone()[0]
        add_item(c, f"CUT-{i:02d}", line_key=lk, transaction_key=tk, patient_control_number=f"ZZ04T{i:04d}C001")
        keys.append(tk)
    assert not run(c, None, "r1")["held"]

    def cut(cn):
        # the copy names the same payer, trace and date but holds only the first three claims
        kept = [remit(cn, "2026-10-20", f"ZZ04T{i:04d}C001", lines, trace="977000001")[0] for i in range(3)]
        cn.executemany("UPDATE transactions SET superseded=1 WHERE transaction_key=?", [(tk,) for tk in keys])
        cn.execute("DELETE FROM findings WHERE finding_id LIKE 'CUT-%'")
        for i, lk in enumerate(kept):
            add_item(cn, f"CUT-NEW-{i}", line_key=lk, patient_control_number=f"ZZ04T{i:04d}C001", run_id="r2")
    out = run(c, cut, "r2")
    van = check_of(sc.meta_get(c, sc.K_LAST)["checks"], "vanished")
    gone = c.execute("SELECT COUNT(*) FROM findings WHERE finding_id LIKE 'CUT-%' AND finding_id NOT LIKE 'CUT-NEW-%'").fetchone()[0]
    assert van["n"] >= 27 or gone >= 27, (f"27 items whose claims are on no current remittance vanished; vanished routes "
                                          f"{van['info'].get('routes')}, kept {out['kept']}")


# ======================================================================================== L3-5: a wave of closures
def test_a_wave_of_automatic_closures_with_proof_is_held(store):
    """One file (a Production export from the wrong tenant, a mapping error, a duplicate posting) closes 500 items as paid,
    each with a proof row: closure_proof passes and the run is not held (L3-5). The archive's largest day closed 111."""
    c = store.conn
    _baseline(c, 500)

    def close_all(cn):
        for (fid, pcn) in cn.execute("SELECT finding_id, patient_control_number FROM findings").fetchall():
            ev = {"paid_later": {"payment_date": "2026-11-20", "paid_cents": 10000, "trace": "EMA check ending 4242",
                                 "claim": pcn, "source": "EMA Production",
                                 "proof": [{"date": "2026-11-20", "source": "production", "kind": "payment", "amount_cents": 10000,
                                            "text": "EMA posted $100.00 on a check no 835 carries", "decisive": True}]}}
            cn.execute("""UPDATE findings SET status='resolved', work_state='done', auto_note='EMA shows it paid', prev_status='open',
                          recovered_cents=10000, recovered_date='2026-11-20', evidence=? WHERE finding_id=?""", (json.dumps(ev), fid))
            cn.execute("""INSERT INTO finding_events (finding_id, at, kind, from_status, to_status, amount_cents, actor, note, run_id)
                          VALUES (?, '2026-11-22T10:18:00+00:00', 'paid_on_later_remit', 'open', 'resolved', 10000, 'system',
                          'EMA shows it paid', 'r2')""", (fid,))
    out = run(c, close_all, "r2")
    recovered = c.execute("SELECT COALESCE(SUM(recovered_cents), 0) FROM findings WHERE status='resolved'").fetchone()[0]
    assert _held_or_named(out, "swing", "closures"), (f"500 automatic closures and ${recovered / 100:,.0f} counted as recovered in one "
                                                      f"run; held={out['held']}, failing={out['failing']}")


# ======================================================================================== L3-6: hide rules after the run
def test_a_wave_let_go_by_a_hide_rule_after_the_run_is_counted(store):
    """apply_hide_rules lets go every new item an old rule covers, after the save, with no count (L3-6): 300 items gone
    from the list in one run, and check_vanished skips them because they are still in the table."""
    c = store.conn
    _baseline(c, 300)

    def hidden(cn):
        cn.execute("UPDATE findings SET status='dismissed', work_state='accepted', state_basis='Let go by a hide rule'")
        cn.executemany("""INSERT INTO finding_events (finding_id, at, kind, from_status, to_status, actor, note)
                          VALUES (?, '2026-11-22T10:18:30+00:00', 'hidden', 'open', 'dismissed', 'hide rule', 'payer not expected to pay')""",
                       [(f,) for (f,) in cn.execute("SELECT finding_id FROM findings").fetchall()])
    out = run(c, hidden, "r2")
    assert _held_or_named(out, "swing", "vanished"), f"300 items let go after the run with no count; held={out['held']}, failing={out['failing']}"


# ======================================================================================== L3-7: the alarm's own blind spot
def test_the_progress_counts_a_swing_alarm_reads_can_see_state_moves(store):
    """progress_snapshots (what hotfix 2b's SWING alarm compares run to run) counts by category and status only: 800 items
    moving from Work now to Can't be checked leave it unchanged (L3-7)."""
    c = store.conn
    for i in range(800):
        add_item(c, f"PS-{i:04d}", signal="NO_ANSWER", category="denial", line_key=None, patient_control_number=f"ZZ05T{i:04d}C001")

    def snap(rid):
        c.execute("DELETE FROM progress_snapshots WHERE run_id=?", (rid,))
        c.execute("""INSERT INTO progress_snapshots (run_id, at, category, status, n, cents)
                     SELECT ?, 'now', category, status, COUNT(*), COALESCE(SUM(shortfall_cents),0) FROM findings
                     WHERE COALESCE(stale,0)=0 GROUP BY category, status""", (rid,))      # the exact SQL of Detector.save
        return {(cat, st): (n, cents) for _r, _a, cat, st, n, cents in c.execute("SELECT * FROM progress_snapshots WHERE run_id=?", (rid,))}
    before = snap("p1")
    c.execute("UPDATE findings SET work_state='cant_check'")
    after = snap("p2")
    assert before != after, "a swing alarm on progress_snapshots cannot see 800 items leave Work now (work_state is not in it)"


# ======================================================================================== L3-8: faxes go out of a held run
def _appealed_with_fax(c, fid: str, sent_on: str) -> None:
    ensure_fax_tables(c)
    c.execute("""INSERT OR REPLACE INTO payer_fax (payer_group, purpose, number, auto_ok, confirmed_by, confirmed_at)
                 VALUES ('Testcare Health', 'appeal', '5025550100', 1, 'invented', '2026-01-01')""")
    c.execute("""INSERT INTO faxes (fax_key, direction, finding_id, payer_group, number, purpose, level, provider, provider_id, status,
                 pages, created_at, updated_at, sent_at, doc) VALUES (?, 'out', ?, 'Testcare Health', '5025550100', 'appeal', 1, 'fake',
                 'P-1', 'sent', 3, ?, ?, ?, '{}')""", (f"FX-{fid}", fid, sent_on + "T15:00:00+00:00", sent_on + "T15:00:00+00:00",
                                                         sent_on + "T15:00:00+00:00"))
    c.commit()


def test_no_automatic_follow_up_while_the_worklist_is_held(store):
    """Safe mode on (a run held): fax.followups still lists an appealed item for an automatic second request with no hold.
    The server sends follow-ups right after every cycle (cli.cmd_cycle); Vercel at 15:43 (L3-8)."""
    c = store.conn
    add_item(c, "AP-1", status="appealed", work_state="waiting", follow_up_on="2026-11-20")
    _appealed_with_fax(c, "AP-1", "2026-09-01")
    sc.ensure_tables(c)
    sc.meta_set(c, sc.K_SAFE, {"on": True, "since": "2026-11-22T10:18:00+00:00", "run_id": "r9", "checks": [{"id": "vanished"}]})
    c.commit()
    due = [d for d in fax.followups(c, today=TODAY) if d["finding_id"] == "AP-1"]
    assert due and due[0]["action"] == "second_request"
    assert due[0].get("hold"), "a held run does not stop the automatic second request: hold is None"


def test_an_item_the_last_run_reopened_gets_no_automatic_follow_up(tmp_path):
    """The real detector: an appealed denial closes on a later remittance; then that remittance is superseded (a cut-short or
    colliding copy, L2-2/L2-3) and the next run reopens the item straight back to Sent ('appealed'). Its second request is
    due at once, with no person in between (L3-9). The feed is current (5 days), so the stale-feed hold does not apply."""
    from tests.test_resolution import base_visits, visit
    from tests.test_features import build_store
    who = "ELM*BO****MI*W55500302"                       # invented
    store = build_store(tmp_path, base_visits() + [visit(300, who, paid="0.00")], pay_date="20260415")
    Detector(store, run_id="t1", today="2026-05-01").run()
    fid = store.conn.execute("""SELECT finding_id FROM findings WHERE patient_control_number='CB0000300C017'
                                AND category='denial'""").fetchone()[0]
    apply_status(store.conn, fid, "appealed", "Appeal faxed", actor="invented-biller")
    store.conn.commit()
    _appealed_with_fax(store.conn, fid, "2026-05-01")
    pg = store.conn.execute("SELECT payer_group FROM findings WHERE finding_id=?", (fid,)).fetchone()[0]
    store.conn.execute("UPDATE payer_fax SET payer_group=?", (pg,))
    store.conn.execute("UPDATE faxes SET payer_group=?", (pg,))
    store.conn.commit()
    store.close()
    store = build_store(tmp_path, [visit(700, who)], pay_date="20260620")         # the later payment
    Detector(store, run_id="t2", today="2026-06-25").run()
    st = store.conn.execute("SELECT status, prev_status FROM findings WHERE finding_id=?", (fid,)).fetchone()
    assert tuple(st) == ("resolved", "appealed"), f"setup: the later payment should close it, got {tuple(st)}"
    store.close()
    store = build_store(tmp_path, [visit(800, "OAK*AL****MI*W55500999")], pay_date="20260705")   # the feed stays current
    tk = store.conn.execute("SELECT transaction_key FROM claims WHERE patient_control_number='CB0000700C017'").fetchone()[0]
    store.conn.execute("UPDATE transactions SET superseded=1 WHERE transaction_key=?", (tk,))     # what a bad copy does
    store.conn.commit()
    Detector(store, run_id="t3", today="2026-07-10").run()
    st = store.conn.execute("SELECT status FROM findings WHERE finding_id=?", (fid,)).fetchone()[0]
    reopened = store.conn.execute("SELECT COUNT(*) FROM finding_events WHERE finding_id=? AND kind='reopened' AND run_id='t3'",
                                  (fid,)).fetchone()[0]
    assert st == "appealed" and reopened == 1, f"setup: the run should reopen it to Sent, got {st}"
    due = [d for d in fax.followups(store.conn, today="2026-07-10") if d["finding_id"] == fid]
    store.close()
    assert not due or due[0].get("hold"), ("the run reopened a paid item to Sent and its automatic second request is due the same "
                                           f"day with no hold: {[(d['action'], d.get('hold')) for d in due]}")


# ======================================================================================== L3-10: money moves on items that stay
def test_dollars_that_move_on_items_that_stay_are_counted(store):
    """One odd line raises a baseline (L1-7) and the next run re-prices 500 items by $300 each ($150,000 more 'owed'):
    the items keep their ids and states, so no check sees it (L3-10)."""
    c = store.conn
    _baseline(c, 500)
    out = run(c, lambda cn: cn.execute("UPDATE findings SET shortfall_cents = shortfall_cents + 30000, "
                                       "expected_cents = expected_cents + 30000"), "r2")
    total = c.execute("SELECT SUM(shortfall_cents) FROM findings").fetchone()[0]
    assert _held_or_named(out, "swing", "money"), f"open dollars went from $50,000 to ${total / 100:,.0f} in one run; held={out['held']}"


# ======================================================================================== L3-11: relabel churn
def test_a_wave_of_relabelled_items_is_counted(store):
    """A rules or data change gives 300 lines a new signal: 300 new ids replace 300 old ones (first seen today, so the
    weekly email lists them as new and their deadlines are worked out again). check_vanished files them all under
    'same_line' and nothing counts the churn (L3-11)."""
    c = store.conn
    _baseline(c, 300)

    def relabel(cn):
        rows = cn.execute("SELECT finding_id, line_key, patient_control_number FROM findings").fetchall()
        cn.execute("DELETE FROM findings")
        for fid, lk, pcn in rows:
            add_item(cn, "RL-" + fid, line_key=lk, patient_control_number=pcn, signal="DENIED_MISSING_INFO", carc="16",
                     first_seen="2026-11-22T10:18:00+00:00", run_id="r2")
    out = run(c, relabel, "r2")
    van = check_of(sc.meta_get(c, sc.K_LAST)["checks"], "vanished")
    assert _held_or_named(out, "swing", "churn"), f"300 items replaced by new ids in one run; vanished routes {van['info'].get('routes')}"


# ======================================================================================== L3-12: alerts clear in a held run
def test_a_held_run_does_not_clear_the_stale_feed_alert(store):
    """While safe mode holds the worklist, the alert engine still runs on the new data and clears what the run no longer
    raises: one stray remittance dated today clears STALE_FEED (the clear is permanent history) (L3-12)."""
    from remitwatch.alerts import run_alerts
    c = store.conn
    remit(c, "2026-10-09", "ZZ06T0001C001", [("99213", 150, 90, [("CO", "45", 60)], "2026-09-30")])
    c.execute("INSERT INTO files (sha256, source_name, kind, ingested_at) VALUES ('f1', 'a', '835', '2026-10-10T10:00:00+00:00')")
    c.commit()
    first = run_alerts(c, today=TODAY)
    assert first["by_kind"].get("STALE_FEED") == 1
    sc.ensure_tables(c)
    sc.meta_set(c, sc.K_SAFE, {"on": True, "since": "2026-11-22T10:18:00+00:00", "run_id": "r9"})
    remit(c, "2026-11-21", "ZZ06T0002C001", [("99213", 10, 0, [("CO", "45", 10)], "2026-11-01")])     # the stray file
    c.execute("INSERT INTO files (sha256, source_name, kind, ingested_at) VALUES ('f2', 'b', '835', '2026-11-22T10:00:00+00:00')")
    c.commit()
    second = run_alerts(c, today=TODAY)
    st = c.execute("SELECT status FROM alerts WHERE kind='STALE_FEED'").fetchone()[0]
    assert st != "cleared", f"a held run cleared the stale-feed alert (cleared={second['cleared']})"


# ======================================================================================== controls: the harness sees a guard
def test_control_a_plain_dropped_item_is_kept(store):
    """CONTROL (passes on the snapshot): the vanished check keeps an item that simply disappears, so the failures above are
    gaps in the checks, not a broken harness."""
    c = store.conn
    _baseline(c, 5)
    out = run(c, lambda cn: cn.execute("DELETE FROM findings WHERE finding_id='BASE-0003'"), "r2")
    assert out["kept"] == 1 and c.execute("SELECT COUNT(*) FROM findings WHERE finding_id='BASE-0003'").fetchone()[0] == 1


def test_control_a_closure_without_proof_holds(store):
    """CONTROL (passes): an invariant break does hold the run and restore the list."""
    c = store.conn
    _baseline(c, 5)
    out = run(c, lambda cn: cn.execute("""UPDATE findings SET status='resolved', work_state='done', auto_note='x', evidence='{}'
                                          WHERE finding_id='BASE-0001'"""), "r2")
    assert out["held"] and c.execute("SELECT status FROM findings WHERE finding_id='BASE-0001'").fetchone()[0] == "open"
