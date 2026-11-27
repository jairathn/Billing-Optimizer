# fx-exports (fix wave F3b: the exports, the twins and G8): progress

Brief: `plans/briefs/2026-11-26_fixwave.md`, section "F3b the exports, the twins and G8" (and the common brief
`plans/briefs/2026-11-26_common.md`). Branch `wip/fx-exports`, started from the integration tip bc29767 (fix7d merged).
Private folder: `$S/tk/units/fx-exports/` (mode 700; scripts `measure.sh`, `measure_both.sh`, `snap2.py`, `compare.py`,
`compare2.py`, `a17cmp.py`, `ht.py`, `ht2.py` (hand-trace: raw rows of a finding's bill, dates as days from the visit, ids
hashed), `breakfix2.py` (break several places, run the named tests, restore exactly), `claim_audit_a17.py` + `a17/` (the
merged A17 from the integration tip 8c59195; the build round's copy is in `old_a17/`)). The reviewers' tests are under
`$S/tk/review/fx-exports-r1-tests/` and `fx-exports-r2-tests/` (a copy in `$S/tk/units/fx-exports/rt/`).

Functions this fixer owns (detect.py): `run_ema_open_claims`, `_ema_check_open_claims`, `_ema_claims_found`,
`_send_day_twins`, `run_ema_report_claims`, and the helpers they call that this fixer wrote: `_export_patients`,
`_note_resent_bill`, `_claim_content`, `_unanswered_before_send`, `_bills_with_lines`, `_g8_one_service`,
`_exports_yield_to_newer_bills`, and in the fix round `_print_batch_paid`, `_twins_by_name`, `_billed_under`, `_who`,
`_one_patient`, `_prior_work`, `_fold_into`. Small edits outside (no fixer owns these lines): `_save_ema_wiring` writes
the feed date as the unseen meta's newest_835 (build round); `_patient_keys` takes two keyword arguments with defaults,
`member_names=True` and `persons=None` (fix round; every other caller keeps its behaviour).

## State (updated 2026-11-27 04:10 UTC): the fix round

Done. Every finding of the two reviews is addressed (table): r1-2 and r2-5 are kept on
this branch (both items listed, pointing to each other) and get the full hand-over at the merge with wip/fx-837 (Needs).
The round's measurement found one more fault of this round's own making, now fixed (c41f286): the r1-3/r1-5 name rules,
as first written, set aside a send-day twin whose EMA name differed from a remitted name, and listed 12 claims EMA had
answered through it (A17: six paid). Branch head before this file's last edit: c41f286 (+ the plans-only merge 944dec5).
tests/test_fx_exports.py: 107 cases (both store kinds) green in python3 and in 3.11.1/SQLite 3.40.

## The fix round: each review finding and its outcome

| Finding | Outcome | Test (tests/test_fx_exports.py, both store kinds) |
|---|---|---|
| r1-1 high: a corrected bill in the print batch after a denial is not listed | fixed: `_ema_check_open_claims` keeps the after-send day for a claim in the print batch (as the tip did); the duplicate rule is its own step, `_print_batch_paid`: only a payment that still stands (net of takebacks and reversals, the claim's own payer company, the service's own part) on its EMA claim, another EMA claim of the patient, or another bill's remittance line the ledger keeps | test_a_print_batch_rebill_of_a_visit_the_payer_denied_is_still_listed_to_be_sent (denied and reversed), test_a_print_batch_rebill_of_a_visit_paid_on_its_835_is_a_duplicate, test_a_print_batch_duplicate_made_after_the_visit_was_paid_is_not_listed |
| r2-1 medium: the same, a corrected C002 of the bill or a new bill after another bill's denial | fixed by r1-1's change | test_a_corrected_claim_left_in_the_print_batch_after_its_denial_is_still_listed (both cases) |
| r2-7 low: a print-batch claim waits as 'no source can check' when a stray remittance is newer than the feed date | fixed: the print batch is exempt from that wait (its due day is the practice's own, dated by the day EMA's exports reach) | test_a_claim_never_sent_is_listed_to_be_sent_whatever_stray_remittance_is_newer |
| r1-5 high: two claims take the same named send-day twin (a child's unpaid claim answered by the parent's) | fixed: `_twins_by_name`: a name from the claim's own evidence (`_who`: its bill's remittances, the EMA claim a bridge found, EMA's patient) chooses one of several twins, one to one (two claims naming the same twin take neither); a member id's one remitted name never chooses | test_a_childs_claim_never_takes_its_parents_send_day_twin_by_the_subscribers_name, test_two_claims_whose_names_choose_the_same_twin_take_neither, test_a_member_ids_one_remitted_name_never_chooses_a_send_day_twin, test_send_day_twins_of_two_patients_are_told_apart_by_the_patients_name (restated on export claims: own evidence) |
| r1-3 medium: a lone twin naming another patient answers the claim | fixed: a twin whose EMA claim names someone else than the claim's own evidence is not its twin; with only a member id's one remitted name, twins that all name others are rejected unless such a patient is billed under the claim's member id elsewhere (`_billed_under`: their other bills' 837s carry it, a dependent) | test_a_lone_send_day_twin_of_another_named_patient_does_not_answer_the_claim (the reviewer's), test_a_lone_send_day_twin_of_another_patient_never_answers_a_claim_emas_reports_name, test_a_lone_send_day_twin_of_a_patient_billed_under_the_claims_member_id_still_answers_it |
| r1-4 medium, r2-6 low: the before-send bridge takes another patient's unanswered EMA claim | fixed: `_unanswered_before_send` takes nothing when nothing names the claim's patient, nor when an answered EMA claim of its lines names the patient; a member id's one remitted name only confirms a lone candidate | test_the_before_send_bridge_never_takes_another_patients_claim_when_the_claims_own_was_answered (the reviewers' two worlds), test_an_answered_ema_claim_of_the_patient_with_the_claims_lines_blocks_the_bridge; answers-7's test restated with the member's remittance naming the patient |
| r2-3 medium: the cross-pass yield merges two children on one subscriber id | fixed: `_one_patient` (the same keyed name and birth date, or the same keyed name under the same member id, each from the claim's own evidence), never a member id's one remitted name | test_a_child_whose_sibling_was_never_remitted_keeps_its_own_visit, test_a_member_ids_one_remitted_name_never_makes_two_bills_one_visit, test_two_people_on_one_subscriber_id_seen_the_same_day_stay_two_services |
| r2-4 medium: the one-visit rules join a father and son of one name | fixed: `_export_patients` and the yield join by `_one_patient`, never the keyed name alone | test_a_father_and_son_of_one_name_are_two_patients_for_the_one_visit_rules (both rules), test_a_father_and_son_named_only_by_their_members_remittances_stay_two_patients (the reviewer's two worlds), test_one_person_under_two_ema_patient_ids_is_one_patient_for_the_one_visit_rule (restated: name and birth date, or name under one member id) |
| r1-2 medium, r2-5 low: a person's work on the exports' item does not follow when the newer item was already listed | fixed on this branch by keeping it: an exports' item a person worked whose newer item was listed before this run stays listed with its work, and both items point to each other (save() hands work only to an item new in its run). The full fix (the work follows) needs save(): F3a's fix round added that clause (see Needs) | test_work_on_an_export_item_stays_on_a_listed_item_when_the_patients_name_arrives_later, test_an_unworked_export_item_yields_when_the_patients_name_arrives_later; `plans/progress/fx-exports_needs_save.py` (not collected) holds the test for after the merge |
| r2-2 medium: the kept resend is listed twice once F3a chases resends, and includes a service already paid | fixed: the kept resend is sized at the services no remittance of the bill answered; `_exports_yield_to_newer_bills` makes it yield to the 837 pass's item of the same bill when one is listed (F3a's `_837_resends`); the answers-5 test's two bills have different patients | test_a_bill_sent_again_after_its_primary_answer_is_chased_once (the reviewer's; also passes on a scratch merge with origin/wip/fx-837), test_an_export_resend_yields_to_the_837_passs_item_for_the_same_bill, test_an_export_claim_whose_bill_went_out_again_stays_listed_when_nothing_else_chases_it |
| (found at the 8c59195 merge) the hook's vanished self-check keeps a yielded item listed | fixed: `_fold_into` records where a claim's chase went in the carrying item's evidence ('folded'), for the yield and the exports' one-visit rule | test_an_unworked_export_item_yields_when_the_patients_name_arrives_later, test_an_export_claim_listed_before_gives_its_visit_to_a_newer_bill_and_is_not_kept_by_the_self_check |
| (F3a's need) an exports' claim not due yet is not on the waiting view | done: `run_ema_open_claims` calls `_note_waiting` where it exists (wip/fx-837); checked on a scratch merge | test_an_exports_claim_not_due_yet_is_noted_for_the_waiting_view |
| (found by this round's measurement) the name rules of r1-3 listed 12 claims EMA answered through their send-day twin: all 837 claims whose twin's EMA name differed from the member id's one remitted name (11) or the bill's own remittance (1); A17 calls six paid, three never answered, three undecided | fixed: a name that differs proves nothing alone (a remittance and EMA spell one person otherwise, a dependent carries the subscriber's id). `_twins_by_name` reads what the bills on file say of each twin's patient (`_twin_kin`: the patient's other EMA claims a bill holds, their 837 member ids and remitted names, remittances compared with remittances): the claim's patient when they meet the claim's bill, member id or names; another patient's when EMA's own name for the claim's patient (`_patient_keys`' new `ema_names`) is another chart's, or the patient's bills carry only other member ids and names. A twin of a name of its own, nothing else known, is set aside only when another EMA claim of the claim's content that no bill holds may be the claim's own (`_spare_ema_claims`, the reviewer's r1-3 world). A member id's one remitted name still never chooses (r1-5) | test_a_lone_twin_whose_ema_name_differs_from_the_remitted_name_is_the_claims_when_no_other_ema_claim_can_be, test_a_lone_twin_of_a_patient_whose_bills_are_another_members_never_answers_the_claim, test_a_twin_whose_patients_remittances_name_the_claims_patient_answers_it_though_ema_spells_the_name_otherwise (with and without the bill's own remittance), test_a_lone_send_day_twin_of_another_patient_never_answers_a_claim_emas_reports_name (also with the third EMA claim held by its bill) |

Every fix broken on purpose and restored exactly (sha256 equal, `breakfix2.py`; the measurement's fix: the kin's 'another patient', the spare rule, the remitted names' tie, the held-claim exclusion, EMA's own name each made their tests fail): the print batch's after day, its wait,
the duplicate rule and its standing check; the own-evidence veto, the one-to-one rule, the member-name veto and its
dependent exception, member names choosing twins; the bridge's name requirement and its answered-claim guard; the
joins by name alone, by a member id's name, by person keys; the keep-for-work branch; the resend carrier and sizing;
the waiting-view call; both folds. Each break made its tests fail.

The reviewers' 28 test cases (r1 and r2, both kinds) on this branch: 26 pass. The two that fail are r1-2's
(test_work_on_an_export_item_follows_the_newer_bill_when_the_patients_name_arrives_after_both_were_listed): its world
names the patient only through the member id's one remitted name, which by r2-3 never joins two claims, so the yield it
asserts does not happen (its own message: "the test no longer reaches the rule"); the restated test covers the finding.

## Decisions taken (for the reviewers)

- One principle for who a claim's patient is: a name from the claim's own evidence (its bill's remittances, the EMA
  claim a bridge found, EMA's patient for an export claim) can join two claims (with the same member id, or with the
  same birth date) and choose a twin; a member id's one remitted name never joins and never chooses (a subscriber's id
  covers the family: r1-5, r2-3), and may confirm a lone candidate of the before-send bridge (as answers-7's review
  proposed).
- A name that differs proves nothing alone (the measurement: a remittance and EMA spell one person otherwise, a middle
  name in one of them; a dependent's claim carries the subscriber's id). A send-day twin is another patient's only on
  evidence: EMA's own name for the claim's patient (same spelling source) names another chart, or the twin's patient's
  other bills on file carry other member ids and other remitted names (as A17 rule 5 calls a twin 'other'). With nothing
  else known, a twin named otherwise is set aside only when another EMA claim of the content, held by no bill, may be
  the claim's own (r1-3's world); else it is the only place the claim's EMA claim can be, and answers it.
- The 837's own patient (F3a's claims_837.patient_key and person_key, READ_VERSION 2) is not read here. It is the
  natural own evidence for 837-only claims; adding it to `_who` is a few lines at the merge, but the test builders'
  placeholder names (clm837 names every subscriber 'SAMPLE PAT') then contradict the remittances' and EMA's invented
  names in many worlds, so it should land with its own test review (see Needs).
- answers-7 without any name is no longer bridged: the claim's own EMA claim may be the answered one (r2-6), so the
  claim and Production's item for the other EMA claim both stay listed (the tip's behaviour).
- r1-2/r2-5: keeping both items (with hints both ways) rather than leaving the work on a row no list shows, until
  save() hands work to an untouched listed heir.
- r2-2: the kept resend's size follows F3a (the services no remittance of the bill answered); the plain exports' path
  for a bill answered before (the tip's L03 control) is unchanged.
- The duplicate rule for the print batch uses the claim's own evidence only (`_patient_keys` without member names).

## Needs from others

- Integrator, at the merge with wip/fx-837 (its save() now hands an item's work to an untouched listed heir, fx-837-r1-3):
  in `_exports_yield_to_newer_bills`, narrow the keep-for-work branch to both items worked, i.e. replace
  `prior.get(fid, {}).get("worked") and to_fid in prior:` with `prior.get(fid, {}).get("worked") and
  prior.get(to_fid, {}).get("worked"):`. Checked on a scratch merge (my branch at 12f0287 + origin/wip/fx-837 d4fc535):
  then `plans/progress/fx-exports_needs_save.py` passes (the work follows to the newer bill's item) and
  test_work_on_an_export_item_stays_on_a_listed_item_when_the_patients_name_arrives_later is to be restated for the
  both-worked case (both stay, pointing to each other). Move the needs test into tests/test_fx_exports.py then.
- F3a / integrator: `run_ema_open_claims.live_items` gives None for a charge the exports do not give (two places:
  `None if sig in unknown else sig[4]`, and the status fallback `None if (v[0], v[1]) in unknown else v[2]`) once F3a's
  `_emit_open_claim` (which sums `x[2] or 0`) is merged; F3a's stopgap in `_emit_open_claim` goes then. F3a's test:
  plans/progress/fx-837_needs_tests.py::test_an_exports_charge_row_with_no_charge_given_is_priced_at_the_payers_rate.
- F3a: its needs test test_an_exports_claim_not_due_yet_is_on_the_waiting_view reads the view's rows as dicts
  (`c.get("claim_id")`); `_finish_waiting` stores lists `[pcn, grp, sent, due, billed]`. With `c[0]` it passes on the
  scratch merge (this branch's `_note_waiting` call is in).
- F3a or the integrator: the 837's own patient (claims_837.patient_key and person_key, READ_VERSION 2) as own evidence in
  `_who` (read the bill's 837 rows where the columns exist; add to 'names' and 'persons'). Then check the worlds whose 837s
  name 'SAMPLE PAT' (tests/test_837_sends.clm837) while the remittances or EMA name someone else (tests/test_fx_exports.py,
  test_837_sends.py, test_ema_wiring.py): an own-evidence name that differs vetoes a send-day twin.
- F3a's need for F3b not done in this round (not among the review's findings): `run_ema_open_claims`' newest_for, a newer
  exports' bill a payer answered as primary keeps carrying the visit (fx-837-r2-6's exports side). Test:
  plans/progress/fx-837_needs_tests.py::test_an_older_exports_bill_stays_given_way_once_the_newer_bill_of_the_visit_is_answered.
  The older bill is listed beside the newer bill's denial: a duplicate on the list, not a claim dropped. Next F3b round.
- F6 (selfcheck.check_vanished) and runlog: this branch records a yield as a fold ('folded' in the carrying item's
  evidence, rules exports_yield_to_newer_bill and exports_one_visit); runlog's decision log names every fold
  `archive_rules:fold_into` (it could read the entry's own 'rule'). After the F3a merge, check that the 837 pass's own
  one-visit yields are not kept by the vanished self-check (they record no fold).
- F3a / integrator, at the merge with wip/fx-837 (found on a scratch merge, 2026-11-27 03:20 UTC): fx-837's 837 one-visit yield records no fold, so the hook's vanished self-check (merged at 8c59195; wip/fx-837 lacks it) keeps the older bill's item listed: 10 cases of tests/test_fx_837.py's work tests fail on 072a920 merged with 8c59195 alone (and the same on 072a920 + this branch), e.g. test_the_work_on_the_older_bill_passes_to_the_bill_made_again. The yield needs the same record as this branch's `_fold_into(carrier, gone, rule, note)` (evidence 'folded' on the newer bill's item).
- Self-check / save owner (the hook's keeper): an item kept by the vanished self-check is re-listed from detection's stale row, which on the world copy had lost the service's part and was priced as the whole service: the two $0-billed export claims of one 88305 visit (TC $16.17 and 26 $42.76; this branch's answers-3 rule no longer lists them, A17: nothing billed) came back at $98.00 each.
- F4 (ema_checks.claim_answers' content bridge): an export claim whose own EMA claim is outside Production's dates can be
  bridged by content to another patient's EMA claim when it is the only one of that content (seen in a test world while
  writing r2-4's tests; the world was given each bill's own EMA claim). Not changed here; no real-data count.
- Build round needs, answered: F3a did the reverse of answers-1 (50fb8eb, `_837_export_carriers`) and the 837 patient
  read (READ_VERSION 2); `s837` index 7 is still read by `_claim_content`.

## Merges

- e641d38 merged the integration tip 8c59195 (hook, decisions and a17 merged) before the measurement; 944dec5 merged
  59f6822 (plans only: the handoff and the final-stage brief). The measured code is c41f286, which equals this branch on
  59f6822. At the final push the tip was still 59f6822 (fetched 2026-11-27 04:00 UTC): no further merge.
- Scratch merges (not pushed), with origin/wip/fx-837 at 072a920: tests/test_fx_exports.py, test_837_sends.py and
  test_ema_wiring.py pass; tests/test_fx_837.py has 10 failing cases (its work tests), which fail the same on 072a920 merged
  with 8c59195 alone: the hook's vanished self-check keeps the older bill's item, as wip/fx-837 lacks the hook merge and
  its yields record no fold (Needs). With the save() narrowing applied there, plans/progress/fx-exports_needs_save.py
  passes and test_work_on_an_export_item_stays_on_a_listed_item_when_the_patients_name_arrives_later fails, as expected
  (to be restated at that merge, see Needs).

## Suites

On c41f286 with this file's edits (the integration tip 59f6822 merged), three shards each (plans/tools/shard.sh):
- python3 3.13.18 with SQLite 3.53.1: 3,454 passed, 29 skipped, 1 xfailed, 1 failed:
  tests/test_feed_and_day.py::test_the_pages_say_how_far_the_remittances_reach[serverless] (the banner was not drawn yet:
  the flaky banner test the fix-wave brief names, routed to F6; passes alone, both cases).
- Python 3.11.1 with SQLite 3.40.0: 3,273 passed, 13 skipped, 151 failed and 47 errors, every one of them the serialize
  class (Connection.serialize is missing in that build; re-run with one line each, every line names serialize), none
  in this fixer's files (test_fx_exports, test_837_sends, test_ema_wiring, test_desk, test_ema_checks, test_insights,
  test_workable, test_answer_needs_proof, test_selfcheck, test_listed_once).
- This fixer's files beside the suites: tests/test_fx_exports.py 107 passed in python3 and in 3.11.1; with
  test_837_sends.py and test_ema_wiring.py 213 passed; the touched set (with test_desk, test_ema_checks, test_insights,
  test_workable, test_answer_needs_proof, test_selfcheck, test_listed_once, test_hooks) 619 passed before c41f286.
- static/ is unchanged: no node check needed.

## Measurements (fix round)

One server cycle per side on a private world copy (the store of 11-24 with EMA's reports; cycle_run dated
2026-11-24), then A17 (the integration tip's claim_audit_a17.py) on the same copy. Sides: tip = 8c59195; build round =
a2120cd merged with 8c59195 (scratch); fix round before the measurement's fix = 4989011; final = c41f286. No EMA step error
on any side; detection's peak 1.1 GB on each (251 s for the tip alone; 325 to 450 s on the branch runs, which shared the
machine with test runs). Aggregates only.

| Tip 8c59195 against the final branch c41f286 | items | dollars |
|---|---|---|
| vanish: NO_ANSWER (Anthem/BCBS 2, Cigna 3, UHC Medicare Advantage 11), REJECTED_PRE_PAYER (Humana MA 1) | 17 | $1,654.68 |
| appear: NO_ANSWER (Anthem/BCBS 10, Anthem/BCBS Medicare Advantage 19, UHC Medicare Advantage 8) | 37 | $6,122.82 |
| change size: NO_ANSWER (Anthem/BCBS 4, Optum 1) | 5 | -$16.55 |
| open in both, evidence changed (a rejected claim's 837 item found its EMA claim; a token line left an item's lines) | 2 | - |

- Split: the build round made the 17 vanishings and the 5 size changes; this fix round made the 37 appearances. At
  4989011 the round listed 12 claims (all set aside by the first name rule); c41f286 answers 4 of them again and lists 29
  others (two claims of one member id competing for one proven twin, or twins whose patient's bills are another member's).
- A17, every open item, tip to final: ALREADY_DONE 2,821 to 2,810 (-11, -$1,074.27), CORRECT 4,969 to 4,997 (+28,
  +$5,307.80), UNDECIDED 1,283 to 1,285 (+2), WRONG_AMOUNT 2,063 to 2,064 (+1). In scope (services since 2026-01-01):
  ALREADY_DONE 1,364 to 1,361, UNDECIDED 115 to 118. Money left on the table and closed wrongly: unchanged.
- The round alone (4989011 against c41f286): ALREADY_DONE -2, CORRECT +27, and the 12 claims the first rule listed: 4
  answered again (A17: 3 paid, 1 not payable and paid), 8 still listed (A17: 2 paid, 2 owed, 1 owed at another amount, 3
  undecided).
- Counters, tip to final: 837_answered_in_ema 4,891 to 4,888, 837_no_answer 777 to 798, 837_waits_unfound 1,690 to 1,672,
  export_answered_in_ema 403 to 402, export_billed_nothing 10 (new), NO_ANSWER items 822 to 840.

## Hand trace (fix round)

All 61 moved items (tip against the final branch) traced from the raw rows (`ht.py`, `dbg_why.py` on an instrumented
run of the same cycle, `probe_kin.py`, `probe_unique.py`; private folder, `hand_trace_fix_round.md`):
- 17 vanished, right: 15 claims whose content with their token line ($0.01 to $0.90) matches exactly one EMA claim charged
  on the send day, which the payer answered after the send (A17: done); 1 rejected claim now carried by its 837 item (one
  claim, one item); 1 Cigna claim EMA shows answered with $0 and no balance (A17 says never answered: the calibration point
  the final-stage brief lists).
- 5 changed: 2 right (sized at the billed charge, not a rate above it); 2 right to leave the lists (two $0-billed parts of
  one 88305 visit, answers-3) but kept by the vanished self-check at $98.00 each (Needs); 1 unclear (J3490 now priced
  from its history, F2's residual).
- 37 appeared: 29 right (A17 finds them never answered: the send-day twins are another patient's, or two claims of one
  member id compete for one twin); 1 right to list at another amount (F3a's estimate); 3 unclear (A17 undecided, the
  twin's patient unproven either way); 4 wrong, wasted checks (A17: paid): 3 had their twins narrowed to one each, which
  the twins rule's day count then refuses because the day's other claims keep the others; 1 had every twin set aside.
  A one-to-one elimination per send day would answer such claims, but on the same send days A17 calls 28 listed claims
  never answered: left as is (the error that costs a check, not money).
- 2 evidence changes, right.

## Build round (kept for the record)

### State at the end of the build round (2026-11-27 00:45 UTC)

- Every finding and residual in scope is fixed but one residual (export claims dated only by the visit day get no
  send-day twins: left, see below). `tests/test_fx_exports.py`: 18 tests, 42 cases (both store kinds), green; every fix
  broken on purpose and restored exactly (sha256 equal after `breakfix.py`).
- The world measurement found one regression of this branch's answers-3 fix and it is fixed (39c5121, 3df7489): a token
  line ($0.01 J3590) left the claim's content, so the content bridge found no EMA claim and 2 export claims EMA shows
  paid were listed. The same gap was already in the 837 pass (15 open items A17 calls already done); `_ema_claims_found`
  now matches every claim's whole content, so both passes are fixed.
- Merges: the integration tip 5eb528e was merged at 4ba364b (before the measurement) and 9f58506 (fix7c and the deadlines
  unit merged) at 97ea88e (after it), with no conflict. The measurement's branch code is 39c5121: this branch plus the
  integration tip as of 5eb528e; it does not include fix7c's or the deadlines unit's merges.

### Findings and residuals: status

| Item | Status | Test (tests/test_fx_exports.py) |
|---|---|---|
| answers-1, exports' side | fixed: `_exports_yield_to_newer_bills` (run_ema_report_claims, after the 837 pass, with or without EMA's reports): an exports' item yields each visit (date, code, part) a newer bill only the 837s know carries, for the same patient by name (`_patient_keys`: never the subscriber id alone); its other services stay, sized again; work follows through `_heirs` | test_a_visit_the_exports_chase_yields_to_a_newer_bill_only_the_837s_know, test_two_people_on_one_subscriber_id_seen_the_same_day_stay_two_services, test_an_export_claim_keeps_the_services_the_newer_bill_does_not_carry, test_two_parts_of_a_service_on_two_bills_are_two_services |
| answers-2 | fixed: `_ema_check_open_claims(final, newest)` takes the feed date; the unseen meta's newest_835 is the feed date | test_an_export_claim_no_source_can_check_waits_whatever_stray_remittance_is_newer |
| answers-3, exports' half | fixed: live lines leave out a line billed $0 or under $1 (a line whose charge is not known is kept); a claim with nothing billed is not listed, counted (export_billed_nothing). The token lines stay in the claim's content (`_claim_content`) | test_a_line_billed_at_no_charge_is_nothing_owed_on_an_export_claim, test_a_line_billed_at_a_token_charge_still_tells_which_ema_claim_is_the_claims, test_a_claims_content_counts_each_token_line_once |
| answers-5, exports' half | fixed: a newer 837 submission drops the exports' claim only when the 837 pass takes it; for a bill answered as primary the claim stays, dated by the newer send, naming the newer claim (`_note_resent_bill`) | test_an_export_claim_whose_bill_went_out_again_stays_listed_when_nothing_else_chases_it |
| answers-7 | fixed: `_unanswered_before_send` (in `_ema_claims_found`): the only unanswered EMA claim of the claim's lines charged on or before its send, one to one (no other candidate claim takes it, no other untied bill on file has those lines: `_bills_with_lines`), that patient's when the patient is known | test_a_claim_charged_days_before_its_837_went_out_is_listed_once (with and without a token line), test_a_claim_two_unanswered_ema_claims_could_be_takes_neither, test_a_claim_with_another_bill_of_the_same_lines_on_file_takes_no_ema_claim, test_a_claim_whose_patient_is_named_takes_only_that_patients_ema_claim |
| residual: G8 three items for whole, 26 and TC; a part priced at the whole rate | fixed: `_g8_one_service` (run_ema_report_claims): the newest charge of one patient's service carries what it covers; each line priced by price_unanswered_lines (the part's rate, units, multiple-procedure share, never above its charge; $0 lines nothing) | test_production_lists_one_service_once_and_prices_its_parts_at_their_own_rates |
| residual: twins told apart by the patient's name key | fixed: `_send_day_twins`: when a claim has several twins, the patient's keyed name (`_patient_keys`) keeps the one twin that is the patient's; such a twin is the claim's EMA claim whatever else went out that day | test_send_day_twins_of_two_patients_are_told_apart_by_the_patients_name (with and without a token line) |
| residual: a print-batch duplicate made after the visit was paid stays listed | fixed: `_ema_check_open_claims`: only a claim that reached its payer and came back rejected is answered by postings after its send alone; a claim still in the print batch is answered by a payment that still stands on its visit, whatever its day | test_a_print_batch_duplicate_made_after_the_visit_was_paid_is_not_listed; tests/test_837_sends.py claim 89's expectation changed (a print-batch bill made after its visit was paid is that duplicate), every other assertion unchanged |
| residual: exports' one-visit rule by one EMA patient id | fixed: `_export_patients`: EMA patient ids whose keyed names meet are one patient for the one-visit rule | test_one_person_under_two_ema_patient_ids_is_one_patient_for_the_one_visit_rule |
| residual: export claims dated only by the visit day get no send-day twins | left, by design: a send-day twin needs the day the claim went out, and such a claim has none (no submission date, no bill date, no 837); the content bridge still applies to it. In the world the tip had 2 such items, both lines billed $0, which answers-3 now leaves out: none is left | (none) |

### Decisions taken (for the reviewers)

- answers-1: the reviewer's own scenario had no name for the patient anywhere (only the subscriber id). The brief and the
  verifier say never merge on the subscriber id alone (two people on one id seen the same day are two services), so the
  ported test adds the review's own identity evidence (an earlier remittance naming the member's patient). With nothing
  naming the patient, both items stay. F3a reads the 837's own patient loop (2000C/2010CA); once merged, the 837 can
  name the patient itself (see Needs).
- answers-3: a token line is nothing owed but part of the claim's content. Leaving it out of what finds EMA's claim made
  paid claims look unanswered (measured), so it is kept there and nowhere else.
- answers-5: the kept exports' item is dated by the newer send (the payer's answer is due from it), so it is not chased
  before the payer could answer the resend.
- answers-7: the new bridge never takes another patient's EMA claim: exactly one unanswered candidate, one to one against
  every untied bill on file with the same lines, and the patient's name when known.
- Print batch: a bill EMA made again after its visit was paid by the same payer, and left in the print batch, is a
  duplicate of a paid visit; test_837_sends claim 89 asserted the opposite and was changed with that reason.

### Needs from others

- F3a (837 pass): the reverse of answers-1 (an 837-only bill OLDER than an exports' bill of the same visit yields to the
  exports' item). wip/fx-837 has `_837_one_visit` with carriers; check at the merge that it covers an exports' carrier.
- F3a (x12_837.py, the 837 tables): the patient of each 837 claim as a keyed name the run can compare with
  `_patient_keys`. wip/fx-837 has `Detector._837_patients`; after both merges, `_exports_yield_to_newer_bills` should
  take the 837 item's patient from it (and the visit key from `detect._visit_line`, which adds the distinct-service
  modifiers). Failing test today (no name anywhere): the reviewer's
  since-answers-tests/test_answers_review.py::test_one_visit_in_the_exports_and_again_under_a_new_bill_only_the_837s_know_is_chased_once.
- F3a: keep `s837` on the 837 pass's claims (index 7: the claim key of the send its lines were read from), or pass
  `token_lines`: `_claim_content` reads the token lines from it. If the 837 pass stops leaving token lines out of its
  items, nothing is needed (the content then uses the items as they are; tested).
- At the merge with wip/fx-837 (asked by F3a; not done here because this branch's `_emit_open_claim` still sums
  `x[2]` and would fail on None): `run_ema_open_claims.live_items` gives None for a charge the exports do not give, in
  both places (`None if sig in unknown else sig[4]`, and the status fallback `None if (v[0], v[1]) in unknown else
  v[2]`); F3a's `_emit_open_claim` sums `x[2] or 0`. F3a's failing test once its stopgap goes:
  tests/test_workable.py::test_each_claim_is_chased_once_at_its_own_size_and_closed_ones_show_as_written_off. Also F3a's
  `_note_waiting(pcn, grp, sent, due, billed)` where run_ema_open_claims skips a claim not due yet (the function exists
  only on wip/fx-837).

### Suites

- tests/test_fx_exports.py (42 cases) green in all four environments: python3 (3.13, SQLite 3.53), the newest libraries
  (venv2_compat), 3.12 with SQLite 3.45, 3.11.1 with SQLite 3.40.0.
- Related suites after the token fix (test_fx_exports, test_837_sends, test_answer_needs_proof, test_ema_wiring,
  test_workable, test_money_falls_off, test_sizes_and_rates, test_ema_checks, test_states, test_story,
  test_archive_rules, test_ema_pages, test_ema_pages2, test_selfcheck): 984 passed.
- Full suite in python3 (plans/tools/shard.sh, 2 shards, on 1b9a99c's code but for the logging-only 9b30ce9 change
  landing while it ran): 2,931 passed, 29 skipped, 1 xfailed, 1 failed: tests/test_feed_and_day.py::
  test_the_pages_say_how_far_the_remittances_reach[serverless] (a browser test: the banner was still empty when read on a
  loaded machine); it passes when run again alone, on both hosts. Not caused by this branch.
- Full suite in 3.11.1 with SQLite 3.40.0 (venv_sqlite340, 2 shards, on 1b9a99c): 2,815 passed, 13 skipped, 1 xfailed,
  109 failed and 24 errors, every one of them for the build's missing Connection.serialize (the serialize class
  CLAUDE.md allows there; rerun with one line each, all 133 name `serialize`): test_cloud 29, test_ema_intake 27,
  test_hotfix2d 14, test_feed_and_day 7, test_hotfix2 7, test_status_and_sends 6, test_feeds 5, test_hotfix2c 5,
  test_archive 4 (compact kind), test_runlog 4, test_parity 4, test_repair 4, test_ema_ledger 3, 2 each in test_swing,
  test_cycle_converges and test_notes_bodies_cli, 1 each in test_fax, test_aws, test_api_review, review6/test_r6_cov_holds,
  test_efficiency, test_ema_reports, test_diagnose and test_hotfix2_pages.

- On the merge with 9f58506 (97ea88e): tests/test_fx_exports.py and tests/test_837_sends.py 74 passed; full suite in
  python3 3,110 passed, 29 skipped, 1 xfailed, none failed; full suite in 3.11.1 with SQLite 3.40.0: 2,992 passed, 13
  skipped, 1 xfailed, 110 failed and 24 errors: the same serialize class as above (every one names `serialize` when run
  again with one line each), plus the brief's known flaky banner test
  (tests/test_feed_and_day.py::test_the_pages_say_how_far_the_remittances_reach[server]), which passed when run again.

### Next command

    cd $S/tk/wt/fx-exports && python3 -m pytest -q tests/test_fx_exports.py

### Measurements (private world copy: the live store image of 2026-11-24 with EMA's reports; today 2026-11-24)

Runs: the tip's code (bc29767) and this branch twice: 4ba364b (cec8036 plus the integration tip 5eb528e) and 39c5121
(the token-line fix on top). Each on a fresh copy, then A17 (claim_audit_a17.py from origin/wip/a17, --as-of 2026-11-24).
The 3df7489 guard changes nothing on the world (the 837 pass still leaves token lines out of its items).

Open never-answered items, tip -> branch (39c5121):

| Signal | Listed by | Tip | Branch |
|---|---|---|---|
| NO_ANSWER | 837 pass | 767, $145,152.38 | 750, $143,497.70 |
| NO_ANSWER | weekly exports | 45, $6,364.54 | 43, $6,305.61 |
| NO_ANSWER | Production (G8) | 10, $1,352.02 | 10, $1,198.40 |
| REJECTED_PRE_PAYER | 837 pass | 10, $1,893.34 | 11, $1,904.78 |
| REJECTED_PRE_PAYER | weekly exports | 35, $6,048.81 | 35, $6,048.81 |
| REJECTED_PRE_PAYER | Production | 6, $2,385.77 | 5, $2,374.33 |

All open items 10,755 ($3,908,321.61) -> 10,736 ($3,906,454.38): 19 items left the list ($1,713.61), 3 Production items
were sized again (-$153.62), 1 claim listed twice became one item.
- By payer group, the items that left: UnitedHealthcare Medicare Advantage 11 ($1,226.80), Anthem / BCBS 4 ($253.41),
  Cigna 3 ($221.96), Humana Medicare Advantage 1 ($11.44, a Production duplicate). Sized again: Anthem / BCBS 2
  (-$119.62), Optum (UHC) 1 (-$34.00).
- Why: 15 claims of the 837s and 1 more answered in EMA, found by their whole content once the token line counts
  (`_claim_content`); 2 export claims billed only at $0 (answers-3); 1 Production rejected item whose claim the 837
  pass now carries (one claim, one item; that 837 item turned from NO_ANSWER to REJECTED_PRE_PAYER, fix and resubmit);
  3 Production items priced line by line (`_g8_one_service`: capped at their charge, a drug line valued at the payer's
  share of charges).
- Run counts: 837_answered_in_ema 4,891 -> 4,926; 837_no_answer 777 -> 761; export_billed_nothing 10; 
  unanswered_claims_one_service 2 (both protos were not listed on the tip either); no EMA step failed. answers-1's
  yield, answers-5's resend and the two-charts rule found no case in this world (no count).
- The first branch run (4ba364b) listed 2 export claims EMA shows paid (a $0.01 J3590 left their content): the
  regression fixed in 39c5121; the second run lists neither.

A17's classes, tip -> branch: ALREADY_DONE|open 2,375 ($664,542.82) -> 2,358 ($662,961.15), -17 and -$1,581.67;
CORRECT|open 4,758 -> 4,757 (-$120.50: a Cigna claim EMA shows answered, see the hand trace); UNDECIDED|open 1,118 ->
1,117 (-$165.06: the Production duplicate and the 3 sized again). Money left on the table unchanged.

Time and memory: detection 216.4 s on the tip, 279.5 s and 282.1 s on the branch runs, peak 1,055 MB in all three. The
machine was busier during the branch runs (the unchanged ema_turnaround step took 5.9 s on the tip run, 9.8 s and 9.1 s
on the branch runs). A timed pair run back to back on a loaded machine (`time_run.py`: wall time per Detector method,
nested calls inside their callers; tip 433.5 s, branch 486.2 s in all, the unchanged save step 89.2 s and 93.6 s) puts
this branch's cost at about 5 s: the three passes it touches took 102.6 s on the tip and 107.4 s on the branch
(run_ema_open_claims 25.0 -> 22.9, run_837_open_claims 63.6 -> 69.9, run_ema_report_claims 14.0 -> 14.6); the new steps
themselves: _claim_content 1.8 s (two calls), _unanswered_before_send 0.5 s, _export_patients 0.2 s, _g8_one_service and
_exports_yield_to_newer_bills under 0.1 s. Peak memory unchanged.

### Hand trace (27 moved items, raw rows: 835s, 837 sends and lines, EMA's export rows and report lines; ht.py, ht2.py)

- Right (25):
  - 2 export claims whose only line is an 88305 billed $0 (the 26 and the TC): nothing owed (A17: ZERO_CHARGE).
  - 3 Production items sized again: two capped at their charge ($580.00 -> $414.21 on a $414.21 charge; $98.00 ->
    $64.00 on a $64.00 charge), one with its J3490 line valued at the payer's share of charges ($104.32 -> $150.49), as
    the 837 pass prices it.
  - 15 claims of the 837s that leave the list: each has exactly one EMA claim with every charged line, the token line
    included (J3590 $0.01, J3301 $0.84 to $0.90, J3357 $0.45, 99024 $0.01), the same payer (EMA's name for it), charged
    on or just before the send day, and the payer's payment or contractual answer posted after the send (12 paid, 3 a
    CO-45 answer with the rest moved to the patient). A17 agrees on 14 (ALREADY_DONE). The 15th it calls CORRECT
    (never answered): EMA shows Cigna's CO-45 on each line after the send and the patient paying the rest, balance $0;
    the payer answered, so this trace keeps the branch's verdict.
  - 1 Production rejected item that now rides on its 837 claim (the same EMA claim, found by content with its J3357
    token line), and that 837 item itself (NO_ANSWER -> REJECTED_PRE_PAYER: EMA calls the claim rejected).
  - 1 exports item that no longer lists its $0.01 J3590 line (still found in EMA, still $76.64).
  - 1 837 item whose EMA claim is now the one same-day twin with the member's keyed name (three Anthem twins of the
    same content charged the day before the send); EMA shows only a bare $0 row on it, so it stays listed.
- Wrong (2, fixed): the 2 export claims the first branch run listed (paid in EMA; a $0.01 J3590 left their content).
  Fixed in 39c5121; the second run does not list them.
- Unclear: none.
