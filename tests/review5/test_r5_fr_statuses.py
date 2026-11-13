"""What people recorded under the live release (3f8855b), through the release's (752c700) first analysis, a week later with
nothing new, and a week later with new remittances. Line denials (99214, CO-16) on C017, one patient per bill; statuses
set with live's apply_status the way the live page does (a status button sends no note; "lost" asks for an optional
note; on a finding the analysis closed, the amount actually received takes two clicks: in progress, then recovered).
Invented data only."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from frh import LIVE, REL, act, add_to, base, brief, clm, copy_db, detect, sv  # noqa: E402


def who(n):
    return f"PAT{n:03d}*AB****MI*W5{n:08d}"


def pcn(n, sub="C017"):
    return f"CB0000{n:03d}{sub}"


def denied(n):
    return clm(pcn(n), "1", "223.65", "0.00", sv("99214", "223.65", "0.00", cas="CAS*CO*16*223.65~"), who=who(n), payer_ctl=f"A{n}A")


def paid(n, sub="C024", amount="117.60"):
    co = f"{223.65 - float(amount):.2f}"
    return clm(pcn(n, sub), "1", "223.65", amount, sv("99214", "223.65", amount, cas=f"CAS*CO*45*{co}~", allowed=amount), who=who(n), payer_ctl=f"A{n}{sub}")


def reversed_(n, sub="C024", amount="117.60"):
    co = f"{223.65 - float(amount):.2f}"
    return clm(pcn(n, sub), "22", "-223.65", f"-{amount}", sv("99214", "-223.65", f"-{amount}", units=-1, cas=f"CAS*CO*45*-{co}~"),
               who=who(n), payer_ctl=f"A{n}{sub}")


CLOSED = {701: "two clicks: in progress, then recovered $95.00", 702: "reopened (open)", 703: "lost, with a note", 704: "dismissed",
          705: "appealed after the closure", 706: "nothing"}
OPEN = {711: "recovered $100.00 from a paper check", 712: "lost, with a note", 713: "dismissed", 714: "appealed", 715: "in progress"}


def row(rows, n):
    rs = [r for r in rows if r["patient_control_number"] == pcn(n) and r["signal"] == "DENIED_MISSING_INFO"]
    assert len(rs) == 1, rs
    return rs[0]


def test_people_s_statuses_through_the_first_runs(tmp_path):
    base(tmp_path)
    db = str(tmp_path / "s.sqlite")
    add_to(tmp_path, db, [denied(n) for n in list(CLOSED) + list(OPEN)], "20260415")
    detect(LIVE, db, "live-1", "2026-04-16T09:00:00+00:00")
    add_to(tmp_path, db, [paid(n) for n in CLOSED], "20260520")
    live1 = detect(LIVE, db, "live-2", "2026-05-21T09:00:00+00:00")
    assert all(row(live1, n)["status"] == "resolved" for n in CLOSED)
    t = "2026-05-22T15:0{}:00+00:00"
    act(LIVE, db, pcn(701), "DENIED_MISSING_INFO", "in_progress", at=t.format(0))
    act(LIVE, db, pcn(701), "DENIED_MISSING_INFO", "resolved", None, 9500, at=t.format(1))
    act(LIVE, db, pcn(702), "DENIED_MISSING_INFO", "open", at=t.format(2))
    act(LIVE, db, pcn(703), "DENIED_MISSING_INFO", "lost", "Payer will recoup: wrong patient", at=t.format(3))
    act(LIVE, db, pcn(704), "DENIED_MISSING_INFO", "dismissed", at=t.format(4))
    act(LIVE, db, pcn(705), "DENIED_MISSING_INFO", "appealed", at=t.format(5))
    act(LIVE, db, pcn(711), "DENIED_MISSING_INFO", "resolved", None, 10000, at=t.format(6))
    act(LIVE, db, pcn(712), "DENIED_MISSING_INFO", "lost", "Past the filing limit", at=t.format(7))
    act(LIVE, db, pcn(713), "DENIED_MISSING_INFO", "dismissed", at=t.format(8))
    act(LIVE, db, pcn(714), "DENIED_MISSING_INFO", "appealed", at=t.format(9))
    act(LIVE, db, pcn(715), "DENIED_MISSING_INFO", "in_progress", at="2026-05-22T15:10:00+00:00")
    live_db, rel_db = copy_db(db, str(tmp_path / "live.sqlite")), copy_db(db, str(tmp_path / "rel.sqlite"))
    stages = {}
    stages["first run"] = (detect(LIVE, live_db, "live-3", "2026-05-28T09:00:00+00:00"), detect(REL, rel_db, "rel-1", "2026-05-28T09:00:00+00:00"))
    stages["a week later, nothing new"] = (detect(LIVE, live_db, "live-4", "2026-06-04T09:00:00+00:00"), detect(REL, rel_db, "rel-2", "2026-06-04T09:00:00+00:00"))
    # new remittances: the open ones are paid on their corrected claims; 701's payment is re-adjudicated (reversed and repaid
    # in one remittance); 706's payment is reversed outright
    add_to(tmp_path, [live_db, rel_db], [paid(n) for n in OPEN] + [reversed_(701), paid(701, "C024", "117.60"), reversed_(706)], "20260610")
    stages["a week later, new remittances"] = (detect(LIVE, live_db, "live-5", "2026-06-11T09:00:00+00:00"), detect(REL, rel_db, "rel-3", "2026-06-11T09:00:00+00:00"))
    problems = []
    for stage, (live, rel) in stages.items():
        print(f"===== {stage}")
        for n, what in {**CLOSED, **OPEN}.items():
            lv, rl = row(live, n), row(rel, n)
            print(f"{n} ({what}): live {lv['status']} {lv['recovered_cents']} | release {rl['status']} {rl['recovered_cents']} notes={rl['notes']!r} hint={rl['hint']!r}")
    # what the person recorded must survive the release's runs
    for stage, (live, rel) in stages.items():
        want = {701: ("resolved", 9500), 702: ("open", None), 703: ("lost", None), 704: ("dismissed", None), 705: ("appealed", None),
                711: ("resolved", 10000), 712: ("lost", None), 713: ("dismissed", None), 714: ("appealed" if stage != "a week later, new remittances" else "resolved", None),
                715: ("in_progress" if stage != "a week later, new remittances" else "resolved", None)}
        for n, (st, amt) in want.items():
            rl = row(rel, n)
            if rl["status"] != st or (amt is not None and rl["recovered_cents"] != amt):
                problems.append((stage, n, rl["status"], rl["recovered_cents"], "wanted", st, amt))
        if row(rel, 703)["notes"] != "Payer will recoup: wrong patient" or row(rel, 712)["notes"] != "Past the filing limit":
            problems.append((stage, "a note was lost"))
    assert not problems, problems
