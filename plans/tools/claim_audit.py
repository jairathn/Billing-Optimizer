#!/usr/bin/env python3
"""claim_audit.py: the independent claim auditor (first pass, rules version A15).

What it does
  1. Reads a copy of the RemitWatch store READ-ONLY (sqlite URI, mode=ro): the raw 835 tables (transactions, claims,
     lines, adjustments = CAS, remarks = RARC, plb), the 837 tables (claims_837, lines_837), EMA's weekly exports
     (ema_rows, ema_sources) and RemitWatch's own items (findings, finding_events, finding_heirs, faxes if present).
  2. Decides, from that raw data alone, the truth of every unit of service:
        unit = (bill = claim id without its resubmission suffix, billed code, component G/26/TC, date of service,
                unit index 1..units billed)
  3. Classifies every RemitWatch finding against that truth, and lists every owed unit that has no item.

It imports nothing from remitwatch/ (independent of detect.py by design: a mistake in the detector must not be repeated
in the check). It prints counts, dollars, codes, payer families, the store's keys and finding ids only. Its output
files (truth table, verdicts) hold claim ids and dates: they are written only to the private folder given with --out.

Usage
  python3 -I claim_audit.py --selftest
  python3 -I claim_audit.py --db STORE.sqlite --out PRIVATE_DIR [--as-of YYYY-MM-DD]
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
import sys
import urllib.parse
from collections import Counter, defaultdict

RULES_VERSION = "A15"
SCOPE_START = "2026-01-01"
PRIMARY = ("1", "19", "4")
SECONDARY = ("2", "3", "20", "21")
REVERSAL = "22"

# Unit states. DONE: nothing more is owed by the payer for the unit. OWED: money owed by the payer. The rest: unknown.
DONE = {"PAID", "MOVED_TO_PATIENT", "SETTLED_SECONDARY", "VOIDED", "ZERO_CHARGE", "NOT_PAYABLE"}
OWED = {"DENIED", "SHORT", "TAKEN_BACK", "NEVER_ANSWERED", "WORKED_WAITING"}
UNKNOWN = {"SECONDARY_ONLY", "IN_PROCESS", "NO_FEED", "FEED_STOPPED", "EMA_PAID_NO_REMIT", "EMA_VOIDED",
           "NOT_SENT_UNKNOWN"}

# Item kinds by RemitWatch signal. D: about an unpaid unit (denial, bundling, claim denial). U: about a paid unit
# (underpayment, downcode, code change). N: claim sent, no answer. T: a PLB take-back. A: aggregate, not one unit.
U_SIGNALS = {"UNDERPAID_VS_OWN_RATE", "MPPR_EXCESS", "DOWNCODE_SVC06", "DOWNCODE_LEVEL_REDUCED", "DOWNCODE_RATE",
             "DOWNCODE_CHARGE", "CODE_CHANGED"}
N_SIGNALS = {"NO_ANSWER", "REJECTED_PRE_PAYER"}
T_SIGNALS = {"TAKEBACK"}
A_SIGNALS = {"FEE_SKIM", "CONTRACT_RATE_GAP", "MA_BELOW_MEDICARE", "MIPS_ADJUSTMENT", "SEQUESTRATION", "SECOND_RATE",
             "G2211_DENIED", "MPPR_POLICY", "CHARGE_BELOW_RATE"}

OPEN_STATUSES = ("open", "in_progress", "appealed")
PERSON_CLOSED = ("lost", "dismissed")

# Modifiers that legitimately change the allowed amount: no "paid short" call is made on such lines.
REDUCING_MODS = {"50", "52", "53", "54", "55", "56", "62", "66", "73", "74", "80", "81", "82", "AS"}
MPPR_MODS = {"51"}
# Not denials: contractual reduction to the fee schedule, sequestration, MIPS, prior payer, MPPR, interest.
NON_DENIAL_CARCS = {"45", "253", "237", "23", "59", "94", "144", "A7"}
COB_CARCS = {"22", "109"}
CORRECTABLE_CARCS = {"4", "5", "6", "16", "125", "146", "181", "182", "189", "206", "252", "A1", "B7", "MA130"}

CID_RE = re.compile(r"^([A-Z]{2}\d{2}[A-Z0-9]{5})(C\d{0,3})?$")
EMBED_RE = re.compile(r"([A-Z]{2}\d{2}[A-Z0-9]{5})(C\d{1,3})")

GENERIC_PAYER_IDS = {"ZELIS", "ECHOH", "99999", "MEDSUP"}
# Payer ids that are one payer in the 835 feed (public payer ids, nothing practice-specific).
ID_MERGE = {"ANTHM": "BCBS-ANTHEM", "00660": "BCBS-ANTHEM", "00630": "BCBS-ANTHEM", "ANTHEM": "BCBS-ANTHEM",
            "HUMANA": "HUMANA", "61101": "HUMANA", "UMR01": "UMR", "39026": "UMR",
            "00882": "MEDICARE-RR", "00320": "MEDICARE-RR", "15102": "MEDICARE-KY", "08102": "MEDICARE-IN"}
_NAME_STOP = {"INC", "LLC", "CO", "COMPANY", "CORP", "CORPORATION", "THE", "LTD"}


# ----------------------------------------------------------------------------------------------------------- helpers
def iso(s):
    if s is None:
        return None
    s = str(s).strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    if len(s) >= 8 and s[:8].isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return None


def days_between(a, b):
    """b - a in days (ISO dates)."""
    return (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days


def add_days(a, n):
    return (dt.date.fromisoformat(a) + dt.timedelta(days=n)).isoformat()


def split_claim_id(pcn):
    s = (pcn or "").strip().upper()
    m = CID_RE.match(s)
    if m:
        return m.group(1), (m.group(2) or "")
    return s, ""


def parse_mods(v):
    if v is None:
        return ()
    s = str(v).strip()
    if not s:
        return ()
    if s.startswith("["):
        try:
            return tuple(str(x).strip().upper() for x in json.loads(s) if str(x).strip())
        except ValueError:
            pass
    return tuple(x.strip().upper() for x in re.split(r"[,\s:;|]+", s) if x.strip())


CODE_WITH_MODS_RE = re.compile(r"^([A-Z0-9]{5})[\s\-:]+([A-Z0-9][A-Z0-9\s\-:]*)$")


def split_code(code):
    """A payer that writes the modifiers into the code field ("99213 25", "99213-25"): (code, extra modifiers)."""
    c = (code or "").strip().upper()
    m_ = CODE_WITH_MODS_RE.match(c)
    if not m_:
        return c, ()
    extra = tuple(x for x in re.split(r"[\s\-:]+", m_.group(2)) if len(x) == 2)
    return m_.group(1), extra


def component(mods):
    if "26" in mods:
        return "26"
    if "TC" in mods:
        return "TC"
    return "G"


def units_of(x):
    try:
        v = abs(float(x))
    except (TypeError, ValueError):
        return 0
    if v <= 0:
        return 0
    return max(1, int(round(v)))


def norm_name(n):
    w = re.sub(r"[^A-Z0-9]+", " ", (n or "").upper()).split()
    while w and w[-1] in _NAME_STOP:
        w.pop()
    return " ".join(w)


def hkey(*parts):
    """A one-way key for linking the same patient across bills in memory. Never written or printed."""
    s = "|".join(re.sub(r"[^A-Z0-9]", "", (x or "").upper()) for x in parts)
    if not s.strip("|"):
        return None
    return hashlib.sha256(s.encode()).hexdigest()[:20]


def specific_payer_id(pid):
    p = (pid or "").strip().upper()
    if not p or p in GENERIC_PAYER_IDS or p.startswith("ZP"):
        return None
    if p.isdigit() and len(p) >= 9:  # a tax id or NPI in the payer-id slot, not a payer id
        return None
    return p


class Families:
    """Payer identity, learned from the 835 feed: a specific payer id names the payer; a clearinghouse id falls back to
    the payer's name, mapped to the id that name carries elsewhere in the feed."""

    def __init__(self):
        self.name_to_fam = {}
        self.labels = {}

    @staticmethod
    def id_fam(sid):
        return ID_MERGE.get(sid, "ID:" + sid)

    def learn(self, pairs):
        cnt = defaultdict(Counter)
        lab = defaultdict(Counter)
        for name, pid, n in pairs:
            sid = specific_payer_id(pid)
            if sid:
                cnt[norm_name(name)][self.id_fam(sid)] += n
        self.name_to_fam = {nm: c.most_common(1)[0][0] for nm, c in cnt.items() if nm}
        for name, pid, n in pairs:
            lab[self.of(name, pid)][norm_name(name)] += n
        self.labels = {f: c.most_common(1)[0][0] for f, c in lab.items()}

    def of(self, name, pid):
        sid = specific_payer_id(pid)
        if sid:
            return self.id_fam(sid)
        nm = norm_name(name)
        return self.name_to_fam.get(nm, "NM:" + nm)

    def label(self, fam):
        return self.labels.get(fam, fam)


def pct(values, q):
    if not values:
        return None
    v = sorted(values)
    i = min(len(v) - 1, max(0, int(round(q * (len(v) - 1)))))
    return v[i]


# ------------------------------------------------------------------------------------------------------- data model
class Proc:
    """One claim processing (one CLP loop) of one payer."""
    __slots__ = ("ck", "tk", "seq", "pcn", "bill", "sfx", "fam", "st", "freq", "prod", "clp07", "pos", "date", "ordk",
                 "charge", "paid", "ccas", "crarc", "lines", "npi", "trace", "sup", "copy_of", "cancelled_by", "cancels",
                 "xover", "nlines_raw", "stmt", "pk", "mks")


class Line:
    """One service line of one processing (or a virtual line for a processing reported at claim level only)."""
    __slots__ = ("lk", "p", "seq", "code", "adj", "mods", "comp", "dos", "charge", "paid", "paid_tb", "allowed", "asrc",
                 "ub", "up", "cas", "rarc", "cancelled_by", "cancels", "svc", "changed", "npi", "virtual", "ans")

    def standing(self):
        return (self.cancelled_by is None and self.p.cancelled_by is None and self.p.copy_of is None
                and self.p.st != REVERSAL and self.p.freq != "8")


class Ans:
    """One processing's answer for one service group: its lines for that service, added up. A payer that splits one
    billed line into several (different charges per unit: copay, sequestration, contractual parts) answers one line."""
    __slots__ = ("p", "svc", "lines", "lk", "ub", "up", "charge", "paid", "paid_tb", "allowed", "cas", "rarc", "mods",
                 "changed", "npi", "virtual", "split", "standing")

    def apu(self):
        """Allowed per paid unit, net of any take-back."""
        return (self.allowed - (self.paid - self.paid_tb)) / max(1, self.up)


class Svc:
    """A service group: (bill, code, component, date of service). Its units are 1..U."""
    __slots__ = ("key", "bill", "code", "comp", "dos", "lines", "answers", "sends", "ema", "emast", "U", "units",
                 "in_scope", "scope_basis", "charge_pu", "pks")

    def __init__(self, key):
        self.key = key
        self.bill, self.code, self.comp, self.dos = key
        self.lines = []
        self.answers = []
        self.sends = []      # (date, fam, freq, pcn, units, charge, source, 837 record key)
        self.ema = []        # EMA charge lines: (pcn, units, charge, bill_created, payer_fam, status, created_at)
        self.emast = []      # EMA claim-status rows: (pcn, status, submitted, fam, created_at, rows_in_export)
        self.U = 0
        self.units = []      # list of unit tuples (see UNIT_FIELDS)
        self.in_scope = False
        self.scope_basis = ""
        self.charge_pu = 0
        self.pks = set()     # one-way patient keys, in memory only


# unit tuple: state, sub, carc, fam, gov_line_key, gov_date, owed_cents (per unit), basis, window_end, inside(bool)
UNIT_FIELDS = ("state", "sub", "carc", "fam", "gov_line_key", "gov_date", "owed", "basis", "window_end", "inside")
# states a payment of the same service on another bill (same patient, code, component and date) settles
LINKABLE = {"DENIED", "NEVER_ANSWERED", "WORKED_WAITING", "SECONDARY_ONLY", "IN_PROCESS", "NO_FEED", "FEED_STOPPED",
            "EMA_PAID_NO_REMIT", "NOT_SENT_UNKNOWN", "TAKEN_BACK"}
DUP_CARCS = {"18", "B13"}
# a payer that (almost) never pays a code is "not payable" only when the denial itself says bundled / not paid
# separately / zero contractual rate; authorization, coverage, eligibility or enrolment denials stay owed
NOT_SEPARATELY_PAID_CARCS = {"97", "234", "45"}
# a level-of-service reduction next to a patient share means the service was allowed at a lower level
LEVEL_REDUCTION_CARCS = {"186", "150"}


# --------------------------------------------------------------------------------------------------------- loading
def table_names(con):
    return {r[0] for r in con.execute("select name from sqlite_master where type='table'")}


def column_names(con, table):
    return [r[1] for r in con.execute(f"pragma table_info({table})")]


def select(con, table, wanted):
    have = set(column_names(con, table))
    cols = [c if c in have else "NULL" for c in wanted]
    return con.execute(f"select {', '.join(cols)} from {table}").fetchall()


class Raw:
    pass


def load(con):
    R = Raw()
    R.tables = table_names(con)
    R.tx = {r[0]: r for r in select(con, "transactions", [
        "transaction_key", "payer_name", "payer_id", "payment_date", "trace_number", "superseded", "envelope_index"])}
    R.claims = select(con, "claims", [
        "claim_key", "transaction_key", "claim_seq", "patient_control_number", "claim_status_code", "charge_cents",
        "paid_cents", "patient_responsibility_cents", "claim_filing_indicator", "payer_claim_control_number",
        "facility_type_code", "claim_frequency_code", "statement_from", "payer_name", "payer_id", "payment_date",
        "crossover_carrier_name", "rendering_provider_npi", "patient_last", "patient_first", "patient_id", "insured_id"])
    # replace the patient's name and member ids by one-way keys at once
    R.claims = [c[:18] + (hkey(c[18], c[19]), hkey(c[20]), hkey(c[21])) for c in R.claims]
    R.lines = select(con, "lines", [
        "line_key", "claim_key", "line_seq", "procedure_qualifier", "procedure_code", "modifiers", "charge_cents",
        "paid_cents", "paid_units", "original_procedure_qualifier", "original_procedure_code", "original_modifiers",
        "original_units", "service_date", "service_period_start", "allowed_cents", "rendering_provider_npi"])
    R.cas = select(con, "adjustments", ["claim_key", "line_key", "level", "group_code", "reason_code", "amount_cents"])
    R.rarc = select(con, "remarks", ["claim_key", "line_key", "level", "code"])
    R.plb = select(con, "plb", ["transaction_key", "reason_code", "reference", "amount_cents", "payer_name", "payer_id",
                                "payment_date"])
    R.c837 = select(con, "claims_837", ["claim_837_key", "bht_created", "patient_control_number", "total_charge_cents",
                                        "frequency_code", "payer_name", "payer_id", "service_date", "place_of_service",
                                        "subscriber_id"])
    R.c837 = [c[:9] + (hkey(c[9]),) for c in R.c837]
    R.l837 = select(con, "lines_837", ["claim_837_key", "line_seq", "procedure_code", "modifiers", "charge_cents",
                                       "units", "service_date"])
    if "ema_rows" in R.tables:
        src = {}
        if "ema_sources" in R.tables:
            src = {r[0]: r[1] for r in select(con, "ema_sources", ["source_name", "created_at"])}
        rows = select(con, "ema_rows", [
            "family", "source_name", "row_index", "service_date", "bill_created", "claim_id", "bill_item_id", "cpt",
            "modifiers", "payer", "coverage_type", "claim_status", "claim_submission_date", "denial_date",
            "charges_cents", "units", "policy_number", "activity_type", "insurance_payments_cents"])
        R.ema = [r[:16] + (src.get(r[1]) or "", hkey(r[16]), (r[17] or "").strip(), r[18]) for r in rows]
    else:
        R.ema = []
    R.findings = select(con, "findings", [
        "finding_id", "signal", "category", "actionable", "status", "line_key", "claim_key", "transaction_key",
        "patient_control_number", "payer_name", "procedure_code", "modifiers", "service_date", "payment_date",
        "charge_cents", "paid_cents", "expected_cents", "shortfall_cents", "carc", "evidence", "appeal_deadline",
        "first_seen", "recovered_cents", "written_off_cents"])
    R.events = select(con, "finding_events", ["finding_id", "at", "kind", "from_status", "to_status", "amount_cents",
                                              "actor"]) if "finding_events" in R.tables else []
    R.heirs = select(con, "finding_heirs", ["old_id", "new_id"]) if "finding_heirs" in R.tables else []
    R.faxes = select(con, "faxes", ["finding_id", "status", "sent_at", "created_at", "direction"]) \
        if "faxes" in R.tables else []
    return R


# ------------------------------------------------------------------------------------------------------- the audit
class Audit:
    def __init__(self, raw, as_of, log=print):
        self.R = raw
        self.as_of = as_of
        self.log = log
        self.stats = Counter()
        self.money = Counter()
        self.fams = Families()
        self.procs = {}          # claim_key -> Proc (kept and copies)
        self.lines = {}          # line_key -> Line
        self.svcs = {}           # key -> Svc
        self.by_bill = defaultdict(list)       # bill -> [Proc] (kept, all statuses)
        self.bill_svcs = defaultdict(set)      # bill -> {svc key}
        self.answers = []
        self.verdicts = {}
        self.lot = []            # LEFT_ON_TABLE units
        self.cw_units = []

    # ------------------------------------------------------------------ build processings
    def build_processings(self):
        R = self.R
        pairs = Counter()
        for c in R.claims:
            pairs[(c[13], c[14])] += 1
        self.fams.learn([(n, p, k) for (n, p), k in pairs.items()])
        ccas = defaultdict(list)
        lcas = defaultdict(list)
        for ck, lk, level, g, r, a in R.cas:
            item = ((g or "").strip().upper(), (r or "").strip().upper(), a or 0)
            if lk:
                lcas[lk].append(item)
            else:
                ccas[ck].append(item)
        crarc = defaultdict(list)
        lrarc = defaultdict(list)
        for ck, lk, level, code in R.rarc:
            if not code:
                continue
            (lrarc[lk] if lk else crarc[ck]).append(code.strip().upper())
        for c in R.claims:
            (ck, tk, seq, pcn, st, charge, paid, prr, prod, clp07, pos, freq, stmt, pname, pid, pdate, xover, npi,
             pk, mk1, mk2) = c
            t = R.tx.get(tk)
            p = Proc()
            p.ck, p.tk, p.seq = ck, tk, seq or 0
            p.pcn = (pcn or "").strip().upper()
            p.bill, p.sfx = split_claim_id(p.pcn)
            p.fam = self.fams.of(pname, pid)
            p.st = (st or "").strip()
            p.freq = (freq or "").strip()
            p.prod = (prod or "").strip().upper()
            p.clp07 = (clp07 or "").strip()
            p.pos = (pos or "").strip()
            p.date = iso((t[3] if t else None) or pdate) or "0000-00-00"
            p.trace = (t[4] if t else "") or ""
            p.sup = bool(t[5]) if t else False
            p.ordk = (p.date, tk or "", p.seq)
            p.charge, p.paid = charge or 0, paid or 0
            p.ccas = ccas.get(ck, [])
            p.crarc = crarc.get(ck, [])
            p.lines = []
            p.npi = npi
            p.copy_of = p.cancelled_by = p.cancels = None
            p.xover = bool((xover or "").strip())
            p.stmt = iso(stmt)
            p.pk = pk
            p.mks = {x for x in (mk1, mk2) if x}
            self.procs[ck] = p
        for ln in R.lines:
            (lk, ck, seq, qual, code, mods, charge, paid, punits, oqual, ocode, omods, ounits, sdate, pstart, allowed,
             npi) = ln
            p = self.procs.get(ck)
            if p is None:
                self.stats["line_without_claim"] += 1
                continue
            L = Line()
            L.lk, L.p, L.seq = lk, p, seq or 0
            code, xmods = split_code(code)
            ocode, xomods = split_code(ocode)
            oqual = (oqual or "").strip().upper()
            L.adj = code
            if xmods or xomods:
                self.stats["codes_with_modifiers_in_code_field"] += 1
            if ocode and oqual in ("HC", "HP", ""):
                L.code = ocode
                L.mods = (parse_mods(omods) if omods else parse_mods(mods)) + xomods
                L.changed = ocode != code
            else:
                L.code = code
                L.mods = parse_mods(mods) + xmods
                L.changed = False
            L.comp = component(L.mods)
            L.dos = iso(sdate) or iso(pstart) or p.stmt or ""
            L.charge, L.paid = charge or 0, paid or 0
            L.paid_tb = L.paid
            L.cas = lcas.get(lk, [])
            L.rarc = lrarc.get(lk, [])
            ub = units_of(ounits) or units_of(punits) or 1
            L.ub = ub
            L.up = (units_of(punits) or ub) if L.paid != 0 else 0
            if allowed is not None:
                L.allowed, L.asrc = allowed, "B6"
            else:
                pr = sum(a for g, r, a in L.cas if g == "PR")
                seq_mips = sum(a for g, r, a in L.cas if r in ("253", "237"))
                L.allowed, L.asrc = L.paid + pr + seq_mips, "derived"
            L.cancelled_by = L.cancels = None
            L.svc = None
            L.ans = None
            L.npi = npi or p.npi
            L.virtual = False
            p.lines.append(L)
            self.lines[lk] = L
        for p in self.procs.values():
            p.lines.sort(key=lambda x: x.seq)
            p.nlines_raw = len(p.lines)
        self.stats["processings"] = len(self.procs)
        self.stats["lines"] = len(self.lines)

    # ------------------------------------------------------------------ duplicate deliveries
    def dedupe(self):
        """The same processing delivered twice counts once. Copies: same payer, claim id, payer claim number, CLP02,
        charge and paid, and either the same check (trace and date) or more such processings than this payer's
        reversals of it plus one (a payment stands once unless it was reversed and paid again)."""
        groups = defaultdict(list)
        for p in self.procs.values():
            groups[(p.fam, p.pcn, p.clp07, p.st, p.charge, p.paid)].append(p)
        rev_count = Counter()
        for p in self.procs.values():
            if p.st == REVERSAL:
                rev_count[(p.fam, p.bill, -p.charge, -p.paid)] += 1

        def rank(p):  # the copy to keep: not superseded, most lines, earliest, lowest key
            return (p.sup, -len(p.lines), p.ordk)

        for key, ps in groups.items():
            if len(ps) < 2:
                continue
            by_check = defaultdict(list)
            for p in ps:
                by_check[(p.trace, p.date)].append(p)
            kept = []
            for chk, cps in by_check.items():
                cps.sort(key=rank)
                kept.append(cps[0])
                for x in cps[1:]:
                    x.copy_of = cps[0]
                    self.stats["copy_same_check"] += 1
                    self.money["copy_same_check_paid"] += x.paid
            if key[3] == REVERSAL:
                continue
            allowed_n = rev_count[(key[0], split_claim_id(key[1])[0], key[4], key[5])] + 1
            if len(kept) > allowed_n:
                # keep the oldest deliveries not marked superseded; the rest are copies of the first one kept
                order = sorted(kept, key=lambda p: (p.sup, p.ordk))
                for x in order[allowed_n:]:
                    x.copy_of = order[0]
                    self.stats["copy_no_reversal_between"] += 1
                    self.money["copy_no_reversal_between_paid"] += x.paid
        for p in self.procs.values():
            if p.copy_of is None:
                self.by_bill[p.bill].append(p)
        for b in self.by_bill:
            self.by_bill[b].sort(key=lambda p: p.ordk)

    # ------------------------------------------------------------------ reversals
    def pair_reversals(self):
        """Each CLP02 22 processing negates an earlier processing of the same payer and bill: whole claim first
        (charge and paid negated), then line by line (code, component, date, charge and paid negated). The original is
        looked for in earlier remittances first; inside the reversal's own remittance only when that remittance holds
        two or more processings of the bill by the payer (the one next to a reversal is usually its replacement). A
        reversal that finds nothing reverses a processing not on file: it cancels nothing else."""
        for bill, ps in self.by_bill.items():
            revs = [p for p in ps if p.st == REVERSAL]
            if not revs:
                continue
            origs = [p for p in ps if p.st != REVERSAL]
            same_tx_n = Counter((o.tk, o.fam) for o in origs)

            def eligible(o, r):
                if o.tk != r.tk:
                    return o.date <= r.date
                return same_tx_n[(o.tk, o.fam)] >= 2 and o.ordk < r.ordk

            def order(o, r):
                return (o.tk != r.tk, o.clp07 == r.clp07, o.pcn == r.pcn, o.ordk if o.tk != r.tk else
                        tuple(-x if isinstance(x, int) else x for x in o.ordk))
            for r in revs:
                cands = [o for o in origs if o.cancelled_by is None and o.fam == r.fam and o.charge == -r.charge
                         and o.paid == -r.paid and eligible(o, r)]
                if cands:
                    cands.sort(key=lambda o: order(o, r), reverse=True)
                    o = cands[0]
                    o.cancelled_by = r
                    r.cancels = o
                    self.stats["reversal_paired_claim"] += 1
                    for L in r.lines:
                        L.cancels = "claim"
                    continue
                for L in r.lines:
                    lc = []
                    for o in origs:
                        if o.fam != r.fam or o.cancelled_by is not None or not eligible(o, r):
                            continue
                        for ol in o.lines:
                            if (ol.cancelled_by is None and ol.code == L.code and ol.comp == L.comp and ol.dos == L.dos
                                    and ol.charge == -L.charge and ol.paid == -L.paid):
                                lc.append(ol)
                    if lc:
                        lc.sort(key=lambda ol: order(ol.p, r), reverse=True)
                        lc[0].cancelled_by = L
                        L.cancels = lc[0].lk
                        self.stats["reversal_paired_line"] += 1
                    else:
                        self.stats["reversal_line_not_on_file"] += 1
                        self.money["reversal_line_not_on_file_paid"] += -L.paid
                if not r.lines:
                    self.stats["reversal_claim_not_on_file"] += 1
                    self.money["reversal_claim_not_on_file_paid"] += -r.paid

    # ------------------------------------------------------------------ PLB take-backs
    def apply_takebacks(self):
        """PLB WO, FB and J1 whose reference embeds a claim id: per payer and bill, the net (positive = money taken).
        Applied to that payer's standing payments of the bill unless the same payer's reversal of the bill is on file
        (any date): then the reversal already took the money back (the PLB entries only recover its balance)."""
        net = defaultdict(int)
        ents = defaultdict(list)
        for tk, rc, ref, amt, pname, pid, pdate in self.R.plb:
            rc = (rc or "").strip().upper()
            if rc not in ("WO", "FB", "J1"):
                continue
            mm = EMBED_RE.search((ref or "").upper())
            if not mm:
                self.stats["plb_takeback_no_claim_id"] += 1
                self.money["plb_takeback_no_claim_id"] += amt or 0
                continue
            fam = self.fams.of(pname, pid)
            key = (fam, mm.group(1))
            net[key] += amt or 0
            ents[key].append((mm.group(1) + mm.group(2), amt or 0, iso(pdate), rc))
        self.plb_net = net
        self.plb_ents = ents
        for (fam, bill), t in net.items():
            if t <= 0:
                self.stats["plb_bills_net_zero_or_returned"] += 1
                continue
            ps = self.by_bill.get(bill, [])
            if any(p.st == REVERSAL and p.fam == fam for p in ps):
                self.stats["plb_bills_reversal_on_file"] += 1
                self.money["plb_reversal_on_file"] += t
                continue
            claim_ids = {e[0] for e in ents[(fam, bill)]}
            cands = [L for p in ps for L in p.lines
                     if p.fam == fam and p.st in PRIMARY + SECONDARY and L.standing() and L.paid_tb > 0]
            cands.sort(key=lambda L: (L.p.pcn in claim_ids, L.p.ordk, L.paid_tb), reverse=True)
            left = t
            for L in cands:
                if left <= 0:
                    break
                take = min(left, L.paid_tb)
                L.paid_tb -= take
                left -= take
            self.stats["plb_bills_applied"] += 1
            self.money["plb_applied"] += t - left
            if left > 0:
                self.money["plb_not_matched_to_payments"] += left

    # ------------------------------------------------------------------ service groups
    def svc(self, key):
        s = self.svcs.get(key)
        if s is None:
            s = self.svcs[key] = Svc(key)
            self.bill_svcs[key[0]].add(key)
        return s

    def build_services(self):
        R = self.R
        member_pk = defaultdict(Counter)
        for p in self.procs.values():
            if p.pk:
                for mk in p.mks:
                    member_pk[mk][p.pk] += 1
        self.member_pk = {mk: c.most_common(1)[0][0] for mk, c in member_pk.items()}
        for p in self.procs.values():
            if p.copy_of is not None:
                continue
            for L in p.lines:
                s = self.svc((p.bill, L.code, L.comp, L.dos))
                L.svc = s
                s.lines.append(L)
                if p.pk:
                    s.pks.add(p.pk)
        l837 = defaultdict(list)
        for r in R.l837:
            l837[r[0]].append(r)
        answers_by_pcn = defaultdict(list)
        for p in self.procs.values():
            if p.copy_of is None:
                answers_by_pcn[p.pcn].append(p)
        for v in answers_by_pcn.values():
            v.sort(key=lambda p: p.ordk)
        # learn which 835 family answers each 837 payer (first answer on or after the send)
        learn = defaultdict(Counter)
        for (k, bht, pcn, total, freq, pname, pid, sdate, pos, sub) in R.c837:
            pcn = (pcn or "").strip().upper()
            bht = iso(bht)
            if not bht:
                continue
            for p in answers_by_pcn.get(pcn, []):
                if p.date >= bht:
                    learn[(norm_name(pname), specific_payer_id(pid))][p.fam] += 1
                    break
        self.map837 = {}
        for key, c in learn.items():
            fam, n = c.most_common(1)[0]
            if n >= 3 and n >= 0.5 * sum(c.values()):
                self.map837[key] = fam
        self.answer_days = defaultdict(list)
        self.sends_by_bill = defaultdict(list)
        for (k, bht, pcn, total, freq, pname, pid, sdate, pos, sub) in R.c837:
            pcn = (pcn or "").strip().upper()
            bht = iso(bht)
            if not bht:
                continue
            bill, sfx = split_claim_id(pcn)
            fam = self.map837.get((norm_name(pname), specific_payer_id(pid))) or self.fams.of(pname, pid)
            freq = (freq or "").strip()
            self.sends_by_bill[bill].append((bht, fam, freq, pcn, "837"))
            for p in answers_by_pcn.get(pcn, []):
                if p.date >= bht:
                    self.answer_days[fam].append(days_between(bht, p.date))
                    break
            spk = self.member_pk.get(sub) if sub else None
            for (_k, seq, code, mods, charge, units, ldate) in l837.get(k, []):
                code = (code or "").strip().upper()
                if not code:
                    continue
                md = parse_mods(mods)
                dos = iso(ldate) or iso(sdate) or ""
                s = self.svc((bill, code, component(md), dos))
                s.sends.append((bht, fam, freq, pcn, units_of(units) or 1, charge or 0, "837", k))
                if spk:
                    s.pks.add(spk)
        # EMA rows
        ema_fam_learn = defaultdict(Counter)
        for r in R.ema:
            pcn = (r[5] or "").strip().upper()
            if not pcn:
                continue
            for p in answers_by_pcn.get(pcn, [])[:1]:
                ema_fam_learn[norm_name(r[9])][p.fam] += 1
        self.ema_fam = {}
        for nm, c in ema_fam_learn.items():
            fam, n = c.most_common(1)[0]
            if n >= 3 and n >= 0.5 * sum(c.values()):
                self.ema_fam[nm] = fam
        seen_items = {}
        status_latest = {}
        status_rows = defaultdict(Counter)
        charge_units = defaultdict(Counter)   # (pcn, key) -> {export: units charged in that export}
        cpu_max = {}
        self.ema_paid = defaultdict(int)      # (bill, code, date) -> insurance payments EMA posted (any export)
        ema_pay_seen = set()
        for r in R.ema:
            (family, sname, ridx, sdate, bcreated, claim_id, item_id, cpt, mods, payer, cov, cstatus, subm, denial,
             charges, units, created, polk, activity, ins_paid) = r
            pcn = (claim_id or "").strip().upper()
            cpt = (cpt or "").strip().upper()
            if not pcn or not cpt:
                continue
            if (cov or "").strip().lower() == "self-pay":
                continue
            bill, sfx = split_claim_id(pcn)
            md = parse_mods(mods)
            dos = iso(sdate) or ""
            key = (bill, cpt, component(md), dos)
            fam = self.ema_fam.get(norm_name(payer)) or ("EMA:" + norm_name(payer) if payer else "")
            if family == "charge_lines":
                # units come from charge rows only (payment and adjustment rows repeat the line), counted per export
                if activity in ("Charge", "") and (charges or 0) > 0:
                    charge_units[(pcn, key)][sname] += units_of(units) or 1
                    cpu_max[(pcn, key)] = max(cpu_max.get((pcn, key), 0), charges / max(1, units_of(units) or 1))
                elif activity == "Void Charge":
                    charge_units[(pcn, key)][sname] -= units_of(units) or 1
                elif activity == "Payment" and (ins_paid or 0) > 0:
                    pk_ = (pcn, cpt, dos, item_id or "", ins_paid)
                    if pk_ not in ema_pay_seen:
                        ema_pay_seen.add(pk_)
                        self.ema_paid[(bill, cpt, dos)] += ins_paid
                if activity not in ("Charge", ""):
                    continue
                ik = (pcn, cpt, tuple(md), dos, charges, units)
                prev = seen_items.get(ik)
                rec = (pcn, units_of(units) or 1, charges or 0, iso(bcreated), fam, (cstatus or "").strip(), created)
                if prev is None or (created or "") >= (prev[1][6] or ""):
                    seen_items[ik] = (key, rec, self.member_pk.get(polk) if polk else None)
            elif family == "claim_status":
                sk = (pcn, key)
                status_rows[sk][sname] += 1
                rec = (pcn, (cstatus or "").strip(), iso(subm), fam, created or "")
                prev = status_latest.get(sk)
                if prev is None or rec[4] >= prev[4]:
                    status_latest[sk] = rec
                if iso(subm):
                    self.sends_by_bill[bill].append((iso(subm), fam, "ema", pcn, "ema"))
        seen_pcn_key = set()
        for key, rec, epk in seen_items.values():
            s = self.svc(key)
            if (rec[0], key) not in seen_pcn_key:
                seen_pcn_key.add((rec[0], key))
                n_units = max(0, max(charge_units.get((rec[0], key), {}).values(), default=0))
                cpu = cpu_max.get((rec[0], key), 0)
                s.ema.append((rec[0], n_units, int(round(cpu * max(1, n_units))), rec[3], rec[4], rec[5], rec[6]))
            if epk:
                s.pks.add(epk)
        for (pcn, key), rec in status_latest.items():
            s = self.svc(key)
            s.emast.append(rec + (max(status_rows[(pcn, key)].values()),))
            if rec[2]:
                s.sends.append((rec[2], rec[3], "ema", rec[0], 0, 0, "ema", None))
        self.stats["services"] = len(self.svcs)

    # ------------------------------------------------------------------ claim-level-only processings
    def attach_claim_level(self):
        """A processing reported at claim level only (no service lines) answers every service its claim id billed on
        its date: those services get a virtual line carrying the claim's adjustments and a share of its payment."""
        self.attach_unlisted_on_claim_denials()
        for p in self.procs.values():
            if p.copy_of is not None or p.lines or p.st == REVERSAL:
                continue
            keys = []
            for key in self.bill_svcs.get(p.bill, ()):
                s = self.svcs[key]
                same_id = any(x[3] == p.pcn for x in s.sends) or any(x[0] == p.pcn for x in s.ema) or \
                    any(L.p.pcn == p.pcn for L in s.lines)
                if not same_id:
                    continue
                if p.stmt and s.dos and s.dos != p.stmt:
                    continue
                keys.append(key)
            if not keys:
                self.stats["claim_level_proc_unplaced"] += 1
                self.money["claim_level_proc_unplaced_charge"] += p.charge
                continue
            self.stats["claim_level_proc_placed"] += 1
            charges = {}
            for key in keys:
                s = self.svcs[key]
                ch = max([x[5] for x in s.sends if x[3] == p.pcn] + [x[2] for x in s.ema if x[0] == p.pcn]
                         + [L.charge for L in s.lines if L.p.pcn == p.pcn] + [0])
                charges[key] = ch
            tot = sum(charges.values()) or 1
            for i, key in enumerate(sorted(keys)):
                s = self.svcs[key]
                L = Line()
                L.lk = f"{p.ck}#v{i}"
                L.p, L.seq = p, 1000 + i
                L.code, L.adj, L.mods, L.comp, L.dos = s.code, s.code, (), s.comp, s.dos
                L.charge = charges[key]
                L.paid = int(round(p.paid * charges[key] / tot)) if p.paid else 0
                L.paid_tb = L.paid
                L.cas = list(p.ccas)
                L.rarc = list(p.crarc)
                ub = max([x[4] for x in s.sends if x[3] == p.pcn] + [x[1] for x in s.ema if x[0] == p.pcn] + [1])
                L.ub = ub
                L.up = ub if L.paid > 0 else 0
                L.allowed, L.asrc = L.paid, "virtual"
                L.cancelled_by = L.cancels = None
                L.svc = s
                L.ans = None
                L.changed = False
                L.npi = p.npi
                L.virtual = True
                p.lines.append(L)
                s.lines.append(L)
                if p.pk:
                    s.pks.add(p.pk)
                self.lines[L.lk] = L

    def attach_unlisted_on_claim_denials(self):
        """A whole-claim denial (CLP02 4) that lists only some lines denies every service its claim id billed on those
        dates: an unlisted service gets a virtual line carrying the claim's main denial reason."""
        n = 0
        for p in list(self.procs.values()):
            if p.copy_of is not None or p.st != "4" or not p.lines or p.cancelled_by is not None:
                continue
            listed = {L.svc.key for L in p.lines if L.svc is not None}
            dates = {L.dos for L in p.lines if L.dos} | ({p.stmt} if p.stmt else set())
            cas = list(p.ccas) or [c for L in p.lines for c in L.cas]
            reasons = Counter()
            for g, r, a_ in cas:
                if a_ > 0 and g != "PR" and r not in NON_DENIAL_CARCS:
                    reasons[(g, r)] += a_
            if not reasons:
                continue
            (g0, r0), _ = reasons.most_common(1)[0]
            for key in sorted(self.bill_svcs.get(p.bill, ())):
                if key in listed or key[3] not in dates:
                    continue
                s = self.svcs[key]
                billed = [x for x in s.sends if x[3] == p.pcn] + [x for x in s.ema if x[0] == p.pcn]
                if not billed:
                    continue
                L = Line()
                L.lk = f"{p.ck}#u{n}"
                L.p, L.seq = p, 2000 + n
                L.code, L.adj, L.mods, L.comp, L.dos = s.code, s.code, (), s.comp, s.dos
                ch = max([x[5] for x in s.sends if x[3] == p.pcn] + [x[2] for x in s.ema if x[0] == p.pcn] + [0])
                L.charge, L.paid, L.paid_tb = ch, 0, 0
                L.cas = [(g0, r0, ch)]
                L.rarc = list(p.crarc)
                L.ub = max([x[4] for x in s.sends if x[3] == p.pcn] + [x[1] for x in s.ema if x[0] == p.pcn] + [1])
                L.up = 0
                L.allowed, L.asrc = 0, "virtual"
                L.cancelled_by = L.cancels = None
                L.svc, L.ans, L.changed, L.npi, L.virtual = s, None, False, p.npi, True
                p.lines.append(L)
                s.lines.append(L)
                if p.pk:
                    s.pks.add(p.pk)
                self.lines[L.lk] = L
                n += 1
        self.stats["unlisted_services_on_claim_denials"] = n

    # ------------------------------------------------------------------ answers
    def build_answers(self):
        """Per processing and service group, its lines added up. Lines of one service whose charges per unit differ
        are one billed line the payer split (copay, sequestration, contractual parts): they count the units once."""
        n_split = 0
        for p in self.procs.values():
            if p.copy_of is not None or p.st == REVERSAL:
                continue
            groups = defaultdict(list)
            for L in p.lines:
                if L.svc is not None:
                    groups[(L.svc.key, L.standing())].append(L)
            for (key, standing), ls in groups.items():
                A = Ans()
                A.p, A.svc, A.lines, A.standing = p, self.svcs[key], ls, standing
                per_unit = {int(round(L.charge / max(1, L.ub))) for L in ls if L.charge > 0}
                A.split = len(ls) > 1 and len(per_unit) > 1
                n_split += A.split
                A.ub = max(L.ub for L in ls) if A.split else sum(L.ub for L in ls)
                A.charge = sum(L.charge for L in ls)
                A.paid = sum(L.paid for L in ls)
                A.paid_tb = sum(L.paid_tb for L in ls)
                A.allowed = sum(L.allowed for L in ls)
                paying = [L for L in ls if L.paid > 0]
                if A.paid > 0:
                    A.up = A.ub if A.split else min(A.ub, sum(L.up for L in paying))
                else:
                    A.up = 0
                A.cas = [c for L in ls for c in L.cas]
                A.rarc = [c for L in ls for c in L.rarc]
                A.mods = tuple(sorted({m_ for L in ls for m_ in L.mods}))
                A.changed = any(L.changed for L in ls)
                A.npi = ls[0].npi
                A.virtual = all(L.virtual for L in ls)
                A.lk = (paying[0] if paying else ls[0]).lk
                for L in ls:
                    L.ans = A
                A.svc.answers.append(A)
                self.answers.append(A)
        self.merge_dropped_components()
        for s in self.svcs.values():
            s.answers.sort(key=lambda A: A.p.ordk)
        self.stats["answers"] = len(self.answers)
        self.stats["answers_split_by_payer"] = n_split

    def merge_dropped_components(self):
        """A payer that answers without the billed 26 or TC modifier: its global answer for a code and date that was
        billed only as 26 (or only as TC), and never billed as global, answers the billed component."""
        n = 0
        for bill, keys in list(self.bill_svcs.items()):
            for key in list(keys):
                s = self.svcs.get(key)
                if s is None or s.comp != "G" or not s.answers or s.sends or s.ema or s.emast:
                    continue
                billed = [self.svcs[(bill, s.code, c, s.dos)] for c in ("26", "TC") if (bill, s.code, c, s.dos) in self.svcs]
                billed = [b for b in billed if (b.sends or b.ema or b.emast) and not b.answers]
                if len(billed) != 1:
                    continue
                t = billed[0]
                for A in s.answers:
                    A.svc = t
                    t.answers.append(A)
                    for L in A.lines:
                        L.svc = t
                        t.lines.append(L)
                t.pks |= s.pks
                del self.svcs[key]
                keys.discard(key)
                n += 1
        self.stats["answers_moved_to_billed_component"] = n

    # ------------------------------------------------------------------ payer timing and feed
    def payer_timing(self):
        all_days = [d for v in self.answer_days.values() for d in v]
        self.global_usual = max(30, pct(all_days, 0.95) or 30)
        self.usual = {}
        for fam, v in self.answer_days.items():
            if len(v) >= 20:
                self.usual[fam] = max(30, pct(v, 0.95))
        newest = {}
        for p in self.procs.values():
            if p.date <= self.as_of and p.date > newest.get(p.fam, ""):
                newest[p.fam] = p.date
        self.fam_newest = newest
        dates = [p.date for p in self.procs.values() if p.date <= self.as_of]
        self.feed_date = max(dates) if dates else self.as_of
        self.stats["future_dated_processings"] = sum(1 for p in self.procs.values() if p.date > self.as_of)

    def usual_days(self, fam):
        return self.usual.get(fam, self.global_usual)

    def feed_state(self, fam):
        if fam not in self.fam_newest:
            return "NO_FEED"
        if days_between(self.fam_newest[fam], self.feed_date) > max(60, self.usual_days(fam)):
            return "FEED_STOPPED"
        return "LIVE"

    # ------------------------------------------------------------------ rates
    @staticmethod
    def pos_class(p):
        return "office" if p.pos in ("", "11") else "facility"

    def build_rates(self):
        """The payer's own rate: the most common allowed amount per unit on its clean, standing, primary, paid answers,
        for the rendering provider when that provider has enough answers of its own (payers pay by provider tier),
        else by payer, product (CLP06), code, component, year of service and setting (falling back to wider groups)."""
        obs = defaultdict(Counter)
        for A in self.answers:
            p, s = A.p, A.svc
            if A.virtual or not A.standing or p.st not in ("1", "19"):
                continue
            if A.paid_tb <= 0 or A.paid_tb < A.paid or A.up <= 0 or A.allowed <= 0 or A.changed or not s.dos:
                continue
            if set(A.mods) & (REDUCING_MODS | MPPR_MODS):
                continue
            if any(r == "59" for g, r, a in A.cas):
                continue
            apu = int(round(A.allowed / A.up))
            yr = s.dos[:4]
            pc = self.pos_class(p)
            obs[(p.fam, p.prod, s.code, s.comp, yr, pc, A.npi or "")][apu] += 1
            obs[(p.fam, p.prod, s.code, s.comp, yr, pc)][apu] += 1
            obs[(p.fam, p.prod, s.code, s.comp, yr)][apu] += 1
            obs[(p.fam, s.code, s.comp, yr)][apu] += 1
        self.rate_obs = obs
        # codes a payer does not pay: never on 10+ answers, or (per product) on under 5% of 20+ answers
        seen = Counter()
        paid = Counter()
        seen_p = Counter()
        paid_p = Counter()
        for A in self.answers:
            if A.virtual or not A.standing or A.p.st not in PRIMARY:
                continue
            k = (A.p.fam, A.svc.code, A.svc.comp)
            kp = (A.p.fam, A.p.prod, A.svc.code, A.svc.comp)
            seen[k] += 1
            seen_p[kp] += 1
            if A.paid > 0:
                paid[k] += 1
                paid_p[kp] += 1
        self.never_paid = {k for k, n in seen.items() if n >= 10 and paid[k] == 0}
        self.rarely_paid_fam = {k for k, n in seen.items() if n >= 20 and paid[k] < 0.05 * n}
        self.rarely_paid = {k for k, n in seen_p.items() if n >= 20 and paid_p[k] < 0.05 * n}

    def rate(self, fam, prod, code, comp, year, pc=None, npi=None):
        tries = []
        if pc is not None and prod is not None and npi:
            tries.append(((fam, prod, code, comp, year, pc, npi), 0.5))
        if pc is not None and prod is not None:
            tries.append(((fam, prod, code, comp, year, pc), 0.4))
        if prod is not None:
            tries.append(((fam, prod, code, comp, year), 0.4))
        tries.append(((fam, code, comp, year), 0.4))
        for key, share in tries:
            c = self.rate_obs.get(key)
            if not c:
                continue
            n = sum(c.values())
            amt, k = c.most_common(1)[0]
            if n >= 3 and k >= share * n:
                return amt, c, len(key)
        return None, None, 0

    # ------------------------------------------------------------------ windows
    @staticmethod
    def appeal_days(prod):
        if prod in ("MB", "MA"):
            return 120
        if prod == "MC":
            return 90
        return 180

    @staticmethod
    def filing_days(prod):
        return 365 if prod in ("MB", "MA", "MC", "16", "") else 180

    # ------------------------------------------------------------------ units billed and scope
    def units_and_scope(self):
        for s in self.svcs.values():
            # units billed, per claim id: what its 837 billed when the 837 is on file (one record = one send),
            # otherwise the larger of the payer's answers and EMA's charge rows; then the largest claim id
            rec_units = defaultdict(int)
            for x in s.sends:
                if x[6] == "837":
                    rec_units[(x[3], x[7])] += x[4]
            u837 = defaultdict(int)
            for (pcn, k), u in rec_units.items():
                u837[pcn] = max(u837[pcn], u)
            proc_units = defaultdict(int)
            for A in s.answers:
                proc_units[(A.p.pcn, A.p.ck)] += A.ub
            other = defaultdict(int)
            for (pcn, ck), u in proc_units.items():
                other[pcn] = max(other[pcn], u)
            for x in s.ema:
                other[x[0]] = max(other[x[0]], x[1])
            for x in s.emast:
                other[x[0]] = max(other[x[0]], x[5])
            cands = [u837[pcn] if pcn in u837 else u for pcn, u in other.items()] + list(u837.values())
            s.U = min(999, max(cands)) if cands and max(cands) > 0 else 1
            charges = [A.charge / max(1, A.ub) for A in s.answers if A.charge > 0]
            charges += [x[5] / max(1, x[4]) for x in s.sends if x[5] > 0]
            charges += [x[2] / max(1, x[1]) for x in s.ema if x[2] > 0]
            s.charge_pu = int(round(max(charges))) if charges else 0
            if s.dos and s.dos >= SCOPE_START:
                s.in_scope, s.scope_basis = True, "dos"
            else:
                sent = [x[0] for x in s.sends if x[0]] + [x[3] for x in s.ema if x[3]]
                if any(d >= SCOPE_START for d in sent):
                    s.in_scope, s.scope_basis = True, "sent_2026"
                else:
                    first = min((A.p.date for A in s.answers), default=None)
                    if first and first >= "2026-01-15" and s.dos and s.dos >= "2025-10-01":
                        s.in_scope, s.scope_basis = True, "inferred_first_answer_2026"

    # ------------------------------------------------------------------ truth of one service group
    def denial_of(self, answers):
        """Main reason of an unpaid answer: (kind, carc). kind: PATIENT, DENIED, ZERO_RATE, ZERO_CHARGE."""
        co = Counter()
        pr = Counter()
        charge = 0
        for A in answers:
            charge += A.charge
            for g, r, a in A.cas:
                if a <= 0:
                    continue
                if g == "PR":
                    pr[r] += a
                elif r not in NON_DENIAL_CARCS:
                    co[(g, r)] += a
                elif r == "45":
                    co[("CO", "45*")] += a
        co_den = sum(a for (g, r), a in co.items() if r != "45*")
        pr_sum = sum(pr.values())
        self._pr_allowed = sum(a for r, a in pr.items() if r in ("1", "2", "3"))
        if pr_sum > 0 and pr_sum >= co_den:
            return "PATIENT", "PR-" + pr.most_common(1)[0][0]
        if co_den > 0:
            (g, r), a = max(((k, v) for k, v in co.items() if k[1] != "45*"), key=lambda kv: kv[1])
            return "DENIED", f"{g}-{r}"
        if co.get(("CO", "45*")):
            return "ZERO_RATE", "CO-45"
        if charge <= 0:
            return "ZERO_CHARGE", ""
        return "DENIED", "none"

    @staticmethod
    def unpaid_part(A):
        """The unpaid lines of a partly paid answer (their adjustments only), else the answer itself."""
        if A.up <= 0 or A.split:
            return A
        ls = [L for L in A.lines if L.paid <= 0]
        if not ls:
            return A
        B = Ans()
        B.lines, B.cas, B.charge = ls, [c for L in ls for c in L.cas], sum(L.charge for L in ls)
        B.ub = sum(L.ub for L in ls)
        return B

    def window_end(self, prod, carc, deny_date, dos):
        end = add_days(deny_date, self.appeal_days(prod)) if deny_date else None
        code = carc.split("-", 1)[-1] if carc else ""
        if dos and (code in CORRECTABLE_CARCS or end is None):
            tf = add_days(dos, self.filing_days(prod))
            end = max(end, tf) if end else tf
        return end

    def primary_answers_after(self, bill, date):
        return [p for p in self.by_bill.get(bill, []) if p.date >= date and p.st in PRIMARY and p.cancelled_by is None
                and p.freq != "8" and p.lines]

    def owed_rate(self, s, fam, prod, pc, npi=None):
        r_amt, _, r_lvl = self.rate(fam, prod, s.code, s.comp, (s.dos or "0000")[:4], pc, npi)
        cpu = s.charge_pu
        if r_amt:
            return (min(r_amt, cpu) if cpu else r_amt), f"rate_l{r_lvl}"
        return cpu, "charge"

    def truth(self, s):
        U = s.U
        units = []
        std = [A for A in s.answers if A.standing]
        prim = [A for A in std if A.p.st in PRIMARY]
        sec = [A for A in std if A.p.st in SECONDARY]
        bill_sends = self.sends_by_bill.get(s.bill, [])
        # voids: the bill's latest send is a void, or a void answer (CLP09 8) with nothing sent or answered after it
        void_dates = [x[0] for x in bill_sends if x[2] == "8"]
        void_dates += [p.date for p in self.by_bill.get(s.bill, []) if p.freq == "8"]
        if void_dates:
            vd = max(void_dates)
            later_send = any(x[0] > vd and x[2] in ("1", "7", "", "ema") for x in bill_sends)
            same_day_new = any(x[0] == vd and x[2] in ("1", "7") for x in bill_sends)
            later_answer = any(A.p.date > vd for A in prim)
            if not later_send and not same_day_new and not later_answer:
                return [("VOIDED", "837_or_clp09_8", "", "", "", vd, 0, "", "", False)] * U
        if s.charge_pu <= 0 and not any(A.paid > 0 for A in s.answers):
            return [("ZERO_CHARGE", "", "", "", "", "", 0, "", "", False)] * U
        # ---- paid units: the payer that paid the most units (a supplement paying a coinsurance as "primary" does not
        # pay a second unit), latest paying answer first
        fam_up = defaultdict(int)
        fam_ans = defaultdict(list)
        fam_taken = defaultdict(int)
        for A in prim:
            if A.paid > 0:
                if A.paid_tb > 0:
                    fam_up[A.p.fam] += A.up
                    fam_ans[A.p.fam].append(A)
                else:
                    fam_taken[A.p.fam] += A.up
        PU = 0
        if fam_up:
            fam = max(fam_up, key=lambda f: (min(U, fam_up[f]), max(A.p.ordk for A in fam_ans[f])))
            PU = min(U, fam_up[fam])
            fl = sorted(fam_ans[fam], key=lambda A: A.p.ordk)
            gov = fl[-1]
            up_f = min(U, sum(A.up for A in fl)) or 1
            apu_ans = max(A.apu() for A in fl)
            tot = sum(A.paid_tb for A in fl) + sum(a for A in fl for g, r, a in A.cas if g == "PR" or r in ("253", "237"))
            apu = max(apu_ans, tot / up_f)
            mods = set(m_ for A in fl for m_ in A.mods)
            f = 1.0
            sub = "full"
            if mods & MPPR_MODS or any(r == "59" for A in fl for g, r, a in A.cas):
                f, sub = 0.5, "full_mppr"
            r_amt, r_obs, r_lvl = self.rate(fam, gov.p.prod, s.code, s.comp, (s.dos or "0000")[:4],
                                            self.pos_class(gov.p), gov.npi)
            state, owed, basis = "PAID", 0, ""
            short_units = []          # (units, gap per unit) of paid pieces below the payer's own rate
            if gov.changed:
                sub = "paid_other_code"
            if mods & REDUCING_MODS:
                sub = "reduced_service_modifier"
            elif r_amt is None:
                sub = "unrated" if sub == "full" else sub
            else:
                basis = f"rate_l{r_lvl}"
                # judge each paid piece: one line of a service billed on several equal lines (a second lesion may
                # be reduced), or the whole answer (split lines, single lines); overlapping payments by the same
                # payer for the same units are judged together (an adjustment paid in two steps)
                pieces = []
                for A in fl:
                    if A.split or len(A.lines) == 1:
                        pieces.append((A.apu(), A.up, A, None))
                    else:
                        for L in A.lines:
                            if L.paid > 0:
                                lup = L.up or 1
                                pieces.append(((L.allowed - (L.paid - L.paid_tb)) / lup, lup, A, L))
                if sum(x[1] for x in pieces) > U:
                    pieces = [(apu, up_f, gov, None)]
                subs = Counter()
                for p_apu, p_units, A, L in pieces:
                    pm = set(L.mods) if L is not None else set(A.mods)
                    pc = L.cas if L is not None else A.cas
                    pf = 0.5 if (pm & MPPR_MODS or any(r == "59" for g, r, a in pc)) else 1.0
                    exp = r_amt * pf
                    gap = exp - p_apu
                    psub = "full_mppr" if pf == 0.5 else "full"
                    if gap > max(100, 0.03 * exp):
                        own = int(round(p_apu))
                        n_other = sum(r_obs.values()) - (1 if own in r_obs else 0)
                        alt = [a_ for a_, k in r_obs.items() if a_ != own and k >= 0.2 * max(1, n_other)]
                        own_lines = {L.lk} if L is not None else {x.lk for x in A.lines}
                        if abs(p_apu - 0.85 * exp) <= 0.015 * exp:
                            psub = "tier_85"
                        elif pf == 1.0 and abs(p_apu - 0.5 * exp) <= 0.05 * exp and self.mppr_eligible_lines(
                                A.p, own_lines, s.code):
                            psub = "half_rate_mppr"
                        elif any(abs(p_apu - a_ * pf) <= 1 for a_ in alt):
                            psub = "second_rate"
                        else:
                            short_units.append((p_units, int(round(gap))))
                            continue
                    subs[psub] += p_units
                if subs and sub not in ("paid_other_code",):
                    sub = subs.most_common(1)[0][0]
                if short_units and not subs:
                    weak = r_obs.most_common(1)[0][1] < 0.5 * sum(r_obs.values())
                    state = "SHORT"
                    owed = int(round(sum(u_ * g_ for u_, g_ in short_units) / max(1, sum(u_ for u_, g_ in short_units))))
                    sub = "paid_other_code" if gov.changed else ("below_weak_rate" if weak else "below_rate")
                    short_units = []
            if state == "PAID" and any(A.paid_tb < A.paid for A in fl):
                taken = sum(A.paid - A.paid_tb for A in fl)
                state, sub, owed = "SHORT", "partly_taken_back", int(round(taken / up_f))
            if state == "PAID":
                pr_left = sum(a for A in fl for g, r, a in A.cas if g == "PR")
                fwd = gov.p.st == "19" or gov.p.xover
                if pr_left > 0 and fwd and not any(x.p.date >= gov.p.date for x in sec):
                    sub = "waiting_secondary"
            wend = add_days(gov.p.date, self.appeal_days(gov.p.prod)) if state == "SHORT" else ""
            inside = bool(wend) and wend >= self.as_of
            n_short = min(PU, sum(u_ for u_, g_ in short_units)) if state == "PAID" else 0
            if n_short:
                weak = r_obs.most_common(1)[0][1] < 0.5 * sum(r_obs.values())
                g_avg = int(round(sum(u_ * g_ for u_, g_ in short_units) / max(1, sum(u_ for u_, g_ in short_units))))
                wend_s = add_days(gov.p.date, self.appeal_days(gov.p.prod))
                units += [("SHORT", "below_weak_rate" if weak else "below_rate", "", fam, gov.lk, gov.p.date, g_avg,
                           basis, wend_s, wend_s >= self.as_of)] * n_short
            units += [(state, sub, "", fam, gov.lk, gov.p.date, owed, basis, wend, inside)] * (PU - n_short)
        rest = U - len(units)
        if rest <= 0:
            return units[:U]
        # ---- taken back units
        taken_units = max(fam_taken.values(), default=0)
        if taken_units > 0:
            tb = sorted([A for A in prim if A.paid > 0 and A.paid_tb <= 0], key=lambda A: A.p.ordk, reverse=True)
            g = tb[0]
            k = min(rest, taken_units)
            wend = add_days(g.p.date, self.appeal_days(g.p.prod))
            units += [("TAKEN_BACK", "plb", "", g.p.fam, g.lk, g.p.date, int(round(g.paid / max(1, g.up))), "taken",
                       wend, wend >= self.as_of)] * k
            rest = U - len(units)
            if rest <= 0:
                return units[:U]
        # ---- the latest standing primary answer with unpaid units
        unpaid = [A for A in prim if (A.paid <= 0) or (A.paid_tb > 0 and A.ub > A.up)]
        if not unpaid and PU > 0:
            # the payer paid fewer units than were billed and denied none: paid short on units
            wend = add_days(gov.p.date, self.appeal_days(gov.p.prod))
            return units + [("SHORT", "fewer_units_answered", "", fam, gov.lk, gov.p.date, int(round(gov.apu())),
                             "paid_unit", wend, wend >= self.as_of)] * rest
        if unpaid:
            # the latest answer; on one day a processing (CLP02 1/19) outranks a whole-claim denial (CLP02 4)
            g = max(unpaid, key=lambda A: (A.p.date, A.p.st != "4", A.p.ordk))
            gl = [A for A in unpaid if A.p.ordk == g.p.ordk]
            kind, carc = self.denial_of([self.unpaid_part(A) for A in gl])
            pr_allowed = self._pr_allowed
            fam, prod = g.p.fam, g.p.prod
            sub = ""
            if kind == "DENIED" and carc.split("-", 1)[-1] in DUP_CARCS:
                # a duplicate denial: the substantive reason is the previous standing denial, if any
                earlier = [A for A in unpaid if A is not g and A.p.ordk <= g.p.ordk]
                for A in sorted(earlier, key=lambda A: A.p.ordk, reverse=True):
                    k2, c2 = self.denial_of([A])
                    if k2 == "DENIED" and c2.split("-", 1)[-1] not in DUP_CARCS:
                        carc, sub = c2, "latest_answer_is_duplicate_denial"
                        break
            if kind == "ZERO_RATE":
                r_amt, _, _ = self.rate(fam, prod, s.code, s.comp, (s.dos or "0000")[:4])
                if r_amt and r_amt > 0:
                    kind, carc = "DENIED", "CO-45"
            if kind == "ZERO_CHARGE":
                return units + [("ZERO_CHARGE", "", "", fam, g.lk, g.p.date, 0, "", "", False)] * rest
            if kind == "ZERO_RATE":
                return units + [("PAID", "zero_rate", carc, fam, g.lk, g.p.date, 0, "", "", False)] * rest
            level_cut = carc.split("-", 1)[-1] in LEVEL_REDUCTION_CARCS
            if pr_allowed > 0 and (kind == "PATIENT" or (kind == "DENIED" and level_cut)):
                # the payer allowed the service and sent the allowed amount to the patient (deductible, coinsurance,
                # copay): moved to the patient, or short when that allowed amount is below the payer's own rate
                r_amt, _, r_lvl = self.rate(fam, prod, s.code, s.comp, (s.dos or "0000")[:4], self.pos_class(g.p), g.npi)
                apu = pr_allowed / max(1, sum(A.ub for A in gl))
                half = abs(apu - 0.5 * r_amt) <= 0.05 * r_amt and self.mppr_eligible(g, s.code) if r_amt else False
                if r_amt and r_amt - apu > max(100, 0.03 * r_amt) and not half \
                        and abs(apu - 0.85 * r_amt) > 0.015 * r_amt:
                    wend = add_days(g.p.date, self.appeal_days(prod))
                    return units + [("SHORT", "to_patient_below_rate", carc, fam, g.lk, g.p.date, int(round(r_amt - apu)),
                                     f"rate_l{r_lvl}", wend, wend >= self.as_of)] * rest
                return units + [("MOVED_TO_PATIENT", carc, carc, fam, g.lk, g.p.date, 0, "", "", False)] * rest
            if kind == "PATIENT":
                return units + [("MOVED_TO_PATIENT", carc, carc, fam, g.lk, g.p.date, 0, "", "", False)] * rest
            bundled = carc.split("-", 1)[-1] in NOT_SEPARATELY_PAID_CARCS
            if bundled and (fam, s.code, s.comp) in self.never_paid:
                return units + [("NOT_PAYABLE", "payer_never_pays_this_code", carc, fam, g.lk, g.p.date, 0, "", "",
                                 False)] * rest
            if bundled and (fam, prod, s.code, s.comp) in self.rarely_paid:
                return units + [("NOT_PAYABLE", "payer_rarely_pays_this_code", carc, fam, g.lk, g.p.date, 0, "", "",
                                 False)] * rest
            later_sec = [A for A in sec if A.p.fam == fam and A.p.ordk > g.p.ordk]
            if later_sec:
                ssub = "cob" if carc.split("-", 1)[-1] in COB_CARCS else "other_denial"
                ls = later_sec[-1]
                return units + [("SETTLED_SECONDARY", ssub, carc, fam, ls.lk, ls.p.date, 0, "", "", False)] * rest
            owed, basis = self.owed_rate(s, fam, prod, self.pos_class(g.p), g.npi)
            other_sec = [A for A in sec if A.p.fam != fam and A.paid_tb > 0]
            if other_sec:
                sec_allowed = sum(A.paid_tb + sum(a for g_, r_, a in A.cas if g_ == "PR") for A in other_sec)
                if owed and sec_allowed >= 0.5 * owed * rest:
                    ls = max(other_sec, key=lambda A: A.p.ordk)
                    return units + [("PAID", "paid_by_other_payer_as_secondary", carc, ls.p.fam, ls.lk, ls.p.date, 0, "", "",
                                     False)] * rest
            newer = [x for x in s.sends if x[0] > g.p.date and x[2] != "8"]
            if newer:
                nd = max(x[0] for x in newer)
                nfam = [x[1] for x in newer if x[0] == nd][0]
                if self.primary_answers_after(s.bill, nd):
                    st, sub = "NEVER_ANSWERED", "answered_without_this_code"
                else:
                    waited = days_between(nd, self.feed_date)
                    st = "WORKED_WAITING"
                    sub = "resent_overdue" if waited > self.usual_days(nfam or fam) else "resent_within_usual_time"
                wend = self.window_end(prod, carc, nd, s.dos)
                wend = max(wend, add_days(s.dos, 365)) if s.dos else wend
                return units + [(st, sub, carc, fam, g.lk, g.p.date, owed, basis, wend, wend >= self.as_of)] * rest
            wend = self.window_end(prod, carc, g.p.date, s.dos)
            return units + [("DENIED", sub, carc, fam, g.lk, g.p.date, owed, basis, wend, wend >= self.as_of)] * rest
        # ---- no standing primary answer
        if sec:
            ls = max(sec, key=lambda A: A.p.ordk)
            return units + [("SECONDARY_ONLY", "primary_remittance_missing", "", ls.p.fam, ls.lk, ls.p.date, 0, "", "",
                             False)] * rest
        cancelled = [A for A in s.answers if not A.standing and A.p.st in PRIMARY]
        sends = [x for x in s.sends if x[2] != "8"]
        ema_status = sorted(s.emast, key=lambda x: x[4])
        est = ema_status[-1][1] if ema_status else ""
        if not sends and not cancelled:
            if est == "Resolved/Paid":
                return units + [("EMA_PAID_NO_REMIT", "", "", "", "", "", 0, "", "", False)] * rest
            sub = "no_send_on_file"
            if s.dos and "2026-01-01" <= s.dos <= "2026-06-30":
                sub = "jan_jun_2026_837_gap"
            return units + [("NOT_SENT_UNKNOWN", sub, "", "", "", "", 0, "", "", False)] * rest
        if sends:
            last = max(sends, key=lambda x: x[0])
            sd, fam = last[0], last[1]
        else:
            lc = max(cancelled, key=lambda A: A.p.ordk)
            rv = lc.p.cancelled_by if lc.p.cancelled_by is not None else lc.lines[0].cancelled_by.p
            sd, fam = rv.date, lc.p.fam
        owed, basis = self.owed_rate(s, fam, None, None)
        if (fam, s.code, s.comp) in self.never_paid or (fam, s.code, s.comp) in self.rarely_paid_fam:
            owed, basis = 0, "payer_does_not_pay_this_code"
        if self.ema_paid.get((s.bill, s.code, s.dos), 0) > 0:
            return units + [("EMA_PAID_NO_REMIT", "ema_posted_insurance_payment", "", fam, "", sd, owed, basis, "",
                             False)] * rest
        if est == "Resolved/Paid":
            return units + [("EMA_PAID_NO_REMIT", "", "", fam, "", sd, owed, basis, "", False)] * rest
        if est == "Inactive/Voided":
            return units + [("EMA_VOIDED", "", "", fam, "", sd, 0, "", "", False)] * rest
        wend = max(add_days(s.dos, 365) if s.dos else sd, add_days(sd, 180))
        if self.primary_answers_after(s.bill, sd):
            return units + [("NEVER_ANSWERED", "answered_without_this_code", "", fam, "", sd, owed, basis, wend,
                             wend >= self.as_of)] * rest
        if est == "Rejected":
            return units + [("NEVER_ANSWERED", "rejected_before_payer", "", fam, "", sd, owed, basis, wend,
                             wend >= self.as_of)] * rest
        fs = self.feed_state(fam) if fam and not fam.startswith("EMA:") else "UNMAPPED"
        if fs in ("NO_FEED", "UNMAPPED"):
            return units + [("NO_FEED", fs.lower(), "", fam, "", sd, owed, basis, wend, False)] * rest
        if fs == "FEED_STOPPED":
            return units + [("FEED_STOPPED", "", "", fam, "", sd, owed, basis, wend, False)] * rest
        if days_between(sd, self.feed_date) > self.usual_days(fam):
            if cancelled and not sends:
                sub = "reversed_not_reprocessed"
            else:
                sub = "print_batch" if est == "Batched for Printing" else "no_answer"
            return units + [("NEVER_ANSWERED", sub, "", fam, "", sd, owed, basis, wend, wend >= self.as_of)] * rest
        return units + [("IN_PROCESS", "", "", fam, "", sd, owed, basis, wend, False)] * rest

    @staticmethod
    def is_surgical(code):
        return len(code) == 5 and code.isdigit() and "10000" <= code <= "69999"

    def mppr_eligible_lines(self, p, own_lines, code):
        if not self.is_surgical(code):
            return False
        return any(L.lk not in own_lines and self.is_surgical(L.code) for L in p.lines)

    def mppr_eligible(self, A, code):
        """Half the rate is a multiple-procedure reduction only for a surgical code when the same processing has
        another surgical line (another code, or another line of the same code: a second lesion). A lone procedure
        paid at half its rate is short."""
        if not self.is_surgical(code):
            return False
        own = {L.lk for L in A.lines} if A.lines else set()
        return any(L.lk not in own and self.is_surgical(L.code) for L in A.p.lines)

    def compute_truth(self):
        for s in self.svcs.values():
            s.units = self.truth(s)
            if len(s.units) != s.U:
                raise AssertionError("unit count mismatch")

    def link_patients(self):
        """A unit not paid on its own bill is paid when the same patient's same service (code, component, date) stands
        paid on another bill (a new bill instead of a resubmission, a crossover filed apart)."""
        idx = defaultdict(set)
        for s in self.svcs.values():
            for pk in s.pks:
                idx[(pk, s.code, s.comp, s.dos)].add(s.key)
        n = 0
        for s in self.svcs.values():
            todo = [i for i, u in enumerate(s.units) if u[0] in LINKABLE]
            if not todo or not s.pks or not s.dos:
                continue
            others = set()
            for pk in s.pks:
                others |= idx[(pk, s.code, s.comp, s.dos)]
            best, n_paid = None, 0
            for o in others:
                so = self.svcs[o]
                if so.bill == s.bill:
                    continue
                paid = [u for u in so.units if u[0] in ("PAID", "SHORT") and u[1] != "paid_on_other_bill"]
                if len(paid) > n_paid:
                    best, n_paid = paid[0], len(paid)
            if not n_paid:
                continue
            for i in todo[:n_paid]:
                s.units[i] = ("PAID", "paid_on_other_bill", "", best[3], best[4], best[5], 0, "", "", False)
                n += 1
        self.stats["units_paid_on_other_bill"] = n

    # ------------------------------------------------------------------ items
    def map_findings(self):
        """Each finding -> parts: [(svc, units claimed)]."""
        R = self.R
        events = defaultdict(list)
        for e in R.events:
            events[e[0]].append(e)
        self.events = events
        faxed = defaultdict(list)
        for fid, status, sent, created, direction in R.faxes:
            if (status or "").lower() in ("failed", "error", "cancelled", "canceled"):
                continue
            if (direction or "out").lower() not in ("out", "outbound", "sent", ""):
                continue
            faxed[fid].append(iso(sent) or iso(created))
        self.faxed = faxed
        self.items = []
        line_twin = {}
        for p in self.procs.values():
            if p.copy_of is not None:
                kept = p.copy_of
                while kept.copy_of is not None:
                    kept = kept.copy_of
                for i, L in enumerate(p.lines):
                    if i < len(kept.lines):
                        line_twin[L.lk] = kept.lines[i]
        for f in R.findings:
            (fid, sig, cat, act, status, lk, ck, tk, pcn, pname, code, mods, sdate, pdate, charge, paid, expected,
             shortfall, carc, evidence, deadline, first_seen, recovered, wo) = f
            it = {"fid": fid, "sig": sig, "cat": cat, "status": status, "parts": [], "kind": "", "amount": shortfall,
                  "recovered": recovered, "first_seen": first_seen or "", "note": "", "line": None, "proc": None,
                  "ans": None}
            try:
                ev = json.loads(evidence) if evidence else {}
            except ValueError:
                ev = {}
            it["ev"] = ev if isinstance(ev, dict) else {}
            if sig in A_SIGNALS:
                it["kind"] = "A"
            elif sig in T_SIGNALS:
                it["kind"] = "T"
                ref = (it["ev"].get("reference") or "").upper()
                mm = EMBED_RE.search(ref)
                it["bill"] = mm.group(1) if mm else None
                t = R.tx.get(tk)
                it["fam"] = self.fams.of(t[1], t[2]) if t else None
                if it["bill"]:
                    parts = []
                    for key in sorted(self.bill_svcs.get(it["bill"], ())):
                        sv = self.svcs[key]
                        if any(A.p.fam == it["fam"] for A in sv.answers):
                            parts.append((sv, sv.U))
                    it["parts"] = parts
                    if not parts:
                        it["note"] = "takeback_bill_has_no_answer_of_this_payer"
            elif lk:
                L = self.lines.get(lk)
                if L is not None and L.p.copy_of is not None:
                    L = line_twin.get(lk, L)
                    it["note"] = "on_duplicate_delivery"
                if L is None or L.svc is None or L.ans is None:
                    it["kind"] = "X"
                    it["note"] = "line_not_found"
                else:
                    A = L.ans
                    it["line"], it["proc"], it["ans"] = L, L.p, A
                    X = A if A.split else L     # a split line is one billed line: the item claims the whole answer
                    if sig in U_SIGNALS:
                        it["kind"] = "U"
                        k = X.up if X.up > 0 else X.ub
                    else:
                        it["kind"] = "D"
                        k = X.ub - X.up if (X.paid > 0 and 0 < X.up < X.ub) else X.ub
                    it["parts"] = [(L.svc, max(1, k))]
            elif ck:
                p = self.procs.get(ck)
                if p is not None and p.copy_of is not None:
                    p = p.copy_of
                if p is None:
                    it["kind"] = "X"
                    it["note"] = "claim_not_found"
                else:
                    it["kind"] = "D"
                    it["proc"] = p
                    want = set()
                    for u in it["ev"].get("unpaid") or []:
                        if isinstance(u, list) and u:
                            want.add((str(u[0]).upper(), iso(u[1]) if len(u) > 1 else None))
                    agg = {}
                    for L in p.lines:
                        if L.svc is None or L.ans is None:
                            continue
                        if want and not any(w[0] == L.code and (w[1] is None or w[1] == L.dos) for w in want):
                            continue
                        agg[L.svc.key] = (L.svc, L.ans.ub)
                    if not agg and want:
                        for (c, d) in want:
                            for key in self.bill_svcs.get(p.bill, ()):
                                sv = self.svcs[key]
                                if sv.code == c and (d is None or sv.dos == d):
                                    agg[key] = (sv, sv.U)
                    it["parts"] = list(agg.values())
                    if not it["parts"]:
                        it["kind"] = "X"
                        it["note"] = "claim_services_not_found"
            elif sig in N_SIGNALS:
                it["kind"] = "N"
                bill, _ = split_claim_id(pcn)
                cnt = Counter()
                for ln in it["ev"].get("lines") or []:
                    if not isinstance(ln, list) or len(ln) < 2:
                        continue
                    c, d = str(ln[0]).upper(), iso(ln[1])
                    cands = [self.svcs[k] for k in self.bill_svcs.get(bill, ()) if k[1] == c and k[3] == d]
                    if not cands:
                        continue
                    cands.sort(key=lambda sv: sv.comp != "G")
                    cnt[cands[0].key] += 1
                it["parts"] = [(self.svcs[k], n) for k, n in cnt.items()]
                if not it["parts"]:
                    it["kind"] = "X"
                    it["note"] = "no_answer_services_not_found"
            else:
                it["kind"] = "X"
                it["note"] = "no_unit_reference"
            ev_kinds = [e[2] for e in events.get(fid, [])]
            closed_as_paid = status == "resolved" and (bool(it["ev"].get("paid_later"))
                                                       or "paid_on_later_remit" in ev_kinds or (recovered or 0) > 0)
            manual = [e for e in events.get(fid, []) if (e[6] or "system") != "system"]
            it["closed_as_paid"] = closed_as_paid and not manual
            it["person_decision"] = bool(manual) or status in PERSON_CLOSED
            it["is_open"] = status in OPEN_STATUSES
            it["todo"] = status == "open" and not faxed.get(fid)
            it["counted_elsewhere"] = bool(it["ev"].get("recovery_counted_elsewhere")) or bool(
                (it["ev"].get("paid_later") or {}).get("recovery_counted_elsewhere"))
            self.items.append(it)

    # ------------------------------------------------------------------ verdicts
    def gov_answer(self, u):
        L = self.lines.get(u[4]) if u[4] else None
        return L.ans if L is not None else None

    def u_done(self, it, u):
        """An underpayment-type item's unit is done when it went to the patient, settled, voided, or when a later
        answer raised its allowed amount (the shortfall was paid), or it stands paid on another bill."""
        if u[0] in ("MOVED_TO_PATIENT", "SETTLED_SECONDARY", "VOIDED", "ZERO_CHARGE"):
            return True
        if u[0] == "PAID" and u[1] == "paid_on_other_bill":
            return True
        A0 = it["ans"]
        if u[0] == "PAID" and A0 is not None:
            G = self.gov_answer(u)
            if G is not None and G is not A0 and G.p.ordk > A0.p.ordk and G.apu() >= A0.apu() + max(100, 0.01 * A0.apu()):
                return True
        return False

    def judge(self):
        on_svc = defaultdict(list)
        for it in self.items:
            for sv, k in it["parts"]:
                on_svc[sv.key].append(it)
        alloc = defaultdict(dict)   # fid -> {svc key: [unit indexes]}
        taken = defaultdict(set)    # svc key -> unit indexes claimed by open items

        def keeper_rank(it):
            A = it["ans"]
            p = it["proc"]
            if it["kind"] == "T":
                return (2, (), it["first_seen"], it["fid"])
            standing = 1 if (A is not None and A.standing) or (A is None and p is not None and p.cancelled_by is None) else 0
            date = p.date if p is not None else ""
            return (0 if standing else 1, tuple(-ord(ch) for ch in date), it["first_seen"], it["fid"])

        pre = {}
        for it in self.items:
            if not it["is_open"] or it["kind"] not in ("D", "U", "N", "T") or not it["parts"]:
                continue
            units = [u for sv, k in it["parts"] for u in sv.units]
            states = [u[0] for u in units]
            kind = it["kind"]
            if kind == "U":
                all_done = all(self.u_done(it, u) for u in units)
            elif kind == "T":
                all_done = all(s_ in DONE for s_ in states)
            else:
                all_done = all(s_ in DONE or s_ == "SHORT" for s_ in states)
            if all_done and states:
                pre[it["fid"]] = "ALREADY_DONE"
                continue
            owed_states = [s_ for s_ in states if s_ in OWED]
            if kind in ("D", "N") and owed_states and all(s_ == "WORKED_WAITING" for s_ in owed_states) and it["todo"]:
                pre[it["fid"]] = "ALREADY_WORKED"
        opens = {it["fid"] for it in self.items
                 if it["is_open"] and it["kind"] in ("D", "U", "N", "T") and it["parts"] and it["fid"] not in pre}
        for it in self.items:   # an item already worked still covers its owed units (they are not left on the table)
            if pre.get(it["fid"]) == "ALREADY_WORKED":
                for sv, k in it["parts"]:
                    for i, u in enumerate(sv.units):
                        if u[0] in OWED:
                            taken[sv.key].add(i)
        for key, its in on_svc.items():
            sv = self.svcs[key]
            cands = sorted([it for it in its if it["fid"] in opens], key=keeper_rank)
            for it in cands:
                k = dict((x.key, kk) for x, kk in it["parts"])[key]
                pref = self.pref_order(it, sv)
                got = [i for i in pref if i not in taken[key]][:k]
                for i in got:
                    taken[key].add(i)
                alloc[it["fid"]][key] = got
        self.alloc = alloc
        for it in self.items:
            fid = it["fid"]
            kind = it["kind"]
            v, sub, truth_c, k_claim, k_got, keeper = None, "", None, 0, 0, ""
            states = Counter()
            for sv, k in it["parts"]:
                k_claim += k
                for u in sv.units:
                    states[u[0]] += 1
            if kind == "A":
                v, sub = "NOT_A_UNIT", it["sig"].lower()
            elif kind == "X":
                v, sub = "UNDECIDED", it["note"]
            elif kind == "T" and not it.get("bill"):
                v, sub = "UNDECIDED", "no_claim_id_in_reference"
            elif kind == "T" and not it["parts"]:
                v, sub = "UNDECIDED", it["note"]
            elif kind == "T" and it["is_open"] and (it["amount"] or 0) > self.bill_paid_ever(it["bill"], it["fam"]) + 1:
                # the PLB amount is larger than everything the bill was ever paid by this payer: it recovers more than
                # this bill, so the bill's units cannot settle it
                v, sub = "UNDECIDED", "takeback_exceeds_bill_payments"
            elif it["is_open"]:
                if fid in pre:
                    v = pre[fid]
                    sub = ",".join(sorted(states))
                else:
                    got_units = [sv.units[i] for sv, k in it["parts"] for i in alloc[fid].get(sv.key, [])]
                    k_got = len(got_units)
                    if k_got == 0:
                        v, sub = "DUPLICATE", "all_units_on_another_open_item"
                        keeper = self.keeper_of(it, on_svc, keeper_rank)
                    elif kind == "T":
                        net = self.plb_net.get((it["fam"], it["bill"]), 0)
                        truth_c = net
                        owed_units = [u for u in got_units if u[0] in OWED]
                        if not owed_units:
                            v, sub = "UNDECIDED", "unit_" + Counter(u[0] for u in got_units).most_common(1)[0][0].lower()
                        elif net > 0 and it["amount"] is not None and abs(it["amount"] - net) > 1:
                            v, sub = "WRONG_AMOUNT", "takeback_net_differs"
                        else:
                            v, sub = "CORRECT", "" if net > 0 else "takeback_netted_on_plb_units_owed"
                    else:
                        v, sub, truth_c = self.judge_amount(it, got_units, k_claim)
                        if v in ("CORRECT", "WRONG_AMOUNT") and k_got < k_claim:
                            sub = (sub + ";" if sub else "") + "partly_duplicate"
            else:
                if not it["parts"]:
                    v, sub = "UNDECIDED", it["note"] or "no_units"
                elif it["closed_as_paid"] and kind in ("D", "U", "N"):
                    wrong = unknown = False
                    for sv, k in it["parts"]:
                        paid_n = sum(1 for u in sv.units if u[0] in ("PAID", "SHORT"))
                        done_n = sum(1 for u in sv.units if u[0] in DONE)
                        unk_n = sum(1 for u in sv.units if u[0] in UNKNOWN)
                        need = min(k, sv.U)
                        if paid_n >= need or done_n >= need:
                            continue
                        if done_n + unk_n >= need:
                            unknown = True
                            continue
                        wrong = True
                    if wrong:
                        v, sub = "CLOSED_WRONGLY", ",".join(sorted(states))
                        truth_c = sum(u[6] for sv, k in it["parts"] for u in sv.units if u[0] in OWED)
                    elif unknown:
                        v, sub = "UNDECIDED", "closed_on_unknown_unit"
                    else:
                        v, sub, truth_c = self.judge_recovery(it)
                else:
                    v, sub = "CORRECT", "closed_by_person" if it["person_decision"] else "closed_other"
            self.verdicts[fid] = {"verdict": v, "sub": sub, "truth": truth_c, "k_claim": k_claim, "k_got": k_got,
                                  "keeper": keeper, "states": dict(states)}
        self.taken = taken

    def bill_paid_ever(self, bill, fam):
        return sum(p.paid for p in self.by_bill.get(bill, []) if p.fam == fam and p.st != REVERSAL and p.paid > 0)

    def judge_amount(self, it, got_units, k_claim):
        owed_units = [u for u in got_units if u[0] in OWED]
        unknown_units = [u for u in got_units if u[0] in UNKNOWN]
        paid_units = [u for u in got_units if u[0] in DONE]
        truth_c = sum(u[6] for u in owed_units)
        amt = it["amount"]
        if not owed_units and unknown_units:
            return "UNDECIDED", "unit_" + Counter(u[0] for u in unknown_units).most_common(1)[0][0].lower(), None
        if it["kind"] == "U" and not owed_units and paid_units:
            if all(u[0] == "PAID" and u[1] in ("unrated", "reduced_service_modifier") for u in paid_units):
                return "UNDECIDED", "paid_no_payer_rate_to_judge", None
            return "WRONG_AMOUNT", "not_short", 0
        if not owed_units:
            return "UNDECIDED", "no_owed_unit", None
        if amt is None:
            sub = "item_has_no_amount"
        elif abs(amt - truth_c) <= max(1, k_claim):
            sub = ""
        elif all(u[7] == "charge" for u in owed_units):
            charge_cap = sum(sv.charge_pu * k for sv, k in it["parts"])
            if charge_cap and amt > charge_cap + 1:
                return "WRONG_AMOUNT", "over_billed_charge", truth_c
            sub = "amount_unverified_no_rate"
        else:
            return "WRONG_AMOUNT", self.amount_reason(it, amt, truth_c, k_claim), truth_c
        if all(not u[9] for u in owed_units):
            sub = (sub + ";" if sub else "") + "past_window"
        return "CORRECT", sub, truth_c

    def pref_order(self, it, sv):
        kind = it["kind"]
        idx = list(range(sv.U))
        if kind == "U":
            rank = {"SHORT": 0, "TAKEN_BACK": 1, "DENIED": 2, "PAID": 3}
        elif kind == "N":
            rank = {"NEVER_ANSWERED": 0, "WORKED_WAITING": 1, "DENIED": 2, "TAKEN_BACK": 3}
        elif kind == "T":
            rank = {"TAKEN_BACK": 0, "SHORT": 1, "DENIED": 2}
        else:
            rank = {"DENIED": 0, "WORKED_WAITING": 1, "TAKEN_BACK": 2, "NEVER_ANSWERED": 3}
        ok = [i for i in idx if sv.units[i][0] in OWED or sv.units[i][0] in UNKNOWN or
              (kind == "U" and sv.units[i][0] == "PAID" and not self.u_done(it, sv.units[i]))]
        ok.sort(key=lambda i: (rank.get(sv.units[i][0], 9), i))
        return ok

    def keeper_of(self, it, on_svc, keeper_rank):
        for sv, k in it["parts"]:
            others = [o for o in on_svc[sv.key] if o is not it and self.alloc.get(o["fid"], {}).get(sv.key)]
            if others:
                return sorted(others, key=keeper_rank)[0]["fid"]
        return ""

    def amount_reason(self, it, amt, truth_c, k_claim):
        A = it["ans"]
        charge = A.charge if A is not None else sum(sv.charge_pu * k for sv, k in it["parts"])
        if charge > 0 and amt > charge + 1:
            return "over_billed_charge"
        if k_claim > 1 and truth_c and abs(amt * k_claim - truth_c) <= k_claim:
            return "ignores_units"
        if any(sv.comp in ("26", "TC") for sv, k in it["parts"]):
            return "component_rate"
        if truth_c == 0:
            return "truth_zero"
        rel = abs(amt - truth_c) / max(1, truth_c)
        if rel <= 0.05:
            return "rate_within_5pct"
        if rel <= 0.25:
            return "rate_5_to_25pct"
        return "rate_over_25pct"

    def judge_recovery(self, it):
        """Closed as paid on a paid unit: is the recovered amount the unit's own standing payment after the denial?"""
        if it["counted_elsewhere"] or it["recovered"] is None:
            return "CORRECT", "recovery_counted_elsewhere" if it["counted_elsewhere"] else "", None
        p = it["proc"]
        after = p.date if p is not None else ""
        total = 0.0
        k_all = 0
        for sv, k in it["parts"]:
            k_all += k
            pays = [A for A in sv.answers if A.standing and A.p.st in PRIMARY and A.paid_tb > 0 and A.p.date >= after
                    and A is not it["ans"]]
            if not pays:
                linked = [u for u in sv.units if u[1] == "paid_on_other_bill"]
                if linked:
                    G = self.gov_answer(linked[0])
                    if G is not None:
                        pays = [G]
            units_left = k
            for A in sorted(pays, key=lambda A: A.p.ordk):
                if units_left <= 0:
                    break
                use = min(units_left, A.up or 1)
                total += A.paid_tb * use / max(1, A.up or 1)
                units_left -= use
        total = int(round(total))
        if abs((it["recovered"] or 0) - total) > max(1, k_all):
            return "WRONG_AMOUNT", "recovered_differs", total
        return "CORRECT", "", total

    # ------------------------------------------------------------------ money left on the table
    def left_on_table(self):
        closed_cover = defaultdict(int)
        decided = set()
        for it in self.items:
            if it["is_open"]:
                continue
            for sv, k in it["parts"]:
                if it["person_decision"]:
                    decided.add(sv.key)
                elif self.verdicts[it["fid"]]["verdict"] == "CLOSED_WRONGLY":
                    closed_cover[sv.key] = max(closed_cover[sv.key], k)
        self.lot = []
        self.cw_units = []
        for key, sv in self.svcs.items():
            tk = self.taken.get(key, set())
            cw = closed_cover.get(key, 0)
            for i, u in enumerate(sv.units):
                st = u[0]
                if st not in OWED or not u[9]:
                    continue
                if st == "WORKED_WAITING" and u[1] != "resent_overdue":
                    continue
                if i in tk or key in decided:
                    continue
                if cw > 0:
                    cw -= 1
                    self.cw_units.append((sv, i, u))
                    continue
                self.lot.append((sv, i, u))

    # ------------------------------------------------------------------ run
    def run(self):
        steps = [("processings", self.build_processings), ("dedupe", self.dedupe), ("reversals", self.pair_reversals),
                 ("takebacks", self.apply_takebacks), ("services", self.build_services),
                 ("claim_level", self.attach_claim_level), ("answers", self.build_answers),
                 ("timing", self.payer_timing), ("rates", self.build_rates), ("units_scope", self.units_and_scope),
                 ("truth", self.compute_truth), ("link_patients", self.link_patients), ("items", self.map_findings),
                 ("judge", self.judge), ("left_on_table", self.left_on_table)]
        for name, fn in steps:
            fn()
            self.log(f"  step {name}: done")
        return self


# ---------------------------------------------------------------------------------------------------------- outputs
def summarize(a):
    out = {"rules_version": RULES_VERSION, "as_of": a.as_of, "feed_date": a.feed_date, "stats": dict(a.stats),
           "money_cents": dict(a.money)}
    st = Counter()
    st_c = Counter()
    st_scope = Counter()
    st_scope_c = Counter()
    for sv in a.svcs.values():
        for u in sv.units:
            key = u[0] + ("" if u[0] not in OWED else (":inside" if u[9] else ":past_window"))
            st[key] += 1
            st_c[key] += u[6]
            if sv.in_scope:
                st_scope[key] += 1
                st_scope_c[key] += u[6]
    out["units_all"] = {k: [st[k], st_c[k]] for k in sorted(st)}
    out["units_in_scope"] = {k: [st_scope[k], st_scope_c[k]] for k in sorted(st_scope)}
    out["scope_basis"] = dict(Counter(sv.scope_basis for sv in a.svcs.values() if sv.in_scope))
    vc = Counter()
    vc_c = Counter()
    vsub = Counter()
    by_scope = Counter()
    for it in a.items:
        v = a.verdicts[it["fid"]]
        key = (v["verdict"], "open" if it["is_open"] else "closed")
        vc[key] += 1
        vc_c[key] += (it["amount"] or 0) if it["is_open"] else (it["recovered"] or 0)
        vsub[(v["verdict"], v["sub"].split(";")[0] if v["sub"] else "")] += 1
        sc = any(sv.in_scope for sv, k in it["parts"])
        by_scope[(v["verdict"], "in_scope" if sc else "out_or_none")] += 1
    out["verdicts"] = {f"{k[0]}|{k[1]}": [vc[k], vc_c[k]] for k in sorted(vc)}
    out["verdict_subs"] = {f"{k[0]}|{k[1]}": n for k, n in sorted(vsub.items())}
    out["verdicts_by_scope"] = {f"{k[0]}|{k[1]}": n for k, n in sorted(by_scope.items())}
    lot = Counter()
    lot_c = Counter()
    for sv, i, u in a.lot:
        k = (u[0], "in_scope" if sv.in_scope else "out_of_scope")
        lot[k] += 1
        lot_c[k] += u[6]
    out["left_on_table"] = {f"{k[0]}|{k[1]}": [lot[k], lot_c[k]] for k in sorted(lot)}
    out["closed_wrongly_units"] = len(a.cw_units)
    out["usual_answer_days"] = {"global": a.global_usual}
    return out


def write_outputs(a, out_dir):
    os.makedirs(out_dir, mode=0o700, exist_ok=True)
    path = os.path.join(out_dir, "audit_result.sqlite")
    if os.path.exists(path):
        os.remove(path)
    con = sqlite3.connect(path)
    os.chmod(path, 0o600)
    con.execute("""create table units (bill TEXT, code TEXT, comp TEXT, dos TEXT, units_billed INTEGER, unit_from INTEGER,
        unit_to INTEGER, state TEXT, sub TEXT, carc TEXT, fam TEXT, gov_line_key TEXT, gov_date TEXT, owed_cents_per_unit
        INTEGER, amount_basis TEXT, window_end TEXT, inside_window INTEGER, in_scope INTEGER, scope_basis TEXT,
        open_items TEXT, closed_items TEXT, left_on_table INTEGER)""")
    con.execute("""create table verdicts (finding_id TEXT PRIMARY KEY, signal TEXT, category TEXT, status TEXT, kind TEXT,
        verdict TEXT, sub TEXT, item_cents INTEGER, truth_cents INTEGER, recovered_cents INTEGER, units_claimed INTEGER,
        units_allocated INTEGER, unit_states TEXT, keeper TEXT, in_scope INTEGER, line_key TEXT, claim_key TEXT,
        svc_keys TEXT, note TEXT)""")
    lot_set = {(sv.key, i) for sv, i, u in a.lot}
    open_on = defaultdict(list)
    closed_on = defaultdict(list)
    for it in a.items:
        for sv, k in it["parts"]:
            (open_on if it["is_open"] else closed_on)[sv.key].append(it["fid"])
    rows = []
    for key, sv in a.svcs.items():
        i = 0
        while i < len(sv.units):
            j = i
            lot_i = (key, i) in lot_set
            while j + 1 < len(sv.units) and sv.units[j + 1] == sv.units[i] and ((key, j + 1) in lot_set) == lot_i:
                j += 1
            u = sv.units[i]
            rows.append((sv.bill, sv.code, sv.comp, sv.dos, sv.U, i + 1, j + 1, u[0], u[1], u[2], u[3], u[4], u[5], u[6],
                         u[7], u[8], 1 if u[9] else 0, 1 if sv.in_scope else 0, sv.scope_basis,
                         ",".join(open_on.get(key, [])), ",".join(closed_on.get(key, [])), 1 if lot_i else 0))
            i = j + 1
    con.executemany("insert into units values (" + ",".join("?" * 22) + ")", rows)
    vrows = []
    for it in a.items:
        v = a.verdicts[it["fid"]]
        L = it["line"]
        vrows.append((it["fid"], it["sig"], it["cat"], it["status"], it["kind"], v["verdict"], v["sub"], it["amount"],
                      v["truth"], it["recovered"], v["k_claim"], v["k_got"], json.dumps(v["states"], sort_keys=True),
                      v["keeper"], 1 if any(sv.in_scope for sv, k in it["parts"]) else 0, L.lk if L is not None else None,
                      it["proc"].ck if it["proc"] is not None else None,
                      json.dumps(["|".join(sv.key) for sv, k in it["parts"]]), it["note"]))
    con.executemany("insert into verdicts values (" + ",".join("?" * 19) + ")", vrows)
    con.commit()
    for table, name in (("units", "truth_units.tsv"), ("verdicts", "finding_verdicts.tsv")):
        tpath = os.path.join(out_dir, name)
        cur = con.execute(f"select * from {table}")
        with open(tpath, "w") as fh:
            fh.write("\t".join(d[0] for d in cur.description) + "\n")
            for row in cur:
                fh.write("\t".join("" if x is None else str(x).replace("\t", " ") for x in row) + "\n")
        os.chmod(tpath, 0o600)
    con.close()
    summary = summarize(a)
    spath = os.path.join(out_dir, "audit_summary.json")
    with open(spath, "w") as fh:
        json.dump(summary, fh, indent=1, sort_keys=True)
    os.chmod(spath, 0o600)
    return path, spath, summary


def open_ro(path):
    uri = "file:" + urllib.parse.quote(os.path.abspath(path)) + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


# -------------------------------------------------------------------------------------------------------- self-test
SELFTEST_SCHEMA = """
create table transactions (transaction_key TEXT PRIMARY KEY, payer_name TEXT, payer_id TEXT, payment_date TEXT,
  trace_number TEXT, superseded INTEGER DEFAULT 0, envelope_index INTEGER);
create table claims (claim_key TEXT PRIMARY KEY, transaction_key TEXT, claim_seq INTEGER, patient_control_number TEXT,
  claim_status_code TEXT, charge_cents INTEGER, paid_cents INTEGER, patient_responsibility_cents INTEGER,
  claim_filing_indicator TEXT, payer_claim_control_number TEXT, facility_type_code TEXT, claim_frequency_code TEXT,
  statement_from TEXT, payer_name TEXT, payer_id TEXT, payment_date TEXT, crossover_carrier_name TEXT,
  rendering_provider_npi TEXT, patient_last TEXT, patient_first TEXT, patient_id TEXT, insured_id TEXT);
create table lines (line_key TEXT PRIMARY KEY, claim_key TEXT, line_seq INTEGER, procedure_qualifier TEXT,
  procedure_code TEXT, modifiers TEXT, charge_cents INTEGER, paid_cents INTEGER, paid_units REAL,
  original_procedure_qualifier TEXT, original_procedure_code TEXT, original_modifiers TEXT, original_units REAL,
  service_date TEXT, service_period_start TEXT, allowed_cents INTEGER, rendering_provider_npi TEXT);
create table adjustments (id INTEGER PRIMARY KEY AUTOINCREMENT, claim_key TEXT, line_key TEXT, level TEXT,
  group_code TEXT, reason_code TEXT, amount_cents INTEGER);
create table remarks (id INTEGER PRIMARY KEY AUTOINCREMENT, claim_key TEXT, line_key TEXT, level TEXT, code TEXT);
create table plb (id INTEGER PRIMARY KEY AUTOINCREMENT, transaction_key TEXT, reason_code TEXT, reference TEXT,
  amount_cents INTEGER, payer_name TEXT, payer_id TEXT, payment_date TEXT);
create table claims_837 (claim_837_key TEXT PRIMARY KEY, bht_created TEXT, patient_control_number TEXT,
  total_charge_cents INTEGER, frequency_code TEXT, payer_name TEXT, payer_id TEXT, service_date TEXT,
  place_of_service TEXT, subscriber_id TEXT);
create table lines_837 (line_837_key TEXT PRIMARY KEY, claim_837_key TEXT, line_seq INTEGER, procedure_code TEXT,
  modifiers TEXT, charge_cents INTEGER, units REAL, service_date TEXT);
create table ema_rows (row_fingerprint TEXT PRIMARY KEY, family TEXT, source_name TEXT, row_index INTEGER,
  service_date TEXT, bill_created TEXT, claim_id TEXT, bill_item_id TEXT, cpt TEXT, modifiers TEXT, payer TEXT,
  coverage_type TEXT, claim_status TEXT, claim_submission_date TEXT, denial_date TEXT, charges_cents INTEGER,
  units REAL, policy_number TEXT, activity_type TEXT);
create table ema_sources (source_name TEXT PRIMARY KEY, created_at TEXT);
create table findings (finding_id TEXT PRIMARY KEY, signal TEXT, category TEXT, actionable TEXT, status TEXT,
  line_key TEXT, claim_key TEXT, transaction_key TEXT, patient_control_number TEXT, payer_name TEXT,
  procedure_code TEXT, modifiers TEXT, service_date TEXT, payment_date TEXT, charge_cents INTEGER, paid_cents INTEGER,
  expected_cents INTEGER, shortfall_cents INTEGER, carc TEXT, evidence TEXT, appeal_deadline TEXT, first_seen TEXT,
  recovered_cents INTEGER, written_off_cents INTEGER);
create table finding_events (id INTEGER PRIMARY KEY AUTOINCREMENT, finding_id TEXT, at TEXT, kind TEXT,
  from_status TEXT, to_status TEXT, amount_cents INTEGER, actor TEXT, note TEXT, run_id TEXT);
create table finding_heirs (old_id TEXT PRIMARY KEY, new_id TEXT, at TEXT, run_id TEXT);
"""


class Fake:
    """Invented data only: made-up claim ids (ZZ99...), payers (TESTPAY), keys and amounts."""

    def __init__(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(SELFTEST_SCHEMA)
        self.n = 0
        self.expect_unit = {}     # (bill, code, comp, dos) -> expected state of unit 1
        self.expect_units_billed = {}   # (bill, code, comp, dos) -> expected units billed
        self.expect_unit_counts = {}    # (bill, code, comp, dos) -> {state: count}
        self.expect_item = {}     # finding id -> expected verdict

    def key(self, prefix):
        self.n += 1
        return f"{prefix}{self.n:06d}"

    def tx(self, date, payer="TESTPAY ONE", pid="T0001", trace=None, sup=0):
        tk = self.key("tx")
        self.con.execute("insert into transactions values (?,?,?,?,?,?,?)",
                         (tk, payer, pid, date, trace or tk, sup, 0))
        return tk

    def claim(self, tk, pcn, st, charge, paid, lines, prod="12", clp07=None, freq="1", ccas=(), payer="TESTPAY ONE",
              pid="T0001", date=None, xover=None, patient=None):
        ck = self.key("cl")
        d = date or self.con.execute("select payment_date from transactions where transaction_key=?", (tk,)).fetchone()[0]
        last, first = patient or ("TESTLAST" + pcn[:9], "TESTFIRST")
        self.con.execute("insert into claims values (" + ",".join("?" * 22) + ")",
                         (ck, tk, 1, pcn, st, charge, paid, 0, prod, clp07 or ("PCC" + pcn), "11", freq, None, payer,
                          pid, d, xover, "1000000001", last, first, "M" + last, "M" + last))
        for g, r, a in ccas:
            self.con.execute("insert into adjustments (claim_key, line_key, level, group_code, reason_code, amount_cents)"
                             " values (?,?,?,?,?,?)", (ck, None, "claim", g, r, a))
        keys = []
        for i, ln in enumerate(lines):
            lk = self.key("ln")
            code, mods, lcharge, lpaid, units, dos = ln["code"], ln.get("mods", []), ln["charge"], ln["paid"], \
                ln.get("units", 1), ln["dos"]
            allowed = ln.get("allowed")
            self.con.execute("insert into lines values (" + ",".join("?" * 17) + ")",
                             (lk, ck, i + 1, "HC", code, json.dumps(mods) if mods else None, lcharge, lpaid,
                              ln.get("paid_units", units if lpaid else 0), None, None, None,
                              ln.get("orig_units", units if not lpaid else None), dos, None, allowed, "1000000001"))
            for g, r, a in ln.get("cas", []):
                self.con.execute("insert into adjustments (claim_key, line_key, level, group_code, reason_code, "
                                 "amount_cents) values (?,?,?,?,?,?)", (ck, lk, "line", g, r, a))
            keys.append(lk)
        return ck, keys

    def send837(self, pcn, bht, lines, freq="1", payer="TESTPAY ONE", pid="T0001"):
        k = self.key("c8")
        self.con.execute("insert into claims_837 values (?,?,?,?,?,?,?,?,?,?)",
                         (k, bht, pcn, sum(l_["charge"] for l_ in lines), freq, payer, pid, lines[0]["dos"], "11", None))
        for i, ln in enumerate(lines):
            self.con.execute("insert into lines_837 values (?,?,?,?,?,?,?,?)",
                             (self.key("l8"), k, i + 1, ln["code"], ",".join(ln.get("mods", [])) or None, ln["charge"],
                              ln.get("units", 1), ln["dos"]))

    def finding(self, signal, cat, status="open", line_key=None, claim_key=None, pcn=None, shortfall=None,
                evidence=None, tk=None, recovered=None, expect=None, events=()):
        fid = "%024x" % (0xF00000 + self.n)
        self.n += 1
        self.con.execute("insert into findings values (" + ",".join("?" * 24) + ")",
                         (fid, signal, cat, "appeal", status, line_key, claim_key, tk, pcn, "TESTPAY ONE", None, None,
                          None, None, None, None, shortfall, shortfall, None, json.dumps(evidence or {}), None,
                          "2026-01-01T00:00:00+00:00", recovered, None))
        for kind, to_status, actor in events:
            self.con.execute("insert into finding_events (finding_id, at, kind, from_status, to_status, amount_cents, "
                             "actor) values (?,?,?,?,?,?,?)", (fid, "2026-06-01T00:00:00+00:00", kind, "open",
                                                               to_status, 0, actor))
        if expect:
            self.expect_item[fid] = expect
        return fid


def L_(code, charge, paid, dos, **kw):
    d = {"code": code, "charge": charge, "paid": paid, "dos": dos}
    d.update(kw)
    return d


def build_selftest_store():
    F = Fake()
    # a rate base: TESTPAY ONE pays 99213 at 10000 (allowed) on clean lines; 88305-26 at 4000; 11103 at 3000/unit
    for i in range(6):
        tk = F.tx("2026-02-%02d" % (i + 1))
        F.claim(tk, "ZZ01RAT%02dC017" % i, "1", 15000, 8000, [
            L_("99213", 15000, 8000, "2026-01-%02d" % (i + 1), allowed=10000, cas=[("CO", "45", 5000), ("PR", "2", 2000)])])
        F.claim(tk, "ZZ01RBT%02dC017" % i, "1", 6000, 4000, [
            L_("88305", 6000, 4000, "2026-01-%02d" % (i + 1), mods=["26"], allowed=4000, cas=[("CO", "45", 2000)])])
        F.claim(tk, "ZZ01RCT%02dC017" % i, "1", 9000, 6000, [
            L_("11103", 9000, 6000, "2026-01-%02d" % (i + 1), units=2, allowed=6000, cas=[("CO", "45", 3000)])])
    # keep the payer's feed live up to the feed date
    F.claim(F.tx("2026-11-20"), "ZZ01LIVE1C017", "1", 15000, 8000,
            [L_("99213", 15000, 8000, "2026-10-01", allowed=10000, cas=[("CO", "45", 5000), ("PR", "2", 2000)])])

    # 1 paid later on another claim id: denial item -> ALREADY_DONE
    t1 = F.tx("2026-03-01")
    ck, (l1,) = F.claim(t1, "ZZ02AAAA1C017", "1", 15000, 0, [L_("99213", 15000, 0, "2026-02-10", cas=[("CO", "16", 15000)])])
    F.claim(F.tx("2026-04-01"), "ZZ02AAAA1C024", "1", 15000, 8000,
            [L_("99213", 15000, 8000, "2026-02-10", allowed=10000, cas=[("CO", "45", 5000), ("PR", "2", 2000)])])
    F.finding("DENIED_MISSING_INFO", "denial", line_key=l1, shortfall=10000, expect="ALREADY_DONE")
    F.expect_unit[("ZZ02AAAA1", "99213", "G", "2026-02-10")] = "PAID"

    # 2 reversal: P1 denied (item A), reversed, P2 denied again (item B) -> B correct, A duplicate
    t2 = F.tx("2026-05-01")
    ck1, (la,) = F.claim(t2, "ZZ03BBBB1C017", "1", 15000, 0, [L_("99213", 15000, 0, "2026-04-02", cas=[("CO", "50", 15000)])],
                         clp07="ICN1")
    t3 = F.tx("2026-06-01")
    F.claim(t3, "ZZ03BBBB1C017", "22", -15000, 0, [L_("99213", -15000, 0, "2026-04-02", cas=[("CO", "50", -15000)])],
            clp07="ICN1")
    ck2, (lb,) = F.claim(t3, "ZZ03BBBB1C017", "1", 15000, 0, [L_("99213", 15000, 0, "2026-04-02", cas=[("CO", "50", 15000)])],
                         clp07="ICN2")
    F.finding("DENIED_MEDICAL_NECESSITY", "denial", line_key=la, shortfall=10000, expect="DUPLICATE")
    F.finding("DENIED_MEDICAL_NECESSITY", "denial", line_key=lb, shortfall=10000, expect="CORRECT")
    F.expect_unit[("ZZ03BBBB1", "99213", "G", "2026-04-02")] = "DENIED"

    # 3 take-back by PLB WO whose reference embeds the claim id, no reversal: closed-as-paid item -> CLOSED_WRONGLY
    t4 = F.tx("2026-05-05")
    ck, (lt,) = F.claim(t4, "ZZ04CCCC1C017", "1", 15000, 0, [L_("99213", 15000, 0, "2026-04-03", cas=[("CO", "197", 15000)])])
    t5 = F.tx("2026-06-05")
    F.claim(t5, "ZZ04CCCC1C024", "1", 15000, 8000,
            [L_("99213", 15000, 8000, "2026-04-03", allowed=10000, cas=[("CO", "45", 5000), ("PR", "2", 2000)])])
    t6 = F.tx("2026-07-05")
    F.con.execute("insert into plb (transaction_key, reason_code, reference, amount_cents, payer_name, payer_id, "
                  "payment_date) values (?,?,?,?,?,?,?)", (t6, "WO", "123456789012345    ZZ04CCCC1C024", 8000,
                                                           "TESTPAY ONE", "T0001", "2026-07-05"))
    F.finding("DENIED_AUTH", "denial", status="resolved", line_key=lt, shortfall=10000, recovered=8000,
              evidence={"paid_later": {"paid_cents": 8000}}, events=[("paid_on_later_remit", "resolved", "system")],
              expect="CLOSED_WRONGLY")
    F.expect_unit[("ZZ04CCCC1", "99213", "G", "2026-04-03")] = "TAKEN_BACK"

    # 3b FB and WO netting to zero: no take-back; unit stays paid
    t7 = F.tx("2026-05-06")
    F.claim(t7, "ZZ05DDDD1C017", "1", 15000, 8000,
            [L_("99213", 15000, 8000, "2026-04-04", allowed=10000, cas=[("CO", "45", 5000), ("PR", "2", 2000)])])
    for rc, amt in (("WO", 8000), ("FB", -8000)):
        F.con.execute("insert into plb (transaction_key, reason_code, reference, amount_cents, payer_name, payer_id, "
                      "payment_date) values (?,?,?,?,?,?,?)", (t7, rc, "99999 ZZ05DDDD1C017", amt, "TESTPAY ONE",
                                                               "T0001", "2026-05-06"))
    F.expect_unit[("ZZ05DDDD1", "99213", "G", "2026-04-04")] = "PAID"

    # 4 COB denial CO-22, later processed by the same payer as secondary: denial item -> ALREADY_DONE
    t8 = F.tx("2026-03-10")
    ck, (lc,) = F.claim(t8, "ZZ06EEEE1C017", "4", 15000, 0, [L_("99213", 15000, 0, "2026-03-01", cas=[("CO", "22", 15000)])])
    t9 = F.tx("2026-04-10")
    F.claim(t9, "ZZ06EEEE1C017", "2", 15000, 500, [L_("99213", 15000, 500, "2026-03-01", cas=[("OA", "23", 14500)])])
    F.finding("DENIED_MISSING_PRIMARY", "denial", line_key=lc, shortfall=10000, expect="ALREADY_DONE")
    F.expect_unit[("ZZ06EEEE1", "99213", "G", "2026-03-01")] = "SETTLED_SECONDARY"

    # 5 void: denied, then the practice voided the claim (837 frequency 8): denial item -> ALREADY_DONE
    t10 = F.tx("2026-07-10")
    ck, (lv,) = F.claim(t10, "ZZ07FFFF1C017", "1", 15000, 0, [L_("99213", 15000, 0, "2026-07-01", cas=[("CO", "16", 15000)])])
    F.send837("ZZ07FFFF1C017", "2026-07-02", [L_("99213", 15000, 0, "2026-07-01")])
    F.send837("ZZ07FFFF1C017", "2026-07-20", [L_("99213", 15000, 0, "2026-07-01")], freq="8")
    F.finding("DENIED_MISSING_INFO", "denial", line_key=lv, shortfall=10000, expect="ALREADY_DONE")
    F.expect_unit[("ZZ07FFFF1", "99213", "G", "2026-07-01")] = "VOIDED"

    # 6 resubmitted after the denial, no answer yet: To-do item -> ALREADY_WORKED
    t11 = F.tx("2026-10-20")
    ck, (lw,) = F.claim(t11, "ZZ08GGGG1C017", "1", 15000, 0, [L_("99213", 15000, 0, "2026-10-01", cas=[("CO", "16", 15000)])])
    F.send837("ZZ08GGGG1C017", "2026-10-05", [L_("99213", 15000, 0, "2026-10-01")])
    F.send837("ZZ08GGGG1C024", "2026-11-10", [L_("99213", 15000, 0, "2026-10-01")], freq="7")
    F.finding("DENIED_MISSING_INFO", "denial", line_key=lw, shortfall=10000, expect="ALREADY_WORKED")
    F.expect_unit[("ZZ08GGGG1", "99213", "G", "2026-10-01")] = "WORKED_WAITING"

    # 7 never answered: sent long ago, payer's feed is live, nothing back. No item -> LEFT_ON_TABLE.
    F.send837("ZZ09HHHH1C017", "2026-06-01", [L_("99213", 15000, 0, "2026-05-28")])
    F.expect_unit[("ZZ09HHHH1", "99213", "G", "2026-05-28")] = "NEVER_ANSWERED"
    # 7b never answered with a NO_ANSWER item -> CORRECT
    F.send837("ZZ09HHHH2C017", "2026-06-01", [L_("99213", 15000, 0, "2026-05-29")])
    F.finding("NO_ANSWER", "denial", pcn="ZZ09HHHH2C017", shortfall=10000,
              evidence={"lines": [["99213", "2026-05-29", "15000", "10000"]]}, expect="CORRECT")
    # 7c payer with no remittances at all in the feed -> NO_FEED (unknown, not left on the table)
    F.send837("ZZ09HHHH3C017", "2026-06-01", [L_("99213", 15000, 0, "2026-05-30")], payer="NOERA PLAN", pid="N0001")
    F.expect_unit[("ZZ09HHHH3", "99213", "G", "2026-05-30")] = "NO_FEED"

    # 8 short paid: allowed 6000 against the payer's 10000 -> SHORT 4000; item 4000 CORRECT; another 5000 WRONG_AMOUNT
    t12 = F.tx("2026-09-01")
    ck, (ls1,) = F.claim(t12, "ZZ10IIII1C017", "1", 15000, 6000,
                         [L_("99213", 15000, 6000, "2026-08-01", allowed=6000, cas=[("CO", "45", 9000)])])
    F.finding("UNDERPAID_VS_OWN_RATE", "underpayment", line_key=ls1, shortfall=4000, expect="CORRECT")
    ck, (ls2,) = F.claim(t12, "ZZ10IIII2C017", "1", 15000, 6000,
                         [L_("99213", 15000, 6000, "2026-08-02", allowed=6000, cas=[("CO", "45", 9000)])])
    F.finding("UNDERPAID_VS_OWN_RATE", "underpayment", line_key=ls2, shortfall=5000, expect="WRONG_AMOUNT")
    F.expect_unit[("ZZ10IIII1", "99213", "G", "2026-08-01")] = "SHORT"
    # 8b paid at the rate, underpayment item -> WRONG_AMOUNT (not short)
    ck, (ls3,) = F.claim(t12, "ZZ10IIII3C017", "1", 15000, 8000,
                         [L_("99213", 15000, 8000, "2026-08-03", allowed=10000, cas=[("CO", "45", 5000), ("PR", "2", 2000)])])
    F.finding("UNDERPAID_VS_OWN_RATE", "underpayment", line_key=ls3, shortfall=1500, expect="WRONG_AMOUNT")

    # 9 units: 2 units of 11103 denied; item sized at one unit -> WRONG_AMOUNT
    t13 = F.tx("2026-09-02")
    ck, (lu,) = F.claim(t13, "ZZ11JJJJ1C017", "1", 9000, 0,
                        [L_("11103", 9000, 0, "2026-08-04", units=2, cas=[("CO", "50", 9000)])])
    F.finding("DENIED_MEDICAL_NECESSITY", "denial", line_key=lu, shortfall=3000, expect="WRONG_AMOUNT")

    # 10 26/TC: 26 denied, TC paid; closed-as-paid item on the 26 -> CLOSED_WRONGLY
    t14 = F.tx("2026-09-03")
    ck, (l26, ltc) = F.claim(t14, "ZZ12KKKK1C017", "1", 12000, 3000, [
        L_("88305", 6000, 0, "2026-08-05", mods=["26"], cas=[("CO", "4", 6000)]),
        L_("88305", 6000, 3000, "2026-08-05", mods=["TC"], allowed=3000, cas=[("CO", "45", 3000)])])
    F.finding("DENIED_CODING", "denial", status="resolved", line_key=l26, shortfall=4000, recovered=3000,
              evidence={"paid_later": {"paid_cents": 3000}}, events=[("paid_on_later_remit", "resolved", "system")],
              expect="CLOSED_WRONGLY")
    F.expect_unit[("ZZ12KKKK1", "88305", "26", "2026-08-05")] = "DENIED"
    F.expect_unit[("ZZ12KKKK1", "88305", "TC", "2026-08-05")] = "PAID"

    # 11 moved to the patient (deductible): denial-type item -> ALREADY_DONE
    t15 = F.tx("2026-09-04")
    ck, (lp,) = F.claim(t15, "ZZ13LLLL1C017", "1", 15000, 0,
                        [L_("99213", 15000, 0, "2026-08-06", allowed=10000, cas=[("CO", "45", 5000), ("PR", "1", 10000)])])
    F.finding("DENIED_NONCOVERED", "denial", line_key=lp, shortfall=10000, expect="ALREADY_DONE")
    F.expect_unit[("ZZ13LLLL1", "99213", "G", "2026-08-06")] = "MOVED_TO_PATIENT"

    # 12 duplicate across resubmissions (both denials standing): latest keeps, earlier is DUPLICATE
    t16 = F.tx("2026-09-05")
    ck, (ld1,) = F.claim(t16, "ZZ14MMMM1C017", "1", 15000, 0, [L_("99213", 15000, 0, "2026-08-07", cas=[("CO", "50", 15000)])])
    t17 = F.tx("2026-10-05")
    ck, (ld2,) = F.claim(t17, "ZZ14MMMM1C024", "1", 15000, 0, [L_("99213", 15000, 0, "2026-08-07", cas=[("CO", "50", 15000)])])
    F.finding("DENIED_MEDICAL_NECESSITY", "denial", line_key=ld1, shortfall=10000, expect="DUPLICATE")
    F.finding("DENIED_MEDICAL_NECESSITY", "denial", line_key=ld2, shortfall=10000, expect="CORRECT")

    # 13 past window (commercial 180 days): denial long ago, no item -> not left on the table
    t18 = F.tx("2026-02-01")
    F.claim(t18, "ZZ15NNNN1C017", "1", 15000, 0, [L_("99213", 15000, 0, "2025-12-05", cas=[("CO", "50", 15000)])])
    F.expect_unit[("ZZ15NNNN1", "99213", "G", "2025-12-05")] = "DENIED"

    # 14 denial inside the window with no item -> LEFT_ON_TABLE
    t19 = F.tx("2026-10-15")
    F.claim(t19, "ZZ16OOOO1C017", "1", 15000, 0, [L_("99213", 15000, 0, "2026-09-15", cas=[("CO", "50", 15000)])])
    F.expect_unit[("ZZ16OOOO1", "99213", "G", "2026-09-15")] = "DENIED"

    # 15 paid before the denial (duplicate claim denied CO-18): item -> ALREADY_DONE
    t20 = F.tx("2026-09-10")
    F.claim(t20, "ZZ17PPPP1C017", "1", 15000, 8000,
            [L_("99213", 15000, 8000, "2026-08-10", allowed=10000, cas=[("CO", "45", 5000), ("PR", "2", 2000)])])
    t21 = F.tx("2026-10-10")
    ck, (lq,) = F.claim(t21, "ZZ17PPPP1C024", "1", 15000, 0, [L_("99213", 15000, 0, "2026-08-10", cas=[("CO", "18", 15000)])])
    F.finding("DENIED_DUPLICATE_UNPAID", "denial", line_key=lq, shortfall=10000, expect="ALREADY_DONE")

    # 16 claim-level denial with no lines (codes from the 837): claim item -> CORRECT
    F.send837("ZZ18QQQQ1C017", "2026-09-01", [L_("99213", 15000, 0, "2026-08-20")])
    t22 = F.tx("2026-09-20")
    ckq, _ = F.claim(t22, "ZZ18QQQQ1C017", "4", 15000, 0, [], ccas=[("CO", "16", 15000)])
    F.con.execute("update claims set statement_from='2026-08-20' where claim_key=?", (ckq,))
    F.finding("CLAIM_DENIED_UNPAID", "denial", claim_key=ckq, shortfall=10000,
              evidence={"codes": ["99213"], "unpaid": [["99213", "2026-08-20"]]}, expect="CORRECT")
    F.expect_unit[("ZZ18QQQQ1", "99213", "G", "2026-08-20")] = "DENIED"

    # 17 answered by a secondary only (primary's remittance missing) -> SECONDARY_ONLY; NO_ANSWER item -> UNDECIDED
    F.send837("ZZ19RRRR1C017", "2026-06-01", [L_("99213", 15000, 0, "2026-05-20")])
    t23 = F.tx("2026-07-01", payer="TESTPAY TWO", pid="T0002")
    F.claim(t23, "ZZ19RRRR1C017", "2", 15000, 400, [L_("99213", 15000, 400, "2026-05-20", cas=[("OA", "23", 14600)])],
            payer="TESTPAY TWO", pid="T0002")
    F.finding("NO_ANSWER", "denial", pcn="ZZ19RRRR1C017", shortfall=10000,
              evidence={"lines": [["99213", "2026-05-20", "15000", "10000"]]}, expect="UNDECIDED")
    F.expect_unit[("ZZ19RRRR1", "99213", "G", "2026-05-20")] = "SECONDARY_ONLY"

    # 18 the same paid processing delivered twice (same check): counted once -> PAID, not double
    t24 = F.tx("2026-09-12", trace="CHK1")
    t25 = F.tx("2026-09-12", trace="CHK1", sup=1)
    for tk in (t24, t25):
        F.claim(tk, "ZZ20SSSS1C017", "1", 15000, 8000,
                [L_("99213", 15000, 8000, "2026-08-11", allowed=10000, cas=[("CO", "45", 5000), ("PR", "2", 2000)])])
    F.expect_unit[("ZZ20SSSS1", "99213", "G", "2026-08-11")] = "PAID"

    # 19 a reversal of a processing not on file cancels nothing else (T-I5): unit stays paid
    t26 = F.tx("2026-09-13")
    F.claim(t26, "ZZ21TTTT1C017", "1", 15000, 8000,
            [L_("99213", 15000, 8000, "2026-08-12", allowed=10000, cas=[("CO", "45", 5000), ("PR", "2", 2000)])])
    t27 = F.tx("2026-10-13")
    F.claim(t27, "ZZ21TTTT1C017", "22", -15000, -7000, [L_("99213", -15000, -7000, "2026-08-12")])
    F.expect_unit[("ZZ21TTTT1", "99213", "G", "2026-08-12")] = "PAID"

    # 20 a payment reversed and the reprocessing denies; the old closed-as-paid item -> CLOSED_WRONGLY
    t28 = F.tx("2026-09-14")
    ck, (lr1,) = F.claim(t28, "ZZ22UUUU1C017", "1", 15000, 0, [L_("99213", 15000, 0, "2026-08-13", cas=[("CO", "16", 15000)])])
    t29 = F.tx("2026-09-30")
    F.claim(t29, "ZZ22UUUU1C024", "1", 15000, 8000,
            [L_("99213", 15000, 8000, "2026-08-13", allowed=10000, cas=[("CO", "45", 5000), ("PR", "2", 2000)])], clp07="X1")
    t30 = F.tx("2026-10-30")
    F.claim(t30, "ZZ22UUUU1C024", "22", -15000, -8000, [L_("99213", -15000, -8000, "2026-08-13")], clp07="X1")
    F.claim(t30, "ZZ22UUUU1C024", "1", 15000, 0, [L_("99213", 15000, 0, "2026-08-13", cas=[("CO", "50", 15000)])], clp07="X2")
    F.finding("DENIED_MISSING_INFO", "denial", status="resolved", line_key=lr1, shortfall=10000, recovered=8000,
              evidence={"paid_later": {"paid_cents": 8000}}, events=[("paid_on_later_remit", "resolved", "system")],
              expect="CLOSED_WRONGLY")
    F.expect_unit[("ZZ22UUUU1", "99213", "G", "2026-08-13")] = "DENIED"

    # 21 a correctly closed item: denial later paid in full; recovered equals the payment -> CORRECT
    t31 = F.tx("2026-09-15")
    ck, (lok,) = F.claim(t31, "ZZ23VVVV1C017", "1", 15000, 0, [L_("99213", 15000, 0, "2026-08-14", cas=[("CO", "16", 15000)])])
    t32 = F.tx("2026-10-15")
    F.claim(t32, "ZZ23VVVV1C024", "1", 15000, 8000,
            [L_("99213", 15000, 8000, "2026-08-14", allowed=10000, cas=[("CO", "45", 5000), ("PR", "2", 2000)])])
    F.finding("DENIED_MISSING_INFO", "denial", status="resolved", line_key=lok, shortfall=10000, recovered=8000,
              evidence={"paid_later": {"paid_cents": 8000}}, events=[("paid_on_later_remit", "resolved", "system")],
              expect="CORRECT")
    # 21b closed as paid with the wrong recovered amount -> WRONG_AMOUNT (recovered differs)
    t33 = F.tx("2026-09-16")
    ck, (lok2,) = F.claim(t33, "ZZ23VVVV2C017", "1", 15000, 0, [L_("99213", 15000, 0, "2026-08-15", cas=[("CO", "16", 15000)])])
    t34 = F.tx("2026-10-16")
    F.claim(t34, "ZZ23VVVV2C024", "1", 15000, 8000,
            [L_("99213", 15000, 8000, "2026-08-15", allowed=10000, cas=[("CO", "45", 5000), ("PR", "2", 2000)])])
    F.finding("DENIED_MISSING_INFO", "denial", status="resolved", line_key=lok2, shortfall=10000, recovered=0,
              evidence={"paid_later": {"paid_cents": 8000}}, events=[("paid_on_later_remit", "resolved", "system")],
              expect="WRONG_AMOUNT")
    # 22 aggregate item -> NOT_A_UNIT
    F.finding("FEE_SKIM", "fees", shortfall=500, expect="NOT_A_UNIT")

    # 18b a payment delivered twice (same check), then reversed once and re-processed as a denial: the unit is denied
    t46 = F.tx("2026-09-12", trace="CHK2")
    t47 = F.tx("2026-09-12", trace="CHK2", sup=1)
    for tk_ in (t46, t47):
        F.claim(tk_, "ZZ36GGGG1C017", "1", 15000, 8000,
                [L_("99213", 15000, 8000, "2026-08-16", allowed=10000, cas=[("CO", "45", 5000), ("PR", "2", 2000)])],
                clp07="D1")
    t48 = F.tx("2026-10-12")
    F.claim(t48, "ZZ36GGGG1C017", "22", -15000, -8000,
            [L_("99213", -15000, -8000, "2026-08-16", cas=[("CO", "45", -5000), ("PR", "2", -2000)])], clp07="D1")
    F.claim(t48, "ZZ36GGGG1C017", "1", 15000, 0, [L_("99213", 15000, 0, "2026-08-16", cas=[("CO", "50", 15000)])],
            clp07="D2")
    F.expect_unit[("ZZ36GGGG1", "99213", "G", "2026-08-16")] = "DENIED"

    # 26b paid at the rate, reversed and re-paid at the same allowed: two underpayment items, one unit -> the old one
    # is a DUPLICATE, the new one says short where the unit is paid in full -> WRONG_AMOUNT
    t49 = F.tx("2026-09-09")
    ck, (lva,) = F.claim(t49, "ZZ37HHHH1C017", "1", 15000, 8000,
                         [L_("99213", 15000, 8000, "2026-08-28", allowed=10000, cas=[("CO", "45", 5000), ("PR", "2", 2000)])],
                         clp07="V1")
    t50 = F.tx("2026-10-09")
    F.claim(t50, "ZZ37HHHH1C017", "22", -15000, -8000,
            [L_("99213", -15000, -8000, "2026-08-28", cas=[("CO", "45", -5000), ("PR", "2", -2000)])], clp07="V1")
    ck, (lvb,) = F.claim(t50, "ZZ37HHHH1C017", "1", 15000, 7000,
                         [L_("99213", 15000, 7000, "2026-08-28", allowed=10000, cas=[("CO", "45", 5000), ("PR", "2", 3000)])],
                         clp07="V2")
    F.finding("UNDERPAID_VS_OWN_RATE", "underpayment", line_key=lva, shortfall=1200, expect="DUPLICATE")
    F.finding("UNDERPAID_VS_OWN_RATE", "underpayment", line_key=lvb, shortfall=1200, expect="WRONG_AMOUNT")
    # 26c short, then reversed and re-paid at the payer's rate: the underpayment item -> ALREADY_DONE
    t51 = F.tx("2026-09-10")
    ck, (lwa,) = F.claim(t51, "ZZ38IIII1C017", "1", 15000, 5000,
                         [L_("99213", 15000, 5000, "2026-08-29", allowed=6000, cas=[("CO", "45", 9000), ("PR", "2", 1000)])],
                         clp07="W1")
    t52 = F.tx("2026-10-10")
    F.claim(t52, "ZZ38IIII1C017", "22", -15000, -5000,
            [L_("99213", -15000, -5000, "2026-08-29", cas=[("CO", "45", -9000), ("PR", "2", -1000)])], clp07="W1")
    F.claim(t52, "ZZ38IIII1C017", "1", 15000, 8000,
            [L_("99213", 15000, 8000, "2026-08-29", allowed=10000, cas=[("CO", "45", 5000), ("PR", "2", 2000)])], clp07="W2")
    F.finding("UNDERPAID_VS_OWN_RATE", "underpayment", line_key=lwa, shortfall=4000, expect="ALREADY_DONE")

    # 30 one remittance lists the corrected processing first, then the reversal of an older version not on file with
    # the same amounts: the corrected processing stands (paid)
    t53 = F.tx("2026-10-14")
    F.claim(t53, "ZZ39JJJJ1C017", "1", 15000, 8000,
            [L_("99213", 15000, 8000, "2026-09-03", allowed=10000, cas=[("CO", "45", 5000), ("PR", "2", 2000)])], clp07="N2")
    F.claim(t53, "ZZ39JJJJ1C017", "22", -15000, -8000,
            [L_("99213", -15000, -8000, "2026-09-03", cas=[("CO", "45", -5000), ("PR", "2", -2000)])], clp07="N1")
    F.con.execute("update claims set claim_seq=2 where claim_key=(select max(claim_key) from claims)")
    F.expect_unit[("ZZ39JJJJ1", "99213", "G", "2026-09-03")] = "PAID"
    # 31 a payer that never pays a code (ten answers, none paid): a denial of it is not money owed
    for i in range(10):
        F.claim(F.tx("2026-0%d-1%d" % (3 + i % 6, i % 10)), "ZZ40KK%02dKC017" % i, "1", 2000, 0,
                [L_("99024", 2000, 0, "2026-02-1%d" % (i % 10), cas=[("CO", "97", 2000)])])
    F.expect_unit[("ZZ40KK00K", "99024", "G", "2026-02-10")] = "NOT_PAYABLE"
    # 31b a payer that denies a code every time for missing authorization: still denied and owed, not "not payable"
    for i in range(10):
        F.claim(F.tx("2026-0%d-2%d" % (3 + i % 6, i % 8)), "ZZ47RR%02dRC017" % i, "1", 20000, 0,
                [L_("G6001", 20000, 0, "2026-02-2%d" % (i % 8), cas=[("CO", "197", 20000)])])
    F.expect_unit[("ZZ47RR00R", "G6001", "G", "2026-02-20")] = "DENIED"
    # 32 billed as 26, answered without the modifier: the answer is the 26's answer (paid), not a never-answered 26
    F.send837("ZZ41LLLL1C017", "2026-09-01", [L_("88305", 6000, 0, "2026-08-30", mods=["26"])])
    F.claim(F.tx("2026-09-21"), "ZZ41LLLL1C017", "1", 6000, 4000,
            [L_("88305", 6000, 4000, "2026-08-30", allowed=4000, cas=[("CO", "45", 2000)])])
    F.expect_unit[("ZZ41LLLL1", "88305", "26", "2026-08-30")] = "PAID"

    # 33 a payer writes the modifier into the code field: "99213 25" answers the billed 99213-25
    F.send837("ZZ42MMMM1C017", "2026-09-01", [L_("99213", 15000, 0, "2026-08-31", mods=["25"])])
    F.claim(F.tx("2026-09-22"), "ZZ42MMMM1C017", "1", 15000, 8000,
            [L_("99213 25", 15000, 8000, "2026-08-31", allowed=10000, cas=[("CO", "45", 5000), ("PR", "2", 2000)])])
    F.expect_unit[("ZZ42MMMM1", "99213", "G", "2026-08-31")] = "PAID"
    # 34 a whole-claim denial listing one of two billed lines denies both
    F.send837("ZZ43NNNN1C017", "2026-09-02", [L_("99213", 15000, 0, "2026-09-01"), L_("11102", 9000, 0, "2026-09-01")])
    F.claim(F.tx("2026-09-23"), "ZZ43NNNN1C017", "4", 24000, 0,
            [L_("99213", 15000, 0, "2026-09-01", cas=[("CO", "16", 15000)])])
    F.expect_unit[("ZZ43NNNN1", "11102", "G", "2026-09-01")] = "DENIED"

    # 35 EMA's export repeats a charge in two weeks (one without the item id) and lists its payment row: one unit
    for i, (src, item, act, ch) in enumerate((("exp1", "IT1", "Charge", 15000), ("exp2", None, "Charge", 15000),
                                             ("exp2", None, "Payment", 0))):
        F.con.execute("insert into ema_rows (row_fingerprint, family, source_name, row_index, service_date, claim_id, "
                      "bill_item_id, cpt, payer, coverage_type, charges_cents, units, activity_type) values "
                      "(?,?,?,?,?,?,?,?,?,?,?,?,?)", (f"fp{i}", "charge_lines", src, i, "2026-09-04", "ZZ44OOOO1C017",
                                                       item, "99213", "TESTPAY ONE", "Primary", ch, 1, act))
    F.con.execute("insert into ema_sources values ('exp1', '2026-10-01T00:00:00Z'), ('exp2', '2026-10-08T00:00:00Z')")
    F.claim(F.tx("2026-09-24"), "ZZ44OOOO1C017", "1", 15000, 8000,
            [L_("99213", 15000, 8000, "2026-09-04", allowed=10000, cas=[("CO", "45", 5000), ("PR", "2", 2000)])])
    F.expect_units_billed[("ZZ44OOOO1", "99213", "G", "2026-09-04")] = 1
    F.expect_unit_counts[("ZZ44OOOO1", "99213", "G", "2026-09-04")] = {"PAID": 1}

    # 36 the same claim sent twice on one day is one unit; two equal lines of one code, one item each: both correct
    F.send837("ZZ45PPPP1C017", "2026-09-05", [L_("99213", 15000, 0, "2026-09-04")])
    F.send837("ZZ45PPPP1C017", "2026-09-05", [L_("99213", 15000, 0, "2026-09-04")])
    F.claim(F.tx("2026-09-25"), "ZZ45PPPP1C017", "1", 15000, 0, [L_("99213", 15000, 0, "2026-09-04", cas=[("CO", "50", 15000)])])
    F.expect_units_billed[("ZZ45PPPP1", "99213", "G", "2026-09-04")] = 1
    ck, (le1, le2) = F.claim(F.tx("2026-09-26"), "ZZ46QQQQ1C017", "1", 18000, 0, [
        L_("11103", 9000, 0, "2026-09-05", cas=[("CO", "197", 9000)]),
        L_("11103", 9000, 0, "2026-09-05", mods=["59"], cas=[("CO", "197", 9000)])])
    F.finding("DENIED_AUTH", "denial", line_key=le1, shortfall=3000, expect="CORRECT")
    F.finding("DENIED_AUTH", "denial", line_key=le2, shortfall=3000, expect="CORRECT")

    # 37 on one day a whole-claim denial and a processing to the deductible: the processing decides (to the patient)
    F.send837("ZZ48SSSS1C017", "2026-09-06", [L_("99213", 15000, 0, "2026-09-06")])
    F.claim(F.tx("2026-09-27", trace="ZZT1"), "ZZ48SSSS1C017", "1", 15000, 0,
            [L_("99213", 15000, 0, "2026-09-06", allowed=10000, cas=[("CO", "45", 5000), ("PR", "1", 10000)])], clp07="S1")
    F.claim(F.tx("2026-09-27", trace="ZZT0"), "ZZ48SSSS1C017", "4", 15000, 0,
            [L_("99213", 15000, 0, "2026-09-06", cas=[("CO", "129", 15000)])], clp07="S2")
    F.expect_unit[("ZZ48SSSS1", "99213", "G", "2026-09-06")] = "MOVED_TO_PATIENT"

    # 38 primary denies as non-covered, another payer pays as secondary: settled (paid by the other payer)
    F.claim(F.tx("2026-09-28"), "ZZ49TTTT1C017", "1", 15000, 0, [L_("99213", 15000, 0, "2026-09-07", cas=[("CO", "96", 15000)])])
    F.claim(F.tx("2026-10-20", payer="TESTPAY TWO", pid="T0002"), "ZZ49TTTT1C017", "2", 15000, 9000,
            [L_("99213", 15000, 9000, "2026-09-07", allowed=9500, cas=[("CO", "45", 5500), ("PR", "3", 500)])],
            payer="TESTPAY TWO", pid="T0002")
    F.expect_unit[("ZZ49TTTT1", "99213", "G", "2026-09-07")] = "PAID"
    # 39 allowed at a lower level and sent to the deductible (CO-186 + PR-1): short by the level difference
    F.claim(F.tx("2026-09-29"), "ZZ50UUUU1C017", "1", 15000, 0,
            [L_("99213", 15000, 0, "2026-09-08", cas=[("CO", "186", 5000), ("PR", "1", 7000), ("CO", "45", 3000)])])
    F.expect_unit[("ZZ50UUUU1", "99213", "G", "2026-09-08")] = "SHORT"
    # 40 an E/M paid at exactly half the payer's rate: E/M is never reduced as a multiple procedure -> short
    F.claim(F.tx("2026-09-30"), "ZZ51VVVV1C017", "1", 15000, 4000,
            [L_("99213", 15000, 4000, "2026-09-09", allowed=5000, cas=[("CO", "45", 10000), ("PR", "2", 1000)])])
    F.expect_unit[("ZZ51VVVV1", "99213", "G", "2026-09-09")] = "SHORT"
    # 40b a second surgical procedure paid at half the rate, beside another surgery: multiple-procedure reduction
    for i in range(4):
        F.claim(F.tx("2026-0%d-1%d" % (4 + i, 2 + i)), "ZZ51SG%02dXC017" % i, "1", 20000, 8000,
                [L_("11402", 20000, 8000, "2026-03-1%d" % (2 + i), allowed=10000, cas=[("CO", "45", 10000), ("PR", "2", 2000)])])
    F.claim(F.tx("2026-10-05"), "ZZ51SGXXXC017", "1", 40000, 12000, [
        L_("11402", 20000, 8000, "2026-09-14", allowed=10000, cas=[("CO", "45", 10000), ("PR", "2", 2000)]),
        L_("11403", 20000, 4000, "2026-09-14", allowed=5000, cas=[("CO", "45", 15000), ("PR", "2", 1000)])])
    for i in range(4):
        F.claim(F.tx("2026-0%d-2%d" % (4 + i, 2 + i)), "ZZ51SH%02dXC017" % i, "1", 20000, 8000,
                [L_("11403", 20000, 8000, "2026-03-2%d" % (2 + i), allowed=10000, cas=[("CO", "45", 10000), ("PR", "2", 2000)])])
    F.expect_unit[("ZZ51SGXXX", "11403", "G", "2026-09-14")] = "PAID"

    # 41 a CO-18 duplicate denial after a CO-50 denial: the reason shown is CO-50, and no patient amount is borrowed
    F.claim(F.tx("2026-10-01"), "ZZ52WWWW1C017", "1", 15000, 0,
            [L_("99213", 15000, 0, "2026-09-10", cas=[("CO", "50", 15000)])])
    F.claim(F.tx("2026-10-11"), "ZZ52WWWW1C024", "1", 15000, 0,
            [L_("99213", 15000, 0, "2026-09-10", cas=[("CO", "18", 15000)])])
    F.expect_unit[("ZZ52WWWW1", "99213", "G", "2026-09-10")] = "DENIED"

    # 42 Mohs paid at half its rate with no other surgery on the claim: short, not a multiple-procedure reduction
    for i in range(4):
        F.claim(F.tx("2026-0%d-0%d" % (4 + i, 2 + i)), "ZZ53MO%02dXC017" % i, "1", 90000, 50000,
                [L_("17311", 90000, 50000, "2026-03-0%d" % (2 + i), allowed=60000, cas=[("CO", "45", 30000), ("PR", "2", 10000)])])
    F.claim(F.tx("2026-10-02"), "ZZ53MOXXXC017", "1", 90000, 25000,
            [L_("17311", 90000, 25000, "2026-09-11", allowed=30000, cas=[("CO", "45", 60000), ("PR", "2", 5000)])])
    F.expect_unit[("ZZ53MOXXX", "17311", "G", "2026-09-11")] = "SHORT"
    # 42b a second Mohs lesion (second line of the same code) at half the rate beside the first: not short
    F.claim(F.tx("2026-10-06"), "ZZ53MOYYYC017", "1", 180000, 75000, [
        L_("17311", 90000, 50000, "2026-09-15", allowed=60000, cas=[("CO", "45", 30000), ("PR", "2", 10000)]),
        L_("17311", 90000, 25000, "2026-09-15", mods=["59"], allowed=30000, cas=[("CO", "45", 60000), ("PR", "2", 5000)])])
    F.expect_unit_counts[("ZZ53MOYYY", "17311", "G", "2026-09-15")] = {"PAID": 2}
    # 43 a drug line answered for 1 of 4 billed units, nothing denied: the 3 others are short on units
    F.send837("ZZ54DRUG1C017", "2026-09-12", [L_("J3301", 4000, 0, "2026-09-12", units=4)])
    F.claim(F.tx("2026-10-03"), "ZZ54DRUG1C017", "1", 4000, 900,
            [L_("J3301", 4000, 900, "2026-09-12", units=1, allowed=1000, cas=[("CO", "45", 3000), ("PR", "2", 100)])])
    F.expect_unit_counts[("ZZ54DRUG1", "J3301", "G", "2026-09-12")] = {"PAID": 1, "SHORT": 3}
    # 44 two lines of one code, one paid with coinsurance, one denied CO-151: the denied unit is denied (not
    # "to the patient" because of the paid line's coinsurance)
    F.claim(F.tx("2026-10-04"), "ZZ55PART1C017", "1", 7300, 3000, [
        L_("77401", 3650, 3000, "2026-09-13", allowed=3650, cas=[("PR", "2", 650)]),
        L_("77401", 3650, 0, "2026-09-13", mods=["59"], cas=[("CO", "151", 3650)])], prod="MB")
    F.expect_unit_counts[("ZZ55PART1", "77401", "G", "2026-09-13")] = {"PAID": 1, "DENIED": 1}

    # 45 an underpayment item where the payer has no established rate for the code: cannot be judged -> UNDECIDED
    ck, (lnr3,) = F.claim(F.tx("2026-10-07", payer="TESTPAY RARE", pid="T0005"), "ZZ56RATE1C017", "1", 9000, 3000,
                          [L_("17250", 9000, 3000, "2026-09-16", allowed=3000, cas=[("CO", "45", 6000)])],
                          payer="TESTPAY RARE", pid="T0005")
    F.finding("UNDERPAID_VS_OWN_RATE", "underpayment", line_key=lnr3, shortfall=1000, expect="UNDECIDED")

    # 46 a "previously paid" denial (B13) split from the copay line (PR-3): still a denial, the copay is not the allowed
    for i in range(3):
        F.claim(F.tx("2026-0%d-0%d" % (4 + i, 3 + i), payer="TESTPAY SPLIT", pid="T0003"), "ZZ57SP%02dXC017" % i, "1",
                15000, 6000, [L_("99213", 15000, 6000, "2026-03-0%d" % (3 + i), allowed=10000, cas=[("CO", "45", 5000), ("PR", "3", 4000)])],
                payer="TESTPAY SPLIT", pid="T0003", prod="16")
    F.claim(F.tx("2026-10-08", payer="TESTPAY SPLIT", pid="T0003"), "ZZ57SPXXXC017", "1", 15000, 0, [
        L_("99213", 11000, 0, "2026-09-17", cas=[("CO", "B13", 11000)]),
        L_("99213", 4000, 0, "2026-09-17", allowed=4000, cas=[("PR", "3", 4000)])], payer="TESTPAY SPLIT", pid="T0003", prod="16")
    F.expect_unit[("ZZ57SPXXX", "99213", "G", "2026-09-17")] = "DENIED"

    # 23 a payer splits one billed 99213 into three lines (paid part, copay, contractual): one unit, paid
    F.send837("ZZ24WWWW1C017", "2026-09-01", [L_("99213", 15000, 0, "2026-08-25")])
    t35 = F.tx("2026-09-20", payer="TESTPAY SPLIT", pid="T0003")
    ck, (lsa, lsb, lsc) = F.claim(t35, "ZZ24WWWW1C017", "1", 15000, 6000, [
        L_("99213", 9000, 6000, "2026-08-25", allowed=6000, cas=[("CO", "45", 3000)]),
        L_("99213", 4000, 0, "2026-08-25", allowed=4000, cas=[("PR", "3", 4000)]),
        L_("99213", 2000, 0, "2026-08-25", cas=[("CO", "45", 2000)])], payer="TESTPAY SPLIT", pid="T0003", prod="16")
    F.finding("DENIED_NONCOVERED", "denial", line_key=lsc, shortfall=2000, expect="ALREADY_DONE")
    F.expect_unit[("ZZ24WWWW1", "99213", "G", "2026-08-25")] = "PAID"
    F.expect_units_billed[("ZZ24WWWW1", "99213", "G", "2026-08-25")] = 1

    # 23b the same split with no 837 on file: still one unit, paid
    t35b = F.tx("2026-09-21", payer="TESTPAY SPLIT", pid="T0003")
    F.claim(t35b, "ZZ24WWWW2C017", "1", 15000, 6000, [
        L_("99213", 9000, 6000, "2026-08-26", allowed=6000, cas=[("CO", "45", 3000)]),
        L_("99213", 4000, 0, "2026-08-26", allowed=4000, cas=[("PR", "3", 4000)]),
        L_("99213", 2000, 0, "2026-08-26", cas=[("CO", "45", 2000)])], payer="TESTPAY SPLIT", pid="T0003", prod="16")
    F.expect_units_billed[("ZZ24WWWW2", "99213", "G", "2026-08-26")] = 1
    F.expect_unit_counts[("ZZ24WWWW2", "99213", "G", "2026-08-26")] = {"PAID": 1}

    # 24 the same patient's same service paid on another bill: the denial (B13) item -> ALREADY_DONE
    t36 = F.tx("2026-08-01")
    F.claim(t36, "ZZ31BBBB1C017", "1", 15000, 8000,
            [L_("99213", 15000, 8000, "2026-07-15", allowed=10000, cas=[("CO", "45", 5000), ("PR", "2", 2000)])],
            patient=("TESTLASTX", "TESTFIRSTX"))
    t37 = F.tx("2026-09-01")
    ck, (lxb,) = F.claim(t37, "ZZ30AAAA1C017", "1", 15000, 0, [L_("99213", 15000, 0, "2026-07-15", cas=[("CO", "B13", 15000)])],
                         patient=("TESTLASTX", "TESTFIRSTX"))
    F.finding("DENIED_DUPLICATE_UNPAID", "denial", line_key=lxb, shortfall=10000, expect="ALREADY_DONE")
    F.expect_unit[("ZZ30AAAA1", "99213", "G", "2026-07-15")] = "PAID"

    # 25 a supplement paying the coinsurance "as primary" does not pay a second unit: 2 units, 1 paid, 1 denied
    t38 = F.tx("2026-09-05")
    F.claim(t38, "ZZ32CCCC1C017", "1", 7300, 3000, [
        L_("77401", 3650, 3000, "2026-08-26", allowed=3650, cas=[("PR", "2", 650)]),
        L_("77401", 3650, 0, "2026-08-26", mods=["59"], cas=[("CO", "112", 3650)])], prod="MB")
    t39 = F.tx("2026-09-25", payer="TESTPAY SUPP", pid="T0004")
    F.claim(t39, "ZZ32CCCC1C017", "1", 7300, 650, [
        L_("77401", 3650, 650, "2026-08-26", allowed=650, cas=[("CO", "45", 3000)]),
        L_("77401", 3650, 0, "2026-08-26", mods=["59"], cas=[("CO", "96", 3650)])], payer="TESTPAY SUPP", pid="T0004")
    F.expect_unit_counts[("ZZ32CCCC1", "77401", "G", "2026-08-26")] = {"PAID": 1, "DENIED": 1}

    # 26 an underpayment re-processed (reversal) at the same allowed: the old item is a DUPLICATE of the new one
    t40 = F.tx("2026-09-08")
    ck, (lua,) = F.claim(t40, "ZZ33DDDD1C017", "1", 15000, 5000,
                         [L_("99213", 15000, 5000, "2026-08-27", allowed=6000, cas=[("CO", "45", 9000), ("PR", "2", 1000)])],
                         clp07="U1")
    t41 = F.tx("2026-10-08")
    F.claim(t41, "ZZ33DDDD1C017", "22", -15000, -5000,
            [L_("99213", -15000, -5000, "2026-08-27", cas=[("CO", "45", -9000), ("PR", "2", -1000)])], clp07="U1")
    ck, (lub,) = F.claim(t41, "ZZ33DDDD1C017", "1", 15000, 4000,
                         [L_("99213", 15000, 4000, "2026-08-27", allowed=6000, cas=[("CO", "45", 9000), ("PR", "2", 2000)])],
                         clp07="U2")
    F.finding("UNDERPAID_VS_OWN_RATE", "underpayment", line_key=lua, shortfall=4000, expect="DUPLICATE")
    F.finding("UNDERPAID_VS_OWN_RATE", "underpayment", line_key=lub, shortfall=4000, expect="CORRECT")

    # 27 no known rate for the payer and code: the item's amount cannot be checked unless it exceeds the charge
    t42 = F.tx("2026-10-02", payer="TESTPAY RARE", pid="T0005")
    ck, (lnr1, lnr2) = F.claim(t42, "ZZ34EEEE1C017", "1", 18000, 0, [
        L_("17000", 9000, 0, "2026-09-02", cas=[("CO", "50", 9000)]),
        L_("17110", 9000, 0, "2026-09-02", cas=[("CO", "50", 9000)])], payer="TESTPAY RARE", pid="T0005")
    F.finding("DENIED_MEDICAL_NECESSITY", "denial", line_key=lnr1, shortfall=5000, expect="CORRECT")
    F.finding("DENIED_MEDICAL_NECESSITY", "denial", line_key=lnr2, shortfall=9500, expect="WRONG_AMOUNT")

    # 28 a take-back recovered after the payer's own reversal and re-payment at its rate: TAKEBACK item -> ALREADY_DONE
    t43 = F.tx("2026-08-10")
    F.claim(t43, "ZZ35FFFF1C017", "1", 15000, 8000,
            [L_("99213", 15000, 8000, "2026-07-20", allowed=10000, cas=[("CO", "45", 5000), ("PR", "2", 2000)])], clp07="R1")
    t44 = F.tx("2026-09-10")
    F.claim(t44, "ZZ35FFFF1C017", "22", -15000, -8000,
            [L_("99213", -15000, -8000, "2026-07-20", cas=[("CO", "45", -5000), ("PR", "2", -2000)])], clp07="R1")
    F.claim(t44, "ZZ35FFFF1C017", "1", 15000, 6000,
            [L_("99213", 15000, 6000, "2026-07-20", allowed=10000, cas=[("CO", "45", 5000), ("PR", "2", 4000)])], clp07="R2")
    t45 = F.tx("2026-10-10")
    for tk_, rc, amt in ((t44, "FB", -2000), (t45, "WO", 2000)):
        F.con.execute("insert into plb (transaction_key, reason_code, reference, amount_cents, payer_name, payer_id, "
                      "payment_date) values (?,?,?,?,?,?,?)", (tk_, rc, "998877 ZZ35FFFF1C017", amt, "TESTPAY ONE",
                                                               "T0001", "2026-09-10"))
    F.finding("TAKEBACK", "fees", tk=t45, shortfall=2000, evidence={"reference": "998877 ZZ35FFFF1C017",
                                                                     "reason_code": "WO"}, expect="ALREADY_DONE")
    F.finding("TAKEBACK", "fees", tk=t45, shortfall=900000, evidence={"reference": "998877 ZZ35FFFF1C017",
                                                                       "reason_code": "WO"}, expect="UNDECIDED")
    # 29 a take-back with no reversal on file: the unit stands taken back; TAKEBACK item -> CORRECT
    F.finding("TAKEBACK", "fees", tk=t6, shortfall=8000, evidence={"reference": "123456789012345    ZZ04CCCC1C024",
                                                                    "reason_code": "WO"}, expect="CORRECT")
    F.con.commit()
    return F


def selftest():
    F = build_selftest_store()
    raw = load(F.con)
    a = Audit(raw, "2026-11-23", log=lambda *x: None).run()
    fails = []
    for key, want in F.expect_unit.items():
        sv = a.svcs.get(key)
        got = sv.units[0][0] if sv and sv.units else None
        if got != want:
            fails.append(f"unit {key[1]}/{key[2]} expected {want} got {got} ({sv.units[0] if sv and sv.units else ''})")
    for fid, want in F.expect_item.items():
        got = a.verdicts[fid]["verdict"]
        if got != want:
            fails.append(f"item {fid} expected {want} got {got} ({a.verdicts[fid]['sub']})")
    lot_keys = {sv.key for sv, i, u in a.lot}
    for key, want_in in ((("ZZ09HHHH1", "99213", "G", "2026-05-28"), True),
                         (("ZZ16OOOO1", "99213", "G", "2026-09-15"), True),
                         (("ZZ15NNNN1", "99213", "G", "2025-12-05"), False),
                         (("ZZ09HHHH3", "99213", "G", "2026-05-30"), False),
                         (("ZZ09HHHH2", "99213", "G", "2026-05-29"), False),
                         (("ZZ03BBBB1", "99213", "G", "2026-04-02"), False)):
        if (key in lot_keys) != want_in:
            fails.append(f"left-on-table {key[1]} expected {want_in}")
    F.expect_units_billed[("ZZ11JJJJ1", "11103", "G", "2026-08-04")] = 2
    for key, want in F.expect_units_billed.items():
        sv = a.svcs.get(key)
        if sv is None or sv.U != want:
            fails.append(f"units billed {key[1]} expected {want} got {sv.U if sv else None}")
    for key, want in F.expect_unit_counts.items():
        sv = a.svcs.get(key)
        got = dict(Counter(u[0] for u in sv.units)) if sv else {}
        if got != want:
            fails.append(f"unit states {key[1]} expected {want} got {got}")
    if a.stats.get("copy_same_check") != 2:
        fails.append(f"duplicate delivery expected 2 copies got {a.stats.get('copy_same_check')}")
    if a.stats.get("reversal_line_not_on_file") != 2:
        fails.append(f"reversals not on file expected 2 got {a.stats.get('reversal_line_not_on_file')}")
    n_checks = len(F.expect_unit) + len(F.expect_item) + len(F.expect_units_billed) + len(F.expect_unit_counts) + 8
    if fails:
        print("SELFTEST FAILED:")
        for f in fails:
            print("  ", f)
        return 1
    print(f"SELFTEST PASSED: {n_checks} checks ({len(F.expect_unit)} unit truths, {len(F.expect_item)} item verdicts, "
          f"{len(F.expect_units_billed)} units-billed and {len(F.expect_unit_counts)} unit-mix checks, 6 left-on-table "
          f"checks, duplicate delivery, reversal not on file)")
    return 0


# ------------------------------------------------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db")
    ap.add_argument("--out")
    ap.add_argument("--as-of", default="2026-11-23")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.db or not args.out:
        ap.error("--db and --out are required (or --selftest)")
    con = open_ro(args.db)
    raw = load(con)
    a = Audit(raw, args.as_of).run()
    path, spath, summary = write_outputs(a, args.out)
    print(json.dumps({"verdicts": summary["verdicts"], "left_on_table": summary["left_on_table"],
                      "feed_date": summary["feed_date"]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
