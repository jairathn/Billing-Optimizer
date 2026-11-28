# Unit ui-work: progress (plans/briefs/2026-11-28_ui.md, unit ui-work)

Branch `wip/ui-work`, worktree `$S/tk/wt/ui-work` (base: integration tip 9a5ef3f merged with wip/ui-a 8c1ed1a; then the
newer tip fa0aafa). Demo on port 8772, private folder `$S/tk/units/ui-work` (mode 700: site, shots, logs). Invented
data only.

The unit: Work (DESIGN P2 and P11), CRITIQUE_premium changes 3 and 10, CRITIQUE_biller changes 3, 6, 7, 8, 13 and 19.

## Merge notes

- wip/ui-a into the tip: one conflict in index.html (ui-a moved the `<style>` block into rw.css; the tip had added the
  routes card's `.rc-*` rules to it). Resolved exactly as wip/ui-words resolved it (its merge commit f27efb8: the block
  stays gone, the `.rc-*` rules in rw.css), so the units merge cleanly with each other.
- wip/ui-words adds `worked_by` to list rows and "by <name>" beside the status pill in rowHtml. This unit rewrote
  rowHtml: the row's payer cell shows the last thing a person did with `<span class="by">by NAME</span>` from the same
  `worked_by` / `worked_at` fields, so the integrator keeps this side of that hunk (ui-words' field and class names are
  used as they are).

## Done

1. The Work views (commit "ui-work: Work shows the list first ..."):
   - One header row: the view's name and its counts as segments (Work now, Closes in 7 days, red only when it holds
     something, Follow-ups due, Waiting, To decide), each opening its list as the tiles did (`data-tile`, same counts).
     One sticky toolbar: kind of work (a select with count and dollars), payer, order, search ("Search /", widens on
     focus), Filters, Export, and the view's one filled button, Start. The description line went to the empty state.
   - Rows of a fixed height (56 px, two lines a cell, ellipsis, `table-layout: fixed`): no row moves when the panel docks.
   - The row (change 6): Due with its verb and date; the patient, never blank ("Name not on file" with the member ID),
     over the claim number (a click copies it) and the date of service; the payer over the last thing a person did; what
     happened over the code and the payer's reason code (meaning on hover); the amount in whole dollars over the status,
     the state, or "low chance" / "high chance" when the payer's recovery rate moved the row (basis on hover; only when
     the order is the money ranking). Orders: Most likely money first, Biggest amount, Due soonest, By payer (most money
     first), By payer name.
   - Last chance (change 3): `api._last_day`, the last day the money can be asked for (our target; once it passed, the
     payer's own limit while ahead). The soon ranges (week, month, due) and the closed ranges (expired, missed) and the
     sorts by date go by it, so Closes in 7 days, Today's step 1, the views' red numbers and the title count such an
     item (one definition, through `_filters`) and it sorts first. "valid" keeps our target date (orchestrator decision
     2: bulk appeal and Today's count of EMA write-offs still appealable go by it). The row: "Bill by Dec 5" in red over
     "7 days left, last chance", the target and the limit on hover.
   - One payer in one sitting (change 8): the claims to chase and the follow-ups group by payer (sort `payer_money`;
     `/api/findings` returns `groups`: each payer's count and dollars in the whole list), each payer's line with "Copy
     the list for the call" (claim number, date of service, billed amount; the patient only with names on; clipboard
     only, nothing to the server).
   - The owner's work (change 19): on the Worklist (`owner_last=1`) a rate pattern (detect.PATTERN_SIGNALS) comes after
     every claim under "For the owner", counted in the count line and Today as before (one definition), with
     `owner_work` {n, cents}; Contracts lists them as before.
   - Late payers (change 13): rows of a payer whose remittances stopped (the Data tab's CH-8 check, `payer_stopped` in
     the brief's health on both hosts) carry the can't-be-checked mark (amber eye) with "has not seen X's payments since
     <date>. Check the payer's portal first." on hover.
   - Under the list: "Not on the list: Done · Accepted · Expired · Can't be checked", Everything to do in any window,
     Hidden, the fix-once notes, What to do by kind of work (a fold, with Appeal everything appealable), the
     never-answered panel and Details.
   - Phone (change 10): no page title (the header's), the segments in one scrolling row, Filters and Start in one row,
     two-line cards (the patient and the amount; what happened, the payer and the due date in red inside 7 days), the
     box that picks a row kept.
   - P11: Bigger wins' empty "Raise the rates" table says why and what fills it; Fees says "None at $5,000 or more".
2. Fees and takebacks in one header row (commit "ui-work: Fees and takebacks ..."): the three lists as segments, the
   takebacks in fixed rows, what to do under the list; the visit notes' batch button in the header row, its progress
   under the toolbar only while it runs.

## Tests

- Moved, same guarantee: test_ema_pages (the Due cell's verb and date, why it waits on hover; the payer's own limit in
  the house date format; an older follow-up link keeps its meaning and groups by payer), test_shell (the guide names the
  Worklist's first order in the order menu's words: desk.js ORDERS).
- New, both hosts (tests/test_ema_pages2.py): last chance counts in 7 days (Today's route, the list, the brief) and
  sorts first, is not closed, keeps out of "valid", and closes once the payer's limit passes; payer groups come together,
  sorted by money, adding up to the count line; the owner's pattern comes last on the Worklist and is counted.
- Broken on purpose (`_last_day` returning the target date; no owner order): both new tests fail; restored.
- Inline style= count 274 (tests/test_style.py STYLE_ATTRS_MAX lowered from 275).

## Numbers measured (plans/tools/ui/checks.js --panel, demo store, light and dark)

See the unit's report for the final table. Work views (Worklist, Denials, Bundling, Downcoding, Contracts): first row at
215 px at 1366x650, 1536x730 and 1400x900; 7 rows at 1366x650, 9 at 1536x730, 12 at 1400x900; 8 to 9 cards at
390x844; no row moves when the panel opens; 5 font sizes (4 on a phone), 1 accent element, 1 filled button, no
overflow, no script error, no dialog, no low-contrast text. Fees: first row at 194 px; the demo holds 5 takebacks in 90
days, all on the first screen (the 7-row check cannot reach 7 there).

## Left (for the review of the whole app, or the units named)

- "Log one call for these" on a payer's group (change 8) needs a call log that writes one entry per claim: the call and
  portal check is ui-panel's (CRITIQUE_biller change 1). The group line has the copy for the call only.
- The panel's sentence "Check X's portal first: RemitWatch has not seen X's payments since ..." (change 13's second
  half) belongs to the next step's words (story.py: ui-words) and the panel (ui-panel); the row has the mark.
- Payer names in display case (P11): the payer groups already read in display case in the demo; no rewrite of names
  was added (acronyms such as BCBS would suffer).
- Today still shows "Not on today's list" and the batches (ui-a's note); with the views grouped by payer the batches'
  job is done on Work, so Today's batches could go in a later pass (today.js tdMoreHtml, #batchList).
- The weekly email and the alerts count "closes within 7 days" in their own sentences (report.py, alerts.py: ui-words);
  the pages and the brief count last-chance items through `_filters`.

## Next command

    cd $S/tk/wt/ui-work && NODE_PATH=$(npm root -g) node plans/tools/ui/checks.js --base http://127.0.0.1:8772 --user <demo user> \
      --pass <demo password> --pages worklist,denials,bundling,downcoding,contracts,fees,wins --panel --shots $S/tk/units/ui-work/shots/now

## Open questions

- none
