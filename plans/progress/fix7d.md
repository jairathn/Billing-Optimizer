# fix7d: one practice day for the whole site (progress)

Brief: `plans/briefs/2026-11-26_fix7d.md`. Branch `wip/fix7d`. Rules: `plans/briefs/2026-11-26_common.md`.

## Done
- f76260a, b560e0e (before this resume): every window, day count and freshness figure counts from
  `runtime.practice_today()`; tests on the practice's calendar; new module tests/test_practice_day.py.
- 2026-11-26 18:30 UTC: merged the integration tip (fc73dec) cleanly (e33e3f3). tests/test_practice_day.py passes.

- Brief item 2 (commit after 146d886): `states._pday` and `states._d` date a stored moment on the practice's calendar
  (`runtime.practice_date`), so a person's Sent/Started/Paid/packet, `appealed_at`, a fax's `sent_at`, the reopen day and
  every `day()` the states say are the practice's; `story._row` dates its rows the same way. Tests (both store kinds):
  `test_a_persons_marks_and_a_fax_in_the_evening_are_dated_that_day_where_the_practice_is`,
  `test_a_payment_dated_the_day_after_an_evening_reopen_is_newer_than_the_reopen`. Broken on purpose and restored.

## In progress
- Brief item 3 (display words: features.js "Appealed", fax.py "number was changed on").

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
