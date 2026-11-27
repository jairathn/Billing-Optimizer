"""Private: rule 8 PR items' facts from the store (EMA line totals, the 835 PR amounts), aggregates only."""
import json, os, sqlite3, sys
from collections import Counter, defaultdict
src, db = sys.argv[1], sys.argv[2]
for k in list(os.environ):
    if k.startswith(("SRFAX_", "DROPBOX_", "TRIZETTO_", "SMTP_", "MODMED_", "REMITWATCH_")) or k == "ANTHROPIC_BASE_URL":
        del os.environ[k]
os.environ["REMITWATCH_EMA_LEDGER"] = os.path.join(os.path.dirname(db), "ema_ledger.sqlite")
sys.path.insert(0, src)
from remitwatch import states as S
c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
c.row_factory = sqlite3.Row
items = [dict(r) for r in c.execute("""SELECT * FROM findings WHERE signal IN ('DENIED_REFERRAL','DENIED_COVERAGE_DATES','DENIED_PLAN_EXCLUSION')
                                       AND carc IN ('288','27','26','200','49','204')""")]
fx = S.facts_of(c, items, today="2026-11-24")
agg = defaultdict(lambda: Counter())
for f in items:
    x = fx[f["finding_id"]]
    tot = S._ema_line_totals(x)
    own = [r for r in x["r835"]["rows"] if r.get("line_key") == f["line_key"]]
    pr = sum(a for r in own for g, cc, a in r["adj"] if g == "PR")
    charge = sum(r["charge"] or 0 for r in own)
    paid_other = sum(r["paid"] or 0 for r in S.standing_rows(x["r835"]["rows"]) if r.get("line_key") != f["line_key"] and r.get("line_key") and r["status"] != "22")
    key = f["work_state"]
    a = agg[key]
    a["n"] += 1
    a["ema_linked"] += 1 if tot["n"] else 0
    a["ema_open>0"] += 1 if tot["n"] and tot["balance"] > 0 else 0
    a["ema_open0"] += 1 if tot["n"] and tot["balance"] <= 0 else 0
    a["pat_paid>0"] += 1 if tot["pat_paid"] > 0 else 0
    a["pat_adj>0"] += 1 if tot["pat_adj"] > 0 else 0
    a["noncontract>0"] += 1 if tot["noncontractual"] > 0 else 0
    a["pr_cents"] += pr
    a["charge_cents"] += charge
    a["shortfall_cents"] += f["shortfall_cents"] or 0
    a["pr<charge"] += 1 if pr < charge else 0
    a["paid_other>0"] += 1 if paid_other > 0 else 0
    a["ema_balance_cents"] += max(tot["balance"], 0) if tot["n"] else 0
for k, v in agg.items():
    print(k, dict(v))
