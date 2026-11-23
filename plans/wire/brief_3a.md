# Phase 3a: units E, G and H build MODULES ONLY now; one hook-in agent wires them later (phase 3b)

S = /tmp/claude-0/-home-user-Billing-Optimizer/96e81489-2ad2-5c70-991d-e00db2e22749/scratchpad. Read ${S}/wire/PLAN.md (the owner's
words, NON-NEGOTIABLES, the revised schedule and release gates at the end), then your unit's full brief:
E = the "Unit E" section of ${S}/wire/brief_E_F_V.md; G = ${S}/wire/brief_G.md; H = ${S}/wire/brief_H.md. This file OVERRIDES
those briefs on file ownership and timing only; their content (what to build, acceptance ideas, the owner's words) stands.

## Why modules first
Units A1 (pipeline.py, cloud.py, cli.py, dropbox_sync.py), INT (detect.py, states.py, story.py) and C1/C2 (api.py and the pages)
are still editing the files your hooks would go into. B2 showed the way: it built archive_rules.py as a pure module with tests, and
wrote into its needs file the exact hook bodies and call sites; the hook-in agent will apply them once those files are quiet.

## Ownership in phase 3a
- E: new remitwatch/archive.py, new tests/test_archive.py.
- G: new remitwatch/selfcheck.py, new tests/test_selfcheck.py.
- H: new remitwatch/runlog.py, remitwatch/diagnose.py, remitwatch/repair.py, tests/test_runlog.py, tests/test_diagnose.py,
  tests/test_repair.py, new CLAUDE.md (repository root) and new docs/TROUBLESHOOTING.md.
- NOBODY in 3a edits pipeline.py, cloud.py, detect.py, store.py, state.py, cli.py, api.py, any static/ page, dropbox_sync.py,
  trizetto.py, fax.py, alerts.py, insights.py or another unit's module. Read them freely. Everything you need in them goes into
  ${S}/wire/needs_<E|G|H>.md as: the file, the function, the exact call (code), where it goes (after which line or call), and
  the test that proves it once wired.

## Interfaces fixed now (so C1, C2, R, INT and the hook-in agent build against the same names)
- Findings columns written at detection save by the hook-in agent from G's functions: `expected_value_cents` (INTEGER: dollars at
  stake x the pattern's recovery rate), `recovery_rate` (REAL 0-1), `recovery_basis` (TEXT: "payer group x code x reason, n cases"
  or the fallback used). G provides `selfcheck.recovery_rates(conn) -> dict` and `selfcheck.expected_value(item, rates) ->
  (cents, rate, basis)`. The worklist sorts by COALESCE(expected_value_cents, shortfall_cents) DESC, then the deadline.
- H's decision log: G and E log through `runlog` (never their own logger). `runlog.record_decisions(conn, run_id, before, after)`
  diffs each item's saved fields across a run and writes one row per automatic change (rule id and version from the item's own
  evidence: archive_rule, auto_status/closure proof, state_basis, ema_outcome), so detection needs one call before and one after
  its save, not a call in every rule. Route (built in 3b, C1's drawer already shows it when it answers):
  GET /api/findings/{finding_id}/decisions -> {"items": [{"at", "run_id", "rule", "rule_version", "change": {field: [before,
  after]}, "evidence": [...]}]}.
- Data tab panels (C2 leaves an empty `<section id="ops-panels"></section>` on the Data tab; 3b adds static/ops.js and
  remitwatch/api_ops.py): each unit provides one function returning a plain dict for its panel:
  `archive.panel(conn, state=None)` (hot-store size, largest tables, archive files, peak memory vs budget, what the last run
  retired), `selfcheck.panel(conn)` ("Running on its own": last run, invariants with history, last self-audit, track record,
  pattern alerts), `runlog.panel(conn, logs_dir)` (last runs with outcome, errors, self-repair attempts, the bundle download).
- Every function that a run calls takes the run's context explicitly (conn, run_id, host mode, today) and returns what it did;
  none reads the clock or the environment by itself except through one named helper, so tests can drive it day by day (V6 runs
  14 simulated days).

## Acceptance in phase 3a (no hooks yet)
Tests with invented data for every part, both store kinds (the server store file and the compact serverless image built the way
cloud.py builds it: read how test_cloud.py does it). Real-data checks by calling your functions directly on a COPY of a world
(python3 -I -B ${S}/build/ema_checks/accept/b0_build.py <your work dir>/world: one world per unit, deleted at the end):
- E: archive.run on the world copy: hot-store size before and after, which tables shrink, every retired item still found by the
  claim lookup you provide (`archive.lookup_claim(...)`, sample 100), a second run changes nothing, crash safety (kill between
  write and delete: nothing lost). And on ${S}/live_state_1122/store.sqlite (Vercel's real image, a COPY): how much it shrinks and
  what the serverless image would weigh after.
- G: every invariant on the world copy (each failure is either a real bug, reported with counts and example finding ids for its
  owner, or a wrong invariant, fixed); the self-audit's coverage and precision lists compared with ${S}/truth/ (HOW_TO_RUN.md if
  frozen, else g1_final/ and g2_final/: say which); recovery rates by pattern with their counts; the money conservation check per
  month and payer group (any gap over $1 named).
- H: the scrubber on a full fixture cycle with planted invented names, birth dates and member ids (zero survive into any log,
  manifest or bundle byte); a bundle from the world copy with every part; the repair registry's remedies each tested with a fake
  failing client; CLAUDE.md and docs/TROUBLESHOOTING.md written for an intern with Claude Code (no names, no identifiers, no host
  names of the practice's systems).

## Tests and environments (CPU is shared: be frugal)
Each part gets a failing test first, then break and restore. Run your own tests while working; at the end run the full suite
ONCE in venv2_compat312 and venv_sqlite340 (other units' half-done edits can fail tests that are not yours: check git diff on the
failing file before acting; do not fix other units' files). Return the structured result (the orchestrator saves it as
report_<unit>.md) and write needs_<unit>.md with the exact hook bodies.
