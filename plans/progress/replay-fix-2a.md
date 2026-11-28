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

- A1 routine-rate reviews and clinician rate gaps outlive their windows: DONE (commit b7d1d44). states.WINDOWED_CONTRACT
  expire past_all in the CONTRACT branch; selfcheck._automatic_open/check_past_window guard owner_decision with an
  appeal_deadline; results registers PROVIDER_RATE_GAP (REGISTER_SIGNALS) and counts an expired review's or gap's lost
  dollars claim by claim (_windowed_lines, _lost_of: claims whose window closed on or after the first flag, each line once,
  flag_info lost_lines / lost_elsewhere, _lost skips lost_elsewhere); story._claim_windows_words on the contract route.
  tests/test_archive.py's two premises: a person marks the pattern Started. Test file
  tests/test_rate_reviews_expire_past_every_window.py (39). Break-and-restore done for each part.
- A2 an inherited Started decides the state over the rules: DONE (commit e9f595f). states.person_started(events) (the
  newest status line a person set is Started); otherwise judged as To do, label stays Started, basis ends with the
  hand-off sentence (states._handoff_of / _handoff_words, a 'handoff' evidence row); selfcheck.check_past_window looks at
  in_progress items with no person's Started and at person_started on them. Test file
  tests/test_handoff_started_is_judged_by_the_rules.py (16). Break-and-restore done.
- A3 a stale change after a person's action is missing from the decision log: not started
- A4 a second payer a person billed, once it answers, is never shown again or closed: not started

## State

- [x] worktree, merges of gapfix and replay-fix (no conflict)
- [x] A1 fixed, tests green in python3 (780 related tests pass)
- [x] A2 fixed, tests green in python3 (745 related tests pass)

## Next command

A3: runlog.record_decisions (fix written, test ported as tests/test_decision_log_keeps_stale_after_a_persons_status.py); break-and-restore, commit.

## Measured

Nothing yet.
