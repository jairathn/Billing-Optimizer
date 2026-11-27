# fx-recover: fix wave F2, recoveries and closures (progress)

Brief: `plans/briefs/2026-11-26_fixwave.md`, section "F2 recoveries and closures" (and its top part: sources, the
ownership table, what every fixer delivers). Rules: `plans/briefs/2026-11-26_common.md`. Branch `wip/fx-recover`, worktree
`$S/tk/wt/fx-recover`, private folder `$S/tk/units/fx-recover/` (mode 700).
`S=/tmp/claude-0/-home-user-Billing-Optimizer/96e81489-2ad2-5c70-991d-e00db2e22749/scratchpad`

## Done
- Branch made from the integration tip 8c59195 and fast-forwarded to 56b538e (a handoff note).
- tests/test_fx_recover.py: the review's and its verifiers' tests ported (invented data, both store kinds). On the tip
  56b538e: 20 fail, 18 pass. Failing: money-3 (case A), money-4 (both cases), money-7 (same bill, 26 and whole),
  money-10 (with interest), money-11, privacy-4 (all four but the control). Passing on the tip: privacy-3's four ported
  tests (the hook made `selfcheck.has_proof` accept `settled_by`; the brief asks for more, proof rows that reconcile, see
  below), money-3 case B (decision 3's second-role rule keeps the Medicaid item listed, so its $20.00 is counted once),
  money-7's control on another bill (T-C1 fixed resolve_elsewhere) and the new same-part control.

## In progress
- Fixing the findings in the functions F2 owns (table in the brief).

## Left, in order
1. privacy-3 (proof rows that reconcile for `_close_by_answer`'s $0 closures; `has_proof` accepts settled_by only then),
   privacy-4, money-3, 4, 7, 10, 11: each with its failing test, the fix, the break and the restore.
2. The residuals: J3490, $0 lines on Work now, the status-1 supplement, the second payer's share above Medicaid's rate,
   the pattern items' line lists and Results credit.
3. F1's routed needs, after `git log origin/claude/ecstatic-fermi-orfcgo --grep 'Merge wip/fx-ledger'` shows the merge.
4. Measure on a private world copy (tip against branch), A17 classes, hand-trace 20+ moved items.
5. Suites, progress file, merge the tip again, final push and report.

## Needs from others
(none yet)

## How to run
- Tests: `cd $S/tk/wt/fx-recover && python3 -m pytest -q -p no:cacheprovider tests/test_fx_recover.py` (unset SRFAX_,
  DROPBOX_, TRIZETTO_, SMTP_, MODMED_, ANTHROPIC_BASE_URL first; REMITWATCH_ALLOW_OPEN=1).
