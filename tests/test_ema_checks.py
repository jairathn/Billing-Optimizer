"""
The checks EMA's A/R and Production reports make possible (remitwatch.ema_checks): the causes of payments with no
remittance (C1), write-off marks (C2), claims never sent, answered in EMA or never answered (C3, G8), payments EMA did
not post (C4), denials only EMA saw (C5), what EMA shows paid (C6), denials posted as contractual (C7), credits (C8),
the overlays (W2-W4) and the line outcome they share (G3).

Every row is invented (tests/ema_fixtures.py). The 835 side is written straight into the store's remittance tables; the
EMA side goes through the real readers, the posting ledger and its refresh, so the checks read exactly what Part A and
Part B write.
"""
from __future__ import annotations

import datetime as dt
import itertools
import json

import pytest

from remitwatch import ema_checks as ec, ema_ledger as el, ema_reports as er
from remitwatch.store import Store
from tests import ema_fixtures as F

D = dt.date
P = F.PAYERS
SVC = D(2026, 3, 2)
S = SVC.isoformat()
SALT_HEX = "cd" * 32
BCBS = "Anthem / BCBS"          # the group of P["bcbs"] (and of the 835 payer BLUE CROSS TEST)
NO_CLOSE = (lambda f: True)     # code_once: the claim carries the code once


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(er, "CLAIM_HOST_SUFFIX", F.HOST_SUFFIX)
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    for k in ("VERCEL", "REMITWATCH_EMA_REPORTS", "REMITWATCH_EMA_CLAIM_URL", "REMITWATCH_EMA_PAYER_ALIASES",
              "REMITWATCH_EMA_LEDGER", "REMITWATCH_EMA_REPORT_KEEP"):
        monkeypatch.delenv(k, raising=False)
    ec._LAST_POST.clear()


class Log(list):
    def __call__(self, *a):
        self.append(" ".join(str(x) for x in a))


@pytest.fixture
def store(tmp_path):
    s = Store(str(tmp_path / "store.sqlite"))
    er.ensure_schema(s.conn)
    s.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('ema:key_salt', ?)", (SALT_HEX,))
    s.conn.commit()
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


_N = itertools.count(1)
_TX = itertools.count(1)


# ------------------------------------------------------------------------------------------------------- builders
def charge(link, cpt="99213", amount=150, patient=0, svc=SVC, posted=None, **kw):
    return F.production_row(link=link, cpt=cpt, charges=amount, patient=patient, svc=svc, posted=posted or svc, **kw)


def pay(link, check, amount, cpt="99213", patient=0, svc=SVC, posted=D(2026, 3, 31), payer=P["bcbs"], **kw):
    return F.production_row(link=link, cpt=cpt, ins_paid=amount, check=check, patient=patient, svc=svc, posted=posted,
                            payer=payer, **kw)


def adj(link, reason, amount, cpt="99213", patient=0, svc=SVC, posted=D(2026, 3, 31), contractual=False, payer=P["bcbs"], **kw):
    money = {"contractual": amount} if contractual else {"noncontractual": amount}
    return F.production_row(link=link, cpt=cpt, reason=reason, patient=patient, svc=svc, posted=posted, payer=payer, **money, **kw)


def patient_pay(amount, cpt="99213", patient=0, svc=SVC, posted=D(2026, 4, 15)):
    return F.production_row(payer="Patient", link=None, cpt=cpt, patient=patient, svc=svc, posted=posted, pat_paid=amount)


def load(store, ledger, tmp_path, rows, ar_rows=None):
    """Read an A/R (when given) and a Production file, then refresh the summaries (Part A and Part B)."""
    if ar_rows is not None:
        p = F.write(str(tmp_path / f"ar{next(_N)}.xlsx"), F.ar_xlsx(ar_rows))
        r = er.ingest_file(store, p, via="test", log=Log())
        assert r["status"] == "current", r
    if rows:
        p = F.write(str(tmp_path / f"prod{next(_N)}.csv"), F.production_csv(rows))
        r = er.ingest_file(store, p, via="test", ledger=ledger, log=Log())
        assert r["status"] == "current", r
    el.refresh(store, ledger, parts="AB", log=Log())


def remit(store, *, trace, paid_on, claims, payer="BLUE CROSS TEST", status="1"):
    """One 835 payment written straight into the store. claims: dicts with pcn, last, first, status and lines
    [(svc, code, charge $, paid $, [(group, reason, $), ...])]."""
    c = store.conn
    tk = f"TX{next(_TX):05d}"
    total = sum(round(ln[3] * 100) for cl in claims for ln in cl["lines"])
    c.execute("INSERT INTO transactions (transaction_key, trace_number, payment_date, payer_name, superseded, "
              "claim_paid_total_cents, payment_amount_cents) VALUES (?,?,?,?,0,?,?)", (tk, trace, paid_on, payer, total, total))
    for i, cl in enumerate(claims):
        ck = f"{tk}-{i}"
        c.execute("INSERT INTO claims (claim_key, transaction_key, patient_control_number, patient_last, patient_first, payment_date, "
                  "trace_number, statement_from, claim_status_code, charge_cents, paid_cents, payer_name) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  (ck, tk, cl["pcn"], cl.get("last", "TESTPATIENT"), cl.get("first", "ALPHA"), paid_on, trace, cl["lines"][0][0],
                   cl.get("status", status), sum(round(ln[2] * 100) for ln in cl["lines"]), sum(round(ln[3] * 100) for ln in cl["lines"]),
                   payer))
        for j, ln in enumerate(cl["lines"]):
            svc, code, chg, paid = ln[:4]
            lk = f"{ck}-{j}"
            c.execute("INSERT INTO lines (line_key, claim_key, transaction_key, procedure_code, service_date, charge_cents, paid_cents, "
                      "patient_control_number, payment_date, payer_name, claim_status_code) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (lk, ck, tk, code, svc, round(chg * 100), round(paid * 100), cl["pcn"], paid_on, payer, cl.get("status", status)))
            for g, r, amt in (ln[4] if len(ln) > 4 else []):
                c.execute("INSERT INTO adjustments (transaction_key, claim_key, line_key, level, group_code, reason_code, amount_cents) "
                          "VALUES (?,?,?,?,?,?,?)", (tk, ck, lk, "line", g, r, round(amt * 100)))
    c.commit()
    return tk


def finding(tk=None, **kw):
    """An item as the Detector holds it (the fields the checks read)."""
    f = {"finding_id": "F1", "signal": "DENIED_AUTH", "category": "denial", "line_key": "LK-1", "claim_key": "CK-1",
         "transaction_key": tk, "patient_control_number": F.claim_id(1), "service_date": S, "payment_date": "2026-03-28",
         "procedure_code": "99213", "original_procedure_code": None, "billed_code": "99213", "expected_cents": 9000,
         "shortfall_cents": 9000, "carc": "197", "payer_group": BCBS, "payer_kind": "commercial", "paid_cents": 0,
         "evidence": "{}", "status": "open", "stale": 0, "written_off_cents": None, "appeal_deadline": "2027-12-31",
         "modifiers": None}
    f.update(kw)
    return f


def denial_world(store, ledger, tmp_path, extra=(), *, own_posted=D(2026, 3, 31), status="Resolved/Paid", ar_rows=None):
    """Claim 1 (link 1): a 99213 charged $150 and denied by Blue Cross on 835 trace 1 (paid $0, CO-197). EMA posted the
    $0 answer under the trace and wrote the denial off the same day. A later charge on link 99 carries Production's
    as_of to May 1. Returns the 835 payment's key."""
    tk = remit(store, trace=F.check_no(1), paid_on="2026-03-28",
               claims=[{"pcn": F.claim_id(1), "lines": [(S, "99213", 150, 0, [("CO", "197", 150)])]}])
    rows = [charge(F.link_no(1), status=status), pay(F.link_no(1), F.check_no(1), 0, posted=own_posted, status=status),
            adj(F.link_no(1), "CO-197", 150, posted=own_posted, status=status),
            charge(F.link_no(99), cpt="99212", patient=3, svc=D(2026, 3, 20), posted=D(2026, 5, 1), status="Payment Pending")]
    load(store, ledger, tmp_path, rows + list(extra), ar_rows)
    ec.classify_checks(store.conn)
    return tk


TODAY = "2026-05-03"


def keep(store, *pcns):
    """Items on file for these claims, as a cycle's detection run leaves them (the summary keeps their EMA lines either
    way: it keeps every line)."""
    from remitwatch import detect
    detect.ensure_schema(store.conn)
    for pcn in pcns:
        store.conn.execute("INSERT INTO findings (finding_id, signal, category, status, patient_control_number, stale) "
                           "VALUES (?,?,?,?,?,0)", (f"K-{pcn}", "UNDERPAID_VS_OWN_RATE", "underpayment", "open", pcn))
    store.conn.commit()


def outcomes(store, fs, code_once=NO_CLOSE):
    return ec.line_outcomes(store.conn, fs, code_once=code_once, today=TODAY)


# ================================================================================================ statuses, payers
def test_the_status_dictionary_reads_emas_exact_words():
    sc = ec.status_class
    assert sc("Batched for Printing") == "not_sent" and sc("Batched for Sending") == "not_sent" and sc("Invalid") == "not_sent"
    assert sc("Ready for Posting") == "answered" and sc("Resolved/Paid") == "paid" and sc("Inactive/Voided") == "void"
    assert sc("Resolved/Other", True) == "settled" and sc("Resolved/Other", False) == "gave_up"
    assert sc("Something New") == "unknown" and sc(None) == "none" and sc("-") == "none"


def test_payer_groups_compare_across_the_two_vocabularies():
    assert ec._same_payer("UnitedHealthcare Medicare Advantage", "UHC Medicare Advantage")
    assert ec._same_payer("Anthem / BCBS Medicare Advantage", "Anthem Medicare Advantage")
    assert not ec._same_payer("Medicare KY (CGS J15)", "Medicare IN (WPS J8)")
    assert not ec._same_payer("Anthem / BCBS", "Anthem Medicare Advantage")
    assert not ec._same_payer(None, BCBS)


# ================================================================================================ C1: missing remittances
def add_check(store, check, payer, cents, posted, *, match="none", grp=None, kind=None, damaged=0, shape=None, trace=None):
    grp_, kind_ = er.payer_group_of(store.conn, payer)
    store.conn.execute("""INSERT INTO ema_checks (check_no, payer, payer_group, payer_kind, shape, damaged, first_posted, last_posted, rows,
                          links, lines, ins_paid, zero_rows, match, trace_key) VALUES (?,?,?,?,?,?,?,?,1,1,1,?,0,?,?)""",
                       (check, payer, grp or grp_, kind or kind_, shape or er.shape(check), damaged, posted, posted, cents, match, trace))


def test_each_check_with_no_remittance_gets_its_cause(store):
    c = store.conn
    remit(store, trace="000001", paid_on="2026-01-02", claims=[{"pcn": F.claim_id(50), "lines": [(S, "99213", 100, 10)]}])
    newest = remit(store, trace="000002", paid_on="2026-06-30", claims=[{"pcn": F.claim_id(51), "lines": [(S, "99213", 100, 10)]}])
    assert newest
    # the payer's own number series: five matched checks of shape 999999999 in the year before
    for i in range(5):
        add_check(store, f"77000000{i}", P["bcbs"], 5000, f"2026-0{i + 1}-10", match="exact")
    add_check(store, "770000009", P["bcbs"], 12345, "2026-06-01")                       # same shape, no 835: feed gap
    add_check(store, "PAPER-1", P["bcbs"], 2222, "2026-06-02")                          # another shape: other
    add_check(store, "VA0001", P["va"], 50000, "2026-06-03")                            # VA, never an 835: no_era_va
    add_check(store, "NE0001", P["commercial"], 3000, "2026-06-04")                     # a payer never matched: no_era
    add_check(store, "1.2E+09", P["bcbs"], 999, "2026-06-05", damaged=1)               # damaged by a spreadsheet
    add_check(store, "LATE01", P["bcbs"], 777, "2026-07-20")                            # after the remittances stopped
    add_check(store, "ZERO01", P["bcbs"], 0, "2026-06-06")                              # status rows only: not judged
    c.commit()
    out = ec.classify_checks(c)
    cause = dict(c.execute("SELECT check_no, cause FROM ema_checks").fetchall())
    assert cause["770000009"] == "feed_gap" and cause["PAPER-1"] == "other" and cause["VA0001"] == "no_era_va"
    assert cause["NE0001"] == "no_era" and cause["1.2E+09"] == "damaged" and cause["LATE01"] == "after_feed"
    assert cause["ZERO01"] is None and cause["770000000"] is None
    assert out["by_cause"]["feed_gap"] == {"checks": 1, "cents": 12345}
    months = {(r[0], r[1]): r[2] for r in c.execute("SELECT month, detail, cents FROM ema_months WHERE measure='no835'")}
    assert months[("2026-06", "feed_gap")] == 12345 and months[("2026-07", "after_feed")] == 777
    # the CSV names checks, dates, payers, amounts and causes only
    rows = list(ec.checks_csv(c, cause="feed_gap"))
    assert rows[0][:3] == ["check_number", "first_posted", "last_posted"] and rows[1][0] == "770000009" and rows[1][5] == "123.45"


def test_a_supplement_carrier_is_judged_by_its_own_number_series(store):
    """A group of many payers mixes their number series: a carrier with five matched checks of its own is compared with
    them, not with the group's."""
    c = store.conn
    remit(store, trace="000001", paid_on="2026-01-02", claims=[{"pcn": F.claim_id(50), "lines": [(S, "99213", 100, 10)]}])
    remit(store, trace="000002", paid_on="2026-06-30", claims=[{"pcn": F.claim_id(51), "lines": [(S, "99213", 100, 10)]}])
    for i in range(5):
        add_check(store, f"MO{i}1234", "Mutual Of Omaha Test", 1000, f"2026-0{i + 1}-11", match="exact")   # shape AA99999
    for i in range(30):
        add_check(store, f"{900000 + i}", "Medico Test", 1000, f"2026-0{1 + i % 5}-12", match="exact")      # shape 999999
    add_check(store, "MO99999", "Mutual Of Omaha Test", 4000, "2026-06-01")
    c.commit()
    ec.classify_checks(c)
    assert one(c, "SELECT payer_group FROM ema_checks WHERE check_no='MO99999'") == ("Medicare Supplement carriers",)
    assert one(c, "SELECT cause FROM ema_checks WHERE check_no='MO99999'") == ("feed_gap",)


def test_a_misnumbered_check_is_paired_with_its_835_and_c4_lists_neither(store, ledger, tmp_path):
    """EMA posted an 835's money under another number: the check is 'misnumbered', paired with the 835, and C4 does not
    call that 835 unposted."""
    tk = remit(store, trace="ZZTRACE1", paid_on="2026-03-28", claims=[{"pcn": F.claim_id(1), "lines": [(S, "99213", 150, 80)]}])
    remit(store, trace=F.check_no(99), paid_on="2026-04-28", claims=[{"pcn": F.claim_id(99), "last": "FIXTURE", "first": "DELTA",
                                                                       "lines": [("2026-03-20", "99212", 90, 40)]}])
    rows = [charge(F.link_no(1)), pay(F.link_no(1), "TYPO-1", 80.40, posted=D(2026, 4, 2)),
            charge(F.link_no(99), cpt="99212", patient=3, svc=D(2026, 3, 20), posted=D(2026, 5, 1)),
            pay(F.link_no(99), F.check_no(99), 40, cpt="99212", patient=3, svc=D(2026, 3, 20), posted=D(2026, 4, 30))]
    load(store, ledger, tmp_path, rows)
    out = ec.classify_checks(store.conn)
    assert one(store.conn, "SELECT cause, pair_trace FROM ema_checks WHERE check_no='TYPO-1'") == ("misnumbered", tk)
    assert out["pairs"] == 1
    assert ec.unposted_remits(store.conn, today=TODAY) == []


def test_c1_counts_payments_posted_with_no_check_number_on_their_own_line(store, ledger, tmp_path):
    rows = [charge(F.link_no(1)), pay(F.link_no(1), F.check_no(1), 60, posted=D(2026, 3, 10)), charge(F.link_no(2), patient=1),
            pay(F.link_no(2), None, 45, patient=1, posted=D(2026, 3, 20)),
            charge(F.link_no(99), cpt="99212", patient=3, svc=D(2026, 3, 20), posted=D(2026, 5, 1))]
    load(store, ledger, tmp_path, rows)
    ec.classify_checks(store.conn)
    s = ec.c1_summary(store.conn, "2026-03-01", "2026-03-31")
    assert s["no_check_cents"] == 4500 and s["posted_cents"] == 10500
    # the line is kept by whole months: a range that starts mid-month gives the same figure
    assert ec.c1_summary(store.conn, "2026-03-25", "2026-03-31")["no_check_cents"] == 4500


def test_c1_data_health_and_the_feed_gap_alert(store):
    c = store.conn
    remit(store, trace="000001", paid_on="2026-01-02", claims=[{"pcn": F.claim_id(50), "lines": [(S, "99213", 100, 10)]}])
    remit(store, trace="000002", paid_on="2026-06-30", claims=[{"pcn": F.claim_id(51), "lines": [(S, "99213", 100, 10)]}])
    for i in range(5):
        add_check(store, f"77000000{i}", P["bcbs"], 5000, f"2026-0{i + 1}-10", match="exact")
    add_check(store, "770000008", P["bcbs"], 300000, "2026-06-01")
    add_check(store, "770000009", P["bcbs"], 300000, "2026-06-02")
    c.commit()
    ec.classify_checks(c)
    alerts = [a for a in ec.alert_candidates(c, today="2026-07-01") if a["kind"] == "REMITS_MISSING"]
    assert len(alerts) == 1 and alerts[0]["cents"] == 600000 and alerts[0]["payer_group"] == BCBS
    # $6,000 of payments with no remittance in the last full month is over max($5,000, 2% of the matched checks)
    health = {h["key"]: h for h in ec.data_health(c, today="2026-07-01")}
    assert health["remits_missing"]["ok"] is False and health["remits_missing"]["month"] == "2026-06"
    # under the threshold: one check of $1,999 is no alert
    c.execute("DELETE FROM ema_checks WHERE check_no='770000009'")
    c.execute("UPDATE ema_checks SET ins_paid=199900 WHERE check_no='770000008'")
    c.commit()
    ec.classify_checks(c)
    assert not [a for a in ec.alert_candidates(c, today="2026-07-01") if a["kind"] == "REMITS_MISSING"]


# ============================================================================================ C4: 835 payments not posted
def c4_world(store, ledger, tmp_path, extra=()):
    rows = [charge(F.link_no(1)), pay(F.link_no(1), F.check_no(1), 80),
            charge(F.link_no(2), patient=1), charge(F.link_no(99), cpt="99212", patient=3, svc=D(2026, 3, 20), posted=D(2026, 5, 1))]
    remit(store, trace=F.check_no(1), paid_on="2026-03-28", claims=[{"pcn": F.claim_id(1), "lines": [(S, "99213", 150, 80)]}])
    return rows + list(extra)


def test_an_835_payment_emas_postings_never_show_becomes_an_item(store, ledger, tmp_path):
    rows = c4_world(store, ledger, tmp_path)
    tk = remit(store, trace="UNSEEN77", paid_on="2026-03-30", payer="UMR",
               claims=[{"pcn": F.claim_id(2), "last": "SAMPLE", "first": "BRAVO", "lines": [(S, "99213", 150, 95.5)]}])
    load(store, ledger, tmp_path, rows)
    ec.classify_checks(store.conn)
    items = ec.unposted_remits(store.conn, today=TODAY)
    assert [i["key_parts"] for i in items] == [(tk,)]
    it = items[0]
    assert it["signal"] == "REMIT_NOT_POSTED" and it["category"] == "fees" and it["actionable"] == "review"
    assert it["expected_cents"] == 9550 and it["appeal_deadline"] is None and it["source"] == "835"
    assert it["title"] == "Paid $95.50 by UMR on 2026-03-30 (trace ending EN77): not posted in EMA"
    assert it["claim"]["transaction_key"] == tk and it["claim"]["patient_control_number"] == F.claim_id(2)
    assert ec._meta(store.conn, ec.UNPOSTED_KEY)["n"] == 1
    csv_rows = list(ec.unposted_csv(store.conn, today=TODAY))
    assert csv_rows[1][:2] == ["EN77", "2026-03-30"] and "UNSEEN77" not in json.dumps(csv_rows)


def test_an_835_payment_ema_posted_under_another_number_is_not_itemized(store, ledger, tmp_path):
    """EMA posted the 835's $95.50 on the claim's own EMA claim, under a paper check number no 835 carries: the money was
    posted, so it is no "not posted" item; it is counted apart. A payment for another amount does not count."""
    rows = c4_world(store, ledger, tmp_path, [pay(F.link_no(2), "PAPER-77", 95.5, patient=1, posted=D(2026, 4, 2))])
    remit(store, trace="UNSEEN77", paid_on="2026-03-30", payer="UMR",
          claims=[{"pcn": F.claim_id(2), "last": "SAMPLE", "first": "BRAVO", "lines": [(S, "99213", 150, 95.5)]}])
    load(store, ledger, tmp_path, rows, [F.ar_row(claim=F.claim_id(2), link=F.link_no(2), patient=1, svc=SVC, current=0, aging=D(2026, 4, 2))])
    ec.classify_checks(store.conn)
    assert ec.unposted_remits(store.conn, today=TODAY) == []
    summary = ec._meta(store.conn, ec.UNPOSTED_KEY)
    assert (summary["n"], summary["elsewhere_n"], summary["elsewhere_cents"]) == (0, 1, 9550)


def test_an_835_payment_ema_posted_for_another_amount_under_another_number_is_still_itemized(store, ledger, tmp_path):
    rows = c4_world(store, ledger, tmp_path, [pay(F.link_no(2), "PAPER-78", 40, patient=1, posted=D(2026, 4, 2))])
    tk = remit(store, trace="UNSEEN78", paid_on="2026-03-30", payer="UMR",
               claims=[{"pcn": F.claim_id(2), "last": "SAMPLE", "first": "BRAVO", "lines": [(S, "99213", 150, 95.5)]}])
    load(store, ledger, tmp_path, rows, [F.ar_row(claim=F.claim_id(2), link=F.link_no(2), patient=1, svc=SVC, current=0, aging=D(2026, 4, 2))])
    ec.classify_checks(store.conn)
    assert [i["key_parts"] for i in ec.unposted_remits(store.conn, today=TODAY)] == [(tk,)]


def test_c4_needs_production_to_cover_the_payment_and_to_be_fresh(store, ledger, tmp_path):
    rows = c4_world(store, ledger, tmp_path)
    remit(store, trace="UNSEEN78", paid_on="2026-04-15", payer="UMR",          # as_of May 1 is under 21 days after it
          claims=[{"pcn": F.claim_id(2), "lines": [(S, "99213", 150, 95)]}])
    remit(store, trace="UNSEEN79", paid_on="2026-03-30", payer="UMR",          # a 2025 service: no box holds it
          claims=[{"pcn": F.claim_id(3), "lines": [("2025-12-01", "99213", 150, 95)]}])
    remit(store, trace="UNSEEN80", paid_on="2026-03-30", payer="UMR",          # paid $1: not an item
          claims=[{"pcn": F.claim_id(4), "lines": [(S, "99213", 150, 1)]}])
    load(store, ledger, tmp_path, rows)
    ec.classify_checks(store.conn)
    assert ec.unposted_remits(store.conn, today=TODAY) == []
    remit(store, trace="UNSEEN81", paid_on="2026-03-30", payer="UMR", claims=[{"pcn": F.claim_id(5), "lines": [(S, "99213", 150, 95)]}])
    assert len(ec.unposted_remits(store.conn, today=TODAY)) == 1
    # Production more than 14 days old proves nothing by an absence
    assert ec.unposted_remits(store.conn, today="2026-05-20") == []
    assert ec._meta(store.conn, ec.UNPOSTED_KEY)["fresh"] is False


def test_a_payment_posted_short_and_offsetting_differences(store, ledger, tmp_path):
    extra = [charge(F.link_no(3), patient=2), pay(F.link_no(3), "880000031", 50, patient=2, payer=P["medicare"]),
             charge(F.link_no(4), patient=2, cpt="17000"), pay(F.link_no(4), "880000032", 70, patient=2, cpt="17000", payer=P["medicare"]),
             charge(F.link_no(5), patient=1, cpt="11102"), pay(F.link_no(5), "880000033", 20, patient=1, cpt="11102")]
    rows = c4_world(store, ledger, tmp_path, extra)
    # Medicare: one trace EMA posted $30 short of its claims, another $30 over: they offset, nothing is listed
    remit(store, trace="880000031", paid_on="2026-03-28", payer="MEDICARE OF KENTUCKY",
          claims=[{"pcn": F.claim_id(3), "last": "EXAMPLE", "first": "CHARLIE", "lines": [(S, "99213", 150, 80)]}])
    remit(store, trace="880000032", paid_on="2026-03-29", payer="MEDICARE OF KENTUCKY",
          claims=[{"pcn": F.claim_id(4), "last": "EXAMPLE", "first": "CHARLIE", "lines": [(S, "17000", 150, 40)]}])
    # Blue Cross: EMA posted $20 of a $60 payment and nothing offsets it: posted $40 short
    tk = remit(store, trace="880000033", paid_on="2026-03-28", claims=[{"pcn": F.claim_id(5), "last": "SAMPLE", "first": "BRAVO",
                                                                        "lines": [(S, "11102", 150, 60)]}])
    load(store, ledger, tmp_path, rows)
    ec.classify_checks(store.conn)
    items = ec.unposted_remits(store.conn, today=TODAY)
    assert len(items) == 1 and items[0]["key_parts"] == (tk,) and items[0]["expected_cents"] == 4000
    assert items[0]["evidence"]["short"] is True and items[0]["title"].endswith("EMA posted $20.00, $40.00 short")
    s = ec._meta(store.conn, ec.UNPOSTED_KEY)
    assert (s["offset_n"], s["offset_cents"], s["over_n"], s["over_cents"]) == (1, 3000, 1, 3000)


# ============================================================================================== C3: statuses and claims
def test_claim_statuses_take_the_newest_report(store, ledger, tmp_path):
    ar = [F.ar_row(claim=F.claim_id(7), link=F.link_no(7), status="Batched for Printing", svc=D(2026, 3, 2), aging=D(2026, 3, 3),
                   current=150)]
    rows = [charge(F.link_no(7), status="Payment Pending", posted=D(2026, 3, 3)),
            charge(F.link_no(99), cpt="99212", patient=3, svc=D(2026, 3, 20), posted=D(2026, 5, 1))]
    load(store, ledger, tmp_path, rows, ar)
    st = ec.claim_statuses(store.conn, claim_ids=[F.claim_id(7)], links=[F.link_no(7)])
    # the A/R's as_of (Mar 3) is older than Production's (May 1): Production's word wins for the claim number
    assert st[F.link_no(7)]["source"] == "production" and st[F.link_no(7)]["class"] == "pending"
    assert st[F.claim_id(7)]["class"] == "pending"


def test_a_claim_batched_for_printing_two_weeks_becomes_not_sent(store, ledger, tmp_path):
    ar = [F.ar_row(claim=F.claim_id(7), link=F.link_no(7), status="Batched for Printing", svc=D(2026, 4, 1), aging=None, current=150),
          F.ar_row(claim=F.claim_id(8), link=F.link_no(8), status="Invalid", svc=D(2026, 4, 21), aging=None, current=90, cpt="11102"),
          F.ar_row(claim=F.claim_id(9), link=F.link_no(9), status="Batched for Printing", svc=D(2026, 4, 25), aging=None, current=60)]
    load(store, ledger, tmp_path, [], ar)
    items = ec.not_sent(store.conn, today="2026-04-27", rate=lambda g, c, y: 8000)
    keys = {i["key_parts"][0]: i for i in items}
    # judged at the A/R's own date (Apr 25): 24 days since the service: listed; Invalid needs 3 days (4 here); Batched for
    # Printing 0 days old is not yet
    assert set(keys) == {F.claim_id(7), F.claim_id(8)}
    it = keys[F.claim_id(7)]
    assert it["signal"] == "NOT_SENT" and it["id_signal"] == "EMA_OPEN_CLAIM" and it["actionable"] == "correct_and_resubmit"
    assert it["title"] == "Never left EMA: Batched for Printing since 2026-04-01: 99213"
    assert it["evidence"]["since_basis"] == "the date of service" and it["expected_cents"] == 8000
    assert it["appeal_deadline"] == "2026-06-30"          # Blue Cross commercial: 90 days from the date of service
    assert it["heir_keys"] == [("EMA_OPEN_CLAIM", f"link:{F.link_no(7)}")]
    # the export path already listed claim 7: skipped
    assert {i["key_parts"][0] for i in ec.not_sent(store.conn, today="2026-04-27", skip_claim_ids={F.claim_id(7)})} == {F.claim_id(8)}


def test_a_rejected_claim_on_the_ar_becomes_rejected_pre_payer(store, ledger, tmp_path):
    ar = [F.ar_row(claim=F.claim_id(7), link=F.link_no(7), status="Rejected", svc=D(2026, 4, 1), current=150)]
    load(store, ledger, tmp_path, [], ar)
    items = ec.rejected_claims(store.conn, today="2026-11-16")
    assert [i["signal"] for i in items] == ["REJECTED_PRE_PAYER"] and items[0]["key_parts"] == (F.claim_id(7),)
    assert items[0]["title"].startswith(f"Rejected before reaching {BCBS} (EMA: Rejected)")


def answers_world(store, ledger, tmp_path):
    """Claims with no remittance: 1 paid in EMA by a paper check; 2 answered with a $0 row that carries the payer's allowed
    amount (put on the patient); 3 a payer's CO-197; 4 only a status (Resolved/Paid); 5 paid only by the ASC's
    separate-payee check; 6 and 16 the same content (a tie); 7 only a bare $0 row on a check (no reason, no allowed amount,
    no adjustment: a check that lists the claim and pays nothing)."""
    rows = [charge(F.link_no(1), status="Payment Pending"), pay(F.link_no(1), "PAPER-1", 60, posted=D(2026, 4, 20)),
            charge(F.link_no(2), patient=1), pay(F.link_no(2), "ZEROANS", 0, patient=1, posted=D(2026, 4, 20), allowed=90),
            charge(F.link_no(7), cpt="99214", amount=170, patient=2), pay(F.link_no(7), "BATCH-7", 0, cpt="99214", patient=2,
                                                                        posted=D(2026, 4, 20)),
            charge(F.link_no(3), patient=2), adj(F.link_no(3), "CO-197", 150, patient=2, posted=D(2026, 4, 20)),
            charge(F.link_no(4), patient=3, status="Resolved/Paid"),
            charge(F.link_no(5), cpt="11102", patient=1), pay(F.link_no(5), "ASC-77", 70, cpt="11102", patient=1, posted=D(2026, 4, 20)),
            charge(F.link_no(6), cpt="17000", amount=210, patient=2), charge(F.link_no(16), cpt="17000", amount=210, patient=3),
            charge(F.link_no(99), cpt="99212", patient=3, svc=D(2026, 3, 20), posted=D(2026, 5, 1))]
    load(store, ledger, tmp_path, rows)
    ec.classify_checks(store.conn)
    store.conn.execute("UPDATE ema_checks SET cause='separate_payee' WHERE check_no='ASC-77'")
    store.conn.commit()


def test_claim_answers_from_emas_postings(store, ledger, tmp_path):
    answers_world(store, ledger, tmp_path)
    claims = [{"claim_id": F.claim_id(i), "sent": "2026-03-05", "payer_group": BCBS, "lines": [(S, cpt, chg)]}
              for i, cpt, chg in ((1, "99213", 15000), (2, "99213", 15000), (3, "99213", 15000), (4, "99213", 15000),
                                  (5, "11102", 15000), (6, "17000", 21000), (7, "99214", 17000))]
    # claims 1-5 share a date, code and charge with each other: tie them by their claim ids, as the A/R does
    for i in (1, 2, 3, 4, 5, 7):
        store.conn.execute("INSERT INTO ema_claim_links (claim_id, link, bill, first_seen, last_seen) VALUES (?,?,?,?,?)",
                           (F.claim_id(i), F.link_no(i), F.bill_id(i), "2026-04-01", "2026-04-01"))
    store.conn.commit()
    got = ec.claim_answers(store.conn, claims)
    assert got[F.claim_id(1)]["answered"] and got[F.claim_id(1)]["how"] == "paid" and got[F.claim_id(1)]["paid_cents"] == 6000
    assert got[F.claim_id(2)]["how"] == "zero_check" and got[F.claim_id(3)]["how"] == "carc"
    assert not got[F.claim_id(4)]["answered"]                 # a status alone never answers
    assert not got[F.claim_id(5)]["answered"]                 # the ASC's separate-payee money never answers
    assert got[F.claim_id(6)]["link"] is None                 # two EMA claims with the same content: no bridge
    # a bare $0 row on a check answers nothing; the item can say EMA shows one
    assert not got[F.claim_id(7)]["answered"] and got[F.claim_id(7)]["bare_zero"]["on"] == "2026-04-20"
    # sent after the answer: not answered
    assert not ec.claim_answers(store.conn, [dict(claims[0], sent="2026-04-25")])[F.claim_id(1)]["answered"]


def test_the_content_bridge_ties_a_claim_to_its_one_ema_claim(store, ledger, tmp_path):
    rows = [charge(F.link_no(1), cpt="11102", amount=180), charge(F.link_no(1), cpt="17000", amount=75),
            pay(F.link_no(1), "PAPER-9", 90, cpt="11102", posted=D(2026, 4, 20)),
            charge(F.link_no(2), cpt="11102", amount=180, patient=2),                  # one code alike, not the other
            charge(F.link_no(99), cpt="99212", patient=3, svc=D(2026, 3, 20), posted=D(2026, 5, 1))]
    load(store, ledger, tmp_path, rows)
    claim = {"claim_id": F.claim_id(1), "sent": "2026-03-05", "payer_group": BCBS,
             "lines": [(S, "11102", 18000), (S, "17000", 4000), (S, "17000", 3500)]}      # 17000 twice: EMA keeps one line
    got = ec.claim_answers(store.conn, [claim])[F.claim_id(1)]
    assert (got["link"], got["bridge"], got["answered"], got["how"]) == (F.link_no(1), "content", True, "paid")
    # another payer group: no bridge
    assert ec.claim_answers(store.conn, [dict(claim, payer_group="Medicare KY (CGS J15)")])[F.claim_id(1)]["link"] is None


def test_answers_through_uses_productions_postings_while_fresh(store, ledger, tmp_path):
    rows = [charge(F.link_no(1)), pay(F.link_no(1), "PAPER-1", 60, posted=D(2026, 4, 28)),
            charge(F.link_no(99), cpt="99212", patient=3, svc=D(2026, 3, 20), posted=D(2026, 5, 1))]
    load(store, ledger, tmp_path, rows)
    assert ec.answers_through(store.conn, BCBS, "2026-03-31", today=TODAY) == "2026-04-23"      # May 1 less 8 days
    assert ec.answers_through(store.conn, "Medicare KY (CGS J15)", "2026-03-31", today=TODAY) == "2026-03-31"  # no recent posting
    assert ec.answers_through(store.conn, BCBS, "2026-03-31", today="2026-06-30") == "2026-03-31"            # stale


def test_unanswered_claims_from_production(store, ledger, tmp_path):
    rows = [charge(F.link_no(1), status="Payment Pending", posted=D(2026, 3, 3)),                   # no answer at all: listed
            charge(F.link_no(2), patient=1, status="Payment Pending", posted=D(2026, 3, 3)),
            pay(F.link_no(2), "ZEROANS", 0, patient=1, posted=D(2026, 4, 1)),                         # a $0 answer: not listed
            adj(F.link_no(2), "CO-45", 150, patient=1, posted=D(2026, 4, 1), contractual=True),      # (its reason, that day)
            charge(F.link_no(6), cpt="99214", amount=170, patient=3, status="Payment Pending", posted=D(2026, 3, 3)),
            pay(F.link_no(6), "BATCH-6", 0, cpt="99214", patient=3, posted=D(2026, 4, 1)),            # a bare $0 row: listed
            charge(F.link_no(3), patient=2, status="Rejected", posted=D(2026, 3, 3)),               # rejected: C3f's
            charge(F.link_no(4), cpt="11102", patient=1, status="Payment Pending", posted=D(2026, 4, 20)),   # not due yet
            charge(F.link_no(5), cpt="17000", patient=2, status="Payment Pending", posted=D(2026, 3, 3)),   # its 835 exists
            charge(F.link_no(99), cpt="99212", patient=3, svc=D(2026, 3, 20), posted=D(2026, 5, 1)),
            pay(F.link_no(98), "PAPER-5", 20, cpt="99212", patient=3, posted=D(2026, 4, 28))]            # Blue Cross is active
    load(store, ledger, tmp_path, rows)
    remit(store, trace="ZZDEN5", paid_on="2026-04-01", claims=[{"pcn": F.claim_id(5), "last": "EXAMPLE", "first": "CHARLIE",
                                                                "lines": [(S, "17000", 150, 0)]}])
    items = ec.unanswered_claims(store.conn, today=TODAY, rate=lambda g, c, y: 7000)
    assert [i["key_parts"] for i in items] == [(f"link:{F.link_no(1)}",), (f"link:{F.link_no(6)}",)]
    it = items[0]
    assert it["signal"] == "NO_ANSWER" and it["id_signal"] == "EMA_OPEN_CLAIM" and it["expected_cents"] == 7000
    assert it["title"] == f"Charged 2026-03-03, no answer from {BCBS} in 59 days: 99213"
    assert it["evidence"]["answer_due"] == "2026-04-17" and it["ema_link"] == F.link_no(1)
    # EMA claim 6's only answer is a bare $0 row: listed, and the item says what EMA shows
    z = items[1]
    assert z["evidence"]["ema_bare_zero"]["on"] == "2026-04-01"
    assert z["hint"].startswith("EMA shows a $0 row for this claim on a check (ending") and "no reason, allowed amount or adjustment" in z["hint"]
    # once the A/R names the claim, the item is keyed by its claim id and inherits the link-keyed item's work
    store.conn.execute("INSERT INTO ema_claim_links (claim_id, link, bill, first_seen, last_seen) VALUES (?,?,?,?,?)",
                       (F.claim_id(1), F.link_no(1), F.bill_id(1), "2026-04-30", "2026-04-30"))
    store.conn.commit()
    items = ec.unanswered_claims(store.conn, today=TODAY)
    assert items[0]["key_parts"] == (F.claim_id(1),) and items[0]["heir_keys"] == [("EMA_OPEN_CLAIM", f"link:{F.link_no(1)}")]
    assert [i["key_parts"] for i in ec.unanswered_claims(store.conn, today=TODAY, skip_claim_ids={F.claim_id(1)})] == [
        (f"link:{F.link_no(6)}",)]
    assert ec.unanswered_claims(store.conn, today="2026-06-30") == []          # Production 60 days old: nothing


def test_turnaround_and_posting_lag_need_twenty_answers(store):
    for i in range(25):
        remit(store, trace=f"T{i:03d}", paid_on=(D(2026, 3, 1) + dt.timedelta(days=10 + i)).isoformat(),
              claims=[{"pcn": F.claim_id(100 + i), "lines": [("2026-03-01", "99213", 100, 50)]}])
    remit(store, trace="U1", paid_on="2026-03-20", payer="UMR", claims=[{"pcn": F.claim_id(200), "lines": [("2026-03-01", "99213", 100, 50)]}])
    for i in range(25):
        add_check(store, f"T{i:03d}", P["bcbs"], 5000, (D(2026, 3, 1) + dt.timedelta(days=12 + i)).isoformat(), match="exact")
        store.conn.execute("UPDATE ema_checks SET trace_payment_date=? WHERE check_no=?",
                           ((D(2026, 3, 1) + dt.timedelta(days=10 + i)).isoformat(), f"T{i:03d}"))
    store.conn.commit()
    tat = ec.turnaround(store.conn)
    assert tat[BCBS]["svc_p95"] == 33 and tat[BCBS]["n"] == 25          # 10 to 34 days: the 95th percentile is 33
    assert tat["UMR"]["svc_p95"] == 45                                   # one bill: the default
    assert ec.turnaround_days(store.conn, BCBS) == 33 and ec.turnaround_days(store.conn, "Nobody") == 45
    assert ec.turnaround_text(BCBS, 33) == f"{BCBS} answers 95% of claims within 33 days"
    lag = ec.posting_lag(store.conn)
    assert lag[BCBS] == 2 and ec._meta(store.conn, ec.LAG_KEY)["default_p95"] == 2


# ================================================================================ G3 outcomes, C6 paid in EMA, C2 written off
def test_a_later_payment_no_835_carries_closes_the_item(store, ledger, tmp_path):
    tk = denial_world(store, ledger, tmp_path, [pay(F.link_no(1), "PAPER-1", 90, posted=D(2026, 4, 20))])
    f = finding(tk)
    o = outcomes(store, [f])["F1"]
    assert (o["link"], o["anchor"], o["anchor_basis"]) == (F.link_no(1), "2026-03-31", "own_posting")
    assert o["outcome"] == "paid_after" and o["paid_after_same"] == 9000 and o["outcome_on"] == "2026-04-20"
    got = ec.paid_in_ema(store.conn, [f], {"F1": o})["F1"]
    assert got["action"] == "close" and got["paid_cents"] == 9000 and got["used"] == ("PAPER-1", "2026-04-20", F.link_no(1), "2026-03-02", "99213")   # one line's payment
    assert got["note"] == (f"EMA shows $90.00 paid by {P['bcbs']} on 2026-04-20 (check ending PER1, postings through 2026-05-01). "
                           "Reopen it if that payment is for something else.")
    assert "PAPER-1" not in got["note"]


def test_another_payers_payment_is_a_hint_and_a_small_one_partial(store, ledger, tmp_path):
    tk = denial_world(store, ledger, tmp_path, [pay(F.link_no(1), "PAPER-2", 90, posted=D(2026, 4, 20), payer=P["commercial"])])
    f = finding(tk)
    o = outcomes(store, [f])
    assert o["F1"]["outcome"] == "paid_after_other"
    got = ec.paid_in_ema(store.conn, [f], o)["F1"]
    assert got["action"] == "hint_other_payer" and "another payer" in got["hint"]
    f2 = finding(tk, expected_cents=30000, shortfall_cents=30000)
    o2 = outcomes(store, [f2])
    assert ec.paid_in_ema(store.conn, [f2], o2)["F1"]["action"] == "hint_partial"


def test_a_later_payment_an_835_carries_is_the_archive_rules_job(store, ledger, tmp_path):
    tk = denial_world(store, ledger, tmp_path, [pay(F.link_no(1), "LATER835", 90, posted=D(2026, 4, 20))])
    remit(store, trace="LATER835", paid_on="2026-04-17", claims=[{"pcn": F.claim_id(1), "lines": [(S, "99213", 150, 90)]}])
    el.refresh(store, ledger, parts="B", log=Log())
    ec.classify_checks(store.conn)
    f = finding(tk)
    o = outcomes(store, [f])["F1"]
    assert o["paid_after_835"] == 9000 and o["paid_after_same"] == 0
    assert "F1" not in ec.paid_in_ema(store.conn, [f], {"F1": o})


def test_the_ascs_separate_payee_money_never_closes_a_professional_item(store, ledger, tmp_path):
    tk = denial_world(store, ledger, tmp_path, [pay(F.link_no(1), "ASC-1", 90, posted=D(2026, 4, 20))])
    store.conn.execute("UPDATE ema_checks SET cause='separate_payee' WHERE check_no='ASC-1'")
    store.conn.commit()
    f = finding(tk)
    o = outcomes(store, [f])["F1"]
    assert o["paid_after_same"] == 0 and o["outcome"] != "paid_after"
    assert "F1" not in ec.paid_in_ema(store.conn, [f], {"F1": o})
    # and a facility link is never the item's line
    store.conn.execute("UPDATE ema_link_map SET facility = 1")
    store.conn.commit()
    assert outcomes(store, [f])["F1"]["outcome"] == "not_found"


def test_a_denial_paid_before_it_and_paid_again_into_credit_counts_no_recovery(store, ledger, tmp_path):
    """EMA shows the line paid $90 by the same payer before the denial, and $90 again after it, which left the line in
    credit. The later payment is a duplicate the payer may take back (the credit item lists it): closing the denial must
    not also count it as recovered (12 closures, $322, on real data). A line not in credit is an ordinary closure."""
    tk = denial_world(store, ledger, tmp_path, [pay(F.link_no(1), "PRIM-0", 90, posted=D(2026, 3, 10)),
                                                pay(F.link_no(1), "PAPER-1", 90, posted=D(2026, 4, 20))])
    f = finding(tk)
    o = outcomes(store, [f])["F1"]
    assert o["paid_before"] == 9000 and o["paid_after_same"] == 9000 and o["balance"] < -100, (o["paid_before"], o["balance"])
    got = ec.paid_in_ema(store.conn, [f], {"F1": o})["F1"]
    assert got["action"] == "close" and got["duplicate"] and got["recover_cents"] == 0
    assert got["note"].startswith("Already paid: EMA shows this line paid $90.00 by ") and "duplicate" in got["note"]
    plain = ec.paid_in_ema(store.conn, [f], {"F1": dict(o, balance=0)})["F1"]
    assert plain["action"] == "close" and not plain.get("duplicate") and plain.get("recover_cents") is None


def test_a_status_alone_never_closes_and_a_payment_before_is_a_hint(store, ledger, tmp_path):
    tk = denial_world(store, ledger, tmp_path, [pay(F.link_no(1), "PRIM-1", 90, posted=D(2026, 3, 20), payer=P["medicare"])])
    f = finding(tk)
    o = outcomes(store, [f])["F1"]
    assert o["status"] == "Resolved/Paid" and o["outcome"] == "paid_before" and o["paid_before"] == 9000
    got = ec.paid_in_ema(store.conn, [f], {"F1": o})["F1"]
    assert got["action"] == "hint_paid_before" and "before this denial" in got["hint"] and got["used"] is None


def test_a_downcode_closes_only_on_95_percent_of_its_shortfall(store, ledger, tmp_path):
    keep(store, F.claim_id(1))
    tk = remit(store, trace=F.check_no(1), paid_on="2026-03-28", claims=[{"pcn": F.claim_id(1), "lines": [(S, "99213", 150, 60)]}])
    rows = [charge(F.link_no(1)), pay(F.link_no(1), F.check_no(1), 60), pay(F.link_no(1), "PAPER-3", 30, posted=D(2026, 4, 20)),
            adj(F.link_no(1), "CO-45", 60, contractual=True),
            charge(F.link_no(99), cpt="99212", patient=3, svc=D(2026, 3, 20), posted=D(2026, 5, 1))]
    load(store, ledger, tmp_path, rows)
    ec.classify_checks(store.conn)
    f = finding(tk, signal="DOWNCODE_SVC06", category="downcoding", carc="45", paid_cents=6000, expected_cents=9500,
                shortfall_cents=3500)
    o = outcomes(store, [f])
    assert ec.paid_in_ema(store.conn, [f], o)["F1"]["action"] == "hint_partial"      # $30 of a $35 shortfall: under 95%
    f2 = dict(f, shortfall_cents=3100)
    o2 = outcomes(store, [f2])
    assert ec.paid_in_ema(store.conn, [f2], o2)["F1"]["action"] == "close"


def test_write_offs_are_capped_netted_and_never_added_to_the_era_figure(store, ledger, tmp_path):
    tk = denial_world(store, ledger, tmp_path)
    f = finding(tk)
    o = outcomes(store, [f])
    w = ec.written_off_in_ema(store.conn, [f], o)["F1"]
    assert w["written_off_cents"] == 9000 and w["written_off_source"] == "production"    # $150 capped at the $90 shortfall
    assert w["written_off_kind"] == "noncontractual" and w["written_off_when"] == "at_posting"
    assert w["written_off_on"] == "2026-03-31"
    f2 = finding(tk, written_off_cents=9500, written_off_on="2026-04-02")             # the ERA lines' figure is larger: kept
    w2 = ec.written_off_in_ema(store.conn, [f2], outcomes(store, [f2]))["F1"]
    assert w2["written_off_cents"] == 9500 and w2["written_off_source"] == "era_lines" and w2["written_off_on"] == "2026-04-02"


def test_a_reversed_write_off_nets_out_and_a_shared_line_marks_nothing(store, ledger, tmp_path):
    tk = denial_world(store, ledger, tmp_path, [adj(F.link_no(1), "CO-197", -150, posted=D(2026, 4, 10))])
    f = finding(tk)
    o = outcomes(store, [f])
    assert o["F1"]["written_off"] == 0 and "F1" not in ec.written_off_in_ema(store.conn, [f], o)
    tk2 = tk
    o2 = outcomes(store, [finding(tk2)], code_once=lambda f: False)
    assert o2["F1"]["outcome"] == "shared_line" and "F1" not in ec.written_off_in_ema(store.conn, [finding(tk2)], o2)


def test_an_underpayment_counts_its_own_reason_and_the_contractual_reduction(store, ledger, tmp_path):
    keep(store, F.claim_id(1))
    tk = remit(store, trace=F.check_no(1), paid_on="2026-03-28", claims=[{"pcn": F.claim_id(1), "lines": [(S, "99213", 150, 60)]}])
    rows = [charge(F.link_no(1)), pay(F.link_no(1), F.check_no(1), 60), adj(F.link_no(1), "CO-45", 90, contractual=True),
            charge(F.link_no(99), cpt="99212", patient=3, svc=D(2026, 3, 20), posted=D(2026, 5, 1))]
    load(store, ledger, tmp_path, rows)
    ec.classify_checks(store.conn)
    f = finding(tk, signal="UNDERPAID_VS_OWN_RATE", category="underpayment", carc=None, paid_cents=6000, expected_cents=8000,
                shortfall_cents=2000)
    o = outcomes(store, [f])["F1"]
    assert o["outcome"] == "contractual_at_posting" and o["contractual_routine"] == 2000
    assert o["pattern"] == f"{BCBS}|99213||CO-45"                                 # the owner's pattern names the routine reason
    assert "F1" not in ec.written_off_in_ema(store.conn, [f], {"F1": o})          # a routine reason is no write-off mark
    f2 = dict(f, carc="45")                                                       # ...unless it is the item's own reason
    w = ec.written_off_in_ema(store.conn, [f2], outcomes(store, [f2]))["F1"]
    assert w["written_off_cents"] == 2000 and w["written_off_kind"] == "contractual"


def test_an_automatic_write_off_and_its_evidence(store, ledger, tmp_path):
    tk = denial_world(store, ledger, tmp_path)
    f = finding(tk)
    o = outcomes(store, [f])["F1"]
    assert o["outcome"] == "auto_written_off" and o["evidence_level"] == "posting_day" and o["outcome_on"] == "2026-03-31"
    store.conn.execute("""CREATE TABLE IF NOT EXISTS line_handling (id INTEGER PRIMARY KEY AUTOINCREMENT, line_key TEXT, claim_id TEXT,
                          cpt TEXT, service_date TEXT, carc TEXT, rarc TEXT, adjustment_status TEXT, adjustment_subtype TEXT,
                          adjustments_cents INTEGER, payments_cents INTEGER, transaction_date TEXT, ema_source TEXT)""")
    store.conn.execute("INSERT INTO line_handling (line_key, carc, adjustment_status, adjustments_cents) VALUES ('LK-1', 'CO-197', 'WRITE_OFF', 15000)")
    store.conn.commit()
    assert outcomes(store, [f])["F1"]["evidence_level"] == "rule_proven"


def test_a_write_off_emas_export_says_its_rule_ignored_is_not_an_automatic_write_off(store, ledger, tmp_path):
    """EMA's ERA-lines export has a row for the line's reason and it reads IGNORE (no WRITE_OFF): EMA's posting rule left the
    adjustment alone, so the same-day write-off was someone's, not the rule's. Written off at posting, evidence
    'rule_ignored', never 'auto written off, inferred from the posting date'."""
    tk = denial_world(store, ledger, tmp_path)
    f = finding(tk)
    store.conn.execute("""CREATE TABLE IF NOT EXISTS line_handling (id INTEGER PRIMARY KEY AUTOINCREMENT, line_key TEXT, claim_id TEXT,
                          cpt TEXT, service_date TEXT, carc TEXT, rarc TEXT, adjustment_status TEXT, adjustment_subtype TEXT,
                          adjustments_cents INTEGER, payments_cents INTEGER, transaction_date TEXT, ema_source TEXT)""")
    store.conn.execute("INSERT INTO line_handling (line_key, carc, adjustment_status, adjustments_cents) VALUES ('LK-1', 'CO-197', 'IGNORE', 15000)")
    store.conn.commit()
    o = outcomes(store, [f])["F1"]
    assert o["evidence_level"] == "rule_ignored" and o["outcome"] == "written_off_at_posting", (o["outcome"], o["evidence_level"])


def test_written_off_later_while_pending_and_lost_to_timely_filing(store, ledger, tmp_path):
    tk = denial_world(store, ledger, tmp_path, [adj(F.link_no(1), "CO-197", -150, posted=D(2026, 4, 1)),
                                                 adj(F.link_no(1), "CO-197", 150, posted=D(2026, 4, 15))])
    f = finding(tk)
    o = outcomes(store, [f])["F1"]
    assert o["outcome"] == "written_off_later" and o["written_off_when"] == "later"
    # a CO-16 (information missing) written off the day it posted: written off while the payer had not finished
    o2 = outcomes(store, [finding(tk, carc="16")])
    assert o2["F1"]["outcome"] in ("written_off_later", "written_off_while_pending")


def test_the_pending_and_timely_filing_labels(store, ledger, tmp_path):
    tk = remit(store, trace=F.check_no(1), paid_on="2026-03-28",
               claims=[{"pcn": F.claim_id(1), "lines": [(S, "99213", 150, 0, [("CO", "16", 150)])]}])
    rows = [charge(F.link_no(1)), pay(F.link_no(1), F.check_no(1), 0), adj(F.link_no(1), "CO-16", 150),
            charge(F.link_no(2), patient=1), pay(F.link_no(2), "880000077", 0, patient=1),
            adj(F.link_no(2), "CO-29", 150, patient=1, posted=D(2026, 4, 25)),
            charge(F.link_no(99), cpt="99212", patient=3, svc=D(2026, 3, 20), posted=D(2026, 5, 1))]
    tk2 = remit(store, trace="880000077", paid_on="2026-03-28", claims=[{"pcn": F.claim_id(2), "last": "SAMPLE", "first": "BRAVO",
                                                                         "lines": [(S, "99213", 150, 0, [("CO", "16", 150)])]}])
    load(store, ledger, tmp_path, rows)
    ec.classify_checks(store.conn)
    f1 = finding(tk, carc="16")
    f2 = finding(tk2, finding_id="F2", patient_control_number=F.claim_id(2), carc="16")
    o = outcomes(store, [f1, f2])
    assert o["F1"]["outcome"] == "written_off_while_pending"
    assert o["F2"]["outcome"] == "lost_to_timely_filing"


def test_patient_money_after_a_co_denial_is_moved_to_the_patient_unless_an_835_moved_it(store, ledger, tmp_path):
    tk = denial_world(store, ledger, tmp_path, [patient_pay(40, posted=D(2026, 4, 15))])
    f = finding(tk)
    o = outcomes(store, [f])["F1"]
    assert o["outcome"] == "patient" and o["patient_after"] == 4000
    assert ec.written_off_in_ema(store.conn, [f], {"F1": o})["F1"]["written_off_kind"] == "moved_to_patient"
    # an 835 after the denial put the amount on the patient (PR): EMA followed the payer, so it is not "moved"
    remit(store, trace="REPROC1", paid_on="2026-04-10", claims=[{"pcn": F.claim_id(1), "lines": [(S, "99213", 150, 0, [("PR", "1", 90)])]}])
    assert outcomes(store, [f])["F1"]["outcome"] != "patient"
    # patient money never touches an underpayment
    f3 = finding(tk, signal="UNDERPAID_VS_OWN_RATE", category="underpayment", carc=None, paid_cents=1000)
    assert outcomes(store, [f3])["F1"]["outcome"] != "patient"


def test_an_accepted_pattern_reads_written_off_by_policy(store, ledger, tmp_path):
    tk = denial_world(store, ledger, tmp_path)
    f = finding(tk)
    o = outcomes(store, [f])["F1"]
    assert o["pattern"] == f"{BCBS}|99213||CO-197"
    pats = ec.policy_summary(store.conn, [f], {"F1": o}, today=TODAY)
    assert pats[0]["pattern"] == o["pattern"] and pats[0]["auto"] == 1 and pats[0]["decision"] == "pursue"
    ec.set_policy(store.conn, o["pattern"], "accept", "owner")
    assert outcomes(store, [f])["F1"]["outcome"] == "written_off_by_policy"
    with pytest.raises(ValueError):
        ec.set_policy(store.conn, o["pattern"], "ignore", "owner")


def test_outside_productions_dates_the_outcome_is_not_covered(store, ledger, tmp_path):
    tk = denial_world(store, ledger, tmp_path)
    f = finding(tk, service_date="2025-11-03")
    assert outcomes(store, [f])["F1"]["outcome"] == "not_covered"


# ================================================================================================ C5: denials only EMA saw
def c5_rows(link=2, patient=1, reason="CO-197", amount=150, cpt="99213", payer=P["commercial"], posted=D(2026, 4, 10), **kw):
    return [charge(F.link_no(link), cpt=cpt, amount=amount, patient=patient, payer=payer, primary=payer, **kw),
            adj(F.link_no(link), reason, amount, cpt=cpt, patient=patient, posted=posted, payer=payer, primary=payer, **kw)]


def c5(store, **kw):
    return ec.ema_denials(store.conn, today=TODAY, rate=kw.get("rate"), ratio=kw.get("ratio"),
                          has_835_finding=kw.get("has_835_finding"))


def test_a_denial_with_no_remittance_becomes_an_item(store, ledger, tmp_path):
    denial_world(store, ledger, tmp_path, c5_rows())
    items = c5(store, rate=lambda g, c, y: 7000)
    assert [i["key_parts"] for i in items] == [(F.link_no(2), "auth")]
    it = items[0]
    assert it["signal"] == "EMA_DENIED" and it["category"] == "denial" and it["carc"] == "197" and it["source"] == "ema"
    assert it["expected_cents"] == 7000 and it["claim"]["patient_control_number"] is None
    # deadline: EMA's posting (Apr 10) less the default 8-day lag, plus the 180-day default window
    assert it["appeal_deadline"] == "2026-09-29" and "less its usual delay of 8 days" in it["deadline_basis"]
    assert it["title"] == "Denied in EMA (CO-197: no authorization), no remittance on file: 99213 on 2026-03-02"
    assert it["evidence"]["lines"] == [["99213", S, 15000, "CO-197", "2026-04-10", 0]]
    # with no usual rate: EMA's allowed, else the ratio, else 45% of the charge, capped at the charge
    assert c5(store)[0]["expected_cents"] == 6750
    assert c5(store, ratio=lambda g, c: 0.6)[0]["expected_cents"] == 9000


def test_no_ema_only_item_where_an_835_line_claim_denial_or_item_exists(store, ledger, tmp_path):
    # claim 2 is tied to its bill by the A/R; its 835 denied the whole claim (status 4), for another code
    remit(store, trace=F.check_no(2), paid_on="2026-03-28", status="4",
          claims=[{"pcn": F.claim_id(2), "last": "SAMPLE", "first": "BRAVO", "lines": [(S, "17000", 150, 0)]}])
    store.conn.execute("INSERT INTO ema_claim_links (claim_id, link, bill, first_seen, last_seen) VALUES (?,?,?,?,?)",
                       (F.claim_id(2), F.link_no(2), F.bill_id(2), "2026-03-30", "2026-03-30"))
    store.conn.commit()
    denial_world(store, ledger, tmp_path, [charge(F.link_no(2), cpt="11102", patient=1),
                                           adj(F.link_no(2), "CO-197", 150, cpt="11102", patient=1, posted=D(2026, 4, 2))])
    assert c5(store) == []          # link 1: its bill's 835 has the line; link 2: the 835 denied the whole claim that day
    store.conn.execute("UPDATE claims SET claim_status_code='1' WHERE patient_control_number=?", (F.claim_id(2),))
    store.conn.commit()
    items = c5(store)
    assert [i["key_parts"][0] for i in items] == [F.link_no(2)] and items[0]["claim"]["patient_control_number"] == F.claim_id(2)
    assert c5(store, has_835_finding=lambda b, s, c: b == F.bill_id(2)) == []     # an 835 item covers it


def test_ema_only_denials_leave_out_policy_codes_products_patient_rows_voids_and_reversals(store, ledger, tmp_path):
    rows = (c5_rows(link=2, cpt="G2211") + c5_rows(link=3, cpt="99024") + c5_rows(link=4, amount=1)
            + c5_rows(link=5, reason="OA-209") + c5_rows(link=6, status="Inactive/Voided")
            + c5_rows(link=7) + [adj(F.link_no(7), "CO-197", -150, patient=1, posted=D(2026, 4, 12), payer=P["commercial"])]
            + c5_rows(link=8, amount=4))
    denial_world(store, ledger, tmp_path, rows)
    assert c5(store) == []


def test_an_unmapped_claim_whose_patient_day_and_code_an_835_carries_is_left_to_the_835(store, ledger, tmp_path):
    rows = c5_rows(link=2, patient=1)
    denial_world(store, ledger, tmp_path, rows)
    assert len(c5(store)) == 1
    remit(store, trace="CORRECTED", paid_on="2026-04-20", claims=[{"pcn": F.claim_id(77), "last": "SAMPLE", "first": "BRAVO",
                                                                    "lines": [(S, "99213", 150, 0, [("CO", "197", 150)])]}])
    assert c5(store) == []


def test_a_second_payers_denial_is_sized_at_what_was_left(store, ledger, tmp_path):
    rows = [charge(F.link_no(2), patient=1, primary=P["medicare"], secondary=P["commercial"]),
            pay(F.link_no(2), "MCR-1", 100, patient=1, payer=P["medicare"], posted=D(2026, 3, 20)),
            adj(F.link_no(2), "CO-45", 20, patient=1, payer=P["medicare"], posted=D(2026, 3, 20), contractual=True),
            adj(F.link_no(2), "CO-197", 30, patient=1, payer=P["commercial"], posted=D(2026, 4, 10))]
    denial_world(store, ledger, tmp_path, rows)
    it = c5(store, rate=lambda g, c, y: 9000)[0]
    assert it["expected_cents"] == 3000 and "after the first payer's payment" in it["evidence"]["expected_basis"][0]


def test_a_denial_paid_since_is_no_item_and_an_ar_denial_with_no_reason_is(store, ledger, tmp_path):
    rows = c5_rows(link=2) + [pay(F.link_no(2), "PAPER-4", 90, patient=1, payer=P["commercial"], posted=D(2026, 4, 25))]
    ar = [F.ar_row(claim=F.claim_id(3), link=F.link_no(3), status="Denied", denial=120, current=0, svc=SVC, patient=2,
                   primary=P["commercial"])]
    denial_world(store, ledger, tmp_path, rows + [charge(F.link_no(3), patient=2, amount=120, payer=P["commercial"],
                                                         primary=P["commercial"], status="Denied")], ar_rows=ar)
    items = c5(store)
    assert [(i["key_parts"], i["evidence"]["lines"][0][3]) for i in items] == [((F.link_no(3), "other"), "A/R Denied")]
    assert items[0]["claim"]["patient_control_number"] == F.claim_id(3)
    assert items[0]["heir_keys"] == [("EMA_OPEN_CLAIM", F.claim_id(3))]


def test_the_denying_payers_payment_posted_the_same_day_reads_as_paid_since(store, ledger, tmp_path):
    """The denying payer paid the line on the posting that carried the adjustment: it was paid, not denied (C6 counts that
    payment as paid too, so the item would close the run it opened). Another payer's payment that day is still a payment
    before the denial: the denial stays an item, sized at what was left."""
    rows = c5_rows(link=2) + [pay(F.link_no(2), "PAPER-5", 90, patient=1, payer=P["commercial"], posted=D(2026, 4, 10))]
    rows += c5_rows(link=3, patient=2) + [pay(F.link_no(3), "MCR-9", 90, patient=2, payer=P["medicare"], posted=D(2026, 4, 10))]
    denial_world(store, ledger, tmp_path, rows)
    items = c5(store, rate=lambda g, c, y: 7000)
    assert [i["key_parts"][0] for i in items] == [F.link_no(3)]
    assert items[0]["expected_cents"] == 6000 and "after the first payer's payment" in items[0]["evidence"]["expected_basis"][0]


def test_a_denial_paid_on_another_claim_for_the_same_patient_date_and_code_is_no_item(store, ledger, tmp_path):
    """A resubmission under a new EMA claim was paid: the denial on the first claim is not money to chase. The ASC's
    facility claim for the same patient, date and code never pays the professional line."""
    paid_again = [charge(F.link_no(3), patient=1, payer=P["commercial"], primary=P["commercial"]),
                  pay(F.link_no(3), "PAPER-6", 90, patient=1, payer=P["commercial"], posted=D(2026, 4, 25))]
    denial_world(store, ledger, tmp_path, c5_rows(link=2) + paid_again)
    assert [i for i in c5(store, rate=lambda g, c, y: 7000) if i["key_parts"][0] == F.link_no(2)] == []


def test_a_payment_on_the_ascs_facility_claim_never_reads_as_paid_on_another_claim(store, ledger, tmp_path):
    ar = [F.ar_row(claim=F.claim_id(3, facility=True), link=F.link_no(3), status="Paid", denial=0, current=0, patient=1,
                   primary=P["commercial"])]
    facility = [charge(F.link_no(3), patient=1, payer=P["commercial"], primary=P["commercial"]),
                pay(F.link_no(3), "PAPER-7", 90, patient=1, payer=P["commercial"], posted=D(2026, 4, 25))]
    denial_world(store, ledger, tmp_path, c5_rows(link=2) + facility, ar_rows=ar)
    assert [i["key_parts"][0] for i in c5(store, rate=lambda g, c, y: 7000)] == [F.link_no(2)]


def test_an_item_whose_service_ema_shows_paid_on_another_claim_gets_a_hint(store, ledger, tmp_path):
    """The 835 denied claim 1's 99213 and EMA shows the same patient, date and code paid $90 on another of its claims (a
    resubmission). paid_in_ema read only the item's own line, so the item said nothing (6 work-now items on real data): it
    now carries a hint, never a closure. A hint already on the item stays, and the ASC's facility claim never counts."""
    again = [charge(F.link_no(3)), pay(F.link_no(3), "PAPER-33", 90, posted=D(2026, 4, 20))]
    tk = denial_world(store, ledger, tmp_path, again)
    f = finding(tk)
    o = outcomes(store, [f])
    assert o["F1"]["outcome"] not in ec.PAID_OUTCOMES and not o["F1"]["paid_after_same"]
    got = ec.paid_in_ema(store.conn, [f], o)["F1"]
    assert (got["action"], got["paid_cents"], got["used"]) == ("hint_paid_elsewhere", 9000, None)
    assert got["hint"].startswith(f"EMA shows $90.00 paid by {BCBS} on another of its claims for this patient, date and code")
    assert "F1" not in ec.paid_in_ema(store.conn, [dict(f, hint="Possibly paid on claim X")], o)
    assert "F1" not in ec.paid_in_ema(store.conn, [dict(f, expected_cents=30000)], o)        # under half: no hint
    store.conn.execute("INSERT OR REPLACE INTO ema_link_map (link, method, facility) VALUES (?, 'test', 1)", (F.link_no(3),))
    store.conn.commit()
    assert "F1" not in ec.paid_in_ema(store.conn, [f], o)


def test_a_claim_never_sent_whose_service_its_payer_paid_on_another_claim_is_not_listed(store, ledger, tmp_path):
    """EMA claim 4 sits 'Batched for Printing' while its payer paid the same patient, date and code on EMA claim 14: the
    service was billed and paid there, nothing is stuck (3 NOT_SENT items on real data). Another payer's payment on the
    other claim leaves the item listed, with a hint to check it."""
    kw = dict(patient=3, status="Batched for Printing")
    ar = [F.ar_row(claim=F.claim_id(4), link=F.link_no(4), status="Batched for Printing", current=150, aging=None, patient=3, svc=SVC)]
    rows = [charge(F.link_no(4), **kw), charge(F.link_no(14), patient=3),
            pay(F.link_no(14), "PAPER-44", 90, patient=3, posted=D(2026, 4, 10))]
    denial_world(store, ledger, tmp_path, rows, ar_rows=ar)
    assert [p for p in ec.not_sent(store.conn, today="2026-04-25") if p["key_parts"] == (F.claim_id(4),)] == []
    store.conn.execute("UPDATE ema_line_payments SET payer = ?, payer_group = 'Testcare' WHERE link = ?", (P["commercial"], F.link_no(14)))
    store.conn.commit()
    p = next(x for x in ec.not_sent(store.conn, today="2026-04-25") if x["key_parts"] == (F.claim_id(4),))
    assert p["hint"].startswith("EMA shows $90.00 paid by Testcare on another of its claims for this patient, date and code")


def test_an_unanswered_claim_whose_service_its_payer_paid_on_another_claim_is_not_listed(store, ledger, tmp_path):
    rows = [charge(F.link_no(1), status="Payment Pending", posted=D(2026, 3, 3)),
            charge(F.link_no(11), status="Resolved/Paid", posted=D(2026, 3, 20)),
            pay(F.link_no(11), "PAPER-11", 90, posted=D(2026, 4, 1)),                                 # claim 1's service, paid
            charge(F.link_no(99), cpt="99212", patient=3, svc=D(2026, 3, 20), posted=D(2026, 5, 1)),
            pay(F.link_no(98), "PAPER-5", 20, cpt="99212", patient=3, posted=D(2026, 4, 28))]
    load(store, ledger, tmp_path, rows)
    assert ec.unanswered_claims(store.conn, today=TODAY) == []
    store.conn.execute("UPDATE ema_line_payments SET payer = ?, payer_group = 'Testcare' WHERE link = ?", (P["commercial"], F.link_no(11)))
    store.conn.commit()
    items = ec.unanswered_claims(store.conn, today=TODAY)
    assert [i["key_parts"] for i in items] == [(f"link:{F.link_no(1)}",)]
    assert items[0]["hint"].startswith("EMA shows $90.00 paid by Testcare on another of its claims")


def test_the_ascs_facility_claim_is_titled_as_such(store, ledger, tmp_path):
    ar = [F.ar_row(claim=F.claim_id(2, facility=True), link=F.link_no(2), status="Denied", denial=0, current=150, patient=1,
                   primary=P["commercial"])]
    denial_world(store, ledger, tmp_path, c5_rows(link=2, reason="OA-274"), ar_rows=ar)
    it = c5(store)[0]
    assert it["title"].startswith("ASC facility claim: denied in EMA (OA-274") and it["evidence"]["facility"] is True


# ============================================================================================ C7 and C8
def test_denials_posted_as_contractual_are_one_aggregate_item(store, ledger, tmp_path):
    rows = []
    for i in range(10):
        rows += [charge(F.link_no(10 + i), cpt="G2211", amount=110, patient=i % 4),
                 adj(F.link_no(10 + i), "CO-97", 110, cpt="G2211", patient=i % 4, contractual=True)]
    denial_world(store, ledger, tmp_path, rows)
    items = ec.denial_as_contractual(store.conn, today=TODAY)
    assert len(items) == 1
    it = items[0]
    assert it["signal"] == "EMA_DENIAL_AS_CONTRACTUAL" and it["key_parts"] == (BCBS, "CO-97", "2026")
    assert it["expected_cents"] is None and it["shortfall_cents"] is None and it["category"] == "contract"
    assert it["evidence"]["written_off_cents"] == 110000 and it["evidence"]["lines"] == 10
    assert it["evidence"]["split_cents"] == {"c5": 110000} and it["evidence"]["top_codes"] == [["G2211", 110000]]
    # nine lines are not enough
    store.conn.execute("DELETE FROM ema_line_reasons WHERE link=?", (F.link_no(19),))
    store.conn.commit()
    assert ec.denial_as_contractual(store.conn, today=TODAY) == []


def test_credits_count_negative_denial_amounts_and_medicares_60_days(store, ledger, tmp_path):
    ar = [F.ar_row(claim=F.claim_id(1), link=F.link_no(1), current=-25, denial=0, aging=D(2026, 9, 1), primary=P["medicare"]),
          F.ar_row(claim=F.claim_id(2), link=F.link_no(2), current=0, denial=-80, aging=D(2026, 11, 10), primary=P["bcbs"]),
          F.ar_row(party="Patient", claim=None, current=-12, aging=D(2026, 8, 1)),
          F.ar_row(claim=F.claim_id(3), link=F.link_no(3), current=40, denial=40)]     # both amounts, equal: never added
    load(store, ledger, tmp_path, [], ar)
    cr = ec.credits(store.conn)
    assert cr["insurance"] == {"n": 2, "cents": -10500} and cr["patient"] == {"n": 1, "cents": -1200}
    assert cr["medicare_primary_over60"] == {"n": 1, "cents": -2500} and cr["ok"] is False
    rows = list(ec.credits_csv(store.conn))
    assert rows[0] == ["initials", "service_date", "code", "amount", "age_days", "party", "coverage", "payer_group"]
    assert all(r[0] in ("AT", "") for r in rows[1:]) and len(rows) == 4
    assert one(store.conn, "SELECT exposure_cents FROM ema_ar WHERE claim_id=?", F.claim_id(3)) == (4000,)


# ================================================================================================= W2, W3, W4
def test_overlays_show_the_outcome_first_and_work_never_from_a_posting_rule(store, ledger, tmp_path):
    tk = denial_world(store, ledger, tmp_path, [adj(F.link_no(1), "CO-16", 10, posted=D(2026, 4, 20))])
    f = finding(tk)
    o = outcomes(store, [f])
    ov = ec.overlays(store.conn, [f], o)["F1"]
    assert ov["ema_link"] == F.link_no(1) and ov["ema_status"] == "Resolved/Paid" and ov["ema_status_src"] == "production"
    assert ov["ema_outcome"] == o["F1"]["outcome"] and ov["ema_as_of"] == "2026-05-01"
    assert (ov["ema_worked_on"], ov["ema_worked_how"]) == ("2026-04-20", "posting")      # a person's later adjustment
    tk2 = tk
    store.conn.execute("DELETE FROM ema_line_reasons WHERE code='16'")
    store.conn.commit()
    ov2 = ec.overlays(store.conn, [finding(tk2)], outcomes(store, [finding(tk2)]))["F1"]
    assert ov2["ema_worked_on"] is None          # written off the day the remittance posted: EMA's rule, not work


def test_overlays_read_the_ars_worked_marks_and_a_later_submission(store, ledger, tmp_path):
    ar1 = [F.ar_row(claim=F.claim_id(1), link=F.link_no(1), status="Denied", denial=150, current=0, aging=D(2026, 4, 1),
                    svc=SVC)]
    tk = denial_world(store, ledger, tmp_path, ar_rows=ar1)
    p = F.write(str(tmp_path / "ar_b.xlsx"), F.ar_xlsx([dict(ar1[0], **{"Last Billing Note": F.NOTES[0], "Aging Date": D(2026, 4, 5)})]))
    assert er.ingest_file(store, p, via="test", log=Log())["status"] == "current"
    store.conn.execute("INSERT INTO ema_claim_links (claim_id, link, bill, first_seen, last_seen) VALUES (?,?,?,?,?)",
                       (F.claim_id(1, sub=2), F.link_no(1), F.bill_id(1), "2026-04-08", "2026-04-08"))
    store.conn.commit()
    f = finding(tk)
    ov = ec.overlays(store.conn, [f], outcomes(store, [f]))["F1"]
    assert (ov["ema_worked_on"], ov["ema_worked_how"]) == ("2026-04-08", "resubmitted")
    assert F.NOTES[0] not in json.dumps(ov)


def test_unworked_equals_its_filter(store):
    from remitwatch import detect
    detect.ensure_schema(store.conn)
    have = {r[1] for r in store.conn.execute("PRAGMA table_info(findings)")}
    for col in ("ema_worked_on", "written_off_when"):          # columns the wiring adds to findings; added here when missing
        if col not in have:
            store.conn.execute(f"ALTER TABLE findings ADD COLUMN {col} TEXT")
    rows = [("A", "denial", "open", None, None, 1000, None), ("B", "denial", "open", "2026-05-01", None, 2000, None),
            ("C", "bundling", "open", None, "2026-01-01", 3000, None), ("D", "denial", "in_progress", None, None, 4000, None),
            ("E", "fees", "open", None, None, 5000, None), ("F", "denial", "open", "2026-03-01", None, 6000, "2026-12-01"),
            ("G", "underpayment", "open", None, None, 7000, None)]
    for fid, cat, st, worked, deadline, cents, deadline2 in rows:
        store.conn.execute("""INSERT INTO findings (finding_id, category, status, ema_worked_on, appeal_deadline, shortfall_cents, stale,
                              written_off_cents) VALUES (?,?,?,?,?,?,0,?)""", (fid, cat, st, worked, deadline2 or deadline, cents,
                                                                               500 if fid == "G" else None))
    store.conn.commit()
    cols = [r[1] for r in store.conn.execute("PRAGMA table_info(findings)")]
    fs = [dict(zip(cols, r)) for r in store.conn.execute("SELECT * FROM findings")]
    got = ec.unworked(store.conn, fs, data_through="2026-05-10")
    sql, args = ec.unworked_sql("2026-05-10")
    n, cents = store.conn.execute(f"SELECT COUNT(*), SUM(shortfall_cents) FROM findings WHERE {sql}", args).fetchone()
    assert (got["n"], got["cents"]) == (n, cents) == (3, 1000 + 6000 + 7000)
    assert got["written_off"]["n"] == 1 and ec._meta(store.conn, ec.UNWORKED_KEY)["n"] == 3


def test_ema_vouches_for_a_fax_only_with_fresh_postings_and_an_open_line(store, ledger, tmp_path):
    """W5: Production fresh, the line tied to its EMA claim, nothing paid after the remittance, and the line on a recent
    A/R or carrying a balance. An item EMA alone saw is always held."""
    ar = [F.ar_row(claim=F.claim_id(1), link=F.link_no(1), status="Denied", denial=150, current=0, aging=D(2026, 4, 28), svc=SVC)]
    tk = denial_world(store, ledger, tmp_path, ar_rows=ar)
    f = finding(tk)
    o = outcomes(store, [f])
    v = ec.fax_vouch(store.conn, [f], o, today=TODAY)["F1"]
    assert v["vouched"] and v["code"] is None and "2026-05-01" in v["message"]       # on the A/R of Apr 28, balance $0
    assert ec.fax_vouch(store.conn, [f], o, today="2026-05-20")["F1"]["code"] == "production_old"
    assert ec.fax_vouch(store.conn, [f], {"F1": dict(o["F1"], paid_after_other=500)}, today=TODAY)["F1"]["code"] == "paid_after"
    assert ec.fax_vouch(store.conn, [f], {"F1": dict(o["F1"], link=None)}, today=TODAY)["F1"]["code"] == "not_mapped"
    # past the A/R's 8 days: a line EMA wrote off is still unpaid and still vouched for; a line closed some other way is not
    assert o["F1"]["outcome"] == "auto_written_off" and ec.fax_vouch(store.conn, [f], o, today="2026-05-07")["F1"]["vouched"]
    closed = {"F1": dict(o["F1"], outcome="closed_unexplained")}
    assert ec.fax_vouch(store.conn, [f], closed, today="2026-05-07")["F1"]["code"] == "no_balance"
    assert ec.fax_vouch(store.conn, [f], {"F1": dict(closed["F1"], balance=2500)}, today="2026-05-07")["F1"]["vouched"]
    d = ec.fax_vouch(store.conn, [dict(f, signal="EMA_DENIED")], o, today=TODAY)["F1"]
    assert d == {"vouched": False, "code": "no_remittance", "message": ec.NO_REMITTANCE_MESSAGE}


def test_the_ar_accounted_for_adds_up_to_its_exposure(store, ledger, tmp_path):
    ar = [F.ar_row(claim=F.claim_id(1), link=F.link_no(1), status="Payment Pending", current=100, aging=D(2026, 4, 25)),
          F.ar_row(claim=F.claim_id(2), link=F.link_no(2), status="Denied", denial=80, current=0, aging=D(2026, 4, 25), patient=1),
          F.ar_row(claim=F.claim_id(3), link=F.link_no(3), status="Denied", denial=70, current=0, aging=D(2026, 4, 25), patient=2),
          F.ar_row(claim=F.claim_id(4), link=F.link_no(4), status="Batched for Printing", current=60, aging=None, patient=3),
          F.ar_row(claim=F.claim_id(5), link=F.link_no(5), status="Rejected", current=50, aging=D(2026, 4, 25)),
          F.ar_row(claim=F.claim_id(6), link=F.link_no(6), status="Payment Pending", current=-30, aging=D(2026, 4, 25)),
          F.ar_row(claim=None, link=None, status="-", current=25, aging=None, patient=1),
          F.ar_row(claim=F.claim_id(8, facility=True), link=F.link_no(8), status="Payment Pending", current=400, aging=D(2026, 4, 25)),
          F.ar_row(claim=F.claim_id(9), link=F.link_no(9), status="Resolved/Paid", current=15, aging=D(2026, 4, 25)),
          F.ar_row(claim=F.claim_id(10), link=F.link_no(10), status="Payment Pending", current=90, aging=D(2026, 4, 25)),
          F.ar_row(party="Patient", claim=None, current=33, aging=D(2026, 4, 25))]
    load(store, ledger, tmp_path, [], ar)
    fs = [{"finding_id": "X", "signal": "DENIED_AUTH", "status": "in_progress", "patient_control_number": F.claim_id(2),
           "service_date": "2026-11-10", "procedure_code": "99213", "line_key": "L"},
          {"finding_id": "Y", "signal": "NO_ANSWER", "status": "open", "patient_control_number": F.claim_id(10)}]
    acc = ec.ar_accounted(store.conn, fs)
    b = {k: (v["n"], v["cents"]) for k, v in acc["buckets"].items() if v["n"]}
    assert acc["adds_up"] and acc["total"] == {"n": 10, "cents": 100 * (100 + 80 + 70 + 60 + 50 - 30 + 25 + 400 + 15 + 90)}
    assert b == {"waiting": (1, 10000), "denied_started": (1, 8000), "denied_no_item": (1, 7000), "not_sent": (2, 11000),
                 "credits": (1, -3000), "unbilled": (1, 2500), "facility": (1, 40000), "other": (1, 1500), "chase": (1, 9000)}
    page = ec.ar_list(store.conn, "not_sent")
    assert page["count"] == 2 and {r["status"] for r in page["rows"]} == {"Batched for Printing", "Rejected"}
    assert "initials" not in json.dumps(page) and ec._meta(store.conn, ec.AR_ACCOUNTED_KEY)["adds_up"] is True


# ========================================================================================= reconcile, health, claim view
def test_reconcile_summary_health_and_the_claim_block_hold_no_names(store, ledger, tmp_path):
    ar = [F.ar_row(claim=F.claim_id(1), link=F.link_no(1), status="Denied", denial=150, current=0, note=F.NOTES[1])]
    tk = denial_world(store, ledger, tmp_path, c5_rows(), ar_rows=ar)
    ec.posting_lag(store.conn)
    ec.turnaround(store.conn)
    ec.unposted_remits(store.conn, today=TODAY)
    f = finding(tk)
    o = outcomes(store, [f])
    out = {"reconcile": ec.reconcile_summary(store.conn, today=TODAY), "health": ec.data_health(store.conn, today=TODAY),
           "block": ec.claim_block(store.conn, claim_id=F.claim_id(1)), "items": c5(store), "outcomes": o,
           "overlays": ec.overlays(store.conn, [f], o), "csv": list(ec.checks_csv(store.conn)) + list(ec.credits_csv(store.conn))}
    text = json.dumps(out, default=str)
    for secret in F.SECRET_STRINGS + F.SECRET_DATES:
        assert secret not in text, secret
    assert "://" not in text.replace(ec.er.claim_url_template(store.conn) or "~", "")
    block = out["block"]
    assert block["links"] == [F.link_no(1)] and block["ar"][0]["has_note"] == 1 and block["postings_note"]
    assert block["lines"][0]["payments"][0]["remittance"] == "835"
    keys = {h["key"] for h in out["health"]}
    assert {"ema_reports_missing", "ema_ar_stale", "ema_production_stale", "ema_report_held", "ema_columns", "ema_map",
            "ema_link_conflicts", "remits_missing", "credits", "ema_status_words"} <= keys
    rec = out["reconcile"]
    assert rec["freshness"]["production_as_of"] == "2026-05-01" and rec["c4"]["fresh"] is True


def test_the_claim_block_shows_the_ledgers_postings_on_a_server(store, ledger, tmp_path):
    denial_world(store, ledger, tmp_path)
    block = ec.claim_block(store.conn, bill=F.bill_id(1), ledger=ledger)
    assert block["postings_note"] is None and block["postings"] and all("name_key" not in p and "person" not in p for p in block["postings"])


# ================================================================================================= efficiency
def test_the_outcome_lookups_use_their_indexes(store, ledger, tmp_path, monkeypatch):
    """The per-item lookups go through temporary key tables and the EMA tables' keys: never a scan of an EMA table per
    item (the compact store of a serverless host keeps only these indexes)."""
    tk = denial_world(store, ledger, tmp_path, c5_rows())
    seen: list[str] = []
    store.conn.set_trace_callback(seen.append)
    monkeypatch.setattr(ec, "_drop", lambda conn, *names: None)      # keep the key tables to read the plans afterwards
    outcomes(store, [finding(tk), finding(tk, finding_id="F2", line_key=None, evidence=json.dumps({"unpaid": [["99213", S]]}))])
    store.conn.set_trace_callback(None)
    plans = []
    for sql in seen:
        s = sql.strip()
        if s.upper().startswith("SELECT") and "temp.ec_" in s and ("ema_lines" in s or "ema_line_payments" in s or "ema_line_reasons" in s):
            plans += [r[3] for r in store.conn.execute("EXPLAIN QUERY PLAN " + s).fetchall()]
    assert plans
    for p in plans:
        for t in ("ema_lines", "ema_line_payments", "ema_line_reasons", "ema_link_map", "ema_checks"):
            assert not (p.startswith("SCAN") and t in p.split()), p


# ================================================================================================= both modes
def _strip_times(x):
    """The same answers with the clock-time stamps the checks write to meta left out."""
    if isinstance(x, dict):
        return {k: _strip_times(v) for k, v in x.items() if k not in ("at", "computed_at")}
    if isinstance(x, list):
        return [_strip_times(v) for v in x]
    return x


def _answers(store, ledger, tmp_path):
    """Every check run on one world: a denial EMA shows paid later with no 835, a denial only EMA saw, a claim never
    sent, and the A/R."""
    ec._LAST_POST.clear()
    ar = [F.ar_row(claim=F.claim_id(1), link=F.link_no(1), status="Denied", denial=150, current=0, aging=D(2026, 4, 25), svc=SVC),
          F.ar_row(claim=F.claim_id(4), link=F.link_no(4), status="Batched for Printing", current=60, aging=None, patient=3, svc=SVC),
          F.ar_row(claim=F.claim_id(6), link=F.link_no(6), status="Payment Pending", current=-30, aging=D(2026, 4, 25), svc=SVC)]
    later = [pay(F.link_no(1), F.check_no(5), 150, posted=D(2026, 4, 20))]
    tk = denial_world(store, ledger, tmp_path, c5_rows() + later, ar_rows=ar)
    c = store.conn
    f = finding(tk)
    out = {"lag": ec.posting_lag(c), "turnaround": ec.turnaround(c, today=TODAY)}
    o = outcomes(store, [f])
    out.update(outcomes=o, paid=ec.paid_in_ema(c, [f], o), written_off=ec.written_off_in_ema(c, [f], o),
               overlays=ec.overlays(c, [f], o, today=TODAY), denied=c5(store, rate=lambda g, code, y: 7000),
               not_sent=ec.not_sent(c, today=TODAY), unposted=ec.unposted_remits(c, today=TODAY),
               accounted=ec.ar_accounted(c, [f], today=TODAY, save=False), credits=ec.credits(c, today=TODAY),
               block=ec.claim_block(c, claim_id=F.claim_id(1)), c1=ec.c1_summary(c))
    return _strip_times(json.loads(json.dumps(out, default=str)))


def test_a_serverless_compact_store_gives_the_same_answers_as_a_server(store, ledger, tmp_path, monkeypatch):
    """A serverless host holds the store in memory with few secondary indexes: every check must give the same answer."""
    server = _answers(store, ledger, tmp_path)
    assert server["paid"]["F1"]["action"] == "close" and server["denied"] and server["not_sent"]
    monkeypatch.setenv("REMITWATCH_MODE", "serverless")
    monkeypatch.setenv("REMITWATCH_EMA_REPORTS", "all")
    mem = Store(":memory:", compact=True)
    er.ensure_schema(mem.conn)
    mem.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('ema:key_salt', ?)", (SALT_HEX,))
    mem.conn.commit()
    lg2 = el.Ledger.open(str(tmp_path / "ledger_serverless.sqlite"))
    try:
        sub = tmp_path / "serverless"
        sub.mkdir()
        assert _answers(mem, lg2, sub) == server
    finally:
        lg2.close()
        mem.close()


# ======================================================================================== the line outcome: review fixes
def test_a_payment_the_items_own_remittance_took_back_is_not_paid_before(store, ledger, tmp_path):
    """Blue Cross paid the line $90 on trace 1, then re-adjudicated it on trace 2 (the item's own remittance): it took the
    $90 back and denied the line. EMA posted both. Nothing paid before the denial still stands: no paid-before outcome,
    no hint about a duplicate, and the write-off at posting reads as what it is."""
    remit(store, trace=F.check_no(1), paid_on="2026-03-10", claims=[{"pcn": F.claim_id(1), "lines": [(S, "99213", 150, 90)]}])
    tk2 = remit(store, trace=F.check_no(2), paid_on="2026-03-28",
                claims=[{"pcn": F.claim_id(1), "lines": [(S, "99213", 150, 0, [("CO", "197", 150)])], "status": "4"}])
    rows = [charge(F.link_no(1)),
            pay(F.link_no(1), F.check_no(1), 90, posted=D(2026, 3, 12)),
            adj(F.link_no(1), "CO-45", 60, contractual=True, posted=D(2026, 3, 12)),
            pay(F.link_no(1), F.check_no(2), -90, posted=D(2026, 3, 31)),             # the takeback, under the item's own trace
            adj(F.link_no(1), "CO-45", -60, contractual=True, posted=D(2026, 3, 31)),
            adj(F.link_no(1), "CO-197", 150, posted=D(2026, 3, 31)),
            charge(F.link_no(99), cpt="99212", patient=3, svc=D(2026, 3, 20), posted=D(2026, 5, 1), status="Payment Pending")]
    load(store, ledger, tmp_path, rows)
    ec.classify_checks(store.conn)
    f = finding(tk2)
    o = outcomes(store, [f])["F1"]
    got = ec.paid_in_ema(store.conn, [f], {"F1": o}).get("F1") or {}
    assert o["paid_before"] == 0 and o["taken_back"] == 9000 and o["paid_before_first"] is None
    assert o["outcome"] in ("auto_written_off", "written_off_at_posting") and got.get("action") != "hint_paid_before"


def test_a_takeback_after_the_denial_cancels_what_was_paid_before_it_or_after_it(store, ledger, tmp_path):
    """A takeback posted after the item cancels a later payment first (a payment taken back never closes an item), and
    otherwise the payment made before the item (so it no longer reads as paid before)."""
    tk = denial_world(store, ledger, tmp_path, [
        pay(F.link_no(1), "PRIM-1", 90, posted=D(2026, 3, 20), payer=P["medicare"]),       # before the denial
        pay(F.link_no(1), "PRIM-2", -90, posted=D(2026, 4, 12), payer=P["medicare"])])     # taken back after it
    f = finding(tk)
    o = outcomes(store, [f])["F1"]
    assert o["paid_before"] == 0 and o["taken_back"] == 9000 and o["outcome"] != "paid_before"
    assert ec.paid_in_ema(store.conn, [f], {"F1": o}).get("F1", {}).get("action") != "hint_paid_before"


def test_a_later_payment_taken_back_never_closes_the_item(store, ledger, tmp_path):
    tk = denial_world(store, ledger, tmp_path, [pay(F.link_no(1), "PAPER-1", 150, posted=D(2026, 4, 10)),
                                                 pay(F.link_no(1), "PAPER-2", -150, posted=D(2026, 4, 20))])
    f = finding(tk)
    o = outcomes(store, [f])["F1"]
    assert o["paid_after_same"] == 0 and o["paid_before"] == 0 and o["taken_back"] == 0
    assert "F1" not in ec.paid_in_ema(store.conn, [f], {"F1": o})


def test_an_items_own_payment_posted_under_a_mistyped_number_never_closes_it(store, ledger, tmp_path):
    """One 835 pays claim 2 $50 and claim 1 $60; claim 1 has an underpayment item ($20 short). EMA posted claim 2 under
    the trace and claim 1's $60 under a mistyped number. C1 pairs that check with the 835's missing part (misnumbered),
    so C4 does not call the 835 posted short, and the $60 is the item's own payment, never a later one."""
    keep(store, F.claim_id(1))
    tk = remit(store, trace=F.check_no(1), paid_on="2026-03-28",
               claims=[{"pcn": F.claim_id(1), "lines": [(S, "99213", 150, 60)]},
                       {"pcn": F.claim_id(2), "last": "SAMPLE", "first": "BRAVO", "lines": [(S, "99213", 150, 50)]}])
    remit(store, trace=F.check_no(99), paid_on="2026-04-28", claims=[{"pcn": F.claim_id(99), "last": "FIXTURE", "first": "DELTA",
                                                                       "lines": [("2026-03-20", "99212", 90, 40)]}])
    rows = [charge(F.link_no(1)), pay(F.link_no(1), "88O000001", 60, posted=D(2026, 4, 1)),        # letter O for zero
            adj(F.link_no(1), "CO-45", 90, contractual=True, posted=D(2026, 4, 1)),
            charge(F.link_no(2), patient=1), pay(F.link_no(2), F.check_no(1), 50, patient=1, posted=D(2026, 3, 31)),
            charge(F.link_no(99), cpt="99212", patient=3, svc=D(2026, 3, 20), posted=D(2026, 5, 1)),
            pay(F.link_no(99), F.check_no(99), 40, cpt="99212", patient=3, svc=D(2026, 3, 20), posted=D(2026, 4, 30))]
    load(store, ledger, tmp_path, rows)
    out = ec.classify_checks(store.conn)
    assert one(store.conn, "SELECT match, cause, pair_trace FROM ema_checks WHERE check_no='88O000001'") == ("none", "misnumbered", tk)
    assert out["pairs_in_part"] == 1
    assert [i for i in ec.unposted_remits(store.conn, today=TODAY) if i["key_parts"] == (tk,)] == []
    f = finding(tk, signal="UNDERPAID_VS_OWN_RATE", category="underpayment", carc=None, paid_cents=6000, expected_cents=8000,
                shortfall_cents=2000)
    o = outcomes(store, [f])["F1"]
    assert o["anchor_basis"] == "own_posting" and o["paid_after_same"] == 0
    assert ec.paid_in_ema(store.conn, [f], {"F1": o}).get("F1", {}).get("action") != "close"


def test_an_own_payment_found_only_by_its_amount_never_closes_an_underpayment(store, ledger, tmp_path):
    """EMA keyed the item's own $60 under a mistyped number and another payer's name, so C1 cannot pair it: an EMA-only
    payment of exactly the item's own paid amount, within the usual posting delay, is taken for the item's own."""
    keep(store, F.claim_id(1))
    tk = remit(store, trace=F.check_no(1), paid_on="2026-03-28", claims=[{"pcn": F.claim_id(1), "lines": [(S, "99213", 150, 60)]}])
    rows = [charge(F.link_no(1)), pay(F.link_no(1), "88O000001", 60, posted=D(2026, 4, 1), payer=P["commercial"]),
            adj(F.link_no(1), "CO-45", 90, contractual=True, posted=D(2026, 4, 1)),
            charge(F.link_no(99), cpt="99212", patient=3, svc=D(2026, 3, 20), posted=D(2026, 5, 1))]
    load(store, ledger, tmp_path, rows)
    ec.classify_checks(store.conn)
    f = finding(tk, signal="UNDERPAID_VS_OWN_RATE", category="underpayment", carc=None, paid_cents=6000, expected_cents=8000,
                shortfall_cents=2000)
    o = outcomes(store, [f])["F1"]
    assert o["anchor_basis"] == "remit_plus_lag" and o.get("own_by_amount") and o["paid_after_same"] + o["paid_after_other"] == 0
    assert "F1" not in ec.paid_in_ema(store.conn, [f], {"F1": o})


def test_a_payment_under_a_check_a_spreadsheet_damaged_never_closes_an_item(store, ledger, tmp_path):
    tk = denial_world(store, ledger, tmp_path, [pay(F.link_no(1), "9.00001E+11", 150, posted=D(2026, 4, 10))])
    f = finding(tk)
    o = outcomes(store, [f])["F1"]
    assert o["paid_after_same"] == 0 and o["paid_after_835"] == 15000
    assert "F1" not in ec.paid_in_ema(store.conn, [f], {"F1": o})


def test_the_items_own_payer_under_emas_name_is_learned_from_checks_that_match_its_traces(store, ledger, tmp_path):
    """The 835 names the payer UMR; EMA posts its checks under a Wausau plan, which groups apart. Three checks EMA posted
    under exactly the payer's own trace numbers teach that the two are one payer: a later payment under EMA's name closes
    the item instead of reading as another payer's."""
    keep(store, F.claim_id(1))
    tk = remit(store, trace=F.check_no(1), paid_on="2026-03-28", payer="UMR TEST",
               claims=[{"pcn": F.claim_id(1), "lines": [(S, "99213", 150, 0, [("CO", "197", 150)])]}])
    ema = "Wausau Test Plan"
    rows = [charge(F.link_no(1)), pay(F.link_no(1), F.check_no(1), 0, posted=D(2026, 3, 31), payer=ema),
            adj(F.link_no(1), "CO-197", 150, posted=D(2026, 3, 31), payer=ema),
            pay(F.link_no(1), "PAPER-9", 150, posted=D(2026, 4, 20), payer=ema),
            charge(F.link_no(99), cpt="99212", patient=3, svc=D(2026, 3, 20), posted=D(2026, 5, 1))]
    for i in range(2, 5):
        remit(store, trace=F.check_no(i), paid_on="2026-03-14", payer="UMR TEST",
              claims=[{"pcn": F.claim_id(10 + i), "last": "SAMPLE", "first": "BRAVO", "lines": [(S, "99213", 150, 50)]}])
        rows += [charge(F.link_no(10 + i), patient=1), pay(F.link_no(10 + i), F.check_no(i), 50, patient=1, payer=ema,
                                                           posted=D(2026, 3, 17))]
    load(store, ledger, tmp_path, rows)
    f = finding(tk, payer_group="UMR", payer_name="UMR TEST")
    assert not ec._same_payer(er.payer_group_of(store.conn, ema)[0], "UMR")
    store.conn.execute("DELETE FROM meta WHERE key=?", (ec.EQUIV_KEY,))
    before = ec.paid_in_ema(store.conn, [f], outcomes(store, [f]))["F1"]
    assert before["action"] == "hint_other_payer"
    ec.classify_checks(store.conn)
    assert ec._meta(store.conn, ec.EQUIV_KEY) == {"UMR TEST": [er.payer_group_of(store.conn, ema)[0]]}
    after = ec.paid_in_ema(store.conn, [f], outcomes(store, [f]))["F1"]
    assert after["action"] == "close" and "check ending" in after["note"]


def test_a_payer_seen_on_fewer_than_three_matched_checks_is_not_learned(store):
    remit(store, trace="T1", paid_on="2026-03-14", payer="UMR TEST", claims=[{"pcn": F.claim_id(1), "lines": [(S, "99213", 150, 50)]}])
    store.conn.execute("""INSERT INTO ema_checks (check_no, payer, payer_group, match, trace_key, first_posted, ins_paid)
                          SELECT 'T1', 'Wausau Test Plan', 'UHC Integrated Services (Wausau)', 'exact', transaction_key, '2026-03-17', 5000
                          FROM transactions""")
    store.conn.commit()
    assert ec.learn_payer_equivalence(store.conn) == {}


# ================================================================================================ C5 and C2: review fixes
def test_an_ema_denial_is_dated_from_its_own_reason_not_an_earlier_reversed_one(store, ledger, tmp_path):
    """CO-16 posted January 15 and reversed February 1 (the claim was corrected), then CO-197 posted April 10. The item is
    the CO-197 denial: its window counts from April 10 less the posting lag, never from January."""
    payer = "Humana Medicare Advantage Test"
    rows = [charge(F.link_no(2), patient=1, payer=payer, primary=payer),
            adj(F.link_no(2), "CO-16", 150, patient=1, posted=D(2026, 1, 15), payer=payer, primary=payer),
            adj(F.link_no(2), "CO-16", -150, patient=1, posted=D(2026, 2, 1), payer=payer, primary=payer),
            adj(F.link_no(2), "CO-197", 150, patient=1, posted=D(2026, 4, 10), payer=payer, primary=payer)]
    denial_world(store, ledger, tmp_path, rows)
    items = [i for i in c5(store, rate=lambda g, c, y: 7000) if i["key_parts"][0] == F.link_no(2)]
    assert len(items) == 1 and items[0]["evidence"]["first_denial_posted"] == "2026-04-10"
    lag = items[0]["evidence"]["posting_lag_days"]
    assert items[0]["appeal_deadline"] > ec._add("2026-04-10", -lag)
    f = dict(finding(None, finding_id="E1"), signal="EMA_DENIED", ema_link=F.link_no(2), payment_date=None,
             evidence=json.dumps(items[0]["evidence"]))
    assert outcomes(store, [f])["E1"]["anchor"] == "2026-04-10"


def test_a_payment_before_the_denial_never_reads_as_paid_since(store, ledger, tmp_path):
    payer = "Humana Medicare Advantage Test"
    rows = [charge(F.link_no(2), patient=1, payer=payer, primary=payer),
            adj(F.link_no(2), "CO-16", 150, patient=1, posted=D(2026, 1, 15), payer=payer, primary=payer),
            adj(F.link_no(2), "CO-16", -150, patient=1, posted=D(2026, 2, 1), payer=payer, primary=payer),
            pay(F.link_no(2), "SUPP0001", 70, patient=1, posted=D(2026, 3, 1), payer=payer, primary=payer),
            adj(F.link_no(2), "CO-197", 150, patient=1, posted=D(2026, 4, 10), payer=payer, primary=payer)]
    denial_world(store, ledger, tmp_path, rows)
    assert [i for i in c5(store, rate=lambda g, c, y: 7000) if i["key_parts"][0] == F.link_no(2)]


def test_a_write_off_on_an_item_with_no_shortfall_is_capped_by_its_value(store, ledger, tmp_path):
    tk = denial_world(store, ledger, tmp_path)
    f = finding(tk, shortfall_cents=None, expected_cents=4000)
    w = ec.written_off_in_ema(store.conn, [f], outcomes(store, [f]))["F1"]
    assert (w["written_off_cents"], w["written_off_basis"]) == (4000, "expected")
    f2 = finding(tk, shortfall_cents=None, expected_cents=None)                   # an item detect could not price
    w2 = ec.written_off_in_ema(store.conn, [f2], outcomes(store, [f2]))["F1"]
    assert (w2["written_off_cents"], w2["written_off_basis"]) == (15000, "charge_less_paid")
    f3 = finding(tk, shortfall_cents=None, expected_cents=None, paid_cents=10000)  # part of the charge was paid already
    assert ec.written_off_in_ema(store.conn, [f3], outcomes(store, [f3]))["F1"]["written_off_cents"] == 5000


# ============================================================================================ C3a and C4: review fixes
def test_a_same_day_ar_status_wins_the_tie_over_the_claim_status_export(store):
    """The A/R (as_of 2026-11-20) says Denied; a Claim & Claim Status export made the same day at 09:00 says Payment
    Pending. A tie is decided by the day: the A/R first, as documented."""
    c = store.conn
    cid = F.claim_id(7)
    c.execute("INSERT INTO ema_ar (file_id, row_no, as_of, svc_year, claim_id, link, claim_status, status_class, party) "
              "VALUES ('AR1', 1, '2026-11-20', '2026', ?, ?, 'Denied', 'denied', 'Insurance')", (cid, F.link_no(7)))
    c.execute("INSERT INTO ema_sources (source_name, sha256, family, created_at, rows, recorded_at) "
              "VALUES ('status.xlsx', 'SHA1', 'claim_status', '2026-11-20T09:00:00Z', 1, '2026-11-20T10:00:00Z')")
    c.execute("INSERT INTO ema_rows (row_fingerprint, family, source_sha256, claim_id, claim_status) "
              "VALUES ('R1', 'claim_status', 'SHA1', ?, 'Payment Pending')", (cid,))
    c.commit()
    st = ec.claim_statuses(c, claim_ids=[cid])[cid]
    assert (st["source"], st["status"]) == ("ar", "Denied")
    ov = ec.overlays(c, [finding(None, patient_control_number=cid, ema_link=F.link_no(7))], {})
    assert ov["F1"]["ema_status"] == "Denied" and ov["F1"]["ema_status_src"] == "ar"
    c.execute("UPDATE ema_sources SET created_at='2026-11-21T08:00:00Z'")       # a day later, the export is newer
    c.commit()
    assert ec.claim_statuses(c, claim_ids=[cid])[cid]["source"] == "export"


def test_an_835_emas_spreadsheet_damaged_check_may_hold_is_never_called_unposted(store, ledger, tmp_path):
    """The practice saved Production in Excel: five 12-digit EFT traces EMA posted read 9.00000E+11 and the like. C4 does
    not call those payments unposted (it counts them apart); a trace whose digits cannot be the damaged number is."""
    tks = []
    rows = []
    for i in range(1, 6):
        trace = f"9{i:011d}"
        tks.append(remit(store, trace=trace, paid_on="2026-03-28", claims=[{"pcn": F.claim_id(i), "lines": [(S, "99213", 150, 80)]}]))
        rows += [charge(F.link_no(i)), pay(F.link_no(i), f"{float(trace):.5E}", 80)]
    other = remit(store, trace="777000000001", paid_on="2026-03-28", claims=[{"pcn": F.claim_id(9), "lines": [(S, "99213", 150, 80)]}])
    rows += [charge(F.link_no(99), cpt="99212", patient=3, svc=D(2026, 3, 20), posted=D(2026, 5, 1))]
    p = F.write(str(tmp_path / "prod.csv"), F.production_csv(rows))
    assert er.ingest_file(store, p, via="test", ledger=ledger, log=Log())["status"] == "current"      # five: under H7's floor
    el.refresh(store, ledger, parts="AB", log=Log())
    ec.classify_checks(store.conn)
    items = ec.unposted_remits(store.conn, today=TODAY)
    assert [i["key_parts"] for i in items] == [(other,)]
    summary = ec._meta(store.conn, ec.UNPOSTED_KEY)
    assert summary["damaged_n"] == 5 and summary["damaged_cents"] == 5 * 8000
    assert ec._sci_could_be("9.00001E+11", "900001234567") and not ec._sci_could_be("9.00001E+11", "900011234567")
    assert not ec._sci_could_be("9.00001E+11", "90000123456") and not ec._sci_could_be("ABC", "900001234567")


# =========================================================================================== the dashboard's copy
def serving_copy(store, path, monkeypatch):
    """The dashboard's copy as cloud.build_serving makes it with the EMA tables wired in (needs_ema_reports): every serving
    table, the EMA tables of ema_reports.serving_tables (no keys, only the lines the pages show) and the meta filter
    that leaves out the key salt."""
    from remitwatch import cloud, detect

    detect.ensure_schema(store.conn)
    orig_empty = cloud.empty_serving

    def empty_with_ema(p):
        orig_empty(p)
        import sqlite3
        c = sqlite3.connect(p)
        er.ensure_schema(c)
        c.close()
    monkeypatch.setattr(cloud, "empty_serving", empty_with_ema)
    tables = ([t for t in cloud.SERVING_TABLES if t[0] != "meta"] + er.serving_tables(store.conn)
              + [("meta", None, er.SERVING_META_FILTER)])
    monkeypatch.setattr(cloud, "SERVING_TABLES", tables)
    cloud.build_serving(store.conn, str(path))


def serving_world(store, ledger, tmp_path):
    """A denial with a later payment no 835 carries, a denial only EMA saw, an 835 EMA never posted, an A/R line still
    open in EMA that insurance paid under the patient's other EMA claim, a credit, and an item on file."""
    ar = [F.ar_row(claim=F.claim_id(1), link=F.link_no(1), status="Denied", denial=150, current=0, aging=D(2026, 4, 25), svc=SVC),
          F.ar_row(claim=F.claim_id(4), link=F.link_no(4), status="Batched for Printing", current=60, aging=None, patient=3, svc=SVC),
          F.ar_row(claim=F.claim_id(6), link=F.link_no(6), status="Payment Pending", current=-30, aging=D(2026, 4, 25), svc=SVC),
          F.ar_row(claim=F.claim_id(11), link=F.link_no(11), status="Payment Pending", current=100, aging=D(2026, 4, 25),
                   patient=2, svc=SVC)]
    extra = [pay(F.link_no(1), F.check_no(5), 150, posted=D(2026, 4, 20)),
             charge(F.link_no(12), patient=2), pay(F.link_no(12), "PAPER-12", 80, patient=2, posted=D(2026, 4, 2)),
             charge(F.link_no(13), patient=1), pay(F.link_no(13), F.check_no(7), 70, patient=1, posted=D(2026, 3, 31))]
    remit(store, trace="UNPOSTED1", paid_on="2026-03-28", claims=[{"pcn": F.claim_id(30), "last": "SAMPLE", "first": "BRAVO",
                                                                    "lines": [(S, "99213", 150, 80)]}])
    remit(store, trace=F.check_no(7), paid_on="2026-03-28", claims=[{"pcn": F.claim_id(13), "last": "SAMPLE", "first": "BRAVO",
                                                                      "lines": [(S, "99213", 150, 70)]}])
    keep(store, F.claim_id(1))
    tk = denial_world(store, ledger, tmp_path, c5_rows() + extra, ar_rows=ar)
    c = store.conn
    f = finding(tk)
    ec.posting_lag(c)
    ec.turnaround(c, today=TODAY)
    ec.unposted_remits(c, today=TODAY)
    return f


def page_answers(conn, f, o):
    """What every page and CSV route asks of ema_checks and ema_reports (needs_ema_checks section 4)."""
    out = {"c1": ec.c1_summary(conn, "2026-01-01", "2026-12-31"), "c1_all": ec.c1_summary(conn),
           "reconcile": ec.reconcile_summary(conn, today=TODAY, save=False),
           "ar": {b: ec.ar_list(conn, b, 1) for b in ec.AR_BUCKETS}, "unposted": list(ec.unposted_csv(conn, today=TODAY)),
           "checks": list(ec.checks_csv(conn)), "credits": ec.credits(conn, today=TODAY), "credits_csv": list(ec.credits_csv(conn)),
           "health": ec.data_health(conn, today=TODAY), "freshness": ec.freshness(conn, TODAY),
           "block": ec.claim_block(conn, claim_id=F.claim_id(1)), "vouch": ec.fax_vouch(conn, [f], o, today=TODAY),
           "outcomes": ec.line_outcomes(conn, [f], code_once=NO_CLOSE, today=TODAY),
           "catalog": {k: v for k, v in er.catalog(conn).items() if k != "files"}, "coverage": er.coverage(conn)}
    return _strip_times(json.loads(json.dumps(out, default=str)))


def test_every_page_reads_the_same_on_the_dashboard_copy_as_on_the_store(store, ledger, tmp_path, monkeypatch):
    """On a serverless host the pages read the dashboard's copy, which holds no keys, only the checks and lines the pages
    show, and no key salt. Every figure must still be the store's: the ones the copy cannot work out are what detection
    stored (meta ema:reconcile and ema:c1_cells, ema:unposted's rows, ema_ar.bucket). And no page writes to the copy."""
    import hashlib
    import sqlite3

    f = serving_world(store, ledger, tmp_path)
    c = store.conn
    o = outcomes(store, [f])
    live = {b: ec.ar_list(c, b, 1) for b in ec.AR_BUCKETS}            # before detection stored the buckets: worked out
    assert live["paid_elsewhere"]["count"] == 1 and live["waiting"]["count"] == 0
    ec.ar_accounted(c, today=TODAY)                                    # detection stores the W4 buckets (the store's items) ...
    assert {b: ec.ar_list(c, b, 1) for b in ec.AR_BUCKETS} == live     # ... and the lists they open do not change
    ec.reconcile_summary(c, today=TODAY)                               # ... and the figures the copy reads
    store_side = page_answers(c, f, o)
    assert store_side["c1"]["matched_cents"] > 0 and store_side["unposted"][1:] and store_side["ar"]["paid_elsewhere"]["rows"]
    assert store_side["credits"]["production_not_on_ar"] is not None
    path = tmp_path / "serving.sqlite"
    serving_copy(store, path, monkeypatch)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    copy = sqlite3.connect(str(path))
    try:
        assert copy.execute("SELECT COUNT(*) FROM meta WHERE key='ema:key_salt'").fetchone()[0] == 0
        assert copy.execute("SELECT COUNT(*) FROM ema_ar WHERE name_key IS NOT NULL").fetchone()[0] == 0
        copy_side = page_answers(copy, f, o)
        copy.commit()
    finally:
        copy.close()
    assert copy_side == store_side
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before       # no page wrote to the copy (no salt, no schema)


def test_a_page_on_a_store_that_never_read_a_report_makes_no_salt(tmp_path):
    s = Store(str(tmp_path / "fresh.sqlite"))
    try:
        er.ensure_schema(s.conn)
        assert ec.c1_summary(s.conn)["matched_cents"] == 0 and ec.ar_list(s.conn, "waiting")["count"] == 0
        assert ec.reconcile_summary(s.conn, save=False) == {} and list(ec.unposted_csv(s.conn))[1:] == []
        ec.data_health(s.conn)
        assert s.conn.execute("SELECT COUNT(*) FROM meta WHERE key='ema:key_salt'").fetchone()[0] == 0
    finally:
        s.close()


def test_the_dashboard_copy_keeps_every_claim_number_of_a_bill_with_an_item(store, ledger, tmp_path, monkeypatch):
    """The map ties the claim number to the bill's first submission (C1); the item is on the second (C2) of the same
    bill. The copy keeps the claim number's lines all the same, so the item's EMA box is not empty on a serverless host."""
    import sqlite3

    f = serving_world(store, ledger, tmp_path)
    c = store.conn
    c.execute("DELETE FROM findings")
    c.execute("INSERT INTO findings (finding_id, signal, category, status, patient_control_number, stale) VALUES "
              "('C2', 'DENIED_AUTH', 'denial', 'open', ?, 0)", (F.claim_id(1, sub=2),))
    c.execute("DELETE FROM ema_claim_links")                         # no A/R tie either: only the bill joins them
    c.execute("DELETE FROM ema_ar")
    c.commit()
    assert one(c, "SELECT claim_id, bill FROM ema_link_map WHERE link=?", F.link_no(1)) == (F.claim_id(1), F.bill_id(1))
    path = tmp_path / "serving.sqlite"
    serving_copy(store, path, monkeypatch)
    copy = sqlite3.connect(str(path))
    try:
        assert copy.execute("SELECT COUNT(*) FROM ema_lines WHERE link=?", (F.link_no(1),)).fetchone()[0] == \
            c.execute("SELECT COUNT(*) FROM ema_lines WHERE link=?", (F.link_no(1),)).fetchone()[0] > 0
        assert copy.execute("SELECT COUNT(*) FROM ema_link_map WHERE link=?", (F.link_no(1),)).fetchone()[0] == 1
        assert copy.execute("SELECT COUNT(*) FROM ema_lines WHERE link=?", (F.link_no(12),)).fetchone()[0] == 0
    finally:
        copy.close()
    assert f["finding_id"] == "F1"


@pytest.mark.parametrize("claim", ["ZZ00TEST1C001", "ZZ00TEST1C1", "ZZ00TEST1C", "FZZ00TEST1C002", "ZZ00TEST1", "OTHER-77",
                                   "ZZ00TEST12345C12"])
def test_the_copys_bill_expression_agrees_with_bill_of_claim(claim):
    import sqlite3

    c = sqlite3.connect(":memory:")
    got = c.execute(f"SELECT x, {er.bill_sql('x')} FROM (SELECT ? AS x)", (claim,)).fetchone()
    assert er.bill_of_claim(claim) in got
