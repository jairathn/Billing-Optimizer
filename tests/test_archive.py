"""
Retention and the cold archive (remitwatch.archive): old, settled bills leave the store the daily run loads, and every one
of them is still found, opened and reopened. Invented data only: made-up claim ids, payers, members and amounts. The
main cases run on both kinds of store: the server's file store with its archive folder, and the compact in-memory image a
serverless host loads (Store.from_bytes, as remitwatch.cloud does) with its archive in the state folder.
"""
from __future__ import annotations

import gzip
import json
import os
import re
import sqlite3
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch import archive  # noqa: E402
from remitwatch.detect import Detector  # noqa: E402
from remitwatch.detect import ensure_schema as detect_schema  # noqa: E402
from remitwatch.state import LocalState  # noqa: E402
from remitwatch.store import Store  # noqa: E402
from tests.test_features import claim, svc  # noqa: E402
from tests.test_remitwatch import remit as envelope  # noqa: E402

TODAY = "2026-11-22"


def _has_dbstat() -> bool:
    try:
        sqlite3.connect(":memory:").execute("SELECT * FROM dbstat").fetchall()
        return True
    except sqlite3.Error:
        return False


HAS_DBSTAT = _has_dbstat()                  # SQLite 3.40 (Vercel's) is built without it: sizes are estimated there
OLD = "20220310"            # service more than three years before TODAY
NEW = "20260310"


# ------------------------------------------------------------------------------------------------------------ helpers
@pytest.fixture
def frozen_hook(monkeypatch):
    """The detection hook as the hook-in agent wires it (needs_E.md): the year baselines kept at the end of
    build_baselines."""
    orig = Detector.build_baselines

    def wired(self):
        orig(self)
        archive.apply_frozen_rates(self)
    monkeypatch.setattr(Detector, "build_baselines", wired)
    return wired


def ingest(store: Store, tmp_path, batches: list[tuple[list[str], str]], tag: str = "r") -> None:
    paths = []
    for n, (claims, day) in enumerate(batches):
        p = tmp_path / f"{tag}{n:03d}-{len(list(tmp_path.glob('*.RMT'))):04d}.RMT"
        p.write_bytes(envelope("".join(claims), "100.00", payer="ANTHEM", seg_count=99,
                               isa_ctrl=f"{len(list(tmp_path.glob('*.RMT'))) + 1:09d}", pay_date=day))
        paths.append(str(p))
    store.ingest_835_paths(paths)


def visits(start: int, n: int, dos: str, paid: str = "117.60") -> list[str]:
    co = f"{223.65 - float(paid):.2f}"
    return [claim(start + i, svc("99214", "223.65", paid, paid, f"CAS*CO*45*{co}~", dos=dos), dos=dos) for i in range(n)]


def denial(i: int, dos: str) -> str:
    return claim(i, svc("11900", "93.75", "0.00", None, "CAS*CO*50*93.75~", dos=dos), dos=dos)


def world(tmp_path, kind: str) -> Store:
    """Eight paid visits and one denial three years ago (the denial's windows long closed: expired), the same this year
    (the denial is work to do)."""
    store = Store(str(tmp_path / "s.sqlite")) if kind == "file" else Store(":memory:", compact=True)
    ingest(store, tmp_path, [(visits(1, 8, OLD) + [denial(50, OLD)], "20220415"),
                             (visits(100, 8, NEW) + [denial(150, NEW)], "20260415")])
    return store


def as_image(store: Store) -> Store:
    """The serverless host's store: the compact image serialized and opened again, as remitwatch.cloud loads it."""
    data = store.to_bytes()
    store.close()
    return Store.from_bytes(data)


def sink_of(tmp_path, kind: str):
    return archive.DirSink(str(tmp_path / "archive")) if kind == "file" else archive.StateSink(LocalState(str(tmp_path / "state")))


def detect(store: Store, run_id: str = "r1") -> None:
    Detector(store, run_id=run_id, today=TODAY).run()


def counts(conn) -> dict:
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("claims", "lines", "adjustments", "findings")}


def items(conn) -> dict:
    return {r[0]: (r[1], r[2], r[3]) for r in conn.execute(
        "SELECT finding_id, patient_control_number, work_state, shortfall_cents FROM findings WHERE COALESCE(stale,0)=0")}


def worklist(conn) -> dict:
    return {k: v for k, v in items(conn).items() if v[1] in archive.OPEN_STATES}


def table_rows(conn, table: str, where: str, params=()) -> list[tuple]:
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    return sorted((tuple(r) for r in conn.execute(f"SELECT {','.join(cols)} FROM {table} WHERE {where}", params)), key=repr)


# a crafted store for the keep rules: claims written straight into the 835 tables, items written straight into findings
def crafted(kind: str, tmp_path) -> Store:
    store = Store(str(tmp_path / "c.sqlite")) if kind == "file" else Store(":memory:", compact=True)
    detect_schema(store.conn)
    store.conn.execute("INSERT INTO detection_runs (run_id, started_at, finished_at, version) VALUES ('r0', ?, ?, 'test')", (TODAY, TODAY))
    store.conn.commit()
    return store


def put_claim(store: Store, bill: str, dos: str, *, n_lines: int = 1, paid_on: str | None = None, payer_ref: str | None = None,
              name: str = "PATIENT") -> list[str]:
    c = store.conn
    tk = f"T-{bill}-{dos}"
    paid_on = paid_on or dos
    c.execute("INSERT OR IGNORE INTO transactions (transaction_key, payment_date, payer_name, superseded) VALUES (?,?,?,0)",
              (tk, paid_on, "ACME HEALTH"))
    ck = f"C-{bill}-{dos}"
    c.execute("""INSERT INTO claims (claim_key, transaction_key, patient_control_number, claim_status_code, charge_cents, paid_cents,
                 payer_claim_control_number, patient_last, patient_first, payer_name, payment_date, statement_from, statement_to)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (ck, tk, bill + "C017", "1", 10000 * n_lines, 5000 * n_lines, payer_ref, name, "ALPHA", "ACME HEALTH", paid_on, dos, dos))
    keys = []
    for j in range(n_lines):
        lk = f"{ck}-L{j}"
        c.execute("""INSERT INTO lines (line_key, claim_key, transaction_key, procedure_code, charge_cents, paid_cents, allowed_cents,
                     service_date, patient_control_number, claim_status_code, payer_name, payment_date)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (lk, ck, tk, "99213", 10000, 5000, 5000, dos, bill + "C017", "1", "ACME HEALTH", paid_on))
        c.execute("INSERT INTO adjustments (transaction_key, claim_key, line_key, level, group_code, reason_code, amount_cents) "
                  "VALUES (?,?,?,?,?,?,?)", (tk, ck, lk, "line", "CO", "45", 5000))
        keys.append(lk)
    c.commit()
    return keys


def put_item(store: Store, fid: str, *, bill: str | None = None, line_key: str | None = None, status: str = "open",
             work_state: str | None = None, stale: int = 0, notes: str | None = None, evidence: dict | None = None,
             shortfall: int = 2500) -> None:
    store.conn.execute("""INSERT INTO findings (finding_id, signal, category, patient_control_number, claim_key, line_key, status,
                          work_state, stale, notes, evidence, shortfall_cents, service_date)
                          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (fid, "SECOND_RATE" if evidence else "DENIED_TEST", "underpayment" if evidence else "denial",
                        (bill + "C017") if bill else None, None, line_key, status, work_state, stale, notes,
                        json.dumps(evidence) if evidence else None, shortfall, "2021-03-01"))
    store.conn.commit()


def freeze_all(store: Store, years=("2019", "2020", "2021", "2022", "2023")) -> None:
    """A crafted store has no analysis run that kept the year baselines: say they are kept."""
    archive.ensure_schema(store.conn)
    archive._meta_put(store.conn, archive.META_FROZEN, {y: {"at": TODAY, "digest": "x", "rates": 0} for y in years})
    store.conn.commit()


# =========================================================================== 1. what leaves and what stays
@pytest.mark.parametrize("kind", ["file", "compact"])
def test_old_settled_bills_leave_open_ones_stay_and_a_second_run_changes_nothing(tmp_path, frozen_hook, kind):
    store = world(tmp_path, kind)
    detect(store)
    if kind == "compact":
        store = as_image(store)
    conn = store.conn
    before = items(conn)
    old_fid = next(f for f, v in before.items() if v[0] == "CB0000050C017")
    new_fid = next(f for f, v in before.items() if v[0] == "CB0000150C017")
    assert before[old_fid][1] == "expired" and before[new_fid][1] == "work_now"
    hot_before = counts(conn)
    sink = sink_of(tmp_path, kind)

    res = archive.run(conn, sink=sink, today=TODAY, run_id="a1", mode=kind)
    assert not res["errors"] and res["blocked"] is None
    assert res["bills"] == 9 and res["claims"] == 9 and res["findings"] == 1       # the nine 2022 bills, one expired item
    after = counts(conn)
    assert after["claims"] == hot_before["claims"] - 9 and after["lines"] == hot_before["lines"] - 9
    assert old_fid not in items(conn) and items(conn)[new_fid] == before[new_fid]   # the open item stays exactly as it was
    assert conn.execute("SELECT work_state FROM archive_findings WHERE finding_id=?", (old_fid,)).fetchone()[0] == "expired"
    # every archived row is in the file it names, and the file is gzipped JSON lines with a header
    name = res["files"][0]["name"]
    raw = gzip.decompress(sink.get(name))
    assert json.loads(raw.split(b"\n")[0])["format"] == archive.FORMAT
    # a second run with nothing new changes nothing
    again = archive.run(conn, sink=sink, today=TODAY, run_id="a2", mode=kind)
    assert again["bills"] == 0 and again["eligible"] == 0 and counts(conn) == after
    # detection after the archive: the worklist is the same, the archived item does not come back
    detect(store, "r2")
    assert worklist(conn) == {k: v for k, v in before.items() if v[1] in archive.OPEN_STATES}
    assert old_fid not in items(conn)
    if kind == "compact":
        # what the run did lasts in the saved image (the serverless host saves the store after the run)
        store = as_image(store)
        assert store.conn.execute("SELECT COUNT(*) FROM archive_bills").fetchone()[0] == 9
    store.close()


@pytest.mark.parametrize("kind", ["file", "compact"])
def test_every_archived_item_still_opens_with_its_rows_and_history(tmp_path, frozen_hook, kind):
    store = world(tmp_path, kind)
    detect(store)
    conn = store.conn
    old = table_rows(conn, "claims", "patient_control_number = 'CB0000050C017'")
    fid = conn.execute("SELECT finding_id FROM findings WHERE patient_control_number='CB0000050C017'").fetchone()[0]
    conn.execute("INSERT INTO finding_events (finding_id, at, kind, from_status, to_status, actor, note) VALUES (?,?,?,?,?,?,?)",
                 (fid, "2023-01-05T10:00:00", "manual", "open", "open", "biller", "called the payer"))
    conn.commit()
    sink = sink_of(tmp_path, kind)
    archive.run(conn, sink=sink, today=TODAY, run_id="a1")
    for text in ("CB0000050C017", "CB0000050", "cb0000050c017 "):      # any submission, the bill, any case
        got = archive.lookup_claim(conn, text, sink=sink)
        assert got and got["bill"] == "CB0000050"
    got = archive.lookup_claim(conn, "CB0000050C017", sink=sink)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(claims)")]
    assert sorted((tuple(r[c] for c in cols) for r in got["tables"]["claims"]), key=repr) == old
    assert {t for t in got["tables"]} >= {"claims", "lines", "adjustments", "findings", "finding_events", "transactions"}
    item = archive.lookup_finding(conn, fid, sink=sink)
    assert item["finding"]["finding_id"] == fid and item["finding"]["work_state"] == "expired"
    assert [e["kind"] for e in item["events"]] == ["manual", "archived"]
    assert "archive" in item["events"][-1]["note"] and archive.RULE in item["events"][-1]["note"]
    # the claim view's own queries run unchanged on the bill's database
    db = archive.claim_db(conn, "CB0000050", sink=sink)
    assert db.execute("""SELECT COUNT(*) FROM claims c JOIN transactions t ON t.transaction_key = c.transaction_key
                         WHERE c.patient_control_number = 'CB0000050C017'""").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM meta WHERE key='ema:key_salt'").fetchone()[0] == 0
    db.close()
    assert archive.lookup_claim(conn, "CB9999999C017", sink=sink) is None
    store.close()


# =========================================================================== 2. the keep rules
@pytest.mark.parametrize("kind", ["file", "compact"])
def test_every_open_state_a_pattern_and_a_reopening_keep_their_bill(tmp_path, kind):
    store = crafted(kind, tmp_path)
    bills = {b: put_claim(store, b, "2021-03-01") for b in
             ("KA01AAAAA", "KB01AAAAA", "KC01AAAAA", "KD01AAAAA", "KE01AAAAA", "KF01AAAAA", "KG01AAAAA", "KH01AAAAA",
              "KI01AAAAA", "KJ01AAAAA", "KK01AAAAA", "KL01AAAAA", "KM01AAAAA", "KN01AAAAA", "KP01AAAAA")}
    put_claim(store, "KO01AAAAA", "2026-03-01")                                 # too new
    put_item(store, "fa", bill="KA01AAAAA", work_state="work_now")
    put_item(store, "fb", bill="KB01AAAAA", work_state="waiting")
    put_item(store, "fc", bill="KC01AAAAA", work_state="cant_check")
    put_item(store, "fd", bill="KD01AAAAA", work_state="expired")
    put_item(store, "fe", bill="KE01AAAAA", work_state="done", status="resolved")
    put_item(store, "ff", bill="KF01AAAAA", work_state="accepted", status="dismissed")
    put_item(store, "fg", bill="KG01AAAAA", status="in_progress")               # no state: a person's started status
    put_item(store, "fh", bill="KH01AAAAA", status="resolved")
    put_item(store, "fi", bill="KI01AAAAA", status="appealed", stale=1)         # a person's sent work the rules no longer list
    put_item(store, "fj", bill="KJ01AAAAA", status="dismissed", stale=1)
    put_item(store, "fl", status="dismissed", notes="not worth it", work_state="accepted",
             evidence={"line_keys": bills["KL01AAAAA"]})                         # a pattern a person worked
    put_item(store, "fm", work_state="work_now", evidence={"line_keys": bills["KM01AAAAA"]})
    put_item(store, "fn", work_state="expired", evidence={"line_keys": bills["KN01AAAAA"]})  # nobody touched it
    archive.ensure_schema(store.conn)
    store.conn.execute("INSERT INTO archive_pins (bill, until, why, at) VALUES ('KP01AAAAA', '2027-01-01', 'reopened', ?)", (TODAY,))
    store.conn.commit()
    freeze_all(store)
    p = archive.plan(store.conn, today=TODAY)
    assert sorted(b for b, *_ in p["bills"]) == ["KD01AAAAA", "KE01AAAAA", "KF01AAAAA", "KH01AAAAA", "KJ01AAAAA", "KK01AAAAA",
                                                  "KN01AAAAA"]
    assert p["held"] == {"open item": 5, "pattern": 2, "reopened": 1}
    res = archive.run(store.conn, sink=sink_of(tmp_path, kind), today=TODAY)
    assert res["bills"] == 7
    # the patterns stay in the store (they belong to no bill); the items of archived bills left with them
    assert {r[0] for r in store.conn.execute("SELECT finding_id FROM findings")} == {"fa", "fb", "fc", "fg", "fi", "fl", "fm", "fn"}
    store.close()


def test_nothing_moves_while_the_analysis_is_behind_or_has_never_run(tmp_path):
    store = crafted("file", tmp_path)
    put_claim(store, "KA01AAAAA", "2021-03-01")
    freeze_all(store)
    store.conn.execute("INSERT INTO meta (key, value) VALUES ('cloud:detect_pending', '1')")
    store.conn.commit()
    res = archive.run(store.conn, sink=sink_of(tmp_path, "file"), today=TODAY)
    assert res["blocked"] and res["bills"] == 0
    store.conn.execute("DELETE FROM meta WHERE key='cloud:detect_pending'")
    store.conn.execute("DELETE FROM detection_runs")
    store.conn.commit()
    res = archive.run(store.conn, sink=sink_of(tmp_path, "file"), today=TODAY)
    assert res["blocked"] and res["bills"] == 0
    assert store.conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 1
    store.close()


def test_a_dry_run_writes_nothing(tmp_path):
    store = crafted("file", tmp_path)
    put_claim(store, "KA01AAAAA", "2021-03-01")
    freeze_all(store)
    meta = dict(store.conn.execute("SELECT key, value FROM meta").fetchall())
    res = archive.run(store.conn, sink=None, today=TODAY)
    assert res["dry_run"] and res["eligible"] == 1 and res["bills"] == 0
    assert dict(store.conn.execute("SELECT key, value FROM meta").fetchall()) == meta
    assert store.conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 1
    store.close()


# =========================================================================== 3. the year baselines
def test_a_year_the_archive_took_keeps_the_rates_measured_on_the_whole_year(tmp_path, frozen_hook):
    """24 visits paid at the usual $117.60 in 2022 and one paid $90.00 (an underpayment kept in the store: reopened).
    Once the archive takes the 24 paid bills, the year's rate would be measured on the one bill left and the
    underpayment would disappear; the kept baseline keeps it, at the same size."""
    store = Store(":memory:", compact=True)
    ingest(store, tmp_path, [(visits(1, 24, OLD) + visits(60, 1, "20220311", paid="90.00"), "20220415"),
                             (visits(100, 6, NEW), "20260415")])
    detect(store)
    under = {f: v for f, v in items(store.conn).items() if v[0] == "CB0000060C017"}
    assert len(under) == 1
    fid, (_pcn, _ws, short) = next(iter(under.items()))
    assert short == 2760
    archive.ensure_schema(store.conn)
    store.conn.execute("INSERT INTO archive_pins (bill, until, why, at) VALUES ('CB0000060', '2027-06-01', 'reopened', ?)", (TODAY,))
    store.conn.commit()
    res = archive.run(store.conn, sink=sink_of(tmp_path, "compact"), today=TODAY)
    assert res["bills"] == 24 and "2022" in archive._locked_years(store.conn)
    detect(store, "r2")
    assert items(store.conn).get(fid, (None, None, None))[2] == 2760
    rate = store.conn.execute("SELECT mode_cents, n FROM payer_rates WHERE year='2022' AND code='99214'").fetchone()
    assert tuple(rate) == (11760, 25)               # the pages show the whole year's rate too
    store.close()


def test_without_a_kept_baseline_no_bill_of_that_year_moves(tmp_path):
    store = Store(":memory:", compact=True)
    ingest(store, tmp_path, [(visits(1, 4, OLD), "20220415"), (visits(100, 2, NEW), "20260415")])
    detect(store)                                   # the hook is not wired: no baseline kept
    res = archive.run(store.conn, sink=sink_of(tmp_path, "compact"), today=TODAY)
    assert res["bills"] == 0 and res["waiting_rates"] == 4 and res["waiting_years"] == ["2022"]
    assert store.conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 6
    assert any(i["key"] == "archive_waiting" and not i["ok"] for i in archive.data_health(store.conn))
    store.close()


def test_the_baseline_hook_at_the_end_of_build_baselines_rewrites_the_locked_years_rows(tmp_path):
    store = Store(":memory:", compact=True)
    ingest(store, tmp_path, [(visits(1, 12, OLD), "20220415")])
    det = Detector(store, run_id="t", today=TODAY)
    det.load()
    det.build_baselines()
    snap = archive.apply_frozen_rates(det)
    assert snap["snapshot"] == ["2022"]
    archive._meta_put(store.conn, archive.META_LOCKED, ["2022"])
    store.conn.execute("DELETE FROM lines WHERE line_key IN (SELECT line_key FROM lines LIMIT 10)")
    store.conn.commit()
    det = Detector(store, run_id="t2", today=TODAY)
    det.load()
    det.build_baselines()
    assert not any(k[1] == "2022" for k in det.rates)              # two lines left: no rate of its own
    out = archive.apply_frozen_rates(det)
    assert out["overridden"] == 1 and det.rates[("Anthem / BCBS", "2022", "99214", "unknown", "office")]["n"] == 12
    assert tuple(store.conn.execute("SELECT mode_cents, n FROM payer_rates WHERE year='2022'").fetchone()) == (11760, 12)
    store.close()


# =========================================================================== 4. safety
def test_a_run_killed_between_writing_the_file_and_deleting_loses_nothing(tmp_path, frozen_hook, monkeypatch):
    store = world(tmp_path, "file")
    detect(store)
    conn = store.conn
    hot = counts(conn)
    sink = sink_of(tmp_path, "file")

    def killed(*_a, **_k):
        raise KeyboardInterrupt("the platform stopped the function")
    monkeypatch.setattr(archive, "_commit_batch", killed)
    with pytest.raises(KeyboardInterrupt):
        archive.run(conn, sink=sink, today=TODAY)
    assert counts(conn) == hot and conn.execute("SELECT COUNT(*) FROM archive_bills").fetchone()[0] == 0
    written = sink.list("bills")
    assert len(written) == 1                                        # the file is there, nothing points to it
    monkeypatch.undo()
    res = archive.run(conn, sink=sink, today=TODAY)
    assert res["bills"] == 9 and res["files"][0]["name"] == written[0] and res["files"][0]["written"] is False
    store.close()


def test_a_failure_inside_the_delete_transaction_rolls_everything_back(tmp_path, frozen_hook, monkeypatch):
    store = world(tmp_path, "file")
    detect(store)
    conn = store.conn
    hot = counts(conn)
    real = archive._meta_put

    def failing(c, key, value):
        if key == archive.META_LOCKED:
            raise sqlite3.OperationalError("disk I/O error")
        return real(c, key, value)
    monkeypatch.setattr(archive, "_meta_put", failing)
    with pytest.raises(sqlite3.OperationalError):
        archive.run(conn, sink=sink_of(tmp_path, "file"), today=TODAY)
    assert counts(conn) == hot
    assert conn.execute("SELECT COUNT(*) FROM archive_bills").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM archive_files").fetchone()[0] == 0
    store.close()


def test_a_person_changing_an_item_while_the_run_works_keeps_the_bill(tmp_path, frozen_hook, monkeypatch):
    store = world(tmp_path, "file")
    detect(store)
    conn = store.conn
    fid = conn.execute("SELECT finding_id FROM findings WHERE patient_control_number='CB0000050C017'").fetchone()[0]
    real = archive._build_records

    def meanwhile(c, bills):
        out = real(c, bills)
        c.execute("UPDATE findings SET status='in_progress', notes='calling the payer' WHERE finding_id=?", (fid,))
        c.commit()
        return out
    monkeypatch.setattr(archive, "_build_records", meanwhile)
    res = archive.run(conn, sink=sink_of(tmp_path, "file"), today=TODAY)
    assert res["changed_meanwhile"] == 1 and res["bills"] == 8
    assert conn.execute("SELECT status, notes FROM findings WHERE finding_id=?", (fid,)).fetchone()[0] == "in_progress"
    assert conn.execute("SELECT COUNT(*) FROM claims WHERE patient_control_number='CB0000050C017'").fetchone()[0] == 1
    assert conn.execute("SELECT 1 FROM archive_bills WHERE bill='CB0000050'").fetchone() is None
    store.close()


def test_rows_leaving_must_equal_rows_archived(tmp_path, frozen_hook, monkeypatch):
    store = world(tmp_path, "file")
    detect(store)
    conn = store.conn
    real = archive._build_records

    def extra_row(c, bills):
        out = real(c, bills)
        ck = c.execute("SELECT claim_key FROM claims WHERE patient_control_number='CB0000001C017'").fetchone()[0]
        c.execute("INSERT INTO adjustments (claim_key, line_key, level, group_code, reason_code, amount_cents) VALUES (?,?,?,?,?,?)",
                  (ck, None, "claim", "CO", "253", 12))
        c.commit()
        return out
    monkeypatch.setattr(archive, "_build_records", extra_row)
    hot = counts(conn)
    res = archive.run(conn, sink=sink_of(tmp_path, "file"), today=TODAY)
    assert res["bills"] == 0 and res["errors"] and "do not match" in res["errors"][0]
    assert counts(conn) == dict(hot, adjustments=hot["adjustments"] + 1)       # nothing deleted, the late row kept
    store.close()


class _Truncating(archive.DirSink):
    """An archive folder whose writes come back cut short (a full disk, a broken upload)."""

    def put(self, name, data):
        return super().put(name, data[: len(data) // 2])


def test_an_archive_that_reads_back_damaged_deletes_nothing(tmp_path, frozen_hook):
    store = world(tmp_path, "file")
    detect(store)
    hot = counts(store.conn)
    res = archive.run(store.conn, sink=_Truncating(str(tmp_path / "archive")), today=TODAY)
    assert res["bills"] == 0 and res["errors"]
    assert counts(store.conn) == hot
    store.close()


def test_a_damaged_or_replaced_file_is_refused_and_never_overwritten(tmp_path, frozen_hook):
    store = world(tmp_path, "file")
    detect(store)
    sink = sink_of(tmp_path, "file")
    res = archive.run(store.conn, sink=sink, today=TODAY)
    name = res["files"][0]["name"]
    raw = gzip.decompress(sink.get(name)).replace(b"TEST PROVIDER", b"TEST PROVIDEX")
    with open(sink._p(name), "wb") as fh:
        fh.write(gzip.compress(raw))
    archive._LAST_FILE.clear()
    with pytest.raises(archive.ArchiveError):
        archive.lookup_claim(store.conn, "CB0000001C017", sink=sink)
    with pytest.raises(archive.ArchiveError):
        archive._put_verified(sink, name, gzip.compress(b"other"), archive._sha(b"other"))
    with pytest.raises(ValueError):
        sink.put("../outside.txt", b"x")
    store.close()


# =========================================================================== 5. bringing it back
@pytest.mark.parametrize("kind", ["file", "compact"])
def test_a_reopened_bill_comes_back_exactly_and_stays_hot_for_ninety_days(tmp_path, frozen_hook, kind):
    store = world(tmp_path, kind)
    detect(store)
    conn = store.conn
    where = "claim_key IN (SELECT claim_key FROM claims WHERE patient_control_number='CB0000050C017')"
    snap = {t: table_rows(conn, t, where) for t in ("claims", "lines", "adjustments")}
    f_snap = table_rows(conn, "findings", "patient_control_number='CB0000050C017'")
    sink = sink_of(tmp_path, kind)
    first = archive.run(conn, sink=sink, today=TODAY)
    out = archive.restore(conn, "CB0000050C017", sink=sink, actor="biller", reason="reopened", today=TODAY)
    assert out["rows"]["claims"] == 1 and out["findings"] == 1
    assert {t: table_rows(conn, t, where) for t in snap} == snap
    assert table_rows(conn, "findings", "patient_control_number='CB0000050C017'") == f_snap
    assert conn.execute("SELECT COUNT(*) FROM archive_bills WHERE bill='CB0000050'").fetchone()[0] == 0
    assert archive.search(conn, "CB0000050") == []
    assert archive.run(conn, sink=sink, today=TODAY)["held"] == {"reopened": 1}
    later = archive.run(conn, sink=sink, today="2027-03-01")       # the pin is over: it goes again, to the same content
    assert later["bills"] == 1
    assert len(sink.list("bills")) == 2 and first["files"][0]["name"] in sink.list("bills")    # files are never deleted
    store.close()


def test_new_data_for_an_archived_bill_brings_the_whole_bill_back(tmp_path, frozen_hook):
    store = world(tmp_path, "file")
    detect(store)
    sink = sink_of(tmp_path, "file")
    archive.run(store.conn, sink=sink, today=TODAY)
    assert archive.restore_touched(store.conn, sink=sink, today=TODAY)["restored"] == 0
    # the payer reprocesses the old denial this year: a new remittance for the same claim
    late = claim(50, svc("11900", "93.75", "60.00", "60.00", "CAS*CO*45*33.75~", dos=OLD), dos=OLD)
    ingest(store, tmp_path, [([late], "20261101")], tag="late")
    out = archive.restore_touched(store.conn, sink=sink, today=TODAY)
    assert out["restored"] == 1 and out["changed"]
    assert store.conn.execute("SELECT COUNT(*) FROM claims WHERE patient_control_number='CB0000050C017'").fetchone()[0] == 2
    assert store.conn.execute("SELECT why FROM archive_pins WHERE bill='CB0000050'").fetchone()[0] == "new data arrived"
    store.close()


def test_a_reopening_asked_in_the_app_waits_for_the_cycle_then_sets_the_status(tmp_path, frozen_hook):
    store = world(tmp_path, "file")
    detect(store)
    conn = store.conn
    sink = sink_of(tmp_path, "file")
    fid = conn.execute("SELECT finding_id FROM findings WHERE patient_control_number='CB0000050C017'").fetchone()[0]
    archive.run(conn, sink=sink, today=TODAY)
    # the server's page queues it; a serverless page records a journal entry the cycle turns into the same request
    entry = archive.journal_entry("CB0000050C017", actor="biller", reason="payer agreed to reprocess", finding_id=fid,
                                  status="in_progress", at="2026-11-22T15:00:00+00:00")
    taken, rest = archive.take_journal(conn, [("actions/1.json", entry), ("actions/2.json", {"kind": "status"})], sink=sink)
    assert taken == ["actions/1.json"] and [n for n, _a in rest] == ["actions/2.json"]
    assert conn.execute("SELECT COUNT(*) FROM archive_requests").fetchone()[0] == 1
    assert archive.run(conn, sink=sink, today=TODAY)["bills"] == 0              # asked for: it is not archived again
    out = archive.process_requests(conn, sink=sink, today=TODAY)
    assert out == {"restored": 1, "statuses": 1, "missing": 0, "errors": 0, "changed": True}
    st = conn.execute("SELECT status FROM findings WHERE finding_id=?", (fid,)).fetchone()[0]
    ev = conn.execute("SELECT actor, to_status FROM finding_events WHERE finding_id=? AND kind='manual'", (fid,)).fetchall()
    assert st == "in_progress" and [tuple(e) for e in ev] == [("biller", "in_progress")]
    assert conn.execute("SELECT COUNT(*) FROM archive_requests").fetchone()[0] == 0
    assert archive.request_restore(conn, "XX99NOTHERE", actor="b") == {"missing": True}
    store.close()


@pytest.mark.parametrize("hot,want", [(("in_progress", "newer work"), ("in_progress", "newer work")),
                                      (("open", None), ("dismissed", "old reason"))])
def test_a_bill_coming_back_keeps_a_persons_newer_work_or_brings_back_their_archived_decision(tmp_path, hot, want):
    store = crafted("file", tmp_path)
    put_claim(store, "KA01AAAAA", "2021-03-01")
    put_item(store, "fa", bill="KA01AAAAA", work_state="expired", status="dismissed", notes="old reason")
    freeze_all(store)
    sink = sink_of(tmp_path, "file")
    assert archive.run(store.conn, sink=sink, today=TODAY)["bills"] == 1
    put_item(store, "fa", bill="KA01AAAAA", work_state="work_now", status=hot[0], notes=hot[1])     # listed again meanwhile
    archive.restore(store.conn, "KA01AAAAA", sink=sink, today=TODAY)
    assert tuple(store.conn.execute("SELECT status, notes FROM findings WHERE finding_id='fa'").fetchone()) == want
    store.close()


# =========================================================================== 6. Find a claim
def test_find_a_claim_finds_archived_bills_by_claim_id_bill_and_payer_number_never_by_name(tmp_path):
    store = crafted("file", tmp_path)
    put_claim(store, "KA01AAAAA", "2021-03-01", payer_ref="77-ABC/12345")
    put_claim(store, "KA02BBBBB", "2021-04-01", name="ZEBRA")
    freeze_all(store)
    archive.run(store.conn, sink=sink_of(tmp_path, "file"), today=TODAY)
    assert [x["bill"] for x in archive.search(store.conn, "KA01AAAAA")] == ["KA01AAAAA"]
    assert {x["bill"] for x in archive.search(store.conn, "KA0")} == {"KA01AAAAA", "KA02BBBBB"}
    assert archive.search(store.conn, "KA0")[0]["svc_hi"] == "2021-04-01"           # newest service first
    assert [x["bill"] for x in archive.search(store.conn, "77abc 12345")] == ["KA01AAAAA"]
    assert [x["bill"] for x in archive.search(store.conn, "77ABC1")] == ["KA01AAAAA"]   # the start of a payer number
    assert archive.search(store.conn, "ZEBRA") == [] and archive.search(store.conn, "KA") == []
    hit = archive.search(store.conn, "KA01AAAAAC017")[0]
    assert hit["claim_ids"] == ["KA01AAAAAC017"] and hit["archived"] and hit["exact"]
    assert archive.resolve_bill(store.conn, "77-abc/12345") == "KA01AAAAA"
    store.close()


def test_a_bill_known_only_from_ema_exports_or_837_files_moves_and_comes_back(tmp_path):
    store = crafted("compact", tmp_path)
    from remitwatch.x12_837 import DDL as DDL_837
    store.conn.executescript(DDL_837)
    store.conn.execute("""INSERT INTO ema_rows (row_fingerprint, family, claim_id, service_date, cpt, charges_cents)
                          VALUES ('fp1', 'charge_lines', 'EM01ZZZZZC017', '2021-05-01', '99213', 10000)""")
    store.conn.execute("""INSERT INTO claims_837 (claim_837_key, patient_control_number, service_date, total_charge_cents)
                          VALUES ('k837', 'EM02YYYYYC017', '2021-05-01', 10000)""")
    store.conn.execute("""INSERT INTO lines_837 (line_837_key, claim_837_key, patient_control_number, service_date, procedure_code)
                          VALUES ('l837', 'k837', 'EM02YYYYYC017', '2021-05-01', '99213')""")
    store.conn.commit()
    put_item(store, "fe", bill="EM01ZZZZZ", work_state="expired")
    sink = sink_of(tmp_path, "compact")
    res = archive.run(store.conn, sink=sink, today=TODAY)
    assert res["bills"] == 2 and res["rows"]["ema_rows"] == 1 and res["rows"]["claims_837"] == 1 and res["rows"]["lines_837"] == 1
    assert store.conn.execute("SELECT COUNT(*) FROM ema_rows").fetchone()[0] == 0
    assert [x["bill"] for x in archive.search(store.conn, "EM01ZZZZZ")] == ["EM01ZZZZZ"]
    assert [x["bill"] for x in archive.search(store.conn, "EM02Y")] == ["EM02YYYYY"]
    archive.restore(store.conn, "EM01ZZZZZC017", sink=sink, today=TODAY)
    archive.restore(store.conn, "EM02YYYYY", sink=sink, today=TODAY)
    assert store.conn.execute("SELECT COUNT(*) FROM ema_rows").fetchone()[0] == 1
    assert store.conn.execute("SELECT COUNT(*) FROM claims_837").fetchone()[0] == 1
    assert store.conn.execute("SELECT COUNT(*) FROM lines_837").fetchone()[0] == 1
    assert store.conn.execute("SELECT status FROM findings WHERE finding_id='fe'").fetchone()[0] == "open"
    store.close()


# =========================================================================== 7. A/R snapshots and the ledger
def test_ar_snapshots_older_than_13_months_go_to_the_archive(tmp_path):
    from remitwatch import ema_reports as er
    store = crafted("file", tmp_path)
    er.ensure_schema(store.conn)
    for fid, as_of in (("ar-old", "2025-09-30"), ("ar-new", "2026-11-20")):
        store.conn.execute("INSERT INTO ema_report_files (file_id, report, status, as_of, live_rows) VALUES (?,?,?,?,?)",
                           (fid, "ar_report", "current", as_of, 2))
        for i in range(2):
            store.conn.execute("INSERT INTO ema_ar (file_id, row_no, as_of, svc_year, claim_id, balance_cents) VALUES (?,?,?,?,?,?)",
                               (fid, i, as_of, as_of[:4], f"AR0{i}XXXXXC017", 1000))
    store.conn.commit()
    sink = sink_of(tmp_path, "file")
    out = archive.archive_ar(store.conn, sink, today=TODAY)
    assert out["files"] == 1 and out["rows"] == 2
    assert [r[0] for r in store.conn.execute("SELECT DISTINCT file_id FROM ema_ar")] == ["ar-new"]
    st = store.conn.execute("SELECT status, live_rows, replaced_by FROM ema_report_files WHERE file_id='ar-old'").fetchone()
    assert st[0] == "replaced" and st[1] == 0 and st[2].startswith("archive:ar/2025-09-30-")
    recs = [json.loads(x) for x in gzip.decompress(sink.get(out["names"][0])).split(b"\n") if x.strip()]
    assert recs[1]["file_id"] == "ar-old" and len(recs[1]["tables"]["ema_ar"]["rows"]) == 2
    assert archive.archive_ar(store.conn, sink, today=TODAY)["files"] == 0           # once
    assert archive.archive_ar(store.conn, sink, today="2027-12-21")["files"] == 1     # the newer one, 13 months on
    store.close()


def _ledger(tmp_path, scratch=False):
    from remitwatch import ema_ledger as el
    lg = el.Ledger.open(str(tmp_path / "ema_ledger.sqlite"), scratch=scratch)
    c = lg.conn
    c.execute("INSERT INTO names (id, name) VALUES (1, 'ACME HEALTH'), (2, 'payment')")
    c.execute("INSERT INTO ledger_files (src, file_id, scope, status, rows, live_rows) VALUES (1, 'prod-a', 'service', 'current', 6, 6)")
    rows = [("2022-05-01", "2022-04-01", 11, 1, 2), ("2022-05-01", "2022-04-01", 11, 1, 2),       # two identical postings: both kept
            ("2023-01-03", "2023-01-01", 12, 1, 2), ("2023-02-01", "2023-12-31", 13, 1, 2),
            ("2026-02-01", "2026-01-15", 11, 1, 2), ("2026-02-01", "2026-01-20", None, 1, 2)]
    c.executemany("INSERT INTO postings (src, posted, svc, link, payer, kind, ins_paid) VALUES (1, ?, ?, ?, ?, ?, 500)", rows)
    return lg


def test_ledger_postings_are_archived_before_retention_deletes_them(tmp_path):
    lg = _ledger(tmp_path)
    sink = archive.DirSink(str(tmp_path / "archive"))
    archive.attach_ledger(lg, sink)
    cutoff = "2023-12-31"                                    # a posting dated exactly on the cutoff stays
    out = archive.before_ledger_retain(lg, cutoff)
    assert out["rows"] == 3 and out["file"].startswith("ledger/2022-04-01_2023-01-01-")
    deleted = lg.conn.execute("DELETE FROM postings WHERE svc < ?", (cutoff,)).rowcount
    assert deleted == out["rows"]
    got = archive.ledger_postings(sink, [11])
    assert len(got) == 2 and got[0]["payer"] == "ACME HEALTH" and got[0]["kind"] == "payment"    # names written out
    assert archive.ledger_postings(sink, [12, 13]) and archive.ledger_postings(sink, [99]) == []
    assert lg.conn.execute("SELECT rows FROM archive_parts").fetchone()[0] == 3
    assert archive.before_ledger_retain(lg, cutoff) == {"rows": 0}
    lg.close()


def test_a_ledger_with_nowhere_lasting_to_write_is_not_archived_and_a_bad_folder_stops_the_delete(tmp_path):
    lg = _ledger(tmp_path, scratch=True)
    assert archive.before_ledger_retain(lg, "2024-01-01") == {"skipped": "no archive folder for this ledger"}
    with pytest.raises(archive.ArchiveError):
        archive.before_ledger_retain(lg, "2024-01-01", sink=_Truncating(str(tmp_path / "archive")))
    assert lg.conn.execute("SELECT COUNT(*) FROM postings").fetchone()[0] == 6
    lg.close()


# =========================================================================== 8. memory, size and the panel
def test_a_run_over_80_percent_of_its_budget_tightens_the_horizon_down_to_the_floor(tmp_path):
    store = crafted("file", tmp_path)
    conn = store.conn
    archive.ensure_schema(conn)
    env = {}
    h = archive.effective_horizon(conn, env=env)
    assert (h["months"], h["tightened"]) == (36, False)
    for want in (30, 24, 24):
        h = archive.effective_horizon(conn, budget_state={"peak_mb": 1400, "budget_mb": 1600}, env=env)
        assert h["months"] == want and h["tightened"]
        archive._meta_put(conn, archive.META_HORIZON, h)
    assert archive.effective_horizon(conn, env=env)["months"] == 24           # kept tighter
    h = archive.effective_horizon(conn, budget_state={"peak_mb": 800, "budget_mb": 1600}, env=env)
    assert h["months"] == 30                                                   # relaxed a step
    assert archive.effective_horizon(conn, env={"REMITWATCH_RETENTION_MONTHS": "6"})["configured"] == 24
    assert archive.effective_horizon(conn, horizon=12, floor=0, env=env)["months"] == 12
    assert archive._months_before(TODAY, 36) == "2023-11-01"
    store.close()


@pytest.mark.parametrize("dbstat", [True, False])
def test_size_memory_and_the_panel_warn_at_80_percent_naming_the_largest_tables(tmp_path, frozen_hook, dbstat):
    store = world(tmp_path, "file")
    detect(store)
    conn = store.conn
    m = archive.measure(conn, peak=1280, mode="serverless", use_dbstat=dbstat)
    assert m["budget_mb"] == 1600 and m["share"] == 0.8 and m["level"] == "warn" and m["exact"] is (dbstat and HAS_DBSTAT)
    assert m["tables"] and all(t["bytes"] > 0 for t in m["tables"])
    res = archive.run(conn, sink=sink_of(tmp_path, "file"), today=TODAY, run_id="a1")
    archive.record_run(conn, run_id="a1", measured=m, result=res, today=TODAY)
    health = {i["key"]: i for i in archive.data_health(conn)}
    assert not health["store_memory"]["ok"] and "1,280 MB of its 1,600 MB" in health["store_memory"]["title"]
    assert m["tables"][0]["table"] in health["store_memory"]["detail"]
    assert health["archive"]["ok"] and "9 older settled claims" in health["archive"]["title"]
    # the panel reads what the run stored: a copy holding only meta (the dashboard's copy) shows the same
    copy = sqlite3.connect(":memory:")
    copy.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    copy.executemany("INSERT INTO meta VALUES (?,?)", conn.execute("SELECT key, value FROM meta WHERE key LIKE 'archive:%'").fetchall())
    p = archive.panel(copy)
    assert p["memory"]["level"] == "warn" and p["archive"]["bills"] == 9 and p["last_retired"]["bills"] == 9
    assert p["horizon"]["months"] == 36 and any("1,280 MB" in s for s in p["lines"])
    ok = archive.measure(conn, peak=1270, mode="serverless")
    assert ok["level"] == "ok"
    store.close()


def test_compact_gives_back_the_pages_the_archive_freed_on_the_serverless_image(tmp_path, frozen_hook):
    store = Store(":memory:", compact=True)
    ingest(store, tmp_path, [(visits(1, 60, OLD), "20220415"), (visits(500, 4, NEW), "20260415")])
    detect(store)
    store = as_image(store)
    before = len(store.to_bytes())
    archive.run(store.conn, sink=sink_of(tmp_path, "compact"), today=TODAY)
    assert archive.compact(store.conn, min_free_share=0.99)["skipped"]
    out = archive.compact(store.conn, min_free_share=0.01)
    assert out["after"] < out["before"] and len(store.to_bytes()) < before
    again = as_image(store)
    assert again.conn.execute("SELECT COUNT(*) FROM archive_bills").fetchone()[0] == 60
    again.close()


# =========================================================================== 9. the run itself
def test_batches_and_the_deadline_split_the_work_across_runs(tmp_path, frozen_hook):
    store = world(tmp_path, "file")
    detect(store)
    sink = sink_of(tmp_path, "file")
    res = archive.run(store.conn, sink=sink, today=TODAY, deadline=time.time() - 1)
    assert res["bills"] == 0 and res["more"]
    res = archive.run(store.conn, sink=sink, today=TODAY, batch_bills=4, max_bills=5)
    assert res["bills"] == 5 and len(res["files"]) == 2 and res["more"]
    res = archive.run(store.conn, sink=sink, today=TODAY, batch_bills=4)
    assert res["bills"] == 4 and not res["more"]
    assert store.conn.execute("SELECT COUNT(*) FROM archive_bills").fetchone()[0] == 9
    store.close()


def _correlated_scans(plan: list) -> list[str]:
    """Plan steps that scan a table (any table: a temporary key table scanned per row is as slow) inside a correlated
    subquery."""
    node = {r[0]: (r[1], r[3]) for r in plan}
    bad = []
    for _nid, (parent, detail) in node.items():
        if not detail.startswith("SCAN ") or "AUTOMATIC" in detail or "CONSTANT" in detail:
            continue
        p = parent
        while p in node:
            if node[p][1].startswith("CORRELATED"):
                bad.append(f"{detail} inside {node[p][1]}")
                break
            p = node[p][0]
    return bad


class _PlanCheck:
    """The store connection as the run sees it, explaining each statement just before it runs (while its temporary key
    tables exist)."""

    def __init__(self, conn):
        self._c = conn
        self.problems: dict = {}
        self.explained = 0

    def execute(self, sql, params=()):
        if sql.lstrip().upper().startswith(("SELECT", "INSERT", "DELETE", "UPDATE", "WITH")):
            try:
                plan = self._c.execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()
                self.explained += 1
            except sqlite3.Error:
                plan = []
            bad = _correlated_scans(plan)
            if bad:
                self.problems[" ".join(sql.split())[:140]] = bad
        return self._c.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._c, name)


def test_the_compact_store_is_read_once_per_table_never_per_row(tmp_path, frozen_hook):
    store = Store(":memory:", compact=True)
    ingest(store, tmp_path, [(visits(1, 30, OLD) + [denial(90, OLD)], "20220415"), (visits(500, 2, NEW), "20260415")])
    detect(store)
    checked = _PlanCheck(store.conn)
    res = archive.run(checked, sink=sink_of(tmp_path, "compact"), today=TODAY, batch_bills=8)
    assert res["bills"] == 31 and checked.explained > 40
    assert not checked.problems, checked.problems
    out = archive.restore(checked, "CB0000090C017", sink=sink_of(tmp_path, "compact"), today=TODAY)
    assert out["rows"]["claims"] == 1 and not checked.problems, checked.problems
    store.close()


def test_no_claim_id_or_name_reaches_the_log_or_the_run_summary(tmp_path, frozen_hook):
    store = world(tmp_path, "file")
    detect(store)
    said: list[str] = []
    res = archive.run(store.conn, sink=sink_of(tmp_path, "file"), today=TODAY, log=lambda *a: said.append(" ".join(map(str, a))))
    m = archive.measure(store.conn, peak=100, mode="server", limit_mb=2000)
    archive.record_run(store.conn, run_id="a1", measured=m, result=res, today=TODAY)
    text = " ".join(said) + json.dumps(res) + json.dumps(archive.panel(store.conn)) + json.dumps(archive.data_health(store.conn))
    pcns = [r[0] for r in store.conn.execute("SELECT id FROM archive_ids")]
    assert said and pcns
    assert not any(p in text for p in pcns) and "SAMPLE" not in text and not re.search(r"CB\d{7}", text)
    store.close()
