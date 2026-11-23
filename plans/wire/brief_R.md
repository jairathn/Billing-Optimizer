# Unit R brief: Results and ROI (what RemitWatch actually brings back, with full transparency), and daily spot checks

S = /tmp/claude-0/-home-user-Billing-Optimizer/96e81489-2ad2-5c70-991d-e00db2e22749/scratchpad. Read ${S}/wire/PLAN.md first
(the owner's non-negotiables) and every ${S}/wire/report_*.md and needs_*.md written before you.

## The owner's words (2026-11-22)
"Our moat will be the tracing logic of how to work claims reliably, and how to work through these messy pools of data. ... If
people hand test and realize a claim you thought was unworked was worked and resolved, or a claim you stated was resolved was in
fact not, and left money on the table, RemitWatch will never be used again. Make sure you're also building a dashboard that keeps
track of the numbers. The money that RemitWatch is actually bringing back. People need a way to measure ROI, and they should have
full transparency into the work being done. Claims worked, money recouped, faxes sent, % of open claims closed, % unable to be
worked, % worked but without any movement on money, hours saved, denials reduced, etc. We need to see ROI to actually use this
product."

## The owner's decision on the headline (2026-11-22, supersedes the narrower attribution below where they conflict)
"Everything that RemitWatch touches should count as money that RemitWatch helped return. You can qualify it with 'this may have
come back anyway' or something but the top line number of money returned should look high without being dishonest."
- TOP LINE: "Money returned on claims RemitWatch flagged or worked" = every dollar paid on an item's line (or claim, for claim-level
  items) AFTER RemitWatch first flagged that item (its first-raised date), plus money found by RemitWatch. Never money paid before
  the item was first flagged (that money was not returned by RemitWatch and counting it would be indefensible). Never double
  counted across items (one payment counts once, to the earliest-raised item on its line).
- Beneath it, the split, strongest first: (a) after RemitWatch work, (b) found by RemitWatch, (c) flagged, no recorded work.
- The qualifier, measured, next to the top line: "About $Y of this may have come back anyway: similar items with no work were paid
  at Z% (n items)." (Z from the lift comparison below; Y = Z x the flagged dollars of bucket (c), capped at bucket (c).)
- ROI headline = top line / monthly cost; the conservative ROI (lift-adjusted) shown beneath it, labelled.

## Honesty rules (the dashboard is worthless if it flatters)
- The split below is shown under the top line; bucket (c) is labelled as possibly coming back anyway. Every recovered dollar is attributed by rule, with the rule shown:
  (a) after RemitWatch work: a payment on the item's own line (or claim, for claim-level items) dated after a person's work on that
  item in RemitWatch (fax or appeal sent through RemitWatch, corrected claim marked sent, call or portal check logged, payment
  marked posted, patient or secondary billed) and inside that payer's response window (p90 from the archive, states.response_days)
  after the work; (b) found by RemitWatch: money RemitWatch surfaced that the practice's own systems missed (an 835 payment EMA had
  not posted, then posted after the item was raised; a secondary not billed, then billed and paid; a takeback EMA had not booked);
  (c) flagged, no recorded work: money paid on a flagged item with no work in RemitWatch: in the top line, labelled "may have come back anyway" with the measured rate.
- Lift: the share of worked items that recovered money versus similar items that were not worked (same pattern: payer group x code
  x CARC, same age band), with counts; the incremental dollars = worked items' recovery minus what the unworked rate predicts. Show
  the counts behind every rate; say "not enough cases yet" under 20.
- Every number on the dashboard opens the exact list of items behind it (counts equal lists), each with its story and verdict
  (unit D) and its decision trail (unit H); every list exports to CSV (no patient details unless the names switch is on).
- Assumptions are visible and editable by the owner (minutes per task, monthly costs), never hidden constants.

## Files
New remitwatch/results.py (attribution, metrics, weekly snapshots, spot-check sampling), new remitwatch/api_results.py (an
APIRouter: one include line in api.py; tell unit C1), new remitwatch/static/results.js and the Results tab's markup (one container
and a nav entry in index.html: ask unit C1 through ${S}/wire/needs_R.md, or add exactly those lines if C1 has finished), new
tests/test_results.py. Reads the store, states.py, story.py, the finding events, fax records, the ledger summaries; writes only its
own tables (results_snapshots, spot_checks) and meta keys under results:.

## Build
1. Money: recovered this week, month and to date, split (a) after RemitWatch work, (b) found by RemitWatch, (c) arrived on its own;
   by payer group, signal, action type, person; the lift (above); median days from work to money; money lost to deadlines; money at
   risk in the next 14 days; money given up without a decision (unit G's figure); credits refunded (compliance).
2. Work done: items worked by action type (fax/appeal sent, corrected claim, call or portal check, payment posted, patient or
   secondary billed, let go with a reason, accepted by policy); faxes sent with pages and their cost; by person and week.
3. Outcomes of the open book: % of open claims closed (paid, patient, secondary, owner policy, expired), % unable to be worked
   (cant_check, with why: missing 835, separate payee), % worked and waiting with no money movement yet (and how long), % worked and
   denied again, win rate by action type and by pattern, the number of items in each work_state, week by week.
4. Hours saved (an estimate, labelled as one): items RemitWatch proved settled or closed by itself x minutes to investigate one by
   hand (default 12), packets built x minutes saved per packet (default 20), faxes sent and followed up by RemitWatch x minutes saved
   per fax (default 10), items folded as duplicates x minutes (default 5); the owner edits each default on the page; the history
   keeps which values were in force.
5. Denials reduced: denial rate (denied lines / answered lines and denied dollars / billed dollars) by payer group, code family and
   rendering clinician, month by month, with a marker where a fix was recorded (a credentialing, authorization or coding fix noted
   on a pattern item) and the before/after rates; the top rising and falling denial reasons.
6. Cost and ROI: monthly cost inputs (hosting, fax per page, AI calls counted by the app where it uses them, other), ROI = money
   recovered after RemitWatch work + money found, over cost, by month; with the honest version (lift-adjusted) beside it.
7. Weekly snapshots (results_snapshots) so trends never change after the fact; a weekly email summary (if email is configured).
8. Spot checks (the hand test, built in): each day, RemitWatch picks 5 open or recently closed items at random (stratified: work_now,
   waiting, done, closed automatically), asks a signed-in person to check each against EMA and the payer, with one click (agree, or
   disagree with the reason: "already worked", "already paid", "not paid", "wrong amount", "wrong deadline", "other"); results kept
   with who and when; the agreement rate by state and signal goes on the dashboard, week by week; every disagreement is recorded with
   the item's decision trail (unit H's diagnostic bundle includes them) so the rule behind it gets fixed.
9. The page (Results tab): the money first (recovered split three ways, lift, ROI), then work done, outcomes, hours saved, denials,
   spot-check accuracy; plain English, no em dashes, every number clickable; phone width works.
10. Baseline: on first run after the release, compute and freeze the "before" (the worklist's state on that day: open items by
   state, the answer-key style counts the self-audit computes) so later weeks are read against it.

## Tests and acceptance
Tests with invented data for every attribution rule (a payment after a fax inside the window counts as after work; after the window
or with no work counts as on its own; found-by money; a takeback reverses an attribution), the lift with small counts, hours saved
with edited minutes, denial rates and fix markers, ROI with costs, snapshots immutability, spot-check sampling and recording, counts
equal lists. Both modes. On the integrated real world: every dashboard number equals a direct SQL recomputation done independently
(write it, show both); the attribution of 30 sampled recovered dollars re-derived by hand from raw rows. Write ${S}/wire/report_R.md
and needs_R.md.

## ADDED 2026-11-22 23:30 (read before you start)
- Read ${S}/wire/brief_C.md's ADDED section: C1 adds the Results tab to the shell (TABS, router to resultsTab(v), the script tag
  /static/results.js). You write remitwatch/results.py, remitwatch/api_results.py (an APIRouter; ask C1 in needs_R.md for its one
  include line, or add exactly that one line yourself if C1 has finished), static/results.js (defines async function
  resultsTab(v)) and tests/test_results.py. No line of index.html.
- Also build GET /api/results/scoreboard for Today (unit C2 shows it): {recovered_cents, lost_cents, at_risk_cents,
  given_up_cents, as_of, weeks: [{week, recovered_cents, lost_cents, at_risk_cents, given_up_cents}]}, each figure opening its list.
- Interfaces fixed for later units (${S}/wire/brief_3a.md): expected_value_cents / recovery_rate / recovery_basis on findings
  (from G through the hook-in agent; use them when present), the decision log route GET /api/findings/{id}/decisions, and unit
  G's "money given up without a decision" (until G is hooked in, compute your own from the store with the same definition:
  written off on 2025+ service with no person's decision and no owner-approved policy; say in the page which one is shown).
- CPU rule (PLAN.md): your own and related tests while working; the full suite ONCE at the end in venv2_compat312 and
  venv_sqlite340.
