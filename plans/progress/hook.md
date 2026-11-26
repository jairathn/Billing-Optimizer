# Progress: unit hook (E, G and H wired into both cycles, then the owner's decision 1)

Brief: plans/briefs/2026-11-26_hook.md (with plans/brief_HOOK_2026-11-24.md and plans/briefs/2026-11-26_common.md).
Branch: wip/hook. Last updated: 2026-11-26 19:58 UTC (the two earlier stamps here were written ahead of the clock).

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

## Real-data runs (2026-11-26, private copy in $S/tk/units/hook, machine load 7 to 9 on 4 CPUs: times are slow)
- Server, world copy (store 11-24 + EMA reports, live code of 11-24 before): cycle 1 (first run of this tree) 350 s,
  peak 1,103 MB, outcome ok; items 17,281 -> 18,514 (2,867 added, 1,634 gone, all gone with a route except 8). Cycle 2
  (nothing new, no analysis) 39 s, changed nothing. Cycle 3 (forced analysis) 556 s, peak 1,129 MB: 0 added, 0 gone,
  0 changed (status, state, stale, shortfall, deadline): the cycles converge.
- Self-checks on cycle 1: vanished FAIL 1,892 open items ($203,702) the new rules no longer produce, kept on the
  worklist (by signal: UNDERPAID_VS_OWN_RATE 1,248, NO_ANSWER 364, MPPR_EXCESS 85, CLAIM_DENIED_UNPAID 73,
  REJECTED_PRE_PAYER 72, SECOND_RATE 29, others 21); left_record WARN 8 record items ($4,594: TAKEBACK 7, G2211_DENIED 1);
  line_balance WARN 37 of 8,050 A/R lines ($17,539) disagree with the postings (data, EMA's side). Cycle 3: vanished
  still fails (the kept items wait for a person's Accept), left_record passes. Not this unit's: the rules changed
  between the live code of 11-24 and the integration tip; the integrator decides (accept on the Data tab, or the rule
  owners look at the signals above).
- Serverless, Vercel's image (722 MB in memory, 70% of the 1,024 MB a run saves in one piece): press 218 s, peak
  1,931 MB, analysis 170 s; idle run 76 s (no analysis; the archive moved 15,862 bills, 852 items; the weekly
  self-audit ran); second press 15 s, analysis handed on (the regulation at "archive" after two runs near memory);
  the next run analysed in 240 s of the 240 s budget, peak 1,995 MB. Near both limits on this loaded machine.
- Serverless, the image with EMA's tables (893 MB in memory, 87% of the ceiling), EMA reports on: the first press ran
  out of time (OutOfTime at 242 s, peak 1,876 MB, nothing saved). Chain continuing.
- Faults on a copy of the converged store: stale_lock: the cycle with a fresh dead lock is skipped, the start-up rule
  removes it, the next cycle is ok, no item changed. network: two dropped connections on the Dropbox listing are tried
  again (repair log: 2 retrying, 1 repaired), outcome ok, no item changed.

## Next
1. Real-data runs on a private world copy ($S/tk/units/hook/, mode 700, one copy at a time, df -h / first): server
   cycles twice (plans/tools/world/cycle_run.py with F5_SRC=this tree), the serverless image chain from
   $S/tk/world/store_live_1124.sqlite (or $S/tk/data/store.sqlite.gz) until more is false plus an idle run, a pressed
   run (force_detect) on it, the image with EMA tables (plans/tools/world/build_img_ema.py), the five faults, forced
   memory, the PHI scan of every log, manifest and bundle written.
2. Suites in 3.13, 3.12, 3.11.1 and the node checks.

## Numbers measured so far
- (from the earlier runs, see plans/HANDOFF.md section 0 item 2): three server cycles converge; five faults detected,
  contained or repaired; forced high memory sheds to level 4; NEAR_CEILING at 76% of 1,024 MB; PHI scan of 129 files: 0.

## Open questions
- (none yet)
