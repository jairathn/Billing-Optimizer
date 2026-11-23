"""
Every item's story, verdict and next step (remitwatch.story, amendment 9): one chronological list of every event on the
line, tagged with its source; a balance line that reconciles to the cent or shows the gap and its cause; the surgery
center's facility claim shown apart; one plain sentence naming the state and its decisive rows, with how strong the proof
is; and the exact next step with its buttons. On a server with the posting ledger and on the serverless dashboard's
in-memory copy without it.

Every row is invented (tests/ema_fixtures.py, and the builders of tests/test_states.py).
"""
from __future__ import annotations

import datetime as dt
import sqlite3

import pytest

from remitwatch import cloud, ema_checks as ec, ema_ledger as el, ema_reports as er, states, story
from remitwatch.store import Store
from tests import ema_fixtures as F
from tests.test_states import (BCBS, S, SVC, TODAY, _secondary_world, adj, charge, denied, event, item, later, line_key, ln, load,
                               new_store, pay, patient_pay, remit, short_window_item)

D = dt.date
P = F.PAYERS


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(er, "CLAIM_HOST_SUFFIX", F.HOST_SUFFIX)
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    for k in ("VERCEL", "REMITWATCH_EMA_REPORTS", "REMITWATCH_EMA_CLAIM_URL", "REMITWATCH_EMA_PAYER_ALIASES",
              "REMITWATCH_EMA_LEDGER", "REMITWATCH_EMA_REPORT_KEEP", "MODMED_CLIENT_ID"):
        monkeypatch.delenv(k, raising=False)
    ec._LAST_POST.clear()
    states._RESPONSE_CACHE.clear()
    states._RECOVERY_CACHE.clear()


@pytest.fixture
def store(tmp_path):
    s = new_store(str(tmp_path / "store.sqlite"))
    yield s
    s.close()


@pytest.fixture
def ledger(tmp_path):
    lg = el.Ledger.open(str(tmp_path / "ema_ledger.sqlite"))
    yield lg
    lg.close()


def dashboard_copy(store, tmp_path) -> Store:
    """The serverless dashboard's copy: what the run copies out of the full store (cloud.build_serving: no ledger, no key
    columns, only the claims items name), opened as a compact in-memory store."""
    path = str(tmp_path / "serving.sqlite")
    store.conn.commit()
    cloud.build_serving(store.conn, path)
    mem = Store(":memory:", compact=True)
    src = sqlite3.connect(path)
    src.backup(mem.conn)
    src.close()
    return mem


def texts(out: dict) -> list[str]:
    """Every sentence the story, verdict and next step show."""
    t = [r["text"] for r in out["story"]["rows"]] + list(out["story"]["notes"]) + [out["story"]["balance"].get("text") or ""]
    t += [out["verdict"]["sentence"], out["next_step"]["action"], out["next_step"]["instructions"]] + list(out["next_step"]["steps"])
    return t


def plain(out: dict) -> None:
    """The wording rules: no em dashes, no jargon, no bare 'review' as an instruction."""
    for t in texts(out):
        assert "—" not in t and "–" not in t, t
        assert "false positive" not in t.lower(), t
    for s_ in out["next_step"]["steps"]:
        assert not s_.lower().startswith("review"), s_


# ------------------------------------------------------------------------------------------------------- worlds
def auto_writeoff_world(store, ledger, tmp_path, *, handled=None, status="Resolved/Paid", reason=("CO", "197"), **kw):
    """The item's 99213 denied (CO-197 by default) by Blue Cross on 835 check 1; EMA posted the $0 answer and wrote the
    $150 off the same day; Production reaches May 1. handled: EMA's ERA export rows for the line (adjustment status by
    reason)."""
    tk = denied(store, reason=reason)
    item(store, line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk, **kw)
    load(store, ledger, tmp_path, [charge(F.link_no(1), status=status), pay(F.link_no(1), F.check_no(1), 0, status=status),
                                   adj(F.link_no(1), f"{reason[0]}-{reason[1]}", 150, status=status), later()])
    if handled:
        from remitwatch.link import DDL
        store.conn.executescript(DDL)
        for carc, st in handled:
            store.conn.execute("INSERT INTO line_handling (line_key, claim_id, cpt, service_date, carc, adjustment_status, adjustments_cents, "
                               "transaction_date) VALUES (?,?,?,?,?,?,?,?)", (line_key(store, tk), F.claim_id(1), "99213", S, carc, st, 15000,
                                                                              "2026-03-31"))
        store.conn.commit()
    return tk


# ================================================================================================ the story
def test_the_story_lists_every_source_in_date_order_with_codes_and_meanings(store, ledger, tmp_path):
    auto_writeoff_world(store, ledger, tmp_path)
    ar = [F.ar_row(claim=F.claim_id(1), link=F.link_no(1), svc=SVC, cpt="99213", current=150, denial=150, status="Denied",
                   note="Test note: called the payer", aging=D(2026, 4, 28))]
    load(store, ledger, tmp_path, [], ar_rows=ar)
    store.conn.execute("""INSERT INTO ema_rows (row_fingerprint, family, claim_id, service_date, cpt, claim_status, claim_submission_date)
                          VALUES ('fp1', 'claim_status', ?, ?, '99213', 'Denied', '2026-03-04')""", (F.claim_id(1), S))
    event(store, "F1", "manual", at="2026-04-02T09:00:00", frm="open", to="in_progress", actor="Biller One")
    store.conn.execute("UPDATE findings SET status='in_progress'")
    store.conn.commit()
    out = story.explain(store.conn, "F1", ledger=ledger, today=TODAY)
    rows = out["story"]["rows"]
    assert {r["source"] for r in rows} >= {"835", "production", "ar", "claim_status", "remitwatch"}
    for r in rows:
        assert {"n", "date", "source", "kind", "payer", "amount_cents", "codes", "text", "decisive"} <= set(r)
        assert r["source"] in story.SOURCES
    dated = [r["date"] for r in rows if r["date"]]
    assert dated == sorted(dated) and [r["n"] for r in rows] == list(range(1, len(rows) + 1))
    den = next(r for r in rows if r["source"] == "835")
    assert den["codes"][0] == {"code": "CO-197", "meaning": "Precertification/authorization/notification/pre-treatment absent.",
                               "amount_cents": 15000} or den["codes"][0]["code"] == "CO-197"
    assert "claim status 1" in [c["code"] for c in den["codes"]] and "own_remit" in den["tags"]
    wo = next(r for r in rows if r["kind"] == "adjustment")
    assert "posted the day the remittance was posted" in wo["text"] and "adj_at_posting" in wo["tags"]
    assert any(r["source"] == "ar" and "a billing note exists" in r["text"] for r in rows)
    assert story.AR_WINDOW in out["story"]["notes"]
    assert any(r["source"] == "remitwatch" and "Biller One set it to Started" in r["text"] for r in rows)
    plain(out)


def test_the_balance_reconciles_to_the_cent_on_the_server_and_on_the_dashboard_copy(store, ledger, tmp_path):
    tk = denied(store, reason=("CO", "16"))
    item(store, signal="DENIED_MISSING_INFO", carc="16", line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    remit(store, trace=F.check_no(2), paid_on="2026-04-20", claims=[{"pcn": F.claim_id(1, sub=2), "pccn": "PCN-B", "received": "2026-04-05",
                                                                    "lines": [ln("99213", 150, 80, [("CO", "45", 50), ("PR", "2", 20)], lcn="1")]}])
    load(store, ledger, tmp_path, [charge(F.link_no(1)), pay(F.link_no(1), F.check_no(1), 0),
                                   pay(F.link_no(1), F.check_no(2), 80, posted=D(2026, 4, 22)),
                                   adj(F.link_no(1), "CO-45", 50, contractual=True, posted=D(2026, 4, 22)),
                                   patient_pay(20, posted=D(2026, 4, 25)), later()])
    out = story.explain(store.conn, "F1", ledger=ledger, today=TODAY)
    b = out["story"]["balance"]
    assert out["story"]["reconciles"] and b["gap_cents"] == 0 and out["story"]["mode"] == "server"
    assert (b["charge_cents"], b["insurer_paid_cents"], b["patient_paid_cents"], b["contractual_cents"], b["open_cents"]) == (15000, 8000, 2000, 5000, 0)
    assert b["text"].startswith("Charge $150.00 = insurer paid $80.00 + patient paid $20.00 + contractual $50.00") and "reconciles to the cent" in b["text"]
    assert [r["kind"] for r in out["story"]["rows"] if r["source"] == "production"].count("payment") == 1
    assert not any(story.SERVER_ONLY in n for n in out["story"]["notes"])
    # the dashboard's copy: no ledger, the store's summaries, the same balance, and the story says the detail is on the server;
    # its state is the one the daily run stored
    st = states.states_of(store.conn, [dict(r) for r in store.conn.execute("SELECT * FROM findings")], today=TODAY)["F1"]
    assert st["work_state"] == out["verdict"]["state"] == "done"
    store.conn.execute("UPDATE findings SET work_state=?, follow_up_on=?, state_basis=? WHERE finding_id='F1'",
                       (st["work_state"], st["follow_up_on"], st["state_basis"]))
    mem = dashboard_copy(store, tmp_path)
    out2 = story.explain(mem.conn, "F1", ledger=None, today=TODAY)
    assert out2["story"]["reconciles"] and out2["story"]["mode"] == "summary" and story.SERVER_ONLY in out2["story"]["notes"]
    assert out2["story"]["balance"]["text"] == b["text"]
    assert story.COPY_NOTE in out2["story"]["notes"] and story.COPY_NOTE not in out["story"]["notes"]
    assert not any("own claim" in n for n in out2["story"]["notes"])
    # the copy keeps every remittance of the bill (cloud.SERVING_BUILD 8): the same state and the same verdict as the server,
    # row numbers aside (the server lists each posting, the copy EMA's summaries)
    import re
    unrow = lambda t: re.sub(r" \(rows? [^)]*\)", "", t)
    assert out2["verdict"]["state"] == "done" and unrow(out2["verdict"]["sentence"]) == unrow(out["verdict"]["sentence"])
    assert out2["next_step"]["route"] == out["next_step"]["route"] == "nothing"
    mem.close()
    plain(out)
    plain(out2)


def test_a_gap_is_shown_with_its_amount_and_cause_when_ema_never_posted_an_835_payment(store, ledger, tmp_path):
    tk = remit(store, trace=F.check_no(1), paid_on="2026-03-28", claims=[{"pcn": F.claim_id(1), "pccn": "PCN-A", "received": "2026-03-05",
        "lines": [ln("99213", 150, 0, [("CO", "197", 150)], lcn="1")]}])
    item(store, line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    remit(store, trace="UNPOSTED9", paid_on="2026-04-10", claims=[{"pcn": F.claim_id(1, sub=2), "pccn": "PCN-B", "received": "2026-04-01",
                                                                  "lines": [ln("99213", 150, 30, [("CO", "45", 120)], lcn="1")]}])
    load(store, ledger, tmp_path, [charge(F.link_no(1), status="Payment Pending"), pay(F.link_no(1), F.check_no(1), 0), later()])
    out = story.explain(store.conn, "F1", ledger=ledger, today=TODAY)
    b = out["story"]["balance"]
    assert not out["story"]["reconciles"] and out["story"]["gap_cents"] == 3000
    assert "paid $30.00 on 99213 (check ending TED9), and EMA has not posted it" in b["gap_cause"]
    assert "It does not reconcile: $30.00 apart, because" in b["text"]


def test_an_835_payment_ema_posted_under_a_number_no_835_carries_is_not_a_gap(store, ledger, tmp_path):
    """The payer's reprocessing paid $30 on 835 trace MISNO835; EMA posted $30 two days later under EMATYPO1, a number no
    835 carries. The checks table pairs the two (C1 'misnumbered'); without that pairing, the payment of its amount on the
    line, from 7 days before to 120 days after the 835, is the same money (the checks' "posted elsewhere" count)."""
    tk = remit(store, trace=F.check_no(1), paid_on="2026-03-28", claims=[{"pcn": F.claim_id(1), "pccn": "PCN-A", "received": "2026-03-05",
        "lines": [ln("99213", 150, 0, [("CO", "197", 150)], lcn="1")]}])
    item(store, line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    remit(store, trace="MISNO835", paid_on="2026-04-10", claims=[{"pcn": F.claim_id(1, sub=2), "pccn": "PCN-B", "received": "2026-04-01",
                                                                 "lines": [ln("99213", 150, 30, [("CO", "45", 120)], lcn="1")]}])
    load(store, ledger, tmp_path, [charge(F.link_no(1)), pay(F.link_no(1), F.check_no(1), 0),
                                   pay(F.link_no(1), "EMATYPO1", 30, posted=D(2026, 4, 12)),
                                   adj(F.link_no(1), "CO-45", 120, contractual=True, posted=D(2026, 4, 12)), later()])
    assert tuple(store.conn.execute("SELECT cause, pair_trace IS NOT NULL FROM ema_checks WHERE check_no='EMATYPO1'").fetchone()) == ("misnumbered", 1)
    for paired in (True, False):
        if not paired:
            store.conn.execute("UPDATE ema_checks SET cause=NULL, pair_trace=NULL WHERE check_no='EMATYPO1'")
        out = story.explain(store.conn, "F1", ledger=ledger, today=TODAY)
        b = out["story"]["balance"]
        assert out["story"]["reconciles"] and b["gap_cents"] == 0, b["gap_cause"]
        row = next(r for r in out["story"]["rows"] if r["source"] == "production" and r["kind"] == "payment")
        if paired:
            assert "no 835 carries this number; it is the 835 payment of Apr 10, 2026, posted under another number" in row["text"]
            assert row["in_835"] and not any(c["source"] == "835_elsewhere" for c in b["checks"])
            # the serverless dashboard's copy keeps the checks table: the same words and the same balance from the summaries
            mem = dashboard_copy(store, tmp_path)
            out2 = story.explain(mem.conn, "F1", ledger=None, today=TODAY)
            mem.close()
            assert out2["story"]["mode"] == "summary" and out2["story"]["reconciles"] and out2["story"]["balance"]["text"] == b["text"]
            assert any("it is the 835 payment of Apr 10, 2026, posted under another number" in r["text"] for r in out2["story"]["rows"])
        else:
            assert "no 835 is on file for this check" in row["text"] and not row["in_835"]
            el_ = next(c for c in b["checks"] if c["source"] == "835_elsewhere")
            assert el_["why"].endswith("EMA posted $30.00 on Apr 12, 2026 under check ending YPO1, a number no 835 carries")
        plain(out)


def test_no_era_is_asked_for_a_number_that_stands_for_an_835_on_file(store, ledger, tmp_path):
    tk = denied(store)
    item(store, line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    load(store, ledger, tmp_path, [charge(F.link_no(1)), pay(F.link_no(1), F.check_no(1), 0),
                                   pay(F.link_no(1), "NOERA01", 90, posted=D(2026, 4, 20)), later()])
    fx = states.facts_of(store.conn, [dict(store.conn.execute("SELECT * FROM findings WHERE finding_id='F1'").fetchone())], today=TODAY)["F1"]
    proof = {"check_tail": "RA01", "payer": P["bcbs"], "rule": "claim_paid_in_ema"}
    assert story._era_request(store.conn, fx, proof).startswith(f"Ask {P['bcbs']} (through its portal, or TriZetto's ERA re-send) for the ERA")
    store.conn.execute("UPDATE ema_checks SET cause='misnumbered', pair_trace=? WHERE check_no='NOERA01'", (tk,))
    assert story._era_request(store.conn, fx, proof) is None


def test_the_facility_twin_is_shown_apart_and_never_counted_on_the_line(store, ledger, tmp_path):
    """The surgery center's claim for the same patient, date and code: its payment is not this line's."""
    tk = denied(store, code="17311", charge_=500, reason=("CO", "97"))
    item(store, signal="PROCEDURE_BUNDLED", category="bundling", carc="97", procedure_code="17311", billed_code="17311",
         charge_cents=50000, line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    load(store, ledger, tmp_path, [charge(F.link_no(1), cpt="17311", amount=500, patient=1, status="Denied"),
                                   pay(F.link_no(1), F.check_no(1), 0, cpt="17311", patient=1),
                                   charge(F.link_no(2), cpt="17311", amount=900, patient=1),
                                   pay(F.link_no(2), "ASC77", 450, cpt="17311", patient=1, posted=D(2026, 3, 30)), later()])
    out = story.explain(store.conn, "F1", ledger=ledger, today=TODAY)
    tw = out["story"]["twin"]
    assert tw and tw["insurer_paid_cents"] == 45000 and "never counted on this line" in tw["note"]
    twin_rows = [r for r in out["story"]["rows"] if "twin" in r["tags"]]
    assert twin_rows and all(r["counted"] is False for r in twin_rows)
    assert out["story"]["balance"]["insurer_paid_cents"] == 0 and out["story"]["balance"]["charge_cents"] == 50000
    assert "The payment on this day belongs to the surgery center's facility claim ($450.00), not this line" in out["verdict"]["sentence"]
    assert out["verdict"]["state"] == "work_now"


def test_a_whole_claim_story_by_claim_key(store, ledger, tmp_path):
    auto_writeoff_world(store, ledger, tmp_path)
    out = story.item_story(store.conn, claim_key=F.claim_id(1), ledger=ledger, today=TODAY)
    assert out["finding_id"] is None and any(r["source"] == "835" for r in out["rows"])
    assert not any(r["kind"] == "raised" for r in out["rows"])
    assert story.item_story(store.conn, "NOPE")["notes"] == ["No such item."]


# ================================================================================================ the verdict
def test_an_auto_write_off_says_so_in_the_owners_words(store, ledger, tmp_path):
    auto_writeoff_world(store, ledger, tmp_path, reason=("CO", "50"), signal="DENIED_MEDICAL_NECESSITY", carc="50")
    v = story.item_verdict(store.conn, "F1", ledger=ledger, today=TODAY)
    assert v["state"] == "work_now" and v["strength"] == "inferred"
    assert v["sentence"].startswith("EMA shows this claim as Resolved, but nobody worked this line. EMA's posting rule wrote off the "
                                    "denial (CO-50, $150.00) on Mar 31, 2026, the day the payer's remittance was posted. The payer "
                                    "paid nothing. You can still appeal until Dec 31, 2027. Inferred from the posting date.")
    assert v["rows"] and "posting day" in v["rule"]
    # an authorization denial says its window in the words of its route (find the authorization first, not an appeal)
    item(store, finding_id="F9", signal="DENIED_AUTH", carc="197", procedure_code="99212", line_key=None)
    assert "The payer's window closes on Dec 31, 2027." in story.item_verdict(store.conn, "F9", today=TODAY)["sentence"]


def test_an_auto_write_off_the_era_export_records_is_proven(store, ledger, tmp_path):
    auto_writeoff_world(store, ledger, tmp_path, handled=[("CO-197", "WRITE_OFF")])
    out = story.explain(store.conn, "F1", ledger=ledger, today=TODAY)
    assert out["verdict"]["strength"] == "proven" and "Recorded by EMA's ERA export." in out["verdict"]["sentence"]
    wo = next(r for r in out["story"]["rows"] if r["kind"] == "adjustment")
    assert "EMA's posting rule wrote this off (EMA's ERA export shows WRITE_OFF)" in wo["text"] and "rule_proven" in wo["tags"]
    # without the export, the posting day alone never says "posting rule wrote this off" on the row
    assert any(r["kind"] == "era_line" for r in out["story"]["rows"])


def test_a_deadline_that_passed_inside_a_later_window_names_that_window(store, ledger, tmp_path):
    """M1 (money review): the item's own deadline passed, but Anthem's two-year dispute window is open: never expired, and the
    step says which window is open and until when; never 'file today only if ... otherwise mark it Lost' (H5)."""
    auto_writeoff_world(store, ledger, tmp_path, reason=("CO", "50"), signal="DENIED_MEDICAL_NECESSITY", carc="50", appeal_deadline="2026-04-20")
    out = story.explain(store.conn, "F1", ledger=ledger, today=TODAY)
    v, n = out["verdict"], out["next_step"]
    assert v["state"] == "work_now" and out["state"]["rule"] == "open_late"
    assert v["sentence"].startswith("Its own deadline passed on Apr 20, 2026; the payer's dispute window is open until Mar 28, 2028")
    assert n["action"].startswith("Its own deadline passed on Apr 20, 2026; the payer's dispute window is open until Mar 28, 2028")
    assert "File today only if" not in n["action"] and "mark it Lost" not in n["action"]
    assert (n["deadline"], n["route"]) == ("2028-03-28", "appeal")
    assert any(c["what"] == "appeal" and c["date"] == "2028-03-28" for c in n["clocks"])


def test_a_window_that_closed_with_every_other_one_is_expired_and_asks_for_nothing(store):
    """H5, M2: past its last window the item expires that day; the step says nothing can be recovered and offers no Let go
    (an expired item already left the list and counts as lost to the deadline)."""
    short_window_item(store)
    out = story.explain(store.conn, "F1", today="2026-04-01")
    v, n = out["verdict"], out["next_step"]
    assert v["sentence"].startswith("Expired: the last window closed on Mar 30, 2026 (Commercial plans: 180 days is the common default), "
                                    "with no work pending")
    assert (v["strength"], v["rule"], n["route"], n["appeal_ok"]) == ("inferred", "past every window", "nothing", False)
    assert n["action"] == ("Nothing can be recovered: the last window closed on Mar 30, 2026 (Commercial plans: 180 days is the common "
                           "default). It left the work list on its own and counts as lost to the deadline.")
    assert n["steps"] == [] and "let_go" not in n["buttons"] and not any("Lost" in s_ for s_ in n["steps"])


def test_paid_later_on_a_check_with_no_835_names_the_row_and_asks_for_the_era(store, ledger, tmp_path):
    tk = denied(store)
    item(store, line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    load(store, ledger, tmp_path, [charge(F.link_no(1)), pay(F.link_no(1), F.check_no(1), 0),
                                   pay(F.link_no(1), "NOERA01", 90, posted=D(2026, 4, 20)),
                                   adj(F.link_no(1), "CO-45", 60, contractual=True, posted=D(2026, 4, 20)), later()])
    out = story.explain(store.conn, "F1", ledger=ledger, today=TODAY)
    v, n = out["verdict"], out["next_step"]
    row = next(r for r in out["story"]["rows"] if r["kind"] == "payment" and r["amount_cents"] == 9000)
    assert v["sentence"] == f"Paid later: $90.00 by {P['bcbs']} on a check with no 835 (row {row['n']})."
    assert row["decisive"] and "no 835 is on file for this check" in row["text"]
    assert (v["state"], n["route"], n["expected_cents"], n["appeal_ok"]) == ("done", "nothing", 0, False)
    assert "request_era" in n["buttons"]
    era = next(s_ for s_ in n["steps"] if s_.startswith("Ask "))
    assert "the ERA of the check ending RA01, posted Apr 20, 2026, $90.00 in all for 1 claim" in era


def test_listed_twice_names_the_other_item(store, ledger, tmp_path):
    auto_writeoff_world(store, ledger, tmp_path)
    item(store, finding_id="F2", signal="DENIED_OTHER", payment_date="2026-04-02")
    v = story.item_verdict(store.conn, "F1", ledger=ledger, today=TODAY)
    assert v["sentence"].endswith(" Listed twice: the same service as item F2.")
    item(store, finding_id="F3", signal="DENIED_OTHER", payment_date="2026-04-03")
    assert " Listed 3 times: the same service as items F2, F3." in story.item_verdict(store.conn, "F1", ledger=ledger, today=TODAY)["sentence"]


def test_an_unanswered_claim_says_how_long_and_the_payers_usual_time(store):
    for i in range(25):     # a payer that usually answers within 40 days of the visit
        remit(store, trace=f"T{i:03d}", paid_on=(SVC + dt.timedelta(days=10 + i)).isoformat(),
              claims=[{"pcn": F.claim_id(100 + i), "received": "2026-03-03", "lines": [ln("99213", 100, 50)]}])
    item(store, signal="NO_ANSWER", carc=None, payment_date=None, line_key=None, claim_key=None,
         evidence={"sent": "2026-03-03", "answer_due": "2026-04-02", "days_waiting": 61, "turnaround_days": 18,
                   "lines": [["99213", S]], "billed_cents": 15000})
    out = story.explain(store.conn, "F1", today=TODAY)
    assert out["verdict"]["sentence"].startswith("Unanswered: no answer from any payer in 61 days; Anthem / BCBS answers 95% of claims "
                                                 "within 18 days")
    n = out["next_step"]
    assert n["route"] == "call" and n["who"] == "A/R follow-up biller" and "log_outcome" in n["buttons"]
    assert any("999" in s_ and "277CA" in s_ for s_ in n["steps"])
    assert any(s_.startswith("Ask Anthem / BCBS: \"Do you have a claim for 99213, billed $150.00") for s_ in n["steps"])


def test_a_remittance_ema_never_posted_says_so_and_the_step_is_post_it(store):
    item(store, signal="REMIT_NOT_POSTED", category="denial", carc=None, shortfall_cents=8000,
         evidence={"trace_tail": "7788"})
    out = story.explain(store.conn, "F1", today=TODAY)
    assert out["next_step"]["route"] == "post" and "mark_posted" in out["next_step"]["buttons"]
    assert out["next_step"]["action"].startswith("Post the payment in EMA: Anthem / BCBS paid $80.00 on Mar 28, 2026 (trace ending 7788)")
    assert out["next_step"]["who"] == "Payment poster"


def test_written_off_while_pending_and_lost_to_timely_filing_wording(store, ledger, tmp_path):
    tk = denied(store, reason=("CO", "16"))
    item(store, signal="DENIED_MISSING_INFO", carc="16", line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    load(store, ledger, tmp_path, [charge(F.link_no(1)), pay(F.link_no(1), F.check_no(1), 0), adj(F.link_no(1), "CO-16", 150), later()])
    v = story.item_verdict(store.conn, "F1", ledger=ledger, today=TODAY)
    assert v["sentence"].startswith("EMA wrote this off the day it posted, but the payer had not finished: it asked for information. "
                                    "This can usually be corrected and sent again.")


def test_a_person_wrote_it_off_later_says_when(store, ledger, tmp_path):
    tk = denied(store)
    item(store, line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    load(store, ledger, tmp_path, [charge(F.link_no(1)), pay(F.link_no(1), F.check_no(1), 0),
                                   adj(F.link_no(1), "CO-197", 150, posted=D(2026, 4, 25)), later()])
    out = story.explain(store.conn, "F1", ledger=ledger, today=TODAY)
    assert out["verdict"]["sentence"].startswith("A person wrote this off in EMA on Apr 25, 2026 (CO-197, $150.00), on a day with no "
                                                 "remittance posted, while the payer had paid nothing")
    wo = next(r for r in out["story"]["rows"] if r["kind"] == "adjustment")
    assert "25 days after the remittance was posted, on a day with no remittance posted" in wo["text"] and "adj_later" in wo["tags"]


def test_moved_to_the_patient_names_the_reversal_and_the_patients_payment(store, ledger, tmp_path):
    tk = denied(store, reason=("CO", "B7"))
    remit(store, trace=F.check_no(2), paid_on="2026-04-25",
          claims=[{"pcn": F.claim_id(1), "status": "22", "pccn": "PCN-A", "received": "2026-03-05",
                   "lines": [ln("99213", -150, 0, [("CO", "B7", -150)], lcn="1")]},
                  {"pcn": F.claim_id(1), "status": "1", "pccn": "PCN-A", "received": "2026-03-05",
                   "lines": [ln("99213", 150, 0, [("PR", "1", 95), ("CO", "45", 55)], lcn="1")]}])
    item(store, signal="DENIED_PROVIDER", carc="B7", line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    load(store, ledger, tmp_path, [charge(F.link_no(1)), pay(F.link_no(1), F.check_no(1), 0),
                                   pay(F.link_no(1), F.check_no(2), 0, posted=D(2026, 4, 28)),
                                   adj(F.link_no(1), "CO-45", 55, contractual=True, posted=D(2026, 4, 28)),
                                   patient_pay(95, posted=D(2026, 4, 30)), later()])
    out = story.explain(store.conn, "F1", ledger=ledger, today=TODAY)
    v = out["verdict"]
    assert v["state"] == "done" and v["strength"] == "proven"
    assert v["sentence"].startswith("Moved to the patient: Anthem / BCBS reversed the denial and reprocessed it on Apr 25, 2026: $95.00 "
                                    "to the patient's deductible, nothing owed by the payer; EMA shows the patient paid")
    rev = next(r for r in out["story"]["rows"] if r["kind"] == "reversal")
    assert rev["decisive"] and "reversal" in rev["tags"]
    assert out["next_step"]["appeal_ok"] is False and out["next_step"]["route"] == "nothing"


def test_a_coordination_of_benefits_denial_is_a_task_to_find_the_first_payer(store, ledger, tmp_path):
    """C13: the payer first put the visit on the patient, then reversed that and said another plan pays first; EMA never
    posted the reversal."""
    remit(store, trace=F.check_no(1), paid_on="2026-03-10", claims=[{"pcn": F.claim_id(1), "pccn": "PCN-A", "received": "2026-03-04",
        "lines": [ln("17110", 180, 0, [("PR", "1", 107), ("CO", "45", 73)], lcn="1", allowed=107)]}])
    tk = remit(store, trace=F.check_no(2), paid_on="2026-04-20", claims=[
        {"pcn": F.claim_id(1), "status": "22", "pccn": "PCN-A", "received": "2026-03-04",
         "lines": [ln("17110", -180, 0, [("PR", "1", -107), ("CO", "45", -73)], lcn="1")]},
        {"pcn": F.claim_id(1), "status": "1", "pccn": "PCN-A", "received": "2026-03-04",
         "lines": [ln("17110", 180, 0, [("CO", "22", 180)], lcn="1")]}])
    item(store, signal="DENIED_OTHER", carc="22", procedure_code="17110", billed_code="17110", payment_date="2026-04-20",
         line_key=f"{tk}-1-0", claim_key=f"{tk}-1", transaction_key=tk)
    load(store, ledger, tmp_path, [charge(F.link_no(1), cpt="17110", amount=180), pay(F.link_no(1), F.check_no(1), 0, cpt="17110", posted=D(2026, 3, 12)),
                                   adj(F.link_no(1), "CO-45", 73, cpt="17110", contractual=True, posted=D(2026, 3, 12)),
                                   patient_pay(107, cpt="17110", posted=D(2026, 3, 20)),
                                   pay(F.link_no(1), F.check_no(2), 0, cpt="17110", posted=D(2026, 4, 22)), later()])
    out = story.explain(store.conn, "F1", ledger=ledger, today=TODAY)
    v, n = out["verdict"], out["next_step"]
    assert v["sentence"].startswith("Coordination of benefits: Anthem / BCBS says another plan pays first (CO-22)")
    assert "reversed that answer on Apr 20, 2026" in v["sentence"] and "The patient has paid $107.00 on this line." in v["sentence"]
    assert "EMA never posted the reversal" in v["sentence"]
    assert n["route"] == "cob" and not n["appeal_ok"] and n["expected_cents"] == 10700
    assert any("timely-filing exception" in s_ for s_ in n["steps"]) and any("coordination of benefits" in s_ for s_ in n["steps"])


def test_a_short_payment_on_the_days_main_procedure_carries_the_argument(store):
    """C28: the day's highest-valued procedure paid at half, as if it were a second procedure."""
    tk = remit(store, trace=F.check_no(1), paid_on="2026-03-28", claims=[{"pcn": F.claim_id(1), "pccn": "PCN-A", "received": "2026-03-05",
        "lines": [ln("17313", 1000, 302, [("CO", "45", 698)], lcn="1", allowed=302), ln("13121", 410, 142.76, [("CO", "45", 267.24)], lcn="2",
                                                                                         allowed=142.76)]}])
    item(store, signal="UNDERPAID_VS_OWN_RATE", category="underpayment", carc=None, procedure_code="17313", billed_code="17313",
         charge_cents=100000, allowed_cents=30200, paid_cents=30200, expected_cents=60400, shortfall_cents=30200,
         line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    out = story.explain(store.conn, "F1", today=TODAY)
    assert out["verdict"]["sentence"].startswith("Paid short: Anthem / BCBS allowed $302.00 for 17313 against its usual $604.00")
    assert "17313 was the highest-valued procedure of the day (the others: 13121 (allowed $142.76))" in out["verdict"]["sentence"]
    n = out["next_step"]
    assert n["route"] == "rate" and n["appeal_ok"] and any(s_.startswith("The argument: 17313 was the highest-valued procedure")
                                                           for s_ in n["steps"])


def test_a_short_payment_of_a_second_procedure_says_check_first_and_is_not_appealed(store):
    """C58: a lower-valued procedure the same day is paid as a second procedure."""
    tk = remit(store, trace=F.check_no(1), paid_on="2026-03-28", claims=[{"pcn": F.claim_id(1), "pccn": "PCN-A", "received": "2026-03-05",
        "lines": [ln("17313", 1000, 604, [("CO", "45", 396)], lcn="1", allowed=604), ln("13121", 410, 142.76, [("CO", "45", 267.24)], lcn="2",
                                                                                         allowed=142.76)]}])
    item(store, signal="UNDERPAID_VS_OWN_RATE", category="underpayment", carc=None, procedure_code="13121", billed_code="13121",
         charge_cents=41000, allowed_cents=14276, paid_cents=14276, expected_cents=28552, shortfall_cents=14276,
         line_key=line_key(store, tk, 0, 1), claim_key=f"{tk}-0", transaction_key=tk)
    out = story.explain(store.conn, "F1", today=TODAY)
    assert out["verdict"]["sentence"].startswith("Check first: 17313 was billed the same day and is valued higher (allowed $604.00), so the "
                                                 "payer pays 13121 as a second procedure")
    assert out["verdict"]["strength"] == "inferred"
    n = out["next_step"]
    assert n["route"] == "decide" and not n["appeal_ok"] and "let it go with that reason" in n["action"]


def test_expired_and_accepted_and_waiting_sentences(store, ledger, tmp_path):
    _f, tk = short_window_item(store)
    out = story.explain(store.conn, "F1", today=TODAY)
    assert out["verdict"]["sentence"].startswith("Expired: the last window closed on Mar 30, 2026 (Commercial plans: 180 days is the common "
                                                 "default), with no work pending")
    assert (out["verdict"]["strength"], out["next_step"]["route"]) == ("inferred", "nothing")
    assert out["next_step"]["buttons"] == ["log_outcome"] and out["next_step"]["steps"] == []
    item(store, finding_id="F2", status="dismissed", status_changed_at="2026-04-02", line_key=line_key(store, tk), claim_key=f"{tk}-0")
    event(store, "F2", "manual", at="2026-04-02T10:00:00", frm="open", to="dismissed", actor="Biller Two", note="contract rate")
    v = story.item_verdict(store.conn, "F2", today=TODAY)
    assert v["state"] == "accepted" and v["sentence"].startswith("Let go by Biller Two on Apr 2, 2026: contract rate")
    assert "reopen" in story.next_step(store.conn, "F2", today=TODAY)["buttons"]
    item(store, finding_id="F3", status="appealed", appealed_at="2026-04-28", line_key=line_key(store, tk), claim_key=f"{tk}-0")
    event(store, "F3", "manual", at="2026-04-28T10:00:00", frm="open", to="appealed", actor="Biller One")
    out = story.explain(store.conn, "F3", today=TODAY)
    assert out["verdict"]["sentence"].startswith("Waiting: Marked Sent by Biller One on Apr 28, 2026; follow up on Jun 12, 2026")
    n = out["next_step"]
    assert (n["route"], n["deadline"], n["appeal_ok"], n["appeal_block"]) == ("wait", "2026-06-12", False, "waiting")


def test_a_stored_state_is_explained_and_a_newer_one_is_named_beside_it(store):
    tk = denied(store)
    item(store, line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk, work_state="work_now", state_basis="stored basis")
    v = story.item_verdict(store.conn, "F1", today=TODAY)
    assert v["stored"] and v["state"] == "work_now"
    store.conn.execute("UPDATE findings SET work_state='waiting', follow_up_on='2026-06-01', state_basis='Marked Sent on Apr 1, 2026'")
    store.conn.commit()
    v = story.item_verdict(store.conn, "F1", today=TODAY)
    assert v["state"] == "waiting" and v["sentence"].startswith("Waiting: Marked Sent on Apr 1, 2026. On the newest data RemitWatch "
                                                                "would say Work now")


# ================================================================================================ the next step
def test_an_appeal_needs_the_visit_note_and_faxes_only_to_a_confirmed_number(store, ledger, tmp_path):
    from remitwatch import actions
    tk = denied(store, reason=("CO", "97"), code="99214", charge_=128)
    item(store, signal="EM_BUNDLED_MOD25", category="bundling", carc="97", procedure_code="99214", billed_code="99214",
         charge_cents=12800, evidence={"same_day_codes": ["17311"]}, line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    n = story.next_step(store.conn, "F1", today=TODAY)
    assert n["route"] == "appeal" and n["appeal_ok"] and n["buttons"][0] == "build_packet"
    assert "upload_note" in n["buttons"] and "fax_payer" not in n["buttons"]
    assert n["action"].startswith("Appeal to Anthem / BCBS: Modifier 25: 99214 was a separate problem evaluated and managed beyond the "
                                  "pre-procedure work of 17311")
    assert "the visit note of Mar 2, 2026 showing a significant, separately identifiable problem" in n["needs"][0]
    # where the appeal goes (M15): Anthem's disputes go on Availity
    assert any(s_.startswith("File it on Availity as a Claim Payment Dispute (Claims & Payments > Disputes)") for s_ in n["steps"])
    assert n["who"] == "Coder reads the note, then the biller sends it" and n["deadline"] == "2027-12-31"
    actions.ensure_fax_tables(store.conn)
    store.conn.execute("INSERT INTO payer_fax (payer_group, purpose, number, confirmed_by, confirmed_at, auto_ok) VALUES (?,?,?,?,?,1)",
                       (BCBS, "appeal", "+15555550123", "Owner", "2026-04-01"))
    store.conn.commit()
    n = story.next_step(store.conn, "F1", today=TODAY)
    assert "fax_payer" in n["buttons"] and any("confirmed number ending 0123, preview first" in s_ for s_ in n["steps"])


def test_a_corrected_claim_names_the_exact_change_and_the_original_claim_number(store):
    tk = denied(store, reason=("CO", "97"), code="99214", charge_=128, pccn="PAYER-ICN-77")
    item(store, signal="EM_BUNDLED_NO_MODIFIER", category="bundling", carc="97", procedure_code="99214", billed_code="99214",
         actionable="correct_and_resubmit", line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    n = story.next_step(store.conn, "F1", today=TODAY)
    assert n["route"] == "corrected" and not n["appeal_ok"]
    assert n["action"] == ("Add modifier 25 to 99214 (when the note shows a separate problem); resubmit as a corrected claim, frequency 7, "
                           "with the original claim number PAYER-ICN-77.")
    assert {"copy_instructions", "mark_sent"} <= set(n["buttons"]) and "proof of timely filing" in n["steps"][0]


def test_a_write_off_decision_goes_through_the_policy_table_or_a_persons_let_go(store, ledger, tmp_path):
    auto_writeoff_world(store, ledger, tmp_path)
    n = story.next_step(store.conn, "F1", ledger=ledger, today=TODAY)
    assert "accept_pattern" in n["buttons"] and "let_go" in n["buttons"]
    assert any("Accept this pattern (Anthem / BCBS, 99213, CO-197)" in s_ and "A single item is let go only by a person, with a reason." in s_
               for s_ in n["steps"]), n["steps"]
    # the button accepts the pattern in one click: the next step carries the owner's policy key (needs_C1 1)
    assert n["pattern"] == f"{BCBS}|99213||CO-197"
    # an authorization denial is decided first (find the authorization): the reversal is said as a condition
    assert n["route"] == "decide"
    assert any(s_.startswith("If you send it, reverse EMA's write-off of $150.00 (CO-197)") for s_ in n["steps"])


def test_an_appealed_write_off_is_reversed_when_the_appeal_goes_out(store, ledger, tmp_path):
    auto_writeoff_world(store, ledger, tmp_path, reason=("CO", "50"), signal="DENIED_MEDICAL_NECESSITY", carc="50")
    n = story.next_step(store.conn, "F1", ledger=ledger, today=TODAY)
    assert n["route"] == "appeal" and n["pattern"] == f"{BCBS}|99213||CO-50"
    assert any(s_.startswith("When you send it, reverse EMA's write-off of $150.00 (CO-50)") for s_ in n["steps"])


def test_never_an_appeal_for_a_line_paid_credited_reprocessed_or_waiting(store, ledger, tmp_path):
    tk = remit(store, trace=F.check_no(1), paid_on="2026-03-28", claims=[{"pcn": F.claim_id(1), "pccn": "PCN-A", "received": "2026-03-05",
        "lines": [ln("99204", 290, 100, [("CO", "45", 125), ("PR", "2", 65)], lcn="1", allowed=165)]}])
    item(store, signal="UNDERPAID_VS_OWN_RATE", category="underpayment", carc=None, procedure_code="99204", billed_code="99204",
         paid_cents=10000, allowed_cents=16500, expected_cents=18000, shortfall_cents=1500, line_key=line_key(store, tk),
         claim_key=f"{tk}-0", transaction_key=tk)
    load(store, ledger, tmp_path, [charge(F.link_no(1), cpt="99204", amount=290), pay(F.link_no(1), F.check_no(1), 100, cpt="99204"),
                                   adj(F.link_no(1), "CO-45", 125, cpt="99204", contractual=True),
                                   pay(F.link_no(1), "SECOND01", 165, cpt="99204", posted=D(2026, 4, 20)), later()])
    out = story.explain(store.conn, "F1", ledger=ledger, today=TODAY)
    n = out["next_step"]
    assert (n["route"], n["appeal_ok"], n["expected_cents"], n["who"]) == ("refund", False, 0, "Billing lead; owner decides")
    assert n["action"].startswith(f"Overpaid by $100.00: decide whether to refund it or let {P['bcbs']} recoup it. Do not appeal")
    assert n["steps"][0].startswith(f"Who paid: $100.00 from {P['bcbs']} posted Mar 31, 2026; $165.00 from {P['bcbs']} posted Apr 20, 2026")
    assert out["verdict"]["sentence"].startswith("Overpaid: EMA shows a credit of $100.00 on this line")


def test_a_timely_filing_denial_with_no_earlier_submission_on_file_is_let_go(store):
    tk = remit(store, trace=F.check_no(1), paid_on="2026-09-10", claims=[{"pcn": F.claim_id(1), "pccn": "PCN-A", "received": "2026-09-01",
                                                                         "lines": [ln("15576", 1056, 0, [("CO", "29", 1056)], lcn="1")]}])
    item(store, signal="DENIED_TIMELY_FILING", carc="29", procedure_code="15576", billed_code="15576", payment_date="2026-09-10",
         appeal_deadline="2028-09-10", line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    n = story.next_step(store.conn, "F1", today="2026-10-01")
    assert n["route"] == "decide" and not n["appeal_ok"]
    assert "On file: the payer first received it on Sep 1, 2026, after the limit." in n["action"]
    assert "No earlier submission is on file." in n["action"]
    assert 'Let go with the reason "lost to timely filing" (provider liability: do not bill the patient)' in n["action"]


def test_a_payers_answer_ema_never_posted_is_the_first_step(store, ledger, tmp_path):
    """C32: the $0 remittance carrying the denial is not in EMA, so EMA's A/R still shows the claim pending."""
    tk = denied(store, reason=("CO", "50"))
    item(store, signal="DENIED_MEDICAL_NECESSITY", carc="50", line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    load(store, ledger, tmp_path, [charge(F.link_no(1), status="Payment Pending"), later()])
    n = story.next_step(store.conn, "F1", ledger=ledger, today=TODAY)
    assert n["steps"][0].startswith("First post Anthem / BCBS's remittance of Mar 28, 2026 in EMA (check ending 0001; paid $0.00; "
                                    "CO-50 $150.00): EMA has not posted it")
    assert "mark_posted" in n["buttons"]


def test_a_takeback_naming_the_claim_that_ema_never_booked_is_a_step(store, ledger, tmp_path):
    tk = denied(store)
    remit(store, trace="PLB01", paid_on="2026-04-15", claims=[{"pcn": F.claim_id(9), "lines": [ln("99212", 90, 40)]}],
          plb=[("WO", F.claim_id(1) + " OVERPAYMENT", 106.54)])
    item(store, line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    load(store, ledger, tmp_path, [charge(F.link_no(1)), pay(F.link_no(1), F.check_no(1), 0), later()])
    out = story.explain(store.conn, "F1", ledger=ledger, today=TODAY)
    plb = next(r for r in out["story"]["rows"] if r["kind"] == "takeback")
    assert plb["amount_cents"] == -10654 and "took back $106.54" in plb["text"]
    assert any(s_.startswith("BLUE CROSS TEST took back $106.54 on Apr 15, 2026") and "post the takeback in EMA" in s_
               for s_ in out["next_step"]["steps"])


def test_few_days_left_says_do_it_today(store):
    tk = denied(store)
    item(store, appeal_deadline="2026-05-06", line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    n = story.next_step(store.conn, "F1", today=TODAY)
    assert n["action"].startswith("Only 3 days left (by May 6, 2026): do it today.")


def test_the_payers_record_for_the_pattern_shapes_the_step(store):
    for i in range(6):
        item(store, finding_id=f"P{i}", status="resolved", auto_note="Paid on a later remittance")
    tk = denied(store, reason=("CO", "97"), code="99214", charge_=128)
    item(store, signal="EM_BUNDLED_MOD25", category="bundling", carc="97", procedure_code="99214", billed_code="99214",
         line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    for i in range(6):
        store.conn.execute("UPDATE findings SET carc='97', procedure_code='99214' WHERE finding_id=?", (f"P{i}",))
    store.conn.commit()
    n = story.next_step(store.conn, "F1", today=TODAY)
    assert n["recovery"]["rate"] == 1.0 and n["ranked_cents"] == n["expected_cents"]
    assert any(s_.startswith("Anthem / BCBS later paid 6 of 6 earlier items like this (99214 with 97): ask it to reprocess") for s_ in n["steps"])


def test_every_route_keeps_to_the_plan_buttons_and_plain_words(store, ledger, tmp_path):
    auto_writeoff_world(store, ledger, tmp_path)
    for sig, cat in (("CONTRACT_RATE_GAP", "contract"), ("FEE_SKIM", "fees"), ("TAKEBACK", "fees"), ("SEQUESTRATION", "info"),
                     ("EMA_DENIED", "denial"), ("REJECTED_PRE_PAYER", "denial"), ("NOT_SENT", "denial")):
        item(store, finding_id=f"X-{sig}", signal=sig, category=cat, carc=None, line_key=None, claim_key=None, payment_date="2026-04-01",
             evidence={"lines": [["99213", S]]})
    for (fid,) in store.conn.execute("SELECT finding_id FROM findings").fetchall():
        for led in (ledger, None):
            out = story.explain(store.conn, fid, ledger=led, today=TODAY)
            assert set(out["next_step"]["buttons"]) <= set(story.BUTTONS)
            assert out["verdict"]["strength"] in ("proven", "one_source", "inferred")
            assert out["verdict"]["state"] in states.STATES and out["verdict"]["sentence"]
            plain(out)


# ================================================================================================ every outcome's sentence
OUTCOME_SENTENCES = [
    ("auto_written_off", {}, "EMA shows this claim as Resolved, but nobody worked this line. EMA's posting rule wrote off the denial (CO-197, $150.00)"),
    ("written_off_while_pending", {"carcs": ["CO-16"]}, "EMA wrote this off the day it posted, but the payer had not finished: it asked for information."),
    ("lost_to_timely_filing", {}, "This was written off for late filing. The payer first denied it for CO-197; it ran out of time before anyone fixed it."),
    ("written_off_at_posting", {}, "EMA wrote off CO-197 ($150.00) the day the remittance posted"),
    ("contractual_at_posting", {"written_off": 0, "contractual_routine": 15000},
     "EMA closed this line with a routine contractual adjustment ($150.00) the day the remittance posted"),
    ("written_off_later", {"outcome_on": "2026-04-25"}, "A person wrote this off in EMA on Apr 25, 2026 (CO-197, $150.00), on a day with no remittance posted"),
    ("contractual_later", {"outcome_on": "2026-04-25"}, "A person wrote this off in EMA on Apr 25, 2026"),
    ("paid_after_partial", {"paid_after_same": 2000}, "Partly paid: EMA shows $20.00 paid since"),
    ("paid_before", {"paid_before": 9000}, "Check first: this line was paid before this denial"),
    ("open", {}, "Unpaid and nobody has worked it: Anthem / BCBS answered CO-197"),
    ("action", {}, "Unpaid and nobody has worked it: Anthem / BCBS answered CO-197"),
    ("closed_unexplained", {}, "Unpaid and nobody has worked it"),
    ("shared_line", {}, "Unpaid and nobody has worked it"),
    ("not_found", {}, "Unpaid and nobody has worked it"),
]


@pytest.mark.parametrize("oc, extra, phrase", OUTCOME_SENTENCES, ids=[c[0] for c in OUTCOME_SENTENCES])
def test_each_outcome_has_its_sentence(store, ledger, tmp_path, monkeypatch, oc, extra, phrase):
    tk = denied(store)
    item(store, line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    load(store, ledger, tmp_path, [charge(F.link_no(1)), pay(F.link_no(1), F.check_no(1), 0), adj(F.link_no(1), "CO-197", 150), later()])
    real = states._outcomes

    def forced(conn, items, t):
        got = real(conn, items, t)
        return {k: dict(v, outcome=oc, **extra) for k, v in got.items()}
    monkeypatch.setattr(states, "_outcomes", forced)
    out = story.explain(store.conn, "F1", ledger=ledger, today=TODAY)
    assert out["verdict"]["sentence"].startswith(phrase), out["verdict"]["sentence"]
    plain(out)


def test_a_date_of_service_emas_reports_do_not_cover_is_said_plainly(store, monkeypatch):
    tk = denied(store)
    item(store, line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    monkeypatch.setattr(states, "_outcomes", lambda conn, items, t: {f["finding_id"]: {"outcome": "not_covered"} for f in items})
    out = story.explain(store.conn, "F1", today=TODAY)
    assert any(n.startswith("EMA's Production reports do not cover this date of service") for n in out["story"]["notes"])
    assert out["story"]["balance"]["basis"] == "835" and "EMA's postings for it are not on file" in out["story"]["balance"]["text"]


# ================================================================================================ the second payer, the payer's own limit, a credit
def test_a_second_payer_never_billed_is_billed_with_the_first_payers_remittance(store, ledger, tmp_path):
    second = _secondary_world(store, ledger, tmp_path)
    out = story.explain(store.conn, "F1", ledger=ledger, today=TODAY)
    n = out["next_step"]
    assert (n["route"], n["expected_cents"], n["who"]) == ("bill_secondary", 2000, "Biller")
    assert n["action"] == (f"Bill {second} for $20.00 (coinsurance): {BCBS} paid on Mar 28, 2026 and left it, and {second} is on file and was "
                           "never billed.")
    assert any(s_.startswith(f"Send the claim to {second} from EMA with {BCBS}'s remittance (its EOB) attached, before Sep 24, 2026") for s_ in n["steps"])
    assert f"EMA wrote the share off: reverse that write-off so the line shows $20.00 open until {second} answers." in n["steps"]
    assert "mark_billed" in n["buttons"] and not n["appeal_ok"]
    plain(out)


def test_a_claim_the_first_payer_forwarded_asks_the_second_payer_for_its_payment(store, ledger, tmp_path):
    second = _secondary_world(store, ledger, tmp_path, crossover=True)
    n = story.next_step(store.conn, "F1", ledger=ledger, today=TODAY)
    assert n["action"].startswith(f"Ask {second} for its payment: {BCBS} paid on Mar 28, 2026 and forwarded the claim")


def test_the_payers_own_limit_is_named_when_the_safe_date_passed(store):
    short_window_item(store, appeal_deadline="2026-03-01", legal_deadline="2026-06-30")
    out = story.explain(store.conn, "F1", today="2026-05-10")
    assert "The safe date to appeal passed on Mar 1, 2026; the payer's own limit is Jun 30, 2026, so it can still be sent." in out["verdict"]["sentence"]
    n = out["next_step"]
    assert n["action"].startswith("The safe date passed on Mar 1, 2026, but Testcare Health's own limit is Jun 30, 2026 (51 days left): "
                                  "send it now.")
    assert n["appeal_ok"] and n["route"] == "appeal"
    plain(out)
    # past both (and every other window): expired the next day, nothing asked, never appealed
    out = story.explain(store.conn, "F1", today="2026-07-01")
    assert out["verdict"]["state"] == "expired" and out["next_step"]["action"].startswith("Nothing can be recovered: the last window closed "
                                                                                          "on Jun 30, 2026")
    assert not out["next_step"]["appeal_ok"] and "let_go" not in out["next_step"]["buttons"]


def test_a_credit_balance_keeps_medicares_date(store):
    item(store, signal="CREDIT_BALANCE", category="refund", payer_group="Medicare Part B", payer_kind="medicare", carc=None,
         expected_cents=4500, shortfall_cents=4500, appeal_deadline="2026-05-30",
         deadline_basis="Medicare expects an overpayment back within 60 days of finding it", evidence={"credit_cents": 4500, "overpaid_by": "Medicare Part B"})
    n = story.next_step(store.conn, "F1", today=TODAY)
    assert (n["route"], n["deadline"], n["expected_cents"]) == ("refund", "2026-05-30", 0)
    assert n["action"].startswith("Overpaid by $45.00: decide whether to refund it") and n["basis"].startswith("Medicare expects")


def test_the_archive_rules_proof_is_the_verdict(store):
    tk = denied(store)
    hint = {"rule": "same_day_answers", "reclass": "dropped_on_correction", "label": "Dropped on the corrected claim",
            "hint": "The corrected claim of Apr 2 no longer bills this line.", "state_hint": "done", "strength": "proven", "proof": []}
    item(store, line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk, evidence={"archive_rule": hint})
    out = story.explain(store.conn, "F1", today=TODAY)
    assert out["verdict"]["sentence"] == "Dropped on the corrected claim: the corrected claim of Apr 2 no longer bills this line."
    assert out["next_step"]["route"] == "nothing" and not out["next_step"]["appeal_ok"]
    plain(out)


# ================================================================================================ claims with no service lines
def _whole_claim(store, *, adj, charge=300, paid=0, paid_on="2026-03-28"):
    """A payer's answer to a whole claim with no service lines (a CO-29 timely-filing denial): claim-level reasons only."""
    c = store.conn
    c.execute("INSERT INTO transactions (transaction_key, trace_number, payment_date, payer_name, superseded, payment_method, "
              "claim_paid_total_cents, payment_amount_cents) VALUES ('TXW1','TRW1',?, 'BLUE CROSS TEST', 0, 'ACH', ?, ?)",
              (paid_on, paid * 100, paid * 100))
    c.execute("""INSERT INTO claims (claim_key, transaction_key, patient_control_number, patient_last, patient_first, payment_date,
                 trace_number, statement_from, claim_status_code, charge_cents, paid_cents, payer_name, claim_filing_indicator,
                 payer_claim_control_number, received_date, claim_frequency_code, patient_id)
                 VALUES ('TXW1-0','TXW1',?, 'TESTPATIENT','ALPHA',?, 'TRW1', ?, '4', ?, ?, 'BLUE CROSS TEST', '12', 'PCN-W', '2026-03-10', '1',
                         'MEMBER-TEST-1')""", (F.claim_id(1), paid_on, S, charge * 100, paid * 100))
    for g, r, amt in adj:
        c.execute("INSERT INTO adjustments (transaction_key, claim_key, line_key, level, group_code, reason_code, amount_cents) "
                  "VALUES ('TXW1','TXW1-0',NULL,'claim',?,?,?)", (g, r, amt * 100))
    c.commit()
    item(store, signal="CLAIM_DENIED_UNPAID", carc="29", procedure_code=None, billed_code=None, charge_cents=charge * 100,
         expected_cents=charge * 100, shortfall_cents=charge * 100, line_key=None, claim_key="TXW1-0", transaction_key="TXW1")


def test_a_whole_claim_answer_balances_at_the_claim(store):
    _whole_claim(store, adj=[("CO", "29", 300)])
    b = story.item_story(store.conn, "F1", today=TODAY)["balance"]
    assert (b["basis"], b["reconciles"], b["denied_cents"], b["open_cents"]) == ("835_claim", True, 30000, 30000)
    assert b["text"] == ("Claim charge $300.00 = paid $0.00 + contractual $0.00 + patient's share $0.00 + denied $300.00 (the 835 of Mar 28, 2026 "
                         "answered the whole claim; EMA's postings for it are not on file). It reconciles to the cent.")


def test_a_whole_claim_answer_that_does_not_add_up_shows_the_gap(store):
    _whole_claim(store, adj=[("CO", "29", 250)])
    st = story.item_story(store.conn, "F1", today=TODAY)
    assert not st["reconciles"] and st["gap_cents"] == 5000
    assert ("the 835's answer of Mar 28, 2026 to the whole claim does not balance: billed $300.00, paid $0.00, adjusted $250.00"
            in st["balance"]["gap_cause"])


def test_an_unanswered_claim_with_no_ema_posting_shows_its_whole_charge_open(store):
    item(store, signal="NO_ANSWER", category="denial", carc=None, payment_date=None, charge_cents=15000)
    b = story.item_story(store.conn, "F1", today=TODAY)["balance"]
    assert (b["basis"], b["open_cents"], b["reconciles"]) == ("unanswered", 15000, True)
    assert b["text"] == ("Charge $150.00, all of it open: no payer's answer to this claim is on file, and no EMA posting is tied to it.")


def test_a_credit_or_a_second_payer_item_keeps_its_own_route_when_the_stored_state_stands_alone(store, monkeypatch):
    """On the dashboard's copy a stored state can differ from what the copy works out (rule 'stored'): a credit is still a
    refund to decide and never an appeal, and a second payer never billed is still billed."""
    item(store, signal="CREDIT_BALANCE", category="refund", payer_group="Medicare Part B", payer_kind="medicare", carc=None,
         expected_cents=4500, shortfall_cents=4500, evidence={"credit_cents": 4500, "overpaid_by": "Medicare Part B"})
    item(store, finding_id="F2", signal="SECONDARY_NOT_BILLED", category="denial", carc=None, expected_cents=2000, shortfall_cents=2000,
         evidence={"first_payer": BCBS, "second_payer": "Testcare Health", "share": "coinsurance"})
    real = story._state_of

    def stored(conn, fx, today):
        return dict(real(conn, fx, today), rule="stored", work_state="work_now")
    monkeypatch.setattr(story, "_state_of", stored)
    n = story.next_step(store.conn, "F1", today=TODAY)
    assert n["route"] == "refund" and not n["appeal_ok"]
    assert story.next_step(store.conn, "F2", today=TODAY)["route"] == "bill_secondary"


# ================================================================================================ after the two reviews
# Each fix from the money review and the practice manager's review, on invented rows. A test names the rule it holds.
def _fax(store, group, number="5555550100"):
    """A confirmed appeal fax number for the payer group (the Faxes page's table)."""
    store.conn.execute("CREATE TABLE IF NOT EXISTS payer_fax (payer_group TEXT, purpose TEXT, number TEXT, auto_ok INTEGER, "
                       "changed_at TEXT, previous TEXT)")
    store.conn.execute("INSERT INTO payer_fax VALUES (?,?,?,?,?,?)", (group, "appeal", number, 1, None, None))
    store.conn.commit()


def _all_text(out: dict) -> list[str]:
    n = out["next_step"]
    return [out["verdict"]["sentence"], n["action"]] + list(n["steps"]) + [str((out["state"] or {}).get("state_basis") or "")]


def _medicare_item(store, *, signal, carc, rarc=None, code="99213", group="Medicare KY (CGS J15)", **kw):
    """A Medicare Part B denial of Mar 20, 2026 (redetermination to Jul 18, 2026, its own limit Jul 23, 2026)."""
    tk = remit(store, trace=F.check_no(1), paid_on="2026-03-20", payer="MEDICARE PART B TEST", claims=[{"pcn": F.claim_id(1), "cfi": "MB",
        "received": "2026-03-05", "lines": [ln(code, 400, 0, [("CO", carc, 400)], lcn="1", rmk=[rarc] if rarc else [])]}])
    item(store, signal=signal, carc=carc, rarc=rarc, procedure_code=code, billed_code=code, charge_cents=40000,
         payer_name="MEDICARE PART B TEST", payer_group=group, payer_kind="medicare", payment_date="2026-03-20",
         appeal_deadline="2026-07-18", legal_deadline="2026-07-23", line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk,
         **kw)
    return tk


# ------------------------------------------------------------------------------------------- windows and expiry
def test_the_payers_own_dispute_window_keeps_a_corrected_claim_item_open(store):
    """Money review 1: dated by Humana's one-year corrected-claim clock, the item stays open while Humana's 18-month dispute
    window is open; it expires the day after that window."""
    tk = denied(store, reason=("CO", "16"), payer="HUMANA TEST", paid_on="2026-03-20")
    item(store, signal="DENIED_MISSING_MODIFIER", carc="16", rarc="N822", actionable="correct_and_resubmit", payer_name="HUMANA TEST",
         payer_group="Humana Medicare Advantage", payer_kind="medicare_advantage", payment_date="2026-03-20", appeal_deadline="2027-03-02",
         line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    last = states.last_window(dict(store.conn.execute("SELECT * FROM findings").fetchone()))
    assert last["what"] == "the payer's dispute window" and last["date"] > "2027-05-01"
    out = story.explain(store.conn, "F1", today="2027-05-01")
    assert (out["verdict"]["state"], out["state"]["rule"]) == ("work_now", "open_late")
    assert out["next_step"]["deadline"] == last["date"]
    after = states.state_of(store.conn, dict(store.conn.execute("SELECT * FROM findings").fetchone()), today=states._add(last["date"], 1))
    assert after["work_state"] == "expired"


def test_a_medicare_remark_naming_a_coding_error_keeps_the_reopening_open_a_year(store):
    """C59: a drug line denied CO-114 with remark M51 (a code missing or invalid). The redetermination window is 120 days,
    but a clerical fix is a Medicare reopening, open for a year from the remittance: the item stays on Work now until then,
    its step compares the line with a paid one, and it expires the day after the year."""
    _medicare_item(store, signal="DENIED_NONCOVERED", carc="114", rarc="M51", code="J3301", group="Medicare IN (WPS J8)")
    n = story.next_step(store.conn, "F1", today="2026-05-01")
    assert any(s_.startswith("The remark M51 (Missing/incomplete/invalid procedure code(s)) points to a coding error: compare this line "
                             "with a paid one of the same code") and "reopening until Mar 20, 2027" in s_ and "by Jul 23, 2026" in s_
               for s_ in n["steps"]), n["steps"]
    out = story.explain(store.conn, "F1", today="2026-10-01")
    v, n = out["verdict"], out["next_step"]
    assert (v["state"], out["state"]["rule"], n["deadline"]) == ("work_now", "open_late", "2027-03-20")
    assert v["sentence"].startswith("Its own deadline passed on Jul 23, 2026; a Medicare reopening is open until Mar 20, 2027 (Medicare "
                                    "reopening of a clerical error, within one year of the remittance (42 CFR 405.980)).")
    assert "ask the Medicare contractor for it on the WPS portal or by phone" in n["action"] and not n["appeal_ok"]
    assert story.item_verdict(store.conn, "F1", today="2027-03-21")["state"] == "expired"
    plain(out)


def test_a_bundling_denial_that_needs_the_visit_note_has_no_reopening_and_its_clocks_match_its_state(store):
    """Money review 1, the modifier-57 case: an E/M billed with 57 and still bundled into a major surgery needs the visit
    note (the decision for surgery), which a reopening never takes. Its clocks list no reopening, and its state expires the
    day after the last clock it lists (C59 is a different case: a remark naming a coding error)."""
    _medicare_item(store, signal="EM_BUNDLED_MOD57_MAJOR", carc="97", category="bundling", actionable="appeal")
    out = story.explain(store.conn, "F1", today="2026-07-23")
    clocks = out["next_step"]["clocks"]
    assert not any(c["process"] == "reopening" for c in clocks)
    assert max(c["date"] for c in clocks) == "2026-07-23" and out["verdict"]["state"] == "work_now"
    assert "(today is the last day): send it now" in out["next_step"]["action"]
    # the argument's first sentence is cut at its end, never inside "ch. 12 sec. 40.2"
    assert "(CMS Claims Processing Manual ch. 12 sec. 40.2). Medicare contractors take no faxed redeterminations" in out["next_step"]["action"]
    late = story.explain(store.conn, "F1", today="2026-07-24")
    assert late["verdict"]["state"] == "expired" and max(c["date"] for c in late["next_step"]["clocks"]) < "2026-07-24"


def test_a_forwarded_claim_past_every_window_expires_and_never_stays_on_work_now(store):
    """Money review 4, practice-manager M19: a claim forwarded to another carrier (CO-B11) runs on the carrier's filing
    limit; past it, nothing can be recovered and the item expires."""
    tk = remit(store, trace=F.check_no(1), paid_on="2026-01-10", claims=[{"pcn": F.claim_id(1), "status": "19", "crossover": "TESTCARE MEDIGAP",
               "pccn": "PCN-A", "received": "2026-01-05", "lines": [ln("99213", 150, 0, [("CO", "B11", 150)], lcn="1")]}])
    item(store, signal="FORWARDED_TO_CARRIER", carc="B11", payment_date="2026-01-10", appeal_deadline="2026-04-10",
         deadline_basis="the carrier's filing limit", line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    assert story.explain(store.conn, "F1", today="2026-11-22")["verdict"]["state"] == "expired"
    assert states.state_of(store.conn, dict(store.conn.execute("SELECT * FROM findings").fetchone()), today="2028-05-01")["work_state"] == "expired"


def test_the_payers_answer_to_a_resubmission_is_worked_in_the_new_answers_window(store):
    """Practice-manager M20: the payer answered a resubmission with a new denial; the step never leads with the first
    denial's closed window."""
    tk = denied(store, reason=("CO", "16"), paid_on="2026-01-10", received="2026-01-05")
    remit(store, trace="T2", paid_on="2026-06-15", claims=[{"pcn": F.claim_id(1, sub=2), "pccn": "PCN-B", "received": "2026-06-01",
          "freq": "7", "lines": [ln("99213", 150, 0, [("CO", "50", 150)], lcn="1")]}])
    item(store, signal="DENIED_MISSING_INFO", carc="16", payment_date="2026-01-10", appeal_deadline="2026-04-10",
         deadline_basis="Blue Cross: 90 days to correct", line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    out = story.explain(store.conn, "F1", today="2026-07-01")
    assert out["state"]["rule"] == "work_answered" and out["verdict"]["state"] == "work_now"
    assert "File today only if" not in out["next_step"]["action"] and "mark it Lost" not in out["next_step"]["action"]


# ------------------------------------------------------------------------------------------- proof of payment
def test_a_short_payment_reversed_and_repaid_at_the_same_amount_is_not_paid_again(store):
    """Money review 5: the payer reversed its $60 answer and paid the same $60 again; nothing more arrived, so the
    underpayment stays open."""
    tk = remit(store, trace="T1", paid_on="2026-03-28", claims=[{"pcn": F.claim_id(1), "pccn": "PCN-A", "received": "2026-03-05",
        "lines": [ln("99214", 200, 60, [("CO", "45", 140)], lcn="1", allowed=60)]}])
    item(store, signal="UNDERPAID_VS_OWN_RATE", category="underpayment", carc=None, procedure_code="99214", billed_code="99214",
         paid_cents=6000, allowed_cents=6000, expected_cents=10000, shortfall_cents=4000, line_key=line_key(store, tk),
         claim_key=f"{tk}-0", transaction_key=tk)
    remit(store, trace="T2", paid_on="2026-05-10", claims=[
        {"pcn": F.claim_id(1), "status": "22", "pccn": "PCN-A", "received": "2026-03-05",
         "lines": [ln("99214", -200, -60, [("CO", "45", -140)], lcn="1", allowed=-60)]},
        {"pcn": F.claim_id(1), "pccn": "PCN-A", "received": "2026-05-01",
         "lines": [ln("99214", 200, 60, [("CO", "45", 140)], lcn="1", allowed=60)]}])
    st = states.state_of(store.conn, dict(store.conn.execute("SELECT * FROM findings").fetchone()), today="2026-06-01")
    assert st["work_state"] == "work_now", st["state_basis"]


def test_a_second_payers_copay_never_settles_the_first_payers_downcode(store):
    """Money review 5: the first payer paid 99204 as 99203 ($20.00 short); the second plan later paid the $20.00 copay.
    That is the patient's share, not the missing money, even though it is the same amount."""
    tk = remit(store, trace="T1", paid_on="2026-03-11", claims=[{"pcn": F.claim_id(1), "received": "2026-03-05",
        "lines": [ln("99203", 280, 60, [("PR", "3", 20), ("CO", "186", 200)], lcn="1", allowed=80, ocode="99204")]}])
    item(store, signal="DOWNCODE_SVC06", category="downcoding", carc="186", procedure_code="99203", original_procedure_code="99204",
         billed_code="99204", charge_cents=28000, paid_cents=6000, allowed_cents=8000, expected_cents=10000, shortfall_cents=2000,
         line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    remit(store, trace="T2", paid_on="2026-03-31", payer="SECONDARY TEST PLAN", claims=[
        {"pcn": F.claim_id(1, sub=2), "status": "2", "received": "2026-03-20",
         "lines": [ln("99204", 280, 20, [("OA", "23", 260)], lcn="1", allowed=80)]}])
    st = states.state_of(store.conn, dict(store.conn.execute("SELECT * FROM findings").fetchone()), today="2026-04-15")
    assert st["work_state"] != "done", st["state_basis"]


def test_another_payers_payment_closes_a_line_whose_claim_has_a_zero_charge_twin(store, ledger, tmp_path):
    """Money review 7 (C02): the claim carries the code twice, once with a $0 charge; the $0 line is not a second service,
    so EMA's proof that another payer paid and settled the line closes it."""
    tk = remit(store, trace=F.check_no(1), paid_on="2026-03-28", claims=[{"pcn": F.claim_id(1), "pccn": "PCN-A", "received": "2026-03-05",
        "lines": [ln("17311", 0, 0, [], lcn="1"), ln("17311", 1080, 0, [("CO", "96", 1080)], lcn="2")]}])
    item(store, signal="DENIED_NONCOVERED", carc="96", procedure_code="17311", billed_code="17311", charge_cents=108000,
         expected_cents=60000, shortfall_cents=60000, line_key=line_key(store, tk, 0, 1), claim_key=f"{tk}-0", transaction_key=tk)
    load(store, ledger, tmp_path, [charge(F.link_no(1), cpt="17311", amount=1080, status="Resolved/Paid"),
                                   pay(F.link_no(1), F.check_no(1), 0, cpt="17311"),
                                   pay(F.link_no(1), "NOERA77", 375, cpt="17311", posted=D(2026, 4, 28), payer=P["commercial"],
                                       status="Resolved/Paid"),
                                   adj(F.link_no(1), "CO-45", 705, cpt="17311", contractual=True, posted=D(2026, 4, 28),
                                       payer=P["commercial"], status="Resolved/Paid"), later()])
    out = story.explain(store.conn, "F1", ledger=ledger, today="2026-05-03")
    b = out["story"]["balance"]
    assert b["insurer_paid_cents"] == 37500 and b["open_cents"] == 0
    assert out["verdict"]["state"] == "done", out["verdict"]["sentence"]


def test_a_short_payment_split_into_two_lines_of_one_claim_is_paid_in_full(store):
    """Money review 7 (C27): the payer split the code into two lines of one claim sharing a line control number; their
    allowed amounts ($385.00 + $90.00) reach the usual $475.00."""
    tk = remit(store, trace="T1", paid_on="2026-03-28", claims=[{"pcn": F.claim_id(1), "pccn": "PCN-A", "received": "2026-03-05",
        "lines": [ln("77600", 860, 360, [("CO", "45", 475), ("PR", "2", 25)], lcn="1", allowed=385),
                  ln("77600", 0, 0, [("CO", "45", 0)], lcn="1", allowed=90)]}])
    item(store, signal="UNDERPAID_VS_OWN_RATE", category="underpayment", carc=None, procedure_code="77600", billed_code="77600",
         charge_cents=86000, paid_cents=36000, allowed_cents=38500, expected_cents=47500, shortfall_cents=9000,
         line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    out = story.explain(store.conn, "F1", today="2026-05-03")
    assert out["verdict"]["state"] == "done" and "into 2 lines of the claim" in out["state"]["state_basis"], out["verdict"]["sentence"]
    assert not out["next_step"]["appeal_ok"]


def test_a_line_ema_shows_settled_by_insurance_is_never_called_unpaid(store, ledger, tmp_path):
    """Practice-manager H6 (C02): EMA shows another payer paid and nothing open; the verdict never says 'Unpaid ... $0.00
    still open' and never claims proof it does not have."""
    tk = remit(store, trace=F.check_no(1), paid_on="2026-03-28", claims=[{"pcn": F.claim_id(1), "pccn": "PCN-A", "received": "2026-03-05",
               "lines": [ln("17311", 600, 0, [("CO", "96", 600)], lcn="1"), ln("17311", 400, 0, [("CO", "96", 400)], lcn="2")]}])
    item(store, signal="DENIED_NONCOVERED", carc="96", procedure_code="17311", billed_code="17311", charge_cents=60000, expected_cents=60000,
         shortfall_cents=60000, line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    load(store, ledger, tmp_path, [charge(F.link_no(1), cpt="17311", amount=1000), pay(F.link_no(1), F.check_no(1), 0, cpt="17311"),
                                   pay(F.link_no(1), "OTHR0001", 375, cpt="17311", posted=D(2026, 4, 20), payer=P["commercial"]),
                                   adj(F.link_no(1), "CO-45", 625, cpt="17311", contractual=True, posted=D(2026, 4, 20), payer=P["commercial"]),
                                   later()])
    out = story.explain(store.conn, "F1", ledger=ledger, today=TODAY)
    s_ = out["verdict"]["sentence"]
    assert "Unpaid" not in s_ and "$0.00 still open" not in s_, s_
    assert out["verdict"]["strength"] != "proven"


def test_a_payment_that_came_before_the_item_closes_with_nothing_recovered(store):
    """Practice-manager M3: the line was paid before this denial; closing it as Paid records $0.00 recovered."""
    remit(store, trace="P1", paid_on="2026-03-20", claims=[{"pcn": F.claim_id(1), "pccn": "PCN-A", "received": "2026-03-05",
          "lines": [ln("99213", 150, 90, [("CO", "45", 60)], lcn="1")]}])
    tk = denied(store, paid_on="2026-04-10", reason=("CO", "50"), pcn=F.claim_id(1, sub=2), pccn="PCN-B", received="2026-04-01")
    item(store, signal="DENIED_MEDICAL_NECESSITY", carc="50", payment_date="2026-04-10", patient_control_number=F.claim_id(1, sub=2),
         line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    n = story.next_step(store.conn, "F1", today=TODAY)
    assert n["route"] == "nothing"
    assert not any(s_.startswith("Close it in RemitWatch: Paid, with the amount") for s_ in n["steps"]), n["steps"]
    assert any("$0.00 recovered" in s_ for s_ in n["steps"]), n["steps"]


# ------------------------------------------------------------------------------------------- credits and refunds
def test_a_credit_made_by_a_takeback_ema_never_posted_is_posted_never_refunded(store, ledger, tmp_path):
    """Money review 1 (high): the payer paid $100, reversed it and paid $60 on reprocessing. EMA posted both payments and
    never the reversal, so EMA shows a $60 credit. The 835 nets $60: nothing was overpaid. The step is to post the
    takeback; deciding a refund would pay the payer back twice."""
    tk = remit(store, trace=F.check_no(1), paid_on="2026-03-28", claims=[{"pcn": F.claim_id(1), "pccn": "PCN-A", "received": "2026-03-05",
        "lines": [ln("99214", 150, 100, [("CO", "45", 50)], lcn="1", allowed=100)]}])
    item(store, signal="UNDERPAID_VS_OWN_RATE", category="underpayment", carc=None, procedure_code="99214", billed_code="99214",
         paid_cents=10000, allowed_cents=10000, expected_cents=11500, shortfall_cents=1500, line_key=line_key(store, tk),
         claim_key=f"{tk}-0", transaction_key=tk)
    remit(store, trace=F.check_no(2), paid_on="2026-04-20", claims=[
        {"pcn": F.claim_id(1), "status": "22", "pccn": "PCN-A", "received": "2026-03-05",
         "lines": [ln("99214", -150, -100, [("CO", "45", -50)], lcn="1", allowed=-100)]},
        {"pcn": F.claim_id(1), "pccn": "PCN-A", "received": "2026-04-10",
         "lines": [ln("99214", 150, 60, [("CO", "45", 90)], lcn="1", allowed=60)]}])
    load(store, ledger, tmp_path, [charge(F.link_no(1), cpt="99214"), pay(F.link_no(1), F.check_no(1), 100, cpt="99214"),
                                   adj(F.link_no(1), "CO-45", 50, cpt="99214", contractual=True),
                                   pay(F.link_no(1), F.check_no(2), 60, cpt="99214", posted=D(2026, 4, 22)), later()])
    out = story.explain(store.conn, "F1", ledger=ledger, today="2026-05-03")
    n = out["next_step"]
    assert out["state"]["rule"] == "credit_unposted_takeback" and n["route"] == "post", n["action"]
    assert n["action"].startswith("Post ") and n["action"].endswith("Nothing is owed back: do not refund it."), n["action"]
    assert "EMA shows a credit of $60.00 only because it was never posted" in n["action"] and not n["appeal_ok"]
    assert out["state"]["appeal_block"] == "credit"          # kept out of every bulk appeal


def test_a_refund_lists_who_paid_and_never_dates_it_by_the_appeal_window(store):
    """Practice-manager M18: a credit with no payment on file never prints 'Who paid:.', and its deadline is never the
    appeal window."""
    item(store, signal="CREDIT_BALANCE", category="refund", carc=None, title="Credit on the claim", expected_cents=4500, shortfall_cents=4500,
         appeal_deadline=None, deadline_basis=None, evidence={"credit_cents": 4500})
    n = story.next_step(store.conn, "F1", today=TODAY)
    assert "Who paid:." not in n["steps"] and not any(s_.startswith("Who paid:.") for s_ in n["steps"]), n["steps"]
    assert "Reconsideration" not in str(n["basis"]) and "appeal" not in str(n["basis"]).lower(), n["basis"]
    assert n["route"] == "refund" and not n["appeal_ok"]


def test_a_possible_overpayment_is_a_refund_to_decide_never_a_rate_request(store):
    """Practice-manager H2: the archive shows the payer paid the line twice; asking it to reprocess at the usual rate would
    grow the overpayment. A $0 shortfall is worth $0."""
    tk = remit(store, trace=F.check_no(1), paid_on="2026-03-28", claims=[{"pcn": F.claim_id(1), "pccn": "PCN-A", "received": "2026-03-05",
               "lines": [ln("99213", 150, 40, [("CO", "45", 110)], allowed=40, lcn="1")]}])
    hint = {"rule": "short_pay_topped_up", "reclass": "overpaid", "label": "Paid more than the payer owes",
            "hint": "Blue Cross Test paid this line again on 2026-04-20 ($90.00) and both payments stand: a possible overpayment of $40.00. "
                    "Settle it with the payer; do not ask for more.", "state_hint": "work_now", "strength": "proven",
            "possible_overpayment_cents": 4000, "proof": []}
    item(store, signal="UNDERPAID_VS_OWN_RATE", category="underpayment", actionable="appeal", carc=None, paid_cents=4000, allowed_cents=4000,
         expected_cents=9000, shortfall_cents=0, line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk,
         hint=hint["hint"], evidence={"archive_rule": hint})
    out = story.explain(store.conn, "F1", today=TODAY)
    n = out["next_step"]
    assert out["state"]["rule"] == "possible_overpayment" and n["route"] == "refund" and not n["appeal_ok"], n["action"]
    assert "reprocess" not in n["action"] and n["expected_cents"] == 0


# ------------------------------------------------------------------------------------------- work already done
def test_an_ar_billing_note_with_a_follow_up_date_waits_and_is_never_bulk_appealed(store, ledger, tmp_path):
    """Money review 3 (high), C37 to C39: the A/R's billing note records a call with 'allow 30 days' and a follow-up date.
    The reader stores the note's kinds; the item waits for them, its verdict never says nobody worked it, and it stays out
    of the bulk appeal."""
    tk = denied(store, reason=("CO", "B10"))
    item(store, signal="PROCEDURE_BUNDLED", category="bundling", carc="B10", line_key=line_key(store, tk), claim_key=f"{tk}-0",
         transaction_key=tk)
    ar = [F.ar_row(claim=F.claim_id(1), link=F.link_no(1), svc=SVC, cpt="99213", current=150, denial=150, status="Denied",
                   note="04/20 spoke with rep, reprocessing request, allow 30 days", follow=D(2026, 5, 20), aging=D(2026, 4, 28))]
    load(store, ledger, tmp_path, [charge(F.link_no(1), status="Denied"), pay(F.link_no(1), F.check_no(1), 0), later()], ar_rows=ar)
    out = story.explain(store.conn, "F1", ledger=ledger, today=TODAY)
    assert out["verdict"]["state"] == "waiting" and out["state"]["follow_up_on"] == "2026-05-20", out["verdict"]["sentence"]
    assert "nobody has worked it" not in out["verdict"]["sentence"] and not out["next_step"]["appeal_ok"]
    assert out["state"]["appeal_block"] is not None


def test_an_ar_note_the_reader_could_not_classify_is_read_first_never_appealed_blind(store, ledger, tmp_path):
    """Money review 3: a billing note whose kind is not on file (note_kind empty) is read first: the item is checked
    first, never in a bulk appeal, and the step says what to do with each kind of note."""
    tk = denied(store, reason=("CO", "50"))
    item(store, signal="DENIED_MEDICAL_NECESSITY", carc="50", line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    ar = [F.ar_row(claim=F.claim_id(1), link=F.link_no(1), svc=SVC, cpt="99213", current=150, denial=150, status="Denied",
                   note="see attached", aging=D(2026, 4, 28))]
    load(store, ledger, tmp_path, [charge(F.link_no(1), status="Denied"), pay(F.link_no(1), F.check_no(1), 0, status="Denied"), later()], ar_rows=ar)
    store.conn.execute("UPDATE ema_ar SET note_kind=NULL")
    store.conn.commit()
    out = story.explain(store.conn, "F1", ledger=ledger, today=TODAY)
    n = out["next_step"]
    assert out["state"]["rule"] == "note_unread" and out["state"]["appeal_block"] == "note" and not n["appeal_ok"]
    assert n["action"].startswith("Read EMA's billing note on this claim first") and "otherwise appeal it" in n["action"]
    assert "nobody has worked it" not in out["verdict"]["sentence"]


def test_an_ar_note_that_records_a_decision_not_to_appeal_is_not_appealed(store, ledger, tmp_path):
    """Practice-manager M8: the note says the practice decided not to appeal; the item is never appealed, singly or in
    bulk, and the step asks to Let go with that reason or appeal if the decision does not stand."""
    tk = denied(store, reason=("CO", "50"))
    item(store, signal="DENIED_MEDICAL_NECESSITY", carc="50", line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    ar = [F.ar_row(claim=F.claim_id(1), link=F.link_no(1), svc=SVC, cpt="99213", current=150, denial=150, status="Denied",
                   note="Test note: reviewed, decided not to appeal", aging=D(2026, 4, 28))]
    load(store, ledger, tmp_path, [charge(F.link_no(1), status="Denied"), pay(F.link_no(1), F.check_no(1), 0, status="Denied"), later()], ar_rows=ar)
    store.conn.execute("UPDATE ema_ar SET note_kind='no_appeal'")
    store.conn.commit()
    out = story.explain(store.conn, "F1", ledger=ledger, today=TODAY)
    assert out["state"]["rule"] == "decided_no_appeal" and out["state"]["appeal_block"] == "decided"
    assert not out["next_step"]["appeal_ok"], out["next_step"]["action"]
    assert "nobody has worked it" not in out["verdict"]["sentence"], out["verdict"]["sentence"]


def test_a_persons_started_status_is_named_and_never_contradicted(store, ledger, tmp_path):
    """Practice-manager M1: a person marked the item Started; the verdict names who and when and never says nobody
    worked it."""
    tk = denied(store)
    item(store, line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk, status="in_progress", status_changed_at="2026-04-02")
    event(store, "F1", "manual", at="2026-04-02T09:00:00", frm="open", to="in_progress", actor="Biller One")
    load(store, ledger, tmp_path, [charge(F.link_no(1), status="Resolved/Paid"), pay(F.link_no(1), F.check_no(1), 0, status="Resolved/Paid"),
                                   adj(F.link_no(1), "CO-197", 150, status="Resolved/Paid"), later()])
    v = story.item_verdict(store.conn, "F1", ledger=ledger, today=TODAY)
    assert "Started by Biller One on Apr 2, 2026" in v["sentence"], v["sentence"]
    assert "nobody worked" not in v["sentence"] and "nobody has worked" not in v["sentence"], v["sentence"]


def test_the_follow_up_of_a_persons_sent_reads_as_english_and_is_due_now(store):
    """Practice-manager M4: the follow-up of a Sent appeal names the work in plain words and is due by its follow-up day,
    not by the far appeal window."""
    tk = denied(store, reason=("CO", "50"))
    item(store, signal="DENIED_MEDICAL_NECESSITY", carc="50", status="appealed", appealed_at="2026-04-01", status_changed_at="2026-04-01",
         line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    event(store, "F1", "manual", at="2026-04-01T09:00:00", frm="open", to="appealed", actor="Biller One", note="faxed the appeal")
    n = story.next_step(store.conn, "F1", today="2026-06-15")
    joined = " ".join([n["action"]] + n["steps"])
    assert "on sent to the payer" not in joined and "whether it has sent to the payer" not in joined, joined
    assert n["deadline"] <= "2026-06-15", n["deadline"]


def test_a_secondary_billed_through_mark_billed_closes_on_the_second_payers_payment(store, ledger, tmp_path):
    """Practice-manager H4: Mark billed sets the item to Sent with 'Billed: <second payer>'. When the second payer pays, the
    verdict names the payment, and the follow-up never goes to the first payer."""
    second = _secondary_world(store, ledger, tmp_path)
    store.conn.execute("UPDATE findings SET status='appealed', appealed_at='2026-04-05', status_changed_at='2026-04-05'")
    event(store, "F1", "billed", at="2026-04-05T09:00:00", note=f"{second}, Apr 5")
    event(store, "F1", "manual", at="2026-04-05T09:00:01", frm="open", to="appealed", actor="Biller One", note=f"Billed: {second}, Apr 5")
    load(store, ledger, tmp_path, [pay(F.link_no(1), "SEC01", 20, posted=D(2026, 4, 20), payer=P["commercial"]), later(posted=D(2026, 5, 2))])
    out = story.explain(store.conn, "F1", ledger=ledger, today="2026-07-10")
    v, n = out["verdict"], out["next_step"]
    assert out["state"]["rule"] == "person_billed_paid" and n["action"].startswith("Mark it Paid"), n["action"]
    assert "$20.00" in v["sentence"] and second in v["sentence"], v["sentence"]
    assert not n["action"].startswith(f"Follow up with {BCBS}"), n["action"]


def test_a_stored_state_never_falls_back_to_a_first_level_appeal_after_a_sent_appeal(store):
    """Practice-manager M22: a stored Work now ('the payer answered the appeal') while today's reading differs: the step
    works the payer's answer, never a fresh first-level appeal with Build packet."""
    tk = denied(store, reason=("CO", "50"))
    item(store, signal="DENIED_MEDICAL_NECESSITY", carc="50", status="appealed", appealed_at="2026-04-01", status_changed_at="2026-04-01",
         line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk, work_state="work_now", follow_up_on="2026-05-16",
         state_basis="Marked Sent by Biller One on Apr 1, 2026; the payer answered on Apr 15, 2026 (answered CO-50): work its answer")
    event(store, "F1", "manual", at="2026-04-01T09:00:00", frm="open", to="appealed", actor="Biller One", note="faxed the appeal")
    n = story.next_step(store.conn, "F1", today="2026-04-20")
    assert n["route"] != "appeal" and "build_packet" not in n["buttons"], (n["route"], n["action"])


def test_a_stale_item_is_never_closed_as_paid_and_a_persons_sent_still_wins(store):
    """Practice-manager L6: a stale item (its remittance replaced) is never told to be closed as Paid, and a person's Sent
    wins over staleness."""
    tk = denied(store, reason=("CO", "50"))
    item(store, signal="DENIED_MEDICAL_NECESSITY", carc="50", line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk, stale=1)
    n = story.next_step(store.conn, "F1", today=TODAY)
    assert not any("Paid" in s_ for s_ in n["steps"]), n["steps"]
    item(store, finding_id="F2", signal="DENIED_MEDICAL_NECESSITY", carc="50", line_key=line_key(store, tk), claim_key=f"{tk}-0",
         transaction_key=tk, stale=1, status="appealed", appealed_at="2026-04-20", status_changed_at="2026-04-20")
    event(store, "F2", "manual", at="2026-04-20T09:00:00", frm="open", to="appealed", actor="Biller One")
    st = states.state_of(store.conn, dict(store.conn.execute("SELECT * FROM findings WHERE finding_id='F2'").fetchone()), today=TODAY)
    assert st["work_state"] == "waiting", st["state_basis"]


# ------------------------------------------------------------------------------------------- routes and their steps
def test_an_archive_likely_paid_item_is_checked_first_never_appealed(store):
    """Practice-manager H1: a 'likely' done from the archive rules never closes an item; its step is a check first naming
    what to verify, with no Fax button and no place in the bulk appeal."""
    tk = denied(store, reason=("CO", "50"))
    hint = {"rule": "same_payer_paid_before", "reclass": "paid_before", "label": "Already paid by this payer, denied again",
            "hint": "Already paid by Blue Cross Test on 2026-03-10 ($90.00); this is a later denial of the same line. Nothing is owed "
                    "unless the payer takes that payment back.", "state_hint": "done", "strength": "likely",
            "proof": [{"date": "2026-03-10", "source": "835", "kind": "payment", "text": "Blue Cross paid $90.00", "decisive": True}]}
    item(store, signal="DENIED_MEDICAL_NECESSITY", carc="50", line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk,
         shortfall_cents=0, hint=hint["hint"], evidence={"archive_rule": hint})
    _fax(store, BCBS)
    out = story.explain(store.conn, "F1", today=TODAY)
    n = out["next_step"]
    assert out["state"]["rule"] == "check_first" and out["state"]["appeal_block"] == "likely_paid"
    assert not n["appeal_ok"] and "fax_payer" not in n["buttons"] and "build_packet" not in n["buttons"], n["buttons"]
    assert n["action"].startswith("Check first"), n["action"]
    assert "nobody has worked it" not in out["verdict"]["sentence"]


def test_an_archive_hint_that_says_look_it_up_first_is_a_check_first(store):
    """Practice-manager H2, L1: a hint that asks to look the primary's payment up before appealing is a check first; its
    text keeps the payer's name as written and is never cut mid-sentence."""
    tk = denied(store, reason=("CO", "50"))
    hint = {"rule": "second_payer_share", "reclass": "secondary_paid", "label": "Paid by a secondary; the primary's payment not on file",
            "hint": "Testcare paid $20.00 as secondary on 2026-04-10, and its remittance says a prior payer settled $70.00 of this service: "
                    "the primary's payment is probably on a remittance that is not on file. Look it up before appealing this denial; the "
                    "secondary's share is not an underpayment.", "state_hint": "work_now", "strength": "likely", "proof": []}
    item(store, signal="DENIED_MEDICAL_NECESSITY", carc="50", line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk,
         hint=hint["hint"], evidence={"archive_rule": hint})
    st = states.state_of(store.conn, dict(store.conn.execute("SELECT * FROM findings").fetchone()), today=TODAY)
    assert "testcare paid" not in st["state_basis"] and "Testcare paid" in st["state_basis"], st["state_basis"]
    assert " ; act by" not in st["state_basis"] and "that is not on ;" not in st["state_basis"], st["state_basis"]
    n = story.next_step(store.conn, "F1", today=TODAY)
    assert not n["appeal_ok"] and not n["action"].startswith("Appeal to"), n["action"]


def _reprocessed_to_patient(store, ledger, tmp_path, *, payer="BLUE CROSS TEST", cfi="12", **kw):
    """The payer reversed its CO-16 denial and reprocessed the line: $25.00 coinsurance to the patient and $125.00 CO-45.
    EMA has not posted the reprocessing, so EMA shows the whole $150.00 open."""
    tk = denied(store, reason=("CO", "16"), payer=payer, cfi=cfi)
    remit(store, trace="T2", paid_on="2026-04-20", payer=payer, claims=[
        {"pcn": F.claim_id(1), "status": "22", "cfi": cfi, "pccn": "PCN-A", "received": "2026-03-05",
         "lines": [ln("99213", -150, 0, [("CO", "16", -150)], lcn="1")]},
        {"pcn": F.claim_id(1), "status": "1", "cfi": cfi, "pccn": "PCN-A", "received": "2026-04-10",
         "lines": [ln("99213", 150, 0, [("PR", "2", 25), ("CO", "45", 125)], lcn="1")]}])
    item(store, signal="DENIED_MISSING_INFO", carc="16", line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk, **kw)
    load(store, ledger, tmp_path, [charge(F.link_no(1)), pay(F.link_no(1), F.check_no(1), 0), later()])


def test_bill_the_patient_only_their_share_never_the_contractual_amount(store, ledger, tmp_path):
    """Practice-manager H3: the patient is billed the $25.00 the 835 put on them, never EMA's $150.00 open balance (which
    holds the $125.00 contractual reduction EMA has not posted)."""
    _reprocessed_to_patient(store, ledger, tmp_path)
    n = story.next_step(store.conn, "F1", ledger=ledger, today=TODAY)
    assert n["route"] == "bill_patient" and n["action"].startswith("Bill the patient $25.00"), n["action"]
    assert "$150.00" not in n["action"].split(":")[0] and n["expected_cents"] == 2500
    assert any("post" in s_.lower() and "$125.00" in s_ for s_ in n["steps"]), n["steps"]


def test_a_medicaid_member_is_never_told_both_do_not_bill_and_bill(store, ledger, tmp_path):
    """Practice-manager H3: a Medicaid member is never billed; the step says so once, with no statement step and no Mark
    billed button."""
    _reprocessed_to_patient(store, ledger, tmp_path, payer="STATE MEDICAID TEST", cfi="MC", payer_name="STATE MEDICAID TEST",
                            payer_group="Medicaid (state)", payer_kind="medicaid")
    n = story.next_step(store.conn, "F1", ledger=ledger, today=TODAY)
    assert n["action"].startswith("Do not bill the patient")
    assert "bill the patient" not in n["action"][len("Do not bill the patient"):].lower(), n["action"]
    assert not any("next statement" in s_ for s_ in n["steps"]) and "mark_billed" not in n["buttons"]


def test_a_forwarded_claim_waits_for_the_carrier_not_for_work_nobody_did(store):
    """Practice-manager M5: a claim the first payer forwarded waits on the carrier it names; the step never speaks of work
    already done."""
    tk = remit(store, trace=F.check_no(1), paid_on="2026-03-28", claims=[{"pcn": F.claim_id(1), "status": "19", "crossover": "TESTCARE MEDIGAP",
               "pccn": "PCN-A", "received": "2026-03-05", "lines": [ln("99213", 150, 0, [("CO", "B11", 150)], lcn="1")]}])
    item(store, signal="DENIED_OTHER", carc="B11", line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    n = story.next_step(store.conn, "F1", today="2026-04-20")
    assert n["route"] == "wait" and "TESTCARE MEDIGAP" in n["action"], n["action"]
    assert "work already done" not in n["action"] and not any("quoting what was sent" in s_ for s_ in n["steps"]), n["steps"]


def test_ema_booking_denials_as_contractual_is_a_policy_decision_with_its_button(store):
    """Practice-manager M6, M7: EMA posting a payer's CO-97 denials as contractual is the owner's decision: the step offers
    Accept this pattern and carries the policy key the button reads; no sentence shows the key's pipes."""
    item(store, signal="EMA_DENIAL_AS_CONTRACTUAL", category="contract", actionable="review", carc="97", expected_cents=None, shortfall_cents=None,
         title="Anthem / BCBS: CO-97 denials posted as contractual in 2026, $1,500.00 on 12 lines", line_key=None, claim_key=None,
         transaction_key=None, patient_control_number=None, procedure_code=None, service_date=None, payment_date=None,
         evidence={"reason": "CO-97", "year": "2026", "top_codes": [["17311", 120000], ["17312", 30000]], "written_off_cents": 150000,
                   "lines": 12, "inside_window_cents": 90000,
                   "next_step": "Have EMA post this reason as non-contractual so it reaches the denial queue; then decide per code: appeal, "
                                "bill it differently, or stop billing it."})
    out = story.explain(store.conn, "F1", today=TODAY)
    n = out["next_step"]
    assert "contract review" not in n["action"] and "accept_pattern" in n["buttons"], (n["action"], n["buttons"])
    assert n["pattern"] == f"{BCBS}|||CO-97"
    assert not any("|" in t for t in _all_text(out)), _all_text(out)
    pol = {f"{BCBS}|||CO-97": {"decision": "accept", "by": "Owner One", "at": "2026-04-03T10:00:00"}}
    store.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('ema:writeoff_policy', ?)", (__import__("json").dumps(pol),))
    store.conn.commit()
    out = story.explain(store.conn, "F1", today=TODAY)
    assert not any("|" in t for t in _all_text(out)), _all_text(out)


def test_a_line_lost_to_timely_filing_is_not_appealed_as_if_it_were_not(store, ledger, tmp_path):
    """Practice-manager M9: EMA wrote the line off as lost to timely filing; the verdict says it ran out of time and the
    step never opens with an appeal."""
    tk = denied(store)
    item(store, line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    load(store, ledger, tmp_path, [charge(F.link_no(1)), pay(F.link_no(1), F.check_no(1), 0), adj(F.link_no(1), "CO-197", 150), later()])
    f = dict(store.conn.execute("SELECT * FROM findings WHERE finding_id='F1'").fetchone())
    base = ec.line_outcomes(store.conn, [f], code_once=lambda x: True, today=TODAY)["F1"]
    oc = dict(base, outcome="lost_to_timely_filing", carcs=["CO-29"], written_off=15000, outcome_on="2026-04-20")
    fx = states.facts_of(store.conn, [f], outcomes={"F1": oc}, today=TODAY)["F1"]
    fx["ctx"]["response"] = states.response_table(store.conn)
    sto = story._story(store.conn, fx, ledger)
    st = story._state_of(store.conn, fx, TODAY)
    v = story._verdict(store.conn, fx, sto, st, TODAY)
    n = story._next(store.conn, fx, sto, st, v, TODAY)
    assert "ran out of time" in v["sentence"]
    assert not n["appeal_ok"] and not n["action"].startswith("Appeal to"), n["action"]


def test_an_authorization_denial_starts_with_finding_the_authorization(store):
    """Practice-manager M10: never a blind appeal asserting the service was authorized; the step finds the authorization."""
    tk = denied(store)            # CO-197, DENIED_AUTH
    item(store, line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    n = story.next_step(store.conn, "F1", today=TODAY)
    assert "The service was authorized (or needed no authorization)" not in n["action"], n["action"]
    assert "find the authorization or referral" in n["action"] and not n["appeal_ok"], n["action"]


def test_medicare_steps_never_contradict_themselves(store):
    """Practice-manager M12: a Medicare redetermination is never faxed (no Fax button even with a number on file), and a
    Medicare fix is a reopening, never a corrected claim through the clearinghouse."""
    _medicare_item(store, signal="DENIED_MEDICAL_NECESSITY", carc="50", group="Medicare Part B")
    _fax(store, "Medicare Part B")
    n = story.next_step(store.conn, "F1", today=TODAY)
    assert "take no faxed" in n["action"] and "fax_payer" not in n["buttons"], n["buttons"]
    assert not any(s_.startswith("Fax to Medicare") for s_ in n["steps"]), n["steps"]
    item(store, finding_id="F2", signal="EM_BUNDLED_NO_MODIFIER", category="bundling", carc="4", actionable="correct_and_resubmit",
         payer_name="MEDICARE PART B TEST", payer_group="Medicare Part B", payer_kind="medicare", line_key=None,
         deadline_basis="Medicare Part B: a reopening for a clerical error within one year", evidence={"same_day_codes": ["11102"]})
    n = story.next_step(store.conn, "F2", today=TODAY)
    assert "takes no corrected claims" in n["action"], n["action"]
    assert not any("through the clearinghouse" in s_ for s_ in n["steps"]), n["steps"]
    assert any(s_.startswith("Ask for the reopening on the contractor's portal or by phone") for s_ in n["steps"]), n["steps"]


def test_a_secondary_never_billed_is_not_described_as_a_denial_to_appeal(store, ledger, tmp_path):
    """Practice-manager M14: the verdict of a second payer never billed names the plan to bill, never an appeal or a
    denial."""
    _secondary_world(store, ledger, tmp_path)
    s_ = story.item_verdict(store.conn, "F1", ledger=ledger, today=TODAY)["sentence"]
    assert "appeal" not in s_ and "(the denial)" not in s_ and "paid nothing" not in s_, s_


def test_the_era_button_always_has_an_era_request_to_copy(store, ledger, tmp_path):
    """Practice-manager M15: the Copy the ERA request button is offered only with a request to copy."""
    item(store, signal="EMA_DENIED", category="denial", actionable="correct_and_resubmit", carc="16", line_key=None, claim_key=None,
         transaction_key=None, payment_date="2026-03-31", title="Denied in EMA: no remittance on file", ema_link=F.link_no(1),
         evidence={"lines": [["99213", S]], "ema_link": F.link_no(1)})
    load(store, ledger, tmp_path, [charge(F.link_no(1), status="Denied"), pay(F.link_no(1), F.check_no(7), 0, status="Denied"),
                                   adj(F.link_no(1), "CO-16", 150, status="Denied"), later()])
    n = story.next_step(store.conn, "F1", ledger=ledger, today=TODAY)
    if "request_era" in n["buttons"]:
        assert any("ERA of the check" in s_ for s_ in n["steps"]), n["steps"]


def test_a_facility_claim_nobody_can_check_is_never_resent_as_a_new_claim(store):
    """Practice-manager M16: the surgery center's claim goes to another payee; the step checks the facility's EOB and
    never resends it as a new claim."""
    item(store, signal="EMA_DENIED", category="denial", actionable="appeal", carc="197", line_key=None, claim_key=None, transaction_key=None,
         payment_date="2026-03-31", patient_control_number=F.claim_id(1, facility=True), evidence={"lines": [["99213", S]], "facility": True})
    f = dict(store.conn.execute("SELECT * FROM findings WHERE finding_id='F1'").fetchone())
    fx = states.facts_of(store.conn, [f], today=TODAY)["F1"]
    fx["ctx"]["response"] = states.response_table(store.conn)
    st = {"work_state": "cant_check", "rule": "cant_check_facility", "deadline": "2027-12-31", "deadline_basis": "the payer's window",
          "state_basis": "The surgery center's facility claim: its remittances go to another payee, and EMA shows nothing posted. "
                         "Check the facility's EOB.", "label": "Can't be checked"}
    sto = story._story(store.conn, fx, None)
    n = story._next(store.conn, fx, sto, st, story._verdict(store.conn, fx, sto, st, TODAY), TODAY)
    assert not any("send it again now as a new claim" in s_ for s_ in n["steps"]), n["steps"]


def test_every_dollar_figure_of_the_next_step_has_its_basis_and_a_zero_shortfall_is_worth_zero(store):
    """Practice-manager M17, M21 (orchestrator decision 5): the next step's figure carries its basis; an estimate says so;
    a $0 shortfall is worth $0."""
    tk = denied(store, reason=("CO", "50"))
    item(store, signal="DENIED_MEDICAL_NECESSITY", carc="50", line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    n = story.next_step(store.conn, "F1", today=TODAY)
    assert n["expected_cents"] == 9000 and n["expected_basis"].startswith("RemitWatch's estimate"), n["expected_basis"]
    store.conn.execute("UPDATE findings SET shortfall_cents=0")
    store.conn.commit()
    assert story.next_step(store.conn, "F1", today=TODAY)["expected_cents"] == 0


def test_an_ema_only_denial_after_the_first_payers_payment_is_worth_what_the_denial_took(store, ledger, tmp_path):
    """Money review 11: a denial only EMA saw, posted after the first payer paid, is worth what it took in EMA (its
    write-off on the denial's day and what is still open), not the item's full estimate."""
    item(store, signal="EMA_DENIED", category="denial", actionable="appeal", carc="16", line_key=None, claim_key=None, transaction_key=None,
         payment_date="2026-04-20", expected_cents=9000, shortfall_cents=9000, charge_cents=15000, title="Denied in EMA: no remittance on file",
         ema_link=F.link_no(1), evidence={"lines": [["99213", S]], "ema_link": F.link_no(1)})
    load(store, ledger, tmp_path, [charge(F.link_no(1)), pay(F.link_no(1), F.check_no(1), 80), adj(F.link_no(1), "CO-45", 50, contractual=True),
                                   adj(F.link_no(1), "CO-16", 20, posted=D(2026, 4, 20), payer=P["commercial"]), later()])
    n = story.next_step(store.conn, "F1", ledger=ledger, today=TODAY)
    assert n["expected_cents"] == 2000, (n["expected_cents"], n["expected_basis"])
    assert n["expected_basis"] == "what the denial took in EMA (its write-off and what is still open)"


def test_the_window_sentence_names_the_work_the_route_asks_for(store):
    """Practice-manager L2: a corrected-claim item never says 'You can still appeal'."""
    tk = denied(store, reason=("CO", "4"))
    item(store, signal="EM_BUNDLED_NO_MODIFIER", category="bundling", carc="4", actionable="correct_and_resubmit", line_key=line_key(store, tk),
         claim_key=f"{tk}-0", transaction_key=tk, appeal_deadline="2026-06-26", deadline_basis="Blue Cross: 90 days to correct",
         evidence={"same_day_codes": ["11102"]})
    out = story.explain(store.conn, "F1", today=TODAY)
    assert out["next_step"]["route"] == "corrected"
    assert "You can still appeal" not in out["verdict"]["sentence"], out["verdict"]["sentence"]


def test_an_unposted_835_payment_reads_plainly(store):
    """Practice-manager L3: 'The 835 paid $90.00 ... but EMA never posted it'."""
    tk = remit(store, trace=F.check_no(3), paid_on="2026-03-28", claims=[{"pcn": F.claim_id(1), "pccn": "PCN-A", "received": "2026-03-05",
               "lines": [ln("99213", 150, 90, [("CO", "45", 60)], lcn="1")]}])
    item(store, signal="REMIT_NOT_POSTED", category="posting", actionable="review", carc=None, paid_cents=9000, shortfall_cents=9000,
         title="Paid on the 835, not posted in EMA", line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    out = story.explain(store.conn, "F1", today=TODAY)
    s_ = out["verdict"]["sentence"]
    assert s_.startswith("The 835 paid $90.00") and "but EMA never posted it" in s_, s_
    assert (out["state"]["rule"], out["state"]["appeal_block"], out["next_step"]["route"]) == ("post_payment", "post", "post")


def test_small_wording_and_button_rules(store):
    """Practice-manager L4, L5, L7: Reopen only where it can change the state; the LCD is named only for Medicare; other
    open items on the visit are named in words, not signal names."""
    item(store, signal="SEQUESTRATION", category="info", carc="253", title="Sequestration")
    assert "reopen" not in story.next_step(store.conn, "F1", today=TODAY)["buttons"]
    tk = denied(store, reason=("CO", "50"))
    item(store, finding_id="F2", signal="DENIED_MEDICAL_NECESSITY", carc="50", line_key=line_key(store, tk), claim_key=f"{tk}-0",
         transaction_key=tk)
    item(store, finding_id="F3", signal="DENIED_FREQUENCY", carc="151", procedure_code="17003", billed_code="17003", line_key=None)
    n = story.next_step(store.conn, "F2", today=TODAY)
    assert not any("(LCD)" in x for x in n["needs"] + n["steps"]), n["needs"]
    sib = [s_ for s_ in n["steps"] if s_.startswith("Also open on this visit")]
    assert sib and "DENIED_FREQUENCY" not in sib[0] and "17003 (denied: too many units or too often" in sib[0], sib


# ------------------------------------------------------------------------------------------- the balance line
def test_the_835_balance_counts_every_standing_payment_of_the_line(store):
    """Money review 6: with no EMA postings, the balance counts the second payer's $50.00 too, or says it does not
    reconcile; it never leaves money out and reconciles."""
    tk = remit(store, trace="T1", paid_on="2026-03-11", claims=[{"pcn": F.claim_id(1), "received": "2026-03-05",
        "lines": [ln("99214", 200, 80, [("CO", "45", 70), ("PR", "2", 50)], lcn="1", allowed=130)]}])
    item(store, signal="UNDERPAID_VS_OWN_RATE", category="underpayment", carc=None, procedure_code="99214", billed_code="99214",
         charge_cents=20000, paid_cents=8000, allowed_cents=13000, expected_cents=15000, shortfall_cents=2000,
         line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    remit(store, trace="T2", paid_on="2026-03-31", payer="SECONDARY TEST PLAN", claims=[
        {"pcn": F.claim_id(1, sub=2), "status": "2", "received": "2026-03-20",
         "lines": [ln("99214", 200, 50, [("OA", "23", 150)], lcn="1", allowed=130)]}])
    b = story.item_story(store.conn, "F1", today="2026-04-15")["balance"]
    assert b["basis"] == "835" and b["insurer_paid_cents"] == 13000 and b["reconciles"], b["text"]
    assert b["patient_owes_cents"] == 0 and b["open_cents"] == 0


def test_the_ar_check_adds_every_ar_row_of_the_line(store, ledger, tmp_path):
    """Money review 9: the line's A/R has a primary row ($40) and a secondary row ($20) in one pull; together they are the
    $60 EMA shows open, so the balance reconciles."""
    tk = denied(store, reason=("CO", "45"))
    item(store, signal="UNDERPAID_VS_OWN_RATE", category="underpayment", carc=None, paid_cents=0, expected_cents=6000,
         shortfall_cents=6000, line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    ar = [F.ar_row(claim=F.claim_id(1), link=F.link_no(1), svc=SVC, cpt="99213", current=40, coverage="Primary", aging=D(2026, 4, 28)),
          F.ar_row(claim=F.claim_id(1), link=F.link_no(1), svc=SVC, cpt="99213", current=20, coverage="Secondary", aging=D(2026, 4, 28))]
    load(store, ledger, tmp_path, [charge(F.link_no(1)), adj(F.link_no(1), "CO-45", 90, contractual=True), later()], ar_rows=ar)
    b = story.item_story(store.conn, "F1", ledger=ledger, today="2026-05-03")["balance"]
    assert b["open_cents"] == 6000 and b["reconciles"], b["text"]
    assert any(c["source"] == "ar" and c["agrees"] and c["rows"] == 2 for c in b["checks"]), b["checks"]


def test_the_copy_never_says_it_reconciles_where_the_server_shows_a_gap(store, ledger, tmp_path):
    """Money review 10: patient money for the patient, date and code that two claims share (the surgery center's claim) is
    a gap on the server; the dashboard's copy, which cannot see it, says the balance cannot be checked to the cent there."""
    tk = denied(store, code="17311", charge_=500, reason=("CO", "97"))
    item(store, signal="PROCEDURE_BUNDLED", category="bundling", carc="97", procedure_code="17311", billed_code="17311",
         charge_cents=50000, line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    load(store, ledger, tmp_path, [charge(F.link_no(1), cpt="17311", amount=500, patient=1, status="Denied"),
                                   pay(F.link_no(1), F.check_no(1), 0, cpt="17311", patient=1),
                                   charge(F.link_no(2), cpt="17311", amount=900, patient=1),
                                   pay(F.link_no(2), "ASC77", 450, cpt="17311", patient=1, posted=D(2026, 3, 30)),
                                   patient_pay(40, cpt="17311", patient=1, posted=D(2026, 4, 10)), later()])
    server = story.item_story(store.conn, "F1", ledger=ledger, today=TODAY)
    assert server["reconciles"] is False and server["gap_cents"] == 4000
    mem = dashboard_copy(store, tmp_path)
    copy = story.item_story(mem.conn, "F1", ledger=None, today=TODAY)
    assert copy["reconciles"] is None and "It reconciles to the cent" not in copy["balance"]["text"]
    assert "It cannot be checked to the cent on this site: patient money for this date and code is listed on the server" in copy["balance"]["text"]
    mem.close()


def test_a_patient_who_paid_a_denial_the_payer_put_on_the_practice_is_appealed_or_refunded(store, ledger, tmp_path):
    """Money review 7: EMA shows the line settled by the patient's $150.00 while the payer's CO-50 puts the service on the
    practice: never 'unpaid and nobody worked it', never done; appeal it and refund the patient, or refund the patient."""
    tk = denied(store, reason=("CO", "50"))
    item(store, signal="DENIED_MEDICAL_NECESSITY", carc="50", line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    load(store, ledger, tmp_path, [charge(F.link_no(1), status="Resolved/Paid"), pay(F.link_no(1), F.check_no(1), 0, status="Resolved/Paid"),
                                   patient_pay(150, posted=D(2026, 4, 10)), later()])
    out = story.explain(store.conn, "F1", ledger=ledger, today=TODAY)
    assert (out["verdict"]["state"], out["state"]["rule"], out["state"]["appeal_block"]) == ("work_now", "patient_paid_co", "patient_paid")
    assert out["verdict"]["sentence"].startswith("EMA shows the patient paid $150.00 and the line settled")
    assert "refund the patient" in out["next_step"]["action"] and not out["next_step"]["appeal_ok"]
    assert "nobody has worked it" not in out["verdict"]["sentence"]


def test_an_ema_follow_up_date_waits_until_that_day_then_asks_to_follow_up_once(store, ledger, tmp_path):
    """Money review 3: a follow-up date a person set in EMA holds the item in Waiting until that day; after it, the step
    says to read the notes and follow up, once (practice-manager M4: plain words)."""
    tk = denied(store, reason=("CO", "50"))
    item(store, signal="DENIED_MEDICAL_NECESSITY", carc="50", line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    ar = [F.ar_row(claim=F.claim_id(1), link=F.link_no(1), svc=SVC, cpt="99213", current=150, denial=150, status="Denied",
                   follow=D(2026, 5, 20), aging=D(2026, 4, 28))]
    load(store, ledger, tmp_path, [charge(F.link_no(1), status="Denied"), pay(F.link_no(1), F.check_no(1), 0, status="Denied"), later()], ar_rows=ar)
    out = story.explain(store.conn, "F1", ledger=ledger, today=TODAY)
    assert (out["verdict"]["state"], out["state"]["rule"], out["state"]["follow_up_on"]) == ("waiting", "ema_follow_up", "2026-05-20")
    assert out["next_step"]["route"] == "wait" and not out["next_step"]["appeal_ok"]
    out = story.explain(store.conn, "F1", ledger=ledger, today="2026-05-25")
    n = out["next_step"]
    assert (out["state"]["rule"], out["state"]["appeal_block"], n["route"]) == ("follow_up_due", "follow_up", "call")
    assert n["action"] == ("Read the claim's notes in EMA, then follow up with Anthem / BCBS: EMA has a follow-up date of May 20, 2026 on it; "
                           "that day passed with no answer from the payer on file.")
    assert n["action"].count("read the claim's notes") + n["action"].count("Read the claim's notes") == 1


@pytest.mark.parametrize("reason,later_pr,words", [
    (("OA", "18"), False, "this denial (OA-18) repeats a claim the payer already answered"),
    (("OA", "109"), False, "the payer's answer (OA-109) put nothing on the practice"),
    (("CO", "96"), True, "the payer's later answer of Apr 20, 2026 put this service on the patient (PR-1)"),
])
def test_a_patients_payment_settles_a_line_the_payer_never_put_on_the_practice(store, ledger, tmp_path, reason, later_pr, words):
    """Money review 7, measured on the real world: of the lines EMA shows settled by the patient, a duplicate denial, an
    OA answer and a denial the payer later reprocessed to the deductible are the patient's share, done; only a CO or PI
    denial with nothing later is the practice's to appeal or refund (the test above)."""
    tk = denied(store, reason=reason)
    if later_pr:
        remit(store, trace=F.check_no(2), paid_on="2026-04-20", claims=[{"pcn": F.claim_id(1, sub=2), "pccn": "PCN-B", "received": "2026-04-05",
              "lines": [ln("99213", 150, 0, [("CO", "45", 60), ("PR", "1", 90)], lcn="1")]}])
    item(store, signal="DENIED_NONCOVERED", carc=reason[1], line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    load(store, ledger, tmp_path, [charge(F.link_no(1), status="Resolved/Paid"), pay(F.link_no(1), F.check_no(1), 0, status="Resolved/Paid"),
                                   patient_pay(150, posted=D(2026, 4, 25)), later()])
    out = story.explain(store.conn, "F1", ledger=ledger, today=TODAY)
    assert (out["verdict"]["state"], out["state"]["rule"]) == ("done", "moved_to_patient"), out["verdict"]["sentence"]
    assert out["state"]["state_basis"].startswith("Settled by the patient:") and words in out["state"]["state_basis"], out["state"]["state_basis"]
    assert out["next_step"]["route"] == "nothing" and not out["next_step"]["appeal_ok"]


def test_an_informational_remittance_whose_money_never_came_is_asked_for_never_posted(store):
    """needs_B 0a.1, C54: the payer's remittance only informed (no deposit came with it or after it): ask the payer for the
    payment; never 'post it', and never a payment EMA failed to post."""
    tk = remit(store, trace=F.check_no(3), paid_on="2026-03-28", method="NON", claims=[{"pcn": F.claim_id(1), "pccn": "PCN-A",
               "received": "2026-03-05", "lines": [ln("99213", 150, 90, [("CO", "45", 60)], lcn="1")]}])
    item(store, signal="REMIT_NOT_POSTED", category="posting", actionable="review", carc=None, paid_cents=9000, shortfall_cents=9000,
         title="Paid on the 835, not posted in EMA", line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk,
         evidence={"informational": True})
    out = story.explain(store.conn, "F1", today=TODAY)
    n = out["next_step"]
    assert (out["state"]["rule"], out["state"]["appeal_block"], n["route"]) == ("payment_never_arrived", "call", "call")
    assert out["verdict"]["sentence"].startswith("The 835 reported $90.00 paid on an informational remittance")
    assert "never posted" not in out["verdict"]["sentence"]
    assert n["action"].startswith(f"Ask {BCBS} for the $90.00 its informational remittance of Mar 28, 2026 reported paid")
    assert "mark_posted" not in n["buttons"] and not n["appeal_ok"]
    plain(out)


def test_the_next_step_opens_the_claim_in_ema_when_its_address_is_set(store, ledger, tmp_path, monkeypatch):
    """needs_C1 F.2 2(a): with REMITWATCH_EMA_CLAIM_URL set ({claim} where the number goes) and the item's EMA claim number
    known, the next step carries the link and the Open in EMA button; without the setting, neither."""
    auto_writeoff_world(store, ledger, tmp_path, reason=("CO", "50"), signal="DENIED_MEDICAL_NECESSITY", carc="50", ema_link=F.link_no(1))
    assert story.next_step(store.conn, "F1", ledger=ledger, today=TODAY)["claim_url"] is None
    monkeypatch.setenv("REMITWATCH_EMA_CLAIM_URL", "https://ema.example.test/claims/{claim}")
    n = story.next_step(store.conn, "F1", ledger=ledger, today=TODAY)
    assert n["claim_url"] == f"https://ema.example.test/claims/{F.link_no(1)}" and "open_in_ema" in n["buttons"]
