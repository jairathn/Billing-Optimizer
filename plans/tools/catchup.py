"""Catch-up check on a private copy of the live store, with the code of TREE: analyse (state B), read the new files the way
the cycle does (dropbox_sync.ingest_new_files), analyse again (state A). Keeps both stores for tracing; prints aggregates.
argv: <tree> <base store> <workdir> <file list>"""
import json, os, resource, shutil, sqlite3, sys, time
from collections import Counter, defaultdict
tree, base, work, flist = sys.argv[1:5]
sys.path.insert(0, tree)
for k in list(os.environ):
    if k.startswith(("SRFAX_", "DROPBOX_", "TRIZETTO_", "ANTHROPIC_", "SMTP_", "MODMED_")) or k in ("REPORT_TO", "ALERT_WEBHOOK_URL", "REMITWATCH_MODE"):
        os.environ.pop(k)
from remitwatch.store import Store
from remitwatch import detect
from remitwatch.alerts import run_alerts
from remitwatch.dropbox_sync import ingest_new_files
os.makedirs(work, exist_ok=True)
B, A = os.path.join(work, "B.sqlite"), os.path.join(work, "A.sqlite")
rep = {"version": detect.DETECT_VERSION}
shutil.copyfile(base, B)
t = time.time()
s = Store(B)
if hasattr(s, "recheck_supersession"):
    s.recheck_supersession()
detect.Detector(s, run_id="B").run()
run_alerts(s.conn); s.conn.commit()
rep["B_seconds"] = round(time.time() - t, 1)
s.close()
shutil.copyfile(B, A)
paths = [l.rstrip("\n") for l in open(flist) if l.strip()]
t = time.time()
s = Store(A)
logs = []
res = ingest_new_files(s, paths, log=logs.append)
if hasattr(s, "recheck_supersession"):
    s.recheck_supersession()
rep["ingest"] = {k: v for k, v in res.items() if k != "failed"}
rep["ingest_failed_count"] = len(res.get("failed") or [])
rep["ingest_seconds"] = round(time.time() - t, 1)
rep["ingest_log_errors"] = sum(1 for l in logs if "ERROR" in str(l))
t = time.time()
detect.Detector(s, run_id="A").run()
rep["A_detect_seconds"] = round(time.time() - t, 1)
run_alerts(s.conn); s.conn.commit()
rep["feed_date"] = detect.feed_date(s.conn) if hasattr(detect, "feed_date") else None
rep["newest_payment"] = s.conn.execute("SELECT MAX(payment_date) FROM transactions WHERE COALESCE(superseded,0)=0").fetchone()[0]
rep["counts_after"] = {t: s.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("transactions", "claims", "lines", "claims_837", "lines_837", "findings")}
rep["alerts_after"] = [(r[0], r[1]) for r in s.conn.execute("SELECT kind, severity FROM alerts WHERE status!='cleared' ORDER BY kind")]
sw = s.conn.execute("SELECT detail FROM alerts WHERE kind='SWING' AND status!='cleared' ORDER BY first_seen DESC LIMIT 1").fetchone() \
    if s.conn.execute("SELECT 1 FROM sqlite_master WHERE name='alerts'").fetchone() else None
if sw:
    d = json.loads(sw[0])
    rep["swing"] = {"headline": d.get("headline"), "before": d.get("before"), "after": d.get("after"), "change": d.get("change"), "moves": d.get("moves"),
                    "files": d.get("files"), "rules_changed": d.get("rules_changed")}
s.close()
rep["peak_rss_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
rep["store_mb"] = {"B": round(os.path.getsize(B) / 2**20), "A": round(os.path.getsize(A) / 2**20)}
# what moved, item by item (ids stay in the private work folder; only aggregates are printed)
OPEN = ("open", "in_progress", "appealed")
def snap(p):
    c = sqlite3.connect(p)
    return {r[0]: r[1:] for r in c.execute("SELECT finding_id, status, signal, category, COALESCE(shortfall_cents,0), COALESCE(stale,0), payer_group FROM findings")}
b, a = snap(B), snap(A)
ob = {k: v for k, v in b.items() if v[0] in OPEN and not v[4]}
oa = {k: v for k, v in a.items() if v[0] in OPEN and not v[4]}
moves = Counter(); cents = Counter()
for k, v in ob.items():
    w = a.get(k)
    why = "gone (not in the findings)" if w is None else ("stale" if w[4] else f"now {w[0]}") if w[0] not in OPEN or w[4] else "still open"
    moves[why] += 1; cents[why] += v[3]
for k, v in oa.items():
    if k not in ob:
        why = "new item" if k not in b else f"reopened from {b[k][0]}"
        moves[why] += 1; cents[why] += v[3]
rep["open_items"] = {"B": [len(ob), sum(v[3] for v in ob.values())], "A": [len(oa), sum(v[3] for v in oa.values())]}
rep["moves"] = {k: [moves[k], cents[k]] for k in sorted(moves)}
json.dump(rep, open(os.path.join(work, "report.json"), "w"), indent=1, default=str)
print(json.dumps(rep, indent=1, default=str)[:6000])
