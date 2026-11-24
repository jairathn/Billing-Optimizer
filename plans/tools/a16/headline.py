"""Headline tables of an auditor output (in scope = services billed since 2026-01-01): open items by verdict and
sub-kind, closed items by verdict, money left on the table by kind, provider reviews. Aggregates only.
Usage: python3 -I headline.py RESULT_DIR [VERDICT_TABLE UNITS_LOT_COLUMN]
  (for push 2's view W: headline.py a16/p2/out_p2 verdicts_w lot_w)"""
import os
import sqlite3
import sys
from collections import Counter, defaultdict

d = sys.argv[1]
vt = sys.argv[2] if len(sys.argv) > 2 else "verdicts"
lotcol = sys.argv[3] if len(sys.argv) > 3 else "left_on_table"
c = sqlite3.connect(f"file:{os.path.join(d, 'audit_result.sqlite')}?mode=ro", uri=True)
OPEN = ("open", "in_progress", "appealed")
openflag = "listed" if vt == "verdicts_w" else None


def m(x):
    return f"${x / 100:,.2f}"


P2_ONLY = {"SECONDARY_NOT_BILLED", "CREDIT_BALANCE", "EMA_DENIED", "NOT_SENT", "REMIT_NOT_POSTED",
           "EMA_DENIAL_AS_CONTRACTUAL", "FORWARDED_TO_CARRIER"}
allrows = c.execute(f"select verdict, sub, status, in_scope, item_cents, recovered_cents, kind"
                    f"{', listed' if openflag else ''}, signal from {vt}").fetchall()
rows = [r for r in allrows if r[-1] not in P2_ONLY]
p2o = [r for r in allrows if r[-1] in P2_ONLY]
if p2o:
    t = Counter(); tc = Counter()
    for r in p2o:
        is_open = (r[7] == 1) if openflag else r[2] in OPEN
        if is_open and r[3]:
            t[(r[-1], r[0])] += 1; tc[(r[-1], r[0])] += r[4] or 0
    print("PUSH-2-ONLY SIGNALS ON THE LIST, in scope (reported apart; the auditor cannot judge them unit by unit):")
    for k in sorted(t):
        print(f"  {k[0]:28s} {k[1]:15s} {t[k]:6d} {m(tc[k]):>14s}")
rows = [r[:-1] for r in rows]
ov = Counter(); ovc = Counter(); osub = Counter(); osubc = Counter(); cv = Counter(); cvc = Counter()
csub = Counter(); csubc = Counter()
for r in rows:
    verdict, sub, status, insc, item, rec, kind = r[:7]
    is_open = (r[7] == 1) if openflag else status in OPEN
    if not insc:
        continue
    s0 = (sub or "").split(";")[0]
    if verdict in ("ALREADY_DONE", "ALREADY_WORKED", "UNDECIDED"):
        s0 = s0  # unit states / reason
    if is_open:
        ov[verdict] += 1; ovc[verdict] += item or 0
        osub[(verdict, s0)] += 1; osubc[(verdict, s0)] += item or 0
    else:
        if status in OPEN:      # open in the store but off the list (view W): judged as not listed
            verdict = verdict
        cv[verdict] += 1; cvc[verdict] += rec or 0
        csub[(verdict, s0)] += 1; csubc[(verdict, s0)] += rec or 0
print("OPEN ITEMS ON THE LIST, in scope (count, item $)")
tot = 0; totc = 0
for v in ("CORRECT", "ALREADY_DONE", "ALREADY_WORKED", "DUPLICATE", "WRONG_AMOUNT", "UNDECIDED", "NOT_A_UNIT"):
    if ov[v]:
        print(f"  {v:15s} {ov[v]:6d} {m(ovc[v]):>14s}")
        tot += ov[v]; totc += ovc[v]
print(f"  {'total':15s} {tot:6d} {m(totc):>14s}")
print("  by sub-kind:")
for (v, s0), n in sorted(osub.items(), key=lambda kv: (kv[0][0], -kv[1])):
    if v == "CORRECT" and n < 5:
        continue
    print(f"    {v:15s} {s0[:60]:60s} {n:6d} {m(osubc[(v, s0)]):>14s}")
print("CLOSED ITEMS (and, for view W, open items off the list), in scope (count, recovered $)")
for v in sorted(cv):
    print(f"  {v:15s} {cv[v]:6d} {m(cvc[v]):>14s}")
print("  by sub-kind (failures):")
for (v, s0), n in sorted(csub.items(), key=lambda kv: (kv[0][0], -kv[1])):
    if v in ("CORRECT", "NOT_A_UNIT"):
        continue
    print(f"    {v:15s} {s0[:60]:60s} {n:6d} {m(csubc[(v, s0)]):>14s}")
lot = defaultdict(lambda: [set(), 0, 0])
for bill, code, comp, dos, fr, to, st, sub, owed in c.execute(
        f"select bill, code, comp, dos, unit_from, unit_to, state, sub, owed_cents_per_unit from units where {lotcol}=1 and in_scope=1"):
    k = (st, sub if st in ("NEVER_ANSWERED", "SHORT", "WORKED_WAITING", "TAKEN_BACK") else "")
    x = lot[k]
    x[0].add((bill, code, comp, dos)); x[1] += to - fr + 1; x[2] += (to - fr + 1) * (owed or 0)
print("MONEY LEFT ON THE TABLE, in scope (services, units, $)")
ts, tu, tc = set(), 0, 0
for k in sorted(lot, key=lambda k: -lot[k][2]):
    x = lot[k]
    print(f"  {k[0]:15s} {k[1][:40]:40s} {len(x[0]):6d} {x[1]:7d} {m(x[2]):>14s}")
    ts |= x[0]; tu += x[1]; tc += x[2]
print(f"  {'total':56s} {len(ts):6d} {tu:7d} {m(tc):>14s}")
den = Counter(); denc = Counter()
for carc, n, owed in c.execute(f"select carc, unit_to-unit_from+1, owed_cents_per_unit from units where {lotcol}=1 and in_scope=1 and state='DENIED'"):
    den[carc] += n; denc[carc] += n * (owed or 0)
print("  DENIED left on the table by CARC (top 10, units, $):", ", ".join(f"{k} {den[k]} {m(denc[k])}" for k, _ in denc.most_common(10)))
try:
    pr = c.execute("select count(*), sum(units_in_scope_inside), sum(gap_cents_in_scope_inside) from provider_reviews "
                   "where units_in_scope_inside>0").fetchone()
    print(f"PROVIDER-RATE REVIEW ITEMS (owner rule 4.1; payer x provider pairs with in-scope owed units inside the window): "
          f"{pr[0]} pairs, {pr[1]} units, {m(pr[2] or 0)}")
except sqlite3.OperationalError:
    pass
