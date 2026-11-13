"""The first run of the release on a store the live release (3f8855b) analysed and people worked, as the first cycle
after deploy does. People's statuses are set with the live release's own apply_status, in the order its page allows
(on a finding already resolved the live page disables "recovered", so a biller who wants to record the amount actually
received clicks "in progress" and then "recovered" and types it). Invented data only."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lvn import LIVE, REL, act, add, base, clean_visits, clm, detect, show, sv  # noqa: E402

PATH = clean_visits(401, 25, "88305", "150.00", "70.00")      # the practice reads its own slides: 88305 has a usual rate


def by(rows, pcn, signal, seq=None):
    out = [r for r in rows if r["patient_control_number"] == pcn and r["signal"] == signal and (seq is None or r["line_key"].endswith(f":{seq}"))]
    assert len(out) == 1, out
    return out[0]


def two_specimens(tmp_path):
    """C017: two specimens (88305, 88305-59) denied CO-16 on 04-15. C024 (05-20) pays one 88305 ($70). The live release
    closes both denials on that payment."""
    base(tmp_path, extra=PATH)
    add(tmp_path, [clm("CB0000910C017", "1", "300.00", "0.00", sv("88305", "150.00", "0.00", cas="CAS*CO*16*150.00~")
                       + sv("88305", "150.00", "0.00", cas="CAS*CO*16*150.00~", mods="59"), payer_ctl="ANT910A")], "20260415")
    add(tmp_path, [clm("CB0000910C024", "1", "150.00", "70.00", sv("88305", "150.00", "70.00", cas="CAS*CO*45*80.00~", allowed="70.00"),
                       payer_ctl="ANT910B")], "20260520")
    db = str(tmp_path / "s.sqlite")
    live = detect(LIVE, db, "live-1", "2026-05-25T09:00:00+00:00")
    show("live", live)
    assert by(live, "CB0000910C017", "DENIED_MISSING_INFO", 1)["status"] == "resolved"
    assert by(live, "CB0000910C017", "DENIED_MISSING_INFO", 2)["status"] == "resolved"
    return db


def test_a_recovery_a_person_recorded_is_reopened_by_the_unit_rule(tmp_path):
    """The biller opens specimen A's finding, checks the 5/20 EOB, and records it recovered: in progress, then recovered,
    $70. The payment still stands. The first run after deploy sets it back to open and drops the $70."""
    db = two_specimens(tmp_path)
    act(LIVE, db, "CB0000910C017", "DENIED_MISSING_INFO", "in_progress", at="2026-05-26T15:00:00+00:00", line_seq=1)
    act(LIVE, db, "CB0000910C017", "DENIED_MISSING_INFO", "resolved", None, 7000, at="2026-05-26T15:00:30+00:00", line_seq=1)
    rel = detect(REL, db, "rel-1", "2026-06-02T09:00:00+00:00")
    show("release", rel)
    mine = by(rel, "CB0000910C017", "DENIED_MISSING_INFO", 1)
    assert (mine["status"], mine["recovered_cents"]) == ("resolved", 7000), \
        f"the biller's recorded recovery became {mine['status']} / {mine['recovered_cents']} with the payment still standing"


def test_a_recovery_a_person_recorded_on_a_payment_since_taken_back_reopens_without_the_hint(tmp_path):
    """The deliberate rule: a recovery a person recorded on a payment the analysis had found reopens WITH a hint when that
    payment is taken back. A visit denied CO-16, paid on the corrected claim, closed by the live release; the biller
    records it recovered ($117.60) as above; the payer then reverses that payment. On the first run after deploy the
    finding reopens, but with no hint, because the live page left the automatic note on the row."""
    base(tmp_path)
    add(tmp_path, [clm("CB0000912C017", "1", "223.65", "0.00", sv("99214", "223.65", "0.00", cas="CAS*CO*16*223.65~"), payer_ctl="ANT912A")],
        "20260415")
    add(tmp_path, [clm("CB0000912C024", "1", "223.65", "117.60", sv("99214", "223.65", "117.60", cas="CAS*CO*45*106.05~", allowed="117.60"),
                       payer_ctl="ANT912B")], "20260520")
    db = str(tmp_path / "s.sqlite")
    live = detect(LIVE, db, "live-1", "2026-05-25T09:00:00+00:00")
    assert by(live, "CB0000912C017", "DENIED_MISSING_INFO")["status"] == "resolved"
    act(LIVE, db, "CB0000912C017", "DENIED_MISSING_INFO", "in_progress", at="2026-05-26T15:00:00+00:00")
    act(LIVE, db, "CB0000912C017", "DENIED_MISSING_INFO", "resolved", None, 11760, at="2026-05-26T15:00:30+00:00")
    add(tmp_path, [clm("CB0000912C024", "22", "-223.65", "-117.60", sv("99214", "-223.65", "-117.60", units=-1, cas="CAS*CO*45*-106.05~"),
                       payer_ctl="ANT912B")], "20260601")
    rel = detect(REL, db, "rel-1", "2026-06-02T09:00:00+00:00")
    show("release", rel)
    mine = by(rel, "CB0000912C017", "DENIED_MISSING_INFO")
    assert mine["status"] == "open"
    assert mine["hint"], "reopened with no word of why: the person's recorded recovery is simply gone"


def test_control_the_amount_a_person_typed_on_a_closure_that_still_holds(tmp_path):
    """Control for the two above: the visit closed by the live release on the corrected claim's $117.60; the biller records
    $95.00 actually received (in progress, then recovered). Nothing is taken back. What does each release keep?"""
    base(tmp_path)
    add(tmp_path, [clm("CB0000913C017", "1", "223.65", "0.00", sv("99214", "223.65", "0.00", cas="CAS*CO*16*223.65~"), payer_ctl="ANT913A")],
        "20260415")
    add(tmp_path, [clm("CB0000913C024", "1", "223.65", "117.60", sv("99214", "223.65", "117.60", cas="CAS*CO*45*106.05~", allowed="117.60"),
                       payer_ctl="ANT913B")], "20260520")
    from lvn import copy_db
    db = str(tmp_path / "s.sqlite")
    detect(LIVE, db, "live-1", "2026-05-25T09:00:00+00:00")
    act(LIVE, db, "CB0000913C017", "DENIED_MISSING_INFO", "in_progress", at="2026-05-26T15:00:00+00:00")
    act(LIVE, db, "CB0000913C017", "DENIED_MISSING_INFO", "resolved", None, 9500, at="2026-05-26T15:00:30+00:00")
    again = copy_db(db, str(tmp_path / "again.sqlite"))
    live2 = by(detect(LIVE, again, "live-2", "2026-06-02T09:00:00+00:00"), "CB0000913C017", "DENIED_MISSING_INFO")
    rel = by(detect(REL, db, "rel-1", "2026-06-02T09:00:00+00:00"), "CB0000913C017", "DENIED_MISSING_INFO")
    print("live next run:", live2["status"], live2["recovered_cents"], " release first run:", rel["status"], rel["recovered_cents"])
