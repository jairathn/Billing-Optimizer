"""
A denial whose service another payer had already paid, listed as work (the auditor A16's already-done items on push 2's
Work now list): a patient with a Medicare Advantage plan and Medicaid. The Medicare Advantage company pays the claim
first, on a check no 835 carries, and EMA posts that check under a name with the word Medicaid in it ("<company>
Medicaid Community Plan"), which the payer-name rules file under the state's own Medicaid. A copy of the claim reaches the
state's Medicaid, which denies it weeks later (CO-96 with B7, CO-24, CO-109). Before, the payment looked like the denying
payer's own, so the item stayed on Work now ("find which payer paid") or behind a billing note or a check-first step.
And a duplicate denial (CO-18) that only restates the same payer's earlier payment, with the second payer's share still
open on the line.

Such an item is done, with each payment as its proof; it stays work when the payment was taken back since, when the
denying payer is the one EMA bills first, when only a second payer paid a little, when the payer that paid first paid
less than half of what the service should bring, when a person set its status, and again when a newer pull no longer
shows the payment.

Every claim, name, amount, check and date here is invented (tests/ema_fixtures.py, tests/test_ema_wiring.remit). Each
test runs on the server's store file and on the compact in-memory store the serverless cycle builds and analyses.
"""
from __future__ import annotations

import datetime as dt

import pytest

from remitwatch import ema_ledger as el, ema_reports as er, states
from remitwatch.store import Store
from tests import ema_fixtures as F
from tests import test_ema_wiring as W

D = dt.date
KINDS = ["file", "compact"]
TODAY = "2026-06-01"
MA = "Testcare Medicare Advantage"              # the plan EMA bills first
MCO = "Testcare Medicaid Community Plan"        # the name EMA posts that company's checks under
MCD = "Medicaid Test State"                     # the state's Medicaid, the second payer on file
STATE = "KENTUCKY MEDICAID"                     # the state's Medicaid on its 835s
BCBS = F.PAYERS["bcbs"]                         # a commercial plan (EMA's name)
BCBS_835 = "BLUE CROSS TEST"                    # the same plan on its 835s
OTHER = F.PAYERS["commercial"]


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(er, "CLAIM_HOST_SUFFIX", F.HOST_SUFFIX)
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    monkeypatch.setenv("REMITWATCH_EMA_STRICT", "1")
    for k in ("VERCEL", "REMITWATCH_EMA_REPORTS", "REMITWATCH_EMA_CLAIM_URL", "REMITWATCH_EMA_PAYER_ALIASES",
              "REMITWATCH_EMA_LEDGER", "REMITWATCH_EMA_REPORT_KEEP", "REMITWATCH_EMA_CLOSES"):
        monkeypatch.delenv(k, raising=False)
    from remitwatch import ema_checks as ec
    ec._LAST_POST.clear()


def _host(tmp_path, kind: str, monkeypatch):
    """A store and a posting ledger for one host: the server's store file, or the compact in-memory store the serverless
    cycle builds (its ledger is a file beside the state)."""
    if kind == "file":
        s = Store(str(tmp_path / "store.sqlite"))
    else:
        monkeypatch.setenv("REMITWATCH_MODE", "serverless")
        monkeypatch.setenv("REMITWATCH_EMA_REPORTS", "all")
        s = Store(":memory:", compact=True)
    er.ensure_schema(s.conn)
    s.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('ema:key_salt', ?)", (W.SALT_HEX,))
    s.conn.commit()
    return s, el.Ledger.open(str(tmp_path / f"ledger_{kind}.sqlite"))


def _svc(n: int) -> D:
    return D(2026, 3, 1) + dt.timedelta(days=n - 30)


def _iso(d: D) -> str:
    return d.isoformat()


def _history(store) -> None:
    """25 visits the state's Medicaid paid at its usual $70.00 for 99214, and Blue Cross at $90.00: each payer's rate,
    so each denial has a size (half of it is what a payment must reach)."""
    W.remit(store, trace="777000901", paid_on="2026-02-20", payer=STATE,
            claims=[{"pcn": f"ZZ09H{i:04d}C001", "last": "HISTORY", "first": "CASE",
                     "lines": [("2026-02-02", "99214", 150, 70, [("CO", "45", 80)], {"allowed_cents": 7000})]} for i in range(25)])
    W.remit(store, trace="777000902", paid_on="2026-02-20", payer=BCBS_835,
            claims=[{"pcn": f"ZZ08H{i:04d}C001", "last": "HISTORY", "first": "CASE",
                     "lines": [("2026-02-02", "99214", 150, 90, [("CO", "45", 60)], {"allowed_cents": 9000})]} for i in range(25)])


def _claim(store, n: int, *, payer: str, carc: str, status: str = "4", paid_on: D | None = None, code: str = "99214",
           pieces: int = 1) -> D:
    """Claim n: a 99214 (or `code`) charged $150.00 on its own day (on `pieces` lines of the code that share the charge),
    denied whole by `payer` (claim status 4) with CO-<carc> on its 835 (trace = check n) six weeks after the visit.
    Returns the remittance's date."""
    paid_on = paid_on or _svc(n) + dt.timedelta(days=45)
    each = 150 / pieces
    W.remit(store, trace=F.check_no(n), paid_on=_iso(paid_on), payer=payer, status=status,
            claims=[{"pcn": F.claim_id(n), "last": "SAMPLE", "first": "CASE",
                     "lines": [(_iso(_svc(n)), code, each, 0, [("CO", carc, each)])] * pieces}])
    return paid_on


def _ema(n: int, *, first: str | None = MCO, first_paid: float = 92.18, primary: str = MA, secondary: str | None = MCD,
         denier: str = MCD, share: str = "written_off", taken_back: bool = False, extra: tuple = (), code: str = "99214",
         amount: float = 150) -> tuple[list, list]:
    """EMA's claim n (its claim number n): the charge; `first` pays `first_paid` on a paper check 20 days after the visit
    (the rest of the allowed amount, $27.82 of the $150.00 when the full $92.18 is paid, is the second payer's share, and
    $30.00 is the contract's reduction); EMA posts the denial's $0 answer under `denier` two days after its remittance,
    then writes the share off (CO-B7) or leaves it open. taken_back: `first` recoups its payment after the denial. amount:
    what EMA's line charges for the date and code (other lines of the code than the claim's when it is not $150.00)."""
    svc = _svc(n)
    remit_day = svc + dt.timedelta(days=45)
    kw = dict(patient=n % 4, svc=svc, cpt=code, primary=primary, secondary=secondary or "None", status="Resolved/Other")
    link = F.link_no(n)
    rows = [W.charge(link, amount=amount, posted=svc, **kw)]
    if first:
        rows += [W.pay(link, f"PAPER{n:04d}", first_paid, posted=svc + dt.timedelta(days=20), payer=first, **kw),
                 W.adj(link, "CO-45", 30, posted=svc + dt.timedelta(days=20), contractual=True, payer=first, **kw)]
    rows.append(W.pay(link, F.check_no(n), 0, posted=remit_day + dt.timedelta(days=2), payer=denier, **kw))
    left = round(amount - 30 - (first_paid if first else 0), 2)
    if share == "written_off" and left > 0:
        rows.append(W.adj(link, "CO-B7", left, posted=remit_day + dt.timedelta(days=2), payer=denier, **kw))
    if taken_back:
        rows.append(W.pay(link, f"RECOUP{n:04d}", -first_paid, posted=remit_day + dt.timedelta(days=10), payer=first, **kw))
    rows += list(extra)
    ar = [F.ar_row(claim=F.claim_id(n), link=link, status="Denied", denial=150, current=0, aging=remit_day, svc=svc, cpt=code,
                   primary=primary, secondary=secondary or "None", patient=n % 4)]
    return rows, ar


def _carry(posted: D) -> dict:
    """A later charge on another claim: Production's postings reach `posted`."""
    return W.charge(F.link_no(99), cpt="99212", patient=3, svc=D(2026, 3, 20), posted=posted, status="Payment Pending")


def _remits(store) -> None:
    """The remittances, each case on its own claim (the numbers name them in the tests below)."""
    _history(store)
    for n in (31, 32, 33, 34, 35, 36):
        # the state's Medicaid denies the whole claim: CO-96 (with B7 on real data), CO-24 (managed care), CO-109 (not its claim)
        _claim(store, n, payer=STATE, carc={32: "24", 33: "109"}.get(n, "96"))
    for n in (37, 38):
        _claim(store, n, payer=BCBS_835, carc="16")         # Blue Cross, billed first, denies for missing information
    _claim(store, 39, payer=BCBS_835, carc="18")             # a duplicate of a claim Blue Cross paid
    for n in (40, 41, 43):
        _claim(store, n, payer=STATE, carc="96", code="17110")   # a code with no rate on file: nothing says what it should bring
    _claim(store, 42, payer=STATE, carc="96", code="17110", pieces=2)  # the code on two lines of the claim, $75.00 each


def _pull(store, ledger, tmp_path, *, drop_first_paid: tuple = (), carry: D = D(2026, 5, 25), ar_too: bool = True) -> None:
    """EMA's Production report and A/R (called again: a newer Production pull of the same dates, the A/R as it was)."""
    rows, ar = [], []

    def add(pair):
        rows.extend(pair[0])
        ar.extend(pair[1])
    # 31-33: the first payer paid; the state's Medicaid denied the copy it got (33: another plan is the second payer on
    # file, so the state's Medicaid is not on EMA's claim at all)
    add(_ema(31, first=None if 31 in drop_first_paid else MCO))
    add(_ema(32, share="open"))
    add(_ema(33, secondary=OTHER))
    # 34: as 31, and the first payer took its payment back after the denial
    add(_ema(34, taken_back=True))
    # 35: the state's Medicaid is the plan EMA bills first; another payer's payment does not settle its denial (the rest of
    # the line is still open in EMA)
    add(_ema(35, first=OTHER, primary=MCD, secondary=None, share="open"))
    # 36: the first payer paid $20.00, less than half of what the service should bring ($70.00, Medicaid's rate)
    add(_ema(36, first_paid=20))
    # 37: Blue Cross, billed first, denied; the second payer then paid $10.00 of it. 38: the second payer had paid $10.00 before
    svc37 = _svc(37)
    kw37 = dict(patient=37 % 4, svc=svc37, cpt="99214", primary=BCBS, secondary=MCD, status="Resolved/Other")
    add(_ema(37, first=None, primary=BCBS, secondary=MCD, denier=BCBS, share="open",
             extra=(W.pay(F.link_no(37), "PAPER0371", 10, posted=svc37 + dt.timedelta(days=52), payer=MCD, **kw37),)))
    add(_ema(38, first=MCD, first_paid=10, primary=BCBS, secondary=MCD, denier=BCBS, share="open"))
    # 39: Blue Cross paid $90.00 on a paper check, then denied a duplicate (CO-18); the second payer's share is still open
    add(_ema(39, first=BCBS, first_paid=90, primary=BCBS, secondary=OTHER, denier=BCBS, share="open"))
    # 40, 41: no rate on file for the code, so half the line's $150.00 charge is the floor: the first payer paid $20.00 (40)
    # or $92.18 (41) before the state's Medicaid denied it
    add(_ema(40, first_paid=20, code="17110"))
    add(_ema(41, code="17110"))
    # 42: EMA's line holds both of the claim's 17110 lines ($150.00, its charge): the payment on it is the claim's. 43: EMA's
    # line for the date and code charges $200.00, more than the claim's one $150.00 line: it holds another claim's line too,
    # so its $120.00 payment is not this claim's to count
    add(_ema(42, code="17110"))
    add(_ema(43, code="17110", amount=200, first_paid=120))
    rows.append(_carry(carry))
    W.load(store, ledger, tmp_path, rows, ar if ar_too else None)


def _world(store, ledger, tmp_path) -> None:
    _remits(store)
    _pull(store, ledger, tmp_path)


def _by_claim(store) -> dict[str, dict]:
    return {r["patient_control_number"]: r for r in W.items(store, "category IN ('denial','bundling') AND patient_control_number LIKE 'ZZ00T%'")}


DONE_FIRST = {F.claim_id(n) for n in (31, 32, 33, 41, 42)}
WORK = {F.claim_id(n) for n in (34, 35, 36, 37, 38, 40, 43)}


def _states(store) -> dict[str, tuple]:
    return {pcn: (r["status"], r["work_state"], r["appeal_block"], r["state_basis"]) for pcn, r in _by_claim(store).items()}


@pytest.mark.parametrize("kind", KINDS)
def test_a_second_payers_denial_of_a_service_the_first_payer_paid_is_done_with_its_proof(tmp_path, kind, monkeypatch):
    """31-33: the first payer's company paid $92.18 on a paper check (EMA posts it under '<company> Medicaid Community
    Plan'), then the state's Medicaid denied its copy (CO-96, CO-24, CO-109): done (off Work now, still open), the payment
    named as the proof; 41 the same on a code with no rate on file; 42 the same on a code billed twice on the claim (EMA's
    line holds both). 39: a duplicate denial of a claim the same payer paid, the second payer's share open: done. The rest
    stay work: 34 (the payment taken back since), 35 (Medicaid is the plan EMA bills first), 36 (the first payer paid less
    than half of what the service should bring), 40 (no rate on file, and the first payer paid less than half the charge),
    43 (EMA's line for the date and code holds another claim's line too), 37 and 38 (Blue Cross, billed first, denied; only
    the second payer paid a little, after or before)."""
    s, lg = _host(tmp_path, kind, monkeypatch)
    try:
        _world(s, lg, tmp_path)
        W.run(s, today=TODAY)
        got = _states(s)
        for pcn in sorted(got):
            print(pcn, got[pcn])
        assert set(got) >= DONE_FIRST | WORK | {F.claim_id(39)}, sorted(got)
        for pcn in DONE_FIRST:
            status, state, block, basis = got[pcn]
            assert status == "open" and state == "done", (pcn, got[pcn])
            assert basis.startswith(f"Already paid by {MCO}: EMA posted $92.18 on "), (pcn, basis)
            assert "(no 835), before this Medicaid KY denial" in basis and "no money to recover" in basis, (pcn, basis)
        # the second payer's share EMA still shows open is named, not hidden
        assert "$27.82 is still open in EMA" in got[F.claim_id(32)][3], got[F.claim_id(32)][3]
        for pcn in WORK:
            assert got[pcn][1] == "work_now" and got[pcn][0] == "open", (pcn, got[pcn])
            assert not got[pcn][3].startswith("Already paid"), (pcn, got[pcn])
        dup = got[F.claim_id(39)]
        assert dup[0] == "open" and dup[1] == "done", dup
        assert dup[3].startswith(f"Already paid: EMA posted $90.00 from {BCBS} on "), dup
        assert "duplicate denial" in dup[3] and "the second payer's share of $30.00 is still open in EMA" in dup[3], dup
    finally:
        lg.close()
        s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_the_proof_rows_reconcile_to_the_cent_and_the_story_names_the_payment(tmp_path, kind, monkeypatch):
    """The state's proof rows are the payments themselves (date, payer, amount, check ending), and they add up to the
    amount the decision names; the item's story says the same."""
    from remitwatch import story
    s, lg = _host(tmp_path, kind, monkeypatch)
    try:
        _world(s, lg, tmp_path)
        W.run(s, today=TODAY)
        f = _by_claim(s)[F.claim_id(31)]
        st = states.state_of(s.conn, f, today=TODAY)
        assert st["work_state"] == "done" and st["rule"] == "paid_first_other_payer", st
        pays = [r for r in st["evidence"] if r["kind"] == "payment"]
        assert len(pays) == 1 and pays[0]["date"] == _iso(_svc(31) + dt.timedelta(days=20)), st["evidence"]
        assert pays[0]["cents"] == 9218 == st["proof"]["cents"] and pays[0]["payer"] == MCO and pays[0]["check_tail"] == "0031"
        v = story.item_verdict(s.conn, f["finding_id"], today=TODAY)
        assert v["state"] == "done" and v["sentence"].startswith(f"Already paid by {MCO}"), v
    finally:
        lg.close()
        s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_a_persons_status_is_kept_and_a_newer_pull_without_the_payment_makes_it_work_again(tmp_path, kind, monkeypatch):
    """A person started item 32 before the run: it stays theirs (work now, Started), the payment named beside it. A newer
    full pull of the same dates no longer shows the first payer's payment on claim 31: work again, as before."""
    from remitwatch.actions import apply_status
    s, lg = _host(tmp_path, kind, monkeypatch)
    try:
        _world(s, lg, tmp_path)
        W.run(s, today=TODAY)
        fid = _by_claim(s)[F.claim_id(32)]["finding_id"]
        apply_status(s.conn, fid, "in_progress", notes="calling the plan", actor="biller")
        s.conn.commit()
        W.run(s, today=TODAY)
        got = _states(s)
        assert got[F.claim_id(32)][0] == "in_progress" and got[F.claim_id(32)][1] == "work_now", got[F.claim_id(32)]
        assert "Started by biller" in got[F.claim_id(32)][3] and "Already paid by" in got[F.claim_id(32)][3], got[F.claim_id(32)]
        assert got[F.claim_id(31)][1] == "done"
        # a newer pull of the same dates, without the first payer's payment on claim 31
        _pull(s, lg, tmp_path, drop_first_paid=(31,), carry=D(2026, 5, 27), ar_too=False)
        W.run(s, today=TODAY)
        got = _states(s)
        assert got[F.claim_id(31)][0] == "open" and got[F.claim_id(31)][1] == "work_now", got[F.claim_id(31)]
        assert got[F.claim_id(33)][1] == "done"
    finally:
        lg.close()
        s.close()


def test_which_payer_an_ema_payer_name_is():
    """A company's plan EMA names '<company> Medicaid ...' is that company's, not the state's Medicaid (the name rules'
    catch-all); the state's own program, by any of its names, is the state's; the checks EMA posted under a name win."""
    fx = {"item": {"payer_group": "Medicaid KY", "payer_name": STATE}, "r835": {"rows": [], "plb": []}, "ctx": {}}
    assert states._ema_payer_is_denier(fx, {"payer": MCD}) is True
    assert states._ema_payer_is_denier(fx, {"payer": "Kentucky Medicaid"}) is True
    assert states._ema_payer_is_denier(fx, {"payer": MCO}) is False
    assert states._ema_payer_is_denier(fx, {"payer": MA}) is False
    assert states._ema_payer_is_denier(fx, {"payer": ""}) is None      # nothing names it: counted neither way
    fx["ctx"]["ema_names"] = {MCO: STATE}           # EMA posted the state's 835 checks under that name: it is the state's
    assert states._ema_payer_is_denier(fx, {"payer": MCO}) is True
    fx["ctx"]["ema_names"] = {MCD: "TESTCARE HEALTH PLAN"}
    assert states._ema_payer_is_denier(fx, {"payer": MCD}) is False
