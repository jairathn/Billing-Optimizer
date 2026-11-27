# fx-passes (fix wave F3c: the two never-answered passes together): progress

Brief: `plans/briefs/2026-11-26_fixwave.md`, section "F3c after F3a and F3b merge: the two passes together" (and the
common brief `plans/briefs/2026-11-26_common.md`). Branch `wip/fx-passes`, started from the integration tip fe9ad71
(batch 1 merged: F1, F3a, F3b). Worktree `$S/tk/wt/fx-passes`; private folder `$S/tk/units/fx-passes/` (mode 700).
Tests: `tests/test_fx_passes.py`.

F3c owns, for these changes only, the functions F3a and F3b owned (detect.py: `run_ema_open_claims`,
`_ema_check_open_claims`, `_ema_claims_found`, `_send_day_twins`, `run_ema_report_claims` and F3b's helpers;
`run_837_open_claims`, `_837_paid_elsewhere`, `_837_ema_answers`, `price_unanswered_lines`, `_emit_open_claim` and F3a's
helpers; x12_837.py; story.py's rows).

## State (2026-11-27 05:05 UTC)
Started. Reading done; the needs tests run on the tip (fe9ad71):
- plans/progress/fx-837_needs_tests.py: 12 cases fail. Mine (F3b's functions): the blank charge (2), the waiting view (2:
  the call is in, via getattr; the test reads the rows as dicts, the store keeps lists), the older exports' bill once the
  newer one is answered (2). Not mine: the dashboard's copy of 837 sends (2, F6), the 837 reread (1, F6), the story's
  '837' label (1, F5), the older bill paid on the remade bill (2, F6's vanished-check route, task "vanished-check routes").
- plans/progress/fx-exports_needs_save.py: 2 cases fail (the keep-for-work narrowing, mine).
- The reviewer's since-answers-tests/test_answers_review.py: 19 pass, 4 fail: the no-name answers-1 test across the passes
  (2, mine: decidable with the 837's own patient) and answers-7 (2: its 837 names SAMPLE PAT while EMA names patient 1).

## Plan (in order)
1. tests/test_fx_passes.py: the ported failing tests (both store kinds).
2. Fixes: live_items None (and the stopgap out of _emit_open_claim); the waiting view call direct and the test on lists;
   the yield by `_837_patients` and `_visit_line`; the keep-for-work narrowing and the save test in the suite; the 837's
   own patient in `_who` (and the test worlds that name 'SAMPLE PAT' reviewed); s837[7] pinned by a test; the exports'
   newer bill answered as primary keeps carrying the visit; F8's two observations (Medicaid plan name over the payer id's
   majority; the $0 never-paid code claim).
3. Measure on a plain world copy (tip against branch) and on a reread world copy (837 files in $S/tk/f837/raw).
4. Hand-trace at least 20 moved items, F3b's 4 claims ($311) with A17's twin proof.

## Needs from others
(none yet)
