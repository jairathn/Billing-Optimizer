"""The integrator's merge of the hook unit (the self-checks wired into every run) with the owner's decision 4 (a code a
payer almost never pays makes one pattern per period of the payer's rule): an item the owner removes by accepting its
pattern has a route out, so the vanished check never keeps it on the list against the owner's own decision.

Invented data only. The end-to-end cases on both store kinds are in tests/test_payer_code_rules.py
(test_ema_only_denials_of_a_rule_that_changed_on_a_date_are_one_pattern_per_period and
test_a_never_payable_pattern_stays_pursued_until_the_owner_accepts_it), which failed on the merge for this reason."""
from __future__ import annotations

import json
import sqlite3

import pytest

from remitwatch import rarely_paid as rp
from remitwatch import selfcheck as sc


def _conn(policy: dict) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO meta (key, value) VALUES (?, ?)", ("ema:writeoff_policy", json.dumps(policy)))
    return conn


def _ema_denial(fid: str, svc: str) -> dict:
    """An EMA-only denial of Optum's 77600 (no remittance on file), listed as Work now."""
    return {"finding_id": fid, "signal": "EMA_DENIED", "category": "denial", "status": "open", "work_state": "work_now",
            "stale": 0, "payer_group": "Optum (UHC)", "procedure_code": "77600", "service_date": svc, "line_key": None,
            "claim_key": None, "patient_control_number": None, "transaction_key": None, "shortfall_cents": 30000,
            "evidence": json.dumps({"lines": [["77600", svc]]})}


def _vanished(conn, before: dict) -> dict:
    return sc.check_vanished(conn, before, {})


@pytest.mark.parametrize("svc, accepted, kept", [
    ("2026-02-02", "from 2026-01-01", 0),      # the owner accepted the 2026 pattern: the 2026 item leaves by that decision
    ("2025-09-02", "from 2026-01-01", 1),      # the 2025 item is another period's pattern, still pursued: kept as vanished
])
def test_an_item_whose_period_pattern_the_owner_accepted_is_not_kept_as_vanished(svc, accepted, kept):
    key = f"Optum (UHC)|77600|{accepted}|payer_policy"
    assert rp.pattern_key("Optum (UHC)", "77600", svc).endswith("|payer_policy")
    conn = _conn({key: {"decision": "accept", "by": "owner", "at": "2026-11-27T01:00:00+00:00"}})
    out = _vanished(conn, {"f1": _ema_denial("f1", svc)})
    expect_key = rp.pattern_key("Optum (UHC)", "77600", svc) == key
    assert (out["n"], expect_key) == (kept, kept == 0), out


def test_an_accept_of_the_pair_at_every_date_covers_each_period_unless_a_newer_pursue_says_otherwise():
    whole = "Optum (UHC)|77600||payer_policy"
    period = rp.pattern_key("Optum (UHC)", "77600", "2026-02-02")
    conn = _conn({whole: {"decision": "accept", "by": "owner", "at": "2026-11-20T10:00:00+00:00"}})
    assert _vanished(conn, {"f1": _ema_denial("f1", "2026-02-02")})["n"] == 0
    conn = _conn({whole: {"decision": "accept", "by": "owner", "at": "2026-11-20T10:00:00+00:00"},
                  period: {"decision": "pursue", "by": "owner", "at": "2026-11-25T10:00:00+00:00"}})
    assert _vanished(conn, {"f1": _ema_denial("f1", "2026-02-02")})["n"] == 1     # the newer decision wins


def test_a_pattern_the_owner_never_accepted_gives_no_route():
    conn = _conn({})
    assert _vanished(conn, {"f1": _ema_denial("f1", "2026-02-02")})["n"] == 1


def _code_changed(fid: str, paid: int) -> dict:
    """A 77600 line the payer answered as 77436 (a code-change item on Work now), paid `paid` cents."""
    return {"finding_id": fid, "signal": "CODE_CHANGED", "category": "downcoding", "status": "open", "work_state": "work_now",
            "stale": 0, "payer_group": "Anthem / BCBS", "procedure_code": "77436", "original_procedure_code": "77600",
            "paid_cents": paid, "service_date": "2026-03-02", "line_key": "line-" + fid, "claim_key": "claim-" + fid,
            "patient_control_number": None, "transaction_key": None, "shortfall_cents": 41000, "evidence": "{}"}


@pytest.mark.parametrize("paid, kept", [
    (0, 0),         # nothing paid: the billed 77600 is the pattern the owner accepted (detect.fold_payer_policy read it so)
    (2500, 1),      # the payer paid on the changed code: not a denial of 77600, the run never folds it, so no route
])
def test_a_code_the_payer_changed_is_read_by_the_code_billed_and_what_was_paid(paid, kept):
    """The run's snapshot (selfcheck._rows_of, as after_detection reads it) carries what fold_payer_policy read: the code
    billed and what was paid. Without them a 77600 answered as 77436 is read as 77436 and kept against the owner's accept."""
    key = rp.pattern_key("Anthem / BCBS", "77600", "2026-03-02")
    assert key.split("|")[1] == "77600"
    conn = _conn({key: {"decision": "accept", "by": "owner", "at": "2026-11-27T01:00:00+00:00"}})
    row = _code_changed("f9", paid)
    conn.execute(f"CREATE TABLE snap ({', '.join(row)})")
    conn.execute(f"INSERT INTO snap VALUES ({','.join('?' * len(row))})", list(row.values()))
    before = sc._rows_of(conn, "snap")
    assert (before["f9"].get("original_procedure_code"), before["f9"].get("paid_cents")) == ("77600", paid)
    assert _vanished(conn, before)["n"] == kept


def test_an_ema_only_denial_leaves_by_the_owner_only_when_every_one_of_its_lines_is_accepted():
    """ema_checks C5 drops each EMA line whose pattern the owner accepted; the item goes only when all its lines went. One
    line accepted and one still pursued, with nothing new on the claim, is no reason for the item to vanish: it is kept."""
    k26 = rp.pattern_key("Optum (UHC)", "77600", "2026-02-02")
    k_other = rp.pattern_key("Optum (UHC)", "G6001", "2026-02-02")
    two = _ema_denial("f2", "2026-02-02")
    two["evidence"] = json.dumps({"lines": [["77600", "2026-02-02"], ["G6001", "2026-02-02"]]})
    accept = {"decision": "accept", "by": "owner", "at": "2026-11-27T01:00:00+00:00"}
    assert _vanished(_conn({k26: accept}), {"f2": dict(two)})["n"] == 1
    assert _vanished(_conn({k26: accept, k_other: accept}), {"f2": dict(two)})["n"] == 0


def test_a_whole_claim_denial_is_never_left_out_by_a_payer_policy_accept():
    """detect.fold_payer_policy folds remittance lines only and C5 EMA's lines only: a whole-claim denial whose unpaid code
    has an accepted pattern was not removed by that decision, so the decision is no route for it."""
    key = rp.pattern_key("Optum (UHC)", "77600", "2026-02-02")
    row = {"finding_id": "f3", "signal": "CLAIM_DENIED_UNPAID", "category": "denial", "status": "open",
           "work_state": "work_now", "stale": 0, "payer_group": "Optum (UHC)", "procedure_code": "77600",
           "service_date": "2026-02-02", "line_key": None, "claim_key": "claim-f3", "patient_control_number": None,
           "transaction_key": None, "shortfall_cents": 30000,
           "evidence": json.dumps({"unpaid": [["77600", "2026-02-02"]]})}
    assert rp.item_codes(row) == [("77600", "2026-02-02")], "the item names its unpaid code"
    conn = _conn({key: {"decision": "accept", "by": "owner", "at": "2026-11-27T01:00:00+00:00"}})
    assert _vanished(conn, {"f3": row})["n"] == 1
