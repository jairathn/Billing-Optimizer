# Unit decisions: progress (the owner's decisions 2, 3 and 4)

Brief: `plans/briefs/2026-11-26_decisions.md` (sections "Decision 2", "Decision 3" and "Decision 4"; decision 4 is in
its own part at the end). Rules:
`plans/briefs/2026-11-26_common.md`, `CLAUDE.md`, `plans/HANDOFF.md` section 3b.

Branch `wip/decisions`, based on the integration tip 1867f60 (nothing to merge on start).

## State

- [x] Read the code paths: rule 8 `archive_rules.pr_group_denials`/`_pr_actions`/`_pr_words`; rule 3 `units_split`;
      detect.py `_close_by_answer` (settled as secondary); states.py `_decide`/`route_of`; story.py `_next`; api.py
      `/status`; results.py (attribution reads payment records, not recovered_cents).
- [x] Baseline on a private world copy (the base tree 1867f60, `cycle_run.py 2026-11-24`). Snapshot `base_snap.sqlite`.
- [x] Decision 3 built (61adb76), decision 2 built (c432cd6), docs (eeb1fd5).
- [x] First after run: 195 items moved, all decision 2 or 3, none other. Hand-tracing them found three faults, each fixed
      with a failing test first (invented data, both store kinds), broken on purpose and restored:
      1. 3eb4036: EMA's patient money. An item the base closed as settled by the patient (EMA: the patient paid part, the
         rest taken off the patient's balance, line closed) came back as "Bill the patient" for the whole share; two
         items where the patient had paid part were asked for the whole share. Now: patient_share gives patient_paid and
         left; settled_in_ema counts what EMA took off the patient's balance for a patient-group denial; paid in full is
         done (patient_paid); part paid bills the rest; Billed the patient closes only on the patient's side
         (patient_side_settled), never on a non-contractual write-off.
      2. 9811ce4: the next payer's contractual answer. 9 work-now items (Medicare primary PR-49/PR-204) were answered
         after by the next payer with CO only and nothing paid (a Medigap plan CO-96/CO-45, Kentucky Medicaid CO-45, 3 of
         them Medicaid). The step still bills the patient (the owner's rule) but first says that payer put it on the
         practice (and that a Medicaid member is not billed for what Medicaid puts on the practice): check, else Done.
      3. 0b9b9cf: decision 3 and takebacks. In 7 of the 12 rule 3 closures that became recoveries, the payment in the
         other role came in a remittance that also took back (claim status 22) a payment of the service the archive does
         not hold (the payer processed again a claim it had already paid, often a duplicate OA-18): about $0 arrived, yet
         the whole payment counted. Rule 3 now measures what arrived after the denial, takebacks included (arrived_cents
         in the closure's detail, Detector._mark_paid records no more); the note says what was taken back.
- [x] Third after run (0b9b9cf) on a fresh copy, measured below; 37 moved items hand-traced from raw rows (tools/hand.py
      reads the tables with no remitwatch code) and agree.
- [x] Full suite in python3 (3.13.18, SQLite 3.53.1) and in 3.11.1 (SQLite 3.40.0) at 0b9b9cf; node checks (see Tests).

## Design

Decision 2 (rule 8 PR-288, 27, 26, 200, 49, 204 with group PR; never CO-147):
- Size and step: X = what the payer put on the patient on the line, never more than the charge less what any payer
  paid (archive_rules.patient_share_of; the payer's usual rate is kept in the evidence as payer_rate_cents). The step
  bills X less what EMA shows the patient already paid; 288 also offers a retroactive referral and a corrected claim
  (by the payer's window), 27/26/200 ask for current insurance, 49/204 mention a corrected claim. No payer window
  expires it. Medicaid follows the same rule (no ABN logic).
- Done by itself when EMA shows the patient paid X, or the line closed on the patient's side (payment, plus what EMA
  took off the patient's balance); a line EMA closed by writing the share off stays listed for a person.
- "Billed the patient": status Sent with a note starting "Billed the patient" (one request, seen_status checked); waits
  60 days (PATIENT_WAIT_DAYS) for the patient's payment in EMA, then work again ("send another statement, or Done").
- "Done": status Paid with $0, the flag `done` strict JSON true, only on these items (DONE_ONLY otherwise); history says
  who and when (rule person_done). No run overwrites either. Results closure kinds done / patient_paid.

Decision 3:
- Archive rule 3, a payment in the other role after the item's answer: the recovery is what actually arrived after the
  denial (the payer's answers of the service after it, takebacks included, never more than the payment). Same day as
  the denial: no recovery.
- What the payer owes in that role: its allowed amount (else the item's expected), never above the charge, less the other
  payers' standing payments (on file), else less the prior payer's answer as the payer applied it (OA-23), less the
  patient's share (PR). Nothing tells what the other payers paid: no remainder is sized. A remainder above $0 stays
  listed (second_role_short, no floor), work now (route call) until the window to dispute that answer closes. The same in
  detect.py's settled-as-secondary closure.
- Results attributes the payment records like any recovery and already nets a takeback of money paid before the flag.

## Numbers (world of 2026-11-24, today 2026-11-24; base 1867f60 against the branch at 0b9b9cf)

- 16,632 items either way; 188 moved: 183 decision 2, 5 decision 3, 0 other. Work now 4,478 -> 4,570 (+92),
  expired 5,067 -> 4,974 (-93), done 6,936 -> 6,937 (+1). No signal count changed.
- Decision 2, the 188 rule 8 PR-group items: base 48 work now, 1 waiting, 46 done, 93 expired; branch 140 work now,
  1 waiting, 47 done. Work now sized $15,476.12 at the patient's share (base sized the same 140 at $8,814.76 at the
  payer's usual rate), the steps bill $15,242.78 (less what EMA shows patients paid). Of the 140: 21 Medicaid (all
  PR-288), 88 Medicare or Medicare Advantage; service years 2023: 7, 2024: 37, 2025: 32, 2026: 64. The 93 that come back
  from expired: PR $6,911.90; EMA links 53 of them, 41 written off non-contractual, 6 still open. One base work-now item
  is done now (patient paid $15.00, $191.75 taken off the patient's balance, line closed). 9 carry the caution about the
  next payer's contractual answer. CO-147: 12 work now $349.29, unchanged.
- Decision 3: 5 items recover $80.96 more (base $0): $18.53, $28.89 and $16.69 that arrived after the denial, and $9.23
  and $7.62 net of a takeback; 7 closures in the other role that came with a takeback recover $0 (as the base did),
  their note saying what was taken back. Remainders left listed: 0 (nothing tells what the primary paid on those).
  Settled as secondary (detect.py): 14 recoveries, $258.66, unchanged; one of them has a later re-adjudication with no
  new money, counted once.

## Open questions for the owner

- Medicaid PR-288 (21 work-now items, Humana Medicaid plans): federal Medicaid rules limit billing a member; the owner's
  rule bills the patient. Confirm, or say Medicaid items should go to Done or a retroactive referral only.
- The 93 items the base had expired (services back to 2023) are on Work now again: confirm, or set an age limit.
- The 9 items where the next payer answered with CO only: the step warns; the owner may prefer them routed to Done.
- Today's work kinds still group DENIED_REFERRAL under "Get the authorization" and the other two under "Check first"
  (insights._WORK_OF); a "Bill the patient" kind would need kind_sql too (counts must equal lists).
- "Done" shows the "Paid" status pill in lists (as a $0 "Posted in EMA" does); the drawer says "Marked done".
- A later payment in the SAME role that comes with a takeback is still counted whole (decision 3 covers the role switch
  only); worth the same treatment.

## Tests

- tests/test_decisions.py (47, both store kinds, both hosts); tests/test_archive_rules.py (3 rule 8 tests updated to the
  decision). Related suites (states, story, archive_rules, decisions, results, settled_elsewhere, 837_sends, docs,
  ema_wiring, selfcheck): 873 passed, 1 skipped in python3 before the last two fixes.
- Full suite, python3 3.13.18: 2,790 passed, 55 failed, 29 skipped. All 55 fail the same way on the base tree 1867f60
  (run there too): test_fax.py 34, test_hotfix2.py 9, test_no_fax_route.py 5, test_notes_bodies_cli.py 4,
  test_hotfix2c.py 2, test_bulk_pause.py 1 (sends "held" where the fixtures expect "sent": date-bound fixtures, it seems).
- Full suite, 3.11.1 with SQLite 3.40.0: 2,673 passed, 163 failed, 25 errors: the same 55, and 108 failures and 25 errors
  that are all `Connection.serialize` missing in that build (the store image tests CLAUDE.md allows to fail there; one
  of them is this unit's serverless Done test, whose fixture builds an image).
- node tests/calc_model_check.js (79 of 79) and tests/lab_review_check.js pass; features.js parses.

## How to measure again

Tools: `plans/tools/decisions/` (README there). U = a private folder (mode 700) holding a copy of the master world:
`cd $U && F5_SRC=<tree exported with git archive> P2_DIR=$U/w P2_DB=world.sqlite python3 -I -B plans/tools/world/cycle_run.py 2026-11-24`,
then `fullsnap.py`, `diff_ba.py <base snap> <after snap> moved.json`, `pick.py moved.json`, and `hand.py`/`trace.py` per id.

# Decision 4: codes a payer almost never pays (pursued; why the payer does not pay, what the note or claim must show)

## State

- [x] Merged the integration tip (8d067e5, then 71ea7f3: plans files only). Read rule 4.2: `Detector._paid_share`,
      `fold_payer_policy`, C5 `ema_checks.ema_denials`, `ec.set_policy`, the policy page (api_ema.policy_table, policy.js).
- [x] The research's data file into `remitwatch/data/payer_code_rules.json` (24 rules, 15 sources; public facts about
      payers only: no counts, dollars or practice data; `applies_when: em_with_modifier_25` added on the two G2211 rules
      whose verdict needs the visit's modifier 25), un-ignored alone in .gitignore. The report into
      `plans/research/codes_rarely_paid_2026-11-26.md` (aggregates only, checked; the plan detail line made generic).
      a6999b6.
- [x] The product: `remitwatch/rarely_paid.py` and its hooks (ca14240, 2ea7b77), the grouping of one rule for several
      codes and EMA's books in the evidence (7b7d957). Tests on both store kinds and both hosts.
- [x] Measured on a private world copy (11-24 data): base a6999b6 against ca14240, then the words of 7b7d957 on the same
      world. Hand checks with plain SQL; every work-now item a never-payable rule leads traced against its own rows.
- [ ] Full suite at the final tip in python3 and 3.11.1 (see Tests).

## Design

- Matching: an entry of the data file applies to an item's code when it names the item's payer group (exact), lists it
  as another spelling (alias), or is one company's plans of one kind (family, `ema_checks._family`); the billed code (the
  original code of a CODE_CHANGED line); the service date inside the entry's dates (`service_dates`, or one period of
  `by_service_date` where the rule changed on a date). Alias and family matches apply only where the run found the pair
  rarely paid (a state's Medicaid program pays most codes when the claim is right).
- Codes of an item (`item_codes`): an unpaid denial, bundling or code-change line; each line of a denial only EMA saw; up
  to six unpaid codes of a whole-claim denial; G2211 for a G2211 denial. Never a paid line, a short payment, a claim sent
  on to another carrier.
- Pattern keys name the period whenever the entry has dates (`Anthem / BCBS|77600|from 2026-01-01|payer_policy`); a rule
  that changed on a date is two patterns. `fold_payer_policy` (835 items) and C5 (EMA-only lines) now also make a pattern
  for a researched pair the 5% count did not find; the decision stays "pursue" by default (nothing accepted by default);
  the pattern carries `researched` (the verdict).
- Evidence (`Detector.explain_rarely_paid`, a contained step after the more rules and before the fold): for each pair an
  item is about (rare or exact research) and each pattern of the owner's table: lines answered as primary and paid
  (standing payments) by year, recoded, reasons, remarks, modifiers, places of service, codes billed with it, how often
  it paid the claim's other lines, and EMA's books (one row per billed service). Kept in meta `rarely_paid:why` (top 400
  pairs by unpaid; counts, codes and years only), so both hosts' panels read the same (the dashboard's copy carries meta).
  A failing step is logged (runlog.caught), drops the old evidence and the run goes on (REMITWATCH_EMA_STRICT re-raises).
- Drawer: "Why (payer) does not pay (code)" per code: the verdict in words, the evidence, the rule, what the note or the
  claim must show, the sources (each read twice). A rule shared by several codes is shown once. A never-payable verdict on
  a one-line item still to work (routes appeal, corrected, call, decide, rate) leads the next step: "Not payable under
  (payer)'s published rule ...: check what the rule still allows; if nothing, the owner accepts the pattern on the
  write-off policy page or a person lets it go with the reason"; Accept this pattern only when the owner's table lists it.
  States, deadlines and closures are not changed by any of it.
- Packet: a "Before you send it" checklist (never in the PDF, never faxed), one line per rule; the payer's published rule
  is enclosed only with a note or claim verdict (never a rule against the appeal). Note review: the rule, what to show
  and the verdict go into its input; a never-payable rule tells it to recommend against the appeal unless the note shows
  exactly what the rule allows.
- Policy page: each payer-policy row says the period, the research's verdict, rule, what to show and sources, and the
  evidence's first four sentences. RULE_FILES hashes rarely_paid.py and the data file (a change re-analyses the archive).

## Numbers (world of 2026-11-24, today 2026-11-24; base a6999b6 against ca14240)

- Detection 385 s and 1,052 MB peak (base) against 241.9 s and 1,054 MB (after): no cost to see (the time is the
  machine's load). 16,632 items either way; 0 changed (state, signal, amounts, deadlines): the product only explains.
- Evidence: 31 pairs (meta 21,894 bytes). The owner's table: 14 payer-policy patterns (base 7), all "pursue" by default:
  Anthem / BCBS 77600 from 2026 (n 209, $74,713.32, never payable), UnitedHealthcare Medicare Advantage G6001 to 2025
  (n 550, $58,899.50, never payable), Optum (UHC) 77600 from 2026 (n 101, $49,705.13, never payable) and to 2025 (n 45,
  $16,086.60, claim), UnitedHealthcare G6001 to 2025 (n 30, $4,588.20), UHC Medicare Advantage G6001 to 2025 (n 26,
  $3,976.44), Medicaid (other) 77600 to 2025 (n 6, $2,144.88, claim), Humana Medicare Advantage G0127 (n 11, $207.46,
  claim), Medicaid (other) 11102 and 88305 (n 1 each), Aetna G2211 (n 2, unclear), Medicare KY (CGS J15) J3490 (n 26, $0),
  Anthem / BCBS Medicaid J3490 (n 10, $0), Anthem / BCBS J2327 (n 14, $0).
- "Why" blocks: 1,217 on 1,119 items ($244,657.52), 520 of them on Work now ($156,391.90). Verdicts: never payable 440
  on Work now, 431 expired; claim 49 on Work now; unclear 25 on Work now; the rest done or not researched (evidence only).
  Items with more than one block: 19 with 2, 15 with 3, 5 with 4, 11 with 5, 2 with 6 (whole-claim Medicaid denials).
  With 7b7d957, 31 of the 33 whole-claim Medicaid items with 3 or more blocks have one rule step and one checklist line.
- Next step led by a never-payable rule: 421 work-now items (CODE_CHANGED Anthem 77600 to 77436 158, EMA_DENIED 116
  (Optum 77600 101, UHC Medicare Advantage G6001 15), DENIED_AUTH 87 (UnitedHealthcare Medicare Advantage G6001 66,
  UnitedHealthcare G6001 21), PROCEDURE_BUNDLED Anthem 77600 27, DENIED_OTHER Anthem 77600 20,
  DENIED_MEDICAL_NECESSITY 13). d4_trace: all 421 checked against their own rows, 0 failing.
- Hand checks with plain SQL (d4_hand) agree with the evidence: Anthem 77600 2026 279 answered, 11 paid, 189 answered as
  77436; reasons CO-97 216, CO-45 27. Anthem G2211 5,792 answered, 24 paid on the line, 23 standing (one reversed).
  UnitedHealthcare Medicare Advantage G6001 2025 347/8; UnitedHealthcare G6001 2025 30/0.

## Open questions for the owner

- Medicare and Medicare Advantage still pay 77600 with 77436 or 77437 in 2026 (owner notes section 1: a question for the
  coding advisor). The app lists no paid line.
- Medicaid KY and IN (the "Medicaid (other)" research by its other spellings, enrollment and claim rules): EMA's books
  show most services paid in the end (11102: 18 of 20; 99213: 16 of 18) while the remittances show 3 of 83 and 3 of 117
  lines paid. The payer-policy count counts each answer, so a claim denied several times before it was fixed looks
  rarely paid. Counting services instead of answers would drop such pairs from rule 4.2; a rule change for the owner
  (not made). The drawer now shows both counts.
- G2211 for Medicaid KY and IN, UMR, UHC Integrated Services, the self-funded TPAs and Aetna: no published rule found
  (unclear: ask the payer). Cigna G2211 and Anthem 99024 are not researched (the evidence only).
- A policy row's item count is counted at the fold (pre-existing): Medicare KY (CGS J15) J3490 n 26, while 9 items carry
  a block after the run's later rules.

## Tests

- tests/test_payer_code_rules.py (24 tests; both store kinds where a run is made, both hosts for the panel): the data
  file holds public facts only; RULE_FILES; dates to the day (leap day, year end); split keys; alias needs rarity;
  item codes; the run's evidence, drawer and next step; a failing step contained; G2211 and modifier 25; the packet's
  checklist, enclosures and PDF; the note review's prompt; the EMA fold pursued until accepted; C5 periods with EMA's
  evidence; the server panel and packet; the serverless panel from the copy; one rule for several codes; EMA's books in
  words; the page's renderer in node; the docs' words. Each change was broken on purpose (13 breaks) and its test failed;
  restored (`git diff` empty).
- Related suites (story, ema pages, decisions, docs, ema wiring, notes): 430 passed, 1 skipped, 4 failed in python3; the
  4 are test_notes_bodies_cli.py's date-bound failures, which fail the same way with this change stashed.
- Node checks: calc_model_check 79 of 79, lab_review_check 143 of 143; features.js parses.

## How to measure again (decision 4)

`plans/tools/decisions/` (README there): a cycle of the tree on a private world copy (`cycle_run.py 2026-11-24`), then
`d4_measure.py <tree> <world>`, `d4_hand.py <world>`, `d4_trace.py <tree> <world>`, `d4_show.py <tree> <world>`.

