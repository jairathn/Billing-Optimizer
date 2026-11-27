"""Needs from others (F1 -> the integrator: detect._set_states is in no fixer's row, or F4 if the size belongs to the
EMA side's Medicaid rule): a failing test, kept out of the suite (the file name does not start with test_) until the
function that stores an item's size is changed. Copy it into tests/ to run it.

money-9 asks for two things. The first is F1's and is done: a Medicaid denial stays work while the first payer paid
less than Medicaid's rate and EMA shows Medicaid's share open (states.paid_first_elsewhere, fx['paid_first_partial'],
named in the open basis: "Medicaid owes up to $20.00 as second payer ($70.00 open in EMA)"). The second is not F1's:
"size the item at expected minus paid, capped at EMA's open balance". The item keeps its stored size, Medicaid's whole
rate ($70.00), so the work list ranks and totals it at $70.00 while $20.00 is owed. The change: where the states are
stored (detect._set_states), store shortfall_cents = fx['paid_first_partial']['owed'] when the state's facts carry it
(states.state_of would return it, for example as st['owed_cents'], a key F1 can add in paid_first_elsewhere's branch).

Invented data only (the fixtures of tests/test_paid_first_elsewhere.py), both store kinds."""
from __future__ import annotations

import datetime as dt

from remitwatch import states
from tests import ema_fixtures as F
from tests import test_ema_wiring as EW
from tests.test_fx_ledger import kind  # noqa: F401  (the both-store-kinds fixture)
from tests.test_paid_first_elsewhere import STATE, TODAY, _by_claim, _carry, _claim, _ema, _env, _history, _host  # noqa: F401


def test_a_medicaid_denial_the_first_payer_paid_in_part_is_sized_at_what_medicaid_still_owes(tmp_path, kind, monkeypatch):  # noqa: F811
    """Medicaid's rate for the 99214 is $70.00; the first payer paid $50.00 on a paper check; EMA shows $70.00 open. The
    denial is work (F1) and its size is the $20.00 Medicaid still owes, not its whole rate."""
    s, lg = _host(tmp_path, kind, monkeypatch)
    try:
        _history(s)
        _claim(s, 44, payer=STATE, carc="96")
        rows, ar = _ema(44, first_paid=50, share="open")
        rows.append(_carry(dt.date(2026, 5, 25)))
        EW.load(s, lg, tmp_path, rows, ar)
        EW.run(s, today=TODAY)
        f = _by_claim(s)[F.claim_id(44)]
        st = states.state_of(s.conn, f, today=TODAY)
        assert st["work_state"] == "work_now", st
        assert f["shortfall_cents"] == 2000, f"sized at {f['shortfall_cents']} cents, while Medicaid owes $20.00"
    finally:
        lg.close()
        s.close()
