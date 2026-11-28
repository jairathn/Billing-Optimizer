# UI release fix round: progress (the one review of wip/ui-release, findings d-1 to d-15 and e-1 to e-4)

Branch `wip/ui-release` (worked on the local branch `ui-rel-fix` tracking it), worktree `$S/tk/wt/ui-rel-fix`, base
233c24f. Demo on port 8771, private folder `$S/tk/units/ui-rel-fix`. Invented data only.

The review walked the merged UI (ui-a, ui-panel, ui-pages, ui-work, ui-words) as a designer and a biller (d-*) and as an
engineer for privacy and both hosts (e-*). Each finding is checked against the branch first; the verified highs and
mediums are fixed worst first, with a test that fails before the fix (broken on purpose, then restored); the lows are
fixed when the change is one safe line, else they wait with the reason.

## Findings

| id | severity | verified | state |
|----|----------|----------|-------|
| e-2 | high | yes: a one-word name the code uses (Grace, Mark, Pat, An) is never learned | fixed |

## Log

- e-2 (staff name in the decision log and the bundle). Verified: tests/test_staff_names_private.py's new cases fail at
  233c24f (7 of 8: the scrubber leaves "Grace", "Mark", "Pat" and "An" whole; the server and serverless decision logs
  keep "Marked Paid by Grace"). Fix: `runlog.Scrubber.people_exact` keeps a one-word staff name that the code also uses
  (or a short English word) as written, with its capital, and masks it where it is written so (the two-letter path too);
  `cloud.run_cloud_cycle` learns the journal's actors before the fold, so a warning logged while folding is masked (a
  test makes the fold fail with the name in its error). Broken on purpose: without people_exact 7 fail; without the
  cloud line the serverless case fails. Restored (git diff clean against the fix).
