# Unit ui-words: progress (plans/briefs/2026-11-28_ui.md, unit ui-words)

Branch `wip/ui-words`, worktree `$S/tk/wt/ui-words` (base: integration tip 9a5ef3f merged with wip/ui-a f1a4877). Demo on
port 8775, private folder `$S/tk/units/ui-words` (mode 700). Invented data only.

The unit: the words (DESIGN P14) and CRITIQUE_biller change 5 (know who is working).

## Merge notes

- wip/ui-a into the integration tip: one conflict in index.html (ui-a moved the page's `<style>` block into rw.css; the
  integration side had added the payer routes card's `.rc-*` rules to that block). The block stays gone; the `.rc-*`
  rules moved into rw.css with the design's tokens (after `.todo-err:empty`).

## Plan

1. Know who is working (part 1)
   - Server: the page sends the person's own name with every change (header `X-RemitWatch-Person`, URI-encoded);
     `api.acting` checks it (letters, spaces, `.'-`, 1 to 40 characters, never a word the app writes itself) and every
     route that records who did something takes it instead of the shared sign-in. Stored where the item's history is
     already kept (`finding_events.actor`; faxes, hide rules, payer fax numbers, policy, report decisions keep theirs),
     so the serving copy and the journal carry it on Vercel with no new column.
   - The scrubber learns the staff names the store holds (runlog.Scrubber.learn) on both hosts.
   - `/api/people`: the names already used (the picker offers them) and what each person did today and this week (the
     manager's Summary, unit ui-pages).
   - Work rows and the panel show "by <name>" where a person acted (small, separate edits in desk.js and features.js).
   - shell.js: the person at the sidebar's foot, asked once per computer (no browser dialog), kept in `rw:person`
     (wrapped); every POST of the app, Upload and Setup sends it.
   - Tests: both hosts; a full cycle on each host with an invented staff name planted, no byte of it in any log,
     manifest or bundle.
2. The words (part 2): story.py, insights.py, alerts.py, report.py; empty and loading states.
3. Checks, suites in four environments, final merge and push.

## Done

1. Know who is working (CRITIQUE_biller change 5), commit 7107212 and later.
   - shell.js: the person picks their own name once per computer (the sidebar's foot, a small card, no browser box);
     browser storage holds only that name (`rw:person`), every read and write wrapped. One wrapper around fetch makes
     every change a page sends (the app's api(), Results, Upload, Setup, any call added later) carry it in
     `X-RemitWatch-Person`, never a read and never another host.
   - api.acting keeps the name as the actor in the item's history in place of the shared sign-in; a change without one
     keeps the sign-in, as before. Let go, Lost, Paid, Sent, Started, the bulk changes, lines written down, faxes, hide
     rules, payer fax numbers, report decisions, the write-off policy, the self-checks' acceptances and the Results
     settings and spot checks all take it. finding_events.actor already travels in the journal and the dashboard's copy,
     so Vercel needs no new column and no serving build bump; tests/test_who_is_working.py runs both hosts.
   - /api/people: the names in use (the picker offers them) and what each person did today and in the last days, by
     status, with the paid amounts and the lines written down; the shared sign-in counted apart as unnamed, the app's own
     lines never. For the manager's Summary (ui-pages).
   - Work's rows (desk.js), the panel's status line (index.html's drawer), its verdict override and its history
     (features.js) say "by <name>" where a person acted: small separate edits in files other units own.
   - story.py: an item a person marks Started says "Started by <name> on <day>" at once, not "a person".
   - Privacy: runlog.Scrubber.learn_people learns the staff names the store holds (the histories and the decisions that
     keep who made them), on both hosts and again once a serverless run folds the journal.
     tests/test_staff_names_private.py plants invented staff names in a full cycle on each host and finds no byte of
     them in any log, manifest or bundle.
2. The words (DESIGN 2.2 and P14).
   - story.py: one words pass on every sentence it hands a page (verdict, next step, steps, instructions, the story's
     rows and its balance line): one date format (a rule's quoted hint that still carries 2026-04-20 reads Apr 20,
     2026), zero as $0, "the analysis" for "the run", a sentence said twice in a row said once; EMA's WRITE_OFF and
     IGNORE said in words; the paid-twice step says its label and "do not ask for more" once. The verdict and the next
     step carry `headline` and `why` (the first sentence and the rest, split at a sentence end only) for the panel.
   - insights.py: its dates in the one format (Mar 2, 2026, was March 2, 2026 and ISO in five lines).
   - alerts.py: ISO dates and UTC times become the practice's day and clock (Nov 20, 2026, 5:17 AM); "analysis" for
     "run" in the swing alert and its mail.
   - report.py (the weekly summary): statuses as the pages say them, what an item is in words (never a rule's code
     name), counts with their noun, dates in the one format.
   - test_story.py's plain() now checks DESIGN 2.2 on every story it reads (66 tests). Tests that read old wording moved
     with the same guarantee (each commit names them).

## Checks

- Measured with a scan of every sentence the story tests produce (175 tests, invented data): before, 10 ISO dates, 99
  sentences with $0.00, 4 code words for the analysis; after, none of these (the code-word hits left are the tests'
  own invented item ids).
- plans/tools/ui/checks.js on today, worklist, denials, /upload, /setup at 1400x900 and 390x844, light and dark: no
  overflow, no script error, no dialog, no contrast failure. The failures are the ones ui-a recorded for other units:
  Work's row limits (first row at 821 px, rows on a phone, a row moving 18 px when the panel opens: no demo row carried
  a name, so not the "by" mark) and /upload's two filled buttons.
- A look at the panel at 1400x900 light and 390x844 dark with an invented name chosen: the sidebar's foot says the
  name, the status line says "Started by <name>". Screenshots stay in the private folder.
- Break and restore: the words pass off fails 27 story tests; the started-by lookup off fails its test. Both restored.
- Node checks: calc_model_check 79 of 79, lab_review_check 143 of 143.

## Left (honestly)

- Verdicts and steps over 20 words: in the story tests' sentences 23 of 103 verdicts and 38 of 84 next steps still
  have a first sentence over 20 words. The headline/why split is there for the panel; rewording those branches is left.
- "Who: Owner / A/R follow-up biller" (next_step.who) is still a role; the critique asks for a person or nothing. The
  API gives worked_by for the panel (ui-panel) to show instead.
- The three "Your name" boxes (Policy, Results, Upload's how-to) are still there; the server already takes the chosen
  name when Policy's and Results' boxes are empty. Removing them is ui-pages' (policy.js, results.js, upload.js).
- Start and Next skipping items someone else started in the last 2 hours, and a Mine view: not built (ui-work).
- Rows' hints, titles and state bases stored by detection (detect.py, archive_rules.py, ema_checks.py) still carry
  ISO dates in some texts; they are rule modules outside this unit. "Big change in one run" in swing.js and
  features.js (ui-pages, ui-panel).
- Empty and loading states in the page scripts: not touched (they live in files other units own this round).
- day() writes the year always (Mar 3, 2026); DESIGN drops it in the current year. Left: it needs today threaded through.

## Next command

    cd $S/tk/wt/ui-words && python3 -m pytest -q -p no:cacheprovider tests/test_who_is_working.py tests/test_story.py

## Open questions

- none
