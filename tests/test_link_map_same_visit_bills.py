"""One patient's visit billed to the same payer under two bills is not a conflict that may cost the visit its EMA claim.

The month-by-month replay of 2026 found this on a real item: a state Medicaid plan denied a visit's claim CO-109 (another payer is
primary) while EMA showed the Medicare Advantage plan had paid it as primary before that denial: the item was done
("Already paid: EMA shows ... by the Medicare Advantage plan, before this denial, and the line settled"). Months later
the practice's claim to Medicaid as the second payer, under a new bill, came back processed as secondary at $0, and EMA
posted that $0 answer on the same EMA claim. The link map then held one payment vote for each bill (1 of 2, under the
2/3 it needs: ema_ledger._map, VOTE_SHARE) and marked the EMA claim a conflict; the visit's denial lost its EMA claim,
went to Work now ("Not paid and nobody has worked it") and expired, counted as lost to its deadline. Both votes name the
same patient and date: one visit's two bills, not two claims competing for one EMA claim. In the replay's last store 39
of the 41 payment conflicts are of this kind, 24 of them visits with two to five codes; the other two name another
patient.

Invented data only (tests/ema_fixtures.py's first patient on both 835 claims and on every EMA posting), on both store
kinds, for a visit with one code and one with two. The control is the same visit before the second bill's answer is on
file: done, as the rules mean. Three more cases: when the second bill names another patient the link stays a conflict
(a split between two patients is never joined), so does a split over two dates of service (two visits), and a store
whose map an older rule made maps the link again at its next refresh, once, with no new report.
"""
from __future__ import annotations

import datetime as dt
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch.detect import Detector  # noqa: E402
from tests import ema_fixtures as F  # noqa: E402
from tests.test_837_sends import KINDS  # noqa: E402
from tests.test_ema_wiring import adj, charge, load, pay, remit  # noqa: E402
from tests.test_fx_exports import _ema_store, ema_env  # noqa: E402,F401

D = dt.date
SVC = D(2026, 3, 2)
S = SVC.isoformat()
MA = "UHC Medicare Advantage Test"          # the visit's first payer: paid in EMA, on a check no 835 carries
MCD = "Kentucky Medicaid Test"              # EMA's name for the second payer
MCD_835 = "KENTUCKY MEDICAID"               # the same payer on its 835s
LINK = F.link_no(41)
# the denied bill and the visit's second bill; the second bill sorts first, so mapping a split link to the lowest bill
# id is not enough: the denied bill must keep its EMA claim
BILL_A, BILL_B = F.claim_id(42), F.claim_id(41)
T_A, T_B = F.check_no(42), F.check_no(41)            # the two 835 traces (Medicaid's checks)


def day(n: int) -> str:
    return (SVC + dt.timedelta(days=n)).isoformat()


# the visit's codes: (code, charge, the first payer's payment, its contractual adjustment)
ONE_CODE = (("99214", 120.00, 78.40, 41.60),)
TWO_CODES = (("99214", 120.00, 78.40, 41.60), ("17000", 90.00, 61.10, 28.90))


def _visit(tmp_path, kind, *, second_bill: bool, second_patient: tuple | None = None, keep: bool = False,
           codes: tuple = ONE_CODE):
    s, lg = _ema_store(tmp_path, kind)
    rows = []
    for cpt, amount, paid, contractual in codes:
        both = dict(patient=0, svc=SVC, cpt=cpt, primary=MA, secondary=MCD)
        rows += [charge(LINK, amount=amount, posted=SVC + dt.timedelta(days=2), **both),
                 # the Medicare Advantage plan pays the visit as primary on day 50 (no 835 for its check) and EMA settles the line
                 pay(LINK, "MA50000001", paid, posted=SVC + dt.timedelta(days=50), payer=MA, **both),
                 adj(LINK, "CO-45", contractual, posted=SVC + dt.timedelta(days=50), contractual=True, payer=MA, **both),
                 # Medicaid's $0 answer to bill A (its CO-109 denial of day 57), posted on day 75 under the denial's check
                 pay(LINK, T_A, 0, posted=SVC + dt.timedelta(days=75), payer=MCD, **both)]
        if second_bill:
            # Medicaid's $0 answer to the visit's second bill (day 170), posted on the same EMA claim on day 185
            rows.append(pay(LINK, T_B, 0, posted=SVC + dt.timedelta(days=185), payer=MCD, **both))
    # a later charge on another EMA claim carries Production's as_of past every posting here
    rows.append(charge(F.link_no(99), cpt="99212", patient=3, svc=SVC + dt.timedelta(days=150),
                       posted=SVC + dt.timedelta(days=195), status="Payment Pending"))
    remit(s, trace=T_A, paid_on=day(57), payer=MCD_835, status="4",
          claims=[{"pcn": BILL_A, "status": "4",
                   "lines": [(S, cpt, amount, 0, [("CO", "109", amount)]) for cpt, amount, _p, _c in codes]}])
    if second_bill:
        # the visit billed to Medicaid again as the second payer, under a new bill: processed as secondary at $0
        who = {"last": second_patient[0], "first": second_patient[1]} if second_patient else {}
        remit(s, trace=T_B, paid_on=day(170), payer=MCD_835, status="2",
              claims=[dict(who, pcn=BILL_B, status="2",
                           lines=[(S, cpt, amount, 0, [("OA", "23", paid), ("CO", "45", contractual)])
                                  for cpt, amount, paid, contractual in codes])])
    load(s, lg, tmp_path, rows)
    det = Detector(s, run_id="r1", today=day(200))
    det.run()
    assert det.ema_errors == [], det.ema_errors
    item = [dict(r) for r in s.query("SELECT signal, status, work_state, state_rule, state_basis, ema_link, shortfall_cents "
                                     "FROM findings WHERE patient_control_number = ? AND COALESCE(stale, 0) = 0", (BILL_A,))]
    link = s.conn.execute("SELECT method, votes, total, conflict FROM ema_link_map WHERE link = ?", (LINK,)).fetchone()
    if keep:
        return item, tuple(link) if link else None, s, lg
    lg.close()
    s.close()
    return item, tuple(link) if link else None


@pytest.mark.parametrize("codes", [ONE_CODE, TWO_CODES], ids=["one_code", "two_codes"])
@pytest.mark.parametrize("kind", KINDS)
def test_control_before_the_second_bill_the_denial_is_done_by_the_first_payers_payment(tmp_path, kind, codes, ema_env):
    item, link = _visit(tmp_path, kind, second_bill=False, codes=codes)
    assert link and link[3] == 0, link
    states = [(i["signal"], i["work_state"], i["state_rule"]) for i in item]
    assert states == [("CLAIM_DENIED_UNPAID", "done", "paid_before_other_payer")], item


@pytest.mark.parametrize("codes", [ONE_CODE, TWO_CODES], ids=["one_code", "two_codes"])
@pytest.mark.parametrize("kind", KINDS)
def test_the_visits_second_bill_does_not_cost_the_denial_its_ema_claim(tmp_path, kind, codes, ema_env):
    item, link = _visit(tmp_path, kind, second_bill=True, codes=codes)
    states = [(i["signal"], i["work_state"], i["state_rule"]) for i in item]
    assert states == [("CLAIM_DENIED_UNPAID", "done", "paid_before_other_payer")], (
        "the visit's second bill to the same payer (same patient and date) split the EMA claim's payment votes evenly, "
        f"the link map marked it a conflict {link} (method, votes, total, conflict), and the denial EMA shows settled by the "
        "first payer went back to Work now: "
        f"{[(i['work_state'], i['state_rule'], i['ema_link'], i['shortfall_cents']) for i in item]}")
    assert link[0] == "payment" and link[3] == 0 and link[1] * 2 == link[2], link       # the denied bill's votes, of all


@pytest.mark.parametrize("kind", KINDS)
def test_control_a_split_between_two_patients_stays_a_conflict(tmp_path, kind, ema_env):
    """The second bill's 835 names another patient: its vote is not the EMA claim's patient, so the two bills are not one
    visit's. The link stays a conflict and maps to no bill, as before."""
    item, link = _visit(tmp_path, kind, second_bill=True, second_patient=("OTHERPATIENT", "ZULU"))
    assert link is not None and link[3] == 1, link
    assert [i["ema_link"] for i in item] == [None], item


@pytest.mark.parametrize("kind", KINDS)
def test_control_a_split_over_two_dates_of_service_stays_a_conflict(tmp_path, kind, ema_env):
    """One EMA claim holds two dates of service, and the same patient's two bills each answer one of them: two visits,
    not one visit billed twice. The link stays a conflict, as before."""
    s, lg = _ema_store(tmp_path, kind)
    try:
        svc2 = SVC + dt.timedelta(days=7)
        rows = [charge(LINK, cpt="99214", amount=120.00, patient=0, svc=SVC, posted=SVC + dt.timedelta(days=2)),
                charge(LINK, cpt="99214", amount=120.00, patient=0, svc=svc2, posted=svc2 + dt.timedelta(days=2)),
                pay(LINK, T_A, 0, cpt="99214", patient=0, svc=SVC, posted=SVC + dt.timedelta(days=40), payer=MCD),
                pay(LINK, T_B, 0, cpt="99214", patient=0, svc=svc2, posted=SVC + dt.timedelta(days=60), payer=MCD)]
        remit(s, trace=T_A, paid_on=day(30), payer=MCD_835, status="4",
              claims=[{"pcn": BILL_A, "status": "4", "lines": [(S, "99214", 120.00, 0, [("CO", "109", 120.00)])]}])
        remit(s, trace=T_B, paid_on=day(50), payer=MCD_835, status="4",
              claims=[{"pcn": BILL_B, "status": "4", "lines": [(svc2.isoformat(), "99214", 120.00, 0, [("CO", "109", 120.00)])]}])
        load(s, lg, tmp_path, rows)
        link = s.conn.execute("SELECT method, votes, total, conflict FROM ema_link_map WHERE link = ?", (LINK,)).fetchone()
        assert link is not None and tuple(link) == ("payment", 1, 2, 1), tuple(link) if link else None
    finally:
        lg.close()
        s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_a_map_an_older_rule_made_is_mapped_again_at_the_next_refresh(tmp_path, kind, ema_env):
    """A store whose map an older build made holds the visit's link as a conflict (meta ema:map_rule absent). The next
    refresh, with no new report, maps it again by this rule: the denied bill keeps its EMA claim. Once: the refresh after
    it has nothing to do."""
    from remitwatch import ema_ledger as el
    from tests.test_ema_wiring import Log
    item, link, s, lg = _visit(tmp_path, kind, second_bill=True, keep=True)
    try:
        s.conn.execute("UPDATE ema_link_map SET conflict = 1, bill = ?, votes = 1 WHERE link = ?", (F.bill_id(41), LINK))
        s.conn.execute("UPDATE ema_lines SET bill = NULL, map_method = 'conflict' WHERE link = ?", (LINK,))
        s.conn.execute("DELETE FROM meta WHERE key = ?", (el.META_MAP_RULE,))
        s.conn.commit()
        out = el.refresh(s, lg, log=Log())
        assert out["part_b"] is not None and out["part_a"] is None, out
        got = s.conn.execute("SELECT bill, conflict FROM ema_link_map WHERE link = ?", (LINK,)).fetchone()
        assert tuple(got) == (F.bill_id(42), 0), tuple(got)
        line_bill = s.conn.execute("SELECT bill FROM ema_lines WHERE link = ? AND cpt = '99214'", (LINK,)).fetchone()[0]
        assert line_bill == F.bill_id(42), line_bill
        assert el.refresh(s, lg, log=Log())["part_b"] is None            # once
    finally:
        lg.close()
        s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_the_denied_bill_keeps_its_ema_claim_when_the_visits_other_bill_was_answered_first(tmp_path, kind, ema_env):
    """The same visit in the other order (the review's case): Medicaid first processes the visit's claim as the second
    payer (bill B, $0, its answer posted in EMA on day 75), and only later denies the visit's erroneous first claim CO-109
    (bill A, posted on day 185). Both votes name the link's patient for one date: one visit's bills. The link goes to the
    bill whose answer was the payer's denial (claim status 4), whatever order EMA posted them in, so that the denial,
    which EMA shows settled by the Medicare Advantage plan before it, reads its EMA claim and is done."""
    s, lg = _ema_store(tmp_path, kind)
    try:
        both = dict(patient=0, svc=SVC, cpt="99214", primary=MA, secondary=MCD)
        rows = [charge(LINK, amount=120.00, posted=SVC + dt.timedelta(days=2), **both),
                pay(LINK, "MA50000001", 78.40, posted=SVC + dt.timedelta(days=50), payer=MA, **both),
                adj(LINK, "CO-45", 41.60, posted=SVC + dt.timedelta(days=50), contractual=True, payer=MA, **both),
                pay(LINK, T_B, 0, posted=SVC + dt.timedelta(days=75), payer=MCD, **both),
                pay(LINK, T_A, 0, posted=SVC + dt.timedelta(days=185), payer=MCD, **both),
                charge(F.link_no(99), cpt="99212", patient=3, svc=SVC + dt.timedelta(days=150),
                       posted=SVC + dt.timedelta(days=195), status="Payment Pending")]
        remit(s, trace=T_B, paid_on=day(57), payer=MCD_835, status="2",
              claims=[{"pcn": BILL_B, "status": "2", "lines": [(S, "99214", 120.00, 0, [("OA", "23", 78.40), ("CO", "45", 41.60)])]}])
        remit(s, trace=T_A, paid_on=day(170), payer=MCD_835, status="4",
              claims=[{"pcn": BILL_A, "status": "4", "lines": [(S, "99214", 120.00, 0, [("CO", "109", 120.00)])]}])
        load(s, lg, tmp_path, rows)
        det = Detector(s, run_id="r1", today=day(200))
        det.run()
        assert det.ema_errors == [], det.ema_errors
        link = tuple(s.conn.execute("SELECT bill, method, votes, total, conflict FROM ema_link_map WHERE link = ?", (LINK,)).fetchone())
        items = [dict(r) for r in s.query("SELECT signal, work_state, state_rule, ema_link FROM findings "
                                          "WHERE patient_control_number = ? AND COALESCE(stale, 0) = 0", (BILL_A,))]
        assert [(i["signal"], i["work_state"], i["state_rule"]) for i in items] == \
            [("CLAIM_DENIED_UNPAID", "done", "paid_before_other_payer")], (link, items)
        assert link == (F.bill_id(42), "payment", 1, 2, 0), link
    finally:
        lg.close()
        s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_control_a_denied_bill_another_link_holds_is_not_taken(tmp_path, kind, ema_env):
    """The same visit in the other order, but another EMA claim (another patient's) already holds the denied bill: it
    voted for bill A on its own posting under the denial's check. The visit's link never takes a bill another link holds
    by its preference for the denied bill: it keeps the bill of its first vote, B, and the other claim keeps A."""
    s, lg = _ema_store(tmp_path, kind)
    try:
        other = F.link_no(43)
        both = dict(patient=0, svc=SVC, cpt="99214", primary=MA, secondary=MCD)
        rows = [charge(LINK, amount=120.00, posted=SVC + dt.timedelta(days=2), **both),
                pay(LINK, "MA50000001", 78.40, posted=SVC + dt.timedelta(days=50), payer=MA, **both),
                pay(LINK, T_B, 0, posted=SVC + dt.timedelta(days=75), payer=MCD, **both),
                pay(LINK, T_A, 0, posted=SVC + dt.timedelta(days=185), payer=MCD, **both),
                charge(other, amount=120.00, patient=3, svc=SVC, cpt="99214", posted=SVC + dt.timedelta(days=3)),
                pay(other, T_A, 0, patient=3, svc=SVC, cpt="99214", posted=SVC + dt.timedelta(days=190), payer=MCD)]
        remit(s, trace=T_B, paid_on=day(57), payer=MCD_835, status="2",
              claims=[{"pcn": BILL_B, "status": "2", "lines": [(S, "99214", 120.00, 0, [("OA", "23", 78.40), ("CO", "45", 41.60)])]}])
        remit(s, trace=T_A, paid_on=day(170), payer=MCD_835, status="4",
              claims=[{"pcn": BILL_A, "status": "4", "lines": [(S, "99214", 120.00, 0, [("CO", "109", 120.00)])]}])
        load(s, lg, tmp_path, rows)
        got = {lk: (b, c) for lk, b, c in s.conn.execute("SELECT link, bill, COALESCE(conflict, 0) FROM ema_link_map WHERE link IN (?, ?)",
                                                         (LINK, other))}
        assert got == {LINK: (F.bill_id(41), 0), other: (F.bill_id(42), 0)}, got
    finally:
        lg.close()
        s.close()
