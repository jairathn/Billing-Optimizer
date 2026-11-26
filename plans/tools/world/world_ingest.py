"""Read EMA's four reports (A/R 2025, A/R 2026, Production 2025, Production 2026) into the world store with push 2's own
code (the fermi tree), the way the command line reads a folder of reports (cli.py: sorted paths, ema_reports.ingest_file
with via='cli', one ledger, then ema_ledger.refresh). Everything it writes stays in the private audit folder.

Prints counts, file-level date ranges and statuses only. The full results (counts and dates only) go to world_tmp/.
"""
import json
import os
import re
import sys
import time

A = "/tmp/claude-0/-home-user-Billing-Optimizer/5f451de0-d1a3-5988-9a9a-08fbd67159f6/scratchpad/audit"
FERMI = "/tmp/claude-0/-home-user-Billing-Optimizer/5f451de0-d1a3-5988-9a9a-08fbd67159f6/scratchpad/src/fermi"
D = ("/tmp/claude-0/-home-user-Billing-Optimizer/5f451de0-d1a3-5988-9a9a-08fbd67159f6/scratchpad/data/dropbox/"
     "Trizetto Data/Trizetto/AR and Collections Reports from EMA")
TMP = os.path.join(A, "world_tmp")
LEDGER = os.path.join(A, "world_ema_ledger.sqlite")
WORLD = os.path.join(A, "world.sqlite")

for k in list(os.environ):
    if k == "ANTHROPIC_BASE_URL" or k.startswith(("SRFAX_", "DROPBOX_", "TRIZETTO_", "SMTP_", "MODMED_", "VERCEL")):
        del os.environ[k]
os.environ.update(REMITWATCH_MODE="server", REMITWATCH_EMA_REPORTS="all", REMITWATCH_EMA_RETAIN_DAYS="3650",
                  TMPDIR=TMP, REMITWATCH_TMP=TMP, SQLITE_TMPDIR=TMP, REMITWATCH_EMA_LEDGER=LEDGER)
sys.path.insert(0, FERMI)
import tempfile  # noqa: E402
tempfile.tempdir = None

from remitwatch import archive, ema_ledger as el, ema_reports as er, runlog  # noqa: E402
from remitwatch.store import Store  # noqa: E402

LOG = []
_LONG = re.compile(r"\d{7,}")


def log(*a):
    s = " ".join(str(x) for x in a)
    LOG.append(s)
    print("   log:", _LONG.sub("#######", s)[:300], flush=True)


SAFE = ("status", "report", "reason", "rows_read", "rows_kept", "rows_applied", "live_rows", "scope", "as_of", "links",
        "seconds", "source_kind", "replaced", "holds")


def main():
    names = sorted(os.listdir(D))
    print("files:", names)
    store = Store(WORLD)
    lg = el.Ledger.open(LEDGER)
    archive.attach_ledger(lg, runlog.DirSink(os.path.join(TMP, "archive")))
    results = {}
    try:
        for name in names:
            path = os.path.join(D, name)
            rec = er.recognize(path, name)
            if rec is None:
                print(name, "NOT A REPORT")
                continue
            print(f"\n== {name}: report={rec.report} fmt={rec.fmt} header_row={rec.header_index} columns={len(rec.columns)} "
                  f"dimension={rec.dimension} delimiter={rec.delimiter!r} sheet={'yes' if rec.sheet else None}")
            print("   missing:", list(rec.missing))
            print("   unused :", list(rec.unused))
            print("   read   :", sorted(rec.index))
            t0 = time.time()
            res = er.ingest_file(store, path, rec, via="cli", origin={"path": name, "audit": "world"}, ledger=lg, log=log)
            dt = round(time.time() - t0, 1)
            results[name] = json.loads(json.dumps(res, default=str))
            print("   result:", {k: res.get(k) for k in SAFE if k in res}, f"({dt} s)")
            acct = res.get("account") or {}
            print("   account:", json.dumps(acct, default=str, sort_keys=True)[:1500])
        print("\n== refresh (auto: Part A from the ledger, Part B against the 835s)")
        t0 = time.time()
        r = el.refresh(store, lg, log=log)
        results["__refresh__"] = json.loads(json.dumps(r, default=str))
        print("   refresh seconds:", round(time.time() - t0, 1))
        print("   part_a:", json.dumps(r.get("part_a"), default=str)[:1500])
        print("   part_b:", json.dumps(r.get("part_b"), default=str)[:2500])
        print("   published:", r.get("published"), "retained:", r.get("retained"), "held:", r.get("retain_held"))
    finally:
        lg.close()
        store.conn.commit()
        store.close()
    with open(os.path.join(TMP, "ingest_results.json"), "w") as fh:
        json.dump(results, fh, indent=1, default=str)
    with open(os.path.join(TMP, "ingest_log.txt"), "w") as fh:
        fh.write("\n".join(LOG))


if __name__ == "__main__":
    main()
