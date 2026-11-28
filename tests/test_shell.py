"""The app shell (static/shell.js), the same on every page: the places in the sidebar and the phone's tab bar, the health
pill that says in dollars what the app cannot check (the owner's rule 3), and each page's title. Run in node with
invented answers; no browser needed (the page tests drive the real pages)."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "remitwatch", "static")
NODE = shutil.which("node")


def read(name: str) -> str:
    with open(os.path.join(STATIC, name), encoding="utf-8") as fh:
        return fh.read()


def shell(calls: list) -> list:
    """Load shell.js in node with a bare window and run each [function, argument...] call; return their answers."""
    js = """
const fs = require('fs'), vm = require('vm');
const ctx = {window: {}, navigator: {platform: 'Linux'}, localStorage: {getItem: () => null, setItem() {}, removeItem() {}},
             document: {getElementById: () => null, querySelectorAll: () => [], documentElement: {setAttribute() {}, removeAttribute() {}, getAttribute: () => null}}};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), ctx);
const S = ctx.window.RWShell, calls = JSON.parse(process.argv[2]);
process.stdout.write(JSON.stringify(calls.map(([f, ...a]) => typeof S[f] === 'function' ? S[f](...a) : S[f])));
"""
    out = subprocess.run([NODE, "-e", js, os.path.join(STATIC, "shell.js"), json.dumps(calls)], capture_output=True, text=True,
                         check=True, cwd=ROOT, timeout=60)
    return json.loads(out.stdout)


pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def test_every_route_of_the_app_has_a_place_and_a_name():
    """The routes stay (#denials, #fees ...): each one the app knows is named in the shell, so the title and the place
    marked in the sidebar follow it, and Work's views are the old category tabs."""
    index = read("index.html")
    keys = re.findall(r"\['([a-z]+)','[^']*'\]", index.split("const TABS = ", 1)[1].split("const ALIASES", 1)[0])
    names, groups = shell([["names"], ["groups"]])
    assert len(keys) >= 20 and not [k for k in keys if k not in names], [k for k in keys if k not in names]
    work = next(it for g in groups for it in g["items"] if it.get("tab") == "worklist")
    assert [s["tab"] for s in work["sub"]] == ["denials", "bundling", "downcoding", "contracts", "fees"]
    owner = next(g for g in groups if g.get("label") == "Owner")
    assert [it["tab"] for it in owner["items"]] == ["policy", "providers", "calculator"] and owner["lock"]   # policy moved to Owner
    settings = next(g for g in groups if g.get("label") == "Settings")
    assert [it.get("tab") or it.get("href") for it in settings["items"]] == ["data", "/upload", "/setup"]


def test_the_health_pill_says_what_cannot_be_checked_in_dollars_worst_first():
    blind = {"blind": True, "cents": 1234500, "feeds": [{"feed": "835", "stale": True, "days": 12}], "lines": ["one", "two"]}
    sync_behind = {"feed_date": "2026-09-01", "newest_payment_date": "2026-11-10", "days_behind": 83}
    brief = {"health": [{"key": "remits_missing", "ok": False, "cents": 765500, "title": "Paid in EMA with no remittance"}]}
    late = {"exports": [{"title": "Charges", "overdue": True}, {"title": "Payments", "overdue": False}]}
    safe = {"banner": {"safe_mode": True}}
    got = shell([["pill", {"meta": safe, "blind": blind}], ["pill", {"blind": blind, "sync": sync_behind}],
                 ["pill", {"sync": sync_behind}], ["pill", {"brief": brief}], ["pill", {"exports": late}], ["pill", {}],
                 ["pill", {"metaFailed": True}], ["pill", {"freshening": True}]])
    s, b, r, m, e, ok, none, busy = got
    assert s["cls"] == "safe" and s["text"].startswith("Safe mode") and "$12,345 unchecked" in s["text"]
    assert b["cls"] == "stale" and b["text"] == "Remittances 12 days old · $12,345 unchecked" and b["anchor"] == "dtMissing"
    assert r["text"] == "Remittances reach Sep 1, 2026 for most payers" and "Newest payment Nov 10, 2026 (83 days ago)." in r["title"]
    assert m["text"] == "$7,655 paid with no remittance" and m["cls"] == "stale"
    assert e["text"] == "1 EMA export overdue" and e["anchor"] == "dtReports"
    assert (ok["cls"], ok["text"]) == ("ok", "Data current") and (none["cls"], none["text"]) == ("stale", "No data yet")
    assert (busy["cls"], busy["text"]) == ("busy", "Looking for new payments")
    for p in got:
        assert "\u2014" not in p["text"] + p["title"]


def test_on_the_vercel_site_the_pill_always_says_it_analyses_only_on_a_press():
    an = {"press_only": True, "analysed": {"at": "2026-11-20T15:04:00Z"}}
    got = shell([["pill", {"sync": an}], ["pill", {"sync": dict(an, running=True)}], ["pill", {"sync": dict(an, last_failed=True)}],
                 ["pill", {"sync": {"press_only": True, "unchecked": {"n": 3, "cents": 45000, "read": {"claims": 2, "cents": 1000}}}}]])
    first, running, failed, unchecked = got
    assert first["press"] and first["cls"] == "press" and "last analysed Nov 20" in first["text"]
    assert first["text"].endswith("only when someone presses Run the analysis now")
    assert "analysing now" in running["text"] and "last analysed" in running["text"]
    assert failed["cls"] == "stale" and "the last run stopped before it finished" in failed["text"]
    assert "not analysed yet" in unchecked["text"] and "3 open items ($450)" in unchecked["text"]
    assert "2 remittance claims read since ($10 not paid)" in unchecked["text"] and unchecked["cls"] == "stale"


def test_the_title_names_the_place_and_counts_what_closes_within_7_days_never_a_patient():
    brief = {"work": {"total": {"due7_n": 4}}}
    got = shell([["title", "today", {"brief": brief}], ["title", "denials", {"brief": brief}], ["title", "data", {}],
                 ["title", "nowhere", {}]])
    assert got == ["(4) Today · RemitWatch", "(4) Work · Denials · RemitWatch", "Data · RemitWatch", "RemitWatch"]


def test_browser_storage_holds_only_the_theme_and_the_sidebar_width_and_never_breaks_the_page():
    src = read("shell.js") + read("index.html") + read("upload.html") + read("setup.html")
    keys = set(re.findall(r"""(?:store\.(?:get|set)|localStorage\.getItem)\(['"]([\w:.-]+)['"]""", src))
    assert keys <= {"rw:theme", "rw:sidebar", "rw:guide:v1"}, keys
    body = read("shell.js").split("const store = {", 1)[1].split("};", 1)[0]
    assert body.count("try {") == 2 and body.count("catch(e)") == 2           # each read and write is wrapped
    for page in ("index.html", "upload.html", "setup.html"):
        head = read(page).split("</head>", 1)[0]
        no_flash = re.search(r"<script>(.*?)</script>", head, re.S).group(1)
        assert "try {" in no_flash and "catch(e)" in no_flash, page            # the theme is set before the first paint


def test_the_nine_figures_head_results_and_the_exports_line_heads_data():
    """Today's nine tiles moved to the top of Results (static/today.js tdBoard), drawn on both of its paths (the figures
    read, or not), before every other panel; EMA's exports line moved to the top of the Data tab (tdEmaInto)."""
    results, data, today = read("results.js"), read("data.js"), read("today.js")
    assert "async function tdBoard(el)" in today and "async function tdEmaInto(el)" in today
    assert results.count('<div id="rsBoard"></div>') == 2 and results.count("tdBoard(v.querySelector('#rsBoard'))") == 2
    draw = results.split("function rsDraw(v, d, score){", 1)[1].split("\n}\n", 1)[0]
    assert draw.index('<div id="rsBoard"></div>') < draw.index("${rsDueHtml(d)}")
    assert '<div id="dtBlind"></div><div id="dtEmaLine"></div>' in data and "tdEmaInto(v.querySelector('#dtEmaLine'))" in data


def test_the_guide_and_the_analysis_page_name_the_places_as_the_sidebar_does():
    """The first-visit guide (today.js guideHtml) and the Analysis page (today.js moreTab) name the places as the sidebar
    names them, and the guide says the Worklist's own first order in the words of its order menu (desk.js DESK_DEFAULTS
    and its orders): a newcomer reads one name for each place, in the guide, the sidebar and the page."""
    today, desk = read("today.js"), read("desk.js")
    groups, names = shell([["groups"], ["names"]])
    labels = {it["label"] for g in groups for it in g["items"]} | {s["label"] for g in groups for it in g["items"] for s in it.get("sub", [])}
    guide = today.split('<div class="guide-more">', 1)[1].split("</div>", 1)[0]
    named = re.findall(r"<b>([^<]+)</b>", guide)
    assert len(named) >= 7 and not [n for n in named if n not in labels], [n for n in named if n not in labels]
    first = re.search(r"sort:'(\w+)'", desk.split("const DESK_DEFAULTS", 1)[1]).group(1)
    # the order menu's words live in desk.js ORDERS (unit ui-work moved them there from deskTab's `orders`)
    orders = dict(re.findall(r"\['(\w+)','([^']+)'\]", desk.split("const ORDERS = ", 1)[1].split(";", 1)[0]))
    assert orders[first].lower() in guide.lower(), (first, orders[first])
    more = today.split("async function moreTab(v){", 1)[1]
    assert re.search(r'<h1 class="page-title">([^<]+)</h1>', more).group(1) == names["more"] == "Analysis"


def _browser_ready() -> bool:
    return bool(NODE) and subprocess.run([NODE, "-e", "require('playwright')"], capture_output=True).returncode == 0


@pytest.mark.skipif(not _browser_ready(), reason="needs node and Playwright for node")
def test_the_shell_in_a_browser(tmp_path):
    """The brand's finish (CRITIQUE_premium change 8 without the sign-in): Ctrl K opens Search and go to and takes the
    person to the place typed; the theme is kept on this computer and set before the first paint; each page's title
    names its place; the icons load; Upload sits in the same shell; on a phone the tab bar's Find and More open the
    palette and the places, and Escape closes them."""
    import socket
    import sys
    import time
    import urllib.request
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DROPBOX_", "TRIZETTO_", "SRFAX_", "ANTHROPIC_", "SMTP_"))
           and k not in ("REPORT_TO", "ALERT_WEBHOOK_URL", "REMITWATCH_MODE", "REMITWATCH_OWNER_PASSWORD", "REMITWATCH_SHOW_PHI",
                         "REMITWATCH_SYNC_INTERVAL_MINUTES", "REMITWATCH_ALLOW_OPEN")}
    env.update(REMITWATCH_PASSWORD="shell-test-password-41", REMITWATCH_USER="oda", REMITWATCH_DB=str(tmp_path / "store.sqlite"),
               PYTHONDONTWRITEBYTECODE="1")
    srv = subprocess.Popen([sys.executable, "-B", "-m", "uvicorn", "remitwatch.api:app", "--host", "127.0.0.1", "--port", str(port),
                            "--log-level", "warning"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for _ in range(150):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        got = subprocess.run([NODE, os.path.join(ROOT, "tests", "shell_page.js"), str(port), "oda", "shell-test-password-41"],
                             capture_output=True, text=True, timeout=240, cwd=ROOT)
    finally:
        srv.terminate()
        srv.wait(timeout=20)
    assert got.returncode == 0, got.stderr[-2000:]
    out = json.loads(got.stdout.strip().splitlines()[-1])
    assert out["errors"] == [] and out["failed"] == [], (out["errors"], out["failed"])
    assert out["places"][:2] == ["today", "worklist"] and {"policy", "providers", "calculator", "data", "/upload", "/setup"} <= set(out["places"])
    assert out["title_today"].endswith("Today · RemitWatch")
    assert out["icons"] and all(status == 200 for _href, status in out["icons"]), out["icons"]
    assert sorted(m for m, _c in out["theme_colors"]) == ["(prefers-color-scheme: dark)", "(prefers-color-scheme: light)"]
    assert out["palette_focus"] == "palQ"
    assert out["after_go"]["hash"] == "#denials" and out["after_go"]["title"].endswith("Work · Denials · RemitWatch")
    assert out["after_go"]["palette_hidden"] and out["after_go"]["active"] == ["denials"] and out["escape_closes"]
    assert out["dark"] == ["dark", "dark"] and out["dark_at_load"] == "dark" and out["system"] == [None, None]
    assert out["upload"]["title"] == "Upload from EMA · RemitWatch" and out["upload"]["places"] >= 12 and out["upload"]["pill"]
    assert out["upload_goto"] and all(h.startswith("/") for h in out["upload_goto"])          # from Upload, the places are the app's
    ph = out["phone"]
    assert ph["tabs"] == ["Today", "Work", "Results", "Find", "More"] and not ph["sidebar_shown"] and ph["overflow"] <= 1
    assert ph["find_opens"] and ph["more_opens"] and ph["more_closes"]
    bell = out["bell"]          # the five newest alerts under the bell, See all, and the page stays where it is (unit ui-pages)
    assert bell["shown"] and bell["items"] == [f"Invented alert {i}" for i in (7, 6, 5, 4, 3)] and bell["all"] == "#alerts"
    assert bell["inside"] and bell["focus"] and bell["hash"] == "#today" and bell["closes"]
