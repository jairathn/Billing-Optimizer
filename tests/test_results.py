"""
The Results tab (remitwatch.results, remitwatch.api_results): money returned on claims RemitWatch flagged or worked, split
by how strongly it can claim it, the lift against similar unworked items, ROI, work done, outcomes, hours saved, denials by
month, weekly snapshots, the baseline and the daily spot checks; counts equal the lists they open; both hosts.

Every row is invented: the 835 side is written straight into the store's remittance tables (tests/test_states.py's
builders), EMA's side straight into its summary tables. Each attribution test runs on the server's file store and on the
compact in-memory store a serverless run works on.
"""
from __future__ import annotations

import base64
import json
import random
import sqlite3

import pytest

from remitwatch import actions, detect, ema_reports as er, results, states
from remitwatch.state import Conflict, LocalState
from remitwatch.store import Store
from tests import ema_fixtures as F
from tests.test_states import BCBS, SALT_HEX, event, item, ln, new_store, remit

TODAY = "2026-06-30"
FLAG = "2026-04-01"
OWNER = "owner-secret-9876"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(er, "CLAIM_HOST_SUFFIX", F.HOST_SUFFIX)
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    for k in ("VERCEL", "REMITWATCH_STATE_DIR", "REMITWATCH_EMA_REPORTS", "DROPBOX_APP_KEY", "DROPBOX_REFRESH_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    states._RESPONSE_CACHE.clear()
    states._RECOVERY_CACHE.clear()


def compact_store() -> Store:
    """The store a serverless run works on: in memory, with the compact schema (most secondary indexes left out)."""
    s = Store(":memory:", compact=True)
    er.ensure_schema(s.conn)
    detect.ensure_schema(s.conn)
    s.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('ema:key_salt', ?)", (SALT_HEX,))
    s.conn.commit()
    return s


@pytest.fixture(params=["file", "compact"])
def store(request, tmp_path):
    s = new_store(str(tmp_path / "store.sqlite")) if request.param == "file" else compact_store()
    actions.ensure_fax_tables(s.conn)
    yield s
    s.close()


# ------------------------------------------------------------------------------------------------------------ builders
def denial(s, n=1, *, fid=None, flagged=FLAG, paid_on="2026-03-28", code="99213", charge=150, signal="DENIED_AUTH", **kw):
    """An item raised from its own 835 denial (claim n, submission 1), first flagged on `flagged`."""
    tk = remit(s, trace=F.check_no(n * 100), paid_on=paid_on,
               claims=[{"pcn": F.claim_id(n), "received": "2026-03-05", "lines": [ln(code, charge, 0, [("CO", "197", charge)])]}])
    fid = fid or f"F{n}"
    item(s, finding_id=fid, signal=signal, line_key=f"{tk}-0-0", claim_key=f"{tk}-0", transaction_key=tk, patient_control_number=F.claim_id(n),
         procedure_code=code, billed_code=code, charge_cents=charge * 100, first_seen=f"{flagged}T08:00:00+00:00", work_state=kw.pop("work_state", "work_now"),
         appeal_deadline=kw.pop("appeal_deadline", "2026-12-31"), **kw)
    return fid, tk


def paid(s, n=1, *, on, amount=90, sub=2, payer="BLUE CROSS TEST", code="99213", charge=150, status="1", plb=()):
    """A later 835 answer for the same service: submission `sub` of claim n's bill, paying `amount` dollars."""
    adj = [("CO", "45", charge - amount)] if amount < charge else []
    return remit(s, trace=F.check_no(n * 100 + sub), paid_on=on, payer=payer, plb=plb,
                 claims=[{"pcn": F.claim_id(n, sub=sub), "status": status, "received": "2026-04-02", "lines": [ln(code, charge, amount, adj)]}])


def fax(s, fid, day, *, status="sent", pages=3, actor="Biller One", direction="out"):
    actions.upsert_fax(s.conn, {"fax_key": f"{fid}-{day}-{direction}", "direction": direction, "finding_id": fid, "status": status,
                                "sent_at": f"{day}T12:00:00+00:00", "created_at": f"{day}T11:00:00+00:00", "updated_at": f"{day}T12:00:00+00:00",
                                "pages": pages, "doc": {"actor": actor}, "payer_group": BCBS, "purpose": "appeal", "level": 1})
    s.conn.commit()


def ema_line(s, link, *, bill, svc="2026-03-02", cpt="99213"):
    s.conn.execute("INSERT OR REPLACE INTO ema_lines (link, svc, cpt, bill, payer_group, charges) VALUES (?,?,?,?,?,?)",
                   (link, svc, cpt, bill, BCBS, 15000))
    s.conn.execute("INSERT OR REPLACE INTO ema_link_map (link, bill, claim_id, method, conflict, facility) VALUES (?,?,?,?,0,0)",
                   (link, bill, bill + "C001", "test"))
    s.conn.commit()


def ema_pay(s, link, *, posted, cents, check="555", in_835=0, svc="2026-03-02", cpt="99213", trace_key=None):
    s.conn.execute("""INSERT OR REPLACE INTO ema_line_payments (link, svc, cpt, check_no, posted, payer, payer_group, ins_paid, in_835,
                      trace_key) VALUES (?,?,?,?,?,?,?,?,?,?)""", (link, svc, cpt, check, posted, "Anthem", BCBS, cents, in_835, trace_key))
    s.conn.commit()


def run(s, today=TODAY, **kw):
    return results.refresh(s.conn, today=today, force=True, **kw)


def dash(s):
    return results.dashboard(s.conn)


def money_of(s, fid):
    lists = results._meta(s.conn, results.LISTS_KEY, {})
    return lists["detail"].get(fid)


# ================================================================================================ attribution rules
def test_a_payment_after_a_fax_inside_the_payers_answer_time_counts_as_after_work(store):
    fid, _tk = denial(store)
    fax(store, fid, "2026-04-05")
    paid(store, on="2026-04-20")
    run(store)
    m = dash(store)["money"]
    assert (m["returned_cents"], m["a_cents"], m["b_cents"], m["c_cents"]) == (9000, 9000, 0, 0)
    ev = money_of(store, fid)["events"][0]
    assert ev["bucket"] == "a" and ev["work_kind"] == "fax" and "15 days after the fax sent through RemitWatch" in ev["rule"]
    assert "45-day answer time" in ev["rule"]          # the payer's answer time (the default with few answers on file)
    assert dash(store)["money"]["median_days_work_to_money"] == 15


def test_a_payment_after_the_answer_time_or_with_no_work_counts_as_flagged_with_no_recorded_work(store):
    late, _ = denial(store, 1)
    fax(store, late, "2026-04-05")
    paid(store, 1, on="2026-06-10")             # 66 days after the fax: past the 45-day answer time
    none, _ = denial(store, 2)
    paid(store, 2, on="2026-04-20", amount=60)
    run(store)
    m = dash(store)["money"]
    assert (m["a_cents"], m["c_cents"], m["items"]) == (0, 15000, 2)
    assert {money_of(store, f)["events"][0]["bucket"] for f in (late, none)} == {"c"}
    assert "no work recorded in RemitWatch" in money_of(store, none)["events"][0]["rule"]


def test_a_status_marked_sent_and_a_logged_call_count_as_work_but_a_note_or_a_system_line_does_not(store):
    sent, _ = denial(store, 1)
    event(store, sent, "manual", at="2026-04-03T10:00:00+00:00", frm="open", to="appealed", actor="Biller Two")
    paid(store, 1, on="2026-04-25")
    called, _ = denial(store, 2)
    event(store, called, "call", at="2026-04-03T10:00:00+00:00", actor="Biller Two")
    paid(store, 2, on="2026-04-25")
    noted, _ = denial(store, 3)
    event(store, noted, "manual", at="2026-04-03T10:00:00+00:00", frm="open", to="in_progress", actor="Biller Two")
    event(store, noted, "paid_on_later_remit", at="2026-04-04T10:00:00+00:00", actor="system")
    event(store, noted, "packet", at="2026-04-04T10:00:00+00:00", actor="packets")
    paid(store, 3, on="2026-04-25")
    run(store)
    kinds = {f: money_of(store, f)["events"][0]["work_kind"] for f in (sent, called, noted)}
    assert kinds == {sent: "sent", called: "call", noted: None}
    assert money_of(store, noted)["events"][0]["bucket"] == "c"
    by = {r["key"]: r["cents"] for r in dash(store)["money"]["by_person"]}
    assert by == {"Biller Two": 18000}


def test_a_fax_that_did_not_go_out_is_not_work(store):
    fid, _ = denial(store)
    fax(store, fid, "2026-04-05", status="failed")
    fax(store, fid, "2026-04-06", status="sending")
    paid(store, on="2026-04-20")
    run(store)
    assert money_of(store, fid)["events"][0]["bucket"] == "c"
    assert dash(store)["work"]["faxes_sent"] == 0


def test_money_paid_before_the_first_flag_never_counts(store):
    fid, _ = denial(store, flagged="2026-05-01")
    paid(store, on="2026-04-20")                # paid before RemitWatch flagged it
    paid(store, on="2026-05-01", sub=3)         # paid on the flag day: not after it
    run(store)
    assert dash(store)["money"]["returned_cents"] == 0 and money_of(store, fid) is None
    paid(store, on="2026-05-09", sub=4, amount=20)      # paid after the flag: only this counts
    run(store)
    assert money_of(store, fid)["returned"] == 2000 and [e["date"] for e in money_of(store, fid)["events"]] == ["2026-05-09"]


def test_the_items_own_remittance_never_counts_even_when_its_date_is_after_the_flag(store):
    # an underpayment raised from a check whose date is two days after the run that raised it (an EFT date ahead)
    tk = remit(store, trace=F.check_no(1), paid_on="2026-04-03", claims=[{"pcn": F.claim_id(1), "lines": [ln("99213", 150, 50, [("CO", "45", 100)])]}])
    item(store, finding_id="U1", signal="UNDERPAID_VS_OWN_RATE", category="underpayment", line_key=f"{tk}-0-0", claim_key=f"{tk}-0",
         transaction_key=tk, first_seen="2026-04-01T08:00:00+00:00", work_state="work_now", shortfall_cents=4000)
    run(store)
    assert dash(store)["money"]["returned_cents"] == 0


def test_a_second_payer_billed_and_paid_after_the_flag_is_money_found(store):
    tk = remit(store, trace=F.check_no(1), paid_on="2026-03-28", claims=[{"pcn": F.claim_id(1), "lines": [ln("99213", 150, 80, [("PR", "2", 20), ("CO", "45", 50)])]}])
    item(store, finding_id="S1", signal="SECONDARY_NOT_BILLED", category="denial", line_key=f"{tk}-0-0", claim_key=f"{tk}-0",
         transaction_key=tk, first_seen="2026-04-01T08:00:00+00:00", work_state="work_now", shortfall_cents=2000)
    remit(store, trace=F.check_no(2), paid_on="2026-05-02", payer="MEDICARE SUPPLEMENT TEST",
          claims=[{"pcn": F.claim_id(1, sub=2), "status": "2", "lines": [ln("99213", 150, 20, [("CO", "45", 130)])]}])
    run(store)
    m = dash(store)["money"]
    assert (m["b_cents"], m["b_items"]) == (2000, 1)
    assert "a second payer nobody had billed" in money_of(store, "S1")["events"][0]["rule"]
    # billed through the button first: the payment comes after that work, inside the answer time, so it is (a)
    event(store, "S1", "billed", at="2026-04-10T09:00:00+00:00", actor="Biller One")
    run(store)
    m = dash(store)["money"]
    assert (m["a_cents"], m["b_cents"]) == (2000, 0)


def test_an_unposted_835_payment_posted_in_ema_after_the_flag_is_money_found_at_most_what_was_unposted(store):
    tk = remit(store, trace=F.check_no(7), paid_on="2026-03-20", claims=[{"pcn": F.claim_id(1), "lines": [ln("99213", 150, 120, [("CO", "45", 30)])]}])
    item(store, finding_id="P1", signal="REMIT_NOT_POSTED", category="fees", transaction_key=tk, patient_control_number=None,
         line_key=None, claim_key=None, first_seen="2026-04-01T08:00:00+00:00", work_state="work_now", shortfall_cents=12000,
         evidence={"ema_posted_cents": 0, "claim_paid_cents": 12000})
    run(store, today="2026-04-02")              # registered while EMA had posted nothing of it
    store.conn.execute("""INSERT INTO ema_checks (check_no, payer, ins_paid, trace_key, first_posted, last_posted)
                          VALUES (?,?,?,?,?,?)""", (F.check_no(7), "Anthem", 15000, tk, "2026-04-20", "2026-04-20"))
    store.conn.commit()
    run(store)
    m = dash(store)["money"]
    assert (m["b_cents"], m["returned_cents"]) == (12000, 12000)       # capped at the 120 dollars unposted when flagged
    assert "after RemitWatch found it unposted" in money_of(store, "P1")["events"][0]["rule"]


def test_a_takeback_reverses_an_attribution(store):
    fid, _ = denial(store)
    fax(store, fid, "2026-04-05")
    tk2 = paid(store, on="2026-04-20")
    run(store)
    assert dash(store)["money"]["returned_cents"] == 9000
    # the payer reverses that payment (status 22, the same received date) and pays nothing again
    remit(store, trace=F.check_no(150), paid_on="2026-05-20",
          claims=[{"pcn": F.claim_id(1, sub=2), "status": "22", "received": "2026-04-02", "lines": [ln("99213", -150, -90, [("CO", "45", -60)])]}])
    run(store)
    m = dash(store)["money"]
    assert m["returned_cents"] == 0 and m["items"] == 0 and m["taken_back_cents"] == 0
    assert tk2


def test_money_paid_before_the_flag_and_taken_back_after_it_is_subtracted_newest_money_first(store):
    fid, _ = denial(store, flagged="2026-04-10")
    paid(store, on="2026-04-01", amount=50)          # before the flag: not counted
    # after the flag: the payer reverses the 50 dollars and pays 90 on the same remittance (a reprocessing)
    remit(store, trace=F.check_no(160), paid_on="2026-05-20",
          claims=[{"pcn": F.claim_id(1, sub=2), "status": "22", "received": "2026-04-02", "lines": [ln("99213", -150, -50, [("CO", "45", -100)])]},
                  {"pcn": F.claim_id(1, sub=3), "status": "1", "received": "2026-05-10", "lines": [ln("99213", 150, 90, [("CO", "45", 60)])]}])
    run(store)
    d = money_of(store, fid)
    assert (d["returned"], d["taken_back"]) == (4000, 0)        # 90 in, 50 of the money before the flag out
    # a takeback with nothing new after the flag is shown as taken back, never as negative money returned
    fid2, _ = denial(store, 2, flagged="2026-04-10")
    paid(store, 2, on="2026-04-01", amount=70)
    remit(store, trace=F.check_no(260), paid_on="2026-05-20",
          claims=[{"pcn": F.claim_id(2, sub=2), "status": "22", "received": "2026-04-02", "lines": [ln("99213", -150, -70, [("CO", "45", -80)])]}])
    run(store)
    d2 = money_of(store, fid2)
    assert (d2["returned"], d2["taken_back"]) == (0, 7000)
    m = dash(store)["money"]
    assert m["taken_back_cents"] == 7000 and m["returned_cents"] == 4000


def test_one_payment_counts_once_to_the_earliest_raised_item_of_its_line(store):
    early, tk = denial(store, 1, flagged="2026-04-01")
    # a second item on the same line and claim, raised later (say the same denial read again under another signal)
    item(store, finding_id="LATE", signal="DENIED_OTHER", line_key=f"{tk}-0-0", claim_key=f"{tk}-0", transaction_key=tk,
         first_seen="2026-04-15T08:00:00+00:00", work_state="work_now")
    fax(store, "LATE", "2026-04-16")
    paid(store, on="2026-04-25")
    run(store)
    m = dash(store)["money"]
    assert (m["returned_cents"], m["items"]) == (9000, 1)
    d = money_of(store, early)
    assert d["members"] == sorted([early, "LATE"]) and money_of(store, "LATE") is None
    assert d["events"][0]["bucket"] == "a"          # the work on the later item of the line counts for the line


def test_an_item_that_drops_off_the_list_keeps_its_first_flag_and_money_paid_on_it_later_counts(store):
    fid, _ = denial(store)
    run(store, today="2026-04-02")
    store.conn.execute("DELETE FROM findings WHERE finding_id=?", (fid,))      # detection no longer lists it
    store.conn.commit()
    run(store, today="2026-04-10")
    reg = results.load_registry(store.conn)[fid]
    assert reg["gone_on"] == "2026-04-10" and reg["first_flagged"] == FLAG
    paid(store, on="2026-04-20")
    run(store)
    assert dash(store)["money"]["returned_cents"] == 9000
    row = results.list_rows(store.conn, "returned", today=TODAY)["items"][0]
    assert row["finding_id"] == fid and row["listed"] is False and row["signal"] == "DENIED_AUTH" and row["first_flagged"] == FLAG


def test_a_flag_date_never_moves_later_once_registered(store):
    fid, _ = denial(store, flagged="2026-04-01")
    run(store, today="2026-04-02")
    store.conn.execute("UPDATE findings SET first_seen='2026-05-01T00:00:00+00:00' WHERE finding_id=?", (fid,))
    store.conn.commit()
    run(store)
    assert results.load_registry(store.conn)[fid]["first_flagged"] == "2026-04-01"


def test_emas_posting_of_a_check_no_835_carries_counts_and_its_posting_of_an_835_payment_does_not_count_twice(store):
    fid, _ = denial(store, 1, ema_link=F.link_no(1))
    ema_line(store, F.link_no(1), bill=F.bill_id(1))
    ema_pay(store, F.link_no(1), posted="2026-04-22", cents=7000, check="PAPER1", in_835=0)       # a paper check: counts
    fid2, _ = denial(store, 2, ema_link=F.link_no(2))
    ema_line(store, F.link_no(2), bill=F.bill_id(2))
    paid(store, 2, on="2026-04-20", amount=90)
    ema_pay(store, F.link_no(2), posted="2026-04-28", cents=9000, check="MISREAD", in_835=0)     # EMA's copy of that 835
    ema_pay(store, F.link_no(2), posted="2026-04-28", cents=9000, check="ERA", in_835=1)
    ema_pay(store, F.link_no(2), posted="2026-04-29", cents=4000, check="ERA2", in_835=1)        # carried by an 835: never EMA's own
    run(store)
    assert money_of(store, fid)["returned"] == 7000 and money_of(store, fid)["events"][0]["source"] == "production"
    assert money_of(store, fid2)["returned"] == 9000 and len(money_of(store, fid2)["events"]) == 1
    assert dash(store)["money"]["returned_cents"] == 16000


def test_a_person_marking_paid_counts_once_and_only_when_no_payment_record_shows_the_money(store):
    fid, _ = denial(store, 1, status="resolved", recovered_cents=8000, recovered_date="2026-05-05")
    fid2, _ = denial(store, 2, status="resolved", recovered_cents=8000, recovered_date="2026-05-05")
    paid(store, 2, on="2026-05-01", amount=90)       # the system sees the money: the person's mark is the same money
    fid3, _ = denial(store, 3, status="resolved", recovered_cents=8000, recovered_date="2026-03-30")   # marked before the flag
    auto, _ = denial(store, 4, status="resolved", recovered_cents=8000, recovered_date="2026-05-05", auto_note="Paid on a later remittance")
    run(store)
    assert money_of(store, fid)["returned"] == 8000 and money_of(store, fid)["events"][0]["source"] == "person"
    assert money_of(store, fid2)["returned"] == 9000 and len(money_of(store, fid2)["events"]) == 1
    assert money_of(store, fid3) is None and money_of(store, auto) is None


def test_a_line_paid_by_whole_claim_answer_counts_once(store):
    tk = remit(store, trace=F.check_no(1), paid_on="2026-03-28",
               claims=[{"pcn": F.claim_id(1), "status": "4", "lines": [ln("99213", 150, 0, [("CO", "16", 150)]), ln("11102", 120, 0, [("CO", "16", 120)])]}])
    item(store, finding_id="C1", signal="CLAIM_DENIED_UNPAID", line_key=None, claim_key=f"{tk}-0", transaction_key=tk,
         evidence={"unpaid": [["99213", "2026-03-02"], ["11102", "2026-03-02"]]}, first_seen="2026-04-01T08:00:00+00:00", work_state="work_now")
    item(store, finding_id="L1", signal="DENIED_CODING", line_key=f"{tk}-0-0", claim_key=f"{tk}-0", transaction_key=tk,
         first_seen="2026-04-03T08:00:00+00:00", work_state="work_now")
    remit(store, trace=F.check_no(2), paid_on="2026-05-01",
          claims=[{"pcn": F.claim_id(1, sub=2), "lines": [ln("99213", 150, 90, [("CO", "45", 60)]), ln("11102", 120, 70, [("CO", "45", 50)])]}])
    run(store)
    m = dash(store)["money"]
    assert (m["returned_cents"], m["items"]) == (16000, 1) and money_of(store, "C1")["members"] == ["C1", "L1"]


# ================================================================================================ lift and the qualifier
def _population(s, worked_paid, worked_unpaid, unworked_paid, unworked_unpaid, start=1):
    n = start
    for count, work, pays in ((worked_paid, True, True), (worked_unpaid, True, False), (unworked_paid, False, True),
                              (unworked_unpaid, False, False)):
        for _i in range(count):
            fid, _ = denial(s, n)
            if work:
                fax(s, fid, "2026-04-05")
            if pays:
                paid(s, n, on="2026-04-20", amount=100)
            n += 1
    return n


def test_the_lift_with_small_counts_says_not_enough_cases_yet(store):
    _population(store, 2, 1, 1, 2)
    run(store)
    lf = dash(store)["lift"]
    assert lf["worked"] == {"n": 3, "paid": 2, "rate": 0.6667, "returned_cents": 20000, "stake_cents": 27000}
    assert (lf["unworked"]["n"], lf["unworked"]["paid"]) == (3, 1) and lf["enough"] is False
    # fewer than 5 unworked items in the pattern: the age band's rate (1 of 3) predicts a third of each worked item's dollars
    assert lf["predicted_cents"] == round(27000 * 10000 / 27000) and lf["incremental_cents"] == 20000 - 10000
    assert lf["z"] == round(1 / 3, 4) and lf["z_n"] == 3


def test_the_lift_compares_each_worked_item_with_its_own_pattern_when_the_pattern_has_enough_unworked_cases(store):
    # code 11102: 2 worked (both paid), 6 unworked (3 paid). Code 99213: 6 unworked, none paid. Same payer, reason, age.
    n = 1
    for code, work, pays, count in (("11102", True, True, 2), ("11102", False, True, 3), ("11102", False, False, 3), ("99213", False, False, 6)):
        for _i in range(count):
            fid, _ = denial(store, n, code=code)
            if work:
                fax(store, fid, "2026-04-05")
            if pays:
                paid(store, n, on="2026-04-20", amount=100, code=code)
            n += 1
    run(store)
    lf = dash(store)["lift"]
    # the pattern's own rate (3 of 6) predicts half of each worked item, not the wider band's 3 of 12
    assert lf["predicted_paid"] == 1.0 and lf["levels_used"] == {"pattern and age": 2}
    assert (lf["z_n"], lf["z_paid"]) == (6, 3)


def test_the_lift_with_enough_cases_and_the_qualifier_is_z_of_the_flagged_dollars_of_bucket_c(store):
    _population(store, 15, 6, 8, 16)            # 21 worked, 24 unworked
    run(store)
    d = dash(store)
    lf, m = d["lift"], d["money"]
    assert lf["enough"] is True and (lf["worked"]["n"], lf["unworked"]["n"]) == (21, 24)
    z = 8 / 24
    assert lf["z"] == round(z, 4)
    # (c): the 8 unworked items that came back, $100 each on $90 flagged: Z x 8 x 90 dollars, at most the 800 dollars of (c)
    assert m["c_cents"] == 80000 and m["anyway"]["c_flagged_cents"] == 8 * 9000
    assert m["anyway"]["cents"] == min(round(z * 8 * 9000), 80000) == 24000
    counts = d["counts"]
    assert counts["lift:worked"] == 21 and counts["lift:worked:paid"] == 15 and counts["lift:unworked:paid"] == 8


# ================================================================================================ settings, hours, ROI
def test_hours_saved_use_the_owners_minutes_and_the_history_keeps_each_version(store):
    fid, _ = denial(store, 1)
    fax(store, fid, "2026-04-05")
    fax(store, fid, "2026-05-05")
    denial(store, 2, status="resolved", auto_note="Paid on a later remittance", recovered_cents=100, recovered_date="2026-03-30")
    event(store, fid, "packet", at="2026-04-04T10:00:00+00:00", actor="packets")
    item(store, finding_id="CARRIER", evidence={"folded": [{"finding_id": "x1"}, {"finding_id": "x2"}]})
    run(store)
    counts = dash(store)["hours_counts"]
    assert counts == {"investigate": 1, "packet": 1, "fax": 2, "duplicate": 2}
    s0 = results.read_settings(store.conn)
    assert s0["version"] == "" and results.hours_saved(counts, s0)["hours"] == round((12 + 20 + 2 * 10 + 2 * 5) / 60, 1)
    s1 = results.save_settings({"minutes": {"investigate": 30, "fax": 15}}, who="The Owner", base_version="", conn=store.conn)
    assert s1["values"]["minutes"] == {"investigate": 30.0, "packet": 20.0, "fax": 15.0, "duplicate": 5.0}
    assert results.hours_saved(counts, s1)["hours"] == round((30 + 20 + 30 + 10) / 60, 1)
    with pytest.raises(results.ResultsError) as stale:
        results.save_settings({"minutes": {"packet": 1}}, who="Someone", base_version="", conn=store.conn)
    assert stale.value.status == 409
    s2 = results.save_settings({"costs": {"hosting_month": 20}}, who="The Owner", base_version=s1["version"], conn=store.conn)
    assert [h["values"]["minutes"]["investigate"] for h in s2["history"]] == [30.0, 30.0] and s2["values"]["costs"]["hosting_month"] == 20.0
    for bad in ({"minutes": {"fax": -1}}, {"minutes": {"fax": float("nan")}}, {"minutes": {"nope": 1}}, {"costs": {"fax_page": "x"}},
                {"minutes": {"fax": True}}, "nonsense"):
        with pytest.raises(results.ResultsError):
            results.save_settings(bad, who="x", base_version=s2["version"], conn=store.conn)


def test_roi_is_money_returned_over_cost_by_month_with_the_lift_adjusted_return_beside_it(store):
    fid, _ = denial(store, 1)
    fax(store, fid, "2026-04-05", pages=10)
    paid(store, 1, on="2026-04-20", amount=150)
    other, _ = denial(store, 2)
    paid(store, 2, on="2026-04-25", amount=50)
    fax(store, "INBOUND", "2026-04-07", direction="in")        # a payer's fax the app read: one AI call
    run(store)
    rec = results.save_settings({"costs": {"hosting_month": 40, "fax_page": 0.10, "ai_call": 0.50, "other_month": 10}}, who="o",
                                base_version="", conn=store.conn)
    r = results.roi(dash(store), rec, TODAY)
    april = next(x for x in r["months"] if x["month"] == "2026-04")
    assert april["cost_cents"] == 5000 + 100 + 50 and april["returned_cents"] == 20000
    assert april["roi"] == round(20000 / 5150, 2) and april["fax_pages"] == 10 and april["ai_calls"] == 1
    lf = dash(store)["lift"]
    factor = max(0.0, min(1.0, lf["incremental_cents"] / lf["worked"]["returned_cents"]))
    assert april["conservative_cents"] == round(15000 * factor) and r["costs_set"] is True
    assert results.roi(dash(store), results.read_settings(None), TODAY)["roi"] is None      # no costs set: no ROI, not infinity


# ================================================================================================ denials
def test_denial_rates_by_month_and_a_recorded_fix_with_the_rate_before_and_after(store):
    k = 0
    for month, denied_n, paid_n in (("2026-01", 3, 1), ("2026-02", 3, 1), ("2026-03", 2, 2), ("2026-05", 1, 3), ("2026-06", 0, 4)):
        for i in range(denied_n + paid_n):
            k += 1
            amount = 0 if i < denied_n else 100
            adj = [("CO", "197", 150)] if amount == 0 else [("CO", "45", 50)]
            remit(store, trace=F.check_no(5000 + k), paid_on=f"{month}-15",
                  claims=[{"pcn": F.claim_id(500 + k), "lines": [ln("99213", 150, amount, adj, allowed=None if amount == 0 else 100)]}])
    fid, _ = denial(store, 900, paid_on="2026-03-20")
    event(store, fid, "manual", at="2026-04-10T10:00:00+00:00", frm="open", to="dismissed", actor="Biller One",
          note="Provider credentialing fixed with the payer")
    run(store)
    dn = dash(store)["denials"]
    jan = next(m for m in dn["months"] if m["month"] == "2026-01")
    assert (jan["answered"], jan["denied"], jan["line_rate"], jan["dollar_rate"]) == (4, 3, 0.75, 0.75)
    g = next(x for x in dn["by_group"] if x["key"] == BCBS)
    assert g["answered"] == 4 + 4 + 4 + 4 + 4 + 1
    mk = dn["markers"][0]
    assert (mk["kind"], mk["payer_group"], mk["date"]) == ("credentialing", BCBS, "2026-04-10")
    assert mk["before"]["line_rate"] == round(9 / 13, 4) and mk["after"]["line_rate"] == round(1 / 8, 4)
    assert dn["reasons"]["rising"] == [] or all(r["change"] > 0 for r in dn["reasons"]["rising"])
    # worked out again only when the 835s change
    cached = results._meta(store.conn, results.DENIALS_KEY)
    run(store)
    assert results._meta(store.conn, results.DENIALS_KEY)["rev"] == cached["rev"]


# ================================================================================================ snapshots and baseline
def test_a_frozen_week_never_changes_and_the_baseline_is_written_once(store):
    fid, _ = denial(store)
    paid(store, on="2026-04-20")
    run(store, today="2026-04-22")              # the week of 2026-04-20 starts its row
    base = results._meta(store.conn, results.BASELINE_KEY)
    assert base["day"] == "2026-04-22" and base["states"]["work_now"]["n"] == 1
    run(store, today="2026-05-11")              # that week ended 2026-04-26; settled 14 days later
    snaps = {s["week"]: s for s in results.snapshots(store.conn)}
    w = snaps["2026-04-20"]
    assert w["final"] and w["money"]["returned_cents"] == 9000 and w["settings"]["minutes"]["investigate"] == 12
    frozen = json.dumps(w, sort_keys=True)
    # money dated in that week arrives late: the live figures show it, the frozen week does not change
    denial(store, 2)
    paid(store, 2, on="2026-04-21", amount=50)
    run(store, today="2026-05-20")
    assert json.dumps({s["week"]: s for s in results.snapshots(store.conn)}["2026-04-20"], sort_keys=True) == frozen
    assert next(r for r in dash(store)["money"]["by_week"] if r["key"] == "2026-04-20")["cents"] == 14000
    assert results._meta(store.conn, results.BASELINE_KEY) == base


def test_refresh_does_nothing_when_nothing_it_rests_on_changed(store):
    denial(store)
    out = results.refresh(store.conn, today=TODAY)
    assert out["changed"]
    again = results.refresh(store.conn, today=TODAY)
    assert again == {"changed": False, "host": None, "run_id": None, "skipped": "nothing changed"}
    event(store, "F1", "call", at="2026-06-01T10:00:00+00:00", actor="Biller One")
    assert results.refresh(store.conn, today=TODAY)["changed"]


def test_an_item_that_expires_in_work_now_is_lost_to_its_deadline_with_its_dollars(store):
    fid, _ = denial(store, appeal_deadline="2026-05-01")
    run(store, today="2026-04-20")
    store.conn.execute("UPDATE findings SET work_state='expired' WHERE finding_id=?", (fid,))
    store.conn.commit()
    run(store, today="2026-06-05")
    d = dash(store)
    assert d["lost"]["n"] == 1 and d["lost"]["cents"] == 9000 and d["lost"]["weeks"][0]["week"] == "2026-04-27"
    assert results.list_rows(store.conn, "lost", today="2026-06-05")["items"][0]["finding_id"] == fid
    # waiting when it expired: worked and waiting on the payer, not lost to neglect
    fid2, _ = denial(store, 2, work_state="waiting", appeal_deadline="2026-05-01")
    run(store, today="2026-06-06")
    store.conn.execute("UPDATE findings SET work_state='expired' WHERE finding_id=?", (fid2,))
    store.conn.commit()
    run(store, today="2026-06-07")
    assert dash(store)["lost"]["n"] == 1


# ================================================================================================ live counts and lists
def test_every_figure_count_equals_the_list_it_opens(store):
    _population(store, 3, 2, 2, 3)
    denial(store, 50, work_state="waiting")
    denial(store, 51, work_state="work_now", appeal_deadline="2026-07-05")
    denial(store, 52, written_off_cents=4000, written_off_on="2026-04-02", service_date="2026-03-02")
    denial(store, 53, status="dismissed", work_state="accepted")
    event(store, "F53", "manual", at="2026-04-03T00:00:00+00:00", frm="open", to="dismissed", actor="Biller One", note="Not worth it")
    run(store)
    d = dash(store)
    for key, n in d["counts"].items():
        assert results.list_rows(store.conn, key, today=TODAY, per=500)["total"] == n, key
    live = results.live_figures(store.conn, TODAY)
    for st, v in live["states"].items():
        assert results.list_rows(store.conn, f"state:{st}", today=TODAY, per=500)["total"] == v["n"], st
    assert live["at_risk"]["n"] == results.list_rows(store.conn, "at_risk", today=TODAY)["total"] == 1
    assert live["given_up"]["n"] == 1 and live["given_up"]["cents"] == 4000 and live["given_up_recoverable"]["n"] == 1
    assert sum(r[1] for r in results.list_entries(store.conn, "returned", TODAY)) == d["money"]["returned_cents"]
    assert results.list_rows(store.conn, "returned", today=TODAY, per=500)["total"] == d["money"]["items"]
    for b in "abc":
        assert sum(r[1] for r in results.list_entries(store.conn, f"returned:{b}", TODAY)) == d["money"][f"{b}_cents"]
        assert results.list_rows(store.conn, f"returned:{b}", today=TODAY, per=500)["total"] == d["money"][f"{b}_items"]
    assert results.list_rows(store.conn, "taken_back", today=TODAY)["total"] == d["money"]["taken_back_items"]
    assert results.list_rows(store.conn, "lost", today=TODAY)["total"] == d["lost"]["n"]
    assert results.list_rows(store.conn, "book", today=TODAY, per=500)["total"] == d["outcomes"]["book"]
    with pytest.raises(KeyError):
        results.list_rows(store.conn, "no-such-list", today=TODAY)
    with pytest.raises(KeyError):
        results.live_sql("state:nonsense", TODAY)


def test_the_list_csv_has_no_patient_details_unless_names_are_on_and_cannot_carry_a_formula(store):
    fid, _ = denial(store)
    paid(store, on="2026-04-20")
    store.conn.execute("UPDATE findings SET payer_group='=HYPERLINK(\"x\")' WHERE finding_id=?", (fid,))
    store.conn.commit()
    run(store)
    text = results.list_csv(store.conn, "returned", today=TODAY)
    head, row = text.splitlines()[0], text.splitlines()[1]
    assert "claim_id" not in head and F.claim_id(1) not in text and "TESTPATIENT" not in text
    assert "'=HYPERLINK" in row
    assert F.claim_id(1) in results.list_csv(store.conn, "returned", today=TODAY, show_phi=True)


# ================================================================================================ the scoreboard
def test_the_scoreboard_gives_todays_four_figures_and_dashes_before_the_first_run(store):
    denial(store, 1, appeal_deadline="2026-07-10")
    sb = results.scoreboard(store.conn, TODAY)
    assert sb["recovered_cents"] is None and sb["lost_cents"] is None and sb["at_risk_cents"] == 9000
    paid(store, 1, on="2026-04-20")
    run(store)
    sb = results.scoreboard(store.conn, TODAY)
    assert set(sb) >= {"recovered_cents", "lost_cents", "at_risk_cents", "given_up_cents", "as_of", "weeks"}
    assert (sb["recovered_cents"], sb["lost_cents"], sb["at_risk_cents"]) == (9000, 0, 9000)
    assert {"week", "recovered_cents", "lost_cents", "at_risk_cents", "given_up_cents"} <= set(sb["weeks"][0])
    assert next(w for w in sb["weeks"] if w["week"] == "2026-04-20")["recovered_cents"] == 9000
    assert sb["weeks"][-1]["week"] == results.week_of(TODAY) and sb["weeks"][-1]["at_risk_cents"] == 9000


def test_the_scoreboard_shows_the_self_checks_given_up_figure_when_the_run_stores_it(store):
    denial(store, 1, written_off_cents=4000, written_off_on="2026-04-02")
    run(store)
    assert results.scoreboard(store.conn, TODAY)["given_up_source"] == "results"
    store.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('selfcheck:given_up', ?)", (json.dumps({"cents": 5000, "n": 2}),))
    store.conn.commit()
    sb = results.scoreboard(store.conn, TODAY)
    assert (sb["given_up_cents"], sb["given_up_source"]) == (5000, "selfcheck")


# ================================================================================================ work done and outcomes
def test_work_done_and_the_open_book(store):
    w1, _ = denial(store, 1)
    fax(store, w1, "2026-04-05", pages=4)
    w2, _ = denial(store, 2)
    event(store, w2, "manual", at="2026-04-05T10:00:00+00:00", frm="open", to="appealed", actor="Biller Two")
    remit(store, trace=F.check_no(299), paid_on="2026-05-01", claims=[{"pcn": F.claim_id(2, sub=2), "status": "4", "lines": [ln("99213", 150, 0, [("CO", "50", 150)])]}])
    store.conn.execute("UPDATE findings SET work_state='work_now' WHERE finding_id=?", (w2,))
    w3, _ = denial(store, 3)
    event(store, w3, "call", at="2026-06-01T10:00:00+00:00", actor="Biller Two")
    store.conn.execute("UPDATE findings SET work_state='waiting' WHERE finding_id=?", (w3,))
    c4, _ = denial(store, 4, work_state="cant_check", state_basis="Separate payee: the remittance goes to another payee. Ask the payer.")
    e5, _ = denial(store, 5, work_state="expired")
    store.conn.commit()
    run(store, today="2026-04-10")                  # the first run: the open book is what is unresolved now
    paid(store, 1, on="2026-04-20")
    store.conn.execute("UPDATE findings SET status='resolved', auto_note='Paid on a later remittance', work_state='done' WHERE finding_id=?", (w1,))
    store.conn.commit()
    run(store)
    d = dash(store)
    w, o = d["work"], d["outcomes"]
    assert {k["key"]: (k["items"], k["n"]) for k in w["kinds"]} == {"fax": (1, 1), "sent": (1, 1), "call": (1, 1)}
    assert w["faxes_sent"] == 1 and w["fax_pages"] == 4 and {p["key"] for p in w["people"]} == {"Biller One", "Biller Two"}
    assert o["book"] == 4 and o["closed"] == 1 and o["closed_by"][0]["key"] == "paid"
    assert o["cant_check"] == 1 and o["cant_by"][0]["key"] == "Separate payee"
    assert o["waiting_no_money"] == 1 and o["waiting_median_days"] == 29
    assert o["denied_again"] == 1 and {k["key"]: (k["won"], k["lost"]) for k in o["win_by_kind"]} == {"fax": (1, 0), "sent": (0, 1)}
    assert e5 not in [x[0] for x in results.list_entries(store.conn, "book", TODAY)]      # never open while RemitWatch watched it
    assert c4


# ================================================================================================ spot checks
def _strata(s):
    n = 1
    for state_, k in (("work_now", 4), ("waiting", 2), ("done", 2)):
        for _i in range(k):
            denial(s, n, work_state=state_)
            n += 1
    for _i in range(2):
        denial(s, n, status="resolved", auto_note="Paid on a later remittance", work_state="done", status_changed_at="2026-06-20T00:00:00+00:00")
        n += 1


def test_spot_checks_are_stratified_repeatable_and_never_repeat_within_90_days(store):
    _strata(store)
    results._set_meta(store.conn, results.SALT_KEY, "fixed-for-the-test")
    store.conn.commit()
    a = results.pick_spot_checks(store.conn, TODAY, "salt", set())
    b = results.pick_spot_checks(store.conn, TODAY, "salt", set())
    assert [p["finding_id"] for p in a] == [p["finding_id"] for p in b] and len(a) == 5
    assert [p["stratum"] for p in a] == ["work_now", "work_now", "waiting", "done", "closed_auto"]
    day1 = results.spot_day(store.conn, TODAY)
    day2 = results.spot_day(store.conn, "2026-07-01")
    assert not {p["finding_id"] for p in day1} & {p["finding_id"] for p in day2}
    assert results.spot_day(store.conn, TODAY) == day1          # asked again: the same picks, kept


def test_a_stratum_with_too_few_items_gives_its_places_to_the_others(store):
    for n in range(1, 8):
        denial(store, n)
    picks = results.pick_spot_checks(store.conn, TODAY, "salt", set())
    assert len(picks) == 5 and {p["stratum"] for p in picks} == {"work_now"}


def test_spot_check_answers_are_kept_with_who_and_when_and_the_agreement_rate_follows(store):
    _strata(store)
    picks = results.spot_day(store.conn, TODAY)
    results.record_spot_check(store.conn, TODAY, picks[0]["finding_id"], "agree", None, None, "Ana")
    results.record_spot_check(store.conn, TODAY, picks[1]["finding_id"], "disagree", "already_paid", "EMA shows the payment ñ", "Bo")
    with pytest.raises(results.ResultsError):
        results.record_spot_check(store.conn, TODAY, picks[2]["finding_id"], "disagree", None, None, "Bo")
    with pytest.raises(results.ResultsError):
        results.record_spot_check(store.conn, TODAY, picks[2]["finding_id"], "disagree", "other", "", "Bo")
    with pytest.raises(results.ResultsError) as missing:
        results.record_spot_check(store.conn, TODAY, "NOT-PICKED", "agree", None, None, "Bo")
    assert missing.value.status == 404
    s = results.spot_summary(results.spot_rows_all(store.conn))
    assert (s["answered"], s["agree"], s["rate"]) == (2, 1, 0.5)
    dis = s["disagreements"][0]
    assert dis["reason_label"] == "Already paid" and dis["who"] == "Bo" and dis["note"].endswith("ñ")
    case = results.spot_check_cases(store.conn, today=TODAY)[0]
    assert "note" not in case and case["note_chars"] == len("EMA shows the payment ñ")
    # a newer answer replaces the older one
    results.record_spot_check(store.conn, TODAY, picks[1]["finding_id"], "agree", None, None, "Bo")
    assert results.spot_summary(results.spot_rows_all(store.conn))["agree"] == 2


class _RacyState(LocalState):
    """A state folder where another person's answer lands between this one's read and its write, once."""

    def __init__(self, root, other):
        super().__init__(root)
        self.other, self.done = other, False

    def put(self, name, data, mode="overwrite", rev=None):
        if not self.done and name.startswith("results/spot_checks_") and mode == "update":
            self.done = True
            self.other()
        return super().put(name, data, mode=mode, rev=rev)


def test_spot_checks_on_a_serverless_host_live_in_the_state_folder_and_two_answers_at_once_both_land(store, tmp_path):
    _strata(store)
    root = str(tmp_path / "state")
    plain = LocalState(root)
    picks = results.spot_day_state(store.conn, plain, TODAY)
    assert len(picks) == 5 and plain.get(f"results/spot_checks_{TODAY[:4]}.json")
    racy = _RacyState(root, lambda: results.record_spot_check_state(plain, TODAY, picks[1]["finding_id"], "agree", None, None, "Bo"))
    results.record_spot_check_state(racy, TODAY, picks[0]["finding_id"], "disagree", "not_paid", None, "Ana")
    rows = {r["finding_id"]: r for r in results.spot_rows_all(None, plain, TODAY)}
    assert rows[picks[0]["finding_id"]]["answer"] == "disagree" and rows[picks[1]["finding_id"]]["answer"] == "agree"
    assert results.spot_day_state(store.conn, plain, TODAY) == [dict(p, day=TODAY, **{k: rows[p["finding_id"]].get(k) for k in
                                                                                        ("answer", "reason", "note", "who", "answered_at")}) for p in picks]
    # the run brings them into the store
    got = results.merge_state(store.conn, plain)
    assert got["spot"] == 2
    assert {r["finding_id"]: r["answer"] for r in results.spot_rows_all(store.conn) if r["answer"]} == \
        {picks[0]["finding_id"]: "disagree", picks[1]["finding_id"]: "agree"}


def test_settings_on_a_serverless_host_live_in_the_state_folder_and_reach_the_run(store, tmp_path):
    st = LocalState(str(tmp_path / "state"))
    rec = results.save_settings({"minutes": {"packet": 45}}, who="Owner", base_version="", state=st)
    with pytest.raises(results.ResultsError) as stale:
        results.save_settings({"minutes": {"packet": 1}}, who="Owner", base_version="", state=st)
    assert stale.value.status == 409
    assert results.read_settings(None, st)["values"]["minutes"]["packet"] == 45.0
    assert results.merge_state(store.conn, st)["settings"] is True
    assert results.read_settings(store.conn)["values"]["minutes"]["packet"] == 45.0 and rec["saved"]


# ================================================================================================ both store kinds agree
def _scenario(s):
    _population(s, 3, 2, 2, 3)
    fid, _ = denial(s, 40, ema_link=F.link_no(40))
    ema_line(s, F.link_no(40), bill=F.bill_id(40))
    ema_pay(s, F.link_no(40), posted="2026-04-22", cents=7000, check="PAPER")
    denial(s, 41, written_off_cents=1000, written_off_on="2026-04-03")
    return s


def _strip(d):
    d = json.loads(json.dumps(d))
    for k in ("at", "seconds", "rev", "detect_run"):
        d.pop(k, None)
    return d


def test_the_server_store_and_the_compact_serverless_store_give_the_same_figures(tmp_path):
    a = new_store(str(tmp_path / "a.sqlite"))
    actions.ensure_fax_tables(a.conn)
    a = _scenario(a)
    b = compact_store()
    actions.ensure_fax_tables(b.conn)
    b = _scenario(b)
    run(a)
    run(b)
    assert _strip(dash(a)) == _strip(dash(b))
    assert results._meta(a.conn, results.LISTS_KEY) == results._meta(b.conn, results.LISTS_KEY)


# ================================================================================================ edge cases, random
def test_an_empty_store_and_a_store_without_the_new_columns_give_empty_figures(tmp_path):
    s = new_store(str(tmp_path / "e.sqlite"))
    out = run(s)
    assert out["changed"] and dash(s)["money"]["returned_cents"] == 0 and results.scoreboard(s.conn, TODAY)["recovered_cents"] == 0
    bare = sqlite3.connect(":memory:")
    bare.execute("CREATE TABLE findings (finding_id TEXT, signal TEXT, category TEXT, status TEXT, first_seen TEXT)")
    bare.execute("INSERT INTO findings VALUES ('X', 'DENIED_AUTH', 'denial', 'open', '2026-04-01')")
    assert results.refresh(bare, today=TODAY)["changed"]
    assert results.live_figures(bare, TODAY)["states"] == {} and results.scoreboard(bare, TODAY)["at_risk_cents"] == 0


def test_week_and_month_edges_leap_day_and_year_end():
    assert results.week_of("2028-02-29") == "2028-02-28"
    assert results.week_of("2027-01-01") == "2026-12-28"
    assert results.week_of("not a date") is None and results.week_of(None) is None
    assert results._months_between("2026-11", "2027-02") == 3
    r = results.roi({"money": {"first_flag": "2026-11-15", "by_month": [{"key": "2027-01", "cents": 100, "a": 100, "b": 0}]},
                     "lift": {}, "work": {}, "ai_calls_by_month": {}}, results.settings_record({"values": {"costs": {"hosting_month": 1}}}), "2027-02-03")
    assert [m["month"] for m in r["months"]] == ["2026-11", "2026-12", "2027-01", "2027-02"]


def test_zero_and_negative_amounts_never_count_as_money_returned(store):
    fid, _ = denial(store)
    paid(store, on="2026-04-20", amount=0)
    fid2, _ = denial(store, 2, ema_link=F.link_no(2))
    ema_line(store, F.link_no(2), bill=F.bill_id(2))
    ema_pay(store, F.link_no(2), posted="2026-04-22", cents=-3000, check="TAKE")
    run(store)
    assert dash(store)["money"]["returned_cents"] == 0 and money_of(store, fid2)["taken_back"] == 3000


def test_randomized_money_invariants(store):
    rnd = random.Random(20261122)
    paid_after = 0
    for n in range(1, 31):
        flagged = f"2026-04-{rnd.randint(1, 20):02d}"
        fid, _ = denial(store, n, flagged=flagged)
        if rnd.random() < 0.5:
            fax(store, fid, f"2026-04-{rnd.randint(1, 28):02d}")
        for sub in range(2, 2 + rnd.randint(0, 2)):
            day = f"2026-0{rnd.randint(3, 6)}-{rnd.randint(1, 28):02d}"
            amount = rnd.choice([0, 30, 90, 150])
            paid(store, n, on=day, amount=amount, sub=sub)
            if day > flagged:
                paid_after += amount * 100
    run(store)
    lists = results._meta(store.conn, results.LISTS_KEY)
    total = 0
    for fid, d in lists["detail"].items():
        assert d["returned"] >= 0 and d["taken_back"] >= 0 and d["a"] + d["b"] + d["c"] == d["returned"]
        assert all(e["date"] > d["F"] for e in d["events"])
        total += d["returned"]
    assert total == dash(store)["money"]["returned_cents"] <= paid_after


# ================================================================================================ the routes, both hosts
@pytest.fixture
def server_api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from remitwatch import api
    path = str(tmp_path / "srv.sqlite")
    s = new_store(path)
    actions.ensure_fax_tables(s.conn)
    _scenario(s)
    s.close()
    monkeypatch.setattr(api, "DB_PATH", path)
    monkeypatch.setattr(results, "_today", lambda today=None: str(today)[:10] if today else TODAY)
    return TestClient(api.app), api, path


def test_the_server_routes_work_the_figures_out_after_an_analysis_and_counts_equal_lists(server_api, monkeypatch):
    tc, api, path = server_api
    d = tc.get("/api/results").json()
    assert d["ready"] and d["host"] == "server" and d["money"]["returned_cents"] > 0
    for key, n in d["counts"].items():
        assert tc.get("/api/results/list", params={"key": key, "per": 500}).json()["total"] == n, key
    for st, v in d["live"]["states"].items():
        assert tc.get("/api/results/list", params={"key": f"state:{st}"}).json()["total"] == v["n"]
    sb = tc.get("/api/results/scoreboard").json()
    assert sb["recovered_cents"] == d["money"]["returned_cents"]
    assert tc.get("/api/results/list", params={"key": "nope"}).status_code == 404
    csv_text = tc.get("/api/results/list.csv", params={"key": "returned"})
    assert csv_text.headers["content-type"].startswith("text/csv") and "TESTPATIENT" not in csv_text.text
    assert len(d["spot"]["today"]) == 5
    # a new analysis makes the page work the figures out again; nothing new does not
    c = sqlite3.connect(path)
    c.execute("INSERT INTO detection_runs (run_id, started_at, finished_at) VALUES ('r2', '2026-06-30T01:00:00', '2026-06-30T01:05:00')")
    c.commit()
    c.close()
    assert tc.get("/api/results").json()["detect_run"] == "2026-06-30T01:05:00"


def test_the_server_routes_record_spot_checks_and_the_owner_sets_the_figures(server_api, monkeypatch):
    tc, api, _path = server_api
    xhr = {"X-Requested-With": "remitwatch"}
    day = tc.get("/api/results/spot").json()
    fid = day["items"][0]["finding_id"]
    assert tc.post("/api/results/spot", json={"day": TODAY, "finding_id": fid, "answer": "agree"}).status_code == 400   # no header
    assert tc.post("/api/results/spot", json={"day": TODAY, "finding_id": fid, "answer": "maybe"}, headers=xhr).status_code == 400
    r = tc.post("/api/results/spot", json={"day": TODAY, "finding_id": fid, "answer": "disagree", "reason": "already_worked", "name": "Ana"},
                headers=xhr)
    assert r.status_code == 200
    got = tc.get("/api/results").json()["spot"]
    assert got["summary"]["answered"] == 1 and got["today"][0]["who"] == "Ana"
    monkeypatch.setattr(api, "OWNER_PASSWORD", OWNER)
    body = {"values": {"minutes": {"fax": 25}}, "base_version": ""}
    assert tc.put("/api/results/settings", json=body, headers=xhr).status_code == 403
    ok = tc.put("/api/results/settings", json=body, headers=dict(xhr, **{"X-Owner-Password": OWNER}))
    assert ok.status_code == 200 and ok.json()["values"]["minutes"]["fax"] == 25.0
    assert tc.put("/api/results/settings", json=body, headers=dict(xhr, **{"X-Owner-Password": OWNER})).status_code == 409
    assert tc.get("/api/results/denials/clinicians").status_code == 403
    assert tc.get("/api/results/denials/clinicians", headers={"X-Owner-Password": OWNER}).status_code == 200
    page = tc.get("/api/results").json()
    assert page["owner_open"] is False and page["hours"]["rows"][2]["minutes"] == 25.0


@pytest.fixture
def cloud_api(tmp_path, monkeypatch):
    """The API in serverless mode over a state folder whose dashboard copy the run made from a compact store."""
    from fastapi.testclient import TestClient

    from remitwatch import api, cloud
    monkeypatch.setenv("REMITWATCH_TMP", str(tmp_path / "scratch"))
    root = tmp_path / "state"
    st = LocalState(str(root))
    s = compact_store()
    actions.ensure_fax_tables(s.conn)
    _scenario(s)

    def publish(with_figures: bool):
        if with_figures:
            run(s, host="serverless", state=st)
        cloud.save_serving(st, s.conn)
    monkeypatch.setenv("REMITWATCH_MODE", "serverless")
    monkeypatch.setenv("REMITWATCH_STATE_DIR", str(root))
    monkeypatch.setattr(cloud, "SERVING_COPY", cloud.ServingCopy())
    monkeypatch.setattr(results, "_today", lambda today=None: str(today)[:10] if today else TODAY)
    return TestClient(api.app), st, s, publish


def test_a_serverless_page_reads_what_the_run_stored_and_never_works_money_out_itself(cloud_api):
    from remitwatch import cloud
    tc, st, s, publish = cloud_api
    publish(False)
    d = tc.get("/api/results").json()
    assert d["ready"] is False and d["host"] == "serverless" and "daily run" in d["note"] and d["money"] is None
    assert d["live"]["states"]["work_now"]["n"] > 0                  # counts on the findings table are live on both hosts
    assert tc.get("/api/results/scoreboard").json()["recovered_cents"] is None
    publish(True)
    cloud.SERVING_COPY.ensure(force=True)
    d = tc.get("/api/results").json()
    assert d["ready"] is True and d["money"]["returned_cents"] == dash(s)["money"]["returned_cents"]
    for key, n in d["counts"].items():
        assert tc.get("/api/results/list", params={"key": key, "per": 500}).json()["total"] == n, key
    assert tc.get("/api/results/scoreboard").json()["recovered_cents"] == d["money"]["returned_cents"]


def test_a_serverless_page_keeps_spot_checks_and_settings_in_the_state_folder(cloud_api):
    tc, st, s, publish = cloud_api
    publish(True)
    xhr = {"X-Requested-With": "remitwatch"}
    picks = tc.get("/api/results/spot").json()["items"]
    assert len(picks) == 5 and st.get(f"results/spot_checks_{TODAY[:4]}.json")
    r = tc.post("/api/results/spot", json={"day": TODAY, "finding_id": picks[0]["finding_id"], "answer": "agree", "name": "Ana"}, headers=xhr)
    assert r.status_code == 200
    assert tc.get("/api/results").json()["spot"]["summary"]["answered"] == 1
    ok = tc.put("/api/results/settings", json={"values": {"costs": {"hosting_month": 25}}, "base_version": ""}, headers=xhr)
    assert ok.status_code == 200 and st.get("results/settings.json")
    assert tc.get("/api/results").json()["settings"]["values"]["costs"]["hosting_month"] == 25.0
    merged = results.merge_state(s.conn, st)            # the next run brings both into the store
    assert merged["settings"] and merged["spot"] == 1


def test_every_results_route_needs_the_login(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from remitwatch import api
    monkeypatch.delenv("REMITWATCH_ALLOW_OPEN", raising=False)
    monkeypatch.setattr(api, "PASSWORD", "correct-horse-battery-7")
    monkeypatch.setattr(api, "DB_PATH", str(tmp_path / "x.sqlite"))
    tc = TestClient(api.app)
    for method, path in (("get", "/api/results"), ("get", "/api/results/scoreboard"), ("get", "/api/results/list?key=returned"),
                         ("get", "/api/results/list.csv?key=returned"), ("get", "/api/results/settings"), ("put", "/api/results/settings"),
                         ("get", "/api/results/spot"), ("post", "/api/results/spot"), ("get", "/api/results/denials/clinicians")):
        r = getattr(tc, method)(path, headers={"X-Requested-With": "remitwatch"})
        assert r.status_code == 401, (method, path, r.status_code)
    auth = {"Authorization": "Basic " + base64.b64encode(b"oda:correct-horse-battery-7").decode()}
    monkeypatch.setattr(api, "USERNAME", "oda")
    assert tc.get("/api/results/scoreboard", headers=auth).status_code == 200


def test_the_page_script_is_plain_and_the_module_mirrors_the_apps_categories():
    from pathlib import Path

    from remitwatch import api
    js = (Path(results.__file__).parent / "static" / "results.js").read_text()
    assert "async function resultsTab(v)" in js and "—" not in js and "false positive" not in js.lower()
    assert "—" not in Path(results.__file__).read_text()
    assert tuple(results.STATE_CATEGORIES) == tuple(api.MONEY_CATEGORIES)
    # every global the script declares starts with rs / RS_ (or is the page itself): nothing clashes with index.html
    import re as _re
    names = _re.findall(r"^(?:async\s+)?function\s+(\w+)|^const\s+(\w+)", js, _re.M)
    assert all((a or b).startswith(("rs", "RS_")) or (a or b) == "resultsTab" for a, b in names)
