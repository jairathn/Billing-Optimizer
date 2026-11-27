"""Private: every work-now item whose next step a never-payable rule now leads, checked against its raw rows (counts and
finding ids only). The list comes from the run; each check reads the item's own line, claim and adjustments with SQL."""
import json, sqlite3, sys, random
from collections import Counter
tree, world = sys.argv[1], sys.argv[2]
sys.path.insert(0, tree)
from remitwatch import rarely_paid as rp
c = sqlite3.connect(f"file:{world}?mode=ro", uri=True)
c.row_factory = sqlite3.Row
items = [dict(r) for r in c.execute("SELECT * FROM findings WHERE COALESCE(stale,0)=0 AND work_state='work_now'")]
led = []
for f in items:
    bl = [b for b in rp.why(c, f) if b["verdict"] == "never_payable" and b["single"]]
    if bl:
        led.append((f, bl[0]))
print("work-now items led by a never-payable rule:", len(led))
ok, bad = Counter(), []
for f, b in led:
    e = rp.entry_for(b["payer_group"], b["code"], f.get("service_date"))
    if f.get("line_key"):
        ln = c.execute("""SELECT l.procedure_code, l.original_procedure_code, COALESCE(l.service_date, cl.statement_from), l.paid_cents,
                                 l.payer_name, cl.claim_filing_indicator, l.claim_status_code
                          FROM lines l JOIN claims cl ON cl.claim_key=l.claim_key WHERE l.line_key=?""", (f["line_key"],)).fetchone()
        code, orig, svc, paid, payer, cfi, st = ln
        billed = orig if (orig and orig != code) else code
        adj = [r[0] + "-" + r[1] for r in c.execute("SELECT group_code, reason_code FROM adjustments WHERE line_key=? AND amount_cents<>0", (f["line_key"],))]
        checks = {"billed code is the rule's": billed == b["code"], "nothing paid on the line": (paid or 0) == 0,
                  "service date inside the rule's period": rp._in(svc, (e or {}).get("period", {}).get("from") if e and e.get("period") else None,
                                                                        (e or {}).get("period", {}).get("to") if e and e.get("period") else None) if e else False,
                  "the payer group is the rule's": e is not None and rp.applies(e, True)}
        kind = f"{f['signal']} {f['payer_group']} {b['code']} {'recoded ' + code if billed != code else ''} {'/'.join(sorted(set(adj)))[:40]}"
    else:
        ev = json.loads(f.get("evidence") or "{}")
        lines = ev.get("lines") or []
        checks = {"one EMA line of the rule's code": len(lines) == 1 and lines[0][0] == b["code"],
                  "service date inside the rule's period": bool(e) and rp._in(lines[0][1], (e.get("period") or {}).get("from"), (e.get("period") or {}).get("to")),
                  "no insurance payment in EMA on the line": not (c.execute("SELECT COALESCE(SUM(ins_paid),0) FROM ema_lines WHERE link=? AND cpt=?", (f.get("ema_link"), b["code"])).fetchone()[0] or 0)}
        kind = f"{f['signal']} {f['payer_group']} {b['code']} {lines[0][3] if lines else ''}"
    for k, v in checks.items():
        ok[(k, v)] += 1
    if not all(checks.values()):
        bad.append((f["finding_id"], kind, [k for k, v in checks.items() if not v]))
    ok[("kind", kind.split(' CO-')[0].split(' OA-')[0][:60])] += 1
for k, v in sorted(ok.items(), key=lambda kv: (str(kv[0][0]), -kv[1])):
    print(" ", k, v)
print("items failing a check:", len(bad))
for x in bad[:15]:
    print("  ", x)
