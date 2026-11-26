"""Private: decision 4 on a world after a run (aggregates only: payer groups, codes, verdicts, counts, dollars)."""
import json, sqlite3, sys
from collections import Counter, defaultdict
tree, world = sys.argv[1], sys.argv[2]
sys.path.insert(0, tree)
from remitwatch import rarely_paid as rp, story
c = sqlite3.connect(f"file:{world}?mode=ro", uri=True)
c.row_factory = sqlite3.Row
meta = json.loads((c.execute("SELECT value FROM meta WHERE key=?", (rp.WHY_META,)).fetchone() or ["{}"])[0])
pairs = meta.get("pairs") or {}
print("evidence pairs:", len(pairs), "at", meta.get("at"))
for k, ev in sorted(pairs.items(), key=lambda kv: -(kv[1].get("unpaid") or 0)):
    ema = ev.get("ema") or {}
    print(f"  {k}: answered {ev.get('answered')} paid {ev.get('paid')} by_year {ev.get('by_year')} recoded {ev.get('recoded')} "
          f"top reasons {ev.get('reasons', [])[:3]} remarks {ev.get('remarks', [])[:3]} other_line_paid {ev.get('other_line_paid')}"
          + (f" | EMA {ema.get('answered')}/{ema.get('paid')} {ema.get('reasons', [])[:2]}" if ema else ""))
pp = json.loads((c.execute("SELECT value FROM meta WHERE key='ema:payer_policy'").fetchone() or ["{}"])[0]).get("patterns") or {}
print("owner's table, payer-policy patterns:", len(pp))
for k, p in sorted(pp.items(), key=lambda kv: -(kv[1].get("cents") or 0)):
    print(f"  {k}: n {p.get('n')} ${(p.get('cents') or 0)/100:,.2f} paid/answered {p.get('paid')}/{p.get('answered')} decision {p.get('decision')} default {p.get('default')} researched {p.get('researched')}")
rows = [dict(r) for r in c.execute("SELECT * FROM findings WHERE COALESCE(stale,0)=0")]
agg = defaultdict(lambda: [0, 0, 0, 0])
lead = Counter()
verdicts = Counter()
per_item = Counter()
for f in rows:
    if not rp.item_codes(f):
        continue
    blocks = rp.why(c, f)
    if not blocks:
        continue
    per_item[len(blocks)] += 1
    for b in blocks:
        k = (b["payer_group"], b["code"], b["verdict"] or "not researched", b["period"] or "")
        a = agg[k]
        a[0] += 1; a[1] += f.get("shortfall_cents") or 0
        if f.get("work_state") == "work_now":
            a[2] += 1; a[3] += f.get("shortfall_cents") or 0
        verdicts[(b["verdict"] or "not researched", f.get("work_state"))] += 1
    if any(b["verdict"] == "never_payable" and b["single"] for b in blocks) and f.get("work_state") == "work_now":
        lead[f["signal"]] += 1
tot = [0, 0, 0, 0]
print("items with a 'Why' block, by payer group, code, verdict, period:")
for k, v in sorted(agg.items(), key=lambda kv: -kv[1][1]):
    print(f"  {k}: {v[0]} items ${v[1]/100:,.2f}; work now {v[2]} ${v[3]/100:,.2f}")
    for i in range(4): tot[i] += v[i]
print("TOTAL blocks", tot[0], f"${tot[1]/100:,.2f}", "work now", tot[2], f"${tot[3]/100:,.2f}")
print("items by number of blocks:", dict(per_item))
print("verdict x state:", sorted(verdicts.items(), key=lambda kv: -kv[1]))
print("work-now items whose next step a never-payable rule leads, by signal:", dict(lead), "total", sum(lead.values()))
