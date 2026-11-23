# needs_C1.md: what unit C1 (api.py and the worklist pages) needs from other units

Status: FINAL after the fix round (section F, newest first). C1 owns remitwatch/api.py (the include lines of A2, C2 and R are plain lines now; the hook agent adds its
own), static/index.html, desk.js, features.js, faxes.js, claim.js, remitwatch/actions.py (the hide grain and the
`ema_policy` journal kind only), tests/test_ema_pages.py (new) and test_api_review.py, test_desk.py, test_claims.py,
test_review7.py. No patient, claim or check identifiers below; items are named by RemitWatch finding id only.

## F. Fix round: reviews C1-1 and C1-2 (26 findings), Nov 23

Every finding was checked against the tree first (the reviewers' 25 failing tests failed for the reason given). Each fix
has a failing test with invented data (tests/test_ema_pages.py, tests/test_api_review.py), broken on purpose and restored
byte for byte: 47 mutations, 47 caught; two (the tab badge's state and the batch that stopped on a stale hold) were missed at first and their tests were strengthened until they failed (S/wire/C1/fix/break_restore_fix.jsonl). The reviewers' own tests now pass, 24 of
25 for review 1 (the last is unit D's story.py, F.2 item 2) and 4 of 4 for review 2; the reviewer's Playwright page checks
pass 12 of 12 on both hosts.

### F.1 Status of every finding
| finding | sev | status |
|---|---|---|
| R1-1 / R2-1: Sent, Paid, Let go or Lost left the item on Work now until new data came | high | fixed: a status a person set after the newest run started decides the state at once, in every list, tile, count, CSV and the panel (api `_state_expr`, `_state_cols`, `state_by_status`), on both hosts; the next run's own state takes over again |
| R1-2: "Send the appeal again" faxed a line found paid, credited or reprocessed since | high | fixed: the send-again path runs the same holds (paid, credit, reprocessed, a closed state); its own failed fax's "waiting" does not stop it |
| R1-3: inside the payer's own limit but past the safe date, held as expired from the panel | high | fixed: one item goes until the later of the two dates (orchestrator decision 2); a bulk send keeps the safe date; the message names the date that closed |
| R1-4: bulk "Fax them anyway" switched W5 off, EMA's own sign of a payment included | high | fixed both sides: in bulk W5 is asked whatever the answer, paid_after and no_balance are held (`code` and `vouch` say which); the page offers "Fax these N anyway" only for the items EMA could not vouch for |
| R1-5: removing a hide rule left items let go when another rule names a code or reason | high | fixed in actions.py (the "still covered" test is NULL-safe) |
| R1-6: settled in EMA, or likely paid in the archive, was faxed from the panel | medium | fixed: held as `check_first` until the person says they checked ("Checked: still unpaid, fax it") |
| R1-7 / R2-11: Show again could not remove a rule by code, modifier or reason | medium / low | fixed: the page posts the rule's grain; a page loaded before sends payer and reason only and the one rule in force for them goes (two: 409, never a guess); the hide prompt shows what the archive saw paid later |
| R1-8 / R2-6: Let go and Lost needed no reason except for six kinds | medium | fixed: every Let go and Lost, one item or many, needs a reason (API 400; the page asks in the panel, the status bar, the l key and the bulk bar) |
| R1-9 / R2-4: held bulk sends counted but never listed | medium | fixed: every held item is listed with its reason, codes the page has no group for included |
| R1-10: "Accept this pattern" not owner-gated | medium | NOT fixed here: the route is POST /api/ema/policy in C2's api_ema.py (F.2 item 1). The page already sends the owner's key when the server asks for it (features.js `xpAccept` through `faxOwnerPost`) |
| R1-11: left-out list missed undated or stateless items; settled lines under "other" | low | fixed (NULL-safe rule; reasons settled, likely_paid, no_date, no_state) |
| R1-12: Show again was a 500 on a copy still at the two-part hide table | low | fixed |
| R1-13: Open in EMA never offered | low | page side fixed (built from EMA's address and the item's EMA claim number, 6 to 12 digits); the next step's own `claim_url` is unit D's (F.2 item 2) |
| R2-2: after building the packet, the fax buttons still said "Build the packet first" | high | fixed: the packet joins the panel's answers and the box is drawn again (`xpPacketBuilt`, `xpRedrawBox`) |
| R2-3: bulk buttons shown while bulk appeals are off | medium | fixed: shown only when /api/meta says `bulk_appeal` |
| R2-5: two primary buttons and many ways to start one appeal | medium | fixed: one main button; the status bar is a plain line; the letter row and the fax box's button step aside while the box offers the appeal; the builder fold starts closed |
| R2-7: first rows waited for the header's calls; the brief was fetched twice | medium | fixed: first rows 613 ms (server) and 510 ms (Vercel copy) with the header calls each held 2 s; one brief per load |
| R2-8: about 200 words above the main button, internal codes, repeated row reference | medium | fixed: the bases are on hover, the steps folded, the verdict's own "(row N)" dropped where the Rows link says it, no signal ids |
| R2-9: links naming a window but no state opened lists of any state | medium | fixed: a link with the to-do statuses closing within 7 or 30 days (Today's step 1, the red number on a tab) opens Work now and the follow-ups due, and those numbers are counted that way (/api/brief badges and step 1, chips included). Every other older link keeps every state, as its number was counted: on real data each Today number equals the list its button opens (F.3) |
| R2-10: Escape, l and s | low | fixed: Escape closes the window on top, then the panel; l and s fall back to the status bar, else say why nothing happened. s still asks for the date sent (kept as the confirmation) |
| R2-12: wording | low | fixed (Worklist intro, the stale feed said once, a missing item in plain words) |
| R2-13: decide items asked "what did the payer do" | low | fixed ("Decide what to do" with the three choices). The other half (an owner's rate question shown as a biller's task on its row) is the work type unit B gives SECOND_RATE (F.2 item 3) |

### F.2 Requests from the fix round
1. Unit C2 (api_ema.py), or the orchestrator: gate POST /api/ema/policy behind the owner's password as the fax numbers
   are (R1-10). Owner policy is how money is written off without a person per item; with unit D's `next_step.pattern` it is
   one click from any signed-in biller. The page needs no change: it already answers the server's owner prompt.
2. Unit D (story.py): (a) fill `next_step.claim_url` and add `open_in_ema` to the buttons when REMITWATCH_EMA_CLAIM_URL is
   set and the item has its EMA claim number (review 1's test_r1_open_in_ema still fails on that alone; the page builds the
   link itself meanwhile). (b) The panel's verdict for an item whose state a person's newer status decides
   (`state_by_status`) is the page's own line ("Let go by a person on <date>"); the story still tells the run's verdict.
   A verdict that reads the person's status since the run would keep CSV, email and panel in one voice.
3. Unit B (insights.py, actions.WORK_TYPES): an owner's rate question (SECOND_RATE, route `rate`/owner) is labelled as a
   biller's kind of work on its row; give it the owner's work type or label (R2-13, second half).
4. Unit B (insights.brief) and unit C2 (today.js): Today's other steps (Work what EMA wrote off, Chase, Clear the biggest
   batch, Decide, Older) are counted by status and window, and their buttons now open the same lists in every state, so
   number and list agree (real data: 1202, 109, 279). On real data they hold items that do not need work now (35 of 1202,
   10 of 109, 60 of 279). Moving them to Work now needs both sides at once: insights counts with the state (as
   api.`_todo_soon` does for step 1) and today.js passes `state: 'todo'` in each `go`.
5. Done by its owner: tests/test_cloud.py's bulk Let go sends a reason (`notes`) since 01:29; nothing left to do.
6. Fax unit or unit D (tests/test_fax.py::test_select_all_then_appeal, test_prepare_send_and_fax_cycle_commands): they fail
   since states.py stores ROUTE_BLOCKS: the medical-necessity denial in that test world now carries appeal_block
   `check_first` (its route is `decide`), so the bulk list leaves it out (checked on the test's own world: status open, work
   now, appeal_block check_first, in its window). Not a C1 change: either the fixture needs an item whose route is an
   appeal, or unit D decides that denial's route is an appeal.
7. Orchestrator / unit D: the one-item send holds paid, credit, reprocessed, waiting (except a failed fax's own), settled,
   likely paid and the closed states. It does not hold unit D's route blocks (check_first for route decide, corrected,
   call, cob, eob_first, bill, post, owner, fees): the panel never offers a fax for them (the fax box hides with any block,
   and the next step offers none), but a direct API call would send one. Decide whether the server should hold those too
   (with a person's override, as "checked" is for check_first).

### F.3 Acceptance the fixes touch, on a real-data world (wire/C1/fixworld, deleted after; aggregates only)
- World: 17,160 findings after detection (170 s, peak 891 MB): work now 4,766, waiting 105, done 6,414, expired 5,867.
- Both hosts gave identical figures. Work now 4,016 items; tile, counts route and list equal; the four tab badges equal
  their lists; the six states add up to all items.
- Eight items worked by a person (two each of Let go, Sent, Paid, Lost): Let go and Lost without a reason refused 5 of 5;
  all 8 left Work now at once (4,016 to 4,008, tile, counts and badges still equal their lists); 8 of 8 in their state's
  list with no follow-up date; 8 of 8 panels say the state their status gives. Two reopened: both back on Work now.
- Today's numbers against the lists their buttons open: step 1, 77 and 77 (the 7-day window; it was 89 against a list of
  77 before `_todo_soon`); 30 days, 273 and 273; written off in EMA 1,202 and 1,202; chase 109 and 109; biggest batch 279 and
  279.
- Bulk list 1,217 items; left out 542, each named (paid 66, waiting 27, credit 7, reprocessed 19, settled in EMA 54,
  likely paid 0, no filing date 0, state not worked out 0, other 369); the counts add up to the line.
- One-item send of 20 lines EMA shows settled: 20 of 20 held as check_first (no fax line set up in the copy).
- Hide by code, then Show again as the Hidden list sends it: 16 items hidden, the rule listed once, Show again 200,
  every status of that payer and reason as before, no rule left.
- Playwright on the invented persona world (review2/scripts, both hosts and bulk on): one main button per item, fax
  offered after the packet, Escape, l asks why, a worked item leaves Work now, status bar Let go asks why, no bulk
  buttons while paused, first rows 613 / 510 ms, intro wording, the feed said once, no signal ids; the bulk summary
  names all 10 held items; no page errors or sideways scroll at 390 px.

### F.4 Suites, once each at the end (HEAD 0fa77ff plus the working tree)
- Python 3.12 with SQLite 3.45 (S/fix/venv2_compat312): 2,207 passed, 7 failed, 2 skipped, 1 xfailed. The 7: five
  tests/test_docs.py checks (the docs do not yet name the EMA and upload settings, the new modules, the ema-reports command
  and the new page scripts: the docs unit's files) and the two test_fax tests in F.2 item 6. None is in C1's files or
  caused by them.
- Python 3.11 with SQLite 3.40.0 (S/fix/venv_sqlite340): 2,111 passed, 78 failed, 15 errors: 86 of the 93 are the missing
  Connection.serialize of that build (expected there; Vercel has it), the other 7 are the same 7 as above.
- node tests/calc_model_check.js: exit 0, 79 checks true. node tests/lab_review_check.js: exit 0, 143 checks true.

### F.5 Open for C1 later (not changed now, after the suites ran)
- The bulk list's left-out line puts unit D's route blocks (decide, call, corrected, bill, post, owner, fees) under
  "other": on real data 369 of 542. Naming them ("its next step is not an appeal") is a small change in
  `_left_out_reason` and LEFT_OUT; the counts already add up.

## 0. Status of every request aimed at C1's files

| request | from | status |
|---|---|---|
| FINDING_COLS gains the new columns; `ema=`, `state=` (also `work_state=`), `ema_outcome=`, `source=` through `api._filters`, so lists, CSV, `/api/worklist/counts`, `/api/desk` tiles and Today agree | brief_C C1.1 | done. Real data, both hosts: every state tile, every EMA filter, every Today tile, the counts route and the CSV equal their lists (section 4) |
| default Worklist = Work now + follow-ups due, sorted by what working an item is worth (`expected_value_cents`, else the shortfall) then deadline; waiting items show their follow-up day and why; the other states reachable by filter and Find a claim | brief_C C1.2 | done (`DESK_DEFAULTS` state todo, sort value; quiet links to Done / Accepted / Expired / Can't check) |
| item drawer: verdict, next step with its buttons, story with decisive rows, balance line; chips; every automatic closure keeps its verdict with Reopen | brief_C C1.3 | done (features.js xp* block); the drawer asks `/api/findings/{id}` and `/api/findings/{id}/explain` in parallel, so the item's own facts show at once |
| `/api/findings/{id}` and `/api/claims/view` carry EMA's block, story, verdict, next step (ledger read-only on a server, the "kept on the server" note on Vercel) | brief_C C1.4 | done. The story lives on `GET /api/findings/{id}/explain` (not on the base route) so the base route stays the same on both hosts (test_parity) |
| deep link `#item=<finding id>` | brief_C C1.5 | done; on real data 11 of 11 practice-manager items opened by link with the verdict the API gives |
| W5 `fax_vouch` on send; bulk appeal = unit B's rule AND a safe date AND the item's own next step says appeal (orchestrator decision), with what was left out and why | brief_C C1.6, needs_B 1.3 | done (`BULK_RULE`, `_appeal_ok_now` at send time, `left_out` line); bulk stays off unless REMITWATCH_BULK_APPEAL |
| policy routes | brief_C C1.7 | C2's api_ema.py (GET/POST /api/ema/policy); C1 added the journal kind `ema_policy` in actions.apply |
| held EMA report decision route | needs_B 1.9, needs_A1 4 | done: POST /api/ema/reports/{file_id}/decision (server: applied at once; Vercel: journal + the ledger's tries cleared by the next run) |
| hide rules at payer x reason x code x modifier x payer's reason code | needs_B2 | done: actions.HIDE_DDL five-part key, `ensure_hide_rules` migration (old rules keep hiding exactly what they hid), /api/hide GET/POST five-part, refuses a whole payer |
| NEVER_HIDDEN (six kinds let go one at a time) mirrored on the page | needs_B | done (desk.js NEVER_HIDDEN equals actions.NEVER_HIDDEN, tested) |
| blind-spot banner on every page, never twice | needs_C2 1 | done: `<div id="blindBar">` under the tabs and `blindBar()` in render() (asked once in ten minutes, a failed ask again on the next page; Today and the Data tab draw their own; never on a printed sheet). Test `test_the_blind_spot_banner_shows_under_the_tabs_of_every_page_and_never_twice`, seven break-and-restore mutations caught. Real data: banner on the Worklist, none in the bar on Today or Data, each of those draws exactly one |
| C1 may delete index.html's old `data(v)` | needs_C2 | kept as the fallback (the router prefers `dataTab`): tests/test_ema_coverage.py:73 still reads its strings from index.html (see 2.4) |
| uploads that finish during a Vercel run are read by a run started right after | review_A2_end-to-end (api.py, medium) | done: the cron route starts a continue run while `intake.state_pending` lists pairs (`continued_for`, `continue_error`); test `test_an_upload_that_finished_during_a_run_is_read_by_a_run_started_right_after`. The `_wake` message part is A2's file |
| hide_sql and hide_match disagree on recoded lines | review_B2_regression (archive_rules.py, low) | nothing in C1's files: actions.py and api.py use `hide_sql` for add, remove, the page's counts and the after-run pass, so B2's fix in hide_sql carries through unchanged |

## 1. Interfaces other units can build on (fixed)
- `GET /api/findings` (and `/api/findings.csv`, `/api/worklist/counts`): `state=` work_now | waiting | done | accepted |
  expired | cant_check | todo (work now + follow-ups due) | followup_due, comma lists allowed; `ema=` written_off |
  contractual | paid_other | on_ar | ema_only | worked | unworked | auto_written_off; `ema_outcome=`, `source=`,
  `work_state=` (same as state). `sort=value` orders by expected value, then due date.
- `GET /api/desk?...&state=&ema=&tileset=state`: the state tiles `api.STATE_TILES` (todo, todo_week, todo_decide, waiting,
  followup_due, done, accepted, st_expired, cant_check), each the count line of the list a click opens.
- `GET /api/findings/{id}/explain` -> `{finding_id, explain: {story, verdict, next_step, state}, ema_block, story_error?}`.
- `POST /api/findings/{id}/event` `{event: posted | era_requested | billed | outcome | instructions, note}`: one history line,
  no status change (journal kind `event` on Vercel).
- `POST /api/ema/reports/{file_id}/decision` `{decision: apply | discard}`.
- `/api/meta`: `report_uploads: true` on both hosts; `decisions`: true when a `GET /api/findings/{id}/decisions` route is
  mounted (route probe), and only then does the drawer ask for it.
- `api._appeal_ok_now(finding_id) -> (ok, why)`: the send-time check used by single and bulk sends.

## 2. Requests to other units
1. Unit D (story.py): `next_step` should carry `pattern` (the 4-part policy key POST /api/ema/policy takes) whenever
   `accept_pattern` is among its buttons. Until it does, that button opens the Write-off policy page instead of accepting
   in one click (features.js `xpAccept`).
2. Unit D or INT: store the next step's route (or `appeal_ok`) at save. On real data 14 of the first 200 items on the bulk
   list (7%) are held back at send because their own next step is not an appeal (8 "decide": a second-procedure rate
   check or a duplicate check first; 6 "call": work the payer's answer to work already done). The send is safe (each item
   is checked), but the list and its count include them until the next step is stored where SQL can read it.
3. Unit D / SWEEP (serving copy, "wider serving tables"): host parity. One of 50 sampled items got a different next step
   on the two hosts: finding 6594e3e528a425d2a9667251 is "call: work the second payer's answer" on the server (the
   ledger shows the rebill and its answer) but "bill the second payer, never billed" on the dashboard copy, which lacks
   those rows. On Vercel that tells a biller to bill a payer already billed. States and routes agreed for the other 49
   and for all 12 practice-manager items.
4. Unit D / B (practice-manager cases, aggregate): of 12 cases (one per stratum S01-S10a), 9 agree with the verified
   truth on verdict and next step, 2 in part, 1 not:
   - 3263992d386c79abd106fa4f (S06, written off later): the panel says appeal (owner policy: pursue); the truth is "let go"
     by default because the A/R note records the practice's decision not to appeal. A/R notes saying "not appealing" are
     not read as a decision.
   - 6b24c57a1f527bffbb6128a0 (S08b): the next step (ask the payer to reprocess the add-on) matches, but the verdict says
     "nobody has worked it" while the A/R note records a call.
   - 2fd1c1b3376bd3deb63c88ef (S04): the item is right (work now, appeal); the truth's step is one batch dispute and an
     escalation for the whole pattern, which no button offers yet.
   - Wording (story.py): a payer name ending in "s" gets "s's" ("Funds's portal").
5. (Done in the fix round: the C1 fixtures now make their undated items undated themselves, as described below.) Fix B / INT (detect.py, checkpoint 1bb8071, `END_DATED_CATEGORIES`): the detector now dates every money item from a
   remittance (a timely-filing CO-29 or non-covered CO-96 denial ends with the payer's window to dispute that answer).
   That breaks the premise of two C1 test fixtures, which assume the detector leaves those undated:
   tests/test_desk.py (6 tests through `_client`, which asserts the undated list is ["29"]), tests/test_claims.py::
   test_a_routine_rate_pattern_shows_on_each_of_its_claims (it uses the same fixture) and
   tests/test_review7.py::test_older_undated_items_open_exactly_what_today_counts; outside C1's files the same change fails
   tests/review6/test_r6_cov_brief_no_deadline.py (it pins non-covered, timely-filing and duplicate denials as undated,
   an earlier decision). C1 has NOT changed the tests (adapting
   them would hide a behavior change): please confirm the change is intended. If it is, the adaptation is: test_desk
   accepts an empty undated list and expects `decide` n == 1 (with that, all 23 test_desk tests pass, so every page count
   still equals its list); test_review7 makes its two CO-96 items undated after detection so it still exercises items
   with no date.
6. A2 fixer / SWEEP (tests/test_uploads.py:1138): the shell shows the Upload link for `m.uploads || m.report_uploads`; the
   assertion should read `"$('#uploadLink').hidden = !(m.uploads || m.report_uploads);" in index`.
   (`test_meta_says_reports_upload_on_both_hosts_and_whether_decisions_are_kept` checks `report_uploads: true`.)
7. Unit C2 (today.js): `KIND_STYLE` lacks the two work types unit B added; index.html has their classes:
   `bill: 'k-bill', post: 'k-post'`.
8. Docs unit / INT (tests/test_ema_coverage.py:73): read the coverage strings from static/data.js (C2 carried that panel
   over); then index.html's old `data(v)` can be deleted.
9. Hook agent: `GET /api/findings/{finding_id}/decisions` is shown in the drawer as soon as the route is mounted (meta
   `decisions` is a route probe); the api_ops include line goes after R's at the end of api.py.
10. Unit B2: no action needed for actions.py when `hide_sql` changes (item 0, last row).
11. INT (tests/conftest.py) or each test's owner: a test that builds or fetches a serving copy must set REMITWATCH_TMP to
    its own folder (tests/test_parity.py and now tests/test_ema_pages.py do). `cloud.save_serving` builds into the fixed
    `<tmp>/remitwatch/build-serving.sqlite`, so two test processes running at once (two agents, or a suite beside a
    single file) replace the file under each other: "attempt to write a readonly database" in `empty_serving`. An autouse
    fixture in conftest.py setting REMITWATCH_TMP to tmp_path would close it for every file. Files that build or fetch
    one in the shared folder today: test_activity, test_aws, test_ema_checks, test_ema_reports, test_fax, test_selfcheck,
    test_story (several agents run suites at once here, so their failures can be this collision, not a bug).

## 3. Cloud and pipeline notes
- `actions.apply_hide_rules` migrates an old hide_rules table to the five-part key on whichever host runs the analysis;
  the journal replays old and new entries alike. Nothing to add in pipeline.py or cloud.py.
- The item panel on Vercel reads the dashboard copy only (no ledger); the story says the postings are kept on the server.

## 4. Real-data acceptance (one world in wire/C1, deleted at the end; aggregates only)
- World: built from the real reports and archive, detection 74 s, peak 884 MB, 17,755 findings.
- Default Worklist: 4,179 items, $496,563; list 94 ms (server) / 91 ms (copy); desk 230 / 195 ms.
- Every state tile equals its list; the six states add up to all items; every EMA filter equals the rule counted on the
  store (server) or the copy (Vercel); every Today tile equals the list its filter opens; counts route and CSV rows equal
  the list for todo, followup_due, done, expired. Identical figures on both hosts.
- Item panel: 40 sampled items, no errors, p50 362 ms / max 663 ms on the server (ledger read), p50 186 / max 297 ms on the
  copy. Claim view 323 / 215 ms. In a browser: first Worklist rows 1.8 s after load; a deep link shows its verdict in
  0.2 to 0.8 s.
- Bulk list: 1,585 items; left out 246 (paid 74, waiting 4, credit 8, reprocessed 18, other 142).
