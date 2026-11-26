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

- Brief item 3: fax.py number_hold says the practice's day (fax._day); the brief names the practice's zone
  (`runtime.practice_zone_name`, brief["tz"]); today.js `practiceDate(iso)` dates a stored moment in that zone; the
  drawer's Appealed and every page that cut a moment to its UTC day (today.js Sent/Faxed, desk.js sent/asked/hide rule,
  faxes.js saved/changed/tie, features.js alerts/packets/status, policy.js decided/run, lab.js saved, index.html
  measured) use it. Tests: test_a_changed_fax_number_is_dated_the_practices_day, test_the_brief_names_the_practices_zone,
  test_the_pages_say_a_moment_on_the_practices_day (node), test_no_page_cuts_a_stored_moment_to_its_utc_day (guard).
  Each broken on purpose and restored. Page tests (435) and both node checks pass.

## In progress
- Brief item 4: report.py "Closed this week" (SQL datetime('now','-7 days')), uploads._free_name's "(uploaded <UTC
  day>)", results.js rsDay (the browser's day keys the spot checks; api_results._day accepts it within a day).

## Left, in order
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
