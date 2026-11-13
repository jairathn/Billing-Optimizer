"""
Detection engine: turns the raw remittance store into a table of findings.

Each finding is one thing a payer did that cost the practice money (or one
thing the practice did that let a payer keep money), with the evidence needed
to act on it. Findings are idempotent: re-running detection keeps the status
(open / appealed / resolved / dismissed) of findings that already exist.

Signals (category: signal)
--------------------------
downcoding   DOWNCODE_SVC06            payer reported the submitted code (SVC06) and paid a lower level
             DOWNCODE_VS_BILLED        the claim file or EMA shows a higher level was billed than the code on the remit
             DOWNCODE_CHARGE           charge amount is the practice's fee for a higher level than the paid code
             DOWNCODE_LEVEL_REDUCED    CARC 186/150 or a level remark on a paid visit: payment cut for the level, code unchanged
             DOWNCODE_RATE             allowed equals the payer's usual rate for a lower level
             DOWNCODE_LINE_SWAP        billed visit denied and a lower-level visit line paid in its place
             DOWNCODE_LEVEL_DENIED     CARC 150/186 or a level remark on an unpaid visit
             CODE_CHANGED              payer changed the code (SVC06 or remark N22) outside the known level families
bundling     EM_BUNDLED_NO_MODIFIER    E/M paid $0 as bundled and no modifier 25/57/24 was billed (correct and resubmit)
             EM_BUNDLED_MOD57_MINOR    E/M-57 bundled; same-day procedures are 0/10-day global (57 is wrong, use 25)
             EM_BUNDLED_MOD57_MAJOR    E/M-57 bundled; a 90-day global procedure was done the same day (appeal)
             EM_BUNDLED_MOD25          E/M-25 bundled (appeal: significant, separately identifiable)
             EM_BUNDLED_MOD24          E/M-24 denied inside a global period (appeal with documentation)
             ADDON_BUNDLED             add-on code (17312, 11103, 17003...) paid $0 as bundled (payer error)
             PROCEDURE_BUNDLED         a procedure (Mohs stage, flap, repair) bundled into another (appeal with NCCI)
denial       DENIED_MISSING_MODIFIER   CO-16 with RARC N822/N823/N519: correct and resubmit
             DENIED_CODING             CO-4/181/182/M20/M51/N56: code or modifier error, correct and resubmit
             DENIED_MISSING_PRIMARY    CO-B15/107: add-on billed without a payable primary
             DENIED_MISSING_INFO       CO-16/226/227/250/251/252: resubmit with the requested information
             DENIED_AUTH               CO-197/198/15/39/210/243: authorization failure
             DENIED_LEVEL_OF_SERVICE   CO-150/186 on a procedure: payer says the records do not support the service as billed
             DENIED_MEDICAL_NECESSITY  CO-50/B12/B22/167/A1/272: appeal with records
             DENIED_FREQUENCY          CO-151/119/222/273/B14: appeal with records
             DENIED_NONCOVERED         CO-96/204/49/55/200...: verify benefits, bill patient where allowed
             DENIED_TIMELY_FILING      CO-29
             DENIED_PROVIDER           CO-B7/170/171/185/208: enrollment or credentialing failure
             DENIED_DUPLICATE_UNPAID   OA-18/B13 and the same line was never paid on any remit
             DENIED_OTHER              paid $0 with another payer-responsibility CARC
             CLAIM_DENIED_UNPAID       whole claim denied (CLP02 = 4) and never paid later
             (a denial is closed when the service is paid later: on the same bill, on a corrected claim under a new bill
             number or to another payer for the same patient, date and code, or when EMA shows the payment first)
underpayment UNDERPAID_VS_OWN_RATE     allowed below the payer's own usual rate (rank-aware) for this code and tier, at an
                                       amount the payer does not use routinely (a one-off)
             MPPR_EXCESS               multiple-procedure reduction on an add-on code or deeper than policy
fees         FEE_SKIM                  PLB fees withheld (Zelis transaction fees, origination fees)
             TAKEBACK                  PLB WO overpayment recovery above a threshold
contract     CONTRACT_RATE_GAP         commercial payer's usual rate far below Medicare for a high-volume code
             MA_BELOW_MEDICARE         Medicare Advantage payer's usual rate below the Medicare fee schedule
             MPPR_POLICY               payer routinely pays later procedures below the standard 50 percent
             CHARGE_BELOW_RATE         the practice's charge is below what the payer allows, so the payer paid the charge
             SECOND_RATE               payer routinely allows a lower amount for a code (PA/NP rate, another office's plan,
                                       multiple-procedure rule, a rate change): one finding per payer, code, year and amount
info         G2211_DENIED              per payer and year: G2211 denied most of the time
             MIPS_ADJUSTMENT           per year: net MIPS adjustment (CARC 144)
             SEQUESTRATION             per year: sequestration (CARC 253)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

from .codes import (ADD_ON_CODES, AGREEMENT_FILING_NOTE, EM_CODES, EM_LEVEL, GLOBAL_DAYS, MEDICARE_SAME_DAY_EM, appeal_rule, carc_text,
                    code_category, corrected_rule, normalize_payer, payer_group_kind, pos_class, rarc_text)
from .fees import FeeTable, family_of, is_downcode
from .store import Store

DETECT_RULES = "2.3"


def _rules_hash() -> str:
    """This file and the ones whose rules it applies: payer windows and reason codes (codes.py), fee families
    (fees.py), and how remittance lines are tied to EMA rows (link.py)."""
    h = hashlib.sha256()
    here = os.path.dirname(os.path.abspath(__file__))
    for name in ("detect.py", "codes.py", "fees.py", "link.py"):
        with open(os.path.join(here, name), "rb") as fh:
            h.update(fh.read())
    return h.hexdigest()[:8]


# The version recorded with each run, compared on the next cycle: any change to these files re-analyses the archive once.
DETECT_VERSION = DETECT_RULES + "-" + _rules_hash()
DERIVED_SCHEMA = "3"        # bump when the layout of a derived table changes; the tables are dropped and rebuilt

BUNDLING_CARCS = {"97", "B10", "236", "234", "231", "M15"}
LEVEL_CARCS = {"150", "186"}
CODING_CARCS = {"4", "181", "182", "5", "6", "11", "146", "199"}
PRIMARY_CARCS = {"B15", "107"}
INFO_CARCS = {"16", "226", "227", "250", "251", "252", "133"}
AUTH_CARCS = {"197", "198", "15", "39", "210", "243"}
MEDNEC_CARCS = {"50", "B12", "B22", "167", "56", "A1", "B5", "272", "58", "216"}
FREQ_CARCS = {"151", "119", "222", "273", "B14", "35"}
NONCOV_CARCS = {"96", "204", "49", "55", "B8", "200", "26", "27", "31", "109", "B11", "24", "256", "246", "112", "B9", "299", "193", "114", "189", "B20"}
TIMELY_CARCS = {"29"}
PROVIDER_CARCS = {"B7", "170", "171", "185", "208", "242", "54", "147"}
DUP_CARCS = {"18", "B13"}
IGNORE_CARCS = {"45", "253", "144", "23", "94", "187", "59", "131", "237", "A2", "P1", "W1", "3", "2", "1"}
MODIFIER_RARCS = {"N822", "N823", "N519", "M78", "N793"}
CODING_RARCS = {"M20", "M51", "N56", "M53"}
LEVEL_RARCS = {"M25", "N657", "N610", "M85", "N22"}     # level-of-service remarks; only meaningful on visit codes
PRICING_MODIFIERS = {"26", "TC", "50", "52", "53", "54", "55", "56", "62", "66", "80", "81", "82", "AS", "22", "51", "73", "74", "FY", "GY"}
DOWNCODE_SIGNALS = ("DOWNCODE_SVC06", "DOWNCODE_VS_BILLED", "DOWNCODE_CHARGE", "DOWNCODE_LEVEL_REDUCED", "DOWNCODE_RATE",
                    "DOWNCODE_LINE_SWAP", "DOWNCODE_LEVEL_DENIED")
MANUAL_STATUSES = ("in_progress", "appealed", "lost")

# columns added after the first release; created by ALTER TABLE on older stores
EXTRA_FINDING_COLS = {
    "ema_handling": "TEXT", "billed_code": "TEXT", "code_pair": "TEXT", "level_drop": "INTEGER", "pos": "TEXT", "pos_class": "TEXT",
    "plan": "TEXT", "month": "TEXT", "service_month": "TEXT", "modifier_class": "TEXT", "location": "TEXT", "diagnosis": "TEXT",
    "deadline_basis": "TEXT", "recovered_cents": "INTEGER", "recovered_date": "TEXT", "appealed_at": "TEXT", "status_changed_at": "TEXT",
    "stale": "INTEGER DEFAULT 0", "auto_note": "TEXT", "prev_status": "TEXT", "hint": "TEXT",
}
EM_SQL = ",".join(f"'{c}'" for c in sorted(EM_CODES))
AUTO_NOTE_PREFIX = "Paid on a later remittance"

FINDINGS_DDL = """
CREATE TABLE IF NOT EXISTS findings (
    finding_id TEXT PRIMARY KEY,
    signal TEXT, category TEXT, severity TEXT, actionable TEXT, title TEXT,
    line_key TEXT, claim_key TEXT, transaction_key TEXT, patient_control_number TEXT,
    payer_name TEXT, payer_group TEXT, payer_kind TEXT, payer_id TEXT,
    rendering_provider_npi TEXT, rendering_provider_name TEXT, provider_tier TEXT,
    service_date TEXT, payment_date TEXT, year TEXT,
    procedure_code TEXT, modifiers TEXT, original_procedure_code TEXT, code_category TEXT,
    charge_cents INTEGER, allowed_cents INTEGER, paid_cents INTEGER, expected_cents INTEGER, shortfall_cents INTEGER,
    carc TEXT, carc_text TEXT, rarc TEXT, rarc_text TEXT,
    evidence TEXT, appeal_deadline TEXT, ema_handling TEXT,
    status TEXT DEFAULT 'open', notes TEXT, run_id TEXT, first_seen TEXT, last_seen TEXT
);
CREATE INDEX IF NOT EXISTS ix_f_signal ON findings(signal);
CREATE INDEX IF NOT EXISTS ix_f_payer ON findings(payer_group);
CREATE INDEX IF NOT EXISTS ix_f_date ON findings(service_date);
CREATE INDEX IF NOT EXISTS ix_f_status ON findings(status);
CREATE INDEX IF NOT EXISTS ix_f_pcn ON findings(patient_control_number);
CREATE INDEX IF NOT EXISTS ix_f_claim ON findings(claim_key);
CREATE INDEX IF NOT EXISTS ix_f_cat ON findings(category);

CREATE TABLE IF NOT EXISTS payer_rates (
    payer_group TEXT, payer_kind TEXT, year TEXT, code TEXT, tier TEXT,
    mode_cents INTEGER, median_cents INTEGER, p10_cents INTEGER, p90_cents INTEGER, n INTEGER, mode_share REAL,
    medicare_cents INTEGER, pct_of_medicare REAL, pos_class TEXT DEFAULT 'office',
    PRIMARY KEY (payer_group, year, code, tier, pos_class)
);
CREATE TABLE IF NOT EXISTS exposure (
    month TEXT, payer_group TEXT, payer_kind TEXT, npi TEXT, tier TEXT, code TEXT, pos_class TEXT, plan TEXT,
    lines INTEGER, zero_paid INTEGER, charge_cents INTEGER, allowed_cents INTEGER, paid_cents INTEGER,
    PRIMARY KEY (month, payer_group, payer_kind, npi, tier, code, pos_class, plan)
);
CREATE INDEX IF NOT EXISTS ix_exp_payer ON exposure(payer_group, month);
CREATE TABLE IF NOT EXISTS exposure_daily (
    day TEXT, payer_group TEXT, payer_kind TEXT, npi TEXT, scope TEXT, lines INTEGER,
    PRIMARY KEY (day, payer_group, payer_kind, npi, scope)
);
CREATE TABLE IF NOT EXISTS rate_points (
    day TEXT, payer_group TEXT, payer_kind TEXT, code TEXT, tier TEXT, pos_class TEXT, allowed_cents INTEGER, n INTEGER,
    PRIMARY KEY (day, payer_group, payer_kind, code, tier, pos_class, allowed_cents)
);
CREATE TABLE IF NOT EXISTS finding_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, finding_id TEXT, at TEXT, kind TEXT, from_status TEXT, to_status TEXT,
    amount_cents INTEGER, actor TEXT, note TEXT, run_id TEXT
);
CREATE INDEX IF NOT EXISTS ix_fe_finding ON finding_events(finding_id);
CREATE INDEX IF NOT EXISTS ix_fe_at ON finding_events(at);
CREATE TABLE IF NOT EXISTS progress_snapshots (
    run_id TEXT, at TEXT, category TEXT, status TEXT, n INTEGER, cents INTEGER, PRIMARY KEY (run_id, category, status)
);
CREATE TABLE IF NOT EXISTS fee_table (year TEXT, code TEXT, charge_cents INTEGER, share REAL, PRIMARY KEY (year, code, charge_cents));
CREATE TABLE IF NOT EXISTS provider_tiers (npi TEXT PRIMARY KEY, name TEXT, tier TEXT, ratio REAL, n INTEGER);
CREATE TABLE IF NOT EXISTS providers (npi TEXT PRIMARY KEY, name TEXT, tier TEXT, lines INTEGER, location TEXT);
CREATE TABLE IF NOT EXISTS detection_runs (run_id TEXT PRIMARY KEY, started_at TEXT, finished_at TEXT, version TEXT, lines INTEGER, findings INTEGER, summary TEXT);
"""


def _fid(*parts) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:24]


def _add_days(iso: str | None, days: int) -> str | None:
    if not iso:
        return None
    try:
        return (date.fromisoformat(iso) + timedelta(days=days)).isoformat()
    except ValueError:
        return None


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the derived tables and add columns introduced after the first release. Safe to call on every start."""
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    cur = conn.execute("SELECT value FROM meta WHERE key='derived_schema'").fetchone()
    if not cur or cur[0] != DERIVED_SCHEMA:
        for t in ("payer_rates", "exposure", "exposure_daily", "rate_points"):      # derived data, rebuilt by every detection run
            conn.execute(f"DROP TABLE IF EXISTS {t}")
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('derived_schema', ?)", (DERIVED_SCHEMA,))
    conn.executescript(FINDINGS_DDL)
    have = {r[1] for r in conn.execute("PRAGMA table_info(findings)")}
    for col, decl in EXTRA_FINDING_COLS.items():
        if col not in have:
            conn.execute(f"ALTER TABLE findings ADD COLUMN {col} {decl}")
    if "auto_note" not in have:     # older stores kept the automatic note in the notes column
        conn.execute(f"UPDATE findings SET auto_note = notes, notes = NULL WHERE notes LIKE '{AUTO_NOTE_PREFIX}%'")
    conn.executescript("""CREATE INDEX IF NOT EXISTS ix_f_month ON findings(month);
                          CREATE INDEX IF NOT EXISTS ix_f_npi ON findings(rendering_provider_npi);""")
    conn.commit()


ORG_NAME_WORDS = ("DERMATOLOG", "ASSOC", " PSC", "CENTER", "CLINIC", "SURGERY", "DERMA")


def best_provider_name(names: Counter) -> str:
    """Payers spell the rendering provider many ways and some send the group name; pick the commonest personal name."""
    clean = Counter()
    for n, k in names.items():
        n = " ".join((n or "").replace(",", ", ").split())
        if n:
            clean[n] += k
    personal = Counter({n: k for n, k in clean.items() if not any(w in n.upper() for w in ORG_NAME_WORDS)})
    pick = personal or clean
    return top_of(pick)[0] if pick else ""


def modifier_class(mods: list[str]) -> str:
    for m in ("25", "57", "24", "59", "XS", "XE", "XU", "XP", "76", "79"):
        if m in mods:
            return m
    return "other" if mods else "none"


LINE_COLUMNS = ("line_key", "claim_key", "transaction_key", "procedure_code", "modifiers", "charge_cents", "paid_cents", "paid_units",
                "original_procedure_code", "original_modifiers", "original_units", "service_date", "allowed_cents",
                "patient_control_number", "claim_status_code", "payer_name", "payer_id", "payment_date", "rendering_provider_npi",
                "claim_filing_indicator", "contract_class", "facility_type_code", "rp_name", "txn_payment_date", "trace_number")
# A line with no service date of its own (no DTM*472) takes a one-day period of its own or of its claim (DTM*150/151,
# DTM*232/233) in load(); one that covers several days stays undated, and an undated line is never matched to a payment.
# l.get("statement_from") is not a column here and always comes back empty.
LINE_DERIVED = ("modifiers_list", "year", "payer_group", "payer_kind", "pricing_mod", "pos", "pos_class", "plan", "month",
                "provider_tier", "surg_rank", "surg_count", "clean")


class Line:
    """One service line with its claim and payment context. It behaves like the dict the rules were written against
    (l["x"], l.get("x"), l["x"] = v) but is a slotted object: with 400,000 lines in memory a dict each costs close to a
    gigabyte more, which is the difference between fitting a small serverless function and not."""
    __slots__ = LINE_COLUMNS + LINE_DERIVED

    def __getitem__(self, k):
        try:
            return getattr(self, k)
        except AttributeError:
            raise KeyError(k) from None

    def __setitem__(self, k, v) -> None:
        setattr(self, k, v)

    def get(self, k, default=None):
        return getattr(self, k, default)

    def setdefault(self, k, v):
        try:
            return getattr(self, k)
        except AttributeError:
            setattr(self, k, v)
            return v

    def keys(self):
        return [k for k in self.__slots__ if hasattr(self, k)]

    def __contains__(self, k) -> bool:
        return hasattr(self, k)


exec("def _line_init(self, r):\n" + "".join(f"    self.{n} = r[{i}]\n" for i, n in enumerate(LINE_COLUMNS)))
Line.__init__ = _line_init      # noqa: F821  (generated above: one bytecode store per column is several times faster than a loop)


def top_of(cnt: Counter) -> tuple:
    """(value, count) of the most common value; on a tie the first in sorted order, never the first that happened to be read."""
    return min(cnt.items(), key=lambda kv: (-kv[1], str(kv[0])))


def usual_amount(cnt: Counter) -> tuple[int, int]:
    """The most common amount in a group and how many lines carry it. When two amounts are equally common the answer
    must not depend on the order the files were read in, so the one nearest the middle of the group wins, then the lower."""
    top = max(cnt.values())
    tied = [v for v, k in cnt.items() if k == top]
    if len(tied) == 1:
        return tied[0], top
    vals = sorted(cnt.elements())
    mid = vals[len(vals) // 2]
    return min(tied, key=lambda v: (abs(v - mid), v)), top


def is_surgical(code: str | None) -> bool:
    return bool(code) and code in GLOBAL_DAYS and code not in ADD_ON_CODES


SECOND_RATE_MIN_LINES = 5        # a lower amount the payer allowed this often for a code is a rule of the payer's, not a one-off
STATE_NAMES = {"KY": "Kentucky", "IN": "Indiana"}


def patient_keys(pid: str | None, last: str | None, first: str | None) -> tuple[str | None, str | None]:
    """(same person, same name). The first needs the member number AND the name, the rule the dashboard uses for "this
    patient": one member number often covers a family, and two people can share a name. The second, the name alone,
    is used only when EMA ties both claims to one patient (a claim sent to another payer has another member number)."""
    norm = lambda v: "".join(ch for ch in (v or "").upper() if ch.isalnum())
    p, name = norm(pid), (norm(last) + "|" + norm(first)) if norm(last) and norm(first) else None
    return (f"P:{p}|{name}" if len(p) >= 6 and name else None), (f"N:{name}" if name else None)


def second_rate_reason(ratio: float, key_tier: str, tiers: Counter, states: Counter, usual_states: Counter, first_dos: str | None,
                       usual_last_dos: str | None, plans: Counter | None = None, usual_plans: Counter | None = None,
                       surgical_with_another: float = 0.0, unnamed: int | None = None, clinician_paid_usual: bool = False) -> tuple[str, bool]:
    """The likely explanation for a payer's routine lower amount, in plain words, and whether the remittances support one.
    A reason must hold for at least 80 percent of the lines (a line whose office or plan is unknown counts against it).
    Without support the lines stay one-off underpayments to appeal.
    `unnamed`: lines whose remittance names no rendering clinician (a line with a named clinician of unknown type is not
    taken for a PA or NP). `clinician_paid_usual`: a clinician on these lines is paid the usual rate on other visits."""
    n = sum(tiers.values()) or 1
    parts = []
    npp = tiers.get("nonphysician", 0) + (tiers.get("unknown", 0) if unnamed is None else unnamed)
    if 0.83 <= ratio <= 0.87 and key_tier in ("physician", "unknown") and npp / n >= 0.8 and not clinician_paid_usual:
        parts.append("85 percent of the usual rate is what many plans pay a physician assistant or nurse practitioner"
                     + (f"; {tiers.get('nonphysician', 0)} of these {n} lines are by a PA or NP" if tiers.get("nonphysician")
                        else "; the remittances do not name the clinician on these lines"))
    if 0.48 <= ratio <= 0.52 and surgical_with_another >= 0.8:
        parts.append("Half the usual rate, with another procedure for the patient that day: how most payers pay a second procedure "
                     "(the multiple-procedure rule)")
    known = {k: v for k, v in states.items() if k in STATE_NAMES}
    uknown = {k: v for k, v in usual_states.items() if k in STATE_NAMES}
    kn, un = sum(known.values()), sum(uknown.values())
    if kn >= 3 and un >= 3:
        for st in sorted(known, key=known.get, reverse=True):
            here, there = known[st] / n, uknown.get(st, 0) / un
            # most of that office's own lines are at this amount: its rate, not the payer's usual rate missed on them
            if here >= 0.8 and here - there >= 0.4 and known[st] / (known[st] + uknown.get(st, 0)) >= 0.8:
                parts.append(f"{known[st]} of the {n} lines are {STATE_NAMES[st]} visits, against {there:.0%} of the lines "
                             f"paid at the usual rate: probably that office's plan or fee schedule")
                break
    pn, upn = sum((plans or {}).values()), sum((usual_plans or {}).values())
    if pn >= 3 and upn >= 3:
        for plan, v in plans.most_common(3):
            if plan in ("", "(none)"):
                continue
            here, there = v / pn, usual_plans.get(plan, 0) / upn
            if here >= 0.8 and here - there >= 0.25 and v / (v + usual_plans.get(plan, 0)) >= 0.8:
                parts.append(f"{v} of the {pn} lines are under the plan \"{plan}\", which is on {there:.0%} of the lines paid at the usual rate")
                break
    if first_dos and usual_last_dos and first_dos > usual_last_dos:
        parts.append(f"Every line is from {first_dos} on, after the last visit paid at the usual rate ({usual_last_dos}): a rate change")
    if not parts:
        return "", False
    parts.append("Check it against the contract before appealing; if the payer is wrong, one request can fix every claim listed "
                 "that is still inside its filing window")
    return ". ".join(parts) + ".", True


def pattern_windows(conn: sqlite3.Connection, statuses: tuple = ("open",)) -> list[tuple]:
    """Every claim filing deadline of the routine-rate findings (SECOND_RATE) with these statuses, as (finding_id,
    payer_group, procedure_code, deadline, lines, cents of shortfall). Such a finding stands for many claims with their own
    deadlines, so the deadline views (summary, the due filter, Progress, the deadline alert, the weekly report) count its
    claims rather than the single date on its row. A deadline of None is a line whose window is unknown."""
    q = ",".join("?" * len(statuses))
    out = []
    for fid, grp, code, ev in conn.execute(f"""SELECT finding_id, payer_group, procedure_code, evidence FROM findings
                                              WHERE signal='SECOND_RATE' AND COALESCE(stale,0)=0 AND status IN ({q})""", tuple(statuses)):
        try:
            dl = (json.loads(ev) if ev else {}).get("deadlines") or []
        except ValueError:
            dl = []
        out += [(fid, grp, code, x[0], x[1] or 0, x[2] or 0) for x in dl if isinstance(x, list) and len(x) == 3]
    return out


_EMA_CLAIM_ID = re.compile(r"([A-Z]{2}\d{2}[A-Z0-9]{5,6})C\d{0,3}")


def bill_of(pcn: str | None) -> str:
    """EMA claim ids are a bill id (two letters, two digits and five more characters) plus a submission suffix (C017,
    C024, ...). Any other claim id is its own bill: ids such as a date and a sequence number share their first characters
    across patients."""
    m = _EMA_CLAIM_ID.fullmatch(pcn or "")
    return m.group(1) if m else (pcn or "")


class Detector:
    def __init__(self, store: Store, run_id: str | None = None):
        self.store = store
        self.conn = store.conn
        ensure_schema(self.conn)
        self.run_id = run_id or datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%SZ")
        self.now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.findings: dict[str, dict] = {}
        self.second_rates: dict[tuple, list] = defaultdict(list)     # (baseline key, amount) -> lines the payer routinely allowed less
        self.ema_paid: dict[tuple, tuple] = {}                        # (claim id, code, date) -> (insurance payments in EMA, export)
        self.ema_patient: dict[str, str] = {}                         # claim id -> EMA patient id (full charge layout only)
        self.worked_underpaid: set[str] = {r[0] for r in store.conn.execute(
            """SELECT line_key FROM findings WHERE signal='UNDERPAID_VS_OWN_RATE' AND line_key IS NOT NULL
               AND (status != 'open' OR notes IS NOT NULL OR appealed_at IS NOT NULL)""")}       # their own finding is kept, never folded
        # A routine-rate finding someone worked keeps exactly the lines it had when they worked it, whatever the rules now say
        # about its amount or reason; lines paid at that amount later go to a new finding.
        # whole-claim denials already listed: a bill's finding stays on the submission that has it (and the work on it)
        self.prev_claims: dict[str, dict] = {}
        manual_at = dict(store.conn.execute("""SELECT finding_id, MAX(at) FROM finding_events WHERE kind='manual' GROUP BY finding_id""").fetchall()) \
            if store.conn.execute("SELECT 1 FROM sqlite_master WHERE name='finding_events'").fetchone() else {}
        for fid, ck, st, notes, appealed, auto, before in store.conn.execute(
                """SELECT finding_id, claim_key, status, notes, appealed_at, auto_note, prev_status FROM findings
                   WHERE signal='CLAIM_DENIED_UNPAID' AND claim_key IS NOT NULL AND COALESCE(stale,0)=0"""):
            # worked: something a person did (an automatic closure is not, but what they did before it still is); changed:
            # when they last changed its status
            worked = bool(notes) or bool(appealed) or st in ("in_progress", "appealed", "lost", "dismissed") or (st == "resolved" and not auto) \
                or (st == "resolved" and bool(auto) and before in ("in_progress", "appealed"))
            self.prev_claims[ck] = {"worked": worked, "changed": manual_at.get(fid) or "", "status": st}
        self.closed_lines: set[str] = {r[0] for r in store.conn.execute(
            "SELECT DISTINCT line_key FROM findings WHERE line_key IS NOT NULL AND status IN ('dismissed','lost')")}
        self.worked_patterns: dict[str, dict] = {}
        self.line_to_pattern: dict[str, str] = {}
        self.claim_to_pattern: dict[tuple, str] = {}    # (claim id, code, date, allowed): a remittance delivered again has new line keys
        self.pattern_lines: dict[str, list] = defaultdict(list)
        for fid, code, ev in store.conn.execute("""SELECT finding_id, procedure_code, evidence FROM findings WHERE signal='SECOND_RATE'
                                                   AND (status != 'open' OR notes IS NOT NULL OR appealed_at IS NOT NULL)"""):
            e = json.loads(ev) if ev else {}
            if e.get("line_keys") and e.get("key_parts"):
                self.worked_patterns[fid] = e
                for lk in e["line_keys"]:
                    self.line_to_pattern.setdefault(lk, fid)
                for cl_ in e.get("claims") or []:
                    if isinstance(cl_, list) and len(cl_) >= 3:
                        self.claim_to_pattern.setdefault((cl_[0], code, cl_[1], cl_[2]), fid)

    # ------------------------------------------------------------------ loading
    def load(self) -> None:
        c = self.conn
        c.row_factory = sqlite3.Row
        # only the columns the rules read, and repeated strings shared: this list is the bulk of the memory a run needs
        cur = c.execute("""
            SELECT l.line_key, l.claim_key, l.transaction_key, l.procedure_code, l.modifiers, l.charge_cents, l.paid_cents, l.paid_units,
                   l.original_procedure_code, l.original_modifiers, l.original_units,
                   COALESCE(l.service_date,
                            CASE WHEN l.service_period_start = COALESCE(l.service_period_end, l.service_period_start) THEN l.service_period_start END,
                            CASE WHEN cl.statement_from = COALESCE(cl.statement_to, cl.statement_from) THEN cl.statement_from END) AS service_date,
                   l.allowed_cents,
                   l.patient_control_number, l.claim_status_code, l.payer_name, l.payer_id, l.payment_date, l.rendering_provider_npi,
                   cl.claim_filing_indicator, cl.contract_class, cl.facility_type_code, cl.rendering_provider_name AS rp_name,
                   t.payment_date AS txn_payment_date, t.trace_number
            FROM lines l JOIN claims cl ON cl.claim_key = l.claim_key
            JOIN transactions t ON t.transaction_key = l.transaction_key
            WHERE t.superseded = 0
        """)
        names = [d[0] for d in cur.description]
        assert tuple(names) == LINE_COLUMNS
        shared = {"procedure_code", "modifiers", "original_procedure_code", "original_modifiers", "service_date", "claim_status_code",
                  "payer_name", "payer_id", "payment_date", "rendering_provider_npi", "claim_filing_indicator", "contract_class",
                  "facility_type_code", "rp_name", "txn_payment_date", "trace_number", "claim_key", "transaction_key",
                  "patient_control_number"}
        share_at = [i for i, n in enumerate(names) if n in shared]
        pool: dict[str, str] = {}
        self.lines = []
        while True:
            chunk = cur.fetchmany(20000)
            if not chunk:
                break
            for r in chunk:
                r = list(r)
                for i in share_at:
                    v = r[i]
                    if v is not None:
                        r[i] = pool.setdefault(v, v)
                self.lines.append(Line(r))
        for l in self.lines:
            l["modifiers_list"] = json.loads(l["modifiers"]) if l.get("modifiers") else []
            l["year"] = (l.get("service_date") or l.get("payment_date") or "")[:4]
            grp, kind = payer_group_kind(l.get("payer_name"), l.get("claim_filing_indicator"))
            l["payer_group"], l["payer_kind"] = grp, kind
            l["pricing_mod"] = next((m for m in l["modifiers_list"] if m in PRICING_MODIFIERS), None)
            l["pos"] = l.get("facility_type_code")
            l["pos_class"] = pos_class(l["pos"])
            l["plan"] = (l.get("contract_class") or "").strip() or None
            l["month"] = (l.get("payment_date") or l.get("txn_payment_date") or "")[:7]
        adj = defaultdict(list)
        for r in c.execute("SELECT line_key, group_code, reason_code, amount_cents FROM adjustments WHERE line_key IS NOT NULL"):
            adj[r[0]].append((pool.setdefault(r[1], r[1]), pool.setdefault(r[2], r[2]), r[3] or 0))
        self.adj = adj
        rem = defaultdict(list)
        for r in c.execute("SELECT line_key, code FROM remarks WHERE line_key IS NOT NULL"):
            rem[r[0]].append(pool.setdefault(r[1], r[1]))
        self.rem = rem
        del pool
        self.claim_adj = defaultdict(list)
        for r in c.execute("SELECT claim_key, group_code, reason_code, amount_cents FROM adjustments WHERE level='claim'"):
            self.claim_adj[r["claim_key"]].append((r["group_code"], r["reason_code"], r["amount_cents"] or 0))
        self.claim_rem = defaultdict(list)
        for r in c.execute("SELECT claim_key, code FROM remarks WHERE level='claim'"):
            self.claim_rem[r["claim_key"]].append(r["code"])
        self.lines_by_claim: dict[str, list[dict]] = defaultdict(list)
        for l in self.lines:
            self.lines_by_claim[l["claim_key"]].append(l)
        self.standing = self._build_ledger()
        self.partly_paid: dict[str, tuple] = {}         # line_key -> (first later payment, units paid, units billed)
        self.used_payments: set[tuple] = set()          # payments already counted as recovered on a finding
        paid_lines: Counter = Counter()
        for l in self.lines:
            if l["line_key"] in self.standing and l.get("claim_status_code") in ("1", "19") and l.get("service_date"):
                paid_lines[(bill_of(l["patient_control_number"]), l.get("procedure_code"), l["service_date"])] += 1
        groups, multi = Counter(), Counter()
        for (_b, code, _d), n in paid_lines.items():
            groups[code] += 1
            multi[code] += n > 1
        self.once_codes = {code for code, g in groups.items() if g >= 20 and multi[code] <= 0.015 * g}     # paid at most once a day
        del paid_lines
        # every payment that still stands, by bill, code and date: (payment date, trace, amount standing, claim status, claim id, units, payer)
        self.paid_events: dict[tuple, list[tuple]] = defaultdict(list)
        self.paid_by_code: dict[tuple, list[tuple]] = defaultdict(list)     # (bill, code) -> the same events, undated lines too
        for l in self.lines:
            amt = self.standing.get(l["line_key"])
            if amt:
                e = (l.get("payment_date") or l.get("txn_payment_date") or "", l.get("trace_number"), amt, l.get("claim_status_code"),
                     l["patient_control_number"], abs(l.get("paid_units") or 1), l.get("payer_id") or l.get("payer_name"), l.get("service_date"))
                self.paid_by_code[(bill_of(l["patient_control_number"]), l["procedure_code"])].append(e)
                if l.get("service_date"):
                    self.paid_events[(bill_of(l["patient_control_number"]), l["procedure_code"], l["service_date"])].append(e)
        # EMA links and biller handling (built by link.build_links when EMA exports are loaded)
        self.links: dict[str, dict] = {}
        self.handling: dict[str, list[dict]] = defaultdict(list)
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "line_links" in tables:
            for r in c.execute("""SELECT ll.line_key, ll.state, ll.bill_item_id, ll.ema_cpt, ll.ema_modifiers, ll.ema_units, ll.ema_charges_cents,
                                         ll.ema_claim_status, e.location, e.icd10
                                  FROM line_links ll LEFT JOIN ema_rows e ON e.row_fingerprint = ll.ema_row_fingerprint
                                  WHERE ll.state IN ('exact_composite','fallback_candidate')"""):
                self.links[r["line_key"]] = dict(r)
        # practice location: from any EMA row for the claim id, else the rendering provider's usual site when one site dominates
        self.claim_location: dict[str, str] = {}
        self.npi_location: dict[str, str] = {}
        if "ema_rows" in tables:
            loc_votes: dict[str, Counter] = defaultdict(Counter)
            for r in c.execute("SELECT claim_id, location, COUNT(*) n FROM ema_rows WHERE claim_id IS NOT NULL AND location IS NOT NULL GROUP BY 1,2"):
                loc_votes[r[0]][r[1]] += r[2]
            self.claim_location = {k: top_of(v)[0] for k, v in loc_votes.items()}
            npi_votes: dict[str, Counter] = defaultdict(Counter)
            seen_claims: set[str] = set()
            for l in self.lines:
                loc = self.claim_location.get(l.get("patient_control_number") or "")
                if loc and l.get("rendering_provider_npi") and l["claim_key"] not in seen_claims:
                    seen_claims.add(l["claim_key"])
                    npi_votes[l["rendering_provider_npi"]][loc] += 1
            for npi, v in npi_votes.items():
                top, n = top_of(v)
                if sum(v.values()) >= 20 and n / sum(v.values()) >= 0.85:
                    self.npi_location[npi] = top
        # what was billed, from the 837 claim files and the EMA charge lines: (claim id, date of service) -> {code: source}
        self.billed: dict[tuple, dict[str, str]] = defaultdict(dict)
        if "lines_837" in tables:
            for r in c.execute("SELECT patient_control_number, service_date, procedure_code FROM lines_837 WHERE procedure_code IS NOT NULL"):
                self.billed[(r[0], r[1])].setdefault(r[2], "837 claim file")
        if "ema_rows" in tables:
            for r in c.execute("""SELECT claim_id, service_date, cpt FROM ema_rows
                                  WHERE family='charge_lines' AND activity_type='Charge' AND claim_id IS NOT NULL AND cpt IS NOT NULL"""):
                self.billed[(r[0], r[1])].setdefault(r[2], "EMA charge line")
        if "line_handling" in tables:
            for r in c.execute("SELECT line_key, carc, rarc, adjustment_status, adjustment_subtype, adjustments_cents, payments_cents, transaction_date FROM line_handling"):
                self.handling[r["line_key"]].append(dict(r))
        if "ema_rows" in tables:
            # what EMA has posted from insurers on each billed line, as of the newest export that lists it (the full Encounter
            # and Charge Lines layout carries it). Only ever a hint: it is by claim, code and date, not by line or payer.
            when = "COALESCE(s.created_at, '')" if "ema_sources" in tables else "''"
            join = "LEFT JOIN ema_sources s ON s.sha256 = e.source_sha256" if "ema_sources" in tables else ""
            newest: dict[tuple, tuple] = {}
            for cid, cpt, dos, paid, src, at in c.execute(f"""SELECT e.claim_id, e.cpt, e.service_date, e.insurance_payments_cents, e.source_name, {when}
                                                             FROM ema_rows e {join}
                                                             WHERE e.family='charge_lines' AND e.activity_type='Charge' AND e.claim_id IS NOT NULL
                                                               AND e.insurance_payments_cents IS NOT NULL"""):
                k = (cid, cpt, dos)
                if k not in newest or (at, src or "") > newest[k][1:]:
                    newest[k] = (paid or 0, at, src or "")
            self.ema_paid = {k: (v[0], v[2]) for k, v in newest.items() if v[0] > 0}
            self.ema_patient = {cid: pat for cid, pat in c.execute("""SELECT claim_id, MAX(patient_id) FROM ema_rows WHERE family='charge_lines'
                                                                      AND claim_id IS NOT NULL AND COALESCE(patient_id,'') <> '' GROUP BY 1""")}
        # surgical rank within each claim: 1 = highest allowed surgical procedure (paid 100 percent), 2+ reduced
        for claim_lines in self.lines_by_claim.values():
            surg = [x for x in claim_lines if is_surgical(x.get("procedure_code")) and (x.get("allowed_cents") or 0) > 0]
            surg.sort(key=lambda x: -(x.get("allowed_cents") or 0))
            for i, x in enumerate(surg):
                x["surg_rank"] = i + 1
            for x in claim_lines:
                x.setdefault("surg_rank", 0)
                x["surg_count"] = len(surg)

    # ------------------------------------------------------------------ baselines
    def build_baselines(self) -> None:
        by_npi: dict[tuple, Counter] = defaultdict(Counter)
        names: dict[str, str] = {}
        for l in self.lines:
            if l["payer_kind"] == "medicare" and l["procedure_code"] in ("99213", "99214", "99203") and l.get("claim_status_code") in ("1", "19") \
                    and (l.get("paid_cents") or 0) > 0 and l.get("allowed_cents") and not l.get("original_procedure_code") and l.get("rendering_provider_npi") \
                    and not l["pricing_mod"]:
                by_npi[(l["rendering_provider_npi"], l["year"], l["procedure_code"])][l["allowed_cents"]] += 1
                names[l["rendering_provider_npi"]] = l.get("rp_name") or ""
        top_by_year_code: dict[tuple, int] = {}
        for (npi, year, code), cnt in by_npi.items():
            mode = usual_amount(cnt)[0]
            top_by_year_code[(year, code)] = max(top_by_year_code.get((year, code), 0), mode)
        tier_votes: dict[str, list[float]] = defaultdict(list)
        for (npi, year, code), cnt in by_npi.items():
            mode = usual_amount(cnt)[0]
            top = top_by_year_code[(year, code)]
            if top:
                tier_votes[npi].append(mode / top)
        self.provider_tier: dict[str, str] = {}
        tier_rows = []
        for npi, ratios in tier_votes.items():
            r = sorted(ratios)[len(ratios) // 2]
            tier = "nonphysician" if r < 0.92 else "physician"
            self.provider_tier[npi] = tier
            tier_rows.append((npi, names.get(npi, ""), tier, round(r, 3), len(ratios)))
        name_votes: dict[str, Counter] = defaultdict(Counter)
        line_counts: Counter = Counter()
        for l in self.lines:
            l["provider_tier"] = self.provider_tier.get(l.get("rendering_provider_npi") or "", "unknown")
            if l.get("rendering_provider_npi"):
                name_votes[l["rendering_provider_npi"]][l.get("rp_name") or ""] += 1
                line_counts[l["rendering_provider_npi"]] += 1
        self.provider_name = {npi: best_provider_name(v) for npi, v in name_votes.items()}
        for l in self.lines:
            if l.get("rendering_provider_npi"):
                l["rp_name"] = self.provider_name.get(l["rendering_provider_npi"]) or l.get("rp_name")
        self.conn.execute("DELETE FROM providers")
        self.conn.executemany("INSERT OR REPLACE INTO providers (npi, name, tier, lines, location) VALUES (?,?,?,?,?)",
                              [(npi, self.provider_name.get(npi, ""), self.provider_tier.get(npi, "unknown"), n, self.npi_location.get(npi))
                               for npi, n in line_counts.items()])
        self.conn.execute("DELETE FROM provider_tiers")
        self.conn.executemany("INSERT INTO provider_tiers (npi, name, tier, ratio, n) VALUES (?,?,?,?,?)", tier_rows)

        self.fees = FeeTable()
        for l in self.lines:
            self.fees.observe(l["year"], l["procedure_code"], l["charge_cents"], l["paid_units"], bool(l.get("original_procedure_code")))
        self.fees.finalize()
        self.conn.execute("DELETE FROM fee_table")
        self.conn.executemany("INSERT OR REPLACE INTO fee_table (year, code, charge_cents, share) VALUES (?,?,?,?)",
                              [(r["year"], r["code"], r["charge_cents"], r["share"]) for r in self.fees.to_rows()])

        # payer rates from clean, full-price lines: primary claims, one unit, no code change, no pricing modifier,
        # and (for surgical codes) the highest-ranked procedure on the claim so multiple-procedure reductions do not pollute the mode
        counts: dict[tuple, Counter] = defaultdict(Counter)
        self.kinds: dict[str, str] = {}
        for l in self.lines:
            if l.get("claim_status_code") not in ("1", "19") or (l.get("paid_cents") or 0) <= 0 or not l.get("allowed_cents"):
                continue
            if l.get("original_procedure_code") or l.get("paid_units") not in (None, 1, 1.0) or l["pricing_mod"]:
                continue
            if l["surg_rank"] > 1:
                continue
            if any(rc in ("59", "B10", "97", "236", "234", "150", "186") for _g, rc, _a in self.adj.get(l["line_key"], [])):
                continue
            if self._same_day_policy_claim(l):
                continue
            l["clean"] = True
            counts[(l["payer_group"], l["year"], l["procedure_code"], l["provider_tier"], l["pos_class"])][l["allowed_cents"]] += 1
            self.kinds[l["payer_group"]] = l["payer_kind"]
        self.rates: dict[tuple, dict] = {}
        market_counts: dict[tuple, Counter] = defaultdict(Counter)
        for key, cnt in counts.items():
            n = sum(cnt.values())
            market_counts[(key[1], key[2], key[3], key[4])].update(cnt)
            if n < 5:
                continue
            mode, mode_n = usual_amount(cnt)
            vals = sorted(cnt.elements())
            self.rates[key] = {"mode": mode, "median": vals[len(vals) // 2], "p10": vals[int(0.1 * (len(vals) - 1))],
                               "p90": vals[int(0.9 * (len(vals) - 1))], "n": n, "share": mode_n / n, "key": key}
        self.rate_counts = counts       # how often each amount was allowed: tells a payer's routine second rate from a one-off
        # cross-payer typical allowed (median) per year, code and tier: the last-resort expectation
        self.market: dict[tuple, dict] = {}
        for key, cnt in market_counts.items():
            n = sum(cnt.values())
            if n >= 10:
                vals = sorted(cnt.elements())
                self.market[key] = {"median": vals[len(vals) // 2], "n": n}
        self.medicare: dict[tuple, int] = {}
        for (grp, year, code, tier, pos), r in self.rates.items():
            if self.kinds.get(grp) == "medicare" and r["n"] >= 10:
                key = (year, code, tier, pos)
                if key not in self.medicare or grp.startswith("Medicare KY"):
                    self.medicare[key] = r["mode"]
        self.conn.execute("DELETE FROM payer_rates")
        rows = []
        for (grp, year, code, tier, pos), r in self.rates.items():
            med = self.medicare.get((year, code, tier, pos))
            rows.append((grp, self.kinds.get(grp, "other"), year, code, tier, r["mode"], r["median"], r["p10"], r["p90"], r["n"],
                         round(r["share"], 3), med, round(r["mode"] / med, 4) if med else None, pos))
        self.conn.executemany("""INSERT OR REPLACE INTO payer_rates (payer_group, payer_kind, year, code, tier, mode_cents, median_cents, p10_cents,
                                 p90_cents, n, mode_share, medicare_cents, pct_of_medicare, pos_class) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)

        # each payer's own multiple-procedure practice: typical allowed as a share of its usual rate for the 2nd and for later procedures
        ratios: dict[tuple, list[float]] = defaultdict(list)
        for l in self.lines:
            rank = l.get("surg_rank", 0)
            code = l.get("procedure_code")
            if rank < 2 or not is_surgical(code) or code in ADD_ON_CODES or l.get("claim_status_code") not in ("1", "19"):
                continue
            if (l.get("paid_cents") or 0) <= 0 or not l.get("allowed_cents") or l.get("original_procedure_code") or l["pricing_mod"] \
                    or l.get("paid_units") not in (None, 1, 1.0):
                continue
            r = self.rates.get((l["payer_group"], l["year"], code, l["provider_tier"], l["pos_class"]))
            if r and r["n"] >= 10 and r["mode"]:
                ratios[(l["payer_group"], 2 if rank == 2 else 3)].append(l["allowed_cents"] / r["mode"])
        self.mppr: dict[tuple, dict] = {}
        for key, vals in ratios.items():
            if len(vals) < 15:
                continue
            med = sorted(vals)[len(vals) // 2]
            snap = min((1.0, 0.75, 0.5, 0.25), key=lambda p: abs(p - med))
            if abs(snap - med) <= 0.08:
                self.mppr[key] = {"pct": snap, "n": len(vals), "median": round(med, 3)}
        self.conn.commit()

    def _same_day_policy_claim(self, l: dict) -> bool:
        """Medicare's reported 2027 same-day rule: a visit with modifier 25 billed with a 0-day global procedure has the
        lower-paid of the two paid at 80 percent. Such lines are kept out of the 2027 Medicare baselines."""
        if l.get("payer_kind") != "medicare" or (l.get("service_date") or "") < MEDICARE_SAME_DAY_EM["effective"]:
            return False
        claim_lines = self.lines_by_claim.get(l["claim_key"], [])
        has_em25 = any(o.get("procedure_code") in EM_CODES and "25" in o["modifiers_list"] for o in claim_lines)
        has_zero_day = any(GLOBAL_DAYS.get(o.get("procedure_code") or "") == 0 and o.get("procedure_code") not in EM_CODES for o in claim_lines)
        code = l.get("procedure_code")
        return has_em25 and has_zero_day and (code in EM_CODES or GLOBAL_DAYS.get(code or "") == 0)

    def _rate_lookup(self, l: dict, code: str, pos: str) -> tuple[int | None, str, dict | None]:
        grp, year, tier = l["payer_group"], l["year"], l.get("provider_tier", "unknown")
        where = "" if pos == "office" else ", facility setting"
        for t in (tier, "physician", "nonphysician", "unknown"):
            r = self.rates.get((grp, year, code, t, pos))
            if r:
                return r["mode"], f"{grp} usual {year} rate for {code} ({t}{where}, n={r['n']})", r
        if year.isdigit():
            for y in (str(int(year) - 1), str(int(year) + 1)):
                for t in (tier, "physician", "unknown"):
                    r = self.rates.get((grp, y, code, t, pos))
                    if r:
                        return r["mode"], f"{grp} usual {y} rate for {code} ({t}{where}, n={r['n']})", r
        med = self.medicare.get((year, code, tier if tier != "unknown" else "physician", pos))
        if med:
            return med, f"Medicare {year} rate for {code} ({tier}{where})", None
        for t in (tier, "physician", "nonphysician", "unknown"):
            m = self.market.get((year, code, t, pos))
            if m:
                return m["median"], f"cross-payer typical {year} allowed for {code} ({t}{where}, n={m['n']})", None
        return None, "no baseline", None

    def usual_rate(self, l: dict, code: str) -> tuple[int | None, str, dict | None]:
        """The payer's usual allowed amount for this code, provider type and place-of-service class.
        The third value is the rate record when the figure is the payer's own (None for fallbacks)."""
        pos = l.get("pos_class", "office")
        rate, basis, r = self._rate_lookup(l, code, pos)
        if rate is None and pos == "facility":
            # no facility-setting baseline: an office-setting figure is good enough to size a denial, never to call a line underpaid
            rate, basis, _r = self._rate_lookup(l, code, "office")
            if rate is not None:
                return rate, basis + " (office-setting figure; no facility-setting baseline)", None
        return rate, basis, r

    def mppr_pct(self, l: dict) -> tuple[float, bool]:
        """Share of the usual rate this payer normally allows for a 2nd or later procedure, and whether it was learned from its remits."""
        p = self.mppr.get((l["payer_group"], 2 if l.get("surg_rank", 0) == 2 else 3))
        return (p["pct"], True) if p else (0.5, False)

    def expected_allowed(self, l: dict, code: str) -> tuple[int | None, str]:
        """Rank-aware expectation: the payer's usual full rate, reduced for a second or later surgical procedure."""
        rate, basis, _r = self.usual_rate(l, code)
        if rate is None:
            return None, basis
        rank = l.get("surg_rank", 0)
        if rank >= 2 and code not in ADD_ON_CODES and is_surgical(code):
            pct, learned = self.mppr_pct(l)
            note = " (this payer's usual practice)" if learned else ""
            return int(round(rate * pct)), f"{basis}; multiple-procedure rank {rank} -> {int(pct*100)}%{note}"
        if self._same_day_policy_claim(l):
            others = [o for o in self.lines_by_claim.get(l["claim_key"], []) if o["line_key"] != l["line_key"] and (o.get("allowed_cents") or 0) > 0]
            if any((o.get("allowed_cents") or 0) > rate for o in others):
                pct = MEDICARE_SAME_DAY_EM["pct"]
                return int(round(rate * pct)), f"{basis}; Medicare same-day visit and procedure rule from {MEDICARE_SAME_DAY_EM['effective']} -> {int(pct*100)}%"
        return rate, basis

    def downcode_expected(self, l: dict, billed: str, paid_code: str, allowed: int) -> tuple[int | None, str]:
        """Expected allowed for the billed code of a downcoded line. Where the payer has no baseline for this place of
        service, scale the amount actually allowed by the payer's office-setting ratio between the two codes."""
        _rate, _basis, r = self.usual_rate(l, billed)
        if r is None and allowed and l.get("surg_rank", 0) < 2:
            hi, _b, hr = self._rate_lookup(l, billed, "office")
            lo, _b2, lr = self._rate_lookup(l, paid_code, "office")
            if hi and lo and hr and lr and hi > lo:
                return int(round(allowed * hi / lo)), (f"allowed amount scaled by this payer's usual {billed} to {paid_code} ratio "
                                                       f"(${hi/100:.2f} to ${lo/100:.2f}); no baseline for this place of service")
        return self.expected_allowed(l, billed)

    def paid_like(self, l: dict, code: str, allowed: int) -> str | None:
        """The lower visit level whose usual rate at this payer equals the allowed amount, checked for every provider type."""
        fam = family_of(code)
        if not fam or not allowed:
            return None
        years = [l["year"]] + ([str(int(l["year"]) - 1)] if l["year"].isdigit() else [])
        for lower in sorted(EM_CODES, reverse=True):
            lf = family_of(lower)
            if not lf or lf[0] != fam[0] or lf[1] >= fam[1]:
                continue
            for y in years:
                for t in ("physician", "nonphysician", "unknown"):
                    r = self.rates.get((l["payer_group"], y, lower, t, l.get("pos_class", "office")))
                    if r and abs(r["mode"] - allowed) <= 1:
                        return lower
        return None

    def billed_higher(self, l: dict, code: str) -> tuple[str, str, list[str]] | None:
        """A higher level of the same code family that the claim file or EMA shows was billed for this claim and date,
        when the code on the remit was not billed at all. Returns (billed code, source, all billed codes)."""
        dos = l.get("service_date") or l.get("statement_from")
        billed = self.billed.get((l.get("patient_control_number"), dos))
        if not billed or code in billed:
            return None
        higher = [b for b in billed if is_downcode(b, code)]
        if not higher:
            return None
        best = max(higher, key=lambda b: (family_of(b) or ("", 0))[1])
        return best, billed[best], sorted(billed)

    # ------------------------------------------------------------------ helpers
    def _line_carcs(self, l: dict):
        adj = self.adj.get(l["line_key"], [])
        payer_adj = [(g, rc, a) for g, rc, a in adj if g in ("CO", "PI", "OA")]
        pr = sum(a for g, rc, a in adj if g == "PR")
        top, best = None, -1
        for g, rc, a in payer_adj:
            if rc in IGNORE_CARCS:
                continue
            if a > best:
                best, top = a, rc
        return adj, top, pr, {rc for _g, rc, _a in payer_adj}

    def _build_ledger(self) -> dict[str, int]:
        """What is left of every payment once the payer's takebacks are applied: {line_key: cents still paid}, with payments
        taken back in full left out.
        A reversal (claim status 22) cancels an earlier payment it reverses: the same bill, code and date, from the same payer
        (a reversal that names no payer cancels only a payment of exactly its amount), preferring the same amount, then the
        same claim id, then the same payer claim number, then the most recent. Only a payment dated before the reversal can
        be cancelled: the archive starts in 2024, and a same-day payment beside a reversal is the repayment of an original
        paid before it. A payment made again after a reversal is a new payment and stands.
        Claim-level takebacks follow: a PLB WO whose reference is a claim id or a payer claim number, netted per reference
        and payer (a balance carried forward with a negative WO and recovered later is taken once), and a reversal sent
        without service lines. Each takes back that payer's earlier payments on that claim, and a WO is skipped where a
        reversal already took back as much on that claim."""
        when = lambda l: l.get("payment_date") or l.get("txn_payment_date") or ""          # noqa: E731
        payer = lambda l: l.get("payer_id") or l.get("payer_name")                         # noqa: E731
        left: dict[str, int] = {}
        pos: dict[tuple, list] = defaultdict(list)
        revs = []
        for l in self.lines:
            st, amt = l.get("claim_status_code"), l.get("paid_cents") or 0
            if st in ("1", "19", "2", "3") and amt > 0:
                left[l["line_key"]] = amt
                pos[(bill_of(l["patient_control_number"]), l.get("procedure_code"), l.get("service_date"))].append(l)
            elif st == "22" and amt < 0:
                revs.append(l)
        reversed_on: Counter = Counter()        # (claim id or payer claim number, date) -> cents a line reversal took back
        if revs:
            keys = sorted({p["claim_key"] for r in revs for p in pos.get((bill_of(r["patient_control_number"]), r.get("procedure_code"),
                                                                          r.get("service_date")), [])} | {r["claim_key"] for r in revs})
            pccn: dict[str, str | None] = {}
            for i in range(0, len(keys), 500):
                part = keys[i:i + 500]
                pccn.update(self.conn.execute(f"SELECT claim_key, payer_claim_control_number FROM claims WHERE claim_key IN ({','.join('?' * len(part))})",
                                              part).fetchall())
            for r in sorted(revs, key=when):
                need = -r["paid_cents"]
                cands = [p for p in pos.get((bill_of(r["patient_control_number"]), r.get("procedure_code"), r.get("service_date")), [])
                         if left[p["line_key"]] > 0 and when(p) < when(r)]
                cands = [p for p in cands if payer(p) == payer(r)] if payer(r) else [p for p in cands if p["paid_cents"] == need]
                cands.sort(key=when, reverse=True)          # most recent first, then (a stable sort) the closest match first
                cands.sort(key=lambda p: (left[p["line_key"]] != need and p["paid_cents"] != need, p["patient_control_number"] != r["patient_control_number"],
                                          not pccn.get(r["claim_key"]) or pccn.get(p["claim_key"]) != pccn.get(r["claim_key"])))
                for p in cands:
                    take = min(left[p["line_key"]], need)
                    left[p["line_key"]] -= take
                    need -= take
                    if need <= 0:
                        break
                taken = -r["paid_cents"] - max(need, 0)
                for ref in {r["patient_control_number"], pccn.get(r["claim_key"])} - {None}:
                    reversed_on[(ref, when(r))] += taken
        takebacks = []              # (references, cents, date, payer, kind)
        net: dict[tuple, list] = defaultdict(lambda: [0, ""])
        wo_rows = self.conn.execute("""SELECT p.reference, p.amount_cents, t.payment_date, COALESCE(t.payer_id, t.payer_name) FROM plb p
                                       JOIN transactions t ON t.transaction_key = p.transaction_key
                                       WHERE t.superseded = 0 AND p.reason_code = 'WO' AND COALESCE(p.reference, '') <> ''""").fetchall()
        # a balance carried under the payer's claim number and recovered under the claim id (or the reverse) is one balance
        ids_of_number: dict[tuple, set] = defaultdict(set)
        wo_refs = sorted({r[0] for r in wo_rows})
        for i in range(0, len(wo_refs), 500):
            part = wo_refs[i:i + 500]
            for pcn, cn, who_ in self.conn.execute(f"""SELECT c.patient_control_number, c.payer_claim_control_number, COALESCE(t.payer_id, t.payer_name)
                                                       FROM claims c JOIN transactions t ON t.transaction_key = c.transaction_key
                                                       WHERE c.payer_claim_control_number IN ({','.join('?' * len(part))})""", part):
                ids_of_number[(cn, who_)].add(pcn)
        number_to_id = {k: next(iter(v)) for k, v in ids_of_number.items() if len(v) == 1}     # only a number that names one claim
        for ref, amount, day, who in wo_rows:
            n = net[(number_to_id.get((ref, who), ref), who)]
            n[0] += amount or 0
            n[1] = max(n[1], day or "")
        takebacks += [((ref,), amount, day, who, "wo") for (ref, who), (amount, day) in net.items() if amount > 0]
        takebacks += [(tuple(x for x in (pcn, cn) if x), -paid, day, who, "reversal") for pcn, cn, paid, day, who in self.conn.execute(
            """SELECT c.patient_control_number, c.payer_claim_control_number, c.paid_cents, COALESCE(c.payment_date, t.payment_date), COALESCE(t.payer_id, t.payer_name)
               FROM claims c JOIN transactions t ON t.transaction_key = c.transaction_key
               WHERE t.superseded = 0 AND c.claim_status_code = '22' AND c.paid_cents < 0
                 AND NOT EXISTS (SELECT 1 FROM lines l WHERE l.claim_key = c.claim_key)""")]
        if takebacks:
            refs = sorted({ref for t in takebacks for ref in t[0]})
            by_id: dict[str, set] = defaultdict(set)        # claim id -> claims
            by_number: dict[str, set] = defaultdict(set)    # payer claim number -> claims
            for i in range(0, len(refs), 500):
                part = refs[i:i + 500]
                q = ','.join('?' * len(part))
                for ck, pcn, cn in self.conn.execute(f"""SELECT claim_key, patient_control_number, payer_claim_control_number FROM claims
                                                        WHERE patient_control_number IN ({q}) OR payer_claim_control_number IN ({q})""", part + part):
                    if pcn in part:
                        by_id[pcn].add(ck)
                    if cn in part:
                        by_number[cn].add(ck)
            for refs_, amount, day, who, kind in sorted(takebacks, key=lambda t: (t[2] or "", t[0])):
                if kind == "wo" and max((reversed_on.get((ref, day), 0) for ref in refs_), default=0) >= amount:
                    continue            # the same remittance reversed that claim: the WO reports the same money again
                paid = []
                # the claim the takeback names by its id first; its payer claim number only when nothing under that id stands
                for index in (by_id, by_number):
                    paid = [l for ck in sorted(set().union(*(index.get(ref, set()) for ref in refs_))) for l in self.lines_by_claim.get(ck, [])
                            if left.get(l["line_key"], 0) > 0 and when(l) < (day or "~") and (who is None or payer(l) == who)]
                    if paid:
                        break
                for l in sorted(paid, key=lambda l: (left[l["line_key"]] != amount, -left[l["line_key"]], l["line_key"])):
                    take = min(left[l["line_key"]], amount)
                    left[l["line_key"]] -= take
                    amount -= take
                    if amount <= 0:
                        break
        return {k: v for k, v in left.items() if v > 0}

    def paid_cover(self, bill: str, code: str | None, dos: str | None, expected: int | None = None, after: str | None = None) -> list[tuple]:
        """The payments still standing for this bill, code and date that count as paying it: a primary payment, or a
        secondary one covering at least half the expected amount; made after `after` when given. An undated line (a claim
        over several days with no line dates) matches the bill's undated payments of the code."""
        if not code:
            return []
        evs = self.paid_events.get((bill, code, dos), []) if dos else [e for e in self.paid_by_code.get((bill, code), []) if e[7] is None]
        return [e for e in evs if (after is None or (e[0] and e[0] > after)) and (e[3] in ("1", "19") or (expected and (e[2] or 0) >= 0.5 * expected))]

    @staticmethod
    def units_paid(events: list[tuple]) -> float:
        """Units these standing payments pay: the primary payments' units (a secondary pays its share of the same units), or
        the secondary payments' units when no primary payment stands."""
        prim = [e for e in events if e[3] in ("1", "19")]
        return sum(e[5] for e in (prim or events))

    def units_billed(self, claim_key: str, code: str | None, dos: str | None) -> float:
        """Units billed on a claim for a code and date, over every line of it (two specimens of 88305 are two units): one
        payment for one of them must not close the other's denial."""
        return sum(abs(ln.get("original_units") or ln.get("paid_units") or 1) for ln in self.lines_by_claim.get(claim_key, [])
                   if ln.get("procedure_code") == code and ln.get("service_date") == dos) or 1

    def later_payment(self, l: dict, code: str | None = None, expected: int | None = None, min_paid: int | None = None) -> tuple | None:
        """The later remit that paid this bill, code and date of service (a reworked or appealed line), as one event: its
        date, trace and claim are the earliest payment's; amount and units are those of every payment counted.
        A primary payment counts; a secondary payment counts only when it covers at least half the expected amount.
        For a line paid nothing, the later payments must cover the units it billed; short of that the line is recorded in
        self.partly_paid for a hint. `min_paid` is for lines that were paid short under the same code: the later payment
        must be clearly larger than the short one, so a repeat of the same reduced payment does not close the finding."""
        mine = l.get("payment_date") or l.get("txn_payment_date") or ""
        code = code or l.get("procedure_code")
        standing = self.paid_cover(bill_of(l.get("patient_control_number")), code, l.get("service_date"), expected)
        # later: after this remittance, or in it on another claim of the bill (a corrected claim paid as the original was denied)
        events = [e for e in standing if e[0] and (e[0] > mine or (e[0] == mine and e[4] != l.get("patient_control_number")))]
        if min_paid is not None:
            events = [e for e in events if (e[2] or 0) >= min_paid]
        if not events:
            return None
        first = min(events)
        if not (l.get("paid_cents") or 0):
            # the bill's payments still standing for this code and date must cover every unit its claim billed for them
            need, got = self.units_billed(l["claim_key"], code, l.get("service_date")), self.units_paid(standing)
            if got < need:
                self.partly_paid[l["line_key"]] = (first, got, need)
                return None
        return first if len(events) == 1 else first[:2] + (sum(e[2] for e in events),) + first[3:5] + (self.units_paid(events),) + first[6:]

    def emit_second_rates(self) -> None:
        """A payer's routine lower amount for a code (the same amount on at least SECOND_RATE_MIN_LINES clean lines of the
        line's own payer, year, code, provider type and place of service) becomes one finding with its claims, when the
        remittances support a reason for most of its lines (the rate for a PA or NP, one office's plan, one plan, a
        multiple-procedure reduction, a rate change). It carries each claim's filing deadline and shows the last. Without a
        reason, each line stays a one-off underpayment to appeal. A finding someone worked keeps the lines it had, and is
        emitted again with them whatever the rules now say; lines paid at that amount since go to a new finding."""
        for fid, w in self.worked_patterns.items():
            lines = self.pattern_lines.get(fid)
            if lines:
                kp = tuple(w["key_parts"])
                self._emit_pattern(kp, lines, w.get("usual_cents"), w.get("note") or "", w.get("continues"))
        if not self.second_rates:
            return
        day_state: dict[tuple, str] = {}
        surg_lines: dict[tuple, set] = defaultdict(set)     # (bill, date) -> primary surgical lines still paid
        for l in self.lines:
            grp = l.get("payer_group") or ""
            if (grp.startswith("Medicare KY") or grp.startswith("Medicare IN")) and l.get("rendering_provider_npi") and l.get("service_date"):
                day_state[(l["rendering_provider_npi"], l["service_date"])] = grp[9:11]
            if is_surgical(l.get("procedure_code")) and l.get("procedure_code") not in ADD_ON_CODES and l.get("claim_status_code") in ("1", "19") \
                    and l["line_key"] in self.standing and l.get("service_date"):
                surg_lines[(bill_of(l.get("patient_control_number")), l["service_date"])].add(l["line_key"])
        state_of = lambda l: day_state.get((l.get("rendering_provider_npi"), l.get("service_date")), "unknown")
        usual = {key: self.rates[key]["mode"] for key, _a in self.second_rates if key in self.rates}
        # NPIs the remittances give a person's first name for: a clinician. A group's NPI (NM1*82*2) names the practice only.
        person = {r[0] for r in self.conn.execute("""SELECT DISTINCT rendering_provider_npi FROM claims WHERE rendering_provider_npi IS NOT NULL
                                                     AND COALESCE(rendering_provider_first, '') <> ''""")}
        usual_states: dict[tuple, Counter] = defaultdict(Counter)
        usual_plans: dict[tuple, Counter] = defaultdict(Counter)
        usual_npis: dict[tuple, set] = defaultdict(set)
        usual_last: dict[tuple, str] = {}
        for l in self.lines:
            if not l.get("clean"):
                continue
            key = (l["payer_group"], l["year"], l["procedure_code"], l["provider_tier"], l["pos_class"])
            if key in usual and l.get("allowed_cents") == usual[key]:
                usual_states[key][state_of(l)] += 1
                usual_plans[key][(l.get("contract_class") or "").strip() or "(none)"] += 1
                if l.get("rendering_provider_npi") in person:
                    usual_npis[key].add(l["rendering_provider_npi"])
                d = l.get("service_date") or ""
                if d > usual_last.get(key, ""):
                    usual_last[key] = d
        for (key, amount), group in self.second_rates.items():
            r = self.rates.get(key)
            if not r or not group:
                continue
            lines = [l for l, _one in group]
            grp, year, code, tier, pos = key
            rate = r["mode"]
            ratio = amount / rate
            tiers = Counter(l.get("provider_tier") or "unknown" for l in lines)
            unnamed = sum(1 for l in lines if (l.get("provider_tier") or "unknown") == "unknown" and l.get("rendering_provider_npi") not in person)
            same_clinician = any(l.get("rendering_provider_npi") in usual_npis.get(key, ()) for l in lines)
            states = Counter(state_of(l) for l in lines)
            dos = sorted(l.get("service_date") or "" for l in lines)
            plans = Counter((l.get("contract_class") or "").strip() or "(none)" for l in lines)
            surgical = is_surgical(code) and code not in ADD_ON_CODES
            with_another = (sum(1 for l in lines if len(surg_lines.get((bill_of(l.get("patient_control_number")), l.get("service_date")), set()) - {l["line_key"]}) >= 1)
                            / len(lines)) if surgical else 0.0
            note, supported = second_rate_reason(ratio, tier, tiers, states, usual_states.get(key, Counter()), dos[0] or None, usual_last.get(key),
                                                 plans, usual_plans.get(key, Counter()), with_another, unnamed, same_clinician)
            if not supported:
                for _l, one_off in group:
                    self._emit(*one_off)
                continue
            kp, n, continues = ("SECOND_RATE", grp, year, code, tier, pos, amount), 0, None
            while _fid("SECOND_RATE", *kp) in self.worked_patterns:      # worked: these lines came after, so they get their own finding
                continues = _fid("SECOND_RATE", *kp)
                n += 1
                kp = ("SECOND_RATE", grp, year, code, tier, pos, amount, "continuation", n)
            self._emit_pattern(kp, lines, rate, note, continues, by_tier=tiers, by_state=states, by_plan=plans,
                               at_amount=self.rate_counts[key][amount], for_code=r["n"])

    def _emit_pattern(self, kp: tuple, lines: list, rate: int | None, note: str, continues: str | None, by_tier: Counter | None = None,
                      by_state: Counter | None = None, by_plan: Counter | None = None, at_amount: int | None = None, for_code: int | None = None) -> None:
        """One SECOND_RATE finding for these lines, with each claim's filing deadline."""
        _sig, grp, year, code, tier, pos, amount = kp[:7]
        rate = rate or max((l.get("allowed_cents") or 0) for l in lines)
        n = len(lines)
        ratio = amount / rate if rate else 0
        gap = sum(max(rate - (l.get("allowed_cents") or 0), 0) for l in lines)
        dos = sorted(l.get("service_date") or "" for l in lines)
        quarters = Counter(f"{d[:4]}-Q{(int(d[5:7]) + 2) // 3}" for d in dos if len(d) >= 7)
        names = Counter(l.get("payer_name") for l in lines)
        deadlines, due, by_due = [], {}, defaultdict(lambda: [0, 0])
        for l in lines:
            rule = appeal_rule(grp, l.get("payer_kind"), (l.get("contract_class") or "").strip() or None)
            d = _add_days(l.get("payment_date") or l.get("txn_payment_date"), rule["days"])
            if d:
                deadlines.append((d, rule["label"] + ("" if rule["verified"] else " (not confirmed against the payer's current manual)")))
                due[l["line_key"]] = d
            by_due[d][0] += 1           # d is None when the remittance has no payment date: the window is unknown
            by_due[d][1] += max(rate - (l.get("allowed_cents") or 0), 0)
        base = {"payer_group": grp, "payer_kind": self.kinds.get(grp), "payer_name": names.most_common(1)[0][0] or grp, "year": year,
                "procedure_code": code, "provider_tier": tier, "pos_class": pos,
                "payment_date": max((l.get("payment_date") or l.get("txn_payment_date") or "") for l in lines) or None}
        ordered = sorted(lines, key=lambda x: (x.get("service_date") or "", x.get("patient_control_number") or ""))
        evidence = {"usual_cents": rate, "amount_cents": amount, "pct_of_usual": round(ratio, 3), "lines": n,
                    "payer_lines_at_this_amount": at_amount, "payer_lines_for_code": for_code,
                    "by_provider_type": dict(by_tier or Counter(l.get("provider_tier") or "unknown" for l in lines)),
                    "by_office_state": dict(by_state or {}), "by_plan": dict((by_plan or Counter()).most_common(6)),
                    "by_quarter": dict(sorted(quarters.items())), "first_visit": dos[0] or None, "last_visit": dos[-1] or None, "note": note,
                    # each claim keeps its own filing deadline: the page counts the ones still open on the day it is read
                    "claims": [[l.get("patient_control_number"), l.get("service_date"), l.get("allowed_cents"), due.get(l["line_key"])] for l in ordered],
                    "deadlines": [[d, v[0], v[1]] for d, v in sorted(by_due.items(), key=lambda x: (x[0] is None, x[0] or ""))],
                    # what keeps this finding's lines with it once someone works it (Detector.__init__)
                    "key_parts": list(kp), "line_keys": [l["line_key"] for l in ordered]}
        if continues:
            evidence["continues"] = continues
        title = (f"{grp} allowed {code} at ${amount/100:,.2f} instead of its usual ${rate/100:,.2f} on {n} line{'s' if n != 1 else ''} "
                 f"in {year} ({ratio:.0%} of usual)" + (": paid since an earlier finding at this amount was worked" if continues else ""))
        # the id is _fid("SECOND_RATE", *kp), and kp itself starts with "SECOND_RATE": kept as the first release had it
        self._emit("SECOND_RATE", "underpayment", "medium" if gap >= 100000 else "low", "review", title, None, rate * n, gap, None, None,
                   evidence, key_parts=kp, claim=base)
        f = self.findings.get(_fid("SECOND_RATE", *kp))
        if f is not None and deadlines:
            # The last deadline, so the pattern stays in the open work while any of its claims can still be raised; the
            # first is on the row, and the finding counts how many claims are still inside their windows.
            first, last = min(deadlines), max(deadlines)
            f["appeal_deadline"] = last[0]
            f["deadline_basis"] = (f"the last of its {n} claims to close; the first closes on {first[0]}. Each claim has its own window. {last[1]}"
                                   if first[0] != last[0] else f"all {n} claims. {last[1]}")
            f["hint"] = f"Filing windows close {first[0]} to {last[0]}" if first[0] != last[0] else None

    def _mark_paid(self, f: dict, paid_date: str | None, paid: int, note: str, detail: dict) -> None:
        """Close a finding the way a later payment on the same bill does, after the line rules have run."""
        short = f.get("shortfall_cents")
        ev = json.loads(f["evidence"]) if f.get("evidence") else {}
        if not short or short <= 0:
            f["expected_cents"] = f["shortfall_cents"] = short = paid
            ev["basis"] = "amount paid later (no baseline for this code)"
        used = (paid_date, detail.get("trace"), detail.get("claim"))
        f["recovered_cents"], f["recovered_date"] = (0 if used in self.used_payments else min(paid, short)), paid_date
        if used in self.used_payments:
            detail = dict(detail, recovery_counted_elsewhere=True)
        self.used_payments.add(used)
        ev["paid_later"] = detail
        f["evidence"] = json.dumps(ev, default=str)
        f["auto_status"], f["auto_notes"] = "resolved", note

    def resolve_elsewhere(self) -> None:
        """Close a denial, or a bundled visit, that was paid on another claim: a corrected claim under a new bill number, or
        the claim sent to another payer. It must be the same patient (member number and name, with the middle initial when
        both claims carry one; another member number only when EMA ties both claims to one patient), the same date and code,
        a payment still standing (a primary payment, or one covering half the expected amount), made after the denial,
        covering the units denied, for a code paid at most once per patient and date, and, for a claim-level denial, every
        code it lists. A duplicate's denial also closes when the service was paid on another claim before it. Anything
        short of that (a payment before the denial, another member number, fewer units, a code often paid twice in a day,
        an adjudication to the patient's deductible, a bundled procedure paid elsewhere) and anything only EMA shows as
        paid is left open with a hint."""
        for f in self.findings.values():           # same bill, fewer units than billed: a hint
            got = self.partly_paid.get(f.get("line_key") or "")
            if got and not f.get("auto_status") and not f.get("hint"):
                e, n, need = got
                ev = json.loads(f["evidence"]) if f.get("evidence") else {}
                ev["partly_paid"] = {"claim": e[4], "payment_date": e[0], "paid_cents": e[2], "units_paid": n, "units_billed": need}
                f["evidence"] = json.dumps(ev, default=str)
                f["hint"] = f"Partly paid on claim {e[4]} ({e[0]}): {n:g} of {need:g} units"
        todo = [f for f in self.findings.values() if not f.get("auto_status") and f.get("claim_key") and f["category"] in ("denial", "bundling")]
        if not todo:
            return
        c = self.conn

        def lines_of(f):
            ls = self.lines_by_claim.get(f["claim_key"], [])
            if f.get("line_key"):
                return [ln for ln in ls if ln["line_key"] == f["line_key"]]
            listed = set(json.loads(f["evidence"]).get("codes") or []) if f.get("evidence") else set()
            return [ln for ln in ls if ln.get("procedure_code") in listed]
        keys = sorted({f["claim_key"] for f in todo})
        who: dict[str, tuple] = {}
        mid: dict[str, str] = {}
        initial = lambda m: "".join(ch for ch in (m or "").upper() if ch.isalpha())[:1]        # noqa: E731
        for i in range(0, len(keys), 500):
            part = keys[i:i + 500]
            for ck, pid, last, first, middle in c.execute(f"SELECT claim_key, patient_id, patient_last, patient_first, patient_middle FROM claims "
                                                          f"WHERE claim_key IN ({','.join('?' * len(part))})", part):
                who[ck], mid[ck] = patient_keys(pid, last, first), initial(middle)
        need: set[tuple] = set()          # (patient key, code, date) the open findings need paid
        for f in todo:
            for k in who.get(f["claim_key"], (None, None)):
                if k:
                    for ln in lines_of(f):
                        if ln.get("service_date") and ln.get("procedure_code"):
                            need.add((k, ln["procedure_code"], ln["service_date"]))
        if not need:
            return
        patients = {k for k, _c, _d in need}
        claim_who: dict[str, tuple] = {}
        for ck, pid, last, first, middle in c.execute("SELECT claim_key, patient_id, patient_last, patient_first, patient_middle FROM claims"):
            ks = patient_keys(pid, last, first)
            if ks[0] in patients or ks[1] in patients:
                claim_who[ck] = ks
                mid[ck] = initial(middle)
        paid: dict[tuple, list] = defaultdict(list)         # standing payments: (date, claim id, trace, amount, status, units, claim key)
        to_patient: dict[tuple, list] = defaultdict(list)   # adjudicated at $0 with the allowed amount owed by the patient
        for l in self.lines:
            ks = claim_who.get(l["claim_key"])
            if not ks or not l.get("service_date"):
                continue
            amt = self.standing.get(l["line_key"])
            pr = 0 if amt else sum(a for g, _rc, a in self.adj.get(l["line_key"], []) if g == "PR")
            if not amt and not (l.get("claim_status_code") in ("1", "19") and pr > 0):
                continue
            for k in ks:
                key = (k, l.get("procedure_code"), l["service_date"])
                if k and key in need:
                    row = (l.get("payment_date") or l.get("txn_payment_date") or "", l["patient_control_number"], l.get("trace_number"),
                           amt or pr, l.get("claim_status_code"), abs(l.get("paid_units") or 1), l["claim_key"])
                    (paid if amt else to_patient)[key].append(row)
        # Codes paid more than once for a patient on a date (Mohs stages, units, fractions): a payment on another claim may
        # be for another stage or lesion. A code counts as paid once when at least 20 patient-dates had it paid and no more
        # than 1.5 percent had two or more paid lines of it, on one bill or several. Measured for the codes needed.
        codes_needed = sorted({code for _k, code, _d in need if code})
        seen_lines: dict[tuple, set] = defaultdict(set)     # (code, patient, date) -> distinct (bill, line number) paid
        for code, pid, dos, pcn, seq in c.execute(f"""SELECT l.procedure_code, cl.patient_id, l.service_date, l.patient_control_number, l.line_seq
                                     FROM lines l JOIN claims cl ON cl.claim_key = l.claim_key JOIN transactions t ON t.transaction_key = l.transaction_key
                                     WHERE t.superseded = 0 AND l.claim_status_code IN ('1','19') AND l.paid_cents > 0 AND COALESCE(cl.patient_id,'') <> ''
                                       AND l.procedure_code IN ({','.join('?' * len(codes_needed))})""", codes_needed):
            seen_lines[(code, pid, dos)].add((bill_of(pcn), seq))
        groups, repeats = Counter(), Counter()
        for (code, _pid, _dos), paid_lines in seen_lines.items():
            groups[code] += 1
            repeats[code] += len(paid_lines) > 1
        del seen_lines
        once = {code for code, g in groups.items() if g >= 20 and repeats[code] <= 0.015 * g}
        for f in todo:
            mine = f.get("payment_date") or ""
            bill = bill_of(f["patient_control_number"])
            exp = f.get("expected_cents") or 0
            dup = f.get("signal") == "DENIED_DUPLICATE_UNPAID" or (f.get("signal") == "CLAIM_DENIED_UNPAID" and f.get("carc") in DUP_CARCS)
            closable = f["category"] == "denial" or f.get("procedure_code") in EM_CODES
            strict, name = who.get(f["claim_key"], (None, None))
            mine_ema = self.ema_patient.get(f["patient_control_number"])
            mine_mid = mid.get(f["claim_key"], "")
            ls = [ln for ln in lines_of(f) if ln.get("service_date") and ln.get("procedure_code")]
            # every unit the claim billed for the code and date (a second specimen is a second unit), against what its own
            # bill still has paid plus what other bills paid
            units: Counter = Counter({(ln["procedure_code"], ln["service_date"]): self.units_billed(f["claim_key"], ln["procedure_code"], ln["service_date"])
                                      for ln in ls})
            found: dict[tuple, dict] = defaultdict(dict)       # same patient, after the denial: can close
            weak: dict[tuple, dict] = defaultdict(dict)        # a hint only
            why_weak: dict[tuple, str] = {}
            for (code, dos) in units:
                per_unit = exp / max(sum(units.values()), 1) if exp else 0
                for k, other_payer in ((strict, False), (name, True)):
                    for e in paid.get((k, code, dos), []) if k else []:
                        if bill_of(e[1]) == bill:
                            continue
                        if not (e[4] in ("1", "19") or (per_unit and e[3] >= 0.5 * per_unit * e[5])):
                            continue
                        theirs = mid.get(e[6], "")
                        if mine_mid and theirs and mine_mid != theirs:
                            continue                # another person with the same name: a father and son
                        if other_payer and not (mine_ema and self.ema_patient.get(e[1]) == mine_ema):
                            weak[(code, dos)][(e[1], e[0])] = e
                            why_weak.setdefault((code, dos), "it is the same name on another member number, and nothing ties the two claims to one patient")
                        elif not dup and e[0] <= mine:
                            weak[(code, dos)][(e[1], e[0])] = e
                            why_weak.setdefault((code, dos), "that claim was paid before this denial, so the denial may be for something else")
                        else:
                            found[(code, dos)][(e[1], e[0])] = e
            prim_units = lambda rows: sum(e[5] for e in ([e for e in rows if e[4] in ("1", "19")] or rows))        # noqa: E731
            per_line = exp / max(len(units), 1) if exp else None
            covered = [key for key in units if key in found
                       and prim_units(list(found[key].values())) + self.units_paid(self.paid_cover(bill, key[0], key[1], per_line)) >= units[key]]
            best = min((e for v in found.values() for e in v.values()), key=lambda e: e[0], default=None)
            all_once = all(code in once for code, _d in units)
            if closable and best and units and len(covered) == len(units) and all_once:
                how = "the service was already paid on another claim" if dup and best[0] <= mine else "another claim for the same patient, date of service and code"
                self._mark_paid(f, best[0], best[3], f"Paid on a later remittance under claim {best[1]} ({best[0]}, trace {best[2]}, ${best[3]/100:.2f})"
                                if best[0] > mine else f"Already paid under claim {best[1]} ({best[0]}, trace {best[2]}, ${best[3]/100:.2f}): this denial is of a duplicate",
                                {"payment_date": best[0], "claim": best[1], "trace": best[2], "paid_cents": best[3], "claim_status": best[4], "how": how})
                f["hint"] = None
                continue
            ev = json.loads(f["evidence"]) if f.get("evidence") else {}
            reason, pick = None, None
            if best:
                pick = best
                reason = ("a bundled procedure is not closed from another claim; check whether that claim paid this one" if not closable
                          else "this code can be paid more than once for a patient on a date" if not all_once
                          else "the units paid there are fewer than the units denied" if any(key in found for key in units if key not in covered)
                          else "not every code on the denied claim was paid again")
                codes_paid = sorted({code for code, _d in found})
            else:
                pick = min((e for v in weak.values() for e in v.values()), key=lambda e: e[0], default=None)
                if pick is not None:
                    reason = next(why_weak[key] for key in weak if pick in weak[key].values())
                    codes_paid = sorted({code for code, _d in weak})
            if pick:
                ev["maybe_paid_later"] = {"claim": pick[1], "payment_date": pick[0], "paid_cents": pick[3], "codes_paid": codes_paid, "why_not_closed": reason}
                f["hint"] = f"Possibly paid on claim {pick[1]} ({pick[0]})"
                f["evidence"] = json.dumps(ev, default=str)
                continue
            owed = [e for k in (strict,) if k for key in units for e in to_patient.get((k,) + key, [])
                    if bill_of(e[1]) != bill and e[0] > mine]
            if owed:
                e = min(owed)
                ev["adjudicated_elsewhere"] = {"claim": e[1], "payment_date": e[0], "patient_owes_cents": e[3]}
                f["hint"] = f"Processed on claim {e[1]} ({e[0]}): ${e[3]/100:,.2f} owed by the patient"
                f["evidence"] = json.dumps(ev, default=str)
                continue
            # EMA's figures are by claim, code and date: a hint where the claim carries the code once on that date
            per_code = Counter((ln.get("service_date"), ln.get("procedure_code")) for ln in self.lines_by_claim.get(f["claim_key"], []))
            hint = None
            for ln in ls:
                if per_code[(ln.get("service_date"), ln.get("procedure_code"))] > 1:
                    continue
                for h in self.handling.get(ln["line_key"], []):
                    amt, when = h.get("payments_cents") or 0, h.get("transaction_date") or ""
                    if amt > 0 and when > mine and (not exp or amt >= 0.5 * exp) and (hint is None or amt > hint[0]):
                        hint = (amt, when, "EMA ERA & Payment Lines")
                got = self.ema_paid.get((ln.get("patient_control_number"), ln.get("procedure_code"), ln.get("service_date")))
                if got and (not exp or got[0] >= 0.5 * exp) and (hint is None or got[0] > hint[0]):
                    hint = (got[0], None, got[1])
            if hint:
                ev["ema_shows_paid"] = {"paid_cents": hint[0], "date": hint[1], "source": hint[2]}
                f["hint"] = f"EMA shows ${hint[0]/100:,.2f} paid by insurance" + (f" on {hint[1]}" if hint[1] else "") + "; check before marking recovered"
                f["evidence"] = json.dumps(ev, default=str)

    def _emit(self, signal, category, severity, actionable, title, l, expected, shortfall, carc, rarc, evidence,
              key_parts=None, claim=None, resolved_by: tuple | None = None) -> None:
        base = l or claim or {}
        lk = base.get("line_key")
        ema_handling = None
        if lk:
            link = self.links.get(lk)
            if link:
                evidence = dict(evidence, ema_link=link["state"], ema_billed=f"{link['ema_cpt']}{'-' + ','.join(json.loads(link['ema_modifiers'])) if link.get('ema_modifiers') else ''} x{link['ema_units']} ${(link['ema_charges_cents'] or 0)/100:.2f}", ema_claim_status=link.get("ema_claim_status"))
            hs = self.handling.get(lk)
            if hs:
                statuses = sorted({h["adjustment_status"] for h in hs if h.get("adjustment_status")})
                ema_handling = statuses[0] if len(statuses) == 1 else ("MIXED" if statuses else None)
                evidence = dict(evidence, ema_handling=[f"{h['carc'] or ''} {h['adjustment_status'] or ''} ${(h['adjustments_cents'] or 0)/100:.2f} on {h['transaction_date'] or ''}".strip() for h in hs[:8]])
        if resolved_by:
            evidence = dict(evidence, paid_later={"payment_date": resolved_by[0], "trace": resolved_by[1], "paid_cents": resolved_by[2], "claim_status": resolved_by[3]})
        fid = _fid(signal, *(key_parts or (base.get("line_key") or base.get("claim_key") or json.dumps(evidence, sort_keys=True),)))
        kind = base.get("payer_kind", "other")
        grp = base.get("payer_group")
        pay_date = base.get("payment_date") or base.get("txn_payment_date")
        plan = base.get("plan") or (base.get("contract_class") or "").strip() or None
        rule = appeal_rule(grp, kind, plan)
        deadline, deadline_basis = None, None
        fix = corrected_rule(grp, kind, rarc) if actionable == "correct_and_resubmit" else None
        if fix:
            # a corrected claim (a reopening for Medicare Part B) runs on its own clock, from the remittance or the date of service
            start = pay_date if fix["clock"] == "remit" else (base.get("service_date") or base.get("statement_from") or pay_date)
            deadline = _add_days(start, fix["days"])
            deadline_basis = fix["label"] + ("" if fix["verified"] else " (not confirmed against the payer's current manual)")
        elif actionable in ("appeal", "correct_and_resubmit"):
            deadline = _add_days(pay_date, rule["days"])
            deadline_basis = rule["label"] + ("" if rule["verified"] else " (not confirmed against the payer's current manual)")
            if actionable == "correct_and_resubmit":
                deadline_basis += AGREEMENT_FILING_NOTE
        code = base.get("procedure_code")
        billed = evidence.get("billed_code") or base.get("original_procedure_code") or evidence.get("charge_implies") or code
        fam_b, fam_p = family_of(billed), family_of(code)
        level_drop = (fam_b[1] - fam_p[1]) if (fam_b and fam_p and fam_b[0] == fam_p[0] and billed != code) else None
        if evidence.get("paid_like") and family_of(evidence["paid_like"]) and fam_p:
            level_drop = fam_p[1] - family_of(evidence["paid_like"])[1]
        pair = f"{billed}→{code}" if (billed and code and billed != code) else (f"{code} at {evidence['paid_like']} rate" if evidence.get("paid_like") else None)
        if pair is None and signal in DOWNCODE_SIGNALS and code:
            pair = f"{code} denied for level" if not (base.get("allowed_cents") or 0) else f"{code} reduced"
        link = self.links.get(lk) if lk else None
        dos = base.get("service_date") or base.get("statement_from")
        recovered = None
        if resolved_by:
            if not shortfall or shortfall <= 0:
                # no baseline for this line: the amount the payer later paid is the best measure of what was at stake
                expected = shortfall = resolved_by[2] or 0
                evidence = dict(evidence, basis="amount paid on the later remittance (no baseline for this code)")
            recovered = min(resolved_by[2] or 0, shortfall)
            used = (resolved_by[0], resolved_by[1], resolved_by[4] if len(resolved_by) > 4 else None)
            if used in self.used_payments:
                recovered = 0           # this payment is already counted as recovered on another finding
                evidence = dict(evidence, recovery_counted_elsewhere=True)
            self.used_payments.add(used)
        self.findings[fid] = {
            "billed_code": billed, "code_pair": pair, "level_drop": level_drop,
            "pos": base.get("pos") or base.get("facility_type_code"), "pos_class": base.get("pos_class") or pos_class(base.get("facility_type_code")),
            "plan": plan, "month": (pay_date or "")[:7] or None, "service_month": (dos or "")[:7] or None,
            "modifier_class": modifier_class(base.get("modifiers_list") or []) if code else None,
            "location": (link or {}).get("location") or self.claim_location.get(base.get("patient_control_number") or "")
                        or (self.npi_location.get(base.get("rendering_provider_npi") or "", "") + " (provider's usual site)"
                            if self.npi_location.get(base.get("rendering_provider_npi") or "") else None),
            "diagnosis": ((link or {}).get("icd10") or "").split(",")[0].strip() or None,
            "deadline_basis": deadline_basis, "recovered_cents": recovered, "recovered_date": resolved_by[0] if resolved_by else None,
            "finding_id": fid, "signal": signal, "category": category, "severity": severity, "actionable": actionable, "title": title,
            "line_key": base.get("line_key"), "claim_key": base.get("claim_key"), "transaction_key": base.get("transaction_key"),
            "patient_control_number": base.get("patient_control_number"),
            "payer_name": base.get("payer_name"), "payer_group": grp, "payer_kind": kind, "payer_id": base.get("payer_id"),
            "rendering_provider_npi": base.get("rendering_provider_npi"), "rendering_provider_name": base.get("rp_name") or base.get("rendering_provider_name"),
            "provider_tier": base.get("provider_tier"),
            "service_date": dos, "payment_date": pay_date, "year": base.get("year"),
            "procedure_code": code, "modifiers": ",".join(base.get("modifiers_list") or []) or None,
            "original_procedure_code": base.get("original_procedure_code"), "code_category": code_category(code),
            "charge_cents": base.get("charge_cents"), "allowed_cents": base.get("allowed_cents"), "paid_cents": base.get("paid_cents"),
            "expected_cents": expected, "shortfall_cents": shortfall,
            "carc": carc, "carc_text": carc_text(carc), "rarc": rarc, "rarc_text": rarc_text((rarc or "").split(",")[0]),
            "evidence": json.dumps(evidence, default=str),
            "appeal_deadline": deadline,
            "ema_handling": ema_handling,
            "auto_status": "resolved" if resolved_by else None,
            "auto_notes": f"Paid on a later remittance ({resolved_by[0]}, trace {resolved_by[1]}, ${(resolved_by[2] or 0)/100:.2f})" if resolved_by else None,
            "run_id": self.run_id,
        }

    # ------------------------------------------------------------------ line rules
    def run_line_rules(self) -> None:
        # a denied visit with a lower level paid in its place is one event: the paid line must not also count as a downcode
        swap_paid: set[str] = set()
        primary_changes: set[tuple] = set()
        for claim_lines in self.lines_by_claim.values():
            for x in claim_lines:
                cx, st = x.get("procedure_code"), x.get("claim_status_code")
                if st not in ("1", "19") or not cx:
                    continue
                ox = x.get("original_procedure_code")
                if ox and ox != cx:
                    primary_changes.add((bill_of(x.get("patient_control_number")), x.get("service_date"), ox, cx))
                if cx in EM_CODES and not (x.get("paid_cents") or 0) and not (x.get("allowed_cents") or 0):
                    carcs_x = {rc for g, rc, _a in self.adj.get(x["line_key"], []) if g in ("CO", "PI", "OA")}
                    if carcs_x & LEVEL_CARCS or any(r in LEVEL_RARCS for r in self.rem.get(x["line_key"], [])):
                        for o in claim_lines:
                            if o["line_key"] != x["line_key"] and (o.get("paid_cents") or 0) > 0 and o.get("service_date") == x.get("service_date") \
                                    and is_downcode(cx, o.get("procedure_code")):
                                swap_paid.add(o["line_key"])
        for l in self.lines:
            status = l.get("claim_status_code")
            code = l.get("procedure_code")
            if not code or l["line_key"] in swap_paid:
                continue
            mods = l["modifiers_list"]
            adj, top, pr, carcs = self._line_carcs(l)
            rarcs = self.rem.get(l["line_key"], [])
            rarc = ",".join(rarcs) if rarcs else None
            primary = status in ("1", "19")
            orig = l.get("original_procedure_code")
            if orig == code:
                orig = None     # SVC06 repeats the paid code when only the modifiers or units changed
            allowed = l.get("allowed_cents") or 0
            paid = l.get("paid_cents") or 0
            level_amt = sum(a for g, rc, a in adj if rc in LEVEL_CARCS and g in ("CO", "PI", "OA"))
            level_flag = bool(carcs & LEVEL_CARCS) or (code in EM_CODES and any(r in LEVEL_RARCS for r in rarcs))

            # ---- downcoding and code changes (any claim status)
            if orig and orig != code:
                # re-adjudication remits split lines into pieces (B13 "previously paid", token charges): not appealable events
                claim_lines = self.lines_by_claim.get(l["claim_key"], [])
                readjudicated = any(rc == "B13" for ln in claim_lines for _g, rc, _a in self.adj.get(ln["line_key"], [])) \
                    or any(rc == "B13" for _g, rc, _a in self.claim_adj.get(l["claim_key"], []))
                if readjudicated or (l.get("charge_cents") or 0) < 2000:
                    continue
                # a secondary payer that echoes the primary's code change is the same event, not a second downcode
                if not primary and (bill_of(l.get("patient_control_number")), l.get("service_date"), orig, code) in primary_changes:
                    continue
                if is_downcode(orig, code):
                    exp, basis = self.downcode_expected(l, orig, code, allowed)
                    short = (exp - allowed) if exp else None
                    ev = {"basis": basis, "original_modifiers": l.get("original_modifiers"), "claim_status": status}
                    if level_flag:
                        ev["level_adjustment"] = f"{'/'.join(sorted(carcs & LEVEL_CARCS)) or rarc} ${level_amt/100:.2f}"
                    self._emit("DOWNCODE_SVC06", "downcoding", "high" if primary else "medium", "appeal",
                               f"Billed {orig}, paid as {code} ({l['payer_group']})", l, exp, short, top, rarc, ev,
                               resolved_by=self.later_payment(l, orig, expected=exp))
                else:
                    exp, basis = self.expected_allowed(l, orig)
                    short = (exp - allowed) if exp else None
                    if short and short > 100:
                        self._emit("CODE_CHANGED", "downcoding", "medium", "review",
                                   f"Payer changed {orig} to {code} ({l['payer_group']})", l, exp, short, top, rarc, {"basis": basis, "claim_status": status})
                continue
            if primary and not orig:
                # the claim file or EMA shows a higher level was billed and the remit does not admit to a change
                hb = self.billed_higher(l, code)
                if hb:
                    exp, basis = self.downcode_expected(l, hb[0], code, allowed)
                    short = (exp - allowed) if exp else None
                    self._emit("DOWNCODE_VS_BILLED", "downcoding", "high", "appeal",
                               f"Billed {hb[0]} per the {hb[1]}, remit shows {code} with no code change reported ({l['payer_group']})",
                               l, exp, short, top, rarc, {"basis": basis, "billed_code": hb[0], "billed_source": hb[1], "billed_codes": hb[2]},
                               resolved_by=self.later_payment(l, hb[0], expected=exp))
                    continue
                implied = self.fees.implied_original(l["year"], code, l.get("charge_cents"))
                if implied:
                    exp, basis = self.downcode_expected(l, implied, code, allowed)
                    short = (exp - allowed) if exp else None
                    self._emit("DOWNCODE_CHARGE", "downcoding", "high", "appeal",
                               f"Charge matches the {implied} fee but paid as {code} ({l['payer_group']})", l, exp, short, top, rarc,
                               {"basis": basis, "charge_implies": implied}, resolved_by=self.later_payment(l, implied, expected=exp))
                    continue
                # payment cut for the level with the billed code left in place (Anthem's CARC 186 pattern)
                if level_flag and code in EM_CODES and allowed > 0:
                    usual, basis, _r = self.usual_rate(l, code)
                    codes186 = "/".join(sorted(carcs & LEVEL_CARCS)) or (rarc or "")
                    exp = usual
                    if level_amt > 0 and (not usual or allowed + level_amt < usual):
                        # the payer's own adjustment amount, unless it exceeds its usual rate for this provider type (it is
                        # computed from the physician fee even when the visit is paid at the non-physician rate)
                        exp, basis = allowed + level_amt, f"allowed amount plus the payer's own level-of-care adjustment ({codes186} ${level_amt/100:.2f})"
                    short = (exp - allowed) if exp else None
                    if short and short > 0:
                        like = self.paid_like(l, code, allowed)
                        self._emit("DOWNCODE_LEVEL_REDUCED", "downcoding", "high", "appeal",
                                   f"{code} paid at a reduced level with the code unchanged ({codes186}{', paid like ' + like if like else ''}, {l['payer_group']})",
                                   l, exp, short, next(iter(sorted(carcs & LEVEL_CARCS)), top), rarc,
                                   {"basis": basis, "paid_like": like, "level_adjustment_cents": level_amt, "usual_rate_cents": usual},
                                   resolved_by=self.later_payment(l, expected=exp, min_paid=paid + short // 2))
                        continue
                if code in EM_CODES and allowed > 0 and paid > 0 and l.get("paid_units") in (None, 1, 1.0) and not l["pricing_mod"]:
                    own, _b, own_r = self.usual_rate(l, code)
                    if own and own_r and allowed < own:
                        lower = self.paid_like(l, code, allowed)
                        if lower:
                            self._emit("DOWNCODE_RATE", "downcoding", "medium", "appeal",
                                       f"{code} allowed at the {lower} rate ({l['payer_group']})", l, own, own - allowed, top, rarc, {"paid_like": lower},
                                       resolved_by=self.later_payment(l, expected=own, min_paid=paid + (own - allowed) // 2))
                            continue
                # remark N22 says the payer added or changed the code but no original code was reported
                if "N22" in rarcs and code not in EM_CODES and allowed > 0:
                    _rate, _b, r = self.usual_rate(l, code)
                    exp, basis = self.expected_allowed(l, code)         # rank-aware: a correctly reduced second procedure is not short
                    self._emit("CODE_CHANGED", "downcoding", "medium", "review",
                               f"Payer says it added or changed {code} (remark N22) without reporting the billed code ({l['payer_group']})",
                               l, exp, (exp - allowed) if (exp and r and exp > allowed) else 0, top, rarc, {"basis": basis, "claim_status": status})
                    continue

            if not primary:
                continue

            # ---- level-of-service denials on unpaid visits (CARC 150/186 or a level remark)
            if level_flag and code in EM_CODES and paid == 0 and allowed == 0:
                exp, basis = self.expected_allowed(l, code)
                # a lower-level visit line paid on the same claim and date means the payer swapped the line rather than denied the visit
                swap = next((o for o in self.lines_by_claim[l["claim_key"]] if o["line_key"] != l["line_key"] and (o.get("paid_cents") or 0) > 0
                             and o.get("service_date") == l.get("service_date") and is_downcode(code, o.get("procedure_code"))), None)
                if swap and exp:
                    self._emit("DOWNCODE_LINE_SWAP", "downcoding", "high", "appeal",
                               f"Billed {code} denied and a {swap['procedure_code']} line paid in its place ({l['payer_group']})",
                               l, exp, exp - (swap.get("allowed_cents") or 0), top or next(iter(carcs & LEVEL_CARCS), None), rarc,
                               {"basis": basis, "paid_line_code": swap["procedure_code"], "paid_line_allowed_cents": swap.get("allowed_cents")},
                               resolved_by=self.later_payment(l, expected=exp))
                    continue
                if exp:
                    self._emit("DOWNCODE_LEVEL_DENIED", "downcoding", "high", "appeal",
                               f"{code}: payer says documentation does not support the level ({top or ','.join(sorted(carcs & LEVEL_CARCS)) or rarc})",
                               l, exp, exp, top or next(iter(carcs & LEVEL_CARCS), None), rarc, {"basis": basis},
                               resolved_by=self.later_payment(l, expected=exp))
                    continue

            # ---- zero-paid lines (silent denials inside paid claims)
            if paid == 0 and allowed == 0 and pr == 0 and (l.get("charge_cents") or 0) > 0 and top:
                exp, basis = self.expected_allowed(l, code)
                cat = code_category(code)
                if code == "99024" or code == "G2211" or (l.get("charge_cents") or 0) <= 100:
                    continue
                later = self.later_payment(l, expected=exp)
                emit = self._emit
                self._emit = lambda *a, **k: emit(*a, **dict(k, resolved_by=later))  # noqa: E731
                if top in BUNDLING_CARCS or "M80" in rarcs or "N19" in rarcs or "M15" in rarcs or "N20" in rarcs:
                    if code in EM_CODES:
                        companions = [o for o in self.lines_by_claim[l["claim_key"]] if o["line_key"] != l["line_key"]]
                        comp_codes = sorted({o["procedure_code"] for o in companions if o.get("procedure_code")})
                        globals_ = [GLOBAL_DAYS.get(c) for c in comp_codes]
                        if "25" in mods:
                            sig, sev, act, ttl = "EM_BUNDLED_MOD25", "high", "appeal", f"{code}-25 bundled into same-day procedure ({l['payer_group']})"
                        elif "57" in mods:
                            if any(g == 90 for g in globals_):
                                sig, sev, act, ttl = "EM_BUNDLED_MOD57_MAJOR", "medium", "appeal", f"{code}-57 bundled; 90-day global procedure same day"
                            else:
                                sig, sev, act, ttl = "EM_BUNDLED_MOD57_MINOR", "high", "correct_and_resubmit", f"{code}-57 with 0/10-day global procedure: modifier 57 is wrong, use 25"
                        elif "24" in mods:
                            sig, sev, act, ttl = "EM_BUNDLED_MOD24", "medium", "appeal", f"{code}-24 denied inside a global period"
                        elif not mods:
                            sig, sev, act, ttl = "EM_BUNDLED_NO_MODIFIER", "high", "correct_and_resubmit", f"{code} billed with a procedure and no modifier 25"
                        else:
                            sig, sev, act, ttl = "EM_BUNDLED_MOD25", "medium", "appeal", f"{code} ({','.join(mods)}) bundled"
                        self._emit(sig, "bundling", sev, act, ttl, l, exp, exp, top, rarc, {"basis": basis, "same_day_codes": comp_codes, "global_days": globals_})
                    elif code in ADD_ON_CODES:
                        self._emit("ADDON_BUNDLED", "bundling", "high", "appeal", f"Add-on {code} paid $0 as bundled (add-ons are payable with their primary)", l, exp, exp, top, rarc, {"basis": basis})
                    else:
                        comp = sorted({o["procedure_code"] for o in self.lines_by_claim[l["claim_key"]] if o["line_key"] != l["line_key"] and o.get("procedure_code")})
                        self._emit("PROCEDURE_BUNDLED", "bundling", "high" if (exp or 0) >= 10000 else "medium", "appeal",
                                   f"{code} bundled into another same-day service ({top})", l, exp, exp, top, rarc, {"basis": basis, "same_day_codes": comp})
                    self._emit = emit
                    continue
                if top in INFO_CARCS:
                    if any(r in MODIFIER_RARCS for r in rarcs):
                        self._emit("DENIED_MISSING_MODIFIER", "denial", "high", "correct_and_resubmit", f"{code} denied: missing/invalid modifier ({rarc})", l, exp, exp, top, rarc, {"basis": basis})
                    elif any(r in CODING_RARCS for r in rarcs):
                        self._emit("DENIED_CODING", "denial", "high", "correct_and_resubmit", f"{code} denied: coding error ({rarc})", l, exp, exp, top, rarc, {"basis": basis})
                    else:
                        self._emit("DENIED_MISSING_INFO", "denial", "medium", "correct_and_resubmit", f"{code} denied for missing information ({top} {rarc or ''})", l, exp, exp, top, rarc, {"basis": basis})
                    self._emit = emit
                    continue
                if top in CODING_CARCS:
                    self._emit("DENIED_CODING", "denial", "high", "correct_and_resubmit", f"{code} denied: code/modifier inconsistent ({top})", l, exp, exp, top, rarc, {"basis": basis, "modifiers": mods})
                    self._emit = emit
                    continue
                if top in PRIMARY_CARCS:
                    self._emit("DENIED_MISSING_PRIMARY", "denial", "medium", "review", f"{code} denied: qualifying/primary service missing or denied ({top})", l, exp, exp, top, rarc, {"basis": basis})
                    self._emit = emit
                    continue
                if top in AUTH_CARCS:
                    self._emit("DENIED_AUTH", "denial", "high", "appeal", f"{code} denied: authorization ({top})", l, exp, exp, top, rarc, {"basis": basis})
                    self._emit = emit
                    continue
                if top in LEVEL_CARCS:
                    self._emit("DENIED_LEVEL_OF_SERVICE", "denial", "high", "appeal",
                               f"{code} denied: payer says the records do not support the service as billed ({top})", l, exp, exp, top, rarc, {"basis": basis})
                    self._emit = emit
                    continue
                if top in MEDNEC_CARCS:
                    self._emit("DENIED_MEDICAL_NECESSITY", "denial", "high", "appeal", f"{code} denied: medical necessity / documentation ({top})", l, exp, exp, top, rarc, {"basis": basis})
                    self._emit = emit
                    continue
                if top in FREQ_CARCS:
                    self._emit("DENIED_FREQUENCY", "denial", "medium", "appeal", f"{code} denied: frequency/units ({top})", l, exp, exp, top, rarc, {"basis": basis})
                    self._emit = emit
                    continue
                if top in TIMELY_CARCS:
                    self._emit("DENIED_TIMELY_FILING", "denial", "high", "review", f"{code} denied: timely filing", l, exp, exp, top, rarc, {"basis": basis})
                    self._emit = emit
                    continue
                if top in PROVIDER_CARCS:
                    self._emit("DENIED_PROVIDER", "denial", "high", "review", f"{code} denied: provider enrollment/credentialing ({top})", l, exp, exp, top, rarc, {"basis": basis})
                    self._emit = emit
                    continue
                if top in DUP_CARCS:
                    have = self.paid_cover(bill_of(l["patient_control_number"]), code, l.get("service_date"), exp)
                    # Units at stake: this line's, plus lines of the code and date paid on this same claim (a second specimen
                    # denied as a duplicate of the first). Other lines denied as duplicates are not counted: a resubmission
                    # that repeats a paid service three times is three copies of one service, not three services.
                    # (for a code paid at most once a day, a second line is a charge posted twice: nothing more is owed)
                    need = abs(l.get("original_units") or l.get("paid_units") or 1) + (0 if code in self.once_codes else sum(
                        abs(o.get("original_units") or o.get("paid_units") or 1) for o in self.lines_by_claim.get(l["claim_key"], [])
                        if o["line_key"] != l["line_key"] and o.get("procedure_code") == code and o.get("service_date") == l.get("service_date")
                        and (o.get("paid_cents") or 0) > 0))
                    if self.units_paid(have) < need:
                        self._emit("DENIED_DUPLICATE_UNPAID", "denial", "medium", "review", f"{code} denied as duplicate but never paid", l, exp, exp, top, rarc, {"basis": basis})
                    self._emit = emit
                    continue
                if top in NONCOV_CARCS:
                    self._emit("DENIED_NONCOVERED", "denial", "low", "review", f"{code} not covered ({top})", l, exp, exp, top, rarc, {"basis": basis})
                    self._emit = emit
                    continue
                if cat not in ("postop",):
                    self._emit("DENIED_OTHER", "denial", "low", "review", f"{code} paid $0 ({top})", l, exp, exp, top, rarc, {"basis": basis})
                self._emit = emit
                continue

            # ---- underpayment on paid lines
            if paid > 0 and allowed > 0 and l.get("paid_units") in (None, 1, 1.0) and not orig and not l["pricing_mod"]:
                if carcs & {"B10", "97", "236", "234"}:
                    continue
                worked_in = self.line_to_pattern.get(l["line_key"]) or self.claim_to_pattern.get((l["patient_control_number"], code, l.get("service_date"), allowed))
                if worked_in:
                    # worked as part of a routine-rate finding: it stays there whatever the baseline now says
                    self.pattern_lines[worked_in].append(l)
                    continue
                rate, basis, r = self.usual_rate(l, code)
                if not rate or not r or r["share"] < 0.4 or r["n"] < 20:
                    continue
                rank = l.get("surg_rank", 0)
                if rank >= 2 and is_surgical(code) and code not in ADD_ON_CODES:
                    pct_paid = allowed / rate
                    policy, learned = self.mppr_pct(l)
                    if pct_paid < policy - 0.07:
                        exp = int(round(rate * policy))
                        self._emit("MPPR_EXCESS", "underpayment", "medium", "appeal",
                                   f"{code} paid at {pct_paid:.0%} of the usual rate as procedure #{rank} ({'this payer normally allows' if learned else 'standard policy'} {policy:.0%})",
                                   l, exp, exp - allowed, "59" if "59" in carcs else top, rarc,
                                   {"basis": basis, "rank": rank, "procedures_on_claim": l.get("surg_count"), "pct_of_usual": round(pct_paid, 3),
                                    "carc_59_reported": "59" in carcs, "policy_pct": policy, "policy_learned": learned})
                    continue
                if code in ADD_ON_CODES and "59" in carcs:
                    c59 = sum(a for _g, rc, a in adj if rc == "59")
                    if c59 > 0:
                        self._emit("MPPR_EXCESS", "underpayment", "high", "appeal", f"Multiple-procedure reduction applied to add-on code {code}", l, allowed + c59, c59, "59", rarc, {"basis": basis})
                        continue
                if allowed < rate and (rate - allowed) >= 200 and (rate - allowed) / rate >= 0.05:
                    one_off = ("UNDERPAID_VS_OWN_RATE", "underpayment", "medium", "appeal",
                               f"{code} allowed ${allowed/100:.2f} vs usual ${rate/100:.2f} ({l['payer_group']})", l, rate, rate - allowed, top, rarc,
                               {"basis": basis, "usual_share": round(r["share"], 3), "usual_n": r["n"], "rank": rank})
                    own = (l["payer_group"], l["year"], code, l["provider_tier"], l["pos_class"])
                    if l["line_key"] not in self.worked_underpaid and r.get("key") == own \
                            and self.rate_counts.get(own, {}).get(allowed, 0) >= SECOND_RATE_MIN_LINES:
                        self.second_rates[(own, allowed)].append((l, one_off))       # grouped if a reason is found, else emitted as is
                        continue
                    self._emit(*one_off)

    # ------------------------------------------------------------------ claim rules
    def run_claim_rules(self) -> None:
        """Whole claims denied (CLP02 = 4) and still unpaid. A denied claim counts only for the units of its lines that the
        bill has not been paid for, at any date, by payments still standing (primary payments, or secondary ones covering
        half the expected amount; a secondary's share of units the primary paid adds none), and is sized by those units. An
        undated line counts as paid when a primary payment of its code on the bill came after the denial. A claim with no
        service lines counts unless a primary payment on the bill came after it, or it is a duplicate's denial and the bill
        stands paid. One payer's repeated denials of a bill are one unpaid service: one submission carries the finding and
        lists the others, which add only lines it does not have. A submission someone worked always keeps its own finding;
        the carrier is otherwise the submission that already has a finding, else the newest; a submission denied after the
        last change someone made on a worked carrier is listed on its own, so a repeat denial is seen. A finding someone
        worked whose lines the bill has since paid is closed by that payment, so the recovery is counted."""
        c = self.conn
        c.row_factory = sqlite3.Row
        by_bill: dict[tuple, list[dict]] = defaultdict(list)
        for r in c.execute("""SELECT cl.*, t.payment_date AS txn_payment_date, COALESCE(t.payer_id, t.payer_name) AS txn_payer
                              FROM claims cl JOIN transactions t ON t.transaction_key=cl.transaction_key
                              WHERE t.superseded=0 AND cl.claim_status_code='4'"""):
            cl = dict(r)
            by_bill[(bill_of(cl["patient_control_number"]), cl.get("txn_payer") or cl.get("payer_id") or cl.get("payer_name"))].append(cl)
        bills = {b for b, _p in by_bill}
        bill_lines: dict[str, list] = defaultdict(list)                 # lines of processed (not wholly denied) claims, per bill
        for l in self.lines:
            if l.get("claim_status_code") in ("1", "19", "2", "3") and bill_of(l["patient_control_number"]) in bills:
                bill_lines[bill_of(l["patient_control_number"])].append(l)

        at = lambda o: o.get("payment_date") or o.get("txn_payment_date") or ""          # noqa: E731
        who = lambda o: o.get("payer_id") or o.get("payer_name")                          # noqa: E731
        listed_lines = {f["line_key"] for f in self.findings.values() if f.get("line_key")} - self.closed_lines
        billed_as: dict[tuple, str] = {}        # (bill, date, billed code) -> claim id of a finding saying that code was billed (a downcode)
        for f in self.findings.values():
            if f.get("line_key") and f.get("billed_code") and f["billed_code"] != f.get("procedure_code"):
                billed_as.setdefault((bill_of(f["patient_control_number"]), f.get("service_date"), f["billed_code"]), f["patient_control_number"])
        reversed_at: dict[tuple, str] = {}      # a processing its payer reversed since settles nothing
        paid_by_payer: set[tuple] = set()       # (claim id, payer) with a payment still standing
        for l in self.lines:
            if bill_of(l["patient_control_number"]) not in bills:
                continue
            if l.get("claim_status_code") == "22":
                k = (l["patient_control_number"], l.get("procedure_code"), l.get("service_date"), who(l))
                reversed_at[k] = max(reversed_at.get(k, ""), at(l))
            elif l["line_key"] in self.standing:
                paid_by_payer.add((l["patient_control_number"], who(l)))

        def settled(bill: str, code: str, dos: str | None, payer: str | None, when: str, pcn: str, dates: set) -> tuple | None:
            """How another claim of the bill settled this code and date other than by a payment still standing, if it did:
            - processed to the patient (copay, coinsurance, deductible) by the same payer after this denial (a corrected
              claim), or by another payer that paid something on this same claim id (a crossover the primary paid and the
              secondary denied); a secondary owes what a primary put to the deductible, so a primary that paid nothing on
              the claim settles nothing;
            - denied as a primary line that has its own finding, which no person dismissed or marked lost;
            - paid under another code (SVC06, or a finding saying this code was billed), its own downcode finding;
            - billed again under another date, for a code this claim has on one date only.
            A processing its payer reversed since counts for nothing."""
            for o in bill_lines.get(bill, []):
                if o.get("service_date") != dos or o.get("claim_status_code") not in ("1", "19"):
                    continue
                if reversed_at.get((o["patient_control_number"], o.get("procedure_code"), o.get("service_date"), who(o)), "") > at(o):
                    continue
                if o.get("procedure_code") == code and not (o.get("paid_cents") or 0):
                    if (o.get("allowed_cents") or 0) > 0 or any(g == "PR" for g, _rc, _a in self.adj.get(o["line_key"], [])):
                        if (who(o) == payer and at(o) > when) or (who(o) != payer and (pcn, who(o)) in paid_by_payer):
                            return ("processed to the patient", o["patient_control_number"])
                    elif o["line_key"] in listed_lines:
                        return ("denied again as a line", o["patient_control_number"])
                if o.get("original_procedure_code") == code and at(o) > when and o["line_key"] in listed_lines:
                    return ("paid under another code", o["patient_control_number"])
            if (bill, dos, code) in billed_as:
                return ("paid under another code", billed_as[(bill, dos, code)])
            corrected = [o for o in bill_lines.get(bill, []) if at(o) > when and o.get("service_date") == dos and o.get("claim_status_code") in ("1", "19")
                         and o.get("procedure_code") not in (code, None) and o["line_key"] in self.standing
                         and code_category(o.get("procedure_code")) == code_category(code)
                         and code_category(code) in ("em", "biopsy", "destruction", "excision", "repair", "mohs")]   # a level or size corrected
            if corrected and not any(o.get("procedure_code") == code and o.get("service_date") == dos and at(o) > when for o in bill_lines.get(bill, [])):
                return (f"billed again as {corrected[0]['procedure_code']}", corrected[0]["patient_control_number"])
            if dos and len(dates) == 1:
                later = [o for o in bill_lines.get(bill, []) if at(o) > when and o.get("claim_status_code") in ("1", "19") and o.get("procedure_code") == code]
                moved = [o for o in later if o.get("service_date") not in dates]
                if moved and not any(o.get("service_date") == dos for o in later):
                    return ("billed again under another date", moved[0]["patient_control_number"])
            return None

        bill_primary: dict[str, list[str]] = defaultdict(list)          # dates of primary payments standing, per bill
        for (b, _code), evs in self.paid_by_code.items():
            if b in bills:
                bill_primary[b] += [e[0] for e in evs if e[3] in ("1", "19")]
        for r in c.execute("""SELECT cl.patient_control_number, COALESCE(cl.payment_date, t.payment_date) FROM claims cl
                              JOIN transactions t ON t.transaction_key = cl.transaction_key
                              WHERE t.superseded = 0 AND cl.claim_status_code IN ('1','19') AND cl.paid_cents > 0
                                AND NOT EXISTS (SELECT 1 FROM lines l WHERE l.claim_key = cl.claim_key)
                                AND NOT EXISTS (SELECT 1 FROM claims r JOIN transactions rt ON rt.transaction_key = r.transaction_key
                                                WHERE rt.superseded = 0 AND r.claim_status_code = '22' AND r.patient_control_number = cl.patient_control_number
                                                  AND COALESCE(rt.payer_id, rt.payer_name) IS COALESCE(t.payer_id, t.payer_name)
                                                  AND COALESCE(r.payment_date, rt.payment_date) > COALESCE(cl.payment_date, t.payment_date))"""):
            if bill_of(r[0]) in bills:
                bill_primary[bill_of(r[0])].append(r[1] or "")          # a payment sent without service lines
        for (bill, _payer), claims in by_bill.items():
            claims.sort(key=lambda cl: (cl.get("payment_date") or cl.get("txn_payment_date") or "", cl["patient_control_number"]), reverse=True)
            claims.sort(key=lambda cl: 0 if self.prev_claims.get(cl["claim_key"], {}).get("worked") else 1 if cl["claim_key"] in self.prev_claims else 2)
            reported: set[tuple] = set()
            carrier: dict | None = None
            decided = ""            # when someone last changed the carrier's finding, if they worked it
            repeats: set[tuple] = set()         # what the denials after that decision already list
            repeat_carrier: dict | None = None
            for cl in claims:
                when = cl.get("payment_date") or cl.get("txn_payment_date") or ""
                prev = self.prev_claims.get(cl["claim_key"])
                grp, kind = payer_group_kind(cl.get("payer_name"), cl.get("claim_filing_indicator"))
                cl["payer_group"], cl["payer_kind"] = grp, kind
                cl["year"] = (cl.get("statement_from") or cl.get("payment_date") or "")[:4]
                cl["rp_name"] = cl.get("rendering_provider_name")
                lines = self.lines_by_claim.get(cl["claim_key"], [])
                adj = self.claim_adj.get(cl["claim_key"], []) + [(g, rc, a) for ln in lines for g, rc, a in self.adj.get(ln["line_key"], [])]
                carcs = Counter(rc for _g, rc, _a in adj if rc not in IGNORE_CARCS)
                top = top_of(carcs)[0] if carcs else None
                covering = None         # the payment that covers it, when it is covered
                if not lines:
                    later = sorted(d for d in bill_primary.get(bill, []) if d > when)
                    crossed = any(p != (cl.get("txn_payer") or cl.get("payer_id") or cl.get("payer_name")) for c_, p in paid_by_payer if c_ == cl["patient_control_number"])
                    if crossed and not (prev and later):
                        continue
                    if later or (top in DUP_CARCS and bill_primary.get(bill)):
                        if prev and later:
                            covering = later[0]
                        else:
                            continue
                need: dict[tuple, list] = {}            # (code, date) -> [units billed, expected]
                for ln in lines:
                    ln2 = dict(ln, payer_group=grp, payer_kind=kind, year=cl["year"], provider_tier=self.provider_tier.get(ln.get("rendering_provider_npi") or "", "unknown"))
                    e, _ = self.expected_allowed(ln2, ln["procedure_code"])
                    k = need.setdefault((ln["procedure_code"], ln.get("service_date")), [0, 0])
                    k[0] += abs(ln.get("original_units") or ln.get("paid_units") or 1)
                    k[1] += e or 0
                unpaid: dict[tuple, list] = {}          # (code, date) -> [units unpaid, expected for them]
                pays: list[tuple] = []
                elsewhere: dict[tuple, tuple] = {}
                noted: dict[tuple, tuple] = {}
                payer = cl.get("txn_payer") or cl.get("payer_id") or cl.get("payer_name")
                for (code, dos), (units, exp_k) in need.items():
                    if dos:
                        evs = self.paid_cover(bill, code, dos, exp_k // max(units, 1) if exp_k else None)
                        got = self.units_paid(evs)
                    else:               # an undated line: a primary payment of the code on the bill after the denial
                        evs = [e for e in self.paid_by_code.get((bill, code), []) if e[3] in ("1", "19") and e[0] > when]
                        got = min(units, self.units_paid(evs))
                    pays += [e for e in evs if e[0] > when]
                    if got < units:
                        how = settled(bill, code, dos, payer, when, cl["patient_control_number"],
                                      {ln.get("service_date") for ln in lines if ln.get("procedure_code") == code})
                        if how and not (prev and prev["worked"]):
                            elsewhere[(code, dos)] = how
                            continue
                        if how:         # someone is working this denial: it stays listed until a payment covers it
                            noted[(code, dos)] = how
                        unpaid[(code, dos)] = [units - got, int(round(exp_k * (units - got) / units)) if units else exp_k]
                if lines and not unpaid:
                    if prev and pays:           # listed before and since paid on the bill: closed by that payment, so it counts
                        covering = min(pays, key=lambda e: (e[0], e[1] or "", e[4] or "", e[2]))
                    else:
                        continue
                if covering is not None:
                    # someone worked this denial and the bill has since paid it: close it by that payment, so it counts
                    exp_all = sum(v[1] for v in need.values()) or None
                    self._emit("CLAIM_DENIED_UNPAID", "denial", "medium", "review", f"Claim denied ({top or 'no CARC'}) and since paid on the bill",
                               None, exp_all, exp_all, top, None, {"codes": sorted({code for code, _d in need})},
                               key_parts=(cl["claim_key"],), claim=cl,
                               resolved_by=covering if isinstance(covering, tuple) else (covering, None, None, "1"))
                    continue
                # a submission a person dismissed, marked lost or recovered keeps its own finding, but neither carries the
                # bill's other denials nor counts as listing their lines
                closed = bool(prev and prev["worked"] and prev.get("status") in ("dismissed", "lost", "resolved"))
                again = bool(decided) and when > decided[:10] and not (prev and prev["worked"])
                if again:               # denied again after someone acted: listed apart from their finding, the repeats together
                    novel = {k: v for k, v in unpaid.items() if k not in repeats} if lines else ({} if repeat_carrier else {("*", None): [1, 0]})
                    home = repeat_carrier
                elif prev and prev["worked"]:
                    novel = unpaid if lines else {("*", None): [1, 0]}       # someone worked this one: it keeps its own finding
                    home = None
                else:
                    novel = {k: v for k, v in unpaid.items() if k not in reported} if lines else ({} if carrier else {("*", None): [1, 0]})
                    home = carrier
                if not novel:
                    if home is not None:
                        home.setdefault("_also", []).append(cl["patient_control_number"])
                    continue
                if not closed:
                    (repeats if again else reported).update(novel)
                rarcs = self.claim_rem.get(cl["claim_key"], []) + [x for ln in lines for x in self.rem.get(ln["line_key"], [])]
                rarc = ",".join(sorted(set(rarcs))) if rarcs else None
                codes = [code for code, _d in novel if code != "*"]
                exp = sum(v[1] for v in novel.values())
                sev = "high" if exp >= 20000 else "medium"
                if top in TIMELY_CARCS:
                    act = "review"
                elif top in INFO_CARCS or top in CODING_CARCS:
                    act = "correct_and_resubmit"
                elif top in NONCOV_CARCS or top in DUP_CARCS:
                    act = "review"
                else:
                    act = "appeal"
                paid_codes = sorted({code for (code, _d) in need if (code, _d) not in unpaid and (code, _d) not in elsewhere})
                ev = {"codes": codes, "claim_charge_cents": cl.get("charge_cents"), "payer_claim_control_number": cl.get("payer_claim_control_number")}
                if elsewhere:
                    ev["settled_elsewhere"] = {code: f"{how} (claim {other})" for (code, _d), (how, other) in elsewhere.items()}
                rebilled = sorted({o["patient_control_number"] for o in bill_lines.get(bill, [])
                                   if (o.get("payment_date") or o.get("txn_payment_date") or "") > when and o["patient_control_number"] != cl["patient_control_number"]})
                if paid_codes:
                    ev["codes_paid_on_the_bill"] = paid_codes
                part = {code: v[0] for (code, _d), v in novel.items() if code != "*" and v[0] < need.get((code, _d), [0])[0]}
                if part:
                    ev["units_unpaid"] = part
                self._emit("CLAIM_DENIED_UNPAID", "denial", sev, act, f"Claim denied ({top or 'no CARC'}) and never paid: {', '.join(codes[:6]) or 'no service lines'}",
                           None, exp or None, exp or None, top, rarc, ev, key_parts=(cl["claim_key"],), claim=cl)
                f = self.findings.get(_fid("CLAIM_DENIED_UNPAID", cl["claim_key"]))
                if f is not None:
                    later_lines = [o for o in bill_lines.get(bill, []) if at(o) > when and o["patient_control_number"] != cl["patient_control_number"]]
                    dropped = [k for k in novel if k[0] != "*" and not any(o.get("procedure_code") == k[0] and o.get("service_date") == k[1] for o in later_lines)]
                    words = {"processed to the patient": "Processed to the patient", "denied again as a line": "Denied again as a line",
                             "paid under another code": "Paid under another code", "billed again under another date": "Billed again under another date"}
                    if noted:
                        (how, other) = next(iter(noted.values()))
                        f["hint"] = f"{words.get(how, how)} on claim {other}; kept listed while it is being worked"
                    elif dropped and later_lines:
                        code0, d0 = dropped[0]
                        instead = [o for o in later_lines if o.get("service_date") == d0 and o.get("procedure_code") != code0
                                   and code_category(o.get("procedure_code")) == code_category(code0) and o.get("procedure_code") not in codes]
                        f["hint"] = (f"Billed again on claim {instead[0]['patient_control_number']} as {instead[0]['procedure_code']} instead of {code0}"
                                     if instead else f"Billed again on claim {rebilled[0]} without {', '.join(k[0] for k in dropped[:3])}; check whether it was dropped on purpose")
                    elif novel and later_lines and all(k[0] != "*" for k in novel):
                        code0, d0 = next(iter(novel))
                        billed_units = need.get((code0, d0), [0])[0]
                        f["hint"] = f"Partly paid on claim {rebilled[0]}: {billed_units - novel[(code0, d0)][0]:g} of {billed_units:g} units"
                if again and repeat_carrier is None and f is not None:
                    repeat_carrier = f
                elif not again and carrier is None and f is not None and not closed:
                    carrier = f
                    decided = prev["changed"] if prev and prev["worked"] else ""
            for home in (carrier, repeat_carrier):
                if home is not None and home.get("_also"):
                    ev = json.loads(home["evidence"])
                    ev["other_denied_submissions"] = home.pop("_also")
                    home["evidence"] = json.dumps(ev, default=str)

    # ------------------------------------------------------------------ aggregate rules
    def run_aggregate_rules(self) -> None:
        c = self.conn
        c.row_factory = sqlite3.Row
        rows = c.execute("""SELECT p.payer_name, substr(p.payment_date,1,7) AS month, p.reason_code, p.reference, p.amount_cents, p.transaction_key, p.payment_date
                            FROM plb p JOIN transactions t ON t.transaction_key=p.transaction_key WHERE t.superseded=0""").fetchall()
        fees: dict[tuple, dict] = {}
        for r in rows:
            amt = r["amount_cents"] or 0
            ref = (r["reference"] or "").upper()
            is_fee = (r["reason_code"] == "AH") or (r["reason_code"] in ("CS", "72", "J1") and "FEE" in ref)
            if is_fee and amt > 0:
                grp, kind = normalize_payer(r["payer_name"])
                key = (r["payer_name"], r["month"])
                f = fees.setdefault(key, {"payer_name": r["payer_name"], "payer_group": grp, "payer_kind": kind, "month": r["month"], "n": 0, "cents": 0, "refs": Counter(), "year": (r["month"] or "")[:4], "payment_date": r["payment_date"]})
                f["n"] += 1
                f["cents"] += amt
                f["refs"][ref[:40] or r["reason_code"]] += 1
                if r["payment_date"] and (not f["payment_date"] or r["payment_date"] < f["payment_date"]):
                    f["payment_date"] = r["payment_date"]       # the month's first, whatever order the rows come in
            if r["reason_code"] == "WO" and amt >= 50000:
                grp, kind = normalize_payer(r["payer_name"])
                base = {"payer_name": r["payer_name"], "payer_group": grp, "payer_kind": kind, "transaction_key": r["transaction_key"], "payment_date": r["payment_date"], "year": (r["payment_date"] or "")[:4]}
                self._emit("TAKEBACK", "fees", "high" if amt >= 500000 else "medium", "review",
                           f"Overpayment recovery of ${amt/100:,.2f} withheld by {r['payer_name']}", None, None, amt, None, None,
                           {"reference": r["reference"], "reason_code": "WO"}, key_parts=(r["transaction_key"], r["reference"], amt), claim=base)
        for key, f in fees.items():
            self._emit("FEE_SKIM", "fees", "medium", "opt_out", f"${f['cents']/100:,.2f} in payment fees withheld by {f['payer_name']} in {f['month']}", None, None, f["cents"], None, None,
                       {"count": f["n"], "references": dict(f["refs"].most_common(5))}, key_parts=key, claim=f)

        g = defaultdict(lambda: {"paid": 0, "zero": 0, "charge": 0, "allowed": 0, "carcs": Counter()})
        for l in self.lines:
            if l.get("procedure_code") == "G2211" and l.get("claim_status_code") in ("1", "19"):
                d = g[(l["payer_group"], l["year"])]
                if (l.get("paid_cents") or 0) > 0:
                    d["paid"] += 1
                    d["allowed"] += l.get("allowed_cents") or 0
                else:
                    d["zero"] += 1
                    _a, top, _pr, _c = self._line_carcs(l)
                    if top:
                        d["carcs"][top] += 1
                d["charge"] += l.get("charge_cents") or 0
                d["kind"] = l["payer_kind"]
        for (grp, year), d in g.items():
            tot = d["paid"] + d["zero"]
            if tot >= 25 and d["zero"] / tot >= 0.5:
                base = {"payer_group": grp, "payer_kind": d.get("kind"), "year": year, "payer_name": grp}
                self._emit("G2211_DENIED", "info", "low", "review", f"{grp} {year}: G2211 denied on {d['zero']} of {tot} visits", None, None, None,
                           top_of(d["carcs"])[0] if d["carcs"] else None, None,
                           {"paid": d["paid"], "zero": d["zero"], "carcs": dict(d["carcs"].most_common(4)), "charge_cents": d["charge"]}, key_parts=(grp, year), claim=base)

        for row in c.execute("""SELECT substr(COALESCE(l.service_date,l.payment_date),1,4) yr, a.reason_code, SUM(a.amount_cents) amt, COUNT(*) n
                                FROM adjustments a JOIN lines l ON l.line_key=a.line_key JOIN transactions t ON t.transaction_key=l.transaction_key
                                WHERE t.superseded=0 AND a.reason_code IN ('144','253') GROUP BY 1,2"""):
            if not row["yr"] or row["yr"] < "2023":
                continue
            amt = row["amt"] or 0
            base = {"year": row["yr"], "payer_group": "Medicare", "payer_kind": "medicare", "payer_name": "Medicare and Medicare Advantage"}
            if row["reason_code"] == "144":
                self._emit("MIPS_ADJUSTMENT", "info", "info", "review", f"{row['yr']}: net MIPS adjustment {'+' if amt < 0 else '-'}${abs(amt)/100:,.0f} across {row['n']} lines",
                           None, None, -amt, "144", None, {"year": row["yr"], "lines": row["n"]}, key_parts=("MIPS", row["yr"]), claim=base)
            else:
                self._emit("SEQUESTRATION", "info", "info", "none", f"{row['yr']}: sequestration withheld ${amt/100:,.0f}", None, None, amt, "253", None,
                           {"year": row["yr"], "lines": row["n"]}, key_parts=("SEQ", row["yr"]), claim=base)

        # a payer whose routine allowance for third and later procedures is below the standard 50 percent
        for (grp, bucket), p in self.mppr.items():
            if bucket == 3 and p["pct"] < 0.5:
                gap, n = 0, 0
                for l in self.lines:
                    if l["payer_group"] != grp or l.get("surg_rank", 0) < 3 or not is_surgical(l.get("procedure_code")) or l.get("procedure_code") in ADD_ON_CODES \
                            or l.get("claim_status_code") not in ("1", "19") or (l.get("paid_cents") or 0) <= 0:
                        continue
                    rate, _b, r = self.usual_rate(l, l["procedure_code"])
                    if rate and r and (l.get("allowed_cents") or 0) < 0.5 * rate:
                        gap += int(0.5 * rate) - (l.get("allowed_cents") or 0)
                        n += 1
                base = {"payer_group": grp, "payer_kind": self.kinds.get(grp), "payer_name": grp, "year": None}
                self._emit("MPPR_POLICY", "contract", "medium", "renegotiate",
                           f"{grp} allows third and later same-day procedures at about {p['pct']:.0%} of its usual rate ({p['n']} lines); the Medicare standard is 50%",
                           None, None, gap, "59", None, {"typical_pct": p["median"], "lines": p["n"], "lines_below_half": n,
                                                          "note": "Check the contract's multiple-procedure terms; this is the payer's routine practice, not a one-off error."},
                           key_parts=("MPPR_POLICY", grp), claim=base)

        # contract-level rate findings: commercial far below Medicare, Medicare Advantage below Medicare
        for (grp, year, code, tier, pos), r in self.rates.items():
            kind = self.kinds.get(grp)
            if pos != "office" or tier == "unknown" or r["n"] < 50 or r["share"] < 0.3:
                continue
            med = self.medicare.get((year, code, tier, pos))
            if not med:
                continue
            pct = r["mode"] / med
            base = {"payer_group": grp, "payer_kind": kind, "year": year, "payer_name": grp, "procedure_code": code, "provider_tier": tier}
            gap = (med - r["mode"]) * r["n"]
            if kind == "commercial" and pct < 0.85:
                self._emit("CONTRACT_RATE_GAP", "contract", "high" if gap >= 2000000 else "medium", "renegotiate",
                           f"{grp} pays {code} at {pct:.0%} of Medicare ({year}, {tier}, {r['n']} lines)", None, med, gap, None, None,
                           {"payer_rate_cents": r["mode"], "medicare_cents": med, "lines": r["n"], "pct_of_medicare": round(pct, 3), "mode_share": round(r["share"], 3)},
                           key_parts=(grp, year, code, tier), claim=base)
            elif kind == "medicare_advantage" and pct < 0.97:
                self._emit("MA_BELOW_MEDICARE", "contract", "medium" if gap < 2000000 else "high", "review",
                           f"{grp} pays {code} at {pct:.0%} of Medicare ({year}, {tier}, {r['n']} lines)", None, med, gap, None, None,
                           {"payer_rate_cents": r["mode"], "medicare_cents": med, "lines": r["n"], "pct_of_medicare": round(pct, 3), "mode_share": round(r["share"], 3),
                            "note": "Contracted MA rates follow the contract; non-contracted providers must receive at least the Medicare amount."},
                           key_parts=(grp, year, code, tier), claim=base)
        self.emit_charge_caps()

    def emit_charge_caps(self) -> None:
        """The practice's own charge below what a payer allows. Payers pay the lesser of the charge and their rate, so
        on such a line the payer paid exactly the charge and the rest is lost, on every line, until the fee is raised.
        Measured within one payer group, year, code, provider type and place of service: the payer's rate is the most
        common amount it allowed on clean lines it did not cap (allowed below the charge, at least five of them), and a
        line counts when it was paid exactly its charge and that charge is below the rate (but at least 40 percent of it and
        $5: a token charge is something else). One finding per group with at least five such lines and $100 between them.
        Nothing here can be appealed: the payer paid what was billed."""
        capped: dict[tuple, list] = defaultdict(list)
        uncapped: dict[tuple, Counter] = defaultdict(Counter)
        for l in self.lines:
            if l.get("claim_status_code") not in ("1", "19") or (l.get("paid_cents") or 0) <= 0:
                continue
            allowed, charge = l.get("allowed_cents") or 0, l.get("charge_cents") or 0
            if allowed <= 0 or charge <= 0 or l.get("original_procedure_code") or l.get("paid_units") not in (None, 1, 1.0) \
                    or l.get("pricing_mod") or l.get("surg_rank", 0) > 1 or not l.get("procedure_code"):
                continue
            key = (l["payer_group"], l["year"], l["procedure_code"], l.get("provider_tier") or "unknown", l.get("pos_class") or "office")
            if allowed == charge:
                capped[key].append(l)
            elif allowed < charge:
                uncapped[key][allowed] += 1
        for key in sorted(capped):
            cnt = uncapped.get(key)
            if not cnt or sum(cnt.values()) < 5:
                continue
            rate, _n = usual_amount(cnt)
            # a charge far below the rate is a billing oddity (a balance or a token amount), not a fee set too low
            short = [l for l in capped[key] if 0.4 * rate <= l["charge_cents"] < rate and l["charge_cents"] >= 500]
            gap = sum(rate - l["charge_cents"] for l in short)
            if len(short) < 5 or gap < 10000:
                continue
            grp, year, code, tier, pos = key
            charges = Counter(l["charge_cents"] for l in short)
            charge = charges.most_common(1)[0][0]
            base = {"payer_group": grp, "payer_kind": self.kinds.get(grp), "payer_name": grp, "year": year, "procedure_code": code,
                    "provider_tier": tier, "pos_class": pos}
            self._emit("CHARGE_BELOW_RATE", "contract", "high" if gap >= 200000 else "medium", "raise_fee",
                       f"{grp} allows ${rate/100:,.2f} for {code} but paid your charge of ${charge/100:,.2f} on {len(short)} lines ({year})",
                       None, rate, gap, None, None,
                       {"payer_rate_cents": rate, "charge_cents": charge, "charges": {str(k): v for k, v in charges.most_common(5)}, "lines": len(short),
                        "uncapped_lines": sum(cnt.values()),
                        "note": "The payer pays the lesser of your charge and its rate, so these lines were paid what was billed and cannot be appealed. "
                                "Raise your fee for this code above the highest amount any payer allows; claims billed after that are paid in full."},
                       key_parts=("CHARGE_BELOW_RATE", grp, year, code, tier, pos), claim=base)

    # ------------------------------------------------------------------ persistence
    def save(self) -> dict:
        c = self.conn
        c.row_factory = sqlite3.Row
        existing = {r["finding_id"]: dict(r) for r in c.execute("SELECT * FROM findings")}
        cols = ["finding_id", "signal", "category", "severity", "actionable", "title", "line_key", "claim_key", "transaction_key",
                "patient_control_number", "payer_name", "payer_group", "payer_kind", "payer_id", "rendering_provider_npi",
                "rendering_provider_name", "provider_tier", "service_date", "payment_date", "year", "procedure_code", "modifiers",
                "original_procedure_code", "code_category", "charge_cents", "allowed_cents", "paid_cents", "expected_cents",
                "shortfall_cents", "carc", "carc_text", "rarc", "rarc_text", "evidence", "appeal_deadline", "status", "notes",
                "run_id", "first_seen", "last_seen"] + list(EXTRA_FINDING_COLS)

        # Findings a person moved away from "resolved" at or after the analysis last closed them (the same second counts: a
        # person can only act on a closed finding after it closed), or that inherited such a decision when their signal
        # changed: the analysis leaves their status alone from then on. A slip a person undid before any automatic closure
        # does not count.
        # A reopening by the analysis (the payment was taken back) ends both: what the person did before it was about a
        # closure that no longer holds.
        overridden = {r[0] for r in c.execute("""SELECT finding_id FROM finding_events GROUP BY finding_id
                                                 HAVING MAX(CASE WHEN kind='override_carried' THEN at END) >= COALESCE(MAX(CASE WHEN kind='reopened' THEN at END), '')
                                                     OR (MAX(CASE WHEN kind='paid_on_later_remit' THEN at END) IS NOT NULL
                                                         AND MAX(CASE WHEN kind='manual' AND from_status='resolved' THEN at END)
                                                             >= MAX(CASE WHEN kind='paid_on_later_remit' THEN at END)
                                                         AND MAX(CASE WHEN kind='manual' AND from_status='resolved' THEN at END)
                                                             >= COALESCE(MAX(CASE WHEN kind='reopened' THEN at END), ''))""")}
        # recoveries a person recorded after the analysis had closed the finding (and before anything reopened it): they rest
        # on that payment
        confirmed = {r[0] for r in c.execute("""SELECT finding_id FROM finding_events GROUP BY finding_id
                                               HAVING MAX(CASE WHEN kind='confirmed_carried' THEN at END) >= COALESCE(MAX(CASE WHEN kind='reopened' THEN at END), '')
                                                   OR (MAX(CASE WHEN kind='paid_on_later_remit' THEN at END) IS NOT NULL
                                                       AND MAX(CASE WHEN kind='manual' AND to_status='resolved' THEN at END)
                                                           >= MAX(CASE WHEN kind='paid_on_later_remit' THEN at END)
                                                       AND MAX(CASE WHEN kind='paid_on_later_remit' THEN at END)
                                                           >= COALESCE(MAX(CASE WHEN kind='reopened' THEN at END), ''))""")}
        # Rows from a release whose status action left the automatic note in place (before this change): where a person
        # acted after the analysis closed it, the status is theirs. A recovery they recorded rests on that payment.
        for fid, kind, from_status, to_status, _mx in c.execute("SELECT finding_id, kind, from_status, to_status, MAX(id) FROM finding_events GROUP BY finding_id"):
            r = existing.get(fid)
            if r and r.get("auto_note") and kind == "manual":
                r["auto_note"], r["prev_status"] = None, None
                if to_status == "resolved":
                    confirmed.add(fid)
                elif from_status == "resolved":
                    overridden.add(fid)         # moved off a closure the live release made without logging it

        def still_paid(f: dict) -> bool:
            """A payment for this finding's service still stands on its bill, made after the finding's remittance."""
            bill, after = bill_of(f.get("patient_control_number")), f.get("payment_date") or ""
            codes = [f.get("procedure_code")] if f.get("procedure_code") else (json.loads(f["evidence"]).get("codes") or [] if f.get("evidence") else [])
            return any(e[0] > after and e[3] in ("1", "19") for code in codes for e in
                       (self.paid_events.get((bill, code, f.get("service_date")), []) if f.get("service_date") else self.paid_by_code.get((bill, code), [])))
        label = {"open": "open", "in_progress": "in progress", "appealed": "appealed", "lost": "lost", "dismissed": "dismissed"}

        def worked(r: dict) -> bool:        # a person recorded something on this finding
            return bool(r.get("notes") or r.get("appealed_at")) or r["status"] in MANUAL_STATUSES or r["status"] == "dismissed" \
                or (r["status"] == "resolved" and not r.get("auto_note")) or r["finding_id"] in overridden

        # when new information changes a line's signal (and so its id), the work recorded on the old finding follows the line
        orphans: dict[tuple, dict] = {}
        for fid, prev in existing.items():
            if fid not in self.findings and prev.get("line_key") and worked(prev) and not prev.get("stale"):
                orphans.setdefault((prev["line_key"], prev["category"]), prev)
        inherited: set[str] = set()
        rows, events = [], []
        for fid, f in self.findings.items():
            prev = existing.get(fid)
            if prev is None and f.get("line_key"):
                heir = orphans.get((f["line_key"], f["category"]))
                if heir and heir["finding_id"] not in inherited:
                    prev = heir
                    inherited.add(heir["finding_id"])
                    events.append((fid, self.now, "signal_changed", heir["status"], heir["status"], f.get("shortfall_cents"), "system",
                                   f"was {heir['signal']}, now {f['signal']}", self.run_id))
                    for mark, kind in ((overridden, "override_carried"), (confirmed, "confirmed_carried")):
                        if heir["finding_id"] in mark:      # the person's decision follows the line to its new id
                            events.append((fid, self.now, kind, heir["status"], heir["status"], None, "system", f"from {heir['finding_id']}", self.run_id))
                            mark.add(fid)
            st, notes, first = (prev["status"], prev["notes"], prev["first_seen"]) if prev else ("open", None, self.now)
            auto_note, prev_status = (prev.get("auto_note"), prev.get("prev_status")) if prev else (None, None)
            before = st
            if prev:    # what people recorded about this finding survives every re-run
                f["appealed_at"], f["status_changed_at"] = prev.get("appealed_at"), prev.get("status_changed_at")
            if f.get("auto_status"):
                if st in ("open", "in_progress", "appealed") and not (prev and prev["finding_id"] in overridden):
                    prev_status, st, auto_note = st, "resolved", f.get("auto_notes")     # remembered so it can be undone exactly
                elif st == "resolved" and auto_note:
                    auto_note = f.get("auto_notes")         # closed by an earlier analysis and still paid
                else:
                    # A person's decision stands: dismissed, lost, recovered with their own amount, or set again after an
                    # automatic closure. The payment is shown, not applied.
                    if st == "resolved":
                        f["recovered_cents"], f["recovered_date"] = prev.get("recovered_cents"), prev.get("recovered_date")
                    else:
                        f["hint"] = f"A later payment was found; kept as {label.get(st, st)} because a person set it"
                    auto_note, prev_status = None, None
            elif st == "resolved" and auto_note:
                # the later payment no longer holds (for example its remittance was superseded): back to where it was
                st, auto_note, prev_status = (prev_status or "open"), None, None
            elif st == "resolved" and prev and prev["finding_id"] in confirmed and not still_paid(f):
                # a person recorded this recovery on a payment the analysis had found, and that payment no longer stands
                st, auto_note, prev_status = "open", None, None
                f["hint"] = "The payment this was recovered on was taken back; check it"
            elif st == "resolved" and prev:
                f["recovered_cents"], f["recovered_date"] = prev.get("recovered_cents"), prev.get("recovered_date")
            else:
                auto_note, prev_status = None, None        # an automatic note left from before a person changed the status
            if st != "resolved":
                f["recovered_cents"], f["recovered_date"] = None, None
            if (prev and before != st) or (not prev and st == "resolved"):
                f["status_changed_at"] = self.now
                events.append((fid, self.now, "paid_on_later_remit" if st == "resolved" else "reopened", before, st,
                               f.get("recovered_cents") if st == "resolved" else f.get("shortfall_cents"), "system", auto_note, self.run_id))
            f["status"], f["notes"], f["first_seen"], f["last_seen"], f["stale"] = st, notes, first, self.now, 0
            f["auto_note"], f["prev_status"] = auto_note, prev_status
            rows.append(tuple(f.get(k) for k in cols))
        # findings a person worked, dismissed or lost that the current rules no longer produce are kept, marked stale, so that
        # no work is lost and a dismissed finding cannot come back as new. Stale rows are left out of every total.
        for fid, prev in existing.items():
            if fid not in self.findings and fid not in inherited and prev["status"] != "open" and worked(prev):
                prev["stale"] = 1
                rows.append(tuple(prev.get(k) for k in cols))
        c.execute("DELETE FROM findings")
        c.executemany(f"INSERT INTO findings ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", rows)
        if events:
            c.executemany("INSERT INTO finding_events (finding_id, at, kind, from_status, to_status, amount_cents, actor, note, run_id) VALUES (?,?,?,?,?,?,?,?,?)", events)
        c.execute("DELETE FROM progress_snapshots WHERE run_id=?", (self.run_id,))
        c.execute("""INSERT INTO progress_snapshots (run_id, at, category, status, n, cents)
                     SELECT ?, ?, category, status, COUNT(*), COALESCE(SUM(shortfall_cents),0) FROM findings GROUP BY category, status""", (self.run_id, self.now))
        self._save_exposure()
        summary = Counter(f["signal"] for f in self.findings.values())
        c.execute("INSERT OR REPLACE INTO detection_runs (run_id, started_at, finished_at, version, lines, findings, summary) VALUES (?,?,?,?,?,?,?)",
                  (self.run_id, self.now, datetime.now(timezone.utc).isoformat(timespec="seconds"), DETECT_VERSION, len(self.lines), len(rows), json.dumps(summary)))
        c.commit()
        return dict(summary)

    def _save_exposure(self) -> None:
        """Denominators for rates: primary-claim lines by payment month, payer, plan, provider, billed code and place of service."""
        agg: dict[tuple, list[int]] = defaultdict(lambda: [0, 0, 0, 0, 0])
        for l in self.lines:
            if l.get("claim_status_code") not in ("1", "19") or not l.get("procedure_code") or not l.get("month"):
                continue
            if (l.get("charge_cents") or 0) <= 0:
                continue
            billed = l.get("original_procedure_code") or l["procedure_code"]
            key = (l["month"], l["payer_group"], l["payer_kind"], l.get("rendering_provider_npi") or "", l.get("provider_tier") or "unknown",
                   billed, l["pos_class"], l.get("plan") or "")
            a = agg[key]
            a[0] += 1
            a[1] += 1 if (l.get("paid_cents") or 0) == 0 and (l.get("allowed_cents") or 0) == 0 else 0
            a[2] += l.get("charge_cents") or 0
            a[3] += l.get("allowed_cents") or 0
            a[4] += l.get("paid_cents") or 0
        self.conn.execute("DELETE FROM exposure")
        self.conn.executemany("INSERT OR REPLACE INTO exposure VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", [k + tuple(v) for k, v in agg.items()])
        # the last 500 days at day resolution for the alert engine: line counts, and the allowed amounts on cleanly paid lines
        days = [l.get("payment_date") or l.get("txn_payment_date") or "" for l in self.lines]
        last = max(days) if days else ""
        cutoff = _add_days(last, -500) or ""
        daily: Counter = Counter()
        points: Counter = Counter()
        for l in self.lines:
            day = l.get("payment_date") or l.get("txn_payment_date") or ""
            if day < cutoff or l.get("claim_status_code") not in ("1", "19") or not l.get("procedure_code") or (l.get("charge_cents") or 0) <= 0:
                continue
            billed = l.get("original_procedure_code") or l["procedure_code"]
            scope = "other" if billed not in EM_CODES else ("em4" if EM_LEVEL[billed] >= 4 else "em")
            daily[(day, l["payer_group"], l["payer_kind"], l.get("rendering_provider_npi") or "", scope)] += 1
            if l.get("clean"):
                points[(day, l["payer_group"], l["payer_kind"], l["procedure_code"], l.get("provider_tier") or "unknown", l["pos_class"], l["allowed_cents"])] += 1
        self.conn.execute("DELETE FROM exposure_daily")
        self.conn.executemany("INSERT OR REPLACE INTO exposure_daily VALUES (?,?,?,?,?,?)", [k + (v,) for k, v in daily.items()])
        self.conn.execute("DELETE FROM rate_points")
        self.conn.executemany("INSERT OR REPLACE INTO rate_points VALUES (?,?,?,?,?,?,?,?)", [k + (v,) for k, v in points.items()])

    def run(self) -> dict:
        try:
            if self.conn.execute("SELECT 1 FROM ema_rows LIMIT 1").fetchone():
                from .link import build_links
                build_links(self.store, log=lambda *_a: None)
        except Exception:
            pass
        self.load()
        self.build_baselines()
        self.run_line_rules()
        self.run_claim_rules()
        self.run_aggregate_rules()
        self.emit_second_rates()
        self.resolve_elsewhere()
        return self.save()


def run_detection(db_path: str, run_id: str | None = None) -> dict:
    import gc

    from .activity import refresh
    store = Store(db_path)
    try:
        det = Detector(store, run_id)
        summary = det.run()
        del det                 # the provider tables are built after the detector has given its memory back
        gc.collect()
        refresh(store, log=lambda *_a: None)
        return summary
    finally:
        store.close()
