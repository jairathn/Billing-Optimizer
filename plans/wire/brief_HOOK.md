# Unit HOOK brief (phase 3b): the self-running modules (E, G, H) wired into both daily cycles, their pages and routes

S = /tmp/claude-0/-home-user-Billing-Optimizer/96e81489-2ad2-5c70-991d-e00db2e22749/scratchpad. Read ${S}/wire/PLAN.md first (the
owner's words, NON-NEGOTIABLES, the revised schedule, release gates, CPU rule, needs sweep and orchestrator decisions), then
${S}/wire/brief_3a.md, brief_G.md, brief_H.md, the "Unit E" section of brief_E_F_V.md, and every report_*.md and needs_*.md
(E, G and H's needs files hold the exact hook bodies you apply).

## The owner's words this unit answers
"It needs to run independently ... self regulating, and self-checking, and maintain itself ... re-evaluating its performance
regularly." "Extensive logs that can be audited ... so detailed that it should be able to recursively fix its own issues, or
produce a log so detailed that an intern using Claude can diagnose and push the fix." Phase 3a built the modules; nothing
runs until you wire them.

## Your files (every earlier owner is finished when you start)
pipeline.py, cloud.py, detect.py (the hook call sites only), cli.py, store.py, dropbox_sync.py, trizetto.py, fax.py (retry and
hold wrappers through repair.py only), api.py (one include line), new remitwatch/api_ops.py (an APIRouter), new
remitwatch/static/ops.js (the Data tab panels, rendered into the empty <section id="ops-panels"> unit C2 left in data.js's
page), index.html (one <script> tag for ops.js), static/index.html's drawer only to show the decisions section if C1 has not;
small fixes in archive.py, selfcheck.py, runlog.py, diagnose.py, repair.py where wiring shows a module bug (a failing test
first); tests: new tests/test_hooks.py plus the modules' own tests.

## Build
1. Apply needs_E.md, needs_G.md and needs_H.md hook by hook, each with the test its needs file names, on both hosts: run
   manifest and event log for every run and step; the decision log before and after detection's save (runlog.record_decisions)
   with GET /api/findings/{finding_id}/decisions (signed-in); invariants every run with safe mode (closures held, the last good
   worklist kept, the next good run releases it); self-regulation of memory and time (the next run sheds work in G's order);
   the archive inside the cycle (E's bounds; never below an open item's window); housekeeping; the weekly self-audit (and on
   demand from the Data tab); the track record; the pattern watch; money conservation; blindness in dollars; recovery rates by
   pattern written at save as expected_value_cents, recovery_rate and recovery_basis (G's rates replace the interim ones INT
   stored); the self-repair registry around Dropbox, TriZetto and SRFax calls.
2. api_ops.py: GET /api/ops/panels (archive.panel, selfcheck.panel, runlog.panel), POST /api/ops/self-audit (signed-in, runs it
   now on a server; on Vercel it asks the next run), GET /api/ops/bundle (the diagnostic bundle as a zip, signed-in, no-store,
   logged), the decisions route above. One include line in api.py. Every route works on both hosts (on Vercel read what the run
   stored; never recompute on the dashboard copy).
3. ops.js: three panels in plain words ("Running on its own", "Logs and diagnostics", "Store size and memory"), each figure
   opening its list, phone width works, no em dashes; the bundle button says what it is for ("Give this file and the repository
   to Claude to find the cause of a problem").
4. CLI: `diagnose [--days N] [--run RUN_ID]`, `self-audit`, `archive --dry-run|--run`.

## Acceptance (real data, both hosts; one world at a time in ${S}/wire/HOOK/, deleted at the end)
- The full server cycle and the serverless runs from ${S}/live_state_1122/store.sqlite and its cursor with the Dropbox mirror
  (${S}/wire/mirror/) behind the fake client, until more is false, then one idle run that changes nothing: per run the manifest,
  peak memory against the 1.75 GB gate (Vercel), time against 180 s, /tmp under 512 MB; every invariant passes or names a real
  bug with its owner; the self-audit's matrix next to ${S}/truth/'s.
- H's five injected faults, each on a copy (a code bug that drops a category of items; a corrupt Production file; a ledger whose
  totals stop matching the store; a network failure mid-run; a lock left by a killed run): detected, contained or repaired,
  logged. For the code bug, hand ONLY the bundle, CLAUDE.md and the repository path to a fresh agent (spawn one with the Agent
  tool; tell it nothing else) and record whether it finds the root cause and writes a tested fix, and how long it took.
- A forced high-memory reading makes the next run shed work and stay under budget, with the alert and the panel saying so.
- The PHI scan of every log, manifest and bundle byte from the real-data runs (names, birth dates, member ids, claim ids, check
  numbers): zero.
- Playwright screenshots of the three panels and the drawer's "Why did this change?" at 1400 and 390 px (fixtures or names off).
Full suite ONCE at the end in venv2_compat312 and venv_sqlite340 plus both node checks. Write needs_HOOK.md; return the
structured result (saved as report_HOOK.md).

## ADDED 2026-11-22 23:55: review findings across units
Every independent review's findings are saved as ${S}/wire/review_findings/review_<unit>_<lens>.json (refreshed by the orchestrator).
A finding aimed at YOUR files that its own unit's fixer could not fix (another owner's file) is yours: check it against the code
(it may be fixed already), fix it with a failing test first, and list it in your needs file's status list.

## ADDED 01:15: the owner's "no dollar amount is too small" (PLAN.md orchestrator decision 9)
Remove every dollar floor PLAN.md decision 9 lists that sits in your files (failing test first, then measure on the real world how
many items and dollars each removal adds), and search your files for any other value floor (a cents comparison that drops, hides or
skips an item). What is not money (a cent of rounding, B13 token pieces, informational remittances) stays out, named by what it is.

## ADDED 01:30: dollars per biller-minute (PLAN.md orchestrator decision 10)
Implement the parts of decision 10 that sit in your files, with tests on invented data and a measurement on the real world (how
many WORK NOW items are above and below $1 a minute, and their dollars).
