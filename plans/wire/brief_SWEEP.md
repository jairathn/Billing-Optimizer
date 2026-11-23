# Unit SWEEP brief: every open request aimed at the intake files is done, tested and measured (runs beside INT)

S = /tmp/claude-0/-home-user-Billing-Optimizer/96e81489-2ad2-5c70-991d-e00db2e22749/scratchpad. Read ${S}/wire/PLAN.md first (the
owner's words, NON-NEGOTIABLES, the revised schedule, release gates, CPU rule, needs sweep and orchestrator decisions at the end).

## Why
Unit A1 (intake) built and fixed its part; its fix stage read only its own reviews. Other units asked A1 for changes in their
needs files, and the A2 security review found a cross-unit problem. Nothing asked for may be dropped silently.

## Your files (A1's, now yours; A1 is finished)
remitwatch/intake.py, ema_exports.py, dropbox_sync.py, cloud.py, pipeline.py, store.py, state.py, cli.py, ema_reports.py,
ema_ledger.py, .env.example (EMA lines); tests: test_ema_intake.py, test_cloud.py, test_sync.py, test_parity.py,
test_ema_reports.py, test_ema_ledger.py, test_ema_vercel.py, tests/ema_fixtures.py, and tests/test_feeds.py (no other owner).
Not yours: detect.py, states.py, story.py, archive_rules.py, insights.py, codes.py, ema_checks.py (unit INT is editing them
now), uploads.py / api_uploads.py / upload.js (A2), api.py and pages (C1, C2), the 3a modules (E, G, H). Ask in needs_SWEEP.md.

## Do, in this order (each: check the code first, it may be done already; then a failing test with invented data, the change,
break and restore)
1. Read EVERY ${S}/wire/needs_*.md and report_*.md (A1, A2, B, B2, C1, C2, D, E, G, H, R, INT as they exist) and list every item
   aimed at your files with its status: done already (where), done now (test name), not needed (why), owner decision (what).
   Put that list at the top of needs_SWEEP.md.
2. The A2 security review's high finding: a small upload (a gzip bomb or one giant line) must never stop a cycle on either host.
   Cap decompressed bytes per file (a ratio and an absolute cap well above the real reports: the 206 MB CSV gzips to 14.3 MB,
   about 14x) and line length in every reader path that unpacks or reads lines (intake, the ema_reports readers, zip members in
   dropbox_sync); an over-cap file is held with a plain reason, named on the Data tab, and the rest of the cycle goes on. Test
   on both hosts with invented bombs.
3. needs_D.md for A1: (a) classify each A/R Last Billing Note when it is read: states.classify_note(note, as_of) stored as
   ema_ar.note_kind, note_date, note_wait (DDL + ensure_schema + migration; never the note text), kept in
   ema_reports.serving_tables; (b) the link map rows for bills with no 835 (patient-day match, never the facility claim) so
   NO_ANSWER items get their ema_link (cases C40 to C43, $4,894.57 paid but still "call the payer"); (c) the wider serving
   tables (every claim, line and adjustment of a bill that has an item) ONLY IF the serverless peak memory stays under the
   1.75 GB gate with it: measure the dashboard copy's size and the run's peak on ${S}/live_state_1122/store.sqlite (a copy)
   before and after; if it does not fit, keep the narrow copy and say so.
4. A1's open items: the key salt on Vercel (create and save the store's salt before the first ledger save, so a run that dies
   after the ledger save never empties the ledger); retention of done/ pairs on both hosts (keep 180 days, never the newest copy
   of each report, never the copy of a held or waiting file); the needs_A2.md items A1 did not finish.
5. Vercel's raw Production CSVs over 60 MB wait by design (amendment X2). Make the waiting message on the Data tab and Today's
   freshness line say exactly what to do: "Upload it on the Upload page (it compresses it), or put a zipped copy in the same
   Dropbox folder". Check that the catalog row, the alert and the C2 Data tab payload carry that sentence.
6. Measure after your changes, on copies: the server cycle and the serverless runs from ${S}/live_state_1122/store.sqlite and
   its cursor with the Dropbox mirror ${S}/wire/mirror/ behind the fake client (A1's recipe in report_A1.md): per run files,
   rows, seconds, peak memory, /tmp; then one more cycle changes nothing. Report before and after your changes.

## Tests and environments (CPU is shared: be frugal)
Your own and related tests while working; the full suite ONCE at the end in ${S}/fix/venv2_compat312/bin/python and
${S}/fix/venv_sqlite340/bin/python (serialize-only failures expected there; docs failures in tests/test_docs.py are unit F's).
One world at a time in ${S}/wire/SWEEP/, deleted at the end. Return the structured result (saved as report_SWEEP.md).

## ADDED 2026-11-22 23:55: review findings across units
Every independent review's findings are saved as ${S}/wire/review_findings/review_<unit>_<lens>.json (refreshed by the orchestrator).
A finding aimed at YOUR files that its own unit's fixer could not fix (another owner's file) is yours: check it against the code
(it may be fixed already), fix it with a failing test first, and list it in your needs file's status list.

## ADDED 01:15: the owner's "no dollar amount is too small" (PLAN.md orchestrator decision 9)
Remove every dollar floor PLAN.md decision 9 lists that sits in your files (failing test first, then measure on the real world how
many items and dollars each removal adds), and search your files for any other value floor (a cents comparison that drops, hides or
skips an item). What is not money (a cent of rounding, B13 token pieces, informational remittances) stays out, named by what it is.
