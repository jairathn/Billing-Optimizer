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

## Review round (2026-11-26, 20:58 to 22:00 UTC): the two reviews' 10 findings
Merged the integration tip again first (35fc020: plans, docs and tests/test_docs.py only; clean, 72dfee6). Every fix has
a failing test first (invented data, both store kinds where the code runs on both), was broken on purpose and restored.
- r1-1 / r2-2 (medium, real): results.update_registry dates the first flag on the practice's calendar
  (`runtime.practice_date(first_seen)`), as a person's Paid (recovered_date) and the payers' dates it is compared with;
  a row stored on UTC's day is lowered by the registry's min() at the next refresh. Tests (tests/test_results.py):
  test_a_paid_marked_the_evening_after_an_evening_flag_counts_as_money_returned,
  test_a_payment_the_payer_dated_the_day_after_an_evening_flag_counts_as_returned_after_the_flag,
  test_a_first_flag_stored_on_utcs_day_is_moved_to_the_practices,
  test_a_paid_marked_the_day_after_an_evening_flag_counts_as_recovered (5 cases: evening, 11:59 PM, year end, end of
  daylight time, Feb 29). Broken: 16 fail; restored.
- r1-2 (medium, real, older than the unit): detect.run_claim_rules' "denied again after someone acted" compares the payer's
  date with `runtime.practice_date(decided)` (decided is the person's change, a UTC moment). Test
  (tests/test_resolution.py): test_a_denial_dated_the_day_after_an_evening_appeal_is_seen_on_its_own, afternoon and evening,
  the server's file store and the compact store. Broken: the 2 evening cases fail; restored.
- r2-1 (medium, real): states._decide decides whether a payment came after a person's reopen by when RemitWatch read it
  (`_came_after`: the 835 row's file `ingested_at`, now on each row of states._r835 as 'read', against the reopen's moment),
  never by the payment date it carries (an EFT date often days after the file). Where the store holds no file for a
  payment (tests that write remittances by hand; the dashboard copy, whose transactions keep no file hash and where the
  stored state stands when it differs) the payer's date against the reopen's practice day is kept. EMA postings keep their
  posting date (a posting dated after the reopen's day was made after it). Tests (tests/test_practice_day.py):
  test_a_reopen_holds_against_the_payment_the_person_rejected (3 reopen moments x 2 stores, the rejected payment's file
  read before the reopen, as in every real store) and test_a_payment_read_after_an_evening_reopen_is_new_money_whatever_its_date.
  The unit's earlier test (a payment dated the day after an evening reopen is newer) still passes. Broken: 8 fail; restored.
  The reviewer's own test, which writes the payment with no file at all, still fails by design (see above).
- r2-5 (medium, real, pre-existing): a fax's sent time. Write side: fax.SRFax.status gives 'at' as a UTC time with its
  zone from EpochTime ("a unix time stamp of the DateSent field", SRFax's Get_FaxStatus page; DateSent is in the account's
  zone, unnamed) and keeps SRFax's words as 'at_text' (not stored); api._fax_refresh stores
  `fax.sent_moment(at, created_at, now)`: the line's time when it has a zone, else the moment the status was read, never
  before the fax was queued nor after now. Read side: `states.fax_day(sent_at, created_at)` (the later of the two days,
  each only when it is a day) in states._evidence, story's fax rows and results.work_of; runtime.practice_date gives ''
  for anything that is not a day. Tests: test_an_evening_fax_is_dated_and_followed_up_from_the_practices_day (SRFax's
  words, no zone, with zone x 2 stores), test_a_fax_is_stored_as_sent_at_a_time_with_its_zone_never_before_it_was_queued,
  test_a_value_that_is_no_day_is_no_day, tests/test_fax.py test_srfax_speaks_its_web_service (rewritten to the new
  contract) and test_select_all_then_appeal (the stored sent_at is zoned and not before created_at). Broken (reader,
  writer, practice_date): each fails; restored.
- r1-3 (low, real, older): alerts.stale_rule and insights.data_health read MAX(ingested_at) through practice_date. Test:
  test_the_stale_feed_alarm_and_the_data_tab_date_the_last_file_on_the_practices_day (afternoon control and evening x 2
  stores). Broken in each module: the evening cases fail; restored.
- r1-4 / r2-3 (low, real): results._d reads a value longer than a day through practice_date (plain days unchanged), so
  _days, week_of, _credits and _hours_counts count an evening moment on the practice's day. Tests (tests/test_results.py):
  test_a_medicare_credit_flagged_in_the_evening_is_overdue_61_practice_days_later,
  test_an_appeal_packet_built_on_sunday_evening_counts_in_that_week,
  test_a_packet_and_a_fax_of_the_same_evening_count_in_the_same_week. Broken: 6 fail; restored.
- r1-5 (low, real): data.js dtDay and results.js rsDate go through practiceDate; today.js has practiceTime (a stored
  moment's date and time in the brief's zone, UTC's said as UTC before the brief loads); features.js atTime (Progress
  activity, feed status "Last new remittance file", the drawer's History, "Why did this change?"), faxes.js faxTime (the
  fax lists), index.html "last analysis" and lab.js "last saved" use it. This also closes the follow-up the last round
  listed (stored times shown in UTC without saying so). Tests: test_the_pages_helpers_say_a_stored_moment_where_the_practice_is
  (node: dtDay, rsDate, atTime, faxTime, practiceTime), test_no_page_shows_a_stored_moments_utc_date_and_time_without_saying_so
  (guard). Broken (dtDay; the feed status line): each fails; restored. Node checks: calc 79, lab 143, none false.
- r2-4 (low, real): runtime.practice_date catches OverflowError (a zoned moment no zone can hold, such as
  '0001-01-01T00:00:00Z') and keeps the day written; fax_day dates such a fax by its queueing day. Test:
  test_one_placeholder_moment_does_not_stop_every_items_state (x 2 stores). Broken: fails; restored.
- Not changed: results.VERSION stays "1" (no unit has bumped it; data_rev changes with the detection run a release brings,
  so the figures are worked out again). The integrator may bump it once after the merges.

## Left
- Nothing of the review round once the suites below are green. Then merge (order: fix7d first, per HANDOFF section 00).
- Follow-ups noticed, not in this round: the 3.12 and newest-library environments were not run by this unit (the common
  brief asks for python3 and 3.11.1).

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

- Review round, what the real data says the fixes move (the reviewers' aggregates on their private copy of the store
  image of 2026-11-24; not re-measured here, nothing in it is moved): 2 manual marks, none in an evening; 17,281 items,
  none first flagged in an evening run (Vercel's daily run is at 5:17 AM Eastern); 2 Paid marks, none credited
  differently; 0 reopens; 34,168 of 34,168 remittance payments have the file that carried them (so the reopen rule reads
  arrival for every one); 1,088 (3.2%) are dated 1 or more days after their file's own date (461 one day, 173 two, 454
  three or more): the case r2-1 guards; the copy has no faxes table yet.

## Open questions
- (none)
