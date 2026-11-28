# Unit ui-pages: progress (plans/briefs/2026-11-28_ui.md, unit ui-pages)

Branch `wip/ui-pages`, worktree `$S/tk/wt/ui-pages` (base: integration tip fa0aafa merged with wip/ui-a 8c1ed1a; the
one conflict, index.html's head, keeps ui-a's head; the route card's styles move to rw.css in ui-panel). Demo on port
8774, private folder `$S/tk/units/ui-pages` (mode 700). Invented data only.

## Done

1. Results (commit "ui-pages Results"): This week first (CRITIQUE_biller change 10): back, lost to deadlines, at risk
   in 14 days, given up with no decision, from the scoreboard's own current week and live at-risk figure (one
   definition with the board and Today); Needs you (write-off patterns to decide, given up with no decision, a late
   feed, payers with letters and no fax number); the team from /api/people (ui-words) once the site has it, hidden
   until then. The chart (CRITIQUE_premium change 11): money back by week at even steps of time, the last complete
   week in the accent, the current week outlined "so far", a line to the to-date figure, short months, --viz tokens,
   its label and the week table as the text equivalent. Sections, not cards (.secs, rw.css section 23). Inline
   style= 275 to 200.
2. The bell (commit "ui-pages bell and charts"): shell.js bellMenu, the five newest alerts with See all, on the app,
   Upload and Setup; Escape and a click outside close it.
3. Charts in both themes (same commit): index.html's palette is read from the --viz tokens, Chart.defaults take the
   font, ink and grid from the tokens, a theme change draws every chart again; straight lines; "Jan 2026", "Feb"
   month labels; no rotated labels; each canvas says its datasets in an aria-label.

4. Faxes, Data, Upload, Setup (commit "ui-pages Faxes, Data, Upload, Setup"): sections; one filled button (Faxes'
   follow-up Send; Upload's most overdue export); Faxes in DESIGN P8's order with no setting names on the page; Data
   with a title and the header's old counts in one line; Overview's second recovered figure renamed "Paid on a later
   remittance" (brief rule 3); Providers' context gray from the token; docs/DESIGN.md components.
5. Tests: tests/test_ui_pages.py (new), the bell in tests/test_shell.py's browser run (tests/shell_page.js).

## Numbers measured (plans/tools/ui/checks.js on the demo, invented data)

- Before (1400x900 and 390x844, light and dark): Faxes and /upload 2 filled buttons at 1400; Results 7 font sizes
  (12, 13, 14, 16, 24, 28, 34 on a desk; 12, 13, 14, 16, 20, 22, 28 on a phone).
- After, every page this unit owns (faxes, results, progress, data, more, overview, alerts, explorer, payers, policy,
  providers, calculator, /upload, /setup) at 1366x650, 1536x730, 1400x900 and 390x844, light and dark: 112 runs, 0
  fail. Worst per page: sizes 6 (results, progress), 5 or fewer elsewhere; accent 3 (explorer), 0 elsewhere; filled
  buttons at most 1 (0 on the first screens); no low contrast, no sideways overflow, no script error, no dialog.
- Inline style= attributes: 275 to 200 (tests/test_style.py STYLE_ATTRS_MAX).

## Tests (tip de82ca4 plus this file)

- The touched files' tests (the 18 test files that read what this unit changed, and tests/test_ui_pages.py): python3
  (3.13, SQLite 3.53) 862 passed, 25 skipped before the last small commits, then test_shell, test_style and
  test_ui_pages again: 27 passed; Python 3.11.1 with SQLite 3.40.0: 843 passed, 33 failed and 20 errors, all 53
  "'sqlite3.Connection' object has no attribute 'serialize'" (no store-image calls in that build; CLAUDE.md allows these).
- Break and restore: the chart's current week drawn filled and the week's at-risk figure read from the week row each
  make tests/test_ui_pages.py fail (2 failed), restored exactly (9 passed).
- Full suite once in python3, two shards (plans/tools/shard.sh): 2309 passed, 28 skipped; 2373 passed, 1 skipped,
  1 xfailed. No failure.
- Node checks: calc_model_check 79 of 79, lab_review_check 143 of 143.
- The demo site on port 8774 was stopped and deleted; screenshots stay in the private folder.

## Left (honest list)

- Results: the board under the week (today.js tdBoard, ui-work) repeats Recovered, Lost, At risk and Given up to date,
  and the Week by week section repeats them a third time as tiles (rsScoreHtml; tests read its links). Folding them
  is one decision for the integrator: drop rsScoreHtml's tiles (keep its links in a line) once the board is final.
- Progress (features.js, ui-panel): its charts now take the token palette, but "recovered" against "being worked" are
  still two series colors, not two steps of one hue with lost in gray (change 9), and DESIGN P6 retires Progress into
  Results: not done.
- Data: section tabs (Feeds, EMA reports, Payments with no remittance, Credits, Running on its own, Logs) and a folded
  "N checks passed" line (DESIGN P7): not done.
- Faxes: payer fax numbers stay on Faxes (last section), not moved to Settings with one Edit per row; the tables'
  times come from faxes.js faxTime ("2026-11-27 10:00 UTC"), which tests/test_practice_day.py reads: left as is.
- Upload (DESIGN P10: one drop zone that names the file before reading it, compact checklist) and Setup (grouped
  Sign-in, Feeds, Fax, Mail, AI; a Technical fold): only sections and one filled button so far.
- Phones open on the Summary (CRITIQUE_biller change 10): Results is the phone's Summary, but the app's home on a phone
  stays Today (shell.js routing and today.js belong to other units' decisions).
- The classic analyst tables (Overview, Who and what, Payers and rates): first column fixed and stacking on a phone
  (DESIGN P12): not done.
- The team part of the week shows once ui-words' /api/people is merged (tested here with a canned answer).

## Notes for the integrator

- rw.css section 23 is this unit's; `.secs` turns a page's panels into sections and `.panel.act` keeps a card.
- tests/test_style.py STYLE_ATTRS_MAX: this branch sets 200; other units lower it too: set it to the merged count.
- index.html: `palette` is now `const palette = [];`, filled from the --viz tokens by vizTheme() at load and on a
  theme change; features.js and desk.js keep using palette[i]. initNav no longer sends the bell to #alerts: shell.js
  bellMenu opens the list (RWShell.bellMenu).
