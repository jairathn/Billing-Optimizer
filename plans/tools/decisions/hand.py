"""Private: an item's raw rows read straight from the store's tables (no remitwatch code): the 835 lines of its service
with their adjustments, EMA's lines for the service, and an independent reading of the owner's decisions 2 and 3 to set
beside what the run decided. Day offsets from the service date; payer names cut short; no claim ids, names or dates."""
import json, re, sqlite3, sys
from datetime import date
world, fid = sys.argv[1], sys.argv[2]
c = sqlite3.connect(f"file:{world}?mode=ro", uri=True)
c.row_factory = sqlite3.Row
BILL = re.compile(r"(F?[A-Z]{2}\d{2}[A-Z0-9]{5,6})C\d{0,3}")
PRIMARY, SECONDARY = {"1", "19"}, {"2", "20", "3", "21"}
def bill(p):
    m = BILL.fullmatch(p or "")
    return m.group(1) if m else (p or "")
f = dict(c.execute("SELECT * FROM findings WHERE finding_id=?", (fid,)).fetchone())
svc = str(f["service_date"] or "")[:10]
def off(d):
    try:
        return (date.fromisoformat(str(d)[:10]) - date.fromisoformat(svc)).days
    except Exception:
        return None
ev = json.loads(f["evidence"] or "{}")
own_lk = f["line_key"]
own = c.execute("SELECT * FROM lines WHERE line_key=?", (own_lk,)).fetchone() if own_lk else None
b = bill(own["patient_control_number"] if own else c.execute("SELECT patient_control_number FROM claims WHERE claim_key=?",
                                                             (f["claim_key"],)).fetchone()[0])
code = f["procedure_code"]
rows = []
for r in c.execute("""SELECT l.*, t.superseded sup, t.payment_date tpay FROM lines l JOIN transactions t ON t.transaction_key=l.transaction_key
                      WHERE l.patient_control_number >= ? AND l.patient_control_number < ? AND l.procedure_code=?""", (b, b + "~", code)):
    r = dict(r)
    if bill(r["patient_control_number"]) != b or (r["service_date"] or "")[:10] != svc or r["sup"]:
        continue
    r["adj"] = [(x[0], x[1], x[2]) for x in c.execute("SELECT group_code, reason_code, amount_cents FROM adjustments WHERE line_key=?", (r["line_key"],))]
    r["cadj"] = [(x[0], x[1], x[2]) for x in c.execute("SELECT group_code, reason_code, amount_cents FROM adjustments WHERE claim_key=? AND line_key IS NULL",
                                                       (r["claim_key"],))]
    rows.append(r)
rows.sort(key=lambda r: (r["tpay"] or "", r["line_key"]))
print(f"item {fid} {f['signal']} carc {f['carc']} grp {ev.get('carc_group')} {f['payer_group']} {f['payer_kind']} code {code} "
      f"state {f['work_state']} status {f['status']} size {f['shortfall_cents']} rec {f['recovered_cents']} paid_on d+{off(f['payment_date'])}")
for r in rows:
    print(f"   {'*' if r['line_key'] == own_lk else ' '} st {r['claim_status_code']:>2} d+{off(r['tpay'])} {str(r['payer_name'] or '')[:20]:20} "
          f"ch {r['charge_cents']} al {r['allowed_cents']} pd {r['paid_cents']} adj {r['adj']}" + (f" claim-adj {r['cadj']}" if r['cadj'] else ""))
links = [x[0] for x in c.execute("SELECT DISTINCT link FROM ema_link_map WHERE bill=?", (b,))]
ema = [dict(x) for x in c.execute(f"""SELECT charges, ins_paid, contractual, noncontractual, pat_paid_joined pp, pat_adj_joined pa, pat_join,
                                             balance FROM ema_lines WHERE (bill=? OR link IN ({','.join('?' * len(links)) or 'NULL'}))
                                             AND cpt=? AND svc=?""", (b, *links, code, svc))]
for e in ema:
    print("   ema", e)
if f["signal"] in ("DENIED_REFERRAL", "DENIED_COVERAGE_DATES", "DENIED_PLAN_EXCLUSION") and ev.get("carc_group") == "PR":
    pr = sum(a for g, _c, a in (own and next(r["adj"] for r in rows if r["line_key"] == own_lk) or []) if g == "PR")
    others = sum(r["paid_cents"] or 0 for r in rows if r["line_key"] != own_lk)
    x = max(min(pr, (own["charge_cents"] or 0) - max(others, 0)), 0) if own else None
    one = ema[0] if len(ema) == 1 and ema[0]["pat_join"] != "several" else None
    pp, pa, bal, nc = ((one["pp"] or 0), (one["pa"] or 0), (one["balance"] or 0), (one["noncontractual"] or 0)) if one else (0, 0, None, 0)
    if x is None:
        want = "no own line"
    elif pp >= x > 0:
        want = "done: the patient paid it all"
    elif one and bal <= 0 and pp > 0 and (2 * pp >= x or pp + pa >= x):
        want = "done: settled on the patient's side"
    else:
        want = f"work now: bill the patient {x - pp}" + (" (after reversing a write-off)" if one and bal <= 0 and nc > 0 else "")
    print(f"   hand: PR on its line {pr}, other payers paid {others}, X {x}; EMA patient paid {pp} adjusted {pa} open {bal} "
          f"written off {nc} -> {want}")
else:
    mine = next((r for r in rows if r["line_key"] == own_lk), None)
    later = [r for r in rows if r["line_key"] != own_lk and (r["paid_cents"] or 0) > 0 and mine and (r["tpay"] or "") > (mine["tpay"] or "")
             and ((r["claim_status_code"] in PRIMARY) != (mine["claim_status_code"] in PRIMARY))]
    print(f"   hand: own answer st {mine and mine['claim_status_code']} paid {mine and mine['paid_cents']}; later in the other role: "
          + ", ".join(f"st {r['claim_status_code']} d+{off(r['tpay'])} paid {r['paid_cents']} allowed {r['allowed_cents']}" for r in later)
          + f" -> arrived {sum(r['paid_cents'] for r in later)}")
