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

- (nothing yet)

## Next command

    cd $S/tk/wt/ui-words && python3 -m pytest -q -p no:cacheprovider tests/test_shell.py tests/test_runlog.py

## Open questions

- none
