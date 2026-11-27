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
    assert (b["memory_mb"], b["seconds"], b["tmp_mb"]) == (2188, 240.0, 512)
    assert round(b["memory_mb"] * b["share"]) == sc.GATE_MEMORY_MB          # acting at release gate 3's 1.75 GB
    s = sc.budgets("server")
    assert s["memory_mb"] is None and s["tmp_mb"] is None and s["seconds"] == 3600.0
    monkeypatch.setenv("REMITWATCH_MEMORY_BUDGET_MB", "3000")
    assert sc.budgets("server")["memory_mb"] == 3000


def test_a_run_past_80_percent_makes_the_next_run_spread_then_archive_then_shed_then_alert(store):
    c = store.conn
    plan = sc.plan_next(c, status("2026-11-01T10:00:00+00:00", 900), mode="serverless")
    assert plan["level"] == 0 and not plan["spread"]
    plan = sc.plan_next(c, status("2026-11-02T10:00:00+00:00", 1800), mode="serverless")      # past release gate 3 (1,750 MB)
    assert plan["level"] == 1 and plan["spread"] and not plan["archive_tight"] and plan["pressure"]["worst"] == "memory"
    assert any("one report file per run" in a for a in plan["actions"])
    plan = sc.plan_next(c, status("2026-11-03T10:00:00+00:00", 1780), mode="serverless")
    assert plan["level"] == 2 and plan["archive_tight"] and plan["skip"] == []
    assert sc.archive_budget_state(plan)["share"] >= 0.8         # unit E's archive takes the tighter horizon
    plan = sc.plan_next(c, status("2026-11-04T10:00:00+00:00", 1760), mode="serverless")
    assert plan["level"] == 3 and set(plan["skip"]) == set(sc.OPTIONAL_STEPS) and not plan["alert"]
    plan = sc.plan_next(c, status("2026-11-05T10:00:00+00:00", 1900), mode="serverless")
    assert plan["level"] == 4 and plan["alert"]
    kinds = {a["kind"]: a for a in sc.alert_candidates(c, today=TODAY)}
    assert "NEAR_LIMIT" in kinds and "Nothing was lost" in kinds["NEAR_LIMIT"]["title"]
    assert c.execute("SELECT COUNT(*) FROM selfcheck_cycles").fetchone()[0] == 5
    assert sc.regulation(c)["changes"][-1]["to"] == "alert"


def test_calm_runs_step_the_level_down_after_three_and_a_status_counts_once(store):
    c = store.conn
    sc.plan_next(c, status("2026-11-02T10:00:00+00:00", 1800), mode="serverless")
    again = sc.plan_next(c, status("2026-11-02T10:00:00+00:00", 1800), mode="serverless")      # the same run read twice
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


def test_mark_measures_remitwatchs_own_temp_folder(tmp_path, monkeypatch):
    """The /tmp figure is RemitWatch's temporary folder (REMITWATCH_TMP, as runlog measures it; on Vercel /tmp is the
    function's own), never the whole of a shared machine's /tmp: 18 GB of other programs' files there made every run on
    a test machine read as pressured (found wiring the cycle)."""
    import tempfile
    ours, theirs = tmp_path / "ours", tmp_path / "theirs"
    (ours / "remitwatch").mkdir(parents=True)
    theirs.mkdir()
    (ours / "remitwatch" / "a.bin").write_bytes(b"x" * (2 << 20))
    (theirs / "b.bin").write_bytes(b"x" * (5 << 20))
    monkeypatch.setenv("REMITWATCH_TMP", str(ours))
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(theirs))
    out = {"mode": "serverless"}
    sc.mark(out, "ingest")
    assert out["tmp_mb"]["ingest"] == 2


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


def test_an_unworked_item_past_every_window_and_its_grace_is_caught(store, monkeypatch):
    from remitwatch import states
    c = store.conn
    add_item(c, "F-late", appeal_deadline="2026-09-01")                                     # 82 days past, nobody worked it
    add_item(c, "F-grace", appeal_deadline="2026-11-10")                                    # 12 days past
    add_item(c, "F-legal", appeal_deadline="2026-09-01", legal_deadline="2027-03-01")       # the payer's own limit is later
    add_item(c, "F-work", appeal_deadline="2026-09-01", state_basis="Anthem received a new claim; no answer by Oct 1: ask")
    add_item(c, "F-rule", appeal_deadline="2026-09-01", state_basis="whatever the words", state_rule="open") \
        if "state_rule" in sc._cols(c, "findings") else None
    # the owner's decision 1: expired the day the last window closes (states.EXPIRE_GRACE_DAYS = 0)
    monkeypatch.setattr(states, "EXPIRE_GRACE_DAYS", 0)
    r = sc.check_past_window(c, TODAY)
    assert {"F-late", "F-grace"} <= set(r["examples"]) and not {"F-legal", "F-work"} & set(r["examples"])
    assert r["info"]["grace_days"] == 0 and r["info"]["past_last_window_in_grace"] == 0
    monkeypatch.setattr(states, "EXPIRE_GRACE_DAYS", 30)                                    # a grace, where one is set
    r = sc.check_past_window(c, TODAY)
    assert "F-late" in r["examples"] and "F-grace" not in r["examples"] and r["info"]["past_last_window_in_grace"] == 1


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
    kind.reopen()
    c = kind.conn

    def save_again(cn):                                                      # the rule is still broken the next day
        cn.execute("DELETE FROM findings WHERE finding_id = 'F-gone'")
    assert run(c, save_again, "r3")["kept"] == 1                             # kept again, not let go on the second run
    sc.accept_findings(c, "owner@example.test", ["vanished"])               # a person says the change is intended
    out = run(c, save_again, "r4")
    assert out["kept"] == 0 and c.execute("SELECT COUNT(*) FROM findings WHERE finding_id='F-gone'").fetchone()[0] == 0


def test_the_real_detector_with_a_dropped_rule_keeps_its_items(kind, monkeypatch):
    """The owner's fault 1 (a code bug that drops a category of items) on the real Detector: the items stay listed."""
    c = kind.conn
    for i in range(3):
        remit(c, "2026-10-20", f"ZZ00T000{i}C001", [("99213", 150, 0, [("CO", "197", 150)], "2026-10-01")])
        remit(c, "2026-10-21", f"ZZ00T000{i}C002", [("99214", 200, 120, [("CO", "45", 80)], "2026-10-02")])
    det = Detector(kind.store, "d1", today=TODAY)
    det.run()                                                                # the self-check runs inside (needs_G section 1)
    first = det.selfcheck
    n0 = c.execute("SELECT COUNT(*) FROM findings WHERE work_state IN ('work_now','waiting')").fetchone()[0]
    assert n0 > 0 and first["first"]
    kind.reopen()
    c = kind.conn
    monkeypatch.setattr(Detector, "run_line_rules", lambda self: None)       # the bug: line rules dropped
    det = Detector(kind.store, "d2", today=TODAY)
    det.run()
    out = det.selfcheck
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
    assert lb["severity"] == "warning" and lb["n"] == 1 and lb["cents"] == 15000 and out["ar_balance"]["ar_above"] == 1
    # the patient's $20, paid with no claim number, is joined to the line first: the line agrees with the A/R
    assert out["ar_balance"]["equal"] == 1 and out["ar_balance"]["postings_above"] == 0


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
    stored = sc.meta_get(c, sc.K_GIVEN_UP)                     # the fields unit R's scoreboard reads (needs_R section 6)
    assert (stored["cents"], stored["n"], stored["recoverable_cents"]) == (10000, 2, 5000) and stored["at"]
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
    stored = sc.meta_get(c, sc.K_BLIND)                        # the fields the pages' banner reads (needs_C2 section 4)
    assert stored["blind"] and stored["cents"] == b["sources"][0]["cents"] and stored["lines"] == [b["sources"][0]["text"]]
    assert stored["feeds"] == [{"feed": "835", "label": "Remittances (835)", "since": "2026-10-09", "days": 44, "stale": True}]
    assert stored["basis"] == b["banner"] and stored["since"] == "2026-10-09"
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
    au = sc.meta_get(c, sc.K_AUDIT)
    assert au["latest"]["gaps_n"] == 1
    # what unit R freezes in its "before" (needs_R section 6): coverage, precision, the matrix and when
    assert (au["coverage"]["listed"], au["coverage"]["missed"], au["coverage"]["share"]) == (1, 1, 0.5)
    assert au["precision"]["possible_waste"] == 1 and au["precision"]["work_now"] >= 1 and au["matrix"] == m and au["at"]
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
    try:                              # the archive's compaction (VACUUM) is unit E's step before detection, never here
        import importlib
        monkeypatch.setattr(importlib.import_module("remitwatch.archive"), "compact",
                            lambda *a, **k: pytest.fail("housekeeping compacted the store"), raising=False)
    except ImportError:
        pass
    out = sc.housekeeping(mode="server", db_path=db, conn=s.conn)
    assert out["kept_copies"]["removed"] == 1 or out["kept_copies"]["removed"] == 2
    left = sorted(p.name for p in done.iterdir())
    assert "a__ar.xlsx" not in left and "c__x.csv" not in left
    assert {"b__ar.xlsx", "d__h.csv", "a__ar.xlsx.json", "c__x.csv.json"} <= set(left)
    assert "uploads" in out and "work_files" in out and sc.meta_get(s.conn, sc.K_HOUSE)["mode"] == "server"
    assert "archive" not in out
    out = sc.housekeeping(mode="server", db_path=db, conn=s.conn, archive_result={"skipped": True, "free_share": 0.01})
    assert sc.meta_get(s.conn, sc.K_HOUSE)["archive"] == {"skipped": True, "free_share": 0.01}   # E's result, recorded
    s.close()


def test_the_panel_and_data_health_read_only_meta_so_the_copy_shows_the_same(store, tmp_path):
    from remitwatch import cloud
    c = store.conn
    add_item(c, "F-1")
    add_item(c, "F-2", work_state=None)
    run(c, None, "r1")
    sc.plan_next(c, status("2026-11-02T10:00:00+00:00", 1800), mode="serverless")
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
    c.execute("INSERT INTO ema_ar (file_id, row_no, as_of, svc_year, balance_cents) VALUES ('a1', 1, '2026-10-01', '2026', 5000)")
    c.commit()
    add_item(c, "F-1", shortfall_cents=7000)
    b = sc.blindness(c, today=TODAY)
    srcs = {s["source"]: s for s in b["sources"]}
    assert set(srcs) == {"production", "ar"} and srcs["production"]["cents"] == 7000 and "cannot see" in srcs["production"]["text"]
    feeds = {f["feed"]: f for f in b["feeds"]}
    assert feeds["835"]["stale"] is False and feeds["production"]["stale"] and feeds["production"]["since"] == "2026-10-01"
    assert b["cents"] == 7000                                  # the items to work counted once, however many reports are old
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
    r1, r2 = "run-20261102T101700Z-aa01", "run-20261102T111700Z-aa02"
    run(c, None, r1)
    run(c, bad_closure, r2)
    assert sc.banner(c)["level"] == "safe_mode" and sc.banner(c)["safe_mode"].startswith("Safe mode")
    # an acceptance names the run the page showed (review hook-r2-11): without one, nothing is accepted
    for rid in (None, "", "r2"):
        refused = sc.apply_request(c, {"what": "accept", "checks": ["closure_proof"], "user": "owner@example.test", "run_id": rid})
        assert refused["accepted"] == {} and refused.get("refused") and "which run" in refused["text"], refused
    acc = sc.apply_request(c, {"what": "accept", "checks": ["closure_proof"], "user": "owner@example.test", "run_id": r2})
    assert acc["accepted"] == {"closure_proof": 1} and acc["text"].startswith("Accepted: 1 item of"), acc


def test_a_server_cycle_summary_is_measured_by_its_start_and_end(store, monkeypatch):
    c = store.conn
    monkeypatch.setenv("REMITWATCH_CYCLE_BUDGET_SECONDS", "1000")
    prev = {"started": "2026-11-02T10:00:00+00:00", "finished": "2026-11-02T10:15:00+00:00"}       # 900 s of 1,000
    p = sc.plan_next(c, prev, mode="server")
    assert p["level"] == 1 and p["pressure"]["worst"] == "time" and p["pressure"]["seconds"] == 900


# ================================================================== the independent review's findings (each failed first)
from remitwatch import actions  # noqa: E402

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _three_denials(c):
    for i in range(3):
        remit(c, "2026-10-20", f"ZZ00T000{i}C001", [("99213", 150, 0, [("CO", "197", 150)], "2026-10-01")])


def _detect(store_obj, rid, today=TODAY):
    """One detection run as needs_G section 1 wires it: Detector.run alone (begin_detection and after_detection inside)."""
    det = Detector(store_obj, rid, today=today)
    det.run()
    return det.selfcheck


def _listed(c):
    return {r[0] for r in c.execute("SELECT finding_id FROM findings WHERE COALESCE(stale,0)=0 "
                                    "AND work_state IN ('work_now','waiting')")}


def test_a_dropped_rule_keeps_the_items_people_worked_on_the_list(kind, monkeypatch):
    """detect.save keeps an item a person worked (Sent, Started, a note) as a stale row when no rule produces it any more;
    stale rows are on no list. The vanished check counts them and keeps them listed, with the person's status and notes,
    until a person accepts the change (review finding 1)."""
    c = kind.conn
    _three_denials(c)
    _detect(kind.store, "d1")
    listed = sorted(_listed(c))
    assert len(listed) == 3
    sent, started, untouched = listed
    actions.apply_status(c, sent, "appealed", notes="Appeal faxed", actor="biller@example.test")
    actions.apply_status(c, started, "in_progress", notes="Called the payer", actor="biller@example.test")
    c.commit()
    kind.reopen()
    c = kind.conn
    monkeypatch.setattr(Detector, "run_line_rules", lambda self: None)       # the bug: the line rules stopped
    out = _detect(kind.store, "d2")
    van = check_of(sc.meta_get(c, sc.K_LAST)["checks"], "vanished")
    assert set(van["examples"]) == {sent, started, untouched} and out["kept"] == 3, (van["examples"], out["kept"])
    assert {sent, started, untouched} <= _listed(c)
    rows = {r[0]: r[1:] for r in c.execute("SELECT finding_id, status, notes, hint FROM findings")}
    assert rows[sent][:2] == ("appealed", "Appeal faxed") and rows[started][:2] == ("in_progress", "Called the payer")
    assert all(sc.KEPT_HINT in (rows[f][2] or "") for f in (sent, started, untouched))
    kind.reopen()
    c = kind.conn
    assert _detect(kind.store, "d3")["kept"] == 3 and {sent, started, untouched} <= _listed(c)    # still broken: kept again
    sc.accept_findings(c, "owner@example.test", ["vanished"])               # the owner says the change is intended
    kind.reopen()
    c = kind.conn
    out = _detect(kind.store, "d4")
    assert out["kept"] == 0 and not ({sent, started, untouched} & _listed(c))


def test_items_the_owner_took_off_by_accepting_a_payer_pattern_are_not_kept(store):
    """The owner's write-off policy is a person's decision: denials of a code the payer never pays leave the work when the
    owner accepts the pattern (ema_checks.set_policy, detect.fold_payer_policy). Found by wiring the check into detection:
    it kept them, overriding the owner. Another payer's item gone with no reason is still kept."""
    from remitwatch import ema_checks as ec
    c = store.conn
    add_item(c, "F-policy-1", line_key="LP1", procedure_code="G6001", payer_group="UnitedHealthcare")
    add_item(c, "F-policy-2", line_key="LP2", procedure_code="G6001", payer_group="UnitedHealthcare")
    add_item(c, "F-other", line_key="LP3", procedure_code="G6001", payer_group="Testcare Health")
    run(c, None, "r1")
    ec.set_policy(c, "UnitedHealthcare|G6001||payer_policy", "accept", "owner@example.test")
    out = run(c, lambda cn: cn.execute("DELETE FROM findings WHERE finding_id LIKE 'F-%'"), "r2")
    van = check_of(sc.meta_get(c, sc.K_LAST)["checks"], "vanished")
    assert out["kept"] == 1 and set(van["examples"]) == {"F-other"}, van
    assert van["info"]["routes"].get("owner_policy") == 2


def test_a_claim_sent_again_takes_its_earlier_chase_off_the_list_without_a_keep(store):
    """A rejected claim fixed and sent again (submission C024 of the same bill) leaves its earlier chase waiting, stale,
    until the new one comes due (detect.save). That is a route, not a vanishing. An item whose bill has no newer
    submission is still kept."""
    c = store.conn
    c.execute("INSERT INTO ema_rows (claim_id, family, claim_submission_date) VALUES ('ZZ00T0001C024', 'claim_status', '2026-10-21')")
    c.execute("INSERT INTO ema_rows (claim_id, family) VALUES ('ZZ00T0002C024', 'claim_status')")      # never went out
    c.commit()
    add_item(c, "F-sent", signal="REJECTED_PRE_PAYER", category="denial", line_key=None, claim_key=None,
             patient_control_number="ZZ00T0001C017")
    add_item(c, "F-lone", signal="REJECTED_PRE_PAYER", category="denial", line_key=None, claim_key=None,
             patient_control_number="ZZ00T0002C017")
    run(c, None, "r1")
    out = run(c, lambda cn: cn.execute("UPDATE findings SET stale=1 WHERE finding_id IN ('F-sent','F-lone')"), "r2")
    van = check_of(sc.meta_get(c, sc.K_LAST)["checks"], "vanished")
    assert out["kept"] == 1 and set(van["examples"]) == {"F-lone"}, van
    assert van["info"]["routes"].get("sent_again") == 1


def test_an_old_decision_on_the_same_line_does_not_excuse_a_vanished_item(store):
    """Only an item this run produced for the line carries it on, never one a person let go long ago (finding 1b)."""
    c = store.conn
    add_item(c, "F-old", line_key="L1", signal="UNDERPAID_VS_OWN_RATE", category="underpayment", status="dismissed",
             work_state="accepted")
    add_item(c, "F-denial", line_key="L1")
    run(c, None, "r1")
    out = run(c, lambda cn: cn.execute("DELETE FROM findings WHERE finding_id='F-denial'"), "r2")
    van = check_of(sc.meta_get(c, sc.K_LAST)["checks"], "vanished")
    assert "F-denial" in _listed(c) and out["kept"] == 1, {"kept": out["kept"], "routes": van["info"].get("routes")}


def test_safe_mode_keeps_a_change_a_person_made_while_detection_ran(tmp_path, monkeypatch):
    """Server: the dashboard writes on its own connection while the cycle runs. A held run puts the items back as they
    were before it, and then every status and note a person saved meanwhile (finding 2)."""
    s = _new("file", tmp_path)
    c = s.conn
    _three_denials(c)
    _detect(s, "d1")                                                          # the first run: the baseline
    ids = sorted(r[0] for r in c.execute("SELECT finding_id FROM findings WHERE work_state='work_now'"))
    person_fid, bug_fid = ids[0], ids[1]
    real_rules = Detector.run_aggregate_rules

    def rules_while_a_person_works(self):
        real_rules(self)
        other = sqlite3.connect(str(tmp_path / "s.sqlite"), timeout=30)      # the dashboard's own connection
        actions.apply_status(other, person_fid, "appealed", notes="Appeal faxed today", actor="biller@example.test")
        other.commit()
        other.close()
    monkeypatch.setattr(Detector, "run_aggregate_rules", rules_while_a_person_works)
    real_hide = Detector.hook_hide

    def hide_then_a_bug(self):
        real_hide(self)
        self.conn.execute("UPDATE findings SET status='resolved', work_state='done', auto_note='Paid', evidence='{}' "
                          "WHERE finding_id=?", (bug_fid,))                   # a release's bug: a closure with no proof
        self.conn.commit()
    monkeypatch.setattr(Detector, "hook_hide", hide_then_a_bug)
    out = _detect(s, "d2")
    assert out["held"] and out["hold_checks"] == ["closure_proof"]
    ev = c.execute("SELECT to_status FROM finding_events WHERE finding_id=? AND kind='manual'", (person_fid,)).fetchone()
    assert ev is not None and ev[0] == "appealed"
    row = tuple(c.execute("SELECT status, notes FROM findings WHERE finding_id=?", (person_fid,)).fetchone())
    bug = c.execute("SELECT status FROM findings WHERE finding_id=?", (bug_fid,)).fetchone()[0]
    text = sc.safe_mode(c)["text"]
    s.close()
    assert row == ("appealed", "Appeal faxed today") and bug == "open"
    assert "The 1 change a person saved during the run stands." in text, text           # plain English, one change


def test_accepting_takes_exactly_the_offenders_a_person_was_shown(store):
    """accept_findings records the offenders of the run the person looked at; a new one in a later run is still new: it
    is kept (vanished) or holds the run (a closure without proof) (finding 3)."""
    c = store.conn
    for f in ("F-a", "F-b", "F-c"):
        add_item(c, f, line_key="L" + f)
    run(c, None, "r1")
    out = run(c, lambda cn: cn.execute("DELETE FROM findings WHERE finding_id='F-a'"), "r2")
    assert out["kept"] == 1 and check_of(sc.meta_get(c, sc.K_LAST)["checks"], "vanished")["examples"] == ["F-a"]
    acc = sc.accept_findings(c, "owner@example.test", ["vanished"])          # the owner accepts the change shown: F-a
    assert acc["accepted"] == {"vanished": 1} and acc["run_id"] == "r2"
    out = run(c, lambda cn: cn.execute("DELETE FROM findings WHERE finding_id IN ('F-a','F-b')"), "r3")   # a 2nd rule broke
    left = {r[0] for r in c.execute("SELECT finding_id FROM findings")}
    assert "F-b" in left and "F-a" not in left and out["kept"] == 1, {"kept": out["kept"], "left": sorted(left)}
    # an acceptance sent for a run that is no longer the latest accepts nothing
    stale = sc.accept_findings(c, "owner@example.test", ["vanished"], run_id="r2")
    assert stale["accepted"] == {} and stale.get("stale")


def test_an_acceptance_does_not_wave_through_a_new_closure_without_proof(store):
    c = store.conn
    for f in ("F-1", "F-2", "F-3"):
        add_item(c, f)
    run(c, None, "r1")
    assert run(c, bad_closure, "r2")["held"]
    sc.accept_findings(c, "owner@example.test", ["closure_proof"])           # accepts F-2's closure, the one shown

    def worse(cn):
        bad_closure(cn)
        cn.execute("UPDATE findings SET status='resolved', work_state='done', auto_note='Paid', evidence='{}' "
                   "WHERE finding_id='F-3'")
    out = run(c, worse, "r3")
    assert out["held"] and out["new"]["closure_proof"] == 1, out["new"]


def test_vercels_normal_runs_are_calm(store):
    """The live Vercel baseline (peak 1,530 MB, 98.6 s of 240 s) and a detection-only run (1,365 MB) pass release gate 3
    (under 1.75 GB): they are calm, so nothing is shed and no alert is raised (finding 4)."""
    c = store.conn
    days = [1530, 1365, 1530, 1365, 1530, 1365, 1530]
    plans = [sc.plan_next(c, status(f"2026-11-{10 + i:02d}T10:17:00+00:00", peak, seconds=98.6, budget=240), mode="serverless")
             for i, peak in enumerate(days)]
    kinds = {a["kind"] for a in sc.alert_candidates(c, today=TODAY)}
    assert [p["level"] for p in plans] == [0] * 7 and plans[-1]["skip"] == [] and "NEAR_LIMIT" not in kinds
    assert sc.plan_next(c, status("2026-11-17T10:17:00+00:00", 1760), mode="serverless")["level"] == 1   # past the gate


def test_the_weekly_steps_are_never_left_for_more_than_a_week(store):
    """Under pressure day after day the optional steps are left for a calm run, but never longer than MAX_DEFER_DAYS: then
    they run again (in a run that does no detection), and only once they ran can they be left again (finding 4)."""
    c = store.conn

    def day(d, peak=1900):
        return sc.plan_next(c, status(f"2026-11-{d:02d}T10:17:00+00:00", peak), mode="serverless",
                            now=f"2026-11-{d:02d}T10:20:00+00:00")
    plans = {d: day(d) for d in range(1, 10)}
    assert plans[3]["level"] == 3 and "audit" in plans[3]["skip"] and "report" in plans[3]["skip"]
    assert "audit" in plans[9]["skip"]                                        # six days after it was first left
    p = day(10)
    assert "audit" not in p["skip"] and "report" not in p["skip"] and set(p["overdue"]) == set(sc.OPTIONAL_STEPS)
    assert any("a week" in a for a in p["actions"])
    assert "audit" not in day(11)["skip"]                                     # still owed: it has not run yet
    sc.record_did(c, {"ran": list(sc.OPTIONAL_STEPS)}, today="2026-11-11")
    assert "audit" in day(12)["skip"]                                         # it ran: it may be left again, for a week


def test_a_run_the_platform_killed_counts_as_pressure(tmp_path):
    """A run killed for memory writes no status (cloud.py and pipeline.py write it in a finally block). Its open marker
    (remitwatch.runlog) is found when the next run's log starts; the plan counts it once as a pressured run (finding 4b)."""
    import subprocess
    import textwrap
    from remitwatch import runlog
    logs = tmp_path / "logs"
    code = textwrap.dedent(f'''
        import os, sys
        sys.path.insert(0, {REPO_DIR!r})
        from remitwatch import runlog
        rl = runlog.start_run("cycle", trigger="cron", logs_dir={str(logs)!r}, host="server")
        rl.checkpoint("detect", force=True)
        os._exit(137)                       # the platform's kill: no finally block, no status, no finish
    ''')
    res = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=180)
    assert res.returncode == 137, res.stderr[-600:]
    s = _new("file", tmp_path)
    c = s.conn
    calm = status("2026-11-20T10:17:00+00:00", 900, seconds=80)
    assert sc.plan_next(c, calm, mode="server")["level"] == 0                 # the last run that finished: calm
    rl = runlog.start_run("cycle", trigger="cron", logs_dir=str(logs), host="server")
    try:
        p = sc.plan_next(c, calm, mode="server")                              # status.json still holds the calm run
        assert p["level"] == 1 and p["pressure"]["killed"], p["pressure"]
        assert "detect" in p["pressure"]["text"]
        assert sc.plan_next(c, calm, mode="server")["level"] == 1             # counted once
    finally:
        rl.finish()
        s.close()


def test_the_audit_counts_lines_remitwatch_closed_or_expired_on_its_own_as_missed(kind):
    """Coverage: a line the audit's own rules find unpaid and inside its window, which RemitWatch closed by itself or
    marked expired, is missed (with the item that holds it), not 'held by a reason' (finding 5)."""
    c = kind.conn
    for i in range(4):                                                        # the payer's usual amount for 99213: $90
        remit(c, "2026-02-05", f"ZZ00T{400 + i:04d}C001", [("99213", 150, 90, [("CO", "45", 60)], "2026-02-01")])
    lk1 = remit(c, "2026-10-01", "ZZ00T0001C001", [("99213", 150, 0, [("CO", "197", 150)], "2026-09-20")])[0]
    lk2 = remit(c, "2026-10-01", "ZZ00T0002C001", [("99213", 150, 0, [("CO", "197", 150)], "2026-09-20")])[0]
    lk3 = remit(c, "2026-10-01", "ZZ00T0003C001", [("99213", 150, 0, [("CO", "197", 150)], "2026-09-20")])[0]
    add_item(c, "F-closed", line_key=lk1, patient_control_number="ZZ00T0001C001", service_date="2026-09-20",
             payment_date="2026-10-01", status="resolved", work_state="done", auto_note="Paid on a later remittance",
             evidence={"paid_later": {"payment_date": "2026-10-20", "paid_cents": 9000, "trace": "T9"}})
    add_item(c, "F-expired", line_key=lk2, patient_control_number="ZZ00T0002C001", service_date="2026-09-20",
             payment_date="2026-10-01", work_state="expired", appeal_deadline="2026-10-15")
    add_item(c, "F-let-go", line_key=lk3, patient_control_number="ZZ00T0003C001", service_date="2026-09-20",
             payment_date="2026-10-01", status="dismissed", work_state="accepted")
    kind.reopen()
    a = sc.self_audit(kind.conn, today=TODAY, force=True)
    assert a["coverage"]["missed"] == 2 and a["coverage"]["held_by_reason"] == 1, a["coverage"]
    gaps = {g["key"]: g for g in sc.audit_lists(kind.conn, "gap")}
    assert gaps["ZZ00T0001|2026-09-20|99213"]["finding_id"] == "F-closed"
    assert a["coverage"]["missed_by"] == {"closed_by_remitwatch": 1, "expired": 1}


def test_the_self_audit_reads_the_stores_own_lines_and_needs_no_ledger(ledger_world):
    """The audit's rules read the store's EMA lines, which keep every line they look at (the summary keeps every line
    the ledger holds, item or not): a write-off at posting with no item is found without the ledger, which the audit
    therefore does not take (finding 7)."""
    import inspect
    s, _lg = ledger_world
    assert "ledger" not in inspect.signature(sc.self_audit).parameters
    sc.self_audit(s.conn, today="2026-05-01", force=True)
    gaps = sc.audit_lists(s.conn, "gap")
    assert any(g["category"] == "auto_written_off_appealable" and g["cents"] == 20000 for g in gaps), gaps


def _prod_with_april():
    """prod_rows() and one posting in April, so March is a closed posting month."""
    return prod_rows() + [F.production_row(posted=F.D(2026, 4, 10), svc=F.D(2026, 4, 6), cpt="99212", link=F.link_no(3),
                                           charges=80.0, payer=F.PAYERS["bcbs"])]


def test_a_payment_a_newer_export_of_the_same_box_drops_is_a_visible_gap(tmp_path):
    """NON-NEGOTIABLE 1: a newer Production export of the same box that lacks one posting (a filtered or partial export)
    replaces the old file and the ledger loses a $90 payment and its $40 contractual. Money posted in a closed month never
    disappears: posted_money fails (critical) with the dollars, holds the next run, and counts in the gap total; a person
    accepting the change moves the mark (finding 10)."""
    s = _new("file", tmp_path)
    lg = el.Ledger.open(str(tmp_path / "ema_ledger.sqlite"))
    try:
        rows = _prod_with_april()
        assert er.ingest_file(s, F.write(str(tmp_path / "p1.csv"), F.production_csv(rows)), via="test", ledger=lg,
                              log=lambda *a: None)["status"] == "current"
        el.refresh(s, lg, log=lambda *a: None)
        add_item(s.conn, "F-1")
        first = sc.check_ledger(s.conn, lg, today=TODAY)
        assert check_of(first["checks"], "posted_money")["status"] == "ok"
        run(s.conn, None, "r1")
        rows2 = [r for r in rows if r.get("Insurance Payments") != 90.0]
        rows2.append(F.production_row(posted=F.D(2026, 4, 12), svc=F.D(2026, 3, 9), cpt="99212", link=None, pat_adj=5.0,
                                      payer="Patient", patient=1))
        r2 = er.ingest_file(s, F.write(str(tmp_path / "p2.csv"), F.production_csv(rows2)), via="test", ledger=lg, log=lambda *a: None)
        assert r2["status"] == "current" and r2["replaced"]
        el.refresh(s, lg, log=lambda *a: None)
        out = sc.check_ledger(s.conn, lg, today=TODAY)
        pm = check_of(out["checks"], "posted_money")
        assert pm["status"] == "fail" and pm["severity"] == "critical" and pm["cents"] == 13000, pm
        assert out["gaps"]["posted_drop_cents"] == 13000 and out["gaps"]["total_cents"] >= 13000
        held = run(s.conn, None, "r2")
        assert held["held"] and held["hold_checks"] == ["posted_money"]
        sc.accept_findings(s.conn, "owner@example.test", ["posted_money"])     # the export was meant to drop it
        assert check_of(sc.check_ledger(s.conn, lg, today=TODAY)["checks"], "posted_money")["status"] == "ok"
        assert not run(s.conn, None, "r3")["held"]
    finally:
        lg.close()
        s.close()


def test_a_line_the_ar_shows_settled_while_its_postings_leave_money_open_is_a_gap(tmp_path):
    """The same lost payment seen with no earlier measurement: EMA's A/R says nothing is owed on the line while its
    postings leave $130 (the patient's $20, paid on no claim number, is joined to the line first). line_balance compares
    both ways now; a line that left the A/R while its postings leave money open is a gap too (finding 10)."""
    s = _new("file", tmp_path)
    lg = el.Ledger.open(str(tmp_path / "ema_ledger.sqlite"))
    try:
        rows = prod_rows()
        er.ingest_file(s, F.write(str(tmp_path / "p1.csv"), F.production_csv(rows)), via="test", ledger=lg, log=lambda *a: None)
        el.refresh(s, lg, log=lambda *a: None)
        rows2 = [r for r in rows if r.get("Insurance Payments") != 90.0]
        rows2.append(F.production_row(posted=F.D(2026, 4, 2), svc=F.D(2026, 3, 9), cpt="99212", link=None, pat_adj=5.0,
                                      payer="Patient", patient=1))           # a newer export: it reaches past the first one
        r2 = er.ingest_file(s, F.write(str(tmp_path / "p2.csv"), F.production_csv(rows2)), via="test", ledger=lg, log=lambda *a: None)
        assert r2["status"] == "current" and r2["replaced"]
        el.refresh(s, lg, log=lambda *a: None)
        s.conn.execute("""INSERT INTO ema_ar (file_id, row_no, as_of, link, svc, cpt, party, balance_cents, facility)
                          VALUES ('ar1', 1, '2026-11-20', ?, '2026-03-02', '99213', 'Insurance', 0, 0)""", (F.link_no(1),))
        s.conn.commit()
        out = sc.check_ledger(s.conn, lg, today=TODAY)
        lb = check_of(out["checks"], "line_balance")
        assert lb["status"] == "fail" and lb["cents"] == 13000 and out["ar_balance"]["postings_above"] == 1, out["ar_balance"]
        assert out["gaps"]["postings_above_cents"] == 13000
    finally:
        lg.close()
        s.close()


def test_a_denied_line_the_ar_lists_by_its_denial_amount_agrees_with_its_postings(tmp_path):
    """EMA's A/R lists a denied line with $0 in Current A/R and the money in Denial Amount (the store's exposure_cents).
    Its postings (the charge, no money since) leave the same $150 open: the line agrees, it is no gap. Found on the real
    world: 545 lines ($192,106.30) read as 'the postings leave more' because the A/R's current column was compared alone."""
    s = _new("file", tmp_path)
    lg = el.Ledger.open(str(tmp_path / "ema_ledger.sqlite"))
    try:
        rows = prod_rows() + [F.production_row(posted=F.D(2026, 3, 6), svc=F.D(2026, 3, 5), cpt="99212", link=F.link_no(4),
                                               charges=150.0, payer=F.PAYERS["bcbs"])]      # billed, denied, nothing posted since
        er.ingest_file(s, F.write(str(tmp_path / "p.csv"), F.production_csv(rows)), via="test", ledger=lg, log=lambda *a: None)
        el.refresh(s, lg, log=lambda *a: None)
        s.conn.execute("""INSERT INTO ema_ar (file_id, row_no, as_of, link, svc, cpt, party, balance_type, balance_cents,
                                              denial_cents, exposure_cents, facility)
                          VALUES ('ar1', 1, '2026-11-20', ?, '2026-03-05', '99212', 'Insurance', 'Debit', 0, 15000, 15000, 0)""",
                       (F.link_no(4),))
        s.conn.commit()
        bal = sc.check_ledger(s.conn, lg, today=TODAY)["ar_balance"]
        assert (bal["equal"], bal["postings_above"], bal["ar_above"]) == (1, 0, 0), bal
        s.conn.execute("UPDATE ema_ar SET denial_cents = 10000, exposure_cents = 10000")       # the A/R holds $50 less
        s.conn.commit()
        bal = sc.check_ledger(s.conn, lg, today=TODAY)["ar_balance"]
        assert (bal["postings_above"], bal["postings_above_cents"]) == (1, 5000), bal
    finally:
        lg.close()
        s.close()


def test_blind_dollars_leave_out_claims_ema_shows_answered(ledger_world):
    """'billed since then has no payer's answer on file': both March lines have the insurer's answer posted in EMA (a $90
    payment; a CO-197 denial), so they are not blind dollars (finding 11)."""
    s, lg = ledger_world
    c = s.conn
    remit(c, "2026-02-28", "ZZ00T0999C001", [("99213", 150, 90, [("CO", "45", 60)], "2026-02-20")])   # the newest 835
    sc.check_ledger(c, lg, today=TODAY)
    b = sc.blindness(c, today=TODAY)
    src = next(x for x in b["sources"] if x["source"] == "835")
    assert src["cents"] == 0 and not src["about"], (src["cents"], src["text"])
    c.execute("UPDATE ema_lines SET ins_answer_rows = 0 WHERE cpt = '99214'")                       # one with no answer
    c.commit()
    src = next(x for x in sc.blindness(c, today=TODAY)["sources"] if x["source"] == "835")
    assert src["cents"] == 20000


def test_a_write_off_with_no_deadline_on_file_is_judged_by_the_payers_window(store):
    """given_up splits recoverable and lost by the item's deadlines; with none on file (677 Work now items in the real
    world) the payer's own appeal window from its answer decides, never 'lost' by default (finding 9)."""
    c = store.conn
    add_item(c, "F-nodl", written_off_cents=5000, written_off_when="at_posting", service_date="2026-10-02",
             payment_date="2026-10-20", appeal_deadline=None, carc="96", signal="DENIED_NONCOVERED")
    add_item(c, "F-old", written_off_cents=3000, written_off_when="at_posting", service_date="2025-02-02",
             payment_date="2025-02-20", appeal_deadline=None, carc="96", signal="DENIED_NONCOVERED")
    g = sc.given_up(c, today=TODAY)
    assert (g["recoverable_cents"], g["lost_cents"]) == (5000, 3000), (g["recoverable_cents"], g["lost_cents"])
    assert g["window_from_payer_cents"] == 8000


def test_a_proof_row_with_no_money_is_not_proof_of_payment(store):
    """has_proof needs money: a $0 payment row, a takeback or a denial row is not a payment that closes an item (finding 8)."""
    c = store.conn
    add_item(c, "F-zero", status="resolved", work_state="done", auto_note="Paid",
             evidence={"paid_later": {"payment_date": "2026-05-01", "paid_cents": 0, "trace": "EMA check ending 1234",
                                      "proof": [{"date": "2026-05-01", "source": "production", "kind": "payment", "amount_cents": 0}]}})
    add_item(c, "F-takeback", status="resolved", work_state="done", auto_note="Paid",
             evidence={"paid_later": {"proof": [{"date": "2026-05-01", "source": "835", "kind": "reversal", "amount_cents": -9000}]}})
    add_item(c, "F-netted", status="resolved", work_state="done", auto_note="Paid",
             evidence={"paid_later": {"proof": [{"date": "2026-05-01", "kind": "payment", "amount_cents": 9000},
                                                {"date": "2026-05-09", "kind": "reversal", "amount_cents": 9000}]}})
    add_item(c, "F-denial-row", status="resolved", work_state="done", auto_note="Paid",
             evidence={"archive_rule": {"action": "close_with_proof", "rule": "invented",
                                        "proof": [{"date": "2026-04-01", "source": "835", "kind": "denial", "amount_cents": 0}]}})
    add_item(c, "F-paid", status="resolved", work_state="done", auto_note="Paid",
             evidence={"archive_rule": {"action": "close_with_proof", "rule": "invented",
                                        "proof": [{"date": "2026-04-01", "kind": "denial", "amount_cents": 0},
                                                  {"date": "2026-05-01", "kind": "payment", "amount_cents": 9000}]}})
    r = sc.check_closure_proof(c, TODAY)
    assert sorted(r["examples"]) == ["F-denial-row", "F-netted", "F-takeback", "F-zero"], r["examples"]


def test_a_payment_emas_report_shows_names_its_amount_claim_and_report(store):
    """detect.resolve_elsewhere closes an item EMA shows paid by insurance; the weekly charge lines give the amount, the
    claim and the export but no payment date. That names the payment (a person checks it in EMA from the claim and the
    report), so it is proof; one with no amount, no claim or no report is not. Found by wiring the check into detection:
    such a closure held the run."""
    c = store.conn
    ema = {"payment_date": None, "claim": "ZZ00T0001C001", "paid_cents": 11760, "how": "EMA shows it paid",
           "trace": "EMA 99214 2026-10-01", "source": "Encounter & Charge Lines.xlsx"}
    add_item(c, "F-ema", status="resolved", work_state="done", auto_note="EMA shows it paid", evidence={"paid_later": ema})
    add_item(c, "F-no-report", status="resolved", work_state="done", auto_note="Paid",
             evidence={"paid_later": dict(ema, source=None)})
    add_item(c, "F-no-claim", status="resolved", work_state="done", auto_note="Paid",
             evidence={"paid_later": dict(ema, claim=None, trace=None)})
    add_item(c, "F-nothing-paid", status="resolved", work_state="done", auto_note="Paid",
             evidence={"paid_later": dict(ema, paid_cents=0)})
    r = sc.check_closure_proof(c, TODAY)
    assert sorted(r["examples"]) == ["F-no-claim", "F-no-report", "F-nothing-paid"], r["examples"]


def test_a_closure_by_the_payers_own_settling_answer_is_proof(store):
    """detect._close_by_answer closes a denial the same payer later settled (processed it as secondary applying the
    primary's answer, or put it on the patient's share): nothing more is owed, and the payer's dated answer on its claim
    is the proof (evidence settled_by), with no money. Found by wiring: such closures held the run. A settled_by with
    no date or no claim is no proof."""
    c = store.conn
    sb = {"payer": "Medicaid KY", "date": "2026-11-10", "claim": "ZZ00T0001C017", "how": "processed as secondary", "paid_cents": 0}
    later = {"payment_date": "2026-11-10", "claim": "ZZ00T0001C017", "trace": "TRACE0001", "paid_cents": 0, "how": "processed as secondary"}
    add_item(c, "F-settled", status="resolved", work_state="done", auto_note="Processed as secondary",
             evidence={"settled_by": sb, "paid_later": later})
    add_item(c, "F-undated", status="resolved", work_state="done", auto_note="Processed as secondary",
             evidence={"settled_by": dict(sb, date=None), "paid_later": dict(later, payment_date=None)})
    add_item(c, "F-no-claim", status="resolved", work_state="done", auto_note="Processed as secondary",
             evidence={"settled_by": dict(sb, claim=None), "paid_later": dict(later, claim=None, trace=None)})
    r = sc.check_closure_proof(c, TODAY)
    assert sorted(r["examples"]) == ["F-no-claim", "F-undated"], r["examples"]


def test_items_regrouped_by_a_pattern_this_run_made_are_not_kept(store):
    """Underpaid one-offs leave when this run groups their lines into a routine-rate pattern (an item that lists the lines
    in its evidence, line_keys), and a pattern leaves when this run lists its lines one by one again: both are routes,
    not vanishings (found by wiring: the one-offs were kept beside the pattern, their dollars counted twice). A one-off
    whose line no new item covers is still kept."""
    c = store.conn
    for fid, lk in (("F-one-1", "L1"), ("F-one-2", "L2"), ("F-lone", "L9")):
        add_item(c, fid, signal="UNDERPAID_VS_OWN_RATE", category="underpayment", line_key=lk)
    add_item(c, "F-pattern", signal="SECOND_RATE", category="underpayment", line_key=None, claim_key=None,
             patient_control_number=None, evidence={"line_keys": ["L5", "L6"]})
    run(c, None, "r1")

    def regroup(cn):
        cn.execute("DELETE FROM findings")
        add_item(cn, "F-new-pattern", signal="SECOND_RATE", category="underpayment", line_key=None, claim_key=None,
                 patient_control_number=None, evidence={"line_keys": ["L1", "L2"]})
        for fid, lk in (("F-new-5", "L5"), ("F-new-6", "L6")):
            add_item(cn, fid, signal="UNDERPAID_VS_OWN_RATE", category="underpayment", line_key=lk)
    out = run(c, regroup, "r2")
    van = check_of(sc.meta_get(c, sc.K_LAST)["checks"], "vanished")
    assert out["kept"] == 1 and set(van["examples"]) == {"F-lone"}, van


def test_a_pattern_whose_lines_an_older_pattern_takes_back_is_not_kept(store):
    """A continuation of a routine-rate pattern leaves when the earlier continuation a person reopened lists its lines
    again (review3's continuations case): the older item's own lines grew this run to cover them, a route. A pattern
    nothing covers is still kept."""
    c = store.conn
    pat = dict(signal="SECOND_RATE", category="underpayment", line_key=None, claim_key=None, patient_control_number=None)
    add_item(c, "F-older", evidence={"line_keys": ["L1", "L2"]}, **pat)
    add_item(c, "F-continuation", evidence={"line_keys": ["L3"]}, **pat)
    add_item(c, "F-alone", evidence={"line_keys": ["L7"]}, **pat)
    run(c, None, "r1")

    def take_back(cn):
        cn.execute("DELETE FROM findings WHERE finding_id IN ('F-continuation', 'F-alone')")
        cn.execute("UPDATE findings SET evidence=? WHERE finding_id='F-older'", (json.dumps({"line_keys": ["L1", "L2", "L3"]}),))
    out = run(c, take_back, "r2")
    van = check_of(sc.meta_get(c, sc.K_LAST)["checks"], "vanished")
    assert out["kept"] == 1 and set(van["examples"]) == {"F-alone"}, van


def test_an_answer_delivered_again_leaves_by_the_delivery_that_keeps_the_item(store):
    """The payer's same answer delivered twice the same day is one item (detect._answer_copies, trace finding T-I3): the
    delivery a person worked keeps it and the other delivery's item leaves the list. The detector names the lines it
    passed by (ctx `delivered_again`): a route, not a vanishing (found by wiring: the item was kept beside the worked one,
    one underpayment listed twice). An item on a line the detector did not name is still kept."""
    c = store.conn
    add_item(c, "F-copy", signal="UNDERPAID_VS_OWN_RATE", category="underpayment", line_key="L1")
    add_item(c, "F-worked", signal="UNDERPAID_VS_OWN_RATE", category="underpayment", line_key="L2", status="in_progress")
    add_item(c, "F-lone", signal="UNDERPAID_VS_OWN_RATE", category="underpayment", line_key="L9")
    run(c, None, "r1")
    ctx = sc.begin_detection(c, run_id="r2", today=TODAY)
    c.execute("DELETE FROM findings WHERE finding_id IN ('F-copy', 'F-lone')")
    c.commit()
    out = sc.after_detection(c, dict(ctx, delivered_again=["L1"]), mode="server", today=TODAY, run_id="r2")
    van = check_of(sc.meta_get(c, sc.K_LAST)["checks"], "vanished")
    assert out["kept"] == 1 and set(van["examples"]) == {"F-lone"}, van
    assert van["info"]["routes"].get("delivered_again") == 1


def test_an_item_that_waited_for_emas_line_leaves_when_the_line_comes(store):
    """A second payer's item raised before EMA's line was on file (SECONDARY_NOT_BILLED: EMA then shows any answer)
    leaves when a Production report read since the run before brings the line: new facts on its own claim, a route. Found
    on the real world copy (299 such items kept). An item whose line is still not on file is kept."""
    c = store.conn
    snb = dict(signal="SECONDARY_NOT_BILLED", category="denial", carc=None, service_date="2026-03-02", procedure_code="99213")
    add_item(c, "F-waited", line_key="La", patient_control_number="ZZ00T0001C001", **snb)
    add_item(c, "F-still", line_key="Lb", patient_control_number="ZZ00T0002C001", **snb)
    run(c, None, "r1")

    def save(cn):
        cn.execute("DELETE FROM findings")
        report_read(cn, "prod-2")                                                   # a Production report read since
        ema_line(cn, 7001, bill="ZZ00T0001", ins_paid=4000, balance=0, status_class="paid")     # the line came: paid
    out = run(c, save, "r2")
    van = check_of(sc.meta_get(c, sc.K_LAST)["checks"], "vanished")
    assert van["examples"] == ["F-still"] and van["info"]["routes"] == {"facts_changed": 1} and out["kept"] == 1, van


def report_read(conn, file_id: str, report: str = "production") -> None:
    """One EMA report file read and applied (ema_report_files), as the readers record it."""
    conn.execute("INSERT INTO ema_report_files (file_id, report, status, applied_at) VALUES (?, ?, 'current', ?)",
                 (file_id, report, f"2026-11-22T0{len(file_id) % 9}:00:00+00:00"))
    conn.commit()


def test_facts_are_new_only_when_a_file_was_read_since_the_run_before(store):
    """New facts on an item's own claim are new source data: an 835 or an EMA report read since the run before. The
    store's own summaries changing with nothing read (a refresh, a rebuild, a release) are no new facts: an item that leaves
    then is kept (found by the code-bug drill: a summary RemitWatch kept and dropped by itself excused 320 departures every
    other run). Once a report is read, the same change is a route."""
    c = store.conn
    snb = dict(signal="SECONDARY_NOT_BILLED", category="denial", carc=None, service_date="2026-03-02", procedure_code="99213")
    ema_line(c, 7001, bill="ZZ00T0001", ins_paid=0, balance=4000)
    report_read(c, "prod-1")
    add_item(c, "F-derived", line_key="La", patient_control_number="ZZ00T0001C001", **snb)
    run(c, None, "r1")

    def summary_only(cn):
        cn.execute("DELETE FROM findings")
        cn.execute("UPDATE ema_lines SET ins_paid = 4000, balance = 0 WHERE link = 7001")    # nothing read: derived only
    out = run(c, summary_only, "r2")
    van = check_of(sc.meta_get(c, sc.K_LAST)["checks"], "vanished")
    assert van["examples"] == ["F-derived"] and "facts_changed" not in van["info"]["routes"] and out["kept"] == 1, van

    def read_then(cn):
        cn.execute("DELETE FROM findings WHERE finding_id = 'F-derived'")
        report_read(cn, "prod-22")
        cn.execute("UPDATE ema_lines SET ins_paid = 3900 WHERE link = 7001")
    run(c, read_then, "r3")
    van = check_of(sc.meta_get(c, sc.K_LAST)["checks"], "vanished")
    assert van["status"] == "ok" and van["info"]["routes"] == {"facts_changed": 1}, van


def test_items_no_list_shows_that_a_run_stops_raising_are_counted_not_kept(store):
    """A rule change, or a bug, that stops raising a category also drops its items no list shows (expired past their
    windows, done, closed with their proof): the scoreboard's record. Found by the code-bug drill: 255 authorization
    denials, expired or done, left the record and no check counted them. A warning check of their own counts them, by
    signal and state, with examples; only listed items are put back. An item this run made for the same line is a route."""
    c = store.conn
    add_item(c, "F-listed", line_key="L1")
    add_item(c, "F-expired", line_key="L2", work_state="expired")
    add_item(c, "F-done", line_key="L3", status="resolved", work_state="done", auto_note="Paid on a later remittance")
    add_item(c, "F-replaced", line_key="L4", work_state="expired")
    run(c, None, "r1")

    def save(cn):
        cn.execute("DELETE FROM findings")
        add_item(cn, "F-new-on-L4", line_key="L4", signal="DENIED_OTHER", work_state="expired")
    out = run(c, save, "r2")
    checks = sc.meta_get(c, sc.K_LAST)["checks"]
    van, left = check_of(checks, "vanished"), check_of(checks, "left_record")
    assert van["examples"] == ["F-listed"] and out["kept"] == 1, van
    assert left["status"] == "fail" and left["severity"] == "warning" and sorted(left["examples"]) == ["F-done", "F-expired"], left
    assert left["info"]["by_signal"] == {"DENIED_AUTH": 2} and left["info"]["by_state"] == {"done": 1, "expired": 1}, left
    assert left["info"]["routes"].get("same_line") == 1, left
    assert c.execute("SELECT COUNT(*) FROM findings WHERE finding_id IN ('F-expired', 'F-done')").fetchone()[0] == 0


def test_fingerprints_another_build_made_never_let_an_item_go(store):
    """The fingerprint is code: a build that reads more of an item's claim (the EMA line above) makes every fingerprint
    the build before stored differ, and a departure that would read as new facts is only the new code. Found on the world
    copy: 1,086 items kept for a person's decision left in one run when the EMA line part came. Fingerprints of another
    build are not compared: the item is kept, and this run stores its own for the next."""
    c = store.conn
    snb = dict(signal="SECONDARY_NOT_BILLED", category="denial", carc=None, service_date="2026-03-02", procedure_code="99213")
    add_item(c, "F-waited", line_key="La", patient_control_number="ZZ00T0001C001", **snb)
    ema_line(c, 7001, bill="ZZ00T0001", ins_paid=4000, balance=0, status_class="paid")      # on file all along
    run(c, None, "r1")
    sc.meta_set(c, sc.K_FACTS_VERSION, "an older build")        # the fingerprints were stored by the build before
    c.execute("UPDATE selfcheck_facts SET fp = 'fp-of-old-code'")
    c.commit()
    out = run(c, lambda cn: cn.execute("DELETE FROM findings"), "r2")
    van = check_of(sc.meta_get(c, sc.K_LAST)["checks"], "vanished")
    assert van["examples"] == ["F-waited"] and not van["info"]["routes"] and out["kept"] == 1, van
    assert sc.meta_get(c, sc.K_FACTS_VERSION) == sc.FACTS_VERSION      # this run's own, compared next time


def test_the_precision_guard_counts_a_service_sent_again_once(kind):
    """Two lines of one code on one claim are two units (their shortfalls add up); the same line sent again on a new claim
    is the same money (finding 12)."""
    c = kind.conn
    lk1 = remit(c, "2026-03-20", "ZZ00T0004C001", [("99213", 150, 0, [("CO", "197", 150)])])[0]
    lk2 = remit(c, "2026-05-20", "ZZ00T0004C002", [("99213", 150, 0, [("CO", "16", 150)])])[0]    # sent again, denied again
    remit(c, "2026-07-20", "ZZ00T0004C003", [("99213", 150, 98, [("CO", "45", 52)])])            # sent again, paid in full
    add_item(c, "F-orig", line_key=lk1, patient_control_number="ZZ00T0004C001", shortfall_cents=9800, payment_date="2026-03-20")
    add_item(c, "F-resub", line_key=lk2, patient_control_number="ZZ00T0004C002", shortfall_cents=9800, payment_date="2026-05-20",
             carc="16")
    kind.reopen()
    r = sc.check_precision_guard(kind.conn, TODAY)
    assert sorted(r["examples"]) == ["F-orig", "F-resub"], r["info"]


def test_a_held_run_still_lists_the_new_items_it_found(store):
    """Safe mode holds the run's closures and keeps the worklist from before, plus the new items to work the run found
    that no failing check names: a new denial due in ten days is not hidden while the run is held (finding 6)."""
    c = store.conn
    add_item(c, "F-1")
    add_item(c, "F-2")
    run(c, None, "r1")

    def save(cn):
        bad_closure(cn)                                                       # the run's bug: one closure without proof
        add_item(cn, "F-new", line_key="Lnew", appeal_deadline="2026-12-02")  # a new denial, due in ten days
    out = run(c, save, "r2")
    assert out["held"] and "F-new" in _listed(c) and c.execute("SELECT status FROM findings WHERE finding_id='F-2'").fetchone()[0] == "open"
    assert sc.safe_mode(c)["new_items"] == 1


def test_write_offs_on_items_a_person_let_go_are_not_money_with_no_item(ledger_world):
    """given_up's 'written off at posting with no RemitWatch item' counts every item on record, whatever its status: an
    item a person let go is an item (finding 9b)."""
    s, lg = ledger_world
    c = s.conn
    out = sc.check_ledger(c, lg, today=TODAY)
    assert out["conservation"]["totals_since"]["wo_at_posting"] == 20000
    add_item(c, "F-let-go", status="dismissed", work_state="accepted", written_off_cents=20000, written_off_when="at_posting",
             service_date="2026-03-03", procedure_code="99214", carc="197")
    g = sc.given_up(c, today=TODAY)
    assert g["with_no_item_cents"] == 0 and g["items_written_off_cents"] == 20000, g


def test_the_tmp_reading_sees_sqlites_unlinked_temporary_files(tmp_path):
    """SQLite unlinks its temporary files as it opens them: the /tmp reading counts the open files in the folder too
    (finding 14)."""
    import subprocess
    import textwrap
    folder = tmp_path / "tmpdir"
    folder.mkdir()
    code = textwrap.dedent(f'''
        import os, sqlite3, sys
        sys.path.insert(0, {REPO_DIR!r})
        from remitwatch import selfcheck as sc
        c = sqlite3.connect(":memory:")
        c.execute("PRAGMA temp.cache_size=-256")
        c.execute("CREATE TEMP TABLE big AS WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i+1 FROM n WHERE i < 80000) "
                  "SELECT i, randomblob(400) AS b FROM n")
        held = 0
        for fd in os.listdir("/proc/self/fd"):
            try:
                target = os.readlink(f"/proc/self/fd/{{fd}}")
            except OSError:
                continue
            if target.startswith(sys.argv[1]):
                held += os.fstat(int(fd)).st_size
        print(sc.tmp_used_mb(sys.argv[1]), held >> 20)
    ''')
    env = dict(os.environ, SQLITE_TMPDIR=str(folder), TMPDIR=str(folder))
    res = subprocess.run([sys.executable, "-I", "-c", code, str(folder)], env=env, capture_output=True, text=True, timeout=180)
    seen, held = map(int, res.stdout.split())
    assert held >= 20 and seen >= held, (seen, held, res.stderr[-300:])


def test_a_critical_check_that_crashes_raises_an_alert_and_a_health_item(store, monkeypatch):
    """run_checks turns a crash into status 'error'; the guard that check gives is off, so the alert and Data health say
    so (finding 15)."""
    c = store.conn
    add_item(c, "F-1")
    run(c, None, "r1")

    def broken(cn, today):
        raise sqlite3.OperationalError("no such column: invented")
    monkeypatch.setitem(sc.ITEM_CHECKS, "precision_guard", broken)
    for rid in ("r2", "r3"):
        out = run(c, None, rid)
    assert out["errors"] == ["precision_guard"] and not out["held"]
    kinds = {a["key"]: a for a in sc.alert_candidates(c, today=TODAY)}
    assert ("CHECK_FAILED", "precision_guard") in kinds and "could not run" in kinds[("CHECK_FAILED", "precision_guard")]["title"]
    assert "selfcheck_precision_guard" in {d["key"] for d in sc.data_health(c, TODAY)}


def test_the_first_run_keeps_vanished_items_too(store):
    """The first wired run is the release that changes the rules most: an open item the new rules no longer produce is
    kept like on any other run (only safe mode's hold needs a baseline) (finding 16)."""
    c = store.conn
    add_item(c, "F-a", line_key="La")                      # items saved by the release before (no self-check yet)
    add_item(c, "F-b", line_key="Lb")
    out = run(c, lambda cn: cn.execute("DELETE FROM findings WHERE finding_id='F-a'"), "r1")   # the new rules drop F-a
    assert out["first"] and check_of(sc.meta_get(c, sc.K_LAST)["checks"], "vanished")["examples"] == ["F-a"]
    assert "F-a" in _listed(c) and out["kept"] == 1
    assert [o["key"] for o in sc.offenders(c, "vanished")] == ["F-a"]          # the Data tab lists every one, not 20


def test_an_item_kept_from_a_store_with_no_states_yet_is_listed_and_kept_again(kind):
    """The store before this release keeps no states (no item has a work state). The first wired run keeps an item the
    new rules drop: it must land on Work now (an item with no state is on no list), and the next run, which sees the
    states the first one set, must keep it again instead of losing it with no trace (found on the real world: the first
    run kept 917 items with no state, and the second run dropped every one)."""
    c = kind.conn
    add_item(c, "F-a", line_key="La", work_state=None, state_basis=None)     # saved by the release before
    add_item(c, "F-b", line_key="Lb", work_state=None, state_basis=None)

    def first(cn):                                       # the new rules drop F-a and give F-b its state
        cn.execute("DELETE FROM findings WHERE finding_id='F-a'")
        cn.execute("UPDATE findings SET work_state='work_now' WHERE finding_id='F-b'")
    out = run(c, first, "r1")
    assert out["first"] and out["kept"] == 1 and "F-a" in _listed(c), (out["kept"], _listed(c))
    kind.reopen()
    c = kind.conn
    out = run(c, lambda cn: cn.execute("DELETE FROM findings WHERE finding_id='F-a'"), "r2")      # still not produced
    assert out["kept"] == 1 and "F-a" in _listed(c), (out["kept"], _listed(c))
    assert [o["key"] for o in sc.offenders(c, "vanished")] == ["F-a"]


def test_an_open_item_with_no_state_is_listed_as_the_lists_show_it(store):
    """In a store that keeps states, an open item with none (saved outside a detection run) shows on Work now, because
    every list reads a missing state as Work now or Waiting; when it disappears with no reason it is kept like any listed
    item."""
    c = store.conn
    add_item(c, "F-x", line_key="Lx", work_state=None, state_basis=None)
    add_item(c, "F-y", line_key="Ly")
    out = run(c, lambda cn: cn.execute("DELETE FROM findings WHERE finding_id='F-x'"), "r1")
    assert out["kept"] == 1 and "F-x" in _listed(c), (out["kept"], _listed(c))


def test_the_banner_and_the_alerts_name_a_check_as_it_is_written(store):
    """A check named after EMA keeps its capitals in the safe-mode sentence and in the alerts: "money posted in ema" reads
    as a typo to the people the banner is for (found on the real world's held run)."""
    c = store.conn
    add_item(c, "F-1")
    run(c, None, "r1")
    sc.meta_set(c, sc.K_MONEY, {"checks": [sc._result("ledger_store", [("cell-1", 500, None)])]})       # a cell off by $5
    out = run(c, None, "r2")
    assert out["held"] and out["hold_checks"] == ["ledger_store"], out
    text = sc.safe_mode(c)["text"]
    assert "the store's EMA money equals the posting ledger's" in text and "ema money" not in text, text
    sc.meta_set(c, sc.K_MONEY, {"checks": []})
    sc.meta_set(c, sc.K_POSTED, {"check": sc._check_error("posted_money", RuntimeError("invented"))})
    assert not run(c, None, "r3")["held"]
    titles = [a["title"] for a in sc.alert_candidates(c, today=TODAY) if a["kind"] == "CHECK_FAILED"]
    assert any("money posted in EMA never disappears" in t for t in titles), titles
    assert not any("posted in ema" in t for t in titles), titles


def test_any_new_coverage_gap_and_any_rise_in_money_given_up_raise_their_alerts(store):
    """The owner's decision 9: no dollar floor. A new $3 coverage gap raises AUDIT_GAPS; money given up rising by $1
    raises GIVEN_UP_RISING."""
    c = store.conn
    for i in range(4):
        remit(c, "2026-02-05", f"ZZ00T{400 + i:04d}C001", [("99213", 150, 90, [("CO", "45", 60)], "2026-02-01")])
    sc.self_audit(c, today="2026-11-08", force=True)
    remit(c, "2026-10-01", "ZZ00T0001C001", [("99213", 3, 0, [("CO", "197", 3)], "2026-09-20")])
    a = sc.self_audit(c, today=TODAY)
    assert a["new_gaps_n"] == 1 and a["new_gaps_cents"] == 300 and a["alert"]
    add_item(c, "F-wo", written_off_cents=10001, written_off_when="at_posting", service_date="2026-03-02")
    sc.meta_set(c, sc.K_GIVEN_UP, {"weeks": [{"week": "2026-W46", "cents": 10000}]})
    sc.given_up(c, today=TODAY)
    kinds = {x["kind"] for x in sc.alert_candidates(c, today=TODAY)}
    assert {"AUDIT_GAPS", "GIVEN_UP_RISING"} <= kinds


def test_a_line_that_left_the_ar_while_its_postings_leave_money_open_is_a_gap(tmp_path):
    """A line an earlier A/R listed and the newest no longer does (settled in EMA: ema_ar_items.absent_since) while its
    postings still leave $150 open is a gap (left_ar); one that left with nothing open is settled; a line no A/R ever
    listed is not compared (finding 10)."""
    s = _new("file", tmp_path)
    lg = el.Ledger.open(str(tmp_path / "ema_ledger.sqlite"))
    try:
        rows = prod_rows() + [F.production_row(posted=F.D(2026, 3, 6), svc=F.D(2026, 3, 5), cpt="99212", link=F.link_no(4),
                                               charges=150.0, payer=F.PAYERS["bcbs"]),      # billed, never answered
                              F.production_row(posted=F.D(2026, 3, 7), svc=F.D(2026, 3, 6), cpt="99212", link=F.link_no(5),
                                               charges=120.0, payer=F.PAYERS["bcbs"])]      # never on any A/R
        er.ingest_file(s, F.write(str(tmp_path / "p.csv"), F.production_csv(rows)), via="test", ledger=lg, log=lambda *a: None)
        el.refresh(s, lg, log=lambda *a: None)
        s.conn.executemany("""INSERT INTO ema_ar (file_id, row_no, as_of, link, svc, cpt, party, balance_cents, facility)
                              VALUES ('ar1', ?, '2026-11-20', ?, ?, ?, 'Insurance', 0, 0)""",
                           [(1, F.link_no(1), "2026-03-02", "99213"), (2, F.link_no(9), "2026-03-09", "99212")])
        s.conn.executemany("""INSERT INTO ema_ar_items (item_key, party, link, cpt, svc, first_seen, last_seen, snapshots, absent_since)
                              VALUES (?, 'Insurance', ?, ?, ?, '2026-10-30', '2026-11-13', 3, '2026-11-20')""",
                           [("k4", F.link_no(4), "99212", "2026-03-05"), ("k2", F.link_no(2), "99214", "2026-03-03")])
        s.conn.commit()
        bal = sc.check_ledger(s.conn, lg, today=TODAY)["ar_balance"]
        assert (bal["left_ar"], bal["left_ar_cents"]) == (1, 15000), bal
        assert bal["settled"] == 1 and bal["equal"] == 1 and bal["left_lines"] == 2      # line 2 left with nothing open
    finally:
        lg.close()
        s.close()


def test_a_held_run_holds_a_flood_of_new_items_with_it(store):
    """A held run lists the new work it found, but not a flood (more than one run brings): that is held with the run."""
    c = store.conn
    add_item(c, "F-1")
    add_item(c, "F-2")
    run(c, None, "r1")

    def save(cn):
        bad_closure(cn)
        for i in range(30):
            add_item(cn, f"F-new{i:02d}", line_key=f"Lnew{i}")
    out = run(c, save, "r2")
    assert out["held"] and not any(f.startswith("F-new") for f in _listed(c))
    safe = sc.safe_mode(c)
    assert safe["new_items"] == 0 and safe["new_items_held"] == 30 and "held the 30 new items" in safe["text"]


def test_an_item_whose_own_claim_got_new_facts_may_leave_without_being_kept(store):
    """Between two runs new answers arrived on the item's own bill: the rules' answer may rightly change, so an item that
    then leaves is not kept. An item whose claim shows the same facts as the run before is kept (a rule changed)."""
    c = store.conn
    remit(c, "2026-03-20", "ZZ00T0001C001", [("99213", 150, 0, [("CO", "197", 150)])])
    remit(c, "2026-03-20", "ZZ00T0002C001", [("99213", 150, 0, [("CO", "197", 150)])])
    add_item(c, "F-new-facts", line_key="La", patient_control_number="ZZ00T0001C001")
    add_item(c, "F-same-facts", line_key="Lb", patient_control_number="ZZ00T0002C001")
    run(c, None, "r1")                                                       # stores each listed item's facts

    def save(cn):
        cn.execute("DELETE FROM findings")
        remit(cn, "2026-11-15", "ZZ00T0001C002", [("99213", 150, 90, [("CO", "45", 60)])])   # its claim was paid since
    out = run(c, save, "r2")
    van = check_of(sc.meta_get(c, sc.K_LAST)["checks"], "vanished")
    assert van["examples"] == ["F-same-facts"] and van["info"]["routes"] == {"facts_changed": 1} and out["kept"] == 1


def test_a_claim_level_item_whose_claim_was_answered_may_leave(store):
    """An item about a whole claim (no answer from the payer) leaves when the payer's 835 answer arrives, even on the
    first run (no facts stored yet): not kept."""
    c = store.conn
    add_item(c, "F-noanswer", signal="NO_ANSWER", category="denial", line_key=None, patient_control_number="ZZ00T0007C001",
             first_seen="2026-10-01T00:00:00+00:00")

    def save(cn):
        cn.execute("DELETE FROM findings")
        remit(cn, "2026-11-01", "ZZ00T0007C001", [("99213", 150, 90, [("CO", "45", 60)])])
    out = run(c, save, "r1")
    van = check_of(sc.meta_get(c, sc.K_LAST)["checks"], "vanished")
    assert out["kept"] == 0 and van["info"]["routes"] == {"claim_answered": 1}, van


def test_a_person_letting_an_item_go_while_the_run_ran_is_not_a_vanished_item(store):
    """detect.save keeps an item a person worked as a stale row; one a person let go during the run left the list by
    that person's decision: not kept."""
    c = store.conn
    add_item(c, "F-go")
    run(c, None, "r1")
    out = run(c, lambda cn: cn.execute("UPDATE findings SET status='dismissed', stale=1 WHERE finding_id='F-go'"), "r2")
    van = check_of(sc.meta_get(c, sc.K_LAST)["checks"], "vanished")
    assert out["kept"] == 0 and van["info"]["routes"] == {"decided": 1}
    assert c.execute("SELECT stale FROM findings WHERE finding_id='F-go'").fetchone()[0] == 1


def test_a_known_money_gap_that_grows_is_new():
    """A money check's known offender (a month cell) is new again when its gap grows by more than $1; an item check's
    known offender is not (its key is the item)."""
    base = {"posted_money": {"2026-03|2026-03|X|ins_paid": 1000}, "closure_proof": {"F-1": 1000}}
    grown = {"id": "posted_money", "keys": [("2026-03|2026-03|X|ins_paid", 5000)]}
    same = {"id": "posted_money", "keys": [("2026-03|2026-03|X|ins_paid", 1050)]}
    item = {"id": "closure_proof", "keys": [("F-1", 9000)]}
    assert sc._new_keys(grown, base) == [("2026-03|2026-03|X|ins_paid", 5000)]
    assert sc._new_keys(same, base) == [] and sc._new_keys(item, base) == []
