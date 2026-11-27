# fx-ema: F4, the EMA side and the bare $0 rule everywhere (progress)

Brief: `plans/briefs/2026-11-26_fixwave.md`, section "F4 the EMA side and the bare $0 rule everywhere". Rules:
`plans/briefs/2026-11-26_common.md`. Branch `wip/fx-ema` (worktree `$S/tk/wt/fx-ema`), from the integration tip 8c59195.
Private folder `$S/tk/units/fx-ema/` (mode 700).

The F4 section names no review finding by id: its items are the fix7e-c residuals (plans/next_fixes_2026-11-24.md
sections 2 and 7, plans/briefs/2026-11-26_fix7e.md) and rule 4.2's measure (decisions unit). No reviewer test in
`$S/tk/review/since-*` reaches these functions (searched for zero_answer, turnaround, status_class,
secondary_not_billed, blindness, self_audit, _paid_share, fold_payer_policy, rarely_paid, work_evidence, Medicaid
(other), C5/C6): the failing tests are written here, in tests/test_fx_ema.py.

## Items (status)
| # | Item | Owner function(s) | Status |
|---|---|---|---|
| A1 | bare $0 row: selfcheck blind-charge count and self-audit | selfcheck `blindness`, `_charges_since`, `self_audit` | not started |
| A2 | bare $0 row: turnaround | ema_checks `turnaround` | not started |
| A3 | bare $0 row: the ledger's status_class | ema_ledger `_part_a` (rw_status_class), ema_reports `status_class` | not started |
| A4 | bare $0 row: SECONDARY_NOT_BILLED | detect `run_secondary_not_billed` | not started |
| B | bare $0 row then the patient's money: a date | ema_ledger (posting dates of patient money) | not started |
| C | C5's patient, day and code check ignores the part | ema_checks `ema_denials` | not started |
| D1 | self-audit windows without plan facts, second payer's name as group | selfcheck `_windows` | not started |
| D2 | ema_checks' inside-the-window counts use the rule's days | ema_checks | not started |
| E | C6 ignores the copay | ema_checks `paid_in_ema` | not started |
| F | "Medicaid (other)" read as the state's Medicaid (C6, paid-before) | ema_checks | not started |
| G | "Already worked, waiting" routed to call (~20) | states `work_evidence`, A/R-note branches | not started |
| H | rule 4.2 measured per service; policy row count = items listed | detect `_paid_share`, `fold_payer_policy`; rarely_paid `applies`; api_ema policy rows | not started |

## Needs from others
(none yet)

## Measured
(nothing yet)

## Next command
Read the code of each item, write the failing tests in tests/test_fx_ema.py.
