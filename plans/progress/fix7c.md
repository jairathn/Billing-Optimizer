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
- (running)

## Open questions
- (none yet)
