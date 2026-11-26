# fx-exports (fix wave F3b: the exports, the twins and G8): progress

Brief: `plans/briefs/2026-11-26_fixwave.md`, section "F3b the exports, the twins and G8" (and the common brief
`plans/briefs/2026-11-26_common.md`). Branch `wip/fx-exports`, started from the integration tip bc29767 (fix7d merged).

Functions this fixer owns (detect.py): `run_ema_open_claims`, `_ema_check_open_claims`, `_ema_claims_found`,
`_send_day_twins`, `run_ema_report_claims` (and new helpers they call, named in the commits).

## State (updated as the work moves)

- Set up: worktree, private folder `$S/tk/units/fx-exports/` (mode 700).
- Reviewer and verifier tests run against the tip (bc29767): every answers test still fails (36 failed, 8 passed;
  the 8 are controls). The ones in this fixer's scope are ported into `tests/test_fx_exports.py`.

## Findings and residuals: status

| Item | Status | Test |
|---|---|---|
| answers-1, exports' side | open | |
| answers-2 | open | |
| answers-3, exports' half | open | |
| answers-5, exports' half | open | |
| answers-7 | open | |
| residual: export claims dated by the visit day get no twins | open | |
| residual: twins told apart by the patient's name key | open | |
| residual: G8 three items for whole, 26 and TC; part priced at the whole rate | open | |
| residual: exports' one-visit rule by one EMA patient id | open | |

## Needs from others

(none yet)

## Next command

    cd $S/tk/wt/fx-exports && python3 -m pytest -q tests/test_fx_exports.py

## Measurements

(none yet)
