"""Private: decision 3 items line by line (finding ids, payer groups, codes, cents, day offsets only)."""
import json, re, sqlite3, sys
from datetime import date
db = sys.argv[1]
c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
c.row_factory = sqlite3.Row
SEC = ("2", "3", "20", "21")
BILL = re.compile(r"([A-Z]{2}\d{2}[A-Z0-9]{5,6})C\d{0,3}")
def bill(p):
    m = BILL.fullmatch(p or "")
    return m.group(1) if m else (p or "")
def off(d, base):
    try:
        return (date.fromisoformat(str(d)[:10]) - date.fromisoformat(str(base)[:10])).days
    except Exception:
        return None
def adj(lk):
    return [(r[0], r[1], r[2]) for r in c.execute("SELECT group_code, reason_code, amount_cents FROM adjustments WHERE line_key=?", (lk,))]
def svc_lines(pcn, code, dos):
    b = bill(pcn)
    out = []
    for r in c.execute("""SELECT l.line_key, l.claim_key, l.procedure_code, l.service_date, l.charge_cents, l.paid_cents, l.allowed_cents,
                                 cl.claim_status_code, cl.payer_name, cl.patient_control_number, t.payment_date, COALESCE(t.superseded,0) sup
                          FROM claims cl JOIN transactions t ON t.transaction_key=cl.transaction_key JOIN lines l ON l.claim_key=cl.claim_key
                          WHERE cl.patient_control_number >= ? AND cl.patient_control_number < ?""", (b, b + "~")):
        if bill(r["patient_control_number"]) == b and r["procedure_code"] == code and (not dos or r["service_date"] == dos) and not r["sup"]:
            out.append(dict(r))
    return out
which = sys.argv[2] if len(sys.argv) > 2 else "both"
for f in c.execute("SELECT * FROM findings WHERE evidence LIKE '%paid_later%' OR evidence LIKE '%settled_by%'"):
    f = dict(f)
    ev = json.loads(f["evidence"] or "{}")
    pl, sb = ev.get("paid_later") or {}, ev.get("settled_by") or {}
    kind = None
    if pl.get("rule") == "units_split" and pl.get("how") == "paid on the item's own line" and str(pl.get("claim_status")) in SEC:
        kind = "r3"
    elif sb.get("how") == "processed as secondary":
        kind = "sec"
    if not kind or (which != "both" and which != kind):
        continue
    dos = f["service_date"]
    rows = svc_lines(f["patient_control_number"], f["procedure_code"], dos) if f.get("procedure_code") else []
    print(f"== {kind} {f['finding_id']} {f['signal']} {f['payer_group']} code {f['procedure_code']} carc {f['carc']} short {f['shortfall_cents']} "
          f"exp {f['expected_cents']} rec {f['recovered_cents']} paid_later {pl.get('paid_cents')} settled {sb.get('paid_cents')} prim {sb.get('primary_remittance')}")
    for r in sorted(rows, key=lambda r: (r["payment_date"] or "", r["line_key"])):
        a = adj(r["line_key"])
        mark = "*" if r["line_key"] == f.get("line_key") else ("P" if r["line_key"] == pl.get("line_key") else " ")
        print(f"   {mark} d+{off(r['payment_date'], dos)} st {r['claim_status_code']} {r['payer_name'][:22] if r['payer_name'] else ''!s:22} ch {r['charge_cents']} al {r['allowed_cents']} pd {r['paid_cents']} adj {a}")
