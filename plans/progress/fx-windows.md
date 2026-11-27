# fx-windows: F8, the deadlines follow-ups (progress)

Brief: `plans/briefs/2026-11-26_fixwave.md`, section "F8 the deadlines follow-ups". Rules: `plans/briefs/2026-11-26_common.md`,
CLAUDE.md, HANDOFF section 0. Branch `wip/fx-windows`, from the integration tip 8c59195; the integration tip 59f6822
(handoff notes and the final-stage brief, no code) merged at b050f9a. Batch 1 (wip/fx-ledger, wip/fx-837, wip/fx-exports)
had not reached the integration tip when this unit measured: the measurement holds this branch's code only.
F8 owns remitwatch/codes.py and docs/RESEARCH_SUMMARY.md, and call sites only to pass a keyword fact (ema_checks.ema_denials
and detect._emit_proto now pass the denial's reason, `carc`).

## Inputs (read only, never in git)
- S=/tmp/claude-0/-home-user-Billing-Optimizer/96e81489-2ad2-5c70-991d-e00db2e22749/scratchpad
- Sources (downloaded by the A17 research): `$S/tk/units/a17/windows/dl/` (anthem/PM_KY_00011, PM_KY_00012, PM_IN_00012,
  KY_CAID_PM; humana/pm2026_deleg; va/usc1703D_b, claims; wellcare/ky_pm_2026; in/anthem_in_ihcp_2026). Fetched by this
  unit into `$S/tk/units/fx-windows/src/`: the IHCP bulletin BT202072 and Humana's claim-payment inquiry guide.
- Private folder (mode 700): `$S/tk/units/fx-windows/` (scripts/measure.sh, snap.py, compare.py, trace.py, mutate.py;
  snapshots, run logs, the A17 logs, the hand-trace notes).

## State
- [x] Sources read for the five items (below, with the words used in the labels).
- [x] tests/test_fx_windows.py: 29 cases (invented data; detection and EMA's checks on both store kinds). Each failed on
      the tip (56b538e) or on the commit before its fix, for the reason it names; all pass on the branch.
- [x] Break and restore: 28 mutations of the fixes, each caught by the tests and restored byte for byte
      (`$S/tk/units/fx-windows/scripts/mutate.py`; each run with its own bytecode folder).
- [x] docs/RESEARCH_SUMMARY.md: "Follow-ups read in the primary source (November 27, 2026)"; tests/test_docs.py green
      with the private word list.
- [ ] Measurement on the final code, A17 classes, hand-trace (below), full suites.

## The five items and what changed (codes.py unless said)
1. Anthem commercial clinical appeals. Anthem's own member: a denial as not medically necessary or experimental
   (CARC 50, 55, 56) is a clinical appeal, "one hundred eighty (180) calendar days ... from the date they receive notice
   of Anthem's initial decision" (KY manuals 2026 v1 and v2, IN manual 2026 v1, Clinical Appeals): 180 days from the
   remittance is the date shown, 185 (receipt presumed 5 days after, as for KRS 205.534) the last day. A BlueCard member
   (another Blue plan's) is not Anthem's: the same manuals (BlueCard Members) send a claim denial the provider appeals
   without the Home Plan's directions to the local plan as a Claim Payment Dispute, two years from the EOP. The 835's plan
   (contract class) tells them apart: BlueCard's words (ITS HOST, BLUECARD, BLUE CARD), a plan name starting with another
   state's two-letter code, or a plan or payer naming a state other than Kentucky and Indiana. A BlueCard denial and one
   whose plan is not known (EMA's own denials name none) keep 180 days as the date shown and the dispute's two years as
   the last day, so no denial still open to a dispute leaves the list. CARC 150, 151, 152 (level of service, frequency,
   length) stay with the two-year dispute ("Claim code editing issues"). Call sites: ema_checks.ema_denials and
   detect._emit_proto pass the reason (the same rule dates an EMA denial and works out its last day).
2. EMA's 'Medicaid of Kentucky' / 'Medicaid of Indiana': the name rules read the state program by a name that starts with
   the state or with the word Medicaid followed by the state. Found on the way: the old unanchored rules read managed care
   plans' names ('Anthem Indiana Medicaid Hoosier Healthwise', 'MHS ... Indiana Medicaid', '<plan> Pathways Indiana
   Medicaid') as Indiana's fee-for-service program. Fixed: Anthem's Medicaid names are Anthem's Medicaid group; other plans
   are 'Medicaid (other)' with the state from the name, now also from the state's code after "of" ('<plan> Medicaid of
   Ky', plan_state), so a Kentucky plan's denial gets Kentucky's managed care windows (120 days, last day 125) instead of
   the 90-day default that would close it 35 days early. A new Indiana managed care filing row (IHCP bulletin BT202072: 90
   days in network, 180 out of it) and dispute row (60 days, not confirmed) keep their dates no later than before.
3. Humana MA corrected claim: not confirmed; the source is the 2026 manual's claim limit ('Medicare lines of business: 1
   year from date of service'); the tip sheet was searched for again (humana.com, Humana's asset store) and not found.
4. VA Community Care: the window functions decide by the payer id (codes.VA_PAYER_IDS = VACCN, TWVACCN, equal to detect's
   and ema_reports'); first filing is the statute's 180 days, confirmed (38 U.S.C. 1703D(b)). The appeal window stays the 90
   days used (no source found for the region). The EMA-name paths (ema_reports.payer_group_of) still group the VA's claims
   as 'Optum (UHC)' because learn_payer_aliases is never called: see "Needs from others".
5. second_claim_window: WellCare of Kentucky Medicaid, the later of 12 months from the service and 6 months from the first
   payer's payment (cob {"later": {"months": 6}}); Anthem Medicaid as second payer counts from the other payer's
   remittance (Indiana's 90 days, Kentucky's 365 days, a plan of no named state Indiana's). Dormant in detection today:
   run_secondary_not_billed counts a Medicaid second payer and lists none (snb_medicaid_second).

## Measurement
(filled in below when the final run is done)

## Hand-trace
(filled in below)

## Needs from others (failing tests in plans/progress/fx-windows_needs_tests.py, not collected by the suite)
1. F4 (ema_checks / ema_ledger) or F6 (the two cycles): call ema_reports.learn_payer_aliases(conn) once the weekly
   exports are read and before the EMA checks and the ledger's refresh use the payer groups. Today nothing calls it, so
   EMA's name-only paths group the VA network's claims (named after its administrator, payer id VACCN on the weekly
   exports' rows) as 'Optum (UHC)', commercial, dated by the UnitedHealthcare-administered plans' 12 months instead of
   the statute's 180 days. On the real copy: 151 EMA_DENIED items ($71,386) and 32 NOT_SENT items ($1,950) of the VA's
   network carry Optum's group and windows. Test: test_an_ema_denial_from_the_va_network_named_after_its_administrator_is_the_vas
   (both store kinds); it passes once learn_payer_aliases runs first (checked privately).
2. F4 (ema_ledger.refresh): the EMA summaries keep the payer group the name rules gave when Part A last ran, and refresh
   runs Part A only when the posting ledger changed. A release that changes the name rules re-runs detection
   (detect.RULE_FILES holds codes.py) but leaves the stored groups until the next Production report, so not_sent and
   unanswered_claims date a claim by the old group's windows. On the real copy one NOT_SENT item named 'Medicaid of
   Kentucky' kept 'Medicaid (other)'. The change: a hash of the name rules in meta; when it differs, Part A (or a
   re-group of the stored rows with rw_payer_group) at the next refresh. Test:
   test_the_ema_summaries_take_a_changed_name_rule_without_a_new_report (both store kinds); it passes when Part A runs
   again (checked privately with parts="AB").

## Observations for the final trace (no test: no date moves on the real copy)
- The 837 pass groups a never-answered claim by the payer id's answered claims (detect's group_of votes). Plans that share
  an id take the id's majority: 'MHS ... Indiana Medicaid' sends under Centene's id are 'Ambetter (Centene)', commercial
  (4 NO_ANSWER items, $377); 'Humana Healthy Horizons Medicaid of Kentucky' sends are 'Humana Medicare Advantage
  Medicaid' (payer_group_kind appends Medicaid to the MA group). The first-filing windows are the same either way
  (Ambetter's 90/180 days equal Indiana's managed care 90/180; Humana's Medicaid rows match either group), so no date
  moves; the group label is wrong. F3a's (detect's 837 pass) to judge.
- EMA names 'Humana Healthy Horizons Medicaid of Kentucky' 'Medicaid (other)' (the generic Medicaid rule comes before the
  Humana rules); its windows are Kentucky's managed care ones either way (120 days, last day 125; filing 365 days).

## Open questions
- The VA network's reconsideration window (Region 2: Kentucky, Indiana) has no source read: 90 days is used, not
  confirmed.
- Indiana managed care plans' dispute windows (60 days used, not confirmed) and whether the practice is in each plan's
  network (90 or 180 days to file).
- Whether a BlueCard member's Home Plan gave its own appeal directions on a given claim is not on file: the item keeps the
  clinical appeal's 180 days as its date and the dispute's two years as its last day.

## Next command
- `$S/tk/units/fx-windows/scripts/measure.sh <tag> <tree>` (one world copy at a time), then
  `python3 -I -B $S/tk/units/fx-windows/scripts/compare.py <before snap> <after snap> <out.json>`.
