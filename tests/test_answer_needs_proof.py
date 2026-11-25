"""
The answered rule for claims no payer answered (detect._answered_rule and the paths around it), two cases found by hand
traces of real claims, each on the three paths that chase such claims (the 837s, EMA's weekly exports, EMA's reports):

1. A bare $0 row is no answer. A $0 row EMA posts under a check is a payer's $0 answer only when it carries its proof: an
   allowed amount, or the payer's reason (a denial, the contractual write-down, the patient's share) posted on the line
   that day. A batch check can list a claim it does not pay (no reason, no allowed amount, no adjustment, nothing put on
   the patient), and the claim may be paid weeks later or never: it stays listed.
2. An answer is for its own part of the service (the release's T-C1). A claim for the professional reading (26) is
   answered by a 26 or by the whole service; one for the technical part (TC) by a TC or the whole service; one for the
   whole service by the whole service, or by both parts. A payment for the TC on another bill never answers the 26.

Every patient, claim, member number, check, date and amount here is invented (tests/ema_fixtures.py, tests/test_ema_wiring.py,
tests/test_837_sends.py, tests/test_workable.py). Each test runs on the server's store file and on the compact in-memory
store the serverless cycle builds and analyses.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch import ema_checks as ec, ema_ledger as el, ema_reports as er  # noqa: E402
from remitwatch.detect import Detector  # noqa: E402
from remitwatch.store import Store  # noqa: E402
from tests import ema_fixtures as F  # noqa: E402
from tests.test_837_sends import clm837, send  # noqa: E402
from tests.test_ema_wiring import adj, charge, denial_world, pay, remit as raw_remit  # noqa: E402
from tests.test_workable import ema  # noqa: E402

KINDS = ["file", "compact"]
D = dt.date
S_ = D(2026, 3, 2)                  # the visits
S = S_.isoformat()
BC = ("BLUE CROSS TEST", "BCT01")    # the payer the 837s name
BCP = F.PAYERS["bcbs"]              # the same payer as EMA names it
TODAY = "2026-05-03"


def _world(tmp_path, kind, monkeypatch):
    monkeypatch.setattr(er, "CLAIM_HOST_SUFFIX", F.HOST_SUFFIX)
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    monkeypatch.setenv("REMITWATCH_EMA_STRICT", "1")
    for k in ("VERCEL", "REMITWATCH_EMA_REPORTS", "REMITWATCH_EMA_CLAIM_URL", "REMITWATCH_EMA_PAYER_ALIASES", "REMITWATCH_EMA_LEDGER",
              "REMITWATCH_EMA_REPORT_KEEP", "REMITWATCH_EMA_CLOSES"):
        monkeypatch.delenv(k, raising=False)
    ec._LAST_POST.clear()
    s = Store(str(tmp_path / "s.sqlite")) if kind == "file" else Store(":memory:", compact=True)
    er.ensure_schema(s.conn)
    s.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('ema:key_salt', ?)", ("ab" * 32,))
    s.conn.commit()
    return s, el.Ledger.open(str(tmp_path / "ema_ledger.sqlite"))


def _name(n: int) -> str:
    """An invented patient for claim n (each claim its own patient, unless a test shares one)."""
    return f"Prooftest, Pat{chr(65 + n % 26)}{chr(65 + n // 26 % 26)}"


def _last_first(n: int) -> tuple[str, str]:
    last, first = _name(n).split(", ")
    return last.upper(), first.upper()


def _ar(n: int, patient: int, cpt: str, current: float) -> dict:
    """The A/R line that pairs claim n with EMA claim n (its claim id and claim number)."""
    return F.ar_row(claim=F.claim_id(n), link=F.link_no(n), status="Payment Pending", current=current, svc=S_, patient=_name(patient),
                    cpt=cpt)


def _run(s, lg):
    det = Detector(s, run_id="r1", today=TODAY)
    det.run()
    assert det.ema_errors == [], det.ema_errors
    lg.close()
    print({k: v for k, v in det.ema_counts.items() if "answer" in k or "837" in k or k in ("NO_ANSWER", "REJECTED_PRE_PAYER")})
    return det


def _listed(s) -> dict:
    """The open chases (never answered, rejected before the payer), by claim id, else by EMA claim number."""
    return {(r["patient_control_number"] or f"link:{r['ema_link']}"): dict(r) for r in s.query(
        "SELECT finding_id, patient_control_number, ema_link, signal, source, hint, evidence FROM findings "
        "WHERE signal IN ('NO_ANSWER','REJECTED_PRE_PAYER') AND COALESCE(stale,0)=0")}


# --------------------------------------------------------------------------------------- 1. a bare $0 row is no answer

def _zero_rows(base: int, ids: dict) -> list[dict]:
    """EMA's postings for five claims of one kind (the visits March 2, charged March 3), and a batch check of April 10 that
    paid another claim of the payer's (base + 9) $95:
    - ids['bare']: the batch check lists it at $0, with no reason, no allowed amount, no adjustment and nothing put on the
      patient; its $167.22 is still open on May 1 (a placeholder: the claim is not answered);
    - ids['allowed']: $0 on the batch check with the allowed amount ($90) put on the patient and the rest written down by
      contract (CO-45) that day: a real $0 answer;
    - ids['denied']: $0 on the batch check and the payer's reason (CO-16, information missing) that day: a real $0 answer
      (the denial is the payer's answer);
    - ids['paid_later']: $0 on the batch check, then paid $80 on April 24: answered by the payment;
    - ids['patient'] (when given): a bare $0 row, then the patient paid $60 on April 20. Whether the payer put the amount on
      the patient or the patient paid at the visit, the row cannot tell: still no answer, and the item says what the
      patient's side shows."""
    rows = [charge(F.link_no(base + 9), cpt="99212", amount=100, patient=_name(base + 9), posted=D(2026, 3, 3)),
            pay(F.link_no(base + 9), f"BATCH{base}", 95, cpt="99212", patient=_name(base + 9), posted=D(2026, 4, 10))]
    for kind, n in ids.items():
        code, amount = ("99214", 167.22) if kind == "bare" else ("99213", 150)
        rows.append(charge(F.link_no(n), cpt=code, amount=amount, patient=_name(n), posted=D(2026, 3, 3), status="Payment Pending"))
        if kind == "patient":
            rows.append(F.production_row(link=F.link_no(n), cpt=code, pat_paid=60, patient=_name(n), svc=S_, posted=D(2026, 4, 20),
                                         payer="Patient"))
        if kind == "allowed":
            rows.append(pay(F.link_no(n), f"BATCH{base}", 0, cpt=code, patient=_name(n), posted=D(2026, 4, 10), allowed=90))
            rows.append(adj(F.link_no(n), "CO-45", 60, cpt=code, patient=_name(n), posted=D(2026, 4, 10), contractual=True))
        elif kind == "denied":
            rows.append(pay(F.link_no(n), f"BATCH{base}", 0, cpt=code, patient=_name(n), posted=D(2026, 4, 10)))
            rows.append(adj(F.link_no(n), "CO-16", amount, cpt=code, patient=_name(n), posted=D(2026, 4, 10)))
        else:
            rows.append(pay(F.link_no(n), f"BATCH{base}", 0, cpt=code, patient=_name(n), posted=D(2026, 4, 10)))
            if kind == "paid_later":
                rows.append(pay(F.link_no(n), f"PAPER{n}", 80, cpt=code, patient=_name(n), posted=D(2026, 4, 24)))
    return rows


@pytest.mark.parametrize("kind", KINDS)
def test_a_bare_zero_row_does_not_answer_an_837_claim(tmp_path, kind, monkeypatch):
    """Only the 837s know claims 101 to 104 (sent March 4 to Blue Cross; no weekly export lists them, no 835 answers them).
    The A/R pairs each with its EMA claim. EMA's postings (_zero_rows): claim 101's only answer is a bare $0 row on a batch
    check: still listed, and the item says what EMA shows (before: the bare row read as a $0 answer and the claim, still
    pending in EMA with its charge open, fell off every list). Claims 102 ($0 with the allowed amount put on the patient),
    103 ($0 and a denial reason) and 104 (paid after the bare row) are answered: not listed. Claim 105's bare $0 row was
    followed by the patient's $60: listed, its item saying so (the payer may have put the amount on the patient)."""
    s, lg = _world(tmp_path, kind, monkeypatch)
    ids = {"bare": 101, "allowed": 102, "denied": 103, "paid_later": 104, "patient": 105}
    rows = _zero_rows(100, ids)
    ar = [_ar(n, n, "99214" if k == "bare" else "99213", 167.22 if k == "bare" else 150) for k, n in ids.items()]
    denial_world(s, lg, tmp_path, rows, ar_rows=ar)
    send(s, tmp_path, [clm837(F.claim_id(n), [("99214", "167.22", "20260302")] if k == "bare" else [("99213", "150.00", "20260302")],
                              payer=BC, member=f"MEMBER000{n}") for k, n in ids.items()], "20260304")
    _run(s, lg)
    got = _listed(s)
    print(sorted(got))
    mine = {F.claim_id(n) for n in ids.values()} & set(got)
    assert mine == {F.claim_id(101), F.claim_id(105)}, f"listed {sorted(mine)}: only claims 101 and 105 (a bare $0 row) are unanswered"
    f = got[F.claim_id(101)]
    assert f["signal"] == "NO_ANSWER" and f["source"] == "837" and f["ema_link"] == F.link_no(101)
    assert "$0" in (f["hint"] or "") and "no reason, allowed amount or adjustment" in (f["hint"] or ""), f["hint"]
    assert "patient's side" not in f["hint"], f["hint"]
    assert json.loads(f["evidence"])["ema_bare_zero"]["on"] == "2026-04-10"
    g = got[F.claim_id(105)]
    assert "$60.00 paid by the patient or written off on the patient's side of this claim" in (g["hint"] or ""), g["hint"]
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_a_bare_zero_row_does_not_answer_a_claim_emas_weekly_exports_chase(tmp_path, kind, monkeypatch):
    """The same four kinds of claim (111 to 114), sent March 4 to Blue Cross as EMA's weekly exports show (no 837 on file):
    claim 111, whose only answer in EMA is a bare $0 row, is listed; the others are answered and are not."""
    s, lg = _world(tmp_path, kind, monkeypatch)
    ids = {"bare": 111, "allowed": 112, "denied": 113, "paid_later": 114}
    rows = _zero_rows(110, ids)
    ar = [_ar(n, n, "99214" if k == "bare" else "99213", 167.22 if k == "bare" else 150) for k, n in ids.items()]
    denial_world(s, lg, tmp_path, rows, ar_rows=ar)
    c = s.conn
    for k, n in ids.items():
        code, cents = ("99214", 16722) if k == "bare" else ("99213", 15000)
        ema(c, F.claim_id(n), cpt=code, service_date=S, bill_created="2026-03-03", charges_cents=cents, payer=BCP, payer_id="BCT01",
            patient_id=f"EMA{n}")
        ema(c, F.claim_id(n), family="claim_status", claim_status="Submitted", claim_submission_date="2026-03-04", cpt=code,
            service_date=S, payer=BCP, payer_id="BCT01", patient_id=f"EMA{n}")
    c.commit()
    _run(s, lg)
    got = _listed(s)
    print(sorted(got))
    mine = {F.claim_id(n) for n in ids.values()} & set(got)
    assert mine == {F.claim_id(111)}, f"listed {sorted(mine)}: only claim 111 (a bare $0 row) is unanswered"
    f = got[F.claim_id(111)]
    assert f["signal"] == "NO_ANSWER" and f["ema_link"] == F.link_no(111)
    # listed by the weekly exports' path, dated by its send (not only by the reports' check, which keys it alike)
    assert json.loads(f["evidence"]).get("sent_basis"), f["evidence"]
    assert "no reason, allowed amount or adjustment" in (f["hint"] or ""), f["hint"]
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_a_bare_zero_row_does_not_answer_a_claim_emas_reports_chase(tmp_path, kind, monkeypatch):
    """EMA's Production report alone knows EMA claims 121 to 124 (no claim id, no export, no 837): the same four kinds. EMA
    claim 121, whose only answer is a bare $0 row, is listed by the reports' check (before: any $0 row under a check read as
    an answer, so the check never listed it); the others are answered and are not."""
    s, lg = _world(tmp_path, kind, monkeypatch)
    ids = {"bare": 121, "allowed": 122, "denied": 123, "paid_later": 124}
    denial_world(s, lg, tmp_path, _zero_rows(120, ids))
    _run(s, lg)
    got = _listed(s)
    print(sorted(got))
    mine = {k for k in got if k in {f"link:{F.link_no(n)}" for n in ids.values()}}
    assert mine == {f"link:{F.link_no(121)}"}, f"listed {sorted(mine)}: only EMA claim 121 (a bare $0 row) is unanswered"
    f = got[f"link:{F.link_no(121)}"]
    assert f["signal"] == "NO_ANSWER" and "no reason, allowed amount or adjustment" in (f["hint"] or ""), f["hint"]
    s.close()


def test_claim_answers_needs_the_proof_of_a_zero_answer(tmp_path, monkeypatch):
    """ema_checks.claim_answers, the bridge every never-answered path uses: a bare $0 row after the send answers nothing;
    a $0 row with an allowed amount, or with the payer's reason posted that day, is a $0 answer."""
    s, lg = _world(tmp_path, "file", monkeypatch)
    ids = {"bare": 131, "allowed": 132, "denied": 133, "paid_later": 134}
    denial_world(s, lg, tmp_path, _zero_rows(130, ids))
    lg.close()
    claims = []
    for k, n in ids.items():
        s.conn.execute("INSERT INTO ema_claim_links (claim_id, link, bill, first_seen, last_seen) VALUES (?,?,?,?,?)",
                       (F.claim_id(n), F.link_no(n), F.bill_id(n), "2026-04-01", "2026-04-01"))
        claims.append({"claim_id": F.claim_id(n), "sent": "2026-03-04", "payer_group": "Anthem / BCBS",
                       "lines": [(S, "99214", 16722)] if k == "bare" else [(S, "99213", 15000)]})
    s.conn.commit()
    got = ec.claim_answers(s.conn, claims)
    assert not got[F.claim_id(131)]["answered"], got[F.claim_id(131)]
    assert got[F.claim_id(131)]["bare_zero"]["on"] == "2026-04-10"         # what EMA shows, for the item
    assert (got[F.claim_id(132)]["answered"], got[F.claim_id(132)]["how"]) == (True, "zero_check")
    assert got[F.claim_id(133)]["answered"] and got[F.claim_id(133)]["how"] in ("zero_check", "carc")
    assert (got[F.claim_id(134)]["answered"], got[F.claim_id(134)]["how"], got[F.claim_id(134)]["paid_cents"]) == (True, "paid", 8000)
    s.close()


# ------------------------------------------------------------------------------- 2. an answer is for its own part

def _parts_rows() -> list[dict]:
    """EMA's postings for the pathology claims below (88305 on March 2; Blue Cross): each claim's charge, and Blue Cross's
    payments of the other claims on April 10."""
    out = []
    for n, amount, patient, paid in ((151, 34.14, 151, None), (152, 31.19, 151, 24.45),     # 26 open; the same patient's TC paid
                                     (153, 65.08, 153, None), (154, 31.19, 153, 24.45),     # whole open; only the TC paid
                                     (155, 65.08, 155, None), (156, 34.14, 155, 27.31), (157, 31.19, 155, 24.45),  # both parts paid
                                     (158, 34.14, 158, None), (159, 65.08, 158, 52.06)):    # 26 open; the whole service paid
        out.append(charge(F.link_no(n), cpt="88305", amount=amount, patient=_name(patient), posted=D(2026, 3, 3), status="Payment Pending"))
        if paid:
            out.append(pay(F.link_no(n), f"PAPER{n}", paid, cpt="88305", patient=_name(patient), posted=D(2026, 4, 10)))
    return out


_PARTS = {151: "26", 152: "TC", 153: "", 154: "TC", 155: "", 156: "26", 157: "TC", 158: "26", 159: ""}
_CHARGE = {"26": "34.14", "TC": "31.19", "": "65.08"}


@pytest.mark.parametrize("kind", KINDS)
def test_an_837_claim_is_answered_only_for_its_own_part(tmp_path, kind, monkeypatch):
    """The 837s show claims 151 to 159 sent March 4 to Blue Cross, each for 88305 (the part each bills in _PARTS); the A/R
    pairs each with its EMA claim; EMA shows the other claims paid April 10 (_parts_rows). Listed:
    - claim 151, the professional reading (26), never answered: the same patient's TC paid on claim 152 is not its answer
      (before: the TC payment on another bill answered it, date and code alike);
    - claim 153, the whole service, never answered: only the TC is paid (claim 154), the reading is still owed.
    Not listed: claim 155 (whole service; both parts paid, 156 and 157), claim 158 (26; the whole service paid on 159), and
    the paid claims themselves.
    And on the remittances, for the patients of claims 160 and 162 (88305-26, never answered): the TC paid under another bill
    of the same member (claim 161), and the TC answered as primary under another bill with another member number but the
    same name (claim 163). Neither answers the reading: both are listed."""
    s, lg = _world(tmp_path, kind, monkeypatch)
    rows = _parts_rows() + [charge(F.link_no(n), cpt="88305", amount=34.14, patient=_name(n), posted=D(2026, 3, 3),
                                   status="Payment Pending") for n in (160, 162)]
    ar = [_ar(n, n if n not in (152, 154, 156, 157, 159) else {152: 151, 154: 153, 156: 155, 157: 155, 159: 158}[n], "88305",
              float(_CHARGE[p])) for n, p in _PARTS.items()] + [_ar(n, n, "88305", 34.14) for n in (160, 162)]
    denial_world(s, lg, tmp_path, rows, ar_rows=ar)
    owner = {152: 151, 154: 153, 156: 155, 157: 155, 159: 158}
    send(s, tmp_path, [clm837(F.claim_id(n), [("88305", _CHARGE[p], "20260302", p)], payer=BC, member=f"MEMBER000{owner.get(n, n)}")
                       for n, p in _PARTS.items()]
         + [clm837(F.claim_id(n), [("88305", "34.14", "20260302", "26")], payer=BC, member=f"MEMBER000{n}") for n in (160, 162)],
         "20260304")
    # the TCs on the remittances: claim 161 under claim 160's member number; claim 163 under another member number, same name
    for n, member, patient in ((161, "MEMBER000160", 160), (163, "MEMBER000999", 162)):
        last, first = _last_first(patient)
        raw_remit(s, trace=f"77716{n}", paid_on="2026-03-26", claims=[{
            "pcn": F.claim_id(n), "status": "1", "member": member, "last": last, "first": first,
            "lines": [(S, "88305", 31.19, 24.45, [("CO", "45", 6.74)], {"modifiers": json.dumps(["TC"])})]}])
    _run(s, lg)
    got = _listed(s)
    print(sorted(got))
    mine = {F.claim_id(n) for n in list(_PARTS) + [160, 162]} & set(got)
    assert mine == {F.claim_id(n) for n in (151, 153, 160, 162)}, (
        f"listed {sorted(mine)}: 151, 160 and 162 bill the reading (26), whose answer is not the TC's; 153 bills the whole "
        f"service, of which only the TC was paid")
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_an_ema_claim_whose_part_nothing_names_answers_the_part_it_is_charged_for(tmp_path, kind, monkeypatch):
    """Claims 166 and 168 (88305-TC at $31.19, the 837s show them sent March 4 to Blue Cross; no A/R pair, and another
    patient's EMA claim of the same day, code and charge, so no bridge finds their own EMA claims). By the patients' names
    (from their other claims' remittances), EMA holds for each one EMA claim of the day for 88305 that nothing names the part
    of (no claim id, 837, remittance or export ties it), paid by Blue Cross April 10:
    - for claim 166, charged $31.19, the TC's own fee: it is the TC (the claim's own EMA claim), paid: not listed;
    - for claim 168, charged $34.14, the reading's fee: it may be the other part, and the TC is still owed: listed."""
    s, lg = _world(tmp_path, kind, monkeypatch)
    rows = []
    for n, chg, paid in ((166, 31.19, 24.45), (168, 34.14, 27.31)):
        rows += [charge(F.link_no(n), cpt="88305", amount=chg, patient=_name(n), posted=S_),
                 pay(F.link_no(n), f"PAPER{n}", paid, cpt="88305", patient=_name(n), posted=D(2026, 4, 10)),
                 # another patient's EMA claim of the same day, code and charge as the 837 claim: no content bridge
                 charge(F.link_no(n + 100), cpt="88305", amount=31.19, patient=_name(n + 100), posted=S_, status="Payment Pending")]
    denial_world(s, lg, tmp_path, rows)
    # the patients' other visits, paid on April 20: their names, and remittances that reach the claims' due days
    for n in (166, 168):
        last, first = _last_first(n)
        raw_remit(s, trace=f"77716{n}9", paid_on="2026-04-20", claims=[{
            "pcn": F.claim_id(n + 1), "status": "1", "member": f"MEMBER000{n}", "last": last, "first": first,
            "lines": [("2026-02-02", "99213", 150, 90, [("CO", "45", 60)])]}])
    send(s, tmp_path, [clm837(F.claim_id(n), [("88305", "31.19", "20260302", "TC")], payer=BC, member=f"MEMBER000{n}") for n in (166, 168)],
         "20260304")
    _run(s, lg)
    got = _listed(s)
    print(sorted(got))
    mine = {F.claim_id(n) for n in (166, 168)} & set(got)
    assert mine == {F.claim_id(168)}, f"listed {sorted(mine)}: claim 166's TC is paid on its own EMA claim; 168's is not"
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_a_claim_emas_weekly_exports_chase_is_answered_only_for_its_own_part(tmp_path, kind, monkeypatch):
    """EMA's weekly exports show claims 171 to 175 sent March 4 to Blue Cross for 88305 (no 837 on file), the parts in their
    Modifiers column (EMA's JSON list):
    - claim 171 (26) never answered; EMA's patient's claim 172 (TC) shows Blue Cross's payment on the export's charge line:
      the TC's payment is not the reading's, 171 is listed (before: the visit read as paid under another bill);
    - claim 173 (26) never answered; the same patient's TC is paid in EMA's Production report on EMA claim 174: listed;
    - claim 175 (whole service) never answered; the same EMA patient's claim 176 (26) paid on the export: listed."""
    s, lg = _world(tmp_path, kind, monkeypatch)
    plan = {171: ("26", "EMA171", 171), 172: ("TC", "EMA171", 171), 173: ("26", "EMA173", 173), 174: ("TC", "EMA173", 173),
            175: ("", "EMA175", 175), 176: ("26", "EMA175", 175)}
    rows = [charge(F.link_no(n), cpt="88305", amount=float(_CHARGE[p]), patient=_name(pt), posted=D(2026, 3, 3), status="Payment Pending")
            for n, (p, _e, pt) in plan.items()]
    # Blue Cross's payments as EMA's Production report shows them (the exports show 172's and 176's too)
    rows += [pay(F.link_no(n), f"PAPER{n}", paid, cpt="88305", patient=_name(plan[n][2]), posted=D(2026, 4, 10))
             for n, paid in ((172, 24.45), (174, 24.45), (176, 27.31))]
    ar = [_ar(n, pt, "88305", float(_CHARGE[p])) for n, (p, _e, pt) in plan.items()]
    denial_world(s, lg, tmp_path, rows, ar_rows=ar)
    c = s.conn
    for n, (p, pid, _pt) in plan.items():
        paid = {172: 2445, 176: 2731}.get(n)
        ema(c, F.claim_id(n), cpt="88305", modifiers=json.dumps([p]) if p else None, service_date=S, bill_created="2026-03-03",
            charges_cents=int(round(float(_CHARGE[p]) * 100)), payer=BCP, payer_id="BCT01", patient_id=pid,
            **({"insurance_payments_cents": paid, "payments_cents": paid} if paid else {}))
        ema(c, F.claim_id(n), family="claim_status", claim_status="Submitted", claim_submission_date="2026-03-04", cpt="88305",
            service_date=S, payer=BCP, payer_id="BCT01", patient_id=pid)
    c.commit()
    _run(s, lg)
    got = _listed(s)
    print(sorted(got))
    mine = {F.claim_id(n) for n in plan} & set(got)
    assert mine == {F.claim_id(n) for n in (171, 173, 175)}, (
        f"listed {sorted(mine)}: each of 171, 173 and 175 was paid only for another part of the service")
    # each listed by the weekly exports' path, dated by its send (not only by the reports' check, which keys it alike)
    assert all(json.loads(got[F.claim_id(n)]["evidence"]).get("sent_basis") for n in (171, 173, 175)), (
        {n: got[F.claim_id(n)]["evidence"] for n in (171, 173, 175)})
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_a_claim_emas_reports_chase_is_answered_only_for_its_own_part(tmp_path, kind, monkeypatch):
    """EMA's Production report alone knows EMA claim 181 (88305, no claim id: its part is not named anywhere, so it is read
    as the whole service), never answered. The same patient's TC was paid on April 10 on EMA claim 182, whose claim id the
    A/R names and whose remittance (March 26) shows the TC: only the TC is paid, so EMA claim 181 is listed (before: the
    reports' check and the answered rule both took the TC's payment as the visit's). EMA claim 183, the same, with the
    whole service paid on EMA claim 184, is answered: not listed."""
    s, lg = _world(tmp_path, kind, monkeypatch)
    rows = [charge(F.link_no(181), cpt="88305", amount=65.08, patient=_name(181), posted=D(2026, 3, 3), status="Payment Pending"),
            charge(F.link_no(182), cpt="88305", amount=31.19, patient=_name(181), posted=D(2026, 3, 3)),
            pay(F.link_no(182), "777181182", 24.45, cpt="88305", patient=_name(181), posted=D(2026, 4, 10)),
            charge(F.link_no(183), cpt="88305", amount=65.08, patient=_name(183), posted=D(2026, 3, 3), status="Payment Pending"),
            charge(F.link_no(184), cpt="88305", amount=65.08, patient=_name(183), posted=D(2026, 3, 3)),
            pay(F.link_no(184), "777183184", 52.06, cpt="88305", patient=_name(183), posted=D(2026, 4, 10))]
    ar = [_ar(182, 181, "88305", 0), _ar(184, 183, "88305", 0)]
    denial_world(s, lg, tmp_path, rows, ar_rows=ar)
    for n, patient, part, chg, paid in ((182, 181, "TC", 31.19, 24.45), (184, 183, "", 65.08, 52.06)):
        last, first = _last_first(patient)
        raw_remit(s, trace=f"777{patient}{n}", paid_on="2026-03-26", claims=[{
            "pcn": F.claim_id(n), "status": "1", "member": f"MEMBER000{patient}", "last": last, "first": first,
            "lines": [(S, "88305", chg, paid, [("CO", "45", round(chg - paid, 2))], {"modifiers": json.dumps([part] if part else [])})]}])
    _run(s, lg)
    got = _listed(s)
    print(sorted(got))
    mine = {k for k in got if k in {f"link:{F.link_no(n)}" for n in (181, 183)}}
    assert mine == {f"link:{F.link_no(181)}"}, f"listed {sorted(mine)}: only the TC of EMA claim 181's visit was paid"
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_a_claim_that_never_left_ema_is_not_paid_by_the_other_part(tmp_path, kind, monkeypatch):
    """EMA's A/R shows claim 187 (88305-26, the reading; EMA's weekly export names its part) waiting in EMA's print batch
    since April 1, and the same patient's TC (claim 188) paid by Blue Cross on April 20. The TC's payment is not the
    reading's: claim 187 never left EMA, and is listed (before: every line read as paid on another of EMA's claims)."""
    from tests.test_ema_wiring import load
    s, lg = _world(tmp_path, kind, monkeypatch)
    A1 = D(2026, 4, 1)
    rows = [charge(F.link_no(187), cpt="88305", amount=34.14, patient=_name(187), svc=A1, posted=A1, status="Batched for Printing"),
            charge(F.link_no(188), cpt="88305", amount=31.19, patient=_name(187), svc=A1, posted=A1),
            pay(F.link_no(188), "PAPER188", 24.45, cpt="88305", patient=_name(187), svc=A1, posted=D(2026, 4, 20))]
    ar = [F.ar_row(claim=F.claim_id(187), link=F.link_no(187), status="Batched for Printing", svc=A1, aging=None, current=34.14,
                   cpt="88305", patient=_name(187)),
          F.ar_row(claim=F.claim_id(188), link=F.link_no(188), status="Payment Pending", svc=A1, aging=None, current=0,
                   cpt="88305", patient=_name(187))]
    load(s, lg, tmp_path, rows, ar)
    lg.close()
    for n, p, cents in ((187, "26", 3414), (188, "TC", 3119)):
        ema(s.conn, F.claim_id(n), cpt="88305", modifiers=json.dumps([p]), service_date=A1.isoformat(), bill_created="2026-04-01",
            charges_cents=cents, payer=BCP, payer_id="BCT01", patient_id="EMA187")
    s.conn.commit()
    items = ec.not_sent(s.conn, today="2026-04-27", rate=lambda g, c, y: 3000)
    assert [i["key_parts"][0] for i in items] == [F.claim_id(187)], items
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_a_zero_row_with_a_remark_code_that_day_is_an_answer_and_a_status_needs_a_real_one(tmp_path, kind, monkeypatch):
    """A $0 row with the payer's remark code posted that day (EMA's report holds a RARC as an adjustment kind of its own) is
    a $0 answer. And EMA's 'Resolved/Other' reads as settled only when an insurance answer is on file: with only a bare $0
    row it was given up (ema_checks.claim_statuses)."""
    s, lg = _world(tmp_path, kind, monkeypatch)
    rows = [charge(F.link_no(191), patient=_name(191), posted=D(2026, 3, 3), status="Payment Pending"),
            pay(F.link_no(191), "BATCH191", 0, patient=_name(191), posted=D(2026, 4, 10)),
            F.production_row(link=F.link_no(191), cpt="99213", reason="N130", patient=_name(191), svc=S_, posted=D(2026, 4, 10),
                             payer=BCP),
            charge(F.link_no(192), patient=_name(192), posted=D(2026, 3, 3), status="Resolved/Other"),
            pay(F.link_no(192), "BATCH192", 0, patient=_name(192), posted=D(2026, 4, 10), status="Resolved/Other"),
            charge(F.link_no(193), patient=_name(193), posted=D(2026, 3, 3), status="Resolved/Other"),
            pay(F.link_no(193), "BATCH193", 0, patient=_name(193), posted=D(2026, 4, 10), status="Resolved/Other", allowed=90)]
    denial_world(s, lg, tmp_path, rows)
    lg.close()
    s.conn.execute("INSERT INTO ema_claim_links (claim_id, link, bill, first_seen, last_seen) VALUES (?,?,?,?,?)",
                   (F.claim_id(191), F.link_no(191), F.bill_id(191), "2026-04-01", "2026-04-01"))
    s.conn.commit()
    got = ec.claim_answers(s.conn, [{"claim_id": F.claim_id(191), "sent": "2026-03-04", "payer_group": "Anthem / BCBS",
                                     "lines": [(S, "99213", 15000)]}])[F.claim_id(191)]
    assert (got["answered"], got["how"]) == (True, "zero_check"), got
    st = ec.claim_statuses(s.conn, links=[F.link_no(192), F.link_no(193)])
    assert st[F.link_no(192)]["class"] == "gave_up" and st[F.link_no(193)]["class"] == "settled", st
    s.close()


def test_lines_with_only_a_bare_zero_row_are_not_answered_lines_of_a_payers_policy(tmp_path, monkeypatch):
    """C5's measure of a code a payer never pays (at least 20 answered lines, 5% or less paid) counts a line as answered
    only when the payer answered it: 2 EMA-only denials of 77600 by the VA's network and 19 lines on which it posted only a
    bare $0 row are 2 answered lines, too few to call the code the payer's policy (before: 21 answered, none paid)."""
    s, lg = _world(tmp_path, "file", monkeypatch)
    va = F.PAYERS["va"]
    rows = []
    for n in range(21):
        kw = dict(patient=n % 4, payer=va, primary=va, svc=D(2026, 2, 2) + dt.timedelta(days=n))
        rows.append(charge(F.link_no(600 + n), cpt="77600", amount=300, **kw))
        if n < 2:
            rows.append(adj(F.link_no(600 + n), "CO-96", 300, cpt="77600", posted=D(2026, 3, 10) + dt.timedelta(days=n), **kw))
        else:
            rows.append(pay(F.link_no(600 + n), f"VABATCH{n:02d}", 0, cpt="77600", posted=D(2026, 3, 10) + dt.timedelta(days=n), **kw))
    denial_world(s, lg, tmp_path, rows)
    _run(s, lg)
    lo = json.loads(s.conn.execute("SELECT value FROM meta WHERE key='ema:c5_left_out'").fetchone()[0])
    assert "VA Community Care|77600||payer_policy" not in ((lo.get("payer_policy") or {}).get("patterns") or {}), lo
    assert len(s.query("SELECT 1 FROM findings WHERE signal='EMA_DENIED' AND procedure_code='77600'")) == 2
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_a_bare_zero_row_is_not_the_primarys_answer_to_a_claim_only_a_secondary_answered(tmp_path, kind, monkeypatch):
    """Blue Cross denied claims 195 and 196 as primary because another payer covers them first (CO-22), then processed them
    as secondary paying $20; the 837s show both sent March 4 and no other payer's remittance is on file. EMA shows, on each
    EMA claim, a $0 row by Medicare on March 20: for claim 195 a bare one (no reason, allowed amount or adjustment), for
    claim 196 with Medicare's allowed amount. Medicare answered claim 196 (nothing to chase); claim 195's primary answer is
    still missing: listed (before: the bare row read as the primary's answer)."""
    s, lg = _world(tmp_path, kind, monkeypatch)
    for n in (195, 196):
        last, first = _last_first(n)
        for trace, on, status, paid, cas in ((f"77719{n}1", "2026-03-12", "4", 0, [("CO", "22", 150)]),
                                              (f"77719{n}2", "2026-03-26", "2", 20, [("OA", "23", 130)])):
            raw_remit(s, trace=trace, paid_on=on, claims=[{"pcn": F.claim_id(n), "status": status, "member": f"MEMBER000{n}",
                                                           "last": last, "first": first, "lines": [(S, "99213", 150, paid, cas)]}])
    rows = []
    for n, extra in ((195, {}), (196, {"allowed": 80})):
        rows += [charge(F.link_no(n), patient=_name(n), posted=D(2026, 3, 3)),
                 pay(F.link_no(n), f"MCARE{n}", 0, patient=_name(n), posted=D(2026, 3, 20), payer=F.PAYERS["medicare"], **extra),
                 pay(F.link_no(n), f"77719{n}2", 20, patient=_name(n), posted=D(2026, 3, 27))]
    # Blue Cross's answers in EMA reach past April 1 (another claim's payment): its claims due by then are judged
    rows += [charge(F.link_no(197), cpt="11102", amount=120, patient=_name(197), posted=D(2026, 3, 3)),
             pay(F.link_no(197), "PAPER197", 60, cpt="11102", patient=_name(197), posted=D(2026, 4, 10))]
    denial_world(s, lg, tmp_path, rows, ar_rows=[_ar(n, n, "99213", 130) for n in (195, 196)])
    send(s, tmp_path, [clm837(F.claim_id(n), [("99213", "150.00", "20260302")], payer=BC, member=f"MEMBER000{n}") for n in (195, 196)],
         "20260304")
    _run(s, lg)
    got = _listed(s)
    print(sorted(got))
    mine = {F.claim_id(n) for n in (195, 196)} & set(got)
    assert mine == {F.claim_id(195)}, f"listed {sorted(mine)}: only claim 195's primary answer is missing"
    s.close()
