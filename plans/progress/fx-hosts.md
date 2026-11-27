# fx-hosts (fix wave F6, hosts): progress

Branch `wip/fx-hosts`, from the integration tip 8c59195, merged with the tip 34e3573 (briefs and plans only since 8c59195)
and with the tips 47b1ba0 (Merge wip/fx-ledger, and the brief that routed F1's need to F6) and fe9ad71 (Merge wip/fx-837,
Merge wip/fx-exports, and the brief that routed F3b's needs).
Brief: `plans/briefs/2026-11-26_fixwave.md`, section "F6 hosts"; rules in `plans/briefs/2026-11-26_common.md`.
Tests: `tests/test_fx_hosts.py` (the reviewer's since-hosts-tests and the verifier's since-hosts-verify-tests, ported, plus
the unit's own; invented data only). Private folder: `$S/tk/units/fx-hosts/` (mode 700).

Last updated: 2026-11-27 05:46 UTC.

## Done (each fix broken on purpose and restored: `$S/tk/units/fx-hosts/breaks.py`, every break fails its tests)

- **hosts-1** (an analysis that stopped is owed). `remitwatch/pipeline.py`: `DETECT_PENDING` (= `cloud:detect_pending`, the
  serverless cycle's own mark and `archive.META_DETECT_PENDING`: one key, one meaning on both hosts), `analysis_owed`,
  `owe_analysis`, `analysis_paid`, `input_position`; `run_cycle` writes the mark (committed) before any reader may commit
  input, analyses while it is set, clears it after `run_detection` returns or when the cycle read nothing that needs an
  analysis and none was owed; a reader whose counts are lost (TriZetto read files and then failed) still makes the cycle
  analyse, because the facts the store holds moved (`input_position`: transactions, claims_837, ema_rows, EMA reports
  applied; never a file's bookkeeping, so a file retried and not read costs no analysis). `out["analysis_owed"]` says a
  cycle ran an analysis a stopped one owed; `out["read_unreported"]` that a reader's counts were lost. `remitwatch/cli.py`:
  `ingest-835`, `ingest-ema` and `sync` (without --no-ingest) owe the next cycle an analysis. Tests: the reviewer's two,
  the verifier's (stop in the rules, stop in the save, SIGKILL in a child process as the scheduler sees it, the fax guard
  control/stopped), a stop after reading and before the analysis (archive requests, EMA's waiting reports), quiet cycles
  analyse nothing and leave no mark, an analysis that keeps failing is tried again and said every cycle, a store imported
  from Vercel with its analysis owed, a feed whose counts were lost, the CLI's ingest. The fax guard (hotfix 2d D4): the
  verifier's chase test passes in both arms (the analysis runs in the next cycle and the paid item closes, so no chase).
- **hosts-2** (supersession decided after a stop). `remitwatch/store.py`: `SUPERSEDE_SEEN` (`supersede:seen`, [count,
  newest rowid] of transactions, written with the flags in `mark_superseded`'s commit, read before its query);
  `recheck_supersession` decides again when the rule changed or when a transaction exists past what the last decision
  saw. Both store kinds. Measured on the world copy: 34,168 transactions, the first recheck (no record yet) decides again
  in 0.8 s and changes no flag (74 superseded before and after, the same live claims and cents); the next is a no-op (2 ms).
- **hosts-3** (tools that read a copy). `remitwatch/diagnose.py` `main`: an image the last run did not save is read with
  check=False and the bundle says so first (README "Read this first", bundle.json `notes`); a cut-short one makes a bundle
  from the logs; a missing one is named. `build_bundle(notes=...)` (our own sentences only). `remitwatch/cli.py`:
  `_cloud_image(check=)`; `compare-cloud` compares the image as it is, says "Not a proof of the move: <sentence>" first
  and last, exits 1; `import-cloud` prints the check's sentence and exits 1, nothing written. Docs: SERVER_RUNBOOK.md (the
  move), TROUBLESHOOTING.md (the bundle).
- **Flaky 1** `tests/test_results.py::test_the_server_store_and_the_compact_serverless_store_give_the_same_figures`: the
  results clock (`results._now`) pinned for both runs: every stamp (run `at`, point `at`, `frozen_at`) is equal and
  compared (the old strips of `at` removed). Broken (no pin, runs 1.1 s apart): fails on `at`; fixed + gap: passes.
- **Flaky 2** `tests/test_feed_and_day.py::test_the_pages_say_how_far_the_remittances_reach`: `tests/feed_page.js` waits
  (20 s at most) until Today's health banner says where the remittances reach (Today holds the feed's check back until
  the blind spot is known) and until the Worklist's line is drawn; same assertions. Broken (the counts answer delayed 3 s
  with a Playwright route): the old script fails, the new passes, both hosts.
- **Flaky 3** `tests/test_hook_review.py::test_presses_rebuild_the_provider_tables_when_the_analysis_fills_the_run`: the
  cause is in the product. `selfcheck.plan_next` knew the previous run by its start (to the second): two runs of a chain
  that started within one second were taken for one, so an analysing run's pressure was missed on a fast machine and
  counted on a loaded one, where the third slow analysis in a row reached level 'shed' and the last run left the provider
  tables (reproduced: each run in its own second, levels 0,1,1,1,2,2,2,3, stale). Fix: a run is known by its run id
  (`last_run_key`; a state from before by its start); `pipeline.run_cycle`'s summary carries its run id. `plan_next` is
  owned by no fixer (a small edit). The test now runs two slow chains (the press that analyses itself, the press that
  hands the analysis on) and checks no run shed the tables; a new test checks three slow analyses in a row shed them by
  design. Unit tests (both store kinds, both host modes) fail with the start-only key.
- **hosts-5**: confirmed against the real clock on 2026-11-27: test_hotfix2, test_hotfix2c, test_no_fax_route,
  test_notes_bodies_cli, test_bulk_pause: 82 passed.
- **Safe mode and the new items of a run it holds.** `selfcheck.py`: `INPUT_CHECKS` (= the money checks: the posting
  ledger, EMA's reports and the store's copy of them), `_new_work` returns (listed, held, why), `_hold(hold_ids=)`,
  `_new_items_sentence`, `_safe_state` (`text`, `new_line`), `banner` (the safe-mode line carries `new_line`). A run held
  by an input check holds its new items with it (counted with their dollars; the next run that passes lists them); a run
  held by a closure or state check still lists them; a flood is held as before, now with its dollars; the reason is said
  on the banner and the Data tab either way. Tests on both store kinds.
- **Vercel reads EMA's A/R only.** `cloud.ema_reads_text` (by `ema_reports.switch`: 'ar' says the site reads the A/R only
  and the server reads Production as well, so its lists can differ; 'off' says neither; 'all' nothing), `press_only_text`
  (the setup page's `press_text`). `api_ops.analysis_status` carries `ema`; `static/ops.js` draws it as a line of the
  analysis panel. `PRESS_ONLY_TEXT` itself is unchanged (the fax closing note named in the brief does not use it on this
  tip: no reader found). Tests: the Data tab's panel payload (and 'all', 'off'), the setup status, ops.js drawn by node.

- **The cycle command when a cycle stops** (follows from hosts-1: an owed analysis that keeps failing fails every cycle
  until its fault is fixed). `remitwatch/cli.py` `cmd_cycle`: the stop is noted (the cycle's run log has the detail), the
  day's copy of the store is still made, the fax run is not (no follow-up from lists an analysis could not bring up to
  date), the summary says the cycle stopped and the command exits 1. Runbook: SERVER_RUNBOOK.md (the cycle, Backups).
- **F1's need (fx-ledger-r2-4): the dashboard's copy carries ledger_taken** (commit c3cfe0c, after the tip's Merge
  wip/fx-ledger). paid_proof counts only what a recoupment left of a payment (detect._save_taken, states._r835) and the
  drawer recomputes the live state on the connection it is given; the copy lacked the table, so Vercel's drawer named the
  whole payment ($117.60) where the server names what is left ($87.60). `cloud.SERVING_TABLES` carries ledger_taken for
  the copy's own lines; `cloud.build_serving` makes a table the run makes for itself (`RUN_MADE`) in the copy from the
  store's own definition (a column added later reaches the copy; a store without the table makes its copy as before);
  SERVING_BUILD 11. Tests: F1's need test as written and the copy's rows, columns and key, both store kinds; broken three
  ways (no table in the list, no table made, every row copied), each fails them.

- **F3a's routed needs** (commit f5b8a79, after the tip carried Merge wip/fx-837 at 09def4e; prepared earlier on a
  local staging branch, never needed on origin). The 837 files an older reader read (x12_837.READ_VERSION below 3: the
  claim's patient and its keyed first name, the payer's place on the claim, the other payer and its answer) are read
  again once on each host: `pipeline.reread_837` (the copy the server read, or a file of the same name and bytes under
  REMITWATCH_DATA_DIR or the kept uploads; a deadline of 20 minutes per cycle, the rest next cycle; run in `run_cycle`
  after the waiting reports; claims updated make the cycle analyse) and `cloud.reread_837` (from Dropbox by the name each
  file was read under, 12 downloads at a time in batches of 200 within half the run's budget; a transient error is tried
  by a later run; what is left holds the chain's analysis until the reading is done). Only bytes whose SHA-256 is the
  file's are read (`x12_837.reread_837_bytes`); files not found are counted on every cycle and remembered per reader
  version and host (meta `x837:reread`), so they are not looked for again. `cloud.SERVING_TABLES` carries claims_837 (no
  patient_key, person_key, first_key, file or member id) and lines_837 for the items' bills; SERVING_BUILD 12 (11 is
  ledger_taken). docs/OPERATIONS.md says what a release that reads more of the 837s does. Tests: 15 in
  tests/test_fx_hosts.py (both store kinds, both hosts), fx-837's two F6 host needs tests among them; each part broken
  on purpose fails its tests (13 breaks, `$S/tk/units/fx-hosts/breaks_f3a.py`).

## Measured (private world copy, store of 11-24 with EMA's reports; today 2026-11-27; aggregates only)

One server cycle (pipeline.run_cycle, no new files, as after a deploy) on a fresh copy with the integration tip 59f6822 and
with this branch (76dc904 code; the tip since then changed briefs only). Both analysed (the rules changed since the copy's
last analysis): 16,555 findings in the run, 18,801 in the store, 6,831 listed to work ($2,052,817.03), safe mode off.
- Items: 0 appear, 0 vanish, 0 change (signal, status, work state, stale, dollars, payer group, deadline, category,
  hint compared item by item). Nothing moves by signal or payer group in a normal cycle.
- A17 (plans/tools/claim_audit_a17.py, as of 2026-11-27) on each copy after its cycle: every class identical (verdicts
  and left on the table), e.g. open items judged correct 4,968 ($778,181.74), already done 2,821 ($775,699.68),
  undecided 1,275 ($269,277.16).
- The branch's cycle left no analysis owed (the mark cleared after the save) and recorded supersede:seen [34168, 34168].
- hosts-2's first recheck on the copy decided supersession again (0.8 s) and changed no flag: 74 copies hidden before and
  after. Hand-trace from the raw rows: all 74 by an independent script (same payer, trace number and payment date; every
  claim and PLB of the hidden copy on a complete, balanced standing version, or a claim-less copy that does not add up
  with the same payment): 73 right, 1 unclear (a group with no complete, balanced version: the version with the claims
  stands, the claim-less copy with the same payment is hidden, no claim or dollar lost), 0 wrong; 26 of them read by hand
  (the unclear one, the 3 that do not add up, 22 identical older copies): 25 right, 1 unclear.
- Safe mode on the hook unit's fault (2% of the posting ledger's rows removed, 18,578 rows, after a converging cycle):
  the tip held 16,551 items and still listed the run's 33 new items ($2,279.40: SECONDARY_NOT_BILLED 15, NOT_SENT 7,
  NO_ANSWER 7, REMIT_NOT_POSTED 3, CREDIT_BALANCE 1; Medicare IN 11, Optum 6, Medicare KY 3, Anthem 2, Tricare 2, Humana MA
  2, 7 others 1 each); the branch held them with the run (6,831 listed, $2,052,817.03, as before the fault) and the banner
  says "It holds back the 33 new items ($2,279.40) that run found: they rest on data a failing check distrusts."
  Hand-trace of the 33 moved items: 22 traced to the removed ledger rows of their own line (right to hold); 11 not traced
  to a row by link (837-only claims with no EMA link and check-level items), and a control (the same copy, two cycles, no
  fault, the second analysis forced) lists 0 new, 0 gone, 0 changed of 18,801, so all 33 exist only because of the fault:
  33 right to hold, 0 wrong.
- The dashboard's copy with F3a's 837 sends, measured first on the staging tree (before the merges): 203,485,184 to
  213,143,552 bytes (+4.7%), gzipped 39,152,057 to 41,253,681 (+5.4%); 13,385 of 158,573 837 claims, 36,035 of 327,332
  lines. Measured again below on the final pair's copy (another state of the store, so not compared with this one).

Again after the tip's merge of wip/fx-ledger (tip 47b1ba0 against this branch's c3cfe0c code, one fresh copy each): 18,680
items each, 0 appear, 0 vanish, 0 change; 6,836 listed ($2,049,876.53) on both; A17 identical (open items judged correct
4,965, $774,394.03; already done 2,747, $537,274.37; undecided 1,269, $245,376.02; already worked 91, $27,749.75); safe
mode off; the branch's cycle left no analysis owed.

The final pair (tip fe9ad71, which carries the merges of wip/fx-ledger, wip/fx-837 and wip/fx-exports, against this branch
at f5b8a79; the merges included: 34e3573, 47b1ba0, fe9ad71):
- Items: 18,701 on each copy, 0 appear, 0 vanish, 0 change; 6,860 listed ($2,056,049.68) on both; safe mode off. A17
  identical: open items judged correct 5,007 ($783,174.49), already done 2,736 ($536,212.20), undecided 1,272
  ($245,611.24), already worked 91 ($27,749.75).
- The branch's cycle read no 837 again on this copy (the files are not on it): 5,383 listed, 5,383 counted as not found
  and remembered for this reader version, nothing analysed for it; no analysis owed after; supersede:seen [34168, 34168].
- Cycle time on this machine is set by its load, not by the branch: tip 407.5 s and branch 536.6 s in this pair, then a
  timed pair with the run manifests kept, tip 583.9 s (detect step 498.6 s) and branch 408.9 s (detect 333.6 s).
- The dashboard's copy built from the branch's store after its cycle: the tip's tables 255,242,240 bytes (gzipped
  45,720,509); with ledger_taken 255,279,104 (+36,864; gzipped +5,015): 268 of the store's 342 takeback rows ($21,977.37
  of $32,988.19; 86 of the 342 listed), every one of a line in the copy, none of a copy line left out; with the 837 sends
  265,818,112 (+10,575,872, +4.1%; gzipped 48,041,557, +5.1%): 15,053 claims and 39,296 lines; both 265,854,976 bytes
  (+4.2%), gzipped 48,051,210 (+5.1%).
- The server's read again on real 837 files (`pipeline.reread_837` alone on a fresh copy, the files the integrator had
  fetched by then reachable under the names they were read under): 3,158 of the 5,383 files read in 37.8 s, 93,800
  claims updated (the patient's keys and the payer's place now on 93,800 of 158,573 claims, the other payer on 2,468),
  2,225 not found (not fetched yet) counted and remembered; the next call read nothing (0.07 s). At that pace the whole
  set takes about a minute on a server. What the read moves on the worklist is F3c's measurement (a reread world copy).

## Half done / next (for F6's fix round)

- The vanished check's routes (selfcheck.check_vanished and `_keep_vanished`, F6's), routed from F3a's fix round (brief
  at 34e3573) and F3b's (brief at fe9ad71). Not done in this round: each needs either the detector's own list of the
  items its rules dropped, with the rule (passed in the self-check's context as `delivered_again` is), from the 837
  pass and the exports' functions that F3c is changing now, or a route the check works out from the store itself.
  1. An older bill's never-answered item whose visit a primary payer paid on the bill made again (no item on the new
     bill to fold into): the check keeps it listed. Test (fails on this branch, both store kinds):
     plans/progress/fx-837_needs_tests.py::test_an_older_bills_item_leaves_the_list_when_its_visit_was_paid_on_the_remade_bill.
     A route the check can prove itself: every service (date, code) of the item's 837 claim paid as primary on another
     bill of the same member after the item was raised.
  2. Claims billed at nothing (F3b's answers-3 rule, export_billed_nothing, 10 claims on the world copy): the check keeps
     them. Route: the item's own claim billed under $1 with no line of unknown charge (the rule's own test).
  3. A kept stale row keeps its part and its own size: on F3b's world copy two $0-billed parts (26, TC) of one 88305
     visit came back at $98.00 each, the whole service. To read first: whether detection's stale row or the snapshot
     (an analysis by an older release) carried the whole-service size; then keep the item's part and size from the row
     the item had when people last saw it listed.

## Tests

- `tests/test_fx_hosts.py`: 60 tests on this branch (19 of the ported tests failed on the tip; the unit's own fail
  without each fix; F1's need test and the 15 of F3a's needs among them), both store kinds and both hosts where the code
  runs on both. The ledger_taken tests pass on python3 3.13/SQLite 3.53, Python 3.12/SQLite 3.45, Python 3.11.1/SQLite
  3.40 and the newest libraries.
- Whole suite on f5b8a79 (the tip fe9ad71 merged), python3 3.13/SQLite 3.53 (2 shards): 3,844 passed, 29 skipped, 1
  xfailed, 1 failed: test_swing.py::test_every_page_of_the_server_site_shows_it_at_the_top_until_acknowledged, a
  Playwright "Invalid InterceptionId" error under the load of the measurement runs; test_swing.py alone again: 56 passed.
- Whole suite on f5b8a79, Python 3.11.1/SQLite 3.40: running at this push; the next push gives the result.
- Touched suites on the merged branch (fx_hosts, fx_837, fx_exports, story, cloud, docs, fx_ledger, selfcheck): 856
  passed, 1 skipped. Earlier: on 3e0b872 the whole suite in python3 passed (3,391), on e344223 in 3.11.1 only the
  serialize class failed.
- Node checks (remitwatch/static/ops.js changed on this branch): calc_model_check and lab_review_check pass.

## Needs from others

- Integrator: F1's need test (plans/progress/fx-ledger_needs/need_serving_copy_ledger_taken.py) is in
  tests/test_fx_hosts.py as test_the_drawer_names_the_same_proof_on_the_server_and_on_the_dashboards_copy and passes; the
  need file can be retired with F1's other met needs. Two of fx-837's three F6 needs tests are in tests/test_fx_hosts.py
  (the dashboard's copy shows every send in an item's story; the server reads an 837 again), so their copies in
  plans/progress/fx-837_needs_tests.py can go; the third (the remade-bill vanished route) stays there for F6's fix round.
- F3c / integrator (for the vanished check's routes in F6's fix round, above): if the 837 pass and the exports' rules
  pass the items they drop, with the rule, in the self-check's context (as detect._answer_copies passes
  `delivered_again`), the check can route them out by rule instead of working the route out from the store.
- F5 (features.js, the header line on every Vercel page): the line says "analyses only when someone presses Run the
  analysis now" from `st.press_only`; it could add what the site reads of EMA's reports (the A/R only). The text is in
  `cloud.ema_reads_text()`; /api/sync/status (api.py) would carry it as `ema_reads`. Not required by a failing test.

## Open questions

- A server cycle while an analysis is owed and the analysis keeps failing (a code bug): every cycle tries again and fails
  loudly (before: one failure, then quiet cycles with stale lists). The day's copy of the store is still made; the fax
  run waits for a cycle that finishes (cli.cmd_cycle). Safe mode does not cover a crash of the analysis itself: the
  worklist stays the last saved one, and the run log and the bundle say where it stopped.
