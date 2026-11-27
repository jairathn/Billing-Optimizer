# fx-windows: F8, the deadlines follow-ups (progress)

Brief: `plans/briefs/2026-11-26_fixwave.md`, section "F8 the deadlines follow-ups". Rules: `plans/briefs/2026-11-26_common.md`,
CLAUDE.md, HANDOFF section 0. Branch `wip/fx-windows`, from the integration tip 8c59195 (56b538e is the same code plus a
handoff note). F8 owns remitwatch/codes.py and docs/RESEARCH_SUMMARY.md, and call sites only to pass a keyword fact.

## Inputs (read only, never in git)
- S=/tmp/claude-0/-home-user-Billing-Optimizer/96e81489-2ad2-5c70-991d-e00db2e22749/scratchpad
- Sources (downloaded by the A17 research): `$S/tk/units/a17/windows/dl/` (anthem/PM_KY_00011, PM_KY_00012, PM_IN_00012,
  KY_CAID_PM; humana/pm2026_deleg; va/usc1703D_b, claims; wellcare/ky_pm_2026; in/anthem_in_ihcp_2026). Fetched by this
  unit into `$S/tk/units/fx-windows/src/`: the IHCP bulletin BT202072 and Humana's claim-payment inquiry guide.
- Private folder (mode 700): `$S/tk/units/fx-windows/` (scripts/measure.sh, scripts/snap.py, tiptree/ = a detached
  worktree of the integration tip, snapshots, run logs).

## State
- [x] Sources read for the five items (below, with the words used in the labels).
- [x] tests/test_fx_windows.py: 23 cases (invented data; detection and EMA's checks on both store kinds). All 23 failed on
      the tip (56b538e) for the reason each names; all pass on the branch.
- [ ] Related suites, break-and-restore, measurement, hand-trace, docs/RESEARCH_SUMMARY.md, full suites.

## The five items and what changed (codes.py unless said)
1. Anthem commercial clinical appeals: a row before the commercial Claim Payment Dispute, for CARC 50, 55, 56, 150, 151,
   152 (MEDICAL_NECESSITY_CARCS): 180 days from the remittance (safe), 185 the last day (receipt presumed 5 days after, as
   for KRS 205.534), verified from the 2026 Kentucky manuals v1 and v2 and the Indiana manual, Clinical Appeals. The two
   call sites that dated EMA-only denials without the reason now pass it (keyword fact `carc`): ema_checks.ema_denials and
   detect._emit_proto (same rule on both sides, so the last day is the clinical one).
2. EMA's 'Medicaid of Kentucky' / 'Medicaid of Indiana': the name rules read the state program by a name that starts with
   the state or with the word Medicaid followed by the state. Found on the way: the old unanchored rules read managed care
   plans' names ('Anthem Indiana Medicaid Hoosier Healthwise', 'MHS ... Indiana Medicaid', '<plan> Pathways Indiana
   Medicaid') as Indiana's fee-for-service program. Fixed: Anthem's Medicaid names are Anthem's Medicaid group; other plans
   are 'Medicaid (other)' with the state from the name; a new Indiana managed care filing row (IHCP bulletin BT202072: 90
   days in network, 180 out of it) and dispute row (60 days, not confirmed) keep their dates no later than before.
3. Humana MA corrected claim: not confirmed; source is the 2026 manual's claim limit ('Medicare lines of business: 1 year
   from date of service'); the tip sheet was searched for again (humana.com, Humana's asset store) and not found.
4. VA Community Care: the window functions decide by the payer id (codes.VA_PAYER_IDS = VACCN, TWVACCN, equal to detect's
   and ema_reports'); first filing is the statute's 180 days, verified (38 U.S.C. 1703D(b)). The appeal window stays the 90
   days used (no source found). The EMA-name paths (ema_reports.payer_group_of) still group the VA's claims as 'Optum (UHC)'
   because learn_payer_aliases is never called: see "Needs from others".
5. second_claim_window: WellCare of Kentucky Medicaid, the later of 12 months from the service and 6 months from the first
   payer's payment (cob {"later": {"months": 6}}); Anthem Medicaid as second payer counts from the other payer's
   remittance (Indiana's 90 days, Kentucky's 365 days, a plan of no named state Indiana's). Dormant in detection today:
   run_secondary_not_billed counts a Medicaid second payer and lists none (snb_medicaid_second).

## Needs from others
- (to be written with failing tests in plans/progress/fx-windows_needs_tests.py)

## Next command
- `python3 -m pytest -q tests/test_fx_windows.py`, then the related files (see the log in the private folder), then the
  measurement: `$S/tk/units/fx-windows/scripts/measure.sh <tag> <tree>` (one world copy at a time).
