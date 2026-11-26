"""Private: what each decision-3 item's payer owed as secondary and what is left, by the formula (aggregates + ids)."""
import json, re, sqlite3, sys
db = sys.argv[1]
c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
c.row_factory = sqlite3.Row
SEC = ("2", "3", "20", "21")
COST = ("1", "2", "3")
BILL = re.compile(r"([A-Z]{2}\d{2}[A-Z0-9]{5,6})C\d{0,3}")
def bill(p):
    m = BILL.fullmatch(p or "")
    return m.group(1) if m else (p or "")
def adj(lk):
    return [(r[0], r[1], r[2] or 0) for r in c.execute("SELECT group_code, reason_code, amount_cents FROM adjustments WHERE line_key=?", (lk,))]
def line(lk):
    r = c.execute("""SELECT l.*, cl.claim_status_code cs, cl.payer_name pn, cl.patient_control_number pcn, t.payment_date pd FROM lines l
                     JOIN claims cl ON cl.claim_key=l.claim_key JOIN transactions t ON t.transaction_key=l.transaction_key WHERE l.line_key=?""", (lk,)).fetchone()
    return dict(r) if r else None
def others_paid(pcn, code, dos, payer):
    b = bill(pcn); tot = 0
    for r in c.execute("""SELECT l.paid_cents, cl.payer_name, cl.claim_status_code, cl.patient_control_number, COALESCE(t.superseded,0) s FROM claims cl
                          JOIN transactions t ON t.transaction_key=cl.transaction_key JOIN lines l ON l.claim_key=cl.claim_key
                          WHERE cl.patient_control_number >= ? AND cl.patient_control_number < ? AND l.procedure_code=? AND l.service_date=?""",
                       (b, b + "~", code, dos)):
        if bill(r[3]) == b and not r[4] and r[1] != payer:
            tot += r[0] or 0      # reversals (negative) net out
    return tot
res = []
for f in c.execute("SELECT * FROM findings WHERE evidence LIKE '%paid_later%'"):
    f = dict(f)
    ev = json.loads(f["evidence"] or "{}")
    pl = ev.get("paid_later") or {}
    if not (pl.get("rule") == "units_split" and pl.get("how") == "paid on the item's own line" and str(pl.get("claim_status")) in SEC):
        continue
    p = line(pl["line_key"])
    a = adj(p["line_key"])
    allowed = p["allowed_cents"] if p["allowed_cents"] else (f["expected_cents"] or 0)
    allowed = min(allowed, p["charge_cents"] or allowed)
    other = others_paid(p["pcn"], p["procedure_code"], p["service_date"], p["pn"])
    oa23 = sum(x for g, r, x in a if g == "OA" and r == "23")
    prior = other if other > 0 else oa23
    share = sum(x for g, r, x in a if g == "PR")
    owed = max(allowed - prior - share, 0)
    arrived = pl.get("paid_cents") or 0
    rem = max(owed - arrived, 0)
    res.append((f["finding_id"], f["payer_group"], f["procedure_code"], allowed, other, oa23, share, owed, arrived, rem))
for r in res:
    print(r)
print("n", len(res), "arrived", sum(r[8] for r in res), "remainder>0", sum(1 for r in res if r[9] > 0), "remainder", sum(r[9] for r in res))
