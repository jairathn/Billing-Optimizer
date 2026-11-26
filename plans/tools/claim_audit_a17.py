#!/usr/bin/env python3
"""claim_audit_a16.py: the independent claim auditor (rules version A16).

What it does
  1. Reads a copy of the RemitWatch store READ-ONLY (sqlite URI, mode=ro): the raw 835 tables (transactions, claims,
     lines, adjustments = CAS, remarks = RARC, plb), the 837 tables (claims_837, lines_837), EMA's weekly exports
     (ema_rows, ema_sources), RemitWatch's own items (findings, finding_events, finding_heirs, faxes if present), and
     (new in A16) EMA's books as the EMA readers stored them: the Production posting ledger (a separate sqlite file,
     tables postings and names), the A/R report (ema_ar), the claim-link map (ema_link_map, ema_claim_links) and the
     check match (ema_checks).
  2. Decides, from that raw data alone, the truth of every unit of service:
        unit = (bill = claim id without its resubmission suffix, billed code, component G/26/TC, date of service,
                unit index 1..units billed)
  3. Classifies every RemitWatch finding against that truth, and lists every owed unit that has no item.

A16 changes against A15 (the hand calibration of 2026-11-24, calibration_notes.md section 3 and 4): EMA's Production
postings and A/R become truth sources (fix 1); units paid from empty SVC05 (2); 26/TC/global linked across bills (3);
the primary payer's answers govern, other payers' answers are the secondary's (4); the payer's own rate observed on
full-rate lines only, by product, capped at the billed charge, provider level only as a recognised tier (5, owner rule
4.1); not-payable codes gated by the payer's record, CARC 246, G2211 beside an E/M -25 (6, owner rule 4.2); PR-group
COB/network/eligibility denials stay denials (7); duplicate denials defer to the payer's previous answer (8); later
deliveries collapsed only when paid (9); work = a resend to the denying payer, or EMA claim actions and A/R notes (10);
copies mapped by code, not position, and no-answer items claim their lines' units (11); verdict mechanics (12); and the
smaller rules of fix 13.

It imports nothing from remitwatch/ (independent of detect.py by design: a mistake in the detector must not be repeated
in the check). It prints counts, dollars, codes, payer families, the store's keys and finding ids only. Its output
files (truth table, verdicts) hold claim ids and dates: they are written only to the private folder given with --out.

Usage
  python3 -I claim_audit_a16.py --selftest
  python3 -I claim_audit_a16.py --db STORE.sqlite [--ledger LEDGER.sqlite] --out PRIVATE_DIR [--as-of YYYY-MM-DD]
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

RULES_VERSION = "A17"
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
                 "xover", "nlines_raw", "stmt", "pk", "mks", "copy_kind")


class Line:
    """One service line of one processing (or a virtual line for a processing reported at claim level only)."""
    __slots__ = ("lk", "p", "seq", "code", "adj", "mods", "comp", "dos", "charge", "paid", "paid_tb", "allowed", "asrc",
                 "ub", "up", "cas", "rarc", "cancelled_by", "cancels", "svc", "changed", "npi", "virtual", "ans",
                 "units_unknown", "tb_date", "omods_missing", "amods")

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
                 "in_scope", "scope_basis", "charge_pu", "pks", "eml", "ema_how", "notes")

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
        self.eml = []        # EMA Production claim lines matched to this service (EmaLine)
        self.ema_how = ""    # how they were matched: bill, person, unique
        self.notes = set()   # side facts for the truth table (written off in EMA, secondary balance...)


class EmaLine:
    """One EMA Production claim line: (claim link, date of service, code), its postings added up. Keys and money only."""
    __slots__ = ("link", "svc", "cpt", "bill", "person", "charges", "units", "pays", "adjs", "answers", "status",
                 "action", "pnames", "primary", "pat_paid", "pat_adj", "allowed", "ar", "svcs", "first_posted",
                 "last_posted")

    def __init__(self, link, svc, cpt):
        self.link, self.svc, self.cpt = link, svc, cpt
        self.bill = None
        self.person = None
        self.charges = 0
        self.units = 0
        self.pays = []       # (posted, payer name id, ins_paid cents, check_no or "", on an 835 on file?)
        self.adjs = []       # (posted, payer name id, group, carc, contractual cents, non-contractual cents)
        self.answers = []    # (posted, payer name id, check_no): payer rows with a check (an answer, $0 included)
        self.status = {}     # payer name id -> EMA claim status (as of the report)
        self.action = {}     # payer name id -> EMA claim action (as of the report)
        self.pnames = set()  # payer name ids posting on the line
        self.primary = None  # EMA's primary payer name id
        self.pat_paid = 0
        self.pat_adj = 0
        self.allowed = 0
        self.ar = []         # A/R rows: (claim_status, note_kind, note_date, follow_up, balance, denial, party)
        self.svcs = []       # the auditor's services it was matched to
        self.first_posted = ""
        self.last_posted = ""


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

# ---- A16 rule constants (public code lists and CARC/RARC meanings; nothing practice-specific)
# fix 6 / owner rule 4.2: a code the payer (almost) never pays is not payable whatever the denial says, except for
# authorization, eligibility, enrolment, timely-filing and missing-information reasons (those stay owed)
NOT_PAYABLE_EXCL = {"197", "198", "15", "62", "27", "26", "31", "32", "33", "177", "200", "B7", "185", "208", "29",
                    "16", "252", "226", "227", "MA130", "22", "109", "24", "242"}
REPORTING_ONLY_CARCS = {"246"}          # "non-payable code for required reporting only"
EM_CODES = {"99202", "99203", "99204", "99205", "99211", "99212", "99213", "99214", "99215"}
# fix 7: PR-group reasons that are COB, network or eligibility denials to work, not a balance moved to the patient
# owner rule 4.2: denials that say 'not paid separately' (bundled)
BUNDLED_CARCS = {"97", "234", "B10"}
PR_WORK_CARCS = {"22", "242", "27", "227", "31", "26", "32", "33", "177", "200"}
# fix 10: a resend to ANOTHER payer works a denial only after a COB, wrong-payer or eligibility denial
WRONG_PAYER_CARCS = {"22", "109", "24", "27", "26", "31", "32", "33", "177", "200", "242", "B11"}
# a "not covered by this plan" denial: a resend to another payer (not the bill's secondary) works it (the wrong plan)
OTHER_PLAN_CARCS = {"96", "204"}
# fix 2: evidence that a payer answered fewer units than billed
UNITS_CARCS = {"151", "119", "273", "35"}
UNITS_RARCS = {"N362", "M53", "N640"}
# fix 8: a duplicate-claim denial (CARC 18, B13; CARC 151 with RARC M86) defers to the payer's previous answer
DUP_RARCS = {"M86"}
# fix 13: a claim-level notice that the claim was split ("separate notice will follow") answers nothing by itself
SPLIT_NOTICE_RARCS = {"MA15"}
# fix 5: add-on codes are never reduced as a multiple procedure (CPT add-on codes used in this kind of practice)
ADDON_CODES = {"11001", "11008", "11045", "11046", "11047", "11103", "11105", "11107", "11201", "11732", "11922",
               "13102", "13122", "13133", "13153", "15003", "15005", "15101", "15111", "15116", "15121", "15131",
               "15136", "15151", "15152", "15156", "15157", "15201", "15221", "15241", "15261", "15272", "15274",
               "15276", "15278", "15777", "15787", "15847", "17003", "17312", "17314", "17315", "19001", "19082",
               "19084", "19086", "19126", "19282", "19284", "19286", "19288", "20930", "20931", "20936", "20937",
               "20938", "20939", "22552", "22614", "22632", "36474", "36476", "36479", "36483", "64462", "64480",
               "64484", "64491", "64492", "64494", "64495", "64634", "64636", "88341", "88350", "88364", "88369",
               "88373", "88377", "96368", "96375", "96376", "96411", "96415", "96417", "96423", "99292", "99354",
               "99355", "99356", "99357", "99417", "99418", "G2211"}
# fix 5(e): unlisted / not-otherwise-classified codes have no "rate" (their price depends on what was given or done)
UNLISTED_RE = re.compile(r"^(\d{3}99|J3490|J3590|J7599|J7699|J7799|J7999|J8499|J8999|J9999|A9999|C9399|E1399|"
                         r"L8699|T5999|S9999|Q4100|J1599|J2999|A4649)$")
# fix 5(b): product classes of CLP06; a rate is never borrowed across them
PRODUCT_CLASS = {"MA": "MCR", "MB": "MCR", "16": "MAP", "MC": "MCD", "CH": "GOV", "VA": "GOV", "OF": "GOV",
                 "WC": "GOV"}
# fix 10: EMA's claim actions that are work on a denial (an appeal, a corrected claim, records sent, a reprocessing)
EMA_WORK_ACTIONS = {"Initial Appeal Electronic", "Submitted Initial Appeal Portal", "Corrected Claim Submission Electronic",
                    "Submitted Corrected Claim Portal", "Resubmitted Initial Appeal Portal", "Sent Medical Records Fax",
                    "Medical Records Sent Paper", "Appeal In Process", "Second Level Appeal Paper",
                    "Medical Records Sent Electronic", "Adjustment Request", "Submitted Second Appeal Portal",
                    "Second Level Appeal Electronic", "Corrected Claim Submission Paper", "Claim Resubmitted Paper",
                    "Claim Resubmitted Electronic", "Claim Reprocessing"}
# EMA A/R billing-note kinds that record an action on the payer's answer that no new 837 shows (an appeal, a reprocessing
# request, records sent). A call, a follow-up reminder or a "resubmitted" note is not counted: the hand calibration
# judged such items still to be worked (a resubmission is seen through its 837 or its answer)
EMA_WORK_NOTES = {"appeal", "reprocess", "records"}
# EMA claim statuses that are still open with the payer
EMA_OPEN_STATUSES = {"Payment Pending", "Denied", "Insurance Reprocessing", "Medical Records Sent", "Rejected",
                     "Ready for Posting", "Batched for Printing", "Batched for Sending", "Invalid"}
PLACEHOLDER_CENTS = 1        # fix 13: a line charged $0.01 a unit is a placeholder, not money


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


def load_ema_books(R, con, ledger=None):
    """A16 fix 1: EMA's books as the EMA readers stored them. From the store: the claim-link map (ema_link_map, ema_claim_links),
    the check match (ema_checks: a check that is an 835 on file) and the A/R report (ema_ar). From the posting ledger
    (a separate sqlite, or the same store when its tables are there): every Production posting and the names table."""
    R.ema_names = {}
    R.ema_lines = {}
    R.ema_rows_read = 0
    R.ema_link_bill = {}
    R.ema_checks = {}
    R.ema_ar = []
    tabs = R.tables
    if "ema_link_map" in tabs:
        for link, bill, conflict, fac in select(con, "ema_link_map", ["link", "bill", "conflict", "facility"]):
            if link is not None and bill and not conflict and not fac:
                R.ema_link_bill[link] = split_claim_id(bill)[0]
    if "ema_claim_links" in tabs:
        for cid, link, bill in select(con, "ema_claim_links", ["claim_id", "link", "bill"]):
            if link is not None and link not in R.ema_link_bill and (bill or cid):
                R.ema_link_bill[link] = split_claim_id(bill or cid)[0]
    if "ema_checks" in tabs:
        for chk, payer, match, tk in select(con, "ema_checks", ["check_no", "payer", "match", "trace_key"]):
            if chk:
                R.ema_checks[(str(chk).strip(), (payer or "").strip())] = ((match or "").strip(), tk)
    if "ema_ar" in tabs:
        R.ema_ar = select(con, "ema_ar", ["link", "svc", "cpt", "claim_status", "note_kind", "note_date", "follow_up",
                                          "balance_cents", "denial_cents", "party", "claim_action", "coverage"])
    lc = ledger if ledger is not None else (con if "postings" in tabs else None)
    if lc is None:
        return
    ltabs = table_names(lc)
    if "postings" not in ltabs or "names" not in ltabs:
        return
    R.ema_names = {i: (n or "").strip() for i, n in lc.execute("select id, name from names")}
    names = R.ema_names
    lines = R.ema_lines
    pat = defaultdict(lambda: [0, 0])
    n_rows = 0
    for (posted, svc, cpt, units, link, chk, grp, code, status, action, payer, primary, person, charges, ins_paid,
         pat_paid, contr, nonc, pat_adj, allowed) in lc.execute(
            "select posted, svc, cpt, units, link, check_no, grp, code, status, action, payer, primary_payer, person, "
            "charges, ins_paid, pat_paid, contractual, noncontractual, pat_adj, allowed from postings"):
        n_rows += 1
        svc = iso(svc) or ""
        cpt = (cpt or "").strip().upper()
        posted = iso(posted) or ""
        if link is None:
            if person is not None and (pat_paid or pat_adj):
                x = pat[(person, svc, cpt)]
                x[0] += pat_paid or 0
                x[1] += pat_adj or 0
            continue
        key = (link, svc, cpt)
        E = lines.get(key)
        if E is None:
            E = lines[key] = EmaLine(link, svc, cpt)
            E.first_posted = posted
        if person is not None:
            E.person = person
        if posted and posted < E.first_posted:
            E.first_posted = posted
        if posted > E.last_posted:
            E.last_posted = posted
        if charges:
            E.charges += charges
            u = units_of(units) or 1
            E.units += u if charges > 0 else -u
        chk = (str(chk).strip() if chk is not None else "")
        if ins_paid:
            m = R.ema_checks.get((chk, names.get(payer, ""))) if chk else None
            E.pays.append((posted, payer, ins_paid, chk, bool(m and m[0] in ("exact", "loose"))))
        if contr or nonc:
            E.adjs.append((posted, payer, (grp or "").strip().upper(), (code or "").strip().upper(), contr or 0,
                           nonc or 0))
        if chk:
            E.answers.append((posted, payer, chk))
        if payer is not None:
            E.pnames.add(payer)
            if status is not None:
                E.status[payer] = names.get(status, "")
            if action is not None:
                E.action[payer] = names.get(action, "")
        if primary is not None:
            E.primary = primary
        E.pat_paid += pat_paid or 0
        E.pat_adj += pat_adj or 0
        if allowed and allowed > E.allowed:
            E.allowed = allowed
    # patient money (posted with no claim link) joins a claim line when exactly one line has that person, day and code
    n_lines = Counter((E.person, E.svc, E.cpt) for E in lines.values() if E.person is not None)
    for E in lines.values():
        if E.person is not None and n_lines[(E.person, E.svc, E.cpt)] == 1:
            x = pat.get((E.person, E.svc, E.cpt))
            if x:
                E.pat_paid += x[0]
                E.pat_adj += x[1]
    for (link, svc, cpt, cst, nk, nd, fu, bal, den, party, act, cov) in R.ema_ar:
        if link is None:
            continue
        E = lines.get((link, iso(svc) or "", (cpt or "").strip().upper()))
        if E is not None:
            E.ar.append(((cst or "").strip(), (nk or "").strip(), iso(nd), iso(fu), bal or 0, den or 0,
                         (party or "").strip(), (act or "").strip(), (cov or "").strip()))
    for E in lines.values():
        E.bill = R.ema_link_bill.get(E.link)
    R.ema_rows_read = n_rows


def load(con, ledger=None):
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
            "charges_cents", "units", "policy_number", "activity_type", "insurance_payments_cents", "payments_cents",
            "payment_method", "carc"])
        # A16: payments_cents and payment_method too (half of the Payment rows carry no insurance_payments value), and
        # the claim-status rows' CARCs (the payer's answer as EMA recorded it)
        R.ema = [r[:16] + (src.get(r[1]) or "", hkey(r[16]), (r[17] or "").strip(), r[18], r[19], (r[20] or "").strip(),
                          (r[21] or "").strip()) for r in rows]
    else:
        R.ema = []
    load_ema_books(R, con, ledger)
    R.findings = select(con, "findings", [
        "finding_id", "signal", "category", "actionable", "status", "line_key", "claim_key", "transaction_key",
        "patient_control_number", "payer_name", "procedure_code", "modifiers", "service_date", "payment_date",
        "charge_cents", "paid_cents", "expected_cents", "shortfall_cents", "carc", "evidence", "appeal_deadline",
        "first_seen", "recovered_cents", "written_off_cents", "recovered_date", "status_changed_at", "last_seen"])
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
        self.review_units = []   # owed units listed once per payer x provider (owner rule 4.1), not unit by unit
        self._pfam = {}          # bill -> primary payer family (A16 fix 4)
        self._ef = {}            # service key -> EMA facts (A16 fix 1)
        self.ema_pfam = {}
        self.ema_on = False
        self.review = defaultdict(lambda: [0, 0, 0])

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
            p.copy_kind = ""
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
            L.omods_missing = False
            L.amods = parse_mods(mods) + xmods        # the modifiers the payer adjudicated (SVC01)
            if ocode and oqual in ("HC", "HP", ""):
                L.code = ocode
                # A16 fix 13: SVC06 is the billed procedure with the billed modifiers; an SVC06 with no modifiers means
                # the billed code had none (A15 fell back to the adjudicated modifiers, e.g. a payer-added 26)
                L.mods = parse_mods(omods) + xomods
                if not omods and not xomods and parse_mods(mods):
                    L.omods_missing = True
                    self.stats["svc06_without_modifiers_billed_none"] += 1
                L.changed = ocode != code
            else:
                L.code = code
                L.mods = parse_mods(mods) + xmods
                L.changed = False
            L.comp = component(L.mods)
            L.dos = iso(sdate) or iso(pstart) or p.stmt or ""
            L.charge, L.paid = charge or 0, paid or 0
            L.paid_tb = L.paid
            L.tb_date = ""
            L.cas = lcas.get(lk, [])
            L.rarc = lrarc.get(lk, [])
            ub = units_of(ounits) or units_of(punits) or 1
            L.ub = ub
            L.up = (units_of(punits) or ub) if L.paid != 0 else 0
            # A16 fix 2: neither SVC05 nor SVC07 says how many units: the billed units decide later (units_from_bills)
            L.units_unknown = not units_of(punits) and not units_of(ounits)
            if allowed is not None:
                L.allowed, L.asrc = allowed, "B6"
                # A16 fix 5(d): AMT*B6 equal to the paid amount while a patient share sits on top and the CAS
                # reconciles the charge: the allowed is charge - CO/OA/PI (= paid + PR), not B6
                pr = sum(a for g, r, a in L.cas if g == "PR")
                co = sum(a for g, r, a in L.cas if g != "PR")
                if pr > 0 and abs(allowed - L.paid) <= 1 and abs(L.charge - co - (L.paid + pr)) <= 1:
                    L.allowed, L.asrc = L.paid + pr, "B6_equals_paid"
                    self.stats["b6_equal_to_paid_with_patient_share"] += 1
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
        charge and paid, and either the same check (trace and date) or, for a paid processing only (A16 fix 9), more
        such processings than this payer's reversals of it plus one (a payment stands once unless it was reversed and
        paid again)."""
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
            if key[5] == 0:
                # A16 fix 9: a $0 answer delivered again on another check and day is a new answer (a corrected claim
                # denied again, a reprocessing): only same-check copies of $0 processings collapse
                self.stats["zero_paid_later_deliveries_kept"] += max(0, len(kept) - 1)
                continue
            allowed_n = rev_count[(key[0], split_claim_id(key[1])[0], key[4], key[5])] + 1
            if len(kept) > allowed_n:
                # keep the oldest deliveries not marked superseded; the rest are copies of the first one kept
                order = sorted(kept, key=lambda p: (p.sup, p.ordk))
                for x in order[allowed_n:]:
                    x.copy_of = order[0]
                    x.copy_kind = "later_delivery"
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
            copies = [p for p in self.procs.values() if p.copy_of is not None and p.copy_kind == "later_delivery"
                      and p.fam == fam and p.bill == bill and p.paid > 0]
            # A16 (calibration round 2): each day's take-back recovers a payment made on or before that day: a duplicate
            # payment first (the same processing paid again on another check), then the payment it matches exactly (a
            # line's, a processing's, or one line per entry), else the latest payments made by then
            days = defaultdict(list)
            for e in ents[(fam, bill)]:
                days[e[2] or ""].append(e[1])
            day_net = sorted((d, sum(v), v) for d, v in days.items())
            takes = []
            for d, tot, amts in day_net:
                if tot > 0:
                    takes.append([d, tot, [a for a in amts if a > 0]])
                elif tot < 0:
                    back = -tot
                    for tk_ in reversed(takes):
                        r_ = min(back, tk_[1])
                        tk_[1] -= r_
                        back -= r_
                        if back <= 0:
                            break
            left_all = 0
            for d, amt, parts in takes:
                if amt <= 0:
                    continue
                left = amt
                # a duplicate payment (a paid processing delivered again on another check) recovered
                for cp in copies:
                    if cp.paid > 0 and (not d or cp.date <= d) and abs(cp.paid - left) <= 1:
                        cp.paid = 0
                        left = 0
                        self.stats["plb_recovers_duplicate_payment"] += 1
                        break
                if left <= 0:
                    continue
                dated = [L for L in cands if L.paid_tb > 0 and (not d or L.p.date <= d)] or \
                    [L for L in cands if L.paid_tb > 0]
                pick = None
                one = [L for L in dated if abs(L.paid_tb - left) <= 1]
                if one:
                    pick = [sorted(one, key=lambda L: L.p.ordk)[-1]]
                else:
                    byp = defaultdict(list)
                    for L in dated:
                        byp[L.p.ck].append(L)
                    whole = [v for v in byp.values() if abs(sum(L.paid_tb for L in v) - left) <= len(v)]
                    if whole:
                        pick = sorted(whole, key=lambda v: v[0].p.ordk)[-1]
                    elif len(parts) > 1:
                        used, ok = [], True
                        for a in parts:
                            m = [L for L in dated if L not in used and abs(L.paid_tb - a) <= 1]
                            if not m:
                                ok = False
                                break
                            used.append(sorted(m, key=lambda L: L.p.ordk)[-1])
                        pick = used if ok else None
                if pick is not None:
                    self.stats["plb_take_matched_by_amount"] += 1
                    for L in pick:
                        take = min(left, L.paid_tb)
                        L.paid_tb -= take
                        L.tb_date = max(L.tb_date or "", d)
                        left -= take
                if left > 0:
                    dated.sort(key=lambda L: (L.p.pcn in claim_ids, L.p.ordk, L.paid_tb), reverse=True)
                    for L in dated:
                        if left <= 0:
                            break
                        take = min(left, L.paid_tb)
                        L.paid_tb -= take
                        L.tb_date = max(L.tb_date or "", d)
                        left -= take
                left_all += max(0, left)
            self.stats["plb_bills_applied"] += 1
            self.money["plb_applied"] += t - left_all
            if left_all > 0:
                self.money["plb_not_matched_to_payments"] += left_all

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
        self.l837_raw = l837
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
        status_carc = {}
        status_rows = defaultdict(Counter)
        charge_units = defaultdict(Counter)   # (pcn, key) -> {export: units charged in that export}
        cpu_max = {}
        self.ema_paid = defaultdict(int)      # (bill, code, date) -> insurance payments EMA posted (any export)
        self.ema_paid_era = defaultdict(int)  # the same from EMA's weekly ERA export
        ema_pay_seen = set()
        for r in R.ema:
            (family, sname, ridx, sdate, bcreated, claim_id, item_id, cpt, mods, payer, cov, cstatus, subm, denial,
             charges, units, created, polk, activity, ins_paid, payments, pmethod, wcarc) = r
            pcn = (claim_id or "").strip().upper()
            cpt = (cpt or "").strip().upper()
            if not pcn or not cpt:
                continue
            if (cov or "").strip().lower() == "self-pay":
                continue
            # A16 fix 1: an insurance payment row carries its amount in insurance_payments or, as often, only in
            # payments (coverage Primary/Secondary/Tertiary, or a payer check/EFT); the ERA export's lines too
            cov_ins = (cov or "").strip().lower() in ("primary", "secondary", "tertiary", "primary unlinked",
                                                      "secondary unlinked")
            if not ins_paid and payments and (cov_ins or pmethod.startswith("Payer")) and \
                    family in ("charge_lines", "era_lines"):
                ins_paid = payments
            if family == "era_lines":
                if (ins_paid or 0) > 0:
                    dos_ = iso(sdate) or ""
                    pk_ = ("era", pcn, cpt, dos_, ins_paid, ridx)
                    if pk_ not in ema_pay_seen:
                        ema_pay_seen.add(pk_)
                        self.ema_paid_era[(split_claim_id(pcn)[0], cpt, dos_)] += ins_paid
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
                    status_carc[sk] = wcarc
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
            s.emast.append(rec + (max(status_rows[(pcn, key)].values()), status_carc.get((pcn, key), "")))
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
            if p.paid == 0 and set(p.crarc) & SPLIT_NOTICE_RARCS:
                # A16 fix 13: "claim separated, a separate notice follows" (MA15) answers no service by itself
                self.stats["split_claim_notices_not_placed"] += 1
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

    @staticmethod
    def pclass(prod):
        return PRODUCT_CLASS.get(prod, "COM")

    @staticmethod
    def is_unlisted(code):
        return bool(UNLISTED_RE.match(code or ""))

    def full_rate_lines(self, p):
        """A16 fix 5(a): the lines of a processing that carry a surgical code's full rate: the highest-valued
        non-add-on surgical line (charge per unit, then allowed per unit), every add-on line, every non-surgical line.
        The other surgical lines may be multiple-procedure reductions reported with no modifier 51 and no CARC 59."""
        surg = [L for L in p.lines if self.is_surgical(L.code) and L.code not in ADDON_CODES]
        if len(surg) <= 1:
            return None
        top = max(surg, key=lambda L: (L.charge / max(1, L.ub), L.allowed / max(1, L.up or L.ub), -L.seq))
        return {L.lk for L in p.lines if L not in surg or L is top}

    def build_rates(self):
        """The payer's own rate (A16, fix 5 and the owner's rule 4.1): the most common allowed amount per unit on the
        payer's clean, standing, primary, paid answers for the same product (CLP06), code, component, year and setting.
        Observed on full-rate lines only (a lower-valued surgical line beside another may be a multiple-procedure
        reduction); no rate for unlisted codes. The mode drops amounts at half of another amount holding 20% of the
        answers and breaks ties to the larger amount. The rendering provider's own mode is used only as a recognised
        tier: 10 answers or more at 60% or more, at 1.00 or 0.85 (NP/PA) of the payer's mode; any other provider-level
        discount is not absorbed (the line is compared with the payer's mode and the pair is listed for review)."""
        obs = defaultdict(Counter)
        frl_cache = {}
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
            if self.is_unlisted(s.code):
                continue
            if self.is_surgical(s.code) and s.code not in ADDON_CODES:
                frl = frl_cache.get(p.ck, 0)
                if frl == 0:
                    frl = frl_cache[p.ck] = self.full_rate_lines(p)
                if frl is not None and not any(L.lk in frl for L in A.lines):
                    self.stats["rate_obs_skipped_not_full_rate_line"] += 1
                    continue
            apu = int(round(A.allowed / A.up))
            yr = s.dos[:4]
            pc = self.pos_class(p)
            obs[(p.fam, p.prod, s.code, s.comp, yr, pc, A.npi or "")][apu] += 1
            obs[(p.fam, p.prod, s.code, s.comp, yr, pc)][apu] += 1
            obs[(p.fam, p.prod, s.code, s.comp, yr)][apu] += 1
            obs[(p.fam, "@" + self.pclass(p.prod), s.code, s.comp, yr)][apu] += 1
            obs[(p.fam, s.code, s.comp, yr)][apu] += 1          # family-wide: estimates for unanswered units only
        self.rate_obs = obs
        # the payer's mode per (payer, product, code, component, year, setting)
        self.l6_mode = {}
        for key, c in obs.items():
            if len(key) == 6:
                amt, k, n = self.mode_of(c)
                if amt and n >= 5 and k < 0.4 * n:
                    # a payer paying two tiers (physicians at the full rate, NPs/PAs at 85% of it) splits its answers:
                    # the full rate and its 85% partner together make the mode
                    amt2, k2, n2 = self.mode_of(c, tiered=True)
                    if amt2 and k2 >= 0.4 * n2:
                        amt, k, n = amt2, k2, n2
                if amt and n >= 5 and k >= 0.4 * n:       # A16: 5 answers at least (A15: 3) for a firm rate
                    self.l6_mode[key] = (amt, k, n)
        # each provider's own mode at each payer mode
        prov_modes = defaultdict(dict)
        for key, c in obs.items():
            if len(key) == 7 and key[6]:
                amt, k, n = self.mode_of(c)
                if amt and n >= 3 and k >= 0.5 * n:
                    prov_modes[key[:6]][key[6]] = (amt, k, n)
        # the payer's full rate: its mode, or a provider's mode at 1/0.85 of it (the payer's mode is then the NP/PA
        # rate, because NPs and PAs bill most of that code at that payer)
        self.full_rate = {}
        for key6, pm in self.l6_mode.items():
            full = pm[0]
            for npi, (amt, k, n) in prov_modes.get(key6, {}).items():
                if amt > full and abs(0.85 * amt - pm[0]) <= 0.015 * pm[0]:
                    full = amt
            self.full_rate[key6] = full
        # a provider's type: NP/PA when, at the same payer, product, code and setting, its own mode sits at 85% of another
        # provider's mode at more places than another provider's mode sits at 85% of its own (pairwise: a payer's mode
        # dominated by NPs would hide it)
        ptype = defaultdict(Counter)
        for key6, provs in prov_modes.items():
            items = list(provs.items())
            for x, (ax, kx, nx) in items:
                for y, (ay, ky, ny) in items:
                    if x == y:
                        continue
                    if abs(ax - 0.85 * ay) <= 0.015 * ay:
                        ptype[x]["85"] += 1
                    elif abs(ay - 0.85 * ax) <= 0.015 * ax:
                        ptype[x]["100"] += 1
        self.np_type = {npi: c["85"] >= 2 and c["85"] > c["100"] for npi, c in ptype.items()}
        # provider tiers at a payer (owner rule 4.1): a provider-level rate only on 10+ answers at 60%+
        self.prov_tier = {}
        for key6, provs in prov_modes.items():
            full = self.full_rate.get(key6)
            if not full:
                continue
            for npi, (amt, k, n) in provs.items():
                if n < 10 or k < 0.6 * n:
                    continue
                ratio = amt / full
                if abs(ratio - 1.0) <= 0.015:
                    tier = "same"
                elif abs(ratio - 0.85) <= 0.015:
                    tier = "np85"
                elif abs(ratio - 0.5) <= 0.03:
                    tier = "half"            # an MPPR half is never a provider rate
                elif ratio < 1.0:
                    tier = "discount"
                else:
                    tier = "above"
                self.prov_tier[key6 + (npi,)] = (tier, amt, ratio, n)
        # owner rule 4.1: the providers a payer pays at an unexplained discount (their own tier) are no evidence of a
        # second rate the payer pays to the other providers (one provider's discount is not the payer's rate)
        self.disc_provs = defaultdict(set)
        for k7, (tier, amt, ratio, n) in self.prov_tier.items():
            if tier in ("discount", "half"):
                self.disc_provs[k7[:6]].add(k7[6])
        self.stats["provider_tiers"] = len(self.prov_tier)
        self.stats["providers_np_type"] = sum(1 for v in self.np_type.values() if v)
        self.stats["payer_modes_np_rate_full_raised"] = sum(1 for k, v in self.full_rate.items() if v != self.l6_mode[k][0])
        self.review = defaultdict(lambda: [0, 0, 0])     # (fam, npi) -> [lines, units, gap cents] (owner rule 4.1)
        # codes a payer does not pay: never on 10+ answers, or (per product) on under 5% of 20+ answers
        seen = Counter()
        paid = Counter()
        seen_p = Counter()
        paid_p = Counter()
        seen_c = Counter()
        paid_c = Counter()
        for A in self.answers:
            if A.virtual or not A.standing or A.p.st not in PRIMARY:
                continue
            # A16: only the bill's primary payer's answers make a payer's record (a Medicaid plan answering mostly as a
            # secondary denies codes it would pay as a primary)
            P = self.primary_family(A.p.bill)
            if P is not None and A.p.fam != P:
                continue
            k = (A.p.fam, A.svc.code, A.svc.comp)
            kp = (A.p.fam, A.p.prod, A.svc.code, A.svc.comp)
            kc = (A.p.fam, "@" + self.pclass(A.p.prod), A.svc.code, A.svc.comp)
            seen[k] += 1
            seen_p[kp] += 1
            seen_c[kc] += 1
            # calibration round 2: a $0.01-a-unit 'payment' (a placeholder the payer allows) is no payment
            if A.paid > PLACEHOLDER_CENTS * max(1, A.ub):
                paid[k] += 1
                paid_p[kp] += 1
                paid_c[kc] += 1
        self.never_paid = {k for k, n in seen.items() if n >= 10 and paid[k] == 0}
        self.rarely_paid_fam = {k for k, n in seen.items() if n >= 20 and paid[k] < 0.05 * n}
        self.rarely_paid = {k for k, n in seen_p.items() if n >= 20 and paid_p[k] < 0.05 * n}
        # per product class (MA / commercial / Medicaid): owner rule 4.2 keeps the split by product (a family's
        # commercial plans may never pay a code its Medicare Advantage plans pay)
        self.rarely_paid_cls = {k for k, n in seen_c.items() if n >= 20 and paid_c[k] < 0.05 * n}
        # owner rule 4.2 ('when the denial says bundled'): for a bundling denial, 'almost never' at class level is under
        # 1 answer in 10 (UHC commercial pays G2211 on 5% of its answers, its HMO product on 7%); a non-covered denial of
        # a code paid now and then (coverage criteria) keeps the 5% line
        self.rarely_paid_cls_bundled = {k for k, n in seen_c.items() if n >= 20 and paid_c[k] < 0.10 * n}

    @staticmethod
    def mode_of(c, tiered=False):
        """The mode of an allowed-per-unit counter: amounts at half (+-5%) of another amount holding 20% or more of the
        answers are dropped (multiple-procedure reductions), and a tie goes to the larger amount. (amount, count, n)."""
        if not c:
            return None, 0, 0
        # amounts a cent or two apart are one amount (sequestration and rounding): merged into the most frequent
        if len(c) > 1:
            merged = {}
            for a in sorted(c, key=lambda a: (-c[a], -a)):
                for b in merged:
                    if abs(a - b) <= 2:
                        merged[b] += c[a]
                        break
                else:
                    merged[a] = c[a]
            c = merged
        n = sum(c.values())
        keep = {a: k for a, k in c.items()
                if not any(b != a and k2 >= 0.2 * n and k <= k2 and abs(a - 0.5 * b) <= 0.05 * b for b, k2 in c.items())}
        if not keep:
            keep = dict(c)
        n2 = sum(keep.values())
        if tiered:
            def with85(a):
                return keep[a] + sum(k2 for b, k2 in keep.items() if b != a and abs(b - 0.85 * a) <= 0.015 * a)
            amt = max(keep, key=lambda a: (with85(a), a))
            return amt, with85(amt), n2
        amt, k = max(keep.items(), key=lambda kv: (kv[1], kv[0]))
        return amt, k, n2

    def rate_for(self, fam, prod, code, comp, year, pc, npi):
        """The firm rate for judging a payment: (expected per unit for this provider, the payer's mode, the payer-level
        observations, tier). Same payer, product, setting and year only; None when there is none (or unlisted code)."""
        if self.is_unlisted(code):
            return None
        key6 = (fam, prod, code, comp, year, pc)
        pm = self.l6_mode.get(key6)
        if pm is None:
            return None
        full = self.full_rate.get(key6, pm[0])
        exp, tier = full, "payer"
        pt = self.prov_tier.get(key6 + (npi or "",)) if npi else None
        if pt is not None:
            if pt[0] == "np85":
                exp, tier = pt[1], "np85"
            elif pt[0] == "discount":
                tier = "provider_discount"
            elif pt[0] == "above":
                exp, tier = pt[1], "provider_above"
        elif npi and self.np_type.get(npi) and self.np_ok_here(fam, prod, code, comp, year, pc, npi, "payer"):
            exp, tier = int(round(0.85 * full)), "np85_type"
        weak = pm[1] < 0.5 * pm[2] and full == pm[0]
        return exp, full, self.rate_obs[key6], tier, weak

    def np_ok_here(self, fam, prod, code, comp, year, pc, npi, tier):
        """Owner rule 4.1: an 85% payment is accepted only for a provider whose own payments sit at 85%: its own mode at
        this payer and code (5 answers or more) at 85% of the full rate; with no such evidence, its type (NP/PA) or an
        unknown rendering provider. A provider this payer pays at 100% is not excused at 85%."""
        if tier in ("np85",):
            return True
        if not npi:
            return True
        key6 = (fam, prod, code, comp, year, pc)
        full = self.full_rate.get(key6)
        c = self.rate_obs.get(key6 + (npi,))
        if full and c:
            amt, k, n = self.mode_of(c)
            if amt and n >= 5 and k >= 0.5 * n:
                r_ = amt / full
                if abs(r_ - 0.85) <= 0.015:
                    return True
                if abs(r_ - 1.0) <= 0.015:
                    return False
        return bool(self.np_type.get(npi))

    def rate_est(self, fam, prod, code, comp, year, pc=None, npi=None):
        """A price for an unpaid unit (an estimate unless it is the firm rate): firm rate, else the product's mode
        (any setting), else the product class's mode; never across product classes. (amount, basis)."""
        if self.is_unlisted(code):
            return None, ""
        if prod is not None and pc is not None:
            r = self.rate_for(fam, prod, code, comp, year, pc, npi)
            if r is not None:
                return r[0], "rate_l6" if r[3] in ("payer", "provider_discount") else "rate_" + r[3]
        tries = []
        if prod is not None:
            tries.append(((fam, prod, code, comp, year), "est_product"))
            tries.append(((fam, "@" + self.pclass(prod), code, comp, year), "est_class"))
        else:
            tries.append(((fam, code, comp, year), "est_payer"))
        for key, basis in tries:
            c = self.rate_obs.get(key)
            if not c:
                continue
            amt, k, n = self.mode_of(c)
            if amt and n >= 3 and k >= 0.4 * n:
                return amt, basis
        return None, ""

    # A15 compatibility (rate_probe and other diagnostics): the payer's mode at the most specific level available
    def rate(self, fam, prod, code, comp, year, pc=None, npi=None):
        r = self.rate_for(fam, prod, code, comp, year, pc, npi) if prod is not None and pc is not None else None
        if r is not None:
            return r[0], r[2], 6
        amt, basis = self.rate_est(fam, prod, code, comp, year, pc, npi)
        return (amt, None, 0) if amt else (None, None, 0)

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
                # calibration round 2: a $0 line beside a charged line of the same code is no billed unit
                u_ = A.ub
                if not A.split and A.charge > 0 and any(L.charge == 0 for L in A.lines):
                    u_ = sum(L.ub for L in A.lines if L.charge != 0) or A.ub
                proc_units[(A.p.pcn, A.p.ck)] += u_
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

    def units_from_bills(self):
        """A16 fix 2: a payer line that reports neither SVC05 nor SVC07 answered the billed units when its CAS reconciles
        the whole charge it answered, that charge is the billed line's, and no units reason (CARC 151, 119, 273, 35;
        RARC N362, M53, N640) says otherwise: its units are the units the claim id billed (837, else EMA's charge rows).
        A15 read such a line as one paid unit and called the other units 'fewer units answered'."""
        n = 0
        for s in self.svcs.values():
            if s.U <= 1:
                continue
            rec = defaultdict(lambda: [0, 0])
            for x in s.sends:
                if x[6] == "837":
                    r_ = rec[(x[3], x[7])]
                    r_[0] += x[4]
                    r_[1] += x[5]
            billed = {}
            for (pcn, k), (u, ch) in rec.items():
                if u > billed.get(pcn, (0, 0))[0]:
                    billed[pcn] = (u, ch)
            for x in s.ema:
                if x[0] not in billed and x[1] > 0:
                    billed[x[0]] = (x[1], x[2])
            for A in s.answers:
                if A.virtual or not A.lines or not all(L.units_unknown for L in A.lines):
                    continue
                b = billed.get(A.p.pcn)
                if not b or b[0] <= A.ub:
                    continue
                adj = sum(a for g, r, a in A.cas)
                if abs(A.charge - (A.paid + adj)) > 1:
                    continue
                if b[1] and A.charge < b[1] - max(100, 0.01 * b[1]):
                    continue
                if any(r in UNITS_CARCS for g, r, a in A.cas) or any(r in UNITS_RARCS for r in A.rarc):
                    continue
                A.ub = min(s.U, b[0])
                A.up = A.ub if A.paid > 0 else 0
                n += 1
        self.stats["answers_units_from_bill"] = n

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
        r_amt, basis = self.rate_est(fam, prod, s.code, s.comp, (s.dos or "0000")[:4], pc, npi)
        cpu = s.charge_pu
        if r_amt:
            return (min(r_amt, cpu) if cpu else r_amt), basis
        return cpu, "charge"

    # ------------------------------------------------------------------ EMA's books (A16 fix 1)
    def ema_match(self):
        """EMA's Production claim lines matched to the services: by the bill the claim link maps to (EMA's own map);
        else through the patient (the service's patient keys, from its 835 names, its 837 subscriber and EMA policy
        numbers through the 835 member ids, to EMA's person on the patient's other bills); else the only EMA line no
        service holds with that day, code and charge. The payer of each EMA posting is named by the 835 its check is
        (EMA's check match), else by EMA's payer name as the weekly exports and the 835 feed name it."""
        R = self.R
        lines = getattr(R, "ema_lines", {}) or {}
        self.ema_pfam = {}
        self.ema_on = bool(lines)
        if not lines:
            return
        names = R.ema_names
        vote = defaultdict(Counter)
        for E in lines.values():
            for posted, payer, chk in E.answers:
                m = R.ema_checks.get((chk, names.get(payer, "")))
                if m and m[0] in ("exact", "loose") and m[1] in R.tx:
                    t = R.tx[m[1]]
                    vote[payer][self.fams.of(t[1], t[2])] += 1
        for pid_, c in vote.items():
            f, n = c.most_common(1)[0]
            if n >= 2 and n >= 0.6 * sum(c.values()):
                self.ema_pfam[pid_] = f
        for pid_, nm in names.items():
            if pid_ in self.ema_pfam:
                continue
            k = norm_name(nm)
            f = self.ema_fam.get(k) or self.fams.name_to_fam.get(k)
            if f:
                self.ema_pfam[pid_] = f
        by_bill = defaultdict(list)
        by_person = defaultdict(list)
        for E in lines.values():
            if E.bill:
                by_bill[(E.bill, E.svc, E.cpt)].append(E)
            if E.person is not None:
                by_person[(E.person, E.svc, E.cpt)].append(E)
        n = Counter()
        pending = []
        for s in self.svcs.values():
            if not s.dos:
                continue
            Es = by_bill.get((s.bill, s.dos, s.code))
            if Es:
                s.eml, s.ema_how = list(Es), "bill"
                for E in Es:
                    E.svcs.append(s)
                n["bill"] += 1
            else:
                pending.append(s)
        pk_person = defaultdict(set)
        for s in self.svcs.values():
            if s.ema_how == "bill":
                persons = {E.person for E in s.eml if E.person is not None}
                for pk in s.pks:
                    pk_person[pk] |= persons
        left = []
        for s in pending:
            persons = set()
            for pk in s.pks:
                persons |= pk_person.get(pk, set())
            cands = []
            for per in persons:
                for E in by_person.get((per, s.dos, s.code), []):
                    if E.bill and E.bill != s.bill:
                        continue
                    if any(o.ema_how == "bill" for o in E.svcs):
                        continue
                    cands.append(E)
            if cands:
                s.eml, s.ema_how = cands, "person"
                for E in cands:
                    E.svcs.append(s)
                n["person"] += 1
            else:
                left.append(s)
        by_dcc = defaultdict(list)
        for E in lines.values():
            if not E.svcs and not E.bill:
                by_dcc[(E.svc, E.cpt, E.charges)].append(E)
        for s in left:
            if not s.charge_pu:
                continue
            c = by_dcc.get((s.dos, s.code, s.charge_pu * max(1, s.U)), []) or by_dcc.get((s.dos, s.code, s.charge_pu), [])
            c = [E for E in c if not E.svcs]
            if len(c) == 1:
                s.eml, s.ema_how = c, "unique"
                c[0].svcs.append(s)
                n["unique"] += 1
        # a match by day, code and charge alone stands only when EMA's claim carries exactly the bill's billed lines that
        # day (codes and charges: from the 837, else EMA's weekly charge rows, else the 835)
        def billed_set(bill, dos):
            out = Counter()
            for key in self.bill_svcs.get(bill, ()):
                sv = self.svcs.get(key)
                if sv is None or sv.dos != dos:
                    continue
                ch = max([x[5] for x in sv.sends if x[6] == "837"] + [0]) or max([x[2] for x in sv.ema] + [0]) or \
                    max([A.charge for A in sv.answers] + [0])
                if ch > 0:
                    out[(sv.code, ch)] += 1
            return out

        def link_set(link, dos):
            out = Counter()
            for E in lines_by_link.get(link, ()):
                if E.svc == dos and E.charges > 0:
                    out[(E.cpt, E.charges)] += 1
            return out
        lines_by_link = defaultdict(list)
        for E in lines.values():
            lines_by_link[E.link].append(E)
        for s in self.svcs.values():
            if s.ema_how != "unique":
                continue
            bs = billed_set(s.bill, s.dos)
            ls = link_set(s.eml[0].link, s.dos)
            if bs != ls:
                for E in s.eml:
                    if s in E.svcs:
                        E.svcs.remove(s)
                s.eml, s.ema_how = [], ""
                n["unique"] -= 1
                n["unique_rejected_lines_differ"] += 1
        # a claim link matched for some services of a bill names EMA's claim: the bill's other services take that
        # claim's lines for their own day and code
        bill_links = defaultdict(set)
        for s in self.svcs.values():
            for E in s.eml:
                bill_links[s.bill].add(E.link)
        for s in self.svcs.values():
            if s.eml or not s.dos or s.bill not in bill_links:
                continue
            c = [lines[(lk, s.dos, s.code)] for lk in bill_links[s.bill] if (lk, s.dos, s.code) in lines]
            c = [E for E in c if not E.svcs and (not E.bill or E.bill == s.bill)]
            if c:
                s.eml, s.ema_how = c, "claim_link"
                for E in c:
                    E.svcs.append(s)
                n["claim_link"] += 1
        for k, v in n.items():
            self.stats["ema_matched_" + k] = v
        self.stats["ema_lines"] = len(lines)
        self._ef = {}

    def efam(self, payer_id):
        f = self.ema_pfam.get(payer_id)
        if f:
            return f
        return "EMA:" + norm_name(self.R.ema_names.get(payer_id, ""))

    def ema_facts(self, s):
        """EMA's books for one service, added up over the EMA lines matched to it. None when there is none."""
        if not s.eml:
            return None
        ef = self._ef.get(s.key)
        if ef is not None:
            return ef
        ef = {"pays": [], "adjs": [], "answers": [], "status": {}, "action": {}, "ar": [], "pat_paid": 0,
              "charges": 0, "paid": 0, "allowed": 0, "shared": [], "voided": False, "primary": None}
        st_all = []
        for E in s.eml:
            for (d, pay, amt, chk, on) in E.pays:
                ef["pays"].append((d, self.efam(pay), amt, on, pay))
                ef["paid"] += amt
            for (d, pay, g, c, ca, nc) in E.adjs:
                ef["adjs"].append((d, self.efam(pay), g, c, ca + nc, nc))
            for (d, pay, chk) in E.answers:
                ef["answers"].append((d, self.efam(pay)))
            for pay, st in E.status.items():
                ef["status"][self.efam(pay)] = st
                st_all.append(st)
            for pay, ac in E.action.items():
                if ac:
                    ef["action"][self.efam(pay)] = ac
            ef["ar"] += E.ar
            ef["pat_paid"] += E.pat_paid
            ef["charges"] += E.charges
            ef["allowed"] = max(ef["allowed"], E.allowed)
            if E.primary is not None:
                ef["primary"] = self.efam(E.primary)
            for o in E.svcs:
                if o is not s and o not in ef["shared"]:
                    ef["shared"].append(o)
        ef["voided"] = bool(st_all) and all(x == "Inactive/Voided" for x in st_all) or \
            (ef["charges"] <= 0 and any(E.charges == 0 and E.units <= 0 for E in s.eml) and not ef["pays"])
        self._ef[s.key] = ef
        return ef

    def ema_paid_excess(self, s, ef):
        """Insurance money EMA posted on the service that the 835s on file do not show (any payer, net of reversals):
        (amount, payer family, first payment date). The 835 side is every standing payment of the services sharing
        EMA's line (a line split into 26 and TC on one bill shares one EMA line)."""
        if ef is None or not ef["pays"]:
            return 0, None, ""
        ema = ef["paid"]
        if ema <= 0:
            return 0, None, ""
        seen = 0
        for o in [s] + ef["shared"]:
            seen += sum(A.paid for A in o.answers if A.standing and A.paid > 0)
        exc = ema - seen
        if exc <= max(100, 0.02 * max(0, ef["charges"])):
            return 0, None, ""
        byf = Counter()
        first = {}
        for d, f, amt, on, pay in ef["pays"]:
            byf[f] += amt
            if amt > 0 and (f not in first or d < first[f]):
                first[f] = d
        f = byf.most_common(1)[0][0]
        return exc, f, first.get(f, "")

    def ema_payment(self, ef, excl=()):
        """EMA's net insurance payments on the service by payers other than excl: (amount, family, first date) or None."""
        if ef is None or not ef["pays"]:
            return None
        byf = Counter()
        first = {}
        for d, f, amt, on, pay in ef["pays"]:
            if f in excl:
                continue
            byf[f] += amt
            if amt > 0 and (f not in first or d < first[f]):
                first[f] = d
        if not byf:
            return None
        tot = sum(byf.values())
        if tot <= 100:
            return None
        f = byf.most_common(1)[0][0]
        return tot, f, first.get(f, "")

    def ema_answer(self, s, ef, excl=()):
        """A payer's answer that the 835s on file do not show, as EMA recorded it: ("DENIED", carc, fam, date),
        ("MOVED_TO_PATIENT", carc, fam, date) or ("ZERO_RATE", "CO-45", fam, date); None when EMA shows no final answer.
        Final: EMA's claim for that payer is Denied, or it is Resolved after a payer answer row (a check row, $0
        included). The reason is the largest payer adjustment that is not routine (CO-45/253...), else the CARCs EMA's
        weekly claim-status export recorded. A row on a claim still pending is not an answer; adjustments with no payer
        answer are a person's write-off."""
        if ef is None:
            return None
        stat = {f: st for f, st in ef["status"].items() if f not in excl}
        ans = [(d, f) for d, f in ef["answers"] if f not in excl]
        denied = [f for f, st in stat.items() if st == "Denied"]
        resolved = [f for f, st in stat.items() if st.startswith("Resolved")]
        if not denied and not (ans and resolved):
            return None
        fams = set(denied) | {f for d, f in ans if f in resolved}
        last = max([d for d, f in ans if f in fams] + [d for d, f, g, c, a, n in ef["adjs"] if f in fams] + [""])
        fam = denied[0] if denied else [f for d, f in ans if f in fams and d == last][0] if \
            any(d == last for d, f in ans if f in fams) else sorted(fams)[0]
        reasons = Counter()
        routine = 0
        for d, f, g, c, amt, nc in ef["adjs"]:
            if f not in fams or amt <= 0:
                continue
            if g != "PR" and c not in NON_DENIAL_CARCS:
                reasons[(g, c)] += amt
            else:
                routine += amt
        wk = [x[6] for x in sorted(s.emast, key=lambda x: x[4]) if len(x) > 6 and x[6]]
        wcodes = [c.strip().upper() for c in wk[-1].split("|")] if wk else []
        wden = [c for c in wcodes if "-" in c and not c.startswith("PR-") and c.split("-", 1)[1] not in NON_DENIAL_CARCS]
        wpr = [c for c in wcodes if c.startswith("PR-")]
        if reasons:
            (g, c), amt = reasons.most_common(1)[0]
            dd = max((d for d, f2, g2, c2, a2, n2 in ef["adjs"] if f2 in fams and c2 == c), default=last)
            return ("DENIED", f"{g}-{c}", fam, dd or last)
        if denied:
            return ("DENIED", wden[0] if wden else "EMA-denied", fam, last)
        if wden:
            return ("DENIED", wden[0], fam, last)
        if wpr or ef["charges"] - routine > 100 or ef["pat_paid"] > 0:
            return ("MOVED_TO_PATIENT", wpr[0] if wpr else "", fam, last)
        return ("ZERO_RATE", "CO-45", fam, last)

    def ema_work(self, ef, fam, since):
        """Work recorded in EMA on the payer's claim after its denial: a current claim action of the appeal, corrected
        claim, records or reprocessing kind on a claim still open with the payer; an A/R billing note of those kinds (or
        a call, a follow-up) on the line; an A/R follow-up date. The sub-kind names the evidence; None when none."""
        if ef is None:
            return None
        for f, ac in ef["action"].items():
            if ac in EMA_WORK_ACTIONS and (f == fam or f.startswith("EMA:")) and \
                    ef["status"].get(f, "") in EMA_OPEN_STATUSES:
                return "ema_claim_action"
        for (cst, nk, nd, fu, bal, den, party, act, cov) in ef["ar"]:
            if cst.startswith("Resolved") or cov.lower().startswith("secondary") or party.lower() == "patient":
                continue
            kinds = set(nk.split(",")) if nk else set()
            if kinds & EMA_WORK_NOTES and (not nd or not since or nd >= since):
                for k_ in ("appeal", "reprocess", "records"):
                    if k_ in kinds:
                        return "ema_note_" + k_
            if act in EMA_WORK_ACTIONS or cst == "Insurance Reprocessing":
                return "ema_claim_action"
        return None

    def primary_family(self, bill):
        """A16 fix 4: the bill's primary payer: of the payers whose standing processings paid or sent an allowed amount
        to the patient (PR-1/2/3) as primary (CLP02 1/19), the first, leaving out payers that answer as a secondary
        (CLP02 2/3, OA-23 or CO-23, RARC MA04) when another is there; with none, the payer that answered last leaving
        out secondaries and wrong-payer answers (CARC 22/109/24, PR-22). None when only one payer answered, or when
        nothing tells (then every payer's CLP02 1/19/4 answers count, as in A15)."""
        if bill in self._pfam:
            return self._pfam[bill]
        procs = [p for p in self.by_bill.get(bill, []) if p.cancelled_by is None and p.st != REVERSAL
                 and p.freq != "8" and p.copy_of is None]
        fams = {p.fam for p in procs}
        P = None
        if len(fams) > 1:
            sec_role, wrong, allow_first, last = set(), set(), {}, {}
            for p in procs:
                cas = list(p.ccas) + [c for L in p.lines for c in L.cas]
                rarcs = set(p.crarc) | {r for L in p.lines for r in L.rarc}
                if p.st in SECONDARY or any(r == "23" and a != 0 for g, r, a in cas) or "MA04" in rarcs:
                    sec_role.add(p.fam)
                if p.paid <= 0 and any(r in ("22", "109", "24") and a > 0 for g, r, a in cas):
                    wrong.add(p.fam)
                # calibration round 2: a processing whose payment was all taken back (recouped) does not make its payer
                # the primary
                recouped = p.paid > 0 and sum(L.paid_tb for L in p.lines) <= 0
                if p.st in ("1", "19") and not recouped and (p.paid > 0 or any(
                        g == "PR" and r in ("1", "2", "3") and a > 0 for g, r, a in cas)):
                    if p.fam not in allow_first or p.ordk < allow_first[p.fam]:
                        allow_first[p.fam] = p.ordk
                if p.st in PRIMARY and (p.fam not in last or p.ordk > last[p.fam]):
                    last[p.fam] = p.ordk
            cands = [f for f in allow_first if f not in sec_role] or list(allow_first)
            # A16 (calibration round 2): a payer that adjudicated the claim as primary (paid, or allowed a patient share)
            # and forwarded it (CLP02 19, or a crossover carrier named) is the primary, whatever the order of the answers
            # (a CLP02 19 that only transfers the claim, CO-B11 with nothing allowed, does not count)
            fwd = {p.fam for p in procs if (p.st == "19" or (p.xover and p.st in ("1", "19"))) and
                   (p.paid > 0 or any(g == "PR" and r in ("1", "2", "3") and a > 0
                                      for g, r, a in list(p.ccas) + [c for L in p.lines for c in L.cas]))}
            if len(fwd) == 1:
                P = next(iter(fwd))
            elif cands:
                P = min(cands, key=lambda f: allow_first[f])
            else:
                c2 = [f for f in last if f not in sec_role and f not in wrong]
                if c2:
                    P = max(c2, key=lambda f: last[f])
        self._pfam[bill] = P
        return P

    def answers_after(self, bill, date, fam=None):
        """The bill's answers on or after a send: any primary answer, or any answer of the payer it was sent to
        (a secondary's CLP02 2 answers a send to the secondary). Split-claim notices (MA15) answer nothing."""
        return [p for p in self.by_bill.get(bill, []) if p.date >= date and p.cancelled_by is None
                and p.st != REVERSAL and p.freq != "8" and p.lines
                and not (p.paid == 0 and set(p.crarc) & SPLIT_NOTICE_RARCS)
                and (p.st in PRIMARY or (fam is not None and p.fam == fam))]

    def line_allowed(self, gl):
        """A16 fix 12: the payer's own price stated on a denied line: charge - CO-45 when the line carries the denial
        and a CO-45 reduction to the allowed (UMR's CO-210 + CO-45). Cents per unit, or 0."""
        tot, u = 0, 0
        for A in gl:
            B = self.unpaid_part(A)
            c45 = sum(a for g, r, a in B.cas if r == "45" and a > 0)
            den = sum(a for g, r, a in B.cas if g != "PR" and r not in NON_DENIAL_CARCS and a > 0)
            if c45 <= 0 or den <= 0 or B.charge - c45 <= 0 or abs(B.charge - c45 - den) > 1:
                return 0
            tot += B.charge - c45
            u += B.ub
        return int(round(tot / max(1, u))) if tot > 0 else 0

    def same_payer(self, send_fam, fam):
        return send_fam == fam or not send_fam or send_fam.startswith("EMA:")

    def patient_paid_claim(self, g, ef):
        """A16 fix 7: the patient paid toward a balance the payer sent to the patient for a COB, network or eligibility
        reason: EMA patient money on this service, or on another service of the same answer that the payer denied for a
        reason of that kind (a coverage lapse denies every line; the patient paying one line takes the claim)."""
        if ef is not None and ef["pat_paid"] > 0:
            return True
        seen = set()
        for L in g.p.lines:
            o = getattr(L, "svc", None)
            if o is None or id(o) in seen or not o.eml:
                continue
            seen.add(id(o))
            if not any(g_ == "PR" and r in PR_WORK_CARCS and a > 0 for g_, r, a in L.cas):
                continue
            ef2 = self.ema_facts(o)
            if ef2 is not None and ef2["pat_paid"] > 0:
                return True
        return False

    def truth(self, s):
        U = s.U
        units = []
        P = self.primary_family(s.bill)
        std = [A for A in s.answers if A.standing]
        if P is None:
            prim = [A for A in std if A.p.st in PRIMARY]
        else:
            prim = [A for A in std if A.p.st in PRIMARY and A.p.fam == P]
        pids = {id(A) for A in prim}
        sec = [A for A in std if id(A) not in pids]
        ef = self.ema_facts(s)
        bill_sends = self.sends_by_bill.get(s.bill, [])
        # voids: the bill's latest send is a void, or a void answer (CLP09 8) with nothing sent or answered after it
        void_dates = [x[0] for x in bill_sends if x[2] == "8"]
        void_dates += [p.date for p in self.by_bill.get(s.bill, []) if p.freq == "8"]
        if void_dates:
            vd = max(void_dates)
            later_send = any(x[0] > vd and x[2] in ("1", "7", "", "ema") for x in bill_sends)
            same_day_new = any(x[0] == vd and x[2] in ("1", "7") for x in bill_sends)
            # A16 (calibration round 2): the payer's $0 whole-claim answer to the void (CLP02 4) does not revive the claim
            later_answer = any(A.p.date > vd and (A.paid > 0 or A.p.st in ("1", "19")) for A in prim)
            if not later_send and not same_day_new and not later_answer:
                return [("VOIDED", "837_or_clp09_8", "", "", "", vd, 0, "", "", False)] * U
        if s.charge_pu <= 0 and not any(A.paid > 0 for A in s.answers):
            return [("ZERO_CHARGE", "", "", "", "", "", 0, "", "", False)] * U
        # A16 fix 13: a code the practice dropped from a corrected claim (a later replacement, CLP09/CLM05-3 = 7, that
        # lists the bill's other services but not this one) was withdrawn: nothing is owed on it
        last_own = max([A.p.date for A in s.answers if A.standing] + [x[0] for x in s.sends] + [""])
        repl = [p for p in self.by_bill.get(s.bill, []) if p.freq == "7" and p.cancelled_by is None and
                p.st in PRIMARY and p.lines and p.date > last_own and s.dos in {L.dos for L in p.lines}]
        if repl and not any(L.svc is s for p in repl for L in p.lines) and not any(A.paid > 0 for A in std):
            return [("VOIDED", "dropped_from_corrected_claim", "", repl[-1].fam, "", repl[-1].date, 0, "", "",
                     False)] * U
        st_v = [x for E in s.eml for x in E.status.values()]
        if ef is not None and st_v and all(x == "Inactive/Voided" for x in st_v) and \
                not any(A.paid_tb > 0 for A in std):
            # calibration round 2: EMA voided the charge line (the practice withdrew the charge, e.g. to rebill it on
            # another claim) and no payment stands: nothing is owed on this bill
            return [("VOIDED", "ema_voided", "", "", "", "", 0, "", "", False)] * U
        if s.charge_pu <= PLACEHOLDER_CENTS and not any(A.paid > 0 for A in s.answers):
            # A16 fix 13: a $0.01-a-unit placeholder line (a drug reported for its NDC on a split claim) is no money
            return [("ZERO_CHARGE", "placeholder_line", "", "", "", "", 0, "", "", False)] * U
        # ---- paid units: the primary payer's (with no primary payer known, the payer that paid the most units)
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
        gov = None
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
            sub = "full"
            if mods & MPPR_MODS or any(r == "59" for A in fl for g, r, a in A.cas):
                sub = "full_mppr"
            rinfo = self.rate_for(fam, gov.p.prod, s.code, s.comp, (s.dos or "0000")[:4], self.pos_class(gov.p),
                                  gov.npi)
            state, owed, basis = "PAID", 0, ""
            short_units = []          # (units, gap per unit, provider discount?) of paid pieces below the rate
            if gov.changed:
                sub = "paid_other_code"
            if mods & REDUCING_MODS:
                sub = "reduced_service_modifier"
            elif rinfo is None:
                sub = "unrated" if sub == "full" else sub
            else:
                r_amt, r_payer, r_obs, r_tier, r_weak = rinfo
                basis = "rate_l6" if r_tier in ("payer", "provider_discount") else "rate_" + r_tier
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
                # an NP/PA (or a line with no rendering provider to tell) may be paid at 85% of the payer's full rate
                np_ok = self.np_ok_here(fam, gov.p.prod, s.code, s.comp, (s.dos or "0000")[:4], self.pos_class(gov.p),
                                        gov.npi, r_tier)
                for p_apu, p_units, A, L in pieces:
                    pm = set(L.mods) if L is not None else set(A.mods)
                    # calibration round 3: a multiple-procedure modifier the payer applied (SVC01 51 with an SVC06 that
                    # carries none) is the payer's reduction too
                    pm |= set(getattr(L, "amods", None) or ()) if L is not None else \
                        {m_ for x_ in A.lines for m_ in (getattr(x_, "amods", None) or ())}
                    pc_ = L.cas if L is not None else A.cas
                    pf = 0.5 if (pm & MPPR_MODS or any(r == "59" for g, r, a in pc_)) else 1.0
                    # A16 fix 5(c): the expected amount never exceeds the billed charge per unit; an NP/PA's payment is
                    # tested against the payer's full rate and its 85% (whichever the payer applies to that provider)
                    cands = [r_payer, r_amt] + ([int(round(0.85 * r_payer))] if np_ok else [])
                    cands = sorted({min(c_ * pf, s.charge_pu) if s.charge_pu else c_ * pf for c_ in cands})
                    if np_ok and s.charge_pu and s.charge_pu < r_payer:
                        # calibration round 2: a charge below the payer's full rate is the full amount; the NP/PA tier
                        # is 85% of it (the payer applies the lesser of charge and rate before the tier)
                        cands = sorted(set(cands) | {int(round(0.85 * s.charge_pu * pf))})
                    exp = cands[0] if np_ok else min(r_amt * pf, s.charge_pu) if s.charge_pu else r_amt * pf
                    gap = exp - p_apu
                    psub = "full_mppr" if pf == 0.5 else "full"
                    if gap > max(100, 0.03 * exp):
                        # A16: another amount the payer pays on 20%+ of its answers to OTHER providers (A15 left the
                        # payment's own amount out of the list, so the test could never pass; and one provider's own
                        # discount is no second rate of the payer: owner rule 4.1); never an amount at 85% of the rate
                        surg = self.is_surgical(s.code)
                        own_c = self.rate_obs.get((fam, gov.p.prod, s.code, s.comp, (s.dos or "0000")[:4],
                                                   self.pos_class(gov.p), gov.npi or "")) or Counter()
                        o_obs = Counter({a_: k - own_c.get(a_, 0) for a_, k in r_obs.items() if k - own_c.get(a_, 0) > 0})
                        k6_ = (fam, gov.p.prod, s.code, s.comp, (s.dos or "0000")[:4], self.pos_class(gov.p))
                        for dn in self.disc_provs.get(k6_, ()):
                            if dn != (gov.npi or ""):
                                for a_, k in (self.rate_obs.get(k6_ + (dn,)) or {}).items():
                                    o_obs[a_] -= k
                        o_obs = Counter({a_: k for a_, k in o_obs.items() if k > 0})
                        n_other = sum(o_obs.values())
                        alt = [a_ for a_, k in o_obs.items() if k >= 0.2 * max(1, n_other)
                               and abs(a_ - 0.85 * r_payer) > 0.015 * r_payer
                               and not (surg and abs(a_ - 0.5 * r_payer) <= 0.03 * r_payer)]
                        own_lines = {L.lk} if L is not None else {x.lk for x in A.lines}
                        n_all = max(1, sum(r_obs.values()))
                        alt_full = [a_ for a_, k in r_obs.items() if k >= 0.1 * n_all and k >= 3]
                        if pf == 1.0 and any(abs(p_apu - 0.5 * c_) <= 0.05 * c_ for c_ in cands + [exp] + alt_full) and \
                                self.mppr_eligible_lines(A.p, own_lines, s.code):
                            psub = "half_rate_mppr"
                        elif any(abs(p_apu - a_ * pf) <= 2 for a_ in alt):
                            psub = "second_rate"
                        else:
                            # calibration round 3: the lower-valued surgical line of a claim (eligible for a multiple-
                            # procedure reduction) is owed at least the half; paid at or above it, it is not short
                            if pf == 1.0 and self.mppr_eligible_lines(A.p, own_lines, s.code):
                                half_exp = 0.5 * exp
                                if p_apu >= half_exp - max(100, 0.03 * half_exp):
                                    subs["mppr_line_paid_half_or_more"] += p_units
                                    continue
                                gap = half_exp - p_apu
                            short_units.append((p_units, int(round(gap)), r_tier == "provider_discount"))
                            continue
                    subs[psub] += p_units
                if subs and sub not in ("paid_other_code",):
                    sub = subs.most_common(1)[0][0]
                if short_units and not subs:
                    state = "SHORT"
                    owed = int(round(sum(u_ * g_ for u_, g_, d_ in short_units) /
                                     max(1, sum(u_ for u_, g_, d_ in short_units))))
                    disc = all(d_ for u_, g_, d_ in short_units)
                    sub = "paid_other_code" if gov.changed else ("below_rate_provider_discount" if disc else
                                                                 "below_weak_rate" if r_weak else "below_rate")
                    if disc:
                        rv = self.review[(fam, gov.npi or "")]
                        rv[0] += 1
                        rv[1] += sum(u_ for u_, g_, d_ in short_units)
                        rv[2] += sum(u_ * g_ for u_, g_, d_ in short_units)
                    short_units = []
            if state == "PAID" and any(A.paid_tb < A.paid for A in fl):
                taken = sum(A.paid - A.paid_tb for A in fl)
                state, sub, owed = "SHORT", "partly_taken_back", int(round(taken / up_f))
            if state == "PAID":
                pr_left = sum(a for A in fl for g, r, a in A.cas if g == "PR")
                fwd = gov.p.st == "19" or gov.p.xover
                if pr_left > 0 and fwd and not any(x.p.date >= gov.p.date for x in sec):
                    sub = "waiting_secondary"
            tbd = max((L.tb_date or "" for A in fl for L in A.lines), default="")
            wbase = tbd if (state == "SHORT" and sub == "partly_taken_back" and tbd) else gov.p.date
            wend = add_days(wbase, self.appeal_days(gov.p.prod)) if state == "SHORT" else ""
            inside = bool(wend) and wend >= self.as_of
            n_short = min(PU, sum(u_ for u_, g_, d_ in short_units)) if state == "PAID" else 0
            if n_short:
                weak = rinfo[4]
                disc = all(d_ for u_, g_, d_ in short_units)
                g_avg = int(round(sum(u_ * g_ for u_, g_, d_ in short_units) / max(1, sum(u_ for u_, g_, d_ in short_units))))
                wend_s = add_days(gov.p.date, self.appeal_days(gov.p.prod))
                if disc:
                    rv = self.review[(fam, gov.npi or "")]
                    rv[0] += 1
                    rv[1] += n_short
                    rv[2] += n_short * g_avg
                units += [("SHORT", "below_rate_provider_discount" if disc else "below_weak_rate" if weak else "below_rate",
                           "", fam, gov.lk, gov.p.date, g_avg, basis, wend_s, wend_s >= self.as_of)] * n_short
            units += [(state, sub, "", fam, gov.lk, gov.p.date, owed, basis, wend, inside)] * (PU - n_short)
        rest = U - len(units)
        if rest <= 0:
            return units[:U]
        # ---- taken back units (A16 fix 13: the window runs from the take-back)
        taken_units = max(fam_taken.values(), default=0)
        if taken_units > 0 and ef is not None and ef["pays"]:
            # calibration round 4: EMA posted the payer's recoupment and then a later payment of the service (a check
            # that is no 835 on file): paid again
            pays = sorted(ef["pays"], key=lambda x: x[0])
            negs = [x[0] for x in pays if x[2] < 0]
            taken_amt = sum(A.paid - A.paid_tb for A in prim if A.paid > 0 and A.paid_tb <= 0)
            if negs and any(x[2] > 0 and x[0] > max(negs) for x in pays) and \
                    sum(x[2] for x in pays) >= 0.9 * taken_amt > 0:
                lp = [x for x in pays if x[2] > 0 and x[0] > max(negs)][-1]
                return units + [("PAID", "ema_repaid_after_takeback", "", lp[1], "", lp[0], 0, "", "", False)] * \
                    (U - len(units))
        if taken_units > 0:
            tb = sorted([A for A in prim if A.paid > 0 and A.paid_tb <= 0], key=lambda A: A.p.ordk, reverse=True)
            g = tb[0]
            k = min(rest, taken_units)
            tbd = max((L.tb_date or "" for L in g.lines), default="") or g.p.date
            wend = add_days(tbd, self.appeal_days(g.p.prod))
            units += [("TAKEN_BACK", "plb", "", g.p.fam, g.lk, g.p.date, int(round(g.paid / max(1, g.up))), "taken",
                       wend, wend >= self.as_of)] * k
            rest = U - len(units)
            if rest <= 0:
                return units[:U]
        # ---- the latest standing primary answer with unpaid units
        unpaid = [A for A in prim if (A.paid <= 0) or (A.paid_tb > 0 and A.ub > A.up)]
        if not unpaid and PU > 0:
            # the payer paid fewer units than were billed and denied none. A16 fix 2: "fewer units" needs evidence
            # (a units CARC/RARC, or a charge the answer does not reconcile); otherwise every billed unit was adjudicated
            fl_ = fam_ans.get(fam, [])
            evid = any(r in UNITS_CARCS for A in fl_ for g_, r, a in A.cas) or \
                any(r in UNITS_RARCS for A in fl_ for r in A.rarc)
            billed = s.charge_pu * U
            answered = sum(A.charge for A in fl_)
            recon = all(abs(A.charge - (A.paid + sum(a for g_, r, a in A.cas))) <= 1 for A in fl_)
            if not evid and recon and answered >= billed - max(100, 0.01 * billed):
                return units + [("PAID", "all_billed_units_adjudicated", "", fam, gov.lk, gov.p.date, 0, "", "",
                                 False)] * rest
            wend = add_days(gov.p.date, self.appeal_days(gov.p.prod))
            return units + [("SHORT", "fewer_units_answered", "", fam, gov.lk, gov.p.date, int(round(gov.apu())),
                             "paid_unit", wend, wend >= self.as_of)] * rest
        if unpaid:
            return units + self.unpaid_truth(s, rest, unpaid, prim, sec, ef, PU)
        # ---- no standing primary answer
        excl = {A.p.fam for A in sec}
        ep = self.ema_payment(ef, excl)
        if ep is not None:
            # A16 fix 1: EMA posted a payment from a payer the 835s on file do not show answering this service
            return units + [("PAID", "ema_paid_off_feed", "", ep[1], "", ep[2], 0, "", "", False)] * rest
        ea = self.ema_answer(s, ef, excl)
        if ea is not None:
            return units + self.ema_answer_units(s, rest, ea, ef)
        if sec:
            ls = max(sec, key=lambda A: A.p.ordk)
            return units + [("SECONDARY_ONLY", "primary_remittance_missing", "", ls.p.fam, ls.lk, ls.p.date, 0, "", "",
                             False)] * rest
        cancelled = [A for A in s.answers if not A.standing and A.p.st in PRIMARY]
        sends = [x for x in s.sends if x[2] != "8"]
        ema_status = sorted(s.emast, key=lambda x: x[4])
        est = ema_status[-1][1] if ema_status else ""
        if ef is not None and ef["voided"]:
            return units + [("VOIDED", "ema_voided", "", "", "", "", 0, "", "", False)] * rest
        weekly_paid = self.ema_paid.get((s.bill, s.code, s.dos), 0) + self.ema_paid_era.get((s.bill, s.code, s.dos), 0)
        if weekly_paid > 0:
            return units + [("PAID", "ema_paid_weekly_export", "", "", "", "", 0, "", "", False)] * rest
        if not sends and not cancelled:
            if est in ("Resolved/Paid", "Resolved/Other"):
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
        if (fam, s.code, s.comp) in self.never_paid or (fam, s.code, s.comp) in self.rarely_paid_fam or \
                s.code == "99024":
            owed, basis = 0, "payer_does_not_pay_this_code"
        if est in ("Resolved/Paid", "Resolved/Other"):
            return units + [("EMA_PAID_NO_REMIT", "", "", fam, "", sd, owed, basis, "", False)] * rest
        if est == "Inactive/Voided":
            return units + [("EMA_VOIDED", "", "", fam, "", sd, 0, "", "", False)] * rest
        wend = max(add_days(s.dos, 365) if s.dos else sd, add_days(sd, 180))
        if self.answers_after(s.bill, sd, fam):
            return units + [("NEVER_ANSWERED", "answered_without_this_code", "", fam, "", sd, owed, basis, wend,
                             wend >= self.as_of)] * rest
        if est == "Rejected":
            return units + [("NEVER_ANSWERED", "rejected_before_payer", "", fam, "", sd, owed, basis, wend,
                             wend >= self.as_of)] * rest
        fs = self.feed_state(fam) if fam and not fam.startswith("EMA:") else "UNMAPPED"
        if fs in ("NO_FEED", "UNMAPPED"):
            # A16 fix 1: a payer outside the 835 feed answers through EMA only; with EMA's books read and nothing posted
            # past its answer time, the claim is never answered
            if ef is not None and days_between(sd, self.feed_date) > self.usual_days(fam):
                return units + [("NEVER_ANSWERED", "no_answer_in_ema", "", fam, "", sd, owed, basis, wend,
                                 wend >= self.as_of)] * rest
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

    def ema_answer_units(self, s, rest, ea, ef):
        """Units of a service the 835s on file never answered but EMA shows the payer answered."""
        kind, carc, fam, d = ea
        if kind == "MOVED_TO_PATIENT":
            if not (carc and carc.split("-", 1)[-1] in PR_WORK_CARCS and ef["pat_paid"] <= 0):
                return [("MOVED_TO_PATIENT", "ema_allowed_to_patient", carc, fam, "", d, 0, "", "", False)] * rest
            kind = "DENIED"
        if kind == "ZERO_RATE":
            r_amt, _ = self.rate_est(fam, None, s.code, s.comp, (s.dos or "0000")[:4])
            if not r_amt:
                return [("PAID", "zero_rate_ema", carc, fam, "", d, 0, "", "", False)] * rest
            kind, carc = "DENIED", "CO-45"
        cnum = carc.split("-", 1)[-1]
        np_ = self.not_payable(s, fam, None, carc, None)
        if np_:
            return [("NOT_PAYABLE", np_, carc, fam, "", d, 0, "", "", False)] * rest
        if carc.startswith("PR-") and cnum not in PR_WORK_CARCS and ef["pat_paid"] > 0:
            return [("MOVED_TO_PATIENT", "ema_" + carc, carc, fam, "", d, 0, "", "", False)] * rest
        owed, basis = self.owed_rate(s, fam, None, None)
        if basis != "charge":
            basis = "est_" + basis if not basis.startswith("est_") else basis
        ew = self.ema_work(ef, fam, d)
        wend = self.window_end("", carc, d, s.dos)
        if ew:
            return [("WORKED_WAITING", ew, carc, fam, "", d, owed, basis, wend, wend >= self.as_of)] * rest
        return [("DENIED", "ema_off_feed_answer", carc, fam, "", d, owed, basis, wend, wend >= self.as_of)] * rest

    def second_rate_alts(self, fam, prod, code, comp, year, pc, npi, r_obs, r_payer):
        """Amounts a payer allows on 20%+ of its answers to OTHER providers (not this provider's own, not a provider-
        level discount, not the NP/PA 85% of the rate, not a surgical half): the payer's second rates."""
        surg = self.is_surgical(code)
        key6 = (fam, prod, code, comp, year, pc)
        own_c = self.rate_obs.get(key6 + (npi or "",)) or Counter()
        o_obs = Counter({a_: k - own_c.get(a_, 0) for a_, k in r_obs.items() if k - own_c.get(a_, 0) > 0})
        for dn in self.disc_provs.get(key6, ()):
            if dn != (npi or ""):
                for a_, k in (self.rate_obs.get(key6 + (dn,)) or {}).items():
                    o_obs[a_] -= k
        o_obs = Counter({a_: k for a_, k in o_obs.items() if k > 0})
        n_other = sum(o_obs.values())
        return [a_ for a_, k in o_obs.items() if k >= 0.2 * max(1, n_other)
                and abs(a_ - 0.85 * r_payer) > 0.015 * r_payer
                and not (surg and abs(a_ - 0.5 * r_payer) <= 0.03 * r_payer)]

    def not_payable(self, s, fam, prod, carc, procs):
        """A16 fix 6 and the owner's rule 4.2: why a denied unit is not payable, or None. 99024 is never paid; CARC
        246 is a reporting-only code; G2211 beside an E/M with modifier 25 on the same claim and day (CMS policy, the
        payers that follow it denied every one); a code the payer never (10+ answers) or rarely (under 5% of 20+, per
        product) pays, unless the denial is an authorization, eligibility, enrolment, timely-filing or
        missing-information reason."""
        cnum = (carc or "").split("-", 1)[-1]
        if s.code == "99024":
            return "never_paid_post_op_visit"
        if cnum in REPORTING_ONLY_CARCS:
            return "reporting_only_code"
        if procs and cnum not in NOT_PAYABLE_EXCL and any(
                "N448" in L.rarc for p in procs for L in p.lines if L.code == s.code and L.dos == s.dos):
            # calibration round 4: RARC N448, the service is not in the payer's fee schedule (G2211 at a Medicaid plan)
            return "code_not_in_payer_fee_schedule"
        if s.code == "G2211" and procs and cnum not in NOT_PAYABLE_EXCL:
            for p in procs:
                if any(L.code in EM_CODES and "25" in L.mods and L.dos == s.dos for L in p.lines):
                    return "g2211_with_em_25"
        if s.code == "G2211" and not procs and cnum not in NOT_PAYABLE_EXCL:
            # calibration round 2: an answer seen only in EMA's books: the claim's own 837 tells the E/M's modifiers
            for x in s.sends:
                if x[6] != "837":
                    continue
                for r in getattr(self, "l837_raw", {}).get(x[7], []):
                    if (r[2] or "").strip().upper() in EM_CODES and "25" in parse_mods(r[3]) and \
                            (iso(r[6]) or s.dos) == s.dos:
                        return "g2211_with_em_25"
        if cnum in NOT_PAYABLE_EXCL:
            return None
        if (fam, s.code, s.comp) in self.never_paid:
            return "payer_never_pays_this_code"
        if prod is not None and (fam, prod, s.code, s.comp) in self.rarely_paid:
            return "payer_rarely_pays_this_code"
        if prod is not None and (fam, "@" + self.pclass(prod), s.code, s.comp) in self.rarely_paid_cls:
            return "payer_rarely_pays_this_code_product_class"
        if prod is not None and cnum in BUNDLED_CARCS and \
                (fam, "@" + self.pclass(prod), s.code, s.comp) in self.rarely_paid_cls_bundled:
            return "payer_rarely_pays_this_code_product_class"
        if prod is None and (fam, s.code, s.comp) in self.rarely_paid_fam:
            return "payer_rarely_pays_this_code"
        return None

    def unpaid_truth(self, s, rest, unpaid, prim, sec, ef, PU):
        """The units the primary payer did not pay, from its latest standing answer with unpaid units."""
        # the latest answer; on one day a processing (CLP02 1/19) outranks a whole-claim denial (CLP02 4)
        g = max(unpaid, key=lambda A: (A.p.date, A.p.st != "4", A.p.ordk))
        gl = [A for A in unpaid if A.p.ordk == g.p.ordk]
        kind, carc = self.denial_of([self.unpaid_part(A) for A in gl])
        pr_allowed = self._pr_allowed
        fam, prod = g.p.fam, g.p.prod
        sub = ""
        cnum = carc.split("-", 1)[-1]
        rarcs = {r for A in gl for r in A.rarc} | set(g.p.crarc)
        if kind == "DENIED" and (cnum in DUP_CARCS or (cnum == "151" and rarcs & DUP_RARCS)):
            # A16 fix 8: a duplicate denial defers to the same payer's previous standing answer, whatever its kind
            earlier = [A for A in prim if A is not g and A.p.fam == fam and A.p.ordk < g.p.ordk]
            for A in sorted(earlier, key=lambda A: A.p.ordk, reverse=True):
                if A.paid > 0 and A.paid_tb > 0:
                    return [("PAID", "duplicate_of_paid_answer", carc, fam, A.lk, A.p.date, 0, "", "", False)] * rest
                k2, c2 = self.denial_of([self.unpaid_part(A)])
                c2n = c2.split("-", 1)[-1]
                rar2 = set(A.rarc) | set(A.p.crarc)
                if k2 == "DENIED" and (c2n in DUP_CARCS or (c2n == "151" and rar2 & DUP_RARCS)):
                    continue
                if k2 in ("PATIENT", "DENIED", "ZERO_RATE"):
                    g = A
                    gl = [A]
                    kind, carc = k2, c2
                    pr_allowed = self._pr_allowed
                    cnum = carc.split("-", 1)[-1]
                    sub = "latest_answer_is_duplicate_denial"
                break
        if kind == "ZERO_RATE":
            r_amt, _ = self.rate_est(fam, prod, s.code, s.comp, (s.dos or "0000")[:4], self.pos_class(g.p), g.npi)
            if r_amt and r_amt > 0:
                kind, carc = "DENIED", "CO-45"
                cnum = "45"
        if kind == "ZERO_CHARGE":
            return [("ZERO_CHARGE", "", "", fam, g.lk, g.p.date, 0, "", "", False)] * rest
        if kind == "ZERO_RATE":
            return [("PAID", "zero_rate", carc, fam, g.lk, g.p.date, 0, "", "", False)] * rest
        # A16 fix 7: PR-22 (another payer primary), PR-242 (out of network), PR-27/26/31/32/33/177/200 (coverage,
        # eligibility), PR-227 (information from the patient) are denials to work, unless EMA shows the patient paid
        if kind == "PATIENT" and cnum in PR_WORK_CARCS and not self.patient_paid_claim(g, ef):
            kind = "DENIED"
            sub = "pr_group_cob_network_eligibility"
        level_cut = cnum in LEVEL_REDUCTION_CARCS
        if pr_allowed > 0 and (kind == "PATIENT" or (kind == "DENIED" and level_cut)):
            # the payer allowed the service and sent the allowed amount to the patient: moved to the patient, or short
            # when that allowed amount is below the payer's own rate (A16 fix 7: per unit of the UNPAID part)
            rinfo = self.rate_for(fam, prod, s.code, s.comp, (s.dos or "0000")[:4], self.pos_class(g.p), g.npi)
            apu = pr_allowed / max(1, sum(self.unpaid_part(A).ub for A in gl))
            if rinfo is not None:
                # the same tests as a payment: capped at the charge, an NP/PA's 85%, a multiple-procedure half
                np_ok = self.np_ok_here(fam, prod, s.code, s.comp, (s.dos or "0000")[:4], self.pos_class(g.p), g.npi,
                                        rinfo[3])
                cands = sorted({(min(c_, s.charge_pu) if s.charge_pu else c_) for c_ in
                                [rinfo[1], rinfo[0]] + ([int(round(0.85 * rinfo[1]))] if np_ok else [])})
                if np_ok and s.charge_pu and s.charge_pu < rinfo[1]:
                    cands = sorted(set(cands) | {int(round(0.85 * s.charge_pu))})
                r_amt = cands[0] if np_ok else (min(rinfo[0], s.charge_pu) if s.charge_pu else rinfo[0])
                half = any(abs(apu - 0.5 * c_) <= 0.05 * c_ for c_ in cands + [r_amt]) and self.mppr_eligible(g, s.code)
                # calibration round 3: the same second-rate test as a payment (an amount the payer allows across
                # providers on 20%+ of its answers, not one provider's discount)
                alt = self.second_rate_alts(fam, prod, s.code, s.comp, (s.dos or "0000")[:4], self.pos_class(g.p),
                                            g.npi, rinfo[2], rinfo[1])
                second = any(abs(apu - a_) <= 2 for a_ in alt)
                if r_amt - apu > max(100, 0.03 * r_amt) and not half and not second:
                    wend = add_days(g.p.date, self.appeal_days(prod))
                    basis = "rate_l6" if rinfo[3] in ("payer", "provider_discount") else "rate_" + rinfo[3]
                    ssub = "to_patient_below_rate"
                    if rinfo[3] == "provider_discount":
                        ssub = "to_patient_below_rate_provider_discount"
                        rv = self.review[(fam, g.npi or "")]
                        rv[0] += 1
                        rv[1] += rest
                        rv[2] += rest * int(round(r_amt - apu))
                    return [("SHORT", ssub, carc, fam, g.lk, g.p.date, int(round(r_amt - apu)),
                             basis, wend, wend >= self.as_of)] * rest
            return [("MOVED_TO_PATIENT", carc, carc, fam, g.lk, g.p.date, 0, "", "", False)] * rest
        if kind == "PATIENT":
            return [("MOVED_TO_PATIENT", carc, carc, fam, g.lk, g.p.date, 0, "", "", False)] * rest
        # A16 fix 1: EMA shows insurance money the 835s on file do not (a primary paid off-feed before a secondary's
        # denial, a payer's paper check): checked before the not-payable test (a paid unit is paid)
        exc, efam_, edate = self.ema_paid_excess(s, ef)
        if exc > 0:
            owed0, basis0 = self.owed_rate(s, fam, prod, self.pos_class(g.p), g.npi)
            if efam_ == fam or exc >= 0.4 * max(1, owed0 or s.charge_pu) * rest * (0.5 if basis0 == "charge" else 1):
                return [("PAID", "ema_paid_off_feed", carc, efam_, g.lk, edate, 0, "", "", False)] * rest
        np_ = self.not_payable(s, fam, prod, carc, [g.p])
        if np_:
            return [("NOT_PAYABLE", np_, carc, fam, g.lk, g.p.date, 0, "", "", False)] * rest
        # A16 fix 13: settled as secondary only after a COB denial, or when the payer's later secondary processing
        # (a later day, not a copy on the same check) paid or sent an allowed amount to the patient
        later_sec = [A for A in sec if A.p.fam == fam and A.p.st in SECONDARY and A.p.date > g.p.date]
        cob = cnum in COB_CARCS or "MA04" in rarcs       # MA04: send the primary payer's answer first
        settle = [A for A in later_sec if cob or A.paid > 0 or
                  any(g_ == "PR" and r_ in ("1", "2", "3") and a_ > 0 for g_, r_, a_ in A.cas)]
        if settle:
            ssub = "cob" if cob else "secondary_allowed"
            ls = settle[-1]
            return [("SETTLED_SECONDARY", ssub, carc, fam, ls.lk, ls.p.date, 0, "", "", False)] * rest
        owed, basis = self.owed_rate(s, fam, prod, self.pos_class(g.p), g.npi)
        la = self.line_allowed(gl)
        if la:
            owed, basis = (min(la, s.charge_pu) if s.charge_pu else la), "line_allowed"
        # another payer adjudicated the service: its payment, or the amount it allowed to the patient (PR-1/2/3; a PR-96
        # "not covered" is not an allowed amount), settles the unit when it is half the owed amount or more
        def allowed_of(A):
            return A.paid_tb + sum(a for g_, r_, a in A.cas if g_ == "PR" and r_ in ("1", "2", "3"))
        # calibration round 3: whatever the order of the answers (a later denial by the primary of a claim another
        # payer already paid, e.g. CO-29 on a resend, does not unpay it)
        other_sec = [A for A in sec if A.p.fam != fam and allowed_of(A) > 0]
        if other_sec:
            sec_allowed = sum(allowed_of(A) for A in other_sec)
            # calibration round 3: with no price of the denying payer (the charge stands in), a payment by the other
            # payer that adjudicated the whole charge (its allowed plus its contractual reductions) on more units than
            # the primary paid (not only the coinsurance of the paid ones) settles the unit
            whole = basis == "charge" and any(A.paid_tb > 0 and A.up > PU and
                                              abs(A.charge - (A.paid + sum(a for g_, r_, a in A.cas))) <= 1
                                              for A in other_sec)
            if owed and (sec_allowed >= 0.5 * owed * rest or whole):
                ls = max(other_sec, key=lambda A: A.p.ordk)
                if sum(A.paid_tb for A in other_sec) > 0:
                    return [("PAID", "paid_by_other_payer_as_secondary", carc, ls.p.fam, ls.lk, ls.p.date, 0, "", "",
                             False)] * rest
                return [("MOVED_TO_PATIENT", "allowed_by_other_payer", carc, ls.p.fam, ls.lk, ls.p.date, 0, "", "",
                         False)] * rest
        # A16 fix 10: a send works the denial when it went to the denying payer (any claim id), or, after a COB,
        # wrong-payer or eligibility denial, to any payer
        wp = cnum in WRONG_PAYER_CARCS
        op = cnum in OTHER_PLAN_CARCS
        sec_fams = {A.p.fam for A in sec if A.p.st in SECONDARY or
                    any(r_ == "23" for g_, r_, a_ in A.cas)} if op else set()
        newer = [x for x in s.sends if x[0] > g.p.date and x[2] != "8" and
                 (wp or self.same_payer(x[1], fam) or (op and x[1] not in sec_fams))]
        if newer:
            nd = max(x[0] for x in newer)
            nfam = [x[1] for x in newer if x[0] == nd][0]
            # EMA shows the resend rejected before the payer (claim status Rejected, latest): the work did not reach it
            est_l = sorted(s.emast, key=lambda x: x[4])
            rejected = (est_l and est_l[-1][1] == "Rejected") or (ef is not None and ef["status"] and
                                                                   all(v == "Rejected" for v in ef["status"].values()))
            own_after = [A for A in s.answers if A.standing and A.p.st != REVERSAL and A.p.date >= nd and
                         (A.p.fam == nfam or self.same_payer(nfam, A.p.fam) or A.p.fam == fam)]
            if own_after:
                # calibration round 2: the payer answered the resend for this code (a CLP02 2 answer is not in the
                # primary's list): denied again
                la = max(own_after, key=lambda A: A.p.ordk)
                k2, c2 = self.denial_of([self.unpaid_part(la)])
                c2 = c2 if k2 == "DENIED" and c2 != "none" else carc
                wend = self.window_end(prod, c2, la.p.date, s.dos)
                return [("DENIED", "resent_and_denied_again", c2, fam, la.lk, la.p.date, owed, basis, wend,
                         wend >= self.as_of)] * rest
            if self.answers_after(s.bill, nd, nfam):
                st, sub = "NEVER_ANSWERED", "answered_without_this_code"
            elif rejected:
                wend = self.window_end(prod, carc, g.p.date, s.dos)
                wend = max(wend, add_days(s.dos, 365)) if s.dos else wend
                return [("DENIED", "resend_rejected_before_payer", carc, fam, g.lk, g.p.date, owed, basis, wend,
                         wend >= self.as_of)] * rest
            else:
                waited = days_between(nd, self.feed_date)
                st = "WORKED_WAITING"
                sub = "resent_overdue" if waited > self.usual_days(nfam or fam) else "resent_within_usual_time"
            wend = self.window_end(prod, carc, nd, s.dos)
            wend = max(wend, add_days(s.dos, 365)) if s.dos else wend
            return [(st, sub, carc, fam, g.lk, g.p.date, owed, basis, wend, wend >= self.as_of)] * rest
        wend = self.window_end(prod, carc, g.p.date, s.dos)
        ew = self.ema_work(ef, fam, g.p.date)
        if ew:
            return [("WORKED_WAITING", ew, carc, fam, g.lk, g.p.date, owed, basis, wend, wend >= self.as_of)] * rest
        return [("DENIED", sub, carc, fam, g.lk, g.p.date, owed, basis, wend, wend >= self.as_of)] * rest

    @staticmethod
    def is_surgical(code):
        return len(code) == 5 and code.isdigit() and "10000" <= code <= "69999"

    def mppr_eligible_lines(self, p, own_lines, code):
        # A16 fix 5(f): an add-on code is never reduced, and an add-on beside it does not make it a second procedure
        if not self.is_surgical(code) or code in ADDON_CODES:
            return False
        return any(L.lk not in own_lines and self.is_surgical(L.code) and L.code not in ADDON_CODES for L in p.lines)

    def mppr_eligible(self, A, code):
        """Half the rate is a multiple-procedure reduction only for a surgical code when the same processing has
        another surgical line (another code, or another line of the same code: a second lesion). A lone procedure
        paid at half its rate is short. A16: add-on codes neither are reduced nor make another code a second one."""
        if not self.is_surgical(code) or code in ADDON_CODES:
            return False
        own = {L.lk for L in A.lines} if A.lines else set()
        return any(L.lk not in own and self.is_surgical(L.code) and L.code not in ADDON_CODES for L in A.p.lines)

    def compute_truth(self):
        for s in self.svcs.values():
            s.units = self.truth(s)
            if len(s.units) != s.U:
                raise AssertionError("unit count mismatch")

    def link_patients(self):
        """A unit not paid on its own bill is paid when the same patient's same service (code, component, date) stands
        paid on another bill (a new bill instead of a resubmission, a crossover filed apart). A16 fix 3: a global unit is
        paid when both the 26 and the TC of the same patient, code and day stand paid (any bill; a global denied for a
        split jurisdiction and rebilled as 26 and TC to two contractors), and a 26 or TC unit when the global stands paid
        (any bill: the component claim then duplicates the global)."""
        idx = defaultdict(set)
        for s in self.svcs.values():
            for pk in s.pks:
                idx[(pk, s.code, s.comp, s.dos)].add(s.key)
        linked_subs = ("paid_on_other_bill", "paid_by_26_and_tc", "paid_as_global")

        def best_paid(keys, exclude):
            best, n_paid = None, 0
            for o in keys:
                if o == exclude[0]:
                    continue
                so = self.svcs[o]
                if exclude[1] and so.bill == exclude[1]:
                    continue
                paid = [u for u in so.units if u[0] in ("PAID", "SHORT") and u[1] not in linked_subs]
                if len(paid) > n_paid:
                    best, n_paid = paid[0], len(paid)
            return best, n_paid

        n = Counter()
        for s in self.svcs.values():
            todo = [i for i, u in enumerate(s.units) if u[0] in LINKABLE]
            if not todo or not s.pks or not s.dos:
                continue
            def keys(comp):
                out = set()
                for pk in s.pks:
                    out |= idx[(pk, s.code, comp, s.dos)]
                return out
            best, n_paid = best_paid(keys(s.comp), (s.key, s.bill))
            sub = "paid_on_other_bill"
            if not n_paid and s.comp == "G":
                b26, n26 = best_paid(keys("26"), (s.key, None))
                btc, ntc = best_paid(keys("TC"), (s.key, None))
                if n26 and ntc:
                    best, n_paid, sub = b26, min(n26, ntc), "paid_by_26_and_tc"
            elif not n_paid and s.comp in ("26", "TC"):
                best, n_paid = best_paid(keys("G"), (s.key, None))
                sub = "paid_as_global"
            if not n_paid:
                continue
            for i in todo[:n_paid]:
                s.units[i] = ("PAID", sub, "", best[3], best[4], best[5], 0, "", "", False)
                n[sub] += 1
        self.stats["units_paid_on_other_bill"] = n["paid_on_other_bill"]
        self.stats["units_paid_by_26_and_tc"] = n["paid_by_26_and_tc"]
        self.stats["units_paid_as_global"] = n["paid_as_global"]

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
                # A16 fix 11: a copy's line is the kept copy's line with the same code, modifiers, charge, units and
                # date (A15 paired them by position, so a copy listing its lines in another order pointed elsewhere)
                used = set()
                for L in p.lines:
                    sig = (L.code, tuple(sorted(L.mods)), L.charge, L.ub, L.dos)
                    twin = None
                    for K in kept.lines:
                        if K.lk in used:
                            continue
                        if (K.code, tuple(sorted(K.mods)), K.charge, K.ub, K.dos) == sig:
                            twin = K
                            break
                    if twin is None:
                        for K in kept.lines:
                            if K.lk not in used and K.code == L.code and K.dos == L.dos and K.comp == L.comp:
                                twin = K
                                break
                    if twin is not None:
                        used.add(twin.lk)
                        line_twin[L.lk] = twin
        for f in R.findings:
            (fid, sig, cat, act, status, lk, ck, tk, pcn, pname, code, mods, sdate, pdate, charge, paid, expected,
             shortfall, carc, evidence, deadline, first_seen, recovered, wo, rec_date, st_changed, last_seen) = f
            # the day RemitWatch closed it: its event to resolved, else its status change, else the run that first saw
            # it (an item created already resolved, on a payment that came before it)
            ev_closed = [iso(e[1]) for e in events.get(fid, []) if (e[4] or "") in ("resolved", "lost", "dismissed")]
            closed_on = max([d for d in ev_closed if d] or [iso(st_changed) or iso(first_seen) or ""])
            it = {"fid": fid, "sig": sig, "cat": cat, "status": status, "parts": [], "kind": "", "amount": shortfall,
                  "recovered": recovered, "first_seen": first_seen or "", "note": "", "line": None, "proc": None,
                  "ans": None, "closed_on": closed_on}
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
                    sv0 = cands[0]
                    # A16 fix 11: an evidence line claims the units of the billed line it names (its charge picks the
                    # line when the claim billed several), not one unit
                    try:
                        lch = int(round(float(ln[2]))) if len(ln) > 2 and ln[2] not in (None, "") else None
                    except (TypeError, ValueError):
                        lch = None
                    u_line = 0
                    if lch:
                        u_line = max([x[4] for x in sv0.sends if x[6] == "837" and x[5] == lch]
                                     + [x[1] for x in sv0.ema if x[2] == lch] + [0])
                        if not u_line and sv0.charge_pu:
                            u_line = int(round(lch / sv0.charge_pu)) if lch >= sv0.charge_pu else 1
                    cnt[sv0.key] += u_line or sv0.U
                it["parts"] = [(self.svcs[k], min(n, self.svcs[k].U)) for k, n in cnt.items()]
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
        # A16 fix 12: a unit that is not payable is done for an underpayment item too
        if u[0] in ("MOVED_TO_PATIENT", "SETTLED_SECONDARY", "VOIDED", "ZERO_CHARGE", "NOT_PAYABLE"):
            return True
        if u[0] == "PAID" and u[1] in ("paid_on_other_bill", "paid_by_26_and_tc", "paid_as_global"):
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
        sec_items = {}              # fid -> the secondary's share owed (calibration round 2)
        self.worked_on_secondary = set()
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
                sh = self.sec_share_item(it) if kind == "D" else None
                if sh:
                    sec_items[it["fid"]] = sh
                    continue
                pre[it["fid"]] = "ALREADY_DONE"
                continue
            owed_states = [s_ for s_ in states if s_ in OWED]
            if kind in ("D", "N") and owed_states and all(s_ == "WORKED_WAITING" for s_ in owed_states) and it["todo"]:
                pre[it["fid"]] = "ALREADY_WORKED"
            elif kind == "D" and it["todo"] and self.secondary_worked(it):
                # A16 fix 4: an item on a secondary payer's own denial, resent to that payer after it: worked (it does
                # not hold the primary's units)
                pre[it["fid"]] = "ALREADY_WORKED"
                self.worked_on_secondary.add(it["fid"])
        opens = {it["fid"] for it in self.items
                 if it["is_open"] and it["kind"] in ("D", "U", "N", "T") and it["parts"] and it["fid"] not in pre
                 and it["fid"] not in sec_items}
        # the secondary's share on a service is held by one item (the keeper); the others on it are duplicates
        sec_keeper = {}
        for it in sorted([x for x in self.items if x["fid"] in sec_items], key=keeper_rank):
            for sv, k in it["parts"]:
                sec_keeper.setdefault(sv.key, it["fid"])
        self.worked_holder = {}
        for it in self.items:   # an item already worked still covers its owed units (they are not left on the table)
            if pre.get(it["fid"]) == "ALREADY_WORKED" and it["fid"] not in self.worked_on_secondary:
                for sv, k in it["parts"]:
                    for i, u in enumerate(sv.units):
                        if u[0] in OWED:
                            taken[sv.key].add(i)
                            self.worked_holder.setdefault((sv.key, i), it)
        # A16 fix 12: open TAKEBACK items of one payer and bill are compared, together, with the bill's net take-back
        self.t_sum = defaultdict(int)
        self.t_n = Counter()
        for it in self.items:
            if it["kind"] == "T" and it["is_open"] and it.get("bill"):
                self.t_sum[(it["fam"], it["bill"])] += it["amount"] or 0
                self.t_n[(it["fam"], it["bill"])] += 1
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
            elif it["is_open"] and fid in sec_items:
                truth_c = sec_items[fid]
                others = [sec_keeper[sv.key] for sv, k in it["parts"] if sec_keeper.get(sv.key) not in (None, fid)]
                amt = it["amount"]
                if others:
                    v, sub, keeper = "DUPLICATE", "secondary_share_on_another_open_item", others[0]
                elif self.secondary_worked(it):
                    v, sub = "ALREADY_WORKED", "secondary_share_resent"
                elif amt is None:
                    v, sub = "CORRECT", "secondary_share;item_has_no_amount"
                elif amt > truth_c + max(100, 0.05 * truth_c):
                    v, sub = "WRONG_AMOUNT", "over_secondary_share"
                else:
                    v, sub = "CORRECT", "secondary_share"
            elif it["is_open"]:
                if fid in pre:
                    v = pre[fid]
                    sub = ",".join(sorted(states))
                else:
                    got_units = [sv.units[i] for sv, k in it["parts"] for i in alloc[fid].get(sv.key, [])]
                    k_got = len(got_units)
                    if k_got == 0:
                        keeper = self.keeper_of(it, on_svc, keeper_rank)
                        if keeper:
                            v, sub = "DUPLICATE", "all_units_on_another_open_item"
                        else:
                            # A16 fix 12: no other item holds a unit: the item is judged on its units' own states
                            allst = [u[0] for sv, k in it["parts"] for u in sv.units]
                            if allst and all(x_ in DONE or x_ == "SHORT" for x_ in allst):
                                v, sub = "ALREADY_DONE", ",".join(sorted(states))
                            else:
                                v, sub = "UNDECIDED", "no_unit_left_to_claim"
                    elif kind == "T":
                        net = self.plb_net.get((it["fam"], it["bill"]), 0)
                        truth_c = net
                        owed_units = [u for u in got_units if u[0] in OWED]
                        tsum = self.t_sum.get((it["fam"], it["bill"]), 0)
                        tn = self.t_n.get((it["fam"], it["bill"]), 1)
                        if not owed_units:
                            v, sub = "UNDECIDED", "unit_" + Counter(u[0] for u in got_units).most_common(1)[0][0].lower()
                        elif net > 0 and it["amount"] is not None and abs(it["amount"] - net) > 1 and \
                                abs(tsum - net) > tn and not any(abs(e[1] - it["amount"]) <= 1 for e in
                                                                 self.plb_ents.get((it["fam"], it["bill"]), [])):
                            v, sub = "WRONG_AMOUNT", "takeback_net_differs"
                        elif net > 0 and it["amount"] is not None and abs(it["amount"] - net) > 1 and \
                                abs(tsum - net) > tn:
                            # A16 fix 12: the item is one PLB entry of the bill, stated exactly; the bill's other entries
                            # have no item (a completeness gap of the bill, not this item's amount)
                            v, sub = "CORRECT", "takeback_entry_matches;bill_has_entries_without_item"
                        else:
                            v, sub = "CORRECT", ("" if net > 0 else "takeback_netted_on_plb_units_owed") + \
                                (";items_sum_to_net" if tn > 1 and abs(tsum - net) <= tn else "")
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
                elif it["person_decision"] and (it["recovered"] or 0) > 0 and kind in ("D", "N") and all(
                        sum(1 for u in sv.units if u[0] in ("PAID", "SHORT")) >= min(k, sv.U) for sv, k in it["parts"]):
                    # A16 fix 13: a person closed it with a recovered amount on paid units: is that the payment?
                    v, sub, truth_c = self.judge_recovery(it)
                    sub = (sub + ";" if sub else "") + "closed_by_person"
                else:
                    v, sub = "CORRECT", "closed_by_person" if it["person_decision"] else "closed_other"
            self.verdicts[fid] = {"verdict": v, "sub": sub, "truth": truth_c, "k_claim": k_claim, "k_got": k_got,
                                  "keeper": keeper, "states": dict(states)}
        self.taken = taken

    def secondary_worked(self, it):
        """An item whose own answer is a secondary payer's (another payer than the bill's primary, or a CLP02 2/3), with
        a send to that same payer after it (any claim id of the bill)."""
        A0 = it.get("ans")
        p0 = A0.p if A0 is not None else it.get("proc")
        if p0 is None or (A0 is not None and not A0.standing) or p0.cancelled_by is not None:
            return False
        P = self._pfam.get(p0.bill)
        if not ((P is not None and p0.fam != P) or p0.st in SECONDARY):
            return False
        for sv, k in it["parts"]:
            if any(x[0] > p0.date and x[2] != "8" and x[1] == p0.fam for x in sv.sends):
                return True
        return False

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
        if all(u[0] == "SHORT" and u[1] == "below_weak_rate" for u in owed_units):
            # A16 fix 5: a payer mode under half of its answers (several amounts in play) cannot tell short from paid
            return "UNDECIDED", "payer_rate_too_weak_to_judge", None
        if amt is None:
            sub = "item_has_no_amount"
        elif abs(amt - truth_c) <= max(1, k_claim):
            sub = ""
        elif all(not self.firm_basis(u[7]) for u in owed_units):
            # A16 fix 12: the truth is itself an estimate (the charge, or a rate borrowed from another product or
            # setting, or a payer-wide rate for a never-answered claim): only an amount above the charge is wrong
            charge_cap = sum(sv.charge_pu * k for sv, k in it["parts"])
            if charge_cap and amt > charge_cap + 1:
                return "WRONG_AMOUNT", "over_billed_charge", truth_c
            sub = "amount_unverified_no_rate" if all(u[7] == "charge" for u in owed_units) else "amount_unverified_estimate"
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

    @staticmethod
    def firm_basis(b):
        return bool(b) and (b.startswith("rate_") or b in ("line_allowed", "taken", "paid_unit",
                                                           "payer_does_not_pay_this_code"))

    def keeper_of(self, it, on_svc, keeper_rank):
        for sv, k in it["parts"]:
            others = [o for o in on_svc[sv.key] if o is not it and self.alloc.get(o["fid"], {}).get(sv.key)]
            if others:
                return sorted(others, key=keeper_rank)[0]["fid"]
        # A16 fix 12: an item already worked holds its units too
        for sv, k in it["parts"]:
            for i in range(sv.U):
                h = getattr(self, "worked_holder", {}).get((sv.key, i))
                if h is not None and h is not it:
                    return h["fid"]
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
        """Closed as paid on a paid unit: is the recovered amount the money paid for the item's own units after its
        answer? A16 fix 12: every payer's payment counts (a supplement's or a secondary's too), and the item's own line
        when the payer answered it (same modifiers, charge and units), not an average over the answer's lines."""
        if it["counted_elsewhere"] or it["recovered"] is None:
            return "CORRECT", "recovery_counted_elsewhere" if it["counted_elsewhere"] else "", None
        p = it["proc"]
        after = p.date if p is not None else ""
        L0 = it["line"]
        sig0 = (tuple(sorted(L0.mods)), L0.charge, L0.ub) if L0 is not None else None
        total = 0.0
        k_all = 0
        for sv, k in it["parts"]:
            k_all += k
            # every payer's payment after the item's answer, up to the day the item was closed (a later secondary
            # payment is not part of what the closure recorded)
            pays = [A for A in sv.answers if A.standing and A.p.st != REVERSAL and A.paid_tb > 0 and A.p.date >= after
                    and A is not it["ans"]]
            upto = it.get("closed_on") or ""
            if pays and upto >= min(A.p.date for A in pays):
                pays = [A for A in pays if A.p.date <= upto]
            if not pays:
                linked = [u for u in sv.units if u[1] in ("paid_on_other_bill", "paid_by_26_and_tc", "paid_as_global")]
                if linked:
                    G = self.gov_answer(linked[0])
                    if G is not None:
                        pays = [G]
            for A in pays:
                own = []
                if sig0 is not None and not A.split and len(A.lines) > 1:
                    own = [L for L in A.lines if L.paid_tb > 0 and (tuple(sorted(L.mods)), L.charge, L.ub) == sig0]
                if own and own[0].ub >= k:
                    total += own[0].paid_tb * k / max(1, own[0].ub)
                else:
                    use = min(k, A.up or 1)
                    total += A.paid_tb * use / max(1, A.up or 1)
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
        self.review_units = []
        self.weak_units = []
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
                if "provider_discount" in u[1]:
                    # owner rule 4.1: an unexplained provider-level discount is listed once per payer and provider
                    self.review_units.append((sv, i, u))
                    continue
                if u[6] <= PLACEHOLDER_CENTS:
                    # calibration round 2: a unit worth $0.01 (the payer's own rate is a placeholder) is no money left
                    continue
                if u[1] == "below_weak_rate":
                    # A16 fix 5: below a weak payer mode (under half of its answers): not counted as money left
                    self.weak_units.append((sv, i, u))
                    continue
                self.lot.append((sv, i, u))
        self.secondary_balances()

    def secondary_balances(self):
        """Section 5 of the calibration (reported apart; not a unit state, and no verdict depends on it): the patient
        share (PR-1/2/3) the bill's primary payer left on a service it paid or allowed, denied by a secondary payer and
        settled by nobody: no secondary payment (835 or EMA), no secondary allowance to the patient, no 'prior payer paid
        more' answer (CARC 23 or the secondary's own CO-45 alone), no patient payment in EMA. One row per service:
        (service, share cents, secondary family, CARC, an open denial item on the service?)."""
        self.sec_bal = []
        held = set()
        for it in self.items:
            if it["is_open"] and it["kind"] == "D":
                for sv, k in it["parts"]:
                    held.add(sv.key)
        for key, sv in self.svcs.items():
            sb = self.sec_balance_of(sv)
            if sb is not None:
                self.sec_bal.append((sv, sb[0], sb[1].p.fam, sb[2], key in held))

    def sec_balance_of(self, sv):
        """The secondary's balance on one service: (patient share cents, the secondary's latest answer, its CARC, the
        primary's governing answer), or None (see secondary_balances)."""
        P = self._pfam.get(sv.bill)
        if P is None or not sv.units or not all(u[0] in ("PAID", "MOVED_TO_PATIENT") for u in sv.units):
            return None
        prim = [A for A in sv.answers if A.standing and A.p.fam == P and A.p.st in PRIMARY and
                (A.paid_tb > 0 or any(g_ == "PR" and r in ("1", "2", "3") and a > 0 for g_, r, a in A.cas))]
        secs = [A for A in sv.answers if A.standing and A.p.st != REVERSAL and A.p.fam != P]
        if not prim or not secs:
            return None
        g0 = max(prim, key=lambda A: A.p.ordk)
        share = sum(a for g_, r, a in g0.cas if g_ == "PR" and r in ("1", "2", "3") and a > 0)
        if share <= 100:
            return None
        if any(A.paid > 0 for A in secs) or \
                any(g_ == "PR" and r in ("1", "2", "3") and a > 0 for A in secs for g_, r, a in A.cas):
            return None
        last = max(secs, key=lambda A: A.p.ordk)
        keep = self._pr_allowed
        kind, carc = self.denial_of([last])
        self._pr_allowed = keep
        if kind != "DENIED" or carc == "none":
            return None
        ef = self.ema_facts(sv)
        if ef is not None:
            if ef["pat_paid"] >= share - 100:
                return None
            exc, f_, d_ = self.ema_paid_excess(sv, ef)
            if exc >= 0.5 * share:
                return None
        return share, last, carc, g0

    def sec_share_item(self, it):
        """A16 fix 4, the secondary's state (calibration round 2): an open denial item on a secondary payer's own answer,
        on services the primary allowed wholly to the patient's share and paid nothing on (a deductible), with that share
        denied by the secondary and settled by nobody: the share is owed by the secondary (the item is not done).
        Returns the share in cents for the item's units, or None. (When the primary paid, the item as framed is done and
        the residual is reported with the secondary balances.)"""
        A0 = it.get("ans")
        p0 = A0.p if A0 is not None else it.get("proc")
        if p0 is None or p0.cancelled_by is not None or p0.copy_of is not None:
            return None
        if A0 is not None and (not A0.standing or A0.paid > 0):
            return None
        P = self._pfam.get(p0.bill)
        if not ((P is not None and p0.fam != P) or p0.st in SECONDARY):
            return None
        total = 0
        for sv, k in it["parts"]:
            if not sv.units or not all(u[0] == "MOVED_TO_PATIENT" for u in sv.units):
                return None
            if any(B.p is p0 and B.paid > 0 for B in sv.answers):
                return None
            sb = self.sec_balance_of(sv)
            if sb is None or sb[3].paid_tb > 0 or sb[3].paid > 0:
                return None
            total += int(round(sb[0] * min(k, sv.U) / max(1, sv.U)))
        return total or None

    # ------------------------------------------------------------------ run
    def run(self):
        steps = [("processings", self.build_processings), ("dedupe", self.dedupe), ("reversals", self.pair_reversals),
                 ("takebacks", self.apply_takebacks), ("services", self.build_services),
                 ("claim_level", self.attach_claim_level), ("answers", self.build_answers),
                 ("timing", self.payer_timing), ("units_scope", self.units_and_scope),
                 ("units_from_bills", self.units_from_bills), ("rates", self.build_rates), ("ema_match", self.ema_match),
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
    # A16: owed units below a weak payer mode (not counted as left on the table)
    wk = Counter()
    for sv, i, u in getattr(a, "weak_units", []):
        k = "in_scope" if sv.in_scope else "out_of_scope"
        wk[(k, "units")] += 1
        wk[(k, "cents")] += u[6]
    out["below_weak_rate_not_counted"] = {f"{k[0]}|{k[1]}": v for k, v in sorted(wk.items())}
    # section 5, reported apart: secondary balances (patient share left by the primary, denied by a secondary)
    sb = Counter()
    for sv, share, fam, carc, held in getattr(a, "sec_bal", []):
        k = ("in_scope" if sv.in_scope else "out_of_scope", "with_open_item" if held else "no_open_item")
        sb[k + ("services",)] += 1
        sb[k + ("cents",)] += share
    out["secondary_balances"] = {"|".join(k): v for k, v in sorted(sb.items())}
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
        open_items TEXT, closed_items TEXT, left_on_table INTEGER, primary_fam TEXT, ema_how TEXT, ema_links TEXT,
        ema_ins_paid INTEGER, ema_status TEXT, review INTEGER)""")
    con.execute("""create table verdicts (finding_id TEXT PRIMARY KEY, signal TEXT, category TEXT, status TEXT, kind TEXT,
        verdict TEXT, sub TEXT, item_cents INTEGER, truth_cents INTEGER, recovered_cents INTEGER, units_claimed INTEGER,
        units_allocated INTEGER, unit_states TEXT, keeper TEXT, in_scope INTEGER, line_key TEXT, claim_key TEXT,
        svc_keys TEXT, note TEXT)""")
    lot_set = {(sv.key, i) for sv, i, u in a.lot}
    rev_set = {(sv.key, i) for sv, i, u in a.review_units}
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
            ef = a.ema_facts(sv) if sv.eml else None
            rows.append((sv.bill, sv.code, sv.comp, sv.dos, sv.U, i + 1, j + 1, u[0], u[1], u[2], u[3], u[4], u[5], u[6],
                         u[7], u[8], 1 if u[9] else 0, 1 if sv.in_scope else 0, sv.scope_basis,
                         ",".join(open_on.get(key, [])), ",".join(closed_on.get(key, [])), 1 if lot_i else 0,
                         a._pfam.get(sv.bill) or "", sv.ema_how, ",".join(str(E.link) for E in sv.eml),
                         ef["paid"] if ef else None, ",".join(sorted(set(ef["status"].values()))) if ef else "",
                         1 if (key, i) in rev_set else 0))
            i = j + 1
    con.executemany("insert into units values (" + ",".join("?" * 28) + ")", rows)
    # owner rule 4.1: unexplained provider-level discounts, one row per payer and provider (NPI's last 3 digits only)
    con.execute("""create table provider_reviews (fam TEXT, npi_tail TEXT, lines INTEGER, units INTEGER, gap_cents INTEGER,
        units_in_scope_inside INTEGER, gap_cents_in_scope_inside INTEGER)""")
    rv_scope = defaultdict(lambda: [0, 0])
    for sv, i, u in a.review_units:
        if sv.in_scope:
            g = None
            for A in sv.answers:
                if A.lk == u[4]:
                    g = A
                    break
            npi = (g.npi or "") if g is not None else ""
            x = rv_scope[(u[3], npi)]
            x[0] += 1
            x[1] += u[6]
    con.executemany("insert into provider_reviews values (?,?,?,?,?,?,?)",
                    [(f, (n or "")[-3:], v[0], v[1], v[2], rv_scope[(f, n)][0], rv_scope[(f, n)][1])
                     for (f, n), v in sorted(a.review.items())])
    # section 5, reported apart: secondary balances (keys of the store only; private result folder)
    con.execute("""create table secondary_balances (bill TEXT, code TEXT, comp TEXT, dos TEXT, share_cents INTEGER,
        secondary_fam TEXT, carc TEXT, open_item INTEGER, in_scope INTEGER)""")
    con.executemany("insert into secondary_balances values (?,?,?,?,?,?,?,?,?)",
                    [(sv.bill, sv.code, sv.comp, sv.dos, share, fam, carc, 1 if held else 0, 1 if sv.in_scope else 0)
                     for sv, share, fam, carc, held in getattr(a, "sec_bal", [])])
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
  units REAL, policy_number TEXT, activity_type TEXT, insurance_payments_cents INTEGER, payments_cents INTEGER,
  payment_method TEXT, carc TEXT);
create table names (id INTEGER PRIMARY KEY, name TEXT);
create table postings (src INTEGER, posted TEXT, svc TEXT, cpt TEXT, units REAL, link INTEGER, check_no TEXT, grp TEXT,
  code TEXT, kind INTEGER, status INTEGER, action INTEGER, payer INTEGER, plan INTEGER, primary_payer INTEGER,
  secondary_payer INTEGER, pccn TEXT, person INTEGER, name_key INTEGER, charges INTEGER, ins_charges INTEGER,
  ins_paid INTEGER, pat_paid INTEGER, contractual INTEGER, noncontractual INTEGER, pat_adj INTEGER, allowed INTEGER,
  flags INTEGER);
create table ema_link_map (link INTEGER PRIMARY KEY, bill TEXT, claim_id TEXT, method TEXT, votes INTEGER, total INTEGER,
  conflict INTEGER, mapped_at TEXT, facility INTEGER);
create table ema_claim_links (claim_id TEXT, link INTEGER, bill TEXT, first_seen TEXT, last_seen TEXT);
create table ema_checks (check_no TEXT, payer TEXT, payer_group TEXT, match TEXT, trace_key TEXT);
create table ema_ar (link INTEGER, svc TEXT, cpt TEXT, claim_status TEXT, note_kind TEXT, note_date TEXT, follow_up TEXT,
  balance_cents INTEGER, denial_cents INTEGER, party TEXT, claim_action TEXT, coverage TEXT);
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
        self.expect_sub = {}      # (bill, code, comp, dos) -> (state, sub) of unit 1 (A16)
        self.expect_lot = {}      # (bill, code, comp, dos) -> left on the table? (A16)
        self.expect_review = {}   # (bill, code, comp, dos) -> on the payer x provider review list? (A16, rule 4.1)
        self.expect_secbal = {}   # (bill, code, comp, dos) -> reported as a secondary balance? (A16, section 5)
        self.expect_owed = {}     # (bill, code, comp, dos) -> owed cents per unit of unit 1 (A16)
        self.fix_of = {}          # check -> the A16 fix it tests (checks absent here are A15's own)
        self.fix = None           # the fix the cases being written test

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
                              ln.get("paid_units", units if lpaid else 0), "HC" if ln.get("ocode") else None,
                              ln.get("ocode"), json.dumps(ln["omods"]) if ln.get("omods") else None,
                              ln.get("orig_units", units if not lpaid else None), dos, None, allowed,
                              ln.get("npi", "1000000001")))
            for g, r, a in ln.get("cas", []):
                self.con.execute("insert into adjustments (claim_key, line_key, level, group_code, reason_code, "
                                 "amount_cents) values (?,?,?,?,?,?)", (ck, lk, "line", g, r, a))
            keys.append(lk)
        return ck, keys

    def send837(self, pcn, bht, lines, freq="1", payer="TESTPAY ONE", pid="T0001", sub=None):
        k = self.key("c8")
        self.con.execute("insert into claims_837 values (?,?,?,?,?,?,?,?,?,?)",
                         (k, bht, pcn, sum(l_["charge"] for l_ in lines), freq, payer, pid, lines[0]["dos"], "11", sub))
        for i, ln in enumerate(lines):
            self.con.execute("insert into lines_837 values (?,?,?,?,?,?,?,?)",
                             (self.key("l8"), k, i + 1, ln["code"], ",".join(ln.get("mods", [])) or None, ln["charge"],
                              ln.get("units", 1), ln["dos"]))

    # ---- A16: EMA's books (the Production ledger, the link map, the check match, the A/R), invented rows only
    def name_id(self, name):
        if name is None:
            return None
        r = self.con.execute("select id from names where name=?", (name,)).fetchone()
        if r:
            return r[0]
        self.con.execute("insert into names (name) values (?)", (name,))
        return self.con.execute("select id from names where name=?", (name,)).fetchone()[0]

    def ema_line(self, link, svc, cpt, rows, bill=None, person=7001, status="Resolved/Paid", action=None, payer=None):
        """EMA claim line (link, svc, cpt): rows are (days after svc, kind, amount, payer, extra) with kind one of
        charge, pay (an insurance payment; extra = check number), answer (a $0 payer row; extra = check), adj
        (extra = (group, carc, non-contractual?)), patpay."""
        for i, (dd, kind, amt, pyr, extra) in enumerate(rows):
            posted = add_days(svc, dd)
            vals = dict(src=2, posted=posted, svc=svc, cpt=cpt, units=1, link=link, check_no=None, grp=None, code=None,
                        status=self.name_id(status), action=self.name_id(action), payer=self.name_id(pyr or payer),
                        primary_payer=self.name_id(payer or pyr), person=person, charges=0, ins_paid=0, pat_paid=0,
                        contractual=0, noncontractual=0, pat_adj=0)
            if kind == "charge":
                vals["charges"] = amt
            elif kind == "pay":
                vals["ins_paid"], vals["check_no"] = amt, extra
            elif kind == "answer":
                vals["check_no"] = extra
            elif kind == "adj":
                g, c, nonc = extra
                vals["grp"], vals["code"] = g, c
                vals["noncontractual" if nonc else "contractual"] = amt
            elif kind == "patpay":
                vals["link"] = None
                vals["pat_paid"] = amt
            cols = ",".join(vals)
            self.con.execute(f"insert into postings ({cols}) values ({','.join('?' * len(vals))})", tuple(vals.values()))
        if bill:
            self.con.execute("insert or replace into ema_link_map (link, bill, method, conflict, facility) values "
                             "(?,?,?,?,?)", (link, bill, "payment", 0, 0))

    def exp(self, kind, key, value, guard=False):
        """guard=True: a regression guard for an A16 refinement where A15 happens to give the right answer (it must
        pass on A16; it is not required to fail on A15)."""
        d = {"unit": self.expect_unit, "item": self.expect_item, "sub": self.expect_sub, "lot": self.expect_lot,
             "counts": self.expect_unit_counts, "billed": self.expect_units_billed, "review": self.expect_review,
             "secbal": self.expect_secbal, "owed": self.expect_owed}[kind]
        d[key] = value
        self.fix_of[(kind, key)] = self.fix + (" [guard]" if guard else "")

    def ema_check(self, check_no, payer, match="none", tk=None):
        self.con.execute("insert into ema_checks (check_no, payer, match, trace_key) values (?,?,?,?)",
                         (check_no, payer, match, tk))

    def ema_ar(self, link, svc, cpt, status, note_kind=None, follow_up=None, coverage="Primary", denial=0):
        self.con.execute("insert into ema_ar (link, svc, cpt, claim_status, note_kind, follow_up, balance_cents, "
                         "denial_cents, party, coverage) values (?,?,?,?,?,?,?,?,?,?)",
                         (link, svc, cpt, status, note_kind, follow_up, 0, denial, "Insurance", coverage))

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
    # ================================================================================================ A16 test cases
    # Each case is built from invented data only (ZZ claim ids, TESTPAY payers, keys and amounts made up) and fails on
    # A15 (the check is labelled with the fix it tests; selftest_vs_a15.py runs A15 on the same store).
    T1 = "TESTPAY ONE"

    def oneline(code, charge, paid, dos, **kw):
        return [L_(code, charge, paid, dos, **kw)]
    # ---- fix 1: EMA's Production ledger and A/R are truth sources
    F.fix = "fix 1 (EMA books)"
    # 1a never answered in the 835s; EMA posted the payer's payment from a check that is no 835 on file -> paid
    F.send837("ZZ60EMAP1C017", "2026-07-01", oneline("99213", 15000, 0, "2026-06-25"))
    F.ema_line(900001, "2026-06-25", "99213", [(0, "charge", 15000, T1, None), (30, "pay", 8000, T1, "CHKOFF1"),
                                               (30, "adj", 5000, T1, ("CO", "45", False))], bill="ZZ60EMAP1")
    F.ema_check("CHKOFF1", T1, "none")
    fid = F.finding("NO_ANSWER", "denial", pcn="ZZ60EMAP1C017", shortfall=10000,
                    evidence={"lines": [["99213", "2026-06-25", "15000"]]})
    F.exp("item", fid, "ALREADY_DONE")
    F.exp("sub", ("ZZ60EMAP1", "99213", "G", "2026-06-25"), ("PAID", "ema_paid_off_feed"))
    # 1b a secondary's 835 denial (CO-B7) after the primary paid off-feed (EMA): paid, the denial item is done
    ck, (l1b,) = F.claim(F.tx("2026-10-01", payer="TESTPAY TWO", pid="T0002"), "ZZ61SECD1C017", "4", 15000, 0,
                         oneline("99213", 15000, 0, "2026-08-21", cas=[("CO", "B7", 15000)]), payer="TESTPAY TWO",
                         pid="T0002", prod="MC")
    F.ema_line(900002, "2026-08-21", "99213", [(0, "charge", 15000, "TESTPAY OFF", None),
                                               (20, "pay", 9000, "TESTPAY OFF", "CHKOFF2"),
                                               (20, "adj", 4000, "TESTPAY OFF", ("CO", "45", False)),
                                               (41, "answer", 0, "TESTPAY TWO", "CHK835B")], bill="ZZ61SECD1")
    F.ema_check("CHKOFF2", "TESTPAY OFF", "none")
    fid = F.finding("DENIED_PROVIDER", "denial", line_key=l1b, shortfall=10000)
    F.exp("item", fid, "ALREADY_DONE")
    F.exp("unit", ("ZZ61SECD1", "99213", "G", "2026-08-21"), "PAID")
    # 1c sent, no 835; EMA's line found through the patient (837 subscriber -> 835 member id -> EMA person) shows the
    # payer denied it CO-97 off-feed: denied, not never answered
    F.claim(F.tx("2026-05-20"), "ZZ62PERA1C017", "1", 15000, 8000,
            oneline("99213", 15000, 8000, "2026-05-01", allowed=10000, cas=[("CO", "45", 5000), ("PR", "2", 2000)]),
            patient=("TESTLASTP", "TESTFIRSTP"))
    F.ema_line(900003, "2026-05-01", "99213", [(0, "charge", 15000, T1, None), (19, "pay", 8000, T1, "CHK835C")],
               bill="ZZ62PERA1", person=7101)
    F.send837("ZZ62PERB1C017", "2026-06-02", oneline("11102", 9000, 0, "2026-06-01"), sub="MTESTLASTP")
    F.ema_line(900004, "2026-06-01", "11102", [(0, "charge", 9000, T1, None), (20, "answer", 0, T1, "CHKOFF3"),
                                               (20, "adj", 9000, T1, ("CO", "97", False))], person=7101,
               status="Resolved/Other")
    F.ema_check("CHKOFF3", T1, "none")
    F.exp("sub", ("ZZ62PERB1", "11102", "G", "2026-06-01"), ("DENIED", "ema_off_feed_answer"))
    # 1d EMA shows the claim voided, no 835: voided
    F.send837("ZZ63VOID1C017", "2026-06-03", oneline("99212", 9000, 0, "2026-06-02"))
    F.ema_line(900005, "2026-06-02", "99212", [(0, "charge", 9000, T1, None), (10, "charge", -9000, T1, None)],
               bill="ZZ63VOID1", status="Inactive/Voided")
    F.exp("unit", ("ZZ63VOID1", "99212", "G", "2026-06-02"), "VOIDED")
    # 1e EMA's weekly export: an insurance Payment row that carries its amount only in payments_cents -> paid
    F.send837("ZZ64WKLY1C017", "2026-06-04", oneline("99212", 9000, 0, "2026-05-26"))
    for i, (act, ch, pay) in enumerate((("Charge", 9000, None), ("Payment", 0, 6000))):
        F.con.execute("insert into ema_rows (row_fingerprint, family, source_name, row_index, service_date, claim_id, "
                      "cpt, payer, coverage_type, charges_cents, units, activity_type, payments_cents, payment_method) "
                      "values (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (f"fw{i}", "charge_lines", "exp3", i, "2026-05-26",
                                                               "ZZ64WKLY1C017", "99212", T1, "Primary", ch, 1, act, pay,
                                                               "Payer EFT"))
    F.con.execute("insert into ema_sources values ('exp3', '2026-07-01T00:00:00Z')")
    F.exp("sub", ("ZZ64WKLY1", "99212", "G", "2026-05-26"), ("PAID", "ema_paid_weekly_export"))
    # 1f a payer with no 835 feed: EMA's books show nothing posted long after the send -> never answered, owed
    F.send837("ZZ65NOFD1C017", "2026-06-05", oneline("99213", 15000, 0, "2026-05-27"), payer="NOERA TWO", pid="N0002")
    F.ema_line(900006, "2026-05-27", "99213", [(0, "charge", 15000, "NOERA TWO", None)], bill="ZZ65NOFD1",
               status="Payment Pending")
    F.exp("unit", ("ZZ65NOFD1", "99213", "G", "2026-05-27"), "NEVER_ANSWERED")
    F.exp("lot", ("ZZ65NOFD1", "99213", "G", "2026-05-27"), True)
    # ---- fix 2: units paid when SVC05 and SVC07 are empty: the billed units, when the CAS reconciles the charge
    F.fix = "fix 2 (units from empty SVC05)"
    F.send837("ZZ66UNIT1C017", "2026-09-02", oneline("88305", 18000, 0, "2026-09-01", units=3))
    F.claim(F.tx("2026-09-25"), "ZZ66UNIT1C017", "1", 18000, 12864,
            oneline("88305", 18000, 12864, "2026-09-01", units=3, paid_units=None, orig_units=None, allowed=12864,
                    cas=[("CO", "45", 5136)]))
    F.exp("counts", ("ZZ66UNIT1", "88305", "G", "2026-09-01"), {"PAID": 3})
    # ---- fix 3: a global settled by its 26 and TC paid on other bills; a 26 settled by the paid global
    F.fix = "fix 3 (26/TC/global across bills)"
    pq = ("TESTLASTQ", "TESTFIRSTQ")
    ck, (lg,) = F.claim(F.tx("2026-09-03"), "ZZ67GLOB1C017", "1", 6000, 0,
                        oneline("88305", 6000, 0, "2026-08-03", cas=[("CO", "16", 6000)]), patient=pq)
    F.claim(F.tx("2026-09-10"), "ZZ67PT261C017", "1", 4000, 3000,
            oneline("88305", 4000, 3000, "2026-08-03", mods=["26"], allowed=3000, cas=[("CO", "45", 1000)]), patient=pq)
    F.claim(F.tx("2026-09-12", payer="TESTPAY TC", pid="T0016"), "ZZ67PTTC1C017", "1", 3000, 2500,
            oneline("88305", 3000, 2500, "2026-08-03", mods=["TC"], allowed=2500, cas=[("CO", "45", 500)]),
            patient=pq, payer="TESTPAY TC", pid="T0016")
    fid = F.finding("DENIED_MISSING_INFO", "denial", status="resolved", line_key=lg, shortfall=6000, recovered=3000,
                    evidence={"paid_later": {"paid_cents": 3000}}, events=[("paid_on_later_remit", "resolved", "system")])
    F.exp("item", fid, "CORRECT")
    F.exp("sub", ("ZZ67GLOB1", "88305", "G", "2026-08-03"), ("PAID", "paid_by_26_and_tc"))
    pr_ = ("TESTLASTR", "TESTFIRSTR")
    ck, (l26,) = F.claim(F.tx("2026-09-20"), "ZZ68COMP1C017", "1", 4000, 0,
                         oneline("88305", 4000, 0, "2026-08-04", mods=["26"], cas=[("CO", "234", 4000)]), patient=pr_)
    F.claim(F.tx("2026-09-08"), "ZZ68GLOB1C017", "1", 6000, 6000,
            oneline("88305", 6000, 6000, "2026-08-04", allowed=6000), patient=pr_)
    fid = F.finding("PROCEDURE_BUNDLED", "bundling", line_key=l26, shortfall=4000)
    F.exp("item", fid, "ALREADY_DONE")
    # ---- fix 4: the primary payer's answers govern; a secondary's later CLP02-1 PR-96 does not move the unit
    F.fix = "fix 4 (primary payer governs)"
    ck, (lp1, lp2) = F.claim(F.tx("2026-09-05"), "ZZ69PRIM1C017", "1", 24000, 6000, [
        L_("11102", 9000, 6000, "2026-08-05", allowed=7000, cas=[("CO", "45", 2000), ("PR", "2", 1000)]),
        L_("99213", 15000, 0, "2026-08-05", mods=["25"], cas=[("CO", "97", 15000)])])
    F.claim(F.tx("2026-09-20", payer="TESTPAY TWO", pid="T0002"), "ZZ69PRIM1C017", "1", 24000, 1000, [
        L_("11102", 9000, 1000, "2026-08-05", allowed=1000, cas=[("OA", "23", 8000)]),
        L_("99213", 15000, 0, "2026-08-05", mods=["25"], cas=[("PR", "96", 15000)])], payer="TESTPAY TWO", pid="T0002")
    fid = F.finding("EM_BUNDLED_MOD25", "bundling", line_key=lp2, shortfall=10000)
    F.exp("item", fid, "CORRECT")
    F.exp("unit", ("ZZ69PRIM1", "99213", "G", "2026-08-05"), "DENIED")
    # ---- fix 5: the payer's own rate (full-rate lines, no cross-product fallback, charge cap, B6, unlisted, add-ons)
    F.fix = "fix 5 (rate evidence)"
    TM = dict(payer="TESTPAY MOHS", pid="T0006")
    for i in range(5):     # the full rate: 17311 alone
        F.claim(F.tx("2026-0%d-1%d" % (3 + i, i), **TM), "ZZ70MA%02dXC017" % i, "1", 90000, 50000,
                [L_("17311", 90000, 50000, "2026-02-1%d" % i, allowed=60000, npi="1000000002",
                    cas=[("CO", "45", 30000), ("PR", "2", 10000)])], **TM)
    for i in range(10):    # 17311 beside a higher-valued surgery, paid at half under CO-45 (no 51, no CARC 59)
        F.claim(F.tx("2026-0%d-2%d" % (3 + i % 5, i % 8), **TM), "ZZ70MB%02dXC017" % i, "1", 185000, 65000, [
            L_("11603", 95000, 40000, "2026-02-2%d" % (i % 8), allowed=50000, npi="1000000002",
               cas=[("CO", "45", 45000), ("PR", "2", 10000)]),
            L_("17311", 90000, 25000, "2026-02-2%d" % (i % 8), allowed=30000, npi="1000000002",
               cas=[("CO", "45", 60000), ("PR", "2", 5000)])], **TM)
    F.claim(F.tx("2026-10-10", **TM), "ZZ70MCXXXC017", "1", 90000, 25000,
            [L_("17311", 90000, 25000, "2026-09-20", allowed=30000, npi="1000000002",
                cas=[("CO", "45", 60000), ("PR", "2", 5000)])], **TM)
    F.exp("unit", ("ZZ70MCXXX", "17311", "G", "2026-09-20"), "SHORT")
    TP = dict(payer="TESTPAY PROD", pid="T0007")
    for i in range(6):
        F.claim(F.tx("2026-0%d-0%d" % (3 + i % 5, 2 + i), **TP), "ZZ71PR%02dXC017" % i, "1", 30000, 20000,
                [L_("11402", 30000, 20000, "2026-02-0%d" % (2 + i), allowed=20000, cas=[("CO", "45", 10000)])], **TP)
    F.claim(F.tx("2026-10-11", **TP), "ZZ71HMXXXC017", "1", 30000, 15000,
            [L_("11402", 30000, 15000, "2026-09-21", allowed=15000, cas=[("CO", "45", 15000)])], prod="HM", **TP)
    F.exp("unit", ("ZZ71HMXXX", "11402", "G", "2026-09-21"), "PAID")
    F.claim(F.tx("2026-10-12"), "ZZ72CAPXXC017", "1", 6508, 6462,
            [L_("99213", 6508, 6462, "2026-09-22", allowed=6462, cas=[("CO", "45", 46)])])
    F.exp("unit", ("ZZ72CAPXX", "99213", "G", "2026-09-22"), "PAID")
    TU = dict(payer="TESTPAY UMRX", pid="T0008")
    for i in range(6):
        F.claim(F.tx("2026-0%d-0%d" % (3 + i % 5, 3 + i), **TU), "ZZ73B6%02dXC017" % i, "1", 19353, 9841,
                [L_("99203", 19353, 9841, "2026-02-0%d" % (3 + i), allowed=10935,
                    cas=[("CO", "45", 8418), ("PR", "2", 1094)])], **TU)
    F.claim(F.tx("2026-10-13", **TU), "ZZ73B6XXXC017", "1", 19353, 9841,
            [L_("99203", 19353, 9841, "2026-09-23", allowed=9841, cas=[("CO", "45", 8418), ("PR", "2", 1094)])], **TU)
    F.exp("unit", ("ZZ73B6XXX", "99203", "G", "2026-09-23"), "PAID")
    for i in range(6):
        F.claim(F.tx("2026-0%d-1%d" % (3 + i % 5, 3 + i)), "ZZ74J3%02dXC017" % i, "1", 9000, 5000,
                [L_("J3490", 9000, 5000, "2026-02-1%d" % (3 + i), allowed=5000, cas=[("CO", "45", 4000)])])
    F.claim(F.tx("2026-10-14"), "ZZ74J3XXXC017", "1", 9000, 2000,
            [L_("J3490", 9000, 2000, "2026-09-24", allowed=2000, cas=[("CO", "45", 7000)])])
    F.exp("unit", ("ZZ74J3XXX", "J3490", "G", "2026-09-24"), "PAID")
    TM2 = dict(payer="TESTPAY MOHS2", pid="T0009")
    for i in range(6):
        F.claim(F.tx("2026-0%d-1%d" % (3 + i % 5, 2 + i), **TM2), "ZZ75AD%02dXC017" % i, "1", 90000, 50000,
                [L_("17311", 90000, 50000, "2026-02-1%d" % (2 + i), allowed=60000,
                    cas=[("CO", "45", 30000), ("PR", "2", 10000)])], **TM2)
    F.claim(F.tx("2026-10-15", **TM2), "ZZ75ADXXXC017", "1", 140000, 45000, [
        L_("17311", 90000, 25000, "2026-09-25", allowed=30000, cas=[("CO", "45", 60000), ("PR", "2", 5000)]),
        L_("17312", 50000, 20000, "2026-09-25", allowed=20000, cas=[("CO", "45", 30000)])], **TM2)
    F.exp("unit", ("ZZ75ADXXX", "17311", "G", "2026-09-25"), "SHORT")
    # ---- owner rule 4.1: an 85% payment only for a provider paid at 85%; a provider-level discount is reviewed
    F.fix = "owner rule 4.1 (provider-level rate)"
    TT = dict(payer="TESTPAY TIER", pid="T0010")
    for i in range(8):
        F.claim(F.tx("2026-0%d-1%d" % (3 + i % 5, i), **TT), "ZZ76TI%02dXC017" % i, "1", 20000, 12000,
                [L_("99214", 20000, 12000, "2026-02-1%d" % i, allowed=12000, npi="1000000003",
                    cas=[("CO", "45", 8000)])], **TT)
    F.claim(F.tx("2026-10-16", **TT), "ZZ76TIXXXC017", "1", 20000, 10200,
            [L_("99214", 20000, 10200, "2026-09-26", allowed=10200, npi="1000000003", cas=[("CO", "45", 9800)])], **TT)
    F.exp("unit", ("ZZ76TIXXX", "99214", "G", "2026-09-26"), "SHORT")
    TD = dict(payer="TESTPAY DISC", pid="T0011")
    for i in range(12):
        F.claim(F.tx("2026-0%d-2%d" % (3 + i % 5, i % 8), **TD), "ZZ77DB%02dXC017" % i, "1", 20000, 9000,
                [L_("99214", 20000, 9000, "2026-02-2%d" % (i % 8), allowed=9000, npi="1000000004",
                    cas=[("CO", "45", 11000)])], **TD)
    for i in range(14):
        F.claim(F.tx("2026-0%d-0%d" % (3 + i % 5, 1 + i % 8), **TD), "ZZ77DC%02dXC017" % i, "1", 20000, 12000,
                [L_("99214", 20000, 12000, "2026-02-0%d" % (1 + i % 8), allowed=12000,
                    npi="1000000005" if i < 8 else "1000000006", cas=[("CO", "45", 8000)])], **TD)
    F.claim(F.tx("2026-10-17", **TD), "ZZ77DBXXXC017", "1", 20000, 9000,
            [L_("99214", 20000, 9000, "2026-09-27", allowed=9000, npi="1000000004", cas=[("CO", "45", 11000)])], **TD)
    F.exp("sub", ("ZZ77DBXXX", "99214", "G", "2026-09-27"), ("SHORT", "below_rate_provider_discount"))
    F.exp("review", ("ZZ77DBXXX", "99214", "G", "2026-09-27"), True)
    # ---- fix 6 and owner rule 4.2: not payable
    F.fix = "fix 6 / owner rule 4.2 (not payable)"
    F.claim(F.tx("2026-10-18"), "ZZ78NP246C017", "1", 2919, 0,
            oneline("G2211", 2919, 0, "2026-09-28", cas=[("CO", "246", 2919)]))
    F.exp("sub", ("ZZ78NP246", "G2211", "G", "2026-09-28"), ("NOT_PAYABLE", "reporting_only_code"))
    F.exp("lot", ("ZZ78NP246", "G2211", "G", "2026-09-28"), False)
    TC_ = dict(payer="TESTPAY MCR", pid="T0012")
    for i in range(6):
        F.claim(F.tx("2026-0%d-1%d" % (3 + i % 5, 4 + i), **TC_), "ZZ79GA%02dXC017" % i, "1", 17000, 9600, [
            L_("99213", 15000, 8000, "2026-02-1%d" % (4 + i), allowed=10000, cas=[("CO", "45", 5000), ("PR", "2", 2000)]),
            L_("G2211", 2000, 1600, "2026-02-1%d" % (4 + i), allowed=1600, cas=[("CO", "45", 400)])], prod="MB", **TC_)
    F.claim(F.tx("2026-10-19", **TC_), "ZZ79GBXXXC017", "1", 26000, 12800, [
        L_("99213", 15000, 8000, "2026-09-29", mods=["25"], allowed=10000, cas=[("CO", "45", 5000), ("PR", "2", 2000)]),
        L_("11102", 9000, 4800, "2026-09-29", allowed=6000, cas=[("CO", "45", 3000), ("PR", "2", 1200)]),
        L_("G2211", 2000, 0, "2026-09-29", cas=[("CO", "234", 2000)])], prod="MB", **TC_)
    F.exp("sub", ("ZZ79GBXXX", "G2211", "G", "2026-09-29"), ("NOT_PAYABLE", "g2211_with_em_25"))
    TN = dict(payer="TESTPAY NOPAY", pid="T0013")
    for i in range(10):
        F.claim(F.tx("2026-0%d-2%d" % (3 + i % 5, i % 8), **TN), "ZZ80NG%02dXC017" % i, "1", 2919, 0,
                [L_("G2211", 2919, 0, "2026-02-2%d" % (i % 8), cas=[("CO", "97", 2919)])], **TN)
    ck, (lnp,) = F.claim(F.tx("2026-10-20", **TN), "ZZ80NGXXXC017", "1", 2919, 0,
                         [L_("G2211", 2919, 0, "2026-09-30", cas=[("PI", "B10", 2919)])], **TN)
    F.exp("sub", ("ZZ80NGXXX", "G2211", "G", "2026-09-30"), ("NOT_PAYABLE", "payer_never_pays_this_code"))
    # ---- fix 7: PR-242 (out of network) is a denial to work; the patient share is per unit of the unpaid part
    F.fix = "fix 7 (PR-group COB/network/eligibility; per-unit patient share)"
    ck, (l242,) = F.claim(F.tx("2026-10-21"), "ZZ81PR242C017", "4", 19353, 0,
                          oneline("99203", 19353, 0, "2026-10-01", cas=[("PR", "242", 19353)]))
    fid = F.finding("DENIED_NETWORK", "denial", line_key=l242, shortfall=10000)
    F.exp("item", fid, "CORRECT")
    TR = dict(payer="TESTPAY RAD", pid="T0014")
    for i in range(6):
        F.claim(F.tx("2026-0%d-0%d" % (3 + i % 5, 4 + i), **TR), "ZZ82RA%02dXC017" % i, "1", 9900, 6785,
                [L_("77436", 9900, 6785, "2026-02-0%d" % (4 + i), allowed=8481, cas=[("CO", "45", 1419), ("PR", "2", 1696)])],
                **TR)
    F.claim(F.tx("2026-10-22", **TR), "ZZ82RAXXXC017", "1", 19800, 6785, [
        L_("77436", 9900, 6785, "2026-10-02", allowed=8481, cas=[("CO", "45", 1419), ("PR", "2", 1696)]),
        L_("77436", 9900, 0, "2026-10-02", mods=["76"], allowed=8481, cas=[("CO", "45", 1419), ("PR", "1", 8481)])], **TR)
    F.exp("counts", ("ZZ82RAXXX", "77436", "G", "2026-10-02"), {"PAID": 1, "MOVED_TO_PATIENT": 1})
    # ---- fix 8: a duplicate denial after the payer allowed the service to the deductible: moved to the patient
    F.fix = "fix 8 (duplicate denial defers)"
    F.claim(F.tx("2026-09-06"), "ZZ83DUPA1C017", "1", 15000, 0,
            oneline("99213", 15000, 0, "2026-08-06", allowed=10000, cas=[("CO", "45", 5000), ("PR", "1", 10000)]))
    ck, (ld8,) = F.claim(F.tx("2026-10-06"), "ZZ83DUPA1C024", "1", 15000, 0,
                         oneline("99213", 15000, 0, "2026-08-06", cas=[("OA", "18", 15000)]))
    fid = F.finding("DENIED_DUPLICATE_UNPAID", "denial", line_key=ld8, shortfall=10000)
    F.exp("item", fid, "ALREADY_DONE")
    # ---- fix 9: a $0 answer delivered again later on another check (the corrected claim denied again) is kept
    F.fix = "fix 9 (later $0 deliveries kept)"
    ck, (l9,) = F.claim(F.tx("2026-07-02", trace="ZZK1"), "ZZ84REDN1C017", "1", 15000, 0,
                        oneline("99213", 15000, 0, "2026-06-20", cas=[("CO", "97", 15000)]), clp07="Q1")
    F.send837("ZZ84REDN1C024", "2026-08-01", oneline("99213", 15000, 0, "2026-06-20"), freq="7")
    F.claim(F.tx("2026-09-02", trace="ZZK2"), "ZZ84REDN1C017", "1", 15000, 0,
            oneline("99213", 15000, 0, "2026-06-20", cas=[("CO", "97", 15000)]), clp07="Q1")
    fid = F.finding("PROCEDURE_BUNDLED", "bundling", line_key=l9, shortfall=10000)
    F.exp("item", fid, "CORRECT")
    # ---- fix 10: work is a resend to the denying payer (or another payer after a COB/eligibility denial), an EMA
    # claim action on the open claim; a resend EMA shows rejected is not pending work
    F.fix = "fix 10 (work signals)"
    ck, (l10a,) = F.claim(F.tx("2026-10-02"), "ZZ85RSND1C017", "1", 15000, 0,
                          oneline("99213", 15000, 0, "2026-09-02", cas=[("CO", "16", 15000)]))
    F.send837("ZZ85RSND1C024", "2026-10-20", oneline("99213", 15000, 0, "2026-09-02"), payer="TESTPAY TWO", pid="T0002")
    fid = F.finding("DENIED_MISSING_INFO", "denial", line_key=l10a, shortfall=10000)
    F.exp("item", fid, "CORRECT")
    ck, (l10c,) = F.claim(F.tx("2026-10-03"), "ZZ85APPL1C017", "1", 9000, 0,
                          oneline("99212", 9000, 0, "2026-09-03", cas=[("CO", "50", 9000)]))
    F.ema_line(900010, "2026-09-03", "99212", [(0, "charge", 9000, T1, None), (30, "answer", 0, T1, "CHK835D")],
               bill="ZZ85APPL1", status="Denied", action="Initial Appeal Electronic")
    fid = F.finding("DENIED_MEDICAL_NECESSITY", "denial", line_key=l10c, shortfall=6000)
    F.exp("item", fid, "ALREADY_WORKED")
    ck, (l10d,) = F.claim(F.tx("2026-10-04"), "ZZ85RJCT1C017", "1", 9000, 0,
                          oneline("99212", 9000, 0, "2026-09-04", cas=[("CO", "16", 9000)]))
    F.send837("ZZ85RJCT1C017", "2026-11-10", oneline("99212", 9000, 0, "2026-09-04"))
    F.ema_line(900011, "2026-09-04", "99212", [(0, "charge", 9000, T1, None), (30, "answer", 0, T1, "CHK835E")],
               bill="ZZ85RJCT1", status="Rejected")
    fid = F.finding("DENIED_MISSING_INFO", "denial", line_key=l10d, shortfall=6000)
    F.exp("item", fid, "CORRECT")
    # ---- fix 11: a copy's lines mapped by content (not position); a no-answer item claims its lines' units
    F.fix = "fix 11 (item-to-unit mapping)"
    for tk_, order in ((F.tx("2026-10-05", trace="ZZK3"), 0), (F.tx("2026-10-05", trace="ZZK3", sup=1), 1)):
        lines_ = [L_("99213", 15000, 8000, "2026-09-05", allowed=10000, cas=[("CO", "45", 5000), ("PR", "2", 2000)]),
                  L_("11102", 9000, 0, "2026-09-05", cas=[("CO", "16", 9000)])]
        ck, lks = F.claim(tk_, "ZZ86COPY1C017", "1", 24000, 8000, lines_ if order == 0 else lines_[::-1], clp07="CP1")
        if order == 1:
            fid = F.finding("DENIED_MISSING_INFO", "denial", line_key=lks[0], shortfall=6000)
            F.exp("item", fid, "CORRECT")
    F.send837("ZZ87NANS1C017", "2026-06-06", oneline("17003", 6000, 0, "2026-05-20", units=3))
    F.finding("NO_ANSWER", "denial", pcn="ZZ87NANS1C017", shortfall=6000,
              evidence={"lines": [["17003", "2026-05-20", "6000"]]})
    F.exp("lot", ("ZZ87NANS1", "17003", "G", "2026-05-20"), False)
    # ---- fix 12: verdict mechanics
    F.fix = "fix 12 (verdict mechanics)"
    ck, (l12a,) = F.claim(F.tx("2026-10-23", **TN), "ZZ88UNPY1C017", "1", 2919, 0,
                          [L_("G2211", 2919, 0, "2026-10-03", cas=[("CO", "97", 2919)])], **TN)
    fid = F.finding("UNDERPAID_VS_OWN_RATE", "underpayment", line_key=l12a, shortfall=2919)
    F.exp("item", fid, "ALREADY_DONE")
    t12 = F.tx("2026-10-24")
    F.claim(F.tx("2026-09-24"), "ZZ89TKBK1C017", "1", 15000, 8000,
            oneline("99213", 15000, 8000, "2026-09-04", allowed=10000, cas=[("CO", "45", 5000), ("PR", "2", 2000)]))
    for amt in (5000, 3000):
        F.con.execute("insert into plb (transaction_key, reason_code, reference, amount_cents, payer_name, payer_id, "
                      "payment_date) values (?,?,?,?,?,?,?)", (t12, "WO", "1122 ZZ89TKBK1C017", amt, T1, "T0001",
                                                               "2026-10-24"))
    fid = F.finding("TAKEBACK", "fees", tk=t12, shortfall=5000, evidence={"reference": "1122 ZZ89TKBK1C017",
                                                                           "reason_code": "WO"})
    F.exp("item", fid, "CORRECT")
    F.send837("ZZ90ESTM1C017", "2026-06-07", oneline("99213", 15000, 0, "2026-05-21"))
    fid = F.finding("NO_ANSWER", "denial", pcn="ZZ90ESTM1C017", shortfall=9000,
                    evidence={"lines": [["99213", "2026-05-21", "15000"]]})
    F.exp("item", fid, "CORRECT")
    TU2 = dict(payer="TESTPAY UMR2", pid="T0015")
    for i in range(6):
        F.claim(F.tx("2026-0%d-1%d" % (3 + i % 5, 5 + i % 4), **TU2), "ZZ91LA%02dXC017" % i, "1", 354, 194,
                [L_("J9190", 354, 194, "2026-02-1%d" % (5 + i % 4), allowed=194, cas=[("CO", "45", 160)])], **TU2)
    ck, (l12d,) = F.claim(F.tx("2026-10-25", **TU2), "ZZ91LAXXXC017", "1", 354, 0,
                          [L_("J9190", 354, 0, "2026-10-05", cas=[("CO", "210", 206), ("CO", "45", 148)])], **TU2)
    fid = F.finding("DENIED_TIMELY_FILING", "denial", line_key=l12d, shortfall=206)
    F.exp("item", fid, "CORRECT")
    ck, (l12e,) = F.claim(F.tx("2026-08-07"), "ZZ92RCVR1C017", "1", 15000, 0,
                          oneline("99213", 15000, 0, "2026-07-07", cas=[("CO", "97", 15000)]))
    F.claim(F.tx("2026-09-07"), "ZZ92RCVR1C024", "1", 15000, 8000,
            oneline("99213", 15000, 8000, "2026-07-07", allowed=10000, cas=[("CO", "45", 5000), ("PR", "2", 2000)]))
    F.claim(F.tx("2026-09-17", payer="TESTPAY SUPP", pid="T0004"), "ZZ92RCVR1C024", "1", 15000, 2000,
            oneline("99213", 15000, 2000, "2026-07-07", allowed=2000, cas=[("OA", "23", 13000)]),
            payer="TESTPAY SUPP", pid="T0004")
    fid = F.finding("PROCEDURE_BUNDLED", "bundling", status="resolved", line_key=l12e, shortfall=10000,
                    recovered=10000, evidence={"paid_later": {"paid_cents": 10000}},
                    events=[("paid_on_later_remit", "resolved", "system")])
    F.exp("item", fid, "CORRECT")
    # ---- fix 13: the smaller rules
    F.fix = "fix 13 (smaller rules)"
    ck, (l13a,) = F.claim(F.tx("2026-09-08"), "ZZ93SSEC1C017", "1", 15000, 0,
                          oneline("99213", 15000, 0, "2026-08-08", mods=["25"], cas=[("CO", "97", 15000)]))
    F.claim(F.tx("2026-10-08"), "ZZ93SSEC1C017", "2", 15000, 0,
            oneline("99213", 15000, 0, "2026-08-08", mods=["25"], allowed=10000, cas=[("OA", "23", 10000), ("CO", "45", 5000)]))
    fid = F.finding("EM_BUNDLED_MOD25", "bundling", line_key=l13a, shortfall=10000)
    F.exp("item", fid, "CORRECT")
    F.claim(F.tx("2026-04-01"), "ZZ94TBWN1C017", "1", 15000, 8000,
            oneline("99213", 15000, 8000, "2026-03-25", allowed=10000, cas=[("CO", "45", 5000), ("PR", "2", 2000)]))
    F.con.execute("insert into plb (transaction_key, reason_code, reference, amount_cents, payer_name, payer_id, "
                  "payment_date) values (?,?,?,?,?,?,?)", (F.tx("2026-10-15"), "WO", "3344 ZZ94TBWN1C017", 8000, T1,
                                                           "T0001", "2026-10-15"))
    F.exp("lot", ("ZZ94TBWN1", "99213", "G", "2026-03-25"), True)
    F.send837("ZZ95SPLT1C017", "2026-11-01", oneline("99214", 20000, 0, "2026-10-28"))
    ckm, _ = F.claim(F.tx("2026-11-12"), "ZZ95SPLT1C017", "1", 20000, 0, [])
    F.con.execute("update claims set statement_from='2026-10-28' where claim_key=?", (ckm,))
    F.con.execute("insert into remarks (claim_key, line_key, level, code) values (?,?,?,?)", (ckm, None, "claim", "MA15"))
    F.exp("unit", ("ZZ95SPLT1", "99214", "G", "2026-10-28"), "IN_PROCESS")
    F.send837("ZZ96SV061C017", "2026-09-11", oneline("77290", 40000, 0, "2026-09-10"))
    F.claim(F.tx("2026-10-01"), "ZZ96SV061C017", "1", 40000, 0,
            oneline("77290", 40000, 0, "2026-09-10", mods=["26"], ocode="77290", cas=[("CO", "4", 40000)]))
    F.exp("unit", ("ZZ96SV061", "77290", "G", "2026-09-10"), "DENIED")
    F.claim(F.tx("2026-10-26"), "ZZ97PLCH1C017", "1", 1, 0, oneline("J3590", 1, 0, "2026-10-06", cas=[("CO", "109", 1)]))
    F.exp("lot", ("ZZ97PLCH1", "J3590", "G", "2026-10-06"), False)
    ck, (l13f,) = F.claim(F.tx("2026-08-09"), "ZZ98PRSN1C017", "1", 15000, 0,
                          oneline("99213", 15000, 0, "2026-07-09", cas=[("CO", "50", 15000)]))
    F.claim(F.tx("2026-09-09"), "ZZ98PRSN1C024", "1", 15000, 8000,
            oneline("99213", 15000, 8000, "2026-07-09", allowed=10000, cas=[("CO", "45", 5000), ("PR", "2", 2000)]))
    fid = F.finding("DENIED_MEDICAL_NECESSITY", "denial", status="resolved", line_key=l13f, shortfall=10000,
                    recovered=15236, events=[("status_change", "resolved", "biller")])
    F.exp("item", fid, "WRONG_AMOUNT")
    ck, (l13g1, l13g2) = F.claim(F.tx("2026-09-10"), "ZZ99DROP1C017", "1", 31005, 8000, [
        L_("99213", 15000, 8000, "2026-08-10", allowed=10000, cas=[("CO", "45", 5000), ("PR", "2", 2000)]),
        L_("G6001", 16005, 0, "2026-08-10", cas=[("CO", "272", 16005)])])
    F.claim(F.tx("2026-10-10"), "ZZ99DROP1C017", "1", 15000, 8000, [
        L_("99213", 15000, 8000, "2026-08-10", allowed=10000, cas=[("CO", "45", 5000), ("PR", "2", 2000)])], freq="7",
        clp07="DR2")
    fid = F.finding("DENIED_MEDICAL_NECESSITY", "denial", line_key=l13g2, shortfall=10709)
    F.exp("item", fid, "ALREADY_DONE")
    # ---- calibration round 2 (A16 sample): refinements of fixes 5, 7 and rule 4.1; section 5 reported apart
    F.fix = "fix 5 / rule 4.1 (second rate is the payer's, not one provider's discount)"
    n_ = 0
    TS = dict(payer="TESTPAY SRA", pid="T0020")
    for npi, amt, cnt in (("1000000021", 12000, 12), ("1000000022", 9000, 12), ("1000000023", 12000, 10)):
        for i in range(cnt):
            n_ += 1
            F.claim(F.tx("2026-0%d-%02d" % (3 + n_ % 5, 1 + n_ % 27), **TS), "ZZ25S%04dC017" % n_, "1", 20000, amt,
                    [L_("99214", 20000, amt, "2026-02-%02d" % (1 + n_ % 27), allowed=amt, npi=npi,
                        cas=[("CO", "45", 20000 - amt)])], **TS)
    F.claim(F.tx("2026-10-20", **TS), "ZZ25SXXXXC017", "1", 20000, 9000,
            [L_("99214", 20000, 9000, "2026-09-30", allowed=9000, npi="1000000021", cas=[("CO", "45", 11000)])], **TS)
    # paid at another provider's own discount: no second rate of the payer -> short against the payer's rate
    F.exp("sub", ("ZZ25SXXXX", "99214", "G", "2026-09-30"), ("SHORT", "below_rate"), guard=True)
    TS2 = dict(payer="TESTPAY SRB", pid="T0021")
    for npi, amts in (("1000000024", [12000] * 8 + [10000] * 4), ("1000000025", [12000] * 8 + [10000] * 4),
                      ("1000000026", [12000] * 6 + [10000] * 6)):
        for amt in amts:
            n_ += 1
            F.claim(F.tx("2026-0%d-%02d" % (3 + n_ % 5, 1 + n_ % 27), **TS2), "ZZ26S%04dC017" % n_, "1", 20000, amt,
                    [L_("99214", 20000, amt, "2026-02-%02d" % (1 + n_ % 27), allowed=amt, npi=npi,
                        cas=[("CO", "45", 20000 - amt)])], **TS2)
    F.claim(F.tx("2026-10-21", **TS2), "ZZ26SXXXXC017", "1", 20000, 10000,
            [L_("99214", 20000, 10000, "2026-09-30", allowed=10000, npi="1000000024", cas=[("CO", "45", 10000)])],
            **TS2)
    # an amount the payer pays across providers (no one provider's own rate): a second rate, paid
    F.exp("sub", ("ZZ26SXXXX", "99214", "G", "2026-09-30"), ("PAID", "second_rate"))
    F.fix = "fix 5 (weak payer rate: undecided, not left on the table)"
    TW = dict(payer="TESTPAY WEAK", pid="T0022")
    for amt in [10000] * 4 + [9000] * 3 + [8000] * 2:
        n_ += 1
        F.claim(F.tx("2026-0%d-%02d" % (3 + n_ % 5, 1 + n_ % 27), **TW), "ZZ27W%04dC017" % n_, "1", 15000, amt,
                [L_("99213", 15000, amt, "2026-02-%02d" % (1 + n_ % 27), allowed=amt, npi="1000000030",
                    cas=[("CO", "45", 15000 - amt)])], **TW)
    ck, (lw,) = F.claim(F.tx("2026-10-22", **TW), "ZZ27WXXXXC017", "1", 15000, 8000,
                        [L_("99213", 15000, 8000, "2026-10-01", allowed=8000, npi="1000000030",
                            cas=[("CO", "45", 7000)])], **TW)
    fid = F.finding("UNDERPAID_VS_OWN_RATE", "underpayment", line_key=lw, shortfall=2000)
    F.exp("item", fid, "UNDECIDED")
    F.exp("lot", ("ZZ27WXXXX", "99213", "G", "2026-10-01"), False, guard=True)
    F.fix = "fix 7 (coverage lapse the patient took on the claim)"
    ck, (lp1, lp2) = F.claim(F.tx("2026-10-23"), "ZZ28PRLPSC017", "4", 25000, 0, [
        L_("99214", 22000, 0, "2026-10-02", cas=[("PR", "200", 22000)]),
        L_("G2211", 3000, 0, "2026-10-02", cas=[("PR", "200", 3000)])])
    F.ema_line(900101, "2026-10-02", "99214", [(0, "charge", 22000, T1, None), (21, "answer", 0, T1, "CHKPR1"),
                                               (40, "patpay", 12500, T1, None)],
               bill="ZZ28PRLPS", person=7101, status="Denied")
    F.ema_line(900101, "2026-10-02", "G2211", [(0, "charge", 3000, T1, None), (21, "answer", 0, T1, "CHKPR1")],
               bill="ZZ28PRLPS", person=7101, status="Denied")
    fid = F.finding("CLAIM_DENIED_UNPAID", "denial", claim_key=ck, shortfall=25000)
    F.exp("item", fid, "ALREADY_DONE", guard=True)
    F.exp("sub", ("ZZ28PRLPS", "G2211", "G", "2026-10-02"), ("MOVED_TO_PATIENT", "PR-200"), guard=True)
    F.fix = "section 5 (secondary balances, reported apart)"
    F.claim(F.tx("2026-10-01"), "ZZ29SECB1C017", "1", 15000, 8000, [
        L_("99213", 15000, 8000, "2026-09-01", allowed=10000, cas=[("CO", "45", 5000), ("PR", "2", 2000)])])
    F.claim(F.tx("2026-10-15", payer="TESTPAY TWO", pid="T0002"), "ZZ29SECB1C017", "1", 15000, 0, [
        L_("99213", 15000, 0, "2026-09-01", cas=[("CO", "185", 15000)])], payer="TESTPAY TWO", pid="T0002", prod="MC")
    F.exp("secbal", ("ZZ29SECB1", "99213", "G", "2026-09-01"), True)
    F.claim(F.tx("2026-10-02"), "ZZ29SECB2C017", "1", 15000, 8000, [
        L_("99213", 15000, 8000, "2026-09-02", allowed=10000, cas=[("CO", "45", 5000), ("PR", "2", 2000)])])
    F.claim(F.tx("2026-10-16", payer="TESTPAY TWO", pid="T0002"), "ZZ29SECB2C017", "2", 15000, 0, [
        L_("99213", 15000, 0, "2026-09-02", cas=[("OA", "23", 8000), ("CO", "45", 7000)])], payer="TESTPAY TWO",
        pid="T0002", prod="MC")
    F.exp("secbal", ("ZZ29SECB2", "99213", "G", "2026-09-02"), False, guard=True)

    def plb_(date, amt, pcn, payer="TESTPAY ONE", pid="T0001", rc="WO"):
        F.con.execute("insert into plb (transaction_key, reason_code, reference, amount_cents, payer_name, payer_id, "
                      "payment_date) values (?,?,?,?,?,?,?)", (F.tx(date, payer=payer, pid=pid), rc,
                                                               "123456789012345    " + pcn, amt, payer, pid, date))
    F.fix = "fix 13 / round 2 (a take-back recovers a payment made before it, matched by amount)"
    # a PLB recovering line A's payment (exact amount) before line B was paid on a later processing: B stays paid
    F.claim(F.tx("2026-03-10"), "ZZ58PLBA1C017", "1", 22802, 12763, [
        L_("11103", 13707, 12763, "2026-03-01", allowed=12763, cas=[("CO", "45", 944)]),
        L_("11102", 9095, 0, "2026-03-01", cas=[("CO", "97", 9095)])])
    plb_("2026-04-20", 12763, "ZZ58PLBA1C017")
    F.claim(F.tx("2026-05-01"), "ZZ58PLBA1C017", "1", 9095, 4407, [
        L_("11102", 9095, 4407, "2026-03-01", allowed=4497, cas=[("CO", "45", 4598), ("CO", "253", 90)])])
    F.exp("unit", ("ZZ58PLBA1", "11102", "G", "2026-03-01"), "PAID")
    F.exp("unit", ("ZZ58PLBA1", "11103", "G", "2026-03-01"), "TAKEN_BACK", guard=True)
    # a PLB of exactly the first processing's payment (another line) after a reprocessing paid this line: this line paid
    TB_ = dict(payer="TESTPAY PLB", pid="T0031")
    F.claim(F.tx("2026-03-12", **TB_), "ZZ58PLBB1C017", "1", 51171, 19642, [
        L_("13101", 35661, 19642, "2026-03-02", allowed=21000, cas=[("CO", "45", 14661), ("PR", "2", 1358)]),
        L_("11402", 15510, 0, "2026-03-02", cas=[("CO", "96", 15510)])], **TB_)
    F.claim(F.tx("2026-05-12", **TB_), "ZZ58PLBB1C017", "1", 51171, 7340, [
        L_("11402", 15510, 7340, "2026-03-02", allowed=7490, cas=[("CO", "45", 8020), ("CO", "253", 150)]),
        L_("13101", 35661, 0, "2026-03-02", cas=[("CO", "18", 35661)])], **TB_)
    plb_("2026-06-20", 19642, "ZZ58PLBB1C017", **TB_)
    F.exp("unit", ("ZZ58PLBB1", "11402", "G", "2026-03-02"), "PAID")
    # the same paid processing delivered again on another check, then a PLB of exactly that amount: the duplicate
    # payment is recovered, the service stays paid once
    F.claim(F.tx("2026-03-14"), "ZZ58PLBC1C017", "1", 89482, 77440, [
        L_("99214", 12042, 0, "2026-03-03", mods=["57"], cas=[("CO", "97", 12042)]),
        L_("14041", 77440, 77440, "2026-03-03", allowed=77440)])
    for d_ in ("2026-08-01", "2026-08-02"):
        F.claim(F.tx(d_), "ZZ58PLBC1C017", "1", 89482, 11211, [
            L_("99214", 12042, 11211, "2026-03-03", mods=["57"], allowed=11440, cas=[("CO", "45", 602), ("CO", "253", 229)]),
            L_("14041", 77440, 0, "2026-03-03", cas=[("CO", "B13", 77440)])])
    plb_("2026-08-23", 11211, "ZZ58PLBC1C017")
    F.exp("unit", ("ZZ58PLBC1", "99214", "G", "2026-03-03"), "PAID")
    F.fix = "fix 4 / round 2 (the payer that answers CLP02 19 is the primary)"
    TM_ = dict(payer="TESTPAY MCR", pid="T0012")
    TP_ = dict(payer="TESTPAY TPA", pid="T0030")
    F.claim(F.tx("2026-03-20", **TM_), "ZZ59PRIM1C017", "19", 66812, 24292, [
        L_("99213", 6062, 0, "2026-03-01", mods=["57"], cas=[("CO", "97", 6062)]),
        L_("11102", 60750, 24292, "2026-03-01", allowed=30367, cas=[("CO", "45", 30383), ("PR", "2", 6075)])],
        prod="MB", xover="TESTPAY TPA", clp07="MCRA", **TM_)
    F.claim(F.tx("2026-04-18", **TP_), "ZZ59PRIM1C017", "1", 66812, 6075, [
        L_("99213", 6062, 0, "2026-03-01", cas=[("CO", "45", 6062)]),
        L_("11102", 60750, 6075, "2026-03-01", allowed=6075, cas=[("CO", "45", 54675)])], prod="15", **TP_)
    t_r = F.tx("2026-05-18", **TM_)
    F.claim(t_r, "ZZ59PRIM1C017", "22", -66812, -24292, [
        L_("99213", -6062, 0, "2026-03-01", mods=["57"], cas=[("CO", "97", -6062)]),
        L_("11102", -60750, -24292, "2026-03-01", allowed=-30367, cas=[("CO", "45", -30383), ("PR", "2", -6075)])],
        prod="MB", clp07="MCRA", **TM_)
    F.claim(t_r, "ZZ59PRIM1C017", "19", 66812, 29140, [
        L_("99213", 6062, 4848, "2026-03-01", mods=["57"], allowed=6062, cas=[("CO", "253", 2), ("PR", "2", 1212)]),
        L_("11102", 60750, 24292, "2026-03-01", allowed=30367, cas=[("CO", "45", 30383), ("PR", "2", 6075)])],
        prod="MB", xover="TESTPAY TPA", clp07="MCRB", **TM_)
    F.claim(F.tx("2026-07-17", **TP_), "ZZ59PRIM1C017", "1", 66812, 1212, [
        L_("99213", 6062, 0, "2026-03-01", cas=[("CO", "45", 6062)]),
        L_("11102", 60750, 1212, "2026-03-01", allowed=1212, cas=[("CO", "45", 59538)])], prod="15", **TP_)
    F.exp("unit", ("ZZ59PRIM1", "99213", "G", "2026-03-01"), "PAID")
    # a CLP02 19 that only transfers the claim to the proper payer (CO-B11, nothing allowed) does not make a primary
    TX_ = dict(payer="TESTPAY XFR", pid="T0033")
    F.claim(F.tx("2026-04-04", **TX_), "ZZ59PRIM2C017", "19", 8909, 0, [
        L_("11102", 8909, 0, "2026-03-05", cas=[("CO", "B11", 8909)])], xover="TESTPAY ONE", **TX_)
    F.claim(F.tx("2026-04-26"), "ZZ59PRIM2C017", "1", 8909, 3907, [
        L_("11102", 8909, 3907, "2026-03-05", allowed=3987, cas=[("CO", "45", 4922), ("CO", "253", 80)])])
    F.claim(F.tx("2026-05-01", **TX_), "ZZ59PRIM2C017", "19", 8909, 0, [
        L_("11102", 8909, 0, "2026-03-05", cas=[("CO", "B11", 8909)])], xover="TESTPAY ONE", **TX_)
    F.exp("unit", ("ZZ59PRIM2", "11102", "G", "2026-03-05"), "PAID", guard=True)
    F.fix = "round 2 (a void's $0 acknowledgement does not revive the claim)"
    F.claim(F.tx("2026-03-18"), "ZZ59VOID1C017", "1", 19000, 9800, [
        L_("88305", 19000, 9800, "2026-03-01", allowed=9800, cas=[("CO", "45", 9200)])], clp07="VA1")
    F.send837("ZZ59VOID1C017", "2026-03-02", [L_("88305", 19000, 0, "2026-03-01")])
    F.send837("ZZ59VOID1C017", "2026-04-15", [L_("88305", 19000, 0, "2026-03-01")], freq="8")
    t_v = F.tx("2026-05-06")
    F.claim(t_v, "ZZ59VOID1C017", "22", -19000, -9800, [
        L_("88305", -19000, -9800, "2026-03-01", allowed=-9800, cas=[("CO", "45", -9200)])], clp07="VA1")
    F.claim(t_v, "ZZ59VOID1C017", "4", 19000, 0, [
        L_("88305", 19000, 0, "2026-03-01", cas=[("CO", "45", 19000)])], clp07="VA2")
    F.exp("unit", ("ZZ59VOID1", "88305", "G", "2026-03-01"), "VOIDED")
    F.fix = "fix 4 / round 2 (a secondary's denial of a deductible the primary paid nothing on)"
    T2_ = dict(payer="TESTPAY TWO", pid="T0002")
    F.claim(F.tx("2026-04-10"), "ZZ58SECS1C017", "1", 11059, 0, [
        L_("99203", 11059, 0, "2026-04-01", allowed=9400, cas=[("PR", "1", 9400), ("CO", "45", 1659)])])
    ck, (l_s1,) = F.claim(F.tx("2026-04-18", **T2_), "ZZ58SECS1C017", "1", 11059, 0, [
        L_("99203", 11059, 0, "2026-04-01", cas=[("CO", "96", 11059)])], prod="MC", **T2_)
    fid = F.finding("DENIED_NONCOVERED", "denial", line_key=l_s1, shortfall=11059)
    F.exp("item", fid, "WRONG_AMOUNT")
    # the primary paid (a coinsurance left): the item as framed is done; the residual is a secondary balance
    F.claim(F.tx("2026-04-11"), "ZZ58SECS2C017", "1", 11059, 6000, [
        L_("99203", 11059, 6000, "2026-04-02", allowed=7500, cas=[("PR", "2", 1500), ("CO", "45", 3559)])])
    ck, (l_s2,) = F.claim(F.tx("2026-04-19", **T2_), "ZZ58SECS2C017", "1", 11059, 0, [
        L_("99203", 11059, 0, "2026-04-02", cas=[("CO", "185", 11059)])], prod="MC", **T2_)
    fid = F.finding("DENIED_PROVIDER", "denial", line_key=l_s2, shortfall=11059)
    F.exp("item", fid, "ALREADY_DONE", guard=True)
    F.exp("secbal", ("ZZ58SECS2", "99203", "G", "2026-04-02"), True)
    F.fix = "rule 4.1 / round 2 (an NP/PA paid 85% of a charge below the payer's rate)"
    TN_ = dict(payer="TESTPAY NPC", pid="T0032")
    n_ = 0
    for npi, amt in (("1000000041", 12000), ("1000000042", 10200)):
        for i in range(10):
            n_ += 1
            F.claim(F.tx("2026-0%d-%02d" % (3 + n_ % 5, 1 + n_ % 27), **TN_), "ZZ57N%04dC017" % n_, "1", 15000, amt,
                    [L_("99213", 15000, amt, "2026-02-%02d" % (1 + n_ % 27), allowed=amt, npi=npi,
                        cas=[("CO", "45", 15000 - amt)])], **TN_)
    F.claim(F.tx("2026-10-24", **TN_), "ZZ57NXXXXC017", "1", 11000, 9350, [
        L_("99213", 11000, 9350, "2026-10-03", allowed=9350, npi="1000000042", cas=[("CO", "45", 1650)])], **TN_)
    F.exp("unit", ("ZZ57NXXXX", "99213", "G", "2026-10-03"), "PAID")
    # ---- calibration round 2, from the left-on-the-table traces
    F.fix = "fix 10 / round 2 (a resend the payer answered for this code is not 'answered without it')"
    TR_ = dict(payer="TESTPAY RSD", pid="T0035")
    F.claim(F.tx("2026-05-16", **TR_), "ZZ56RSND1C017", "1", 21000, 9000, [
        L_("99213", 15000, 9000, "2026-05-01", allowed=10000, cas=[("CO", "45", 5000), ("PR", "2", 1000)]),
        L_("17000", 6000, 0, "2026-05-01", cas=[("CO", "97", 6000)])], **TR_)
    F.send837("ZZ56RSND1C024", "2026-05-22", [L_("99213", 15000, 0, "2026-05-01"), L_("17000", 6000, 0, "2026-05-01")],
              payer="TESTPAY RSD", pid="T0035")
    F.claim(F.tx("2026-06-01", **TR_), "ZZ56RSND1C024", "2", 21000, 0, [
        L_("99213", 15000, 0, "2026-05-01", cas=[("CO", "18", 15000)]),
        L_("17000", 6000, 0, "2026-05-01", cas=[("CO", "97", 6000)])], **TR_)
    F.exp("sub", ("ZZ56RSND1", "17000", "G", "2026-05-01"), ("DENIED", "resent_and_denied_again"))
    F.fix = "fix 6 / rule 4.2 / round 2 (a $0.01 placeholder payment is no payment; per product class)"
    TPH = dict(payer="TESTPAY PHD", pid="T0034")
    for i in range(20):
        F.claim(F.tx("2026-0%d-%02d" % (3 + i % 5, 1 + i), **TPH), "ZZ56PH%03dC017" % i, "1", 2919, 1, [
            L_("G2211", 2919, 1, "2026-02-%02d" % (1 + i), allowed=1, cas=[("CO", "45", 2918)])], **TPH)
    F.claim(F.tx("2026-10-25", **TPH), "ZZ56PHXXXC017", "1", 2919, 0, [
        L_("G2211", 2919, 0, "2026-10-04", cas=[("CO", "97", 2919)])], **TPH)
    F.exp("sub", ("ZZ56PHXXX", "G2211", "G", "2026-10-04"), ("NOT_PAYABLE", "payer_never_pays_this_code"))
    TCL = dict(payer="TESTPAY CLS", pid="T0036")
    for i, prod in enumerate(["HM"] * 10 + ["12"] * 10 + ["16"] * 20):
        paid_ = 1600 if prod == "16" else 0
        cas_ = [("CO", "45", 400)] if paid_ else [("CO", "234", 2000)]
        F.claim(F.tx("2026-0%d-%02d" % (3 + i % 5, 1 + i % 27), **TCL), "ZZ56CL%03dC017" % i, "1", 2000, paid_, [
            L_("G2211", 2000, paid_, "2026-02-%02d" % (1 + i % 27), allowed=paid_ or None, cas=cas_)], prod=prod, **TCL)
    F.claim(F.tx("2026-10-26", **TCL), "ZZ56CLXXXC017", "1", 2000, 0, [
        L_("G2211", 2000, 0, "2026-10-05", cas=[("CO", "234", 2000)])], prod="HM", **TCL)
    F.exp("sub", ("ZZ56CLXXX", "G2211", "G", "2026-10-05"),
          ("NOT_PAYABLE", "payer_rarely_pays_this_code_product_class"))
    F.fix = "fix 1 / round 2 (EMA voided the charge after a take-back)"
    F.claim(F.tx("2026-06-18"), "ZZ56EVOD1C017", "1", 19524, 19485, [
        L_("88305", 19524, 19485, "2026-06-01", units=3, allowed=19485, cas=[("CO", "45", 39)])])
    plb_("2026-07-19", 19485, "ZZ56EVOD1C017")
    F.ema_line(900201, "2026-06-01", "88305", [(14, "charge", 19524, T1, None), (24, "pay", 19485, T1, "CHKEV1"),
                                               (51, "pay", -19485, T1, "CHKEV2"), (51, "charge", -19524, T1, None)],
               bill="ZZ56EVOD1", status="Inactive/Voided")
    F.exp("unit", ("ZZ56EVOD1", "88305", "G", "2026-06-01"), "VOIDED")
    F.fix = "fix 2 / round 2 (a $0 line adds no billed unit)"
    TZ_ = dict(payer="TESTPAY ZLN", pid="T0037")
    F.claim(F.tx("2026-06-20", **TZ_), "ZZ56ZLIN1C017", "1", 107786, 37396, [
        L_("17311", 0, 0, "2026-06-02"),
        L_("17311", 107786, 37396, "2026-06-02", allowed=37396, paid_units=None, cas=[("CO", "45", 70390)])], **TZ_)
    F.exp("billed", ("ZZ56ZLIN1", "17311", "G", "2026-06-02"), 1)
    F.fix = "fix 4 / round 2 (a payer whose payment was recouped is not the primary)"
    TH_ = dict(payer="TESTPAY HUM", pid="T0038")
    F.claim(F.tx("2026-06-12", **TH_), "ZZ56RCUP1C017", "1", 8952, 8001, [
        L_("99213", 8952, 8001, "2026-06-02", allowed=8504, cas=[("CO", "45", 448), ("CO", "253", 163),
                                                                 ("PR", "2", 340)])], **TH_)
    F.claim(F.tx("2026-08-19", **TM_), "ZZ56RCUP1C024", "1", 8952, 7065, [
        L_("99213", 8952, 7065, "2026-06-02", allowed=8952, cas=[("PR", "2", 1790), ("CO", "253", 97)])], prod="MB",
        **TM_)
    plb_("2026-08-27", 8001, "ZZ56RCUP1C017", **TH_)
    F.exp("unit", ("ZZ56RCUP1", "99213", "G", "2026-06-02"), "PAID")
    F.fix = "fix 5 / round 2 (a multiple-procedure half of another amount the payer pays)"
    TMP = dict(payer="TESTPAY MPH", pid="T0039")
    for i, amt in enumerate([4906] * 22 + [5826] * 3):
        F.claim(F.tx("2026-0%d-%02d" % (3 + i % 5, 1 + i % 27), **TMP), "ZZ56MP%03dC017" % i, "1", 6133, amt, [
            L_("17000", 6133, amt, "2026-02-%02d" % (1 + i % 27), allowed=amt, cas=[("CO", "45", 6133 - amt)])],
            prod="13", **TMP)
    F.claim(F.tx("2026-10-27", **TMP), "ZZ56MPXXXC017", "1", 20000, 13913, [
        L_("11102", 13867, 11000, "2026-10-06", allowed=11000, cas=[("CO", "45", 2867)]),
        L_("17000", 6133, 2913, "2026-10-06", mods=["59"], allowed=2913, cas=[("CO", "45", 3220)])], prod="13", **TMP)
    F.exp("sub", ("ZZ56MPXXX", "17000", "G", "2026-10-06"), ("PAID", "half_rate_mppr"))
    F.fix = "fix 6 / rule 4.2 / round 2 (G2211 beside an E/M -25, the answer in EMA's books only)"
    TO_ = "TESTPAY OFF"
    F.send837("ZZ56G25E1C017", "2026-09-08", [L_("99214", 22458, 0, "2026-09-07", mods=["25"]),
                                              L_("G2211", 1668, 0, "2026-09-07")], payer=TO_, pid="T0099")
    F.ema_line(900301, "2026-09-07", "G2211", [(0, "charge", 1668, TO_, None), (30, "answer", 0, TO_, "CHKG25"),
                                               (30, "adj", 1668, TO_, ("CO", "181", True))],
               bill="ZZ56G25E1", status="Denied")
    F.ema_check("CHKG25", TO_, "none")
    F.exp("sub", ("ZZ56G25E1", "G2211", "G", "2026-09-07"), ("NOT_PAYABLE", "g2211_with_em_25"))
    F.fix = "fix 6 / rule 4.2 / round 2 ('almost never' at product-class level: under 1 in 10)"
    TC2 = dict(payer="TESTPAY CL2", pid="T0040")
    for i in range(30):
        paid_ = 1600 if i < 2 else 0
        cas_ = [("CO", "45", 400)] if paid_ else [("CO", "234", 2000)]
        F.claim(F.tx("2026-0%d-%02d" % (3 + i % 5, 1 + i % 27), **TC2), "ZZ56CM%03dC017" % i, "1", 2000, paid_, [
            L_("G2211", 2000, paid_, "2026-02-%02d" % (1 + i % 27), allowed=paid_ or None, cas=cas_)],
            prod="HM" if i % 2 else "13", **TC2)
    F.claim(F.tx("2026-10-28", **TC2), "ZZ56CMXXXC017", "1", 2000, 0, [
        L_("G2211", 2000, 0, "2026-10-07", cas=[("CO", "234", 2000)])], prod="HM", **TC2)
    F.exp("sub", ("ZZ56CMXXX", "G2211", "G", "2026-10-07"),
          ("NOT_PAYABLE", "payer_rarely_pays_this_code_product_class"))
    # ... but a non-covered denial (CO-96) of a code the payer pays now and then keeps the 5% line: owed
    TC3 = dict(payer="TESTPAY CL3", pid="T0042")
    for i in range(30):
        paid_ = 9000 if i < 2 else 0
        cas_ = [("CO", "45", 3000)] if paid_ else [("CO", "96", 12000)]
        F.claim(F.tx("2026-0%d-%02d" % (3 + i % 5, 1 + i % 27), **TC3), "ZZ56CN%03dC017" % i, "1", 12000, paid_, [
            L_("96904", 12000, paid_, "2026-02-%02d" % (1 + i % 27), allowed=paid_ or None, cas=cas_)],
            prod="16", **TC3)
    F.claim(F.tx("2026-10-28", **TC3), "ZZ56CNXXXC017", "1", 12000, 0, [
        L_("96904", 12000, 0, "2026-10-07", cas=[("CO", "96", 12000)])], prod="16", **TC3)
    F.exp("unit", ("ZZ56CNXXX", "96904", "G", "2026-10-07"), "DENIED", guard=True)
    F.fix = "fix 13 / round 2 (a unit worth $0.01 is no money left on the table)"
    TPL = dict(payer="TESTPAY PLH", pid="T0041")
    for i in range(15):
        amt = 2000 if i < 3 else 1
        F.claim(F.tx("2026-0%d-%02d" % (3 + i % 5, 1 + i), **TPL), "ZZ56PL%03dC017" % i, "1", 2919, amt, [
            L_("G2211", 2919, amt, "2026-02-%02d" % (1 + i), allowed=amt, cas=[("CO", "45", 2919 - amt)])], **TPL)
    F.claim(F.tx("2026-10-29", **TPL), "ZZ56PLXXXC017", "1", 2919, 0, [
        L_("G2211", 2919, 0, "2026-10-08", cas=[("CO", "97", 2919)])], **TPL)
    F.exp("lot", ("ZZ56PLXXX", "G2211", "G", "2026-10-08"), False)
    F.fix = "fix 4 / round 3 (another payer paid the whole charge's adjudication; the denying payer has no price)"
    TS_ = dict(payer="TESTPAY SIH", pid="T0043")
    ck, (l_s3,) = F.claim(F.tx("2026-04-16", **TS_), "ZZ55SIHO1C017", "1", 3836, 0, [
        L_("17003", 3836, 0, "2026-04-01", units=2, cas=[("CO", "96", 3836)])], prod="15", **TS_)
    F.claim(F.tx("2026-09-08", **TM_), "ZZ55SIHO1C024", "2", 3836, 852, [
        L_("17003", 3836, 852, "2026-04-01", units=2, allowed=1004,
           cas=[("CO", "45", 2832), ("PR", "2", 201), ("CO", "253", 17), ("CO", "144", -66)])], prod="MB", **TM_)
    F.exp("unit", ("ZZ55SIHO1", "17003", "G", "2026-04-01"), "PAID")
    F.fix = "fix 5 / round 3 (an amount allowed to the patient at a second rate of the payer)"
    TQ_ = dict(payer="TESTPAY TP2", pid="T0044")
    n_ = 0
    for npi, amts in (("1000000051", [11274] * 10), ("1000000052", [11274] * 6 + [7862] * 4),
                      ("1000000053", [11274] * 5 + [7862] * 4)):
        for amt in amts:
            n_ += 1
            F.claim(F.tx("2026-0%d-%02d" % (3 + n_ % 5, 1 + n_ % 27), **TQ_), "ZZ55TP%03dC017" % n_, "1", 15286, amt,
                    [L_("11102", 15286, amt, "2026-02-%02d" % (1 + n_ % 27), allowed=amt, npi=npi,
                        cas=[("CO", "45", 15286 - amt)])], **TQ_)
    F.claim(F.tx("2026-10-30", **TQ_), "ZZ55TPXXXC017", "1", 15286, 0, [
        L_("11102", 15286, 0, "2026-10-09", allowed=7862, npi="1000000051", cas=[("PR", "1", 7862), ("CO", "45", 7424)])],
        **TQ_)
    F.exp("unit", ("ZZ55TPXXX", "11102", "G", "2026-10-09"), "MOVED_TO_PATIENT")
    F.fix = "fix 4 / round 3 (another payer's payment settles whatever the order of the answers)"
    TA_ = dict(payer="TESTPAY ANT", pid="T0045")
    TX2 = dict(payer="TESTPAY AUX", pid="T0046")
    F.claim(F.tx("2026-04-18", **TA_), "ZZ55AUXA1C017", "1", 19353, 0, [
        L_("99203", 19353, 0, "2026-04-01", allowed=7922, cas=[("PR", "1", 7922), ("CO", "45", 11431)])], **TA_)
    F.claim(F.tx("2026-07-30", **TX2), "ZZ55AUXA1C024", "1", 19353, 4092, [
        L_("99203", 19353, 4092, "2026-04-01", allowed=8592, cas=[("PR", "3", 4500), ("CO", "45", 10761)])], **TX2)
    F.claim(F.tx("2026-08-28", **TA_), "ZZ55AUXA1C031", "4", 19353, 0, [
        L_("99203", 19353, 0, "2026-04-01", cas=[("CO", "29", 19353)])], **TA_)
    F.exp("unit", ("ZZ55AUXA1", "99203", "G", "2026-04-01"), "PAID", guard=True)
    F.fix = "fix 5 / round 3 (the lower surgical line: owed the multiple-procedure half, not the full rate)"
    TMQ = dict(payer="TESTPAY MP2", pid="T0047")
    for i in range(6):
        F.claim(F.tx("2026-0%d-%02d" % (3 + i % 5, 2 + i), **TMQ), "ZZ55MQ%03dC017" % i, "1", 16772, 12983, [
            L_("11402", 16772, 12983, "2026-02-%02d" % (2 + i), allowed=12983, cas=[("CO", "45", 3789)])], **TMQ)
    F.claim(F.tx("2026-10-30", **TMQ), "ZZ55MQXX1C017", "1", 40146, 30244, [
        L_("11403", 23374, 25000, "2026-10-10", allowed=25000, cas=[("CO", "45", -1626)]),
        L_("11402", 16772, 5244, "2026-10-10", allowed=5244, cas=[("CO", "45", 11528)])], **TMQ)
    F.exp("sub", ("ZZ55MQXX1", "11402", "G", "2026-10-10"), ("SHORT", "below_rate"), guard=True)
    F.exp("owed", ("ZZ55MQXX1", "11402", "G", "2026-10-10"), 1248, guard=True)
    F.claim(F.tx("2026-10-31", **TMQ), "ZZ55MQXX2C017", "1", 40146, 35000, [
        L_("11403", 23374, 25000, "2026-10-11", allowed=25000, cas=[("CO", "45", -1626)]),
        L_("11402", 16772, 10000, "2026-10-11", allowed=10000, cas=[("CO", "45", 6772)])], **TMQ)
    F.exp("unit", ("ZZ55MQXX2", "11402", "G", "2026-10-11"), "PAID")
    F.fix = "fix 5 / round 3 (a multiple-procedure modifier the payer applied, SVC06 without modifiers)"
    T51 = dict(payer="TESTPAY M51", pid="T0048")
    for i in range(6):
        F.claim(F.tx("2026-0%d-%02d" % (3 + i % 5, 3 + i), **T51), "ZZ55M5%03dC017" % i, "1", 10733, 7345, [
            L_("17000", 10733, 7345, "2026-02-%02d" % (3 + i), allowed=7345, cas=[("CO", "45", 3388)])], **T51)
    F.claim(F.tx("2026-10-31", **T51), "ZZ55M5XXXC017", "1", 10733, 3673, [
        L_("17000", 10733, 3673, "2026-10-12", mods=["51"], ocode="17000", allowed=3673, cas=[("CO", "45", 7060)])],
        **T51)
    F.exp("sub", ("ZZ55M5XXX", "17000", "G", "2026-10-12"), ("PAID", "full_mppr"), guard=True)
    F.fix = "fix 6 / round 4 (RARC N448: the code is not in the payer's fee schedule)"
    TWC = dict(payer="TESTPAY WCM", pid="T0049")
    ck, _ = F.claim(F.tx("2026-10-20", **TWC), "ZZ55N448XC017", "1", 25284, 6039, [
        L_("99214", 22365, 6039, "2026-10-05", allowed=6039, cas=[("CO", "45", 16326)]),
        L_("G2211", 2919, 0, "2026-10-05", cas=[("CO", "96", 2919)])], prod="MC", **TWC)
    lk_g = F.con.execute("select line_key from lines where claim_key=? and procedure_code='G2211'", (ck,)).fetchone()[0]
    F.con.execute("insert into remarks (claim_key, line_key, level, code) values (?,?,?,?)", (ck, lk_g, "line", "N448"))
    F.exp("sub", ("ZZ55N448X", "G2211", "G", "2026-10-05"), ("NOT_PAYABLE", "code_not_in_payer_fee_schedule"))
    F.fix = "fix 1 / round 4 (EMA shows the recoupment and a later payment of the service)"
    F.claim(F.tx("2026-06-14"), "ZZ55RPAY1C017", "1", 8952, 3434, [
        L_("99213", 8952, 3434, "2026-06-01", allowed=8504, cas=[("CO", "45", 448), ("PR", "3", 5000), ("CO", "253", 70)])])
    plb_("2026-08-07", 3434, "ZZ55RPAY1C017")
    F.ema_line(900401, "2026-06-01", "99213", [(3, "charge", 8952, T1, None), (23, "pay", 3434, T1, "CHKRP1"),
                                               (73, "pay", -3434, T1, "CHKRP2"), (98, "pay", 3434, T1, "CHKRP3")],
               bill="ZZ55RPAY1")
    F.ema_check("CHKRP3", T1, "none")
    F.exp("sub", ("ZZ55RPAY1", "99213", "G", "2026-06-01"), ("PAID", "ema_repaid_after_takeback"))
    F.fix = None
    F.con.commit()
    return F


def evaluate(F, a):
    """Every check of the self-test against an auditor's results: [(check, fix label or 'A15 base', passed, detail)].
    Works on A15's Audit objects too (selftest_vs_a15.py)."""
    out = []

    def add(kind, key, ok, detail):
        out.append((f"{kind} {key[1] if isinstance(key, tuple) else key}", F.fix_of.get((kind, key), "A15 base"), ok,
                    detail))
    for key, want in F.expect_unit.items():
        sv = a.svcs.get(key)
        got = sv.units[0][0] if sv and sv.units else None
        add("unit", key, got == want, f"{key[0]} {key[1]}/{key[2]} expected {want} got {got}")
    for key, want in F.expect_sub.items():
        sv = a.svcs.get(key)
        got = tuple(sv.units[0][:2]) if sv and sv.units else None
        add("sub", key, got == tuple(want), f"{key[0]} {key[1]} expected {want} got {got}")
    for fid, want in F.expect_item.items():
        got = a.verdicts[fid]["verdict"]
        add("item", fid, got == want, f"item {fid[-4:]} expected {want} got {got} ({a.verdicts[fid]['sub']})")
    lot_keys = {sv.key for sv, i, u in a.lot}
    base_lot = ((("ZZ09HHHH1", "99213", "G", "2026-05-28"), True), (("ZZ16OOOO1", "99213", "G", "2026-09-15"), True),
                (("ZZ15NNNN1", "99213", "G", "2025-12-05"), False), (("ZZ09HHHH3", "99213", "G", "2026-05-30"), False),
                (("ZZ09HHHH2", "99213", "G", "2026-05-29"), False), (("ZZ03BBBB1", "99213", "G", "2026-04-02"), False))
    for key, want_in in base_lot:
        out.append((f"lot {key[1]}", "A15 base", (key in lot_keys) == want_in, f"left-on-table {key[0]} expected {want_in}"))
    for key, want_in in F.expect_lot.items():
        add("lot", key, (key in lot_keys) == want_in, f"left-on-table {key[0]} {key[1]} expected {want_in}")
    rev_keys = {sv.key for sv, i, u in getattr(a, "review_units", [])}
    for key, want_in in F.expect_review.items():
        add("review", key, (key in rev_keys) == want_in, f"provider review {key[0]} {key[1]} expected {want_in}")
    for key, want in F.expect_owed.items():
        sv = a.svcs.get(key)
        got = sv.units[0][6] if sv and sv.units else None
        add("owed", key, got == want, f"owed per unit {key[0]} {key[1]} expected {want} got {got}")
    sb_keys = {x[0].key for x in getattr(a, "sec_bal", [])}
    for key, want_in in F.expect_secbal.items():
        add("secbal", key, (key in sb_keys) == want_in, f"secondary balance {key[0]} {key[1]} expected {want_in}")
    for key, want in F.expect_units_billed.items():
        sv = a.svcs.get(key)
        add("billed", key, sv is not None and sv.U == want, f"units billed {key[0]} expected {want} got "
                                                            f"{sv.U if sv else None}")
    for key, want in F.expect_unit_counts.items():
        sv = a.svcs.get(key)
        got = dict(Counter(u[0] for u in sv.units)) if sv else {}
        add("counts", key, got == want, f"unit states {key[0]} {key[1]} expected {want} got {got}")
    out.append(("copies", "A15 base", a.stats.get("copy_same_check") == 3,
                f"duplicate delivery expected 3 copies got {a.stats.get('copy_same_check')}"))
    out.append(("reversals", "A15 base", a.stats.get("reversal_line_not_on_file") == 2,
                f"reversals not on file expected 2 got {a.stats.get('reversal_line_not_on_file')}"))
    return out


def selftest():
    F = build_selftest_store()
    F.expect_units_billed[("ZZ11JJJJ1", "11103", "G", "2026-08-04")] = 2
    raw = load(F.con)
    a = Audit(raw, "2026-11-23", log=lambda *x: None).run()
    res = evaluate(F, a)
    fails = [r for r in res if not r[2]]
    by_fix = defaultdict(lambda: [0, 0])
    for name, fix, ok, detail in res:
        by_fix[fix][0] += 1
        by_fix[fix][1] += ok
    for fix in sorted(by_fix, key=lambda f: (f != "A15 base", f)):
        print(f"  {fix:62s} {by_fix[fix][1]:3d}/{by_fix[fix][0]:3d} passed")
    if fails:
        print("SELFTEST FAILED:")
        for name, fix, ok, detail in fails:
            print("   ", fix, "|", detail)
        return 1
    print(f"SELFTEST PASSED: {len(res)} checks ({sum(1 for r in res if r[1] == 'A15 base')} from A15, "
          f"{sum(1 for r in res if r[1] != 'A15 base')} new in A16)")
    return 0


# ------------------------------------------------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db")
    ap.add_argument("--ledger", help="EMA's Production posting ledger (default: ema_ledger.sqlite or "
                                     "world_ema_ledger.sqlite beside the store, when there)")
    ap.add_argument("--out")
    ap.add_argument("--as-of", default="2026-11-23")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.db or not args.out:
        ap.error("--db and --out are required (or --selftest)")
    con = open_ro(args.db)
    led = args.ledger
    if not led:
        d = os.path.dirname(os.path.abspath(args.db))
        for nm in ("ema_ledger.sqlite", "world_ema_ledger.sqlite"):
            if os.path.exists(os.path.join(d, nm)):
                led = os.path.join(d, nm)
                break
    lcon = open_ro(led) if led else None
    raw = load(con, lcon)
    a = Audit(raw, args.as_of).run()
    path, spath, summary = write_outputs(a, args.out)
    print(json.dumps({"verdicts": summary["verdicts"], "left_on_table": summary["left_on_table"],
                      "feed_date": summary["feed_date"]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
