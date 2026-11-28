# Unit replay-fix-2b: what leaves the list and why (progress)

Brief: the workflow's task text (wf_a3fa6266-96f), with plans/briefs/2026-11-26_common.md, CLAUDE.md and plans/HANDOFF.md.
The verdicts behind each bug are private: `$S/tk/replay/verify2_result.json`, list "verdicts" (B1 = 0, B2 = 2, B3 = 7,
B4 = 10, B5 = 8). The proposed failing tests (invented data) are in `$S/tk/replay/proposed_tests/`.

Base: origin/claude/ecstatic-fermi-orfcgo (ed245a1) with origin/wip/gapfix (89acb3c) and origin/wip/replay-fix (0cd46c8)
merged in. Worktree `$S/tk/wt/replay-fix-2b`, branch `wip/replay-fix-2b`, private folder `$S/tk/units/replay-fix-2b`.

Files this unit owns: remitwatch/selfcheck.py (check_vanished, _no_route, _sent_again, _facts_of, later_payments,
after_detection's ctx), remitwatch/detect.py (the retired hand-off, settled(), _after_save_checks),
remitwatch/ema_ledger.py (_map or the narrow read), and the tests added. The sibling unit replay-fix-2a edits
selfcheck._automatic_open, check_past_window, states.py, results.py and run_secondary_not_billed's person-billed lines.

## State

| Bug | What | Test | State |
|-----|------|------|-------|
| B1 | vanished check: routes for a payment EMA posted and a claim sent again under the same claim id | | not started |
| B2 | detection hands the checks the ids it retires on purpose (left_record) | | not started |
| B3 | precision_guard: own payer only, PLB takebacks netted, own answer repeated skipped | | not started |
| B4 | a let-go line settles a never-listed whole-claim denial | | not started |
| B5 | a same-visit vote split keeps its EMA link | | not started |

## Next command

Port the B1 test: `cp $S/tk/replay/proposed_tests/test_vanished_lawful_departures.py tests/...`, see it fail, fix.

## Measurements (replay data, read only)

None yet.

## Open questions

None yet.
