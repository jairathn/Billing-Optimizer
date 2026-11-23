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


def test_an_old_service_paid_inside_the_horizon_stays_and_an_open_remittance_item_keeps_its_remittance_whole(tmp_path):
    store = crafted("file", tmp_path)
    put_claim(store, "KA01AAAAA", "2021-03-01", paid_on="2026-06-01")         # paid this year: the payer's recent activity
    put_claim(store, "KB01AAAAA", "2021-03-01")
    put_claim(store, "KC01AAAAA", "2021-03-01")
    put_claim(store, "KD01AAAAA", "2021-04-01")
    store.conn.execute("UPDATE claims SET transaction_key='T-SHARED' WHERE patient_control_number IN ('KB01AAAAAC017','KC01AAAAAC017')")
    store.conn.execute("UPDATE lines SET transaction_key='T-SHARED' WHERE patient_control_number IN ('KB01AAAAAC017','KC01AAAAAC017')")
    store.conn.execute("""INSERT INTO findings (finding_id, signal, category, transaction_key, status, work_state, shortfall_cents)
                          VALUES ('remit', 'REMIT_NOT_POSTED', 'fees', 'T-SHARED', 'open', 'work_now', 4000)""")
    store.conn.commit()
    freeze_all(store)
    p = archive.plan(store.conn, today=TODAY)
    assert [b for b, *_ in p["bills"]] == ["KD01AAAAA"] and p["held"] == {"open item": 2}
    store.close()


def test_a_bill_another_items_proof_names_stays_until_the_item_goes(tmp_path):
    """A duplicate denial on a newer bill was closed as "already paid under claim ..." of an older bill: the older bill is
    the proof and stays while the item does. So does a bill whose line an archive rule's proof row names."""
    store = crafted("file", tmp_path)
    put_claim(store, "KA01AAAAA", "2021-03-01")                                 # the paid original: the proof
    keys = put_claim(store, "KB01AAAAA", "2021-03-01")                          # a line another item's evidence names
    store.conn.execute("UPDATE lines SET line_key='LK-OPAQUE-1' WHERE line_key=?", (keys[0],))   # keys name no claim id
    store.conn.execute("UPDATE adjustments SET line_key='LK-OPAQUE-1' WHERE line_key=?", (keys[0],))
    keys = ["LK-OPAQUE-1"]
    put_claim(store, "KC01AAAAA", "2021-03-01")                                 # nothing names it
    put_claim(store, "KD01AAAAA", "2026-02-01")                                 # the newer duplicate, still in the store
    put_item(store, "dup", bill="KD01AAAAA", work_state="done", status="resolved")
    store.conn.execute("UPDATE findings SET auto_note=?, evidence=? WHERE finding_id='dup'",
                       ("Already paid under claim KA01AAAAAC017 (paid in full): this denial is of a duplicate",
                        json.dumps({"archive_rule": {"proof": [{"line_key": keys[0], "source": "835"}]}})))
    store.conn.commit()
    freeze_all(store)
    p = archive.plan(store.conn, today=TODAY)
    assert [b for b, *_ in p["bills"]] == ["KC01AAAAA"] and p["held"] == {"named by another item": 2}
    # an item of a bill that leaves names nothing: the bill it names leaves in the same run, and a second run has nothing
    put_claim(store, "KE01AAAAA", "2021-05-01")
    put_claim(store, "KF01AAAAA", "2021-05-01")
    put_item(store, "old-dup", bill="KE01AAAAA", work_state="done", status="resolved")
    store.conn.execute("UPDATE findings SET auto_note='Already paid under claim KF01AAAAAC017' WHERE finding_id='old-dup'")
    store.conn.commit()
    sink = sink_of(tmp_path, "file")
    res = archive.run(store.conn, sink=sink, today=TODAY)
    assert res["bills"] == 3 and res["held"] == {"named by another item": 2}        # KC01, KE01 and KF01 leave
    assert archive.run(store.conn, sink=sink, today=TODAY)["eligible"] == 0
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


def _sequestered(start: int, n: int, dos: str) -> list[str]:
    return [claim(start + i, svc("99214", "223.65", "115.25", "117.60", "CAS*CO*45*106.05~CAS*CO*253*2.35~", dos=dos), dos=dos)
            for i in range(n)]


def test_a_locked_years_totals_stay_as_measured_on_the_whole_year(tmp_path, frozen_hook, monkeypatch):
    """Sequestration withheld in 2023 is a yearly total of the 835 lines. Once the archive takes 2023's bills, the
    total measured on what is left would vanish; the carried item keeps the whole year's figure."""
    monkeypatch.setattr(Detector, "save", lambda self, _orig=Detector.save: (archive.carry_locked_aggregates(self), _orig(self))[1])
    store = Store(":memory:", compact=True)
    ingest(store, tmp_path, [(_sequestered(1, 12, "20230310"), "20230415"), (visits(100, 4, NEW), "20260415")])
    detect(store)
    seq = {r[0]: r[1] for r in store.conn.execute("SELECT finding_id, shortfall_cents FROM findings WHERE signal='SEQUESTRATION'")}
    assert list(seq.values()) == [12 * 235]
    res = archive.run(store.conn, sink=sink_of(tmp_path, "compact"), today=TODAY, horizon=24)
    assert res["bills"] == 12 and "2023" in archive._locked_years(store.conn)
    detect(store, "r2")
    assert {r[0]: r[1] for r in store.conn.execute("SELECT finding_id, shortfall_cents FROM findings WHERE signal='SEQUESTRATION'")} == seq
    store.close()


def test_a_new_pattern_measured_on_what_the_archive_left_is_dropped_and_its_lines_items_come_back(tmp_path):
    store = crafted("compact", tmp_path)
    store.conn.execute("""INSERT INTO findings (finding_id, signal, category, year, status, work_state, shortfall_cents, evidence)
                          VALUES ('total-2023', 'SEQUESTRATION', 'info', '2023', 'open', 'accepted', 2820, '{}')""")
    store.conn.execute("""INSERT INTO findings (finding_id, signal, category, year, line_key, patient_control_number, status,
                          work_state, shortfall_cents) VALUES ('one-off', 'UNDERPAID_VS_OWN_RATE', 'underpayment', '2023', 'L-1',
                          'KA01AAAAAC017', 'open', 'expired', 2760)""")
    store.conn.commit()
    archive._meta_put(store.conn, archive.META_LOCKED, ["2023"])

    class Det:
        conn = store.conn
        findings = {"new-pattern": {"finding_id": "new-pattern", "signal": "SECOND_RATE", "year": "2023",
                                    "evidence": json.dumps({"line_keys": ["L-1"]})},
                    "total-2023": {"finding_id": "total-2023", "signal": "SEQUESTRATION", "year": "2023", "shortfall_cents": 900},
                    "fees-2023": {"finding_id": "fees-2023", "signal": "FEE_SKIM", "year": "2023", "shortfall_cents": 500},
                    "now": {"finding_id": "now", "signal": "SEQUESTRATION", "year": "2026", "shortfall_cents": 100}}
    det = Det()
    out = archive.carry_locked_aggregates(det)
    assert out == {"kept": 1, "dropped": 1, "put_back": 1}
    assert det.findings["total-2023"]["shortfall_cents"] == 2820                   # the whole year's figure
    assert "new-pattern" not in det.findings and det.findings["one-off"]["shortfall_cents"] == 2760
    assert det.findings["fees-2023"]["shortfall_cents"] == 500 and det.findings["now"]["shortfall_cents"] == 100
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
        # a note on the settled item (it stays settled: no open status that would keep the bill on its own)
        c.execute("UPDATE findings SET notes='the payer agreed to look again' WHERE finding_id=?", (fid,))
        c.commit()
        return out
    monkeypatch.setattr(archive, "_build_records", meanwhile)
    res = archive.run(conn, sink=sink_of(tmp_path, "file"), today=TODAY)
    assert res["changed_meanwhile"] == 1 and res["bills"] == 8
    assert conn.execute("SELECT notes FROM findings WHERE finding_id=?", (fid,)).fetchone()[0] == "the payer agreed to look again"
    assert conn.execute("SELECT COUNT(*) FROM claims WHERE patient_control_number='CB0000050C017'").fetchone()[0] == 1
    assert conn.execute("SELECT 1 FROM archive_bills WHERE bill='CB0000050'").fetchone() is None
    store.close()


def test_a_file_whose_every_bill_a_person_changed_meanwhile_is_written_but_not_listed(tmp_path, frozen_hook, monkeypatch):
    store = world(tmp_path, "file")
    detect(store)
    conn = store.conn
    real = archive._build_records

    def meanwhile(c, bills):
        out = real(c, bills)
        c.execute("UPDATE findings SET status='in_progress' WHERE patient_control_number='CB0000050C017'")
        c.commit()
        return out
    monkeypatch.setattr(archive, "_build_records", meanwhile)
    sink = sink_of(tmp_path, "file")
    res = archive.run(conn, sink=sink, today=TODAY, batch_bills=1)
    assert res["bills"] == 8 and res["changed_meanwhile"] == 1 and len(sink.list("bills")) == 9
    assert conn.execute("SELECT COUNT(*) FROM archive_files").fetchone()[0] == 8
    assert archive.summary(conn)["files"] == 8
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
    # the last run's memory, as recorded, is what the next run tightens on
    assert archive.budget_state(conn) is None
    archive.record_run(conn, run_id="a", measured={"peak_mb": 1450, "budget_mb": 1600, "share": 0.906, "mode": "serverless"})
    archive.record_run(conn, run_id="b", measured={"budget_mb": 1600, "mode": "serverless"})   # a run with no peak measured
    assert archive.budget_state(conn) == {"peak_mb": 1450, "budget_mb": 1600, "share": 0.906}
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
    for parent, detail in node.values():
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


# =========================================================================== 10. EMA's reports still name the bill
# (the review's finding A: a bill whose 835 left while EMA's posting summaries kept it came back as "denied in EMA, no
# remittance on file", with a deadline still open). The EMA side goes through the real readers, the posting ledger and its
# refresh, as tests/test_ema_checks.py builds it; every row is invented (tests/ema_fixtures.py).
from tests import ema_fixtures as EF  # noqa: E402
from tests.test_ema_checks import adj as ema_adj, charge as ema_charge, load as ema_load, pay as ema_pay  # noqa: E402
from tests.test_ema_checks import remit as ema_remit  # noqa: E402

EMA_TODAY = "2026-05-03"            # the 24-month horizon's cutoff is 2024-05-01
D = __import__("datetime").date


@pytest.fixture
def ema_world(tmp_path, monkeypatch, request):
    """A store with the EMA tables and a posting ledger, set up as tests/test_ema_checks.py's fixtures are: the server's
    file store, or (indirect parameter 'compact') the compact in-memory store a serverless run loads."""
    from remitwatch import ema_checks as ec, ema_ledger as el, ema_reports as er
    monkeypatch.setattr(er, "CLAIM_HOST_SUFFIX", EF.HOST_SUFFIX)
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    for k in ("VERCEL", "REMITWATCH_EMA_REPORTS", "REMITWATCH_EMA_CLAIM_URL", "REMITWATCH_EMA_PAYER_ALIASES",
              "REMITWATCH_EMA_LEDGER", "REMITWATCH_EMA_REPORT_KEEP"):
        monkeypatch.delenv(k, raising=False)
    ec._LAST_POST.clear()
    kind = getattr(request, "param", "file")
    store = Store(str(tmp_path / "store.sqlite")) if kind == "file" else Store(":memory:", compact=True)
    er.ensure_schema(store.conn)
    store.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('ema:key_salt', ?)", ("cd" * 32,))
    store.conn.commit()
    lg = el.Ledger.open(str(tmp_path / "ema_ledger.sqlite"))
    yield store, lg
    lg.close()
    store.close()


def _c5(store) -> list:
    from remitwatch import ema_checks as ec
    return [(i["signal"], i.get("appeal_deadline")) for i in ec.ema_denials(store.conn, today=EMA_TODAY)]


def _denied_835(store) -> None:
    """Claim 1: a 99213 denied by its 835 (service 2024-03-02, the $0 answer on 2024-03-28, CO-197)."""
    ema_remit(store, trace=EF.check_no(1), paid_on="2024-03-28",
              claims=[{"pcn": EF.claim_id(1), "lines": [("2024-03-02", "99213", 150, 0, [("CO", "197", 150)])]}])


def _ema_rows(posted=D(2024, 6, 15)) -> list:
    """EMA posted the same $0 answer under the 835's check and the CO-197 on `posted`; a recent charge on claim 99 keeps
    Production fresh."""
    svc = D(2024, 3, 2)
    return [ema_charge(EF.link_no(1), svc=svc, status="Resolved/Paid"),
            ema_pay(EF.link_no(1), EF.check_no(1), 0, svc=svc, posted=posted, status="Resolved/Paid"),
            ema_adj(EF.link_no(1), "CO-197", 150, svc=svc, posted=posted, status="Resolved/Paid"),
            ema_charge(EF.link_no(99), cpt="99212", patient=3, svc=D(2026, 3, 20), posted=D(2026, 5, 1), status="Payment Pending")]


def _analysed(conn) -> None:
    """What the analysis that ran before the archive leaves: a detection run, kept baselines, the 835's own item (its
    windows long closed)."""
    detect_schema(conn)
    conn.execute("INSERT INTO detection_runs (run_id, started_at, finished_at, version) VALUES ('r0', ?, ?, 't')", (EMA_TODAY, EMA_TODAY))
    archive.ensure_schema(conn)
    archive._meta_put(conn, archive.META_FROZEN, {y: {"at": EMA_TODAY, "digest": "x", "rates": 0} for y in ("2023", "2024")})
    conn.execute("""INSERT INTO findings (finding_id, signal, category, patient_control_number, status, work_state, stale,
                    service_date, shortfall_cents) VALUES ('den-1', 'DENIED_AUTH', 'denial', ?, 'open', 'expired', 0,
                    '2024-03-02', 9000)""", (EF.claim_id(1),))
    conn.commit()


def _ema_named_bill(store, ledger, tmp_path, posted=D(2024, 6, 15)) -> None:
    from remitwatch import ema_checks as ec
    _denied_835(store)
    ema_load(store, ledger, tmp_path, _ema_rows(posted))
    ec.classify_checks(store.conn)
    assert _c5(store) == []                                  # the 835 covers the denial: no EMA-only item
    _analysed(store.conn)


@pytest.mark.parametrize("posted", [D(2024, 6, 15), D(2024, 4, 15)])      # EMA's posting after the cutoff, and before it
@pytest.mark.parametrize("ema_world", ["file", "compact"], indirect=True)
def test_a_bill_emas_reports_still_list_keeps_its_835_and_is_never_listed_as_denied_with_no_remittance(ema_world, tmp_path,
                                                                                                        posted):
    """A 24-month horizon (the floor) and an old bill whose items are all closed: while EMA's posting summaries still list
    it (the ledger keeps a line with a payer's denial posted for its whole retention, longer than the floor), its 835 rows
    stay, so EMA's checks still find the remittance that answered it. Both store kinds (the compact one is saved and
    loaded again as the serverless run does)."""
    store, ledger = ema_world
    kind = "file" if store.conn.execute("PRAGMA database_list").fetchone()[2] else "compact"
    _ema_named_bill(store, ledger, tmp_path, posted)
    if kind == "compact":
        store = as_image(store)
    p = archive.plan(store.conn, today=EMA_TODAY, horizon=24)
    assert p["bills"] == [] and p["held"] == {archive.HELD_EMA: 1}
    res = archive.run(store.conn, sink=sink_of(tmp_path, kind), today=EMA_TODAY, horizon=24)
    assert res["bills"] == 0 and not res["errors"]
    assert store.conn.execute("SELECT COUNT(*) FROM claims WHERE patient_control_number=?", (EF.claim_id(1),)).fetchone()[0] == 1
    assert _c5(store) == []
    if kind == "compact":
        store.close()


def test_end_to_end_the_next_analysis_puts_no_item_of_an_old_bill_emas_reports_list_on_the_worklist(ema_world, tmp_path):
    store, ledger = ema_world
    _ema_named_bill(store, ledger, tmp_path)
    archive.run(store.conn, sink=sink_of(tmp_path, "file"), today=EMA_TODAY, horizon=24)
    Detector(store, run_id="after", today=EMA_TODAY).run()
    rows = store.conn.execute("""SELECT signal, work_state FROM findings WHERE patient_control_number LIKE ? AND COALESCE(stale,0)=0
                                 AND signal = 'EMA_DENIED'""", (EF.bill_id(1) + "%",)).fetchall()
    assert [tuple(r) for r in rows] == []


def test_the_map_refresh_and_a_claim_the_ar_ties_to_its_bill_keep_it_too(ema_world, tmp_path):
    """Detection's map refresh (Part B, when new 835s arrive) and a full refresh with the A/R's claim id tying the EMA
    claim number to the bill: the bill is held either way, and EMA's checks list nothing."""
    from remitwatch import ema_checks as ec, ema_ledger as el
    store, ledger = ema_world
    store.conn.execute("INSERT INTO ema_claim_links (claim_id, link, bill, first_seen, last_seen) VALUES (?,?,?,?,?)",
                       (EF.claim_id(1), EF.link_no(1), EF.bill_id(1), "2026-04-30", "2026-04-30"))
    store.conn.commit()
    _ema_named_bill(store, ledger, tmp_path, D(2024, 3, 31))
    res = archive.run(store.conn, sink=sink_of(tmp_path, "file"), today=EMA_TODAY, horizon=24)
    assert res["bills"] == 0 and res["held"] == {archive.HELD_EMA: 1}
    store.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('ema:map_dirty', '1')")
    store.conn.commit()
    el.refresh(store, None, parts="B", log=lambda *a: None)
    assert _c5(store) == []
    el.refresh(store, ledger, parts="AB", log=lambda *a: None)
    ec.classify_checks(store.conn)
    assert _c5(store) == []


def test_an_archived_bill_emas_reports_name_again_comes_back_before_the_analysis(ema_world, tmp_path):
    """The bill left while EMA had nothing on it. Later the A/R ties its claim id to an EMA claim number and Production
    posts the payer's denial on it: EMA's summaries name the bill again, so it comes back whole (restore_touched) and
    EMA's checks find its remittance."""
    from remitwatch import ema_checks as ec
    store, ledger = ema_world
    _denied_835(store)
    _analysed(store.conn)
    sink = sink_of(tmp_path, "file")
    assert archive.run(store.conn, sink=sink, today=EMA_TODAY, horizon=24)["bills"] == 1
    assert archive.restore_touched(store.conn, sink=sink, today=EMA_TODAY)["restored"] == 0
    store.conn.execute("INSERT INTO ema_claim_links (claim_id, link, bill, first_seen, last_seen) VALUES (?,?,?,?,?)",
                       (EF.claim_id(1), EF.link_no(1), EF.bill_id(1), "2026-04-30", "2026-04-30"))
    store.conn.commit()
    ema_load(store, ledger, tmp_path, _ema_rows())
    ec.classify_checks(store.conn)
    out = archive.restore_touched(store.conn, sink=sink, today=EMA_TODAY)
    assert out["restored"] == 1 and out["why"] == {"EMA lists it again": 1}
    assert store.conn.execute("SELECT COUNT(*) FROM claims WHERE patient_control_number=?", (EF.claim_id(1),)).fetchone()[0] == 1
    assert _c5(store) == []
    # and it stays while EMA lists it: the next run takes nothing
    assert archive.run(store.conn, sink=sink, today="2026-09-01", horizon=24)["bills"] == 0


def test_control_without_the_archive_the_analysis_lists_no_ema_only_denial_for_the_bill(ema_world, tmp_path):
    store, ledger = ema_world
    _denied_835(store)
    ema_load(store, ledger, tmp_path, _ema_rows())
    Detector(store, run_id="no-archive", today=EMA_TODAY).run()
    sig = [r[0] for r in store.conn.execute("SELECT signal FROM findings WHERE patient_control_number LIKE ? AND COALESCE(stale,0)=0",
                                            (EF.bill_id(1) + "%",))]
    assert "EMA_DENIED" not in sig


# =========================================================================== 11. compacting the serverless image
# (the review's finding C: VACUUM wrote the compacted image to a file in the temporary folder, which on Vercel is 512 MB and
# shared with the dashboard's copy; the hook called it unguarded, so every idle run would end in error)
_COMPACT_SCRIPT = r'''
import json, resource, sqlite3, sys
sys.path.insert(0, sys.argv[1])
from remitwatch import archive
from remitwatch.store import Store
limit = int(sys.argv[2])
s = Store(":memory:", compact=True)
blob = "x" * 300
s.conn.executemany("INSERT INTO remarks (transaction_key, claim_key, line_key, level, qualifier, code) VALUES (?,?,?,?,?,?)",
                   ((f"T{i}", f"C{i}", f"L{i}" + blob, "line", "HE", "N123") for i in range(80_000)))
s.conn.commit()
s.conn.execute("DELETE FROM remarks WHERE id <= 30000")          # what an archive run frees: whole pages
s.conn.commit()
if hasattr(sqlite3.Connection, "serialize"):
    img = Store.from_bytes(s.to_bytes())                         # the image a serverless run loads (cloud.load_raw)
    s.close()
else:                                                            # a Python without serialize: the same in-memory store
    img = s
ps, pc, fl = (img.conn.execute(f"PRAGMA {p}").fetchone()[0] for p in ("page_size", "page_count", "freelist_count"))
ts = img.conn.execute("PRAGMA temp_store").fetchone()[0]
resource.setrlimit(resource.RLIMIT_FSIZE, (limit << 20, limit << 20))
out = archive.compact(img.conn, budget=1_000_000)
print(json.dumps({"out": out, "compacted_mb": (ps * (pc - fl)) >> 20, "rows": img.conn.execute("SELECT COUNT(*) FROM remarks").fetchone()[0],
                  "temp_store": [ts, img.conn.execute("PRAGMA temp_store").fetchone()[0]]}))
'''


@pytest.mark.skipif(sys.platform == "win32", reason="the file-size limit is a POSIX resource limit")
def test_compact_on_the_serverless_image_does_not_need_the_images_size_in_the_temporary_folder(tmp_path):
    """The process may write only 4 MB to any file; the compacted image is about 18 MB. VACUUM still works (it builds
    the copy in memory), the rows are all there, and the connection's temp_store is as it was."""
    import subprocess
    script = tmp_path / "compact_tmp.py"
    script.write_text(_COMPACT_SCRIPT)
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    run = subprocess.run([sys.executable, "-I", str(script), repo, "4"], capture_output=True, text=True, timeout=300)
    assert run.returncode == 0, run.stderr[-2000:]
    got = json.loads(run.stdout.strip().splitlines()[-1])
    assert got["compacted_mb"] > 4                       # it would not fit in the file space the process has
    assert "skipped" not in got["out"], got["out"]
    assert got["out"]["after"] < got["out"]["before"] and got["rows"] == 50_000
    assert got["temp_store"][0] == got["temp_store"][1]


def test_compact_waits_when_the_memory_budget_has_no_room_and_never_raises(tmp_path, frozen_hook):
    store = Store(":memory:", compact=True)
    ingest(store, tmp_path, [(visits(1, 60, OLD), "20220415"), (visits(500, 4, NEW), "20260415")])
    detect(store)
    archive.run(store.conn, sink=sink_of(tmp_path, "compact"), today=TODAY)
    size = store.conn.execute("PRAGMA page_count").fetchone()[0]
    # 1,600 MB budget, 1,590 MB held: no room for the compacted copy
    out = archive.compact(store.conn, min_free_share=0.01, budget=1600, rss=1590)
    assert out["skipped"].startswith("not enough memory") and out["need_mb"] >= archive.COMPACT_MARGIN_MB
    assert store.conn.execute("PRAGMA page_count").fetchone()[0] == size            # nothing done
    ok = archive.compact(store.conn, min_free_share=0.01, budget=1600, rss=100)
    assert ok["after"] < ok["before"]
    store.close()
    # a store that cannot be read: a reason, never an exception
    gone = sqlite3.connect(":memory:")
    gone.close()
    assert archive.compact(gone)["skipped"]


def test_a_serverless_archive_run_keeps_its_temporary_tables_in_memory(tmp_path, frozen_hook, monkeypatch):
    store = world(tmp_path, "compact")
    detect(store)
    seen = []
    real = archive._build_records

    def watch(c, bills):
        seen.append(c.execute("PRAGMA temp_store").fetchone()[0])
        return real(c, bills)
    monkeypatch.setattr(archive, "_build_records", watch)
    before = store.conn.execute("PRAGMA temp_store").fetchone()[0]
    res = archive.run(store.conn, sink=sink_of(tmp_path, "compact"), today=TODAY, mode="serverless")
    assert res["bills"] == 9 and seen and set(seen) == {2}                      # MEMORY while it ran
    assert store.conn.execute("PRAGMA temp_store").fetchone()[0] == before      # as it was after
    store.close()
