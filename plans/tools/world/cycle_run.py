"""Run push 2's detection (the fermi tree, claude/ecstatic-fermi-orfcgo @a84c781) on p2/world_p2.sqlite the way its
server cycle does after the readers (pipeline.run_cycle): the posting ledger's refresh (auto), the payers' answer times
from the ledger (ledger_turnaround), then run_detection (Detector.run with today fixed to the released run's day, then
activity.refresh), then the refresh after detection when detection kept new bills (META_KEEP_DIRTY).

Prints counts, dollars, signals, states and step times / memory only. Error texts of failed EMA steps (they can quote a
value) go to p2/run_private.json (mode 600) and only the step name and exception type are printed.

Usage: F5_SRC=TREE P2_DIR=PRIVATE_DIR [P2_DB=world.sqlite] python3 -I -B cycle_run.py [TODAY]
"""
import gc
import json
import os
import resource
import sys
import time

# Paths come from the environment (a private folder of your own, never a path in the repository):
#   F5_SRC  the code tree to run (a git archive or worktree of the commit to measure)
#   P2_DIR  the private folder that holds the world copy and its ema_ledger.sqlite
#   P2_DB   the world copy's file name in P2_DIR (default world.sqlite)
FERMI = os.environ.get("F5_SRC") or sys.exit("set F5_SRC to the code tree to run")
P2 = os.environ.get("P2_DIR") or sys.exit("set P2_DIR to the private folder holding the world copy")
TMP = os.path.join(P2, "tmp")
DB = os.path.join(P2, os.environ.get("P2_DB") or "world.sqlite")
LEDGER = os.path.join(P2, "ema_ledger.sqlite")
TODAY = sys.argv[1] if len(sys.argv) > 1 else "2026-11-24"

for k in list(os.environ):
    if k == "ANTHROPIC_BASE_URL" or k.startswith(("SRFAX_", "DROPBOX_", "TRIZETTO_", "SMTP_", "MODMED_", "VERCEL",
                                                   "REMITWATCH_")):
        del os.environ[k]
os.environ.update(REMITWATCH_MODE="server", REMITWATCH_EMA_REPORTS="all", TMPDIR=TMP, REMITWATCH_TMP=TMP,
                  SQLITE_TMPDIR=TMP, REMITWATCH_EMA_LEDGER=LEDGER, REMITWATCH_ARCHIVE_DIR=os.path.join(P2, "archive"),
                  REMITWATCH_DATA_DIR=os.path.join(P2, "data"))
sys.path.insert(0, FERMI)
sys.dont_write_bytecode = True
import tempfile  # noqa: E402
tempfile.tempdir = None

STEPS = []


def mem():
    out = {}
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith(("VmHWM:", "VmRSS:")):
                    out[line.split(":")[0]] = int(line.split()[1]) // 1024
    except OSError:
        pass
    out["maxrss"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024
    return out


def reset_peak():
    try:
        with open("/proc/self/clear_refs", "w") as fh:
            fh.write("5")
        return True
    except OSError:
        return False


def step(name, fn, *a, **kw):
    gc.collect()
    can = reset_peak()
    m0 = mem()
    t0 = time.time()
    err = None
    try:
        res = fn(*a, **kw)
    except Exception as exc:        # noqa: BLE001
        res = None
        err = type(exc).__name__
        PRIVATE.setdefault("step_errors", []).append({"step": name, "error": f"{type(exc).__name__}: {exc}"[:500]})
    m1 = mem()
    rec = {"step": name, "seconds": round(time.time() - t0, 1), "rss_before_mb": m0.get("VmRSS"),
           "peak_mb": m1.get("VmHWM") if can else None, "rss_after_mb": m1.get("VmRSS"), "maxrss_mb": m1["maxrss"],
           "error": err}
    STEPS.append(rec)
    print("STEP", json.dumps(rec), flush=True)
    return res


PRIVATE = {}


def main():
    from remitwatch import activity, ema_ledger
    from remitwatch.detect import DETECT_RULES, DETECT_VERSION, Detector
    from remitwatch.pipeline import ledger_turnaround
    from remitwatch.store import Store
    if os.environ.get("P2_HOOKS") == "1":
        # needs_B2 section 1: the three hook bodies, exactly (INT item 1); hook_hide stays a no-op
        from remitwatch import archive_rules as ar

        def hook_expected(self):
            self.archive_expected = ar.hook_expected(self)

        def hook_archive_rules(self):
            self.archive_summary = ar.hook_archive_rules(self)

        def hook_fold(self):
            self.archive_folded = ar.hook_fold(self)
        Detector.hook_expected, Detector.hook_archive_rules, Detector.hook_fold = hook_expected, hook_archive_rules, hook_fold
        print("archive-rule hooks wired (needs_B2 section 1)", flush=True)
    print("push 2 rules:", DETECT_RULES, DETECT_VERSION, "today", TODAY, flush=True)
    t_all = time.time()
    store = Store(DB)
    lg = step("ledger_open", ema_ledger.open_for_store, DB)
    r = step("ema_refresh", ema_ledger.refresh, store, lg, log=lambda *a: None)
    if isinstance(r, dict):
        print("refresh:", {k: (bool(v) if k in ("part_a", "part_b") else v) for k, v in r.items()
                           if k in ("part_a", "part_b", "seconds", "published", "retained", "retain_held")}, flush=True)
    tat = step("ema_turnaround", ledger_turnaround, store, lg)
    print("turnaround:", tat, flush=True)
    store.close()

    def detect():
        st = Store(DB)
        try:
            det = Detector(st, run_id="P2", today=TODAY)
            summary = det.run()
            PRIVATE["ema_errors"] = det.ema_errors
            info = {"ema_on": det.ema_on, "ema_counts": dict(det.ema_counts),
                    "archive": {k: getattr(det, k, None) for k in ("archive_expected", "archive_summary", "archive_folded")},
                    "ema_error_steps": [(e.get("step"), (e.get("error") or "").split(":")[0]) for e in det.ema_errors],
                    "summary": summary}
            del det
            gc.collect()
            return info
        finally:
            st.close()
    info = step("detect", detect)

    def act():
        st = Store(DB)
        try:
            return activity.refresh(st, log=lambda *a: None)
        finally:
            st.close()
    step("activity_refresh", act)
    store = Store(DB)
    keep = store.conn.execute("SELECT 1 FROM meta WHERE key=?", (ema_ledger.META_KEEP_DIRTY,)).fetchone()
    if keep:
        r2 = step("ema_refresh_after", ema_ledger.refresh, store, lg, log=lambda *a: None)
        if isinstance(r2, dict):
            print("refresh after:", {k: bool(v) for k, v in r2.items() if k in ("part_a", "part_b")}, flush=True)
    store.conn.commit()
    store.close()
    lg.close()
    total = round(time.time() - t_all, 1)
    print("TOTAL seconds", total, "process maxrss MB", resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024)
    if info:
        print("ema_on:", info["ema_on"])
        print("ema_error_steps:", info["ema_error_steps"])
        print("ema_counts:", json.dumps(info["ema_counts"], sort_keys=True))
        print("summary:", json.dumps(info["summary"], sort_keys=True))
    out = {"today": TODAY, "steps": STEPS, "total_seconds": total,
           "maxrss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024,
           "info": {k: v for k, v in (info or {}).items()}}
    with open(os.path.join(P2, "run_stats.json"), "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True, default=str)
    os.chmod(os.path.join(P2, "run_stats.json"), 0o600)
    with open(os.path.join(P2, "run_private.json"), "w") as fh:
        json.dump(PRIVATE, fh, indent=1, default=str)
    os.chmod(os.path.join(P2, "run_private.json"), 0o600)


if __name__ == "__main__":
    main()
