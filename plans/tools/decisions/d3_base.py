"""Private: decision 3 populations in a snapshot: rule 3 closures in another role, and settled-as-secondary closures."""
import json, sqlite3, sys
from collections import Counter, defaultdict
db = sys.argv[1]
c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
c.row_factory = sqlite3.Row
SEC = ("2", "3", "20", "21")
r3 = defaultdict(lambda: [0, 0, 0, 0])
sec = defaultdict(lambda: [0, 0, 0, 0])
for f in c.execute("SELECT * FROM findings WHERE evidence LIKE '%paid_later%' OR evidence LIKE '%settled_by%'"):
    f = dict(f)
    ev = json.loads(f["evidence"] or "{}")
    pl = ev.get("paid_later") or {}
    ar = ev.get("archive_rule") or {}
    if pl.get("rule") == "units_split" and pl.get("how") == "paid on the item's own line":
        key = (str(pl.get("claim_status")) in SEC, f["status"], f["work_state"], bool(f.get("auto_note")), (f.get("recovered_cents") or 0) > 0)
        r3[key][0] += 1
        r3[key][1] += pl.get("paid_cents") or 0
        r3[key][2] += f.get("recovered_cents") or 0
        r3[key][3] += f.get("shortfall_cents") or 0
    sb = ev.get("settled_by") or {}
    if sb.get("how") == "processed as secondary":
        key = (f["status"], f["work_state"], bool(f.get("auto_note")), (sb.get("paid_cents") or 0) > 0, sb.get("primary_remittance"), bool(f.get("line_key")))
        sec[key][0] += 1
        sec[key][1] += sb.get("paid_cents") or 0
        sec[key][2] += f.get("recovered_cents") or 0
        sec[key][3] += f.get("shortfall_cents") or 0
print("rule 3 own-line closures (secondary pay?, status, state, auto, recovered>0): n, paid, recovered, shortfall")
for k, v in sorted(r3.items(), key=lambda kv: -kv[1][0]):
    print(" ", k, v[0], f"paid ${v[1]/100:,.2f} rec ${v[2]/100:,.2f} short ${v[3]/100:,.2f}")
print("settled as secondary (status, state, auto, paid>0, primary remit, line item): n, paid, recovered, shortfall")
for k, v in sorted(sec.items(), key=lambda kv: -kv[1][0]):
    print(" ", k, v[0], f"paid ${v[1]/100:,.2f} rec ${v[2]/100:,.2f} short ${v[3]/100:,.2f}")
