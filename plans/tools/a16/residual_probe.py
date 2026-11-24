"""Work-now items (in scope) A16 calls already done / already worked / duplicate, other than EMA's off-feed payments,
crossed with the merged code's own fields. Aggregates only. Usage: python3 -I -B residual_probe.py OUT_DIR WORLD_DB"""
import json, os, re, sqlite3, sys
from collections import Counter
d, world = sys.argv[1], sys.argv[2]
c = sqlite3.connect(f"file:{os.path.join(d, 'audit_result.sqlite')}?mode=ro", uri=True)
w = sqlite3.connect(f"file:{world}?mode=ro", uri=True)
units = {}
for r in c.execute("select bill, code, comp, dos, state, sub from units where in_scope=1"):
    units.setdefault(r[:4], []).append(r[4:])
P2_ONLY = {"SECONDARY_NOT_BILLED", "CREDIT_BALANCE", "EMA_DENIED", "NOT_SENT", "REMIT_NOT_POSTED", "EMA_DENIAL_AS_CONTRACTUAL", "FORWARDED_TO_CARRIER"}
tab = Counter(); dol = Counter()
for fid, s, amt, keys, verdict, vsub in c.execute("select finding_id, signal, item_cents, svc_keys, verdict, sub from verdicts_w where verdict in ('ALREADY_DONE','ALREADY_WORKED','DUPLICATE') and in_scope=1 and listed=1"):
    if s in P2_ONLY: continue
    us = [u for k in json.loads(keys) for u in units.get(tuple(k.split("|")), [])]
    if any(u[0] == "PAID" and (u[1] or "") == "ema_paid_off_feed" for u in us): continue
    r = w.execute("select signal, work_state, appeal_block, state_basis, payer_group, carc from findings where finding_id=?", (fid,)).fetchone()
    basis = re.sub(r"\$[0-9,.]+", "$#", re.sub(r"\d{5,}", "#####", r[3] or "-"))
    basis = re.sub(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2},? ?\d{0,4}", "<day>", basis)
    basis = re.sub(r"\d{4}-\d{2}-\d{2}", "<day>", basis)[:70]
    k = (verdict, (vsub or "-")[:34], r[0][:24], r[2] or "-", basis)
    tab[k] += 1; dol[k] += amt or 0
for k, n in sorted(tab.items(), key=lambda kv: (kv[0][0], -kv[1])):
    print(f"{n:4d} ${dol[k]/100:>9,.2f}  {k[0]:14s} {k[1]:34s} {k[2]:24s} {k[3]:14s} {k[4]}")
