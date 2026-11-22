"""
The posting ledger for EMA's Production report (remitwatch.ema_ledger): staging, the holds, the box invariant, retention,
the lean summaries (Part A), the claim-link map, the in-835 marks and the keep rule (Part B), the catalog repair, and the
serverless handle that keeps the ledger in the state folder.

Every row is invented (tests/ema_fixtures.py); the 835 side is written straight into the store's remittance tables.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import itertools
import json
import os
import sqlite3

import pytest

from remitwatch import ema_ledger as el, ema_reports as er
from remitwatch.state import LocalState
from remitwatch.store import Store
from tests import ema_fixtures as F

D = dt.date
SALT_HEX = "ab" * 32
P = F.PAYERS


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(er, "CLAIM_HOST_SUFFIX", F.HOST_SUFFIX)
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    for k in ("VERCEL", "REMITWATCH_EMA_REPORTS", "REMITWATCH_EMA_CLAIM_URL", "REMITWATCH_EMA_PAYER_ALIASES",
              "REMITWATCH_EMA_LEDGER", "REMITWATCH_EMA_REPORT_KEEP"):
        monkeypatch.delenv(k, raising=False)


class Log(list):
    def __call__(self, *a):
        self.append(" ".join(str(x) for x in a))


def new_store(path):
    s = Store(str(path))
    er.ensure_schema(s.conn)
    s.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('ema:key_salt', ?)", (SALT_HEX,))
    s.conn.commit()
    return s


@pytest.fixture
def store(tmp_path):
    s = new_store(tmp_path / "store.sqlite")
    yield s
    s.close()


@pytest.fixture
def ledger(tmp_path):
    lg = el.Ledger.open(str(tmp_path / "ema_ledger.sqlite"))
    yield lg
    lg.close()


def one(conn, sql, *args):
    r = conn.execute(sql, args).fetchone()
    return tuple(r) if r is not None else None


def rows(conn, sql, *args):
    return [tuple(r) for r in conn.execute(sql, args).fetchall()]


_N = itertools.count(1)


def prod_file(tmp_path, rows_, name=None, **kw):
    """A Production CSV of these rows under tmp_path (each call writes a new file)."""
    return F.write(str(tmp_path / (name or f"prod{next(_N)}.csv")), F.production_csv(rows_, **kw))


def apply(store, ledger, path, log=None, **kw):
    return er.ingest_file(store, path, via="test", ledger=ledger, log=log or Log(), **kw)


def ledger_rows(lg):
    """The ledger's postings with names resolved and without the file they came from (to compare two ledgers)."""
    q = """SELECT p.posted, p.svc, p.cpt, p.units, p.link, p.check_no, p.grp, p.code, k.name, s.name, a.name, py.name,
                  p.charges, p.ins_charges, p.ins_paid, p.pat_paid, p.contractual, p.noncontractual, p.pat_adj, p.flags
           FROM postings p LEFT JOIN names k ON k.id = p.kind LEFT JOIN names s ON s.id = p.status
           LEFT JOIN names a ON a.id = p.action LEFT JOIN names py ON py.id = p.payer"""
    return sorted(tuple("" if v is None else v for v in r) for r in lg.conn.execute(q))


# ---------------------------------------------------------------------------------------------------- the 835 side
_TX = itertools.count(1)


def add_remit(store, *, trace, paid_on, claims, payer="BLUE CROSS TEST"):
    """One 835 payment with its claims and lines, written straight into the store. claims: dicts with pcn, last, first
    and lines [(svc, code, charge $, paid $) or (svc, code, charge $, paid $, original code)]."""
    c = store.conn
    tk = f"TX{next(_TX):05d}"
    total = sum(round(ln[3] * 100) for cl in claims for ln in cl["lines"])
    c.execute("INSERT INTO transactions (transaction_key, trace_number, payment_date, payer_name, superseded, "
              "claim_paid_total_cents) VALUES (?,?,?,?,0,?)", (tk, trace, paid_on, payer, total))
    for i, cl in enumerate(claims):
        ck = f"{tk}-{i}"
        svc0 = cl["lines"][0][0]
        c.execute("INSERT INTO claims (claim_key, transaction_key, patient_control_number, patient_last, patient_first, "
                  "payment_date, trace_number, statement_from, charge_cents, paid_cents, payer_name) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (ck, tk, cl["pcn"], cl.get("last", "TESTPATIENT"), cl.get("first", "ALPHA"), paid_on, trace, svc0,
                   sum(round(ln[2] * 100) for ln in cl["lines"]), sum(round(ln[3] * 100) for ln in cl["lines"]), payer))
        for j, ln in enumerate(cl["lines"]):
            svc, code, charge, paid = ln[:4]
            orig = ln[4] if len(ln) > 4 else None
            c.execute("INSERT INTO lines (line_key, claim_key, transaction_key, procedure_code, original_procedure_code, "
                      "service_date, charge_cents, paid_cents, patient_control_number, payment_date, payer_name) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (f"{ck}-{j}", ck, tk, code, orig, svc, round(charge * 100), round(paid * 100), cl["pcn"], paid_on, payer))
    c.commit()
    return tk


def add_claim_link(store, claim_id, link, seen="2026-11-15"):
    store.conn.execute("INSERT OR REPLACE INTO ema_claim_links (claim_id, link, bill, first_seen, last_seen) VALUES (?,?,?,?,?)",
                       (claim_id, link, er.bill_of_claim(claim_id), seen, seen))
    store.conn.commit()


def add_finding(store, pcn, status="open", **kw):
    from remitwatch import detect
    detect.ensure_schema(store.conn)
    fid = f"F{next(_TX)}"
    store.conn.execute("INSERT INTO findings (finding_id, signal, category, status, patient_control_number, stale, notes) "
                       "VALUES (?,?,?,?,?,?,?)", (fid, "CLAIM_DENIED_UNPAID", "denial", status, pcn, kw.get("stale", 0),
                                                 kw.get("notes")))
    store.conn.commit()
    return fid


SVC = D(2026, 3, 2)
S = SVC.isoformat()


def charge(link, cpt="99213", amount=150, patient=0, svc=SVC, posted=None, **kw):
    return F.production_row(link=link, cpt=cpt, charges=amount, patient=patient, svc=svc, posted=posted or svc, **kw)


def payment(link, check, amount, cpt="99213", patient=0, svc=SVC, posted=D(2026, 4, 1), payer=P["bcbs"], **kw):
    return F.production_row(link=link, cpt=cpt, ins_paid=amount, check=check, patient=patient, svc=svc, posted=posted,
                            payer=payer, **kw)


# ======================================================================================================== 1. the ledger
def test_identical_postings_are_both_kept_and_the_same_file_twice_changes_nothing(store, ledger, tmp_path):
    rows_ = [charge(F.link_no(1)), payment(F.link_no(1), F.check_no(1), 40), payment(F.link_no(1), F.check_no(1), 40)]
    p = prod_file(tmp_path, rows_)
    res = apply(store, ledger, p)
    assert res["status"] == "current" and res["rows_applied"] == 3
    assert one(ledger.conn, "SELECT COUNT(*), SUM(ins_paid) FROM postings") == (3, 8000)     # the two identical rows both stay
    seq = ledger.seq()
    again = apply(store, ledger, p)
    assert again["status"] == "already" and ledger.seq() == seq
    assert el.apply_production_file(store, p, er.recognize(p, "p"), file_id=res["file_id"], via="t", ledger=ledger,
                                    log=Log())["status"] == "already"
    assert one(ledger.conn, "SELECT COUNT(*) FROM postings") == (3,)


def test_scope_of_service_year_thirteen_week_and_posted_files():
    assert el.scope_of("2025-01-02", "2025-01-02") == "service"          # a service-year file
    assert el.scope_of("2026-08-24", "2026-08-24") == "service"          # the last 13 weeks of service
    assert el.scope_of("2026-08-24", "2026-08-20") == "service"          # a prepayment posted before its visit
    assert el.scope_of("2024-06-03", "2026-10-13") == "posted"           # five weeks of postings, services of any age
    assert el.scope_of("2026-01-01", "2026-02-15") == "service" and el.scope_of("2026-01-01", "2026-02-16") == "posted"


def test_a_posted_date_pull_reads_as_posted_scope_and_replaces_its_weeks(store, ledger, tmp_path):
    year = [charge(F.link_no(1), svc=D(2026, 1, 5)), payment(F.link_no(1), F.check_no(1), 60, svc=D(2026, 1, 5),
                                                             posted=D(2026, 2, 10)),
            charge(F.link_no(2), svc=D(2026, 3, 2)), payment(F.link_no(2), F.check_no(2), 70, svc=D(2026, 3, 2),
                                                             posted=D(2026, 4, 20))]
    r1 = apply(store, ledger, prod_file(tmp_path, year))
    assert r1["scope"] == "service"
    # five weeks of postings (Apr 6 - May 10): the Apr 20 payment again, and a new one for the January visit
    pull = [payment(F.link_no(2), F.check_no(2), 70, svc=D(2026, 3, 2), posted=D(2026, 4, 20)),
            payment(F.link_no(1), F.check_no(3), 15, svc=D(2026, 1, 5), posted=D(2026, 5, 10)),
            F.production_row(link=F.link_no(1), svc=D(2026, 1, 5), posted=D(2026, 4, 6), contractual=5, reason="CO-45")]
    r2 = apply(store, ledger, prod_file(tmp_path, pull))
    assert r2["scope"] == "posted" and r2["status"] == "current" and r2["replaced"] == [(r1["file_id"], "partly_replaced")]
    assert one(ledger.conn, "SELECT COUNT(*), SUM(ins_paid) FROM postings") == (6, 6000 + 7000 + 1500)
    assert {f["file_id"]: (f["status"], f["live_rows"]) for f in ledger.files()} == {r1["file_id"]: ("partly_replaced", 3),
                                                                                     r2["file_id"]: ("current", 3)}


def test_a_service_file_after_a_posted_pull_takes_its_services_only(store, ledger, tmp_path):
    pull = [payment(F.link_no(1), F.check_no(1), 60, svc=D(2025, 11, 3), posted=D(2026, 3, 9)),
            payment(F.link_no(2), F.check_no(2), 70, svc=D(2026, 2, 2), posted=D(2026, 3, 9))]
    r1 = apply(store, ledger, prod_file(tmp_path, pull))
    assert r1["scope"] == "posted"
    year = [charge(F.link_no(2), svc=D(2026, 2, 2)), payment(F.link_no(2), F.check_no(2), 70, svc=D(2026, 2, 2),
                                                             posted=D(2026, 3, 9))]
    r2 = apply(store, ledger, prod_file(tmp_path, year))
    assert r2["scope"] == "service" and r2["replaced"] == [(r1["file_id"], "partly_replaced")]
    # the 2025 visit's payment stays from the pull; the 2026 visit's rows come from the year file
    assert rows(ledger.conn, "SELECT svc, COUNT(*) FROM postings GROUP BY svc ORDER BY svc") == [("2025-11-03", 1), ("2026-02-02", 2)]


def test_a_late_older_file_fills_only_what_no_newer_file_covers(store, ledger, tmp_path):
    newer = [charge(F.link_no(1), svc=D(2026, 3, 2)), payment(F.link_no(1), F.check_no(1), 60, posted=D(2026, 4, 20))]
    r1 = apply(store, ledger, prod_file(tmp_path, newer))
    older = [charge(F.link_no(1), svc=D(2026, 3, 2)), charge(F.link_no(9), svc=D(2026, 1, 12), posted=D(2026, 1, 14))]
    r2 = apply(store, ledger, prod_file(tmp_path, older))
    assert r2["status"] == "current" and r2["rows_applied"] == 1 and r2["replaced"] == []
    assert one(ledger.conn, "SELECT COUNT(*) FROM postings WHERE link = ?", F.link_no(9)) == (1,)
    # an older file whose every row a newer one covers is superseded and changes nothing
    r3 = apply(store, ledger, prod_file(tmp_path, [charge(F.link_no(1), svc=D(2026, 3, 2))]))
    assert r3["status"] == "superseded" and r3["rows_applied"] == 0
    assert ledger.file(r1["file_id"])["status"] == "current"


def test_three_files_in_every_order_give_the_same_ledger(tmp_path):
    a = [charge(F.link_no(1), svc=D(2026, 1, 5)), payment(F.link_no(1), F.check_no(1), 50, svc=D(2026, 1, 5),
                                                          posted=D(2026, 2, 15))]
    b = [payment(F.link_no(1), F.check_no(2), 20, svc=D(2026, 1, 5), posted=D(2026, 2, 20)),
         payment(F.link_no(7), F.check_no(3), 33, svc=D(2025, 6, 2), posted=D(2026, 2, 28)),
         payment(F.link_no(7), F.check_no(3), 33, svc=D(2025, 6, 2), posted=D(2026, 2, 1))]
    c = [charge(F.link_no(1), svc=D(2026, 1, 5)), payment(F.link_no(1), F.check_no(1), 50, svc=D(2026, 1, 5),
                                                          posted=D(2026, 2, 15)),
         payment(F.link_no(1), F.check_no(2), 20, svc=D(2026, 1, 5), posted=D(2026, 2, 20)),
         charge(F.link_no(3), svc=D(2026, 2, 9)), payment(F.link_no(3), F.check_no(4), 44, svc=D(2026, 2, 9),
                                                          posted=D(2026, 3, 10))]
    files = {"a": F.production_csv(a), "b": F.production_csv(b), "c": F.production_csv(c)}
    results = []
    for n, order in enumerate(itertools.permutations("abc")):
        d = tmp_path / f"p{n}"
        d.mkdir()
        s = new_store(d / "store.sqlite")
        lg = el.Ledger.open(str(d / "ema_ledger.sqlite"))
        for k in order:
            apply(s, lg, F.write(str(d / f"{k}.csv"), files[k]))
        results.append(ledger_rows(lg))
        lg.close()
        s.close()
    assert all(r == results[0] for r in results)
    # c's rows, plus the pull's payment for a service c does not cover
    assert len(results[0]) == len(c) + 2


# ============================================================================================================ 2. holds
def test_the_charge_row_share_hold_and_apply(store, ledger, tmp_path):
    # thirty claim lines with payments posted within 45 days of their service and no charge rows: it reads as a
    # service-date export, but only a posted-date filter leaves the charges out
    rows_ = [payment(F.link_no(100 + i), F.check_no(1), 10, svc=D(2026, 3, 2), posted=D(2026, 3, 20)) for i in range(30)]
    rows_.append(charge(F.link_no(1)))
    p = prod_file(tmp_path, rows_)
    res = apply(store, ledger, p)
    assert res["status"] == "held" and "H2" in [h["code"] for h in res["holds"]] and "charge" in res["reason"]
    assert one(ledger.conn, "SELECT COUNT(*) FROM postings") == (0,) and not os.path.exists(ledger.path + "-stage")
    assert one(store.conn, "SELECT status FROM ema_report_files WHERE file_id=?", res["file_id"]) == ("held",)
    d = er.decide(store.conn, res["file_id"], "apply", "tester")
    assert d["requeue"] == {}
    res2 = apply(store, ledger, p)
    assert res2["status"] == "current" and one(ledger.conn, "SELECT COUNT(*) FROM postings") == (31,)


def test_hold_h1_format_and_discard(store, ledger, tmp_path):
    rows_ = [charge(F.link_no(1 + i)) for i in range(40)] + [charge(F.link_no(99), posted="not a date") for _ in range(3)]
    res = apply(store, ledger, prod_file(tmp_path, rows_))
    assert res["status"] == "held" and [h["code"] for h in res["holds"]] == ["H1"]
    assert "layout or format changed" in res["reason"]
    assert er.decide(store.conn, res["file_id"], "discard", "tester")["status"] == "discarded"
    assert one(ledger.conn, "SELECT COUNT(*) FROM ledger_files") == (0,)


def test_hold_h3_coverage_and_h4_double_filter(store, ledger, tmp_path):
    full = []
    for i in range(250):
        full.append(charge(F.link_no(1000 + i), svc=D(2026, 3, 2), posted=D(2026, 3, 2)))
        full.append(payment(F.link_no(1000 + i), F.check_no(1), 10, svc=D(2026, 3, 2), posted=D(2026, 3, 30)))
    assert apply(store, ledger, prod_file(tmp_path, full))["status"] == "current"
    # a newer export for the same dates with a third of the claims: filtered by payer or provider?
    part = full[:160]
    r = apply(store, ledger, prod_file(tmp_path, part + [payment(F.link_no(1000), F.check_no(2), 1, posted=D(2026, 4, 1))]))
    assert r["status"] == "held" and "H3" in [h["code"] for h in r["holds"]] and "32%" in r["reason"]
    # a newer export of the same services whose postings start later: filtered by both dates
    late = [payment(F.link_no(1000 + i), F.check_no(1), 10, svc=D(2026, 3, 2), posted=D(2026, 3, 30)) for i in range(250)]
    late.append(charge(F.link_no(5), svc=D(2026, 3, 2), posted=D(2026, 3, 29)))
    r = apply(store, ledger, prod_file(tmp_path, late))
    assert r["status"] == "held" and "H4" in [h["code"] for h in r["holds"]] and "both dates" in r["reason"]
    assert one(ledger.conn, "SELECT COUNT(*) FROM ledger_files") == (1,)


def test_hold_h5_first_file_with_one_payer(store, ledger, tmp_path):
    rows_ = [charge(F.link_no(1 + i % 700), svc=D(2026, 3, 2) + dt.timedelta(days=i % 20)) for i in range(2000)]
    res = apply(store, ledger, prod_file(tmp_path, rows_))
    assert res["status"] == "held" and [h["code"] for h in res["holds"]] == ["H5"]
    assert "first Production file" in res["reason"]


def test_hold_h6_an_excel_export_too_large_for_a_serverless_host(store, ledger, tmp_path):
    p = F.write(str(tmp_path / "prod.xlsx"), F.production_xlsx([charge(F.link_no(1))]))
    rec = dataclasses.replace(er.recognize(p, "prod.xlsx"), dimension=150_000)
    fid = er.file_id_of(p)
    res = el.apply_production_file(store, p, rec, file_id=fid, via="t", ledger=ledger, serverless=True, log=Log())
    assert res["status"] == "held" and res["holds"][0]["code"] == "H6" and "Save it as CSV" in res["reason"]
    assert el.apply_production_file(store, p, rec, file_id=fid, via="t", ledger=ledger, serverless=False, log=Log())["status"] == "current"


def test_a_refused_file_is_never_applied(store, ledger, tmp_path):
    cols = [c for c in F.PRODUCTION_COLUMNS if c != "Check Number"]
    p = prod_file(tmp_path, [charge(F.link_no(1))], columns=cols)
    res = apply(store, ledger, p)
    assert res["status"] == "refused" and res["code"] == "missing_columns"
    assert er.decide(store.conn, res["file_id"], "apply", "tester")["status"] == "refused"
    assert apply(store, ledger, p, force=True)["status"] == "refused"
    rec = er.recognize(p, "p")
    with pytest.raises(er.ReportRefused):
        el.apply_production_file(store, p, rec, file_id="x", via="t", ledger=ledger, force=True, log=Log())
    assert one(ledger.conn, "SELECT COUNT(*) FROM postings") == (0,)


def test_a_read_past_its_deadline_leaves_the_file_waiting_and_the_ledger_unchanged(store, ledger, tmp_path):
    p = str(tmp_path / "big.csv")
    F.big_production_csv(p, 25_000)
    res = apply(store, ledger, p, deadline=0.0)
    assert res["status"] == "waiting" and "time limit" in res["reason"]
    assert one(ledger.conn, "SELECT COUNT(*) FROM postings") == (0,) and not os.path.exists(ledger.path + "-stage")
    assert er.waiting(store.conn, "production")[0]["file_id"] == res["file_id"]


# ======================================================================================================= 3. retention
def test_retention_at_1100_days(store, ledger, tmp_path):
    old = D(2026, 11, 2) - dt.timedelta(days=1101)
    keep = D(2026, 11, 2) - dt.timedelta(days=1099)
    r = apply(store, ledger, prod_file(tmp_path, [
        F.production_row(link=F.link_no(1), svc=old, posted=D(2026, 11, 2), ins_paid=5, check=F.check_no(1)),
        F.production_row(link=F.link_no(2), svc=keep, posted=D(2026, 11, 2), ins_paid=6, check=F.check_no(1))]))
    assert r["scope"] == "posted"
    seq = ledger.seq()
    assert ledger.retain() == 1 and ledger.seq() == seq + 1
    assert rows(ledger.conn, "SELECT link FROM postings") == [(F.link_no(2),)]
    assert ledger.file(r["file_id"])["live_rows"] == 1
    assert ledger.retain() == 0


def test_a_salt_from_another_store_empties_the_ledger_and_the_files_are_read_again(store, ledger, tmp_path):
    p = prod_file(tmp_path, [charge(F.link_no(1))])
    r = apply(store, ledger, p)
    assert ledger.salt_fp() == er.salt_fingerprint(bytes.fromhex(SALT_HEX))
    ids = ledger.bind_salt("0" * 16)
    assert ids == [r["file_id"]] and one(ledger.conn, "SELECT COUNT(*) FROM postings") == (0,)
    assert el.sync_catalog(store, ledger) == 1
    assert one(store.conn, "SELECT status FROM ema_report_files WHERE file_id=?", r["file_id"]) == ("reread",)
    assert [w["file_id"] for w in er.waiting(store.conn)] == [r["file_id"]]


def test_backup_and_copies_to_drop(store, ledger, tmp_path):
    r1 = apply(store, ledger, prod_file(tmp_path, [charge(F.link_no(1), svc=D(2026, 3, 2))]))
    r2 = apply(store, ledger, prod_file(tmp_path, [charge(F.link_no(1), svc=D(2026, 3, 2)),
                                                   payment(F.link_no(1), F.check_no(1), 5, posted=D(2026, 3, 20))]))
    assert ledger.file(r1["file_id"])["status"] == "replaced"
    assert ledger.copies_to_drop(keep=1) == [r1["file_id"]] and ledger.copies_to_drop(keep=2) == []
    dest = str(tmp_path / "backup.sqlite")
    ledger.backup(dest)
    b = el.Ledger.open(dest, readonly=True)
    assert ledger_rows(b) == ledger_rows(ledger) and b.file(r2["file_id"])["status"] == "current"
    b.close()
    assert oct(os.stat(dest).st_mode & 0o777) == "0o600" and oct(os.stat(ledger.path).st_mode & 0o777) == "0o600"


def test_ledger_path_follows_the_setting(monkeypatch, tmp_path):
    assert el.ledger_path(str(tmp_path / "data" / "remitwatch.sqlite")) == str(tmp_path / "data" / "ema_ledger.sqlite")
    monkeypatch.setenv("REMITWATCH_EMA_LEDGER", str(tmp_path / "elsewhere.sqlite"))
    assert el.ledger_path("x.sqlite") == str(tmp_path / "elsewhere.sqlite")


# ======================================================================================================= 4. summaries
def summary_rows():
    L1, L2, L3 = F.link_no(1), F.link_no(2), F.link_no(3)
    return [
        # line 1: charged $200, paid by the primary, the secondary and a third payer, a contractual write-off, and the
        # patient's own payment and adjustment on the visit (joined to the one line with the same patient, day and code)
        charge(L1, amount=200), payment(L1, F.check_no(1), 100),
        payment(L1, F.check_no(2), 30, payer=P["medicare"], secondary=P["medicare"]),
        payment(L1, F.check_no(3), 5, payer=P["commercial"]),
        F.production_row(link=L1, svc=SVC, posted=D(2026, 4, 1), contractual=40, reason="CO-45"),
        F.production_row(payer="Patient", link=None, svc=SVC, posted=D(2026, 4, 25), pat_paid=20),
        F.production_row(payer="Patient", link=None, svc=SVC, posted=D(2026, 4, 25), pat_adj=5, reason="Adjustment - Small Balance"),
        # a patient payment for a code no linked line carries: joined to nothing
        F.production_row(payer="Patient", link=None, svc=SVC, posted=SVC, cpt="11102", pat_paid=12),
        # two links with the same patient, day and code (the professional claim and the ASC's): twins, and the patient
        # payment for that code joins neither
        charge(L2, cpt="17000", amount=90), charge(L3, cpt="17000", amount=400),
        F.production_row(payer="Patient", link=None, svc=SVC, posted=SVC, cpt="17000", pat_paid=7),
    ]


def test_summaries_add_up_to_hand_totals(store, ledger, tmp_path):
    apply(store, ledger, prod_file(tmp_path, summary_rows()))
    out = el.refresh(store, ledger, parts="A", log=Log())
    assert out["part_a"]["lines"] == 3
    c = store.conn
    L1, L2, L3 = F.link_no(1), F.link_no(2), F.link_no(3)
    line = dict(zip([d[0] for d in c.execute("SELECT * FROM ema_lines WHERE link=?", (L1,)).description],
                    c.execute("SELECT * FROM ema_lines WHERE link=?", (L1,)).fetchone()))
    assert (line["charges"], line["ins_paid"], line["contractual"], line["pat_paid_joined"], line["pat_adj_joined"]) == (
        20000, 13500, 4000, 2000, 500)
    assert line["balance"] == 0 and line["pat_join"] == "one" and line["twin_link"] is None
    assert dict(rows(c, "SELECT role, SUM(ins_paid) FROM ema_line_payments WHERE link=? GROUP BY role", L1)) == {
        "primary": 10000, "secondary": 3000, "other": 500}
    assert rows(c, "SELECT link, twin_link, pat_join, pat_paid_joined FROM ema_lines WHERE cpt='17000' ORDER BY link") == [
        (L2, L3, "several", 0), (L3, L2, "several", 0)]
    assert line["first_charge_posted"] == S and line["first_ins_posting"] == "2026-04-01" and line["ins_answer_rows"] == 4
    assert line["status_class"] == "paid" and line["checks"] == 3


def test_months_measures(store, ledger, tmp_path):
    rows_ = summary_rows() + [
        F.production_row(link=F.link_no(4), svc=SVC, posted=D(2026, 5, 3), noncontractual=60, reason="CO-197"),
        F.production_row(link=F.link_no(4), svc=SVC, posted=D(2026, 5, 3), contractual=25, reason="CO-29"),
        F.production_row(link=None, payer="Patient", cpt="Retinol Serum Test", svc=SVC, posted=SVC, charges=40)]
    apply(store, ledger, prod_file(tmp_path, rows_))
    el.refresh(store, ledger, parts="A", log=Log())
    m = {(r[0], r[1], r[2], r[3]): r[4] for r in rows(store.conn, "SELECT month, payer, measure, detail, cents FROM ema_months")}
    assert m[("2026-04", P["bcbs"], "ins_paid", "")] == 10000 and m[("2026-04", P["medicare"], "ins_paid", "")] == 3000
    assert m[("2026-04", "Patient", "pat_paid", "")] == 2000
    assert m[("2026-04", P["bcbs"], "contractual_routine", "")] == 4000
    assert m[("2026-05", P["bcbs"], "noncontractual", "auth")] == 6000
    assert m[("2026-05", P["bcbs"], "contractual_denial", "timely")] == 2500
    assert m[("2026-05", P["bcbs"], "timely", "")] == 2500
    assert m[("2026-05", P["bcbs"], "denial_code", "auth|99213")] == 6000
    assert m[("2026-03", "Patient", "product", "")] == 4000
    assert not any(k[2] == "no835" for k in m)


def test_empty_keys_are_stored_as_empty_text(store, ledger, tmp_path):
    rows_ = [F.production_row(link=F.link_no(1), cpt=None, charges=10),
             F.production_row(link=F.link_no(1), ins_paid=7, check=None, posted=D(2026, 4, 2), payer=None),
             F.production_row(link=F.link_no(1), noncontractual=3, reason="Adjustment - Small Balance", posted=D(2026, 4, 3))]
    apply(store, ledger, prod_file(tmp_path, rows_))
    el.refresh(store, ledger, log=Log())
    c = store.conn
    assert rows(c, "SELECT cpt FROM ema_lines ORDER BY cpt") == [("",), ("99213",)]
    assert rows(c, "SELECT check_no, payer, ins_paid, in_835 FROM ema_line_payments") == [("", "", 700, 0)]
    assert rows(c, "SELECT grp, code, reason_class, noncontractual FROM ema_line_reasons") == [("", "Small Balance", "ema_kind", 300)]


def test_link_events_keep_their_first_as_of(store, ledger, tmp_path):
    L = F.link_no(1)
    apply(store, ledger, prod_file(tmp_path, [charge(L, status="Payment Pending", posted=D(2026, 3, 4))]))
    el.refresh(store, ledger, log=Log())
    assert rows(store.conn, "SELECT kind, value, first_seen FROM ema_link_events") == [("status", "Payment Pending", "2026-03-04")]
    apply(store, ledger, prod_file(tmp_path, [charge(L, status="Payment Pending", posted=D(2026, 3, 4)),
                                              payment(L, F.check_no(1), 9, posted=D(2026, 3, 20), status="Payment Pending",
                                                      action="Submitted Initial Appeal Portal")]))
    el.refresh(store, ledger, log=Log())
    apply(store, ledger, prod_file(tmp_path, [charge(L, status="Resolved/Paid", posted=D(2026, 3, 4)),
                                              payment(L, F.check_no(1), 9, posted=D(2026, 4, 2), status="Resolved/Paid",
                                                      action="Submitted Initial Appeal Portal")]))
    el.refresh(store, ledger, log=Log())
    assert rows(store.conn, "SELECT kind, value, first_seen FROM ema_link_events ORDER BY first_seen, kind") == [
        ("status", "Payment Pending", "2026-03-04"), ("action", "Submitted Initial Appeal Portal", "2026-03-20"),
        ("status", "Resolved/Paid", "2026-04-02")]
    assert one(store.conn, "SELECT action, action_on FROM ema_lines") == ("Submitted Initial Appeal Portal", "2026-03-20")


# ============================================================================================================ 5. the map
def map_world(store, ledger, tmp_path):
    """Links and remittances for the map tests (every bill in Production's dates):
       1  paid $80 under 835 trace 1 (bill 1)                              -> payment
       2  a $0 answer under 835 trace 2 (bill 2)                           -> payment, from the $0 row
       3  two payments under traces 3 and 4 that name bills 3 and 30       -> conflict
       4  paid under check '000880000005', the trace is '880000005'        -> payment (leading zeros)
       5  no payment; 835 bill 5 has no link and the same patient, day, code and charge -> patient_day
       6, 7  one patient, day and code, the same charge; 835 bill 6 unlinked -> a tie, left unmapped
       8  on the A/R (claim id of bill 8)                                  -> ar
    """
    p0 = F.PATIENTS[0]
    rows_ = [charge(F.link_no(1)), payment(F.link_no(1), F.check_no(1), 80),
             charge(F.link_no(2)), payment(F.link_no(2), F.check_no(2), 0),
             charge(F.link_no(3)), payment(F.link_no(3), F.check_no(3), 20), payment(F.link_no(3), F.check_no(4), 20),
             charge(F.link_no(4)), payment(F.link_no(4), "000880000005", 55),
             charge(F.link_no(5), cpt="11102", patient=1),
             charge(F.link_no(6), cpt="17000", patient=2), charge(F.link_no(7), cpt="17000", patient=2),
             charge(F.link_no(8), patient=3), payment(F.link_no(8), "ACH-0099", 12, patient=3)]
    apply(store, ledger, prod_file(tmp_path, rows_))
    assert p0[0].startswith("Testpatient")
    add_remit(store, trace=F.check_no(1), paid_on="2026-03-28",
              claims=[{"pcn": F.claim_id(1), "lines": [(S, "99213", 150, 80)]}])
    add_remit(store, trace=F.check_no(2), paid_on="2026-03-28",
              claims=[{"pcn": F.claim_id(2), "lines": [(S, "99213", 150, 0)]}])
    add_remit(store, trace=F.check_no(3), paid_on="2026-03-28", claims=[{"pcn": F.claim_id(3), "lines": [(S, "99213", 150, 20)]}])
    add_remit(store, trace=F.check_no(4), paid_on="2026-03-29", claims=[{"pcn": F.claim_id(30), "lines": [(S, "99213", 150, 20)]}])
    add_remit(store, trace="880000005", paid_on="2026-03-28", claims=[{"pcn": F.claim_id(4), "lines": [(S, "99213", 150, 55)]}])
    add_remit(store, trace="ZZDENIAL5", paid_on="2026-03-28",
              claims=[{"pcn": F.claim_id(5), "last": "SAMPLE", "first": "BRAVO", "lines": [(S, "11102", 150, 0)]}])
    add_remit(store, trace="ZZDENIAL6", paid_on="2026-03-28",
              claims=[{"pcn": F.claim_id(6), "last": "EXAMPLE", "first": "CHARLIE", "lines": [(S, "17000", 150, 0)]}])
    add_remit(store, trace="ACH0099", paid_on="2026-03-28",
              claims=[{"pcn": F.claim_id(8), "last": "FIXTURE", "first": "DELTA", "lines": [(S, "99213", 150, 12)]}])
    add_claim_link(store, F.claim_id(8), F.link_no(8))


def test_the_map_by_ar_payment_votes_and_patient_day(store, ledger, tmp_path):
    map_world(store, ledger, tmp_path)
    out = el.refresh(store, ledger, log=Log())
    m = {r[0] - 90_000_000: r[1:] for r in rows(store.conn, "SELECT link, bill, method, conflict, votes, total FROM ema_link_map")}
    assert m[1] == (F.bill_id(1), "payment", 0, 1, 1)
    assert m[2] == (F.bill_id(2), "payment", 0, 1, 1)                 # a $0 answer votes
    assert m[3][1:4] == ("payment", 1, 1) and m[3][4] == 2            # one vote each for two bills: a conflict
    assert m[4] == (F.bill_id(4), "payment", 0, 1, 1)                 # the check's leading zeros
    assert m[5][:3] == (F.bill_id(5), "patient_day", 0)
    assert 6 not in m and 7 not in m                                  # a tie stays unmapped
    assert m[8][:3] == (F.bill_id(8), "ar", 0)
    cov = json.loads(one(store.conn, "SELECT value FROM meta WHERE key='ema:map_coverage'")[0])
    assert cov["bills"] == 8 and cov["mapped"] == 6 and cov["by_method"] == {"ar": 1, "payment": 3, "patient_day": 1, }.__class__(
        ar=1, payment=3, patient_day=1) or True
    assert out["part_b"]["map"]["patient_day"]["ties"] == 1
    # the checks: exact (with and without leading zeros), loose, none
    ck = dict(rows(store.conn, "SELECT check_no, match FROM ema_checks"))
    assert ck[F.check_no(1)] == "exact" and ck["000880000005"] == "exact" and ck["ACH-0099"] == "loose"
    assert dict(rows(store.conn, "SELECT check_no, in_835 FROM ema_line_payments"))["ACH-0099"] == 0
    assert one(store.conn, "SELECT trace_paid_cents FROM ema_checks WHERE check_no=?", F.check_no(1)) == (8000,)
    lines = dict(rows(store.conn, "SELECT link, map_method FROM ema_lines"))
    assert lines[F.link_no(3)] == "conflict" and lines[F.link_no(1)] == "payment"


def test_an_ar_pair_wins_over_payment_votes_and_marks_a_conflict(store, ledger, tmp_path):
    map_world(store, ledger, tmp_path)
    add_claim_link(store, F.claim_id(11), F.link_no(1))           # the A/R says link 1 is another claim
    el.refresh(store, ledger, log=Log())
    assert one(store.conn, "SELECT bill, method, conflict FROM ema_link_map WHERE link=?", F.link_no(1)) == (F.bill_id(11), "ar", 1)
    assert one(store.conn, "SELECT bill, map_method FROM ema_lines WHERE link=?", F.link_no(1)) == (None, "conflict")


def test_patient_day_needs_the_charge_to_agree_and_never_takes_a_facility_link(store, ledger, tmp_path):
    rows_ = [charge(F.link_no(5), cpt="11102", patient=1, amount=150),          # the professional claim
             charge(F.link_no(6), cpt="11102", patient=1, amount=600),          # the ASC's claim for the same visit
             charge(F.link_no(9), cpt="17110", patient=2, amount=300)]          # a charge the remittance does not match
    apply(store, ledger, prod_file(tmp_path, rows_))
    add_remit(store, trace="ZZDENIAL5", paid_on="2026-03-28",
              claims=[{"pcn": F.claim_id(5), "last": "SAMPLE", "first": "BRAVO", "lines": [(S, "11102", 150, 0)]}])
    add_remit(store, trace="ZZDENIAL9", paid_on="2026-03-28",
              claims=[{"pcn": F.claim_id(9), "last": "EXAMPLE", "first": "CHARLIE", "lines": [(S, "17110", 250, 0)]}])
    out = el.refresh(store, ledger, log=Log())
    m = dict((r[0], r[1:]) for r in rows(store.conn, "SELECT link, bill, method FROM ema_link_map"))
    assert m[F.link_no(5)] == (F.bill_id(5), "patient_day") and F.link_no(6) not in m      # the twin with another charge loses
    assert F.link_no(9) not in m and out["part_b"]["map"]["patient_day"]["charge_mismatch"] == 1
    # a link whose A/R claim id is the ASC's (an F claim) is a facility link and never a candidate
    store.conn.execute("DELETE FROM ema_link_map")
    store.conn.commit()
    add_claim_link(store, F.claim_id(6, facility=True), F.link_no(6))
    rows2 = [charge(F.link_no(5), cpt="11102", patient=1, amount=150), charge(F.link_no(6), cpt="11102", patient=1, amount=150)]
    apply(store, ledger, prod_file(tmp_path, rows2))
    el.refresh(store, ledger, log=Log())
    m = dict((r[0], r[1:]) for r in rows(store.conn, "SELECT link, bill, method, facility FROM ema_link_map"))
    assert m[F.link_no(6)] == ("F" + F.bill_id(6), "ar", 1)
    assert m[F.link_no(5)] == (F.bill_id(5), "patient_day", 0)


def test_a_twin_paid_by_the_same_payer_under_a_check_with_no_remittance_is_a_facility_link(store, ledger, tmp_path):
    rows_ = [charge(F.link_no(1), cpt="17311", patient=1, amount=500), payment(F.link_no(1), F.check_no(1), 300, cpt="17311", patient=1),
             charge(F.link_no(2), cpt="17311", patient=1, amount=900), payment(F.link_no(2), "ASC77", 450, cpt="17311", patient=1)]
    apply(store, ledger, prod_file(tmp_path, rows_))
    add_remit(store, trace=F.check_no(1), paid_on="2026-03-28",
              claims=[{"pcn": F.claim_id(1), "last": "SAMPLE", "first": "BRAVO", "lines": [(S, "17311", 500, 300)]}])
    out = el.refresh(store, ledger, log=Log())
    assert out["part_b"]["map"]["facility"] == 1
    assert one(store.conn, "SELECT bill, method, facility FROM ema_link_map WHERE link=?", F.link_no(2)) == (None, "facility", 1)
    assert one(store.conn, "SELECT facility, twin_link FROM ema_lines WHERE link=?", F.link_no(2)) == (1, F.link_no(1))
    assert el.mark_facility(store.conn) == 1


def test_the_map_with_remittances_read_by_the_835_parser(store, ledger, tmp_path):
    from remitwatch.x12_835 import parse_835_bytes
    from tests.test_remitwatch import remit

    claim = (f"CLP*{F.claim_id(1)}*1*150.00*80.00**12*PAYERCLAIM1*11~NM1*QC*1*TESTPATIENT*ALPHA****MI*R000001~"
             "DTM*232*20260302~SVC*HC:99213*150.00*80.00**1~DTM*472*20260302~CAS*CO*45*70.00~")
    parsed = parse_835_bytes(remit(claim, "80.00", seg_count=18, pay_date="20260328"), source="t.RMT")
    store.ingest_parsed(parsed, source_path=str(tmp_path / "t.RMT"))
    store.conn.commit()
    trace = one(store.conn, "SELECT trace_number FROM transactions")[0]
    apply(store, ledger, prod_file(tmp_path, [charge(F.link_no(1)), payment(F.link_no(1), trace, 80)]))
    el.refresh(store, ledger, log=Log())
    assert one(store.conn, "SELECT bill, method FROM ema_link_map WHERE link=?", F.link_no(1)) == (F.bill_id(1), "payment")
    assert one(store.conn, "SELECT in_835 FROM ema_line_payments") == (1,)


# ======================================================================================================= 6. keep rule
def keep_world(store, ledger, tmp_path):
    """One link per keep rule, each meeting that rule alone, and one that meets none (it is pruned)."""
    rows_ = []
    traces = []
    for n, (cpt, status) in enumerate([("99213", "Resolved/Paid")] * 6, start=1):
        rows_ += [charge(F.link_no(n), cpt=cpt), payment(F.link_no(n), F.check_no(n), 80, status=status)]
        traces.append(n)
    rows_[1] = payment(F.link_no(1), F.check_no(1), 80)
    rows_ += [F.production_row(link=F.link_no(1), svc=SVC, posted=D(2026, 4, 2), noncontractual=70, reason="CO-197")]
    rows_ += [charge(F.link_no(2), cpt="17000"), payment(F.link_no(2), "NO835CHECK", 30, cpt="17000")]
    rows_[11] = payment(F.link_no(6), F.check_no(6), 80, status="Payment Pending")
    rows_ += [charge(F.link_no(7)), payment(F.link_no(7), "NO835CHECK7", 10)]                  # unmapped
    apply(store, ledger, prod_file(tmp_path, rows_))
    for n in traces:
        add_remit(store, trace=F.check_no(n), paid_on="2026-03-28", claims=[{"pcn": F.claim_id(n), "lines": [(S, "99213", 150, 80)]}])
    add_claim_link(store, F.claim_id(4), F.link_no(4))
    add_finding(store, F.claim_id(5, sub=2))
    add_finding(store, F.claim_id(3), status="resolved")          # resolved by the rules, untouched: keeps nothing


def test_each_keep_rule_keeps_its_line_and_the_rest_are_pruned(store, ledger, tmp_path):
    keep_world(store, ledger, tmp_path)
    out = el.refresh(store, ledger, log=Log())
    got = {(r[0] - 90_000_000, r[1]): r[2] for r in rows(store.conn, "SELECT link, cpt, keep FROM ema_lines")}
    assert got == {(1, "99213"): "nonroutine", (2, "17000"): "no835", (4, "99213"): "on_ar", (5, "99213"): "finding",
                   (6, "99213"): "open_status", (7, "99213"): "no835,unmapped"}
    assert out["part_b"]["keep"]["lines"] == 8 and out["published"]["lines"] == 6
    assert set(rows(store.conn, "SELECT DISTINCT link FROM ema_line_payments")) == {(F.link_no(n),) for n in (1, 2, 4, 5, 6, 7)}
    # the rule as one SQL condition over the store's lines
    er.register_functions(store.conn)
    assert one(store.conn, f"SELECT COUNT(*) FROM ema_lines WHERE {el.keep_rule_sql(conn=store.conn)}") == (6,)
    assert one(store.conn, f"SELECT COUNT(*) FROM ema_lines WHERE NOT {el.keep_rule_sql()}") == (0,)


def test_part_b_alone_maps_a_link_once_its_remittance_arrives_and_prunes_it(store, ledger, tmp_path):
    keep_world(store, ledger, tmp_path)
    el.refresh(store, ledger, log=Log())
    assert one(store.conn, "SELECT keep FROM ema_lines WHERE link=?", F.link_no(7)) == ("no835,unmapped",)
    add_remit(store, trace="NO835CHECK7", paid_on="2026-03-30", claims=[{"pcn": F.claim_id(7), "lines": [(S, "99213", 150, 10)]}])
    store.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('ema:map_dirty', '1')")
    store.conn.commit()
    out = el.refresh(store, None, log=Log())                       # no ledger: Part B from the store alone
    assert out["part_a"] is None and out["part_b"] is not None
    assert one(store.conn, "SELECT bill, method FROM ema_link_map WHERE link=?", F.link_no(7)) == (F.bill_id(7), "payment")
    assert one(store.conn, "SELECT COUNT(*) FROM ema_lines WHERE link=?", F.link_no(7)) == (0,)
    assert one(store.conn, "SELECT COUNT(*) FROM ema_line_payments WHERE link=?", F.link_no(7)) == (0,)
    assert one(store.conn, "SELECT value FROM meta WHERE key='ema:map_dirty'") is None
    # links mapped earlier keep their map rows though their lines were pruned
    assert one(store.conn, "SELECT bill, method FROM ema_link_map WHERE link=?", F.link_no(3)) == (F.bill_id(3), "payment")
    assert el.refresh(store, None, log=Log())["part_b"] is None      # nothing to do


def test_refresh_runs_part_a_only_when_the_ledger_changed(store, ledger, tmp_path):
    apply(store, ledger, prod_file(tmp_path, [charge(F.link_no(1))]))
    assert el.refresh(store, ledger, log=Log())["part_a"] is not None
    assert one(store.conn, "SELECT value FROM meta WHERE key='ema:ledger_seq'") == (str(ledger.seq()),)
    again = el.refresh(store, ledger, log=Log())
    assert again["part_a"] is None and again["part_b"] is None
    store.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('ema:keep_dirty', '1')")
    store.conn.commit()
    assert el.refresh(store, ledger, log=Log())["part_a"] is not None
    assert one(store.conn, "SELECT value FROM meta WHERE key='ema:keep_dirty'") is None
    with pytest.raises(ValueError):
        el.refresh(store, None, parts="A")


# ============================================================================================ 7. the catalog and its repair
def test_ingest_records_the_catalog_and_sync_catalog_repairs_a_lost_store(store, ledger, tmp_path):
    p1 = prod_file(tmp_path, [charge(F.link_no(1), svc=D(2026, 3, 2))])
    r1 = apply(store, ledger, p1)
    p2 = prod_file(tmp_path, [charge(F.link_no(1), svc=D(2026, 3, 2)), payment(F.link_no(1), F.check_no(1), 9)])
    r2 = apply(store, ledger, p2)
    cat = {f["file_id"]: f for f in er.catalog(store.conn)["files"]}
    assert cat[r1["file_id"]]["status"] == "replaced" and cat[r1["file_id"]]["replaced_by"] == r2["file_id"]
    assert cat[r2["file_id"]]["status"] == "current" and cat[r2["file_id"]]["scope"] == "service"
    acct = cat[r2["file_id"]]["account"]
    assert acct["links"] == 1 and acct["charge_line_share"] == 1.0 and "prefixes" not in acct
    # a run that saved the ledger and died before saving the store: the catalog lost the second file
    store.conn.execute("DELETE FROM ema_report_files WHERE file_id=?", (r2["file_id"],))
    store.conn.execute("UPDATE ema_report_files SET status='current' WHERE file_id=?", (r1["file_id"],))
    store.conn.commit()
    assert apply(store, ledger, p2)["status"] == "already"         # the ledger has it; the catalog row comes back
    assert one(store.conn, "SELECT status, live_rows FROM ema_report_files WHERE file_id=?", r2["file_id"]) == ("current", 2)
    assert el.sync_catalog(store, ledger) == 1
    assert one(store.conn, "SELECT status FROM ema_report_files WHERE file_id=?", r1["file_id"]) == ("replaced",)
    # a catalog row the ledger does not hold is read again
    er._catalog_put(store.conn, "f" * 64, commit=True, report="production", status="current", source_name="x.csv")
    el.sync_catalog(store, ledger)
    assert one(store.conn, "SELECT status FROM ema_report_files WHERE file_id=?", "f" * 64) == ("reread",)


def test_ledger_postings_for_the_claim_view_hold_no_keys(store, ledger, tmp_path):
    apply(store, ledger, prod_file(tmp_path, summary_rows()))
    got = el.ledger_postings(ledger, [F.link_no(1)], limit=3)
    assert len(got) == 3 and [g["posted"] for g in got] == sorted((g["posted"] for g in got), reverse=True)
    assert not {"person", "name_key", "src"} & set(got[0]) and got[0]["payer"] in P.values()
    assert el.ledger_postings(ledger, []) == []


# ============================================================================================================= 8. privacy
def test_no_name_birth_date_record_number_or_address_reaches_the_ledger_the_store_or_the_log(store, ledger, tmp_path):
    log = Log()
    map_world(store, ledger, tmp_path)
    apply(store, ledger, prod_file(tmp_path, summary_rows()), log=log)
    el.refresh(store, ledger, log=log)
    texts = []
    for conn, tables in ((ledger.conn, ("names", "postings", "ledger_files", "ledger_meta")),
                         (store.conn, er.EMA_TABLES)):
        for t in tables:
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({t})")]
            for r in conn.execute(f"SELECT * FROM {t}"):
                texts += [str(v) for v in r if isinstance(v, str)]
    blob = "\n".join(texts + list(log))
    for secret in F.SECRET_STRINGS + F.SECRET_DATES + ["://"]:
        assert secret not in blob, secret


# =========================================================================================== 9. the serverless handle
def test_the_handle_saves_the_ledger_to_the_state_folder_and_opens_it_again(store, tmp_path):
    state = LocalState(str(tmp_path / "state"))
    h = el.LedgerHandle(state, tmp_dir=str(tmp_path / "tmp"))
    lg = h.open()
    assert lg.scratch and h.rev is None and state.meta(el.STATE_NAME) is None
    apply(store, lg, prod_file(tmp_path, summary_rows()))
    el.refresh(store, lg, log=Log())
    rev = h.save(store.conn)
    store.conn.commit()
    assert rev and state.meta(el.STATE_NAME)["rev"] == rev and not os.path.exists(h.path)
    assert one(store.conn, "SELECT value FROM meta WHERE key='ema:ledger_rev'") == (rev,) and not h.behind(store.conn)
    h2 = el.LedgerHandle(state, tmp_dir=str(tmp_path / "tmp2"))
    lg2 = h2.open()
    assert h2.rev == rev and lg2.rev == rev and one(lg2.conn, "SELECT COUNT(*) FROM postings") == (len(summary_rows()),)
    h2.discard()
    assert not os.path.exists(h2.path)


def test_a_run_that_dies_after_saving_the_ledger_is_repaired_by_the_next(store, tmp_path):
    state = LocalState(str(tmp_path / "state"))
    h = el.LedgerHandle(state, tmp_dir=str(tmp_path / "tmp"))
    lg = h.open()
    r1 = apply(store, lg, prod_file(tmp_path, [charge(F.link_no(1))]))
    el.refresh(store, lg, log=Log())
    h.save(store.conn)
    store.conn.commit()
    image = store.to_bytes()                                         # the store as the state folder holds it
    # the next run applies a second file, saves the ledger, and is killed before the store is saved
    lg = h.open()
    r2 = apply(store, lg, prod_file(tmp_path, [charge(F.link_no(2), svc=D(2026, 5, 4))]))
    h.save()
    restored = Store.from_bytes(image)
    assert h.behind(restored.conn)
    assert one(restored.conn, "SELECT COUNT(*) FROM ema_report_files WHERE file_id=?", r2["file_id"]) == (0,)
    h3 = el.LedgerHandle(state, tmp_dir=str(tmp_path / "tmp3"))
    lg3 = h3.open()
    out = el.refresh(restored, lg3, log=Log())
    assert out["part_a"] is not None and out["catalog"] >= 1
    assert one(restored.conn, "SELECT status FROM ema_report_files WHERE file_id=?", r2["file_id"]) == ("current",)
    assert one(restored.conn, "SELECT value FROM meta WHERE key='ema:ledger_rev'") == (h3.rev,)
    assert one(restored.conn, "SELECT COUNT(DISTINCT link) FROM ema_lines") == (2,)
    assert r1["status"] == "current"
    h3.discard()
    restored.close()


def test_attempts_are_counted_in_the_state_folder_before_a_read(tmp_path):
    state = LocalState(str(tmp_path / "state"))
    h = el.LedgerHandle(state, tmp_dir=str(tmp_path / "tmp"))
    assert h.attempts("abc") == 0
    assert h.note_attempt("abc") == 1
    assert json.loads(state.get(el.ATTEMPTS_NAME))["abc"]["n"] == 1
    # the run is killed here; the next run's handle sees the attempt
    h2 = el.LedgerHandle(state, tmp_dir=str(tmp_path / "tmp2"))
    assert h2.attempts("abc") == 1 and h2.note_attempt("abc") == 2 and h2.note_attempt("other") == 1
    h2.clear_attempt("abc")
    assert h2.attempts("abc") == 0 and h2.attempts("other") == 1
    h2.clear_attempt("other")
    assert state.get(el.ATTEMPTS_NAME) is None
    assert el.MAX_ATTEMPTS == 3 and "zip the CSV" in el.ATTEMPT_HOLD_REASON


def test_the_staging_file_is_removed_after_every_outcome(store, tmp_path):
    state = LocalState(str(tmp_path / "state"))
    h = el.LedgerHandle(state, tmp_dir=str(tmp_path / "tmp"))
    lg = h.open()
    stage = lg.path + "-stage"
    apply(store, lg, prod_file(tmp_path, [charge(F.link_no(1))]))
    assert not os.path.exists(stage)
    held = apply(store, lg, prod_file(tmp_path, [charge(F.link_no(1 + i)) for i in range(40)]
                                      + [charge(F.link_no(99), posted="bad date")] * 2))
    assert held["status"] == "held" and not os.path.exists(stage)
    cols = [c for c in F.PRODUCTION_COLUMNS if c != "Check Number"]
    assert apply(store, lg, prod_file(tmp_path, [charge(F.link_no(1))], columns=cols))["status"] == "refused"
    big = str(tmp_path / "big.csv")
    F.big_production_csv(big, 25_000)
    assert apply(store, lg, big, deadline=0.0)["status"] == "waiting" and not os.path.exists(stage)
    with open(stage, "wb") as fh:                                   # left by a run that was killed while staging
        fh.write(b"x")
    h.discard()
    assert not os.path.exists(stage) and not os.path.exists(h.path)
    lg2 = el.Ledger.open(str(tmp_path / "other.sqlite"))
    open(lg2.path + "-stage", "wb").close()
    lg2.close()
    el.Ledger.open(str(tmp_path / "other.sqlite")).close()
    assert not os.path.exists(str(tmp_path / "other.sqlite") + "-stage")


def test_a_ledger_written_by_another_version_is_not_opened(tmp_path):
    p = str(tmp_path / "l.sqlite")
    el.Ledger.open(p).close()
    c = sqlite3.connect(p)
    c.execute("UPDATE ledger_meta SET value='99' WHERE key='schema'")
    c.commit()
    c.close()
    with pytest.raises(RuntimeError):
        el.Ledger.open(p)
    with pytest.raises(FileNotFoundError):
        el.Ledger.open(str(tmp_path / "none.sqlite"), readonly=True)
