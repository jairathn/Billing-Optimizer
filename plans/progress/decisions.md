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
- [ ] Decision 3 built with tests (both store kinds). In progress.
- [ ] Decision 2 built with tests (both store kinds).
- [ ] Measured after; 20+ moved items hand-traced from raw rows.
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

## Next command

Write the decision 3 tests (tests/test_decisions.py), see them fail, then the code in archive_rules.py and detect.py.
