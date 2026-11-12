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
    conn.commit()
    out = {"lines": sum(stats.values()), "states": dict(stats), "charge_pool_rows": sum(len(v) for v in pool.values()),
           "charge_conflicts": len(conflicts), "handling_rows": len(handling_rows)}
    log(json.dumps(out))
    return out
