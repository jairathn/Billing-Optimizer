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
    # its state is the one the daily run stored (the copy keeps only the remittances of the item's own claim)
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
    assert any("Accept this pattern" in s_ and "A single item is let go only by a person, with a reason." in s_ for s_ in n["steps"])
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
