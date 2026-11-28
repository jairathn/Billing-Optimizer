# Unit ui-a: progress (plans/briefs/2026-11-28_ui.md, unit ui-a)

Branch `wip/ui-a`, worktree `$S/tk/wt/ui-a` (base: integration tip dc92143). Demo on port 8771, private folder
`$S/tk/units/ui-a` (site, shots, font sources). Invented data only.

## Steps (in the brief's order)

1. P0 stylesheet: DONE (commit "ui-a P0").
   - `remitwatch/static/rw.css` from the design's built stylesheet with CRITIQUE_premium section 7 applied in place
     (the one-off transform is `$S/tk/units/ui-a/p0_css.py`; from now on rw.css is the source, edited by hand).
   - Fonts: Inter 4.1 (official release, OFL) subset with both axes (opsz 14-32, wght) and the features of change 6:
     `fonts/inter-latin-opsz-wght.woff2` 74 KB, `fonts/inter-latin-ext-opsz-wght.woff2` 130 KB, `fonts/OFL-Inter.txt`.
     Command (fontTools in `$S/tk/units/ui-a/venv_fonts`):
     `pyftsubset InterVariable.ttf --flavor=woff2 --unicodes=<the two ranges in rw.css section 0> --layout-features=kern,calt,ccmp,locl,mark,mkmk,tnum,pnum,case,zero,ss02,frac,numr,dnom`
   - Icons: `plans/tools/ui/icons.py --write` writes rw.css's icon block (between the @icons markers) and
     `remitwatch/static/icons.svg` (the sprite pages use). New marks: st-open, st-half, minus, monitor, sidebar, command, corner.
   - Dark tokens: text-3 #949ca9 and accent-soft-2 #292c55 (caption on the selection fill passes 4.5 in both themes).
   - The three page shells link `/static/rw.css?v=2026-11-28` and preload the Latin font; no `<style>` block left.
   - Tests moved (same guarantee, read where the styles live now): test_ema_pages2 (every work type has its style),
     test_ema_pages (a printed page never carries the blind-spot banner). New: tests/test_style.py.
   - Inline `style=` count: 282 at P0 (tests/test_style.py STYLE_ATTRS_MAX; lower it as styles go).
2. P1 shell: DONE (commit "ui-a P1").
   - `remitwatch/static/shell.js` (one global, RWShell) draws the same shell on the app, Upload and Setup: the sidebar
     (Today; Work with its views Denials, Bundling, Downcoding, Contracts, Fees and takebacks; Faxes; Results; Insights:
     Bigger wins, Progress, Analysis; Settings: Data, Upload from EMA, Setup; Owner behind the lock: Write-off policy,
     Providers, Calculator), the header (search, health pill, alerts bell, Help with How RemitWatch works, Keyboard
     shortcuts and Search and go to), the phone's tab bar (Today, Work, Results, Find, More) and its More sheet.
   - Routes, ids and data- attributes stay: `#nav`, `#feedChip` (the pill's words), `#claimFind`, `#bellBtn`, `#lockBtn`,
     `#guideBtn`, `#keysBtn`, `#uploadLink`, `#swingBar`, `#scBar`, `#blindBar`, `#view`. TABS keys all have a place.
   - The pill: safe mode, then what the app cannot check in dollars (/api/ema/blind), then how far the remittances
     reach, then payments with no remittance, then overdue EMA exports; on Vercel always the press-only sentence.
   - Theme (System, Light, Dark) and the sidebar's width are kept per computer (localStorage, wrapped); no flash on load.
   - Page titles carry the count of what closes within 7 days, never patient data. Ctrl K or Cmd K opens Search and go to.
   - The brand mark (a remittance slip with a check) and favicons in `static/brand/` (icon.svg follows the theme;
     icon-32.png and icon-180.png drawn from it with Playwright by `$S/tk/units/ui-a/raster.js`).
   - Phone width: worklist cards keep the box that picks a row (bulk changes still work at 390 px).
   - Tests moved (same guarantee, new place): calc_model_check gate 9 (the Lock button names both tabs: shell.js) and
     r34 (tab strips wrap at phone width: rw.css); test_uploads (the Upload link: shell.js's place, hidden until
     /api/meta says so); test_hook_review (the pill names read-but-unchecked claims: shell.js); test_hotfix2_pages H8
     (the pill says how far remittances reach, the newest payment in its hover) and H10 (on a phone, Find in the tab bar);
     swing banner first inside Upload's and Setup's main. New: tests/test_shell.py.
   - Inline `style=` count: 278 (tests/test_style.py STYLE_ATTRS_MAX).
3. P4 Today: DONE (commit "ui-a P4").
   - Order: a banner only for a late feed (safe mode has #scBar above every page); the eyebrow date and the title
     "Today"; one strip (Work now as the whole with a composition bar, New and Follow-ups due as its parts, which add up
     to the whole as shown, and inside it in red what closes within 7 days; beside it Sent or paid this week and Lost to
     deadlines, last 30 days); the quiet "Not on today's list" line; the plan; What changed and Bigger wins side by side;
     the batches last (ui-work may move "Not on today's list" and the batches to Work).
   - The plan: fixed columns (number, words, money, button); the next step is marked is-next and has the page's one
     filled button "Start (N)"; the others say "Start"; a done step is one line with a check and a quiet Review link;
     "Plan · N of M done".
   - /api/ema/today: Work now carries `due7`, counted with the Worklist's own filter (state todo, the to-do statuses,
     deadline week, soonest first), so the red part is inside the whole and opens its own list (api_ema.py, one field).
     One definition with the plan's step 1, the red numbers on Work's views and the count in the title (api._todo_soon):
     a follow-up due on an item already sent is in Work now, but its filing window no longer closes on it.
   - Moved, without losing a figure: the nine tiles (scoreboard and items by state) to the top of Results
     (today.js tdBoard, one hook in results.js); EMA's exports line to the top of Data (today.js tdEmaInto, one hook in
     data.js) and to the pill; the other self-checks stay on Data with the dot in the sidebar.
   - Tests moved: test_ema_pages2 (the tiles, titles, clicks, compact phone lines and scoreboard read Results' board;
     the exports line and its links read the Data tab; load order); feed_page.js and clock_page.js wait for #tdPlan and
     read the date from the eyebrow. New: the strip adds up and opens its lists (with and without something closing
     within 7 days), the route's due7 equals the Worklist's count and /api/brief's due7 on both hosts (a sent follow-up
     due today is left out, a denial due on the 6th day counts, one due on the 7th day does not), the late-feed-only
     banner, the board and line placements (tests/test_shell.py).
   - Inline `style=` count: 275.
4. Brand finish: DONE (commit "ui-a brand").
   - The mark: a remittance slip with a check, one flat color on an inverse tile (shell.js markSvg; static/brand/icon.svg
     with its dark variant, icon-32.png, icon-180.png); the wordmark in Inter's Display cut, 600, -0.02em.
   - Favicons and theme-color for both themes in the three page heads; titles "(N) Work · Denials · RemitWatch" with
     the count that closes within 7 days and never patient data; the theme (System, Light, Dark) kept per computer
     and set before the first paint; Ctrl or Cmd K opens Search and go to (a place that matches comes first, then
     Find a claim); on a phone, Find in the tab bar opens it.
   - Tests: tests/test_shell.py (a browser run of the shell on a fresh store: palette, theme, titles, icons, Upload in
     the shell, the phone's tab bar, Find and More) with tests/shell_page.js.
5. docs/DESIGN.md: DONE (commit "ui-a docs"). The design system for the next person, from DESIGN.src.md with the
   critiques' corrections (CRITIQUE_premium sections 4 and 8): principles, type, words and numbers, space, color with
   both themes' values, state and status, the shell, components, how to change it and the checks, and what changed
   from the first design. No screenshots, no demo password, no em dashes. plans/tools/ui/checks.js now also fails Work
   on its row limits (first row at most 220 px at 1400x900, at least 7 rows at 1366x650 and 7 cards at 390x844, no row
   moving when the panel opens with --panel): Work fails them today, which is ui-work's job.
6. Final checks: DONE (see Numbers measured). Also: Work is marked in the sidebar's rail while one of its views is open
   (commit "ui-a: Work is marked ..."). The demo site is deleted; the screenshots stay in `$S/tk/units/ui-a/shots/final`.
7. After the checks: the 7-day count inside Work now is tested on both hosts with something closing (commit "ui-a: the
   7-day count ..."); Today's red part has one definition with step 1, the views' red numbers and the title (commit
   "ui-a: Today's red part ..."); the first-visit guide and the Analysis page name the places as the sidebar does (the
   guide said "the tabs after Today", "Worklist" and "soonest first"; the page said "More"), tested in
   tests/test_shell.py.

## Numbers measured

- P0, 1400x900 light (plans/tools/ui/checks.js): today sizes 6, accent 2, filled 1; worklist first row at 865 px (old
  markup), data, /upload (2 filled buttons: its own page), /setup: no overflow, no script error.

- P1, 1400x900 light and dark, 390x844 light and dark, 22 pages each (plans/tools/ui/checks.js): no overflow, no script
  error, no dialog on any page. Left for the page units: downcoding, faxes and /upload show 2 filled buttons (their own
  content); results shows 7 type sizes; overview's dark alerts link fixed here.

- P4, Today at 1366x650, 1536x730, 1400x900 and 390x844, light and dark: sizes 5 to 6, accent 2, one filled button,
  no overflow, no error. Start (N) on the first screen at every size. Results keeps 7 sizes (its own page: ui-pages).

- Final, every page at 1366x650, 1536x730, 1400x900 and 390x844, light and dark (176 runs of
  plans/tools/ui/checks.js): no overflow, no script error, no dialog and no low-contrast text on any page. Today, Data,
  Setup, Faxes' and Upload's shells, and every other page within 6 sizes and 8 accent elements. 50 runs fail, all on
  pages the later units own: Work and its views fail the row limits (first row at 523 to 821 px at 1400x900; 0 to 5
  rows on a laptop or phone screen: ui-work); downcoding, faxes and /upload show 2 filled buttons (their own content:
  ui-work, ui-pages); Results shows 7 sizes (its 34 px hero beside the board's 28 px figures: ui-pages).
- Tests, on the tip f1a4877 (the code; this file's commit adds no code):
  - The touched files' tests (the 22 test files this unit changed or that read what it moved, and the three that reach
    api_ema.py: test_decisions_review, test_fx_ema, test_payer_code_rules): the newest libraries (Python 3.13.18,
    SQLite 3.53.1, FastAPI 0.165.0, Starlette 1.10.0) 1273 passed, 1 skipped; Python 3.12.3 with SQLite 3.45.1 1273
    passed, 1 skipped. The skip is test_docs' private words check, which needs REMITWATCH_PRIVATE_WORDS; with the
    private list (outside the repository) it passes. In python3 and in Python 3.11.1 they ran inside the full suites.
  - Full suite in python3 (3.13.18, SQLite 3.53.1, FastAPI 0.162.0, Starlette 1.7.0), two shards: 4333 passed, 29
    skipped, 1 xfailed, 1 failed. The failure is test_hooks.py's weekly self-audit test ("attempt to write a readonly
    database" in cloud.build_serving): the test calls monkeypatch.undo(), which also undoes conftest's per-test
    REMITWATCH_TMP, so it builds the serving copy in the machine's shared /tmp/remitwatch, where another run's build
    removed the file mid-write. It passes alone (three times) and with its file (58 passed). Not this unit's code; the
    owner of tests/test_hooks.py can set REMITWATCH_TMP again after the undo. (A first attempt of this run stopped when
    the machine's disk filled; its results were thrown away.)
  - Full suite in Python 3.11.1 with SQLite 3.40.0, two shards: 4132 passed, 14 skipped, 1 xfailed, 169 failed and 48
    errors, every one of the 217 "Connection has no attribute serialize/deserialize" (that build has no store-image
    calls; CLAUDE.md allows these there).
  - Node checks: calc_model_check 79 of 79, lab_review_check 143 of 143.
  - Full runs used PYTEST_ADDOPTS="-o tmp_path_retention_policy=failed" with plans/tools/shard.sh, so a passed test's
    temporary folder is removed at once (the machine's disk is short).

## Next command

    cd $S/tk/wt/ui-a && NODE_PATH=$(npm root -g) node plans/tools/ui/checks.js --base http://127.0.0.1:<port> --user <demo user> \
      --pass <demo password> --owner <demo owner password> --shots $S/tk/units/<unit>/shots/now
    (start a demo with $S/tk/ui/demo/serve_demo.py; its sign-in is in that file's docstring, never in the repository)

## Notes for the units after ui-a

- ui-work: Today still shows "Not on today's list" (#tdMoreSlot, today.js tdMoreHtml) and the batches (#batchList);
  DESIGN P4 moves both to Work. The phone's list cards keep the box that picks a row (rw.css: table.wl td:first-child
  in grid area sel). checks.js now fails Work on its row limits.
- ui-panel: on a phone the item sheet covers the tab bar (z-index 30 over 15); Escape closes it first. features.js
  refreshHeader now only fetches the alerts and the sync status and calls index.html's shellHeader; the pill's words
  live in shell.js (RWShell.pill), tested in tests/test_shell.py.
- ui-pages: Results' board is `#rsBoard`, drawn by today.js tdBoard (the nine tiles, unchanged); Data's first line is
  `#dtEmaLine`, drawn by today.js tdEmaInto. Charts take `--viz-*`.
- ui-words: the person's name and menu belong in the sidebar's foot (`.nav-foot`), beside the theme.
- ui-signin: the sign-in page should link `/static/rw.css?v=...`, preload the Latin font and use `static/brand/icon.svg`.
- /api/ema/today gained `states.todo.due7` (api_ema.py), counted with the Worklist's own filter; it equals /api/brief's
  `work.total.due7_n` and `due7_cents` (one definition: step 1, the views' red numbers, the title).

## Open questions

- none
