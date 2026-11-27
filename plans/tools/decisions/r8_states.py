"""Private: rule 8 PR items: state rules, service years, EMA line totals (aggregates only)."""
import json, sqlite3, sys
from collections import Counter, defaultdict
db = sys.argv[1]
c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
c.row_factory = sqlite3.Row
rows = [dict(r) for r in c.execute("""SELECT * FROM findings WHERE signal IN ('DENIED_REFERRAL','DENIED_COVERAGE_DATES','DENIED_PLAN_EXCLUSION')
                                      AND carc IN ('288','27','26','200','49','204')""")]
by = defaultdict(lambda: [0, 0])
yrs = Counter()
for f in rows:
    by[(f["work_state"], f.get("state_rule") if "state_rule" in f else None, (f.get("state_basis") or "")[:60])][0] += 1
    yrs[(f["work_state"], (f["service_date"] or "")[:4])] += 1
for k, v in sorted(by.items(), key=lambda kv: -kv[1][0]):
    print(v[0], k)
print(sorted(yrs.items()))
print([k for k in rows[0].keys()][:200])
