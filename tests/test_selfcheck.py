"""
RemitWatch runs itself (remitwatch.selfcheck): self-regulation, the self-check of every run with safe mode, the weekly
self-audit, the track record, the pattern watch, the money checks, the recovery rates and the panel. Invented data only:
made-up claim ids, payers, members and amounts. Each part has a case where it must act and a case where it must not, on
both kinds of store: the server's file store and the compact in-memory store of the serverless host (and that store's
image, serialized and opened again the way cloud.load_raw does).
"""
from __future__ import annotations

import itertools
import json
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch import ema_ledger as el  # noqa: E402
from remitwatch import ema_reports as er  # noqa: E402
from remitwatch import selfcheck as sc  # noqa: E402
from remitwatch.detect import Detector, ensure_schema  # noqa: E402
from remitwatch.store import Store  # noqa: E402
from tests import ema_fixtures as F  # noqa: E402

TODAY = "2026-11-22"
SALT_HEX = "5e1fc0ffee" * 6 + "0123"
_TX = itertools.count(1)
CAN_SERIALIZE = hasattr(sqlite3.Connection, "serialize")


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(er, "CLAIM_HOST_SUFFIX", F.HOST_SUFFIX)
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    for k in ("VERCEL", "REMITWATCH_EMA_REPORTS", "REMITWATCH_EMA_PAYER_ALIASES", "REMITWATCH_EMA_LEDGER", "REMITWATCH_TODAY",
              "REMITWATCH_MEMORY_BUDGET_MB", "REMITWATCH_MAX_SECONDS", "REMITWATCH_CYCLE_BUDGET_SECONDS", "REMITWATCH_STALE_DAYS",
              "REMITWATCH_AUDIT_GAP_CENTS", "REMITWATCH_AUDIT_WASTED_ITEMS"):
        monkeypatch.delenv(k, raising=False)
    sc._GROUPS.clear()


def _new(kind: str, tmp_path) -> Store:
    s = Store(str(tmp_path / "s.sqlite")) if kind == "file" else Store(":memory:", compact=True)
    ensure_schema(s.conn)
    s.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('ema:key_salt', ?)", (SALT_HEX,))
    s.conn.commit()
    return s


class Kind:
    """A store of one kind; `image` builds the data in a compact store and reopens its serialized image (cloud.load_raw)."""

    def __init__(self, kind: str, tmp_path):
        self.kind, self.tmp = kind, tmp_path
        self.store = _new("compact" if kind == "image" else kind, tmp_path)

    @property
    def conn(self) -> sqlite3.Connection:
        return self.store.conn

    def reopen(self) -> None:
        """The serverless host's next invocation: the image saved and loaded again (a no-op for the other kinds)."""
        if self.kind == "image":
            data = self.store.to_bytes()
            self.store.close()
            self.store = Store.from_bytes(data)
            ensure_schema(self.store.conn)


@pytest.fixture(params=["file", "compact", "image"])
def kind(request, tmp_path):
    if request.param == "image" and not CAN_SERIALIZE:
        pytest.skip("this SQLite build cannot serialize a store image")
    k = Kind(request.param, tmp_path)
    yield k
    k.store.close()


@pytest.fixture(params=["file", "compact"])
def store(request, tmp_path):
    s = _new(request.param, tmp_path)
    yield s
    s.close()


# ------------------------------------------------------------------------------------------------------- invented rows
ITEM_DEFAULTS = dict(signal="DENIED_AUTH", category="denial", severity="high", actionable="appeal", title="Invented denial",
                     status="open", work_state="work_now", shortfall_cents=10000, expected_cents=10000, paid_cents=0,
                     payer_group="Testcare Health", payer_kind="commercial", payer_name="TESTCARE HEALTH",
                     service_date="2026-03-02", payment_date="2026-03-20", procedure_code="99213", carc="197",
                     patient_control_number="ZZ00T0001C001", appeal_deadline="2026-12-31", deadline_basis="invented window",
                     state_basis="Not paid and nobody has worked it; act by Dec 31, 2026", stale=0, evidence="{}",
                     first_seen="2026-03-21T00:00:00+00:00", last_seen="2026-11-22T00:00:00+00:00", run_id="r0")


def add_item(conn, fid: str, **kw) -> dict:
    row = dict(ITEM_DEFAULTS, finding_id=fid, **kw)
    if isinstance(row.get("evidence"), dict):
        row["evidence"] = json.dumps(row["evidence"])
    cols = list(row)
    conn.execute(f"INSERT OR REPLACE INTO findings ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [row[c] for c in cols])
    conn.commit()
    return row


def set_item(conn, fid: str, **kw) -> None:
    for k, v in kw.items():
        conn.execute(f"UPDATE findings SET {k} = ? WHERE finding_id = ?", (json.dumps(v) if isinstance(v, dict) else v, fid))
    conn.commit()


def remit(conn, paid_on: str, pcn: str, lines: list, *, payer="TESTCARE HEALTH", payer_id="TC001", status="1",
          trace=None, cfi="CI", npi=None, method="ACH", superseded=0) -> list[str]:
    """One remittance written straight into the 835 tables: lines are (code, charge, paid, [(group, reason, dollars)], dos,
    extra) with dollars. Returns the line keys."""
    n = next(_TX)
    tk = f"TX{n:05d}"
    trace = trace or f"9{n:08d}"
    cents = lambda v: None if v is None else int(round(v * 100))      # noqa: E731
    total = sum(cents(x[2]) for x in lines)
    conn.execute("""INSERT INTO transactions (transaction_key, trace_number, payment_date, payer_name, payer_id, superseded,
                    claim_paid_total_cents, payment_amount_cents, payment_method) VALUES (?,?,?,?,?,?,?,?,?)""",
                 (tk, trace, paid_on, payer, payer_id, superseded, total, total, method))
    ck = f"{tk}-C0"
    dos = lines[0][4] if len(lines[0]) > 4 else "2026-03-02"
    conn.execute("""INSERT INTO claims (claim_key, transaction_key, patient_control_number, claim_status_code, charge_cents, paid_cents,
                    claim_filing_indicator, payer_name, payer_id, payment_date, trace_number, statement_from, statement_to,
                    rendering_provider_npi) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (ck, tk, pcn, status, sum(cents(x[1]) for x in lines), total, cfi, payer, payer_id, paid_on, trace, dos, dos, npi))
    keys = []
    for j, x in enumerate(lines):
        code, charge, paid, adj = x[0], x[1], x[2], x[3] if len(x) > 3 else []
        d = x[4] if len(x) > 4 else dos
        extra = x[5] if len(x) > 5 else {}
        lk = f"{ck}-L{j}"
        conn.execute("""INSERT INTO lines (line_key, claim_key, transaction_key, procedure_code, charge_cents, paid_cents, allowed_cents,
                        paid_units, service_date, patient_control_number, claim_status_code, payer_name, payer_id, payment_date,
                        rendering_provider_npi, modifiers) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (lk, ck, tk, code, cents(charge), cents(paid), cents(extra.get("allowed", paid if paid else None)),
                      extra.get("units", 1), d, pcn, status, payer, payer_id, paid_on, npi, extra.get("mods")))
        for g, r, a in adj:
            conn.execute("""INSERT INTO adjustments (transaction_key, claim_key, line_key, level, group_code, reason_code, amount_cents)
                            VALUES (?,?,?,?,?,?,?)""", (tk, ck, lk, "line", g, r, cents(a)))
        keys.append(lk)
    conn.commit()
    return keys


def ema_line(conn, link: int, svc="2026-03-02", cpt="99213", **kw) -> None:
    row = dict(link=link, svc=svc, cpt=cpt, bill=kw.pop("bill", "ZZ00T0001"), payer_group="Testcare Health", payer_kind="commercial",
               charges=15000, ins_charges=15000, ins_paid=0, contractual=0, noncontractual=0, nonroutine=0, balance=15000,
               facility=0, ins_answer_rows=0, status_class="pending", first_charge_posted="2026-03-03")
    row.update(kw)
    cols = list(row)
    conn.execute(f"INSERT OR REPLACE INTO ema_lines ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [row[c] for c in cols])
    conn.commit()


def payment(conn, link: int, posted: str, cents: int, svc="2026-03-02", cpt="99213", check="880000001", in_835=0, role="primary",
            payer="Testcare Health") -> None:
    conn.execute("""INSERT OR REPLACE INTO ema_line_payments (link, svc, cpt, check_no, posted, payer, role, ins_paid, rows, in_835)
                    VALUES (?,?,?,?,?,?,?,?,1,?)""", (link, svc, cpt, check, posted, payer, role, cents, in_835))
    conn.commit()


def check_of(results: list, cid: str) -> dict:
    return next(r for r in results if r["id"] == cid)


# =================================================================================================== 1. self-regulation
def status(started: str, peak: int, seconds: float = 60, budget: float = 240, tmp: int | None = None, **kw) -> dict:
    st = {"started": started, "finished": started[:-5] + "59:59", "mode": "serverless", "seconds": seconds, "budget_seconds": budget,
          "memory_mb": {"load": peak // 2, "detect": peak, "peak": peak, "limit": 2048}}
    if tmp is not None:
        st["tmp_mb"] = {"ingest": tmp}
    st.update(kw)
    return st


def test_budgets_follow_the_host(monkeypatch):
    b = sc.budgets("serverless", budget_s=240)
    assert (b["memory_mb"], b["seconds"], b["tmp_mb"]) == (1600, 240.0, 512)
    s = sc.budgets("server")
    assert s["memory_mb"] is None and s["tmp_mb"] is None and s["seconds"] == 3600.0
    monkeypatch.setenv("REMITWATCH_MEMORY_BUDGET_MB", "3000")
    assert sc.budgets("server")["memory_mb"] == 3000


def test_a_run_past_80_percent_makes_the_next_run_spread_then_archive_then_shed_then_alert(store):
    c = store.conn
    plan = sc.plan_next(c, status("2026-11-01T10:00:00+00:00", 900), mode="serverless")
    assert plan["level"] == 0 and not plan["spread"]
    plan = sc.plan_next(c, status("2026-11-02T10:00:00+00:00", 1400), mode="serverless")      # 87% of 1,600 MB
    assert plan["level"] == 1 and plan["spread"] and not plan["archive_tight"] and plan["pressure"]["worst"] == "memory"
    assert any("one report file per run" in a for a in plan["actions"])
    plan = sc.plan_next(c, status("2026-11-03T10:00:00+00:00", 1350), mode="serverless")
    assert plan["level"] == 2 and plan["archive_tight"] and plan["skip"] == []
    assert sc.archive_budget_state(plan)["share"] >= 0.8         # unit E's archive takes the tighter horizon
    plan = sc.plan_next(c, status("2026-11-04T10:00:00+00:00", 1300), mode="serverless")
    assert plan["level"] == 3 and set(plan["skip"]) == set(sc.OPTIONAL_STEPS) and not plan["alert"]
    plan = sc.plan_next(c, status("2026-11-05T10:00:00+00:00", 1500), mode="serverless")
    assert plan["level"] == 4 and plan["alert"]
    kinds = {a["kind"]: a for a in sc.alert_candidates(c, today=TODAY)}
    assert "NEAR_LIMIT" in kinds and "Nothing was lost" in kinds["NEAR_LIMIT"]["title"]
    assert c.execute("SELECT COUNT(*) FROM selfcheck_cycles").fetchone()[0] == 5
    assert sc.regulation(c)["changes"][-1]["to"] == "alert"


def test_calm_runs_step_the_level_down_after_three_and_a_status_counts_once(store):
    c = store.conn
    sc.plan_next(c, status("2026-11-02T10:00:00+00:00", 1400), mode="serverless")
    again = sc.plan_next(c, status("2026-11-02T10:00:00+00:00", 1400), mode="serverless")      # the same run read twice
    assert again["level"] == 1
    for day in (3, 4):
        assert sc.plan_next(c, status(f"2026-11-0{day}T10:00:00+00:00", 600), mode="serverless")["level"] == 1
    assert sc.plan_next(c, status("2026-11-05T10:00:00+00:00", 600), mode="serverless")["level"] == 0


def test_time_tmp_and_a_killed_run_count_as_pressure(store):
    c = store.conn
    assert sc.plan_next(c, status("2026-11-02T10:00:00+00:00", 500, seconds=200), mode="serverless")["level"] == 1   # 83% of 240 s
    assert sc.plan_next(c, status("2026-11-03T10:00:00+00:00", 500, tmp=450), mode="serverless")["level"] == 2       # /tmp 88%
    killed = {"started": "2026-11-04T10:00:00+00:00", "mode": "serverless", "budget_seconds": 240}               # never finished
    p = sc.plan_next(c, killed, mode="serverless")
    assert p["level"] == 3 and p["pressure"]["killed"]
    p2 = sc.pressure_of({"started": "x", "finished": "y", "error": "MemoryError: out", "memory_mb": {"peak": 100}},
                        sc.budgets("serverless"))
    assert p2["pressured"] and p2["killed"]


def test_mark_records_memory_tmp_and_seconds():
    import time as _t
    out = {"mode": "serverless"}
    fig = sc.mark(out, "ingest", _t.time() - 1.5)
    assert out["memory_mb"]["ingest"] == fig["mem"] and fig["mem"] > 0
    assert "ingest" in out["tmp_mb"] and out["step_seconds"]["ingest"] >= 1.4
    server = {"mode": "server"}
    sc.mark(server, "detect")
    assert "tmp_mb" not in server and "detect" in server["memory_mb"]


# =================================================================================================== 2. the checks
def test_every_check_passes_on_a_clean_store(store):
    c = store.conn
    add_item(c, "F-open")
    add_item(c, "F-wait", work_state="waiting", follow_up_on="2026-12-10", state_basis="Sent on Nov 1; check on Dec 10")
    add_item(c, "F-paid", status="resolved", work_state="done", auto_note="Paid on a later remittance",
             evidence={"paid_later": {"payment_date": "2026-05-01", "paid_cents": 9000, "trace": "T1", "claim": "ZZ00T0002C002"}})
    res = sc.run_checks(c, today=TODAY)
    bad = [(r["id"], r["n"]) for r in res if r["status"] == "fail" and r["id"] not in ("feed_835",)]
    assert bad == []


def test_an_open_item_with_no_state_or_an_unknown_state_is_caught(store):
    c = store.conn
    add_item(c, "F-ok")
    add_item(c, "F-none", work_state=None)
    add_item(c, "F-odd", work_state="maybe")
    add_item(c, "F-closed-none", status="resolved", work_state=None)      # a closed item is not asked for a state
    r = sc.check_state_valid(c, TODAY)
    assert r["status"] == "fail" and sorted(r["examples"]) == ["F-none", "F-odd"]


def test_a_waiting_item_without_its_follow_up_day_or_with_a_past_one_is_caught(store):
    c = store.conn
    add_item(c, "F-w1", work_state="waiting", follow_up_on=None)
    add_item(c, "F-w2", work_state="waiting", follow_up_on="2026-11-01")
    add_item(c, "F-w3", work_state="waiting", follow_up_on="2026-12-01")
    r = sc.check_followup_date(c, TODAY)
    assert sorted(r["examples"]) == ["F-w1", "F-w2"]


def test_nothing_is_both_open_and_closed(store):
    c = store.conn
    add_item(c, "F-1", status="resolved", work_state="work_now", auto_note="Paid on a later remittance",
             evidence={"paid_later": {"payment_date": "2026-05-01", "paid_cents": 1, "trace": "T"}})
    add_item(c, "F-2", status="open", auto_note="Paid on a later remittance")
    add_item(c, "F-3", status="lost", work_state="waiting", follow_up_on="2026-12-01")
    add_item(c, "F-4", status="dismissed", work_state="accepted")
    add_item(c, "F-5", status="resolved", work_state="expired", auto_note="Paid on a later remittance",
             evidence={"paid_later": {"payment_date": "2026-05-01", "paid_cents": 1, "trace": "T"}})
    r = sc.check_open_and_closed(c, TODAY)
    assert sorted(r["examples"]) == ["F-1", "F-2", "F-3", "F-5"]
    assert r["info"]["by_kind"] == {"closed_but_listed": 2, "open_with_closing_note": 1, "closed_by_analysis_not_done": 1}


def test_an_automatic_closure_without_its_proof_is_caught(store):
    c = store.conn
    add_item(c, "F-proof", status="resolved", work_state="done", auto_note="Paid",
             evidence={"paid_later": {"payment_date": "2026-05-01", "paid_cents": 9000, "trace": "T1"}})
    add_item(c, "F-rows", status="resolved", work_state="done", auto_note="Paid",
             evidence={"paid_later": {"proof": [{"date": "2026-05-01", "amount_cents": 9000, "source": "production"}]}})
    add_item(c, "F-archive", status="resolved", work_state="done", auto_note="Paid",
             evidence={"archive_rule": {"action": "close_with_proof", "proof": [{"date": "2026-04-01", "amount_cents": 500}]}})
    add_item(c, "F-bare", status="resolved", work_state="done", auto_note="Paid", evidence={})
    add_item(c, "F-nomoney", status="resolved", work_state="done", auto_note="Paid",
             evidence={"paid_later": {"payment_date": "2026-05-01", "paid_cents": 0, "trace": "T1"}})
    add_item(c, "F-person", status="resolved", work_state="done", evidence={})        # a person marked it paid: not automatic
    r = sc.check_closure_proof(c, TODAY)
    assert sorted(r["examples"]) == ["F-bare", "F-nomoney"]


def test_a_surgery_center_payment_never_closes_the_clinicians_line(store):
    c = store.conn
    ema_line(c, 90000007, facility=1)
    add_item(c, "F-twin", status="resolved", work_state="done", auto_note="EMA paid", ema_link=90000001,
             evidence={"paid_later": {"payment_date": "2026-05-01", "paid_cents": 9000, "trace": "EMA", "ema_link": 90000007}})
    add_item(c, "F-fclaim", status="resolved", work_state="done", auto_note="Paid",
             evidence={"paid_later": {"payment_date": "2026-05-01", "paid_cents": 9000, "trace": "T", "claim": "FZZ00T0001C001"}})
    add_item(c, "F-own", status="resolved", work_state="done", auto_note="EMA paid", ema_link=90000007,
             evidence={"paid_later": {"payment_date": "2026-05-01", "paid_cents": 9000, "trace": "EMA", "ema_link": 90000007}})
    r = sc.check_facility_twin(c, TODAY)
    assert sorted(r["examples"]) == ["F-fclaim", "F-twin"]


def test_the_precision_guard_catches_work_now_items_a_later_payment_covers(kind):
    c = kind.conn
    lk = remit(c, "2026-03-20", "ZZ00T0001C001", [("99213", 150, 0, [("CO", "197", 150)])])[0]
    remit(c, "2026-05-20", "ZZ00T0001C002", [("99213", 150, 98, [("CO", "45", 52)])])          # paid on the bill's resubmission
    add_item(c, "F-paid", line_key=lk, shortfall_cents=9800, transaction_key="TX-none")
    # paid by EMA (a check no 835 carries) after the denial
    lk2 = remit(c, "2026-03-20", "ZZ00T0002C001", [("99214", 200, 0, [("CO", "16", 200)])])[0]
    add_item(c, "F-ema", line_key=lk2, patient_control_number="ZZ00T0002C001", procedure_code="99214", shortfall_cents=12000,
             ema_link=90000002)
    ema_line(c, 90000002, cpt="99214", bill="ZZ00T0002")
    payment(c, 90000002, "2026-06-02", 12000, cpt="99214", check="880000555")
    # two lines of one code on one claim: a later payment for one of them does not cover both
    lks = remit(c, "2026-03-20", "ZZ00T0003C001", [("11102", 100, 0, [("CO", "151", 100)]), ("11102", 100, 0, [("CO", "151", 100)])])
    remit(c, "2026-06-20", "ZZ00T0003C002", [("11102", 100, 60, [])])
    add_item(c, "F-sib1", line_key=lks[0], patient_control_number="ZZ00T0003C001", procedure_code="11102", shortfall_cents=6000)
    add_item(c, "F-sib2", line_key=lks[1], patient_control_number="ZZ00T0003C001", procedure_code="11102", shortfall_cents=6000)
    # a credit to refund is not money to collect
    add_item(c, "F-credit", line_key=lk, appeal_block="credit", shortfall_cents=100, state_basis="EMA shows a credit of $1.00")
    kind.reopen()
    r = sc.check_precision_guard(kind.conn, TODAY)
    assert sorted(r["examples"]) == ["F-ema", "F-paid"]


def test_an_unworked_item_past_every_window_and_its_grace_is_caught(store):
    c = store.conn
    add_item(c, "F-late", appeal_deadline="2026-09-01")                                     # 82 days past, nobody worked it
    add_item(c, "F-grace", appeal_deadline="2026-11-10")                                    # inside the 30-day grace
    add_item(c, "F-legal", appeal_deadline="2026-09-01", legal_deadline="2027-03-01")       # the payer's own limit is later
    add_item(c, "F-work", appeal_deadline="2026-09-01", state_basis="Anthem received a new claim; no answer by Oct 1: ask")
    add_item(c, "F-rule", appeal_deadline="2026-09-01", state_basis="whatever the words", state_rule="open") \
        if "state_rule" in sc._cols(c, "findings") else None
    r = sc.check_past_window(c, TODAY)
    assert "F-late" in r["examples"] and not {"F-grace", "F-legal", "F-work"} & set(r["examples"])
    assert r["info"]["past_last_window_in_grace"] == 1


def test_items_to_work_without_a_deadline_are_a_warning(store):
    c = store.conn
    add_item(c, "F-dl")
    add_item(c, "F-nodl", appeal_deadline=None, signal="DENIED_NONCOVERED")
    r = sc.check_deadline_basis(c, TODAY)
    assert r["severity"] == "warning" and r["examples"] == ["F-nodl"] and r["info"]["by_signal"] == {"DENIED_NONCOVERED": 1}


def test_counts_equal_the_lists_they_open(store):
    c = store.conn
    add_item(c, "F-a")
    add_item(c, "F-b", category="bundling")
    c.execute("""INSERT INTO progress_snapshots (run_id, at, category, status, n, cents)
                 SELECT 'r9', 'now', category, status, COUNT(*), SUM(shortfall_cents) FROM findings GROUP BY 1, 2, 3, 4""")
    c.commit()
    assert sc.check_counts_lists(c, TODAY, "r9")["status"] == "ok"
    add_item(c, "F-c")                                       # the list grew after the count was taken
    r = sc.check_counts_lists(c, TODAY, "r9")
    assert r["status"] == "fail" and any(k.startswith("progress:denial") for k, _c in r["keys"])
    sc.register_pair("page", lambda cn, t: (5, 0), lambda cn, t: [("x", 0)])
    try:
        r2 = sc.check_counts_lists(c, TODAY)
        assert any(k == "page" for k, _c in r2["keys"])
    finally:
        sc._EXTRA_PAIRS.clear()


def test_the_feeds_freshness(store, monkeypatch):
    c = store.conn
    remit(c, "2026-11-20", "ZZ00T0001C001", [("99213", 150, 80)])
    assert sc.check_feed_835(c, TODAY)["status"] == "ok"
    assert sc.check_feed_835(c, "2026-12-10")["status"] == "fail"
    assert sc.check_feed_ema(c, TODAY)["status"] == "skip"


def test_a_check_that_crashes_is_reported_not_swallowed(store, monkeypatch):
    c = store.conn
    add_item(c, "F-1")
    monkeypatch.setitem(sc.ITEM_CHECKS, "state_valid", lambda cn, t: 1 / 0)
    r = check_of(sc.run_checks(c, today=TODAY), "state_valid")
    assert r["status"] == "error" and "ZeroDivisionError" in r["info"]["error"]


# ======================================================================================= the run: baseline, safe mode
def run(conn, mutate=None, rid="r1", today=TODAY):
    """One detection run as the hooks will drive it: begin_detection, the run's save (`mutate`), after_detection."""
    ctx = sc.begin_detection(conn, run_id=rid, today=today)
    if mutate:
        mutate(conn)
        conn.commit()
    return sc.after_detection(conn, ctx, mode="server", today=today, run_id=rid)


def bad_closure(conn):
    conn.execute("""UPDATE findings SET status='resolved', work_state='done', auto_note='Paid on a later remittance',
                    prev_status='open', evidence='{}' WHERE finding_id='F-2'""")
    conn.execute("""INSERT INTO finding_events (finding_id, at, kind, from_status, to_status, actor, note, run_id)
                    VALUES ('F-2', 'now', 'paid_on_later_remit', 'open', 'resolved', 'system', 'Paid', 'r2')""")


def test_the_first_run_sets_the_baseline_and_holds_nothing(kind):
    c = kind.conn
    add_item(c, "F-1")
    add_item(c, "F-2")
    out = run(c, bad_closure, "r1")
    assert out["first"] and not out["held"] and "closure_proof" in out["failing"]
    assert c.execute("SELECT status FROM findings WHERE finding_id='F-2'").fetchone()[0] == "resolved"
    assert sc.safe_mode(c)["not_held"]["checks"] == ["closure_proof"]
    kinds = {a["kind"] for a in sc.alert_candidates(c, today=TODAY)}
    assert "CHECK_FAILED" in kinds and "SAFE_MODE" not in kinds


def test_a_critical_failure_holds_the_run_and_the_next_good_run_releases_it(kind):
    c = kind.conn
    add_item(c, "F-1")
    add_item(c, "F-2")
    assert not run(c, None, "r1")["held"]                                    # the baseline: a clean run
    kind.reopen()
    c = kind.conn
    c.execute("UPDATE findings SET notes = 'a person wrote this' WHERE finding_id = 'F-1'")      # a person's change since
    c.commit()
    out = run(c, bad_closure, "r2")
    assert out["held"] and out["hold_checks"] == ["closure_proof"]
    # what people see is the worklist from before the run, with the person's note; the run's closure is held
    st, note = c.execute("SELECT status, notes FROM findings WHERE finding_id='F-2'").fetchone()[0], \
        c.execute("SELECT notes FROM findings WHERE finding_id='F-1'").fetchone()[0]
    assert st == "open" and note == "a person wrote this"
    assert c.execute("SELECT COUNT(*) FROM finding_events WHERE run_id='r2'").fetchone()[0] == 0
    held = sc.held_copy(c)
    assert held["run_id"] == "r2" and any("F-2" in r for r in held["findings"]["rows"]) and len(held["events"]["rows"]) == 1
    safe = sc.safe_mode(c)
    assert safe["on"] and safe["held_closures"] == 1 and safe["checks"][0]["id"] == "closure_proof"
    assert any(a["kind"] == "SAFE_MODE" for a in sc.alert_candidates(c, today=TODAY))
    assert any(d["key"] == "selfcheck_safe_mode" for d in sc.data_health(c, TODAY))
    kind.reopen()
    c = kind.conn
    out = run(c, None, "r3")                                                 # the bug is fixed: a clean run
    assert not out["held"] and not sc.safe_mode(c)["on"] and sc.safe_mode(c)["released"]["run_id"] == "r3"
    assert sc.held_copy(c) == {}
    assert [h["held"] for h in sc.meta_get(c, sc.K_HISTORY)] == [False, True, False]


def test_offenders_the_last_good_run_had_stay_red_without_freezing_the_worklist(store):
    c = store.conn
    add_item(c, "F-1")
    add_item(c, "F-2")
    run(c, bad_closure, "r1")                                                # first run: the bad closure is the baseline
    add_item(c, "F-3")
    out = run(c, None, "r2")
    assert not out["held"] and "closure_proof" in out["failing"] and out["new"]["closure_proof"] == 0
    # a NEW offender beyond the tolerance holds the run
    out = run(c, lambda cn: cn.execute("UPDATE findings SET status='resolved', auto_note='x', evidence='{}' WHERE finding_id='F-3'"), "r3")
    assert out["held"] and out["new"]["closure_proof"] == 1


def test_a_person_can_accept_a_change_and_the_next_run_uses_it(store):
    c = store.conn
    add_item(c, "F-1")
    add_item(c, "F-2")
    run(c, None, "r1")
    assert run(c, bad_closure, "r2")["held"]
    acc = sc.accept_findings(c, "owner@example.test", ["closure_proof"])
    assert acc["accepted"] == {"closure_proof": 1}
    out = run(c, bad_closure, "r3")
    assert not out["held"]
    assert c.execute("SELECT accepted_by FROM selfcheck_baseline WHERE check_id='closure_proof'").fetchone()[0] == "owner@example.test"


def test_an_item_that_vanishes_with_no_reason_is_kept_and_a_replaced_one_is_not(kind):
    c = kind.conn
    add_item(c, "F-gone", line_key="L1")
    add_item(c, "F-old", line_key="L2", signal="DENIED_AUTH")
    add_item(c, "F-done", line_key="L3", work_state="done")
    run(c, None, "r1")
    kind.reopen()
    c = kind.conn

    def save(cn):
        cn.execute("DELETE FROM findings")                                   # the run's save, with a rule that stopped
        add_item(cn, "F-new", line_key="L2", signal="DENIED_MISSING_INFO")   # the line's item under its new signal
    out = run(c, save, "r2")
    assert not out["held"] and out["kept"] == 1
    ids = {r[0] for r in c.execute("SELECT finding_id FROM findings")}
    assert ids == {"F-gone", "F-new"}
    assert sc.KEPT_HINT in c.execute("SELECT hint FROM findings WHERE finding_id='F-gone'").fetchone()[0]
    assert c.execute("SELECT kind FROM finding_events WHERE finding_id='F-gone'").fetchone()[0] == "kept_by_selfcheck"
    van = check_of(sc.meta_get(c, sc.K_LAST)["checks"], "vanished")
    assert van["status"] == "fail" and van["examples"] == ["F-gone"]


def test_the_real_detector_with_a_dropped_rule_keeps_its_items(kind, monkeypatch):
    """The owner's fault 1 (a code bug that drops a category of items) on the real Detector: the items stay listed."""
    c = kind.conn
    for i in range(3):
        remit(c, "2026-10-20", f"ZZ00T000{i}C001", [("99213", 150, 0, [("CO", "197", 150)], "2026-10-01")])
        remit(c, "2026-10-21", f"ZZ00T000{i}C002", [("99214", 200, 120, [("CO", "45", 80)], "2026-10-02")])
    ctx = sc.begin_detection(c, run_id="d1", today=TODAY)
    Detector(kind.store, "d1", today=TODAY).run()
    first = sc.after_detection(c, ctx, mode="server", today=TODAY, run_id="d1")
    n0 = c.execute("SELECT COUNT(*) FROM findings WHERE work_state IN ('work_now','waiting')").fetchone()[0]
    assert n0 > 0 and first["first"]
    kind.reopen()
    c = kind.conn
    monkeypatch.setattr(Detector, "run_line_rules", lambda self: None)       # the bug: line rules dropped
    ctx = sc.begin_detection(c, run_id="d2", today=TODAY)
    Detector(kind.store, "d2", today=TODAY).run()
    out = sc.after_detection(c, ctx, mode="server", today=TODAY, run_id="d2")
    assert out["kept"] == n0
    assert c.execute("SELECT COUNT(*) FROM findings WHERE work_state IN ('work_now','waiting')").fetchone()[0] == n0


# =================================================================================================== 4. the track record
def test_the_track_record_follows_waits_closures_reopenings_missed_deadlines_and_let_go(store):
    c = store.conn
    add_item(c, "F-wait", work_state="waiting", follow_up_on="2026-12-15")
    add_item(c, "F-close")
    add_item(c, "F-miss", appeal_deadline="2026-10-01")
    add_item(c, "F-go", status="dismissed", work_state="accepted")
    run(c, None, "r1", today="2026-11-01")

    def day2(cn):
        cn.execute("""UPDATE findings SET status='resolved', work_state='done', auto_note='Paid on a later remittance',
                      evidence=? WHERE finding_id IN ('F-wait','F-close')""",
                   (json.dumps({"paid_later": {"payment_date": "2026-11-20", "paid_cents": 9000, "trace": "T9"}}),))
        cn.execute("UPDATE findings SET work_state='expired' WHERE finding_id='F-miss'")
        cn.execute("UPDATE findings SET hint='A later payment was found; kept as dismissed because a person set it' "
                   "WHERE finding_id='F-go'")
    run(c, day2, "r2", today="2026-11-21")

    def day3(cn):
        cn.execute("UPDATE findings SET status='open', work_state='work_now', auto_note=NULL WHERE finding_id='F-close'")
    run(c, day3, "r3", today="2026-11-22")
    tr = sc.track_record(c, today=TODAY)
    assert tr["waits"] == {"paid": 1} and tr["closures"] == 2 and tr["reopened"] == 1 and tr["closure_error_rate"] == 0.5
    assert tr["let_go_paid"]["n"] == 1
    assert sum(w["missed_n"] for w in tr["weeks"]) == 1
    assert c.execute("SELECT days FROM selfcheck_track WHERE kind='waiting'").fetchone()[0] == 20


# ====================================================================================== 8. money: the ledger and the store
def prod_rows():
    """One claim paid by insurance and the patient; one denied and written off at posting; patient money for a visit with
    no claim number that matches no line (a visible gap)."""
    P = F.PAYERS
    D = F.D
    return [
        F.production_row(posted=D(2026, 3, 4), svc=D(2026, 3, 2), link=F.link_no(1), charges=150.0, payer=P["bcbs"]),
        F.production_row(posted=D(2026, 3, 20), svc=D(2026, 3, 2), link=F.link_no(1), ins_paid=90.0, contractual=40.0,
                         reason="CO-45", check="880000001", payer=P["bcbs"]),
        F.production_row(posted=D(2026, 3, 25), svc=D(2026, 3, 2), link=None, pat_paid=20.0, payer="Patient"),
        F.production_row(posted=D(2026, 3, 4), svc=D(2026, 3, 3), cpt="99214", link=F.link_no(2), charges=200.0, payer=P["bcbs"]),
        F.production_row(posted=D(2026, 3, 21), svc=D(2026, 3, 3), cpt="99214", link=F.link_no(2), noncontractual=200.0,
                         reason="CO-197", check="880000002", payer=P["bcbs"]),
        F.production_row(posted=D(2026, 3, 30), svc=D(2026, 3, 9), cpt="99212", link=None, pat_paid=35.0, payer="Patient", patient=1),
    ]


@pytest.fixture
def ledger_world(tmp_path):
    s = _new("file", tmp_path)
    lg = el.Ledger.open(str(tmp_path / "ema_ledger.sqlite"))
    path = F.write(str(tmp_path / "prod.csv"), F.production_csv(prod_rows()))
    res = er.ingest_file(s, path, via="test", ledger=lg, log=lambda *a: None)
    assert res["status"] == "current", res
    el.refresh(s, lg, log=lambda *a: None)
    yield s, lg
    lg.close()
    s.close()


def test_the_store_and_the_ledger_agree_and_a_changed_month_is_caught(ledger_world):
    s, lg = ledger_world
    out = sc.check_ledger(s.conn, lg, today=TODAY)
    ls = check_of(out["checks"], "ledger_store")
    assert ls["status"] == "ok" and ls["info"]["month_cells"] > 0 and ls["info"]["lines_sampled"] >= 1
    s.conn.execute("UPDATE ema_months SET cents = cents + 5000 WHERE measure = 'ins_paid'")
    s.conn.commit()
    ls = check_of(sc.check_ledger(s.conn, lg, today=TODAY)["checks"], "ledger_store")
    assert ls["status"] == "fail" and ls["n"] >= 1 and ls["cents"] >= 5000
    # the summaries are of another ledger revision: not compared until the refresh brings them level
    s.conn.execute("UPDATE meta SET value='999' WHERE key='ema:ledger_seq'")
    s.conn.commit()
    assert check_of(sc.check_ledger(s.conn, lg, today=TODAY)["checks"], "ledger_store")["status"] == "skip"


def test_money_conservation_adds_up_and_patient_money_on_no_line_is_a_visible_gap(ledger_world):
    s, lg = ledger_world
    out = sc.check_ledger(s.conn, lg, today=TODAY)
    mc = check_of(out["checks"], "money_conservation")
    assert mc["status"] == "ok"
    cons = out["conservation"]
    t = cons["totals_since"]
    assert t["charges"] == 35000 and t["ins_paid"] == 9000 and t["contractual"] == 4000
    assert t["pat_paid"] == 2000 and t["wo_at_posting"] == 20000 and t.get("open", 0) == 0
    assert cons["unlinked_since_cents"] == 3500 and cons["patient_money_joined_cents"] == 2000
    pieces = sum(t.get(k, 0) for k in ("ins_paid", "pat_paid", "contractual", "wo_at_posting", "wo_later", "wo_timely", "pat_adj",
                                       "open")) - t.get("credit", 0)
    assert pieces == t["charges"]
    assert s.conn.execute("SELECT COUNT(*) FROM selfcheck_money").fetchone()[0] > 0
    # a reason the rules do not know puts its money in no piece: the cell no longer adds up
    import remitwatch.ema_ledger as elm
    real = elm.reason_class
    try:
        elm.reason_class = lambda g, c, k=None: None
        bad = check_of(sc.check_ledger(s.conn, lg, today=TODAY)["checks"], "money_conservation")
    finally:
        elm.reason_class = real
    assert bad["status"] == "fail" and bad["cents"] >= 24000


def test_an_ar_line_above_what_its_postings_leave_does_not_reconcile(ledger_world):
    s, lg = ledger_world
    c = s.conn
    c.execute("""INSERT INTO ema_ar (file_id, row_no, as_of, link, svc, cpt, party, balance_cents, facility)
                 VALUES ('ar1', 1, '2026-11-20', ?, '2026-03-02', '99213', 'Insurance', 0, 0),
                        ('ar1', 2, '2026-11-20', ?, '2026-03-03', '99214', 'Insurance', 15000, 0)""", (F.link_no(1), F.link_no(2)))
    c.commit()
    out = sc.check_ledger(c, lg, today=TODAY)
    lb = check_of(out["checks"], "line_balance")
    assert lb["severity"] == "warning" and lb["n"] == 1 and lb["cents"] == 15000
    assert out["ar_balance"]["postings_leave_more"] == 1          # the patient's $20 share, paid on no line: not a gap


def test_given_up_counts_write_offs_at_posting_with_no_decision_or_policy(store):
    c = store.conn
    common = dict(written_off_cents=5000, written_off_when="at_posting", service_date="2026-03-02", carc="197")
    add_item(c, "F-open", **common)                                                          # recoverable (window open)
    add_item(c, "F-lost", appeal_deadline="2026-06-01", **common)                            # window closed
    add_item(c, "F-let", status="dismissed", **common)                                       # a person decided
    add_item(c, "F-later", **dict(common, written_off_when="later"))                         # a person wrote it off later
    add_item(c, "F-old", **dict(common, service_date="2024-06-01"))                          # before 2025
    add_item(c, "F-policy", payer_group="Policy Payer", procedure_code="17000", **common)
    sc.meta_set(c, "ema:writeoff_policy", {"Policy Payer|17000||CO-197": {"decision": "accept", "by": "owner"}})
    g = sc.given_up(c, today=TODAY)
    assert (g["items"], g["recoverable_cents"], g["lost_cents"]) == (2, 5000, 5000)
    assert g["by_reason"][0]["reason"] == "197" and g["weeks"][-1]["cents"] == 10000
    add_item(c, "F-more", written_off_cents=60000, written_off_when="at_posting", service_date="2026-03-02")
    sc.meta_set(c, sc.K_GIVEN_UP, dict(sc.meta_get(c, sc.K_GIVEN_UP), weeks=[{"week": "2026-W46", "cents": 10000}]))
    sc.given_up(c, today=TODAY)
    assert any(a["kind"] == "GIVEN_UP_RISING" for a in sc.alert_candidates(c, today=TODAY))


def test_blindness_says_what_the_app_cannot_see_in_dollars(store):
    c = store.conn
    remit(c, "2026-10-09", "ZZ00T0001C001", [("99213", 150, 80)])
    c.execute("INSERT INTO ema_months (month, payer, measure, detail, cents, rows) VALUES ('2026-10', 'X', 'charges', '', 310000, 10),"
              " ('2026-11', 'X', 'charges', '', 100000, 5)")
    c.commit()
    b = sc.blindness(c, today=TODAY)
    assert b["blind"] and b["sources"][0]["source"] == "835" and b["sources"][0]["days"] == 44
    assert b["sources"][0]["cents"] == 310000 * 22 // 31 + 100000 and "never" not in b["banner"]
    assert sc.data_health(c, TODAY)[0]["key"] == "selfcheck_blind"
    assert not sc.blindness(c, today="2026-10-15")["blind"]


def test_the_dashboard_copy_is_compared_with_the_store(store, tmp_path):
    from remitwatch import cloud
    c = store.conn
    add_item(c, "F-1")
    path = str(tmp_path / "serving.sqlite")
    cloud.build_serving(c, path)
    copy = sqlite3.connect(path)
    assert sc.check_serving(c, copy)["status"] == "ok"
    add_item(c, "F-2")
    r = sc.check_serving(c, copy)
    assert r["status"] == "fail" and sc.meta_get(c, "selfcheck:serving")["status"] == "fail"
    copy.close()


# ====================================================================================== 12. recovery rates and the ranking
def rates_world(c):
    """Twelve matured denials of 99213 (CO-197) at Testcare: four later paid in full."""
    for i in range(12):
        pcn = f"ZZ00T{100 + i:04d}C001"
        remit(c, "2026-01-10", pcn, [("99213", 150, 0, [("CO", "197", 150)])])
        if i < 4:
            remit(c, "2026-02-10", pcn, [("99213", 150, 90, [("CO", "45", 60)])])
    for i in range(6):                                                   # the payer's usual amount for the code
        remit(c, "2026-01-05", f"ZZ00T{300 + i:04d}C001", [("99213", 150, 90, [("CO", "45", 60)])])
    for i in range(3):                                                   # a thin pattern: 11102 denied three times
        remit(c, "2026-01-10", f"ZZ00T{200 + i:04d}C001", [("11102", 120, 0, [("CO", "197", 120)])])
    remit(c, "2026-08-01", "ZZ00T0999C001", [("99213", 150, 90, [("CO", "45", 60)])])    # the newest remittance


def test_recovery_rates_by_pattern_with_their_counts(store):
    c = store.conn
    rates_world(c)
    r = sc.recovery_rates(c)
    v = r["levels"]["gcr"]["|".join((sc._group_kind("TESTCARE HEALTH", "CI")[0], "99213", "197"))]
    assert (v["n"], v["paid_n"]) == (12, 4) and v["rate"] == pytest.approx(4 / 12, abs=0.01) and v["median_days"] == 31
    assert sc.meta_get(c, sc.K_RATES)["cases"] == 15


def test_expected_value_falls_back_level_by_level_and_says_so(store):
    c = store.conn
    rates_world(c)
    r = sc.recovery_rates(c)
    grp = sc._group_kind("TESTCARE HEALTH", "CI")[0]
    cents, rate, basis = sc.expected_value({"category": "denial", "signal": "DENIED_AUTH", "carc": "CO-197", "payer_group": grp,
                                            "procedure_code": "99213", "shortfall_cents": 9000}, r)
    assert cents == 3000 and basis.startswith("payer group x code x reason:") and "4 of 12" in basis
    # 11102 has three cases of its own: too few, so the payer group x reason rate (4 of 15) is used, and the basis says so
    cents, rate, basis = sc.expected_value({"category": "denial", "carc": "197", "payer_group": grp, "procedure_code": "11102",
                                            "shortfall_cents": 9000}, r)
    assert basis.startswith("payer group x reason") and "fewer than 10" in basis
    assert sc.expected_value({"category": "refund", "signal": "CREDIT_BALANCE", "shortfall_cents": 500}, r)[0] == 0
    full = sc.expected_value({"category": "underpayment", "signal": "UNDERPAID_VS_OWN_RATE", "carc": "45", "shortfall_cents": 700}, r)
    assert full[0] == 700 and full[1] is None and "full dollars" in full[2]
    assert sc.expected_value({"category": "denial", "carc": "197", "shortfall_cents": 0}, r)[2] == "Nothing at stake on this item"
    other = sc.expected_value({"category": "denial", "carc": "B7", "payer_group": "Elsewhere", "shortfall_cents": 1000}, r)
    assert other[2].startswith("all denials")


def test_apply_expected_values_writes_the_ranking_columns(kind):
    c = kind.conn
    rates_world(c)
    add_item(c, "F-1", payer_group=sc._group_kind("TESTCARE HEALTH", "CI")[0], shortfall_cents=9000)
    kind.reopen()
    out = sc.apply_expected_values(kind.conn)
    assert out["items"] == 1
    ev, rate, basis = kind.conn.execute("SELECT expected_value_cents, recovery_rate, recovery_basis FROM findings").fetchone()
    assert ev == 3000 and rate == pytest.approx(0.333, abs=0.01) and "12" in basis


# ======================================================================================== 3. the weekly self-audit
def test_the_self_audit_lists_gaps_covered_lines_and_possible_wasted_work(kind):
    c = kind.conn
    for i in range(4):                                                   # the payer's usual amount for 99213: $90
        remit(c, "2026-02-05", f"ZZ00T{400 + i:04d}C001", [("99213", 150, 90, [("CO", "45", 60)], "2026-02-01")])
    gap = remit(c, "2026-10-01", "ZZ00T0001C001", [("99213", 150, 0, [("CO", "197", 150)], "2026-09-20")])[0]
    listed = remit(c, "2026-10-01", "ZZ00T0002C001", [("99213", 150, 0, [("CO", "197", 150)], "2026-09-20")])[0]
    add_item(c, "F-listed", line_key=listed, patient_control_number="ZZ00T0002C001", service_date="2026-09-20",
             payment_date="2026-10-01")
    paid = remit(c, "2026-10-01", "ZZ00T0003C001", [("99213", 150, 0, [("CO", "16", 150)], "2026-09-20")])[0]
    remit(c, "2026-10-20", "ZZ00T0003C002", [("99213", 150, 90, [("CO", "45", 60)], "2026-09-20")])
    add_item(c, "F-paid", line_key=paid, patient_control_number="ZZ00T0003C001", service_date="2026-09-20",
             payment_date="2026-10-01")
    kind.reopen()
    c = kind.conn
    a = sc.self_audit(c, today=TODAY, force=True)
    gaps = sc.audit_lists(c, "gap")
    assert [g["key"] for g in gaps] == ["ZZ00T0001|2026-09-20|99213"] and gaps[0]["cents"] == 9000
    assert a["covered_by_category"]["denial_in_window"]["listed"] == 1
    wasted = sc.audit_lists(c, "wasted")
    assert [(w["finding_id"], w["reason"]) for w in wasted] == [("F-paid", "paid_after")]
    m = a["matrix"]
    assert m["needs_work_missed"] == {"n": 1, "cents": 9000} and m["no_work_listed"]["n"] == 1
    assert sc.meta_get(c, sc.K_AUDIT)["latest"]["gaps_n"] == 1
    assert gap  # the 835 line exists
    assert sc.self_audit(c, today=TODAY)["skipped"] == "done this week"


def test_new_gaps_over_the_threshold_raise_the_audit_alert(store):
    c = store.conn
    for i in range(4):
        remit(c, "2026-02-05", f"ZZ00T{400 + i:04d}C001", [("99213", 1500, 900, [("CO", "45", 600)], "2026-02-01")])
    sc.self_audit(c, today="2026-11-08", force=True)
    remit(c, "2026-10-01", "ZZ00T0001C001", [("99213", 1500, 0, [("CO", "197", 1500)], "2026-09-20")])
    a = sc.self_audit(c, today=TODAY)
    assert a["new_gaps_n"] == 1 and a["new_big_gaps"] == 1 and a["alert"]
    assert any(x["kind"] == "AUDIT_GAPS" for x in sc.alert_candidates(c, today=TODAY))


# ============================================================================================== 5. the pattern watch
def test_a_new_denial_pattern_is_raised_unless_an_existing_alert_covers_the_payer(store):
    c = store.conn
    remit(c, "2026-08-01", "ZZ00T0999C001", [("99213", 150, 90)])
    for i in range(6):
        add_item(c, f"F-{i}", signal="DENIED_MODIFIER", procedure_code="11102", billed_code="11102", carc="4",
                 payer_group="Testcare Health", payment_date="2026-07-25")
    got = [a for a in sc.pattern_watch(c, today=TODAY) if a["kind"] == "DENIAL_PATTERN"]
    assert len(got) == 1 and got[0]["n"] == 6 and "began denying 11102" in got[0]["title"]
    covered = {"x": {"kind": "RAMP", "payer_group": "Testcare Health"}}
    assert not [a for a in sc.pattern_watch(c, today=TODAY, active=covered) if a["kind"] == "DENIAL_PATTERN"]
    assert sc.pattern_breakdown(c, "Testcare Health")[0]["code"] == "11102"


def test_a_new_clinician_denied_for_enrollment_is_raised(store):
    c = store.conn
    remit(c, "2025-01-10", "ZZ00T0900C001", [("99213", 150, 90)], npi="1000000001")          # an established clinician
    for i in range(3):
        remit(c, "2026-07-20", f"ZZ00T{500 + i:04d}C001", [("99213", 150, 0, [("CO", "B7", 150)])], npi="1000000002")
    remit(c, "2026-08-01", "ZZ00T0999C001", [("99213", 150, 90)], npi="1000000001")
    got = [a for a in sc.pattern_watch(c, today=TODAY) if a["kind"] == "CREDENTIALING"]
    assert len(got) == 1 and got[0]["n"] == 3 and got[0]["detail"]["npi"] == "1000000002"


def test_a_clinician_paid_below_the_practices_own_rate_is_raised_but_not_the_npp_rate(store):
    c = store.conn
    c.executemany("INSERT INTO provider_tiers (npi, name, tier) VALUES (?, ?, 'physician')",
                  [("1000000001", "Invented Clinician A"), ("1000000002", "Invented Clinician B"), ("1000000003", "Invented C")])
    for i in range(10):
        remit(c, "2026-07-10", f"ZZ00T{600 + i:04d}C001", [("99214", 200, 110, [], "2026-07-01", {"allowed": 110})], npi="1000000001")
    for i in range(5):
        remit(c, "2026-07-12", f"ZZ00T{700 + i:04d}C001", [("99214", 200, 90, [], "2026-07-02", {"allowed": 90})], npi="1000000002")
        remit(c, "2026-07-12", f"ZZ00T{800 + i:04d}C001", [("99214", 200, 93.5, [], "2026-07-02", {"allowed": 93.5})], npi="1000000003")
    got = [a for a in sc.pattern_watch(c, today=TODAY) if a["kind"] == "CLINICIAN_BELOW_GROUP"]
    assert [a["detail"]["npi"] for a in got] == ["1000000002"]                # 93.5 of 110 is the 85% rule: not raised


def test_a_payer_that_reprocessed_a_batch_of_identical_denials_is_raised(store):
    c = store.conn
    remit(c, "2026-08-01", "ZZ00T0999C001", [("99213", 150, 90)])
    for i in range(3):
        add_item(c, f"F-paid{i}", status="resolved", work_state="done", auto_note="Paid", carc="97",
                 evidence={"paid_later": {"payment_date": "2026-07-25", "paid_cents": 9000, "trace": "T"}})
    for i in range(2):
        add_item(c, f"F-open{i}", carc="97")
    got = [a for a in sc.pattern_watch(c, today=TODAY) if a["kind"] == "BATCH_REPROCESSED"]
    assert len(got) == 1 and got[0]["n"] == 2 and sorted(got[0]["detail"]["open"]) == ["F-open0", "F-open1"]


# ================================================================================= 6. housekeeping, 7. panel and health
def test_housekeeping_runs_each_cleanup_and_keeps_what_a_person_may_need(tmp_path, monkeypatch):
    import time as _t
    db = str(tmp_path / "data" / "store.sqlite")
    os.makedirs(os.path.dirname(db))
    s = Store(db)
    ensure_schema(s.conn)
    done = tmp_path / "data" / "incoming" / "done"
    os.makedirs(done)
    old = _t.time() - 40 * 86400
    for name, fid, status_ in (("a__ar.xlsx", "f-replaced", "applied"), ("b__ar.xlsx", "f-current", "applied"),
                               ("c__x.csv", "f-same", "already"), ("d__h.csv", "f-held", "held")):
        (done / name).write_bytes(b"invented")
        (done / (name + ".json")).write_text(json.dumps({"sha256": fid, "result": {"status": status_, "file_id": fid}}))
        for p in (done / name, done / (name + ".json")):
            os.utime(p, (old, old))
    s.conn.executemany("INSERT INTO ema_report_files (file_id, report, status, received_at) VALUES (?, 'ar_report', ?, ?)",
                       [("f-replaced", "replaced", "2026-01-01"), ("f-current", "current", "2026-02-01"),
                        ("f-same", "current", "2026-01-15"), ("f-held", "held", "2026-01-20")])
    s.conn.commit()
    out = sc.housekeeping(mode="server", db_path=db, conn=s.conn)
    assert out["kept_copies"]["removed"] == 1 or out["kept_copies"]["removed"] == 2
    left = sorted(p.name for p in done.iterdir())
    assert "a__ar.xlsx" not in left and "c__x.csv" not in left
    assert {"b__ar.xlsx", "d__h.csv", "a__ar.xlsx.json", "c__x.csv.json"} <= set(left)
    assert "uploads" in out and "work_files" in out and sc.meta_get(s.conn, sc.K_HOUSE)["mode"] == "server"
    s.close()


def test_the_panel_and_data_health_read_only_meta_so_the_copy_shows_the_same(store, tmp_path):
    from remitwatch import cloud
    c = store.conn
    add_item(c, "F-1")
    add_item(c, "F-2", work_state=None)
    run(c, None, "r1")
    sc.plan_next(c, status("2026-11-02T10:00:00+00:00", 1400), mode="serverless")
    p = sc.panel(c)
    assert p["title"] == "Running on its own" and p["regulation"]["level"] == 1
    assert check_of(p["checks"], "state_valid")["status"] == "fail" and p["checks"][0]["history"]
    assert any("Working lighter" in x for x in p["lines"])
    path = str(tmp_path / "serving.sqlite")
    cloud.build_serving(c, path)
    copy = sqlite3.connect(path)
    q = sc.panel(copy)
    assert q["checks"] == p["checks"] and q["regulation"]["level"] == 1
    assert [d["key"] for d in sc.data_health(copy, TODAY)] == [d["key"] for d in sc.data_health(c, TODAY)]
    copy.close()
    for line in p["lines"]:
        assert "—" not in line                                           # plain English, no em dashes


def test_scoreboard_figures_for_the_results_tab(store):
    c = store.conn
    add_item(c, "F-soon", appeal_deadline="2026-12-01", shortfall_cents=4000)
    add_item(c, "F-later", appeal_deadline="2027-03-01", shortfall_cents=5000)
    s = sc.scoreboard(c, today=TODAY)
    assert s["at_risk_14"] == {"n": 1, "cents": 4000}
    assert set(s) >= {"given_up", "lost_to_deadlines", "audit", "track"}


# ================================================================================================ more edges
def test_vanished_items_leave_by_a_fold_or_a_superseded_remittance_without_a_gap(store):
    c = store.conn
    lk = remit(c, "2026-10-20", "ZZ00T0005C001", [("99213", 150, 0, [("CO", "197", 150)])])[0]
    tk = c.execute("SELECT transaction_key FROM lines WHERE line_key=?", (lk,)).fetchone()[0]
    add_item(c, "F-folded", line_key="Lx")
    add_item(c, "F-super", line_key=lk, transaction_key=tk, patient_control_number="ZZ00T0005C001")
    add_item(c, "F-carrier", line_key="Ly")
    run(c, None, "r1")

    def save(cn):
        cn.execute("DELETE FROM findings WHERE finding_id IN ('F-folded', 'F-super')")
        cn.execute("UPDATE findings SET evidence=? WHERE finding_id='F-carrier'",
                   (json.dumps({"folded": [{"finding_id": "F-folded"}]}),))
        cn.execute("UPDATE transactions SET superseded=1 WHERE transaction_key=?", (tk,))
    out = run(c, save, "r2")
    van = check_of(sc.meta_get(c, sc.K_LAST)["checks"], "vanished")
    assert van["status"] == "ok" and out["kept"] == 0 and van["info"]["routes"] == {"folded": 1, "superseded": 1}


def test_stale_ema_reports_make_the_app_say_what_it_cannot_see(store):
    c = store.conn
    remit(c, "2026-11-18", "ZZ00T0001C001", [("99213", 150, 80)])
    c.execute("""INSERT INTO ema_report_files (file_id, report, status, as_of, received_at, svc_lo, svc_hi, scope)
                 VALUES ('p1', 'production', 'current', '2026-10-01', '2026-10-02', '2025-01-01', '2026-09-30', 'service'),
                        ('a1', 'ar_report', 'current', '2026-10-01', '2026-10-02', '2026-01-01', '2026-09-30', 'service')""")
    c.commit()
    add_item(c, "F-1", shortfall_cents=7000)
    b = sc.blindness(c, today=TODAY)
    srcs = {s["source"]: s for s in b["sources"]}
    assert set(srcs) >= {"production"} and srcs["production"]["cents"] == 7000 and "cannot see" in srcs["production"]["text"]
    assert sc.check_feed_ema(c, TODAY)["status"] == "fail"


def test_serverless_housekeeping_keeps_the_newest_and_held_copies(tmp_path):
    from remitwatch.state import LocalState
    import time as _t
    state = LocalState(str(tmp_path / "state"))
    s = _new("compact", tmp_path)
    for name, fid, st in (("u1__p.csv", "f-old", "applied"), ("u2__p.csv", "f-new", "applied"), ("u3__h.csv", "f-held", "held")):
        state.put(f"intake/done/{name}", b"invented")
        state.put(f"intake/done/{name}.json", json.dumps({"sha256": fid, "result": {"status": st, "file_id": fid}}).encode())
    s.conn.executemany("INSERT INTO ema_report_files (file_id, report, status, received_at) VALUES (?, 'production', ?, ?)",
                       [("f-old", "superseded", "2026-01-01"), ("f-new", "current", "2026-02-01"), ("f-held", "held", "2026-02-02")])
    s.conn.commit()
    out = sc.housekeeping(mode="serverless", state=state, conn=s.conn, now=_t.time() + 40 * 86400)
    names = {e["name"].rsplit("/", 1)[-1] for e in state.list("intake/done/")}
    assert out["kept_copies"]["removed"] == 1 and "u1__p.csv" not in names
    assert {"u1__p.csv.json", "u2__p.csv", "u3__h.csv"} <= names
    s.close()


def test_a_pattern_rule_that_fails_is_reported_as_a_pattern_error(store, monkeypatch):
    def boom(*a, **k):
        raise sqlite3.OperationalError("invented failure")
    monkeypatch.setattr(sc, "_pw_batch", boom)
    got = sc.pattern_watch(store.conn, today=TODAY)
    assert any(a["kind"] == "PATTERN_ERROR" for a in got)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_randomized_postings_always_add_up_to_their_charges(tmp_path, seed):
    """Invented Production postings at random (charges, insurance and patient payments, contractual and denial
    write-offs, patient adjustments, refunds as negative payments): every cell adds up, and the totals equal the sums of
    what was generated."""
    import random
    rnd = random.Random(seed)
    P, D = F.PAYERS, F.D
    rows, tot = [], {"charges": 0, "ins": 0, "pat": 0, "adj": 0}
    for n in range(1, 25):
        svc = D(2026, rnd.randint(1, 7), rnd.randint(1, 28))
        payer = rnd.choice([P["bcbs"], P["medicare"], P["commercial"]])
        charge = rnd.randint(50, 400)
        rows.append(F.production_row(posted=svc + F.dt.timedelta(days=2), svc=svc, link=F.link_no(n), charges=float(charge),
                                     payer=payer, cpt=rnd.choice(["99213", "99214", "11102"])))
        tot["charges"] += charge * 100
        cpt = rows[-1]["CPT/Product"]
        for _k in range(rnd.randint(0, 3)):
            ins = rnd.choice([0, rnd.randint(1, charge), -rnd.randint(1, 30)])
            contr = rnd.randint(0, 50)
            nonc = rnd.choice([0, 0, rnd.randint(1, 80)])
            reason = rnd.choice(["CO-45", "CO-197", "CO-97", "PR-1", "CO-29", "OA-23"])
            rows.append(F.production_row(posted=svc + F.dt.timedelta(days=rnd.randint(10, 60)), svc=svc, link=F.link_no(n),
                                         ins_paid=float(ins), contractual=float(contr), noncontractual=float(nonc), reason=reason,
                                         check=str(880000000 + n), payer=payer, cpt=cpt))
            tot["ins"] += ins * 100
            tot["adj"] += (contr + nonc) * 100
        if rnd.random() < 0.5:
            pat = rnd.randint(1, 40)
            rows.append(F.production_row(posted=svc + F.dt.timedelta(days=70), svc=svc, link=F.link_no(n), pat_paid=float(pat),
                                         payer=payer, cpt=cpt))
            tot["pat"] += pat * 100
    s = _new("file", tmp_path)
    lg = el.Ledger.open(str(tmp_path / "ema_ledger.sqlite"))
    try:
        res = er.ingest_file(s, F.write(str(tmp_path / "p.csv"), F.production_csv(rows)), via="test", ledger=lg, log=lambda *a: None)
        assert res["status"] == "current", res
        el.refresh(s, lg, log=lambda *a: None)
        out = sc.check_ledger(s.conn, lg, today=TODAY)
        assert check_of(out["checks"], "money_conservation")["status"] == "ok"
        assert check_of(out["checks"], "ledger_store")["status"] == "ok"
        t = out["conservation"]["totals_since"]
        assert t["charges"] == tot["charges"] and t["ins_paid"] == tot["ins"] and t.get("pat_paid", 0) == tot["pat"]
        assert t.get("contractual", 0) + t.get("wo_at_posting", 0) + t.get("wo_later", 0) + t.get("wo_timely", 0) == tot["adj"]
    finally:
        lg.close()
        s.close()


def test_the_banner_and_the_data_tab_requests(store):
    c = store.conn
    assert sc.banner(c) is None
    remit(c, "2026-10-09", "ZZ00T0001C001", [("99213", 150, 80)])
    sc.blindness(c, today=TODAY)
    b = sc.banner(c)
    assert b["level"] == "blind" and "No remittance since 2026-10-09" in b["text"]
    add_item(c, "F-1")
    out = sc.apply_request(c, {"kind": "selfcheck_request", "what": "audit"}, today=TODAY)
    assert out["audit"]["why"] == "on demand"
    assert sc.apply_request(c, {"what": "nonsense"})["skipped"]
    add_item(c, "F-2")
    run(c, None, "r1")
    run(c, bad_closure, "r2")
    assert sc.banner(c)["level"] == "safe_mode"
    acc = sc.apply_request(c, {"what": "accept", "checks": ["closure_proof"], "user": "owner@example.test"})
    assert acc["accepted"] == {"closure_proof": 1}


def test_a_server_cycle_summary_is_measured_by_its_start_and_end(store, monkeypatch):
    c = store.conn
    monkeypatch.setenv("REMITWATCH_CYCLE_BUDGET_SECONDS", "1000")
    prev = {"started": "2026-11-02T10:00:00+00:00", "finished": "2026-11-02T10:15:00+00:00"}       # 900 s of 1,000
    p = sc.plan_next(c, prev, mode="server")
    assert p["level"] == 1 and p["pressure"]["worst"] == "time" and p["pressure"]["seconds"] == 900
