# Unit deadlines: every payer window from its primary source

Why: the A17 unit's research (2026-11-26) read the filing, corrected-claim and appeal windows of the practice's 52 payer
groups from primary sources (the CFR, CMS manuals, Kentucky and Indiana statutes and regulations, the payers' own 2026
manuals) and found 28 rows where `remitwatch/codes.py` disagrees. The review of 2026-11-26 found one of them on its own
(answers-8, Indiana Medicaid). A window that is too long lets a biller miss the real deadline (money lost); one that is
too short marks an item expired while it can still be appealed (money given up). Both break the owner's bar.

Inputs (the research's public table and its notes; public facts, no patient data):
- `$S/tk/units/a17/windows/payer_windows.json` and `payer_windows.md` (156 rows: 95 verified, 61 unverified, each with
  its source, section, revision date, URL and a quote of 40 words or fewer), its scripts in `.../windows/scripts`, and the
  downloaded sources in `.../windows/dl`. If the A17 branch has committed them (`plans/tools/a17/payer_windows.json`),
  use that copy.
- The 28 disagreements, in the integrator's words: `plans/review_2026-11-26.md` (section "Deadlines", added by the
  integrator) and the review's answers-8 with its failing test
  (`$S/tk/review/since-answers-tests/test_answers_review.py`, the two Indiana Medicaid tests).

What to do:
1. For each disagreement, decide with the source in hand, and change `remitwatch/codes.py` (APPEAL_RULES,
   CORRECTED_RULES, FILING_RULES, the defaults, the legal limits, the payer-name patterns such as CHAMPVA's) so each window
   says what the source says, with its label and source, and `verified` true only where the primary source was read.
   Keep the two dates the product already has: the safe date (`appeal_deadline`, what the biller works to) and the legal
   date (`legal_deadline`, the last day the payer accepts; an item expires only after it). A window the product had too
   long gets the true, earlier date in both. A window it had too short keeps a safe date no later than the true one and
   gets the true legal date, so the item is not expired early.
2. Where one payer group mixes plans with different windows (Anthem Medicaid in Kentucky and in Indiana; the Anthem
   Federal Employee Program inside Anthem commercial; Oxford under a Medicare filing indicator), tell them apart by what
   the remittance or the 837 carries (the plan name, the payer id, the claim filing indicator, the member's or the
   practice location's state), with optional keyword arguments so the 44 call sites keep working. Never guess: when the
   item does not say which plan, use the shorter window and say so on the item.
3. Kentucky Medicaid fee-for-service has no appeal for an ordinary claim denial (907 KAR 1:671): its denials are fixed
   by a corrected or new claim within 12 months of the date of service (907 KAR 1:673), and a crossover from Medicare
   within 6 months of Medicare's adjudication (907 KAR 3:005). Make the item's window, next step and wording say so.
4. The Kentucky Medicaid managed care plans' 120 days from confirmed receipt of the denial (KRS 205.534(1)(d)1, since
   2026-04-14): count from the remittance plus the receipt days the product uses elsewhere, and apply it by service or
   denial date as the statute does (say which you chose, with the statute's words).
5. Docs: each new or changed source in `docs/RESEARCH_SUMMARY.md`; tests/test_docs.py green.
6. Tests: a failing test first for every changed window (invented data, both store kinds where an item is dated), the
   Indiana Medicaid tests of answers-8 ported, each fix broken and restored.
7. Measure on a private world copy (the integration tip against this branch): items that move between expired, Work now
   and waiting, and the deadlines that move earlier or later, with dollars, by payer group. Hand-trace at least 20 moved
   items from the raw rows (all if fewer). An item that becomes expired must say why and from what date.

Work in `remitwatch/codes.py`, `docs/RESEARCH_SUMMARY.md`, the tests, and only the call sites that must pass the new
plan or state arguments (other units edit detect.py, states.py and ema_checks.py now: keep your edits there small).
