"""ALREADY_DONE open items in scope by signal and by the unit sub-kinds that settled them. Aggregates only.
Usage: python3 -I done_breakdown.py RESULT_DIR [verdicts_w]"""
import json, os, sqlite3, sys
from collections import Counter
d = sys.argv[1]; vt = sys.argv[2] if len(sys.argv) > 2 else "verdicts"
c = sqlite3.connect(f"file:{os.path.join(d, 'audit_result.sqlite')}?mode=ro", uri=True)
P2_ONLY = {"SECONDARY_NOT_BILLED", "CREDIT_BALANCE", "EMA_DENIED", "NOT_SENT", "REMIT_NOT_POSTED", "EMA_DENIAL_AS_CONTRACTUAL", "FORWARDED_TO_CARRIER"}
cond = "listed=1" if vt == "verdicts_w" else "status in ('open','in_progress','appealed')"
sig = Counter(); sigc = Counter(); why = Counter(); whyc = Counter()
units = {}
for r in c.execute("select bill, code, comp, dos, state, sub from units where in_scope=1"):
    units.setdefault(r[:4], []).append((r[4], r[5] or ""))
for fid, s, amt, keys in c.execute(f"select finding_id, signal, item_cents, svc_keys from {vt} where verdict='ALREADY_DONE' and in_scope=1 and {cond}"):
    if s in P2_ONLY:
        continue
    sig[s] += 1; sigc[s] += amt or 0
    subs = set()
    for k in json.loads(keys):
        for st, sub in units.get(tuple(k.split("|")), []):
            subs.add(st + ("/" + sub if sub.startswith(("ema", "paid_", "dropped", "allowed", "duplicate", "g2211", "reporting", "payer_", "placeholder", "all_billed")) else ""))
    w = ",".join(sorted(subs))[:90]
    why[w] += 1; whyc[w] += amt or 0
print("by signal:", ", ".join(f"{k} {v} ${sigc[k]/100:,.0f}" for k, v in sig.most_common(12)))
print("by unit truth:")
for k, v in why.most_common(18):
    print(f"   {v:5d} ${whyc[k]/100:>11,.2f}  {k}")
