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
- A3 a stale change after a person's action is missing from the decision log: DONE. runlog.record_decisions keeps a change
  that carries the stale mark; a 'state' row with it gets detect:no_longer_listed (detect:listed_again back). Test file
  tests/test_decision_log_keeps_stale_after_a_persons_status.py (12; the cycle case uses a chase marked Sent then
  answered, since the proposed SNB flow closes as Paid after A4). Break-and-restore done.
- A4 a second payer a person billed, once it answers, is never shown again or closed: DONE. detect.run_secondary_not_billed
  keeps the lines a person billed (_snb_billed_rows: SNB items Sent, or a person's own Started, and those the run closed
  after that; a handed-on Started is left to the rule as To do) and hands each to _snb_billed_answer once another payer
  answered (835 lines of the key from another payer; EMA's postings by another payer group since the first payer's
  payment, on the line's EMA claim or the patient's other EMA claim for the date and code) or the patient paid since:
  paid what it owes to $1 (the rule's own floor), closed as Paid (_mark_paid, proof rows: 835 answer lines or EMA
  postings, recovery = what arrived, at most the share; patient only: 'Paid by the patient', a patient_payment proof row
  dated by the line's newest posting, recovered 0); paid less or nothing, kept listed at what is left with evidence
  second_answer. What it owes: the billed share (evidence billed_share_cents, kept run to run), or decision 3's
  owed_in_second_role where its own 835 gives allowed amounts. states._billed_second reads second_answer first: 'work its
  answer' (rule work_answered). results._closure: a stale row in an open state is closed (paid with money, else gone);
  _outcomes reads stale and waiting_no_money leaves stale rows out. Docs: TROUBLESHOOTING (three entries) and
  ARCHITECTURE (stale rows). Test file tests/test_billed_second_payer_closes_or_stays_listed.py (42: the ported 10, and
  part paid, denied, patient, paid by EMA and 835 with the decision log row and a second run, person's Started, takeback
  reopens, person moved off the closure, decision 3 owed, handed-on Started, results unit, stale chase in the open book;
  file, compact and image). Break-and-restore done for 7 parts (private break_a4.py).

## State

- [x] worktree, merges of gapfix and replay-fix (no conflict)
- [x] A1 fixed, tests green in python3 (780 related tests pass)
- [x] A2 fixed, tests green in python3 (745 related tests pass)
- [x] A3 fixed, tests green in python3 (runlog, diagnose, hooks, swing, selfcheck, results pass)
- [x] merged the newest origin/wip/gapfix (4f5d8b1) and origin/wip/replay-fix (995abe7) before A4 (no conflict)
- [x] A4 fixed, its test file green in python3; related files running

## Next command

Measure A1-A4 on the replay's data, read only (verdict reproductions with this tree), and the release effect on final_world3 (read only); then the four environments for the touched files' tests and the full suite in python3 and venv_sqlite340; merge the newest gapfix and replay-fix tips again before the final push.

## Measured

Nothing yet.
