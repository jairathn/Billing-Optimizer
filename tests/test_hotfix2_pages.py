"""Hotfix 2, the pages in a browser (Playwright through node, hotfix2_page.js) on an invented store served by uvicorn:
H1 the item panel acts on the item it shows, once; H3 every status change carries what the page showed; H4 the Paid amount
is read as typed; H6 the note check waits for a person's tick; H7 hiding shows its size and needs the reason, the count
and the owner's password; H8 the header says how far the remittances reach; H10 Find a claim on a partial copy says so;
H11 the address bar takes only RemitWatch's own names; and push 2's next-step panel (its explain answer let through), where
billers log what happened: the Paid amount read as typed from an empty prompt, Save acting once (fix wave F5, review
people-1). On both hosts: the server's store file, and the serverless site serving its dashboard copy and journalling what
people do; at a desk's width (1400 px) and a phone's (390 px). Skipped where node or Playwright is missing. Invented data
only."""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import date, timedelta

import pytest

from remitwatch.detect import Detector
from tests.test_features import build_store, claim, clean_visits, svc

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PW = "correct-horse-battery-7"
OWNER = "owner-invented-passphrase-42"


def _port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _claims() -> tuple[list[str], str]:
    """One payer, paid last week: a usual rate of $117.60 for 99214, fourteen visits allowed less (each by its own amount,
    so each is its own item), and two visits denied for medical necessity."""
    pay = (date.today() - timedelta(days=7)).strftime("%Y%m%d")
    dos = (date.today() - timedelta(days=14)).strftime("%Y%m%d")
    under = [claim(300 + i, svc("99214", "223.65", f"{80 + i}.00", f"{80 + i}.00", f"CAS*CO*45*{143.65 - i:.2f}~", dos=dos), dos=dos) for i in range(14)]
    denied = [claim(400 + i, svc("99214", "223.65", "0.00", None, "CAS*CO*50*223.65~", dos=dos), dos=dos) for i in range(2)]
    # each visit its own patient: one name on many member numbers, the same day and code, is the archive's sign that a
    # denial was possibly paid on another claim (push 2's check-first step), which these walks are not about
    every = clean_visits(1, 25, "99214", "223.65", "117.60") + under + denied
    return [c.replace("NM1*QC*1*SAMPLE*PAT", f"NM1*QC*1*SAMPLE{n:03d}*PAT", 1) for n, c in enumerate(every)], pay


def _store(tmp_path, monkeypatch) -> dict:
    """The server: the store file, analysed."""
    claims, pay = _claims()
    s = build_store(tmp_path, claims, pay_date=pay)
    Detector(s, run_id="t").run()
    s.close()
    return {"REMITWATCH_DB": str(tmp_path / "s.sqlite")}


def _serverless(tmp_path, monkeypatch) -> dict:
    """The serverless site: the same remittances loaded by one run into a state folder (a fake Dropbox that keeps it on
    disk); the site serves the dashboard's copy from it and journals what people do there, as Vercel does."""
    from remitwatch import cloud
    from remitwatch.state import DropboxState
    from tests.fakes import FakeDropbox
    from tests.test_remitwatch import remit
    for k in ("DROPBOX_APP_KEY", "DROPBOX_APP_SECRET", "DROPBOX_REFRESH_TOKEN", "REMITWATCH_STATE_DIR", "VERCEL", "VERCEL_ENV", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("REMITWATCH_TMP", str(tmp_path / "scratch"))
    monkeypatch.setenv("DROPBOX_ROOT_PATH", "/Trizetto Data")
    root = tmp_path / "dbx"
    (root / "Trizetto Data").mkdir(parents=True)
    claims, pay = _claims()
    for n, i in enumerate(range(0, len(claims), 20)):
        (root / "Trizetto Data" / f"r{n:04d}.RMT").write_bytes(remit("".join(claims[i:i + 20]), "100.00", seg_count=99, isa_ctrl=f"{n + 1:09d}", pay_date=pay))
    client = FakeDropbox(str(root))
    cloud.run_cloud_cycle(DropboxState(client, "/RemitWatch-state"), client, email=False, log=lambda *a: None, budget_s=600)
    return {"REMITWATCH_MODE": "serverless", "REMITWATCH_STATE_DIR": str(root / "RemitWatch-state"), "REMITWATCH_TMP": str(tmp_path / "scratch")}


PAID_EMPTY = "Type the amount the payer paid, from the remittance or EMA. If you do not know it yet, leave the item as it is."
PAID_ZERO = "Paid needs an amount above $0. If the payer paid nothing, mark it Lost or Let go instead."


@pytest.mark.skipif(not shutil.which("node"), reason="needs node")
@pytest.mark.parametrize("width", ["1400", "390"])
@pytest.mark.parametrize("host", ["server", "serverless"])
def test_the_pages_act_on_the_item_they_show_once_and_say_what_they_saw(tmp_path, monkeypatch, host, width):
    if subprocess.run(["node", "-e", "require('playwright')"], capture_output=True).returncode:
        pytest.skip("needs Playwright for node")
    site = (_store if host == "server" else _serverless)(tmp_path, monkeypatch)
    port = _port()
    env = dict(os.environ)
    for k in [k for k in env if k.startswith(("SRFAX_", "DROPBOX_", "TRIZETTO_", "ANTHROPIC_", "MODMED_", "SMTP_"))] + \
            ["REMITWATCH_MODE", "REMITWATCH_SHOW_PHI", "REMITWATCH_SYNC_INTERVAL_MINUTES", "REPORT_TO", "ALERT_WEBHOOK_URL", "REMITWATCH_DB",
             "REMITWATCH_STATE_DIR", "VERCEL", "VERCEL_ENV"]:
        env.pop(k, None)
    env.update(site, REMITWATCH_PASSWORD=PW, REMITWATCH_USER="oda", REMITWATCH_OWNER_PASSWORD=OWNER, PYTHONDONTWRITEBYTECODE="1", VW=width)
    srv = subprocess.Popen([sys.executable, "-B", "-m", "uvicorn", "remitwatch.api:app", "--host", "127.0.0.1", "--port", str(port),
                            "--log-level", "warning"], cwd=REPO, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for _ in range(150):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        run = subprocess.run(["node", os.path.join(HERE, "hotfix2_page.js"), str(port), "oda", PW, OWNER], capture_output=True, text=True, timeout=420,
                             env=dict(os.environ, VW=width))
        assert run.returncode == 0, run.stderr[-3000:]
        out = json.loads(run.stdout.strip().splitlines()[-1])
    finally:
        srv.terminate()
        srv.wait(timeout=10)
    assert out["errors"] == [], out["errors"]
    # H11: names every object has are not tabs: the address stays as typed and Today is shown
    for h, r in out["hash"].items():
        assert "function" not in r["hash"] and "[object" not in r["hash"] and r["active"] == "today", (h, r)
    # H1 (0a-2): a double click marks only the item on screen; a triple click too
    assert out["walk"]["double"] == ["appealed", "open", "open"], out["walk"]
    assert out["walk"]["panel_after_double"] == out["walk"]["ids"][1]
    assert out["walk"]["triple"] == ["appealed", "open", "open"], out["walk"]
    assert all(p["seen"] == "open" for p in out["walk"]["posts"]) and len(out["walk"]["posts"]) == 2       # H3: what the panel showed
    # H1 (0a-7, 0d-4): the panel shows the last row clicked, whatever answer came last
    assert out["race"]["panel"] == out["race"]["last"], out["race"]
    # H1 (0a-10): two clicks on Sent, one change
    assert out["double_sent"] == {"posts": 1, "events": 1}, out["double_sent"]
    # H1 (0d-1): the late answer for A stays A's; the button pressed in B's panel faxes B
    c = out["cross"]
    assert c["panel"] == c["B"] and c["stale_buttons"] == 1 and c["modal_open"] is True, c
    assert c["sends"] == [{"id": "A", "stale_ok": False}, {"id": "B", "stale_ok": False}, {"id": "B", "stale_ok": True}], c["sends"]
    assert any("Not faxed" in n for n in c["notice"]), c["notice"]
    # H1 (0d-5): A's fax going out leaves the panel on B with what was typed; a notice says A was faxed
    y = out["yank"]
    assert y["panel"] == y["B"] and y["typed"] == "Text typed for the second item" and any(n.startswith("Faxed:") for n in y["notice"]), y
    # H1: a double click builds one packet and sends one email
    assert out["once"] == {"builds": 1, "emails": 1}, out["once"]
    # H6: the tick, then the send carries note_checked and the packet shown
    n = out["note"]
    assert n["disabled_before"] is True and n["disabled_after"] is False and n["packet_link"] == 1 and "I opened this note" in n["label"]
    assert [(x.get("note_checked"), x.get("packet_id")) for x in n["sends"]] == [(None, None), (True, "pkt.1")], n["sends"]
    # H4: the prompt starts empty with the disputed amount as a hint; amounts are read as typed; anything else is asked again
    p = out["paid"]
    assert all(a["value"] == "" for x in p for a in x["asked"]) and "The amount in dispute is" in p[0]["asked"][0]["message"]
    assert p[0]["sent"]["recovered_cents"] == 125000 and p[0]["sent"]["seen_status"] == "open"
    whys = [a["message"].split("\n\n")[0] for a in p[1]["asked"][1:]]
    zero = "Paid needs an amount above $0. If the payer paid nothing, mark it Lost or Let go instead."         # CH-10: '0' is no payment
    assert whys == ["Type the amount the payer paid, from the remittance or EMA. If you do not know it yet, leave the item as it is.",
                    "The amount paid cannot be below zero.", "That is not an amount. Type it as the remittance or EMA shows it, for example 1,250.00.",
                    "Use at most two decimals (dollars and cents).", "That is not an amount. Type it as the remittance or EMA shows it, for example 1,250.00.",
                    zero, zero], whys
    assert p[1]["sent"]["recovered_cents"] == 125000 and p[2]["sent"]["recovered_cents"] == 125050 and p[3]["sent"]["recovered_cents"] == 9500
    assert p[4]["sent"] is None and "ten times the charge" in p[4]["asked"][1]["message"]
    # push 2's panel (review people-1): Log what happened is the main button of an item a person marked Sent; the Paid prompt
    # starts empty with the disputed amount as a hint; 1,250.00 and $1,250.00 are $1,250.00; OK on the empty prompt and 0
    # are asked again, and backing out sends nothing; a double click on Save is one change and one line in the history
    pa = out["panel"]
    assert {pa[k]["main"] for k in "ABCD"} == {"log_outcome"}, pa
    assert pa["A"]["asked"][0]["value"] == "" and "The amount in dispute is" in pa["A"]["asked"][0]["message"], pa["A"]
    assert [b.get("recovered_cents") for b in pa["A"]["sent"]] == [125000] and pa["A"]["sent"][0]["seen_status"] == "appealed", pa["A"]
    assert [b.get("recovered_cents") for b in pa["D"]["sent"]] == [125000], pa["D"]
    assert pa["B"]["sent"] == [] and [a["message"].split("\n\n")[0] for a in pa["B"]["asked"][1:]] == [PAID_EMPTY, PAID_ZERO], pa["B"]
    assert pa["C"]["posts"] == 1 and pa["C"]["lines"] == 1, pa["C"]
    assert pa["overflow"] <= 0, pa["overflow"]                      # nothing wider than the screen, at either width
    # H3: a bulk change carries the status each row showed
    assert len(out["bulk"]) == 1 and set(out["bulk"][0]["seen"].values()) == {"open"} and len(out["bulk"][0]["seen"]) == 2
    # H7: the size first, no reason written for the person, the count typed, the owner's password asked and sent
    h = out["hide"]
    assert "open items now, worth" in h["text"] and "still inside" in h["text"] and "Every future item" in h["text"], h
    assert h["note_prefilled"] == "" and "Type" in h["wrong_count"] and h["asked_owner"] is True, h
    assert h["posts"][-1]["owner"] is True and h["posts"][-1]["body"]["note"] == "This payer does not pay these"
    assert h["posts"][-1]["body"]["confirm"] == int(h["text"].split("lets go ")[1].split(" ")[0]) and h["status_after"] == "dismissed"
    # H8: how far the remittances reach for most payers (the health pill), and the newest payment beside it (its hover)
    assert "Remittances reach Sep 1, 2026 for most payers" in out["header"], out["header"]
    assert "Newest payment Nov 10, 2026 (83 days ago)." in out["header_title"], out["header_title"]
    # H10: a partial copy says so; the server keeps its words, and the serverless site's own answer says its copy is partial
    assert "holds only the claims that have items" in out["find_partial"] and "Nothing in the remittance files" not in out["find_partial"]
    if host == "server":
        assert "Nothing in the remittance files or the EMA exports matches" in out["find_server"]
    else:
        assert "holds only the claims that have items" in out["find_server"] and "Nothing in the remittance files" not in out["find_server"]
