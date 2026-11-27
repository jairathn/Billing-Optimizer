"""Private: finding ids of decision-2 items by a condition on EMA's line totals (ids and states only)."""
import os, sqlite3, sys
src, world, cond = sys.argv[1], sys.argv[2], sys.argv[3]
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
for f in items:
    t = S._ema_line_totals(fx[f["finding_id"]])
    if eval(cond, {}, {"t": t, "f": f}):
        print(f["finding_id"], f["work_state"], f["signal"], f["carc"], f["shortfall_cents"], {k: t[k] for k in ("n", "pat_paid", "pat_adj", "noncontractual", "balance", "ins_paid")})
