"""Private: one item's bill, every 835 line of its codes and dates, with reasons (day offsets from the service date)."""
import json, re, sqlite3, sys
from datetime import date
db, fid = sys.argv[1], sys.argv[2]
c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
c.row_factory = sqlite3.Row
BILL = re.compile(r"([A-Z]{2}\d{2}[A-Z0-9]{5,6})C\d{0,3}")
def bill(p):
    m = BILL.fullmatch(p or "")
    return m.group(1) if m else (p or "")
f = dict(c.execute("SELECT * FROM findings WHERE finding_id=?", (fid,)).fetchone())
ev = json.loads(f["evidence"] or "{}")
dos = f["service_date"] or (ev.get("lines") or [[None, None]])[0][1] if isinstance(ev.get("lines"), list) else f["service_date"]
base = f["service_date"]
def off(d):
    try:
        return (date.fromisoformat(str(d)[:10]) - date.fromisoformat(str(base)[:10])).days
    except Exception:
        return None
print("item", fid, f["signal"], f["category"], f["payer_group"], f["payer_kind"], "code", f["procedure_code"], "carc", f["carc"], "short", f["shortfall_cents"],
      "exp", f["expected_cents"], "status", f["status"], "state", f["work_state"], "rec", f["recovered_cents"])
print("  keys:", sorted(k for k in ev.keys()))
for k in ("settled_by", "paid_later", "archive_rule", "codes", "lines", "carc_group"):
    if k in ev:
        v = ev[k]
        if isinstance(v, dict):
            v = {kk: vv for kk, vv in v.items() if kk not in ("proof", "claim", "trace", "hint", "note")}
        print("  ", k, json.dumps(v, default=str)[:400])
b = bill(f["patient_control_number"])
for r in c.execute("""SELECT cl.claim_key, cl.claim_status_code, cl.payer_name, cl.patient_control_number, cl.charge_cents, cl.paid_cents, t.payment_date,
                             COALESCE(t.superseded,0) s FROM claims cl JOIN transactions t ON t.transaction_key=cl.transaction_key
                      WHERE cl.patient_control_number >= ? AND cl.patient_control_number < ? ORDER BY t.payment_date""", (b, b + "~")):
    if bill(r["patient_control_number"]) != b or r["s"]:
        continue
    ca = [(x[0], x[1], x[2]) for x in c.execute("SELECT group_code, reason_code, amount_cents FROM adjustments WHERE claim_key=? AND line_key IS NULL", (r["claim_key"],))]
    sub = r["patient_control_number"][len(b):] if r["patient_control_number"].startswith(b) else "?"
    print(f"  claim sub {sub} d+{off(r['payment_date'])} st {r['claim_status_code']} {str(r['payer_name'])[:24]:24} ch {r['charge_cents']} pd {r['paid_cents']} cadj {ca}")
    for l in c.execute("SELECT line_key, procedure_code, service_date, charge_cents, paid_cents, allowed_cents, line_control_number FROM lines WHERE claim_key=?", (r["claim_key"],)):
        la = [(x[0], x[1], x[2]) for x in c.execute("SELECT group_code, reason_code, amount_cents FROM adjustments WHERE line_key=?", (l["line_key"],))]
        mark = "*" if l["line_key"] == f.get("line_key") else " "
        print(f"     {mark} {l['procedure_code']} dos d+{off(l['service_date'])} ch {l['charge_cents']} al {l['allowed_cents']} pd {l['paid_cents']} adj {la}")
