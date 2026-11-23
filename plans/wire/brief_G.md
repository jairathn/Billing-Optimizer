# Unit G brief: the software runs itself (self-regulation, self-checks, self-audit, pattern watch)

S = /tmp/claude-0/-home-user-Billing-Optimizer/96e81489-2ad2-5c70-991d-e00db2e22749/scratchpad. Read ${S}/wire/PLAN.md first,
then every ${S}/wire/report_*.md and needs_*.md written before you (phases 1-3 are finished when G starts).

## The owner's words (2026-11-22)
"By the end of this, it needs to run independently, and do all these clearing tasks independently. You can't come and save the
software when its memory use gets high. It needs to be self regulating, and self-checking, and maintain itself, keeping itself in
check, and re-evaluating its performance regularly to make sure things are still working as they should without issue. ... you
should be finding and understanding rule based patterns so that things don't get missed, and AI is only used for the extreme edge
cases."

## Rules
- Everything here is plain rules and arithmetic. No AI anywhere in G. (AI stays only where it is today and always waits for a
  person: reviewing a visit note for an appeal (notes.py), reading a payer's incoming fax (fax.py), and a documentation
  template written on request (lessons.py).)
- Runs on both hosts inside the existing daily cycle and the existing schedulers (Vercel cron, the server's timer). No new
  service, no person needed. Every action it takes on its own is recorded (what, why, the numbers) and visible on the Data tab.
- It never deletes source data and never hides an item to make a number look better.

## Files
New remitwatch/selfcheck.py and tests/test_selfcheck.py; small, named hooks in pipeline.py, cloud.py, detect.py (the save step),
alerts.py, insights.py (data health), api.py and the Data tab page (one panel). Unit E's archive.py is used, not changed (ask
through ${S}/wire/needs_G.md).

## Build
1. Self-regulation of memory and time, every run, both hosts. Measure peak memory, wall time and /tmp (serverless) per step (the
   cloud status already records memory per step: extend, do not duplicate). Budgets: serverless 1.6 GB of the 2 GB limit, the run
   inside its time budget; server a configured budget. When a run passes 80% of a budget, the NEXT run acts without a person, in
   this order, each logged: (a) spread work across runs (one report file per run, more=True, which A1 built), (b) run the archive
   at the tighter end of its allowed horizon (unit E's bounds; never below the windows any open item needs), (c) leave the
   optional steps (provider tables, the dashboard extras) for the next run and say so. When nothing more can be shed, raise one
   alert ("RemitWatch is near its memory limit: <numbers>; nothing was lost; this is what it did") instead of failing silently.
2. Invariants on every run (the self-check). A battery of rule-based checks over the run's output, each with a name, a plain
   sentence, the count of offending items and up to 20 example finding ids: every open item has exactly one work_state, a due or
   follow-up date where its state needs one, a deadline with its basis; nothing is both open and closed; every automatic closure
   carries its proof rows; no facility twin closed a professional line; the item's balance line reconciles to the cent or carries
   its gap; counts on Today and the worklist equal the lists they open; no WORK NOW item has a payment after its denial that
   covers it (the precision guard); no item is past its legal limit while still WORK NOW; ledger and store totals agree (Production
   money per box = what the ledger holds); the dashboard copy's figures equal the store's (Vercel); the EMA reports' freshness;
   the 835 feed's freshness. Severity: a critical failure (money or state invariants) puts the run in safe mode: the store keeps
   the run's data, but the worklist people see stays on the last good run's items, automatic closures of this run are held, and an
   alert says why and what to check; a warning goes to Data health. Results kept per run (history), so a check that starts failing
   is visible as a change.
3. The weekly self-audit (re-evaluating its own performance). Once a week (and on demand from the Data tab), independent of the
   detection code paths (a separate, simpler rule set that reads the raw tables, the way the answer key in ${S}/truth/ was built:
   read ${S}/truth/g1_final/README.md and g2_final/README.md and reuse their rules, not their code): (a) coverage: every 2025+ line
   with recoverable dollars inside a window (denial, open balance, unanswered, short pay, unposted 835 payment, secondary not
   billed, auto write-off still appealable) must have an item in WORK NOW or WAITING or a recorded reason (accepted pattern, person
   let go); every line that does not is listed as a coverage gap with dollars; (b) precision: every WORK NOW item with evidence it
   is settled (paid after or before, patient or secondary settled, already worked and waiting, duplicate, twin, expired) is listed
   as possible wasted work; (c) the confusion matrix by count and dollars, kept week by week; (d) alerts when coverage gaps or
   wasted work rise above a small threshold (configurable; default: any new gap over $500 or more than 10 new wasted items).
4. Its own track record (rule-based calibration, no AI). From what happened afterwards: automatic closures later reopened (a
   payment taken back) as a closure error rate by rule; waiting items the payer then denied vs paid, and the time it took, which
   updates each payer group's p90 response time (states.response_days) every run from the archive; items a person let go that were
   later paid; deadlines missed (an item expired while WORK NOW). Shown on Progress and the Data tab; an alert when a rate moves
   past a threshold.
5. Pattern watch (rule-based). Each run compares the last 30 days with the prior 90 by payer group x code x modifier x CARC/RARC
   and by rendering clinician x payer: a new or rising denial reason (the existing RAMP/RISING alerts: extend, do not duplicate), a
   new clinician whose claims a payer denies for credentialing or enrollment (CO-147/M16, enrollment CARCs), a clinician paid below
   the group's own rate for the same code and payer, a payer that reprocessed and paid a batch of identical denials (so the rest of
   the batch becomes one pattern item: unit B's rule, G only raises the alert), a payer whose ERAs stopped arriving while EMA still
   posts its checks (REMITS_MISSING), a check EMA posted with no 835 where the payer usually sends one. Each alert names the
   pattern, counts, dollars and the items, and links to them.
6. Housekeeping on its own: unit A1/A2/E's clean-ups (stale upload parts after 24 h, ledger work files, the intake folder's done
   items after 30 days, archive compaction) are called from the cycle with their results recorded; nothing waits for a person.
7. The Data tab gets one "Running on its own" panel: last run (when, how long, peak memory vs budget, what it shed or archived), the
   invariant results with history, the last self-audit (coverage gaps, wasted work, the matrix), the track record, the pattern
   alerts. Plain English, no em dashes.

## Silent loss is the failure to prevent (the owner, 2026-11-22: "If the software is silently losing money, then it is useless")
8. Money conservation, every run, both hosts: per service month and payer group, charges = insurer paid + patient paid +
   contractual (CO-45 and the like, at the payer's own consistent rate) + written off (by reason: EMA posting rule, person, policy,
   timely filing, small balance) + open (insurance, patient) +/- credits. Any month or payer whose pieces do not add up to the
   charge within $1 is a critical invariant; any line whose balance line does not reconcile counts in a gap total with dollars.
9. Money given up without a decision: every week, the dollars written off on 2025+ service that no person decided and no
   owner-approved policy covers (EMA's posting rules above all), by payer group, reason and month, with the part still inside its
   window (= recoverable) and the part already lost. On Today, Progress and the weekly email; an alert when the week's figure rises.
10. Blindness in dollars: when the 835 feed or the EMA reports are stale (thresholds as in PLAN.md and needs files), every page
   shows one banner: what the app cannot see, since when, and the dollars of claims it cannot check (charges with no payer answer
   on file after the last 835 date). It never says "all clear" while blind.
11. The scoreboard (unit R's Results tab owns the page and the attribution rules; G supplies the self-audit figures and the
   money given up without a decision): recovered (money that arrived after an item was raised and worked), lost (items that expired while WORK
   NOW), at risk (WORK NOW items whose deadline is within 14 days), given up without a decision (item 9), week by week, with the
   track record of item 4.
12. Recovery rates by pattern for ranking: from the archive, per payer group x code x CARC (and modifier where the weekly exports
   give it), the share of denied dollars later paid and the median days, with the count it rests on; served to story.next_step
   (unit D) as the expected value's basis and to the worklist's sort (unit C). Thin patterns (fewer than 10 cases) fall back to
   the payer group x CARC rate, then the CARC rate, and say so.

## Tests and acceptance
Tests with invented data for every invariant (a store that breaks it, caught; a clean store, passes), safe mode (a critical failure
holds closures and keeps the last good worklist; the next good run releases it), each self-regulation step (a fake memory reading
over 80% makes the next run spread and archive), the weekly audit's coverage and precision lists, the track record, each pattern.
Both modes. Then on the integrated real world (the verification phase's world, both modes): all invariants pass (or each failure is
a real bug, fixed by its owner); the weekly audit's coverage and precision lists match the independent answer key's confusion
matrix within its explained differences; a forced high-memory reading makes the next run shed work and stay under budget.
Write ${S}/wire/report_G.md and needs_G.md.
