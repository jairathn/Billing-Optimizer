"""save() reading rows the live release (3f8855b) left. The live release writes no event when it creates a finding that
is already paid (a closure 'at first sight': its save() logs only when a row it had changes status), and its status
action leaves the automatic note on the row. The release recognises a person's override only through a
'paid_on_later_remit' event, so a person who moved such a finding away from 'resolved' is overridden by the first
analysis after deploy. Invented data only. Run from the repository root."""
from __future__ import annotations

import sqlite3
import sys

import os  # noqa: E402
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from tests.review4.lvn import LIVE, REL, act, add, base, clm, copy_db, detect, show, sv  # noqa: E402


def by(rows, pcn, signal):
    out = [r for r in rows if r["patient_control_number"] == pcn and r["signal"] == signal and not r.get("stale")]
    assert len(out) == 1, out
    return out[0]


def events(db, fid):
    c = sqlite3.connect(db)
    out = c.execute("SELECT kind, from_status, to_status, at FROM finding_events WHERE finding_id=? ORDER BY id", (fid,)).fetchall()
    c.close()
    return out


def story(tmp_path, status, notes):
    """C017's visit denied CO-16 (04-15) and the corrected C024 paid (05-20) both arrive before the live release's run of
    05-25, which creates the denial finding already resolved, with no event. On 05-26 a biller who knows the 05-20
    payment is for something else moves it to `status`."""
    base(tmp_path)
    add(tmp_path, [clm("CB0000980C017", "1", "223.65", "0.00", sv("99214", "223.65", "0.00", cas="CAS*CO*16*223.65~"), payer_ctl="ANT980A")],
        "20260415")
    add(tmp_path, [clm("CB0000980C024", "1", "223.65", "117.60", sv("99214", "223.65", "117.60", cas="CAS*CO*45*106.05~", allowed="117.60"),
                       payer_ctl="ANT980B")], "20260520")
    db = str(tmp_path / "s.sqlite")
    live = detect(LIVE, db, "live-1", "2026-05-25T09:00:00+00:00")
    f = by(live, "CB0000980C017", "DENIED_MISSING_INFO")
    assert f["status"] == "resolved" and f["auto_note"], "precondition: closed at first sight by the live release"
    assert events(db, f["finding_id"]) == [], "precondition: the live release wrote no event for that closure"
    act(LIVE, db, "CB0000980C017", "DENIED_MISSING_INFO", status, notes, at="2026-05-26T15:00:00+00:00")
    print("events before deploy:", events(db, f["finding_id"]))
    return db, f["finding_id"]


def test_appealed_after_a_first_sight_closure_is_closed_again_by_the_first_release_run(tmp_path):
    db, fid = story(tmp_path, "appealed", "Appeal faxed: the 05-20 payment was for the 05-02 visit")
    again = copy_db(db, str(tmp_path / "live2.sqlite"))
    live2 = by(detect(LIVE, again, "live-2", "2026-06-02T09:00:00+00:00"), "CB0000980C017", "DENIED_MISSING_INFO")
    rel = by(detect(REL, db, "rel-1", "2026-06-02T09:00:00+00:00"), "CB0000980C017", "DENIED_MISSING_INFO")
    print("live next run:", live2["status"], live2.get("hint"), "| release first run:", rel["status"], rel.get("hint"))
    print("events after the release's run:", events(db, fid))
    assert rel["status"] == "appealed", f"the biller's 'appealed' became {rel['status']} on the first analysis after deploy"


def test_open_with_a_note_after_a_first_sight_closure_is_closed_again(tmp_path):
    db, fid = story(tmp_path, "open", "Not paid: the 05-20 payment was for the 05-02 visit")
    rel = by(detect(REL, db, "rel-1", "2026-06-02T09:00:00+00:00"), "CB0000980C017", "DENIED_MISSING_INFO")
    print("release first run:", rel["status"], rel.get("notes"), rel.get("hint"))
    assert rel["status"] == "open", f"the biller's reopening became {rel['status']} on the first analysis after deploy"


def test_control_the_same_override_after_a_closure_with_an_event_is_kept(tmp_path):
    """Control: the live release closes the finding on its second run (an event is written), then the biller appeals.
    The release keeps the biller's status."""
    base(tmp_path)
    add(tmp_path, [clm("CB0000981C017", "1", "223.65", "0.00", sv("99214", "223.65", "0.00", cas="CAS*CO*16*223.65~"), payer_ctl="ANT981A")],
        "20260415")
    db = str(tmp_path / "s.sqlite")
    detect(LIVE, db, "live-0", "2026-04-16T09:00:00+00:00")
    add(tmp_path, [clm("CB0000981C024", "1", "223.65", "117.60", sv("99214", "223.65", "117.60", cas="CAS*CO*45*106.05~", allowed="117.60"),
                       payer_ctl="ANT981B")], "20260520")
    f = by(detect(LIVE, db, "live-1", "2026-05-25T09:00:00+00:00"), "CB0000981C017", "DENIED_MISSING_INFO")
    assert f["status"] == "resolved"
    act(LIVE, db, "CB0000981C017", "DENIED_MISSING_INFO", "appealed", "Appeal faxed", at="2026-05-26T15:00:00+00:00")
    rel = by(detect(REL, db, "rel-1", "2026-06-02T09:00:00+00:00"), "CB0000981C017", "DENIED_MISSING_INFO")
    print("events:", events(db, f["finding_id"]), "release:", rel["status"], rel.get("hint"))
    assert rel["status"] == "appealed"
