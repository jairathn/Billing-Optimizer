# fix7c: underpayment rules from the hand-check (progress)

Brief: `plans/briefs/2026-11-26_fix7c.md`. Branch `wip/fix7c`. Rules: `plans/briefs/2026-11-26_common.md`.

## Done
- 237951e (before this resume, committed as WIP when the agent stopped): rates keyed by plan (835 group number REF*1L or
  contract class REF*CE, `Detector._plan_schedules`, `_rate_group`), the PA/NP tier by the office's state (`np_state`,
  `_office_state`), a separate surgical site's schedule (`_fill_setting`, the facility-rate check in `usual_rate`),
  cross-claim multiple-procedure ranking (`_settle_mppr_candidates`, `_procedure_worth`), new-year services paid on last
  year's schedule (`_old_schedule_of`, `_new_schedule_starts`, `emit_old_schedules`, signal OLD_FEE_SCHEDULE, route
  `repricing` in states.py and story.py). Tests: tests/test_plan_rates.py (20, both store kinds).
- 2026-11-26 18:27 UTC: merged the integration tip fc73dec cleanly (71e7511). tests/test_plan_rates.py,
  tests/test_sizes_and_rates.py and tests/test_review_rates.py pass (85).
- The cached-array change (`_rate_line`, `_rate_lines`: one pass, an `array('q')` of amounts and a `bytearray` of
  components, read by `_plan_schedules` and `_build_yardsticks`) is whole: both read the same arrays, the old inline
  filter is gone, the line filter is unchanged (checked against the base's loop condition by condition).

- 540dffb: three regressions the first branch run showed on the world, each with a failing test first (both store
  kinds), broken on purpose and restored: (1) `_settle_mppr_candidates` + `_billed_same_day` + `_billed_lines`: a split day
  is ranked with the reducible procedures the 837s show billed on another bill with no answer on file (a larger repair
  makes the Mohs rightly second; a smaller one counts as paid in full, the least owed); (2) `_own_rate`/`_own_yard(exact)`:
  a plan's own rate is read for the line's own year only; (3) `_rate` falls back to the facility setting (EMA-only claims
  of codes paid only at the surgery center had no size once the 837 named the setting).

## In progress
- Measurement on the world (brief item 2): base = fc73dec, branch = this branch. Private folder
  `$S/tk/units/fix7c/` (mode 700): `base_tree/` (git archive of fc73dec), `audit/p2/world.sqlite` + `ema_ledger.sqlite`
  (one world copy at a time), `cycle_run.py` (the world tool, unchanged), `extract_findings.py`, `compare_runs.py`,
  `a16tools/` (A16 with `p2_analyze16w.py` patched to run without a pass-1 output).

## Left, in order
1. Base run on a fresh world copy: `F5_SRC=$U/base_tree P2_DIR=$U/audit/p2 P2_DB=world.sqlite python3 -I -B $U/cycle_run.py 2026-11-24`,
   then `python3 -I -B $U/extract_findings.py $U/audit/p2/world.sqlite $U/base_findings.sqlite`, then A16:
   `AUDIT_DIR=$U/audit python3 -I -B $U/a16tools/a16/p2_analyze16w.py $U/audit/p2/world.sqlite base`.
2. Delete the copy, copy the world again, the same with `F5_SRC=<git archive of the branch tip>`, tag `fix7c`.
3. `compare_runs.py base_findings.sqlite fix7c_findings.sqlite moves.tsv`; A16 `moves_w.py`, `headline.py`.
4. The bar: the 7 real underpayments (4 Humana MA Mohs + surgery-center repair pairs, 3 UHC MA January lines) by
   finding id; the 23 plan-rate cases (UMR plans, UHC commercial PA/NP by state, Anthem products and the surgical site)
   stay off. Hand-trace every item added or removed on Work now (or a stratified 60).
5. Memory and time of detection, base and branch (cycle_run's STEP lines).
6. Suites: tests of touched files, then the full suite in python3 and `$S/fix/venv_sqlite340/bin/python`; node checks.

## How to run
- `S=/tmp/claude-0/-home-user-Billing-Optimizer/96e81489-2ad2-5c70-991d-e00db2e22749/scratchpad; U=$S/tk/units/fix7c`
- Tests: `cd $S/tk/wt/fix7c && python3 -m pytest -q -p no:cacheprovider -o addopts="" tests/test_plan_rates.py`
  (unset SRFAX_, DROPBOX_, TRIZETTO_, SMTP_, MODMED_, ANTHROPIC_BASE_URL first).

## Measured
- Base fc73dec on the world (today 2026-11-24): detect 197.8 s, peak 1,054 MB; 16,632 items, 4,478 on Work now.
  A16 view W (in scope): correct 1,380; already done 160; already worked 27; duplicate 5; wrong amount 151; undecided 49;
  closed wrongly 1; money left on the table $117,240 (SHORT below_rate 190 services $12,945, to_patient 91 $3,205).
- First branch run (b6592c8 = 237951e + merge): detect 194.5 s, peak 1,063 MB; 16,564 items, 4,403 on Work now; 6 plans
  read apart (Aetna product, Anthem product and employer plan, Humana commercial product, one employer plan at both UMR and
  Wausau); UHC commercial PA/NP by state (KY 100%, IN 85%, 2024-2026); 109,942 lines took a setting from the 837 or EMA
  location (1,620 of them a facility); 62 split days where the Mohs was the highest-valued; 1 OLD_FEE_SCHEDULE (UHC MA 2026,
  18 E/M lines, $109.97 - the hand-check's January lines). Moves on Work now: 52 on, 128 off. Three regressions found and
  fixed in 540dffb (see Done). Second branch run in progress.

## Open questions
- (none yet)
