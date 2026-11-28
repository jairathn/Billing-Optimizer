"""
Two self-check bugs a month-by-month replay of real data found (2026-11-28), each confirmed by a second check that could
not refute it. Invented data only (tests/ema_fixtures.py and the builders of tests/test_selfcheck.py): made-up payers,
claim numbers, check numbers, amounts and dates.

1. A payer name that changes payer group is not money leaving the posting ledger. selfcheck.check_posted keeps a
   high-water mark per (service month, closed posting month, payer group, measure) and gives each ledger payer name its
   group by the name rules of now. Those rules change on their own: an alias learned or dropped from the weekly exports'
   payer ids (ema_ledger.refresh calls learn_payer_aliases every cycle), the owner's aliases (REMITWATCH_EMA_PAYER_ALIASES),
   a release's name rules (codes.PAYER_RULES). The marks kept the old group, so the moved name's money read as a fall of
   its old group: posted_money (critical, it holds the run) put the app in safe mode and blamed "a newer export of the same
   dates" that did not exist (the replay's August: 93 cells, $477,532.67 of marks, 597 new items held). A real loss must
   still be caught, with its own dollars, whether it is read after the change or with it.
2. A held run must not move the vanished check's fingerprints. after_detection saved them in a held run too, made from the
   held run's data for the worklist safe mode put back, so the next good run found no new facts for answers that arrived
   before the held run and kept the items they settled on Work now as "nothing explains why" (the replay's September: 67
   items, $3,871.34).

Each case runs on the server's store file, the compact in-memory store the serverless cycle builds and, where the build
can serialize one, that store's image saved and loaded between runs: the Vercel host keeps selfcheck_posted,
selfcheck_facts and the meta in it from one run to the next.
"""
from __future__ import annotations

import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch import codes  # noqa: E402
from remitwatch import ema_ledger as el  # noqa: E402
from remitwatch import ema_reports as er  # noqa: E402
from remitwatch import selfcheck as sc  # noqa: E402
from remitwatch.detect import ensure_schema  # noqa: E402
from remitwatch.store import Store  # noqa: E402
from tests import ema_fixtures as F  # noqa: E402
from tests.test_selfcheck import CAN_SERIALIZE, SALT_HEX, TODAY, Kind, add_item, check_of, remit, run  # noqa: E402

KINDS = ["file", "compact", "image"]
NO_IMAGE = "this SQLite build cannot serialize a store image"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(er, "CLAIM_HOST_SUFFIX", F.HOST_SUFFIX)
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    for k in ("VERCEL", "REMITWATCH_EMA_REPORTS", "REMITWATCH_EMA_PAYER_ALIASES", "REMITWATCH_EMA_LEDGER", "REMITWATCH_TODAY",
              "REMITWATCH_EMA_RETAIN_DAYS", "REMITWATCH_MEMORY_BUDGET_MB", "REMITWATCH_MAX_SECONDS",
              "REMITWATCH_CYCLE_BUDGET_SECONDS", "REMITWATCH_STALE_DAYS", "REMITWATCH_AUDIT_GAP_CENTS",
              "REMITWATCH_AUDIT_WASTED_ITEMS"):
        monkeypatch.delenv(k, raising=False)
    er._GROUPERS.clear()
    sc._GROUPS.clear()
    yield
    er._GROUPERS.clear()
    sc._GROUPS.clear()


# =========================================================== 1. a payer name that changes group moves no money out
P, D = F.PAYERS, F.D
MOVED = P["optum"]                  # "Optum Test Network": Optum (UHC) by the code's name rules
HOW = ["none", "learned_alias", "owner_alias", "release_rule", "dropped_alias"]
CHANGES = HOW[1:]
GROUP_BEFORE = {"dropped_alias": "VA Community Care"}
GROUP_AFTER = {"none": "Optum (UHC)", "learned_alias": "VA Community Care", "owner_alias": "VA Community Care",
               "release_rule": "Testnet Health", "dropped_alias": "Optum (UHC)"}


def _before(how: str) -> str:
    return GROUP_BEFORE.get(how, "Optum (UHC)")


def _rows() -> list[dict]:
    """March: two claims of the moved payer paid (with contractual write-downs) and one Blue Cross claim paid; one posting
    in April, so March is a closed posting month."""
    return [
        F.production_row(posted=D(2026, 3, 4), svc=D(2026, 3, 2), link=F.link_no(1), charges=150.0, payer=MOVED),
        F.production_row(posted=D(2026, 3, 20), svc=D(2026, 3, 2), link=F.link_no(1), ins_paid=90.0, contractual=40.0,
                         reason="CO-45", check=F.check_no(1), payer=MOVED),
        F.production_row(posted=D(2026, 3, 5), svc=D(2026, 3, 3), cpt="99214", link=F.link_no(2), charges=200.0, payer=MOVED,
                         patient=1),
        F.production_row(posted=D(2026, 3, 23), svc=D(2026, 3, 3), cpt="99214", link=F.link_no(2), ins_paid=120.0,
                         contractual=80.0, reason="CO-45", check=F.check_no(2), payer=MOVED, patient=1),
        F.production_row(posted=D(2026, 3, 6), svc=D(2026, 3, 4), cpt="99212", link=F.link_no(3), charges=80.0, payer=P["bcbs"],
                         patient=2),
        F.production_row(posted=D(2026, 3, 24), svc=D(2026, 3, 4), cpt="99212", link=F.link_no(3), ins_paid=50.0,
                         contractual=30.0, reason="CO-45", check=F.check_no(3), payer=P["bcbs"], patient=2),
        F.production_row(posted=D(2026, 4, 10), svc=D(2026, 4, 6), cpt="99212", link=F.link_no(4), charges=80.0, payer=P["bcbs"],
                         patient=3),
    ]


def _without(rows: list[dict], paid: float, posted) -> list[dict]:
    """A newer export of the same dates that lacks the posting of that insurance payment (and the contractual on its row),
    with a patient adjustment posted later, so it reaches past the one before and replaces it."""
    out = [r for r in rows if r.get("Insurance Payments") != paid]
    assert len(out) == len(rows) - 1
    return out + [F.production_row(posted=posted, svc=D(2026, 3, 9), cpt="99212", link=None, pat_adj=5.0, payer="Patient",
                                   patient=1)]


def _weekly(payer_id: str, tag: str, n: int = 25) -> list[dict]:
    """n rows of the weekly exports for the moved name, each with this payer id (learn_payer_aliases learns from them)."""
    return [{"row_fingerprint": f"fp{tag}{i:02d}", "family": "charge_lines", "source_sha256": f"invented-export-{tag}",
             "payer": MOVED, "payer_id": payer_id} for i in range(n)]


OTHER_A = "Optum Test Choice"       # Optum (UHC) by the code's name rules, whatever is learned for the moved name
VA = P["va"]                        # "VA CCN Test": VA Community Care
VA_TWO = "VA Test Second Network"   # VA Community Care too, with no money before the newer export
BCBS = P["bcbs"]


def _pay(n: int, payer: str, paid: float, contr: float, posted, *, svc=D(2026, 3, 2), cpt: str = "99213",
         patient: int = 0) -> list[dict]:
    """Invented claim n: its charge and its payment (with its contractual write-down)."""
    return [F.production_row(posted=D(2026, 3, 1) if posted.month == 3 else posted, svc=svc, cpt=cpt, link=F.link_no(n),
                             charges=paid + contr, payer=payer, patient=patient),
            F.production_row(posted=posted, svc=svc, cpt=cpt, link=F.link_no(n), ins_paid=paid, contractual=contr,
                             reason="CO-45", check=F.check_no(n), payer=payer, patient=patient)]


def _wide() -> list[dict]:
    """March service and March postings (a closed month once an April posting exists): two claims of the moved name, one
    of another name of its group, one VA claim, one Blue Cross claim, and a February service Blue Cross paid in March."""
    rows = (_pay(1, MOVED, 90.0, 40.0, D(2026, 3, 20)) + _pay(2, MOVED, 120.0, 80.0, D(2026, 3, 23), cpt="99214", patient=1)
            + _pay(5, OTHER_A, 70.0, 30.0, D(2026, 3, 21), cpt="99212", patient=2)
            + _pay(6, VA, 100.0, 50.0, D(2026, 3, 22), cpt="99212", patient=3)
            + _pay(3, BCBS, 50.0, 30.0, D(2026, 3, 24), cpt="99212")
            + _pay(8, BCBS, 60.0, 20.0, D(2026, 3, 18), svc=D(2026, 2, 10), patient=1))
    return rows + [F.production_row(posted=D(2026, 4, 10), svc=D(2026, 4, 6), cpt="99212", link=F.link_no(4), charges=80.0,
                                    payer=BCBS, patient=3)]


def _drop(rows: list[dict], payer: str, paid: float) -> list[dict]:
    """The rows without that payer's payment of that amount (and the contractual on its row)."""
    out = [r for r in rows if not (r.get("Payer") == payer and r.get("Insurance Payments") == paid)]
    assert len(out) == len(rows) - 1, (payer, paid)
    return out


def _after(rows: list[dict], day) -> list[dict]:
    """A newer export of the same dates: a patient adjustment posted later, so it reaches past the one before."""
    return rows + [F.production_row(posted=day, svc=D(2026, 3, 9), cpt="99212", link=None, pat_adj=5.0, payer="Patient",
                                    patient=1)]


def _offset(rows: list[dict], gainer: str) -> list[dict]:
    """The other Optum name's $70 payment and its $30 contractual are gone, and a new $70 payment and $30 contractual of
    `gainer` (a VA name) are there, in the same service and posting month: the month's total does not move."""
    return _drop(rows, OTHER_A, 70.0) + _pay(7, gainer, 70.0, 30.0, D(2026, 3, 26), cpt="99212", patient=2)


def _shave(rows: list[dict], payer: str, paid: float, by: float) -> list[dict]:
    return [dict(r, **{"Insurance Payments": round(r["Insurance Payments"] - by, 2), "Payments": round(r["Payments"] - by, 2)})
            if (r.get("Payer") == payer and r.get("Insurance Payments") == paid) else r for r in rows]


def _change(how: str, store: Store, monkeypatch) -> None:
    """The name rules change; the posting ledger does not."""
    if how == "learned_alias":
        # the weekly exports arrive: every row of that name carries the VA network's payer id (learned at the next refresh)
        store.ingest_ema_rows(_weekly("VACCN", "va"))
    elif how == "dropped_alias":
        # newer weekly exports carry the administrator's own payer id on as many rows: the learned alias goes
        store.ingest_ema_rows(_weekly("87726", "uhc"))
    elif how == "owner_alias":
        monkeypatch.setenv("REMITWATCH_EMA_PAYER_ALIASES", json.dumps({MOVED: "VA Community Care"}))
    elif how == "release_rule":
        # a release whose name rules read that administrator's network as a group of its own (as tests/test_fx_ema.py's
        # changed-name-rule test does for Medicaid of Kentucky)
        monkeypatch.setattr(codes, "PAYER_RULES", [(r"OPTUM TEST NETWORK", "Testnet Health", "commercial")] + list(codes.PAYER_RULES))
    er._GROUPERS.clear()


def _posted(out: dict) -> dict:
    return check_of(out["checks"], "posted_money")


def _groups(pm: dict) -> set[str]:
    return {k.split("|")[2] for k, _c in pm["keys"]}


def _show(pm: dict) -> dict:
    return {k: pm.get(k) for k in ("status", "n", "cents", "text", "keys", "info")}


class World:
    """A store of one kind and its posting ledger (a file, as both hosts keep it while they read), driven in the server
    cycle's order: a Production file read into the ledger, the refresh (it learns the aliases and re-groups the store's EMA
    rows), the money checks, a detection run. The 'image' store is saved and loaded again after every run."""

    def __init__(self, kind: str, tmp_path):
        if kind == "image" and not CAN_SERIALIZE:
            pytest.skip(NO_IMAGE)
        self.k = Kind(kind, tmp_path)
        self.tmp = tmp_path
        self.lg = el.Ledger.open(str(tmp_path / "ema_ledger.sqlite"))
        self.files = 0
        self.weeks = 0

    @property
    def store(self) -> Store:
        return self.k.store

    @property
    def conn(self):
        return self.k.conn

    def read(self, rows: list[dict]) -> dict:
        self.files += 1
        path = F.write(str(self.tmp / f"p{self.files}.csv"), F.production_csv(rows))
        r = er.ingest_file(self.store, path, via="test", ledger=self.lg, log=lambda *a: None)
        assert r["status"] == "current" and (self.files == 1 or r["replaced"]), r
        return r

    def refresh(self) -> None:
        el.refresh(self.store, self.lg, log=lambda *a: None)

    def check(self) -> dict:
        return sc.check_ledger(self.conn, self.lg, today=TODAY)

    def run(self, rid: str) -> dict:
        out = run(self.conn, None, rid)
        self.k.reopen()
        return out

    def money(self) -> tuple:
        return self.lg.conn.execute("""SELECT COUNT(*), SUM(COALESCE(ins_paid, 0)), SUM(COALESCE(contractual, 0)
                                       + COALESCE(noncontractual, 0)), SUM(COALESCE(pat_paid, 0)), SUM(COALESCE(pat_adj, 0))
                                       FROM postings""").fetchone()

    def marks(self, group: str) -> dict:
        return {(m, p): (h, last) for m, p, h, last in self.conn.execute(
            "SELECT measure, posted_month, high_cents, last_cents FROM selfcheck_posted WHERE payer_group = ? AND high_cents > 0",
            (group,))}

    def start(self, how: str) -> list[dict]:
        """The first Production file, its check (the marks are made) and a first run (the baseline)."""
        if how == "dropped_alias":
            self.store.ingest_ema_rows(_weekly("VACCN", "va"))     # learned before the marks were made
        rows = _rows()
        self.read(rows)
        self.refresh()
        assert er.payer_group_of(self.conn, MOVED)[0] == _before(how)
        add_item(self.conn, "F-1")
        assert _posted(self.check())["status"] == "ok"
        assert self.marks(_before(how)) == {("ins_paid", "2026-03"): (21000, 21000), ("adjustments", "2026-03"): (12000, 12000)}
        assert not self.run("r1")["held"]
        return rows

    def start_wide(self, rows: list[dict] | None = None) -> list[dict]:
        """As start, with more payer names (_wide): the first file, its check and a first run."""
        rows = rows or _wide()
        self.read(rows)
        self.refresh()
        add_item(self.conn, "F-1")
        assert _posted(self.check())["status"] == "ok"
        assert not self.run("r1")["held"]
        return rows

    def learn(self) -> None:
        """This week's exports (the earlier weeks pruned): every row of the moved name carries the VA network's payer id,
        so the next refresh learns the alias."""
        self.weeks += 1
        self.conn.execute("DELETE FROM ema_rows")
        self.store.ingest_ema_rows(_weekly("VACCN", f"va{self.weeks}"))
        er._GROUPERS.clear()

    def unlearn(self) -> None:
        """This week's exports: every row of the moved name carries the administrator's own payer id (the alias goes)."""
        self.weeks += 1
        self.conn.execute("DELETE FROM ema_rows")
        self.store.ingest_ema_rows(_weekly("87726", f"uhc{self.weeks}"))
        er._GROUPERS.clear()

    def forget_groups(self) -> None:
        """The store as a version before 2026-11-28 left it: marks with no record of each payer name's group."""
        self.conn.execute("DELETE FROM meta WHERE key = ?", (sc.K_POSTED_GROUPS,))
        self.conn.commit()

    def close(self) -> None:
        self.lg.close()
        self.k.store.close()


@pytest.mark.parametrize("how", HOW)
@pytest.mark.parametrize("kind", KINDS)
def test_a_payer_name_that_changes_group_is_not_money_leaving_the_ledger(tmp_path, kind, how, monkeypatch):
    """A name's group moves (an alias learned or dropped, the owner's alias, a release's rule) and no posting leaves the
    ledger: posted_money passes, counts no gap and holds no run, and says the marks moved with the name. A real loss after
    the change is caught under the group of now, with its own dollars. 'none' is the control (nothing changes)."""
    w = World(kind, tmp_path)
    try:
        rows = w.start(how)
        money = w.money()

        _change(how, w.store, monkeypatch)
        w.refresh()
        assert er.payer_group_of(w.conn, MOVED)[0] == GROUP_AFTER[how]      # the premise: the name's group moved
        assert w.money() == money                                            # and no posting left the ledger
        out = w.check()
        pm = _posted(out)
        assert pm["status"] == "ok", _show(pm)
        assert out["gaps"]["posted_drop_cents"] == 0, out["gaps"]
        if how == "none":
            assert "regrouped" not in pm["info"], _show(pm)
        else:
            assert pm["info"]["regrouped"] == {"names": 1, "cells": 4}, _show(pm)
            assert "changed group" in pm["text"], pm["text"]
            # the marks moved with the name's money: none left under its old group, all of it under its new one
            assert w.marks(_before(how)) == {}
            assert w.marks(GROUP_AFTER[how]) == {("ins_paid", "2026-03"): (21000, 21000),
                                                 ("adjustments", "2026-03"): (12000, 12000)}
        held = w.run("r2")
        assert not held["held"], {k: held.get(k) for k in ("held", "hold_checks", "failing")}

        # a real loss after the change: a newer export of the same dates lacks the moved name's $90 payment and its $40
        # contractual. Caught with exactly its dollars, under the group of now, and it holds the run
        w.read(_without(rows, 90.0, D(2026, 4, 12)))
        w.refresh()
        out = w.check()
        pm = _posted(out)
        assert pm["status"] == "fail" and pm["cents"] == 13000, _show(pm)
        assert _groups(pm) == {GROUP_AFTER[how]}, _show(pm)
        assert out["gaps"]["posted_drop_cents"] == 13000
        held = w.run("r3")
        assert held["held"] and held["hold_checks"] == ["posted_money"], held
    finally:
        w.close()


@pytest.mark.parametrize("how", CHANGES)
@pytest.mark.parametrize("kind", KINDS)
def test_a_real_loss_read_with_a_regroup_is_caught_with_its_own_dollars(tmp_path, kind, how, monkeypatch):
    """The name rules change and, before the same refresh and check, a newer export of the same dates lacks the moved
    name's $90 payment and its $40 contractual. The check compares each name under the group its marks were made with:
    exactly $130 is caught (not the moved name's whole $330), under that group, and the run is held. Once a person accepts
    it, the check passes and the marks move to the groups of now: a later loss of the name's other payment is caught under
    its new group, again with its own dollars."""
    w = World(kind, tmp_path)
    try:
        rows = w.start(how)
        _change(how, w.store, monkeypatch)
        rows2 = _without(rows, 90.0, D(2026, 4, 12))
        w.read(rows2)
        w.refresh()
        assert er.payer_group_of(w.conn, MOVED)[0] == GROUP_AFTER[how]
        out = w.check()
        pm = _posted(out)
        assert pm["status"] == "fail" and pm["cents"] == 13000, _show(pm)
        assert _groups(pm) == {_before(how)}, _show(pm)                     # the group its marks were made with
        assert out["gaps"]["posted_drop_cents"] == 13000, out["gaps"]
        assert "changed group" in pm["text"], pm["text"]
        held = w.run("r2")
        assert held["held"] and held["hold_checks"] == ["posted_money"], held

        sc.accept_findings(w.conn, "owner@example.test", ["posted_money"])  # EMA itself removed that payment
        pm = _posted(w.check())
        assert pm["status"] == "ok", _show(pm)
        assert pm["info"]["regrouped"]["names"] == 1, _show(pm)
        assert w.marks(_before(how)) == {}
        assert w.marks(GROUP_AFTER[how]) == {("ins_paid", "2026-03"): (12000, 12000), ("adjustments", "2026-03"): (8000, 8000)}
        assert not w.run("r3")["held"]

        w.read(_without(rows2, 120.0, D(2026, 4, 14)))                     # then its $120 payment and $80 contractual go
        w.refresh()
        pm = _posted(w.check())
        assert pm["status"] == "fail" and pm["cents"] == 20000, _show(pm)
        assert _groups(pm) == {GROUP_AFTER[how]}, _show(pm)
        assert w.run("r4")["held"]
    finally:
        w.close()


@pytest.mark.parametrize("kind", ["file", "compact"])
def test_marks_made_before_the_groups_were_recorded_follow_a_regroup_by_each_months_total(tmp_path, kind, monkeypatch):
    """A store whose marks an earlier version made has no record of the group each name had (meta selfcheck:posted_groups).
    If the name rules change before the first check of this version, the check sums each (service month, posted month,
    measure) across every payer group: one whose total did not fall, and whose fall a whole payer name's money explains,
    lost nothing; its marks move to the groups of now and the check's text says how much moved. A month whose total fell
    keeps its marks and is caught. The groups are recorded at that first check either way, so it happens once."""
    w = World(kind, tmp_path)
    try:
        rows = w.start("none")
        w.forget_groups()
        _change("learned_alias", w.store, monkeypatch)
        w.refresh()
        pm = _posted(w.check())
        assert pm["status"] == "ok", _show(pm)
        assert pm["info"]["regrouped"] == {"slices": 2, "cents": 33000}, _show(pm)
        assert "$330 moved with whole payer names" in pm["text"], pm["text"]
        assert w.marks("Optum (UHC)") == {}
        assert w.marks("VA Community Care") == {("ins_paid", "2026-03"): (21000, 21000), ("adjustments", "2026-03"): (12000, 12000)}
        assert sc.meta_get(w.conn, sc.K_POSTED_GROUPS)                     # recorded from now on
        assert not w.run("r2")["held"]

        w.forget_groups()
        w.read(_without(rows, 90.0, D(2026, 4, 12)))                       # a real loss, with no regroup: caught as before
        w.refresh()
        pm = _posted(w.check())
        assert pm["status"] == "fail" and pm["cents"] == 13000 and _groups(pm) == {"VA Community Care"}, _show(pm)
        assert "regrouped" not in pm["info"], _show(pm)
        # recorded at this check too, with the fall outstanding: the month totals stand in for one check only
        assert sc.meta_get(w.conn, sc.K_POSTED_GROUPS) == {MOVED: "VA Community Care", P["bcbs"]: "Anthem / BCBS"}
        assert w.run("r3")["held"]
    finally:
        w.close()


@pytest.mark.parametrize("kind", KINDS)
def test_the_first_check_after_the_release_catches_a_loss_another_groups_gain_offsets(tmp_path, kind):
    """Review rf-r1-1. The marks were made before the groups were recorded. A newer export of the same dates lacks the
    other Optum name's $70 payment and $30 contractual, and carries a new VA payment of $70 and $30 in the same month. The
    month's total did not fall, but no payer name changed group: the VA name's own money there ($170, $80) is more than
    its group rose. Caught with its $100 and the run held, as the check did before the release; the groups are recorded."""
    w = World(kind, tmp_path)
    try:
        rows = w.start_wide()
        w.forget_groups()
        w.read(_after(_offset(rows, VA), D(2026, 4, 12)))
        w.refresh()
        out = w.check()
        pm = _posted(out)
        assert pm["status"] == "fail" and pm["cents"] == 10000, _show(pm)
        assert _groups(pm) == {"Optum (UHC)"} and "regrouped" not in pm["info"], _show(pm)
        assert out["gaps"]["posted_drop_cents"] == 10000, out["gaps"]
        assert sc.meta_get(w.conn, sc.K_POSTED_GROUPS)
        held = w.run("r2")
        assert held["held"] and held["hold_checks"] == ["posted_money"], held
    finally:
        w.close()


@pytest.mark.parametrize("kind", KINDS)
def test_the_month_totals_stand_in_for_one_check_only(tmp_path, kind):
    """Review rf-r1-1. The first check after the release finds a real loss (a February service's $60 and $20 at Blue
    Cross) that nobody accepts. The groups are recorded at that check all the same, so every later check compares each
    name under its group. Two runs later a newer export loses the other Optum name's $70 and $30 and carries a new VA
    name's $70 and $30 in the same March month (a whole name's money, as a name that changed group would): caught, since
    no name changed group, $80 + $100."""
    w = World(kind, tmp_path)
    try:
        rows = w.start_wide()
        w.forget_groups()
        rows2 = _drop(rows, BCBS, 60.0)
        w.read(_after(rows2, D(2026, 4, 12)))
        w.refresh()
        pm = _posted(w.check())
        assert pm["status"] == "fail" and pm["cents"] == 8000, _show(pm)
        assert sc.meta_get(w.conn, sc.K_POSTED_GROUPS)
        assert w.run("r2")["held"]
        assert _posted(w.check())["cents"] == 8000
        assert w.run("r3")["held"]
        w.read(_after(_offset(rows2, VA_TWO), D(2026, 4, 14)))
        w.refresh()
        pm = _posted(w.check())
        assert pm["status"] == "fail" and pm["cents"] == 8000 + 10000, _show(pm)
        assert "regrouped" not in pm["info"], _show(pm)
    finally:
        w.close()


@pytest.mark.parametrize("kind", ["file", "compact"])
def test_at_the_first_check_after_the_release_a_whole_names_offset_shows_its_dollars(tmp_path, kind):
    """What the one check after the release cannot tell apart: a loss in one group that a payer name met for the first
    time offsets in another group, with all of its money in that month, looks like that name having changed group. That
    check lets the month start again, and says so with the dollars (in the check's text and info), and what to compare if
    no name changed group. The groups are recorded then, so from the next check on such a loss is caught (the test above)."""
    w = World(kind, tmp_path)
    try:
        rows = w.start_wide()
        w.forget_groups()
        w.read(_after(_offset(rows, VA_TWO), D(2026, 4, 12)))
        w.refresh()
        pm = _posted(w.check())
        assert pm["status"] == "ok" and pm["info"]["regrouped"] == {"slices": 2, "cents": 10000}, _show(pm)
        assert "$100 moved with whole payer names" in pm["text"] and "if none did, compare the newest Production export" in pm["text"], pm["text"]
        assert sc.meta_get(w.conn, sc.K_POSTED_GROUPS)
    finally:
        w.close()


@pytest.mark.parametrize("kind", ["file", "compact"])
def test_the_first_check_after_the_release_follows_a_name_that_moved_with_a_takeback(tmp_path, kind, monkeypatch):
    """The moved name's money in one month is negative (a $20 takeback posted in April for a March visit) and the VA group
    already had money there ($50). When the name's group changes before the first check of this version, the VA cell of
    that month falls by the takeback and the name's old group's cell rises by it. The month's total did not move and the
    name's own (negative) money covers the fall: it moved with the name and nothing left."""
    rows = _rows() + _pay(6, VA, 50.0, 0.0, D(2026, 4, 9), svc=D(2026, 3, 3), cpt="99212", patient=3) + [
        F.production_row(posted=D(2026, 4, 8), svc=D(2026, 3, 2), link=F.link_no(1), ins_paid=-20.0, reason="WO",
                         check=F.check_no(9), payer=MOVED),
        F.production_row(posted=D(2026, 5, 11), svc=D(2026, 5, 4), cpt="99212", link=F.link_no(10), charges=80.0,
                         payer=BCBS, patient=2)]
    w = World(kind, tmp_path)
    try:
        w.start_wide(rows)
        assert {(g, m, p): h for g, m, p, h in w.conn.execute(
            "SELECT payer_group, measure, posted_month, high_cents FROM selfcheck_posted WHERE posted_month = '2026-04'")} == {
            ("Optum (UHC)", "ins_paid", "2026-04"): -2000, ("VA Community Care", "ins_paid", "2026-04"): 5000}
        w.forget_groups()
        _change("learned_alias", w.store, monkeypatch)
        w.refresh()
        assert er.payer_group_of(w.conn, MOVED)[0] == "VA Community Care"
        out = w.check()
        pm = _posted(out)
        assert pm["status"] == "ok", _show(pm)
        assert pm["info"]["regrouped"] == {"slices": 3, "cents": 21000 + 12000 + 2000}, _show(pm)
        assert out["gaps"]["posted_drop_cents"] == 0
        assert not w.run("r2")["held"]
    finally:
        w.close()


@pytest.mark.parametrize("flap", [False, True])
@pytest.mark.parametrize("kind", KINDS)
def test_a_slow_leak_is_caught_while_an_alias_comes_and_goes(tmp_path, kind, flap):
    """Review rf-r1-2. 60 cents of the moved name's $120 payment go at each newer export, under the $1 a cell may fall
    each time. Without a change of group, the mark catches the leak once it passes $1 (the second export). With the alias
    learned and dropped at each export, the marks move with the name each time; a cell they move keeps the fall it already
    had, so the leak is caught by the third export, with its $1.20."""
    w = World(kind, tmp_path)
    try:
        cur = w.start_wide()
        caught = None
        for i in range(1, 5):
            cur = _shave(cur, MOVED, round(120.0 - 0.6 * (i - 1), 2), 0.6)
            if flap:
                (w.learn if i % 2 == 1 else w.unlearn)()
            w.read(_after(cur, D(2026, 4, 10 + i)))
            w.refresh()
            pm = _posted(w.check())
            if pm["status"] == "fail":
                caught = (i, pm["cents"])
                break
            assert not w.run(f"rl{i}")["held"]
        assert caught == ((3, 120) if flap else (2, 120)), (flap, caught)
    finally:
        w.close()


@pytest.mark.parametrize("kind", KINDS)
def test_an_alias_that_comes_and_goes_wipes_no_marks(tmp_path, kind):
    """The alias learned and dropped four times with nothing missing: each check passes, no run is held, the marks keep
    the full money after every move, and a real loss after the loop is caught with exactly its dollars."""
    w = World(kind, tmp_path)
    try:
        rows = w.start_wide()
        full = sum(h for (h,) in w.conn.execute("SELECT high_cents FROM selfcheck_posted WHERE measure = 'ins_paid'"))
        for i in range(4):
            (w.learn if i % 2 == 0 else w.unlearn)()
            w.refresh()
            assert er.payer_group_of(w.conn, MOVED)[0] == ("VA Community Care" if i % 2 == 0 else "Optum (UHC)")
            pm = _posted(w.check())
            assert pm["status"] == "ok", _show(pm)
            assert not w.run(f"rf{i}")["held"]
            assert sum(h for (h,) in w.conn.execute(
                "SELECT high_cents FROM selfcheck_posted WHERE measure = 'ins_paid'")) == full
        w.read(_after(_drop(rows, MOVED, 120.0), D(2026, 4, 12)))
        w.refresh()
        pm = _posted(w.check())
        assert pm["status"] == "fail" and pm["cents"] == 20000, _show(pm)
    finally:
        w.close()


@pytest.mark.parametrize("kind", KINDS)
def test_a_new_loss_in_a_cell_a_person_accepted_holds_the_run(tmp_path, kind):
    """Review rf-r1-3. A person accepts a fall at Blue Cross ($50 and $30): accepting moves the cell's mark to what the
    ledger holds, so its gap starts again from 0. Later another payment of the same cell goes ($40 and $10): that is new
    money gone, and it holds the run like any new gap, whatever was accepted in that cell before."""
    w = World(kind, tmp_path)
    try:
        rows = w.start_wide() + _pay(9, BCBS, 40.0, 10.0, D(2026, 3, 25), cpt="99213", patient=2)
        w.read(_after(rows, D(2026, 4, 11)))
        w.refresh()
        assert _posted(w.check())["status"] == "ok"
        assert not w.run("r1b")["held"]
        rows2 = _drop(rows, BCBS, 50.0)
        w.read(_after(rows2, D(2026, 4, 12)))
        w.refresh()
        pm = _posted(w.check())
        assert pm["status"] == "fail" and pm["cents"] == 8000, _show(pm)
        assert w.run("r2")["held"]
        sc.accept_findings(w.conn, "owner@example.test", ["posted_money"])
        assert _posted(w.check())["status"] == "ok"
        assert not w.run("r3")["held"]
        # the accepted dollars stay on record with who accepted them
        assert sorted(tuple(r) for r in w.conn.execute(
            "SELECT cents, accepted_by FROM selfcheck_baseline WHERE check_id = 'posted_money'")) == [
            (3000, "owner@example.test"), (5000, "owner@example.test")]
        w.read(_after(_drop(rows2, BCBS, 40.0), D(2026, 4, 13)))          # a different payment of the same cell goes
        w.refresh()
        pm = _posted(w.check())
        assert pm["status"] == "fail" and pm["cents"] == 5000, _show(pm)
        out = w.run("r4")
        assert out["held"] and out["hold_checks"] == ["posted_money"] and out["new"]["posted_money"] == 2, out
    finally:
        w.close()


@pytest.mark.skipif(not CAN_SERIALIZE, reason=NO_IMAGE)
def test_on_the_vercel_host_an_alias_learned_after_a_production_run_is_no_money_leaving(tmp_path, monkeypatch):
    """The serverless host reads a Production file in a run of its own (cloud.ProductionSlot: the money checks before the
    file, the posted check after it, then the refresh, which learns the aliases), and keeps selfcheck_posted and the meta
    in the store image it saves to the state folder and loads at the next run. The marks of the first file are made before
    its refresh learns that the moved name is the VA's; the next file's checks must not read that as money leaving, and a
    real loss in a third file is caught with its dollars."""
    from remitwatch import cloud
    from remitwatch.state import LocalState
    monkeypatch.setenv("REMITWATCH_MODE", "serverless")
    monkeypatch.setenv("REMITWATCH_EMA_REPORTS", "all")
    state = LocalState(str(tmp_path / "state"))
    image: list = [None]

    def invocation(*, rows=None, name="", detect=None, before=None):
        """One run: load the store image, read one Production file or run the detection hooks, save the image."""
        if image[0] is None:
            s = Store(":memory:", compact=True)
            ensure_schema(s.conn)
            s.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('ema:key_salt', ?)", (SALT_HEX,))
            s.conn.commit()
        else:
            s = Store.from_bytes(image[0])
            ensure_schema(s.conn)
        try:
            if before:
                before(s)
            out = None
            if rows is not None:
                slot = cloud.ProductionSlot(s, state, deadline=time.time() + 120, log=lambda *a: None)
                res = slot.apply(F.write(str(tmp_path / name), F.production_csv(rows)), origin={}, name=name)
                assert res["status"] == "current", res
                assert slot.finish({})
                slot.clear_attempts()
                out = sc.meta_get(s.conn, sc.K_POSTED)["check"]
            if detect:
                out = run(s.conn, None, detect)
            image[0] = s.to_bytes()
            return out, er.payer_group_of(s.conn, MOVED)[0]
        finally:
            s.close()

    def weekly_and_item(s):
        s.ingest_ema_rows(_weekly("VACCN", "va"))         # the weekly exports an earlier run read
        add_item(s.conn, "F-1")
    rows = _rows()
    pm, group = invocation(rows=rows, name="2026 Production a.csv", before=weekly_and_item)
    assert pm["status"] == "ok" and group == "VA Community Care", _show(pm)    # learned by this run's refresh
    assert not invocation(detect="r1")[0]["held"]

    pm, _g = invocation(rows=rows + [F.production_row(posted=D(2026, 4, 12), svc=D(2026, 3, 9), cpt="99212", link=None,
                                                      pat_adj=5.0, payer="Patient", patient=1)], name="2026 Production b.csv")
    assert pm["status"] == "ok", _show(pm)
    out, _g = invocation(detect="r2")
    assert not out["held"], {k: out.get(k) for k in ("held", "hold_checks", "failing")}
    assert check_of(out["checks"], "posted_money")["status"] == "ok"

    pm, _g = invocation(rows=_without(rows, 90.0, D(2026, 4, 14)), name="2026 Production c.csv")
    assert pm["status"] == "fail" and pm["cents"] == 13000 and _groups(pm) == {"VA Community Care"}, _show(pm)
    out, _g = invocation(detect="r3")
    assert out["held"] and out["hold_checks"] == ["posted_money"], out


def test_the_posted_check_says_a_payer_name_that_changed_group_moves_no_money():
    """What to check, on the Data tab and in the alert, names the other cause of a closed month's money moving: a payer
    name that changed group (and that the check follows it by itself)."""
    what = sc.CHECKS["posted_money"][5]
    assert "changed group" in what and "REMITWATCH_EMA_PAYER_ALIASES" in what, what


# =========================================================== 2. a held run keeps the facts of the last good run
def _close_without_proof(conn, rid: str) -> None:
    """The held run's bug: an automatic closure with no payment named (closure_proof, a critical check that holds)."""
    conn.execute("""UPDATE findings SET status='resolved', work_state='done', auto_note='Paid on a later remittance',
                    prev_status='open', evidence='{}' WHERE finding_id='F-bad-close'""")
    conn.execute("""INSERT INTO finding_events (finding_id, at, kind, from_status, to_status, actor, note, run_id)
                    VALUES ('F-bad-close', 'now', 'paid_on_later_remit', 'open', 'resolved', 'system', 'Paid', ?)""", (rid,))


@pytest.fixture(params=KINDS)
def kind(request, tmp_path):
    if request.param == "image" and not CAN_SERIALIZE:
        pytest.skip(NO_IMAGE)
    k = Kind(request.param, tmp_path)
    yield k
    k.store.close()


def _listed(c) -> set[str]:
    return {f for (f,) in c.execute("SELECT finding_id FROM findings WHERE COALESCE(stale, 0) = 0 AND status = 'open'")}


@pytest.mark.parametrize("holds", [1, 2])
@pytest.mark.parametrize("between", ["file_read", "nothing_read"])
def test_an_item_settled_before_a_held_run_leaves_by_its_new_facts_on_the_next_good_run(kind, between, holds):
    """A clean run (r1) lists three items; the payer then pays the claim of one of them (F-settled); the next run drops
    that item rightly but closes another with no proof, and is held (twice in a row with holds=2); the run after is clean
    and again does not list the settled item. Its claim shows new facts since the last good run (r1), so it leaves; the
    control, an item whose claim shows nothing new since r1 that the clean run also drops, is kept. With an unrelated 835
    read between the held run and the clean one, and with nothing read (the run after the release that fixed what held
    the run: detection runs because the rules changed)."""
    c = kind.conn
    remit(c, "2026-03-20", "ZZ00T0001C001", [("99213", 150, 0, [("CO", "197", 150)])])     # both claims denied
    remit(c, "2026-03-20", "ZZ00T0002C001", [("99213", 150, 0, [("CO", "197", 150)])])
    add_item(c, "F-settled", line_key="La", patient_control_number="ZZ00T0001C001")
    add_item(c, "F-same-facts", line_key="Lb", patient_control_number="ZZ00T0002C001")
    add_item(c, "F-bad-close", line_key="Lc", patient_control_number="ZZ00T0003C001")
    assert not run(c, None, "r1")["held"]                       # the last good run: it stores each item's facts
    facts_r1 = dict(c.execute("SELECT finding_id, fp FROM selfcheck_facts"))
    input_r1 = sc.meta_get(c, sc.K_FACTS_INPUT)

    kind.reopen()
    c = kind.conn
    # the payer pays F-settled's claim (sent again as C002): new facts on its own claim, read before the next run
    remit(c, "2026-08-20", "ZZ00T0001C002", [("99213", 150, 90, [("CO", "45", 60)])])

    for i in range(holds):
        rid = f"r2{'abc'[i]}"

        def held_save(cn, rid=rid):
            cn.execute("DELETE FROM findings WHERE finding_id = 'F-settled'")     # rightly no longer raised: it was paid
            _close_without_proof(cn, rid)
        out = run(c, held_save, rid)
        assert out["held"] and out["hold_checks"] == ["closure_proof"], out
        van = check_of(sc.meta_get(c, sc.K_LAST)["checks"], "vanished")
        assert van["info"]["routes"].get("facts_changed") == 1, van           # the held run saw the new facts too
        assert _listed(c) == {"F-settled", "F-same-facts", "F-bad-close"}     # safe mode: the last good run's worklist
        # the facts stay the last good run's, with the input position they were taken at
        assert dict(c.execute("SELECT finding_id, fp FROM selfcheck_facts")) == facts_r1
        assert sc.meta_get(c, sc.K_FACTS_INPUT) == input_r1
        kind.reopen()
        c = kind.conn

    if between == "file_read":
        remit(c, "2026-09-15", "ZZ00T0009C001", [("99214", 200, 120, [("CO", "45", 80)])])   # another claim's 835

    def good_save(cn):
        # the clean run: F-settled's claim is still paid, so it is again not raised; F-same-facts goes too, with nothing
        # new on its claim since r1 (a rule that stopped): the control, which must be kept
        cn.execute("DELETE FROM findings WHERE finding_id IN ('F-settled', 'F-same-facts')")
    out = run(c, good_save, "r3")
    assert not out["held"], out
    van = check_of(sc.meta_get(c, sc.K_LAST)["checks"], "vanished")
    back = c.execute("SELECT COUNT(*) FROM findings WHERE finding_id = 'F-settled' AND COALESCE(stale, 0) = 0").fetchone()[0]
    assert van["examples"] == ["F-same-facts"] and out["kept"] == 1 and not back, (
        "F-settled's claim was paid after the last good run (r1) and before the held run: new facts on its own claim, so "
        "it may leave. A held run that saves fingerprints of its own data for the worklist it put back makes the next "
        f"run compare against them and keep it. kept={out['kept']}, examples={van['examples']}, "
        f"routes={van['info'].get('routes')}, back on the worklist={bool(back)}")
    assert van["info"]["routes"].get("facts_changed") == 1, van
    # the clean run saves its own facts and input position, for the run after it
    assert sc.meta_get(c, sc.K_FACTS_INPUT) == sc._input_position(c)


def test_a_new_item_a_held_run_lists_keeps_the_facts_it_was_found_on(kind):
    """A held run still lists the new items to work no failing check names (safe mode never hides new work), and they
    were found on the held run's data: their fingerprints are kept beside the last good run's. On the next good run, the
    new item whose own claim was paid since then leaves by its new facts; the one whose claim shows nothing new is kept."""
    c = kind.conn
    add_item(c, "F-old", line_key="La", patient_control_number="ZZ00T0001C001")
    add_item(c, "F-bad-close", line_key="Lc", patient_control_number="ZZ00T0003C001")
    assert not run(c, None, "r1")["held"]
    facts_r1 = dict(c.execute("SELECT finding_id, fp FROM selfcheck_facts"))

    kind.reopen()
    c = kind.conn
    remit(c, "2026-08-20", "ZZ00T0004C001", [("99213", 150, 0, [("CO", "197", 150)])])     # the new items' denials
    remit(c, "2026-08-20", "ZZ00T0005C001", [("99213", 150, 0, [("CO", "197", 150)])])

    def held_save(cn):
        add_item(cn, "F-new-paid", line_key="Ld", patient_control_number="ZZ00T0004C001", run_id="r2")
        add_item(cn, "F-new-same", line_key="Le", patient_control_number="ZZ00T0005C001", run_id="r2")
        _close_without_proof(cn, "r2")
    out = run(c, held_save, "r2")
    assert out["held"] and out["hold_checks"] == ["closure_proof"], out
    assert sc.meta_get(c, sc.K_SAFE)["new_items"] == 2
    assert _listed(c) == {"F-old", "F-bad-close", "F-new-paid", "F-new-same"}     # listed by the held run itself
    facts = dict(c.execute("SELECT finding_id, fp FROM selfcheck_facts"))
    assert {f: facts[f] for f in facts_r1} == facts_r1 and {"F-new-paid", "F-new-same"} <= set(facts), facts

    kind.reopen()
    c = kind.conn
    remit(c, "2026-09-15", "ZZ00T0004C002", [("99213", 150, 90, [("CO", "45", 60)])])      # F-new-paid's claim is paid

    def good_save(cn):
        cn.execute("DELETE FROM findings WHERE finding_id IN ('F-new-paid', 'F-new-same')")
    out = run(c, good_save, "r3")
    assert not out["held"], out
    van = check_of(sc.meta_get(c, sc.K_LAST)["checks"], "vanished")
    assert van["examples"] == ["F-new-same"] and out["kept"] == 1, van
    assert van["info"]["routes"].get("facts_changed") == 1, van
