# deadlines: every payer window from its primary source (progress)

Brief: `plans/briefs/2026-11-26_deadlines.md`. Rules: `plans/briefs/2026-11-26_common.md`, CLAUDE.md, HANDOFF section 0.
Branch `wip/deadlines`, from the integration tip 98070b8. Edits: `remitwatch/codes.py`, `docs/RESEARCH_SUMMARY.md`, tests,
and the smallest call-site edits (detect.py, states.py and ema_checks.py are being edited by other units now).

## Inputs (read only, never in git)
- S=/tmp/claude-0/-home-user-Billing-Optimizer/96e81489-2ad2-5c70-991d-e00db2e22749/scratchpad
- The research table: `$S/tk/units/a17/windows/payer_windows.json` and `.md` (156 rows); sources in `.../windows/dl`.
- The 28 disagreements: `plans/review_2026-11-26.md`, section "Deadlines"; answers-8 and its two Indiana Medicaid tests in
  `$S/tk/review/since-answers-tests/test_answers_review.py`.
- Private folder (mode 700): `$S/tk/units/deadlines/` (scripts/, peek_*.txt aggregates, later the one world copy).

## State
- [x] Worktree and branch set up; brief, review section and research rows read.
- [ ] Each source read for every window changed (decisions below).
- [ ] Tests first (tests/test_payer_windows.py), then codes.py, then call sites.
- [ ] docs/RESEARCH_SUMMARY.md.
- [ ] Measure on a world copy (tip vs branch) and hand-trace 20+ moved items.
- [ ] Suites; final report.

## Decisions (filled in as each source is read)

## Numbers measured

## Next command

## Open questions
