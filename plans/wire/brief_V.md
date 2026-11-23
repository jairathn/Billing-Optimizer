# Phase 4 brief: verification of the release candidate (independent of every builder)

S = /tmp/claude-0/-home-user-Billing-Optimizer/96e81489-2ad2-5c70-991d-e00db2e22749/scratchpad. Read ${S}/wire/PLAN.md (the owner's
words, NON-NEGOTIABLES, release gates, orchestrator decisions) and the "Phase V" section of ${S}/wire/brief_E_F_V.md (V1 to V10:
the checks themselves). This file adds how the phase runs.

## The release candidate (RC)
The orchestrator freezes the code under test as a commit and checks it out read-only at ${S}/V/rc/ (a git worktree). Every
verifier runs the RC from there, never the shared working tree (builders and fixers keep editing that one). Verifiers never edit
the RC or the repository. Each verifier works in its own folder ${S}/V/<check>/ and deletes its worlds at the end.

## The integrated worlds (built once by V1, copied by the others)
V1 starts from copies of ${S}/live_state_1122/store.sqlite and cursor.json (Vercel's real image and Dropbox position, with the
billers' statuses, notes, faxes and hide rules) and runs the RC's real cycles with the Dropbox mirror ${S}/wire/mirror/ behind a
fake client: the server cycle (pipeline.run_cycle on a store file + ledger, as AWS runs it) and the serverless runs
(run_cloud_cycle with LocalState until more is false, as Vercel runs it; then a person's upload of the two raw Production CSVs
through the intake path, as the release note will ask, then the runs until more is false again). It leaves:
- ${S}/V/world_server/ (store.sqlite, the ledger, logs) and ${S}/V/world_serverless/ (the LocalState folder) and
- ${S}/V/V1_runs.json (per run: files listed, fetched, applied, held; rows; seconds; peak memory; /tmp; the manifest ids).
Other checks copy those folders (never write into them).

## Findings
Every check returns findings: {check, severity (high | medium | low), area (intake | detection | states_story | pages_api |
results | ops | docs | deploy), file, title, evidence (a failing invented test, a measurement, a screenshot path, or the finding
ids and aggregate numbers; never patient details), expected, got, fix}. A high is anything that loses or hides money, closes or
lists an item wrongly in a way a person checking by hand would catch, breaks a gate of PLAN.md, leaks PHI, or breaks a host.

## The fix loop
The orchestrator merges findings, sends each area's highs and mediums to one fixer for that area (fixers own the area's files,
write a failing test first, and re-run the check that found it), builds a new RC, and re-runs only the checks the fixes touch,
then V10's hunters again. The loop ends when two hunter rounds in a row find nothing new and no high or medium is open.

## Release gates (all measured here, on the RC, not taken from a unit's report)
The seven gates in PLAN.md. The release note gives each gate's number.

## V9 and V10 are run by irrational humans (the owner, 2026-11-23)
"Make sure you're creative with your bug hunt. Your tester should adopt the mentality of an irrational human." Every V9 persona
and every V10 hunter plays the personas of ${S}/wire/brief_CHAOS.md (the impatient clicker, the confused newcomer, the distracted
multitasker, the skeptic and flip-flopper, the power user, the accidental data saboteur, the owner who changes their mind, the
clock and calendar) on the RC, both hosts, with the chaos workflow (${S}/wire/scripts/chaos.js: tester then an independent
reproduce-or-refute verifier). V10 loops until two rounds in a row find nothing new; each round reshuffles which persona hits which
area (uploads, the worklist and drawer, faxes and appeals, statuses and history, the policy page, Results and spot checks, the
Data tab and its panels, Find a claim, settings and the owner gate, the daily run itself) so no area is only ever seen by one mind.
Chaos round 0 already ran on the live code (949c4db): its confirmed findings that still apply to the RC are re-checked first.
