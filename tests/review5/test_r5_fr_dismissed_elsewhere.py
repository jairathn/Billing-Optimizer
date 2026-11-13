"""A whole-claim denial and the corrected claim's line denial of the same visit, both listed by the live release (3f8855b):
the biller keeps one and dismisses the other. Then the release's (752c700) first analysis of that store. Invented data."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from frh import LIVE, REL, act, add_to, base, brief, clm, copy_db, detect, owed, sv  # noqa: E402

PFX = "CB0000985"


def story(tmp_path, keep, dismiss):
    """C017 (99214) denied whole (CO-16) on 04-15. The corrected C024 is processed on 05-06 with the visit denied again at
    line level (CO-16, claim status 1). Live lists both. On 05-12 the biller works `keep` (in progress) and dismisses
    `dismiss` as the same visit listed twice. Nothing has been paid."""
    base(tmp_path)
    db = str(tmp_path / "s.sqlite")
    add_to(tmp_path, db, [clm(f"{PFX}C017", "4", "223.65", "0.00", sv("99214", "223.65", "0.00", cas="CAS*CO*16*223.65~"), payer_ctl="A985A")],
           "20260415")
    detect(LIVE, db, "live-1", "2026-04-16T09:00:00+00:00")
    add_to(tmp_path, db, [clm(f"{PFX}C024", "1", "223.65", "0.00", sv("99214", "223.65", "0.00", cas="CAS*CO*16*223.65~"), payer_ctl="A985B")],
           "20260506")
    live1 = detect(LIVE, db, "live-2", "2026-05-07T09:00:00+00:00")
    brief("live, before anyone acts", live1, PFX)
    act(LIVE, db, keep[0], keep[1], "in_progress", at="2026-05-12T15:00:00+00:00")
    act(LIVE, db, dismiss[0], dismiss[1], "dismissed", at="2026-05-12T15:01:00+00:00")
    live_db, rel_db = copy_db(db, str(tmp_path / "live.sqlite")), copy_db(db, str(tmp_path / "rel.sqlite"))
    live = detect(LIVE, live_db, "live-3", "2026-05-19T09:00:00+00:00")
    rel = detect(REL, rel_db, "rel-1", "2026-05-19T09:00:00+00:00")
    brief("live, next run", live, PFX)
    brief("release, first run after deploy", rel, PFX)
    rel2 = detect(REL, rel_db, "rel-2", "2026-05-26T09:00:00+00:00")
    brief("release, a week later (nothing new)", rel2, PFX)
    print("live owed:", owed(live, PFX), " release owed:", owed(rel, PFX), " a week later:", owed(rel2, PFX))
    return live, rel, rel2


def test_whole_claim_kept_in_progress_line_denial_dismissed(tmp_path):
    live, rel, rel2 = story(tmp_path, keep=(f"{PFX}C017", "CLAIM_DENIED_UNPAID"), dismiss=(f"{PFX}C024", "DENIED_MISSING_INFO"))
    assert owed(live, PFX) == (1, 11760)
    assert owed(rel, PFX) == owed(live, PFX), "the visit the biller is working (in progress) is gone from the list: made stale"


def test_control_line_denial_kept_whole_claim_dismissed(tmp_path):
    live, rel, rel2 = story(tmp_path, keep=(f"{PFX}C024", "DENIED_MISSING_INFO"), dismiss=(f"{PFX}C017", "CLAIM_DENIED_UNPAID"))
    assert owed(rel, PFX) == owed(live, PFX)


def test_whole_claim_in_progress_line_denial_left_open(tmp_path):
    """The biller works the whole-claim denial (in progress) and leaves the corrected claim's line denial open. The money
    stays listed (the line denial); where does the biller's in-progress status end up?"""
    base(tmp_path)
    db = str(tmp_path / "s.sqlite")
    add_to(tmp_path, db, [clm(f"{PFX}C017", "4", "223.65", "0.00", sv("99214", "223.65", "0.00", cas="CAS*CO*16*223.65~"), payer_ctl="A985A")],
           "20260415")
    detect(LIVE, db, "live-1", "2026-04-16T09:00:00+00:00")
    add_to(tmp_path, db, [clm(f"{PFX}C024", "1", "223.65", "0.00", sv("99214", "223.65", "0.00", cas="CAS*CO*16*223.65~"), payer_ctl="A985B")],
           "20260506")
    detect(LIVE, db, "live-2", "2026-05-07T09:00:00+00:00")
    act(LIVE, db, f"{PFX}C017", "CLAIM_DENIED_UNPAID", "in_progress", at="2026-05-12T15:00:00+00:00")
    rel = detect(REL, db, "rel-1", "2026-05-19T09:00:00+00:00")
    brief("release, first run after deploy", rel, PFX)
    mine = [r for r in rel if r["patient_control_number"] == f"{PFX}C017"]
    print("the biller's finding:", [(r["status"], r["stale"]) for r in mine], " owed:", owed(rel, PFX))
    # Deliberate: the denial the biller is working stays listed (with a note naming the corrected claim's line denial)
    # rather than leaving the work list as a stale row; the line denial is listed too, as on the live release.
    assert owed(rel, PFX) == (2, 23520) and any((r["hint"] or "").startswith("Denied again as a line on claim") for r in mine if not r["stale"])
