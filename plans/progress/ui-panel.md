# Unit ui-panel: progress (plans/briefs/2026-11-28_ui.md, unit ui-panel)

Branch `wip/ui-panel`, worktree `$S/tk/wt/ui-panel` (base: the integration tip merged with wip/ui-a 8c1ed1a). Demo on port
8773, private folder `$S/tk/units/ui-panel` (screenshots, probes, check results). Invented data only.

## Parts

1. The item panel (DESIGN P3; CRITIQUE_premium 1 and 4; CRITIQUE_biller 1, 2, 4, 9 and 15) and the route card as the
   panel's send-to-payer step (DESIGN 6): DONE (commits "ui-panel part 1", "ui-panel part 2", "ui-panel part 3" and the
   phone-rules fix), with what is left below.
   - Head (features.js panelHeadHtml): where you are ("Denials · Item 5 of 143", or state.queueLabel for a plan step) with
     Previous, Next and Close; the title; the patient (names on only), the payer, the date of service, the code and the
     claim ID (one click or y copies it); the state chip beside the person's status, one button with its menu (Started,
     Sent, Paid, then Lost and Let go apart, Back to To do; data-st kept on each); At stake and Due (a follow-up says
     "Follow up ... today", never a window that closes).
   - No browser box in the panel: Sent, Paid, Lost, Let go, Billed, Billed the patient, Done, Posted, Reopen and Accept as
     policy open a small form under the head (stForm: focus in the first field, Enter saves, Esc cancels, Save says
     "Saving…" and takes one press). Let go and Lost take a reason as a chip (six reasons) or in words; the server still
     holds to a reason. Paid's amount starts empty with the amount in dispute in its help. Every save shows a toast with
     Undo (8 seconds; u undoes); Started and To do stay on the item, the others open the next one.
   - Log what happened is the call or portal check: what the payer said as a chip (In process, Paid, Denied, No claim on
     file, Needs records, Wrong payer; keys 1 to 6 while it is open), the amount (Paid) or the code (Denied), the
     reference, the note; Save opens the next item. Each answer is a status the rules know (in process waits on the payer;
     Paid is Paid; the others stay to do with the answer in the history).
   - Keys (features.js panelKey, the sheet in index.html KEYS): Enter presses the main button, s p l b open their forms, n
     logs a call, e opens EMA, y copies the claim ID, 1 to 9 copy the route's fields, c copies them all, u undoes, j and k
     move; Esc closes the menu, then a form, then the panel. The panel takes the focus when it opens.
   - Instant loop: the answers of the next two items (and the one before) are asked for while one is read and kept two
     minutes (dropped on any change a person makes); until an answer comes the panel shows the head's frame, with
     placeholders only after 250 ms (never "loading…"). Measured on the demo: the next item paints 36 to 49 ms after j.
   - The route card is the send step: its line with "Easiest", its main button right under it, numbered copy rows (1 to 9)
     with a check on what was copied, the next field to paste marked and "3 of 8 copied", Copy all, the privacy line, I
     sent it with an optional reference in its own block. It leads (its button the one filled button, above the next
     step's buttons) once nothing is left to prepare; while a packet, a note or a decision comes first, the next step's
     button leads and the route follows below it.
   - One filled button per screen: the page beside the docked panel keeps none; on a phone the page under the full sheet
     is hidden. The panel's title takes the page title's size (24 px; 16 on a phone) so a page and its panel keep to six
     sizes.
   - The panel never contradicts itself (in part): an item that is over or waits on the payer, or whose next step bills
     someone, shows no appeal letter and no fax beside its next step.
   - claim.js: its four inline looks are classes. Inline `style=` count 275 -> 266 (tests/test_style.py STYLE_ATTRS_MAX).
   - docs/DESIGN.md: the item panel in Components, tests/test_panel.py in the tests.
   - Tests moved with the same guarantees (none skipped or deleted): test_ema_pages (state, outcome, place, Previous and
     Next read the panel's head), test_desk (the panel's Let go and Lost need a reason from its form), test_fx_people and
     fx_people_xp.js (the Paid amount in the call form's own field), hotfix2_page.js and test_hotfix2_pages (Sent's Save
     acts once, the Paid form refuses bad amounts with their reasons, no dialog), test_routes and routes_card_page.js (the
     card above the buttons when it leads, below them otherwise). New: tests/test_panel.py with tests/panel_page.js.

## Numbers measured

- plans/tools/ui/checks.js on four panels of the demo (a chase whose route leads, an appeal whose packet comes first, an
  underpayment, an item that waits on the payer), each at 1366x650, 1536x730, 1400x900 and 390x844, light and dark (32
  runs): no overflow, no script error, no dialog, no low-contrast text; 6 font sizes on a desk (the page and its docked
  panel together), 4 to 5 on a phone; 1 to 3 accent elements; at most 1 filled button (1 on every screen but the chase at
  1366x650, whose route button sat under the fold before it moved up under its line; the 12 runs after that move pass).
- tests/panel_page.js on the invented store: the next item's answers were asked for while the first was read and Next
  asked for nothing more, on both hosts and both widths. On the demo: the head painted 124 ms after a click on a row the
  first time, the next item 36 to 49 ms after j.
- Final look: 1400x900 light and 390x844 dark (screenshots in the private folder only).
- Tests, in python3 (3.13, SQLite 3.53): the touched files' tests (test_panel, test_ema_pages, test_desk, test_fx_people,
  test_hotfix2_pages, test_routes, test_routes_review, test_style, test_decisions_review) and those that read the panel
  (test_shell, test_ema_pages2, test_practice_day, test_review_rates, test_hotfix2c, test_claims, test_fax, test_fx_hosts,
  test_payer_code_rules, test_workable, test_m1_837, test_hooks) all pass (438 in the second group).
- In Python 3.11.1 with SQLite 3.40.0, the touched files' tests with test_shell, test_docs and test_claims: 436 passed, 1
  skipped, 13 failed, every one of the 13 "Connection has no attribute serialize" (the serverless store image; CLAUDE.md
  allows these there).
- Full suite once in python3 (3.13, SQLite 3.53) on cacff8e (the integration tip fa0aafa and wip/ui-a merged in), two shards
  with plans/tools/shard.sh: 4677 passed, 29 skipped, 1 xfailed, none failed.
- Node checks: calc_model_check 79 of 79 (gate r34 had read the panel's phone block as the first one: it is now written
  "screen and"), lab_review_check 143 of 143.

## Left (honest)

- CRITIQUE_biller 1 in part: the call or portal check records what the payer said, the reference and a note, but has no
  "Follow up on" date (the server takes no date from a person; the next run dates the follow-up, and the form says so),
  and an answer does not yet change the next step by a new rule (no claim on file, records asked, a wrong payer stay to
  do with the answer in the history; the rules in story.py would need them).
- CRITIQUE_biller 2 in part: a fax that came in and is not confirmed does not lead the panel yet (faxes.js, ui-pages);
  Expired has no "Appeal late anyway". The route's own date (the request's limit, say a status inquiry by Dec 22) can
  differ from the next step's date (Jun 20) on the same item: worth one wording, with ui-words and the routes table.
- CRITIQUE_premium 1 in part: queue mode from Today is ready on the panel's side (state.queueLabel and state.worklist,
  then openDetail: the crumb says "Step 2 · Item 1 of 36"), but Today's Start (today.js, ui-work) does not call it yet;
  rows collapsing and counts ticking on Work, row actions on hover, and the end-of-step card are not built. The head is
  painted from kept answers, not from the row (a row carries too little for the whole head).
- CRITIQUE_premium 4 in part: the steps stay folded (a review C1 test asks for that), not a stepper with an action per
  step; the evidence stays in folds, not in tabs (Story, Claim, History, Letter); no Technical view for administrators.
- Phone: no pinned bottom bar; the head with Previous and Next stays at the top of the sheet instead.
- Kept answers can be two minutes old for an item someone changes in another tab; a change sent from such a view is
  refused by the server (the status the panel showed) and the form says so.
- Opening an item asks for the next two items' answers too (four more requests per open).

## Next command

    cd $S/tk/wt/ui-panel && NODE_PATH=$(npm root -g) node plans/tools/ui/checks.js --base http://127.0.0.1:<port> --user <demo user> \
      --pass <demo password> --pages item=<finding id>,item=<finding id> --shots $S/tk/units/<unit>/shots/now
    (an item's panel is the page "item=<finding id>"; start a demo with $S/tk/ui/demo/serve_demo.py)

## Notes for the units after ui-panel

- ui-work: Today's Start can open the step's first item in the panel: set state.worklist to the step's ids (and
  state.wlTotal), state.queueLabel = 'Step 2', then openDetail(first); clear state.queueLabel when the list is drawn again.
  The panel drops its kept answers on any change (panelCacheDrop); a list redrawn after a change needs nothing more.
- ui-pages: faxDrawerHtml and tieFaxHtml (faxes.js) still draw their own rows in the panel; a fax that came in and is not
  confirmed should lead the panel (CRITIQUE_biller 2). The panel hides #letterRow and #fxOneRow while an item is over,
  waits on the payer, or its next step bills someone.
- ui-words: the toast words are in features.js ST_SAID; the forms' titles and help in stSpec and xpBind's act.
