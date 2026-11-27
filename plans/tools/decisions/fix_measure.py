"""Private: the fix round's aggregates on a world after a cycle (counts, dollars, rules, codes, payer groups only).
Usage: python3 -I -B fix_measure.py <tree> <world.sqlite> <today>"""
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict

tree, db, today = sys.argv[1], sys.argv[2], sys.argv[3]
for k in list(os.environ):
    if k == "ANTHROPIC_BASE_URL" or k.startswith(("SRFAX_", "DROPBOX_", "TRIZETTO_", "SMTP_", "MODMED_", "REMITWATCH_")):
        del os.environ[k]
os.environ["REMITWATCH_MODE"] = "server"
sys.path.insert(0, tree)
from remitwatch import states as S  # noqa: E402

c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
c.row_factory = sqlite3.Row
items = [dict(r) for r in c.execute("SELECT * FROM findings WHERE COALESCE(stale,0)=0 AND status IN ('open','in_progress','appealed')")]
pd = [f for f in items if S.patient_denial(f)]
print("open patient-group denials:", len(pd), "dollars", sum(f["shortfall_cents"] or 0 for f in pd) / 100)
fx = S.facts_of(c, pd, today=today) if pd else {}
rules = defaultdict(lambda: [0, 0])
capped = [0, 0]
co_only = 0
paid_no_pr = 0
for f in pd:
    x = fx.get(f["finding_id"])
    if not x:
        continue
    st = S.decide(x, today, c)
    rules[(st.get("work_state"), st.get("rule"))][0] += 1
    rules[(st.get("work_state"), st.get("rule"))][1] += f["shortfall_cents"] or 0
    nxt = S.next_payer_answer(x)
    if nxt:
        share = S.patient_share(x)
        own_pr = sum(a for _g, _c, a in share["pr"])
        uncapped = max(min(own_pr, share["charge"] - share["paid"]), 0)
        if share["cents"] < uncapped:
            capped[0] += 1
            capped[1] += uncapped - share["cents"]
        if nxt["pr"] <= 0 and nxt["paid"] <= 0 and nxt["co"] > 0:
            co_only += 1
        if nxt["pr"] <= 0 and nxt["paid"] > 0:
            paid_no_pr += 1
print("live state and rule of the open patient-group denials (count, stored dollars):")
for k, v in sorted(rules.items(), key=lambda kv: -kv[1][0]):
    print("  ", k, v[0], v[1] / 100)
print("share capped by the next payer's patient share:", capped[0], "items, $", capped[1] / 100, "less")
print("next payer contractual only (caution kept):", co_only, "; next payer paid with nothing on the patient:", paid_no_pr)
print("stored states of open patient-group denials:", Counter((f["work_state"]) for f in pd))
blocks = Counter(r[0] for r in c.execute("SELECT appeal_block FROM findings WHERE COALESCE(stale,0)=0 AND status IN ('open','in_progress') AND work_state='work_now'"))
print("work-now appeal blocks:", dict(blocks))
sr = [dict(r) for r in c.execute("SELECT shortfall_cents, evidence FROM findings WHERE COALESCE(stale,0)=0 AND status IN ('open','in_progress','appealed') AND evidence LIKE '%second_role%'")]
n_sr = [x for x in sr if "second_role" in (json.loads(x["evidence"] or "{}"))]
capped_sr = [x for x in n_sr if (json.loads(x["evidence"])["second_role"] or {}).get("left_by_others_cents") is not None]
print("open items with a second-role remainder:", len(n_sr), "$", sum(x["shortfall_cents"] or 0 for x in n_sr) / 100,
      "; of them capped by what the others left:", len(capped_sr))
g = [dict(r) for r in c.execute("SELECT payer_group, work_state, shortfall_cents, appeal_block FROM findings WHERE COALESCE(stale,0)=0 AND status IN ('open','in_progress') AND procedure_code='G6001'")]
gg = defaultdict(lambda: [0, 0, 0])
for r in g:
    k = (r["payer_group"], r["work_state"])
    gg[k][0] += 1
    gg[k][1] += r["shortfall_cents"] or 0
    gg[k][2] += 1 if r["appeal_block"] == "never_payable" else 0
print("open G6001 items by payer group and state (n, $, never-payable blocks):")
for k, v in sorted(gg.items(), key=lambda kv: -kv[1][0])[:12]:
    print("  ", k, v[0], v[1] / 100, v[2])
