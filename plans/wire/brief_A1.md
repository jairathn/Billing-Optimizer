# Unit A1 brief: intake-core (the EMA reports enter the cycle on both hosts)

S = /tmp/claude-0/-home-user-Billing-Optimizer/96e81489-2ad2-5c70-991d-e00db2e22749/scratchpad. Read ${S}/wire/PLAN.md first.

## Your files
remitwatch/ema_exports.py, dropbox_sync.py, cloud.py, pipeline.py, store.py, state.py, cli.py, new remitwatch/intake.py,
remitwatch/ema_reports.py and remitwatch/ema_ledger.py (wiring edits; the EMA fixer has finished them: read
${S}/build/fix_review_report.md first), the one-line `ema_reports.ensure_schema` call in `detect.ensure_schema` (unit B owns the
rest of detect.py), .env.example (EMA variables only), vercel.json and api/index.py only if a function setting must change.
Tests: tests/test_cloud.py, test_sync.py, test_parity.py, test_ema_reports.py, test_ema_ledger.py, tests/ema_fixtures.py, new
tests/test_ema_intake.py. Not yours (forward needs in ${S}/wire/needs_A1.md): uploads.py and the Upload page (unit A2),
api.py (unit C), codes.py/detect.py/insights.py/actions.py/alerts.py (unit B), states.py/story.py (unit D).

## Read first
- ${S}/build/needs_ema_reports.md and ${S}/build/needs_ema_ledger.md in full: they list the exact changes to your files.
- Amendments ${S}/build/amendments.md sections 0, 4 (4.1, 4.2, 4.4), 5 (Steps 0, 1, 3, 4), 10, 11, 13.1.
- The scale review's numbers (${S}/build/review-scale/ and the EMA fixer's report): an apply run peaked at 1,229-1,328 MB on
  Vercel's limits, a full cycle with EMA tables at 1,460 MB (limit 2,278 MB); Vercel's /tmp is 512 MB.
- ${S}/wire/needs_A2.md and ${S}/wire/report_A2.md if they exist (the upload hand-off is already written against PLAN.md).

## Build, in this order, each with tests in both modes
1. Recognition first (Step 0). `ema_exports.detect_family` recognises the two reports before the old weekly-export families;
   the old readers refuse report files with a plain message; `iter_export_paths` takes .csv/.tsv/.zip/.gz only when they are
   reports. `dropbox_sync`: WANTED_EXT and `wanted()` admit report files (.xlsx, .csv, .zip, .gz) and still keep the old 835
   backfill zip out; the router sends reports to `ema_reports.ingest_file`; old families over 60 MB refused with a plain
   message; files of an unknown layout recorded as `ema:unknown` / `not_read` (listed on the Data tab, not silently skipped);
   download timeouts per needs_ema_reports.md.
2. The misread-rows migration (E4), once per host (meta `ema:migrated_v1`): purge rows stored from report files that an older
   reader misread as weekly exports; a genuine Claim & Claim Status export (no claim ids, the 23-row kind) must survive. Prove
   it on a copy of ${S}/live_state_1122/store.sqlite (rows per source before and after) and with the fixture.
3. Find the report files the Dropbox cursor already passed. Both live sites' cursors have moved (or will at the next run) past
   the four files in "/Trizetto Data/Trizetto/AR and Collections Reports from EMA/" without reading them. On the first wired run
   (meta flag), list the root recursively (all pages) for files that are reports by name and extension, are not in `files` as
   applied reports, fetch and recognise them, then ingest. On Vercel at most one Production file per run (the rest wait for the
   next run, `more=True`). Test with a fake Dropbox client whose cursor is already past the files.
4. Server cycle (Step 3): the pipeline opens the ledger at `ema_ledger.ledger_path(db)` once per cycle, applies every Production
   file of the cycle, runs `refresh` before detection (or sets meta `ema:map_dirty` so detection's step 2 does it: agree the
   exact contract with unit B through ${S}/wire/needs_A1.md and B's report), keeps the ledger in backups (`cli backup`) and
   leaves key columns out of `import-cloud` / `compare-cloud`. `cli ema-reports --status/--summarize/--reread/--decide`.
5. Serverless cycle (Step 4): `LedgerHandle` only on runs that apply a Production file; `note_attempt` before any read (a run
   killed mid-read is counted on the next); save order ledger, meta rev, store, dashboard copy, cursor; one Production file per
   run; the repair path for a run that died after saving the ledger; Production files over 60 MB that are not compressed wait
   with the message "Too big to read on Vercel: zip or gzip it, or upload it in the app (the app compresses it)". Gzipped and
   zipped files under 60 MB are read streaming. The ledger must stay inside the 512 MB /tmp (check the EMA fixer's fix).
6. The section-10 seam: `ema_ledger.apply_postings(handle_or_conn, source_id, box, postings, *, source_kind)` and
   `ema_reports.apply_ar_snapshot(conn, source_id, as_of, rows, *, source_kind)`; the file readers become the one adapter that
   calls them; every applied batch records its source kind (file, upload, api). No API adapter.
7. Section-11.2 de-duplication, tested and proven on the real files: (a) a content hash of the parsed header and rows (immune
   to xlsx metadata, zip times and CSV line endings) so a re-saved identical export reports "already" and changes nothing;
   (b) the whole-year export again replaces its box and nothing is counted twice; (c) posted-date pulls after a year file, and a
   year file after posted-date pulls, end with the same totals as one fresh full export (cut a last-five-weeks posted-date
   slice of prod2026.csv in your work folder; apply in both orders on two fresh worlds; compare ledger totals by payer group
   and month to the cent); (d) an upload and the same file in Dropbox apply once, whichever comes first (the sidecar's
   original-bytes sha256 = the Dropbox file's sha256); (e) each A/R snapshot dated, the newest current, older kept as history;
   (f) the same 835 again ignored, as today. Never de-duplicate individual postings by content (487 identical postings are real).
8. The upload hand-off (PLAN.md): new `remitwatch/intake.py` with `process_ready(store, ...)` for the server (called at the start
   of each pipeline cycle and by `intake.kick(data_dir)`, which A2 calls after a finish; takes the pipeline's lock or queues)
   and the serverless run's reading of `<REMITWATCH_STATE_PATH>/intake/` before the Dropbox delta (stream .gz, verify the
   sidecar sha256 while reading, origin 'upload', move each pair to intake/done/ after it is applied or refused, and record the
   result where the Upload page can show it: the catalog row with origin, upload_id, user and the plain result).
9. Deterministic fixtures stay deterministic (tests/ema_fixtures.py FIXED_SAVE_TIME / stable_xlsx).

## Acceptance on the real data (aggregates only; PHI stays in ${S}/wire/A1/)
- Step 0: the four files recognised (two `production`, two `ar_report`); 0 report rows left in `ema_rows` after the migration;
  the genuine 23-row export untouched (on a copy of the live store).
- Step 1 (from amendments section 5): A/R rows 9,653 (9,411 + 242), claim ids 4,229 + 61, Current A/R $1,115,513 and $3,218,
  denial-only exposure $87,370 and $91,388, aging window 30 on both files.
- Step 3: map at least 95% of covered 2025+ bills, 0 facility links mapped to a professional bill, lean lines about 74.5K.
- Step 4 (Vercel gate with the zipped/gzipped files): peak RSS under 1.75 GB per run, apply under 120 s per run, /tmp under
  512 MB, an attempt recorded before every read, a killed run repaired by the next.
- The dedup scenarios (a)-(f) with the real files.
- The full manual workflow in both modes, starting from a copy of ${S}/live_state_1122/store.sqlite and its cursor:
  server `pipeline.run_cycle` with the Dropbox mirror ${S}/wire/mirror/ behind a fake client; serverless `run_cloud_cycle` with
  a LocalState and the same fake client, run repeatedly until `more` is false. Report per run: files listed, fetched, ingested,
  rows, boxes, holds, seconds, peak memory, /tmp peak. Then one more cycle: nothing changes (idempotent).
- The re-find scenario: cursor already past the four files; the first wired run finds and applies them.

## ADDED 2026-11-22 22:55 (the practice's team is dropping new TriZetto and EMA files into Dropbox now)
- Never skip a file silently. .zip files in Dropbox: read their members (835s, weekly exports, reports) with per-member
  de-duplication by content; only the original first-load backfill archive keeps its read-once rule. .csv/.tsv in Dropbox: the
  three weekly exports as CSV are read too (recognised by their headers), not only the reports. Any file that is still not read
  (unknown layout, too large, unreadable) is listed on the Data tab with the reason and an alert, never just logged.

## Done means
All tests pass in the four environments of PLAN.md; ${S}/wire/report_A1.md (no identifiers) and ${S}/wire/needs_A1.md written;
the world copies you made deleted.
