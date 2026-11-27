"""Private: decision-2 items after the run, by state, with EMA's line totals (aggregates only)."""
import json, os, sqlite3, sys
from collections import Counter, defaultdict
src, world, moved = sys.argv[1], sys.argv[2], sys.argv[3]
for k in list(os.environ):
    if k.startswith(("SRFAX_", "DROPBOX_", "TRIZETTO_", "SMTP_", "MODMED_", "REMITWATCH_")) or k == "ANTHROPIC_BASE_URL":
        del os.environ[k]
os.environ["REMITWATCH_EMA_LEDGER"] = os.path.join(os.path.dirname(world), "ema_ledger.sqlite")
sys.path.insert(0, src)
from remitwatch import states as S
c = sqlite3.connect(f"file:{world}?mode=ro", uri=True)
c.row_factory = sqlite3.Row
items = [dict(r) for r in c.execute("SELECT * FROM findings WHERE signal IN ('DENIED_REFERRAL','DENIED_COVERAGE_DATES','DENIED_PLAN_EXCLUSION')")]
items = [f for f in items if S.patient_denial(f)]
fx = S.facts_of(c, items, today="2026-11-24")
mv = {m["fid"]: m for m in json.load(open(moved))}
agg = defaultdict(Counter)
yrs = Counter()
for f in items:
    x = fx[f["finding_id"]]
    t = S._ema_line_totals(x)
    k = (f["work_state"], "moved" if f["finding_id"] in mv else "same", mv.get(f["finding_id"], {}).get("b", {}).get("work_state"))
    a = agg[k]
    a["n"] += 1
    a["size"] += f["shortfall_cents"] or 0
    a["ema_line"] += 1 if t["n"] else 0
    a["bal>0"] += 1 if t["n"] and t["balance"] > 0 else 0
    a["bal>=size"] += 1 if t["n"] and t["balance"] >= (f["shortfall_cents"] or 0) else 0
    a["bal<=0"] += 1 if t["n"] and t["balance"] <= 0 else 0
    a["pat_paid>0"] += 1 if t["pat_paid"] > 0 else 0
    a["pat_paid>=size"] += 1 if t["pat_paid"] > 0 and t["pat_paid"] >= (f["shortfall_cents"] or 0) else 0
    a["pat_adj>0"] += 1 if t["pat_adj"] > 0 else 0
    a["noncontract>0"] += 1 if t["noncontractual"] > 0 else 0
    a["ins_paid>0"] += 1 if t["ins_paid"] > 0 else 0
    if f["work_state"] == "work_now":
        yrs[(f["service_date"] or "")[:4]] += 1
for k, v in sorted(agg.items(), key=lambda kv: str(kv[0])):
    print(k, dict(v))
print("work_now service years", sorted(yrs.items()))
