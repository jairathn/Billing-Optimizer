# Unit B brief: detection wiring (the EMA checks, outcomes, states and the practice-manager rules inside the daily run)

S = /tmp/claude-0/-home-user-Billing-Optimizer/96e81489-2ad2-5c70-991d-e00db2e22749/scratchpad. Read ${S}/wire/PLAN.md first.

## Your files
remitwatch/detect.py (all but the one-line `ema_reports.ensure_schema` call in `ensure_schema`, which unit A1 adds),
actions.py, insights.py, alerts.py, codes.py, report.py, and remitwatch/ema_checks.py (wiring edits and the fixes your real-data
acceptance shows; the EMA fixer has finished it: read ${S}/build/fix_review_report.md). Tests: tests/test_remitwatch.py,
test_features.py, test_insights.py, test_resolution.py, test_workable.py, test_review_*.py, tests/review*/ (keep them passing;
change one only with a stated reason), test_ema_checks.py, new tests/test_ema_wiring.py.
Not yours: api.py and the pages (unit C: write what they need in ${S}/wire/needs_B.md), states.py/story.py (unit D, built in
parallel; read ${S}/wire/report_D.md and needs_D.md), archive_rules.py (unit B2, built in parallel; its hooks into detect.py are
added after you finish, by the B2 hook agent, at the points you leave for it: see "Hooks for B2" below), the intake (unit A1).

## Read first
- ${S}/build/needs_ema_checks.md in full: it is the wiring specification (sections 0-9, including the measured figures).
- Amendments ${S}/build/amendments.md sections 0, 4.3, 4.5, 4.6, 5, 8, 8.1, 8.2, 9, 13, 14.
- ${S}/wire/pm_product_gaps.md (every product gap the 66 traced cases found) and ${S}/pm/worksheet.json when it exists (the
  compiled, skeptic-checked version; it supersedes the gaps file where they differ).

## Build
1. The detection order of amendment 4.5, exactly (build_links; ledger refresh Part B when meta `ema:map_dirty` is set, per the
   contract agreed with A1 in ${S}/wire/needs_A1.md; classify_checks / turnaround / posting_lag; load, baselines, line and claim
   rules; run_ema_open_claims with the status dictionary, claim_answers and answers_through, then not_sent, rejected_claims and
   unanswered_claims; ema_denials (C5); aggregate rules and denial_as_contractual (C7); second rates; resolve_elsewhere (archive
   branches first), line_outcomes once, paid_in_ema (C6); unposted_remits (C4); written_off_in_ema (C2) and overlays; save with
   heir_keys, ema_heirs and keep_dirty; reconcile_summary, ar_accounted, unworked).
2. The new finding columns of PLAN.md in `detect.EXTRA_FINDING_COLS` (api.FINDING_COLS is unit C's: say so in needs_B.md).
3. The new signals and their keys, labels, work types and hide rules (needs_ema_checks.md sections 2-3): NOT_SENT,
   REJECTED_PRE_PAYER from the reports, NO_ANSWER from Production (G8), EMA_DENIED (C5), REMIT_NOT_POSTED (C4),
   EMA_DENIAL_AS_CONTRACTUAL (C7); `actions.NEVER_HIDDEN` gains NOT_SENT and REMIT_NOT_POSTED; insights work type `post`;
   alerts REMITS_MISSING and EMA_STALE; data_health additions; the weekly email additions.
4. Outcomes (amendment 8 and 8.1): auto_written_off with its evidence level (rule_proven only when the weekly ERA-lines export
   shows WRITE_OFF; posting_day otherwise), written_off_while_pending, lost_to_timely_filing, the precedence on one line, and the
   policy table (O11: `set_policy` / `policy_summary`; an accepted pattern turns matching write-offs into written_off_by_policy,
   out of the work queue, still findable). C6 closes only with its proof row and its used payment recorded so a newer pull that
   no longer shows the payment reopens the item; a person's status is never replaced.
5. States: at save time call `states.state_of(conn, item, outcome=..., today=...)` for every open item and store work_state,
   follow_up_on and state_basis (if states.py is not in the tree yet when you reach this, guard the import, leave the columns
   empty, and finish it once it lands; D's report says when).
6. The rules of amendment section 14 and every product gap in pm_product_gaps.md / worksheet.json that is a DETECTION rule
   (next-step wording is unit D's; page work is unit C's). At least: never include in a bulk "appeal everything appealable" an
   item EMA shows paid or settled, a credit, a line reprocessed in the archive, or a waiting item (where the bulk list is built:
   if it is in api.py, give C the exact rule); close from EMA payments on no-835 checks with the proof row, and "rebilled to
   <payer>, paid" when EMA moved the line to another payer who paid; CO-B11 or claim status 19 with a crossover carrier = forwarded,
   a 45-day chase timer, not non-covered; paid-before covers any denial on a line the right payer already paid, including a
   payer that is not the patient's plan denying after the primary and the Medigap paid (verify case C09: the skeptic found it was
   very likely the patient's own Medigap under another payer id; the item is still settled); a denied frequency-7 replacement
   while the original's payment stands unreversed = "paid; replacement denied; watch for a takeback"; a status-22 reversal then
   re-adjudication to PR = moved to the patient; UNDERPAID_VS_OWN_RATE needs the later-payment and reprocess check; an
   overpayment (credit) becomes a refund item (who overpaid, how much; owner decision); a PLB WO takeback EMA never booked =
   "post the takeback in EMA"; the missing-ERA list (C1) per check with the re-send request facts; payer-reprocessed patterns
   (e.g. Humana Medicare Advantage paying E/M with modifier 57 on the original claim after CO-16/N822 denials; UHC reprocessing
   77600 CO-234/M80 denials) become one pattern item with every still-open line listed and the batch route (call or dispute),
   never a modifier change; a provider-specific rate (one rendering clinician paid below the group's own rate for the same code
   and payer) becomes a pattern item that lists every affected line, including lines not yet listed; a new clinician whose
   claims a payer denies for credentialing (CO-147/M16, enrollment CARCs) becomes one credentialing item that names the payer and
   counts the claims and dollars, with the claims linked; claims stuck Rejected or never sent that the weekly exports never
   carried come from the reports (NOT_SENT / REJECTED_PRE_PAYER); an informational-only 835 (zero-dollar payment notice from a
   TPA whose money never came) must not count as paid; an 835 denial EMA never posted is a "post the denial" item (C4 for $0
   answers); A/R notes that show a dispute pending or a denial overturned feed the waiting state (states.py; give D the note
   patterns you find).
7. DETECT_RULES and `_rules_hash` as the spec says (2.7 to 2.11; the hash includes ema_reports.py, ema_ledger.py,
   ema_checks.py, states.py, story.py and archive_rules.py).
8. Hooks for B2: leave clearly named call sites (one function each, a no-op until B2's hook agent wires them) at the points
   ${S}/wire/needs_B2.md asks for (read it; if it is not written yet when you finish step 1, leave the call sites where
   resolve_elsewhere runs, where items are folded before save, where hide rules are matched, and where expected amounts are set).

## Gate 1 coverage targets (from the independent answer key; read ${S}/truth/g1_final/README.md when it exists, else
## ${S}/truth/g1_A/README.md and g1_B/README.md)
The independent money-first builder found, on 2025+ service lines, 5,070 lines ($497.5K nominal) still recoverable today, and
3,155 of them have NO RemitWatch item (open or closed). Every category must end up covered by an item in WORK NOW or WAITING
(or be explained line by line in your report):
- auto_written_off_appealable: 1,759 lines, $181.3K (852 without an item): EMA wrote off a denial and the window is open.
- denial_in_window: 1,034 lines, $141.6K (382 without): includes denials only EMA saw (C5) and denials on no-835 checks.
- short_vs_payer_rate: 732 lines, $68.8K (488 without): includes lines paid short on checks with no 835 (EMA-only payments):
  the underpayment rules must also run on EMA postings where no 835 exists, against the same payer's own consistent rate.
- open_balance_in_window: 506 lines, $44.6K (501 without): insurance balance open with no final answer (G8 and the A/R).
- unanswered_in_filing_limit: 243 lines, $29.2K (208 without): G8 / NOT_SENT / REJECTED_PRE_PAYER.
- secondary_not_billed: 517 lines, $16.3K (457 without): the primary paid and left a balance a secondary should pay, and no
  secondary claim or payment exists. This needs a NEW signal (SECONDARY_NOT_BILLED: who the secondary is, from the 835's
  crossover or EMA's secondary payer; the amount and basis; mark billed). Amendments listed it as later work; the owner's
  gate makes it part of this push.
- era_payment_not_posted: 279 rows, $15.6K (267 without): C4, judged per 835 claim and check (per-line matching overstated it
  about 8 times in the answer key's first attempt).
- credit_refund_due: 392 lines, $35.4K: refund items (owner/compliance decision), separate from money to recoup.
- expired_loss: 2,300 lines, $247.2K: not work; counted on Progress as process loss (expired state).
Deadlines: the answer key found codes.py's windows a few days EARLIER than the documents in places (Medicare Part B
redetermination 120 days from the remittance vs 125 with the 5-day presumed receipt, 42 CFR 405.942; Humana 540 days vs 18
months; Medicare Advantage default 60 vs 65 days; federal employee plans 180 days vs 6 months; Anthem commercial 730 days vs
2 years across a leap day). Keep the safe (earlier) date as the due date, and also store the legal limit so the pages can say
"file by <safe date>; the payer's limit is <legal date>". Give unit D and unit C the field name.

## Deadline bugs the two independent answer-key builders found in codes.py (fix them; they are in your files)
- Federal Employee Program: codes.py applies its 180-day window only when the plan name contains 'FEDERAL EMPLOYEE'. Other
  Anthem FEP claims (identify them the way the answer key did: the 835's plan/payer identifiers; read
  ${S}/truth/g1_B/README.md and its scripts) fall to Anthem commercial's 730 days, 4 times the documented 6-month federal
  window (5 CFR 890.105), so those items stay listed as appealable long after they expired (wasted work). Fix it.
- Windows a few days early (keep the safe date as the due date, store the legal limit too; see the coverage section): Medicare
  Part B 120 vs 125 days (42 CFR 405.942(a)); Medicare Advantage default 60 vs 65 (42 CFR 422.582(b)); Humana 540 days vs 18
  calendar months. Labels: Anthem MA and UHC marked verified=True while RESEARCH_SUMMARY lists them as not re-read; UHC-administered
  plans (UMR, Optum, Surest, UHC Integrated) marked unverified though the docs support 12 months for UMR/UHSS.
- The appeal clock starts at the payer's answer (the remittance with the denial), never at EMA's later write-off, and at the
  FIRST of a run of identical denials of one service (cases C59, C60). Medicare reopening applies to clerical errors only.
- Both builders' totals (A: 5,070 lines/$497.5K, 3,155 without an item; B: 4,077 lines/$414.7K, 2,290/$201K without an item)
  are being reconciled into ${S}/truth/g1_final/; use the final when it exists.

## Acceptance on the real data (aggregates only; PHI stays in ${S}/wire/B/)
Build a world with the world builder in PLAN.md (after the EMA fixer's report exists). Report, with the expected figure beside
each (needs_ema_checks.md section 9 and amendments section 5): NOT_SENT lines and dollars; REJECTED_PRE_PAYER; credits;
W4 buckets adding up exactly; C6 closures (count, dollars, by category; none on a facility claim, a separate-payee or a
misnumbered check); paid-before hints; C2 items inside the deadline; C3e NO_ANSWER drop; G8 claims; C1 total and causes; C4;
C5 (and 0 items on a link whose patient-day has an 835 line); C7; auto_written_off by evidence level, payer and CARC, the top 20
patterns and the still-appealable subset (spot-read 25 de-identified items: each a denial written off on its posting day with
nothing after it). Then: open items and dollars by signal and by work_state before and after; every closure keeps its proof;
nothing closed or answered by EMA claim status alone. The 66 practice-manager cases: for each, the item's signal, outcome and
state against the verified truth; list every mismatch with the reason. If ${S}/truth/confusion.py exists, run it on your world
store and report the matrix and the off-diagonal lines by reason (explain each class; do not tune to it blindly).
Both modes: run detection on the server store and on the serverless compact image of the same data and show the findings are
identical (ids, signals, states, amounts).

## Done means
All tests pass in the four environments of PLAN.md; ${S}/wire/report_B.md (no identifiers) and ${S}/wire/needs_B.md written;
world copies deleted.
