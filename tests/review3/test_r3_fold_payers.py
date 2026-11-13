"""The one-finding-per-bill fold joins submissions of a bill to different payers. Invented data only."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from r3helpers import add, base, clm, ev, findings, show, sv  # noqa: E402

from remitwatch.actions import apply_status  # noqa: E402
from remitwatch.detect import Detector  # noqa: E402


def denied(pcn, carc, payer_ctl):
    return clm(pcn, "4", "223.65", "0.00", sv("99214", "223.65", "0.00", 1, f"CAS*CO*{carc}*223.65~"), payer_ctl=payer_ctl)


def run(store, run_id, now):
    d = Detector(store, run_id=run_id)
    d.now = now
    d.run()


def test_dismissing_the_wrong_payers_denial_does_not_hide_the_right_payers_denial(tmp_path):
    """Bill 300 goes to Anthem first (C017): denied CO-22, another payer is primary. The practice rebills Humana (C024):
    Humana denies it too, CO-16, missing information (2026-05-20). On 2026-05-25 the biller dismisses the Anthem finding
    ('wrong payer, rebilled to Humana'). Humana's denial is unpaid money that still needs a corrected claim."""
    base(tmp_path).close()
    store = add(tmp_path, [denied("CB0000300C017", "22", "ANT300")], "20260415")
    run(store, "t1", "2026-04-16T09:00:00+00:00")
    store.close()
    store = add(tmp_path, [denied("CB0000300C024", "16", "HUM300")], "20260520", payer="HUMANA", payer_id="HUMNA")
    run(store, "t2", "2026-05-21T09:00:00+00:00")
    rows = findings(store, "signal='CLAIM_DENIED_UNPAID'")
    show(rows, ("patient_control_number", "payer_name", "carc", "actionable", "appeal_deadline", "status"))
    print("evidence of the listed finding:", [ev(r).get("earlier_denied_submissions") for r in rows])
    anthem = next(r for r in rows if r["patient_control_number"] == "CB0000300C017")
    apply_status(store.conn, anthem["finding_id"], "dismissed", "Wrong payer: Anthem is not primary, rebilled to Humana", actor="biller",
                 at="2026-05-25T15:00:00+00:00")
    store.conn.commit()
    run(store, "t3", "2026-05-26T09:00:00+00:00")
    rows = findings(store, "signal='CLAIM_DENIED_UNPAID' AND COALESCE(stale,0)=0")
    show(rows, ("patient_control_number", "payer_name", "carc", "actionable", "status"))
    live = [r for r in rows if r["status"] not in ("dismissed", "lost", "resolved")]
    assert live, "Humana's denial of bill 300 is not listed anywhere: it was folded into the dismissed Anthem finding"
    store.close()


def test_the_listed_finding_names_the_payer_that_must_be_worked(tmp_path):
    """Before anyone acts: the bill is shown once, on Anthem's submission (CO-22, appeal, Anthem's deadline), while the live
    denial is Humana's (CO-16, correct and resubmit, Humana's deadline)."""
    base(tmp_path).close()
    store = add(tmp_path, [denied("CB0000300C017", "22", "ANT300")], "20260415")
    run(store, "t1", "2026-04-16T09:00:00+00:00")
    store.close()
    store = add(tmp_path, [denied("CB0000300C024", "16", "HUM300")], "20260520", payer="HUMANA", payer_id="HUMNA")
    run(store, "t2", "2026-05-21T09:00:00+00:00")
    rows = [r for r in findings(store, "signal='CLAIM_DENIED_UNPAID'") if r["status"] == "open"]
    show(rows, ("patient_control_number", "payer_name", "carc", "actionable", "appeal_deadline", "status"))
    assert any(r["payer_name"] == "HUMANA" for r in rows), "Humana's denial is only an entry in the evidence of Anthem's finding"
    store.close()


def test_a_secondarys_denial_of_the_same_claim_does_not_carry_the_primarys(tmp_path):
    """Crossover: Anthem (primary) denies C017 for no authorization (CO-197) on 2026-04-15; Humana (secondary) receives the
    same claim id and denies it on 2026-05-01 (CO-22: the primary has not paid). The bill is shown once, on Humana's
    denial. The biller dismisses Humana's ('secondary: nothing to do until the primary pays'); Anthem's denial, the one
    to appeal, must still be listed."""
    base(tmp_path).close()
    store = add(tmp_path, [denied("CB0000300C017", "197", "ANT300")], "20260415")
    store.close()
    store = add(tmp_path, [denied("CB0000300C017", "22", "HUM300")], "20260501", payer="HUMANA", payer_id="HUMNA")
    run(store, "t1", "2026-05-02T09:00:00+00:00")
    rows = findings(store, "signal='CLAIM_DENIED_UNPAID'")
    show(rows, ("patient_control_number", "payer_name", "carc", "actionable", "appeal_deadline", "status"))
    shown = [r for r in rows if r["status"] == "open"]
    humana = next((r for r in shown if r["payer_name"] == "HUMANA"), None)
    if humana is not None:
        apply_status(store.conn, humana["finding_id"], "dismissed", "Secondary denial: waits on the primary", actor="biller",
                     at="2026-05-03T15:00:00+00:00")
        store.conn.commit()
    run(store, "t2", "2026-05-04T09:00:00+00:00")
    rows = findings(store, "signal='CLAIM_DENIED_UNPAID' AND COALESCE(stale,0)=0")
    show(rows, ("patient_control_number", "payer_name", "carc", "actionable", "status"))
    assert any(r["payer_name"] == "ANTHEM" and r["status"] == "open" for r in rows), "Anthem's CO-197 denial is not listed: folded into Humana's"
    store.close()
