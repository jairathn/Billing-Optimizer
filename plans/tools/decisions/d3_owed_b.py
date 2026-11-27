"""Private: settled-as-secondary items: owed and remainder by the formula, from the settling claim's lines."""
import json, re, sqlite3, sys
db = sys.argv[1]
c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
c.row_factory = sqlite3.Row
BILL = re.compile(r"([A-Z]{2}\d{2}[A-Z0-9]{5,6})C\d{0,3}")
def bill(p):
    m = BILL.fullmatch(p or "")
    return m.group(1) if m else (p or "")
def adj(lk):
    return [(r[0], r[1], r[2] or 0) for r in c.execute("SELECT group_code, reason_code, amount_cents FROM adjustments WHERE line_key=?", (lk,))]
def cadj(ck):
    return [(r[0], r[1], r[2] or 0) for r in c.execute("SELECT group_code, reason_code, amount_cents FROM adjustments WHERE claim_key=? AND line_key IS NULL", (ck,))]
tot = [0, 0, 0, 0]
for f in c.execute("SELECT * FROM findings WHERE evidence LIKE '%settled_by%'"):
    f = dict(f)
    ev = json.loads(f["evidence"] or "{}")
    sb = ev.get("settled_by") or {}
    if sb.get("how") != "processed as secondary":
        continue
    codes = sb.get("codes") or [f["procedure_code"]]
    # the settling claim(s): claim id sb['claim'] on date sb['date']
    rows = [dict(r) for r in c.execute("""SELECT l.*, cl.claim_status_code cs, cl.payer_name pn, cl.claim_key ck, t.payment_date pd FROM claims cl
                      JOIN transactions t ON t.transaction_key=cl.transaction_key JOIN lines l ON l.claim_key=cl.claim_key
                      WHERE cl.patient_control_number=? AND t.payment_date=? AND COALESCE(t.superseded,0)=0""", (sb.get("claim"), sb.get("date")))]
    rows = [r for r in rows if r["cs"] in ("2", "3", "20", "21") and (not codes or r["procedure_code"] in codes)]
    allowed = sum(r["allowed_cents"] or 0 for r in rows)
    charge = sum(r["charge_cents"] or 0 for r in rows)
    base = min(allowed or (f["expected_cents"] or 0), charge) if charge else (allowed or (f["expected_cents"] or 0))
    oa23 = sum(x for r in rows for g, rc, x in adj(r["line_key"]) if g == "OA" and rc == "23")
    if not oa23 and rows:
        cks = {r["ck"] for r in rows}
        oa23 = sum(x for ck in cks for g, rc, x in cadj(ck) if g == "OA" and rc == "23")
    share = sum(x for r in rows for g, rc, x in adj(r["line_key"]) if g == "PR")
    paid = sum(r["paid_cents"] or 0 for r in rows)
    owed = max(base - oa23 - share, 0)
    left = max(owed - paid, 0)
    tot[0] += 1; tot[1] += paid; tot[2] += 1 if left > 0 else 0; tot[3] += left
    print(f["finding_id"], f["signal"], f["payer_group"], "lines", len(rows), "allowed", allowed, "charge", charge, "base", base, "oa23", oa23, "pr", share,
          "paid", paid, "owed", owed, "left", left, "settled_paid", sb.get("paid_cents"))
print("n", tot[0], "paid", tot[1], "left>0", tot[2], "left", tot[3])
