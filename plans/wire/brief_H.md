# Unit H brief: audit logs, self-repair and diagnostic bundles (traced and fixed fast, by the software or an intern with Claude)

S = /tmp/claude-0/-home-user-Billing-Optimizer/96e81489-2ad2-5c70-991d-e00db2e22749/scratchpad. Read ${S}/wire/PLAN.md first
(including the owner's non-negotiables) and every ${S}/wire/report_*.md and needs_*.md written before you.

## The owner's words (2026-11-22)
"You need to build it extensive logs that can be audited too, in case anything is going wrong, so that it can be immediately
traced and fixed in the event of any issues - so detailed, that it should be able to recursively fix its own issues. Or produce
a log so detailed that an intern using Claude can diagnose and push the fix."

## Boundary (say it plainly in the docs)
The software repairs operational failures by itself, by fixed rules (below). It never edits its own code in production: a code
bug is contained (unit G's safe mode keeps the last good worklist and holds automatic closures) and turned into a diagnostic
bundle complete enough that a person with Claude Code and this repository reaches the root cause and a tested fix.

## Files
New remitwatch/runlog.py (run manifest, structured event log, decision log, scrubber, retention), new remitwatch/diagnose.py
(the bundle), new remitwatch/repair.py (the self-repair registry), tests/test_runlog.py, tests/test_diagnose.py,
tests/test_repair.py, new docs/TROUBLESHOOTING.md, new CLAUDE.md at the repository root; small named hooks in pipeline.py,
cloud.py, detect.py (decision log at the points items change), dropbox_sync.py/trizetto.py/fax.py (retry and hold wrappers via
repair.py), cli.py (`diagnose`), api.py (one owner-gated or signed-in route for the bundle; agree with unit C's owner), the Data
tab (a "Logs and diagnostics" panel). Coordinate with unit G (it owns invariants, safe mode, self-regulation; H logs them and
links them; do not duplicate).

## Build
1. Run manifest, one per run, both hosts: run_id; start and end; host mode; code commit (VERCEL_GIT_COMMIT_SHA or the deployed
   commit file); DETECT_RULES and the rules hash; Python and SQLite versions; the names (never values) of the settings in force;
   inputs (each file listed, fetched, applied, held or refused, with size, sha256 prefix, kind, origin, result); Dropbox cursor and
   ledger revision before and after; outputs (items by signal and state, raised, closed by rule, reopened, changed); per step: wall
   time, peak memory, /tmp (serverless), counts; invariant results (unit G); self-regulation and self-repair actions; errors.
2. Structured event log: JSON lines, one stable schema {ts, run_id, step, level, event, rule, rule_version, item, counts, ms,
   mem_mb, detail, error: {type, message, stack, where}}; correlation by run_id; every exception caught at a step boundary is
   logged with its full stack trace and the step's inputs (ids only) before it is contained; nothing swallowed silently (a test
   greps the code for bare excepts that do not log).
3. Decision log per item: every AUTOMATIC change to an item (raised, closed with proof, reopened, state changed, signal changed,
   folded into another, hidden by a rule, expected value or deadline changed) with the rule id and version, the evidence row
   keys, and before and after; stored with the item (finding_events kind 'auto', or a table of its own if that is cleaner: say
   which) and shown in the drawer's history as "Why did this change?". A person's actions keep their existing events.
4. Privacy of logs: no patient name, birth date, record number, member id, claim id, check number, note text or cell text in
   any log line, error message, manifest or bundle; use finding ids and the store's salted keys. One scrubber in runlog.py that
   every writer goes through, plus a test that runs a full fixture cycle in both modes (with invented names, birth dates, member
   ids planted in the data) and scans every byte of the logs, manifests and bundle for them.
5. Where logs live and how long: server /data/logs (rotated daily, gzipped, 180 days); serverless the state folder's logs/
   (one gzipped file per run, 90 days, pruned by the run itself); a size budget with the oldest dropped first and the drop
   logged. Vercel's own function logs carry only one line per run (run_id and outcome), never detail. Never in git.
6. Self-repair registry (repair.py), rule-based, each remedy with an attempt limit, a back-off, a log entry with before and after,
   and an alert when it runs out of attempts: transient network or HTTP 5xx/429 from Dropbox, TriZetto, SRFax (retry with
   back-off, then hold the step and carry on with the rest); a file that cannot be read (hold it with the reason, keep the others
   going); a derived table that fails an invariant (rebuild it from its sources once: ledger refresh, link map, serving copy,
   detection; then safe mode through unit G if it still fails); a run killed mid-way (the repair paths unit A1 built, logged); a
   lock left by a dead run (the existing stale-lock rule, logged); a schema missing a column (the migration, logged); memory or
   time pressure (unit G's self-regulation, logged here). An alert after a failed remedy says what failed, where, what was tried,
   and the exact next step, including "download the diagnostic bundle from the Data tab and give it to Claude with the repository".
7. The diagnostic bundle: `python -m remitwatch.cli diagnose [--days N] [--run RUN_ID]` and a Data-tab button. A zip with: the
   manifests and event logs of the chosen runs; the invariant and self-audit history; every error with its stack; the self-repair
   attempts; the store's schema with row counts per table and the meta keys (values only for non-secret, non-patient keys); the
   settings' names; the versions; for each failing invariant up to 20 example finding ids with their decision logs (no patient
   data); and REPRODUCE.md with the exact commands to reproduce on a copy (server: the backup and a local run; serverless: the
   state folder copy and run_cloud_cycle with LocalState), and to run the tests in the four environments. A test proves the bundle
   holds no patient data and has every part.
8. CLAUDE.md (repository root; read automatically by Claude Code) and docs/TROUBLESHOOTING.md: what RemitWatch is in a paragraph;
   the architecture map (modules and what each owns); the daily cycle on each host; the owner's non-negotiables and the
   invariants; the privacy rules (no patient data in the repo, logs, tests or issues; invented data in tests); how to read a
   bundle; the failure classes and their fixes; how to reproduce; how to run the tests in the four environments (and why each
   exists: Vercel's SQLite 3.40, AWS and CI's 3.45, the newest libraries); how to release (dev branch, the full suite, push to
   main deploys both sites, check /api/health on both); what never to do (edit the live store by hand, push PHI, skip tests). No
   personal names, no identifiers, no host names of the practice's systems.

## Acceptance
Tests with invented data for every part, both modes. Then the owner's literal test, on copies of the integrated real world:
inject five faults one at a time (a code bug that drops a category of items; a corrupt Production file; a ledger whose totals stop
matching the store; a network failure mid-run; a lock left by a killed run). For each: the software must contain or repair it
(logged), and for the code bug, a fresh agent given ONLY the diagnostic bundle, CLAUDE.md and the repository (no other context)
must find the root cause and produce a tested fix. Report each fault: detected (when, how), contained or repaired, diagnosed from
the bundle (yes/no, how long), fix correct (yes/no). Write ${S}/wire/report_H.md and needs_H.md.
