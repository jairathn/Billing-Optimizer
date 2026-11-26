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

- 22:50 UTC: the eight findings fixed (all 58 ported tests and 26 own edge tests pass on both store kinds; each fix broken on
  purpose and restored, `$S/tk/units/fx-ledger/breaks.py`, files byte-identical after):
  - hosts-4 (detect._build_ledger): ORDER BY on the PLB query; the entry naming a takeback is the largest, then the
    latest, then the greatest reference.
  - money-5 (_build_ledger, the deferred rule): a budget per (claim, payer) = the reversal's amount less the same
    remittance's re-payment of the bill by that payer, capped by what the remittance carried; later WO/J1 entries skip
    only up to it.
  - money-1 (_build_ledger, _count_not_on_file, insights.data_health): what a WO/FB/J1 naming a claim could not apply is
    `self.plb_unapplied`; counted in REVERSALS_NOT_ON_FILE_META (claims and cents with the reversals; entries,
    recouped_claims, recouped_cents, each with its older_ twin); the Data tab's "reversal_not_on_file" item names it.
    Design note: the meta is the same key (the reviewers' tests read it), widened, not a new key beside it.
  - money-6 (_taken_back_items returns {WO key: cents not applied}; run_aggregate_rules lists a WO of $500+ at that size,
    largest entry first; title "...: $X of it takes back no payment on file", evidence withheld_cents/not_applied_cents).
  - money-2 (_taken_back_items, _emit_taken_back): a status 2/3 payment taken back is judged against that payer's own
    standing payments of the service (paid_events, not paid_cover); another second payer answering since is "answered
    again"; the item says "paid its share as second payer, then took it back: bill the patient, or dispute the takeback",
    evidence second_payer: true.
  - money-8 (states.taken_back_since, used by paid_first_elsewhere and paid_proof's paid_before_same_payer): WO, FB, J1
    start a takeback; CS and 72 net with them; a 72 alone no longer keeps an item as work (the ledger's own rule).
  - money-9 (paid_first_elsewhere): a Medicaid denial needs the first payer's payments to reach the item's whole expected
    amount; between half and all of it, the item stays work when Medicaid's remaining share is open in EMA
    (fx['paid_first_partial'], named in the open basis); nothing open in EMA leaves it done as before.
  - answers-5 (paid_proof): a claim-level never-answered item (CLAIM_SIGNALS, no line) counts only payments after its
    send (evidence 'sent') and needs every service of the claim paid.

## Half done
- (nothing)

## Next
1. (done)
2. Residuals (next_fixes section 4 and 2): PLB/J1 in paid_proof; reversals under another payer id; voids; denials paid
   but routed to note/check_first.
3. Merge the integration tip; measure tip vs branch on a world copy (A17 classes before/after); hand-trace 20+.

## Measured
- (nothing yet)

## Needs from others
- (none yet)

## Open questions
- (none yet)
