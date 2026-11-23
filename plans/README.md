# Plans, briefs and research saved on 2026-11-23

Work stopped when the weekly usage limit ran out (resets 2026-11-26 16:00 UTC). This folder keeps the plan files that lived
only in the session's scratch folder. Paths inside the briefs that start with ${S} point to that scratch folder, which is gone.
The live sites still run main at 949c4db. Nothing below is deployed.

Cleaning before the push: private words, the live site address and the EMA tenant host were replaced with [redacted],
[site address] or [ema host]; long digit runs in plan files became [number]. Left out: the per-payer dollar list
(channels/payers.json), copied payer documents, scripts that embed practice data, and the practice-manager case notes.

## Branches

| Branch | What it holds | State |
|---|---|---|
| hotfix/chaos-r0 | Hotfix 2: chaos round 0 fixes H1-H11 on live code | Builder stopped mid-run. Not reviewed, suites not run |
| hotfix/swing-alarm | Hotfix 2b: swing alarm on every page and in the alert email | Built with tests. Not reviewed. Counting items that appear and vanish by finding id (L3-9) is not done |
| hotfix/deadlines | Hotfix 2c: practice-local day, follow-ups from the appeal date, calendar months, Medicare receipt +5 | Built, suites passed on 3.13 and 3.12. Not reviewed. Its rules version 2.8 clashes with hotfix 2's 2.7 |
| hotfix/supersede-and-heirs | Hotfix 2d: supersede only overlapping copies, missing store image is a stop, faxes follow the heir, no chase of a paid claim | D1-D4 built. Rules bump, suites and measurement unfinished |
| claude/ecstatic-fermi-orfcgo | Every push-2 unit together (A1, A2, B, B2, C1, C2, D, E, G, H, R, SWEEP) | The tree that runs. E and R fixes stopped midway |
| wip/archive | Unit E's own files on main | Review only, does not run alone |
| wip/today-data | Unit C2's own files on main | Review only. Fixes built, final checks not run |
| wip/results | Unit R's own files on main | Review only. Fix stopped midway |
| wip/intake-sweep | The intake files SWEEP changed (they also hold A1's build) | Review only. Second review and fix not run |
| wip/unit-h | Unit H (run logs, decision log, self-repair, diagnostic bundle) | Built, reviewed, fixed. Not wired in yet |
| wip/plans | This folder | |

The integration unit (INT) never wrote code before the limit. Its brief is wire/brief_INT.md.

## Next, in order

1. Live hotfix: finish hotfix 2, review 2b, 2c and 2d, add the 2b appear/vanish counting, merge the four plus I1 (no level-one
   fax for Medicare KY at CGS J15: myCGS or mail, one claim per request), set one rules version, run hotfix/integrate_draft.js,
   the suite in four environments, push to main, check /api/health on both sites.
2. Push 2: finish the E, R, C2 and SWEEP fix stages, then INT, HOOK, docs (F) and verification (wire/brief_V.md). Delete
   tests/test_zz_dbg_desk_b.py before release.
3. Payer routes (channels/): verify the TPA and other-insurer research, build the directory, then hotfix 3 (channels/brief_build.md).
4. Leverage: the synthesis was never finished. lever/synthesis_partial holds what it had built. Re-run wire/scripts/lever.js
   with {areas: []}.
5. Guardrails: the five finders are in guard/L1-L5 (L1 verified). Verify L2-L5 and write GUARDRAILS.md.

## Folder map

- wire/: PLAN.md (decisions and release gates), every unit brief (phase 3: brief_3a, brief_E_F_V, brief_G, brief_H, brief_HOOK;
  phase 4: brief_V, brief_CHAOS), the chaos round 0 backlog, scripts/ (the workflow scripts), needs/ (hook-in notes per unit).
- build/: the EMA modules' hook-in notes and the design amendments. design/: the EMA design spec.
- hotfix/: briefs for hotfixes 2, 2b, 2c, 2d and the integrated check script.
- channels/: payer fax and portal research per family (Medicare, Medicare Advantage, commercial, Medicaid) and the build brief.
- guard/: the guardrail audit brief, findings L1-L5 and their failing tests (invented data).
- lever/: leverage research and independent verification for eight areas, plus the partial synthesis.
