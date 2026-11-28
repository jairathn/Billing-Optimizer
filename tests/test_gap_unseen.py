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
        "SELECT patient_control_number, signal, hint, ema_link, shortfall_cents, evidence, work_state, state_rule FROM findings "
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


# ---------------------------------------------------------------------------------------- G2: the Data tab, on both hosts
@pytest.mark.parametrize("host", ["server", "serverless"])
def test_the_data_tab_shows_the_unseen_claims_in_dollars_by_reason_and_payer_with_their_list(tmp_path, host, monkeypatch):
    """Three claims to Blue Cross no source can show an answer to: claim 72 (January 20, older than EMA's reports, $150),
    claim 75 (March 6, no EMA claim of its content, $150) and claim 76 (sent to Blue Cross as the second payer, $26.66 left by
    the first). The Data tab (the server, and Vercel's dashboard copy) shows the total in dollars by reason and payer, names
    the export that would let the bridges run on claim 72 (EMA's Production for services from January 20, 2026 to March 1,
    2026 and the A/R for service year 2026), and lists the claims most dollars first, as a page and as a CSV, with the member
    id only while the names switch is on (on the server: Vercel's dashboard copy keeps no member id). On Vercel, which reads EMA's A/R only, one sentence says why the count is larger
    there. The data-health check says the same in dollars. Before: nothing read the count, and no page showed it."""
    import remitwatch.api as api
    from remitwatch import insights
    from tests import ema_fixtures as F
    from tests.test_ema_pages2 import _site
    s, lg = _world(tmp_path, "file", monkeypatch)
    send(s, tmp_path, [clm837(F.claim_id(72), [("99213", "150.00", "20260120")], payer=BC, member="MEMBER000072"),
                       clm837(F.claim_id(75), [("99213", "150.00", "20260306")], payer=BC, member="MEMBER000075"),
                       clm837_second(F.claim_id(76), [("99213", "150.00", "20260306")], BC, "MEMBER000076")], "20260309")
    _run(s, lg)
    db = str(tmp_path / "s.sqlite")
    s.close()
    c = _site(monkeypatch, db, serverless=host == "serverless", tmp=tmp_path)
    u = c.get("/api/ema/data").json()["unseen"]
    assert (u["n"], u["cents"]) == (3, 32666), u
    assert {r["reason"]: (r["n"], r["cents"]) for r in u["reasons"]} == {
        "before_ema_reports": (1, 15000), "ema_claim_not_found": (1, 15000), "second_payer_not_matched": (1, 2666)}, u["reasons"]
    assert [(g["payer"], g["n"], g["cents"]) for g in u["groups"]] == [(u["groups"][0]["payer"], 3, 32666)], u["groups"]
    assert u["export"] == ("EMA's Production report for services from January 20, 2026 to March 1, 2026, and the A/R report "
                           "for service year 2026"), u["export"]
    assert bool(u["host_note"]) == (host == "serverless"), u["host_note"]
    page = c.get("/api/ema/unseen").json()
    assert [(r["claim_id"], r["cents"]) for r in page["rows"]] == [(F.claim_id(72), 15000), (F.claim_id(75), 15000),
                                                                  (F.claim_id(76), 2666)], page["rows"]
    assert page["count"] == 3 and page["cents"] == 32666 and not page["names"]
    assert all("member_id" not in r for r in page["rows"]) and page["rows"][0]["codes"] == ["99213"], page["rows"][0]
    text = c.get("/api/ema/unseen.csv").text
    assert text.splitlines()[0].startswith("claim_id,payer_group,dollars") and "MEMBER0000" not in text, text[:300]
    monkeypatch.setattr(api, "SHOW_PHI", True)
    named = c.get("/api/ema/unseen").json()
    # the member id while the names switch is on, where the store holds it (the dashboard's copy keeps none)
    want = "MEMBER000072" if host == "server" else ""
    assert named["names"] and named["rows"][0]["member_id"] == want, named["rows"][0]
    assert (want in c.get("/api/ema/unseen.csv").text) if want else True
    if host == "server":
        conn = sqlite3_connect(db)
        try:
            h = next(x for x in insights.data_health(conn, "2026-03-28", "2026-05-03") if x.get("key") == "unseen_837")
        finally:
            conn.close()
        assert h["title"] == "3 claims sent that no source can show an answer to ($326.66)", h["title"]
        assert "the service is older than every EMA report in the store" in h["detail"] and "March 1, 2026" in h["who"], h


def sqlite3_connect(db):
    import sqlite3
    return sqlite3.connect(db)


@pytest.mark.parametrize("kind", KINDS)
def test_every_payer_that_sends_no_era_is_on_the_data_tab_with_its_dollars(tmp_path, kind):
    """Twelve small plans, each sent ten claims ($100 each) in August, none of which ever got an ERA: each is a payer whose
    837 claims get no ERA. The Data tab names the first ten, each line with its dollars (and the dollars no source can check),
    and one more line for the other two: '2 more payers send no ERA: 20 claims, $2,000.00 billed'. Before: the eleventh
    and twelfth payers were on no line (the list was cut at ten in silence), and no line gave the unchecked claims' dollars."""
    from remitwatch import insights
    from tests.test_837_sends import _remits, _visits
    s = _store(tmp_path, kind)
    _remits(s, tmp_path, _visits(), "20261110")
    for p in range(12):
        send(s, tmp_path, [clm837(f"CB0{p:02d}N{i:03d}C017", [("99214", "100.00", "20260810")], payer=(f"SMALL PLAN {p:02d}", f"SP{p:03d}"),
                                  member=f"N{p:02d}{i:06d}") for i in range(10)], "20260812")
    Detector(s, run_id="era", today="2026-11-12").run()
    items = [h for h in insights.data_health(s.conn, "2026-11-10", "2026-11-12") if str(h.get("key", "")).startswith("no_era_837")]
    lines = [h for h in items if h["key"] == "no_era_837"]
    more = [h for h in items if h["key"] == "no_era_837_more"]
    assert len(lines) == 10, [h["title"] for h in items]
    assert all("($1,000.00)" in h["detail"] and "$1,000.00 billed" in h["detail"] for h in lines), lines[0]["detail"]
    assert len(more) == 1 and more[0]["title"] == "2 more payers send no ERA: 20 claims, $2,000.00 billed", [h["title"] for h in more]
    assert more[0]["unchecked"] == 20 and more[0]["unchecked_cents"] == 200000, more[0]
    s.close()


# --------------------------------------------------------------------------------------- G4: when a payer's ERAs begin or end
@pytest.mark.parametrize("kind", KINDS)
def test_claims_sent_before_a_payer_starts_sending_eras_are_not_listed_once_it_does(tmp_path, kind):
    """The VA's community care network (payer id VACCN) sent no ERA for the twelve claims sent January 14 (services January
    12): its answers reach the practice outside the 835 feed. In August the practice enrolled for its ERAs: the twenty claims
    sent August 12 are each answered by an 835 (September 15). The January claims were sent while no ERA could come for
    them: nothing shows they went unanswered, so they are counted with the claims no source can check (the Data tab's line
    for the payer, and the unseen count), not listed as never answered.
    Before: whether a payer sends ERAs was decided once per payer id over every claim ever sent (detect.NO_ERA_MIN_CLAIMS,
    NO_ERA_MAX_SHARE); once more than 5% of its claims got an 835 the January claims were judged by the 835s and listed.
    On the real data, the VA's ERA enrollment would do this to the 1,236 VA claims of 2023 and 2024 counted unseen today
    (about 97.5% of the VA claims EMA can check are paid), unless EMA's 2024 reports are loaded first."""
    from tests.test_837_sends import VA, _chased, _remits, _visits, answer
    s = _store(tmp_path, kind)
    _remits(s, tmp_path, _visits(), "20261110")
    jan = [f"CB0000J{i:02d}C017" for i in range(12)]
    aug = [f"CB0000A{i:02d}C017" for i in range(20)]
    send(s, tmp_path, [clm837(c, [("99214", "100.00", "20260112")], payer=VA, member=f"J{i:08d}") for i, c in enumerate(jan)], "20260114")
    send(s, tmp_path, [clm837(c, [("99214", "100.00", "20260810")], payer=VA, member=f"A{i:08d}") for i, c in enumerate(aug)], "20260812")
    _remits(s, tmp_path, [answer(c, "1", "80.00", dos="20260810", code="99214", charge="100.00") for c in aug], "20260915",
            payer="OPTUM VA CCN TEST", payer_id="VACCN")
    found = _chased(s, "t1", today="2026-11-12")
    assert not set(jan) & set(found), (
        f"{len(set(jan) & set(found))} claims sent before the payer's ERAs began are listed as never answered")
    assert not set(aug) & set(found), "the August claims are answered by their 835s"
    no_era = json.loads((s.conn.execute("SELECT value FROM meta WHERE key='feed:no_era_837'").fetchone() or ["[]"])[0])
    va = [p for p in no_era if p.get("payer_id") == "VACCN"]
    assert len(va) == 1 and va[0]["unchecked"] == 12 and va[0]["unchecked_cents"] == 120000, no_era
    assert sorted(r[0] for r in _unseen(s).get("claims") or []) == sorted(jan), "the January claims are counted unseen, by name"
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_claims_sent_after_a_payer_stops_sending_eras_are_counted_not_listed(tmp_path, kind):
    """The mirror case. The VA's network answered the twelve claims sent January 14 with 835s (February 15); then its ERAs
    stopped: the twenty claims sent August 12 got none, though they are due and the remittances on file reach November 10.
    Nothing on file can say whether the VA answered them outside the 835 feed: they are counted with the claims no source can
    check, not listed as never answered. Before: 12 of the 32 claims ever sent got an 835 (over 5%), so the payer was judged
    to send ERAs and the twenty August claims were listed as never answered."""
    from tests.test_837_sends import VA, _chased, _remits, _visits, answer
    s = _store(tmp_path, kind)
    _remits(s, tmp_path, _visits(), "20261110")
    jan = [f"CB0000J{i:02d}C017" for i in range(12)]
    aug = [f"CB0000A{i:02d}C017" for i in range(20)]
    send(s, tmp_path, [clm837(c, [("99214", "100.00", "20260112")], payer=VA, member=f"J{i:08d}") for i, c in enumerate(jan)], "20260114")
    _remits(s, tmp_path, [answer(c, "1", "80.00", dos="20260112", code="99214", charge="100.00") for c in jan], "20260215",
            payer="OPTUM VA CCN TEST", payer_id="VACCN")
    send(s, tmp_path, [clm837(c, [("99214", "100.00", "20260810")], payer=VA, member=f"A{i:08d}") for i, c in enumerate(aug)], "20260812")
    found = _chased(s, "t1", today="2026-11-12")
    assert not set(aug) & set(found), f"{len(set(aug) & set(found))} claims sent after the payer's ERAs stopped are listed"
    no_era = json.loads((s.conn.execute("SELECT value FROM meta WHERE key='feed:no_era_837'").fetchone() or ["[]"])[0])
    va = [p for p in no_era if p.get("payer_id") == "VACCN"]
    assert len(va) == 1 and va[0]["unchecked"] == 20, no_era
    assert sorted(r[0] for r in _unseen(s).get("claims") or []) == sorted(aug)
    s.close()


# ------------------------------------------------------------------------------------ G5: a line EMA's A/R holds open
@pytest.mark.parametrize("kind", KINDS)
def test_a_claim_whose_line_emas_ar_holds_open_is_listed_not_counted_unseen(tmp_path, kind, monkeypatch):
    """Claim 71: a pathology line (88305 at $64) of a visit March 5, sent March 9 to Blue Cross (the 837s show it); no 835
    answers it and no Production line holds it. EMA's A/R holds the same patient's 88305 of March 5 open for insurance
    ($64, primary, no claim created: the 'unbilled' bucket). EMA's own books say the money is open, and the 837 says the
    claim went out: it is listed as never answered (with the A/R's balance, bucket and date as its evidence), not counted
    unseen. Before: no bridge could find an EMA claim (the A/R row has no claim id and no claim number, Production has no
    line), so the claim was counted unseen and never listed (on the real data: 2 claims, $254.00, both 'unbilled')."""
    from tests import ema_fixtures as F
    ar = [F.ar_row(claim=None, link=None, party="Insurance", coverage="Primary", current=64.0, cpt="88305", svc=D(2026, 3, 5),
                   patient="Sample, Pat", status=None, aging=D(2026, 4, 25))]
    s, lg = _world(tmp_path, kind, monkeypatch, [], ar)
    send(s, tmp_path, [clm837(F.claim_id(71), [("88305", "64.00", "20260305")], payer=BC, member="MEMBER000071")], "20260309")
    det, listed, unseen = _run(s, lg)
    assert F.claim_id(71) in listed, (
        f"not listed (unseen {unseen.get('n')}): EMA's A/R holds the line open for insurance and the 837 shows it sent")
    ev = json.loads(listed[F.claim_id(71)]["evidence"])
    assert ev["ar_open"]["balance_cents"] == 6400 and ev["ar_open"]["bucket"] == "held for insurance, no claim created", ev["ar_open"]
    assert "holds this service open for insurance ($64.00" in listed[F.claim_id(71)]["hint"]
    assert (unseen.get("n") or 0) == 0, unseen
    s.close()


# ------------------------------------------------------------------------------- G6: a claim sent to the visit's second payer
@pytest.mark.parametrize("kind", KINDS)
def test_a_claim_sent_to_the_visits_second_payer_is_found_by_its_ema_claims_secondary_payer(tmp_path, kind, monkeypatch):
    """Claim 70: a visit March 5 (11102 at $86.66). Medicare Test Region is its first payer and paid $60 on March 6 (posted
    in EMA; no 835 for it). The 837s show claim 70 sent March 9 to Blue Cross, the visit's second payer; no 835 from Blue
    Cross reaches its due day (the remittances on file stop March 28). EMA's claim 70 has Medicare as its primary payer and
    Blue Cross as its secondary, and Blue Cross's $15 is posted on it April 10, after the send. EMA's postings answered the
    claim: it is neither listed nor counted with the claims no source can show an answer to.
    Before: the content bridge compared the claim's payer with the group on EMA's lines (the primary's) only, found no EMA
    claim, and counted the claim unseen (on the real data: 11 claims, $3,021.64, 4 of them answered by the second payer's
    own payment after the send)."""
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import charge, pay
    MED, BCP = F.PAYERS["medicare"], F.PAYERS["bcbs"]
    both = {"primary": MED, "secondary": BCP}
    rows = [charge(F.link_no(70), cpt="11102", amount=86.66, patient=1, svc=D(2026, 3, 5), posted=D(2026, 3, 5), **both),
            pay(F.link_no(70), "PAPER-70A", 60, cpt="11102", patient=1, svc=D(2026, 3, 5), posted=D(2026, 3, 6), payer=MED, **both),
            pay(F.link_no(70), "PAPER-70B", 15, cpt="11102", patient=1, svc=D(2026, 3, 5), posted=D(2026, 4, 10), payer=BCP, **both)]
    s, lg = _world(tmp_path, kind, monkeypatch, rows)
    send(s, tmp_path, [clm837(F.claim_id(70), [("11102", "86.66", "20260305")], payer=BC, member="MEMBER000070")], "20260309")
    det, listed, unseen = _run(s, lg)
    assert F.claim_id(70) not in listed, "EMA shows the second payer's answer after the send: nothing to chase"
    assert (unseen.get("n") or 0) == 0, (
        f"counted unseen ({unseen.get('n')} claim): EMA's claim 70 names Blue Cross as its secondary payer, and the content "
        "bridge must accept it")
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_the_first_payers_payment_after_the_send_does_not_answer_the_second_payers_claim(tmp_path, kind, monkeypatch):
    """The guard. Claim 73: a visit March 5 (11102 at $86.66). The 837s show it sent March 9 to Blue Cross, the visit's
    second payer. EMA's claim 73 has Medicare Test Region as its first payer and Blue Cross as its second; Medicare's $60 is
    posted March 12, after the send, and Blue Cross has posted nothing. Nothing shows Blue Cross answered: the claim is
    listed (or counted with the claims no source can show an answer to), never dropped as answered. On the real data the
    investigation's first what-if dropped such claims: a claim to Medicaid as second payer was 'answered' by the first
    payer's reasons posted 31 days after the send, with no posting by Medicaid at all."""
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import charge, pay
    MED, BCP = F.PAYERS["medicare"], F.PAYERS["bcbs"]
    both = {"primary": MED, "secondary": BCP}
    rows = [charge(F.link_no(73), cpt="11102", amount=86.66, patient=1, svc=D(2026, 3, 5), posted=D(2026, 3, 5), **both),
            pay(F.link_no(73), "PAPER-73A", 60, cpt="11102", patient=1, svc=D(2026, 3, 5), posted=D(2026, 3, 12), payer=MED, **both)]
    s, lg = _world(tmp_path, kind, monkeypatch, rows)
    send(s, tmp_path, [clm837(F.claim_id(73), [("11102", "86.66", "20260305")], payer=BC, member="MEMBER000073")], "20260309")
    det, listed, unseen = _run(s, lg)
    assert F.claim_id(73) in listed or (unseen.get("n") or 0) == 1, (
        "dropped as answered: the only posting after the send is the FIRST payer's, and Blue Cross never answered")
    if F.claim_id(73) in listed:
        # found through its EMA claim's second payer, and valued at what EMA leaves open after the first payer ($26.66)
        assert json.loads(listed[F.claim_id(73)]["evidence"]).get("visit_ema_claim") == F.link_no(73), listed[F.claim_id(73)]
        assert listed[F.claim_id(73)]["ema_link"] is None, "the first payer's EMA claim is not the second payer's claim's own"
        assert 0 < (listed[F.claim_id(73)]["shortfall_cents"] or 0) <= 2666, listed[F.claim_id(73)]["shortfall_cents"]
    s.close()


def _second_payer_world(tmp_path, kind, monkeypatch, *, eaf, contractual, written_off, paid_on=D(2026, 3, 6)):
    """Claim 75: a visit March 5 (11102 at $86.66). EMA files it under Medicare Test Region, its first payer, which paid $60
    (posted `paid_on`, March 6 unless said) and wrote `contractual` dollars off as contractual, and names Blue Cross as its
    second payer. The 837s show
    claim 75 sent March 9 to Blue Cross as the second payer, saying Medicare left the patient `eaf` dollars (AMT*EAF; None:
    the 837 does not say). Blue Cross never posted anything; on April 12 the practice wrote `written_off` dollars off in EMA
    (CO-206, not contractual, under Medicare's name). Returns the listed items and the unseen count."""
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import adj, charge, pay
    MED, BCP = F.PAYERS["medicare"], F.PAYERS["bcbs"]
    both = {"primary": MED, "secondary": BCP}
    v = {"cpt": "11102", "patient": 1, "svc": D(2026, 3, 5)}
    rows = [charge(F.link_no(75), amount=86.66, posted=D(2026, 3, 5), **v, **both),
            pay(F.link_no(75), "PAPER-75A", 60, posted=paid_on, payer=MED, **v, **both)]
    if contractual:
        rows.append(adj(F.link_no(75), "CO-45", contractual, posted=paid_on, contractual=True, payer=MED, **v, **both))
    if written_off:
        rows.append(adj(F.link_no(75), "CO-206", written_off, posted=D(2026, 4, 12), payer=MED, **v, **both))
    s, lg = _world(tmp_path, kind, monkeypatch, rows)
    send(s, tmp_path, [clm837_second(F.claim_id(75), [("11102", "86.66", "20260305")], BC, "MEMBER000075", left=eaf)], "20260309")
    det, listed, unseen = _run(s, lg)
    s.close()
    return det, listed, unseen


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("eaf", ["20.00", None], ids=["837_says", "837_silent"])
def test_a_second_payers_claim_whose_remainder_ema_wrote_off_is_listed_at_what_the_first_payer_left(tmp_path, kind, monkeypatch, eaf):
    """Medicare paid $60 and wrote $6.66 off as contractual: it left the patient $20.00, and the claim to Blue Cross, the
    second payer, is worth that. Blue Cross never answered; five weeks after the send the practice wrote the $20.00 off in
    EMA, so EMA leaves nothing open on the visit. The claim is listed at $20.00, what the first payer left (by the 837's
    AMT*EAF, or, when the 837 does not say, by EMA's postings: the charge less the first payer's payment and contractual
    adjustment), and its evidence keeps what EMA leaves open: a write-off with no answer from the claim's payer is no
    answer, and EMA's figures alone close nothing.
    Before: EMA's open balance, $0 after the write-off, was taken for what the first payer left, and the claim was on no
    list and in no count but the maintainers' (on the real data two claims to Medicaid as second payer, $285.21 by their
    837s, were dropped)."""
    from tests import ema_fixtures as F
    det, listed, unseen = _second_payer_world(tmp_path, kind, monkeypatch, eaf=eaf, contractual=6.66, written_off=20,
                                              paid_on=D(2026, 3, 9))
    assert F.claim_id(75) in listed, (
        f"not listed (counts {dict((k, v) for k, v in det.ema_counts.items() if 'second' in k)}): Blue Cross never answered, "
        "and the practice's write-off in EMA is no answer")
    item = listed[F.claim_id(75)]
    assert item["ema_link"] is None and json.loads(item["evidence"]).get("visit_ema_claim") == F.link_no(75), item
    # the first payer's payment, posted the day the claim went to Blue Cross, and the write-off settle EMA's claim: that is
    # the first payer's settlement, never this claim's answer, so the claim is work, not done
    assert item["work_state"] == "work_now", (item["work_state"], item["state_rule"])
    assert item["shortfall_cents"] == 2000, item["shortfall_cents"]
    ev = json.loads(item["evidence"])
    assert ev.get("second_payer_left_cents") == 2000 and ev.get("ema_open_cents") == 0, ev
    assert "has posted nothing" in (item["hint"] or ""), item["hint"]
    assert (unseen.get("n") or 0) == 0, unseen


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("sent", ["20260309", "20260428"], ids=["due", "waiting"])
def test_a_claim_to_the_second_payer_never_stands_for_its_ema_claims_own_never_sent_item(tmp_path, kind, monkeypatch, sent):
    """EMA's claim 76 (11102 at $86.66, March 5) has Medicare Test Region as its first payer and Blue Cross as its second,
    and Production of May 1 calls it Invalid: nothing went to Medicare. The 837s show claim 76 sent March 9 to Blue Cross
    as the second payer (or April 28, not due yet); Blue Cross never answered. Two matters, two payers: EMA's claim is
    listed as never sent (fix it and send it to Medicare), and the claim to Blue Cross is listed as never answered on its
    own (or waits for its due day on the waiting view, holding nothing of EMA's claim). EMA's status of its claim
    is the first payer's, never the second payer's claim's, and the second payer's claim never takes EMA's claim from the
    report checks (nor waits for it, nor inherits its work).
    Before: finding the claim to Blue Cross through EMA's claim's second payer gave it EMA's claim's status, so it was
    handed to the never-sent check and absorbed in its item; with the EMA claim marked as the 837 pass's own, a never-sent
    item for the first payer vanished on the real data (UHC Medicare Advantage, $290.38) and was kept only by the
    self-check."""
    from tests import ema_fixtures as F
    from tests.test_ema_wiring import charge
    MED, BCP = F.PAYERS["medicare"], F.PAYERS["bcbs"]
    rows = [charge(F.link_no(76), cpt="11102", amount=86.66, patient=1, svc=D(2026, 3, 5), posted=D(2026, 3, 5), status="Invalid",
                   primary=MED, secondary=BCP)]
    s, lg = _world(tmp_path, kind, monkeypatch, rows)
    send(s, tmp_path, [clm837_second(F.claim_id(76), [("11102", "86.66", "20260305")], BC, "MEMBER000076", paid="0.00", left=None)],
         sent)
    det = Detector(s, run_id="gap", today="2026-05-03")
    det.run()
    assert det.ema_errors == [], det.ema_errors
    lg.close()
    got = {(r["signal"], r["patient_control_number"] or f"link:{r['ema_link']}"): dict(r) for r in s.query(
        "SELECT signal, patient_control_number, ema_link, payer_group, hint, evidence FROM findings WHERE COALESCE(stale,0)=0 "
        "AND signal IN ('NO_ANSWER','REJECTED_PRE_PAYER','NOT_SENT') AND (patient_control_number=? OR ema_link=?)",
        (F.claim_id(76), F.link_no(76)))}
    w = json.loads((s.conn.execute("SELECT value FROM meta WHERE key='feed:waiting_837'").fetchone() or ["{}"])[0])
    waiting = next((r for r in w.get("claims") or [] if r and r[0] == F.claim_id(76)), None)
    s.close()
    mine = [k for k in got if k[1] == F.claim_id(76)]
    theirs = [k for k in got if k[1] == f"link:{F.link_no(76)}"]
    assert len(theirs) == 1 and theirs[0][0] in ("NOT_SENT", "REJECTED_PRE_PAYER"), (
        f"EMA's claim, never sent to its first payer, is on no list of its own: {sorted(got)}")
    if sent == "20260428":
        assert not mine and waiting is not None and len(waiting) == 5, (sorted(got), waiting)
        return
    assert mine == [("NO_ANSWER", F.claim_id(76))], f"the claim to the second payer is not listed as never answered: {sorted(got)}"
    item = got[mine[0]]
    assert item["ema_link"] is None and json.loads(item["evidence"]).get("visit_ema_claim") == F.link_no(76), item
    assert "Invalid" not in (item["hint"] or ""), item
    assert json.loads(item["evidence"]).get("ema_bridge", "").endswith("_secondary"), item["evidence"]


@pytest.mark.parametrize("kind", KINDS)
def test_a_second_payers_claim_the_first_payer_settled_in_full_is_not_listed(tmp_path, kind, monkeypatch):
    """Control: Medicare paid $60 and wrote the other $26.66 off as contractual, so it left the patient nothing, and the
    837 does not say otherwise. Blue Cross owes nothing on the visit: the claim is not listed (counted for the maintainers,
    meta ema:wiring), and not counted with the claims no source can show an answer to (its EMA claim was found)."""
    from tests import ema_fixtures as F
    det, listed, unseen = _second_payer_world(tmp_path, kind, monkeypatch, eaf=None, contractual=26.66, written_off=0)
    assert F.claim_id(75) not in listed, listed.get(F.claim_id(75))
    assert det.ema_counts.get("open_claim_second_nothing_left") == 1, dict(det.ema_counts)
    assert (unseen.get("n") or 0) == 0, unseen


@pytest.mark.parametrize("kind", KINDS)
def test_a_small_plan_that_never_sends_eras_is_judged_by_its_nearest_claims(tmp_path, kind):
    """A small plan (payer id SP001) sent no ERA for any of its twelve claims, two a month from January to June: around any
    one send fewer than ten of its claims fall within 90 days, so its ten claims sent nearest each claim's own send decide,
    and none got an 835: its claims are counted with the claims no source can check, never listed as never answered. (On
    the real data, judging such plans by the 180 days alone listed 104 old claims of small plans as never answered, and
    let 15 others go, until the nearest claims decided.)"""
    from tests.test_837_sends import _chased, _remits, _visits
    s = _store(tmp_path, kind)
    _remits(s, tmp_path, _visits(), "20261110")
    claims = []
    for m in range(1, 7):
        for k in range(2):
            claims.append((f"CB0000S{m}{k}C017", f"2026{m:02d}{10 + k:02d}"))
    for cid, day in claims:
        send(s, tmp_path, [clm837(cid, [("99214", "100.00", day)], payer=("SMALL PLAN", "SP001"), member=f"S{cid[7:9]}000")], day)
    found = _chased(s, "sp", today="2026-11-12")
    assert not {c for c, _d in claims} & set(found), sorted(set(found) & {c for c, _d in claims})
    no_era = json.loads((s.conn.execute("SELECT value FROM meta WHERE key='feed:no_era_837'").fetchone() or ["[]"])[0])
    sp = [p for p in no_era if p.get("payer_id") == "SP001"]
    assert len(sp) == 1 and sp[0]["unchecked"] == 12, no_era
    s.close()
