"""The fix wave's F1 (ledger and takebacks): the review of 2026-11-26's money findings (money-1, 2, 5, 6, 8, 9), hosts-4
and answers-5's paid_proof part, ported from the reviewers' and verifiers' failing tests (plans/review_2026-11-26.md),
plus the residuals of plans/next_fixes_2026-11-24.md section 4 that fall in the ledger and paid_proof.

Every name, member number, claim id, payer claim number, trace, payer id and amount here is invented. Each test runs on
both kinds of store: the server's file and the serverless image held in memory (compact)."""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch import states  # noqa: E402
from remitwatch.detect import REVERSALS_NOT_ON_FILE_META, Detector  # noqa: E402
from remitwatch.store import Store  # noqa: E402
from tests.review3.r3helpers import clm, sv, visit_denied_line, visit_paid, visit_reversed  # noqa: E402
from tests.test_features import clean_visits  # noqa: E402
from tests.test_remitwatch import remit  # noqa: E402

OTHER = "ASH*RO****MI*W55500304"
KY = {"payer": "KY MEDICARE", "payer_id": "MCRKY"}
GAP = {"payer": "TESTGAP SUPPLEMENT", "payer_id": "TGAP1"}
BILL = "CB0000300"


@pytest.fixture(params=["file", "compact"])
def kind(request):
    return request.param


class World:
    """One test store of either kind, fed remittance files one at a time, with a pinned run day."""

    def __init__(self, tmp_path, kind: str):
        self.tmp, self.n = tmp_path, 0
        self.store = Store(str(tmp_path / "s.sqlite")) if kind == "file" else Store(":memory:", compact=True)

    def remit(self, claims: list[str], pay_date: str, plb: str = "", payer: str = "ANTHEM", payer_id: str = "ANTHM") -> None:
        self.n += 1
        p = self.tmp / f"r{self.n:04d}.RMT"
        p.write_bytes(remit("".join(claims), "100.00", payer=payer, payer_id=payer_id, seg_count=99, isa_ctrl=f"{self.n:09d}",
                            pay_date=pay_date, plb=plb))
        self.store.ingest_835_paths([str(p)])

    def run(self, run_id: str, today: str | None = None) -> Detector:
        det = Detector(self.store, run_id=run_id, today=today)
        det.run()
        return det

    def rows(self, where: str = "1=1") -> list[dict]:
        return [dict(r) for r in self.store.query(f"SELECT * FROM findings WHERE {where} ORDER BY finding_id")]

    def meta(self, key: str):
        row = self.store.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row and row[0] else None


def brief(rows: list[dict]) -> list[tuple]:
    """A row's signal, payer, status, line or claim item, shortfall and recovery, for the test output."""
    return [(r["patient_control_number"], r["procedure_code"], r["signal"], r["payer_name"], r["status"], r.get("stale"),
             "line" if r["line_key"] else "claim", r["shortfall_cents"], r["recovered_cents"], (r.get("auto_note") or "")[:110],
             (r.get("hint") or "")[:90]) for r in rows]


def left_on(det, pcn: str) -> int:
    return sum(det.standing.get(l["line_key"], 0) for l in det.lines if l["patient_control_number"] == pcn)


# ================================================================================================ money-1
# A recoupment under $500 naming a claim whose payment is not on file reconciles nowhere: no item, no Data-tab count.
def _not_on_file_counted(w: World) -> int:
    m = w.meta(REVERSALS_NOT_ON_FILE_META) or {}
    return (m.get("cents") or 0) + (m.get("older_cents") or 0)


def _recoup_not_on_file(w: World, ref: str, cents: str) -> None:
    w.remit(clean_visits(1, 25, "99214", "223.65", "117.60"), "20260301")
    w.remit([visit_denied_line(f"{BILL}C024", carc="16", payer_ctl="PC300B")], "20260415")
    w.remit([visit_paid("CB0000900C017", who=OTHER, payer_ctl="PC900")], "20260803", plb=f"PLB*1777777772*20261231*{ref}*{cents}~")


def test_money1_a_recoupment_of_a_payment_not_on_file_is_counted_somewhere(tmp_path, kind):
    """(reviewer) The payer withholds $85.00 (PLB WO) naming claim C024, whose only answer on file is a denial: the payment
    it recoups came on a remittance not on file. The $85.00 must be on an item or in a Data-tab count."""
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99214", "223.65", "117.60"), "20260301")
    w.remit([visit_denied_line(f"{BILL}C024", carc="16", payer_ctl="PC300B")], "20260415")
    w.remit([visit_paid("CB0000900C017", who=OTHER, payer_ctl="PC900")], "20260803",
            plb=f"PLB*1777777772*20261231*WO:87654321 {BILL}C024*85.00~")
    w.run("r1", today="2026-09-20")
    tb = sum(r["shortfall_cents"] or 0 for r in w.rows("signal='TAKEBACK'"))
    row = w.store.conn.execute("SELECT value FROM meta WHERE key=?", (REVERSALS_NOT_ON_FILE_META,)).fetchone()
    counted = json.loads(row[0]) if row and row[0] else {}
    cents = (counted.get("cents") or 0) + (counted.get("older_cents") or 0)
    print("TAKEBACK items", tb, "not-on-file count", counted)
    assert tb >= 8500 or cents >= 8500, "the payer withheld $85.00 naming C024 and no item or Data-tab count holds it"
    w.store.close()


@pytest.mark.parametrize("ref", [f"WO:87654321 {BILL}C024", f"WO:{BILL}C024"])
def test_money1_a_small_recoupment_of_a_payment_not_on_file_is_on_an_item_or_counted(tmp_path, kind, ref):
    """(verifier) The same, with the claim id among other text and as the whole reference."""
    w = World(tmp_path, kind)
    _recoup_not_on_file(w, ref, "85.00")
    det = w.run("r1", today="2026-09-20")
    tb = sum(r["shortfall_cents"] or 0 for r in w.rows("signal='TAKEBACK'"))
    took = [(t[2], t[3].get("claim")) for t in getattr(det, "plb_took", [])]
    print("ref", ref, "| plb_took", took, "| TAKEBACK", tb, "| not-on-file meta", w.meta(REVERSALS_NOT_ON_FILE_META))
    print("items on the bill:", brief(w.rows(f"patient_control_number LIKE '{BILL}%'")))
    assert tb >= 8500 or _not_on_file_counted(w) >= 8500, "the payer withheld $85.00 naming C024; no item and no Data-tab count holds it"
    w.store.close()


def test_money1_precedent_a_line_reversal_of_a_payment_not_on_file_is_counted(tmp_path, kind):
    """(verifier's control, T-I5) A reversal of $85.00 whose payment is not on file is counted for the Data tab."""
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99214", "223.65", "117.60"), "20260301")
    w.remit([visit_denied_line(f"{BILL}C024", carc="16", payer_ctl="PC300B")], "20260415")
    w.remit([clm(f"{BILL}C024", "22", "-223.65", "-85.00", sv("99214", "-223.65", "-85.00", -1, "CAS*CO*45*-138.65~"), payer_ctl="PC300B")],
            "20260803")
    w.run("r1", today="2026-09-20")
    print("not-on-file meta", w.meta(REVERSALS_NOT_ON_FILE_META))
    assert _not_on_file_counted(w) == 8500
    w.store.close()


def test_money1_a_large_recoupment_of_a_payment_not_on_file_keeps_its_listing(tmp_path, kind):
    """(verifier's control) The same recoupment at $600.00 keeps its own TAKEBACK listing (the $500 floor)."""
    w = World(tmp_path, kind)
    _recoup_not_on_file(w, f"WO:87654321 {BILL}C024", "600.00")
    w.run("r1", today="2026-09-20")
    rows = w.rows("signal='TAKEBACK'")
    print(brief(rows))
    assert [(r["line_key"] is None, r["shortfall_cents"]) for r in rows] == [(True, 60000)]
    w.store.close()


# ================================================================================================ money-2
# A second payer's share taken back in full is on no item, because the primary's payment counts as covering it.
def _primary_and_share(w: World, pcn: str, code: str, charge: str, allowed: str, paid: str, coins: str) -> None:
    co = f"{float(charge) - float(allowed):.2f}"
    w.remit(clean_visits(1, 25, code, charge, allowed), "20260301", **KY)
    w.remit([clm(pcn, "1", charge, paid, sv(code, charge, paid, 1, f"CAS*CO*45*{co}~CAS*PR*2*{coins}~", allowed=allowed), payer_ctl="MC1")],
            "20260415", **KY)
    oa = f"{float(charge) - float(coins):.2f}"
    w.remit([clm(pcn, "2", charge, coins, sv(code, charge, coins, 1, f"CAS*OA*23*{oa}~"), payer_ctl="GP1")], "20260425", **GAP)


def _mentions_takeback(rows: list[dict]) -> list[str]:
    out = []
    for r in rows:
        ev = json.loads(r["evidence"]) if r.get("evidence") else {}
        if ev.get("taken_back") or "taken back" in (r.get("hint") or "") or "taken back" in (r.get("title") or ""):
            out.append(r["signal"])
    return out


def test_money2_a_secondary_share_taken_back_is_listed(tmp_path, kind):
    """(reviewer) Medicare pays $64.00 (coinsurance $16.00); the supplement pays the $16.00 and later recoups it (PLB WO
    naming the claim). The supplement's $16.00 is open again: an item lists it."""
    w = World(tmp_path, kind)
    pcn = "CB0000300C017"
    _primary_and_share(w, pcn, "99214", "223.65", "80.00", "64.00", "16.00")
    w.remit([visit_paid("CB0000900C017", who=OTHER, payer_ctl="GP9", paid="16.00")], "20260803",
            plb=f"PLB*1777777772*20261231*WO:5550001 {pcn}*16.00~", **GAP)
    det = w.run("r1", today="2026-09-20")
    gone = sum(det.standing.get(l["line_key"], 0) for l in det.lines if l["patient_control_number"] == pcn and l.get("claim_status_code") == "2")
    assert gone == 0, "the ledger did not apply the supplement's recoupment"
    tb = w.rows("signal='TAKEBACK'")
    print([(r["line_key"] is not None, r["payer_name"], r["shortfall_cents"]) for r in tb])
    assert sum(r["shortfall_cents"] or 0 for r in tb) >= 1600, "the supplement took back its $16.00 share and no item lists it"
    w.store.close()


@pytest.mark.parametrize("code,charge,allowed,paid,coins", [("99214", "223.65", "80.00", "64.00", "16.00"),
                                                            ("17311", "1400.00", "800.00", "640.00", "160.00")])
def test_money2_a_second_payers_share_taken_back_is_on_an_item(tmp_path, kind, code, charge, allowed, paid, coins):
    """(verifier) The same at two sizes: the amount plays no part."""
    w = World(tmp_path, kind)
    pcn = "CB0000300C017"
    _primary_and_share(w, pcn, code, charge, allowed, paid, coins)
    w.remit([visit_paid("CB0000900C017", who=OTHER, payer_ctl="GP9", paid="16.00")], "20260803",
            plb=f"PLB*1777777772*20261231*WO:5550001 {pcn}*{coins}~", **GAP)
    det = w.run("r1", today="2026-09-20")
    share = [l for l in det.lines if l["patient_control_number"] == pcn and l.get("claim_status_code") == "2"]
    print("share standing", [det.standing.get(l["line_key"], 0) for l in share])
    rows = w.rows(f"patient_control_number='{pcn}'")
    print("items on the claim:", brief(rows))
    assert all(det.standing.get(l["line_key"], 0) == 0 for l in share), "the ledger did not apply the supplement's recoupment"
    listed = sum(r["shortfall_cents"] or 0 for r in w.rows("signal='TAKEBACK' AND status='open'"))
    assert listed >= int(float(coins) * 100) or _mentions_takeback(rows), \
        f"the supplement took back its ${coins} share; no item lists it or names the takeback"
    w.store.close()


def test_money2_control_a_second_payer_paying_twice_and_recouping_once_is_not_listed(tmp_path, kind):
    """(verifier's control) The supplement pays the same share again on a second claim of the bill and recoups that one:
    a double payment recovered, nothing owed, nothing listed."""
    w = World(tmp_path, kind)
    _primary_and_share(w, "CB0000300C017", "99214", "223.65", "80.00", "64.00", "16.00")
    w.remit([clm("CB0000300C024", "2", "223.65", "16.00", sv("99214", "223.65", "16.00", 1, "CAS*OA*23*207.65~"), payer_ctl="GP2")],
            "20260510", **GAP)
    w.remit([visit_paid("CB0000900C017", who=OTHER, payer_ctl="GP9", paid="16.00")], "20260803",
            plb="PLB*1777777772*20261231*WO:5550001 CB0000300C024*16.00~", **GAP)
    w.run("r1", today="2026-09-20")
    rows = w.rows("signal='TAKEBACK'")
    print("control TAKEBACK:", brief(rows))
    assert rows == []
    w.store.close()


# ================================================================================================ money-5
# The ledger's 'deferred' rule skips every later recoupment of a claim after an unrelated balance was carried forward.
def _readjudicated_then_recouped(w: World, carried: str, ref: str, cents: str) -> None:
    w.remit(clean_visits(1, 25, "99214", "223.65", "117.60"), "20260301")
    w.remit([visit_paid(f"{BILL}C024", payer_ctl="PC300B")], "20260520")
    # June: C024 re-adjudicated (reversed and paid the same again); the remittance may also carry $50.00 forward for another account
    w.remit([visit_reversed(f"{BILL}C024", payer_ctl="PC300B"), visit_paid(f"{BILL}C024", payer_ctl="PC300C")], "20260610", plb=carried)
    # August: the payer recoups C024's payment, naming the claim, from a check that pays another patient
    w.remit([visit_paid("CB0000900C017", who=OTHER, payer_ctl="PC900")], "20260803", plb=f"PLB*1777777772*20261231*{ref}*{cents}~")


@pytest.mark.parametrize("carried", ["", "PLB*1777777772*20261231*FB:ACCT77001*-50.00~"])
def test_money5_a_later_recoupment_is_applied_after_an_unrelated_carried_balance(tmp_path, kind, carried):
    """(reviewer)"""
    w = World(tmp_path, kind)
    _readjudicated_then_recouped(w, carried, f"WO:87654321 {BILL}C024", "117.60")
    det = w.run("r1", today="2026-09-20")
    left = left_on(det, f"{BILL}C024")
    tb = w.rows("signal='TAKEBACK'")
    print("carried" if carried else "control", "standing", left, [(r["line_key"] is not None, r["shortfall_cents"]) for r in tb])
    assert left == 0, f"the August recoupment of C024 was skipped: ${left/100:.2f} still stands as paid"
    w.store.close()


@pytest.mark.parametrize("ref", [f"WO:87654321 {BILL}C024", f"WO:{BILL}C024"])
@pytest.mark.parametrize("carried", ["", "PLB*1777777772*20261231*FB:ACCT77001*-50.00~"])
@pytest.mark.parametrize("cents", ["117.60", "40.00"])
def test_money5_a_later_recoupment_is_taken_after_an_unrelated_carried_balance(tmp_path, kind, carried, cents, ref):
    """(verifier) The whole recoupment and a partial one, the claim id among other text and as the whole reference."""
    w = World(tmp_path, kind)
    _readjudicated_then_recouped(w, carried, ref, cents)
    det = w.run("r1", today="2026-09-20")
    left = left_on(det, f"{BILL}C024")
    print("carried" if carried else "control", cents, "standing", left, brief(w.rows("signal='TAKEBACK'")))
    assert left == 11760 - int(round(float(cents) * 100)), f"the August recoupment of ${cents} naming C024 was skipped: ${left/100:.2f} stands"
    w.store.close()


def test_money5_control_a_reversals_carried_balance_recovered_naming_the_claim_is_taken_once(tmp_path, kind):
    """(verifier's control: the rule's own purpose) June reverses C024 with no re-payment and carries the $117.60 forward
    under its own number; August recovers it naming C024: taken once, standing 0, nothing taken twice."""
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99214", "223.65", "117.60"), "20260301")
    w.remit([visit_paid(f"{BILL}C024", payer_ctl="PC300B")], "20260520")
    w.remit([visit_reversed(f"{BILL}C024", payer_ctl="PC300B")], "20260610", plb="PLB*1777777772*20261231*FB:CARRY0001*-117.60~")
    w.remit([visit_paid("CB0000900C017", who=OTHER, payer_ctl="PC900")], "20260803",
            plb=f"PLB*1777777772*20261231*WO:87654321 {BILL}C024*117.60~")
    det = w.run("r1", today="2026-09-20")
    took = [(t[2], t[3].get("claim")) for t in getattr(det, "plb_took", [])]
    print("control: standing", left_on(det, f"{BILL}C024"), "plb took", took)
    assert left_on(det, f"{BILL}C024") == 0 and took == []
    w.store.close()


# ================================================================================================ money-6
# A WO of $500 or more naming a claim loses its own listing when the claim's payments on file are smaller.
def test_money6_every_dollar_a_recoupment_withheld_is_on_an_item(tmp_path, kind):
    """(reviewer) $600.00 withheld naming C024, whose only payment on file is $117.60: $482.40 must not vanish."""
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99214", "223.65", "117.60"), "20260301")
    w.remit([visit_paid(f"{BILL}C024", payer_ctl="PC300B")], "20260520")
    w.remit([visit_paid("CB0000900C017", who=OTHER, payer_ctl="PC900")], "20260803",
            plb=f"PLB*1777777772*20261231*WO:87654321 {BILL}C024*600.00~")
    w.run("r1", today="2026-09-20")
    tb = w.rows("signal='TAKEBACK'")
    listed = sum(r["shortfall_cents"] or 0 for r in tb)
    print([(r["line_key"] is not None, r["shortfall_cents"], r["title"]) for r in tb])
    assert listed >= 60000, (f"the payer withheld $600.00 naming {BILL}C024; the TAKEBACK items list ${listed/100:.2f}: "
                             f"${(60000 - listed)/100:.2f} is on no item")
    w.store.close()


@pytest.mark.parametrize("on_file", ["paid", "denied"])
def test_money6_every_dollar_a_large_recoupment_withheld_is_on_an_item(tmp_path, kind, on_file):
    """(verifier) The same with the claim paid on file and with only its denial on file."""
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99214", "223.65", "117.60"), "20260301")
    w.remit([visit_paid(f"{BILL}C024", payer_ctl="PC300B") if on_file == "paid" else visit_denied_line(f"{BILL}C024", payer_ctl="PC300B")],
            "20260520")
    w.remit([visit_paid("CB0000900C017", who=OTHER, payer_ctl="PC900")], "20260803",
            plb=f"PLB*1777777772*20261231*WO:87654321 {BILL}C024*600.00~")
    w.run("r1", today="2026-09-20")
    tb = w.rows("signal='TAKEBACK'")
    listed = sum(r["shortfall_cents"] or 0 for r in tb)
    print(on_file, brief(tb))
    assert listed >= 60000, f"$600.00 withheld naming {BILL}C024 ({on_file} on file); TAKEBACK items list ${listed/100:.2f}"
    w.store.close()


# ================================================================================================ hosts-4
# A TAKEBACK item's proof names a different reference depending on the order the remittances were read.
_FIRST = ("20260803", "PC900", "CB0000900C017", "PLB*1777777772*20261231*WO:AAA111 CB0000300C024*58.80~")
_SECOND = ("20260810", "PC901", "CB0000901C017", "PLB*1777777772*20261231*WO:BBB222 CB0000300C024*58.80~")


def _takeback_reference(tmp_path, kind: str, order: tuple) -> str | None:
    """Claim C024 is paid $117.60; the payer then takes it back in two equal parts from two later checks, each PLB entry
    naming the claim under its own reference. The remittances are read in `order`."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99214", "223.65", "117.60"), "20260301")
    w.remit([visit_paid("CB0000300C024", payer_ctl="PC300B")], "20260520")
    for day, ctl, other, plb in order:
        w.remit([visit_paid(other, who=OTHER, payer_ctl=ctl)], day, plb=plb)
    w.run("r1")
    rows = w.rows("signal='TAKEBACK' AND patient_control_number='CB0000300C024'")
    w.store.close()
    assert rows, "the take-back of C024 is listed"
    return json.loads(rows[0]["evidence"]).get("reference")


def test_hosts4_a_takeback_names_the_same_reference_whatever_order_its_remittances_were_read_in(tmp_path, kind):
    a = _takeback_reference(tmp_path / "a", kind, (_FIRST, _SECOND))
    b = _takeback_reference(tmp_path / "b", kind, (_SECOND, _FIRST))
    assert a == b, f"the item's proof names {a!r} when the first check is read first and {b!r} when the second is"


# ================================================================================================ money-8 and money-9
# fix6's 'paid first elsewhere' (states.paid_first_elsewhere): the fixtures of tests/test_paid_first_elsewhere.py.
from tests import ema_fixtures as F  # noqa: E402
from tests import test_ema_wiring as EW  # noqa: E402
from tests.test_paid_first_elsewhere import (STATE, TODAY, _by_claim, _carry, _claim, _ema, _env, _history, _host,  # noqa: E402,F401
                                             _svc)


@pytest.mark.parametrize("reason", ["WO", "FB", "J1"])
def test_money8_a_takeback_naming_the_claim_keeps_the_denial_work_whatever_its_reason(tmp_path, kind, reason, monkeypatch):
    """(reviewer) A recoupment naming claim 31 after the first payer's payment keeps the Medicaid denial as work whether
    the payer wrote it WO, FB or J1 (the ledger reads all three as takebacks naming a claim)."""
    s, lg = _host(tmp_path, kind, monkeypatch)
    try:
        _history(s)
        paid_on = _claim(s, 31, payer=STATE, carc="96")          # the state's Medicaid denies claim 31
        rows, ar = _ema(31)                                       # the first payer paid $92.18 on a paper check before
        rows.append(_carry(dt.date(2026, 5, 25)))
        EW.load(s, lg, tmp_path, rows, ar)
        # after the payment, a recoupment naming claim 31 on a later remittance (PLB)
        tk = EW.remit(s, trace="777000931", paid_on=(paid_on + dt.timedelta(days=10)).isoformat(), payer=STATE,
                      claims=[{"pcn": "ZZ09X0001C001", "lines": [(_svc(1).isoformat(), "99214", 150, 70, [("CO", "45", 80)])]}])
        s.conn.execute("INSERT INTO plb (transaction_key, reason_code, reference, amount_cents, payer_name, payment_date, trace_number) "
                       "VALUES (?,?,?,?,?,?,?)", (tk, reason, f"{reason}:1234567 {F.claim_id(31)}", 9218, STATE,
                                                  (paid_on + dt.timedelta(days=10)).isoformat(), "777000931"))
        s.conn.commit()
        EW.run(s, today=TODAY)
        f = _by_claim(s)[F.claim_id(31)]
        st = states.state_of(s.conn, f, today=TODAY)
        print(reason, st["work_state"], st.get("rule"))
        assert st["work_state"] == "work_now", f"a {reason} naming the claim after the first payment left it {st['work_state']} ({st.get('rule')})"
    finally:
        lg.close()
        s.close()


def test_money9_a_medicaid_denial_is_not_done_while_the_first_payer_paid_under_medicaids_rate(tmp_path, kind, monkeypatch):
    """(reviewer) Medicaid's rate is $70.00 and the first payer paid $50.00 (over half, under the rate): up to $20.00 is
    still Medicaid's to pay, and EMA shows $70.00 open on the line. The item is not done."""
    s, lg = _host(tmp_path, kind, monkeypatch)
    try:
        _history(s)                                   # Medicaid's usual 99214: $70.00
        _claim(s, 44, payer=STATE, carc="96")         # the state's Medicaid denies its copy of claim 44
        rows, ar = _ema(44, first_paid=50, share="open")   # the first payer paid $50.00 on a paper check first; $70.00 open
        rows.append(_carry(dt.date(2026, 5, 25)))
        EW.load(s, lg, tmp_path, rows, ar)
        EW.run(s, today=TODAY)
        f = _by_claim(s)[F.claim_id(44)]
        st = states.state_of(s.conn, f, today=TODAY)
        print(f["expected_cents"], f["shortfall_cents"], st["work_state"], st.get("rule"), (st.get("proof") or {}).get("text"))
        assert st["work_state"] != "done", ("Medicaid's $70.00 rate less the first payer's $50.00 leaves up to $20.00 owed, yet the "
                                            f"denial is done: {(st.get('proof') or {}).get('text')}")
    finally:
        lg.close()
        s.close()


# ================================================================================================ answers-5 (paid_proof)
# A claim-level never-answered item is not put done by a payment of one of its services made before its send.
def test_answers5_the_exports_item_for_a_resend_with_an_added_line_is_not_put_done_by_the_original_lines_payment(tmp_path, kind):
    """(reviewer) Anthem paid bill CB0000L04's C017 (99214, August 20). The bill went out again as C024 (September 1) with
    the 99214 and a forgotten 17110; EMA's weekly exports list C024 and nothing answers it by November 10. The item is
    not done by the 99214's payment made before C024 was even sent: the 17110's $80 stays work."""
    from tests.test_837_sends import _remits, _store, _visits, answer, clm837, send
    from tests.test_features import claim, svc
    from tests.test_workable import ema
    s = _store(tmp_path, kind)
    rates = [claim(600 + i, svc("17110", "200.00", "80.00", "80.00", "CAS*CO*45*120.00~")) for i in range(25)]
    v1 = [("99214", "223.65", "20260730")]
    v2 = v1 + [("17110", "200.00", "20260730")]
    send(s, tmp_path, [clm837("CB0000L04C017", v1)], "20260801")
    _remits(s, tmp_path, [answer("CB0000L04C017", dos="20260730")], "20260820")
    send(s, tmp_path, [clm837("CB0000L04C024", v2, freq="7")], "20260901")
    ema(s.conn, "CB0000L04C024", service_date="2026-07-30", bill_created="2026-07-31")
    ema(s.conn, "CB0000L04C024", cpt="17110", charges_cents=20000, service_date="2026-07-30", bill_created="2026-07-31")
    ema(s.conn, "CB0000L04C024", family="claim_status", claim_status="Submitted", claim_submission_date="2026-09-01",
        service_date="2026-07-30")
    s.conn.commit()
    _remits(s, tmp_path, rates + _visits(), "20261110")
    Detector(s, run_id="t1").run()
    f = dict(s.query("SELECT signal, work_state, state_basis, shortfall_cents, evidence FROM findings "
                     "WHERE patient_control_number='CB0000L04C024'")[0])
    print(f["signal"], f["work_state"], "|", f["state_basis"], "|", f["shortfall_cents"], json.loads(f["evidence"])["lines"])
    assert f["work_state"] != "done", f"the 17110 was never answered, yet the item is done: {f['state_basis']}"
    s.close()


# ================================================================================================ F1's own edges of each fix
def test_money1_the_data_tab_names_a_recoupment_of_a_payment_not_on_file(tmp_path, kind):
    """The $85.00 recoupment of money-1 is on the Data tab beside the reversals of payments not on file: one claim, the
    amount, the entry, and words that say a recoupment took it."""
    from remitwatch.insights import data_health
    w = World(tmp_path, kind)
    _recoup_not_on_file(w, f"WO:87654321 {BILL}C024", "85.00")
    w.run("r1", today="2026-09-20")
    got = {h["key"]: h for h in data_health(w.store.conn, "2026-08-03", "2026-09-20")}.get("reversal_not_on_file")
    assert got and not got["ok"], got
    assert (got["claims"], got["cents"], got["lines"], got["entries"], got["recouped_claims"], got["recouped_cents"]) == (1, 8500, 0, 1, 1, 8500)
    assert "recoupment the payer withheld from a later check ($85.00)" in got["detail"], got["detail"]
    w.store.close()


def test_money1_a_recoupment_of_a_payment_made_before_the_archive_begins_is_counted_as_expected(tmp_path, kind):
    """The claim's service is dated before the payer's first remittance on file: the payment it recoups was made before
    the archive begins. It is counted with the older ones (expected), not as a missing remittance."""
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99214", "223.65", "117.60"), "20260301")
    w.remit([visit_denied_line(f"{BILL}C024", carc="16", payer_ctl="PC300B", dos="20251110")], "20260415")
    w.remit([visit_paid("CB0000900C017", who=OTHER, payer_ctl="PC900")], "20260803",
            plb=f"PLB*1777777772*20261231*WO:87654321 {BILL}C024*85.00~")
    w.run("r1", today="2026-09-20")
    m = w.meta(REVERSALS_NOT_ON_FILE_META)
    assert (m["cents"], m["older_cents"], m["older_recouped_cents"], m["older_entries"], m["older_claims"]) == (0, 8500, 8500, 1, 1), m
    w.store.close()


def test_money1_a_recoupment_the_ledger_applied_is_not_counted(tmp_path, kind):
    """Control: a recoupment that took a payment on file is no gap."""
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99214", "223.65", "117.60"), "20260301")
    w.remit([visit_paid(f"{BILL}C024", payer_ctl="PC300B")], "20260520")
    w.remit([visit_paid("CB0000900C017", who=OTHER, payer_ctl="PC900")], "20260803",
            plb=f"PLB*1777777772*20261231*WO:87654321 {BILL}C024*117.60~")
    det = w.run("r1", today="2026-09-20")
    assert left_on(det, f"{BILL}C024") == 0
    m = w.meta(REVERSALS_NOT_ON_FILE_META)
    assert m["cents"] == 0 and m["older_cents"] == 0 and m["entries"] == 0, m
    w.store.close()


def test_money5_a_recoupment_larger_than_the_carried_balance_takes_the_rest_from_the_repayment(tmp_path, kind):
    """June: the payer reverses C024's $117.60 and pays it again at $40.00; the $77.60 it is owed is carried forward under
    its own number (FB naming no claim). August: it withholds $100.00 naming C024. $77.60 of it is that balance (the
    reversal already took it); the other $22.40 comes off the $40.00 re-payment."""
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99214", "223.65", "117.60"), "20260301")
    w.remit([visit_paid(f"{BILL}C024", payer_ctl="PC300B")], "20260520")
    w.remit([visit_reversed(f"{BILL}C024", payer_ctl="PC300B"), visit_paid(f"{BILL}C024", paid="40.00", payer_ctl="PC300C")], "20260610",
            plb="PLB*1777777772*20261231*FB:900000000000001*-77.60~")
    w.remit([visit_paid("CB0000900C017", who=OTHER, payer_ctl="PC900")], "20260803",
            plb=f"PLB*1777777772*20261231*WO:87654321 {BILL}C024*100.00~")
    det = w.run("r1", today="2026-09-20")
    assert left_on(det, f"{BILL}C024") == 4000 - 2240
    w.store.close()


def test_money5_a_carried_balance_excuses_one_recovery_only(tmp_path, kind):
    """The same June, then two recoupments of $77.60 naming C024 (August 3 and 10): the first recovers the carried
    balance; the second takes the $40.00 re-payment, and the $37.60 it could not apply is counted for the Data tab."""
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99214", "223.65", "117.60"), "20260301")
    w.remit([visit_paid(f"{BILL}C024", payer_ctl="PC300B")], "20260520")
    w.remit([visit_reversed(f"{BILL}C024", payer_ctl="PC300B"), visit_paid(f"{BILL}C024", paid="40.00", payer_ctl="PC300C")], "20260610",
            plb="PLB*1777777772*20261231*FB:900000000000001*-77.60~")
    w.remit([visit_paid("CB0000900C017", who=OTHER, payer_ctl="PC900")], "20260803",
            plb=f"PLB*1777777772*20261231*WO:87654321 {BILL}C024*77.60~")
    w.remit([visit_paid("CB0000901C017", who=OTHER, payer_ctl="PC901")], "20260810",
            plb=f"PLB*1777777772*20261231*WO:87654322 {BILL}C024*77.60~")
    det = w.run("r1", today="2026-09-20")
    assert left_on(det, f"{BILL}C024") == 0
    m = w.meta(REVERSALS_NOT_ON_FILE_META)
    assert m["recouped_cents"] + m["older_recouped_cents"] == 3760, m
    w.store.close()


def test_money2_a_share_another_second_payer_answered_since_is_not_listed(tmp_path, kind):
    """The supplement pays its $16.00 share, then recoups it; meanwhile another supplement (the right one) answered the
    service as second payer: the right payer was billed, so no item lists the first supplement's takeback."""
    w = World(tmp_path, kind)
    pcn = "CB0000300C017"
    _primary_and_share(w, pcn, "99214", "223.65", "80.00", "64.00", "16.00")
    w.remit([clm(pcn, "2", "223.65", "16.00", sv("99214", "223.65", "16.00", 1, "CAS*OA*23*207.65~"), payer_ctl="GQ1")], "20260601",
            payer="OTHERGAP SUPPLEMENT", payer_id="OGAP1")
    w.remit([visit_paid("CB0000900C017", who=OTHER, payer_ctl="GP9", paid="16.00")], "20260803",
            plb=f"PLB*1777777772*20261231*WO:5550001 {pcn}*16.00~", **GAP)
    w.run("r1", today="2026-09-20")
    assert w.rows("signal='TAKEBACK'") == [], brief(w.rows("signal='TAKEBACK'"))
    w.store.close()


def test_money2_the_share_item_says_who_took_it_and_what_to_do(tmp_path, kind):
    """The item of a second payer's share taken back says so: bill the patient, or dispute the takeback; its evidence marks
    the second payer."""
    w = World(tmp_path, kind)
    pcn = "CB0000300C017"
    _primary_and_share(w, pcn, "99214", "223.65", "80.00", "64.00", "16.00")
    w.remit([visit_paid("CB0000900C017", who=OTHER, payer_ctl="GP9", paid="16.00")], "20260803",
            plb=f"PLB*1777777772*20261231*WO:5550001 {pcn}*16.00~", **GAP)
    w.run("r1", today="2026-09-20")
    (r,) = w.rows("signal='TAKEBACK'")
    ev = json.loads(r["evidence"])
    assert ev["second_payer"] is True and ev["taken_back"]["cents"] == 1600
    assert "bill the patient, or dispute the takeback" in r["title"] and "second payer" in r["title"], r["title"]
    w.store.close()


def test_money6_the_rest_of_two_recoupments_is_on_their_own_listings(tmp_path, kind):
    """$600.00 and $550.00 withheld naming C024, whose only payment on file is $117.60: the service's item lists the
    $117.60, the $600.00 recoupment its whole amount and the $550.00 one the $432.40 left. Every dollar is on an item."""
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99214", "223.65", "117.60"), "20260301")
    w.remit([visit_paid(f"{BILL}C024", payer_ctl="PC300B")], "20260520")
    w.remit([visit_paid("CB0000900C017", who=OTHER, payer_ctl="PC900")], "20260803",
            plb=f"PLB*1777777772*20261231*WO:87654321 {BILL}C024*600.00~")
    w.remit([visit_paid("CB0000901C017", who=OTHER, payer_ctl="PC901")], "20260810",
            plb=f"PLB*1777777772*20261231*WO:87654322 {BILL}C024*550.00~")
    w.run("r1", today="2026-09-20")
    tb = w.rows("signal='TAKEBACK'")
    got = sorted((r["line_key"] is None, r["shortfall_cents"]) for r in tb)
    assert got == [(False, 11760), (True, 43240), (True, 60000)], brief(tb)
    part = next(r for r in tb if r["shortfall_cents"] == 43240)
    assert json.loads(part["evidence"])["not_applied_cents"] == 43240 and "$432.40 of it takes back no payment on file" in part["title"]
    w.store.close()


def test_hosts4_equal_recoupments_on_one_day_name_one_reference_whatever_the_order(tmp_path, kind):
    """Two equal recoupments ($58.80 each) of one $117.60 payment on two checks of the same day, under two references:
    the proof names the same reference whichever check was read first."""
    first = ("20260803", "PC900", "CB0000900C017", "PLB*1777777772*20261231*WO:AAA111 CB0000300C024*58.80~")
    second = ("20260803", "PC901", "CB0000901C017", "PLB*1777777772*20261231*WO:BBB222 CB0000300C024*58.80~")
    a = _takeback_reference(tmp_path / "a", kind, (first, second))
    b = _takeback_reference(tmp_path / "b", kind, (second, first))
    assert a == b and a in ("AAA111 CB0000300C024", "BBB222 CB0000300C024"), (a, b)


@pytest.mark.parametrize("reason", ["CS", "72"])
def test_money8_a_recoupment_its_own_adjustment_cancels_leaves_the_denial_done(tmp_path, kind, reason, monkeypatch):
    """A WO naming claim 31 and, in the same remittance, a CS or 72 of the same amount naming it: they cancel (the ledger
    nets them), no money was taken, and the Medicaid denial stays done on the first payer's payment."""
    s, lg = _host(tmp_path, kind, monkeypatch)
    try:
        _history(s)
        paid_on = _claim(s, 31, payer=STATE, carc="96")
        rows, ar = _ema(31)
        rows.append(_carry(dt.date(2026, 5, 25)))
        EW.load(s, lg, tmp_path, rows, ar)
        on = (paid_on + dt.timedelta(days=10)).isoformat()
        tk = EW.remit(s, trace="777000931", paid_on=on, payer=STATE,
                      claims=[{"pcn": "ZZ09X0001C001", "lines": [(_svc(1).isoformat(), "99214", 150, 70, [("CO", "45", 80)])]}])
        for code, cents in (("WO", 9218), (reason, -9218)):
            s.conn.execute("INSERT INTO plb (transaction_key, reason_code, reference, amount_cents, payer_name, payment_date, trace_number) "
                           "VALUES (?,?,?,?,?,?,?)", (tk, code, f"{code}:1234567 {F.claim_id(31)}", cents, STATE, on, "777000931"))
        s.conn.commit()
        EW.run(s, today=TODAY)
        st = states.state_of(s.conn, _by_claim(s)[F.claim_id(31)], today=TODAY)
        assert (st["work_state"], st.get("rule")) == ("done", "paid_first_other_payer"), st
    finally:
        lg.close()
        s.close()


def test_money9_a_first_payment_under_medicaids_rate_with_nothing_open_in_ema_is_done(tmp_path, kind, monkeypatch):
    """The first payer paid $50.00 of Medicaid's $70.00 and EMA wrote the rest off (nothing open): nothing is left of
    Medicaid's share to recover, so the denial is done as before."""
    s, lg = _host(tmp_path, kind, monkeypatch)
    try:
        _history(s)
        _claim(s, 44, payer=STATE, carc="96")
        rows, ar = _ema(44, first_paid=50)                 # the rest written off (CO-B7): $0.00 open
        rows.append(_carry(dt.date(2026, 5, 25)))
        EW.load(s, lg, tmp_path, rows, ar)
        EW.run(s, today=TODAY)
        st = states.state_of(s.conn, _by_claim(s)[F.claim_id(44)], today=TODAY)
        assert (st["work_state"], st.get("rule")) == ("done", "paid_first_other_payer"), st
    finally:
        lg.close()
        s.close()


def test_money9_the_open_denial_names_what_medicaid_still_owes(tmp_path, kind, monkeypatch):
    """The money-9 case: its basis names the first payment and the $20.00 still Medicaid's to pay."""
    s, lg = _host(tmp_path, kind, monkeypatch)
    try:
        _history(s)
        _claim(s, 44, payer=STATE, carc="96")
        rows, ar = _ema(44, first_paid=50, share="open")
        rows.append(_carry(dt.date(2026, 5, 25)))
        EW.load(s, lg, tmp_path, rows, ar)
        EW.run(s, today=TODAY)
        st = states.state_of(s.conn, _by_claim(s)[F.claim_id(44)], today=TODAY)
        assert st["work_state"] == "work_now", st
        assert "Medicaid owes up to $20.00 as second payer ($70.00 open in EMA)" in st["state_basis"], st["state_basis"]
    finally:
        lg.close()
        s.close()


# ================================================================================================ residual: PLB/J1 in paid_proof
@pytest.mark.parametrize("reason", ["WO", "FB", "J1"])
def test_a_payment_a_recoupment_took_back_proves_nothing_paid(tmp_path, kind, reason):
    """C017's visit is denied (missing information); the corrected claim C024 is paid $117.60; the payer then answers the
    service again on C031 (a duplicate denial), and in August withholds the $117.60 naming C024 from a check that pays
    another patient. The ledger takes C024's payment back. No item of the service may be done on that payment: the
    takeback left it unpaid (next_fixes section 4: paid_proof saw reversals, not PLB or J1 takebacks)."""
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99214", "223.65", "117.60"), "20260301")
    w.remit([visit_denied_line(f"{BILL}C017", carc="16")], "20260415")
    w.remit([visit_paid(f"{BILL}C024", payer_ctl="PC300B")], "20260520")
    w.remit([visit_denied_line(f"{BILL}C031", carc="18", payer_ctl="PC300C")], "20260615")
    w.remit([visit_paid("CB0000900C017", who=OTHER, payer_ctl="PC900")], "20260803",
            plb=f"PLB*1777777772*20261231*{reason}:87654321 {BILL}C024*117.60~")
    det = w.run("r1", today="2026-09-20")
    assert left_on(det, f"{BILL}C024") == 0, "the ledger did not take the payment back"
    rows = w.rows(f"patient_control_number LIKE '{BILL}%' AND COALESCE(stale, 0) = 0")
    print(brief(rows), [(r["patient_control_number"], r["work_state"], r["state_basis"]) for r in rows])
    done = [(r["patient_control_number"], r["signal"], r["state_basis"]) for r in rows if r["work_state"] == "done"]
    assert not done, f"done on a payment the payer took back: {done}"
    assert any(r["work_state"] == "work_now" for r in rows), "the service the takeback left unpaid is on no work item"
    w.store.close()


MCD = {"payer": "KY MEDICAID", "payer_id": "MCDKY"}


@pytest.mark.parametrize("reason", ["WO", "J1"])
def test_a_takeback_on_an_item_of_its_own_is_not_worked_again_on_another_payers_denial(tmp_path, kind, reason):
    """(hand trace of the measurement) Medicaid denies the whole of claim C017 in April; Medicare, the first payer, pays
    its 99214 $117.60 in May, then withholds the $117.60 naming C017 from a June check that pays another patient. The
    takeback is a TAKEBACK item of Medicare's line (find out why, then dispute or bill the right payer). Medicaid's
    denial still rests on Medicare's answer: listing it as work (or as lost to its deadline) too would put the same
    service's money on two items. The ledger marks the takeback as listed on its own item (ledger_taken.listed) and the
    denial's proof names it."""
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99214", "223.65", "117.60"), "20260301", **KY)
    w.remit([clm(f"{BILL}C017", "4", "223.65", "0.00", sv("99214", "223.65", "0.00", 1, "CAS*CO*22*223.65~"), payer_ctl="PC300D")],
            "20260415", **MCD)
    w.remit([visit_paid(f"{BILL}C017", payer_ctl="PC300M")], "20260520", **KY)
    w.remit([visit_paid("CB0000900C017", who=OTHER, payer_ctl="PC900")], "20260615",
            plb=f"PLB*1777777772*20261231*{reason}:87654321 {BILL}C017*117.60~", **KY)
    det = w.run("r1", today="2026-09-20")
    assert left_on(det, f"{BILL}C017") == 0, "the ledger did not take the payment back"
    rows = w.rows(f"patient_control_number LIKE '{BILL}%' AND COALESCE(stale, 0) = 0")
    print(brief(rows), [(r["signal"], r["payer_group"], r["work_state"], r["state_basis"]) for r in rows])
    tb = [r for r in rows if r["signal"] == "TAKEBACK"]
    assert len(tb) == 1 and tb[0]["work_state"] == "work_now", "Medicare's takeback is its own work item"
    mcd = [r for r in rows if r["signal"] != "TAKEBACK" and "Medicaid" in (r["payer_group"] or "")]
    assert mcd, "Medicaid's denial is an item"
    for r in mcd:
        assert r["work_state"] == "done", f"Medicaid's denial is listed again ({r['work_state']}): {r['state_basis']}"
        assert "took" in (r["state_basis"] or "") and "back" in (r["state_basis"] or ""), r["state_basis"]
    listed = dict(w.store.conn.execute("SELECT line_key, listed FROM ledger_taken").fetchall())
    assert listed.get(tb[0]["line_key"]) == 1, f"the takeback is marked as listed on its own item: {listed}"
    w.store.close()


@pytest.mark.parametrize("wo,listing", [("517.60", True), ("467.60", False)])
def test_a_recoupment_listed_on_its_own_carries_what_it_took_from_a_claim_in_part(tmp_path, kind, wo, listing):
    """Medicaid denies the whole of claim C017 in April; Medicare, the first payer, pays its 99214 $117.60 and its 11102
    $450.00 in May, then withholds `wo` naming C017 from an August check that pays another patient. The ledger takes the
    11102's $450.00 and part of the 99214's payment: C017 is a claim the recoupment took from only in part, so there is no
    TAKEBACK line item, and a recoupment of $500 or more keeps its own listing at its whole amount (match it to the
    payer's letter). That listing carries what it took: Medicaid's denial still rests on Medicare's payments (done, the
    takeback named), never the same money worked twice. A recoupment under $500 has no listing of its own: the takeback
    is on no item, and Medicaid's denial is not done on the payments it took."""
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99214", "223.65", "117.60"), "20260301", **KY)
    w.remit([clm(f"{BILL}C017", "4", "723.65", "0.00", sv("99214", "223.65", "0.00", 1, "CAS*CO*22*223.65~")
                 + sv("11102", "500.00", "0.00", 1, "CAS*CO*22*500.00~"), payer_ctl="PC300D")], "20260415", **MCD)
    w.remit([clm(f"{BILL}C017", "1", "723.65", "567.60", sv("99214", "223.65", "117.60", 1, "CAS*CO*45*106.05~", allowed="117.60")
                 + sv("11102", "500.00", "450.00", 1, "CAS*CO*45*50.00~", allowed="450.00"), payer_ctl="PC300M")], "20260520", **KY)
    w.remit([visit_paid("CB0000900C017", who=OTHER, payer_ctl="PC900")], "20260803",
            plb=f"PLB*1777777772*20261231*WO:87654321 {BILL}C017*{wo}~", **KY)
    w.run("r1", today="2026-09-20")
    rows = w.rows(f"COALESCE(stale, 0) = 0 AND (patient_control_number LIKE '{BILL}%' OR signal = 'TAKEBACK')")
    print(wo, brief(rows), [(r["signal"], r["work_state"], r["state_basis"]) for r in rows])
    assert not [r for r in rows if r["signal"] == "TAKEBACK" and r["line_key"]], "a claim taken from in part has no TAKEBACK line item"
    own = [r for r in rows if r["signal"] == "TAKEBACK" and not r["line_key"]]
    assert bool(own) is listing, brief(own)
    taken = {r[0] for r in w.store.conn.execute("SELECT listed FROM ledger_taken")}
    assert taken == ({1} if listing else {0}), taken
    mcd = [r for r in rows if r["signal"] != "TAKEBACK" and "Medicaid" in (r["payer_group"] or "")]
    assert mcd, "Medicaid's denial is an item"
    if listing:
        assert all(r["work_state"] == "done" for r in mcd), [(r["work_state"], r["state_basis"]) for r in mcd]
        assert all("worked on its own takeback item" in (r["state_basis"] or "") for r in mcd), [r["state_basis"] for r in mcd]
    else:
        assert all(r["work_state"] != "done" for r in mcd), [(r["work_state"], r["state_basis"]) for r in mcd]
    w.store.close()


def test_a_takeback_no_item_carries_leaves_the_payment_unmarked(tmp_path, kind):
    """The control of the test above: the payer answers the service again after its payment (a duplicate denial), so no
    TAKEBACK item carries the takeback (that answer is the work): the ledger leaves it unmarked and the payment proves
    nothing paid (test_a_payment_a_recoupment_took_back_proves_nothing_paid)."""
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99214", "223.65", "117.60"), "20260301")
    w.remit([visit_paid(f"{BILL}C024", payer_ctl="PC300B")], "20260520")
    w.remit([visit_denied_line(f"{BILL}C031", carc="18", payer_ctl="PC300C")], "20260615")
    w.remit([visit_paid("CB0000900C017", who=OTHER, payer_ctl="PC900")], "20260803",
            plb=f"PLB*1777777772*20261231*WO:87654321 {BILL}C024*117.60~")
    w.run("r1", today="2026-09-20")
    rows = w.rows(f"patient_control_number LIKE '{BILL}%' AND COALESCE(stale, 0) = 0")
    assert not [r for r in rows if r["signal"] == "TAKEBACK"], "the duplicate denial is the work, not a TAKEBACK item"
    taken = w.store.conn.execute("SELECT taken_cents, listed FROM ledger_taken").fetchall()
    assert [tuple(t) for t in taken] == [(11760, 0)], taken
    w.store.close()


def _states_store(tmp_path, kind: str):
    """A store of either kind with EMA's tables and the derived tables, as tests/test_states.py's store."""
    from remitwatch import detect as D, ema_reports as er
    from tests import test_states as TS
    if kind == "file":
        return TS.new_store(str(tmp_path / "store.sqlite"))
    s = Store(":memory:", compact=True)
    er.ensure_schema(s.conn)
    D.ensure_schema(s.conn)
    s.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('ema:key_salt', ?)", (TS.SALT_HEX,))
    s.conn.commit()
    return s


@pytest.mark.parametrize("taken,done", [(0, True), (3000, True), (7000, False), (11000, False)])
def test_paid_proof_counts_only_what_the_ledger_left_of_a_later_payment(tmp_path, kind, taken, done):
    """A denial of 99213 ($90.00 expected), then a later payment of the same line ($110.00). The ledger found a recoupment
    took `taken` of that payment (ledger_taken): what is left must still reach half of the expected amount to prove the
    denial paid; a payment taken back in full proves nothing."""
    from tests import test_states as TS
    s = _states_store(tmp_path, kind)
    try:
        tk0 = TS.denied(s)
        TS.item(s, line_key=TS.line_key(s, tk0), claim_key=f"{tk0}-0", transaction_key=tk0)
        tk1 = TS.remit(s, trace="T1", paid_on="2026-04-10", claims=[{"pcn": F.claim_id(1), "pccn": "PCN-A", "received": "2026-03-05",
                                                                    "lines": [TS.ln("99213", 150, 110, [("CO", "45", 40)], lcn="1")]}])
        s.conn.execute("CREATE TABLE IF NOT EXISTS ledger_taken (line_key TEXT PRIMARY KEY, taken_cents INTEGER, taken_on TEXT, "
                       "standing_cents INTEGER)")
        if taken:
            s.conn.execute("INSERT INTO ledger_taken VALUES (?,?,?,?)", (TS.line_key(s, tk1), taken, "2026-04-20", 11000 - taken))
        s.conn.commit()
        st = TS.state(s)
        print(taken, st["work_state"], st.get("rule"), st.get("state_basis"))
        assert (st["work_state"] == "done") is done, (taken, st["work_state"], st.get("state_basis"))
        if done:
            assert st["proof"]["cents"] == 11000 - taken, st["proof"]
    finally:
        s.close()


# ================================================================================================ residual: reversals under another company name
UMR_PAY = {"payer": "UNITED MEDICAL RESOURCES", "payer_id": "UMR01"}


@pytest.mark.parametrize("rev_payer,rev_ctl,paired", [
    ({"payer": "WAUSAU", "payer_id": "WAUS1"}, "PC300B", True),       # UMR's former name, the payer's own claim number
    ({"payer": "WAUSAU", "payer_id": "WAUS1"}, "PC300X", False),      # another claim number: another version's payment
    ({"payer": "AETNA", "payer_id": "AETN1"}, "PC300B", False),       # another company, whatever its claim number
])
def test_a_reversal_under_another_name_of_the_payers_company_pairs_by_its_claim_number(tmp_path, kind, rev_payer, rev_ctl, paired):
    """UMR pays C024 $117.60 under its own name and id; the payer later reverses it under 'WAUSAU' (UMR's former name) and
    another id. The ids never join, so before this the reversal stayed unpaired (counted as reversing a payment not on file)
    and the payment stood. The payer's own claim number and the amount pair them, within one payer company (next_fixes
    section 4: 27 such reversals on the real copy)."""
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99214", "223.65", "117.60"), "20260301", **UMR_PAY)
    w.remit([visit_paid(f"{BILL}C024", payer_ctl="PC300B")], "20260520", **UMR_PAY)
    w.remit([visit_reversed(f"{BILL}C024", payer_ctl=rev_ctl)], "20260610", **rev_payer)
    det = w.run("r1", today="2026-09-20")
    m = w.meta(REVERSALS_NOT_ON_FILE_META)
    print(rev_payer, rev_ctl, left_on(det, f"{BILL}C024"), m)
    if paired:
        assert left_on(det, f"{BILL}C024") == 0 and m["lines"] + m["older_lines"] == 0, (left_on(det, f"{BILL}C024"), m)
    else:
        assert left_on(det, f"{BILL}C024") == 11760 and m["lines"] + m["older_lines"] == 1, (left_on(det, f"{BILL}C024"), m)
    w.store.close()


# ================================================================================================ residual: takebacks on voided claims
@pytest.mark.parametrize("sends,listed", [
    ([("8", "20260615")], False),                       # the practice voided C024 after the payment: the takeback answers it
    ([("8", "20260615"), ("7", "20260615")], True),     # a void and a new claim the same day is a replacement, not a void
    ([("8", "20260501")], True),                        # a void sent before the payment: the payment came after it
])
def test_a_takeback_of_a_claim_the_practice_voided_is_no_work(tmp_path, kind, sends, listed):
    """Anthem pays C024 $117.60 on May 20; the practice sends C024 again as a void (837 frequency 8); in August Anthem
    recoups the $117.60 naming C024. The takeback is the payer's answer to the void: nothing to dispute or bill (next_fixes
    section 2: TAKEBACK items on voided claims). A void followed by a new claim the same day is a replacement and leaves
    the takeback listed; so does a void sent before the payment."""
    from tests.test_837_sends import clm837, send
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99214", "223.65", "117.60"), "20260301")
    send(w.store, tmp_path, [clm837(f"{BILL}C024", [("99214", "223.65", "20260401")])], "20260402")
    w.remit([visit_paid(f"{BILL}C024", payer_ctl="PC300B")], "20260520")
    for freq, day in sends:
        send(w.store, tmp_path, [clm837(f"{BILL}C024", [("99214", "223.65", "20260401")], freq=freq)], day)
    w.remit([visit_paid("CB0000900C017", who=OTHER, payer_ctl="PC900")], "20260803",
            plb=f"PLB*1777777772*20261231*WO:87654321 {BILL}C024*117.60~")
    det = w.run("r1", today="2026-09-20")
    assert left_on(det, f"{BILL}C024") == 0
    tb = w.rows("signal='TAKEBACK'")
    print(sends, brief(tb))
    assert bool(tb) is listed, brief(tb)
    w.store.close()


@pytest.mark.parametrize("status,charges,listed", [("Inactive/Voided", 0, False), ("Inactive/Voided", 22365, True),
                                                   ("Payment Pending", 0, True)])
def test_a_takeback_of_a_charge_ema_shows_voided_is_no_work(tmp_path, kind, status, charges, listed):
    """EMA's lines of the service show the charge voided (every line's status a void, the charges netting to nothing): the
    practice withdrew the charge, and the payer's recoupment of its payment is no work. A void status with the charge
    still on the line, or a zero charge under another status, is not enough (a status alone never closes or drops an item)."""
    from remitwatch import ema_reports as er
    w = World(tmp_path, kind)
    er.ensure_schema(w.store.conn)
    w.remit(clean_visits(1, 25, "99214", "223.65", "117.60"), "20260301")
    w.remit([visit_paid(f"{BILL}C024", payer_ctl="PC300B")], "20260520")
    w.remit([visit_paid("CB0000900C017", who=OTHER, payer_ctl="PC900")], "20260803",
            plb=f"PLB*1777777772*20261231*WO:87654321 {BILL}C024*117.60~")
    w.store.conn.execute("INSERT INTO ema_lines (link, svc, cpt, bill, claim_id, status, charges, ins_paid, balance) VALUES (?,?,?,?,?,?,?,?,?)",
                         (7001, "2026-04-01", "99214", BILL, f"{BILL}C024", status, charges, 0, 0))
    w.store.conn.commit()
    w.run("r1", today="2026-09-20")
    tb = w.rows("signal='TAKEBACK'")
    assert bool(tb) is listed, (status, charges, brief(tb))
    w.store.close()
