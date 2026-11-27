# fx-hosts (fix wave F6, hosts): progress

Branch `wip/fx-hosts`, from the integration tip 8c59195, merged with the tip 59f6822 (plans only). Brief:
`plans/briefs/2026-11-26_fixwave.md`, section "F6 hosts"; rules in `plans/briefs/2026-11-26_common.md`.
Tests: `tests/test_fx_hosts.py` (the reviewer's since-hosts-tests and the verifier's since-hosts-verify-tests, ported, plus
the unit's own; invented data only). Private folder: `$S/tk/units/fx-hosts/` (mode 700).

Last updated: 2026-11-27 02:50 UTC.

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

## Half done / next

- F3a's routed needs, after the tip carries "Merge wip/fx-837" (not yet at 02:50 UTC): read the 837 files again once on
  each host (`x12_837.files_to_reread`, `reread_837_bytes`: the server from the path its `files` row names, Vercel from
  Dropbox), and `cloud.SERVING_TABLES` with claims_837 (no patient_key, person_key) and lines_837 for the findings'
  bills; tests in `plans/progress/fx-837_needs_tests.py` on wip/fx-837; measure the serving copy before and after.
- Measurement on the world copy (`$S/tk/units/fx-hosts/cycle_world.py TREE LABEL`: one server cycle, today 2026-11-27,
  findings snapshot `snap_<label>.sqlite`): tip running, then branch; then the diff and A17 classes.
- Suites at the end: python3 full, 3.11.1/SQLite 3.40 full; node checks (static/ops.js changed).

## Tests (python3 3.13)

- `tests/test_fx_hosts.py`: 42 passed (19 of the ported tests failed on the tip; the unit's own fail without each fix).
- Related files on the first WIP (api_review, archive, cloud, cycle_converges, diagnose, ema_intake, feeds, hook_review,
  hooks, hotfix2c, hotfix2d, parity, runlog, selfcheck, swing, uploads, aws): 783 passed, 27 skipped, 0 failed.
- Second related run (fx_hosts, selfcheck, hooks, hook_review, cloud, cycle_converges, runlog, diagnose, results,
  feed_and_day, fax, merge_vanished_policy, docs) on 564047f: running.

## Needs from others

- F5 (features.js, the header line on every Vercel page): the line says "analyses only when someone presses Run the
  analysis now" from `st.press_only`; it could add what the site reads of EMA's reports (the A/R only). The text is in
  `cloud.ema_reads_text()`; /api/sync/status (api.py) would carry it as `ema_reads`. Not required by a failing test.

## Open questions

- A server cycle while an analysis is owed and the analysis keeps failing (a code bug): every cycle tries again and fails
  loudly (before: one failure, then quiet cycles with stale lists). The backup and the fax run come after the cycle in
  `cmd_cycle`, so they are skipped while it fails, as they were on any cycle with new files.
