"""One payer's repeated whole-claim denials of a bill, worked under the live release (3f8855b), which lists every denied
submission on its own; then the release's (752c700) first analysis of that store, and a week later. Statuses are set
with live's apply_status, as the live page does (a status button sends no note). Invented data only."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from frh import LIVE, REL, act, add_to, base, brief, clm, copy_db, detect, owed, sv  # noqa: E402

PFX = "CB0000980"


def denied(pcn, ctl, carc="16"):
    return clm(pcn, "4", "223.65", "0.00", sv("99214", "223.65", "0.00", cas=f"CAS*CO*{carc}*223.65~"), payer_ctl=ctl)


def story(tmp_path, dismiss, keep_open):
    """C017 denied whole (CO-16) on 04-15; the practice resubmits; C024 denied whole again (CO-16) on 05-06. Live lists both
    denials. On 05-12 the biller tidies the double listing: `dismiss` is dismissed, `keep_open` is left open to be worked.
    Nothing has been paid on the bill."""
    base(tmp_path)
    db = str(tmp_path / "s.sqlite")
    add_to(tmp_path, db, [denied(f"{PFX}C017", "A980A")], "20260415")
    detect(LIVE, db, "live-1", "2026-04-16T09:00:00+00:00")
    add_to(tmp_path, db, [denied(f"{PFX}C024", "A980B")], "20260506")
    live1 = detect(LIVE, db, "live-2", "2026-05-07T09:00:00+00:00")
    brief("live, before anyone acts", live1, PFX)
    assert owed(live1, PFX)[0] == 2
    act(LIVE, db, dismiss, "CLAIM_DENIED_UNPAID", "dismissed", at="2026-05-12T15:00:00+00:00")
    live_db, rel_db = copy_db(db, str(tmp_path / "live.sqlite")), copy_db(db, str(tmp_path / "rel.sqlite"))
    live = detect(LIVE, live_db, "live-3", "2026-05-19T09:00:00+00:00")
    rel = detect(REL, rel_db, "rel-1", "2026-05-19T09:00:00+00:00")
    brief("live, next run", live, PFX)
    brief("release, first run after deploy", rel, PFX)
    rel2 = detect(REL, rel_db, "rel-2", "2026-05-26T09:00:00+00:00")
    brief("release, a week later (nothing new)", rel2, PFX)
    print("live owed:", owed(live, PFX), " release owed:", owed(rel, PFX), " a week later:", owed(rel2, PFX))
    return live, rel, rel2


def test_the_older_listing_dismissed_hides_the_newer_one_left_open(tmp_path):
    live, rel, rel2 = story(tmp_path, dismiss=f"{PFX}C017", keep_open=f"{PFX}C024")
    assert [r["status"] for r in live if r["patient_control_number"] == f"{PFX}C024"] == ["open"]
    assert owed(rel, PFX) == owed(live, PFX), "the open denial the biller kept to work is gone from the list (folded into the dismissed one)"


def test_the_newer_listing_dismissed_hides_the_older_one_left_open(tmp_path):
    live, rel, rel2 = story(tmp_path, dismiss=f"{PFX}C024", keep_open=f"{PFX}C017")
    assert [r["status"] for r in live if r["patient_control_number"] == f"{PFX}C017"] == ["open"]
    assert owed(rel, PFX) == owed(live, PFX), "the open denial the biller kept to work is gone from the list (folded into the dismissed one)"


def test_control_dismissed_before_the_second_denial_arrived(tmp_path):
    """Control: the biller dismisses C017 on 04-20, before C024's denial (05-06) arrives. The release lists C024 on its own
    (denied again after someone acted), as live does."""
    base(tmp_path)
    db = str(tmp_path / "s.sqlite")
    add_to(tmp_path, db, [denied(f"{PFX}C017", "A980A")], "20260415")
    detect(LIVE, db, "live-1", "2026-04-16T09:00:00+00:00")
    act(LIVE, db, f"{PFX}C017", "CLAIM_DENIED_UNPAID", "dismissed", at="2026-04-20T15:00:00+00:00")
    add_to(tmp_path, db, [denied(f"{PFX}C024", "A980B")], "20260506")
    detect(LIVE, db, "live-2", "2026-05-07T09:00:00+00:00")
    live_db, rel_db = copy_db(db, str(tmp_path / "live.sqlite")), copy_db(db, str(tmp_path / "rel.sqlite"))
    live = detect(LIVE, live_db, "live-3", "2026-05-19T09:00:00+00:00")
    rel = detect(REL, rel_db, "rel-1", "2026-05-19T09:00:00+00:00")
    brief("live", live, PFX)
    brief("release", rel, PFX)
    assert owed(rel, PFX) == owed(live, PFX) == (1, 11760)
