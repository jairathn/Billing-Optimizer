"""Needs from others (F1 -> F6, cloud.py): a failing test, kept out of the suite (the file name does not start with
test_) until the dashboard's copy carries the ledger's takebacks. Copy it into tests/ to run it. Review finding
fx-ledger-r2-4 (host parity of the PLB/J1 residual).

paid_proof counts only what a recoupment left of a payment: the detection run's ledger writes table ledger_taken
(detect._save_taken: line_key, taken_cents, taken_on, standing_cents, listed) and states._r835 reads it beside each 835
answer. cloud.SERVING_TABLES does not carry ledger_taken, and the drawer (story.explain -> story._state_of) recomputes
the live state on whichever connection it is given, showing the live proof when the stored and the live work states
agree. On the server the proof counts the payment less the takeback ($87.60); on Vercel's dashboard copy the whole
payment ($117.60).

The change (F6 owns cloud.py): add ("ledger_taken", None, "line_key IN (SELECT line_key FROM main.lines)") to
SERVING_TABLES, or filter it to the copy's lines as lines itself is filtered ({DETAIL} OR {BILLS}), and bump
SERVING_BUILD so copies made by the older build are made again. (The other way: story._state_of shows the stored basis
and proof on the copy; story._state_of is in no fixer's row.)
Real copy (reviewers): ledger_taken holds 342 rows ($32,988.19, 67 listed); 54 payments were taken in part with no item of
their own ($3,242.08), so any proof citing them names another amount on Vercel.

Invented data only, both store kinds."""
from __future__ import annotations

from remitwatch import cloud, story
from remitwatch.store import Store
from tests.review3.r3helpers import visit_denied_line, visit_paid
from tests.test_features import clean_visits
from tests.test_fx_ledger import BILL, OTHER, World, kind  # noqa: F401  (kind: the both-store-kinds fixture)


def test_the_drawer_names_the_same_proof_on_the_server_and_on_the_dashboards_copy(tmp_path, kind):  # noqa: F811
    """C017's 99214 is denied (missing information, April 15). The corrected claim C024 is paid $117.60 (May 20). In August
    Anthem withholds $30.00 naming C024 from a check that pays another patient: a recoupment under $500 that took the
    payment only in part, so no item of its own carries it. The denial is done on what is left of C024's payment,
    $87.60; the drawer must say the same on the store and on the dashboard's copy."""
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99214", "223.65", "117.60"), "20260301")
    w.remit([visit_denied_line(f"{BILL}C017", carc="16")], "20260415")
    w.remit([visit_paid(f"{BILL}C024", payer_ctl="PC300B")], "20260520")
    w.remit([visit_paid("CB0000900C017", who=OTHER, payer_ctl="PC900")], "20260803",
            plb=f"PLB*1777777772*20261231*WO:87654321 {BILL}C024*30.00~")
    w.run("r1", today="2026-09-20")
    (f,) = w.rows(f"patient_control_number = '{BILL}C017' AND COALESCE(stale, 0) = 0")
    copy = str(tmp_path / "serving.sqlite")
    cloud.build_serving(w.store.conn, copy)
    served = Store(copy)
    try:
        got = {}
        for where, conn in (("store", w.store.conn), ("dashboard's copy", served.conn)):
            st = story.explain(conn, f["finding_id"], today="2026-09-20")["state"]
            proof = st.get("proof") or {}
            got[where] = (st["work_state"], proof.get("cents"), proof.get("text"))
        assert got["store"][0] == "done" and got["store"][1] == 8760, got["store"]
        assert got["dashboard's copy"] == got["store"], f"the drawer names another proof on the dashboard's copy: {got}"
    finally:
        served.close()
        w.store.close()
