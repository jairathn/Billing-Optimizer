"""run_claim_rules' settled(): "billed again under another date" fires for a code and date whenever any later line of
the bill has the code on some other date and none on this one. On a bill that carries one code on several dates (a
phototherapy series), a corrected claim that leaves one date off hides that date's denial. Invented data only.
Run from the repository root."""
from __future__ import annotations

import sys

import os  # noqa: E402
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from tests.review4.lvn import add, base, both, clean_visits, clm, money, show, sv  # noqa: E402

PHOTO = clean_visits(501, 25, "96910", "120.00", "60.00")       # the payer has a usual rate for 96910


def series(pcn, status, dates, paid="0.00", cas="CAS*CO*16*120.00~", allowed=None, payer_ctl="ANT970A"):
    lines = "".join(sv("96910", "120.00", paid, cas=cas, dos=d, allowed=allowed) for d in dates)
    total = f"{120 * len(dates):.2f}"
    claim_paid = f"{float(paid) * len(dates):.2f}"
    return clm(pcn, status, total, claim_paid, lines, payer_ctl=payer_ctl, dos=dates[0])


def covers(rows, pcn_prefix, dos):
    """Open findings (any signal) that list 96910 on this date for this bill."""
    import json
    out = []
    for r in rows:
        if r.get("stale") or r["status"] not in ("open", "in_progress", "appealed") or not (r.get("patient_control_number") or "").startswith(pcn_prefix):
            continue
        if r.get("procedure_code") == "96910" and r.get("service_date") == dos:
            out.append(r)
        elif r["signal"] == "CLAIM_DENIED_UNPAID" and "96910" in (json.loads(r["evidence"] or "{}").get("codes") or []):
            out.append(r)       # a whole-claim finding lists its codes, all dates
    return out


def test_corrected_series_leaves_one_date_off_and_is_denied_again(tmp_path):
    """C017 bills three phototherapy sessions (96910 on 04-01, 04-03, 04-06) and is denied whole on 2026-04-15 (CO-16,
    missing referring provider). The corrected C024 (2026-05-20) carries only 04-03 and 04-06, and both are denied again
    at line level (CO-16). The 04-01 session was never paid nor billed again. Live lists it within C017's finding; the
    release takes it as 'billed again under another date' and lists it nowhere."""
    base(tmp_path, extra=PHOTO)
    add(tmp_path, [series("CB0000970C017", "4", ["20260401", "20260403", "20260406"])], "20260415")
    add(tmp_path, [series("CB0000970C024", "1", ["20260403", "20260406"], payer_ctl="ANT970B")], "20260520")
    live, rel = both(tmp_path)
    show("live", live)
    show("release", rel)
    print("live owed:", money(live), " release owed:", money(rel))
    assert covers(live, "CB0000970", "2026-04-01"), "precondition: live lists the 04-01 session (in C017's whole-claim finding)"
    assert covers(rel, "CB0000970", "2026-04-01"), "the release lists the 04-01 session nowhere"


def test_corrected_series_leaves_one_date_off_and_is_paid(tmp_path):
    """The same with C024 paid for 04-03 and 04-06. Live hides the bill (a later payment). The release's deliberate rule
    is that a line left off a corrected claim stays listed with a note naming that claim: is 04-01 listed?"""
    base(tmp_path, extra=PHOTO)
    add(tmp_path, [series("CB0000971C017", "4", ["20260401", "20260403", "20260406"])], "20260415")
    add(tmp_path, [series("CB0000971C024", "1", ["20260403", "20260406"], paid="60.00", cas="CAS*CO*45*60.00~", allowed="60.00",
                          payer_ctl="ANT971B")], "20260520")
    live, rel = both(tmp_path)
    show("live", live)
    show("release", rel)
    print("live 04-01:", [r["signal"] for r in covers(live, "CB0000971", "2026-04-01")],
          " release 04-01:", [(r["signal"], r.get("hint")) for r in covers(rel, "CB0000971", "2026-04-01")])
    assert covers(rel, "CB0000971", "2026-04-01"), "the session left off the corrected claim is not listed (deliberate rule says it is)"
