"""The Data tab in a browser (Playwright, r6cov_page.js) on three invented stores: the full store this release analysed,
its dashboard copy (the serverless copy keeps only the lines behind findings) and a store the live release (6dcc450)
saved. The panel "EMA charge exports that miss visits" lists the weeks the record holds, from the copy as from the full
store; on the live store the tab has no such panel and no error; nothing overflows at phone width. Skipped where node or
Playwright is missing. Invented data only."""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from r6cov_h import HERE, PW, REPO, live_tree, run_tree  # noqa: E402

from remitwatch import cloud  # noqa: E402
from remitwatch.detect import run_detection  # noqa: E402
from remitwatch.store import Store  # noqa: E402
from tests.test_activity import write_files  # noqa: E402
from tests.test_ema_coverage import files as cov_files  # noqa: E402
from tests.test_ema_coverage import rows as cov_rows  # noqa: E402

WEEKS = [["Mar 9, 2026", "41", "10", "24%"], ["Mar 16, 2026", "40", "0", "0%"]]


def _port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _page(db: str) -> dict:
    port = _port()
    env = dict(os.environ, REMITWATCH_DB=db, REMITWATCH_PASSWORD=PW, REMITWATCH_USER="oda", PYTHONDONTWRITEBYTECODE="1")
    for k in ("REMITWATCH_MODE", "REMITWATCH_OWNER_PASSWORD", "REMITWATCH_SHOW_PHI"):
        env.pop(k, None)
    srv = subprocess.Popen([sys.executable, "-B", "-m", "uvicorn", "remitwatch.api:app", "--host", "127.0.0.1", "--port", str(port),
                            "--log-level", "warning"], cwd=REPO, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        out = subprocess.run(["node", os.path.join(HERE, "r6cov_page.js"), str(port), "oda", PW], capture_output=True, text=True, timeout=120)
        assert out.returncode == 0, out.stderr[-2000:]
        return json.loads(out.stdout.strip().splitlines()[-1])
    finally:
        srv.terminate()
        srv.wait(timeout=10)


def _stores(tmp_path) -> dict:
    full = str(tmp_path / "full.sqlite")
    st = Store(full)
    st.ingest_835_paths(write_files(tmp_path, cov_files()))
    st.ingest_ema_rows(cov_rows())
    st.close()
    live = str(tmp_path / "live.sqlite")
    shutil.copy(full, live)
    run_detection(full)
    st = Store(full)
    serving = str(tmp_path / "serving.sqlite")
    cloud.build_serving(st.conn, serving)
    st.close()
    out = {"full": full, "serving": serving}
    tree = live_tree()
    if tree:
        run_tree(tree, live, f"run_detection({live!r})")
        out["live"] = live
    return out


@pytest.mark.skipif(not shutil.which("node"), reason="needs node")
def test_the_data_tab_lists_the_weeks_on_every_copy_and_nothing_on_a_live_store(tmp_path):
    if subprocess.run(["node", "-e", "require('playwright')"], capture_output=True).returncode:
        pytest.skip("needs Playwright for node")
    stores = _stores(tmp_path)
    seen = {name: _page(db) for name, db in stores.items()}
    for name, views in seen.items():
        for view, r in views.items():
            assert r["errors"] == [] and r["failed"] == [], (name, view, r["errors"], r["failed"])
            assert r["overflow"] <= 0, (name, view, r["overflow"])
    for name in ("full", "serving"):
        for view in ("desktop", "phone"):
            assert seen[name][view]["panel"] and seen[name][view]["rows"] == WEEKS, (name, view, seen[name][view])
    if "live" in seen:
        assert not seen["live"]["desktop"]["panel"] and not seen["live"]["phone"]["panel"]
