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

- Brief item 4 (search, first batch): report.py "Closed this week" counts from the practice's day a week ago (was SQL
  datetime('now','-7 days')); uploads._free_name's "(uploaded <day>)" is the practice's day; the Results spot checks are
  the practice's day's (api_results._day no longer takes the browser's day; results.js rsDay uses siteDay); the swing
  alarm's seven days count the run's practice day (alerts.swing_rule); results.work_of/_fix_markers/_minutes_at date a
  person's work, a fax, a fix and a settings save on the practice's day; selfcheck._work_after compares the practice's
  day of a person's change with the answer's day; insights.trizetto_health says the practice's day of a refused sign-in.
  Tests in tests/test_practice_day.py, tests/test_results.py (spot day, rewritten to the new rule), tests/test_swing.py;
  each fix broken on purpose and restored.

- Brief item 4 (rest of the search, nothing more to change): api*.py, cloud.py, pipeline.py, fax.py and the pages read
  the practice's day already; left on purpose: the server's daily backup (cli._daily_backup, an operational UTC day),
  the log files' day names (runlog, UTC by design), runtime's SQL self-probe (date('now') on a blank store, no data),
  fax._range_days (asks SRFax for both days), today.js localDay / features.js / index.html fallbacks used only before the
  brief has loaded, the scoreboard's frozen_at display (results.py, one display line), ema_checks' export status order
  (substr(recorded_at,1,10) only orders sources).

- Brief item 5: every suite run (see Measured). The runs went from a frozen worktree of the tip (`git worktree add --detach $S/tk/units/fix7d/frozen <tip>`), with
  `RW_RUNS=$S/tk/units/fix7d/runs REMITWATCH_TZ=Etc/GMT-14 $S/tk/units/fix7d/shard_tz.sh $S/tk/units/fix7d/frozen python3 tz_minus14 3`
  (shard_tz.sh is plans/tools/shard.sh keeping REMITWATCH_TZ; copy it from this file's section "How to run" if the unit
  folder is gone: it is shard.sh with `[ "$v" = "REMITWATCH_TZ" ] ||` before `unset`).
- Merged the integration tip again before the final runs (cefa3b2 and 7001513, plans only).

## Left
- Nothing of the brief. The unit is finished: ready to merge (merge order: fix7d first, per HANDOFF section 00).
- Follow-ups noticed, not in the brief: the pages still show some stored times of day in UTC without saying so
  (faxes.js fax lists, features.js history and "Why did this change?" tables, index.html's "last analysis"); a
  practiceTime helper (the zone the brief names) would show them where the practice is. The 3.12 and newest-library
  environments were not run by this unit (the common brief asks for python3 and 3.11.1).

## How to run
- `S=/tmp/claude-0/-home-user-Billing-Optimizer/96e81489-2ad2-5c70-991d-e00db2e22749/scratchpad`
- Tests of one file: `$S/tk/units/fix7d/t.sh python3 tests/test_practice_day.py` (unsets the feed variables, keeps
  REMITWATCH_TZ).

## Measured
- Full suite, python3, REMITWATCH_TZ=Etc/GMT-14, on cff80cf: 14 failed (all tests written for an Eastern practice that
  the new practice-day dating moved: test_results x6 (fax/fix/settings moments at 08:00-12:00 UTC), test_swing x7 (runs at
  10:17 UTC), test_uploads x1 (the UTC day in an upload's name)). Fixed in the tests (Eastern pinned in test_results' and
  test_swing's module fixtures, the practice's day in test_uploads); both zones then pass those three files.
- Full suite, python3, REMITWATCH_TZ=Etc/GMT+12, on 253d961: 2,839 passed, 29 skipped, 1 xfailed, 0 failed.
- Full suite, python3, REMITWATCH_TZ=Etc/GMT-14, on 253d961 (again): 2,839 passed, 29 skipped, 1 xfailed, 0 failed.
- Merged the integration tip again (cefa3b2/7001513: plans only, no code).
- Full suite, python3 as it is (REMITWATCH_TZ unset), on b5202a0: 2,839 passed, 29 skipped, 1 xfailed, 0 failed
  (19:19-19:31 UTC).
- Node checks on b5202a0: calc_model_check.js 79 checks, lab_review_check.js 143 checks, none false.
- Full suite, $S/fix/venv_sqlite340/bin/python (3.11.1, SQLite 3.40.0), on b5202a0: 2,722 passed, 13 skipped,
  1 xfailed, 109 failed and 24 errors, every one of the 133 with "AttributeError: 'sqlite3.Connection' object has no
  attribute 'serialize'" (the allowed class: tests that serialize a store image; that build has no
  Connection.serialize). No other failure.

## Open questions
- (none)
