"""Private: hand-check of decision 4's evidence from the raw 835 rows, with plain SQL and no remitwatch code (counts only)."""
import sqlite3, sys, json, random
world = sys.argv[1]
c = sqlite3.connect(f"file:{world}?mode=ro", uri=True)

def count(code, frm, to, payer_like, cfi_in=None, cfi_not=None):
    """Lines billed as `code` (the line's code, or its original code when the payer changed it), answered with claim status
    1, 4 or 19, service date in the period, on non-superseded remittances of payers whose name matches; paid = the line's
    own paid amount above zero (no reversal pairing)."""
    q = f"""SELECT COUNT(*), SUM(COALESCE(l.paid_cents,0) > 0), SUM(l.original_procedure_code = ? AND l.procedure_code <> ?)
            FROM lines l JOIN claims cl ON cl.claim_key = l.claim_key JOIN transactions t ON t.transaction_key = l.transaction_key
            WHERE t.superseded = 0 AND l.claim_status_code IN ('1','4','19')
              AND (CASE WHEN l.original_procedure_code IS NOT NULL AND l.original_procedure_code <> l.procedure_code
                        THEN l.original_procedure_code ELSE l.procedure_code END) = ?
              AND COALESCE(l.service_date, cl.statement_from) >= ? AND COALESCE(l.service_date, cl.statement_from) <= ?
              AND ({' OR '.join('UPPER(l.payer_name) LIKE ?' for _ in payer_like)})"""
    args = [code, code, code, frm, to] + payer_like
    if cfi_in:
        q += f" AND cl.claim_filing_indicator IN ({','.join('?'*len(cfi_in))})"; args += cfi_in
    if cfi_not:
        q += f" AND COALESCE(cl.claim_filing_indicator,'') NOT IN ({','.join('?'*len(cfi_not))})"; args += cfi_not
    return c.execute(q, args).fetchone()

print("Anthem 77600 2026 (names ANTHEM/BLUE CROSS/BCBS, not MA/Medicaid CFI):",
      count("77600", "2026-01-01", "9999", ["ANTHEM%", "%BLUE CROSS%", "BCBS%", "%BLUECARD%"], cfi_not=["16", "MC"]))
print("Anthem G2211 since 2025:", count("G2211", "2025-01-01", "9999", ["ANTHEM%", "%BLUE CROSS%", "BCBS%", "%BLUECARD%"], cfi_not=["16", "MC"]))
print("UHC MA G6001 2025 (UHC names with CFI 16 or MEDICARE in name):",
      c.execute("""SELECT COUNT(*), SUM(COALESCE(l.paid_cents,0)>0) FROM lines l JOIN claims cl ON cl.claim_key=l.claim_key
                   JOIN transactions t ON t.transaction_key=l.transaction_key
                   WHERE t.superseded=0 AND l.claim_status_code IN ('1','4','19') AND l.procedure_code='G6001'
                     AND COALESCE(l.service_date, cl.statement_from) BETWEEN '2025-01-01' AND '2025-12-31'
                     AND (UPPER(l.payer_name) LIKE '%UNITED%' OR UPPER(l.payer_name) LIKE 'UHC%' OR UPPER(l.payer_name) LIKE 'AARP%')
                     AND (cl.claim_filing_indicator='16' OR UPPER(l.payer_name) LIKE '%MEDICARE%')""").fetchone())
print("UHC commercial G6001 2025 (UHC names, not CFI 16/MC and no MEDICARE in name):",
      c.execute("""SELECT COUNT(*), SUM(COALESCE(l.paid_cents,0)>0) FROM lines l JOIN claims cl ON cl.claim_key=l.claim_key
                   JOIN transactions t ON t.transaction_key=l.transaction_key
                   WHERE t.superseded=0 AND l.claim_status_code IN ('1','4','19') AND l.procedure_code='G6001'
                     AND COALESCE(l.service_date, cl.statement_from) BETWEEN '2025-01-01' AND '2025-12-31'
                     AND (UPPER(l.payer_name) LIKE 'UNITEDHEALTHCARE%' OR UPPER(l.payer_name) LIKE 'UNITED HEALTHCARE%')
                     AND COALESCE(cl.claim_filing_indicator,'') NOT IN ('16','MC') AND UPPER(l.payer_name) NOT LIKE '%MEDICARE%'""").fetchone())
# the reasons on the unpaid Anthem 77600 2026 lines, read straight from adjustments
rows = c.execute("""SELECT a.group_code || '-' || a.reason_code, COUNT(DISTINCT l.line_key) FROM lines l
                    JOIN claims cl ON cl.claim_key=l.claim_key JOIN transactions t ON t.transaction_key=l.transaction_key
                    JOIN adjustments a ON a.line_key = l.line_key
                    WHERE t.superseded=0 AND l.claim_status_code IN ('1','4','19') AND COALESCE(l.paid_cents,0) = 0
                      AND (l.procedure_code='77600' OR l.original_procedure_code='77600')
                      AND COALESCE(l.service_date, cl.statement_from) >= '2026-01-01' AND COALESCE(a.amount_cents,0) <> 0
                      AND (UPPER(l.payer_name) LIKE 'ANTHEM%' OR UPPER(l.payer_name) LIKE '%BLUE CROSS%' OR UPPER(l.payer_name) LIKE 'BCBS%' OR UPPER(l.payer_name) LIKE '%BLUECARD%')
                      AND COALESCE(cl.claim_filing_indicator,'') NOT IN ('16','MC')
                    GROUP BY 1 ORDER BY 2 DESC LIMIT 4""").fetchall()
print("Anthem 77600 2026 unpaid-line reasons:", rows)
