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
underpayment UNDERPAID_VS_OWN_RATE     allowed below the payer's own usual rate (rank-aware) for this code and tier
             MPPR_EXCESS               multiple-procedure reduction on an add-on code or deeper than policy
fees         FEE_SKIM                  PLB fees withheld (Zelis transaction fees, origination fees)
             TAKEBACK                  PLB WO overpayment recovery above a threshold
contract     CONTRACT_RATE_GAP         commercial payer's usual rate far below Medicare for a high-volume code
             MA_BELOW_MEDICARE         Medicare Advantage payer's usual rate below the Medicare fee schedule
             MPPR_POLICY               payer routinely pays later procedures below the standard 50 percent
info         G2211_DENIED              per payer and year: G2211 denied most of the time
             MIPS_ADJUSTMENT           per year: net MIPS adjustment (CARC 144)
             SEQUESTRATION             per year: sequestration (CARC 253)
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

from .codes import (ADD_ON_CODES, EM_CODES, EM_LEVEL, GLOBAL_DAYS, MEDICARE_SAME_DAY_EM, appeal_rule, carc_text, code_category,
                    normalize_payer, payer_group_kind, pos_class, rarc_text)
from .fees import FeeTable, family_of, is_downcode
from .store import Store

DETECT_VERSION = "2.1"
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
    "stale": "INTEGER DEFAULT 0", "auto_note": "TEXT", "prev_status": "TEXT",
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
    pick = (personal or clean).most_common(1)
    return pick[0][0] if pick else ""


def modifier_class(mods: list[str]) -> str:
    for m in ("25", "57", "24", "59", "XS", "XE", "XU", "XP", "76", "79"):
        if m in mods:
            return m
    return "other" if mods else "none"


def is_surgical(code: str | None) -> bool:
    return bool(code) and code in GLOBAL_DAYS and code not in ADD_ON_CODES


def bill_of(pcn: str | None) -> str:
    """EMA claim ids are a 9-character bill id plus a submission suffix (C017, C024, ...)."""
    return (pcn or "")[:9]


class Detector:
    def __init__(self, store: Store, run_id: str | None = None):
        self.store = store
        self.conn = store.conn
        ensure_schema(self.conn)
        self.run_id = run_id or datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%SZ")
        self.now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.findings: dict[str, dict] = {}

    # ------------------------------------------------------------------ loading
    def load(self) -> None:
        c = self.conn
        c.row_factory = sqlite3.Row
        rows = c.execute("""
            SELECT l.*, cl.claim_filing_indicator, cl.contract_class, cl.facility_type_code, cl.rendering_provider_name AS rp_name,
                   cl.charge_cents AS claim_charge_cents, cl.paid_cents AS claim_paid_cents, cl.received_date,
                   t.payment_date AS txn_payment_date, t.trace_number
            FROM lines l JOIN claims cl ON cl.claim_key = l.claim_key
            JOIN transactions t ON t.transaction_key = l.transaction_key
            WHERE t.superseded = 0
        """).fetchall()
        self.lines = [dict(r) for r in rows]
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
            adj[r["line_key"]].append((r["group_code"], r["reason_code"], r["amount_cents"] or 0))
        self.adj = adj
        rem = defaultdict(list)
        for r in c.execute("SELECT line_key, code FROM remarks WHERE line_key IS NOT NULL"):
            rem[r["line_key"]].append(r["code"])
        self.rem = rem
        self.claim_adj = defaultdict(list)
        for r in c.execute("SELECT claim_key, group_code, reason_code, amount_cents FROM adjustments WHERE level='claim'"):
            self.claim_adj[r["claim_key"]].append((r["group_code"], r["reason_code"], r["amount_cents"] or 0))
        self.claim_rem = defaultdict(list)
        for r in c.execute("SELECT claim_key, code FROM remarks WHERE level='claim'"):
            self.claim_rem[r["claim_key"]].append(r["code"])
        self.paid_line_keys: set[tuple] = set()
        self.paid_pcns: set[str] = set()
        self.paid_events: dict[tuple, list[tuple]] = defaultdict(list)   # (bill, code, dos) -> [(payment_date, trace, paid, status)]
        self.bill_paid_dates: dict[str, list[str]] = defaultdict(list)
        self.lines_by_claim: dict[str, list[dict]] = defaultdict(list)
        for l in self.lines:
            self.lines_by_claim[l["claim_key"]].append(l)
            if (l.get("paid_cents") or 0) > 0 and l.get("claim_status_code") in ("1", "19", "2", "3"):
                bill = bill_of(l["patient_control_number"])
                dos = l.get("service_date")
                self.paid_line_keys.add((bill, l["procedure_code"], dos))
                self.paid_pcns.add(l["patient_control_number"])
                pd_ = l.get("payment_date") or l.get("txn_payment_date") or ""
                self.paid_events[(bill, l["procedure_code"], dos)].append((pd_, l.get("trace_number"), l["paid_cents"], l.get("claim_status_code")))
                self.bill_paid_dates[bill].append(pd_)
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
            self.claim_location = {k: v.most_common(1)[0][0] for k, v in loc_votes.items()}
            npi_votes: dict[str, Counter] = defaultdict(Counter)
            seen_claims: set[str] = set()
            for l in self.lines:
                loc = self.claim_location.get(l.get("patient_control_number") or "")
                if loc and l.get("rendering_provider_npi") and l["claim_key"] not in seen_claims:
                    seen_claims.add(l["claim_key"])
                    npi_votes[l["rendering_provider_npi"]][loc] += 1
            for npi, v in npi_votes.items():
                top, n = v.most_common(1)[0]
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
            mode = cnt.most_common(1)[0][0]
            top_by_year_code[(year, code)] = max(top_by_year_code.get((year, code), 0), mode)
        tier_votes: dict[str, list[float]] = defaultdict(list)
        for (npi, year, code), cnt in by_npi.items():
            mode = cnt.most_common(1)[0][0]
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
            mode, mode_n = cnt.most_common(1)[0]
            vals = sorted(cnt.elements())
            self.rates[key] = {"mode": mode, "median": vals[len(vals) // 2], "p10": vals[int(0.1 * (len(vals) - 1))],
                               "p90": vals[int(0.9 * (len(vals) - 1))], "n": n, "share": mode_n / n}
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

    def later_payment(self, l: dict, code: str | None = None, expected: int | None = None, min_paid: int | None = None) -> tuple | None:
        """Earliest later remit that paid this bill, code and date of service (a reworked or appealed line).
        A primary payment counts; a secondary payment counts only when it covers at least half the expected amount.
        `min_paid` is for lines that were paid short under the same code: the later payment must be clearly larger than
        the short one, so a repeat of the same reduced payment does not close the finding."""
        bill = bill_of(l.get("patient_control_number"))
        dos = l.get("service_date") or l.get("statement_from")
        mine = l.get("payment_date") or l.get("txn_payment_date") or ""
        events = [e for e in self.paid_events.get((bill, code or l.get("procedure_code"), dos), []) if e[0] and e[0] > mine]
        events = [e for e in events if e[3] in ("1", "19") or (expected and (e[2] or 0) >= 0.5 * expected)]
        if min_paid is not None:
            events = [e for e in events if (e[2] or 0) >= min_paid]
        return min(events) if events else None

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
        if actionable in ("appeal", "correct_and_resubmit"):
            deadline = _add_days(pay_date, rule["days"])
            deadline_basis = rule["label"] + ("" if rule["verified"] else " (not confirmed against the payer's current manual)")
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
                    if (bill_of(l["patient_control_number"]), code, l.get("service_date")) not in self.paid_line_keys:
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
                    self._emit("UNDERPAID_VS_OWN_RATE", "underpayment", "medium", "appeal",
                               f"{code} allowed ${allowed/100:.2f} vs usual ${rate/100:.2f} ({l['payer_group']})", l, rate, rate - allowed, top, rarc,
                               {"basis": basis, "usual_share": round(r["share"], 3), "usual_n": r["n"], "rank": rank})

    # ------------------------------------------------------------------ claim rules
    def run_claim_rules(self) -> None:
        c = self.conn
        c.row_factory = sqlite3.Row
        rows = c.execute("""SELECT cl.*, t.payment_date AS txn_payment_date FROM claims cl JOIN transactions t ON t.transaction_key=cl.transaction_key
                            WHERE t.superseded=0 AND cl.claim_status_code='4'""").fetchall()
        for r in rows:
            cl = dict(r)
            if cl["patient_control_number"] in self.paid_pcns:
                continue
            if any(d > (cl.get("payment_date") or "") for d in self.bill_paid_dates.get(bill_of(cl["patient_control_number"]), [])):
                continue
            grp, kind = payer_group_kind(cl.get("payer_name"), cl.get("claim_filing_indicator"))
            cl["payer_group"], cl["payer_kind"] = grp, kind
            cl["year"] = (cl.get("statement_from") or cl.get("payment_date") or "")[:4]
            cl["rp_name"] = cl.get("rendering_provider_name")
            lines = self.lines_by_claim.get(cl["claim_key"], [])
            adj = self.claim_adj.get(cl["claim_key"], []) + [(g, rc, a) for ln in lines for g, rc, a in self.adj.get(ln["line_key"], [])]
            carcs = Counter(rc for _g, rc, _a in adj if rc not in IGNORE_CARCS)
            top = carcs.most_common(1)[0][0] if carcs else None
            rarcs = self.claim_rem.get(cl["claim_key"], []) + [x for ln in lines for x in self.rem.get(ln["line_key"], [])]
            rarc = ",".join(sorted(set(rarcs))) if rarcs else None
            codes = [ln["procedure_code"] for ln in lines]
            exp = 0
            for ln in lines:
                ln2 = dict(ln, payer_group=grp, payer_kind=kind, year=cl["year"], provider_tier=self.provider_tier.get(ln.get("rendering_provider_npi") or "", "unknown"))
                e, _ = self.expected_allowed(ln2, ln["procedure_code"])
                exp += e or 0
            sev = "high" if exp >= 20000 else "medium"
            if top in TIMELY_CARCS:
                act = "review"
            elif top in INFO_CARCS or top in CODING_CARCS:
                act = "correct_and_resubmit"
            elif top in NONCOV_CARCS or top in DUP_CARCS:
                act = "review"
            else:
                act = "appeal"
            self._emit("CLAIM_DENIED_UNPAID", "denial", sev, act, f"Claim denied ({top or 'no CARC'}) and never paid: {', '.join(codes[:6])}", None, exp or None, exp or None, top, rarc,
                       {"codes": codes, "claim_charge_cents": cl.get("charge_cents"), "payer_claim_control_number": cl.get("payer_claim_control_number")},
                       key_parts=(cl["claim_key"],), claim=cl)

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
                           d["carcs"].most_common(1)[0][0] if d["carcs"] else None, None,
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

        def worked(r: dict) -> bool:        # a person recorded something on this finding
            return bool(r.get("notes") or r.get("appealed_at")) or r["status"] in MANUAL_STATUSES or r["status"] == "dismissed" \
                or (r["status"] == "resolved" and not r.get("auto_note"))

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
            st, notes, first = (prev["status"], prev["notes"], prev["first_seen"]) if prev else ("open", None, self.now)
            auto_note, prev_status = (prev.get("auto_note"), prev.get("prev_status")) if prev else (None, None)
            before = st
            if prev:    # what people recorded about this finding survives every re-run
                f["appealed_at"], f["status_changed_at"] = prev.get("appealed_at"), prev.get("status_changed_at")
            if f.get("auto_status"):
                if st == "dismissed":
                    auto_note = None
                elif st == "resolved" and not auto_note:
                    # already marked recovered by a person: keep the amount and date they recorded
                    f["recovered_cents"], f["recovered_date"] = prev.get("recovered_cents"), prev.get("recovered_date")
                else:
                    if st != "resolved":
                        prev_status = st            # remembered so the resolution can be undone exactly
                    st, auto_note = "resolved", f.get("auto_notes")
            elif st == "resolved" and auto_note:
                # the later payment no longer holds (for example its remittance was superseded): back to where it was
                st, auto_note, prev_status = (prev_status or "open"), None, None
            elif st == "resolved" and prev:
                f["recovered_cents"], f["recovered_date"] = prev.get("recovered_cents"), prev.get("recovered_date")
            if st != "resolved":
                f["recovered_cents"], f["recovered_date"] = None, None
            if prev and before != st:
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
        # the last 400 days at day resolution for the alert engine: line counts, and the allowed amounts on cleanly paid lines
        days = [l.get("payment_date") or l.get("txn_payment_date") or "" for l in self.lines]
        last = max(days) if days else ""
        cutoff = _add_days(last, -400) or ""
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
        return self.save()


def run_detection(db_path: str, run_id: str | None = None) -> dict:
    store = Store(db_path)
    try:
        return Detector(store, run_id).run()
    finally:
        store.close()
