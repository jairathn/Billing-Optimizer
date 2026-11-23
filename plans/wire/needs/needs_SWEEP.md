# needs_SWEEP.md: unit SWEEP (the intake files after unit A1)

Status: FINAL (2026-11-23). Aggregates only; no names, claim ids, check numbers, hosts or payer aliases below.

SWEEP owns remitwatch/intake.py, ema_exports.py, dropbox_sync.py, cloud.py, pipeline.py, store.py, state.py, cli.py,
ema_reports.py, ema_ledger.py, .env.example (EMA lines) and tests/test_ema_intake.py, test_cloud.py, test_sync.py,
test_parity.py, test_ema_reports.py, test_ema_ledger.py, tests/ema_fixtures.py, tests/test_feeds.py. store.py, state.py,
.env.example, test_sync.py, test_parity.py, ema_fixtures.py and test_feeds.py needed no change.

## 0. Every request aimed at these files, with its status

Legend: DONE NOW (test that pins it; each test was broken and restored on a private copy of the tree, results in
wire/SWEEP/results/break_restore_*.json), DONE ALREADY (where), NOT NEEDED (why), OWNER (who decides or applies).

| # | request | from | status |
|---|---|---|---|
| 1 | A 1 MB upload that unpacks without end (gzip bomb) or holds one giant line stops every cycle | review_A2_security #0 (high), PLAN needs sweep, brief 2 | DONE NOW. One cap on every reader path: unpacked bytes at most max(64 MB, 200 times the packed size), never over 2 GB; lines at most 1 MB (the longest real export line is 621 bytes; the highest real ratio is 13.7). intake (hash and copy of both hosts' pairs, non-report gzips), ema_reports (gzip, zip members, workbook parts before openpyxl, the line reader), ema_exports (workbooks), dropbox_sync (non-report gzips, zip members, X12 over 64 MB). The file is refused with a plain reason ("It unpacks to more than 199 MB from a file of 1 MB ... Export it again from EMA"), listed on the Data tab, never read again; the cycle goes on. A pair that stops the cycle twice anyway is set aside (held, Apply or Discard). Tests: test_a_gzip_bomb_uploaded_on_vercel_is_refused_without_ever_being_unpacked_whole (child process under a 384 MB address-space cap), test_a_gzip_bomb_uploaded_on_a_server_is_set_aside_and_the_cycle_goes_on, test_one_giant_line_in_a_report_is_refused_as_soon_as_it_passes_the_cap[server,serverless], test_a_workbook_whose_parts_unpack_far_past_any_export_is_set_aside_unopened, test_a_zip_whose_member_unpacks_far_past_any_export_is_refused_and_listed, test_a_gzip_that_is_no_report_and_unpacks_too_far_is_listed_as_not_read, test_an_x12_file_far_larger_than_any_payer_sends_is_listed_as_not_read, test_a_pair_that_stops_the_server_cycle_twice_is_set_aside_and_the_rest_goes_on, test_a_pair_that_stops_the_run_twice_is_set_aside_on_vercel_and_production_keeps_the_slots_own_count. A2's uploads.py now calls ema_reports.unpack_limit and LINE_MAX (keep those names). |
| 2 | A Production report kept waiting while a host did not read Production is never read later (the origin's size was the original's, so a 206 MB CSV kept as a 15 MB gzip looked too big) | review_A2_end-to-end #0 (high), PLAN decision 6 | DONE NOW: cloud.reread_waiting decides by the size as kept (_stored_size). Test: test_a_production_upload_kept_waiting_by_the_switch_is_read_once_the_host_reads_production. |
| 3 | Vercel: an upload finished while a run went on waits for the next notification | review_A2_end-to-end #3 (cloud side) | DONE NOW: `more` is true while intake pairs remain. Test: test_an_upload_finished_while_a_run_went_on_starts_a_follow_up_run. |
| 4 | Abandoned uploads are removed only when someone opens the Upload page | review_A2_security #5 | DONE NOW: every server cycle (pipeline.sweep_uploads) and every serverless run (state_uploads.sweep) sweeps. Test: test_every_cycle_sweeps_what_uploads_left_behind_on_both_hosts. |
| 5 | A weekly export read through the intake folder says only "Read as an EMA export." | review_A2_end-to-end #10 | DONE NOW: the router keeps each export's counts and dates; intake words them with uploads.describe (the one-piece route's sentences). Test: test_a_weekly_export_read_through_the_hand_off_says_what_it_held_like_the_upload_page[server,serverless]. |
| 6 | A/R note kinds stored as ema_ar.note_kind / note_date / note_wait (never the words), in the serving tables | needs_D A1.1, brief 3a, INT item 7 | DONE NOW (states.classify_note per row, date resolved on the file's as_of; DDL, ensure_schema migration, serving whitelist). Real world: 9,653 A/R rows, 1,542 with a note, 326 classified (follow_up 101, resubmitted 86, appeal 64, records 24, reprocess 10, no_appeal 1), 70 dated, 6 with a wait. Tests: test_each_ar_billing_note_is_kept_as_what_it_says_never_its_words, test_an_ar_file_read_by_a_build_without_note_columns_gets_them_and_keeps_its_rows. |
| 7 | Link map rows for bills with no 835 (patient-day match, never a facility claim) | needs_D A1.2, brief 3b | NOT BUILT (measured, unsafe): the export's patient id matches the A/R record-number key for 0 of 3,117 patients, so no patient key exists; of 2,157 export bills with no 835, 2,046 are unlinked, and the ERA-export amount bridge gives one candidate for 105 bills but several for 90 (no check numbers in the ERA lines), so a wrong link would follow. review_B_gates shows C40 to C43 agree through B's claim_answers content bridge. OWNER: D/INT if a patient key ever arrives (an export with the record number). |
| 8 | Wider serving tables, only if the serverless peak stays under 1.75 GB | needs_D A1.3, brief 3c, needs_C1 3 (host parity) | DONE NOW: every 835 claim, line, adjustment and remark of a bill that has an item (SERVING_BUILD 8). Real world: claims 21,588 to 22,675, lines 30,453 to 63,081, adjustments 52,776 to 108,279, remarks 19,792 to 40,063; the copy 151.6 MB to 188.4 MB. Peak: section 2. needs_C1 3 (one item told to bill a second payer already billed, because the copy lacked the second payer's answer): build 8 carries every 835 claim of the bill, that answer included; not re-checked on C1's world (its item is not in this unit's world). Test: test_the_dashboard_copy_holds_every_claim_of_a_bill_that_has_an_item. |
| 9 | Vercel: the store's key salt is saved before the first ledger save | report_A1 open question, brief 4 | DONE NOW (ProductionSlot.apply makes and saves it before the first attempt). Test: test_a_first_production_file_saves_the_stores_key_salt_before_the_ledger_on_vercel; the save-order test now expects [store, ledger, store, serving, cursor]. |
| 10 | Retention of read uploads (done/) on both hosts | needs_A1 5, needs_A2 A.3.8, brief 4 | DONE NOW: 180 days after arrival, never the copy of a report held, waiting, to be read again or live, nor each report's newest copy (intake.retain_done_server / retain_done_state, run each cycle). Test: test_read_uploads_go_after_180_days_but_never_a_held_waiting_live_or_newest_reports_copy[server,serverless]. |
| 11 | The 60 MB waiting sentence on the catalog row, the alert and the Data tab | brief 5, needs_C2 row 19 | DONE NOW on the catalog row (ema_reports.BIG_CSV_REASON: "Upload it on the Upload page (it compresses it), or put a zipped copy in the same Dropbox folder") and ema_reports.waiting_reason(conn, report) for any caller; C2 shows status_reason verbatim. Test: test_a_raw_csv_too_large_for_this_site_waits_until_a_zipped_copy_is_read. OWNER of the alert text: INT/B (section 3). |
| 12 | ema_checks.turnaround(conn, ledger=lg) after the refresh | needs_B 4.3, review_B_gates #13 | DONE NOW: server cycle (pipeline.ledger_turnaround, once per ledger change, stamped with the ledger revision) and the serverless apply run (read while the ledger is open, before it is released; the next run keeps it). Real world: 4.0 s, 33 of 111 groups' charge p95 change. Tests: test_the_server_cycle_reads_the_payers_answer_times_from_the_whole_ledger_once_per_change, test_a_serverless_apply_run_reads_the_answer_times_from_the_ledger_and_the_next_run_keeps_them. |
| 13 | Keep EMA lines with a second payer on file (and crossovers) though the bill has no item | needs_B 4.1, review_B_gates #2 (high) | DONE NOW (measured first): keep rule second_payer = a second payer on file, the first payer paid, EMA still shows a balance. Real world: 3,861 more lines than the 70,285 kept (+5.5%); without the balance condition it would be 45,567 (+65%), and detection only lists lines with a balance. Crossed-over lines (status 19, MA18/N89) add 0 lines on the real world (already kept), and B lists a crossover from the 835 alone, so no crossover rule. Test: test_a_line_with_a_second_payer_on_file_and_a_balance_left_is_kept_though_its_bill_has_no_item. |
| 14 | needs_B 4.2: ema_line_payments is lean, C40 stays a C5 item | needs_B 4.2 | NOT NEEDED here: review_B_gates finds C40 to C43 agree through B's content bridge. |
| 15 | needs_B 4.4: serverless memory of detection on the whole image | needs_B 4.4 | MEASURED (section 2): the run that analyses peaks at 1,625 MB (r4) and 1,485 MB (r1), under 1.75 GB, measured as A1 does (separate processes, the real compact image). |
| 16 | The ledger's retention archives first (needs_E 2a, 2b) and the server ledger attached to the store's archive folder | needs_E 2 | DONE NOW in ema_ledger.py (HOOK's list leaves this file out): Ledger.retain calls archive.before_ledger_retain before it deletes; a folder or Dropbox that refuses the write deletes nothing (Ledger.archive_error, out["retain_archive_error"], a log line); LedgerHandle.open attaches the state folder's archive; ema_ledger.open_for_store (pipeline and cli) attaches archive.sink_for(db_path) so REMITWATCH_EMA_LEDGER or REMITWATCH_ARCHIVE_DIR never split them. Tests: test_retention_archives_every_posting_it_deletes_where_the_store_keeps_its_archive, test_retention_deletes_nothing_when_its_archive_cannot_be_written_and_says_so, test_a_serverless_ledger_archives_what_retention_deletes_to_the_state_folder. |
| 17 | needs_E 3-4, needs_G 2-3 and 7, needs_H, needs_R 1: hook bodies in pipeline.py and cloud.py | needs_E/G/H/R | OWNER: HOOK (phase 3b, brief_HOOK lists pipeline.py and cloud.py). Not applied here, so each hook has one owner. Note for HOOK in section 3. |
| 18 | A1 integrity review (crash repair, re-find, transient errors, peek failure, zips of remittances, same-day exports): ten invented cases, all failing on the tree before this unit | review_A1_integrity (its JSON had not landed; its ten test files did, in wire/review_A1_integrity/) | DONE NOW, each with a test of this unit (the reviewer's ten pass too): (a) a Production upload applied by a run stopped at the store save was reported held, attempts never cleared: the slot calls a file the repaired catalog holds 'already' and clears its attempts (test_an_upload_applied_by_a_run_stopped_at_the_store_save_is_already_read_and_its_attempts_go); (b, c) the repair left no files / ema_sources rows and no Dropbox origin: an 'already' Production file restores them, and the row from the ledger (test_the_repair_brings_back_the_files_and_sources_rows_and_where_a_dropbox_file_is); (d) the later of two same-day CSV exports lost to the one that arrived later: same as_of and no workbook time, the one holding more postings made that day is the newer, arrival only on a tie (test_the_later_of_two_same_day_exports_wins_whichever_arrives_first[both orders], test_two_same_day_exports_that_hold_as_much_on_that_day_go_by_arrival); (e, f, g) server: a fetched or re-found file whose read was stopped or failed was never offered again: sync_pending keeps every fetched file until a cycle offered it, counted before each offer; after 5 failing cycles in a row it is offered once a day, never dropped, so a file that stops the cycle costs one cycle a day and a long outage loses nothing (test_a_report_the_re_find_brought_in_is_read_by_the_next_cycle_when_one_stops_while_reading_it, test_a_report_whose_read_fails_once_is_read_by_the_next_cycle[re-find,delta], test_a_fetched_file_whose_read_fails_every_cycle_waits_a_day_between_tries_and_is_read_once_it_can_be); (h) Vercel: a big raw CSV whose 64 KB peek failed once was remembered as seen: it is not, and the position stays (test_a_big_report_whose_first_bytes_could_not_be_fetched_is_looked_at_again); (i, j) a zip of remittances dropped in the folder was skipped: each member is read on its own on both hosts (not the first-load archive), the same bytes never twice (test_the_remittances_in_a_zip_dropped_in_the_folder_are_read_once[server,serverless]). |
| 19 | review_A1_hosts | brief ADDED | Its folder was empty when this unit finished: nothing to apply. |
| 20 | needs_A2 A.3.1 to A.3.7, A.4, D (catalog id = sidecar sha, via/origin, done/ move and result, kick, held requeue, _content_call text, serving_tables in build_serving, howto copy, the two test_feeds expectations) | needs_A2 | DONE ALREADY by A1 (report_A1; the tests pass). A.3.8 retention: row 10. |
| 21 | needs_C1 6: tests/test_uploads.py:1138 expects the old Upload link line | needs_C1 6 | OWNER: A2 fixer (test_uploads.py is A2's file). |
| 22 | needs_B 1.9: the held-report decision clears a slow file's attempts | needs_B 1.9 | DONE ALREADY by C (api.py decide route calls LedgerHandle.clear_attempt). The slot does not clear on its own (a file that kills runs would loop). |
| 23 | review_D_money #5 cites ema_ledger's ema_lines.balance | review_D_money #5 | NOT NEEDED here: the balance is EMA's own by design; the check that cannot fail is in story.py (D's). |
| 24 | PLAN orchestrator decision 6 (a report kept waiting while a host did not read Production is read once it does) | PLAN | DONE NOW (row 2). |
| 25 | A person's Sent, Paid, Let go or Lost leaves the item on Work now until new data arrives; ask of A1's files: have the cycle analyse again once a status, hide or policy change lands | review_C1_1 #0 (high; the api.py part is C1's) | DONE NOW on both hosts: pipeline.people_changed_since_detection (a person's own status event, kind 'manual', a hide rule added or removed, or a write-off policy set after the last run started; never the hide pass that runs after each analysis) makes the server cycle and the serverless run analyse again, once. test_cloud's fold test now expects that run to analyse. Tests: test_a_status_a_person_set_after_the_last_run_brings_the_states_up_to_date_once[server,serverless], test_only_a_persons_change_after_the_last_run_asks_for_detection_never_the_hide_pass, test_status_changes_show_at_once_survive_and_reach_the_next_run. |
| 26 | review_C1_1 #12 ('Open in EMA' never offered) | review_C1_1 #12 | NOT MINE: story.py (D); ema_reports.claim_url_template is unchanged. |
| 27 | A zip uploaded in the app: its members' own names must not reach a log line or a result | needs_A2 A.3.2 (no file name in a log line), found while doing row 18 (i, j) | DONE NOW: an upload's members are named by their place ("file 2 of the zip"); a damaged member is listed and the others are read; a zip read in part is 'applied' with the member it could not read among the lines. Tests: test_an_uploaded_zip_of_remittances_is_read_and_names_its_files_by_place_only, test_a_damaged_member_of_a_zip_of_remittances_is_listed_and_the_others_are_read. |
| 28 | A report held again after one Apply keeps decision 'apply': every run fetches it, the page offers nothing | review_C2_1 #4 (its ask of A1's files; data.js is C2's) | DONE NOW: ema_ledger.hold_slow_file clears decision, decided_by and decided_at when a held file a person applied runs out of attempts again, so the Data tab asks again and cloud._held_to_apply stops fetching it. Test: test_a_held_file_a_person_applied_that_runs_out_of_attempts_again_asks_again. |
| 29 | review_C2_1 #8 (the re-send list for checks with no payer name) | review_C2_1 #8 | NOT MINE: api_ema.py (C2). |
| 30 | review_G #6 (a killed run counted: a 'running' status right after the lock), review_G #12 (the copy check's result kept outside the store), review_H #6 (a cycle that dies before its lock leaves a trace) | review_G, review_H | OWNER: HOOK with the G and H fixers (their hooks in pipeline.py, cloud.py, cli.py). |
| 31 | Never catch an exception without logging it (CLAUDE.md, runlog.caught) | CLAUDE.md, found by this unit's own check | DONE NOW: this unit had added 7 except blocks that said nothing (intake 4, dropbox_sync 1, ema_reports 2). Each now logs with runlog.caught at level warning (the run's log keeps the error and its stack; the run stays 'ok'), or is narrowed (the note classifier's import: ImportError only). An A/R note the classifier cannot read is logged once per file without its message (it can quote the note's words), with a count of such notes. The silent counts are back at what these files had before the unit (intake 4, dropbox_sync 3, ema_reports 3; ema_ledger, ema_exports and cli 0), pinned by a test; pipeline.py 9 and cloud.py 11 hold under H's ceiling. Tests: test_the_intake_files_silent_blocks_do_not_grow, test_what_the_intake_contains_is_in_the_runs_log_as_a_warning_never_a_notes_words (breaks B44 to B51, results/break_restore_logging.json). |
| 32 | Two analyses that finished within the same second: the newest was picked by finished_at alone, so an older run's version or start could ask for another analysis | found in the 3.40 suite (tests/test_features.py cycle test, once) | DONE NOW: the newest run is ORDER BY finished_at DESC, rowid DESC (the one written last) in pipeline.py (the rules check and people_changed_since_detection) and cloud.py (the rules check). Before, the pick among equal seconds was SQLite's and could be the older run, so a cycle analysed again for nothing. Test: test_two_analyses_that_finished_in_the_same_second_are_told_apart_by_the_one_written_last[server,serverless] (breaks B52 to B54, results/break_restore_tie.json). runlog.py has the same query (section 3, H). |

## 1. What changed, file by file

- intake.py: capped hashing and unpacking on both hosts; pair attempt notes (server meta intake:attempts, serverless
  intake_attempts.json) and set-aside after PAIR_TRIES; bomb refusals recorded under the sidecar digest; retention of
  done/ (both hosts); export result lines through uploads.describe; a zip read in part counts as applied.
- ema_reports.py: unpack and line caps (Capped, unpack_limit, zip_refusal, LINE_MAX); refusals by packed id; note kinds;
  BIG_CSV_REASON and waiting_reason; _restore_rows (catalog from the ledger, origin, files and ema_sources rows) for an
  'already' Production file; a zip's other members handed to the router ('others').
- ema_exports.py: workbook parts checked before openpyxl.
- dropbox_sync.py: not-read rows for non-report gzips, X12 over 64 MB and workbooks too large; sync_pending (add_pending,
  take_pending, settle_pending; once a day after five failing cycles) for fetched and re-found files; 'retry' paths from the router; zip members read on their
  own (64 MB unpacked at a time); per-export counts for the hand-off's words; no exception message in a log line.
- cloud.py: serving copy SERVING_BUILD 8 (whole bills); reread by the size as kept; sweep each run; `more` while pairs
  remain; salt before the ledger; ledger turnaround in the apply run; 'already' instead of held for a repaired file; a big
  report whose peek failed is looked at again; a person's status change since the last analysis has the run analyse again.
- pipeline.py: sweep, intake retention, ledger turnaround, sync_pending offer and settle; the ledger via open_for_store;
  people_changed_since_detection (both hosts) so a person's status change brings the stored states up to date.
- ema_ledger.py: retention archives first (needs_E 2); open_for_store; same-day tie by postings made that day; the
  second_payer keep rule; hold_slow_file clears a spent Apply.
- cli.py: the ledger via open_for_store (retention by a person archives too).
- Late changes (rows 31 and 32): runlog.caught in the except blocks this unit added (intake.py, dropbox_sync.py,
  ema_reports.py; the A/R note classifier logs once per file and never a note's words); the newest analysis is the one
  that finished last, then the one written last (pipeline.py twice, cloud.py once).

## 2. Measurements (live_state_1122 copies, the Dropbox mirror behind a fake client, A1's recipe)

Server, one cycle on the live store copy (the position past every file: the re-find reads four reports, 938,578 rows),
then an idle cycle:

| | before (HEAD 37561c1) | after |
|---|---|---|
| cycle 1 | 210.9 s, peak 930 MB | 194.5 s, peak 948 MB |
| idle cycle | 2.0 s, 59 MB, no detection, no refresh | 2.3 s, 59 MB, no detection, no refresh |

Serverless, runs until `more` is false (the A/R from Dropbox in run 1, then the 2026 Production CSV uploaded as an 11.2 MB
gzip and the 2025 one dropped as a 14.4 MB zip):

CPU seconds per run (comparable on a shared machine: the load average was near 10 on 4 CPUs; wall seconds in brackets),
peak resident memory and the temporary folder's peak. Before: HEAD 37561c1; after: the working tree at 01:14 (this unit's
changes and the other units' of the same hour: detection now finds 18,936 items where it found 17,799).

| run | before | after |
|---|---|---|
| r1: the A/R from Dropbox, the one full listing, the analysis | 85.1 s (99.3), 1,471 MB, /tmp 130 MB | 99.4 s (135.7), 1,485 MB, /tmp 167 MB |
| r2: the 2026 Production upload applied | 30.9 s (39.7), 1,287 MB, /tmp 139 MB | 34.6 s (42.2), 1,296 MB, /tmp 175 MB |
| r3: the 2025 Production zip applied | 43.9 s (59.5), 1,372 MB, /tmp 290 MB | 50.7 s (64.6), 1,382 MB, /tmp 290 MB |
| r4: the analysis (deferred to a fresh run) | 104.4 s (126.2), 1,580 MB, /tmp 154 MB | 126.8 s (168.8), 1,625 MB, /tmp 193 MB |
| idle run | 4.1 s (4.8), 1,252 MB, nothing saved | 4.3 s (6.7), 1,262 MB, nothing saved |

Gate 3 holds on this emulation: every run under 1.75 GB (the highest 1,625 MB, 125 MB below) and under 180 s of CPU;
/tmp under 512 MB. What this unit adds to a run: the answer times read from the ledger in the apply runs (about 4 s each),
the whole-bill dashboard copy (188.4 MB instead of 151.6 MB, built in r1 and r4), the note kinds of the A/R rows, and the
second_payer keep rule's 3,861 lines. An idle run still saves nothing and analyses nothing.

The server rows above used a frozen copy of the working tree at 01:00 (other units' changes of that hour included).

## 3. Asks of other units

1. C2 (data.js, optional): the server cycle's output carries sync_pending {waiting, failing, gone}; "failing" counts
   fetched files not read in five cycles in a row (offered again once a day, named in the log). The Data tab may show
   "N files from Dropbox could not be read yet" when failing > 0.
2. INT/B (alerts or insights, the EMA freshness alert): name a waiting report with ema_reports.waiting_reason(conn,
   'production') so the alert says what to do, as the Data tab does.
3. HOOK: (a) the server cycle already opens the ledger with ema_ledger.open_for_store (archive attached); needs_E 3's
   archive step needs no attach of its own. (b) The uploads sweep and the retention of read uploads already run each cycle
   on both hosts (pipeline: uploads_sweep, intake_retain; cloud: uploads_swept, intake_retain): selfcheck.housekeeping
   (needs_G 7) must not sweep a second time; its 30-day removal of replaced/superseded/discarded kept copies is compatible
   with SWEEP's 180-day rule (SWEEP never removes a held, waiting, live or newest copy). (c) sync_pending is the server's
   journal of fetched files: a repair step that resets a stopped cycle must not empty it.
4. A2 fixer: needs_C1 6 (tests/test_uploads.py:1138).
5. Phase 4: the serverless gate is measured with REMITWATCH_EMA_REPORTS=all (decision 6); section 2 used 'all'.
6. H (runlog.py, optional): `SELECT version FROM detection_runs ORDER BY finished_at DESC LIMIT 1` (the rules version
   it records) has the tie row 32 fixed in pipeline.py and cloud.py; `ORDER BY finished_at DESC, rowid DESC` picks the
   run written last.

## 4. Known limits

- On Vercel a weekly export or remittance whose read raises is still remembered as seen (as before); only the server keeps
  such files pending. A Production file whose apply raises waits in the catalog and is read again (as before).
- The same-day tie rule counts postings made on the shared as_of day; two exports of one day where the later one lost a
  posting (a void deleted, not reversed) would keep the earlier one's posting until a newer export arrives.
- Link map rows for bills with no 835 (row 7) need a patient key the exports do not carry.
- A person's status change makes the next run analyse (row 25): on Vercel that run costs what r4 does (about 105 to 130 s
  of CPU, 1.6 GB). Runs with no change since the last analysis still analyse nothing.

## 5. The full suite (once per environment, in 8 processes of test files: one long process was killed on this shared machine)

Results in wire/SWEEP/results/suite_312/ and suite_340/ (chunk_NN.txt each, exits.txt). Other units edited the tree while it
ran; the tree is checkpointed by the orchestrator, so each chunk ran on the tree of its own minute.

| environment | passed | failed or error | of those, not this unit's or expected |
|---|---|---|---|
| Python 3.12.3, SQLite 3.45.1 (venv2_compat312), 01:43 to 01:57 | 2,175 (2 skipped, 1 xfailed) | 10 | test_docs x5 (F's, expected); test_fax test_select_all_then_appeal and test_prepare_send_and_fax_cycle_commands (the appeal list: api.py/story, C1 and D); review5 test_one_payment_counted_as_two_recoveries and review6 test_open_findings_without_a_deadline_still_show_as_work (both pass in the later 3.40 run: another unit's edit in flight); test_runlog test_the_cycle_files_silent_blocks_do_not_grow (this unit's: people_changed_since_detection had two broad silent excepts; narrowed, pipeline.py 9 and cloud.py 11 now, passes) |
| Python 3.11.1, SQLite 3.40.0 (venv_sqlite340), 02:06 to 02:51 | 2,112 (13 skipped, 1 xfailed) | 92 | 84 are "Connection has no attribute 'serialize'" (expected on this build; Vercel has it); test_docs x5 (F's); the same two test_fax tests; test_features test_cycle_ingests_detects_alerts_and_releases_the_lock once (two analyses finished in the same second: row 32, fixed; it passes in all three Pythons now) |

After the suite, the files this unit touched since were run again: Python 3.12 test_ema_intake, test_ema_reports,
test_ema_ledger, test_sync, test_cloud and the silent-block count (236 passed), then after row 32 test_cloud,
test_features, test_ema_intake, test_sync and test_parity (150 passed); Python 3.13.18 with SQLite 3.53.1 the same files
plus test_feeds, test_parity and test_uploads (259 passed, 25 skipped: paramiko is not installed there) and test_cloud
(35 passed); Python 3.11 the new tests (the server cases pass, the serverless ones stop at serialize, as expected).
Break and restore: results/break_restore_sys.json (43 breaks), break_restore_logging.json (B44 to B51 and each of B44
to B48 against the behaviour test alone), break_restore_tie.json (B52 to B54): every break fails its test and every
restore passes.
