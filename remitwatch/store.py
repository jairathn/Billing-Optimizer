"""
SQLite store for parsed 835 remittances and ModMed EMA exports.

One file, no server. Every row keeps its source file hash so a weekly run can
re-ingest a folder idempotently: files already seen are skipped by sha256, and
835 transactions already seen (same ST..SE content delivered under a new file
name) are skipped by content fingerprint.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from typing import Iterable, Iterator

from .x12_835 import Parsed835, iter_835_paths, parse_835_file

SCHEMA_VERSION = 3

JSON_FIELDS = {
    "transactions": ("payee_other_ids", "payer_other_ids"),
    "claims": ("other_refs", "other_dates", "other_amounts", "quantities"),
    "lines": ("modifiers", "original_modifiers", "other_dates", "other_refs", "other_amounts", "quantities"),
}

DDL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS files (
    sha256 TEXT PRIMARY KEY,
    source_path TEXT, source_name TEXT, size INTEGER, mtime TEXT, kind TEXT,
    ingested_at TEXT, transactions INTEGER, claims INTEGER, lines INTEGER,
    duplicates INTEGER, problems INTEGER, status TEXT
);

CREATE TABLE IF NOT EXISTS transactions (
    transaction_key TEXT PRIMARY KEY,
    content_fingerprint TEXT, file_sha256 TEXT, source_name TEXT, envelope_index INTEGER,
    st_type TEXT, st_control TEXT,
    isa_sender_qual TEXT, isa_sender TEXT, isa_receiver_qual TEXT, isa_receiver TEXT,
    isa_date TEXT, isa_time TEXT, isa_version TEXT, isa_control TEXT, isa_usage TEXT,
    gs_functional_id TEXT, gs_sender TEXT, gs_receiver TEXT, gs_date TEXT, gs_time TEXT,
    gs_control TEXT, gs_version TEXT,
    payment_amount_cents INTEGER, credit_debit TEXT, payment_method TEXT, payment_format TEXT,
    payment_date TEXT, payer_bank_routing TEXT, payer_account TEXT, payee_bank_routing TEXT,
    payee_account TEXT, trace_type TEXT, trace_number TEXT, trace_payer_id TEXT,
    trace_originating_company TEXT, receiver_id TEXT, version_id TEXT, production_date TEXT,
    currency TEXT,
    payer_name TEXT, payer_id TEXT, payer_address TEXT, payer_city TEXT, payer_state TEXT,
    payer_zip TEXT, payer_submitter_id TEXT, payer_contact TEXT, payer_contact_phone TEXT,
    payer_contact_email TEXT, payer_contact_url TEXT, payer_other_ids TEXT,
    payee_name TEXT, payee_npi TEXT, payee_id_qual TEXT, payee_id TEXT, payee_tin TEXT,
    payee_address TEXT, payee_city TEXT, payee_state TEXT, payee_zip TEXT, payee_other_ids TEXT,
    segment_count INTEGER, claim_count INTEGER, claim_paid_total_cents INTEGER,
    plb_total_cents INTEGER, balanced INTEGER, superseded INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_txn_fp ON transactions(content_fingerprint);
CREATE INDEX IF NOT EXISTS ix_txn_trace ON transactions(trace_number, payer_id);
CREATE INDEX IF NOT EXISTS ix_txn_paydate ON transactions(payment_date);
CREATE INDEX IF NOT EXISTS ix_txn_payer ON transactions(payer_name);

CREATE TABLE IF NOT EXISTS claims (
    claim_key TEXT PRIMARY KEY,
    transaction_key TEXT, file_sha256 TEXT, claim_seq INTEGER, lx_number TEXT,
    patient_control_number TEXT, claim_status_code TEXT,
    charge_cents INTEGER, paid_cents INTEGER, patient_responsibility_cents INTEGER,
    claim_filing_indicator TEXT, payer_claim_control_number TEXT, facility_type_code TEXT,
    claim_frequency_code TEXT, drg_code TEXT, drg_weight REAL,
    patient_last TEXT, patient_first TEXT, patient_middle TEXT, patient_id_qual TEXT, patient_id TEXT,
    insured_last TEXT, insured_first TEXT, insured_id_qual TEXT, insured_id TEXT,
    corrected_patient_last TEXT, corrected_patient_first TEXT,
    rendering_provider_name TEXT, rendering_provider_last TEXT, rendering_provider_first TEXT,
    rendering_provider_npi TEXT, rendering_provider_id_qual TEXT, rendering_provider_id TEXT,
    crossover_carrier_name TEXT, crossover_carrier_id TEXT, corrected_payer_name TEXT, corrected_payer_id TEXT,
    group_number TEXT, contract_class TEXT, original_reference_number TEXT, medical_record_number TEXT,
    member_id_ref TEXT, authorization_number TEXT, prior_authorization TEXT, other_refs TEXT,
    received_date TEXT, statement_from TEXT, statement_to TEXT, coverage_expiration TEXT, other_dates TEXT,
    coverage_amount_cents INTEGER, other_amounts TEXT, quantities TEXT,
    moa_reimbursement_rate REAL, moa_hcpcs_payable_cents INTEGER, moa_esrd_cents INTEGER,
    moa_nonpayable_professional_cents INTEGER, payer_claim_contact_phone TEXT,
    payer_name TEXT, payer_id TEXT, payee_npi TEXT, payment_date TEXT, trace_number TEXT,
    line_count INTEGER, line_charge_total_cents INTEGER, line_paid_total_cents INTEGER,
    claim_adjustment_total_cents INTEGER, line_adjustment_total_cents INTEGER, balanced INTEGER, claim_paid_minus_lines_cents INTEGER
);
CREATE INDEX IF NOT EXISTS ix_claims_txn ON claims(transaction_key);
CREATE INDEX IF NOT EXISTS ix_claims_pcn ON claims(patient_control_number);
CREATE INDEX IF NOT EXISTS ix_claims_payer ON claims(payer_name);
CREATE INDEX IF NOT EXISTS ix_claims_paydate ON claims(payment_date);
CREATE INDEX IF NOT EXISTS ix_claims_status ON claims(claim_status_code);
CREATE INDEX IF NOT EXISTS ix_claims_npi ON claims(rendering_provider_npi);

CREATE TABLE IF NOT EXISTS lines (
    line_key TEXT PRIMARY KEY,
    claim_key TEXT, transaction_key TEXT, file_sha256 TEXT, line_seq INTEGER,
    procedure_qualifier TEXT, procedure_code TEXT, modifiers TEXT, modifier_1 TEXT, modifier_2 TEXT,
    procedure_description TEXT, charge_cents INTEGER, paid_cents INTEGER, revenue_code TEXT,
    paid_units REAL, original_procedure_qualifier TEXT, original_procedure_code TEXT,
    original_modifiers TEXT, original_units REAL, code_changed INTEGER, modifiers_changed INTEGER,
    service_date TEXT, service_period_start TEXT, service_period_end TEXT, other_dates TEXT,
    line_control_number TEXT, location_number TEXT, other_refs TEXT,
    allowed_cents INTEGER, other_amounts TEXT, quantities TEXT,
    adjustment_total_cents INTEGER, balanced INTEGER,
    patient_control_number TEXT, claim_status_code TEXT, payer_name TEXT, payer_id TEXT,
    payment_date TEXT, rendering_provider_npi TEXT
);
CREATE INDEX IF NOT EXISTS ix_lines_claim ON lines(claim_key);
CREATE INDEX IF NOT EXISTS ix_lines_txn ON lines(transaction_key);
CREATE INDEX IF NOT EXISTS ix_lines_pcn ON lines(patient_control_number);
CREATE INDEX IF NOT EXISTS ix_lines_proc ON lines(procedure_code);
CREATE INDEX IF NOT EXISTS ix_lines_svc_date ON lines(service_date);
CREATE INDEX IF NOT EXISTS ix_lines_payer ON lines(payer_name);
CREATE INDEX IF NOT EXISTS ix_lines_orig ON lines(original_procedure_code);

CREATE TABLE IF NOT EXISTS adjustments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_key TEXT, claim_key TEXT, line_key TEXT, level TEXT,
    group_code TEXT, reason_code TEXT, amount_cents INTEGER, quantity REAL, cas_index INTEGER
);
CREATE INDEX IF NOT EXISTS ix_adj_line ON adjustments(line_key);
CREATE INDEX IF NOT EXISTS ix_adj_claim ON adjustments(claim_key);
CREATE INDEX IF NOT EXISTS ix_adj_reason ON adjustments(reason_code);
CREATE INDEX IF NOT EXISTS ix_adj_txn ON adjustments(transaction_key);

CREATE TABLE IF NOT EXISTS remarks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_key TEXT, claim_key TEXT, line_key TEXT, level TEXT, qualifier TEXT, code TEXT
);
CREATE INDEX IF NOT EXISTS ix_rem_line ON remarks(line_key);
CREATE INDEX IF NOT EXISTS ix_rem_claim ON remarks(claim_key);
CREATE INDEX IF NOT EXISTS ix_rem_code ON remarks(code);

CREATE TABLE IF NOT EXISTS plb (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_key TEXT, file_sha256 TEXT, provider_id TEXT, fiscal_period_date TEXT,
    reason_code TEXT, reference TEXT, amount_cents INTEGER,
    payer_name TEXT, payer_id TEXT, payment_date TEXT, trace_number TEXT
);
CREATE INDEX IF NOT EXISTS ix_plb_txn ON plb(transaction_key);
CREATE INDEX IF NOT EXISTS ix_plb_reason ON plb(reason_code);

CREATE TABLE IF NOT EXISTS problems (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_sha256 TEXT, source TEXT, level TEXT, message TEXT, segment TEXT, transaction_ref TEXT, claim_ref TEXT
);

-- ModMed EMA exports (see ema_exports.py); one row per source row, deduplicated by row fingerprint
CREATE TABLE IF NOT EXISTS ema_rows (
    row_fingerprint TEXT PRIMARY KEY,
    family TEXT, source_sha256 TEXT, source_name TEXT, row_index INTEGER,
    service_date TEXT, transaction_date TEXT, posted_date TEXT, bill_created TEXT,
    provider TEXT, provider_role TEXT, referring_provider TEXT, patient_id TEXT,
    bill_id TEXT, claim_id TEXT, bill_item_id TEXT, auth_number TEXT,
    cpt TEXT, modifiers TEXT, modifier_1 TEXT, modifier_2 TEXT, icd10 TEXT, icd10_desc TEXT,
    ndc_code TEXT, ndc_quantity TEXT, ndc_unit TEXT, ndc_desc TEXT,
    payer TEXT, payer_id TEXT, policy_number TEXT, group_number TEXT, coverage_type TEXT, claims_state TEXT,
    claim_status TEXT, claim_action TEXT, claim_submission_date TEXT, denial_date TEXT,
    location TEXT, business_unit TEXT,
    charges_cents INTEGER, payments_cents INTEGER, adjustments_cents INTEGER,
    insurance_payments_cents INTEGER, insurance_adjustments_cents INTEGER,
    contractual_adjustments_cents INTEGER, noncontractual_adjustments_cents INTEGER,
    patient_adjustments_cents INTEGER, allowed_cents INTEGER, patient_charges_cents INTEGER,
    patient_payments_cents INTEGER, denial_amount_cents INTEGER, rejected_amount_cents INTEGER,
    units REAL, activity_type TEXT, adjustment_reason TEXT, payment_method TEXT, batch_name TEXT,
    carc TEXT, carc_group TEXT, carc_code TEXT, carc_desc TEXT, rarc TEXT, rarc_desc TEXT,
    recreated_bill TEXT, adjustment_status TEXT, adjustment_subtype TEXT,
    denial_category TEXT, previously_denied TEXT, submission_format TEXT
);
-- one row per export file: when the workbook says it was created is when the export was pulled
CREATE TABLE IF NOT EXISTS ema_sources (
    source_name TEXT PRIMARY KEY, sha256 TEXT, family TEXT, created_at TEXT, rows INTEGER, recorded_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_ema_family ON ema_rows(family);
CREATE INDEX IF NOT EXISTS ix_ema_claim ON ema_rows(claim_id);
CREATE INDEX IF NOT EXISTS ix_ema_bill_item ON ema_rows(bill_item_id);
CREATE INDEX IF NOT EXISTS ix_ema_svc ON ema_rows(service_date);
CREATE INDEX IF NOT EXISTS ix_ema_cpt ON ema_rows(cpt);
CREATE INDEX IF NOT EXISTS ix_ema_activity ON ema_rows(activity_type);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Kept even in a compact store. A few rules ask, for each claim, whether it has service lines or whether the payer
# later reversed it; without these two each question scans a whole table, and on the full archive that took longer than
# a function may run. Together about 4% of the store.
COMPACT_INDEXES = ("ix_lines_claim", "ix_claims_pcn")


def ddl(compact: bool = False) -> str:
    """The schema. A compact store leaves out most secondary indexes: it is scanned whole by detection, and the indexes
    are a third of its size. The dashboard's copy (remitwatch.cloud) builds its own."""
    if not compact:
        return DDL
    def keep(ln: str) -> bool:
        s = ln.lstrip().upper()
        if s.startswith("PRAGMA JOURNAL_MODE"):
            return False
        return not s.startswith("CREATE INDEX") or any(f" {ix.upper()} " in s for ix in COMPACT_INDEXES)
    return "\n".join(ln for ln in DDL.splitlines() if keep(ln))


def connect(db_path: str, compact: bool = False) -> sqlite3.Connection:
    if db_path != ":memory:":
        os.makedirs(os.path.dirname(os.path.abspath(db_path)) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=60, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(ddl(compact))
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
    conn.execute("UPDATE transactions SET superseded=0 WHERE superseded IS NULL")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(claims)")}
    if "claim_paid_minus_lines_cents" not in cols:
        conn.execute("ALTER TABLE claims ADD COLUMN claim_paid_minus_lines_cents INTEGER")
    conn.commit()
    return conn


def _row(d: dict, table: str, columns: Iterable[str]) -> tuple:
    js = JSON_FIELDS.get(table, ())
    out = []
    for c in columns:
        v = d.get(c)
        if c in js:
            v = json.dumps(v, default=str) if v not in (None, {}, []) else None
        elif isinstance(v, bool):
            v = int(v)
        elif isinstance(v, (dict, list)):
            v = json.dumps(v, default=str)
        out.append(v)
    return tuple(out)


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


# The server's store file has a marker beside it (<store>.marker), written when the store is first made. While the marker
# is there, a store file that is missing (a volume not mounted, a file moved or renamed), or one that holds no remittance
# where the store did, stops every run: nothing makes a new empty store and saves it as the practice's. The marker shares
# the store's folder, so a whole new empty volume is a first start again (the disk's snapshots cover that: SERVER_RUNBOOK).
STORE_MISSING = ("The store file is missing. Nothing was changed. Put it back (the backups folder beside it keeps a copy a day), "
                 "or mount the data volume again: docs/OPERATIONS.md, Recovering the store.")
STORE_EMPTIED = ("The store file holds no remittance, but the store here did. Nothing was changed. Put the store back from the "
                 "backups folder beside it: docs/OPERATIONS.md, Recovering the store.")


def _marker(db_path: str) -> str:
    return os.path.abspath(db_path) + ".marker"


def _read_marker(db_path: str) -> dict | None:
    """The marker's content; None when there is no marker. One that cannot be read is a marker all the same."""
    try:
        with open(_marker(db_path), encoding="utf-8") as fh:
            m = json.load(fh)
        return m if isinstance(m, dict) else {}
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {}


def _holds_remittances(db_path: str) -> bool:
    """Whether the file at db_path is a store holding at least one remittance. Opened read-only: asking never makes a file."""
    from urllib.request import pathname2url
    if not os.path.exists(db_path) or os.path.getsize(db_path) == 0:
        return False
    try:
        conn = sqlite3.connect(f"file:{pathname2url(os.path.abspath(db_path))}?mode=ro", uri=True, timeout=30)
        try:
            return conn.execute("SELECT 1 FROM transactions LIMIT 1").fetchone() is not None
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def store_problem(db_path: str) -> str | None:
    """Why the server's store must not be opened now, in words for a person, or None when it may be: its marker says a
    store was made here, and the file is missing or empty, or holds no remittance though the store did. Opening it would
    make a new empty store, which the next run would fill from the feeds' current position and save as the practice's."""
    if not db_path or db_path == ":memory:":
        return None
    marker = _read_marker(db_path)
    if marker is None:
        return None
    if not os.path.exists(db_path) or os.path.getsize(db_path) == 0:
        return STORE_MISSING
    if marker.get("held_data") and not _holds_remittances(db_path):
        return STORE_EMPTIED
    return None


def _write_marker(db_path: str, body: dict) -> None:
    tmp = _marker(db_path) + ".part"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(body, fh)
    os.replace(tmp, _marker(db_path))


def mark_store(db_path: str) -> None:
    """Write the marker beside the server's store where there is none (the store's first making, or a store from before
    the marker), and note it once the store holds a remittance."""
    if not db_path or db_path == ":memory:" or not os.path.exists(db_path):
        return
    marker = _read_marker(db_path)
    held = bool((marker or {}).get("held_data")) or _holds_remittances(db_path)
    if marker is not None and bool(marker.get("held_data")) == held:
        return
    _write_marker(db_path, {"store": os.path.basename(db_path), "created": (marker or {}).get("created") or utcnow(), "held_data": held})


def accept_store(db_path: str) -> str:
    """A person's decision after a stop (docs/OPERATIONS.md, Recovering the store): the store file in place now is the
    store from here on, or, where there is none (or it holds nothing), the next run starts a new, empty store. Returns
    what was done, in words."""
    if not os.path.exists(db_path) or os.path.getsize(db_path) == 0 or not _holds_remittances(db_path):
        if os.path.exists(_marker(db_path)):
            os.remove(_marker(db_path))
        return ("The store holds no remittance: the next run starts a new, empty store, reads every file the feeds still hold, "
                "and analyses them. People's statuses and notes are not in it.")
    _write_marker(db_path, {"store": os.path.basename(db_path), "created": utcnow(), "held_data": True, "accepted": utcnow()})
    return "The store file in place is the store from now on."


# Which copies of a remittance stand (Store.mark_superseded). The rule's version is kept in the store: a release that
# changes the rule decides every store again once (Store.recheck_supersession), whether or not a new file arrives.
SUPERSEDE_RULE = "2"            # 2: only versions of one remittance compete; a complete, balanced one always stands
SUPERSEDE_META = "supersede:rule"
# versions of one remittance that differ (both counted), and broken copies holding claims no standing version holds (not
# counted, kind "lost"): shown on the Data tab's checks
DIFFER_META = "supersede:differ"


def _versions(members: list[dict], held: dict) -> list[list[dict]]:
    """The transactions of one payer, trace number and payment date, sorted into the remittances they are versions of
    (only groups of two or more come back). Two are versions of one remittance when their content fingerprints match,
    when they hold a claim in common (the same claim id, the practice's or the payer's, with the same charge), or when
    one is a copy cut short or that does not add up, holds no claim, and carries the other's payment amount."""
    parent = {m["transaction_key"]: m["transaction_key"] for m in members}

    def root(k: str) -> str:
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k
    own = {m["transaction_key"]: _ids(held[m["transaction_key"]]["claims"]) for m in members}
    for i, a in enumerate(members):
        for b in members[i + 1:]:
            ka, kb = a["transaction_key"], b["transaction_key"]
            same = (a["content_fingerprint"] and a["content_fingerprint"] == b["content_fingerprint"]) or bool(own[ka] & own[kb])
            for x, y in ((a, b), (b, a)):
                if not held[x["transaction_key"]]["claims"] and not _whole(x) and x["payment_amount_cents"] is not None \
                        and x["payment_amount_cents"] == y["payment_amount_cents"]:
                    same = True
            if same:
                parent[root(ka)] = root(kb)
    out: dict[str, list[dict]] = {}
    for m in members:
        out.setdefault(root(m["transaction_key"]), []).append(m)
    return [v for v in out.values() if len(v) > 1]


def _ids(claims: list[tuple]) -> set:
    """The ids the claims are known by (the practice's and the payer's), each with the claim's charge."""
    out = set()
    for pcn, icn, _st, charge, *_rest in claims:
        if pcn:
            out.add(("ours", pcn, charge))
        if icn:
            out.add(("payer", icn, charge))
    return out


def _whole(m: dict) -> bool:
    """A complete transaction (the parser saw its SE: only then is there a fingerprint) whose payment adds up."""
    return bool(m["content_fingerprint"]) and m["balanced"] == 1


def _standing(versions: list[dict], held: dict) -> tuple[list[dict], set[str]]:
    """Of the versions of one remittance, those that stand and the keys of those hidden. A complete, balanced version is
    never hidden by one that is cut short or does not add up; one whose every claim and provider adjustment another
    complete, balanced version also holds (with the same amounts) is hidden by it, the newest standing among identical
    ones. With no complete, balanced version, the complete one holding the most claims stands, then the newest."""
    from collections import Counter
    # the last key makes the choice the same whatever order the files were read in
    newest = lambda m: ((m["isa_date"] or ""), (m["isa_time"] or ""), (m["mtime"] or ""), m["transaction_key"])   # noqa: E731
    whole = [m for m in versions if _whole(m)]
    if not whole:
        best = max(versions, key=lambda m: (bool(m["content_fingerprint"]), len(held[m["transaction_key"]]["claims"]), newest(m)))
        return [best], {m["transaction_key"] for m in versions if m is not best}
    has = {m["transaction_key"]: (Counter(held[m["transaction_key"]]["claims"]), Counter(held[m["transaction_key"]]["plb"])) for m in whole}

    def covers(a: dict, b: dict) -> bool:
        (ca, pa), (cb, pb) = has[a["transaction_key"]], has[b["transaction_key"]]
        return not (cb - ca) and not (pb - pa)

    def hides(a: dict, b: dict) -> bool:
        return covers(a, b) and (not covers(b, a) or newest(a) > newest(b))
    kept = [m for m in whole if not any(o is not m and hides(o, m) for o in whole)]
    keep = {m["transaction_key"] for m in kept}
    return kept, {m["transaction_key"] for m in versions if m["transaction_key"] not in keep}


def _differ_entry(kept: list[dict], held: dict) -> dict:
    """What a person needs to find two versions of one remittance that differ (both counted): the payer, the trace number,
    the payment date, each version's payment, and the claims on more than one version with the dollars counted more than
    once (what each such claim paid beyond the largest of its versions)."""
    paid: dict[tuple, list[int]] = {}
    for m in kept:
        for pcn, _icn, _st, charge, p, *_rest in held[m["transaction_key"]]["claims"]:
            paid.setdefault((pcn, charge), []).append(p or 0)
    twice = [v for v in paid.values() if len(v) > 1]
    first = max(kept, key=lambda m: m["transaction_key"])
    return {"payer": first["payer_name"], "trace": first["trace_number"], "payment_date": first["payment_date"],
            "payments_cents": sorted(m["payment_amount_cents"] or 0 for m in kept), "claims_on_both": len(twice),
            "cents_counted_twice": sum(sum(v) - max(v) for v in twice)}


def _lost_entry(kept: list[dict], dropped: list[dict], held: dict) -> dict | None:
    """A copy hidden because it is cut short or does not add up can hold claims no standing version holds (a later version
    with a claim added, broken in transfer). Those claims are not counted, so a person is told, with what they paid; None
    when every claim of the hidden copies is on a version that stands."""
    on = _ids([c for m in kept for c in held[m["transaction_key"]]["claims"]])
    lost: dict = {}
    for m in dropped:
        for c in held[m["transaction_key"]]["claims"]:
            ids = _ids([c])
            if not ids & on:
                lost[frozenset(ids) or c] = max(lost.get(frozenset(ids) or c, 0), c[4] or 0)
    if not lost:
        return None
    first = max(kept, key=lambda m: m["transaction_key"])
    return {"kind": "lost", "payer": first["payer_name"], "trace": first["trace_number"], "payment_date": first["payment_date"],
            "payments_cents": sorted(m["payment_amount_cents"] or 0 for m in kept + dropped), "claims_not_counted": len(lost),
            "cents_not_counted": sum(lost.values())}


class Store:
    def __init__(self, db_path: str, compact: bool = False):
        self.db_path = db_path
        self.compact = compact
        self.conn = connect(db_path, compact)
        self._cols = {t: table_columns(self.conn, t) for t in
                      ("transactions", "claims", "lines", "adjustments", "remarks", "plb", "problems", "files", "ema_rows")}
        self._known_fingerprints: set[str] | None = None

    # ------------------------------------------------------------------ in-memory stores (serverless hosts have no disk)
    @classmethod
    def from_bytes(cls, data) -> "Store":
        """Open a compact in-memory store from a serialized database image."""
        store = cls(":memory:", compact=True)
        if data:
            store.conn.deserialize(data)
            store.conn.row_factory = sqlite3.Row
            store.conn.executescript(ddl(True))         # tables added since the image was written
            store.conn.execute("UPDATE transactions SET superseded=0 WHERE superseded IS NULL")
            store.conn.commit()
            store._cols = {t: table_columns(store.conn, t) for t in store._cols}
        return store

    def to_bytes(self) -> bytes:
        self.conn.commit()
        return self.conn.serialize()

    # ------------------------------------------------------------------ files
    def file_seen(self, sha256: str) -> bool:
        return self.conn.execute("SELECT 1 FROM files WHERE sha256=?", (sha256,)).fetchone() is not None

    def known_fingerprints(self) -> set[str]:
        if self._known_fingerprints is None:
            self._known_fingerprints = {r[0] for r in self.conn.execute(
                "SELECT content_fingerprint FROM transactions WHERE content_fingerprint IS NOT NULL")}
        return self._known_fingerprints

    # ------------------------------------------------------------------ 835 ingest
    def ingest_parsed(self, parsed: Parsed835, source_path: str | None = None, kind: str = "835") -> dict:
        """Insert one parsed file. Returns counts; duplicates (by fingerprint) are skipped."""
        path = source_path or parsed.source
        try:
            st = os.stat(path)
            size, mtime = st.st_size, datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(timespec="seconds")
        except OSError:
            size, mtime = None, None
        known = self.known_fingerprints()
        kept_txn_keys: set[str] = set()
        dup = 0
        for t in parsed.transactions:
            t.setdefault("superseded", 0)
            fp = t.get("content_fingerprint")
            if fp and fp in known:
                dup += 1
                continue
            kept_txn_keys.add(t["transaction_key"])
            if fp:
                known.add(fp)
        c = self.conn
        cols = self._cols
        if self.compact:        # the file hash is kept on the transaction and the file row; repeating it on every line is 40 MB
            for x in parsed.claims:
                x["file_sha256"] = None
            for x in parsed.lines:
                x["file_sha256"] = None
        ins = lambda table, rows: c.executemany(  # noqa: E731
            f"INSERT OR IGNORE INTO {table} ({','.join(cols[table])}) VALUES ({','.join('?' * len(cols[table]))})",
            [_row(r, table, cols[table]) for r in rows])
        ins("transactions", [t for t in parsed.transactions if t["transaction_key"] in kept_txn_keys])
        ins("claims", [x for x in parsed.claims if x["transaction_key"] in kept_txn_keys])
        ins("lines", [x for x in parsed.lines if x["transaction_key"] in kept_txn_keys])
        ins("adjustments", [x for x in parsed.adjustments if x["transaction_key"] in kept_txn_keys])
        ins("remarks", [x for x in parsed.remarks if x["transaction_key"] in kept_txn_keys])
        ins("plb", [x for x in parsed.plb if x["transaction_key"] in kept_txn_keys])
        for p in parsed.problems:
            c.execute("INSERT INTO problems (file_sha256, source, level, message, segment, transaction_ref, claim_ref) VALUES (?,?,?,?,?,?,?)",
                      (parsed.sha256, p.get("source"), p.get("level"), p.get("message"), p.get("segment"),
                       p.get("transaction"), p.get("claim")))
        status = "ok" if parsed.transactions else ("empty" if not parsed.problems else "error")
        if parsed.transactions and dup == len(parsed.transactions):
            status = "duplicate"
        c.execute("INSERT OR REPLACE INTO files (sha256, source_path, source_name, size, mtime, kind, ingested_at, "
                  "transactions, claims, lines, duplicates, problems, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (parsed.sha256, path, os.path.basename(path), size, mtime, kind, utcnow(),
                   len(parsed.transactions), len(parsed.claims), len(parsed.lines), dup, len(parsed.problems), status))
        return {"transactions": len(kept_txn_keys), "duplicates": dup, "problems": len(parsed.problems), "status": status}

    def ingest_835_paths(self, paths: Iterable[str], commit_every: int = 500, progress=None) -> dict:
        """Parse and store many files; files already in the store (by sha256) are skipped."""
        import hashlib
        stats = {"files": 0, "skipped_seen": 0, "ingested": 0, "duplicate_files": 0, "transactions": 0,
                 "duplicate_transactions": 0, "problems": 0, "errors": 0, "seconds": 0.0}
        t0 = time.time()
        self.conn.commit()          # the setting below cannot be changed inside an open transaction
        self.conn.execute("PRAGMA synchronous=OFF")
        n = 0
        for p in paths:
            stats["files"] += 1
            try:
                with open(p, "rb") as fh:
                    data = fh.read()
                sha = hashlib.sha256(data).hexdigest()
                if self.file_seen(sha):
                    stats["skipped_seen"] += 1
                    continue
                head = data[:600]
                if b"GS*HC*" in head or b"*837*" in head:
                    from .x12_837 import ingest_837_file
                    r837 = ingest_837_file(self, p)
                    stats["claim_files_837"] = stats.get("claim_files_837", 0) + 1
                    stats["claims_837"] = stats.get("claims_837", 0) + (r837.get("claims") or 0)
                    continue
                from .x12_835 import parse_835_bytes
                parsed = parse_835_bytes(data, source=p)
                r = self.ingest_parsed(parsed, source_path=p)
                stats["ingested"] += 1
                stats["transactions"] += r["transactions"]
                stats["duplicate_transactions"] += r["duplicates"]
                stats["problems"] += r["problems"]
                if r["status"] == "duplicate":
                    stats["duplicate_files"] += 1
            except Exception as exc:  # never let one file kill the batch
                stats["errors"] += 1
                self.conn.execute("INSERT INTO problems (file_sha256, source, level, message) VALUES (?,?,?,?)",
                                  (None, p, "file", f"{type(exc).__name__}: {exc}"))
            n += 1
            if n % commit_every == 0:
                self.conn.commit()
                if progress:
                    progress(stats)
        self.conn.commit()
        self.conn.execute("PRAGMA synchronous=NORMAL")
        stats["seconds"] = round(time.time() - t0, 1)
        return stats

    def ingest_835_directory(self, root: str, **kw) -> dict:
        return self.ingest_835_paths(iter_835_paths(root), **kw)

    # ------------------------------------------------------------------ supersession
    def mark_superseded(self) -> int:
        """A payer can deliver one remittance more than once: a file sent again, a copy cut short in transfer, a version
        with a claim added. Of the transactions that share a payer, a trace number and a payment date, only versions of
        one remittance compete (_versions): two that hold no claim in common are two payments, and both are counted.
        Among versions of one remittance (_standing), a complete one (it ends with its SE) whose payment equals its claims
        paid less its provider adjustments stands over any copy cut short or that does not add up; a version whose every
        claim and adjustment is on another complete, balanced version is hidden by it (among identical ones the newest,
        by ISA date, ISA time and file time, stands). Two complete, balanced versions that each hold something the other
        does not are both counted, and the store records it for a person (DIFFER_META, on the Data tab's checks), as it
        does a hidden broken copy that holds claims no standing version holds (_lost_entry). Returns how many
        transactions are hidden."""
        c = self.conn
        rows = c.execute("""
            SELECT t.transaction_key, COALESCE(t.trace_payer_id, t.payer_id, t.payer_name) AS pid, t.trace_number, t.payment_date,
                   t.isa_date, t.isa_time, f.mtime, t.content_fingerprint, t.balanced, t.payment_amount_cents, t.payer_name
            FROM transactions t LEFT JOIN files f ON f.sha256 = t.file_sha256
            WHERE t.trace_number IS NOT NULL AND t.trace_number <> ''
        """).fetchall()
        groups: dict[tuple, list] = {}
        for r in rows:
            groups.setdefault((r["pid"], r["trace_number"], r["payment_date"]), []).append(dict(r))
        multi = [m for m in groups.values() if len(m) > 1]
        keys = [r["transaction_key"] for m in multi for r in m]
        held: dict[str, dict] = {k: {"claims": [], "plb": []} for k in keys}
        for i in range(0, len(keys), 500):
            part = keys[i:i + 500]
            marks = ",".join("?" * len(part))
            for r in c.execute(f"""SELECT transaction_key, patient_control_number, payer_claim_control_number, claim_status_code, charge_cents,
                                          paid_cents, patient_responsibility_cents FROM claims WHERE transaction_key IN ({marks})""", part):
                held[r[0]]["claims"].append(tuple(r)[1:])
            for r in c.execute(f"SELECT transaction_key, reason_code, reference, amount_cents FROM plb WHERE transaction_key IN ({marks})", part):
                held[r[0]]["plb"].append(tuple(r)[1:])
        hidden: set[str] = set()
        differ: list[dict] = []
        for members in multi:
            for one in _versions(members, held):
                kept, drop = _standing(one, held)
                hidden |= drop
                if len(kept) > 1:
                    differ.append(_differ_entry(kept, held))
                lost = _lost_entry(kept, [m for m in one if m["transaction_key"] in drop], held)
                if lost:
                    differ.append(lost)
        c.execute("UPDATE transactions SET superseded=0")
        c.executemany("UPDATE transactions SET superseded=1 WHERE transaction_key=?", [(k,) for k in sorted(hidden)])
        c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (SUPERSEDE_META, SUPERSEDE_RULE))
        if differ:
            c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (DIFFER_META, json.dumps(differ[:50], default=str)))
        else:
            c.execute("DELETE FROM meta WHERE key=?", (DIFFER_META,))
        c.commit()
        return len(hidden)

    def recheck_supersession(self) -> bool:
        """Decide again which copies of a remittance stand when the rule that decided them has changed since (a release), new
        files or not: the next run after a deploy puts right what the old rule hid. True when a transaction's flag changed."""
        r = self.conn.execute("SELECT value FROM meta WHERE key=?", (SUPERSEDE_META,)).fetchone()
        if r and r[0] == SUPERSEDE_RULE:
            return False
        flags = "SELECT transaction_key FROM transactions WHERE COALESCE(superseded, 0) = 1"
        before = {k for (k,) in self.conn.execute(flags)}
        self.mark_superseded()
        return before != {k for (k,) in self.conn.execute(flags)}

    # ------------------------------------------------------------------ EMA ingest
    def ingest_ema_rows(self, rows: Iterable[dict]) -> dict:
        cols = self._cols["ema_rows"]
        sql = f"INSERT OR IGNORE INTO ema_rows ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"
        before = self.conn.total_changes
        self.conn.executemany(sql, [_row(r, "ema_rows", cols) for r in rows])
        self.conn.commit()
        return {"inserted": self.conn.total_changes - before}

    def record_export(self, source_name: str, sha256: str, family: str, created_at: str | None, rows: int) -> None:
        """Keep when an EMA export was pulled. An export only holds the visits that had become a bill by then, so the
        provider view needs it to tell a light day from a day whose bills were not written yet."""
        self.conn.execute("INSERT OR REPLACE INTO ema_sources (source_name, sha256, family, created_at, rows, recorded_at) VALUES (?,?,?,?,?,?)",
                          (source_name, sha256, family, created_at, rows, utcnow()))
        self.conn.commit()

    def record_file(self, sha256: str, path: str, kind: str, status: str = "ok", **counts) -> None:
        try:
            st = os.stat(path)
            size, mtime = st.st_size, datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(timespec="seconds")
        except OSError:
            size, mtime = None, None
        self.conn.execute("INSERT OR REPLACE INTO files (sha256, source_path, source_name, size, mtime, kind, ingested_at, "
                          "transactions, claims, lines, duplicates, problems, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (sha256, path, os.path.basename(path), size, mtime, kind, utcnow(),
                           counts.get("transactions"), counts.get("claims"), counts.get("lines"),
                           counts.get("duplicates"), counts.get("problems"), status))
        self.conn.commit()

    # ------------------------------------------------------------------ misc
    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    def close(self) -> None:
        self.conn.close()
