"""The item panel (DESIGN P3, unit ui-panel) in a browser (Playwright through node, tests/panel_page.js) on the invented store
of tests/test_hotfix2_pages.py, on both hosts (the server's store file; the serverless site's dashboard copy and journal), at
a desk's width and a phone's: no browser box anywhere in the panel (CRITIQUE_biller 4); the head with the code and the
item's place and no patient with names off (CRITIQUE_biller 15); the panel takes the focus and shows one filled button; the
next item's answers are asked for while one is read, so Next asks for nothing more (CRITIQUE_premium 1); Paid's amount starts
empty in the panel's own form and Esc closes the form before the panel; Let go needs a reason (a chip gives it) and Undo puts
the status back; s and Enter save Sent and open the next item, and Enter on the panel presses its main button (CRITIQUE_biller
9). Skipped where node or Playwright is missing. Invented data only."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

import pytest

from tests.test_hotfix2_pages import OWNER, PW, _port, _serverless, _store

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
REASON = "Not changed. Letting an item go, or marking it lost, needs a reason."


@pytest.mark.skipif(not shutil.which("node"), reason="needs node")
@pytest.mark.parametrize("width", ["1400", "390"])
@pytest.mark.parametrize("host", ["server", "serverless"])
def test_the_panel_works_without_a_browser_box_from_the_keyboard_and_at_once(tmp_path, monkeypatch, host, width):
    if subprocess.run(["node", "-e", "require('playwright')"], capture_output=True).returncode:
        pytest.skip("needs Playwright for node")
    site = (_store if host == "server" else _serverless)(tmp_path, monkeypatch)
    port = _port()
    env = dict(os.environ)
    for k in [k for k in env if k.startswith(("SRFAX_", "DROPBOX_", "TRIZETTO_", "ANTHROPIC_", "MODMED_", "SMTP_"))] + \
            ["REMITWATCH_MODE", "REMITWATCH_SHOW_PHI", "REMITWATCH_SYNC_INTERVAL_MINUTES", "REPORT_TO", "ALERT_WEBHOOK_URL", "REMITWATCH_DB",
             "REMITWATCH_STATE_DIR", "VERCEL", "VERCEL_ENV"]:
        env.pop(k, None)
    env.update(site, REMITWATCH_PASSWORD=PW, REMITWATCH_USER="oda", REMITWATCH_OWNER_PASSWORD=OWNER, PYTHONDONTWRITEBYTECODE="1")
    srv = subprocess.Popen([sys.executable, "-B", "-m", "uvicorn", "remitwatch.api:app", "--host", "127.0.0.1", "--port", str(port),
                            "--log-level", "warning"], cwd=REPO, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for _ in range(150):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        run = subprocess.run(["node", os.path.join(HERE, "panel_page.js"), str(port), "oda", PW], capture_output=True, text=True, timeout=300,
                             env=dict(os.environ, VW=width))
        assert run.returncode == 0, run.stderr[-3000:]
        out = json.loads(run.stdout.strip().splitlines()[-1])
    finally:
        srv.terminate()
        srv.wait(timeout=10)
    assert out["errors"] == [] and out["dialogs"] == [], (out["errors"], out["dialogs"])
    # the head: the item's place, the code, and with names off no patient
    head = out["head"]
    assert "Item 1 of" in head and "99214" in head and "At stake" in head and "SAMPLE" not in head, head
    # the panel takes the keys; one filled button in it (and on the whole screen, the panel docked beside the list)
    assert out["focus"] == "drawer", out["focus"]
    assert out["filled"]["panel"] == 1 and out["filled"]["page"] == 1, out["filled"]
    assert out["overflow"] <= 0 and out["overflow_end"] <= 0
    # Next: asked for while the first was read, and opened without asking again
    assert out["prefetched"] is True and out["next_gets"] == 0, out
    # Paid: the panel's own form, its amount empty and focused; Esc closes the form and keeps the panel
    assert out["paid"] == {"open": True, "value": "", "focus": "stAmt"}, out["paid"]
    assert out["after_esc"] == {"form": False, "panel": True}, out["after_esc"]
    # Let go: no reason, nothing sent and the form says why; a chip's reason goes with the change; Undo puts it back
    assert out["letgo_empty"] == {"said": REASON, "posts": 0}, out["letgo_empty"]
    first = out["ids"][1]
    assert out["letgo"]["sent"] == [{"status": "dismissed", "seen_status": "open", "notes": out["letgo_chip"]}], out["letgo"]
    assert out["letgo"]["status"] == "dismissed" and out["letgo"]["panel"] != first, out["letgo"]
    assert out["undo"]["sent"] == [{"status": "open", "seen_status": "dismissed", "notes": "Undone: back to To do"}], out["undo"]
    assert out["undo"]["status"] == out["undo"]["before"] == "open", out["undo"]
    # the keyboard path: s opens Sent with the focus in its field; Enter saves it (with what the panel showed) and walks on
    assert out["sent_form"] == "INPUT", out["sent_form"]
    s = out["sent"]
    assert s["sent"] == [{"url": f"/api/findings/{s['from']}/status", "status": "appealed", "seen": "open"}] and s["walked"], s
    # Enter on the panel presses its main button
    assert out["enter"], out["enter"]
