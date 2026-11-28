"""
The never-answered claims no source can show the answer to (meta ema:unanswered_unseen, unit gapfix G2, G3, G4, G5, G6).

A claim the 837s show sent, that no 835 answers and whose EMA claim no bridge finds, is counted, never listed: nothing on
file says whether it was answered. A private study of the real data (1,557 such claims, $629,759 billed) found the count
gave one false reason for all of them ("EMA's claim cannot be told apart from others"), when 1,542 were older than every
EMA report in the store; that nothing showed it; that the archive retired their bills and so dropped them from the count in
silence; and that the rule judging whether a payer sends ERAs looked at every claim it was ever sent. These tests hold:
- the count gives each claim its reason, values a claim sent to a second payer at what the first payer left, keeps totals
  by reason and payer group, the first service day EMA's reports cover, and the claims themselves (G2);
- the archive keeps the bill of a claim counted unseen, whatever the horizon (G3);
- a claim sent before a payer's ERAs began (or after they stopped) is counted unseen, never listed (G4);
- a claim whose line EMA's A/R holds open for insurance is listed, with the A/R row as its evidence (G5);
- a claim sent to the visit's second payer is found by its EMA claim's secondary payer, and only that payer's own
  postings answer it (G6, with the guard that the first payer's posting never does).

Every name, claim id, member id, EMA claim number, amount and date here is invented (tests/ema_fixtures.py, the 837
builder of tests/test_837_sends.py). Each test runs on the server's store file and on the compact in-memory store the
serverless cycle builds.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch.detect import UNSEEN_KEY, Detector  # noqa: E402
from tests.test_837_sends import KINDS, _store, clm837, send  # noqa: E402
from tests.test_gap_one_claim import _world  # noqa: E402

BC = ("BLUE CROSS TEST", "BCT01")
D = dt.date


def _run(s, lg=None, today="2026-05-03"):
    det = Detector(s, run_id="gap", today=today)
    det.run()
    assert det.ema_errors == [], det.ema_errors
    if lg is not None:
        lg.close()
    listed = {r["patient_control_number"]: dict(r) for r in s.query(
        "SELECT patient_control_number, signal, hint, ema_link, shortfall_cents, evidence FROM findings "
        "WHERE signal='NO_ANSWER' AND COALESCE(stale,0)=0")}
    return det, listed, _unseen(s)


def _unseen(s) -> dict:
    row = s.conn.execute("SELECT value FROM meta WHERE key=?", (UNSEEN_KEY,)).fetchone()
    return json.loads(row[0]) if row else {}


def clm837_second(claim_id: str, lines: list[tuple], payer: tuple, member: str, *, first=("MEDICARE TEST", "00888"),
                  paid: str = "60.00", left: str | None = "26.66") -> str:
    """One subscriber loop of an 837P sending the claim to `payer` as the visit's SECOND payer (SBR01 S), with the first
    payer's answer in its other-subscriber loop (2320 SBR P, AMT*D what it paid, AMT*EAF what it left the patient)."""
    total = sum(float(ln[1]) for ln in lines)
    out = (f"HL*2*1*22*0~SBR*S*18*GRP2******CI~NM1*IL*1*SAMPLE*PAT****MI*{member}~NM1*PR*2*{payer[0]}*****PI*{payer[1]}~"
           f"CLM*{claim_id}*{total:.2f}***11:B:1*Y*A*Y*Y~"
           f"SBR*P*18*GRP1******MB~AMT*D*{paid}~" + (f"AMT*EAF*{left}~" if left is not None else "")
           + f"OI***Y***Y~NM1*IL*1*SAMPLE*PAT****MI*{member}F~NM1*PR*2*{first[0]}*****PI*{first[1]}~")
    for n, (code, charge, dos) in enumerate(lines, 1):
        out += f"LX*{n}~SV1*HC:{code}*{charge}*UN*1***1~DTP*472*D8*{dos}~"
    return out


# --------------------------------------------------------------------------------------------- G2: the reason and the value
@pytest.mark.parametrize("kind", KINDS)
def test_a_claim_older_than_emas_reports_is_counted_with_that_reason_and_the_reports_first_day(tmp_path, kind, monkeypatch):
    """Claim 72: a visit January 20 (99213 at $150), sent late on March 9 to Blue Cross; no 835 answers it, and EMA's
    reports in the store start with services on March 2 (Production) and hold no line for it. It stays unlisted (nothing
    can show its answer), but the count says why: no EMA report covers its service date, and EMA's reports start on March
    2, so exporting EMA's Production and A/R for the earlier services would let the bridges run. (On the real data: 1,542
    of the 1,557 claims, $626,251.87, services in 2023 and 2024, while EMA's reports start with services on 2025-01-02.)
    Before: the count's reason said EMA's claim 'cannot be told apart from others with the same date, code and charge',
    which is not what happened, and named no report to export."""
    from tests import ema_fixtures as F
    s, lg = _world(tmp_path, kind, monkeypatch)
    send(s, tmp_path, [clm837(F.claim_id(72), [("99213", "150.00", "20260120")], payer=BC, member="MEMBER000072")], "20260309")
    det, listed, unseen = _run(s, lg)
    assert F.claim_id(72) not in listed
    assert unseen.get("n") == 1, unseen
    reasons = unseen.get("by_reason") or {}
    assert (reasons.get("before_ema_reports") or {}).get("n") == 1, (
        f"the count gives no reason per claim ({sorted(unseen)}): a claim older than EMA's reports is not one whose EMA claim "
        "'cannot be told apart'")
    assert unseen.get("ema_reports_from") == "2026-03-02", "the first service date EMA's reports cover, for the export to ask for"
    assert unseen.get("first_service") == "2026-01-20" and unseen.get("years_before") == ["2026"], unseen
    assert "cannot be told apart" not in json.dumps(unseen)
    assert unseen["claims"][0][:3] == [F.claim_id(72), unseen["claims"][0][1], "before_ema_reports"], unseen["claims"]
    assert unseen["by_group"] and sum(g["cents"] for g in unseen["by_group"].values()) == unseen["cents"] == 15000, unseen
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_a_claim_inside_emas_reports_whose_ema_claim_is_not_found_says_so(tmp_path, kind, monkeypatch):
    """Claim 75: a visit March 6 (99213 at $150), inside what EMA's reports cover (from March 2), sent March 9 to Blue Cross;
    no 835 answers it and no EMA claim holds its content. Counted unseen with the reason that EMA's claim was not found
    (not 'older than EMA's reports', not 'cannot be told apart')."""
    from tests import ema_fixtures as F
    s, lg = _world(tmp_path, kind, monkeypatch)
    send(s, tmp_path, [clm837(F.claim_id(75), [("99213", "150.00", "20260306")], payer=BC, member="MEMBER000075")], "20260309")
    det, listed, unseen = _run(s, lg)
    assert F.claim_id(75) not in listed
    assert {k: v["n"] for k, v in (unseen.get("by_reason") or {}).items()} == {"ema_claim_not_found": 1}, unseen
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_a_claim_sent_to_the_second_payer_is_counted_at_what_the_first_payer_left(tmp_path, kind, monkeypatch):
    """Claim 76: a visit March 6 (99213 at $150). Medicare Test paid $60 and left the patient $26.66 (the 837's other-payer
    loop: AMT*D, AMT*EAF); the 837s show the claim sent March 9 to Blue Cross as the visit's second payer. No 835 answers it
    and no EMA claim is found: counted unseen, at $26.66 (what Blue Cross can owe at most, as a listed claim is valued),
    with the reason that a second payer's claim is not matched. Before: counted at the full $150 charge (19 such claims on
    the real data, $7,702.41 counted for $3,511.88 the first payers left)."""
    from tests import ema_fixtures as F
    s, lg = _world(tmp_path, kind, monkeypatch)
    send(s, tmp_path, [clm837_second(F.claim_id(76), [("99213", "150.00", "20260306")], BC, "MEMBER000076")], "20260309")
    det, listed, unseen = _run(s, lg)
    assert F.claim_id(76) not in listed
    assert unseen.get("n") == 1 and unseen.get("cents") == 2666 and unseen.get("billed_cents") == 15000, unseen
    assert {k: v["n"] for k, v in (unseen.get("by_reason") or {}).items()} == {"second_payer_not_matched": 1}, unseen
    s.close()


# ------------------------------------------------------------------------------------------- G3: the archive keeps the bill
def _old_va_world(tmp_path, kind):
    """No EMA report in the store. Anthem's remittances on file reach back to January 10, 2022 and forward to November 10,
    2026. Twelve claims went to the VA's community care network (payer id VACCN, which sends no ERA) on June 14, 2022 for
    services June 12, 2022, and no 835 ever answered one: nothing can show their answer."""
    from tests.test_837_sends import VA, _remits, _visits
    s = _store(tmp_path, kind)
    _remits(s, tmp_path, _visits(25, 1), "20220110")
    _remits(s, tmp_path, _visits(25, 200), "20261110")
    old = [f"CB0000V{i:02d}C017" for i in range(12)]
    send(s, tmp_path, [clm837(c, [("99214", "100.00", "20220612")], payer=VA, member=f"V{i:08d}") for i, c in enumerate(old)],
         "20220614")
    return s, old


@pytest.mark.parametrize("kind", KINDS)
def test_the_archive_keeps_the_bills_of_claims_counted_unseen_past_the_horizon(tmp_path, kind):
    """The twelve VA claims of June 2022 are counted unseen (with no EMA report in the store, nothing can show their
    answer), $1,200 billed. Their services are older than the archive's horizon (36 months from November 12, 2026), and no
    item holds their bills. The archive keeps them, at the default horizon and at the tightened 24-month floor: retired,
    their 837 rows would go and the claims would leave the count in silence. Before: the plan retired all twelve bills."""
    from remitwatch import archive
    from remitwatch.detect import bill_of
    s, old = _old_va_world(tmp_path, kind)
    Detector(s, run_id="g3", today="2026-11-12").run()
    unseen = _unseen(s)
    assert unseen.get("n") == 12 and unseen.get("cents") == 120000, unseen
    assert {k: v["n"] for k, v in unseen["by_reason"].items()} == {"no_ema_reports": 12}, unseen
    assert sorted(r[0] for r in unseen["claims"]) == sorted(old)
    for horizon in (None, 24):
        p = archive.plan(s.conn, horizon=horizon, today="2026-11-12")
        assert p["blocked"] is None, p["blocked"]
        leaving = {b[0] for b in p["bills"]}
        assert not leaving & {bill_of(c) for c in old}, (
            f"horizon {p['horizon']['months']}: the archive would retire {len(leaving & {bill_of(c) for c in old})} bills of claims "
            "counted unseen")
        assert p["held"].get(archive.HELD_UNSEEN) == 12, p["held"]
    s.close()
