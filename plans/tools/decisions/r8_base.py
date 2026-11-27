"""Private: rule 8 items in a snapshot or store, aggregates only."""
import json, sqlite3, sys
from collections import Counter, defaultdict
db = sys.argv[1]
c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
c.row_factory = sqlite3.Row
cols = {r[1] for r in c.execute("PRAGMA table_info(findings)")}
rows = [dict(r) for r in c.execute("SELECT * FROM findings WHERE signal IN ('DENIED_REFERRAL','DENIED_COVERAGE_DATES','DENIED_PLAN_EXCLUSION','DENIED_CONTRACT_RATE')")]
print("rule-8 signal items:", len(rows))
agg = defaultdict(lambda: [0, 0])
for f in rows:
    ev = json.loads(f["evidence"] or "{}")
    g = ev.get("carc_group") or "?"
    rule = (ev.get("archive_rule") or {}).get("rule")
    key = (f["signal"], g, f["carc"], f.get("work_state"), f.get("status"), f.get("payer_kind"), bool(f.get("stale")), rule)
    agg[key][0] += 1
    agg[key][1] += f["shortfall_cents"] or 0
for k, v in sorted(agg.items(), key=lambda kv: (-kv[1][0])):
    print(k, v[0], f"${v[1]/100:,.2f}")
# replaced reclass carrying a PR reason
rep = Counter()
for f in c.execute("SELECT evidence, work_state, status, shortfall_cents FROM findings WHERE evidence LIKE '%pr_group_denials%' AND evidence LIKE '%replaced%'"):
    ev = json.loads(f[0] or "{}")
    ar = ev.get("archive_rule") or {}
    if ar.get("reclass") == "replaced":
        rep[(ar.get("new_reason"), f[1], f[2])] += 1
print("replaced by rule 8:", dict(rep))
