# Progress: unit hook (E, G and H wired into both cycles, then the owner's decision 1)

Brief: plans/briefs/2026-11-26_hook.md (with plans/brief_HOOK_2026-11-24.md and plans/briefs/2026-11-26_common.md).
Branch: wip/hook. Last updated: 2026-11-26 21:15 UTC (two earlier stamps here were written ahead of the clock).

## Done
- 2026-11-26 18:35 UTC: merged the integration tip (fc73dec) into wip/hook, no conflicts. After the merge, in python3:
  tests/test_hooks.py, test_selfcheck.py, test_runlog.py, test_diagnose.py, test_repair.py, test_archive.py: 423 passed;
  test_cloud.py, test_answer_needs_proof.py, test_ema_checks.py, test_docs.py, test_aws.py: 200 passed, 2 skipped.
- Earlier (2026-11-24/25, commits 5c75342..71b6971): the hooks of needs_E, needs_G and needs_H on both hosts; the 1 GiB
  fix in Store.from_bytes; the ceiling budgets (5657a45); api_ops.py, ops.js, the CLI commands (5954bf4); regulation carried
  over idle Vercel runs; self-check facts count only new input; a left_record check.

- 0ae525a: items 2-6 checked against needs_E/G/H. Gap found and fixed: an archived bill found by Find a claim opened as a
  404 (api.claim_view now reads the archive's record through an in-memory copy held for the request, api._ARCHIVED_DB);
  archived search rows carry the page's fields; an archived item's panel (ops.js opArchivedItem) and the claim view say
  so and offer "Bring it back to the lists". Test: test_hooks.py test_an_archived_claim_found_by_find_a_claim_opens_whole...
  and the browser check (tests/ops_page.js) opens both at 1400 and 390 px.
- 8f38502: /api/cron/faxes wraps the fax line's reads in repair.resilient as cli._fax_run does (needs_H L2); test
  test_the_daily_fax_runs_reads_are_tried_again_and_a_send_never_is on both entries.

- f719f28, dd53d57, 59e7990: the owner's decision 1 is built (server side, pages, docs), with tests on both hosts:
  - vercel.json: only /api/cron/faxes (15:43 UTC) stays; it runs no analysis and sends nothing unless
    REMITWATCH_FAX_SENDER=serverless. No cron "only keeps the site healthy", so none other is kept.
  - POST /api/ops/run (api_ops.ops_run, press_run; cloud.press, run_going, _answer_press, press.json): Vercel only; 404
    on a server. /api/sync/run on Vercel is the same press (the Feed, Upload and /setup buttons).
  - Nothing else starts a run on Vercel: freshen, the Dropbox webhook (api._webhook_work removed), an upload's finish
    (api_uploads._wake), a held report's apply.
  - cloud.run_cloud_cycle: forced analysis handed on when short of time (counts as progress); status.json "analysed"
    and "unchecked" (cloud.unchecked_note, _last_detection).
  - Pages: ops.js opAnalysisHtml/opBindRun ("Analysis on this site"), features.js header line and Feed, upload.js and
    setup.html (#pressLine). Docs: VERCEL_SETUP.md, OPERATIONS.md, README.md, ARCHITECTURE.md.
  - Tests: test_hooks.py section 7 (7 API tests, 1 browser test on a serverless site via tests/press_page.js); existing
    tests updated for the decision (test_cloud webhook and sync/run, test_upload_chunks x3, test_ema_pages held report,
    test_uploads Vercel answer, test_results note). Each part broken on purpose failed its test; restored.
  - NOT changed: CLAUDE.md line 87 still says "the daily cron calls /api/cron/cycle (10:17 UTC; vercel.json)". Agents
    may not edit CLAUDE.md: the owner or integrator should change that line to the press.
  - Release note line (for the integrator's release message): "The Vercel site no longer analyses on a schedule: a
    signed-in person presses Run the analysis now on its Data tab. The server analyses every day as before. Billers work
    on the server's site: what is recorded on the Vercel site is not on the server."

- 71c8c1d: three fixes the real-data runs found in decision 1 (each with a failing test first, broken on purpose and
  restored): the press button had the same attribute as the owner's "Accept what run ... found" (data-op-run), so on
  the server's Data tab every Accept also posted /api/ops/run (404); now data-op-press, scoped to #opAnalysis. While a
  run was going the Vercel header said only "analysing now" (no last analysed time, no press-only line); now it says
  all three. A deferred press with nothing new read said "N open items were not checked against the files read" and
  "to keep within memory" whatever the reason; now the pending marker says whether files are behind it ('1') or a press
  alone ('press'), and the note names what happened and the reason (cloud.unchecked_note, DEFER_REASONS, SPREAD_REASONS).

- a08d8c3, 7620b56, b07466d: three more from the runs, each with a failing test first (both store kinds where the store
  is read), broken on purpose and restored: the note counts open items a release has not given a state yet by their
  status (it said "0 open items ($0)" on the first run after this release); a pressed run that stops before it finishes
  (OutOfTime) is said on the Data tab's panel (why, the open items and dollars that stand as the last analysis left
  them, what to do) and in the header (cloud.failed_note, cloud.open_items, api_ops.analysis_status(conn),
  /api/sync/status last_failed); a run that only left the weekly self-audit no longer counts every open item as
  unchecked (the header said "could not check 7,526 open items").
- 2ace6ab: merged the integration tip 67cc3d2 (plans, the review, the privacy scan in test_docs): no conflict;
  tests/test_docs.py 38 passed, 1 skipped.

## Real-data runs (2026-11-26, private copy in $S/tk/units/hook, deleted after; machine load 7 to 15 on 4 CPUs, so wall
## times are long: CPU seconds are given where they matter)
- Server, world copy (store 11-24 + EMA reports; its items were made by the live code of 11-24): cycle 1 (first run of
  this tree) 350 s, peak 1,103 MB, outcome ok; items 17,281 -> 18,514 (2,867 added, 1,634 gone; every gone item had a
  route except 8). Cycle 2 (nothing new, no analysis) 39 s, changed nothing. Cycle 3 (forced analysis) 556 s, peak
  1,129 MB: 0 added, 0 gone, 0 changed (status, state, stale, shortfall, deadline): the cycles converge.
- Self-checks on cycle 1: vanished FAIL 1,892 open items ($203,702) the new rules no longer produce, kept on the
  worklist (UNDERPAID_VS_OWN_RATE 1,248, NO_ANSWER 364, MPPR_EXCESS 85, CLAIM_DENIED_UNPAID 73, REJECTED_PRE_PAYER 72,
  SECOND_RATE 29, others 21); left_record 8 record items ($4,594: TAKEBACK 7, G2211_DENIED 1); line_balance 37 of
  8,050 A/R lines ($17,539) disagree with the postings (EMA's data). Cycle 3: vanished still fails (kept items wait
  for a person's Accept), left_record passes. Not this unit's: rules changed between the live code of 11-24 and the
  integration tip; the integrator accepts on the Data tab or the rule owners look at the signals above.
- Serverless, Vercel's image (722 MB in memory, 70% of the 1,024 MB a run saves in one piece): first press 218 s,
  peak 1,931 MB, analysis 170 s; idle run 76 s (no analysis; the archive moved 15,862 bills and 852 settled items;
  the weekly self-audit ran); second press 15 s, analysis handed on (the regulation at "archive" after two runs near
  memory); the next run analysed in 240 s of the 240 s budget, peak 1,995 MB. With a long budget: 127.7 CPU seconds
  for the whole run (analysis 179 s wall at load 12), peak 1,934 MB. On a CPU of its own the analysis fits the
  budget; memory is within about 250 MB of the 2,188 MB budget.
- Serverless, the image with EMA's A/R and Production tables (893 MB in memory, 87% of the ceiling): with EMA reports
  'all', and again with 'ar' (Vercel's default), every analysis ran out of time at 240 s (5 runs; nothing saved; the
  regulation went to "alert", NEAR_LIMIT raised). With a 900 s budget: 280 CPU seconds, peak 2,317 MB. It does not
  fit one Vercel run (time or memory). NEAR_CEILING raised (high): 893 MB, 87%, 6,426 open items ($2,034,832). Agrees
  with review hosts-6: a decision for the integrator (keep settled EMA lines off the serverless image, or the A/R
  only there, orchestrator decision 6).
- Forced memory (the last peak set to 2,150 MB before each run, on Vercel's image): level shed, then alert, and it
  stays there; shed: provider tables, report email, export-time backfill, weekly self-audit; NEAR_LIMIT alert (high)
  with the pressure in plain words; a press at "alert" hands the analysis on (more: true) with its note.
- Faults on a copy of the converged store: stale_lock: the cycle with a fresh dead lock is skipped, the start-up rule
  removes it, the next cycle is ok, nothing changed. network: two dropped connections on the Dropbox listing tried
  again (repair log: 2 retrying, 1 repaired), outcome ok, nothing changed. corrupt_production: a cut-short Production
  file is held, outcome partial with 1 logged error, nothing changed. ledger (2% of the posting ledger's rows
  removed): check ledger_store fails, the derived-table rebuild gave up once (repair log), safe mode on (16,627 items
  held, 0 closures held). codebug (the 837 open-claims rule emits nothing, fresh copy): vanished catches 217 more open
  items and keeps them on the list (kept 2,109), left_record counts 551 NO_ANSWER record items, CHECK_FAILED alert
  (high); the open list grew by 101 items ($28,899) as other checks list claims the 837 pass covered.
- PHI scan of every log, manifest, status file and bundle written (server copies, both state folders, four bundles
  made by `python -m remitwatch.diagnose`): 135 files, 1.46 MB, against 11,043 name words and 425,191 ids from the
  store: no patient identifier. Every match is explained: 155 inside the code's own identifiers (signal and alert
  kinds), 4 in the bundle README's fixed text (code words), 1 numeric token inside the store's transaction keys (an
  ST control number equal to a patient id by chance).

- 2982485, 1a6d92e, ff7f97c: three contained errors logged through the scrubber (cloud._last_detection, the cron
  cycle's late-file continuation, the self-check's input position); VERCEL_SETUP says why a run hands the analysis on,
  what a run that stops says, and the measured limits; review privacy-3 (the integrator's merge blocker) is covered on
  this branch: selfcheck.has_proof accepts the payer's own dated answer that settled an item (settled_by), and
  test_a_closure_settled_by_the_payers_own_zero_answer_carries_its_proof_and_holds_no_run runs the S01 and P01
  scenarios on both store kinds (closure_proof passes, no hold); removing the clause holds the run (checked, restored).

## Suites (2026-11-26, this branch)
- python3 (3.13, SQLite 3.5x), whole suite in 3 shards: 2,823 passed, 55 failed, 29 skipped, 1 xfailed. The 55: 34 in
  test_fax.py, 9 test_hotfix2.py, 5 test_no_fax_route.py, 4 test_notes_bodies_cli.py, 2 test_hotfix2c.py, 1
  test_bulk_pause.py. Not this unit's: the same 44 of fax/no_fax_route/notes_bodies/bulk_pause fail on the integration
  tip itself (35fc020, extracted with git archive), and hotfix2/hotfix2c are review hosts-5 (dated fixtures against the
  real clock, the stale-feed hold). With the practice's clock pinned to 2026-11-20 (a pytest plugin outside the
  repository) those six files give 139 passed, 1 failed on this branch; the one
  (test_fax.py::test_a_decision_on_a_claim_reaches_the_lines_it_names) fails the same way on the tip with the pin.
- Python 3.12 / SQLite 3.45 (venv2_compat312), the touched files (test_hooks, cloud, selfcheck, runlog, archive,
  diagnose, repair, upload_chunks, uploads, ema_pages, results, docs): 894 passed, 1 skipped.
- node tests/calc_model_check.js (79 checks) and node tests/lab_review_check.js (143 checks): none failing.

## Next
1. The whole suite in venv_sqlite340 (running), then the final report.

## Numbers measured so far
- (from the earlier runs, see plans/HANDOFF.md section 0 item 2): three server cycles converge; five faults detected,
  contained or repaired; forced high memory sheds to level 4; NEAR_CEILING at 76% of 1,024 MB; PHI scan of 129 files: 0.

## Open questions
- (none yet)
