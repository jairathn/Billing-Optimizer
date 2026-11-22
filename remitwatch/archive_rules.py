"""
Archive rules: what the 835 archive alone says about the run's items, before they are saved.

These are the rule gaps that need no EMA data (amendment section 7; the why-missed lens). Each rule is a pure function
over a SQLite connection and the Detector's in-memory items (the finding dicts of Detector.findings, before save), and
returns actions with proof rows. Nothing here writes to the store or changes an item: the detection wiring applies the
actions (docs: the needs file of unit B2 names the hook for each rule).

Actions (every one carries 'action', 'rule', 'finding_id' where it concerns an item, 'strength' and 'proof'):
  reclassify        the item stays listed, but it is not what its signal says: a new label, sentence, size and a state
                    hint ('reclass', 'label', 'hint', 'shortfall_cents', 'state_hint')
  close_with_proof  the archive proves the money was paid: what Detector._mark_paid needs ('paid_date', 'paid_cents',
                    'note', 'detail'), used only where a standing payment proves it
  fold_into         a repeat listing of one service: fold it into the carrier ('into')
  raise_item        a line no rule lists today: a proto-finding for Detector._emit ('signal', 'category', 'line_key', ...)
  hide_match        an item a hide rule at payer x code x modifier x reason grain covers ('hide_rule')
  expected_cents    a fair expected amount where the item has none ('expected_cents', 'basis')

Proof rows have the shape of a story row (remitwatch.story): {'date', 'source': '835', 'kind', 'payer', 'amount_cents',
'codes': [{'code', 'meaning'}], 'text', 'decisive'} plus the keys that find the row again ('line_key', 'claim_key',
'trace'). They stay in the store (an item's evidence); nothing here logs them.

The rules (in the order the wiring applies them):
  1 same_payer_paid_before   a denial or short pay on a line the same payer had already paid: reclassify, never close
  2 other_payer_paid_first   a payer's denial after another payer paid the line: not a recoverable denial; who paid
  3 units_split              a denial or short pay that is a units split across lines of one code
  4 short_pay_topped_up      a short payment the same payer later topped up is not short
  5 same_day_answers         two answers on one day for one line: a reversal and its re-adjudication, a claim the payer
                             split (MA15), a payment on another remittance the same day
  6 second_payer_share       a secondary's payment under half the expected is the secondary's share, not an underpayment
  7 fold_repeat_denials      one item per service and reason; repeat denials fold into the newest
  8 pr_group_denials         PR denials that are not deductible, coinsurance or copay (PR-288, PR-27, PR-49, PR-204,
                             PR-200, PR-26) and CO-147: raise the item the archive shows
  9 hide_matches             hide rules at payer x code x modifier x reason grain
  10 drug_expected           a fair expected amount for drugs and skin substitutes from the payer's own paid lines

Efficiency (tests/test_efficiency.py): the compact serverless store has almost no secondary indexes. Every query here is
one pass over a table, or a lookup by a primary key or by one of the compact store's two indexes (lines by claim, claims
by claim id), driven from a temporary key table. No statement looks a table up once per row of another. SQL stays
within SQLite 3.40 and never names an outer table inside a correlated subquery's ORDER BY.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter, defaultdict
from typing import Any, Callable, Iterable, Mapping, Sequence

from .codes import EM_CODES, carc_text, code_category, normalize_payer, rarc_text

CHUNK = 500
PAYMENT_STATUSES = ("1", "2", "3", "19", "20", "21")
PRIMARY_STATUSES = ("1", "19")
SECONDARY_STATUSES = ("2", "3", "20", "21")
REVERSAL_STATUS = "22"
DENIED_STATUS = "4"
# what the patient owes because of the plan's cost sharing: never a denial
COST_SHARE_CARCS = {"1", "2", "3"}

_EMA_CLAIM_ID = re.compile(r"([A-Z]{2}\d{2}[A-Z0-9]{5,6})C\d{0,3}")


def bill_of(pcn: str | None) -> str:
    """The bill a claim id belongs to: the same rule as remitwatch.detect.bill_of (tests pin that the two agree). Kept here
    so this module never imports detect.py, which calls it."""
    m = _EMA_CLAIM_ID.fullmatch(pcn or "")
    return m.group(1) if m else (pcn or "")


def _usd(cents: Any) -> str:
    c = int(cents or 0)
    return f"-${abs(c) / 100:,.2f}" if c < 0 else f"${c / 100:,.2f}"


def _ev(f: Mapping) -> dict:
    ev = f.get("evidence")
    if isinstance(ev, dict):
        return ev
    try:
        return json.loads(ev) if ev else {}
    except (TypeError, ValueError):
        return {}


def _chunks(seq: Sequence, n: int = CHUNK):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _payer_key(payer_id: Any, payer_name: Any) -> str:
    """Who answered, as detect.py compares payers: the payer id, else the payer's name."""
    return str(payer_id or payer_name or "")


def _family(payer_name: Any) -> str:
    """A payer's company, for "the same payer" when the ids differ (a plan's own id on one remittance, its parent's on
    another): the first word of its payer group, or its own name for a payer the rules do not know."""
    grp, kind = normalize_payer(str(payer_name or ""))
    if kind == "other" or grp in ("Medicare Supplement carriers", "Self-funded plan TPAs"):
        return "own:" + " ".join(re.findall(r"[A-Z0-9]+", str(payer_name or "").upper()))
    return grp.split(" ")[0].lower()


def _code_meaning(group: str | None, code: str | None) -> dict:
    g = (group or "").upper()
    return {"code": f"{g}-{code}" if g else str(code or ""), "meaning": carc_text(code)}


# ----------------------------------------------------------------------------------------------------- the archive view
class Ans:
    """One 835 service line on one of the items' bills: one payer's answer to one line of one submission."""
    __slots__ = ("line_key", "claim_key", "txn", "pcn", "bill", "code", "orig", "mods", "charge", "paid", "allowed", "units",
                 "orig_units", "dos", "status", "payer", "payer_name", "paid_on", "trace", "lcn", "adj", "rem", "standing", "claim")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    @property
    def payment(self) -> bool:
        return self.status in PAYMENT_STATUSES and (self.paid or 0) > 0

    @property
    def unit_count(self) -> float:
        return abs(self.units or 1) or 1

    @property
    def billed_units(self) -> float:
        return abs(self.orig_units or self.units or 1) or 1

    def carcs(self, groups: Iterable[str] = ("CO", "OA", "PI", "PR", "CR")) -> list[tuple[str, str, int]]:
        g = set(groups)
        return [(a, b, c) for a, b, c in (self.adj or []) if a in g]

    def pr_cents(self, codes: Iterable[str] | None = None) -> int:
        want = set(codes) if codes else None
        return sum(c for g, r, c in (self.adj or []) if g == "PR" and (want is None or r in want))

    def amount_of(self, group: str, reason: str) -> int:
        return sum(c for g, r, c in (self.adj or []) if g == group and r == reason)


class Claim:
    """One 835 claim (CLP) on an item's bill, with its remittance's date, trace and payer."""
    __slots__ = ("claim_key", "txn", "pcn", "bill", "status", "charge", "paid", "patient_resp", "pccn", "freq", "crossover",
                 "payer", "payer_name", "paid_on", "trace", "adj", "rem", "lines", "original_ref", "patient")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))


class Archive:
    """Every 835 answer on the bills of a set of items, keyed for the rules: by line, by claim, and by service (bill,
    code, date of service; an answer whose payer changed the code is filed under the billed code too)."""

    def __init__(self):
        self.lines: dict[str, Ans] = {}
        self.claims: dict[str, Claim] = {}
        self.by_service: dict[tuple, list[Ans]] = defaultdict(list)
        self.by_bill: dict[str, list[Claim]] = defaultdict(list)

    def service(self, bill: str, code: str | None, dos: str | None) -> list[Ans]:
        return self.by_service.get((bill, code, dos), []) if code and dos else []

    def answers_for(self, f: Mapping) -> list[Ans]:
        """Every answer on the item's service: its bill, its code (the billed one too, where the payer changed it) and its
        date of service, oldest first."""
        bill = bill_of(f.get("patient_control_number"))
        dos = f.get("service_date")
        out: dict[str, Ans] = {}
        for code in dict.fromkeys(c for c in (f.get("procedure_code"), f.get("original_procedure_code"), f.get("billed_code")) if c):
            for a in self.service(bill, code, dos):
                out[a.line_key] = a
        return sorted(out.values(), key=lambda a: (a.paid_on or "", a.line_key))


def load_archive(conn: sqlite3.Connection, items: Iterable[Mapping], *, standing: Mapping[str, int] | None = None) -> Archive:
    """The archive view for these items: every claim on their bills (claims whose id has the same bill, so every
    submission of it, whoever answered it), their service lines, adjustments and remarks, and the remittances' dates,
    traces and payers. Superseded remittances are left out. `standing` is the Detector's ledger of payments still
    standing once takebacks are applied (Detector.standing, {line_key: cents}); without it the ledger is worked out
    here for these bills alone (reversals paired as detect.py pairs them; claim-level takebacks are not seen)."""
    arch = Archive()
    bills = sorted({bill_of(f.get("patient_control_number")) for f in items
                    if f.get("patient_control_number") and (f.get("line_key") or f.get("claim_key"))} - {""})
    if not bills:
        return arch
    conn.execute("DROP TABLE IF EXISTS temp.b2_bills")
    conn.execute("DROP TABLE IF EXISTS temp.b2_claims")
    conn.execute("CREATE TEMP TABLE b2_bills (bill TEXT PRIMARY KEY, hi TEXT)")
    conn.execute("CREATE TEMP TABLE b2_claims (claim_key TEXT PRIMARY KEY)")
    try:
        conn.executemany("INSERT OR IGNORE INTO temp.b2_bills VALUES (?, ?)", [(b, b + "D") for b in bills])
        # every claim id from the bill up to the bill followed by 'D': the bill itself and its submissions (bill + 'C' + digits)
        cur = conn.execute("""SELECT c.claim_key, c.transaction_key, c.patient_control_number, c.claim_status_code, c.charge_cents, c.paid_cents,
                                     c.patient_responsibility_cents, c.payer_claim_control_number, c.claim_frequency_code, c.crossover_carrier_name,
                                     c.original_reference_number, c.payment_date, c.patient_id, c.patient_last, c.patient_first,
                                     t.payment_date, t.trace_number, t.payer_name, t.payer_id
                              FROM temp.b2_bills b
                              JOIN claims c ON c.patient_control_number >= b.bill AND c.patient_control_number < b.hi
                              JOIN transactions t ON t.transaction_key = c.transaction_key
                              WHERE COALESCE(t.superseded, 0) = 0""")
        keep = []
        wanted = set(bills)
        for (ck, txn, pcn, st, charge, paid, pr, pccn, freq, cross, oref, cpay, pid, plast, pfirst, tpay, trace, pname, pid_) in cur:
            b = bill_of(pcn)
            if b not in wanted:
                continue                # an id that only shares the bill's first characters
            cl = Claim(claim_key=ck, txn=txn, pcn=pcn, bill=b, status=st, charge=charge, paid=paid, patient_resp=pr, pccn=pccn,
                       freq=freq, crossover=cross, original_ref=oref, payer=_payer_key(pid_, pname), payer_name=pname,
                       paid_on=cpay or tpay or "", trace=trace, adj=[], rem=[], lines=[],
                       patient=(pid or "", (plast or "").upper(), (pfirst or "").upper()))
            arch.claims[ck] = cl
            arch.by_bill[b].append(cl)
            keep.append((ck,))
        conn.executemany("INSERT OR IGNORE INTO temp.b2_claims VALUES (?)", keep)
        cur = conn.execute("""SELECT l.line_key, l.claim_key, l.procedure_code, l.original_procedure_code, l.modifiers, l.charge_cents,
                                     l.paid_cents, l.allowed_cents, l.paid_units, l.original_units,
                                     COALESCE(l.service_date, CASE WHEN l.service_period_start = COALESCE(l.service_period_end, l.service_period_start)
                                                                   THEN l.service_period_start END),
                                     l.line_control_number, l.claim_status_code, l.payer_name, l.payer_id, l.payment_date
                              FROM temp.b2_claims k JOIN lines l ON l.claim_key = k.claim_key""")
        for (lk, ck, code, orig, mods, charge, paid, allowed, units, ounits, dos, lcn, st, pname, pid, pay) in cur:
            cl = arch.claims.get(ck)
            if cl is None:
                continue
            try:
                ml = tuple(json.loads(mods)) if mods else ()
            except ValueError:
                ml = ()
            a = Ans(line_key=lk, claim_key=ck, txn=cl.txn, pcn=cl.pcn, bill=cl.bill, code=code, orig=orig if orig != code else None,
                    mods=ml, charge=charge or 0, paid=paid or 0, allowed=allowed, units=units, orig_units=ounits, dos=dos,
                    status=st or cl.status, payer=_payer_key(pid, pname) or cl.payer, payer_name=pname or cl.payer_name,
                    paid_on=pay or cl.paid_on, trace=cl.trace, lcn=lcn, adj=[], rem=[], standing=0, claim=cl)
            arch.lines[lk] = a
            cl.lines.append(a)
        # a line with no date of its own takes its claim's one-day statement period (as detect.py's load does)
        undated = [a for a in arch.lines.values() if not a.dos]
        if undated:
            want = sorted({a.claim_key for a in undated})
            period: dict[str, str] = {}
            for part in _chunks(want):
                for ck, frm, to in conn.execute(f"SELECT claim_key, statement_from, statement_to FROM claims WHERE claim_key IN ({','.join('?' * len(part))})", part):
                    if frm and frm == (to or frm):
                        period[ck] = frm
            for a in undated:
                a.dos = period.get(a.claim_key)
        for ck, lk, level, grp, reason, amt in conn.execute(
                """SELECT a.claim_key, a.line_key, a.level, a.group_code, a.reason_code, a.amount_cents FROM adjustments a
                   WHERE a.claim_key IN (SELECT claim_key FROM temp.b2_claims)"""):
            row = ((grp or "").upper(), (reason or "").upper(), amt or 0)
            if lk and lk in arch.lines:
                arch.lines[lk].adj.append(row)
            elif ck in arch.claims and level == "claim":
                arch.claims[ck].adj.append(row)
        for ck, lk, level, code in conn.execute(
                """SELECT r.claim_key, r.line_key, r.level, r.code FROM remarks r
                   WHERE r.claim_key IN (SELECT claim_key FROM temp.b2_claims)"""):
            if lk and lk in arch.lines:
                arch.lines[lk].rem.append((code or "").upper())
            elif ck in arch.claims and level == "claim":
                arch.claims[ck].rem.append((code or "").upper())
    finally:
        conn.execute("DROP TABLE IF EXISTS temp.b2_bills")
        conn.execute("DROP TABLE IF EXISTS temp.b2_claims")
    for a in arch.lines.values():
        if a.dos:
            arch.by_service[(a.bill, a.code, a.dos)].append(a)
            if a.orig:
                arch.by_service[(a.bill, a.orig, a.dos)].append(a)
    for v in arch.by_service.values():
        v.sort(key=lambda a: (a.paid_on or "", a.line_key))
    if standing is not None:
        for a in arch.lines.values():
            a.standing = standing.get(a.line_key, 0) or 0
    else:
        _net_payments(arch)
    return arch


def _net_payments(arch: Archive) -> None:
    """The payments still standing on these bills, when the Detector's ledger is not given: a reversal (claim status 22,
    a negative payment) cancels the payer's earlier payments of the same bill, code and date, the same amount first, then
    the most recent (as remitwatch.detect pairs them). Claim-level takebacks (PLB WO, a reversal sent without lines) are
    not seen here."""
    pos: dict[tuple, list[Ans]] = defaultdict(list)
    revs: list[Ans] = []
    for a in arch.lines.values():
        a.standing = 0
        if a.status in PAYMENT_STATUSES and (a.paid or 0) > 0:
            a.standing = a.paid
            pos[(a.bill, a.code, a.dos)].append(a)
        elif a.status == REVERSAL_STATUS and (a.paid or 0) < 0:
            revs.append(a)
    for r in sorted(revs, key=lambda x: (x.paid_on or "", x.line_key)):
        need = -r.paid
        cands = [p for p in pos.get((r.bill, r.code, r.dos), []) if p.standing > 0 and (p.paid_on or "") < (r.paid_on or "")]
        cands = [p for p in cands if p.payer == r.payer] if r.payer else [p for p in cands if p.paid == need]
        cands.sort(key=lambda p: (p.paid_on or "", p.line_key), reverse=True)
        cands.sort(key=lambda p: (p.standing != need and p.paid != need, p.pcn != r.pcn))
        for p in cands:
            take = min(p.standing, need)
            p.standing -= take
            need -= take
            if need <= 0:
                break
