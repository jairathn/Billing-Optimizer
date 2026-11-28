# Unit replay-fix-2a: progress

Branch `wip/replay-fix-2a` (worktree `$S/tk/wt/replay-fix-2a`), from the integration tip ed245a1 with origin/wip/gapfix
(89acb3c) and origin/wip/replay-fix (0cd46c8) merged in. Private folder `$S/tk/units/replay-fix-2a` (mode 700). The
verdicts behind each bug are private: `$S/tk/replay/verify2_result.json`, list "verdicts" (A1 = [4], A2 = [6], A3 = [3],
A4 = [1]); the proposed tests are in `$S/tk/replay/proposed_tests/`.

Files this unit owns: remitwatch/states.py, remitwatch/runlog.py (record_decisions only), remitwatch/story.py (the
contract route and hand-off basis sentences only), remitwatch/results.py, remitwatch/detect.py
(run_secondary_not_billed's person-billed lines and the closure only), remitwatch/selfcheck.py (_automatic_open and
check_past_window only), tests/test_archive.py (two premises), and the tests added.

## The bugs, in order

- A1 routine-rate reviews and clinician rate gaps outlive their windows (states._decide CONTRACT branch): not started
- A2 an inherited Started decides the state over the rules: not started
- A3 a stale change after a person's action is missing from the decision log: not started
- A4 a second payer a person billed, once it answers, is never shown again or closed: not started

## State

- [x] worktree, merges of gapfix and replay-fix (no conflict)

## Next command

Port tests/test_contract_pattern_past_window.py (A1) and run it on the base.

## Measured

Nothing yet.
