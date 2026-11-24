"""The state of a service a recoupment left unpaid (a TAKEBACK item on a line, tests/test_taken_back.py): work now, with the
payer's words; done once EMA shows the service paid again after the takeback on a check no 835 on file carries (a payer that
repays by paper check, or another payer billed since). Every row is invented (tests/ema_fixtures.py for EMA's side; the
835 side is written straight into the store by tests/test_states.py's builders)."""
from __future__ import annotations

import datetime as dt

from tests import ema_fixtures as F
from tests.test_states import _env, item, ledger, line_key, ln, load, pay, remit, charge, later, state, store  # noqa: F401

D = dt.date


def _paid_then_taken(store) -> str:
    """Blue Cross pays the visit $34.00 on March 28 (its check 1) and recoups it on April 20, naming the claim (WO)."""
    tk = remit(store, trace=F.check_no(1), paid_on="2026-03-28",
               claims=[{"pcn": F.claim_id(1), "lines": [ln("99213", 150, 34.00, [("CO", "45", 66.00), ("PR", "3", 50)], allowed=84.00)]}])
    remit(store, trace=F.check_no(2), paid_on="2026-04-20", claims=[{"pcn": F.claim_id(9), "lines": [ln("99212", 90, 50, [("CO", "45", 40)])]}],
          plb=[("WO", f"Q{F.claim_id(1)}/123456789012345", 34.00)])
    item(store, signal="TAKEBACK", category="fees", actionable="review", title="99213 paid, then taken back", line_key=line_key(store, tk),
         claim_key=f"{tk}-0", transaction_key=tk, payment_date="2026-04-20", paid_cents=3400, expected_cents=3400, shortfall_cents=3400,
         carc=None, appeal_deadline="2028-04-19", evidence={"taken_back": {"date": "2026-04-20", "cents": 3400, "payer": "BLUE CROSS TEST",
                                                                             "reasons": ["WO"]}, "reference": "WO"})
    return tk


def test_a_service_paid_then_taken_back_is_work_now_with_the_payers_words(store, ledger, tmp_path):
    _paid_then_taken(store)
    load(store, ledger, tmp_path, [charge(F.link_no(1)), pay(F.link_no(1), F.check_no(1), 34.00), later()])
    st = state(store, today="2026-05-03")
    assert (st["work_state"], st["rule"]) == ("work_now", "fees"), (st["rule"], st["state_basis"])
    assert st["state_basis"].startswith("Paid, then taken back by Anthem / BCBS on Apr 20, 2026: find out why"), st["state_basis"]


def test_a_service_paid_again_after_the_takeback_on_a_check_with_no_835_is_done(store, ledger, tmp_path):
    """EMA posted the payment (check 1), the takeback (-$34.00) and, on May 10, the payer's new payment of $34.00 on a paper
    check no 835 carries: the service is paid again, the item done with that payment as its proof."""
    _paid_then_taken(store)
    load(store, ledger, tmp_path, [charge(F.link_no(1)), pay(F.link_no(1), F.check_no(1), 34.00),
                                   pay(F.link_no(1), F.check_no(2), -34.00, posted=D(2026, 4, 22)),
                                   pay(F.link_no(1), "PAPER-778", 34.00, posted=D(2026, 5, 10)), later(posted=D(2026, 5, 12))])
    st = state(store, today="2026-05-13")
    assert (st["work_state"], st["rule"]) == ("done", "repaid_after_takeback"), (st["rule"], st["state_basis"])
    assert "no 835" in st["state_basis"] and "$34.00" in st["state_basis"], st["state_basis"]
