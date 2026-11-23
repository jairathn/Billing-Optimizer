"""GUARD lens L2, trust at the door: failing tests on invented data for inputs the snapshot lets in unchecked.

Run against the frozen snapshot, never inside it:
    cd <this folder> && PYTHONDONTWRITEBYTECODE=1 SNAP=<snapshot> python3 -m pytest -q -p no:cacheprovider test_l2_door.py

Each test states what a door that checks its inputs does. Every value here is invented (tests/test_remitwatch.remit and
tests/ema_fixtures build the files): no name, id, number or date of a real patient, payer contract or practice."""
from __future__ import annotations

import gzip
import json
import os
import sys
import time

import pytest

SNAP = os.environ.get("SNAP") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "snap")
sys.path.insert(0, SNAP)
os.environ.setdefault("REMITWATCH_ALLOW_OPEN", "1")
os.environ.setdefault("REMITWATCH_MODE", "server")

from remitwatch import cloud, dropbox_sync, ema_exports  # noqa: E402
from remitwatch import ema_reports as er  # noqa: E402
from remitwatch.state import LocalState  # noqa: E402
from remitwatch.store import Store  # noqa: E402
from remitwatch.x12_835 import parse_835_bytes  # noqa: E402
from tests import ema_fixtures as F  # noqa: E402
from tests.test_remitwatch import CLAIM_DOWNCODE, remit  # noqa: E402

# CLAIM_DOWNCODE pays 77.60 on one invented claim (CB00TEST01C017, invented patient DOE JANE)
GOOD = remit(CLAIM_DOWNCODE, "77.60", seg_count=20, isa_ctrl="000000301", pay_date="20261020")


def _store(tmp_path, name="s.sqlite") -> Store:
    return Store(str(tmp_path / name))


def _route(store: Store, tmp_path, name: str, data: bytes) -> dict:
    """The router every remittance from the Dropbox folder, TriZetto and the CLI goes through."""
    d = tmp_path / f"in-{abs(hash(name)) % 10_000}"
    d.mkdir(exist_ok=True)
    p = d / name
    p.write_bytes(data)
    return dropbox_sync.ingest_new_files(store, [str(p)], log=lambda *a: None)


def _claims(store: Store) -> int:
    return store.conn.execute("""SELECT COUNT(*) FROM claims c JOIN transactions t ON t.transaction_key=c.transaction_key
                                 WHERE COALESCE(t.superseded,0)=0""").fetchone()[0]


def _named_unread(store: Store) -> int:
    return store.conn.execute("SELECT COUNT(*) FROM files WHERE status='not_read'").fetchone()[0]


# ------------------------------------------------------------------------------------------- identity: bytes, not a substring
def test_a_remittance_whose_group_control_number_is_837_is_read_as_a_remittance(tmp_path):
    """The router calls a file a claim file (837) when '*837*' appears anywhere in its first 400 bytes. A remittance whose
    GS06 group control number is 837 is then read by the 837 reader, recorded as a claim file with no claim, and its bytes
    are marked seen: the payment never reaches the store and the same file is never read again."""
    data = GOOD.replace(b"*20260904*0909*1*X*", b"*20260904*0909*837*X*")
    assert b"ST*835*" in data[:400]
    s = _store(tmp_path)
    _route(s, tmp_path, "era.RMT", data)
    kind = s.conn.execute("SELECT kind FROM files").fetchone()
    assert _claims(s) == 1, f"the remittance was filed as {kind[0] if kind else None!r} and its paid claim was never read"


def test_a_payment_of_exactly_837_dollars_is_read_as_a_remittance(tmp_path):
    """X12 writes whole amounts without decimals: BPR*I*837*C... is a payment of $837.00. The same substring test sends
    it to the claim-file reader."""
    claim = CLAIM_DOWNCODE.replace("*205.80*77.60*40.00*", "*1000.00*837*40.00*")
    data = remit(claim, "837", seg_count=20, isa_ctrl="000000302", pay_date="20261020")
    s = _store(tmp_path)
    _route(s, tmp_path, "era.RMT", data)
    assert _claims(s) == 1, "an $837.00 remittance was read as a claim file"


def test_a_remittance_saved_with_a_byte_order_mark_is_read_or_named(tmp_path):
    """A remittance opened and saved in a text editor gains a byte-order mark (or a leading blank line). The Upload page
    strips it; the Dropbox and TriZetto router asks whether the file starts with 'ISA', counts it as skipped, keeps no
    record and never offers it again. Either read it or name it where a person sees it."""
    s = _store(tmp_path)
    out = _route(s, tmp_path, "era.RMT", b"\xef\xbb\xbf" + GOOD)
    assert _claims(s) == 1 or _named_unread(s) == 1, f"skipped without a word: {out}"


def test_a_remittance_named_csv_is_read_by_what_its_bytes_are(tmp_path):
    """A remittance saved as '.csv' (or '.xlsx') by a person is sent to the export reader by its name, found to be no
    export, and set aside as 'none of the EMA exports': the payment is never read and the reason is wrong."""
    s = _store(tmp_path)
    _route(s, tmp_path, "remittance from portal.csv", GOOD)
    reason_kind = s.conn.execute("SELECT kind FROM files").fetchone()
    assert _claims(s) == 1, f"filed as {reason_kind[0] if reason_kind else None!r}, never read as a remittance"


# ------------------------------------------------------------------------------------------- what it really is: test data
def test_a_clearinghouse_test_file_is_held_not_read_as_payments(tmp_path):
    """ISA15 says whether an interchange is production data (P) or test data (T). The parser keeps it (isa_usage) and
    nothing reads it: a clearinghouse's test file becomes the practice's payments and denials."""
    data = GOOD.replace(b"*00501*000000301*0*P*:~", b"*00501*000000301*0*T*:~")
    assert b"*0*T*:~" in data
    s = _store(tmp_path)
    _route(s, tmp_path, "trizetto test.RMT", data)
    assert _claims(s) == 0, "a file marked as test data (ISA15 = T) was read as real payments"


# ------------------------------------------------------------------------------------------- complete and balanced
def test_a_remittance_that_does_not_balance_is_held(tmp_path):
    """The parser works out whether BPR02 equals the claims paid less the provider adjustments (transactions.balanced)
    and stores it; nothing reads it. A remittance whose payment and claims disagree by $900 is read as 'ok'."""
    data = remit(CLAIM_DOWNCODE, "977.60", seg_count=20, isa_ctrl="000000303", pay_date="20261020")
    s = _store(tmp_path)
    _route(s, tmp_path, "era.RMT", data)
    row = s.conn.execute("SELECT t.balanced, f.status FROM transactions t JOIN files f ON f.sha256=t.file_sha256").fetchone()
    assert row is None or row[1] != "ok", f"an unbalanced remittance (balanced={row[0]}) was read with file status 'ok'"


def test_a_segment_count_that_disagrees_is_held(tmp_path):
    """SE01 says how many segments the transaction has; a copy that lost segments in the middle (a damaged transfer)
    still ends with SE. The count is stored (segment_count) and never compared."""
    probe = remit(CLAIM_DOWNCODE, "77.60", seg_count=1, isa_ctrl="000000304", pay_date="20261020").decode()
    segs = [x for x in probe.split("~") if x]
    n = next(i for i, x in enumerate(segs) if x.startswith("SE*")) - next(i for i, x in enumerate(segs) if x.startswith("ST*")) + 1
    good = remit(CLAIM_DOWNCODE, "77.60", seg_count=n, isa_ctrl="000000304", pay_date="20261020")    # SE01 right: 28
    damaged = good.replace(b"REF*6R*1001~", b"")                # one segment lost in transfer; SE01 still says 28
    s = _store(tmp_path)
    _route(s, tmp_path, "era.RMT", damaged)
    st = s.conn.execute("SELECT segment_count FROM transactions").fetchone()
    assert st is None, f"a transaction whose SE01 says {st[0]} segments but holds {n - 1} was read as complete"


# ------------------------------------------------------------------------------------------- plausible: amounts and dates
def test_an_amount_that_cannot_be_read_is_a_problem_not_zero(tmp_path):
    """CLP04 '77.6O' (a letter O) cannot be read as money. cents() returns None without a word: the claim is stored as
    paid nothing and no problem row says why, so a paid claim looks unpaid."""
    claim = CLAIM_DOWNCODE.replace("*205.80*77.60*40.00*", "*205.80*77.6O*40.00*")
    data = remit(claim, "77.60", seg_count=20, isa_ctrl="000000305", pay_date="20261020")
    p = parse_835_bytes(data, "x.RMT")
    assert p.claims[0]["paid_cents"] is None
    assert any("CLP" in (q.get("segment") or "") or "amount" in q["message"].lower() for q in p.problems), \
        "an unreadable paid amount left no problem: the claim reads as paid nothing"


def test_an_impossible_date_is_refused(tmp_path):
    """DTM*472*20261340 is no date. iso_date() checks only that there are eight digits: '2026-13-40' is stored and compared
    as text with real dates (it sorts after every day of 2026)."""
    data = GOOD.replace(b"DTM*472*20251111~", b"DTM*472*20261340~")
    p = parse_835_bytes(data, "x.RMT")
    assert p.lines[0]["service_date"] != "2026-13-40", "an impossible service date was kept as '2026-13-40'"


def test_an_export_with_an_impossible_or_day_first_date_is_not_read_month_first(tmp_path):
    """The weekly exports' date reader (ema_exports._date) takes M/D/Y with no calendar check and no test of the file's
    date order. The A/R and Production readers check both (ema_reports.date_order, _iso_date). A CSV saved day-first
    gives '2026-25-09' and '2026-03-10' for 25 Sep and 3 Oct."""
    csv = ("Service Date,Claim ID,CPT/Product,Activity Type,Charges,Bill ID\r\n"
           "25/09/2026,ZZ00T0001C001,99213,Charge,150.00,ZZ00T0001\r\n"
           "03/10/2026,ZZ00T0002C001,99213,Charge,150.00,ZZ00T0002\r\n").encode()
    p = tmp_path / "Encounter - Charge Lines.csv"
    p.write_bytes(csv)
    days = sorted(r["service_date"] for r in ema_exports.iter_rows(str(p)))
    assert days == ["2026-09-25", "2026-10-03"], f"read as {days}"


# ------------------------------------------------------------------------------------------- whose it is: EMA reports
@pytest.fixture
def ema_store(tmp_path, monkeypatch):
    monkeypatch.setattr(er, "CLAIM_HOST_SUFFIX", F.HOST_SUFFIX)
    monkeypatch.setattr(er, "today_iso", lambda: "2026-12-31")
    for k in ("VERCEL", "REMITWATCH_EMA_REPORTS", "REMITWATCH_EMA_CLAIM_URL", "REMITWATCH_EMA_PAYER_ALIASES",
              "REMITWATCH_EMA_LEDGER", "REMITWATCH_EMA_RETAIN_DAYS"):
        monkeypatch.delenv(k, raising=False)
    s = Store(str(tmp_path / "store.sqlite"))
    er.ensure_schema(s.conn)
    yield s
    s.close()


def test_an_ar_report_from_another_ema_system_is_held_and_keeps_the_practice_s_address(ema_store, tmp_path):
    """Every claim link of an A/R report names the EMA system it came from. Two systems inside one file refuse it
    ('two_hosts'); a whole file from another practice's EMA (one system, not this practice's) is applied as this
    practice's A/R, and learn_claim_prefix replaces the practice's own EMA address with the other one."""
    s = ema_store
    rows = [F.ar_row(), F.ar_row(claim=F.claim_id(2), link=F.link_no(2), current=40, denial=40, status="Denied")]
    first = F.write(str(tmp_path / "AR 2026.xlsx"), F.ar_xlsx(rows))
    assert er.ingest_file(s, first, via="test")["status"] in ("current", "superseded")
    own = er.claim_url_template(s.conn)
    other = [dict(r, **{"Claim Link": r["Claim Link"].replace(F.TEST_HOST, "otherclinic.ema.test"),
                        "A/R Claim ID": r["A/R Claim ID"].replace("ZZ00T", "ZZ99T")}) for r in rows]
    other_path = F.write(str(tmp_path / "AR 2026 other.xlsx"), F.ar_xlsx(other))
    res = er.ingest_file(s, other_path, via="test")
    assert er.claim_url_template(s.conn) == own, "the practice's own EMA address was replaced by another practice's"
    assert res["status"] not in ("current", "superseded"), "another practice's A/R report was applied as this practice's"


# ------------------------------------------------------------------------------------------- the state folder (serverless)
def _image(tmp_path) -> bytes:
    s = Store(":memory:", compact=True)
    s.ingest_parsed(parse_835_bytes(GOOD, "era.RMT"), source_path=str(tmp_path / "era.RMT"))
    s.conn.commit()
    data = s.to_bytes()
    s.close()
    return gzip.compress(data)


def test_a_cut_short_store_image_is_not_loaded_as_the_store(tmp_path):
    """Every serverless run starts from store.sqlite.gz in the state folder (Dropbox). A copy cut short (an upload that
    stopped, a sync conflict, a restore of a partial file) is decompressed as far as it goes and opened: nothing checks
    the gzip trailer, the size or a hash written beside it. The run then saves whatever it could read over the store."""
    st = LocalState(str(tmp_path / "state"))
    whole = _image(tmp_path)
    st.put(cloud.RAW, whole[: len(whole) * 2 // 3])
    try:
        store, existed = cloud.load_raw(st)
    except Exception:
        return                                                  # refused: the door held
    n = store.conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0] if existed else None
    pytest.fail(f"a store image cut to two thirds was loaded as the store (claims readable: {n})")


def test_a_missing_store_image_is_not_taken_for_a_first_load(tmp_path):
    """When store.sqlite.gz is gone from the state folder (moved, renamed by a person, or not found for a moment) while
    the folder still holds the Dropbox position and the last run's status, load_raw hands the run a blank store as if
    this were the first load. The run carries on from the saved position and saves the blank store as the practice's."""
    st = LocalState(str(tmp_path / "state"))
    st.put(cloud.CURSOR, json.dumps({"cursor": "AAAinvented", "root": "/Trizetto Data"}).encode())
    st.put(cloud.STATUS, json.dumps({"finished": "2026-11-22T10:20:00+00:00", "ok": True}).encode())
    try:
        store, existed = cloud.load_raw(st)
    except Exception:
        return
    assert existed, "a missing store image was taken for a first load while the folder shows earlier runs"


# ------------------------------------------------------------------------------------------- the journal (serverless)
def _finding_store(tmp_path) -> Store:
    from remitwatch.detect import ensure_schema
    s = Store(str(tmp_path / "j.sqlite"))
    ensure_schema(s.conn)
    s.conn.execute("INSERT INTO findings (finding_id, status, signal, payer_group, shortfall_cents) VALUES (?,?,?,?,?)",
                   ("f" * 24, "open", "CLAIM_DENIED_UNPAID", "TEST PAYER", 5000))
    s.conn.commit()
    return s


def test_a_journal_entry_that_cannot_be_applied_is_named_not_dropped(tmp_path):
    """apply_actions treats an entry that raises as 'malformed: nothing a later run could do with it' and deletes it with
    no log line and no record. A person's status change written with a value this build does not know is lost without
    a trace (CLAUDE.md: never catch an exception without logging it)."""
    s = _finding_store(tmp_path)
    entry = ("actions/20261122T101500000000-abc123.json",
             {"kind": "status", "finding_id": "f" * 24, "status": "Sent", "actor": "biller", "at": "2026-11-22T10:15:00+00:00"})
    done, left, wrote = cloud.apply_actions(s.conn, [entry])
    assert entry[0] not in done, "a person's journal entry that could not be applied was deleted without a record"


def test_a_journal_entry_dated_in_the_future_cannot_pin_a_fax_number(tmp_path):
    """The newest save of a payer's fax number wins, by the entry's own 'at'. An entry dated in the future (a clock set
    wrong on one instance, a hand-made file in the state folder) wins over every later save, for good."""
    from remitwatch import actions
    s = _finding_store(tmp_path)
    actions.apply_payer_fax(s.conn, {"payer_group": "TEST PAYER", "number": "8005550101", "actor": "a",
                                     "at": "2099-01-01T00:00:00+00:00"})
    actions.apply_payer_fax(s.conn, {"payer_group": "TEST PAYER", "number": "8005550199", "actor": "b",
                                     "at": "2026-11-22T10:00:00+00:00"})
    num = s.conn.execute("SELECT number FROM payer_fax WHERE payer_group='TEST PAYER'").fetchone()[0]
    assert num == "8005550199", "a fax number saved 'in 2099' cannot be replaced by any real save"


# ------------------------------------------------------------------------------------------- size: what one file may cost
def _big(n_claims: int) -> bytes:
    head = GOOD.split(b"LX*1~")[0] + b"LX*1~"
    body = []
    for i in range(n_claims):
        body.append(f"CLP*ZZ{i:08d}*1*300.00*100.00*0*12*PC{i}*11~NM1*QC*1*SAMPLE*PAT****MI*M{i:08d}~")
        for k in range(3):
            body.append(f"SVC*HC:9921{k}*100.00*33.33**1~DTM*472*20261001~CAS*CO*45*60.00~CAS*PR*2*6.67~")
    return head + "".join(body).encode() + b"SE*99*000000001~GE*1*1~IEA*1*000000301~"


def test_reading_a_large_remittance_takes_time_in_proportion_to_its_size(tmp_path):
    """finish_line and finish_claim sum over every adjustment of the whole file for each line and each claim: reading
    is quadratic. On this machine 1,000 claims take about a second and 4,000 about twenty; a 5 MB remittance (a year of
    one payer's ERAs in one portal download) passes a serverless run's 300 seconds, while MAX_X12_BYTES lets in 64 MB.
    Nothing counts the runs a file stopped (repair.read_file is never called), so the same file stops every run."""
    t = time.time()
    parse_835_bytes(_big(1000), "a.RMT")
    small = time.time() - t
    t = time.time()
    parse_835_bytes(_big(3000), "b.RMT")
    large = time.time() - t
    assert large < 4.5 * max(small, 0.05), f"3x the claims took {large / max(small, 0.05):.1f}x the time ({small:.2f}s vs {large:.2f}s)"


# ------------------------------------------------------------------------------------------- settings: the practice's identity
def _npi_ok(npi: str) -> bool:
    """The NPI check digit (Luhn over 80840 + the first nine digits), as CMS defines it."""
    if not (len(npi) == 10 and npi.isdigit()):
        return False
    digits = [int(c) for c in "80840" + npi[:9]]
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 0:
            d *= 2
            d = d - 9 if d > 9 else d
        total += d
    return (10 - total % 10) % 10 == int(npi[9])


def test_a_practice_tax_id_or_npi_that_cannot_be_right_is_named_before_any_letter(tmp_path, monkeypatch):
    """Every faxed appeal letter carries PRACTICE_TIN and PRACTICE_NPI. They are checked only for being set: an eight-digit
    tax ID or an NPI whose check digit is wrong goes on every letter, and nothing compares them with the payee tax ID and
    NPIs every remittance on file names."""
    monkeypatch.setenv("REMITWATCH_DB", str(tmp_path / "api.sqlite"))
    import remitwatch.api as api
    from remitwatch import appeals
    bad_npi = next(f"177777777{d}" for d in range(10) if not _npi_ok(f"177777777{d}"))
    monkeypatch.setitem(appeals.PRACTICE, "name", "Test Dermatology")
    monkeypatch.setitem(appeals.PRACTICE, "address", "2 Main St, Anytown KY 40000")
    monkeypatch.setitem(appeals.PRACTICE, "tin", "99-999999")          # eight digits
    monkeypatch.setitem(appeals.PRACTICE, "npi", bad_npi)
    monkeypatch.setitem(appeals.PRACTICE, "phone", "(800) 555-0100")
    monkeypatch.setitem(appeals.PRACTICE, "fax", "(800) 555-0101")
    monkeypatch.setitem(appeals.PRACTICE, "email", "billing@example.org")
    missing = api._practice_missing()
    assert "PRACTICE_TIN" in missing and "PRACTICE_NPI" in missing, f"named as wrong: {missing}"


# ------------------------------------------------------------------------------------------- new or a copy: what supersedes what
def test_two_different_remittances_that_share_a_trace_number_are_both_counted(tmp_path):
    """mark_superseded keeps one transaction per (payer, trace number, payment date) and flags the rest, whatever they
    hold. Two remittances that share a trace and a payment date but pay different claims (both balanced, no claim in
    common) are not two copies of one: hiding one hides a payment. On the real store copy of 2026-11-22, 6 such groups
    hide 6 paid claims ($126.68), and in 2 more a copy with no claim that does not balance was kept over the complete,
    balanced one (6 claims, $570.16)."""
    s = _store(tmp_path)
    a = remit(CLAIM_DOWNCODE, "77.60", seg_count=28, isa_ctrl="000000311", pay_date="20261020")
    other = CLAIM_DOWNCODE.replace("CB00TEST01C017", "CB00TEST09C017").replace("PAYERCLAIM1", "PAYERCLAIM9")
    b = remit(other.replace("*205.80*77.60*40.00*", "*205.80*67.60*50.00*").replace("CAS*PR*3*40.00", "CAS*PR*3*50.00"),
              "67.60", seg_count=28, isa_ctrl="000000312", pay_date="20261020")
    b = b.replace(b"TRN*1*TRACE000000312*", b"TRN*1*TRACE000000311*")       # the payer reused the trace number
    _route(s, tmp_path, "era a.RMT", a)
    _route(s, tmp_path, "era b.RMT", b)
    live = s.conn.execute("""SELECT COUNT(*), COALESCE(SUM(c.paid_cents),0) FROM claims c JOIN transactions t
                             ON t.transaction_key=c.transaction_key WHERE t.superseded=0""").fetchone()
    live = tuple(live)
    assert live == (2, 7760 + 6760), f"live claims and paid cents: {live}: one remittance was hidden as a copy of the other"
