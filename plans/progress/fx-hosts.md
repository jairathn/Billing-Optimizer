# fx-hosts (fix wave F6, hosts): progress

Branch `wip/fx-hosts`, from the integration tip 8c59195 (`origin/claude/ecstatic-fermi-orfcgo`). Brief:
`plans/briefs/2026-11-26_fixwave.md`, section "F6 hosts"; rules in `plans/briefs/2026-11-26_common.md`.
Tests: `tests/test_fx_hosts.py` (the reviewer's since-hosts-tests and the verifier's since-hosts-verify-tests, ported;
invented data only). Private folder: `$S/tk/units/fx-hosts/` (mode 700).

Last updated: 2026-11-27 02:20 UTC.

## Done

- **hosts-1** (an analysis that stopped is owed). `remitwatch/pipeline.py`: `DETECT_PENDING` (= `cloud:detect_pending`, the
  serverless cycle's own mark and `archive.META_DETECT_PENDING`: one key, one meaning on both hosts), `analysis_owed`,
  `owe_analysis`, `analysis_paid`; `run_cycle` writes the mark (committed) before any reader may commit input, analyses
  while it is set, and clears it after `run_detection` returns, or when the cycle read nothing that needs an analysis and
  none was owed. `out["analysis_owed"]` says a cycle ran an analysis a stopped one owed. Tests: the reviewer's two, the
  verifier's (stop in the rules, stop in the save, SIGKILL in a child process as the scheduler sees it, the fax guard
  control/stopped), a stop after reading and before the analysis (archive requests, EMA's waiting reports), quiet cycles
  analyse nothing and leave no mark, an analysis that keeps failing is tried again and said every cycle, a store imported
  from Vercel with its analysis owed. The fax guard (hotfix 2d D4): the verifier's chase test passes in both arms (the
  analysis runs in the next cycle and the paid item closes, so no chase fax goes).
- **hosts-2** (supersession decided after a stop). `remitwatch/store.py`: `SUPERSEDE_SEEN` (`supersede:seen`, [count,
  newest rowid] of transactions, written with the flags in `mark_superseded`'s commit, read before its query);
  `recheck_supersession` decides again when the rule changed or when a transaction exists beyond what the last decision
  saw. Both store kinds (file and compact). A store decided by the release before has no record: decided once more
  (the flags come out the same; measured cost on the world: see below).
- **hosts-3** (tools that read a copy). `remitwatch/diagnose.py` `main`: an image the last run did not save is read with
  check=False and the bundle says so first (README "Read this first", bundle.json `notes`); a cut-short one makes a bundle
  from the logs; a missing one is named. `build_bundle(notes=...)` keyword (our own sentences only). `remitwatch/cli.py`:
  `_cloud_image(check=)`; `compare-cloud` compares the image as it is, says "Not a proof of the move: <sentence>" first and
  last, exits 1; `import-cloud` prints the check's sentence and exits 1, nothing written.
- **Flaky 1** `tests/test_results.py::test_the_server_store_and_the_compact_serverless_store_give_the_same_figures`: the
  results clock (`results._now`) pinned for both runs, so every stamp (run `at`, point `at`, `frozen_at`) is the same and
  compared (the old strip of the snapshots' `at` and the top `at` removed: the comparison covers more). Broken on purpose
  (no pin, a 1.1 s gap between the runs): fails on the snapshots' `at`; with the pin and the gap: passes.
- **Flaky 2** `tests/test_feed_and_day.py::test_the_pages_say_how_far_the_remittances_reach`: `tests/feed_page.js` waits
  (20 s at most) until Today's health banner says where the remittances reach (Today holds the feed's check back until the
  blind spot is known), and until the Worklist's line is drawn, then reads as before (same assertions). Broken on purpose
  (the counts answer delayed 3 s by a Playwright route): the old script fails on the banner, the new passes (both hosts).

## Half done / next

- Flaky 3 `tests/test_hook_review.py::test_presses_rebuild_the_provider_tables_when_the_analysis_fills_the_run`: tracing
  the chain's runs (levels, skips) with `$S/tk/units/fx-hosts/probe2/test_probe_hook.py`.
- hosts-5: confirm the hotfix2 tests pass against the real clock (today 2026-11-27).
- Safe mode and the new items of a run it holds (selfcheck `_new_work`/`_hold`/`_safe_state`): test first.
- Vercel reads EMA's A/R only: `cloud.PRESS_ONLY_TEXT`, `api_ops.analysis_status`, `static/ops.js`.
- F3a's routed needs (after `Merge wip/fx-837` is on the tip): 837 files read again on each host; `cloud.SERVING_TABLES`
  with claims_837/lines_837.
- Measurement on a world copy, hand-trace, suites.

## Tests (python3 3.13)

- `tests/test_fx_hosts.py`: 23 passed (19 failed on the tip before the fixes; the 4 that passed are the verifier's scope
  and parity controls).
- Related files (test_api_review, archive, cloud, cycle_converges, diagnose, ema_intake, feeds, hook_review, hooks,
  hotfix2c, hotfix2d, parity, runlog, selfcheck, swing, uploads, aws): running.

## Needs from others

(none yet)

## Open questions

- A server cycle while an analysis is owed and the analysis keeps failing (a code bug): every cycle now tries again and
  fails loudly (before: one failure, then quiet cycles with stale lists). The backup and the fax run come after the cycle
  in `cmd_cycle`, so they are skipped while it fails, as they were on any cycle with new files.
