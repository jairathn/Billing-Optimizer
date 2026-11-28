# Unit replay-fix-2b: what leaves the list and why (progress)

Brief: the workflow's task text (wf_a3fa6266-96f), with plans/briefs/2026-11-26_common.md, CLAUDE.md and plans/HANDOFF.md.
The verdicts behind each bug are private: `$S/tk/replay/verify2_result.json`, list "verdicts" (B1 = 0, B2 = 2, B3 = 7,
B4 = 10, B5 = 8). The proposed failing tests (invented data) are in `$S/tk/replay/proposed_tests/`.

Base: origin/claude/ecstatic-fermi-orfcgo (ed245a1) with origin/wip/gapfix (89acb3c) and origin/wip/replay-fix (0cd46c8)
merged in. Worktree `$S/tk/wt/replay-fix-2b`, branch `wip/replay-fix-2b`, private folder `$S/tk/units/replay-fix-2b`.

Files this unit owns: remitwatch/selfcheck.py (check_vanished, _no_route, _sent_again, _facts_of, later_payments,
after_detection's ctx), remitwatch/detect.py (the retired hand-off, settled(), _after_save_checks),
remitwatch/ema_ledger.py (_map or the narrow read), and the tests added. The sibling unit replay-fix-2a edits
selfcheck._automatic_open, check_past_window, states.py, results.py and run_secondary_not_billed's person-billed lines.

## State

| Bug | What | Test | State |
|-----|------|------|-------|
| B1 | vanished check: routes for a payment EMA posted and a claim sent again under the same claim id | tests/test_vanished_posted_or_sent_again.py (21: 7 cases x 3 store kinds) | done: failed 9/9 on the base; passes; each of the 4 parts broken fails its cases; restored (sha1) |
| B2 | detection hands the checks the ids it retires on purpose (left_record) | | not started |
| B3 | precision_guard: own payer only, PLB takebacks netted, own answer repeated skipped | | not started |
| B4 | a let-go line settles a never-listed whole-claim denial | | not started |
| B5 | a same-visit vote split keeps its EMA link | | not started |

## Done, by bug

- B1 (selfcheck.py): `_no_route` has a route `posted_in_ema` (`_posted_in_ema`: EMA's checks of the item's trace,
  trace_key plus pair_trace, hold the transaction's claim_paid_total within max($1, 0.5%), ema_checks' DIFF_MIN and
  DIFF_SHARE); `_facts_of` adds EMA's checks of the payment (`_ema_checks_of`: count, cents, first and newest posting)
  for an item about a payment itself (`_about_payment`: a transaction and no line or claim, REMIT_NOT_POSTED and
  TAKEBACK; CLAIM_DENIED_UNPAID is left out on purpose, a check paying many claims is no new fact on one denial);
  FACTS_VERSION 3 -> 4; `_sent_again` also counts the same claim id sent again: claims_837.bht_created or
  ema_rows.claim_submission_date on or after the first_seen day and after the evidence's `sent` (the send the item is
  about is never a new one).

## Next command

B2: port `$S/tk/replay/proposed_tests/test_left_record_rule_retired.py`, see it fail, add Detector.retired.

## Measurements (replay data, read only)

None yet.

## Open questions

None yet.
