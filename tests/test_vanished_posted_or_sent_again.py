"""
Two lawful departures the vanished self-check has no route for, so it keeps the item on Work now although the work is
done (a month-by-month replay of real data, 2026-11-28, class "vanished check lacks routes for lawful departures: kept as
Work now although not work").

1. A payment EMA has since posted. ema_checks.unposted_remits stops listing a REMIT_NOT_POSTED item once EMA's check for
   its trace holds what the 835 paid (Results counts that as money found). The item names one bill and no EMA line, so
   selfcheck._facts_of fingerprints it by that bill's 835 answers only: an EMA posting under its check never changes the
   fingerprint, no route applies, and _keep_vanished puts "Post the payment" back on Work now, run after run, until a
   person accepts the whole vanished check. In the replay: 6 such items, EMA's check for the trace holding exactly the
   835's paid amount, the posting first seen in the month each item left; each kept from then to the end (1 to 8 months).

2. A claim sent again under the same claim id. A biller fixes a rejected claim (or re-sends an unanswered one) without a
   new submission number; the 837 pass then waits for the new send's due day and stops listing the claim, as detect.save
   designs it ("the item waits, stale, and comes back with its own status and notes if its claim id is listed again").
   selfcheck._sent_again only counts a HIGHER submission number of the bill, so the same claim id sent again is no route:
   the old "chase the payer" or "fix and send again" is kept on Work now while the new send is in flight (for a rejected
   claim, an invitation to send a duplicate). In the replay: 22 of 22 such items had an 837 of the same claim id sent in
   the month they left; 13 came back on their own when the new send came due, 6 left when its 835 came.

Each case has a control that must still be kept (the same departure with nothing that explains it), so a fix has to be a
route, not a blanket pass. The fix (selfcheck._no_route's posted_in_ema route, _facts_of's EMA checks of a payment and
_sent_again's same claim id) is checked route by route: the posting is let go even on a run that compares no facts (the
first run of a build whose fingerprints changed), a part posting C4 no longer lists is let go by its new facts, and a
send the item is itself about is never taken for a new one. Every claim id, check number, payer and amount is invented.
Each case runs on the server's store file, the compact in-memory store of the serverless host, and that store's image
reopened.
"""
from __future__ import annotations

import itertools
import json
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch import ema_reports as er  # noqa: E402
from remitwatch import selfcheck as sc  # noqa: E402
from remitwatch import x12_837  # noqa: E402
from remitwatch.detect import ensure_schema  # noqa: E402
from remitwatch.store import Store  # noqa: E402
from tests import ema_fixtures as F  # noqa: E402

TODAY = "2026-11-22"
SALT_HEX = "5e1fc0ffee" * 6 + "0123"
CAN_SERIALIZE = hasattr(sqlite3.Connection, "serialize")
_TX = itertools.count(1)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(er, "CLAIM_HOST_SUFFIX", F.HOST_SUFFIX)
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    for k in ("VERCEL", "REMITWATCH_EMA_REPORTS", "REMITWATCH_EMA_PAYER_ALIASES", "REMITWATCH_EMA_LEDGER", "REMITWATCH_TODAY",
              "REMITWATCH_STALE_DAYS"):
        monkeypatch.delenv(k, raising=False)
    sc._GROUPS.clear()


def _new(kind: str, tmp_path) -> Store:
    s = Store(str(tmp_path / "s.sqlite")) if kind == "file" else Store(":memory:", compact=True)
    ensure_schema(s.conn)
    x12_837.ensure_schema(s.conn)
    s.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('ema:key_salt', ?)", (SALT_HEX,))
    s.conn.commit()
    return s


class Kind:
    """A store of one kind; `image` keeps the data in a compact store and reopens its serialized image between runs (the
    serverless host's next invocation, cloud.load_raw)."""

    def __init__(self, kind: str, tmp_path):
        self.kind = kind
        self.store = _new("compact" if kind == "image" else kind, tmp_path)

    @property
    def conn(self) -> sqlite3.Connection:
        return self.store.conn

    def reopen(self) -> None:
        if self.kind == "image":
            data = self.store.to_bytes()
            self.store.close()
            self.store = Store.from_bytes(data)
            ensure_schema(self.store.conn)
            x12_837.ensure_schema(self.store.conn)


@pytest.fixture(params=["file", "compact", "image"])
def kind(request, tmp_path):
    if request.param == "image" and not CAN_SERIALIZE:
        pytest.skip("this SQLite build cannot serialize a store image")
    k = Kind(request.param, tmp_path)
    yield k
    k.store.close()


# ------------------------------------------------------------------------------------------------------- invented rows
ITEM = dict(severity="medium", actionable="appeal", title="Invented item", status="open", work_state="work_now",
            expected_cents=None, paid_cents=None, payer_group="Testcare Health", payer_kind="commercial",
            payer_name="TESTCARE HEALTH", service_date="2026-03-02", payment_date="2026-09-20", carc=None,
            appeal_deadline=None, deadline_basis=None, stale=0, evidence="{}", line_key=None, claim_key=None,
            first_seen="2026-10-01T00:00:00+00:00", last_seen="2026-10-31T00:00:00+00:00", run_id="r0")


def add_item(conn, fid: str, **kw) -> None:
    row = dict(ITEM, finding_id=fid, **kw)
    if isinstance(row.get("evidence"), dict):
        row["evidence"] = json.dumps(row["evidence"])
    cols = list(row)
    conn.execute(f"INSERT OR REPLACE INTO findings ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [row[c] for c in cols])
    conn.commit()


def remit(conn, paid_on: str, pcn: str, paid_cents: int, trace: str) -> str:
    """One 835 payment of one claim (one line), written straight into the remittance tables. Returns its transaction key."""
    tk = f"TXV{next(_TX):04d}"
    conn.execute("""INSERT INTO transactions (transaction_key, trace_number, payment_date, payer_name, payer_id, superseded,
                    claim_paid_total_cents, payment_amount_cents, payment_method) VALUES (?,?,?,?,?,0,?,?,'ACH')""",
                 (tk, trace, paid_on, "TESTCARE HEALTH", "TC001", paid_cents, paid_cents))
    ck = f"{tk}-C0"
    conn.execute("""INSERT INTO claims (claim_key, transaction_key, patient_control_number, claim_status_code, charge_cents, paid_cents,
                    payer_name, payer_id, payment_date, trace_number, statement_from, statement_to)
                    VALUES (?,?,?,'1',?,?,?,?,?,?,?,?)""",
                 (ck, tk, pcn, paid_cents + 3000, paid_cents, "TESTCARE HEALTH", "TC001", paid_on, trace, "2026-03-02", "2026-03-02"))
    conn.execute("""INSERT INTO lines (line_key, claim_key, transaction_key, procedure_code, charge_cents, paid_cents, allowed_cents,
                    paid_units, service_date, patient_control_number, claim_status_code, payer_name, payer_id, payment_date)
                    VALUES (?,?,?,?,?,?,?,1,?,?,'1',?,?,?)""",
                 (f"{ck}-L0", ck, tk, "99213", paid_cents + 3000, paid_cents, paid_cents, "2026-03-02", pcn, "TESTCARE HEALTH",
                  "TC001", paid_on))
    conn.commit()
    return tk


def report_read(conn, file_id: str, report: str = "production") -> None:
    """One EMA report file read and applied (ema_report_files), as the readers record it: new source data since the run
    before, so the self-check compares the items' facts."""
    conn.execute("INSERT INTO ema_report_files (file_id, report, status, applied_at) VALUES (?, ?, 'current', ?)",
                 (file_id, report, f"2026-11-2{len(file_id) % 9}T08:00:00+00:00"))
    conn.commit()


def sent_837(conn, pcn: str, sent_on: str) -> None:
    """One 837 claim the practice sent (claims_837, as x12_837 reads it), under the claim id `pcn`."""
    conn.execute("""INSERT INTO claims_837 (claim_837_key, patient_control_number, bht_created, total_charge_cents, frequency_code,
                    payer_name, payer_id, service_date) VALUES (?,?,?,?,'1','TESTCARE HEALTH','TC001','2026-03-02')""",
                 (f"K837-{pcn}-{sent_on}", pcn, sent_on, 15000))
    conn.commit()


def run(conn, mutate=None, rid="r1", today=TODAY):
    """One detection run as Detector.run drives the self-check: begin_detection, the run's save (`mutate`), after_detection."""
    ctx = sc.begin_detection(conn, run_id=rid, today=today)
    if mutate:
        mutate(conn)
        conn.commit()
    return sc.after_detection(conn, ctx, mode="server", today=today, run_id=rid)


def vanished(conn) -> dict:
    return next(r for r in sc.meta_get(conn, sc.K_LAST)["checks"] if r["id"] == "vanished")


def listed(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT finding_id FROM findings WHERE COALESCE(stale, 0) = 0 "
                                       "AND status IN ('open','in_progress','appealed') AND work_state IN ('work_now','waiting')")}


def unposted(tk: str, pcn: str, cents: int) -> dict:
    return dict(signal="REMIT_NOT_POSTED", category="fees", severity="medium", actionable="post",
                title="Paid by Testcare Health (trace ending 0001): not posted in EMA", transaction_key=tk,
                patient_control_number=pcn, procedure_code=None, shortfall_cents=cents, state_rule="post_payment",
                state_basis="Post the payment: find the deposit, then post it under this trace in EMA",
                evidence={"ema_posted_cents": 0, "claim_paid_cents": cents, "claims": 1})


# ===================================================================================== 1. a payment EMA has since posted
def test_a_payment_ema_posted_in_full_leaves_the_list_without_a_keep(kind):
    """Run 1 lists two 835 payments EMA had not posted. Before run 2 a newer Production report is read: EMA posted the
    first one in full under its own trace (ema_checks.unposted_remits no longer lists it: settled). The second is still not
    posted, and the run drops it anyway (a code bug would): only that one may be kept."""
    c = kind.conn
    tk_posted = remit(c, "2026-09-20", "ZZ00T0011C001", 8543, trace="770000011")
    tk_lost = remit(c, "2026-09-20", "ZZ00T0012C001", 21204, trace="770000012")
    add_item(c, "F-posted", **unposted(tk_posted, "ZZ00T0011C001", 8543))
    add_item(c, "F-unposted", **unposted(tk_lost, "ZZ00T0012C001", 21204))
    report_read(c, "prod-1")
    out1 = run(c, None, "r1")
    assert out1["kept"] == 0 and {"F-posted", "F-unposted"} <= listed(c)
    kind.reopen()
    c = kind.conn
    # the next cycle reads a newer Production report before detection: EMA posted trace 770000011 in full under its number
    report_read(c, "prod-22")
    c.execute("""INSERT INTO ema_checks (check_no, payer, payer_group, ins_paid, rows, first_posted, last_posted, trace_key,
                 trace_payment_date, trace_paid_cents, match) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
              ("770000011", "Testcare Health", "Testcare Health", 8543, 1, "2026-11-03", "2026-11-03", tk_posted,
               "2026-09-20", 8543, "exact"))
    c.commit()

    def save(cn):
        # the run's save: unposted_remits lists neither (the first is posted; the second is dropped with no reason)
        cn.execute("DELETE FROM findings WHERE finding_id IN ('F-posted', 'F-unposted')")
    out = run(c, save, "r2")
    van = vanished(c)
    assert "F-unposted" in listed(c), van                    # nothing explains the second: still kept (the check's purpose)
    assert "F-posted" not in listed(c) and out["kept"] == 1, {
        "kept": out["kept"], "examples": van.get("examples"), "routes": van.get("info", {}).get("routes"),
        "why": "EMA posted the payment in full under its trace, yet the item is kept on Work now ('Post the payment'): "
               "_facts_of fingerprints the item by its bill's 835 answers only and no route reads EMA's check"}
    assert van["info"]["routes"].get("posted_in_ema") == 1, van["info"]


def _two_unposted(kind) -> tuple[str, str]:
    """Run 1 on two 835 payments EMA had not posted (one is the control). Returns their transaction keys."""
    c = kind.conn
    tk_posted = remit(c, "2026-09-20", "ZZ00T0031C001", 8543, trace="770000031")
    tk_lost = remit(c, "2026-09-20", "ZZ00T0032C001", 21204, trace="770000032")
    add_item(c, "F-posted", **unposted(tk_posted, "ZZ00T0031C001", 8543))
    add_item(c, "F-unposted", **unposted(tk_lost, "ZZ00T0032C001", 21204))
    report_read(c, "prod-1")
    out1 = run(c, None, "r1")
    assert out1["kept"] == 0 and {"F-posted", "F-unposted"} <= listed(c)
    return tk_posted, tk_lost


def _ema_check(c, check_no: str, tk: str, cents: int, trace_paid: int) -> None:
    c.execute("""INSERT INTO ema_checks (check_no, payer, payer_group, ins_paid, rows, first_posted, last_posted, trace_key,
                 trace_payment_date, trace_paid_cents, match) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
              (check_no, "Testcare Health", "Testcare Health", cents, 1, "2026-11-03", "2026-11-03", tk, "2026-09-20",
               trace_paid, "exact"))
    c.commit()


def _drop_both(cn) -> None:
    cn.execute("DELETE FROM findings WHERE finding_id IN ('F-posted', 'F-unposted')")


def test_a_payment_ema_posted_in_full_leaves_on_a_run_that_compares_no_facts(kind):
    """The first run of a build whose fingerprints changed (another facts version is stored) compares no facts: nothing is
    excused as new facts there. EMA's check holding the payment in full is a route of its own, on that run too."""
    tk_posted, _tk_lost = _two_unposted(kind)
    kind.reopen()
    c = kind.conn
    report_read(c, "prod-22")
    _ema_check(c, "770000031", tk_posted, 8543 - 40, 8543)         # posted 40 cents short: within max($1, 0.5%)
    sc.meta_set(c, sc.K_FACTS_VERSION, "an older build")            # the fingerprints stored are another build's
    c.commit()
    out = run(c, _drop_both, "r2")
    van = vanished(c)
    assert "F-unposted" in listed(c) and out["kept"] == 1, van
    assert "F-posted" not in listed(c), van
    assert van["info"]["routes"].get("posted_in_ema") == 1 and not van["info"]["routes"].get("facts_changed"), van["info"]


def test_a_payment_ema_posted_in_part_that_c4_no_longer_lists_leaves_by_its_new_facts(kind):
    """EMA posts $70.00 of the $85.43: short by $15.43, under the $25 C4 lists as 'posted short', so the run drops it. Not
    posted in full (no posted_in_ema route), but EMA's checks of the payment are new facts on the item's own subject."""
    tk_posted, _tk_lost = _two_unposted(kind)
    kind.reopen()
    c = kind.conn
    report_read(c, "prod-22")
    _ema_check(c, "770000031", tk_posted, 7000, 8543)
    out = run(c, _drop_both, "r2")
    van = vanished(c)
    assert "F-unposted" in listed(c) and out["kept"] == 1, van
    assert "F-posted" not in listed(c), van
    assert van["info"]["routes"].get("facts_changed") == 1 and not van["info"]["routes"].get("posted_in_ema"), van["info"]


def test_control_a_payment_posted_short_by_more_than_the_tolerance_on_a_run_with_no_facts_is_kept(kind):
    """The same part posting on a run that compares no facts: nothing shows the work done, so the item is kept."""
    tk_posted, _tk_lost = _two_unposted(kind)
    kind.reopen()
    c = kind.conn
    report_read(c, "prod-22")
    _ema_check(c, "770000031", tk_posted, 7000, 8543)
    sc.meta_set(c, sc.K_FACTS_VERSION, "an older build")
    c.commit()
    out = run(c, _drop_both, "r2")
    assert {"F-posted", "F-unposted"} <= listed(c) and out["kept"] == 2, vanished(c)


# ============================================================================ 2. a claim sent again under the same claim id
@pytest.mark.parametrize("signal", ["NO_ANSWER", "REJECTED_PRE_PAYER"])
def test_a_claim_sent_again_under_the_same_claim_id_waits_without_a_keep(kind, signal):
    """Run 1 lists two claims no payer answered (or that never reached the payer). Before run 2 the biller sends the first
    one again under the same claim id (an 837 dated after the item was raised); the 837 pass waits for that send's due day
    and stops listing it. The second claim was not sent again and the run drops it anyway: only that one may be kept."""
    c = kind.conn
    sent_837(c, "ZZ00T0021C017", "2026-08-01")         # each claim's first send, before the items were raised
    sent_837(c, "ZZ00T0022C017", "2026-08-01")
    for fid, pcn in (("F-resent", "ZZ00T0021C017"), ("F-lone", "ZZ00T0022C017")):
        add_item(c, fid, signal=signal, category="denial", patient_control_number=pcn, procedure_code="99213",
                 shortfall_cents=None, charge_cents=15000, appeal_deadline="2027-03-01", deadline_basis="invented window",
                 state_rule="open", state_basis="No answer from the payer on file" if signal == "NO_ANSWER" else
                 "Never reached the payer: fix it and send it again", transaction_key=None)
    run(c, None, "r1")
    assert {"F-resent", "F-lone"} <= listed(c)
    kind.reopen()
    c = kind.conn
    sent_837(c, "ZZ00T0021C017", "2026-11-10")         # the next cycle reads the 837: sent again, same claim id, after the
                                                        # item was raised (the 837 pass shows it waiting for its due day)

    def save(cn):
        # the run's save: the 837 pass lists neither (the first waits for its new send; the second is dropped with no reason)
        cn.execute("DELETE FROM findings WHERE finding_id IN ('F-resent', 'F-lone')")
    out = run(c, save, "r2")
    van = vanished(c)
    assert "F-lone" in listed(c), van                         # nothing explains the second: still kept
    assert "F-resent" not in listed(c) and out["kept"] == 1, {
        "kept": out["kept"], "examples": van.get("examples"), "routes": van.get("info", {}).get("routes"),
        "why": "the claim went out again under the same claim id after the item was raised, yet the old next step is kept "
               "on Work now: _sent_again only counts a higher submission number"}
    assert van["info"]["routes"].get("sent_again") == 1, van["info"]


def test_control_the_send_an_item_is_about_is_not_a_send_again(kind):
    """A claim rejected the day it went out: the item, raised that day, is about that send (its evidence's `sent`), and
    the 837 of that day is the send itself, not a new one. Dropped with no reason, it is kept. A send the EMA exports date
    later (claim_submission_date) is one: that claim leaves."""
    c = kind.conn
    for fid, pcn in (("F-same-day", "ZZ00T0041C017"), ("F-ema-resent", "ZZ00T0042C017")):
        sent_837(c, pcn, "2026-10-01")
        add_item(c, fid, signal="REJECTED_PRE_PAYER", category="denial", patient_control_number=pcn, procedure_code="99213",
                 shortfall_cents=None, charge_cents=15000, appeal_deadline="2027-03-01", deadline_basis="invented window",
                 state_rule="open", state_basis="Never reached the payer: fix it and send it again", transaction_key=None,
                 first_seen="2026-10-01T09:00:00+00:00", evidence={"sent": "2026-10-01", "sent_basis": "837"})
    run(c, None, "r1")
    assert {"F-same-day", "F-ema-resent"} <= listed(c)
    kind.reopen()
    c = kind.conn
    sent_837(c, "ZZ00T0041C017", "2026-09-30")          # an older send read late: never a send again
    c.execute("INSERT INTO ema_rows (row_fingerprint, family, claim_id, claim_submission_date) VALUES (?, ?, ?, ?)",
              ("fp-invented-0042", "charges", "ZZ00T0042C017", "2026-11-12"))     # EMA's weekly export: submitted again
    c.commit()
    out = run(c, lambda cn: cn.execute("DELETE FROM findings WHERE finding_id IN ('F-same-day', 'F-ema-resent')"), "r2")
    van = vanished(c)
    assert "F-same-day" in listed(c) and out["kept"] == 1, van
    assert "F-ema-resent" not in listed(c) and van["info"]["routes"].get("sent_again") == 1, van


# ================================================================ the review's cases: a resend read late, or sent that evening
def _two_claims_raised(kind, signal: str, first_seen: str, evidence: dict | None) -> None:
    """Two claims, each sent on August 1 and listed by run 1 (raised on `first_seen`, about the send its evidence names)."""
    c = kind.conn
    sent_837(c, "ZZ00R10021C017", "2026-08-01")
    sent_837(c, "ZZ00R10022C017", "2026-08-01")
    for fid, pcn in (("F-resent", "ZZ00R10021C017"), ("F-lone", "ZZ00R10022C017")):
        add_item(c, fid, signal=signal, category="denial", patient_control_number=pcn, procedure_code="99213",
                 shortfall_cents=None, charge_cents=15000, appeal_deadline="2027-03-01", deadline_basis="invented window",
                 state_rule="open", state_basis="No answer from the payer on file", transaction_key=None, first_seen=first_seen,
                 evidence=evidence if evidence is not None else "{}")
    run(c, None, "r1")
    assert {"F-resent", "F-lone"} <= listed(c)


def _drop_two(cn) -> None:
    cn.execute("DELETE FROM findings WHERE finding_id IN ('F-resent', 'F-lone')")


@pytest.mark.parametrize("signal", ["NO_ANSWER", "REJECTED_PRE_PAYER"])
def test_a_resend_read_after_the_item_was_raised_is_a_send_again(kind, signal):
    """The biller sends the claim again under its claim id on November 8. That 837 reaches the store only after the run
    of November 10 raised the item from the August send it knew (837 files come in batches). Once it is read the claim
    waits for its new due day and the item leaves: the send is after the one the item is about (its evidence's sent,
    August 1), so it explains the departure, though it is dated before the day the item was first seen."""
    _two_claims_raised(kind, signal, "2026-11-10T14:00:00+00:00", {"sent": "2026-08-01", "sent_basis": "837"})
    kind.reopen()
    c = kind.conn
    sent_837(c, "ZZ00R10021C017", "2026-11-08")
    out = run(c, _drop_two, "r2")
    van = vanished(c)
    assert "F-lone" in listed(c), van
    assert "F-resent" not in listed(c) and out["kept"] == 1, (van.get("examples"), van.get("info"))
    assert van["info"]["routes"].get("sent_again") == 1, van["info"]


def test_a_resend_the_evening_the_item_was_raised_is_a_send_again(kind):
    """The run of 9:30 PM Eastern on November 10 (02:30 UTC on November 11) raises the item; the biller sends the claim
    again that evening, an 837 dated November 10 (the practice's day)."""
    _two_claims_raised(kind, "NO_ANSWER", "2026-11-11T02:30:00+00:00", {"sent": "2026-08-01", "sent_basis": "837"})
    kind.reopen()
    c = kind.conn
    sent_837(c, "ZZ00R10021C017", "2026-11-10")
    out = run(c, _drop_two, "r2")
    van = vanished(c)
    assert "F-resent" not in listed(c) and out["kept"] == 1, (van.get("examples"), van.get("info"))


def test_an_item_with_no_send_named_counts_a_resend_from_the_practices_day_it_was_raised(kind, monkeypatch):
    """An item an older build raised names no send in its evidence: a send of its claim id on or after the day it was
    raised explains its departure. That day is the practice's (America/New_York here): raised at 02:30 UTC on November 11,
    9:30 PM on November 10 where the practice is, so an 837 dated November 10 counts. One dated November 9 does not."""
    monkeypatch.setenv("REMITWATCH_TZ", "America/New_York")
    _two_claims_raised(kind, "NO_ANSWER", "2026-11-11T02:30:00+00:00", None)
    kind.reopen()
    c = kind.conn
    sent_837(c, "ZZ00R10021C017", "2026-11-10")
    sent_837(c, "ZZ00R10022C017", "2026-11-09")        # before the day it was raised: the send it was raised from, read late
    out = run(c, _drop_two, "r2")
    van = vanished(c)
    assert "F-lone" in listed(c), van
    assert "F-resent" not in listed(c) and out["kept"] == 1, (van.get("examples"), van.get("info"))


# =========================================================== the review's case: the first run after the payment part came
def _ema_line(conn, link: int, bill: str, **kw) -> None:
    """One EMA line summary (ema_lines), as the ledger keeps it for an EMA claim number."""
    row = dict(link=link, svc="2026-03-02", cpt="99213", bill=bill, payer_group="Testcare Health", payer_kind="commercial",
               charges=15000, ins_charges=15000, ins_paid=0, contractual=0, noncontractual=0, nonroutine=0, balance=15000,
               facility=0, ins_answer_rows=0, status_class="pending", first_charge_posted="2026-03-03")
    row.update(kw)
    cols = list(row)
    conn.execute(f"INSERT OR REPLACE INTO ema_lines ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [row[c] for c in cols])
    conn.commit()


def test_the_first_run_after_the_payment_part_came_still_compares_every_other_items_facts(kind):
    """The build that added EMA's checks of a payment to the fingerprint of an item about a payment (REMIT_NOT_POSTED,
    TAKEBACK) changed no other item's fingerprint. Its first run on a store the build before saved (facts version 3, no
    payment part) must still compare the others: a never-answered claim whose EMA lines moved (the payer's answer posted
    in EMA, read in a new report) leaves by its new facts, instead of being kept on Work now ('nothing explains why')
    until the owner accepts. Only the payment item's stored fingerprint, made without its checks, is not compared: the
    payment EMA posted short is kept once, as before."""
    c = kind.conn
    tk = remit(c, "2026-09-20", "ZZ00T0051C001", 21204, trace="770000051")
    add_item(c, "F-payment", **unposted(tk, "ZZ00T0051C001", 21204))
    _ema_line(c, 7051, bill="ZZ00T0052")
    add_item(c, "F-claim", signal="NO_ANSWER", category="denial", patient_control_number="ZZ00T0052C017", procedure_code="99213",
             shortfall_cents=None, charge_cents=15000, appeal_deadline="2027-03-01", deadline_basis="invented window",
             state_rule="open", state_basis="No answer from the payer on file", transaction_key=None, ema_link=7051)
    report_read(c, "prod-1")
    run(c, None, "r1")
    assert {"F-payment", "F-claim"} <= listed(c)
    # the store as the build released before left it: fingerprints of facts version 3, none with a payment part
    sc.meta_set(c, sc.K_FACTS_VERSION, "3")
    c.execute("DELETE FROM meta WHERE key = 'selfcheck:facts_payment_version'")
    c.commit()
    kind.reopen()
    c = kind.conn
    report_read(c, "prod-22")
    _ema_line(c, 7051, bill="ZZ00T0052", ins_paid=9000, contractual=6000, balance=0, ins_answer_rows=1, status_class="paid",
              last_posted="2026-11-03")
    _ema_check(c, "770000051", tk, 7000, 21204)         # EMA posted $70.00 of the $212.04: not in full
    out = run(c, lambda cn: cn.execute("DELETE FROM findings WHERE finding_id IN ('F-payment', 'F-claim')"), "r2")
    van = vanished(c)
    assert "F-claim" not in listed(c) and van["info"]["routes"].get("facts_changed") == 1, van
    assert "F-payment" in listed(c) and out["kept"] == 1, van
    assert sc.meta_get(c, "selfcheck:facts_payment_version") is not None      # this run's own, compared next time
