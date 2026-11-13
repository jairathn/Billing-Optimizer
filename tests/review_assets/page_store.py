"""An invented store for the page check: payer and plan names, a claim ID and a biller's note that carry HTML, a routine
second rate (plan reason) for that payer, a pattern with one window passed and one open, and a pattern whose evidence
comes from an earlier release (no deadlines). Usage: python3 page_store.py <dir>"""
import json
import pathlib
import sys
from datetime import date, timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from tests.test_features import build_store, svc  # noqa: E402
from remitwatch.detect import Detector  # noqa: E402
from remitwatch.actions import apply_status  # noqa: E402

work = pathlib.Path(sys.argv[1])
work.mkdir(parents=True, exist_ok=True)
for p in list(work.glob("*.RMT")) + list(work.glob("s.sqlite*")):
    p.unlink()

DOC = "1555555551"
EVIL_PAYER = "<img src=x onerror=alert(1) id=payerx>"
EVIL_PLAN = "<b id=planx onmouseover=alert(2)>GOLD</b>"


def claim(i, code, charge, allowed, *, cfi="12", dos="20260401", plan="PPO", status="1", pcn=None, who=None, paid=None):
    pcn = pcn or f"CB{i:07d}C017"
    who = who or f"SAMPLE*PAT****MI*M{i:08d}"
    paid = allowed if paid is None else paid
    co = f"{float(charge) - float(allowed):.2f}"
    cas = f"CAS*CO*45*{co}~" if float(paid) > 0 else f"CAS*CO*16*{charge}~"
    return (f"CLP*{pcn}*{status}*{charge}*{paid}*0.00*{cfi}*PC{i}*11~NM1*QC*1*{who}~NM1*82*1*DOCTOR*ALEX****XX*{DOC}~REF*CE*{plan}~"
            f"DTM*232*{dos}~" + svc(code, charge, paid, allowed if float(paid) > 0 else None, cas, dos=dos))


f = lambda d: d.strftime("%Y%m%d")
today = date.today()
jan = date(today.year, 1, 5)
# the clinician is a physician (Medicare visits)
build_store(work, [claim(i, "99214", "223.65", "127.80", cfi="MB", dos=f(jan)) for i in range(1, 26)], payer="KY MEDICARE", pay_date=f(jan + timedelta(days=10))).close()
# a payer whose name is HTML: usual $117.60, then $90.00 under a plan whose name is HTML (plan reason)
build_store(work, [claim(100 + i, "99214", "223.65", "117.60", dos=f(jan)) for i in range(25)]
            + [claim(200 + i, "99214", "223.65", "90.00", plan=EVIL_PLAN, dos=f(jan)) for i in range(6)], payer=EVIL_PAYER, pay_date=f(jan + timedelta(days=10))).close()
# Aetna: usual $700 for 17311 in January, then $400 (a rate change) on visits paid 190 days ago (window passed) and 20 days ago
old_pay, new_pay = today - timedelta(days=190), today - timedelta(days=20)
build_store(work, [claim(300 + i, "17311", "1200.00", "700.00", dos=f(jan)) for i in range(25)], payer="AETNA", pay_date=f(jan + timedelta(days=10))).close()
build_store(work, [claim(400 + i, "17311", "1200.00", "400.00", dos=f(old_pay - timedelta(days=10))) for i in range(3)], payer="AETNA", pay_date=f(old_pay)).close()
build_store(work, [claim(500 + i, "17311", "1200.00", "400.00", dos=f(new_pay - timedelta(days=10))) for i in range(3)], payer="AETNA", pay_date=f(new_pay)).close()
# a denial, and the same name paid on another member number under a claim ID that is HTML: a "Possibly paid on claim" hint
build_store(work, [claim(600, "99214", "223.65", "0.00", paid="0.00", who="SMITH*JOHN****MI*W11100001", dos=f(jan))], payer="ANTHEM", pay_date=f(jan + timedelta(days=10))).close()
store = build_store(work, [claim(601, "99214", "223.65", "117.60", who="SMITH*JOHN****MI*W11100002", dos=f(jan), pcn="<i id=pcnx>9</i>")],
                    payer="HUMANA", pay_date=f(jan + timedelta(days=40)))
# a remittance that withholds an overpayment recovery ($600, PLB WO) and a payment-vendor fee ($15): TAKEBACK and FEE_SKIM
from tests.test_remitwatch import remit  # noqa: E402
plb_file = work / "plb_only.RMT"
plb_file.write_bytes(remit(claim(800, "99214", "223.65", "117.60", dos=f(jan)), "0.00", payer="AETNA", seg_count=99, isa_ctrl="000000777",
                           pay_date=f(today - timedelta(days=15)), plb="PLB*999999999*20261231*WO:OVP-2026-0042*600.00*CS:ZELIS TRANSACTION FEE*15.00~"))
store.ingest_835_paths([str(plb_file)])
Detector(store, run_id="page").run()
# a biller's note that is HTML, on the plan pattern
fid = store.query("SELECT finding_id FROM findings WHERE signal='SECOND_RATE' AND title LIKE '%99214%'")[0][0]
apply_status(store.conn, fid, "in_progress", "<img src=y onerror=alert(3) id=notex>", actor="<b id=actorx>biller</b>")
# an earlier release's evidence on the Aetna pattern: no deadlines, three-column claims
aid, ev = store.query("SELECT finding_id, evidence FROM findings WHERE signal='SECOND_RATE' AND title LIKE '%17311%'")[0]
ev = json.loads(ev)
legacy = {k: v for k, v in ev.items() if k != "deadlines"}
legacy["claims"] = [c[:3] for c in ev["claims"]]
cols = [r[1] for r in store.conn.execute("PRAGMA table_info(findings)")]
row = dict(zip(cols, store.conn.execute("SELECT * FROM findings WHERE finding_id=?", (aid,)).fetchone()))
row.update(finding_id="legacy00000000000000aaaa", evidence=json.dumps(legacy), status="appealed", stale=1, hint=None,
           title="(earlier release) " + row["title"], appeal_deadline=None, category="contract")
store.conn.execute(f"INSERT INTO findings ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [row[c] for c in cols])
store.conn.commit()
rows = store.query("SELECT finding_id, signal, status, title, hint, appeal_deadline FROM findings ORDER BY signal")
print(json.dumps({"rows": [dict(r) for r in rows], "aetna": aid, "aetna_evidence": ev, "legacy_evidence": legacy, "plan_fid": fid}, default=str))
store.close()
