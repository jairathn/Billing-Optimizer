"""A finding to fix and resend for a payer without a published corrected-claim rule (Anthem commercial, UnitedHealthcare,
Aetna, the TPAs) carries the appeal window as its date, and its basis says the participation agreement's filing limit
applies instead, counted from the date of service and often 90 days. /api/brief puts such a finding in "Fix and resend
... before the date shown" and its data-health check counts only bases that say "not confirmed", so these are neither
shown apart nor counted as resting on an unconfirmed window. On the archive: 120 of the 453 fix items in window ($9,900),
114 of them for visits more than 90 days old ($8,700), 43 more than a year old ($5,200). Invented data only."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from r6cov_h import add_file, clean_visits, clm, pcn, sv  # noqa: E402

from remitwatch import insights  # noqa: E402
from remitwatch.codes import AGREEMENT_FILING_NOTE  # noqa: E402
from remitwatch.detect import Detector  # noqa: E402
from remitwatch.store import Store  # noqa: E402

TODAY = "2026-11-13"


def test_a_fix_whose_real_limit_is_the_agreement_is_flagged_as_unconfirmed(tmp_path):
    db = str(tmp_path / "s.sqlite")
    add_file(tmp_path, db, clean_visits(100, 10, "99213", "150.00", "90.00", "20251001"), "20251015")
    # a visit 400 days ago, denied for a missing modifier 380 days ago
    add_file(tmp_path, db, [clm(pcn(1), "1", sv("99213", "150.00", "0.00", cas="CAS*CO*16*150.00~LQ*HE*N822~", dos="20251009"), dos="20251009")],
             "20251029")
    st = Store(db)
    Detector(st, run_id="r6").run()
    sig, act, dl, basis = st.conn.execute("""SELECT signal, actionable, appeal_deadline, deadline_basis FROM findings
                                             WHERE patient_control_number=?""", (pcn(1),)).fetchone()
    assert (sig, act, dl) == ("DENIED_MISSING_MODIFIER", "correct_and_resubmit", "2027-10-29")
    assert AGREEMENT_FILING_NOTE.strip() in basis and "not confirmed" in AGREEMENT_FILING_NOTE      # the note now says so itself
    b = insights.brief(st.conn, today=TODAY)
    st.close()
    fix = next(t for t in b["work"]["types"] if t["key"] == "fix")
    assert fix["n"] == 1 and "before the date shown" in fix["how"]
    health = {h["key"]: h for h in b["health"]}
    assert "deadlines" in health and health["deadlines"]["n"] >= 1, \
        f"a corrected claim the agreement limits (often 90 days from a visit 400 days ago) is listed to fix before {dl} and not flagged: {sorted(health)}"
