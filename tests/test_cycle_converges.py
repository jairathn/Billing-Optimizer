"""
A cycle converges in one run, on both hosts (release gate 4: a second cycle with no new data changes nothing).

The store's summary of EMA's Production lines (remitwatch.ema_ledger.refresh) used to keep a bill's lines only while the
bill had an item. Detection reads those lines, so the items of one run decided the lines the next run read:
- a crossover whose carrier answered only in EMA was listed from the remittance alone (its line was not kept), the item
  made the next summary keep the line, the next run saw the carrier's answer and dropped the item, the lines went again,
  and the item came back a run later, every other cycle;
- a new item's first state was decided before its lines were kept: a plan's exclusion (PR-204) the patient paid was work
  on the first cycle and done on the second.
The lines kept now come from the data alone, so each run reads the same lines whatever the last run listed.

Every row is invented (tests/ema_fixtures.py, and the 835 builders of tests/test_features.py and tests/test_remitwatch.py).
The server runs its real cycle (pipeline.run_cycle) on the file store; the serverless host runs its real cycle
(cloud.run_cloud_cycle) on the compact in-memory image, with a fake Dropbox.
"""
from __future__ import annotations

import datetime as dt
import os

import pytest

from remitwatch import cloud, ema_ledger as el, ema_reports as er, pipeline, runtime
from remitwatch.state import DropboxState
from remitwatch.store import Store
from tests import ema_fixtures as F
from tests.fakes import FakeDropbox
from tests.test_features import NPI, svc
from tests.test_remitwatch import remit

D = dt.date
P = F.PAYERS
DATA = "Trizetto Data"
DOS = "20260302"
SVC = D(2026, 3, 2)
TODAY = dt.datetime(2026, 5, 20, 16, 0, tzinfo=dt.timezone.utc)       # the practice's day: 2026-05-20
MEDICARE_TRACE, BCBS_TRACE = "TRACE000000101", "TRACE000000102"      # TRN02 of the two remittances (remit() names them)
CARRIER = "TEST MEDIGAP LIFE"
FIELDS = ("finding_id", "signal", "patient_control_number", "status", "work_state", "state_basis", "appeal_block",
          "expected_cents", "shortfall_cents", "stale", "follow_up_on", "auto_note", "ema_outcome")


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    for k in ("VERCEL", "VERCEL_ENV", "REMITWATCH_EMA_REPORTS", "REMITWATCH_EMA_CLAIM_URL", "REMITWATCH_EMA_PAYER_ALIASES",
              "REMITWATCH_EMA_LEDGER", "REMITWATCH_EMA_REPORT_KEEP", "REMITWATCH_EMA_RETAIN_DAYS", "REMITWATCH_STATE_DIR",
              "DROPBOX_APP_KEY", "DROPBOX_APP_SECRET", "DROPBOX_REFRESH_TOKEN", "SMTP_HOST", "REPORT_TO", "ALERT_WEBHOOK_URL",
              "TRIZETTO_SFTP_USER", "SRFAX_ACCESS_ID", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    monkeypatch.setenv("REMITWATCH_TMP", str(tmp_path / "scratch"))
    monkeypatch.setenv("DROPBOX_ROOT_PATH", f"/{DATA}")
    monkeypatch.setattr(er, "CLAIM_HOST_SUFFIX", F.HOST_SUFFIX)
    monkeypatch.setattr(runtime, "_utc_now", lambda: TODAY)


# ------------------------------------------------------------------------------------------------------------- data
def _clp(n: int, status: str, charge: str, paid: str, owed: str, name: tuple[str, str], lines: str, crossover: str = "") -> str:
    """One claim of EMA claim id F.claim_id(n), answered with claim status `status`; `crossover` names the carrier the
    payer forwarded it to (NM1*TT)."""
    last, first = name
    return (f"CLP*{F.claim_id(n)}*{status}*{charge}*{paid}*{owed}*12*PCN{n:05d}*11~NM1*QC*1*{last}*{first}****MI*M{n:08d}~"
            + (f"NM1*TT*2*{crossover}*****PI*99999~" if crossover else "")
            + f"NM1*82*2*TEST PROVIDER*****XX*{NPI}~DTM*232*{DOS}~" + lines)


def world(folder) -> None:
    """The remittances and EMA's Production report of three visits on March 2, 2026, as a practice's folder holds them.

    1. Medicare paid $90 of a $150 visit (CO-45 $30) and left the $30 coinsurance (PR-2), forwarding the claim to a
       Medigap carrier (claim status 19). EMA shows the second payer on file answered on April 20: a $0 row on its own
       check and its OA-23 for the $30, which closed the line. No keep rule names this settled line: the old summary
       kept it only while its bill had an item.
    2. Blue Cross denied a $150 visit as a plan exclusion (PR-204, paid $0). EMA shows the $0 answer, the PR-204 and the
       patient's $150 payment, which closed the line: a settled line too.
    3. Blue Cross denied a $150 visit for its authorization (CO-197) and EMA wrote the denial off: an item to work.
    A charge on another visit, posted May 15, carries the report's date close to the day the cycles run."""
    os.makedirs(folder, exist_ok=True)
    medicare = _clp(1, "19", "150.00", "90.00", "30.00", ("EXAMPLE", "CHARLIE"),
                    svc("99213", "150.00", "90.00", "120.00", "CAS*CO*45*30.00~CAS*PR*2*30.00~", dos=DOS), crossover=CARRIER)
    bcbs = (_clp(2, "1", "150.00", "0.00", "150.00", ("SAMPLE", "BRAVO"), svc("99213", "150.00", "0.00", None, "CAS*PR*204*150.00~", dos=DOS))
            + _clp(3, "1", "150.00", "0.00", "0.00", ("FIXTURE", "DELTA"), svc("99213", "150.00", "0.00", None, "CAS*CO*197*150.00~", dos=DOS)))
    with open(os.path.join(folder, "r101.RMT"), "wb") as fh:
        fh.write(remit(medicare, "90.00", payer="MEDICARE OF KENTUCKY TEST", payer_id="MCRTEST", seg_count=99, isa_ctrl="000000101",
                       pay_date="20260328"))
    with open(os.path.join(folder, "r102.RMT"), "wb") as fh:
        fh.write(remit(bcbs, "0.00", payer="BLUE CROSS TEST", payer_id="BCBSTEST", seg_count=99, isa_ctrl="000000102",
                       pay_date="20260328"))
    l1, l2, l3 = F.link_no(1), F.link_no(2), F.link_no(3)
    mcr = dict(patient=2, svc=SVC, primary=P["medicare"], secondary=P["commercial"])
    bc = dict(svc=SVC, primary=P["bcbs"], payer=P["bcbs"])
    rows = [F.production_row(link=l1, charges=150, posted=D(2026, 3, 4), payer=P["medicare"], status="Payment Pending", **mcr),
            F.production_row(link=l1, ins_paid=90, check=MEDICARE_TRACE, posted=D(2026, 4, 3), payer=P["medicare"], **mcr),
            F.production_row(link=l1, contractual=30, reason="CO-45", posted=D(2026, 4, 3), payer=P["medicare"], **mcr),
            F.production_row(link=l1, ins_paid=0, check="TC0000001", posted=D(2026, 4, 20), payer=P["commercial"], **mcr),
            F.production_row(link=l1, noncontractual=30, reason="OA-23", posted=D(2026, 4, 20), payer=P["commercial"], **mcr),
            F.production_row(link=l2, charges=150, posted=D(2026, 3, 4), status="Payment Pending", patient=1, **bc),
            F.production_row(link=l2, ins_paid=0, check=BCBS_TRACE, posted=D(2026, 4, 2), patient=1, **bc),
            F.production_row(link=l2, reason="PR-204", pat_adj=0, posted=D(2026, 4, 2), patient=1, **bc),
            F.production_row(link=None, payer="Patient", pat_paid=150, posted=D(2026, 4, 25), patient=1, svc=SVC,
                             primary=P["bcbs"]),
            F.production_row(link=l3, charges=150, posted=D(2026, 3, 4), status="Payment Pending", patient=3, **bc),
            F.production_row(link=l3, ins_paid=0, check=BCBS_TRACE, posted=D(2026, 4, 2), patient=3, **bc),
            F.production_row(link=l3, noncontractual=150, reason="CO-197", posted=D(2026, 4, 2), patient=3, **bc),
            F.production_row(link=F.link_no(99), cpt="99212", charges=90, patient=0, svc=D(2026, 5, 12), posted=D(2026, 5, 15),
                             status="Payment Pending")]
    with open(os.path.join(folder, "2026 Production.csv"), "wb") as fh:
        fh.write(F.production_csv(rows))


def snapshot(conn) -> list[tuple]:
    return [tuple(r) for r in conn.execute(f"SELECT {', '.join(FIELDS)} FROM findings ORDER BY finding_id")]


def decided_on_the_first_cycle(snap: list[tuple]) -> None:
    """What the first cycle must already say: the carrier answered (nothing to bill), the exclusion the patient paid is
    done, and the authorization denial is work."""
    by_claim: dict = {}
    for r in snap:
        by_claim.setdefault(r[2], []).append(dict(zip(FIELDS, r)))
    assert not [f for f in by_claim.get(F.claim_id(1), []) if f["signal"] == "SECONDARY_NOT_BILLED"], by_claim.get(F.claim_id(1))
    excl = [f for f in by_claim.get(F.claim_id(2), []) if not f["stale"]]
    assert len(excl) == 1, excl
    assert excl[0]["work_state"] == "done" and excl[0]["state_basis"].startswith("Settled by the patient"), excl[0]
    assert [f["signal"] for f in by_claim.get(F.claim_id(3), [])] == ["DENIED_AUTH"]


def summary_lines(conn) -> set[int]:
    return {r[0] for r in conn.execute("SELECT DISTINCT link FROM ema_lines")}


# ------------------------------------------------------------------------------------------------------- the server
def test_three_server_cycles_with_no_new_data_list_the_same_items_decided_on_the_first(tmp_path):
    db = str(tmp_path / "data" / "remitwatch.sqlite")
    local = tmp_path / "drop"
    world(local)
    snaps, lines = [], []
    for _ in range(3):
        out = pipeline.run_cycle(db, sync=False, local=str(local), force_detect=True, email=False, report=False,
                                 log=lambda *a: None)
        assert "detect" in out and "error" not in out, out.get("error")
        # detection never asks the ledger for a second summary: the summary does not depend on what it listed
        assert "ema_refresh_after" not in out
        s = Store(db)
        try:
            snaps.append(snapshot(s.conn))
            lines.append(summary_lines(s.conn))
            assert s.conn.execute("SELECT 1 FROM meta WHERE key='ema:keep_dirty'").fetchone() is None
        finally:
            s.close()
    decided_on_the_first_cycle(snaps[0])
    assert snaps[1] == snaps[0] and snaps[2] == snaps[0]
    # the lines detection reads are the same every run: the two settled lines are there from the first
    assert {F.link_no(1), F.link_no(2)} <= lines[0] and lines[1] == lines[0] and lines[2] == lines[0]


# -------------------------------------------------------------------------------------------- the serverless host
def _serverless(tmp_path, monkeypatch):
    monkeypatch.setenv("REMITWATCH_MODE", "serverless")
    monkeypatch.setenv("REMITWATCH_EMA_REPORTS", "all")
    root = tmp_path / "dbx"
    client = FakeDropbox(str(root))
    state = DropboxState(client, "/RemitWatch-state")
    world(root / DATA)
    return client, state


def _state_snapshot(state) -> tuple[list[tuple], set[int]]:
    store, existed = cloud.load_raw(state)
    assert existed
    try:
        assert store.compact                 # the compact in-memory image cloud.py builds
        # no summary waits on what detection listed (the next Production file would otherwise read the ledger again)
        assert store.conn.execute("SELECT 1 FROM meta WHERE key='ema:keep_dirty'").fetchone() is None
        return snapshot(store.conn), summary_lines(store.conn)
    finally:
        store.close()


def test_three_serverless_cycles_with_no_new_data_list_the_same_items_decided_on_the_first(tmp_path, monkeypatch):
    client, state = _serverless(tmp_path, monkeypatch)
    outs = []
    for _ in range(6):                      # the Production file in one run, detection in the next
        out = cloud.run_cloud_cycle(state, client, budget_s=600, email=False, log=lambda *a: None)
        outs.append(out)
        if not out.get("more"):
            break
    assert not outs[-1].get("more") and any("detect" in o for o in outs) and any("ema_apply" in o for o in outs), outs
    first, lines = _state_snapshot(state)
    decided_on_the_first_cycle(first)
    assert {F.link_no(1), F.link_no(2)} <= lines
    for _ in range(2):
        out = cloud.run_cloud_cycle(state, client, budget_s=600, force_detect=True, email=False, log=lambda *a: None)
        assert "detect" in out and not out.get("more"), out
        again, lines_again = _state_snapshot(state)
        assert again == first and lines_again == lines


def test_both_hosts_list_the_same_items(tmp_path, monkeypatch):
    db = str(tmp_path / "server" / "remitwatch.sqlite")
    world(tmp_path / "drop")
    pipeline.run_cycle(db, sync=False, local=str(tmp_path / "drop"), force_detect=True, email=False, report=False,
                       log=lambda *a: None)
    s = Store(db)
    try:
        server = snapshot(s.conn)
    finally:
        s.close()
    client, state = _serverless(tmp_path, monkeypatch)
    for _ in range(6):
        if not cloud.run_cloud_cycle(state, client, budget_s=600, email=False, log=lambda *a: None).get("more"):
            break
    serverless, _lines = _state_snapshot(state)
    assert serverless == server


# --------------------------------------------------------------------------------- a store an older build summarized
def _summarized(tmp_path):
    """A store and ledger with the world's Production file read and summarized."""
    store = Store(str(tmp_path / "s.sqlite"))
    er.ensure_schema(store.conn)
    lg = el.Ledger.open(str(tmp_path / "ema_ledger.sqlite"))
    p = str(tmp_path / "drop" / "2026 Production.csv")
    world(tmp_path / "drop")
    r = er.ingest_file(store, p, via="test", ledger=lg, log=lambda *a: None)
    assert r["status"] == "current", r
    assert el.refresh(store, lg, log=lambda *a: None)["part_a"] is not None
    return store, lg


def test_a_summary_an_older_rule_kept_is_built_again_once(tmp_path):
    """A store whose EMA summary an older build pruned by its items (no record of the keep rule it was built by) gets
    the whole summary at its next refresh, once, without a new Production file; the serverless host's ledger handle
    calls the store behind its ledger until then, so its next run opens the ledger and refreshes."""
    store, lg = _summarized(tmp_path)
    try:
        everything = store.conn.execute("SELECT COUNT(*) FROM ema_lines").fetchone()[0]
        assert el.refresh(store, lg, log=lambda *a: None)["part_a"] is None         # nothing changed: nothing to do
        # what an older build left: lines pruned and no record of the rule
        store.conn.execute("DELETE FROM ema_lines WHERE link IN (?, ?)", (F.link_no(1), F.link_no(2)))
        store.conn.execute("DELETE FROM meta WHERE key LIKE 'ema:keep%'")
        store.conn.commit()

        class State:
            def meta(self, name):
                return {"rev": "rev-1"} if name == el.STATE_NAME else None
        store.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, 'rev-1')", (el.META_REV,))
        store.conn.commit()
        assert el.LedgerHandle(State(), tmp_dir=str(tmp_path / "h")).behind(store.conn) is True
        out = el.refresh(store, lg, log=lambda *a: None)
        assert out["part_a"] is not None and out["part_b"] is not None
        assert store.conn.execute("SELECT COUNT(*) FROM ema_lines").fetchone()[0] == everything
        assert el.LedgerHandle(State(), tmp_dir=str(tmp_path / "h")).behind(store.conn) is False
        assert el.refresh(store, lg, log=lambda *a: None)["part_a"] is None         # once
    finally:
        lg.close()
        store.close()
