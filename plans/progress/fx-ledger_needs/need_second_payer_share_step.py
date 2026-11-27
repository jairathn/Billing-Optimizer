"""Needs from others (F1 -> F5, with the shared branch of states._decide): a failing test, kept out of the suite (the file
name does not start with test_) until the functions that word a TAKEBACK item's state and next step are changed. Copy it
into tests/ to run it. Review findings fx-ledger-r1-2 and fx-ledger-r2-10.

money-2 (F1, done) lists a second payer's share taken back in full as a TAKEBACK item of the second payer's line, with
evidence['second_payer'] = True and the title "<code>: <payer> paid its share as second payer, then took it back on
<day>: bill the patient, or dispute the takeback". Only the title says so. The state basis comes from the TAKEBACK
branch every takeback shares (states._decide, `if sig in FEES:`, the work_now basis "Paid, then taken back by <group> on
<day>: find out why, then dispute or bill the right payer"), and the drawer's next step from story's route 'fees'
("Match the takeback to the payer's overpayment letter; if it was not owed, dispute it; either way post it in EMA ...",
buttons copy_instructions, mark_posted, mark_sent, open_in_ema, let_go). The coinsurance is the patient's again unless
the takeback is disputed: the basis and the next step must say "bill the patient, or dispute the takeback", with the
bill-the-patient action (F5's _bill_patient_step), never "bill the right payer".

The change: in states._decide's FEES branch, when _ev(f).get('second_payer') is true, the work_now basis says the second
payer took back the coinsurance it paid (the patient's share is open again: bill the patient, or dispute the takeback),
and the route/next step follows (route 'bill_patient', or a dispute step that offers the bill-the-patient action).
Real copy (reviewers): 11 second-payer TAKEBACK items ($369.63) on Work now, e.g. Humana Medicare Advantage 99213 $24.50.

Invented data only, both store kinds."""
from __future__ import annotations

from remitwatch import story
from tests.review3.r3helpers import clm, sv, visit_paid
from tests.test_features import clean_visits
from tests.test_fx_ledger import GAP, KY, OTHER, World, kind  # noqa: F401  (kind: the both-store-kinds fixture)


def _share_taken_back(w: World, pcn: str) -> None:
    w.remit(clean_visits(1, 25, "99214", "223.65", "80.00"), "20260301", **KY)
    w.remit([clm(pcn, "1", "223.65", "64.00", sv("99214", "223.65", "64.00", 1, "CAS*CO*45*143.65~CAS*PR*2*16.00~", allowed="80.00"),
                 payer_ctl="MC1")], "20260415", **KY)
    w.remit([clm(pcn, "2", "223.65", "16.00", sv("99214", "223.65", "16.00", 1, "CAS*OA*23*207.65~"), payer_ctl="GP1")], "20260425", **GAP)
    w.remit([visit_paid("CB0000900C017", who=OTHER, payer_ctl="GP9", paid="16.00")], "20260803",
            plb=f"PLB*1777777772*20261231*WO:5550001 {pcn}*16.00~", **GAP)


def test_a_second_payers_share_taken_back_tells_the_biller_to_bill_the_patient(tmp_path, kind):  # noqa: F811
    """(r1-2) Medicare pays $64.00 and leaves $16.00 of coinsurance; the supplement pays the $16.00 as second payer, then
    withholds it (PLB WO naming the claim). The item's state and the drawer's next step say to bill the patient (or
    dispute), not only to match the overpayment letter and post it."""
    w = World(tmp_path, kind)
    pcn = "CB0000300C017"
    _share_taken_back(w, pcn)
    w.run("r1", today="2026-09-20")
    tb = w.rows("signal='TAKEBACK' AND COALESCE(stale, 0) = 0")
    assert len(tb) == 1, "the supplement's $16.00 taken back is one TAKEBACK item"
    out = story.explain(w.store.conn, tb[0]["finding_id"], today="2026-09-20")
    nxt, st = out["next_step"] or {}, out["state"] or {}
    words = " ".join([nxt.get("action") or "", nxt.get("instructions") or "", st.get("state_basis") or ""]).lower()
    assert "patient" in words, ("a second payer took back the coinsurance it paid; the state and the drawer's next step never "
                                f"say to bill the patient: basis {st.get('state_basis')!r}, next step {nxt.get('action')!r}")
    w.store.close()


def test_a_second_payers_share_taken_back_says_bill_the_patient_or_dispute_everywhere(tmp_path, kind):  # noqa: F811
    """(r2-10) The same: the item's title says 'bill the patient, or dispute the takeback'; its basis (the worklist's
    reason) must say the same, never 'bill the right payer'."""
    w = World(tmp_path, kind)
    _share_taken_back(w, "CB0000300C017")
    w.run("r1", today="2026-09-20")
    (r,) = w.rows("signal='TAKEBACK'")
    assert "bill the patient" in r["title"]
    assert "bill the right payer" not in (r["state_basis"] or ""), r["state_basis"]
    assert "patient" in (r["state_basis"] or "").lower(), r["state_basis"]
    w.store.close()
