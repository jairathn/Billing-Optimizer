# Unit decisions: progress (the owner's decisions 2 and 3; decision 4 is another unit's)

Brief: `plans/briefs/2026-11-26_decisions.md` (sections "Decision 2" and "Decision 3" only). Rules:
`plans/briefs/2026-11-26_common.md`, `CLAUDE.md`, `plans/HANDOFF.md` section 3b.

Branch `wip/decisions`, based on the integration tip 1867f60 (nothing to merge on start).

## State (updated as work goes)

- [x] Read the code paths: rule 8 `archive_rules.pr_group_denials`/`_pr_actions`/`_pr_words`; rule 3 `units_split` (its
      `switched` closure sets counts_as_recovery False, apply_actions then writes recovered_cents 0); detect.py
      `_close_by_answer` (settled as secondary: already records what the payer paid, capped at the item's size);
      states.py `_decide`/`route_of`; story.py `_next`/`_bill_patient_step`; api.py `/status` (`_person_status`, seen_status,
      ZERO_PAID_BLOCKS); results.py (attribution reads payment records, not recovered_cents).
- [x] Baseline on a private world copy (`$S/tk/units/decisions/w/world.sqlite`, the base tree exported to
      `$S/tk/units/decisions/src_base`, `cycle_run.py 2026-11-24`, 216 s). Snapshot: `$S/tk/units/decisions/base_snap.sqlite`.
- [x] Decision 3 built with tests (both store kinds): commit 61adb76. archive_rules.owed_in_second_role and
      second_role_words; rule 3 (units_split) counts a later payment in the other role as recovered and keeps what is
      left listed (reclass second_role_short, evidence second_role); detect.py _second_role_due/_second_role_short in
      _close_by_answer; states._second_role_short (work now until the window to dispute that answer closes, route call);
      story._call_step's step. Results already attributed the payment: test locks it.
- [x] Decision 2 built with tests (both store kinds, both hosts): commit c432cd6. archive_rules PR_REASONS/_pr_words/
      patient_share_of (size = the patient's share, payer rate kept as payer_rate_cents); states patient_denial,
      patient_share, rules bill_patient_denial / billed_patient / patient_unpaid / patient_paid / person_done (before the
      payer's windows expire anything); story _bill_patient_denial_step and the wait step; api /status 'done' (strict
      true, DONE_ONLY for other items); features.js buttons billed_patient and mark_done; results STATUS_NOTE_KINDS and
      closures done / patient_paid; docs/OPERATIONS.md daily routine step 3.
- [x] Measured after (first after run, branch c432cd6): 195 items moved, all of them decision 2 or 3, none other. One
      of them showed a fault: a PR-204 item the base closed as settled by the patient (EMA: the patient paid part, the
      rest taken off the patient's balance, line closed) came back as "Bill the patient" for the whole share, because the
      half-of-it test now compared with the patient's share. Two items where the patient had paid part were asked for the
      whole share again.
- [x] Fixed with tests first (commit 3eb4036): states.patient_share gives patient_paid and left; settled_in_ema counts
      what EMA took off the patient's balance for a patient-group denial; paid in full is done (patient_paid); part paid
      bills the rest; Billed the patient closes only on the patient's side (patient_side_settled), never on a write-off.
      Each part broken on purpose and restored (states, api strict Done, Done for other items).
- [ ] Second after run (branch 3eb4036) on a fresh copy; 20+ moved items hand-traced from raw rows (tools/hand.py).
- [ ] Suites (touched files, then the full suite in python3 and 3.11.1).

## Design (decided while reading; see the open questions)

Decision 3:
- Archive rule 3, a payment in the other role (denied as primary, paid as secondary) AFTER the item's answer: a recovery
  (counts_as_recovery True). A payment the same day as the denial stays no recovery (it came with the answer).
- What the payer owes in that role: its allowed amount on that answer (else the item's expected), never above the charge,
  less the other payers' standing payments on the service (on file), else less the prior payer's answer as the payer itself
  applied it (OA-23), less the patient's share it set (PR). Nothing tells what the other payers paid: no remainder is sized
  (never a guessed one). A remainder above $0 keeps the item listed (reclass `second_role_short`, its size the remainder).
- detect.py `_close_by_answer` as secondary: the same rule; a remainder keeps the item open at that size.
- Results: the payment record is attributed like any other (test locks it).

Decision 2:
- Rule 8 PR items (PR-288, 27, 26, 200, 49, 204 with group PR; never CO-147) go to the route `bill_patient` with the step
  "Bill the patient $X", X = the PR the payer set on the line, never more than the charge less what any payer paid.
- Not expired by the payer's windows (billing the patient has no payer deadline), as the existing H2 bill-the-patient
  route already is.
- "Billed the patient": status Sent with the note "Billed the patient..." (one request); the item waits for EMA's patient
  payment (follow-up after PATIENT_WAIT_DAYS; every waiting item needs one, selfcheck followup_date), done when EMA shows it.
- "Done": status Paid with $0 and the note "Done...", allowed only for these items, the flag strict JSON true.

## Numbers measured so far (world of 2026-11-24, today 2026-11-24)

Baseline (base tree):
- 16,632 items. Rule 8 PR-group items: 188 (48 work now $3,156.28 at the usual rate, PR on their lines $8,770.97;
  1 waiting; 46 done (settled by the patient); 93 expired (PR $6,911.90: EMA linked 53, of them 41 written off
  non-contractual, 6 still open $569.63; 40 not linked)). CO-147: 12 work now $349.29.
- Rule 3 closures in another role after the answer: 12 items, $295.56 arrived, recorded $0. Same-day: 3 items $77.96.
- Settled as secondary (detect): 32 items (31 whole claims, mostly Medicaid KY with no primary remittance on file);
  14 paid $258.66 already recorded; by the formula above 0 remainders (1 more only if the item's expected stood in for an
  unknown primary payment: not sized).

## Open questions

- Medicaid: the brief says a patient-group reason from Medicaid follows the same rule (bill the patient). Federal Medicaid
  rules limit billing a member (payment in full); the owner should confirm for PR-288 from a Medicaid plan.
- The 93 expired PR items come back to Work now under "not expired by the payer's windows": confirm the owner wants them.

## Tests so far

- tests/test_decisions.py (35, both store kinds and both hosts) pass; test_archive_rules (3 rule 8 tests updated to the
  decision), test_states, test_story, test_settled_elsewhere, test_837_sends, test_results, test_docs pass in python3.
- test_hotfix2.py: 9 failures that fail the same way on the base tree (1867f60), so not this unit's: the three H6 note
  tests, H5 json true, H3 list, and four H2 fax tests (all date-bound fixtures, it seems).
- node tests/calc_model_check.js (79 ok) and tests/lab_review_check.js pass; features.js parses.

## Next command

The after run: `cd $U && F5_SRC=$U/src_after P2_DIR=$U/w P2_DB=world.sqlite python3 -I -B $U/tools/cycle_run.py 2026-11-24`
(U = $S/tk/units/decisions, src_after = the branch exported by git archive), then
`python3 -I -B $U/tools/fullsnap.py $U/w/world.sqlite $U/after_snap.sqlite` and
`python3 -I -B $U/tools/diff_ba.py $U/base_snap.sqlite $U/after_snap.sqlite $U/moved.json`, then hand-trace 20+ moved
items with `$U/tools/one_item.py`.
