# fx-ema: F4, the EMA side and the bare $0 rule everywhere (progress)

Brief: `plans/briefs/2026-11-26_fixwave.md`, section "F4 the EMA side and the bare $0 rule everywhere". Rules:
`plans/briefs/2026-11-26_common.md`. Branch `wip/fx-ema` (worktree `$S/tk/wt/fx-ema`), from the integration tip 8c59195;
the tip 59f6822 (plans only since) merged at bffe448. No fixer's merge (F1, F3a, F3b) had reached the tip when this was
measured. Private folder `$S/tk/units/fx-ema/` (mode 700): `measure.sh`, `compare.py`, `breaks.py`, `patches/`,
`explore*.py`, the snapshots and the hand-trace file.

The F4 section names no review finding by id: its items are the fix7e-c residuals (plans/next_fixes_2026-11-24.md
sections 2 and 7, plans/briefs/2026-11-26_fix7e.md) and rule 4.2's measure (decisions unit). No reviewer test in
`$S/tk/review/since-*` reaches these functions (searched for zero_answer, turnaround, status_class, secondary_not_billed,
blindness, self_audit, _paid_share, fold_payer_policy, rarely_paid, work_evidence, Medicaid (other), C5/C6), so the
failing tests are written here: `tests/test_fx_ema.py`, 13 functions, 25 cases (both store kinds where the code runs on
both). All 25 fail on the tip (59f6822) and pass on the branch. Each fix was broken on purpose (`breaks.py`, 15 breaks):
every break fails its test, every file restored byte for byte.

## Items (status)
| # | Item | Function(s) changed | Status |
|---|---|---|---|
| A1 | bare $0 row: selfcheck blind-charge count and self-audit | selfcheck `_charges_since`, `_Facts` (answered = `ema_checks.line_answered_sql`, first answer = `ema_checks.first_answer_days`) | done |
| A2 | bare $0 row: turnaround | ema_checks `turnaround` (charge to the payer's first real answer, from the store's lines, which hold every ledger line; `first_answer_days` new: the never-answered rule's predicates `answer_row_sql` + the payer's reasons) | done |
| A3 | bare $0 row: the ledger's status class | ema_ledger `_part_a`: `rw_status_class(status, line_answered_sql)` (Resolved/Other on a bare row is gave_up) | done |
| A4 | bare $0 row: SECONDARY_NOT_BILLED | detect `_ema_lines_for` (only `run_secondary_not_billed` calls it): a second payer's EMA payment row answers only with `answer_row_sql`'s proof | done |
| B | bare $0 row then the patient's money: a date | not done (see "Left") | left |
| C | C5's patient, day and code check ignores the part | ema_checks `ema_denials`: `_name_day_codes_835(with_parts=True)` + `line_parts` + `part_fits` | done |
| D1 | self-audit windows: plan facts, a second payer's name read as its group | selfcheck `_windows(**facts)`, `_appeal_end` (new: the payer's window from the answer with its own limit; Kentucky Medicaid's corrected-claim window where it takes no appeal), `_second_end` (new: the second payer's group by EMA's name rules and `codes.second_claim_window`, as the product dates SECONDARY_NOT_BILLED); every `_cov_*` passes the facts. The audit stays independent of the items' own dates (an item closed or expired wrongly must still show as a gap: test_selfcheck's own test) | done |
| D2 | ema_checks' inside-the-window counts (C7, C9) | ema_checks `_still_recoverable` (the payer's name and reason as plan facts, the payer's own limit, `codes.rule_at`; a line an item covers by the item's own last day: `_finding_line_deadlines`, new) | done |
| E | C6 ignores the copay | ema_checks `_outcome_of` (`patient_paid`, `patient_cover`), `paid_in_ema`: a later payment plus what the patient paid on the line reaches the threshold (never for a short payment) | done |
| F | "Medicaid (other)" read as the state's Medicaid (C6, C5's same-day, the outcome's before/after split) | ema_checks `_catch_all_company`, `_same_payer_named` (new, fix6's reading: `states.MEDICAID_CATCH_ALL`), `_payer_matcher` (takes the EMA payer name), `_outcome_of`, `ema_denials` | done in ema_checks; states.paid_proof's own branch is F1's (Needs) |
| G | "Already worked, waiting" routed to call | not done (see "Left"): 56 items measured | left |
| H | rule 4.2 per service; policy row count = items listed | detect `_paid_share` (per service: patient name key else bill, date, code, part; paid when a payment stands on any answer or EMA shows the payer's money), `_share_patients`, `_share_ema_paid` (new); ema_checks `ema_denials`' EMA fallback per service; api_ema `_listed_policy_items` (new), `_payer_policies` | done |

Two older tests changed so their fixtures meet the bare $0 rule (their intent kept): tests/test_ema_wiring.py
`test_a_second_payer_whose_only_answer_is_in_ema_was_billed` (the second payer's EMA-only $0 answer now carries its
allowed amount); tests/test_selfcheck.py `test_blind_dollars_leave_out_claims_ema_shows_answered` (the line "with no
answer" now has none of an answer's figures, not only ins_answer_rows = 0).

## Needs from others
- F1 (states.paid_proof, the outcome's paid-before branch): `same = _same_payer(_grp_kind(pb.get("payer"))[0], denier)`
  reads a payment EMA posted under "<company> Medicaid Community Plan" as the state's Medicaid (the catch-all). Use
  `ema_checks._same_payer_named(_grp_kind(pb["payer"])[0], pb["payer"], denier)` there (the reading ema_checks now uses
  for C6). Test to add with it: an 835 Medicaid KY denial, EMA showing a payment posted under a company's Medicaid
  Community Plan name before the denial with the line settled: paid_before_other_payer (today: neither rule fires).

## Measured
(see the bottom section, written when the measurement ends)

## Left (not done in this round, with what is known)
- B (bare $0 row followed by the patient's money): on the tip's run 6 never-answered items carry the note (Tricare 2,
  VA Community Care 1, SIHO 1, Anthem 2; none of them has an EMA link in its evidence: they come from the 837 and exports
  passes). The ledger keeps each patient posting's date (postings.posted), but the store's EMA lines keep only the sums
  (pat_paid_joined), so the note cannot say whether the money came after the $0 row. The change: a column on ema_lines
  (the newest posting day of the patient's money on the line, own and joined) in ema_reports' DDL with its migration,
  filled by ema_ledger `_part_a` (pa_agg / pa_pat), read by ema_checks `_patient_cents` and said by `bare_zero_note`; then
  the rule (money after the row that settles the balance = the payer put it on the patient) is the owner's call, written
  in plans/owner_notes. Not started.
- G ("already worked" routed to call): on the tip's run 56 Work-now items ($29,975.16) have the route call because an A/R
  billing note already on the first A/R (its own date unknown) followed a long silence from the payer (`states._follow`,
  `work_stale`): Humana MA Medicaid DENIED_REFERRAL 14, Anthem CLAIM_DENIED_UNPAID 6, Medicare KY medical necessity 4, UMR
  6, Cigna 3, others. Note kinds: call or portal check 20, resubmitted 15, appeal 10, appeal and call 5, call and
  reprocessing 4, others 2. The hand decision from the raw A/R notes and 835s has not been made; next: trace each with
  `$S/tk/units/fx-ema/explore2.py`'s item list, then set the rule in `states._follow` / `_note_or_follow`.

## Next command
Measure: `KEEP_WORLD=1 bash $S/tk/units/fx-ema/measure.sh $S/tk/units/fx-ema/brtree br1` (tip: `... tiptree tip0`, done),
then `python3 -I -B $S/tk/units/fx-ema/compare.py snap_tip0.sqlite snap_br1.sqlite moves1.json`.
