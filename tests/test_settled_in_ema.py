"""A claim no 835 answered is not chased when EMA shows its own EMA claim paid, though other patients' EMA claims carry the
same day, code and fee (the August 2026 hand test of the release, 2026-11-27: 66 Work-now items, $21,801, whose visits
EMA shows paid under the same patient's EMA claim: the content bridge saw several EMA claims of that content and chose
none). The claim's own patient, from its 837, chooses. Invented data, both store kinds; the control: the paid EMA claim
is another patient's, and the claim stays listed."""
from __future__ import annotations

import datetime as dt

import pytest

from remitwatch.detect import Detector
from tests.test_837_sends import KINDS, send
from tests.test_fx_837 import clm837
from tests.test_fx_exports import _ema_store, _volume, ema_env  # noqa: F401

D = dt.date
BLUE = ("BLUE CROSS TEST", "BCT01")


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("same_patient", [True, False])
def test_a_never_answered_claim_whose_own_ema_claim_was_paid_is_not_chased(tmp_path, kind, ema_env, same_patient):
    """Casey Rebilltest's 99214 of March 2 ($200) went to Blue Cross as claim F.claim_id(60): its 837 of March 5 names the
    patient. EMA charged the visit on February 20 (held for coding) as EMA claim 61, which Blue Cross paid $90 on April 1
    (CO-45 $110): settled. EMA claim 62, Riley Sametest's 99214 of the same day at the same fee, was charged the same day
    and never paid. No 835 answers claim 60. The patient names EMA claim 61 as claim 60's own: answered, nothing to chase.
    The control: EMA claim 61 is a third patient's visit; nothing names claim 60's EMA claim, and it stays listed."""
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import adj, charge, denial_world, pay
    from tests.test_workable import ema
    s, lg = _ema_store(tmp_path, kind)
    who, dob = "Rebilltest, Casey", D(1970, 1, 1)
    o_who, o_dob = (who, dob) if same_patient else ("Othertest, Jordan", D(1981, 6, 6))
    mar2, feb20, bcbs = D(2026, 3, 2), D(2026, 2, 20), F.PAYERS["bcbs"]
    rows = [charge(F.link_no(61), cpt="99214", amount=200, patient=o_who, svc=mar2, posted=feb20, dob=o_dob),
            pay(F.link_no(61), "BC-61", 90, cpt="99214", patient=o_who, svc=mar2, posted=D(2026, 4, 1), payer=bcbs, dob=o_dob),
            adj(F.link_no(61), "CO-45", 110, cpt="99214", patient=o_who, svc=mar2, posted=D(2026, 4, 1), contractual=True,
                payer=bcbs, dob=o_dob),
            charge(F.link_no(62), cpt="99214", amount=200, patient="Sametest, Riley", svc=mar2, posted=feb20, dob=D(1990, 9, 9))]
    denial_world(s, lg, tmp_path, rows)
    _volume(s)
    send(s, tmp_path, [clm837(F.claim_id(60), [("99214", "200.00", "20260302")], payer=BLUE, member="MREB000060",
                              subscriber=("REBILLTEST", "CASEY", "19700101"))], "20260305")
    bc = dict(payer=bcbs, payer_id="BCT01", patient_id="EMA060")
    ema(s.conn, F.claim_id(60), cpt="99214", service_date="2026-03-02", bill_created="2026-02-20", charges_cents=20000, **bc)
    ema(s.conn, F.claim_id(60), family="claim_status", claim_status="Submitted", claim_submission_date="2026-03-05",
        service_date="2026-03-02", cpt="99214", **bc)
    s.conn.commit()
    det = Detector(s, run_id="r1", today="2026-05-25")
    det.run()
    assert det.ema_errors == [], det.ema_errors
    lg.close()
    listed = [r["signal"] for r in s.query("SELECT signal FROM findings WHERE COALESCE(stale,0)=0 AND status <> 'resolved' "
                                           "AND patient_control_number=?", (F.claim_id(60),))]
    s.close()
    if same_patient:
        assert not listed, f"EMA shows claim 60's own EMA claim paid (Blue Cross, April 1), yet claim 60 is chased: {listed}"
    else:
        assert listed, "nothing on file answers claim 60: it must stay listed"
