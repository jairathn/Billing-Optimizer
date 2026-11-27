# fx-ledger: F1 ledger and takebacks (progress)

Brief: `plans/briefs/2026-11-26_fixwave.md`, section "F1 ledger and takebacks" (and its top part). Branch `wip/fx-ledger`,
started from the integration tip 993f440 (fix7d merged). Rules: `plans/briefs/2026-11-26_common.md`. Worktree on the
session's machine: `$S/tk/wt/fx-ledger`; private folder `$S/tk/units/fx-ledger/` (mode 700; `runtests.sh <python> <args>`
runs pytest in the worktree with the feed variables unset).

Owned (the brief's table): detect.py `_build_ledger` (its take-back entry builder, the deferred rule, reversal pairing),
`_count_not_on_file`, `_taken_back_items`, `_emit_taken_back`, the WO/recoupment listing inside `run_aggregate_rules`;
states.py `paid_proof` and its helpers, `paid_first_elsewhere` (and the branches of `_decide` that decide a paid item is done).

## Fix round (2026-11-27, the two reviews' findings: fx-ledger-r1-1..4, r2-1..10)
Reviewers' tests: `$S/tk/review/fx-ledger-r1-tests/test_review_fx_ledger_r1.py`, `$S/tk/review/fx-ledger-r2-tests/`. Ported into
`tests/test_fx_ledger.py` (section "the fix round's review"), with F1's own edges; the three that are not F1's are in
`plans/progress/fx-ledger_needs/` (below). The round was written on 9f58506 + this branch (fix7d, fix7c and deadlines
merged, at 316b9d8); the integration tip then moved to 59f6822 (hook, decisions, a17's tool and the batch-2 handoffs),
merged here at 99ff8f7 without a conflict (the related suites and the decisions tests: 1443 passed). The final measurement
(run 4 below) is on 59f6822 against this branch with it merged.

Outcome per finding (all confirmed on invented data first: each ported test failed on 316b9d8 for the reviewer's reason):
- r1-3 (high) FIXED. states.paid_proof: paid_before_other_payer never counts an answer carrying CARC 22/23 (processed after
  another payer, a supplement answering with status 1) as another plan's primary payment (`_after_other_payer`).
  detect._taken_back_items: a primary's taken-back payment is covered by such an answer only as a secondary (half the
  expected amount), and such an answer is not "another payer answering as primary" in `again` (`Detector._after_other_payer`;
  paid_cover, F2's, is filtered here, not changed). The J1 case of the review: one item, Medicare's TAKEBACK, work now.
  Found beside it, not F1's: archive_rules.other_payer_paid_first reads the same status-1 supplement as primary and closes
  Medicare's denial on it (need 4 below).
- r1-4 (high) FIXED. paid_proof's EMA branches (C07 and paid_before) net out what the ledger's unlisted recoupments took of
  the 835 payment EMA posted (`_ema_pay_taken`: matched by EMA's trace_key or the check, the code and the date; a reversed
  835 payment counts as taken; an EMA-only payment counts as taken when the 835s name a recoupment of the claim after it),
  less the takebacks EMA posted itself; a listed takeback is named in the proof. The duplicate branch's "has not taken the
  payment back" no longer shows beside a listed takeback.
- r1-1 (medium) FIXED. _taken_back_items: a duplicate denial (CARC 18/B13, $0) by the same payer on or before the takeback day
  restates the payment and is neither 'again' nor 'mine': the TAKEBACK is listed (its own window), the restatement is done
  with the takeback named. The other way too: the payer's own denial that carries a takeback ('mine') gets the takeback's
  window when later (`_carry_takeback_window`: the window a TAKEBACK item would have; appeal_deadline, legal_deadline and
  deadline_basis say so), and the payment's other items name where it is worked (ledger_taken.listed = 2; `_taken_note`).
  Test changed: `test_a_takeback_no_item_carries_leaves_the_payment_unmarked` now uses a CO-50 denial (a later answer that is
  the work) instead of a duplicate denial (which r1-1 makes no longer the work).
- r1-2 (medium) NEED (F5 and _decide's shared FEES branch): need_second_payer_share_step.py (with r2-10's test).
- r2-1 (medium) FIXED. A $500+ WO naming a claim on file is listed at what the claim's netted recoupments withheld
  (`_wo_listing_sizes` from `plb_claimed`, `plb_unapplied`): nothing when they net to nothing (a carried balance recovered,
  a WO its own CS cancels) or the same remittance's reversal took the claim; the whole net (not the face) for a claim taken
  from in part ("net of the payer's adjustments naming the claim"). Not done (open question): a TAKEBACK for a service whose
  own reversal took the payment and that nothing answers since (the reviewer's "list the service's TAKEBACK when nothing
  answers it"); see the measurement for how many removed listings had no other item.
- r2-2 (medium) FIXED. _voided_services counts a void only of the paid claim: its claim id, a send whose original reference
  is the claim's payer claim number, or a send to the same payer group; later sends count the same way (a send to another
  payer after the void does not undo it).
- r2-3 (medium) FIXED. paid_first_elsewhere: with no expected amount, a first payment under half the item's EMA charges never
  closes the Medicaid denial (the half floor is `need`, never _threshold's one cent).
- r2-4 (low) NEED (F6, cloud.SERVING_TABLES): need_serving_copy_ledger_taken.py.
- r2-5 (low) FIXED. A second payer's share taken back is covered by any second payer's standing share of the service
  (status 2/3, or status 1 after another payer), whichever paid first.
- r2-6 (low) FIXED. paid_proof's whole-claim rule leaves the claim's $0.00-billed services out of those that must be paid
  (`_zero_billed`: a known charge of nothing in the [code, date, charge, ...] lines; unknown charges count).
- r2-7 (low) FIXED. `_recouped_not_on_file`: entries whose reference holds exactly one claim id not on file, netted by claim id
  and payer (WO/FB/J1 with CS/72), are counted in REVERSALS_NOT_ON_FILE_META (older when nothing of the bill is on file).
- r2-8 (low) FIXED. The deferred budget: one remainder per remittance, shared by its reversed claims in (remittance, claim id)
  order.
- r2-9 (low) FIXED. states._r835 reads a PLB entry as naming a claim of the bill by its claim id in any letter case (the longest
  that fits) or by a word that is the payer claim number of exactly one claim of the bill (the ledger's _plb_names reading).
- r2-10 (low) NEED (F5 and _decide's FEES branch): need_second_payer_share_step.py.
- Found by the hand trace of run 3 (r1-1's own residual), FIXED at a97c3c0: a payer's restatement split into pieces (the
  payment marked B13 on one $0 line, the original CO-45, CO-253 and PR-3 on $0 lines of their own) read as new answers of the
  service, so the takeback of that payment was listed nowhere and its duplicate denial expired. `Detector._restates` reads
  the claim's other $0 lines of the same code and date: one restatement. Test
  `test_a_restatement_split_into_pieces_is_not_the_takebacks_work` (both store kinds; needs line control numbers, REF*6R,
  for code_once to hold). Run 4 shows it on the real copy: the TAKEBACK is listed, work now in its own window, and the
  duplicate denial stays done with "took ... of it back later, worked on its own takeback item".
Every fix broken on purpose and restored: `$U/breaks2.py` (16 breaks, the split restatement included; each fails its tests;
restored 202 passed, `git status` clean), output `$U/breaks2.out`.

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
    `scripts/trace_r4.py`; its output deleted with the world copy): 25 items on the tip (W view, in scope, plus 6
    SECONDARY_NOT_BILLED). None is a payment paid_proof
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
- Nothing (2026-11-27 04:30 UTC): every finding fixed or written as a need (above), the hand trace's fix in, 16 breaks
  done, both measurements, the timed pair and both full suites done (below), the integration tip merged again (da2f255,
  then 1fd1a9d), world copies deleted, the measuring worktrees removed.

## Next
- The integrator: merge wip/fx-ledger; route "Needs from others" (each with its failing test) and read the open questions.

## Tests
- Fix round: tests/test_fx_ledger.py 202 tests, both store kinds (the reviewers' r1 and r2 tests ported, the hand trace's
  split restatement, F1's own edges), all passing; 16 breaks each fail their tests and restore exactly. After merging
  59f6822: the related suites (ledger, states, story, takebacks, archive rules, decisions) 1443 passed.
- Full suite on 68c2e9f (59f6822 merged; `plans/tools/shard.sh`, 4 shards, logs in `$U/runs/fxl-py313`, `fxl-py311`):
  - python3 3.13 / SQLite 3.5x: 3,549 passed, 29 skipped, 1 xfailed, 1 failed: test_hook_review.py::
    test_presses_rebuild_the_provider_tables_when_the_analysis_fills_the_run, not F1's (cloud and activity): its clock is
    the real one plus a simulated 200 s analysis, so a load average of 16 leaves the run under its 45 s for the provider
    tables; it passes alone on this branch (53 s).
  - 3.11.1 / SQLite 3.40.0: 3,367 passed, 13 skipped, 1 xfailed, 152 failed, 47 errors: 198 are the store image's
    `Connection.serialize`, which
    that build lacks (the class CLAUDE.md allows there; the merged hook and decisions tests add to round 1's 133). The
    other one, test_results.py::test_the_server_store_and_the_compact_serverless_store_give_the_same_figures, failed in the
    shard after test_ema_intake's serialize failures and passes alone and in the same file order on both this branch and
    the tip (27 failed, the serialize ones, 237 passed on each). None is in the ledger's, the states' or the takebacks'
    tests.
- Then the integration tip moved to bf1f17b (a brief only, nothing routed to F1), merged at da2f255, and to 34e3573 (a
  brief: an F3a need routed to F6), merged at 1fd1a9d; tests/test_fx_ledger.py 202 passed after each.
- tests/test_fx_ledger.py (round 1): 126 tests, both store kinds (58 ported from the reviewers and verifiers, the rest F1's own edges
  and the residuals'), all passing. Every fix broken on purpose and restored (`$U/breaks.py`).
- Full suite before the last merge (commit 741b25e): python3 3.13 / SQLite 3.53: 3016 passed, 29 skipped, 1 xfailed,
  none failed. 3.11.1 / SQLite 3.40: 109 failed and 24 errors, every one of them the store image's
  `Connection.serialize` that build lacks (test_cloud, test_ema_intake, test_hotfix2d, test_hotfix2, test_feed_and_day,
  test_status_and_sends and the like; none in the ledger's or the states' tests), the class CLAUDE.md allows there.
- After merging the integration tip 7b1937b (fix7c and deadlines; commit eb5c12b): tests/test_fx_ledger.py and the
  takeback suites 213 passed; full suite in python3 (plans/tools/shard.sh, 4 shards): 3194 passed, 29 skipped, 1 xfailed,
  none failed; in 3.11.1/SQLite 3.40: 3077 passed, 109 failed and 24 errors, the same 133 tests as before the merge and
  each the missing `Connection.serialize` (one such error line per failure, the same 24 files). No node check: static/
  is untouched.

## Measured
### Fix round (2026-11-27)
Two runs, each on a fresh private world copy (store 11-24 with EMA's reports, server cycle, today 2026-11-24), deleted after:
- Run 3 (01:40 to 01:51 UTC): tip 9f58506 (fix7d, fix7c and deadlines merged) against the branch at 341af0d (every finding
  fixed, before the hand trace's fix).
- Run 4 (02:49 to 03:08 UTC), the final one: tip 59f6822 against the branch at a97c3c0, which merged 59f6822 (99ff8f7). The
  merges it includes, on both sides: fix7d, fix7c, deadlines, hook, decisions and a17's tool (the whole integration branch
  at 59f6822, which has not moved since).
Detection: run 3 204.6 s (tip) and 192.6 s (branch); run 4 250.9 s and 378.3 s, peak 1,105 and 1,106 MB. Run 4's branch ran
under a load average of 12 on 4 CPUs (other units' runs): the steps F1 does not touch slowed as much (activity refresh 18.0
to 28.8 s, EMA turnaround 7.8 to 10.3 s), and run 3 measured the same F1 code faster than its tip. Then a timed pair
(04:16 to 04:27 UTC, `$U/timed_cycle.sh`: F1's functions wrapped with timers, the branch at da2f255 then the tip at
bf1f17b, each on a fresh copy deleted after; load average falling from 12 to 8): detection 323.2 s (branch) and 244.2 s
(tip), yet F1's functions took 22.4 s against 20.0 s: `_r835` 13.8 / 13.7 (9 calls each), `_build_ledger` 3.8 / 2.5,
`run_aggregate_rules` 3.1 / 2.5, `paid_proof` 1.0 / 0.7 (14,114 calls), `_taken_back_items` 0.7 / 0.6, the rest (the new
`_ema_pay_taken`, `_restates`, `_recouped_not_on_file`, `_carry_takeback_window`, `_voided_services`) under 0.1 s. F1
adds about 2.4 s to a detection; the rest of the gap is the load (the branch ran first both times, as the load fell).

Moved in run 4: 156 items (run 3: the same 156 but two, below).
- Claim-level recoupment listings removed 124, $440,716.06 (Medicare KY 75 $347,554.69, Medicare IN 43 $87,299.12,
  UnitedHealthcare 3 $3,515.18, Aetna 2 $1,815.86, UHC Integrated Services 1 $531.21), r2-1: their netted entries
  withheld nothing (a carried balance FB recovered by WOs, a WO its own CS cancels, or the same remittance's reversal took
  the claim). Probed from the raw rows in run 3: each removed listing's bill has another live item for the services (91)
  or was paid again since (33); 2 listings shrink, $9,737.07 less (Medicare IN: a $510.61 face nets to $177.90 withheld;
  $9,562.33 to the $157.97 twenty WOs took beyond the carried balance).
- TAKEBACK vanished 8, $1,135.80 (Humana Medicare Advantage 7, UMR 1): charges the practice voided (EMA's void, or an 837
  void after the payment).
- TAKEBACK appeared 12, $792.11: second payers' shares taken back in full, Humana 5 $120.89 and Humana Medicare Advantage
  1 $24.50 (r1-3: the share is no cover for its own takeback); Humana Medicare Advantage 4 $330.81 and UnitedHealthcare
  Medicare Advantage 1 $255.95 where the payer's duplicate answer on or before the takeback restated the payment (r1-1, one
  of them the hand trace's fix); Medicare KY 1 $59.96, a re-payment withheld by J1 on its own remittance.
- Denials carrying a takeback, expired to work now 5, $554.92 (Humana Medicare Advantage 4, Humana 1): the payer paid the
  service after the denial and took the payment back later; worked until the window to dispute the takeback closes (r1-1).
- REJECTED_PRE_PAYER done to work now 3, $879.04 (answers-5, round 1); a duplicate denial done to expired 1, $130.23 (its
  payment taken back by a WO no item carries); a Medicare KY denial the payer reversed, gone 1, $76.47 (its money is on
  the J1 TAKEBACK above).
- Run 3 against run 4: the hand trace's fix lists one more TAKEBACK ($23.04, work now) and keeps its duplicate denial done
  with the takeback named (run 3 moved it to expired).
A17 (W view, in scope, items and dollars; tip to branch, run 4):
- Work now: correct 1,407 $237,123.48 to 1,409 $237,581.22; already done 1,007 $135,364.46 to 1,004 $134,303.84;
  duplicate 20 $2,034.93 to 19 $1,091.82; wrong amount 540 $34,645.78 to 541 $35,067.08; already worked 27 and undecided
  109 unchanged.
- Closed: correct 2,579 to 2,575 in scope (10,445 to 10,319 in all: the removed listings); closed wrongly 1 and wrong
  amount 126 unchanged.
- Money left on the table (lot W, denied, in scope): 326 claims, 907 units, $47,185.27 to 323, 506, $46,355.03 (the
  rejected claims' drug units, now listed). Taken-back units on no open item (lot W): in scope 1 ($41.77) on both sides
  (a TAKEBACK closed as paid again on EMA's posting of a check no 835 carries: A17 does not read EMA-only payments); out of
  scope 12 $1,574.59 to 7 $856.32.
Ledger meta (REVERSALS_NOT_ON_FILE_META, run 4): recoupments that take back no payment on file, 18 claims $1,104.44 within
the archive (as in round 1) and 41 claims $4,408.13 older (round 1: 34, $3,355.62): r2-7 counts 7 more older claims,
$1,052.51.
Hand trace (private: `$U/hand_trace_verdicts3.txt`, raw rows `$U/trace3.txt`, both mode 600): all 38 items run 3 moved, from
the raw rows (835 lines and CAS, PLB entries naming the claim, 837 sends, EMA's lines), relative days only. 37 right (one
with the amount not F1's, one overlapping a CO-16 denial item), 1 wrong: the split restatement, fixed at a97c3c0 and
checked in run 4. Notes: A17 calls the second payers' shares taken back "already done" (its units count the share as paid
or moved to the patient, whatever was taken back); 3 denials carrying a takeback show A17 "wrong amount" (their size is the
fee table's rate, not what the payer paid and took back); 2 of them are in the check-first block settled_in_ema, which
cites EMA's posting of the payment taken back (open question below).

### Round 1
Final run (2026-11-26 23:10 to 23:19 UTC) on a fresh private world copy (store 11-24 with EMA's reports, server cycle,
today 2026-11-24). Tip = the integration branch at 993f440 (fix7d merged) with plans 5eb528e; branch = 8bf4493, which
merged that same tip (624bf75). The integration branch moved after the measurement (fix7c and deadlines, merged into
this branch at eb5c12b): the measurement includes neither, on either side.
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
- Hand trace (private: the verdicts in `$U/hand_trace_verdicts.txt`; the raw-row dump, `$U/scripts/trace_moves.py`'s
  output, deleted with the world copy): all 22 items the first run moved, from
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

4. `need_second_payer_share_step.py` (fx-ledger-r1-2 and r2-10; states._decide's shared FEES branch and story's route 'fees'
   next step, with F5's bill-the-patient steps). A second payer's share taken back (TAKEBACK with evidence second_payer) says
   "bill the patient, or dispute the takeback" in its title only: its basis says "find out why, then dispute or bill the right
   payer" and its next step "Match the takeback to the payer's overpayment letter ...". The change: when
   _ev(f)['second_payer'], the basis says the coinsurance is the patient's again (bill the patient, or dispute the takeback)
   and the route/next step offers the bill-the-patient action. Real copy (reviewers): 11 such items ($369.63) on Work now.
5. `need_serving_copy_ledger_taken.py` (fx-ledger-r2-4; F6, cloud.py). The dashboard's copy does not carry ledger_taken, so the
   drawer's live proof on Vercel counts a payment a recoupment took in part at its whole amount ($117.60 where the server
   says $87.60). The change: SERVING_TABLES carries ledger_taken (filtered to the copy's lines, as lines is), SERVING_BUILD
   bumped. (Or story._state_of shows the stored basis and proof on the copy.)
6. `need_status_1_supplement_not_primary.py` (found fixing r1-3; archive_rules.other_payer_paid_first, in no fixer's row: F2,
   whose residual is "a supplement that answers with claim status 1 is counted as a primary payment", or the integrator). The
   rule reads a supplement's status-1 payment carrying OA-23 as another payer paying as primary, and closes Medicare's
   denial on it, proven ("Paid by <supplement> as primary ...: this denial is another payer's answer"). The change: leave out
   of its `prim` a payment whose line or claim carries CARC 22 or 23 by the payer (as states._after_other_payer and
   Detector._after_other_payer read it). Real copy (reviewers): 820 paid supplement answers report status 1, 342 with OA-22/23.

## Open questions
- Fix round residuals (none is a closure; each named with its item count on the real copy, run 4):
  - states.settled_in_ema (a check-first block, F1-adjacent): it counts EMA's posting of a payment the payer took back as
    "insurance paid", so 2 denials carrying a takeback say "find which payer paid" where the answer is "the payer took it
    back" (the drawer shows the takeback). The fix would net `_ema_pay_taken`'s gone amounts there, as paid_proof does.
  - A denial carrying a takeback keeps the fee table's size, not what the payer paid and took back (3 items, A17 wrong
    amount, within 5% for 2 of them): detect's valuation, not F1's.
  - A TAKEBACK closed as paid again on EMA's posting of a check no 835 carries (1 item, $41.77): A17 counts the unit still
    taken back because it reads no EMA-only payment. The proof names the check; only the 835 of that check would settle it.
  - G2211 and 99024 shares taken back are never listed (as on a denial: nothing to pursue); A17's lot shows 3 G2211 units
    out of scope.
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
