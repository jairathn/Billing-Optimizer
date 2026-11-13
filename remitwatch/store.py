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


def ddl(compact: bool = False) -> str:
    """The schema. A compact store leaves out the secondary indexes: it is scanned whole by detection and never queried
    row by row, and the indexes are a third of its size. The dashboard's copy (remitwatch.cloud) builds its own."""
    if not compact:
        return DDL
    return "\n".join(ln for ln in DDL.splitlines() if not ln.lstrip().upper().startswith(("CREATE INDEX", "PRAGMA JOURNAL_MODE")))


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
        """When the same payer trace number was delivered with different content, keep the
        latest file (by ISA date, then mtime) as authoritative and flag the rest."""
        c = self.conn
        c.execute("UPDATE transactions SET superseded=0")
        rows = c.execute("""
            SELECT t.transaction_key, COALESCE(t.trace_payer_id, t.payer_id, t.payer_name) AS pid, t.trace_number, t.payment_date,
                   t.isa_date, t.isa_time, f.mtime
            FROM transactions t LEFT JOIN files f ON f.sha256 = t.file_sha256
            WHERE t.trace_number IS NOT NULL AND t.trace_number <> ''
        """).fetchall()
        groups: dict[tuple, list] = {}
        for r in rows:
            groups.setdefault((r["pid"], r["trace_number"], r["payment_date"]), []).append(r)
        flagged = 0
        for key, members in groups.items():
            if len(members) < 2:
                continue
            # the last key makes the choice the same whatever order the files were read in
            members.sort(key=lambda r: ((r["isa_date"] or ""), (r["isa_time"] or ""), (r["mtime"] or ""), r["transaction_key"]))
            for r in members[:-1]:
                c.execute("UPDATE transactions SET superseded=1 WHERE transaction_key=?", (r["transaction_key"],))
                flagged += 1
        c.commit()
        return flagged

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
