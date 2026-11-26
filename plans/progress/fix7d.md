# fix7d: one practice day for the whole site (progress)

Brief: `plans/briefs/2026-11-26_fix7d.md`. Branch `wip/fix7d`. Rules: `plans/briefs/2026-11-26_common.md`.

## Done
- f76260a, b560e0e (before this resume): every window, day count and freshness figure counts from
  `runtime.practice_today()`; tests on the practice's calendar; new module tests/test_practice_day.py.
- 2026-11-26 18:30 UTC: merged the integration tip (fc73dec) cleanly (e33e3f3). tests/test_practice_day.py passes.

## In progress
- Brief item 2: `states._evidence` (and the story's timeline rows, `states.day`, the reopen dates) date a moment by
  `runtime.practice_date`. Test on both store kinds in tests/test_practice_day.py.

## Left, in order
3. features.js "Appealed" and fax.py "number was changed on": the practice's day.
4. Search the whole tree for other "today" reads (date('now'), utcnow().date(), date.today(), new Date() as a day).
5. Full suite in python3 three times: as it is, `REMITWATCH_TZ=Etc/GMT-14`, `REMITWATCH_TZ=Etc/GMT+12`
   (plans/tools/shard.sh unsets REMITWATCH_*: use the copy in the unit folder that keeps REMITWATCH_TZ, see below).
   Then the final suites: python3 and `$S/fix/venv_sqlite340/bin/python`; node checks if static/ changed.

## How to run
- `S=/tmp/claude-0/-home-user-Billing-Optimizer/96e81489-2ad2-5c70-991d-e00db2e22749/scratchpad`
- Tests of one file: `$S/tk/units/fix7d/t.sh python3 tests/test_practice_day.py` (unsets the feed variables, keeps
  REMITWATCH_TZ).

## Measured
- (none yet this resume)

## Open questions
- (none)
