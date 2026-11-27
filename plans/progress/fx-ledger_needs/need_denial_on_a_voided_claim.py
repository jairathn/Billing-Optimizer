"""Needs from others (F1 -> the integrator: the denial and bundling line rules and the claim rules are in no fixer's
row): a failing test, kept out of the suite (the file name does not start with test_) until the rule that lists a
denial reads the practice's voids. Copy it into tests/ to run it.

F1 reads the voids for takebacks (detect._voided_services: an 837 void sent after the payment with no later send, or
EMA's lines of the service all voided with the charges netting to nothing): a takeback of a voided charge is no work.
The same voids leave other items listed. On the real copy (tip 993f440, A17 W view) 3 claim denials ($226.33, routed
to check first) answer claims the practice had voided (837 frequency 8), and 2 bundling denials ($81.92) are on
charges EMA shows voided; A17 calls all 5 already done (VOIDED). The change: the rule that lists a denial skips a
service in detect._voided_services' answer (the denial's own answer day as the 'paid' day), or the state rules
(states._decide, shared) put it done with the void as proof.

Invented data only, both store kinds."""
from __future__ import annotations

from tests.test_features import clean_visits
from tests.test_fx_ledger import BILL, World, brief, kind  # noqa: F401
from tests.review3.r3helpers import visit_denied_line


def test_a_denial_of_a_claim_the_practice_voided_afterwards_is_not_work(tmp_path, kind):  # noqa: F811
    """Anthem denies C017's 99214 (missing information) on April 15; the practice sends C017 again as a void (837
    frequency 8) on April 20 and nothing after it: the charge is withdrawn, and the denial is nothing to work."""
    from tests.test_837_sends import clm837, send
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99214", "223.65", "117.60"), "20260301")
    send(w.store, tmp_path, [clm837(f"{BILL}C017", [("99214", "223.65", "20260401")])], "20260402")
    w.remit([visit_denied_line(f"{BILL}C017", carc="16")], "20260415")
    send(w.store, tmp_path, [clm837(f"{BILL}C017", [("99214", "223.65", "20260401")], freq="8")], "20260420")
    w.run("r1", today="2026-09-20")
    rows = w.rows(f"patient_control_number LIKE '{BILL}%' AND COALESCE(stale, 0) = 0")
    assert rows, "the denial is an item"
    assert all(r["work_state"] != "work_now" for r in rows), brief(rows)
    w.store.close()
