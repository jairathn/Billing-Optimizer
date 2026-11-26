# fx-ledger: F1 ledger and takebacks (progress)

Brief: `plans/briefs/2026-11-26_fixwave.md`, section "F1 ledger and takebacks" (and its top part). Branch `wip/fx-ledger`,
started from the integration tip 993f440 (fix7d merged). Rules: `plans/briefs/2026-11-26_common.md`. Worktree on the
session's machine: `$S/tk/wt/fx-ledger`; private folder `$S/tk/units/fx-ledger/` (mode 700; `runtests.sh <python> <args>`
runs pytest in the worktree with the feed variables unset).

Owned (the brief's table): detect.py `_build_ledger` (its take-back entry builder, the deferred rule, reversal pairing),
`_count_not_on_file`, `_taken_back_items`, `_emit_taken_back`, the WO/recoupment listing inside `run_aggregate_rules`;
states.py `paid_proof` and its helpers, `paid_first_elsewhere` (and the branches of `_decide` that decide a paid item is done).

## Done
- 22:20 UTC: ported the reviewers' and verifiers' tests into `tests/test_fx_ledger.py` (58 tests, both store kinds).
  On the tip (993f440): 36 fail (every finding: money-1, 2, 5, 6, 8, 9, hosts-4, answers-5's paid_proof part), 22 pass
  (each a control meant to pass: money-1's T-I5 precedent and its $600 listing, money-2's double-payment control, money-5's
  no-carried-balance runs and its own-purpose control, money-6 with only a denial on file, money-8's WO case).
  Sources: `$S/tk/review/since-money-tests/`, `since-money-verify-tests/` (v1, v2, v5, v5b, v6),
  `since-hosts-tests/test_hosts_takeback_order.py`, `since-answers-tests/test_answers_review.py` (answers-5's exports item).

## Half done
- (nothing yet)

## Next
1. Fix each finding in the owned functions; break each fix and restore it.
2. Residuals (next_fixes section 4 and 2): PLB/J1 in paid_proof; reversals under another payer id; voids; denials paid
   but routed to note/check_first.
3. Merge the integration tip; measure tip vs branch on a world copy (A17 classes before/after); hand-trace 20+.

## Measured
- (nothing yet)

## Needs from others
- (none yet)

## Open questions
- (none yet)
