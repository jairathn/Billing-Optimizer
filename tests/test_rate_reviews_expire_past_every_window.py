"""
A routine-rate review (SECOND_RATE) or a clinician's rate gap (PROVIDER_RATE_GAP) that nobody worked leaves Work now the
day after its last claim's filing window closes, like every other item past every window (replay-fix-2a A1, from a
month-by-month replay of real data: 16 routine-rate reviews and 4 clinician rate gaps made inside their windows were still
Work now months after every claim's window had closed, $8,096.41 with the reviews ranked by their full dollars and the
gaps at $0; a fresh analysis of the same data never makes them).

Both kinds stand for many claims, each with its own filing window (evidence 'deadlines'); detection dates the item by the
last of them "so the pattern stays in the open work while any of its claims can still be raised", and refuses a new one
whose windows have all closed (trace finding T-N4: "no work to do"). states.py sent every contract question to Work now
before it looked at a window, so once the last window closed an unworked review stayed Work now for good, the Results
scoreboard never counted it lost to deadlines, the at-risk figure dropped it the day after its last date, and the
self-check past_window looked only at the rule 'open'. Here: it expires (a person's Started or Sent still decides), the
scoreboard counts each claim's dollars lost once, the self-check guards it, and the story says when every window closed.

Each case runs the way the live server does (an analysis inside the windows, then the day after the last one closes) on
the server's store file, the compact in-memory store and that store's serialized image (the serverless host's next
invocation). Every name, member number, NPI, claim id and amount is invented (the builders of
tests/test_sizes_and_rates.py).
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch import actions, results, selfcheck, story  # noqa: E402
from remitwatch.detect import Detector, ensure_schema  # noqa: E402
from remitwatch.store import Store  # noqa: E402
from tests.test_remitwatch import remit  # noqa: E402
from tests.test_sizes_and_rates import DOC, DOC2, clean, medicare_em  # noqa: E402

CAN_SERIALIZE = hasattr(sqlite3.Connection, "serialize")
FOUND = "2026-06-15"            # the analysis that finds the patterns: every claim inside its window
PAID = "20260601"               # the day the payer paid every claim of this file (one window for all of them)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    for k in ("VERCEL", "REMITWATCH_EMA_REPORTS", "REMITWATCH_EMA_LEDGER", "REMITWATCH_TODAY", "REMITWATCH_STALE_DAYS"):
        monkeypatch.delenv(k, raising=False)


class World:
    """One store of a kind, fed remittance files one at a time; `image` is the compact store saved and loaded again
    between analyses (cloud.load_raw's round trip)."""

    def __init__(self, tmp_path, kind: str):
        self.tmp, self.kind, self.n = tmp_path, kind, 0
        self.store = Store(str(tmp_path / "s.sqlite")) if kind == "file" else Store(":memory:", compact=True)

    def remit(self, claims: list[str], pay_date: str, payer: str = "ANTHEM", payer_id: str = "ANTHM") -> None:
        for i in range(0, len(claims), 40):
            self.n += 1
            p = self.tmp / f"r{self.n:04d}.RMT"
            p.write_bytes(remit("".join(claims[i:i + 40]), "100.00", payer=payer, payer_id=payer_id, seg_count=99,
                                isa_ctrl=f"{self.n:09d}", pay_date=pay_date))
            self.store.ingest_835_paths([str(p)])

    def run(self, today: str) -> None:
        if self.kind == "image":
            data = self.store.to_bytes()
            self.store.close()
            self.store = Store.from_bytes(data)
            ensure_schema(self.store.conn)
        Detector(self.store, run_id=f"run-{today}", today=today).run()
        results.update_registry(self.store.conn, today=today)      # the Results tab's registry, as the cycle keeps it
        self.store.conn.commit()

    def one(self, sig: str) -> dict:
        got = [dict(r) for r in self.store.query(f"SELECT * FROM findings WHERE COALESCE(stale,0)=0 AND signal='{sig}'")]
        assert len(got) == 1, [(r["signal"], r["status"], r["work_state"]) for r in got]
        r = got[0]
        r["ev"] = json.loads(r["evidence"]) if r.get("evidence") else {}
        return r

    def open_ids(self, sig: str) -> set[str]:
        return {r[0] for r in self.store.conn.execute(
            "SELECT finding_id FROM findings WHERE COALESCE(stale,0)=0 AND signal=? AND status IN ('open','in_progress','appealed') "
            "AND work_state IN ('work_now','waiting')", (sig,))}


@pytest.fixture(params=["file", "compact", "image"])
def kind(request):
    if request.param == "image" and not CAN_SERIALIZE:
        pytest.skip("this SQLite build cannot serialize a store image")
    return request.param


def _feed_rates(w: World) -> None:
    """Anthem pays a physician's 99214 $117.60 on 25 visits and $100.00 on 6 (one routine-rate review, $105.60), and his
    11102 $112.74 on 25 visits and $90.99 on one (a one-off underpayment, $21.75: the control). Every claim paid the same
    day, so every claim has the same filing window."""
    w.remit(medicare_em(1, 25, DOC, "127.80", "1*DOCTOR*ONE"), "20260301", payer="KY MEDICARE", payer_id="15102")
    w.remit(clean(100, 25, "99214", "223.65", "117.60") + clean(200, 6, "99214", "223.65", "100.00")
            + clean(300, 25, "11102", "150.00", "112.74") + clean(400, 1, "11102", "150.00", "90.99"), PAID)


def _feed_gap(w: World) -> None:
    """Anthem pays one physician's biopsies $112.74 (25 claims) and another's $90.99 on all 12 of his: a clinician's
    rate gap of $261.00, every claim paid the same day."""
    w.remit(medicare_em(1, 25, DOC, "127.80", "1*DOCTOR*ONE") + medicare_em(40, 25, DOC2, "127.80", "1*DOCTOR*TWO"),
            "20260301", payer="KY MEDICARE", payer_id="15102")
    w.remit(clean(100, 25, "11102", "150.00", "112.74")
            + clean(200, 12, "11102", "150.00", "90.99", npi=DOC2, prov="1*DOCTOR*TWO"), PAID)


def _day_after(d: str, days: int = 1) -> str:
    return (date.fromisoformat(d) + timedelta(days=days)).isoformat()


def _found_inside_its_window(w: World, sig: str) -> dict:
    w.run(FOUND)
    item = w.one(sig)
    assert (item["status"], item["work_state"]) == ("open", "work_now"), (item["status"], item["work_state"], item["state_rule"])
    assert item["appeal_deadline"] and item["appeal_deadline"] > FOUND and item["ev"]["lines_open"] == item["ev"]["lines"]
    # the item's date is the last of its claims' windows: once it passes, no claim of it can be raised
    assert item["appeal_deadline"] == max(d[0] for d in item["ev"]["deadlines"] if d[0])
    return item


def test_a_routine_rate_review_nobody_worked_leaves_work_now_when_its_last_window_closes(tmp_path, kind):
    w = World(tmp_path, kind)
    _feed_rates(w)
    item = _found_inside_its_window(w, "SECOND_RATE")
    one_off = w.one("UNDERPAID_VS_OWN_RATE")
    assert one_off["appeal_deadline"] == item["appeal_deadline"], "the control's window differs: pick other dates"
    w.run(_day_after(item["appeal_deadline"]))         # the daily run the day after every claim's window closed
    one_off, item = w.one("UNDERPAID_VS_OWN_RATE"), w.one("SECOND_RATE")
    assert (one_off["work_state"], one_off["state_rule"]) == ("expired", "expired")     # control: one line, same window
    assert item["status"] == "open" and item["ev"]["lines_open"] == 0            # nobody worked it; no claim can be raised
    assert item["work_state"] != "work_now", (
        f"a routine-rate review nobody worked is still Work now ({item['state_rule']}) the day after the last of its "
        f"{item['ev']['lines']} claims' windows closed, ranked at its full ${item['shortfall_cents'] / 100:,.2f} "
        f"(value {item.get('expected_value_cents')}); the same money paid on one line expired that day")
    assert item["work_state"] == "expired", (item["work_state"], item["state_rule"])
    w.store.close()


def test_its_money_is_counted_lost_to_deadlines_not_dropped_from_the_scoreboard(tmp_path, kind):
    """Rule 5: at risk in the next 14 days the week before its last window, lost to deadlines the day after."""
    w = World(tmp_path, kind)
    _feed_rates(w)
    item = _found_inside_its_window(w, "SECOND_RATE")
    fid, last = item["finding_id"], item["appeal_deadline"]
    w.run(_day_after(last, -7))
    risk = results.live_figures(w.store.conn, today=_day_after(last, -7))["at_risk"]
    assert risk["cents"] >= item["shortfall_cents"], "control: the week before, its dollars are at risk in the next 14 days"
    w.run(_day_after(last))
    risk = results.live_figures(w.store.conn, today=_day_after(last))["at_risk"]
    lost = w.store.conn.execute("SELECT expired_on, lost_cents FROM results_items WHERE finding_id=?", (fid,)).fetchone()
    assert risk["cents"] < item["shortfall_cents"]                                  # no longer at risk...
    assert lost is not None and lost[1] == item["shortfall_cents"], (
        f"${item['shortfall_cents'] / 100:,.2f} at risk the week before its last window is neither at risk nor lost to "
        f"deadlines the day after (registry: {tuple(lost) if lost else None}): it left the scoreboard silently")
    w.store.close()


def test_a_clinicians_rate_gap_with_nothing_left_to_raise_is_not_work_now_at_zero(tmp_path, kind):
    w = World(tmp_path, kind)
    _feed_gap(w)
    gap = _found_inside_its_window(w, "PROVIDER_RATE_GAP")
    assert gap["shortfall_cents"] == 12 * (11274 - 9099)
    w.run(_day_after(gap["appeal_deadline"]))
    gap = w.one("PROVIDER_RATE_GAP")
    assert gap["status"] == "open" and gap["shortfall_cents"] == 0 and gap["ev"]["lines_open"] == 0
    assert gap["work_state"] != "work_now", (
        f"a clinician's rate gap worth $0.00 (every claim's window closed, nobody worked it) is still Work now "
        f"({gap['state_rule']}) on the owner's list")
    w.store.close()


@pytest.mark.parametrize("feed,sig", [(_feed_rates, "SECOND_RATE"), (_feed_gap, "PROVIDER_RATE_GAP")])
def test_the_daily_store_and_a_fresh_analysis_list_the_same_open_work(tmp_path, kind, feed, sig):
    """Path independence: the store the daily runs kept and a first analysis of the same files on the same day list the
    same open items (a fresh analysis never makes a rate item whose windows have all closed, trace finding T-N4)."""
    (tmp_path / "daily").mkdir()
    (tmp_path / "fresh").mkdir()
    daily = World(tmp_path / "daily", kind)
    feed(daily)
    item = _found_inside_its_window(daily, sig)
    day = _day_after(item["appeal_deadline"])
    daily.run(day)
    fresh = World(tmp_path / "fresh", kind)
    feed(fresh)
    fresh.run(day)
    assert not fresh.open_ids(sig)                       # control: T-N4, no new rate item past every window
    assert daily.open_ids(sig) == fresh.open_ids(sig), (
        f"{sig}: the daily runs keep {len(daily.open_ids(sig))} item(s) open past every window that a fresh analysis of "
        "the same data never lists")
    daily.store.close()
    fresh.store.close()


# ------------------------------------------------------------------------------------------- the self-check guards it
@pytest.mark.parametrize("feed,sig", [(_feed_rates, "SECOND_RATE"), (_feed_gap, "PROVIDER_RATE_GAP")])
def test_the_self_check_catches_a_review_or_gap_left_work_now_past_every_window(tmp_path, kind, feed, sig):
    """past_window ('nothing nobody has worked stays Work now past the payer's last window') reads the owner's question on
    an item that carries a filing window as automatic: a review or rate gap Work now the day after its last claim's window
    is caught, as the old rule left them; one a person marked Started, or a contract question with no window, is not."""
    w = World(tmp_path, kind)
    feed(w)
    item = _found_inside_its_window(w, sig)
    day = _day_after(item["appeal_deadline"])
    w.run(day)
    fid = item["finding_id"]
    assert selfcheck.check_past_window(w.store.conn, day)["status"] == "ok"            # the rule expired it: nothing to catch
    w.store.conn.execute("UPDATE findings SET work_state='work_now', state_rule='owner_decision', "
                         "state_basis='A contract or billing-policy question for the owner, not a claim to appeal' WHERE finding_id=?", (fid,))
    w.store.conn.commit()
    r = selfcheck.check_past_window(w.store.conn, day)
    assert r["status"] == "fail" and fid in r["examples"], r               # as the old rule left it: caught
    # a contract question with no window of its own stays the owner's for as long as it takes
    w.store.conn.execute("UPDATE findings SET appeal_deadline=NULL WHERE finding_id=?", (fid,))
    w.store.conn.commit()
    assert fid not in selfcheck.check_past_window(w.store.conn, day)["examples"]
    w.store.close()


@pytest.mark.parametrize("feed,sig", [(_feed_rates, "SECOND_RATE"), (_feed_gap, "PROVIDER_RATE_GAP")])
def test_a_review_or_gap_a_person_started_stays_by_their_status_past_every_window(tmp_path, kind, feed, sig):
    """A person's Started wins over the windows (the owner is taking the rate up with the payer): Work now by that person's
    status, never expired, never caught by the self-check, and its next step says every claim's window has closed."""
    w = World(tmp_path, kind)
    feed(w)
    item = _found_inside_its_window(w, sig)
    fid = item["finding_id"]
    assert actions.apply_status(w.store.conn, fid, "in_progress", actor="Owner One", at=f"{FOUND}T15:00:00")
    w.store.conn.commit()
    day = _day_after(item["appeal_deadline"])
    w.run(day)
    got = w.one(sig)
    assert (got["status"], got["work_state"], got["state_rule"]) == ("in_progress", "work_now", "person_started")
    assert fid not in selfcheck.check_past_window(w.store.conn, day)["examples"]
    step = story.next_step(w.store.conn, fid, today=day)
    assert step["route"] == "contract", step["route"]
    assert any(s.startswith("Every claim it lists is past its filing window") for s in step["steps"]), step["steps"]
    w.store.close()


def test_the_next_step_of_an_open_review_counts_the_claims_still_inside_their_windows(tmp_path, kind):
    w = World(tmp_path, kind)
    _feed_rates(w)
    item = _found_inside_its_window(w, "SECOND_RATE")
    step = story.next_step(w.store.conn, item["finding_id"], today=FOUND)
    assert step["route"] == "contract"
    assert any(s.startswith("6 of its 6 claims are still inside their filing window; the last closes on ") for s in step["steps"]), \
        step["steps"]
    w.store.close()


# --------------------------------------------------------------------------------- the scoreboard counts it once
def test_a_rate_gap_is_counted_lost_to_its_deadline_with_the_claims_it_was_listed_for(tmp_path, kind):
    """A clinician's rate gap is worth what can still be raised, so its dollars fall to $0 as its claims' windows close. The
    registry keeps it (category 'contract'), and when it expires its claims' dollars are counted lost to the deadline: the
    claims whose window closed while it was listed."""
    w = World(tmp_path, kind)
    _feed_gap(w)
    gap = _found_inside_its_window(w, "PROVIDER_RATE_GAP")
    fid, last = gap["finding_id"], gap["appeal_deadline"]
    w.run(_day_after(last, -7))
    w.run(_day_after(last))
    row = w.store.conn.execute("SELECT expired_on, lost_cents, lost_on FROM results_items WHERE finding_id=?", (fid,)).fetchone()
    assert row is not None and row[0] == _day_after(last) and row[1] == 12 * (11274 - 9099) and row[2] == last, row
    lost, lists = results._lost(results.load_registry(w.store.conn))
    assert lost["cents"] == 12 * (11274 - 9099) and [fid, 12 * (11274 - 9099)] in lists["lost"]
    # it brings no money of its own back: no money figure counts it (returnable False)
    assert results.load_registry(w.store.conn)[fid]["returnable"] is False
    w.store.close()


def _one_off_lost(conn, fid: str, line_key: str, cents: int, day: str) -> None:
    """The registry row of a one-off underpayment of `line_key` counted lost to its deadline on `day` (as update_registry
    writes it)."""
    conn.execute("""INSERT INTO results_items (finding_id, first_flagged, last_seen, signal, category, flagged_cents, shortfall_cents,
                    last_state, last_status, expired_on, lost_on, lost_cents, flag_info, item)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (fid, FOUND, day, "UNDERPAID_VS_OWN_RATE", "underpayment", cents, cents, "expired", "open", day, day, cents, "{}",
                  json.dumps({"finding_id": fid, "signal": "UNDERPAID_VS_OWN_RATE", "line_key": line_key})))
    conn.commit()


def test_each_lines_dollars_are_counted_lost_once_beside_the_one_off_items_of_its_lines(tmp_path, kind):
    """Rule 5's scoreboard counts a dollar lost once. A line a one-off item already counted lost adds nothing to the review
    that lists it when the review expires; a one-off of a line the review counted, expiring later, is the review's loss,
    never a second one."""
    w = World(tmp_path, kind)
    _feed_rates(w)
    item = _found_inside_its_window(w, "SECOND_RATE")
    fid, ev = item["finding_id"], item["ev"]
    keys, gaps = ev["line_keys"], [ev["usual_cents"] - c[2] for c in ev["claims"]]
    assert sum(gaps) == item["shortfall_cents"] and len(keys) == 6
    results.update_registry(w.store.conn, today=FOUND)
    _one_off_lost(w.store.conn, "ONEOFF-1", keys[0], gaps[0], _day_after(FOUND, 3))
    w.run(_day_after(item["appeal_deadline"]))
    reg = results.load_registry(w.store.conn)
    assert reg[fid]["lost_cents"] == item["shortfall_cents"] - gaps[0], (reg[fid]["lost_cents"], item["shortfall_cents"], gaps[0])
    assert sorted(reg[fid]["flag_info"]["lost_lines"]) == sorted(keys[1:])
    control = w.one("UNDERPAID_VS_OWN_RATE")             # the one-off underpayment of another code, lost the same day
    assert reg[control["finding_id"]]["lost_cents"] == control["shortfall_cents"] == 2175
    lost, _ = results._lost(reg)
    # every line once: the review's line the one-off counted, the review's other five, and the control
    assert lost["cents"] == item["shortfall_cents"] + 2175 and lost["n"] == 3
    # a one-off item of a line the review counted, which expires after it: no second loss
    c = w.store.conn
    c.execute("""INSERT INTO findings (finding_id, signal, category, line_key, shortfall_cents, first_seen, work_state, status, stale)
                 VALUES ('ONEOFF-2', 'UNDERPAID_VS_OWN_RATE', 'underpayment', ?, ?, ?, 'work_now', 'open', 0)""",
              (keys[1], gaps[1], f"{FOUND}T12:00:00"))
    c.commit()
    day = _day_after(item["appeal_deadline"], 2)
    results.update_registry(c, today=day)
    c.execute("UPDATE findings SET work_state='expired' WHERE finding_id='ONEOFF-2'")
    c.commit()
    results.update_registry(c, today=_day_after(day))
    reg = results.load_registry(c)
    assert reg["ONEOFF-2"]["expired_on"] and reg["ONEOFF-2"]["lost_cents"] == 0
    assert reg["ONEOFF-2"]["flag_info"]["lost_elsewhere"] == fid
    lost, lists = results._lost(reg)
    assert lost["cents"] == item["shortfall_cents"] + 2175 and "ONEOFF-2" not in {x[0] for x in lists["lost"]}
    w.store.close()


def test_a_reviews_claims_already_past_their_window_when_it_was_found_are_not_counted_lost(tmp_path, kind):
    """A review found when some of its claims were already past their windows (T-N4 refuses a review only when every claim
    is) was never listed as work for those claims: the scoreboard counts the claims whose window closed while it was
    listed. Aetna answers a payment review within 180 days of its payment."""
    w = World(tmp_path, kind)
    aetna = {"payer": "AETNA", "payer_id": "AETN1"}
    w.remit(medicare_em(1, 25, DOC, "127.80", "1*DOCTOR*ONE"), "20260110", payer="KY MEDICARE", payer_id="15102")
    w.remit(clean(100, 25, "99214", "223.65", "117.60", dos="20260105"), "20260110", **aetna)
    w.remit(clean(200, 3, "99214", "223.65", "100.00", dos="20260105"), "20260110", **aetna)    # their windows close Jul 9
    w.remit(clean(300, 3, "99214", "223.65", "100.00", dos="20260720"), "20260801", **aetna)    # theirs close Jan 28, 2027
    w.run("2026-09-01")
    item = w.one("SECOND_RATE")
    dues = sorted({c[3] for c in item["ev"]["claims"]})
    assert dues == ["2026-07-09", "2027-01-28"] and item["appeal_deadline"] == "2027-01-28", dues
    w.run("2027-01-29")
    assert w.one("SECOND_RATE")["work_state"] == "expired"
    lost = w.store.conn.execute("SELECT lost_cents FROM results_items WHERE finding_id=?", (item["finding_id"],)).fetchone()[0]
    assert lost == 3 * (11760 - 10000) and item["shortfall_cents"] == 6 * (11760 - 10000), (lost, item["shortfall_cents"])
    w.store.close()


# ------------------------------------------------------------- a review listed again for new claims (review r1)
def _listed_again(tmp_path, kind):
    """Anthem's routine $100.00 for a 99214 (usual $117.60): 6 claims, found inside their windows, expire unworked (lost:
    6 x $17.60). Three more visits of the same year are then paid at the same $100.00: the same review is listed again
    (Work now) for them, with a later last window."""
    w = World(tmp_path, kind)
    _feed_rates(w)
    item = _found_inside_its_window(w, "SECOND_RATE")
    fid, last = item["finding_id"], item["appeal_deadline"]
    w.run(_day_after(last))
    assert w.one("SECOND_RATE")["work_state"] == "expired"
    lost1 = w.store.conn.execute("SELECT lost_cents FROM results_items WHERE finding_id=?", (fid,)).fetchone()[0]
    assert lost1 == 6 * 1760, lost1
    w.remit(clean(500, 3, "99214", "223.65", "100.00"), _day_after(last, 3).replace("-", ""))
    w.run(_day_after(last, 5))
    again = w.one("SECOND_RATE")
    assert again["finding_id"] == fid and again["work_state"] == "work_now", (again["finding_id"] == fid, again["work_state"])
    assert again["ev"]["lines"] == 9 and again["appeal_deadline"] > last
    return w, fid, again


def test_a_review_listed_again_with_new_claims_counts_their_dollars_lost_when_it_expires_again(tmp_path, kind):
    """Nobody works the review listed again, and the new claims' windows close too. Rule 5: their 3 x $17.60 are lost to
    the deadline as well, each dollar once (the 6 counted the first time are not counted again)."""
    w, fid, again = _listed_again(tmp_path, kind)
    w.run(_day_after(again["appeal_deadline"]))
    assert w.one("SECOND_RATE")["work_state"] == "expired"
    reg = results.load_registry(w.store.conn)[fid]
    assert reg["lost_cents"] == 9 * 1760, (
        f"the review counted ${reg['lost_cents'] / 100:,.2f} lost; the 3 claims it was listed again for (${3 * 17.60:,.2f}) "
        "expired unworked and are lost to no deadline on the scoreboard")
    assert len(reg["flag_info"]["lost_lines"]) == 9 and len(set(reg["flag_info"]["lost_lines"])) == 9
    assert reg["lost_on"] == again["appeal_deadline"], (reg["lost_on"], again["appeal_deadline"])
    lost, _lists = results._lost(results.load_registry(w.store.conn))
    assert lost["cents"] == 9 * 1760 + 2175, lost        # the review once (9 claims) and the control's one-off
    # the run after changes nothing: nothing is counted twice
    w.run(_day_after(again["appeal_deadline"], 2))
    assert results.load_registry(w.store.conn)[fid]["lost_cents"] == 9 * 1760
    w.store.close()


def test_while_listed_again_the_claims_already_counted_lost_are_not_at_risk_again(tmp_path, kind):
    """The week before the new claims' windows close, the scoreboard's 'at risk in the next 14 days' and 'lost to
    deadlines' never both hold the 6 claims already counted lost (each dollar once); the list the figure opens agrees."""
    w, fid, again = _listed_again(tmp_path, kind)
    wk = _day_after(again["appeal_deadline"], -7)
    w.run(wk)
    figs = results.live_figures(w.store.conn, today=wk)
    risk = figs["at_risk"]["cents"]
    lost = results.load_registry(w.store.conn)[fid]["lost_cents"]
    assert lost == 6 * 1760
    assert risk == 3 * 1760, (
        f"at risk ${risk / 100:,.2f} while ${lost / 100:,.2f} of the same review's claims are already counted lost: the 6 "
        "dead claims are on the scoreboard twice")
    listed = results.list_entries(w.store.conn, "at_risk", wk)
    assert sum(e[1] for e in listed) == risk and fid in {e[0] for e in listed}, listed
    w.store.close()
