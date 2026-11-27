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
- [x] Measurement on a private world copy, tip vs branch, A17 classes (below); the final code measured again.
- [x] Hand-trace of 30 moved items from the raw rows (below): 29 right, 1 unclear, 0 wrong.
- [x] Suites: the whole suite in python3 (3.13.18, SQLite 3.53.1) on fffc96f: 3,373 passed, 29 skipped, 1 xfailed, 3 failed,
      the 3 this branch's own rules changed (tests/test_story.py's Anthem CO-50 window, tests/test_archive.py's April
      CO-50 denial), fixed at 0892a41 and 346b3aa; then the files the rules touch, on the final code: 615 passed. Python
      3.11.1 with SQLite 3.40.0, the whole suite on 38bedf8: 3,193 passed, 13 skipped, 1 xfailed, 153 failed and 47
      errors, every one but the story test above stopping at `Connection.serialize`, which that build lacks (CLAUDE.md:
      only tests that serialize a store image may fail there); on the final code, the changed and window files
      (test_story, test_archive, test_fx_windows, test_payer_windows*, test_workable): 309 passed, 4 failed, the 4 the
      store image's serialize.

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

## Measurement (private world copy, day 2026-11-24, one server cycle; tip = 56b538e, the same code as 59f6822)
Run on fffc96f (the branch with the BlueCard rows; 38bedf8 adds only the state read after "of", measured separately
below). Merges in the measurement: the integration tip 59f6822 (no code); batch 1 was not on the integration tip yet.
Detection 381 s at a 1,104 MB peak (no step error).
- Open items 12,929 ($4,208,193.06) -> 12,930 (the same dollars); Work now 6,714 ($2,041,479.95) -> 6,702
  ($2,040,727.57). 377 items moved, none vanished.
- Anthem / BCBS clinical denials (CARC 50, 55, 56): the date shown earlier on 38 ($9,155.50): CLAIM_DENIED_UNPAID 14
  ($7,369.93), DENIED_NONCOVERED 20 ($1,181.02), DENIED_MEDICAL_NECESSITY 2 ($484.65), EMA_DENIED 2 ($119.90). The last
  day earlier on 23 of them ($7,956.38), Anthem's own plans; the other 15 (BlueCard or another state's plan, 13; EMA's,
  2) keep the dispute's two years. Newly expired 13 ($752.38): DENIED_NONCOVERED 12 ($701.42), DENIED_MEDICAL_NECESSITY
  1 ($50.96), all Anthem's own plans with a CO-55 or CO-50 remittance more than 185 days old. (Before the BlueCard rows,
  the first branch run expired 22, $1,572.07: 9 of them, $819.69, were BlueCard, another state's or EMA's.)
- Basis words only, dates unchanged, 337 ($54,004.34): Humana Medicare Advantage 293 ($43,972.31; the corrected-claim
  label no longer calls a tip sheet confirmed): DENIED_MISSING_MODIFIER 182, DENIED_MISSING_INFO 83, DENIED_CODING 16,
  EM_BUNDLED_MOD57_MINOR 8, EM_BUNDLED_NO_MODIFIER 4; VA Community Care 44 ($10,032.03; the statute's words):
  NO_ANSWER 36, REJECTED_PRE_PAYER 8.
- 'Medicaid of Indiana' read as Indiana's program: 1 EMA_DENIED ($63.89), expired before and after (its dates move
  later by the group's own posting delay).
- Appears 1, $0: a NO_ANSWER sent to Anthem's Indiana Medicaid plan (read as Indiana's program before, whose
  remittances stop before its due day, so it was counted and not listed).
- A17 (W view), tip -> branch: CORRECT|open 2,215 -> 2,202 and CORRECT|closed 10,445 -> 10,458 (the 13 newly expired,
  which A17 also calls lost to the deadline: lot DENIED in scope 342 -> 345, out of scope 102 -> 112, off_list:expired
  9 -> 12); ALREADY_DONE|open 1,278 -> 1,279 (the appearing $0 item, see the hand-trace). No other item's A17 verdict
  changed, moved or not.
- The final code (38bedf8, the state read after "of"), a second run on a fresh copy (detection 467 s at a 1,108 MB peak,
  no step error): the same as above plus 5 NO_ANSWER items of a Kentucky managed care plan EMA and the 837s name
  '<plan> Medicaid of Ky' ($56.47): their date moves 30 days later (the first-filing limit plus KRS 205.534's 120 days
  of appeal instead of the 90-day default). Open items, Work now and every other item as above (382 moved against the tip).

## Hand-trace (30 moved items, from the raw rows: the 835 claim, line and CAS rows, EMA's summary rows, the 837 sends)
Each item's dates were worked out again by hand from the raw dates and the source's words, then compared with what the
branch wrote. Verdicts: 29 right, 1 unclear, 0 wrong (30 items).
- Anthem's own plans, CO-55 or CO-50 on the 835, newly expired (180 days from the remittance as the date shown, 185 the
  last day, both past): 20a09dbf841aaceeea04bf7d DENIED_NONCOVERED $59.95, 86ca7816d5076f64febe0189 DENIED_NONCOVERED
  $59.95 (EMA also shows an earlier CO-210, another reason, so the CO-55 remittance starts the clock),
  0c0e3bba7d4a7a3e1394b468 DENIED_NONCOVERED $59.95, 48d261c0190012a39cf9ff64 DENIED_MEDICAL_NECESSITY $50.96: right.
- Anthem's own plans, whole claims denied CO-50, expired before (the two years had passed) and after, dates earlier:
  1cfd80257c9c0d537b5a2d93 CLAIM_DENIED_UNPAID $788.68, 40a8030f0ce026fa51faeb51 CLAIM_DENIED_UNPAID $741.85,
  1877fd1e298de09c88e7a4d0 CLAIM_DENIED_UNPAID $741.85, af97a80255178c6ce2ec04c8 DENIED_MEDICAL_NECESSITY $433.69: right.
- BlueCard members (the 835's plan: another state's two-letter code, BlueCard's words or ITS HOST): 180 days as the
  date shown, the dispute's two years as the last day, still listed: f9f15a3956306ec0cdf97396 DENIED_NONCOVERED $59.95,
  2c11e2b2f5b9575438908ece DENIED_NONCOVERED $59.95, 8b87b6b3fab4e0c8d45f1e80 DENIED_NONCOVERED $59.95,
  7950abbdced9e69a7300d2c9 CLAIM_DENIED_UNPAID $200.02 (PR-55), 6696d7558c76d18215076306 CLAIM_DENIED_UNPAID $59.95,
  305bae34e9a19e832c2a78e9 CLAIM_DENIED_UNPAID $88.67 (PR-50, already done): right. Whether a member of another Elevance
  state's plan counts as a BlueCard member for this practice is not on file; the item keeps both dates, so it is worked
  early and does not leave the list early either way.
- EMA's own clinical denials (no plan named): 8535d8d1be007ae9bd40e194 EMA_DENIED $59.95 (CO-55) and
  9166d036e64794a06e8c2f36 EMA_DENIED $59.95 (CO-50): 180 days from EMA's posting less the payer's usual 5-day delay,
  last day two years: right.
- 84c6128e241cf91fed4ca54f NO_ANSWER Anthem / BCBS Medicaid, $0, appears: an 837 send to Anthem's Indiana Medicaid plan
  with no 835. The group is right (the name is Anthem's plan, not Indiana's program), and with it the payer's remittances
  reach past the claim's due day, so it is listed. A17 calls its one unit (G2211) not payable by the plan: whether a $0
  claim of a code the plan never pays should be listed as never answered is the 837 pass's rule. Unclear.
- f08d747771bd5fd7fc44b73d EMA_DENIED 'Medicaid of Indiana' $63.89: now Indiana's program (IHCP's 60-day administrative
  review); expired before and after; its dates move by the group's own posting delay (9 days, the catch-all's 50): right.
- VA Community Care, first filing 180 days after the service in each (38 U.S.C. 1703D(b)), dates unchanged:
  663853191add5c59f4e55a2c NO_ANSWER $65.08 (expired), 917bff5041201c0e73055374 NO_ANSWER $65.08,
  c7417f54f780099f7015e62d NO_ANSWER $130.16, fd7c792ab50a72c335d2abff REJECTED_PRE_PAYER $0,
  da8e05fad801864e563ddd54 REJECTED_PRE_PAYER $127.80: right.
- Humana Medicare Advantage, a corrected claim within a year of the service and the dispute's 18 months as the last
  day, dates unchanged, the label no longer says the tip sheet was confirmed: 0c0a9e1f96f0f0bb811366b4
  DENIED_MISSING_MODIFIER $79.14, 79ea3ae195be1f17357ee425 DENIED_MISSING_INFO $71.62, 3dfa6855292807c5d55ef9c8
  DENIED_CODING $44.70 (expired), 08c638937c6b30e8123deb22 EM_BUNDLED_MOD57_MINOR $159.14, 10abcf26dd765627032d7b98
  EM_BUNDLED_NO_MODIFIER $40.86: right.
- A Kentucky managed care plan named '<plan> Medicaid of Ky', never answered, sent on time (the final code's run): the
  date shown is the first-filing limit (365 days from the service) plus 120 days (KRS 205.534(1)(d)1), was plus 90:
  58bb593548102cfc5b031450 NO_ANSWER $56.47 and d4dd79dd2ccb0737bcf378d6 NO_ANSWER $0: right.

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

- The appearing NO_ANSWER item (84c6128e241cf91fed4ca54f, $0): a claim whose one code (G2211) A17 calls not payable by
  the plan is listed as never answered now that its group lets the check see it. Whether a $0 claim of a code the plan
  never pays belongs on the never-answered list is the 837 pass's rule (F3a's).

## For the merge
- codes.py is this unit's only rule file; the call sites (ema_checks.ema_denials, detect._emit_proto) only pass `carc`.
  detect.RULE_FILES holds codes.py, so the release re-analyses the archive once.
- F4's wip/fx-ema adds ema_checks._catch_all_company, whose docstring says the state's own program 'Medicaid of <state>'
  lands in 'Medicaid (other)'. After this branch, Kentucky's and Indiana's land in 'Medicaid KY' and 'Medicaid IN' (a
  program of another state still lands in the catch-all); the logic still holds (a name in the catch-all is told by its
  company, a group outside it by the group), only the docstring's example is out of date.
- tests/test_payer_windows_review.py and tests/test_workable.py hold expectations this branch changed on purpose:
  'KY MEDCAID' (with the state first) is Kentucky's program, and the VA filing label quotes 38 U.S.C. 1703D(b).
  tests/test_story.py keeps its expectation (an Anthem CO-50 denial whose remittance names no plan stays open to the
  dispute's two years); only its docstring says why.

## Open questions
- The VA network's reconsideration window (Region 2: Kentucky, Indiana) has no source read: 90 days is used, not
  confirmed.
- Indiana managed care plans' dispute windows (60 days used, not confirmed) and whether the practice is in each plan's
  network (90 or 180 days to file).
- Whether a BlueCard member's Home Plan gave its own appeal directions on a given claim is not on file: the item keeps the
  clinical appeal's 180 days as its date and the dispute's two years as its last day.

## Next command
- Merge the integration tip again when batch 1 lands, then `python3 -m pytest -q tests/test_fx_windows.py` and the files
  the rules touch (tests/test_payer_windows*.py, test_story, test_archive, test_features, test_workable, test_ema_checks,
  test_837_sends, test_states, test_ema_wiring, test_remitwatch). A new measurement: `$S/tk/units/fx-windows/scripts/
  measure.sh <tag> <tree>` (one world copy at a time; the scripts stay in the private folder).
