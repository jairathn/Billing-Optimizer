# Unit ui-panel: progress (plans/briefs/2026-11-28_ui.md, unit ui-panel)

Branch `wip/ui-panel`, worktree `$S/tk/wt/ui-panel` (base: the integration tip merged with wip/ui-a 8c1ed1a). Demo on port
8773, private folder `$S/tk/units/ui-panel` (screenshots, probes, check results). Invented data only.

## Parts

1. The item panel (DESIGN P3; CRITIQUE_premium 1 and 4; CRITIQUE_biller 1, 2, 4, 9 and 15) and the route card as the
   panel's send-to-payer step (DESIGN 6): DONE (commits "ui-panel part 1", "ui-panel part 2", "ui-panel part 3").
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

(filled in at the end of the unit)

## Left (honest)

(filled in at the end of the unit)
