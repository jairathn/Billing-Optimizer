# Phase 2 briefs: unit C1 (the API and the worklist pages) and unit C2 (the EMA pages: Today, Data, policy, reconcile)

S = /tmp/claude-0/-home-user-Billing-Optimizer/96e81489-2ad2-5c70-991d-e00db2e22749/scratchpad. Read ${S}/wire/PLAN.md first.
Then read the phase-1 reports and needs files: ${S}/wire/report_A1.md, needs_A1.md, report_A2.md, needs_A2.md, report_B.md,
needs_B.md, report_B2.md, needs_B2.md, report_D.md, needs_D.md (every item aimed at api.py or a page is yours to do or to
answer). Amendments ${S}/build/amendments.md sections 8, 9, 12, 13 and needs_ema_checks.md sections 4 and 7 are the page spec.
The practice manager's view: ${S}/pm/worksheet.json (66 verified cases) and ${S}/wire/pm_product_gaps.md.

## Ease of use (the owner's rule: a newcomer can pick it up; an experienced biller is never slowed or lectured)
- One obvious primary action per item (the next step's main button), secondary actions in a menu; details folded until asked for.
- Plain words, short labels; no paragraph of instructions on a working screen; help as a hover or a one-line hint, never a wall.
- A first-visit guide of at most five short steps, dismissible, never shown again to that person (and re-openable from Help).
- Keyboard shortcuts for the worklist (next/previous item, open, mark sent, let go, search) shown in a small "?" sheet.
- The same words for the same thing everywhere (states: Work now, Waiting, Done, Accepted, Expired, Can't be checked).
- Fast: the worklist opens in under a second on the real store; long lists paginate; nothing blocks while loading.
- Phone width works (390 px) for reading and the main actions.

## Shared rules for both units
- Both hosts: every route and page works in server mode and on Vercel (serverless; dashboard copy; 4.5 MB request limit;
  routes must NOT recompute EMA figures from the dashboard copy on Vercel: read what the run stored in meta, per the scale
  review's high finding and the EMA fixer's report).
- Plain English, short sentences, no em dashes, no jargon ("false positive", "C6", "W4" never appear on a page). Numbers on a
  page equal the count of the list they open. The names switch governs patient details as today. Dates are fine behind login.
- Every button is safe: a fax only to a confirmed number with a preview first; never an appeal of a line that was paid,
  settled, credited, reprocessed or is waiting; write-offs only through the owner's policy table or a person with a reason.
- Tests with invented data (both modes); node checks (tests/calc_model_check.js, tests/lab_review_check.js) and the drawer
  evaluation (tests/review_assets/drawer_eval.js) keep passing; Playwright screenshots at 1400 px and 390 px of every new or
  changed view on a store built from fixtures (no real patient data in screenshots).

## Unit C1: api.py and the worklist pages
Files: remitwatch/api.py (except A2's include line and C2's include line), static/index.html, desk.js, features.js, faxes.js,
claim.js; tests: test_api_review.py, test_desk.py, test_claims.py, test_review7.py, new tests/test_ema_pages.py (shared with C2:
C1 owns the file; C2 adds a separate tests/test_ema_pages2.py).
Build:
1. `api.FINDING_COLS` gains the new columns (PLAN.md). Filters through `api._filters` so counts, tiles and lists agree:
   `ema=` written_off | contractual | paid_other | on_ar | ema_only | worked | unworked | auto_written_off; `state=` work_now |
   waiting | done | accepted | expired | cant_check; "Auto write-offs still appealable" (auto_written_off with
   appeal_deadline >= today), sorted by dollars then deadline.
2. The default worklist and Today show WORK NOW and due follow-ups only (13.1), sorted by expected value (dollars x the
   pattern's recovery rate, unit G/D) and then deadline; WAITING items show their follow-up date and
   why; DONE/ACCEPTED/EXPIRED/CANT_CHECK are reachable by filter and by Find a claim, never silently gone.
3. The item drawer: the outcome before EMA's claim status (claim status is context only); the story (`story.item_story`: one
   chronological table, source tags, the balance line that reconciles to the cent or shows the gap), the verdict
   (`story.item_verdict`: the sentence, decisive rows highlighted, strength), the next step (`story.next_step`: action, who,
   deadline and basis, expected value, instructions) with its buttons wired: build_packet, attach_note, find_note, upload_note,
   fax_payer (confirmed number, preview), copy_instructions, mark_sent, log_outcome, mark_posted, mark_billed, accept_pattern
   (owner-gated), let_go (note required), reopen, request_era (copyable request text: payer, check date, amount), open_in_ema
   (the EMA claim URL built from a host name in an environment variable; never a host name in the repo). Chip "Auto write-off"
   and the amendment-8 wording for auto_written_off, written_off_while_pending and lost_to_timely_filing with the evidence line.
   Every automatic closure keeps its verdict and proof rows, with Reopen.
4. `GET /api/findings/{id}` and `/api/claims/view` carry the `ema` block (`ema_checks.claim_block`; ledger read-only on a
   server, None on Vercel with the "kept on the server" note) plus story, verdict and next step.
5. Deep link `#item=<finding id>` opens the drawer for that item (so a finding id in a message is one click away).
6. `POST /api/appeals/send` applies W5 (fax_vouch) and the bulk "appeal everything appealable" excludes paid, settled,
   credited, reprocessed and waiting items (unit B's rule), with a count of what was left out and why.
7. Policy routes (`GET /api/ema/policy`, `POST /api/ema/policy`) if unit C2 has not put them in api_ema.py: agree with C2.

## Unit C2: the EMA pages
Files: new remitwatch/api_ema.py (an APIRouter; one include line in api.py, tell C1), static/today.js and the Today view's markup,
the Data tab's markup and script, new static/policy.js (and its view), static/upload.html only where it shows catalog results
(A2 owns the upload mechanics: agree through needs files); tests: new tests/test_ema_pages2.py.
Build:
1. `GET /api/ema/reconcile` (meta `ema:reconcile` written by the run), the CSVs (`checks.csv`, `unposted.csv`, `credits.csv`;
   no patient data; no-store), `GET /api/ema/ar?bucket=&page=` (never writes meta).
2. Today: the money scoreboard first (recovered, lost to deadlines, at risk in 14 days, given up without a decision; unit G
   computes it; until G lands show what the run stores) and the blindness banner when a feed is stale (in dollars); then counts
   by state (work now, follow-ups due), auto write-offs still appealable, EMA reports freshness (A/R 8 days,
   Production 14, weekly exports 8; EMA_STALE), REMITS_MISSING, the unworked number; each count opens its list.
3. Data tab: the EMA reports catalog (each file: recognised as, scope, rows, boxes replaced, holds and decisions with who and
   when, origin file/upload), freshness, data health items from `ema_checks.data_health`, credits (C8) with the Medicare
   over-60-days line as a compliance item, the C1 missing-remittance summary per payer and month with the re-send request
   facts, the A/R accounted-for buckets (W4) adding up to the exposure, the hot-store size and memory budget lines (unit E adds
   them later: leave the slot).
4. The owner's policy table page: patterns (payer group x code x modifier x CARC), items, dollars, still appealable, auto,
   later-paid share (from the archive), decision (pursue | accept) with who and when; any signed-in user may set it unless the
   existing hide rules require the owner's password (follow the existing rule); accepted patterns leave the work queue and
   stay findable.
5. The weekly email additions (needs_ema_checks.md section 5) if they live in a page-side template (else they are unit B's).

## Acceptance (both units, real data, aggregates and screenshots with names off)
Run the app in server mode on the integrated world (the world builder output after A1 and B, with the live store's
statuses: use the integration world unit A1 or the verification phase leaves at ${S}/wire/world_integrated/ if present, else
build one: copy ${S}/live_state_1122/store.sqlite, import it the way cli import-cloud does, ingest the four reports, run one
cycle) and in serverless mode on its compact image; open every view; for 12 of the 66 practice-manager cases (one per
stratum) open the item by deep link and compare the drawer's verdict and next step with the verified truth; every count on Today
and the Data tab equals its list. Report mismatches; fix yours; hand the rest to the owners through needs files.

## ADDED 2026-11-22 23:30 (read before you start; overrides the file lists above where they differ)
State of the tree: phase 1 units are finishing (A1 intake, B detection wiring with reviews, B2 archive-rules module, D states and
story). Read ${S}/wire/report_A2.md, report_B.md, needs_A1.md, needs_A2.md, needs_B.md, needs_B2.md, needs_D.md and
${S}/wire/brief_3a.md (interfaces fixed for units E, G, H and the hook-in agent). INT (${S}/wire/brief_INT.md) is about to wire
the archive rules and finish the states; build against the interfaces, not today's exact numbers.

The page shell (index.html) is C1's file. C1 does these shell changes FIRST (in the first half hour, so C2 and R can test):
- TABS: add ['results','Results'] right after ['wins','Bigger wins'] (and 'results' in REF_TABS); the router sends 'results' to
  resultsTab(v) (unit R's static/results.js), 'data' to dataTab(v) when it is defined (unit C2's new static/data.js; the old
  data(v) in index.html stays as the fallback until C2's is complete, then C1 deletes it), and a routable page 'policy' (in the
  CLASSIC list, not the main nav) to policyTab(v) (C2's static/policy.js).
- <script> tags for /static/results.js, /static/data.js, /static/policy.js after today.js. (The hook-in agent adds ops.js later.)
- C2 writes no line of index.html; R writes no line of index.html. Ask C1 through needs_C2.md / needs_R.md.

C1 also owns, in this push:
1. needs_B.md section 1 in full (the 19 columns in FINDING_COLS; "Payer's limit" from legal_deadline beside the due date; the
   bulk rule's exact SQL; NEVER_HIDDEN in desk.js; work types bill/post/check; category refund kept out of money totals; filters
   ema_outcome, work_state, source; the held-report decision route incl. LedgerHandle(state).clear_attempt on Vercel).
2. needs_B2.md section 4: hide rules at payer x signal x code x modifier x reason grain, in actions.py (HIDE_DDL, _hide_matching
   with archive_rules.hide_sql, apply_hide_rule journaling the new fields, apply_hide_rules via read_hide_rules, journal replay)
   AND api.py /api/hide in one change; the existing hide rules (payer x signal) keep working unchanged (migration test).
3. The bulk-appeal switch now on main (REMITWATCH_BULK_APPEAL, default off; held code bulk_paused; appealable returns
   {paused: text, items: []}): keep it. When on, the list is B's exact rule AND story.next_step(...)['appeal_ok'], with a line
   "Left out: N items (paid M, waiting K, credit C, reprocessed R)" that opens the left-out list. Tests cover both switch states.
   Do not change the default: the orchestrator decides it after verification.
4. Drawer: story.explain(conn, finding_id, ledger=...) (D's one call) for the story table, verdict and next step; a "Why did this
   change?" section from GET /api/findings/{id}/decisions (built later by the hook-in agent; hide the section on 404); deep link
   #item=<finding id>; every next_step button wired (PLAN.md ids).
5. Worklist and Today sort: ORDER BY COALESCE(expected_value_cents, shortfall_cents) DESC, then appeal_deadline; the expected
   value shows its basis on hover (recovery_basis). The columns appear after the hook-in agent; until then the COALESCE falls back.
6. needs_A2.md items aimed at api.py or the pages; the page and API product gaps in ${S}/wire/pm_product_gaps.md.

C2 also owns, in this push:
1. New static/data.js with dataTab(v) (the Data tab moves out of index.html): keep everything the old data(v) shows, then add the
   EMA catalog, freshness, data health (ema_checks.data_health), credits, C1 missing remittances, W4 accounted-for buckets,
   meta ema:wiring errors, and an empty <section id="ops-panels"></section> for the hook-in agent.
2. needs_B.md 1.6 (charge_less_paid upper bound labelled or kept out of totals), 1.7 (ema:policy_summary and the decision route
   calling ema_checks.set_policy), 1.9 (held EMA report decisions shown with who and when).
3. Today: the scoreboard row reads GET /api/results/scoreboard (unit R builds it: {recovered_cents, lost_cents, at_risk_cents,
   given_up_cents, as_of, weeks: [...]}); show a dash for any figure the route does not give yet. The blindness banner in dollars.
