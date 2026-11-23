"""
GUARD lens L5: undo and explanation. Every automatic change and every input must be recorded with its rule and evidence
(the decision log), shown in the item's "Why did this change?", and undoable; nothing a bad input caused may stick after
it is taken back. Each test below states one guarantee. On the snapshot (push 2 at 2f9c6ea) they FAIL: that is the
finding. The two controls at the end PASS, to show the harness sees the behaviour it tests where it exists.

Invented data only (the project's own builders: tests/test_runlog.py, tests/test_selfcheck.py, tests/test_ema_ledger.py,
tests/test_features.py, tests/test_remitwatch.py). Both store kinds where the code runs on both: the server's file store and
the compact in-memory store of the serverless host.

Run (read-only on the snapshot, no bytecode written into it):
  cd <this folder> && PYTHONDONTWRITEBYTECODE=1 SNAP=<snapshot> python3 -B -m pytest -q -p no:cacheprovider test_l5_undo.py
"""
from __future__ import annotations

import importlib
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

SNAP = os.environ.get("SNAP") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "snap")
sys.path.insert(0, os.path.abspath(SNAP))
os.environ.setdefault("REMITWATCH_ALLOW_OPEN", "1")

from remitwatch import cloud, fax, runlog  # noqa: E402
from remitwatch import ema_ledger as el  # noqa: E402
from remitwatch import ema_reports as er  # noqa: E402
from remitwatch import results as rs  # noqa: E402
from remitwatch import selfcheck as sc  # noqa: E402
from remitwatch import uploads  # noqa: E402
from remitwatch.actions import ensure_fax_tables  # noqa: E402
from remitwatch.detect import Detector  # noqa: E402
from remitwatch.state import LocalState  # noqa: E402
from remitwatch.store import Store  # noqa: E402
from tests import ema_fixtures as F  # noqa: E402
from tests.test_ema_ledger import D, apply as ledger_apply, charge, new_store, payment as prod_payment, prod_file  # noqa: E402
from tests.test_features import clean_visits  # noqa: E402
from tests.test_remitwatch import remit as remit_bytes  # noqa: E402
from tests.test_runlog import _findings_table, _item, denial_files, make_store  # noqa: E402
from tests.test_selfcheck import TODAY, _new, add_item, remit as remit_rows  # noqa: E402
from tests.test_selfcheck import run as sc_run  # noqa: E402


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setattr(er, "CLAIM_HOST_SUFFIX", F.HOST_SUFFIX)
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    monkeypatch.setenv("REMITWATCH_TMP", str(tmp_path / "scratch"))
    for k in ("VERCEL", "VERCEL_ENV", "REMITWATCH_EMA_REPORTS", "REMITWATCH_EMA_PAYER_ALIASES", "REMITWATCH_EMA_LEDGER",
              "REMITWATCH_TODAY", "REMITWATCH_EMA_REPORT_KEEP", "REMITWATCH_EMA_RETAIN_DAYS", "REMITWATCH_STALE_DAYS",
              "SRFAX_ACCESS_ID", "SRFAX_ACCESS_PWD", "DROPBOX_APP_KEY", "DROPBOX_REFRESH_TOKEN", "TRIZETTO_SFTP_USER",
              "ANTHROPIC_API_KEY", "SMTP_HOST", "REPORT_TO"):
        monkeypatch.delenv(k, raising=False)
    sc._GROUPS.clear()
    runlog.set_clock(None)
    yield
    runlog.set_clock(None)


@pytest.fixture(params=["file", "compact"])
def kind(request):
    return request.param


def _items(conn) -> dict:
    return {r[0]: (r[1], r[2], r[3]) for r in conn.execute(
        "SELECT finding_id, status, COALESCE(stale,0), COALESCE(shortfall_cents,0) FROM findings")}


# ============================================================================ L5-1: the decision log is never written
def test_a_detection_run_writes_its_own_decision_log(tmp_path, kind):
    """Two ordinary detection runs (a denial, then the payment that closes it). The closure must be in item_decisions
    without any test calling snapshot_items/record_decisions: today nothing in either cycle calls them (L5-1)."""
    store = make_store(kind, tmp_path)
    p1, p2 = denial_files(tmp_path / "feed", paid_later=True)
    store.ingest_835_paths([p1])
    Detector(store, "run-20261101T000000Z-aaaa").run()
    fid = store.conn.execute("SELECT finding_id FROM findings WHERE COALESCE(stale,0)=0").fetchone()[0]
    store.ingest_835_paths([p2])
    Detector(store, "run-20261102T000000Z-bbbb").run()
    assert store.conn.execute("SELECT status FROM findings WHERE finding_id=?", (fid,)).fetchone()[0] == "resolved"
    got = runlog.decisions_for(store.conn, fid)["items"]
    store.close()
    assert any(d["kind"] == "closed" for d in got), \
        "the item was closed automatically but item_decisions holds no row for it: the decision log is never written"


def test_the_item_drawer_can_ask_why_an_item_changed():
    """static/features.js asks GET /api/findings/{id}/decisions and /api/meta probes it; the route must exist (L5-1)."""
    from remitwatch import api
    paths = {getattr(r, "path", "") for r in api.app.routes}
    assert "/api/findings/{finding_id}/decisions" in paths, \
        "no decisions route: meta.decisions is false and the drawer never shows 'Why did this change?'"


def test_the_dashboard_copy_carries_the_decision_log():
    """On Vercel the pages read the dashboard's copy: it must hold item_decisions (L5-1, needs_H C9 not landed)."""
    names = {t[0] for t in cloud.SERVING_TABLES}
    assert "item_decisions" in names, "the serverless dashboard copy has no decision log to show"


# ============================================================ L5-3/L5-4: a vanished item carries no cause and no input
def test_an_item_that_vanishes_names_the_input_that_removed_it(tmp_path, kind):
    """File A holds 25 paid visits and one denial (an open item). File B is another copy of A's remittance (same payer,
    trace and payment date, newer ISA date) without the denial: it supersedes A and the item vanishes. The decision row
    for the vanished item must name what removed it (the copy that superseded its remittance, by its store key), so a
    person can find and take back the bad input. Today the row says only 'detect:no_longer_raised' with the item's own
    keys (and no row is written at all, L5-1)."""
    store = make_store(kind, tmp_path)
    p1 = denial_files(tmp_path / "feed")[0]
    store.ingest_835_paths([p1])
    store.mark_superseded()
    Detector(store, "run-20261101T000000Z-aaaa").run()
    before = runlog.snapshot_items(store.conn)
    fid = next(iter(before.rows))
    pb = tmp_path / "feed" / "copy_without_the_denial.RMT"
    data = remit_bytes("".join(clean_visits(1, 25, "99214", "223.65", "127.80", cfi="16")), "100.00", seg_count=99,
                       isa_ctrl="000000001", pay_date="20260415").replace(b"*260904*0909*", b"*260910*0909*")
    pb.write_bytes(data)
    store.ingest_835_paths([str(pb)])
    assert store.mark_superseded() == 1
    Detector(store, "run-20261102T000000Z-bbbb").run()
    assert fid not in _items(store.conn), "precondition: the superseding copy removed the item"
    runlog.record_decisions(store.conn, "run-20261102T000000Z-bbbb", before, rule_version="test")
    rows = runlog.decisions_for(store.conn, fid)["items"]
    store.close()
    assert rows and rows[0]["kind"] == "gone"
    named = [e for e in rows[0]["evidence"] if isinstance(e, dict) and (e.get("superseded_by") or e.get("input")
                                                                            or e.get("source") in ("input", "superseded"))]
    assert named, f"the vanished item's decision names no cause or input: rule {rows[0]['rule']}, evidence {rows[0]['evidence']}"


# ======================================================================== L5-5/L5-6: what the decision log leaves out
def test_a_change_of_recovered_dollars_is_a_decision(tmp_path, kind):
    """An automatic closure's recovered dollars move (a later copy pays less): Results and Progress count recovered
    dollars, so the change must be explained. TRACKED has no recovered_cents: nothing is written (L5-5)."""
    store = make_store(kind, tmp_path)
    c = store.conn
    A = "a" * 24
    _findings_table(c, [_item(A, status="resolved", work_state="done", auto_note="Paid on a later remittance",
                              recovered_cents=12780, recovered_date="2026-11-01")])
    before = runlog.snapshot_items(c)
    c.execute("UPDATE findings SET recovered_cents=2000 WHERE finding_id=?", (A,))
    c.commit()
    res = runlog.record_decisions(c, "run-x", before, rule_version="test")
    store.close()
    assert res["written"] >= 1, "$107.80 of recovered money moved on a closed item and the decision log says nothing"


def test_an_automatic_state_change_is_recorded_even_on_an_item_a_person_touched(tmp_path, kind):
    """A biller marks an item Started at 09:00; that night new data moves its state to Can't be checked. The state change
    is the run's, not the person's, but record_decisions skips every state change on an item touched since the last run
    ('after_person'), so it is explained nowhere (L5-6)."""
    store = make_store(kind, tmp_path)
    c = store.conn
    P = "b" * 24
    _findings_table(c, [_item(P, status="in_progress", work_state="work_now")])
    c.execute("INSERT INTO detection_runs (run_id, started_at, finished_at, version) VALUES ('run-1', '2026-11-01T00:00:00+00:00', "
              "'2026-11-01T00:05:00+00:00', 'x')")
    c.execute("INSERT INTO finding_events (finding_id, at, kind, from_status, to_status, actor) VALUES (?,?,?,?,?,?)",
              (P, "2026-11-01T09:00:00+00:00", "manual", "open", "in_progress", "a biller"))
    c.commit()
    before = runlog.snapshot_items(c)
    c.execute("UPDATE findings SET work_state='cant_check', state_basis='EMA reports are 40 days old' WHERE finding_id=?", (P,))
    c.commit()
    runlog.record_decisions(c, "run-2", before, rule_version="test")
    got = runlog.decisions_for(c, P)["items"]
    store.close()
    assert any(d["kind"] == "state" for d in got), "the run moved the item to Can't be checked and no decision row explains it"


# =================================================================== L5-9: a file read by mistake cannot be taken back
def _take_back_api():
    """The take-back the class guard specifies (door.forget), under any of the names a builder may give it."""
    for mod, name in (("remitwatch.door", "forget"), ("remitwatch.intake", "take_back"), ("remitwatch.store", "forget_file")):
        try:
            m = importlib.import_module(mod)
        except ImportError:
            continue
        fn = getattr(m, name, None)
        if callable(fn):
            return fn
    fn = getattr(Store, "forget_file", None)
    return (lambda store, sha, actor: fn(store, sha, actor=actor)) if fn else None


def test_a_remittance_read_by_mistake_can_be_taken_back(tmp_path, kind):
    """Another clinic's remittance (another payee tax ID and NPI, invented) is read as this practice's and raises items
    (0e-5). Taking it back must leave the items and their dollars exactly as they were before it. The snapshot has no
    take-back at all: no route, no CLI, no function removes a file's transactions, claims, lines or adjustments, and
    deleting it from Dropbox changes nothing (L5-9)."""
    store = make_store(kind, tmp_path)
    p1 = denial_files(tmp_path / "feed")[0]
    store.ingest_835_paths([p1])
    store.mark_superseded()
    Detector(store, "run-20261101T000000Z-aaaa").run()
    before = _items(store.conn)
    other = remit_bytes("".join(clean_visits(500, 3, "99214", "223.65", "0.00", cfi="16")).replace("CAS*CO*45*223.65~", "CAS*CO*197*223.65~"),
                        "0.00", seg_count=99, isa_ctrl="000000777", pay_date="20260420")
    other = other.replace(b"N1*PE*TEST DERMATOLOGY*XX*1777777772", b"N1*PE*ANOTHER CLINIC LLC*XX*1999999992") \
                 .replace(b"REF*TJ*999999999", b"REF*TJ*888888888")
    pb = tmp_path / "feed" / "another_clinic.RMT"
    pb.write_bytes(other)
    store.ingest_835_paths([str(pb)])
    store.mark_superseded()
    Detector(store, "run-20261102T000000Z-bbbb").run()
    assert set(_items(store.conn)) - set(before), "precondition: the other clinic's remittance raised items here"
    sha = store.conn.execute("SELECT sha256 FROM files WHERE source_name='another_clinic.RMT'").fetchone()[0]
    take_back = _take_back_api()
    if take_back is None:
        store.close()
        pytest.fail("no way to take a file back (door.forget / intake.take_back / Store.forget_file): once read, a wrong "
                    "remittance and every item it raised stay for good")
    take_back(store, sha, actor="owner")
    Detector(store, "run-20261103T000000Z-cccc").run()
    after = _items(store.conn)
    store.close()
    assert after == before


# =========================================================== L5-11: a Production file taken back loses what it replaced
def test_taking_a_production_file_back_restores_the_rows_it_replaced(tmp_path):
    """A service-year Production export, then a posted-date pull that replaces part of it. Taking the pull back (the only
    primitive is Ledger.forget, behind the server CLI's --reread) must leave the ledger as it was before the pull: today
    'the rows of older files it replaced do not come back' (ema_ledger.forget docstring), so a wrong pull taken back
    leaves a hole in the ledger (L5-11)."""
    store = new_store(tmp_path / "store.sqlite")
    lg = el.Ledger.open(str(tmp_path / "ema_ledger.sqlite"))
    year = [charge(F.link_no(1), svc=D(2026, 1, 5)), prod_payment(F.link_no(1), F.check_no(1), 60, svc=D(2026, 1, 5), posted=D(2026, 2, 10)),
            charge(F.link_no(2), svc=D(2026, 3, 2)), prod_payment(F.link_no(2), F.check_no(2), 70, svc=D(2026, 3, 2), posted=D(2026, 4, 20))]
    r1 = ledger_apply(store, lg, prod_file(tmp_path, year))
    assert r1["status"] == "current"
    rows_before = lg.conn.execute("SELECT COUNT(*), SUM(ins_paid) FROM postings").fetchone()
    pull = [prod_payment(F.link_no(2), F.check_no(2), 70, svc=D(2026, 3, 2), posted=D(2026, 4, 20)),
            prod_payment(F.link_no(1), F.check_no(3), 15, svc=D(2026, 1, 5), posted=D(2026, 5, 10)),
            F.production_row(link=F.link_no(1), svc=D(2026, 1, 5), posted=D(2026, 4, 6), contractual=5, reason="CO-45")]
    r2 = ledger_apply(store, lg, prod_file(tmp_path, pull))
    assert r2["status"] == "current" and r2["replaced"], "precondition: the pull replaced part of the year file"
    lg.forget(r2["file_id"])
    rows_after = lg.conn.execute("SELECT COUNT(*), SUM(ins_paid) FROM postings").fetchone()
    lg.close()
    store.close()
    assert tuple(rows_after) == tuple(rows_before), \
        f"after taking the pull back the ledger holds {tuple(rows_after)} (rows, paid) where it held {tuple(rows_before)}"


# ============================================================================= L5-14: a held report's decision is one-way
def test_a_held_report_discarded_by_mistake_can_still_be_applied(tmp_path):
    """A held Production export discarded by mistake: the person changes their mind. decide() accepts only a 'held' file,
    and the same bytes read again are 'already' (discarded): the export is lost until EMA exports it again (L5-14)."""
    store = new_store(tmp_path / "store.sqlite")
    lg = el.Ledger.open(str(tmp_path / "ema_ledger.sqlite"))
    rows_ = [charge(F.link_no(1 + i)) for i in range(40)] + [charge(F.link_no(99), posted="not a date") for _ in range(3)]
    path = prod_file(tmp_path, rows_)
    res = ledger_apply(store, lg, path)
    assert res["status"] == "held"
    assert er.decide(store.conn, res["file_id"], "discard", "a biller")["status"] == "discarded"
    again = er.decide(store.conn, res["file_id"], "apply", "the owner")
    reread = ledger_apply(store, lg, path)
    lg.close()
    store.close()
    assert "error" not in again or reread["status"] == "held", \
        f"a discard cannot be undone: decide says {again.get('error')!r}, reading it again says {reread['status']!r}"


# ================================================= L5-15: a person's action on a wrong list sticks after the input goes
def test_a_recovery_typed_on_an_item_the_rules_no_longer_find_is_not_counted_silently(tmp_path, kind):
    """During the stray-835 flood (0b-1) a biller marked a 'never answered' item Paid and typed $500 (0b-6). The flood's
    input is gone and the rules no longer raise the item. Detector.save keeps it listed (stale 0: 'a recovery on the
    record') with its $500 counted as recovered, with no note that the item it rests on no longer exists (L5-15)."""
    store = _new(kind, tmp_path)
    c = store.conn
    remit_rows(c, "2026-10-20", "ZZ00T0009C001", [("99213", 150, 120, [("CO", "45", 30)], "2026-10-01")])
    N = "c" * 24
    add_item(c, N, signal="NO_ANSWER", category="denial", actionable="review", status="resolved", work_state="done",
             recovered_cents=50000, recovered_date="2026-11-20", patient_control_number="ZZ00T0101C001", line_key=None,
             claim_key=None, title="Invented never-answered claim")
    c.execute("INSERT INTO finding_events (finding_id, at, kind, from_status, to_status, amount_cents, actor, note) VALUES "
              "(?,?,?,?,?,?,?,?)", (N, "2026-11-20T15:00:00+00:00", "manual", "open", "resolved", 50000, "a biller", None))
    c.commit()
    Detector(store, "run-20261122T000000Z-dddd", today=TODAY).run()
    row = c.execute("SELECT status, COALESCE(stale,0), recovered_cents, hint FROM findings WHERE finding_id=?", (N,)).fetchone()
    store.close()
    assert row is not None and row[0] == "resolved" and row[2] == 50000, "precondition: the person's recovery is kept"
    assert row[1] == 1 or "no longer" in str(row[3] or "").lower(), \
        "a typed recovery on an item the rules no longer find stays counted as money recovered, with nothing to say so"


# ======================================== L5-16: the follow-up engine keeps chasing an item the rules no longer raise
def _chase_world(store, stale: int) -> str:
    c = store.conn
    ensure_fax_tables(c)
    X = "d" * 24
    add_item(c, X, status="appealed", work_state="waiting", stale=stale, payer_group="Testcare Health", payer_kind="commercial",
             appealed_at="2026-09-20T12:00:00+00:00")
    sent = (date.fromisoformat(TODAY) - timedelta(days=60)).isoformat() + "T12:00:00+00:00"
    c.execute("""INSERT INTO faxes (fax_key, direction, finding_id, packet_id, payer_group, number, purpose, level, provider,
                 provider_id, status, pages, created_at, updated_at, sent_at, doc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              ("e" * 24, "out", X, "p" * 24, "Testcare Health", "8005550100", "appeal", 1, "srfax", "F0001", "sent", 3, sent, sent,
               sent, "{}"))
    c.execute("INSERT INTO payer_fax (payer_group, purpose, number, confirmed_by, confirmed_at, auto_ok) VALUES (?,?,?,?,?,1)",
              ("Testcare Health", "appeal", "8005550100", "a biller", "2026-09-01T00:00:00+00:00"))
    c.commit()
    return X


def test_no_letter_chases_an_item_the_rules_no_longer_find(tmp_path, kind):
    """An appeal faxed during a flood: once the input is gone the item is kept only for its history (stale 1). The
    follow-up engine still finds it due and a weekly 'second request' goes to the payer, unattended, for a claim the
    rules no longer find anything wrong with (L5-16; hotfix 2d D4 covers 'paid', not 'no longer found')."""
    store = _new(kind, tmp_path)
    X = _chase_world(store, stale=1)
    due = [d for d in fax.followups(store.conn, today=TODAY) if d["finding_id"] == X]
    store.close()
    auto = [d for d in due if d["action"] in ("second_request", "next_level", "payment", "records") and not d.get("hold")]
    assert not auto, f"an unattended {auto[0]['action']} is due for an item the rules no longer raise"


# ======================================== L5-17: a note taken back leaves no trace on the faxes that already carried it
def test_taking_a_note_back_writes_a_line_in_the_history_of_its_items(tmp_path):
    """A visit note matched to a claim (and faxed in its packet) is taken back as the wrong patient's (0b-2). Future sends
    are blocked (_packet_problem), but remove_note writes nothing in the item's history and records no person: nobody is
    told that a fax already sent carried it, which is the disclosure a person must review (L5-17)."""
    store = _new("file", tmp_path)
    c = store.conn
    uploads.ensure_schema(c)
    X = "f" * 24
    add_item(c, X, status="appealed", patient_control_number="ZZ00T0201C001")
    p = tmp_path / "note.txt"
    p.write_text("Invented visit note")
    sha = "1" * 64
    c.execute("""INSERT INTO uploaded_notes (note_id, sha256, name, path, kind, size, uploaded_at, uploaded_by, status, matched_by,
                 matched_at, claims, service_date) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              ("9" * 24, sha, "note.txt", str(p), "note", 19, "2026-11-01T00:00:00+00:00", "a biller", "matched", "person",
               "2026-11-01T00:00:00+00:00", json.dumps(["ZZ00T0201C001"]), "2026-03-02"))
    c.execute("INSERT INTO uploads (upload_id, kind, name, path, size, uploaded_at, uploaded_by, status) VALUES (?,?,?,?,?,?,?,?)",
              (sha, "note", "note.txt", str(p), 19, "2026-11-01T00:00:00+00:00", "a biller", "done"))
    c.commit()
    assert uploads.remove_note(c, "9" * 24)
    lines = c.execute("SELECT kind, note FROM finding_events WHERE finding_id=?", (X,)).fetchall()
    store.close()
    assert lines, "the note was taken back and the item it was matched to (and faxed for) has no line saying so"


# ===================================== L5-22: counters that freeze what a bad run did, even after a later run undoes it
def test_a_missed_deadline_that_the_next_run_undoes_is_withdrawn(tmp_path, kind):
    """A bad run (a future-dated file, the evening clock of 0f-1) moves a Work now item to Expired; the next run, with the
    input taken back, puts it back to Work now. The track record keeps 'missed_deadline' (INSERT OR IGNORE, never
    revisited) and the scoreboard's 'lost to deadlines' counts it for good (L5-22)."""
    store = _new(kind, tmp_path)
    c = store.conn
    add_item(c, "F-miss", appeal_deadline="2026-12-31")
    out1 = sc_run(c, None, "r1", today="2026-11-20")
    out2 = sc_run(c, lambda cn: cn.execute("UPDATE findings SET work_state='expired' WHERE finding_id='F-miss'"), "r2", today="2026-11-21")
    out3 = sc_run(c, lambda cn: cn.execute("UPDATE findings SET work_state='work_now' WHERE finding_id='F-miss'"), "r3", today="2026-11-22")
    assert not (out2["held"] or out3["held"]), "precondition: no run was held"
    tr = sc.track_record(c, today=TODAY)
    store.close()
    missed = sum(w["missed_n"] for w in tr["weeks"])
    assert missed == 0, f"the item is back in Work now but 'lost to deadlines' still counts {missed}"


def test_the_results_registry_withdraws_a_loss_the_next_run_undid(tmp_path, kind):
    """results.update_registry records lost_on/lost_cents when an item goes Work now -> Expired and never clears them;
    the Results tab's 'lost to deadlines' keeps an item that is back in Work now (L5-22)."""
    store = _new(kind, tmp_path)
    c = store.conn
    add_item(c, "F-res", appeal_deadline="2026-12-31", first_seen="2026-11-01T00:00:00+00:00")
    rs.update_registry(c, today="2026-11-20")
    c.execute("UPDATE findings SET work_state='expired' WHERE finding_id='F-res'")
    rs.update_registry(c, today="2026-11-21")
    c.execute("UPDATE findings SET work_state='work_now' WHERE finding_id='F-res'")
    rs.update_registry(c, today="2026-11-22")
    row = c.execute("SELECT lost_on, lost_cents, last_state FROM results_items WHERE finding_id='F-res'").fetchone()
    store.close()
    assert row[2] == "work_now"
    assert row[0] is None, f"the item is back in Work now but Results still counts it lost on {row[0]} (${(row[1] or 0) / 100:,.2f})"


# ================================================================ L5-24: no earlier store image to go back to on Vercel
def test_the_serverless_store_keeps_the_image_before_each_save(tmp_path):
    """cloud.save_raw overwrites store.sqlite.gz in place: the run before a bad input cannot be returned to from the app
    (the server keeps 7 daily backups; Vercel keeps none of its own) (L5-24)."""
    state = LocalState(str(tmp_path / "state"))
    s = Store(":memory:", compact=True)
    s.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('marker', 'first')")
    s.conn.commit()
    cloud.save_raw(state, s)
    s.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('marker', 'second')")
    s.conn.commit()
    cloud.save_raw(state, s)
    s.close()
    names = [m["name"] for m in state.list("")]
    images = [n for n in names if "store" in n and n.endswith(".gz")]
    assert len(images) >= 2, f"only {images} in the state folder: the image before the last save is gone"


# ===================================================================================================== controls (PASS)
def test_control_the_follow_up_engine_chases_an_item_still_raised(tmp_path, kind):
    """The same world with the item still raised (stale 0): the second request is due. Shows the harness sees a chase."""
    store = _new(kind, tmp_path)
    X = _chase_world(store, stale=0)
    due = [d for d in fax.followups(store.conn, today=TODAY) if d["finding_id"] == X]
    store.close()
    assert any(d["action"] == "second_request" and not d.get("hold") for d in due)


def test_control_record_decisions_names_a_closure_when_called(tmp_path, kind):
    """runlog.record_decisions itself works (tests/test_runlog.py): the gap is that no cycle calls it."""
    store = make_store(kind, tmp_path)
    p1, p2 = denial_files(tmp_path / "feed", paid_later=True)
    store.ingest_835_paths([p1])
    Detector(store, "run-20261101T000000Z-aaaa").run()
    before = runlog.snapshot_items(store.conn)
    store.ingest_835_paths([p2])
    Detector(store, "run-20261102T000000Z-bbbb").run()
    res = runlog.record_decisions(store.conn, "run-20261102T000000Z-bbbb", before, rule_version="test")
    store.close()
    assert res["by_kind"].get("closed") == 1
