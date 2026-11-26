# deadlines: every payer window from its primary source (progress)

Brief: `plans/briefs/2026-11-26_deadlines.md`. Rules: `plans/briefs/2026-11-26_common.md`, CLAUDE.md, HANDOFF section 0.
Branch `wip/deadlines`, from the integration tip 98070b8; the review round merged the tip again (bc29767, fix7d). Edits:
`remitwatch/codes.py`, `docs/RESEARCH_SUMMARY.md`, tests, and the smallest call-site edits in detect.py, states.py,
story.py and ema_checks.py (other units edit those files too: merge by hand, the edits are short and marked by their
comments).

## Inputs (read only, never in git)
- S=/tmp/claude-0/-home-user-Billing-Optimizer/96e81489-2ad2-5c70-991d-e00db2e22749/scratchpad
- The research table: `$S/tk/units/a17/windows/payer_windows.json` and `.md` (156 rows); sources in `.../windows/dl`.
- The 28 disagreements: `plans/review_2026-11-26.md`, section "Deadlines"; answers-8 and its two Indiana Medicaid tests in
  `$S/tk/review/since-answers-tests/test_answers_review.py` (ported into tests/test_payer_windows.py).
- The two reviews of this unit (findings deadlines-r1-1 to r1-9, r2-1 to r2-6) and their tests:
  `$S/tk/review/deadlines-r1-tests`, `$S/tk/review/deadlines-r2-tests` (run with REVIEW_TREE=<checkout>).
- Private folder (mode 700): `$S/tk/units/deadlines/` (scripts/, tools/, fix/, m2/: peeks, traces, snapshots).

## State
- [x] Round 1 (the 28 rows and answers-8): done, measured, suites recorded (see git history of this file, ae7a98f).
- [x] Review round: all 15 findings confirmed against the downloaded sources and fixed (outcomes below).
- [x] Reviewers' tests ported into `tests/test_payer_windows_review.py` (26 functions, 36 cases, invented data, both store
      kinds where an item is dated); 31 of them fail on ae7a98f. The reviewers' own folders: 29 of 30 pass on the branch;
      the one left (r1-7's) computes `window_end` on the Indiana rule directly, while every caller now dates a corrected
      claim through `codes.corrected_window`, which takes the earlier of the two windows (the ported test checks that).
- [x] Break-and-restore: 30 mutations of the round's fixes (`$S/tk/units/deadlines/fix/mutate_fix.py`), each fails a test,
      files restored byte for byte; the FEP reach fix broken two ways (Anthem only, every payer), both caught.
- [x] Integration tip merged again (bc29767, fix7d: the practice's day): no conflict.
- [x] docs/RESEARCH_SUMMARY.md: every changed source; tests/test_docs.py 38 passed, 1 skipped (the private word list).
- [x] Measured on a private world copy, tip bc29767 against the branch (numbers below), moved items hand-traced.
- [x] Full suite in python3 and in Python 3.11.1 (SQLite 3.40.0): see "Suites" below.

## Review round: each finding's outcome
- r1-1, r2-3 (high) FEP dated by the member's December 31: fixed. Anthem's manual ("Submission of Claims under the FEHBP
  and PSHBP") holds a participating provider to the agreement's limit: first filing and corrected claims 90 days from the
  service (the common limit, unconfirmed); December 31 named as the member's. A second claim to the FEP counts from the
  first payer's EOP (the manual's words).
- r2-1 (high) WellCare's Kentucky Medicaid plan named 'Medcaid' read as Medicare Advantage (180 days): fixed. The name
  rules read MEDI?CAID; WellCare's Medicaid group; its 12 months apply.
- r1-2 (medium) a second payer dated from the first payer's remittance with days only: fixed. `codes.second_claim_window`
  counts by the second payer's own clock (`cob`): the date of service for Medicare, CHAMPVA, TRICARE and GEHA (with their
  months and December 31), the first payer's EOP for the FEP and Ambetter; when not known, the safe date from the service
  and the last day the latest plausible reading, so a guess never makes a share a loss early.
- r1-3 (medium) WellCare of Kentucky's classes: fixed. 24 months for claim payment appeals (CARC 29, 97, B15, 234, 236, 96,
  204, 167: untimely filing, bundling, non-covered codes); 60 days (125 by KRS 205.534 since April 14, 2026) for clinical
  appeals (documentation, authorization, medical necessity) or an unknown reason.
- r2-2 (medium) Kentucky Medicaid's decide route said "appeal": fixed. `states.no_appeal_step`, `story._decide_step` and
  `story.NO_APPEAL_WORDS`: the step says a corrected or new claim within 12 months of the service (907 KAR 1:673), never
  appeal.
- r2-4 (medium) FEP disputes expired at the member's 6 months: fixed. A payment dispute 180 days safe, two years the last
  day (Anthem's Claim Payment Dispute); a clinical denial (CARC 50, 55, 56, 150, 151, 152) the member's 6 months.
- r2-6 (medium) Indiana Medicaid CO-208/B7 denial expired at the 60-day review while its 180-day resubmission was open:
  fixed. `states.windows` adds the resubmission window (180 days from the service) for a denied claim
  (`resubmit_denials`).
- r1-4 (low) Kentucky Medicaid write-offs inside the new-claim window counted as given up: fixed
  (`ema_checks._still_recoverable`).
- r1-5 (low) crossover 6 months named, not computed: fixed. Detection dates it from Medicare's remittance for the same bill
  and day (`Detector._first_answer_of`, 907 KAR 3:005) and raises the last day where later.
- r1-6 (low) Aetna's confirmed row reached its Medicaid plans: fixed. Commercial and Medicare Advantage only; Aetna
  Medicaid with no state: 90 days safe, 180 the last day (unconfirmed); with Kentucky named, KRS 205.534.
- r1-7 (low) Indiana corrected claim, paid unknown: fixed. `codes.corrected_window` takes the earlier of 60 days from the
  RA and 180 days from the service (`or_dos`).
- r1-8 (low) UnitedHealthcare corrected claim called confirmed: fixed (unconfirmed; 90 days is the guide's example).
- r1-9 (low) Anthem Medicaid of another state: fixed. A plan naming another state gets its own unconfirmed row that says
  so (`other_state`).
- r2-5 (low) KY managed care item before April 14 named the statute: fixed. `codes.rule_at` labels an item by the window
  its start date falls under.
- Found while porting (review6's guard): the round's FEP narrowing left a payer named for the program itself on default
  windows; the FEP rows now reach Anthem's group and those names, and no other payer.
- Two existing tests in tests/test_ema_wiring.py held the old FEP 6 months and the old second-payer clock: updated.

## Decisions (round 1, with the review round's changes)
- Two dates per item: `appeal_deadline` is the safe date, `legal_deadline` the payer's own last day where later. A window
  that was too long now gives the earlier date in both; one that was too short keeps a safe date no later than the true
  one and gets the true last day. An item expires only after its last day.
- Plans inside one group are told apart by keyword facts (codes.rule_facts: the 835's plan, the payer's name and id, the
  claim filing indicator, the reason, paid or not). An unknown keyword raises TypeError. A rule whose fact is missing is
  skipped, so an item that does not say which plan gets the shorter window, and its label says so.
- Medicare first filing: the safe date stays the calendar year; the last day moves to the next workday (424.44(c)).
- Medicare Advantage (Humana, UnitedHealthcare, Anthem) and Ambetter: the practice is treated as contracted and
  participating (volume and contracted payments say so); the non-contracted windows are named in the labels.
- UnitedHealthcare corrected claim: 90 days from the service (the guide's example, unconfirmed); the 12-month
  reconsideration stays as the last day.
- Wellcare Medicare Advantage first filing: 180 days from the service.
- Federal Employee Program: the agreement's limit (90 days, unconfirmed) for first and corrected claims; disputes as above.
  GEHA: first filing dated by December 31 of the year after the service (its brochure's words, the latest it takes any
  claim, so a guess never expires an item), with the 90 days a network agreement often sets named as the shortest common
  limit; unconfirmed (no GEHA provider source found); reconsideration 180 days safe, 6 months the last day, unconfirmed.
- Aetna: 180 days, confirmed, for commercial and Medicare Advantage plans; the fully insured 2 years named, not used.
- Anthem Medicaid: Kentucky plans 365 days to file and to correct, 90 days to appeal (KRS 205.534 since April 14, 2026);
  Indiana plans 90 days to file, 60 to dispute; a plan naming no state gets the Indiana (shorter) windows; a plan naming
  another state its own unconfirmed row.
- Kentucky managed care: KRS 205.534(1)(d)1 (since 2026-04-14) applied by the denial's remittance date: 120 days from
  receipt, presumed 5 days after the remittance (125); an earlier denial is labelled by the window before it.
- Kentucky Medicaid fee-for-service: no appeal of an ordinary denial (907 KAR 1:671); dated and routed as a corrected or
  new claim within 12 months of the service (907 KAR 1:673); a crossover 6 months from Medicare's adjudication where later
  (907 KAR 3:005).
- Indiana Medicaid: 180 days to file, 60 days for administrative review, a denied claim corrected and resubmitted within
  180 days of the service, a paid claim's adjustment 60 days from the RA, the earlier of the two when paid is unknown.
- TRICARE: a year from the service (first filing and a returned claim). CHAMPVA: every name it comes under;
  reconsideration 12 months; first filing a year from the service.
- Oxford and other unknown payers with Medicare's filing indicator: not Medicare; their windows are unconfirmed.
- Workers' compensation (Kentucky): 45 days from the treatment.
- A second payer never billed (SECONDARY_NOT_BILLED): the second payer's own clock; a loss only after the latest reading;
  a share past its limit is counted (ema counts snb_past_limit, snb_past_limit_cents).

## Numbers measured (review round: private world copy, tip bc29767 against this branch at 538571f, today 2026-11-24)
Aggregates only. Tools: `$S/tk/units/deadlines/tools/cycle_run.py`, `snap.py`, `diff.py`; the trace
`$S/tk/units/deadlines/fix/trace3.py`; outputs in `$S/tk/units/deadlines/m2/` (private).
- Findings: tip 16,632, branch 16,618 (14 left, none came on). Detection 262 s on the tip, 215 s on the branch; peak
  1,055 and 1,056 MB; no EMA step error on either.
- State moves: 13 items back from expired to Work now, $1,079.02 (Medicaid KY 10, $990.81: a corrected or new claim within
  12 months of the service; Medicaid IN 2, $24.57: a denied claim resubmitted within 180 days of the service; FEP 1,
  $63.64: a takeback's payment dispute two years from the EOP). 1 newly expired from Work now, $152.78 (Wellcare MA,
  sent on time: its 180 days from the service and the 60 days of appeal past them closed; its state says when and why).
  No item moved to or from Waiting.
- Due date earlier: 680 items, $37,908.98: second payers never billed 489, $17,230.41 (the safe date now counts from the
  service where the second payer's clock is not known or is the service; the last day mostly did not move);
  UnitedHealthcare and its Medicare Advantage plans' corrected claims 53, $10,476.98 (90 days from the service); FEP
  corrected claims 35, $3,365.84 (the agreement's 90 days); Medicaid IN 62, $2,806.73 (60-day review); first filings 19,
  $2,236.31 (Wellcare MA's 180 days, Indiana's 180); Anthem Medicaid 5, Medicaid KY 8.
- Due date later: 160 items, $20,186.28 (Medicaid KY 130, $16,314.29: 12 months from the service; Anthem Medicaid
  corrected claims 24, $3,227.16: 365 days from the service; CHAMPVA first filings 5, $629.61).
- Last day earlier: 140 items, $8,838.32 (Medicaid IN 62, $2,806.73; first filings 19, $2,236.31; second payers 39,
  $2,204.65; Anthem Medicaid 5; Medicaid KY 8; Aetna Medicaid 7, $84.20).
- Last day later: 266 items, $37,203.68 (Medicaid KY 130, $16,314.29; FEP 81, $12,212.69: payment disputes two years from
  the EOP; Anthem Medicaid 42, $7,155.79; first filings 8; GEHA 2; WellCare Medicaid 2, $202.08).
- Left the list: 14 second-payer items, $339.11. 10 ($245.94) have WellCare's Kentucky Medicaid plan as the second payer:
  EMA names it with 'Medcaid' (118 EMA lines), which the tip read as a Medicare Advantage plan; as Medicaid it is never a
  'bill it' item and is counted (ema counts snb_medicaid_second 74 to 84). 4 ($93.17, all expired on the tip: CHAMPVA 3,
  TRICARE 1) are past their second payer's own limit (a year from the service) and counted as lost to the deadline.
- Second-payer shares past their limit counted (the tip dropped them without a trace): 130, $4,551.93.
- Hand-trace (`trace3.py`, every item whose state moved, then up to 3 per payer group, signal and direction): 102 items
  re-derived from the raw 835 rows (lines, claims) and the sources' words, written apart from codes.py; all 102 agree
  (dates to the day where the trace states them, the latest window and the state otherwise). Not covered by the trace:
  40 (second payers with commercial clocks, first filings sent on time); the one newly expired item was checked by hand
  from its raw rows (sent inside the 180 days; the 60 days of appeal past them closed before the run's day).

## Suites
- The files this round touched, python3: test_payer_windows, test_payer_windows_review, test_features, test_ema_wiring,
  test_states, test_story, test_ema_checks, review6, test_docs, test_837_sends, test_workable: all pass (542 and 108 cases
  in two runs; test_docs skips only the private word list).
- Full suite, python3 (SQLite 3.5x), three shards on the merged branch: 2,964 passed, 29 skipped, 1 xfailed, 1 failed:
  tests/test_results.py::test_the_server_store_and_the_compact_serverless_store_give_the_same_figures, which passed alone
  three times, in its file (189 passed) and after the file before it in its shard (264 passed). Not this branch's: it
  compares two stores' Results figures, and the weekly snapshots carry times taken while the run goes (the current week's
  point 'at', a frozen week's 'frozen_at') that the test's _strip does not drop, so two runs a second apart on a loaded
  machine differ; results.py and its test are not changed here.
- Full suite, Python 3.11.1 with SQLite 3.40.0: 2,848 passed, 109 failed, 24 errors; every one of the 133 is a test that
  serializes a store image (no Connection.serialize in that build, as CLAUDE.md expects). None in a file this branch
  touched.

## Next command
- Merge into the integration branch by hand (detect.py, states.py, story.py, ema_checks.py are edited by other units; this
  branch already holds bc29767): then `python3 -m pytest -q tests/test_payer_windows.py tests/test_payer_windows_review.py
  tests/test_features.py tests/test_ema_wiring.py tests/test_states.py tests/test_story.py tests/test_ema_checks.py
  tests/review6/`.
- After the merge, the fix wave's F5 check is done here: `codes.normalize_payer` reads EMA's 'Medcaid' plan as WellCare's
  Medicaid group (tests/test_payer_windows_review.py, both store kinds; 118 EMA lines on the world copy).

## Open questions (for the owner)
1. Is the practice contracted with every Medicare Advantage plan it bills, and a participating Ambetter provider? The
   dates assume so; a non-contracted provider has 60 days from receipt (MA) and 90 days (Ambetter).
2. The FEP's limits in the practice's Anthem agreement: the filing limit (the common 90 days used) and whether the Claim
   Payment Dispute's two years apply to FEP claims (the manual makes no exception; used). GEHA's limits for a network
   provider (its brochure's December 31 dates the items; 90 days named).
3. Humana's Medicaid plans: the 835 does not say Kentucky or Indiana (120 days used).
4. A second payer's share past its limit is counted, not listed: should it be listed as lost to the deadline?
5. Aetna fully insured plans (2 years) and Aetna Better Health of Kentucky's own window before April 14, 2026 (90 days
   default used); an Aetna Medicaid plan with no state named: 90 days safe, 180 the last day.
6. WellCare of Kentucky's six months from a primary payer's payment and Anthem Indiana Medicaid's 90 days from the other
   payer's RA, for a claim they get second: named in the labels, not yet dated.

## Found, not this unit's (for the integrator)
- An EMA name for Kentucky Medicaid fee-for-service that puts the state after the word Medicaid is read as 'Medicaid
  (other)', so the Kentucky fee-for-service rules (no appeal, 12 months, the crossover) do not reach EMA-only items with
  that name (the name rules match 'KY MEDICAID' and 'KENTUCKY MEDICAID' only).
- Found in this round, not changed (not a review finding; it needs its own failing test, measurement and trace): Anthem's
  commercial row (codes.APPEAL_RULES, 'Anthem commercial: Claim Payment Reconsideration within two years') gives a clinical
  denial (CARC 50, 55, 56, 150, 151, 152) the two-year payment dispute as a confirmed safe date. The 2026 Kentucky manual
  (v2), Clinical Appeals: 'Providers and Facilities have one hundred eighty (180) calendar days to file a clinical appeal
  from the date they receive notice of Anthem's initial decision', and its Claim Payment Dispute 'does not apply to appeals
  regarding a clinical decision denial'. A window too long: the FEP rows split the two; the commercial row should too.
- The handoff's open point (task 475): Humana Medicare Advantage's corrected-claim rule is marked verified without a found
  source.
