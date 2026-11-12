"""
Run the serverless cycle against a local folder standing in for Dropbox, one invocation per process, and report the time
and peak memory of each, the way a serverless platform would run it.

    python scripts/cloud_bench.py --dropbox /path/to/folder-that-contains-Trizetto-Data --budget 240

The folder is treated as the Dropbox root: it must contain the data folder (default "Trizetto Data"). State is written
to <dropbox>/RemitWatch-state.
"""
from __future__ import annotations

import argparse
import json
import os
import resource
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def one(dropbox: str, budget: int, force: bool) -> dict:
    from remitwatch.cloud import run_cloud_cycle
    from remitwatch.state import DropboxState
    from tests.fakes import FakeDropbox
    client = FakeDropbox(dropbox)
    return run_cloud_cycle(DropboxState(client, "/RemitWatch-state"), client, budget_s=budget, trigger="bench", email=False,
                           force_detect=force, log=lambda *a: None)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dropbox", required=True)
    ap.add_argument("--budget", type=int, default=240)
    ap.add_argument("--max-runs", type=int, default=12)
    ap.add_argument("--one", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    if a.one:
        print(json.dumps(one(a.dropbox, a.budget, a.force), default=str))
        return
    for i in range(a.max_runs):
        t = time.time()
        before = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
        p = subprocess.run([sys.executable, __file__, "--dropbox", a.dropbox, "--budget", str(a.budget), "--one"] + (["--force"] if a.force and i == 0 else []),
                           capture_output=True, text=True)
        peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
        if p.returncode != 0:
            print(f"run {i + 1} FAILED:\n{p.stderr[-1500:]}")
            break
        out = json.loads(p.stdout.strip().splitlines()[-1])
        ing = out.get("ingest") or {}
        print(f"run {i + 1}: {time.time() - t:6.1f}s wall, peak {max(peak, before) / 1024:6.0f} MB | load {out.get('load_seconds')}s | "
              f"ingest remits={ing.get('remits')} ema={ing.get('ema')} 837={ing.get('claims_837')} archive={ing.get('from_archive')} "
              f"downloaded={ing.get('downloaded')} remaining={ing.get('remaining')} | detect={out.get('detect')} deferred={out.get('deferred')} | "
              f"serving={out.get('serving')} store={out.get('store')} | memory={out.get('memory_mb')} | more={out.get('more')} "
              f"progress={out.get('progress')} totals={out.get('totals')}", flush=True)
        if not out.get("more"):
            break


if __name__ == "__main__":
    main()
