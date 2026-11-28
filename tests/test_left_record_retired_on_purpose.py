"""
An item its own rule stops raising on purpose, and counts with its dollars, has left the record WITH a reason: the
left_record self-check must not count it (a month-by-month replay of real data, 2026-11-28, class "left_record fails every
month: items whose last window closed leave the record with no route").

The left_record warning (selfcheck.check_vanished, the uroutes branch) counts the items no list shows (expired, done,
closed with proof) that a run no longer produced and no route explains: it was added to catch a rule change or a bug
that drops a whole category of the scoreboard's record ("A rule changed on purpose explains them; a whole signal gone is
a bug"). But detection itself drops some of those items on purpose, every day, and counts them elsewhere:

- SECONDARY_NOT_BILLED (detect.run_secondary_not_billed): once the second payer's last day is more than 30 days past,
  the share is "a loss, not work; counted with its dollars, so a window that closed is never dropped without a trace"
  (ema_counts snb_past_limit and snb_past_limit_cents, in meta ema:wiring). The item, expired since its last day and
  worked by nobody, is not raised again and Detector.save deletes it.
- NO_ANSWER from the 837s (detect.run_837_open_claims): a never-answered claim whose payer id becomes a payer that sends
  no ERA (its tenth claim due with no 835) is moved to the claims no source can check, counted with its dollars (meta
  feed:no_era_837 and the unseen count, ema:unanswered_unseen; with EMA's reports, ema_counts 837_waits_unfound), never
  listed. An expired one leaves the record the same way. The second case below.

The check has no route for either, so it fails on nearly every run: in the replay, all ten monthly runs after January
failed it (68 offender-months, $4,822.30). 54 ($1,859.26) were second-payer items, 53 items and one of them counted
twice (the held August run counted it, safe mode put it back, September dropped it again). Of the 56 second-payer items
that left between two monthly snapshots (3 of them excused by another route), 55 left in the first run after their last
day plus 30 days and the 56th is that item, whose first such run was the held one. 10 ($2,122.25) were 837 claims
whose payer id crossed the no-ERA threshold in the run that dropped them (9 claims due with no ERA at the run before, 10
at that run; nothing new on their own bills). 1 ($573.30) was a claim sent again under the same claim id (the sibling
class of the vanished check), and 3 ($267.49) were other items whose cause is not traced. On the live site's daily runs
the second-payer retirements alone fall on about one day in eight and in half of all weeks. A warning that fires for
designed behaviour, with the advice "a whole signal gone is a bug", hides the real rule change it was built to catch.

The case: Blue Cross paid a December 15, 2025 visit on January 2, 2026 and left $30 of coinsurance; EMA shows an Ambetter
plan on file as the second payer, never billed. Ambetter's 90 days (the safe date) end April 2, its 180 days for a
participating provider (the last day) July 1. The run of July 20 lists the item as expired (past its last day, inside
the month's grace). The run of August 5 no longer raises it, by the rule's own design, and counts its $30.00 in
snb_past_limit. That departure has its reason: left_record must not count it. The control is the same item dropped by a
bug (the rule switched off) on July 25, inside the grace: no counter holds its dollars, and left_record must still count
it, so a fix has to be a route for the rule's own retirement, not a pass for every expired second-payer item.

The second case: nine claims sent in March 2024 to a small plan that never sends an ERA are listed as never answered and
have expired; a tenth claim to the plan comes due in August 2026, the plan is found to send no ERA, and the nine move to
the claims no source can check with their dollars. Its control: the same nine dropped by a bug (the 837 pass switched
off), with nothing counting them, must still be counted.

Each case runs on the server's store file, the compact in-memory store of the serverless host, and that store's image
reopened between the runs (cloud.load_raw); the main case also with an unrelated 835 read between the runs (the live
site's daily case: the vanished check then compares each item's facts, and this item's own claim shows nothing new).

The fix (Detector.retired, handed to the self-check with the counter that holds each item's dollars, the route
retired:<counter> and the rule the decision log names) is checked route by route: the route and the decision's rule are
named, an item a person worked is still kept when its rule retires it, and the retired dollars stay on the figures that
count them (Results' lost to deadlines for a share that expired while it was Work now; the Data tab's claims no source can
check for the no-ERA plan's claims).

Every claim id, payer, amount and date here is invented (tests/ema_fixtures.py and the builders of tests/test_ema_wiring.py).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch import actions  # noqa: E402
from remitwatch import ema_checks as ec  # noqa: E402
from remitwatch import insights  # noqa: E402
from remitwatch import results  # noqa: E402
from remitwatch import ema_ledger as el  # noqa: E402
from remitwatch import ema_reports as er  # noqa: E402
from remitwatch import selfcheck as sc  # noqa: E402
from remitwatch.detect import Detector, ensure_schema  # noqa: E402
from remitwatch.store import Store  # noqa: E402
from tests import ema_fixtures as F  # noqa: E402
from tests import test_ema_wiring as W  # noqa: E402

CAN_SERIALIZE = hasattr(sqlite3.Connection, "serialize")
VISIT = dt.date(2025, 12, 15)
PAID_ON = "2026-01-02"                  # Blue Cross's answer: Ambetter's 90 days end April 2, its 180 days July 1, 2026
POSTED = dt.date(2026, 1, 4)
SECOND = "Ambetter Sample Plan"
LISTED_EXPIRED = "2026-07-20"          # past the last day, inside the month's grace: listed as expired
RETIRED = "2026-08-05"                  # more than 30 days past the last day: the rule stops raising it, on purpose
INSIDE_GRACE = "2026-07-25"            # still inside the grace: the rule would raise it


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    """The settings the EMA wiring tests run under (tests/test_ema_wiring.py's _env)."""
    monkeypatch.setattr(er, "CLAIM_HOST_SUFFIX", F.HOST_SUFFIX)
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    monkeypatch.setenv("REMITWATCH_EMA_STRICT", "1")
    for k in ("VERCEL", "REMITWATCH_EMA_REPORTS", "REMITWATCH_EMA_CLAIM_URL", "REMITWATCH_EMA_PAYER_ALIASES",
              "REMITWATCH_EMA_LEDGER", "REMITWATCH_EMA_REPORT_KEEP", "REMITWATCH_EMA_CLOSES", "REMITWATCH_TODAY",
              "REMITWATCH_STALE_DAYS"):
        monkeypatch.delenv(k, raising=False)
    ec._LAST_POST.clear()
    sc._GROUPS.clear()


class World:
    """The second payer's world in a store of one kind; `image` builds it in a compact store and reopens its serialized
    image between the runs, as the serverless host's next invocation does (cloud.load_raw)."""

    def __init__(self, kind: str, tmp_path):
        if kind == "image" and not CAN_SERIALIZE:
            pytest.skip("this SQLite build cannot serialize a store image")
        self.kind = kind
        self.store = Store(str(tmp_path / "store.sqlite")) if kind == "file" else Store(":memory:", compact=True)
        er.ensure_schema(self.store.conn)
        self.store.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('ema:key_salt', ?)", (W.SALT_HEX,))
        self.store.conn.commit()
        self.ledger = el.Ledger.open(str(tmp_path / "ema_ledger.sqlite"))
        W.remit(self.store, trace="777000931", paid_on=PAID_ON,
                claims=[{"pcn": F.claim_id(31), "last": "SAMPLE", "first": "ECHO",
                         "lines": [(VISIT.isoformat(), "99213", 150, 90, [("CO", "45", 30), ("PR", "2", 30)],
                                    {"allowed_cents": 12000})]}])
        rows = [W.charge(F.link_no(31), patient=2, svc=VISIT, posted=VISIT, secondary=SECOND),
                W.pay(F.link_no(31), "777000931", 90, patient=2, svc=VISIT, posted=POSTED, secondary=SECOND),
                W.adj(F.link_no(31), "CO-45", 30, patient=2, svc=VISIT, posted=POSTED, contractual=True, secondary=SECOND)]
        ar = [F.ar_row(claim=F.claim_id(31), link=F.link_no(31), status="Payment Pending", current=30, aging=POSTED, svc=VISIT,
                       patient=2, secondary=SECOND)]
        W.denial_world(self.store, self.ledger, tmp_path, rows, ar_rows=ar)

    @property
    def conn(self) -> sqlite3.Connection:
        return self.store.conn

    def reopen(self) -> None:
        if self.kind == "image":
            data = self.store.to_bytes()
            self.store.close()
            self.store = Store.from_bytes(data)
            ensure_schema(self.store.conn)

    def run(self, run_id: str, today: str) -> Detector:
        det = Detector(self.store, run_id=run_id, today=today)
        det.run()
        assert det.ema_errors == [], det.ema_errors
        return det

    def second_payer_items(self) -> list[dict]:
        self.conn.row_factory = None
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(findings)")]
        return [dict(zip(cols, r)) for r in self.conn.execute("SELECT * FROM findings WHERE signal = 'SECONDARY_NOT_BILLED'")]

    def check(self, cid: str) -> dict:
        last = sc.meta_get(self.conn, sc.K_LAST) or {}
        got = [c for c in last.get("checks") or [] if c.get("id") == cid]
        assert got, f"no {cid} check in the run's results: {[c.get('id') for c in last.get('checks') or []]}"
        return got[0]

    def counts(self) -> dict:
        row = self.conn.execute("SELECT value FROM meta WHERE key = 'ema:wiring'").fetchone()
        return (json.loads(row[0]) if row and row[0] else {}).get("counts") or {}

    def close(self) -> None:
        self.ledger.close()
        self.store.close()


@pytest.fixture(params=["file", "compact", "image"])
def world(request, tmp_path):
    w = World(request.param, tmp_path)
    yield w
    w.close()


def _listed_expired(w: World) -> str:
    """The first run: the second payer's share, past its last day and inside the grace, is an expired item no list shows."""
    w.run("r1", LISTED_EXPIRED)
    got = w.second_payer_items()
    assert len(got) == 1, [(g["signal"], g["work_state"]) for g in got]
    f = got[0]
    assert (f["appeal_deadline"], f["legal_deadline"]) == ("2026-04-02", "2026-07-01"), (f["appeal_deadline"], f["legal_deadline"])
    assert f["status"] == "open" and f["work_state"] == "expired" and not f.get("stale"), (f["status"], f["work_state"], f["state_basis"])
    assert f["shortfall_cents"] == 3000, f["shortfall_cents"]
    return f["finding_id"]


@pytest.mark.parametrize("between", ["nothing_read", "unrelated_835_read"])
def test_a_second_payer_item_its_rule_retires_past_the_limit_leaves_the_record_with_a_route(world, between):
    fid = _listed_expired(world)
    world.reopen()
    if between == "unrelated_835_read":
        # another patient's visit paid in full on July 30: the store has read a file since the run before, so the check
        # compares each item's facts, as on the live site's daily runs; this item's own claim shows nothing new
        W.remit(world.store, trace="777000932", paid_on="2026-07-30",
                claims=[{"pcn": F.claim_id(32), "last": "SAMPLE", "first": "FOXTROT",
                         "lines": [("2026-07-08", "99212", 90, 90, [], {"allowed_cents": 9000})]}])
    world.run("r2", RETIRED)
    assert world.second_payer_items() == [], "the rule raises a second payer's share more than 30 days past its last day"
    counts = world.counts()
    # the rule's own trace of the retirement: the share is counted with its dollars (already so before any fix)
    assert counts.get("snb_past_limit") == 1 and counts.get("snb_past_limit_cents") == 3000, counts
    left = world.check("left_record")
    assert fid not in (left.get("examples") or []) and "SECONDARY_NOT_BILLED" not in ((left.get("info") or {}).get("by_signal") or {}), (
        "the run retired the expired second-payer item on purpose, past the payer's last day plus the month's grace, and "
        "counted its $30.00 in snb_past_limit, yet the self-check says it left the record with no reason found: "
        f"{left.get('text')!r}; routes {(left.get('info') or {}).get('routes')}, by signal {(left.get('info') or {}).get('by_signal')}")
    assert ((left.get("info") or {}).get("routes") or {}).get("retired:snb_past_limit") == 1, left.get("info")
    # the item's history names the rule that retired it, not "no longer raised"
    assert gone_rules(world.conn, [fid]) == {fid: "detect:snb_past_limit"}


def gone_rules(conn, fids) -> dict:
    """The rule of each item's 'gone' row in the decision log (item_decisions)."""
    conn.row_factory = None
    q = ",".join("?" * len(fids))
    return {f: r for f, r in conn.execute(f"SELECT finding_id, rule FROM item_decisions WHERE kind = 'gone' AND finding_id IN ({q})",
                                          list(fids))}


def test_a_second_payer_item_a_person_worked_is_kept_when_its_rule_retires_it(world):
    """A person billed the second payer (Sent) while the share was Work now; the run past the payer's last day and the
    month's grace does not raise it. A person's work is never dropped by a rule: the item stays on a list, kept by the
    self-check if detection keeps only its stale row, and the retirement route is never used for it."""
    world.run("r0", "2026-03-01")
    got = world.second_payer_items()
    assert len(got) == 1 and got[0]["work_state"] == "work_now", [(g["work_state"], g["state_basis"]) for g in got]
    fid = got[0]["finding_id"]
    assert actions.apply_status(world.conn, fid, "appealed", "Billed the second payer", None, "Test Biller",
                                "2026-03-02T15:00:00+00:00", "open")
    world.conn.commit()
    world.reopen()
    world.run("r2", RETIRED)
    row = world.conn.execute("SELECT status, work_state, COALESCE(stale, 0) FROM findings WHERE finding_id = ?", (fid,)).fetchone()
    assert row is not None and tuple(row)[0] == "appealed" and not tuple(row)[2] and tuple(row)[1] in ("work_now", "waiting"), row
    routes = {**((world.check("vanished").get("info") or {}).get("routes") or {}),
              **((world.check("left_record").get("info") or {}).get("routes") or {})}
    assert not any(k.startswith("retired:") for k in routes), routes


def test_a_retired_share_that_expired_while_work_now_stays_lost_to_its_deadline_on_results(world):
    """Results counts a share that expired while it was Work now as lost to its deadline, with its dollars (the registry,
    results_items). Its rule retires it a month later: the registry keeps that loss (the item is only marked gone)."""
    world.run("r0", "2026-03-01")                                  # inside the safe date: Work now
    results.refresh(world.conn, today="2026-03-01", force=True)
    fid = world.second_payer_items()[0]["finding_id"]
    world.run("r1", LISTED_EXPIRED)                                # past the last day: expired
    results.refresh(world.conn, today=LISTED_EXPIRED, force=True)
    world.reopen()
    world.run("r2", RETIRED)                                       # retired by its rule, counted in snb_past_limit
    assert world.second_payer_items() == []
    results.refresh(world.conn, today=RETIRED, force=True)
    world.conn.row_factory = None
    lost_cents, gone_on, expired_on = world.conn.execute(
        "SELECT lost_cents, gone_on, expired_on FROM results_items WHERE finding_id = ?", (fid,)).fetchone()
    assert (lost_cents, expired_on, gone_on) == (3000, LISTED_EXPIRED, RETIRED), (lost_cents, expired_on, gone_on)
    lost = (results.dashboard(world.conn) or {}).get("lost") or {}
    assert lost.get("cents") == 3000 and lost.get("n") == 1, lost


def test_control_the_same_item_dropped_by_a_bug_inside_its_grace_is_still_counted(world, monkeypatch):
    fid = _listed_expired(world)
    world.reopen()
    # the bug the check was built for: a rule that stops raising its items (here the whole second-payer rule, switched off)
    monkeypatch.setattr(Detector, "run_secondary_not_billed", lambda self: None)
    world.run("r2", INSIDE_GRACE)
    assert world.second_payer_items() == [], "the switched-off rule raised the item anyway"
    assert not world.counts().get("snb_past_limit"), world.counts()      # nothing holds its dollars
    left = world.check("left_record")
    assert left["status"] == "fail" and fid in (left.get("examples") or []), left
    assert ((left.get("info") or {}).get("by_signal") or {}).get("SECONDARY_NOT_BILLED") == 1, left.get("info")
    assert ((left.get("info") or {}).get("by_state") or {}).get("expired") == 1, left.get("info")


# ======================================================================== the same defect: a payer found to send no ERA
# A never-answered 837 claim is chased from the 835s only while its payer id is one whose claims get ERAs. Once a payer id
# has NO_ERA_MIN_CLAIMS (10) claims due and at most 5% of them answered by any 835, its claims are "claims no source can
# check": counted with their dollars (meta feed:no_era_837, and the unseen count), never listed (detect.run_837_open_claims).
# In the replay, 10 of the 68 offender-months were such claims: their payer id had 9 claims due with no ERA at the run
# before and 10 at the run that dropped them; nothing new was on their own bill (no 835, no 837, no export row).
SMALL = ("SMALL PLAN 07", "SP007")


def _old_anthem(i: int, dos: str) -> str:
    from tests.test_features import claim, svc
    return claim(i, svc("99214", "223.65", "117.60", "117.60", "CAS*CO*45*106.05~", dos=dos), dos=dos)


class World837:
    """Nine claims sent in March 2024 to a small plan that never sends an ERA, and Anthem's remittances from January 2024 to
    the run: the nine are never-answered claims whose every window has closed (expired, no list shows them)."""

    def __init__(self, kind: str, tmp_path):
        from tests.test_837_sends import _remits, clm837, send
        if kind == "image" and not CAN_SERIALIZE:
            pytest.skip("this SQLite build cannot serialize a store image")
        self.kind, self.tmp = kind, tmp_path
        self.store = Store(str(tmp_path / "s.sqlite")) if kind == "file" else Store(":memory:", compact=True)
        _remits(self.store, tmp_path, [_old_anthem(i, "20231201") for i in range(1, 26)], "20240110")
        _remits(self.store, tmp_path, [_old_anthem(i, "20260601") for i in range(40, 65)], "20260720")
        self.small = [f"CB0SPN{i:03d}C017" for i in range(9)]
        send(self.store, tmp_path, [clm837(c, [("99214", "100.00", "20240304")], payer=SMALL, member=f"S{i:08d}")
                                    for i, c in enumerate(self.small)], "20240306")

    def tenth_claim_comes_due(self) -> None:
        """A tenth claim to the same plan (June 2026) that no ERA answers either, and Anthem's August remittances, so that
        it is due: the plan now has ten claims due and none answered by an 835."""
        from tests.test_837_sends import _remits, clm837, send
        send(self.store, self.tmp, [clm837("CB0SPN009C017", [("99214", "100.00", "20260618")], payer=SMALL, member="S00000009")],
             "20260620")
        _remits(self.store, self.tmp, [_old_anthem(i, "20260715") for i in range(70, 95)], "20260820")

    def reopen(self) -> None:
        if self.kind == "image":
            data = self.store.to_bytes()
            self.store.close()
            self.store = Store.from_bytes(data)
            ensure_schema(self.store.conn)

    def run(self, run_id: str, today: str) -> Detector:
        det = Detector(self.store, run_id=run_id, today=today)
        det.run()
        return det

    def chased(self) -> dict[str, dict]:
        self.store.conn.row_factory = None
        cols = [r[1] for r in self.store.conn.execute("PRAGMA table_info(findings)")]
        return {r["patient_control_number"]: r for r in (dict(zip(cols, x)) for x in self.store.conn.execute(
            "SELECT * FROM findings WHERE signal = 'NO_ANSWER' AND COALESCE(stale, 0) = 0"))}

    def check(self, cid: str) -> dict:
        last = sc.meta_get(self.store.conn, sc.K_LAST) or {}
        got = [c for c in last.get("checks") or [] if c.get("id") == cid]
        assert got, f"no {cid} check in the run's results"
        return got[0]

    def no_era(self) -> list[dict]:
        row = self.store.conn.execute("SELECT value FROM meta WHERE key = 'feed:no_era_837'").fetchone()
        return json.loads(row[0]) if row and row[0] else []


@pytest.fixture(params=["file", "compact", "image"])
def world837(request, tmp_path):
    w = World837(request.param, tmp_path)
    yield w
    w.store.close()


def _nine_expired(w: World837) -> dict[str, str]:
    w.run("n1", "2026-07-25")
    got = w.chased()
    assert sorted(set(w.small) & set(got)) == sorted(w.small), sorted(got)
    states = {got[c]["work_state"] for c in w.small}
    assert states == {"expired"}, {c: (got[c]["work_state"], got[c]["state_basis"]) for c in w.small}
    return {c: got[c]["finding_id"] for c in w.small}


def test_never_answered_claims_of_a_payer_found_to_send_no_era_leave_the_record_with_a_route(world837):
    ids = _nine_expired(world837)
    world837.reopen()
    world837.tenth_claim_comes_due()
    world837.run("n2", "2026-08-25")
    assert not set(world837.small) & set(world837.chased()), "a claim of a payer that sends no ERA is still listed"
    plan = [p for p in world837.no_era() if p.get("payer_id") == SMALL[1]]
    # the detection's own trace: the plan's ten claims are counted with their dollars as claims no source can check
    assert len(plan) == 1 and plan[0].get("unchecked") == 10 and plan[0].get("unchecked_cents") == 100000, world837.no_era()
    left = world837.check("left_record")
    counted = sorted(f for f in ids.values() if f in (left.get("examples") or []))
    assert not counted and "NO_ANSWER" not in ((left.get("info") or {}).get("by_signal") or {}), (
        "the run moved the nine expired claims to the claims no source can check (the plan was found to send no ERA) and "
        "counted their $900.00 on the Data tab, yet the self-check says they left the record with no reason found: "
        f"{left.get('text')!r}; routes {(left.get('info') or {}).get('routes')}, by signal {(left.get('info') or {}).get('by_signal')}")
    assert ((left.get("info") or {}).get("routes") or {}).get("retired:unseen") == 9, left.get("info")
    assert gone_rules(world837.store.conn, list(ids.values())) == {f: "detect:unseen" for f in ids.values()}
    # their dollars are on the Data tab: the claims no source can check (the unseen count), each one by its claim id
    u = insights.unseen_summary(world837.store.conn, serverless=False) or {}
    assert u.get("n") == 10 and u.get("cents") == 100000, {k: u.get(k) for k in ("n", "cents")}
    raw = json.loads(world837.store.conn.execute("SELECT value FROM meta WHERE key = 'ema:unanswered_unseen'").fetchone()[0])
    assert set(world837.small) <= {c[0] for c in raw.get("claims") or []}


def test_control_the_same_claims_dropped_by_a_bug_are_still_counted(world837, monkeypatch):
    ids = _nine_expired(world837)
    world837.reopen()
    monkeypatch.setattr(Detector, "run_837_open_claims", lambda self: None)      # the bug: the 837 pass stops raising its items
    world837.run("n2", "2026-07-30")
    assert not set(world837.small) & set(world837.chased())
    assert not [p for p in world837.no_era() if p.get("payer_id") == SMALL[1] and p.get("unchecked")], world837.no_era()
    left = world837.check("left_record")
    assert left["status"] == "fail" and ((left.get("info") or {}).get("by_signal") or {}).get("NO_ANSWER") == 9, left
    assert set(left.get("examples") or []) >= set(ids.values()) or left["n"] >= 9, left


# ================================================================== the review's cases: where the retired dollars show
def test_a_second_payers_share_raised_past_its_last_day_and_retired_is_on_the_data_tab(world):
    """The share is first seen on a run already past the second payer's last day (listed as expired, never Work now), and
    the next run retires it past the month's grace (snb_past_limit). Results' lost to deadlines never records it (it
    counts only items that expired while Work now), and left_record lets it go by its route. Its $30.00 must still be on a
    page (owner's rules 1 and 2): the Data tab's 'Running on its own' panel names the second payers' shares past their
    limit, with their dollars, every run (from meta ema:wiring, so the dashboard's copy shows it too)."""
    world.run("r1", LISTED_EXPIRED)
    results.refresh(world.conn, today=LISTED_EXPIRED, force=True)
    got = world.second_payer_items()
    assert len(got) == 1 and got[0]["work_state"] == "expired", [(g["work_state"],) for g in got]
    world.reopen()
    world.run("r2", RETIRED)
    results.refresh(world.conn, today=RETIRED, force=True)
    assert world.second_payer_items() == []
    assert world.counts().get("snb_past_limit_cents") == 3000, world.counts()
    lost = (results.dashboard(world.conn) or {}).get("lost") or {}
    assert not lost.get("cents"), lost                          # never Work now: not lost to its deadline on Results
    left = world.check("left_record")
    assert left["status"] == "ok" and ((left.get("info") or {}).get("routes") or {}).get("retired:snb_past_limit") == 1, left
    panel = sc.panel(world.conn)
    assert panel.get("second_payer_past_limit") == {"n": 1, "cents": 3000}, panel.get("second_payer_past_limit")
    line = [t for t in panel["lines"] if "second payer" in t and "past" in t]
    assert line and "$30" in line[0] and "1 share" in line[0], panel["lines"]


@pytest.mark.parametrize("kind", ["file", "compact"])
@pytest.mark.parametrize("reason, retired", [(None, True), ("no_ema_reports", True), ("payer_remittances_stop", False)])
def test_only_the_named_unseen_reasons_retire_a_claims_item(tmp_path, kind, reason, retired):
    """Detection retires a never-answered claim's item on purpose when it moves the claim to the claims no source can
    check: its payer sends no ERA and the store has no EMA report (no_ema_reports), or EMA's claim for it was not found
    while it waits (no reason). A claim whose payer's remittances stop before its due day (payer_remittances_stop: an ERA
    outage, or EMA's postings no longer reaching that day) is counted unseen too, but it is not retired: a listed one stays
    on Work now, kept by the vanished check, and an expired one is counted by left_record, so an outage never empties the
    billers' list in silence."""
    s = Store(str(tmp_path / "s.sqlite")) if kind == "file" else Store(":memory:", compact=True)
    try:
        det = Detector(s, run_id="u1", today="2026-08-25")
        cd = {"pcn": "ZZ00T0061C017", "s837": None, "grp": "Testcare Health", "sent": "2026-05-01", "due": "2026-05-31",
              "source": "837"}
        det._count_unseen(cd, [("99213", "2026-04-20", 15000, 1, None, None)], None, reason=reason)
        from remitwatch.detect import OPEN_837_ID, _fid
        fid = _fid(OPEN_837_ID, "ZZ00T0061C017")
        assert (det.retired.get(fid) == "unseen") is retired, (reason, det.retired)
        assert [r["pcn"] for r in det._unseen_rows] == ["ZZ00T0061C017"]       # counted unseen with its dollars either way
    finally:
        s.close()
