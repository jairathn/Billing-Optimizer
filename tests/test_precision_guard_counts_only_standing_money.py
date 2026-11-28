"""The precision guard ("No item to work was already paid after its denial", a critical check that holds a run when more
than 5 new offenders appear) must name only items a later payment really covers. The month-by-month replay of 2026 found
it naming items no payment covers, every month, on three paths of selfcheck.later_payments:

1. a second payer's payment of the patient's share (claim status 2, after the first payer's downcode) counted as if the
   first payer had paid what its downcode took (the shortfall is the first payer's CO-186 level change; the second payer
   paid the copay);
2. a later payment the payer took back in full with a provider-level adjustment (PLB WO naming the claim) counted as
   standing (later_payments nets only claim status 22 reversals; detection and states.py net the PLB);
3. the item's own payment, answered again by the plan's network under another payer name the same week (same claim id,
   same amounts; EMA posts one check), counted as a second, later payment.

Each control is the same story where the money is really there (the same payer reprocesses at the billed level, the
payment stands, a new payment by the same payer). Further cases pin each rule of selfcheck.later_payments down: a
takeback named by the payer's own claim number is netted; one naming another claim, one dated before the payment, or one
for less than the payment leaves the rest standing; the reversal of a repeated answer is no money either, so a real
top-up after it is still caught; and another payer's posting in EMA (no 835) is not a downcode's money. Invented data
only, on the server's file store, the compact in-memory store and that store's serialized image.
"""
from __future__ import annotations

import itertools
import json
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch import selfcheck as sc  # noqa: E402
from remitwatch.detect import ensure_schema  # noqa: E402
from remitwatch.store import Store  # noqa: E402

TODAY = "2026-11-24"
_TX = itertools.count(1)
CAN_SERIALIZE = hasattr(sqlite3.Connection, "serialize")


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    for k in ("VERCEL", "REMITWATCH_EMA_REPORTS", "REMITWATCH_EMA_LEDGER", "REMITWATCH_TODAY"):
        monkeypatch.delenv(k, raising=False)
    sc._GROUPS.clear()


class Kind:
    """A store of one kind; `image` builds the rows in a compact store and reopens its serialized image (cloud.load_raw)."""

    def __init__(self, kind: str, tmp_path):
        self.kind = kind
        self.store = Store(str(tmp_path / "s.sqlite")) if kind == "file" else Store(":memory:", compact=True)
        ensure_schema(self.store.conn)

    @property
    def conn(self) -> sqlite3.Connection:
        return self.store.conn

    def reopen(self) -> None:
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


def cents(v):
    return None if v is None else int(round(v * 100))


def remit(conn, paid_on: str, pcn: str, lines: list, *, payer="TESTCARE HEALTH", payer_id="TC001", status="1", cfi="CI",
          pccn: str | None = None) -> list[str]:
    """One remittance written straight into the 835 tables. lines: (code, charge, paid, [(group, reason, dollars)], extra)
    with dollars; extra may carry 'orig' (the billed code a payer changed) and 'allowed'. Returns the line keys."""
    n = next(_TX)
    tk, trace = f"TXPG{n:04d}", f"7{n:08d}"
    total = sum(cents(x[2]) for x in lines)
    conn.execute("""INSERT INTO transactions (transaction_key, trace_number, payment_date, payer_name, payer_id, superseded,
                    claim_paid_total_cents, payment_amount_cents, payment_method) VALUES (?,?,?,?,?,0,?,?,'ACH')""",
                 (tk, trace, paid_on, payer, payer_id, total, total))
    ck = f"{tk}-C0"
    conn.execute("""INSERT INTO claims (claim_key, transaction_key, patient_control_number, claim_status_code, charge_cents, paid_cents,
                    claim_filing_indicator, payer_name, payer_id, payment_date, trace_number, statement_from, statement_to,
                    payer_claim_control_number) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (ck, tk, pcn, status, sum(cents(x[1]) for x in lines), total, cfi, payer, payer_id, paid_on, trace,
                  "2026-03-02", "2026-03-02", pccn))
    keys = []
    for j, x in enumerate(lines):
        code, charge, paid, adj = x[0], x[1], x[2], x[3]
        extra = x[4] if len(x) > 4 else {}
        lk = f"{ck}-L{j}"
        conn.execute("""INSERT INTO lines (line_key, claim_key, transaction_key, procedure_code, original_procedure_code, charge_cents,
                        paid_cents, allowed_cents, paid_units, service_date, patient_control_number, claim_status_code, payer_name,
                        payer_id, payment_date) VALUES (?,?,?,?,?,?,?,?,1,'2026-03-02',?,?,?,?,?)""",
                     (lk, ck, tk, code, extra.get("orig"), cents(charge), cents(paid), cents(extra.get("allowed", paid or None)),
                      pcn, status, payer, payer_id, paid_on))
        for g, r, a in adj:
            conn.execute("""INSERT INTO adjustments (transaction_key, claim_key, line_key, level, group_code, reason_code, amount_cents)
                            VALUES (?,?,?,'line',?,?,?)""", (tk, ck, lk, g, r, cents(a)))
        keys.append(lk)
    conn.commit()
    return keys


def plb_takeback(conn, paid_on: str, reference: str, dollars: float, *, payer="TESTCARE HEALTH", payer_id="TC001") -> None:
    """A later remittance that carries no claim, only a provider-level adjustment: WO (an overpayment recovered) naming the
    claim it takes the money back from."""
    n = next(_TX)
    tk, trace = f"TXPG{n:04d}", f"7{n:08d}"
    conn.execute("""INSERT INTO transactions (transaction_key, trace_number, payment_date, payer_name, payer_id, superseded,
                    claim_paid_total_cents, payment_amount_cents, plb_total_cents, payment_method) VALUES (?,?,?,?,?,0,0,?,?,'ACH')""",
                 (tk, trace, paid_on, payer, payer_id, -cents(dollars), cents(dollars)))
    conn.execute("""INSERT INTO plb (transaction_key, reason_code, reference, amount_cents, payer_name, payer_id, payment_date, trace_number)
                    VALUES (?,?,?,?,?,?,?,?)""", (tk, "WO", reference, cents(dollars), payer, payer_id, paid_on, trace))
    conn.commit()


ITEM = dict(severity="medium", actionable="appeal", title="Invented item", status="open", work_state="work_now", paid_cents=0,
            payer_group="Testcare Health", payer_kind="commercial", payer_name="TESTCARE HEALTH", service_date="2026-03-02",
            procedure_code="99213", appeal_deadline="2027-03-01", deadline_basis="invented window",
            state_basis="Not paid and nobody has worked it; act by Mar 1, 2027", stale=0, evidence="{}",
            first_seen="2026-04-01T14:00:00+00:00", last_seen="2026-11-24T14:00:00+00:00", run_id="r0")


def add_item(conn, fid: str, **kw) -> None:
    row = dict(ITEM, finding_id=fid, **kw)
    row.setdefault("expected_cents", row.get("shortfall_cents"))
    cols = list(row)
    conn.execute(f"INSERT OR REPLACE INTO findings ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [row[c] for c in cols])
    conn.commit()


def guard(kind) -> dict:
    kind.reopen()
    return sc.check_precision_guard(kind.conn, TODAY)


def downcode(conn, fid: str, bill: str) -> None:
    """Testcare (primary) pays the 99204 billed as a 99203 (SVC06): $56.10 after the patient's $25.00 copay, and $198.90
    taken by the level change (CO-186). The item: the 99204's rate less the 99203's, $23.90."""
    lk = remit(conn, "2026-03-11", f"{bill}C017", [("99203", 280.00, 56.10, [("PR", "3", 25.00), ("CO", "186", 198.90)],
                                                    {"orig": "99204", "allowed": 81.10})])[0]
    add_item(conn, fid, signal="DOWNCODE_SVC06", category="downcoding", carc="186", line_key=lk, patient_control_number=f"{bill}C017",
             procedure_code="99203", original_procedure_code="99204", billed_code="99204", paid_cents=5610, shortfall_cents=2390,
             payment_date="2026-03-11")


def test_a_second_payers_copay_is_not_the_first_payers_downcode_paid(kind):
    c = kind.conn
    downcode(c, "F-copay", "ZZ00PG001")
    # the second payer (claim status 2: processed as secondary) pays the patient's $25.00 copay on the same bill, date and
    # billed code, the first payer's answer taken as made (OA-23)
    remit(c, "2026-03-31", "ZZ00PG001C024", [("99204", 280.00, 25.00, [("OA", "23", 255.00)])], payer="OTHERCARE SECONDARY",
          payer_id="OC002", status="2")
    r = guard(kind)
    assert "F-copay" not in r["examples"], (
        "the second payer's payment of the patient's copay ($25.00) was counted as the first payer paying its downcode "
        f"($23.90): {r['examples']}, {r['info']}")


def test_control_the_first_payer_reprocessing_at_the_billed_level_is_caught(kind):
    c = kind.conn
    downcode(c, "F-repriced", "ZZ00PG002")
    # Testcare itself pays the 99204 on the corrected claim: the level change is undone and the item should have closed
    remit(c, "2026-04-15", "ZZ00PG002C018", [("99204", 280.00, 80.00, [("PR", "3", 25.00), ("CO", "45", 175.00)])])
    r = guard(kind)
    assert r["examples"] == ["F-repriced"], (r["examples"], r["info"])


def denial_paid_later(conn, fid: str, bill: str, *, takeback: bool) -> None:
    """Testcare denies the 99213 for authorization (CO-197); on a later remittance it pays $61.20 for it; with `takeback`, it
    recovers those $61.20 three months later with a WO entry naming the claim that was paid (no claim lines: a PLB only)."""
    lk = remit(conn, "2026-03-20", f"{bill}C017", [("99213", 150.00, 0, [("CO", "197", 150.00)])])[0]
    add_item(conn, fid, signal="DENIED_AUTH", category="denial", carc="197", line_key=lk, patient_control_number=f"{bill}C017",
             shortfall_cents=6000, payment_date="2026-03-20")
    remit(conn, "2026-05-20", f"{bill}C018", [("99213", 150.00, 61.20, [("CO", "45", 85.00), ("PR", "2", 3.80)])])
    if takeback:
        plb_takeback(conn, "2026-08-20", f"{bill}C018", 61.20)


def test_a_payment_taken_back_by_a_provider_level_adjustment_covers_nothing(kind):
    denial_paid_later(kind.conn, "F-taken", "ZZ00PG003", takeback=True)
    r = guard(kind)
    assert "F-taken" not in r["examples"], (
        "a later payment the payer took back in full (PLB WO naming the claim) was counted as standing: "
        f"{r['examples']}, {r['info']}")


def test_control_a_later_payment_that_stands_is_caught(kind):
    denial_paid_later(kind.conn, "F-stands", "ZZ00PG004", takeback=False)
    r = guard(kind)
    assert r["examples"] == ["F-stands"], (r["examples"], r["info"])


def own_answer_repeated(conn, fid: str, bill: str, *, again: str, network: str = "NORTHWIND NETWORK SERVICES") -> None:
    """A self-funded plan's administrator pays the 99214 $101.15 (the plan's usual rate is $121.25: an underpayment of
    $20.10). `again`: 'network' -- three days later the plan's network answers the same claim id again, line for line the
    same ($101.15), under its own payer name; 'paid' -- the administrator pays the $20.10 left on a corrected claim."""
    lk = remit(conn, "2026-03-20", f"{bill}C017", [("99214", 224.00, 101.15, [("PR", "2", 11.25), ("CO", "45", 111.60)],
                                                    {"allowed": 112.40})], payer="NORTHWIND BENEFITS TPA", payer_id="NB003")[0]
    add_item(conn, fid, signal="UNDERPAID_VS_OWN_RATE", category="underpayment", carc=None, line_key=lk, procedure_code="99214",
             patient_control_number=f"{bill}C017", payer_group="Self-funded plan TPAs", payer_name="NORTHWIND BENEFITS TPA",
             paid_cents=10115, shortfall_cents=2010, payment_date="2026-03-20")
    if again == "network":
        remit(conn, "2026-03-23", f"{bill}C017", [("99214", 224.00, 101.15, [("PR", "2", 11.25), ("CO", "45", 111.60)],
                                                   {"allowed": 112.40})], payer=network, payer_id="NN004")
    else:
        remit(conn, "2026-05-02", f"{bill}C018", [("99214", 224.00, 20.10, [("CO", "45", 203.90)])], payer="NORTHWIND BENEFITS TPA",
              payer_id="NB003")


def test_the_items_own_answer_repeated_under_another_payer_name_is_no_new_money(kind):
    own_answer_repeated(kind.conn, "F-repeat", "ZZ00PG005", again="network")
    r = guard(kind)
    assert "F-repeat" not in r["examples"], (
        "the item's own $101.15 payment, answered again by the plan's network under another payer name, was counted as a "
        f"later payment covering the $20.10 underpayment: {r['examples']}, {r['info']}")


def test_control_the_administrator_paying_what_was_left_is_caught(kind):
    own_answer_repeated(kind.conn, "F-topped", "ZZ00PG006", again="paid")
    r = guard(kind)
    assert r["examples"] == ["F-topped"], (r["examples"], r["info"])


def test_later_payments_nets_the_takeback_and_leaves_out_the_second_payers_share(kind):
    """The function the guard rests on, directly: what it returns for the first two stories."""
    c = kind.conn
    downcode(c, "F-copay2", "ZZ00PG007")
    remit(c, "2026-03-31", "ZZ00PG007C024", [("99204", 280.00, 25.00, [("OA", "23", 255.00)])], payer="OTHERCARE SECONDARY",
          payer_id="OC002", status="2")
    denial_paid_later(c, "F-taken2", "ZZ00PG008", takeback=True)
    kind.reopen()
    c = kind.conn
    items = [dict(r) for r in c.execute("SELECT * FROM findings WHERE finding_id IN ('F-copay2', 'F-taken2')").fetchall()] \
        if c.row_factory else None
    if items is None:
        cur = c.execute("SELECT * FROM findings WHERE finding_id IN ('F-copay2', 'F-taken2')")
        names = [d[0] for d in cur.description]
        items = [dict(zip(names, r)) for r in cur.fetchall()]
    got = sc.later_payments(c, items)
    assert got["F-taken2"]["total"] <= 0, ("the WO entry took the $61.20 back", json.dumps(got["F-taken2"]))
    assert got["F-copay2"]["total"] < 2390 * sc.LATER_PAID_SHARE, ("the copay is the second payer's share", json.dumps(got["F-copay2"]))


# ===================================================================================== each rule of later_payments, pinned
def _denied(conn, fid: str, bill: str) -> None:
    lk = remit(conn, "2026-03-20", f"{bill}C017", [("99213", 150.00, 0, [("CO", "197", 150.00)])])[0]
    add_item(conn, fid, signal="DENIED_AUTH", category="denial", carc="197", line_key=lk, patient_control_number=f"{bill}C017",
             shortfall_cents=6000, payment_date="2026-03-20")


def test_a_takeback_named_by_the_payers_own_claim_number_covers_nothing_either(kind):
    c = kind.conn
    _denied(c, "F-number", "ZZ00PG011")
    remit(c, "2026-05-20", "ZZ00PG011C018", [("99213", 150.00, 61.20, [("CO", "45", 85.00), ("PR", "2", 3.80)])],
          pccn="PAYERCLM7731")
    plb_takeback(c, "2026-08-20", "PAYERCLM7731", 61.20)            # the payer's own number for that claim
    r = guard(kind)
    assert "F-number" not in r["examples"], (r["examples"], r["info"])


@pytest.mark.parametrize("how", ["another_claim", "before_the_payment", "part_of_it"])
def test_control_a_takeback_that_leaves_the_later_payment_standing_is_no_cover(kind, how):
    """The later payment still stands: the takeback names another claim, came before the payment (it recovered an older
    one), or took back only $2.00 of it. The guard must still name the item."""
    c = kind.conn
    _denied(c, "F-stands2", "ZZ00PG012")
    remit(c, "2026-05-20", "ZZ00PG012C018", [("99213", 150.00, 61.20, [("CO", "45", 85.00), ("PR", "2", 3.80)])])
    if how == "another_claim":
        remit(c, "2026-04-01", "ZZ00PG099C017", [("99212", 90.00, 40.00, [("CO", "45", 50.00)])])
        plb_takeback(c, "2026-08-20", "ZZ00PG099C017", 40.00)
    elif how == "before_the_payment":
        plb_takeback(c, "2026-05-01", "ZZ00PG012C018", 61.20)
    else:
        plb_takeback(c, "2026-08-20", "ZZ00PG012C018", 2.00)
    r = guard(kind)
    assert r["examples"] == ["F-stands2"], (how, r["examples"], r["info"])


SAME_GROUP_NETWORK = "NORTHWIND NETWORK TPA"         # another payer name of the item's own payer group (the plan's TPAs)


def test_the_items_own_answer_repeated_by_its_own_payer_group_is_no_new_money(kind):
    """The repeat comes under another name of the item's own payer group, so only the repeat rule can tell it from new
    money: the same claim id, code and paid amount as the item's own answer."""
    own_answer_repeated(kind.conn, "F-repeat2", "ZZ00PG015", again="network", network=SAME_GROUP_NETWORK)
    r = guard(kind)
    assert "F-repeat2" not in r["examples"], (r["examples"], r["info"])


def test_the_reversal_of_a_repeated_answer_is_no_money_and_a_real_top_up_after_it_is_caught(kind):
    """The network repeats the administrator's $101.15 answer, then reverses that repeat: nothing new either way. Then the
    administrator pays the $20.10 it left: the guard names the item."""
    c = kind.conn
    own_answer_repeated(c, "F-rev", "ZZ00PG013", again="network", network=SAME_GROUP_NETWORK)
    remit(c, "2026-04-02", "ZZ00PG013C017", [("99214", -224.00, -101.15, [("PR", "2", -11.25), ("CO", "45", -111.60)])],
          payer=SAME_GROUP_NETWORK, payer_id="NN004", status="22")
    assert "F-rev" not in guard(kind)["examples"]
    c = kind.conn
    remit(c, "2026-05-02", "ZZ00PG013C018", [("99214", 224.00, 20.10, [("CO", "45", 203.90)])], payer="NORTHWIND BENEFITS TPA",
          payer_id="NB003")
    r = guard(kind)
    assert r["examples"] == ["F-rev"], (r["examples"], r["info"])


def test_another_payers_posting_in_ema_is_not_a_downcodes_money(kind):
    """EMA posts the second payer's $25.00 copay (no 835 carries that check): a downcode's money is only its own payer's.
    The same payer's posting of the level it took (a check no 835 carries) still counts."""
    c = kind.conn
    sc_tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "ema_line_payments" not in sc_tables:
        from remitwatch import ema_reports
        ema_reports.ensure_schema(c)
    downcode(c, "F-ema", "ZZ00PG014")
    c.execute("UPDATE findings SET ema_link = 4114 WHERE finding_id = 'F-ema'")
    c.execute("""INSERT INTO ema_line_payments (link, svc, cpt, check_no, posted, payer, payer_group, role, ins_paid, rows, in_835)
                 VALUES (4114, '2026-03-02', '99204', 'EC0000771', '2026-04-10', 'Othercare Secondary', 'Othercare Secondary',
                         'secondary', 2500, 1, 0)""")
    c.commit()
    assert "F-ema" not in guard(kind)["examples"]
    c = kind.conn
    c.execute("""INSERT INTO ema_line_payments (link, svc, cpt, check_no, posted, payer, payer_group, role, ins_paid, rows, in_835)
                 VALUES (4114, '2026-03-02', '99204', 'EC0000772', '2026-05-10', 'Testcare Health', 'Testcare Health',
                         'primary', 2390, 1, 0)""")
    c.commit()
    r = guard(kind)
    assert r["examples"] == ["F-ema"], (r["examples"], r["info"])
