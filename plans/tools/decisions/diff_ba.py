"""Private: base vs after, item by item. Aggregates by signal and transition; ids only for the moved-item list file."""
import json, sqlite3, sys
from collections import Counter, defaultdict
base, after, out = sys.argv[1], sys.argv[2], sys.argv[3]
def load(p):
    c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return {r["finding_id"]: dict(r) for r in c.execute("SELECT * FROM findings")}
B, A = load(base), load(after)
PRC = {"288", "27", "26", "200", "49", "204"}
SIG = ("DENIED_REFERRAL", "DENIED_COVERAGE_DATES", "DENIED_PLAN_EXCLUSION")
def ev(f):
    try:
        return json.loads(f.get("evidence") or "{}")
    except ValueError:
        return {}
def pd(f):
    return f and f["signal"] in SIG and str(f["carc"]) in PRC and (ev(f).get("carc_group") == "PR")
def key(f):
    return (f["status"], f["work_state"], f["shortfall_cents"], f["recovered_cents"], f.get("appeal_block"))
moved = []
trans = defaultdict(lambda: [0, 0, 0])
only_b = [k for k in B if k not in A]
only_a = [k for k in A if k not in B]
for fid in set(B) | set(A):
    b, a = B.get(fid), A.get(fid)
    if b and a and key(b) == key(a) and (b.get("state_basis") or "")[:40] == (a.get("state_basis") or "")[:40]:
        continue
    f = a or b
    if pd(b) or pd(a):
        cls = "d2"
    elif any(x and ("second_role" in (x.get("evidence") or "") or "settled_by" in (x.get("evidence") or "")
                    or "units_split" in (x.get("evidence") or "")) for x in (b, a)):
        cls = "d3"
    else:
        cls = "other"
    t = (cls, f["signal"], (b or {}).get("work_state"), (a or {}).get("work_state"))
    trans[t][0] += 1
    trans[t][1] += (b or {}).get("shortfall_cents") or 0
    trans[t][2] += (a or {}).get("shortfall_cents") or 0
    moved.append({"fid": fid, "cls": cls, "signal": f["signal"], "carc": f["carc"], "payer_group": f["payer_group"],
                  "b": {k: (b or {}).get(k) for k in ("status", "work_state", "shortfall_cents", "recovered_cents", "appeal_block")},
                  "a": {k: (a or {}).get(k) for k in ("status", "work_state", "shortfall_cents", "recovered_cents", "appeal_block")}})
print("items base", len(B), "after", len(A), "only base", len(only_b), "only after", len(only_a), "changed", len(moved))
for t, v in sorted(trans.items(), key=lambda kv: (kv[0][0], -kv[1][0])):
    print(f"  {t} n={v[0]} short base ${v[1]/100:,.2f} after ${v[2]/100:,.2f}")
rec = Counter()
for m in moved:
    rb, ra = m["b"]["recovered_cents"] or 0, m["a"]["recovered_cents"] or 0
    if rb != ra:
        rec[(m["cls"], m["signal"])] += ra - rb
print("recovered change by class:", {k: f"${v/100:,.2f}" for k, v in rec.items()})
json.dump(moved, open(out, "w"), indent=0)
