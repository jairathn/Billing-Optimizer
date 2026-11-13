"""save(): 'A reopening by the analysis ends a person's earlier override or confirmation'. The SQL that builds the
overridden and confirmed sets applies that only to findings whose decision was made under their own id: a decision
carried through a signal change ('override_carried', 'confirmed_carried') counts forever, reopening or not. Invented
data only. Run from the repository root."""
from __future__ import annotations

import sys

import os  # noqa: E402
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from remitwatch.actions import apply_status  # noqa: E402
from remitwatch.detect import Detector  # noqa: E402
from remitwatch.x12_837 import DDL as DDL_837  # noqa: E402
from tests.test_features import build_store, claim, clean_visits, svc  # noqa: E402


def run(store, run_id, now):
    d = Detector(store, run_id=run_id)
    d.now = now
    d.run()


def downcoding(store):
    return [dict(r) for r in store.query("""SELECT finding_id, signal, status, notes, hint, auto_note, recovered_cents, stale
                                            FROM findings WHERE category='downcoding' AND patient_control_number LIKE 'CB0000200%'
                                            AND COALESCE(stale,0)=0""")]


def events(store, fid):
    return [tuple(r) for r in store.query("SELECT kind, from_status, to_status, at FROM finding_events WHERE finding_id=? ORDER BY id", (fid,))]


def c(pcn, line, pay_date, tmp_path):
    return build_store(tmp_path, [claim(200, line).replace("CB0000200C017", pcn)], pay_date=pay_date)


def story(tmp_path):
    """C017: a 99213 paid $76.15 on the practice's 99214 charge (DOWNCODE_CHARGE). The corrected C024 is paid as 99214
    ($117.60) on 05-20 and the run of 05-21 closes the finding. The biller records what was received the page's way
    (in progress, then recovered $41.45) on 05-22. The 837 for C017 then arrives (the line's signal becomes
    DOWNCODE_VS_BILLED, run of 05-28). On 06-10 the payer reverses C024's payment; the run of 06-11 reopens the finding."""
    claims = clean_visits(1, 25, "99214", "223.65", "117.60") + clean_visits(100, 25, "99213", "156.66", "76.15")
    claims.append(claim(200, svc("99213", "223.65", "76.15", "76.15", "CAS*CO*45*147.50~")))
    build_store(tmp_path, claims, pay_date="20260415").close()
    store = c("CB0000200C024", svc("99214", "223.65", "117.60", "117.60", "CAS*CO*45*106.05~"), "20260520", tmp_path)
    run(store, "r1", "2026-05-21T09:00:00+00:00")
    [f] = downcoding(store)
    assert (f["signal"], f["status"]) == ("DOWNCODE_CHARGE", "resolved"), f
    apply_status(store.conn, f["finding_id"], "in_progress", None, actor="biller", at="2026-05-22T15:00:00+00:00")
    apply_status(store.conn, f["finding_id"], "resolved", None, 4145, actor="biller", at="2026-05-22T15:00:30+00:00")
    store.conn.commit()
    store.conn.executescript(DDL_837)
    store.conn.execute("INSERT INTO lines_837 (line_837_key, claim_837_key, patient_control_number, line_seq, procedure_code, charge_cents, units, "
                       "service_date) VALUES ('k1','c1','CB0000200C017',1,'99214',22365,1,'2026-04-01')")
    store.conn.commit()
    run(store, "r2", "2026-05-28T09:00:00+00:00")
    [f2] = downcoding(store)
    print("after the claim file:", f2, events(store, f2["finding_id"]))
    assert (f2["signal"], f2["status"], f2["recovered_cents"]) == ("DOWNCODE_VS_BILLED", "resolved", 4145), "precondition: the recovery follows the line"
    store.close()
    rev = claim(200, svc("99214", "-223.65", "-117.60", None, "CAS*CO*45*-106.05~").replace("**1~", "**-1~"), status="22")
    store = build_store(tmp_path, [rev.replace("CB0000200C017", "CB0000200C024")], pay_date="20260610")
    run(store, "r3", "2026-06-11T09:00:00+00:00")
    [f3] = downcoding(store)
    print("after the reversal:", f3)
    assert f3["status"] == "open" and "taken back" in (f3["hint"] or ""), "precondition: reopened by the takeback"
    return store, f3["finding_id"]


def test_a_recovery_recorded_after_the_takeback_is_reopened_every_run_once_the_line_changed_signal(tmp_path):
    """06-20: the payer settles by paper check; the biller marks the finding recovered ($41.45, with a note). The run of
    06-21 has nothing new. Without the signal change this recovery stands (tests/review4/test_r4_statuses.py)."""
    store, fid = story(tmp_path)
    apply_status(store.conn, fid, "resolved", "Paper check 5521", 4145, actor="biller", at="2026-06-20T15:00:00+00:00")
    store.conn.commit()
    run(store, "r4", "2026-06-21T09:00:00+00:00")
    [f] = downcoding(store)
    print("after r4:", f, events(store, fid))
    assert (f["status"], f["recovered_cents"]) == ("resolved", 4145), \
        f"the biller's recovery, recorded after the takeback, was set to {f['status']} by the analysis ({f['hint']!r})"
    store.close()


def test_a_reopened_finding_is_never_closed_again_once_the_line_changed_signal(tmp_path):
    """07-15: after the practice's appeal the payer pays C024 again ($117.60). The service is paid; nobody set the finding
    open (the analysis did, on 06-11), yet the override carried with the signal change still holds it open."""
    store, fid = story(tmp_path)
    store.close()
    store = c("CB0000200C024", svc("99214", "223.65", "117.60", "117.60", "CAS*CO*45*106.05~"), "20260715", tmp_path)
    run(store, "r4", "2026-07-16T09:00:00+00:00")
    [f] = downcoding(store)
    print("after the payment of 07-15:", f, events(store, fid))
    assert f["status"] == "resolved", f"paid again on 07-15 but {f['status']}: {f['hint']!r}"
    store.close()
