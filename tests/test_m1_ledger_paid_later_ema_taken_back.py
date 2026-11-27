"""m1-fx-ledger review (invented data, both store kinds): fx-ledger-r1-4 nets the payer's recoupments out of EMA's postings
in paid_proof's C07 and paid-before branches (states._ema_pay_taken), but not in its first branch: a denial is done as
'Paid later' on a payment EMA posted on a check no 835 carries, though a later 835 recoups that very money naming the
claim. _ema_pay_taken's own docstring covers the case ('a payment no 835 on file carries: a recoupment the 835s name for
the claim after it was posted takes it all'); the paid_after branch never asks it."""
from __future__ import annotations

import datetime as dt

import pytest

from remitwatch import ema_ledger as el
from remitwatch.store import Store
from tests import ema_fixtures as F
from tests import test_states as TS

D = dt.date


@pytest.fixture(params=["file", "compact"])
def kind(request):
    return request.param


def _store(tmp_path, kind):
    from remitwatch import detect as Dt, ema_reports as er
    if kind == "file":
        return TS.new_store(str(tmp_path / "store.sqlite"))
    s = Store(":memory:", compact=True)
    er.ensure_schema(s.conn)
    Dt.ensure_schema(s.conn)
    s.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('ema:key_salt', ?)", (TS.SALT_HEX,))
    s.conn.commit()
    return s


@pytest.mark.parametrize("recouped", [True, False])
def test_a_payment_ema_posted_with_no_835_and_recouped_since_does_not_close_the_denial(tmp_path, kind, recouped):
    """Blue Cross denies the 99213 (CO-197, March 28). EMA posts Blue Cross's $90.00 on April 20 on a check no 835 carries,
    and the line settles. On May 1 a Blue Cross 835 for another patient withholds $90.00 naming this claim (WO). Nothing
    pays the 99213 now: the denial is not done on EMA's posting of the money the payer took back. The control (no
    recoupment) is done, as tests/test_states.py's C01 case."""
    s = _store(tmp_path, kind)
    lg = el.Ledger.open(str(tmp_path / "ema_ledger.sqlite"))
    try:
        tk = TS.denied(s)
        TS.item(s, line_key=TS.line_key(s, tk), claim_key=f"{tk}-0", transaction_key=tk)
        if recouped:
            TS.remit(s, trace=F.check_no(2), paid_on="2026-05-01",
                     claims=[{"pcn": F.claim_id(9), "lines": [TS.ln("99212", 100, 50, [("CO", "45", 50)])]}],
                     plb=[("WO", f"WO:7777 {F.claim_id(1)}", 90)])
        TS.load(s, lg, tmp_path, [TS.charge(F.link_no(1), status="Resolved/Paid"), TS.pay(F.link_no(1), F.check_no(1), 0),
                                  TS.pay(F.link_no(1), "NOERA01", 90, posted=D(2026, 4, 20), status="Resolved/Paid"),
                                  TS.adj(F.link_no(1), "CO-45", 60, contractual=True, posted=D(2026, 4, 20), status="Resolved/Paid"),
                                  TS.later(posted=D(2026, 5, 2))])
        st = TS.state(s, today="2026-05-03")
        print(recouped, st["work_state"], st.get("rule"), "|", st.get("state_basis"))
        if recouped:
            assert st["work_state"] != "done", (
                "the payer took back the $90.00 EMA posted (WO naming the claim on a later 835) and nothing pays the 99213, yet "
                f"the denial is done: {st.get('rule')}: {st.get('state_basis')}")
        else:
            assert (st["work_state"], st.get("rule")) == ("done", "paid_later_ema")
    finally:
        lg.close()
        s.close()
