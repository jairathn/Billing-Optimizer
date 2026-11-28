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
   - /api/ema/today: Work now carries `due7`, counted with the Worklist's own filter (state todo, deadline week), so the
     red part is inside the whole and opens its own list (api_ema.py, one field).
   - Moved, without losing a figure: the nine tiles (scoreboard and items by state) to the top of Results
     (today.js tdBoard, one hook in results.js); EMA's exports line to the top of Data (today.js tdEmaInto, one hook in
     data.js) and to the pill; the other self-checks stay on Data with the dot in the sidebar.
   - Tests moved: test_ema_pages2 (the tiles, titles, clicks, compact phone lines and scoreboard read Results' board;
     the exports line and its links read the Data tab; load order); feed_page.js and clock_page.js wait for #tdPlan and
     read the date from the eyebrow. New: the strip adds up and opens its lists, the route's due7 equals the Worklist's
     count, the late-feed-only banner, the board and line placements (tests/test_shell.py).
   - Inline `style=` count: 275.
4. Brand finish: next.
5. docs/DESIGN.md.

## Numbers measured

- P0, 1400x900 light (plans/tools/ui/checks.js): today sizes 6, accent 2, filled 1; worklist first row at 865 px (old
  markup), data, /upload (2 filled buttons: its own page), /setup: no overflow, no script error.

- P1, 1400x900 light and dark, 390x844 light and dark, 22 pages each (plans/tools/ui/checks.js): no overflow, no script
  error, no dialog on any page. Left for the page units: downcoding, faxes and /upload show 2 filled buttons (their own
  content); results shows 7 type sizes; overview's dark alerts link fixed here.

- P4, Today at 1366x650, 1536x730, 1400x900 and 390x844, light and dark: sizes 5 to 6, accent 2, one filled button,
  no overflow, no error. Start (N) on the first screen at every size. Results keeps 7 sizes (its own page: ui-pages).

## Next command

    cd $S/tk/wt/ui-a && NODE_PATH=$(npm root -g) node plans/tools/ui/checks.js --base http://127.0.0.1:8771 --user <demo user> \
      --pass <demo password> --owner <demo owner password> --shots $S/tk/units/ui-a/shots/now
    (the demo's sign-in is in the docstring of $S/tk/ui/demo/serve_demo.py; never in the repository)

## Open questions

- none yet
