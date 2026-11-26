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
import re
import sqlite3

import pytest

from remitwatch import actions, detect, ema_reports as er, results, states
from remitwatch.state import LocalState
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


def test_a_reversal_cancels_the_earlier_answer_not_the_new_one_on_its_own_remittance_even_under_another_product(store):
    # paid 50 dollars before the flag; after it the payer moves the claim to its Medicare Advantage product: it reverses
    # the 50 (filing indicator 16) and pays 60 on the same remittance, then reverses the 60 and pays 60 again
    fid, _ = denial(store, 1, flagged="2026-04-10")
    remit(store, trace=F.check_no(170), paid_on="2026-04-01",
          claims=[{"pcn": F.claim_id(1, sub=2), "received": "2026-03-05", "lines": [ln("99213", 150, 50, [("CO", "45", 100)])]}])
    remit(store, trace=F.check_no(171), paid_on="2026-05-20",
          claims=[{"pcn": F.claim_id(1, sub=2), "status": "22", "cfi": "16", "received": "2026-03-05", "lines": [ln("99213", -150, -50, [("CO", "45", -100)])]},
                  {"pcn": F.claim_id(1, sub=3), "status": "1", "cfi": "16", "received": "2026-03-05", "lines": [ln("99213", 150, 60, [("CO", "45", 90)])]}])
    remit(store, trace=F.check_no(172), paid_on="2026-06-10",
          claims=[{"pcn": F.claim_id(1, sub=3), "status": "22", "cfi": "16", "received": "2026-03-05", "lines": [ln("99213", -150, -60, [("CO", "45", -90)])]},
                  {"pcn": F.claim_id(1, sub=4), "status": "1", "cfi": "16", "received": "2026-06-01", "lines": [ln("99213", 150, 60, [("CO", "45", 90)])]}])
    run(store)
    d = money_of(store, fid)
    # after the flag: 50 of the money before it taken back, 60 paid and taken back, 60 paid again: 10 dollars net, and
    # nothing is left taken back (the later money paid it back)
    assert (d["returned"], d["taken_back"]) == (1000, 0)


def test_a_reversal_of_money_no_835_shows_takes_back_emas_check_or_money_paid_before_the_flag(store):
    # 1: EMA posted the payer's check after the flag (no 835 here carries it); the payer then reverses that payment (its
    # pair here, the denial, paid nothing) and pays again: the check counts, the reversal takes it back, the new payment counts
    fid, _ = denial(store, 1, ema_link=F.link_no(1))
    ema_line(store, F.link_no(1), bill=F.bill_id(1))
    ema_pay(store, F.link_no(1), posted="2026-04-22", cents=9000, check="PAPER1", in_835=0)
    remit(store, trace=F.check_no(180), paid_on="2026-05-20",
          claims=[{"pcn": F.claim_id(1, sub=2), "status": "22", "received": "2026-03-05", "lines": [ln("99213", -150, -90, [("CO", "45", -60)])]},
                  {"pcn": F.claim_id(1, sub=3), "status": "1", "received": "2026-05-10", "lines": [ln("99213", 150, 90, [("CO", "45", 60)])]}])
    # 2: the same with EMA's check posted before the flag: money paid before the flag, taken back after it
    fid2, _ = denial(store, 2, ema_link=F.link_no(2), flagged="2026-04-10")
    ema_line(store, F.link_no(2), bill=F.bill_id(2))
    ema_pay(store, F.link_no(2), posted="2026-04-05", cents=9000, check="PAPER2", in_835=0)
    remit(store, trace=F.check_no(280), paid_on="2026-05-20",
          claims=[{"pcn": F.claim_id(2, sub=2), "status": "22", "received": "2026-03-05", "lines": [ln("99213", -150, -90, [("CO", "45", -60)])]},
                  {"pcn": F.claim_id(2, sub=3), "status": "1", "received": "2026-05-10", "lines": [ln("99213", 150, 90, [("CO", "45", 60)])]}])
    # 3: a reversal nothing here pairs with (the payment is older than the archive): money paid before the flag
    fid3, _ = denial(store, 3, flagged="2026-04-10")
    remit(store, trace=F.check_no(380), paid_on="2026-05-20",
          claims=[{"pcn": F.claim_id(3, sub=2), "status": "22", "received": "2026-01-05", "lines": [ln("99213", -120, -70, [("CO", "45", -50)])]},
                  {"pcn": F.claim_id(3, sub=3), "status": "1", "received": "2026-05-10", "lines": [ln("99213", 150, 90, [("CO", "45", 60)])]}])
    run(store)
    d = money_of(store, fid)
    assert (d["returned"], d["taken_back"]) == (9000, 0)
    assert {(e["source"], e["kind"]) for e in d["events"]} == {("production", "payment"), ("835", "payment"), ("835", "takeback")}
    assert ((money_of(store, fid2) or {}).get("returned", 0), (money_of(store, fid2) or {}).get("taken_back", 0)) == (0, 0)
    assert (money_of(store, fid3)["returned"], money_of(store, fid3)["taken_back"]) == (2000, 0)
    assert dash(store)["money"]["returned_cents"] == 11000


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


def test_a_payment_on_a_service_only_a_later_item_covers_counts_from_that_items_own_flag(store):
    # one claim, two services. C1 (flagged April 1) is about 99213 only; L2 (flagged May 1) is about 11102 only. A
    # whole-claim row ties them into one group, but a payment on 11102 in April came before L2 was flagged: never counted.
    tk = remit(store, trace=F.check_no(1), paid_on="2026-03-28",
               claims=[{"pcn": F.claim_id(1), "status": "4", "lines": [ln("99213", 150, 0, [("CO", "16", 150)]), ln("11102", 120, 0, [("CO", "16", 120)])]}])
    item(store, finding_id="C1", signal="DENIED_CODING", line_key=f"{tk}-0-0", claim_key=f"{tk}-0", transaction_key=tk,
         first_seen="2026-04-01T08:00:00+00:00", work_state="work_now")
    item(store, finding_id="L2", signal="DENIED_CODING", line_key=f"{tk}-0-1", claim_key=f"{tk}-0", transaction_key=tk,
         procedure_code="11102", billed_code="11102", first_seen="2026-05-01T08:00:00+00:00", work_state="work_now")
    remit(store, trace=F.check_no(2), paid_on="2026-04-20",
          claims=[{"pcn": F.claim_id(1, sub=2), "lines": [ln("99213", 150, 90, [("CO", "45", 60)]), ln("11102", 120, 70, [("CO", "45", 50)])]}])
    remit(store, trace=F.check_no(3), paid_on="2026-05-10", claims=[{"pcn": F.claim_id(1, sub=3), "lines": [ln("11102", 120, 30, [("CO", "45", 90)])]}])
    # a whole-claim answer with no service lines: it answers every service of the claim, so both items read it
    store.conn.execute("INSERT INTO transactions (transaction_key, trace_number, payment_date, payer_name, superseded) VALUES ('TXW', 'W1', '2026-03-30', 'BLUE CROSS TEST', 0)")
    store.conn.execute("""INSERT INTO claims (claim_key, transaction_key, patient_control_number, payment_date, statement_from, claim_status_code,
                          charge_cents, paid_cents, payer_name, claim_filing_indicator) VALUES ('TXW-0', 'TXW', ?, '2026-03-30', '2026-03-02', '4', 27000, 0,
                          'BLUE CROSS TEST', '12')""", (F.claim_id(1, sub=4),))
    store.conn.commit()
    run(store)
    assert money_of(store, "C1")["members"] == ["C1", "L2"]     # one group: they share the whole-claim answer
    assert money_of(store, "C1")["returned"] == 9000            # its own 99213 payment after its flag
    assert money_of(store, "L2")["returned"] == 3000            # 11102: only the payment after May 1
    assert dash(store)["money"]["returned_cents"] == 12000


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
    assert {k: counts[k] for k in results.HOUR_KINDS} == {"investigate": 1, "packet": 1, "fax": 2, "duplicate": 2}
    assert counts["items"] == {"investigate": 1, "packet": 1, "fax": 1, "duplicate": 1}      # two faxes on one item
    s0 = results.read_settings(store.conn)
    assert s0["version"] == "" and results.hours_saved(counts, s0)["hours"] == round((12 + 20 + 2 * 10 + 2 * 5) / 60, 1)
    s1 = results.save_settings({"minutes": {"investigate": 30, "fax": 15}}, who="The Owner", base_version="", conn=store.conn)
    assert s1["values"]["minutes"] == {"investigate": 30.0, "packet": 20.0, "fax": 15.0, "duplicate": 5.0}
    # everything counted so far happened before the owner's edit: it keeps the minutes in force then
    assert results.hours_saved(counts, s1)["hours"] == round((12 + 20 + 2 * 10 + 2 * 5) / 60, 1)
    # a fax sent after the edit is priced at the new minutes; the older ones keep theirs
    later = {"at": "2026-05-01T00:00:00+00:00", "by": "The Owner", "values": s1["values"]}
    dated = dict(s1, history=[later])
    assert results.hours_saved(counts, dated)["hours"] == round((12 + 20 + 10 + 15 + 2 * 5) / 60, 1)
    with pytest.raises(results.ResultsError) as stale:
        results.save_settings({"minutes": {"packet": 1}}, who="Someone", base_version="", conn=store.conn)
    assert stale.value.status == 409
    s2 = results.save_settings({"costs": {"hosting_month": 20}}, who="The Owner", base_version=s1["version"], conn=store.conn)
    assert [h["values"]["minutes"]["investigate"] for h in s2["history"]] == [30.0, 30.0] and s2["values"]["costs"]["hosting_month"] == 20.0
    for bad in ({"minutes": {"fax": -1}}, {"minutes": {"fax": float("nan")}}, {"minutes": {"nope": 1}}, {"costs": {"fax_page": "x"}},
                {"minutes": {"fax": True}}, "nonsense"):
        with pytest.raises(results.ResultsError):
            results.save_settings(bad, who="x", base_version=s2["version"], conn=store.conn)
    # the refusals name each figure as the page does, never by its internal name
    assert results.settings_problem({"costs": {"hosting_month": -1}}) == "Hosting per month must be between $0 and $1,000,000."
    assert results.settings_problem({"minutes": {"investigate": 601}}) == "The minutes to investigate one item by hand must be between 0 and 600 minutes."
    assert "_" not in results.settings_problem({"minutes": {"fax_x": 1}})


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
    # two items are not enough cases to measure what the work added: no lift-adjusted return is stated
    assert dash(store)["lift"]["enough"] is False and r["costs_set"] is True
    assert (april["conservative_cents"], april["roi_conservative"], r["roi_conservative"], r["conservative_cents"]) == (None, None, None, None)
    assert "not enough cases yet" in r["basis"]
    assert results.roi(dash(store), results.read_settings(None), TODAY)["roi"] is None      # no costs set: no ROI, not infinity
    # with enough cases on both sides, the lift-adjusted return is the share the unworked rate does not explain, plus found
    enough = dict(dash(store), lift=dict(dash(store)["lift"], enough=True))
    lf = enough["lift"]
    factor = max(0.0, min(1.0, lf["incremental_cents"] / lf["worked"]["returned_cents"]))
    april2 = next(x for x in results.roi(enough, rec, TODAY)["months"] if x["month"] == "2026-04")
    assert april2["conservative_cents"] == round(15000 * factor)


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


def test_settled_weeks_before_the_first_run_are_frozen_the_first_time_a_run_sees_them(store):
    fid, _ = denial(store)
    paid(store, on="2026-04-20")
    run(store, today="2026-06-30")              # the first run comes long after that week settled
    snaps = {s["week"]: s for s in results.snapshots(store.conn)}
    assert snaps["2026-04-20"]["final"] and snaps["2026-04-20"]["money"]["returned_cents"] == 9000
    assert snaps["2026-04-20"]["point"] is None                 # nobody measured the items by state that week
    assert not snaps[results.week_of("2026-06-30")]["final"]    # the current week keeps moving
    weeks = {w["week"]: w for w in results.scoreboard(store.conn, "2026-06-30")["weeks"]}
    assert weeks["2026-04-20"]["frozen"] is True and weeks["2026-04-20"]["at_risk_cents"] is None


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


def test_the_given_up_figure_and_the_list_it_opens_come_from_one_source(store):
    denial(store, 1, written_off_cents=4000, written_off_on="2026-04-02")
    run(store)
    assert results.scoreboard(store.conn, TODAY)["given_up_source"] == "results"
    # the self-check stores its own figure without the items behind it: ours stays the clickable figure, its own is a
    # cross-check beside it that opens nothing
    store.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('selfcheck:given_up', ?)", (json.dumps({"cents": 5000, "n": 2}),))
    store.conn.commit()
    sb = results.scoreboard(store.conn, TODAY)
    entries = results.list_entries(store.conn, "given_up", TODAY)
    assert (sb["given_up_cents"], sb["counts"]["given_up"], sb["given_up_source"]) == (4000, 1, "results")
    assert (sb["given_up_cents"], sb["counts"]["given_up"]) == (sum(c for _f, c in entries), len(entries))
    assert sb["given_up_check"] == {"cents": 5000, "n": 2, "at": None, "source": "selfcheck"}
    # when it stores the items it counted, its figure is the one shown and opens exactly those items, week by week its own
    store.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('selfcheck:given_up', ?)",
                       (json.dumps({"cents": 5000, "n": 2, "ids": [["F1", 4000], ["G9", 1000]],
                                    "weeks": [{"week": "2026-06-29", "cents": 5000}]}),))
    store.conn.commit()
    sb = results.scoreboard(store.conn, TODAY)
    entries = results.list_entries(store.conn, "given_up", TODAY)
    assert (sb["given_up_cents"], sb["counts"]["given_up"], sb["given_up_source"]) == (5000, 2, "selfcheck")
    assert entries == [["F1", 4000], ["G9", 1000]] and "given_up_check" not in sb
    assert next(w for w in sb["weeks"] if w["week"] == "2026-06-29")["given_up_cents"] == 5000


def test_the_self_checks_ranking_columns_when_the_run_writes_them_show_what_at_risk_items_are_likely_worth(store):
    fid, _ = denial(store, 1, appeal_deadline="2026-07-10")
    denial(store, 2, appeal_deadline="2026-07-12")
    run(store)
    have = results._cols(store.conn, "findings")
    if "expected_value_cents" not in have:      # before the hook-in agent adds them, nothing is shown and nothing breaks
        assert "expected_cents" not in results.live_figures(store.conn, TODAY)["at_risk"]
        assert "at_risk_expected_cents" not in results.scoreboard(store.conn, TODAY)
        assert results.list_rows(store.conn, "at_risk", today=TODAY)["items"][0]["expected_value_cents"] is None
    for col, kind in (("expected_value_cents", "INTEGER"), ("recovery_rate", "REAL"), ("recovery_basis", "TEXT")):
        if col not in have:
            store.conn.execute(f"ALTER TABLE findings ADD COLUMN {col} {kind}")
    store.conn.execute("UPDATE findings SET expected_value_cents=3150, recovery_rate=0.35, recovery_basis='payer group x code x reason, 40 cases' "
                       "WHERE finding_id=?", (fid,))
    store.conn.commit()
    live = results.live_figures(store.conn, TODAY)
    assert live["at_risk"]["n"] == 2 and live["at_risk"]["cents"] == 18000
    assert (live["at_risk"]["expected_cents"], live["at_risk"]["expected_n"]) == (3150, 1)
    sb = results.scoreboard(store.conn, TODAY)
    assert (sb["at_risk_cents"], sb["at_risk_expected_cents"], sb["counts"]["at_risk_expected"]) == (18000, 3150, 1)
    assert "recovery rate" in sb["basis"]["at_risk_expected"]
    rows = {r["finding_id"]: r for r in results.list_rows(store.conn, "at_risk", today=TODAY)["items"]}
    assert rows[fid]["expected_value_cents"] == 3150 and rows[fid]["recovery_basis"].endswith("40 cases")
    assert rows["F2"]["expected_value_cents"] is None
    text = results.list_csv(store.conn, "at_risk", today=TODAY)
    assert "Likely worth" in text.splitlines()[0] and ",31.50," in text


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
    # the open book's own breakdown: still open by state, closed, cannot be checked, adding up to the book
    assert {r["key"]: r["n"] for r in o["open_by"]} == {"work_now": 1, "waiting": 1} and o["still_open"] == 2
    assert o["still_open"] + o["closed"] + o["cant_check"] == o["book"]
    for r in o["open_by"]:
        assert len(results.list_entries(store.conn, f"book:open:{r['key']}", TODAY)) == r["n"]
    assert d["first_run"] == "2026-04-10"


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
    # each weekly snapshot carries the second it was taken: two runs a second apart are the same figures
    for snap in d.get("snapshots") or []:
        if isinstance(snap, dict):
            snap.pop("at", None)
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
    # a new analysis makes the page work the figures out again, in the background: the page answers at once with the
    # stored figures and says they are being updated; the next load has the new ones
    from remitwatch import api_results
    c = sqlite3.connect(path)
    c.execute("INSERT INTO detection_runs (run_id, started_at, finished_at) VALUES ('r2', '2026-06-30T01:00:00', '2026-06-30T01:05:00')")
    c.commit()
    c.close()
    first = tc.get("/api/results").json()
    assert first["ready"] and first["updating"] is True and first["detect_run"] == d["detect_run"] and first["money"] == d["money"]
    api_results.wait_background()
    again = tc.get("/api/results").json()
    assert again["detect_run"] == "2026-06-30T01:05:00" and again["updating"] is False


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
    # the note says the figures come from an analysis, which on Vercel runs only on a press (the owner's decision 1)
    assert d["ready"] is False and d["host"] == "serverless" and "Run the analysis now" in d["note"] and d["money"] is None
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
    assert "async function resultsTab(v)" in js and "\u2014" not in js and "false positive" not in js.lower()
    assert "\u2014" not in Path(results.__file__).read_text()
    assert tuple(results.STATE_CATEGORIES) == tuple(api.MONEY_CATEGORIES)
    # every global the script declares starts with rs / RS_ (or is the page itself): nothing clashes with index.html
    import re as _re
    names = _re.findall(r"^(?:async\s+)?function\s+(\w+)|^const\s+(\w+)", js, _re.M)
    assert all((a or b).startswith(("rs", "RS_")) or (a or b) == "resultsTab" for a, b in names)


# ================================================================================================ review fixes (both reviews)
def _m(s, fid):
    return money_of(s, fid) or {"returned": 0, "taken_back": 0, "a": 0, "b": 0, "c": 0, "events": []}


def test_money_paid_before_the_flag_taken_back_then_paid_again_later_counts_only_the_new_money(store):
    # the same reprocessing as the one-remittance case above (50 before the flag reversed, 90 paid), a week apart: 40
    fid, _ = denial(store, 1, flagged="2026-04-10")
    paid(store, 1, on="2026-04-01", amount=50)
    remit(store, trace=F.check_no(160), paid_on="2026-05-20",
          claims=[{"pcn": F.claim_id(1, sub=2), "status": "22", "received": "2026-04-02", "lines": [ln("99213", -150, -50, [("CO", "45", -100)])]}])
    remit(store, trace=F.check_no(161), paid_on="2026-05-27",
          claims=[{"pcn": F.claim_id(1, sub=3), "status": "1", "received": "2026-05-10", "lines": [ln("99213", 150, 90, [("CO", "45", 60)])]}])
    # the same 50 paid again: nothing new
    fid2, _ = denial(store, 2, flagged="2026-04-10")
    paid(store, 2, on="2026-04-01", amount=50)
    remit(store, trace=F.check_no(260), paid_on="2026-05-20",
          claims=[{"pcn": F.claim_id(2, sub=2), "status": "22", "received": "2026-04-02", "lines": [ln("99213", -150, -50, [("CO", "45", -100)])]}])
    remit(store, trace=F.check_no(261), paid_on="2026-05-27",
          claims=[{"pcn": F.claim_id(2, sub=3), "status": "1", "received": "2026-05-10", "lines": [ln("99213", 150, 50, [("CO", "45", 100)])]}])
    run(store)
    got = {f: (_m(store, f)["returned"], _m(store, f)["taken_back"]) for f in (fid, fid2)}
    assert got == {fid: (4000, 0), fid2: (0, 0)}
    assert dash(store)["money"]["returned_cents"] == 4000
    ev = next(e for e in money_of(store, fid)["events"] if e["cents"] > 0)
    assert ev["counted_cents"] == 4000 and "$50.00 of it only paid back money the payer had paid before the flag" in ev["rule"]


def test_a_reprocessing_that_nets_to_zero_never_raises_the_top_line(store):
    def case(n, with_second):
        fid, _ = denial(store, n, flagged="2026-04-10")
        remit(store, trace=F.check_no(n * 100 + 70), paid_on="2026-04-01",
              claims=[{"pcn": F.claim_id(n, sub=2), "received": "2026-03-05", "lines": [ln("99213", 150, 50, [("CO", "45", 100)])]}])
        remit(store, trace=F.check_no(n * 100 + 71), paid_on="2026-05-20",
              claims=[{"pcn": F.claim_id(n, sub=2), "status": "22", "cfi": "16", "received": "2026-03-05", "lines": [ln("99213", -150, -50, [("CO", "45", -100)])]},
                      {"pcn": F.claim_id(n, sub=3), "status": "1", "cfi": "16", "received": "2026-03-05", "lines": [ln("99213", 150, 60, [("CO", "45", 90)])]}])
        if with_second:
            remit(store, trace=F.check_no(n * 100 + 72), paid_on="2026-06-10",
                  claims=[{"pcn": F.claim_id(n, sub=3), "status": "22", "cfi": "16", "received": "2026-03-05", "lines": [ln("99213", -150, -60, [("CO", "45", -90)])]},
                          {"pcn": F.claim_id(n, sub=4), "status": "1", "cfi": "16", "received": "2026-06-01", "lines": [ln("99213", 150, 60, [("CO", "45", 90)])]}])
        return fid
    one, two = case(1, False), case(2, True)
    run(store)
    assert {"without": _m(store, one)["returned"], "with": _m(store, two)["returned"]} == {"without": 1000, "with": 1000}


@pytest.mark.parametrize("signal,category", [("CREDIT_BALANCE", "refund"), ("FEE_SKIM", "fees"), ("TAKEBACK", "fees"),
                                             ("REMIT_NOT_POSTED", "fees")])
def test_a_refund_fee_or_unposted_item_marked_paid_is_never_money_returned(store, signal, category):
    # a credit refunded to the payer is money going out; a fee or takeback a person closes is not a payer's payment; an
    # unposted payment's money is found by EMA's posting of it, never by a mark
    tk = remit(store, trace=F.check_no(1), paid_on="2026-03-28", claims=[{"pcn": F.claim_id(1), "lines": [ln("99213", 150, 200, [])]}])
    item(store, finding_id="X1", signal=signal, category=category, line_key=f"{tk}-0-0", claim_key=f"{tk}-0", transaction_key=tk,
         first_seen="2026-04-01T08:00:00+00:00", work_state="work_now", shortfall_cents=5000)
    run(store, today="2026-04-02")
    actions.apply_status(store.conn, "X1", "resolved", notes="Refund sent to the payer", actor="Biller One", at="2026-04-20T10:00:00+00:00")
    store.conn.commit()
    run(store)
    assert dash(store)["money"]["returned_cents"] == 0 and money_of(store, "X1") is None


def test_emas_posting_of_an_835_payment_under_a_misread_number_on_the_same_day_counts_once(store):
    fid, _ = denial(store, 1, ema_link=F.link_no(1))
    ema_line(store, F.link_no(1), bill=F.bill_id(1))
    paid(store, 1, on="2026-04-20", amount=90)
    ema_pay(store, F.link_no(1), posted="2026-04-20", cents=9000, check="MISREAD", in_835=0)
    run(store)
    assert _m(store, fid)["returned"] == 9000 and len(_m(store, fid)["events"]) == 1


def test_emas_copy_of_an_835_payment_the_payer_then_reversed_is_not_counted(store):
    fid, _ = denial(store, 1, ema_link=F.link_no(1))
    ema_line(store, F.link_no(1), bill=F.bill_id(1))
    paid(store, 1, on="2026-04-20", amount=90)
    ema_pay(store, F.link_no(1), posted="2026-04-23", cents=9000, check="MISREAD", in_835=0)
    remit(store, trace=F.check_no(150), paid_on="2026-05-20",
          claims=[{"pcn": F.claim_id(1, sub=2), "status": "22", "received": "2026-04-02", "lines": [ln("99213", -150, -90, [("CO", "45", -60)])]}])
    run(store)
    assert _m(store, fid)["returned"] == 0


@pytest.mark.parametrize("match,cause,damaged", [("loose", None, 0), ("exact", None, 0), (None, "misnumbered", 0), (None, None, 1)])
def test_a_check_the_check_match_ties_to_an_835_is_that_835s_payment(store, match, cause, damaged):
    # ema_checks' own rule (line_outcomes): matched exactly or loosely, misnumbered, or a number a spreadsheet damaged
    fid, _ = denial(store, 1, ema_link=F.link_no(1))
    ema_line(store, F.link_no(1), bill=F.bill_id(1))
    tk = paid(store, 1, on="2026-04-20", amount=90)
    ema_pay(store, F.link_no(1), posted="2026-06-10", cents=9000, check="LOOSE10", in_835=0)      # 51 days after the 835
    store.conn.execute("""INSERT INTO ema_checks (check_no, payer, ins_paid, match, cause, damaged, trace_key, first_posted, last_posted)
                          VALUES ('LOOSE10', 'Anthem', 9000, ?, ?, ?, ?, '2026-06-10', '2026-06-10')""", (match, cause, damaged, tk))
    store.conn.commit()
    run(store)
    assert _m(store, fid)["returned"] == 9000 and {e["source"] for e in _m(store, fid)["events"]} == {"835"}


def test_emas_late_posting_of_the_items_own_remittance_is_money_paid_before_the_flag(store):
    tk = remit(store, trace=F.check_no(1), paid_on="2026-03-28", claims=[{"pcn": F.claim_id(1), "lines": [ln("99213", 150, 50, [("CO", "45", 100)])]}])
    item(store, finding_id="U1", signal="UNDERPAID_VS_OWN_RATE", category="underpayment", line_key=f"{tk}-0-0", claim_key=f"{tk}-0",
         transaction_key=tk, first_seen="2026-04-01T08:00:00+00:00", work_state="work_now", shortfall_cents=4000, ema_link=F.link_no(1))
    item(store, finding_id="P1", signal="REMIT_NOT_POSTED", category="fees", transaction_key=tk, patient_control_number=None,
         line_key=None, claim_key=None, first_seen="2026-04-01T08:00:00+00:00", work_state="work_now", shortfall_cents=5000,
         evidence={"ema_posted_cents": 0, "claim_paid_cents": 5000})
    ema_line(store, F.link_no(1), bill=F.bill_id(1))
    run(store, today="2026-04-02")
    ema_pay(store, F.link_no(1), posted="2026-05-25", cents=5000, check="LOOSE10", in_835=0)
    store.conn.execute("""INSERT INTO ema_checks (check_no, payer, ins_paid, match, trace_key, first_posted, last_posted)
                          VALUES ('LOOSE10', 'Anthem', 5000, 'loose', ?, '2026-05-25', '2026-05-25')""", (tk,))
    store.conn.commit()
    run(store)
    assert {f: _m(store, f)["returned"] for f in ("U1", "P1")} == {"U1": 0, "P1": 5000}


def test_an_unposted_payment_posted_under_another_number_is_money_found(store):
    # EMA keyed the 835's check under another number (the check match paired them: misnumbered)
    tk = remit(store, trace=F.check_no(7), paid_on="2026-03-20", claims=[{"pcn": F.claim_id(1), "lines": [ln("99213", 150, 120, [("CO", "45", 30)])]}])
    item(store, finding_id="P1", signal="REMIT_NOT_POSTED", category="fees", transaction_key=tk, patient_control_number=None,
         line_key=None, claim_key=None, first_seen="2026-04-01T08:00:00+00:00", work_state="work_now", shortfall_cents=12000,
         evidence={"ema_posted_cents": 0, "claim_paid_cents": 12000})
    run(store, today="2026-04-02")
    store.conn.execute("""INSERT INTO ema_checks (check_no, payer, ins_paid, cause, pair_trace, first_posted, last_posted)
                          VALUES ('TYPO7', 'Anthem', 12000, 'misnumbered', ?, '2026-04-20', '2026-04-20')""", (tk,))
    store.conn.commit()
    run(store)
    assert (_m(store, "P1")["b"], dash(store)["money"]["returned_cents"]) == (12000, 12000)


def test_a_paid_mark_never_brings_back_a_payment_the_payer_took_back(store):
    fid, _ = denial(store, 1)
    paid(store, 1, on="2026-04-20", amount=90)
    actions.apply_status(store.conn, fid, "resolved", actor="Biller One", at="2026-04-22T10:00:00+00:00")
    store.conn.commit()
    remit(store, trace=F.check_no(150), paid_on="2026-05-20",
          claims=[{"pcn": F.claim_id(1, sub=2), "status": "22", "received": "2026-04-02", "lines": [ln("99213", -150, -90, [("CO", "45", -60)])]}])
    run(store)
    m = dash(store)["money"]
    assert m["returned_cents"] == 0 and m["mark_taken_back_items"] == 1
    assert results.list_entries(store.conn, "mark_taken_back", TODAY) == [[fid, 0]]


def _two_services_one_group(s):
    tk = remit(s, trace=F.check_no(1), paid_on="2026-03-28",
               claims=[{"pcn": F.claim_id(1), "status": "4", "lines": [ln("99213", 150, 0, [("CO", "16", 150)]), ln("11102", 120, 0, [("CO", "16", 120)])]}])
    item(s, finding_id="C1", signal="DENIED_CODING", line_key=f"{tk}-0-0", claim_key=f"{tk}-0", transaction_key=tk,
         first_seen="2026-04-01T08:00:00+00:00", work_state="work_now")
    item(s, finding_id="L2", signal="DENIED_CODING", line_key=f"{tk}-0-1", claim_key=f"{tk}-0", transaction_key=tk,
         procedure_code="11102", billed_code="11102", first_seen="2026-04-02T08:00:00+00:00", work_state="work_now")
    s.conn.execute("INSERT INTO transactions (transaction_key, trace_number, payment_date, payer_name, superseded) VALUES ('TXW', 'W1', '2026-03-30', 'BLUE CROSS TEST', 0)")
    s.conn.execute("""INSERT INTO claims (claim_key, transaction_key, patient_control_number, payment_date, statement_from, claim_status_code,
                      charge_cents, paid_cents, payer_name, claim_filing_indicator) VALUES ('TXW-0', 'TXW', ?, '2026-03-30', '2026-03-02', '4',
                      27000, 0, 'BLUE CROSS TEST', '12')""", (F.claim_id(1, sub=4),))
    s.conn.commit()


def test_work_on_one_service_is_not_work_on_another_service_of_the_claim(store):
    _two_services_one_group(store)
    fax(store, "L2", "2026-04-05")
    remit(store, trace=F.check_no(2), paid_on="2026-04-20", claims=[{"pcn": F.claim_id(1, sub=2), "lines": [ln("99213", 150, 90, [("CO", "45", 60)])]}])
    run(store)
    assert money_of(store, "C1")["members"] == ["C1", "L2"]
    ev = money_of(store, "C1")["events"][0]
    assert (ev["bucket"], ev["work_kind"]) == ("c", None)


def test_a_second_payer_item_on_another_service_never_makes_the_primarys_payment_found_money(store):
    _two_services_one_group(store)
    store.conn.execute("UPDATE findings SET signal='SECONDARY_NOT_BILLED' WHERE finding_id='L2'")
    store.conn.commit()
    remit(store, trace=F.check_no(2), paid_on="2026-04-20", claims=[{"pcn": F.claim_id(1, sub=2), "lines": [ln("99213", 150, 90, [("CO", "45", 60)])]}])
    run(store)
    assert money_of(store, "C1")["events"][0]["bucket"] == "c"


def test_on_a_second_payer_item_only_the_other_payers_money_is_found(store):
    # the primary pays more on the same line (a reprocessing): not money from the second payer nobody had billed
    tk = remit(store, trace=F.check_no(1), paid_on="2026-03-28", claims=[{"pcn": F.claim_id(1), "lines": [ln("99213", 150, 80, [("PR", "2", 20), ("CO", "45", 50)])]}])
    item(store, finding_id="S1", signal="SECONDARY_NOT_BILLED", category="denial", line_key=f"{tk}-0-0", claim_key=f"{tk}-0",
         transaction_key=tk, first_seen="2026-04-01T08:00:00+00:00", work_state="work_now", shortfall_cents=2000)
    remit(store, trace=F.check_no(2), paid_on="2026-05-02", claims=[{"pcn": F.claim_id(1, sub=2), "lines": [ln("99213", 150, 90, [("CO", "45", 60)])]}])
    run(store)
    assert {(e["bucket"], e["cents"]) for e in money_of(store, "S1")["events"] if e["cents"] > 0} == {("c", 9000)}


def test_work_recorded_after_the_money_came_is_not_worked_in_the_lift(store):
    n = _population(store, 0, 0, 3, 3)          # 6 unworked items, 3 paid on 2026-04-20
    for k in range(3):
        fid, _ = denial(store, n + k)
        paid(store, n + k, on="2026-04-20", amount=100)
        event(store, fid, "mark_posted", at="2026-04-25T10:00:00+00:00", actor="Biller One")     # posted after the money
    # a call logged after the money came is not what brought it either
    fid, _ = denial(store, n + 3)
    paid(store, n + 3, on="2026-04-20", amount=100)
    event(store, fid, "call", at="2026-04-22T10:00:00+00:00", actor="Biller One")
    run(store)
    lf = dash(store)["lift"]
    assert lf["worked"]["n"] == 0 and lf["incremental_cents"] == 0 and lf["unworked"]["n"] == 10
    # posting a payment is bookkeeping: on an item with no money yet it does not make it worked either
    fid2, _ = denial(store, n + 4)
    event(store, fid2, "posted", at="2026-04-25T10:00:00+00:00", actor="Biller One")
    run(store)
    assert dash(store)["lift"]["worked"]["n"] == 0


def test_with_fewer_than_20_cases_the_email_and_the_lift_adjusted_roi_state_no_measured_rate(store):
    _population(store, 2, 1, 1, 2)
    run(store)
    joined = " ".join(results.email_lines(store.conn, TODAY))
    rec = results.save_settings({"costs": {"hosting_month": 40}}, who="o", base_version="", conn=store.conn)
    r = results.roi(dash(store), rec, TODAY)
    assert dash(store)["lift"]["enough"] is False
    assert "% (3 items)" not in joined and "not enough similar items yet" in joined
    assert r["roi_conservative"] is None and r["roi"] is not None


def _frozen_week_with_late_money(s):
    denial(s, 1)
    paid(s, 1, on="2026-04-20")
    run(s, today="2026-04-22")
    run(s, today="2026-05-11")              # the week of 2026-04-20 is frozen at $90
    denial(s, 2)
    paid(s, 2, on="2026-04-21", amount=50)  # money dated in that week, seen later
    run(s, today="2026-05-20")


def test_a_frozen_weeks_figure_opens_the_list_frozen_with_it_and_todays_reading_beside_it(store):
    _frozen_week_with_late_money(store)
    week = next(w for w in results.scoreboard(store.conn, "2026-05-20")["weeks"] if w["week"] == "2026-04-20")
    listed = results.list_entries(store.conn, "returned:week:2026-04-20", "2026-05-20")
    assert week["frozen"] and week["recovered_cents"] == 9000 == sum(c for _f, c in listed) and listed == [["F1", 9000]]
    assert week["now_cents"] == 14000 and week["frozen_at"]
    now = results.list_entries(store.conn, "returned:week_now:2026-04-20", "2026-05-20")
    assert sum(c for _f, c in now) == 14000


def test_a_frozen_week_whose_payment_was_taken_back_still_opens_its_list(store):
    denial(store, 1)
    paid(store, 1, on="2026-04-20")
    run(store, today="2026-05-11")
    remit(store, trace=F.check_no(150), paid_on="2026-05-25",
          claims=[{"pcn": F.claim_id(1, sub=2), "status": "22", "received": "2026-04-02", "lines": [ln("99213", -150, -90, [("CO", "45", -60)])]}])
    run(store, today="2026-06-30")
    week = next(w for w in results.scoreboard(store.conn, "2026-06-30")["weeks"] if w["week"] == "2026-04-20")
    assert week["recovered_cents"] == 9000 and week["now_cents"] == 0
    assert results.list_entries(store.conn, "returned:week:2026-04-20", "2026-06-30") == [["F1", 9000]]


def test_each_hours_saved_figure_opens_a_list_of_the_items_it_counts(store):
    fid, _ = denial(store, 1)
    fax(store, fid, "2026-04-05")
    fax(store, fid, "2026-05-05")
    event(store, fid, "packet", at="2026-04-04T10:00:00+00:00", actor="packets")
    event(store, fid, "packet", at="2026-04-06T10:00:00+00:00", actor="packets")
    item(store, finding_id="CARRIER", evidence={"folded": [{"finding_id": "x1"}, {"finding_id": "x2"}, {"finding_id": "x3"}]})
    run(store)
    rows = {r["key"]: r for r in results.hours_saved(dash(store)["hours_counts"], results.read_settings(store.conn))["rows"]}
    assert {k: (r["n"], r["items"]) for k, r in rows.items()} == {"investigate": (0, 0), "packet": (2, 1), "fax": (2, 1), "duplicate": (3, 1)}
    for k, r in rows.items():
        assert results.list_rows(store.conn, f"hours:{k}", today=TODAY, per=500)["total"] == r["items"], k


def test_the_hours_count_and_its_list_come_from_one_run(store):
    denial(store, 1, work_state="done")
    denial(store, 2, work_state="done")
    run(store)
    # a biller closes one of them as paid by hand: the figure and its list stay the run's until the next one
    store.conn.execute("UPDATE findings SET status='resolved', recovered_cents=9000, recovered_date=? WHERE finding_id='F1'", (TODAY,))
    store.conn.commit()
    shown = results.hours_saved(dash(store)["hours_counts"], results.read_settings(store.conn))["rows"][0]
    assert shown["items"] == len(results.list_entries(store.conn, "hours:investigate", TODAY)) == 2


def test_a_frozen_week_keeps_its_hours_at_the_minutes_then_in_force(store):
    fid, _ = denial(store, 1)
    fax(store, fid, "2026-04-21")
    paid(store, 1, on="2026-04-20")
    run(store, today="2026-04-22")
    run(store, today="2026-05-11")
    snap = {s["week"]: s for s in results.snapshots(store.conn)}["2026-04-20"]
    assert snap["final"] and snap["hours"]["hours"] == round(10 / 60, 1)
    assert {r["key"]: r["n"] for r in snap["hours"]["rows"]}["fax"] == 1


def test_a_serverless_scoreboard_shows_the_frozen_weeks_the_server_shows(cloud_api):
    tc, st, s, publish = cloud_api
    publish(True)
    from remitwatch import cloud
    cloud.SERVING_COPY.ensure(force=True)
    server_weeks = {w["week"]: w for w in results.scoreboard(s.conn, TODAY)["weeks"]}
    frozen = sorted(w for w, v in server_weeks.items() if v.get("frozen"))
    assert frozen
    sls_weeks = {w["week"]: w for w in tc.get("/api/results/scoreboard").json()["weeks"]}
    assert [w for w in frozen if sls_weeks.get(w, {}).get("frozen")] == frozen
    for w in frozen:
        listed = tc.get("/api/results/list", params={"key": f"returned:week:{w}", "per": 500}).json()
        assert sum(r["cents"] for r in listed["items"]) == sls_weeks[w]["recovered_cents"]
    assert [x["week"] for x in tc.get("/api/results").json()["snapshots"] if x.get("final")] == frozen


def test_money_returned_to_date_survives_the_archive_taking_the_settled_bill(store, tmp_path):
    from remitwatch import archive
    fid, _ = denial(store, 1)
    fax(store, fid, "2026-04-05")
    paid(store, 1, on="2026-04-20")
    run(store)
    assert (dash(store)["money"]["returned_cents"], dash(store)["money"]["a_cents"]) == (9000, 9000)
    store.conn.execute("UPDATE findings SET status='resolved', auto_note='Paid on a later remittance', work_state='done' WHERE finding_id=?", (fid,))
    store.conn.execute("INSERT INTO detection_runs (run_id, started_at, finished_at, version) VALUES ('rX', '2029-06-30', '2029-06-30', 'test')")
    archive.ensure_schema(store.conn)
    archive._meta_put(store.conn, archive.META_FROZEN, {"2026": {"at": "2029-06-30", "digest": "x", "rates": 0}})
    store.conn.commit()
    res = archive.run(store.conn, sink=archive.DirSink(str(tmp_path / "archive")), today="2029-06-30", run_id="a1")
    assert res.get("bills", 0) >= 1
    run(store, today="2029-06-30")
    d = dash(store)
    assert (d["money"]["returned_cents"], d["money"]["a_cents"]) == (9000, 9000)
    assert d["work"]["faxes_sent"] == 1 and {k["key"] for k in d["work"]["kinds"]} == {"fax"}
    assert money_of(store, fid)["events"][0]["rule"].startswith("BLUE CROSS TEST paid $90.00")


def test_a_fax_appeal_counts_once_as_a_fax_and_is_credited_as_a_fax(store):
    fid, _ = denial(store)
    fax(store, fid, "2026-04-05")
    # what the app's fax send does right after it goes out: it marks the item Sent in the sender's name
    event(store, fid, "manual", at="2026-04-05T12:00:05+00:00", frm="open", to="appealed", actor="Biller One",
          note="Faxed to Anthem / BCBS at (555) 010-0000 (fax F1, 3 pages).")
    paid(store, on="2026-04-20")
    run(store)
    w = dash(store)["work"]
    assert {k["key"]: (k["items"], k["n"]) for k in w["kinds"]} == {"fax": (1, 1)}
    assert {p["key"]: p["n"] for p in w["people"]} == {"Biller One": 1}
    assert money_of(store, fid)["events"][0]["work_kind"] == "fax"


def test_mark_billed_counts_once_and_a_logged_call_counts_as_a_call(store):
    billed, _ = denial(store, 1)
    # Mark billed: its own 'billed' line and the status Sent it sets ("Billed: ...")
    event(store, billed, "billed", at="2026-04-05T10:00:00+00:00", actor="Biller One", note="Billed: the second payer")
    event(store, billed, "manual", at="2026-04-05T10:00:01+00:00", frm="open", to="appealed", actor="Biller One", note="Billed: the second payer")
    called, _ = denial(store, 2)
    # Log what happened, the payer is working on it: a status Sent whose note says it was a call or portal check
    event(store, called, "manual", at="2026-04-05T10:00:00+00:00", frm="open", to="appealed", actor="Biller Two",
          note="Call or portal check: reprocessing, reference 12")
    # a fax line that never settled, marked Sent by the app later: still one fax of work
    unsettled, _ = denial(store, 3)
    fax(store, unsettled, "2026-04-05", status="sending")
    event(store, unsettled, "manual", at="2026-04-06T10:00:00+00:00", frm="open", to="appealed", actor="Biller One",
          note="Faxed to Anthem / BCBS at (555) 010-0000 (fax not settled yet).")
    run(store)
    kinds = {k["key"]: (k["items"], k["n"]) for k in dash(store)["work"]["kinds"]}
    assert kinds == {"billed": (1, 1), "call": (1, 1), "fax": (1, 1)}


def test_the_spot_checks_follow_the_practices_own_day(server_api):
    tc, api, _path = server_api
    # the page sends its own date: a browser in the practice's time zone a day behind the server keeps its day's picks
    got = tc.get("/api/results", params={"day": "2026-06-29"}).json()
    assert got["day"] == "2026-06-29" and {p["day"] for p in got["spot"]["today"]} == {"2026-06-29"}
    assert tc.get("/api/results/spot", params={"day": "2026-06-29"}).json()["items"] == got["spot"]["today"]
    # a date far from the server's is not a time zone: the server's own day is used
    assert tc.get("/api/results", params={"day": "2026-01-01"}).json()["day"] == TODAY
    sb = tc.get("/api/results/scoreboard", params={"day": "2026-06-29"}).json()
    assert sb["spot"] == {"picked": 5, "answered": 0, "due": 5}


def test_hours_saved_before_the_first_run_on_a_serverless_host_shows_no_count(cloud_api):
    tc, st, s, publish = cloud_api
    denial(s, 60, work_state="done")
    publish(False)
    row = tc.get("/api/results").json()["hours"]["rows"][0]
    assert row["n"] is None and row["hours"] is None


class _Counting:
    def __init__(self, inner):
        self.inner, self.calls = inner, []

    def __getattr__(self, name):
        fn = getattr(self.inner, name)
        if name in ("get", "meta", "put", "list", "get_chunks"):
            def wrapped(*a, **kw):
                self.calls.append((name, a[0] if a else ""))
                return fn(*a, **kw)
            return wrapped
        return fn


def test_a_serverless_results_page_load_reads_the_state_folder_a_few_times_at_most(cloud_api, monkeypatch):
    from remitwatch import api_results
    tc, st, s, publish = cloud_api
    publish(True)
    counting = _Counting(st)
    monkeypatch.setattr(api_results, "_state", lambda: counting)
    tc.get("/api/results")                      # the first visit of the day picks the day's spot checks
    counting.calls.clear()
    tc.get("/api/results")                      # every later visit: one read each of the owner's figures and this year's checks
    assert len(counting.calls) <= 2, counting.calls


def test_a_spot_answer_returns_the_agreement_rates_so_the_page_never_reloads(server_api):
    tc, api, _path = server_api
    fid = tc.get("/api/results/spot").json()["items"][0]["finding_id"]
    r = tc.post("/api/results/spot", json={"day": TODAY, "finding_id": fid, "answer": "agree", "name": "Ana"},
                headers={"X-Requested-With": "remitwatch"}).json()
    assert r["ok"] and r["summary"]["answered"] == 1 and r["summary"]["agree"] == 1 and r["who"] == "Ana"


# ================================================================================================ the page's own code, in node
RS_PAGE_JS = r"""
const fs = require('fs'), vm = require('vm'), path = require('path');
const read = f => fs.readFileSync(path.join(process.argv[2], f), 'utf8');
const html = read('index.html');
const grab = (src, re) => { const m = src.match(re); if (!m) throw new Error('not found: ' + re); return m[0]; };
const helpers = [/const money = [^\n]*\n/, /const money2 = [^\n]*\n/, /const esc = [^\n]*\n/, /function qs\([^\n]*\n/].map(re => grab(html, re)).join('\n');
const calls = [];
const ctx = { console, state: {}, $: () => null, document: { querySelector: () => null }, api: async (p, o) => { calls.push([p, o]); return {}; } };
vm.createContext(ctx);
for (const src of [helpers, read('results.js')]) vm.runInContext(src, ctx);
ctx.state.rsData = { labels: { states: { work_now: 'Work now', waiting: 'Waiting', cant_check: "Can't be checked" },
  closures: { paid: 'Paid', let_go: 'Let go by a person' }, work: { fax: 'Fax or appeal sent through RemitWatch', let_go: 'x' },
  signals: { DENIED_AUTH: 'Denied: no authorization' }, minutes: { fax: 'The minutes saved per fax' } } };
const out = {};
(async () => {
  ctx.state.ownerKey = ''; await ctx.rsLoad();
  ctx.state.ownerKey = 'owner-key'; await ctx.rsLoad();
  out.headers = calls.map(([p, o]) => [p.split('?')[0], ((o || {}).headers || {})['X-Owner-Password'] || null]);
  out.day = /^\d{4}-\d{2}-\d{2}$/.test(ctx.rsDay());
  const spot = ctx.rsSpotHtml({ spot: { today: [{ finding_id: 'f1', signal: 'DENIED_AUTH', stratum: 'work_now', cents: 4000, day: '2026-06-30' }],
    reasons: { already_worked: 'Already worked', other: 'Other' }, summary: {} }, labels: ctx.state.rsData.labels, today: '2026-06-30' });
  out.first_reason = (spot.match(/<select class="rs-reason"[^>]*>\s*<option value="([^"]*)"/) || [])[1];
  out.save_disabled = /<button class="rs-send" disabled>/.test(spot);
  out.titles = ['state:work_now', 'state:cant_check', 'lift:worked:paid', 'lift:unworked:paid', 'work:let_go', 'work:person:Biller Two',
                'cant:Separate payee', 'hours:investigate', 'denial_fix:0', 'returned:week:2026-10-26', 'returned:month:2026-10',
                'closed:let_go', 'book:open:work_now', 'mark_taken_back', 'returned:action:fax', 'work:fax'].map(k => ctx.rsTitle(k));
  out.dash_tile = ctx.rsTile('Recovered', '–', 'after the first run', 'returned');
  out.full_tile = ctx.rsTile('Recovered', '$90.00', '1 item', 'returned', '', '', 9000);
  out.cents = [ctx.rsM(40), ctx.rsM(149), ctx.rsM(-250), ctx.rsBig(4000), ctx.rsBig(1234567)];
  out.hours = ctx.rsHoursHtml({ hours: { hours: 0.7, rows: [{ key: 'fax', label: 'Faxes sent', n: 2, items: 1, minutes: 10, hours: 0.3 },
                                                       { key: 'packet', label: 'Packets', n: 1, items: 1, minutes: 20, hours: 0.3 }] },
                               owner_open: false, owner_locked: true, labels: ctx.state.rsData.labels });
  out.hours_none = ctx.rsHoursHtml({ hours: { hours: null, rows: [{ key: 'fax', label: 'Faxes sent', n: null, items: null, minutes: 10, hours: null }] }, owner_open: true });
  out.score = ctx.rsScoreHtml({ recovered_cents: 9000, lost_cents: 0, at_risk_cents: 0, given_up_cents: 4000, counts: { recovered: 1, lost: 0, at_risk: 0, given_up: 1 },
    given_up_source: 'results', given_up_check: { cents: 5000, n: 2 }, basis: {},
    weeks: [{ week: '2026-04-20', recovered_cents: 9000, now_cents: 14000, lost_cents: 0, at_risk_cents: null, given_up_cents: 0, frozen: true, frozen_at: '2026-05-11' }] });
  out.money = ctx.rsMoneyHtml({ money: { returned_cents: 9000, a_cents: 0, b_cents: 0, c_cents: 9000, items: 1, a_items: 0, b_items: 0, c_items: 1,
    taken_back_cents: 0, mark_taken_back_items: 2, first_flag: '2026-04-01', anyway: { cents: 3000, rate: 0.33, n: 24, paid: 8 } } });
  out.book = ctx.rsOutcomesHtml({ outcomes: { book: 4, closed: 1, closed_rate: 0.25, closed_by: [{ key: 'paid', label: 'Paid', n: 1, rate: 0.25 }],
    open_by: [{ key: 'work_now', label: 'Work now', n: 2 }], still_open: 2, cant_check: 1, cant_rate: 0.25, cant_by: [{ key: 'Separate payee', n: 1 }],
    worked: 0, waiting_no_money: 0, denied_again: 0, win_by_kind: [] }, live: { states: { work_now: { n: 9, cents: 100 } } }, labels: ctx.state.rsData.labels, first_run: '2026-04-10' });
  out.book_live = ctx.rsOutcomesHtml({ outcomes: null, live: { states: { work_now: { n: 9, cents: 100 } } }, labels: ctx.state.rsData.labels });
  out.roi = ctx.rsRoiHtml({ roi: { costs_set: true, enough: false, roi: 2.5, roi_conservative: null, returned_cents: 10000, cost_cents: 4000, months: [], basis: 'b' } });
  const send = (read('results.js').match(/const send = async \(answer, reason, note\) => \{([\s\S]*?)\n    \};/) || [])[1] || '';
  out.send_found = send.length > 0;
  out.send_renders = /\brender\(\)/.test(send);
  const save = (read('results.js').match(/const saveVals = async \(group, sel, msgId\) => \{([\s\S]*?)\n  \};/) || [])[1] || '';
  out.save_renders = /\brender\(\)/.test(save);
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
"""


@pytest.fixture(scope="module")
def rs_page(tmp_path_factory):
    import os
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    script = tmp_path_factory.mktemp("rs") / "results_page.js"
    script.write_text(RS_PAGE_JS, encoding="utf-8")
    res = subprocess.run([node, str(script), os.path.join(os.path.dirname(results.__file__), "static")], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr[-3000:]
    return json.loads(res.stdout)


def test_the_page_sends_the_owners_password_it_holds_so_the_owner_can_set_costs_and_minutes(rs_page):
    assert rs_page["headers"] == [["/api/results", None], ["/api/results", "owner-key"]] and rs_page["day"]
    # without the password the panels offer the owner a place to type it, instead of sending them to another tab
    assert 'class="rs-unlock' in rs_page["hours"] and "Providers tab" not in rs_page["hours"]


def test_a_disagreement_starts_with_no_reason_and_an_answer_or_a_save_never_redraws_the_page(rs_page):
    assert rs_page["first_reason"] == "" and rs_page["save_disabled"] is True
    assert rs_page["send_found"] and rs_page["send_renders"] is False and rs_page["save_renders"] is False


def test_every_list_title_is_plain_words(rs_page):
    for t in rs_page["titles"]:
        assert t != "Items" and "_" not in t and not re.search(r"\b(work_now|cant_check|let_go|hours|lift|denial_fix)\b", t), t
    assert "Oct 26, 2026" in rs_page["titles"][9] and "October 2026" in rs_page["titles"][10]


def test_money_shows_to_the_cent_and_a_dash_opens_nothing(rs_page):
    assert rs_page["cents"] == ["$0.40", "$1.49", "-$2.50", "$40.00", "$12,346"]
    assert "data-key" not in rs_page["dash_tile"] and "first one after this update" in rs_page["dash_tile"]
    assert 'data-key="returned"' in rs_page["full_tile"] and "Exactly $90.00" in rs_page["full_tile"]


def test_counts_name_their_items_frozen_weeks_say_so_and_the_open_book_adds_up(rs_page):
    assert ">2 on <a" in rs_page["hours"] and "1 item</a>" in rs_page["hours"]
    assert "Hours saved: 0.7" in rs_page["hours"] and "rs-wide" in rs_page["hours"] and "rs-narrow" in rs_page["hours"]
    assert "–" in rs_page["hours_none"] and "first one after this update" in rs_page["hours_none"]
    assert 'data-key="returned:week:2026-04-20"' in rs_page["score"] and 'data-key="returned:week_now:2026-04-20"' in rs_page["score"]
    assert "The self-check counts $50.00 on 2 items" in rs_page["score"] and 'data-key="given_up"' in rs_page["score"]
    assert 'data-key="mark_taken_back"' in rs_page["money"] and 'data-key="lift:z"' in rs_page["money"] and 'data-key="lift:z:paid"' in rs_page["money"]
    assert 'data-key="book:open:work_now"' in rs_page["book"] and "since the first run on Apr 10, 2026" in rs_page["book"]
    assert 'data-key="state:work_now"' in rs_page["book_live"]
    assert "not enough cases yet" in rs_page["roi"] and "Lift-adjusted" not in rs_page["roi"]


def test_a_run_that_could_not_work_part_of_the_money_out_keeps_what_was_saved(store, monkeypatch):
    fid, _ = denial(store)
    paid(store, on="2026-04-20")
    run(store)
    saved = store.conn.execute("SELECT data FROM results_money WHERE finding_id=?", (fid,)).fetchone()[0]

    def broken(*a, **kw):
        raise RuntimeError("the facts could not be read")
    monkeypatch.setattr(states, "facts_of", broken)
    out = run(store, today="2026-07-01")
    assert out["errors"] and dash(store)["errors"]                      # the gap is said on the figures
    assert store.conn.execute("SELECT data FROM results_money WHERE finding_id=?", (fid,)).fetchone()[0] == saved


def test_a_payment_from_an_unknown_payer_is_never_counted_as_a_second_payers(store):
    tk = remit(store, trace=F.check_no(1), paid_on="2026-03-28", claims=[{"pcn": F.claim_id(1), "lines": [ln("99213", 150, 80, [("PR", "2", 20), ("CO", "45", 50)])]}])
    item(store, finding_id="S1", signal="SECONDARY_NOT_BILLED", category="denial", line_key=f"{tk}-0-0", claim_key=f"{tk}-0",
         transaction_key=tk, first_seen="2026-04-01T08:00:00+00:00", work_state="work_now", shortfall_cents=2000)
    remit(store, trace=F.check_no(2), paid_on="2026-05-02", payer="",
          claims=[{"pcn": F.claim_id(1, sub=2), "status": "2", "lines": [ln("99213", 150, 20, [("CO", "45", 130)])]}])
    run(store)
    assert {e["bucket"] for e in money_of(store, "S1")["events"] if e["cents"] > 0} == {"c"}


def test_a_second_payer_item_whose_own_payer_is_unknown_never_counts_a_payment_as_found(store):
    tk = remit(store, trace=F.check_no(1), paid_on="2026-03-28", payer="",
               claims=[{"pcn": F.claim_id(1), "lines": [ln("99213", 150, 80, [("PR", "2", 20), ("CO", "45", 50)])]}])
    item(store, finding_id="S1", signal="SECONDARY_NOT_BILLED", category="denial", line_key=f"{tk}-0-0", claim_key=f"{tk}-0",
         transaction_key=tk, payer_name="", payer_group="Unknown", first_seen="2026-04-01T08:00:00+00:00", work_state="work_now",
         shortfall_cents=2000)
    remit(store, trace=F.check_no(2), paid_on="2026-05-02",
          claims=[{"pcn": F.claim_id(1, sub=2), "status": "2", "lines": [ln("99213", 150, 20, [("CO", "45", 130)])]}])
    run(store)
    assert {e["bucket"] for e in money_of(store, "S1")["events"] if e["cents"] > 0} == {"c"}


def test_a_part_of_the_figures_that_could_not_be_worked_out_is_in_the_runs_log_with_its_stack(store, monkeypatch, tmp_path):
    """A failure the figures contain (an item's facts, the denials chart) is never swallowed: it is on the figures and
    in the run's log with its stack, its message scrubbed."""
    from remitwatch import runlog
    denial(store)
    paid(store, on="2026-04-20")

    def broken_facts(*a, **kw):
        raise RuntimeError("could not read the claim of Pat Invented 1234567")

    def broken_denials(*a, **kw):
        raise sqlite3.OperationalError("disk I/O error near Pat Invented")
    monkeypatch.setattr(states, "facts_of", broken_facts)
    monkeypatch.setattr(results, "_denials", broken_denials)
    rl = runlog.start_run("cycle", logs_dir=str(tmp_path / "logs"), host="server")
    try:
        out = run(store)
    finally:
        rl.finish()
    assert len(out["errors"]) == 2
    got = [(e["step"], e["type"]) for e in rl.manifest["errors"]]
    assert ("results.facts_of", "RuntimeError") in got and ("results.denials", "OperationalError") in got
    assert all(e["stack"] for e in rl.manifest["errors"])
    logged = json.dumps(rl.manifest) + "".join(p.read_text(errors="replace") for p in (tmp_path / "logs").rglob("*")
                                               if p.is_file() and not p.name.endswith(".gz"))
    for piece in ("Pat Invented", "1234567"):
        assert piece not in logged
