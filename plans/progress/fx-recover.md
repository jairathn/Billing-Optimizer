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

## Done (hand trace), commits d54fb7c, 5a6aa7f, 2a93acd, 4bea68e
- d54fb7c: a second payer's share paid between a denial and the payer's new payment is part of what came back (the
  denial held nothing for it to share); a payment that only replaces one the payer held at the answer and took back is
  picked last. Tests: 2.
- 5a6aa7f, 2a93acd (detect.py `_gained`, `_recover`, new `_claim_share`): the hand trace of branch 2's moves found the
  held-share rule too wide. A second payer's payment after an item's answer was held as its share of that answer whenever
  the service held any money then (another unit's payment on the same claim, or another payer's), so a first payer's
  denied unit later paid by the second payer, a denial after another payer had paid, and a payer that denied as primary
  and paid as second payer all recorded $0. Now only the money the item's own line held at its answer is shared. A line's
  money is what its claim paid of it: where a claim's own adjustments take back what its lines pay (a second payer's
  claim-level PI-22 or OA-23 naming the first payer's payment: the line pays as if first, the claim pays the
  difference), the paying lines share the take-back in proportion; a claim total that differs from its lines with no
  claim-level adjustment changes nothing (the test builders write a fixed CLP04). 2a93acd dropped an own-payer exclusion
  that no case reached (its break failed no test). Tests: 3 functions (both store kinds) plus a control.
- 4bea68e: what a payer adds at the claim level (a negative adjustment: the patient's share paid after all) is money the
  claim paid toward its lines; only interest is left out (an Aetna second payer's claim in the trace). Test: the
  claim-level case with an added PR-2.

## Done (F1's routed needs), commits 4bea68e, 6665b49, dda9345
- need_status_1_supplement_not_primary (archive_rules.other_payer_paid_first, new `_after_other_payer`): a payment
  processed after another payer's (CARC 22 or 23 on its line or claim, by the payer) is no primary payment, whatever
  claim status it reports (F1's reading in states.paid_proof and detect._taken_back_items).
- need_part_paid_as_global_elsewhere (detect.resolve_elsewhere): a 26 or TC part billed apart after the same payer paid
  the whole service on another bill of the patient is a duplicate, closed as paid before, whatever its CARC.
- need_medicaid_share_size (detect._set_states; states.states_of passes the state's paid_first_partial on, a small edit
  in a function no fixer owns): a Medicaid denial kept as work because the first payer paid part of Medicaid's rate is
  sized at what Medicaid still owes.
- need_denial_on_a_voided_claim (new detect._voided_denials, run right after resolve_elsewhere): a denial of a claim the
  practice voided afterwards (the newest 837 send since the denial a void with nothing after it, or EMA's charge voided:
  detect._voided_services) is reclassified as archive rule h's 'voided', so states._decide's archive branch puts it done
  with the void as proof. The rule that lists the denial and states._decide are unchanged. 6665b49 moved it after
  resolve_elsewhere: run before it, 16 Medicare denials voided and billed again under a new bill that paid were sized at
  $0 first and closed at what the payer paid. Test added.
- 6665b49 also: money-11's same-day fold (archive_rules.fold_repeat_denials) folded a second delivery of the same claim
  id under another plan (REF*CE) into the first, and its item took the first plan's deadline
  (test_payer_windows::test_detection_dates_each_plan_by_its_own_window[store_file] failed since 422b99e). The answer's
  signature carries the claim's plan; load_archive reads claims.contract_class into Claim.plan (no fixer owns it).
- dda9345: detect's same-day copies (`_answer_copies`) keep the plan too: in Python 3.11.1 the second plan's delivery was
  still passed by as a copy of the first, which the store already listed (the same test failed there only).
- The four need tests copied into tests/test_fx_recover.py (`test_f1_*`, both store kinds).
- Every fix broken on purpose and restored ($U/scripts/breaks.py: 39 breaks, each fails its tests; git diff unchanged).

## Measured (private world copy, cycle_run 2026-11-24, A17 W view)
Final: the integration tip fe9ad71 (F1, F3a and F3b merged) against this branch at 6665b49 (that tip merged in at
ca811ed). Earlier rounds: tip 56b538e against branch 2 (d54fb7c) and branch 3 (2a93acd), before F1's merge.
- 574 items moved. Recovered: 546 items, -$15,710.47 (440 capped at the shortfall, -$8,116.94; 95 lower, -$8,255.10;
  11 higher, +$661.57). The lower ones are mostly lines that pay more than their claim (a second payer's claim-level
  PI-22 or OA-23 taking back the first payer's payment, or CO-B13 'paid before'): the recovery is now what the claim paid.
- Appeared: 5 DENIED_OTHER (+$1,813.23): 4 Medicare Supplement carriers (99999 three times and '17263 76 5', expired,
  payer_has_no_rate) and 1 Self-funded plan TPAs 11401 on Work now ($116.60, payer_has_no_rate).
- Closed as paid before, a part billed apart after the same payer paid the whole service (F1's need): 10 items from Work
  now ($741.92): 88305 parts at Anthem Medicare Advantage (4), UMWA (2), Anthem claim denials (2), UnitedHealthcare
  Medicare Advantage (2, CO-151 on 9 units of the 26, against the same payer's 9 units of the whole service paid 17 days
  before).
- Done, the claim voided by the practice (F1's need): 10 items, 2 bundling denials from Work now (UnitedHealthcare and its
  Medicare Advantage plan) and 8 expired denials (UMWA 4, Medicare KY 3, UHC Integrated Services 1), the void in EMA's
  lines or in an 837 frequency 8 sent after the denial.
- Vanished: 2 DENIED_FREQUENCY UnitedHealthcare Medicare Advantage 77401 ($25.55, already done on the tip). Size: 1
  SECOND_RATE Humana Medicare Advantage 11102 (-$17.28: a claim taken back left the review).
- A17 W, tip -> branch: ALREADY_DONE|open 1,265 -> 1,256 (in scope 998 -> 989); CLOSED_WRONGLY|closed 5 -> 7 (the two
  CO-151 items above, $0 each; A17 reads them as real denials); CORRECT|closed 10,316 -> 9,838; CORRECT|open 2,254 ->
  2,252; WRONG_AMOUNT|closed 485 -> 975 (in scope 126 -> 205); NOT_A_UNIT -$17.28; money left on the table unchanged.
  Of the WRONG_AMOUNT moves, about 440 are the brief's cap at the shortfall (A17 counts every payment after the answer,
  uncapped), most of the rest the claim-level take-back (A17 counts a line's paid as listed).
- privacy-3 on real data: 222 closures by the payer's own settling answer, every one with proof rows that reconcile; no
  answer failed to reconcile. privacy-4: 106 whole-charge CO-45 lines counted out as paid at the claim level and 2 as
  rebilled and paid, unchanged by money-10 (no prompt-pay interest among them).
- Lines that pay more than their claim, the rest taken back at the claim level: 472 second-payer claims ($119,332.25)
  and 58 first-payer claims ($17,654.33) on the real copy.

## Hand trace (de-identified views only: days from the bill's first row, claims lettered, payers by group)
42 items traced against their raw 835 rows.
- Branch 1 and 2 picks (26): the three 99999 and the '17263 76 5' supplement items listed as codes the payer has no
  rate for: right (privacy-4, decision 4); the '17263 76 5' one unsized (residual). The 11401 TPA item on Work now: right
  by decision 4 (A17 calls it already done). Four second-payer-share cases wrong on branch 1 and one replacement-payment
  case wrong on both: fixed in d54fb7c. Two vanished 77401 frequency denials: wrong (a same-day delivery reversed later
  folded as a copy; residual, already-done items). One multi-unit service whose other unit's second-payer takeback
  lowers this unit's recovery: wrong (residual). Six capped at the shortfall: right by the brief (A17 disagrees). One
  Cigna claim-level second-payer settlement: unclear. The rest right.
- Branch 2's ten lower recoveries A17 called wrong (L0 to L9), each against raw rows, all fixed on branch 3:
  L0 Medicare KY OA-109, another payer's second payer paid $45.00 after the denial: $0 -> $45.00, right. L1 Medicare KY
  11305 CO-50, reopened and paid $33.25 (the supplement's share of the other unit between): $24.96 -> $33.25, right. L2
  and L6 UMWA, denied as primary then paid as second payer, its line $42.88 / $75.58 with $37.88 / $72.50 taken back at
  the claim level: tip $42.88 / $75.58 (too high), branch 2 $0, branch 3 $5.00 / $3.08, right. L3 to L5 UMWA, three lines
  of one claim repeating the first payer's payments, $124.15 taken back at the claim level, $45.00 arrived: tip $169.15,
  branch 3 shares the claim's $45.00 by what each line pays ($1.21, $20.59, $17.06 with 17000's $6.15): the claim's total
  right, each line's share an estimate (by the first payer's own line payments it would be $0, $0, $21.88, $23.12):
  unclear per line. L7 to L9 Anthem CODE_CHANGED 77436, Medicare as second payer paid the line (as 77600) $388.33: $0 ->
  $357.48 (capped at the shortfall by the brief), right by the brief.
- Branch 3's new lower recoveries (6 sampled): two CO-B13 'paid before' claims with no earlier payment on the bill:
  unclear ($0 now; the payment they name is not on file). One B13 claim repeating a payment made before the denial:
  right ($0, tip $33.37). Medicare paid after the denial, the supplement's line repeating it with its claim paying $0:
  right ($42.33, tip $95.25). An Aetna second payer whose claim paid the coinsurance back at the claim level: wrong on
  branch 3 ($70.75), fixed in 4bea68e (the added money counts). L5 above.
- Final branch's state moves: the two CO-151 88305-26 items closed as paid before (9 units of the whole service paid by
  the same payer 17 days earlier on another bill): right by F1's rule, A17 calls them closed wrongly. The voided items:
  2 checked against their sends (one an 837 void after the denial, one EMA's void): right.

## Suites
- python3 (3.13), the full suite at ca811ed: 3,891 passed, 29 skipped, 1 xfailed, 1 failed
  (test_payer_windows::test_detection_dates_each_plan_by_its_own_window[store_file], mine since 422b99e, fixed in
  6665b49 and dda9345).
- Python 3.11.1 (SQLite 3.40.0), the full suite at 6665b49: 152 failed and 47 errors, all from the missing
  Connection.serialize but two: the same payer-windows test (fixed in dda9345: 39 passed in that file) and
  test_cloud::test_self_test_passes_when_many_requests_ask_at_once, which fails on the tip fe9ad71 in 3.11.1 too (not
  mine).
- At dda9345, the 53 test files that touch recoveries, folds and copies: 2,465 passed (four shards).

## Not done, and why
- Items worth $0 on Work now: on the real copy no Work-now item has a $0 charge; the 154 with no amount have a charge
  (133) or an unknown one (21): unpriced, not $0 lines, so they stay. The $0 and token lines of never-answered export
  claims are the exports pass's (review answers item, run_ema_open_claims).
- A second payer the 837 names (F2's residual's second half): the CARC 22 and 23 reading is in (F1's routed need); the
  837's own naming of the second payer is not.
- F1's routed items with no need file: one service on two items (a REJECTED_PRE_PAYER claim item beside a waiting
  line denial of the same drug line) and the 129 second payers' shares taken back in full ($6,424.85) on no item. Not
  started (time); the second is the owner's call for the small partial recoupments, per the brief.

## Residuals for the integrator
- The cap at the shortfall (brief, money-4) against A17 and decision 3 ("the recovery is what actually arrived"): 440
  closed items move to WRONG_AMOUNT on the cap alone. One line decides it (`cap=` in `_emit`); the owner's call.
- Lines that pay more than their claim: recoveries now count what the claim paid (shared by what each line pays); every
  other use of a line's payment (whether a service is paid, sizes, the ledger) still reads the line. No fixer owns line
  money as a whole; F1's ledger is the nearest. A17 reads the line too.
- CO-B13 'paid before' at the claim level with no earlier payment on file: the recovery is $0 now; where that payment
  was made is not known from the 835s.
- A multi-unit service where another unit's second-payer takeback lowers this unit's recovery (one item seen).
- A same-day delivery reversed later is folded as a copy (two 77401 items, $25.55, already done).
- The '17263 76 5' no-rate item is unsized (size by the base code's rate).

## Needs from others
(none: the items above have no owner among the fixers' functions; the integrator routes them)

## How to run
- Tests: `cd $S/tk/wt/fx-recover && python3 -m pytest -q -p no:cacheprovider tests/test_fx_recover.py` (unset SRFAX_,
  DROPBOX_, TRIZETTO_, SMTP_, MODMED_, ANTHROPIC_BASE_URL first; REMITWATCH_ALLOW_OPEN=1).
