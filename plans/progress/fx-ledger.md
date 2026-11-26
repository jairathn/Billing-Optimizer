# fx-ledger: F1 ledger and takebacks (progress)

Brief: `plans/briefs/2026-11-26_fixwave.md`, section "F1 ledger and takebacks" (and its top part). Branch `wip/fx-ledger`,
started from the integration tip 993f440 (fix7d merged). Rules: `plans/briefs/2026-11-26_common.md`. Worktree on the
session's machine: `$S/tk/wt/fx-ledger`; private folder `$S/tk/units/fx-ledger/` (mode 700; `runtests.sh <python> <args>`
runs pytest in the worktree with the feed variables unset).

Owned (the brief's table): detect.py `_build_ledger` (its take-back entry builder, the deferred rule, reversal pairing),
`_count_not_on_file`, `_taken_back_items`, `_emit_taken_back`, the WO/recoupment listing inside `run_aggregate_rules`;
states.py `paid_proof` and its helpers, `paid_first_elsewhere` (and the branches of `_decide` that decide a paid item is done).

## Done
- about 22:14 UTC: ported the reviewers' and verifiers' tests into `tests/test_fx_ledger.py` (58 tests, both store kinds).
  On the tip (993f440): 36 fail (every finding: money-1, 2, 5, 6, 8, 9, hosts-4, answers-5's paid_proof part), 22 pass
  (each a control meant to pass: money-1's T-I5 precedent and its $600 listing, money-2's double-payment control, money-5's
  no-carried-balance runs and its own-purpose control, money-6 with only a denial on file, money-8's WO case).
  Sources: `$S/tk/review/since-money-tests/`, `since-money-verify-tests/` (v1, v2, v5, v5b, v6),
  `since-hosts-tests/test_hosts_takeback_order.py`, `since-answers-tests/test_answers_review.py` (answers-5's exports item).

- about 22:30 UTC: the eight findings fixed (all 58 ported tests and 26 own edge tests pass on both store kinds; each fix broken on
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

- about 22:45 UTC: residuals (commit 6e32766, tests in tests/test_fx_ledger.py):
  - PLB/J1 in paid_proof: `detect._save_taken` (called at the end of `_build_ledger`) writes table `ledger_taken`
    (line_key, taken_cents, taken_on, standing_cents) every run; `states._r835` adds `taken` to each 835 row;
    `paid_proof` drops a payment taken back in full and counts only what is left of one taken in part. The dashboard's
    copy does not carry the table (cloud.SERVING_TABLES is F6's): its states are the stored ones, so nothing differs.
    Real data: on the tip one service of the reviewer's kind (paid, then the payment recouped on a later check, then
    answered again) is 'done' on the recouped payment; see the measurement for the count.
  - Reversals under another company name: the reversal pairing in `_build_ledger` falls back to the payer's own claim
    number and the amount within one payer company (`LEDGER_COMPANIES = {"umr": "uhc"}` over `payer_company`). Real data
    (read-only probe of the master): 26 reversals under WAUSAU and 1 under UMR whose payment of the same amount sits under
    UNITED MEDICAL RESOURCES with the same payer claim number, one candidate each ($1,684.33).
  - Takebacks on voided claims: `_voided_services` (837 void sent after the payment with no later send, or EMA's lines of
    the service all a void status with the charges netting to nothing: a posted void, never a status alone); A17 calls
    the 8 such TAKEBACK items on the tip already done (2 in scope by 837, 6 out of scope by EMA's void).
  - Denials paid on an 835 but routed to note/check_first (residual 4): traced from raw rows (private
    `trace_r4.txt`): 25 items on the tip (W view, in scope, plus 6 SECONDARY_NOT_BILLED). None is a payment paid_proof
    misses on the item's own bill: 13 are 88305 parts (26/TC) or the global whose payment sits on another bill of the
    same patient (A17: paid_as_global / paid_on_other_bill; the facts read one bill); 5 are second payers' denials of
    the patient's share after the primary paid (check_first: the archive rules' 'likely' done, decision 4); 4 are
    second-payer answers of services the primary paid ('call' after a resend); the rest single cases. See "Needs from
    others".

- about 23:05 UTC: the hand trace of the first measurement found one wrong move (see "Measured"): a Medicaid denial of a
  service Medicare had paid went from done to expired because Medicare's payment was taken back (J1), while Medicare's
  own TAKEBACK item already carries that takeback (the same service's money on two items, one of them counted as lost to
  its deadline). Fixed: `ledger_taken.listed` is 1 once the takeback is on an item of its own (a TAKEBACK item of the
  line, from `_emit_taken_back`; or the recoupment's own $500+ listing for a claim it took from only in part, from the WO
  listing in `run_aggregate_rules`; `_mark_taken_listed`); `paid_proof` leaves a listed takeback's payment in the other
  items' proofs and names it ("...; KY MEDICARE took $117.60 of it back later, worked on its own takeback item"). An
  unlisted takeback (the service answered again since: that answer is the work) still pays nothing. Tests:
  `test_a_takeback_on_an_item_of_its_own_is_not_worked_again_on_another_payers_denial` (WO, J1) and its control
  `test_a_takeback_no_item_carries_leaves_the_payment_unmarked`. Every residual fix broken and restored (breaks.py:
  R1 12 failed, R1 listed 4, R2 2, R3 4; restored 122 passed).

## Half done
- The measurement again with the listed fix (the first run's numbers are under "Measured"), then the hand traces of
  whatever else moves, then delete the world copy (`$U/p2/world.sqlite*`, the tip_tree and branch_tree worktrees).

## Next
1. Re-measure the branch (commit, `git -C $U/branch_tree checkout --detach <commit>`, `KEEP_WORLD=1 $U/measure.sh
   $U/branch_tree branch2`, `compare.py snap_tip snap_branch2`), trace the moves, fill "Measured".
2. Suites (touched files, then the full suite in python3 and in 3.11.1/SQLite 3.40), merge the integration tip again,
   final push, report.

## Measured
First run (2026-11-26 22:44 to 22:53 UTC; world copy of store 11-24 with EMA's reports, server cycle, today 2026-11-24):
tip = integration 993f440 (fix7d merged; plans 5eb528e), branch = 624bf75 (that tip merged) + 6e32766 (residuals) +
the uncommitted findings fixes of 7993e16 (all committed by then). Detection 259 s (tip) and 275 s (branch), peak 1,056 MB.
- 22 items moved, $2,768.96 (signal, payer group, items, dollars):
  - TAKEBACK vanished 8, $1,135.80: Humana Medicare Advantage 7 ($1,092.92), UMR 1 ($42.88). Takebacks of charges the
    practice voided (6 by EMA's posted void, 2 by an 837 void after the payment). A17: all 8 already done (VOIDED).
  - TAKEBACK appeared 6, $145.39: Humana 5 ($120.89), Humana Medicare Advantage 1 ($24.50). Second payers' shares taken
    back in full (money-2). A17 calls them already done (its unit counts a taken-back second share as moved to the
    patient or paid); EMA shows one of them, $34.41, still open on the line.
  - DENIED_DUPLICATE_UNPAID done -> expired 4, $562.61 (Humana Medicare Advantage): the payment the duplicate denial
    rested on was taken back by a WO naming the claim, and no other item carries that takeback (the PLB residual).
  - CLAIM_DENIED_UNPAID done -> expired 1, $23.31 (Medicaid KY): WRONG, fixed since (the listed takeback above).
  - REJECTED_PRE_PAYER done -> work now 3, $879.04: Humana Medicare Advantage 2 ($457.74), Medicaid IN 1 ($421.30).
    answers-5: the only payment was made before the item's send, or paid one service of the claim and not the drug.
- A17 classes, W view, in scope (items, dollars), tip -> branch:
  - Work now correct 1316 $213,547.49 -> 1318 $214,005.23; already done 590 $33,716.42 -> 588 $33,661.06; already worked
    27 $4,689.95 -> same; duplicate 5 $1,296.14 -> same; wrong amount 223 $15,539.93 -> 224 $15,961.23; undecided 40
    $7,700.45 -> same; closed wrongly 1 $76.87 -> same; closed correct 2618 -> 2615.
  - Money left on the table (lot W, denied, in scope): 346 claims, 929 units, $50,333.20 -> 343, 528, $49,502.96.
- Ledger meta (Data tab, REVERSALS_NOT_ON_FILE_META): the reversals of payments not on file, 190 lines dated within the
  archive (as on the tip) and 967 older (994 on the tip: 27 now paired under another name of the payer's company); the
  recoupments that take back no payment on file, new: 18 claims $1,104.44 within the archive and 34 claims $3,355.62
  older, 44 entries each. (The reviewer's 36 entries $7,885.80 counted WO under $500 with no payment before them on the
  11-24 copy, before CS/72 netting and money-5's budget: not the same measure.)

## Needs from others
Each with its failing test in `plans/progress/fx-ledger_needs/` (named need_*.py so the suite does not collect it; copy it
into tests/ to run it; each fails on this branch for the reason given).
1. `need_part_paid_as_global_elsewhere.py` (detect.resolve_elsewhere: in no fixer's row, so the integrator; or F2 if
   routed with the closures). A part of 88305 (26 or TC) billed apart and denied after the same payer paid the whole
   service on another bill of the same patient is a duplicate whatever its CARC; resolve_elsewhere closes a part's
   denial on a payment of the whole only when the payment comes after the denial (or the CARC is a duplicate's). Real
   copy: 13 Work-now items (A17: already done, paid_as_global / paid_on_other_bill), routed to "note".
2. `need_medicaid_share_size.py` (detect._set_states: in no fixer's row, so the integrator). money-9's second half: the
   item stays work (done here) but keeps Medicaid's whole rate as its size; store shortfall_cents =
   fx['paid_first_partial']['owed'] (F1 can return it from the state, for example st['owed_cents'], when asked).
3. `need_denial_on_a_voided_claim.py` (the line rules that list a denial and the claim rules: in no fixer's row; or
   states._decide's shared branches). A denial of a claim the practice voided afterwards (837 frequency 8, nothing sent
   after) is listed as work. F1 reads the voids only for takebacks (detect._voided_services, which the rule can call).
   Real copy: 3 claim denials ($226.33, check first) and 2 bundling denials ($81.92, EMA voided), A17: already done.

## Open questions
- A claim-level REJECTED_PRE_PAYER item and a line-level denial of the same service can both be listed (real copy: a drug
  line J7345, repeatedly denied CO-16 and waiting on a resend, and the claim's latest send rejected; A17 marks the claim
  item "partly duplicate" but correct). One item per bill would be the fold's rule (not F1's).
- A rejected Medicaid claim sent as the second payer is sized at Medicaid's full rate (A17: wrong amount, rate over
  25%). That is next_fixes section 3's residual (a claim sent as secondary priced at the full rate), F3a's sizing.
- The second payers' shares taken back in full and on no item: 129 lines ($6,424.85) on the branch's real copy; 9
  ($503.67) are on claims a recoupment took from only in part (the recoupment's own listing stands for the claim when it
  is $500 or more; under $500 nothing lists them); a rough probe from the raw rows puts 4 ($272.75) as paid again by
  the same payer, 4 ($20.74) as G2211 or 99024 and 1 ($21.73) as answered again, and could not tell which of the older
  rules (billed again on another claim, voided, answered again on the same day) skips the other 111 ($5,605.96).
  money-2 removed the one cause the review named (the primary's payment counted as covering the share). Whether the
  partial-claim rule should list recoupments under $500 is for the owner.
