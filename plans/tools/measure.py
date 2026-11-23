"""Run one detection (and the alert engine) on a private copy of the live store with the code of TREE; write aggregates only.
argv: <tree> <store copy path> <out json> <label>"""
import json, os, sys, time, sqlite3
from collections import Counter, defaultdict
tree, path, out, label = sys.argv[1:5]
sys.path.insert(0, tree)
os.environ.pop("REMITWATCH_MODE", None)
for k in list(os.environ):
    if k.startswith(("SRFAX_", "DROPBOX_", "TRIZETTO_", "ANTHROPIC_", "SMTP_", "MODMED_")) or k in ("REPORT_TO", "ALERT_WEBHOOK_URL"):
        os.environ.pop(k)
from remitwatch.store import Store
from remitwatch import detect
c0 = sqlite3.connect(path)
before = {r[0]: (r[1], r[2], r[3], r[4], r[5]) for r in c0.execute(
    "SELECT finding_id, status, signal, COALESCE(shortfall_cents,0), appeal_deadline, payer_group FROM findings")}
people = {r[0] for r in c0.execute("SELECT DISTINCT finding_id FROM finding_events WHERE kind='manual'")}
sup_before = c0.execute("SELECT COUNT(*) FROM transactions WHERE COALESCE(superseded,0)=1").fetchone()[0]
c0.close()
t = time.time()
store = Store(path)
res = {"label": label, "version": detect.DETECT_VERSION}
if hasattr(store, "recheck_supersession"):
    res["recheck_supersession"] = bool(store.recheck_supersession())
res["feed_date"] = detect.feed_date(store.conn) if hasattr(detect, "feed_date") else store.conn.execute(
    "SELECT MAX(payment_date) FROM transactions WHERE superseded=0").fetchone()[0]
detect.Detector(store, run_id="measure-" + label).run()
res["detect_seconds"] = round(time.time() - t, 1)
c = store.conn
c.row_factory = None
res["superseded_before"], res["superseded_after"] = sup_before, c.execute("SELECT COUNT(*) FROM transactions WHERE COALESCE(superseded,0)=1").fetchone()[0]
after = {r[0]: (r[1], r[2], r[3], r[4], r[5]) for r in c.execute(
    "SELECT finding_id, status, signal, COALESCE(shortfall_cents,0), appeal_deadline, payer_group FROM findings WHERE COALESCE(stale,0)=0")}
OPEN = ("open", "in_progress", "appealed")
by = defaultdict(lambda: [0, 0])
for fid, (st, sig, cents, dl, pg) in after.items():
    if st in OPEN:
        by[sig][0] += 1; by[sig][1] += cents
res["open_by_signal"] = {k: v for k, v in sorted(by.items())}
res["open_total"] = [sum(v[0] for v in by.values()), sum(v[1] for v in by.values())]
st_count = Counter(v[0] for v in after.values())
res["status_counts"] = dict(st_count)
# a person's decision kept: items with a manual event whose status the run changed
changed_people = [f for f in people if f in before and f in after and before[f][0] != after[f][0]]
res["people_items"], res["people_status_changed_by_run"] = len(people), len(changed_people)
# deadlines that moved (same finding id)
moved = Counter(); moved_cents = 0; shifts = Counter(); reopened = [0, 0]
today = "2026-11-23"
for fid, a in after.items():
    b = before.get(fid)
    if not b or b[3] == a[3] or not b[3] or not a[3]:
        continue
    moved[a[4] or "?"] += 1; moved_cents += a[2]
    from datetime import date
    d = (date.fromisoformat(a[3][:10]) - date.fromisoformat(b[3][:10])).days
    shifts[d if abs(d) <= 3 else (">+3" if d > 0 else "<-3")] += 1
    if b[3][:10] < today <= a[3][:10] and a[0] in OPEN:
        reopened[0] += 1; reopened[1] += a[2]
res["deadline_moved"] = {"n": sum(moved.values()), "cents": moved_cents, "by_payer_group_top": moved.most_common(12),
                         "shift_days": {str(k): v for k, v in sorted(shifts.items(), key=lambda x: str(x[0]))}, "now_open_again": reopened}
res["ids_appeared"] = len(set(after) - set(before)); res["ids_vanished"] = len(set(before) - set(after))
try:
    from remitwatch.alerts import run_alerts
    a = run_alerts(c, today=today)
    c.commit()
    res["alerts"] = [(r[0], r[1], (r[2] or "")[:150]) for r in c.execute("SELECT kind, severity, title FROM alerts WHERE status!='cleared' ORDER BY kind")]
except Exception as exc:
    res["alerts_error"] = type(exc).__name__
store.close()
json.dump(res, open(out, "w"), indent=1, default=str)
print(label, "done", res["detect_seconds"], "s")
