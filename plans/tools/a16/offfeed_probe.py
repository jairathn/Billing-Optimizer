"""ALREADY_DONE items on Work now (in scope) whose units A16 calls paid off-feed by EMA's books: cross-tabs of the merged
code's own fields. Aggregates only (counts and dollars, codes, payer groups).
Usage: python3 -I -B offfeed_probe.py RESULT_DIR WORLD_DB"""
import json, os, re, sqlite3, sys
from collections import Counter
d, world = sys.argv[1], sys.argv[2]
c = sqlite3.connect(f"file:{os.path.join(d, 'audit_result.sqlite')}?mode=ro", uri=True)
w = sqlite3.connect(f"file:{world}?mode=ro", uri=True)
units = {}
for r in c.execute("select bill, code, comp, dos, state, sub, ema_how, ema_ins_paid, ema_status, carc from units where in_scope=1"):
    units.setdefault(r[:4], []).append(r[4:])
sel = []
for fid, s, amt, keys in c.execute("select finding_id, signal, item_cents, svc_keys from verdicts_w where verdict='ALREADY_DONE' and in_scope=1 and listed=1"):
    us = [u for k in json.loads(keys) for u in units.get(tuple(k.split("|")), [])]
    if any(u[0] == "PAID" and (u[1] or "") == "ema_paid_off_feed" for u in us):
        sel.append((fid, s, amt, us))
print("items", len(sel), "dollars", sum(x[2] or 0 for x in sel) / 100)
F = {}
q = ("select finding_id, signal, work_state, appeal_block, hint, ema_status, ema_status_src, ema_outcome, payer_group, "
     "ema_link, state_basis, status, paid_cents, carc, source from findings where finding_id=?")
tabs = {k: Counter() for k in ("signal", "work_state", "block", "hint", "ema_status", "ema_outcome", "payer_group",
                               "has_link", "basis", "how", "a16_status", "carc", "source")}
dol = {k: Counter() for k in tabs}
def add(k, v, a):
    tabs[k][v] += 1; dol[k][v] += a or 0
for fid, s, amt, us in sel:
    r = w.execute(q, (fid,)).fetchone()
    if not r:
        add("signal", "missing", amt); continue
    add("signal", r[1], amt); add("work_state", r[2], amt); add("block", r[3] or "-", amt)
    h = (r[4] or "-")
    h = re.sub(r"[0-9$.,/]+", "#", h)  # every digit masked[:40]
    add("hint", h, amt); add("ema_status", (r[5] or "-")[:30], amt); add("ema_outcome", (r[7] or "-")[:30], amt)
    add("payer_group", r[8] or "-", amt); add("has_link", "link" if r[9] else "no_link", amt)
    add("basis", re.sub(r"[0-9$.,/-]+", "#", (r[10] or "-"))[:50], amt)  # every digit masked
    add("how", ",".join(sorted({(u[2] or "-") for u in us})), amt)
    add("a16_status", ",".join(sorted({(u[4] or "-")[:20] for u in us})), amt)
    add("carc", r[13] or "-", amt); add("source", r[14] or "-", amt)
for k in tabs:
    print(f"== {k}")
    for v, n in tabs[k].most_common(14):
        print(f"   {n:4d} ${dol[k][v]/100:>10,.2f}  {v}")
