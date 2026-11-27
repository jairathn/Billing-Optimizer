"""Private: check every moved item against its own rows with plain SQL (no remitwatch code). Prints counts only."""
import json
import sqlite3
import sys
from collections import Counter

world, moved = sys.argv[1], json.load(open(sys.argv[2]))
c = sqlite3.connect(f"file:{world}?mode=ro", uri=True)
c.row_factory = sqlite3.Row
boxes = [dict(r) for r in c.execute("SELECT scope, svc_lo, svc_hi, posted_lo, posted_hi, status FROM ema_report_files WHERE report='production' AND status='current'")]
def covered(day):
    return any((b["scope"] == "service" and b["svc_lo"] and b["svc_lo"] <= day <= b["svc_hi"])
               or (b["scope"] == "posted" and b["posted_lo"] and b["posted_lo"] <= day <= (b["posted_hi"] or "")) for b in boxes)
res = Counter()
for m in moved:
    f = dict(c.execute("SELECT * FROM findings WHERE finding_id=?", (m["fid"],)).fetchone())
    ev = json.loads(f["evidence"] or "{}")
    if f["work_state"] == "cant_check":
        # r1-1: no EMA line of the service (link map of its claim, same code and date) and no Production box covering its date
        n = c.execute("""SELECT COUNT(*) FROM ema_lines l JOIN ema_link_map k ON k.link = l.link
                         WHERE k.claim_id = ? AND l.cpt = ? AND l.svc = ?""",
                      (f["patient_control_number"], f["procedure_code"], (f["service_date"] or "")[:10])).fetchone()[0]
        res[("r1-1 cant_check", "no EMA line" if n == 0 else "EMA line!", "not covered" if not covered((f["service_date"] or "")[:10]) else "covered!")] += 1
    elif f["appeal_block"] == "never_payable":
        res[("r2-1 never_payable block", f["signal"], f["work_state"])] += 1
    elif (m["b"] or {}).get("shortfall_cents") != (m["a"] or {}).get("shortfall_cents"):
        rate = int(ev.get("payer_rate_cents") or 0)
        sz = f["shortfall_cents"]
        if f["work_state"] in ("work_now", "waiting") and rate and sz == rate:
            res[("r1-4 payer rate", f["work_state"], "size = evidence payer_rate_cents")] += 1
        else:
            # r1-5: the next payer's patient share on a later standing line of the same code and date caps it
            pr = c.execute("""SELECT COALESCE(SUM(a.amount_cents),0) FROM lines l JOIN adjustments a ON a.line_key = l.line_key
                              JOIN claims cl ON cl.claim_key = l.claim_key JOIN transactions t ON t.transaction_key = cl.transaction_key
                              WHERE cl.patient_control_number = ? AND l.procedure_code = ? AND l.service_date = ? AND a.group_code = 'PR'
                                AND l.line_key <> ? AND t.payment_date > ? AND t.superseded = 0 AND cl.claim_status_code <> '22'""",
                           (f["patient_control_number"], f["procedure_code"], f["service_date"], f["line_key"], f["payment_date"])).fetchone()[0]
            res[("r1-5 cap", f["work_state"], "size <= next payer's PR" if pr and sz <= pr else f"check ({sz} vs {pr})")] += 1
    else:
        res[("other", f["signal"], f["work_state"])] += 1
for k, v in sorted(res.items(), key=lambda kv: -kv[1]):
    print(v, k)
