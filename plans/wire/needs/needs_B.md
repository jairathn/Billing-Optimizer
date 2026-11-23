# needs_B.md: what unit B (detection wiring) needs from other units

Final version. Unit B owns remitwatch/detect.py (but not A1's last line of ensure_schema), actions.py, insights.py,
alerts.py, codes.py, report.py, the wiring edits in ema_checks.py, tests/test_ema_wiring.py and its edits in
tests/test_ema_checks.py, test_insights.py, test_workable.py and review6/test_r6_cov_docs.py. No identifiers below.

## 0. Fix B (final, 2026-11-23): what the B fixes need from other units

### 0a. Unit D (states.py, story.py): exact, from the B fixes

1. REMIT_NOT_POSTED with `evidence.informational == true` (an informational 835, BPR04 'NON' or a $0 deposit, whose money
   never came: PM case C54) is a payment to ASK THE PAYER FOR, never "post it". states.py `_decide`, the branch
   `if sig == "REMIT_NOT_POSTED"` (around line 1594): when `_ev(f).get("informational")`, basis
   "The payer's informational remittance reported this paid, but no payment came with it or after it: ask <payer> for it"
   (rule 'payment_never_arrived'); story.py next_step route (around line 1646 `if rule == "post_payment" or sig ==
   "REMIT_NOT_POSTED": return "post"`): return "call" for that evidence flag, and the verdict sentence (around line 1216)
   "The 835 reported it paid on an informational remittance; no money came" instead of "EMA never posted it".
2. A person who reopens an automatic EMA closure: detection now passes that item's outcome to states_of as
   `dict(o, outcome="open", person_reopened=True, rejected_payment=o["first_paid_after"], paid_after_same=0,
   paid_after_other=0)`. Please show the rejected payment as a conflict in the basis, the way in_progress shows one
   ("EMA shows $X by <payer> on <date>; you reopened it"), and keep the state work_now (never done, never appeal_block
   'paid'). The general case (a person's 'open' after any automatic closure, 835 ones too) is yours.
3. FORWARDED_TO_CARRIER now carries an appeal_deadline (the forwarding payer's window from its remittance) and a
   legal_deadline. states.py `_decide` returns at the forwarded branch (`fwd = _forwarded(fx)`) before the "past every
   window" check, so these items never expire: run the expiry check first (or inside that branch) so a forward from 2024 is
   expired, not "ask the carrier for it" (121 items on real data; test_gates_b.py test_a_claim_forwarded_to_another_carrier).
4. Review-type money items from a remittance (DENIED_NONCOVERED, DENIED_TIMELY_FILING, DENIED_DUPLICATE_UNPAID,
   DENIED_OTHER, CLAIM_DENIED_UNPAID, DENIED_PROVIDER, DENIED_MISSING_PRIMARY) now carry their own appeal_deadline (the
   payer's window from the remittance), so `_deadline(f)` no longer needs its fallback for them; deadline_basis ends
   "Counted from the remittance with this answer: past it, the payer takes no dispute of it".
5. legal_deadline is now set (later than the safe date) for: corrected-claim items where the payer's dispute window outlasts
   its corrected-claim limit (Humana Medicare Advantage: 18 months from the determination); appeal/review denials a
   corrected claim can fix at payers with a corrected-claim rule (Anthem and Humana MA: a year from the date of service;
   signals in detect.CORRECTABLE_APPEAL_SIGNALS); G8 NO_ANSWER claims charged on time (filing limit + appeal window). Your
   `_last_window` already reads it; with decision 1 (no grace) the item expires the day after it.
6. EMA_DENIED items valued at "the balance left when this payer denied it, after the first payer's payment" now come with
   `outcome.paid_before = 0` and `outcome.first_payer_paid = <that payment>`: the first payer's payment is the share the
   second payer was billed for, never a "paid before" proof (paid_proof's `pb` branch must not call these done).
7. SECONDARY_NOT_BILLED can now come from the 835 alone when EMA's line is not in the store (`evidence.ema_line_on_file ==
   false`, `crossover == true`, the carrier the remittance names): the next ledger refresh keeps the line (its bill now has
   an item) and the next run judges it on EMA's line. Medicaid second payers are never listed (counted in ema:wiring
   `snb_medicaid_second`), and a second payer that answered at any amount ($0 included, 835 or EMA) is never listed.
8. CREDIT_BALANCE evidence now splits the credit: `insurance_over_cents`, `patient_over_cents`, `overpaid_by` ("<payer>",
   "the patient", "<payer> ($x) and the patient ($y)" or "check who overpaid"), `patient_share_835_cents`. The Medicare
   60-day deadline is set only when insurance overpaid, for that part. Please word the state and next step from these
   (refund the patient's part to the patient, the payer's part to the payer).
9. A C6 closure whose line EMA shows paid before the denial and left in credit by the later payment is now closed with
   `recovered_cents = 0`: `evidence.paid_later.how` = "already paid before this denial; the later payment looks like a
   duplicate", auto_note "Already paid: EMA shows this line paid ...". Story: say it was already paid and that the duplicate
   is worked on the line's credit item (ema:wiring count `c6_closed_duplicate`).
10. New hints (detection sets `hint`, never a closure):
    - 835 money items: "EMA shows $X paid by <payer> on another of its claims for this patient, date and code: check
      whether that paid this service (a resubmission, or the service billed twice) before working it" (paid_in_ema action
      `hint_paid_elsewhere`, count `c6_hint_paid_elsewhere`). Please treat it like the "Possibly paid on claim" hint
      (check before appealing).
    - NOT_SENT and NO_ANSWER: "EMA shows $X paid by <other payer> on another of its claims for this patient, date and
      code: check whether this claim is still owed". When the claim's own payer paid every line on another claim, the
      item is no longer made at all.
11. UNDERPAID_VS_OWN_RATE with `evidence.highest_valued == true` (count `mppr_highest_cut`): the payer cut this procedure
    as a second one although its own ranking of the pair puts it first (PM C28). Next step: a payment dispute saying the
    reduction belongs on the lower-valued procedure (`evidence.note` has the sentence).
12. Duplicate copies (C12): a whole-claim denial of a copy (OA-18, or any denial on the same remittance under the same
    claim id) that the same payer processed to the patient is no longer made. Nothing to do in D; the C12 item goes away.

### 0b. Unit C1 (tests you own that the B fixes change)

1. Done by C1 at 01:38: tests/test_desk.py (fixture `_client`) and tests/test_review7.py now make their CO-29 / CO-96
   item undated themselves, since detection dates every money item from its remittance (finding 19, PM C63). Both pass,
   and tests/test_claims.py with them. Nothing left here.
2. Not from B, seen in the full suite: tests/test_fax.py::test_select_all_then_appeal and
   test_prepare_send_and_fax_cycle_commands fail because states.py now gives the DENIED_MEDICAL_NECESSITY item
   `appeal_block='check_first'` (it carries the 835 hint "Possibly paid on claim ..."), so bulk appeal leaves it out
   (fax unit and Fix D to agree which is right). tests/test_selfcheck.py's grace test fails on Fix D's no-grace change.

### 0c. Unit C2 (Data tab, policy page)

1. New meta keys detection writes: `ema:c5_left_out` {at, <rule>: {n, cents, by_reason}, payer_policy: {n, cents,
   patterns}} (EMA-only denials a rule left out, never a silent drop); `ema:unanswered_unseen` {n, billed_cents, due_from,
   due_to, overdue_30, overdue_30_cents, by_group, at, newest_835, why} (unanswered claims no source can check yet);
   `ema:payer_policy` {at, patterns: {"<group>|<code>||payer_policy": {group, code, paid, answered, n, cents, decision,
   default, source}}}.
2. `ema:policy_summary` rows now include kind `payer_policy` (pattern "<group>|<code>||payer_policy", decision 'accept'
   until the owner picks 'pursue' through `ema_checks.set_policy`): codes a payer pays on 5% or less of at least 20 lines
   it answered as primary since 2025. Their 835 and EMA-only items are folded out of the work until 'pursue'.
3. `ema_writeoff_evidence` can now be 'rule_ignored' (EMA's ERA-lines export says its rule ignored that reason): not an
   automatic write-off.
4. New counts in `ema:wiring.counts` worth a line on the Data tab: `co45_contract_remark` (whole-charge CO-45 lines a
   contract remark explains, 1,306 on real data), `co45_answered_elsewhere` (43), `payer_policy_835` (579 remittance
   denials folded into payer-policy patterns), `snb_medicaid_second` / `_cents`, `c6_hint_paid_elsewhere`,
   `c6_closed_duplicate`, `mppr_other_claim`, `mppr_highest_cut`.

### 0d. SWEEP / unit A1 (ema_reports.py, ema_ledger.py)

1. Keep the EMA lines (and their payments and reasons) of any claim with a second payer on file or a crossover, whatever
   its items: SECONDARY_NOT_BILLED judges the second payer from EMA's line; without it, detection now makes a
   remittance-only item on the first cycle that converges only after the next refresh keeps the line (680 such items on a
   first cycle, 33% precision; after one refresh they become EMA-line judgments).
2. Keep the lines of any claim that shares (patient key, date, code) with a line the ledger keeps: the "paid on another of
   EMA's claims" checks (C5 step 4, the new C6 hint, NOT_SENT and NO_ANSWER) read ema_line_payments and cannot see a
   payment the lean copy dropped (1 of 6 real cases).

### 0e. INT and unit B2 (archive rules hook)

1. Wire B2's `run_on_detector` in `hook_archive_rules` (needs_B2): PM cases C27 (split claim), C50 (Medicaid PR-288),
   C57 (CO-129 processed correction), C58 (surgery-center repair benchmark) and C61 (older determination) wait on it.
2. Re-measure every gate-1 class against truth/g1_final after the hook: B's classes are measured below (report_B.md).

## 1. Unit C (api.py and the pages)

1. `api.FINDING_COLS` (yours): add the columns detection now writes (`detect.EXTRA_FINDING_COLS`):
   `written_off_source, written_off_kind, written_off_when, ema_writeoff_evidence, ema_link, ema_status, ema_status_src,
   ema_as_of, ema_balance_cents, ema_worked_on, ema_worked_how, ema_outcome, ema_outcome_on, source, work_state,
   follow_up_on, state_basis, appeal_block, legal_deadline`.
2. Deadlines: `appeal_deadline` stays the safe (earlier) date and drives every view. `legal_deadline` holds the payer's
   own limit, and only when it is later than the safe date (else NULL). Show it beside the due date as "Payer's limit".
3. Bulk "Appeal everything appealable" must take only:
   `status='open' AND COALESCE(stale,0)=0 AND work_state='work_now' AND appeal_block IS NULL AND actionable='appeal'
   AND appeal_deadline >= :today AND category <> 'refund' AND signal NOT IN ('NO_ANSWER','REJECTED_PRE_PAYER','NOT_SENT',
   'REMIT_NOT_POSTED','SECONDARY_NOT_BILLED','CREDIT_BALANCE','FORWARDED_TO_CARRIER','EMA_DENIAL_AS_CONTRACTUAL')`.
   `appeal_block` is states.py's reason ('paid', 'credit', 'reprocessed', 'waiting'); show it on a left-out row.
4. desk.js `NEVER_HIDDEN` mirror: add `NOT_SENT`, `REMIT_NOT_POSTED`, `SECONDARY_NOT_BILLED`, `CREDIT_BALANCE`
   (actions.NEVER_HIDDEN has them; tests/test_workable.py checks the page's set stays inside it).
5. Work types: `insights.WORK_TYPES` is now fix, auth, note, rule, rate, chase, bill, post, check ("bill" =
   SECONDARY_NOT_BILLED, "post" = REMIT_NOT_POSTED). New category `refund` (CREDIT_BALANCE): money owed back, never in a
   "money to recover" total. `EMA_DENIAL_AS_CONTRACTUAL` has no expected or shortfall: its dollars are in
   `evidence.written_off_cents` and stay out of every money total.
6. C2: an item whose `evidence.written_off_basis` is `charge_less_paid` carries an upper bound (detect could not price
   it): keep it out of dollar totals or label it.
7. Meta keys for the Data tab: `ema:wiring` = {at, on, counts, errors} (show the errors when not empty; with
   REMITWATCH_EMA_STRICT=1 a failing step stops the run instead), `ema:policy_summary` = {at, patterns[...]} for the
   owner's write-off policy table; the decision route calls `ema_checks.set_policy(conn, pattern, 'accept'|'pursue', user)`.
8. Filters worth adding: `ema_outcome`, `work_state`, `source` ('835' or 'ema').
9. Held EMA reports: `actions.apply` now takes the journal kind `ema_report_decision` {file_id, decision ('apply' or
   'discard'), actor, at} and calls `ema_reports.decide`. On a server the route can call `decide` itself; on Vercel journal
   it. For a file `ema_ledger.hold_slow_file` held, the route must also call `LedgerHandle(state).clear_attempt(file_id)`
   (needs_A1 section 1; actions.apply has no state folder).

## 2. Unit D (states.py)

1. Detection calls `states.save_response_days(conn)` once per run, then `states.states_of(conn, items, outcomes=...,
   today=...)` once, and stores work_state, follow_up_on, state_basis (first 300 characters) and appeal_block.
2. Field names you asked for: `appeal_deadline` (safe date) and `legal_deadline` (the payer's own limit, only when later).
3. Explicit rules wanted for the new signals: SECONDARY_NOT_BILLED (work now until the second payer's limit; detection
   already holds back a crossover line for 45 days after the first payer's payment), CREDIT_BALANCE (work now; a Medicare
   credit is due back 60 days after it is found), FORWARDED_TO_CARRIER (waiting until 45 days after the remittance; on real
   data 98 of 219 read "done", please check why), EMA_DENIAL_AS_CONTRACTUAL (an owner decision; "accepted" once the
   pattern's policy is accept).
4. "Waiting" is 0 on real data: one A/R pull, no note text, and EMA work dated by the first pull reads as stale. Expected
   to change with a second pull; worth a test with two pulls.
5. In a spot-read of 25 automatic write-offs, 3 items past their safe date read "work now" (inside the payer's own
   limit, I assume). Please confirm that is the rule.

## 3. Unit B2 and the hook agent (detect.py hooks)

1. `Detector.run` has four no-op hooks: `hook_expected` (after the claim rules), `hook_archive_rules` (now between
   `emit_second_rates` and `resolve_elsewhere`, as needs_B2 section 1 asks), `hook_fold` (before save) and `hook_hide`
   (after the hide rules). The hook agent replaces the body of `hook_archive_rules` with
   `from .archive_rules import run_on_detector; self.archive_summary = run_on_detector(self)`. If run_on_detector does it
   all, the other three hooks can go.
2. NOT done by unit B in this push: needs_B2 section 3, hide rules by payer x signal x code x modifier x reason in
   actions.py (HIDE_DDL, _hide_matching, apply_hide_rule, apply_hide_rules). It changes the key that api.py's /api/hide
   (unit C) and the journal replay read, so it should land in one step with C's change. archive_rules.hide_key, hide_sql
   and read_hide_rules are ready for it.
3. Rule 8 signals have labels and work types in insights.py: DENIED_REFERRAL (auth), DENIED_CONTRACT_RATE (rate),
   DENIED_COVERAGE_DATES and DENIED_PLAN_EXCLUSION (check). CARC 288 is in codes.py.
4. Practice-manager cases the archive rules could close (not detection's): C27, a split claim (MA15) paid on its other
   half, still reads "work now"; C57, a CO-129 status-4 record next to a same-day correction (the correction outranks it).

## 4. Unit A1 and the EMA ledger's owner

1. The store keeps 70,285 of about 204,000 EMA lines, and routine and patient-share reasons only for lines whose bill has
   an item. So SECONDARY_NOT_BILLED cannot see 392 of the answer key's 517 lines (no EMA line in the store), the
   answer key's open-balance lines are out of reach for the same reason, and a Medicaid PR-group denial (C50) cannot be
   seen. Ask: keep the lines with a second payer on file and a first payer's payment, and PR-group reasons on Medicaid
   lines (measure the size first).
2. The resubmission check added to C5 (paid on another of EMA's claims) reads ema_line_payments, which is lean too: the
   practice-manager case C40 stays a C5 item.
3. Server cycle: run `ema_checks.turnaround(conn, ledger=lg)` after the refresh; detection reads it from meta.
4. Serverless memory, measured again with the final B code on the compact image the way cloud.load_raw holds it (bytes ->
   Store.from_bytes, 580 MB image of the real world): 1,182 MB high-water after loading, 1,530 MB peak during detection
   (same in both runs of 2026-11-23), under the 1.75 GB gate with about 260 MB to spare. The earlier 2.4 GB figure came
   from an emulation that loaded the 830 MB server file; please keep watching the margin as the store grows.

## 5. Unit F (docs)

1. Name `REMITWATCH_EMA_STRICT` (read in detect.py: 1 makes a failing EMA step stop the run; by default the step is
   recorded in meta `ema:wiring` and the run goes on) in docs/SERVER_RUNBOOK.md and docs/VERCEL_SETUP.md.
   tests/test_docs.py fails on it until then, together with other units' new settings, modules, page script and command.

## 6. The EMA checks' owner (if anyone takes ema_checks.py after B)

1. Two practice-manager cases of 835 money never posted (C54, C56) are not C4 items. Worth a look at the C4 population.
2. Payer group names split in EMA's own names: "UHC Medicare Advantage" beside "UnitedHealthcare Medicare Advantage" and
   "Anthem Medicare Advantage" beside "Anthem / BCBS Medicare Advantage" in the write-off patterns. Pattern counts and
   the owner's policy split across the two names.
