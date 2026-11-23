# needs_B2: wiring remitwatch/archive_rules.py (unit B2 -> INT, unit B, unit C1, unit D)

Status: FINAL, after the reviews and fixes (2026-11-23). This replaces the builder's version. Unit B2 owns only
remitwatch/archive_rules.py and tests/test_archive_rules.py and edits nothing else; every item below is a request to the
owner of the file it names. INT applies sections 1, 3 and 6 (brief_INT items 1 to 3). Figures are aggregates from a copy of
the real store (run date 2026-11-22, server mode); finding ids are RemitWatch ids.

## 1. The hook bodies (remitwatch/detect.py, class Detector; INT item 1)

There is one way to wire the rules now. `archive_rules.run_on_detector` (the builder's "Option B") is gone: two ways to wire
invited both, and the one-call path skipped hook_fold's second reading of a person's work. Replace the bodies of the
three no-op methods unit B left in detect.py (section "remitwatch.archive_rules acts on the run's items at these four
points") with exactly:

```python
    def hook_expected(self) -> None:
        """After the line and claim rules set expected amounts: a fair expected amount where an item has none (drugs and
        skin substitutes: archive_rules.drug_expected, rule 10)."""
        from .archive_rules import hook_expected
        self.archive_expected = hook_expected(self)        # {'expected_cents': n}

    def hook_archive_rules(self) -> None:
        """Between the second rates and resolve_elsewhere, before every EMA closure, so a closure the 835 archive proves
        comes first: closures with proof, reclassifications and the items rule 8 raises (archive_rules.evaluate, rules 1
        to 8). The folds wait for hook_fold."""
        from .archive_rules import hook_archive_rules
        self.archive_summary = hook_archive_rules(self)    # per-rule counts, applied_counts, rate_errors; sets self.archive_folds

    def hook_fold(self) -> None:
        """Before save: repeat listings of one service folded into the item that carries it (archive_rules fold_into). A
        person's work is read again first: an item a person worked is never folded."""
        from .archive_rules import hook_fold
        self.archive_folded = hook_fold(self)              # {'fold_into': n}
```

The docstring detect.py has today for hook_archive_rules names `archive_rules.run_on_detector`, which no longer exists:
the body above replaces it. hook_hide stays a no-op (section 4). The call sites stay where unit B put them in
Detector.run: hook_expected right after run_claim_rules; hook_archive_rules between emit_second_rates and
resolve_elsewhere; hook_fold right before save; hook_hide after save and apply_hide_rules. RULE_FILES already lists
archive_rules.py.

Tests that patch exactly these three bodies onto the real Detector (file store and compact store) and must keep passing
once wired: test_the_detector_hooks_apply_the_rules_before_save, test_wired_rule5_fold_does_not_drop_a_worked_item_from_the_worklist,
test_wired_rule8_raises_one_listing_of_the_service. test_without_the_hooks_nothing_changes pins today's unwired result and
must be changed to the wired one (it asserts no DENIED_PLAN_EXCLUSION item and two unfolded J7345 items without an expected
amount; wired, the PR-204 line is raised and the J7345 listings fold and get the payer's own price).

Cost (copy of the real store, server mode): peak resident memory 867 MB with the hooks against 853 MB without (+14 MB; the
pre-fix module took 940 MB, +87 MB). The archive is released before each hook returns (Archive.release breaks the
answer/claim cycles, so nothing waits for the cycle collector). evaluate takes 2.8 to 3.6 s on a quiet machine. Serverless
emulation (compact in-memory store, EMA world): see section 8.

## 2. What apply_actions does to the Detector's items

| action | effect |
|---|---|
| raise_item | applied first: det._emit(signal, 'denial', ...) on the 835 line, then its hint; evidence: basis, carc_group, who_should_pay, what_to_fix, proof, replaces (the older listings folded into it), and archive_rule = {rule, action 'raise_item', strength 'proven'}, a dict like every other action |
| close_with_proof, proven | det._mark_paid(f, paid_date, paid_cents, note, detail); recovered_cents 0 where the money came with the answer itself or a role change; hint cleared; evidence.archive_rule = {rule, action, strength, proof} |
| close_with_proof, likely | never closes: the item stays open, hint = the note, evidence.archive_rule = {reclass 'likely_paid', label 'Likely paid: check first', state_hint 'done', strength 'likely', proof}; counted as likely_close_kept_open |
| reclassify, proven | hint; shortfall_cents = the new size when the action gives one, the old size kept as evidence.archive_rule.size_before_cents |
| reclassify, likely | hint; the size stays (a person confirms first); the proposed size is evidence.archive_rule.proposed_shortfall_cents |
| fold_into | the item leaves det.findings; the carrier's evidence.folded lists it (finding_id, payment_date, carc, shortfall_cents, appeal_deadline, legal_deadline, same_claim_number, rule, signal, note). Where the payer answered under the same claim number and the folded answer's deadline is earlier, the carrier takes the first decision's appeal_deadline, keeps its legal_deadline only when later, gets "Counted from the payer's first decision of {date}: it reprocessed the same claim number later, which does not start the window again" added to deadline_basis, and evidence.first_decision_deadline (C26) |
| expected_cents | expected_cents, shortfall_cents and evidence.expected_basis |

evidence.archive_rule keys: rule, reclass, label, state_hint, strength, proof (the decisive rows and one more), and where
present possible_overpayment_cents, paid_by, units_billed, units_paid, patient_share_left_cents, patient_share_cents,
prior_payer_cents, secondary_paid_cents, taken_back_cents, new_item, new_reason, who_should_pay, what_to_fix,
size_before_cents, proposed_shortfall_cents.

Never folded: an item a person worked (a status other than open, a note, an appeal date; archive_rules.worked_ids). evaluate
turns a rule-5 fold of a worked item into a 'replaced' reclassification naming the new item (new_item), a rule-8 fold of a
worked older listing into 'replaced' with new_reason, who_should_pay and what_to_fix (nothing is raised then: the worked item
stays the one listing), and hook_fold reads the work again before it folds.

On the real store the deadline rule moved 97 carriers' dispute deadlines to the first decision's (Humana Medicare
Advantage 53, UnitedHealthcare Medicare Advantage 42, Anthem 2), 91 days earlier on average; C26 matches the worksheet.

## 3. States (INT item 2: remitwatch/states.py, story.py)

Read evidence.archive_rule.state_hint and strength. 'done' + 'proven' -> DONE; 'waiting' -> WAITING with the follow-up
counted from the newest proof row's date (reversed_no_answer carries the latest takeback); 'likely' is never DONE without a
person (show proposed_shortfall_cents as "if confirmed"); state_basis = label.

| reclass | state_hint, strength | meaning |
|---|---|---|
| paid_before | done (work_now when paid twice), proven or likely | the same payer paid this line before; denied again, or restated (B13) |
| paid_before_part | work_now | the payer paid part of the units before; the rest is the size |
| other_payer_paid | done, or work_now when a share is left | another payer paid as primary; the size is what the primary left that no one paid. 'done' also when the denying payer paid part of the share as secondary and declined the rest (not when it asks for records: CO-16, CO-226, CO-252), when it says the claim is not its own and no other plan answered, or when a Medicare contractor says the claim is not in its jurisdiction (CO-109, N104) |
| other_medicare_contractor | work_now, likely | one Medicare contractor paid, the other denied: check which jurisdiction is right while the window is open; the size stays. NEW |
| unit_denied | work_now | another line of the code was paid; this line is the unit denied |
| overpaid | work_now | the same payer paid the line twice by its own reckoning (its allowed amount less the patient's share); possible overpayment, size 0 |
| partly_topped_up | work_now | a later payment covered part of a short payment |
| answered_same_day | done | the same remittance answered the line on another claim |
| dropped_on_correction | done | the practice's corrected claim (frequency 7 or 8, or the payer's CO-129/N770 note of its adjustment request) no longer bills the line, or bills fewer units and the payer paid all of them |
| recoded | done | the line was billed again under another code on the corrected claim, which was answered. NEW |
| moved_to_patient | done | the payer put the line on the patient's deductible, coinsurance or copay (after a reversal, or in a later answer of the same line); label "Allowed in full, on the patient" where the new answer allows the expected amount onto the patient |
| settled_by_prior_payer | done | the payer's new answer pays nothing and reports the prior payer's settlement (OA-23) |
| replaced | work_now | the payer reversed and answered again (rule 5), or a worked listing the payer answered again (rule 8, with new_reason, who_should_pay, what_to_fix): work the new answer |
| units_on_reanswer | work_now | after the reversal the payer paid fewer units than were billed; the rest is the size |
| reversed_no_answer | waiting | the payer reversed and has not answered again; a short payment taken back counts the whole line (taken_back_cents) |
| split_claim | work_now | MA15 halves together still short |
| secondary_paid | work_now, likely | a secondary paid and reports a primary payment the archive lacks |
| likely_paid | done, likely | a closure the archive cannot prove: never closes; check first |

Do not let states.py's own inference make an archive 'likely' reading DONE. On the real store, the snapshot's states.py
marks the $67,647 Q4316 item (6ac13f4fd0) done from its own "already paid by another Medicare contractor" reading; the
archive reads it other_medicare_contractor, likely, work_now (the contractor that denied took its payment back after an
audit, the other's payment stands; a person should confirm the jurisdiction before it is let go).

appeal_block (never appealed in bulk): paid_before, overpaid, answered_same_day, moved_to_patient, settled_by_prior_payer,
dropped_on_correction, recoded, other_medicare_contractor, likely_paid, and secondary_paid until checked.

## 4. Hide rules (unit C1: actions.py, api.py)

Unchanged from the builder's request (HIDE_DDL gains code, modifier, reason; _hide_matching uses archive_rules.hide_sql;
apply_hide_rules iterates archive_rules.read_hide_rules; /api/hide accepts and lists the three fields). One change in
archive_rules: hide_match now takes a code family from the item's code_category, else code_category(procedure_code), the
answered code, exactly as hide_sql does (the two disagreed on a recoded line). The real store has no rule at this grain yet,
so rule 9 is tested on invented data only.

## 5. The four signals rule 8 raises (unit B / INT: insights.py work types, story.py next_step)

DENIED_REFERRAL (PR-288), DENIED_COVERAGE_DATES (PR-27, PR-26, PR-200), DENIED_PLAN_EXCLUSION (PR-49, PR-204),
DENIED_CONTRACT_RATE (CO-147); category 'denial'. No file outside archive_rules.py knows them yet. Evidence carries
who_should_pay, what_to_fix, carc_group and basis; a Medicaid member is never billed (the wording says so). Sizes now follow
the Detector's own reading of the line (usual_rate through expected_allowed: the line's provider tier, place of service and
rank; times the units billed; never above the charge); a Medicaid plan is sized only from its own rates, else no size. One
listing per service: an older open listing of the same billed line from the same payer folds into the raised item, and a
worked one stays the listing instead. A coverage-date answer raises nothing once another plan answered the service as
primary since.

## 6. Ledger and closure requests (INT item 3: detect.py, unit B's code)

(a) Restated claims (high in the review). 631 non-reversal claims list more on their lines than the claim pays (by
$152,326; 236 paid $0), 606 of them with claim-level adjustments that account for the gap within $1 (OA-23, CO-B13 or
PI-B13, PI-22, PR-2 on a secondary). Detector.standing counts those lines at their listed amounts, so every detect.py rule
reads them as paid again. In _build_ledger, for such a claim set each paid line's standing to its own new money: the line's
listed payment less what the payer's latest earlier answer of the same line paid (same claim id and payer; the same line
control number, else the same charge; a line never answered before is all new), never below 0, when those add up to CLP04
within $1; otherwise share CLP04 in proportion to the lines' listed payments. Reference: archive_rules._restated_claims and
_line_increases (tested: test_a_restated_claim_nets_its_lines_to_what_it_paid,
test_rule4_a_restatement_that_raises_another_line_pays_this_one_nothing). 5 of 5,275 reversal claims take back more on
their lines than the claim ($395); 8 of the 606 restating claims were later reversed under the same claim id ($815), the only
places a reversal could be paired with a restated line.
(b) Reversal pairing. Pair a reversal with its payment by archive_rules._same_payer_loose (same normalized name, or the same
payer group unless it is a many-payer group, or the same payer id), never across two plans of one company
(archive_rules._two_plans: the claim filing indicators make them different kinds, a Medicaid plan and a Medicare Advantage
plan). 184 payment lines ($23,527) on the open items' bills still stand in Detector.standing after a reversal made under a
renamed payer.
(c) Claim-level payments. 140 claims pay more at the claim level than their lines list ($10,309; 104 as primary, 36 as
secondary). The ledger gives none of it to a line. Rule 2 counts it where the claim bills only the one service (a Medigap
paying the coinsurance at the claim level); detect.py reads those services as unpaid by that payer.
(d) _mark_paid (finding 22). It decides "no baseline" from the item's shortfall; after a proven $0 reclassification the
shortfall is 0 while expected_cents still holds the baseline, so a later closure loses it. Decide by expected_cents.
archive_rules keeps size_before_cents in the evidence meanwhile.
(e) OA-100 (payment made to the patient) and undated reversal lines: unchanged from the builder's request.

## 7. Rule by rule (final pass; listed = the items' sizes before the hook, new = after)

| # | rule (function) | hook | action | items | dollars |
|---|---|---|---|---|---|
| 1 | same_payer_paid_before | hook_archive_rules | reclassify paid_before, proven | 342 | listed $69,015 -> $45 (333 done, 9 work) |
| 1 | same_payer_paid_before | hook_archive_rules | reclassify paid_before, likely | 176 | $53,361 kept until a person confirms |
| 2 | other_payer_paid_first | hook_archive_rules | reclassify other_payer_paid | 265 | listed $37,279 -> $3,631 (132 done, 133 work) |
| 2 | other_payer_paid_first | hook_archive_rules | reclassify other_medicare_contractor, likely | 3 | $67,778 kept |
| 3 | units_split | hook_archive_rules | close_with_proof | 138 | listed $10,439 |
| 3 | units_split | hook_archive_rules | reclassify unit_denied | 173 | listed $17,220 -> $16,741 |
| 4 | short_pay_topped_up | hook_archive_rules | close 8, overpaid 1, partly_topped_up 3 | 12 | listed $423 -> $34 left |
| 5 | same_day_answers | hook_archive_rules | close_with_proof | 186 | listed $12,468 |
| 5 | same_day_answers | hook_fold | fold_into | 396 | listed $35,060 |
| 5 | same_day_answers | hook_archive_rules | reclassify done (moved_to_patient 209, answered_same_day 49, dropped_on_correction 72, recoded 24, settled_by_prior_payer 9) | 363 | listed $35,968 -> $0 |
| 5 | same_day_answers | hook_archive_rules | reclassify replaced 35, units_on_reanswer 2 | 37 | listed $2,944 -> $2,570 |
| 5 | same_day_answers | hook_archive_rules | reclassify reversed_no_answer (waiting) | 15 | listed $1,323 -> $1,477 |
| 6 | second_payer_share | hook_archive_rules | reclassify secondary_paid, likely | 1 | $81 |
| 7 | fold_repeat_denials | hook_fold | fold_into | 417 | listed $45,901; 97 carriers' deadlines moved to the first decision |
| 8 | pr_group_denials | hook_archive_rules | raise_item | 182 | $10,059 sized (122 plan exclusion, 21 referral, 35 coverage dates, 4 contract rate) |
| 8 | pr_group_denials | hook_fold | fold_into | 6 | listed $172 |
| 9 | hide_match / hide_sql | after save (actions.apply_hide_rules) | hide_match | 0 | no rule at this grain in the store |
| 10 | drug_expected | hook_expected | expected_cents | 49 | expected added $392,169 |

Against the pre-fix module (same store, same day): rule 8 raises 184 -> 182 ($11,263 -> $10,059 sized), rule 10 values 74 ->
49 items ($480,153 -> $392,169), open items 10,117 -> 10,118. Saved findings with the fixed module: 10,118 open,
$3,495,720 (work_now 3,958 $1,381,420; expired 5,197 $1,166,745; done 931 $74,679, of which $67,647 is the Q4316 item in
section 3).

## 8. Memory

Server mode, copy of the real store: 853 MB without the hooks, 867 MB with them (pre-fix module: 940 MB). Serverless
emulation on the EMA world (the eg2 store with the two A/R and two Production files read in; compact in-memory store,
indexes dropped, high-water mark reset after loading; EMA steps on, no errors): 1,725 MB without the hooks, 1,742 MB with
them (+17 MB). The review measured 1,723 MB and 1,805 MB (+82 MB) with the pre-fix module. Inside the hook the resident
size rises 55 MB while the archive is held and drops again when it is released.

## 9. Docs (unit F: README.md)

tests/test_docs.py::test_the_readme_layout_names_every_module_the_app_uses fails today: README.md's layout leaves out
archive_rules.py (actions.py and api.py import its hide functions now), with api_ema, api_results, api_uploads, archive,
ema_checks, ema_ledger, ema_reports and intake. archive_rules.py reads no setting and adds no command, so no runbook entry
is needed: one layout line, "remitwatch/archive_rules.py: what the 835 archive proves about an item (closures with proof,
reclassifications, repeat listings folded, PR-group denials raised, drug prices, hide rules at payer x code x modifier x
reason grain)".

## 10. Practice-manager cases (66 traced)

Against the run without the hooks, 15 cases are touched (C05, C07, C09, C10, C11, C12, C14, C26, C27, C50, C57, C59, C60,
C62, C64), none away from its verified truth. Against the pre-fix module two change: C14 (one listing; the raised
DENIED_REFERRAL is sized $50.32, the payer's own rate, where the pre-fix module listed the service twice) and C07 (the two
'likely paid before' items keep their $548.44 and $55.27 until a person confirms; the hint still says check first and the
worksheet's truth is "paid: close both").
