# Phase 3 and 4 briefs: unit E (retention and archive), unit F (docs), and the verification phase V

S = /tmp/claude-0/-home-user-Billing-Optimizer/96e81489-2ad2-5c70-991d-e00db2e22749/scratchpad. Read ${S}/wire/PLAN.md first and
every ${S}/wire/report_*.md and needs_*.md written before you.

## Unit E: retention and archive (amendment section 11.3), so old data never weighs on the daily run
Files: new remitwatch/archive.py, new tests/test_archive.py, and small, named hooks in store.py, cloud.py, pipeline.py,
detect.py, api.py and the Data tab (phase 1 and 2 are finished when E starts; keep each hook to a call and say where in the report).
Build: hot data = what a daily run needs (open items, their claims and lines, the history the rules need: payer rate baselines
over their measured window); everything else goes to compressed archive files the daily run never loads (server: /data/archive;
serverless: the state folder's archive/), read on demand by Find a claim and the claim view: lines and postings with service older
than the retention horizon (REMITWATCH_RETENTION_MONTHS, default 36) and no open item, ledger boxes older than 1,100 days, A/R
snapshots older than 13 months. Items past every appeal and filing window + 30 days retire from the worklist (work_state
expired, with their proof; still findable and reopenable). Each run records the hot-store size and peak memory against a budget
(serverless 1.6 GB of the 2 GB limit); Data health warns at 80% and names the largest tables. Archives are kept, never deleted
(EMA and TriZetto stay the records of truth). Idempotent; crash-safe (archive written and verified before rows leave the hot store).
Acceptance (real data, both modes): hot-store size, Vercel peak memory and run time before and after the horizon; every retired
item still opens from Find a claim and the claim view (sample 100 by stratified hash, all must open with their story); the
worklist's open-item count and dollars by signal before and after, every removed item accounted for (expired vs archived) to the
item; a second run changes nothing.

Self-running: the archive runs by itself in every cycle; expose archive.run(conn, *, horizon=None, budget_state=None) for
unit G's self-regulation (tighter horizon when a run passes 80% of its memory budget, never below open items' windows).

## Unit F: docs
Files: README.md, ARCHITECTURE.md, docs/OPERATIONS.md, docs/VERCEL_SETUP.md, docs/AWS_SETUP.md, docs/SERVER_RUNBOOK.md,
docs/CONTEXT_CHECKPOINT.md, docs/MISSING_DATA.md, docs/DATA_FINDINGS.md, .env.example comments, tests/test_docs.py; also
.github/workflows/tests.yml gains `workflow_dispatch:` so CI can be re-run by hand or by API (one line, nothing else).
Write what is true in the code (the doc tests hold docs to code): the weekly EMA routine as the supported feed (a named person
and a backup, Monday, the five exports of section 12 with filters and cadence, freshness alerts), what to ask ModMed for if an
API is offered (section 10), how uploads of large reports work and their limits, de-duplication guarantees, retention and
archives, the states (work now, waiting, done, accepted, expired, can't be checked) and what moves an item between them, the
auto write-off flag and the owner's policy table, the story/verdict/next step, both hosts' limits, the owner decisions still
open. No names, no tenant host, no identifiers. Public-repo scan in test_docs.py stays green.

## Phase V: end-to-end verification (the owner's acceptance; nothing ships without it)
Independent of the builders: verifiers do not edit repository files; they report. Use ${S}/truth/ (HOW_TO_RUN.md, confusion.py,
g1_final/, g2_final/) built independently from raw sources.
V1 Dropbox route, both modes: start from copies of ${S}/live_state_1122/store.sqlite and cursor.json; serve ${S}/wire/mirror/
through a fake Dropbox client whose cursor is already past the four report files (as on the live sites); run the cycles (server
`pipeline.run_cycle`; serverless `run_cloud_cycle` with LocalState until `more` is false); the reports must be found, read and
applied; report every run's figures (files, rows, boxes, holds, seconds, peak memory, /tmp); then a second pass changes nothing.
V2 Upload route, both modes: the real Upload page in Chromium (Playwright): upload prod2025.csv (206 MB), prod2026.csv and the
two A/R workbooks; pause and resume; a killed tab resumed; the same file again ("already have this"); the same file in Dropbox
and by upload applied once; a posted-date slice after the year file and a year file after posted-date slices give the same
totals; no request over 4.5 MB on the serverless host.
V3 The two gates (amendment 13) on the full 2025+ population in both modes: run ${S}/truth/confusion.py on the rebuilt stores;
the confusion matrix by count and dollars (needs work and listed WORK NOW or WAITING; needs work and missed; no work and listed;
no work and not listed); every off-diagonal line explained or fixed. Targets: zero unexplained missed money, zero unexplained
wasted work.
V4 The 66-case click-through: for every practice-manager case, open the item in the running app by deep link (or Find a claim
for items the app should now raise), screenshot the drawer (names off), and compare state, verdict, decisive rows, next step,
deadline and buttons with the verified truth (${S}/pm/worksheet.json). Every mismatch is a bug to fix before shipping.
V6 Running on its own: on the integrated world in both modes, run 14 simulated daily cycles with the clock advanced a day at a
time (new reports arriving weekly, none for two weeks once, a forced high-memory reading once, a deliberately broken invariant
once): the software must regulate itself, alert where it should, hold closures in safe mode on the broken invariant, recover on
the next good run, run its weekly self-audit, and never need a person except for business decisions.
V7 Diagnosability (the owner's literal test): unit H's five injected faults on copies of the integrated world, re-run by the
verifiers themselves; for the code bug a fresh agent with ONLY the bundle, CLAUDE.md and the repository must find the root
cause and produce a tested fix.
V8 Results and ROI: every number on the Results tab recomputed independently from raw rows (counts and dollars equal); 30
sampled recovered dollars' attribution re-derived by hand; the spot-check flow done by a person-like script (agree and
disagree), the accuracy shown and each disagreement present in the diagnostic bundle.
V9 Ease of use with two personas on the real (names-off) data in both modes: a newcomer with no billing experience and an
experienced biller each do the daily tasks (open Today, work the top five items, send one appeal packet to the fake fax, log a
call, mark a payment posted, let one go with a reason, find a claim, upload a report): time, clicks, every point of confusion and
every point of over-guidance recorded; fix the pages until the newcomer finishes every task without help and the expert never
hits an unnecessary step.
V10 Bug hunt, loop until dry: independent hunters with different lenses (data edge cases; concurrency; both-hosts parity; pages on
phone and slow network; security and privacy; money invariants with randomized invented data) each round; every confirmed bug gets
a failing test, then the fix; stop only after two rounds in a row find nothing new.
V5 Adversarial review: correctness of money (sample re-derivations), safety (PHI in pages, logs and the repo; fax and button
safety; auth on every new route), both-modes parity (findings identical between server and serverless on the same data), SQL
on SQLite 3.40/3.45 and the compact store's query plans, memory and time on Vercel's limits.
Then: fix loop (owners fix, verifiers re-check) until V1-V5 pass; the full suite in the four environments plus node checks on a
clean checkout of the release commit; push to main; live checks (both /api/health commits, 401 gate, Vercel's next run's
status.json in /RemitWatch-state, the AWS deploy).
