"""
Link 835 service lines to ModMed EMA rows.

Two joins, both keyed by the EMA Claim ID (which the practice sends as the
835 patient control number, CLP01):

  line_links     835 line -> the EMA charge line that was billed (family
                 charge_lines, Activity Type = Charge). Exact composite match:
                 same claim id, service date, procedure code (the submitted code
                 when the payer reported one in SVC06), modifiers, units and
                 charge in cents, with no EMA row reused within one claim event.
                 A fallback candidate matches on date, code and charge only.
  line_handling  835 line -> the EMA "ERA & Payment Lines" rows for the same
                 claim, code and date: how the biller handled each adjustment
                 (WRITE_OFF, TRANSFER_RESPONSIBILITY, IGNORE).

The rules follow the practice's earlier reconciliation work: no probabilistic
matching, ambiguity is recorded rather than resolved.
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from datetime import date, timedelta

from .store import Store

DDL = """
CREATE TABLE IF NOT EXISTS line_links (
    line_key TEXT PRIMARY KEY, claim_key TEXT, state TEXT, cause TEXT,
    ema_row_fingerprint TEXT, bill_item_id TEXT, ema_source TEXT, ema_cpt TEXT, ema_modifiers TEXT,
    ema_units REAL, ema_charges_cents INTEGER, ema_provider TEXT, ema_payer TEXT, ema_claim_status TEXT
);
CREATE INDEX IF NOT EXISTS ix_links_claim ON line_links(claim_key);
CREATE TABLE IF NOT EXISTS line_handling (
    id INTEGER PRIMARY KEY AUTOINCREMENT, line_key TEXT, claim_id TEXT, cpt TEXT, service_date TEXT,
    carc TEXT, rarc TEXT, adjustment_status TEXT, adjustment_subtype TEXT, adjustments_cents INTEGER,
    payments_cents INTEGER, transaction_date TEXT, ema_source TEXT
);
CREATE INDEX IF NOT EXISTS ix_handling_line ON line_handling(line_key);
"""


EMA_WEEK_SHARE = 0.8        # a week of service with fewer of its remittance claims in an EMA charge export is listed
EMA_WEEK_MIN_CLAIMS = 30    # claims a week needs on the remittances before its share says anything
EMA_COVERAGE_KEY = "ema:coverage"


def stored_ema_coverage(conn: sqlite3.Connection) -> dict:
    """The coverage measured at the last analysis run (see ema_coverage), or nothing when no run has measured it."""
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (EMA_COVERAGE_KEY,)).fetchone()
        out = json.loads(row[0]) if row and row[0] else None
    except (sqlite3.Error, ValueError, TypeError):
        out = None
    return out if isinstance(out, dict) else {"weeks": [], "first_week": None, "last_ema_service": None,
                                              "share": EMA_WEEK_SHARE, "min_claims": EMA_WEEK_MIN_CLAIMS}


def ema_coverage(conn: sqlite3.Connection) -> dict:
    """For each week of service since the EMA charge exports began: how many claims the remittances hold for it, and
    for how many of them an Encounter and Charge Lines export holds a charge row (by claim id, or by bill for a second
    payer's claim). An export only holds the visits that had become a bill when it was pulled, so a week pulled as it
    ended, or never pulled, shows up here with a low share.

    The exports begin with the first week holding at least a fifth of the busiest week's charge rows (a stray older row
    does not count), and run to the newest week the remittances have claims for. A week's claims are counted by the
    date on each service line; lines without a date are left out."""
    from .detect import bill_of
    empty = {"weeks": [], "first_week": None, "last_ema_service": None, "share": EMA_WEEK_SHARE, "min_claims": EMA_WEEK_MIN_CLAIMS}
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"ema_rows", "lines", "claims", "transactions"} <= tables:
        return empty
    monday = lambda d: (date.fromisoformat(d[:10]) - timedelta(days=date.fromisoformat(d[:10]).weekday())).isoformat()    # noqa: E731
    ids: set = set()
    bills: set = set()
    rows_by_week: Counter = Counter()
    last_service = None
    for cid, d in conn.execute("""SELECT claim_id, service_date FROM ema_rows
                                  WHERE family='charge_lines' AND activity_type='Charge' AND claim_id IS NOT NULL AND service_date IS NOT NULL"""):
        try:
            w = monday(d)
        except ValueError:
            continue
        ids.add(cid)
        bills.add(bill_of(cid))
        rows_by_week[w] += 1
        last_service = d[:10] if last_service is None or d[:10] > last_service else last_service
    if not rows_by_week:
        return empty
    busiest = max(rows_by_week.values())
    first = min(w for w, n in rows_by_week.items() if n * 5 >= busiest)
    first_claim: dict[str, str] = {}
    for pcn, d in conn.execute("""SELECT l.patient_control_number, l.service_date FROM lines l
                                  JOIN claims c ON c.claim_key = l.claim_key JOIN transactions t ON t.transaction_key = l.transaction_key
                                  WHERE t.superseded = 0 AND c.claim_status_code != '22' AND l.service_date >= ? AND l.patient_control_number IS NOT NULL""",
                               (first,)):
        if d and (pcn not in first_claim or d < first_claim[pcn]):
            first_claim[pcn] = d
    claims: Counter = Counter()
    held: Counter = Counter()
    for pcn, d in first_claim.items():
        try:
            w = monday(d)
        except ValueError:
            continue
        claims[w] += 1
        if pcn in ids or bill_of(pcn) in bills:
            held[w] += 1
    weeks = [{"week": w, "claims": claims[w], "in_ema": held[w]} for w in sorted(claims)]
    return dict(empty, weeks=weeks, first_week=first, last_ema_service=last_service)


def _mods(js: str | None) -> tuple:
    if not js:
        return ()
    try:
        return tuple(sorted(json.loads(js)))
    except Exception:
        return ()


def build_links(store: Store, log=print) -> dict:
    conn = store.conn
    conn.row_factory = sqlite3.Row
    conn.executescript(DDL)
    conn.execute("DELETE FROM line_links")
    conn.execute("DELETE FROM line_handling")

    # EMA charge pool: one row per stable (bill_item_id, claim_id); conflicting duplicates excluded
    pool: dict[str, list[dict]] = defaultdict(list)
    seen: dict[tuple, tuple] = {}
    conflicts: set[tuple] = set()
    rows = conn.execute("""SELECT row_fingerprint, source_name, claim_id, bill_item_id, service_date, cpt, modifiers, units, charges_cents,
                                  patient_id, provider, payer, claim_status
                           FROM ema_rows WHERE family='charge_lines' AND activity_type='Charge' AND claim_id IS NOT NULL
                           ORDER BY CASE WHEN source_name LIKE 'Encount%' THEN 0 ELSE 1 END, source_name, row_index""").fetchall()
    staged: list[dict] = []
    for r in rows:
        d = dict(r)
        sig = (d["service_date"], d["cpt"], _mods(d["modifiers"]), d["units"], d["charges_cents"])
        key = (d["bill_item_id"], d["claim_id"])
        if d["bill_item_id"]:
            if key in seen:
                if seen[key] != sig:
                    conflicts.add(key)
                continue
            seen[key] = sig
        staged.append(d)
    # overlapping exports (daily and weekly, with and without bill item ids) repeat the same charge:
    # within a claim, identical signatures coming from different source files are one charge
    sig_seen: dict[tuple, str] = {}
    for d in staged:
        if (d["bill_item_id"], d["claim_id"]) in conflicts:
            continue
        sig = (d["claim_id"], d["service_date"], d["cpt"], _mods(d["modifiers"]), d["units"], d["charges_cents"])
        src = sig_seen.get(sig)
        if src is not None and src != d["source_name"]:
            continue
        sig_seen[sig] = d["source_name"]
        pool[d["claim_id"]].append(d)

    # ERA handling rows
    handling: dict[tuple, list[dict]] = defaultdict(list)
    for r in conn.execute("""SELECT source_name, claim_id, cpt, service_date, carc, rarc, adjustment_status, adjustment_subtype,
                                    adjustments_cents, payments_cents, transaction_date
                             FROM ema_rows WHERE family='era_lines' AND claim_id IS NOT NULL"""):
        d = dict(r)
        handling[(d["claim_id"], d["cpt"], d["service_date"])].append(d)

    stats = Counter()
    link_rows: list[tuple] = []
    handling_rows: list[tuple] = []
    claim_lines = conn.execute("""SELECT l.line_key, l.claim_key, l.patient_control_number pcn, l.procedure_code, l.original_procedure_code,
                                         l.modifiers, l.original_modifiers, l.paid_units, l.original_units, l.charge_cents, l.service_date,
                                         c.claim_status_code, c.statement_from
                                  FROM lines l JOIN claims c ON c.claim_key=l.claim_key JOIN transactions t ON t.transaction_key=l.transaction_key
                                  WHERE t.superseded=0 ORDER BY l.claim_key, l.line_seq""").fetchall()
    by_claim: dict[str, list[dict]] = defaultdict(list)
    for r in claim_lines:
        by_claim[r["claim_key"]].append(dict(r))

    for claim_key, lines in by_claim.items():
        pcn = lines[0]["pcn"]
        cands_pool = pool.get(pcn, [])
        patients = {x["patient_id"] for x in cands_pool if x.get("patient_id")}
        results = []
        for l in lines:
            date = l["service_date"] or l["statement_from"]
            code = l["original_procedure_code"] or l["procedure_code"]
            units = l["original_units"] if l["original_units"] is not None else l["paid_units"]
            mods = _mods(l["original_modifiers"]) if l["original_procedure_code"] else _mods(l["modifiers"])
            charge = l["charge_cents"] or 0
            if l["claim_status_code"] == "22":
                charge = abs(charge)
                units = abs(units) if units is not None else None
            if not cands_pool:
                results.append(("unmatched", "no EMA charge row for this claim id", None, []))
                continue
            if len(patients) > 1:
                results.append(("ambiguous", "claim id maps to several patients in EMA", None, []))
                continue
            loose = [x for x in cands_pool if x["service_date"] == date and x["cpt"] == code and x["charges_cents"] == charge]
            exact = [x for x in loose if _mods(x["modifiers"]) == mods and (x["units"] == units or (units is None and x["units"] is None))] if units not in (None, 0) else []
            results.append((None, None, exact, loose))
        reuse = Counter(id(e[2][0]) for e in results if e[2] and len(e[2]) == 1)
        for l, (state, cause, exact, loose) in zip(lines, results):
            match = None
            if state is None:
                if exact and len(exact) == 1 and reuse[id(exact[0])] == 1:
                    state, cause, match = "exact_composite", "", exact[0]
                elif (exact and len(exact) > 1) or (not exact and loose and len(loose) > 1) or (exact and len(exact) == 1 and reuse[id(exact[0])] > 1):
                    state, cause = "ambiguous", "multiple charge candidates or within-claim reuse"
                elif not exact and loose and len(loose) == 1:
                    state, cause, match = "fallback_candidate", "units or modifiers differ or are unavailable", loose[0]
                else:
                    state, cause = "unmatched", "same claim but date, code or charge differs"
            stats[state] += 1
            if state != "unmatched":        # nine lines in ten have no EMA row; storing "unmatched" for each only costs space
                link_rows.append((l["line_key"], claim_key, state, cause,
                                  match["row_fingerprint"] if match else None, match["bill_item_id"] if match else None,
                                  match["source_name"] if match else None, match["cpt"] if match else None,
                                  match["modifiers"] if match else None, match["units"] if match else None,
                                  match["charges_cents"] if match else None, match["provider"] if match else None,
                                  match["payer"] if match else None, match["claim_status"] if match else None))
            key = (pcn, l["procedure_code"], l["service_date"] or l["statement_from"])
            alt = (pcn, l["original_procedure_code"], l["service_date"] or l["statement_from"]) if l["original_procedure_code"] else None
            for h in handling.get(key, []) + (handling.get(alt, []) if alt else []):
                handling_rows.append((l["line_key"], pcn, h["cpt"], h["service_date"], h["carc"], h["rarc"], h["adjustment_status"],
                                      h["adjustment_subtype"], h["adjustments_cents"], h["payments_cents"], h["transaction_date"], h["source_name"]))
    conn.executemany("INSERT OR REPLACE INTO line_links VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", link_rows)
    conn.executemany("INSERT INTO line_handling (line_key, claim_id, cpt, service_date, carc, rarc, adjustment_status, adjustment_subtype, adjustments_cents, payments_cents, transaction_date, ema_source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", handling_rows)
    # measured here, on the full store: the dashboard's serverless copy keeps only the lines behind findings
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (EMA_COVERAGE_KEY, json.dumps(ema_coverage(conn))))
    conn.commit()
    out = {"lines": sum(stats.values()), "states": dict(stats), "charge_pool_rows": sum(len(v) for v in pool.values()),
           "charge_conflicts": len(conflicts), "handling_rows": len(handling_rows)}
    log(json.dumps(out))
    return out
