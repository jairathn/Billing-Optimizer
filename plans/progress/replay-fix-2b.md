# Unit replay-fix-2b: what leaves the list and why (progress)

Brief: the workflow's task text (wf_a3fa6266-96f), with plans/briefs/2026-11-26_common.md, CLAUDE.md and
plans/HANDOFF.md. The verdicts behind each bug are private: `$S/tk/replay/verify2_result.json`, list "verdicts" (B1 = 0,
B2 = 2, B3 = 7, B4 = 10, B5 = 8). The proposed failing tests (invented data) are in `$S/tk/replay/proposed_tests/`.

Base: origin/claude/ecstatic-fermi-orfcgo (ed245a1) with origin/wip/gapfix (89acb3c) and origin/wip/replay-fix (0cd46c8)
merged in; their newest tips merged again after B5 (gapfix 4f5d8b1, replay-fix 995abe7), during the suites (gapfix
cdd3463, replay-fix 386f026) and at the end (the integration tip fa0aafa: gapfix and routes merged there, replay-fix
eef678f), each clean. Worktree `$S/tk/wt/replay-fix-2b`, branch `wip/replay-fix-2b`, private folder
`$S/tk/units/replay-fix-2b`.

Files this unit owns: remitwatch/selfcheck.py (check_vanished, _no_route, _sent_again, _facts_of, later_payments,
after_detection's ctx), remitwatch/detect.py (the retired hand-off, settled(), _after_save_checks),
remitwatch/ema_ledger.py (_map or the narrow read), and the tests added. The sibling unit replay-fix-2a edits
selfcheck._automatic_open, check_past_window, states.py, results.py and run_secondary_not_billed's person-billed lines.

## State

| Bug | What | Test | State |
|-----|------|------|-------|
| B1 | vanished check: routes for a payment EMA posted and a claim sent again under the same claim id | tests/test_vanished_posted_or_sent_again.py (21: 7 cases x 3 store kinds) | done: failed 9/9 on the base; passes; each of the 4 parts broken fails its cases; restored (sha1) |
| B2 | detection hands the checks the ids it retires on purpose (left_record) | tests/test_left_record_retired_on_purpose.py (21) | done: failed 9/15 on the base; passes; 5 breaks each fail; restored (sha1) |
| B3 | precision_guard: own payer only, PLB takebacks netted, own answer repeated skipped | tests/test_precision_guard_counts_only_standing_money.py (42) | done: failed 12/21 of the proposed cases on the base; passes; 7 breaks each fail; restored (sha1) |
| B4 | a let-go line settles a never-listed whole-claim denial | tests/test_let_go_line_not_back_as_claim_denial.py (10) | done: failed 2/4 of the proposed cases on the base; passes; 3 breaks each fail; restored (sha1) |
| B5 | a same-visit vote split keeps its EMA link | tests/test_link_map_same_visit_bills.py (14: 7 cases x 2 store kinds), test_ema_ledger.py map_world cases 3 and 9 | done: failed on the base; passes; 6 breaks each fail their cases; restored (sha1) |

## Fix round 1 (the review of 8a1cdfc, 2026-11-28)

The review's nine findings, each checked with its failing test (copies of the review's tests in
`$S/tk/units/replay-fix-2b-r1/tests`, invented data), on the tip 8a1cdfc and on the base fa0aafa:

| Finding | Verified | Fix | Test (both store kinds; image too where the file has it) |
|---------|----------|-----|------|
| r1-b3-ma-plb (high) | yes: the MA and MC cases fail on the tip and the base; replay final store, read only: 187 positive WO/FB/J1/L6 entries ($37,280.09) naming a paid claim matched by company but not by product family (the review: 192, $38,663.65) | `_plb_taken` compares the PLB entry's payer and the payment's by company (detect.payer_company, LEDGER_COMPANIES) | test_precision_guard_counts_only_standing_money.py: the plan's takeback is netted (16, MC, CI); control: another company's PLB takes nothing |
| r1-b3-repeat-order (medium) | yes: reversal_first fails on the tip, passes on fa0aafa (introduced by B3) | `later_payments` counts the repeats over the filtered rows first, then skips every repeat and as many reversals of the item's own amount, whatever the line order | same file: both orders with a real top-up; control: the same-amount reprocessing alone is no money |
| r1-b3-double-take (medium) | yes: fails on the tip, passes on fa0aafa (introduced by B3) | `_plb_taken` applies the status 22 reversals to what is left of each payment first (same claim id, company, date of service, code, dated before the reversal), so a PLB entry takes only what the reversal left | same file; control: a PLB after a reversal still takes the payment the reversal left |
| r1-b1-resend-before-first-seen (medium) | yes: read-late and evening cases fail on the tip and the base | `_sent_again`: a send of the same claim id counts when it is after the send the item is about (evidence `sent`), whenever it was read; for an item that names no send, on or after the practice's day it was first seen (runtime.practice_date) | test_vanished_posted_or_sent_again.py: the review's cases (NO_ANSWER and REJECTED_PRE_PAYER) and an item naming no send |
| r1-b2-snb-past-limit-silent (medium) | yes: on the tip the share's $30.00 was on no page (left_record ok, Results' lost 0) | `selfcheck.panel` (the Data tab's "Running on its own", from meta ema:wiring, so the dashboard copy too) names the second payers' shares past their limit with their dollars every run (`second_payer_past_limit`); the route stays | test_left_record_retired_on_purpose.py: the share raised past its last day and retired is on the Data tab |
| r1-b2-unseen-breadth (low) | yes (code reading: every reason set `retired`) | `Detector._count_unseen` retires only for UNSEEN_RETIRES (None, no_ema_reports); payer_remittances_stop is counted unseen but not retired | same file: the reasons that retire (file, compact) |
| r1-facts-version (low) | yes: the first run after the bump compared no fingerprints | FACTS_VERSION back to 3; FACTS_PAYMENT_VERSION 1 (meta selfcheck:facts_payment_version) versions the payment part alone; `_stored_facts(conn, before)` leaves out only the payment items' fingerprints while it differs | test_vanished_posted_or_sent_again.py: the first run after the payment part came still compares the others |
| r1-b5-order (low) | yes: the reverse order fails on the tip (link to the processed bill) | `_map`: each vote says whether its 835 claim was the payer's denial (status 4); a visit's link goes to that bill unless another link holds it (`pb_held`), else to its first vote's bill; MAP_RULE 2 -> 3 | test_link_map_same_visit_bills.py: the review's order; control: a denied bill another link holds is not taken |
| r1-b5-results-lost (medium) | yes: fails on the tip, the base and replay-fix-2a's tips 8ed9355 and 1873731 | not here: results.py is replay-fix-2a's. Patch and test ready for the integrator, checked on replay-fix-2a's 1873731 (results.py the same at 3ad88c7): `$U/r1/results_lost_done.patch`, `$U/r1/test_results_expired_then_done_is_not_lost.py` (see Open questions) | (routed) |

Each fix was broken on purpose and its cases failed; each file restored exactly (sha1 in `$U/r1/*.sha`).

Measured, read only (replay final store, the previous tip 8a1cdfc against this one, at its last day 2026-11-24):
- B3: the guard names 1 item ($127.80, the duplicate denial the review traced, by design) before and after; the
  verified seven cover nothing either way. 187 takebacks ($37,280.09) can now be netted against their payment.
- B1: no kept item routes differently at the store's last day (the read-late case needs an 837's read time, which the
  replay store does not keep; the tests carry it).
- B5 order: the conflicts remapped by rule 2 and by rule 3, on the replay final store and on the world store: no link
  changes. Without the held-bill guard one link would move onto a bill another patient's EMA claim holds.
- The world store (final_world3), one analysis run at its day on in-memory copies, the reviewed tip 8a1cdfc against this
  one: the same items, states, self-checks and decision log; detection's retired map holds 19 claims fewer (counted
  unseen because their payer's remittances stopped; none has a row, so nothing moves). See Release effect.

## Done, by bug

As first built (8a1cdfc). Fix round 1 (above) changed B1's day of a send again, B2's unseen reasons that retire, B3's
payer match (by company) and its order of lines, and B5's choice of bill (the denied one).

- B1 (selfcheck.py): `_no_route` has a route `posted_in_ema` (`_posted_in_ema`: EMA's checks of the item's trace,
  trace_key plus pair_trace, hold the transaction's claim_paid_total within max($1, 0.5%), ema_checks' DIFF_MIN and
  DIFF_SHARE); `_facts_of` adds EMA's checks of the payment (`_ema_checks_of`: count, cents, first and newest posting)
  for an item about a payment itself (`_about_payment`: a transaction and no line or claim, REMIT_NOT_POSTED and
  TAKEBACK; CLAIM_DENIED_UNPAID is left out on purpose, a check paying many claims is no new fact on one denial);
  FACTS_VERSION 3 -> 4; `_sent_again` also counts the same claim id sent again: claims_837.bht_created or
  ema_rows.claim_submission_date on or after the first_seen day and after the evidence's `sent` (the send the item is
  about is never a new one).

- B2 (detect.py, selfcheck.py, runlog.py): `Detector.retired` {finding id: counter}: run_secondary_not_billed's
  past-limit branch (snb_past_limit), `_count_unseen` (every claim counted unseen: the 837 pass's no-ERA branch, its
  waits-unfound branch and the exports' waits-unfound branch; the item id from cd's id_signal, OPEN_837_ID for the 837
  pass), and `_finish_unseen`'s second-send-nothing-left drop. `_after_save_checks` hands `retired` in ctx;
  `after_detection` passes it to `check_vanished(retired=)`, which routes `retired:<counter>` beside delivered_again for
  listed and unlisted items, only when detection deleted the row (a person's worked item keeps its stale row and stays
  kept). `runlog.record_decisions(retired=)` names `detect:<counter>` on the item's 'gone' row (runlog.py is outside the
  owned list: one parameter and the gone branch; replay-fix-2a edits other lines of record_decisions). Figures: a share
  that expired while Work now stays in Results' lost to deadlines after its retirement (registry); the no-ERA plan's
  claims are on the Data tab's unseen figure. A share raised already past its last day was never Work now, so Results
  does not count it as lost; its dollars are only in meta ema:wiring snb_past_limit_cents (see Open questions).

- B3 (selfcheck.py): `later_payments` (used by check_precision_guard and the weekly self-audit's `_precision`):
  downcoding and underpayment items count only their own payer group's later money (835 rows by `ema_checks._same_payer`
  on `_group_kind(payer, cfi)`, EMA postings by `_same_payer_named`); a later payment is counted net of `_plb_taken`
  (PLB WO, FB, J1, L6 with a positive amount whose reference is the claim id, holds exactly one claim id of these bills,
  or holds the payer's claim number of exactly one claim; same payer group; dated on or after the payment; newest
  payment first, each at most to nothing); a later answer repeating the item's own (same claim id, code and paid amount,
  not a reversal) is skipped, and so is one reversal of each such repeat. Detection's ledger_taken was not reused: the
  guard's tests build rows by hand (no detection run), so one direct netting serves both.

- B4 (detect.py): `settled()` takes `listed`; a line whose finding a person dismissed or marked lost (`let_go_lines`:
  this run's line findings in `closed_lines`) settles the code ("denied again as a line", "paid under another code")
  when this claim's own item did not list that code: `listed = prev is not None and (prev["codes"] is None or code in
  prev["codes"])`, `prev_claims[ck]["codes"]` read from the item's evidence (codes and unpaid). This is a code-level
  reading of the brief's "never listed (no prev_claims entry)": with the literal rule, a claim item listed for other
  codes (a 17000 the processed claim left out) took the let-go 99214 back as work the next run, overriding the person's
  decision; the code-level rule keeps the review5 exclusion exactly where the claim's item listed the same code (a test
  plants that older-release state). Codes nobody decided stay listed.

- B5 (ema_ledger.py): `_map` keeps a link whose payment votes split with no bill at 2/3 when the split is one visit's:
  every vote chose its bill by the patient's name (`named`: the name-and-amount or the name narrowing) and every vote is
  for one date of service (any of the visit's codes). The link keeps the bill of its first vote (by EMA's posting day,
  then bill), with that bill's votes and the total, conflict 0 (`pb_visit`). A split between two patients (a vote that
  did not choose by name) or over two dates stays a conflict. No schema change; `MAP_RULE` "2" in meta ema:map_rule: a
  store mapped by another rule runs Part B once at the next refresh (no new report needed; links kept are not re-voted,
  only conflicts and links with no bill). map_world case 3 now says it is two patients' bills; case 9 is one visit
  billed twice. The verdict's fix sketch said "same date and code"; the replay's 41 payment conflicts are all one date,
  15 with one code and 24 with two to five codes, so the rule takes one date, any codes (39 resolve, the verdict's
  count); a one-code rule would have left 24 of the 39 same-visit splits as conflicts (measured below).

## Next command

None in this unit: the work is done and reported to the workflow. For the integrator: trial merges (`git merge-tree`) of
this tip with wip/replay-fix-2a (3ad88c7), wip/ui-words, wip/ui-signin, wip/ui-work, wip/ui-panel and wip/ui-pages are
clean; wip/ui-a conflicts in remitwatch/static/index.html, as the integration tip itself does (the routes card against
ui-a's shell; this unit changes nothing under static/). After merging replay-fix-2a, apply the routed Results fix
(r1-b5-results-lost, Open questions) and run tests/test_results.py with its test.

## Tests

Fix round 1 (code at e4a6df4; gapfix cdd3463 and replay-fix eef678f, both already merged, did not move):
- The touched files' tests (26 files: the five of this unit, test_selfcheck, test_replay_selfcheck,
  test_merge_vanished_policy, test_ema_ledger, test_runlog, test_docs, test_gap_unseen, test_gap_one_claim,
  test_ema_wiring, test_837_sends, test_fx_exports, test_fx_ema, test_fx_837, test_fx_hosts, test_hooks,
  test_hook_review, test_insights, test_routes, test_workable, test_archive, test_upload_chunks), 1,585 tests: python3
  1,584 passed, 1 skipped; venv2_compat (newest libraries) 1,584 passed, 1 skipped; venv2_compat312 (Python 3.12.3,
  SQLite 3.45.1) 1,584 passed, 1 skipped; venv_sqlite340 1,406 passed, 96 skipped, 60 failed and 23 errors, all 83 the
  serialize limit (test_hook_review 34, test_hooks 30, test_fx_hosts 8, test_runlog 4, test_archive 4, test_ema_ledger
  3; none in this unit's files, whose image cases skip there).
- The full suite in python3: 4,818 passed, 29 skipped, 1 xfailed, none failed. In venv_sqlite340: 4,533 passed, 97
  skipped, 1 xfailed; 169 failed and 48 errors, all 217 tracebacks the missing Connection.serialize or deserialize (as
  many as the last round's run).
- Each run one environment at a time, pytest's temp folders in a private folder deleted after it.

Before fix round 1:
- python3 (3.13.18, SQLite 3.53.1), the full suite on the merge of gapfix 4f5d8b1 and replay-fix 995abe7: 4,432 passed,
  29 skipped, 1 xfailed; 3 failed and 162 errors, every one "No space left on device" or "disk is full" (the shared disk
  filled during the run); the 3 files they were in (test_fx_ledger, test_fx_hosts, test_hooks) run again once space was
  back: 320 passed.
- venv_sqlite340 (Python 3.11.1, SQLite 3.40.0), the full suite on 85d135f (gapfix cdd3463 and replay-fix 386f026
  merged): 4,381 passed, 81 skipped, 1 xfailed; 169 failed and 48 errors, all 217 tracebacks that build's missing
  Connection.serialize or deserialize (the store image; CLAUDE.md allows it there), as many as the gapfix and
  replay-fix runs report on their tips. None is in this unit's five test files (their image cases skip there).
- After the last merge (205b0bf: the integration tip fa0aafa), the touched files' tests (17 files: the five added,
  test_ema_ledger, test_selfcheck, test_replay_selfcheck, test_runlog, test_docs, test_gap_unseen, test_gap_one_claim,
  test_ema_wiring, test_837_sends, test_fx_exports, test_fx_ema, test_archive) and the merge's own (test_routes,
  test_routes_review, test_hooks, test_m1_exports), 1,221 tests: python3 1,220 passed, 1 skipped; venv2_compat (newest
  libraries) 1,220 passed, 1 skipped; venv2_compat312 (Python 3.12.3, SQLite 3.45.1) 1,220 passed, 1 skipped;
  venv_sqlite340 1,100 passed, 80 skipped, 36 failed and 5 errors, all 41 the serialize limit (test_hooks 30,
  test_archive 4, test_runlog 4, test_ema_ledger 3; each among the full suite's 217).
- Node checks on the merged tree: calc_model_check 79 of 79, lab_review_check 143 of 143 (static/ changed by the routes
  merge; this unit changes nothing there).
- Each run with pytest's temp folders in a private folder deleted after it (`$U/suite.sh`), one environment at a time.

## Measurements (replay data, read only)

- B1, the replay's kept items (snap.sqlite events kept_by_selfcheck, the first keep of each; final.sqlite read only;
  private script b1_keeptime.py), each judged in the month it was kept: 211 items, $12,014.37. The new routes let 30
  ($2,764.46) leave that month: 22 claims sent again under the same claim id ($1,993.64: 17 NO_ANSWER, 5
  REJECTED_PRE_PAYER; each has an 837 of its own claim id created that month, on or after the day the item was first
  seen and after the send it is about: the verifier's 22, to the cent) and 8 REMIT_NOT_POSTED payments EMA had posted in
  full by then ($770.82; the verifier confirmed 6 at $605.75 by exact amount, the route takes ema_checks' tolerance).
  The other 181 ($9,249.91) stay kept (the refuted classes: second-payer work a person did, rates re-learned, the held
  month). At the store's last day (b13_measure.py, old and new _no_route side by side) no item routed by the old code is
  kept by the new one.
- B2, the replay's snapshots (private script b2_measure.py; the past-limit cut-off read as appeal_deadline + 30 days
  before the run's day): second-payer items that left the record unlisted, past the cut-off, nobody's work: 56
  ($1,880.30; the verifier's left_record group: 54 offender-months, $1,859.26): detection's past-limit branch now names
  each in Detector.retired, so left_record routes them retired:snb_past_limit. Listed ones the vanished check kept after
  the cut-off: 14 nobody worked ($414.80) leave with the same route; 40 a person worked ($1,537.23) stay kept (a
  person's work stays on the list). The 10 NO_ANSWER claims moved to the unseen count ($2,122.25) are counted by
  _count_unseen, which now records each id (retired:unseen); the 1 same-id resend ($573.30) takes B1's route. Left: the
  3 untraced ($267.49). Detection was not re-run on the replay's month states (a run writes; copying a state file needs
  more than 3 GB free and the disk had 2.5 to 3.6 GB): the counts come from the snapshots and the routes from the tests.
- B3, final.sqlite read only (b13_measure.py, the replay-fix tip's selfcheck against this one): the guard at the last
  day names 3 offenders ($241.64: DENIED_AUTH, DENIED_DUPLICATE_UNPAID, UNDERPAID_VS_OWN_RATE) before and 1 after
  ($127.80, DENIED_DUPLICATE_UNPAID: another payer's later 835 covers a duplicate denial a person worked and the payer
  answered, state rule work_answered; not one of the verified seven). The verified seven: later money before $25.00,
  $51.48, $7.35, $58.34, $180.30, $99.39 and $99.39, each covering its shortfall; after $0.00 each (2 downcodes and 1
  MPPR item paid only by a second payer, 1 bundling and 1 auth denial whose reprocessing PLB WO took back, 2
  underpayments answered twice).
- B4, final.sqlite read only: the traced CLAIM_DENIED_UNPAID ($78.07, first seen in September) lists 99214 and G2211;
  the bill's only line item is the 99214's bundling denial ($63.89), which the simulated biller let go. By the new rule
  (not re-run on the month state; the B4 test plants the same case with invented data) the September run settles the
  99214 by that let-go line, which the claim's item never listed, and raises the whole-claim item for G2211 alone (an
  add-on code with no item of its own: only let-go codes are settled). On the final store the item already lists both
  codes, so the review5 exclusion keeps it as it is: the rule stops new duplicates, it does not take back an item
  already listed.
- B5, the replay's final store (final.sqlite, mode=ro, work database in memory, private script b5_measure.py): the map
  by the old rule equals the stored map (92,670 links, 42 conflicts: 41 payment, 1 ar). By the new rule 39 links change,
  each from a conflict to a bill (no link loses or changes a bill; patient_day unchanged); 3 conflicts remain (the ar
  one and two payment splits whose votes name another patient). Of the 39: every voted bill names the link's patient, no
  bill is held by another link: 0 links join two patients. Items of the bills involved: 13 (8 listed). The traced
  denial: expired (not_found) -> done, paid_before_other_payer, $80.33 paid before by the other payer ($50.33
  shortfall). Outcome only, state kept: 1 EM_BUNDLED done/auto_closed, 1 SNB done/second_payer_paid, 3 SNB
  work_now/follow_up_due. 2 SNB work_now/follow_up_due -> work_now/person_billed_paid (the rule replay-fix-2a's A4
  changes). The one-code variant changes 15 links and 2 items (the traced denial the same).

- One analysis run at the replay's last day on in-memory copies of final.sqlite (mem_run.py, before = the merged tips
  without this unit, after = this branch): the vanished check's offenders 175 ($10,839.36) -> 147 ($7,671.35), the 28
  routed by posted_in_ema 8, sent_again 3, retired:snb_past_limit 12, retired:unseen 2 and
  retired:open_claim_second_patient_paid 3 (the gapfix counter, through the same hand-off); left_record 37 ($6,554.10,
  NO_ANSWER moved to the unseen count) -> 0; the precision guard 1 ($93.91, the PLB-taken-back auth denial of the
  verified seven) -> 0; the decision log's 125 detect:no_longer_raised become 71 of those, 39 detect:unseen, 12
  detect:snb_past_limit and 3 detect:open_claim_second_patient_paid. Items: the traced B5 denial expired -> done; 2
  second-payer items a person billed take rule person_billed_paid (state Work now either way); the B4 item stays as it
  is (it already lists both codes). On this store the release's own acceptance of departures (_release_accept, the
  owner's 11-27 decision) keeps nothing back in either run, so no row moves on the list here.

## Release effect (final_world3, read only)

One analysis run at the world's day (2026-11-24) on in-memory copies of world.sqlite (sqlite backup into memory, the
store opened mode=ro; the EMA work database in memory; private scripts mem_run.py and mem_compare.py), once per tree:
before = the integration tip this unit merged (fa0aafa) without its changes, then the reviewed tip (8a1cdfc) and this
tip (fix round 1). Every difference is this unit's.
- B5: the refresh redoes the link map once (meta ema:map_rule is not "3"; Part B, 9 s): 39 EMA claims stop being payment
  conflicts (41 -> 2, plus 1 A/R conflict); no link joins two patients; rule 3 maps every link as rule 2 did on this
  store. One item moves: a CLAIM_DENIED_UNPAID, $50.33, from expired (lost to its deadline) to done, paid before the
  denial by the other payer ($80.33). One EM_BUNDLED item stays done (its EMA outcome reads contractual_later instead of
  not_found). The second-payer rule finds 24 more shares answered in EMA, so 109 shares ($4,105.99) are past their
  payer's limit instead of 130 ($4,551.93).
- B2: 41 items the 837 pass moves to the unseen count leave with route retired:unseen: 3 listed NO_ANSWER ($462.86) that
  the vanished check kept on Work now before ("nothing explains why") and 38 unlisted ($6,680.21) the left_record check
  failed on; left_record goes from fail (38, $6,680.21) to ok; the vanished check from fail (5, $583.96) to fail (2,
  $121.10: no rule retired those two, not this unit's). The decision log names them detect:unseen (41) instead of
  detect:no_longer_raised (38 rows, the 3 kept ones had none). Fix round 1: the Data tab's "Running on its own" names
  the 109 second payers' shares past their limit ($4,105.99) every run; 19 claims counted unseen because their payer's
  remittances stopped are no longer retired (none has a row: nothing moves).
- B3: the precision guard (critical) goes from fail (1, $40.72: an EM_BUNDLED_MOD57_MAJOR whose reprocessing the payer
  took back by PLB WO) to ok. Fix round 1 changes nothing more here (ok in both).
- B1 and B4: no item moves in this run (no payment EMA posted in full, no claim sent again under its claim id, no let-go
  line under a claim item that never listed it, between the world's last run and this one).
- Work now after the run: 4,450 items, $1,816,938.95 before; 4,447 items, $1,816,476.09 after (the 3 NO_ANSWER,
  $462.86). The reviewed tip and this tip give the same items, states, self-checks and decision log.
- Results: the world store keeps no Results registry (it is written only when the Results page is opened on a server).
  Where a registry recorded the B5 denial as lost, it stays counted lost until the routed Results fix is applied.
- Once, at the first run after the release: FACTS_VERSION stays 3 and only the payment part has a new version
  (selfcheck:facts_payment_version 1), so that run still compares every item's fingerprint but those of items about a
  payment (REMIT_NOT_POSTED, TAKEBACK): only such an item that leaves on new facts alone is kept once and waits for
  Accept on the Data tab. In the measured run nothing was read since the world's last run, so the facts route was
  unused in every tree.

## Open questions

- B2: a second payer's share raised already past its last day (never Work now) and then retired: fix round 1 names the
  shares past their limit, with their dollars, on the Data tab's "Running on its own" panel (selfcheck.panel, from meta
  ema:wiring). Left for results.py's owner: count them on Results, by week and payer group, as lost to deadlines or
  given up without a decision. Results is refreshed only when the Results page is opened on a server (api_results), never
  by either cycle: an item that expires and is retired between two openings is never recorded lost by the registry.
- Results keeps a loss it recorded: once an item went from Work now to expired, the registry's expired_on and lost_cents
  stay when the item later turns done (results.py `_lost` lists every row with expired_on). The B5 denial the release
  moves from expired to done ($50.33, paid before by the other payer) stays counted as lost to its deadline if Results
  recorded it so. results.py is not this unit's file (replay-fix-2a edits it). The routed fix, for the integrator after
  merging replay-fix-2a: `update_registry` reads the state's rule (findings.state_rule, added to FINDING_FIELDS) and,
  for a row with an expiry, keeps it as flag_info `done_rule` while the item is done (dropped when it is not);
  `_lost` leaves out a row whose last state is done by a rule that brought or proved money. A person's Done (nothing
  recovered), a stale row, a rule the store did not keep and a let-go stay lost: the loss came at the deadline; money
  taken back later counts the loss again. Patch `$U/r1/results_lost_done.patch` and test
  `$U/r1/test_results_expired_then_done_is_not_lost.py` (14 cases, invented data, both store kinds), checked on
  replay-fix-2a's 1873731 (results.py is the same at 3ad88c7): 6 cases fail there without the fix and all pass with
  it; four breaks each fail their cases; restored (sha1); test_results, the rate-review test and the six other files
  that use Results pass (711 tests). The first version of the patch (`_lost` left out every row whose last state is
  done) would also have hidden a person's Done after the deadline: superseded.
- B3: the replay's last guard offender ($127.80, DENIED_DUPLICATE_UNPAID, work_answered: another payer's later 835
  covers a duplicate denial a person worked) was not traced. The own-payer rule covers downcoding and underpayment items
  only, as the brief says; a denial still counts any payer's later money.
- B4: only codes a person let go settle. An add-on code with no item of its own (G2211 beside a let-go 99214) stays
  listed as the claim's denial, and an item that already lists a let-go code keeps it (the review5 exclusion): the rule
  stops new duplicates, it does not take back one already listed.
- The world's vanished check keeps 2 NO_ANSWER ($121.10) in both runs: no rule of this unit retires them.
- Facts versions (fix round 1): only items about a payment are left out of the facts comparison at the first run after
  the release (docs/TROUBLESHOOTING.md says so).
- B5 on Vercel: the map is redone (rule "3") at the first run that applies an EMA report or reads a new 835 (meta
  ema:map_dirty); the server's cycle refreshes every cycle, so it redoes the map at its next cycle.
- B5 order (fix round 1): a visit's other voted bill still reads no EMA claim (one bill per link). Letting every voted
  bill read the link needs every reader of ema_link_map by bill (detect._ema_lines_for, ema_checks.line_outcomes and
  others, results, the dashboard's copy, the archive's copies): a cross-module change left for the integrator. The
  review measured 12 listed items on such bills in the replay, none whose state would change today.
- r1-b2-unseen-breadth (fix round 1): a never-answered claim whose payer's remittances stop before its due day is no
  longer retired: a listed one is kept on Work now by the vanished check (red until accepted) and an expired one is
  counted by left_record, while the Data tab's payers-behind line and unseen figure show its dollars.
- Measurement limits: detection was not re-run on the replay's month states (copying a state file needs more than 3 GB
  free; the disk had 2.5 to 3.6 GB then). The month counts come from the replay's snapshots; the in-run figures come
  from one run at the replay's last day and one at the world's day, each on a copy held in memory (sqlite backup of the
  store opened mode=ro; nothing written to disk).
