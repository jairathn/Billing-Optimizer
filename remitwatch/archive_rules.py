"""
Archive rules: what the 835 archive alone says about the run's items, before they are saved.

These are the rule gaps that need no EMA data (amendment section 7; the why-missed lens). Each rule is a pure function
over a SQLite connection and the Detector's in-memory items (the finding dicts of Detector.findings, before save), and
returns actions with proof rows. The rules never write to the store and never change an item: evaluate() runs them and
settles which action wins for each item, and run_on_detector() applies that to a running Detector (the one call the
detection wiring adds; the needs file of unit B2 gives the hook).

Actions (each has 'action', 'rule', 'finding_id' where it concerns an item, 'strength' and 'proof'):
  close_with_proof  the archive proves the money was paid: what Detector._mark_paid needs ('paid_date', 'paid_cents',
                    'note', 'detail'). Used only where a payment that still stands proves it.
  reclassify        the item stays listed but is not what its signal says: 'reclass' (a code), 'label', 'hint' (one plain
                    sentence), 'shortfall_cents' (its new size, or None to keep it) and 'state_hint' ('work_now',
                    'waiting' or 'done': the state remitwatch.states should weigh)
  fold_into         a repeat listing of one service: 'into' is the item that carries it
  raise_item        a line no rule lists today: a proto-finding for Detector._emit ('signal', 'category', 'severity',
                    'actionable', 'title', 'line_key', 'claim_key', 'expected_cents', 'shortfall_cents', 'carc', 'rarc',
                    'evidence', 'hint', 'who_should_pay', 'what_to_fix')
  hide_match        an item that a hide rule at payer x code x modifier x reason grain covers ('hide_rule', 'hide_key')
  expected_cents    a fair expected amount where an item has none ('expected_cents', 'shortfall_cents', 'basis')
'strength' is 'proven' (the archive shows it on the item's own service) or 'likely' (one remittance says so, another one
the archive lacks would prove it): the wiring may show a 'likely' action as a hint only.

Proof rows have the shape of a story row (remitwatch.story): {'date', 'source': '835', 'kind', 'payer', 'amount_cents',
'codes': [{'code', 'meaning'}], 'text', 'decisive'} plus keys that find the 835 row again ('line_key', 'claim_key',
'trace', 'claim_status', 'code', 'units', 'allowed_cents'). They belong in an item's evidence, which stays in the store;
nothing here logs them.

The rules:
  1  same_payer_paid_before  a denial or short pay on a line the same payer had already paid: reclassified, never closed
  2  other_payer_paid_first  a denial after another payer paid the line (a second payer's answer, or a plan that is not the
                             patient's): not a recoverable denial; says who paid and what is left of the patient's share
  3  units_split             a denial or short pay that is a units split across lines of one code
  4  short_pay_topped_up     a short payment the same payer later topped up (same bill, a later remittance, any check)
  5  same_day_answers        two answers on one day for one line: the payer's reversal of the item's answer and its new
                             answer, a line the payer split across two claims (MA15), a payment on another remittance the
                             same day, and a whole-claim denial answered on another claim of the bill the same day
  6  second_payer_share      a secondary that paid less than half the expected amount paid its share of what the primary
                             left: not an underpayment, and not a denial left unpaid
  7  fold_repeat_denials     one item per service and reason: a payer's repeat denials fold into the newest
  8  pr_group_denials        PR denials that are not deductible, coinsurance or copay (PR-288, PR-27, PR-26, PR-200,
                             PR-49, PR-204) and CO-147: raise the item the archive shows, who should pay and what to fix
  9  hide_matches            hide rules at payer x code x modifier x reason grain (hide_match, hide_sql)
  10 drug_expected           a fair expected amount for drugs and skin substitutes: the payer's own median allowed per unit

Efficiency (tests/test_efficiency.py): the compact serverless store has almost no secondary indexes. Every statement here
is one pass over a table, or lookups by a primary key or by one of the compact store's two indexes (lines by claim, claims
by claim id) driven from a temporary key table. None looks a table up once per row of another. The SQL stays within
SQLite 3.40 and never names an outer table inside a correlated subquery's ORDER BY.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter, defaultdict
from statistics import median
from typing import Any, Callable, Iterable, Mapping, Sequence

from .codes import carc_text, code_category, payer_group_kind, rarc_text

CHUNK = 500
PAYMENT_STATUSES = ("1", "2", "3", "19", "20", "21")
PRIMARY_STATUSES = ("1", "19")
SECONDARY_STATUSES = ("2", "3", "20", "21")
REVERSAL_STATUS = "22"
COST_SHARE_CARCS = frozenset({"1", "2", "3"})           # the patient's deductible, coinsurance and copay: never a denial
# reasons that only restate a payer's pricing or a prior payer's part (as detect.IGNORE_CARCS): not a denial
PRICING_CARCS = frozenset({"45", "253", "144", "23", "94", "187", "59", "131", "237", "A2", "P1", "W1", "3", "2", "1"})
# a restatement piece (a payer repeating part of an earlier answer as its own line) carries one of these and nothing else
RESTATE_REASONS = PRICING_CARCS | {"B13"}
# modifiers that make a second line of one code another unit (another site, a repeat procedure), not the same line again
DISTINCT_MODS = frozenset({"59", "XS", "XE", "XU", "XP", "76", "77", "91", "RT", "LT", "50", "E1", "E2", "E3", "E4", "FA", "F1",
                           "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "TA", "T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8", "T9"})
TOP_UP_SHARE = 0.95             # a short payment topped up to this share of its shortfall is paid
SPLIT_SHARE = 0.95              # the halves of a split line allowed this share of the expected amount together: paid
MIN_DRUG_LINES = 3              # paid lines of the payer's own a drug's expected amount needs
NO_CODE = frozenset({"99024", "G2211"})                 # post-op visits and G2211 are not appealed (as detect.py)

R_PAID_BEFORE = "same_payer_paid_before"
R_OTHER_PAYER = "other_payer_paid_first"
R_UNITS = "units_split"
R_TOPPED_UP = "short_pay_topped_up"
R_SAME_DAY = "same_day_answers"
R_SECOND = "second_payer_share"
R_FOLD = "fold_repeat_denials"
R_PR = "pr_group_denials"
R_HIDE = "hide_matches"
R_DRUG = "drug_expected"
RULES = (R_SAME_DAY, R_TOPPED_UP, R_UNITS, R_PAID_BEFORE, R_OTHER_PAYER, R_SECOND, R_FOLD, R_PR, R_DRUG, R_HIDE)

# Which action wins when several rules speak about one item: a closure the archive proves first, then the payer's own
# replacement of the item's answer, then what happened before the item's answer, then the size of what is left.
PRECEDENCE = ((R_SAME_DAY, "close_with_proof"), (R_UNITS, "close_with_proof"), (R_TOPPED_UP, "close_with_proof"),
              (R_SAME_DAY, "fold_into"), (R_SAME_DAY, "reclassify"), (R_PAID_BEFORE, "reclassify"),
              (R_OTHER_PAYER, "reclassify"), (R_UNITS, "reclassify"), (R_SECOND, "reclassify"), (R_TOPPED_UP, "reclassify"))

STATUS_WORDS = {"1": "as primary", "19": "as primary", "2": "as secondary", "20": "as secondary", "3": "as tertiary",
                "21": "as tertiary", "22": "reversal", "4": "denied the claim", "23": "not its claim"}

_EMA_CLAIM_ID = re.compile(r"([A-Z]{2}\d{2}[A-Z0-9]{5,6})C\d{0,3}")


def bill_of(pcn: str | None) -> str:
    """The bill a claim id belongs to: the same rule as remitwatch.detect.bill_of (a test pins that the two agree). Kept
    here so this module never imports detect.py, which calls it."""
    m = _EMA_CLAIM_ID.fullmatch(pcn or "")
    return m.group(1) if m else (pcn or "")


def _usd(cents: Any) -> str:
    c = int(round(cents or 0))
    return f"-${abs(c) / 100:,.2f}" if c < 0 else f"${c / 100:,.2f}"


def _units_text(u: float) -> str:
    return f"{u:g} unit" + ("" if u == 1 else "s")


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


def _is_open(f: Mapping) -> bool:
    return not f.get("auto_status")


# ----------------------------------------------------------------------------------------------------- the archive view
class Ans:
    """One 835 service line on one of the items' bills: one payer's answer to one line of one submission."""
    __slots__ = ("line_key", "claim_key", "txn", "pcn", "bill", "code", "orig", "mods", "charge", "paid", "allowed", "units",
                 "orig_units", "dos", "status", "payer", "payer_name", "group", "paid_on", "trace", "lcn", "adj", "rem",
                 "standing", "claim")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    @property
    def unit_count(self) -> float:
        """Units this answer pays for (SVC05, one when the payer leaves it out): detect.paid_events' rule."""
        return abs(self.units or 1) or 1

    @property
    def billed_units(self) -> float:
        return abs(self.orig_units or self.units or 1) or 1

    @property
    def billed_code(self) -> str | None:
        """The code the practice billed (the payer may answer under another one)."""
        return self.orig or self.code

    def sig(self) -> frozenset:
        """The modifiers that make this line another unit of its code (another site, a repeat procedure)."""
        return frozenset(m for m in (self.mods or ()) if m in DISTINCT_MODS)

    def piece(self) -> bool:
        """A restatement piece: a line a payer sends to repeat one amount of an earlier answer (the amount paid before as
        B13, the patient's share, the contractual cut), unpaid, with no line control number, one reason taking its charge."""
        if self.lcn or (self.paid or 0) or self.status == REVERSAL_STATUS:
            return False
        amts = [(g, r, c) for g, r, c in (self.adj or []) if c]
        return len(amts) == 1 and amts[0][1] in RESTATE_REASONS and abs(amts[0][2] - (self.charge or 0)) <= 1

    def restated_cents(self) -> int:
        """What a B13 restatement piece says the payer paid before on its line (0 for any other line)."""
        if not self.piece():
            return 0
        return sum(c for _g, r, c in self.adj if r == "B13")

    def pr_cents(self, codes: Iterable[str] | None = None) -> int:
        want = set(codes) if codes else None
        return sum(c for g, r, c in (self.adj or []) if g == "PR" and (want is None or r in want))

    def amount_of(self, group: str, reason: str) -> int:
        return sum(c for g, r, c in (self.adj or []) if g == group and r == reason)

    def denial_reasons(self) -> list[tuple[str, str, int]]:
        """The payer's reasons for not paying (not its pricing, not the patient's cost share), largest first."""
        out = [(g, r, c) for g, r, c in (self.adj or []) if c > 0 and r not in PRICING_CARCS and not (g == "PR" and r in COST_SHARE_CARCS)]
        return sorted(out, key=lambda x: (-x[2], x[0], x[1]))

    def to_patient_only(self) -> bool:
        """Processed with nothing paid and the allowed amount on the patient: a cost share and no denial reason."""
        return not (self.paid or 0) and self.pr_cents(COST_SHARE_CARCS) > 0 and not self.denial_reasons()


class Claim:
    """One 835 claim (CLP) on an item's bill, with its remittance's date, trace and payer."""
    __slots__ = ("claim_key", "txn", "pcn", "bill", "status", "charge", "paid", "patient_resp", "pccn", "freq", "crossover",
                 "payer", "payer_name", "group", "paid_on", "trace", "adj", "rem", "lines", "original_ref")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))


class Archive:
    """Every 835 answer on the bills of a set of items, keyed for the rules: by line, by claim, by bill, and by service
    (bill, code, date of service; an answer whose payer changed the code is filed under the billed code too)."""

    def __init__(self):
        self.lines: dict[str, Ans] = {}
        self.claims: dict[str, Claim] = {}
        self.by_service: dict[tuple, list[Ans]] = defaultdict(list)
        self.by_bill: dict[str, list[Claim]] = defaultdict(list)
        self._cancelled: dict[str, Ans] | None = None

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
        return sorted(out.values(), key=lambda a: (a.paid_on or "", a.trace or "", a.line_key))

    def cancelled(self) -> dict[str, Ans]:
        """{line_key of an answer: the reversal that cancels it}. A reversal (claim status 22) restates the answer it
        takes back with every amount negated; it cancels the payer's most recent earlier answer of that service with those
        amounts, on an earlier remittance (a remittance that reverses an answer and answers again carries both)."""
        if self._cancelled is not None:
            return self._cancelled
        out: dict[str, Ans] = {}
        groups: dict[tuple, list[Ans]] = defaultdict(list)
        for a in self.lines.values():
            groups[(a.bill, a.code, a.dos, a.payer)].append(a)
        for g in groups.values():
            revs = sorted((x for x in g if x.status == REVERSAL_STATUS), key=lambda x: (x.paid_on or "", x.trace or "", x.line_key))
            for r in revs:
                want = (-(r.charge or 0), -(r.paid or 0), tuple(sorted((gg, c, -x) for gg, c, x in r.adj)))
                cands = [a for a in g if a.status != REVERSAL_STATUS and a.line_key not in out
                         and ((a.paid_on or "") < (r.paid_on or "") or ((a.paid_on or "") == (r.paid_on or "") and a.trace != r.trace))
                         and ((a.charge or 0), (a.paid or 0), tuple(sorted(a.adj))) == want]
                if cands:
                    out[max(cands, key=lambda a: (a.paid_on or "", a.trace or "", a.line_key)).line_key] = r
        self._cancelled = out
        return out


def load_archive(conn: sqlite3.Connection, items: Iterable[Mapping], *, standing: Mapping[str, int] | None = None,
                 line_keys: Iterable[str] = ()) -> Archive:
    """The archive view for these items: every claim on their bills (every submission of each bill, whoever answered
    it), with service lines, adjustments, remarks and the remittances' dates, traces and payers. Superseded remittances
    are left out. `line_keys` adds the bills of these 835 lines too. `standing` is the Detector's ledger of payments
    still standing once takebacks are applied (Detector.standing, {line_key: cents}); without it the payments are
    netted here for these bills alone (reversals paired as detect.py pairs them; claim-level takebacks are not seen)."""
    arch = Archive()
    bills = {bill_of(f.get("patient_control_number")) for f in items
             if f.get("patient_control_number") and (f.get("line_key") or f.get("claim_key"))}
    extra = sorted(set(line_keys))
    for part in _chunks(extra):
        for (pcn,) in conn.execute(f"SELECT patient_control_number FROM lines WHERE line_key IN ({','.join('?' * len(part))})", part):
            bills.add(bill_of(pcn))
    bills = sorted(bills - {""})
    if not bills:
        return arch
    conn.execute("DROP TABLE IF EXISTS temp.b2_bills")
    conn.execute("DROP TABLE IF EXISTS temp.b2_claims")
    conn.execute("CREATE TEMP TABLE b2_bills (bill TEXT PRIMARY KEY, hi TEXT)")
    conn.execute("CREATE TEMP TABLE b2_claims (claim_key TEXT PRIMARY KEY)")
    try:
        conn.executemany("INSERT OR IGNORE INTO temp.b2_bills VALUES (?, ?)", [(b, b + "D") for b in bills])
        wanted = set(bills)
        keep = []
        # every claim id from the bill up to the bill followed by 'D': the bill itself and its submissions (bill + 'C' + digits)
        for (ck, txn, pcn, st, charge, paid, pr, pccn, freq, cross, oref, cpay, cfi, tpay, trace, pname, pid) in conn.execute(
                """SELECT c.claim_key, c.transaction_key, c.patient_control_number, c.claim_status_code, c.charge_cents, c.paid_cents,
                          c.patient_responsibility_cents, c.payer_claim_control_number, c.claim_frequency_code, c.crossover_carrier_name,
                          c.original_reference_number, c.payment_date, c.claim_filing_indicator,
                          t.payment_date, t.trace_number, t.payer_name, t.payer_id
                   FROM temp.b2_bills b
                   JOIN claims c ON c.patient_control_number >= b.bill AND c.patient_control_number < b.hi
                   JOIN transactions t ON t.transaction_key = c.transaction_key
                   WHERE COALESCE(t.superseded, 0) = 0"""):
            b = bill_of(pcn)
            if b not in wanted:
                continue                # an id that only shares the bill's first characters
            cl = Claim(claim_key=ck, txn=txn, pcn=pcn, bill=b, status=st, charge=charge or 0, paid=paid or 0, patient_resp=pr, pccn=pccn,
                       freq=freq, crossover=cross, original_ref=oref, payer=_payer_key(pid, pname), payer_name=pname,
                       group=payer_group_kind(pname, cfi)[0], paid_on=cpay or tpay or "", trace=trace, adj=[], rem=[], lines=[])
            arch.claims[ck] = cl
            arch.by_bill[b].append(cl)
            keep.append((ck,))
        conn.executemany("INSERT OR IGNORE INTO temp.b2_claims VALUES (?)", keep)
        for (lk, ck, code, orig, mods, charge, paid, allowed, units, ounits, dos, lcn, st, pname, pid, pay) in conn.execute(
                """SELECT l.line_key, l.claim_key, l.procedure_code, l.original_procedure_code, l.modifiers, l.charge_cents,
                          l.paid_cents, l.allowed_cents, l.paid_units, l.original_units,
                          COALESCE(l.service_date, CASE WHEN l.service_period_start = COALESCE(l.service_period_end, l.service_period_start)
                                                        THEN l.service_period_start END),
                          l.line_control_number, l.claim_status_code, l.payer_name, l.payer_id, l.payment_date
                   FROM temp.b2_claims k JOIN lines l ON l.claim_key = k.claim_key"""):
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
                    group=cl.group, paid_on=pay or cl.paid_on, trace=cl.trace, lcn=lcn, adj=[], rem=[], standing=0, claim=cl)
            arch.lines[lk] = a
            cl.lines.append(a)
        # a line with no date of its own takes its claim's one-day statement period (as detect.py's load does)
        undated = sorted({a.claim_key for a in arch.lines.values() if not a.dos})
        period: dict[str, str] = {}
        for part in _chunks(undated):
            for ck, frm, to in conn.execute(f"SELECT claim_key, statement_from, statement_to FROM claims WHERE claim_key IN ({','.join('?' * len(part))})", part):
                if frm and frm == (to or frm):
                    period[ck] = frm
        for a in arch.lines.values():
            if not a.dos:
                a.dos = period.get(a.claim_key)
        for ck, lk, level, grp, reason, amt in conn.execute(
                """SELECT a.claim_key, a.line_key, a.level, a.group_code, a.reason_code, a.amount_cents FROM adjustments a
                   WHERE a.claim_key IN (SELECT claim_key FROM temp.b2_claims)"""):
            row = ((grp or "").upper(), (reason or "").upper(), amt or 0)
            if lk and lk in arch.lines:
                arch.lines[lk].adj.append(row)
            elif ck in arch.claims and (level == "claim" or not lk):
                arch.claims[ck].adj.append(row)
        for ck, lk, level, code in conn.execute(
                """SELECT r.claim_key, r.line_key, r.level, r.code FROM remarks r
                   WHERE r.claim_key IN (SELECT claim_key FROM temp.b2_claims)"""):
            if lk and lk in arch.lines:
                arch.lines[lk].rem.append((code or "").upper())
            elif ck in arch.claims and (level == "claim" or not lk):
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
        v.sort(key=lambda a: (a.paid_on or "", a.trace or "", a.line_key))
    if standing is not None:
        for a in arch.lines.values():
            a.standing = max(0, standing.get(a.line_key, 0) or 0)
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


# ------------------------------------------------------------------------------------------------------------ helpers
MANY_PAYER_GROUPS = frozenset({"Medicare Supplement carriers", "Self-funded plan TPAs", "Unknown"})


def _same_payer(a: Ans, b: Ans) -> bool:
    """One payer: the same payer id (or name), or the same payer group as the claim filing indicator refines it (one plan
    under two ids). Medicare's two contractors, a company's commercial plan and its Medicare Advantage plan, or two
    carriers of a group that holds many payers (the Medigap carriers, the self-funded plans' administrators) are two."""
    if a.payer and a.payer == b.payer:
        return True
    return bool(a.group and a.group == b.group and a.group not in MANY_PAYER_GROUPS)


def _units_paid(events: Sequence[Ans]) -> float:
    """Units these standing payments pay: the primary payments' units (a secondary pays its share of the same units), or
    the secondary payments' units when no primary payment stands (detect.units_paid's rule)."""
    prim = [e for e in events if e.status in PRIMARY_STATUSES]
    return sum(e.unit_count for e in (prim or events))


def _claim_lines(a: Ans) -> list[Ans]:
    """The lines of the item's claim that bill its code on its date: every billed line, the item's own included, and a B13
    restatement piece for the line it restates; the payer's other restatement pieces (its patient-share and contractual
    pieces) are parts of that same line and are left out."""
    return [x for x in a.claim.lines if x.billed_code == a.billed_code and x.dos == a.dos and (not x.piece() or x.restated_cents())]


def _units_billed(a: Ans) -> float:
    """Units the item's claim billed for its code and date, over every line of it (detect.units_billed's rule), except
    that lines sharing a line control number are one billed line the payer split into pieces (counted once), and a
    payer's restatement pieces count as the one line they restate."""
    split: dict[str, float] = {}
    n = 0.0
    for x in _claim_lines(a):
        if x.lcn:
            split[x.lcn] = max(split.get(x.lcn, 0), x.billed_units)
        else:
            n += x.billed_units
    return (n + sum(split.values())) or 1


def _attribute(a: Ans, x: Ans, lines: Sequence[Ans]) -> str:
    """Which of the item's claim's billed lines an answer x is about: 'own' (the item's line), 'other' (another line of
    the claim) or '' (it cannot be told). By line control number first; then, where the claim bills the code once on the
    date, it is the item's line; then by the modifiers that make a line another unit (59, XS, 76, RT, ...), when exactly
    one line of the claim has them."""
    if a.lcn and x.lcn:
        if x.lcn == a.lcn:
            return "own"
        if any(l.lcn == x.lcn for l in lines if l.line_key != a.line_key):
            return "other"
    if len(lines) <= 1:
        return "own"
    same = [l for l in lines if l.sig() == x.sig()]
    if len(same) == 1:
        return "own" if same[0].line_key == a.line_key else "other"
    return ""


def _line_identity(a: Ans) -> str:
    """Which billed line of its service an answer is, for telling repeats of one line from other units: '*' when its
    claim bills the code once on the date, else its line control number, else its unit-making modifiers."""
    if len(_claim_lines(a)) <= 1:
        return "*"
    return ("L:" + a.lcn) if a.lcn else ("S:" + ",".join(sorted(a.sig())))


def _per_unit(paid: Sequence[Ans], fallback: float | None) -> float | None:
    """What one unit is worth at this payer: its allowed amount per unit on the payments, else what it paid per unit."""
    units = sum(e.unit_count for e in paid)
    allowed = sum(e.allowed or 0 for e in paid if (e.allowed or 0) > 0)
    if units and allowed:
        return allowed / units
    if units and any(e.paid for e in paid):
        return sum(max(e.paid or 0, 0) for e in paid) / units
    return fallback


def _cap(size: float, f: Mapping) -> float:
    """A size from units never above the item's own expected amount, where it has one."""
    exp = f.get("expected_cents") or 0
    return min(size, exp) if exp > 0 else size


def _codes(a: Ans) -> list[dict]:
    out, seen = [], set()
    for g, r, c in sorted(a.adj or [], key=lambda x: -abs(x[2])):
        k = f"{g}-{r}"
        if k not in seen and c:
            seen.add(k)
            out.append({"code": k, "meaning": carc_text(r)})
    for r in a.rem or []:
        if r not in seen:
            seen.add(r)
            out.append({"code": r, "meaning": rarc_text(r)})
    return out


def _reasons_text(a: Ans) -> str:
    rs = a.denial_reasons()
    return ", ".join(f"{g}-{r}" for g, r, _c in rs[:3]) or "no reason code"


def _says(a: Ans) -> str:
    """One answer in plain words."""
    who = a.payer_name or "The payer"
    code = a.code + (f" (billed {a.orig})" if a.orig else "")
    units = f", {_units_text(a.unit_count)}" if a.unit_count != 1 else ""
    if a.status == REVERSAL_STATUS:
        return f"{who} reversed its earlier answer on {code} ({_usd(-(a.paid or 0))} taken back)"
    if (a.paid or 0) > 0:
        share = a.pr_cents(COST_SHARE_CARCS)
        return (f"{who} paid {code} {_usd(a.paid)} {STATUS_WORDS.get(a.status, '')}{units}"
                + (f"; patient's share {_usd(share)}" if share else "")).replace("  ", " ")
    if a.to_patient_only():
        return f"{who} allowed {code} and put {_usd(a.pr_cents(COST_SHARE_CARCS))} on the patient{units}"
    return f"{who} paid {code} nothing ({_reasons_text(a)}){units}"


def _proof(a: Ans, kind: str, decisive: bool = False, text: str | None = None) -> dict:
    return {"date": a.paid_on or None, "source": "835", "kind": kind, "payer": a.payer_name, "amount_cents": a.paid or 0,
            "codes": _codes(a), "text": text or _says(a), "decisive": decisive, "line_key": a.line_key,
            "claim_key": a.claim_key, "trace": a.trace, "claim_status": a.status, "code": a.code, "units": a.unit_count,
            "allowed_cents": a.allowed}


def _kind(a: Ans) -> str:
    if a.status == REVERSAL_STATUS:
        return "reversal"
    if (a.paid or 0) > 0:
        return "payment"
    if a.to_patient_only():
        return "to_patient"
    return "denial"


def _act(action: str, rule: str, f: Mapping | None, strength: str, proof: list[dict], **kw) -> dict:
    out = {"action": action, "rule": rule, "finding_id": f.get("finding_id") if f else None, "strength": strength, "proof": proof}
    out.update(kw)
    return out


def _close(rule: str, f: Mapping, pay: Ans, paid_cents: int, note: str, how: str, proof: list[dict], *, recovery: bool,
           strength: str = "proven") -> dict:
    """A closure in the shape Detector._mark_paid takes: the payment's date, amount, a note and the paid_later detail."""
    detail = {"payment_date": pay.paid_on or None, "claim": pay.pcn, "trace": pay.trace, "paid_cents": paid_cents,
              "claim_status": pay.status, "how": how, "rule": rule}
    return _act("close_with_proof", rule, f, strength, proof, paid_date=pay.paid_on or None, paid_cents=paid_cents, note=note,
                detail=detail, counts_as_recovery=recovery)


def _reclass(rule: str, f: Mapping, reclass: str, label: str, hint: str, shortfall: int | None, state: str, proof: list[dict],
             strength: str = "proven", **kw) -> dict:
    return _act("reclassify", rule, f, strength, proof, reclass=reclass, label=label, hint=hint,
                shortfall_cents=None if shortfall is None else int(round(max(shortfall, 0))), state_hint=state, **kw)


def _line_items(items: Iterable[Mapping], arch: Archive, *, zero_paid: bool | None = None,
                categories: Iterable[str] = ("denial", "bundling", "downcoding", "underpayment")) -> list[tuple[Mapping, Ans]]:
    """Open line items with their own 835 answer: zero-paid ones (True), paid ones (False) or both (None)."""
    cats = set(categories)
    out = []
    for f in items:
        if not _is_open(f) or not f.get("line_key") or f.get("category") not in cats:
            continue
        a = arch.lines.get(f["line_key"])
        if a is None or not a.dos:
            continue
        paid = (f.get("paid_cents") or 0) > 0
        if zero_paid is True and paid or zero_paid is False and not paid:
            continue
        out.append((f, a))
    return out


def _after(x: Ans, a: Ans) -> bool:
    """x came after the item's answer a: a later date, or the same date on another remittance."""
    return (x.paid_on or "") > (a.paid_on or "") or ((x.paid_on or "") == (a.paid_on or "") and x.trace != a.trace)


def _before(x: Ans, a: Ans) -> bool:
    return (x.paid_on or "") < (a.paid_on or "")


# ------------------------------------------------------------------------------------- 1. the same payer paid before
def _restates(x: Ans, pays: Sequence[Ans]) -> Ans | None:
    """The earlier standing payment a B13 restatement piece repeats (its amount within $1 or 3%), if the archive has it."""
    amt = x.restated_cents()
    if not amt:
        return None
    return next((p for p in pays if abs((p.paid or 0) - amt) <= max(100, 0.03 * (p.paid or 0))), None)


def same_payer_paid_before(conn: sqlite3.Connection, items: Iterable[Mapping], *, archive: Archive | None = None,
                           standing: Mapping[str, int] | None = None) -> list[dict]:
    """A denial, or a short payment, on a line the same payer had already paid (a payment that still stands, dated before
    the item's remittance): a repeat submission denied again, a duplicate's denial, a replacement claim denied while the
    original's payment stands (practice-manager case C10). Reclassified with the earlier payment as proof, never closed:
    the practice contests about one in six of these (a second-level review, a takeback to come).
    The payment must be the item's own line's: the same line control number; or the claim billed the code once that day;
    or the unit-making modifiers (59, XS, 76, RT, ...) point to it; or, where nothing tells the lines apart, the payer's
    earlier payments cover every unit the claim billed. A payment on another line of the claim is a units question (rule
    3). The payer's own B13 restatement (it repeats a payment as 'previously paid', C07) counts as its word that the line
    was paid: 'proven' when the restated payment is in the archive, 'likely' when it is not. The line paid in full before:
    nothing is owed unless the payer takes the payment back (state 'done'); fewer units than billed: sized at the units
    left, at the payer's own rate per unit."""
    items = list(items)
    arch = archive or load_archive(conn, items, standing=standing)
    out = []
    for f, a in _line_items(items, arch):
        ans = arch.answers_for(f)
        same = [x for x in ans if x.line_key != a.line_key and _same_payer(x, a)]
        before = [x for x in same if x.standing > 0 and _before(x, a)]
        restating = [x for x in same if x.restated_cents()]
        if not before and not restating and not a.restated_cents():
            continue
        who = (before[0] if before else restating[0] if restating else a).payer_name or "The payer"
        if (f.get("paid_cents") or 0) > 0:
            if not before:
                continue
            # a short payment after an earlier one that stands: two payments of one service by one payer
            first = before[0]
            proof = [_proof(x, "payment", True) for x in before] + [_proof(a, "payment", False)]
            total = sum(x.standing for x in before) + (a.standing or 0)
            best_allowed = max([x.allowed or 0 for x in before] + [a.allowed or 0])
            expected = f.get("expected_cents") or 0
            over = total - max(best_allowed, expected) if best_allowed or expected else 0
            left = max(0, (expected or 0) - best_allowed)
            hint = (f"{who} had already paid this service {_usd(sum(x.standing for x in before))} on {first.paid_on}; this payment is a "
                    f"second one ({_usd(a.paid)}).")
            if over > 100:
                hint += f" Together they exceed what the payer allows by {_usd(over)}: a possible overpayment to settle with the payer."
            out.append(_reclass(R_PAID_BEFORE, f, "paid_before", "Already paid by this payer", hint, left,
                                "work_now" if over > 100 or left > 0 else "done", proof, "proven" if any(x.status in PRIMARY_STATUSES for x in before) else "likely",
                                possible_overpayment_cents=max(over, 0) if over > 100 else 0))
            continue
        lines = _claim_lines(a)
        need = _units_billed(a)
        # the item is the payer's own restatement of an earlier payment (a B13 piece)
        if a.restated_cents():
            match = _restates(a, [x for x in same if x.standing > 0 and not _after(x, a)])
            proof = ([_proof(match, "payment", True)] if match else []) + [_proof(a, "denial", True, f"{who} restated {_usd(a.restated_cents())} "
                                                                           f"as paid before (B13) on {a.paid_on}")]
            hint = (f"{who} says it already paid this line {_usd(a.restated_cents())} (B13, previously paid)"
                    + (f": its payment of {match.paid_on} is on file. " if match else ": that payment's remittance is not on file. ")
                    + "This is a restatement, not a denial. Nothing is owed unless the payer takes the payment back.")
            out.append(_reclass(R_PAID_BEFORE, f, "paid_before", "Already paid by this payer (restated)", hint, 0, "done", proof,
                                "proven" if match else "likely"))
            continue
        own = [x for x in before if _attribute(a, x, lines) == "own"]
        own_restated = [x for x in restating if _attribute(a, x, lines) == "own"]
        unknown = [x for x in before if _attribute(a, x, lines) == ""]
        if own:
            got = _units_paid(own)
            prim = [x for x in own if x.status in PRIMARY_STATUSES]
            strength = "proven" if prim else "likely"
            proof = [_proof(x, "payment", True) for x in own] + [_proof(a, _kind(a), False)]
            if got >= a.billed_units:
                hint = (f"Already paid by {who} on {own[0].paid_on} ({_usd(sum(x.standing for x in own))}); this is a later denial of the "
                        f"same line. Nothing is owed unless the payer takes that payment back.")
                out.append(_reclass(R_PAID_BEFORE, f, "paid_before", "Already paid by this payer, denied again", hint, 0, "done", proof,
                                    strength, units_billed=a.billed_units, units_paid=got))
            else:
                per = _per_unit(prim or own, (f.get("expected_cents") or 0) / max(a.billed_units, 1) or None)
                size = _cap(per * (a.billed_units - got), f) if per else f.get("shortfall_cents")
                hint = (f"{who} paid {got:g} of the {_units_text(a.billed_units)} this line billed, on {own[0].paid_on}; this denial is of "
                        f"the rest" + (f", worth about {_usd(size)} at the payer's own rate per unit." if per else "."))
                out.append(_reclass(R_PAID_BEFORE, f, "paid_before_part", "Partly paid by this payer before", hint, size, "work_now", proof,
                                    strength, units_billed=a.billed_units, units_paid=got))
        elif own_restated:
            pays = [x for x in same if x.standing > 0]
            match = next((m for m in (_restates(x, pays) for x in own_restated) if m), None)
            r = own_restated[0]
            proof = ([_proof(match, "payment", True)] if match else []) + [_proof(r, "denial", True, f"{who} restated {_usd(r.restated_cents())} "
                                                                           f"on this line as paid before (B13) on {r.paid_on}")] + [_proof(a, _kind(a), False)]
            hint = (f"{who} says it already paid this line {_usd(r.restated_cents())} (B13 on {r.paid_on})"
                    + (f"; its payment of {match.paid_on} is on file." if match else "; that payment's remittance is not on file.")
                    + " Nothing is owed unless the payer takes the payment back.")
            out.append(_reclass(R_PAID_BEFORE, f, "paid_before", "Already paid by this payer (restated)", hint, 0, "done", proof,
                                "proven" if match else "likely"))
        elif unknown and _units_paid(unknown) >= need:
            prim = [x for x in unknown if x.status in PRIMARY_STATUSES]
            proof = [_proof(x, "payment", True) for x in unknown] + [_proof(a, _kind(a), False)]
            hint = (f"Already paid by {who} on {unknown[0].paid_on} ({_usd(sum(x.standing for x in unknown))}), every unit the claim billed; "
                    "this is a later denial of the same service. Nothing is owed unless the payer takes that payment back.")
            out.append(_reclass(R_PAID_BEFORE, f, "paid_before", "Already paid by this payer, denied again", hint, 0, "done", proof,
                                "proven" if prim else "likely", units_billed=need, units_paid=_units_paid(unknown)))
    return out


# ----------------------------------------------------------------------------------------- 2. another payer paid first
def other_payer_paid_first(conn: sqlite3.Connection, items: Iterable[Mapping], *, archive: Archive | None = None,
                           standing: Mapping[str, int] | None = None) -> list[dict]:
    """A payer's denial of a service another payer had already paid as primary (a payment that still stands, before the
    denial or the same day on another remittance): a second payer's answer after the primary paid, or a plan that is not
    the patient's denying after the primary and the Medigap paid (practice-manager case C09). Not a denial of the service:
    the item says who paid, and is sized at what the primary left to the patient that no secondary has paid or assigned to
    the patient since (any payer's secondary answer counts, the denying payer's own included). Nothing left: 'done'.
    Something left: that is all the denial is about (a Medicaid crossover denied for enrollment, a Medigap's denial), and
    the item stays to work at that size. A payment another payer made only as secondary says nothing here: a crossover
    secondary pays on the primary's answer, and when the primary has since reversed its payment and denied the line
    (Medicare's takebacks) the denial is real."""
    items = list(items)
    arch = archive or load_archive(conn, items, standing=standing)
    out = []
    for f, a in _line_items(items, arch, zero_paid=True):
        ans = arch.answers_for(f)
        prim = [x for x in ans if x.line_key != a.line_key and x.standing > 0 and not _same_payer(x, a) and x.status in PRIMARY_STATUSES
                and (_before(x, a) or ((x.paid_on or "") == (a.paid_on or "") and x.trace != a.trace))]
        if not prim:
            continue
        p = prim[0]
        share_left = sum(x.pr_cents(COST_SHARE_CARCS) for x in prim)
        sec = [x for x in ans if x.line_key != a.line_key and x.status in SECONDARY_STATUSES and (x.standing > 0 or x.pr_cents(COST_SHARE_CARCS))]
        settled = sum(x.standing for x in sec) + sum(x.pr_cents(COST_SHARE_CARCS) for x in sec)
        left = max(0, share_left - settled)
        proof = [_proof(x, "payment", True) for x in prim] + [_proof(x, _kind(x), False) for x in sec] + [_proof(a, _kind(a), False)]
        paid_sec = [x for x in sec if x.standing > 0]
        who = a.payer_name or "the payer"
        hint = (f"Paid by {p.payer_name} as primary on {p.paid_on} ({_usd(sum(x.standing for x in prim))})"
                + (f", and by {paid_sec[0].payer_name} as secondary ({_usd(sum(x.standing for x in paid_sec))})" if paid_sec else ""))
        if left:
            hint += (f": this denial by {who} is about the patient's share the primary left, {_usd(left)}, not the whole service. "
                     "Fix what it asks and send it as a secondary claim, or bill the patient where the plan allows it (never a Medicaid member).")
        else:
            hint += f": this denial by {who} is another payer's answer, not a denial to appeal. Nothing is left to collect from a payer."
        out.append(_reclass(R_OTHER_PAYER, f, "other_payer_paid", "Paid by another payer first", hint, left,
                            "work_now" if left else "done", proof, "proven", paid_by=p.payer_name, patient_share_left_cents=left))
    return out


# ---------------------------------------------------------------------------------------------------------- 3. units
def units_split(conn: sqlite3.Connection, items: Iterable[Mapping], *, archive: Archive | None = None,
                standing: Mapping[str, int] | None = None) -> list[dict]:
    """A denial or a short payment that is a units split across lines of one code (pay per unit against billed units).
    The payer's payments of the service (any claim, any date) are tied to the claim's billed lines as rule 1 ties them
    (line control number, the code billed once, the unit-making modifiers):
    - zero-paid, and the item's own line was paid on or after its remittance (another line of the claim was the one
      denied): closed with that payment as proof;
    - zero-paid, and the payer paid other lines of the claim but not this one: the item is the unit (or units) denied,
      sized at the payer's own rate per unit, never above its own expected amount;
    - zero-paid, payments that cannot be tied to a line: by units, every unit paid with a paid line on the same claim
      closes it, some units paid makes it the units left;
    - paid short while another line of the code on the claim was paid: measured over the lines together (a line the payer
      split into pieces sharing one line control number, or the payer spreading its allowed amount over the units); at
      the expected amount together it is not short.
    A line the payer itself restated as paid before (B13) is rule 1's."""
    items = list(items)
    arch = archive or load_archive(conn, items, standing=standing)
    out = []
    for f, a in _line_items(items, arch):
        lines = _claim_lines(a)
        siblings = [x for x in lines if x.line_key != a.line_key]
        need = _units_billed(a)
        if (f.get("paid_cents") or 0) > 0:
            paid_sibs = [x for x in a.claim.lines if x.line_key != a.line_key and x.billed_code == a.billed_code and x.dos == a.dos
                         and (x.paid or 0) > 0 and x.status in PAYMENT_STATUSES]
            exp = f.get("expected_cents") or 0
            if not paid_sibs or not exp:
                continue
            pieces = [x for x in paid_sibs if a.lcn and x.lcn == a.lcn]
            if pieces:
                # the payer split this one billed line into pieces on the same claim: the pieces together against the line
                paid_sibs, want = pieces, exp
            else:
                want = exp / max(a.billed_units, 1) * need
            total_allowed = (a.allowed or 0) + sum(x.allowed or 0 for x in paid_sibs)
            if total_allowed >= SPLIT_SHARE * want:
                proof = [_proof(x, "payment", True) for x in paid_sibs] + [_proof(a, "payment", True)]
                note = (f"Not short: {a.payer_name or 'the payer'} allowed {_usd(total_allowed)} for {a.code} on this claim, spread over "
                        f"{len(paid_sibs) + 1} lines; the expected amount is {_usd(want)}.")
                out.append(_close(R_UNITS, f, paid_sibs[0], 0, note, "units paid across lines of the same claim", proof, recovery=False))
            continue
        ans = arch.answers_for(f)
        same = [x for x in ans if x.line_key != a.line_key and _same_payer(x, a)]
        if any(x.restated_cents() and _attribute(a, x, lines) == "own" for x in same):
            continue                    # the payer restated this line as paid before: rule 1
        pays = [x for x in same if x.standing > 0]
        if not pays:
            continue
        tag = {x.line_key: _attribute(a, x, lines) for x in pays}
        own = [x for x in pays if tag[x.line_key] == "own"]
        other = [x for x in pays if tag[x.line_key] == "other"]
        unknown = [x for x in pays if tag[x.line_key] == ""]
        proof = [_proof(x, "payment", True) for x in pays] + [_proof(a, _kind(a), False)]
        who = pays[0].payer_name or "the payer"
        if own and _units_paid(own) >= a.billed_units:
            later = [x for x in own if not _before(x, a)]
            if not later:
                continue                # paid before: rule 1
            p = later[0]
            note = (f"Paid on this line: {p.payer_name or 'the payer'} paid it {_usd(sum(x.standing for x in later))} on {p.paid_on}"
                    + (f"; the unit it denied was another line of the claim." if siblings else "."))
            out.append(_close(R_UNITS, f, p, sum(x.standing for x in later), note, "paid on the item's own line",
                              proof, recovery=(p.paid_on or "") > (a.paid_on or "")))
            continue
        if other and not own:
            got = _units_paid(other)
            per = _per_unit([x for x in other if x.status in PRIMARY_STATUSES] or other,
                            (f.get("expected_cents") or 0) / max(a.billed_units, 1) or None)
            size = _cap(per * a.billed_units, f) if per else f.get("shortfall_cents")
            hint = (f"{got:g} of {_units_text(need)} of {a.billed_code} paid by {who}, on the claim's other line"
                    f"{'s' if len({x.lcn or x.line_key for x in other}) > 1 else ''}; this line is the one denied"
                    + (f", worth about {_usd(size)} at the payer's own rate per unit." if per else "."))
            out.append(_reclass(R_UNITS, f, "unit_denied", "Units: another line was paid", hint, size, "work_now", proof, "proven",
                                units_billed=need, units_paid=got))
            continue
        got = _units_paid(own + other + unknown)
        same_claim = [x for x in pays if x.claim_key == a.claim_key]
        if got >= need and same_claim:
            p = same_claim[0]
            note = (f"Paid on another line of the same claim: {p.payer_name or 'the payer'} paid {_units_text(_units_paid(same_claim))} of "
                    f"{a.billed_code} ({_usd(sum(x.standing for x in same_claim))}) on {p.paid_on}, every unit the claim billed.")
            out.append(_close(R_UNITS, f, p, 0, note, "units paid on another line of the claim", proof, recovery=False))
        elif 0 < got < need:
            per = _per_unit([x for x in pays if x.status in PRIMARY_STATUSES] or pays,
                            (f.get("expected_cents") or 0) / max(a.billed_units, 1) or None)
            mine = min(a.billed_units, need - got)
            size = _cap(per * mine, f) if per else f.get("shortfall_cents")
            hint = (f"{got:g} of {_units_text(need)} of {a.billed_code} paid by {who}; this line is "
                    f"{'the unit' if mine == 1 else f'{mine:g} of the units'} denied"
                    + (f", worth about {_usd(size)} at the payer's own rate per unit." if per else "."))
            out.append(_reclass(R_UNITS, f, "unit_denied", "Units: part paid", hint, size, "work_now", proof, "proven",
                                units_billed=need, units_paid=got))
    return out


# ------------------------------------------------------------------------------------- 4. short payments topped up later
def short_pay_topped_up(conn: sqlite3.Connection, items: Iterable[Mapping], *, archive: Archive | None = None,
                        standing: Mapping[str, int] | None = None) -> list[dict]:
    """The later-payment check the short payments lack (UNDERPAID_VS_OWN_RATE, MPPR_EXCESS, CODE_CHANGED, and the downcodes
    as a net over every later payment): what the same payer paid on the service after the item's remittance (a later 835,
    any check, the billed code too), net of anything it took back. A top-up of TOP_UP_SHARE of the shortfall or more closes
    the item with that payment as proof; a smaller one leaves the rest as the size. A payment that only replaced the short
    one (reversed and paid again at the same amount) tops nothing up. When the payer has now paid more than it allows the
    action says so (possible_overpayment_cents): practice-manager case C05."""
    items = list(items)
    arch = archive or load_archive(conn, items, standing=standing)
    out = []
    for f, a in _line_items(items, arch, zero_paid=False, categories=("underpayment", "downcoding")):
        short = f.get("shortfall_cents") or 0
        if short <= 0:
            continue
        ans = arch.answers_for(f)
        same = [x for x in ans if _same_payer(x, a) and x.status in PAYMENT_STATUSES]
        later = [x for x in same if x.line_key != a.line_key and x.standing > 0 and _after(x, a)]
        if not later:
            continue
        total = sum(x.standing for x in same)
        top_up = total - (f.get("paid_cents") or 0)
        if top_up <= 0:
            continue
        best_allowed = max(x.allowed or 0 for x in same)
        over = total - max(best_allowed, f.get("expected_cents") or 0)
        proof = [_proof(a, "payment", False)] + [_proof(x, "payment", True) for x in later]
        first = later[0]
        extra = {"possible_overpayment_cents": over} if over > 100 else {}
        if top_up >= TOP_UP_SHARE * short and extra:
            # not short, but now paid more than the payer allows (two plans paid as primary, C05): work, not a closure
            hint = (f"Not short: {first.payer_name or 'the payer'} paid {_usd(top_up)} more on {first.paid_on}, and has now paid {_usd(total)} "
                    f"for a service it allows at {_usd(max(best_allowed, f.get('expected_cents') or 0))}: a possible overpayment of {_usd(over)}. "
                    "Settle it with the payer (which plan is primary, then a refund or an offset); do not ask for more.")
            out.append(_reclass(R_TOPPED_UP, f, "overpaid", "Paid more than the payer allows", hint, 0, "work_now", proof, **extra))
        elif top_up >= TOP_UP_SHARE * short:
            note = f"Topped up: {first.payer_name or 'the payer'} paid {_usd(top_up)} more on {first.paid_on}; the short payment is now paid in full."
            out.append(_close(R_TOPPED_UP, f, first, top_up, note, "the same payer paid more later", proof, recovery=True))
        else:
            hint = f"{first.payer_name or 'The payer'} paid {_usd(top_up)} more on {first.paid_on}; {_usd(short - top_up)} is still short."
            out.append(_reclass(R_TOPPED_UP, f, "partly_topped_up", "Partly topped up", hint, short - top_up, "work_now", proof, **extra))
    return out


# ------------------------------------------------------------------------------------------------ 5. same-day answers
def same_day_answers(conn: sqlite3.Connection, items: Iterable[Mapping], *, archive: Archive | None = None,
                     standing: Mapping[str, int] | None = None) -> list[dict]:
    """Two answers on one day for one line.
    a) The payer reversed the item's own answer (a claim status 22 that restates it negated) and answered again, on that
       remittance or later. The item's answer no longer stands; the new answer decides:
       - it pays every unit billed (a payment that still stands): closed with that payment as proof;
       - it pays some units: the item is the units left, at the payer's rate per unit;
       - it has its own open item: this one folds into it (the payer's newest answer is the one to work);
       - it puts the amount on the patient (a cost share and no denial reason): nothing to appeal ('done', C11, C12);
       - it denies again with no item of its own: reclassified with the new reasons;
       - there is no new answer on file: the payer is reprocessing ('waiting').
    b) The same payer paid the service the same day on another remittance (another trace): closed when it pays every unit.
    c) The payer split the item's line across two claims on one remittance (MA15 on a claim, or one line control number):
       the halves are measured together (C27); at the expected amount together, closed with the other half as proof.
    d) A whole-claim denial whose codes the same remittance answered on another claim of the bill (C12's duplicate copy):
       paid on every code, closed; otherwise processed to the patient, 'done'."""
    items = list(items)
    arch = archive or load_archive(conn, items, standing=standing)
    cancelled = arch.cancelled()
    open_by_line: dict[str, list[Mapping]] = defaultdict(list)
    for f in items:
        if _is_open(f) and f.get("line_key"):
            open_by_line[f["line_key"]].append(f)
    out = []
    for f, a in _line_items(items, arch):
        ans = arch.answers_for(f)
        need = _units_billed(a)
        r = cancelled.get(a.line_key)
        if r is not None:
            out.append(_replaced(f, a, r, ans, cancelled, open_by_line, need))
            continue
        mine = (f.get("paid_cents") or 0) > 0
        # c) the line split across two claims of one remittance
        halves = [x for x in ans if x.trace == a.trace and x.claim_key != a.claim_key and _same_payer(x, a) and x.status in PAYMENT_STATUSES
                  and x.line_key not in cancelled and (("MA15" in (a.claim.rem or []) or "MA15" in (x.claim.rem or []))
                                                        or (a.lcn and a.lcn == x.lcn))]
        if halves:
            exp = f.get("expected_cents") or 0
            together = (a.allowed or 0) + sum(x.allowed or 0 for x in halves)
            proof = [_proof(a, _kind(a), True)] + [_proof(x, _kind(x), True) for x in halves]
            paid_halves = [x for x in halves if x.standing > 0]
            if not mine and paid_halves and not exp and _units_paid(paid_halves) >= need:
                note = (f"{a.payer_name or 'The payer'} split this line across {len(halves) + 1} claims on the same remittance and paid it on "
                        f"the other one ({_usd(sum(x.standing for x in paid_halves))}).")
                out.append(_close(R_SAME_DAY, f, paid_halves[0], 0, note, "a line the payer split across two claims", proof, recovery=False))
                continue
            if exp and together >= SPLIT_SHARE * exp and paid_halves:
                note = (f"{a.payer_name or 'The payer'} split this line across {len(halves) + 1} claims on the same remittance"
                        f"{' (MA15)' if any('MA15' in (x.claim.rem or []) for x in halves + [a]) else ''}: together it allowed "
                        f"{_usd(together)}, the expected amount, and paid {_usd(sum(x.standing for x in paid_halves) + (a.standing or 0))}.")
                out.append(_close(R_SAME_DAY, f, paid_halves[0], 0, note, "a line the payer split across two claims", proof, recovery=False))
                continue
            if exp and together > (a.allowed or 0):
                hint = (f"{a.payer_name or 'The payer'} split this line across {len(halves) + 1} claims on the same remittance: together it "
                        f"allowed {_usd(together)}; {_usd(exp - together)} is short of the expected {_usd(exp)}.")
                out.append(_reclass(R_SAME_DAY, f, "split_claim", "Split across two claims", hint, exp - together, "work_now", proof))
                continue
        # b) paid the same day on another remittance
        sameday = [x for x in ans if x.line_key != a.line_key and x.standing > 0 and _same_payer(x, a)
                   and (x.paid_on or "") == (a.paid_on or "") and x.trace != a.trace]
        if sameday and not mine:
            pays = [x for x in ans if x.standing > 0 and _same_payer(x, a)]
            if _units_paid(pays) >= need:
                p = sameday[0]
                proof = [_proof(x, "payment", True) for x in sameday] + [_proof(a, _kind(a), False)]
                note = (f"Paid the same day on another remittance: {p.payer_name or 'the payer'} paid {_usd(sum(x.standing for x in sameday))} "
                        f"on {p.paid_on}.")
                out.append(_close(R_SAME_DAY, f, p, sum(x.standing for x in sameday), note, "paid the same day on another remittance",
                                  proof, recovery=True))
    out += _claim_level_same_day(items, arch)
    return out


def _replaced(f: Mapping, a: Ans, r: Ans, ans: list[Ans], cancelled: dict, open_by_line: dict, need: float) -> dict:
    """5a: the payer reversed the item's answer; what its new answer says."""
    newer = [x for x in ans if x.trace == r.trace and _same_payer(x, a) and x.status != REVERSAL_STATUS and x.line_key != a.line_key
             and x.line_key not in cancelled]
    if not newer:
        newer = [x for x in ans if (x.paid_on or "") >= (r.paid_on or "") and _same_payer(x, a) and x.status != REVERSAL_STATUS
                 and x.line_key != a.line_key and x.line_key not in cancelled]
    proof = [_proof(a, _kind(a), False), _proof(r, "reversal", True, f"{r.payer_name or 'The payer'} reversed this answer on {r.paid_on}")]
    proof += [_proof(x, _kind(x), True) for x in newer]
    # the payer's processed re-answer decides; a denied copy on the same remittance (a duplicate submission, or CO-129 "the
    # adjustment request was processed" on a corrected claim) is about the copy, not the service (C12, C57)
    newer = [x for x in newer if x.status in PAYMENT_STATUSES] or newer
    who = r.payer_name or "The payer"
    if not newer:
        hint = (f"{who} reversed this answer on {r.paid_on} and no new answer is on file: the payer is reprocessing it. Check the claim's "
                "status with the payer before working it.")
        return _reclass(R_SAME_DAY, f, "reversed_no_answer", "Reversed by the payer, no new answer yet", hint, f.get("shortfall_cents"),
                        "waiting", proof)
    pays = [x for x in ans if x.standing > 0 and _same_payer(x, a)]
    new_pays = [x for x in newer if x.standing > 0]
    carriers = [g for x in newer for g in open_by_line.get(x.line_key, []) if g.get("finding_id") != f.get("finding_id")]
    if (f.get("paid_cents") or 0) > 0:
        # a short payment the payer reversed: its new payment decides, measured against the expected amount, not as "paid"
        exp = f.get("expected_cents") or 0
        best = max(newer, key=lambda x: ((x.allowed or 0), x.standing))
        if new_pays and exp and (best.allowed or 0) >= SPLIT_SHARE * exp and best.standing > 0:
            note = f"{who} reversed this payment on {r.paid_on} and paid the service again at {_usd(best.allowed)} allowed, the expected amount."
            return _close(R_SAME_DAY, f, best, max(best.standing - (f.get("paid_cents") or 0), 0), note,
                          "the payer reversed its answer and paid the expected amount", proof, recovery=True)
        if carriers:
            same_sig = [g for g in carriers if g.get("signal") == f.get("signal")]
            return _act("fold_into", R_SAME_DAY, f, "proven", proof, into=(same_sig or carriers)[0]["finding_id"],
                        note=f"{who} reversed this payment on {r.paid_on} and paid again; the new payment is listed on its own.")
        left = (exp - (best.allowed or 0)) if exp else f.get("shortfall_cents")
        hint = (f"{who} reversed this payment on {r.paid_on} and answered again ({_says(best)})"
                + (f": {_usd(left)} is still short of the expected {_usd(exp)}." if exp else "."))
        return _reclass(R_SAME_DAY, f, "replaced", "Replaced by a new answer", hint, left, "work_now", proof)
    if new_pays:
        # which line the new payments are for: the re-adjudicated claim keeps the line control numbers
        lines = _claim_lines(a)
        tag = {x.line_key: _attribute(a, x, lines) for x in pays}
        own = [x for x in pays if tag[x.line_key] == "own"]
        other = [x for x in pays if tag[x.line_key] == "other"]
        got = _units_paid(pays)
        if (own and _units_paid(own) >= a.billed_units) or (not own and not other and got >= need):
            mine_new = [x for x in new_pays if x in own] or new_pays
            p = mine_new[0]
            amt = sum(x.standing for x in mine_new)
            note = f"{who} reversed this answer on {r.paid_on} and paid this line {_usd(amt)} on {p.paid_on}."
            return _close(R_SAME_DAY, f, p, amt, note, "the payer reversed its answer and paid", proof, recovery=True)
        per = _per_unit([x for x in (other or pays) if x.status in PRIMARY_STATUSES] or (other or pays),
                        (f.get("expected_cents") or 0) / max(a.billed_units, 1) or None)
        if other and not own:
            size = _cap(per * a.billed_units, f) if per else f.get("shortfall_cents")
            hint = (f"{who} reversed this answer on {r.paid_on} and answered again: it paid the claim's other line of {a.billed_code}, "
                    f"not this one" + (f", worth about {_usd(size)} at its own rate per unit." if per else "."))
        else:
            mine = min(a.billed_units, max(need - got, 0)) or a.billed_units
            size = _cap(per * mine, f) if per else f.get("shortfall_cents")
            hint = (f"{who} reversed this answer on {r.paid_on} and paid {got:g} of {_units_text(need)}; what is left is "
                    f"{'the unit' if mine == 1 else f'{mine:g} units'}" + (f", worth about {_usd(size)} at its own rate per unit." if per else "."))
        return _reclass(R_SAME_DAY, f, "units_on_reanswer", "Reversed and partly paid", hint, size, "work_now", proof)
    if carriers:
        same_sig = [g for g in carriers if g.get("signal") == f.get("signal")]
        into = (same_sig or carriers)[0]
        return _act("fold_into", R_SAME_DAY, f, "proven", proof, into=into["finding_id"],
                    note=f"{who} reversed this answer on {r.paid_on} and answered again; the new answer is listed on its own.")
    if all(x.to_patient_only() for x in newer):
        pr = sum(x.pr_cents(COST_SHARE_CARCS) for x in newer)
        hint = (f"{who} reversed this denial on {r.paid_on} and put {_usd(pr)} on the patient (the plan's deductible, coinsurance or "
                "copay): there is nothing to appeal; the patient's share is collected as usual.")
        return _reclass(R_SAME_DAY, f, "moved_to_patient", "Moved to the patient by the payer", hint, 0, "done", proof,
                        patient_share_cents=pr)
    reasons = ", ".join(dict.fromkeys(_reasons_text(x) for x in newer))
    hint = f"{who} reversed this answer on {r.paid_on} and answered again ({reasons}): work the new answer."
    return _reclass(R_SAME_DAY, f, "replaced", "Replaced by a new answer", hint, f.get("shortfall_cents"), "work_now", proof)


def _claim_level_same_day(items: Sequence[Mapping], arch: Archive) -> list[dict]:
    """5d: a whole-claim denial whose codes the same remittance answered on another claim of the bill."""
    out = []
    for f in items:
        if not _is_open(f) or f.get("line_key") or not f.get("claim_key") or f.get("signal") != "CLAIM_DENIED_UNPAID":
            continue
        cl = arch.claims.get(f["claim_key"])
        if cl is None:
            continue
        ev = _ev(f)
        pairs = [tuple(p) for p in ev.get("unpaid") or [] if isinstance(p, (list, tuple)) and len(p) == 2]
        if not pairs:
            continue
        others = [x for c2 in arch.by_bill.get(cl.bill, []) if c2.claim_key != cl.claim_key and c2.trace == cl.trace
                  and (c2.payer == cl.payer or (c2.group and c2.group == cl.group)) for x in c2.lines if x.status != REVERSAL_STATUS]
        found: dict[tuple, list[Ans]] = {}
        for code, dos in pairs:
            hit = [x for x in others if (x.code == code or x.orig == code) and (x.dos == dos or dos is None)
                   and ((x.paid or 0) > 0 or x.to_patient_only())]
            if hit:
                found[(code, dos)] = hit
        if len(found) != len(pairs):
            continue
        rows = [x for v in found.values() for x in v]
        proof = [_proof(x, _kind(x), True) for x in rows]
        paid = [x for x in rows if x.standing > 0]
        if all(any(x.standing > 0 for x in v) for v in found.values()):
            amt = sum(x.standing for x in paid)
            note = (f"The same remittance paid {', '.join(sorted({c for c, _d in found}))} on another claim of this bill "
                    f"({_usd(amt)}): this denial is of a duplicate copy.")
            out.append(_close(R_SAME_DAY, f, paid[0], amt, note, "answered on another claim the same day", proof, recovery=False))
        else:
            hint = ("The same remittance answered these codes on another claim of this bill (paid, or put on the patient): this denial is of "
                    "a duplicate copy, and nothing is owed by the payer.")
            out.append(_reclass(R_SAME_DAY, f, "answered_same_day", "Answered on another claim the same day", hint, 0, "done", proof))
    return out


# ----------------------------------------------------------------------------------------------- 6. second payer share
def second_payer_share(conn: sqlite3.Connection, items: Iterable[Mapping], *, archive: Archive | None = None,
                       standing: Mapping[str, int] | None = None) -> list[dict]:
    """A zero-paid item that a secondary paid after the denial for less than half the expected amount, with no primary
    payment standing after it. detect.paid_cover counts a secondary payment only at half the expected amount, so the item
    stays a full denial. But a secondary pays what the primary left: its own remittance reports the prior payer's part
    (OA-23), and measured against what was left the payment is the secondary's share, not an underpayment and not a
    denial left unpaid. When the secondary's line settled everything the prior payer left (paid, its contractual cut and
    the patient's share add up to it) the item has nothing left for a payer ('done'); otherwise it keeps its size. Always
    'likely': the primary's own payment is not on file (find it before appealing)."""
    items = list(items)
    arch = archive or load_archive(conn, items, standing=standing)
    out = []
    for f, a in _line_items(items, arch, zero_paid=True):
        ans = arch.answers_for(f)
        after = [x for x in ans if x.line_key != a.line_key and x.standing > 0 and _after(x, a)]
        if not after or any(x.status in PRIMARY_STATUSES for x in after):
            continue
        sec = [x for x in after if x.status in SECONDARY_STATUSES]
        if not sec:
            continue
        exp = f.get("expected_cents") or 0
        paid = sum(x.standing for x in sec)
        if exp and paid >= 0.5 * exp:
            continue                        # detect.paid_cover counts it already
        s = sec[0]
        prior = sum(x.amount_of("OA", "23") for x in sec)
        # the secondary settled what the prior payer left: its line carries the prior payer's part and no denial of its own
        settled = prior > 0 and not any(x.denial_reasons() for x in sec)
        proof = [_proof(a, _kind(a), False)] + [_proof(x, "payment", True) for x in sec]
        if prior:
            hint = (f"{s.payer_name} paid {_usd(paid)} as secondary on {s.paid_on}; its remittance says a prior payer settled {_usd(prior)}"
                    f" of this service. That is the secondary's share, not an underpayment. The primary's own remittance is not on file: "
                    f"confirm its payment before appealing this denial.")
        else:
            hint = (f"{s.payer_name} paid {_usd(paid)} as secondary on {s.paid_on}, under half the expected amount: a secondary's share, not "
                    "an underpayment. Check the primary's answer before appealing this denial.")
        out.append(_reclass(R_SECOND, f, "secondary_paid", "Paid by a secondary", hint, 0 if settled else None,
                            "done" if settled else "work_now", proof, "likely", prior_payer_cents=prior, secondary_paid_cents=paid))
    return out


# ------------------------------------------------------------------------------------------------ 7. repeat denials
def worked_ids(conn: sqlite3.Connection, ids: Iterable[str]) -> set[str]:
    """The items a person has worked (a status other than open, a note, an appeal date), from the store's findings table:
    those are never folded away."""
    ids = sorted(set(i for i in ids if i))
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='findings'")}
    if not have or not ids:
        return set()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(findings)")}
    # as detect.save's worked(): a status a person set (a closure by the analysis is not one), a note, an appeal date
    auto = "auto_note IS NULL" if "auto_note" in cols else "1=1"
    worked = (f"status IN ('in_progress', 'appealed', 'lost', 'dismissed') OR (status = 'resolved' AND {auto}) OR notes IS NOT NULL"
              + (" OR appealed_at IS NOT NULL" if "appealed_at" in cols else ""))
    out: set[str] = set()
    for part in _chunks(ids):
        out |= {r[0] for r in conn.execute(f"SELECT finding_id FROM findings WHERE finding_id IN ({','.join('?' * len(part))}) AND ({worked})", part)}
    return out


def fold_repeat_denials(conn: sqlite3.Connection, items: Iterable[Mapping], *, archive: Archive | None = None,
                        standing: Mapping[str, int] | None = None, worked: set[str] | None = None) -> list[dict]:
    """One item per service and reason: a payer's denials of one service (bill, billed code, date of service) for one
    reason (the item's signal), on several remittances, fold into the newest. The lines of the newest remittance stay as
    they are (each is a unit of that answer); every older listing folds into the first of them and is listed in its
    evidence. A listing a person has worked (worked_ids) is never folded. Nothing is hidden: the newest denial stays."""
    items = list(items)
    arch = archive or load_archive(conn, items, standing=standing)
    groups: dict[tuple, list[tuple[Mapping, Ans]]] = defaultdict(list)
    for f, a in _line_items(items, arch, zero_paid=True, categories=("denial", "bundling", "downcoding")):
        billed = f.get("billed_code") or f.get("original_procedure_code") or f.get("procedure_code")
        # one billed line: two lines of one code on a claim (77401 and 77401-59) are two units, never repeats of each other
        groups[(a.bill, billed, a.dos, a.group or a.payer, f.get("signal"), _line_identity(a))].append((f, a))
    multi = {k: v for k, v in groups.items() if len(v) > 1}
    if worked is None:
        worked = worked_ids(conn, [f["finding_id"] for v in multi.values() for f, _a in v])
    out = []
    for v in multi.values():
        newest = max((a.paid_on or "") for _f, a in v)
        carriers = sorted((p for p in v if (p[1].paid_on or "") == newest), key=lambda p: (p[1].trace or "", p[1].line_key))
        carrier, carrier_ans = carriers[0]
        for f, a in sorted(v, key=lambda p: (p[1].paid_on or "", p[1].line_key)):
            if (a.paid_on or "") == newest or f["finding_id"] in worked:
                continue
            # a reprocessing under the payer's same claim number: its dispute clock may run from this first decision (C26)
            same_number = bool(a.claim.pccn and a.claim.pccn == carrier_ans.claim.pccn)
            proof = [_proof(a, _kind(a), True, f"Denied before on {a.paid_on} ({_reasons_text(a)})")]
            out.append(_act("fold_into", R_FOLD, f, "proven", proof, into=carrier["finding_id"],
                            note=f"The same service was denied again for the same reason on {newest}; this earlier listing is part of that item.",
                            folded={"finding_id": f["finding_id"], "payment_date": a.paid_on, "carc": f.get("carc"),
                                    "shortfall_cents": f.get("shortfall_cents"), "appeal_deadline": f.get("appeal_deadline"),
                                    "same_claim_number": same_number}))
    return out


# ------------------------------------------------------------------------------- 8. PR-group denials and CO-147
# reason -> (signal, severity, actionable, who should pay, what to fix); the Medicaid wording is in _pr_words
PR_REASONS: dict[str, tuple] = {
    "288": ("DENIED_REFERRAL", "high", "correct_and_resubmit", "the payer, once a referral from the patient's primary care doctor is on file",
            "Get the referral (a retroactive one if the plan allows it) and send a corrected claim with the referral number."),
    "27": ("DENIED_COVERAGE_DATES", "high", "review", "the plan that covered the patient on the date of service, else the patient",
           "Check the patient's coverage on the date of service, update the insurance in EMA and bill the plan that covered that day."),
    "26": ("DENIED_COVERAGE_DATES", "high", "review", "the plan that covered the patient on the date of service, else the patient",
           "Check the patient's coverage on the date of service, update the insurance in EMA and bill the plan that covered that day."),
    "200": ("DENIED_COVERAGE_DATES", "high", "review", "the plan that covered the patient on the date of service, else the patient",
            "The coverage had lapsed on that day: check for other coverage, bill it, or bill the patient."),
    "49": ("DENIED_PLAN_EXCLUSION", "low", "review", "the patient, when the visit was routine",
           "If the visit treated a problem, correct the diagnosis and send a corrected claim; if it was routine, bill the patient."),
    "204": ("DENIED_PLAN_EXCLUSION", "low", "review", "the patient, under the plan's exclusion",
            "Check the patient's benefits: bill the patient, or send a corrected claim if a covered code or diagnosis applies."),
    "147": ("DENIED_CONTRACT_RATE", "high", "review", "the payer, at the contracted rate, once the clinician is loaded on the agreement",
            "Ask the payer to link the clinician to the practice's agreement (credentialing), then to reprocess the claim."),
}
PR_GROUP_CODES = frozenset({"288", "27", "26", "200", "49", "204"})


def _pr_words(reason: str, kind: str | None) -> tuple[str, str]:
    signal, _sev, _act_, who, fix = PR_REASONS[reason]
    if kind == "medicaid" and reason != "147":
        who = "the payer: a Medicaid member cannot be billed for a covered service"
        if reason == "288":
            fix = "Get a referral from the member's assigned primary care office (a retroactive one if needed) and send a corrected claim with it."
        elif reason in ("27", "26", "200"):
            fix = "Check the member's eligibility on the date of service and bill the plan that covered that day; never bill the member."
    return who, fix


def pr_group_denials(conn: sqlite3.Connection, items: Iterable[Mapping], *, archive: Archive | None = None,
                     standing: Mapping[str, int] | None = None, rate: Callable[[str, str, str], int | None] | None = None) -> list[dict]:
    """Lines whose payer answered with a patient-group reason that is not the patient's deductible, coinsurance or copay
    (PR-288 referral absent, PR-27 coverage ended, PR-26 before coverage, PR-200 lapse, PR-49 routine service, PR-204 not
    covered by the plan), or with CO-147 (the clinician's contracted rate is not on file), that no item lists. detect.py
    reads every PR amount as the patient's share, so such a line raises nothing (C14, C50). One raise_item per line: the
    payer answered as primary, paid nothing (CO-147: or less than it allows), no item lists the line, the payer has not
    reversed or answered the line again, and no payer has paid the service since. Sized at the amount the payer would
    allow: `rate(payer_group, code, year)` (the Detector's usual rate), else the line's own allowed amount, else nothing."""
    items = list(items)
    listed = {f.get("line_key") for f in items if f.get("line_key")}
    cand: dict[str, tuple[str, str, int]] = {}
    for lk, grp, reason, amt in conn.execute(
            """SELECT a.line_key, a.group_code, a.reason_code, a.amount_cents FROM adjustments a
               WHERE a.reason_code IN ('288','27','26','200','49','204','147') AND a.amount_cents > 0 AND a.line_key IS NOT NULL"""):
        g, r = (grp or "").upper(), (reason or "").upper()
        if lk in listed or (r in PR_GROUP_CODES and g != "PR"):
            continue
        if lk not in cand or amt > cand[lk][2]:
            cand[lk] = (g, r, amt or 0)
    if not cand:
        return []
    # the candidates' own bills (the run's archive view holds only the listed items' bills)
    arch = load_archive(conn, [], standing=standing, line_keys=cand)
    cancelled = arch.cancelled()
    out = []
    for lk in sorted(cand):
        a = arch.lines.get(lk)
        g, reason, amt = cand[lk]
        if a is None or not a.dos or a.status not in PRIMARY_STATUSES or a.code in NO_CODE or (a.charge or 0) <= 100:
            continue
        if lk in cancelled:
            continue
        if reason in PR_GROUP_CODES and (a.paid or 0) > 0:
            continue
        if reason == "147" and (a.paid or 0) > 0 and not (a.allowed or 0):
            continue
        svc = arch.service(a.bill, a.code, a.dos)
        if any(x.line_key != lk and x.standing > 0 and _after(x, a) for x in svc):
            continue                        # paid since, by this payer or another
        if any(x.line_key != lk and _same_payer(x, a) and _after(x, a) and x.status != REVERSAL_STATUS for x in svc):
            continue                        # the payer answered again: its newest answer counts
        kind = payer_group_kind(a.payer_name, None)[1]
        cl_kind = _claim_kind(a)
        kind = cl_kind or kind
        signal, severity, actionable, _who, _fix = PR_REASONS[reason]
        who, fix = _pr_words(reason, kind)
        year = (a.dos or "")[:4]
        usual = rate(a.group, a.code, year) if rate else None
        expected = usual or ((a.allowed or 0) if (a.allowed or 0) > 0 else None)
        shortfall = expected
        if reason == "147" and expected and (a.allowed or 0) > 0 and usual:
            shortfall = max(usual - (a.allowed or 0), 0)
        if reason == "147" and shortfall is not None and shortfall <= 0:
            continue
        label = {"288": "referral absent", "27": "coverage had ended", "26": "coverage had not started", "200": "coverage had lapsed",
                 "49": "routine service not covered", "204": "not covered by the plan", "147": "clinician not on the payer's contract"}[reason]
        title = f"{a.code} {label} ({g}-{reason}, {a.group})"
        rarc = ",".join(a.rem) if a.rem else None
        ev = {"basis": (f"{a.group}'s usual {year} rate for {a.code}" if usual else "the payer's allowed amount on the line" if expected else
                        "no baseline for this code"),
              "carc_group": g, "who_should_pay": who, "what_to_fix": fix, "archive_rule": R_PR}
        hint = f"Who should pay: {who}. What to fix: {fix}"
        out.append(_act("raise_item", R_PR, None, "proven", [_proof(a, "denial", True)], line_key=lk, claim_key=a.claim_key,
                        signal=signal, category="denial", severity=severity, actionable=actionable, title=title,
                        expected_cents=expected, shortfall_cents=shortfall, carc=reason, carc_group=g, rarc=rarc, evidence=ev,
                        hint=hint, who_should_pay=who, what_to_fix=fix, payer_group=a.group, payment_date=a.paid_on,
                        service_date=a.dos, procedure_code=a.code))
    return out


def _claim_kind(a: Ans) -> str | None:
    """The payer kind as the claim filing indicator refines it (a Medicaid plan run by a commercial company)."""
    g = a.group or ""
    if "Medicaid" in g:
        return "medicaid"
    if "Medicare Advantage" in g:
        return "medicare_advantage"
    return None


# ------------------------------------------------------------------------------------------------------- 9. hide rules
HIDE_ANY = ("", "*", None)
HIDE_DIMENSIONS = ("payer_group", "signal", "code", "modifier", "reason")


def hide_rule(payer_group: str, signal: str | None = None, code: str | None = None, modifier: str | None = None,
              reason: str | None = None, **extra: Any) -> dict:
    """A hide rule at payer x code x modifier x reason grain, in its canonical form. Each dimension but the payer may be
    left open ('*'). `code` is a code, a comma list of codes, or a code family from codes.code_category with an '@'
    ('@em' for office visits, '@drug', '@skin_substitute', ...). `modifier` is one modifier the line must carry, or 'none'
    for a line with none. `reason` is a reason code, with or without its group ('97' or 'CO-97'). A rule with only the
    payer and a signal is the old payer x signal rule."""
    def norm(v: Any) -> str:
        return "*" if v in HIDE_ANY else str(v).strip()
    out = {"payer_group": str(payer_group or "").strip(), "signal": norm(signal).upper() if norm(signal) != "*" else "*",
           "code": norm(code).upper() if not norm(code).startswith("@") else norm(code).lower(),
           "modifier": norm(modifier).upper() if norm(modifier).lower() != "none" else "none",
           "reason": norm(reason).upper().replace(" ", "")}
    out.update({k: v for k, v in extra.items() if k not in out})
    return out


def hide_key(rule: Mapping) -> str:
    """One string per rule (payer|signal|code|modifier|reason): what a journal entry and the table's key can carry."""
    r = hide_rule(**{k: rule.get(k) for k in HIDE_DIMENSIONS})
    return "|".join(r[k] for k in HIDE_DIMENSIONS)


def _reason_code(v: str) -> tuple[str, str]:
    m = re.match(r"^(CO|PR|OA|PI|CR)-?(.+)$", v or "")
    return (m.group(1), m.group(2)) if m else ("", v or "")


def _item_codes(item: Mapping) -> set[str]:
    return {str(c).upper() for c in (item.get("procedure_code"), item.get("original_procedure_code"), item.get("billed_code")) if c}


def _item_mods(item: Mapping) -> set[str]:
    m = item.get("modifiers")
    if isinstance(m, (list, tuple)):
        return {str(x).upper() for x in m if x}
    return {x.strip().upper() for x in str(m or "").split(",") if x.strip()}


def hide_match(rule: Mapping, item: Mapping) -> bool:
    """Whether a hide rule covers an item (a finding dict or a findings row): same payer group, and each dimension the
    rule names. Never an unanswered claim (NO_ANSWER, REJECTED_PRE_PAYER: money nobody has asked for yet)."""
    r = hide_rule(**{k: rule.get(k) for k in HIDE_DIMENSIONS})
    if item.get("signal") in ("NO_ANSWER", "REJECTED_PRE_PAYER") or not r["payer_group"]:
        return False
    if (item.get("payer_group") or "") != r["payer_group"]:
        return False
    if r["signal"] != "*" and (item.get("signal") or "").upper() != r["signal"]:
        return False
    if r["code"] != "*":
        if r["code"].startswith("@"):
            fam = r["code"][1:]
            if not any(code_category(c) == fam for c in _item_codes(item)):
                return False
        elif not (_item_codes(item) & {c.strip() for c in r["code"].split(",") if c.strip()}):
            return False
    if r["modifier"] != "*":
        mods = _item_mods(item)
        if (r["modifier"] == "none" and mods) or (r["modifier"] != "none" and r["modifier"] not in mods):
            return False
    if r["reason"] != "*":
        grp, code = _reason_code(r["reason"])
        if (item.get("carc") or "").upper() != code:
            return False
        own_grp = (item.get("carc_group") or _ev(item).get("carc_group") or "").upper()
        if grp and own_grp and own_grp != grp:
            return False
    return True


def hide_sql(rule: Mapping, alias: str = "f") -> tuple[str, list]:
    """The SQL condition (and its parameters) for the findings rows a hide rule covers, for actions._hide_matching:
    one condition on the row's own columns, no subquery. A code family compares code_category, the family of the code
    the payer answered (hide_match also looks at the billed code)."""
    r = hide_rule(**{k: rule.get(k) for k in HIDE_DIMENSIONS})
    a = alias
    cond, params = [f"{a}.payer_group = ?", f"{a}.signal NOT IN ('NO_ANSWER', 'REJECTED_PRE_PAYER')"], [r["payer_group"]]
    if r["signal"] != "*":
        cond.append(f"{a}.signal = ?")
        params.append(r["signal"])
    if r["code"] != "*":
        if r["code"].startswith("@"):
            cond.append(f"{a}.code_category = ?")
            params.append(r["code"][1:])
        else:
            codes = [c.strip() for c in r["code"].split(",") if c.strip()]
            q = ",".join("?" * len(codes))
            cond.append(f"({a}.procedure_code IN ({q}) OR {a}.original_procedure_code IN ({q}) OR {a}.billed_code IN ({q}))")
            params += codes * 3
    if r["modifier"] != "*":
        if r["modifier"] == "none":
            cond.append(f"COALESCE({a}.modifiers, '') = ''")
        else:
            cond.append(f"(',' || REPLACE(COALESCE({a}.modifiers, ''), ' ', '') || ',') LIKE ?")
            params.append(f"%,{r['modifier']},%")
    if r["reason"] != "*":
        cond.append(f"{a}.carc = ?")
        params.append(_reason_code(r["reason"])[1])
    return " AND ".join(cond), params


def read_hide_rules(conn: sqlite3.Connection) -> list[dict]:
    """The hide rules in force from the store's hide_rules table, at whatever grain its columns hold (payer x signal
    today; code, modifier and reason once the wiring adds them)."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='hide_rules'").fetchone():
        return []
    cols = [r[1] for r in conn.execute("PRAGMA table_info(hide_rules)")]
    want = [c for c in ("payer_group", "signal", "code", "modifier", "reason", "note", "at") if c in cols]
    where = "WHERE removed_at IS NULL" if "removed_at" in cols else ""
    return [hide_rule(**dict(zip(want, r))) for r in conn.execute(f"SELECT {', '.join(want)} FROM hide_rules {where}")]


def hide_matches(conn: sqlite3.Connection, items: Iterable[Mapping], *, rules: Sequence[Mapping] | None = None) -> list[dict]:
    """The open items each hide rule covers (the first rule that covers an item names it)."""
    rules = list(rules) if rules is not None else read_hide_rules(conn)
    if not rules:
        return []
    canon = [(hide_rule(**{k: r.get(k) for k in HIDE_DIMENSIONS}), r) for r in rules]
    out = []
    for f in items:
        if not _is_open(f):
            continue
        for c, raw in canon:
            if hide_match(c, f):
                out.append(_act("hide_match", R_HIDE, f, "proven", [], hide_rule=c, hide_key=hide_key(c), note=raw.get("note")))
                break
    return out


def hide_patterns(items: Iterable[Mapping], *, min_items: int = 1) -> list[dict]:
    """The patterns a person can decide on, at the hide grain (payer group x code x modifier x reason), with how many items
    each has open, their dollars, and how many of all its zero-paid items (open and closed) the archive later saw paid:
    the column the owner reads before accepting a pattern (amendment 8.2)."""
    pat: dict[tuple, dict] = {}
    for f in items:
        if not f.get("line_key") or (f.get("paid_cents") or 0) > 0 or f.get("category") not in ("denial", "bundling"):
            continue
        mods = sorted(_item_mods(f) & {"25", "57", "24", "59", "XS", "XE", "XU", "XP", "76", "79"})
        key = (f.get("payer_group") or "", f.get("billed_code") or f.get("procedure_code") or "", mods[0] if mods else "none", f.get("carc") or "")
        p = pat.setdefault(key, {"payer_group": key[0], "code": key[1], "modifier": key[2], "reason": key[3], "open": 0, "open_cents": 0,
                                 "all": 0, "paid_later": 0})
        p["all"] += 1
        if f.get("auto_status"):
            p["paid_later"] += 1
        else:
            p["open"] += 1
            p["open_cents"] += f.get("shortfall_cents") or 0
    out = [dict(p, share_paid_later=round(p["paid_later"] / p["all"], 3) if p["all"] else None) for p in pat.values() if p["open"] >= min_items]
    return sorted(out, key=lambda p: (-p["open_cents"], -p["open"], p["payer_group"], p["code"]))


# ---------------------------------------------------------------------------------- 10. drugs and skin substitutes
DRUG_FAMILIES = ("drug", "skin_substitute")


def drug_rates(conn: sqlite3.Connection, codes: Iterable[str]) -> dict[tuple, list[tuple[float, str]]]:
    """(payer group, code) -> [(allowed per unit, service year)] over every primary payment of these codes that stands
    unreversed in the archive (paid and allowed above $0). One pass over the lines of these codes."""
    codes = sorted({c for c in codes if c})
    out: dict[tuple, list[tuple[float, str]]] = defaultdict(list)
    if not codes:
        return out
    conn.execute("DROP TABLE IF EXISTS temp.b2_codes")
    conn.execute("CREATE TEMP TABLE b2_codes (code TEXT PRIMARY KEY)")
    try:
        conn.executemany("INSERT OR IGNORE INTO temp.b2_codes VALUES (?)", [(c,) for c in codes])
        for code, payer, cfi, allowed, units, ounits, yr in conn.execute(
                """SELECT l.procedure_code, t.payer_name, c.claim_filing_indicator, l.allowed_cents, l.paid_units, l.original_units,
                          substr(COALESCE(l.service_date, l.payment_date, t.payment_date), 1, 4)
                   FROM lines l JOIN claims c ON c.claim_key = l.claim_key JOIN transactions t ON t.transaction_key = l.transaction_key
                   WHERE l.procedure_code IN (SELECT code FROM temp.b2_codes) AND COALESCE(t.superseded, 0) = 0
                     AND l.claim_status_code IN ('1', '19') AND l.paid_cents > 0 AND l.allowed_cents > 0"""):
            u = abs(units or ounits or 1) or 1
            out[(payer_group_kind(payer, cfi)[0], code)].append((allowed / u, yr or ""))
    finally:
        conn.execute("DROP TABLE IF EXISTS temp.b2_codes")
    return out


def _drug_rate(points: Sequence[tuple[float, str]], year: str, min_lines: int) -> tuple[float, list[float], str] | None:
    """The payer's median allowed per unit for the item's service year, else the years next to it."""
    def pick(ys):
        return [p for p, y in points if y in ys]
    if not year.isdigit():
        vals = [p for p, _y in points]
        return (median(vals), vals, "all years") if len(vals) >= min_lines else None
    for ys, label in (({year}, year), ({str(int(year) - 1), year, str(int(year) + 1)}, f"{int(year) - 1} to {int(year) + 1}")):
        vals = pick(ys)
        if len(vals) >= min_lines:
            return median(vals), vals, label
    return None


def drug_expected(conn: sqlite3.Connection, items: Iterable[Mapping], *, archive: Archive | None = None,
                  standing: Mapping[str, int] | None = None, min_lines: int = MIN_DRUG_LINES,
                  rates: Mapping[tuple, list] | None = None) -> list[dict]:
    """A fair expected amount for open drug and skin-substitute items that have none (their units keep them out of every
    baseline, so they are valued at $0): the same payer group's own median allowed per unit for the code, over its primary
    payments in the item's service year (else the years next to it, at least `min_lines` lines), times the units the item
    billed. A whole-claim denial gets it for each drug code it lists unpaid. No such payments: no amount (never a guess)."""
    items = list(items)
    want = []
    for f in items:
        if not _is_open(f) or (f.get("expected_cents") or 0) > 0:
            continue
        if f.get("line_key"):
            if any(code_category(c) in DRUG_FAMILIES for c in _item_codes(f)):
                want.append(f)
        elif f.get("claim_key") and any(code_category(p[0]) in DRUG_FAMILIES for p in _ev(f).get("unpaid") or []
                                        if isinstance(p, (list, tuple)) and p):
            want.append(f)
    if not want:
        return []
    arch = archive or load_archive(conn, want, standing=standing)
    codes = {c for f in want for c in _item_codes(f)} | {p[0] for f in want for p in _ev(f).get("unpaid") or [] if isinstance(p, (list, tuple)) and p}
    table = rates if rates is not None else drug_rates(conn, [c for c in codes if code_category(c) in DRUG_FAMILIES])
    out = []
    for f in want:
        grp = f.get("payer_group") or ""
        parts = []          # (code, units, per unit, lines, years label, values)
        if f.get("line_key"):
            a = arch.lines.get(f["line_key"])
            code = next((c for c in (f.get("billed_code"), f.get("original_procedure_code"), f.get("procedure_code"))
                         if c and code_category(c) in DRUG_FAMILIES), None)
            units = a.billed_units if a else 1
            pick = _drug_rate(table.get((grp, code), []), (f.get("service_date") or "")[:4], min_lines) if code else None
            if pick:
                parts.append((code, units, pick[0], len(pick[1]), pick[2], pick[1]))
        else:
            cl = arch.claims.get(f.get("claim_key"))
            for code, dos in [tuple(p) for p in _ev(f).get("unpaid") or [] if isinstance(p, (list, tuple)) and len(p) == 2]:
                if code_category(code) not in DRUG_FAMILIES:
                    continue
                units = sum(x.billed_units for x in (cl.lines if cl else []) if x.code == code and x.dos == dos) or 1
                pick = _drug_rate(table.get((grp, code), []), (dos or f.get("service_date") or "")[:4], min_lines)
                if pick:
                    parts.append((code, units, pick[0], len(pick[1]), pick[2], pick[1]))
        if not parts:
            continue
        added = int(round(sum(u * p for _c, u, p, _n, _y, _v in parts)))
        charge = f.get("charge_cents") or 0
        if f.get("line_key") and charge > 0:
            added = min(added, charge)          # a payer allows the lesser of the charge and its rate
        if added <= 0:
            continue
        expected = (f.get("expected_cents") or 0) + added
        paid = f.get("allowed_cents") or 0 if f.get("line_key") else 0
        shortfall = max(expected - paid, 0)
        basis = "; ".join(f"{grp}'s own median allowed for {c} in {y}: {_usd(p)} a unit on {n} paid lines "
                          f"({_usd(min(v))} to {_usd(max(v))}), times {u:g} units billed" for c, u, p, n, y, v in parts)
        proof = [{"date": None, "source": "835", "kind": "baseline", "payer": grp, "amount_cents": int(round(p)),
                  "codes": [{"code": c, "meaning": code_category(c).replace("_", " ")}], "decisive": True,
                  "text": f"{grp} allowed {c} at a median of {_usd(p)} a unit on {n} paid lines ({y})"} for c, u, p, n, y, v in parts]
        out.append(_act("expected_cents", R_DRUG, f, "proven", proof, expected_cents=expected, shortfall_cents=shortfall, basis=basis,
                        added_cents=added))
    return out


# ----------------------------------------------------------------------------------------------- the combined plan
RULE_FUNCS: dict[str, Callable] = {
    R_SAME_DAY: same_day_answers, R_TOPPED_UP: short_pay_topped_up, R_UNITS: units_split, R_PAID_BEFORE: same_payer_paid_before,
    R_OTHER_PAYER: other_payer_paid_first, R_SECOND: second_payer_share, R_FOLD: fold_repeat_denials, R_PR: pr_group_denials,
    R_DRUG: drug_expected, R_HIDE: hide_matches,
}


def evaluate(conn: sqlite3.Connection, items: Iterable[Mapping], *, standing: Mapping[str, int] | None = None,
             rate: Callable[[str, str, str], int | None] | None = None, rules: Iterable[str] | None = None,
             hide_rules: Sequence[Mapping] | None = None, worked: set[str] | None = None) -> dict:
    """Run the rules on the run's items and settle what happens to each:
      - one primary action per item, by PRECEDENCE (a closure the archive proves, then the payer's replacement of the
        item's answer, then what happened before it, then the size of what is left);
      - fold_into for repeat listings (rule 7) and for answers the payer replaced (rule 5), unless the item closes;
        a fold into an item that itself folds goes on to the item that carries it;
      - expected_cents (rule 10) and hide_match (rule 9) for items that stay;
      - raise_item (rule 8) for lines no item lists.
    Returns {'actions': [...] (what to apply, in order), 'by_rule': {rule: [actions each rule returned]}, 'summary':
    {rule: {action: count}}}."""
    items = [f for f in items]
    wanted = [r for r in RULES if rules is None or r in set(rules)]
    claim_items = [f for f in items if _is_open(f) and (f.get("line_key") or f.get("claim_key"))]
    arch = load_archive(conn, claim_items, standing=standing)
    by_rule: dict[str, list[dict]] = {}
    for r in wanted:
        if r == R_HIDE:
            continue
        fn = RULE_FUNCS[r]
        if r == R_PR:
            by_rule[r] = fn(conn, items, archive=arch, standing=standing, rate=rate)
        elif r == R_FOLD:
            by_rule[r] = fn(conn, items, archive=arch, standing=standing, worked=worked)
        else:
            by_rule[r] = fn(conn, items, archive=arch, standing=standing)
    primary: dict[str, dict] = {}
    order = {k: i for i, k in enumerate(PRECEDENCE)}

    def rank(act: dict) -> tuple:
        # closures and the payer's own replacement of the answer first; then what happened before the answer, a 'proven'
        # reading ahead of a 'likely' one (Medicare paid as primary outranks a Medigap's own secondary payment)
        i = order[(act["rule"], act["action"])]
        return (0 if i <= order[(R_SAME_DAY, "reclassify")] else 1, 0 if act.get("strength") == "proven" else 1, i)
    for r, acts in by_rule.items():
        for act in acts:
            k = (r, act["action"])
            if act.get("finding_id") and k in order:
                cur = primary.get(act["finding_id"])
                if cur is None or rank(act) < rank(cur):
                    primary[act["finding_id"]] = act
    closed = {fid for fid, a in primary.items() if a["action"] == "close_with_proof"}
    folds: dict[str, dict] = {fid: a for fid, a in primary.items() if a["action"] == "fold_into"}
    for act in by_rule.get(R_FOLD, []):
        fid = act["finding_id"]
        if fid in closed or fid in folds:
            continue
        cur = primary.get(fid)
        if cur is not None and cur["action"] == "reclassify":
            act = dict(act, folded=dict(act.get("folded") or {}, reclass=cur.get("reclass"), label=cur.get("label")))
        folds[fid] = act
    # a fold into an item that folds itself goes on to its carrier; a fold into a closed item stays a fold (it disappears into
    # the closed carrier's evidence)
    def carrier(fid: str, seen: set) -> str:
        nxt = folds.get(fid)
        if nxt is None or fid in seen:
            return fid
        seen.add(fid)
        return carrier(nxt["into"], seen)
    for fid, act in list(folds.items()):
        tgt = carrier(act["into"], {fid})
        if tgt == fid:
            del folds[fid]              # a cycle: keep the item
            continue
        if tgt != act["into"]:
            folds[fid] = dict(act, into=tgt)
    gone = closed | set(folds)
    actions: list[dict] = []
    for fid in sorted(primary):
        a = primary[fid]
        if a["action"] in ("close_with_proof", "reclassify") and fid not in folds:
            actions.append(a)
    actions += [folds[fid] for fid in sorted(folds)]
    if R_DRUG in by_rule:
        actions += [a for a in by_rule[R_DRUG] if a["finding_id"] not in gone]
    raised = by_rule.get(R_PR, [])
    actions += raised
    if R_HIDE in wanted:
        stay = [f for f in items if f.get("finding_id") not in gone]
        hides = hide_matches(conn, stay, rules=hide_rules)
        by_rule[R_HIDE] = hides
        actions += hides
    summary = {r: dict(Counter(a["action"] for a in acts)) for r, acts in by_rule.items()}
    summary["applied"] = dict(Counter(f"{a['rule']}:{a['action']}" for a in actions))
    return {"actions": actions, "by_rule": by_rule, "summary": summary}


# --------------------------------------------------------------------------------------------- applying to a Detector
def run_on_detector(det, *, rules: Iterable[str] | None = None, hide_rules: Sequence[Mapping] | None = None) -> dict:
    """The one call the detection wiring makes (Detector.run, after emit_second_rates and before resolve_elsewhere):
    evaluate the rules on det.findings with the Detector's own ledger and usual rates, and apply the result in memory.
    Returns the evaluation's summary."""
    def rate(group: str, code: str, year: str) -> int | None:
        try:
            return det.usual_rate({"payer_group": group, "year": year or "", "provider_tier": "unknown", "pos_class": "office"}, code)[0]
        except Exception:
            return None
    res = evaluate(det.conn, list(det.findings.values()), standing=getattr(det, "standing", None), rate=rate, rules=rules,
                   hide_rules=hide_rules if hide_rules is not None else [])
    res["summary"]["applied_counts"] = apply_actions(det, res["actions"])
    return res["summary"]


def _merge_evidence(f: dict, key: str, value: Any) -> None:
    ev = _ev(f)
    ev[key] = value
    f["evidence"] = json.dumps(ev, default=str)


def apply_actions(det, actions: Iterable[Mapping]) -> dict:
    """Apply evaluate()'s actions to a Detector's in-memory items before save:
      close_with_proof  Detector._mark_paid with the action's payment; not counted as a recovery where the money was paid
                        with the answer itself (counts_as_recovery False); the hint is cleared
      reclassify        the hint, the new size (shortfall_cents) and evidence['archive_rule'] (reclass, label, state_hint,
                        strength, proof); the signal and id stay, so the item keeps its history and its status
      fold_into         the item leaves the run; the carrier's evidence['folded'] lists it with its date and reason
      raise_item        Detector._emit on the line, then the hint and the rule's evidence
      expected_cents    expected_cents and shortfall_cents, with evidence['expected_basis']
      hide_match        nothing here: hides apply after save (actions.apply_hide_rules)
    Returns counts by action."""
    from .detect import _fid            # at call time: detect.py is loaded (it calls this), and this module never imports it at load
    n: Counter = Counter()
    for act in actions:
        kind = act["action"]
        fid = act.get("finding_id")
        f = det.findings.get(fid) if fid else None
        if kind == "close_with_proof" and f is not None and not f.get("auto_status"):
            det._mark_paid(f, act["paid_date"], act["paid_cents"], act["note"], dict(act["detail"]))
            if not act.get("counts_as_recovery"):
                f["recovered_cents"] = 0
            f["hint"] = None
            _merge_evidence(f, "archive_rule", {k: act.get(k) for k in ("rule", "action", "strength", "proof", "possible_overpayment_cents")
                                                if act.get(k) is not None})
            n[kind] += 1
        elif kind == "reclassify" and f is not None and not f.get("auto_status"):
            f["hint"] = act["hint"]
            if act.get("shortfall_cents") is not None:
                f["shortfall_cents"] = act["shortfall_cents"]
            _merge_evidence(f, "archive_rule", {k: act.get(k) for k in ("rule", "reclass", "label", "state_hint", "strength", "proof",
                                                                        "possible_overpayment_cents", "paid_by", "units_billed",
                                                                        "units_paid", "patient_share_left_cents") if act.get(k) is not None})
            n[kind] += 1
        elif kind == "fold_into" and f is not None:
            into = det.findings.get(act["into"])
            if into is None:
                continue
            ev = _ev(into)
            folded = ev.get("folded") or []
            info = dict(act.get("folded") or {"finding_id": fid, "payment_date": f.get("payment_date"), "carc": f.get("carc"),
                                              "shortfall_cents": f.get("shortfall_cents"), "appeal_deadline": f.get("appeal_deadline")},
                        rule=act["rule"], signal=f.get("signal"), note=act.get("note"))
            folded.append(info)
            ev["folded"] = folded
            if info.get("same_claim_number") and info.get("appeal_deadline") and \
                    info["appeal_deadline"] < (ev.get("first_decision_deadline") or "~"):
                # the payer reprocessed the same claim number: its dispute clock may run from the first decision (C26)
                ev["first_decision_deadline"] = info["appeal_deadline"]
            into["evidence"] = json.dumps(ev, default=str)
            del det.findings[fid]
            n[kind] += 1
        elif kind == "raise_item":
            line = next((x for x in det.lines_by_claim.get(act["claim_key"], []) if x["line_key"] == act["line_key"]), None)
            if line is None:
                continue
            det._emit(act["signal"], act["category"], act["severity"], act["actionable"], act["title"], line, act["expected_cents"],
                      act["shortfall_cents"], act["carc"], act["rarc"], dict(act["evidence"], proof=act["proof"]))
            new = det.findings.get(_fid(act["signal"], act["line_key"]))
            if new is not None:
                new["hint"] = act["hint"]
            n[kind] += 1
        elif kind == "expected_cents" and f is not None and not f.get("auto_status"):
            f["expected_cents"], f["shortfall_cents"] = act["expected_cents"], act["shortfall_cents"]
            _merge_evidence(f, "expected_basis", act["basis"])
            n[kind] += 1
    return dict(n)
