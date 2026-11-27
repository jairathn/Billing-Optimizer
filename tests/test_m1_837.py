"""
Review m1-fx-837 of the fix round of F3a (the 837 pass and sizing), on the merge 8cb51f6: failing tests for the problems
found, with invented data only (every claim id, member id, name, birth date, payer id and amount is made up). Run against
a checkout of 8cb51f6:

    RW_CHECKOUT=<checkout> python3 -m pytest -q -p no:cacheprovider <this file>

Each detection test runs on the server's store file and on the compact in-memory store (KINDS), as the 837 pass runs on
both.
"""
from __future__ import annotations

import json
import os
import sys

import pytest


from tests.test_837_sends import KINDS, V, _chased, _remits, _store, _visits, send  # noqa: E402
from tests.test_fx_837 import clm837  # noqa: E402

V_MARCH = [("99214", "223.65", "20260302")]


def _show(found: dict) -> None:
    for k, v in sorted(found.items()):
        print(" ", k, v["signal"], v["finding_id"], "shortfall", v["shortfall_cents"], "state", v.get("work_state"),
              "deadline", v.get("appeal_deadline"), "|", (v.get("deadline_basis") or "")[:90])


# ---------------------------------------------------------------- 1. save()'s r1-3 clause: one hand-over, not one per run

@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("how", ["fax", "let_go"])
def test_an_older_bills_work_without_a_note_is_handed_on_once_not_on_every_run(tmp_path, kind, how):
    """(fx-837-r1-3's clause in detect.save.) Bills CB0000H31 (September 15) and CB0000H32 (September 25) carry one
    patient's 99214 of September 14; Anthem answered neither. The 837s were read before the patient loop was read, so both
    bills are listed. A biller works H31 without writing a note: a chase fax goes out for it (how='fax'), or they let it go
    (how='let_go'). The 837s are read again and name the patient: H32 now carries the visit and takes H31's work once
    (save's heir keys; a 'signal_changed' line in its history). The next runs change nothing, so they must add nothing to
    H32's history. On 8cb51f6 every later run adds another 'Carried over from an earlier item' line: H31 stays worked
    (its fax, or its Let go) and stale, and H32 stays untouched (no note, To do), so the clause fires on every run."""
    from remitwatch import x12_837
    from remitwatch.actions import apply_status, ensure_fax_tables
    s = _store(tmp_path, kind)
    m, who = "WREAD00031", ("SAMPLE", "PAT", "19600101")
    send(s, tmp_path, [clm837("CB0000H31C017", V, member=m, subscriber=who)], "20260915")
    send(s, tmp_path, [clm837("CB0000H32C017", V, member=m, subscriber=who)], "20260925")
    s.conn.execute(f"UPDATE claims_837 SET {', '.join(c + '=NULL' for c in x12_837.EXTRA_CLAIM_COLS)}")
    s.conn.commit()
    _remits(s, tmp_path, _visits(), "20261110")
    found = _chased(s, "t1")
    assert {"CB0000H31C017", "CB0000H32C017"} <= set(found), "with no patient on file the two bills are two items"
    old = found["CB0000H31C017"]["finding_id"]
    if how == "fax":
        ensure_fax_tables(s.conn)
        s.conn.execute("""INSERT INTO faxes (fax_key, direction, finding_id, payer_group, number, purpose, level, status, created_at, sent_at)
                          VALUES ('XF1', 'out', ?, 'Anthem / BCBS', '8005550142', 'status', 1, 'sent', '2026-11-12T15:00:00+00:00',
                                  '2026-11-12T15:00:00+00:00')""", (old,))
    else:
        apply_status(s.conn, old, "dismissed", None, actor="biller")
    s.conn.commit()
    for p in sorted(tmp_path.glob("*.837")):
        x12_837.reread_837_bytes(s.conn, p.read_bytes())
    for run in ("t2", "t3", "t4", "t5"):
        found = _chased(s, run)
    _show(found)
    visit = {k: v for k, v in found.items() if k in ("CB0000H31C017", "CB0000H32C017")}
    assert set(visit) == {"CB0000H32C017"}, sorted(visit)
    new = visit["CB0000H32C017"]["finding_id"]
    rows = s.conn.execute("SELECT run_id, note FROM finding_events WHERE finding_id=? AND kind='signal_changed' ORDER BY id",
                          (new,)).fetchall()
    for r in rows:
        print("  event", tuple(r))
    assert len(rows) == 1, (f"H31's work was handed to H32 {len(rows)} times, once per run since the 837s were read again "
                            f"(runs {[r[0] for r in rows]}): the item's history repeats 'Carried over from an earlier item'")
    s.close()


# ---------------------------------------------------------------- 2. _one_payer: a send to another payer is no proof

@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("first,last,file_by", [
    # two administrators of self-funded plans (their group's 90-day common limit from March 2: May 31)
    (("AUXIANT", "AUX01"), ("MERITAIN HEALTH", "MRT01"), "2026-05-31"),
    # two payers the name rules do not know (dated by the longest common limit, a year from March 2)
    (("PINECREST HEALTH PLAN", "PCH01"), ("PINECREST MUTUAL INSURANCE", "PMI01"), "2027-03-02"),
])
def test_a_claim_first_sent_to_another_payer_of_one_payer_group_has_no_timely_filing_with_the_payer_it_reached_late(
        tmp_path, kind, first, last, file_by):
    """(06b51a1's guard, _first_send_to and _one_payer.) One patient's 99214 of March 2, 2026 went as claim CB0000T01C017 on
    March 5 to one payer (`first`), which never answered; the practice sent the same claim id September 25 to another
    payer (`last`, another payer id and another company), past the first-filing limit; no answer either. The March send
    reached the first payer: it is no proof of timely filing with the second (06b51a1: 'a send to another payer proves
    nothing to this one'). On 8cb51f6 _one_payer counts the two as one payer, because payer_company gives every payer of a
    many-payer group ('Self-funded plan TPAs', 'Medicare Supplement carriers') the group's one name, and every payer the
    name rules do not know its first word ('pinecrest'): the item is dated as sent on time, by the appeal window, and its basis
    names the March send to the other payer. (Control: the same claim sent to `last` both times keeps its timely filing,
    the last test in this file.)"""
    s = _store(tmp_path, kind)
    send(s, tmp_path, [clm837("CB0000T01C017", V_MARCH, payer=first)], "20260305")
    send(s, tmp_path, [clm837("CB0000T01C017", V_MARCH, payer=last)], "20260925")
    _remits(s, tmp_path, _visits(), "20261110", payer=last[0], payer_id=last[1])
    found = _chased(s, "t1")
    _show(found)
    f = found["CB0000T01C017"]
    print(f["payer_group"], f["appeal_deadline"], "|", f["deadline_basis"])
    assert "2026-03-05" not in (f["deadline_basis"] or ""), (
        f"the March send went to {first[0]} ({first[1]}), not to {last[0]} ({last[1]}): it is no proof of timely filing "
        f"with {last[0]}, but the item reads: {f['deadline_basis']}")
    assert f["appeal_deadline"] == file_by, (
        f"dated {f['appeal_deadline']} by the appeal window past the limit, as a claim sent on time; its own first-filing "
        f"limit is {file_by}")
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_control_the_same_claim_sent_twice_to_one_administrator_keeps_its_timely_filing(tmp_path, kind):
    """(Control for the test above: passes on 8cb51f6.) The same claim id sent March 5 and September 25 to the same
    administrator keeps its timely filing from the March send."""
    s = _store(tmp_path, kind)
    last = ("MERITAIN HEALTH", "MRT01")
    send(s, tmp_path, [clm837("CB0000T02C017", V_MARCH, payer=last)], "20260305")
    send(s, tmp_path, [clm837("CB0000T02C017", V_MARCH, payer=last)], "20260925")
    _remits(s, tmp_path, _visits(), "20261110", payer=last[0], payer_id=last[1])
    found = _chased(s, "t1")
    f = found["CB0000T02C017"]
    print(f["payer_group"], f["appeal_deadline"], "|", f["deadline_basis"])
    assert "2026-03-05" in (f["deadline_basis"] or "") and f["work_state"] != "expired", (f["work_state"], f["deadline_basis"])
    s.close()


# ---------------------------------------------------------------- 3. an earlier submission of the same bill sent on time

@pytest.mark.parametrize("kind", KINDS)
def test_an_earlier_submission_of_the_bill_sent_on_time_keeps_the_visits_timely_filing(tmp_path, kind):
    """(fx-837-r1-2: 'a visit first sent inside the filing limit keeps its timely filing'.) One patient's 99214 of March 2,
    2026 went to Anthem on bill CB0000S01 as submission C017 on March 5, inside Anthem's 90-day first-filing limit (May
    31); Anthem never answered. The practice sent the bill again as submission C024 on September 25, past that limit; no
    answer either. Anthem's remittances reach November 10. The 837 pass chases the newest submission (C024) only, and the
    March send of C017 is proof of timely filing, as an older bill's send (_open_claim_dates' visit_first) and the same
    claim id's first send (072a920's own_first) are: the item should be dated by Anthem's appeal window past the limit,
    with its basis naming the March send. On 8cb51f6 neither path sees an earlier submission of the same bill (the one-visit
    rule never pairs two submissions of one bill, and own_first reads only C024's own sends), so the item is dated by the
    first-filing limit and shown expired: the recoverable visit moves to lost to deadlines."""
    s = _store(tmp_path, kind)
    send(s, tmp_path, [clm837("CB0000S01C017", V_MARCH)], "20260305")
    send(s, tmp_path, [clm837("CB0000S01C024", V_MARCH)], "20260925")
    _remits(s, tmp_path, _visits(), "20261110")
    found = _chased(s, "t1")
    _show(found)
    assert "CB0000S01C017" not in found, "only the newest submission of a bill is chased"
    f = found["CB0000S01C024"]
    print(f["appeal_deadline"], "|", f["deadline_basis"])
    assert f["work_state"] != "expired" and f["appeal_deadline"] > "2026-11-10", (
        f"C017 went to Anthem on time (2026-03-05): {f['work_state']}, {f['appeal_deadline']}, {f['deadline_basis']}")
    assert "2026-03-05" in (f["deadline_basis"] or "")
    s.close()


# ---------------------------------------------------------------- 4. the sibling rule by first name alone (fx-837-r2-5)

def paid_as(claim_id: str, member: str, last: str, first: str) -> str:
    """Anthem's answer to a claim naming the patient (NM1*QC) with the member id: 99214 of 2026-09-14 paid $117.60 as
    primary (a copy of tests/test_fx_837.paid_as, so the test also runs on a checkout that does not have it)."""
    from tests.test_features import NPI, svc
    return (f"CLP*{claim_id}*1*223.65*117.60*0.00*12*P{claim_id[4:9]}*11~NM1*QC*1*{last}*{first}****MI*{member}~"
            f"NM1*82*2*TEST PROVIDER*****XX*{NPI}~DTM*232*20260914~"
            + svc("99214", "223.65", "117.60", "117.60", "CAS*CO*45*106.05~", dos="20260914"))


@pytest.mark.parametrize("kind", KINDS)
def test_a_family_members_paid_visit_does_not_pay_this_patients_visit_because_their_first_names_match(tmp_path, kind):
    """(fx-837-r2-5's rule in _837_paid_elsewhere.) A mother (MARIA LOPEZTEST, the subscriber) and her daughter (MARIA
    GARCIATEST, a dependent on the mother's plan: the 837's patient loop names her, with her own birth date) were both seen
    September 14 for a 99214. The mother's visit was paid by Anthem on bill CB0000N21 (October 1; the remittance names the
    mother). The daughter's bill CB0000N22 went out September 15 under the mother's member id, and no payer answered it;
    Anthem's remittances reach November 10. Two people, and the daughter's visit is unpaid: it is chased. On 8cb51f6 the
    sibling rule compares first names alone whenever both sides give one (same_patient: 'both give a first name: it
    decides'), so MARIA and MARIA are one patient and the mother's payment pays the daughter's visit: CB0000N22 leaves
    every list, counted only in ema:wiring's 837_paid_under_another_bill (a claim count, no dollars). The integration tip
    before the fix round (3a7f95b), which compared the keyed whole names, chases it."""
    s = _store(tmp_path, kind)
    sub = "WFAMM00021"
    send(s, tmp_path, [clm837("CB0000N22C017", V, member=sub, subscriber=("LOPEZTEST", "MARIA", "19800101"),
                              patient=("GARCIATEST", "MARIA", "20120704"))], "20260915")
    _remits(s, tmp_path, [paid_as("CB0000N21C017", sub, "LOPEZTEST", "MARIA")], "20261001")
    _remits(s, tmp_path, _visits(), "20261110")
    found = _chased(s, "t1")
    _show(found)
    assert "CB0000N22C017" in found, (f"listed {sorted(found)}: the daughter's own visit was never answered; the mother's "
                                      "paid visit (same first name, another last name) is no payment of it")
    s.close()


# ---------------------------------------------------------------- 5. the answered carrier and a same-day void (fx-837-r2-6)

@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("void_day", ["20260925", "20260924"])
def test_an_older_bill_gives_way_to_the_answered_newer_bill_whose_old_submission_was_voided_the_day_it_went_again(
        tmp_path, kind, void_day):
    """(fx-837-r2-6's _837_answered_carriers.) One patient's 99214 of September 14: bill CB0000Y71 went to Anthem
    September 15 and got no answer. The practice made the bill again as CB0000Y72 (C017, September 20), then corrected it:
    it voided C017 and sent C018 (an original) on September 25. Anthem denied C018 (CO-16, October 30); its remittances
    reach November 10. The visit's answer is on the newest bill: Y71 must not be chased as never answered beside Y72's
    denial (the fixer's own r2-6 rule). On 8cb51f6 _837_answered_carriers drops a bill as a carrier when any submission
    of it was voided on or after its newest send (`voided[b] >= r[0]`), so a void of the old submission the day the new
    one went out (void_day 2026-09-25) makes Y72 carry nothing, and Y71 comes back beside the denial: one visit chased
    twice. _load_837 reads the same day the other way ('a void and a new claim the same day is a replacement, not a
    void'). Control: the void a day earlier (2026-09-24) passes."""
    from tests.test_837_sends import answer
    s = _store(tmp_path, kind)
    member = "WDUP000071"
    send(s, tmp_path, [clm837("CB0000Y71C017", V, member=member)], "20260915")
    send(s, tmp_path, [clm837("CB0000Y72C017", V, member=member)], "20260920")
    send(s, tmp_path, [clm837("CB0000Y72C017", V, member=member, freq="8")], void_day)
    send(s, tmp_path, [clm837("CB0000Y72C018", V, member=member)], "20260925")
    _remits(s, tmp_path, [answer("CB0000Y72C018", status="1", paid="0.00")], "20261030")
    _remits(s, tmp_path, _visits(), "20261110")
    found = _chased(s, "t1")
    y72 = [dict(r) for r in s.query("SELECT signal, status FROM findings WHERE patient_control_number LIKE 'CB0000Y72%' "
                                    "AND COALESCE(stale,0)=0")]
    print(sorted(found), y72)
    assert y72, "control: Y72's denial is an item"
    assert "CB0000Y71C017" not in found, "the visit is chased twice: Y71 as never answered beside Y72's denial"
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_the_items_story_says_carried_over_once(tmp_path, kind):
    """(m1-fx-837-1, what a person sees.) The same as the fax case above: after the 837s are read again and three more runs
    with no new data, H32's story (story.item_story, the drawer) says 'Carried over from an earlier item' once. On 8cb51f6
    it says it once per run."""
    from remitwatch import story, x12_837
    from remitwatch.actions import ensure_fax_tables
    s = _store(tmp_path, kind)
    m, who = "WREAD00041", ("SAMPLE", "PAT", "19600101")
    send(s, tmp_path, [clm837("CB0000H41C017", V, member=m, subscriber=who)], "20260915")
    send(s, tmp_path, [clm837("CB0000H42C017", V, member=m, subscriber=who)], "20260925")
    s.conn.execute(f"UPDATE claims_837 SET {', '.join(c + '=NULL' for c in x12_837.EXTRA_CLAIM_COLS)}")
    s.conn.commit()
    _remits(s, tmp_path, _visits(), "20261110")
    found = _chased(s, "t1")
    ensure_fax_tables(s.conn)
    s.conn.execute("""INSERT INTO faxes (fax_key, direction, finding_id, payer_group, number, purpose, level, status, created_at, sent_at)
                      VALUES ('XF2', 'out', ?, 'Anthem / BCBS', '8005550142', 'status', 1, 'sent', '2026-11-12T15:00:00+00:00',
                              '2026-11-12T15:00:00+00:00')""", (found["CB0000H41C017"]["finding_id"],))
    s.conn.commit()
    for p in sorted(tmp_path.glob("*.837")):
        x12_837.reread_837_bytes(s.conn, p.read_bytes())
    for run in ("t2", "t3", "t4", "t5"):
        found = _chased(s, run)
    st = story.item_story(s.conn, found["CB0000H42C017"]["finding_id"])
    rows = st.get("rows") if isinstance(st, dict) else None
    texts = [str(r.get("text") or r.get("what") or r) for r in (rows or [])] if rows is not None else [json.dumps(st, default=str)]
    carried = sum(t.count("Carried over from an earlier item") for t in texts)
    print("story rows:", len(texts), "carried over:", carried)
    assert carried == 1, f"the story says 'Carried over from an earlier item' {carried} times"
    s.close()
