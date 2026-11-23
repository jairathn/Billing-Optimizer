# needs_C2.md: what unit C2 (the EMA pages) needs from other units

Status: FINAL after the C2 fixer (2026-11-23). C2 owns remitwatch/api_ema.py, remitwatch/static/today.js,
remitwatch/static/data.js, remitwatch/static/policy.js and tests/test_ema_pages2.py. The fixer wrote no line of
index.html, api.py, desk.js, features.js, ema_checks.py, detect.py or any other unit's file. No identifiers below:
counts, dollars and RemitWatch finding ids only.

## 0. The two reviews of C2: status of every finding (fixer, 2026-11-23)

Each fix has a failing test first in tests/test_ema_pages2.py (25 new tests, 64 in the file: 22 failed together on the
code before the fixes, the 3 written later each failed before its own fix; all pass after) and a deliberate break that
its tests catch (39 breaks: wire/C2/fix/break_restore.json, break_restore_late*.json; every file restored and checked by
SHA-256).

| finding | status |
|---|---|
| R1 high: codes a payer almost never pays ('group\|code\|\|payer_policy') missing from the owner's page, 404 on Pursue | FIXED. policy_table reads meta ema:payer_policy (every pattern; the stored summary keeps 500 rows) and lists each one as kind `payer_policy`, decision `decide` until a person decides (PLAN decision 14, whatever word the run's default uses), its denials, dollars (an estimate) and the payer's paid share; `totals.decide` says how many wait and their dollars, never in the write-off totals. Today: "N write-off patterns worth $X (est.) wait for the owner's decision" (links to #policy). The policy route accepts the key; its items route answers with a note (the denials are listed in the Worklist once Pursue is chosen). Test: test_a_code_the_payer_never_pays_waits_for_the_owners_decision_on_the_policy_page_and_today (real Detector, Pursue, next run lists the 21 denials, both hosts) |
| R1 high: decision 3's contractual key 'group\|\|\|reason' not on the page, the drawer's Accept gets 404 | FIXED. One row per key from the live EMA_DENIAL_AS_CONTRACTUAL items (kind `contractual`, key built exactly as story.next_step and states build it), what EMA wrote off that way said apart (in no total), the part inside a window; the items route lists them; the route accepts the key. A claim-level write-off pattern with the same shape joins that row. Test: test_the_contractual_pattern_of_decision_3_is_on_the_policy_page_and_the_drawers_accept_records_it (real Detector, story's key accepted, next run marks the item accepted, both hosts) |
| R2 high + R1 medium: "N payments to post" and "N credits to refund" open pages that do not list them | FIXED on C2's pages. Each opens the Data tab on its own list (#dtPostList in "Payments the payer sent that EMA never posted", #dtRefundList in Credits), counted with the same filter Today counts with (`/api/ema/data` items.post / items.refund carry it), scrolled to; an item opened there walks the list. The A/R's own credits are labelled "on EMA's A/R". The Fees & takebacks sentence is gone. Tests: test_the_payments_to_post_and_the_credits_to_refund_open_lists_that_hold_them, ..._land_on_lists_of_exactly_those_items; browser check on both hosts |
| R2 high: "Nobody has worked these" counted Done, Expired and Accepted items | FIXED: counted and opened inside Work now (go `state=todo` + `ema=unworked`); hint "of Work now, nothing done in 21 days". Test: test_nobody_has_worked_these_counts_only_what_is_in_work_now. The stored meta ema:unworked (INT's) still counts outside Work now: see 4 |
| R1 medium: credits.csv gave initials with names off | FIXED: the file is built by api_ema from the rows ema_checks.credits counts, with the claim ID first and initials only while the names switch is on. Test: test_the_credits_file_names_no_patient_while_names_are_off (both hosts) |
| R1 medium: a held report applied once and held again had no buttons and said "the next run reads it" | FIXED on the page: every held report offers Apply and Discard unless someone chose Apply after the newest run (then "the next run reads it" and "Discard it instead"); one chosen before the newest run and still held says so and offers both again (`decision_pending` on each catalog row). SWEEP already made hold_slow_file clear the decision (needs_SWEEP 28). Test: test_a_held_report_is_decided_safely_and_offered_again_when_a_run_left_it_held |
| R2 medium: Apply was the blue button and asked nothing | FIXED: neither button is primary; Apply asks once, saying it replaces what is on file and that pulling the report again is the safe way. Same test |
| R1 medium: still appealable ignored the payer's own limit | FIXED: the policy page and the Data tab count open items inside the safe date or the payer's own limit, and say the part only inside the payer's limit apart (`legal_only`); the items list says "payer's limit <date>" for such an item. Today's auto write-off tile stays on the safe date (decision 2) and says so on hover. Test: test_still_appealable_on_the_policy_page_counts_the_payers_own_limit |
| R2 medium: Today blind and ticked "all clear" when /api/ema/today failed | FIXED: Today asks /api/ema/blind itself, says "Today's EMA counts did not come back", and never ticks step 1 until a blind answer says no feed is behind. Test: test_today_stays_blind_and_ticks_nothing_when_its_counts_do_not_come_back |
| R2 medium: nothing may block while loading | FIXED: Today draws the plan when the brief answers and fills the scoreboard, the counts and the exports' line as each answers; the Data tab draws the checklist, feeds and fixed panels at once and fills EMA's panels later. Test: test_today_and_the_data_tab_draw_at_once_and_fill_each_place_when_its_answer_comes; browser: plan within 2.5 s with each side answer held 6 s |
| R2 medium: a policy decision redrew the page and lost the owner's place | FIXED: the row and the totals line change in place; the filter, Show all and the scroll stay. Test: test_the_policy_page_keeps_the_owners_place_asks_at_most_once_and_walks_a_patterns_items |
| R1 low: the blind spot's Work now was not the tile | FIXED: the banner counts with the tile's own filter (passed in by Today); with two feeds behind each part carries its own dollars, words and basis (`parts`); the headline shows each part. Tests: test_the_blind_spot_counts_work_now_as_the_tile_beside_it_and_gives_each_part_its_dollars, test_the_blind_spot_gives_each_part_its_dollars_and_basis |
| R1 low: Today said a decided report waits for a decision | FIXED: `reports_held` counts only reports that wait for a person; `reports_pending` ("waits for the next run") the ones applied since the newest run. Test: test_a_held_report_waits_for_a_decision_only_until_someone_decides |
| R1 low: the re-send request for checks with no payer name listed 0 checks | FIXED: both sides group an empty or missing group as Unknown. Test: test_the_request_for_checks_posted_with_no_payer_name_lists_those_checks |
| R1 low: two decisions at once lost one | FIXED: the server path takes the write lock before reading the decisions (BEGIN IMMEDIATE). Test: test_two_people_deciding_two_patterns_at_once_on_a_server_keep_both_decisions. (The reviewer's own barrier test now stops at its barrier, as it must once the reads cannot interleave.) |
| R1 low: decision 5 (basis on hover, estimates labelled) | FIXED on C2's figures: the routes return `basis` (api_ema.BASIS); Today's tiles, the policy page's money cells and the Data tab's dollar lines carry it on hover; estimated dollars say "est." ("Dollars (est.)" on the policy table) |
| R2 low: two dialogs per Accept, one per Pursue | FIXED: a remembered "Your name" field; one confirm on Accept, none on Pursue |
| R2 low: "this week" on the Work now tile | FIXED: "to work, follow-ups due included" |
| R2 low: Today's tiles opened the Worklist by filing date | FIXED: no order forced; the Worklist's own (worth most first) applies. The auto write-off tile keeps its dollar order |
| R2 low: links into the Data tab landed at its top | FIXED: links name where they land (held report, payments with no remittance, the two lists); on the Data tab the blind banner's "What to do" scrolls there |
| R2 low: a pattern's item had no Next | FIXED: opening an item from a pattern's list (or from the Data tab's lists) sets the list Next and "Mark sent and open the next" walk |
| R2 low: "/" searched Find a claim on the policy page | FIXED: the pattern filter carries data-f="q" |
| R2 low: A/R rows at phone width | FIXED: cards with Open in EMA first; a row in "Denied, with an item ..." links to its item (`finding_id` on the row). Test: test_an_ar_row_in_a_bucket_with_an_item_names_the_item, ..._shows_an_ar_rows_item |
| R2 low: Today's first action off the first phone screen | FIXED: on a phone the scoreboard and the counts are one compact line each, the lines after them come below the plan, the health banner shows its title only, the blind spot keeps its headline, dollars and What to do and folds its explanation under Why, and step 1 names the three kinds worth most and how many more. Real data at 390 x 844: Start at 787 to 825 px, all on the first screen (was 1,387; 870 before the last two changes). Tests: test_today_words_dollars_and_phone, test_the_blind_spot_gives_each_part_its_dollars_and_basis (phone part), test_on_a_phone_step_one_names_the_three_biggest_kinds_and_how_many_more |
| Real-data acceptance (fixer): the Data tab's unposted line said 35 + 50 payments while its file and Today said 86 | FIXED: the run's list also holds payments a remittance reported whose money never arrived (`unfunded_n`, kind never_arrived); the line now says them too ("the payer reported but never sent ... ask the payer for it"). Real data: 35 + 50 + 1 = 86 = the file's rows = Today's count, and the three dollar figures add to the list's $28,140.44 (whole-dollar rounding only). Test: test_the_unposted_line_counts_every_row_of_its_file |
| R2 low: "835" where the site says "remittance" | FIXED: "Payments the payer sent that EMA never posted", "no remittance on file carries" (the request text to TriZetto keeps "835") |
| PLAN decision 9 (today.js) | DONE: the "worth less than $20 ... the amount filter hides them" nudge and "not worth pursuing" are gone. Test: test_no_page_tells_a_person_to_leave_an_item_for_its_size |
| needs_A2 F.5 (today.js): "never arrived" for an export held or not read yet | DONE: "the newest file is held" / "none read yet". Test: test_an_export_held_or_not_read_yet_is_not_said_never_to_have_arrived |
| review C1-2 #8 (aimed at C2): the guide's first item opened any state | DONE: "Show me the first item" opens Work now (state todo). Step 1's link left as is: see 1 |

Not done, with the reason:
- SWEEP's optional ask (needs_SWEEP 3.1, sync_pending "N files could not be read yet"): the feed panel on the Data tab is
  features.js's feedPanel (C1's file); the figure is not in /api/sync/status yet. Left for C1 / HOOK.

## 1. Unit C1 (api.py, desk.js, features.js, index.html)
- Today's step 1 ("Work what closes this week") counts from the brief (insights.work, INT's file), which counts open items of
  any state. If desk.js adds state 'todo' to a link that names a deadline but no state (review C1-2 #8), insights.work's
  due7/due30 must count state todo too, or step 1's number and its list differ. Today's other links carry their own filters.
- Optional: a "Payments to post" list on Fees & takebacks (signal REMIT_NOT_POSTED, status open/in_progress, live) would
  let a poster work them where the fees are; Today links to the Data tab's list today, which holds exactly what it counts.
- features.js xpAccept posts story.next_step's pattern: the route now accepts 'group|||reason' and 'group|code||payer_policy'
  keys. Whether Accept needs the owner's password is the orchestrator's call (review C1-1 #9); the page and the route
  follow the hide rules (any signed-in person) as brief_C says.

## 2. Unit R: the scoreboard Today reads (unchanged)
GET /api/results/scoreboard as in needs_R 5. Today shows `basis[key]` on hover and a dash for a missing figure.

## 3. Unit G / the hook-in agent
- `selfcheck_banner` is still not in /api/meta (api.meta): add `"selfcheck_banner": selfcheck.banner(conn)`. today.js reads
  `state.meta.selfcheck_banner` and gives no all clear while it is set.
- meta selfcheck:blind, when G stores it, may add `parts` ([{feed, cents, words, basis}]) so the banner gives each part
  its dollars; without it the banner shows G's `cents` with its `basis` on hover.
- PLAN decision 14 ("money given up without a decision" counts the patterns waiting for the owner): the figure is
  api_ema.decide_totals(conn) (meta ema:payer_policy patterns with no person's decision in meta ema:writeoff_policy).

## 4. INT (ema_checks.py, detect.py)
- Decision 14: ema_checks.ema_denials and detect.fold_payer_policy still write `or "accept"` as a payer_policy pattern's
  default. The page already shows such a pattern as waiting for the owner's decision (no button pressed) and Today counts
  it; the default word in meta should become 'decide' (behaviour unchanged: its denials stay out of Work now).
- ema_checks.unworked_sql (meta ema:unworked, `ema=unworked` with no state): add Work now (api._state_sql('todo')) so the
  stored count, the plain list and Today's tile (now counted inside Work now) agree.
- The policy summary keeps 500 rows (`pol[:500]`) and appends the payer_policy rows after them: the page reads them from
  meta ema:payer_policy, so nothing is lost; later_paid_share for patterns past the 500th is still missing (dash).
- ema_checks.credits_csv is no longer used by the route (it gave initials whatever the names switch says): remove it or
  keep it for the CLI only.

## 5. Units D and B: from the 12 practice-manager cases (unchanged from the builder)
C27 e956c8e0be84819fdabf1abf (paid in full across split lines, still Work now); C38 ddc891c5a519ccb76bc0ab77 and
cb60f706111f0f71edb61842 (A/R note says the denial was overturned: Waiting); C25 7bf282d220df72e751ce05f0 (call to
reprocess, no corrected claim); C59 d88ac933f373996854a685c2 (1-year reopening still open).

## 6. Unit F (docs)
- README layout: `remitwatch/api_ema.py`; `static/` line: `data.js` and `policy.js` (test_docs).
- docs/OPERATIONS.md: the policy page's three kinds of key (write-off patterns: pursue by default; codes a payer almost never
  pays and reasons EMA posts as contractual: wait for the owner's decision), Today's "patterns wait for the owner's
  decision" line, the Data tab's two lists (payments to post, credits to refund), the re-send request, the blind-spot
  banner, credits.csv's columns (claim_id first; initials only with names on).

## 7. The hook-in agent (ops.js): unchanged
Render into `<section id="ops-panels"></section>`; data.js leaves it empty.
