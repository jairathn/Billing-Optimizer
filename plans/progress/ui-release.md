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
| d-1 | high | yes: a packet from another patient's note downloaded (200) and emailed with no warning | fixed |
| e-1 | high | yes: the reviewer's test fails on both hosts at 233c24f | fixed |

## Log

- e-2 (staff name in the decision log and the bundle). Verified: tests/test_staff_names_private.py's new cases fail at
  233c24f (7 of 8: the scrubber leaves "Grace", "Mark", "Pat" and "An" whole; the server and serverless decision logs
  keep "Marked Paid by Grace"). Fix: `runlog.Scrubber.people_exact` keeps a one-word staff name that the code also uses
  (or a short English word) as written, with its capital, and masks it where it is written so (the two-letter path too);
  `cloud.run_cloud_cycle` learns the journal's actors before the fold, so a warning logged while folding is masked (a
  test makes the fold fail with the name in its error). Broken on purpose: without people_exact 7 fail; without the
  cloud line the serverless case fails. Restored (git diff clean against the fix).
- d-1 (another patient's note in the panel's packet). Verified: a build from an invented note of another patient
  answered 200 with no warning, its PDF downloaded (200) and the note was kept as the visit's own. Fix: one rule for
  every way a packet goes out (`api._note_hold`, the rule the fax send already used): `appeals_build` works the note's
  identity out once, answers `note_hold` (why, visit date) when the note's text does not show this visit, and keeps the
  note as the visit's own only when it does; GET `/api/packets/{id}/pdf` and POST `/email` answer 409 "Check the visit
  note before it goes" until a person ticks; `?check=1` shows the PDF in the browser for that look (never a download);
  POST `/api/packets/{id}/note-checked` keeps the person's check with the packet (both hosts: `cloud.mark_packet_checked`)
  and a line in the item's history; the fax send takes the kept check as the tick, and a tick given at the fax is kept
  with the packet too. The panel: one blocking notice in place of Download, Copy and Send, with "Open it", the tick and
  "Use this note"; earlier packets, the route card's Open/Print the packet and the fax step wait for the same tick.
  Tests (tests/test_hotfix2.py, both hosts): the wrong note is held everywhere until the tick, then goes, the fax with
  no second tick; the right note downloads at once. Moved with the same guarantee: test_h6_a_scanned_note (the scan's
  own packet still never goes without the tick; a send naming no packet now builds from EMA's own note, since the scan
  is no longer kept as the visit's note) and test_identifiers_stay_on_the_server (a person ticks before the download).
  Broken on purpose (no hold): 3 fail; restored.
- e-1 (one definition of the last day). Verified with the reviewer's test (tests/test_last_day.py): at 233c24f the
  brief counted a last-chance item lost while Work's missed list did not hold it, and Results' at-risk list left it out,
  on both hosts. Fix: `states.last_day` and `states.last_day_sql` (our target until it passes, then the payer's own
  limit while still ahead) used by `api._last_day`, `results.live_sql` (at risk, its order, given up recoverable and
  past), `insights.missed` and `insights.work` (a last-chance item counts inside its window, dated by the limit).
  Broken on purpose (insights and results back): both host cases fail; restored.
