# Unit replay-fix: progress

Branch `wip/replay-fix` (worktree `$S/tk/wt/replay-fix`), from the integration tip 7b2aeb0. Private folder
`$S/tk/units/replay-fix` (mode 700). The two code bugs the month-by-month replay confirmed (their verdicts are in
`$S/tk/replay/replay_finish_result.json`, result.verdicts; the first verdict, one claim keyed two ways, is gapfix G8's).

## The two bugs

1. posted_money falls falsely when a payer name changes group (`remitwatch/selfcheck.py`, `check_posted`). The marks are
   kept per (service month, closed posting month, payer GROUP, measure); the groups follow the name rules of now (learned
   aliases, REMITWATCH_EMA_PAYER_ALIASES, a release's rules), so a moved name's money reads as a fall of its old group and
   the critical 'hold' check puts the app in safe mode (the replay's August: 93 cells, $477,532.67 of marks, 597 new items
   held).
2. A held run still saves the self-check facts (`selfcheck.after_detection`: `_save_facts` ran outside `if not held:`), so
   the next good run's vanished check finds no new facts for answers that came before the held run and keeps settled items
   (the replay's September: 67 items, $3,871.34).

## State

- [ ] tests/test_replay_selfcheck.py: failing tests first (both bugs, file and compact stores, the store image)
- [ ] fix 1 in check_posted; fix 2 in after_detection
- [ ] break each fix on purpose, see the tests fail, restore exactly
- [ ] real-data reproduction before and after (read only)
- [ ] docs/TROUBLESHOOTING.md (posted_money)
- [ ] selfcheck tests in four environments; full suite in python3 and 3.11.1
- [ ] push wip/replay-fix

## Next command

(filled in as the unit goes)

## Measured

(filled in as the unit goes)

## Open questions

(none yet)
