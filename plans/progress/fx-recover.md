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

## Done (residuals), commit "fx-recover: pattern items list the claims still short; Results credits a review's claims"
- The routine-rate review's line list (detect.py `emit_second_rates`, `_still_short`, `_answered_since`,
  `_emit_pattern_settled`, `_emit_pattern(left=)`): a claim the payer took back or paid more since leaves the review (a
  new review and a worked one), named in evidence `left` ([claim id, date, why, money now, line key]); one paid more since
  is a one-off that its payment closes as recovered; a worked review whose every claim left is closed (its recovery what
  the payer's money on them rose by). Tests: 2 (both store kinds).
- The repricing request's line list (detect.py `_note_paid_more`; the routing of a sent request's claims and of a new
  request's claims): a claim the payer paid the difference on without taking its first payment back leaves the request
  (new or worked), named as repriced at its new amount, its one-off closed as recovered on its own line, as a claim taken
  back and paid again already was. Repriced and taken-back entries carry their line key. Test: 1 (new and worked).
- Results credits a review (results.py `_pattern_ever`, `_pattern_refs`, `_item_json(prev)`, `update_registry`'s previous
  item, `_pattern_claims`, `_pattern_facts`, `_fold_patterns`, `attribute`): the registry keeps every claim a review ever
  listed (claims that left it at its first registration too); each claim is worked out as an item of its own (its first
  flag day the review's, or the day the payer paid that claim when it joined later; only the review's payer's records), in
  the review's group, then folded into the review (each claim's takebacks offset its own payments only). A person's Paid
  mark on a review counts when no payment after each claim's own flag shows. Tests: 7 (both store kinds).
- Every fix broken on purpose and restored ($U/scripts/breaks.py, 10 more breaks; each fails its tests).
- Related suites (fx_recover, plan_rates, results, review_rates, sizes_and_rates, review3 patterns and views, review6
  pattern windows and holds, resolution, features, insights, desk, selfcheck, archive, claims): 805 passed, 1 xfailed.

## Done (residuals), commit "fx-recover: J3490 by its units; Medicaid owes a claim's share up to its own rate"
- J3490 (detect.py `_co45_not_denials`): an unlisted code (codes.is_unlisted: J3490 and the like) has a rate only where the
  payer allowed or paid it with the units the line billed (the units stand for the drug and dose). A 4-unit J3490 written
  off whole by a payer that once allowed a 1-unit J3490 is listed as a code it has no rate for (decision 4's research);
  with the same units before, as a denial of a drug it pays. Test: 1 (two cases, both store kinds).
- The second payer's share above Medicaid's rate (archive_rules.py `_claim_after_primary`, new `_medicaid_owes` and
  `_owes_words`; detect.py run_claim_rules writes the claim item's `rate_expected`: each service the claim lists at the
  payer's own rate): a Medicaid plan that denied a whole claim after another payer paid it owes the share the first payer
  left only up to its own rate less what the first payer paid, service by service; nothing owed is done (written off,
  never billed to a Medicaid member). A service Medicaid has no rate of its own for caps nothing. Small edits in
  functions no fixer owns (archive rule 2's claim branch, one evidence key in run_claim_rules). Tests: 3.

## Done (hand trace), commits d54fb7c, 5a6aa7f, 2a93acd
- d54fb7c: a second payer's share paid between a denial and the payer's new payment is part of what came back (the
  denial held nothing for it to share); a payment that only replaces one the payer held at the answer and took back is
  picked last. Tests: 2.
- 5a6aa7f and 2a93acd (detect.py `_gained`, `_recover`, new `_claim_share`): the hand trace of branch 2's moves found the
  held-share rule too wide. A second payer's payment after an item's answer was held as its share of that answer whenever
  the service held any money then (another unit's payment on the same claim, or another payer's), so a first payer's
  denied unit later paid by the second payer, a denial after another payer had paid, and a payer that denied as primary
  and paid as second payer all recorded $0. Now only the money the item's own line held at its answer is shared. And a
  line's money is what its claim paid of it: where a claim's own adjustments take back what its lines pay (a second
  payer's claim-level PI-22 or OA-23 naming the first payer's payment: the line pays as if first, the claim pays the
  difference), the paying lines share the take-back in proportion; a claim total that differs from its lines with no
  claim-level adjustment changes nothing (the test builders write a fixed CLP04). Tests: 3 functions (6 ids with the
  two offsets and both store kinds) plus a control.
- Every fix broken on purpose and restored ($U/scripts/breaks.py: 32 breaks, each fails its tests; git diff unchanged).
- Recovery suites (every test file that checks a recovery, 43 files): 1,497 passed.

## Measured (private world copy, cycle_run 2026-11-24, A17 W view)
Tip: 56b538e's code (the tip is now bf1f17b, plans only since). Branch 2: d54fb7c. Branch 3: 2a93acd. No merge of
another fixer's branch is in either measurement (F1's merge has not reached the tip).
- Branch 2 against the tip: 494 items moved. Recovered: 486 items, -$10,742.85 (454 capped at the shortfall, -$9,346.32;
  21 lower, -$2,058.10; 11 higher, +$661.57). Appeared: 5 DENIED_OTHER (+$1,813.23: 4 Medicare Supplement carriers, 3
  of them 99999 and one '17263 76 5', all expired, payer_has_no_rate; 1 Self-funded plan TPAs 11401, Work now, $116.60,
  payer_has_no_rate). Vanished: 2 DENIED_FREQUENCY UnitedHealthcare Medicare Advantage 77401 ($25.55, already done).
  Size: 1 SECOND_RATE Humana Medicare Advantage 11102 (-$17.28: one claim taken back left the review). On real data
  the pattern rules moved one review (a claim taken back); no claim was paid more since or repriced.
- A17 W, tip -> branch 2: CORRECT|closed 10,445 -> 9,996 (in scope 2,579 -> 2,511); WRONG_AMOUNT|closed 485 -> 936
  (in scope 126 -> 194); ALREADY_DONE|open 1,278 -> 1,279 (+1: the 11401 item, $116.60); NOT_A_UNIT -$17.28; every
  other class the same (ALREADY_WORKED 37, DUPLICATE 66, CORRECT|open 2,215, WRONG_AMOUNT|open 1,636, UNDECIDED 984,
  CLOSED_WRONGLY 5); money left on the table unchanged. Of the verdict changes, 441 CORRECT -> WRONG_AMOUNT are the
  brief's cap at the shortfall (A17 counts every payment after the answer, uncapped) and 10 are lower recoveries, all
  hand traced below (the reason for 5a6aa7f).
- Branch 3: BRANCH3_RESULTS

## Hand trace (de-identified views only; verdicts on the branch's value)
Branch 1 and 2 picks (26): the three 99999 and the '17263 76 5' supplement items listed as codes the payer has no
rate for: right (privacy-4, decision 4), the '17263 76 5' one unsized (residual). The 11401 TPA item on Work now:
right by decision 4 (A17 calls it already done). Four second-payer-share cases wrong on branch 1, fixed in d54fb7c; one
replacement-payment case wrong on both, fixed in d54fb7c. Two vanished 77401 frequency denials: wrong (a same-day
delivery reversed later was folded as a copy; residual, already-done items, no Work-now effect). One multi-unit service
whose other unit's second-payer takeback lowers this unit's recovery: wrong (residual). Six capped at the shortfall:
right by the brief (A17 disagrees: the cap question below). One Cigna claim-level second-payer settlement: unclear.
Others right (a review losing a claim taken back; recoveries picked by charge and modifiers).
Branch 2's ten lower recoveries A17 calls wrong (L0 to L9), each against raw rows:
- L0, Medicare KY OA-109 99213: another payer paid first, its second payer paid $45.00 after Medicare's denial. Branch
  2 $0: wrong (Medicare's denial held nothing). Branch 3: L0_RESULT.
- L1, Medicare KY 11305 CO-50 (one unit paid, this one denied); Medicare reopened and paid it $33.25; the supplement's
  $8.29 share of the other unit came between. Branch 2 $24.96: wrong. Branch 3: L1_RESULT.
- L2 and L6, UMWA 99213 denied as primary after another payer paid; UMWA then paid as second payer: its line says
  $42.88 (and $75.58) but its claim took $37.88 (and $72.50) back at the claim level (PI-22), so $5.00 (and $3.08)
  arrived. Tip $42.88 and $75.58: wrong (too high); branch 2 $0: wrong. Branch 3: L2_RESULT.
- L3, L4, L5, UMWA 17003, 17110, 99213 on one claim: UMWA's lines repeat the first payer's payments ($169.15 in all) and
  its claim takes $124.15 back (PI-22): $45.00 arrived. Tip credited $169.15 (wrong); branch 2 $0 on these three (17000's
  $23.12 kept). Branch 3 shares the $124.15 by what each line pays: L3_RESULT. The claim's total is right; each line's
  share is an estimate (by the first payer's own line payments it would be $0, $0, $21.88 and $23.12): unclear per line.
- L7, L8, L9, Anthem CODE_CHANGED 77436 (CO-97); Medicare as second payer paid the line (as 77600) $388.33. Branch 2
  $0: wrong. Branch 3: L7_RESULT (capped at the $357.48 shortfall by the brief).

## Not done, and why
- Items worth $0 on Work now: measured on the branch's real-copy run, no Work-now item has a $0 charge; the 154 with no
  amount have a charge (133) or an unknown one (21): unpriced, not $0 lines, so they stay. The $0 and token lines of
  never-answered export claims are the exports pass's (review answers item, run_ema_open_claims).
- The status-1 supplement: waits for F1's `_after_other_payer` (F1's merge has not reached the tip).
- F1's routed needs (part paid as global elsewhere, Medicaid share size, denial on a voided claim, one service on two
  items, the 129 taken-back second shares): wait for `Merge wip/fx-ledger` on the tip.

## Residuals for the integrator
- The cap at the shortfall (brief, money-4) against A17 and decision 3 ("the recovery is what actually arrived"): A17
  counts every payment after the answer, uncapped; 441 closed items move to WRONG_AMOUNT on the cap alone. One line
  decides it (`cap=` in `_emit`); the owner's call.
- Lines that pay more than their claim, the rest taken back at the claim level: 472 second-payer claims ($119,332.25)
  and 58 first-payer claims ($17,654.33) on the real copy. Recoveries now count what the claim paid; every other use of a
  line's payment (whether a service is paid, sizes, the ledger) still reads the line. No fixer owns line money as a
  whole: the integrator's to route (F1's ledger is the nearest).
- A multi-unit service where another unit's second-payer takeback lowers this unit's recovery (one item seen).
- A same-day delivery reversed later is folded as a copy (two 77401 items, $25.55, already done).
- The '17263 76 5' no-rate item is unsized (size by the base code's rate).
- A Cigna claim-level second-payer settlement (one item): unclear.

## Needs from others
(none: the items above have no owner among the fixers' functions; the integrator routes them)

## How to run
- Tests: `cd $S/tk/wt/fx-recover && python3 -m pytest -q -p no:cacheprovider tests/test_fx_recover.py` (unset SRFAX_,
  DROPBOX_, TRIZETTO_, SMTP_, MODMED_, ANTHROPIC_BASE_URL first; REMITWATCH_ALLOW_OPEN=1).
