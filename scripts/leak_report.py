"""
Regenerate docs/DATA_FINDINGS.md from the store: payer-level aggregates only, no patient data.

    python scripts/leak_report.py --db data/remitwatch.sqlite > docs/DATA_FINDINGS.md
"""
from __future__ import annotations

import argparse
import sqlite3
from datetime import date


def money(c) -> str:
    return f"${(c or 0) / 100:,.0f}"


def q(conn, sql, params=()):
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/remitwatch.sqlite")
    a = ap.parse_args()
    conn = sqlite3.connect(a.db)
    conn.row_factory = sqlite3.Row
    out: list[str] = []
    p = out.append
    meta = q(conn, "SELECT COUNT(*) n, MIN(payment_date) lo, MAX(payment_date) hi, SUM(payment_amount_cents) paid FROM transactions WHERE superseded=0")[0]
    lines = q(conn, "SELECT COUNT(*) n FROM lines")[0]["n"]
    claims = q(conn, "SELECT COUNT(*) n FROM claims")[0]["n"]
    run = q(conn, "SELECT * FROM detection_runs ORDER BY started_at DESC LIMIT 1")
    p(f"# What the remittances say: where Owensboro Dermatology is losing money\n")
    p(f"Generated {date.today().isoformat()} from {meta['n']:,} unique 835 remittances ({claims:,} claims, {lines:,} service lines), "
      f"payment dates {meta['lo']} to {meta['hi']}, {money(meta['paid'])} paid. Detection version {run[0]['version'] if run else '?'}. "
      "Dollar figures are the allowed-amount shortfall against the payer's own usual rate (or Medicare where noted), not charges. "
      "All findings are open until a biller marks them otherwise.\n")

    p("## The one-paragraph answer\n")
    cat = {r["category"]: r for r in q(conn, "SELECT category, COUNT(*) n, SUM(shortfall_cents) c FROM findings WHERE status IN ('open','in_progress','appealed') AND category IN ('downcoding','bundling','denial','underpayment','fees','contract') GROUP BY 1")}
    rec = q(conn, "SELECT COUNT(*) n, SUM(shortfall_cents) c FROM findings WHERE status='resolved' AND notes LIKE 'Paid on a later remittance%'")[0]
    tot = sum(r["c"] or 0 for r in cat.values())
    p(f"Across the whole archive the detectable, still-unrecovered leakage is about {money(tot)}. Classic E/M downcoding, the thing the practice asked about, is "
      f"{money((cat.get('downcoding') or {}).get('c'))} of it. The money is in bundling ({money((cat.get('bundling') or {}).get('c'))}), "
      f"denials that were never worked ({money((cat.get('denial') or {}).get('c'))}), contract rates below Medicare ({money((cat.get('contract') or {}).get('c'))} at Medicare parity), "
      f"underpayment against the payer's own rate ({money((cat.get('underpayment') or {}).get('c'))}), and fees plus takebacks ({money((cat.get('fees') or {}).get('c'))}). "
      f"A further {money(rec['c'])} across {rec['n']:,} lines was initially denied or bundled and then paid on a later remittance, which is the rework the billers did do; those are marked resolved and excluded from the open figures.\n")

    p("## By year and category\n")
    p("| year | downcoding | bundling | denial | underpayment | fees | contract | total |")
    p("|---|---:|---:|---:|---:|---:|---:|---:|")
    p("Open findings only (resolved and dismissed excluded).\n")
    for y in q(conn, "SELECT year FROM findings WHERE year>='2023' GROUP BY 1 ORDER BY 1"):
        row = {r["category"]: r["c"] for r in q(conn, "SELECT category, SUM(shortfall_cents) c FROM findings WHERE year=? AND status IN ('open','in_progress','appealed') GROUP BY 1", (y["year"],))}
        cats = ["downcoding", "bundling", "denial", "underpayment", "fees", "contract"]
        p(f"| {y['year']} | " + " | ".join(money(row.get(c)) for c in cats) + f" | {money(sum(row.get(c) or 0 for c in cats))} |")
    p("")

    p("## By signal\n")
    p("| signal | category | what to do | open findings | open dollars | paid later (rework) |")
    p("|---|---|---|---:|---:|---:|")
    for r in q(conn, """SELECT signal, category, actionable, SUM(status IN ('open','in_progress','appealed')) n, SUM(CASE WHEN status IN ('open','in_progress','appealed') THEN shortfall_cents END) c,
                        SUM(CASE WHEN status='resolved' THEN shortfall_cents END) r FROM findings WHERE category<>'info' GROUP BY 1,2,3 ORDER BY c DESC"""):
        p(f"| {r['signal']} | {r['category']} | {r['actionable']} | {int(r['n'] or 0):,} | {money(r['c'])} | {money(r['r'])} |")
    p("")

    p("## By payer\n")
    p("| payer | kind | findings | dollars | of which appealable now |")
    p("|---|---|---:|---:|---:|")
    for r in q(conn, """SELECT payer_group, payer_kind, COUNT(*) n, SUM(shortfall_cents) c,
                        SUM(CASE WHEN actionable IN ('appeal','correct_and_resubmit') AND appeal_deadline >= date('now') THEN shortfall_cents ELSE 0 END) a
                        FROM findings WHERE status IN ('open','in_progress','appealed') AND category IN ('downcoding','bundling','denial','underpayment','fees','contract') GROUP BY 1,2 ORDER BY c DESC LIMIT 20"""):
        p(f"| {r['payer_group']} | {r['payer_kind']} | {r['n']:,} | {money(r['c'])} | {money(r['a'])} |")
    p("")

    p("## Contract rates versus Medicare\n")
    p("Usual allowed amount per code (most common allowed amount on cleanly paid primary lines) as a percent of the same code on this practice's own Medicare remits, same year and provider type.\n")
    p("| payer | year | code | provider type | lines | payer rate | Medicare | % of Medicare | gap at parity |")
    p("|---|---|---|---|---:|---:|---:|---:|---:|")
    for r in q(conn, """SELECT payer_group, year, procedure_code, provider_tier, json_extract(evidence,'$.lines') n, json_extract(evidence,'$.payer_rate_cents') pr,
                        json_extract(evidence,'$.medicare_cents') mc, json_extract(evidence,'$.pct_of_medicare') pct, shortfall_cents FROM findings
                        WHERE signal IN ('CONTRACT_RATE_GAP','MA_BELOW_MEDICARE') ORDER BY shortfall_cents DESC LIMIT 30"""):
        p(f"| {r['payer_group']} | {r['year']} | {r['procedure_code']} | {r['provider_tier']} | {r['n']} | {money(r['pr'])} | {money(r['mc'])} | {round(100*float(r['pct']))}% | {money(r['shortfall_cents'])} |")
    p("")

    p("## Medicare program adjustments\n")
    for r in q(conn, "SELECT title FROM findings WHERE signal IN ('MIPS_ADJUSTMENT','SEQUESTRATION') ORDER BY year, signal"):
        p(f"- {r['title']}")
    p("")

    p("## Fees withheld and takebacks\n")
    for r in q(conn, """SELECT payer_name, SUM(shortfall_cents) c, SUM(json_extract(evidence,'$.count')) n FROM findings WHERE signal='FEE_SKIM' GROUP BY 1 ORDER BY c DESC LIMIT 12"""):
        p(f"- Fees: {r['payer_name']}: {money(r['c'])} across {int(r['n'] or 0)} remittances")
    for r in q(conn, """SELECT payer_name, year, COUNT(*) n, SUM(shortfall_cents) c FROM findings WHERE signal='TAKEBACK' GROUP BY 1,2 ORDER BY c DESC LIMIT 10"""):
        p(f"- Takebacks: {r['payer_name']} {r['year']}: {money(r['c'])} across {r['n']} recoupments of $500 or more")
    p("")

    p("## Correctable billing errors (no appeal needed, just a corrected claim)\n")
    for r in q(conn, "SELECT signal, COUNT(*) n, SUM(shortfall_cents) c FROM findings WHERE actionable='correct_and_resubmit' AND status IN ('open','in_progress','appealed') GROUP BY 1 ORDER BY c DESC"):
        p(f"- {r['signal']}: {r['n']:,} lines, {money(r['c'])}")
    p("")

    p("## Appeal deadlines (estimated from the remittance date and payer type)\n")
    for r in q(conn, """SELECT CASE WHEN appeal_deadline < date('now') THEN 'expired' WHEN appeal_deadline < date('now','+30 days') THEN 'due within 30 days'
                        WHEN appeal_deadline < date('now','+90 days') THEN 'due within 90 days' ELSE 'later' END b, COUNT(*) n, SUM(shortfall_cents) c
                        FROM findings WHERE status='open' AND actionable IN ('appeal','correct_and_resubmit') GROUP BY 1 ORDER BY 1"""):
        p(f"- {r['b']}: {r['n']:,} findings, {money(r['c'])}")
    p("")
    p("## Caveats\n")
    p("- Expected amounts come from the payer's own remittance history (mode of allowed amounts), not from executed contracts. Load the contracts to replace them.")
    p("- Provider type (physician vs PA/NP) is inferred from Medicare allowed amounts; a roster would make it exact.")
    p("- Appeal windows are defaults by payer type; confirm against each contract and provider manual.")
    p("- Whole-claim denials count as unpaid only when no later remit paid the same claim number.")
    print("\n".join(out))


if __name__ == "__main__":
    main()
