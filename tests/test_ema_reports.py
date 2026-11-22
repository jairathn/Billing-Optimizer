"""
EMA's A/R and Production reports (remitwatch.ema_reports): recognition by exact column names, the streaming readers, cell
parsing as EMA writes cells, keys instead of names, the A/R snapshots and their history, the catalog, ingest and preview,
held-file decisions, the switch, and the clean-up of report rows that older code read as an export.

Every row is invented (tests/ema_fixtures.py); claim links use a host under a reserved test domain.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import tracemalloc
import types

import pytest

from remitwatch import ema_exports, ema_reports as er
from remitwatch.store import Store
from tests import ema_fixtures as F

D = dt.date
SALT = b"t" * 32


TODAY = "2026-12-31"        # the day the readers take files to be read on (a posting may be dated at most the day after)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(er, "CLAIM_HOST_SUFFIX", F.HOST_SUFFIX)
    monkeypatch.setattr(er, "today_iso", lambda: TODAY)
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    for k in ("VERCEL", "REMITWATCH_EMA_REPORTS", "REMITWATCH_EMA_CLAIM_URL", "REMITWATCH_EMA_PAYER_ALIASES",
              "REMITWATCH_EMA_LEDGER", "REMITWATCH_EMA_RETAIN_DAYS"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def store(tmp_path):
    s = Store(str(tmp_path / "store.sqlite"))
    er.ensure_schema(s.conn)
    yield s
    s.close()


class Log(list):
    def __call__(self, *a):
        self.append(" ".join(str(x) for x in a))


def one(conn, sql, *args):
    """One row as a plain tuple (the store hands back sqlite3.Row objects)."""
    r = conn.execute(sql, args).fetchone()
    return tuple(r) if r is not None else None


def put(tmp_path, name, data):
    return F.write(str(tmp_path / name), data)


def prod_rows():
    return [F.production_row(),
            F.production_row(ins_paid=80, check=F.check_no(1), posted=D(2026, 4, 1)),
            F.production_row(contractual=70, reason="CO-45", posted=D(2026, 4, 1)),
            F.production_row(payer="Patient", pat_paid=20, posted=D(2026, 3, 2), link=None),
            F.production_row(cpt="Retinol Serum Test", charges=40, posted=D(2026, 3, 2), svc=D(2026, 3, 2)),
            F.production_row(units=-1.0, charges=-150, posted=D(2026, 4, 2), reason="Adjustment - Small Balance"),
            F.production_row(ins_paid=0, check="000123", posted=D(2026, 4, 3)),
            F.production_row(ins_paid=12.5, check="8.8E+08", posted=D(2026, 4, 3))]


def ar_rows():
    return [F.ar_row(),
            F.ar_row(claim=F.claim_id(2), link=F.link_no(2), current=0, denial=55.5, status="Denied", aging=D(2026, 5, 1)),
            F.ar_row(claim=F.claim_id(4), link=F.link_no(4), current=40, denial=40, status="Denied"),
            F.ar_row(party="Patient", current=-20, btype="Credit", note=F.NOTES[0], patient=1),
            F.ar_row(claim=F.claim_id(3, facility=True), link=F.link_no(3), current=200),
            F.ar_row(claim=None, link=None, status="-", current=33, coverage="Primary", patient=2),
            F.ar_row(svc=D(2025, 12, 30), claim=F.claim_id(5), link=F.link_no(5), current=10, aging=D(2026, 11, 1))]


# ------------------------------------------------------------------------------------------------------- 1. recognition
def test_reports_are_told_apart_by_their_exact_column_names(tmp_path):
    p_csv = put(tmp_path, "prod.csv", F.production_csv(prod_rows()))
    p_xlsx = put(tmp_path, "prod.xlsx", F.production_xlsx(prod_rows()))
    a_xlsx = put(tmp_path, "AR 2026.xlsx", F.ar_xlsx(ar_rows()))
    a_csv = put(tmp_path, "ar.csv", F.ar_csv(ar_rows()))
    got = {os.path.basename(p): (er.recognize(p, p).report, er.recognize(p, p).fmt) for p in (p_csv, p_xlsx, a_xlsx, a_csv)}
    assert got == {"prod.csv": ("production", "csv"), "prod.xlsx": ("production", "xlsx"),
                   "AR 2026.xlsx": ("ar_report", "xlsx"), "ar.csv": ("ar_report", "csv")}
    assert er.detect_report(F.PRODUCTION_COLUMNS) == "production"
    assert er.detect_report(F.AR_COLUMNS) == "ar_report"
    # an Encounter & Charge Lines layout with a posting-like column set is still not Production
    assert er.detect_report(F.PRODUCTION_COLUMNS + ["Activity Type"]) is None


def test_todays_weekly_exports_still_go_to_todays_reader(tmp_path):
    rows = {"charge_lines": [[dt.datetime(2026, 11, 1), dt.datetime(2026, 11, 3), F.CLINICIANS[0], "Rendering", "EMA77", "B300",
                              dt.datetime(2026, 11, 2), F.claim_id(9), 7000300, "99214", None, 1, "L98.9", 223.65, 0, 0, 0,
                              "Charge", F.PAYERS["bcbs"], "00660", "Main Office"]],
            "era_lines": [[dt.datetime(2026, 11, 10), dt.datetime(2026, 11, 1), F.claim_id(9), "99214", None, F.PAYERS["bcbs"],
                           "CO-50", "Not medically necessary", "N115", "LCD", "IGNORE", None, 0, 0, "EMA77"]],
            "claim_status": [[F.claim_id(9), "EMA77", dt.datetime(2026, 11, 1), "99214", F.PAYERS["bcbs"], "Denied",
                              dt.datetime(2026, 11, 2), dt.datetime(2026, 11, 10), 223.65, "Medical Necessity", "CO-50", "No"]]}
    for fam, rr in rows.items():
        p = put(tmp_path, f"{fam}.xlsx", F.weekly_xlsx(fam, rr))
        assert er.recognize(p, p) is None, fam
        assert er.detect_report(F.WEEKLY_COLUMNS[fam]) is None
        assert ema_exports.detect_family(F.WEEKLY_COLUMNS[fam]) == fam
        assert next(ema_exports.iter_rows(p))["family"] == fam
    assert er.recognize(put(tmp_path, "genuine.xlsx", F.genuine_status_export()), "genuine.xlsx") is None


@pytest.mark.parametrize("kw", [dict(), dict(bom=True), dict(crlf=False), dict(delimiter="\t"), dict(delimiter=";"),
                                dict(delimiter="|"), dict(title="Production report, run by a test")])
def test_text_layouts_are_recognised(tmp_path, kw):
    p = put(tmp_path, "prod.txt", F.production_csv(prod_rows(), **kw))
    rec = er.recognize(p, "prod.txt")
    assert rec and rec.report == "production" and rec.delimiter == kw.get("delimiter", ",")
    assert rec.header_index == (2 if kw.get("title") else 0)
    acct = er.Account()
    out = list(er.iter_production(p, rec, salt=SALT, account=acct))
    assert len(out) == len(prod_rows()) and acct["rows_kept"] == len(out)


def test_a_big_csv_is_recognised_from_its_first_64_kb(tmp_path):
    p = str(tmp_path / "big.csv")
    F.big_production_csv(p, 3000)
    with open(p, "rb") as fh:
        head = fh.read(er.PEEK_BYTES)
    rec = er.recognize(head, "big.csv")
    assert rec.report == "production" and rec.index["posted date"] == 0
    assert er.recognize(b"Name,Value\r\nx,1\r\n", "other.csv") is None
    assert er.recognize(b"\x00\x01\x02", "binary.csv") is None


def test_report_rows_never_reach_ema_rows(store, tmp_path):
    a = put(tmp_path, "AR 2026.xlsx", F.ar_xlsx(ar_rows()))
    p = put(tmp_path, "prod.csv", F.production_csv(prod_rows()))
    assert er.ingest_file(store, a, via="test")["status"] == "current"
    assert er.ingest_file(store, p, via="test")["status"] == "waiting"      # no ledger given: Production waits
    assert store.conn.execute("SELECT COUNT(*) FROM ema_rows").fetchone()[0] == 0
    kinds = dict(store.conn.execute("SELECT kind, COUNT(*) FROM files GROUP BY kind").fetchall())
    assert kinds == {"ema:ar": 1}


def test_columns_missing_or_named_twice_refuse_the_file(store, tmp_path):
    cols = [c for c in F.PRODUCTION_COLUMNS if c not in ("Check Number", "Patient DOB")]
    p = put(tmp_path, "prod.csv", F.production_csv(prod_rows(), columns=cols))
    rows = [{k: v for k, v in r.items() if k in cols} for r in prod_rows()]
    p = put(tmp_path, "prod.csv", F.production_csv(rows, columns=cols))
    rec = er.recognize(p, "prod.csv")
    assert rec.report == "production" and set(rec.missing) == {"check number", "patient dob"}
    res = er.ingest_file(store, p, via="test", ledger=object())
    assert res["status"] == "refused" and res["code"] == "missing_columns"
    assert "Check Number (ties each payment to its remittance)" in res["reason"] and "Patient DOB" in res["reason"]
    row = store.conn.execute("SELECT status, status_reason FROM ema_report_files").fetchone()
    assert row[0] == "refused" and "Check Number" in row[1]
    with pytest.raises(er.ReportRefused) as e:
        list(er.iter_production(p, rec, salt=SALT, account={}))
    assert e.value.code == "missing_columns"
    twice = F.PRODUCTION_COLUMNS + ["Check Number"]
    data = F.production_csv([dict(r, **{"Check Number": "1"}) for r in prod_rows()], columns=twice)
    with pytest.raises(er.ReportRefused) as e:
        er.recognize(data, "prod.csv")
    assert e.value.code == "duplicate_column" and e.value.report == "production"
    res = er.ingest_file(store, put(tmp_path, "twice.csv", data), via="test")
    assert res["status"] == "refused" and res["code"] == "duplicate_column"


# --------------------------------------------------------------------------------------------------------- 2. cells
def test_money_as_ema_writes_it():
    cases = {"$1,234.56": 123456, "($12.34)": -1234, "$45.": 4500, "-$5.00": -500, "$0.00": 0, "12": 1200, "(1,000)": -100000,
             "$12.345": 1235, "-": None, "None": None, "": None, None: None, 12.34: 1234, 7: 700, -2.5: -250}
    assert {k: er.money(k) for k in cases} == cases
    for bad in ("abc", "$1,23.00", "($5", "1.2.3", True):
        assert er.money(bad) is None


def test_dates_as_ema_writes_them():
    assert er.parse_date("3/4/2026") == "2026-03-04"
    assert er.parse_date("3/4/26") == "2026-03-04"
    assert er.parse_date("2026-03-04") == er.parse_date("2026-03-04 10:31:00") == "2026-03-04"
    assert er.parse_date(dt.datetime(2026, 3, 4, 9)) == er.parse_date(D(2026, 3, 4)) == "2026-03-04"
    assert er.parse_date(46085) == er.parse_date(46085.0) == "2026-03-04"          # an Excel serial day number, typed
    assert er.parse_date("46085") is None             # the same digits written as text are a damaged cell, not a date
    assert er.parse_date("13/4/2026", "dmy") == "2026-04-13"
    assert er.parse_date("2/30/2026") is None and er.parse_date("-") is None and er.parse_date(12) is None
    assert er.date_order(["3/4/2026", "12/31/2026"]) == "mdy"
    assert er.date_order(["13/4/2026", "4/4/2026"]) == "dmy"
    with pytest.raises(er.ReportRefused) as e:
        er.date_order(["13/4/2026", "4/13/2026"])
    assert e.value.code == "two_date_orders"


def test_codes_reasons_checks_and_links():
    assert er.code_of("99213") == ("99213", False) and er.code_of(99213) == ("99213", False)
    assert er.code_of("g2211") == ("G2211", False) and er.code_of("0001T") == ("0001T", False)
    assert er.code_of("Retinol Serum Test") == ("RETINOL SERUM TEST", True)
    assert er.code_of("-") == (None, False)
    assert er.reason_of("CO-45") == ("CO", "45", None) and er.reason_of("pr - 1") == ("PR", "1", None)
    assert er.reason_of("OA-23") == ("OA", "23", None) and er.reason_of("CO-B7") == ("CO", "B7", None)
    assert er.reason_of("Adjustment - Small Balance") == (None, None, "Small Balance")
    assert er.reason_of("Debt: Bad Debt") == (None, None, "Debt: Bad Debt") and er.reason_of("-") == (None, None, None)
    assert er.check_number("000123") == ("000123", False)
    assert er.check_number("8.8E+08") == ("8.8E+08", True) and er.check_number("1.23e+11")[1]
    assert er.check_number(123456.0) == ("123456", False) and er.check_number(1.2345e20)[1]
    assert er.check_number("-") == (None, False)
    n, prefix = er.link_number(F.claim_url(12345678))
    assert n == 12345678 and prefix == F.CLAIM_PREFIX
    assert er.link_number(F.claim_url(12345678) + "/ ") == (12345678, F.CLAIM_PREFIX)
    assert er.link_number("-") == (None, None) and er.link_number("not a link") == (None, None)


def test_production_rows_parse_as_ema_writes_them(tmp_path):
    p = put(tmp_path, "prod.csv", F.production_csv(prod_rows()))
    rec = er.recognize(p, "prod.csv")
    acct = er.Account()
    out = list(er.iter_production(p, rec, salt=SALT, account=acct))
    by = {(x.posted, x.cpt, x.check_no): x for x in out}
    paid = by[("2026-04-01", "99213", F.check_no(1))]
    assert paid.ins_paid == 8000 and paid.link == F.link_no(1) and paid.flags == 0 and paid.payer == F.PAYERS["bcbs"]
    adj = [x for x in out if x.grp == "CO"][0]
    assert (adj.code, adj.contractual, adj.kind) == ("45", 7000, None)
    pat = [x for x in out if x.flags & er.FLAG_PATIENT_SIDE][0]
    assert pat.link is None and pat.pat_paid == 2000 and pat.status is None
    prod = [x for x in out if x.flags & er.FLAG_PRODUCT][0]
    assert prod.cpt == "RETINOL SERUM TEST"
    neg = [x for x in out if x.units == -1.0][0]
    assert neg.charges == -15000 and neg.kind == "Small Balance"
    assert by[("2026-04-03", "99213", "000123")].ins_paid == 0           # a $0 answer keeps its check number and zeros
    dmg = by[("2026-04-03", "99213", "8.8E+08")]
    assert dmg.flags & er.FLAG_DAMAGED_CHECK and acct["damaged_checks"] == 1
    assert all(x.person is not None and x.name_key is not None for x in out)
    assert acct["svc_lo"] == "2026-03-02" and acct["posted_hi"] == acct["as_of"] == "2026-04-03"
    assert acct["hosts"] == 1 and acct.prefixes == [F.CLAIM_PREFIX]
    assert acct["identity_breaks"] == 0 and acct["bad_amounts"] == 0


def test_flags_counts_and_left_out_lines(tmp_path):
    rows = [F.production_row(posted=D(2026, 3, 1), svc=D(2026, 3, 2)),           # posted before the visit
            F.production_row(ins_paid=50, payments=49),                         # breaks a money identity
            F.production_row(charges="abc"),                                    # counted, never shown
            F.production_row(posted="not a date"),
            {c: None for c in F.PRODUCTION_COLUMNS},
            dict({c: "" for c in F.PRODUCTION_COLUMNS}, **{"Posted Date": "Grand Total", "Charges": 1234.0})]
    p = put(tmp_path, "prod.csv", F.production_csv(rows))
    acct = er.Account()
    out = list(er.iter_production(p, er.recognize(p, "p.csv"), salt=SALT, account=acct))
    assert len(out) == 3
    assert out[0].flags & er.FLAG_POSTED_BEFORE_SERVICE and out[1].flags & er.FLAG_IDENTITY_BREAK
    assert out[2].charges is None and acct["bad_amounts"] == 1 and acct["identity_breaks"] == 1
    assert acct["bad_dates"] == 1 and acct["rows_read"] == 6
    assert acct["left"] == {"blank": 1, "total": 1, "no_date": 1, "misaligned": 0}


def test_day_first_files_and_files_in_two_date_orders(tmp_path):
    rows = [F.production_row(posted="13/3/2026", svc="13/3/2026"), F.production_row(posted="20/4/2026", svc="13/3/2026")]
    p = put(tmp_path, "dmy.csv", F.production_csv(rows))
    acct = {}
    out = list(er.iter_production(p, er.recognize(p, "x"), salt=SALT, account=acct))
    assert acct["date_order"] == "dmy" and [x.posted for x in out] == ["2026-03-13", "2026-04-20"]
    rows = [F.production_row(posted="13/3/2026"), F.production_row(posted="3/14/2026")]
    p = put(tmp_path, "mixed.csv", F.production_csv(rows))
    with pytest.raises(er.ReportRefused) as e:
        list(er.iter_production(p, er.recognize(p, "x"), salt=SALT, account={}))
    assert e.value.code == "two_date_orders"


def test_limits_time_and_rows(tmp_path, monkeypatch):
    p = put(tmp_path, "prod.csv", F.production_csv(prod_rows()))
    rec = er.recognize(p, "p.csv")
    monkeypatch.setattr(er, "DEADLINE_EVERY", 2)
    with pytest.raises(er.ReadTimeout):
        list(er.iter_production(p, rec, salt=SALT, account={}, deadline=0.0))
    monkeypatch.setattr(er, "MAX_ROWS", 3)
    with pytest.raises(er.ReportRefused) as e:
        list(er.iter_production(p, rec, salt=SALT, account={}))
    assert e.value.code == "too_many_rows"


def test_a_conversion_error_never_carries_the_cell_text(tmp_path, monkeypatch):
    p = put(tmp_path, "prod.csv", F.production_csv(prod_rows()))
    rec = er.recognize(p, "p.csv")

    def boom(v):
        return float(F.PATIENTS[0][0])          # ValueError: could not convert string to float: '<the cell>'
    monkeypatch.setattr(er, "check_number", boom)
    with pytest.raises(er.ReportRefused) as e:
        list(er.iter_production(p, rec, salt=SALT, account={}))
    text = f"{e.value} {e.value.message} {e.value!r} {e.value.__context__!r} {e.value.__cause__!r}"
    assert e.value.code == "unreadable" and "ValueError" in str(e.value)
    assert F.PATIENTS[0][0] not in text and "Testpatient" not in text


# -------------------------------------------------------------------------------------------------- 3. formats and memory
def test_csv_xlsx_and_zip_copies_give_the_same_rows(tmp_path):
    rows = prod_rows()
    paths = [put(tmp_path, "p.csv", F.production_csv(rows)), put(tmp_path, "p.xlsx", F.production_xlsx(rows)),
             put(tmp_path, "p.zip", F.zipped({"p.csv": F.production_csv(rows), "__MACOSX/._p.csv": b"x", "readme.txt": b"hello"}))]
    got = []
    for p in paths:
        rec = er.recognize_zip(p, p)[0] if p.endswith(".zip") else er.recognize(p, p)
        got.append(list(er.iter_production(p, rec, salt=SALT, account={})))
    assert got[0] == got[1] == got[2]
    a = ar_rows()
    ap = [put(tmp_path, "a.csv", F.ar_csv(a)), put(tmp_path, "a.xlsx", F.ar_xlsx(a)), put(tmp_path, "a.zip", F.zipped("a.xlsx", F.ar_xlsx(a)))]
    got = []
    for p in ap:
        rec = er.recognize_zip(p, p)[0] if p.endswith(".zip") else er.recognize(p, p)
        got.append(list(er.iter_ar(p, rec, salt=SALT, account={})))
    assert got[0] == got[1] == got[2]


def test_zips_skip_hidden_members_and_refuse_a_zip_inside(tmp_path, store):
    inner = F.zipped("p.csv", F.production_csv(prod_rows()))
    p = put(tmp_path, "nested.zip", F.zipped({"inner.zip": inner}))
    with pytest.raises(er.ReportRefused) as e:
        er.recognize_zip(p, "nested.zip")
    assert e.value.code == "zip_in_zip"
    assert er.ingest_file(store, p, via="test")["status"] == "refused"
    assert er.recognize_zip(put(tmp_path, "w.xlsx", F.ar_xlsx(ar_rows())), "w.xlsx") == []     # a workbook is not a zip of reports
    z = put(tmp_path, "both.zip", F.zipped({"AR 2026.xlsx": F.ar_xlsx(ar_rows()), "Production.csv": F.production_csv(prod_rows()),
                                            "__MACOSX/._AR 2026.xlsx": b"junk", ".hidden.csv": b"x", "notes.txt": b"hello"}))
    res = er.ingest_file(store, z, via="test")
    assert res["status"] == "zip" and [m["status"] for m in res["members"]] == ["current", "waiting"]
    assert "notes.txt" in res["ignored"]
    member_id = er.file_id_of(F.ar_xlsx(ar_rows()))
    assert res["members"][0]["file_id"] == member_id
    loose = put(tmp_path, "AR copy.xlsx", F.ar_xlsx(ar_rows()))
    assert er.ingest_file(store, loose, via="upload")["status"] == "already"      # the same bytes by another route


def test_a_200k_row_csv_streams_in_little_memory(tmp_path):
    p = str(tmp_path / "big.csv")
    tot = F.big_production_csv(p, 200_000)
    rec = er.recognize(p, "big.csv")
    acct = er.Account()
    n = paid = charges = 0
    tracemalloc.start()
    try:
        for x in er.iter_production(p, rec, salt=SALT, account=acct):
            n += 1
            paid += x.ins_paid or 0
            charges += x.charges or 0
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert n == tot["rows"] and paid == tot["ins_paid_cents"] and charges == tot["charges_cents"]
    assert peak < 64 * 1024 * 1024, peak


# --------------------------------------------------------------------------------------------------------- 4. privacy
def _text_cells(conn: sqlite3.Connection) -> list[str]:
    out = []
    for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({t})")]
        for row in conn.execute(f"SELECT * FROM {t}"):
            out += [str(v) for v in row if isinstance(v, str)]
        out += cols
    return out


def _assert_clean(texts: list[str], allow_salt: bool = False):
    blob = "\n".join(texts)
    for s in F.SECRET_STRINGS + F.SECRET_DATES:
        assert s not in blob, s
    assert "://" not in blob.replace("https://" + F.TEST_HOST + "/ema/ws/v9/qlikRedirect/claim/", ""), "an address other than the prefix"


def test_no_name_dob_mrn_note_or_staff_reaches_the_store_or_the_logs(store, tmp_path):
    log = Log()
    a = put(tmp_path, "AR 2026.xlsx", F.ar_xlsx(ar_rows()))
    assert er.ingest_file(store, a, via="test", log=log)["status"] == "current"
    for r in er.iter_production(put(tmp_path, "p.csv", F.production_csv(prod_rows())), er.recognize(tmp_path / "p.csv", "p"),
                                salt=SALT, account={}):
        _assert_clean([str(v) for v in r])
    cells = _text_cells(store.conn)
    meta_prefix = [c for c in cells if c.startswith("https://")]
    assert meta_prefix == [F.CLAIM_PREFIX]           # the validated prefix in meta is the only address kept
    _assert_clean(cells)
    _assert_clean(log)
    initials = {r[0] for r in store.conn.execute("SELECT initials FROM ema_ar WHERE initials IS NOT NULL")}
    assert initials <= {"AT", "BS", "CE", "DF"}
    notes = store.conn.execute("SELECT has_note, note_chars, note_hash FROM ema_ar WHERE has_note=1").fetchall()
    assert len(notes) == 1 and notes[0][1] == len(F.NOTES[0]) and isinstance(notes[0][2], int)


def test_the_dashboard_copy_has_no_salt_and_no_keys(store, tmp_path, monkeypatch):
    from remitwatch import cloud, detect

    a = put(tmp_path, "AR 2026.xlsx", F.ar_xlsx(ar_rows()))
    er.ingest_file(store, a, via="test")
    detect.ensure_schema(store.conn)
    orig = detect.ensure_schema

    def both(c):
        orig(c)
        er.ensure_schema(c)
    monkeypatch.setattr(detect, "ensure_schema", both)
    tables = [t for t in cloud.SERVING_TABLES if t[0] != "meta"] + [("meta", None, er.SERVING_META_FILTER)] + er.serving_tables(store.conn)
    monkeypatch.setattr(cloud, "SERVING_TABLES", tables)
    path = str(tmp_path / "serving.sqlite")
    cloud.build_serving(store.conn, path)
    c = sqlite3.connect(path)
    assert c.execute("SELECT COUNT(*) FROM meta WHERE key='ema:key_salt'").fetchone()[0] == 0
    assert store.conn.execute("SELECT COUNT(*) FROM meta WHERE key='ema:key_salt'").fetchone()[0] == 1
    assert c.execute("SELECT COUNT(*) FROM ema_ar").fetchone()[0] == len(ar_rows())
    for col in ("name_key", "mrn_key", "note_hash"):
        assert c.execute(f"SELECT COUNT(*) FROM ema_ar WHERE {col} IS NOT NULL").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM ema_report_files WHERE origin IS NOT NULL").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM ema_ar_items WHERE last_note_hash IS NOT NULL").fetchone()[0] == 0
    _assert_clean(_text_cells(c))
    c.close()
    assert "origin" not in json.dumps(er.catalog(store.conn)["files"])


# ------------------------------------------------------------------------------------------- 5. the repository holds no tenant host
def test_no_ema_tenant_host_anywhere_in_the_repository():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        files = subprocess.run(["git", "-C", root, "ls-files", "--cached", "--others", "--exclude-standard"], capture_output=True,
                               text=True, check=True, timeout=60).stdout.split("\n")
    except (OSError, subprocess.SubprocessError):
        files = [os.path.relpath(os.path.join(dp, f), root) for dp, ds, fs in os.walk(root)
                 for f in fs if not any(x in dp for x in (".git", "__pycache__", "node_modules", os.sep + "data"))]
    host = re.compile(r"[a-z]+://([a-z0-9-]+(?:\.[a-z0-9-]+)*)\." + re.escape("ema") + r"\." + re.escape("md") + r"\b", re.I)
    vendor = {"sso"}                    # ModMed's own public sign-in service, not a practice's system
    found = []
    for rel in filter(None, files):
        p = os.path.join(root, rel)
        try:
            with open(p, "rb") as fh:
                data = fh.read(4 << 20)
        except OSError:
            continue
        if b"\x00" in data[:4096]:
            continue
        for m in host.finditer(data.decode("utf-8", "replace")):
            if m.group(1).lower() not in vendor:
                found.append(rel)
    assert not found, f"a URL under the vendor's domain names a practice's EMA system in: {sorted(set(found))}"


# ------------------------------------------------------------------------------------------------------ 6. the claim address
def test_the_claim_address_prefix(store, tmp_path, monkeypatch):
    conn = store.conn
    er.ingest_file(store, put(tmp_path, "AR 2026.xlsx", F.ar_xlsx(ar_rows())), via="test")
    assert er.claim_url_template(conn) == F.CLAIM_PREFIX + "{claim}"
    assert er.learn_claim_prefix(conn, ["https://another.example.org/ema/ws/v9/qlikRedirect/claim/"]) is None
    assert er.learn_claim_prefix(conn, ["javascript:alert(1)//claim/"]) is None
    assert er.claim_url_template(conn) == F.CLAIM_PREFIX + "{claim}"            # neither replaced the kept prefix
    with pytest.raises(er.ReportRefused) as e:
        er.learn_claim_prefix(conn, [F.CLAIM_PREFIX, "https://other.ema.test/ema/ws/v9/qlikRedirect/claim/"])
    assert e.value.code == "two_hosts"
    monkeypatch.setenv("REMITWATCH_EMA_CLAIM_URL", "https://claims.example.org/open?c={claim}")
    assert er.claim_url_template(conn) == "https://claims.example.org/open?c={claim}"
    monkeypatch.setenv("REMITWATCH_EMA_CLAIM_URL", "javascript:{claim}")
    assert er.claim_url_template(conn) == F.CLAIM_PREFIX + "{claim}"            # an unusable setting is ignored
    monkeypatch.setenv("REMITWATCH_EMA_CLAIM_URL", "off")
    assert er.claim_url_template(conn) is None
    monkeypatch.setattr(er, "CLAIM_HOST_SUFFIX", "ema.md")
    monkeypatch.delenv("REMITWATCH_EMA_CLAIM_URL")
    assert er.claim_url_template(conn) is None                                   # the kept prefix is checked again on use


def test_claim_links_from_two_systems_refuse_the_file(store, tmp_path):
    rows = ar_rows()
    rows[1]["Claim Link"] = "https://other.ema.test/ema/ws/v9/qlikRedirect/claim/12345678"
    res = er.ingest_file(store, put(tmp_path, "AR 2026.xlsx", F.ar_xlsx(rows)), via="test")
    assert res["status"] == "refused" and res["code"] == "two_hosts"
    assert store.conn.execute("SELECT COUNT(*) FROM ema_ar").fetchone()[0] == 0
    prows = prod_rows()
    prows[2]["Claim Link"] = "https://other.ema.test/ema/ws/v9/qlikRedirect/claim/12345678"
    p = put(tmp_path, "p.csv", F.production_csv(prows))
    with pytest.raises(er.ReportRefused) as e:
        list(er.iter_production(p, er.recognize(p, "p"), salt=SALT, account={}))
    assert e.value.code == "two_hosts" and "other" not in e.value.message


# --------------------------------------------------------------------------------------------------------------- 8. the A/R
def ar_file(tmp_path, name, rows, created=None):
    return put(tmp_path, name, F.ar_xlsx(rows, created=created))


def test_ar_read_exposure_bills_and_facility_claims(store, tmp_path):
    res = er.ingest_file(store, ar_file(tmp_path, "AR 2026.xlsx", ar_rows()), via="test")
    assert res["status"] == "current" and res["rows_applied"] == 7 and res["scope"] == "2025-2026"
    c = store.conn
    got = {r[0]: r[1:] for r in c.execute("SELECT claim_id, balance_cents, denial_cents, exposure_cents, bill, facility FROM ema_ar "
                                          "WHERE claim_id IS NOT NULL")}
    assert got[F.claim_id(2)] == (0, 5550, 5550, F.bill_id(2), 0)                 # denial only
    assert got[F.claim_id(4)][:3] == (4000, 4000, 4000)                            # both, equal: never added
    assert got[F.claim_id(3, facility=True)] == (20000, 0, 20000, "F" + F.bill_id(3), 1)
    assert one(c, "SELECT COUNT(*), SUM(exposure_cents) FROM ema_ar WHERE party='Insurance' AND claim_id IS NULL") == (1, 3300)
    assert one(c, "SELECT payer_group, payer_kind FROM ema_ar WHERE claim_id=?", F.claim_id(1)) == ("Anthem / BCBS", "commercial")
    assert dict(c.execute("SELECT claim_id, link FROM ema_claim_links").fetchall()) == {
        F.claim_id(i, facility=(i == 3)): F.link_no(i) for i in (1, 2, 3, 4, 5)}
    cov = er.coverage(c)["ar"]
    assert set(cov) == {"2025", "2026"} and cov["2026"]["aging_window"] == 30 and cov["2026"]["as_of"] == "2026-11-15"
    assert c.execute("SELECT value FROM meta WHERE key='ema:map_dirty'").fetchone()[0] == "1"


def test_bill_of_claim_keeps_the_facility_f():
    assert er.bill_of_claim(F.claim_id(7)) == F.bill_id(7)
    assert er.bill_of_claim(F.claim_id(7, sub=12, facility=True)) == "F" + F.bill_id(7)
    assert len(F.claim_id(7, facility=True)) == 14 and er.is_facility_claim(F.claim_id(7, facility=True))
    assert not er.is_facility_claim(F.claim_id(7)) and not er.is_facility_claim(None)
    assert er.bill_of_claim("2026-0001-77") == "2026-0001-77" and er.bill_of_claim(None) is None
    from remitwatch.detect import bill_of
    assert er.bill_of_claim(F.claim_id(7)) == bill_of(F.claim_id(7))


def test_aging_window_and_bucket_columns(store, tmp_path):
    acct = {}
    p = ar_file(tmp_path, "a.xlsx", [F.ar_row(aging=D(2026, 11, 15)), F.ar_row(claim=F.claim_id(2), aging=D(2026, 8, 1), current=0, denial=9)])
    list(er.iter_ar(p, er.recognize(p, "a"), salt=SALT, account=acct))
    assert acct["aging_window"] == 30                     # the old line carries only a denial amount
    p = ar_file(tmp_path, "b.xlsx", [F.ar_row(aging=D(2026, 11, 15)), F.ar_row(claim=F.claim_id(2), aging=D(2026, 8, 1), current=5)])
    list(er.iter_ar(p, er.recognize(p, "b"), salt=SALT, account=acct))
    assert acct["aging_window"] is None                   # an open line 106 days old: a full export
    rows = [F.ar_row(current=10, buckets={"31-60 A/R ($) (Aging)": 20, "61-90 A/R ($) (Aging)": 30}),
            F.ar_row(claim=F.claim_id(2), current=0, denial=7, buckets={"31-60 A/R ($) (Aging)": 0, "61-90 A/R ($) (Aging)": 0})]
    p = ar_file(tmp_path, "c.xlsx", rows)
    rec = er.recognize(p, "c")
    out = list(er.iter_ar(p, rec, salt=SALT, account=acct))
    assert [r["exposure_cents"] for r in out] == [6000, 700] and acct["aging_window"] is None
    assert acct["buckets"] == ["31-60 a/r ($) (aging)", "61-90 a/r ($) (aging)"]
    rows = [F.ar_row(current=10, buckets={"Total A/R ($) (Aging)": 95, "31-60 A/R ($) (Aging)": 85})]
    p = ar_file(tmp_path, "d.xlsx", rows)
    assert [r["exposure_cents"] for r in er.iter_ar(p, er.recognize(p, "d"), salt=SALT, account={})] == [9500]


def snapshot(n, as_of, *, svc=D(2026, 11, 1), first=1, payer=F.PAYERS["bcbs"], **kw):
    """n insurance lines of one service day, the newest posting (aging date) on as_of."""
    return [F.ar_row(claim=F.claim_id(i), link=F.link_no(i), svc=svc, aging=as_of, primary=payer, **kw)
            for i in range(first, first + n)]


def test_each_year_is_replaced_on_its_own_and_an_older_file_never_wins(store, tmp_path):
    c = store.conn
    both = snapshot(3, D(2026, 11, 10)) + snapshot(2, D(2026, 11, 10), svc=D(2025, 6, 1), first=50)
    ra = er.ingest_file(store, ar_file(tmp_path, "AR all.xlsx", both), via="test")
    assert ra["status"] == "current" and ra["scope"] == "2025-2026"
    rb = er.ingest_file(store, ar_file(tmp_path, "AR 2026.xlsx", snapshot(4, D(2026, 11, 17))), via="test")
    assert rb["status"] == "current" and rb["replaced"] == [(ra["file_id"], "partly_replaced")]
    assert dict(c.execute("SELECT svc_year, COUNT(*) FROM ema_ar GROUP BY 1").fetchall()) == {"2025": 2, "2026": 4}
    assert dict(c.execute("SELECT svc_year, MAX(file_id) FROM ema_ar GROUP BY 1").fetchall()) == {"2025": ra["file_id"], "2026": rb["file_id"]}
    rc = er.ingest_file(store, ar_file(tmp_path, "AR old.xlsx", snapshot(1, D(2026, 11, 12))), via="test")
    assert rc["status"] == "superseded" and rc["rows_applied"] == 0
    assert c.execute("SELECT COUNT(*) FROM ema_ar WHERE svc_year='2026'").fetchone()[0] == 4
    # the same as_of: the workbook's own creation time decides, the later pull wins
    early = ar_file(tmp_path, "AR e.xlsx", snapshot(5, D(2026, 11, 20)), created=dt.datetime(2026, 11, 20, 8))
    late = ar_file(tmp_path, "AR l.xlsx", snapshot(6, D(2026, 11, 20)), created=dt.datetime(2026, 11, 20, 9))
    assert er.ingest_file(store, late, via="test")["status"] == "current"
    assert er.ingest_file(store, early, via="test")["status"] == "superseded"
    assert c.execute("SELECT COUNT(*) FROM ema_ar WHERE svc_year='2026'").fetchone()[0] == 6
    statuses = dict(c.execute("SELECT source_name, status FROM ema_report_files").fetchall())
    assert statuses == {"AR all.xlsx": "partly_replaced", "AR 2026.xlsx": "replaced", "AR old.xlsx": "superseded",
                        "AR l.xlsx": "current", "AR e.xlsx": "superseded"}


def test_history_first_seen_absent_since_and_worked_marks(store, tmp_path):
    c = store.conn
    one = [F.ar_row(svc=D(2026, 10, 20), claim=F.claim_id(1), aging=D(2026, 11, 1), note=F.NOTES[0]),
           F.ar_row(svc=D(2026, 10, 20), claim=F.claim_id(2), link=F.link_no(2), aging=D(2026, 11, 1)),
           F.ar_row(svc=D(2026, 10, 20), claim=F.claim_id(3), link=F.link_no(3), aging=D(2026, 11, 1), status="Denied", current=0, denial=30),
           F.ar_row(svc=D(2026, 10, 20), claim=F.claim_id(5), link=F.link_no(5), aging=D(2026, 11, 1))]
    er.ingest_file(store, ar_file(tmp_path, "w1.xlsx", one), via="test")
    two = [F.ar_row(svc=D(2026, 10, 20), claim=F.claim_id(1), aging=D(2026, 11, 8), note=F.NOTES[1]),                      # note changed
           F.ar_row(svc=D(2026, 10, 20), claim=F.claim_id(3), link=F.link_no(3), aging=D(2026, 11, 8), status="Payment Pending"),   # resubmitted
           F.ar_row(svc=D(2026, 10, 20), claim=F.claim_id(4), link=F.link_no(4), aging=D(2026, 11, 8)),                    # new
           F.ar_row(svc=D(2026, 10, 20), claim=F.claim_id(5), link=F.link_no(5), aging=D(2026, 11, 8), follow=D(2026, 12, 1), action="Other")]
    er.ingest_file(store, ar_file(tmp_path, "w2.xlsx", two), via="test")
    items = {r[0].split("|")[1]: r[1:] for r in c.execute(
        "SELECT item_key, first_seen, last_seen, snapshots, absent_since, worked_on, worked_how, note_changed FROM ema_ar_items")}
    assert items[F.claim_id(1)] == ("2026-11-01", "2026-11-08", 2, None, "2026-11-08", "note", "2026-11-08")
    assert items[F.claim_id(2)][:4] == ("2026-11-01", "2026-11-01", 1, "2026-11-08")
    assert items[F.claim_id(3)][4:6] == ("2026-11-08", "resubmitted")
    assert items[F.claim_id(4)][:3] == ("2026-11-08", "2026-11-08", 1) and items[F.claim_id(4)][4] is None
    assert items[F.claim_id(5)][4:6] == ("2026-11-08", "follow_up,action")
    three = [F.ar_row(svc=D(2026, 10, 20), claim=F.claim_id(2), link=F.link_no(2), aging=D(2026, 11, 15))]
    er.ingest_file(store, ar_file(tmp_path, "w3.xlsx", three), via="test")
    back = c.execute("SELECT snapshots, absent_since, last_seen FROM ema_ar_items WHERE claim_id=?", (F.claim_id(2),)).fetchone()
    assert tuple(back) == (2, None, "2026-11-15")
    assert c.execute("SELECT absent_since FROM ema_ar_items WHERE claim_id=?", (F.claim_id(1),)).fetchone()[0] == "2026-11-15"


def test_the_shrink_hold_and_a_persons_decision(store, tmp_path):
    c = store.conn
    first = er.ingest_file(store, ar_file(tmp_path, "AR 1.xlsx", snapshot(600, D(2026, 11, 12))), via="test",
                           origin={"path": "first"})
    assert first["status"] == "current"
    shrunk = ar_file(tmp_path, "AR 2.xlsx", snapshot(100, D(2026, 11, 19)))
    held = er.ingest_file(store, shrunk, via="test", origin={"path": shrunk})
    assert held["status"] == "held" and "filtered" in held["reason"] and "16%" in held["reason"]
    assert c.execute("SELECT COUNT(*) FROM ema_ar").fetchone()[0] == 600                   # nothing applied
    assert er.ingest_file(store, shrunk, via="test")["status"] == "held"                    # still waiting for a person
    assert er.catalog(c)["held"][0]["file_id"] == held["file_id"]
    d = er.decide(c, held["file_id"], "apply", "biller")
    assert d["requeue"] == {"path": shrunk} and d["decision"] == "apply"
    assert er.ingest_file(store, shrunk, via="test")["status"] == "current"
    assert c.execute("SELECT COUNT(*) FROM ema_ar").fetchone()[0] == 100
    # an honest re-export with fewer lines a week later is fine
    ok = er.ingest_file(store, ar_file(tmp_path, "AR 3.xlsx", snapshot(80, D(2026, 11, 24))), via="test")
    assert ok["status"] == "current"


def test_a_payer_missing_from_a_reexport_holds_it(store, tmp_path):
    base = snapshot(500, D(2026, 11, 12)) + snapshot(60, D(2026, 11, 12), first=501, payer=F.PAYERS["medicare"])
    er.ingest_file(store, ar_file(tmp_path, "AR 1.xlsx", base), via="test")
    res = er.ingest_file(store, ar_file(tmp_path, "AR 2.xlsx", snapshot(500, D(2026, 11, 15))), via="test")
    assert res["status"] == "held" and F.PAYERS["medicare"] in res["reason"]
    assert er.decide(store.conn, res["file_id"], "discard", "biller")["status"] == "discarded"
    assert er.ingest_file(store, ar_file(tmp_path, "AR 2.xlsx", snapshot(500, D(2026, 11, 15))), via="test")["status"] == "already"
    # a snapshot more than ten days older is not compared
    later = er.ingest_file(store, ar_file(tmp_path, "AR 3.xlsx", snapshot(100, D(2026, 11, 30))), via="test")
    assert later["status"] == "current"


def test_decisions_only_on_held_files(store, tmp_path):
    c = store.conn
    assert "error" in er.decide(c, "nope", "apply", "u")
    r = er.ingest_file(store, ar_file(tmp_path, "AR.xlsx", ar_rows()), via="test")
    assert "error" in er.decide(c, r["file_id"], "apply", "u") and "error" in er.decide(c, r["file_id"], "maybe", "u")
    cols = [x for x in F.AR_COLUMNS if x != "Claim Status"]
    bad = er.ingest_file(store, put(tmp_path, "bad.xlsx", F.ar_xlsx(ar_rows(), columns=cols)), via="test")
    assert bad["status"] == "refused" and "error" in er.decide(c, bad["file_id"], "apply", "u")


def test_preview_says_what_a_file_holds_and_changes_nothing(store, tmp_path):
    c = store.conn
    er.ingest_file(store, ar_file(tmp_path, "AR 1.xlsx", snapshot(600, D(2026, 11, 12))), via="test")
    before = c.execute("SELECT COUNT(*) FROM ema_ar").fetchone()[0], c.execute("SELECT COUNT(*) FROM ema_report_files").fetchone()[0]
    pv = er.preview(c, ar_file(tmp_path, "AR 2.xlsx", snapshot(100, D(2026, 11, 19))))
    assert pv["report"] == "ar_report" and pv["rows"] == 100 and pv["claim_ids"] == 100 and pv["insurance_cents"] == 12_000 * 100
    assert pv["replaces"][0]["year"] == "2026" and pv["holds"] and pv["holds"][0]["code"] == "shrink"
    assert (c.execute("SELECT COUNT(*) FROM ema_ar").fetchone()[0], c.execute("SELECT COUNT(*) FROM ema_report_files").fetchone()[0]) == before
    pp = er.preview(c, put(tmp_path, "p.csv", F.production_csv(prod_rows())))
    assert pp["report"] == "production" and pp["rows"] == 8 and pp["scope"] == "service" and pp["insurers"] == 1
    assert pp["box"]["svc_lo"] == "2026-03-02" and pp["box"]["as_of"] == "2026-04-03"
    miss = er.preview(c, put(tmp_path, "m.xlsx", F.ar_xlsx(ar_rows(), columns=[x for x in F.AR_COLUMNS if x != "Primary Payer"])))
    assert miss["code"] == "missing_columns" and miss["missing"][0]["column"] == "Primary Payer"
    assert er.preview(c, put(tmp_path, "w.xlsx", F.weekly_xlsx("claim_status", [])))["report"] is None


def test_boxes_meet():
    svc = {"scope": "service", "svc_lo": "2026-01-01", "svc_hi": "2026-12-31", "as_of": "2026-11-20"}
    posted = {"scope": "posted", "posted_lo": "2026-10-01", "posted_hi": "2026-11-05"}
    old_posted = {"scope": "posted", "posted_lo": "2026-12-01", "posted_hi": "2026-12-20"}
    other_year = {"scope": "service", "svc_lo": "2025-01-01", "svc_hi": "2025-12-31", "as_of": "2026-11-20"}
    assert er._boxes_meet(svc, posted) and er._boxes_meet(posted, other_year)
    assert not er._boxes_meet(svc, old_posted) and not er._boxes_meet(svc, other_year)


# ---------------------------------------------------------------------------------------------- statuses, payers, keys
def test_the_status_dictionary():
    assert er.status_class("Payment Pending") == er.status_class("medical records sent") == "pending"
    assert er.status_class("Batched for Printing") == er.status_class("Invalid") == "not_sent"
    assert er.status_class("Ready for Posting") == "answered" and er.status_class("Resolved/Paid") == "paid"
    assert er.status_class("Resolved/Other", True) == "settled" and er.status_class("Resolved / Other", False) == "gave_up"
    assert er.status_class("Inactive/Voided") == "void" and er.status_class("-") == er.status_class(None) == "none"
    assert er.status_class("On Hold Test") == "not_sent" and er.status_class("Something New") == "unknown"


def test_unknown_status_words_are_listed(store, tmp_path):
    rows = ar_rows()
    rows[0]["Claim Status"] = "Awaiting Test Review"
    er.ingest_file(store, ar_file(tmp_path, "AR.xlsx", rows), via="test")
    assert er.catalog(store.conn)["unknown_status_words"] == ["awaiting test review"]


def test_va_pattern_aliases_and_learned_payer_ids(store, monkeypatch):
    c = store.conn
    assert er.payer_group_of(c, "VA CCN OPTUM") == ("VA Community Care", "va")
    assert er.payer_group_of(c, "WPS-VACAA") == ("VA Community Care", "va")
    assert er.payer_group_of(c, F.PAYERS["optum"]) == ("Optum (UHC)", "commercial")
    assert er.payer_group_of(c, None) == ("Unknown", "other")
    rows = [{"row_fingerprint": f"fp{i}", "family": "charge_lines", "source_sha256": "s", "payer": F.PAYERS["optum"],
             "payer_id": "VACCN" if i < 24 else "87726"} for i in range(25)]
    rows += [{"row_fingerprint": f"fq{i}", "family": "charge_lines", "source_sha256": "s", "payer": F.PAYERS["commercial"],
              "payer_id": "VACCN"} for i in range(5)]
    store.ingest_ema_rows(rows)
    er.register_functions(c, SALT)
    assert c.execute("SELECT rw_payer_group(?)", (F.PAYERS["optum"],)).fetchone()[0] == "Optum (UHC)"
    learned = er.learn_payer_aliases(c)
    assert learned == {F.PAYERS["optum"].upper(): "VA Community Care"}          # 24 of 25 rows; the other payer has 5 only
    assert er.payer_group_of(c, F.PAYERS["optum"]) == ("VA Community Care", "va")
    assert c.execute("SELECT rw_payer_group(?)", (F.PAYERS["optum"],)).fetchone()[0] == "VA Community Care"
    c.execute("DELETE FROM ema_rows")
    assert er.learn_payer_aliases(c) == learned                                  # it survives the exports being dropped
    monkeypatch.setenv("REMITWATCH_EMA_PAYER_ALIASES", json.dumps({F.PAYERS["optum"]: "Anthem / BCBS"}))
    assert er.payer_group_of(c, F.PAYERS["optum"]) == ("Anthem / BCBS", "commercial")


def test_keys_and_sql_functions(store):
    c = store.conn
    salt = er.key_salt(c)
    assert len(salt) == 32 and er.key_salt(c) == salt and len(er.salt_fingerprint(salt)) == 16
    k = er.name_key(salt, "Testpatient, Alpha B")
    assert k == er.name_key(salt, "Alpha Testpatient") == er.name_key_parts(salt, "TESTPATIENT", "ALPHA")
    assert er.name_key(salt, "Example, Charlie Jr") == er.name_key_parts(salt, "Example", "Charlie")
    assert er.name_key(salt, "Testpatient, Alpha") != er.name_key(b"x" * 32, "Testpatient, Alpha")
    assert -2 ** 63 <= k < 2 ** 63 and er.name_key(salt, None) is None and er.name_key(salt, "-") is None
    assert er.person_key(salt, "Testpatient, Alpha", "1/2/1950") == er.person_key(salt, "Alpha Testpatient", D(1950, 1, 2))
    assert er.person_key(salt, "Testpatient, Alpha", None) is None
    assert er.mrn_key(salt, "TSTMRN0001") != er.note_hash(salt, "TSTMRN0001")
    er.register_functions(c)
    assert c.execute("SELECT rw_name_key('Testpatient', 'Alpha')").fetchone()[0] == k
    assert one(c, "SELECT rw_bill(?), rw_status_class('Denied'), rw_status_class('Resolved/Other', 0)",
               F.claim_id(3, facility=True)) == ("F" + F.bill_id(3), "denied", "gave_up")
    assert one(c, "SELECT rw_check_loose('00 12-3a'), rw_shape('A12-3b'), rw_payer_kind('VA CCN Test')") == ("123A", "A99-9A", "va")


def test_the_switch(monkeypatch):
    assert er.switch() == "all" and er.reads("production") and er.reads("ar_report")
    monkeypatch.setenv("REMITWATCH_MODE", "serverless")
    assert er.switch() == "ar" and not er.reads("production") and er.reads("ar_report")
    monkeypatch.setenv("REMITWATCH_EMA_REPORTS", "off")
    assert er.switch() == "off" and not er.reads("ar_report")
    monkeypatch.setenv("REMITWATCH_EMA_REPORTS", "all")
    assert er.reads("production") and not er.reads("something")
    monkeypatch.setenv("REMITWATCH_EMA_REPORTS", "bogus")
    assert er.switch() == "ar"


def test_schema_is_safe_on_every_start_and_adds_missing_columns(tmp_path):
    c = sqlite3.connect(str(tmp_path / "old.sqlite"))
    c.execute("CREATE TABLE ema_ar_items (item_key TEXT PRIMARY KEY, gone_since TEXT)")
    c.execute("INSERT INTO ema_ar_items VALUES ('I|x|1', '2026-01-01')")
    c.commit()
    er.ensure_schema(c)
    er.ensure_schema(c)
    assert c.execute("SELECT absent_since FROM ema_ar_items").fetchone()[0] == "2026-01-01"
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert set(er.EMA_TABLES) <= tables
    lines = {r[1] for r in c.execute("PRAGMA table_info(ema_lines)")}
    assert {"facility", "first_charge_posted", "first_ins_posting", "ins_answer_rows"} <= lines and "unit_allowable" not in lines
    assert "facility" in {r[1] for r in c.execute("PRAGMA table_info(ema_link_map)")}
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO ema_line_payments (link, svc, cpt, check_no, posted, payer) VALUES (1, '2026-01-01', '99213', NULL, '2026-01-02', '')")
    compact = Store.from_bytes(Store(":memory:", compact=True).to_bytes())
    er.ensure_schema(compact.conn)
    assert set(er.EMA_TABLES) <= {r[0] for r in compact.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_file_ids():
    data = b"abc" * 1000
    assert er.file_id_of(data) == er.file_id_of(io.BytesIO(data))
    assert er.ChunkStream([b"ab", b"", b"c"]).read() == b"abc"


# ------------------------------------------------------------------------------------------------- 9. misread report rows
def _old_load(store, path, family="claim_status"):
    """Load a file as the code before the reports did: its rows through the export reader, as Claim & Claim Status (the
    family the old test gave any header with a Claim Status column) or as an unknown layout."""
    rows = list(ema_exports.iter_rows(path, {}))
    for r in rows:
        r["family"] = family
    store.ingest_ema_rows(rows)
    sha = er.file_id_of(path)
    store.record_file(sha, path, kind=f"ema:{family}", status="ok", lines=len(rows), duplicates=0)
    store.record_export(os.path.basename(path), sha, family, None, len(rows))
    return sha


def _rows_of(store, sha):
    return store.conn.execute("SELECT COUNT(*) FROM ema_rows WHERE source_sha256=?", (sha,)).fetchone()[0]


def test_migration_on_a_server_mirror(store, tmp_path):
    a = ar_file(tmp_path, "AR 2026.xlsx", ar_rows())
    sha = _old_load(store, a)
    genuine = put(tmp_path, "Claim Status.xlsx", F.genuine_status_export())
    g = _old_load(store, genuine, family=ema_exports.detect_family(F.GENUINE_STATUS_COLUMNS))
    assert _rows_of(store, sha) == 7 and _rows_of(store, g) == 23
    log = Log()
    out = er.migrate_misread(store, serverless=False, log=log)
    assert out["candidates"] == 1 and out["purged"] == 1 and out["queued"] == 1
    assert _rows_of(store, sha) == 0 and _rows_of(store, g) == 23
    assert store.conn.execute("SELECT COUNT(*) FROM files WHERE sha256=?", (sha,)).fetchone()[0] == 0
    assert store.conn.execute("SELECT COUNT(*) FROM ema_sources WHERE sha256=?", (g,)).fetchone()[0] == 1
    w = er.waiting(store.conn)
    assert [(x["file_id"], x["report"], x["origin"]["path"]) for x in w] == [(sha, "ar_report", a)]
    assert er.migrate_misread(store, serverless=False) == {"skipped": True}
    res = er.ingest_file(store, w[0]["origin"]["path"], via=w[0]["via"], origin=w[0]["origin"])
    assert res["status"] == "current" and res["rows_applied"] == 7 and not er.waiting(store.conn)
    _assert_clean(log)


def test_migration_of_an_upload_and_of_unknown_rows(store, tmp_path):
    from remitwatch import uploads

    kept = put(tmp_path, "uploads/exports/abc/AR 2026.xlsx", F.ar_xlsx(ar_rows()))
    sha = _old_load(store, kept)
    uploads.ensure_schema(store.conn)
    store.conn.execute("INSERT INTO uploads (upload_id, stored_sha, kind, name, path, status) VALUES (?,?,?,?,?,?)",
                       (sha, sha, "ema", "AR 2026.xlsx", kept, "done"))
    other = put(tmp_path, "odd.xlsx", F.weekly_xlsx("claim_status", []))           # unknown layout rows of a non-report
    junk = list(ema_exports.iter_rows(F.write(str(tmp_path / "junk.xlsx"), F.production_xlsx(prod_rows())), {}))
    for r in junk:
        r["family"] = "unknown"
    store.ingest_ema_rows(junk)
    junk_sha = junk[0]["source_sha256"]
    store.record_file(junk_sha, str(tmp_path / "gone.xlsx"), kind="ema:unknown", status="ok", lines=len(junk), duplicates=0)
    out = er.migrate_misread(store, serverless=False)
    assert out["purged"] == 1 and out["unknown_rows_deleted"] == len(junk)
    assert store.conn.execute("SELECT status FROM uploads WHERE upload_id=?", (sha,)).fetchone()[0] == "queued"
    assert er.waiting(store.conn)[0]["origin"]["upload_id"] == sha
    assert one(store.conn, "SELECT kind, status FROM files WHERE sha256=?", junk_sha) == ("ema:unknown", "not_read")
    assert other  # the weekly file itself was never loaded


def test_migration_keeps_rows_whose_original_is_gone(store, tmp_path):
    a = ar_file(tmp_path, "AR 2026.xlsx", ar_rows())
    sha = _old_load(store, a)
    os.remove(a)
    out = er.migrate_misread(store, serverless=False)
    assert out["kept"] == 1 and _rows_of(store, sha) == 7
    assert [s["sha256"] for s in er.misread_suspects(store.conn)] == [sha]
    assert er.catalog(store.conn)["misread_suspects"][0]["why"] == "original file gone"


def test_migration_serverless_queues_first_and_purges_on_confirmation(store, tmp_path):
    from remitwatch import cloud

    store.conn.executescript(cloud.CLOUD_DDL)
    a = ar_file(tmp_path, "AR 2026.xlsx", ar_rows())
    sha = _old_load(store, a)
    with open(a, "rb") as fh:
        data = fh.read()
    os.remove(a)                                   # a serverless batch file is gone after its run
    store.conn.execute("INSERT INTO dropbox_seen (id, content_hash, path, at) VALUES (?,?,?,?)",
                       ("id:abc", "h" * 64, "/Trizetto Data/AR Reports/AR 2026.xlsx", "2026-11-20"))
    genuine = put(tmp_path, "Status.xlsx", F.genuine_status_export())
    g = _old_load(store, genuine, family="claim_status")
    store.conn.execute("UPDATE ema_rows SET bill_id=NULL WHERE source_sha256=?", (g,))   # a keyless genuine export
    store.conn.execute("INSERT INTO dropbox_seen (id, content_hash, path, at) VALUES (?,?,?,?)",
                       ("id:def", "i" * 64, "/Trizetto Data/Status.xlsx", "2026-11-20"))
    store.conn.commit()
    out = er.migrate_misread(store, serverless=True)
    assert out["queued"] == 2 and _rows_of(store, sha) == 7 and _rows_of(store, g) == 23     # nothing deleted yet
    w = {x["file_id"]: x for x in er.waiting(store.conn)}
    assert w[sha]["origin"]["dropbox_id"] == "id:abc" and w[sha]["report"] == "ar_report"
    # the run fetches the entry by its Dropbox id
    fetched = put(tmp_path, "batch/AR 2026.xlsx", data)
    rec = er.recognize(fetched, "AR 2026.xlsx")
    assert er.confirm_misread(store, er.file_id_of(fetched), rec) is True
    assert _rows_of(store, sha) == 0
    assert er.ingest_file(store, fetched, via="dropbox", origin=w[sha]["origin"])["status"] == "current"
    # the genuine export is fetched, is not a report: its rows stay and its queue entry goes
    assert er.confirm_misread(store, g, er.recognize(genuine, "Status.xlsx")) is False
    assert _rows_of(store, g) == 23 and not er.waiting(store.conn)


def test_ingest_removes_misread_rows_of_the_same_bytes(store, tmp_path):
    a = ar_file(tmp_path, "AR 2026.xlsx", ar_rows())
    sha = _old_load(store, a, family="unknown")
    res = er.ingest_file(store, a, via="mirror")
    assert res["status"] == "current" and _rows_of(store, sha) == 0
    assert store.conn.execute("SELECT kind FROM files WHERE sha256=?", (sha,)).fetchone()[0] == "ema:ar"


# ---------------------------------------------------------------------------------------------- 10. Step 0: waiting reports
def test_a_report_the_switch_does_not_read_waits_and_is_read_later(store, tmp_path, monkeypatch):
    monkeypatch.setenv("REMITWATCH_EMA_REPORTS", "off")
    a = ar_file(tmp_path, "AR 2026.xlsx", ar_rows())
    res = er.ingest_file(store, a, via="dropbox", origin={"dropbox_id": "id:1", "path": "/x/AR 2026.xlsx", "size": 10})
    assert res["status"] == "waiting" and "REMITWATCH_EMA_REPORTS" in res["reason"]
    assert store.conn.execute("SELECT COUNT(*) FROM ema_ar").fetchone()[0] == 0
    assert er.ingest_file(store, a, via="dropbox")["status"] == "waiting"         # still waiting, origin kept
    w = er.waiting(store.conn)
    assert len(w) == 1 and w[0]["origin"]["dropbox_id"] == "id:1" and w[0]["format"] == "xlsx"
    monkeypatch.setenv("REMITWATCH_EMA_REPORTS", "ar")
    assert er.ingest_file(store, a, via="dropbox", origin=w[0]["origin"])["status"] == "current"
    assert not er.waiting(store.conn)
    p = put(tmp_path, "Production.csv", F.production_csv(prod_rows()))
    assert er.ingest_file(store, p, via="dropbox")["status"] == "waiting"         # the switch on 'ar' keeps Production waiting
    monkeypatch.setenv("REMITWATCH_EMA_REPORTS", "all")
    r = er.ingest_file(store, p, via="dropbox")
    assert r["status"] == "waiting" and "ledger" in r["reason"]                   # no ledger yet: Step 0 behaviour
    assert er.record_waiting(store.conn, file_id=r["file_id"], report="production", source_name="Production.csv", via="dropbox",
                             origin={"size": 99}, reason="again") is None
    assert er.supersede_waiting(store.conn, r["file_id"], reason="A zipped copy was read instead.")
    assert er.catalog(store.conn)["waiting"] == 0


@pytest.fixture
def fake_ledger(monkeypatch):
    """A stand-in for ema_ledger.apply_production_file (another module owns the ledger): it reads the file with
    iter_production and reports it applied, as the ledger's result does."""
    import remitwatch

    calls = []

    def apply_production_file(store_, path, rec, *, file_id, via, origin=None, force=False, ledger, deadline=None,
                              serverless=False, log=print):
        acct = er.Account()
        rows = list(er.iter_production(path, rec, salt=er.key_salt(store_.conn), account=acct))
        calls.append((file_id, len(rows), ledger))
        return {"status": "current", "report": "production", "file_id": file_id, "reason": None, "rows_read": acct["rows_read"],
                "rows_kept": acct["rows_kept"], "rows_applied": len(rows), "live_rows": len(rows), "scope": "service",
                "box": types.SimpleNamespace(file_id=file_id, scope="service", svc_lo=acct["svc_lo"], svc_hi=acct["svc_hi"],
                                             posted_lo=acct["posted_lo"], posted_hi=acct["posted_hi"], as_of=acct["as_of"]),
                "as_of": acct["as_of"], "replaced": [("older", "replaced")], "account": acct}
    fake = types.ModuleType("remitwatch.ema_ledger")
    fake.apply_production_file = apply_production_file
    monkeypatch.setitem(sys.modules, "remitwatch.ema_ledger", fake)
    monkeypatch.setattr(remitwatch, "ema_ledger", fake, raising=False)
    return calls


def test_production_goes_through_the_ledger_and_the_catalog_records_it(store, tmp_path, fake_ledger):
    calls = fake_ledger
    er._catalog_put(store.conn, "older", commit=True, report="production", status="current")
    p = put(tmp_path, "Production.csv", F.production_csv(prod_rows()))
    opened = []
    res = er.ingest_file(store, p, via="dropbox", ledger=lambda: opened.append(1) or "LEDGER")
    assert res["status"] == "current" and calls[0][1:] == (8, "LEDGER") and opened == [1]
    cur = store.conn.execute("SELECT * FROM ema_report_files WHERE file_id=?", (res["file_id"],))
    row = dict(zip([d[0] for d in cur.description], cur.fetchone()))
    assert (row["status"], row["scope"], row["svc_lo"], row["as_of"], row["rows_applied"]) == ("current", "service", "2026-03-02",
                                                                                               "2026-04-03", 8)
    assert "prefixes" not in row["account"] and "://" not in row["account"]
    assert one(store.conn, "SELECT status, replaced_by FROM ema_report_files WHERE file_id='older'") == ("replaced", res["file_id"])
    assert one(store.conn, "SELECT kind, lines FROM files WHERE sha256=?", res["file_id"]) == ("ema:production", 8)
    assert er.claim_url_template(store.conn) == F.CLAIM_PREFIX + "{claim}"
    assert er.coverage(store.conn)["production_as_of"] == "2026-04-03"
    assert er.ingest_file(store, p, via="upload", ledger="L")["status"] == "already" and len(calls) == 1


def test_a_raw_csv_too_large_for_this_site_waits_until_a_zipped_copy_is_read(store, tmp_path, fake_ledger):
    data = F.production_csv(prod_rows())
    head = data[:er.PEEK_BYTES]
    rec = er.recognize(head, "2026 Production.csv")
    assert rec.report == "production"
    pid = er.provisional_id("c" * 64)
    er.record_waiting(store.conn, file_id=pid, report=rec.report, source_name="2026 Production.csv", via="dropbox",
                      origin={"dropbox_id": "id:big", "content_hash": "c" * 64}, reason=er.BIG_CSV_REASON, size=len(data))
    assert er.waiting(store.conn)[0]["status_reason"].startswith("Too large to read on this site")
    z = put(tmp_path, "2026 Production.zip", F.zipped("2026 Production.csv", data))
    res = er.ingest_file(store, z, via="dropbox", ledger="L")
    assert res["members"][0]["status"] == "current"
    assert one(store.conn, "SELECT status, replaced_by FROM ema_report_files WHERE file_id=?", pid) == ("superseded",
                                                                                                    res["members"][0]["file_id"])
    assert not er.waiting(store.conn)


# ------------------------------------------------------------------------------------------- 11. what a damaged file can do
def real_ledger(tmp_path):
    from remitwatch import ema_ledger as el

    return el, el.Ledger.open(str(tmp_path / "ema_ledger.sqlite"))


def year_rows(year, n, last_posted, extra=()):
    """A service-year Production file: a charge and a payment on n claims of three insurers."""
    payers = [F.PAYERS["bcbs"], F.PAYERS["medicare"], F.PAYERS["commercial"]]
    out = []
    for i in range(n):
        svc = D(year, 1 + i % 10, 2)
        out.append(F.production_row(charges=150, posted=svc, svc=svc, link=F.link_no(year * 1000 + i), patient=i % 4,
                                    payer=payers[i % 3], primary=payers[i % 3]))
        out.append(F.production_row(ins_paid=80, check=F.check_no(i % 7), posted=min(D(svc.year, svc.month, 20), last_posted),
                                    svc=svc, link=F.link_no(year * 1000 + i), patient=i % 4, payer=payers[i % 3],
                                    primary=payers[i % 3]))
    return out + list(extra)


def test_a_date_no_export_can_hold_is_left_out_and_never_dates_the_file(tmp_path):
    """A posted date after tomorrow, a service date more than a month ahead or before 2000, and five digits written as
    text (a damaged cell, not an Excel serial) are left out and counted. The file's as_of is its newest real posting."""
    good = [F.production_row(posted=D(2026, 4, 1), svc=D(2026, 3, 2)) for _ in range(5)]
    bad = [F.production_row(posted="71234"),                                    # text: never read as the year 2095
           F.production_row(posted=D(2095, 1, 10)),                             # after tomorrow
           F.production_row(svc=D(2027, 3, 1), posted=D(2026, 12, 30)),         # a visit two months ahead
           F.production_row(svc=D(1999, 12, 31), posted=D(2026, 4, 1))]         # before any real service
    tomorrow = F.production_row(posted=D(2027, 1, 1), svc=D(2026, 12, 30))      # a posting stamped tomorrow is real
    p = put(tmp_path, "p.csv", F.production_csv(good + bad + [tomorrow]))
    acct = er.Account()
    kept = list(er.iter_production(p, er.recognize(p, "p.csv"), salt=SALT, account=acct))
    assert len(kept) == 6 and acct["left"]["no_date"] == 4 and acct["out_of_range_dates"] == 3 and acct["bad_dates"] == 4
    assert acct["posted_hi"] == "2027-01-01" and acct["as_of"] == TODAY                # never later than the day it is read
    # an .xlsx cell typed as a number is an Excel serial: 71234 is the year 2095, so it is left out too
    x = put(tmp_path, "p.xlsx", F.production_xlsx(good + [F.production_row(posted=71234)]))
    acct = er.Account()
    assert len(list(er.iter_production(x, er.recognize(x, "p.xlsx"), salt=SALT, account=acct))) == 5
    assert acct["out_of_range_dates"] == 1 and acct["as_of"] == "2026-04-01"
    # the A/R: a service date in 2062 leaves its row out; an aging date after tomorrow is a damaged cell, left blank
    rows = [F.ar_row(claim=F.claim_id(1), aging=D(2026, 11, 15)), F.ar_row(claim=F.claim_id(2), svc=D(2062, 1, 15)),
            F.ar_row(claim=F.claim_id(3), aging=D(2031, 5, 5))]
    a = put(tmp_path, "a.xlsx", F.ar_xlsx(rows))
    acct = er.Account()
    got = list(er.iter_ar(a, er.recognize(a, "a.xlsx"), salt=SALT, account=acct))
    assert [r["claim_id"] for r in got] == [F.claim_id(1), F.claim_id(3)] and got[1]["aging_date"] is None
    assert acct["left"]["no_date"] == 1 and acct["out_of_range_dates"] == 2 and acct["as_of"] == "2026-11-15"


def test_a_mistyped_ar_service_year_never_wipes_the_claim_links_or_freezes_the_ar(store, tmp_path):
    """A 2026 A/R with one service date typed as 2062: the row is left out, the snapshot covers 2026 alone, the 2025
    snapshot and its claim links stay, and next month's corrected export is read."""
    r25 = [F.ar_row(claim=F.claim_id(i), link=F.link_no(i), svc=D(2025, 6, 2), aging=D(2026, 11, 1)) for i in range(1, 301)]
    assert er.ingest_file(store, put(tmp_path, "ar2025.xlsx", F.ar_xlsx(r25)), via="test")["status"] == "current"
    r26 = [F.ar_row(claim=F.claim_id(1000 + i), link=F.link_no(1000 + i), svc=D(2026, 10, 2), aging=D(2026, 11, 15))
           for i in range(1, 301)] + [F.ar_row(claim=F.claim_id(5000), link=F.link_no(5000), svc=D(2062, 1, 15))]
    r = er.ingest_file(store, put(tmp_path, "ar2026.xlsx", F.ar_xlsx(r26)), via="test")
    assert r["status"] == "current" and r["box"]["years"] == ["2026"] and r["as_of"] == "2026-11-15"
    c = store.conn
    assert one(c, "SELECT COUNT(*) FROM ema_claim_links") == (600,)
    assert one(c, "SELECT COUNT(*) FROM ema_ar WHERE svc_year='2025'") == (300,)
    later = [F.ar_row(claim=F.claim_id(1000 + i), link=F.link_no(1000 + i), svc=D(2026, 11, 2), aging=D(2026, 12, 15))
             for i in range(1, 301)]
    r2 = er.ingest_file(store, put(tmp_path, "ar2026b.xlsx", F.ar_xlsx(later)), via="test")
    assert r2["status"] == "current" and one(c, "SELECT DISTINCT file_id FROM ema_ar WHERE svc_year='2026'") == (r2["file_id"],)
    # a file with rows in 2023 and 2026 only takes those two years; the years between keep their own snapshots
    gap = [F.ar_row(claim=F.claim_id(7000), link=F.link_no(7000), svc=D(2023, 3, 1), aging=D(2026, 12, 20)),
           F.ar_row(claim=F.claim_id(7001), link=F.link_no(7001), svc=D(2026, 12, 1), aging=D(2026, 12, 20))]
    r3 = er.ingest_file(store, put(tmp_path, "gap.xlsx", F.ar_xlsx(gap)), via="test")
    assert r3["box"]["years"] == ["2023", "2026"] and r3["scope"] == "2023, 2026"
    assert one(c, "SELECT COUNT(*) FROM ema_ar WHERE svc_year='2025'") == (300,)


def shifted_production(tmp_path, n=200, shift=(5,)):
    """A Production CSV of 2n rows in which the given data lines carry their description unquoted: the comma in it adds
    a cell and shifts every later column by one (a patient's name lands in the payer column, a staff name in the check)."""
    payers = [F.PAYERS["bcbs"], F.PAYERS["medicare"], F.PAYERS["commercial"]]
    rows = []
    for i in range(n):
        rows.append(F.production_row(charges=150, posted=D(2026, 3, 2), svc=D(2026, 3, 2), link=F.link_no(i), patient=i % 4,
                                     payer=payers[i % 3], primary=payers[i % 3]))
        rows.append(F.production_row(ins_paid=80, check=F.check_no(i % 7), posted=D(2026, 4, 1 + i % 20), svc=D(2026, 3, 2),
                                     link=F.link_no(i), patient=i % 4, payer=payers[i % 3], primary=payers[i % 3]))
    lines = F.production_csv(rows).decode().split("\r\n")
    for k in shift:
        assert '"Office visit, test"' in lines[k + 1]
        lines[k + 1] = lines[k + 1].replace('"Office visit, test"', "Office visit, test", 1)
    return put(tmp_path, f"shifted{len(shift)}.csv", "\r\n".join(lines).encode())


def test_a_shifted_csv_row_is_left_out_and_no_name_reaches_the_ledger_the_store_or_the_copy(store, tmp_path, monkeypatch):
    from remitwatch import cloud, detect

    p = shifted_production(tmp_path)
    acct = er.Account()
    kept = list(er.iter_production(p, er.recognize(p, "p.csv"), salt=SALT, account=acct))
    assert len(kept) == 399 and acct["left"]["misaligned"] == 1 and acct["rows_read"] == 400
    for r in kept:
        _assert_clean([str(v) for v in r])
    el, lg = real_ledger(tmp_path)
    res = er.ingest_file(store, p, via="test", ledger=lg, log=Log())
    assert res["status"] == "current" and res["rows_applied"] == 399            # one row in 400 is under the H1 share
    el.refresh(store, lg, log=Log())
    _assert_clean([n for (n,) in lg.conn.execute("SELECT name FROM names")])
    _assert_clean(_text_cells(store.conn))
    detect.ensure_schema(store.conn)
    orig = detect.ensure_schema
    monkeypatch.setattr(detect, "ensure_schema", lambda c: (orig(c), er.ensure_schema(c)))
    tables = ([t for t in cloud.SERVING_TABLES if t[0] != "meta"] + [("meta", None, er.SERVING_META_FILTER)]
              + er.serving_tables(store.conn))
    monkeypatch.setattr(cloud, "SERVING_TABLES", tables)
    copy = str(tmp_path / "serving.sqlite")
    cloud.build_serving(store.conn, copy)
    c = sqlite3.connect(copy)
    _assert_clean(_text_cells(c))
    c.close()
    # many shifted rows: the layout changed, and the file waits for a person
    many = shifted_production(tmp_path, shift=range(3, 60, 7))
    held = er.ingest_file(store, many, via="test", ledger=lg, log=Log())
    assert held["status"] == "held" and "more or fewer cells" in held["reason"]
    lg.close()


def test_a_file_cut_short_is_held_and_its_last_line_is_never_read(store, tmp_path):
    rows = [F.production_row(charges=150, link=F.link_no(i), patient=i % 4) for i in range(50)]
    rows.append(F.production_row(ins_paid=80, check=F.check_no(1), link=F.link_no(1), posted=D(2026, 4, 1)))
    data = F.production_csv(rows)
    last = data.rstrip(b"\r\n").rsplit(b"\r\n", 1)[1]
    cuts = {"inside a quoted description": data.rfind(b'"Office visit, test"') + 8,
            "after the check number": len(data) - len(last) + last.find(F.check_no(1).encode()) + 9 + 2}
    el, lg = real_ledger(tmp_path)
    for label, cut in cuts.items():
        p = put(tmp_path, f"cut{cut}.csv", data[:cut])
        acct = er.Account()
        got = list(er.iter_production(p, er.recognize(p, "p.csv"), salt=SALT, account=acct))
        assert acct["cut_short"] and len(got) == 50 and acct["left"]["misaligned"] == 1, label
        assert all(g.check_no is None for g in got), label                       # the cut payment row is never kept
        res = er.ingest_file(store, p, via="test", ledger=lg, log=Log())
        assert res["status"] == "held" and "cut short" in res["reason"], label
    assert one(lg.conn, "SELECT COUNT(*) FROM postings") == (0,)
    a = F.ar_csv([F.ar_row(claim=F.claim_id(i), link=F.link_no(i)) for i in range(1, 30)])
    r = er.ingest_file(store, put(tmp_path, "ar_cut.csv", a[:-30]), via="test")
    assert r["status"] == "held" and "cut short" in r["reason"] and one(store.conn, "SELECT COUNT(*) FROM ema_ar") == (0,)
    lg.close()


@pytest.mark.parametrize("fmt", ["iso_csv", "xlsx"])
def test_no_reader_holds_the_file_when_its_dates_need_no_order(tmp_path, monkeypatch, fmt):
    """Typed dates in an .xlsx and ISO dates in a CSV give no sample for the date order: the reader still holds at most
    ORDER_SAMPLES rows before the first row comes out (it held the whole file: 82 MB for 60,000 rows of an .xlsx)."""
    monkeypatch.setattr(er, "ORDER_SAMPLES", 200)
    n = 6000
    p = str(tmp_path / ("big.xlsx" if fmt == "xlsx" else "big.csv"))
    tot = F.big_production_xlsx(p, n) if fmt == "xlsx" else F.big_production_csv(p, n, iso=True)
    rec = er.recognize(p, os.path.basename(p))
    it = er.iter_production(p, rec, salt=SALT, account={})
    tracemalloc.start()
    try:
        next(it)
        held = tracemalloc.get_traced_memory()[0]
    finally:
        tracemalloc.stop()
    assert held < 3 * 1024 * 1024, held
    assert 1 + sum(1 for _ in it) == tot["rows"]


def test_an_ar_whose_dates_cannot_be_read_is_held(store, tmp_path):
    good = [F.ar_row(claim=F.claim_id(i), link=F.link_no(i), svc=D(2026, 10, 1 + i % 28), aging=D(2026, 11, 10))
            for i in range(1, 1201)]
    assert er.ingest_file(store, put(tmp_path, "ar1.csv", F.ar_csv(good)), via="test")["status"] == "current"
    bad = [dict(r) for r in good]
    for k, r in enumerate(bad):
        r["Aging Date"] = D(2026, 11, 14)
        if k % 2:
            s = r["Service Date"]
            r["Service Date"] = f"{s.year}.{s.month:02d}.{s.day:02d}"            # a format the reader does not know
    res = er.ingest_file(store, put(tmp_path, "ar2.csv", F.ar_csv(bad)), via="test")
    assert res["status"] == "held" and "layout or format changed" in res["reason"] and "600 rows" in res["reason"]
    c = store.conn
    assert one(c, "SELECT COUNT(*) FROM ema_ar") == (1200,)
    assert one(c, "SELECT COUNT(*) FROM ema_ar_items WHERE absent_since IS NOT NULL") == (0,)
    assert er.preview(c, put(tmp_path, "ar3.csv", F.ar_csv(bad)))["holds"][0]["code"] == "format"


def test_a_shifted_ar_row_is_left_out_and_its_note_never_reaches_the_copy(store, tmp_path):
    note = "Test note, spoke with Testpatient about the bill"
    rows = [F.ar_row(claim=F.claim_id(i), link=F.link_no(i)) for i in range(1, 50)]
    rows.append(F.ar_row(claim=F.claim_id(77), link=F.link_no(77), note=note))
    text = F.ar_csv(rows).decode().replace(f'"{note}"', note)
    r = er.ingest_file(store, put(tmp_path, "ar.csv", text.encode()), via="test")
    assert r["status"] == "current" and r["rows_kept"] == 49 and r["account"]["left"]["misaligned"] == 1
    assert one(store.conn, "SELECT COUNT(*) FROM ema_ar WHERE claim_id=?", F.claim_id(77)) == (0,)
    assert not [t for t in _text_cells(store.conn) if "spoke with" in t or "Testpatient" in t]


def test_a_report_never_replaces_an_exports_ema_sources_row(store, tmp_path):
    """EMA names every download alike: an A/R named like a weekly export keeps the export's row and its pull time."""
    store.record_export("Report.xlsx", "e" * 64, "claim_status", "2026-11-18T09:00:00", 40)
    os.makedirs(tmp_path / "ar")
    res = er.ingest_file(store, F.write(str(tmp_path / "ar" / "Report.xlsx"), F.ar_xlsx([F.ar_row()])), via="test")
    assert res["status"] == "current"
    c = store.conn
    assert one(c, "SELECT family, created_at FROM ema_sources WHERE sha256=?", "e" * 64) == ("claim_status", "2026-11-18T09:00:00")
    assert one(c, "SELECT source_name, family FROM ema_sources WHERE sha256=?", res["file_id"]) == ("report:Report.xlsx", "ar_report")


def test_a_crash_between_the_ledger_and_the_store_is_repaired_on_the_next_cycle(store, tmp_path):
    """The ledger committed a Production file and the process died before the store did: the next cycle finds it
    'already' applied and writes back its catalog row, its files and ema_sources rows, and the claim address."""
    el, lg = real_ledger(tmp_path)
    payers = [F.PAYERS["bcbs"], F.PAYERS["medicare"], F.PAYERS["commercial"]]
    rows = [F.production_row(charges=150, link=F.link_no(i), patient=i % 4, payer=payers[i % 3], primary=payers[i % 3])
            for i in range(60)]
    p = put(tmp_path, "prod.csv", F.production_csv(rows))
    fid = er.file_id_of(p)
    assert el.apply_production_file(store, p, er.recognize(p, "prod.csv"), file_id=fid, via="mirror", ledger=lg,
                                    log=Log())["status"] == "current"
    store.conn.rollback()                                    # the store's half never happened
    c = store.conn
    assert one(c, "SELECT COUNT(*) FROM files WHERE sha256=?", fid) == (0,)
    r = er.ingest_file(store, p, via="mirror", ledger=lg, log=Log())
    assert r["status"] == "already"
    assert one(c, "SELECT status FROM ema_report_files WHERE file_id=?", fid) == ("current",)
    assert one(c, "SELECT kind, lines FROM files WHERE sha256=?", fid) == ("ema:production", 60)
    assert one(c, "SELECT source_name FROM ema_sources WHERE sha256=?", fid) == ("report:prod.csv",)
    assert er.claim_url_template(c) == F.CLAIM_PREFIX + "{claim}"
    lg.close()
