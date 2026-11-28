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

## Next

The checks at the four sizes in both themes on every page this unit owns; the touched tests in python3 and 3.11.1;
the full suite once in python3; both node checks; final merge and push.
