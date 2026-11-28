"""The pages unit of the premium pass (plans/briefs/2026-11-28_ui.md, unit ui-pages): Results opens on the manager's
week (CRITIQUE_biller change 10) with the money-back chart (CRITIQUE_premium change 11); the charts take their colors
from the stylesheet's tokens in both themes (change 9); the long pages are sections, not cards (change 13), with one
filled button each; a biller's page names no setting. The page code runs in node with invented figures only."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "remitwatch", "static")


def read(name: str) -> str:
    with open(os.path.join(STATIC, name), encoding="utf-8") as fh:
        return fh.read()


# The Results page's own code (results.js) with index.html's helpers, in node. The site's answers are canned and
# invented: a scoreboard whose last week is the current one, the policy, the blind spot, the payers' fax numbers and the
# people (unit ui-words' /api/people), or a 404 for it.
RESULTS_PAGE_JS = r"""
const fs = require('fs'), vm = require('vm'), path = require('path');
const read = f => fs.readFileSync(path.join(process.argv[2], f), 'utf8');
const html = read('index.html');
const grab = (src, re) => { const m = src.match(re); if (!m) throw new Error('not found: ' + re); return m[0]; };
const helpers = [/const money = [^\n]*\n/, /const money2 = [^\n]*\n/, /const esc = [^\n]*\n/, /function qs\([^\n]*\n/].map(re => grab(html, re)).join('\n')
  + grab(read('today.js'), /const STATUS_WORDS = [^\n]*\n/);
let answers = {};
const calls = [];
const el = () => ({ innerHTML: '', hidden: false, isConnected: true, querySelectorAll: () => [], querySelector: () => null, clientWidth: 0 });
const ctx = { console, state: {}, $: () => null, document: { querySelector: () => null, querySelectorAll: () => [] },
  api: async p => { calls.push(p); const k = p.split('?')[0]; if (!(k in answers)) throw Object.assign(new Error('Not Found'), { status: 404 }); return answers[k]; } };
vm.createContext(ctx);
vm.runInContext(helpers + read('results.js'), ctx);
const week = (w, rec, extra) => Object.assign({ week: w, recovered_cents: rec, lost_cents: 0, at_risk_cents: null, given_up_cents: 0 }, extra || {});
const score = { recovered_cents: 104300, lost_cents: 97900, at_risk_cents: 56700, given_up_cents: 390800,
  counts: { recovered: 14, lost: 3, at_risk: 5, given_up: 39 }, basis: { recovered: 'r', lost: 'l', at_risk: 'a', given_up: 'g' },
  given_up_weeks: 'written off in that week',
  weeks: [week('2026-09-28', 27500, { frozen: true }), week('2026-10-26', 8000, { frozen: true, now_cents: 9000 }), week('2026-11-02', 38000, { frozen: true }),
          week('2026-11-09', 17000), week('2026-11-16', 7800, { lost_cents: 1200 }), week('2026-11-23', 5900, { lost_cents: 3000, given_up_cents: 2500, at_risk_cents: 56700 })] };
const out = {};
(async () => {
  out.figs = ctx.rsSumFigsHtml(score);
  out.figs_none = ctx.rsSumFigsHtml(null);
  // Needs you: every part, then nothing at all
  answers = { '/api/ema/policy': { totals: { decide: { patterns: 3, items: 12, cents: 150000 } } }, '/api/ema/blind': { blind: true, cents: 420000 },
    '/api/faxes/payers': { payers: [{ payer_group: 'Plan A', n: 2, cents: 18000, numbers: {} }, { payer_group: 'Plan B', n: 4, cents: 9000, numbers: { appeal: { number: '(800) 555-0142' } } },
      { payer_group: 'Plan C', n: 1, cents: 500, by_hand: 'Through its portal' }] } };
  const needs = el(); await ctx.rsNeedsFill(needs, score); out.needs = needs.innerHTML;
  answers = { '/api/ema/policy': { totals: { decide: { patterns: 0, items: 0, cents: 0 } } }, '/api/ema/blind': { blind: false }, '/api/faxes/payers': { payers: [] } };
  const none = el(); await ctx.rsNeedsFill(none, Object.assign({}, score, { given_up_cents: 0 })); out.needs_none = none.innerHTML;
  // the team: from /api/people when the site has it, hidden when it does not
  answers = { '/api/people': { people: [{ name: 'Biller One', today: { items: 3, statuses: { appealed: 2, resolved: 1 }, paid_cents: 8000, lines: 0, changes: 3 },
    days: { items: 9, statuses: {}, paid_cents: 0, lines: 2, changes: 11 } }], unnamed: { today: { items: 0 }, days: { items: 2 } } } };
  const team = el(); team.hidden = true; await ctx.rsTeamFill(team); out.team = team.innerHTML; out.team_hidden = team.hidden;
  answers = {};
  const gone = el(); gone.hidden = true; await ctx.rsTeamFill(gone); out.team_gone_hidden = gone.hidden; out.team_gone_html = gone.innerHTML;
  // the chart on a desk and on a phone
  const box = el(); box.clientWidth = 900; ctx.rsChartDraw(box, score, { returned_cents: 110000, first_flag: '2026-10-03' }); out.chart = box.innerHTML; out.chart_hidden = box.hidden;
  const pbox = el(); pbox.clientWidth = 340; ctx.rsChartDraw(pbox, score, { returned_cents: 110000, first_flag: '2026-10-03' }); out.chart_phone = pbox.innerHTML;
  const empty = el(); ctx.rsChartDraw(empty, { weeks: [week('2026-11-23', 0)] }, {}); out.chart_empty_hidden = empty.hidden;
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
"""


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    script = tmp_path_factory.mktemp("ui_pages") / "results_page.js"
    script.write_text(RESULTS_PAGE_JS, encoding="utf-8")
    res = subprocess.run([node, str(script), STATIC], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr[-3000:]
    return json.loads(res.stdout)


def test_the_week_comes_from_the_scoreboards_current_week_and_its_live_at_risk(page):
    """The manager's week (CRITIQUE_biller change 10): back, lost, at risk in 14 days and given up, one definition with the
    scoreboard (its current week and its live at-risk figure), each opening the list it counts; a dash, never $0, when
    the figures could not be read."""
    figs = page["figs"]
    assert ">$59<" in figs and ">$30<" in figs and ">$567<" in figs and ">$25<" in figs, figs
    assert 'data-key="returned:week:2026-11-23"' in figs and 'data-key="lost:week:2026-11-23"' in figs and 'data-key="at_risk"' in figs
    assert "5 items to work" in figs
    assert len(re.findall(r'class="rs-fig[ "]', figs)) == 4 and figs.count("<a ") == 3      # given up opens no list of its own week
    assert "is-bad" in figs and "is-warn" in figs
    assert "could not be read" in page["figs_none"] and "$0" not in page["figs_none"]


def test_needs_you_names_each_thing_only_the_manager_settles_and_says_when_there_is_none(page):
    needs = page["needs"]
    assert "3 write-off patterns to decide" in needs and "$1,500 on 12 items" in needs and 'href="#policy"' in needs
    assert "Given up with no decision" in needs and 'data-key="given_up"' in needs and "$3,908 on 39 items" in needs
    assert "A feed is late" in needs and "$4,200 cannot be checked" in needs and 'href="#data"' in needs
    # only the payer with letters, no number, not by hand
    assert "1 payer with letters and no fax number" in needs and "$180 waiting" in needs and 'href="#faxes"' in needs
    assert "Nothing needs you this week." in page["needs_none"] and "rs-need rs-open" not in page["needs_none"]


def test_the_team_shows_who_did_what_and_stays_hidden_on_a_site_without_names(page):
    team = page["team"]
    assert page["team_hidden"] is False and "Biller One" in team and "3 items today" in team and "9 this week" in team
    assert "Sent 2" in team and "Paid 1" in team and "$80 paid" in team and "2 items this week were changed with no name" in team
    assert page["team_gone_hidden"] is True and page["team_gone_html"] == ""


def test_the_chart_colors_the_last_complete_week_outlines_this_week_and_ends_on_the_to_date_figure(page):
    """CRITIQUE_premium change 11: weeks at even steps of time (a week with nothing at zero), the last complete week in the
    accent, earlier weeks in the context gray, the current week outlined "so far", a line to the to-date figure, short
    month labels, no ISO date, colors only through the stylesheet's classes, and a text equivalent."""
    c = page["chart"]
    bars = re.findall(r'<rect class="([^"]+)" data-key="returned:week:([0-9-]+)"', c)
    weeks = [w for _cls, w in bars]
    assert weeks[0] == "2026-09-21" and weeks[-1] == "2026-11-23" and len(weeks) == 10        # from the week before the first money
    assert [cls for cls, _w in bars][-2:] == ["bar", "bar now"] and all(cls == "bar past" for cls, _w in bars[:-2])
    assert "Last complete week, week of Nov 16: $78." in c and "This week so far: $59." in c and "$1,100 since Oct 3" in c
    assert ">Sep<" in c and ">Oct<" in c and ">Nov<" in c and not re.search(r">\d{4}-\d{2}", c)
    assert "#" not in re.sub(r'(href|data-key)="[^"]*"', "", c).replace("&#39;", "")          # no color written in the page
    assert 'role="img"' in c and "rsChartT" in c
    # a phone: the last twelve weeks, the end label in the key instead of beside the line
    assert len(re.findall(r"<rect ", page["chart_phone"])) == 10 and 'class="cum-label"' not in page["chart_phone"]
    assert page["chart_hidden"] is False and page["chart_empty_hidden"] is True


def test_results_draws_the_week_first_and_keeps_the_board_and_one_stylesheet():
    js = read("results.js")
    draw = js.split("function rsDraw(v, d, score){", 1)[1].split("\n}\n", 1)[0]
    assert draw.index("${rsSumHtml()}") < draw.index('<div id="rsBoard"></div>') < draw.index("${rsDueHtml(d)}")
    assert "rsSumFill(v, score)" in draw and "rsChartDraw(v.querySelector('#rsChart'), score, d.money)" in draw
    assert "document.createElement('style')" not in js and "font-size:34px" not in js        # the page's look lives in rw.css
    css = read("rw.css")
    assert "== 23. Pages" in css and ".secs .panel:not(.act)" in css and ".rs-chart .bar.now" in css


def test_the_charts_take_their_colors_from_the_tokens_and_follow_the_theme():
    """CRITIQUE_premium change 9: no hand-picked palette; Chart.js's font, ink and grid from the tokens; a theme change
    draws the charts again; straight lines; "Jan 2026", "Feb" month labels and no rotated label; a text equivalent."""
    html = read("index.html")
    assert re.search(r"^const palette = \[\];$", html, re.M) and "'#1f5fbf'" not in html
    assert "const VIZ_TOKENS = ['--viz-1','--viz-2','--viz-3','--viz-4'" in html
    assert "Chart.defaults.color = v('--text-2'); Chart.defaults.borderColor = v('--viz-grid');" in html
    assert "onTheme: () => { vizTheme();" in html and "d.tension = 0;" in html and "maxRotation: 0" in html
    assert "el.setAttribute('aria-label'" in html
    css = read("rw.css")
    for k in ("--viz-1", "--viz-2", "--viz-3", "--viz-4", "--viz-context", "--viz-grid"):
        assert css.count(k + ":") >= 3, k                                                       # light, system dark, chosen dark


def test_the_long_pages_are_sections_with_one_filled_button_and_name_no_setting():
    """CRITIQUE_premium change 13 and the brief's checks: Data, Faxes, Upload and Setup draw their panels as sections; one
    filled button per view; a biller's page names no setting (an administrator's Setup may)."""
    faxes, data, upload = read("faxes.js"), read("data.js"), read("upload.js")
    assert 'v.innerHTML = `<div class="secs"><div class="page-head ph-row">' in faxes
    assert '<div class="secs"><div class="page-head"><h1 class="page-title">Data</h1>' in data
    assert '<main class="page doc secs" id="main">' in read("upload.html") and '<main class="page doc secs" id="main">' in read("setup.html")
    assert 'class="primary" id="fxAll"' not in faxes and 'class="primary" id="fxPush"' in faxes
    assert "class=\"primary\" data-pick" not in upload and "leadKey = ((EX.exports.find(e => rowFlag(e) === 'overdue')" in upload
    page_text = re.sub(r"title=\"\$\{[^}]*\}\"", "", faxes)
    for name in ("SRFAX_CALLER_ID", "SRFAX_ACCESS_ID", "ANTHROPIC_API_KEY", "set ${esc(fx.practice_missing"):
        assert name not in page_text, name
    # the answers that came in come before the follow-ups, then what went out, the payer numbers last (DESIGN P8)
    order = [faxes.index(h) for h in ("<h2>Came in", "<h2>Follow-ups due", "<h2>Sent", "<h2>Payer fax numbers")]
    assert order == sorted(order)


def test_a_number_saved_with_the_owners_password_says_so(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    fn = re.search(r"^function faxBy\(by\)\{[^\n]*\n", read("faxes.js"), re.M).group(0)
    script = tmp_path / "fax_by.js"
    script.write_text(fn + "console.log(JSON.stringify([faxBy(\"anonymous (owner's password)\"), faxBy('Biller One'), faxBy(''), "
                      "faxBy(\"Biller Two (owner's password)\")]));", encoding="utf-8")
    got = json.loads(subprocess.run([node, str(script)], capture_output=True, text=True, timeout=30).stdout)
    assert got == ["with the owner's password", "by Biller One", "by someone", "by Biller Two with the owner's password"]


def test_the_bell_lists_the_five_newest_alerts_with_see_all():
    """CRITIQUE_premium change 17, read in the shell's code (the browser run in tests/test_shell.py opens it)."""
    sh = read("shell.js")
    body = sh.split("function bellMenu(open){", 1)[1].split("\n  }\n", 1)[0]
    assert ".slice(0, 5)" in body and "localeCompare" in body and "See all alerts" in body and "No new alerts." in body
    assert "e.key === 'Escape'" in body and "bellMenu(false)" in body
    assert "bellMenu" in sh.split("window.RWShell = {", 1)[1]
    assert "$('#bellBtn').onclick" not in read("index.html")                    # the bell no longer leaves the page
