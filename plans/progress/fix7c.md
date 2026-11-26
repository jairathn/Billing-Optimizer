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

- 1d16cda: a split day reads the other claim's 835 answer where the day's patient match misses it (the 837's bill,
  member id and NPI find it); a procedure of the day the payer denied or took back ranks nothing.
- 98b55a6: a claim with no remittance (EMA's C5 sizing through `_rate`) is sized by the code's typical amount across
  settings when no one setting has enough lines (the run-3 EMA_DENIED with no size).
- (this commit) `_every_procedure_cut`: a split day on which the payer paid no procedure as the first (the Mohs and the
  surgery center's repair both at half) is owed one missing half, listed on the Mohs line at the least of the two halves.
  The world showed 7 such days with no item (5 Mohs + 14041, 2 Mohs + 14301; about $2,000), because the repair's
  facility rate rests on 1 to 3 lines (not an item of its own). Tests: 2 new (both store kinds), each guard broken on
  purpose and restored (the cut check, the item-of-its-own check, the whole rule).

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

- Second and third branch runs: regressions found and fixed (1d16cda, 98b55a6).
- Fourth branch run (98b55a6): detect 227.4 s, peak 1,064 MB (machine shared with other units; base 197.8 s, 1,054 MB;
  runs vary 194 to 227 s with the load). 4,398 on Work now (base 4,478): 48 added, 128 removed.
- A16 view W on run 4 (in scope): correct 1,351 (base 1,380); wrong amount 171 (151); undecided 51 (49); money left on
  the table, short below the rate, 172 services $7,537 (190, $12,945). The changes A16 sees: 19 Humana MA Mohs units and
  2 UHC units it called owed are now listed; 3 Anthem 99213 units at a plan's own rate leave the list. A16's 20 new
  "wrong amount over 25%" are the Humana split days: A16's truth is the Mohs's gross half ($292.56 or $274.22) and does
  not net what a correct ranking takes back from the repair paid in full; the branch's net is the money the payer owes
  for the day (hand-traced, 12 of 12 right). A16 cannot judge OLD_FEE_SCHEDULE (no unit reference).
- The bar. UHC MA January: all 18 January 2026 visit lines UHC MA paid on its 2025 schedule with a payment are in one
  repricing item, 58e4941184a9954aa7ea58a1 ($109.97: 99203 x3, 99204 x1, 99213 x12, 99214 x2, physician and the
  85 percent tier both checked against the 2025 and 2026 amounts); the base listed 6 of them as one-offs (absorbed) and
  missed 12. The 6 other January lines at 2025 amounts paid $0 (deductible): the patient's share, rightly left out.
  Humana MA split days with a surgery-center claim, 2026: 14 of 27 halved 17311s and 11 of 12 halved 17313s carry an
  item; of the 14 without: 6 rightly second (the repair worth more at its facility rate: 14041, 14061, 14301, 15260 in
  full), 1 nets under the threshold ($14.79), 7 both cut (now caught by `_every_procedure_cut`, run 5 to confirm).
  The hand-check's own 4 finding ids are not on file in this container; they match the description of the listed days.
- Hand-trace of a stratified 60 of the 176 moves (12 Humana MA added, 6 other added, 42 removed across Anthem, Wausau,
  UHC, UHC MA, UMR, Humana, by signal): 57 right, 1 right with an amount question, 2 doubtful (below). Removed: the
  surgery center's facility rate (Anthem repairs, 11402s, 99204-57, 12031; 5 MPPR_EXCESS, 5 SECOND_RATE, 1 downcode,
  1 provider gap), plans with schedules of their own (Anthem employer plan and products, the UMR and Wausau plan, PAs at
  85.7 percent), UHC's Indiana 85 percent, UHC MA's January lines absorbed into the repricing item, a 2024 contract item
  whose office lines fell under its 50-line floor once the surgery center's were counted as facility.

## Open questions
- UHC Kentucky PA/NP: paid in full from January 2025 to late October 2026, then 85 percent on some lines. The branch
  lists the 99213s after the switch as one routine-rate review framed as a rate change (1add3df444791b0e54dc8909,
  check the contract first), but two one-offs dated after the switch (ee002bd949c21d04ccba43bb 17110 $18.88,
  f1ceae8b8ba61059469ba9e3 99214 $16.56) are listed as appeals: likely the same change. If UHC confirms it, the Kentucky
  rule needs an end date.
- UMR routine-rate review 22130d50949acee5d66aad47 (5 NP 99214 lines, $312.80): sized against the plan's full rate;
  if the plan pays nurse practitioners 85.7 percent as elsewhere, the gap is about $217.
- Repricing items list only lines short by the one-off threshold ($2 and 5 percent): UHC MA also paid 16 January
  procedure lines at 2025 amounts with smaller increases (about $60 in all, mostly under $5 a line).
