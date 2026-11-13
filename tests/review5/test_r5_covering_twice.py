"""run_claim_rules' covering for listed denials (`if prev and pays`, was `prev["worked"]`): every previously listed
whole-claim denial of a bill that the bill has since paid is closed by that payment and records it as recovered. Two
listed submissions of one visit record one payment twice. Invented data only. Run from the repository root."""
from __future__ import annotations

import sys

import os  # noqa: E402
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from tests.review4.lvn import LIVE, REL, add, base, clm, detect, show, sv  # noqa: E402
from tests.review4.r4h import apply_status, brief, rows, run  # noqa: E402
from tests.review4.r4h import add as add_open  # noqa: E402
from tests.review4.r4h import base as base_open  # noqa: E402


def denied(pcn, carc="16", ctl="ANT1"):
    return clm(pcn, "4", "223.65", "0.00", sv("99214", "223.65", "0.00", cas=f"CAS*CO*{carc}*223.65~"), payer_ctl=ctl)


def paid(pcn, ctl="ANT9"):
    return clm(pcn, "1", "223.65", "117.60", sv("99214", "223.65", "117.60", cas="CAS*CO*45*106.05~", allowed="117.60"), payer_ctl=ctl)


def recovered(rs, prefix):
    sel = [r for r in rs if (r.get("patient_control_number") or "").startswith(prefix) and r["status"] == "resolved" and r.get("auto_note")]
    return len(sel), sum(r.get("recovered_cents") or 0 for r in sel)


def test_first_release_run_records_one_payment_as_recovered_on_each_live_listed_submission(tmp_path):
    """The live release lists C017 (denied 04-15) and the resubmission C024 (denied again 05-10) as two whole-claim
    findings for one visit. The corrected C031 is paid $117.60 on 06-01, after the live release's last run. The release's
    first run closes both findings, each recording $117.60 recovered: $235.20 for one $117.60 payment."""
    base(tmp_path)
    add(tmp_path, [denied("CB0000995C017", ctl="A995A")], "20260415")
    add(tmp_path, [denied("CB0000995C024", ctl="A995B")], "20260510")
    db = str(tmp_path / "s.sqlite")
    live = detect(LIVE, db, "live-1", "2026-05-11T09:00:00+00:00")
    show("live", live)
    assert len([r for r in live if r["signal"] == "CLAIM_DENIED_UNPAID" and r["status"] == "open"]) == 2, "precondition: live lists both"
    add(tmp_path, [paid("CB0000995C031", ctl="A995C")], "20260601")
    rel = detect(REL, db, "rel-1", "2026-06-02T09:00:00+00:00")
    show("release", rel)
    n, cents = recovered(rel, "CB0000995")
    print("findings closed as recovered:", n, "recovered:", cents)
    assert cents <= 11760, f"one payment of $117.60 recorded as ${cents/100:.2f} recovered across {n} findings"


def test_release_alone_an_appealed_carrier_and_its_repeat_both_record_the_payment(tmp_path):
    """Release only: C017 denied (04-15) and appealed (05-01); C024 denied again (05-10) is listed apart (the repeat).
    C031 is paid on 06-01: both findings close, each recording $117.60."""
    base_open(tmp_path).close()
    store = add_open(tmp_path, [denied("CB0000300C017")], "20260415")
    run(store, "t1", "2026-04-16T09:00:00+00:00")
    [f] = rows(store, "signal='CLAIM_DENIED_UNPAID'")
    apply_status(store.conn, f["finding_id"], "appealed", "Appeal faxed", actor="biller", at="2026-05-01T15:00:00+00:00")
    store.conn.commit()
    store.close()
    store = add_open(tmp_path, [denied("CB0000300C024", "50")], "20260510")
    run(store, "t2", "2026-05-11T09:00:00+00:00")
    brief(rows(store, "signal='CLAIM_DENIED_UNPAID'"))
    store.close()
    store = add_open(tmp_path, [paid("CB0000300C031")], "20260601")
    run(store, "t3", "2026-06-02T09:00:00+00:00")
    rs = rows(store, "signal='CLAIM_DENIED_UNPAID'")
    brief(rs)
    n, cents = recovered(rs, "CB0000300")
    print("closed as recovered:", n, cents)
    assert cents <= 11760, f"one payment of $117.60 recorded as ${cents/100:.2f} recovered across {n} findings"
    store.close()
