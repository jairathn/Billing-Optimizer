"""An item Results recorded as lost to its deadline (it expired while Work now) that later turns done by money is no loss:
the link map's one-visit rule (replay-fix-2b, B5) gives a visit's denial its EMA claim back, EMA shows the other payer paid
before the denial, and the item is done (paid_before_other_payer). The scoreboard's "lost to deadlines" (owner's rule 5)
counts real losses only. A loss stands when the item is let go, marked Done by a person (nothing recovered) or leaves the
list afterwards, and again when money it was done by is taken back. Invented data only, both store kinds."""
from __future__ import annotations

import pytest

from remitwatch import results
from remitwatch.detect import ensure_schema
from remitwatch.store import Store


def _expired_item(tmp_path, kind, fid="F-visit", cents=5033):
    s = Store(str(tmp_path / "s.sqlite")) if kind == "file" else Store(":memory:", compact=True)
    ensure_schema(s.conn)
    results.ensure_schema(s.conn)
    c = s.conn
    row = dict(finding_id=fid, signal="CLAIM_DENIED_UNPAID", category="denial", severity="medium", actionable="review",
               title="Invented", status="open", patient_control_number="ZZ00R50001C017", service_date="2026-03-02",
               payer_group="Testcare Medicaid", payer_kind="medicaid", shortfall_cents=cents, appeal_deadline="2026-09-01",
               first_seen="2026-05-01T12:00:00+00:00", last_seen="2026-05-01T12:00:00+00:00", run_id="r0", stale=0,
               work_state="work_now")
    cols = list(row)
    c.execute(f"INSERT INTO findings ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [row[k] for k in cols])
    c.commit()
    results.update_registry(c, "2026-05-01")
    c.execute("UPDATE findings SET work_state='expired', state_rule='expired' WHERE finding_id=?", (fid,))
    c.commit()
    results.update_registry(c, "2026-09-10")
    lost, _l = results._lost(results.load_registry(c))
    assert lost["n"] == 1 and lost["cents"] == cents                                      # lost to its deadline, as it was
    return s, c


@pytest.mark.parametrize("kind", ["file", "compact"])
def test_an_item_that_expired_then_turned_done_is_not_lost_to_its_deadline(tmp_path, kind):
    s, c = _expired_item(tmp_path, kind)
    c.execute("UPDATE findings SET work_state='done', state_rule='paid_before_other_payer' WHERE finding_id='F-visit'")
    c.commit()
    results.update_registry(c, "2026-11-25")
    lost, lists = results._lost(results.load_registry(c))
    assert lost["n"] == 0 and lost["cents"] == 0, ("an item now done (paid before by another payer) is still counted as lost "
                                                   f"to its deadline on Results: {lost}")
    assert lists["lost"] == [], lists
    s.close()


@pytest.mark.parametrize("kind", ["file", "compact"])
def test_an_expired_item_a_person_marks_paid_is_not_lost(tmp_path, kind):
    """A person's Paid with the dollars recovered: Results counts the recovery, never the same dollars lost as well."""
    s, c = _expired_item(tmp_path, kind)
    c.execute("UPDATE findings SET work_state='done', state_rule='person_paid', status='resolved', recovered_cents=5033 "
              "WHERE finding_id='F-visit'")
    c.commit()
    results.update_registry(c, "2026-11-25")
    lost, _l = results._lost(results.load_registry(c))
    assert lost["n"] == 0 and lost["cents"] == 0, lost
    s.close()


@pytest.mark.parametrize("kind", ["file", "compact"])
@pytest.mark.parametrize("after", [
    dict(work_state="accepted", status="dismissed", state_rule="person_let_go"),      # a person let it go
    dict(work_state="done", status="resolved", state_rule="person_done"),             # a person's Done: nothing recovered
    dict(work_state="done", stale=1, state_rule="stale"),                             # no longer listed, its row kept
    dict(work_state="done", state_rule=None),                                         # done by a rule the store did not keep
], ids=["let_go", "person_done", "stale", "rule_unknown"])
def test_control_a_loss_stands_when_no_money_came(tmp_path, kind, after):
    """The loss happened at the deadline: a person letting the item go or marking it Done afterwards, or the item leaving
    the list, does not undo it."""
    s, c = _expired_item(tmp_path, kind, fid="F-gone", cents=4000)
    c.execute(f"UPDATE findings SET {', '.join(k + '=?' for k in after)} WHERE finding_id='F-gone'", list(after.values()))
    c.commit()
    results.update_registry(c, "2026-11-25")
    lost, _l = results._lost(results.load_registry(c))
    assert lost["n"] == 1 and lost["cents"] == 4000, lost
    s.close()


@pytest.mark.parametrize("kind", ["file", "compact"])
def test_control_a_loss_counts_again_when_the_money_it_was_done_by_is_taken_back(tmp_path, kind):
    s, c = _expired_item(tmp_path, kind)
    c.execute("UPDATE findings SET work_state='done', state_rule='paid_before_other_payer' WHERE finding_id='F-visit'")
    c.commit()
    results.update_registry(c, "2026-11-25")
    assert results._lost(results.load_registry(c))[0]["n"] == 0
    c.execute("UPDATE findings SET work_state='expired', state_rule='expired' WHERE finding_id='F-visit'")   # taken back
    c.commit()
    results.update_registry(c, "2026-12-02")
    lost, _l = results._lost(results.load_registry(c))
    assert lost["n"] == 1 and lost["cents"] == 5033, lost
    s.close()
