"""Serverless-style memory emulation of one detection run: the store image is read into memory the way cloud.load_raw
does (Store.from_bytes: a compact in-memory store), then Detector.run on it; nothing is written to disk (the in-memory
store is thrown away). Prints only step times and memory (VmHWM / VmRSS, MB) and the item count.

Usage: python3 -I -B p2_serverless.py CODE_TREE IMAGE_PATH TODAY
"""
import gc
import json
import os
import resource
import sys
import time

TREE, IMAGE, TODAY = sys.argv[1], sys.argv[2], sys.argv[3]
A = "/tmp/claude-0/-home-user-Billing-Optimizer/5f451de0-d1a3-5988-9a9a-08fbd67159f6/scratchpad/audit"
TMP = os.path.join(A, "p2", "tmp")
for k in list(os.environ):
    if k == "ANTHROPIC_BASE_URL" or k.startswith(("SRFAX_", "DROPBOX_", "TRIZETTO_", "SMTP_", "MODMED_", "VERCEL",
                                                   "REMITWATCH_")):
        del os.environ[k]
os.environ.update(REMITWATCH_MODE="serverless", REMITWATCH_EMA_REPORTS="all", TMPDIR=TMP, REMITWATCH_TMP=TMP,
                  SQLITE_TMPDIR=TMP)
sys.dont_write_bytecode = True
sys.path.insert(0, TREE)


def mem():
    out = {}
    with open("/proc/self/status") as fh:
        for line in fh:
            if line.startswith(("VmHWM:", "VmRSS:")):
                out[line.split(":")[0]] = int(line.split()[1]) // 1024
    return out


def main():
    from remitwatch.detect import DETECT_VERSION, Detector
    from remitwatch.store import Store
    t0 = time.time()
    with open(IMAGE, "rb") as fh:
        data = bytearray(fh.read())
    if data[18:20] == b"\x02\x02":     # a WAL-mode file copy: the serverless image is saved in rollback mode
        data[18:20] = b"\x01\x01"
    data = bytes(data)
    store = Store.from_bytes(data)
    del data
    gc.collect()
    m_load = mem()
    t1 = time.time()
    with open("/proc/self/clear_refs", "w") as fh:
        fh.write("5")
    m_base = mem()
    try:
        det = Detector(store, run_id="SV", today=TODAY)
    except TypeError:           # the released code has no today argument (it uses the practice's day)
        det = Detector(store, run_id="SV")
    summary = det.run()
    m_det = mem()
    t2 = time.time()
    out = {"version": DETECT_VERSION, "load_seconds": round(t1 - t0, 1), "load_peak_mb": m_load["VmHWM"],
           "rss_after_load_mb": m_base["VmRSS"], "detect_seconds": round(t2 - t1, 1),
           "detect_peak_mb": m_det["VmHWM"], "items": sum(summary.values()),
           "ema_errors": [(e.get("step"), (e.get("error") or "").split(":")[0]) for e in getattr(det, "ema_errors", [])]}
    print(json.dumps(out))


if __name__ == "__main__":
    main()
