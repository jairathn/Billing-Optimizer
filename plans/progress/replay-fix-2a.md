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
- [x] touched files' tests (the 23 files of this unit and the rules they touch), four environments, at 09d247a:
      python3 1400 passed, 1 skipped; newest libraries (3.13, SQLite 3.53) 1400 passed, 1 skipped; 3.12/SQLite 3.45
      1400 passed, 1 skipped; 3.11.1/SQLite 3.40 1275 passed, 70 skipped, 41 failed or errors, every one the missing
      Connection.serialize (that build cannot make a store image; Vercel can). The one skip is test_docs' private
      words test: with REMITWATCH_PRIVATE_WORDS set, test_docs is 40 passed.
- [x] full suite, python3 (shard.sh, 3 shards): 4,666 passed, 29 skipped, 1 xfailed, 0 failed
- [x] full suite, 3.11.1/SQLite 3.40 (shard.sh, 3 shards): 4,392 passed, 71 skipped, 1 xfailed, 217 failed or errors,
      every traceback the missing serialize, the same 217 as the replay-fix unit's run on its tip
- [x] merged origin/wip/replay-fix eef678f (its progress file only); gapfix cdd3463 already in
- [x] final push and report

## Next command

The fix round (review r1) is being finished: see "Review round 1" below. When its State list is all ticked, nothing is
left for this unit; a later unit takes the items under "Left for a later unit".


## Review round 1 (fix round, 2026-11-28)

The review (reviewed tip 89e2802) found six things; its failing tests (invented data) are ported, one per bug file, each
seen failing on the base first (commit 86be34c: the merge of the integration tip fa0aafa, i.e. gapfix cdd3463, routes and
replay-fix eef678f, then the ported tests). Each finding was verified by its test failing on file, compact and image.

- r1-a2-heir-started-judged-by-rules (high), VERIFIED, FIXED (8b7fd4a). Detector.save decided the states before
  carry_to_heirs moved the old id's history and faxes to the heir, so a person's own Started carried to a line's new id
  was judged as To do for one run (expired, with a chase hand-off basis). Now Detector.save moves finding_events and
  faxes of the run's heir pairs to the heir just before self._set_states (eight lines; outside the unit's listed
  functions, in a region replay-fix-2b's diff does not touch). Tests in tests/test_handoff_started_is_judged_by_the_rules.py:
  the downcode heir (the review's), the same claim under its EMA claim number then its claim id, and a faxed appeal's
  heir judged on its newest fax. tests/test_hotfix2d.py's in-flight heir test: the heir is now judged on its in-flight
  fax (Waiting), as the item it was pressed on is after a run, so the second Appeal is held ('blocked') before the 409;
  the guarantee holds (nothing sent, the status change refused 409). Break and restore: events only fails the fax case;
  neither fails all three.
- r1-a4-namesake-closes-billed-item (high), VERIFIED, FIXED (8ed9355). _snb_second_postings finds the patient's other
  EMA claims by ema_lines.person (name and birth date), never name_key. Test with its same-patient control. Break: name_key
  fails the namesake cases, the controls pass.
- r1-a4-first-payer-repayment-read-as-second (high), VERIFIED, FIXED (8ed9355). run_secondary_not_billed's 'seconds' leave
  out the first payer's own group (ema_checks._same_payer). Break: without it the three cases fail.
- r1-a3-person-status-written-up-as-rule (medium) and r1-a4-work-its-answer-not-logged (low), VERIFIED, FIXED (88c72da).
  runlog.Snapshot keeps max_event (the newest finding_events id when it was taken); a person's change is a manual or
  unhidden line with a higher id (_person_touched; a hand-made snapshot falls back to the time rule). For such an item a
  change carrying the stale mark is logged as kind 'stale' with stale, work_state and follow_up_on only. Breaks: the time
  rule fails the work-its-answer cases; logging the whole diff fails the let_go and reopened cases.
- r1-a1-review-listed-again (medium), VERIFIED, FIXED (e339680). (a) update_registry counts a review's or rate gap's new
  claims lost when it expires again (each line once), each loss in its own week (flag_info lost_parts; _lost buckets
  each part), so a frozen week is never counted again later. (b) results.live_cents: at risk, a review or rate gap adds
  only its claims still inside their windows (evidence 'deadlines', JSON1); live_figures and list_entries use the same
  expression; the at-risk worth is that part's share. Breaks: each of the three parts fails its case.
- Docs: TROUBLESHOOTING (the four rules) and the at-risk basis on the scoreboard (621e43b).

Routed, not changed (outside this unit): detect._ema_lines_for's 'elsewhere' (other_paid) still finds the patient's other
EMA claims by name_key, so an unworked second-payer line can be dropped on a namesake's payment (14 namesake services on
the replay). For the owner (from the review): the ema_heirs hand-off (an EMA_DENIED item a person worked, handed to the
835 item of the same line) is judged as To do though it is the same denial.

Measured, read only (aggregates):
- r1-a3: record_decisions for the replay's run 10 (state_9 before, final after): before this round after_person 250,
  state 282, follow_up 56, stale 58; after: after_person 0 (no person's line was written during the run: all 351 came
  before its snapshot), state 520, follow_up 68, stale 58; the 101 items run 10 set stale ($6,016.30) all logged.
- r1-a4: of the replay's 474 billed second-payer items, 0 have a namesake's paid sibling claim and 0 a first-payer group
  payment under another name (the reviewer's script); the population holds 14 namesake services (name, date, code held
  by two people) and every EMA line has a person key (0 of 202,793 without one).
- r1-a1 at risk: replay final store at 11-24: 97 items, $9,005.32 before, $8,971.32 after (1 review, $34.00 of claims
  past their window out); release copy (final_world3) at 11-28: 120 items, $11,919.76 before, $11,715.86 after (1
  review, $203.90 out). Re-expiry: no review had expired before, so nothing at release.
- r1-a2: the replay made no heir of a worked item (every signal_changed is a via_bill hand-off); the release copy has no
  Started or Sent item and no hand-off history: no effect at release.

## Left for a later unit (found on the replay, not fixed here)

- Two billed items on one service (bill, date, code): 3 on the replay ($38.93), each a pair where the first payer paid
  two lines of the code (or paid, took back and paid the line again) and a person marked both items billed. The rule
  answers the item on its candidate line; the other stays stale, as before this unit (the open book counts it closed).
  A fix has to tell a second bill of the same share (close it with the first, no second recovery) from two lines of the
  service (split the second payer's money between them, never counted twice).
- Billed, no answer, past the second payer's limit: 3 on the replay ($75.19) are dropped by the rule's past-limit branch
  (replay-fix-2b's retired line names them to the vanished check). Whether a line a person billed in time should stay
  listed for its follow-up past that limit is a decision for the owner.
- (review r1, routed) detect._ema_lines_for's 'elsewhere' (other_paid) finds the patient's other EMA claims by name_key:
  an unworked second-payer line can be dropped on a namesake's payment. Its owner: whoever owns the rule's listing path
  (run_secondary_not_billed's unworked lines). The fix is the one made here for billed items: ema_lines.person.
- (review r1, for the owner) the ema_heirs hand-off (an EMA_DENIED item a person worked, handed to the 835 item of the
  same line) is judged as To do (A2), though it is the same denial. Carrying the person's lines with it, as a whole
  hand-off does, would make it theirs; the owner decides.
- (review r1, optional, not done) a sibling EMA claim's posting counts for a billed item from any payer other than the
  first payer's group; it could be held to the second payer billed (evidence second_payer). With the person key there is
  no namesake left; a third payer paying the same patient's same service is rare and settles the share anyway.

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
