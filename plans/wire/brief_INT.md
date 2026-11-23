# Unit INT brief: logic integration (the archive rules, the states and the EMA checks working as one, measured on real data)

S = /tmp/claude-0/-home-user-Billing-Optimizer/96e81489-2ad2-5c70-991d-e00db2e22749/scratchpad. Read ${S}/wire/PLAN.md first
(the owner's words and NON-NEGOTIABLES), then every ${S}/wire/report_*.md and needs_*.md (A1, A2, B, B2, D at least).

## Why this unit exists
Units B (detection wiring), B2 (archive-only rules, a module with no call sites yet) and D (states, story, verdict, next step)
each finished their part. Nobody has yet run them together. The owner's test is a person hand-checking items: an item RemitWatch
says to work that was already worked or paid, or an item it calls resolved that still has money on the table, ends the product.
This unit makes the three parts one, measures the result against the frozen answer keys, and fixes what the measurement shows.

## Your files
remitwatch/detect.py, remitwatch/archive_rules.py, remitwatch/states.py, remitwatch/story.py, remitwatch/insights.py,
remitwatch/codes.py, remitwatch/ema_checks.py; and, ONLY after ${S}/wire/report_A1.md has a section "After the reviews and fixes"
(A1's fix is done), remitwatch/ema_reports.py and remitwatch/ema_ledger.py for item 7 below. Tests: tests/test_archive_rules.py,
tests/test_ema_wiring.py, tests/test_states.py, tests/test_story.py, tests/test_ema_checks.py, new tests/test_integration_logic.py.
Not yours: actions.py hide grain and /api/hide (unit C1 does them together), api.py and pages (C1, C2), pipeline.py/cloud.py
(A1, then the phase-3 hook-in agent), docs (F). Ask through ${S}/wire/needs_INT.md.

## Build, in this order
1. Wire the archive rules (needs_B2.md section 1, Option A: hook_expected, hook_archive_rules, hook_fold; hook_hide stays a
   no-op). Update tests/test_archive_rules.py::test_without_the_hooks_nothing_changes to the wired expectation.
2. States for the archive rules' reclassifications (needs_B2.md section 3) in states.py: 'done' + 'proven' -> done,
   'waiting' -> waiting with a follow-up date from the payer's response days, 'likely' is never done without a person;
   state_basis = the action's label. The new signals' state rules (needs_B.md section 2.3: SECONDARY_NOT_BILLED,
   CREDIT_BALANCE, FORWARDED_TO_CARRIER (find why 98 of 219 read done), EMA_DENIAL_AS_CONTRACTUAL) and the four rule-8
   signals (DENIED_REFERRAL, DENIED_COVERAGE_DATES, DENIED_PLAN_EXCLUSION, DENIED_CONTRACT_RATE). appeal_block for every
   reclassification that must never be appealed in bulk (paid_before, overpaid, answered_same_day, moved_to_patient,
   settled_by_prior_payer, dropped_on_correction, secondary_paid until checked). story.py: the verdict sentence and the
   decisive rows for every reclassification and closure (the proof rows B2 attaches), next_step for the new signals.
3. The ledger's reversal pairing (needs_B2.md section 6): pair a reversal with its payment when the payer id or name changed
   (archive_rules._same_payer_loose; never by a many-payer clearinghouse id alone) in detect._build_ledger; measure before and
   after on the real world (the 184 lines, $23,527 B2 found) and check 25 by hand from raw rows. OA-100 (payment made to the
   patient): paid to the patient, not a denial (the practice collects from the patient); measure how many items it touches.
4. Case C05 (an underpayment on a line EMA holds as a credit) and the other practice-manager mismatches B listed (C27, C40, C50,
   C54, C56, C57, C58, C60, C65): fix each that a rule can fix; for each you leave, write why (data the store does not keep,
   an owner decision) in needs_INT.md. The verified truth is ${S}/pm/worksheet.json.
5. Payer group names EMA splits ("UHC Medicare Advantage" beside "UnitedHealthcare Medicare Advantage", "Anthem Medicare
   Advantage" beside "Anthem / BCBS Medicare Advantage"): one group each, so the owner's write-off policy and every pattern count
   cover the whole pattern. The equivalence lives in one place (codes.py's payer grouping), tested with invented names.
6. FEP plans without "FEDERAL EMPLOYEE" in the name (brief_B.md's codes.py bug): check whether unit B fixed it; if not, fix it
   with a test. Same for the appeal clock starting at the payer's answer (or the first of identical denials).
7. (After A1's fix only.) needs_D.md: classify each A/R Last Billing Note when it is read (states.classify_note -> ema_ar
   columns note_kind, note_date, note_wait; never the note text), keep them in ema_reports.serving_tables, and use them in
   states.work_evidence (amendment 13.1: an A/R note about an appeal, call or resubmission is evidence of work -> WAITING with the
   note's own "allow N days"). If A1's fix is not done when you reach this step, write the exact change into needs_INT.md for the
   verification fix loop instead and go on.
8. Every automatic decision your files make carries its rule id, rule version and evidence keys (unit H's decision log will
   read evidence.archive_rule, state_basis and the closure proof rows: keep them complete and stable).

## Measure (real data, aggregates only; one world in ${S}/wire/INT/, deleted at the end)
Build the integrated world: python3 -I -B ${S}/build/ema_checks/accept/b0_build.py ${S}/wire/INT/world, then bring in the live
statuses the way the end-to-end runs will (the billers' statuses, notes, faxes and hide rules from
${S}/live_state_1122/store.sqlite: the cli import-cloud path, or the A1 report's recipe), then one full detection run.
- Gate 1 (missed money) and gate 2 (wasted work): use the frozen answer keys if ${S}/truth/HOW_TO_RUN.md exists
  (confusion.py); else the two builders' outputs (${S}/truth/g1_A/, g1_B/ and g2_A/oracle.py, g2_B/) and say which. Report
  before (B's numbers, no hooks) and after (everything wired): lines and dollars of recoverable money with an item in WORK NOW
  or WAITING; and among items in WORK NOW, the share the oracle says need no work, by reason (paid before, paid after, another
  payer paid, worked and waiting, contractual or policy, duplicate, expired, can't be checked). Target: wasted share of WORK NOW
  under 10% and no WORK NOW item that the oracle shows paid in full; every recoverable line of the key either in WORK NOW,
  WAITING with a date, or listed in your report with the reason it cannot be.
- The 66 practice-manager cases: verdict and next step against pm/worksheet.json; target 64 or more, the rest explained.
- Both modes: the server store and the compact serverless image give identical items (every compared field), and the
  serverless path's PEAK MEMORY on the real compact image: unit B measured 2.4 GB in an in-memory emulation of the server image,
  over Vercel's 2,278 MB. Measure the real thing: ${S}/live_state_1122/store.sqlite is Vercel's actual image; run the serverless
  detection on it after ingesting the reports (A1's serverless path or the world builder's equivalent) with getrusage peak RSS.
  If it is over 1.75 GB, find where the memory goes (per step) and fix it in your files (chunked reads, no whole-table Python
  lists, temp tables) until it is under, without changing any result. This is a release blocker.
- Detection time on both hosts (the live baseline: detect 51.7 s, whole run 98.6 s of 240 s on Vercel).

## Tests and environments (CPU is shared by many agents: be frugal)
Each fix gets a failing test first (invented data), then break and restore. During the work run your own tests and the related
suites. At the end run the full suite ONCE in two environments: ${S}/fix/venv2_compat312/bin/python (AWS and CI) and
${S}/fix/venv_sqlite340/bin/python (Vercel's SQLite; serialize-only failures expected). The orchestrator runs all four before
release. Docs failures in tests/test_docs.py belong to unit F.

## Done means
Everything above, ${S}/wire/needs_INT.md written (exact changes for other files), the world deleted, and your structured return
(the orchestrator saves it as report_INT.md): summary, files changed, tests, acceptance (each gate with before/after numbers),
needs, risks.

## ADDED 2026-11-22 23:40 (after unit D's report; read ${S}/wire/report_D.md and needs_D.md)
- Apply the orchestrator decisions in PLAN.md (EXPIRED at window close; bulk on the safe date; policy key; "Check first" route).
- From needs_D.md aimed at B's files (now yours): codes.py meanings for CARC 114 and B9, RARC N104 and MA63; UMR's 180-day appeal
  window; VACCN = VA Community Care; A1 with N130 on an otherwise paid claim is not medical necessity; expected amounts priced by
  place of service and procedure rank (C30, C57, C58: the second procedure's 50% at an ASC is not an underpayment); global-period
  details (C16, C17, C22, C31, C33, C39); provider-level rate shifts (C29); the earliest charge-entry date as the floor for
  NO_ANSWER 'sent' (C40, C42, C43); a CO-18 denial settled by the same remittance's re-answer (C12); the B13 shared_line in
  ema_checks.line_outcomes (C07); the ranking column (expected value) kept for unit G's expected_value_cents (store D's
  ranked_cents now as expected_value_cents with recovery_rate and recovery_basis from states.recovery_of; G's later rates replace
  them through the hook-in agent).
- states.work_evidence reads ema_ar.note_kind, note_date and note_wait WHEN THOSE COLUMNS EXIST (the hook-in agent adds them to
  the A/R reader after A1's fix); test it with invented rows that have them and a store that has not. Item 7 of this brief moves
  to the hook-in agent; so do A1-file items in needs_D.md (the link map rows for bills with no 835, the wider serving tables).
- From needs_A1.md (aimed at detect.py, now yours): when meta ema:map_dirty is set, Detector.run() step 2 (after build_links,
  before load) calls ema_ledger.refresh(self.store, None, parts="B"); keep A1's last line of detect.ensure_schema; ema:keep_dirty
  stays set by detection's save. Check whether unit B already did it before changing anything.
- Unit SWEEP (${S}/wire/brief_SWEEP.md) edits A1's files at the same time as you; you do not touch them, they do not touch yours.
- TIMING: unit D's fixer may still be editing states.py, story.py, tests/test_states.py and tests/test_story.py when you start. Do
  NOT edit those four files until ${S}/wire/report_D.md has a section "After the reviews and fixes" (D's fix is done). Do your
  detect.py, codes.py, archive_rules.py, insights.py and ema_checks.py work first (items 1, 3, 5, 6 and the ADDED items there),
  then states.py and story.py. Same for B2: archive_rules.py waits until ${S}/wire/report_B2.md has that section.

## ADDED 2026-11-22 23:55: review findings across units
Every independent review's findings are saved as ${S}/wire/review_findings/review_<unit>_<lens>.json (refreshed by the orchestrator).
A finding aimed at YOUR files that its own unit's fixer could not fix (another owner's file) is yours: check it against the code
(it may be fixed already), fix it with a failing test first, and list it in your needs file's status list.

## ADDED 01:15: the owner's "no dollar amount is too small" (PLAN.md orchestrator decision 9)
Remove every dollar floor PLAN.md decision 9 lists that sits in your files (failing test first, then measure on the real world how
many items and dollars each removal adds), and search your files for any other value floor (a cents comparison that drops, hides or
skips an item). What is not money (a cent of rounding, B13 token pieces, informational remittances) stays out, named by what it is.

## ADDED 01:30: dollars per biller-minute (PLAN.md orchestrator decision 10)
Implement the parts of decision 10 that sit in your files, with tests on invented data and a measurement on the real world (how
many WORK NOW items are above and below $1 a minute, and their dollars).

## ADDED 02:10: no default 'accept' (PLAN.md orchestrator decision 14)
ema_checks.py's never_paid patterns and any other `or "accept"` default become 'decide' (owner decision pending): out of Work now,
on the policy page and Today, counted as given up without a decision until the owner decides. Test it; measure the patterns,
items and dollars that move from silently accepted to 'decide'.

## ADDED 2026-11-23 03:35: chaos round 0, the live hotfixes and the guard audit (all yours where they touch your files)
1. ${S}/wire/chaos_r0_backlog.md: every item marked [INT] (0e-4: a payment taken back in full creates an item; H8's feed date in
   the open-claim pass; 0f-1 to 0f-5 in codes.py and detect.py; 0c-1 and 0c-3 in detect.py's save).
2. The live hotfixes being built now in worktrees from main (each has a brief and, when done, tests that lock in its guarantees):
   ${S}/hotfix2/wt (H8 feed date), ${S}/hotfix2c/wt (practice-local day, calendar months and years, Medicare +5 days, follow-ups
   while the feed is stale), ${S}/hotfix2d/wt (supersede only overlapping copies, the store never starts empty where one existed,
   faxes follow the heir, follow-ups never chase a claim shown paid). For every guarantee that lands in your files: copy the
   hotfix's tests for it into the dev tree unchanged and make them pass on push 2's code (do not copy the hotfix code blindly: push 2
   rewrote these files). If a hotfix is not final when you finish, list what is left in needs_INT.md for unit PORT.
3. ${S}/guard/GUARDRAILS.md (the class guards and every instance with its owner; it may appear while you work: check for it before
   you finish): every instance owned by INT, in particular the one-input-moves-a-shared-value class (L1) for detect.py, states.py,
   insights.py and ema_checks.py.
4. PLAN.md orchestrator decision 16 (04:05): no probability floor. Unit B's fix folds codes a payer pays on 5% or less of at least
   20 answered lines into owner patterns (252 EMA lines, $111,436; 579 remittance items): those patterns default to 'pursue in a
   batch per payer' with expected value and cost shown, never to 'accept' or a passive 'decide'; check states.py and story.py for
   any rule that drops, hides or deprioritizes an item for a low recovery rate alone (expected value ranks; nothing is cut).
5. Verified by the leverage research: CARC 253 (sequestration, 2%) is statutory and never money to chase; confirm no item is made
   from it (and that its dollars are excluded from underpayment figures, not hidden: named as sequestration).
6. (orchestrator decision) Unit B's END_DATED_CATEGORIES (timely-filing and non-covered denials now carry their appeal window as a
   deadline) is the intended behaviour: a timely-filing denial is appealable with proof of timely submission inside that window.
   Adapt the fixtures C1 left failing (test_desk x6, test_claims x1, test_review7 x1, review6 x1; see needs_C1.md 2.5) to it, keeping
   every count-equals-list assertion.
7. Host parity (C1 acceptance, 49 of 50): on Vercel one item tells a biller to bill a second payer that was already billed, because the
   dashboard copy lacks the rebill rows next_step reads. Every row next_step and story read must be in the serving copy, or the
   serving copy stores the next step computed at the run (as the decisions table does); with a parity test over a sample of items.
