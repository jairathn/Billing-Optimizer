"""run_claim_rules' settled(): a line another payer processed at $0 as SECONDARY (CLP02 = 2) is taken for "denied as a
line on another claim (that denial is its own finding)". The line rules never list a secondary's line (they skip any
claim that is not primary), so the primary's whole-claim denial is hidden and nothing lists the service. The live
release (3f8855b) lists the primary's denial. Invented data only: names, member numbers, claim ids, payer ids, amounts.
Run from the repository root."""
from __future__ import annotations

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from tests.review4.lvn import add, base, both, clm, money, show, sv  # noqa: E402


def test_primary_denies_whole_claim_secondary_processes_crossover_at_zero(tmp_path):
    """Anthem (primary) denies C017 whole on 2026-04-15: 99214, CO-16 (missing information: correct and resubmit). The
    claim crosses over to the patient's secondary plan, which processes it as secondary (CLP02 = 2) on 2026-04-29 and
    pays $0 because the primary paid nothing (OA-23). Nothing has been paid: the primary's denial is money to recover."""
    base(tmp_path)
    add(tmp_path, [clm("CB0000960C017", "4", "223.65", "0.00", sv("99214", "223.65", "0.00", cas="CAS*CO*16*223.65~"),
                       payer_ctl="ANT960A")], "20260415")
    add(tmp_path, [clm("CB0000960C017", "2", "223.65", "0.00", sv("99214", "223.65", "0.00", cas="CAS*OA*23*223.65~"),
                       payer_ctl="SEC960A", cfi="CI")], "20260429", payer="SECONDARY HEALTH PLAN", payer_id="SECHP")
    live, rel = both(tmp_path)
    show("live", live)
    show("release", rel)
    print("live owed:", money(live), " release owed:", money(rel))
    mine = lambda r: (r.get("patient_control_number") or "").startswith("CB0000960")       # noqa: E731
    assert money(live, mine)[0] == 1, "precondition: the live release lists the primary's whole-claim denial"
    assert money(rel, mine) == money(live, mine), \
        "the release hides the primary's whole-claim denial because the secondary processed the crossover at $0"


def test_first_release_run_hides_the_appeal_a_biller_filed_on_the_live_release(tmp_path):
    """The same story on a store the live release analysed: it lists C017's denial on 04-30, and the biller appeals it on
    05-02 (status 'appealed', with a note). The first run after deploy no longer produces the finding: the biller's appeal
    is kept only as a stale row, out of the work queue and every total, while the $117.60 is still unpaid."""
    from tests.review4.lvn import LIVE, REL, act, detect
    base(tmp_path)
    add(tmp_path, [clm("CB0000962C017", "4", "223.65", "0.00", sv("99214", "223.65", "0.00", cas="CAS*CO*16*223.65~"),
                       payer_ctl="ANT962A")], "20260415")
    add(tmp_path, [clm("CB0000962C017", "2", "223.65", "0.00", sv("99214", "223.65", "0.00", cas="CAS*OA*23*223.65~"),
                       payer_ctl="SEC962A", cfi="CI")], "20260429", payer="SECONDARY HEALTH PLAN", payer_id="SECHP")
    db = str(tmp_path / "s.sqlite")
    live = detect(LIVE, db, "live-1", "2026-04-30T09:00:00+00:00")
    assert [r["status"] for r in live if r["signal"] == "CLAIM_DENIED_UNPAID"] == ["open"], "precondition: listed by live"
    act(LIVE, db, "CB0000962C017", "CLAIM_DENIED_UNPAID", "appealed", "Corrected and appealed to Anthem", at="2026-05-02T15:00:00+00:00")
    rel = detect(REL, db, "rel-1", "2026-05-05T09:00:00+00:00")
    mine = [r for r in rel if r["signal"] == "CLAIM_DENIED_UNPAID" and r["patient_control_number"] == "CB0000962C017"]
    print("release rows:", [(r["status"], r["stale"], r["notes"]) for r in mine])
    assert [(r["status"], r["stale"]) for r in mine] == [("appealed", 0)], \
        "the biller's appeal of an unpaid whole-claim denial left the work queue on the first run after deploy (stale)"


def test_control_without_the_secondary_both_list_it(tmp_path):
    """Control: the same denial with no secondary processing is listed by both releases."""
    base(tmp_path)
    add(tmp_path, [clm("CB0000961C017", "4", "223.65", "0.00", sv("99214", "223.65", "0.00", cas="CAS*CO*16*223.65~"),
                       payer_ctl="ANT961A")], "20260415")
    live, rel = both(tmp_path)
    mine = lambda r: (r.get("patient_control_number") or "").startswith("CB0000961")       # noqa: E731
    print("live owed:", money(live, mine), " release owed:", money(rel, mine))
    assert money(live, mine)[0] == 1 and money(rel, mine) == money(live, mine)
