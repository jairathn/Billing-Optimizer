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
  `test_a_takeback_no_item_carries_leaves_the_payment_unmarked`, and for the recoupment's own listing
  `test_a_recoupment_listed_on_its_own_carries_what_it_took_from_a_claim_in_part` ($517.60 listed: the denial done with
  the takeback named; $467.60, under $500, not listed: the denial not done). Every residual fix broken and restored
  (breaks.py: R1 12 failed, R1 listed 4, R2 2, R3 4; restored 122 passed; the partial-claim mark separately: 2 failed,
  restored 4 passed).

## Half done
- (nothing: the full suite in python3 and in 3.11.1/SQLite 3.40 is running at the time of writing; see "Tests")

## Next
1. When the suites pass: merge the integration tip again (`git fetch origin && git merge origin/claude/ecstatic-fermi-orfcgo`),
   rerun tests/test_fx_ledger.py, final push, report.

## Measured
Final run (2026-11-26 23:10 to 23:19 UTC) on a fresh private world copy (store 11-24 with EMA's reports, server cycle,
today 2026-11-24). Tip = the integration branch at 993f440 (fix7d merged) with plans 5eb528e; branch = 8bf4493, which
merged that same tip (624bf75); the integration branch has not moved since, so no other merge is in either side.
Detection 259 s (tip) and 240 s (branch), peak 1,057 MB both. (A first run at 22:44 measured 6e32766 + the findings
fixes: the same moves plus one wrong one, the Medicaid denial fixed since; its hand trace is below.)
- 21 items moved, $2,722.84 (signal, payer group, items, dollars):
  - TAKEBACK vanished 8, $1,135.80: Humana Medicare Advantage 7 ($1,092.92), UMR 1 ($42.88): takebacks of charges the
    practice voided (6 by EMA's posted void, $1,080.44; 2 by an 837 void after the payment, $55.36). A17: all 8
    already done (VOIDED).
  - TAKEBACK appeared 6, $145.39: Humana 5 ($120.89), Humana Medicare Advantage 1 ($24.50): second payers' shares taken
    back in full (money-2). A17 calls them already done (its unit counts a taken-back second share as paid or moved to
    the patient); EMA still shows one of them open on the line.
  - DENIED_DUPLICATE_UNPAID done -> expired 4, $562.61 (Humana Medicare Advantage): the payment each duplicate denial
    rested on was taken back by a WO naming the claim, and no other item carries that takeback (the PLB residual).
  - REJECTED_PRE_PAYER done -> work now 3, $879.04: Humana Medicare Advantage 2 ($457.74), Medicaid IN 1 ($421.30)
    (answers-5: the payment cited came before the item's send, or paid one service of the claim and not the drug).
- A17 classes, W view, in scope (items, dollars), tip -> branch:
  - Work now: correct 1316 $213,547.49 -> 1318 $214,005.23; already done 590 $33,716.42 -> 588 $33,661.06; already
    worked 27 $4,689.95 -> same; duplicate 5 $1,296.14 -> same; wrong amount 223 $15,539.93 -> 224 $15,961.23 (the
    Medicaid IN claim, sized at Medicaid's full rate: F3a's sizing residual); undecided 40 $7,700.45 -> same.
  - Closed: correct 2618 -> 2615 (the 3 rejected claims are listed now); closed wrongly 1 $76.87 -> same; wrong amount
    127 $14,035.40 -> same.
  - Money left on the table (lot W, denied, in scope): 346 claims, 929 units, $50,333.20 -> 343, 528, $49,502.96.
- Ledger meta (the Data tab's REVERSALS_NOT_ON_FILE_META): reversals of payments not on file, 190 lines within the
  archive (as on the tip) and 967 older (994 on the tip: the 27 reversals under another name of the payer's company now
  pair, $1,684.33); recoupments that take back no payment on file, new: 18 claims $1,104.44 within the archive and 34
  claims $3,355.62 older (44 entries each). The reviewer's measure (WO under $500 naming one claim on file with no
  payment of it before the WO) gives 37 entries $7,544.39 on this copy (36, $7,885.80 on theirs): 29 of them ($6,333.25)
  are cancelled by the payer's own CS, 72 or FB entries naming the same claim (nothing withheld, net); of the other 8
  ($1,211.14) the ledger took money from a payment of the same claim for 6, and 2 took nothing and are in the new count.
- ledger_taken on the real copy: 342 payments a recoupment took from ($32,988.19), 67 of them ($12,309.71) listed on an
  item of their own (58 TAKEBACK line items; the rest under a recoupment's own listing).
- Hand trace (private, `$U/trace_moves.txt` and `$U/hand_trace_verdicts.txt`): all 22 items the first run moved, from
  the raw rows (835 lines and CAS, PLB entries naming the claim, 837 sends, EMA's lines), relative days only. 20 right
  (one of them with the amount wrong, F3a's sizing), 1 wrong (the Medicaid denial: fixed by ledger_taken.listed, and the
  final run moves it back to done with the takeback named), 1 unclear (a duplicate denial whose remittance also
  withholds the same amount naming the claim: only the payer's letter says whether the payment stands).

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
