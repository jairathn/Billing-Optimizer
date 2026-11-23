"""Compare two after-states of the same catch-up (two runs of catchup.py with different code on the same base store and
files): every item whose outcome differs, grouped by how it moved, with dollars. Aggregates and finding ids only.
argv: <A1.sqlite> <A2.sqlite> [ids_out.tsv]"""
import sqlite3, sys
from collections import Counter
a1, a2 = sys.argv[1], sys.argv[2]
OPEN = ("open", "in_progress", "appealed")
def snap(p):
    c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    return {r[0]: r[1:] for r in c.execute("""SELECT finding_id, status, signal, COALESCE(shortfall_cents,0), COALESCE(stale,0),
                                                     payer_group FROM findings""")}
x, y = snap(a1), snap(a2)
def state(v):
    if v is None: return "absent"
    return ("open" if v[0] in OPEN else v[0]) + ("/stale" if v[3] else "")
moves, cents, rows = Counter(), Counter(), []
for fid in set(x) | set(y):
    s1, s2 = state(x.get(fid)), state(y.get(fid))
    if s1 == s2 and (x[fid][2] == y[fid][2]):
        continue
    sig = (y.get(fid) or x.get(fid))[1]
    k = f"{s1} -> {s2} [{sig}]"
    moves[k] += 1
    cents[k] += (y.get(fid) or x.get(fid))[2]
    rows.append((fid, s1, s2, sig, (y.get(fid) or x.get(fid))[2]))
for k, n in sorted(moves.items(), key=lambda kv: -kv[1]):
    print(f"{n:6d}  ${cents[k] / 100:>12,.2f}  {k}")
o1 = [v for v in x.values() if v[0] in OPEN and not v[3]]
o2 = [v for v in y.values() if v[0] in OPEN and not v[3]]
print(f"open items: {len(o1)} (${sum(v[2] for v in o1) / 100:,.2f}) -> {len(o2)} (${sum(v[2] for v in o2) / 100:,.2f})")
if len(sys.argv) > 3:
    with open(sys.argv[3], "w") as f:
        for r in rows: f.write("\t".join(map(str, r)) + "\n")
