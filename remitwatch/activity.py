"""
Provider activity: what each clinician did, read from the same remittances and exports the detector uses.

The remittances answer the long-run questions (almost every insured visit ends up on one): how many patients a
clinician sees on a clinic day, how many are new, which visit levels and procedures they bill, and what those services
are allowed. The EMA charge exports answer the recent ones, weeks before the remittances arrive, and add what never
reaches a payer (self-pay and cosmetic visits) and how long each clinician takes to close a visit into a bill.

How a remittance line becomes activity
--------------------------------------
  bill        The first nine characters of the patient control number (CLP01) are the EMA bill id. A bill is one visit.
  service     One billed line. The same line comes back on several remittances (primary, secondary, a reversal and a
              reprocessed claim). They are folded into one service by the line control number (REF*6R, the EMA bill
              item id); payers that do not echo it are matched on date of service, billed code and charge.
  allowed     From the latest primary remittance that allowed something. Paid is every insurer payment for the service
              added up, reversals included.
  clinician   The rendering provider on the claim when it is a person. Supplement and secondary payers often send the
              group instead; then the person named on the claim, the provider reference on the line, another remittance
              for the same bill, and last the EMA export are tried in that order.
  encounter   One bill on one date of service, typed by what is on it: an office visit, a procedure without a visit
              code, pathology only (a slide read is not a patient in the room), radiation only.
  patients    People seen face to face (visit or procedure) by one clinician on one day. The same person on two bills
              counts once.

Everything written here is a count or a sum per clinician. No patient is stored.
"""
from __future__ import annotations

import calendar
import json
import re
import sqlite3
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

from .codes import DRUG_RE, RADIATION_CODES, SKIN_SUBSTITUTE_RE, payer_group_kind

ACTIVITY_VERSION = "1"      # bump when the tables or the way they are filled change; they are then rebuilt
CLINIC_DAY_MIN = 5          # a day counts as a clinic day when the clinician saw at least this many patients
COMPLETE_SHARE = 0.97       # a month of service is treated as complete when this share of its visits has been paid
LAG_DAYS = 150              # how far the service-to-payment curve is kept

DDL = """
CREATE TABLE IF NOT EXISTS provider_roster (
    provider_id TEXT PRIMARY KEY, name TEXT, kind TEXT, tier TEXT, role TEXT, ema_name TEXT,
    first_date TEXT, last_date TEXT, patients INTEGER
);
CREATE TABLE IF NOT EXISTS provider_days (
    provider_id TEXT, service_date TEXT,
    patients INTEGER, visits INTEGER, new_visits INTEGER, est_visits INTEGER, proc_only INTEGER,
    path_only INTEGER, radiation INTEGER, biopsy_patients INTEGER, biopsy_lesions REAL, items INTEGER,
    charge_cents INTEGER, allowed_cents INTEGER, paid_cents INTEGER, clinic_allowed_cents INTEGER, path_generated_cents INTEGER,
    PRIMARY KEY (provider_id, service_date)
);
CREATE TABLE IF NOT EXISTS provider_services (
    provider_id TEXT, month TEXT, code TEXT, family TEXT,
    items INTEGER, units REAL, denied INTEGER, charge_cents INTEGER, allowed_cents INTEGER, paid_cents INTEGER,
    PRIMARY KEY (provider_id, month, code)
);
CREATE TABLE IF NOT EXISTS provider_payers (
    provider_id TEXT, month TEXT, payer_kind TEXT, encounters INTEGER, charge_cents INTEGER, allowed_cents INTEGER, paid_cents INTEGER,
    seen INTEGER, biopsied INTEGER,
    PRIMARY KEY (provider_id, month, payer_kind)
);
CREATE TABLE IF NOT EXISTS provider_panels (
    provider_id TEXT, month TEXT, panel INTEGER, prior_panel INTEGER, kept INTEGER, kept_same INTEGER,
    PRIMARY KEY (provider_id, month)
);
CREATE TABLE IF NOT EXISTS provider_cohorts (
    provider_id TEXT, quarter TEXT, new_patients INTEGER, back_90 INTEGER, back_365 INTEGER, back_same INTEGER, return_visits INTEGER,
    biopsied INTEGER, treated_120 INTEGER,
    PRIMARY KEY (provider_id, quarter)
);
CREATE TABLE IF NOT EXISTS provider_recall (
    provider_id TEXT PRIMARY KEY, cancer_patients INTEGER, overdue_12 INTEGER, overdue_18 INTEGER, as_of TEXT
);
CREATE TABLE IF NOT EXISTS provider_ema_days (
    provider TEXT, provider_id TEXT, role TEXT, service_date TEXT,
    patients INTEGER, insured_patients INTEGER, loose_lines INTEGER, visits INTEGER, new_visits INTEGER, est_visits INTEGER, est_high INTEGER,
    path_only INTEGER, radiation INTEGER, biopsy_lesions REAL, charges_cents INTEGER, clinic_charges_cents INTEGER, location TEXT, pulled TEXT,
    PRIMARY KEY (provider, service_date)
);
CREATE TABLE IF NOT EXISTS provider_ema_lag (
    provider TEXT, provider_id TEXT, lag_days INTEGER, bills INTEGER, mature INTEGER,
    PRIMARY KEY (provider, lag_days, mature)
);
"""
TABLES = ("provider_roster", "provider_days", "provider_services", "provider_payers", "provider_panels", "provider_cohorts",
          "provider_recall", "provider_ema_days", "provider_ema_lag")
INFO_KEY = "activity:info"

EM_NEW = ("99202", "99203", "99204", "99205")
EM_EST = ("99211", "99212", "99213", "99214", "99215")
BIOPSY = ("11102", "11103", "11104", "11105", "11106", "11107")
FAMILIES = [
    ("new_visit", "New patient visits"), ("est_visit", "Established patient visits"), ("biopsy", "Biopsies"),
    ("destruction_premalignant", "Destruction, premalignant"), ("destruction_benign", "Destruction, benign"),
    ("destruction_malignant", "Destruction, malignant"), ("excision", "Excisions and shaves"), ("repair", "Repairs, flaps and grafts"),
    ("mohs", "Mohs"), ("pathology", "Pathology and lab"), ("radiation", "Radiation"), ("skin_substitute", "Skin substitute products"),
    ("drug", "Drugs"), ("injection", "Injections"), ("phototherapy", "Phototherapy"), ("visit_addon", "Visit add-on (G2211)"), ("other", "Other"),
]
FAMILY_LABEL = dict(FAMILIES)
NOT_CLINIC = ("pathology", "radiation")     # read or delivered away from the exam room: not a patient seen
PRODUCT = ("skin_substitute", "drug")       # paid mostly to cover what the product cost
NOT_PER_PATIENT = NOT_CLINIC + PRODUCT      # kept out of "allowed per patient seen"
CANCER_TREATMENT = ("mohs", "destruction_malignant", "radiation")     # with malignant excisions (116xx): a cancer was treated
PRIMARY = ("1", "19")                       # processed as primary, with or without forwarding to another payer


def clean_code(code: str | None) -> str:
    """'11102 59' -> '11102'. A few payers put the modifier inside the code field."""
    parts = (code or "").upper().split()
    return parts[0] if parts else ""


def service_family(code: str | None) -> str:
    c = code or ""
    if c in EM_NEW:
        return "new_visit"
    if c in EM_EST:
        return "est_visit"
    if c in BIOPSY:
        return "biopsy"
    if c in ("17000", "17003", "17004"):
        return "destruction_premalignant"
    if c in ("17110", "17111", "17106", "17107", "17108", "17250"):
        return "destruction_benign"
    if c.startswith("172"):
        return "destruction_malignant"
    if c.startswith("1731"):
        return "mohs"
    if c[:3] in ("112", "113", "114", "116"):
        return "excision"
    if len(c) == 5 and c.isdigit() and c[:2] in ("12", "13", "14", "15"):
        return "repair"
    if c in RADIATION_CODES or c.startswith("77"):
        return "radiation"
    if len(c) == 5 and c.isdigit() and c[0] == "8":
        return "pathology"
    if SKIN_SUBSTITUTE_RE.match(c):
        return "skin_substitute"
    if DRUG_RE.match(c):
        return "drug"
    if c in ("11900", "11901", "96372"):
        return "injection"
    if c.startswith("969"):
        return "phototherapy"
    if c == "G2211":
        return "visit_addon"
    return "other"


# --------------------------------------------------------------------------- names
NAME_NOISE = frozenset(("MD", "DO", "PA", "PAC", "APRN", "NP", "FNP", "DNP", "ARNP", "RN", "III", "II", "IV", "JR", "SR", "DR", "NOT",
                        "AVAIL", "AVAILABLE", "PHD", "FAAD", "MR", "MS", "MRS", "UNKNOWN", "PROVIDER"))
ORG_WORDS = ("DERMATOLOG", "ASSOC", " PSC", "CENTER", "CLINIC", "SURGERY", "DERMA", "LASER", "HOSPITAL", "MEDICAL", "GROUP", "LLC", "PLLC")


def is_org(name: str) -> bool:
    u = f" {name.upper()} "
    return any(w in u for w in ORG_WORDS)


def name_tokens(*parts: str | None) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(words, initials) of a person's name however it was written: 'RIVERS III', 'JOAN P' -> (('RIVERS', 'JOAN'), ('P',))."""
    s = re.sub(r"[^A-Z ]+", " ", " ".join(p for p in parts if p).upper())
    words, initials = [], []
    for t in s.split():
        if len(t) > 6 and t.endswith("III"):        # 'RIVERSIII'
            t = t[:-3]
        if t in NAME_NOISE:
            continue
        (initials if len(t) == 1 else words).append(t)
    return tuple(words), tuple(initials)


def identify_people(conn: sqlite3.Connection) -> tuple[dict[str, dict], set[str]]:
    """Which rendering NPIs are a person, and which stand for the group. Payers that send the group as the rendering
    provider attach every clinician's name to it, so an NPI seen under many different names is not a person."""
    seen: dict[str, Counter] = defaultdict(Counter)
    for npi, last, first, n in conn.execute(
            """SELECT c.rendering_provider_npi, COALESCE(c.rendering_provider_last,''), COALESCE(c.rendering_provider_first,''), COUNT(*)
               FROM claims c JOIN transactions t ON t.transaction_key = c.transaction_key
               WHERE t.superseded = 0 AND c.rendering_provider_npi IS NOT NULL GROUP BY 1, 2, 3"""):
        seen[npi][(last, first)] += n
    payee = dict(conn.execute("SELECT payee_npi, COUNT(*) FROM transactions WHERE superseded = 0 AND payee_npi IS NOT NULL GROUP BY 1"))
    paid_to = sum(payee.values())
    people: dict[str, dict] = {}
    groups: set[str] = set()
    for npi in sorted(seen):
        org = personal = 0
        word_n: Counter = Counter()
        pair_n: Counter = Counter()
        bags: list[tuple[frozenset, int]] = []
        for (last, first), n in seen[npi].items():
            full = f"{last} {first}".strip()
            if not full:
                continue
            if is_org(full):
                org += n
                continue
            words, _ = name_tokens(last, first)
            if not words:
                continue
            personal += n
            bags.append((frozenset(words), n))
            for w in set(words):
                word_n[w] += n
            lw, fw = name_tokens(last)[0], name_tokens(first)[0]
            if len(lw) == 1 and len(fw) == 1:
                pair_n[(lw[0], fw[0])] += n
        if (paid_to and payee.get(npi, 0) > 0.5 * paid_to) or org > personal:
            groups.add(npi)
            continue
        if not personal:
            people[npi] = {"surname": "", "given": "", "name": f"NPI {npi}"}
            continue
        if pair_n:
            surname, given = min(pair_n.items(), key=lambda kv: (-kv[1], kv[0]))[0]
        else:
            ranked = sorted(word_n.items(), key=lambda kv: (-kv[1], kv[0]))
            surname, given = ranked[0][0], (ranked[1][0] if len(ranked) > 1 else "")
        covered = sum(n for bag, n in bags if surname in bag)
        if covered < 0.75 * personal:
            groups.add(npi)
            continue
        people[npi] = {"surname": surname, "given": given, "name": f"{surname.title()}, {given.title()}" if given else surname.title()}
    return people, groups


def match_person(people: dict[str, dict], words: tuple[str, ...], initials: tuple[str, ...] = ()) -> str | None:
    """The one person a written name can only mean, or None. A surname alone is accepted; a surname next to a different
    first name is not."""
    hits: list[tuple[int, str]] = []
    for npi, p in people.items():
        s, g = p["surname"], p["given"]
        if not s or s not in words:
            continue
        others = [w for w in words if w != s]
        if g and g in others:
            hits.append((0, npi))
        elif not others and g and initials and g[0] in initials:
            hits.append((1, npi))
        elif not others and not initials:
            hits.append((2, npi))
        elif not others and not g:
            hits.append((2, npi))
    if not hits:
        return None
    best = min(h[0] for h in hits)
    cands = sorted({n for r, n in hits if r == best})
    return cands[0] if len(cands) == 1 else None


def _slug(name: str) -> str:
    return "ema:" + re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")


# --------------------------------------------------------------------------- schema
def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    cur = conn.execute("SELECT value FROM meta WHERE key='activity_schema'").fetchone()
    if not cur or cur[0] != ACTIVITY_VERSION:
        for t in TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {t}")
        conn.execute("DELETE FROM meta WHERE key=?", (INFO_KEY,))
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('activity_schema', ?)", (ACTIVITY_VERSION,))
    conn.executescript(DDL)
    conn.commit()


def info(conn: sqlite3.Connection) -> dict:
    try:
        r = conn.execute("SELECT value FROM meta WHERE key=?", (INFO_KEY,)).fetchone()
        return json.loads(r[0]) if r else {}
    except (sqlite3.Error, ValueError):
        return {}


def stale(conn: sqlite3.Connection) -> bool:
    """True when the store holds remittance lines and the activity tables were never built, or by an older version."""
    try:
        if conn.execute("SELECT 1 FROM lines LIMIT 1").fetchone() is None:
            return False
    except sqlite3.Error:
        return False
    return info(conn).get("version") != ACTIVITY_VERSION


def _days(a: str | None, b: str | None) -> int | None:
    try:
        return (date.fromisoformat((b or "")[:10]) - date.fromisoformat((a or "")[:10])).days
    except ValueError:
        return None


# --------------------------------------------------------------------------- build
def build_activity(store, log=print) -> dict:
    """Rebuild every provider table from the store. Returns a short summary."""
    conn = store.conn if hasattr(store, "conn") else store
    t0 = time.time()
    ensure_schema(conn)
    people, groups = identify_people(conn)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    has_ema = "ema_rows" in tables and conn.execute("SELECT 1 FROM ema_rows LIMIT 1").fetchone() is not None

    # EMA: which clinician each bill belongs to (used only where no remittance names a person)
    ema_people: dict[str, str | None] = {}

    def ema_id(name: str | None) -> str | None:
        if not name:
            return None
        if name not in ema_people:
            words, initials = name_tokens(name)
            ema_people[name] = match_person(people, words, initials) or _slug(name)
        return ema_people[name]

    ema_bill: dict[str, str] = {}
    if has_ema:
        votes: dict[str, Counter] = defaultdict(Counter)
        for claim_id, bill_id, provider in conn.execute(
                """SELECT claim_id, bill_id, provider FROM ema_rows
                   WHERE family='charge_lines' AND activity_type='Charge' AND provider IS NOT NULL AND (claim_id IS NOT NULL OR bill_id IS NOT NULL)"""):
            for b in {(claim_id or "")[:9], (bill_id or "")[:9]}:
                if len(b) == 9:
                    votes[b][provider] += 1
        ema_bill = {b: min(v.items(), key=lambda kv: (-kv[1], kv[0]))[0] for b, v in votes.items()}
        del votes

    newest = conn.execute("SELECT MAX(payment_date), MIN(payment_date) FROM transactions WHERE superseded = 0").fetchone()
    last_payment, first_payment = newest[0], newest[1]
    ref_from = ref_to = None
    if last_payment:
        try:
            p = date.fromisoformat(last_payment)
            ref_from, ref_to = (p - timedelta(days=485)).isoformat(), (p - timedelta(days=LAG_DAYS)).isoformat()
        except ValueError:
            pass

    days: dict[tuple, dict] = defaultdict(dict)                     # (provider, date) -> {patient: [is_visit, new, est, lesions]}
    day_n: dict[tuple, list] = defaultdict(lambda: [0] * 8)           # path, radiation, items, charge, allowed, paid, clinic allowed, biopsy units
    services: dict[tuple, list] = defaultdict(lambda: [0, 0.0, 0, 0, 0, 0])
    payers: dict[tuple, list] = defaultdict(lambda: [0, 0, 0, 0, 0, 0])
    journeys: dict[tuple, list] = {}                                 # patient -> [(date, clinician, new patient visit, biopsied)]; never stored
    treated: dict[tuple, list] = {}                                  # patient -> dates a skin cancer was treated, by anyone
    last_visit: dict[tuple, list] = {}                               # patient -> [date last seen face to face, the clinician then]
    lag_hist: Counter = Counter()
    how: Counter = Counter()                                         # how each encounter found its clinician
    took: dict[tuple, str] = {}                                      # (patient, date) -> the clinician who took the biopsy
    read: list[tuple] = []                                           # (patient, date, allowed) for pathology billed on its own
    kind_cache: dict[tuple, str] = {}
    undated = 0

    def payer_kind(name, cfi) -> str:
        k = (name, cfi)
        if k not in kind_cache:
            kind_cache[k] = payer_group_kind(name, cfi)[1]
        return kind_cache[k]

    def flush(bill: str, rows: list[tuple]) -> None:
        nonlocal undated
        # ---- who: evidence from every remittance of the bill, best kind first, newest primary remittance first
        evidence: dict[str, tuple] = {}
        group_seen = False
        refs: dict[str, Counter] = defaultdict(Counter)
        for r in rows:
            if r[17]:
                try:
                    h = json.loads(r[17]).get("HPI")
                except ValueError:
                    h = None
                if h:
                    refs[r[0]][h] += 1
        for r in rows:
            ck = r[0]
            if ck in evidence:
                continue
            npi, last, first = r[14], r[15] or "", r[16] or ""
            rank = (0 if r[2] in PRIMARY else 1, r[12] or "")
            who, src = None, 9
            if npi and npi in people:
                who, src = npi, 0
            else:
                if npi and npi in groups:
                    group_seen = True
                if ck in refs:
                    h = min(refs[ck].items(), key=lambda kv: (-kv[1], kv[0]))[0]
                    if h in people:
                        who, src = h, 1
                if who is None and (last or first) and not is_org(f"{last} {first}"):
                    words, initials = name_tokens(last, first)
                    m = match_person(people, words, initials) if words else None
                    if m:
                        who, src = m, 2
            evidence[ck] = (src, rank, who)
        found = [e for e in evidence.values() if e[2]]
        if found:
            best = min(e[0] for e in found)
            pick = [e for e in found if e[0] == best]
            newest_primary = [e for e in pick if e[1][0] == 0]
            pool = newest_primary or pick
            top = max(e[1][1] for e in pool)
            provider = min(e[2] for e in pool if e[1][1] == top)
            source = ("npi", "line_ref", "name")[best]
        else:
            ema_name = ema_bill.get(bill)
            via = ema_id(ema_name)
            if via:
                provider, source = via, "ema"
            else:
                provider, source = ("group" if group_seen else "unattributed"), ("group" if group_seen else "none")

        # ---- what: fold the remittance lines into services
        items: dict[tuple, list] = {}
        by_sig: dict[tuple, list] = defaultdict(list)
        loose: list[tuple] = []
        for r in rows:
            code = clean_code(r[4] or r[3])
            if not code:
                continue
            if r[11]:
                key = ("L", r[11])
                if key not in items:
                    # date, code, units, charge, allowed, paid, live, primary, denied, payer kind, first primary payment
                    items[key] = [None, code, 0.0, 0, 0, 0, False, False, False, None, None]
                _fold(items[key], r, payer_kind)
            else:
                loose.append(r)
        for key, it in items.items():
            by_sig[(it[0], it[1], it[3])].append(key)
            by_sig[(None, it[1], it[3])].append(key)
        nth: Counter = Counter()
        for r in loose:
            code = clean_code(r[4] or r[3])
            d = r[10] or r[13]
            sig = (d, code, abs(r[7] or 0))
            k = nth[(r[0], sig)]
            nth[(r[0], sig)] += 1
            cands = by_sig.get(sig) or []
            if k < len(cands):
                key = cands[k]
            else:
                key = ("S", sig, k)
                items[key] = [None, code, 0.0, 0, 0, 0, False, False, False, None, None]
                by_sig[sig].append(key)
                if d is not None:
                    by_sig[(None, code, sig[2])].append(key)
            _fold(items[key], r, payer_kind)

        # ---- the patient (never stored): two bills for one person on one day are one patient seen
        named = next((r for r in rows if (r[18] or "").strip()), None)
        patient = (re.sub(r"[^A-Z0-9]", "", named[18].upper()), re.sub(r"[^A-Z0-9]", "", (named[19] or "").upper())) if named else ("#", bill)

        # ---- encounters: one per date of service on the bill
        by_date: dict[str, list] = defaultdict(list)
        for it in items.values():
            if not it[6]:               # only reversals were seen for it
                continue
            if not it[0]:
                undated += 1
                continue
            by_date[it[0]].append(it)
        for d, its in by_date.items():
            fams = [service_family(it[1]) for it in its]
            new = any(f == "new_visit" for f in fams)
            est = any(f == "est_visit" for f in fams)
            high = any(it[1] in ("99214", "99215") for it in its)
            if new or est:
                kind = "visit"
            elif all(f == "pathology" for f in fams):
                kind = "path"
            elif all(f in NOT_CLINIC for f in fams):
                kind = "radiation"
            else:
                kind = "procedure"
            lesions = sum((it[2] or 1.0) for it in its if it[1] in BIOPSY)
            n = day_n[(provider, d)]
            month = d[:7]
            enc_kind = None
            enc = [0, 0, 0]
            first_paid = None
            for it, fam in zip(its, fams):
                n[2] += 1
                n[3] += it[3]
                n[4] += it[4]
                n[5] += it[5]
                if fam not in NOT_PER_PATIENT:
                    n[6] += it[4]
                s = services[(provider, month, it[1])]
                s[0] += 1
                s[1] += it[2] or 1.0
                s[2] += 1 if it[8] else 0
                s[3] += it[3]
                s[4] += it[4]
                s[5] += it[5]
                enc[0] += it[3]
                enc[1] += it[4]
                enc[2] += it[5]
                if it[9] and enc_kind is None:
                    enc_kind = it[9]
                if it[10] and (first_paid is None or it[10] < first_paid):
                    first_paid = it[10]
            pk = payers[(provider, month, enc_kind or "secondary only")]
            pk[0] += 1
            pk[1] += enc[0]
            pk[2] += enc[1]
            pk[3] += enc[2]
            if kind in ("visit", "procedure"):
                pk[4] += 1
                pk[5] += 1 if lesions else 0
                if provider in people and patient[0] != "#":
                    journeys.setdefault(patient, []).append((d, provider, new, bool(lesions)))
                if patient[0] != "#":
                    seen_last = last_visit.get(patient)
                    if seen_last is None:
                        last_visit[patient] = [d, provider if provider in people else None]
                    elif d > seen_last[0] or (d == seen_last[0] and provider in people and (seen_last[1] is None or provider < seen_last[1])):
                        seen_last[0] = d
                        seen_last[1] = provider if provider in people else seen_last[1]
            if patient[0] != "#" and any(f in CANCER_TREATMENT or it[1].startswith("116") for it, f in zip(its, fams)):
                treated.setdefault(patient, []).append(d)
            how[source] += 1
            if lesions and kind != "path" and provider not in ("group", "unattributed"):
                took.setdefault((patient, d), provider)
            if kind == "path":
                n[0] += 1
                read.append((patient, d, enc[1]))
            elif kind == "radiation":
                n[1] += 1
            else:
                seen = days[(provider, d)].get(patient)
                if seen is None:
                    days[(provider, d)][patient] = [kind == "visit", new, est and not new, lesions, high]
                else:
                    seen[0] = seen[0] or kind == "visit"
                    seen[1] = seen[1] or new
                    seen[2] = (seen[2] or est) and not seen[1]
                    seen[3] += lesions
                    seen[4] = seen[4] or high
            if first_paid and ref_from and ref_from <= d <= ref_to:
                lag = _days(d, first_paid)
                if lag is not None and lag >= 0:
                    lag_hist[min(lag, LAG_DAYS)] += 1

    cur = conn.execute(
        """SELECT l.claim_key, l.line_seq, l.claim_status_code, l.procedure_code, l.original_procedure_code, l.paid_units, l.original_units,
                  l.charge_cents, l.paid_cents, l.allowed_cents, l.service_date, l.line_control_number, l.payment_date, c.statement_from,
                  c.rendering_provider_npi, c.rendering_provider_last, c.rendering_provider_first,
                  CASE WHEN l.other_refs LIKE '%"HPI"%' THEN l.other_refs END, c.patient_last, c.patient_first,
                  l.payer_name, c.claim_filing_indicator,
                  CASE WHEN length(l.patient_control_number) >= 9 THEN substr(l.patient_control_number, 1, 9) ELSE l.claim_key END AS bill
           FROM lines l JOIN claims c ON c.claim_key = l.claim_key JOIN transactions t ON t.transaction_key = l.transaction_key
           WHERE t.superseded = 0
           ORDER BY bill, l.payment_date, CASE WHEN l.claim_status_code = '22' THEN 0 ELSE 1 END, l.claim_key, l.line_seq""")
    bill, rows, n_lines, n_bills = None, [], 0, 0
    while True:
        chunk = cur.fetchmany(20000)
        if not chunk:
            break
        for r in chunk:
            n_lines += 1
            if r[22] != bill:
                if rows:
                    flush(bill, rows)
                    n_bills += 1
                bill, rows = r[22], []
            rows.append(r)
    if rows:
        flush(bill, rows)
        n_bills += 1

    # ---- pathology is billed on its own bill under the physician who read the slide. Credit it, as a separate figure, to the
    # clinician who took the specimen from the same patient on the same day, so a biopsy can be seen for what it brings in.
    generated: Counter = Counter()
    for patient, d, allowed in read:
        who = took.get((patient, d))
        if who and allowed:
            generated[(who, d)] += allowed
    took.clear()
    read.clear()

    # ---- write the remittance side
    day_rows = []
    span: dict[str, list] = {}
    for key in sorted(set(days) | set(day_n)):
        provider, d = key
        seen = days.get(key, {})
        n = day_n.get(key) or [0] * 8
        pts = len(seen)
        visits = sum(1 for v in seen.values() if v[0])
        new = sum(1 for v in seen.values() if v[1])
        est = sum(1 for v in seen.values() if v[2])
        bx_pts = sum(1 for v in seen.values() if v[3])
        lesions = sum(v[3] for v in seen.values())
        day_rows.append((provider, d, pts, visits, new, est, pts - visits, n[0], n[1], bx_pts, lesions, n[2], n[3], n[4], n[5], n[6],
                         generated.get(key, 0)))
        sp = span.setdefault(provider, [d, d, 0])
        sp[0], sp[1], sp[2] = min(sp[0], d), max(sp[1], d), sp[2] + pts
    for t in ("provider_days", "provider_services", "provider_payers"):
        conn.execute(f"DELETE FROM {t}")
    conn.executemany("INSERT INTO provider_days VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", day_rows)
    conn.executemany("INSERT INTO provider_services VALUES (?,?,?,?,?,?,?,?,?,?)",
                     [(k[0], k[1], k[2], service_family(k[2]), v[0], v[1], v[2], v[3], v[4], v[5]) for k, v in sorted(services.items())])
    conn.executemany("INSERT INTO provider_payers VALUES (?,?,?,?,?,?,?,?,?)", [k + tuple(v) for k, v in sorted(payers.items())])
    for done in (days, day_n, services, payers):       # give the memory back before following patients through time
        done.clear()
    build_panels(conn, journeys, treated, first_payment, last_payment)
    build_recall(conn, treated, last_visit, last_payment)
    journeys.clear()
    treated.clear()
    last_visit.clear()

    # ---- the EMA side: recent weeks, self-pay included, and how long a visit takes to become a bill
    ema_summary = build_ema(conn, ema_id) if has_ema else {}

    # ---- roster
    tiers = {}
    if "providers" in tables:
        tiers = {r[0]: r[1] for r in conn.execute("SELECT npi, tier FROM providers")}
    roles: dict[str, tuple] = {}
    if has_ema:
        for pid, name, role, n in conn.execute(
                "SELECT provider_id, provider, MAX(role), SUM(patients) FROM provider_ema_days GROUP BY 1, 2 ORDER BY 4 DESC"):
            roles.setdefault(pid, (name, role))
    conn.execute("DELETE FROM provider_roster")
    roster = []
    for pid in sorted(set(span) | set(roles)):
        sp = span.get(pid) or [None, None, 0]
        ema_name, role = roles.get(pid, (None, None))
        if pid in people:
            kind, name = "person", ema_name or people[pid]["name"]
        elif pid == "group":
            kind, name = "group", "Billed under the group only"
        elif pid == "unattributed":
            kind, name = "unattributed", "No clinician on the remittance"
        elif pid.startswith("ema:"):
            kind, name = "ema_only", ema_name or pid[4:]
        else:
            kind, name = "person", f"NPI {pid}"
        tier = _tier(role) or tiers.get(pid) or "unknown"
        roster.append((pid, name, kind, tier, role, ema_name, sp[0], sp[1], sp[2]))
    conn.executemany("INSERT INTO provider_roster VALUES (?,?,?,?,?,?,?,?,?)", roster)

    # ---- how long a visit takes to reach a remittance: decides which months are complete
    total = sum(lag_hist.values())
    cdf: list[float] = []
    marks: dict[str, int] = {}
    if total >= 200:
        acc = 0
        for dd in range(LAG_DAYS + 1):
            acc += lag_hist.get(dd, 0)
            cdf.append(round(acc / total, 4))
            for p in (0.5, 0.75, 0.9, 0.95, 0.98):
                if f"p{int(p * 100)}" not in marks and acc >= p * total:
                    marks[f"p{int(p * 100)}"] = dd
    out = {
        "version": ACTIVITY_VERSION, "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "first_payment": first_payment, "last_payment": last_payment, "lines": n_lines, "bills": n_bills, "undated_services": undated,
        "clinic_day_min": CLINIC_DAY_MIN, "lag": dict(marks, n=total, cdf=cdf), "attribution": dict(how),
        "people": len(people), "group_ids": len(groups), "ema": ema_summary, "seconds": round(time.time() - t0, 1),
    }
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (INFO_KEY, json.dumps(out)))
    conn.commit()
    summary = {k: out[k] for k in ("lines", "bills", "people", "seconds")}
    summary["providers"] = len(roster)
    log(json.dumps({"activity": summary}))
    return summary


def build_panels(conn: sqlite3.Connection, journeys: dict, treated: dict, first_payment: str | None, last_payment: str | None) -> None:
    """Who keeps coming back, and what the biopsies find.

    A clinician's panel is the distinct patients they saw in twelve months; `kept` is how many of the panel of the twelve
    months before were seen again, by anyone in the practice, in these twelve. A cohort is the patients a clinician first
    saw on a new-patient code in a quarter, and how many returned within 90 and 365 days. `treated_120` counts the
    patients a clinician biopsied who had a skin cancer treated here (Mohs, malignant excision or destruction,
    radiation) within 120 days: a floor on how often the biopsy found one, comparable between clinicians.
    Patients are told apart by name as the payer wrote it, which is close enough for a rate and is never stored."""
    conn.execute("DELETE FROM provider_panels")
    conn.execute("DELETE FROM provider_cohorts")
    if not journeys or not last_payment:
        return
    base = (first_payment or min(v[0][0] for v in journeys.values()))[:7]
    y0, m0 = int(base[:4]), int(base[5:7])
    last = last_payment[:7]
    n_months = (int(last[:4]) - y0) * 12 + int(last[5:7]) - m0 + 1
    if n_months < 1:
        return
    full = (1 << 12) - 1
    panel = defaultdict(lambda: [[0, 0, 0, 0] for _ in range(n_months)])
    cohort: dict[tuple, list] = defaultdict(lambda: [0, 0, 0, 0, 0, 0, 0])
    quarter = lambda d: f"{d[:4]}-Q{(int(d[5:7]) + 2) // 3}"      # noqa: E731
    for patient, visits in journeys.items():
        visits.sort()
        anyone = 0
        own: dict[str, int] = {}
        for d, who, _new, _bx in visits:
            i = (int(d[:4]) - y0) * 12 + int(d[5:7]) - m0
            if 0 <= i < n_months:
                anyone |= 1 << i
                own[who] = own.get(who, 0) | (1 << i)
        for who, mask in own.items():
            rows = panel[who]
            for i in range(n_months):
                window = (full << (i - 11)) if i >= 11 else (full >> (11 - i))
                row = rows[i]
                seen = bool(mask & window)
                row[0] += seen
                if i >= 23 and mask & (full << (i - 23)):
                    row[1] += 1
                    row[2] += bool(anyone & window)
                    row[3] += seen
        counted: set = set()
        cancer = treated.get(patient) or ()
        for k, (d, who, new, bx) in enumerate(visits):
            if bx:
                c = cohort[(who, quarter(d))]
                c[5] += 1
                c[6] += any(x > d and (_days(d, x) or 999) <= 120 for x in cancer)
            if not new or who in counted:
                continue
            counted.add(who)
            c = cohort[(who, quarter(d))]
            c[0] += 1
            later = [(x, w) for x, w, _n, _b in visits[k + 1:] if x > d and (_days(d, x) or 999) <= 365]
            c[1] += any((_days(d, x) or 999) <= 90 for x, _w in later)
            c[2] += bool(later)
            c[3] += any(w == who for _x, w in later)
            c[4] += len({x for x, _w in later})
    rows = []
    for who, months in sorted(panel.items()):
        for i, r in enumerate(months):
            if i < 11 or not r[0]:
                continue
            y, m = y0 + (m0 - 1 + i) // 12, (m0 - 1 + i) % 12 + 1
            rows.append((who, f"{y:04d}-{m:02d}", r[0], r[1] if i >= 23 else None, r[2] if i >= 23 else None, r[3] if i >= 23 else None))
    conn.executemany("INSERT INTO provider_panels VALUES (?,?,?,?,?,?)", rows)
    conn.executemany("INSERT INTO provider_cohorts VALUES (?,?,?,?,?,?,?,?,?)", [k + tuple(v) for k, v in sorted(cohort.items())])


def build_recall(conn: sqlite3.Connection, treated: dict, last_visit: dict, last_payment: str | None) -> None:
    """Patients who had a skin cancer treated here and have not been seen face to face for a year or more, counted under
    the clinician who saw them last. Measured 60 days before the newest payment, so a recent visit still waiting to be
    paid is not mistaken for an absence. A count, not a list: some of these patients have moved, died or are followed
    elsewhere, and the list itself belongs in EMA."""
    conn.execute("DELETE FROM provider_recall")
    try:
        as_of = date.fromisoformat((last_payment or "")[:10]) - timedelta(days=60)
    except ValueError:
        return
    cut12, cut18 = (as_of - timedelta(days=365)).isoformat(), (as_of - timedelta(days=548)).isoformat()
    n: dict[str, list] = defaultdict(lambda: [0, 0, 0])
    for patient, dates in treated.items():
        seen = last_visit.get(patient) or [None, None]
        last = max([d for d in (seen[0], max(dates)) if d])
        who = n[seen[1] or "unattributed"]
        who[0] += 1
        who[1] += last < cut12
        who[2] += last < cut18
    conn.executemany("INSERT INTO provider_recall VALUES (?,?,?,?,?)", [(k, v[0], v[1], v[2], as_of.isoformat()) for k, v in sorted(n.items())])


def refresh(store, log=print) -> dict:
    """Rebuild the provider tables without ever failing the run that asked for it. An error is kept where the dashboard
    can show it and cleared by the next good build."""
    conn = store.conn if hasattr(store, "conn") else store
    try:
        out = build_activity(store, log=log)
        conn.execute("DELETE FROM meta WHERE key='activity:error'")
        conn.commit()
        return out
    except Exception as exc:        # noqa: BLE001  (the remittance analysis must not be lost to a fault here)
        try:
            conn.rollback()
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('activity:error', ?)", (f"{type(exc).__name__}: {str(exc)[:200]}",))
            conn.commit()
        except sqlite3.Error:
            pass
        log(f"provider activity could not be built: {type(exc).__name__}: {exc}")
        return {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}


def status(conn: sqlite3.Connection) -> dict:
    """Whether the tables are there to read, for the dashboard."""
    meta = info(conn)
    try:
        err = conn.execute("SELECT value FROM meta WHERE key='activity:error'").fetchone()
    except sqlite3.Error:
        err = None
    return {"ready": meta.get("version") == ACTIVITY_VERSION, "built_at": meta.get("built_at"), "error": err[0] if err else None}


def _fold(it: list, r: tuple, payer_kind) -> None:
    """Add one remittance line to its service. `it` is [date, code, units, charge, allowed, paid, live, primary, denied,
    payer kind, first primary payment date]; the lines of a bill arrive oldest payment first."""
    status = r[2]
    it[5] += r[8] or 0                      # every insurer payment, reversals included
    if status == "22":                      # a reversal takes back what was decided before it; what follows decides again
        it[4], it[8] = 0, False
        return
    it[6] = True
    if it[0] is None:
        it[0] = r[10] or r[13]
    units = r[6] if r[6] is not None else r[5]
    if units and abs(units) > it[2]:
        it[2] = float(abs(units))
    charge = abs(r[7] or 0)
    if charge > it[3]:
        it[3] = charge
    if status in PRIMARY or status == "4":
        if not it[7] or status in PRIMARY:
            allowed = r[9] or 0
            if status in PRIMARY and allowed > 0:
                it[4] = allowed
                it[8] = False
            elif not it[4]:
                it[8] = (r[8] or 0) == 0
        it[7] = True
        it[9] = payer_kind(r[20], r[21])
        if it[10] is None or (r[12] and r[12] < it[10]):
            it[10] = r[12]


def _tier(role: str | None) -> str | None:
    r = (role or "").lower()
    if not r:
        return None
    if "doctor" in r or r in ("physician", "md", "do"):
        return "physician"
    if "assistant" in r and "medical" not in r or "nurse practitioner" in r or r in ("pa", "np", "aprn"):
        return "nonphysician"
    return "staff"


def build_ema(conn: sqlite3.Connection, ema_id) -> dict:
    """Per clinician and day from the EMA charge exports.

    The exports come in several layouts. The full one names the patient, the bill and the bill item and says when the
    bill was created; the thin weekly one carries only the claim id. A visit is identified by its patient where the
    export names one, else by its bill. Overlapping exports repeat a charge; it is counted once.

    An export only holds the visits that had become a bill when it was pulled, so each day also records the latest
    date an export that covers it was pulled (`pulled`): the newest bill creation date in that export, or its last day
    of service when the layout has no creation date. The dashboard treats a day as captured only when that is far enough
    behind for the clinician's bills to exist."""
    conn.execute("DELETE FROM provider_ema_days")
    conn.execute("DELETE FROM provider_ema_lag")
    voided = {r[0] for r in conn.execute(
        "SELECT bill_item_id FROM ema_rows WHERE family='charge_lines' AND activity_type LIKE 'Void%' AND bill_item_id IS NOT NULL")}
    base = """FROM ema_rows WHERE family='charge_lines' AND activity_type='Charge' AND service_date IS NOT NULL AND provider IS NOT NULL"""
    pulled_at: dict[str, str] = {}
    for source, created, last_day in conn.execute(f"SELECT source_name, MAX(substr(bill_created,1,10)), MAX(service_date) {base} GROUP BY 1"):
        pulled_at[source] = max(created or "", last_day or "")
    patient_of: dict[str, str] = {}
    for bill_id, claim_id, patient in conn.execute(f"SELECT bill_id, claim_id, patient_id {base} AND patient_id IS NOT NULL"):
        b = (bill_id or claim_id or "")[:9]
        if b:
            patient_of.setdefault(b, patient)
    seen_item: set = set()
    seen_sig: dict[tuple, str] = {}
    day: dict[tuple, dict] = {}
    bills: dict[tuple, tuple] = {}
    first_service = last_service = None
    for (source, provider, role, d, patient, bill_id, claim_id, item, cpt, units, charge, created, location, mods) in conn.execute(
            f"""SELECT source_name, provider, provider_role, service_date, patient_id, bill_id, claim_id, bill_item_id, cpt, units, charges_cents,
                       bill_created, location, modifiers {base}
                ORDER BY CASE WHEN bill_item_id IS NOT NULL THEN 0 ELSE 1 END, source_name, row_index"""):
        if item:
            if item in seen_item or item in voided:
                continue
            seen_item.add(item)
        bill = (bill_id or claim_id or "")[:9] or None
        sig = (bill, provider, d, cpt, mods, units, charge)
        prev = seen_sig.get(sig)
        if prev is not None and prev != source:     # the same charge again, in an export that carries no bill item id
            continue
        seen_sig[sig] = source
        e = day.get((provider, d))
        if e is None:
            e = day[(provider, d)] = {"role": None, "pts": {}, "loose": 0, "lesions": 0.0, "charges": 0, "clinic": 0, "loc": Counter(), "pulled": ""}
        e["role"] = e["role"] or role
        if pulled_at.get(source, "") > e["pulled"]:
            e["pulled"] = pulled_at[source]
        code = clean_code(cpt)
        fam = service_family(code)
        who = patient or (patient_of.get(bill) if bill else None) or bill
        if who is None and fam in ("new_visit", "est_visit"):
            who = f"visit:{len(seen_sig)}"          # no id at all (a self-pay visit in the thin layout): one visit code is one visit
        if who is None:
            if fam not in NOT_CLINIC:
                e["loose"] += 1
        else:
            p = e["pts"].get(who)
            if p is None:
                p = e["pts"][who] = [False, False, False, False, False, False]    # new, established, high level, seen, radiation, insured
            if fam == "new_visit":
                p[0] = True
            elif fam == "est_visit":
                p[1] = True
                p[2] = p[2] or code in ("99214", "99215")
            if fam not in NOT_CLINIC:
                p[3] = True
            elif fam == "radiation":
                p[4] = True
            if claim_id:
                p[5] = True
        if code in BIOPSY:
            e["lesions"] += abs(units or 1.0)
        e["charges"] += charge or 0
        if fam not in NOT_PER_PATIENT:
            e["clinic"] += charge or 0
        if location:
            e["loc"][location] += 1
        if bill and created and (provider, bill) not in bills:
            bills[(provider, bill)] = (d, created[:10], pulled_at.get(source, ""))
        first_service = d if first_service is None or d < first_service else first_service
        last_service = d if last_service is None or d > last_service else last_service
    rows = []
    for (provider, d), e in sorted(day.items()):
        pts = list(e["pts"].values())
        seen = [p for p in pts if p[3]]
        loc = min(e["loc"].items(), key=lambda kv: (-kv[1], kv[0]))[0] if e["loc"] else None
        rows.append((provider, ema_id(provider), e["role"], d, len(seen), sum(1 for p in seen if p[5]), e["loose"],
                     sum(1 for p in seen if p[0] or p[1]), sum(1 for p in seen if p[0]), sum(1 for p in seen if p[1] and not p[0]),
                     sum(1 for p in seen if p[2]), sum(1 for p in pts if not p[3] and not p[4]), sum(1 for p in pts if not p[3] and p[4]),
                     e["lesions"], e["charges"], e["clinic"], loc, e["pulled"] or None))
    conn.executemany("INSERT INTO provider_ema_days VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    last_created = max((c for (_d, c, _p) in bills.values()), default=None)
    # days from the visit to the bill. A bill written after its export was pulled is not in it, so the newest three weeks
    # of an export under-report slow billers; "mature" marks the visits old enough for nearly every bill to exist.
    lag: Counter = Counter()
    for (provider, _bill), (d, created, pulled) in bills.items():
        n = _days(d, created)
        if n is None or n < 0:
            continue
        mature = 1 if pulled and (_days(d, pulled) or 0) >= 21 else 0
        lag[(provider, min(n, 90), mature)] += 1
    conn.executemany("INSERT INTO provider_ema_lag VALUES (?,?,?,?,?)", [(k[0], ema_id(k[0]), k[1], v, k[2]) for k, v in sorted(lag.items())])
    return {"first_service": first_service, "last_service": last_service, "last_bill_created": last_created, "exports": len(pulled_at),
            "exports_with_bill_dates": len({p for (_d, _c, p) in bills.values()}), "charge_rows": len(seen_sig), "provider_days": len(rows)}


# --------------------------------------------------------------------------- reading it back (the dashboard)
def _quantile(vals: list, q: float) -> float | None:
    if not vals:
        return None
    v = sorted(vals)
    x = (len(v) - 1) * q
    lo, hi = int(x), min(int(x) + 1, len(v) - 1)
    return round(v[lo] + (v[hi] - v[lo]) * (x - lo), 1)


def month_completeness(month: str, last_payment: str | None, cdf: list) -> float:
    """Share of a month's visits that should have reached a remittance by the newest payment date in the store."""
    try:
        y, m = int(month[:4]), int(month[5:7])
        p = date.fromisoformat(last_payment or "")
    except ValueError:
        return 1.0
    n = calendar.monthrange(y, m)[1]
    tot = 0.0
    for d in range(1, n + 1):
        lag = (p - date(y, m, d)).days
        if lag < 0:
            continue
        tot += cdf[min(lag, len(cdf) - 1)] if cdf else (1.0 if lag >= 60 else lag / 60.0)
    return round(tot / n, 3)


def _add_months(month: str, k: int) -> str:
    y, m = int(month[:4]), int(month[5:7]) - 1 + k
    return f"{y + m // 12:04d}-{m % 12 + 1:02d}"


def coverage(conn: sqlite3.Connection) -> dict:
    """Which months of service the remittances cover, and how complete the newest ones are."""
    meta = info(conn)
    first_pay, last_pay = meta.get("first_payment"), meta.get("last_payment")
    cdf = (meta.get("lag") or {}).get("cdf") or []
    r = conn.execute("SELECT MIN(service_date), MAX(service_date) FROM provider_days WHERE patients > 0").fetchone()
    if not r or not r[0] or not last_pay:
        return {"months": [], "first_month": None, "last_month": None, "last_complete_month": None, "default_from": None, "default_to": None,
                "last_payment": last_pay, "lag": {k: v for k, v in (meta.get("lag") or {}).items() if k != "cdf"}}
    # a month is covered from its first day only when payments were already arriving then
    first_month = first_pay[:7] if first_pay and first_pay[8:10] <= "03" else _add_months((first_pay or r[0])[:7], 1)
    first_month = max(first_month, r[0][:7])
    last_month = min(r[1][:7], last_pay[:7])
    months = []
    m = first_month
    while m <= last_month:
        months.append({"month": m, "complete": month_completeness(m, last_pay, cdf)})
        m = _add_months(m, 1)
    done = [x["month"] for x in months if x["complete"] >= COMPLETE_SHARE]
    last_complete = done[-1] if done else (months[-1]["month"] if months else None)
    default_from = max(first_month, _add_months(last_complete, -11)) if last_complete else None
    return {"months": months, "first_month": first_month, "last_month": last_month, "last_complete_month": last_complete,
            "default_from": default_from, "default_to": last_complete, "last_payment": last_pay, "first_payment": first_pay,
            "lag": {k: v for k, v in (meta.get("lag") or {}).items() if k != "cdf"}, "complete_share": COMPLETE_SHARE}


def _window(conn, month_from: str | None, month_to: str | None) -> tuple[dict, str, str]:
    cov = coverage(conn)
    ok = re.compile(r"^\d{4}-\d{2}$")
    a = month_from if month_from and ok.match(month_from) else cov["default_from"]
    b = month_to if month_to and ok.match(month_to) else cov["default_to"]
    a, b = a or "0000-00", b or "9999-99"
    if a > b:
        a, b = b, a
    return cov, a, b


def _weeks(a: str, b: str) -> float:
    try:
        d0 = date(int(a[:4]), int(a[5:7]), 1)
        d1 = date(int(b[:4]), int(b[5:7]), calendar.monthrange(int(b[:4]), int(b[5:7]))[1])
        return max((d1 - d0).days + 1, 1) / 7.0
    except ValueError:
        return 0.0


def _share(n, d) -> float | None:
    return round(n / d, 4) if d else None


def _rate(mix: dict, only: tuple | None, without: tuple = ()) -> float | None:
    """Patients biopsied per 100 seen, within some kinds of payer. Age drives biopsies, and the payer is the nearest thing
    to age on a remittance, so clinicians are compared within Medicare and within everyone else."""
    seen = sum(v["seen"] or 0 for k, v in mix.items() if (only is None or k in only) and k not in without)
    bx = sum(v["biopsied"] or 0 for k, v in mix.items() if (only is None or k in only) and k not in without)
    return round(100 * bx / seen, 1) if seen >= 100 else None


def _roster(conn) -> dict[str, dict]:
    conn.row_factory = sqlite3.Row
    return {r["provider_id"]: dict(r) for r in conn.execute("SELECT * FROM provider_roster")}


DOWNCODE = ("DOWNCODE_SVC06", "DOWNCODE_VS_BILLED", "DOWNCODE_CHARGE", "DOWNCODE_LEVEL_REDUCED", "DOWNCODE_RATE", "DOWNCODE_LINE_SWAP",
            "DOWNCODE_LEVEL_DENIED")


def overview(conn: sqlite3.Connection, month_from: str | None = None, month_to: str | None = None) -> dict:
    """One row per clinician for a window of months of service."""
    conn.row_factory = sqlite3.Row
    cov, a, b = _window(conn, month_from, month_to)
    roster = _roster(conn)
    d0, d1 = f"{a}-01", f"{b}-31"
    per: dict[str, dict] = {}
    for r in conn.execute("SELECT * FROM provider_days WHERE service_date BETWEEN ? AND ? ORDER BY provider_id, service_date", (d0, d1)):
        p = per.setdefault(r["provider_id"], {"days": [], "sum": Counter()})
        p["days"].append(r["patients"])
        for k in ("patients", "visits", "new_visits", "est_visits", "proc_only", "path_only", "radiation", "biopsy_patients", "biopsy_lesions",
                  "items", "charge_cents", "allowed_cents", "paid_cents", "clinic_allowed_cents", "path_generated_cents"):
            p["sum"][k] += r[k] or 0
        if r["patients"] >= CLINIC_DAY_MIN:
            p["sum"]["clinic_patients"] += r["patients"]
            p["sum"]["clinic_new"] += r["new_visits"]
            p["sum"]["clinic_day_allowed"] += r["clinic_allowed_cents"] or 0
    fam: dict[str, dict] = defaultdict(lambda: defaultdict(lambda: [0, 0.0, 0, 0]))
    codes: dict[str, Counter] = defaultdict(Counter)
    for r in conn.execute("SELECT provider_id, code, family, SUM(items) n, SUM(units) u, SUM(allowed_cents) a, SUM(charge_cents) c FROM provider_services "
                          "WHERE month BETWEEN ? AND ? GROUP BY 1, 2, 3", (a, b)):
        f = fam[r["provider_id"]][r["family"]]
        f[0] += r["n"]
        f[1] += r["u"] or 0
        f[2] += r["a"] or 0
        f[3] += r["c"] or 0
        if r["code"] in EM_NEW or r["code"] in EM_EST:
            codes[r["provider_id"]][r["code"]] += r["n"]
    mix: dict[str, dict] = defaultdict(dict)
    for r in conn.execute("SELECT provider_id, payer_kind, SUM(encounters) n, SUM(allowed_cents) a, SUM(seen) seen, SUM(biopsied) bx FROM provider_payers "
                          "WHERE month BETWEEN ? AND ? GROUP BY 1, 2", (a, b)):
        mix[r["provider_id"]][r["payer_kind"]] = {"encounters": r["n"], "allowed_cents": r["a"], "seen": r["seen"], "biopsied": r["bx"]}
    down: dict[str, dict] = {}
    try:
        marks = ",".join("?" * len(DOWNCODE))
        for r in conn.execute(f"""SELECT rendering_provider_npi npi, COUNT(*) n, COALESCE(SUM(shortfall_cents),0) cents FROM findings
                                  WHERE signal IN ({marks}) AND billed_code LIKE '992%' AND COALESCE(stale,0)=0 AND status != 'dismissed'
                                    AND service_date BETWEEN ? AND ? GROUP BY 1""", DOWNCODE + (d0, d1)):
            down[r["npi"] or ""] = {"n": r["n"], "cents": r["cents"]}
    except sqlite3.Error:
        pass
    panels = {r["provider_id"]: dict(r) for r in conn.execute(
        "SELECT * FROM provider_panels WHERE month = (SELECT MAX(month) FROM provider_panels WHERE month <= ?)", (b,))}
    horizon = cov.get("last_complete_month") or b
    came_back: dict[str, list] = defaultdict(lambda: [0, 0, 0, 0, 0, 0, 0, 0])
    for r in conn.execute("SELECT * FROM provider_cohorts"):
        q_end = _add_months(f"{r['quarter'][:4]}-{int(r['quarter'][6]) * 3:02d}", 0)
        x = came_back[r["provider_id"]]
        if _add_months(q_end, 4) <= horizon and _add_months(q_end, 4) > _add_months(horizon, -24):       # 120 days of follow-up
            x[6] += r["biopsied"] or 0
            x[7] += r["treated_120"] or 0
        if _add_months(q_end, 12) <= horizon and _add_months(q_end, 12) > _add_months(horizon, -24):     # followed a full year, recent
            x[0] += r["new_patients"]
            x[1] += r["back_365"]
            x[2] += r["back_same"]
            x[3] += r["return_visits"]
        if _add_months(q_end, 3) <= horizon and _add_months(q_end, 3) > _add_months(horizon, -12):
            x[4] += r["new_patients"]
            x[5] += r["back_90"]
    recall = {r["provider_id"]: dict(r) for r in conn.execute("SELECT * FROM provider_recall")}
    weeks = _weeks(a, b)
    out, other = [], []
    for pid, p in per.items():
        s, who = p["sum"], roster.get(pid, {"name": pid, "kind": "person", "tier": "unknown"})
        clinic = [x for x in p["days"] if x >= CLINIC_DAY_MIN]
        c = codes.get(pid, Counter())
        est_n = sum(c[k] for k in EM_EST)
        new_n = sum(c[k] for k in EM_NEW)
        row = {
            "provider_id": pid, "name": who.get("name"), "kind": who.get("kind"), "tier": who.get("tier"), "role": who.get("role"),
            "days_any": sum(1 for x in p["days"] if x > 0), "clinic_days": len(clinic),
            "days_per_week": round(len(clinic) / weeks, 2) if weeks else None,
            "patients": s["patients"], "per_day": {"median": _quantile(clinic, 0.5), "p25": _quantile(clinic, 0.25), "p75": _quantile(clinic, 0.75),
                                                   "mean": round(sum(clinic) / len(clinic), 1) if clinic else None, "max": max(clinic) if clinic else None},
            "visits": s["visits"], "new_visits": s["new_visits"], "est_visits": s["est_visits"], "proc_only": s["proc_only"],
            "new_share": _share(s["new_visits"], s["visits"]), "new_per_day": round(s["clinic_new"] / len(clinic), 2) if clinic else None,
            "path_only": s["path_only"], "radiation": s["radiation"],
            "biopsy_lesions": round(s["biopsy_lesions"], 1), "biopsy_patients": s["biopsy_patients"],
            "biopsied_per_100_patients": round(100 * s["biopsy_patients"] / s["patients"], 1) if s["patients"] else None,
            "lesions_per_biopsied_patient": round(s["biopsy_lesions"] / s["biopsy_patients"], 2) if s["biopsy_patients"] else None,
            "lesions_per_clinic_day": round(s["biopsy_lesions"] / len(clinic), 1) if clinic else None,
            "levels": {k: c[k] for k in EM_NEW + EM_EST if c[k]},
            "est_high_share": _share(c["99214"] + c["99215"], est_n), "new_high_share": _share(c["99204"] + c["99205"], new_n),
            "charge_cents": s["charge_cents"], "allowed_cents": s["allowed_cents"], "paid_cents": s["paid_cents"],
            "clinic_allowed_cents": s["clinic_allowed_cents"], "path_generated_cents": s["path_generated_cents"],
            "product_allowed_cents": sum(v[2] for f, v in fam.get(pid, {}).items() if f in PRODUCT),
            "allowed_per_patient": round(s["clinic_allowed_cents"] / s["patients"]) if s["patients"] else None,
            "path_generated_per_patient": round(s["path_generated_cents"] / s["patients"]) if s["patients"] else None,
            "allowed_per_clinic_day": round(s["clinic_day_allowed"] / len(clinic)) if clinic else None,
            "allowed_share_of_charge": _share(s["allowed_cents"], s["charge_cents"]),
            "families": [{"family": f, "label": FAMILY_LABEL.get(f, f), "items": v[0], "units": round(v[1], 1), "allowed_cents": v[2], "charge_cents": v[3]}
                         for f, v in sorted(fam.get(pid, {}).items(), key=lambda kv: -kv[1][2])],
            "payers": mix.get(pid, {}),
            "medicare_share": _share(sum(v["seen"] or 0 for k, v in mix.get(pid, {}).items() if k in ("medicare", "medicare_advantage")),
                                     sum(v["seen"] or 0 for v in mix.get(pid, {}).values())),
            "biopsied_per_100_medicare": _rate(mix.get(pid, {}), ("medicare", "medicare_advantage")),
            "biopsied_per_100_other": _rate(mix.get(pid, {}), None, ("medicare", "medicare_advantage")),
            "panel": (panels.get(pid) or {}).get("panel"), "panel_month": (panels.get(pid) or {}).get("month"),
            "kept_share": _share((panels.get(pid) or {}).get("kept"), (panels.get(pid) or {}).get("prior_panel"))
            if ((panels.get(pid) or {}).get("prior_panel") or 0) >= 100 else None,
            "new_back_365": _share(came_back[pid][1], came_back[pid][0]) if came_back[pid][0] >= 50 else None,
            "new_back_90": _share(came_back[pid][5], came_back[pid][4]) if came_back[pid][4] >= 50 else None,
            "return_visits_per_new": round(came_back[pid][3] / came_back[pid][0], 2) if came_back[pid][0] >= 50 else None,
            "cancer_per_100_biopsied": round(100 * came_back[pid][7] / came_back[pid][6], 1) if came_back[pid][6] >= 100 else None,
            "recall": {k: (recall.get(pid) or {}).get(k) for k in ("cancer_patients", "overdue_12", "overdue_18")},
            "downcoded": down.get(pid, {"n": 0, "cents": 0}),
            "downcoded_per_100_visits": round(100 * down.get(pid, {"n": 0})["n"] / (est_n + new_n), 2) if (est_n + new_n) else None,
        }
        (out if who.get("kind") == "person" else other).append(row)
    out.sort(key=lambda r: (-(r["patients"] or 0), r["name"] or ""))
    seen_total = sum(r["patients"] for r in out) + sum(r["patients"] for r in other)
    months_in = [m for m in cov["months"] if a <= m["month"] <= b]
    return {"from": a, "to": b, "weeks": round(weeks, 1), "coverage": cov, "providers": out, "unassigned": other,
            "unassigned_share": _share(sum(r["patients"] for r in other), seen_total),
            "incomplete_months": [m for m in months_in if m["complete"] < COMPLETE_SHARE],
            "clinic_day_min": CLINIC_DAY_MIN, "families": [{"family": k, "label": v} for k, v in FAMILIES],
            "recall": {"as_of": next((r["as_of"] for r in recall.values()), None), "cancer_patients": sum(r["cancer_patients"] for r in recall.values()),
                       "overdue_12": sum(r["overdue_12"] for r in recall.values()), "overdue_18": sum(r["overdue_18"] for r in recall.values()),
                       "unattributed": {k: (recall.get("unattributed") or {}).get(k, 0) for k in ("cancer_patients", "overdue_12", "overdue_18")}},
            "attribution": info(conn).get("attribution") or {}}


def _period(month: str, by: str) -> str:
    return month if by == "month" else f"{month[:4]}-Q{(int(month[5:7]) + 2) // 3}"


def trend(conn: sqlite3.Connection, by: str = "quarter") -> dict:
    """Every clinician by quarter (or month) of service: clinic days, patients per clinic day, new patients, levels, allowed."""
    conn.row_factory = sqlite3.Row
    by = "month" if by == "month" else "quarter"
    cov = coverage(conn)
    if not cov["first_month"]:
        return {"by": by, "periods": [], "providers": [], "coverage": cov}
    roster = _roster(conn)
    d0, d1 = f"{cov['first_month']}-01", f"{cov['last_month']}-31"
    cell: dict[tuple, dict] = {}
    for r in conn.execute("SELECT * FROM provider_days WHERE service_date BETWEEN ? AND ?", (d0, d1)):
        c = cell.setdefault((r["provider_id"], _period(r["service_date"][:7], by)), {"days": [], "s": Counter()})
        c["days"].append(r["patients"])
        for k in ("patients", "visits", "new_visits", "biopsy_patients", "biopsy_lesions", "allowed_cents", "clinic_allowed_cents", "path_only",
                  "radiation", "path_generated_cents"):
            c["s"][k] += r[k] or 0
    lv: dict[tuple, Counter] = defaultdict(Counter)
    for r in conn.execute("SELECT provider_id, month, code, SUM(items) n FROM provider_services WHERE code LIKE '992%' AND month BETWEEN ? AND ? GROUP BY 1, 2, 3",
                          (cov["first_month"], cov["last_month"])):
        lv[(r["provider_id"], _period(r["month"], by))][r["code"]] += r["n"]
    done: dict[str, float] = {}
    count: Counter = Counter()
    for m in cov["months"]:
        p = _period(m["month"], by)
        done[p] = done.get(p, 0.0) + m["complete"]
        count[p] += 1
    periods = [{"period": p, "complete": round(done[p] / count[p], 3)} for p in sorted(done)]
    providers = []
    for pid in sorted({k[0] for k in cell}):
        who = roster.get(pid, {"name": pid, "kind": "person", "tier": "unknown"})
        if who.get("kind") != "person":
            continue
        series, total = [], 0
        for p in periods:
            c = cell.get((pid, p["period"]))
            if not c:
                series.append(None)
                continue
            clinic = [x for x in c["days"] if x >= CLINIC_DAY_MIN]
            s, codes = c["s"], lv.get((pid, p["period"]), Counter())
            est_n, new_n = sum(codes[k] for k in EM_EST), sum(codes[k] for k in EM_NEW)
            total += s["patients"]
            series.append({"clinic_days": len(clinic), "patients": s["patients"], "per_day": _quantile(clinic, 0.5),
                           "per_day_mean": round(sum(clinic) / len(clinic), 1) if clinic else None, "new_visits": s["new_visits"],
                           "est_high_share": _share(codes["99214"] + codes["99215"], est_n) if est_n >= 30 else None,
                           "new_high_share": _share(codes["99204"] + codes["99205"], new_n) if new_n >= 30 else None,
                           "biopsied_per_100_patients": round(100 * s["biopsy_patients"] / s["patients"], 1) if s["patients"] >= 30 else None,
                           "allowed_cents": s["allowed_cents"], "clinic_allowed_cents": s["clinic_allowed_cents"],
                           "allowed_per_patient": round(s["clinic_allowed_cents"] / s["patients"]) if s["patients"] else None})
        providers.append({"provider_id": pid, "name": who.get("name"), "tier": who.get("tier"), "patients": total, "series": series})
    providers.sort(key=lambda r: -r["patients"])
    return {"by": by, "periods": periods, "providers": providers, "coverage": cov, "clinic_day_min": CLINIC_DAY_MIN}


def detail(conn: sqlite3.Connection, provider_id: str, month_from: str | None = None, month_to: str | None = None) -> dict | None:
    """One clinician: how the days are distributed, which weekdays, which codes, which payers."""
    conn.row_factory = sqlite3.Row
    who = _roster(conn).get(provider_id)
    if not who:
        return None
    cov, a, b = _window(conn, month_from, month_to)
    d0, d1 = f"{a}-01", f"{b}-31"
    hist: Counter = Counter()
    weekday = [[0, 0] for _ in range(7)]
    monthly: dict[str, dict] = {}
    for r in conn.execute("SELECT * FROM provider_days WHERE provider_id=? AND service_date BETWEEN ? AND ? ORDER BY service_date", (provider_id, d0, d1)):
        m = monthly.setdefault(r["service_date"][:7], {"days": [], "patients": 0, "new_visits": 0, "allowed_cents": 0, "clinic_allowed_cents": 0})
        m["days"].append(r["patients"])
        m["patients"] += r["patients"]
        m["new_visits"] += r["new_visits"]
        m["allowed_cents"] += r["allowed_cents"] or 0
        m["clinic_allowed_cents"] += r["clinic_allowed_cents"] or 0
        if r["patients"] >= CLINIC_DAY_MIN:
            hist[min(r["patients"] // 5 * 5, 60)] += 1
            try:
                wd = date.fromisoformat(r["service_date"]).weekday()
                weekday[wd][0] += 1
                weekday[wd][1] += r["patients"]
            except ValueError:
                pass
    months = []
    for m in sorted(monthly):
        v = monthly[m]
        clinic = [x for x in v["days"] if x >= CLINIC_DAY_MIN]
        months.append({"month": m, "clinic_days": len(clinic), "patients": v["patients"], "per_day": _quantile(clinic, 0.5), "new_visits": v["new_visits"],
                       "allowed_cents": v["allowed_cents"], "clinic_allowed_cents": v["clinic_allowed_cents"]})
    codes = [dict(r) for r in conn.execute(
        """SELECT code, family, SUM(items) items, SUM(units) units, SUM(denied) denied, SUM(charge_cents) charge_cents, SUM(allowed_cents) allowed_cents,
                  SUM(paid_cents) paid_cents FROM provider_services WHERE provider_id=? AND month BETWEEN ? AND ? GROUP BY 1, 2 ORDER BY 7 DESC LIMIT 40""",
        (provider_id, a, b))]
    payer = [dict(r) for r in conn.execute(
        "SELECT payer_kind, SUM(encounters) encounters, SUM(allowed_cents) allowed_cents, SUM(paid_cents) paid_cents FROM provider_payers "
        "WHERE provider_id=? AND month BETWEEN ? AND ? GROUP BY 1 ORDER BY 2 DESC", (provider_id, a, b))]
    names = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    return {"provider": who, "from": a, "to": b, "months": months, "coverage": cov,
            "histogram": [{"from": k, "days": hist[k]} for k in sorted(hist)],
            "weekdays": [{"day": names[i], "clinic_days": w[0], "per_day": round(w[1] / w[0], 1) if w[0] else None} for i, w in enumerate(weekday)],
            "codes": codes, "payers": payer, "clinic_day_min": CLINIC_DAY_MIN}


def _short_names(names: set) -> dict:
    """'Northside Dermatology Associates' -> 'Northside' when several offices end in the same words."""
    tails = Counter(" ".join(n.split()[-2:]) for n in names if len(n.split()) > 2)
    return {n: (" ".join(n.split()[:-2]) if len(n.split()) > 2 and tails[" ".join(n.split()[-2:])] >= 2 else n) for n in names}


def _offices(rows: list, short: dict) -> list:
    """Where a clinician worked, by share of patients seen (each day counts at the office most of its charges name)."""
    n: Counter = Counter()
    for r in rows:
        if r["location"]:
            n[r["location"]] += r["patients"]
    total = sum(n.values())
    return [{"name": short.get(k, k), "share": round(v / total, 3)} for k, v in sorted(n.items(), key=lambda kv: (-kv[1], kv[0])) if v / total >= 0.05][:3] if total else []


def recent(conn: sqlite3.Connection) -> dict:
    """The newest weeks, from the EMA charge exports: every visit (self-pay included), and how long each clinician takes
    to turn a visit into a bill. A day counts only when an export that covers it was pulled late enough for that
    clinician's bills to exist; the days still waiting are reported apart instead of being read as light days."""
    conn.row_factory = sqlite3.Row
    meta = (info(conn).get("ema") or {})
    if not meta.get("provider_days"):
        return {"ema": meta, "providers": [], "clinic_day_min": CLINIC_DAY_MIN}
    lags: dict[str, dict] = defaultdict(lambda: {"all": Counter(), "mature": Counter()})
    for r in conn.execute("SELECT provider, lag_days, bills, mature FROM provider_ema_lag"):
        lags[r["provider"]]["all"][r["lag_days"]] += r["bills"]
        if r["mature"]:
            lags[r["provider"]]["mature"][r["lag_days"]] += r["bills"]

    def q(cnt: Counter, p: float) -> int | None:
        tot, acc = sum(cnt.values()), 0
        for k in sorted(cnt):
            acc += cnt[k]
            if acc >= p * tot:
                return k
        return None
    by: dict[str, dict] = {}
    for r in conn.execute("SELECT * FROM provider_ema_days ORDER BY provider, service_date"):
        p = by.setdefault(r["provider"], {"provider_id": r["provider_id"], "role": None, "rows": []})
        p["role"] = p["role"] or r["role"]
        p["rows"].append(dict(r))
    remit: dict[tuple, int] = {}
    for r in conn.execute("SELECT provider_id, service_date, patients FROM provider_days WHERE service_date >= ?", (meta.get("first_service") or "",)):
        remit[(r["provider_id"], r["service_date"])] = r["patients"]
    everyone = Counter()
    for v in lags.values():
        everyone.update(v["mature"] if sum(v["mature"].values()) >= 40 else v["all"])
    short = _short_names({r["location"] for p in by.values() for r in p["rows"] if r["location"]})
    out = []
    for name, p in by.items():
        worked = [r for r in p["rows"] if r["patients"] > 0]
        if sum(r["patients"] for r in worked) < 10:      # a stray charge under a name is not a clinician's week
            continue
        lag = lags.get(name, {"all": Counter(), "mature": Counter()})
        use = lag["mature"] if sum(lag["mature"].values()) >= 40 else lag["all"]
        known = sum(use.values()) >= 20
        p90 = q(use, 0.9) if known else q(everyone, 0.9)
        hold = max(p90 if p90 is not None else 14, 3)
        done = lambda r: bool(r["pulled"]) and (_days(r["service_date"], r["pulled"]) or 0) >= hold      # noqa: E731
        full = [r for r in worked if done(r) and r["patients"] >= CLINIC_DAY_MIN]
        waiting = [r for r in worked if not done(r)]
        if not full and _tier(p["role"]) == "staff":         # a billing bucket or a few nurse visits, with no day to show
            continue
        n = len(full)
        tot = lambda k: sum((r[k] or 0) for r in full)       # noqa: E731
        est = tot("est_visits")
        out.append({
            "provider": name, "provider_id": p["provider_id"], "role": p["role"], "tier": _tier(p["role"]),
            "first_day": worked[0]["service_date"], "last_day": worked[-1]["service_date"],
            "clinic_days": n, "days_waiting": len(waiting), "hold_days": hold,
            "patients_per_day": round(tot("patients") / n, 1) if n else None,
            "insured_per_day": round(tot("insured_patients") / n, 1) if n else None,
            "insured_share": _share(tot("insured_patients"), tot("patients")),
            "charges_per_day_cents": round(tot("clinic_charges_cents") / n) if n else None,
            "new_per_day": round(tot("new_visits") / n, 1) if n else None,
            "est_high_share": _share(tot("est_high"), est) if est >= 30 else None,
            "lesions_per_day": round(tot("biopsy_lesions") / n, 1) if n else None,
            "raw_patients_per_day": round(sum(r["patients"] for r in worked) / len(worked), 1),
            "raw_days": len(worked),
            "lag": {"median": q(use, 0.5) if known else None, "p75": q(use, 0.75) if known else None, "p90": q(use, 0.9) if known else None,
                    "bills": sum(use.values()),
                    "basis": "visits at least three weeks before the export was pulled" if use is lag["mature"] else "every bill in the exports"},
            "remit_patients_per_day": round(sum(remit.get((p["provider_id"], r["service_date"]), 0) for r in full) / n, 1) if n else None,
            "offices": _offices(worked, short),
        })
    out.sort(key=lambda r: (-(r["patients_per_day"] or 0) * (r["clinic_days"] or 0), r["provider"]))
    return {"ema": meta, "providers": out, "clinic_day_min": CLINIC_DAY_MIN}
