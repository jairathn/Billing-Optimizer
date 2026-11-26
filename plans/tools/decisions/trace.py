"""Private: one moved item before and after, then the raw facts the states use (EMA line totals, the 835 rows of its
service), with every date shown as a day offset from the service date and dates inside sentences masked."""
import json, os, re, sqlite3, sys
from datetime import date
src, world, base_snap, after_snap, fid = sys.argv[1:6]
for k in list(os.environ):
    if k.startswith(("SRFAX_", "DROPBOX_", "TRIZETTO_", "SMTP_", "MODMED_", "REMITWATCH_")) or k == "ANTHROPIC_BASE_URL":
        del os.environ[k]
os.environ["REMITWATCH_EMA_LEDGER"] = os.path.join(os.path.dirname(world), "ema_ledger.sqlite")
sys.path.insert(0, src)
from remitwatch import states as S, story
MONTHS = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2}, \d{4}"
def mask(t):
    t = re.sub(MONTHS, "<date>", str(t or ""))
    return re.sub(r"\d{4}-\d{2}-\d{2}", "<date>", t)
def snap(p):
    c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    r = c.execute("SELECT * FROM findings WHERE finding_id=?", (fid,)).fetchone()
    return dict(r) if r else None
b, a = snap(base_snap), snap(after_snap)
base_day = a["service_date"]
def off(d):
    try:
        return (date.fromisoformat(str(d)[:10]) - date.fromisoformat(str(base_day)[:10])).days
    except Exception:
        return None
for name, f in (("base", b), ("after", a)):
    print(name, f["status"], f["work_state"], "short", f["shortfall_cents"], "exp", f["expected_cents"], "rec", f["recovered_cents"],
          "block", f["appeal_block"], "|", mask(f["state_basis"])[:260])
c = sqlite3.connect(f"file:{world}?mode=ro", uri=True)
c.row_factory = sqlite3.Row
f = dict(c.execute("SELECT * FROM findings WHERE finding_id=?", (fid,)).fetchone())
ev = json.loads(f["evidence"] or "{}")
print("item", f["signal"], f["payer_group"], f["payer_kind"], "code", f["procedure_code"], "carc", f["carc"], "status", f["status"])
for k in ("patient_share", "payer_rate_cents", "payer_rate_basis", "second_role", "settled_by", "archive_rule"):
    if k in ev:
        v = ev[k]
        if isinstance(v, dict):
            v = {kk: vv for kk, vv in v.items() if kk not in ("proof", "claim", "trace", "note", "text", "date")}
        print("  ev", k, mask(json.dumps(v, default=str))[:300])
fx = S.facts_of(c, [f], today="2026-11-24")[fid]
tot = S._ema_line_totals(fx)
print("  ema", {k: tot.get(k) for k in ("n", "charge", "ins_paid", "pat_paid", "pat_adj", "noncontractual", "balance")})
for r in fx["r835"]["rows"]:
    print(f"   {'*' if r.get('line_key') == f['line_key'] else ' '} st {r.get('status')} paid_on d+{off(r.get('paid_on'))} {str(r.get('payer_name') or '')[:22]:22}"
          f" code {r.get('code')} ch {r.get('charge')} al {r.get('allowed')} pd {r.get('paid')} adj {r.get('adj')}")
st = S.state_of(c, f, today="2026-11-24")
print("  state", st.get("state"), st.get("rule"), "|", mask(st.get("basis"))[:300])
if "patient_share" in st:
    print("  patient_share", st["patient_share"])
try:
    ns = story.next_step(c, fid, today="2026-11-24")
    if ns:
        print("  next", mask(ns.get("action"))[:300], "| expected", ns.get("expected_cents"), "| buttons", ns.get("buttons"))
except Exception as e:
    print("  next_step error", type(e).__name__, mask(str(e))[:200])
