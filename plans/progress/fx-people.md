# fx-people (fix wave F5: people and decision 2's follow-ups): progress

Branch `wip/fx-people`, worktree `$S/tk/wt/fx-people`, private folder `$S/tk/units/fx-people/` (mode 700).
Brief: `plans/briefs/2026-11-26_fixwave.md` section F5; rules `plans/briefs/2026-11-26_common.md`.
Runner: `$S/tk/units/fx-people/bin/t.sh [python] -- <pytest args>` (clean environment, run from the worktree).

## State
- Started 2026-11-27 ~02:00 UTC from the integration tip 56b538e (no batch-1 merge on it yet).
- Ported the reviewers' and verifiers' tests into `tests/test_fx_people.py` (+ `tests/fx_people_xp.js`, the panel's page
  logic in a node vm). On the tip: 36 failed, 4 passed (the passes are controls: JSON true sends, an unpaid line's $0
  refused, and the whole send without a copy refresh).

## Done
(nothing yet)

## Half done

## Next command

## Numbers measured

## Needs from others

## Open questions
