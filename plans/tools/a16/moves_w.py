"""Items whose Work-now listing changed between two A16 outputs (view W, in scope), with the newer run's own state for
each. Aggregates only. Usage: python3 -I -B moves_w.py OLD_OUT NEW_OUT NEW_WORLD"""
import os, re, sqlite3, sys
from collections import Counter
old, new, world = sys.argv[1:4]
def load(d):
    c = sqlite3.connect(f"file:{os.path.join(d, 'audit_result.sqlite')}?mode=ro", uri=True)
    return {r[0]: r[1:] for r in c.execute("select finding_id, listed, verdict, sub, item_cents, in_scope, signal from verdicts_w")}
A, B = load(old), load(new)
w = sqlite3.connect(f"file:{world}?mode=ro", uri=True)
tab = Counter(); dol = Counter()
for fid in set(A) | set(B):
    a, b = A.get(fid), B.get(fid)
    if not ((a and a[4]) or (b and b[4])):
        continue
    la = bool(a and a[0]); lb = bool(b and b[0])
    if la == lb:
        continue
    va = a[1] if a else "absent"; vb = b[1] if b else "absent"
    r = w.execute("select signal, work_state, state_basis from findings where finding_id=?", (fid,)).fetchone()
    sig = r[0] if r else (b[5] if b else a[5])
    st = r[1] if r else "gone"
    basis = re.sub(r"\$[0-9,.]+", "$#", re.sub(r"\d{5,}", "#####", (r[2] if r else "") or "-"))
    basis = re.sub(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2},? ?\d{0,4}", "<day>", basis)
    basis = re.sub(r"\d{4}-\d{2}-\d{2}", "<day>", basis)[:60]
    k = ("off" if la else "on", va, vb, sig[:22], st, basis)
    tab[k] += 1; dol[k] += (b or a)[3] or 0
for k, n in sorted(tab.items(), key=lambda kv: (kv[0][0], kv[0][1], -kv[1])):
    print(f"{n:4d} ${dol[k]/100:>9,.2f}  {k[0]:3s} {k[1]:14s}->{k[2]:14s} {k[3]:22s} {k[4]:9s} {k[5]}")
