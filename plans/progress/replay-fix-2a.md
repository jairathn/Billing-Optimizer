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
  Follow-ups from the replay (each with a test on file, compact and image, break-and-restore B8-B11): the item keeps its
  own line where the candidate moved to the first payer's later line; EMA's second-payer money is read at any posting
  date; the item's own EMA claim is read where the link map let go of the bill; a billed item whose service has no
  candidate any more (the first payer took its payment back) is still read (and nothing changes with no answer: control
  test). The test file has 57 tests.

## State

- [x] worktree, merges of gapfix and replay-fix (no conflict)
- [x] A1 fixed, tests green in python3 (780 related tests pass)
- [x] A2 fixed, tests green in python3 (745 related tests pass)
- [x] A3 fixed, tests green in python3 (runlog, diagnose, hooks, swing, selfcheck, results pass)
- [x] merged the newest origin/wip/gapfix (4f5d8b1) and origin/wip/replay-fix (995abe7) before A4 (no conflict)
- [x] A4 fixed, its test file green in python3
- [x] A1-A4 measured on the replay (read only) and the release effect on final_world3 (below)
- [x] A4 follow-ups from the replay's residuals (5fc3746), TROUBLESHOOTING line (cdc0743)
- [x] merged origin/wip/replay-fix 386f026 (no conflict); gapfix cdd3463 already in
- [ ] touched files' tests in four environments (private touched_envs.sh; one environment at a time)
- [ ] full suite in python3 and venv_sqlite340 (plans/tools/shard.sh, RW_RUNS in the private folder)
- [ ] merge the newest gapfix and replay-fix tips again, rerun the touched tests, final push, report

## Next command

ENVS=python3 then the other three: private touched_envs.sh; then plans/tools/shard.sh <tree> python3 full-py3 2 and
the same with venv_sqlite340; then fetch and merge origin/wip/gapfix and origin/wip/replay-fix again.

## Left for a later unit (found on the replay, not fixed here)

- Two billed items on one service (bill, date, code): 3 on the replay ($38.93), each a pair where the first payer paid
  two lines of the code (or paid, took back and paid the line again) and a person marked both items billed. The rule
  answers the item on its candidate line; the other stays stale, as before this unit (the open book counts it closed).
  A fix has to tell a second bill of the same share (close it with the first, no second recovery) from two lines of the
  service (split the second payer's money between them, never counted twice).
- Billed, no answer, past the second payer's limit: 3 on the replay ($75.19) are dropped by the rule's past-limit branch
  (replay-fix-2b's retired line names them to the vanished check). Whether a line a person billed in time should stay
  listed for its follow-up past that limit is a decision for the owner.

## Measured (read only; aggregates)

Scripts in the private folder (`meas/`): states_a1a2.py (states_of on the store, tree given), a3_cover.py and
a3_rules.py (record_decisions for run 10, write captured), a4_results.py (the verifier's results check, tree given),
detect_mem.py (one detection on an in-memory copy, nothing written to disk) and a4_replay_compare.py. "Before" is a
base tree (the integration tip with the same gapfix and replay-fix tips merged, no commit of this unit).

- A1, replay final store at 11-24: 37 unworked rate patterns listed. Before: all Work now (owner_decision). After: 16
  routine-rate reviews ($8,096.41) and 4 clinician rate gaps ($0 left) Expired, 17 reviews still Work now (a claim still
  in its window); the scoreboard counts $6,807.75 (reviews) and $9,515.05 (gaps) lost to deadlines; 20 bases say every
  claim is past its window.
- A2, replay final store: 31 Started items, none set by a person. Before: 31 Work now person_started, basis "Started by".
  After: 11 change state ($1,328.52: 4 Expired, 4 Waiting on EMA's follow-up, 3 Done), 20 keep their state; all 31
  bases name the hand-off.
- A3, replay run 10 (state_9 before, final after): 101 items set stale ($6,016.30). Before: 39 logged with the stale
  mark, 62 missing ($3,066.85). After: 101 logged (94 detect:no_longer_listed, 7 detect:follow_up), 0 missing.
- A4, replay final store, the verdict's 184 items (stale, Sent, Work now, $5,959.00): open book before 184 still open
  (Work now), 0 closed, 17 'worked, waiting, no money'; after (results only, same rows) 0 still open, 184 closed (154
  paid, 30 no longer listed), 0 waiting with no money. One detection run of this tree on an in-memory copy of the
  replay's final store (replay clock 11-25): of the 184, 163 closed as Paid by the second payer ($5,397.76 billed,
  $4,853.09 recovered), 19 closed as paid by the patient ($476.24), 2 listed with 'work its answer' ($85.00), 0 left
  stale. Across all 474 second-payer items a person billed in that store (259 stale before): 220 closed Paid by the
  second payer, 33 by the patient, 148 follow-up due, 45 waiting, 22 work its answer, 6 still stale (3 follow-up due
  with no answer, the rule's own drop; 3 'mark it Paid' where two billed items share one service: next).
  The run's decision log: 253 'closed' rows (paid_later:paid_as_second_payer, settled_by_the_patient).
  Residuals found on the replay and fixed with a test each (break-and-restore B8-B11): the candidate moved to the
  first payer's later line of the service; EMA's posting dated before the first payer's payment; the link map let
  go of the bill (the item's own EMA claim is read); the first payer took its payment back and no candidate is left.
- Release effect, the 11-24 live-store copy (final_world3) at 11-28: A1 one routine-rate review ($42.50) Expires, $8.50
  counted lost; A2 none (no Started item); A3 none at release (no person's action after the last run); A4 none at
  release (no second-payer item a person billed: every one of the 492 is To do), and no stale row in an open state.
