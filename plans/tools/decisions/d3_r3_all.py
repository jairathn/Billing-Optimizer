"""Private: every rule 3 own-line closure: the item's answer status vs the payment's, dates, recovery."""
import json, sqlite3, sys
from collections import Counter
db = sys.argv[1]
c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
PRIM = ("1", "19")
cnt = Counter(); cents = Counter()
for fid, ev, lk, pd, rec in c.execute("SELECT finding_id, evidence, line_key, payment_date, recovered_cents FROM findings WHERE evidence LIKE '%units_split%'"):
    ev = json.loads(ev or "{}")
    pl = ev.get("paid_later") or {}
    if pl.get("rule") != "units_split" or pl.get("how") != "paid on the item's own line":
        continue
    own = c.execute("SELECT claim_status_code FROM lines WHERE line_key=?", (lk,)).fetchone()
    a_st = own[0] if own else None
    p_st = str(pl.get("claim_status"))
    switched = (p_st in PRIM) != (a_st in PRIM)
    later = (pl.get("payment_date") or "") > (pd or "")
    k = (f"item {a_st}", f"pay {p_st}", "switched" if switched else "same role", "later" if later else "same day or before", "rec>0" if (rec or 0) > 0 else "rec 0")
    cnt[k] += 1; cents[k] += pl.get("paid_cents") or 0
for k, v in sorted(cnt.items(), key=lambda kv: -kv[1]):
    print(v, k, f"${cents[k]/100:,.2f}")
