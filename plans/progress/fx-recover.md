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

## Done (findings), commit "fx-recover: money-3, 4, 7, 10, 11, privacy-3, privacy-4"
- money-7 (detect.py): every standing payment carries its part (`_event_of`, the 11th field: component_of); `paid_cover`,
  `with_second_shares`, `units_paid`, `units_billed` take `part=` (default None keeps the old behaviour, F1's calls);
  `later_payment` passes the line's part and puts it on `wants`. archive_rules `Archive.answers_of` keeps the answers of
  the item's part or the whole service (`_answers_part`; in no fixer's row, a small edit): rule 3 (units_split) closed
  the 26 denial on the corrected claim's TC payment. Tests: 3 ported + control + second payer's share by part.
- money-4 (detect.py `_recover`, `_gained`, `_events_of`): the recovery is the gain of the service since the item's
  answer: every payer's money now less what it held at the answer (a second payer's share of that answer, paid after it
  and before the later payment, counted as held), over the service and the item's own line's code (a downcode), less
  what other items took from the payments made since; capped at the item's shortfall (`cap=`: `_emit`, resolve_elsewhere,
  `_close_by_answer`). Past the cap, the payments the item did not need stay free (greedy, the item's own payer first).
  `_mark_paid` caps its other branches at the shortfall too. Changed an existing test: T-C5's
  test_a_downcode_paid_again_in_full_records_what_it_brought_beyond_the_first_payment now expects the $41.45 shortfall
  (was $53.85): the brief's cap.
- money-3 (detect.py `_close_by_answer`, `_credit_lines`): the closure by the payer's own answer credits through the
  per-line ledger (used_units), never used_payments; a line another item counted is $0 here.
- privacy-3 (detect.py `_close_by_answer`, `_answer_rows`; selfcheck.py `answer_reconciles`, `has_proof`,
  NO_MONEY_KINDS/ANSWER_KINDS): proof rows (each settling line: date, claim, trace, code, modifiers, charge, allowed,
  paid, standing, adjustments by group and reason, the claim's own adjustments) in paid_later.proof with
  paid_later.reconciled; a $0 closure whose answer does not add up closes nothing (evidence answer_not_reconciled, hint,
  ema_counts settled_not_reconciled); has_proof accepts settled_by only with dated rows on a claim that reconcile.
  Changed an existing test: test_selfcheck.py's test_a_closure_by_the_payers_own_settling_answer_is_proof (rows now
  required; two more cases).
- money-10 (detect.py `_co45_not_denials`): a claim's interest (AMT*I) and its negative claim-level adjustments are not
  a claim-level payment of a line.
- privacy-4 (detect.py `_co45_not_denials`, `_settle_co45_candidates`): a whole-charge CO-45 on a code the payer never
  allowed or paid is listed (DENIED_OTHER, evidence payer_has_no_rate, sized by line_expected's no-rate sizing), so
  decision 4's explain_rarely_paid and the owner's policy page see it; the code is read without the modifiers a payer
  writes into it ('17263 76'). Changed an existing test: test_whole_charge_co45.py's no-rate test (fix5's rule reversed).
- money-11 (detect.py `_answer_copies` + one skip line in run_line_rules; archive_rules.py `fold_repeat_denials`,
  `_answer_sig`): a $0 answer delivered again the same day is a copy (claim id, payer, day, status, code, date,
  modifiers, charge, reasons); the line rules pass it by; rule 7 folds a same-day second delivery (another payer id of
  the same payer) into the first, a worked one kept.
- Every fix broken on purpose and restored ($U/scripts/breaks.py: 15 breaks, each fails its tests; git diff unchanged).
- Related suites (the takeback, settled, archive-rule, CO-45, decisions, review3, selfcheck, results, states, story,
  rates suites): 1,435 passed after the test changes above.

## In progress
- The residuals (next section's item 2).

## Left, in order
1. The residuals: J3490, $0 lines on Work now, the status-1 supplement, the second payer's share above Medicaid's rate,
   the pattern items' line lists and Results credit.
2. F1's routed needs, after `git log origin/claude/ecstatic-fermi-orfcgo --grep 'Merge wip/fx-ledger'` shows the merge.
3. Measure on a private world copy (tip against branch), A17 classes, hand-trace 20+ moved items. Also measure the
   claim-level-paid and rebilled-paid CO-45 drops (privacy-4's "money counted nowhere" check) and how many settling
   answers fail to reconcile on real data (privacy-3).
4. Suites, progress file, merge the tip again, final push and report.

## Needs from others
(none yet)

## How to run
- Tests: `cd $S/tk/wt/fx-recover && python3 -m pytest -q -p no:cacheprovider tests/test_fx_recover.py` (unset SRFAX_,
  DROPBOX_, TRIZETTO_, SMTP_, MODMED_, ANTHROPIC_BASE_URL first; REMITWATCH_ALLOW_OPEN=1).
