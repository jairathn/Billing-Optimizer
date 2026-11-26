# deadlines: every payer window from its primary source (progress)

Brief: `plans/briefs/2026-11-26_deadlines.md`. Rules: `plans/briefs/2026-11-26_common.md`, CLAUDE.md, HANDOFF section 0.
Branch `wip/deadlines`, from the integration tip 98070b8. Edits: `remitwatch/codes.py`, `docs/RESEARCH_SUMMARY.md`, tests,
and the smallest call-site edits in detect.py, states.py, story.py and ema_checks.py (other units edit those files too:
merge by hand, the edits are short and marked by their comments).

## Inputs (read only, never in git)
- S=/tmp/claude-0/-home-user-Billing-Optimizer/96e81489-2ad2-5c70-991d-e00db2e22749/scratchpad
- The research table: `$S/tk/units/a17/windows/payer_windows.json` and `.md` (156 rows); sources in `.../windows/dl`.
- The 28 disagreements: `plans/review_2026-11-26.md`, section "Deadlines"; answers-8 and its two Indiana Medicaid tests in
  `$S/tk/review/since-answers-tests/test_answers_review.py` (ported into tests/test_payer_windows.py).
- Private folder (mode 700): `$S/tk/units/deadlines/` (scripts/, tools/, peek and trace outputs, snapshots).

## State
- [x] Worktree and branch set up; brief, review section and research rows read.
- [x] Each source read in the downloaded file before its window changed; its words are in the rule's label.
- [x] Tests first (tests/test_payer_windows.py: 31 test functions, 39 cases with both store kinds), then codes.py, then the call sites.
- [x] docs/RESEARCH_SUMMARY.md: a section with every new or changed source and its words; open questions 2, 5 and 6.
- [x] Break-and-restore: 32 mutations, one per fix (codes.py rows, plan facts, call sites, the second-payer rule); each
      fails at least one test; files restored byte for byte (`$S/tk/units/deadlines/tools/mutate.py`).
- [x] Measured on a private world copy, the tip against the branch (numbers below); 85 moved items hand-traced from the
      raw 835 rows and EMA rows against the sources' words, all agree.
- [x] Full suite, python3 (SQLite 3.5x): 2,782 passed, 55 failed; the same 55 fail on the tip 98070b8 (test_fax 34,
      test_hotfix2 9, test_no_fax_route 5, test_notes_bodies_cli 4, test_hotfix2c 2, test_bulk_pause 1). Python 3.11.1
      with SQLite 3.40.0: 2,666 passed, 163 failed, 24 errors: the same 55, and 132 that fail the same way on the tip
      (131 of them: no Connection.serialize in that build, as CLAUDE.md expects). None is caused by this branch.

## Decisions
- Two dates per item: `appeal_deadline` is the safe date, `legal_deadline` the payer's own last day where later. A window
  that was too long now gives the earlier date in both; one that was too short keeps a safe date no later than the true
  one and gets the true last day. An item expires only after its last day.
- Plans inside one group are told apart by keyword facts (codes.rule_facts: the 835's plan, the payer's name and id, the
  claim filing indicator, the reason, paid or not). An unknown keyword raises TypeError. A rule whose fact is missing is
  skipped, so an item that does not say which plan gets the shorter window, and its label says so.
- Medicare first filing: the safe date stays the calendar year; the last day moves to the next workday (424.44(c)).
- Medicare Advantage (Humana, UnitedHealthcare, Anthem) and Ambetter: the practice is treated as contracted and
  participating (volume and contracted payments say so); the non-contracted windows are named in the labels.
- UnitedHealthcare corrected claim: 90 days from the service (the guide's example of the agreement's limit); the 12-month
  reconsideration stays as the last day.
- Wellcare Medicare Advantage first filing: 180 days from the service.
- Federal Employee Program (by plan) and GEHA: claims by December 31 of the year after the service; GEHA reconsideration
  180 days safe, 6 months last day; FEP the same, the provider dispute period unconfirmed.
- Aetna: 180 days, confirmed; the fully insured 2 years named, not used (the 835 does not say).
- Anthem Medicaid: Kentucky plans 365 days to file and to correct, 90 days to appeal; Indiana plans 90 days to file, 60 to
  dispute; a plan naming no state gets the Indiana (shorter) windows.
- Kentucky managed care: KRS 205.534(1)(d)1 (since 2026-04-14) applied by the denial's remittance date: 120 days from
  receipt, presumed 5 days after the remittance (125). WellCare of Kentucky: 24 months for coding, bundling and coverage
  denials, 60 days (125 by the statute) for utilization-management denials or an unknown reason.
- Kentucky Medicaid fee-for-service: no appeal of an ordinary denial (907 KAR 1:671); dated and routed as a corrected or
  new claim within 12 months of the service (907 KAR 1:673); the crossover 6 months (907 KAR 3:005) is named, not computed.
- Indiana Medicaid: 180 days to file, 60 days for administrative review, a corrected denied claim 180 days from the
  service, a paid claim's adjustment 60 days from the RA.
- TRICARE returned claim: a year from the service. CHAMPVA: every name it comes under; reconsideration 12 months.
- Oxford and other unknown payers with Medicare's filing indicator: not Medicare; their windows are unconfirmed.
- Workers' compensation (Kentucky): 45 days from the treatment.
- A second payer never billed (SECONDARY_NOT_BILLED): the payer's own last day decides when the share is a loss; a loss is
  counted (ema counts snb_past_limit, snb_past_limit_cents) instead of leaving no trace.

## Numbers measured (private world copy, today 2026-11-24, aggregates only)
- Findings: tip 16,632, branch 16,626 (7 left the list, 1 came on). Detection 404 s, peak 1,052 MB, no EMA step errors.
- Back from expired to Work now: 10 items, $990.81 (Medicaid KY: a new claim within 12 months of the service).
- Newly expired from Work now: 19 items, $1,140.39: Anthem / BCBS (FEP plans) 13, $667.97 (states.py now reads the plan,
  as detection did: the FEP's own 6 months had passed); Medicaid IN 3, $254.42 (60-day review); Wellcare MA 1, $152.78
  (180 days); second payer Wellcare MA behind UMR 1, $50.00 and behind Medicare KY 1, $15.22. Each says when and why its
  last window closed.
- Due date earlier: 158 items, $17,298.74 (UHC MA 25 $5,776.05; UHC 28 $4,700.93; Medicaid IN 71 $4,029.18; Wellcare MA
  10 $1,013.86; Anthem Medicaid 5 $768.97; Medicaid KY 8 $737.46; WellCare Medicaid 1 $101.04; Aetna Medicaid 7 $84.20;
  UMR 1 $50.00; Medicare KY 2 $37.05).
- Due date later: 195 items, $23,637.94 (Medicaid KY 130 $16,314.29; Anthem / BCBS 35 $3,365.84; Anthem Medicaid 24
  $3,227.16; CHAMPVA 5 $629.61; WellCare Medicaid 1 $101.04).
- Last day earlier: 104 items, $6,720.72. Last day later: 241 items, $35,625.92.
- Left the list: 7 second-payer items, $158.89 (Wellcare MA's 180 days passed more than a month ago; 4 were Work now,
  $105.20; 3 expired, $53.69). The first live run's vanished check keeps the 4 on the list with a note until a person
  accepts the change on the Data tab. Came on: 1 second-payer item, expired, $148.64 (Medicare's last day moved off a
  weekend). Past-limit second-payer shares now counted: 131, $4,449.02.
- Self-check past_window on the branch run: 0 offenders.

## Next command
- Merge into the integration branch by hand (detect.py, states.py, story.py, ema_checks.py are edited by other units):
  then `python3 -m pytest -q tests/test_payer_windows.py tests/test_features.py tests/test_ema_wiring.py tests/test_states.py
  tests/test_story.py tests/test_ema_checks.py tests/review6/test_r6_appeal_rules_and_version.py`.

## Open questions (for the owner)
1. Is the practice contracted with every Medicare Advantage plan it bills, and a participating Ambetter provider? The
   dates assume so; a non-contracted provider has 60 days from receipt (MA) and 90 days (Ambetter).
2. The FEP's provider dispute period at the local Blue plan (the item uses the member's 6 months as the last day).
3. Humana's Medicaid plans: the 835 does not say Kentucky or Indiana (120 days used).
4. A second payer's share past its limit is counted, not listed: should it be listed as lost to the deadline?
5. Kentucky crossovers: 6 months from Medicare's adjudication (907 KAR 3:005) is named, not computed.
6. Aetna fully insured plans (2 years) and Aetna Better Health of Kentucky's own window before April 14, 2026 (90 days
   default used) are not known from the 835.
